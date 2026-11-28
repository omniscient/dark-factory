"""Numbered, non-deleting rotation for the state directory's JSONL stores (#652).

Shared by the model-proxy request ledger (model_proxy.py) and the GraphQL cost log
(cost_log.py); run_record.py reads ledger generations through generations() and
open_regular(). For a live file <name>:

    <name>.lock                  sidecar lock; never rotated, renamed or removed
    <name>.<N>                   plain generation N (decimal, no leading zero)
    <name>.<N>.gz                compressed generation N; never rewritten, renamed or removed
    <name>.<N>.gz.tmp.<16 hex>   a compression in progress

The state directory is a volume every gated run container mounts read-write, so any of
these names can be planted: generations are only opened O_NOFOLLOW | O_NONBLOCK and must be
regular files of bounded size, and no lock taken here is waited on without bound. Imports
nothing from providers. Spec: docs/superpowers/specs/2026-11-27-durable-factory-stats-design.md.
"""
import contextlib
import fcntl
import gzip
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

# A generation is recognised by its whole suffix after <name>; a temporary file matches neither.
_GENERATION_RE = re.compile(r"\.([1-9][0-9]*)(\.gz)?")
_TEMPORARY_RE = re.compile(r"\.[1-9][0-9]*\.gz\.tmp\.[0-9a-f]{16}")
_LOCK_POLL_SECONDS = 0.01
RECOVERY_PER_ROTATION = 2
STALE_TEMPORARY_SECONDS = 3600
_COPY_CHUNK_BYTES = 1 << 20


def log(message: str) -> None:
    print(f"factory-rotation: {message}", file=sys.stderr, flush=True)


def lock_path(path) -> Path:
    """<name>.lock beside the live file: the one lock for rotation, append and the swap."""
    path = Path(path)
    return path.with_name(path.name + ".lock")


def _names(path: Path) -> list:
    try:
        return os.listdir(path.parent)
    except OSError:
        return []


def generations(path) -> dict:
    """{N: {"plain": Path, "gz": Path}} (either key may be absent) for every entry beside the
    live file whose whole suffix after its name is .N or .N.gz. An unlistable directory reads
    as no generations. Whatever sits at a name (a FIFO, a link, a directory) is listed; the
    opener refuses it (open_regular)."""
    path = Path(path)
    found = {}
    for entry in _names(path):
        if not entry.startswith(path.name):
            continue
        match = _GENERATION_RE.fullmatch(entry[len(path.name):])
        if match:
            form = "gz" if match.group(2) else "plain"
            found.setdefault(int(match.group(1)), {})[form] = path.with_name(entry)
    return found


def temporaries(path) -> list:
    """Every <name>.<N>.gz.tmp.<16 hex> beside the live file."""
    path = Path(path)
    return [path.with_name(entry) for entry in _names(path)
            if entry.startswith(path.name) and _TEMPORARY_RE.fullmatch(entry[len(path.name):])]


def open_regular(path, max_bytes=None):
    """(fd, os.stat_result) for path opened O_RDONLY | O_NOFOLLOW | O_NONBLOCK when it is a
    regular file of at most max_bytes bytes; None otherwise, with one log line unless the name
    is simply absent. A FIFO opens without blocking and is then refused; a link is refused by
    O_NOFOLLOW. The caller closes the descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log(f"skipped {path}: {exc.strerror or exc}")
        return None
    try:
        info = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        log(f"skipped {path}: {exc.strerror or exc}")
        return None
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        log(f"skipped {path}: not a regular file")
        return None
    if max_bytes is not None and info.st_size > max_bytes:
        os.close(fd)
        log(f"skipped {path}: {info.st_size} bytes is over {max_bytes}")
        return None
    return fd, info


@contextlib.contextmanager
def locked(lock_file, *, timeout, shared=False, create=True):
    """Yield True while flock (LOCK_EX, or LOCK_SH when shared) is held on lock_file, False
    when it was not taken within `timeout` seconds. The lock file is opened
    O_NOFOLLOW | O_NONBLOCK (O_CREAT only when create) and must be a regular file; anything
    else (absent with create=False, a FIFO, a link, an unopenable path) yields False at once.
    Never raises for a lock it cannot take and never waits past the deadline."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    fd = None
    held = False
    try:
        fd = os.open(lock_file, flags, 0o666)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            operation = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, operation)
                    held = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_LOCK_POLL_SECONDS)
    except OSError:
        pass
    try:
        yield held
    finally:
        if fd is not None:
            os.close(fd)  # closing the descriptor releases the flock


def store_size(path) -> int:
    """Bytes in the live file plus every generation in either form (W10)."""
    path = Path(path)
    total = 0
    for candidate in [path, *(p for forms in generations(path).values() for p in forms.values())]:
        try:
            total += os.lstat(candidate).st_size
        except OSError:
            pass
    return total


def rotate_locked(path, threshold) -> "int | None":
    """Rotate the live file when due (W1, W2, W10). The caller holds LOCK_EX on lock_path(path).

    Returns the number of the generation that now needs compressing (compress_and_recover,
    after the lock is released), or None. Lists the directory only when a rotation is due or
    the live file has a second link. Never raises: a failure is one log line, and the caller
    still appends to the live file, which then keeps growing."""
    path = Path(path)
    try:
        try:
            live = os.lstat(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(live.st_mode):
            log(f"not rotating {path}: not a regular file")
            return None
        found = None
        if live.st_nlink > 1:
            # W2: a writer killed between os.link and os.unlink left the live name and the
            # highest plain generation on one inode. Complete that rotation: repeating it would
            # link the same rows to a second number and every reader would count them twice.
            found = generations(path)
            plain = [n for n, forms in found.items() if "plain" in forms]
            if plain:
                top = max(plain)
                try:
                    same = os.path.samestat(os.lstat(found[top]["plain"]), live)
                except OSError:
                    same = False
                if same:
                    os.unlink(path)
                    log(f"completed an interrupted rotation of {path.name} to generation {top}; "
                        f"store is {store_size(path)} bytes")
                    return top
        if live.st_size < threshold:
            return None
        if found is None:
            found = generations(path)
        # W1: one more than every number present in either form. os.link fails on an existing
        # name where os.rename would replace it, so no generation is ever overwritten.
        number = max(found, default=0) + 1
        os.link(path, path.with_name(f"{path.name}.{number}"), follow_symlinks=False)
        os.unlink(path)
        log(f"rotated {path.name} to generation {number}; store is {store_size(path)} bytes")
        return number
    except OSError as exc:
        log(f"rotation of {path} failed: {exc.strerror or exc}")
        return None


def _unlink_quietly(path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(f"could not remove {path}: {exc.strerror or exc}")


def _is_regular(path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _write_gz(src_fd: int, dst_fd: int) -> None:
    """gzip src into dst in chunks; a 100 MiB generation is never held in memory."""
    with os.fdopen(src_fd, "rb", closefd=False) as src, \
            os.fdopen(dst_fd, "wb", closefd=False) as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
        shutil.copyfileobj(src, out, _COPY_CHUNK_BYTES)


def _compress(path: Path, number: int, threshold: int, lock) -> bool:
    """W3 and W4 for generation `number`. Returns False when the plain file was skipped (W6:
    absent, not a regular file, or over twice the threshold) or its temporary name was taken;
    True otherwise."""
    plain = path.with_name(f"{path.name}.{number}")
    gz = path.with_name(f"{path.name}.{number}.gz")
    opened = open_regular(plain, 2 * threshold)
    if opened is None:
        return False
    src_fd, info = opened
    # W3: a random name, created exclusively. A pid is not unique here, because both ledger
    # writers are pid 1 of their own container.
    temporary = path.with_name(f"{gz.name}.tmp.{secrets.token_hex(8)}")
    try:
        try:
            dst_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o666)
        except FileExistsError:
            log(f"skipped compressing {plain}: temporary name {temporary.name} is taken")
            return False
        try:
            try:
                _write_gz(src_fd, dst_fd)
                os.fsync(dst_fd)
                # Keep the plain file's mtime so run_record's since_epoch filter still applies.
                os.utime(dst_fd, ns=(info.st_atime_ns, info.st_mtime_ns))
            finally:
                os.close(dst_fd)
        except BaseException:
            _unlink_quietly(temporary)
            raise
    finally:
        os.close(src_fd)
    # W4: the swap takes the lock again, briefly. An existing .gz is never replaced.
    with lock() as held:
        if not held:
            _unlink_quietly(temporary)
            log(f"left {plain} uncompressed: the lock was not taken for the swap")
            return True
        if not os.path.lexists(gz):
            try:
                os.link(temporary, gz, follow_symlinks=False)
            except OSError as exc:
                log(f"could not link {gz}: {exc.strerror or exc}")
        _unlink_quietly(temporary)
        if _is_regular(gz):
            _unlink_quietly(plain)
    return True


def _recover(path: Path, created: int, threshold: int, lock) -> None:
    """W5, the recovery pass. Compresses up to RECOVERY_PER_ROTATION other plain generations
    that have no .gz (a writer was killed between rotating and swapping), highest number
    first; an entry skipped under W6 does not count. Then, under the lock, it removes each
    plain generation whose .gz is a regular file, and every temporary older than
    STALE_TEMPORARY_SECONDS. A planted non-regular .gz keeps its plain generation in place
    (readers prefer plain) and is not retried."""
    found = generations(path)
    done = 0
    for number in sorted(found, reverse=True):
        if done >= RECOVERY_PER_ROTATION:
            break
        if number == created or "plain" not in found[number] or "gz" in found[number]:
            continue
        if _compress(path, number, threshold, lock):
            done += 1
    cutoff = time.time() - STALE_TEMPORARY_SECONDS
    with lock() as held:
        if not held:
            return
        for forms in generations(path).values():
            if "plain" in forms and "gz" in forms and _is_regular(forms["gz"]):
                _unlink_quietly(forms["plain"])
        for temporary in temporaries(path):
            try:
                info = os.lstat(temporary)
            except OSError:
                continue
            if not stat.S_ISDIR(info.st_mode) and info.st_mtime < cutoff:
                _unlink_quietly(temporary)


def compress_and_recover(path, created, *, threshold, lock) -> None:
    """W3-W6 after a rotation, called with no lock held. `lock` is a zero-argument callable
    that returns a context manager yielding True while LOCK_EX on lock_path(path) is held; the
    swap and every removal take it briefly. Compresses generation `created`, then runs the
    recovery pass (W5). Nothing else triggers the pass, and there is no background task.
    Never raises: a failure is one log line and leaves the plain generation in place,
    still readable."""
    path = Path(path)
    try:
        _compress(path, created, threshold, lock)
        _recover(path, created, threshold, lock)
    except Exception as exc:  # noqa: BLE001 — compression must never fail an append
        log(f"compression of {path.name}.{created} failed: {exc!r}")

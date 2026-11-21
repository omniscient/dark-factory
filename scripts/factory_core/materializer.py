"""Harness candidate materializer, Phase A (#465, epic #463).

Turns a mechanism_candidate.py-valid record into a disposable workspace built from an
exact, digested baseline commit with the candidate's diff applied, writes an immutable
artifact bundle, and translates the record's envelopes into the bwrap argument list the
Phase B executor (#544) will launch. Phase A executes nothing from the workspace, needs
no namespaces, and makes no claim that the sandbox boundary holds: its argv is checked
by tests, never run. Candidate payloads are untrusted data; every check fails closed.
Spec: docs/archive/2026-10-22-harness-candidate-materializer-design.md

Usage:
    PYTHONPATH=scripts python3 -m factory_core.materializer materialize \\
        --candidate CANDIDATE.json --commit <40-hex sha> --repo-root <clone> \\
        [--out-dir DIR] [--known-digest sha256:...]...

Exit 0 on a PASS bundle, 1 on a BLOCKED bundle, 2 when no bundle can be written (an
unreadable candidate file, no --out-dir and no $ARTIFACTS_DIR, an unsafe candidate_id
for the default path, an output directory inside the baseline clone, or a write error).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import mechanism_candidate as mc
from . import verdict as _verdict


class MaterializerError(Exception):
    pass


MATERIALIZER_VERSION = "1"
GATE_TYPE = "materializer"

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_KEYS = ("memory_limit_mb", "max_duration_seconds", "max_workspace_bytes", "max_nproc")

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")  # run_record.py's _SAFE_RUN_ID_RE
_CPU_LIMIT_RE = re.compile(r"[1-9][0-9]{0,2}")  # bare integer CPU count, 1-999
_LFS_HEADER = b"version https://git-lfs.github.com/spec/"
_FILE_MODES = frozenset({"100644", "100755"})
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
_MAX_SYMLINK_HOPS = 40
_ACCEPTED_RISK = ("H0", "H1", "H2")  # materialize(); Phase B's run() accepts H0-H1 only

SANDBOX_WORKSPACE = "/workspace"
SANDBOX_HOME = "/home/sandbox"
SANDBOX_PATH = "/usr/bin:/bin"
BASE_BIND_DIRS = ("/usr", "/bin", "/lib")
OPTIONAL_BASE_BIND_DIRS = ("/lib64",)


@dataclass(frozen=True)
class Baseline:
    """The pinned baseline: a full commit SHA in the clone at repo_root. Caller-supplied,
    because a candidate record carries only digests, never a git ref."""
    commit: str
    repo_root: str


def _fail(msg):
    raise MaterializerError(msg)


def _error_text(exc):
    if isinstance(exc, (MaterializerError, mc.MechanismCandidateError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _is_within(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


# ── Config ceilings ─────────────────────────────────────────────────────────────

def check_config(block):
    """The four `materializer:` ceilings, each an int >= 1; anything else fails closed."""
    if not isinstance(block, dict):
        _fail("config: the materializer block is missing or not a mapping")
    unknown = sorted(set(block) - set(CONFIG_KEYS))
    if unknown:
        _fail(f"config: unknown materializer keys {unknown}")
    for key in CONFIG_KEYS:
        if not mc._is_int(block.get(key)) or block[key] < 1:
            _fail(f"config: materializer.{key} must be an int >= 1, got {block.get(key)!r}")
    return {k: block[k] for k in CONFIG_KEYS}


def load_config(path=None):
    """The `materializer:` block of config/config.yaml (this checkout's, by default)."""
    import yaml
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"config: cannot load {p}: {exc}")
    return check_config(data.get("materializer") if isinstance(data, dict) else None)


def _config(config):
    return load_config() if config is None else check_config(config)


# ── Envelope policy ─────────────────────────────────────────────────────────────

def check_network(candidate):
    """Declared egress is refused, never downgraded: the sandbox has no network."""
    net = candidate["network_envelope"]
    if net["egress_allowed"] or net["allowed_hosts"]:
        _fail("network_envelope declares egress (egress_allowed or allowed_hosts); the "
              "sandbox has no network by construction, and declared egress is refused, "
              "never silently downgraded")


def resolve_limits(candidate, config):
    """Per-process rlimit values. A candidate may lower a config ceiling, never raise it."""
    config = check_config(config)
    res = candidate["resource_envelope"]
    limits = {}
    for key in ("memory_limit_mb", "max_duration_seconds"):
        declared = res.get(key)
        if declared is None:
            limits[key] = config[key]
        elif not mc._is_int(declared) or declared < 1:
            _fail(f"resource_envelope.{key} must be an int >= 1 or null, got {declared!r}")
        elif declared > config[key]:
            _fail(f"resource_envelope.{key} {declared} exceeds the materializer.{key} "
                  f"ceiling {config[key]}")
        else:
            limits[key] = declared
    cpu = res.get("cpu_limit")
    if cpu is None:
        cpus = 1
    elif isinstance(cpu, str) and _CPU_LIMIT_RE.fullmatch(cpu):
        cpus = int(cpu)
    else:
        _fail(f"resource_envelope.cpu_limit {cpu!r} is not a supported format "
              f"(a bare integer CPU count from 1 to 999)")
    # Host capacity (how many CPUs this machine has) is checked where the candidate is
    # executed (Phase B, #544), not here: a sealed verdict must not depend on the host.
    limits["cpu_seconds"] = cpus * limits["max_duration_seconds"]
    limits["max_nproc"] = config["max_nproc"]
    limits["max_file_bytes"] = config["max_workspace_bytes"]
    return limits


# ── Baseline digesting ──────────────────────────────────────────────────────────

def _check_baseline(baseline):
    if not isinstance(baseline, Baseline):
        _fail("baseline must be a Baseline(commit, repo_root)")
    if not isinstance(baseline.commit, str) or not _COMMIT_RE.fullmatch(baseline.commit):
        _fail(f"baseline.commit must be a full 40-hex commit SHA, got {baseline.commit!r}")
    if not isinstance(baseline.repo_root, str) or not os.path.isdir(baseline.repo_root):
        _fail(f"baseline.repo_root {baseline.repo_root!r} is not a directory")
    top = _clone_top(baseline.repo_root)
    if top != os.path.realpath(baseline.repo_root):
        _fail(f"baseline.repo_root {baseline.repo_root!r} is not the top level of its clone "
              f"({top!r}); a subdirectory would move the clone-containment checks and the "
              f"adapter safety lookup off the clone root")


def _git_env():
    """os.environ minus every inherited GIT_* variable (GIT_DIR, GIT_OBJECT_DIRECTORY,
    GIT_INDEX_FILE, ... would silently retarget git at another repository), with replace
    refs disabled (refs/replace/* would let cat-file serve other content for a sha) and
    no lazy fetch (a partial-clone baseline must never reach the network)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _git(repo_root, *args, data=None):
    try:
        proc = subprocess.run(["git", "-C", str(repo_root), *args], input=data,
                              capture_output=True, check=False, env=_git_env())
    except OSError as exc:
        _fail(f"cannot run git: {exc}")
    if proc.returncode != 0:
        _fail(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _clone_top(repo_root):
    """realpath of the work-tree top level git reports for repo_root. Fails closed for a
    directory that is not inside a work tree (not a repository, a bare one, a .git dir)."""
    out = _git(repo_root, "rev-parse", "--show-toplevel")
    top = os.fsdecode(out[:-1] if out.endswith(b"\n") else out)
    if not top:
        _fail(f"git reports no work-tree top level for {repo_root!r}")
    return os.path.realpath(top)


def _cap_msg(size, cap):
    return f"workspace expands to {size} bytes, over materializer.max_workspace_bytes ({cap})"


def _list_tree(commit, repo_root):
    out = _git(repo_root, "ls-tree", "-r", "-l", "-z", "--full-tree", f"{commit}^{{commit}}")
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, raw = rec.partition(b"\t")
        mode, _kind, sha, size = meta.decode("ascii").split()
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            _fail(f"baseline path {raw!r} is not UTF-8")
        try:
            mc._check_path_text(path, "baseline path")
        except mc.MechanismCandidateError as exc:
            _fail(str(exc))
        if any(part.casefold() == ".git" for part in path.split("/")):
            _fail(f"baseline path {path!r} has a .git component")
        if mode == _GITLINK_MODE:
            _fail(f"baseline entry {path!r} is a gitlink (submodule); submodules are "
                  f"refused, never flattened")
        if mode not in _FILE_MODES and mode != _SYMLINK_MODE:
            _fail(f"baseline entry {path!r} has unsupported mode {mode}")
        if not size.isdigit():  # ls-tree -l prints BAD for a blob it cannot read
            _fail(f"baseline entry {path!r} has no readable blob (ls-tree size {size!r})")
        entries.append((path, mode, sha, int(size)))
    if not entries:
        _fail(f"baseline commit {commit} has an empty tree")
    return entries


def _read_blobs(repo_root, entries, max_bytes):
    """Blob contents by path, via one `git cat-file --batch`. The expanded size is checked
    against the cap from the tree listing before any content is read."""
    total = sum(size for _, _, _, size in entries)
    if total > max_bytes:
        _fail(_cap_msg(total, max_bytes))
    out = _git(repo_root, "cat-file", "--batch",
               data="".join(f"{sha}\n" for _, _, sha, _ in entries).encode("ascii"))
    blobs, pos = {}, 0
    try:
        for path, _, sha, size in entries:
            nl = out.index(b"\n", pos)
            if out[pos:nl].decode("ascii").split() != [sha, "blob", str(size)]:
                _fail(f"git cat-file returned an unexpected header for {path!r}")
            blobs[path] = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
    except ValueError:
        _fail("git cat-file output ended early")
    return blobs


def resolve_in_tree(path, links):
    """Resolve repo-relative `path` against a virtual tree whose symlinks are `links`
    ({path: target}), component by component like realpath. None when the walk leaves
    the root, hits an absolute target, or exceeds the hop limit (a loop)."""
    parts, todo, hops = [], path.split("/"), 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
        target = links.get("/".join(parts))
        if target is not None:
            hops += 1
            if hops > _MAX_SYMLINK_HOPS or target.startswith("/"):
                return None
            parts.pop()
            todo = target.split("/") + todo
    return "/".join(parts)


def _load_baseline(commit, repo_root, max_bytes):
    """(entries, blobs, manifest) for the pinned commit. Refusal order: gitlink, then
    Git LFS pointer, then a symlink resolving outside the snapshot."""
    entries = _list_tree(commit, repo_root)
    blobs = _read_blobs(repo_root, entries, max_bytes)
    links = {}
    for path, mode, _, _ in entries:
        if mode == _SYMLINK_MODE:
            try:
                links[path] = blobs[path].decode("utf-8")
            except UnicodeDecodeError:
                _fail(f"baseline symlink {path!r} has a non-UTF-8 target")
        elif blobs[path].startswith(_LFS_HEADER):
            _fail(f"baseline entry {path!r} is a Git LFS pointer; LFS content is refused, "
                  f"never fetched")
    for path, target in sorted(links.items()):
        if resolve_in_tree(path, links) is None:
            _fail(f"baseline symlink {path!r} -> {target!r} resolves outside the snapshot "
                  f"(or loops)")
    manifest = {}
    for path, mode, _, _ in entries:
        entry = {"mode": mode, "sha256": "sha256:" + hashlib.sha256(blobs[path]).hexdigest()}
        if mode == _SYMLINK_MODE:
            entry["target"] = links[path]
        manifest[path] = entry
    return entries, blobs, manifest


def compute_repo_digest(commit, repo_root, *, max_bytes=None):
    """repo_digest of a pinned commit: canonical_digest over {path: {mode, sha256[, target]}}
    from `git ls-tree -r` + `git cat-file` (never `git archive`, never the working tree)."""
    _check_baseline(Baseline(commit, str(repo_root)))
    cap = load_config()["max_workspace_bytes"] if max_bytes is None else max_bytes
    _, _, manifest = _load_baseline(commit, repo_root, cap)
    return mc.canonical_digest(manifest)


# ── Workspace construction ──────────────────────────────────────────────────────

def _write_snapshot(workspace, entries, blobs, cap):
    """Write the digested blobs with their recorded modes; symlinks last, so no regular
    file is ever written through one."""
    written = 0
    for path, mode, _, _ in sorted(entries, key=lambda e: e[1] == _SYMLINK_MODE):
        dest = os.path.join(workspace, *path.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == _SYMLINK_MODE:
            os.symlink(blobs[path].decode("utf-8"), dest)
            continue
        written += len(blobs[path])
        if written > cap:
            _fail(_cap_msg(written, cap))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(dest, flags, 0o755 if mode == "100755" else 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(blobs[path])


def _apply_diff(workspace, diff):
    """`git apply --check`, then `git apply`, in the snapshot (no .git, so git runs as a
    plain patch tool). Inherited GIT_* variables are dropped, repository discovery stops
    at the workspace, a baseline shaped like a bare repository (top-level HEAD, objects/,
    refs/, config) is never used as one, and no system/global config applies — so no
    other repository and no host or baseline config can steer the apply."""
    env = _git_env()
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(workspace))
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    for args in (("apply", "--check"), ("apply",)):
        proc = subprocess.run(["git", "-c", "safe.bareRepository=explicit", *args, "-"],
                              cwd=workspace, input=diff.encode("utf-8"),
                              capture_output=True, env=env, check=False)
        if proc.returncode != 0:
            _fail("candidate diff does not apply cleanly to the baseline: "
                  + proc.stderr.decode("utf-8", "replace").strip())


def _check_diff_lines(diff):
    """#464's extract_diff_paths() reads the diff with str.splitlines(), which also breaks
    on \\r, \\v, \\f, \\x1c-\\x1e, \\x85, U+2028 and U+2029; git breaks only on \\n. A
    diff the two would split differently is refused, so the lines that were validated are
    the lines that are applied."""
    lines = diff.split("\n")
    if diff.endswith("\n"):
        lines.pop()
    if diff.splitlines() != lines:
        _fail("candidate diff contains a line break other than '\\n' (\\r, \\v, \\f, "
              "\\x1c-\\x1e, \\x85, U+2028 or U+2029); mechanism_candidate and git would "
              "split it differently, so it is refused")


def _verify_applied(workspace, candidate, entries, blobs):
    """What git applied must be what #464 validated: every path whose content, type or
    exec bit differs from the digested baseline is named by the validated diff and lies
    under editable_path_allowlist, and no symlink was added, removed or retargeted. Never
    follows a symlink."""
    declared = set(mc.extract_diff_paths(candidate["diff"]))
    allowlist = candidate["editable_path_allowlist"]
    base = {path: mode for path, mode, _, _ in entries}
    seen, changed = set(), set()
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            st = os.lstat(full)
            if stat.S_ISDIR(st.st_mode):
                continue
            rel = os.path.relpath(full, workspace).replace(os.sep, "/")
            seen.add(rel)
            mode = base.get(rel)
            if stat.S_ISLNK(st.st_mode):
                if mode != _SYMLINK_MODE or os.fsencode(os.readlink(full)) != blobs[rel]:
                    _fail(f"candidate diff added or retargeted the symlink {rel!r}")
            elif not stat.S_ISREG(st.st_mode):
                _fail(f"workspace entry {rel!r} is not a regular file, directory or symlink")
            elif mode == _SYMLINK_MODE:
                _fail(f"candidate diff replaced the symlink {rel!r} with a regular file; "
                      f"removing a symlink is refused, never accepted as an ordinary change")
            elif (mode not in _FILE_MODES
                  or (mode == "100755") != bool(st.st_mode & stat.S_IXUSR)
                  or st.st_size != len(blobs[rel])
                  or Path(full).read_bytes() != blobs[rel]):
                changed.add(rel)
    for rel in sorted(set(base) - seen):
        if base[rel] == _SYMLINK_MODE:
            _fail(f"candidate diff removed the symlink {rel!r}")
        changed.add(rel)
    stray = sorted(p for p in changed if p not in declared or not mc._contained(p, allowlist))
    if stray:
        _fail(f"candidate diff changed paths the validated diff does not name: {stray}")


def _tree_bytes(root):
    """Total size of everything under root; never follows a symlink."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            if not stat.S_ISDIR(st.st_mode):
                total += st.st_size
    return total


def remove_workspace(workspace):
    if workspace and os.path.lexists(workspace):
        shutil.rmtree(workspace)


def _cleanup(workspace):
    """remove_workspace(), reporting a failure instead of raising it, so the original
    refusal survives and the leaked workspace is named."""
    try:
        remove_workspace(workspace)
    except OSError as exc:
        return f"workspace {workspace} was not removed: {exc}"
    return None


def _build_workspace(candidate, baseline, entries, blobs, cap):
    workspace = tempfile.mkdtemp(prefix="materializer-")
    try:
        roots = [baseline.repo_root] + [d for d in (os.environ.get("ARTIFACTS_DIR"),) if d]
        for root in roots:
            if _is_within(workspace, root):
                _fail(f"workspace {workspace} is inside {root}; it must be outside the "
                      f"clone and $ARTIFACTS_DIR")
        _write_snapshot(workspace, entries, blobs, cap)
        _check_diff_lines(candidate["diff"])
        _apply_diff(workspace, candidate["diff"])
        size = _tree_bytes(workspace)
        if size > cap:
            _fail(_cap_msg(size, cap))
        _verify_applied(workspace, candidate, entries, blobs)
    except BaseException as exc:
        leftover = _cleanup(workspace)
        if leftover and isinstance(exc, Exception):
            raise MaterializerError(f"{_error_text(exc)}; {leftover}") from exc
        raise
    return workspace


def build_workspace(candidate, baseline, *, config=None):
    """A fresh mkdtemp() workspace: the pinned commit's blobs plus the candidate diff.
    Outside the clone and $ARTIFACTS_DIR; no .git. The size cap is checked on the tree
    listing before any blob is read, while writing, and after the diff is applied. On
    any failure the workspace is removed before the error propagates; on success the
    caller owns remove_workspace()."""
    _check_baseline(baseline)
    cap = _config(config)["max_workspace_bytes"]
    entries, blobs, _ = _load_baseline(baseline.commit, baseline.repo_root, cap)
    return _build_workspace(candidate, baseline, entries, blobs, cap)


# ── bwrap argv translation (pure; Phase B launches it) ──────────────────────────

def validate_write_paths(candidate, workspace):
    """filesystem_envelope.write_paths as validated workspace-relative paths (no trailing
    '/', sorted). Each entry is a relative, normalised repo path under
    editable_path_allowlist that exists in the workspace with no symlink component."""
    allowlist = candidate["editable_path_allowlist"]
    name = "filesystem_envelope.write_paths entry"
    out = set()
    for entry in candidate["filesystem_envelope"]["write_paths"]:
        try:
            mc._check_path_text(entry, name)
        except mc.MechanismCandidateError as exc:
            _fail(str(exc))
        if mc._normalise(entry) != entry or entry in (".", "./"):
            _fail(f"{name} {entry!r} is not a normalised repo-relative path")
        if not mc._contained(entry, allowlist):
            _fail(f"{name} {entry!r} is not under editable_path_allowlist")
        rel = entry.rstrip("/")
        cur = workspace
        for part in rel.split("/"):
            cur = os.path.join(cur, part)
            if os.path.islink(cur):
                _fail(f"{name} {entry!r} passes through a symlink ({part!r})")
        if not os.path.exists(cur):
            _fail(f"{name} {entry!r} does not exist in the workspace")
        if not _is_within(cur, workspace) or os.path.realpath(cur) == os.path.realpath(workspace):
            _fail(f"{name} {entry!r} does not resolve inside the workspace")
        out.add(rel)
    return sorted(out)


def base_bind_dirs(exists=os.path.exists):
    """The minimum host directories needed to exec python3 or /bin/sh in the sandbox."""
    return [*BASE_BIND_DIRS, *(d for d in OPTIONAL_BASE_BIND_DIRS if exists(d))]


def build_bwrap_argv(candidate, workspace, config, *, argv=()):
    """The complete bwrap argument list for `argv` in `workspace`. Executes nothing.

    Bind sources are only the base dirs, the workspace, and validated workspace
    subpaths — never `/`, $HOME, $ARTIFACTS_DIR, the clone, or any credential path, by
    construction. The env is cleared to PATH/HOME/TMPDIR. prlimit sits after `--` so
    the rlimits bind the sandboxed child, never bwrap itself. `--disable-userns` blocks
    the sandboxed child from opening a further, unconfined user namespace of its own
    (bwrap >= 0.8); a seccomp filter is a Phase B launcher item, not this translator's.
    Each `--tmpfs` carries a matching `--size` (bwrap >= 0.9), so /tmp and $HOME are
    bounded by the same byte ceiling as the workspace instead of being able to exhaust
    host RAM/disk unbounded; RLIMIT_FSIZE still only caps one file at a time, and
    accounting the total written across tmpfs plus the bound write paths during an
    actual run (a quota, or a post-run walk) is Phase B's job, since Phase A never
    executes anything to recheck."""
    check_network(candidate)
    limits = resolve_limits(candidate, config)
    writes = validate_write_paths(candidate, workspace)
    tmpfs_size = str(limits["max_file_bytes"])
    args = ["bwrap", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup-try", "--unshare-net", "--disable-userns",
            "--die-with-parent", "--new-session",
            "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", SANDBOX_PATH, "--setenv", "HOME", SANDBOX_HOME,
            "--setenv", "TMPDIR", "/tmp"]
    for d in base_bind_dirs():
        args += ["--ro-bind", d, d]
    args += ["--size", tmpfs_size, "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
             "--size", tmpfs_size, "--tmpfs", SANDBOX_HOME]
    args += ["--ro-bind", workspace, SANDBOX_WORKSPACE]
    for rel in writes:
        args += ["--bind", os.path.join(workspace, *rel.split("/")), f"{SANDBOX_WORKSPACE}/{rel}"]
    args += ["--chdir", SANDBOX_WORKSPACE, "--",
             "prlimit", f"--as={limits['memory_limit_mb'] * 1024 * 1024}",
             f"--cpu={limits['cpu_seconds']}", f"--nproc={limits['max_nproc']}",
             f"--fsize={limits['max_file_bytes']}", "--", *argv]
    return args


def invocation_digest(args, workspace):
    """Digest of the resolved argv with the random workspace path normalised away."""
    return mc.canonical_digest([a.replace(workspace, "<workspace>") for a in args])


# ── Artifact bundle ─────────────────────────────────────────────────────────────

def _verdict_block(status, reason):
    findings, severity = (0, "none") if status == "PASS" else (1, "high")
    block = _verdict.parse_verdict(_verdict.format_verdict(GATE_TYPE, status, findings, severity))
    block["reason"] = reason
    return block


def _module_digest():
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def seal_bundle(bundle):
    """Add bundle_digest: canonical_digest over the bundle with that key absent."""
    body = {k: v for k, v in bundle.items() if k != "bundle_digest"}
    return {**body, "bundle_digest": mc.canonical_digest(body)}


def verify_bundle(bundle):
    """True when bundle_digest still matches the rest of the bundle. A bundle that cannot
    be digested at all (a lone surrogate, a non-JSON value) is False, never an exception:
    the bundle being verified is a file someone else may have written."""
    if not isinstance(bundle, dict) or "bundle_digest" not in bundle:
        return False
    try:
        return seal_bundle(bundle)["bundle_digest"] == bundle["bundle_digest"]
    except Exception:  # noqa: BLE001 — unverifiable is not verified
        return False


def resolve_out_dir(candidate, out_dir=None):
    """out_dir, else $ARTIFACTS_DIR/materializer/<candidate_id>/ (sanitized id); never
    the current directory."""
    if out_dir:
        return Path(out_dir)
    artifacts = os.environ.get("ARTIFACTS_DIR")
    if not artifacts:
        _fail("out_dir is required when $ARTIFACTS_DIR is unset (no fallback to the "
              "current directory)")
    cid = candidate.get("candidate_id") if isinstance(candidate, dict) else None
    if not isinstance(cid, str) or not _SAFE_ID_RE.fullmatch(cid):
        _fail(f"candidate_id {cid!r} is not safe for the default bundle path "
              f"(^[A-Za-z0-9_-]+$)")
    return Path(artifacts) / "materializer" / cid


def _write_bundle(dest, bundle):
    """Atomic, read-only bundle.json (a unique temp name, so concurrent writers never
    interleave). Any OSError is a MaterializerError: no bundle was written, which callers
    (and the CLI's exit 2) must never mistake for a BLOCKED one."""
    path = dest / "bundle.json"
    tmp = None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest, prefix=".bundle-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o444)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:  # ValueError: NUL or unencodable path
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        _fail(f"cannot write bundle to {dest}: {exc}")
    return path


def _text(v):
    """v if it is a str that encodes as UTF-8, else None. JSON (and surrogateescape argv)
    can carry lone surrogates, which canonical_digest() cannot seal; untrusted fields are
    copied into the bundle only through this."""
    if not isinstance(v, str):
        return None
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return v



def materialize(candidate, *, baseline, out_dir=None, config=None, known_digests=()):
    """Validate, digest, build and remove one candidate workspace; write and return its
    bundle. Accepts H0-H2. Every refusal is a BLOCKED bundle, never an exception; only
    an unwritable bundle location or a failed bundle write raises MaterializerError.
    Executes nothing from the workspace and never probes for bwrap
    (provenance.bwrap_available is null)."""
    dest = resolve_out_dir(candidate, out_dir)
    clone = None
    try:
        if isinstance(baseline, Baseline) and isinstance(baseline.repo_root, str) \
                and os.path.isdir(baseline.repo_root):
            roots = [baseline.repo_root]
            try:  # the real top level too: repo_root may name a subdirectory of the clone
                roots.append(_clone_top(baseline.repo_root))
            except MaterializerError:
                pass  # not a work tree; _check_baseline() refuses it below, as BLOCKED
            clone = next((root for root in roots if _is_within(dest, root)), None)
    except (OSError, ValueError) as exc:  # ValueError: NUL or unencodable path
        _fail(f"bundle directory {dest!r} is not a usable path: {exc}")
    if clone:
        _fail(f"bundle directory {dest} is inside the baseline clone {clone}")
    rec = candidate if isinstance(candidate, dict) else {}
    declared = rec.get("baseline") if isinstance(rec.get("baseline"), dict) else {}
    commit = baseline.commit if isinstance(baseline, Baseline) else None
    risk = computed = inv = limits = None
    workspace = None
    try:
        _check_baseline(baseline)
        cfg = _config(config)
        # The spec's safety source: the clone's working-tree .factory/adapter.yaml, not
        # content of the pinned commit (that is what repo_digest covers).
        risk = mc.validate_candidate(candidate, safety=mc.load_safety(baseline.repo_root),
                                     known_digests=known_digests)
        if risk not in _ACCEPTED_RISK:
            _fail(f"risk class {risk} is not materializable")
        if _text(candidate["candidate_id"]) is None:
            # Not an identity-bearing field, so #464's digest check does not see it; a
            # PASS bundle would carry candidate_id null and name no candidate.
            _fail("candidate_id is not UTF-8 encodable (a lone surrogate); a bundle that "
                  "cannot name its candidate is refused")
        check_network(candidate)
        limits = resolve_limits(candidate, cfg)
        cap = cfg["max_workspace_bytes"]
        entries, blobs, manifest = _load_baseline(baseline.commit, baseline.repo_root, cap)
        computed = mc.canonical_digest(manifest)
        if computed != candidate["baseline"]["repo_digest"]:
            _fail(f"baseline repo_digest mismatch: commit {baseline.commit} hashes to "
                  f"{computed}, the candidate declares {candidate['baseline']['repo_digest']}")
        # The workspace is written from the very blobs just digested, never a re-read.
        workspace = _build_workspace(candidate, baseline, entries, blobs, cap)
        inv = invocation_digest(build_bwrap_argv(candidate, workspace, cfg), workspace)
        status, reason = "PASS", ("workspace built from the digested baseline with the "
                                  "candidate diff applied; nothing executed")
    except Exception as exc:  # noqa: BLE001 — every failure is a BLOCKED bundle, never a crash
        status, reason = "BLOCKED", _error_text(exc)
    finally:
        leftover = _cleanup(workspace)
    if leftover:
        status, reason = "BLOCKED", f"{reason}; {leftover}"
    bundle = seal_bundle({
        "candidate_id": _text(rec.get("candidate_id")),
        "candidate_digest": _text(rec.get("candidate_digest")),
        "risk_class": risk,
        "baseline": {**{k: _text(declared.get(k)) for k in mc._BASELINE_FIELDS},
                     "commit": _text(commit), "computed_repo_digest": computed},
        "diff": _text(rec.get("diff")),
        "materialized_at": datetime.datetime.now(datetime.timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "materializer_version": MATERIALIZER_VERSION,
            "module_digest": _module_digest(),
            "bwrap_available": None,
            "invocation_digest": inv,
            "limits": limits,
        },
        "verdict": _verdict_block(status, reason.encode("utf-8", "backslashreplace")
                                         .decode("utf-8")),
    })
    _write_bundle(dest, bundle)
    return bundle


# ── CLI ─────────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("materialize", help="build, digest and bundle a candidate workspace "
                                           "(executes nothing)")
    m.add_argument("--candidate", required=True, help="candidate record JSON file")
    m.add_argument("--commit", required=True, help="full 40-hex baseline commit SHA")
    m.add_argument("--repo-root", required=True, help="clone containing --commit")
    m.add_argument("--out-dir", default="",
                   help="bundle directory (default $ARTIFACTS_DIR/materializer/<candidate_id>/)")
    m.add_argument("--known-digest", action="append", default=[],
                   help="a candidate_digest parent/dependency digests may resolve to (repeatable)")
    return p.parse_args(argv)


_MAX_CANDIDATE_BYTES = 16 * 1024 * 1024


def _load_candidate(path):
    """The candidate JSON, read through a size cap: the file is agent-written."""
    with open(path, "rb") as f:
        raw = f.read(_MAX_CANDIDATE_BYTES + 1)
    if len(raw) > _MAX_CANDIDATE_BYTES:
        raise ValueError(f"file is larger than {_MAX_CANDIDATE_BYTES} bytes")
    return json.loads(raw.decode("utf-8"))


def main(argv=None):
    """Exit 1 means "BLOCKED, bundle written" and nothing else. An uncaught exception
    would also exit 1, so both steps catch Exception, not a list of the types seen so
    far (OSError, ValueError, RecursionError, MemoryError, ...): exit 2, no bundle."""
    args = parse_args(argv)
    try:
        candidate = _load_candidate(args.candidate)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        print(f"materializer: cannot read candidate {args.candidate!r}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    try:
        bundle = materialize(candidate, baseline=Baseline(args.commit, args.repo_root),
                             out_dir=args.out_dir or None, known_digests=args.known_digest)
    except Exception as exc:  # noqa: BLE001 — materialize() raises only before a bundle exists
        print(f"materializer: no bundle written: {_error_text(exc)}", file=sys.stderr)
        return 2
    v = bundle["verdict"]
    sys.stdout.write(_verdict.format_verdict(GATE_TYPE, v["status"], v["findings_count"],
                                             v["severity"]))
    if v["status"] != "PASS":
        print(f"BLOCKED: {v['reason']}", file=sys.stderr)
    return 0 if v["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

# Model Registry: Bind the Staged Digest to the Source, Read Each File Once — Implementation Plan

**Issue:** #593

**Spec:** `docs/superpowers/specs/2026-11-24-model-registry-digest-bind-read-once-design.md`
(operator-amended at `646e858`)

**Goal:** Close the two #557 registry-digest defects before #558 consumes the digest.
(1) `stage_registry()` returns the read-only **source** digest and fails if the staged copy
differs from it. (2) Every staged-registry decision (`model_endpoint.preflight()`,
`providers._endpoint_preflight()`'s `hosting`, `model-endpoints show`) is made on the bytes
that were hashed, read once. Also add the missing skip-the-swap regression test and the
agent-controlled-env comments.

**Architecture:** A new `_read_tree(root) -> (digest, {rel_path: bytes})` walks the tree
once, with the same walk, sort and symlink rejection `tree_sha256()` has today, plus a 1 MiB
per-file cap. `tree_sha256()` becomes `_read_tree(root)[0]`. `read_verified_tree()` is the
only staged-tree reader. It checks `FACTORY_MODEL_REGISTRY_SHA256`, calls `_read_tree()`,
compares the digest and returns the bytes it hashed. It replaces `verify_staged_digest()`,
which is deleted. `load_registry()`/`load_evidence()` keep their path-based signatures and
gain bytes-based cores (`_parse_registry()`, `_parse_evidence()`). `preflight()` and
`redacted_view_staged()` parse only from the `files` map. `preflight(registry_path,
endpoint_id)` and `scheduler_preflight()` return `(problems, hosting)`.
`hosting_location_of()` is deleted. `_endpoint_preflight()` takes `hosting` from the call it
already makes. No end-of-function digest recheck remains, because there is no second read
for one to guard.

**Tech Stack:** Python 3.12 (CI) / stdlib `hashlib`, `hmac`, `os`; PyYAML; pytest.

**Out of scope (do not touch):** `scheduler.sh`, `scripts/provider_env.sh`, `entrypoint.sh`,
`deploy/**`, `.github/workflows/**`, gates, breaker, budgets. `provider_env.sh::model_registry_env_flags()`
already fails closed on a non-zero `model-endpoints stage` exit. So Task 1's new
`RegistryError` needs no shell change (memory: `dark-factory-ops.md` [AVOID], #593).
`providers.preflight()`'s public `list[str]` return is unchanged.

**Hotspot note:** `scripts/factory_core/providers/__init__.py` is a blast-radius hotspot. Expect
a hold and an owner signature. Keep its diff limited to `_endpoint_preflight()` (Task 4).

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | `MAX_FILE_BYTES`; `_read_file`/`_parse_yaml` replace `_read_yaml`; `_parse_registry`, `_parse_evidence` cores; `_read_tree` + `tree_sha256` wrapper; `stage_registry` binds to source; `read_verified_tree` replaces `verify_staged_digest`; `_pick`; `_local_problems(entry, files, registry_path)`; `preflight`/`scheduler_preflight` return `(problems, hosting)`; `hosting_location_of` deleted; `redacted_view_staged` + `_redact`; agent-env comments |
| `scripts/factory_core/providers/__init__.py` | `_endpoint_preflight()` only: no pre-read, unpack `(problems, hosting)`, comment rewritten |
| `scripts/factory_core/providers/cli.py` | `_model_endpoints_show()` only: staged path → `redacted_view_staged()`; second verify removed |
| `tests/test_provider_model_endpoint.py` | New Req 1/2/5 tests; ported `read_verified_tree` tests; tuple updates; two tests deleted, one rewritten |
| `tests/test_provider_cli.py` | `show` read-once test; staged-copy-without-registry-file test |

## Conventions for every task

- Run tests from the repo root with `PYTHONPATH=scripts python -m pytest <file> -v` (CI:
  `.github/workflows/ci.yml`, `PYTHONPATH: scripts`). If pytest is missing locally, install
  CI's set: `pip install pytest pyyaml aiohttp`. (`factory_core/__init__.py` imports
  `aiohttp`, so the test modules fail to collect without it.)
- Baseline before Task 1 (current `main` `848c6a4`): `tests/test_provider_model_endpoint.py`
  → `160 passed`; `tests/test_provider_cli.py` → `32 passed`.
- Commit messages: `fix(model): <what> (#593)`. Every commit block below passes a second
  `-m` with the session's `Co-Authored-By:` attribution trailer. Keep it when copying.
- Memory rule (architecture.md [AVOID] ×2, #593): never add a "read hosting separately,
  then preflight" step, and never guard a staged read with a hash-then-reread-then-recheck.
  Every staged decision must parse the `files` bytes `read_verified_tree()` returned.

---

## Task 1: `stage_registry()` binds its digest to the read-only source (Req 1) + skip-the-swap regression test (Req 5)

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`stage_registry`, lines 311–349)
- Test: `tests/test_provider_model_endpoint.py` (Task 2 section, after `test_stage_copy_failure_leaves_no_partial_tree`, line ~279)

- [ ] **Step 1: Write the tests.** Insert after `test_stage_copy_failure_leaves_no_partial_tree`:

```python
def test_stage_fails_when_the_staged_copy_changes_mid_staging(tmp_path, monkeypatch):
    # #593 defect 1: a run container shares the volume and can write the temp copy between
    # copytree and the digest. Staging must fail rather than bless the tampered tree.
    dest = tmp_path / "state" / "model-registry"
    good = me.stage_registry(_write_registry(tmp_path / "good", [LOCAL]), dest)
    src = _write_registry(tmp_path / "src", [CLOUD])
    real_copytree = me.shutil.copytree

    def _copy_then_inject(source, target, **kw):
        result = real_copytree(source, target, **kw)
        _write_registry(Path(target), [CLOUD, _entry(CLOUD, id="cloud-injected")])
        return result
    monkeypatch.setattr(me.shutil, "copytree", _copy_then_inject)
    with pytest.raises(me.RegistryError, match="does not match the read-only source digest"):
        me.stage_registry(src, dest)
    assert me.tree_sha256(dest) == good
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model-registry"]


def test_stage_skips_the_swap_when_unchanged(tmp_path):
    # Pins bd6be81's fast path: an unchanged source never replaces dest, so a concurrently
    # starting run never sees it missing.
    src = _write_registry(tmp_path / "src", [CLOUD])
    dest = tmp_path / "state" / "model-registry"
    first = me.stage_registry(src, dest)
    before = os.stat(dest).st_ino
    assert me.stage_registry(src, dest) == first == me.tree_sha256(src)
    assert os.stat(dest).st_ino == before
    assert not list(dest.parent.glob(f".{dest.name}.tmp-*"))
```

- [ ] **Step 2: Run them. Expect one failure and one pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v -k "mid_staging or skips_the_swap"
```

Expected: `test_stage_fails_when_the_staged_copy_changes_mid_staging FAILED` with
`Failed: DID NOT RAISE <class '...RegistryError'>`.
`test_stage_skips_the_swap_when_unchanged PASSED`. This one is a regression pin for
already-shipped behaviour (spec table: "passes (pins `bd6be81`)"), so it is green before
any code change.

- [ ] **Step 3: Implement.** In `stage_registry()`, replace the docstring and the body
between `shutil.copytree(...)` and the end of the function:

```python
def stage_registry(source=None, dest=None) -> str:
    """Copy the read-only source registry onto the shared volume; return the source's digest.

    The copy goes to a temp directory beside dest and is swapped in by rename, so a failed
    copy never leaves a half-written registry where a run container reads it. The returned
    digest is the read-only source's: a copy that differs from it (a run container shares
    the volume and can write the temp directory) fails staging instead of being blessed.
    """
```

```python
        shutil.copytree(source, tmp, dirs_exist_ok=True)
        staged_digest = tree_sha256(tmp)
        if staged_digest != source_digest:
            raise RegistryError([
                f"{tmp}: staged copy digest does not match the read-only source digest "
                f"({staged_digest} != {source_digest}) — the source or the staged copy changed "
                "mid-staging; refusing to publish a digest that does not bind the source"])
        if dest.exists():
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if old.exists():
                os.replace(old, dest)
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    return source_digest
```

The mismatch check runs before either `os.replace`, so `dest` is never touched. The
existing `except BaseException` removes `tmp`. `providers/cli.py::_model_endpoints_stage()`
already turns `RegistryError` into exit 1, so no other file changes.

- [ ] **Step 4: Run the whole file. Expect all green.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v
```

Expected: `162 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "fix(model): stage_registry returns the source digest and fails on a changed copy (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Read-once primitives — `_read_tree`, 1 MiB cap, bytes-based parse cores (Req 2, groundwork)

This task only refactors. No caller changes behaviour yet, apart from the new 1 MiB cap.

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (constants block line ~47; `_read_yaml` lines 124–135; `load_registry` 258–286; `tree_sha256` 289–303; `load_evidence` 392–401)
- Test: `tests/test_provider_model_endpoint.py` (after `test_tree_sha256_rejects_symlinks`, line ~238)

- [ ] **Step 1: Write the failing test.** Insert after `test_tree_sha256_rejects_symlinks`:

```python
def test_tree_sha256_fails_closed_on_an_oversized_file(tmp_path):
    # The staged tree is writable by run containers and is read into memory: cap each file.
    reg = _write_registry(tmp_path / "reg", [LOCAL])
    (reg / "big.yaml").write_bytes(b"#" * (me.MAX_FILE_BYTES + 1))
    with pytest.raises(me.RegistryError) as exc:
        me.tree_sha256(reg)
    assert exc.value.problems == [f"{reg}: big.yaml is larger than {me.MAX_FILE_BYTES} bytes"]
```

- [ ] **Step 2: Run it. Expect a failure.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v -k oversized
```

Expected: `FAILED ... AttributeError: module 'factory_core.providers.model_endpoint' has no attribute 'MAX_FILE_BYTES'`.

- [ ] **Step 3: Add the constant.** Directly after the `TIMEOUT_FIELDS = (...)` line:

```python
# Per-file read cap for a registry tree (#593): larger files fail closed.
MAX_FILE_BYTES = 1024 * 1024
```

- [ ] **Step 4: Replace `_read_yaml()`** (whole function) with a reader and a bytes parser:

```python
def _read_file(path) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise RegistryError([f"{path}: unreadable ({e.strerror})"]) from None


def _parse_yaml(data, where):
    """Parse YAML from bytes already read, so a caller can hash and parse the same read."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError([f"{where}: not valid UTF-8"]) from None
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 — SafeLoader subclass
    except yaml.YAMLError as e:
        # Never echo the offending line: a pasted secret would land in the log.
        mark = getattr(e, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark else ""
        problem = getattr(e, "problem", None) or "parse error"
        raise RegistryError([f"{where}: invalid YAML{line}: {problem}"]) from None
```

Decoding with `utf-8` matches today's `open(path, encoding="utf-8")`. Invalid UTF-8 was an
uncaught `UnicodeDecodeError` before. It now becomes a `RegistryError` problem, and both
paths still fail closed.

- [ ] **Step 5: Split `load_registry()`.** Replace its first lines, through `doc = _read_yaml(path)`, with
the following. The rest of the old body, from `if not isinstance(doc, dict):` to
`return endpoints`, stays unchanged as the body of `_parse_registry`:

```python
def load_registry(path) -> list[dict]:
    """Parse and validate model-endpoints.yaml (spec §B). Never reads the environment."""
    path = Path(path)
    if not path.is_file():
        raise RegistryError([f"model endpoint registry not found at {path}"])
    return _parse_registry(_read_file(path), path)


def _parse_registry(data, path) -> list[dict]:
    """load_registry() over bytes already read; path only labels the messages."""
    doc = _parse_yaml(data, path)
    if not isinstance(doc, dict):
        raise RegistryError([f"{path}: must be a mapping with schema_version and endpoints"])
    # ... remainder of the existing body, unchanged ...
```

(The `# ... remainder` line marks unchanged existing code. Do not add it to the file.)

- [ ] **Step 6: Replace `tree_sha256()`** with `_read_tree()` plus a thin wrapper:

```python
def _read_tree(root) -> tuple[str, dict[str, bytes]]:
    """One walk of a directory tree: its SHA-256 (sorted relative paths and file bytes) and
    the bytes that were hashed, keyed by relative POSIX path (#593).

    A caller that parses these bytes decides on exactly what the digest covers; re-opening a
    file afterwards would reopen the swap-and-swap-back window this closes.
    """
    root = Path(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):  # never descends into a symlink
        entries += [Path(dirpath) / name for name in dirnames + filenames]
    digest = hashlib.sha256()
    files = {}
    for path in sorted(entries):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RegistryError([f"{root}: {rel} is not a regular file or directory"])
        if path.is_file():
            # The staged tree is writable by run containers: cap what is read into memory.
            too_big = [f"{root}: {rel} is larger than {MAX_FILE_BYTES} bytes"]
            if path.stat().st_size > MAX_FILE_BYTES:
                raise RegistryError(too_big)
            with open(path, "rb") as f:
                data = f.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise RegistryError(too_big)
            files[rel] = data
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest(), files


def tree_sha256(root) -> str:
    """SHA-256 over a directory tree: sorted relative paths and file bytes."""
    return _read_tree(root)[0]
```

The digest bytes are identical to today's: same path order and the same `rel\0` +
`hexdigest\n` framing. `test_tree_sha256_is_stable_and_content_sensitive` and every
digest-equality test therefore stay green. The size check runs before the read, as the spec
requires. The bounded `read(MAX+1)` also catches a file that grows between `stat` and
`open`.

- [ ] **Step 7: Split `load_evidence()`.** Replace its body from `doc = _read_yaml(path)` down:

```python
def load_evidence(evidence_dir, endpoint_id):
    """The capability-evidence record for endpoint_id, or None if there is none."""
    path = Path(evidence_dir) / f"{endpoint_id}.yaml"
    if not path.is_file():
        return None
    return _parse_evidence(_read_file(path), path)


def _parse_evidence(data, path) -> dict:
    """load_evidence() over bytes already read; path only labels the messages."""
    doc = _parse_yaml(data, path)
    if (not isinstance(doc, dict) or set(doc) != {"capability_evidence"}
            or not isinstance(doc["capability_evidence"], dict)):
        raise RegistryError([f"{path}: must contain exactly one capability_evidence mapping"])
    return doc["capability_evidence"]
```

- [ ] **Step 8: Confirm `_read_yaml` is gone, then run the file. Expect all green.**

```bash
grep -n "_read_yaml" scripts/factory_core/providers/model_endpoint.py   # expect no output
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v
```

Expected: `163 passed` (model_endpoint) + `32 passed` (cli) = `195 passed`.

- [ ] **Step 9: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "fix(model): read-once tree reader with a 1 MiB per-file cap and bytes-based parsers (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 3: `read_verified_tree()` — the one staged-tree reader (Req 2; Req 6 comment)

`verify_staged_digest()` becomes a one-line shim over `read_verified_tree()` here, so
`preflight()` and `cli.py` keep working between commits. Task 5 deletes the shim along with
its last caller.

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`verify_staged_digest`, lines 352–372)
- Test: `tests/test_provider_model_endpoint.py` (the four `test_verify_staged_digest_*` tests, lines 287–308)

- [ ] **Step 1: Port the four tests and add the oversized case.** Replace
`test_verify_staged_digest_requires_the_digest`, `..._rejects_malformed_digest`,
`..._missing_dir_never_falls_back` and `..._detects_tampering` with:

```python
def test_read_verified_tree_requires_the_digest(tmp_path):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    problems, files = me.read_verified_tree(reg)
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in problems[0] and files is None


def test_read_verified_tree_rejects_malformed_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "not-hex")
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    assert me.read_verified_tree(reg) == (
        ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"], None)


def test_read_verified_tree_missing_dir_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    problems, files = me.read_verified_tree(tmp_path / "absent")
    assert "no fallback to cloud" in problems[0] and files is None


def test_read_verified_tree_detects_tampering(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert me.read_verified_tree(reg) == ([], {me.REGISTRY_FILE: (reg / me.REGISTRY_FILE).read_bytes()})
    _write_registry(reg, [_entry(CLOUD, accepts_payload_classes=["cloud_ok"], id="cloud-evil")])
    problems, files = me.read_verified_tree(reg)
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0] and files is None


def test_read_verified_tree_fails_closed_on_an_oversized_file(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    (reg / "big.yaml").write_bytes(b"#" * (me.MAX_FILE_BYTES + 1))
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    problems, files = me.read_verified_tree(reg)
    assert problems == [f"{reg}: big.yaml is larger than {me.MAX_FILE_BYTES} bytes"] and files is None
```

- [ ] **Step 2: Run them. Expect failures.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v -k read_verified_tree
```

Expected: 5 `FAILED`, each with `AttributeError: ... has no attribute 'read_verified_tree'`.

- [ ] **Step 3: Implement.** Replace the whole `verify_staged_digest()` function with:

```python
def read_verified_tree(registry_path) -> tuple[list[str], dict[str, bytes] | None]:
    """The one staged-tree reader (#593): ([], files) only if the bytes read match the digest
    the scheduler passed; (problems, None) otherwise.

    files maps each relative path to the bytes that were hashed. Parse those — never re-open a
    staged file, or a swap-and-swap-back between the two reads goes unseen.
    """
    # Registry verification must never run from agent-controlled env (#593): today only the
    # pre-agent entrypoint.sh preflight reads FACTORY_MODEL_REGISTRY_SHA256, and #558 must
    # keep it that way.
    expected = os.environ.get("FACTORY_MODEL_REGISTRY_SHA256", "")
    if not expected:
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not set — the staged model registry is trusted "
                "only with the digest the scheduler passes to this dispatch"], None
    if not _SHA256_HEX_RE.match(expected):
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"], None
    registry_path = Path(registry_path)
    if not registry_path.is_dir():
        return [f"staged model registry not found at {registry_path} — no fallback to cloud"], None
    try:
        actual, files = _read_tree(registry_path)
    except RegistryError as e:
        return e.problems, None
    except OSError:
        return [f"staged model registry at {registry_path} is unreadable"], None
    if not hmac.compare_digest(actual, expected):
        return [f"staged model registry at {registry_path} does not match "
                "FACTORY_MODEL_REGISTRY_SHA256 — it changed after the scheduler staged it"], None
    return [], files


def verify_staged_digest(registry_path) -> list[str]:
    # Transitional shim, deleted in this ticket's Task 5 together with its last caller.
    return read_verified_tree(registry_path)[0]
```

All problem messages are byte-identical to today's `verify_staged_digest()`.

- [ ] **Step 4: Run both files. Expect all green.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v
```

Expected: `164 passed` + `32 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "fix(model): read_verified_tree returns the bytes it hashed (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 4: `preflight()`/`scheduler_preflight()` decide on one read and return `(problems, hosting)`; `_endpoint_preflight()` drops its pre-read (Req 2, Req 3)

These signature changes and their only non-test caller (`providers/__init__.py`) must land
in one commit.

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`_select`, `hosting_location_of`, `_local_problems`, `preflight`, `scheduler_preflight`; lines ~500–581)
- Modify: `scripts/factory_core/providers/__init__.py` (`_endpoint_preflight`, lines 79–102 only)
- Test: `tests/test_provider_model_endpoint.py`

- [ ] **Step 1: Add the swap-and-swap-back helper and the two read-once tests.** Add
`import io` to the top-of-file imports (alphabetical, before `import os`). Then append this
block after `test_preflight_output_never_contains_secret_values` (end of the "Task 4:
endpoint preflight" section):

```python
def _serve_first_read(monkeypatch, root, tampered):
    """Swap-and-swap-back (#593): each file under root reads as its real bytes on its first
    read and as tampered[rel] on every later read. Returns {rel: read count}.

    Hooks both read primitives this module has used (open() and Path.read_bytes), so a
    re-read is observed whichever primitive performs it.
    """
    root = Path(root).resolve()
    real_open, real_read_bytes = open, Path.read_bytes
    reads = {}

    def _serve(path):
        try:
            rel = Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            return None
        reads[rel] = reads.get(rel, 0) + 1
        if reads[rel] > 1 and rel in tampered:
            return tampered[rel]
        return real_read_bytes(Path(path))

    def _open(file, mode="r", *args, **kwargs):
        data = _serve(file) if isinstance(file, (str, os.PathLike)) else None
        if data is None:
            return real_open(file, mode, *args, **kwargs)
        return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode("utf-8"))

    def _read_bytes(self):
        data = _serve(self)
        return real_read_bytes(self) if data is None else data

    monkeypatch.setattr(me, "open", _open, raising=False)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    return reads


def _registry_bytes(endpoints):
    return yaml.safe_dump({"schema_version": "1.0", "endpoints": endpoints},
                          sort_keys=False).encode("utf-8")


def test_preflight_uses_only_the_first_read_of_each_staged_file(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    evidence = f"{me.EVIDENCE_DIR}/local-test.yaml"
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(CLOUD, id="local-test")]),
        evidence: yaml.safe_dump({"capability_evidence": _record(
            LOCAL, results=dict(_record(LOCAL)["results"], streaming="fail"))}).encode("utf-8"),
    })
    assert me.preflight(reg, "local-test") == ([me._NO_LOCAL_DISPATCH.format(eid="local-test")], "local")
    assert reads == {me.REGISTRY_FILE: 1, evidence: 1}


def test_swap_and_swap_back_cannot_change_the_endpoint_decision(base_env, tmp_path, monkeypatch):
    # Through the public, unchanged providers.preflight(): on main the later reads see the
    # swapped-in local entry and preflight fails on them. After #593 there are no later reads.
    from factory_core import providers
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(LOCAL, id="cloud-test")])})
    assert providers.preflight() == []
    assert reads == {me.REGISTRY_FILE: 1}
```

`test_swap_and_swap_back_...` uses the `base_env` fixture, which is defined later in the
file. pytest resolves fixtures by name, so the order does not matter.

- [ ] **Step 2: Add the missing-registry-file test.** Replace
`test_preflight_rechecks_digest_after_reading` (whole function). It patches `_select`,
which `preflight()` no longer calls, and the read-once tests above supersede it:

```python
def test_preflight_without_registry_file_reports_it_not_found(tmp_path, monkeypatch):
    reg = tmp_path / "staged"
    (reg / me.EVIDENCE_DIR).mkdir(parents=True)
    (reg / me.EVIDENCE_DIR / "cloud-test.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert _run_preflight(reg, "cloud-test", None) == [
        f"model endpoint registry not found at {reg / me.REGISTRY_FILE}"]
```

- [ ] **Step 3: Update `_run_preflight` and its call sites to the tuple API.** Replace the helper:

```python
def _run_preflight(reg, endpoint_id, hosting):
    """me.preflight()'s problems, after asserting the hosting it returned with them."""
    problems, got = me.preflight(reg, endpoint_id)
    assert got == hosting
    return problems
```

Then make each call site pass its expected hosting. Every old `_run_preflight(reg, "local-test")` becomes
`_run_preflight(reg, "local-test", "local")`. That covers 7 sites, in
`test_local_selection_missing_secret_...`, `..._fails_when_a_cloud_credential_is_present`,
`..._requires_matching_base_url`, `..._requires_every_tier_alias_...`,
`..._requires_evidence`, `..._always_fails_as_an_existing_phase` and
`..._with_failed_dimension_reports_it`. The remaining sites:

| Test | New call |
|---|---|
| `test_cloud_selection_passes_with_secret_and_digest` | `assert _run_preflight(reg, "cloud-test", "cloud") == []` |
| `test_cloud_selection_needs_no_evidence_but_names_missing_secret` | `assert _run_preflight(reg, "cloud-test", "cloud") == ["ANTHROPIC_API_KEY is not set. Add it to .archon/.env"]` |
| `test_unknown_endpoint_fails_without_choosing_another` | `problems = _run_preflight(reg, "cloud-nope", None)` |
| `test_preflight_without_digest_fails_before_reading_registry` | `... in _run_preflight(reg, "cloud-test", None)[0]` |
| `test_preflight_fails_on_tampered_staged_copy` | `problems = _run_preflight(reg, "cloud-test", None)` |
| `test_preflight_reports_schema_violations` | `... for p in _run_preflight(reg, "cloud-test", None))` |
| `test_preflight_output_never_contains_secret_values` | loop becomes `for eid, hosting in (("local-test", "local"), ("cloud-test", "cloud")):` with body `assert SENTINEL not in " ".join(_run_preflight(reg, eid, hosting))` |

- [ ] **Step 4: Update the scheduler-side tests.** Replace `test_scheduler_preflight_checks_schema_and_id_only`,
`test_scheduler_preflight_blocks_a_local_selection` and `test_hosting_location_of` with the
following. The `hosting_location_of()` test is deleted, and its absent-registry case moves
to `scheduler_preflight`:

```python
def test_scheduler_preflight_checks_schema_and_id_only(tmp_path):
    src = _write_registry(tmp_path / "src", [CLOUD, LOCAL])
    assert me.scheduler_preflight(src, "cloud-test") == ([], "cloud")
    problems, hosting = me.scheduler_preflight(src, "nope")
    assert "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in problems[0] and hosting is None


def test_scheduler_preflight_blocks_a_local_selection(tmp_path):
    src = _write_registry(tmp_path / "src", [LOCAL])
    assert me.scheduler_preflight(src, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], "local")


def test_scheduler_preflight_without_a_registry_has_no_hosting(tmp_path):
    problems, hosting = me.scheduler_preflight(tmp_path / "absent", "local-test")
    assert "model endpoint registry not found" in problems[0] and hosting is None
```

- [ ] **Step 5: Rewrite `test_undetermined_hosting_never_skips_the_cloud_credential_check`.**
It patched the deleted `hosting_location_of`. It now patches `scheduler_preflight` to
return `([], None)` and restores it before the unknown-id half:

```python
def test_undetermined_hosting_never_skips_the_cloud_credential_check(base_env, tmp_path, monkeypatch):
    # The endpoint check reports no problem but no hosting either: the missing cloud
    # credential must still be reported.
    from factory_core import providers
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))
    real_scheduler_preflight = me.scheduler_preflight
    monkeypatch.setattr(me, "scheduler_preflight", lambda registry_dir, endpoint_id: ([], None))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert providers.preflight(side="scheduler") == [
        "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    # An unknown id still fails on the endpoint check alone, with no credential noise.
    monkeypatch.setattr(me, "scheduler_preflight", real_scheduler_preflight)
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "nope")
    problems = providers.preflight(side="scheduler")
    assert len(problems) == 1 and "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in problems[0]
```

- [ ] **Step 6: Run the file. Expect failures** (production code still has the old shapes).

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v
```

Expected failures:
- `test_preflight_uses_only_the_first_read_of_each_staged_file`: `TypeError: preflight() missing 1 required positional argument: 'endpoint_id'`.
- `test_swap_and_swap_back_cannot_change_the_endpoint_decision`: `AssertionError` whose left side is
  `['staged model registry at ... does not match FACTORY_MODEL_REGISTRY_SHA256 — it changed after the scheduler staged it']`.
  This is the red-on-`main` proof. A later read saw the swapped bytes. The branch state
  after Tasks 1–3 is equivalent to `main` here, because the hooked `open`/`Path.read_bytes`
  catch the re-read either way. This test was also run against unmodified `main` and fails
  the same way.
- Every `_run_preflight`/`scheduler_preflight` test: `TypeError` or tuple-vs-list `AssertionError`.
- `test_undetermined_hosting_...`: `TypeError` (tuple `+=` list).

- [ ] **Step 7: Implement in `model_endpoint.py`.** (a) Replace `_select()` and `hosting_location_of()` (delete the latter) with:

```python
def _pick(endpoints, endpoint_id, path):
    for entry in endpoints:
        if entry["id"] == endpoint_id:
            return entry, []
    return None, [f"Unknown FACTORY_MODEL_ENDPOINT '{endpoint_id}' — no such id in {path}; "
                  "no other endpoint is chosen instead"]


def _select(registry_path, endpoint_id):
    path = Path(registry_path) / REGISTRY_FILE
    try:
        endpoints = load_registry(path)
    except RegistryError as e:
        return None, e.problems
    return _pick(endpoints, endpoint_id, path)
```

(b) Change `_local_problems`'s signature to `def _local_problems(entry, files, registry_path):`
and replace its `record = load_evidence(evidence_dir, eid)` `try` line with the following.
The `except`/`else` branches stay unchanged:

```python
    # The evidence record comes from the verified tree's bytes, never a separate read (#593).
    rel = f"{EVIDENCE_DIR}/{eid}.yaml"
    try:
        record = _parse_evidence(files[rel], registry_path / rel) if rel in files else None
    except RegistryError as e:
```

`registry_path` is passed only so that evidence messages keep today's exact path,
`<registry>/capability-evidence/<id>.yaml`. The spec sketches `_local_problems(entry,
files)`, and this extra argument is the only difference. A missing `files` key is "no
evidence record", the same as `load_evidence()` returning `None`.

(c) Replace `preflight()` and `scheduler_preflight()` with:

```python
def preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    """Run-container check of the staged registry for one selected endpoint.

    Returns (problems, hosting); ([], hosting) == OK. hosting is the selected entry's
    hosting_location, or None if no entry was selected. Every decision is made on the bytes
    read_verified_tree() hashed, so no file is read twice (#593).
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    entry, problems = _pick(endpoints, endpoint_id, path)
    if entry is None:
        return problems, None
    hosting = entry["hosting_location"]
    ref = entry["secret_reference"]
    if hosting == "cloud":
        problems += _missing([ref])  # a cloud entry's secret arrives via .archon/.env
    elif not os.environ.get(ref):
        # Not _missing(): a local secret must not be delivered through .archon/.env (spec §C).
        problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is not set")
    if hosting == "local":
        problems += _local_problems(entry, files, registry_path)
    return problems, hosting


def scheduler_preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    """Scheduler boot check on its read-only source mount: schema, selected id, §4.11 decision 3.

    Returns (problems, hosting), like preflight(). The digest, secret presence and
    credential/alias guards are the run container's: it holds the environment they check.
    """
    entry, problems = _select(registry_path, endpoint_id)
    hosting = entry["hosting_location"] if entry else None
    if hosting == "local":
        problems.append(_NO_LOCAL_DISPATCH.format(eid=entry["id"]))
    return problems, hosting
```

The `evidence_dir` parameter is gone, so evidence can only come from the digest-covered
tree. The closing `verify_staged_digest()` recheck is gone as well, because no second read
remains for it to guard (spec Alternatives §1).

- [ ] **Step 8: Implement in `providers/__init__.py`.** Replace the body of `_endpoint_preflight()`
after the `from factory_core.providers import model_endpoint` line with the following. This
removes both `registry_dir` branches, the `hosting_location_of` pre-read and its "Read
before preflight()" comment:

```python
    # hosting comes from the same read the endpoint checks used — never a separate pre-read,
    # which a swap-and-swap-back could answer differently (#593).
    if side == "scheduler":
        problems, hosting = model_endpoint.scheduler_preflight(
            model_endpoint.SOURCE_REGISTRY_DIR, endpoint_id)
    else:
        problems, hosting = model_endpoint.preflight(
            model_endpoint.staged_registry_dir(), endpoint_id)
    # A local selection holds no cloud model credential, so the cloud credential check is
    # skipped for it; a cloud selection runs it in addition to the endpoint checks. hosting is
    # None when no entry was selected (unknown id, invalid or unverified registry): those
    # problems already fail preflight and the cloud check would only add unrelated credential
    # errors — but with no problem to explain it, the cloud check is never skipped.
    if hosting == "cloud" or (hosting is None and not problems):
        problems += model.preflight(model_name)
    return problems
```

Leave the signature (`-> list[str]`), the import comment and everything else in the file
untouched.

- [ ] **Step 9: Confirm no stale references remain, then run both files. Expect all green.**

```bash
grep -rn "hosting_location_of\|preflight(.*evidence_dir\|EVIDENCE_DIR, endpoint_id" --include="*.py" scripts/ tests/   # expect no output
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v
```

Expected: `166 passed` + `32 passed`. (164, minus `test_preflight_rechecks_digest_after_reading`
and `test_hosting_location_of`, plus the read-once ×2, not-found and
scheduler-without-registry tests.)

- [ ] **Step 10: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py scripts/factory_core/providers/__init__.py tests/test_provider_model_endpoint.py
git commit -m "fix(model): preflight decides on one read and returns hosting; drop the hosting pre-read (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 5: `model-endpoints show` reads the staged copy once; delete `verify_staged_digest()` (Req 4, Req 2)

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`redacted_view`, line ~636; delete the `verify_staged_digest` shim; `stage_registry`'s fast-path comment)
- Modify: `scripts/factory_core/providers/cli.py` (`_model_endpoints_show`, lines 241–261 only)
- Test: `tests/test_provider_model_endpoint.py` (after `test_redacted_view_reduces_secret_reference_to_name_and_presence`), `tests/test_provider_cli.py` (end of file)

- [ ] **Step 1: Write the `model_endpoint` tests.** Insert after
`test_redacted_view_reduces_secret_reference_to_name_and_presence`:

```python
def test_redacted_view_staged_matches_the_source_view(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL, CLOUD])
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    assert me.redacted_view_staged(reg) == ([], me.redacted_view(reg))


def test_redacted_view_staged_refuses_an_unverified_copy(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems, view = me.redacted_view_staged(reg)
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0] and view is None
```

- [ ] **Step 2: Write the CLI tests.** Append to `tests/test_provider_cli.py`. The helper is
self-contained and does not import from the other test module, matching this file's style:

```python
def test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry(tmp_path, monkeypatch, capsys):
    # Swap-and-swap-back (#593): the staged registry reads as its real bytes once, then as a
    # registry with an injected endpoint on every later read. show must print the first read.
    import io
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    staged = _staged_fixture(tmp_path, monkeypatch).resolve()
    registry = staged / "model-endpoints.yaml"
    tampered = registry.read_bytes().replace(b"- id: local-lan", b"- id: injected", 1)
    real_open, real_read_bytes = open, Path.read_bytes
    reads = []

    def _serve(path):
        if Path(path).resolve() != registry:
            return None
        reads.append(path)
        return real_read_bytes(registry) if len(reads) == 1 else tampered

    def _open(file, mode="r", *args, **kwargs):
        data = _serve(file) if isinstance(file, (str, Path)) else None
        if data is None:
            return real_open(file, mode, *args, **kwargs)
        return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode("utf-8"))

    def _read_bytes(self):
        data = _serve(self)
        return real_read_bytes(self) if data is None else data

    monkeypatch.setattr(model_endpoint, "open", _open, raising=False)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    cli_mod.main()
    ids = [e["id"] for e in json.loads(capsys.readouterr().out)["endpoints"]]
    assert ids == ["local-same-host", "local-lan", "local-remote", "cloud-anthropic"]
    assert len(reads) == 1


def test_model_endpoints_show_staged_copy_without_registry_file_exits_1(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    staged = _staged_fixture(tmp_path, monkeypatch)
    (staged / "model-endpoints.yaml").unlink()
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "model endpoint registry not found" in capsys.readouterr().err
```

- [ ] **Step 3: Run them. Expect failures.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v -k "redacted_view_staged or first_read_of_the_staged or staged_copy_without_registry_file"
```

Expected:
- `test_redacted_view_staged_*` ×2: `AttributeError: ... has no attribute 'redacted_view_staged'`.
- `test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry`: `SystemExit: 1`.
  stderr shows `ERROR: staged model registry at ... does not match FACTORY_MODEL_REGISTRY_SHA256`,
  because today's second `verify_staged_digest()` re-read the swapped bytes. This is the
  red-on-`main` proof for Req 4.
- `test_model_endpoints_show_staged_copy_without_registry_file_exits_1`: **PASSES**. Today
  `load_registry` raises the same "not found" problem. The test pins that behaviour through
  the rewrite.

(As in Task 4, the branch state after Tasks 1–4 is equivalent to `main` for the `show`
test. It also fails the same way against unmodified `main`.)

- [ ] **Step 4: Implement `redacted_view_staged()` and `_redact()`.** Replace `redacted_view()` in
`model_endpoint.py` with:

```python
def redacted_view(registry_path) -> dict:
    """Effective configuration with each secret_reference reduced to its name and presence.

    Path-based, for the scheduler's read-only source mount (--from-source); the staged copy
    goes through redacted_view_staged().
    """
    path = Path(registry_path) / REGISTRY_FILE
    return _redact(load_registry(path), path)


def redacted_view_staged(registry_path) -> tuple[list[str], dict | None]:
    """redacted_view() of the staged copy, parsed from the bytes read_verified_tree() hashed.

    Returns (problems, view); view is None whenever problems is non-empty.
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    return [], _redact(endpoints, path)


def _redact(endpoints, path) -> dict:
    """The one place that decides what of a registry is safe to print."""
    views = []
    for entry in endpoints:
        view = {k: v for k, v in entry.items() if k != "secret_reference"}
        ref = entry["secret_reference"]
        view["secret_reference"] = {"name": ref, "present": bool(os.environ.get(ref))}
        if "base_url" in entry:
            view["base_url_scope"] = base_url_scope(entry["base_url"])
        views.append(view)
    return {"registry": str(path), "endpoints": views}
```

`_redact` takes `path` as well as `endpoints` so the output keeps its existing `"registry"`
key. The spec sketches `_redact(endpoints)`, and this extra argument is the only difference.

- [ ] **Step 5: Implement `_model_endpoints_show()`.** In `providers/cli.py`, replace the whole function:

```python
def _model_endpoints_show(args):
    # Redacted effective-configuration view (#557): secret_reference prints as its name and
    # presence only. The staged copy is trusted only with this dispatch's digest and is parsed
    # from the bytes that digest covered (#593); --from-source reads the scheduler's read-only
    # mount instead.
    from factory_core.providers import model_endpoint
    if args.from_source:
        try:
            problems, view = [], model_endpoint.redacted_view(model_endpoint.SOURCE_REGISTRY_DIR)
        except model_endpoint.RegistryError as e:
            problems, view = e.problems, None
    else:
        problems, view = model_endpoint.redacted_view_staged(model_endpoint.staged_registry_dir())
    if problems:
        _fail(problems)
    print(json.dumps(view, indent=2, default=str))
```

`--from-source` behaves exactly as today: no digest, and a `RegistryError` exits 1 with its
problems. The second "unchanged while read?" verify is removed.

- [ ] **Step 6: Delete the `verify_staged_digest()` shim** (the 3-line function added in Task 3).
Then update `stage_registry()`'s fast-path comment so it names the surviving reader:

```python
                # Nothing changed: skip the swap so a concurrent dispatch's
                # read_verified_tree() never sees a missing path or a mid-walk
                # FileNotFoundError while this call replaces an identical tree.
```

- [ ] **Step 7: Confirm no staged-path reader re-reads, then run both files. Expect all green.**

```bash
grep -rn "verify_staged_digest\|hosting_location_of\|_read_yaml" --include="*.py" scripts/ tests/   # expect no output
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v
```

Expected: `168 passed` + `34 passed`.

- [ ] **Step 8: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py scripts/factory_core/providers/cli.py tests/test_provider_model_endpoint.py tests/test_provider_cli.py
git commit -m "fix(model): model-endpoints show parses the staged bytes it verified; drop verify_staged_digest (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 6: Agent-controlled-env comment on `staged_registry_dir()` (Req 6) + final verification

The `FACTORY_MODEL_REGISTRY_SHA256` half of Req 6 landed with `read_verified_tree()` in
Task 3. This task adds the `FACTORY_MODEL_REGISTRY_PATH` half. It is a comment only, with
no behaviour change and no test (spec: "Requirement 6 is comment-only and has no test").

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`staged_registry_dir`)

- [ ] **Step 1: Add the comment.** Replace `staged_registry_dir()` with:

```python
def staged_registry_dir() -> Path:
    # FACTORY_MODEL_REGISTRY_PATH is a test-only override, never forwarded to run containers.
    # Registry verification must never run from agent-controlled env (#593): today only the
    # pre-agent entrypoint.sh preflight reads this, and #558 must keep it that way.
    return Path(os.environ.get("FACTORY_MODEL_REGISTRY_PATH") or _DEFAULT_STAGED_DIR)
```

- [ ] **Step 2: Full verification.**

  Doc citations (operator amendment after plan gate, 2026-11-24): no doc cites lines in
  `model_endpoint.py` or `providers/cli.py`, and the cited `providers/__init__.py` lines
  (15, 16, 24, 32, 66) are above the change, so there is no re-map step. Run
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  and expect a pass.

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py tests/test_provider_env_passthrough.py tests/test_adapter_authoring_guide.py -v
bash tests/test_provider_env_flags.sh
PYTHONPATH=scripts python -m pytest tests/ -q
grep -n "FACTORY_MODEL_REGISTRY_SHA256\"\|FACTORY_MODEL_REGISTRY_PATH\"" scripts/factory_core/providers/model_endpoint.py
```

Expected:
- The first command passes, with `168` + `34` from the two primary files.
- `test_provider_env_flags.sh` exits 0. It fakes `providers/cli.py`, so it is unaffected.
  If ambient `JIRA_*` variables are set in the container, it also fails on unmodified `main`
  with `FAIL: nothing set should emit no flags, got: -e JIRA_PROJECT_KEY ...`. Re-run it as
  `env -u JIRA_PROJECT_KEY -u JIRA_EPIC_LINK_FIELD bash tests/test_provider_env_flags.sh`.
- The full suite shows no new failures relative to `main`.
- The `grep` prints exactly two `os.environ.get(...)` read sites: one in `staged_registry_dir()`
  and one in `read_verified_tree()`. Each sits directly under its #593 comment.

- [ ] **Step 3: Scope check.** Use the two-dot form (memory, codebase-patterns.md [PATTERN]).
The three-dot form would list files `main` merged after this branch forked:

```bash
git fetch origin main
git diff --stat origin/main HEAD
```

Expected: only `scripts/factory_core/providers/{model_endpoint,__init__,cli}.py`,
`tests/test_provider_{model_endpoint,cli}.py`, and the refine-phase docs and memory files
(`docs/superpowers/specs/…`, `docs/superpowers/plans/…`, `.archon/memory/…`). There must be
no `scheduler.sh`, `scripts/provider_env.sh`, `entrypoint.sh` or `deploy/**` changes.

- [ ] **Step 4: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py
git commit -m "fix(model): state the agent-controlled-env invariant on the staged registry path (#593)" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec traceability

| Spec requirement / table row | Task |
|---|---|
| R1: source-bound digest; mismatch → `RegistryError`, `dest` untouched, no temp left | 1 |
| R1: a normal stage returns `tree_sha256(source)` | 1 (`test_stage_skips_the_swap_when_unchanged` asserts `== me.tree_sha256(src)`; the existing `test_stage_copies_tree_and_returns_its_digest` also does) |
| R2: `_read_tree`, `tree_sha256` wrapper, bytes-based `_parse_registry`/`_parse_evidence` | 2 |
| R2: 1 MiB per-file cap fails closed | 2, 3 |
| R2: `read_verified_tree()` replaces `verify_staged_digest()`; four tests ported | 3 (shim), 5 (deleted) |
| R2: first-read-legit / later-reads-tampered test, red on `main`; each file read once | 4 |
| R2: `preflight()` drops `evidence_dir`; missing `model-endpoints.yaml` → existing "not found" problem | 4 |
| R3: `(problems, hosting)` from both functions; `hosting_location_of` and its test deleted; pre-read and misleading comment removed | 4 |
| R3: `test_undetermined_hosting_...` rewritten; `test_preflight_rechecks_digest_after_reading` deleted | 4 |
| R4: `show` read-once via `redacted_view_staged()`; shared `_redact`; second verify removed; `--from-source` unchanged; read-once test red on `main` | 5 |
| R5: skip-the-swap regression test (inode unchanged, no temp dir) | 1 |
| R6: agent-controlled-env comments at both env read sites | 3 (SHA256), 6 (PATH) |

## Self-review

- `**Issue:** #593` line present under the title.
- No placeholders. The single `# ... remainder of the existing body, unchanged ...` line in
  Task 2 Step 5 marks unchanged existing code and says explicitly not to copy it into the
  file. Every new or changed line has a code block.
- Names are consistent across tasks: `MAX_FILE_BYTES`, `_read_file`, `_parse_yaml`,
  `_parse_registry`, `_parse_evidence`, `_read_tree`, `read_verified_tree`, `_pick`,
  `_local_problems(entry, files, registry_path)`, `preflight(registry_path, endpoint_id)`,
  `scheduler_preflight`, `redacted_view_staged`, `_redact(endpoints, path)`,
  `_serve_first_read`, `_registry_bytes`.
- The whole sequence was prototyped on a scratch copy of `main` `848c6a4` before
  publishing. The final state passed `tests/test_provider_model_endpoint.py` and
  `tests/test_provider_cli.py`. Against unmodified `main`, the new read-once and
  mid-staging tests failed for the reasons quoted in Task 4 Step 6 and Task 5 Step 3.

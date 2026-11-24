# Model Registry: Bind the Staged Digest to the Source, Read Each File Once

**Issue:** #593

**Revised:** 2026-11-24 (operator amendment after spec gate)

## Overview / Problem Statement

#593 is a follow-up from an independent review of the #557 branch (reviewed at
`4611ca9`; merged as PR #595, `848c6a4`). #557 built the model-endpoint registry loader, the scheduler→run-container
digest handoff, and endpoint preflight (`docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md`).
Neither defect below has an observable effect yet, because #557 only *validates*
configuration — nothing dispatches to a local endpoint today (contract §4.11 decision 3).
**Both defects must be fixed before #558** (the runtime adapter that will actually consume
the digest and dispatch to a selected endpoint) builds on this code, per the issue:
"Neither has an effect today because #557 only validates; both must land before #558
consumes the digest."

Both defects share one root cause: a value is computed from one read of a file or tree,
then a *different*, later read of the same file or tree is trusted for an actual decision.
Between the two reads, any run container (same uid 1000, same read-write shared volume,
`dark_factory_state` / `scheduler_state` at `/var/lib/dark-factory`) can swap the content —
and, in the second case, swap it back before any later recheck notices.

1. **The digest the scheduler forwards is computed over the staged copy, not the read-only
   source it's supposed to attest to** (`model_endpoint.py::stage_registry()`). A run
   container can edit the temp staging directory between `copytree` and the digest
   computation; the scheduler then forwards — and blesses — a digest over the tampered
   tree, never the untouched source.
2. **Verify-then-read is open to a swap-and-swap-back.** Three call sites hash a tree (or
   check a previously-computed hash) and then separately re-open and re-parse individual
   files from disk. A write-then-restore between those two reads is invisible to any digest
   check that runs before or after it, because the check only inspects the tree's state *at
   the moment it runs*, not the bytes a parse in between actually used.

Two smaller items surfaced by the same review are addressed here too (see Requirements 5
and 6): one is a live bug already fixed on `main` before this issue was filed, needing only
a regression test; the other is a documentation-only ask.

**Depends on:** #557 (merged, closed — satisfied).

**Scope (from the issue):** `scripts/factory_core/providers/model_endpoint.py`,
`providers/__init__.py` (a blast-radius hotspot — expect a hold and an owner signature),
`providers/cli.py`, and their tests. No change to `deploy/**`, gates, breaker, or budgets —
consistent with CLAUDE.md's hard limits. No change to `scheduler.sh` or `provider_env.sh`:
both already propagate a `RegistryError`/non-zero exit from `providers/cli.py
model-endpoints stage` into "do not dispatch" (`provider_env.sh::model_registry_env_flags`
returns 1 when the staging subprocess exits non-zero or prints no digest; `scheduler.sh`'s
`dispatch()` must not proceed past that), so Requirement 1 below needs no change outside
the listed scope to take effect.

## Requirements (from Q&A)

Full brainstorming dialogue is reproduced in the Phase 6 issue comment. Load-bearing
resolutions:

1. **`stage_registry()` must bind the returned digest to the read-only source, not the
   staged copy.** Keep computing `source_digest = tree_sha256(source)` before anything is
   copied (this already rejects symlinks and detects a source-side problem before any copy
   starts). After `copytree`, compute the temp copy's digest and compare it to
   `source_digest`; if they differ, raise `RegistryError` — never proceed to swap `tmp` into
   `dest`, never return the temp digest. On success, return `source_digest` itself, never a
   digest computed over `tmp`. Add a test that injects a write into the temp directory
   between `copytree` and the digest check and asserts staging fails (`RegistryError`) and
   `dest` is left untouched (same behavior preflight already expects of "the copy goes to a
   temp directory... and is swapped in by rename, so a failed copy never leaves a
   half-written registry").

2. **Every content-affecting decision in `model_endpoint.preflight()` must come from bytes
   read exactly once, hashed and parsed from the same read — no second disk read of the
   same logical content.** This replaces the current pattern (hash the tree, then separately
   re-open `model-endpoints.yaml` via `load_registry()`, then separately re-open the
   selected endpoint's evidence file via `load_evidence()`) with a single tree walk that
   captures each file's bytes once, hashes them for the tree digest, and hands the same
   bytes to the YAML parser. This closes the swap-and-swap-back window structurally (there
   is no second read left to exploit), which is a stronger guarantee than the current
   end-of-preflight digest recheck (kept in #557 to catch broad tampering, but
   insufficient against a swap-then-restore around one specific read — see Alternatives).

   There is exactly one staged-tree reader: `read_verified_tree(registry_path) ->
   (problems, files)`. It checks `FACTORY_MODEL_REGISTRY_SHA256` (present, 64-hex), reads
   the tree once, compares the digest and returns the bytes it hashed. It replaces
   `verify_staged_digest()`, which is deleted: no public hash-only verifier remains for a
   later reader (#558) to pair with a separate read. `preflight()` loses its
   `evidence_dir` parameter — the evidence record is the `capability-evidence/<id>.yaml`
   key of `files`, so evidence can never come from outside the digest-covered tree. A
   verified tree with no `model-endpoints.yaml` key returns the existing "model endpoint
   registry not found" problem, never a `KeyError`. The tree read fails closed
   (`RegistryError`) if any file exceeds 1 MiB, since the staged tree is writable by run
   containers and is read into memory before the digest is compared.

   Test (must fail on `main`): serve the legitimate bytes on the first read of each
   staged file and tampered bytes on any later read; `preflight()`'s problems and hosting
   must reflect only the first read, and each file is read once.

3. **`preflight()` and `scheduler_preflight()` both change their return shape to
   `(problems: list[str], hosting: str | None)`**, with `hosting` taken from the same
   parsed entry the problems were computed from (confirmed with the product owner — see
   issue comment). `providers/__init__.py::_endpoint_preflight()` drops its own pre-read
   (`hosting = model_endpoint.hosting_location_of(...)`, today's line 88) on **both**
   branches and instead takes `hosting` from whichever of `preflight()` /
   `scheduler_preflight()` it already calls. This is deliberately symmetric across the
   scheduler and run-container branches, even though the scheduler side (reading
   `SOURCE_REGISTRY_DIR`, a mount no run container can write to) has no TOCTOU exposure to
   fix — a single shared code path through a hotspot file is the most surgical, easiest-to-
   review change, and it keeps the "read hosting separately, then preflight" shape from
   being copied into #558's adapter. `hosting_location_of()` is deleted (its only caller is
   `_endpoint_preflight()`); its dedicated test is deleted with it. The misleading comment
   at today's line 87 ("Read before preflight(), so its closing digest check also covers
   this read") is removed along with the code it described.

   Existing tests that call `model_endpoint.preflight(...)` / `scheduler_preflight(...)`
   directly and assert a plain-list return are updated to unpack the tuple (confirmed with
   the product owner: no backward-compatible plain-list wrapper — the only non-test caller
   is `_endpoint_preflight`, and a wrapper would just reintroduce an unused second code
   path in a hotspot file).

   Two existing tests in `tests/test_provider_model_endpoint.py` stop working and are
   handled here. `test_preflight_rechecks_digest_after_reading` patches `_select`, which
   `preflight()` no longer calls: it is replaced by Requirement 2's read-once test.
   `test_undetermined_hosting_never_skips_the_cloud_credential_check` patches the deleted
   `hosting_location_of`: it is rewritten to patch `scheduler_preflight` to return
   `([], None)`, keeping its assertion that the cloud credential problem is reported.

4. **`providers/cli.py::_model_endpoints_show()` gets the same read-once treatment for the
   staged path.** Today it calls `verify_staged_digest()`, then separately
   `redacted_view()` (which internally calls `load_registry()`, a second disk read), then
   `verify_staged_digest()` again ("# unchanged while read?") — the same swap-and-swap-back
   shape as Requirement 2, plus a redundant third read. This becomes one read-once-and-parse
   call; `redacted_view()` is split so the redaction logic operates on an already-parsed
   entry list, with a thin path-based wrapper kept only for `--from-source` (which reads
   `SOURCE_REGISTRY_DIR` directly — no digest to bind to, same posture as
   `scheduler_preflight()`, unchanged by this ticket). The second, redundant
   `verify_staged_digest()` call after the read is removed — there is nothing left for it to
   usefully recheck once the parse comes from the same bytes the first (and now only) digest
   check covered. The staged path goes through `read_verified_tree()` (Requirement 2).
   Requirement 2's read-once test is repeated for `model-endpoints show`: its output must
   reflect only the first read.

5. **Add the regression test the already-shipped "skip the swap when unchanged" fix is
   missing.** `stage_registry()` already returns early without touching `dest` when
   `tree_sha256(dest) == source_digest` (shipped in `bd6be81`, before this issue was filed) —
   this is not a new behavior change, but no test exercises that path today (existing tests
   cover the swap-happens case only). Confirmed with the product owner: add a test that
   stages twice with an unchanged source and asserts `dest`'s inode is unchanged
   (`os.stat(dest).st_ino` — sturdier than mtime) and that no `.model-registry.tmp-*`
   directory is left behind, without conflicting with Requirement 1's new injected-write
   test (that one drives the swap path; this one drives the early-return path).

6. **State the agent-controlled-env invariant in code, as a comment, not a new check.**
   `model_endpoint.staged_registry_dir()` (reads `FACTORY_MODEL_REGISTRY_PATH`) and
   `read_verified_tree()` (reads `FACTORY_MODEL_REGISTRY_SHA256`) each get a short comment
   stating that registry verification must never run under agent-controlled environment —
   today only the pre-agent `entrypoint.sh` preflight call reads these — and that #558 must
   preserve that. Confirmed with the product owner: this is in-scope for #593 as a
   comment-only addition (the issue explicitly asks for it to be "stated in the code," and
   both functions are already inside this ticket's scope file), not a behavior change, and
   not a new enforcement mechanism. The same point is carried forward as a non-blocking item
   below for #558's own acceptance criteria.

## Architecture / Approach

### Requirement 1 — `stage_registry()` binds to the source

```python
def stage_registry(source=None, dest=None) -> str:
    source = Path(source) if source else SOURCE_REGISTRY_DIR
    dest = Path(dest) if dest else staged_registry_dir()
    load_registry(source / REGISTRY_FILE)       # unchanged: never stage an invalid registry
    source_digest = tree_sha256(source)          # unchanged position; rejects symlinks first
    if dest.exists():
        try:
            if tree_sha256(dest) == source_digest:
                return source_digest             # unchanged: skip-the-swap fast path
        except (RegistryError, OSError):
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    old = dest.with_name(f".{dest.name}.old-{uuid.uuid4().hex}")
    try:
        os.chmod(tmp, 0o755)
        shutil.copytree(source, tmp, dirs_exist_ok=True)
        tmp_digest = tree_sha256(tmp)
        if tmp_digest != source_digest:
            raise RegistryError([
                f"{tmp}: staged copy digest does not match the read-only source digest "
                f"({tmp_digest} != {source_digest}) — the source or the staged copy "
                "changed mid-staging; refusing to publish a digest that does not bind the "
                "read-only source"])
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
    return source_digest                         # the fix: bind to source, never to tmp
```

The existing `except BaseException: shutil.rmtree(tmp, ...); raise` cleanup already handles
a raised `RegistryError` correctly: `tmp` is removed, `dest` is never touched (the mismatch
check runs before either `os.replace` call), and the exception propagates to
`providers/cli.py::_model_endpoints_stage()`, which already catches `RegistryError` and
exits non-zero via `_fail()`. `provider_env.sh::model_registry_env_flags()` already treats a
non-zero `model-endpoints stage` exit (or an empty digest) as "return 1 — do not dispatch."
No change is needed in either of those files or in `scheduler.sh`.

### Requirements 2–4 — read once, hash and parse the same bytes

A new internal helper replaces the "hash the tree, then separately re-open specific files"
pattern everywhere it occurs:

```python
def _read_tree(root) -> tuple[str, dict[str, bytes]]:
    """Walk root once (same ordering/symlink-rejection as tree_sha256): returns the tree's
    SHA-256 digest and a {relative_posix_path: bytes} map of every file in it. Raises
    RegistryError if any file exceeds 1 MiB (checked before the file is read)."""
```

This has the same walk, sort, and symlink-rejection behavior `tree_sha256` has today (kept
as a thin wrapper: `tree_sha256(root) = _read_tree(root)[0]`, so every existing caller and
test of `tree_sha256` is unaffected). The registry and capability-evidence files are tiny
YAML (`model-endpoints.yaml` plus one small file per endpoint); holding all of their bytes
in memory for the duration of one preflight call is negligible.

`load_registry()` and `load_evidence()` are each split into a path-based wrapper (unchanged
public signature, used by `scheduler_preflight()`, `--from-source`, and all existing
path-based tests) and a bytes-based core the new read-once call sites use directly:

```python
def _parse_registry(data: bytes, where) -> list[dict]:   # the existing load_registry body,
                                                            # taking bytes instead of a path
def load_registry(path) -> list[dict]:                    # unchanged signature
    return _parse_registry(Path(path).read_bytes(), path)  # (plus the existing not-found check)

def _parse_evidence(data: bytes, where) -> dict:          # likewise for load_evidence
def load_evidence(evidence_dir, endpoint_id) -> dict | None:
    ...                                                    # unchanged signature and behavior
```

`model_endpoint.preflight()` becomes:

```python
def read_verified_tree(registry_path) -> tuple[list[str], dict[str, bytes] | None]:
    """The one staged-tree reader. files is None whenever problems is non-empty."""
    expected = os.environ.get("FACTORY_MODEL_REGISTRY_SHA256", "")
    # ... verify_staged_digest()'s presence/shape/missing-dir checks, same messages ...
    try:
        actual, files = _read_tree(registry_path)
    except RegistryError as e:
        return e.problems, None
    except OSError:
        return [...], None                                 # unchanged "unreadable" message
    if not hmac.compare_digest(actual, expected):
        return [...], None                                 # unchanged mismatch message
    return [], files


def preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {registry_path / REGISTRY_FILE}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], registry_path / REGISTRY_FILE)
    except RegistryError as e:
        return e.problems, None
    entry = next((e for e in endpoints if e["id"] == endpoint_id), None)
    if entry is None:
        return [f"Unknown FACTORY_MODEL_ENDPOINT '{endpoint_id}' ..."], None
    hosting = entry["hosting_location"]
    # ... secret-reference presence check, unchanged ...
    if hosting == "local":
        problems += _local_problems(entry, files)           # reads the evidence file's bytes
                                                              # out of `files`, not off disk
    return problems, hosting
```

No end-of-function digest recheck remains: the tree was hashed and every byte used for a
decision came from that one read, so there is no later read left for a swap to target. This
is strictly stronger than today's "recheck after reading" mitigation, which only detects
tampering present *at the moment of the recheck* — not a swap that was already restored by
then (exactly the gap `test_preflight_rechecks_digest_after_reading` does not cover, since
it tampers and leaves the tamper in place rather than restoring it).

`_local_problems(entry, files)` is adjusted to take the already-read `files` map (keyed by
the evidence file's relative path, e.g. `capability-evidence/<id>.yaml`) instead of calling
`load_evidence(evidence_dir, eid)` itself; it still raises/returns the same problem messages
on a missing or invalid evidence file (a missing key in `files` is "no evidence record",
same as `load_evidence` returning `None` today).

`scheduler_preflight()` has no digest to verify (it reads the scheduler's own read-only
source mount) and already has the selected `entry` in hand via `_select()`; it changes only
to also return `entry["hosting_location"] if entry else None` alongside its existing
problems list — no new read, no behavior change to the problems it reports:

```python
def scheduler_preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    entry, problems = _select(registry_path, endpoint_id)
    hosting = entry["hosting_location"] if entry else None
    if entry is not None and hosting == "local":
        problems.append(_NO_LOCAL_DISPATCH.format(eid=entry["id"]))
    return problems, hosting
```

`providers/__init__.py::_endpoint_preflight()` drops the pre-read entirely:

```python
def _endpoint_preflight(side: str, endpoint_id: str, model_name: str) -> list[str]:
    from factory_core.providers import model_endpoint
    if side == "scheduler":
        problems, hosting = model_endpoint.scheduler_preflight(
            model_endpoint.SOURCE_REGISTRY_DIR, endpoint_id)
    else:
        problems, hosting = model_endpoint.preflight(
            model_endpoint.staged_registry_dir(), endpoint_id)
    if hosting == "cloud" or (hosting is None and not problems):
        problems += model.preflight(model_name)
    return problems
```

`_endpoint_preflight()`'s own return type (a plain `list[str]`) is unchanged — it's
`providers.preflight()`'s public contract and has callers/tests outside this ticket's scope
(`entrypoint.sh`'s preflight call, `scheduler.sh`'s boot check) that this ticket does not
touch.

`providers/cli.py::_model_endpoints_show()`:

```python
def _model_endpoints_show(args):
    from factory_core.providers import model_endpoint
    if args.from_source:
        problems, view = [], model_endpoint.redacted_view(model_endpoint.SOURCE_REGISTRY_DIR)
    else:
        registry_dir = model_endpoint.staged_registry_dir()
        problems, view = model_endpoint.redacted_view_staged(registry_dir)
    if problems:
        _fail(problems)
    print(json.dumps(view, indent=2, default=str))
```

`redacted_view(registry_path)` keeps its existing path-based signature and behavior for
`--from-source` (unchanged: source isn't digest-gated, same posture as
`scheduler_preflight()`). A new `redacted_view_staged(registry_path)` calls
`read_verified_tree()` once, parses `model-endpoints.yaml` from the returned bytes (a
missing key is the same "registry not found" problem as in `preflight()`), builds the
same redacted shape, and returns
`(problems, view)` — `view` is `None` when `problems` is non-empty. The redaction logic
itself (stripping `secret_reference` down to `{"name", "present"}`, `base_url_scope`) is
factored into a shared `_redact(endpoints) -> dict` both `redacted_view()` and
`redacted_view_staged()` call, so there is exactly one place that decides what's safe to
print.

### Requirement 5 — skip-the-swap regression test

```python
def test_stage_skips_the_swap_when_unchanged(tmp_path, monkeypatch):
    src = _write_registry(tmp_path / "src", [CLOUD])
    dest = tmp_path / "staged"
    first = me.stage_registry(src, dest)
    before_ino = os.stat(dest).st_ino
    second = me.stage_registry(src, dest)
    assert second == first
    assert os.stat(dest).st_ino == before_ino
    assert not list(dest.parent.glob(f".{dest.name}.tmp-*"))
```

### Requirement 6 — agent-controlled-env comments

```python
def staged_registry_dir() -> Path:
    # FACTORY_MODEL_REGISTRY_PATH is a test-only override, never forwarded to run containers.
    # Registry verification must never run from agent-controlled env (#593): today only the
    # pre-agent entrypoint.sh preflight call reads this. #558's runtime adapter must preserve
    # that — it must not add a reader that executes under an agent session's environment.
    return Path(os.environ.get("FACTORY_MODEL_REGISTRY_PATH") or _DEFAULT_STAGED_DIR)
```

(and the equivalent comment on `read_verified_tree()`'s `FACTORY_MODEL_REGISTRY_SHA256`
read — the only place that variable is read, per Requirement 2).

### Tests and where they run

All new and changed tests are pytest, in `tests/test_provider_model_endpoint.py` and
`tests/test_provider_cli.py`, and run in CI under `python -m pytest tests/`. No `.sh` test
and no `scheduler.sh`/`provider_env.sh` line changes, so nothing here relies on
`tests/test_scheduler.sh`, which is not listed in `.github/workflows/ci.yml`.

| Requirement | Test | On `main` today |
|---|---|---|
| 1 | a write injected into the temp directory between `copytree` and the digest check makes staging raise `RegistryError`; `dest` is untouched and no temp directory remains | fails (staging succeeds) |
| 1 | a normal stage returns `tree_sha256(source)` | passes |
| 2 | first read legitimate, any later read tampered: `preflight()` problems and hosting reflect the first read only; each staged file is read once | fails |
| 2 | a verified tree with no `model-endpoints.yaml` yields the "registry not found" problem; a file over 1 MiB fails closed | new |
| 2 | `read_verified_tree()`: digest unset, malformed digest, missing directory, tampered tree — the four `test_verify_staged_digest_*` tests, ported | ported |
| 3 | `preflight()`/`scheduler_preflight()` return `(problems, hosting)`; existing direct callers (`_run_preflight`, which drops its `evidence_dir` argument) unpack and assert hosting | updated |
| 3 | `test_undetermined_hosting_never_skips_the_cloud_credential_check`, rewritten to patch `scheduler_preflight` | rewritten |
| 3 | `test_hosting_location_of` and `test_preflight_rechecks_digest_after_reading` | deleted (replaced by the Requirement 2 test) |
| 4 | the Requirement 2 read-once test against `model-endpoints show` (staged path); `--from-source` unchanged | fails |
| 5 | staging twice with an unchanged source keeps `dest`'s inode and leaves no temp directory | passes (pins `bd6be81`) |

Requirement 6 is comment-only and has no test.

## Alternatives Considered

1. **Keep the end-of-`preflight()` digest recheck instead of eliminating the second read.**
   Rejected — this is today's posture and is exactly what the issue reports as insufficient:
   a recheck only catches tampering still present when it runs; a swap that is restored
   before the recheck fires is invisible to it. Reading once and parsing the same bytes
   removes the second read (and therefore the window) entirely, rather than trying to shrink
   it.
2. **Keep `hosting_location_of()` as a separate function, called only internally by the new
   `preflight()`/`scheduler_preflight()` instead of by `_endpoint_preflight()`.** Rejected —
   it would still exist as a standalone path-based reader with no caller outside the two
   functions that now compute hosting directly from their own already-read entry; keeping it
   would be dead indirection in a file the issue itself flags as a blast-radius hotspot.
3. **Load the entire staged tree's bytes into a module-level cache keyed by digest, shared
   across `preflight()` and `redacted_view_staged()` calls within one process.** Rejected —
   unnecessary: each CLI invocation and each `providers.preflight()` call is a fresh process
   (`entrypoint.sh`/`scheduler.sh` each shell out once), so there is no in-process reuse
   opportunity to exploit, and a cache would be one more place a stale read could leak across
   calls.
4. **Have `stage_registry()` retry the copy once on a digest mismatch instead of failing
   immediately.** Rejected — the issue's fix directive is "fail staging"; a retry risks
   looping against an actively-tampering container and adds complexity with no stated
   requirement for it. Fail-closed (no dispatch) matches `model_registry_env_flags()`'s
   existing "return 1 — the caller must not dispatch" contract on any staging failure.

## Open Questions (non-blocking)

- **#558's acceptance criteria** should explicitly carry forward the agent-controlled-env
  invariant (Requirement 6): any new reader of `FACTORY_MODEL_REGISTRY_PATH`,
  `FACTORY_MODEL_REGISTRY_SHA256`, or the staged tree that #558's runtime adapter adds must
  not execute under an agent session's environment. This is the operator's to add to #558,
  not #593's to enforce.
- **A per-subpath read-only mount** for the run container (raised as an open question in
  #557's own spec) would add defence in depth independent of the digest check; still
  unverified in this checkout and not depended on here.

## Assumptions (flagged)

- Current `main` (`848c6a4`, this branch's base) was read live at refinement time
  (2026-11-24); all line numbers cited above are from that state, not the reviewer's original
  numbering against `4611ca9` (the head the reviewer read; #557 merged as `848c6a4`),
  which has since shifted across
  `bd6be81`, `6d25cba`, `9c7cab7`, `919de51`, and `07f0662`.
- The "unnecessary staged-dir swap on every dispatch" item (Requirement 5) is confirmed
  already fixed on `main` by reading `stage_registry()`'s current body and by `git log`
  showing the skip-the-swap check predates this issue's filing; this spec adds only the
  missing regression test, not a behavior change.
- `providers/__init__.py::preflight()`'s own public return shape (`list[str]`) is assumed
  stable and out of scope — only its internal `_endpoint_preflight()` helper's handling of
  `hosting` changes.
- No real `deploy/instances/*/model-registry/` directory exists on any instance yet (per
  #557's spec), so none of these changes are exercised by a real dispatch today; all new and
  modified tests use `tests/fixtures/model_registry/`-style fixtures, matching #557's
  existing test fixtures and `_write_registry`/`_stage` test helpers in
  `tests/test_provider_model_endpoint.py`.

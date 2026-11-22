# Deconflict: skip the alembic multi-head check on backend-less targets, escalate on real failures

**Issue:** #576

**Revised:** 2026-11-22 (operator amendment after spec gate review)

## Overview / Problem statement

`scripts/factory_core/deconflict.py::resolve_merge_conflicts()` runs an unconditional
alembic multi-head check after Tier 1 resolution (lines 280-295):

```python
backend_dir = os.path.join(clone_dir, "backend")
r3 = subprocess.run(["python", "-m", "alembic", "heads"], cwd=backend_dir, ...)
```

`subprocess.run` raises `FileNotFoundError` when `cwd` doesn't exist. dark-factory's
own self-target clone has no `backend/` directory at all (this repo is the factory,
not a MarketHawk-shaped backend+frontend app), so every self-target deconflict run
that reaches this line crashes with an uncaught traceback — no `_escalate()` call, no
board update, no issue comment. The scheduler sees a dead container and redispatches
the same "Deconflict issue #N" command, which crashes identically, until the retry
ceiling trips the breaker.

Concretely, on 2026-11-22: #551/PR #571 went CONFLICTING after #575 merged. Deconflict
was dispatched three times (10:02Z, 10:05Z, 10:07Z), crashed the same way each time
~2 minutes in, and the breaker tripped `phase=resolve` at 10:09Z. Every conflict on
any self-target PR that reaches Tier-1-complete costs three wasted runs and a breaker
trip, with no diagnostic trail for an operator beyond the crash log.

This is a crash-safety fix in a non-gating step of one function. It is explicitly
**not** a gate, breaker, or `conflict_resolution:` config change (CLAUDE.md: "Never
weaken safety gates ... as a side effect of another change; gate changes get their
own reviewed ticket").

## Requirements (from Q&A)

1. **Skip, don't crash, when the target has no alembic setup.** Detection is a
   filesystem check, adapter-overridable: a new `deconflict.alembic_ini` adapter key
   (default `"backend/alembic.ini"`, mirroring the existing `deconflict.models_init`
   / `deconflict.migrations_dir` keys in `adapter_defaults.py` DEFAULTS["deconflict"]).
   If `os.path.join(clone_dir, alembic_ini_path)` is not a file, skip the multi-head
   check and continue the merge — no escalation, no comment, this is the
   expected path for backend-less targets. The skip is never invisible: print
   `[deconflict] alembic check skipped: <path> not found` to stdout (run log). One
   exception: if any conflicted file matches the migrations branch of `tier1`
   (`_deconflict_migrations_dir(clone_dir) in filepath and filepath.endswith(".py")`)
   while the configured alembic.ini is absent, the target evidently has migrations
   and the path is wrong (mistyped override, relocated ini) — escalate (same pattern
   as requirement 5) instead of skipping.
2. **No adapter.yaml change required for dark-factory's self-target.** The default
   path (`backend/alembic.ini`) doesn't exist in this repo's clone, so the skip fires
   with zero configuration. `.factory/adapter.yaml` gets no new `deconflict:` section
   as part of this fix. MarketHawk's `backend/alembic.ini` exists and its adapter sets
   no `deconflict.alembic_ini`, so its multi-head check keeps running. **Behaviour
   change on MarketHawk (intended):** today a non-zero `alembic heads` or
   `alembic merge heads` is ignored and the merge continues (`deconflict.py:282-293`);
   after this fix an alembic failure escalates to Blocked (requirement 5), and the
   generated merge revision is actually staged (requirement 4).
3. **A single path key covers both halves of "no backend/alembic.ini or adapter says
   so."** No separate `deconflict.alembic_enabled` boolean. A target with a real but
   relocated alembic.ini points the key at it; a target that wants to force-disable
   the check points it at a path that will never exist. Two independent knobs
   (a path and a boolean) could disagree with each other for no benefit.
4. **The check's working directory derives from the configured path, and the merge
   revision is staged where alembic actually wrote it.** `alembic heads` /
   `alembic merge heads` run with `cwd` = the configured alembic.ini's parent
   directory. The generated revision is staged by listing untracked `*.py` under
   that parent directory (`git ls-files --others --exclude-standard`), taken before
   and after `alembic merge heads`; the files new in the second listing are
   `git add`ed with `check=True`. Zero new files after a merge escalates
   (requirement 5). The staging path is **not** derived from
   `deconflict.migrations_dir`: that key is a substring pattern matched against
   conflicted paths (`deconflict.py:94`), not a path relative to the backend root.
   **This closes a pre-existing MarketHawk defect:** its `backend/alembic.ini` has
   `script_location = app/alembic`, so revisions land in
   `backend/app/alembic/versions/`, while today's literal
   `git add backend/alembic/versions/` (`deconflict.py:294`) matches nothing and
   fails unchecked — the merge revision stays untracked, is not in the merge commit,
   and the branch is pushed with two heads.
5. **An unexpected error in the check (alembic.ini present but the check itself
   fails) escalates, it does not crash.** Wrap the subprocess calls in this block
   (alembic heads, alembic merge, the untracked-file listings and the `git add`) in
   a `try`/`except Exception`; on failure, follow the same pattern as the function's
   two existing escalation sites (no resolvable conflict markers; AI-tier/hard-grep
   survivors): abort the in-progress merge (`git merge --abort`), call
   `_escalate(issue_num, owner, repo, reason)`, write the `escalate` artifact, and
   return 1. This surfaces alembic-not-installed, permission errors, or any other
   unanticipated subprocess failure as a BLOCKED issue with a comment instead of a
   silent container crash.
6. **Test with a fixture repo that has no `backend/`.** Coverage must include: (a)
   no-alembic-setup fixture (no `backend/` at all, and `backend/` present without
   alembic.ini) → check is skipped with the log line, merge proceeds normally past
   that point; (b) adapter-overridden `deconflict.alembic_ini` path → check runs
   against the overridden location; (c) alembic.ini present but the check
   raises/fails → routes through `_escalate()`, merge is aborted, function returns 1;
   (d) single head → no merge call; (e) multi-head → merge runs and the new revision
   file is staged, and no new file escalates; (f) alembic.ini absent with a
   conflicted migration file → escalates instead of skipping.
7. **Not a gate or breaker change.** No edits to `config/config.yaml`'s
   `conflict_resolution:` block, `scripts/factory_core/board.py`'s breaker logic, or
   dispatch-ceiling behavior.

## Architecture / Approach

**1. `scripts/factory_core/adapter_defaults.py` — new default key.**

```python
DEFAULTS = {
    ...
    "deconflict": {
        "models_init": "backend/app/models/__init__.py",
        "migrations_dir": "alembic/versions/",
        "alembic_ini": "backend/alembic.ini",
    },
    ...
}
```

**2. `scripts/factory_core/deconflict.py` — new adapter-read helper, same shape as
the two existing ones.**

```python
_DEFAULT_ALEMBIC_INI = _AD["deconflict"]["alembic_ini"]

def _deconflict_alembic_ini(clone_dir: str | None = None) -> str:
    """Return deconflict.alembic_ini from adapter at use-time, falling back to default."""
    try:
        from . import adapter
        val = adapter.get(clone_dir or ".", "deconflict.alembic_ini")
        if val is not None and isinstance(val, str):
            return val
    except Exception:
        pass
    return _DEFAULT_ALEMBIC_INI
```

**3. `resolve_merge_conflicts()` — guard and wrap the multi-head block.**

Replace the current unconditional block (lines 280-295) with:

```python
alembic_ini_rel = _deconflict_alembic_ini(clone_dir)
alembic_ini_path = os.path.join(clone_dir, alembic_ini_rel)
alembic_failure = None

if os.path.isfile(alembic_ini_path):
    backend_dir = os.path.dirname(alembic_ini_path)
    backend_rel = os.path.relpath(backend_dir, clone_dir)

    def _untracked_py() -> set:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", backend_rel],
            cwd=clone_dir, capture_output=True, text=True, check=True,
        ).stdout
        return {p for p in out.splitlines() if p.endswith(".py")}

    try:
        r3 = subprocess.run(
            ["python", "-m", "alembic", "heads"],
            cwd=backend_dir, capture_output=True, text=True, check=True,
        )
        heads = [ln for ln in r3.stdout.splitlines()
                 if ln and ln[:1] in "0123456789abcdef"]
        if len(heads) > 1:
            before = _untracked_py()
            subprocess.run(
                ["python", "-m", "alembic", "merge", "heads",
                 "-m", f"merge_branches_issue_{issue_num}"],
                cwd=backend_dir, capture_output=True, check=True,
            )
            generated = sorted(_untracked_py() - before)
            if not generated:
                raise RuntimeError(
                    "alembic merge heads produced no new revision file")
            subprocess.run(["git", "add", "--", *generated],
                           cwd=clone_dir, capture_output=True, check=True)
    except Exception as exc:
        alembic_failure = f"Alembic multi-head check failed unexpectedly: {exc}"
else:
    print(f"[deconflict] alembic check skipped: {alembic_ini_rel} not found",
          flush=True)
    migrations_rel = _deconflict_migrations_dir(clone_dir)
    migration_conflicts = [f for f in conflicted
                           if migrations_rel in f and f.endswith(".py")]
    if migration_conflicts:
        alembic_failure = (
            f"Conflicted migration files ({', '.join(migration_conflicts)}) but no "
            f"alembic.ini at {alembic_ini_rel}; check deconflict.alembic_ini.")

if alembic_failure:
    subprocess.run(["git", "merge", "--abort"], cwd=clone_dir, capture_output=True)
    _escalate(issue_num, owner, repo, alembic_failure)
    _write_artifact(artifact_path, "escalate", len(conflicted),
                    tier1_count, tier2_count, 1)
    return 1
```

The sketch is illustrative; the plan may restructure it, but the behaviours in
requirements 1, 4 and 5 are binding.

`check=True` on the two alembic subprocess calls turns a non-zero alembic exit (e.g.
alembic installed but misconfigured) into a `CalledProcessError`, caught by the same
`except Exception` — matching the requirement that *any* unexpected failure in this
block escalates rather than silently producing a bad merge state. The `git add` of
the merge revision is `check=True` too, unlike the best-effort `git add` calls
elsewhere in this file: an unstaged merge revision is exactly the two-heads-pushed
defect requirement 4 closes. The before/after listing keeps any pre-existing
untracked file out of the commit.

**4. Tests** (`tests/test_factory_core_deconflict.py`):
- `test_resolve_skips_alembic_check_when_no_backend` — `tmp_path` with no
  `backend/alembic.ini`; monkeypatch `subprocess.run` to assert it is never invoked
  with `"alembic"` in the command; assert `rc == 0` and the merge completes.
- `test_resolve_alembic_check_respects_adapter_override` — fixture with alembic.ini
  at a non-default path, adapter config pointing `deconflict.alembic_ini` at it;
  assert the check runs with that directory as `cwd`.
- `test_resolve_skips_alembic_check_when_backend_has_no_ini` — `backend/` exists
  without `alembic.ini`; same assertions as the no-backend test, plus the
  `alembic check skipped` line on stdout (`capsys`).
- `test_resolve_escalates_on_alembic_check_failure` — alembic.ini present; two
  cases (parametrized): the fake `subprocess.run` raises `OSError` for the
  `alembic heads` call, and raises `subprocess.CalledProcessError` for it (a fake
  does not honour `check=True`, so the test must raise it itself). Assert
  `_escalate` is called, `git merge --abort` is called, artifact verdict is
  `escalate`, and `rc == 1`.
- `test_resolve_alembic_single_head_does_not_merge` — one head line; assert no
  `alembic merge` and no `git add` of a revision; `rc == 0`.
- `test_resolve_alembic_multi_head_merges_and_stages_generated_revision` — two
  head lines; the second `git ls-files --others` listing returns one extra
  `backend/app/alembic/versions/<rev>.py` (MarketHawk's real layout); assert
  `alembic merge heads` ran with `cwd` = the ini's directory and `git add` received
  exactly that file; `rc == 0`.
- `test_resolve_escalates_when_alembic_merge_generates_no_file` — two heads, both
  listings identical; assert escalation, merge abort, `rc == 1`.
- `test_resolve_escalates_on_migration_conflict_without_alembic_ini` — no
  alembic.ini, conflicted list contains a path matching `migrations_dir`; assert
  escalation instead of skip, `rc == 1`.

`tests/test_adapter.py`, next to the existing `models_init` / `migrations_dir`
pairs (lines 1037-1068):
- `test_deconflict_alembic_ini_default_parity` — no adapter file →
  `_deconflict_alembic_ini` returns `DEFAULTS["deconflict"]["alembic_ini"]`.
- `test_deconflict_alembic_ini_adapter_override` — adapter override is returned.

**5. Docs** (`docs/configuration.md`): the adapter-keys row for
`deconflict.models_init`, `deconflict.migrations_dir` (line 554) gains
`deconflict.alembic_ini` and its `deconflict.py` line reference is refreshed; the
file is checked by `scripts/docs_refs_check.py` in CI.

Existing tests that monkeypatch `subprocess.run` wholesale
(`test_resolve_clean_merge_writes_artifact`, `test_resolve_escalation_on_no_conflicted_files`)
exit before reaching this block (clean merge / no-conflicted-files) and are
unaffected.

## Alternatives considered

1. **Catch `FileNotFoundError` around the existing unconditional call, no
   path-existence pre-check.** Rejected: `FileNotFoundError` on a missing `cwd` is
   indistinguishable, in a bare `except`, from "alembic really is missing" — treating
   dark-factory's own routine, expected backend-less state as an *escalation* would
   abort every single self-target merge that reaches this line and trip the board to
   BLOCKED on every normal run, which is worse than today, not better, and contradicts
   the issue's explicit ask for a silent skip.
2. **Boolean adapter flag (`deconflict.alembic_enabled`, default `true`) instead of a
   path check.** Rejected: requires every backend-less target (including
   dark-factory's own `.factory/adapter.yaml`, which has no `deconflict:` section
   today) to proactively opt out, so the bug would persist until someone remembered
   to add the flag. A path-existence default auto-detects with zero config changes.
3. **Both a path key and a boolean.** Rejected: redundant control surface — the two
   could be set inconsistently (path exists but boolean says disabled, or vice versa)
   with no behavioral benefit over the path check alone, which already expresses both
   "no alembic setup" (default path missing) and "adapter says so" (path overridden
   to somewhere that doesn't exist).

## Open questions (non-blocking)

- Whether `adapter_defaults.py`'s `DEFAULTS["deconflict"]` key should eventually be
  renamed to something like `backend_dir` with `alembic_ini`/`migrations_dir`/
  `models_init` all relative to it, rather than three independently-defaulted paths
  that happen to agree today. Out of scope here; this fix only adds the one key this
  issue needs.

## Assumptions (flagged)

- MarketHawk's `backend/alembic.ini` exists at the default path today (confirmed by
  the issue's own framing: "assumes MarketHawk layout", and checked at the spec
  gate: the file exists, `script_location = app/alembic`, no adapter override), so
  the check keeps running there. Observable behaviour does change for that target in
  the two ways stated in requirement 2: alembic failures escalate, and the merge
  revision is staged.
- `python -m alembic` exits non-zero (not just empty stdout) on a genuinely broken
  alembic setup, which is what makes `check=True` a reasonable way to route "ran but
  failed" through the same `except Exception` as "couldn't run at all." If a target's
  alembic wrapper swallows errors and exits 0, that failure mode is unchanged from
  today's behavior (pre-existing, out of scope for this fix).

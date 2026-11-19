# A guard test for shell tests missing from CI

**Issue:** #426

## Overview / Problem statement

`.github/workflows/ci.yml`'s `tests` job lists shell tests one `- run: bash tests/X.sh`
line at a time, with no glob. Adding a `tests/*.sh` file does not make it run in CI unless
someone remembers to add a matching line — and nothing today notices when they forget. As
of `f457c2c` (HEAD at refinement time), 23 of the repo's `tests/*.sh` files are not
referenced anywhere in `ci.yml` and therefore never execute on any PR:

```
test_159_regression.sh              test_memory_integration.sh
test_431_telemetry_isolation.sh     test_memory_write_gate.sh
test_close_preview_teardown.sh      test_off_board_detection.sh
test_codeindex_config.sh            test_preview_differentiator.sh
test_config_deletion.sh             test_restore_drill.sh
test_conformance_memory_write.sh    test_scheduler.sh
test_dispatch_ceiling.sh            test_scheduler_autopilot_guard.sh
test_entrypoint_fix_main.sh         test_scheduler_ceiling.sh
test_epic_autopilot_config.sh       test_scheduler_main_red_fixer.sh
test_failing_checks_for_pr.sh       test_scheduler_pagination.sh
test_has_new_comment_after_report.sh test_session_window_gate.sh
test_load_memory.sh
```

A shell test that never runs is indistinguishable, in every artifact the factory produces
(the PR diff, the conformance gate, a green CI run), from one that runs and passes. Three
recent tickets (#201, #395, #418) each independently tripped over an instance of this —
#395's plan had to discover and separately wire in `test_entrypoint_preflight.sh`; #418
deliberately left a new 175-line test unwired, correctly but uncomfortably citing this gap
as "existing project convention." This ticket closes the class, not another instance of it.

This ticket owns only the "tests not in CI" line item of umbrella issue #531; #531's other
items (unwired scripts, unreachable `BENCH_MODE=full`/`resolve` workflow paths, etc.) are
out of scope here.

## Requirements (from Q&A)

1. A new test, `tests/test_ci_covers_shell_tests.py`, fails when a `tests/*.sh` file is
   neither referenced in `.github/workflows/ci.yml` nor listed in an in-file exemption
   allowlist carrying a one-line reason.
2. The allowlist is a module-level Python dict in the test file itself — `{bare filename:
   reason}` — not a separate YAML/JSON file. This matches the repo's existing convention
   for this kind of guard (`tests/test_gate_diff_doc_exemption_regex.py`'s `EXEMPT_AREAS`,
   `tests/test_adapter.py`'s drift-tests), and keeps every exemption visible in the PR
   diff and reachable by the conformance/code-review gates, rather than in a side file
   those gates would have to be taught to look at.
3. "Referenced in `ci.yml`" is a whole-file regex match of `tests/[a-z_0-9]+\.sh` against
   `ci.yml`'s text — the same pattern the issue's own reproduce command uses — not
   restricted to `- run:` step lines specifically. `ci.yml` carries no comments today and
   every current mention of a `tests/*.sh` path is already a `- run: bash tests/...` line,
   so the narrower match would only protect against a case that doesn't exist yet, at the
   cost of the allowlist-seeding command and the test's own enforcement using two
   different patterns that could silently drift apart. Accepted limitation, stated
   explicitly: a future comment-only mention of a filename would also count as "covered."
4. The test additionally fails on any allowlisted filename that is now either referenced
   in `ci.yml` or no longer present under `tests/*.sh` — a stale exemption is the same
   "looks resolved but isn't" failure mode this ticket exists to catch, just in the other
   direction, and it is free to check once the dict and the glob both exist.
5. The 23 currently-unwired files (list above) are seeded into the allowlist at
   implementation time, each with a short reason. Per the issue's scope note, the seed
   list is re-derived from the reproduce command against the implementation branch's HEAD
   at that time, not copied verbatim from this spec or the issue body (file additions,
   deletions, or new CI wiring may land on `main` between refinement and implementation).
6. No change to `.github/workflows/ci.yml`. Because `ci.yml`'s `tests` job already runs
   `python -m pytest tests/ -v` (which auto-discovers every `.py` file under `tests/`),
   the new guard test starts running the moment it exists on disk — no new `- run:` line,
   no edit to the workflow file at all. This also means the fix needs no change to
   `gate_*`, breaker, budgets, `deploy/**`, or `publish.yml`, consistent with the issue's
   explicit exclusions.
7. Options 2 (glob all shell tests into CI directly) and 3 (triage and wire all 23 now) are
   not implemented by this ticket — see Alternatives.

## Architecture / Approach

Single new file, `tests/test_ci_covers_shell_tests.py`, following the house style used by
`tests/test_adapter.py`'s drift tests and `tests/test_push_gate_dag.py`'s DAG-node tests
(bare module-level `def test_*()`, no fixture, `_REPO_ROOT = Path(__file__).resolve().parents[1]`):

```python
_REPO_ROOT = Path(__file__).resolve().parents[1]

# tests/*.sh not yet wired into .github/workflows/ci.yml, each with a reason.
# Seeded 2026-11-19 (#426) from:
#   comm -23 \
#     <(git ls-tree HEAD tests/ --name-only | grep '\.sh$' | sed 's|tests/||' | sort) \
#     <(git show HEAD:.github/workflows/ci.yml | grep -oE 'tests/[a-z_0-9]+\.sh' | sed 's|tests/||' | sort -u)
# Re-derive this list from the command above at implementation time -- do not copy
# verbatim from an older spec or issue body.
CI_EXEMPT_SHELL_TESTS = {
    "test_scheduler.sh": "not yet triaged — #426 backlog",
    # ... one entry per currently-unwired file, each with a short reason
}

_SH_REF_PATTERN = re.compile(r"tests/[a-z_0-9]+\.sh")

def _ci_yml_text() -> str:
    return (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

def _referenced_in_ci(ci_text: str) -> set[str]:
    return {m.split("/", 1)[1] for m in _SH_REF_PATTERN.findall(ci_text)}

def test_every_shell_test_is_wired_or_exempt():
    sh_files = {p.name for p in (_REPO_ROOT / "tests").glob("*.sh")}
    referenced = _referenced_in_ci(_ci_yml_text())
    unaccounted = sh_files - referenced - CI_EXEMPT_SHELL_TESTS.keys()
    assert not unaccounted, (
        f"tests/*.sh not in ci.yml and not in CI_EXEMPT_SHELL_TESTS: {sorted(unaccounted)}"
    )

def test_no_stale_exemptions():
    sh_files = {p.name for p in (_REPO_ROOT / "tests").glob("*.sh")}
    referenced = _referenced_in_ci(_ci_yml_text())
    stale = {
        name for name in CI_EXEMPT_SHELL_TESTS
        if name in referenced or name not in sh_files
    }
    assert not stale, f"CI_EXEMPT_SHELL_TESTS entries now stale (wired or deleted): {sorted(stale)}"

def test_exemptions_have_reasons():
    empty = [name for name, reason in CI_EXEMPT_SHELL_TESTS.items() if not reason.strip()]
    assert not empty, f"CI_EXEMPT_SHELL_TESTS entries missing a reason: {empty}"
```

This is illustrative, not a literal diff to apply verbatim — the implement phase writes
the real file, seeds `CI_EXEMPT_SHELL_TESTS` from a fresh run of the reproduce command
against its own branch HEAD (not this spec's snapshot), and follows existing repo
formatting/import conventions (e.g. wherever `re` and `Path` are already imported
elsewhere in `tests/`).

No production code changes. No `ci.yml` changes. The new test file is picked up by the
existing `python -m pytest tests/ -v` step with zero additional CI wiring.

**Out of scope, flagged as a non-blocking follow-up, not an action item here:**
`docs/factory-target-boundary.md`'s "Known gaps" section (~line 309) carries a note that
"`tests/test_scheduler.sh` exists but is not in `.github/workflows/ci.yml`'s bash-test
list." Once this guard test ships, that line becomes either resolved (if
`test_scheduler.sh` is later wired) or redundant with the new allowlist (while it stays
exempt) — either way it's stale prose, but refine's scope boundary for this ticket is
`docs/superpowers/specs/` and `.archon/memory/` only, so updating that doc is left for the
implement phase or a separate ticket.

## Alternatives considered

1. **Glob all shell tests directly into `ci.yml`** (the issue's option 2): replace the
   per-file `- run:` lines with a loop over `tests/*.sh`. Rejected for this ticket — several
   of the 23 unwired files likely need fixtures or a live environment the `tests` job
   doesn't provide, and turning an invisible gap into an immediately red `main` is a worse
   outcome than the status quo. The issue explicitly does not recommend this option on its
   own.
2. **Triage and wire all 23 now** (the issue's option 3): the real long-term fix, but the
   issue explicitly recommends sequencing it after option 1, since the allowlist this
   ticket produces is what makes the backlog visible and tractable to work through
   file-by-file. Deferred as follow-up work; each wired-in test removes one entry from
   `CI_EXEMPT_SHELL_TESTS` and `test_no_stale_exemptions` will catch anyone who wires a
   test in but forgets to drop its now-stale exemption.
3. **A separate YAML/JSON allowlist file** instead of an in-test Python dict: rejected per
   Q&A — no existing precedent for this shape of exemption list in the repo, and it would
   put the allowlist a layer away from the PR diff / conformance gate visibility the
   in-test dict gets for free.

## Open questions (non-blocking)

- Whether any of the 23 currently-exempt tests should be prioritized for wiring-in as
  near-term follow-up tickets (vs. left in the allowlist indefinitely) is a product
  decision for whoever triages the backlog this allowlist now makes visible — not decided
  here.

## Assumptions

- The seed list in Requirement 5 / the Architecture section's example is illustrative of
  the current (refinement-time) state and must be re-derived from the reproduce command
  against the implementation branch's actual HEAD, per the issue's scope note — the 23
  files listed in this spec's Overview are not a committed, final allowlist.
- `ci.yml` has no comment lines mentioning a `tests/*.sh` path today (verified by
  inspection during refinement); if that ever changes, Requirement 3's accepted limitation
  (a comment-only mention counts as "covered") would need revisiting, but that is not the
  current state and is not addressed by this ticket.

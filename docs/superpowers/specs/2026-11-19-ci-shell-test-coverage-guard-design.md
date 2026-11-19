# A guard test for shell tests missing from CI

**Issue:** #426

**Revised:** 2026-11-19 (operator amendment after spec-gate review: YAML-aware "referenced" check, synthetic failing-path tests, per-file run-derived reasons, doc follow-through)

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
deliberately left a new test (196 lines on `main` today) unwired, correctly but uncomfortably citing this gap
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
3. "Referenced in `ci.yml`" means the path matches `tests/[a-z_0-9]+\.sh` inside the
   `run:` string of a step under `jobs.*.steps`, obtained with `yaml.safe_load` (pyyaml is
   installed at `ci.yml:12`). A step carrying an `if:` key whose value is the literal
   `false` does not count. YAML comments therefore never count — in particular a test
   disabled as `# - run: bash tests/test_x.sh` is *not* covered and must be either
   re-enabled or moved to the allowlist with a reason. The allowlist-seeding command stays
   the issue's grep; the two agree on `main` today because `ci.yml` has no comments.
   Residual limitation, stated explicitly: a shell comment *inside* a multi-line `run:`
   block would still count; no such block exists today.
4. The test additionally fails on any allowlisted filename that is now either referenced
   in `ci.yml` or no longer present under `tests/*.sh` — a stale exemption is the same
   "looks resolved but isn't" failure mode this ticket exists to catch, just in the other
   direction, and it is free to check once the dict and the glob both exist.
5. The 23 currently-unwired files (list above) are seeded into the allowlist at
   implementation time, each with a short reason. Per the issue's scope note, the seed
   list is re-derived from the reproduce command against the implementation branch's HEAD
   at that time, not copied verbatim from this spec or the issue body (file additions,
   deletions, or new CI wiring may land on `main` between refinement and implementation).
   The implement phase runs each seeded file once, in the run container, and writes the
   reason as either `passes standalone — wire in follow-up` or
   `fails: <first failing assertion or error>` (a timeout is `fails: timeout after 120s`).
   A blanket reason such as "not yet triaged" is not acceptable: the issue asks for the
   current state to be "recorded rather than silently blessed". A refinement-time sample
   (factory image, offline) found `test_dispatch_ceiling.sh`,
   `test_session_window_gate.sh` and `test_scheduler.sh` failing on `main` and
   `test_memory_write_gate.sh` and `test_off_board_detection.sh` passing.
   **Run them with credentials and scheduler state stripped** — never with the run's live
   token:
   `env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$(mktemp -d)" SCHEDULER_STATE_DIR="$(mktemp -d)" timeout 120 bash tests/X.sh`.
   At least one seeded test (`test_dispatch_ceiling.sh`, which does
   `export GH_TOKEN="${GH_TOKEN:-stub-token}"` at line 23) reaches the real `gh` binary and
   attempts label operations on issues #70-#73; with a live token those would be real
   writes.
   Fixing or wiring the failing tests is **out of scope for #426**: the per-file reasons
   are the hand-off to the follow-up work (issue option 3), nothing more.
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
# verbatim from an older spec or issue body. Reason format: Requirement 5.
CI_EXEMPT_SHELL_TESTS = {
    "test_memory_write_gate.sh": "passes standalone — wire in follow-up",
    "test_scheduler.sh": "fails: line 411: STATUS_REFINED: unbound variable",
    # ... one entry per currently-unwired file, reason taken from an actual run
}

_SH_REF_PATTERN = re.compile(r"tests/[a-z_0-9]+\.sh")

def _referenced_in_ci(ci_text: str) -> set[str]:
    """Shell tests named in the run: string of a live step. YAML comments never count."""
    refs = set()
    for job in ((yaml.safe_load(ci_text) or {}).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if str(step.get("if", "")).strip().lower() == "false":
                continue
            for m in _SH_REF_PATTERN.findall(str(step.get("run") or "")):
                refs.add(m.split("/", 1)[1])
    return refs

def _violations(sh_files: set, referenced: set, exempt) -> tuple[set, set]:
    """Pure: (unaccounted, stale). No filesystem access, so it is testable synthetically."""
    unaccounted = sh_files - referenced - set(exempt)
    stale = {name for name in exempt if name in referenced or name not in sh_files}
    return unaccounted, stale

def _live() -> tuple[set, set]:
    sh_files = {p.name for p in (_REPO_ROOT / "tests").glob("*.sh")}
    ci_text = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    referenced = _referenced_in_ci(ci_text)
    # A wrong path or a parse that finds nothing must not pass vacuously.
    assert sh_files, "no tests/*.sh found — wrong repo root?"
    assert referenced, "no shell test found in any ci.yml run: step — parse broken?"
    return sh_files, referenced

# --- live-repo checks ---
def test_every_shell_test_is_wired_or_exempt():
    unaccounted, _ = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
    assert not unaccounted, (
        f"tests/*.sh not in ci.yml and not in CI_EXEMPT_SHELL_TESTS: {sorted(unaccounted)}"
    )

def test_no_stale_exemptions():
    _, stale = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
    assert not stale, f"CI_EXEMPT_SHELL_TESTS entries now stale (wired or deleted): {sorted(stale)}"

def test_exemptions_have_reasons():
    empty = [name for name, reason in CI_EXEMPT_SHELL_TESTS.items() if not reason.strip()]
    assert not empty, f"CI_EXEMPT_SHELL_TESTS entries missing a reason: {empty}"

# --- synthetic checks: prove the guard can fail ---
def test_new_unwired_file_is_unaccounted():
    assert _violations({"a.sh", "new.sh"}, {"a.sh"}, {}) == ({"new.sh"}, set())

def test_exempt_file_that_is_wired_is_stale():
    assert _violations({"a.sh"}, {"a.sh"}, {"a.sh": "r"}) == (set(), {"a.sh"})

def test_exempt_file_that_is_missing_is_stale():
    assert _violations({"a.sh"}, {"a.sh"}, {"gone.sh": "r"}) == (set(), {"gone.sh"})

def test_commented_out_or_disabled_step_is_not_referenced():
    ci = (
        "jobs:\n  tests:\n    steps:\n"
        "      - run: bash tests/test_live.sh\n"
        "      # - run: bash tests/test_commented.sh\n"
        "      - run: bash tests/test_off.sh\n        if: false\n"
    )
    assert _referenced_in_ci(ci) == {"test_live.sh"}
```

This is illustrative, not a literal diff to apply verbatim — the implement phase writes
the real file, seeds `CI_EXEMPT_SHELL_TESTS` from a fresh run of the reproduce command
against its own branch HEAD (not this spec's snapshot), and follows existing repo
formatting/import conventions (e.g. wherever `re`, `yaml` and `Path` are already imported
elsewhere in `tests/`).

No production code changes. No `ci.yml` changes. The new test file is picked up by the
existing `python -m pytest tests/ -v` step with zero additional CI wiring.

The set logic lives in a pure helper `_violations(sh_files, referenced, exempt) ->
(unaccounted, stale)`, unit-tested with synthetic inputs: a new unwired file is
unaccounted; an exempt file that is wired is stale; an exempt file that is missing is
stale; a commented-out `run:` line (and a step under `if: false`) is not referenced. The
live-repo tests also assert `sh_files` and `referenced` are non-empty, so a wrong path
cannot pass vacuously. Without the synthetic cases the issue's first acceptance item ("a
test fails when…") would be asserted only by inspection, since every live-repo test is
green by construction on the day it lands.

**What runs where:** all of the above is pure Python and runs in CI's `tests` job via
pytest (`ci.yml:13`); it also runs unchanged on a developer's Windows host. No shell test
is added, so nothing here depends on the per-file `- run:` list.

**Doc follow-through (implement phase):**
`docs/factory-target-boundary.md`'s "Known gaps" section (~line 309) carries a note that
"`tests/test_scheduler.sh` exists but is not in `.github/workflows/ci.yml`'s bash-test
list." Once this guard test ships, that line becomes either resolved (if
`test_scheduler.sh` is later wired) or redundant with the new allowlist (while it stays
exempt) — either way it's stale prose. Refine's scope boundary for this ticket is
`docs/superpowers/specs/` and `.archon/memory/` only, so refine does not touch it; the
implement phase updates that Known-gaps bullet to point at `CI_EXEMPT_SHELL_TESTS` and
records it in `doc-impact.md` (the artifact implement Phase 4 writes once PR #542's
docs-conformance gate is on `main`; if it has not merged, the doc edit is still made).
This is the only file other than the new test that the implementation touches.

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

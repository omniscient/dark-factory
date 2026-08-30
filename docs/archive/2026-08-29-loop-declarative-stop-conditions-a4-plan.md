# Declarative per-loop stop conditions enforced by the breaker (A4) — implementation plan

**Issue:** #198 · **Epic:** #194 · **Spec:** `docs/superpowers/specs/2026-08-29-loop-declarative-stop-conditions-a4-design.md`
**Depends on:** #195 (shipped), #301 (merged to `main` via PR #369 — verified below), #197 (spec+plan
approved on `refine/issue-197-refactor-gates---verifier-abstraction-ex`; implementation on
`origin/feat/issue-197-refactor-gates---verifier-abstraction-ex` at `e852ecf`, **not yet merged to
`main`** — signatures verified below)

## Pre-flight: dependency state re-verified against `main` (2026-08-29)

Per the spec's own Sequencing note ("the plan phase re-verifies both file/function names against
`main` before writing"):

- **#301 is merged** (`739c2d6` Merge PR #369). `main`'s `scripts/factory_core/adapter.py` already
  has the five-move-block shape (`discovery`/`handoff`/`verification`/`persistence`/`scheduling`)
  and `_validate_subblock(entry, index, name, "scheduling", str_fields=("failure_behavior",),
  required_fields=("failure_behavior",))` at the exact call site R1 describes. Confirmed by direct
  read of `main:scripts/factory_core/adapter.py` and `main:tests/test_adapter.py`.
- **This branch (`refine/issue-198-...`) is currently 21 commits behind `main`** and still carries
  the pre-#301 flat-field `adapter.py`/`test_adapter.py`. `breaker.py`, `run_record.py`, `cli.py`,
  `scheduler.sh`, `test_factory_core_breaker.py`, and `test_scheduler.sh` are **byte-identical**
  between this branch and `main` (verified: empty `git diff`), so every task below that touches those
  six files is written directly against this branch's current content with no re-verification risk.
  Task 1 (the `adapter.py`/`test_adapter.py` task) is written against `main`'s content instead
  (quoted in-place below). **No merge step is needed and none must be attempted:** the workflow's
  `setup-branch` node (`workflows/archon-dark-factory.yaml`, `git checkout -b "$BRANCH"`) creates
  `feat/issue-198-*` from the freshly cloned `main`, so the feat branch already carries #301's code
  and Task 1 applies directly. If `main` has moved and `adapter.py`'s `scheduling`
  `_validate_subblock` call deviates from what is quoted here, re-verify against the checked-out
  tree before editing.
- **#197's code exists on `origin/feat/issue-197-refactor-gates---verifier-abstraction-ex`
  (`e852ecf`), not yet on `main`** (operator review, 2026-08-29). The issue's `Depends on: #197`
  line already gates the scheduler's *implementation* dispatch (`Fix issue #198`) until #197 is
  Done — this plan does not need to invent a second gate. Every signature Tasks 16–17 call was
  verified directly against that branch's `scripts/factory_core/verifier.py`:
  - `resolve_verifier(clone_dir: str, verifier_path: str) -> str` — a plain `os.path.join`, no
    existence check.
  - `run_verifier(resolved_path: str, env: dict, timeout: int = 300) -> tuple[int, str]` — passes
    `env` to the child **verbatim** (so the `CLONE_DIR` fixture seam below holds by construction);
    **raises `VerifierError`** (does not return a verdict) on a missing/non-executable path
    (`os.access(X_OK)` guard), timeout, or a process that cannot start — `STATUS: BLOCKED`
    synthesis for those cases lives in `resolve_and_run`, not in `run_verifier`.
  - `normalize_verdict(exit_code: int, stdout: str, gate_type: str) -> str` — `gate_type` is a
    required positional; bare exit 0 → `STATUS: PASS`, non-zero → `STATUS: BLOCKED`/high.
  - `verdict.py`: `parse_verdict(content)`, `format_verdict(gate_type, status, findings_count,
    severity)`.
  - The CLI (`python -m factory_core.verifier`) takes `--clone-dir --loop-name --verifier-path
    [--timeout --issue-num --factory-repo-slug --side-effect-level] run --out`; `gate_type` is
    derived as `loop:<loop-name>` and there is **no `--gate-type` flag** — which is why Task 17
    uses the Python API (the spec's own stated fallback, R6) and adds no CLI flag.

  **The implementer must still re-verify these names against `main` immediately before starting
  Task 17** (Task 16's predicate script itself has no #197 dependency and can be built any time)
  and adjust only if #197 merged with a different shape than `e852ecf`.
- **Implement-phase reminder (memory pattern, issue #42):** a refine-phase spec/plan approved on a
  sibling `refine/issue-198-...` branch does not transfer automatically onto the `feat/issue-198-...`
  branch implementation happens on — the implement phase must itself copy
  `docs/superpowers/specs/2026-08-29-loop-declarative-stop-conditions-a4-design.md` and this plan file
  onto the feat branch and commit them before starting Task 1 (a prior omission of this step broke CI
  on PR #215). This plan's own `SCOPE BOUNDARY` (`docs/superpowers/plans/` only) is why this note is
  documentation here rather than a numbered task with its own commit. The same memory pattern's
  archive half applies later, at PR/merge time, not during implementation: only rename *this plan
  file* into `docs/archive/` once the ticket completes — the spec stays at its durable
  `docs/superpowers/specs/` path (archiving both broke CI on PR #215, since a test and the README both
  pin the spec path there).

  **Exact first action on the feat branch, before Task 1** (the conformance gate greps
  `docs/superpowers/specs/` and `docs/superpowers/plans/` for `#198` on the feat branch, so both
  files must be committed there):

  ```bash
  git fetch origin refine/issue-198-feat-loops---declarative-per-loop-stop-c
  git checkout FETCH_HEAD -- \
    docs/superpowers/specs/2026-08-29-loop-declarative-stop-conditions-a4-design.md \
    docs/superpowers/plans/2026-08-29-loop-declarative-stop-conditions-a4-plan.md
  git commit -m "docs: carry #198 spec+plan onto feat branch"
  ```

**Flag for the conformance reviewer:** Task 12 introduces a `--peek` flag on `breaker-evaluate-stop`
that was not in the spec's original CLI contract (R7 stated the subcommand as
`breaker-evaluate-stop --issue N --phase P --ceiling C`, no flags); the 2026-08-29 operator review
recorded it in spec R7 as the resolve-site form. The addition is justified in
Task 12's own design note against `scheduler.sh`'s actual (verified) code shape — the resolve site's
increment is structurally deferred to its `CONFLICTING` dispatch branch, unlike the other three sites
— and preserves R7's byte-identical-parity claim, which a literal reading of R7 would otherwise break
at exactly this one site. Surfacing it explicitly here so it is reviewed as a deliberate, evidenced
deviation rather than discovered as an unexplained one.

## Goal

Give `.factory/adapter.yaml` `loops:` entries real, enforced stop conditions instead of opaque
declared-but-unenforced strings (A1's `verification.stop_condition` and the new
`scheduling.max_iterations`/`scheduling.deadline_seconds`/`budget_caps.max_tokens` cap fields), so
that "the agent says it's done" can never be what ends a loop. Cap-class stops (iteration count,
wall-clock deadline, cumulative token spend) are evaluated breaker-side, before dispatch, exactly
where the factory's own `MAX_RETRIES`/`REFINE_MAX_RETRIES` ceiling is evaluated today. The
external-predicate class (`verification.stop_condition` as an executable check) is resolved through
#197's verifier seam and is Gate-2-class — never breaker-side.

Every live call site in `scheduler.sh` today declares no loops (`loops: []` everywhere), so this
ticket ships **real, tested enforcement code with no live consumer yet** — the same
"execution-inert until A2–A5" framing #301 and #197 use for themselves. The four factory-phase retry
sites (`resolve`, `implement`, `plan`, `refine`) are refactored onto the new evaluator with
byte-identical observable behavior (verified by `test_scheduler.sh`), plus one new addition: a
`runs.jsonl` audit row on every trip.

## Architecture

```
scheduler.sh (4 call sites)
    │  evaluate_stop() bash helper
    ▼
factory_core/cli.py  breaker-evaluate-stop --issue --phase --ceiling [--peek]
    │
    ▼
factory_core/breaker.py
    evaluate_stop_condition(loop_entry, issue_num, phase, ceiling, state_file, now, peek)
        → StopVerdict(stopped, reason, detail)
        - loop_entry=None (every live site today): parity path, cap-class reason is only
          ever "max_retries"
        - loop_entry=<populated #301-shape dict> (unit-tested only, no live caller):
          max_retries → max_iterations → deadline → max_tokens, first-tripped-wins
        - on trip: appends one row to runs.jsonl via run_record.append_stop_record()
        - on no trip (and not peek): advances the relevant counter(s)
    add_loop_tokens(issue_num, phase, name, n, state_file)   [unit-tested only, no live caller]
    reset_retry(key, state_file)   [extended to pop the three new :loop:<name>:* suffixes]

External-predicate class (verification.stop_condition), never breaker-side:
    factory_core/verifier.py (#197)  resolve_verifier → run_verifier → normalize_verdict
        │
        ▼
    scripts/cost_report_marker_check.py   (#198's own concrete regression fixture)
        │  emits STATUS: PASS|BLOCKED via bare-exit-code mode
        ▼
    scripts/verdict_gate_check.sh (#271, unmodified, real subprocess in the integration test)
```

## Tech Stack

Python 3 (`factory_core` package, stdlib only — no new dependencies), Bash (`scheduler.sh`,
POSIX-ish, `set -euo pipefail`), `pytest` for Python tests, the repo's own hand-rolled bash test
harness (`tests/test_scheduler.sh`, stub-log assertions) for shell tests.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/adapter.py` | +2 names in the `scheduling` block's `int_fields` tuple (Task 1) |
| `scripts/factory_core/breaker.py` | + `StopVerdict`, `evaluate_stop_condition` (+ its private `_evaluate_loop_caps`/`_advance_loop_counters`/`_append_stop_audit_row` helpers), `_loop_state_key`, `add_loop_tokens`, `format_trip_reason`; `reset_retry` extended (Tasks 2–5, 7–8) |
| `scripts/factory_core/run_record.py` | + `append_stop_record(record)` (Task 6) |
| `scripts/factory_core/cli.py` | + `breaker-evaluate-stop` subcommand (Task 9) |
| `scheduler.sh` | + `evaluate_stop()` bash helper; four call sites rewired (Tasks 10–12) |
| `scripts/cost_report_marker_check.py` | new — R6's example contract-satisfaction predicate (Task 16) |
| `tests/test_adapter.py` | + `scheduling.max_iterations`/`deadline_seconds` cases (Task 1) |
| `tests/test_factory_core_breaker.py` | + hermeticity fixture + all new `breaker.py` surface + parity table (Tasks 2–5, 7–8, 13) |
| `tests/test_run_record.py` | + `append_stop_record` case (Task 6) |
| `tests/test_factory_core_cli.py` | + `breaker-evaluate-stop` CLI-contract cases, CI-covered (Task 9) |
| `tests/test_run_record_hermetic.sh` | + `breaker-evaluate-stop` added to the df#300 static guard's pattern (Task 14) |
| `tests/test_scheduler.sh` | + per-site `breaker-evaluate-stop` wiring assertions (Task 14) |
| `tests/test_cost_report_marker_check.py` | new — Task 16's own unit/subprocess tests |
| `tests/test_verifier.py` (#197-owned, extended here) | + cost-report-marker fixture round-trip (Task 17) |
| `tests/test_verdict_gate_check.sh` | + R6 integration case, appended (Task 17) |

---

## Task 1 — `adapter.py`: `scheduling.max_iterations` / `scheduling.deadline_seconds` (R1, R13)

**Depends on:** the branch merging `main`'s #301 code first (see Pre-flight).

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

1. Red — add to `tests/test_adapter.py` (mirroring the existing `budget_caps.max_tokens` int-field
   tests immediately below `test_budget_caps_unknown_field_raises`):

   ```python
   def test_scheduling_max_iterations_valid_parses(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["scheduling"]["max_iterations"] = 3
       parsed["loops"][0]["scheduling"]["deadline_seconds"] = 3600
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["scheduling"]["max_iterations"] == 3
       assert merged["loops"][0]["scheduling"]["deadline_seconds"] == 3600


   def test_scheduling_max_iterations_and_deadline_absent_accepted(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
       merged = adapter.load(str(tmp_path))
       assert "max_iterations" not in merged["loops"][0]["scheduling"]
       assert "deadline_seconds" not in merged["loops"][0]["scheduling"]


   @pytest.mark.parametrize("field", ["max_iterations", "deadline_seconds"])
   @pytest.mark.parametrize("bad_value", [0, True, "60"])
   def test_scheduling_int_field_rejects_bad_values(tmp_path, field, bad_value):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["scheduling"][field] = bad_value
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'scheduling': field '{field}' must be an int >= 1")):
           adapter.load(str(tmp_path))


   def test_scheduling_budget_caps_max_retry_spend_still_accepted_and_ignored(tmp_path):
       """#301's budget_caps.max_retry_spend is #234-family territory; #198 must not
       reject it even though it never reads it."""
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "max_retry_spend": 10000}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["budget_caps"]["max_retry_spend"] == 10000
   ```

   Run: `python -m pytest tests/test_adapter.py -k scheduling_max_iterations -v` — expect
   `NameError`/`KeyError`-driven failures (fields don't exist yet).

2. Green — in `scripts/factory_core/adapter.py`, change the `scheduling` sub-block call inside
   `_validate_loop` from:

   ```python
       _validate_subblock(entry, index, name, "scheduling",
                           str_fields=("failure_behavior",),
                           required_fields=("failure_behavior",))
   ```

   to:

   ```python
       _validate_subblock(entry, index, name, "scheduling",
                           str_fields=("failure_behavior",),
                           int_fields=("max_iterations", "deadline_seconds"),
                           required_fields=("failure_behavior",))
   ```

   This is the entire code change — `_validate_subblock`'s existing `int_fields` branch already
   produces the exact R13 error string (`block 'scheduling': field '{field}' must be an int >= 1`)
   and already rejects `bool` (`isinstance(v, bool)` guard) and non-int.

3. Run: `python -m pytest tests/test_adapter.py -v` — full file green, including the new cases.
4. Commit:
   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(adapter): scheduling.max_iterations/deadline_seconds cap fields (#198 R1)"
   ```

---

## Task 2 — `breaker.py`: `StopVerdict` + state-key helpers (R3, R4)

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

1. Red — add to `tests/test_factory_core_breaker.py`:

   ```python
   from factory_core.breaker import StopVerdict, _loop_state_key


   def test_stop_verdict_defaults():
       v = StopVerdict(stopped=False)
       assert v.reason is None
       assert v.detail == {}


   def test_loop_state_key_shape():
       assert _loop_state_key("42:plan", "nightly-scan", "iter") == "42:plan:loop:nightly-scan:iter"
       assert _loop_state_key("42", "nightly-scan", "tokens") == "42:loop:nightly-scan:tokens"
   ```

   Run: `python -m pytest tests/test_factory_core_breaker.py -k "stop_verdict or loop_state_key" -v`
   — `ImportError` (names don't exist yet).

2. Green — in `scripts/factory_core/breaker.py`, add near the top (after the existing imports) and
   before `_DEFAULT_STATE`:

   ```python
   from dataclasses import dataclass, field
   from datetime import datetime, timezone
   ```

   and after `_make_key`:

   ```python
   @dataclass
   class StopVerdict:
       """Result of evaluate_stop_condition. `reason` is one of the closed cap-class
       enum {"max_retries", "max_iterations", "deadline", "max_tokens"} or None (not
       tripped) — never a value implying a *successful* stop; that verdict class lives
       on #197's verifier seam, never here (spec R3)."""
       stopped: bool
       reason: "str | None" = None
       detail: dict = field(default_factory=dict)


   def _loop_state_key(key: str, name: str, suffix: str) -> str:
       return f"{key}:loop:{name}:{suffix}"
   ```

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — new cases green, no regressions.
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(breaker): StopVerdict + per-loop state-key helper (#198 R3/R4)"
   ```

---

## Task 3 — `breaker.py`: `evaluate_stop_condition` parity path (`loop_entry=None`) (R3, R7)

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

0. **Hermeticity first** (df#300 precedent): once this task's `evaluate_stop_condition` starts
   calling through to `run_record.append_stop_record` (wired fully in Task 7), any test that trips
   the ceiling writes a real `runs.jsonl` row. Unlike `tests/test_run_record.py` (which has an
   autouse `_hermetic_scheduler_state_dir` fixture), `tests/test_factory_core_breaker.py` has no such
   guard today. Add, near the top of the file (after the existing imports), before any test function:

   ```python
   import pytest
   from factory_core import run_record


   @pytest.fixture(autouse=True)
   def _hermetic_runs_jsonl(tmp_path, monkeypatch):
       """Never let a tripped StopVerdict's audit row (#198 R8) write to the real
       /var/lib/dark-factory/runs.jsonl (df#300 precedent — mirrors
       tests/test_run_record.py's own hermeticity fixture). _append_jsonl reads the
       JSONL_PATH module global directly, not a re-derived SCHEDULER_STATE_DIR, so it
       must be patched directly rather than via SCHEDULER_STATE_DIR."""
       monkeypatch.setattr(run_record, "JSONL_PATH", tmp_path / "runs.jsonl")
   ```

   This makes every test in the file hermetic by default; Tasks 6 and 7's tests that assert on the
   jsonl content directly still set `monkeypatch.setattr(run_record, "JSONL_PATH", jsonl)` themselves
   (harmless redundant override, same tmp-path family, last-write-wins within one test).

1. Red — add:

   ```python
   from factory_core.breaker import evaluate_stop_condition


   def test_evaluate_stop_condition_parity_not_tripped_increments(tmp_path):
       sf = tmp_path / "state.json"
       v = evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       assert v == StopVerdict(False)
       assert get_retry_count("42:plan", sf) == 1


   def test_evaluate_stop_condition_parity_trips_at_ceiling(tmp_path):
       sf = tmp_path / "state.json"
       for _ in range(3):
           evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       v = evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       assert v.stopped is True
       assert v.reason == "max_retries"
       # tripped: counter is NOT incremented past the ceiling
       assert get_retry_count("42:plan", sf) == 3


   def test_evaluate_stop_condition_peek_does_not_increment(tmp_path):
       sf = tmp_path / "state.json"
       v = evaluate_stop_condition(None, 42, "resolve", ceiling=3, state_file=sf, peek=True)
       assert v == StopVerdict(False)
       assert get_retry_count("42:resolve", sf) == 0


   def test_evaluate_stop_condition_peek_still_trips_at_ceiling(tmp_path):
       sf = tmp_path / "state.json"
       from factory_core.breaker import set_retry_count
       set_retry_count("42:resolve", 3, sf)
       v = evaluate_stop_condition(None, 42, "resolve", ceiling=3, state_file=sf, peek=True)
       assert v.stopped is True
       assert v.reason == "max_retries"


   def test_evaluate_stop_condition_parity_never_writes_loop_key(tmp_path):
       sf = tmp_path / "state.json"
       evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       data = json.loads(sf.read_text())
       assert not any(":loop:" in k for k in data)
   ```

   Run: `python -m pytest tests/test_factory_core_breaker.py -k evaluate_stop_condition -v` —
   `ImportError`.

2. Green — add to `scripts/factory_core/breaker.py`, after `_loop_state_key`:

   ```python
   def evaluate_stop_condition(
       loop_entry: Optional[dict],
       issue_num: int,
       phase: str,
       ceiling: int,
       state_file: Path = _DEFAULT_STATE,
       now: Optional[int] = None,
       peek: bool = False,
   ) -> StopVerdict:
       """Cap-class-only stop evaluator (state-file I/O only — no subprocess, no
       network; the external-predicate class lives on #197's verifier.py seam, never
       here). `loop_entry=None` is the parity path every live scheduler.sh site uses
       today: identical to the inline get_retry_count/compare/increment_retry sequence
       it replaces, with one addition — a runs.jsonl audit row on trip (R8).
       `peek=True` evaluates without advancing any counter (used only by the
       conflict-resolve site, whose own increment is deferred to its dispatch branch —
       see Task 12's note); a trip is still recorded and audited under peek.
       """
       key = _make_key(issue_num, phase)
       count = get_retry_count(key, state_file)

       reason: Optional[str] = None
       detail: dict = {}
       if count >= ceiling:
           reason, detail = "max_retries", {"count": count, "ceiling": ceiling}

       if reason is None and loop_entry is not None:
           reason, detail = _evaluate_loop_caps(loop_entry, key, ceiling, state_file, now)

       if reason is not None:
           verdict = StopVerdict(True, reason, detail)
           _append_stop_audit_row(verdict, issue_num, phase, loop_entry)
           return verdict

       if not peek:
           increment_retry(key, state_file)
           if loop_entry is not None:
               _advance_loop_counters(loop_entry, key, state_file, now)
       return StopVerdict(False)
   ```

   Add stub helpers (fleshed out in Task 4) so the module imports cleanly:

   ```python
   def _evaluate_loop_caps(loop_entry, key, ceiling, state_file, now):
       return None, {}


   def _advance_loop_counters(loop_entry, key, state_file, now):
       pass


   def _append_stop_audit_row(verdict: StopVerdict, issue_num: int, phase: str,
                               loop_entry: Optional[dict]) -> None:
       pass  # wired for real in Task 7
   ```

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — Task 3's four new cases green
   (the peek-trips-at-ceiling case exercises `_append_stop_audit_row`'s no-op stub harmlessly).
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(breaker): evaluate_stop_condition parity path + peek mode (#198 R3/R7)"
   ```

---

## Task 4 — `breaker.py`: populated-`loop_entry` cap-class logic (R2, R3, R4, R13)

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

**Design note (R2 tighten-only rule):** `max_iterations` compares against
`min(scheduling.max_iterations, ceiling)` — the factory ceiling always wins if lower. `deadline`
and `max_tokens` have no factory-side equivalent to tighten against, so they apply as declared.

1. Red — add (a synthetic #301-shape fixture matching `main`'s `_VALID_LOOP_ENTRY` five-block shape):

   ```python
   def _loop(name="nightly-scan", **scheduling_extra):
       entry = {
           "name": name,
           "purpose": "test loop",
           "side_effect_level": 2,
           "discovery": {"trigger": "cron:0 6 * * *", "inputs": []},
           "handoff": {"manifest": "h.py", "outputs": []},
           "verification": {"verifier": "v.py", "stop_condition": "s.py"},
           "persistence": {"artifacts": []},
           "scheduling": {"failure_behavior": "escalate_to_human", **scheduling_extra},
       }
       return entry


   def test_max_iterations_trips_after_n_evaluations(tmp_path):
       sf = tmp_path / "state.json"
       entry = _loop(max_iterations=3)
       for _ in range(3):
           v = evaluate_stop_condition(entry, 7, "implement", ceiling=10, state_file=sf)
           assert v.stopped is False
       v = evaluate_stop_condition(entry, 7, "implement", ceiling=10, state_file=sf)
       assert v.stopped is True
       assert v.reason == "max_iterations"
       assert v.detail["iter"] == 3
       assert v.detail["max_iterations"] == 3


   def test_max_iterations_tighten_only_factory_ceiling_wins(tmp_path):
       """side_effect_level 5, max_iterations=10, ceiling=3: the 4th evaluation trips
       on the factory ceiling, not the declared 10 (R2)."""
       sf = tmp_path / "state.json"
       entry = _loop(max_iterations=10)
       entry["side_effect_level"] = 5
       for _ in range(3):
           evaluate_stop_condition(entry, 7, "implement", ceiling=3, state_file=sf)
       v = evaluate_stop_condition(entry, 7, "implement", ceiling=3, state_file=sf)
       assert v.stopped is True
       assert v.reason == "max_retries"


   def test_deadline_trips_at_exact_boundary(tmp_path):
       sf = tmp_path / "state.json"
       entry = _loop(deadline_seconds=60)
       v0 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1000)
       assert v0.stopped is False
       v1 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1059)
       assert v1.stopped is False
       v2 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1060)
       assert v2.stopped is True
       assert v2.reason == "deadline"
       assert v2.detail["elapsed"] == 60


   def test_deadline_start_anchored_once(tmp_path):
       sf = tmp_path / "state.json"
       entry = _loop(deadline_seconds=60)
       evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1000)
       evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1010)
       # _make_key(8, "implement") is the bare "8" (implement's special case, breaker.py's
       # existing convention) — the loop-scoped key is "8:loop:<name>:...", NOT
       # "8:implement:loop:...". Matches Task 2's own _loop_state_key test.
       assert get_retry_count("8:loop:nightly-scan:deadline_start", sf) == 1000


   def test_max_tokens_absent_never_trips(tmp_path):
       sf = tmp_path / "state.json"
       entry = _loop()  # no max_tokens anywhere — budget_caps absent entirely
       v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
       assert v.stopped is False


   def test_max_tokens_trips_after_add_loop_tokens(tmp_path):
       from factory_core.breaker import add_loop_tokens
       sf = tmp_path / "state.json"
       entry = _loop()
       entry["budget_caps"] = {"max_tokens": 1000}
       add_loop_tokens(9, "implement", "nightly-scan", 1000, sf)
       v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
       assert v.stopped is True
       assert v.reason == "max_tokens"
       assert v.detail == {"tokens": 1000, "max_tokens": 1000}


   def test_populated_entry_no_caps_declared_behaves_as_parity(tmp_path):
       """Absence of every cap field means parity (R2): a populated entry with no
       max_iterations/deadline_seconds/budget_caps never trips on cap grounds."""
       sf = tmp_path / "state.json"
       entry = _loop()
       for _ in range(5):
           v = evaluate_stop_condition(entry, 10, "implement", ceiling=10, state_file=sf)
           assert v.stopped is False


   def test_add_loop_tokens_from_run_record_totals_shape(tmp_path):
       """R7: add_loop_tokens is 'unit-tested against run_record totals fixtures' —
       pin the intended data source (input + output tokens summed), not a bare int."""
       from factory_core.breaker import add_loop_tokens
       sf = tmp_path / "state.json"
       totals = {"gen_ai.usage.input_tokens": 600, "gen_ai.usage.output_tokens": 400}
       n = totals["gen_ai.usage.input_tokens"] + totals["gen_ai.usage.output_tokens"]
       assert add_loop_tokens(9, "implement", "nightly-scan", n, sf) == 1000
       entry = _loop()
       entry["budget_caps"] = {"max_tokens": 1000}
       v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
       assert v.stopped is True and v.reason == "max_tokens"


   def test_cap_class_trip_independent_of_predicate_state(tmp_path):
       """R6's third fixture assertion, proven here directly against breaker.py — it
       needs nothing from #197's verifier.py, so it does not wait on Task 17: with
       max_iterations reached, evaluate_stop_condition trips with reason
       max_iterations regardless of any predicate/verification field's content."""
       sf = tmp_path / "state.json"
       entry = _loop(max_iterations=1)
       entry["verification"]["stop_condition"] = "scripts/cost_report_marker_check.py"
       v = evaluate_stop_condition(entry, 300, "implement", ceiling=10, state_file=sf)
       assert v.stopped is False
       v2 = evaluate_stop_condition(entry, 300, "implement", ceiling=10, state_file=sf)
       assert v2.stopped is True
       assert v2.reason == "max_iterations"
   ```

   Run: `python -m pytest tests/test_factory_core_breaker.py -k "max_iterations or deadline or max_tokens" -v`
   — fails (`_evaluate_loop_caps`/`_advance_loop_counters` are no-op stubs, `add_loop_tokens`
   doesn't exist).

2. Green — replace the two stub functions in `scripts/factory_core/breaker.py`:

   ```python
   def _evaluate_loop_caps(loop_entry, key, ceiling, state_file, now):
       name = loop_entry["name"]
       scheduling = loop_entry.get("scheduling") or {}
       budget_caps = loop_entry.get("budget_caps") or {}
       max_iterations = scheduling.get("max_iterations")
       deadline_seconds = scheduling.get("deadline_seconds")
       max_tokens = budget_caps.get("max_tokens")

       if max_iterations is not None:
           cur_iter = get_retry_count(_loop_state_key(key, name, "iter"), state_file)
           effective = min(max_iterations, ceiling)
           if cur_iter >= effective:
               return "max_iterations", {
                   "iter": cur_iter, "max_iterations": max_iterations,
                   "effective_ceiling": effective,
               }

       if deadline_seconds is not None:
           deadline_start = get_retry_count(_loop_state_key(key, name, "deadline_start"), state_file)
           if deadline_start:
               now_ts = now if now is not None else int(time.time())
               elapsed = now_ts - deadline_start
               if elapsed >= deadline_seconds:
                   return "deadline", {"elapsed": elapsed, "deadline_seconds": deadline_seconds}

       if max_tokens is not None:
           cur_tokens = get_retry_count(_loop_state_key(key, name, "tokens"), state_file)
           if cur_tokens >= max_tokens:
               return "max_tokens", {"tokens": cur_tokens, "max_tokens": max_tokens}

       return None, {}


   def _advance_loop_counters(loop_entry, key, state_file, now):
       name = loop_entry["name"]
       iter_key = _loop_state_key(key, name, "iter")
       deadline_key = _loop_state_key(key, name, "deadline_start")
       new_iter = get_retry_count(iter_key, state_file) + 1
       _write_key(iter_key, new_iter, state_file)
       if get_retry_count(deadline_key, state_file) == 0:
           now_ts = now if now is not None else int(time.time())
           _write_key(deadline_key, now_ts, state_file)
   ```

   Add `import time` to the top-of-file imports. Add `add_loop_tokens` after `set_retry_count`:

   ```python
   def add_loop_tokens(issue_num: int, phase: str, name: str, n: int,
                        state_file: Path = _DEFAULT_STATE) -> int:
       """Adds n to the per-loop cumulative token counter. No live caller today (R7) —
       becomes live only when a future loop-dispatcher passes a populated loop_entry
       and reports run_record totals through this helper."""
       key = _loop_state_key(_make_key(issue_num, phase), name, "tokens")
       new = get_retry_count(key, state_file) + n
       _write_key(key, new, state_file)
       return new
   ```

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — all green.
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(breaker): populated-loop_entry cap-class evaluation + add_loop_tokens (#198 R2/R3/R4)"
   ```

---

## Task 5 — `breaker.py`: `reset_retry` pops the three new suffixes (R4)

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

1. Red — add:

   ```python
   def test_reset_retry_clears_loop_state(tmp_path):
       from factory_core.breaker import _make_key, add_loop_tokens
       sf = tmp_path / "state.json"
       entry = _loop(max_iterations=5)
       entry["budget_caps"] = {"max_tokens": 5000}
       evaluate_stop_condition(entry, 11, "implement", ceiling=10, state_file=sf)
       add_loop_tokens(11, "implement", "nightly-scan", 100, sf)

       key = _make_key(11, "implement")
       assert get_retry_count(f"{key}:loop:nightly-scan:iter", sf) == 1
       assert get_retry_count(f"{key}:loop:nightly-scan:deadline_start", sf) != 0
       assert get_retry_count(f"{key}:loop:nightly-scan:tokens", sf) == 100

       reset_retry(key, sf)

       assert get_retry_count(f"{key}:loop:nightly-scan:iter", sf) == 0
       assert get_retry_count(f"{key}:loop:nightly-scan:deadline_start", sf) == 0
       assert get_retry_count(f"{key}:loop:nightly-scan:tokens", sf) == 0

       # next evaluation starts fresh — not tripped even though 5 prior "attempts" existed
       v = evaluate_stop_condition(entry, 11, "implement", ceiling=10, state_file=sf)
       assert v.stopped is False
   ```

   Note: `reset_retry(key, ...)` only knows the bare key, not the loop `name` — see the green step
   for how the suffix is discovered generically (matching how `:sig`/`:delivery` are popped by
   literal string, but `:loop:<name>:*` needs a prefix scan since `name` isn't a `reset_retry`
   parameter and must not become one — `reset_retry`'s existing four call sites across the codebase
   pass only `key`).

   Run: `python -m pytest tests/test_factory_core_breaker.py -k reset_retry_clears_loop_state -v` —
   fails (extra keys survive the reset).

2. Green — in `reset_retry`, after the existing `data.pop(f"{key}:delivery", None)` line, add a
   prefix-scan pop (state files are small — tens of keys — so a linear scan is fine, matching this
   file's existing "no external deps, flat dict" posture):

   ```python
       # #198 R4: pop every per-loop suffix this key owns (<key>:loop:<name>:iter/
       # deadline_start/tokens for every declared loop name that ever wrote one) —
       # same #33/#279 rationale as the :sig/:delivery pops above: a resumed ticket
       # must not inherit banked loop-scoped state from a prior episode.
       loop_prefix = f"{key}:loop:"
       for k in [k for k in data if k.startswith(loop_prefix)]:
           data.pop(k, None)
   ```

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — green.
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "fix(breaker): reset_retry pops per-loop :iter/:deadline_start/:tokens suffixes (#198 R4)"
   ```

---

## Task 6 — `run_record.py`: `append_stop_record` (R8)

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Read `tests/test_run_record.py`'s existing style around `_append_jsonl`/`JSONL_PATH` usage first
   (`grep -n "_append_jsonl\|JSONL_PATH" tests/test_run_record.py`) to match its monkeypatch
   convention for pointing `JSONL_PATH` at a tmp file.

2. Red — add to `tests/test_run_record.py`:

   ```python
   def test_append_stop_record_writes_jsonl_no_seq(tmp_path, monkeypatch):
       # Module is imported as `rr` in this file (l.8: `from factory_core import
       # run_record as rr`) — every existing test uses that alias, not `run_record`.
       jsonl = tmp_path / "runs.jsonl"
       monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
       posted = []
       monkeypatch.setattr(rr, "_post_seq_raw", lambda payload: posted.append(payload))
       rr.append_stop_record({"stage": "stop_condition", "verdict": "STOPPED"})
       lines = jsonl.read_text().strip().splitlines()
       assert len(lines) == 1
       assert json.loads(lines[0]) == {"stage": "stop_condition", "verdict": "STOPPED"}
       assert posted == []
   ```

   Run: `python -m pytest tests/test_run_record.py -k append_stop_record -v` — `AttributeError`.

3. Green — in `scripts/factory_core/run_record.py`, add immediately after `_append_jsonl`:

   ```python
   def append_stop_record(record: dict) -> None:
       """Public wrapper for a breaker stop-condition audit row (#198 R8) — writes to
       runs.jsonl only, no Seq post (unlike cmd_record/_post_seq, which are for actual
       run verdicts, not breaker decisions)."""
       _append_jsonl(record)
   ```

4. Run: `python -m pytest tests/test_run_record.py -v` — green, no regressions.
5. Commit:
   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(run_record): append_stop_record — Seq-free audit-row writer (#198 R8)"
   ```

---

## Task 7 — `breaker.py`: wire the `runs.jsonl` audit row on trip (R8)

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

1. Red — add:

   ```python
   def test_trip_appends_runs_jsonl_row_parity_path(tmp_path, monkeypatch):
       sf = tmp_path / "state.json"
       jsonl = tmp_path / "runs.jsonl"
       import factory_core.run_record as run_record
       monkeypatch.setattr(run_record, "JSONL_PATH", jsonl)
       from factory_core.breaker import set_retry_count
       set_retry_count("42:plan", 3, sf)
       evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       rows = [json.loads(l) for l in jsonl.read_text().strip().splitlines()]
       assert len(rows) == 1
       row = rows[0]
       assert row["stage"] == "stop_condition"
       assert row["verdict"] == "STOPPED"
       assert row["issue_number"] == 42
       assert row["phase"] == "plan"
       assert row["loop"] is None
       # R8: no run_id — a breaker decision is not a run. Load-bearing for
       # reconcile_cost_reports.py's _load_jsonl_stubs, which skips any row without one
       # (confirmed: scripts/reconcile_cost_reports.py:65-68) rather than reporting a
       # spurious "irrecoverable" run.
       assert "run_id" not in row
       assert row["reason"] == "max_retries"
       assert row["failure_behavior"] is None
       assert "timestamp" in row


   def test_non_tripped_evaluation_writes_no_row(tmp_path, monkeypatch):
       sf = tmp_path / "state.json"
       jsonl = tmp_path / "runs.jsonl"
       import factory_core.run_record as run_record
       monkeypatch.setattr(run_record, "JSONL_PATH", jsonl)
       evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       assert not jsonl.exists() or jsonl.read_text() == ""


   def test_trip_row_failure_behavior_truncated_to_64_chars(tmp_path, monkeypatch):
       sf = tmp_path / "state.json"
       jsonl = tmp_path / "runs.jsonl"
       import factory_core.run_record as run_record
       monkeypatch.setattr(run_record, "JSONL_PATH", jsonl)
       entry = _loop(max_iterations=1)
       entry["scheduling"]["failure_behavior"] = "x" * 200
       # First evaluation: :iter is 0, effective=min(1,10)=1, 0 >= 1 is False — not
       # tripped yet (matches the ">=  evaluated before the dispatch" semantics Task 4
       # already exercises). The second evaluation trips.
       evaluate_stop_condition(entry, 43, "implement", ceiling=10, state_file=sf)
       evaluate_stop_condition(entry, 43, "implement", ceiling=10, state_file=sf)
       row = json.loads(jsonl.read_text().strip().splitlines()[0])
       assert row["failure_behavior"] == "x" * 64
       assert row["loop"] == "nightly-scan"
       assert row["reason"] == "max_iterations"


   def test_trip_audit_row_write_failure_does_not_swallow_verdict(tmp_path, monkeypatch, capsys):
       """Operator review (2026-08-29): the audit row is written on the live scheduler.sh
       path, where `EVAL_RESULT=$(evaluate_stop ...)` runs under `set -euo pipefail` — an
       OSError escaping here (unwritable runs.jsonl, flock failure) would exit
       breaker-evaluate-stop non-zero and kill the whole poll loop at the moment a
       ticket trips. Today's inline compare has no such surface (_write_key swallows
       OSError). The trip verdict must survive; the failure is reported on stderr."""
       sf = tmp_path / "state.json"
       import factory_core.run_record as run_record

       def _boom(record):
           raise OSError("read-only runs.jsonl")

       monkeypatch.setattr(run_record, "append_stop_record", _boom)
       from factory_core.breaker import set_retry_count
       set_retry_count("42:plan", 3, sf)
       v = evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
       assert v.stopped is True
       assert v.reason == "max_retries"
       assert "stop-condition audit row not written" in capsys.readouterr().err
   ```

   Run: `python -m pytest tests/test_factory_core_breaker.py -k "runs_jsonl or non_tripped_evaluation or audit_row_write_failure" -v`
   — fails (`_append_stop_audit_row` is still a no-op stub; the write-failure case fails on the
   missing stderr message).

2. Green — replace the stub in `scripts/factory_core/breaker.py` (and add `import sys` to the
   top-of-file imports):

   ```python
   def _append_stop_audit_row(verdict: StopVerdict, issue_num: int, phase: str,
                               loop_entry: Optional[dict]) -> None:
       from . import run_record
       failure_behavior = None
       loop_name = None
       if loop_entry is not None:
           loop_name = loop_entry.get("name")
           fb = (loop_entry.get("scheduling") or {}).get("failure_behavior")
           if fb:
               failure_behavior = fb[:64]
       try:
           run_record.append_stop_record({
               "stage": "stop_condition",
               "verdict": "STOPPED",
               "issue_number": issue_num,
               "phase": phase,
               "loop": loop_name,
               "reason": verdict.reason,
               "failure_behavior": failure_behavior,
               "detail": verdict.detail,
               "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           })
       except OSError as exc:
           # Operator review (2026-08-29): this runs on scheduler.sh's live dispatch
           # path under `set -euo pipefail`; a propagated OSError would turn one
           # unwritable runs.jsonl into a dead poll loop. Mirror _write_key's posture
           # (swallow OSError) but say so loudly — the verdict itself is unaffected.
           print(f"breaker: stop-condition audit row not written: {exc}", file=sys.stderr)
   ```

   Note: `run_record` transitively imports `model_proxy` → `aiohttp` (installed in the image,
   `Dockerfile` `pip install ... aiohttp`, and in CI); the import stays lazy so the non-trip path
   never pays for it.

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — all green (this file's full suite,
   including Tasks 2–7's new cases).
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(breaker): runs.jsonl audit row on every cap-class trip (#198 R8)"
   ```

---

## Task 8 — `breaker.py`: `format_trip_reason` — the four exact R13 strings

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

**Design note:** the CLI subcommand (Task 9) prints only `stopped=<bool> reason=<enum>` per the
spec's literal CLI contract — every live `scheduler.sh` site only ever sees `reason=max_retries`
(the only reason `loop_entry=None` can produce) and already hardcodes that exact byte-identical
string per site (`"retry limit of ${MAX_RETRIES} reached"` / `"... for conflict resolution"` —
unchanged, Tasks 10–12). `format_trip_reason` exists so the three loop-scoped reason strings R13
specifies are implemented and directly unit-tested even though no live caller constructs them today
(same "no live consumer" posture as `add_loop_tokens`) — a future loop-dispatcher's `trip_to_blocked`
call is the intended caller.

1. Red — add:

   ```python
   from factory_core.breaker import format_trip_reason


   def test_format_trip_reason_max_retries():
       v = StopVerdict(True, "max_retries", {"count": 3, "ceiling": 3})
       assert format_trip_reason(v, None) == "retry limit of 3 reached"


   def test_format_trip_reason_max_iterations():
       entry = _loop(max_iterations=3)
       v = StopVerdict(True, "max_iterations", {"iter": 3, "max_iterations": 3, "effective_ceiling": 3})
       assert format_trip_reason(v, entry) == (
           "loop 'nightly-scan' stop condition 'max_iterations' reached (3/3); "
           "declared failure_behavior: escalate_to_human"
       )


   def test_format_trip_reason_deadline():
       entry = _loop(deadline_seconds=60)
       v = StopVerdict(True, "deadline", {"elapsed": 61, "deadline_seconds": 60})
       assert format_trip_reason(v, entry) == (
           "loop 'nightly-scan' stop condition 'deadline' reached (61s >= 60s); "
           "declared failure_behavior: escalate_to_human"
       )


   def test_format_trip_reason_max_tokens():
       entry = _loop()
       entry["budget_caps"] = {"max_tokens": 1000}
       v = StopVerdict(True, "max_tokens", {"tokens": 1000, "max_tokens": 1000})
       assert format_trip_reason(v, entry) == (
           "loop 'nightly-scan' stop condition 'max_tokens' reached (1000/1000 tokens); "
           "declared failure_behavior: escalate_to_human"
       )


   def test_format_trip_reason_failure_behavior_truncated_to_64_chars():
       """R13 AC: 'A 200-character failure_behavior reaches the trip_to_blocked
       comment ... truncated to 64 characters.' This is the trip_to_blocked-comment
       half of that AC (format_trip_reason's output is what a future caller passes to
       trip_to_blocked's `reason` argument); the runs.jsonl-row half is
       test_trip_row_failure_behavior_truncated_to_64_chars in Task 7."""
       entry = _loop(max_iterations=3)
       entry["scheduling"]["failure_behavior"] = "x" * 200
       v = StopVerdict(True, "max_iterations", {"iter": 3, "max_iterations": 3, "effective_ceiling": 3})
       result = format_trip_reason(v, entry)
       assert result.endswith("declared failure_behavior: " + "x" * 64)
       assert "x" * 65 not in result
   ```

   Run: `python -m pytest tests/test_factory_core_breaker.py -k format_trip_reason -v` — `ImportError`.

2. Green — add to `scripts/factory_core/breaker.py`:

   ```python
   def format_trip_reason(verdict: StopVerdict, loop_entry: Optional[dict]) -> str:
       """The exact R13 trip-reason strings passed to trip_to_blocked. No live caller
       constructs the three loop-scoped variants today (R7's live sites only ever see
       reason="max_retries", which scheduler.sh already renders byte-identically
       inline) — this is the tested, ready-to-call implementation for the loop-scoped
       reasons, for whichever future loop-dispatcher wires a populated loop_entry."""
       d = verdict.detail
       if verdict.reason == "max_retries":
           return f"retry limit of {d['ceiling']} reached"
       name = loop_entry["name"]
       fb = (loop_entry.get("scheduling") or {}).get("failure_behavior", "")[:64]
       if verdict.reason == "max_iterations":
           return (f"loop '{name}' stop condition 'max_iterations' reached "
                    f"({d['iter']}/{d['max_iterations']}); declared failure_behavior: {fb}")
       if verdict.reason == "deadline":
           return (f"loop '{name}' stop condition 'deadline' reached "
                    f"({d['elapsed']}s >= {d['deadline_seconds']}s); declared failure_behavior: {fb}")
       if verdict.reason == "max_tokens":
           return (f"loop '{name}' stop condition 'max_tokens' reached "
                    f"({d['tokens']}/{d['max_tokens']} tokens); declared failure_behavior: {fb}")
       raise ValueError(f"format_trip_reason: unknown reason {verdict.reason!r}")
   ```

3. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — full file green.
4. Commit:
   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(breaker): format_trip_reason — exact R13 trip strings (#198 R13)"
   ```

---

## Task 9 — `cli.py`: `breaker-evaluate-stop` subcommand (R7)

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_cli.py`

**CI-coverage note:** `tests/test_scheduler.sh` (Task 14) is this subcommand's end-to-end exercise,
but `.github/workflows/ci.yml` does not run that file — only `pytest tests/` plus a fixed list of
other named `.sh` files. `tests/test_factory_core_cli.py` already exists (a real, CI-covered file,
not the `test_scheduler.sh` shell harness) with an established `_cli(monkeypatch, **env)` +
`monkeypatch.setattr(sys, "argv", ...)` + `capsys` pattern for exercising `cli.py` subcommands
in-process. Adding a small case there closes the CI gap directly, rather than leaving the argparse
wiring and print format covered only by a suite CI never runs.

1. Red — add to `tests/test_factory_core_cli.py`, following the file's existing `_cli`/`capsys`
   convention exactly:

   ```python
   def test_breaker_evaluate_stop_prints_stopped_and_reason(monkeypatch, tmp_path, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "plan", "--ceiling", "3",
       ])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "stopped=false reason=none"


   def test_breaker_evaluate_stop_trips_at_ceiling(monkeypatch, tmp_path, capsys):
       # A trip writes a runs.jsonl audit row (R8) via run_record.append_stop_record,
       # whose JSONL_PATH is bound at import time — same df#300 hermeticity requirement
       # as test_factory_core_breaker.py's autouse fixture (Task 3, step 0), applied
       # here by hand since this file has no such autouse fixture of its own.
       from factory_core import run_record
       monkeypatch.setattr(run_record, "JSONL_PATH", tmp_path / "runs.jsonl")
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text('{"1:plan": 3}')
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "plan", "--ceiling", "3",
       ])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "stopped=true reason=max_retries"


   def test_breaker_evaluate_stop_peek_does_not_increment(monkeypatch, tmp_path, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "resolve",
           "--ceiling", "3", "--peek",
       ])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "stopped=false reason=none"
       import json
       assert json.loads(state_file.read_text()) == {}
   ```

   Run: `python -m pytest tests/test_factory_core_cli.py -k breaker_evaluate_stop -v` — fails
   (`SystemExit` / argparse "invalid choice") until the subcommand exists.

2. Green — add handler after `_breaker_set_retry`:

   ```python
   def _breaker_evaluate_stop(args):
       from factory_core.breaker import evaluate_stop_condition
       state_file = Path(os.environ.get("STATE_FILE",
                                        "/var/lib/dark-factory/scheduler-state.json"))
       verdict = evaluate_stop_condition(
           loop_entry=None,
           issue_num=args.issue,
           phase=args.phase,
           ceiling=args.ceiling,
           state_file=state_file,
           peek=args.peek,
       )
       print(f"stopped={'true' if verdict.stopped else 'false'} reason={verdict.reason or 'none'}")
   ```

   Add the subparser after `bsr` (`breaker-set-retry`):

   ```python
       bes = sub.add_parser("breaker-evaluate-stop")
       bes.add_argument("--issue", type=int, required=True)
       bes.add_argument("--phase", required=True)
       bes.add_argument("--ceiling", type=int, required=True)
       bes.add_argument("--peek", action="store_true")
       bes.set_defaults(func=_breaker_evaluate_stop)
   ```

3. Manual smoke check:
   ```bash
   STATE_FILE=$(mktemp) bash -c 'echo "{}" > "$STATE_FILE"; \
     STATE_FILE="$STATE_FILE" python3 scripts/factory_core/cli.py breaker-evaluate-stop --issue 1 --phase plan --ceiling 3; \
     STATE_FILE="$STATE_FILE" python3 scripts/factory_core/cli.py breaker-evaluate-stop --issue 1 --phase plan --ceiling 3; \
     STATE_FILE="$STATE_FILE" python3 scripts/factory_core/cli.py breaker-evaluate-stop --issue 1 --phase plan --ceiling 3; \
     STATE_FILE="$STATE_FILE" python3 scripts/factory_core/cli.py breaker-evaluate-stop --issue 1 --phase plan --ceiling 3'
   ```
   Expected output: three `stopped=false reason=none` lines, then `stopped=true reason=max_retries`.
4. Run: `python -m pytest tests/test_factory_core_cli.py -v` — green, no regressions.
5. Commit:
   ```bash
   git add scripts/factory_core/cli.py tests/test_factory_core_cli.py
   git commit -m "feat(cli): breaker-evaluate-stop subcommand (#198 R7)"
   ```

---

## Task 10 — `scheduler.sh`: `evaluate_stop()` bash helper

**Files:** `scheduler.sh`

1. Add, immediately after `reset_retry()` (l.154–156 — the third function in the
   `# --- Retry tracking ---` group that starts at l.145 with `get_retry_count()`/l.146–148 and
   `increment_retry()`/l.150–152; keep the new helper appended to the end of that group, not spliced
   between `increment_retry` and `reset_retry`), and before `# --- Duplicate dispatch prevention ---`
   at l.158 (`trip_to_blocked` itself is much further down, at l.378, not adjacent):

   ```bash
   # --- Cap-class stop-condition evaluator (thin adapter — logic lives in factory_core/breaker.py) ---
   # Usage: evaluate_stop <issue_num> <phase> <ceiling> [--peek] — echoes
   # "stopped=true|false reason=<enum|none>". loop_entry is always None at every live
   # site today (#198 R7) — no call site here declares a populated loop.
   # SCHEDULER_STATE_DIR is forwarded explicitly, matching check_failure_signature's
   # existing precedent (l.395) — scheduler.sh assigns it without `export` (l.10), so a
   # runs.jsonl-writing call path (R8) must pass it through by hand or the audit row
   # lands under the default /var/lib/dark-factory instead of the configured state dir.
   evaluate_stop() {
     local issue_num="$1" phase="$2" ceiling="$3" peek_flag="${4:-}"
     STATE_FILE="$STATE_FILE" SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" python3 "$FACTORY_CORE_CLI" \
       breaker-evaluate-stop --issue "$issue_num" --phase "$phase" --ceiling "$ceiling" $peek_flag
   }
   ```

2. No test yet (covered by Task 14 alongside the call-site wiring). No commit yet — bundled with
   Task 11's first call-site edit so the helper is never committed unused (avoids a dead-code
   window).

---

## Task 11 — `scheduler.sh`: wire `stage_blocked_retry`, `stage_plan`, `stage_refine` (R7)

**Files:** `scheduler.sh`

**Note for the PR description (undisclosed-deviation risk):** today, `increment_retry "$ISSUE"` (and
the equivalent at the other two sites) prints the new counter value to the scheduler's own stdout as
a side effect of the CLI call. After this task that print is captured into `$EVAL_RESULT` instead and
never surfaces. This is harmless (nothing parses that stdout today), but R7's byte-identical-parity
claim names only the new `runs.jsonl` audit row as its one disclosed observable addition — call this
stdout difference out explicitly in the PR body so a conformance reviewer diffing "byte-identical"
finds a named, considered change rather than an undisclosed one.

These three sites share the identical `count|*)` branch shape. For each, replace:

```bash
    count|*)
      RETRIES=$(get_retry_count "<KEY>")
      if [ "$RETRIES" -ge "<CEILING>" ]; then
        trip_to_blocked "$ISSUE" "<PHASE>" "retry limit of ${<CEILING>} reached"
        continue
      fi
      increment_retry "<KEY>"
      ;;
```

with:

```bash
    count|*)
      EVAL_RESULT=$(evaluate_stop "$ISSUE" "<PHASE>" "<CEILING>")
      if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
        trip_to_blocked "$ISSUE" "<PHASE>" "retry limit of ${<CEILING>} reached"
        continue
      fi
      ;;
```

1. `stage_blocked_retry` (l.1049–1056), `<KEY>` = `"$ISSUE"`, `<PHASE>` = `implement`,
   `<CEILING>` = `MAX_RETRIES`:

   ```bash
       count|*)
         EVAL_RESULT=$(evaluate_stop "$ISSUE" "implement" "$MAX_RETRIES")
         if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
           trip_to_blocked "$ISSUE" "implement" "retry limit of ${MAX_RETRIES} reached"
           continue
         fi
         ;;
   ```

2. `stage_plan` (l.1114–1121), `<KEY>` = `"${ISSUE}:plan"`, `<PHASE>` = `plan`,
   `<CEILING>` = `REFINE_MAX_RETRIES`:

   ```bash
       count|*)
         EVAL_RESULT=$(evaluate_stop "$ISSUE" "plan" "$REFINE_MAX_RETRIES")
         if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
           trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
           continue
         fi
         ;;
   ```

3. `stage_refine` (l.1182–1189), same shape as `stage_plan` with `<PHASE>` = `refine`:

   ```bash
       count|*)
         EVAL_RESULT=$(evaluate_stop "$ISSUE" "refine" "$REFINE_MAX_RETRIES")
         if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
           trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
           continue
         fi
         ;;
   ```

4. Manual sanity check (full suite is Task 14, but confirm nothing is syntactically broken first):
   `bash -n scheduler.sh` — expect no output (clean parse).
5. Commit:
   ```bash
   git add scheduler.sh
   git commit -m "feat(scheduler): wire implement/plan/refine retry sites onto breaker-evaluate-stop (#198 R7)"
   ```

---

## Task 12 — `scheduler.sh`: wire `stage_conflict_resolve` with `--peek` (R7)

**Files:** `scheduler.sh`

**Design note (a real structural asymmetry this plan must account for):** unlike the other three
sites, `stage_conflict_resolve`'s retry counter increment is **not** unconditional after the ceiling
compare — it only fires inside the `CONFLICTING` case, after `get_pr_for_issue`/`check_pr_mergeable`
(l.916–921), and is skipped entirely for `UNKNOWN`/no-PR. Confirmed by direct read of `scheduler.sh`
l.867–930 and cross-checked against `tests/test_scheduler.sh`'s `_run_resolve_body` (l.1679–1721,
section W), which is a literal copy of this site's current body and independently confirms the same
shape. Naively substituting `evaluate_stop`'s combined compare-and-increment at the ceiling-check
site (l.902–906) would increment the retry counter on every poll cycle for every resolve-eligible
issue — including ones with no PR or an `UNKNOWN` mergeable state — a real behavior change (the
counter would advance without a dispatch ever happening), breaking parity. `evaluate_stop`'s
`--peek` flag (Task 9) exists specifically to keep the ceiling-check side-effect-free here, while the
existing standalone `increment_retry "${ISSUE}:resolve"` call inside the `CONFLICTING` branch stays
exactly where it is, untouched.

1. In `stage_conflict_resolve`, replace (l.902–906):

   ```bash
       RETRIES=$(get_retry_count "${ISSUE}:resolve")
       if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
         trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
         continue
       fi
   ```

   with:

   ```bash
       EVAL_RESULT=$(evaluate_stop "$ISSUE" "resolve" "$MAX_RETRIES" --peek)
       if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
         trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
         continue
       fi
   ```

   The `else` block's surrounding `if [ "$SIG_VALUE" = "environmental:delivery_failure" ]; then ...
   else ... fi` structure, and everything from `PR_NUM=$(get_pr_for_issue "$ISSUE")` onward
   (including the existing `increment_retry "${ISSUE}:resolve"` inside `CONFLICTING`), are
   **unchanged**.

2. `bash -n scheduler.sh` — clean parse.
3. Commit:
   ```bash
   git add scheduler.sh
   git commit -m "feat(scheduler): wire resolve-site ceiling check onto breaker-evaluate-stop --peek (#198 R7)"
   ```

---

## Task 13 — `test_factory_core_breaker.py`: parity table (R7 AC bullet 3)

**Files:** `tests/test_factory_core_breaker.py`

1. Red — add:

   ```python
   # `pytest` is already imported at module scope (Task 3, step 0's hermeticity fixture)
   # — reuse it, don't add a second import/alias.
   @pytest.mark.parametrize("count,ceiling", [(0, 3), (2, 3), (3, 3), (4, 3)])
   def test_evaluate_stop_condition_parity_table(tmp_path, count, ceiling):
       """For loop_entry=None, matches today's inline
       get_retry_count/compare/increment_retry exactly: stopped iff count >= ceiling;
       counter incremented iff not stopped."""
       sf = tmp_path / "state.json"
       from factory_core.breaker import set_retry_count
       set_retry_count("99:plan", count, sf)
       v = evaluate_stop_condition(None, 99, "plan", ceiling=ceiling, state_file=sf)
       expect_stopped = count >= ceiling
       assert v.stopped == expect_stopped
       expect_count = count if expect_stopped else count + 1
       assert get_retry_count("99:plan", sf) == expect_count
   ```

2. Run: `python -m pytest tests/test_factory_core_breaker.py -v` — green (this exercises code
   already built in Tasks 3–4; it should pass immediately, confirming the parity claim rather than
   driving new implementation).
3. Commit:
   ```bash
   git add tests/test_factory_core_breaker.py
   git commit -m "test(breaker): evaluate_stop_condition parity table vs today's inline compare (#198 R7)"
   ```

---

## Task 14 — `test_scheduler.sh`: per-site `breaker-evaluate-stop` wiring assertions (R7 AC bullet 2)

**Files:** `tests/test_scheduler.sh`

**Insertion point:** immediately after line 1806 (`> "$STUB_LOG"`, the last line of section W) and
**before** line 1808's `# ========== Cleanup ==========` block — appending after section W in the
"add it at the end of the file" sense is wrong: the file ends with a `rm -f`/`rm -rf` Cleanup block
and then the `${PASSED}`/`${FAILED}` results summary + `[ "$FAILED" -eq 0 ]` exit gate (l.1822–1823),
so anything literally appended after those lines would never execute and its assertions would never
count toward the suite's pass/fail result.

Each sub-case uses a fresh local body-copy helper mirroring the **post-Task-11/12** real
`scheduler.sh` code (matching this file's own established convention — sections V/W's
`_run_blocked_retry_body`/`_run_resolve_body` already mirror the pre-refactor bodies for the
delivery-failure-exemption scenarios; these new helpers mirror the refactored ceiling-check step
specifically, which V/W deliberately do not touch). Note: after Tasks 11/12 land, `_run_blocked_retry_body`
(l.1597) and `_run_resolve_body` (l.1679) still embed the *pre-refactor* inline
`get_retry_count`/compare/`increment_retry` shape on purpose — they exist to characterize
`rollback_paused_retry`/`retry_or_skip_delivery_failure` in isolation from this refactor, not to track
the real ceiling-check code, and will keep passing unmodified. Add a one-line comment to each (e.g.
`# NOTE: mirrors the pre-#198 ceiling-check shape on purpose — see section X for the current code`)
so a future reader doesn't mistake them for the current implementation.

**Stub-log quoting note:** the file's `python3` stub logs via `echo "python3 $*"` (l.31), which does
**not** preserve shell quoting — a Python `--reason "retry limit of 3 reached"` call is logged as the
literal unquoted words `--reason retry limit of 3 reached`. Every existing assertion in the file greps
without surrounding quote characters for this reason (e.g. l.148, 784); the assertions below follow
the same convention — do **not** wrap the expected `--reason ...` value in quote marks inside the grep
pattern.

1. Add:

   ```bash
   # ==========================================
   # X: breaker-evaluate-stop wiring at all four retry sites (#198 R7)
   # ==========================================
   echo ""
   echo "--- X: breaker-evaluate-stop wiring ---"

   # X1: stage_blocked_retry (implement) — one evaluate_stop call, trips at ceiling
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
   get_pr_for_issue() { echo ""; }
   dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
   export -f get_pr_for_issue dispatch

   _run_blocked_retry_ceiling_step() {
     local issue="$1"
     EVAL_RESULT=$(evaluate_stop "$issue" "implement" "$MAX_RETRIES")
     if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
       trip_to_blocked "$issue" "implement" "retry limit of ${MAX_RETRIES} reached"
       return
     fi
     dispatch "Fix issue #${issue}" > /dev/null
   }

   for i in $(seq 1 "$MAX_RETRIES"); do _run_blocked_retry_ceiling_step 200; done
   assert_eq "X1a: three evaluate_stop calls (implement)" \
     "3" "$(grep -c 'breaker-evaluate-stop --issue 200 --phase implement --ceiling 3' "$STUB_LOG" || echo 0)"
   assert_eq "X1b: three dispatches, no trip yet" \
     "3" "$(grep -c 'dispatch Fix issue #200' "$STUB_LOG" || echo 0)"
   > "$STUB_LOG"
   _run_blocked_retry_ceiling_step 200
   assert_eq "X1c: 4th call trips via breaker-trip with exact reason text" \
     "1" "$(grep -c 'breaker-trip --issue 200 --phase implement --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"
   assert_eq "X1d: no dispatch on trip" "0" "$(grep -c 'dispatch Fix' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # X2: stage_plan — one evaluate_stop call, trips at REFINE_MAX_RETRIES
   _run_plan_ceiling_step() {
     local issue="$1"
     EVAL_RESULT=$(evaluate_stop "$issue" "plan" "$REFINE_MAX_RETRIES")
     if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
       trip_to_blocked "$issue" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
       return
     fi
     dispatch "Plan issue #${issue}" > /dev/null
   }
   for i in $(seq 1 "$REFINE_MAX_RETRIES"); do _run_plan_ceiling_step 201; done
   > "$STUB_LOG"
   _run_plan_ceiling_step 201
   assert_eq "X2: stage_plan trips via breaker-trip with exact reason text" \
     "1" "$(grep -c 'breaker-trip --issue 201 --phase plan --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # X3: stage_refine — one evaluate_stop call, trips at REFINE_MAX_RETRIES
   _run_refine_ceiling_step() {
     local issue="$1"
     EVAL_RESULT=$(evaluate_stop "$issue" "refine" "$REFINE_MAX_RETRIES")
     if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
       trip_to_blocked "$issue" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
       return
     fi
     dispatch "Refine issue #${issue}" > /dev/null
   }
   for i in $(seq 1 "$REFINE_MAX_RETRIES"); do _run_refine_ceiling_step 202; done
   > "$STUB_LOG"
   _run_refine_ceiling_step 202
   assert_eq "X3: stage_refine trips via breaker-trip with exact reason text" \
     "1" "$(grep -c 'breaker-trip --issue 202 --phase refine --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # X4: stage_conflict_resolve — peek mode: evaluate_stop called with --peek, no increment
   # until the (untouched) CONFLICTING-branch increment_retry fires.
   check_pr_mergeable() { echo "CONFLICTING"; }
   get_pr_for_issue() { echo "500"; }
   export -f check_pr_mergeable get_pr_for_issue

   _run_resolve_ceiling_step() {
     local issue="$1"
     EVAL_RESULT=$(evaluate_stop "$issue" "resolve" "$MAX_RETRIES" --peek)
     if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
       trip_to_blocked "$issue" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
       return
     fi
     PR_NUM=$(get_pr_for_issue "$issue")
     MERGEABLE=$(check_pr_mergeable "$PR_NUM")
     if [ "$MERGEABLE" = "CONFLICTING" ]; then
       increment_retry "${issue}:resolve" || true
       dispatch "Deconflict issue #${issue}" > /dev/null
     fi
   }
   for i in $(seq 1 "$MAX_RETRIES"); do _run_resolve_ceiling_step 203; done
   assert_eq "X4a: three --peek evaluate_stop calls (resolve)" \
     "3" "$(grep -c 'breaker-evaluate-stop --issue 203 --phase resolve --ceiling 3 --peek' "$STUB_LOG" || echo 0)"
   assert_eq "X4b: normal counter incremented by the unchanged CONFLICTING-branch call, not by peek" \
     "3" "$(get_retry_count "203:resolve")"
   > "$STUB_LOG"
   _run_resolve_ceiling_step 203
   assert_eq "X4c: 4th call trips via breaker-trip with exact resolve reason text" \
     "1" "$(grep -c 'breaker-trip --issue 203 --phase resolve --reason retry limit of 3 reached for conflict resolution' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # Restore stubs to section defaults
   get_pr_for_issue() { echo ""; }
   check_pr_mergeable() { echo "UNKNOWN"; }
   dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
   export -f get_pr_for_issue check_pr_mergeable dispatch

   # Static drift lock, mirroring the existing #341 precedent at l.1815
   # (rollback_paused_retry wired 4x) — nothing else currently detects divergence
   # between Task 11/12's real scheduler.sh edits and this section's hand-copied
   # post-refactor bodies, and the R7 parity claim rests entirely on those copies
   # staying in sync with the real call sites.
   assert_eq "evaluate_stop wired 4x in scheduler.sh" "4" "$(grep -c 'evaluate_stop "\$ISSUE"' "$SCHED")"
   ```

2. Extend the df#300 static hermeticity guard to cover the new subcommand: in
   `tests/test_run_record_hermetic.sh`, change

   ```bash
     if grep -qE 'run-record (record|assemble)|error-signature-write' "$f"; then
   ```

   to

   ```bash
     if grep -qE 'run-record (record|assemble)|error-signature-write|breaker-evaluate-stop' "$f"; then
   ```

   `breaker-evaluate-stop` now writes `runs.jsonl` on trip (R8) via the same code path the guard
   already polices; `test_scheduler.sh` already exports `SCHEDULER_STATE_DIR` at file scope
   (l.67–68), so this addition passes immediately — it only stops the guard from silently going
   blind to the new call path, it does not require any other change. Run:
   `bash tests/test_run_record_hermetic.sh` — expect `OK`.
3. Run: `bash tests/test_scheduler.sh 2>&1 | tail -40` — expect `PASSED=<N+13> FAILED=0` (or however
   the harness reports totals; confirm zero failures and the new `X1`–`X4` lines all show `PASS`).
   Also re-run the **full** existing suite to confirm sections B, K9, K10, V, W are unaffected:
   `bash tests/test_scheduler.sh` — must show 0 failures.

   **Note for the PR description:** `.github/workflows/ci.yml` does not currently run
   `tests/test_scheduler.sh` (it runs `pytest tests/` plus a fixed list of other named `.sh` files).
   This task's assertions — the primary evidence for issue AC3 — therefore only run when a human or
   the `verify` skill runs them locally, not automatically in CI. Call this out explicitly in the PR
   body; adding `test_scheduler.sh` to CI is out of this ticket's own scope (a CI config change is not
   in the spec's file list) and should be its own follow-up if wanted.
4. Commit:
   ```bash
   git add tests/test_scheduler.sh tests/test_run_record_hermetic.sh
   git commit -m "test(scheduler): breaker-evaluate-stop wiring at all four retry sites (#198 R7)"
   ```

---

## Task 15 — Full regression pass on Tasks 1–14 before starting the predicate-class work

1. Run:
   ```bash
   python -m pytest tests/test_adapter.py tests/test_factory_core_breaker.py tests/test_run_record.py -v
   bash tests/test_scheduler.sh
   bash -n scheduler.sh
   python -m pytest tests/ -v 2>&1 | tail -30   # full suite, per CLAUDE.md conventions
   bash smoke_gate.sh || true   # informational only at plan-review time; CI runs it for real
   ```
   All green, zero regressions. This closes out every requirement except R5/R6 (external-predicate
   class), which is gated on #197.

---

## Task 16 — `scripts/cost_report_marker_check.py`: the R6 example predicate (no #197 dependency yet)

**Files:** `scripts/cost_report_marker_check.py`, `tests/test_cost_report_marker_check.py`

This script is pure repo content (a bare-exit-code check-only script) and has **no dependency on
#197** — it only calls `get_tracker().get_comments(issue_num)`, which already exists. Its wiring
into #197's `verifier.py`/`verdict_gate_check.sh` (Task 17) is what's gated on #197.

**Test-seam design note:** rather than an env-var-selected `importlib` swap (which would need
`PYTHONPATH` — or some other extra env var — to survive a `run_verifier` subprocess boundary whose
exact env-forwarding behavior isn't verifiable until #197 exists, see Task 17's own note), the test
seam here is a **`CLONE_DIR`-relative JSON fixture file**: if
`<CLONE_DIR>/.cost_report_marker_check_test_fixture.json` exists, its `{"comments": [...]}` is used
verbatim instead of calling the real tracker. `CLONE_DIR` is not a test-only variable — it is one of
the four core env vars #197's spec explicitly commits `run_verifier` to always forward (the same
`hooks.sh::run_hook` contract this predicate already documents reading), so this seam has no
dependency on any *additional* env var surviving the subprocess boundary, unlike a
`PYTHONPATH`/custom-module-name approach. Task 17 reuses this exact mechanism.

1. Red — create `tests/test_cost_report_marker_check.py`:

   ```python
   import json
   import os
   import subprocess
   import sys
   from pathlib import Path
   from unittest.mock import patch

   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


   def test_exits_0_when_marker_present():
       import cost_report_marker_check as m
       with patch.object(m, "get_tracker") as mock_gt:
           mock_gt.return_value.get_comments.return_value = [
               {"body": "some other comment"},
               {"body": "## Cost Report\n<!-- dark-factory-cost-report -->\n..."},
           ]
           assert m.check(42) == 0


   def test_exits_1_when_marker_absent():
       import cost_report_marker_check as m
       with patch.object(m, "get_tracker") as mock_gt:
           mock_gt.return_value.get_comments.return_value = [{"body": "unrelated"}]
           assert m.check(42) == 1


   def test_exits_1_when_no_comments():
       import cost_report_marker_check as m
       with patch.object(m, "get_tracker") as mock_gt:
           mock_gt.return_value.get_comments.return_value = []
           assert m.check(42) == 1


   def test_clone_dir_fixture_file_overrides_real_tracker():
       """The CLONE_DIR-relative JSON fixture seam Task 17 depends on — proven here in
       isolation, in-process, before Task 17 relies on it surviving a subprocess."""
       import cost_report_marker_check as m
       with patch.object(m, "get_tracker") as mock_gt:
           mock_gt.return_value.get_comments.return_value = [{"body": "should not be used"}]
           import tempfile
           with tempfile.TemporaryDirectory() as clone_dir:
               fixture = Path(clone_dir) / ".cost_report_marker_check_test_fixture.json"
               fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
               with patch.dict(os.environ, {"CLONE_DIR": clone_dir}):
                   assert m.check(42) == 0
                   mock_gt.assert_not_called()


   def test_cli_reads_issue_num_and_clone_dir_env(tmp_path):
       """Real subprocess invocation — the actual production entry point, exercising
       ISSUE_NUM + the CLONE_DIR fixture seam together, with no network call."""
       script = Path(__file__).resolve().parents[1] / "scripts" / "cost_report_marker_check.py"
       clone_dir = tmp_path / "clone"
       clone_dir.mkdir()
       fixture = clone_dir / ".cost_report_marker_check_test_fixture.json"
       fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
       env = {"ISSUE_NUM": "99", "CLONE_DIR": str(clone_dir), "PATH": os.environ["PATH"]}
       result = subprocess.run(["python3", str(script)], env=env, capture_output=True, text=True)
       assert result.returncode == 0
   ```

   Run: `python -m pytest tests/test_cost_report_marker_check.py -v` — `ModuleNotFoundError`.

2. Green — create `scripts/cost_report_marker_check.py`:

   ```python
   #!/usr/bin/env python3
   """Example contract-satisfaction stop-condition predicate (#198 R6): checks whether
   the durable <!-- dark-factory-cost-report --> marker comment has been posted for
   this issue — the exact regression check for #300 ("a run reached Done because
   completion was inferred from node exit status, with no cost-report comment ever
   posted"). Bare-exit-code convention for #197's verifier.py (exit 0 = PASS, exit 1 =
   BLOCKED): no STATUS: lines are printed, matching smoke-gate's own low-effort on-ramp
   for a target's first verifier.

   Env contract (set by #197's verifier.py run_verifier(), same four-var + LOOP_NAME
   contract as scripts/hooks.sh::run_hook): CLONE_DIR, ARTIFACTS_DIR, ISSUE_NUM,
   FACTORY_REPO_SLUG, LOOP_NAME. CLONE_DIR and ISSUE_NUM are read here.

   Checks marker *presence*, not the path that produced it — "posted once at run end"
   and "posted early, updated in place under the same marker" both PASS (#311's own
   stated invariant for this fixture).
   """
   import json
   import os
   import sys
   from pathlib import Path

   COST_MARKER = "<!-- dark-factory-cost-report -->"
   _TEST_FIXTURE_NAME = ".cost_report_marker_check_test_fixture.json"


   def get_tracker():
       sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ itself, so
                                                                   # `factory_core` resolves
       from factory_core.providers import get_tracker as _get_tracker
       return _get_tracker()


   def _load_comments(issue_num: int) -> list:
       # Test-only seam: a CLONE_DIR-relative fixture file, never present in production
       # (CLONE_DIR is always a real clone there). CLONE_DIR is one of the four core
       # env vars #197 commits run_verifier to always forward, unlike an ad hoc extra
       # var, so this seam has no dependency on unverified subprocess env-forwarding
       # behavior (see #198 plan Task 16/17's own design note).
       clone_dir = os.environ.get("CLONE_DIR", "")
       if clone_dir:
           fixture = Path(clone_dir) / _TEST_FIXTURE_NAME
           if fixture.is_file():
               return json.loads(fixture.read_text()).get("comments", [])
       return get_tracker().get_comments(str(issue_num))


   def check(issue_num: int) -> int:
       for comment in _load_comments(issue_num):
           if COST_MARKER in comment.get("body", ""):
               return 0
       return 1


   def main() -> None:
       issue_num = os.environ.get("ISSUE_NUM", "")
       if not issue_num.isdigit():
           sys.exit(1)  # fail closed — no issue context, never PASS
       sys.exit(check(int(issue_num)))


   if __name__ == "__main__":
       main()
   ```

   `get_tracker`'s own `sys.path.insert(0, str(Path(__file__).resolve().parent))` adds `scripts/`
   itself (this script's own directory) to `sys.path`, making `factory_core` importable regardless of
   the caller's working directory or `PYTHONPATH` — the same kind of self-sufficient
   `sys.path.insert` `cli.py` already relies on for itself (one directory deeper, at
   `scripts/factory_core/cli.py`, so its own insert climbs `parents[1]` to reach `scripts/`; this
   script sits directly in `scripts/`, so a plain `.parent` is the equivalent climb). No external
   `PYTHONPATH` setup is needed in either the test or production path.

3. **Mark the script executable** — #197's `run_verifier` raises `VerifierError` on a declared
   verifier path that exists but isn't executable (verified on `origin/feat/issue-197-...`: an
   `os.access(X_OK)` guard; `resolve_and_run` is what turns that into `STATUS: BLOCKED`). Every other
   `scripts/*.py` in this repo is non-executable (`0644`) because none of them are *resolved and
   directly exec'd* by another component the way a declared `verification.verifier`/`stop_condition`
   path is — this one specifically needs the bit set:
   ```bash
   chmod +x scripts/cost_report_marker_check.py
   git add scripts/cost_report_marker_check.py
   git update-index --chmod=+x scripts/cost_report_marker_check.py
   ```
4. Run: `python -m pytest tests/test_cost_report_marker_check.py -v` — green.
5. Commit:
   ```bash
   git add scripts/cost_report_marker_check.py tests/test_cost_report_marker_check.py
   git commit -m "feat: cost-report-marker predicate — #300 regression check-only script (#198 R6)"
   ```
   Confirm the executable bit survived the commit: `git show HEAD --stat` then
   `git ls-files -s scripts/cost_report_marker_check.py` — expect mode `100755`, not `100644`.

---

## Task 17 — Wire the predicate through #197's `verifier.py` + real `verdict_gate_check.sh` (R5, R6)

**BLOCKED until #197 merges to `main`.** Do not start this task while `scripts/factory_core/verifier.py`
and `scripts/factory_core/verdict.py` are absent from `main` — re-run the Pre-flight section's checks
(`git show main:scripts/factory_core/verifier.py`) first. If present, **re-verify every function
signature below against the merged `main` file** before writing code — the signatures were verified
against #197's implementation branch (`e852ecf`, see Pre-flight), not against what finally merged.

**Files:** `tests/test_verifier.py` (#197-owned, extended here), `tests/test_verdict_gate_check.sh`
(extended here, per #197 R9/#198 R6's own stated placement)

1. Red — add to `tests/test_verifier.py` (or create it if #197 didn't, matching its own spec's
   promised location). **Env-passing note:** `run_verifier(resolved_path, env)` takes the exact env
   dict the caller assembles — reuse Task 16's `_fixture_env`-equivalent `CLONE_DIR`-relative JSON
   fixture-file seam (`test_cli_reads_issue_num_and_clone_dir_env`'s pattern), not a dotted
   `tests.fixtures.*` import or a `PYTHONPATH`-forwarded stub module: this repo has no
   `tests/__init__.py`/`tests/fixtures/__init__.py` anywhere (only data fixtures like
   `tests/fixtures/jira/*.json` exist, no Python package there), and #197's own env contract
   (CLONE_DIR/ARTIFACTS_DIR/ISSUE_NUM/FACTORY_REPO_SLUG/LOOP_NAME) is not guaranteed to pass arbitrary
   *extra* vars like a test-only `PYTHONPATH` through untouched. `CLONE_DIR` itself, by contrast, is
   one of the four vars #197's spec explicitly commits to always forwarding — which is exactly why
   Task 16 built the fixture-file seam around it instead:

   ```python
   import os
   from pathlib import Path
   from factory_core.verifier import resolve_verifier, run_verifier, normalize_verdict

   REPO_ROOT = Path(__file__).resolve().parents[1]


   def _fixture_env(tmp_path, issue_num, comments):
       """Reuses Task 16's CLONE_DIR-relative JSON fixture seam (not a PYTHONPATH/module
       swap) — the whole point of that design (see Task 16's test-seam note) is that it
       needs nothing beyond CLONE_DIR, one of the four vars #197's own spec commits
       run_verifier to always forward, so it survives the real subprocess boundary here
       without any dependency on unverified extra-env-var forwarding behavior."""
       clone_dir = tmp_path / "clone"
       clone_dir.mkdir()
       fixture = clone_dir / ".cost_report_marker_check_test_fixture.json"
       fixture.write_text(json.dumps({"comments": comments}))
       return {"ISSUE_NUM": str(issue_num), "CLONE_DIR": str(clone_dir), "PATH": os.environ["PATH"]}


   def test_cost_report_marker_predicate_blocked_when_absent(tmp_path):
       env = _fixture_env(tmp_path, 300, [{"body": "unrelated comment"}])
       resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
       exit_code, stdout = run_verifier(resolved, env)
       verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
       assert "STATUS: BLOCKED" in verdict


   def test_cost_report_marker_predicate_passes_when_present_end_of_run(tmp_path):
       comments = [{"body": "unrelated"}, {"body": "## Cost Report\n<!-- dark-factory-cost-report -->"}]
       env = _fixture_env(tmp_path, 300, comments)
       resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
       exit_code, stdout = run_verifier(resolved, env)
       verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
       assert "STATUS: PASS" in verdict


   def test_cost_report_marker_predicate_passes_when_present_updated_in_place(tmp_path):
       """#311's own invariant: 'posted early, updated in place under the same marker'
       must PASS identically to 'posted once at run end' — the predicate checks marker
       presence, not the path that produced it. Deliberately a single comment (not the
       two-comments-marker-last shape of the prior case) so this is a structurally
       different fixture, not the same list twice."""
       comments = [{"body": "## Cost Report\n<!-- dark-factory-cost-report -->\n(updated)"}]
       env = _fixture_env(tmp_path, 300, comments)
       resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
       exit_code, stdout = run_verifier(resolved, env)
       verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
       assert "STATUS: PASS" in verdict
   ```

   Add `import json`, `import os` to this test file's imports alongside `Path`.

   **First, a probe** (not a throwaway — keep it): before relying on `run_verifier` forwarding
   `CLONE_DIR` into the child process, add and run one direct check:
   ```python
   def test_run_verifier_forwards_clone_dir_to_child_env(tmp_path):
       """Probes the actual #197 behavior this task's whole test-seam design depends
       on, rather than assuming it. If this fails, run_verifier only forwards a fixed
       whitelist that excludes a caller-supplied CLONE_DIR override — in which case
       every _fixture_env-based test above needs a different seam (e.g. writing the
       fixture file into the *real*, resolved CLONE_DIR that resolve_verifier used,
       rather than a caller-chosen override path), and this plan's design note is wrong
       and must be revised before continuing Task 17."""
       env = {"CLONE_DIR": str(tmp_path), "ISSUE_NUM": "1", "PATH": os.environ["PATH"]}
       probe = tmp_path / "probe.py"
       # #197's run_verifier raises VerifierError (verified: os.access(X_OK) guard) on
       # a path that exists but isn't executable; a shebang + the executable bit are
       # required here or this probe fails for the wrong reason (not-executable), not
       # the thing it's actually meant to test (env forwarding).
       probe.write_text(
           "#!/usr/bin/env python3\n"
           "import os, sys; sys.stdout.write(os.environ.get('CLONE_DIR', 'MISSING'))\n"
       )
       probe.chmod(0o755)
       exit_code, stdout = run_verifier(str(probe), env)
       assert str(tmp_path) in stdout
   ```
   Run this one first, in isolation: `python -m pytest tests/test_verifier.py -k forwards_clone_dir -v`.
   If it fails, stop and re-derive the fixture-passing mechanism (e.g. resolve the predicate's
   *actual* `CLONE_DIR` via `resolve_verifier`'s own contract and write the fixture file there
   instead) before writing the rest of this task's tests against a false assumption.

   (R6's third assertion — "cap class independent of predicate state" — is already covered by
   `test_cap_class_trip_independent_of_predicate_state` in Task 4, which needs nothing from #197 and
   so was moved there rather than sitting blocked in this task; not repeated here.)

   Run: `python -m pytest tests/test_verifier.py -v` — fails until #197's `verifier.py` exists
   (`ModuleNotFoundError` — expected at this stage; this red step only becomes meaningful once #197
   is merged).

2. Green — no new production code beyond Task 16's script; this task only wires the existing
   predicate through #197's already-implemented seam. `verifier.py`'s CLI has `--loop-name` (gate
   type `loop:<name>`) and **no `--gate-type` flag** (verified, see Pre-flight): use the Python API
   directly (as the tests above already do) and do not add a CLI flag — only a *later* consumer
   that specifically needs the CLI form would add `--gate-type` (spec's own stated fallback, R6).

3. Append to `tests/test_verdict_gate_check.sh` (mirroring #197's existing case structure exactly,
   see `_run` helper and Case 1–2 pattern read during research). **This closes the R6 chain
   end-to-end**: R6 requires the predicate's *actual* artifact — `normalize_verdict`'s real output,
   not a hand-authored `STATUS:` file — to be piped through the real gate. Use `$_REAL_PY3` (already
   exported at the top of this file specifically for real-interpreter calls that must bypass the
   file's own `python3` stub) to run the real `resolve_verifier`/`run_verifier`/`normalize_verdict`
   chain first, writing its genuine output to a file, then feed *that* file to the existing `_run`
   helper (which still exercises the stubbed tracker-CLI half of `verdict_gate_check.sh` itself,
   unmodified, exactly like every other case in this file):

   ```bash
   # --- Case (#198 R6): cost-report-marker predicate, REAL verifier output piped through REAL gate --
   _cost_report_verify() {
     # $1=clone_dir (with the fixture file already written) $2=out_file $3=issue_num
     # sys.path gets scripts/ itself, not the repo root, so `factory_core` resolves
     # (factory_core lives at scripts/factory_core — same arithmetic as the predicate
     # script's own get_tracker() in Task 16). ISSUE_NUM must be set explicitly: the
     # predicate's main() fails closed (exit 1 / BLOCKED) whenever it's absent or
     # non-numeric, so omitting it here would make the "real-PASS" case fail for the
     # wrong reason and make the "real-BLOCKED" case pass for the wrong reason.
     CLONE_DIR="$1" ISSUE_NUM="$3" "$_REAL_PY3" - <<PYEOF > "$2"
   import sys
   sys.path.insert(0, "${REPO_ROOT}/scripts")
   from factory_core.verifier import resolve_verifier, run_verifier, normalize_verdict
   import os
   resolved = resolve_verifier("${REPO_ROOT}", "scripts/cost_report_marker_check.py")
   exit_code, stdout = run_verifier(resolved, dict(os.environ))
   sys.stdout.write(normalize_verdict(exit_code, stdout, gate_type="stop_condition"))
   PYEOF
   }

   COST_REPORT_CLONE_ABSENT="${WORK}/cost_report_clone_absent"; mkdir -p "$COST_REPORT_CLONE_ABSENT"
   echo '{"comments": [{"body": "unrelated"}]}' \
     > "${COST_REPORT_CLONE_ABSENT}/.cost_report_marker_check_test_fixture.json"
   _cost_report_verify "$COST_REPORT_CLONE_ABSENT" "${WORK}/cost_report_blocked_real.md" "300"
   NEEDS_DISCUSSION_LABEL="true"
   RC=$(_run "${WORK}/cost_report_blocked_real.md" "300" "Stop condition (cost-report-marker)")
   [ "$RC" = "1" ] || { echo "FAIL cost-report-marker real-BLOCKED case: $RC"; cat "${WORK}/cost_report_blocked_real.md"; exit 1; }
   NEEDS_DISCUSSION_LABEL="false"

   COST_REPORT_CLONE_PRESENT="${WORK}/cost_report_clone_present"; mkdir -p "$COST_REPORT_CLONE_PRESENT"
   echo '{"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}' \
     > "${COST_REPORT_CLONE_PRESENT}/.cost_report_marker_check_test_fixture.json"
   _cost_report_verify "$COST_REPORT_CLONE_PRESENT" "${WORK}/cost_report_pass_real.md" "300"
   RC=$(_run "${WORK}/cost_report_pass_real.md" "300" "Stop condition (cost-report-marker)")
   [ "$RC" = "0" ] || { echo "FAIL cost-report-marker real-PASS case: $RC"; cat "${WORK}/cost_report_pass_real.md"; exit 1; }

   RC=$(_run "${WORK}/does_not_exist.md" "300" "Stop condition (cost-report-marker)")
   [ "$RC" = "1" ] || { echo "FAIL cost-report-marker missing-artifact case: $RC"; exit 1; }

   echo "PASS: #198 R6 cost-report-marker integration cases (real verifier output, real gate)"
   ```

   `REPO_ROOT` is already defined at this file's top (`REPO_ROOT="$(cd "$(dirname
   "${BASH_SOURCE[0]}")/.." && pwd)"`) — reused here, not redefined. Insert this block **before** the
   file's final `echo "PASS"` / summary line (check the file's actual tail — do not append after its
   own terminal success line, the same insertion-point care Task 14 required for `test_scheduler.sh`).

   Run: `bash tests/test_verdict_gate_check.sh` — green.

4. Run full regression: `python -m pytest tests/ -v && bash tests/test_scheduler.sh && bash tests/test_verdict_gate_check.sh`.
5. Commit:
   ```bash
   git add tests/test_verifier.py tests/test_verdict_gate_check.sh
   git commit -m "test: wire cost-report-marker predicate through verifier.py + real verdict_gate_check.sh (#198 R5/R6)"
   ```

---

## Non-goals (documented, not built — R9, R10, R11)

No task above builds: a `failure_behavior` vocabulary/dispatch table (R9 — every value still routes
to `trip_to_blocked` verbatim, truncated to 64 chars, already covered by Task 7/8's tests); an
advisory/blocking mode toggle for the predicate class (R10 — no live blocking call site exists to
promote); `epic_autopilot.should_advance`'s contract-input condition, `entrypoint.sh`'s Done-transition
wiring, or `error_signature.py`'s `substantive:contract_violation` class (R11 items 1/2/4 — each
explicitly deferred to a follow-up ticket once #197's verdict artifact exists on `main` and a real
predicate caller is wired). If the architect or conformance reviewer flags any of these as "missing,"
the correct response is to point at this section and R9/R10/R11 of the spec, not to add scope.

## Final verification checklist

- [ ] `python -m pytest tests/ -v` — full suite green (Task 15 + Task 17's additions)
- [ ] `bash tests/test_scheduler.sh` — 0 failures, sections B/K9/K10/V/W unmodified-and-passing, new
      section X passing. **CI does not run this file** (`.github/workflows/ci.yml` runs `pytest
      tests/` plus a fixed list of other named `.sh` files) — it must be run locally here and named in
      the PR body as the AC3 evidence (Task 14's note)
- [ ] `bash tests/test_run_record_hermetic.sh` — `OK` (CI runs it; Task 14 changes its regex)
- [ ] `bash tests/test_verdict_gate_check.sh` — 0 failures (once Task 17 lands)
- [ ] `bash -n scheduler.sh` — clean parse
- [ ] `bash smoke_gate.sh` (informational; CI is authoritative)
- [ ] No changes to `config/config.yaml`, `entrypoint.sh`, `epic_autopilot.py`, `error_signature.py`,
      `scripts/verdict_gate_check.sh`, any `gate_*` script, `deploy/**`, or `.factory/adapter.yaml`
      itself (R11/R12) — confirm with `git diff origin/main HEAD --stat` (two-dot form — the
      repo's own convention for an out-of-scope check, per `.archon/memory`) before the final push and excise
      anything unexpected
- [ ] Issue AC1 (`max_iterations: 3` halts 4th attempt, audit trail in `runs.jsonl`) — Task 4 + Task 7
- [ ] Issue AC2 (external-predicate evaluated by executing the declared check, not trusting agent
      output) — Task 16 + Task 17
- [ ] Issue AC3 (`test_scheduler*.sh` green, existing breaker behavior for factory phases unchanged)
      — Task 14 + Task 15

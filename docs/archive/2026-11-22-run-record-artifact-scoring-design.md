# Truthful outcome scoring for artifact-less `refine`/`plan` runs

**Issue:** #447

**Revised:** 2026-11-22 (operator amendment after spec gate review)

---

## Overview / Problem Statement

`scripts/factory_core/run_record.py::_compute_outcome(status, stages)` (lines 428-479)
computes the `outcome.state`/`outcome.score` written into every run's
`run-record.json`. Its one direct caller is `_compute_harness_economics`
(`run_record.py:576`), which itself has two callers: `cmd_assemble`
(`run_record.py:676`), invoked from `entrypoint.sh` after every DAG run
(`entrypoint.sh:1090` on the normal path, `entrypoint.sh:559` on the failure-trap path
with `--status failed`), and `_backfill_run_economics` (`run_record.py:822`), the
`backfill-economics` recompute of an already-written record (see R4). Its precedence
is:

1. `status != "completed"` → `failed` (score `0.0`)
2. any gate stage (`validation`/`conformance`/`review`) blocking → `blocked` (score `0.0`)
3. **zero gate stages present** → `produced_ungated` (score **`1.0`**)
4. otherwise → `delivered_clean`/`delivered_with_findings` (penalized score)

Only `implement`-shaped runs ever write `validation.md`/`conformance.md`/`review.md`
into `$ARTIFACTS_DIR` (the files `_parse_artifact_stage` turns into gate stages).
`refine` and `plan` runs never do — so *every* `refine`/`plan` run whose container
exits with status `"completed"` lands on branch 3, `produced_ungated`/`1.0`,
**regardless of whether the phase agent actually committed a spec/plan file.** A
phase agent that ends its turn with zero artifact (or a DAG node auto-closed by an
idle timeout, still reported `"completed"`) is scored identically to a real,
accepted spec/plan delivery.

This is a regression against the function's own original design intent, not just an
omission: `docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md`'s
"Outcome-score policy" table defines `produced_ungated` as triggering on "`status ==
"completed"`, zero gate stages present (e.g. a `refine`/`plan` run that **only
produced a spec/plan doc**)" — the design's own description assumed the doc existed.
The shipped code never checks for it; it only checks for the absence of gate stages,
which is true of every `refine`/`plan` run, successful or not.

**Real-world impact** (per issue body): MarketHawk #388/#441 burned six `plan` runs
this way before the scheduler's retry breaker (`scheduler.sh`'s per-issue retry
counter, capped by `max_retries` — a separate mechanism from this function that never
reads `outcome`) finally tripped. By then the economics/telemetry layer had scored
every one of those six runs `produced_ungated`/`1.0` — "successful, cheap" — giving no
signal that anything was wrong and delaying diagnosis. This ticket fixes the scoring
only; it does not touch the breaker itself (see Safety Invariants).

---

## Requirements

### R1 — Scope: `refine`/`plan` only

The fix is keyed on `args.intent in ("refine", "plan")` inside `cmd_assemble`. Every
other intent value (`fix`, `continue`, `close`, `deconflict`, `recheck`, `fix-main`,
`audit-deps`, and any future one) must produce **byte-identical**
`_compute_outcome`/`_compute_harness_economics` output to current `main`.

Which of those actually reach `cmd_assemble` with `status == "completed"`: only the
intents that run the archon workflow to its end — `fix`/`continue` (scored via real
gate stages, structurally unaffected) and `close` (no gate stages, so
`produced_ungated`/`1.0` today). `fix-main`, `audit-deps`, `recheck` and `deconflict`
all `exit 0` before the success-path assemble (`entrypoint.sh:872-1016`); they reach
`cmd_assemble` only through the failure trap with `--status failed`, which is already
`failed` and stays so. None of these intents has a single-file artifact contract or a
`push_gate_check.sh`-style check; designing one is out of scope (see Non-Goals).

A regression test must pin this: a completed `close` run with zero gate stages, and
the existing fixture intent `"new"` (`tests/test_run_record.py:621`), still score
`produced_ungated`/`1.0` after this change, exactly as before.

### R2 — New outcome state: `clean_abort`

For a `refine`/`plan` run with `status == "completed"` and zero gate stages, split
today's single `produced_ungated` branch into three outcomes based on whether a
spec/plan artifact actually exists and, if not, whether the halt was a legitimate,
already-labelled clean abort:

| Condition | `outcome.state` | `outcome.score` |
|---|---|---|
| Committed artifact found (see R3) | `produced_ungated` | `1.0` (unchanged) |
| No artifact, but `needs-discussion` is on the issue | `clean_abort` (**new**) | `0.0` |
| No artifact, and `needs-discussion` is absent (or its state could not be determined — see R4) | `failed` | `0.0` |

`clean_abort` is deliberately **not** `produced_ungated` (that would keep rewarding a
doc-less run as if it delivered a doc — exactly this ticket's bug, just now
label-gated instead of unconditional) and **not** `failed` (the issue explicitly
rules this out: "Scoring every artifact-less completion as a failure would make a
legitimate needs-discussion halt trip the breaker" — the halt was a correct decision,
not a malfunction). Its score is `0.0`, not `1.0`, because `factory_cpm = score *
1_000_000 / tokens_per_task`: scoring a doc-less halt `1.0` would make the cheapest
possible run (do nothing, label it) the best-CPM outcome in the whole system,
rewarding halting over producing — exactly the "raw token reduction over correctness"
trap `_compute_outcome`'s own docstring warns against. The distinct state name, not
the score, is what keeps a clean abort from reading as a failure.

Other outcome fields for `clean_abort`:
- **`failure_spend`** stays on the existing `retry_only` branch (the
  `_compute_harness_economics` check at line 589, `if outcome["state"] in ("failed",
  "blocked")`, is **not** extended to include `clean_abort` — choosing not to guess
  did not waste the whole run's spend).
- **`evidence`** gains the raw halt-check input (see R3/R4) under a new key, e.g.
  `evidence["artifact_check"]`, so the decision is auditable the same way
  `evidence["penalties"]` already documents the delivered-run scoring. The same input
  is also persisted as a top-level `run_record["artifact_check"]` key so a later
  backfill can replay it (R4, "Backfill keeps the verdict"). The existing
  `evidence["ungated"] = state == "produced_ungated"` line needs no change — it
  already evaluates `False` for `clean_abort` and `failed`.
- **`POLICY_VERSION`** bumps from `"1.0"` to `"1.1"` (new state, changed scoring for
  the branch it covers) so old and new `run-record.json` files stay distinguishable.
  Nothing outside `run_record.py` and its tests reads `policy_version` today; the bump
  is a marker for future readers, not a contract any existing consumer depends on.

### R3 — Artifact signal: reuse `push-result.json`, not a new mechanism

`refine-push`/`plan-push-and-advance` (`workflows/archon-dark-factory.yaml:463-587`,
`594-716`) already run `push_gate_check.sh` and write `$ARTIFACTS_DIR/push-result.json`
**only when a committed artifact was found** on the branch, before any label/comment
call (explicitly so "a comment failure or node timeout still leaves the outcome for
`run_record.py`'s `cmd_assemble`" — workflow comments at lines 517-518, 648-649: this
plumbing was built forward-compatible for exactly this ticket). `cmd_assemble`
already reads this file into `run_record["push_result"]` (lines 696-701) but never
consults it for scoring.

"Artifact found" for R2's first row is: **`push-result.json` exists in
`$ARTIFACTS_DIR`**, regardless of its `pushed` field's value. A `pushed: false` entry
(artifact committed locally, but the verified remote push failed/was unverified) is
explicitly **out of scope** for this ticket and keeps today's `produced_ungated`
scoring — that case already has its own `**Push:** FAILED` cost-report line
(`cost_report.py::format_push_line`, #473). Do not add a third branch for it here.

Correction (2026-11-22, plan gate review): the DAG nodes write `push-result.json` to
archon's per-workflow-run directory `runs/<workflow run id>`, a sibling of the
`--artifacts-dir` that `cmd_assemble` receives, so `cmd_assemble` must look there first
and fall back to `--artifacts-dir`; see plan Design decision 9.

Accepted limitation: both nodes write the file with `> "$ARTIFACTS_DIR/push-result.json"
|| true` (`workflows/archon-dark-factory.yaml:521`, `652`), so a failed write (full or
read-only artifacts dir) leaves no file even though the artifact was committed. Such a
run is then scored `failed` (or `clean_abort` if the label is present) although it
delivered. This is rare, errs strict rather than lenient, and is accepted rather than
adding a second artifact probe to `cmd_assemble`.

`push-result.json`'s existence check must be computed by `cmd_assemble` *before* it
calls `_compute_harness_economics`/`_compute_outcome` (currently the file is read
later, at lines 696-701, purely to be embedded verbatim into the output record) —
move or duplicate the `pathlib.Path(...).exists()` check earlier in `cmd_assemble` so
it can feed the outcome computation.

### R4 — Clean-abort signal: a live `needs-discussion` label lookup, fail-closed

When `push-result.json` is absent (R3's "no artifact" case) on a run whose
`args.status == "completed"`, `cmd_assemble` must determine whether `needs-discussion`
is currently on the issue, for `refine`/`plan` intents only:

- **Mechanism:** reuse the exact live lookup `refine-push`/`plan-push-and-advance`
  already run moments earlier in the same container
  (`workflows/archon-dark-factory.yaml:554-560`, `685-691`): a `tracker get --id
  <issue> --fields labels` call (via
  `dark-factory/scripts/factory_core/providers/cli.py`, the same CLI the DAG nodes
  shell out to), checking for `needs-discussion` in the returned label list. Do
  **not** introduce a second, parallel bookkeeping mechanism (e.g. a new status-marker
  file written by `.archon/commands/dark-factory-refine.md`/`-plan.md`) — that would
  bundle command-file/prompt changes into a ticket scoped to scoring only, and could
  drift out of sync with the label itself, which is already the source of truth the
  factory's own halt logic (`needs-discussion` stops all automation) relies on.
- **Call shape:** only invoked when `args.status == "completed"` **and**
  `args.intent in ("refine", "plan")` **and** `push-result.json` is absent — never on
  every run, never for other intents, and never on the failure-trap path
  (`entrypoint.sh:559`, `--status failed`): there the outcome is `failed` by the first
  precedence rule whatever the label says, so a lookup would only add a GitHub call
  and a timeout-bounded delay to `on_failure` (and to the bash tests that source
  `entrypoint.sh` and call it). Wrap the subprocess call in `try/except Exception`, mirroring `cmd_assemble`'s
  existing fallback-on-failure style for `adapter.get(args.clone_dir, "loops")`
  (`except Exception: loops = []`). Apply a bounded timeout consistent with other
  short-lived subprocess calls in this codebase (e.g. the `timeout 10`/`timeout 30`
  calls already used for `git ls-remote`/`git push` in the DAG nodes this logic
  mirrors).
- **Fail-closed on lookup error:** if the lookup raises or times out, treat the label
  as **absent** (→ `failed`), not present (→ `clean_abort`). This matches the DAG's
  own existing fail-direction: `grep -Fxc 'needs-discussion' || true` already yields
  `0` (= no label) on a `tracker get` failure in `refine-push`/`plan-push-and-advance`,
  so those nodes already treat a lookup failure as "silent death" and post the
  `df-refine-failure`-equivalent comment. A `cmd_assemble` that failed *open* to
  `clean_abort` on the same error would write a run-record that contradicts the
  failure comment the factory just posted for that same run. Record
  `evidence["artifact_check"]["label_lookup"] = "error"` on a caught exception
  (distinct from `"ok"` when the call succeeded), so telemetry can separate "failed,
  label confirmed absent" from "failed, label state unknown" without inventing a
  fourth outcome state for it.
- **Threading:** `_compute_outcome` must stay a pure function taking no subprocess/
  network dependency — it must remain directly unit-testable with plain in-memory
  arguments, exactly as `tests/test_run_record.py` exercises it today. `cmd_assemble`
  performs the artifact/label lookup itself and passes the *result* (not a live
  callable) into `_compute_outcome` via a new, optional keyword argument that
  defaults to `None`/absent — preserving every existing two-positional-argument call
  site and test byte-for-byte. `_compute_harness_economics` (the one function between
  `cmd_assemble` and `_compute_outcome`) needs the same new parameter threaded through
  its own signature and its one `_compute_outcome(...)` call site (currently
  `run_record.py:576`, positional-only today).
- **Backfill keeps the verdict:** `_backfill_run_economics` (`run_record.py:822`)
  recomputes `harness_economics` for an already-written record through the same
  `_compute_harness_economics`. Left unchanged it would pass no artifact-check input
  and silently rewrite a `failed`/`clean_abort` refine/plan record back to
  `produced_ungated`/`1.0`, now stamped `policy_version 1.1`. Therefore: `cmd_assemble`
  persists the artifact-check input as a top-level `run_record["artifact_check"]` key
  (written only when it is not `None`), and `_backfill_run_economics` passes
  `record.get("artifact_check")` through to `_compute_harness_economics`. A record
  without the key (every pre-1.1 record, and every non-refine/plan run) yields `None`,
  i.e. today's legacy scoring. Backfill never performs a label lookup of its own — it
  replays the recorded input only.

### R5 — Test coverage

- `tests/test_run_record.py`: every existing `_compute_outcome`/`_compute_harness_economics`
  test keeps passing unmodified (R4's additive-kwarg constraint). Add new unit tests,
  calling `_compute_outcome` directly with the new kwarg, for: artifact present → still
  `produced_ungated`/`1.0`; no artifact + label present → `clean_abort`/`0.0`,
  `failure_spend` basis `retry_only`; no artifact + label absent → `failed`/`0.0`; no
  artifact + lookup-error sentinel → `failed`/`0.0` with `label_lookup: "error"` in
  evidence; `intent` outside `("refine", "plan")` (or the kwarg simply omitted) →
  today's unconditional `produced_ungated`/`1.0` for zero gate stages, unchanged (R1's
  regression pin).
- Add or extend a `cmd_assemble`-level test (using `tmp_path` fixtures, matching this
  file's existing style, e.g. around `test_parse_archon_cost_basic`) covering: a
  `refine` run with `push-result.json` present; one with it absent and a mocked/faked
  label lookup returning present vs. absent vs. raising; and that the written record
  carries the top-level `artifact_check` key in each of those cases.
- Lookup-gating tests (`cmd_assemble`-level, lookup helper monkeypatched to raise if
  called at all): a `refine` run with `status = "failed"` and no `push-result.json`
  never invokes the helper and scores `failed`; a completed `close` run and a
  completed `"new"` run with zero gate stages never invoke it and score
  `produced_ungated`/`1.0` (R1's regression pin at the `cmd_assemble` level).
- Backfill tests (matching `test_backfill_economics_full_recompute`'s style): a stored
  `refine` record whose `artifact_check` says no artifact / label absent stays
  `failed` after `_backfill_run_economics`; one whose `artifact_check` says label
  present stays `clean_abort`; a record with no `artifact_check` key and zero gate
  stages stays `produced_ungated`/`1.0` (legacy records unchanged).

### R6 — Minor, same-file cleanup

`_compute_outcome`'s docstring cites `docs/superpowers/specs/2026-07-16-harness-
economics-ledger-cpm-design.md`, but that file has since moved to
`docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md` (per this repo's own
"archive completed workflow artifacts" convention). Since this ticket is already
editing this exact docstring to describe the new `clean_abort` branch, correct the
path reference in the same edit rather than filing a separate ticket for a one-line
stale citation.

---

## Architecture / Approach

**Chosen approach: extend `_compute_outcome`/`_compute_harness_economics` with an
additive, optional parameter; compute its value in `cmd_assemble` from artifacts
already on disk plus one new, narrowly-scoped live label lookup.** This is the
smallest change that reuses 100% of the existing plumbing identified in Requirements
R3/R4 rather than inventing a new one.

```
cmd_assemble (entrypoint.sh calls this once per run, after the whole DAG):
  push_path = artifacts_dir / "push-result.json"
  refine_plan_halt = None
  if args.intent in ("refine", "plan"):
      if push_path.exists():
          refine_plan_halt = {"artifact_present": True}
      elif args.status == "completed":                # never on the failure-trap path
          needs_discussion, lookup_ok = _check_needs_discussion(args.issue, args.clone_dir)  # new helper
          refine_plan_halt = {
              "artifact_present": False,
              "needs_discussion": needs_discussion,     # True / False — False also covers lookup failure
              "label_lookup": "ok" if lookup_ok else "error",
          }
      # status != "completed" and no file: stays None — outcome is "failed" by status anyway

  if refine_plan_halt is not None:
      run_record["artifact_check"] = refine_plan_halt  # persisted so backfill can replay it
  run_record["harness_economics"] = _compute_harness_economics(
      ..., refine_plan_halt=refine_plan_halt,
  )

_backfill_run_economics (recompute of a stored record — no lookup, replay only):
  record["harness_economics"] = _compute_harness_economics(
      ..., refine_plan_halt=record.get("artifact_check"),   # absent -> None -> legacy scoring
  )
      │
      ▼
_compute_harness_economics(..., refine_plan_halt=None):
  outcome = _compute_outcome(status, stages, refine_plan_halt=refine_plan_halt)
  ...
  if outcome["state"] in ("failed", "blocked"):          # UNCHANGED — clean_abort
      failure_spend = {"tokens": tokens_per_task, "basis": "whole_run"}  # not added here
  else:
      failure_spend = {"tokens": retry_spend["tokens"] or 0, "basis": "retry_only"}
      │
      ▼
_compute_outcome(status, stages, *, refine_plan_halt=None):
  ...
  elif not gate_stages:
      if refine_plan_halt is None or refine_plan_halt.get("artifact_present"):
          state = "produced_ungated"                      # today's behavior, default-preserving
      elif refine_plan_halt.get("needs_discussion"):
          state = "clean_abort"                            # NEW
      else:
          state = "failed"                                 # NEW: artifact-less, unlabelled refine/plan
  ...
  if state in ("failed", "blocked", "clean_abort"):
      score = 0.0
  elif state == "produced_ungated":
      score = 1.0
  else:
      ...  # delivered_clean / delivered_with_findings, unchanged
```

The exact helper name/shape for the label lookup (`_check_needs_discussion` above) and
the precise `evidence` dict keys are implementation detail for the plan phase to pin
down; the constraints that matter are: `_compute_outcome` stays a pure function of
its arguments (R4), the new parameter is optional and additive (R1/R4), and the
precedence order — `status != "completed"` first, then blocking gate stages, then
this new refine/plan artifact check, only inside the already-existing "zero gate
stages" branch — is unchanged from today.

**Call sites unaffected:** `entrypoint.sh`'s two `run-record assemble` invocations
(success path `:1090`, failure path `:559`) pass no new flags — `cmd_assemble`
derives `refine_plan_halt` entirely from `args.intent`/`args.status` (already passed)
and `args.artifacts_dir`/`args.issue`/`args.clone_dir` (already passed). No workflow YAML,
`entrypoint.sh`, `scheduler.sh`, or `config/config.yaml` changes are required.

---

## Safety Invariants (hard requirements)

This ticket is explicitly flagged in the issue body as "Breaker/scoring-sensitive."
None of the following may change as a side effect of this fix (CLAUDE.md hard
limits: never weaken a `gate_*`/breaker/budget as a side effect of another change):

1. **The scheduler's retry breaker is untouched.** `scheduler.sh`'s per-issue retry
   counter and `scripts/factory_core/breaker.py::trip_to_blocked` never read
   `outcome.state`/`outcome.score` today (confirmed: no reference to `produced_ungated`,
   `_compute_outcome`, or `run_record` anywhere in `breaker.py`) and must not gain one
   as part of this ticket. This fix changes *telemetry/economics labeling only* — it
   cannot alter when a ticket retries or trips to Blocked.
2. **The DAG-level clean-abort exemption (label application, comments, gate labels)
   is unchanged.** `refine-push`/`plan-push-and-advance`'s own `needs-discussion`
   check, `spec-pending-review`/`plan-pending-review` application, and
   `df-refine-failure`/push-failure comments are read, never written, by this change.
   This ticket adds a *second, independent* reader of the same label for scoring
   purposes; it must not alter the DAG nodes' own behavior or timing.
3. **No `config/config.yaml` knob changes.** No new budget, ceiling, gate, or
   autopilot key is introduced or altered.
4. **Every non-`refine`/`plan` intent's `_compute_outcome`/`_compute_harness_economics`
   output is byte-identical to pre-change `main`** (R1's regression test).
5. **Every existing `_compute_outcome`/`_compute_harness_economics` call site and unit
   test keeps working unmodified** — the new parameter is optional and additive, never
   a required positional argument (R4).
6. **The new live label lookup fails closed, never open, and cannot hang
   indefinitely** — a lookup error or timeout must map to `failed`, never silently to
   `clean_abort` or `produced_ungated`, and must not block `cmd_assemble` beyond a
   bounded timeout (R4). It runs only for completed `refine`/`plan` runs with no
   `push-result.json` — never inside the failure trap.
7. **Session-window-paused runs bypass `cmd_assemble` and are unaffected.** Both pause
   paths return before any `run-record assemble` call (`entrypoint.sh:1031-1034` in the
   run loop, `entrypoint.sh:507-537` in `on_failure`), writing only a `paused` stage
   event. A paused `refine`/`plan` run therefore cannot be rescored `failed` by this
   change.
8. **A backfill never changes a recorded verdict.** `_backfill_run_economics` replays
   the stored `artifact_check` input and performs no lookup; a `failed`/`clean_abort`
   record stays so, and a record without the key keeps legacy scoring (R4).

---

## Non-Goals

- **Breaker trip reason for silent deaths is unchanged.** The issue's suggested
  direction also wants the breaker to be able to report "agent ended its turn without
  committing". This spec does not deliver that: the trip reason for an artifact-less
  `refine`/`plan` loop is still `retry limit of N reached` (`scheduler.sh:1290`,
  `1357`). Changing what the breaker reads or says is a breaker change and needs its
  own reviewed ticket (CLAUDE.md hard limits); a follow-up ticket is required.
- **No artifact contract for other intents.** `close`, `deconflict`, `recheck`,
  `fix-main` and `audit-deps` keep today's scoring (R1).
- **`pushed: false`** (artifact committed, push unverified) keeps `produced_ungated`
  (R3).

---

## Alternatives Considered

- **Generalize to "every ungated intent must prove its artifact."** Rejected (product
  owner decision, R1): `close`/`deconflict`/`fix-main`/`audit-deps` have no single-file
  artifact contract and no existing push-gate check; designing one per intent would be
  guesswork about each intent's own success definition, not a bug fix, and the
  issue/comments explicitly favor the smallest correct, non-bundled change.
- **Keep the artifact-less-but-labelled case as `produced_ungated`/`1.0` (no new
  state).** Rejected: perpetuates this exact bug's mislabeling (a doc-less run scored
  as if it produced a doc), just now gated by label presence instead of applying
  unconditionally — defeats the purpose of fixing "truthful outcome scoring."
- **Score the artifact-less-but-labelled case as `failed`/`0.0` (no new state).**
  Rejected — explicitly ruled out by the issue body: "Scoring every artifact-less
  completion as a failure would make a legitimate needs-discussion halt trip the
  breaker."
- **Score `clean_abort` as `1.0` (reward correctly recognizing a dead end).**
  Rejected: `factory_cpm = score * 1e6 / tokens_per_task` would make the cheapest
  possible run (halt immediately, apply a label) the best-CPM outcome in the system,
  rewarding halting over producing — the same "raw token reduction over correctness"
  trap the existing scoring policy's docstring already warns against avoiding.
- **New local status-marker file (written by the refine/plan command files) instead
  of a live label lookup.** Rejected (R4): avoids a live API call, but requires
  editing `.archon/commands/dark-factory-refine.md`/`-plan.md` (bundling
  prompt/behavior changes into a scoring-only ticket) and introduces a second,
  parallel "did we clean-abort" record that can drift out of sync with the
  `needs-discussion` label, which is already the authoritative signal the rest of the
  factory relies on.
- **Fail open to `clean_abort` on a label-lookup error.** Rejected (R4): would let a
  transient GitHub API/rate-limit error quietly resurrect this exact bug (a silent
  death scored as a non-failure) and would contradict the failure comment
  `refine-push`/`plan-push-and-advance` already post for the same run when their own
  identical lookup fails.
- **Give `_compute_outcome` the subprocess/network dependency directly (have it call
  the tracker itself).** Rejected: breaks the function's existing pure-function
  contract, which `tests/test_run_record.py` relies on for direct, I/O-free unit
  testing; all I/O stays in `cmd_assemble`, consistent with its existing role as the
  only place in this module that touches the filesystem/network.

---

## Open Questions (non-blocking)

- Exact field names inside the new `refine_plan_halt`/`evidence["artifact_check"]`
  dicts (R2-R4's architecture sketch) are illustrative; the plan/implement phases may
  adjust naming as long as the state/score/evidence semantics in R2-R4 hold and
  `_compute_outcome` stays a pure, additively-extended function.
- Whether `cost_report.py::format_economics_line` or any dashboard/report also wants
  an explicit human-readable caption for `clean_abort` (today it would render
  mechanically as `**Outcome:** clean_abort (score 0.0)`, which is already truthful
  and requires no code change) is left to the plan phase's judgment — no change is
  required by this spec.

## Assumptions (flagged)

- `docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md` is a historical
  snapshot (per this repo's archive convention) and is **not** retroactively edited by
  this ticket to document `policy_version 1.1`/`clean_abort`; this spec is the
  authoritative design reference for the new behavior going forward. Only the stale
  path *citation* inside `_compute_outcome`'s own docstring is corrected (R6), not the
  archived document's content.
- The live `tracker get --fields labels` lookup this ticket adds to `cmd_assemble` is
  assumed to carry the same authentication/rate-limit characteristics as the
  identical call `refine-push`/`plan-push-and-advance` already make in the same
  container moments earlier — no new credentials or API scope are required.
- "`push-result.json` exists" is read as sufficient evidence of "an artifact was
  committed" regardless of its `pushed` field, per R3; this is a deliberate
  simplification agreed with the product owner, not an oversight of the `pushed:
  false` case (which is explicitly out of scope / already separately surfaced by
  #473's `Push: FAILED` line).

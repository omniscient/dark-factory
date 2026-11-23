# Add `substantive:verdict_missing` Classification and a Conformance Artifact Guard

**Issue:** #373

---

## Overview / Problem Statement

When the `conformance` DAG node (`commands/dark-factory-conformance.md`) ends its turn
without ever writing `$ARTIFACTS_DIR/conformance.md`, the downstream `conformance-gate`
node (`scripts/verdict_gate_check.sh`) correctly blocks the run — but `entrypoint.sh`'s
failure classifier (`scripts/factory_core/error_signature.py::classify()`) frequently
mislabels *why* it blocked. Three real occurrences on this repo (run
`6788a7c07570490bb11aa48f4c501d07` / issue #197; issue #384; issue #436) show the
pattern: the conformance phase's reviewer subagent hits a transient hiccup (a
prompt-truncation error that triggers a relaunch, or an unparseable extraction), the
phase ends cleanly without a verdict, and `verdict_gate_check.sh` fails the run with its
existing, already-loud "No verdict was recorded for this run" comment and `exit 1`. The
gate itself is not the bug. The bug is two-fold:

1. **Misclassification.** `classify()` receives the *entire* captured
   `archon workflow run` transcript (every node's stdout+stderr, concatenated — not
   just the failing node's output; verified in `entrypoint.sh`'s `TMP_OUT` capture and
   `_write_error_signature()`). It checks `_PREVIEW_INFRA_RE`/`_RATE_LIMIT_RE`
   (environmental) before any substantive regex, and today has **no regex at all** for a
   missing/unparseable gate verdict. In #197, an earlier, non-fatal "Preview environment
   failed to start" warning (present in every self-target run — there is no backend to
   build) matched `_PREVIEW_INFRA_RE` first, so the run was signed
   `environmental:preview_infra` even though the terminal, run-ending failure was the
   missing conformance verdict. This matters operationally:
   `scripts/factory_core/breaker.py::record_failure_signature()` only trips the circuit
   breaker early (`stuck=true` → immediate `trip_to_blocked`) when two consecutive
   signatures both start with `"substantive:"` and match exactly — `environmental:*`
   never trips it. A repeated verdict-missing defect was therefore invisible to the
   breaker and kept consuming retry budget as if it were an infrastructure blip.
2. **No earlier, targeted signal from the originating phase.** `verdict_gate_check.sh`
   already fails loudly and posts a comment today — that part works. But the only
   thing that catches "the reviewer subagent errored with no usable response at all, and
   the whole phase ended without a verdict" is the generic downstream gate, one DAG hop
   later, with a generic message. The conformance command's own instructions (Phase 3
   step 7, shipped by #394) already treat a parseable-but-bad response (unparseable
   verdict line, a refusal, error text as the response body) as a Material divergence
   and route it through the reconcile loop — which re-spawns the reviewer and, within
   `max_reconcile_cycles`, always ends by writing `conformance.md` (PASS in Phase 4 or
   BLOCKED in Phase 5). That path is not the gap. The gap is the case where the Agent
   tool call itself throws/times out with **no response text at all**, or the
   orchestrating phase's turn ends for some other reason before Phase 4/5 ever runs —
   i.e. nothing in the command file's own instructions catches this today, and the phase
   relies entirely on the downstream gate to notice.

## Requirements

(From the issue body and brainstorming Q&A.)

1. `error_signature.py::classify()` gains a new regex check, `_VERDICT_MISSING_RE`,
   checked **before** `_PREVIEW_INFRA_RE` and `_RATE_LIMIT_RE`, matching the stable
   wording `scripts/verdict_gate_check.sh` already emits on its "true silent death"
   path (`verdict missing/unparseable for issue #...` / `No verdict was recorded for
   this run`). A match returns the literal string `"substantive:verdict_missing"`.
2. Add a short cross-reference comment in both `error_signature.py` (above
   `_VERDICT_MISSING_RE`) and `verdict_gate_check.sh` (above the "true silent death"
   log/comment text) noting that the classifier's regex depends on this exact wording,
   so a future rewording of one is a deliberate, cross-checked edit, not a silent break.
3. A new regression test in `tests/test_factory_core_error_signature.py` reproducing the
   #197 transcript shape: a text blob containing both the benign preview-infra warning
   *and*, later, the verdict-missing failure text → `classify()` must return
   `"substantive:verdict_missing"`, not `"environmental:preview_infra"`.
4. Document (code comment in `error_signature.py`, near `SESSION_WINDOW_PAUSE_SIGNATURE`)
   that this new regex's precedence is deliberately safe against the session-window-pause
   path: `entrypoint.sh::_handle_session_window_pause()` runs *before* `classify()` is
   ever invoked and, on a genuine match, writes `environmental:session_window_pause`
   directly via a separate CLI subcommand, bypassing `classify()` entirely (see
   `error_signature.py:28-34`). So a real session-window-pause event never reaches this
   new regex in the first place; no ordering conflict exists between the two paths today.
   This note exists so a future reader doesn't "fix" an imagined race.
5. `commands/dark-factory-conformance.md` Phase 3.5 (RECONCILE LOOP) step 4 gets one new
   conditional sub-bullet: when the current cycle was triggered by a tool error, timeout,
   or empty/no-response result from the reviewer subagent (not a parseable finding to act
   on), skip the code-fix and re-diff steps (today's steps 4–5) and go straight to
   re-spawning the reviewer subagent (step 6) with the identical prompt — still consuming
   one of `CONFORMANCE_CYCLE`'s `max_reconcile_cycles`. This is the "retry the reviewer
   subagent once" behavior the issue asks for, implemented as a refinement of the
   *existing* reconcile loop rather than a second, separately-budgeted retry mechanism at
   the Phase 3 step 4 spawn site (see Alternatives Considered).
6. A new, unconditional final section, **Phase 6: ARTIFACT GUARD**, appended after
   today's Phase 5 body (so it is the last instruction in the file regardless of which
   phase branch executed):
   - Check: `$ARTIFACTS_DIR/conformance.md` exists AND has a `^STATUS:` line
     (`test -f ... && grep -q '^STATUS:' ...`).
   - If the check passes (a real Phase 4 PASS or Phase 5 BLOCKED verdict is already
     there), do nothing — this step must never overwrite an existing verdict.
   - If the check fails, write `STATUS: BLOCKED` (never `STATUS: ERROR` — `ERROR` is
     code-review's documented fail-open token and must not leak into conformance's
     stricter contract) with `GATE_TYPE: conformance`, `FINDINGS_COUNT: 1`,
     `SEVERITY: critical`, and a `REASON:` line naming the missing artifact path
     explicitly, to `$ARTIFACTS_DIR/conformance.md`; print an explicit stderr message
     naming the same path; and `exit 1`. Note, mirroring Phase 5 step 5's existing
     language, that a `command:` node's internal `exit 1` does not reliably surface to
     the DAG executor on its own (#212, #271) — `conformance-gate` is still the real
     enforcement, now reading a substantive `BLOCKED` artifact instead of a wholly
     absent one.
   - This is an explicit, best-effort safety net, not a 100% technical guarantee against
     an LLM turn ending before any instruction in the file executes — see Assumptions.
     Requirement 1 (the classifier fix) is the real backstop for that residual case: even
     when the guard itself never runs, `verdict_gate_check.sh`'s existing missing-verdict
     message still lands in the transcript and is now classified correctly.
7. A new test (style matching `tests/test_conformance_command_shadow_review.py`'s
   grep-based static content assertions over `commands/dark-factory-conformance.md`)
   asserting: Phase 6 exists, follows Phase 5 in file order, writes `STATUS: BLOCKED`
   (not `ERROR`) on the missing-artifact branch, and the Phase 3.5 step 4 conditional
   sub-bullet text exists ahead of the existing "Fix the code to align with the spec"
   step.
8. No change to `scripts/verdict_gate_check.sh`'s behavior, `breaker.py`, or
   `workflows/archon-dark-factory.yaml` — the gate's block/pass decision is unchanged;
   only the classification of *why* it blocked, and how early the originating phase
   itself can say so, change.

## Architecture / Approach

**Part A — Classifier (`scripts/factory_core/error_signature.py`):**

Add one new compiled regex and one new `if` branch in `classify()`, positioned first
(before `_PREVIEW_INFRA_RE`/`_RATE_LIMIT_RE`):

```python
_VERDICT_MISSING_RE = re.compile(
    r"verdict (?:is )?missing/unparseable|No verdict was recorded for this run",
    re.IGNORECASE,
)
```

placed ahead of the `delivery_failure` conjunction's result (unchanged — that check
stays first; it is about failures *too fast* to have produced any output worth
regex-matching) and ahead of `_PREVIEW_INFRA_RE`. This is a pure regex-chain insertion,
not a restructuring of `classify()`'s control flow.

**Part B — Conformance command (`commands/dark-factory-conformance.md`):**

Two surgical edits, both additive:

- Phase 3.5 step 4 gains a leading conditional that branches on *why* this cycle was
  entered (tool error/timeout/empty response vs. an actual parsed Material-divergence
  finding) before deciding whether to attempt a code fix at all.
- A new Phase 6 section, unconditionally reached last.

Neither edit touches Phase 3 step 4's reviewer-subagent spawn itself, Phase 3 step 7's
verdict-parsing logic, or the shadow-spawn machinery (3.1 step 5a, 3.5 step 7a) — all of
that is unchanged and already correct per #394.

## Alternatives Considered

1. **A second, separate retry at the Phase 3 step 4 spawn site** (re-invoke the Agent
   tool call immediately, before even reaching step 5's parsing), kept distinct from the
   reconcile loop. Rejected: this would create two retry policies with independent
   budgets for what is fundamentally the same failure class (reviewer subagent didn't
   produce a usable verdict this cycle), making the phase harder to reason about and to
   test, for no behavior the existing reconcile loop doesn't already provide once
   Requirement 5's conditional is added.
2. **Match a new, structured machine-readable marker instead of `verdict_gate_check.sh`'s
   existing prose.** Considered for robustness against future wording changes, but
   rejected for this ticket's S–M scope: it would require `verdict_gate_check.sh` to emit
   a new marker on a path that already works correctly (it already fails loudly and
   posts a comment) purely to make the classifier's job easier. Requirement 2's
   cross-reference comment is the lighter-weight mitigation for the same risk.
3. **Generalize the Phase 6 artifact guard to `dark-factory-code-review.md` and
   `dark-factory-docs-conformance.md` too.** Those commands have their own distinct
   fail-open/observe-mode contracts (`code_review.fail_open`, `docs_conformance.mode`)
   that a copy-pasted BLOCKED-only guard would not fit without its own design pass.
   Out of scope here; flagged as a candidate follow-up only if this pattern proves
   useful in practice.

## Follow-ups (explicitly out of scope for this ticket)

Raised by the 2026-09-16 MarketHawk (#439) issue comment, describing a *different* root
cause (an SDK rate-limit rejection mid-node, misleadingly surfaced as `"Node
'conformance' failed: SDK returned success"`) that happens to end in the same
`STATUS=missing` symptom:

- Classify an SDK error result during a phase node as
  `environmental:session_window_pause` when the underlying rate-limit event says
  `rejected`, widening `entrypoint.sh::_handle_session_window_pause()`'s existing,
  separately-gated (#344) evidence check. This is its own reviewed ticket — it touches
  session-window pause semantics, not this ticket's regex ordering or verdict-writing
  steps.
- Push the implementation branch before the conformance gate runs (or on any
  post-implement failure), so a gate death never loses finished work — DAG-sequencing
  work in `workflows/archon-dark-factory.yaml`, following the same pattern as #409's
  plan checkpoint. A different code path (push-and-pr ordering) than anything this
  ticket touches.

Issue comments #384 and #436 are additional real-world evidence supporting this
ticket's Requirement 5/6 (a passing — or silently-lost — conformance review should not
cost a full pipeline re-run); they introduce no additional scope.

## Open Questions (non-blocking)

- Whether `_VERDICT_MISSING_RE`'s wording should eventually be shared verbatim (e.g. via
  a small constants module) between `error_signature.py` and `verdict_gate_check.sh`
  instead of being duplicated prose kept in sync by comment convention. Deferred: no
  other `environmental:*`/`substantive:*` regex in this file shares a constant with its
  origin script today, so doing it only here would be an inconsistent one-off.

## Assumptions (flagged)

- The Phase 6 artifact guard is a best-effort mitigation, not a guarantee: if the
  orchestrating agent's turn is killed externally (process OOM, container timeout,
  `idle_timeout` on the DAG node) before reaching Phase 6, the guard cannot run, and
  `conformance.md` stays absent exactly as today. Requirement 1 (classifier fix) is the
  backstop that still makes the resulting failure classify correctly even in that
  residual case, since `verdict_gate_check.sh`'s own message still reaches `TMP_OUT`
  regardless of what the conformance phase itself managed to write.
- This spec assumes `conformance.max_reconcile_cycles` (default 3) remains the shared
  budget for both "material divergence found" and "tool error/empty response" cycles
  per Requirement 5/Alternative 1 — no config change is introduced.

# Add `substantive:verdict_missing` Classification and a Conformance Artifact Guard

**Issue:** #373

Revised: 2026-11-23 (operator amendment after spec gate)

2026-11-23 second operator amendment after plan gate: classifier also matches the gate's
quiet-branch STATUS=missing line

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
   missing/unparseable gate verdict. In #197, the degraded-preview path that every
   self-target run takes (there is no backend to build) left earlier, non-fatal output in
   the transcript that matched `_PREVIEW_INFRA_RE` first, so the run was signed
   `environmental:preview_infra` even though the terminal, run-ending failure was the
   missing conformance verdict. (Precision note: the human-facing sentence "Preview
   environment failed to start" is only posted as an issue comment by
   `workflows/archon-dark-factory.yaml`'s `preview_fail()` and is **not** itself an
   alternative of `_PREVIEW_INFRA_RE`; what matched was a toolchain token the regex does
   list, e.g. `docker compose` / `buildkit` / `failed to solve`. The regression fixture in
   Requirement 3 must therefore use a token the regex really matches.) This matters
   operationally:
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
   BLOCKED in Phase 5). That path is not the gap. The gap is two cases nothing in the
   command file's own instructions catches today:
   - the Agent tool call itself throws/times out with **no response text at all**; and
   - the orchestrating agent ends its turn while the reviewer subagent is still running.
     This recurred on issue #580 (run `23860f3dc5a756b4e64ef2ab7a4b3154`, 2026-11-23): the
     conformance agent printed "Verdict: Conforms" and ended its turn while the Opus
     reviewer and the Fable shadow were both still "running in the background", so
     `conformance.md` was never written and `verdict_gate_check.sh` hard-blocked a likely
     passing review. The container CLI's Agent tool launches subagents in the background
     by default unless `run_in_background: false` is passed, and none of the command's
     four reviewer dispatch sites passes it. Turn end = process end (CLAUDE.md), so the
     pending reviewer work is destroyed. Requirement 8 closes this path at its source;
     reclassifying it after the fact (Requirement 1) is not enough on its own.

## Requirements

(From the issue body and brainstorming Q&A.)

1. `error_signature.py::classify()` gains a new regex check, `_VERDICT_MISSING_RE`,
   matching the stable line `scripts/verdict_gate_check.sh` already writes to stderr on
   its "true silent death" path (`verdict_gate_check.sh: <gate label> verdict
   missing/unparseable for issue #<n> ...`). A match returns the literal string
   `"substantive:verdict_missing"`. The final check order in `classify()` is exactly:
   1. the `delivery_failure` conjunction (unchanged, stays first);
   2. `_RATE_LIMIT_RE` → `environmental:rate_limit` (moved ahead of the preview check);
   3. `_VERDICT_MISSING_RE` → `substantive:verdict_missing` (new);
   4. `_PREVIEW_INFRA_RE` → `environmental:preview_infra`;
   5. the existing substantive regexes and the `unknown` fallback (unchanged).

   So `verdict_missing` outranks the preview-infra warning that did not stop the run (the
   #197 bug) but stays **below** rate-limit: a rate-limit death that ends in a missing
   verdict remains environmental and is never counted toward the breaker's
   two-consecutive-substantive trip (operator decision, 2026-11-23). The regex is anchored
   on the gate script's own log prefix so that a transcript which merely quotes the phrase
   (a run that `cat`s the gate script, or this ticket's own implementation run) cannot be
   mis-signed. The issue-comment sentence "No verdict was recorded for this run" is
   written to a temp file and posted to the tracker, not printed, so it is deliberately
   **not** an alternative of the regex.
   Under the same `verdict_gate_check.sh: ` prefix the regex **also** matches the gate's
   quiet-branch line when, and only when, it reports a missing verdict
   (`verdict_gate_check.sh: <gate label> blocks issue #<n> (STATUS=missing); ...`, printed
   when the verdict is absent and `needs-discussion` is present — the shape run #197 most
   likely took, and the `STATUS=missing` trigger the issue body names). A quiet-branch
   line reporting `STATUS=BLOCKED` (a real reviewer verdict) must **not** match. This is
   classifier-only: `verdict_gate_check.sh`'s behavior is unchanged.
2. Add a short cross-reference comment in both `error_signature.py` (above
   `_VERDICT_MISSING_RE`) and `verdict_gate_check.sh` (above the "true silent death"
   `echo`) noting that the classifier's regex depends on this exact wording. The comment
   alone is not the enforcement: a new pytest in
   `tests/test_factory_core_error_signature.py` reads `scripts/verdict_gate_check.sh`,
   extracts that `echo` line, substitutes `${GATE_LABEL}` → `Conformance (Gate 2)` and
   `${ISSUE_NUM}` → a number, and asserts `_VERDICT_MISSING_RE` matches the result — so a
   rewording of either side fails CI instead of silently breaking the classification.
   A second coupling test does the same for the quiet-branch `echo`: rendered with
   `${STATUS:-missing}` → `missing` it must match, rendered with `BLOCKED` it must not.
   This is a comment-only edit to `verdict_gate_check.sh`; its behavior is unchanged.
3. A new regression test in `tests/test_factory_core_error_signature.py` reproducing the
   #197 transcript shape. The fixture's early "benign preview" text must contain a token
   that `_PREVIEW_INFRA_RE` really matches (e.g. `docker compose`); the test first asserts
   that this text **alone** classifies as `"environmental:preview_infra"` (proving the
   fixture exercises the ordering), then asserts that the same text followed by the gate
   line classifies as `"substantive:verdict_missing"`. A negative test asserts that the
   bare phrase without the `verdict_gate_check.sh:` prefix (e.g. a quoted `${GATE_LABEL}
   verdict missing/unparseable for issue #${ISSUE_NUM}` source line) does **not** classify
   as `verdict_missing`.
   One further fixture covers the quiet branch: preview text followed by the
   quiet-branch `STATUS=missing` line classifies as `"substantive:verdict_missing"`, and
   the same preview text followed by a quiet-branch `STATUS=BLOCKED` line stays
   `"environmental:preview_infra"` (negative test — today's behavior for a real BLOCKED
   verdict is unchanged).
4. A new test in the same file: a transcript containing both a token `_RATE_LIMIT_RE`
   matches (reuse the string an existing rate-limit test in this file already uses) and
   the gate line classifies as `"environmental:rate_limit"`, in either textual order. A
   companion test asserts a transcript with a rate-limit token and a preview-infra token
   (no gate line) classifies as `"environmental:rate_limit"`, pinning the one intentional
   reordering of the two pre-existing environmental checks; both are `environmental:*`, so
   the breaker treats them identically.
5. Document (code comment in `error_signature.py`, near `SESSION_WINDOW_PAUSE_SIGNATURE`)
   that this new regex's position is deliberately safe against the session-window-pause
   path: `entrypoint.sh::_handle_session_window_pause()` runs *before* `classify()` is
   ever invoked and, on a genuine match, writes `environmental:session_window_pause`
   directly via a separate CLI subcommand, bypassing `classify()` entirely (see
   `error_signature.py:28-34`). So a real session-window-pause event never reaches this
   new regex in the first place; no ordering conflict exists between the two paths today.
   This note exists so a future reader doesn't "fix" an imagined race.
6. Verify the signal path end to end rather than assuming it. Requirement 1 only helps if
   the gate script's stderr line actually lands in the text `classify()` receives
   (`entrypoint.sh`'s `TMP_OUT`, the `tee` of `archon workflow run ... 2>&1`). Two parts:
   - Add a case to `tests/test_entrypoint_error_signature.sh` (already listed per-file in
     `.github/workflows/ci.yml`) that feeds a text file containing a preview-infra token
     followed by the gate line through the entrypoint's signature-writing path and asserts
     the written `.sig` carries `substantive:verdict_missing`. This `.sh` test runs in CI
     only; it cannot run on the Windows host.
   - That test still cannot prove that the Archon executor forwards a `bash:` node's
     stderr to its own stdout/stderr. This remains an **explicit assumption** (see
     Assumptions). The plan must include a verification step: inspect the captured
     container log of a real failed run for a line beginning `verdict_gate_check.sh:` and
     record the result in the PR description. If the line is absent, the implementer must
     stop and report rather than ship a classifier that can never fire; changing how the
     gate node surfaces its message is a gate change needing its own ticket.
7. `commands/dark-factory-conformance.md` Phase 3.5 (RECONCILE LOOP) step 4 gets one new
   conditional sub-bullet: when the current cycle was triggered by a tool error, timeout,
   or empty/no-response result from the reviewer subagent (not a parseable finding to act
   on), skip the code-fix and re-diff steps (today's steps 4–5) and go straight to
   re-spawning the reviewer subagent (step 6) with the identical prompt — still consuming
   one of `CONFORMANCE_CYCLE`'s `max_reconcile_cycles`. This is the "retry the reviewer
   subagent once" behavior the issue asks for, implemented as a refinement of the
   *existing* reconcile loop rather than a second, separately-budgeted retry mechanism at
   the Phase 3 step 4 spawn site (see Alternatives Considered).
8. Foreground reviewer dispatch (closes the #580 path). At each of the four reviewer
   dispatch sites in `commands/dark-factory-conformance.md` — Step 3.1 step 4 (Opus
   reviewer), Step 3.1 step 5a (shadow), Phase 3.5 step 6 (Opus re-spawn) and Phase 3.5
   step 7a (shadow re-spawn) — add explicit text that:
   - the Agent tool call passes `run_in_background: false`;
   - the reviewer's response text is required before any later step executes;
   - if the tool returns only a launch acknowledgement instead of the review, the agent
     polls inside its turn (keeps issuing tool calls) until the response arrives — it
     never ends the turn to wait;
   - the agent never states a verdict, and never enters Phase 4 or Phase 5, without the
     step-4 (Opus) response text in hand for the current cycle.

   For the two shadow sites the existing non-gating contract is unchanged: a shadow tool
   error, timeout or refusal still degrades to `SHADOW_STATUS: UNCERTAIN` and never blocks
   or retries the Opus verdict handling; the new text only requires that the shadow call
   has *returned* (result or error) before the turn can end. No edit is made inside the
   `<!-- headless-contract:begin/end -->` block. No verdict is defaulted or synthesized
   anywhere by this requirement.
9. A new, unconditional final section, **Phase 6: ARTIFACT GUARD**, appended after
   today's Phase 5 body (so it is the last instruction in the file regardless of which
   phase branch executed). It **never writes a `STATUS:` line of its own**:
   - Check: `$ARTIFACTS_DIR/conformance.md` exists AND has a `^STATUS:` line
     (`test -f ... && grep -q '^STATUS:' ...`).
   - If the check passes (a real Phase 4 PASS, Phase 5 BLOCKED or Phase 1 SKIPPED verdict
     is already there), do nothing — this step must never overwrite an existing verdict.
   - If the check fails and a reviewer dispatch is still pending, wait for it per
     Requirement 8 first.
   - If the check fails and the step-4 (Opus) reviewer response for the current cycle is
     in hand, go back and execute Phase 4 or Phase 5 **now**, as that response's verdict
     dictates under Phase 3 step 7's existing rules, then re-run the check. The verdict
     written is the reviewer's real one, produced by the existing Phase 4/5 code — Phase 6
     adds no new way to reach PASS.
   - Otherwise (no usable reviewer response exists and the reconcile budget cannot
     produce one), print an explicit stderr message naming the missing artifact path,
     **leave the file absent**, and `exit 1`. `verdict_gate_check.sh`'s existing "true
     silent death" path then fires unchanged: it posts the `df-push-gate-failure` comment
     and emits the log line Requirement 1 classifies.

   Rationale for never synthesizing: a guard-written `STATUS: BLOCKED` would route the
   gate down its quiet branch (`verdict_gate_check.sh:63-66`, "upstream already
   communicated, no comment needed"), suppressing the only issue comment and emitting a
   log line `_VERDICT_MISSING_RE` does not match — re-creating the silent,
   mis-classified failure this ticket exists to fix. This section is a best-effort
   safety net, not a guarantee against a turn ending before it executes — see
   Assumptions.
10. A new test file (style matching `tests/test_conformance_command_shadow_review.py`'s
    grep-based static content assertions over `commands/dark-factory-conformance.md`)
    asserting:
    - Phase 6 exists and follows Phase 5 in file order;
    - the Phase 6 section contains no `STATUS:` write (no `emit_verdict` call and no
      literal `STATUS: BLOCKED` / `STATUS: PASS` / `STATUS: ERROR` text) and does contain
      the "leave the file absent" instruction — i.e. Phase 6 never synthesizes a verdict;
    - the Phase 3.5 step 4 conditional sub-bullet text exists ahead of the existing "Fix
      the code to align with the spec" step;
    - each of the four dispatch sites named in Requirement 8 contains
      `run_in_background: false` (four separate assertions, one per site, each scoped to
      that step's text so one site cannot satisfy another's assertion);
    - the headless-contract block is byte-identical to its pre-change content.

    These are **static text checks** on the command file. They prove the instructions are
    present and correctly placed; they prove nothing about whether a live agent obeys
    them. No automated test in this ticket exercises agent behavior.
11. No change to `scripts/verdict_gate_check.sh`'s behavior, `breaker.py`, or
    `workflows/archon-dark-factory.yaml` — the gate's block/pass decision is unchanged;
    only the classification of *why* it blocked, and whether the originating phase
    actually waits for and records its reviewer's verdict, change.

**Where the tests run.** Requirements 2, 3, 4 and 10 are pytest tests: they run in CI's
`python -m pytest tests/ -v` step and on a developer host. Requirement 6's case lives in
`tests/test_entrypoint_error_signature.sh`, which CI runs because it is listed per-file in
`.github/workflows/ci.yml`; it does not run on the Windows host.
`tests/test_verdict_gate_check.sh` (also CI-listed) must stay green unmodified, since
Requirement 2's edit to the gate script is comment-only.

**Retry/breaker effect (stated, not changed in code).** A verdict-missing run that was
previously shadowed as `environmental:preview_infra` retried until `MAX_RETRIES`; signed
`substantive:verdict_missing` it now trips `trip_to_blocked` on the second consecutive
identical signature (existing `breaker.py::record_failure_signature()` rule, unmodified).
Failures count for more, never less. Requirement 8 is what keeps a flaky-but-passing
review from reaching that trip.

## Architecture / Approach

**Part A — Classifier (`scripts/factory_core/error_signature.py`):**

Add one new compiled regex and one new `if` branch in `classify()`:

```python
_VERDICT_MISSING_RE = re.compile(
    r"verdict_gate_check\.sh: .{0,80}"
    r"(?:verdict missing/unparseable for issue #\d+|blocks issue #\d+ \(STATUS=missing\))"
)
```

Resulting order (Requirement 1): `delivery_failure` conjunction (unchanged, first — it is
about failures *too fast* to have produced any output worth regex-matching) →
`_RATE_LIMIT_RE` → `_VERDICT_MISSING_RE` → `_PREVIEW_INFRA_RE` → the existing substantive
regexes → `unknown`. Concretely: the existing `_RATE_LIMIT_RE` branch moves above the
existing `_PREVIEW_INFRA_RE` branch and the new branch is inserted between them. This is
a regex-chain reordering/insertion, not a restructuring of `classify()`'s control flow.
No `re.IGNORECASE`: the regex matches a line this repo's own script emits verbatim.

**Part B — Conformance command (`commands/dark-factory-conformance.md`):**

Three additive edits:

- Phase 3.5 step 4 gains a leading conditional that branches on *why* this cycle was
  entered (tool error/timeout/empty response vs. an actual parsed Material-divergence
  finding) before deciding whether to attempt a code fix at all (Requirement 7).
- The four reviewer dispatch sites (Step 3.1 steps 4 and 5a; Phase 3.5 steps 6 and 7a)
  each gain the foreground-dispatch text of Requirement 8.
- A new Phase 6 section, unconditionally reached last, that never writes a verdict of its
  own (Requirement 9).

None of these edits changes Phase 3 step 7's verdict-parsing logic, the shadow verdict
mapping, or what Phase 4/5 write — all of that is unchanged and already correct per #394.
The dispatch-site edits add only *how* the Agent tool is called and that its result is
awaited; model pins, prompts and read-access rules at those sites are untouched.

## Alternatives Considered

1. **A second, separate retry at the Phase 3 step 4 spawn site** (re-invoke the Agent
   tool call immediately, before even reaching step 5's parsing), kept distinct from the
   reconcile loop. Rejected: this would create two retry policies with independent
   budgets for what is fundamentally the same failure class (reviewer subagent didn't
   produce a usable verdict this cycle), making the phase harder to reason about and to
   test, for no behavior the existing reconcile loop doesn't already provide once
   Requirement 7's conditional is added.
2. **Match a new, structured machine-readable marker instead of `verdict_gate_check.sh`'s
   existing prose.** Considered for robustness against future wording changes, but
   rejected for this ticket's S–M scope: it would require `verdict_gate_check.sh` to emit
   a new marker on a path that already works correctly (it already fails loudly and
   posts a comment) purely to make the classifier's job easier. Requirement 2's
   cross-reference comment plus its wording-coupling test is the lighter-weight
   mitigation for the same risk.
3. **Generalize the Phase 6 artifact guard to `dark-factory-code-review.md` and
   `dark-factory-docs-conformance.md` too.** Those commands have their own distinct
   fail-open/observe-mode contracts (`code_review.fail_open`, `docs_conformance.mode`)
   that a copy-pasted BLOCKED-only guard would not fit without its own design pass.
   Out of scope here; flagged as a candidate follow-up only if this pattern proves
   useful in practice.
4. **Have the Phase 6 guard write `STATUS: BLOCKED` with a `REASON:` line when the
   artifact is missing** (this spec's original design). Rejected at the spec gate:
   `verdict_gate_check.sh` treats any `BLOCKED` file as "upstream already
   communicated" and exits quietly, so the synthesized verdict would suppress the
   `df-push-gate-failure` comment and replace the classifiable "verdict
   missing/unparseable" log line with one the classifier does not match. A guard
   that synthesizes a verdict also sets a precedent a later edit could turn into a
   defaulted PASS. Requirement 9 therefore only re-enters the real Phase 4/5 or
   leaves the file absent.
5. **Rank `verdict_missing` above `_RATE_LIMIT_RE` as well** (original design).
   Rejected by operator decision: a rate-limit death that ends in a missing verdict
   (the MarketHawk #439 shape, two runs in a row) would become substantive and trip
   the breaker on the second attempt. Rate-limit stays environmental.

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
ticket's Requirements 7–9 (a passing — or silently-lost — conformance review should not
cost a full pipeline re-run); they introduce no additional scope.

## Open Questions (non-blocking)

- Whether `_VERDICT_MISSING_RE`'s wording should eventually be shared verbatim (e.g. via
  a small constants module) between `error_signature.py` and `verdict_gate_check.sh`
  instead of being duplicated prose kept in sync by comment convention. Deferred: no
  other `environmental:*`/`substantive:*` regex in this file shares a constant with its
  origin script today, so doing it only here would be an inconsistent one-off.

## Assumptions (flagged)

- Requirements 8 and 9 are instructions to an LLM agent, not enforced mechanisms: if
  the orchestrating agent ignores them, or its turn is killed externally (process OOM,
  container timeout, `idle_timeout` on the DAG node) before reaching Phase 6,
  `conformance.md` stays absent exactly as today. In that residual case the gate still
  hard-blocks and posts its comment (unchanged), and Requirement 1 classifies the
  failure correctly — provided the next assumption holds.
- **Unverified, load-bearing:** the Archon executor forwards the `conformance-gate`
  `bash:` node's stderr into the stream `entrypoint.sh` tees to `TMP_OUT`. Nothing in
  this repo proves it. Requirement 6 makes verifying it a mandatory plan step with a
  stop-and-report outcome if it is false.
- The container CLI's Agent tool accepts `run_in_background: false` and runs subagents
  in the background by default (observed in the tool schema recorded in run transcripts
  from the current image). If a future CLI renames or removes the parameter, Requirement
  8's remaining text (response required before any later step; poll inside the turn)
  still applies, and Requirement 10's static assertions will need updating.
- The #580 root cause (turn ended while reviewers were in the background) is taken from
  the operator's post-mortem of run `23860f3dc5a756b4e64ef2ab7a4b3154`; that run's
  transcript was not available to the spec gate.
- This spec assumes `conformance.max_reconcile_cycles` (default 3) remains the shared
  budget for both "material divergence found" and "tool error/empty response" cycles
  per Requirement 7/Alternative 1 — no config change is introduced.

# Declarative per-loop stop conditions enforced by the breaker (A4)

**Issue:** #198 · **Epic:** #194 (Factory/Target boundary v1) · **Depends on:** #195 (A1, shipped),
#301 (A1.5, spec-pending-review + plan written on `refine/issue-301-...`, not yet merged)
**Status:** spec-pending-review

## Overview

Stop conditions today are factory-global: `breaker.py`'s single `MAX_RETRIES`
(`config/config.yaml scheduler.max_retries`) per issue:phase, gate-`BLOCK` halts, and
`epic_autopilot`'s `daily_cap`/`confidence_floor`. Epic #194's A1 (#195, shipped) added a `loops:`
block to `.factory/adapter.yaml` letting a target *declare* a loop's `verifier`/`stop_condition`/
`failure_behavior` as opaque strings, but nothing reads or enforces them — "declared and validated
today, enforced by nothing" (spike #311's own inventory). This ticket (A4) is the first of #194's
children to build real enforcement against a declared `stop_condition`, so that "agent says done" can
never be the thing that ends a loop.

Two upstream artifacts materially re-scope this ticket beyond its own issue text and are treated as
binding here:

- **#301 (A1.5)**, the schema owner, restructures the loop entry from 11 flat fields into five
  required sub-blocks (`discovery`, `handoff`, `verification`, `persistence`, `scheduling`) plus
  optional `human_checkpoint`/`budget_caps`/`role_card`/`economics`/`skills`, and its binding
  "Consumers" table names exactly what #198 reads: `verification.stop_condition`,
  `scheduling.failure_behavior`, `budget_caps.max_tokens` — with `budget_caps` declared the **sole**
  home for token caps (operator-review Alternative 6, rejected: a duplicate `stop_condition.max_tokens`
  field). The operator's own 2026-08-28 comment on #198 names the same three surfaces
  (`verification.*`, `budget_caps`, `scheduling`).
- **Spike #311**'s "Handoff to #197 and #198" section renders a verdict: **"#198: PROCEED,
  re-scoped — the external-predicate stop condition MUST be a contract-satisfaction check (required
  deliverables reached a durable sink + ack), not only iteration/token/deadline caps,"** with two
  inherited acceptance criteria — "agent says done" is never a stop condition, and the #300 regression
  (a run reaching Done because completion was inferred from node exit status, with no cost-report
  comment ever posted) must be shown to halt under the new mechanism. The same spike also rules that
  #198's cap-class stops (`max_iterations`/`max_tokens`/`deadline`) are "legitimately breaker-side"
  (stop owner 1, reads prior-run state) and exempt from the advisory-promotion bar that governs
  trajectory/contract verdicts (stop owner 2, #197's territory) — see Requirement 8.

This spec was produced by a multi-round product-owner brainstorming pass (transcript in the issue
comment) that resolved several internal tensions left open by #301 and #311 — most importantly, where
`max_iterations`/`deadline` live in the restructured schema (§Requirement 1), and how "MUST be a
contract-satisfaction check" is satisfiable without #198 designing the full `contract:` block that
#301 explicitly defers (§Requirement 6).

## Requirements

### R1 — Schema delta: two new fields in `scheduling`; `verification.stop_condition` is resolved, not widened

`scheduling.max_iterations` (int, `>= 1`) and `scheduling.deadline_seconds` (int, `>= 1`, **relative**
seconds from the loop's first recorded attempt — not an absolute timestamp) are new, additive,
optional fields inside the existing required `scheduling` sub-block. Both validated with the same
hand-rolled primitives #301 (R6) commits `adapter.py` to (no `jsonschema`), following #301's exact
message convention: `loops[{i}] ('{name}'): block 'scheduling': field '{key}' must be an int >= 1`.

This resolves a tension #301 leaves open: its R1 rationale prose says "`scheduling` is reserved for
the [Loop Engineering paper's] retry/cadence/iteration policy that #198 will populate," while its
binding Consumers table lists only `scheduling.failure_behavior`, and separately permits widening
`verification.stop_condition` to a mapping. Resolution, by the schema's own field-grouping logic
(quoted from #301 itself): `verification` = "verifier produces the signal `stop_condition`
interprets" — but an iteration count or a wall clock is not a signal any verifier produces, it's
counted by the dispatcher across attempts with no verifier in the path. `scheduling` = "what happens
next on failure is a scheduling concern" — attempt/deadline accounting is exactly that, and it's
literally where the counting already lives (`breaker.py`'s `get_retry_count`/`increment_retry`,
consulted pre-dispatch, not mid-run). Spike #311 draws the identical line for its own purposes:
cap-class stops are breaker-side because they "read prior-run state"; trajectory/contract checks are
mid-run and never a scheduler predicate. Cap-class → `scheduling`. Evidence-class → `verification`.

`verification.stop_condition` therefore stays exactly what A1 shipped: a plain non-empty string,
opaque-reference-typed like `verifier` (e.g. today's `stop_conditions/triage_stop.py` example in
`tests/test_adapter.py`). #198 **resolves** it (executes what it references) rather than widening its
type — the same relationship #197 (A3) has to `verification.verifier`. #301's "may widen to
string-or-mapping" is permission, not instruction; after `max_iterations`/`deadline` move to
`scheduling` and `max_tokens` reads `budget_caps` (R2), nothing is left that would need a mapping.

Deadline is relative-seconds rather than an absolute timestamp for three reasons: an absolute
timestamp checked into a versioned `adapter.yaml` goes stale and becomes a silent permanent trip; a
relative int keeps `adapter.py` dependency-free (no date parsing, matching #301 R6); and it composes
directly with storing the computed deadline as epoch-seconds in breaker state (R4), matching that
file's existing all-int-valued shape.

### R2 — `max_tokens`: presence of `budget_caps.max_tokens` alone activates the cap; no new field anywhere

No `stop_condition`/`scheduling` key for max_tokens. This is foreclosed by #301's own rejected
Alternative 6 ("a third [field], `stop_condition.max_tokens`, planned by #198 — would force every
consumer to define cross-block precedence; a foundation schema gets one home") and R4 ("the
`max_tokens` stop condition MUST read `budget_caps.max_tokens` rather than add a field of its own").
`budget_caps.max_tokens` is required-within-block whenever `budget_caps` is present at all (#301
R4 — `budget_caps: {}` is itself an error), so presence is already an unambiguous, non-defeasible
declaration; an `enforce_budget_caps: true`-style flag would add only one new, harmful state (cap
declared, enforcement off) that could defeat #301 R4's `side_effect_level >= 4` mandatory-cap rule —
a target-authored weakening of a safety-adjacent field, which CLAUDE.md's hard limits reserve for its
own ticket.

**Composition rule (new, #198's to state):** a declared `budget_caps.max_tokens` composes with the
factory-global budget as `min(budget_caps.max_tokens, factory-global budget)` — a per-loop cap may
only *tighten*, never raise, the ceiling `config/config.yaml`'s `token_optimization` block already
enforces (that config carries its own comment: "NO env override — rollback is a git commit to main").
A target-owned adapter file that could widen a factory budget ceiling would be exactly the kind of
budget escalation CLAUDE.md reserves for a human-reviewed change to `config/config.yaml` itself, not
a side effect of a target's own loop declaration.

Note the semantic split this implies, spelled out because the issue text elides it: `budget_caps.max_tokens`
is a **cumulative, cross-iteration spend cap** for one declared loop (the concept `run_record.py`'s
`_compute_harness_economics`/`retry_spend`/`failure_spend` already measures, in tokens, per run,
correlatable across a loop's repeated runs by loop name), not the same thing as `config.yaml
token_optimization.budgets.<scenario>` (a **per-invocation prompt-context** budget for a single agent
call, enforced by `scripts/budget_enforce.py`). #198 enforces the former; the latter is unmodified and
unrelated infrastructure that happens to share the word "budget."

### R3 — Generic, loop-entry-parameterized stop-condition evaluator in `breaker.py`

Add one new pure-ish function (state-file I/O only, no network) to `scripts/factory_core/breaker.py`:
conceptually `evaluate_stop_condition(loop_entry: dict | None, issue_num: int, phase: str,
clone_dir: str, state_file: Path) -> StopVerdict`, where `StopVerdict` is a small structured result
— not a bare bool — carrying `stopped: bool`, a `reason` drawn from a **closed enum** that partitions
cap-class from predicate-class outcomes (`max_iterations`, `deadline`, `max_tokens`,
`predicate_satisfied`, `predicate_unsatisfied`, `predicate_error`, or `None`/not-yet-tripped), and a
free-form `detail` dict for the audit trail. `predicate_satisfied` is the **only** successful-stop
reason; every other tripped reason is a failure-class stop routed to the loop's declared
`failure_behavior` (R5). This partition is what lets a future caller (the entrypoint.sh wiring
follow-up, R11.2) apply different authority per class without re-plumbing the evaluator, per #311's
owner ruling that trajectory/contract verdicts and breaker-side caps are different signal classes
governed by different rules.

`loop_entry=None` means "no declared loop governs this dispatch" — the parity path. Today, **every**
live call site in `scheduler.sh` passes `loop_entry=None`, because `.factory/adapter.yaml` on `main`
(dark-factory's own) declares zero `loops:` entries, and none is known to exist on MarketHawk's either
(#301 verified this directly). This is the ticket's central scoping fact: #198 ships real enforcement
*code*, exercised by direct fixture/unit tests (§Requirement 7), but has no live consumer that
declares a real business loop today — the same "execution-inert until A2-A5" framing #301 uses for
itself applies to A4 too, in the sense that nothing currently *dispatches* a declared loop. #198 does
not invent a phase-name-to-loop-name binding convention (e.g. a loop literally named `implement`
silently overriding the factory's own `MAX_RETRIES`) — that would let a target-owned adapter file
override the factory's own safety counters for its *own* phases, which is a different, larger, and
more dangerous change than this ticket's scope, and CLAUDE.md's "never weaken safety gates... as a
side effect of another change" reserves it for its own ticket if ever built.

### R4 — Per-loop state: reuse `breaker.py`'s existing flat-key scheme, namespaced

New counters/timestamps live in the same flat `dict[str, int]` `scheduler-state.json` file, keyed by
extending the existing `f"{issue_num}:{phase}"` convention with a loop segment — e.g.
`f"{issue_num}:{phase}:loop:{name}:iter"` for the per-loop attempt counter and `...:deadline_start`
for the epoch-seconds deadline anchor — mirroring the file's existing `<key>:sig` / `<key>:delivery`
suffix convention (`record_failure_signature`, `retry_or_skip_delivery_failure`). No new state-file
shape, no nested objects (matches `record_failure_signature`'s own documented rationale for staying
inside the flat-key-per-issue+phase shape). **`reset_retry` must pop these new keys alongside the
existing `<key>`/`<key>:sig`/`<key>:delivery` triad** — the #33/#279 precedent this file's own
docstrings warn about: a resumed-from-Blocked ticket inheriting banked state trips the breaker one
attempt early. An issue-less business loop (not tied to any GitHub issue) is explicitly **not**
designed here — nothing in this repo dispatches anything that isn't a GitHub issue today, so there is
no lifecycle (who resets it?) to design against; a future loop-runtime ticket owns that shape.

### R5 — External predicate: check-only command, exit code is the verdict, fail-closed on anything else

`verification.stop_condition`'s string is resolved as an executable reference (relative to
`clone_dir`, same treatment as `verifier`) and run via `subprocess.run` in **argv form** — never
`shell=True`, never string-interpolated with issue/branch/comment text — under a hard timeout. Exit
code is the verdict:

| Predicate result | Evaluator outcome |
|---|---|
| exit 0 | `predicate_satisfied` → successful stop |
| nonzero exit | `predicate_unsatisfied` → not satisfied, loop continues subject to `max_iterations`/`deadline_seconds`/`budget_caps.max_tokens` |
| missing / not executable / times out / crashes | `predicate_error` → **fail-closed**, treated as a failure-class stop, never as satisfied and never as "keep looping forever" |

The fail-closed row is the mechanism's whole safety argument and directly satisfies the inherited AC
("agent says done" is never a stop condition: the evaluator's only inputs are counters, a clock, the
token ledger, and this exit code — never agent-authored text). It mirrors this repo's own existing
`scripts/verdict_gate_check.sh` convention almost exactly ("a missing or unparseable verdict is a
BLOCK... the exit code IS the gate signal — do not wrap this call in `|| true`"), cited here as
precedent rather than reused as code, per the divergence discussion in Alternatives #1.

A predicate can only ever **cause** a stop (satisfied or errored); it can never extend, relax, or
override `max_iterations`, `deadline_seconds`, or `budget_caps.max_tokens` — caps stop the loop
regardless of what the predicate returns, closing off a target-authored predicate script from
becoming a second, unaudited path to raise its own resource ceiling.

### R6 — Satisfying "MUST be a contract-satisfaction check" without designing `contract:`

Spike #311 requires the external predicate to be *capable of* being a contract-satisfaction check
(required deliverables reached a durable sink + acknowledgement), not merely a cap. #301 R5
simultaneously forecloses #198 from adding a `contract:` schema block — that full design
(`required_deliverables[]`, `durable_sink`, `evidence_predicate`, `required_delivery_ack`,
`clarification_policy`) is explicitly deferred to "a follow-up child of epic #194" that #311
recommends filing, not assigned to #197 or #198. The reconciliation: **#301 owns the declarative
shape (a future ticket); #198 owns the enforcement seam (this ticket)** — R5's generic
exit-code-verdict mechanism is exactly that seam, and it is agnostic to what the predicate checks.

#198 discharges the inherited AC by shipping, as a concrete regression fixture (not a schema
addition):

- **One working example predicate** that performs a real contract-satisfaction check: "has a
  `<!-- dark-factory-cost-report -->`-marked comment been posted for this run?" — reusing
  `get_tracker().get_comments(issue_num)` (`scripts/factory_core/providers/cli.py`'s
  `tracker get-comments`, already shipped) to search for the marker, the exact durable sink and
  evidence predicate spike #311's own #300 desk walk-through names (`durable_sink` = issue comment;
  `evidence_predicate` = marker-comment exists for this run; `required_delivery_ack: true`). This is
  a genuinely useful first instance of the seam, not a throwaway — it directly checks the class of
  regression #300 was.
- **A test proving the #300 failure class produces no successful stop**: with the marker comment
  absent (the actual pre-fix #300 state), the evaluator must never return `predicate_satisfied`; it
  falls through to the cap-class stops and, on trip, records `predicate_unsatisfied`/`predicate_error`
  as the reason — never inferring success from a clean process exit, which was #300's actual defect
  ("a run reached Done because completion was inferred from node exit status").
  A second assertion covers spike #311's own stated invariant for this exact fixture: two valid
  trajectories both satisfy the predicate ("posted once at run end" and "posted early, updated in
  place under the same marker") — "the predicate checks marker presence, not the path that produced
  it" — so the fixture must not accidentally encode a single exact-ordering assumption.
- Placement: the example predicate script lives under `scripts/` (sibling to
  `verdict_gate_check.sh`/`push_gate_check.sh`) or `tests/fixtures/` if genuinely test-only — repo
  content, not adapter schema — wired into #198's own test suite as `stop_condition: <path>` on a
  synthetic `loop_entry` fixture. It is explicitly **not** a claim to own completion-contract design;
  when #301's deferred `contract:` block eventually ships, it is expected to compile down to a call
  through this same evaluator seam rather than opening a second stop-condition path (stated here so a
  future ticket inherits the seam instead of forking it).

### R7 — Enforcement wiring: real dispatch-path integration, not a dead branch

`scheduler.sh`'s existing retry-related call sites — Priority 3 `stage_blocked_retry`
(`get_retry_count`/`MAX_RETRIES`/`increment_retry`) and the equivalent refine/plan retry checks — are
refactored to route through the new evaluator (via a new thin `factory_core/cli.py` subcommand,
mirroring the existing `breaker-get`/`breaker-incr`/`breaker-trip` pattern) with `loop_entry=None`,
rather than their current ad-hoc integer comparison against `MAX_RETRIES`. This keeps the mechanism on
the real, live dispatch path (satisfying the issue's "enforce in `breaker.py` + the dispatch path")
instead of shipping an untested branch, while being **byte-identical** in observable behavior for
every phase that exists today — the acceptance criterion "existing breaker behavior for factory phases
is unchanged (`test_scheduler*.sh` green)" is exactly this parity claim, and is the primary thing
`test_scheduler*.sh` must keep proving. `runs.jsonl` audit-trail entries (§Requirement 8) are written
regardless of whether `loop_entry` is `None` or populated, so the plumbing itself is exercised by
every existing dispatch even though no test asserts on new-loop-specific fields until a real
`loops:` entry exists.

### R8 — `runs.jsonl` audit trail via the real writer, not the Seq-only health-event path

The acceptance criterion "an audit trail in `runs.jsonl`" must go through `run_record.py`'s actual
`cmd_record`/`_append_jsonl` path — **not** `emit_health_event`, which posts only to Seq, is
best-effort, and swallows every exception (an unsuitable, unfalsifiable evidence source for a stop
decision, let alone its own audit trail). `breaker.py` currently imports no `run_record` module at
all, so this wiring is net-new: on any tripped `StopVerdict`, `breaker.py` writes one `runs.jsonl` line
carrying the loop name (if any), the `reason` enum value, and the `detail` dict, via the existing
`SCHEDULER_STATE_DIR`/`runs.jsonl` path both files already agree on
(`scheduler.sh` and `run_record.py` share `SCHEDULER_STATE_DIR`).

### R9 — `failure_behavior`: stays free-form and unvalidated; every value resolves to `trip_to_blocked` today

#198 does not add a `failure_behavior` vocabulary or a dispatch table keyed on its value. Grounds: (a)
narrowing an A1-shipped, non-empty-string-only field to a closed enum is a schema change #198 doesn't
own — that's #301's surface, via its own reserved-key/carve-out mechanism, on its own ticket; (b)
`.factory/adapter.yaml` declares zero `loops:` entries anywhere, so there is no production data to
design a vocabulary from — the only value that exists anywhere in this repo is `escalate_to_human`, in
an archived design example and two test fixtures; (c) that one example value is, operationally,
already what `trip_to_blocked` does (Blocked + `needs-discussion` + comment). Every declared
`failure_behavior` string is recorded **verbatim** (length-bounded, treated as untrusted adapter-
supplied text — it flows into a GitHub comment body) in both the `trip_to_blocked` `reason` text and
the `runs.jsonl` record, so an operator can see which declared behavior *would* apply, without #198
building a second behavior today. A named non-goal, not a silent gap: the first ticket that needs a
second real behavior builds the dispatch table then.

### R10 — Cap-class stops ship directly blocking; the predicate class has no live blocking call site to promote

Spike #311's advisory→conformance-input promotion bar (false-positive rate == 0% over ≥10 bench
issues, 5-run monitoring window, tiered rollback — the #190 precedent) explicitly **exempts** "#198's
cap-class stops, which are legitimately breaker-side." This exemption is confirmed correct and does
not extend to the external-predicate class: cap-class stops (`max_iterations`/`deadline`/`max_tokens`)
extend an *already-blocking* mechanism (`MAX_RETRIES` already trips directly to Blocked +
`needs-discussion` with no advisory phase today) with more counters of the same objective kind; there
is no inferential judgment involved (`iterations > N` has no false-positive population), and the
composition rule in R2 means a cap bug's blast radius is "stopped too early," never "let a bad run
through" — the inverse of the risk the promotion bar manages.

For the predicate class: #198 ships **no live blocking call site** for it at all (R7's parity wiring
only touches the cap-class path; no `entrypoint.sh` Done-transition currently consults any predicate).
The advisory-vs-blocking distinction is therefore moot for what #198 actually ships — mirroring #190's
own precedent for an unwired checker ("this ticket wires no caller — advisory only... no changes to
`config/config.yaml` ship in this ticket; the mode knob is delivered as normative spec text for the
separately reviewed follow-up"). #198 does **not** build an `advisory`/`blocking` mode toggle now (a
knob with one reachable value on a governance-adjacent surface is dead config a conformance review
would be right to flag). Instead:

- The `StopVerdict` reason enum (R3) is the live mechanism that lets a future caller apply different
  authority per class without re-plumbing — that's the actual inheritance path.
- Normative spec text, for the follow-up ticket that wires a predicate verdict into a real
  Done-transition gate (§Requirement 11), names the exact quantified bar above as its acceptance
  criterion, not merely a cross-reference.

### R11 — Named, deferred, spike-assigned obligations (must not be silently dropped)

Four items spike #311 explicitly assigns toward #198's territory that this ticket does **not** build,
each named here with its reason so a conformance reviewer diffing against the spike's verdicts finds
an explicit ruling, not a silent omission:

1. **`epic_autopilot.should_advance` contract-input condition.** The spike: "Autopilot... must treat a
   missing or BLOCKED contract verdict exactly as it treats a BLOCKED conformance verdict: no advance.
   That is one input condition on `should_advance`, owned by #198." `should_advance`
   (`scripts/factory_core/epic_autopilot.py`) reads a verdict dict that nothing produces for contracts
   yet (that verdict artifact is #197's Gate-2-sibling `contract.md`, unmerged). Building this now
   would consult a value that doesn't exist. **Deferred** to the same follow-up as item 2, once #197's
   verdict artifact exists to read.
2. **`entrypoint.sh` Done-transition wiring.** The spike places the real run-end contract check
   "immediately before the Done/board transition" in `entrypoint.sh`'s post-run path. Changing what
   blocks a Done transition is gate-changing behavior (CLAUDE.md: "gate changes get their own reviewed
   ticket"), and there is no loop runtime today to wire into regardless. **Filed as a follow-up
   ticket** (not built here), pointed at `entrypoint.sh`'s three `post_cost_report ... || true` call
   sites — the literal #300 silent-failure shape — as its motivating evidence; #198 does not remove
   the `|| true`, that is the follow-up's call once the gate itself exists.
3. **`emit_verdict`/`verdict_gate_check.sh` shape reconciliation.** The spike recommends the external
   predicate emit `emit_verdict` shape (STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY, a file artifact),
   consumed by the existing `verdict_gate_check.sh` pattern — explicitly "recommended... binding only
   once their own specs adopt it." #198 adopts exit-code semantics instead (R5); see Alternatives #1
   for the justification and how the two are expected to reconcile at #197's Gate-2-sibling evaluation
   point rather than inside #198's breaker-side evaluator.
4. **`substantive:contract_violation` error-signature class.** The spike wants a contract BLOCK
   written through `error_signature.classify()` as a new signature class, for breaker stuck-detection
   (`record_failure_signature`) to see repeated contract failures without a fifth ad-hoc classifier.
   #198's evaluator does not flow through `classify()` — its own `StopVerdict.reason` enum plus the
   `runs.jsonl` record (R8) is its audit trail. **Declined explicitly**, not silently: a fifth
   classifier is coupled to #197's verdict artifact and belongs with that ticket, not this one.

### R12 — Security: predicate execution is target-authored-code execution, treated accordingly

`stop_condition`'s resolved command is content from the target repo's own `.factory/adapter.yaml` —
already within this factory's existing trust model (it already executes target test suites and smoke
checks), but the evaluator must still: invoke via argv list (never `shell=True`, never string-format
issue/branch/comment content into a command), resolve any relative path against `clone_dir` (never an
absolute or `..`-escaping path — same posture as `run_record.py`'s existing `run_id` path-traversal
guard), and enforce a hard timeout with `predicate_error` (fail-closed) on expiry. The declared
`failure_behavior` string (R9) is likewise treated as untrusted adapter-supplied text, length-bounded
before it reaches a GitHub comment body.

## Architecture / Approach

Files touched:

- `scripts/factory_core/adapter.py` — add `max_iterations`/`deadline_seconds` as optional int fields
  inside the existing `scheduling` sub-block validation (additive; #301's Consumers preamble
  pre-authorizes one-line schema additions inside an existing block, in their own ticket).
- `scripts/factory_core/breaker.py` — new `evaluate_stop_condition(...)` / `StopVerdict`, the new
  namespaced state-key helpers, `reset_retry` extended to pop the new keys, the argv-only/timeout
  subprocess-execution helper for the external predicate, and the `runs.jsonl` write-through (new
  `run_record` import).
- `scripts/factory_core/cli.py` — one new thin subcommand mirroring the existing `breaker-*` family,
  giving `scheduler.sh` (bash) a way to invoke the new evaluator.
- `scheduler.sh` — refactor the Priority 3 `stage_blocked_retry` path and the refine/plan retry checks
  to call the new subcommand with `loop_entry=None`, replacing the current ad-hoc `MAX_RETRIES`
  integer comparison; behavior must be byte-identical (verified by `test_scheduler*.sh`).
- One new example predicate script (`scripts/` or `tests/fixtures/`) implementing the #300
  cost-report-marker check via `tracker get-comments`, plus its regression test.
- `tests/test_factory_core_breaker.py`, `tests/test_adapter.py` (new `scheduling` field cases),
  `tests/test_scheduler*.sh` (parity assertions) — extended, not restructured.
- No changes to `config/config.yaml`, `entrypoint.sh`, `epic_autopilot.py`, or `error_signature.py`
  (R11 items are named, not built).

## Alternatives considered

1. **Adopt spike #311's recommended `emit_verdict`/file-artifact shape for the external predicate**
   (rejected — R5/R11.3). That shape assumes an artifacts directory and a check-only DAG node
   (`verdict_gate_check.sh`'s actual consumer), which is #197's Gate-2-sibling evaluation point, not a
   breaker-side pre-dispatch evaluator with no artifacts dir. The issue's own text ("a check-only
   command/hook whose exit code is the verdict — same trust model as smoke-gate") is also direct,
   already-authorized product intent for the simpler exit-code form. The recommendation is explicitly
   "binding only once [#197/#198's] own specs adopt it" — this spec declines for #198's own mechanism
   and expects reconciliation to happen where #197's richer verdict artifact is produced, not by
   duplicating that shape inside `breaker.py`.
2. **Widen `verification.stop_condition` to a mapping holding `max_iterations`/`deadline`/
   `external_predicate` together** (rejected — R1). Would cost a dual-form parser in a schema whose
   loader is deliberately hand-rolled and dependency-free (#301 R6), for no benefit once cap fields
   move to `scheduling` and `max_tokens` reads `budget_caps` — nothing is left that needs a mapping.
3. **Invent a loop-name-to-phase binding so a declared loop could override the factory's own
   refine/plan/implement retry limits today** (rejected — R3). Lets a target-owned adapter file
   override the factory's own safety counters for its own operational phases; a materially different
   and riskier change than "declare stop conditions for a target's own business loops," and out of
   this ticket's authorized scope per CLAUDE.md's hard limits on weakening breaker/budget behavior as
   a side effect.
4. **Build a `failure_behavior` dispatch table / validated enum now** (rejected — R9). No production
   data exists to design a vocabulary from (zero declared `loops:` entries anywhere); the one example
   value in the repo (`escalate_to_human`) is already operationally identical to `trip_to_blocked`.
5. **Ship an `advisory`/`blocking` mode toggle on the evaluator now** (rejected — R10). Directly
   mirrors #190's own precedent for an unwired checker: ship the checker, state the promotion bar as
   normative spec text for the ticket that actually wires a live caller, don't add a knob with one
   reachable value.
6. **Wire the contract-satisfaction predicate into `entrypoint.sh`'s Done-transition gate now**
   (rejected — R11.2). Gate-changing behavior needs its own reviewed ticket per CLAUDE.md, and there
   is no loop runtime to wire into regardless (`.factory/adapter.yaml` declares no `loops:` entries on
   any known instance).
7. **Design the full `contract:` schema block (`required_deliverables[]` etc.) to make the "MUST be a
   contract-satisfaction check" AC maximally literal** (rejected — R6). Explicitly reserved/deferred by
   #301 R5 to a separate, not-yet-filed follow-up child of epic #194; #198 satisfies the inherited AC
   via a concrete regression fixture through its generic seam instead, per the reconciliation in R6.

## Open Questions (non-blocking)

- Exact CLI subcommand name/flag shape for the new `breaker-*` evaluator entry point (implementation
  detail; follows the existing `breaker-get`/`breaker-incr`/`breaker-trip` family's pattern).
- Whether the entrypoint.sh Done-gate follow-up ticket (R11.2) and the `epic_autopilot.should_advance`
  condition (R11.1) should be one ticket or two — left to whoever files it, once #197's verdict
  artifact shape is merged and concrete.
- `#301` and `#197` are both still on unmerged refine branches as of this spec; the exact field paths
  (`verification.stop_condition`, `scheduling.*`) must be re-verified against `main` at plan/implement
  time in case either spec changes shape before merging.

## Assumptions (flagged)

- **[ASSUMPTION]** `scheduling.deadline_seconds` is a new field name invented by this spec (#301 names
  `scheduling` as reserved territory for iteration/cadence/retry policy but does not itself name a
  deadline field). Chosen for consistency with `max_iterations` sitting in the same block and to keep
  the loader dependency-free (relative int, no date parsing).
- **[ASSUMPTION]** The per-loop state key shape (`f"{issue_num}:{phase}:loop:{name}:iter"` etc.) is a
  new convention modeled on the file's existing `<key>:sig`/`<key>:delivery` suffix pattern; #198 is
  the first ticket to need a three-part key, and the exact separator/ordering is an implementation
  detail free to adjust as long as `reset_retry` pops every new suffix it introduces.
- **[ASSUMPTION]** The example contract-satisfaction predicate reuses `tracker get-comments` +
  substring search for the `<!-- dark-factory-cost-report -->` marker, rather than adding a new
  provider-level "find comment by marker" primitive — this mirrors `entrypoint.sh`'s own existing
  idempotent marker-comment-upsert helper's search step closely enough that no new abstraction is
  justified for one fixture.
- **[ASSUMPTION]** "Existing breaker behavior for factory phases is unchanged" (issue AC3) is
  interpreted as an observable-behavior/parity claim (identical dispatch decisions, identical
  comments, identical board transitions), not a literal no-diff constraint on `breaker.py`'s source —
  R7's refactor necessarily changes the code path while preserving the outcome.

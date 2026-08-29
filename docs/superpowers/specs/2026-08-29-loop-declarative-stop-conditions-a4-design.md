# Declarative per-loop stop conditions enforced by the breaker (A4)

**Revised:** 2026-08-29 (operator review)

**Issue:** #198 · **Epic:** #194 (Factory/Target boundary v1) · **Depends on:** #195 (A1, shipped),
#301 (A1.5, spec-pending-review + plan written on `refine/issue-301-...`, not yet merged),
#197 (A3, spec approved on `refine/issue-197-...`, not yet merged; R5/R6 build on its `verifier.py`/`verdict.py`)
**Status:** spec-pending-review

## Overview

Stop conditions today are factory-global: `scheduler.sh`'s per-phase retry ceilings compared against
`breaker.py`'s per-issue:phase counters — `MAX_RETRIES` (`config/config.yaml scheduler.max_retries`,
default 3) for implement/resolve and env-only `REFINE_MAX_RETRIES` (default 3) for refine/plan;
`breaker.py` itself holds no ceiling — gate-`BLOCK` halts, and
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
  trajectory/contract verdicts (stop owner 2, #197's territory) — see Requirement 10.

This spec resolves several internal tensions left open by #301 and #311 — most importantly, where
`max_iterations`/`deadline` live in the restructured schema (§Requirement 1), and how "MUST be a
contract-satisfaction check" is satisfiable without #198 designing the full `contract:` block that
#301 explicitly defers (§Requirement 6). The 2026-08-29 operator review re-placed the
external-predicate class onto #197's `verifier.py` seam and the Gate-2-class evaluation point
(#311's owner ruling, adopted here as binding), leaving `breaker.py` with cap-class stops only — see
R5, R7, R12 and the plan-executable R13.

## Requirements

### R1 — Schema delta: two new fields in `scheduling`; `verification.stop_condition` is resolved, not widened

`scheduling.max_iterations` (int, `>= 1`) and `scheduling.deadline_seconds` (int, `>= 1`, **relative**
seconds from the loop's first recorded attempt — not an absolute timestamp) are new, additive,
optional fields inside the existing required `scheduling` sub-block. Both validated with the same
hand-rolled primitives #301 (R6) commits `adapter.py` to (no `jsonschema`), following #301's exact
message convention: `loops[{i}] ('{name}'): block 'scheduling': field '{key}' must be an int >= 1`
(on the #301 implementation branch this is `_validate_subblock(..., int_fields=...)`, so each is a
one-tuple-entry change). These are the only schema additions. `budget_caps` on that branch also
carries an optional `max_retry_spend` (int `>= 1`); #198 does not read or enforce it (#234-family
territory) and adds nothing to `budget_caps`.

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
`tests/test_adapter.py`). #198 **resolves** it (executes what it references, through #197's
`verifier.py` — R5) rather than widening its type — the same relationship #197 (A3) has to
`verification.verifier`. #301's "may widen to
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

**Tighten-only rule (new, #198's to state; replaces any notion of composing with a factory-global
token budget):** `budget_caps.max_tokens` is an *additional* stop. It never reads, modifies, or
relaxes `config/config.yaml`'s `token_optimization.*` block (a per-invocation prompt-context budget
enforced, unchanged, by `scripts/budget_enforce.py`; that block carries its own "NO env override —
rollback is a git commit to main" comment and is not touched by this ticket). There is no
factory-global *cumulative* token cap today, so there is nothing for a per-loop cap to compose with —
it is compared only against the per-loop spend counter R4 defines. More generally, per #193's
ownership boundary (levels 1–3 target-definable, 4–5 factory-owned; reproduced in #301 R4), **a
target-declared cap may only tighten, never raise, a factory default**:

- The factory's own issue:phase retry ceiling (`MAX_RETRIES` / `REFINE_MAX_RETRIES`, passed to the
  evaluator as `ceiling`, R3/R7) is evaluated **first, at every `side_effect_level`, on every
  dispatch shape that exists today** (every dispatch is an issue:phase dispatch). A declared
  `scheduling.max_iterations` can therefore only tighten below it in practice; the effective value
  is `min(scheduling.max_iterations, ceiling)`.
- For a loop entry with `side_effect_level >= 4` that rule is permanent: any future loop runtime
  MUST keep the factory ceiling in the evaluation regardless of dispatch shape. For levels 1–3 a
  future issue-less runtime (explicitly not designed here, R4) may honour declared values as
  declared; that is its spec's call, not this one's.
- `deadline_seconds` and `budget_caps.max_tokens` apply as declared (there is no factory-side
  deadline or cumulative token cap to relax), and a declared loop can never raise any factory
  default. Absence of every cap field means **parity**: no loop-scoped stop of any kind — R3's
  `loop_entry=None` path and a populated entry with none of `max_iterations`/`deadline_seconds`/
  `budget_caps` behave identically for cap purposes.

Note the semantic split this implies, spelled out because the issue text elides it:
`budget_caps.max_tokens` is a **cumulative, cross-iteration spend cap** for one declared loop —
input + output tokens summed over the loop's repeated runs, accumulated in the breaker-state counter
R4 defines — not the same thing as `config.yaml token_optimization.budgets.<scenario>` (a
**per-invocation prompt-context** budget for a single agent call). #198 enforces the former; the
latter is unmodified and unrelated infrastructure that happens to share the word "budget." The opt-in
model-proxy request ledger (`run_record.py`'s `LEDGER_PATH`, `request-ledger.jsonl`, keyed by
`run_id`, absent unless the proxy is enabled) is **not** the cap's data source — it carries no loop
name and may not exist; `evaluate_stop_condition` never reads it.

### R3 — Generic, loop-entry-parameterized cap-class evaluator in `breaker.py`

Add one new pure function (state-file I/O only; **no subprocess, no network**) to
`scripts/factory_core/breaker.py`: conceptually `evaluate_stop_condition(loop_entry: dict | None,
issue_num: int, phase: str, ceiling: int, state_file: Path, now: int | None = None) -> StopVerdict`,
where `ceiling` is the caller's factory retry ceiling (`MAX_RETRIES` or `REFINE_MAX_RETRIES`, R7),
`now` is injectable epoch-seconds for tests, and `StopVerdict` is a small structured result — not a
bare bool — carrying `stopped: bool`, a `reason` drawn from a **closed, cap-class-only enum** —
`max_retries` (the factory ceiling; the parity path's only possible reason), `max_iterations`,
`deadline`, `max_tokens`, or `None` (not tripped) — and a free-form `detail` dict for the audit trail
(R8). Every tripped reason is a failure-class stop routed to the loop's declared `failure_behavior`
(R9). **There is no successful-stop reason in this evaluator**: a successful stop is a Gate-2-class
predicate verdict (R5), produced mid-run/run-end through #197's verifier seam and never by
`breaker.py`, per #311's owner ruling that trajectory/contract verdicts and breaker-side caps are
different signal classes governed by different rules. The enum is deliberately cap-class only so no
future caller can mistake a breaker outcome for evidence of completion.

Comparison semantics are `>=`, evaluated **before** the dispatch that would become the next attempt:
with `max_iterations: 3` and the per-loop attempt counter (R4) already at 3, the 4th attempt is
refused and the loop halted (issue AC1). `deadline` trips when `now >= deadline_start +
deadline_seconds`; `max_tokens` trips when the per-loop token counter `>= budget_caps.max_tokens`.
Evaluation order on a populated entry: `max_retries` (factory ceiling, always) → `max_iterations` →
`deadline` → `max_tokens`; the first tripped reason wins and is the one recorded. On no trip the
evaluator increments the retry counter (and, for a populated entry, the per-loop attempt counter,
anchoring `deadline_start` if absent) — the exact `get_retry_count`/compare/`increment_retry`
sequence the four `scheduler.sh` sites perform today (R7), so the parity path is byte-identical.

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

New counters/timestamps live in the same flat `dict[str, int]` `scheduler-state.json` file, namespaced
off the existing `_make_key(issue_num, phase)` value (`str(issue_num)` for `implement`,
`f"{issue_num}:{phase}"` for every other phase — the file's actual convention) with a loop segment,
mirroring the file's existing `<key>:sig` / `<key>:delivery` suffix convention (`:sig` is owned by
`record_failure_signature` in `breaker.py`; `:delivery` by `retry_or_skip_delivery_failure`, the bash
helper at `scheduler.sh:435`). Three new suffixes, all int-valued:

| Key | Meaning | Written by |
|---|---|---|
| `<key>:loop:<name>:iter` | per-loop attempt counter | `evaluate_stop_condition`, on each non-tripped evaluation of a populated entry |
| `<key>:loop:<name>:deadline_start` | epoch-seconds anchor for `deadline_seconds`; set on the first evaluation that increments `:iter`, never overwritten until reset | `evaluate_stop_condition` |
| `<key>:loop:<name>:tokens` | cumulative loop spend, input + output tokens, across the loop's runs | the run-end path, from `run_record`'s per-run `totals` (`gen_ai.usage.input_tokens` + `gen_ai.usage.output_tokens`), whenever a declared loop is dispatched — a new `breaker.add_loop_tokens(issue_num, phase, name, n)` helper; no live site calls it today (R7) |

`evaluate_stop_condition` compares `:tokens` to `budget_caps.max_tokens` and never reads the
model-proxy ledger (R2). An absent `:tokens` is read as `0` (parity — a cap with no recorded spend
never trips); absent `:iter`/`:deadline_start` read as `0`/"not yet anchored". No new state-file
shape, no nested objects (matches `record_failure_signature`'s own documented rationale for staying
inside the flat-key-per-issue+phase shape). **`reset_retry` must pop these three new suffixes
alongside the existing `<key>`/`<key>:sig`/`<key>:delivery` triad** — the #33/#279 precedent this
file's own docstrings warn about: a resumed-from-Blocked ticket inheriting banked state trips the
breaker one attempt early. Because `trip_to_blocked` ends by calling `reset_retry(key)`, every
cap-class trip clears the loop's counters as a side effect, exactly as today's `MAX_RETRIES` trip
clears the retry counter. An issue-less business loop (not tied to any GitHub issue) is explicitly **not**
designed here — nothing in this repo dispatches anything that isn't a GitHub issue today, so there is
no lifecycle (who resets it?) to design against; a future loop-runtime ticket owns that shape.

### R5 — External predicate: resolved through #197's `verifier.py`, `emit_verdict` shape, Gate-2-class, never breaker-side

`verification.stop_condition` is resolved and executed through #197's
`scripts/factory_core/verifier.py` (`resolve_verifier(clone_dir, path)` → `run_verifier(resolved, env)`
→ `normalize_verdict(exit_code, stdout, gate_type="stop_condition")`), **never by `breaker.py`**.
`verifier.py` already owns the execution posture this class needs — argv-list invocation (never
`shell=True`), clone-relative resolution that refuses absolute or `..`-escaping paths, a hard
timeout, and fail-closed normalisation — so #198 adds no second subprocess runner anywhere. Its
output is an `emit_verdict`-shape artifact (`STATUS / GATE_TYPE: stop_condition / FINDINGS_COUNT /
SEVERITY`, #197's `verdict.py` schema); a bare exit code is accepted only via `normalize_verdict`'s
bare-exit-code mode:

| Predicate result | Normalised verdict |
|---|---|
| exit 0 (or structured `STATUS: PASS`) | `PASS` → the successful stop |
| nonzero exit (or structured `STATUS: BLOCKED`) | `BLOCKED` → not satisfied |
| missing / not executable / times out / unparseable output | `BLOCKED` (**fail-closed**) — never satisfied, never "keep looping forever" |

This is a **Gate-2-class verdict (stop owner 2)**, exactly as #311's owner ruling places it: evaluated
mid-run or at run end by the `verdict_gate_check.sh` pattern (a missing or unparseable verdict is a
BLOCK), **never pre-dispatch by `scheduler.sh`, and never an input to `evaluate_stop_condition`**
(R3). `breaker.py` learns of a predicate BLOCK only through the existing error-signature drop file
(`error_signature.write_signature`) as `substantive:contract_violation` — a class added to
`error_signature.classify()` by whichever ticket first wires a live predicate verdict (per #197
Requirement 8, which reserves the class for that gate and forbids `verifier.py` from calling
`breaker.py` or writing the drop file itself) — so `record_failure_signature`'s stuck-detection sees
repeated contract failures through the one channel it already has, with no parallel signal. Routing
of that BLOCK per the loop's declared `failure_behavior` happens at that same follow-up (R9, R11).

The inherited AC ("agent says done" is never a stop condition) is satisfied structurally: the only
inputs on either path are counters, a clock and the token counter (cap class) or a check-only
command's exit code / structured verdict (predicate class) — never agent-authored text.
`scripts/verdict_gate_check.sh`'s own contract ("a missing or unparseable verdict is a BLOCK... the
exit code IS the gate signal — do not wrap this call in `|| true`") is reused as an unmodified
subprocess, not re-implemented.

A predicate can only ever **cause** a stop (PASS or BLOCKED); it can never extend, relax, or override
`max_iterations`, `deadline_seconds`, or `budget_caps.max_tokens` — caps stop the loop regardless of
what the predicate returns, closing off a target-authored predicate script from becoming a second,
unaudited path to raise its own resource ceiling. #198 ships **no live caller** for this class (R10);
the mechanism is exercised end-to-end by R6's fixture.

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
  `evidence_predicate` = marker-comment exists for this run; `required_delivery_ack: true`). It is a
  check-only command in #197's registration contract (bare-exit-code mode: exit 0 = marker present,
  exit 1 = absent), invoked through `verifier.run_verifier` + `normalize_verdict(...,
  gate_type="stop_condition")`, and its artifact is piped through the **real**
  `scripts/verdict_gate_check.sh` as an unmodified subprocess — mirroring #197's own integration
  test. This is a genuinely useful first instance of the seam, not a throwaway — it directly checks
  the class of regression #300 was.
- **A test proving the #300 failure class produces no successful stop**: with the marker comment
  absent (the actual pre-fix #300 state), the normalised verdict is `BLOCKED` and
  `verdict_gate_check.sh` exits 1; a missing artifact also exits 1 — never inferring success from a
  clean process exit, which was #300's actual defect ("a run reached Done because completion was
  inferred from node exit status"). A second assertion covers spike #311's own stated invariant for
  this exact fixture: two valid trajectories both yield `PASS` ("posted once at run end" and "posted
  early, updated in place under the same marker") — "the predicate checks marker presence, not the
  path that produced it" — so the fixture must not accidentally encode a single exact-ordering
  assumption. A third assertion proves the cap class is independent of the predicate: with the
  marker absent and `max_iterations` reached, `evaluate_stop_condition` trips with reason
  `max_iterations` — never anything predicate-shaped.
- Placement: the example predicate script lives under `scripts/` (sibling to
  `verdict_gate_check.sh`/`push_gate_check.sh`) or `tests/fixtures/` if genuinely test-only — repo
  content, not adapter schema — wired into #198's own test suite as
  `verification.stop_condition: <path>` on a synthetic #301-shape `loop_entry` fixture. If #197's
  `verifier.py` CLI lacks a gate-type flag at implement time, #198 uses the Python API and adds
  `--gate-type` as a one-line additive change in its own PR; `verifier.py`'s execution semantics are
  otherwise not modified. It is explicitly **not** a claim to own completion-contract design; when
  #301's deferred `contract:` block eventually ships, it is expected to compile down to a call
  through this same seam rather than opening a second stop-condition path (stated here so a future
  ticket inherits the seam instead of forking it).

### R7 — Enforcement wiring: real dispatch-path integration at all four retry sites; nothing else moves

`scheduler.sh` has **four** live retry sites, each performing the same five-step sequence: (1)
`check_failure_signature` → `stuck=true` → `trip_to_blocked`; (2) `rollback_paused_retry` (#341:
`environmental:session_window_pause` decrement via `breaker-set-retry`); (3)
`retry_or_skip_delivery_failure` (#279: `<key>:delivery` shadow counter → `skip` / `trip:<reason>` /
`count`); (4) in the `count|*` branch, `get_retry_count` `>=` ceiling → `trip_to_blocked "retry limit
of N reached"`; (5) `increment_retry`. The sites and their ceilings:

| Stage | Phase | Retry key | Ceiling | Site (`scheduler.sh`, approx.) |
|---|---|---|---|---|
| `stage_conflict_resolve` (Priority 1.5) | `resolve` | `<issue>:resolve` | `MAX_RETRIES` | l.883–904 (inline delivery-failure path, not the shared helper) |
| `stage_blocked_retry` (Priority 3) | `implement` | `<issue>` | `MAX_RETRIES` | l.1039–1052 |
| `stage_plan` (Priority 4) | `plan` | `<issue>:plan` | `REFINE_MAX_RETRIES` | l.1099–1117 |
| `stage_refine` (Priority 5) | `refine` | `<issue>:refine` | `REFINE_MAX_RETRIES` | l.1167–1185 |

`MAX_RETRIES` comes from `config/config.yaml scheduler.max_retries` (default 3; env `MAX_RETRIES`
overrides); `REFINE_MAX_RETRIES` is env-only by design (default 3; not in `config.yaml`). **Both stay
exactly as they are; no key in `config/config.yaml` changes.**

**The refactor replaces only steps (4)+(5)** — the `count|*` branch's `get_retry_count`/ceiling
compare/`increment_retry` — at all four sites with one call to a new thin `factory_core/cli.py`
subcommand, `breaker-evaluate-stop --issue N --phase P --ceiling C` (mirroring the existing
`breaker-get`/`breaker-incr`/`breaker-trip` family), invoked with `loop_entry=None` at every site. It
prints `stopped=<true|false> reason=<enum|none>`; on `stopped=true` the bash site calls the existing
`trip_to_blocked` adapter with the reason string R13 fixes (for the parity path, the byte-identical
`retry limit of ${MAX_RETRIES} reached` / `... for conflict resolution` text used today), so the
`breaker-trip` delegation the scheduler tests grep for is unchanged. Steps (1)–(3) — stuck-detection
(`record_failure_signature`, the `substantive:` prefix rule), `rollback_paused_retry` (#341),
`retry_or_skip_delivery_failure` (#279, `breaker-set-retry` back-fill), the resolve site's inline
delivery path — and `trip_to_blocked`'s labels/comment/`reset_retry` are **untouched**. This keeps
the mechanism on the real, live dispatch path (satisfying the issue's "enforce in `breaker.py` + the
dispatch path") while being **byte-identical** in observable behavior for every phase that exists
today — the acceptance criterion "existing breaker behavior for factory phases is unchanged
(`test_scheduler*.sh` green)" is exactly this parity claim, and is the primary thing
`test_scheduler*.sh` must keep proving. The single carved-out addition is R8's audit row on a trip.

Because every live site passes `loop_entry=None`, no live site writes the `:tokens` counter (R4)
today either; `add_loop_tokens` is specified, unit-tested against `run_record` `totals` fixtures, and
becomes live only when a future loop-dispatcher passes a populated entry — the same
"execution-inert until A2–A5" framing #301 uses for itself.

Acceptance criteria for this requirement (executed by the plan):

- [ ] `tests/test_scheduler.sh` sections B (`breaker-trip` delegation), B4e (#341 `breaker-set-retry`
      decrement), K9 (P1.5 trip at `MAX_RETRIES`), K10 (early trip bypassing `MAX_RETRIES`) and every
      existing `rollback_paused_retry`/`retry_or_skip_delivery_failure` case pass **unmodified**.
- [ ] A new `test_scheduler.sh` case per site asserts exactly one `breaker-evaluate-stop --issue N
      --phase P --ceiling C` call in the stub log and, at ceiling, one `breaker-trip` call carrying
      today's exact reason text.
- [ ] `tests/test_factory_core_breaker.py` parity table: for `loop_entry=None`, `(count, ceiling)` in
      {(0,3), (2,3), (3,3), (4,3)} yields the same stopped/increment outcome as today's inline compare
      (`stopped` iff `count >= ceiling`; counter incremented iff not stopped).

### R8 — `runs.jsonl` audit trail via the real writer, not the Seq-only health-event path

The acceptance criterion "an audit trail in `runs.jsonl`" must go through `run_record.py`'s actual
`_append_jsonl` path — **not** `emit_health_event`, which posts only to Seq, is best-effort, and
swallows every exception (an unsuitable, unfalsifiable evidence source for a stop decision, let alone
its own audit trail) — and **not** `cmd_record`, which is argparse-shaped and also posts to Seq.
`run_record.py` gains one small public helper, `append_stop_record(record: dict) -> None`, wrapping
`_append_jsonl` with no Seq post; `breaker.py` (which imports no `run_record` module today — this
wiring is net-new) calls it **only on a tripped `StopVerdict`**, never on a non-tripped evaluation,
so the parity path's dispatches write nothing new. The row shape, exact:

```json
{"stage": "stop_condition", "verdict": "STOPPED", "issue_number": 42, "phase": "implement",
 "loop": "<name or null>", "reason": "<StopVerdict.reason value>",
 "failure_behavior": "<declared, truncated to 64 chars, or null>", "detail": {},
 "timestamp": "<UTC ISO-8601>"}
```

It carries **no `run_id`** (a breaker decision is not a run), so `scripts/reconcile_cost_reports.py`'s
`_load_jsonl_stubs` skips it rather than reporting a spurious "irrecoverable" run, and its `stage` is
not in `GATE_STAGE_NAMES`, so `_compute_outcome` ignores it. Written via the existing
`SCHEDULER_STATE_DIR`/`runs.jsonl` path both files already agree on (`scheduler.sh` and
`run_record.py` share `SCHEDULER_STATE_DIR`). For the parity path (`loop` null, `reason`
`max_retries`) this is the **one** observable addition to today's behavior: a single audit row per
`MAX_RETRIES`/`REFINE_MAX_RETRIES` trip, asserted by a `test_factory_core_breaker.py` case and
explicitly carved out of R7's parity claim (dispatch decisions, comments and board transitions remain
identical).

### R9 — `failure_behavior`: stays free-form and unvalidated; every value resolves to `trip_to_blocked` today

#198 does not add a `failure_behavior` vocabulary or a dispatch table keyed on its value. Grounds: (a)
narrowing an A1-shipped, non-empty-string-only field to a closed enum is a schema change #198 doesn't
own — that's #301's surface, via its own reserved-key/carve-out mechanism, on its own ticket; (b)
`.factory/adapter.yaml` declares zero `loops:` entries anywhere, so there is no production data to
design a vocabulary from — the only value that exists anywhere in this repo is `escalate_to_human`, in
an archived design example and two test fixtures; (c) that one example value is, operationally,
already what `trip_to_blocked` does (Blocked + `needs-discussion` + `factory-regression` labels +
comment + `reset_retry`). Every declared
`failure_behavior` string is recorded **verbatim, truncated to 64 characters** (treated as untrusted
adapter-supplied text — it flows into a GitHub comment body) in both the `trip_to_blocked` `reason` text and
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
only touches the cap-class path; the predicate class lives on #197's verifier seam, R5, and no
`entrypoint.sh` Done-transition or DAG node currently consults any predicate).
The advisory-vs-blocking distinction is therefore moot for what #198 actually ships — mirroring #190's
own precedent for an unwired checker ("this ticket wires no caller — advisory only... no changes to
`config/config.yaml` ship in this ticket; the mode knob is delivered as normative spec text for the
separately reviewed follow-up"). #198 does **not** build an `advisory`/`blocking` mode toggle now (a
knob with one reachable value on a governance-adjacent surface is dead config a conformance review
would be right to flag). Instead:

- The class separation itself is the inheritance path: cap-class authority lives in `StopVerdict`
  (R3, always blocking, cap-class-only enum); predicate-class authority is decided where the verdict
  artifact is consumed (`verdict_gate_check.sh` at the Gate-2-class evaluation point, R5), so a future
  caller applies the promotion bar there without re-plumbing `breaker.py`.
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
3. **`emit_verdict`/`verdict_gate_check.sh` shape reconciliation.** **Adopted, not deferred**: R5
   emits `emit_verdict` shape via #197's `verifier.py`/`verdict.py` and consumes it through the
   unmodified `verdict_gate_check.sh` (the 2026-08-29 operator review reversed this spec's original
   exit-code-inside-`breaker.py` choice; see Alternatives #1). What remains deferred is the live
   consumer — the Done-transition gate of item 2.
4. **`substantive:contract_violation` error-signature class.** The spike wants a contract BLOCK
   written through `error_signature.classify()` as a new signature class, for breaker stuck-detection
   (`record_failure_signature`) to see repeated contract failures without a fifth ad-hoc classifier.
   R5 binds that as the *only* channel by which a predicate BLOCK reaches `breaker.py`; the class is
   added to `error_signature.py` by whichever ticket first wires a live predicate verdict (item 2),
   in the same PR, per #197 Requirement 8. #198 does not add it because it ships no live predicate
   caller. #198's cap-class stops do not flow through `classify()` at all — they trip directly via
   `trip_to_blocked`, exactly as `MAX_RETRIES` does today, with `StopVerdict.reason` plus the R8 row
   as their audit trail. **Deferred with a named owner**, not declined.

### R12 — Security: predicate execution is target-authored-code execution, delegated to `verifier.py`'s posture

`stop_condition`'s resolved command is content from the target repo's own `.factory/adapter.yaml` —
already within this factory's existing trust model (it already executes target test suites and smoke
checks). #198 adds **no execution path of its own**: the predicate runs only through #197's
`verifier.py`, inheriting its posture verbatim — argv-list invocation (never `shell=True`, never
string-formatting issue/branch/comment content into a command), clone-relative resolution that
refuses absolute or `..`-escaping paths (the same guard `run_record.py`'s `_SAFE_RUN_ID_RE` applies to
`run_id`), a hard timeout, and fail-closed `BLOCKED` on expiry or unparseable output. `breaker.py`
gains no `subprocess` call in this ticket. The declared `failure_behavior` string (R9) is treated as
untrusted adapter-supplied text, truncated to 64 characters (R13) before it reaches a GitHub comment
body. #198 does **not read or modify** tool allow/deny lists, `role_card.allowed_tools`/
`forbidden_tools`, any `gate_*` script, `verdict_gate_check.sh`, `deploy/**`, `config/config.yaml`,
or `.factory/adapter.yaml` itself.

### R13 — Exact defaults, trip strings, and acceptance criteria (plan-executable)

Schema (validated by `adapter.py`'s existing `_validate_subblock` for `scheduling`, via `int_fields`):

| Field | Type | Default when absent | Effect |
|---|---|---|---|
| `scheduling.max_iterations` | int `>= 1` (bool rejected) | no per-loop attempt cap | trip when `<key>:loop:<name>:iter >= min(max_iterations, ceiling)` (R2 tighten-only rule) |
| `scheduling.deadline_seconds` | int `>= 1` (bool rejected) | no deadline | trip when `now >= deadline_start + deadline_seconds` |
| `budget_caps.max_tokens` (#301-owned) | int `>= 1` | `budget_caps` absent → no token cap | trip when `<key>:loop:<name>:tokens >= max_tokens` |
| `scheduling.failure_behavior` (#301-owned) | non-empty string | (required by #301) | recorded verbatim, truncated to 64 characters, in the trip comment and the R8 row |

Error strings (exact): `loops[{i}] ('{name}'): block 'scheduling': field 'max_iterations' must be an
int >= 1`, and the same for `deadline_seconds`. An adapter with `loops: []` or no `loops:` key (every
live adapter today) loads byte-identically to #301's behavior: no new error, no new default key, and
`adapter.load()`'s returned mapping is unchanged.

`trip_to_blocked` reason strings (exact; the `reason` enum value is the machine key, the string is
what the issue comment shows):

| Reason | String passed to `trip_to_blocked` |
|---|---|
| `max_retries` (parity) | today's text, unchanged: `retry limit of {ceiling} reached` (`retry limit of {ceiling} reached for conflict resolution` at the resolve site) |
| `max_iterations` | `loop '{name}' stop condition 'max_iterations' reached ({iter}/{max_iterations}); declared failure_behavior: {failure_behavior}` |
| `deadline` | `loop '{name}' stop condition 'deadline' reached ({elapsed}s >= {deadline_seconds}s); declared failure_behavior: {failure_behavior}` |
| `max_tokens` | `loop '{name}' stop condition 'max_tokens' reached ({tokens}/{max_tokens} tokens); declared failure_behavior: {failure_behavior}` |

Every cap-class trip goes through the unchanged `trip_to_blocked` (Blocked + `needs-discussion` +
`factory-regression` + comment + `reset_retry`).

Acceptance criteria (in addition to R7's):

- [ ] AC1: synthetic `loop_entry` with `scheduling.max_iterations: 3` and `ceiling=10` (so the
      declared cap is the binding one): three evaluations return `stopped=false` and advance `:iter`
      to 3; the 4th returns `stopped=true, reason=max_iterations`. With `ceiling=3` the 4th returns
      `reason=max_retries` (factory ceiling evaluated first). One R8 row with the matching `reason`
      is appended in each case.
- [ ] `deadline_seconds: 60`, `now` injected: `stopped=false` at `deadline_start + 59`,
      `stopped=true, reason=deadline` at `deadline_start + 60`; `:deadline_start` is set once and not
      overwritten by later evaluations.
- [ ] `budget_caps: {max_tokens: 1000}`: `:tokens` absent → not tripped; `add_loop_tokens(..., 1000)`
      then evaluate → `stopped=true, reason=max_tokens`.
- [ ] `side_effect_level: 5`, `max_iterations: 10`, `ceiling=3`: 4th evaluation → `stopped=true,
      reason=max_retries` (the factory ceiling wins; the declared 10 never takes effect).
- [ ] `reset_retry(key)` pops `:iter`, `:deadline_start`, `:tokens` alongside the existing triad; the
      next evaluation starts from 0 / unanchored.
- [ ] `evaluate_stop_condition(None, ...)` reads and writes only the existing `<key>` counter — no
      `:loop:` key ever appears in `scheduler-state.json` on the parity path.
- [ ] A 200-character `failure_behavior` reaches the `trip_to_blocked` comment and the R8 row
      truncated to 64 characters.
- [ ] R6 fixture: marker absent → `BLOCKED`, `verdict_gate_check.sh` exit 1; both valid trajectories
      → `PASS`, exit 0; missing artifact → exit 1; cap-class trip unaffected by marker state.
- [ ] `tests/test_adapter.py`: `max_iterations: 0`, `max_iterations: true`, `deadline_seconds: "60"`
      → the exact error strings above; both absent → accepted; `budget_caps.max_retry_spend` still
      accepted and ignored by the evaluator.

## Architecture / Approach

Files touched:

- `scripts/factory_core/adapter.py` — add `max_iterations`/`deadline_seconds` to the `scheduling`
  sub-block's `int_fields` (additive; #301's Consumers preamble pre-authorizes one-line schema
  additions inside an existing block, in their own ticket). Nothing added to `budget_caps`.
- `scripts/factory_core/breaker.py` — new `evaluate_stop_condition(...)` / `StopVerdict`, the three
  namespaced state-key helpers and `add_loop_tokens(...)`, `reset_retry` extended to pop the new
  suffixes, and the `runs.jsonl` write-through on trip (new `run_record` import). **No `subprocess`
  call and no predicate execution** — the predicate class lives in #197's `verifier.py` (R5).
- `scripts/factory_core/run_record.py` — one public helper, `append_stop_record(record)`, wrapping
  the existing `_append_jsonl` (no Seq post).
- `scripts/factory_core/cli.py` — one new thin subcommand, `breaker-evaluate-stop --issue --phase
  --ceiling`, mirroring the existing `breaker-*` family, giving `scheduler.sh` (bash) a way to invoke
  the evaluator.
- `scheduler.sh` — at the four retry sites (`stage_conflict_resolve`, `stage_blocked_retry`,
  `stage_plan`, `stage_refine`) replace only the `count|*` branch's `get_retry_count`/ceiling
  compare/`increment_retry` with the new subcommand (`loop_entry=None`); steps (1)–(3) of R7 and
  `trip_to_blocked` untouched; behavior byte-identical (verified by `test_scheduler*.sh`).
- One new example predicate script (`scripts/` or `tests/fixtures/`) implementing the #300
  cost-report-marker check via `tracker get-comments`, plus its regression test through
  `verifier.py` and the real `verdict_gate_check.sh` (R6). If needed, a one-line additive
  `--gate-type` flag on `verifier.py`'s CLI.
- `tests/test_factory_core_breaker.py`, `tests/test_adapter.py` (new `scheduling` field cases),
  `tests/test_run_record.py` (`append_stop_record`), `tests/test_scheduler*.sh` (parity assertions
  per R7) — extended, not restructured.
- No changes to `config/config.yaml`, `entrypoint.sh`, `epic_autopilot.py`, `error_signature.py`,
  `scripts/verdict_gate_check.sh`, any `gate_*` script, `deploy/**`, or `.factory/adapter.yaml` (R11
  items are named, not built; R12).
- Sequencing: implement waits for #301 (schema) **and** #197 (`verifier.py`/`verdict.py`) to land on
  `main`; the plan phase re-verifies both file/function names against `main` before writing.

## Alternatives considered

1. **Bare exit-code semantics evaluated inside `breaker.py`'s evaluator** (this spec's original R5;
   rejected — 2026-08-29 operator review, R5/R11.3). Would fork #197's `verifier.py` seam into a
   second subprocess runner and reverse #311's owner ruling placing the predicate class at the
   Gate-2-class evaluation point (`emit_verdict` shape, `verdict_gate_check.sh` pattern, never a
   scheduler pre-dispatch predicate), leaving epic #194 with two check-only execution paths. The
   issue's own "exit code is the verdict" intent is preserved through `normalize_verdict`'s
   bare-exit-code mode, so nothing is lost by routing through the one seam — and `breaker.py` gets
   smaller, not larger.
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

- Whether #197's `verifier.py` CLI grows a `--gate-type` flag in #197 itself or via #198's one-line
  additive change (R6); the Python API (`normalize_verdict(..., gate_type=...)`) is sufficient either
  way.
- Whether the entrypoint.sh Done-gate follow-up ticket (R11.2) and the `epic_autopilot.should_advance`
  condition (R11.1) should be one ticket or two — left to whoever files it, once #197's verdict
  artifact shape is merged and concrete.
- `#301` and `#197` are both still on unmerged refine branches as of this spec; the exact field paths
  (`verification.stop_condition`, `scheduling.*`) and #197's `verifier.py`/`verdict.py` function names
  must be re-verified against `main` at plan/implement time in case either spec changes shape before
  merging. The issue body's `Depends on:` lines should gain `#197` (an issue-body edit, outside this
  spec's own file).

## Assumptions (flagged)

- **[ASSUMPTION]** `scheduling.deadline_seconds` is a new field name invented by this spec (#301 names
  `scheduling` as reserved territory for iteration/cadence/retry policy but does not itself name a
  deadline field). Chosen for consistency with `max_iterations` sitting in the same block and to keep
  the loader dependency-free (relative int, no date parsing).
- **[ASSUMPTION]** The per-loop state key shape (`<_make_key(issue, phase)>:loop:<name>:iter`,
  `:deadline_start`, `:tokens`) is a new convention modeled on the file's existing
  `<key>:sig`/`<key>:delivery` suffix pattern; #198 is the first ticket to need a three-part key, and
  the exact separator/ordering is an implementation detail free to adjust as long as `reset_retry`
  pops every new suffix it introduces and the parity path never writes any of them.
- **[ASSUMPTION]** The example contract-satisfaction predicate reuses `tracker get-comments` +
  substring search for the `<!-- dark-factory-cost-report -->` marker, rather than adding a new
  provider-level "find comment by marker" primitive — this mirrors `entrypoint.sh`'s own existing
  idempotent marker-comment-upsert helper's search step closely enough that no new abstraction is
  justified for one fixture.
- **[ASSUMPTION]** "Existing breaker behavior for factory phases is unchanged" (issue AC3) is
  interpreted as an observable-behavior/parity claim (identical dispatch decisions, identical
  comments, identical board transitions), not a literal no-diff constraint on `breaker.py`'s source —
  R7's refactor necessarily changes the code path while preserving the outcome. The one carved-out
  addition is R8's single `runs.jsonl` audit row per trip.

# Adapter schema v2 — research-driven loop metadata blocks (A1.5)

**Issue:** #301 · **Epic:** #194 (Factory/Target boundary v1) · **Depends on:** #195 (A1, shipped)
**Status:** spec-pending-review

## Overview

#195 ("A1") shipped a tracer-bullet `loops:` block in `.factory/adapter.yaml`: each
entry is a flat mapping of 11 required fields (`name`, `purpose`, `trigger`, `inputs`,
`outputs`, `artifacts`, `verifier`, `stop_condition`, `failure_behavior`,
`side_effect_level`, `handoff`), structurally validated with hand-rolled
`isinstance`/`AdapterError` checks and surfaced verbatim in the run record. A1's own
spec explicitly flagged that flat shape as **provisional**, deferring five
research-driven extensions — proposed via the maintainer-authorized "Hermes Agent /
Product Manager" comment channel (CLAUDE.md § Trusted comment channels, PR #299) — to
this ticket, informally "A1.5": `role_card`, `economics`, `skills`, a five-move
restructuring of the loop entry, and a `side_effect_level`-conditional requiredness
rule. This must land **before** #196 (A2) starts building side-effect enforcement
against the loop-entry shape, since the five-move restructuring changes that shape.

Per the operator's 2026-08-28 comment on #301, this is refined and delivered as a
**spec-only deliverable** (parse/validate/surface, no runtime behavior) — the operator
reviews the spec gate directly.

A sixth item arrived after the ticket was filed: a `contract:` completion-contract
extension proposed via a Hermes Agent comment on #311 (Agentic Design Patterns
research). See "Deferred: `contract:`" below for why it is not designed in this spec.

## Requirements

### R1 — Five-move restructuring is a breaking reshape, not additive

The loop entry moves from 11 flat fields to this shape. This is the **only** valid
shape after A1.5 — there is no dual-form/back-compat fallback to the flat A1 form.

```yaml
loops:
  - name: nightly-scan-triage          # unchanged, top-level
    purpose: Triage overnight false positives  # unchanged, top-level
    side_effect_level: 2                # unchanged, top-level
    discovery:
      trigger: 'cron:0 6 * * *'
      inputs: ["scanner_output.json"]
    handoff:
      outputs: ["triage_report.md"]
      handoff: handoffs/triage_handoff.py
    verification:
      verifier: verifiers/triage_verifier.py
      stop_condition: stop_conditions/triage_stop.py
    persistence:
      artifacts: [".factory/state/triage.json"]
    scheduling:
      failure_behavior: escalate_to_human
    # optional:
    # human_checkpoint: "manual-approval:slack-#factory-ops"
    # budget_caps: { max_tokens: 50000 }
    # role_card: {...}
    # economics: {...}
    # skills: {...}
```

Mechanical field mapping (each A1 field keeps its own key name, relocated into its
move's sub-block — chosen for the tightest semantic coupling between fields that
must move together):

| A1 flat field | New location | Rationale |
|---|---|---|
| `name`, `purpose`, `side_effect_level` | stay top-level | identity/classification, not a process move |
| `trigger`, `inputs` | `discovery.{trigger,inputs}` | what starts the loop and what it consumes |
| `outputs`, `handoff` | `handoff.{outputs,handoff}` | what the loop produces and how it's handed off |
| `verifier`, `stop_condition` | `verification.{verifier,stop_condition}` | verifier produces the signal `stop_condition` interprets |
| `artifacts` | `persistence.artifacts` | durable output = persistence, by name |
| `failure_behavior` | `scheduling.failure_behavior` | what happens next on failure is a scheduling concern |

`discovery`, `handoff`, `verification`, `persistence`, `scheduling` are all
**required** sub-blocks on every entry (they jointly carry A1's 8 relocated required
fields); each sub-block's own fields keep A1's exact type rules (non-empty string /
list of strings). Unknown keys inside a sub-block, and unknown top-level-of-entry keys,
remain a hard `AdapterError` — Requirement 5 of #195 extends inward.

**Why a breaking reshape is safe:** `loops:` has zero shipped usage anywhere in this
repo (`.factory/adapter.yaml` has no `loops:` key; `adapter_defaults.DEFAULTS["loops"]`
is `[]`) or in `deploy/instances/**`. "Parity-when-absent/v1 mandatory" binds inputs
that exist in the wild today; no file that loads clean today gains a new error. Loops
are execution-inert until A2–A5, so reshaping now (before any real consumer exists) is
strictly cheaper than reshaping after #196 ships enforcement against the flat form.

### R2 — New optional sub-blocks: `role_card`, `economics`, `skills`

All three are optional at the loop-entry level (omittable — absence is not defaulted
to `{}` in `adapter_defaults.DEFAULTS`, so `run_record.py`'s verbatim passthrough never
fabricates a declaration that wasn't written). When present, each is validated as a
strict mapping — unknown keys inside it are `AdapterError`s — using only the four
primitive checks `adapter.py` already has (non-empty string, list of strings, int
range, and a new plain-bool check; no `jsonschema`, no enums, no floats).

**`role_card`** (Agency Agents pattern) — `name` is the only field required *if* the
block is present (a nameless role card is unusable); the rest may be partially
declared:
| Field | Type |
|---|---|
| `name` | non-empty string (required within the block) |
| `responsibilities` | list of strings |
| `non_responsibilities` | list of strings |
| `output_schema` | non-empty string (opaque reference — not resolved/read, same treatment as `verifier`) |
| `fallback_path` | non-empty string (opaque escalation-route reference, **not** a filesystem path; not existence-checked) |
| `observability` | list of strings (signal/event names the role emits) |

`allowed_tools`/`forbidden_tools` inside `role_card` are **permanently excluded**, not
deferred (see R3).

**`economics`** (Harness Effect, epic #234) — all fields individually optional
(partial declaration is meaningful, e.g. `max_tokens` alone):
| Field | Type |
|---|---|
| `max_tokens` | int, `>= 1`, bool excluded (same guard idiom as `side_effect_level`). No upper ceiling — a ceiling is #196/#234 enforcement policy, out of scope here. |
| `max_retry_spend` | int, `>= 1`, bool excluded. **Token count, not a dollar amount** — `run_record.py`'s `_compute_harness_economics` already emits `retry_spend`/`failure_spend` denominated in tokens; the declaration must match the ledger it feeds. |
| `context_offload_required` | strict `bool` (first bool field in the schema; `0`/`1`/`"yes"` are rejected, not coerced) |
| `feature_demand` | non-empty string, free-form (no enum — no demand vocabulary exists anywhere in the repo today; inventing one here would be #234-family policy made by an inert-schema ticket) |
| `model_capability_floor` | non-empty string, free-form (no enum — the repo already mixes tier labels (`haiku`/`sonnet`/`opus`) and concrete model IDs (`claude-opus-4-8`), and target repos may be non-Anthropic per the provider-abstraction work; an enum would break provider-agnosticism on day one) |

**`skills`** (agent-skills; policy semantics stay with the #42 family — this block only
declares references, it does not define skill naming/`allowed-tools` policy):
| Field | Type |
|---|---|
| `primary` | list of strings (skill names) |
| `supplemental` | list of strings |
| `forbidden` | list of strings |
| `eval_cases` | list of strings (opaque path/ID references, unresolved — no eval-case infrastructure exists in the repo yet) |

None of `primary`/`supplemental`/`forbidden`/`eval_cases` validate against
`.claude/skills/` naming or existence — target-repo skills are target-owned.

### R3 — `role_card.allowed_tools`/`forbidden_tools`: permanent exclusion, not deferral

These are tool allow/deny-list declarations — exactly the "security-sensitive surface"
CLAUDE.md's Trusted comment channels section says comment-channel input may never
authorize, regardless of signature. Unlike `memory_intervention`/`contract`
(deferred to a *future* ticket), these two names get a targeted `AdapterError` that
states they are excluded outright and would require a human-reviewed spec via the
canonical channel — not a ticket number, because there is no ticket to point to; this
is a standing exclusion. Implementation mirrors `_RESERVED_LOOP_FIELDS`'s
"check-before-generic-unknown-field" pattern, one nesting level deeper (inside
`role_card`'s own key loop).

### R4 — Conditional-requiredness: `side_effect_level >= 4`

`side_effect_level`'s 1–6 range has been validated since A1 but never semantically
documented. This spec documents it for the first time (A1.5 still does not *enforce*
banding beyond the one new rule below — full banding enforcement is A2/#196):

| Level | Meaning |
|---|---|
| 1 | Read-only — inspection/analysis/reporting, nothing outlives the process |
| 2 | Ephemeral local writes — scratch files inside the run container, discarded at run end |
| 3 | Reversible target-owned writes — branch commits, PR/issue/label/comment mutation; undone by one `git revert` or API call |
| 4 | Shared-state writes beyond the loop's own branch — default-branch merges, CI/workflow-config edits, outbound notifications; observed by others, reversible only by a compensating action |
| 5 | Non-revertible external effects — deploys, published releases/artifacts, real spend, destructive infra/data operations |
| 6 | Reserved/prohibited surfaces — CLAUDE.md's human-in-the-loop paths (`deploy/instances/**`, `.github/workflows/publish.yml`) and any `gate_*`/breaker/budget weakening |

**N = 4.** Below 3, the rule would fire on essentially every ordinary factory loop
(routine branch commits), making the declaration boilerplate rather than signal; at 5
a loop could merge to the default branch with no declared cap or checkpoint, which
directly contradicts CLAUDE.md's hard limits. `>= 4` gates the upper half of the scale,
mirroring `code_review.block_threshold: high` gating the top half of its own 4-level
scale.

**Rule:** when a loop entry's `side_effect_level >= 4`, both `budget_caps` and
`human_checkpoint` must be present on that entry (else a targeted `AdapterError` naming
the missing field and the triggering `side_effect_level`). The issue text also lists
"verification" as a third required-when-triggered field, but R1's restructuring
already makes `verification` an **unconditionally** required move block on every
entry — so that clause is automatically satisfied for every entry and A1.5 does not
add a second, redundant check for it. This is called out explicitly rather than left
as a silent no-op, since sequencing R1 and R4 in the same ticket is exactly what makes
the issue's third condition vacuous.

New field shapes for the two blocks this rule gates (not specified in the issue body;
kept minimal and consistent with existing primitives):
- `human_checkpoint`: non-empty string (opaque reference to the checkpoint mechanism,
  e.g. `"manual-approval:slack-#factory-ops"` — same opaque-reference treatment as
  `verifier`/`handoff`; resolution is a future ticket's job, not A1.5's).
- `budget_caps`: mapping with `max_tokens` (int `>= 1`, bool excluded — **required**
  within the block, since an empty `budget_caps: {}` would satisfy "present" while
  declaring no actual cap) and `max_retry_spend` (int `>= 1`, bool excluded, optional
  within the block, same semantics as `economics.max_retry_spend`).

`budget_caps` and `economics.{max_tokens,max_retry_spend}` use matching field names by
design but are independent, both-optional-to-populate blocks: `economics` is general
resource-planning metadata (always optional, informational), `budget_caps` is
specifically the declaration this rule gates. Unifying them would require
cross-block-consistency logic (e.g., does `budget_caps.max_tokens` have to match
`economics.max_tokens`?), which is enforcement logic and out of scope for a
parse/validate/surface-only ticket. This tradeoff is documented here rather than
silently resolved either way — a future enforcement ticket (A2/#196 or #234) should
decide whether to unify them.

A1.5 does **not** hard-reject `side_effect_level: 6` — banning level 6 outright is the
other half of the #193 banding policy, explicitly assigned to A2 by A1's own spec. A
level-6 entry is simply subject to the same R4 requirement as 4 and 5.

### R5 — Deferred: `contract:` (not designed in this spec)

The #311 Hermes Agent comment proposes a `contract:` block (objective, scope,
`required_deliverables`, `clarification_policy`, etc.) inside the five-move
restructuring, explicitly self-scoped as "parse/validate/surface-only" and offering an
escape hatch: "if a coherent contract block materially exceeds #301's size, preserve a
rejected/reserved key and let #311 recommend one new child under #194."

#301 is already the product of that exact size-discipline mechanism — A1 deferred
four items here rather than 2–3x its own `size: M` surface, and A1.5 itself already
carries five substantial deliverables (R1–R4 above) including a breaking reshape.
`contract:` as specified is not a leaf field: it is `objective` + `scope` +
`required_inputs` + `accepted_sources` + `required_deliverables` (itself a list with
five sub-fields per entry) + `clarification_policy`, plus its own
"reference-don't-duplicate" cross-block rules against `economics`/scheduling/
`side_effect_level`/`verification`/persistence. Fully designing that is a sixth block
with its own validation and test matrix — the same 2–3x blowout A1 rejected once
already, and the comment's own author pre-authorized deferral for exactly this case.

A1.5 ships only the cheap half: `contract` is added to a per-entry reserved-field
mechanism (mirroring `memory_intervention` → `#241`), rejected with a targeted
`AdapterError`: *"field 'contract' is reserved for a follow-up child of epic #194
(completion-contract extension recommended by #311); not accepted in this schema;
remove it."* No field list, no validation rules — that design work belongs to the
follow-up ticket #311 already routes through, and duplicating it here would be wasted
work reviewed twice.

### R6 — No new dependency; sub-block validation stays hand-rolled

Confirmed by memory precedent from #195's refinement: the adapter loader stays
dependency-free. R1/R2's nested sub-blocks are the first dict-of-typed-fields shapes
in `adapter.py`; implementation should factor one small generic helper (parametrized
by which keys are string/list/int/bool and which are required-within-block) rather
than hand-copying the per-field loop five times, while staying `isinstance`-only.

### R7 — No change to existing parity invariants

Confirmed unaffected by this ticket: no-`loops:` adapters still deep-merge to today's
`DEFAULTS`; `schema_version` stays inert and never gates `loops:` validation; top-level
unknown keys still warn-and-carry; `mechanism_candidates` keeps its existing targeted
top-level reject. `run_record.py`'s `adapter.get(clone_dir, "loops") or []` passthrough
needs no code change — it embeds whatever shape `adapter.py` returns verbatim; only
`tests/test_run_record.py`'s shape assertions see the new nesting.

## Architecture / Approach

All changes are confined to `scripts/factory_core/adapter.py` (plus test-fixture
updates in `tests/test_adapter.py`):

- Replace `_LOOP_REQUIRED_FIELDS`/`_LOOP_STRING_FIELDS`/`_LOOP_LIST_FIELDS` with the
  new top-level-of-entry shape: 3 required scalars (`name`, `purpose`,
  `side_effect_level`) + 5 required sub-block names (`discovery`, `handoff`,
  `verification`, `persistence`, `scheduling`) + 2 optional sub-block names
  (`human_checkpoint` as a scalar, `budget_caps` as a sub-block) + 3 optional
  metadata sub-block names (`role_card`, `economics`, `skills`).
- Add a generic `_validate_subblock(entry, index, name, block, *, str_fields=(),
  list_fields=(), int_fields=(), bool_fields=(), required_fields=())` helper used for
  all eight sub-blocks (five moves + `budget_caps` + the three metadata blocks),
  raising the same indexed/named `AdapterError` style as `_validate_loop` today.
- Extend `_RESERVED_LOOP_FIELDS` with `"contract": "a follow-up child of epic #194
  (completion-contract extension recommended by #311)"`.
- Add a small reserved-field check inside `role_card` validation for
  `allowed_tools`/`forbidden_tools`, with the permanent-exclusion message from R3
  (not epic-numbered, since there is no ticket to defer to).
- Add the R4 conditional check after all per-entry structural validation succeeds:
  `if entry["side_effect_level"] >= 4: require "budget_caps" and "human_checkpoint" in entry`.
- No changes to `adapter_defaults.py` (`"loops": []` stays as-is), `run_record.py`, or
  `entrypoint.sh`.

## Alternatives considered

1. **Keep the flat A1 shape and add the five-move blocks as additive siblings**
   (rejected — R1). A1's own spec already settled this: the restructuring is
   explicitly a re-map, not an addition, and doing it additively would mean A2 builds
   enforcement against two co-existing shapes, or a later ticket has to do the
   breaking reshape anyway after real consumers exist. Doing it now, while `loops:`
   still has zero shipped usage, is strictly cheaper.
2. **Fully design and ship `contract:` in A1.5** (rejected — R5). Would 2–3x the
   ticket's already-substantial surface a second time, against the same
   tracer-bullet/size discipline that produced A1.5 itself.
3. **`jsonschema` dependency for the now-nested schema** (rejected — R6). The
   Dockerfile pins exactly one dependency (`pyyaml`) for this tooling; the nested
   shape is still small and closed enough for one generic hand-rolled helper.
4. **`max_retry_spend` denominated in dollars** (rejected — R2). The only shipped
   consumer (`run_record.py`'s harness-economics block) already measures retry/failure
   spend in tokens; a dollar field would require currency/pricing-table semantics this
   dependency-free loader has no business owning.
5. **Enum vocabularies for `feature_demand`/`model_capability_floor`/`trigger`-style
   fields** (rejected — R2, consistent with A1's precedent). No vocabulary is defined
   anywhere in the repo for any of these; inventing one would freeze policy that
   belongs to #234/#42, not an inert schema ticket.

## Open questions (non-blocking)

- Whether `economics` and `budget_caps` should eventually be unified (R4) is left to
  a future enforcement ticket (A2/#196 or the #234 epic) — A1.5 documents the tradeoff
  but does not resolve it, since resolving it would require cross-block consistency
  logic outside parse/validate/surface scope.
- `human_checkpoint`'s opaque-string format (how a resolver would eventually act on
  it) is undefined, matching `verifier`/`handoff`'s existing opaque-reference
  treatment — resolution is a future ticket's job (mirrors A3/#197's relationship to
  `verifier`).
- Filing the `contract:` follow-up ticket under epic #194 is a maintainer/scheduler
  action outside this refine run's file-output scope (same convention A1 used for
  filing A1.5 itself).

## Assumptions (flagged)

- **[ASSUMPTION]** The `stop_condition`/`failure_behavior` → `verification`/
  `scheduling` split (R1's mapping table) picks `stop_condition` into `verification`
  (tightly coupled to `verifier`'s pass/fail signal) and `failure_behavior` into
  `scheduling` (a retry/escalate policy concern). The prior A1 spec left this pairing
  ambiguous ("stop_condition/failure_behavior→verification/scheduling"); this is a
  refine-time judgment call, not a re-derivation of a settled answer.
- **[ASSUMPTION]** `skills`' eval-case references collapse to a single `eval_cases:
  list[str]` field. The issue body says only "eval-case references" without naming
  sub-categories; inventing multiple category names without a verifiable source would
  be a larger, less-grounded assumption than one generic list.
- **[ASSUMPTION]** `human_checkpoint`/`budget_caps`' exact field shapes (R4) are new
  minimal designs, not sourced from the issue body (which names the blocks but not
  their fields). Kept deliberately small and consistent with existing primitives.
- **[ASSUMPTION]** `role_card`, `economics`, and `skills` are independently optional
  per entry (no requirement that declaring one implies declaring another), per
  "parity-when-absent/v1 mandatory" and #195's byte-identical-parity guarantee —
  making any of them required would invalidate every A1-shaped loop entry.

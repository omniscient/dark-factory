# Adapter schema v2 — research-driven loop metadata blocks (A1.5)

**Issue:** #301 · **Epic:** #194 (Factory/Target boundary v1) · **Depends on:** #195 (A1, shipped)
**Status:** spec-pending-review
**Revised:** 2026-08-28 — operator spec-gate review amendments (side-effect taxonomy
aligned with #193/#196, single home for token caps, consumer map, exact error strings,
acceptance criteria, `handoff.manifest` rename, fail-open precondition, docs in scope).

## Overview

#195 ("A1") shipped a tracer-bullet `loops:` block in `.factory/adapter.yaml`: each
entry is a flat mapping of 11 required fields (`name`, `purpose`, `trigger`, `inputs`,
`outputs`, `artifacts`, `verifier`, `stop_condition`, `failure_behavior`,
`side_effect_level`, `handoff`), structurally validated with hand-rolled
`isinstance`/`AdapterError` checks and surfaced verbatim in the run record. A1's own
spec — archived at `docs/archive/2026-07-07-adapter-schema-v2-loops-design.md`
(sections "Deferred to A1.5" and Alternative 7), with its plan at
`docs/archive/2026-07-17-adapter-schema-v2-loops.md` — explicitly flagged that flat
shape as **provisional**, deferring five research-driven extensions — proposed via the
maintainer-authorized "Hermes Agent / Product Manager" comment channel (CLAUDE.md §
Trusted comment channels, PR #299) — to this ticket, informally "A1.5": `role_card`,
`economics`, `skills`, a five-move restructuring of the loop entry, and a
`side_effect_level`-conditional requiredness rule. This must land **before** #196 (A2)
starts building side-effect enforcement against the loop-entry shape, since the
five-move restructuring changes that shape. (Note: the comment at
`scripts/factory_core/adapter.py:104` still cites the pre-archive
`docs/superpowers/specs/...` path for the A1 spec; the implementer may fix it in
passing.)

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
      manifest: handoffs/triage_handoff.py
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
must move together — with one rename: A1's `handoff` scalar becomes
`handoff.manifest`, because `handoff.handoff` is an awkward key for the field #199
(A5) will consume most, and no consumer exists yet to break):

| A1 flat field | New location | Rationale |
|---|---|---|
| `name`, `purpose`, `side_effect_level` | stay top-level | identity/classification, not a process move |
| `trigger`, `inputs` | `discovery.{trigger,inputs}` | what starts the loop and what it consumes |
| `outputs`, `handoff` | `handoff.{outputs,manifest}` | what the loop produces and how it's handed off |
| `verifier`, `stop_condition` | `verification.{verifier,stop_condition}` | verifier produces the signal `stop_condition` interprets |
| `artifacts` | `persistence.artifacts` | durable output = persistence, by name |
| `failure_behavior` | `scheduling.failure_behavior` | what happens next on failure is a scheduling concern |

`trigger` stays in `discovery` rather than `scheduling` even though the Loop
Engineering source keeps `trigger` top-level and puts `cadence` under `scheduling`:
in A1 `trigger` is an opaque "what starts the loop" reference (cron, event, or manual),
i.e. the discovery entry point, whereas `scheduling` is reserved for the source's
retry/cadence/iteration policy that #198 will populate (see "Consumers" below).

`discovery`, `handoff`, `verification`, `persistence`, `scheduling` are all
**required** sub-blocks on every entry (they jointly carry A1's 8 relocated required
fields); each sub-block's own fields keep A1's exact type rules (non-empty string /
list of strings; empty lists remain valid, as in A1). Unknown keys inside a sub-block,
and unknown top-level-of-entry keys, remain a hard `AdapterError` — Requirement 3 of
#195 (strict unknown-key rejection inside loop entries) extends inward.

Error strings for the new structural checks (exact, for the test matrix):
- `loops[{i}] ('{name}'): missing required block '{block}'`
- `loops[{i}] ('{name}'): block '{block}' must be a mapping`
- `loops[{i}] ('{name}'): block '{block}': unknown field '{key}'`
- `loops[{i}] ('{name}'): block '{block}': missing required field '{key}'`
- `loops[{i}] ('{name}'): block '{block}': field '{key}' must be a non-empty string`
  / `... must be a list of strings` / `... must be an int >= 1` / `... must be a bool`

**Why a breaking reshape is safe:** `loops:` has zero shipped usage anywhere.
Precondition: all adapter consumers fail open (`effective_config.py:49`,
`gate_lib.sh:16-19`, `run_record.py:602-604`) — an `AdapterError` silently drops
EVERY adapter override (safety keywords, `hard_exclude_paths`, memory routing) back to
`adapter_defaults`, so the reshape is only safe if no live adapter can raise on it.
Verified 2026-08-28 that neither `omniscient/dark-factory` nor `omniscient/markethawk`
`.factory/adapter.yaml` declares `loops:` (both are `schema_version: 1` files without
the key; MarketHawk's lives in the MarketHawk repo, `deploy/instances/markethawk/`
holds only `instance.env`), and `adapter_defaults.DEFAULTS["loops"]` is `[]`, so no
live adapter can fail on the reshape and no migration is needed. No edit under
`deploy/**` is required or permitted. "Parity-when-absent/v1 mandatory" binds inputs
that exist in the wild today; no file that loads clean today gains a new error.
Migration rule for any future flat-form entry: relocate per the table above; no
automatic conversion is provided (a flat entry fails with the
`missing required block` error). Loops are execution-inert until A2–A5, so reshaping
now (before any real consumer exists) is strictly cheaper than reshaping after #196
ships enforcement against the flat form.

### R2 — New optional sub-blocks: `role_card`, `economics`, `skills`

All three are optional at the loop-entry level (omittable — absence is not defaulted
to `{}` in `adapter_defaults.DEFAULTS`, so `run_record.py`'s verbatim passthrough never
fabricates a declaration that wasn't written). When present, each is validated as a
strict mapping — unknown keys inside it are `AdapterError`s — using only the three
primitive checks `adapter.py` already has (non-empty string, list of strings, int
range) plus one new plain-bool check; no `jsonschema`, no enums, no floats.
`economics: {}` and `skills: {}` are accepted (declared-empty); `role_card: {}` is
rejected (missing required `name`, see below).

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
(partial declaration is meaningful, e.g. `feature_demand` alone). The Harness Effect
brief's two cap fields (`max_tokens`, `max_retry_spend`) are **not** in this block:
`budget_caps` (R4) is the sole declaration point for token caps, under Loop
Engineering's block name — nothing is dropped, the caps simply have one home.
| Field | Type |
|---|---|
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
`forbidden` is declared-but-unenforced metadata only. Any future enforcement of
`skills.{primary,supplemental,forbidden}` as a skill allow/deny surface requires a
human-reviewed spec on a branch (CLAUDE.md § Trusted comment channels); #196 may not
enforce it on comment-channel authority.

### R3 — `role_card.allowed_tools`/`forbidden_tools`: permanent exclusion, not deferral

These are tool allow/deny-list declarations — exactly the "security-sensitive surface"
CLAUDE.md's Trusted comment channels section says comment-channel input may never
authorize, regardless of signature. Unlike `memory_intervention`/`contract`
(deferred to a *future* ticket), these two names get a targeted `AdapterError` that
states they are excluded outright and would require a human-reviewed spec via the
canonical channel — not a ticket number, because there is no ticket to point to; this
is a standing exclusion. Implementation mirrors `_RESERVED_LOOP_FIELDS`'s
"check-before-generic-unknown-field" pattern, one nesting level deeper (inside
`role_card`'s own key loop). Exact message:

`loops[{i}] ('{name}'): role_card field '{key}' is a tool allow/deny declaration and is permanently excluded from adapter.yaml (CLAUDE.md § Trusted comment channels); remove it`

### R4 — Conditional-requiredness: `side_effect_level >= 4`

`side_effect_level`'s 1–6 range has been validated since A1 but never documented in
the schema. Level semantics are **owned by #193/#196** and reproduced here verbatim,
not redefined (A1.5 still does not *enforce* banding beyond the one new rule below —
full banding enforcement is A2/#196):

| Level | Meaning (#193 "Suggested side-effect levels", adopted by #196) |
|---|---|
| 1 | read-only research |
| 2 | artifact writing |
| 3 | GitHub ticket creation |
| 4 | code modification |
| 5 | PR creation |
| 6 | external production side effect (A2 rejects 6 at validation; A1.5 does not) |

**N = 4** because it coincides with #193's ownership boundary: levels 1–3 are
target-definable (if auditable), 4–5 are factory-owned, so the first factory-owned
level is where a declared cap and human checkpoint become mandatory. `>= 4` gates the
upper half of the scale, mirroring `code_review.block_threshold: high` gating the top
half of its own 4-level scale (`config/config.yaml`).

**Rule:** when a loop entry's `side_effect_level >= 4`, both `budget_caps` and
`human_checkpoint` must be present on that entry, else a targeted `AdapterError` —
one error per missing field, `budget_caps` checked first:

`loops[{i}] ('{name}'): side_effect_level {sel} >= 4 requires '{field}'`

The issue text also lists "verification" as a third required-when-triggered field,
but R1's restructuring already makes `verification` an **unconditionally** required
move block on every entry — so that clause is automatically satisfied for every entry
and A1.5 does not add a second, redundant check for it. This is called out explicitly
rather than left as a silent no-op, since sequencing R1 and R4 in the same ticket is
exactly what makes the issue's third condition vacuous.

New field shapes for the two fields this rule gates (not specified in the issue body;
kept minimal and consistent with existing primitives):
- `human_checkpoint`: optional top-level-of-entry **scalar**, non-empty string (opaque
  reference to the checkpoint mechanism, e.g. `"manual-approval:slack-#factory-ops"` —
  same opaque-reference treatment as `verifier`/`handoff.manifest`; resolution is a
  future ticket's job, not A1.5's).
- `budget_caps`: optional top-level-of-entry **sub-block**, mapping with `max_tokens`
  (int `>= 1`, bool excluded — **required** within the block, since an empty
  `budget_caps: {}` would satisfy "present" while declaring no actual cap) and
  `max_retry_spend` (int `>= 1`, bool excluded, optional within the block).
  `max_retry_spend` is a **token count, not a dollar amount** — `run_record.py`'s
  `_compute_harness_economics` already emits `retry_spend`/`failure_spend`
  denominated in tokens; the declaration must match the ledger it feeds. No upper
  ceiling on either — a ceiling is #196/#234 enforcement policy, out of scope here.

`budget_caps` is the **sole** declaration point for token caps in the schema:
`economics` carries no cap fields (R2), and #198's `max_tokens` stop condition MUST
read `budget_caps.max_tokens` rather than add a field of its own (see "Consumers").

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

A1.5 ships only the cheap half: `contract` is added to the per-entry reserved-field
mechanism (`_RESERVED_LOOP_FIELDS`, mirroring `memory_intervention` → `#241`),
rejected with a targeted `AdapterError`. Today's message template hardcodes
`f"reserved for epic {value} (per-loop memory intervention)"`, which would not render
for a second entry, so the mechanism is generalized: `_RESERVED_LOOP_FIELDS` values
become full descriptions and the message becomes
`loops[{i}] ('{name}'): field '{key}' is reserved for {desc} and is not accepted in this schema; remove it`, with
- `"memory_intervention": "epic #241 (per-loop memory intervention)"` — keeps
  `test_loop_entry_memory_intervention_reserved_raises`'s `reserved for epic #241`
  match intact;
- `"contract": "a follow-up child of epic #194 (completion-contract extension recommended by #311)"`.

No field list, no validation rules — that design work belongs to the follow-up ticket
#311 already routes through, and duplicating it here would be wasted work reviewed
twice.

### R6 — No new dependency; sub-block validation stays hand-rolled

Confirmed by memory precedent from #195's refinement (`.archon/memory/architecture.md`):
the adapter loader stays dependency-free. R1/R2's nested sub-blocks are the first
dict-of-typed-fields shapes in `adapter.py`; implementation should factor one small
generic helper (parametrized by which keys are string/list/int/bool and which are
required-within-block) rather than hand-copying the per-field loop five times, while
staying `isinstance`-only.

### R7 — No change to existing parity invariants

Confirmed unaffected by this ticket: no-`loops:` adapters still deep-merge to today's
`DEFAULTS`; `schema_version` stays inert and never gates `loops:` validation; top-level
unknown keys still warn-and-carry; `mechanism_candidates` keeps its existing targeted
top-level reject. `run_record.py`'s `adapter.get(clone_dir, "loops") or []` passthrough
needs no code change — it embeds whatever shape `adapter.py` returns verbatim; only
`tests/test_run_record.py`'s shape assertions
(`test_assemble_surfaces_loops_from_adapter`) see the new nesting.

`schema_version` remains `1` in `.factory/adapter.yaml`; no v1→v2 migration step
exists because `load()` (`adapter.py:90`, `102-108`) validates `loops:` regardless of
`schema_version`. No new top-level key is introduced, so `_KNOWN_TOP` is unchanged.

## Consumers (binding for #194 children)

This is the shape A2–A5 build against. Additive fields inside an existing sub-block
are non-breaking for A2–A5 (strict unknown-key rejection means each addition is a
one-line schema change in its own ticket); renaming or moving any field after A1.5
requires a new spec.

| Ticket | Consumes | Notes |
|---|---|---|
| #196 (A2) | `side_effect_level`, the R4 rule, `human_checkpoint`, `budget_caps` | Owns level semantics (#193 scale above); adds rejection of level 6; maps levels to enforced permission profiles |
| #197 (A3) | `verification.verifier` | Opaque string reference resolved by A3; maker≠checker rule enforced there, not in the schema |
| #198 (A4) | `verification.stop_condition`, `scheduling.failure_behavior`, `budget_caps.max_tokens` | A4 may widen `stop_condition` from string to string-or-mapping (additive); `scheduling` is the reserved home for the Loop Engineering source's `cadence`/`retry_policy`/`max_iterations`; the `max_tokens` stop condition reads `budget_caps.max_tokens` |
| #199 (A5) | `handoff.manifest`, `handoff.outputs`, `persistence.artifacts` | Manifest format is A5's; the schema only carries the reference |
| #234 family | `economics.*` | Informational; wiring into `harness_economics` is #234/#236–#240 |
| #42 family | `skills.*` | Policy semantics (naming, `allowed-tools`) stay with the skills-policy family |

## Acceptance criteria

All exercised by `tests/test_adapter.py` (plus the one `tests/test_run_record.py`
shape update); `python -m pytest tests/ -v` green.

- [ ] The R1 example entry (with and without every optional field) parses and is
      returned verbatim by `adapter.get(clone_dir, "loops")`.
- [ ] Each of the five move blocks missing → `missing required block '{block}'`;
      a block that is not a mapping → `block '{block}' must be a mapping`.
- [ ] Each relocated field missing from its block → `missing required field`; each
      wrong-typed → the matching type error (parametrized over all fields).
- [ ] A flat A1-shaped entry (e.g. today's `tests/test_run_record.py` fixture) fails
      with `missing required block 'discovery'`.
- [ ] Unknown key inside any sub-block, and unknown top-level-of-entry key → error.
- [ ] Each `role_card`/`economics`/`skills`/`budget_caps` field wrong-typed → error
      (parametrized); `context_offload_required: 1` and `"yes"` → `must be a bool`;
      `budget_caps.max_tokens: true` → `must be an int >= 1`.
- [ ] `role_card: {}` → `missing required field 'name'`; `economics: {}` and
      `skills: {}` accepted; `budget_caps: {}` → `missing required field 'max_tokens'`.
- [ ] `role_card.allowed_tools` / `forbidden_tools` → the R3 message.
- [ ] `side_effect_level: 4` (and 5, 6) without `budget_caps` → the R4 message naming
      `budget_caps`; with `budget_caps` but no `human_checkpoint` → naming
      `human_checkpoint`; `side_effect_level: 3` without either → accepted.
- [ ] `contract:` on an entry → the R5 message; `memory_intervention` still matches
      `reserved for epic #241`.
- [ ] Duplicate loop names, `loops:` not a list, entry not a mapping → unchanged A1
      errors.
- [ ] No-`loops:` fixtures, `.factory/adapter.yaml`, and an absent adapter file still
      load byte-identical to today (`test_unknown_keys_warn_not_fail`,
      `test_loops_default_is_empty_list`, parity tests unchanged).
- [ ] README "adapter.yaml keys" table gains a `loops` row and its `schema_version`
      row reads "integer, inert (never gates validation)".

## Architecture / Approach

Files touched: `scripts/factory_core/adapter.py`, `tests/test_adapter.py`,
`tests/test_run_record.py` (one fixture/assertion reshaped), `README.md` (adapter
table). No changes to `adapter_defaults.py` (`"loops": []` stays as-is),
`run_record.py`, or `entrypoint.sh`.

- Replace `_LOOP_REQUIRED_FIELDS`/`_LOOP_STRING_FIELDS`/`_LOOP_LIST_FIELDS` with the
  new top-level-of-entry shape: 3 required scalars (`name`, `purpose`,
  `side_effect_level`) + 5 required sub-blocks (`discovery`, `handoff`,
  `verification`, `persistence`, `scheduling`) + 1 optional scalar
  (`human_checkpoint`) + 1 optional sub-block (`budget_caps`) + 3 optional metadata
  sub-blocks (`role_card`, `economics`, `skills`).
- Add a generic `_validate_subblock(entry, index, name, block, *, str_fields=(),
  list_fields=(), int_fields=(), bool_fields=(), required_fields=())` helper used for
  all nine sub-blocks (five moves + `budget_caps` + the three metadata blocks),
  raising the indexed/named `AdapterError` strings listed in R1.
- Generalize `_RESERVED_LOOP_FIELDS` to full-description values and add the
  `contract` entry (R5).
- Add a small reserved-field check inside `role_card` validation for
  `allowed_tools`/`forbidden_tools`, with the permanent-exclusion message from R3
  (not epic-numbered, since there is no ticket to defer to).
- Add the R4 conditional check after all per-entry structural validation succeeds:
  `if entry["side_effect_level"] >= 4: require "budget_caps" then "human_checkpoint" in entry`.
- README: add a `loops` row to the "adapter.yaml keys" table pointing to this spec's
  R1 shape; correct the `schema_version` row (currently "Must be `1`", stale since A1)
  to "integer, inert".

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
   Dockerfile pins no schema library (`pyyaml` is the only YAML/config dependency
   for this tooling); the nested shape is still small and closed enough for one
   generic hand-rolled helper.
4. **`budget_caps.max_retry_spend` denominated in dollars** (rejected — R4). The only
   shipped consumer (`run_record.py`'s harness-economics block) already measures
   retry/failure spend in tokens; a dollar field would require currency/pricing-table
   semantics this dependency-free loader has no business owning.
5. **Enum vocabularies for `feature_demand`/`model_capability_floor`/`trigger`-style
   fields** (rejected — R2, consistent with A1's precedent). No vocabulary is defined
   anywhere in the repo for any of these; inventing one would freeze policy that
   belongs to #234/#42, not an inert schema ticket.
6. **Duplicate `max_tokens`/`max_retry_spend` in both `economics` and `budget_caps`**
   (rejected — R2/R4, operator review). Two identically named fields in two blocks —
   with a third (`stop_condition.max_tokens`) planned by #198 — would force every
   consumer to define cross-block precedence; a foundation schema gets one home.
7. **Redefine the `side_effect_level` scale in this spec** (rejected — R4, operator
   review). #193 already defines it and #196 is chartered to enforce it; a second
   definition here is exactly the reopening this ticket exists to prevent.

## Open questions (non-blocking)

- `human_checkpoint`'s opaque-string format (how a resolver would eventually act on
  it) is undefined, matching `verifier`/`handoff.manifest`'s existing opaque-reference
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

# README `safety.*` adapter-table accuracy fix

**Operator spec gate:** 2026-09-10 — approved with amendments. Every per-row finding below was
independently re-verified against `origin/main` at the gate (see the disposition comment); all
six hold. One wording amendment, to keep the fix from introducing a fresh overstatement of its
own.

**Issue:** #415

## Overview / Problem statement

`README.md`'s "adapter.yaml keys" table (lines 166-171) describes the `safety.*` keys a
target repo can set in `.factory/adapter.yaml`. The `safety.hard_exclude_paths` row claims:

> Path prefixes the factory will never touch; **matched diff paths abort the run**.

Neither half is true. Its only consumer, `scripts/factory_core/epic_autopilot.py::hard_excluded`,
filters **epic-autopilot candidate tickets** — it never inspects a diff or aborts a run — and
`config/config.yaml` ships `epic_autopilot.enabled: false`, so today the consumer doesn't even
run. What actually holds a path boundary like `deploy/instances/**` is two other mechanisms:
CLAUDE.md's Hard limits (agent-facing policy) and `.factory/adapter.yaml`'s
`migration_seed_auth_patterns`, which `scripts/gate_blast_radius.py` reads to produce
`HUMAN_REQUIRED`. `docs/factory-target-boundary.md` (merged under #201, OD3) already states this
accurately; the README row is the one place still overstating it.

The issue asked to also check the table's other four `safety.*` rows for the same kind of drift.
An audit (file:line evidence in Requirements below) found two more rows with the *same* pattern
(mechanically accurate, but the underlying feature is off by default) and two rows with a
*different, more severe* pattern (the row names the wrong consumer, or the adapter key has no
live consumer at all). One row (`migration_seed_auth_patterns`) is accurate as-is.

This is a documentation-only fix: rewrite table cells to describe what the code does today. No
code, config, or gate behavior changes — per the issue's scope note and CLAUDE.md's "gate changes
get their own ticket," making any of these keys actually do what its current README text claims
is explicitly out of scope here.

## Requirements (from Q&A)

A product-owner review confirmed the full-table scope and the wording approach (full dialogue in
the pipeline comment); the resolutions below are load-bearing.

1. **Fix all five `safety.*` rows in this single pass, not just `hard_exclude_paths`.** The issue
   authorizes checking neighboring rows "for the same kind of drift," and the unit of work is six
   lines of one table in one file — splitting it into follow-up tickets would leave rows the
   factory already knows are wrong sitting next to a freshly corrected one, which is worse for a
   reader than the current uniformly-stale state. Still `size: S`: no code changes, one file.
2. **Where a row's mechanism is real but gated behind a disabled-by-default flag, name the flag**
   (`epic_autopilot.enabled`, `main_red_autofix.enabled`) rather than writing a bare
   "(disabled by default)" — a flag name is greppable against `config/config.yaml` and won't rot
   silently the way the original prose claim did.
3. **Per-row findings and required corrections:**
   - `safety.hard_exclude_paths` (the issue's own finding) — only consumer is
     `epic_autopilot.py::hard_excluded` (candidate-ticket filter, not a diff/run check), gated by
     `epic_autopilot.enabled` (`config/config.yaml:77`, ships `false`). Point at
     `docs/factory-target-boundary.md`'s existing OD3 explanation instead of re-deriving it inline
     — the README table is an index, the boundary doc is the reference (it already carries this
     row's history, e.g. `docs/factory-target-boundary.md:296-303`).
   - `safety.sensitive_keywords` — real consumer is `epic_autopilot.py::_sensitive_keywords()`
     (`adapter.get(..., "safety.sensitive_keywords")`) feeding `hard_excluded()`, gated by the
     same `epic_autopilot.enabled: false`. (`scripts/architecture_slice.py` has a same-named
     module constant, but it is a baked `adapter_defaults.DEFAULTS` fallback used only when
     `config.yaml`'s own `epic_autopilot.sensitive_keywords` is absent — it never reads a target's
     `.factory/adapter.yaml`, so it is not a second consumer of this adapter key and the row must
     not imply one.)
   - `safety.dispatch_ceiling_keywords` — **no live consumer reads this adapter key at all.** The
     real dispatch-ceiling gate (`scripts/scheduler_lib.sh::is_above_ceiling`, wired at
     `scheduler.sh:1170`) reads `dispatch_ceiling.keywords` from `config/config.yaml` (env
     `ABOVE_CEILING_KEYWORDS`, `scheduler.sh:79`) — never `.factory/adapter.yaml`.
     `architecture_slice.py`'s same-named constant is the same baked-default, non-adapter-reading
     pattern as the `sensitive_keywords` case above.

     **Precision required in the replacement text (operator amendment).** Write that *the adapter
     key* is never read — i.e. a target setting `safety.dispatch_ceiling_keywords` in its own
     `.factory/adapter.yaml` has no effect — **not** that the key has "no consumer at all."
     `scripts/architecture_slice.py:83` does read
     `adapter_defaults.DEFAULTS["safety"]["dispatch_ceiling_keywords"]`, and
     `tests/test_adapter.py:1134` pins that default against `config/config.yaml`. A flat "unused"
     claim would be a *new* false absolute in the row — which is the exact failure mode this
     ticket exists to remove, and it would be worse than the drift it replaces because it would
     read as freshly audited. Separately, "L tickets parked" is also wrong:
     `is_above_ceiling()` parks size XL unconditionally, or size M only when the title matches the
     keyword pattern — L is never parked by this logic. The row must state plainly that this
     adapter key is currently unused and point at `config/config.yaml`'s `dispatch_ceiling.keywords`
     as the actual knob, and must not repeat the L-tickets claim.
   - `safety.critical_diff_paths` — README attributes this to "the blast-radius gate"; the actual
     sole functional consumer is `scripts/diff_rank.py::classify_file`, which ranks/prioritizes the
     diff fed to code-review/conformance reviewers. `scripts/gate_blast_radius.py` (the real
     blast-radius gate) has its own `classify_file` driven by `migration_seed_auth_patterns` and
     never reads `critical_diff_paths`. This key also carries the non-overridable
     `FACTORY_OWNED_CRITICAL_DIFF_FLOOR` (#200/A6, `scripts/factory_core/adapter.py::_apply_boundary_floor`),
     unioned in on every adapter load regardless of a target's own list — worth a short mention
     since it's the one `safety.*` list a target cannot shrink below the floor.
   - `safety.migration_seed_auth_patterns` — verified accurate (`gate_blast_radius.py:178-190` →
     `HUMAN_REQUIRED`, `blast_radius.enabled: true` by default, unsuppressible per
     `gate_blast_radius.py:336`). No change.
   - `safety.main_red_allowed_paths` — mechanically accurate (`main_red_fixer.py`), but gated by
     `main_red_autofix.enabled` (`config/config.yaml:117`, ships `false`); flipping that config
     value alone is also insufficient — `config/config.yaml:114-116` documents that the dispatched
     fixer additionally requires `MAIN_RED_AUTOFIX_ENABLED=true` in `.archon/.env`. Row should name
     both conditions.
4. **The `dispatch_ceiling_keywords` dead-key finding is a code question, not a docs question** —
   should this adapter key be wired up to the real gate, or removed from
   `adapter_defaults.py`/`.factory/adapter.yaml`'s schema? Out of scope for this ticket (code
   change, needs its own reviewed spec per CLAUDE.md). Document current reality only; flagged
   below under Open questions as a candidate follow-up.
5. **No other files change.** `docs/factory-target-boundary.md` already carries the accurate
   explanation and needs no edit. No test pins the exact README row wording (confirmed:
   `tests/test_factory_target_boundary_doc.py::test_readme_links_to_boundary_doc_near_loops_row`
   only checks that the existing `docs/factory-target-boundary.md` link stays within a few lines
   of the `loops` row, which this change does not move).

## Architecture / Approach

Edit only the six `safety.*`-adjacent table rows in `README.md`'s `### adapter.yaml keys` section
(currently lines 166-171). No code, config, or test changes. Row-by-row target text:

- `safety.sensitive_keywords`: describe the real consumer (`epic_autopilot.py::hard_excluded` via
  `_sensitive_keywords()`) and name the gating flag `epic_autopilot.enabled` (ships `false`).
- `safety.hard_exclude_paths`: describe the candidate-ticket-filter mechanism, name the gating
  flag, and point at `docs/factory-target-boundary.md` for the fuller boundary explanation instead
  of re-deriving it.
- `safety.dispatch_ceiling_keywords`: state that **this adapter key is not read** — setting it in
  a target's `.factory/adapter.yaml` has no effect — and point at `config/config.yaml`'s
  `dispatch_ceiling.keywords` (env `ABOVE_CEILING_KEYWORDS`) as the real knob. Do not write "unused"
  or "no consumer": the baked default *is* consumed by `scripts/architecture_slice.py:83`. Correct
  the parking rule too (XL always; M only on keyword match; **L never** — verified at
  `scripts/scheduler_lib.sh:44-53`).
- `safety.critical_diff_paths`: attribute to `scripts/diff_rank.py` (review ordering), not the
  blast-radius gate, and note the non-overridable factory-owned floor it carries.
- `safety.migration_seed_auth_patterns`: unchanged.
- `safety.main_red_allowed_paths`: name the gating flag `main_red_autofix.enabled` (ships `false`)
  and the additional `.archon/.env` requirement.

Table cells stay terse (existing table style is one sentence to two clauses per cell); mechanism
names and flag names are inline code spans so they stay greppable, matching the existing table's
convention (e.g. the `loops` row's inline references).

## Alternatives considered

- **Fix only `hard_exclude_paths`, file follow-ups for the other four rows.** Rejected per
  Requirement 1 — four separate refine/plan/implement/review cycles to change one table row each,
  while leaving known-wrong rows next to a freshly corrected one, is worse for the reader and out
  of proportion to a `size: S` docs fix.
- **Rewrite the whole `## Adapter contract` section, not just the table.** Rejected — the
  surrounding prose and the `docs/factory-target-boundary.md` pointer are already accurate; only
  the `safety.*` table cells carry the drift the issue identified.
- **Also fix the code-level dead key (`dispatch_ceiling_keywords`).** Rejected — a behavior change
  (wiring the key up or removing it) needs its own reviewed spec per CLAUDE.md's "gate changes get
  their own ticket," and this ticket's scope note explicitly rules out behavior changes.

## Open questions (non-blocking)

- Should `safety.dispatch_ceiling_keywords` be wired up to `scheduler_lib.sh::is_above_ceiling`,
  or removed from `adapter_defaults.py`'s schema as dead config? Recommend filing this as a
  separate follow-up ticket; it's a functional/code decision, not something this docs-only pass
  should resolve or implement.

## Verified at the operator spec gate (2026-09-10)

Each per-row finding was re-resolved against `origin/main` rather than accepted from this spec:

- `dispatch_ceiling_keywords` — no code reads the adapter key. The only non-doc, non-test hits are
  `scripts/architecture_slice.py:83` and `scripts/factory_core/adapter_defaults.py:54`, both the
  baked default. Confirmed.
- `critical_diff_paths` — `scripts/diff_rank.py:61,73,78` is the sole adapter-reading consumer.
  `scripts/gate_blast_radius.py::classify_file` (`:178-191`) iterates only
  `_migration_seed_auth_patterns()` and never touches `critical_diff_paths`. The README's
  blast-radius attribution is wrong; the likely source of the confusion is
  `adapter_defaults.py:3-7`, whose comment names both lists in one breath while describing the
  shared `SKILL_SECURITY_TOKENS` sub-classifier. Confirmed.
- Parking rule — `scripts/scheduler_lib.sh:48-52`: `XL) return 0`, `M) grep -qiE keywords`,
  `*) return 1`. L falls to the wildcard and is never parked. The README row is backwards.
  Confirmed.
- Gating flags — `config/config.yaml:77` `epic_autopilot.enabled: false`; `:117`
  `main_red_autofix.enabled: false`, with `:114-116` documenting that the dispatched container
  reads `.archon/.env`, so flipping the config value alone does not enable it. Confirmed.
- README row under repair is `README.md:168`. Confirmed.

## Assumptions

- The `docs/factory-target-boundary.md` `hard_exclude_paths`/OD3 explanation (merged under #201)
  is treated as the current source of truth for that mechanism and is not re-verified line-by-line
  beyond the confirmations already captured in Requirements — it was independently audited during
  #201's own spec gate.
- No other `safety.*`-adjacent README content (e.g. the `Adapter contract` prose above the table)
  needs correction; the audit was scoped to the table's six `safety.*` cells per the issue's
  explicit ask.

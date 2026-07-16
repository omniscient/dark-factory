# Per-Run Harness Economics Ledger and CPM Scorecard

**Issue:** omniscient/dark-factory#235
**Status:** draft — pending review
**Depends on / consumes:** omniscient/dark-factory#208 (transparent model proxy + request-ledger,
merged — `scripts/factory_core/model_proxy.py`)
**Parent epic:** omniscient/dark-factory#234 ("Harness economics — make Dark Factory token-efficient
by design")
**Related:** omniscient/dark-factory#207 (Archon Web UI cockpit spike, spec-pending-review, no code
yet — a future *consumer* of this data, not built here); omniscient/dark-factory#190 (always-on
state-governance scorecard, spec-pending-review, no code yet — another future consumer); a
comment on #235 proposes `memory_intervention` fields tied to omniscient/dark-factory#241
(proactive-memory epic — does not exist yet; see [Non-goals](#non-goals))

---

## Overview / Problem Statement

Dark Factory now measures raw per-request model traffic (#208's `request-ledger.jsonl`) and
per-run DAG-node cost/token totals plus gate verdicts (`scripts/factory_core/run_record.py` →
`run-record.json`), but nothing turns either into an answer to "was this run's spend worth it."
Per the epic's own gap analysis: *"#208 measures requests, but not outcome-weighted CPM."* The
reference paper's central claim — "the harness, not only the model, sets the price of agentic
work" — is only actionable if Dark Factory can compute a comparable, outcome-weighted cost metric
per run, and can tell a genuinely clean, low-cost run apart from a run that was cheap only because
it produced bad or blocked work.

This ticket adds that aggregation layer: a `harness_economics` block computed at the end of every
run and attached to the existing `run-record.json`, plus a read-only cross-run query for
issue/phase rollups. It does not build a UI (#207's job), does not build a persistent
state-governance store (#190's job), and does not compute independent dollar figures (Archon
already does that, see [Cost source](#cost-source-no-new-pricing-table)).

**Non-goals** (see also [Non-goals](#non-goals) below):
- A new `$`/token pricing table. All dollar figures reuse Archon's existing `cost_usd`.
- A new persistent cross-run artifact/database (`issue-economics.json` or similar). Cross-run
  rollups are computed on demand from existing durable stores.
- `memory_intervention` fields from the #241 proactive-memory proposal — no source subsystem
  exists yet to populate them.
- Any change to `deploy/instances/**` (hard-excluded, human-only).
- A cockpit/dashboard UI (#207) or a governance scorecard product (#190) — this ticket produces
  the data those tickets will consume.

## Requirements

Distilled from the issue's 5 acceptance criteria and the Q&A below:

1. Every run gets a `harness_economics` block, computed once at end-of-run and attached to that
   run's `run-record.json` — no separate persisted file, no independent cost pipeline.
2. `harness_economics` is always present, even when #208's model-proxy ledger has no rows for the
   run (it's opt-in, default off) — a nullable `ledger_mechanics` sub-object plus an explicit
   `ledger_available` flag distinguish "measured zero" from "not measured."
3. A deterministic, documented `outcome.state` / `outcome.score` policy computed from run-record's
   existing gate-stage verdicts, such that a blocked or failed run cannot score well regardless of
   how few tokens it used.
4. `factory_cpm` (outcome-weighted successful work per million tokens), `cost_per_task`,
   `tokens_per_task`, `retry_spend`, and `failure_spend`, computed per the formulas in
   [Data Model](#data-model).
5. A read-only, on-demand cross-run/issue/phase rollup query over #208's ledger (no new persisted
   artifact) for "how much did issue #N cost across all its runs."
6. Historical backfill: a best-effort, degrade-only recomputation of `harness_economics` for runs
   whose `run-record.json` still exists on disk, using whatever ledger rows have not yet rotated
   away.
7. `harness_economics` must be computed on the failure path too, not only the success path — see
   [Wiring `cmd_assemble` into `on_failure`](#wiring-cmd_assemble-into-on_failure).

## Architecture / Approach

### Storage: extend `run-record.json`, not a new file

`harness_economics` is added as a new top-level key inside the object `cmd_assemble()` already
builds (`scripts/factory_core/run_record.py:285-300`), populated after `stages`/`nodes`/`totals`
are computed. This was the single largest scope question in Q&A: the issue's own Scope section
names an illustrative `harness_economics.json`, but the Acceptance Criteria normatively require
persisting into "existing run-record/scorecard paths, not a separate source of truth." A sibling
file would still need `run_id` correlation and would duplicate the assemble step; folding the data
into the artifact that already carries the exact substrate (`totals`, `nodes[]`, `stages[]`)
avoids a second, independently-computed source that could drift from the canonical one.

`entrypoint.sh`'s `post_cost_report()` (already the sole durable, cross-run-visible surface, via
the `<!-- dark-factory-cost-report -->` marked GitHub comment) is extended to read
`harness_economics.factory_cpm` / `.outcome.state` from the same `run-record.json` it already
parses (`entrypoint.sh:309-336`) and add one line to the per-run table — no new file, no new read
path.

### Cost source: no new pricing table

The #208 ledger (`model_proxy.py`) has no `cost_usd` field and no `$`/token table exists anywhere
in this repo — every existing `cost_usd` value is a pass-through from Archon's own
`archon workflow cost --json` (`_parse_archon_cost()`, `run_record.py:142-186`). This ticket does
**not** add a pricing table. All dollar figures (`cost_per_task`, the dollar half of
`factory_cpm`'s inputs) reuse `run-record.json`'s existing `totals.cost_usd` /
`nodes[].cost_usd` exclusively. The #208 ledger is used only for what it uniquely provides:
token/cache/tool/retry *mechanics* — never an independently computed dollar figure. This keeps
exactly one authoritative cost source per run, consistent with this repo's existing pattern of
deferring pricing to upstream tools (Archon, and — for eval scripts — the Claude Code CLI's own
reported cost).

One naming mismatch to handle: `nodes[].model` is stripped of the `claude-` prefix and date
suffix (e.g. `sonnet-4-6`) by `_parse_archon_cost`, while ledger rows keep the full Anthropic
model string (e.g. `claude-sonnet-4-6-20251101`). Any code joining the two data sources by model
name must normalize through the same stripping rule `_parse_archon_cost` already uses.

### Data model

`harness_economics` block, attached inside `run-record.json`:

```jsonc
{
  // ... existing run-record.json fields (run_id, issue_number, intent, status, stages, nodes, totals, ...)
  "harness_economics": {
    "policy_version": "1.0",
    "cost_per_task": 0.0,          // = totals.cost_usd (one run == one task)
    "tokens_per_task": 0,          // = totals.gen_ai.usage.{input_tokens+output_tokens}
    "wall_clock_seconds": 0,       // completed_at - started_at, in seconds
    "outcome": {
      "state": "delivered_clean",  // enum — see below
      "score": 1.0,                 // float [0.0, 1.0]
      "evidence": {
        "status": "completed",
        "gate_stages": [ /* {stage, verdict, cycles?|blockers?|advisory?} lifted from stages[] */ ],
        "penalties": [ /* {reason, count, delta} — empty if none */ ],
        "ungated": false
      }
    },
    "factory_cpm": null,           // outcome.score * 1_000_000 / tokens_per_task, or null if tokens_per_task == 0
    "retry_spend": { "tokens": 0, "request_count": 0 },   // ledger-derived, null fields if !ledger_available
    "failure_spend": { "tokens": 0, "basis": "retry_only" },  // "whole_run" when outcome.state in {failed, blocked}
    "ledger_available": false,
    "ledger_rows_correlated": 0,
    "ledger_mechanics": null       // or {cache_hit_ratio, tool_schema_overhead_bytes, system_prompt_bytes, largest_tools} when ledger_available
  }
}
```

#### Outcome-score policy (documents the required "outcome-score policy")

`outcome.state` — deterministic enum, evaluated in this precedence order against the run's
existing `status` and `stages[]` (already parsed by `_parse_artifact_stage`,
`run_record.py:189-258`):

| state | trigger |
|---|---|
| `failed` | `status != "completed"` |
| `blocked` | `status == "completed"` and any gate stage is non-passing (`validation == FAIL`, `conformance == BLOCKED`, or `review == BLOCKED`) |
| `produced_ungated` | `status == "completed"`, zero gate stages present (e.g. a `refine`/`plan` run that only produced a spec/plan doc) |
| `delivered_clean` | `status == "completed"`, ≥1 gate stage present, all pass/`RESOLVED`, zero friction signals (no `conformance.cycles`, no `review.advisory`) |
| `delivered_with_findings` | same as `delivered_clean` but with friction signals present |

`outcome.score` (float, `[0.0, 1.0]`):

```
failed, blocked          -> 0.0
produced_ungated          -> 1.0   (reported separately — see below, never blended with gated CPM)
delivered_clean/with_findings:
    score = 1.0
    score -= 0.10 * stage.cycles      for the conformance stage, if present
    score -= 0.05 * stage.advisory    for the review stage, if present
    score = clamp(score, 0.25, 1.0)
```

A `blocked`/`failed` run scores `0.0` regardless of token spend — this is the mechanical
enforcement of the acceptance criterion "refuses to present raw token reduction as success without
outcome quality," and of the epic's non-goal "do not reduce tokens blindly at the expense of
correctness." Rework signals (conformance revision cycles, review advisory findings) discount a
delivered run's score but the 0.25 floor keeps a delivered-but-imperfect run distinguishable from
a blocked one. `produced_ungated` runs (refine/plan — no gate exists to fail) score `1.0` but are
**excluded from the headline (gated) Factory CPM** and reported as a separate `ungated` line, so a
cheap ungated run can never be blended into — and inflate — the correctness-weighted number.
`outcome.evidence` records exactly which stages/penalties produced the score, so the policy is
auditable, not just a number.

`factory_cpm = outcome.score * 1_000_000 / tokens_per_task` (or `null` if `tokens_per_task == 0`).
The denominator is tokens, not dollars, matching the epic's own definition: *"Factory CPM:
outcome-weighted successful factory work per million tokens."* `cost_per_task` remains available
separately for dollar-based reporting.

#### Retry spend and failure spend

The #208 ledger's own `retry_count` field is **always `0`** — the proxy is a single-pass
forwarder; a retried call arrives as a second, separately-correlated ledger row rather than an
incremented counter (`model_proxy.py:110-115`). Retry spend is therefore derived from ledger rows
with a non-success `status`, not from `retry_count`:

```
retry_spend.tokens         = sum(input_tokens + output_tokens) over ledger rows for this run
                              where status indicates failure (>= 400)
retry_spend.request_count  = count of such rows
```

This requires `ledger_available` (see below); it is `null` when no ledger rows are correlated.

`failure_spend` captures the epic's broader definition ("tokens/cost spent on retries, blocked
runs, repeated failed tool calls ... and avoidable dead ends"):

```
if outcome.state in {"failed", "blocked"}:
    failure_spend = { tokens: tokens_per_task, basis: "whole_run" }   # nothing salvageable was delivered
else:
    failure_spend = { tokens: retry_spend.tokens or 0, basis: "retry_only" }
```

### Graceful degradation when the ledger has no data

#208's model-proxy is opt-in and disabled by default (`FACTORY_MODEL_PROXY_ENABLED`, unset =
false) — most runs, historical and future, will have zero correlated `request-ledger.jsonl` rows.
`harness_economics` is **always** computed and attached regardless:

- **Always computable** (sourced from `stages[]`/`totals`/`nodes[]`, independent of #208):
  `cost_per_task`, `tokens_per_task`, `wall_clock_seconds`, `outcome.*`, `factory_cpm`, and the
  `failure_spend.basis == "whole_run"` case (which needs no ledger data at all).
- **Ledger-dependent** (require correlated `request-ledger.jsonl` rows, `run_id`-matched):
  `retry_spend`, the `failure_spend.basis == "retry_only"` tokens figure, and everything under
  `ledger_mechanics` (cache-hit ratio, tool-schema/system-prompt byte overhead, largest offered
  tools) — nested under one nullable sub-object rather than scattered per-field nulls, so
  downstream consumers do one null check, not several.
- `ledger_available: true|false` and `ledger_rows_correlated: <int>` are always present and
  non-null, so a consumer can distinguish "genuinely zero retries, ledger confirms it" from "we
  have no ledger data for this run" — without this pair, a `0` is ambiguous.

`harness_economics` is computed **live**, inside `cmd_assemble()`, while ledger rows are freshest
— not deferred to a later batch job. The ledger rotates at 100 MB / 3 backups
(`model_proxy.py:37-38,119-130`); a deferred computation risks finding rows already rotated away
and silently mis-reporting a run that actually had ledger coverage as `ledger_available: false`.
Historical backfill (below) is the explicitly separate, best-effort, degrade-only path for runs
`cmd_assemble` already ran for before this ticket existed.

### Wiring `cmd_assemble` into `on_failure`

Investigation finding not anticipated by the issue text: `cmd_assemble` — and therefore
`harness_economics` — currently only runs on the **success** path
(`entrypoint.sh:837-844`, after the main workflow loop completes). The `on_failure` trap
(`entrypoint.sh:474-528`) only calls `run-record record` (`entrypoint.sh:480-484`, a bare
`stage=failed` event appended to `runs.jsonl`) — it never calls `run-record assemble`, so
`run-record.json` — and thus any `harness_economics` block — is never written for a run that fails
before reaching the success path. Since `outcome.state == "failed"` is one of this ticket's core
states, and the acceptance criteria explicitly require refusing to present token spend as success
"without outcome quality," a failed run must actually get an assembled record showing
`outcome.score == 0.0`, or the failed-state branch of the policy is dead code in practice.

Fix (small, contained diff, consistent with `on_failure`'s existing pattern of best-effort/`|| true`
calls): add a `--status` flag to `run-record assemble` (default `"completed"`, so the existing
success-path call is unaffected), and add a second `run-record assemble --status failed ...` call
inside `on_failure`, alongside (not replacing) its existing `run-record record` call, guarded the
same `|| true` way everything else in that trap is. This is the minimum change needed to make
`outcome.state == "failed"` reachable; it does not alter `on_failure`'s existing board-status or
comment-posting behavior.

### Cross-run / issue / phase rollup — read-only query, no new artifact

The acceptance criterion "aggregates #208 request-ledger rows by run/issue/phase" is a group-by
over data that does not fit inside a single run's `run-record.json` (one GitHub issue typically
spans multiple runs — refine, plan, one or more implement/conformance/code-review cycles — each
its own `$RUN_ID`). `request-ledger.jsonl` is the only store that is both cross-run and already
stamps every row with the three needed group-by keys: `run_id` (run), `issue_number` (issue), and
`intent`/`stage` (phase).

Add a new read-only subcommand, `run_record.py issue-economics --issue N`, that:
- Scans `request-ledger.jsonl` (plus any still-present `.1`/`.2`/`.3` rotation backups), filtering
  by `issue_number`, and folds rows into run/issue/phase buckets for the ledger-mechanics fields.
- Overlays dollar figures and outcome scores per `run_id` by reading that run's own
  `run-record.json` under `${ARTIFACTS_DIR}/../<run_id>/run-record.json` if still present on disk
  (never recomputes them independently — see [Cost source](#cost-source-no-new-pricing-table)).
- Produces no new persisted file — purely a query, run on demand (e.g. by the plan/implement phase
  commands, a human operator, or later by #207's cockpit). This keeps the rollup out of the "not a
  separate source of truth" constraint entirely, since nothing new is written.
- `post_cost_report()`'s existing cumulative-comment mechanism remains the one *human-facing*
  cross-run headline (extended with one `harness_economics` cumulative line); it is not upgraded
  into the authoritative rollup, since it already depends on fragile regex-scraping of its own
  prior comment text.

### Historical backfill

Backfill is a separate, best-effort, degrade-only CLI path (e.g.
`run_record.py backfill-economics --run-id <id>` or a small loop script over existing `$RUN_ID`
directories) that:
- Reads the retained `run-record.json` for a given `$RUN_ID` (for `outcome`/`totals`/dollar
  figures) and joins against `request-ledger.jsonl` rows still present for that `run_id` (for
  ledger mechanics), writing the recomputed `harness_economics` block back into that run's
  `run-record.json` in place.
- Is bounded by two independent retention floors: the `$RUN_ID` artifacts directory
  (`${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs/${RUN_ID}/`) must still exist
  on the host, and any ledger rows for that run must not yet have rotated past the 100 MB / 3
  backup window. Runs failing either check are simply out of scope — this satisfies the
  acceptance criterion's own qualifier, "recent run artifacts **where enough data exists**."
- Never fabricates ledger data: a run whose directory exists but whose ledger rows have rotated
  away gets the same `ledger_available: false` degraded record the live path would have produced.
- Does **not** read `runs.jsonl` as a backfill source — its `cmd_assemble`-written stage rows
  hard-code `tokens=0`/`cost=0.0` (`run_record.py:322-324`) and carry no ledger-joinable data; it
  is useful only as a discovery index of which `run_id`/`issue_number` pairs exist, not as a data
  source.

### Testing

- Unit tests (extend `tests/test_run_record.py`): `harness_economics` shape and field presence for
  each `outcome.state`; the penalty-formula arithmetic; `ledger_available`/`ledger_mechanics`
  null-vs-populated branches; the new `--status failed` path through `cmd_assemble`.
- Unit tests for the new `issue-economics` query: correct run/issue/phase grouping from a fixture
  ledger, correct overlay of per-run dollar/outcome figures, and correct handling of a `run_id`
  whose `run-record.json` no longer exists on disk (missing overlay, ledger data still returned).
- Unit tests for backfill: fresh run-record + fresh ledger (full recompute), fresh run-record +
  rotated-away ledger (degrades to `ledger_available: false`), missing run-record directory
  (skipped, not an error).
- A bash smoke assertion (pattern: existing `tests/test_cost_report_endpoint.sh`) that
  `post_cost_report()`'s new `harness_economics` line renders without breaking the existing
  cost-table format when `run-record.json` predates this change (i.e. lacks the key —
  `harness_economics` must be treated as optional/absent-tolerant by the comment-rendering code,
  matching `memory_trace`'s existing optional-key precedent).

## Alternatives Considered

1. **New sibling `harness_economics.json` file (per the issue's illustrative Scope text) vs.
   extending `run-record.json` in place.** A sibling file was rejected: it would need its own
   `run_id`-correlation join, adds a second write/read path, and the Acceptance Criteria's "not a
   separate source of truth" language directly forbids exactly this kind of parallel artifact for
   data that is a pure derivation of what `run-record.json` already holds.
2. **New `$`/token pricing table for request-level dollar figures vs. reusing Archon's node-level
   `cost_usd`.** Rejected building a new table: it would create two independently-computed dollar
   totals for the same run (Archon's and the ledger's), which is precisely the drift the "not a
   separate source of truth" constraint exists to prevent, and this repo has no prior art for
   maintaining its own model pricing anywhere.
3. **Persisted `issue-economics.json` (written incrementally per run) vs. an on-demand read-only
   query over `request-ledger.jsonl`.** Rejected the persisted form: new durable state for a
   third time, in tension with the same constraint; the on-demand query needs no new persisted
   artifact and the ledger already carries every group-by key needed.
4. **Deferred/batch computation of `harness_economics` vs. computing it live inside
   `cmd_assemble`.** Rejected deferred computation: the ledger's 100 MB/3-backup rotation means a
   batch job would eventually find rows already gone, silently and permanently mis-reporting
   `ledger_available: false` for runs that genuinely had ledger coverage at the time they ran.
5. **Adding placeholder `memory_intervention` fields now (per the issue's expansion comment) vs.
   deferring entirely to #241.** Rejected adding any placeholder/stub structure — see
   [Non-goals](#non-goals).

## Non-goals

- **`memory_intervention` fields** (`worker_calls`, `reminders_emitted`, `useful_interventions`,
  etc.), proposed in a comment on #235 referencing the "Proactive Memory Agent" paper and epic
  #241. #241 does not exist as a spec or code anywhere in this repo today — there is no
  reminder/worker-call/memory-bank-edit subsystem to source real values from, and this repo's own
  `.archon/memory/architecture.md` repeatedly flags adding anticipatory schema/scaffolding for an
  unimplemented future feature as a scope-discipline violation (see e.g. the #41 and #49 entries).
  Adding the fields now would either sit permanently null or lock in a shape #241 has not yet
  designed and would likely have to migrate. **Extension point for #241:** when its emitting
  subsystem lands, extend the `harness_economics` object with a new top-level `memory_intervention`
  key, additively — this ticket deliberately does not reserve that key or its field types.
- Building #207's cockpit UI or #190's state-governance scorecard. This ticket produces data those
  tickets can read (`run-record.json.harness_economics`, the `issue-economics` query); it does not
  build their consuming surfaces.
- Any change to `deploy/instances/**` or `.github/workflows/publish.yml`.

## Open Questions

- **Per-persona/sub-phase attribution** remains unavailable (inherited limitation from #208 — see
  that spec's "Persona/Stage Attribution" section): the ledger's `persona` field is always
  `"unknown"` and `stage` is phase-level only. `harness_economics`/`issue-economics` inherit this
  same granularity ceiling; revisit only if Archon gains true per-node env injection.
- **Ledger cache fields are currently always zero** (`cache_read_input_tokens`/
  `cache_creation_input_tokens` in `model_proxy.py` are hardcoded `0`, not yet wired to real
  response values). `ledger_mechanics.cache_hit_ratio` will report `null`/`0` universally until a
  follow-up to #208 wires real cache accounting through — flagged here so a future implementer
  doesn't mistake universal zeros for "the factory never hits cache."
- **`issue-economics` consumer**: this ticket ships the query capability; which future work (plan
  phase context assembly? #207's cockpit? a periodic report?) actually calls it routinely is left
  to those tickets to decide.

## Assumptions

- One run == one task for `cost_per_task`/`tokens_per_task` purposes (i.e., these are per-run
  metrics, not per-DAG-node or per-subagent-call metrics) — consistent with `run-record.json`
  already being the per-run unit of account.
- The `harness_economics` schema is additive/versioned (`policy_version` field) so the scoring
  formula's constants (`0.10`, `0.05`, `0.25` floor) can be tuned in a follow-up without a
  breaking schema change; they are defined as named constants alongside `cmd_assemble` so the
  documented policy and the code cannot silently diverge.
- Ledger rotation backups (`.1`/`.2`/`.3`) are assumed still readable in the same JSONL row shape
  as the live file for backfill purposes; no separate backup-format handling is assumed necessary.

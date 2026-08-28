# Harness-Swap Replay Benchmark: Wiring Economics into the Existing Replay Path

**Issue:** omniscient/dark-factory#240
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#234 ("Harness economics — make Dark Factory
token-efficient by design")
**Depends on / consumes:** omniscient/dark-factory#208 (transparent model proxy +
request-ledger, merged — `scripts/factory_core/model_proxy.py`); omniscient/dark-factory#235
(`harness_economics`/`factory_cpm` computation, merged — `scripts/factory_core/run_record.py`);
omniscient/dark-factory#335 (replay benchmark suite, merged — `bench/`); omniscient/dark-factory#48
(skill-modularization eval harness, merged — `evals/skill_flow_eval.py`)
**Related, explicitly deferred (see [Non-goals](#non-goals)):** omniscient/dark-factory#241
(proactive-memory epic — no code yet); a #311-derived contract/trajectory-evalset follow-up (no
ticket number assigned yet)
**Operator note (issue #240, 2026-08-28 comment):** refine as a spec-only, Wave 1 deliverable;
spec gate reviewed by the operator, not auto-merged on grace timer.

---

## Overview / Problem Statement

Issue #240 asks for a Dark Factory version of the "harness-swap" method from *The Harness
Effect* (arXiv 2607.06906v1): run the same historical tasks under the same model policy across
two orchestration variants, and report cost/tokens/wall-clock/quality together so a harness
change can be promoted or rolled back on evidence, not vibes.

Codebase research for this spec found that **most of the substrate already exists**, split
across two systems that have never been joined:

- `bench/` (built under #335) is a locked, 10-task replay fixture set (`bench/suite.json`) with
  a live re-execution runner (`bench/run_suite.sh`, `archon workflow run archon-dark-factory
  "Fix issue #N"` under `BENCH_MODE=stub`) and a `pass^k` outcome scorer. `docs/parity-p2.md`
  (2026-07-06) already ran a real two-harness replay across this suite — with gate criteria
  pinned *before* numbers were collected — but scored **outcome only**; cost appears once, as a
  rough estimate, not a measured figure.
- `scripts/factory_core/run_record.py` (built under #235) already computes everything #240's
  acceptance criteria ask for in an economics block — `cost_per_task`, `tokens_per_task`,
  `wall_clock_seconds`, `outcome{state,score,evidence}`, `factory_cpm`, `retry_spend`,
  `failure_spend` — via `_compute_harness_economics()`. It is attached to every normal factory
  run's `run-record.json`.

The gap: `bench/run_suite.sh` calls `archon workflow run` directly, bypassing `entrypoint.sh`
entirely. `run-record assemble` and `post_cost_report` are called only from `entrypoint.sh`
(lines 890–905), so **replay runs never produce a `run-record.json` and `harness_economics`
never fires on them.** #240's real, bounded deliverable is closing that gap — wiring economics
into the replay path — not inventing new replay or economics machinery.

A second, unrelated gap: `evals/skill_flow_eval.py` (built under #48) already does *retrospective*
harness-swap comparison — mining before/after merge-boundary populations from durable GitHub
issue-comment data (cost-report comments, conformance/code-review verdicts) — but it has never
been pointed at an economics-relevant boundary; #48's own scope was skill-modularization, not
economics.

This spec treats both as one substrate (per issue Comment 2's instruction not to build a second
eval system): **Tier A (controlled replay)** wires `harness_economics` into `bench/`; **Tier B
(retrospective mining)** extends `evals/skill_flow_eval.py`'s mining to the same metric columns.
Both are scored against the same `bench/suite.json` fixture population where possible.

## Requirements (from Q&A)

1. **Scope this ticket to the core harness-swap economics comparison only.** The issue's two
   follow-up comments (#241 memory-intervention ablations; a #311-sourced contract/trajectory
   evalset) grow the ask well past `size:M`, and #241 has no code yet to ablate. Reserve both as
   named-but-rejected `dimension` keys in the variant schema (see
   [Reserved dimensions](#reserved-dimensions-241-and-311)) rather than folding them in or
   silently dropping them, per the precedent set refining #195/epic #194.
2. **"Replay" means using the existing `bench/` machinery**, not building new live-execution
   infrastructure. This refine run cannot execute code or commit anything outside
   `docs/superpowers/specs/` and `.archon/memory/` (OOS excision), so this spec fixes methodology,
   variant schema, and pre-registered gate criteria; a later (non-refine) ticket executes and
   fills in results — mirroring the `#189` precedent that a live-benchmark spike's execution
   cannot happen inside a refine run.
3. **Both tiers must report economics and quality/outcome together**, per the issue's own
   acceptance bar ("token savings alone cannot pass").
4. **The worked example variant is `token_optimization.enforce_budgets`**
   (`config/config.yaml:122`), not a net-new mechanism from the referenced paper (cache-shape
   discipline, structured incremental compaction, etc.). It is the only real, already-shipped,
   already-toggleable harness change in the repo with existing calibration data
   (`evals/reports/budget-calibration-scorecard-2026-07-03.md`,
   `evals/token_opt_eval.py`) to validate the new instrument against, and it is genuinely a
   single-env-var delta — the cleanest possible satisfaction of "without changing task/model
   inputs unnecessarily."
5. **Promotion/rollback reuses two existing, orthogonal ladders**, not a new taxonomy:
   `replay → shadow → advisory → blocking` (promotion; from issue Comment 2, for future
   calibrated-mandatory-class variants) and Tier 0/1/2 (rollback; from
   `docs/dark-factory-token-optimization.md`'s rollout runbook).
6. **Reported cost must distinguish "measured zero" from "not measured."** `bench/run_suite.sh`'s
   existing `get_last_run_cost_cents()` silently returns `0` on any parse failure — a bug that
   would bias a comparison toward whichever arm happened to fail to report. `harness_economics`
   must supersede it, with unavailable cost surfaced as `null` + `cost_unavailable: true`, never
   `0`.

## Architecture / Approach

### Tier A — controlled replay (`bench/` + `harness_economics`)

1. Add an `--emit-run-record` (or always-on) path to `bench/run_suite.sh` so each per-task
   `archon workflow run` invocation also runs `run-record assemble` against that invocation's
   artifacts directory and writes `harness_economics` to `bench/results/<run>-run-record.json`
   (a new file, not the GitHub-comment path). `post_cost_report` is *not* called on this path —
   `BENCH_MODE=stub` stubs `push-and-pr`, so there is no PR to comment on; Tier A's report is
   file-based only.
2. Declare economics as **agent-phase economics, preview/PR stages excluded** — `BENCH_MODE=stub`
   skips `preview-up` and `push-and-pr`, so absolute `cost_per_task` is a lower bound relative to
   a production run. Deltas between two variants run under the identical stub configuration
   remain valid; absolute figures are not comparable to production `dark-factory-cost-report`
   totals without a documented offset.
3. **Variant declaration schema** (generalizes `docs/parity-p2.md`'s single image-swap row and
   the config axis in one shape):
   ```
   variant:
     variant_id: string          # e.g. "budget-enforce-on", "budget-enforce-off"
     dimension: economics | memory_intervention | contract_trajectory   # see reserved dimensions
     image: string (optional)    # docker image ref; omitted = current default
     config_overlay: {...}       # config.yaml key overrides; omitted = current default
     env: {...}                  # env var overrides (e.g. TOKEN_OPTIMIZATION_ENFORCE_BUDGETS)
   ```
   Only `dimension: economics` variants are runnable by this ticket's implementation; the other
   two enum values exist so a follow-up ticket's runner can reuse the schema without a breaking
   change, and any attempt to run one now must fail loudly naming the tracking ticket rather than
   silently no-op.
4. **Worked example — two arms, one suite, one env var:**
   - Arm A (`budget-enforce-off`): `env.TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false` (kill-switch,
     confirmed live in `scripts/budget_gate.sh:50` — `false|0|no` forces observe mode; it cannot
     force enforcement on, so this is the only reachable "disabled" arm).
   - Arm B (`budget-enforce-on`, baseline/current default): env unset, config as committed
     today (`enforce_budgets: true`, per-scenario `enforce` flags as in `config/config.yaml`).
   - Both arms run the full `bench/suite.json`, same `pre_pr_sha` per task, same model policy,
     same image. The only delta is the one env var.
   - Optional validity check: compare the measured Tier A token delta against
     `evals/token_opt_eval.py`'s `simulate_enforcement()` offline prediction. Large disagreement
     is a signal the benchmark itself (not the harness) is broken, before any promotion decision
     is made on its output.
5. **Fixture health caveat to carry into execution:** `bench/suite.json`'s tasks were authored
   pre-extraction (nested MarketHawk paths); per `.archon/memory/dark-factory-ops.md` and
   `docs/parity-p2.md` §4a, roughly 6/10 tasks currently score "expected-fail-both" on outcome for
   unrelated path/tooling reasons, leaving ~3-4 S-bucket tasks with real outcome-discriminating
   power. This does **not** block the economics dimension — tokens/task, cost/task, wall-clock,
   and failure_spend remain measurable on expected-fail-both tasks — but the outcome/quality
   comparison should either re-lock eligible tasks via `bench/find_eligible.py` first, or scope
   the outcome gate to the known-good subset and say so in the results write-up.

### Tier B — retrospective mining (`evals/skill_flow_eval.py`)

1. Extend the existing mining functions (`mine_cost_report_population`,
   `mine_conformance_population`, `mine_code_review_population`) with a boundary-SHA pair
   bracketing the `enforce_budgets` T3b/T6 enforcement-live commits (config.yaml's own comments
   date these: `refine`/`plan` T3b, `conformance`/`code-review` T6).
   `evals/token_opt_eval.py:547`'s `load_bench_issues(suite_json_path)` already reads
   `bench/suite.json`, so Tier B's population and Tier A's fixture set are the same 10 issues
   measured two different ways (observational vs. controlled) — not two unrelated benchmarks.
2. Output: extend the existing scorecard renderer (`evals/skill_flow_scorecard.py`'s pattern) with
   the same economics columns Tier A produces, so a reviewer reads one joined report, not two.

### Gate criteria (pre-registered before execution, per the `parity-p2.md` discipline)

A later execution ticket must pin, before collecting numbers:
- An outcome non-inferiority bound (mirroring `parity-p2.md`'s `c_ext ≥ c_base − 1` per size
  bucket, adapted to arms instead of images).
- A `tokens_per_task` / `cost_per_task` improvement threshold for the "improvement" arm to be
  worth promoting.
- All non-tested config flags (`architecture`, `memory`, `comments`, each independently toggleable
  per `config/config.yaml`'s `token_optimization` block) held at committed defaults in both arms —
  this is the mechanical enforcement of "without changing task/model inputs unnecessarily."
- At `n` ≤ 10 paired same-issue/same-`pre_pr_sha` runs, report paired median and range as
  directional evidence only; do not claim statistical significance.

### Promotion / rollback recommendation format

Per-variant table, reusing both existing ladders rather than inventing a third:

| variant_id | outcome delta (pass^k) | economics delta | gate verdict | promotion stage (Comment 2 ladder) | rollback_tier |
|---|---|---|---|---|---|
| budget-enforce-on | ... | ... | pass/fail | replay / shadow / advisory / blocking | 0 / 1 / 2 / none |

`rollback_tier` generalizes `docs/dark-factory-token-optimization.md`'s Tier 0 (env kill,
`TOKEN_OPTIMIZATION_ENFORCE_BUDGETS`, no commit) / Tier 1 (master config revert) / Tier 2
(per-scenario revert) — Tier 0 only exists for variants with a live env kill-switch; a
`config_overlay`-only or `image`-swap variant (e.g. parity-p2's) has no Tier 0, since image
rebuild/redeploy touches `deploy/**`, which is human-only per this repo's hard limits, so it must
report `rollback_tier: none` and route through a human-reviewed revert instead.

### Reserved dimensions (#241 and #311)

Per the write-bar precedent set refining #195/epic #194 (a comment-channel proposal that would
grow an already-scoped ticket past its size label gets a minimal reserved-key carve-out now, plus
a follow-up ticket reusing the source brief verbatim), this spec's variant schema reserves but
does not implement:

- `dimension: memory_intervention` — Comment 1's 5 replay variants (current retrieval, full-bank
  exposure, always-inject, advisor/no-bank, maintained-bank + selective reminder/silence).
  Blocked on epic #241, which has no code yet (confirmed via `docs/archive/2026-07-16-harness-
  economics-ledger-cpm-design.md`'s own Non-goals). **Recommended follow-up:** a ticket scoped to
  #241's own implementation first; this dimension only becomes runnable after that lands, and
  should then consume this spec's variant schema and Tier A/B reporting rather than a new one.
- `dimension: contract_trajectory` — Comment 2's 4 variants (final-artifact/gate checks;
  reflection/critic only; resolved completion contract only; completion contract + observable
  partial-order trajectory verifier), seeded with #300/#271/#279/#280/#293/#305 as representative
  failures. **Recommended follow-up:** a ticket that designs the trajectory-event schema (explicitly
  *not* raw chain-of-thought, per Comment 2) and its verifier, then plugs into this substrate as
  its `dimension` value; promotion for any resulting mandatory class still follows
  `replay → shadow → advisory → blocking` per Comment 2.

Attempting to run either dimension against today's schema must fail with an error naming the
relevant tracking ticket, not silently no-op or silently produce empty results.

## Alternatives Considered

1. **Build new live re-execution machinery (two containers, same historical issue, two harness
   configs) as this ticket's deliverable.** Rejected — `bench/` already does this
   (`bench/run_suite.sh` + `bench/suite.json`), and `docs/parity-p2.md` already proved the
   two-image-swap version end-to-end. Building a second replay runner would violate the issue's
   own "extend or coordinate with #48 rather than duplicating" instruction, generalized to
   `bench/`.
2. **Design economics + memory-intervention ablations + trajectory verification together as one
   unified spec.** Rejected — #241 has no implementation to ablate yet, making that dimension's
   design speculative; folding a genuinely unimplementable dependency into a `size:M` spec-only
   ticket would produce a spec whose own scope the conformance gate can't police. Handled instead
   via reserved dimension keys and named follow-ups (see above).
3. **Use only the parity-p2-style image swap as the worked example**, since it already ran once.
   Rejected — it already scored outcome and never captured economics (the actual gap #240 exists
   to close), and rerunning a two-image build/publish cycle just to add economics is far more
   expensive and slower to validate than a one-env-var config overlay that reuses #48's existing
   calibration data as a sanity check. The image-swap variant is kept as a second, reserved
   `dimension: economics` candidate for a follow-up backfill, not dropped.

## Open Questions (non-blocking)

- Should Tier A's per-run economics artifacts (`bench/results/<run>-run-record.json`) be
  committed to the repo (like `bench/baseline.md`) or treated as local/gitignored scratch
  (`bench/.gitignore` already excludes some `results/` content)? Recommend gitignored, with only
  the rendered scorecard/report committed — mirrors how `bench/results/` is already treated today.
- Exact `pass^k` k value and n for the execution ticket's gate criteria are left to that ticket,
  consistent with `parity-p2.md`'s own precedent of pinning criteria immediately before that
  specific run rather than baking a fixed n into the schema.
- Whether `bench/find_eligible.py` should be re-run to refresh `bench/suite.json` before or as
  part of the execution ticket, given the ~6/10 "expected-fail-both" staleness noted above.

## Assumptions (flagged)

- `BENCH_MODE=stub` is an acceptable execution mode for the economics comparison (agent-phase
  costs are real; preview/PR costs are out of scope for this ticket, as they are for `bench/`
  today). If a future need arises to measure end-to-end economics including preview/PR, that is a
  separate follow-up, not part of this ticket's worked example.
- `config/config.yaml`'s inline comment claiming `enforce_budgets` has "NO env override" is stale;
  `.archon/memory/dark-factory-ops.md` already marks the corresponding entry `[INVALID]` and
  records the correct, live `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-switch semantics (#732,
  `scripts/budget_gate.sh:50`). This spec relies on the live behavior, not the stale comment. The
  stale comment itself is a one-line follow-up (config.yaml's `token_optimization` block is a gate
  surface per CLAUDE.md, so it gets its own ticket rather than an incidental fix here).
- The operator's "spec-only... spike" framing is read as: this spec fixes methodology, schema, and
  pre-registered gate criteria now; a separate (non-refine) execution ticket runs the benchmark and
  fills in the results table, consistent with the `#189` refine/implement split precedent for
  live-benchmark spikes.

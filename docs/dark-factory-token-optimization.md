# Dark Factory — Token Optimization Operator Guide

## Overview

The Dark Factory has four context-optimization features intended to reduce input token usage
per run, plus per-scenario budget enforcement on top of them. All are **enabled by default**
in config and can be independently disabled via config flags or environment variable
overrides. Enabled is not the same as effective: architecture slicing currently changes
telemetry only, and budget enforcement changes no prompt today (see "What changes a phase's
prompt" and the status column below).

Rollout phases 1–3 (observe, low-risk optimization, implementation/conformance) landed
via issues #664–#671. Phase 4 (budget enforcement) shipped via #713–#719.
As of T3b (#733), the enforcement flags are on for conformance, code-review, refine, and plan.

---

## Active Features

| Feature | Config key | Env override | Effect when enabled | Status in a run |
|---------|-----------|--------------|---------------------|-----------------|
| Architecture slicing | `token_optimization.architecture.enabled` | `TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED` | Computes the component-relevant slice of ARCHITECTURE.md | **Telemetry only.** `context_budget.py` measures the slice, but no DAG node produces the context pack that would deliver it, so phase commands still read the full file ([#487](https://github.com/omniscient/dark-factory/issues/487)) |
| Memory top-k | `token_optimization.memory.enabled` | `TOKEN_OPTIMIZATION_MEMORY_ENABLED` | Caps memory retrieval to the top 8 highest-scored entries (≤ 1 500 tokens) | Applies on both the index path and the markdown fallback path (the path in use for the self-target); on the markdown path entries are ranked by `path:` tag specificity, label boost and `date:` recency ([#486](https://github.com/omniscient/dark-factory/issues/486)) |
| Comment digesting | *(none read in a run — see below)* | `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` | Collapses raw comment history into a single human-feedback digest | Active for `continue` runs |
| Diff ranking | `token_optimization.diff.enabled` | `TOKEN_OPTIMIZATION_DIFF_ENABLED` | Ranks and truncates the review diff by risk tier; critical files bypass the cap | Active in conformance, docs-conformance and code-review |
| Budget enforcement | `token_optimization.enforce_budgets` + `enforce.<scenario>` | `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` | Kill-only: `false`/`0`/`no` (case-insensitive) forces observe in all 5 nodes; unset or any other value defers to config | In enforce mode the gate writes `export VAR=N` lines to the caps file; the consumers source it, so the derived memory cap (`memory_retrieve.py`) and diff cap (`diff_rank.py`) reach the optimizers ([#511](https://github.com/omniscient/dark-factory/issues/511); see Budget Enforcement) |

---

## Configuration

The defaults live in `config/config.yaml` under `token_optimization`. That file is **baked
into the image** at `/opt/dark-factory/config/config.yaml`:

```yaml
token_optimization:
  enforce_budgets: true      # master enforcement gate
  default_budget_tokens: 30000
  budgets:                   # per-scenario token budgets
    refine: 30000
    plan: 30000
    implement: 30000         # observe-only — enforce: false
    conformance: 22000
    code-review: 22000
  enforce:
    refine: true
    plan: true
    implement: false         # deferred; see Follow-up Path below
    conformance: true
    code-review: true
  issue_context:
    reserve_tokens: 2000
  architecture:
    enabled: true
    max_tokens: 5000
    min_tokens: 2500
  memory:
    enabled: true
    max_entries: 8           # read by memory_retrieve.py; falls back to TOP_K_DEFAULT = 8
    max_tokens: 1500
    min_tokens: 750
  comments:
    enabled: true            # not consulted in a run (the gate reads only the env override)
    max_tokens: 2000
    min_tokens: 1000
  diff:
    enabled: true
    max_review_tokens: 6000
    min_review_tokens: 3000
```

`config/config.yaml` holds further keys (for example `escalation`); it is the reference for
the full block.

### How a run resolves its config

Scripts inside a run read `<clone>/.claude/skills/refinement/config.yaml`. `entrypoint.sh`
prepares that path through `factory_core.effective_config`. The same resolution is also
written to `run-config.json`, which `scripts/budget_gate.sh` and `scripts/architecture_slice.py`
read through `FACTORY_RUN_CONFIG_JSON` (#180):

1. If the target repo **commits** `.claude/skills/refinement/config.yaml`, that file is used
   as is. A `token_optimization` block in the adapter that disagrees with it only produces a
   drift warning.
2. Otherwise the file is materialized (and git-excluded) from the baked defaults, overlaid
   with the `token_optimization` block of the target's `.factory/adapter.yaml`, if it has one.

The self-target commits no such config file and its adapter omits `token_optimization`, so it
runs on the baked defaults.

> **Deploy nuance:** `config/config.yaml`, `workflows/`, `commands/`, `scripts/` and
> `entrypoint.sh` are all **baked into the Docker image**. A commit that changes them on
> `main` has no effect until a new image is published and the scheduler runs it. The
> no-rebuild route for token-optimization settings is the `token_optimization` block in the
> target's `.factory/adapter.yaml`, which is read from the fresh clone on every run.
> Nothing reads `<clone>/config/config.yaml`.

### Keys that are not read

- `token_optimization.comments.enabled`: the comment-digest gate reads only the
  `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` environment variable. `scheduler.sh` copies the config
  value into its own process, but dispatch does not pass it to run containers.
- `token_optimization.escalation.opus_only_for`: nothing reads it.

There is no master switch (`token_optimization.enabled` and `TOKEN_OPTIMIZATION_ENABLED` do
not exist) and no cheap-model routing switch (`escalation.cheap_model_first` and
`TOKEN_OPTIMIZATION_CHEAP_MODEL_FIRST` do not exist); both were removed under
[#487](https://github.com/omniscient/dark-factory/issues/487). A target adapter that still
sets `token_optimization.enabled` is accepted and the key is ignored. Use the per-feature
switches.

### What changes a phase's prompt, and what is only measured

Only these change what a phase sees, each at its **config default cap** (not a
budget-derived one, see below):

- **Diff cap** (`diff.*`): conformance, docs-conformance and code-review receive the ranked, truncated diff.
- **Memory cap** (`memory.*`): memory retrieval is cut to the top entries / token cap.
- **Comment digest**: `continue` runs get a digest instead of raw comments.

These are **measured only**; they do not shrink any prompt:

- **Architecture slicing** (`architecture.*`): no DAG node produces the context pack, so
  `context_pack.py` is never run and phase commands read the full `ARCHITECTURE.md`. Delivery
  was deliberately not wired (the telemetry-only path was chosen for
  [#487](https://github.com/omniscient/dark-factory/issues/487)). `context_budget.py` still
  computes the slice, and the cost report lists those savings separately as "measured-only".
- **Budget enforcement**, in scenarios with `enforce: true`. The gate's derived caps tighten
  the memory cap (refine, plan) and the diff cap (conformance, code-review) through
  `token-opt-caps.env`, which holds `export VAR=N` lines so the sourcing shell passes them to
  the Python optimizers it starts ([#511](https://github.com/omniscient/dark-factory/issues/511)).
  Caps are clamped to `[floor, default]`, so they only differ from the config defaults when the
  reserved context leaves too small an allowance. The architecture and comments caps have no
  consumer in refine and plan.

### Environment overrides

The per-feature `TOKEN_OPTIMIZATION_*_ENABLED` variables override config values, and
`TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` is a kill-only override for the budget gates. They are
read **inside run containers**, which load `<PROJECT_DIR>/.archon/.env` and never read the
scheduler's `deploy/instance.env`. The scheduler copies `<PROJECT_DIR>/.archon/.env` to the
path the run compose file loads **once, at startup**, so an edit to that file reaches runs
only after the scheduler is restarted.

`scheduler.sh` also exports the four per-feature flags from config into its own process
(`read_config`), but dispatch does not pass them to run containers; that export has no effect on
a run.

---

## Disable / Rollback Procedure

### Per-feature bypass (architecture, memory, comments, diff)

1. Edit the target's `.archon/.env` (the file under `PROJECT_DIR`):
   ```bash
   TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED=false
   ```
2. Restart the scheduler so it re-copies the file for dispatched runs:
   ```bash
   docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d --force-recreate backlog-scheduler
   ```
3. Runs dispatched after the restart see the new value. In-flight runs keep their spawn-time
   environment.

Diff ranking and architecture slicing read the run's resolved config, so they can also be
disabled durably through the adapter (`token_optimization.diff.enabled: false` or
`token_optimization.architecture.enabled: false` in `.factory/adapter.yaml`) and memory
(`token_optimization.memory.enabled: false`; `load_memory_context.sh` passes the clone path to the
retriever). Comments cannot: the comments config key is not read. Use the env override for comments.

**Fail-safe semantics:** Every disabled path widens context to the full/original baseline.
Disabling a feature never silently drops content.

### Budget enforcement rollback (Tier 0, Tier 1, Tier 2)

Three tiers are available. Tier 0 needs no git commit. Tier 1 and Tier 2 are git commits to
the **target repo's `.factory/adapter.yaml`** and take effect on the next factory run with no
scheduler restart or image rebuild. A commit to `config/config.yaml` in this repo is not a
rollback: it changes the baked defaults only after the next image publish.

**Tier 0 — env kill (fastest, no git commit required):**

Set `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false` in the target's `.archon/.env` so all five
`enforce-budget-*` nodes switch to observe mode. The env var can only force observe — it
cannot force enforcement on.

```bash
# 1. Set the kill switch in the target's Archon env file
echo "TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false" >> .archon/.env

# 2. Recreate the scheduler so it re-copies that file for dispatched runs
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d --force-recreate backlog-scheduler
```

> **Note:** In-flight factory containers are unaffected — the kill switch takes effect on
> runs dispatched after the scheduler restarts. The restart is needed because the scheduler
> copies `.archon/.env` for run containers only at startup; the variable is not part of the
> scheduler's own `env_file`.

To restore enforcement: remove the line from `.archon/.env` and recreate the scheduler again.

**Tier 1 — master kill (reverts all enforcement via the adapter):**
```yaml
# .factory/adapter.yaml in the target repo
token_optimization:
  enforce_budgets: false
```
```bash
git commit -am "revert(enforce): disable budget enforcement master gate"
git push origin main
```

**Tier 2 — targeted (disable a single scenario):**
```yaml
# .factory/adapter.yaml in the target repo
token_optimization:
  enforce:
    code-review: false
```
```bash
git commit -am "revert(enforce): disable code-review budget enforcement"
git push origin main
```

If the target commits its own `.claude/skills/refinement/config.yaml`, that file wins over the
adapter (see "How a run resolves its config"); make the Tier 1 / Tier 2 change there instead.

---

## Reading the Cost Report

After each factory run, a cost report comment is posted on the issue. When `schema_version: 2`
artifacts are present, the comment includes a savings line:

```
**Context savings: 4.2K tokens (28.5%)**
**Fallbacks:** architecture_md: safety_keyword:migration
```

- **Context savings** = baseline minus delivered, in tokens, for the text that really went into
  the prompts of the whole run, summed across its scenarios (a new/continue run covers implement,
  conformance and code-review). Two optimizers count, matching `_DELIVERED_SAVINGS` in
  `context_budget.py`: the memory cap in refine, plan, implement and continue
  ([#486](https://github.com/omniscient/dark-factory/issues/486)) and the diff cap in conformance
  and code-review. Both are measured **after** the optimizer ran, from its own output, not from a
  pre-run probe (the budget nodes run before `memory-context.md` or the ranked diff exist):
  - `memory_retrieve.py` (when it emits its trace) appends `{kind: memory, phase, baseline_tokens:
    uncapped, delivered_tokens: emitted}` to `$ARTIFACTS_DIR/delivered-savings.jsonl`. Only the
    refine/plan/implement/continue phases count; the validate-phase load does not.
  - `diff_rank.py` appends `{kind: diff, baseline_tokens: raw_diff_tokens, delivered_tokens:
    estimated_tokens_emitted}` to the same file, one line per ranking run.
  The file is append-only because `memory-trace.json` and `diff-ranking.json` are overwritten by
  the next phase. The percentage is saved over the baseline of the delivered text.
- **Measured-only** = savings `context_budget.py` computed for features that do not reach a prompt
  (today only the architecture slice). Reported on a separate line and never added to the headline
  figure: `Measured-only (not delivered to the prompt): 4.2K tokens (architecture_md)`. Architecture
  stays measured-only until its delivery is wired ([#487](https://github.com/omniscient/dark-factory/issues/487)).
  Summed over the run's scenarios.
- **Fallbacks** = sections where a safety trigger fired and the full baseline was loaded instead of the optimized version. These are expected and correct behavior (not errors).
- **Budget trim / Over budget** lines are rendered once per scenario that tripped them.

Artifacts written before #487 fold measured-only savings into `savings_tokens`, and runs that
predate `delivered-savings.jsonl` fall back to summing the budget artifacts' own `savings_tokens`,
so older cost reports can still show the old figure.

If no savings line appears, no `context-budget.<scenario>.json` artifact was present,
`schema_version` < 2 (older run artifact), or the computed saving was zero.

---

## Per-Section Savings Data

Each budget node writes its own artifact in `$ARTIFACTS_DIR`: `context-budget.refine.json`,
`.plan.json`, `.implement.json`, `.conformance.json`, `.code-review.json`. The name is the budget
slot (the argument of `budget_gate.sh`), so later nodes of a new/continue run no longer overwrite
earlier scenarios' measurements; the implement slot holds both `new` and `continue` runs (the
`scenario` field inside says which). The cost report (`cost-report-render --artifacts-dir`) reads
all of them, and falls back to the legacy single `context-budget.json` only when no per-scenario
file exists (artifacts from before the split). Each artifact includes per-section savings. Its
`savings_tokens` / `savings_pct` are the pre-run probe; the cost report's headline comes from
`delivered-savings.jsonl` (above), not from these two fields:

```json
{
  "schema_version": 2,
  "savings_tokens": 4200,
  "savings_pct": 28.5,
  "measured_only_savings_tokens": 3800,
  "measured_only_sections": ["architecture_md"],
  "fallback_events": [
    {"section": "architecture_md", "reason": "safety_keyword:migration"}
  ],
  "sections": {
    "architecture_md": {
      "tokens": 1200,
      "baseline_tokens": 5000,
      ...
    },
    "memory_context": {
      "tokens": 800,
      "baseline_tokens": 2400,
      ...
    }
  }
}
```

`baseline_tokens` per section is the unoptimized token count:
- `architecture_md`: full ARCHITECTURE.md token count
- `memory_context`: `uncapped_tokens` from `memory-trace.json` (all matching entries before top-k cap)
- `diff`: `raw_diff_tokens` from `diff-ranking.json` (full diff before ranking/truncation)

---

## Budget Enforcement (Phase 4)

As of T3b (#733), the enforcement flags are on for `conformance`, `code-review`, `refine`, and `plan`.

| Scenario | Enforce | Budget | Intended effect on the prompt | Effect today |
|----------|---------|--------|-------------------------------|--------------|
| refine | **true** | **30 000** | Derived memory cap (via `load_memory_context.sh`); the other caps have no consumer | Memory cap applies; 1 500 (= config default) for a typical run |
| plan | **true** | **30 000** | Derived memory cap, for the same reason | Memory cap applies; 1 500 (= default) typical |
| implement | false | 30 000 | Observe-only | Observe-only |
| conformance | **true** | **22 000** | Derived diff cap applied by `diff_rank.py` | Diff cap applies; 6 000 (= default) typical |
| code-review | **true** | **22 000** | Derived diff cap applied by `diff_rank.py` | Diff cap applies; 6 000 (= default) typical |

**Derived caps reach the optimizers**
([#511](https://github.com/omniscient/dark-factory/issues/511)). `budget_enforce.py` writes
the derived caps to `token-opt-caps.env` as `export VAR=N` lines. The three consumers
(`scripts/load_memory_context.sh` and the conformance and code-review commands) source the
file and then start a Python child; because the lines are exported, `memory_retrieve.py` and
`diff_rank.py` receive the derived cap instead of falling back to their config default
(memory 8 entries / 1 500 tokens, diff 6 000 tokens). Anything else that reads the file must
accept the `export ` prefix. Caps are clamped to `[min, default]`, so a derived cap is never
looser than the default: it only tightens when the reserved context (CLAUDE.md plus the
`issue_context` floor of 2 000) leaves less allowance than the default caps need. For example,
with a 1 300-token CLAUDE.md and 3 000-token issue, refine/plan reserve 4 300, leaving 25 700
tokens, so memory stays at 1 500; the memory cap would only drop (floor 750) if the reserved
context exceeded about 21 500 tokens. The diff cap in conformance/code-review stays 6 000
unless the reserved context exceeds about 16 000.

Why refine and plan are wired for the memory effect only: the caps file is sourced only by
`scripts/load_memory_context.sh` and by the conformance and code-review commands. For refine
and plan that leaves the memory cap (the retriever caps the markdown path too,
[#486](https://github.com/omniscient/dark-factory/issues/486)); the
architecture cap has no consumer in the prompt path; and the comments cap has none, because
the comment digest runs only for `continue` and does not source the file.

Budgets for refine and plan were validated by the full-corpus calibration in #731 (Phase 4b T2)
with 0% `section_at_risk_rate` at the 30k budget. Budgets for conformance and code-review were
derived from the T5 smoke run — run `evals/token_opt_eval.py --calibrate` after
accumulating ≥ 10 bench issues to confirm or adjust.

### How enforcement works

A pre-phase `enforce-budget-<scenario>` DAG node runs `scripts/budget_gate.sh <scenario>`,
which reads the run's config, applies the kill switch, and calls `budget_enforce.py` before
each scenario's command node. It:

1. Estimates un-trimmable reserved tokens (CLAUDE.md + safety full-doc fallback if active
   + `issue_context` floor).
2. Distributes the remaining allowance across the four optimizable sections proportional
   to their default caps, clamped to each section's floor.
3. In **enforce mode**: writes the derived caps to `$ARTIFACTS_DIR/token-opt-caps.env` under
   the env var names the optimizers read (`TOKEN_OPTIMIZATION_ARCHITECTURE_MAX_TOKENS`,
   `TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS`, `TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS`,
   `TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS`) as `export VAR=N` lines, so a consumer that
   sources the file passes the values to the optimizer processes it starts
   ([#511](https://github.com/omniscient/dark-factory/issues/511)).
4. If the un-trimmable core reaches or exceeds the budget, records `over_budget: true` in
   telemetry and proceeds — enforcement **never blocks a run**.

The whole node is fail-open (`|| true`): on any error, no caps are exported and optimizers
use their config defaults.

### Telemetry signals

In `context-budget.<scenario>.json` (`$ARTIFACTS_DIR`; the enforce write-back targets the same file the gate read):
- `over_budget` — `true` if the un-trimmable reserved context is at or above the scenario budget
- `would_trim` — `true` when the derived caps are tighter than the config defaults; computed and written the same way in observe and enforce mode
- `derived_caps` — computed cap values (enforced or hypothetical)

The cost-report comment surfaces `over_budget` when it fires.

---

## Observe → Enforce Procedure

Use this procedure when promoting a scenario from observe-only to enforced.

**Prerequisites:**
- `over_budget_rate ≤ 10%` across ≥ 10 bench issues at the candidate budget
- `section_at_risk_rate == 0%` (enforcement would not trim any required-by-calibration
  ARCHITECTURE.md section)

Run the calibration eval to check. Both list flags are plural and comma-separated, and
`--clone-dir` defaults to `/workspace/markethawk`, so pass it explicitly for any other target:
```bash
# Inside the factory container
python3 evals/token_opt_eval.py --calibrate \
  --budgets <candidate_tokens>[,<candidate_tokens>...] \
  --scenarios <scenario_name>[,<scenario_name>...] \
  --clone-dir <path to the target clone>
```

Per-run `context-budget.implement.json` (and `.refine.json` / `.plan.json`) artifacts hold
`sections.architecture_md.tokens` — collect these to compute the p90 architecture slice size and
verify it fits within the planned `architecture.max_tokens` cap before flipping. Each scenario has
its own file, so the value survives a full new/continue run.

**Flip procedure (when gates pass):**
1. Set `budgets.<scenario>: <calibrated_value>` and `enforce.<scenario>: true`. For one
   target, put them in the `token_optimization` block of its `.factory/adapter.yaml`
   (effective on the next run). To change the default for every target, edit
   `config/config.yaml` here and publish a new image.
2. Monitor the next 5 runs for `over_budget` or unexpected `section_at_risk` signals.
3. If issues arise, use the Tier 1 or Tier 2 rollback (see Rollback section).

---

## Path to Phase 4 — Current Status

- **Conformance and code-review**: enforcement flags on since T6 (#719); wired to the diff cap.
- **Refine and plan**: enforcement flags on since T3b (#733) — #731 scorecard gated go-live;
  wired to the memory cap.
- In all four, the derived caps now reach the optimizers
  ([#511](https://github.com/omniscient/dark-factory/issues/511)); with current budgets they
  equal the config defaults for typical inputs (see Budget Enforcement).
- **Implement**: observe-only — deferred pending calibration.

### Follow-up Path for deferred scenario (implement)

**Why deferred:** T5 calibration showed `section_at_risk_rate == 50%` at ALL tested
budgets (22k–40k) for implement. Root cause at the time: `architecture.max_tokens` was 3000,
below real arch slice sizes (3–4k+ tokens), so enforcement would have trimmed architecture
context on every issue regardless of budget size — violating the "Never drop safety-critical
content" goal.

`architecture.max_tokens` has since been raised to **5000** (step 2 below). Implement is
still `enforce: false`; the most recent calibration scorecard in `evals/reports/` is dated
2026-07-03, so re-calibration against the new cap is the remaining work.

**Unlock steps:**

1. **Measure real arch slice sizes** — collect `sections.architecture_md.tokens` from
   per-run `context-budget.<scenario>.json` artifacts across ≥ 10 full-corpus bench issues
   (implement, refine and plan carry the `architecture_md` section):
   ```bash
   # Example: extract p90 slice size from recent artifacts
   python3 -c "
   import json, glob, statistics
   sizes = []
   for f in glob.glob('$ARTIFACTS_DIR/**/context-budget.*.json', recursive=True):
       d = json.load(open(f))
       s = d.get('sections', {}).get('architecture_md', {}).get('tokens')
       if s: sizes.append(s)
   sizes.sort()
   p90 = sizes[int(len(sizes) * 0.9)] if sizes else 'no data'
   print(f'p90 arch slice: {p90} tokens ({len(sizes)} samples)')
   "
   ```

2. **Raise `architecture.max_tokens`** above the p90 arch slice size. Done: the value is
   now 5000. Confirm against step 1 that it is high enough.

3. **Re-run calibration** with the current `architecture.max_tokens` and a candidate budget.
   Gates: `over_budget_rate ≤ 10%` + `section_at_risk_rate == 0%`.

4. **Flip** implement using the Observe → Enforce Procedure above.

Flipping implement is wired to the memory cap only (implement loads memory through
`load_memory_context.sh`); architecture slicing is not delivered to the implement command
([#487](https://github.com/omniscient/dark-factory/issues/487)). Its only effect will be the
derived memory cap ([#511](https://github.com/omniscient/dark-factory/issues/511)).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Cost report shows no savings line | No `context-budget.<scenario>.json`, schema v1, or zero delivered saving (check `delivered-savings.jsonl`) | Check for `context_budget.py` errors in the run log |
| Architecture slice always falls back | Safety keyword in labels or changed files | Expected behavior; check `fallback_reason` in `context-budget.<scenario>.json` |
| Agent still reads the full ARCHITECTURE.md | Slicing is telemetry-only | Expected today; see [#487](https://github.com/omniscient/dark-factory/issues/487) |
| Memory returns all entries (ignores top-k) | `TOKEN_OPTIMIZATION_MEMORY_ENABLED=false` (env, or `memory.enabled: false` in the config of the clone) | Check `memory_enabled` in `memory-trace.json` |
| Comment digest skipped | `TOKEN_OPTIMIZATION_COMMENTS_ENABLED=false`, or the run is not a `continue` | Set to `true` in the target's `.archon/.env` and restart the scheduler |
| Full diff passed to code-review | `TOKEN_OPTIMIZATION_DIFF_ENABLED=false` | Set to `true` in the target's `.archon/.env` and restart the scheduler |
| `over_budget` fires on conformance / code-review | Reserved context (CLAUDE.md + safety fallback) reaches the 22k budget | Raise `budgets.<scenario>` in the adapter's `token_optimization` block, or use Tier 2 rollback to disable enforcement for that scenario |
| Enforcement seems to have no effect | Often expected: derived caps are clamped to `[floor, default]`, so unless the reserved context (CLAUDE.md + `issue_context`) is large they equal the config defaults (see the table under Budget Enforcement). Otherwise: per-scenario `enforce` flag is `false`; `enforce_budgets: false`; or the kill switch is set | Check the run's `.claude/skills/refinement/config.yaml` and `.archon/.env`; a change to `config/config.yaml` needs an image publish |
| An env change in `.archon/.env` is ignored | Scheduler was not restarted, so run containers still load the old copy | Recreate the scheduler (see Tier 0) |

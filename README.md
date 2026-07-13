# Dark Factory

An autonomous development agent that polls a GitHub Projects v2 board, picks up
Ready tickets, and drives them through a full factory pipeline — refine, plan,
implement, conformance, code review — producing a draft PR for human review.

Originally extracted from [omniscient/markethawk](https://github.com/omniscient/markethawk).
See the [extraction plan](https://github.com/omniscient/markethawk/blob/main/docs/superpowers/plans/2026-07-03-dark-factory-extraction-p0-p1.md)
for the full design and phasing.

---

## What / Why

The Dark Factory automates the mechanical parts of the development loop so that
humans can focus on deciding what to build rather than building it.  Given a
GitHub issue with an acceptance-tested specification, the factory:

1. Refines the ticket into an implementable spec (refinement pipeline).
2. Plans the implementation (architect pass).
3. Implements, runs CI, and applies conformance/code-review gates autonomously.
4. Opens a draft PR with full context and waits for human approval.

The factory is **product-agnostic**: all target-specific knowledge lives in a
`.factory/adapter.yaml` file committed to the target repo (or defaults to
MarketHawk-parity when the file is absent).

---

## Architecture

```
  ┌──────────────────────────────┐
  │      Backlog Scheduler       │  polls GitHub Projects v2 board every POLL_INTERVAL s
  │  (scheduler.sh, always-on)   │
  └────────────┬─────────────────┘
               │ docker compose run
               ▼
  ┌──────────────────────────────┐
  │    Per-issue Factory Run     │  ephemeral container, one per ticket
  │      (entrypoint.sh)         │
  └────────────┬─────────────────┘
               │ git clone
               ▼
  ┌──────────────────────────────┐
  │    Target Repo Clone         │  fresh clone at FACTORY_CLONE_DIR
  │  /workspace/project (ro)     │  adapter.yaml read here (not from image)
  └────────────┬─────────────────┘
               │ loads
               ▼
  ┌──────────────────────────────┐
  │   .factory/adapter.yaml      │  target-specific overrides (optional)
  │  (clone-read semantics)      │  deep-merged over built-in defaults
  └──────────────────────────────┘
```

**Clone-read semantics**: the factory reads `.factory/adapter.yaml` and hook
scripts from the *fresh clone* of the target repo, not from the baked image.
Committing a change to `.factory/` in the target repo takes effect on the next
dispatch — no image rebuild required.

**Self-contained fallbacks**: at run start the entrypoint copies baked pieces
into the clone *only where the target repo does not provide them* —
`dark-factory/scripts/` (factory scripts + `factory_core`),
`.archon/workflows/`, and `.archon/commands/`.  Every fallback copy is
appended to the clone's `.git/info/exclude`, so it can never be committed
back to the target repo.  Targets that still commit their own copies
(transition period) are untouched.  The effective refinement config is
likewise resolved per run: when the target commits no
`.claude/skills/refinement/config.yaml`, the factory materializes one from
the baked defaults plus the adapter's `token_optimization` block (also
git-excluded); when the target does commit one, it wins byte-identically.

---

## Quickstart

### Prerequisites

- Docker with Compose v2
- A GitHub Projects v2 board with the standard column set
  (Backlog, Ready, In Progress, In Review, Blocked, Refined, Done)
- A GitHub token with `repo`, `project`, and `workflow` scope
- A Claude authentication credential (`CLAUDE_CODE_OAUTH_TOKEN` for Max plan,
  or `ANTHROPIC_API_KEY` for direct API access)

### 1. Clone the dark-factory repo

```bash
git clone https://github.com/omniscient/dark-factory.git
cd dark-factory
```

### 2. Create instance.env

```bash
cp deploy/instance.env.example deploy/instance.env
$EDITOR deploy/instance.env   # fill in GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN, identity vars
```

For a non-MarketHawk target, every `FACTORY_*` identity variable must be
overridden. Run the GraphQL queries in the example file's comments to retrieve
your project board IDs.

Per-instance configs live under `deploy/instances/` (markethawk: see [`docs/cutover-markethawk.md`](docs/cutover-markethawk.md)).

### Provider selection (optional)

Three env vars select the tracker/code-host/model-endpoint providers,
each defaulting to today's behavior when unset:

```bash
FACTORY_TRACKER=github          # ticket tracker (only "github" implemented today)
FACTORY_CODEHOST=github         # code host (only "github" implemented today)
FACTORY_MODEL_PROVIDER=anthropic  # anthropic | bedrock | vertex | databricks | openai
```

`databricks`/`openai` are recognized but not yet implemented (the model
gateway is a later step); an unknown value for any of the three, or
missing provider-specific required env, fails startup loudly via
`providers preflight` — run it directly to check your configuration:

```bash
python3 scripts/factory_core/providers/cli.py preflight
```

### 3. Point PROJECT_DIR at your target repo

```bash
export PROJECT_DIR=/path/to/your-repo   # absolute path on the host
```

The scheduler bind-mounts this path read-only to `/workspace/project` inside
the container so it can read `config/config.yaml` and `.factory/adapter.yaml`
from the current branch without cloning.

### 4. Start the scheduler

```bash
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f backlog-scheduler
```

The scheduler polls the board every `POLL_INTERVAL` seconds (default: 60).
When a Ready ticket is found, it dispatches an ephemeral factory run container.

### 5. Verify

Move a ticket to the **Ready** column on your GitHub project board.  Within one
poll interval, you should see a factory run container start (`docker ps`) and
progress logged to the scheduler stream.

---

## Adapter contract

The target repo may commit a `.factory/adapter.yaml` to customize factory
behaviour.  When the file is absent, built-in MarketHawk defaults apply.

### adapter.yaml keys

| Key | Type | Description |
|-----|------|-------------|
| `schema_version` | `int` | Must be `1`. |
| `components` | map | Maps component label (`backend`, `frontend`, …) to a list of ARCHITECTURE.md section names used for context slicing. |
| `safety.sensitive_keywords` | `string` | Pipe-separated regex of sensitive topic keywords; matched tickets skip autopilot and go to human review. |
| `safety.hard_exclude_paths` | `list[str]` | Path prefixes the factory will never touch; matched diff paths abort the run. |
| `safety.dispatch_ceiling_keywords` | `string` | Pipe-separated regex; matching ticket titles trigger the dispatch ceiling (L tickets parked). |
| `safety.critical_diff_paths` | `list[str]` | Regex patterns; diffs touching these paths are flagged as Critical in the blast-radius gate. |
| `safety.migration_seed_auth_patterns` | `list[str]` | Regex patterns; diffs matching these require explicit human sign-off. |
| `safety.main_red_allowed_paths` | `list[str]` | Path prefixes the main-red auto-fixer is allowed to modify. |
| `memory_routing` | map | Maps glob patterns to memory file paths inside the target repo. |
| `deconflict` | map | Paths for models index (`models_init`) and migrations dir (`migrations_dir`) used by the deconflict guard. |
| `token_optimization` | map | **Active.** Per-scenario token budget overrides (deep-merged; see `config/config.yaml` for schema). Resolution order, highest wins: adapter > clone `.claude/skills/refinement/config.yaml` (transition period) > baked `config/config.yaml` defaults — resolved per run by `factory_core.effective_config`. |

All keys are optional and deep-merged over the built-in defaults.

### Hooks

Place executable scripts at `.factory/hooks/<name>` in the target repo.
The factory discovers and runs them at the appropriate pipeline stage.

| Hook name | Stage | Gate? | Description |
|-----------|-------|-------|-------------|
| `smoke-gate` | Pre-dispatch | Yes (check-only) | Exit 0 = green, non-zero = red. Factory keeps sentinel + regression-ticket handling regardless of hook presence. Built-in default: tsc + backend import checks (MarketHawk). |
| `validate` | Deconflict | No | Post-merge validation (lint, type-check, etc.). Built-in default: no-op (deconflict flow falls back to inline tsc). |
| `preview-up` | Post-implement | No | Spins up a preview stack for the PR branch. Built-in default: no-op. |
| `preview-down` | PR closed | No | Tears down the preview stack. Built-in default: no-op. |

**Hook env contract**: the factory exports these variables to every hook process:

| Variable | Description |
|----------|-------------|
| `CLONE_DIR` | Absolute path to the target repo clone inside the run container. |
| `ARTIFACTS_DIR` | Directory where the run stores intermediate artifacts. |
| `ISSUE_NUM` | The GitHub issue number being processed. |
| `FACTORY_REPO_SLUG` | `owner/repo` of the target repository. |

**Gate semantics**: for most hooks, `--gate` propagates the exit code to the
factory pipeline — a non-zero exit marks the ticket Blocked and stops the run.
Non-gate hooks always return success to the pipeline.

**smoke-gate is check-only**: the hook supplies only the pass/fail signal
(exit 0 = green, non-zero = red).  All state machinery — writing/clearing the
`main-is-red` sentinel, filing/closing the regression ticket, and clean-halting
with exit 0 — stays factory-side and runs identically whether the check comes
from a target hook or the built-in default.  This means you never need to
replicate sentinel or ticket logic in your hook.

### Bench parity

`bench/run_suite.sh` is baked into the image at `/opt/dark-factory/bench/run_suite.sh`
so it can drive parity runs against a cloned target repo without requiring a
separate dark-factory checkout.

Set `BENCH_TARGET_DIR` to point the suite at a specific clone:

```bash
# Run the suite against a pre-cloned MarketHawk checkout
BENCH_TARGET_DIR=/workspace/markethawk \
  bash /opt/dark-factory/bench/run_suite.sh --tasks /opt/dark-factory/bench/suite.json --dry-run
```

Without `BENCH_TARGET_DIR`, the suite resolves the repo root from its own
location (the dark-factory checkout), which is the normal local development
workflow.  Passing `--tasks FILE` overrides the manifest path so you can supply
a target-specific suite alongside `BENCH_TARGET_DIR`.

---

## Weekly dispatch-ceiling revisit

A generic, env-driven maintenance capability that tunes the dispatch-ceiling
keyword list (`dispatch_ceiling.keywords` / `ABOVE_CEILING_KEYWORDS`). Weekly it
builds a **Factory Scorecard** (`scripts/fetch_scorecard.py`), measures each
above-ceiling keyword's success rate against the M-size baseline
(`scripts/ceiling_revisit.py`), and — via the Archon command
[`commands/ceiling-revisit.md`](commands/ceiling-revisit.md) — posts an analysis
comment, optionally opens a PR editing `.archon/.env`, and files next week's
revisit issue.

No target repo is hardcoded: the scripts resolve identity from
`FACTORY_REPO_SLUG`, `FACTORY_EMAIL`, and `FACTORY_PRODUCT_NAME` (defaults =
MarketHawk parity, matching `scripts/identity.sh`), with `--repo` /
`--factory-email` overrides on `fetch_scorecard.py`.

---

## Rollback

The factory relies on **clone-read semantics**: every run clones the target
repo fresh from the default branch.  This means:

- Rolling back a `.factory/adapter.yaml` change requires a git revert or commit
  to the target repo's default branch — no image rebuild needed.
- Hook scripts in `.factory/hooks/` are picked up from the clone; reverting the
  commit reverts the hook.
- The scheduler itself (`scheduler.sh`) and entrypoint (`entrypoint.sh`) are
  baked into the image.  Rollback for those requires re-tagging or pinning
  `IMAGE_TAG` in `instance.env`.

### Token budget enforcement rollback (Tier 0 & Tier 1)

Two tiers are available. Tier 0 is instant (no git commit); Tier 1 is durable
and tracked in history.

**Tier 0 — env kill-switch (fastest):** set `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false`
in `instance.env`, then force-recreate the scheduler:

```bash
docker compose -f deploy/docker-compose.yml up -d --force-recreate backlog-scheduler
```

Kill-only semantics: `false`/`0`/`no` forces observe mode on subsequent runs;
the variable can never force enforcement ON. In-flight runs keep their spawn-time env.

**Tier 1 — git (durable):** commit `enforce_budgets: false` (or revert the enabling
commit) to `.factory/adapter.yaml` in the target repo — clone-read, effective on
the next factory run; the only way to durably change budgets/flags.

See `docs/dark-factory-token-optimization.md` for the full operator runbook.

---

## Further reading

- [Extraction plan](https://github.com/omniscient/markethawk/blob/main/docs/superpowers/plans/2026-07-03-dark-factory-extraction-p0-p1.md) — P0 extract + P1 generalize design
- [`docs/dark-factory-token-optimization.md`](docs/dark-factory-token-optimization.md) — token optimization operator guide
- [`docs/dark-factory-memory-contract.md`](docs/dark-factory-memory-contract.md) — memory schema and lifecycle
- [`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`](docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md) — Claude Skills naming, safety, and tool-permission policy
- [`docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md`](docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md) — Claude Skills rollout-status runbook: per-scenario advisory state and rollback steps
- [`config/config.yaml`](config/config.yaml) — all policy knobs with inline documentation
- [`bench/baseline.md`](bench/baseline.md) — replay benchmark task manifest and scoring formula

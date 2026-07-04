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
| `token_optimization` | map | Per-scenario token budget overrides (deep-merged; see `config/config.yaml` for schema). |

All keys are optional and deep-merged over the built-in defaults.

### Hooks

Place executable scripts at `.factory/hooks/<name>` in the target repo.
The factory discovers and runs them at the appropriate pipeline stage.

| Hook name | Stage | Gate? | Description |
|-----------|-------|-------|-------------|
| `smoke-gate` | Pre-dispatch | Yes | Blocks dispatch if main is broken. Built-in default: tsc + backend import checks (MarketHawk). |
| `validate` | Post-implement | No | Validates the working tree (lint, type-check, etc.). Built-in default: no-op. |
| `preview-up` | Post-implement | No | Spins up a preview stack for the PR branch. Built-in default: no-op. |
| `preview-down` | PR closed | No | Tears down the preview stack. Built-in default: no-op. |

**Hook env contract**: the factory exports these variables to every hook process:

| Variable | Description |
|----------|-------------|
| `CLONE_DIR` | Absolute path to the target repo clone inside the run container. |
| `ARTIFACTS_DIR` | Directory where the run stores intermediate artifacts. |
| `ISSUE_NUM` | The GitHub issue number being processed. |
| `FACTORY_REPO_SLUG` | `owner/repo` of the target repository. |

**Gate semantics**: hooks called with `--gate` propagate their exit code to the
factory pipeline. A non-zero exit from a gate hook marks the ticket Blocked and
stops the run.  Non-gate hooks always return success to the pipeline regardless
of their exit code.

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

### Token budget enforcement kill-switch

If budget enforcement causes unexpected run failures, it can be disabled by
committing a `.factory/adapter.yaml` change to the target repo:

```yaml
token_optimization:
  enforce_budgets: false
```

**Note:** `enforce_budgets` does not have an environment variable override by
design — rollback must go through git so the decision is tracked in history.
See `docs/dark-factory-token-optimization.md` for the full operator runbook.

---

## Further reading

- [Extraction plan](https://github.com/omniscient/markethawk/blob/main/docs/superpowers/plans/2026-07-03-dark-factory-extraction-p0-p1.md) — P0 extract + P1 generalize design
- [`docs/dark-factory-token-optimization.md`](docs/dark-factory-token-optimization.md) — token optimization operator guide
- [`docs/dark-factory-memory-contract.md`](docs/dark-factory-memory-contract.md) — memory schema and lifecycle
- [`config/config.yaml`](config/config.yaml) — all policy knobs with inline documentation
- [`bench/baseline.md`](bench/baseline.md) — replay benchmark task manifest and scoring formula

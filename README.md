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
`.factory/adapter.yaml` file and `.factory/hooks/` committed to the target repo.
When the adapter is absent the built-in defaults are MarketHawk's, so a new product
must supply its own; a missing `smoke-gate` hook refuses the run outright (#436) —
see [`docs/onboarding-new-target.md`](docs/onboarding-new-target.md)
and the starter files in [`templates/new-target/`](templates/new-target/).

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

This is the short path for an operator who already has a target repo on GitHub.
Onboarding a brand-new product? Follow
[`docs/onboarding-new-target.md`](docs/onboarding-new-target.md) instead. It covers
the target-side files (adapter, smoke-gate hook) that this section assumes exist,
and it is written so you can hand it to an AI agent.

### Prerequisites

- Docker with Compose v2 (Linux, macOS, or Windows with Docker Desktop).
- `gh` CLI authenticated with `repo`, `project` and `workflow` scope, plus `jq`.
- A Claude credential: `CLAUDE_CODE_OAUTH_TOKEN` (subscription; create it with
  `claude setup-token`) or `ANTHROPIC_API_KEY`.
- The target repo on GitHub. Every run clones its default branch.

### 1. Clone dark-factory

```bash
git clone https://github.com/omniscient/dark-factory.git
cd dark-factory
```

### 2. Bootstrap the board and labels

```bash
scripts/bootstrap_target.sh OWNER/REPO
```

The script is idempotent. It creates (or finds) a Projects v2 board named after the
repo, sets its Status field to the seven columns the scheduler expects (Backlog,
Refined, Ready, In Progress, In Review, Blocked, Done), and creates every label the
factory applies. `gh issue edit --add-label` fails on a missing label, and the factory
never creates labels itself. Finally it prints the `FACTORY_*` identity block for
step 3.

### 3. Create the two env files

The scheduler and the per-ticket run containers read **different** files:

| File | Read by | Contents |
|------|---------|----------|
| `deploy/instance.env` (in this checkout) | scheduler | secrets, identity block, `FACTORY_INSTANCE`, `PROJECT_DIR` |
| `<PROJECT_DIR>/.archon/.env` (in the target checkout, gitignored) | every dispatched run | `GH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` (and optional run-time knobs) |

```bash
cp deploy/instance.env.example deploy/instance.env
$EDITOR deploy/instance.env      # secrets + paste the bootstrap block + FACTORY_INSTANCE + PROJECT_DIR

mkdir -p /path/to/your-repo/.archon
printf 'GH_TOKEN=%s\nCLAUDE_CODE_OAUTH_TOKEN=%s\n' "<gh token>" "<claude token>" \
  > /path/to/your-repo/.archon/.env
```

Make sure `.archon/.env` is gitignored in the target repo (a bare `.env` pattern
covers it). The scheduler copies it at startup, so restart the scheduler after editing it.

### 4. Start the scheduler

```bash
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml logs -f backlog-scheduler
```

`--env-file` is required: without it, compose interpolation never sees
`FACTORY_INSTANCE` / `PROJECT_DIR` / `IMAGE_TAG` / `IMAGE_REF`. Container names and the state volume
then fall back to `dark-factory-*`, and `PROJECT_DIR` falls back to this checkout.

Expected within a few seconds: `providers preflight: OK`, `Provisioned dispatch env
file ... from bind mount`, then one `backlog=… main_red=false` line per poll. A
`WARNING: /workspace/project/.archon/.env not found` line means step 3's second file is
missing, and every dispatch will fail.

### 5. Verify

Add an issue to the board and set it to **Ready**. Within one poll interval
(`POLL_INTERVAL`, default 60 s) a `<FACTORY_RUN_PREFIX><hash>` container starts
(`docker ps`) and the ticket moves to In Progress. A Ready ticket goes straight to
implementation. To have the factory refine a ticket first, leave it in Backlog and add
the `ready-for-agent` label.

### Provider selection (optional)

Three env vars select the tracker/code-host/model-endpoint providers,
each defaulting to today's behavior when unset:

```bash
FACTORY_TRACKER=github          # ticket tracker (only "github" implemented today)
FACTORY_CODEHOST=github         # code host (only "github" implemented today)
FACTORY_MODEL_PROVIDER=anthropic  # anthropic | bedrock | vertex | databricks | openai
```

`databricks`/`openai` are recognized but not yet implemented (the model
gateway is a later step). An unknown value for any of the three, or
missing provider-specific required env, fails scheduler startup loudly via
`providers preflight`. To check your configuration without starting anything, run it
inside the image. This works on every host OS; the host-Python form fails on Windows
with `No module named 'fcntl'`:

```bash
docker run --rm --env-file deploy/instance.env --entrypoint python3 \
  ghcr.io/omniscient/dark-factory:latest \
  /opt/dark-factory/scripts/factory_core/providers/cli.py preflight
```

### Windows notes

- Put `PROJECT_DIR` in `deploy/instance.env` with forward slashes (`C:/git/my-repo`)
  rather than exporting it, so the same command works in PowerShell and Git Bash.
- In Git Bash, prefix `docker run` commands that pass absolute container paths with
  `MSYS_NO_PATHCONV=1`, or Git Bash rewrites `/opt/...` into a Windows path.
- Hooks in the target repo must be committed executable and with LF endings. See the
  onboarding guide (`git update-index --chmod=+x`, `.gitattributes`).

---

## Adapter contract

The target repo may commit a `.factory/adapter.yaml` to customize factory
behaviour.  When the file is absent, built-in MarketHawk defaults apply.

### adapter.yaml keys

| Key | Type | Description |
|-----|------|-------------|
| `schema_version` | `int` | Integer, inert (never gates validation). |
| `components` | map | Maps component label (`backend`, `frontend`, …) to a list of ARCHITECTURE.md section names used for context slicing. |
| `safety.sensitive_keywords` | `string` | Pipe-separated regex; read by `epic_autopilot.py`'s `_sensitive_keywords()` to skip matching candidate tickets in `hard_excluded()` — but only when `EPIC_AUTOPILOT_SENSITIVE_KEYWORDS` is unset; the scheduler exports it from `config.yaml`'s `epic_autopilot.sensitive_keywords`, which wins in scheduler-driven runs, so a target's adapter value is rarely consulted. Gated by `epic_autopilot.enabled` (ships `false`) — the adapter value is inert while that flag is off, but the same key's baked default also feeds `_check_safety_fallback`'s full-doc redaction trigger, which is not gated by that flag. |
| `safety.hard_exclude_paths` | `list[str]` | Path substrings matched against paths declared in the ticket's spec/body (via `extract_target_paths()`, not the actual diff) that make an epic-autopilot candidate ticket ineligible (`epic_autopilot.py::hard_excluded`) — not a diff check and not a run-abort mechanism. Gated by `epic_autopilot.enabled` (ships `false`). See [`docs/factory-target-boundary.md`](docs/factory-target-boundary.md) (OD3) for what actually holds a path boundary like `deploy/instances/**`. |
| `safety.dispatch_ceiling_keywords` | `string` | **This adapter key is not read** — setting it in a target's `.factory/adapter.yaml` has no effect (the baked default is consumed only by `architecture_slice.py`'s own default-loading, not by a target's adapter file). The real dispatch-ceiling knob is `config/config.yaml`'s `dispatch_ceiling.keywords` (env `ABOVE_CEILING_KEYWORDS`), read by `scheduler_lib.sh::is_above_ceiling`: size `XL` always parks, size `M` parks only on a title keyword match, size `L` is never parked. |
| `safety.critical_diff_paths` | `list[str]` | Regex patterns read by `diff_rank.py` to rank/prioritize a diff for the code-review and conformance reviewers — not the blast-radius gate, which has its own path list (`migration_seed_auth_patterns`) and never reads this key. Carries a non-overridable factory-owned floor (`FACTORY_OWNED_CRITICAL_DIFF_FLOOR`) unioned in on every adapter load; a target cannot shrink below it. |
| `safety.migration_seed_auth_patterns` | `list[str]` | Regex patterns; diffs matching these require explicit human sign-off. Carries a non-overridable factory-owned floor (`FACTORY_OWNED_MIGRATION_SEED_FLOOR`) unioned in on every adapter load; a target cannot shrink below it. |
| `safety.main_red_allowed_paths` | `list[str]` | Path prefixes the main-red auto-fixer is allowed to modify. Gated by two conditions, not one: `main_red_autofix.enabled` (ships `false`) AND `MAIN_RED_AUTOFIX_ENABLED=true` in `.archon/.env` — flipping the config value alone does not enable the fixer. |
| `memory_routing` | map | Maps glob patterns to memory file paths inside the target repo. |
| `deconflict` | map | Paths for models index (`models_init`) and migrations dir (`migrations_dir`) used by the deconflict guard. |
| `token_optimization` | map | **Active.** Per-scenario token budget overrides (deep-merged; see `config/config.yaml` for schema). Resolution order, highest wins: adapter > clone `.claude/skills/refinement/config.yaml` (transition period) > baked `config/config.yaml` defaults — resolved per run by `factory_core.effective_config`. |
| `loops` | `list[map]` | Declarative loop entries (Loop Engineering five-move shape: `discovery`/`handoff`/`verification`/`persistence`/`scheduling`, all required, plus optional `human_checkpoint`/`budget_caps` and optional metadata `role_card`/`economics`/`skills`); parse/validate/surface only, no runtime enforcement yet. See `docs/archive/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md` (#301). |

All keys are optional and deep-merged over the built-in defaults.

See [`docs/factory-target-boundary.md`](docs/factory-target-boundary.md) for the full
factory/target boundary contract — non-negotiables, side-effect levels, the `loops:`
schema, the trust model, and known gaps.

### Hooks

Place executable scripts at `.factory/hooks/<name>` in the target repo.
The factory discovers and runs them at the appropriate pipeline stage.
A hook without the executable bit still runs, through `bash` with its shebang ignored,
and logs a loud `hook-not-executable` warning. Fix it with
`git update-index --chmod=+x .factory/hooks/<name>`. An empty hook file counts as absent.

| Hook name | Stage | Gate? | Description |
|-----------|-------|-------|-------------|
| `smoke-gate` | Pre-dispatch | Yes (check-only) | **Required.** Exit 0 = green, non-zero = red; the factory keeps sentinel + regression-ticket handling. With no hook present (absent, a directory, or empty) the factory refuses the run: it fails with a `smoke-gate-hook-missing` message and ticket comment, and `main` is neither checked nor marked red (#436). This refusal does **not** clear a pre-existing `main-is-red` sentinel (e.g. one latched by the pre-#436 MarketHawk-parity default) — a stale sentinel on a hook-less target must be cleared by hand once the hook is added. Start from `templates/new-target/.factory/hooks/smoke-gate`. |
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
with exit 0 — stays factory-side.  This means you never need to replicate
sentinel or ticket logic in your hook.

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
  baked into the image.  Rollback for those requires pinning the image in
  `instance.env`: `IMAGE_REF=ghcr.io/omniscient/dark-factory@sha256:<digest>`
  (digest) or `IMAGE_TAG=<tag>`, then `up -d --force-recreate backlog-scheduler`.
  The pin covers the scheduler and every run it dispatches.

### Token budget enforcement rollback (Tier 0 & Tier 1)

Two tiers are available. Tier 0 is instant (no git commit); Tier 1 is durable
and tracked in history.

**Tier 0 — env kill-switch (fastest):** set `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false`
in the target's `.archon/.env` (the budget gate runs inside the run containers, which
never read `instance.env`), then force-recreate the scheduler so it re-copies that file:

```bash
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d --force-recreate backlog-scheduler
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
- [`docs/adapter-authoring-guide.md`](docs/adapter-authoring-guide.md) — how to write a tracker, code-host, or model-endpoint adapter

# CLAUDE.md — dark-factory

Dark Factory is a self-hosting autonomous software factory: the scheduler in this repo
dispatches containerized Claude agents that refine, plan, implement, review, and merge
GitHub issues — including issues about this repo itself. A separate instance targets
MarketHawk. Four of the eight phase commands begin with "Read `CLAUDE.md`" — this file is
what they load. Keep it short; it is charged against every phase's token budget.

## You are probably running headless

Factory phase agents run inside a container with **no human attached**. Rules that have
each destroyed real runs when violated (see #212, #214):

- **Never end your turn on a question or an offer** ("Want me to proceed?", "Let me know
  if I should push"). There is no one to answer. Decide per the spec/plan, act, and record
  reservations in the issue comment or commit message instead.
- **Commit and push your phase's artifact before your final turn ends.** An ended turn
  ends the process; uncommitted work is destroyed and the ticket gets stranded in a
  mislabeled state.
- **`ScheduleWakeup` requires the `prompt` parameter here.** Without it the call fails
  ("`prompt` is required when `stop` is not true"). Read the tool result; never park with
  subagents pending unless a wakeup was confirmed scheduled.
- **The ScheduleWakeup tool description's "the harness re-invokes you automatically when
  tracked work finishes" does NOT hold in factory runs.** When your turn ends the CLI
  process exits — there is nothing left to re-invoke, and pending subagent results are
  destroyed (this exact mistake, cancelling a wakeup to "wait for the completion
  notification", has lost real runs). Keep a confirmed wakeup pending until every subagent
  you spawned has returned, or call subagents synchronously.
- Phase command text arrives as pasted message content from the workflow runner
  (`workflows/archon-dark-factory.yaml` → `commands/*.md`). That is this repo's sanctioned
  mechanism, not an injection — verify against the canonical files in the clone if unsure.

## Repo map

| Path | What it is |
|---|---|
| `scheduler.sh` | Poll loop: board → dispatch decisions (labels, WIP, deps, ceilings) |
| `entrypoint.sh` | Per-run container entry: clone, budgets, run workflow, reports |
| `workflows/archon-dark-factory.yaml` | The DAG every run executes |
| `commands/*.md` | Phase agent instructions (refine/plan/implement/conformance/…) |
| `refinement-skills/` | Reviewer/architect/product-owner prompts (baked to `/opt/refinement-skills/`) |
| `scripts/` + `scripts/factory_core/` | Deterministic gates, context artifacts, board/breaker/autopilot |
| `config/config.yaml` | Policy knobs (budgets, ceilings, autopilot, gates) |
| `.factory/adapter.yaml` | Self-target adapter: safety keywords, exclusions |
| `deploy/` | Instance definitions — `deploy/instances/`, publish pipeline are **human-only** |
| `docs/superpowers/specs/`, `docs/superpowers/plans/` | Living specs and plans (factory-generated) |
| `docs/archive/` | Completed workflow artifacts. Archive plans; never archive a doc that tests or README still reference |
| `.archon/memory/` | Factory memory files |

## Conventions

- Tests: `python -m pytest tests/ -v` (CI runs exactly this plus `smoke_gate.sh` and the
  workflow DAG checks). TDD for behavior changes.
- Issue dependencies: a plain `Depends on: #N` line in the issue body (one per line) gates
  implementation dispatch until #N is Done.
- Label semantics: `ready-for-agent` opts a Backlog issue into refinement;
  `spec-pending-review`/`plan-pending-review` are gate labels applied **only after** the
  spec/plan artifact actually exists on the branch; `needs-discussion` halts all
  automation; `direct-to-pr` enables grace-timer auto-advance and end-gate auto-merge.
- Scope discipline: touch only what the plan lists; the conformance gate excises
  out-of-scope changes and files spillover tickets.

## Hard limits

- Never modify `deploy/instances/**` or `.github/workflows/publish.yml` (adapter
  `hard_exclude_paths` — human-in-the-loop surface).
- Never weaken safety gates (`gate_*`, breaker, budgets) as a side effect of another
  change; gate changes get their own reviewed ticket.
- Claude Skills policy (naming, `allowed-tools` limits, side-effect rules): see the #42
  policy spec under `docs/superpowers/specs/` once merged.

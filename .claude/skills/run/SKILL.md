---
name: run
description: >
  Launch a Dark Factory scheduler instance for local development or debugging.
  Project-scoped override discovered by Claude Code's global `run` skill.
allowed-tools: Read, Grep, Glob, Bash(command -v docker:*), Bash(docker info:*),
  Bash(docker compose -f deploy/docker-compose.yml up:*),
  Bash(docker compose -f deploy/docker-compose.yml logs:*)
---

# Run

Reference recipe for launching this repo (Dark Factory itself) — the self-target case where a
factory agent is working a ticket about dark-factory, not a downstream target repo.

## Launching a scheduler instance

The one real, actionable self-target run recipe: bring up a scheduler instance.

**Docker requirement: live daemon.** Probe before attempting anything:

```bash
command -v docker && docker info
```

If either command fails, state plainly that this host cannot launch an instance — there is no
daemon-free equivalent for `up -d`. Do not attempt a degraded alternative.

Prerequisites (mirrors `README.md`'s Quickstart steps 2–5):

1. `deploy/instance.env`, copied from `deploy/instance.env.example` and filled in
   (`GH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, `FACTORY_*` identity vars for a
   non-MarketHawk target).
2. `config/config.yaml` and `.factory/adapter.yaml` present (or defaults apply).

Launch:

```bash
docker compose -f deploy/docker-compose.yml up -d
```

Follow the scheduler's poll/dispatch log:

```bash
docker compose -f deploy/docker-compose.yml logs -f backlog-scheduler
```

**Hard exclusion (CLAUDE.md):** `deploy/instances/**` is human-only. Configure a target via
`deploy/instance.env` (the example file above); never create or edit files under
`deploy/instances/`.

## Not applicable — internal machinery

These two areas appear in some issue templates as "recipes to document" but are **not**
runnable self-target recipes for this repo. Documenting them as if they were would waste a
future agent's turn attempting something that was never meant to be invoked directly.

- **Preview stack** (`docker-compose.preview.yml`, `dark-factory/seed/*.sql`): MarketHawk
  target-repo PR-preview machinery — the header of `docker-compose.preview.yml` itself says
  "Preview environment for MarketHawk feature branches," and the seed files are stockscanner
  fixtures. Dark Factory has no `backend/`/`frontend/` of its own. `entrypoint.sh` copies these
  files into every clone unconditionally, but `preview-up` is a no-op for self-target runs
  because no `.factory/hooks/preview-up` exists in this repo. Nothing to launch here.
- **`run-compose.yml`**: scheduler-internal dispatch plumbing. `scheduler.sh`'s `dispatch()`
  invokes it against the baked `/opt/dark-factory/docker-compose.yml` image to spin up each
  per-issue factory run container. No human or agent runs it directly — it is not a `run`
  recipe in the sense this skill documents, and there is no scenario where invoking it directly
  is the right action for a self-target ticket.

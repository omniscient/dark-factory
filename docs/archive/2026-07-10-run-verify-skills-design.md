# Dark Factory Run/Verify Recipe Skills

**Issue:** omniscient/dark-factory#47
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#42 (Claude Skills conventions and safety policy — merged,
`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`)

---

## Overview / Problem Statement

Dark Factory has no documented, discoverable recipe for how to launch or validate **this repo**
(the self-target case: a factory agent working a ticket about dark-factory itself, e.g. this
one). Every phase agent that needs to run the test suite, check the workflow DAG gates, or bring
up a scheduler instance currently has to rediscover the exact commands from `README.md`,
`CLAUDE.md`, `.github/workflows/ci.yml`, and `.factory/hooks/*` each time.

Claude Code ships two global, generically-invocable skills that are built for exactly this
gap: `run` ("Launch and drive this project's app... First looks for a project skill that
already covers launching the app; otherwise falls back to built-in patterns per project type")
and `verify` ("Verify that a code change actually does what it's supposed to... bootstraps this
repo's project verify skill if none exists yet"). Both explicitly describe looking for a
project-scoped skill of the same name before falling back to generic, less-accurate behavior.
This ticket writes that project-scoped content: two reference skills, `.claude/skills/run/` and
`.claude/skills/verify/`, conforming to the naming/layout/tooling policy `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`
(#42) already defines for this repo.

This is a **documentation-only** change: two new `SKILL.md` files recording existing commands.
No new scripts, no changes to phase commands, `scheduler.sh`, or the Archon DAG.

---

## Requirements

Distilled from the issue's acceptance criteria, refined through Q&A (full log below):

1. Two reference skills, `.claude/skills/run/SKILL.md` and `.claude/skills/verify/SKILL.md`,
   each a bare capability noun matching both `name:` frontmatter and directory name per #42 §2.
2. `verify` records the recipes that actually validate a dark-factory change: the `tests/`
   suite, the two workflow-DAG gate scripts, and the CI-only checks — each tagged with its
   Docker-requirement tier (see Requirement 5).
3. `run` records the one recipe that is genuinely actionable for self-target dark-factory work
   (launching a scheduler instance) and explicitly documents that the other two AC-listed areas
   — the preview stack and `run-compose.yml` — are **not applicable** self-target run recipes,
   with a short explanation of what they actually are, so an agent doesn't waste a turn on them.
4. Recipes must be usable both by implementation agents (which have Bash + a Docker-socket-proxy
   connection with `EXEC`/`CONTAINERS`/`IMAGES`/`POST` but not `BUILD`) and by read-only
   validation/review agents (conformance, code-review — reasoning-only personas, no shell) —
   i.e. content must be legible as prose, not assume a specific tool grant.
5. Neither skill may require live host Docker access unconditionally. Both document a
   Docker-requirement tier per recipe (none / CLI-only / live-daemon / CI-only-unreachable) and
   reuse the repo's existing probe-then-skip idiom (`tests/test_run_compose.sh`'s
   `command -v docker && docker compose version` / `docker info` pattern) inline before any
   Docker-touching step — no new script.
6. The issue's "measure whether repeated tool calls decrease" acceptance criterion is satisfied
   as an **observational** expectation (existing cost-report/run-record signals), not new
   measurement tooling — restated explicitly below (Requirement 6a) because its literal reading
   would otherwise conflict with this ticket's doc-only scope and #42 §8's Tier 0 classification.
   - **6a (restated AC):** the `verify` skill documents the intent (cut repeated exploratory
     tool calls) and points at the per-run cost report (`<!-- dark-factory-cost-report -->`,
     posted to each issue) and run-record JSONL as the informal before/after proxy a human or
     future agent can check anecdotally once the skills have been in use. Building dedicated
     tool-call-counting instrumentation is explicitly deferred (Open Questions).
7. Both skills follow #42 §4's `allowed-tools` tiering (enumerated verbs, no bare `Bash(*)`, no
   family wildcards) and #42 §3 (since both are pure recipe/reference content with no
   side-effecting action of their own — they document commands, they don't grant a skill the
   power to merge/deploy/close — `disable-model-invocation` stays unset/false).

---

## Brainstorming Q&A

> **Q1:** Claude Code's global `run`/`verify` skills each look for a project-scoped skill of the
> same name before falling back to generic behavior. Given #42's bare-capability-noun naming
> policy, should this ticket create two separate skills (`run`, `verify`) so each is
> auto-discovered by its matching global skill, or one combined skill under a different name?
>
> **A1:** Two separate skills — `.claude/skills/run/SKILL.md` and `.claude/skills/verify/SKILL.md`.
> The discovery mechanism is name-keyed, and matching it is the actual goal (deterministic
> lookup instead of the probabilistic description-matching a combined skill would fall back to).
> #42 A3 already mandates a bare capability noun matching both `name:` and the directory name;
> `run`/`verify` fit that shape directly, while a combined name like `dark-factory-recipes` would
> collide with the `dark-factory-<phase>` prefix reserved for Archon commands. The two
> capabilities also map cleanly onto the issue's own content split: run = launching things,
> verify = the test suite + gates.

> **Q2:** Are all four issue-listed recipe areas genuine, actionable self-target "run"/"verify"
> recipes, or does dark-factory's own architecture make some of them inapplicable?
>
> **A2:** No — investigation found dark-factory has no `backend/`/`frontend/` of its own.
> `docker-compose.preview.yml` + `dark-factory/seed/*.sql` are MarketHawk-target-only preview
> machinery (header: "Preview environment for MarketHawk feature branches"; seed files are
> stockscanner fixtures). `entrypoint.sh` copies these files into every clone unconditionally,
> but `preview-up` is a no-op for self-target runs (no `.factory/hooks/preview-up` exists in
> this repo) — there is nothing to launch. `run-compose.yml` is scheduler-internal dispatch
> plumbing (`scheduler.sh`'s `dispatch()` invokes it against the baked
> `/opt/dark-factory/docker-compose.yml`; no human or agent runs it directly). The one real
> `run` recipe is `docker compose -f deploy/docker-compose.yml up -d` (launching a scheduler
> instance, configured via `deploy/instance.env` + `config/config.yaml` +
> `.factory/adapter.yaml` — never `deploy/instances/**`, which is CLAUDE.md's human-only hard
> exclusion). `verify` maps cleanly onto real, already-in-use commands: `.factory/hooks/smoke-gate`
> runs exactly `python -m pytest tests/ -q`, `check_workflow_dag.py`, and `check_workflow_when.py`;
> `.factory/hooks/validate` runs the pytest suite; `.github/workflows/ci.yml` additionally runs
> `test_identity.sh`, `test_hooks.sh`, `test_smoke_gate.sh`, `test_run_compose.sh`, and a
> separate `docker-build` job.

> **Q3:** The `run` skill's one real recipe needs a live Docker daemon; `verify`'s core recipes
> need no Docker at all, but CI's `docker-build` job and `test_run_compose.sh` touch Docker at
> different levels. What should both skills document as Docker-unavailable fallback behavior?
>
> **A3:** Tier every recipe by Docker requirement — **none** (pytest, both `check_workflow_*.py`
> gates, `test_identity.sh`/`test_hooks.sh`/`test_smoke_gate.sh`), **CLI-only, no daemon**
> (`docker compose -f run-compose.yml config`, as `test_run_compose.sh` already does), **live
> daemon** (`deploy/docker-compose.yml up -d`), and **CI-only, unreachable from factory
> containers** (`docker build` — the docker-socket-proxy grants `CONTAINERS`/`IMAGES`/`POST`/`EXEC`
> but not `BUILD`; ops memory #436 confirms `--build` over the proxy 403s). Present this as a
> prose tier table in both skills (the thing a reasoning-only reviewer with no shell can
> self-select against), and for any recipe an agent would actually execute, prepend the repo's
> own existing probe-then-skip idiom from `tests/test_run_compose.sh`
> (`command -v docker && docker compose version` for CLI-only steps; `docker info` — which
> round-trips to the daemon, unlike `command -v` — for daemon-requiring steps) rather than
> writing a new script. `docker-build` gets an explicit "CI-only, do not attempt" note so an
> agent never burns a turn on it.

> **Q4:** Should this ticket build new tool-call-measurement instrumentation to satisfy "measure
> whether repeated tool calls decrease," or treat it as an observational expectation?
>
> **A4:** Observational, not new tooling. Building a bench scenario, logging hook, or comparison
> script would itself be a new script — contradicting the issue's own doc-only scope (`.claude/skills/**`
> only) — and #42 §8 classifies pure doc/prompt reorganization as Tier 0 (standard gates only, no
> bench run) specifically to avoid mandating expensive evaluation for non-behavior changes.
> `bench/suite.json`'s oracles are implement-phase-only, so a bench sweep would measure the wrong
> population regardless. The factory already captures a proxy signal — per-run cost reports
> (`<!-- dark-factory-cost-report -->`, token/duration per node) and run-record JSONL — so the
> `verify` skill documents pointing at that existing data as an informal before/after check,
> explicitly named as anecdotal, with dedicated tool-call-counting instrumentation recorded as
> non-blocking future work rather than silently dropped.

---

## Architecture / Approach

### Layout

```
.claude/skills/run/
  SKILL.md
.claude/skills/verify/
  SKILL.md
```

Both skills stay flat (single file, no `templates/`/`references/`/`scripts/` subdirectories) —
#42 §2's graduation rule only promotes a supporting-file kind into a subdirectory at ≥3 files of
that kind or ≥2 distinct kinds; each skill here is one file below that threshold.

### `run/SKILL.md`

**Frontmatter** (illustrative; exact enumeration is a plan/implement-phase decision, tiered per
#42 §4):

```yaml
name: run
description: >
  Launch a Dark Factory scheduler instance for local development or debugging.
  Project-scoped override discovered by Claude Code's global `run` skill.
allowed-tools: Read, Grep, Glob, Bash(docker info:*), Bash(docker compose version:*),
  Bash(docker compose -f deploy/docker-compose.yml up:*),
  Bash(docker compose -f deploy/docker-compose.yml logs:*)
```

**Content:**

1. **Launching a scheduler instance** (the one real recipe): `docker compose -f
   deploy/docker-compose.yml up -d`, prerequisites (`deploy/instance.env` copied from
   `deploy/instance.env.example`, `config/config.yaml`, `.factory/adapter.yaml`), and the
   `docker compose -f deploy/docker-compose.yml logs -f backlog-scheduler` follow-up — mirrors
   README's existing Quickstart steps 2–5. Note the CLAUDE.md hard exclusion:
   `deploy/instances/**` is human-only; an agent configures via the example env file, never by
   editing files under `deploy/instances/`.
2. **Docker requirement:** live daemon — probe with `docker info` first; if it fails, state
   plainly that this host cannot launch an instance (no daemon-free equivalent exists for
   `up -d`) rather than attempting a degraded alternative.
3. **Not applicable — internal machinery** (explicit exclusion, not silence, per the issue's own
   "where appropriate" hedge):
   - *Preview stack* (`docker-compose.preview.yml`, `dark-factory/seed/*.sql`): MarketHawk
     target-repo PR-preview machinery. Dark Factory has no backend/frontend of its own;
     `preview-up` is a no-op for self-target runs. Nothing to launch here.
   - *`run-compose.yml`*: scheduler-internal dispatch plumbing that `scheduler.sh`'s `dispatch()`
     invokes against the baked image. Never invoked directly by a human or agent.

### `verify/SKILL.md`

**Frontmatter** (illustrative, same caveat as above):

```yaml
name: verify
description: >
  Validate a Dark Factory change: test suite, workflow-DAG gates, and CI parity checks.
  Project-scoped override bootstrapped by Claude Code's global `verify` skill.
allowed-tools: Read, Grep, Glob, Bash(python -m pytest tests/:*),
  Bash(python scripts/check_workflow_dag.py:*), Bash(python scripts/check_workflow_when.py:*),
  Bash(bash tests/*.sh:*), Bash(docker compose -f run-compose.yml config:*),
  Bash(command -v docker:*), Bash(docker info:*)
```

**Content:**

1. **Test suite:** `PYTHONPATH=scripts python -m pytest tests/ -v` (the canonical form per
   CLAUDE.md and `ci.yml`). Note the narrower `-q` variant used inline by
   `.factory/hooks/smoke-gate` and `.factory/hooks/validate` (same suite, quiet flag, plus a
   `pip install pytest pyyaml` preamble) so an agent recognizes both invocations as the same
   check rather than two different ones. Docker requirement: none.
2. **Workflow gates:** `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml`
   and `python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml` — exactly what
   `.factory/hooks/smoke-gate` and `ci.yml`'s `dag-check` job run. Docker requirement: none.
3. **CI parity checks:** `bash tests/test_identity.sh`, `test_hooks.sh`, `test_smoke_gate.sh`,
   `test_run_compose.sh` — the remaining `ci.yml` `tests` job steps beyond the pytest suite.
   `test_run_compose.sh` is CLI-only (`docker compose ... config`, no daemon needed); the other
   three need no Docker.
4. **`docker-build` (CI-only, do not attempt):** `docker build -f Dockerfile -t dark-factory:pr .`
   — flagged explicitly as unreachable from inside a factory-dispatched container (the
   docker-socket-proxy blocks the `BUILD` verb), so an agent doesn't waste a turn reproducing it
   locally; CI is the only place it actually runs.
5. **Docker requirement tier table** (none / CLI-only / live-daemon / CI-only), matching the
   `run` skill's tiering so an agent reads one consistent vocabulary across both skills.
6. **Evaluating effectiveness** (Requirement 6a): a short note stating the intent (fewer
   repeated exploratory tool calls once these recipes exist) and pointing at the per-run cost
   report and run-record JSONL as the informal, already-available before/after signal —
   explicitly anecdotal, not a new metric.

---

## Alternatives Considered

1. **Single combined skill** (e.g. `.claude/skills/dark-factory-recipes/SKILL.md`) covering both
   run and verify content. **Rejected.** Breaks the name-keyed discovery that both global `run`
   and `verify` skills use to find a project override — the combined skill would only surface
   via fuzzy description-matching, reintroducing the exact "rediscovery" gamble the issue exists
   to close. Also conflicts with #42's bare-capability-noun naming policy and its
   `dark-factory-<phase>` prefix reservation for Archon commands.
2. **Document all four issue-listed recipe areas as literal runnable recipes**, regardless of
   applicability to self-target work. **Rejected.** The preview stack and `run-compose.yml` are
   inapplicable/internal machinery for this repo (Q2); presenting them as runnable recipes would
   mislead a future agent into wasted turns attempting a MarketHawk preview stack or scheduler
   plumbing that was never meant to be invoked directly. The issue's own "where appropriate"
   hedge on the preview stack anticipated this.
3. **Build dedicated tool-call-count instrumentation** (bench scenario, logging hook, comparison
   script) to satisfy the "measure" acceptance criterion literally. **Rejected.** New scripts
   contradict this ticket's doc-only scope; #42 §8 classifies this change as Tier 0 (no bench
   run); `bench/suite.json`'s oracles are implement-phase-only and would measure the wrong
   population even if run. An observational note pointing at existing cost-report/run-record data
   satisfies the criterion's intent proportionately (Q4).

---

## Open Questions (Non-blocking)

- **Exact `allowed-tools` enumeration** for both `SKILL.md` frontmatters is a plan/implement-phase
  decision. This spec gives illustrative examples (tiered per #42 §4: exact subcommands, no bare
  `Bash(*)`, no family wildcards); the plan should finalize the precise verb list against
  whatever the written recipes actually invoke.
- **Dedicated tool-call-reduction measurement tooling** (a refine/plan-phase bench scenario, a
  logging hook, a comparison script) is explicitly deferred per Q4/A4. If ever pursued, it should
  be its own reviewed ticket — it is evaluation infrastructure, not a doc change, and would need
  refine/plan-phase bench oracles that don't currently exist (`bench/suite.json` is
  implement-phase-only).
- **Formal closure of the preview-stack/`run-compose.yml` "not applicable" framing** if either
  ever gains a real self-target use (e.g. a future `.factory/hooks/preview-up` for this repo, or
  a direct human `run-compose.yml` invocation path) — revisit the `run` skill's exclusion notes
  at that point rather than assuming they stay permanently inapplicable.
- Whether either skill should later graduate a `references/` subdirectory (e.g. a shared
  Docker-tier-table doc referenced by both `SKILL.md` files instead of duplicated prose) — not
  warranted yet at a population of two single-file skills, per #42 §2's graduation threshold.

---

## Assumptions

- **[Flagged]** Claude Code's global `run`/`verify` skills' project-skill discovery is name-keyed
  and file-based, as described in their own tool descriptions ("First looks for a project skill
  that already covers launching the app..."). This repo has no visibility into or control over
  that harness-side lookup mechanism; the two skills are named and shaped to match the documented
  behavior, but this spec cannot directly verify the lookup implementation.
- **[Flagged]** `docker compose -f deploy/docker-compose.yml up -d` is documented as a recipe
  usable by any agent with Docker access, but in practice is primarily a human/local-dev action
  per README's existing Quickstart — the spec does not assume factory-dispatched implement
  containers routinely bring up a second scheduler instance from inside themselves.
- CI's `docker-build` job is documented in `verify` for completeness even though it cannot be
  reproduced from inside a factory-dispatched container (socket-proxy `BUILD` block) — included
  so an agent recognizes what it's reading in CI output rather than assuming a passing/failing
  suite locally is a full CI parity check.

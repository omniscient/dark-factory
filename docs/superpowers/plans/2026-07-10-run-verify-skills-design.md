# Implementation Plan: Dark Factory Run/Verify Recipe Skills

**Issue:** omniscient/dark-factory#47
**Spec:** `docs/superpowers/specs/2026-07-10-run-verify-skills-design.md`
**Depends on:** omniscient/dark-factory#42 (merged — `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`)

---

## Goal

Add two reference skills — `.claude/skills/run/SKILL.md` and `.claude/skills/verify/SKILL.md`
— that record dark-factory's own existing launch/validate recipes, so Claude Code's global
`run`/`verify` skills discover a project-scoped override instead of falling back to generic
per-project-type guessing. **Documentation-only**: no new scripts, no changes to phase
commands, `scheduler.sh`, or the Archon DAG.

## Architecture

Both skills are flat, single-file reference skills conforming to #42's taxonomy (§1–§4):

- Bare capability-noun naming (`run`, `verify`) matching directory name, per #42 §2.
- `allowed-tools` enumerates exact subcommands only — no bare `Bash(*)`, no family
  wildcards (`Bash(docker:*)`, `Bash(gh:*)`) — per #42 §4.
- Read-only, no side effects (they document commands, they don't execute merges/deploys), so
  `disable-model-invocation` stays unset (default false) and `user-invocable` stays unset
  (default true) — matching the existing `.claude/skills/code-review/SKILL.md` and
  `.claude/skills/conformance/SKILL.md` frontmatter precedent exactly (both omit these two
  fields since defaults already apply).
- Content is prose + fenced commands only — legible to a reasoning-only reviewer (conformance,
  code-review personas) with no shell, per spec Requirement 4.
- Every Docker-touching step is preceded by the repo's existing probe-then-skip idiom from
  `tests/test_run_compose.sh`, tiered correctly by what each recipe actually needs:
  - **CLI-only tier** (`verify`'s `test_run_compose.sh` recipe): probe is
    `command -v docker && docker compose version` — exactly what `test_run_compose.sh` line 41
    uses; `docker compose config` never touches a daemon.
  - **Live-daemon tier** (`run`'s scheduler-launch recipe): probe is
    `command -v docker && docker info` — `docker info` round-trips to the daemon, unlike
    `docker compose version`, so it is the correct check before an `up -d` that will actually
    fail without a reachable daemon.

No `bench/run_suite.sh` parity run is warranted: per #42 §8 this is Tier 0 (pure doc addition,
new skill population, zero behavior change to any phase command or DAG node). Standard
`conformance:` + `code_review:` gates only.

## Tech Stack

Markdown (`SKILL.md` frontmatter + prose), Python (`pytest` test files asserting file
existence/content), no new scripts or dependencies.

---

## File Structure

| File | Change |
|---|---|
| `.claude/skills/run/SKILL.md` | New — run-recipe reference skill |
| `.claude/skills/verify/SKILL.md` | New — verify-recipe reference skill |
| `tests/test_run_skill_files.py` | New — frontmatter/content assertions for `run` |
| `tests/test_verify_skill_files.py` | New — frontmatter/content assertions for `verify` |

---

## Memory Context Applied

Two accumulated-memory lessons are baked into the task steps below (not left as a separate
advisory section):

1. **`.archon/memory/architecture.md` [AVOID] (issue #47, refine phase):** self-target tickets
   must verify applicability of `docker-compose.preview.yml`/`run-compose.yml` before
   documenting them as runnable recipes — both are internal/inapplicable machinery for this
   repo's own self-target case. Task 2 below writes the `run` skill's "Not applicable" section
   with this exact reasoning, not as a runnable recipe.
2. **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #42):** a later `implement`-phase
   agent must itself copy this plan and its spec onto the `feat/issue-47-*` branch and commit
   them — they do not transfer automatically from this `refine/issue-47-*` branch. This is
   standard implement-phase behavior (not a step in this plan, which only produces the plan
   document), flagged here so the implement-phase agent that later reads this plan is not
   surprised.

---

## Task 1: Add failing test for the `run` skill file

**Files:** `tests/test_run_skill_files.py` (new)

1. Write the test file:

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "run"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: run" in text
    assert "allowed-tools:" in text
    for tool in (
        "Bash(command -v docker:*)",
        "Bash(docker info:*)",
        "Bash(docker compose -f deploy/docker-compose.yml up:*)",
        "Bash(docker compose -f deploy/docker-compose.yml logs:*)",
    ):
        assert tool in text, f"missing allowed-tools entry: {tool}"


def test_skill_bans_bare_bash_and_family_wildcards():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Bash(*)" not in text
    assert "Bash(docker:*)" not in text
    assert "Bash(docker compose:*)" not in text


def test_documents_launch_recipe():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker compose -f deploy/docker-compose.yml up -d" in text
    assert "deploy/instance.env.example" in text
    assert "deploy/instances/" in text  # hard-exclusion note, per CLAUDE.md


def test_documents_daemon_probe_before_launch():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker info" in text
    assert "live daemon" in text.lower() or "live-daemon" in text.lower()


def test_documents_not_applicable_recipes():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker-compose.preview.yml" in text
    assert "run-compose.yml" in text
    assert "not applicable" in text.lower()
```

2. Verify it fails (file doesn't exist yet):

```bash
pip install --quiet pytest pyyaml
python -m pytest tests/test_run_skill_files.py -v
```

Expected output: `FileNotFoundError` (or similar) in every test — `.claude/skills/run/SKILL.md`
does not exist.

3. Commit:

```bash
git add tests/test_run_skill_files.py
git commit -m "test(run-skill): add failing frontmatter/content assertions for .claude/skills/run"
```

---

## Task 2: Implement `.claude/skills/run/SKILL.md`

**Files:** `.claude/skills/run/SKILL.md` (new)

1. Create the skill file:

```markdown
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
```

2. Verify Task 1's test suite passes:

```bash
python -m pytest tests/test_run_skill_files.py -v
```

Expected output: `5 passed`.

3. Commit:

```bash
git add .claude/skills/run/SKILL.md
git commit -m "feat(run-skill): add .claude/skills/run/SKILL.md scheduler-launch recipe"
```

---

## Task 3: Add failing test for the `verify` skill file

**Files:** `tests/test_verify_skill_files.py` (new)

1. Write the test file:

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "verify"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: verify" in text
    assert "allowed-tools:" in text
    for tool in (
        "Bash(PYTHONPATH=scripts python -m pytest tests/:*)",
        "Bash(python -m pytest tests/:*)",
        "Bash(python scripts/check_workflow_dag.py:*)",
        "Bash(python scripts/check_workflow_when.py:*)",
        "Bash(bash tests/*.sh:*)",
        "Bash(docker compose -f run-compose.yml config:*)",
        "Bash(command -v docker:*)",
        "Bash(docker compose version:*)",
    ):
        assert tool in text, f"missing allowed-tools entry: {tool}"


def test_skill_bans_bare_bash_and_family_wildcards():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Bash(*)" not in text
    assert "Bash(gh:*)" not in text
    assert "Bash(python:*)" not in text
    assert "Bash(docker:*)" not in text
    assert "Bash(docker compose:*)" not in text


def test_documents_test_suite_recipe_both_invocations():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "PYTHONPATH=scripts python -m pytest tests/ -v" in text
    assert "python -m pytest tests/ -q" in text
    assert ".factory/hooks/smoke-gate" in text
    assert ".factory/hooks/validate" in text


def test_documents_workflow_gates():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "scripts/check_workflow_dag.py" in text
    assert "scripts/check_workflow_when.py" in text


def test_documents_ci_parity_checks():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for f in (
        "test_identity.sh",
        "test_hooks.sh",
        "test_smoke_gate.sh",
        "test_run_compose.sh",
    ):
        assert f in text


def test_documents_run_compose_probe_is_cli_only_tier():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker compose version" in text
    assert "CLI-only" in text


def test_documents_docker_build_as_ci_only():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker build" in text
    assert "CI-only" in text


def test_documents_docker_tier_table():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "live daemon" in text.lower() or "live-daemon" in text.lower()
    assert "CLI-only" in text


def test_documents_evaluation_note():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "cost-report" in text or "cost report" in text
    assert "run-record" in text
```

2. Verify it fails:

```bash
python -m pytest tests/test_verify_skill_files.py -v
```

Expected output: `FileNotFoundError` (or similar) in every test —
`.claude/skills/verify/SKILL.md` does not exist.

3. Commit:

```bash
git add tests/test_verify_skill_files.py
git commit -m "test(verify-skill): add failing frontmatter/content assertions for .claude/skills/verify"
```

---

## Task 4: Implement `.claude/skills/verify/SKILL.md`

**Files:** `.claude/skills/verify/SKILL.md` (new)

1. Create the skill file:

````markdown
---
name: verify
description: >
  Validate a Dark Factory change: test suite, workflow-DAG gates, and CI parity checks.
  Project-scoped override bootstrapped by Claude Code's global `verify` skill.
allowed-tools: Read, Grep, Glob, Bash(PYTHONPATH=scripts python -m pytest tests/:*),
  Bash(python -m pytest tests/:*), Bash(python scripts/check_workflow_dag.py:*),
  Bash(python scripts/check_workflow_when.py:*), Bash(bash tests/*.sh:*),
  Bash(docker compose -f run-compose.yml config:*), Bash(command -v docker:*),
  Bash(docker compose version:*)
---

# Verify

Reference recipes that actually validate a dark-factory change, in the order CI runs them
(`.github/workflows/ci.yml`).

## Docker requirement tier table

| Tier | Recipes |
|---|---|
| none | pytest suite, both `check_workflow_*.py` gates, `test_identity.sh`, `test_hooks.sh`, `test_smoke_gate.sh` |
| CLI-only (no daemon) | `test_run_compose.sh` (`docker compose ... config`) |
| live daemon | none in this skill — see the `run` skill's scheduler-launch recipe |
| CI-only, unreachable from a factory container | `docker build` |

## 1. Test suite

Canonical form (`CLAUDE.md`, `ci.yml`):

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

`.factory/hooks/smoke-gate` and `.factory/hooks/validate` run the same suite inline as:

```bash
pip install --quiet --no-warn-script-location pytest pyyaml
python -m pytest tests/ -q
```

Same check as above (quiet flag, no separate `PYTHONPATH` export in the hook script) — treat
both invocations as one check, not two different ones.

Docker requirement: none.

## 2. Workflow gates

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Exactly what `.factory/hooks/smoke-gate` and `ci.yml`'s `dag-check` job run.

Docker requirement: none.

## 3. CI parity checks

```bash
bash tests/test_identity.sh
bash tests/test_hooks.sh
bash tests/test_smoke_gate.sh
bash tests/test_run_compose.sh
```

The remaining `ci.yml` `tests` job steps beyond the pytest suite. `test_run_compose.sh` probes
before touching Docker at all:

```bash
command -v docker && docker compose version
```

If either fails, it prints `SKIP: docker not available — skipping compose config parse check`
and exits 0 rather than attempting `docker compose -f run-compose.yml config` — the CLI-only
tier's probe-then-skip idiom. This tier never touches a daemon; `docker compose version` is
sufficient, unlike the `run` skill's live-daemon recipe which needs `docker info`.

Docker requirement: CLI-only (no daemon) for `test_run_compose.sh`; none for the other three.

## 4. `docker-build` (CI-only — do not attempt)

```bash
docker build -f Dockerfile -t dark-factory:pr .
```

Runs only in `.github/workflows/ci.yml`'s `docker-build` job. Unreachable from inside a
factory-dispatched container: the docker-socket-proxy grants `EXEC`/`CONTAINERS`/`IMAGES`/
`POST` but blocks `BUILD` (ops memory #436 confirms `--build` over the proxy 403s). Do not
attempt to reproduce this locally from a run container — recognize it as CI-only output when
reading a CI run, not as a check to replicate.

## 5. Evaluating effectiveness

Intent: fewer repeated exploratory tool calls once these recipes exist, replacing ad hoc
rediscovery of how to test/validate this repo. This is checked informally, not with new
instrumentation — point at the existing per-run cost report
(`<!-- dark-factory-cost-report -->`, posted to each issue) and the run-record JSONL as an
anecdotal before/after signal. Dedicated tool-call-count instrumentation is explicitly deferred
as non-blocking future work (see the design spec's Open Questions) — building it here would
itself be a new script, contradicting this ticket's doc-only scope.
````

2. Verify Task 3's test suite passes:

```bash
python -m pytest tests/test_verify_skill_files.py -v
```

Expected output: `9 passed`.

3. Commit:

```bash
git add .claude/skills/verify/SKILL.md
git commit -m "feat(verify-skill): add .claude/skills/verify/SKILL.md test/gate recipes"
```

---

## Task 5: Full-suite verification

**Files:** none (verification only)

1. Run the complete test suite to confirm no regression:

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

Expected output: all tests pass, including the two new files (`test_run_skill_files.py`,
`test_verify_skill_files.py`), with no changes to any pre-existing test's pass/fail status.

2. Run the two workflow-DAG gate scripts (unaffected by this change, but part of the standard
   pre-publish check per `smoke-gate`):

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected output: both exit 0 with no errors — this change touches no workflow YAML.

3. No commit in this task — it is a verification checkpoint confirming Tasks 1–4 left the repo
   green before the plan is published.

---

## Out of Scope (explicitly, per spec)

- No changes to `context_budget.py`'s `_SKILL_PROMPT_FILES` or any token-budget accounting —
  unlike `code-review`/`conformance`, the `run`/`verify` skills are not injected into any phase
  command's subagent prompt; they are discovered independently by Claude Code's own global
  `run`/`verify` skills. Nothing in the factory pipeline reads their content programmatically.
- No changes to `CLAUDE.md`'s repo map table — the existing `code-review`/`conformance` skills
  (issue #44) were not added there either; this repo's convention is that `.claude/skills/**`
  additions don't require a CLAUDE.md repo-map entry.
- No changes to `.factory/adapter.yaml`'s `safety.hard_exclude_paths` — that follow-up is
  tracked under #42 §7, not this ticket.
- No new tool-call-count measurement tooling (Requirement 6a / Q4-A4 in the spec).

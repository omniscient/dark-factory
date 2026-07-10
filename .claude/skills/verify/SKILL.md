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

# classify-preview prompt, pinned for the hosting shadow comparator (#563)

Frozen copy: the shadow runner never reads the live DAG file. Below the delimiter is the
node's `prompt` value exactly as yaml.safe_load returns it. Re-pin only in a reviewed
change that bumps RUNNER_VERSION; no test compares this file to the live DAG.

source_commit: 1628a6e06144b6ea0e6d27b0f25b940a1b8a0739
source_file: workflows/archon-dark-factory.yaml
source_lines: 863-896
---8<--- prompt below ---
You are a classifier deciding whether a dark-factory preview stack (postgres, redis, backend,
frontend, celery) is needed for this branch's changeset. A wasted preview costs minutes; a
wrongly skipped preview means a code change ships without live endpoint validation.

## Config kill-switch
Read the preview.enabled flag from .claude/skills/refinement/config.yaml.
If preview.enabled is false, ALWAYS output needs_preview=true regardless of the changeset.

## Changeset
$preview-changeset.output

## Issue context (secondary signal only — the diff is authoritative)
Title: $fetch-issue.output.title
Labels: $fetch-issue.output.labels

## Classifier rubric

Set needs_preview = false ONLY if EVERY changed file falls into one or more of these
non-runtime-affecting categories:

| Category        | Matches |
|-----------------|---------|
| Documentation   | *.md, *.mdx, anything under docs/ or Docs/, LICENSE |
| Agent/workflow config | .archon/**, .claude/**, workflow YAML (NOT docker-compose* or app config) |
| Tests           | backend/tests/**, **/*.test.ts(x), **/*.spec.ts(x), conftest.py, **/*_test.py, dark-factory/tests/** |
| CI / repo meta  | .github/**, .gitignore, .pre-commit-config.yaml, lint/format config (.eslintrc*, .prettierrc*, ruff/flake8/mypy config), .editorconfig |

Force needs_preview = true if ANY file touches the running app: backend/app/**, alembic/versions/**,
requirements.txt, frontend/src/**, package.json, package-lock.json, Dockerfile*, docker-compose*,
.env*, dark-factory/seed/**.

Fail-safe: an empty changeset, a mix that is not clearly skip-only, or any uncertainty → needs_preview=true.
The diff is authoritative — a documentation-labeled issue that touches backend/app/** still gets a preview.

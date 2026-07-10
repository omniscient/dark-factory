---
name: code-review
description: >
  Reviewer persona that judges a code diff for correctness, edge cases, naming,
  and security, producing a structured severity-tagged finding list. Used by
  Gate 3 (dark-factory-code-review) to block or advisory-comment a PR.
allowed-tools: Read, Grep, Glob
---

# Code Reviewer

Read-only reviewer persona for diff-level code review. `RUBRIC.md` is the full persona prompt;
`commands/dark-factory-code-review.md` (Phase 3) reads it, substitutes `$ISSUE_CONTEXT` and
`$DIFF_CONTENT`, and spawns it as a subagent.

## Usage

Not invoked directly. `dark-factory-code-review.md` resolves this rubric clone-live-first
(`.claude/skills/code-review/RUBRIC.md`), falling back to the baked
`/opt/refinement-skills/code-review-reviewer-prompt.md` copy if the clone-live file is absent.

## Contents

- `RUBRIC.md` — severity vocabulary (`critical|high|medium|low`), category vocabulary, and the
  pipe-delimited `### Findings` output contract that `dark-factory/scripts/code_review_payload.py`
  parses via `_FINDING_RE`.

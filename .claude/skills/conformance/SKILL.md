---
name: conformance
description: >
  Reviewer persona that judges whether an implementation plan or code diff stays
  faithful to its approved spec — approach fidelity, constraint adherence, scope,
  and requirement satisfaction. Used by Gate 2 (dark-factory-conformance) and the
  plan phase's Phase 3.5 plan-vs-spec check.
allowed-tools: Read, Grep, Glob
---

# Conformance Reviewer

Read-only reviewer persona for spec-conformance checks. `RUBRIC.md` is the full persona prompt;
`commands/dark-factory-plan.md` (Phase 3.5) and `commands/dark-factory-conformance.md` (Phase 3)
read it, substitute `$ARTIFACT_KIND`, `$SPEC_CONTENT`, and `$ARTIFACT_CONTENT`, and spawn it as a
subagent.

## Usage

Not invoked directly. The Archon commands above resolve this rubric clone-live-first
(`.claude/skills/conformance/RUBRIC.md`), falling back to the baked
`/opt/refinement-skills/conformance-reviewer-prompt.md` copy if the clone-live file is absent.

## Contents

- `RUBRIC.md` — reviewer instructions, verdict tiers (`CONFORMS` / `MINOR DEVIATION` /
  `MATERIAL DIVERGENCE`), and the machine-parsed `## Out-of-Scope Changes` / `**Verdict:**`
  output contract that `commands/dark-factory-conformance.md` regex-parses.

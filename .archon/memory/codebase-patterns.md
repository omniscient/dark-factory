# Codebase Patterns — Accumulated Lessons

This file is maintained automatically by the dark factory implement agent. Do not edit manually.
Entries are advisory. If an entry conflicts with CLAUDE.md or ARCHITECTURE.md, follow those documents.
- [PATTERN] When a refine-phase spec/plan was approved on a sibling `refine/issue-N-...` branch, the implement phase must itself copy `docs/superpowers/specs/*.md` and `docs/superpowers/plans/*.md` onto the `feat/issue-N-...` branch and commit them — they do not transfer automatically, and a plan step that assumes the spec "is already committed" (e.g. for a doc-pinning regression test) will be testing a file that does not yet exist on the feat branch. Mirrors the #41 precedent (commits `52ce1a6`, `621155e`); a later archive step (out of scope for implement) renames them into `docs/archive/`. <!-- issue:#42 date:2026-07-10 expires:2027-01-10 source:implement -->

# Conformance's NO_SPEC Decision: Replace Unset-vs-Empty Variable Testing with an Explicit On-Disk Artifact

**Issue:** #423

---

## Overview / Problem Statement

Every `commands/*.md` phase file binds "shell-looking" variables (`$SPEC_FILE`,
`$ISSUE_NUM`, `$PLAN_FILE`, ...) by prose instruction, not by a real mechanism: each
fenced ` ```bash ` block in these files is executed by the orchestrating LLM agent as a
**separately invoked Bash tool call**, and shell variable bindings do not persist across
those calls. The corpus works today only because the orchestrating agent manually
re-inlines the real value into each new bash snippet it composes — a behavioral
convention, not something the shell or the harness enforces.

Most instances of this pattern fail loudly when the convention is broken (e.g. an empty
`--set X=@""` makes `render-prompt` exit non-zero, aborting the run visibly — see the
`RUBRIC_CONTENT` hard-stop near-miss caught during #400's architect review, now fixed in
all three of `dark-factory-plan.md`, `dark-factory-conformance.md` and
`dark-factory-code-review.md` by re-deriving a real `RUBRIC_FILE` path in the consuming
block instead of `printf`-ing a prose-only variable).

This ticket fixes the one instance identified so far that fails **silently, in the wrong
direction**: `commands/dark-factory-conformance.md`'s NO_SPEC decision.

- **Phase 2 (LOCATE SPEC)**, across steps 2a/2b/2c (lines 85-129), tries three fallback
  lookups to find the approved spec and bind `$SPEC_FILE`. If none match, **step 2d**
  (lines 131-141) sets `SPEC_FILE=""` and documents (in prose only) `NO_SPEC=true`.
- **Step 3a** (lines 259-276), in a separately-invoked Bash block, branches on
  `if [ -n "$SPEC_FILE" ]`. This test cannot distinguish "Phase 2 deliberately resolved
  no spec" from "this fresh Bash call never re-bound `$SPEC_FILE` at all" (e.g. the
  orchestrating agent forgot to re-inline it, or enough turns/subagent dispatches sit
  between Phase 2 and Step 3a that the value was lost) — both look identical to `-n`:
  empty. If a real spec WAS found in Phase 2 but `$SPEC_FILE` is merely unbound in Step
  3a's own shell, the run silently reviews the implementation against the **issue body**
  instead of the **approved spec** — a merge-gating reviewer judging the wrong document,
  and the run reports success with nothing in the log flagging it.
- The same ambiguity recurs downstream: **Phase 3 step 7**'s verdict routing (line 351,
  "If `NO_SPEC=true` OR `BLOCK_ON_MATERIAL=false` → treat as advisory") and **Phase 4**'s
  PASS attestation write (lines 603-604) —
  `printf "...NO_SPEC: %s\n..." ... "${NO_SPEC:-false}" ...` — both read `$NO_SPEC` as a
  literal bash value in their own (likely separate) Bash invocation. `${NO_SPEC:-false}`
  silently defaults to `false` whenever `$NO_SPEC` is unset in *that* call, which is the
  norm since Phase 2d set it in an earlier invocation. So even when the run correctly
  takes the NO_SPEC path, the persisted audit artifact (`$ARTIFACTS_DIR/conformance.md`)
  can misreport `NO_SPEC: false` — directly undermining the requirement that the
  no-spec decision be "explicit, logged."
- The diff-ranking call in **Step 3.0.2** (line 215),
  `${SPEC_FILE:+--spec-file "$SPEC_FILE"}`, reads the same possibly-stale `$SPEC_FILE` in
  its own fenced block (which already sources `token-opt-caps.env` two lines earlier, at
  line 209, confirming it is a separate invocation boundary from Phase 2). `diff_rank.py`
  uses `--spec-file` to boost spec-named files' ranking so they are not chunked or
  truncated out of the diff the conformance reviewer sees (`_extract_spec_names`). A
  silently-dropped `$SPEC_FILE` here degrades the diff the merge-gating reviewer actually
  reads, even though this call site is nominally advisory-only.

`commands/dark-factory-code-review.md`'s own `$SPEC_FILE` resolution (step 10) is **not**
in scope: that file explicitly documents its spec-file signal as advisory-only (it only
promotes a diff out of the `low` risk tier) and states "no `NO_SPEC` escalation applies
here" — so an unset-vs-empty ambiguity there has no behavioral consequence either way.

## Requirements

1. **Phase 2 writes an explicit, single on-disk artifact recording the spec-resolution
   outcome**, in the same Bash invocation that makes the determination (2a/2b/2c finding
   a match, or 2d's exhausted-fallback case) — not left to a later, separate call to
   re-derive or assume.
   - File: `$ARTIFACTS_DIR/spec_resolution.env`, a sourceable shell-variable file
     following this file's own existing `token-opt-caps.env` idiom (sourced at line 209
     via `[ -f ... ] && . ... || true`) and the corpus-wide precedent of
     `scripts/load_memory_context.sh` persisting a resolved value
     (`$ARTIFACTS_DIR/memory-context.md`) for later, separately-invoked Bash calls to
     read deterministically.
   - Contents, written unconditionally on every Phase 2 run (both outcomes):
     ```bash
     SPEC_FILE="<resolved path, or empty string if none found>"
     NO_SPEC=<true|false>
     ```
   - `NO_SPEC` is always written explicitly as the literal string `true` or `false` — it
     is never inferred downstream from whether `SPEC_FILE` happens to be empty.
   - Phase 2d additionally logs the decision in prose/stdout (e.g. "No spec found after
     2a/2b/2c — NO_SPEC=true, writing $ARTIFACTS_DIR/spec_resolution.env"), so the
     decision is visible in the run transcript, not just on disk.
2. **A single artifact, not a sentinel file.** Rejected the `$ARTIFACTS_DIR/no_spec`
   presence/absence sentinel named as an option in the issue body: consumers need the
   resolved *path* (Step 3a's `SPEC_CONTENT_PATH`, Step 3.0.2's `--spec-file` argument),
   not just a boolean, so a presence-only sentinel would still need a second artifact for
   the path — and a sentinel's own absence is ambiguous between "a spec was found" and
   "Phase 2 never ran," reintroducing the same unset-vs-"confirmed-empty" ambiguity this
   ticket exists to remove.
3. **Decision consumers read the artifact and hard-stop loudly if it is missing** — they
   must NOT use the fail-open `[ -f ... ] && . ... || true` form, because for these three
   sites a missing artifact means "Phase 2 never ran for this cycle," which must never be
   silently treated as NO_SPEC:
   - **Step 3a** (lines 259-276): replace the `if [ -n "$SPEC_FILE" ]` test with sourcing
     `spec_resolution.env` and branching on its explicit `$NO_SPEC` value. If the file
     does not exist, hard-stop (loud error, non-zero exit from that Bash call) rather
     than falling back to issue-body review.
   - **Phase 3 step 7** (line 351): the verdict-routing prose must name
     `$ARTIFACTS_DIR/spec_resolution.env`'s `NO_SPEC` value as the thing being tested,
     not a bare recalled `$NO_SPEC`.
   - **Phase 4** (lines 603-604): source `spec_resolution.env` in that Bash block and
     derive the `NO_SPEC:` field of the attestation printf from its value, not from
     `${NO_SPEC:-false}`. If the artifact is missing at this point (should not happen if
     Phase 2 ran), that is itself a hard-stop condition for this field, not a silent
     `false`.
4. **The Step 3.0.2 diff-ranking call (line 215) also sources the artifact**, extending
   this ticket's scope narrowly to that one call site because it reuses the exact same
   mechanism Requirement 1 introduces (no new design) and because the silent-drop failure
   there has a real effect on what the merge-gating reviewer sees (per Overview). Unlike
   the Requirement 3 sites, this remains **fail-open**: add a source of
   `spec_resolution.env` alongside the existing `token-opt-caps.env` source at line 209,
   using the same `[ -f ... ] && . ... || true` form (diff ranking as a whole is already
   fail-open on error — see the surrounding `|| echo "diff_rank: ranking failed..."`
   fallback). Leave `${SPEC_FILE:+--spec-file "$SPEC_FILE"}` itself unchanged; only its
   source of `$SPEC_FILE` changes.
5. **No change to `scripts/gate_lib.sh`.** That file's own header comment scopes it to
   three shared primitives (`route_memory_file`, `write_memory_entry`, `emit_verdict`)
   used by both `dark-factory-conformance.md` and `dark-factory-code-review.md`; NO_SPEC
   resolution is conformance-specific logic and must not be added there. Either inline
   the read/write logic directly in `commands/dark-factory-conformance.md`'s existing
   bash blocks, or add a small conformance-specific helper script under `scripts/` (e.g.
   alongside `push_gate_check.sh`) if the plan phase finds the inline form unwieldy — the
   plan phase decides which, scoped to this file only.
6. **Test coverage**, following this repo's existing convention for `commands/*.md`
   prose/bash files — static content assertions against the `.md` text (see
   `tests/test_conformance_command_rubric_fallback.py`,
   `tests/test_conformance_command_artifact_guard.py`), since these files are
   instructions for an LLM agent, not directly executable scripts:
   - Assert Phase 2 (both the 2a/2b/2c match path and 2d's exhausted-fallback path)
     writes `spec_resolution.env` with both `SPEC_FILE=` and an explicit `NO_SPEC=`
     value, in the same block as the resolution.
   - Assert Step 3a sources `spec_resolution.env` (not a bare `[ -n "$SPEC_FILE" ]` test
     against an unsourced variable) and hard-stops if the file is missing.
   - Assert Phase 4's attestation-writing block sources `spec_resolution.env` before its
     `NO_SPEC:` printf field, and that the literal `${NO_SPEC:-false}` pattern is gone
     from that block.
   - Assert the Step 3.0.2 diff-ranking block sources `spec_resolution.env` (fail-open
     form) before its `diff_rank.py` invocation.
   - A missing-artifact case (Step 3a, Phase 4) must assert the command text says to
     stop/error explicitly, not silently default — this is the test required by the
     issue's acceptance criteria ("a test asserts that a missing spec path produces an
     explicit, logged 'no spec' decision rather than an implicit fallback").

## Architecture / Approach

Replace the implicit, per-call "re-inline the right value or the test silently defaults
wrong" convention with a durable, read-anywhere artifact for exactly this one decision —
mirroring the mechanism `scripts/load_memory_context.sh` and this same file's own
`token-opt-caps.env` already use elsewhere in the corpus, rather than inventing a new
pattern.

```
Phase 2 (2a/2b/2c/2d, one Bash call per attempt, same overall step)
  resolves SPEC_FILE (path or "")
  determines NO_SPEC (true/false) explicitly
       │
       ▼
  writes $ARTIFACTS_DIR/spec_resolution.env:
    SPEC_FILE="<path-or-empty>"
    NO_SPEC=<true|false>
       │
       ├──► Step 3.0.2 (diff_rank.py --spec-file): sources it, fail-open
       ├──► Step 3a (SPEC_CONTENT_PATH decision): sources it, hard-stop if missing
       ├──► Phase 3 step 7 (verdict routing: advisory vs reconcile): reads its NO_SPEC
       └──► Phase 4 (attestation printf NO_SPEC: field): sources it, hard-stop if missing
```

Every consumer of the spec-resolution outcome now reads the same on-disk value instead of
five independent re-derivations (today's `[ -n "$SPEC_FILE" ]`, `${SPEC_FILE:+...}`,
prose-recalled `$NO_SPEC`, and `${NO_SPEC:-false}`) that can silently disagree with each
other and with Phase 2's actual determination.

## Alternatives Considered

- **Sentinel file (`$ARTIFACTS_DIR/no_spec`, presence/absence only).** Named as an option
  in the issue body. Rejected (Requirement 2): consumers need the resolved path, not just
  a flag, and a sentinel's own absence is ambiguous between "a spec was found" and "Phase
  2 never ran" — the same class of ambiguity this ticket removes, just inverted.
- **Bash `:?` hard-require idiom** (`: "${SPEC_FILE:?...}"`), already used elsewhere in
  `dark-factory-plan.md`'s draft-detection step. Rejected for this branch specifically:
  `:?` cannot distinguish "deliberately empty" from "unset," and in conformance.md's
  NO_SPEC branch an empty `$SPEC_FILE` is itself a valid, deliberate outcome (no spec
  exists) that must not hard-error — the opposite problem from where `:?` is used in
  plan.md (where an empty/unset `SPEC_FILE` is always wrong).
- **Corpus-wide conversion of every prose-bound variable in every `commands/*.md` file
  to an artifact file (issue's Option 3).** Explicitly rejected for this ticket per the
  issue author's own framing ("needs its own epic/spec... should not be started here")
  and CLAUDE.md's scope-discipline convention. This spec fixes the one identified
  silent-failure branch and, narrowly, its direct downstream consumers within the same
  file; it does not touch `$ISSUE_NUM`, `$ARTIFACTS_DIR`, `$PLAN_FILE`, or any variable in
  any other command file.
- **Leave Step 3.0.2's diff-ranking `--spec-file` consumption untouched (treat it as a
  separate ticket)**, since it is nominally advisory-only like code-review.md's own
  `$SPEC_FILE` use. Rejected: it is one extra `source` line reusing the exact mechanism
  this ticket already introduces (no new design, no new file), and a silently-dropped
  spec-file argument measurably degrades the diff the merge-gating reviewer reads via
  `diff_rank.py`'s `_extract_spec_names` ranking boost — cheaper to fix inline here than
  to file and track a follow-up for a one-line change.
- **Do nothing, document the convention (issue's Option 1).** Rejected: cannot satisfy
  either acceptance criterion (removing the unset-vs-empty dependency; a test asserting
  an explicit, logged decision).

## Open Questions (non-blocking)

- Whether the plan phase inlines the artifact read/write logic directly in
  `commands/dark-factory-conformance.md`'s bash blocks or extracts a small
  conformance-specific helper script is left to the plan phase (Requirement 5); either
  satisfies this spec's requirements.
- Exact variable naming inside `spec_resolution.env` (`SPEC_FILE`/`NO_SPEC` as proposed)
  is a reasonable default matching the names already used throughout
  `dark-factory-conformance.md`, not dictated by the issue — the plan phase may adjust if
  it finds a naming collision risk, as long as the file records both the resolved path
  and an explicit boolean.

## Assumptions (flagged)

- `commands/dark-factory-code-review.md`'s own `$SPEC_FILE` resolution is out of scope,
  per that file's own explicit "advisory-only... no `NO_SPEC` escalation applies here"
  documentation (step 10) — confirmed by reading the file directly, not inferred.
- Line numbers cited above (e.g. "lines 259-276", "line 604") are taken from
  `commands/dark-factory-conformance.md` on this branch as of this refine pass; the plan
  phase should re-confirm them against whatever commit it actually branches from, since
  drift is possible between refine and plan.
- This ticket does not change `conformance.max_reconcile_cycles`,
  `conformance.block_on_material`, `conformance.scope_enforcement`,
  `conformance.excise_out_of_scope`, or any other `config.yaml`/`gate_*` knob — only how
  the already-existing `NO_SPEC`/`SPEC_FILE` decision is computed, persisted, and read.
  No change to `gate_*`, breaker, budgets, `deploy/**`, or `.claude/skills/**`.

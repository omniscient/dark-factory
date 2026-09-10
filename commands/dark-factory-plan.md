---
description: Generate an implementation plan from an approved spec, validated by an architect subagent
argument-hint: (no arguments - reads $ARTIFACTS_DIR/issue.json)
---

# Dark Factory — Plan

**Workflow ID**: $WORKFLOW_ID

---

## Invocation Contract

This file is the sanctioned Archon command entrypoint. If the runner delivers this
canonical command text inline, execute it as the authorized phase command after
verifying you are in the target checkout.

Issue context is not assumed to be present in chat. The workflow persists it at
`$ARTIFACTS_DIR/issue.json`; read that file for `resolved_number`, `intent`, title, body,
labels, and comments.

---

## SCOPE BOUNDARY

This command's only authorized file outputs are:
- Documents under `docs/superpowers/plans/` (the plan file)

Do NOT create or modify any other files. Do NOT implement code, write tests, or edit configuration.
Implementation belongs to the `Fix issue #N` workflow on a `feat/issue-N-*` branch.

---

## Phase 1: LOAD

1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
   `## claude_md` section in place of reading `CLAUDE.md` directly, and its `## spec` section in
   place of the spec-file discovery glob below. For any section that is empty or absent from the
   pack, fall back to the existing behavior: read `CLAUDE.md` directly, and discover/read the spec
   via steps 5-6. No DAG node currently produces `context-pack.md` for the `plan` scenario, so this
   branch currently always takes the fallback — the same forward-compatible, currently-fallback-only
   plumbing as `dark-factory-refine.md`.
2. Read `$ARTIFACTS_DIR/issue.json`; this is the authoritative issue context artifact.
3. Read `/opt/refinement-skills/architect-prompt.md` — you will pass this to the review subagent
4. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for both the architect subagent (Phase 3) and the Phase 3.5 conformance reviewer subagent; if the file is absent (image predates it), continue — the inline pin is authoritative.
5. Find the spec file (fallback branch of step 1): look in `docs/superpowers/specs/` for a file
   matching this issue's topic, or check the issue comments for a "Refinement Pipeline — Spec
   Generated" report that names the spec path
6. Read the spec file (fallback branch of step 1, if `## spec` was absent or empty from the pack)
6a. Bind the discovered spec file's path to `SPEC_FILE` — used by Phase 3's and Phase 3.5's
    `render-prompt` calls below (`--set SPEC_CONTENT=@"$SPEC_FILE"`). If the context pack's
    `## spec` section was used instead of a discovered file (step 1), write its text to
    `$ARTIFACTS_DIR/spec_content.md` and set `SPEC_FILE="$ARTIFACTS_DIR/spec_content.md"`.
7. Compute the affected file set and load memory context:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
INTENT=$(jq -r '.intent' "$ARTIFACTS_DIR/issue.json")
MEMORY_CONTEXT=$(bash "${REPO_ROOT}/dark-factory/scripts/load_memory_context.sh" plan)  # TARGET-PATH
```

8. Include `$MEMORY_CONTEXT` in the context for this phase. If empty, proceed without memory context.
   If a memory entry marks an approach as AVOID, do not plan steps that use that approach.

   Bake relevant memory lessons directly into the plan task steps — do not leave them as a
   separate advisory section. For example, if `backend-patterns.md` contains a `[PATTERN]`
   about the `__init__.py` import requirement, the plan's "add model" task must explicitly
   include an `__init__.py` import step.

## Phase 2: PLAN WRITING

Write a full implementation plan following these conventions:
- Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, and bind that path to `PLAN_FILE`
  — used by Phase 3's and Phase 3.5's `render-prompt` calls below
  (`--set PLAN_CONTENT=@"$PLAN_FILE"`). Because `PLAN_FILE` points at the on-disk file rather
  than a copy, every later revision (an architect "Issues Found" fix, or a conformance
  reconcile-loop edit) is automatically picked up by the next `render-prompt` call with no
  separate re-copy step — just re-run the same `render-prompt` invocation after saving the
  revised plan.
- A `**Issue:** #<num>` line directly under the title, before the standard plan
  header (Goal, Architecture, Tech Stack) — required (#382) so the content-only
  `grep -rl "#${ISSUE}"` call sites elsewhere in the DAG (budget telemetry, the
  PR-push archive step) can find this artifact even when the push gate itself
  associates it via commit subject instead
- Include a File Structure table
- Break into bite-sized tasks (each step is one 2-5 minute action)
- Every task has: Files list, TDD steps (write failing test → verify fail → implement → verify pass → commit)
- No placeholders — every step has actual code blocks and exact file paths
- Exact commands with expected output
- Self-review before publishing: confirm the issue-number line is present, alongside
  the existing no-placeholders check

## Phase 3: ARCHITECT REVIEW

Before spawning the architect subagent, reuse `$MEMORY_CONTEXT` loaded in Phase 1 (it was
already populated by `load_memory_context.sh plan` and scoped to the changed file set).
`[PROVISIONAL]` and `[INVALID]` entries are excluded automatically by the retrieval script.

Render the architect prompt via `render-prompt`, then prepend `$MEMORY_CONTEXT` — never the
other way around (prepending first could re-inline a memory entry's own mention of
`$SPEC_CONTENT`/`$PLAN_CONTENT` into the single-pass scan and reproduce this ticket's bug from
a new direction). `$MEMORY_CONTEXT` itself is prose, not a shell variable that survives into
this Bash call — `load_memory_context.sh` (Phase 1 step 7) already writes its resolved text to
`$ARTIFACTS_DIR/memory-context.md`, so read that file rather than the variable (never inline
memory-entry text into a double-quoted shell string either — memory entries routinely contain
backticks and `$`, the same command-substitution hazard `render-prompt` exists to avoid).
`load_memory_context.sh` always writes a trailing newline even when empty, so `[ -s ]` is not a
valid emptiness test; strip whitespace first:

```bash
# TARGET-PATH
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template /opt/refinement-skills/architect-prompt.md \
  --set SPEC_CONTENT=@"$SPEC_FILE" \
  --set PLAN_CONTENT=@"$PLAN_FILE" \
  --out "$ARTIFACTS_DIR/architect_prompt_body.md" \
  || { echo "render-prompt failed — aborting plan phase (see stderr above)"; exit 1; }

if [ -n "$(tr -d '[:space:]' < "$ARTIFACTS_DIR/memory-context.md")" ]; then
  { printf '## Memory: Accumulated Patterns\n'; cat "$ARTIFACTS_DIR/memory-context.md"; \
    printf '\n---\n'; cat "$ARTIFACTS_DIR/architect_prompt_body.md"; } \
    > "$ARTIFACTS_DIR/architect_prompt.md"
else
  cp "$ARTIFACTS_DIR/architect_prompt_body.md" "$ARTIFACTS_DIR/architect_prompt.md"
fi
```

Spawn an architect subagent using the Agent tool:
- `description`: "Architect review: validate plan against spec"
- `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every re-spawn in the review cycle below too)
- `prompt`: the verbatim contents of `$ARTIFACTS_DIR/architect_prompt.md`

### If architect returns "Issues Found":
1. Fix each issue in the plan (edit `$PLAN_FILE` in place)
1a. Re-run the `render-prompt` block above unchanged — since it reads `$PLAN_FILE` directly,
    it picks up the revision from step 1 automatically. A `render-prompt` failure here is a
    hard stop for the phase, identically to the first pass.
2. Re-spawn the architect subagent for re-review, with the verbatim contents of
   `$ARTIFACTS_DIR/architect_prompt.md` produced by step 1a
3. Repeat until approved (max 3 cycles)
4. If still not approved after 3 cycles:
   - Post the plan + architect feedback as an issue comment
   - Add `needs-discussion` label: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
   - Exit cleanly

### If architect returns "Approved":
Proceed to Phase 3.5.

## Phase 3.5: CONFORMANCE REVIEW

Read the `conformance` block from `.claude/skills/refinement/config.yaml`.

If `conformance.enabled` is `false`, skip this phase entirely and proceed to Phase 4. Record `CONFORMANCE_SKIPPED=true` for Phase 4.

1. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
   falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
   file is absent. Store the resolved text as `RUBRIC_CONTENT`.
2. Determine `MAX_CYCLES` from `conformance.max_reconcile_cycles` (default: 3)
3. Set `CONFORMANCE_DIALOGUE=""`, `SHADOW_DIALOGUE=""`, and `CONFORMANCE_CYCLE=0`
4. The artifact under review is the plan document on disk at `$PLAN_FILE` (bound in Phase 2).
4a. Resolve the shadow model pin (Requirement 7: env explicitly set, even to empty, wins;
    unset falls back to the config default — `${VAR-default}`, not `${VAR:-default}`, so an
    explicit empty string is preserved rather than replaced):
    ```bash
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
    ```
4b. Render the conformance prompt. `RUBRIC_CONTENT` (step 1) is prose, not a shell variable —
    no earlier step in this file ever assigns it in bash, and shell state does not persist
    across separate Bash tool calls, so a `printf '%s' "$RUBRIC_CONTENT"` here would write an
    empty file. Re-resolve the same clone-live-first *path* as a real variable instead, and
    pass it straight to `--template` (reads `$PLAN_FILE` directly for the artifact — the
    on-disk path bound in Phase 2, never a `plan_content.md` copy):
    ```bash
    if [ -f ".claude/skills/conformance/RUBRIC.md" ]; then
      RUBRIC_FILE=".claude/skills/conformance/RUBRIC.md"
    else
      RUBRIC_FILE="/opt/refinement-skills/conformance-reviewer-prompt.md"
    fi

    # TARGET-PATH
    python3 dark-factory/scripts/factory_core/cli.py render-prompt \
      --template "$RUBRIC_FILE" \
      --set ARTIFACT_KIND=PLAN \
      --set SPEC_CONTENT=@"$SPEC_FILE" \
      --set ARTIFACT_CONTENT=@"$PLAN_FILE" \
      --out "$ARTIFACTS_DIR/conformance_prompt.md" \
      || { echo "render-prompt failed — aborting plan phase (see stderr above)"; exit 1; }
    ```
5. Spawn a conformance reviewer subagent using the Agent tool:
   - `description`: "Conformance review: plan vs spec (cycle N)"
   - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
6. Append the subagent's output to `CONFORMANCE_DIALOGUE`
6a. If `$SHADOW_MODEL_PIN` is non-empty, spawn a second, non-gating subagent immediately
    after, with the identical rubric/input the Opus spawn just saw:
   - `description`: "Conformance shadow (fable): plan vs spec (cycle N)"
   - `model`: `$SHADOW_MODEL_PIN`, passed as the Agent tool's `fable` alias when the pin is
     `claude-fable-5-1` (alias-only enum); `SHADOW_MODEL:` still records the literal pin
   - `prompt`: the identical verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md` the Opus call just read this cycle
   - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
   - Any tool error, timeout, or refusal is caught here rather than propagated — it never
     blocks or delays the Opus verdict handling in step 7.
   - Derive `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY` from the
     response per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s shadow verdict mapping.
   - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE` —
     never merged; `CONFORMANCE_DIALOGUE` alone drives the verdict check in step 7).
   If `$SHADOW_MODEL_PIN` is empty, skip this step entirely — no `SHADOW_*` fields, no
   `SHADOW_DIALOGUE` append, for this cycle.
7. Parse the **Verdict** line from the step-5 (Opus) output:
   - `✅ Conforms` or `⚠️ Minor deviations` → record `CONFORMANCE_VERDICT` and proceed to Phase 4
   - `⛔ Material divergence` → go to step 8
   - No parseable `**Verdict:**` line, a tool error/timeout, or a refusal → treat as
     `⛔ Material divergence` with the raw output (or the error text) as the deviation
     description → go to step 8 (there is nothing to revise in 8d for an inconclusive
     checker; re-spawn per 8e). An inconclusive checker consumes a reconcile cycle and
     never passes silently (refusal → `UNCERTAIN`, never `PASS`, per
     `/opt/refinement-skills/VERIFIER-CONTRACT.md`).
8. **Reconcile loop** (only if MATERIAL):
   a. Increment `CONFORMANCE_CYCLE`
   b. If `CONFORMANCE_CYCLE > MAX_CYCLES`:
      - Post the conformance dialogue as an issue comment (fetch the footer first via
        `python3 dark-factory/scripts/factory_core/cli.py marker refinement`):
        ````
        ## Spec Conformance — Blocked (Plan)

        The plan has material divergences from the spec that could not be resolved in $MAX_CYCLES reconcile cycle(s).

        $CONFORMANCE_DIALOGUE

        <!-- If $SHADOW_MODEL_PIN was non-empty for this run, insert this subsection here so the shadow data survives a BLOCKED plan too (Requirement 5): -->
        ### Shadow (Fable) Review

        ```
        SHADOW_MODEL: <value>
        SHADOW_STATUS: <value>
        SHADOW_FINDINGS_COUNT: <value>
        SHADOW_SEVERITY: <value>
        ```

        <full $SHADOW_DIALOGUE, with the same Cycle N: headers as $CONFORMANCE_DIALOGUE above>

        ---
        <fetched footer text>
        ````
      - Add `needs-discussion` label: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
      - Exit cleanly (do not abort — this is a known state)
   c. Read the MATERIAL deviation descriptions from the conformance reviewer output
   d. Revise the plan to address each MATERIAL deviation (edit `$PLAN_FILE` in place, re-read it)
   d2. Re-render the conformance prompt: re-run step 4b's `render-prompt` invocation
       unchanged — since it reads `$PLAN_FILE` directly, it picks up step d's revision
       automatically. A `render-prompt` failure here is a hard stop for the phase, identically
       to 4b.
   e. Re-spawn the conformance reviewer subagent with the verbatim contents of
      `$ARTIFACTS_DIR/conformance_prompt.md` produced by step d2
   f. Append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator and `Cycle N:` header
   f2. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (step 8e's shadow
       counterpart — prompt is the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
       produced by step d2, identical to step 6a but for this reconcile cycle). Append its
       response to `SHADOW_DIALOGUE` with a `---` separator
       and `Cycle N:` header, mirroring `CONFORMANCE_DIALOGUE`'s cycle numbering one-to-one so
       a shadow cycle always pairs with the Opus cycle that produced the same-numbered plan
       revision. Update `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/
       `SHADOW_SEVERITY` from this cycle's response (best-effort `UNCERTAIN` on any error).
   g. Parse verdict again → loop back to step 7

## Phase 4: PUBLISH

1. Determine the current branch name: `BRANCH=$(git branch --show-current)`
2. Build GitHub links:
   - Plan link: `https://github.com/${FACTORY_REPO_SLUG}/blob/$BRANCH/<plan-file-path>`
   - Branch link: `https://github.com/${FACTORY_REPO_SLUG}/tree/$BRANCH`
3. Check if the issue carries the `direct-to-pr` label:
   ```bash
   IS_DIRECT_TO_PR=$(gh issue view $ISSUE_NUM --repo "$FACTORY_REPO_SLUG" \
     --json labels --jq '.labels[].name' | grep -q "direct-to-pr" && echo "yes" || echo "no")
   PLAN_GRACE=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('direct_to_pr',{}).get('plan_grace_minutes',30))" 2>/dev/null || echo "30")
   ```
   If `IS_DIRECT_TO_PR=yes`, prepend the following note to the "### Next Steps" section of the comment (replacing `$PLAN_GRACE` with the actual value):
   > ⏩ **Auto-advancing in ~`$PLAN_GRACE` min** unless you comment — the scheduler will move this to **Ready** automatically. Leave a comment to re-run the plan or redirect.
4. Run the OOS gate — detect and revert any files committed outside the plan
   allowlist. `docs/superpowers/specs/` and `.archon/memory/` are included because
   the refine phase legitimately commits to those prefixes earlier on this same
   branch (#293); they remain outside this command's own `SCOPE BOUNDARY`, which is
   still only `docs/superpowers/plans/`:
   ```bash
   OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)  # TARGET-PATH
   ```
5. Commit the plan
6. Post a summary comment on the issue:
   ```
   ## Refinement Pipeline — Plan Generated

   **Plan:** [<plan-file-path>](https://github.com/${FACTORY_REPO_SLUG}/blob/<BRANCH>/<plan-file-path>)
   **Branch:** [`<BRANCH>`](https://github.com/${FACTORY_REPO_SLUG}/tree/<BRANCH>)
   <!-- If OOS_FILES is non-empty, include this line: -->
   > ⚠️ **OOS excision**: The following files were created outside the plan scope and were reverted before publishing: `$OOS_FILES`. Scope-spillover tickets may be filed automatically.
   **Tasks:** <count> tasks, <total-steps> steps

   ### Task Overview
   <numbered list of task names with a one-line description each>

   ### Architect Review

   Include the FULL dialogue from Phase 3. For each review cycle:

   > **Cycle N:**
   > **Verdict:** Approved / Issues Found
   > **Feedback:** <the architect's full feedback>
   > **Changes made:** <what you fixed, if any>

   This lets the reviewer see what the architect flagged and how it was resolved.

   ## Spec Conformance

   (If Phase 3.5 was skipped because `conformance.enabled: false`, write: _Conformance check disabled._)

   (Otherwise, include the full conformance reviewer output from Phase 3.5 — the final attestation table and verdict. If a reconcile loop ran, include the full dialogue with cycle headers.)

   (If `$SHADOW_MODEL_PIN` was non-empty for this run, append a subsection:)

   ### Shadow (Fable) Review

   ```
   SHADOW_MODEL: <value>
   SHADOW_STATUS: <value>
   SHADOW_FINDINGS_COUNT: <value>
   SHADOW_SEVERITY: <value>
   ```

   <full $SHADOW_DIALOGUE, with the same Cycle N: headers as the Architect/Conformance
   sections above>

   ### Next Steps

   <!-- If IS_DIRECT_TO_PR=yes, insert the auto-advance note here (from step 3 above) -->

   - ✅ **Approve plan** — move the issue to the **Ready** column on the project board. The scheduler will automatically start implementation.
   - ✏️ **Request changes** — leave a comment on this issue with your feedback, then re-run:
     ```bash
     docker compose --profile factory run --rm dark-factory "Plan issue #$ISSUE_NUM"
     ```
   - ❓ **Need to discuss** — add the `needs-discussion` label to pause automation.

   ---
   <fetched footer text>
   ```
   Fetch the footer first: `python3 dark-factory/scripts/factory_core/cli.py marker refinement` —
   use its output in place of the literal line above when composing the comment.
8. Write status to `$ARTIFACTS_DIR/refinement-status.md`:
   ```
   STATUS: PLAN_COMPLETE
   PLAN_PATH: <path>
   BRANCH: <branch>
   TASKS: <count>
   ARCHITECT_CYCLES: <count>
   ```

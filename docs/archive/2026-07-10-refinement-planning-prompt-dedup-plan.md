# Plan: Refinement/Planning Prompt De-duplication and Playbook Clarification

**Issue:** omniscient/dark-factory#43 — Split refinement and planning prompts into concise phase skills
**Spec:** [docs/superpowers/specs/2026-07-10-refinement-planning-prompt-dedup-design.md](../specs/2026-07-10-refinement-planning-prompt-dedup-design.md)

## Goal

Collapse the refine-side prompt duplication identified by #41/#43: make `commands/dark-factory-refine.md`
(clone-live, Archon-dispatched) the sole canonical owner of the refine procedure, and shrink
`refinement-skills/orchestrator-prompt.md` (baked, `/opt/refinement-skills/`) to a thin persona stub.
Add the context-pack presence-check + fallback pattern to both `dark-factory-refine.md` and
`dark-factory-plan.md`'s Phase 1 LOAD (forward-compatible plumbing for #36's not-yet-wired
context-pack DAG node). Fix the "MarketHawk" product-identity hardcoding in the four
`refinement-skills/*` persona files that #43 touches. No physical directory move
(`refinement-skills/` → `.claude/skills/refinement/`) — that stays the deferred #42 §2
follow-up ticket.

## Architecture

This is a prompt/documentation ticket: no application (backend/frontend/database) code changes.
The "implementation" is markdown-content edits to Archon commands and baked persona prompts.
Per this repo's existing convention for doc-only tickets (see the archived #41/#42 plans), TDD is
adapted mechanically: each task adds a pytest content-assertion test (a `Path.read_text()` +
`assert` check, the same shape as the existing `tests/test_command_identity.py` and
`tests/test_conformance_prompt_formatter_rule.py`), confirms it fails against the current file,
then edits the file until the assertion passes.

`commands/dark-factory-refine.md` and `commands/dark-factory-plan.md` are each mirrored verbatim
at `.archon/commands/<same-name>.md` (both copies are checked into this repo; `entrypoint.sh` only
auto-populates `.archon/commands/` when it is absent from the clone, so keeping the two copies in
sync here is a manual edit-time responsibility). Every task below that edits a `commands/*.md` file
applies the identical edit to its `.archon/commands/` mirror in the same task and asserts the two
are byte-identical.

## Tech Stack

- Python 3 stdlib + `pytest` for content-assertion regression tests (matches
  `python -m pytest tests/ -v` from `CLAUDE.md`)
- `grep`/`diff` for manual verification during editing
- `gh` CLI for the tracking-issue confirmation task (Task 9)

## File Structure

| File | Change |
|---|---|
| `refinement-skills/architect-prompt.md` | MarketHawk product-identity wording only (lines 1, 3) |
| `refinement-skills/product-owner-prompt.md` | MarketHawk product-identity wording only (lines 1, 3) |
| `refinement-skills/conformance-reviewer-prompt.md` | MarketHawk product-identity wording only (lines 1, 3) |
| `refinement-skills/orchestrator-prompt.md` | Full replacement — thin persona stub (also removes its MarketHawk mention) |
| `refinement-skills/SKILL.md` | One-line Prompt Files description update for `orchestrator-prompt.md` |
| `commands/dark-factory-refine.md` | Phase 1 LOAD: context-pack presence-check + fallback; Phase 4: migrated "Focus questions on" bullet list |
| `.archon/commands/dark-factory-refine.md` | Mirror of the above |
| `commands/dark-factory-plan.md` | Phase 1 LOAD: context-pack presence-check + fallback (`claude_md`, `spec` sections) |
| `.archon/commands/dark-factory-plan.md` | Mirror of the above |
| `tests/test_no_markethawk_hardcoding.py` | New — regression tests for Task 1-3 |
| `tests/test_orchestrator_prompt_stub.py` | New — regression tests for Task 4-5 |
| `tests/test_refine_command_updates.py` | New — regression tests for Task 6-7 |
| `tests/test_plan_command_context_pack.py` | New — regression tests for Task 8 |

No other files are created or modified. `refinement-skills/code-review-reviewer-prompt.md` and
`commands/dark-factory-conformance.md`'s Phase 3.5 reconcile-loop content are explicitly untouched
(#44's scope).

---

## Task 1: Fix MarketHawk hardcoding — `architect-prompt.md`

**Files:**
- `tests/test_no_markethawk_hardcoding.py` (new)
- `refinement-skills/architect-prompt.md`

### Step 1 — Write the failing test

```bash
cat > tests/test_no_markethawk_hardcoding.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_architect_prompt_no_markethawk():
    text = (REPO_ROOT / "refinement-skills" / "architect-prompt.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "architect-prompt.md still hardcodes MarketHawk"
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v
```
Expected: `test_architect_prompt_no_markethawk` FAILS (`MarketHawk` is present at lines 1 and 3).

### Step 3 — Implement

Edit `refinement-skills/architect-prompt.md`:
- Line 1: `# Architect Reviewer — MarketHawk` → `# Architect Reviewer`
- Line 3: `You are an architect reviewing an implementation plan for the MarketHawk stock scanning platform.` → `You are an architect reviewing an implementation plan for the target codebase.`

No other line changes — the review checklist and output format are unaffected.

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add tests/test_no_markethawk_hardcoding.py refinement-skills/architect-prompt.md
git commit -m "fix(refinement-skills): remove MarketHawk hardcoding from architect-prompt.md"
```

---

## Task 2: Fix MarketHawk hardcoding — `product-owner-prompt.md`

**Files:**
- `tests/test_no_markethawk_hardcoding.py`
- `refinement-skills/product-owner-prompt.md`

### Step 1 — Append the failing test

```python
def test_product_owner_prompt_no_markethawk():
    text = (REPO_ROOT / "refinement-skills" / "product-owner-prompt.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "product-owner-prompt.md still hardcodes MarketHawk"
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v -k product_owner
```
Expected: FAILS.

### Step 3 — Implement

Edit `refinement-skills/product-owner-prompt.md`:
- Line 1: `# Product Owner — MarketHawk` → `# Product Owner`
- Line 3: `You are the product owner for MarketHawk, a full-stack stock scanning platform that identifies pre-market volume spikes and unusual trading patterns.` → `You are the product owner for the target codebase, representing its users' and stakeholders' interests as described by the issue, its comments, and the existing documentation.`

No other line changes.

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v
```
Expected: both tests PASSED.

### Step 5 — Commit

```bash
git add tests/test_no_markethawk_hardcoding.py refinement-skills/product-owner-prompt.md
git commit -m "fix(refinement-skills): remove MarketHawk hardcoding from product-owner-prompt.md"
```

---

## Task 3: Fix MarketHawk hardcoding — `conformance-reviewer-prompt.md`

**Files:**
- `tests/test_no_markethawk_hardcoding.py`
- `refinement-skills/conformance-reviewer-prompt.md`

### Step 1 — Append the failing test

```python
def test_conformance_reviewer_prompt_no_markethawk():
    text = (REPO_ROOT / "refinement-skills" / "conformance-reviewer-prompt.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "conformance-reviewer-prompt.md still hardcodes MarketHawk"
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v -k conformance_reviewer
```
Expected: FAILS.

### Step 3 — Implement

Edit `refinement-skills/conformance-reviewer-prompt.md`:
- Line 1: `# Conformance Reviewer — MarketHawk` → `# Conformance Reviewer`
- Line 3: `You are a conformance reviewer for the MarketHawk dark factory pipeline. Your job is to judge whether an artifact (an implementation plan or a code implementation) is **faithful to its approved spec**. You focus on intent, approach, scope, and constraints — not on mechanical correctness (file paths, test structure, line numbers). Those are the architect's domain.` → `You are a conformance reviewer for the dark factory pipeline. Your job is to judge whether an artifact (an implementation plan or a code implementation) is **faithful to its approved spec**. You focus on intent, approach, scope, and constraints — not on mechanical correctness (file paths, test structure, line numbers). Those are the architect's domain.`

Only the product-identity clause ("for the MarketHawk dark factory pipeline" → "for the dark
factory pipeline") changes — the rest of the sentence and the entire remainder of the file
(verdict tiers, output format, formatter/documentation exceptions) is untouched, since that
content is shared with `dark-factory-conformance.md` and out of scope for #43 (#44's Phase-3.5
logic is unaffected).

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_no_markethawk_hardcoding.py -v
```
Expected: all three tests PASSED.

### Step 5 — Commit

```bash
git add tests/test_no_markethawk_hardcoding.py refinement-skills/conformance-reviewer-prompt.md
git commit -m "fix(refinement-skills): remove MarketHawk hardcoding from conformance-reviewer-prompt.md"
```

---

## Task 4: Collapse `orchestrator-prompt.md` to a thin persona stub

**Files:**
- `tests/test_orchestrator_prompt_stub.py` (new)
- `refinement-skills/orchestrator-prompt.md`

### Step 1 — Write the failing test

```bash
cat > tests/test_orchestrator_prompt_stub.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT = REPO_ROOT / "refinement-skills" / "orchestrator-prompt.md"


def test_orchestrator_prompt_is_thin_stub():
    text = PROMPT.read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "stub must not hardcode a product identity"
    assert "dark-factory-refine" in text, "stub must point to the canonical command"
    assert "### Phase 6" not in text, "six-phase process narration must be removed"
    assert "$ISSUE_CONTEXT" not in text, "vestigial template placeholder must be removed"
    assert "$FEEDBACK" not in text, "vestigial template placeholder must be removed"
    assert len(text.strip()) > 0, "stub file must not be deleted (context_budget.py enumerates it)"
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_orchestrator_prompt_stub.py -v
```
Expected: FAILS (current file has all six phases, both placeholders, and MarketHawk).

### Step 3 — Implement

Replace the full content of `refinement-skills/orchestrator-prompt.md` with:

```markdown
# Refinement Orchestrator

You are the refinement orchestrator for Dark Factory's self-hosting and target-repo pipelines.
Your full process is defined by the `dark-factory-refine` phase command
(`.archon/commands/dark-factory-refine.md`) that is currently instructing you — this file exists
only to hold your persona identity and is read as a supporting reference, not a second procedure.

When spawning product-owner subagents to answer clarifying questions, follow the command's Phase 4
instructions exactly (question style, Agent tool invocation, model pin, UNCERTAIN: handling).
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_orchestrator_prompt_stub.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add tests/test_orchestrator_prompt_stub.py refinement-skills/orchestrator-prompt.md
git commit -m "refactor(refinement-skills): collapse orchestrator-prompt.md to a thin persona stub"
```

---

## Task 5: Update `SKILL.md`'s Prompt Files description for the stub

**Files:**
- `tests/test_orchestrator_prompt_stub.py`
- `refinement-skills/SKILL.md`

### Step 1 — Append the failing test

```python
def test_skill_md_describes_orchestrator_as_stub():
    skill = (REPO_ROOT / "refinement-skills" / "SKILL.md").read_text(encoding="utf-8")
    assert "dark-factory-refine.md" in skill
    assert "stub" in skill.lower()
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_orchestrator_prompt_stub.py -v -k skill_md
```
Expected: FAILS (current line reads "Instructions for the brainstorming orchestrator").

### Step 3 — Implement

In `refinement-skills/SKILL.md`, under `## Prompt Files`, change:
```
- `orchestrator-prompt.md` — Instructions for the brainstorming orchestrator
```
to:
```
- `orchestrator-prompt.md` — Persona stub for the brainstorming orchestrator — full process lives in `dark-factory-refine.md`
```

No other section of `SKILL.md` changes (kept concise per spec Requirement 6).

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_orchestrator_prompt_stub.py -v
```
Expected: both tests PASSED.

### Step 5 — Commit

```bash
git add tests/test_orchestrator_prompt_stub.py refinement-skills/SKILL.md
git commit -m "docs(refinement-skills): update SKILL.md prompt-files description for the stub"
```

---

## Task 6: `dark-factory-refine.md` Phase 1 LOAD — context-pack presence-check

**Files:**
- `tests/test_refine_command_updates.py` (new)
- `commands/dark-factory-refine.md`
- `.archon/commands/dark-factory-refine.md`

### Step 1 — Write the failing tests

```bash
cat > tests/test_refine_command_updates.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFINE_CMD = REPO_ROOT / "commands" / "dark-factory-refine.md"
REFINE_CMD_MIRROR = REPO_ROOT / ".archon" / "commands" / "dark-factory-refine.md"


def test_refine_has_context_pack_presence_check():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "context-pack.md" in text
    assert "## claude_md" in text
    assert "## architecture_md" in text


def test_refine_command_mirrors_are_identical():
    assert REFINE_CMD.read_text(encoding="utf-8") == REFINE_CMD_MIRROR.read_text(encoding="utf-8")
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_refine_command_updates.py -v -k context_pack
```
Expected: FAILS (no `context-pack.md` reference exists yet in `dark-factory-refine.md`).

### Step 3 — Implement

In `commands/dark-factory-refine.md`, replace Phase 1 LOAD's five existing steps — `1. Read
CLAUDE.md`, `2. Read ARCHITECTURE.md`, `3. The issue context has been fetched...`, `4. Read
orchestrator-prompt.md...`, `5. Read product-owner-prompt.md...` — with the following four-item
block (steps 1 and 2 collapse into the new step 1's presence-check + fallback):

```
1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
   `## claude_md` and `## architecture_md` sections and use them in place of reading the source
   files directly. For any section that is empty or absent from the pack, fall back to reading
   the corresponding source file directly (`CLAUDE.md`, `ARCHITECTURE.md`) at the repo root.
   No DAG node currently produces `context-pack.md` for the `refine` scenario, so this branch
   currently always takes the fallback — this is intentional, forward-compatible plumbing for
   when omniscient/dark-factory#36 wires in a context-pack DAG node, not a currently-exercised
   optimization.
2. The issue context has been fetched by the workflow. It is available in the conversation.
3. Read `/opt/refinement-skills/orchestrator-prompt.md` — a short persona stub; your full process
   instructions are Phases 1–6 below (this file), not a separate document.
4. Read `/opt/refinement-skills/product-owner-prompt.md` — you will pass this to subagents
```

Steps 5 (`config.yaml`) and 6 (memory context bash block) keep their existing content, renumbered
to 5 and 6. Apply the identical edit to `.archon/commands/dark-factory-refine.md`.

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_refine_command_updates.py -v -k "context_pack or mirrors"
```
Expected: both PASSED.

### Step 5 — Commit

```bash
git add tests/test_refine_command_updates.py commands/dark-factory-refine.md .archon/commands/dark-factory-refine.md
git commit -m "feat(dark-factory-refine): add context-pack presence-check to Phase 1 LOAD"
```

---

## Task 7: `dark-factory-refine.md` Phase 4 — migrate the focus-questions bullet list

**Files:**
- `tests/test_refine_command_updates.py`
- `commands/dark-factory-refine.md`
- `.archon/commands/dark-factory-refine.md`

### Step 1 — Append the failing tests

```python
def test_refine_has_focus_questions_migrated():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "Focus questions on" in text
    assert "integration points with existing code" in text.lower()


def test_refine_phase4_lead_in_is_not_circular():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "Follow the process in `orchestrator-prompt.md`" not in text, (
        "orchestrator-prompt.md is now a stub that points back at this command; "
        "the Phase 4 lead-in must not point to it as if it holds the process"
    )
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_refine_command_updates.py -v -k "focus_questions or lead_in"
```
Expected: `test_refine_has_focus_questions_migrated` FAILS; `test_refine_phase4_lead_in_is_not_circular`
also FAILS against the current file, which still reads `Follow the process in \`orchestrator-prompt.md\`:`
at line 72.

### Step 3 — Implement

In `commands/dark-factory-refine.md` Phase 4 BRAINSTORMING LOOP:

1. Replace the lead-in line
   ```
   Follow the process in `orchestrator-prompt.md`:
   ```
   with:
   ```
   Follow this process:
   ```
   (Task 4 shrinks `orchestrator-prompt.md` to a stub whose own text says "your full process is
   defined by the `dark-factory-refine` phase command" — leaving this line pointing at the stub
   would be circular and undercut the de-duplication this ticket is making.)
2. Insert after step 1 ("Formulate one clarifying question at a time"):
   ```
      Focus questions on: purpose and success criteria; scope boundaries (what's in, what's out);
      integration points with existing code; data model decisions; UI/UX requirements (if
      applicable); error handling and edge cases.
   ```

Apply both edits identically to `.archon/commands/dark-factory-refine.md`.

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_refine_command_updates.py -v
```
Expected: all five tests in this file PASSED.

### Step 5 — Commit

```bash
git add tests/test_refine_command_updates.py commands/dark-factory-refine.md .archon/commands/dark-factory-refine.md
git commit -m "feat(dark-factory-refine): migrate focus-questions guidance, drop circular stub pointer"
```

---

## Task 8: `dark-factory-plan.md` Phase 1 LOAD — context-pack presence-check

**Files:**
- `tests/test_plan_command_context_pack.py` (new)
- `commands/dark-factory-plan.md`
- `.archon/commands/dark-factory-plan.md`

### Step 1 — Write the failing tests

```bash
cat > tests/test_plan_command_context_pack.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"
PLAN_CMD_MIRROR = REPO_ROOT / ".archon" / "commands" / "dark-factory-plan.md"


def test_plan_has_context_pack_presence_check():
    text = PLAN_CMD.read_text(encoding="utf-8")
    assert "context-pack.md" in text
    assert "## claude_md" in text
    assert "## spec" in text


def test_plan_command_mirrors_are_identical():
    assert PLAN_CMD.read_text(encoding="utf-8") == PLAN_CMD_MIRROR.read_text(encoding="utf-8")
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_plan_command_context_pack.py -v
```
Expected: `test_plan_has_context_pack_presence_check` FAILS; the mirrors test PASSES (both copies
already identical) — that is fine, it stays green after the edit too since both copies receive the
same change.

### Step 3 — Implement

In `commands/dark-factory-plan.md`, replace Phase 1 LOAD steps 1-5 with:

```
1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
   `## claude_md` section in place of reading `CLAUDE.md` directly, and its `## spec` section in
   place of the spec-file discovery glob below. For any section that is empty or absent from the
   pack, fall back to the existing behavior: read `CLAUDE.md` directly, and discover/read the spec
   via steps 4-5. No DAG node currently produces `context-pack.md` for the `plan` scenario, so this
   branch currently always takes the fallback — the same forward-compatible, currently-fallback-only
   plumbing as `dark-factory-refine.md`.
2. The issue context has been fetched by the workflow. It is available in the conversation.
3. Read `/opt/refinement-skills/architect-prompt.md` — you will pass this to the review subagent
4. Find the spec file (fallback branch of step 1): look in `docs/superpowers/specs/` for a file
   matching this issue's topic, or check the issue comments for a "Refinement Pipeline — Spec
   Generated" report that names the spec path
5. Read the spec file (fallback branch of step 1, if `## spec` was absent or empty from the pack)
```

Steps 6 (memory context bash block) and 7 (memory-usage guidance) keep their existing content and
numbering. Apply the identical edit to `.archon/commands/dark-factory-plan.md`. No change to
Phase 2, Phase 3, Phase 3.5, or Phase 4.

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_plan_command_context_pack.py -v
```
Expected: both tests PASSED.

### Step 5 — Commit

```bash
git add tests/test_plan_command_context_pack.py commands/dark-factory-plan.md .archon/commands/dark-factory-plan.md
git commit -m "feat(dark-factory-plan): add context-pack presence-check to Phase 1 LOAD"
```

---

## Task 9: Confirm or file the deferred #42 §2 physical-consolidation tracking ticket

This is a housekeeping/issue-tracking action, not a code change — no test applies (nothing on disk
to assert against). Per the spec's Open Questions, #43 assumes a follow-up ticket for the
`refinement-skills/` → `.claude/skills/refinement/` physical move exists or gets filed, so it is
not silently dropped.

**Files:** none.

### Step 1 — Check whether the follow-up ticket already exists

```bash
gh issue list --repo omniscient/dark-factory --state all --search \
  "refinement-skills .claude/skills/refinement in:title,body" \
  --json number,title,state
```

### Step 2 — File it if absent

If the search returns no issue that clearly tracks the physical move designed in #42 §2, file one:

```bash
gh issue create --repo omniscient/dark-factory \
  --title "Move refinement-skills/ to .claude/skills/refinement/ (physical consolidation from #42 §2)" \
  --body "Follow-up to #42 §2 and #43. Execute the physical directory move designed in #42's \
approved policy spec: \`refinement-skills/\` -> \`.claude/skills/refinement/\` (+ \`templates/\` \
subdirectory), updating the ~10 hardcoded \`/opt/refinement-skills\` references across \
Dockerfile, entrypoint.sh, scheduler.sh, context_budget.py, context_pack.py, \
architecture_slice.py, memory_retrieve.py, and 3 test files. Deferred out of #43 (see that \
spec's Q1/A1) because it is a different risk class (baked-image-to-clone-live delivery \
mechanism vs. prompt content) and exceeds a size:M+size:M combined budget."
```

### Step 3 — Record the result

Note the ticket number (existing or newly filed) in this plan's Task Overview when publishing, so
the issue comment references it.

---

## Task 10: Full verification sweep

**Files:** none (verification only).

### Step 1 — Run the full test suite

```bash
python -m pytest tests/ -v
```
Expected: all tests PASSED, including the four new test files from Tasks 1-8.

### Step 2 — Confirm command/mirror parity for every touched Archon command

```bash
diff commands/dark-factory-refine.md .archon/commands/dark-factory-refine.md
diff commands/dark-factory-plan.md .archon/commands/dark-factory-plan.md
```
Expected: both `diff` invocations produce no output (exit code 0).

### Step 3 — Confirm untouched files remain untouched

```bash
git diff --stat origin/main HEAD -- refinement-skills/code-review-reviewer-prompt.md commands/dark-factory-conformance.md .archon/commands/dark-factory-conformance.md
```
Expected: no output — these files are not part of #43's diff (#44's scope).

### Step 4 — MarketHawk sweep across the full `refinement-skills/` directory

```bash
grep -rn "MarketHawk" refinement-skills/
```
Expected: no matches (all four touched files fixed; `code-review-reviewer-prompt.md` was never
supposed to carry this string in scope — if it does, that is pre-existing and out of #43's scope,
do not touch it here).

### Step 5 — Self-review checklist

- Placeholder scan: `grep -rn "TBD\|TODO" commands/dark-factory-refine.md commands/dark-factory-plan.md refinement-skills/` → expect no matches introduced by this change.
- Scope check: `git diff --stat origin/main HEAD` shows only the files listed in the File Structure table above (plus Task 9's tracking-issue side effect, which touches no files).
- Confirm `Refine issue #N` / `Plan issue #N` invocation strings are unchanged (no edits to command frontmatter or workflow DAG `command:` ids).

No commit for this task — it is a verification pass over commits already made in Tasks 1-9.

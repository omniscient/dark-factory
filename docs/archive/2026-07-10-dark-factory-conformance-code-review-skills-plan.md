# Plan: Dark Factory Conformance and Code-Review Skills

**Issue:** omniscient/dark-factory#44 — Create Dark Factory conformance and code-review skills backed by compact artifacts
**Spec:** [docs/superpowers/specs/2026-07-10-dark-factory-conformance-code-review-skills-design.md](../specs/2026-07-10-dark-factory-conformance-code-review-skills-design.md)

## Goal

Extract `refinement-skills/conformance-reviewer-prompt.md` and
`refinement-skills/code-review-reviewer-prompt.md` (baked into the Docker image at
`/opt/refinement-skills/`, requiring a full image rebuild to edit) into two dedicated Claude
Code Skills — `.claude/skills/conformance/` and `.claude/skills/code-review/` — each with a
concise `SKILL.md` and a `RUBRIC.md` carrying the reviewer rubric verbatim (plus the
"MarketHawk" product-identity fix where it still applies). Wire all three command call sites
that read these prompts (`commands/dark-factory-plan.md` Phase 3.5,
`commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`) to resolve the
rubric clone-live-first, baked-path fallback — mirroring the `config.yaml` precedent at
`entrypoint.sh:40`. Along the way, fix a live gap: `commands/dark-factory-code-review.md`
already reads a clone-live path that has never existed
(`.claude/skills/refinement/code-review-reviewer-prompt.md`), silently degrading Gate 3 to an
advisory no-op because `code_review.fail_open` defaults `true`. Update
`scripts/context_budget.py`'s skill-prompt probe in lockstep so the budget artifact measures
the same file the command actually injects.

## Architecture

This is a prompt/documentation ticket with one small Python refactor
(`scripts/context_budget.py`); no backend/frontend/database code changes. TDD is adapted per
this repo's existing convention for doc-only tickets (see the archived #43 plan): each task
that edits a markdown command/prompt file adds a pytest content-assertion test (`Path.read_text()`
+ `assert`), confirms it fails against the current file, then edits until it passes. The one
Python task (`context_budget.py`) uses ordinary unit tests against the module.

### Deviations from the spec (verified against current repo state + fresh memory)

The spec's Architecture §3/§4 describes two mirrored-file responsibilities that this plan
**does not** carry out, because verifying the actual repo state shows they are runtime-generated
copies, not checked-in files — carrying them out would either silently no-op or (for
`.archon/commands/`) attempt to commit a path `.git/info/exclude` already ignores:

1. **No edits to `.archon/commands/*.md`.** `git ls-files .archon/commands/` returns nothing —
   the directory is listed in `.git/info/exclude` and is populated by
   `entrypoint.sh:518-525`'s `_exclude_in_clone` only when absent from a fresh clone, copied from
   the Docker-baked `/opt/dark-factory/commands` (itself built from this repo's own
   `commands/*.md`). `commands/*.md` is the sole tracked source of truth. This matches the
   `[PATTERN]` memory entry written during #43's implementation (`.archon/memory/dark-factory-ops.md`,
   issue:#43): *"Do NOT `git add -f`/commit `.archon/commands/*.md` even if a plan calls it a
   'checked-in mirror'... Picking up a `commands/` edit into a *live* clone's `.archon/commands/`
   requires either deleting that directory... or a genuinely fresh clone."* Every task below
   edits `commands/*.md` only.
2. **No edits to `dark-factory/scripts/context_budget.py`.** `git ls-files dark-factory/scripts/`
   returns 0 files — it is the self-contained-fallback runtime copy `entrypoint.sh:509-513`
   copies from `/opt/dark-factory/scripts` only when `$CLONE_DIR/dark-factory/scripts` is absent
   (this session's copy exists on disk only because entrypoint.sh already ran it once; its
   content is byte-identical to `scripts/` today). `scripts/context_budget.py` is the tracked
   source of truth and the only copy this plan edits.

Both corrections were verified directly (`git ls-files`, `.git/info/exclude` contents,
`entrypoint.sh` read) before writing this plan, per the accumulated-memory instruction to bake
lessons into task steps rather than leave them as advisory notes.

A second, smaller correction: the spec's §2 says the MarketHawk mislabel needs fixing "two
occurrences per file" in **both** rubric source files. Reading the actual current
`refinement-skills/conformance-reviewer-prompt.md` shows it was already de-labeled by #43
(opens with *"You are a conformance reviewer for the dark factory pipeline."* — no MarketHawk
mention, confirmed by the passing `tests/test_no_markethawk_hardcoding.py` /
`tests/test_conformance_prompt_formatter_rule.py` today). Only
`refinement-skills/code-review-reviewer-prompt.md` still carries the mislabel (`# Code Reviewer
— MarketHawk` / *"You are a senior code reviewer for the MarketHawk dark factory pipeline."*).
Task 2 below fixes it in the new `RUBRIC.md` copy only (the spec's requirement 7 keeps
`refinement-skills/` itself untouched — it must stay available as the baked fallback source
until #43's Dockerfile-retirement follow-up lands). The delabel wording mirrors the exact
pattern #43 already established for the other three persona files — drop "— MarketHawk"
entirely rather than substitute a "— Dark Factory Pipeline" suffix, for consistency across all
migrated persona files.

## Tech Stack

- Python 3 stdlib + `pytest` for content-assertion regression tests and the
  `context_budget.py` unit tests (matches `python -m pytest tests/ -v` from `CLAUDE.md`)
- Claude Code Skills frontmatter (`name`, `description`, `allowed-tools`) per the merged #42
  policy

## File Structure

| File | Change |
|---|---|
| `.claude/skills/conformance/SKILL.md` | New — skill descriptor |
| `.claude/skills/conformance/RUBRIC.md` | New — faithful copy of `refinement-skills/conformance-reviewer-prompt.md` (no content change; already de-labeled) |
| `.claude/skills/code-review/SKILL.md` | New — skill descriptor |
| `.claude/skills/code-review/RUBRIC.md` | New — copy of `refinement-skills/code-review-reviewer-prompt.md` + MarketHawk delabel (2 spots) |
| `scripts/context_budget.py` | `_SKILL_PROMPT_FILES`/`_probe_skill_prompts` refactor: per-file clone-live-first, baked-fallback resolution, scoped to the two migrated files |
| `commands/dark-factory-conformance.md` | Phase 1 step 3 + Phase 3.1 step 4 + memory-write comment: read rubric clone-live-first |
| `commands/dark-factory-plan.md` | Phase 3.5 step 1: read rubric clone-live-first |
| `commands/dark-factory-code-review.md` | Phase 3 step 2: fix stale `.claude/skills/refinement/...` reference → clone-live-first `.claude/skills/code-review/RUBRIC.md` |
| `tests/test_conformance_skill_files.py` | New — asserts the two new conformance skill files exist and carry the required contract |
| `tests/test_code_review_skill_files.py` | New — asserts the two new code-review skill files exist, carry the contract, and are de-labeled |
| `tests/test_context_budget.py` | Extended — clone-live-first / baked-fallback resolution tests for `_probe_skill_prompts` |
| `tests/test_conformance_command_rubric_fallback.py` | New — asserts `dark-factory-conformance.md` reads clone-live before baked |
| `tests/test_plan_command_conformance_rubric_fallback.py` | New — asserts `dark-factory-plan.md` Phase 3.5 reads clone-live before baked |
| `tests/test_code_review_command.py` | Updated — assertion repointed from the stale `.claude/skills/refinement/...` path to `.claude/skills/code-review/RUBRIC.md`, plus baked-fallback ordering |
| `tests/test_code_review_prompt.py` | Updated — `PROMPT` repointed to `.claude/skills/code-review/RUBRIC.md` (new canonical source) |
| `tests/test_conformance_prompt_formatter_rule.py` | Updated — `PROMPT` repointed to `.claude/skills/conformance/RUBRIC.md` (new canonical source) |

Not touched (see Deviations above and spec requirement 7): `.archon/commands/*.md`,
`dark-factory/scripts/context_budget.py`, `Dockerfile`, `refinement-skills/` source files
(left in place, byte-unchanged, as the baked-fallback source), `dark-factory/scripts/diff_rank.py`.

---

## Task 1: Create the `conformance` skill

**Files:**
- `tests/test_conformance_skill_files.py` (new)
- `.claude/skills/conformance/SKILL.md` (new)
- `.claude/skills/conformance/RUBRIC.md` (new)

### Step 1 — Write the failing test

```bash
cat > tests/test_conformance_skill_files.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "conformance"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: conformance" in text
    assert "allowed-tools: Read, Grep, Glob" in text
    assert "RUBRIC.md" in text


def test_rubric_matches_source_prompt_content():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    source = (REPO_ROOT / "refinement-skills" / "conformance-reviewer-prompt.md").read_text(encoding="utf-8")
    assert rubric == source, "RUBRIC.md must be a faithful copy — conformance-reviewer-prompt.md needed no MarketHawk fix"


def test_rubric_preserves_output_contract():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "## Out-of-Scope Changes" in rubric
    assert "**Verdict:**" in rubric
    assert "$ARTIFACT_KIND" in rubric and "$SPEC_CONTENT" in rubric and "$ARTIFACT_CONTENT" in rubric
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_conformance_skill_files.py -v
```
Expected: all three FAIL (`.claude/skills/conformance/` does not exist yet).

### Step 3 — Implement

```bash
mkdir -p .claude/skills/conformance
cp refinement-skills/conformance-reviewer-prompt.md .claude/skills/conformance/RUBRIC.md
```

Create `.claude/skills/conformance/SKILL.md`:

```markdown
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
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_conformance_skill_files.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add tests/test_conformance_skill_files.py .claude/skills/conformance/
git commit -m "feat(skills): add conformance skill (SKILL.md + RUBRIC.md)"
```

---

## Task 2: Create the `code-review` skill (with MarketHawk delabel)

**Files:**
- `tests/test_code_review_skill_files.py` (new)
- `.claude/skills/code-review/SKILL.md` (new)
- `.claude/skills/code-review/RUBRIC.md` (new)

### Step 1 — Write the failing test

```bash
cat > tests/test_code_review_skill_files.py << 'EOF'
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "code-review"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: code-review" in text
    assert "allowed-tools: Read, Grep, Glob" in text
    assert "RUBRIC.md" in text


def test_rubric_has_no_markethawk_mislabel():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in rubric, "RUBRIC.md must not carry the MarketHawk mislabel"


def test_rubric_matches_source_except_delabel():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    source = (REPO_ROOT / "refinement-skills" / "code-review-reviewer-prompt.md").read_text(encoding="utf-8")
    rubric_lines = rubric.splitlines()
    source_lines = source.splitlines()
    assert len(rubric_lines) == len(source_lines), "only the title/opening-sentence lines should change"
    assert rubric_lines[0] == "# Code Reviewer"
    assert source_lines[0] == "# Code Reviewer — MarketHawk"
    assert rubric_lines[2] == "You are a senior code reviewer for the dark factory pipeline. You review a code"
    # every other line is untouched
    for i in range(len(rubric_lines)):
        if i in (0, 2):
            continue
        assert rubric_lines[i] == source_lines[i], f"unexpected content drift at line {i + 1}"


def test_rubric_preserves_output_contract():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "### Findings" in rubric
    assert "[severity] category | path:line | description" in rubric
    for sev in ("critical", "high", "medium", "low"):
        assert sev in rubric
    assert "$ISSUE_CONTEXT" in rubric and "$DIFF_CONTENT" in rubric
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_code_review_skill_files.py -v
```
Expected: all FAIL (`.claude/skills/code-review/` does not exist yet).

### Step 3 — Implement

```bash
mkdir -p .claude/skills/code-review
cp refinement-skills/code-review-reviewer-prompt.md .claude/skills/code-review/RUBRIC.md
```

Edit `.claude/skills/code-review/RUBRIC.md` (only these two lines change):
- Line 1: `# Code Reviewer — MarketHawk` → `# Code Reviewer`
- Line 3: `You are a senior code reviewer for the MarketHawk dark factory pipeline. You review a code` → `You are a senior code reviewer for the dark factory pipeline. You review a code`

Create `.claude/skills/code-review/SKILL.md`:

```markdown
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
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_code_review_skill_files.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add tests/test_code_review_skill_files.py .claude/skills/code-review/
git commit -m "feat(skills): add code-review skill (SKILL.md + RUBRIC.md), fix MarketHawk mislabel"
```

---

## Task 3: `scripts/context_budget.py` — per-file clone-live-first resolution

**Files:**
- `tests/test_context_budget.py` (extended)
- `scripts/context_budget.py`

### Step 1 — Write the failing tests

Append to `tests/test_context_budget.py`:

```python
# ── skill_prompts clone-live-first / baked-fallback resolution ───────────────

def test_skill_prompts_resolves_clone_live_rubric_before_baked(tmp_path, monkeypatch):
    clone_dir = tmp_path / "clone"
    skill_dir = clone_dir / ".claude" / "skills" / "conformance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE conformance content")

    # baked dir has a DIFFERENT conformance prompt present — clone-live must win
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    result = cb._probe_skill_prompts(str(clone_dir))
    assert result["status"] == "included"
    assert result["tokens"] == cb.te.estimate_tokens("CLONE-LIVE conformance content"), (
        "clone-live RUBRIC.md must be used even when a baked copy also exists"
    )


def test_skill_prompts_falls_back_to_baked_when_clone_live_missing(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance content")
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"  # no .claude/skills/ present at all
    clone_dir.mkdir()

    result = cb._probe_skill_prompts(str(clone_dir))
    assert result["status"] == "included"
    assert result["tokens"] == cb.te.estimate_tokens("BAKED conformance content\nBAKED code-review content")


def test_skill_prompts_dropped_when_nothing_resolves(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked_empty"
    baked_dir.mkdir()
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))
    clone_dir = tmp_path / "clone_empty"
    clone_dir.mkdir()

    result = cb._probe_skill_prompts(str(clone_dir))
    assert result["status"] == "dropped"
    assert result["reason"] == "container_mounted_not_found"
```

### Step 2 — Verify they fail

```bash
python -m pytest tests/test_context_budget.py -v -k skill_prompts
```
Expected: FAILS with `TypeError: _probe_skill_prompts() takes 0 positional arguments but 1 was given`.

### Step 3 — Implement

Edit `scripts/context_budget.py`:

```python
_SKILL_PROMPT_DIR = "/opt/refinement-skills"
# Each entry: (baked filename under _SKILL_PROMPT_DIR, clone-live path relative to
# <clone_dir>/.claude/skills/, or None). Only the two files #44 migrates get a
# clone-live candidate; orchestrator/product-owner/architect stay baked-only until #43.
_SKILL_PROMPT_FILES = [
    ("orchestrator-prompt.md", None),
    ("product-owner-prompt.md", None),
    ("architect-prompt.md", None),
    ("conformance-reviewer-prompt.md", "conformance/RUBRIC.md"),
    ("code-review-reviewer-prompt.md", "code-review/RUBRIC.md"),
]


def _resolve_skill_prompt(clone_dir: str, baked_name: str, clone_relpath: str | None) -> str | None:
    if clone_relpath:
        txt = _read_text(os.path.join(clone_dir, ".claude", "skills", clone_relpath))
        if txt:
            return txt
    return _read_text(os.path.join(_SKILL_PROMPT_DIR, baked_name))


def _probe_skill_prompts(clone_dir: str) -> dict:
    parts = []
    for baked_name, clone_relpath in _SKILL_PROMPT_FILES:
        txt = _resolve_skill_prompt(clone_dir, baked_name, clone_relpath)
        if txt:
            parts.append(txt)
    if not parts:
        return _dropped("container_mounted_not_found")
    return {"status": "included", "tokens": te.estimate_tokens("\n".join(parts))}
```

Update the one call site (inside `build_budget`, `clone_dir` is already an in-scope parameter):

```python
        elif sec == "skill_prompts":
            sections[sec] = _probe_skill_prompts(clone_dir)
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_context_budget.py -v
```
Expected: all PASSED, including the pre-existing `test_missing_source_files_drop_gracefully` /
`test_conformance_excludes_inapplicable_sections` (they pass `clone_dir=str(tmp_path)` with no
`.claude/skills/` present, so behavior is unchanged: baked-dir-only, same as before this task).

Also confirm `tests/test_context_pack.py` still passes unmodified (it already threads
`clone_dir=str(tmp_path)` through `assemble_pack`, so no shape change is needed there — this
was flagged as an Open Question in the spec and is resolved by inspection, not a code change):

```bash
python -m pytest tests/test_context_pack.py -v
```
Expected: all PASSED, unmodified.

### Step 5 — Commit

```bash
git add scripts/context_budget.py tests/test_context_budget.py
git commit -m "refactor(context-budget): resolve conformance/code-review skill prompts clone-live-first"
```

---

## Task 4: Wire `commands/dark-factory-conformance.md` to the new rubric

**Files:**
- `tests/test_conformance_command_rubric_fallback.py` (new)
- `commands/dark-factory-conformance.md`

### Step 1 — Write the failing test

```bash
cat > tests/test_conformance_command_rubric_fallback.py << 'EOF'
from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-conformance.md"


def test_reads_clone_live_rubric_before_baked_fallback():
    text = CMD.read_text(encoding="utf-8")
    assert ".claude/skills/conformance/RUBRIC.md" in text
    assert "/opt/refinement-skills/conformance-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/conformance/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/conformance-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_conformance_command_rubric_fallback.py -v
```
Expected: FAILS — `.claude/skills/conformance/RUBRIC.md` is not yet mentioned in the command.

### Step 3 — Implement

Edit `commands/dark-factory-conformance.md`:

Phase 1, step 3 — replace:
```
3. Read `/opt/refinement-skills/conformance-reviewer-prompt.md`
```
with:
```
3. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
   falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
   file is absent. Store the resolved text as `RUBRIC_CONTENT`.
```

Phase 3.1, step 4 — replace:
```
   - `prompt`: Content of `/opt/refinement-skills/conformance-reviewer-prompt.md` with:
```
with:
```
   - `prompt`: `RUBRIC_CONTENT` (resolved in Phase 1 step 3) with:
```

Phase 4's memory-write comment block — replace:
```
  # Read /opt/refinement-skills/conformance-reviewer-prompt.md to understand the reviewer's
```
with:
```
  # Read the conformance rubric (RUBRIC_CONTENT, resolved in Phase 1 step 3) to understand the reviewer's
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_conformance_command_rubric_fallback.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add commands/dark-factory-conformance.md tests/test_conformance_command_rubric_fallback.py
git commit -m "fix(conformance): resolve reviewer rubric clone-live-first, baked-path fallback"
```

---

## Task 5: Wire `commands/dark-factory-plan.md` Phase 3.5 to the new rubric

**Files:**
- `tests/test_plan_command_conformance_rubric_fallback.py` (new)
- `commands/dark-factory-plan.md`

### Step 1 — Write the failing test

```bash
cat > tests/test_plan_command_conformance_rubric_fallback.py << 'EOF'
from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-plan.md"


def test_phase_3_5_reads_clone_live_rubric_before_baked_fallback():
    text = CMD.read_text(encoding="utf-8")
    assert ".claude/skills/conformance/RUBRIC.md" in text
    assert "/opt/refinement-skills/conformance-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/conformance/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/conformance-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_plan_command_conformance_rubric_fallback.py -v
```
Expected: FAILS.

### Step 3 — Implement

Edit `commands/dark-factory-plan.md`, Phase 3.5, step 1 — replace:
```
1. Read `/opt/refinement-skills/conformance-reviewer-prompt.md`
```
with:
```
1. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
   falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
   file is absent. Store the resolved text as `RUBRIC_CONTENT`.
```

And the step 5 prompt-content line — replace:
```
   - `prompt`: Content of `conformance-reviewer-prompt.md` with:
```
with:
```
   - `prompt`: `RUBRIC_CONTENT` (resolved in step 1) with:
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_plan_command_conformance_rubric_fallback.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add commands/dark-factory-plan.md tests/test_plan_command_conformance_rubric_fallback.py
git commit -m "fix(plan): resolve conformance rubric clone-live-first in Phase 3.5, baked-path fallback"
```

---

## Task 6: Fix `commands/dark-factory-code-review.md`'s stale rubric reference

**Files:**
- `tests/test_code_review_command.py` (updated)
- `commands/dark-factory-code-review.md`

### Step 1 — Update the test (write it failing against the current file)

Edit `tests/test_code_review_command.py`, replace:
```python
    # reads the clone-path reviewer prompt (not the baked /opt path)
    assert ".claude/skills/refinement/code-review-reviewer-prompt.md" in text
```
with:
```python
    # reads the clone-live rubric first, falls back to the baked /opt path
    assert ".claude/skills/code-review/RUBRIC.md" in text
    assert "/opt/refinement-skills/code-review-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/code-review/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/code-review-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_code_review_command.py -v
```
Expected: FAILS — the command still names the old, nonexistent
`.claude/skills/refinement/code-review-reviewer-prompt.md` path with no fallback.

### Step 3 — Implement

Edit `commands/dark-factory-code-review.md`, Phase 3, step 2 — replace:
```
2. Read `.claude/skills/refinement/code-review-reviewer-prompt.md`.
```
with:
```
2. Read the code-review rubric, clone-live-first: `.claude/skills/code-review/RUBRIC.md`,
   falling back to `/opt/refinement-skills/code-review-reviewer-prompt.md` if the clone-live
   file is absent. Store the resolved text as `RUBRIC_CONTENT`.
```

And step 3's prompt-content line — replace:
```
   - `prompt`: the reviewer-prompt content with `$ISSUE_CONTEXT` replaced by the issue context from step 1 and `$DIFF_CONTENT` replaced by the contents of `$ARTIFACTS_DIR/review_diff.txt`.
```
with:
```
   - `prompt`: `RUBRIC_CONTENT` (resolved in step 2) with `$ISSUE_CONTEXT` replaced by the issue context from step 1 and `$DIFF_CONTENT` replaced by the contents of `$ARTIFACTS_DIR/review_diff.txt`.
```

### Step 4 — Verify it passes

```bash
python -m pytest tests/test_code_review_command.py -v
```
Expected: PASSED.

### Step 5 — Commit

```bash
git add commands/dark-factory-code-review.md tests/test_code_review_command.py
git commit -m "fix(code-review): correct stale rubric reference, add clone-live-first/baked-fallback"
```

---

## Task 7: Repoint the prompt-content regression tests at the new canonical `RUBRIC.md` files

**Files:**
- `tests/test_code_review_prompt.py`
- `tests/test_conformance_prompt_formatter_rule.py`

### Step 1 — Write the failing change

Edit `tests/test_code_review_prompt.py`, replace:
```python
PROMPT = Path(__file__).resolve().parents[1] / "refinement-skills" / "code-review-reviewer-prompt.md"
```
with:
```python
PROMPT = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "code-review" / "RUBRIC.md"
```

Edit `tests/test_conformance_prompt_formatter_rule.py`, replace:
```python
PROMPT = (
    Path(__file__).resolve().parents[1]
    / "refinement-skills" / "conformance-reviewer-prompt.md"
)
```
with:
```python
PROMPT = (
    Path(__file__).resolve().parents[1]
    / ".claude" / "skills" / "conformance" / "RUBRIC.md"
)
```

### Step 2 — Verify (should already pass, since Tasks 1-2 created faithful copies)

```bash
python -m pytest tests/test_code_review_prompt.py tests/test_conformance_prompt_formatter_rule.py -v
```
Expected: all PASSED — this confirms the new `RUBRIC.md` files preserve every assertion the old
`refinement-skills/*.md` files satisfied (placeholders, severity vocabulary, `### Findings`
format, the formatter/import-ordering OOS exception and its ordering relative to the `[OOS]`
bullet example).

### Step 3 — Commit

```bash
git add tests/test_code_review_prompt.py tests/test_conformance_prompt_formatter_rule.py
git commit -m "test: repoint prompt-contract tests at the canonical .claude/skills/*/RUBRIC.md files"
```

---

## Task 8: Full verification pass

**Files:** none (verification only)

### Step 1 — Full test suite

```bash
python -m pytest tests/ -v
```
Expected: all tests PASSED, including every test touched or added in Tasks 1-7 and the full
pre-existing suite (no regressions).

### Step 2 — Workflow DAG / smoke checks (Tier 1 rollout, per spec Assumptions)

```bash
bash smoke_gate.sh
python3 dark-factory/scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python3 dark-factory/scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```
Expected: all exit 0 — this ticket makes no DAG changes, so these should be unaffected; running
them confirms that.

### Step 3 — Targeted scratch dry run (Tier 1, per spec)

Confirm the resolution order end-to-end by simulating both branches of the fallback:

```bash
# clone-live present (normal case after this ticket lands):
test -f .claude/skills/conformance/RUBRIC.md && echo "conformance: clone-live OK"
test -f .claude/skills/code-review/RUBRIC.md && echo "code-review: clone-live OK"

# baked fallback still intact (requirement 7 — refinement-skills/ untouched):
test -f refinement-skills/conformance-reviewer-prompt.md && echo "conformance: baked fallback intact"
test -f refinement-skills/code-review-reviewer-prompt.md && echo "code-review: baked fallback intact"
```
Expected: all four echo lines print — both new clone-live files exist, and both baked fallback
sources remain in place, satisfying the fallback contract this ticket adds.

### Step 4 — No further commit

This task is verification-only; nothing to commit if all checks pass. If any test regresses,
fix it under its own task's TDD cycle (do not bundle an unrelated fix here) before considering
the plan complete.

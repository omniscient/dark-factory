# Plan: Wire comment-digest.md into Implement's Continue-Intent Phase 1

**Issue:** omniscient/dark-factory#45 — Wire Claude Skills dynamic context injection to #36 context artifacts
**Spec:** [docs/superpowers/specs/2026-07-10-implement-continue-comment-digest-injection-design.md](../specs/2026-07-10-implement-continue-comment-digest-injection-design.md)

## Goal

Close the one concrete gap the spec identifies: the `digest-comments` workflow node
(`workflows/archon-dark-factory.yaml`) already runs `dark-factory/scripts/comment_digest.py` and
writes a compact, deterministic, budget-capped `$ARTIFACTS_DIR/comment-digest.md` on every
`continue`-intent run, but `commands/dark-factory-implement.md`'s Phase 1 "If intent is continue"
section never reads it — it still instructs the agent to read the raw `comments`, `pr_reviews`,
and `pr_inline_comments` arrays out of `issue.json`. This plan makes that section prefer the
digest file when present and non-empty, and fall back to the raw-array instructions verbatim when
it is not (feature-gate-off or the step didn't run), preserving this repo's documented fail-open
doctrine.

## Architecture

This is a prompt/documentation ticket: no application code changes, no workflow YAML change, no
script change. The "implementation" is a markdown-content edit to one Archon command file. Per
this repo's existing convention for doc-only tickets (see the archived #43/#44 plans), TDD is
adapted mechanically: a pytest content-assertion test (`Path.read_text()` + `assert`, the same
shape as `tests/test_command_issue_context_contract.py` and
`tests/test_conformance_prompt_formatter_rule.py`) is written first, confirmed to fail against the
current file, then the command file is edited until the assertions pass.

`commands/dark-factory-implement.md` has a `.archon/commands/dark-factory-implement.md` mirror,
but that mirror is already substantially stale for this file (it lacks the "Invocation Contract"
section and several other newer blocks present in `commands/`, confirmed by `diff`) — unlike
`dark-factory-refine.md`/`dark-factory-plan.md`, whose mirrors are kept byte-identical by
convention. Per the spec's flagged assumption and its explicit "Change scope:
`commands/dark-factory-implement.md` only" / "No other file changes" statement, this plan edits
only the live `commands/` copy and does not touch the mirror or attempt to reconcile its
pre-existing drift — that reconciliation is out of scope for this ticket.

## Tech Stack

- Python 3 stdlib + `pytest` for the content-assertion regression test (matches
  `python -m pytest tests/ -v` from `CLAUDE.md`)
- `diff`/`grep` for manual verification during editing

## File Structure

| File | Change |
|---|---|
| `commands/dark-factory-implement.md` | Phase 1 "If intent is continue" section: prefer `$ARTIFACTS_DIR/comment-digest.md`, fall back to raw arrays |
| `tests/test_implement_continue_comment_digest.py` | New — regression tests for the above |

No other files are created or modified. `architecture_slice.py`/`context_pack.py` wiring,
`memory_retrieve.py` wiring into conformance/code-review, and any change to `refine`, `plan`,
`conformance`, or `code-review` commands are explicitly out of scope per the spec's Alternatives
Considered and Open Questions sections.

---

## Task 1: Add regression test for digest-preference + raw-array fallback

**Files:**
- `tests/test_implement_continue_comment_digest.py` (new)

### Step 1 — Write the failing test

```bash
cat > tests/test_implement_continue_comment_digest.py << 'EOF'
from pathlib import Path

COMMAND = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-implement.md"


def _continue_section() -> str:
    text = COMMAND.read_text(encoding="utf-8")
    start = text.index('### If intent is "continue"')
    end = text.index('### If intent is "new"')
    return text[start:end]


def test_continue_intent_checks_for_comment_digest():
    section = _continue_section()
    assert '[ -s "$ARTIFACTS_DIR/comment-digest.md" ]' in section, (
        "continue-intent Phase 1 must check for the comment-digest artifact by "
        "presence/non-emptiness, not a script exit code"
    )
    assert "FEEDBACK_SOURCE" in section


def test_continue_intent_prefers_digest_over_raw_arrays():
    section = _continue_section()
    assert "comment-digest.md" in section
    assert "do not separately re-read the raw arrays it was built from" in section


def test_continue_intent_keeps_raw_array_fallback():
    section = _continue_section()
    assert "Read the latest issue comments (bottom of the `comments` array)" in section
    assert "Read `pr_reviews` if present" in section
    assert "Read `pr_inline_comments` if present" in section


def test_continue_intent_digest_check_precedes_raw_array_fallback():
    section = _continue_section()
    digest_pos = section.index('[ -s "$ARTIFACTS_DIR/comment-digest.md" ]')
    fallback_pos = section.index(
        "Read the latest issue comments (bottom of the `comments` array)"
    )
    assert digest_pos < fallback_pos, (
        "the digest presence-check must appear before the raw-array fallback instructions"
    )


def test_continue_intent_keeps_branch_review_and_focus_steps():
    section = _continue_section()
    assert "git log --oneline main..HEAD" in section
    assert "Focus exclusively on addressing the feedback" in section
EOF
```

### Step 2 — Verify it fails

```bash
python -m pytest tests/test_implement_continue_comment_digest.py -v
```
Expected: `test_continue_intent_checks_for_comment_digest` and
`test_continue_intent_prefers_digest_over_raw_arrays` FAIL (the current file has no
`comment-digest.md` reference and no `FEEDBACK_SOURCE` variable in the continue-intent section);
`test_continue_intent_keeps_raw_array_fallback` and
`test_continue_intent_keeps_branch_review_and_focus_steps` PASS (today's raw-array-only text
already contains these strings); `test_continue_intent_digest_check_precedes_raw_array_fallback`
FAILS or errors (the digest-check substring does not exist yet, so `.index()` raises `ValueError`).

---

## Task 2: Wire the digest-preference into `dark-factory-implement.md`

**Files:**
- `commands/dark-factory-implement.md`

### Step 1 — Implement

In `commands/dark-factory-implement.md`, replace the current "If intent is continue" section:

```
### If intent is "continue"

This is an iteration on existing work. **The latest comments on the issue and PR contain feedback that must drive your changes.** Do NOT re-implement from scratch. Instead:
1. Read the latest issue comments (bottom of the `comments` array) — these are the user's feedback
2. Read `pr_reviews` if present — top-level PR conversation and review summaries
3. Read `pr_inline_comments` if present — these are line-level code review comments with `path` and `line` pointing to exact locations
3. Review what was already implemented on this branch (`git log --oneline main..HEAD`, read changed files)
4. Focus exclusively on addressing the feedback
```

with:

```
### If intent is "continue"

This is an iteration on existing work. **The latest human feedback on the issue and PR must drive your changes.** Do NOT re-implement from scratch.

Prefer the pre-computed comment digest over raw comment/PR-review arrays:

```bash
if [ -s "$ARTIFACTS_DIR/comment-digest.md" ]; then
  FEEDBACK_SOURCE="$ARTIFACTS_DIR/comment-digest.md"
else
  FEEDBACK_SOURCE=""  # fall back to raw arrays below
fi
```

1. If `$FEEDBACK_SOURCE` is set, read `$ARTIFACTS_DIR/comment-digest.md` — this is a pre-filtered, deterministic, token-budget-capped digest of human-authored feedback (issue comments after the latest factory marker, PR review summaries, and inline review comments with `path`/`line` pointers) already assembled by the `digest-comments` workflow step. Treat it as the complete feedback source; do not separately re-read the raw arrays it was built from.
2. If `$FEEDBACK_SOURCE` is empty (the digest file is missing or empty — token optimization is disabled for this run, or the step did not run), fall back to the raw arrays exactly as before:
   - Read the latest issue comments (bottom of the `comments` array)
   - Read `pr_reviews` if present
   - Read `pr_inline_comments` if present
3. Review what was already implemented on this branch (`git log --oneline main..HEAD`, read changed files)
4. Focus exclusively on addressing the feedback
```

This also incidentally fixes a pre-existing duplicate `3.`/`3.` numbering typo in the original
list — not a separate change, just a byproduct of the replacement.

No other lines in the file change. Do not touch `.archon/commands/dark-factory-implement.md` (see
Architecture above).

### Step 2 — Verify it passes

```bash
python -m pytest tests/test_implement_continue_comment_digest.py -v
```
Expected: all 5 tests PASSED.

### Step 3 — Commit

```bash
git add commands/dark-factory-implement.md tests/test_implement_continue_comment_digest.py
git commit -m "feat(implement): prefer comment-digest.md over raw arrays in continue-intent Phase 1 (#45)"
```

---

## Task 3: Full verification sweep

**Files:** none (verification only).

### Step 1 — Run the full test suite

```bash
python -m pytest tests/ -v
```
Expected: all tests PASSED, including the new `tests/test_implement_continue_comment_digest.py`.
In particular `tests/test_command_issue_context_contract.py` still passes — the edit does not
remove `$ARTIFACTS_DIR/issue.json` or the "sanctioned Archon command entrypoint" text, both of
which live outside the replaced section.

### Step 2 — Confirm the replaced section is the only diff

```bash
git diff --stat origin/main HEAD -- commands/dark-factory-implement.md
```
Expected: one file changed, matching the single-section replacement in Task 2 (no incidental
edits elsewhere in the file).

### Step 3 — Confirm no other command or script files changed

```bash
git diff --stat origin/main HEAD
```
Expected: only `commands/dark-factory-implement.md` and
`tests/test_implement_continue_comment_digest.py` appear — no changes to
`workflows/archon-dark-factory.yaml`, `dark-factory/scripts/comment_digest.py`,
`.archon/commands/dark-factory-implement.md`, or any `refine`/`plan`/`conformance`/`code-review`
command.

### Step 4 — Placeholder scan

```bash
grep -n "TBD\|TODO" commands/dark-factory-implement.md
```
Expected: no matches introduced by this change.

No commit for this task — it is a verification pass over the commit already made in Task 2.

# Implementation Plan: Fix Gate 2/3 diff construction — stop blanket-excluding `*.md`

**Issue:** #399
**Spec:** `docs/superpowers/specs/2026-09-08-gate-diff-md-exclusion-fix-design.md`

## Goal

Gate 2 (`commands/dark-factory-conformance.md`) and Gate 3 (`commands/dark-factory-code-review.md`)
currently build their reviewer diff artifacts with a blanket `':!*.md'` pathspec exclusion. This
makes `commands/*.md`, `refinement-skills/*.md`, `.claude/skills/**`, `.archon/**` (except
`.archon/memory/**`), and `workflows/**` content — all executable policy — invisible to the
conformance and code-review subagents, regardless of extension. Replace the blanket exclusion at
all three call sites with a blacklist scoped to the repo's actual prose/generated trees
(`docs/**`, `evals/**`, `bench/**`), and narrow Step 3.6.0's doc-exemption guard so a now-visible
out-of-scope `.md` finding against a policy file isn't silently discarded by an unrelated
over-broad exemption.

## Architecture

**Pathspec change**, applied at all three existing call sites: replace the bare `':!*.md'` token
with the three-token sequence `':!docs/*.md' ':!evals/*.md' ':!bench/*.md'` (git's default
pathspec magic lets `*` match `/`, so each token covers its tree at any depth). This is a
blacklist inversion, not an allowlist: a positive-pathspec carve-out for named policy directories
is not achievable (git's `:!pattern` exclude-magic unconditionally removes a matching path
regardless of any positive pathspec that also matches it — confirmed in the approved spec), and a
five-directory allowlist would reproduce the exact blind-spot failure mode this ticket exists to
fix (`tests/fixtures/verdicts/*.md`, 18 live test fixtures, sits outside every named policy
directory).

At `commands/dark-factory-conformance.md:126` (Step 3.0.1, `RAW_DIFF`), the two now-redundant
`':!docs/codeindex-hotspots.md'` / `':!docs/database-schema.md'` tokens are also dropped (both are
subsumed by the new `':!docs/*.md'` token). At `:460` (Phase 3.5 reconcile-loop diff refresh) and
`commands/dark-factory-code-review.md:63` (Phase 2, `review_diff.txt`), only the bare `':!*.md'`
token is swapped for the three-token sequence — those two sites' other tokens are left exactly
as-is (per the approved spec's Architecture section; `code-review.md:63` keeps its now-partially-
redundant `docs/codeindex-hotspots.md`/`docs/database-schema.md` tokens, which is harmless —
`docs/*.md` already subsumes them).

**Step 3.6.0 exemption narrowing** (`commands/dark-factory-conformance.md:337`): the current regex
`'\.md([^a-z0-9]|$)|(^|[^a-z])docs/'` treats *any* `.md` path as an exempt "doc change," so once
Requirement 1 makes `commands/*.md` visible to the reviewer, this unrelated guard would silently
drop the now-correct `[OOS] commands/dark-factory-plan.md — ...` finding before excision/
ticketing — a detected-but-discarded gap, worse than today's never-detected status quo. Narrow it
to `'(^|[^a-z0-9_])(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|README|CLAUDE)\.md([^a-z0-9]|$)|(^|[^a-z])docs/'`
— the actual `dark-factory-implement.md` Phase-4 doc-map filename set plus `docs/**`. The left
boundary is `(^|[^a-z0-9_])`, not `(^|/)`: the tested string is the `area` variable, which begins
with `[OOS] ` and often wraps the path in backticks, so a root-level filename is preceded by a
space or a backtick, never start-of-string or `/`.

**No shared script.** Both gates already duplicate this exclusion list inline by documented
convention; this fix preserves that pattern (three literal edits) rather than introducing new
shared pathspec-construction plumbing in `scripts/gate_lib.sh`.

## Tech Stack

Bash embedded in Claude Code command markdown files (`commands/*.md`), a bash pathspec/regex
regression test (this repo's existing convention — see `tests/test_close_preview_teardown.sh`),
and `pytest` content-assertion tests over the command files (see
`tests/test_conformance_formatter_step.py`, `tests/test_command_issue_context_contract.py`).

## File Structure

| Path | Change |
|---|---|
| `commands/dark-factory-conformance.md` | Pathspec fix at Step 3.0.1 (`:126`) and Phase 3.5 reconcile-loop refresh (`:460`); Step 3.6.0 doc-exemption regex narrowed (`:337`) |
| `commands/dark-factory-code-review.md` | Pathspec fix at Phase 2 (`:63`) |
| `tests/test_gate_diff_pathspec_tokens.py` | New: static regression lock on the exact three-token pathspec sequence in both command files |
| `tests/test_gate_diff_md_visibility.sh` | New: behavioral test proving a `commands/*.md`-only diff yields a non-empty, commands-inclusive/docs-exclusive `git diff` under the new pathspec |
| `tests/test_gate_diff_doc_exemption_regex.py` | New: static test proving the narrowed Step 3.6.0 regex exempts doc-map targets but enforces policy files |
| `.github/workflows/ci.yml` | Wire `tests/test_gate_diff_md_visibility.sh` into the explicit per-`.sh`-file test list |

---

## Task 1: Pathspec fix at all three gate call sites + regression-lock tests

**Files:** `commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`,
`tests/test_gate_diff_pathspec_tokens.py` (new), `tests/test_gate_diff_md_visibility.sh` (new),
`.github/workflows/ci.yml`

### TDD Steps

1. Write the failing static test:

```python
# tests/test_gate_diff_pathspec_tokens.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"
CODE_REVIEW_CMD = REPO_ROOT / "commands" / "dark-factory-code-review.md"

EXPECTED_TOKEN_SEQUENCE = "':!docs/*.md' ':!evals/*.md' ':!bench/*.md'"


def test_conformance_command_has_new_token_sequence_exactly_twice():
    text = CONFORMANCE_CMD.read_text(encoding="utf-8")
    assert text.count(EXPECTED_TOKEN_SEQUENCE) == 2, (
        "commands/dark-factory-conformance.md must use the "
        f"{EXPECTED_TOKEN_SEQUENCE!r} pathspec sequence at both diff call sites "
        "(Step 3.0.1 RAW_DIFF and the Phase 3.5 reconcile-loop diff refresh)"
    )


def test_code_review_command_has_new_token_sequence_exactly_once():
    text = CODE_REVIEW_CMD.read_text(encoding="utf-8")
    assert text.count(EXPECTED_TOKEN_SEQUENCE) == 1, (
        "commands/dark-factory-code-review.md must use the "
        f"{EXPECTED_TOKEN_SEQUENCE!r} pathspec sequence at its Phase 2 diff call site"
    )


def test_neither_command_still_uses_blanket_md_exclusion():
    for path in (CONFORMANCE_CMD, CODE_REVIEW_CMD):
        text = path.read_text(encoding="utf-8")
        assert "':!*.md'" not in text, (
            f"{path.name} must not reintroduce the blanket ':!*.md' exclusion — "
            "it hides commands/*.md, refinement-skills/*.md and other "
            "executable-policy content from the reviewer (issue #399)"
        )
```

2. Write the failing behavioral test:

```bash
#!/usr/bin/env bash
# tests/test_gate_diff_md_visibility.sh
# Regression test for #399: Gate 2/3 diff construction must not blanket-exclude
# *.md (which hid commands/*.md, refinement-skills/*.md, etc. from the
# conformance/code-review reviewers — see #394). Builds a throwaway git repo,
# commits a baseline, modifies a commands/*.md-analog file and a docs/*.md-analog
# file, extracts the exclusion pathspec tokens straight out of
# commands/dark-factory-conformance.md (precedent:
# tests/test_command_issue_context_contract.py) rather than hard-coding them, and
# asserts the resulting `git diff -- <tokens>` contains the commands-file change
# but not the docs-file change.
# Run: bash tests/test_gate_diff_md_visibility.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFORMANCE_CMD="${REPO_ROOT}/commands/dark-factory-conformance.md"
ORIG_DIR="$(pwd)"

PASSED=0; FAILED=0
assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected to find '$needle' in output" >&2; FAILED=$((FAILED+1))
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    echo "  FAIL: $desc — did not expect '$needle' in output" >&2; FAILED=$((FAILED+1))
  else
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  fi
}

echo "--- gate diff: *.md pathspec must not blind-spot commands/*.md ---"

RAW_LINE=$(grep -m1 "':!docs/\*\.md'" "$CONFORMANCE_CMD" || true)
if [ -z "$RAW_LINE" ]; then
  echo "  FAIL: could not find the docs/*.md exclusion pathspec in $CONFORMANCE_CMD" >&2
  FAILED=$((FAILED+1))
else
  mapfile -t RAW_TOKENS < <(grep -oE "':![^']*'" <<< "$RAW_LINE")
  # Strip the shell single-quotes each token is wrapped in — grep captures them
  # literally (e.g. "':!docs/*.md'"), but git's pathspec argument must be the bare
  # ":!docs/*.md" the shell would have unquoted at the real call site; passing the
  # quote characters through verbatim makes git match nothing (silently empty diff).
  TOKENS=()
  for t in "${RAW_TOKENS[@]}"; do
    TOKENS+=("${t:1:-1}")
  done
  echo "  extracted tokens: ${TOKENS[*]}"

  WORKDIR=$(mktemp -d)
  cd "$WORKDIR" || exit 1
  git init -q -b main
  git config user.email test@example.com
  git config user.name test
  mkdir -p commands docs evals bench
  echo baseline > commands/dark-factory-plan.md
  echo baseline > docs/some-spec.md
  echo baseline > evals/some-scorecard.md
  echo baseline > bench/baseline.md
  git add -A
  git commit -qm baseline >/dev/null

  git checkout -qb feature
  echo "changed commands content" > commands/dark-factory-plan.md
  echo "changed docs content" > docs/some-spec.md
  echo "changed evals content" > evals/some-scorecard.md
  echo "changed bench content" > bench/baseline.md
  git add -A
  git commit -qm "feature change" >/dev/null

  DIFF_OUT=$(git diff main...HEAD -- "${TOKENS[@]}" 2>&1)

  assert_contains "diff includes commands/*.md change" "changed commands content" "$DIFF_OUT"
  assert_not_contains "diff excludes docs/*.md change" "changed docs content" "$DIFF_OUT"
  assert_not_contains "diff excludes evals/*.md change" "changed evals content" "$DIFF_OUT"
  assert_not_contains "diff excludes bench/*.md change" "changed bench content" "$DIFF_OUT"
  if [ -n "$DIFF_OUT" ]; then
    echo "  PASS: diff is non-empty"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: diff is empty" >&2; FAILED=$((FAILED+1))
  fi

  cd "$ORIG_DIR" || exit 1
  rm -rf "$WORKDIR"
fi

echo ""
echo "============================="
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "============================="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
```

3. Verify fail:
   ```bash
   cd /workspace/dark-factory
   chmod +x tests/test_gate_diff_md_visibility.sh
   python -m pytest tests/test_gate_diff_pathspec_tokens.py -v
   bash tests/test_gate_diff_md_visibility.sh
   ```
   Expected: all three pytest assertions fail (the old blanket `':!*.md'` token is still present,
   the new three-token sequence is absent). The bash script's own `grep -m1 "':!docs/\*\.md'"`
   also finds nothing yet, so it reports one `FAIL` (extraction failure) and exits `1`.

4. Implement. In `commands/dark-factory-conformance.md`, replace (Step 3.0.1, around line 126):

   ```
   RAW_DIFF=$(git diff main...HEAD \
     -- ':!*.lock' ':!*.md' \
     ':!.archon/memory/**' \
     ':!codeindex.json' ':!symbolindex.json' \
     ':!docs/codeindex-hotspots.md' \
     ':!docs/database-schema.md' \
     2>/dev/null)
   ```

   With:

   ```
   RAW_DIFF=$(git diff main...HEAD \
     -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' \
     ':!.archon/memory/**' \
     ':!codeindex.json' ':!symbolindex.json' \
     2>/dev/null)
   ```

   Then replace (Phase 3.5 reconcile loop, around line 460):

   ```
   git diff main...HEAD -- ':!*.lock' ':!*.md' 2>/dev/null | head -1000
   ```

   With:

   ```
   git diff main...HEAD -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' 2>/dev/null | head -1000
   ```

   In `commands/dark-factory-code-review.md`, replace (Phase 2, around line 63):

   ```
   git diff main...HEAD \
     -- ':!*.lock' ':!*.md' \
     ':!.archon/memory/**' \
     ':!codeindex.json' ':!symbolindex.json' \
     ':!docs/codeindex-hotspots.md' ':!docs/database-schema.md' \
     2>/dev/null > "$RANK_IN"
   ```

   With:

   ```
   git diff main...HEAD \
     -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' \
     ':!.archon/memory/**' \
     ':!codeindex.json' ':!symbolindex.json' \
     ':!docs/codeindex-hotspots.md' ':!docs/database-schema.md' \
     2>/dev/null > "$RANK_IN"
   ```

5. Wire the new bash test into CI. In `.github/workflows/ci.yml`, replace the existing step

   ```
      - run: bash tests/test_budget_context.sh
   ```

   with the same line followed by the new step. Keep the new line at exactly the same
   indentation as the existing `- run:` steps (six spaces before the dash in the file; a deeper
   indent is a YAML `ScannerError` that fails the whole workflow):

   ```
      - run: bash tests/test_budget_context.sh
      - run: bash tests/test_gate_diff_md_visibility.sh
   ```

6. Verify pass:
   ```bash
   python -m pytest tests/test_gate_diff_pathspec_tokens.py -x -v
   bash tests/test_gate_diff_md_visibility.sh
   ```
   Expected: all pytest assertions pass; the bash script reports `Results: 5 passed, 0 failed`
   and exits `0`.

7. Commit:
   ```bash
   git add commands/dark-factory-conformance.md commands/dark-factory-code-review.md \
     tests/test_gate_diff_pathspec_tokens.py tests/test_gate_diff_md_visibility.sh \
     .github/workflows/ci.yml
   git commit -m "fix(#399): stop blanket-excluding *.md from Gate 2/3 reviewer diffs"
   ```

---

## Task 2: Narrow Step 3.6.0's doc-exemption regex

**Files:** `commands/dark-factory-conformance.md`, `tests/test_gate_diff_doc_exemption_regex.py`
(new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_gate_diff_doc_exemption_regex.py
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"

NARROWED_REGEX = (
    r"(^|[^a-z0-9_])(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|README|CLAUDE)"
    r"\.md([^a-z0-9]|$)|(^|[^a-z])docs/"
)
DOC_EXEMPTION_PATTERN = re.compile(NARROWED_REGEX, re.IGNORECASE)

EXEMPT_AREAS = [
    "[OOS] ARCHITECTURE.md ",
    "[OOS] `CLAUDE.md` (repo instructions) ",
    "[OOS] README.md ",
    "[OOS] docs/superpowers/specs/foo.md ",
]

ENFORCED_AREAS = [
    "[OOS] commands/dark-factory-plan.md ",
    "[OOS] refinement-skills/VERIFIER-CONTRACT.md ",
    "[OOS] `.claude/skills/x/SKILL.md` ",
]


def test_command_file_contains_narrowed_regex():
    text = CONFORMANCE_CMD.read_text(encoding="utf-8")
    assert NARROWED_REGEX in text, (
        "Step 3.6.0's doc-exemption guard must use the narrowed doc-map regex, "
        "not the old blanket '\\.md(...)' pattern"
    )
    # The old blanket guard is a literal substring of the new one, so presence of the
    # new regex alone would not catch a re-added blanket guard elsewhere in the file.
    assert "grep -qiE '\\.md([^a-z0-9]|$)" not in text, (
        "the old blanket '\\.md(...)' guard must be gone, not merely accompanied by the new one"
    )


def test_narrowed_regex_exempts_doc_map_targets():
    for area in EXEMPT_AREAS:
        assert DOC_EXEMPTION_PATTERN.search(area), f"expected exemption for: {area!r}"


def test_narrowed_regex_enforces_policy_files():
    for area in ENFORCED_AREAS:
        assert not DOC_EXEMPTION_PATTERN.search(area), (
            f"expected enforcement (no exemption) for: {area!r}"
        )
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_gate_diff_doc_exemption_regex.py -v
   ```
   Expected: `test_command_file_contains_narrowed_regex` fails (old regex still present);
   `test_narrowed_regex_exempts_doc_map_targets` and `test_narrowed_regex_enforces_policy_files`
   already pass (they test the Python-side pattern object directly, independent of the file
   content) — this confirms the pattern itself is well-formed before wiring it into the command
   file.

3. Implement. In `commands/dark-factory-conformance.md`, Step 3.6.0 (around line 337), replace:

   ```
     if printf '%s' "$area" | grep -qiE '\.md([^a-z0-9]|$)|(^|[^a-z])docs/'; then
   ```

   With:

   ```
     if printf '%s' "$area" | grep -qiE '(^|[^a-z0-9_])(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|README|CLAUDE)\.md([^a-z0-9]|$)|(^|[^a-z])docs/'; then
   ```

4. Verify pass:
   ```bash
   python -m pytest tests/test_gate_diff_doc_exemption_regex.py -x -v
   ```
   Expected: all three tests pass.

5. Commit:
   ```bash
   git add commands/dark-factory-conformance.md tests/test_gate_diff_doc_exemption_regex.py
   git commit -m "fix(#399): narrow Step 3.6.0 doc-exemption guard to the actual doc-map filename set"
   ```

---

## Task 3: Full-suite verification and self-review

**Files:** none (verification only).

### Steps

1. Run the full test suite exactly as CI does:
   ```bash
   cd /workspace/dark-factory
   PYTHONPATH=scripts python -m pytest tests/ -v
   ```
   Expected: all tests pass, including the three new files from Tasks 1-2 and every
   pre-existing test adjacent to the edited command files (`test_conformance_formatter_step.py`,
   `test_command_issue_context_contract.py`, `test_conformance_dedupe_step.py`,
   `test_conformance_command_rubric_fallback.py`, `test_conformance_command_shadow_review.py`).

2. Run the bash test explicitly (it is not auto-discovered by `pytest`, only by its new CI
   entry):
   ```bash
   bash tests/test_gate_diff_md_visibility.sh
   ```
   Expected: `Results: 5 passed, 0 failed`, exit `0`.

3. Run the smoke gate and workflow DAG checks exactly as CI's `dag-check` job does (this ticket
   adds no DAG node and changes no `workflows/archon-dark-factory.yaml` content, so both should
   be a no-op confirmation):
   ```bash
   bash smoke_gate.sh
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```

4. Self-review checklist (per Requirements, re-verified against the diff):
   - [ ] The plan's own issue-number line (`**Issue:** #399`) is present at the top of this
     document, and every task step above has an exact file path and a real code block (no
     "TBD"/"similar to Task N" placeholders).
   - [ ] `grep -c "':!docs/\*\.md' ':!evals/\*\.md' ':!bench/\*\.md'" commands/dark-factory-conformance.md`
     returns `2`; the same grep against `commands/dark-factory-code-review.md` returns `1`.
   - [ ] `grep -c "':!\*\.md'" commands/dark-factory-conformance.md commands/dark-factory-code-review.md`
     returns `0` for both files.
   - [ ] `commands/dark-factory-implement.md`'s `':(exclude)*.md'` self-review scan is untouched
     (non-goal, operator review F5) — `git diff origin/main HEAD -- commands/dark-factory-implement.md`
     is empty (two-dot form per memory `[PATTERN]` #250: three-dot can pull in commits `main`
     merged independently after divergence, producing a false-positive non-empty diff).
   - [ ] No edits under `.archon/commands/`, `deploy/instances/**`, or
     `.github/workflows/publish.yml` (hard-excluded surfaces).
   - [ ] `scripts/gate_lib.sh` has zero diff — no new shared pathspec-construction helper was
     added (per the approved spec's rejection of Alternative 4).

5. Commit any residual fixups from the self-review individually (each with its own `git commit`),
   per this repo's TDD convention of one commit per verified change.

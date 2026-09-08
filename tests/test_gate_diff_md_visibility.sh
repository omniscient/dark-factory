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
  mkdir -p commands docs evals bench refinement-skills .claude/skills/example
  echo baseline > commands/dark-factory-plan.md
  echo baseline > docs/some-spec.md
  echo baseline > evals/some-scorecard.md
  echo baseline > bench/baseline.md
  echo baseline > refinement-skills/reviewer.md
  echo baseline > .claude/skills/example/SKILL.md
  echo baseline > README.md
  echo baseline > CLAUDE.md
  git add -A
  git commit -qm baseline >/dev/null

  git checkout -qb feature
  echo "changed commands content" > commands/dark-factory-plan.md
  echo "changed docs content" > docs/some-spec.md
  echo "changed evals content" > evals/some-scorecard.md
  echo "changed bench content" > bench/baseline.md
  echo "changed refinement-skills content" > refinement-skills/reviewer.md
  echo "changed claude skills content" > .claude/skills/example/SKILL.md
  echo "changed readme content" > README.md
  echo "changed claude-md content" > CLAUDE.md
  git add -A
  git commit -qm "feature change" >/dev/null

  DIFF_OUT=$(git diff main...HEAD -- "${TOKENS[@]}" 2>/dev/null)

  assert_contains "diff includes commands/*.md change" "changed commands content" "$DIFF_OUT"
  assert_not_contains "diff excludes docs/*.md change" "changed docs content" "$DIFF_OUT"
  assert_not_contains "diff excludes evals/*.md change" "changed evals content" "$DIFF_OUT"
  assert_not_contains "diff excludes bench/*.md change" "changed bench content" "$DIFF_OUT"
  assert_contains "diff includes refinement-skills/*.md change" "changed refinement-skills content" "$DIFF_OUT"
  assert_contains "diff includes .claude/skills/**/*.md change" "changed claude skills content" "$DIFF_OUT"
  assert_contains "diff includes root README.md change" "changed readme content" "$DIFF_OUT"
  assert_contains "diff includes root CLAUDE.md change" "changed claude-md content" "$DIFF_OUT"
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

#!/usr/bin/env bash
# Regression guard for the cost-report comment endpoint in entrypoint.sh.
#
# Bug: post_cost_report() read and PATCHed the cost-report comment via
#   /repos/{owner}/{repo}/issues/{ISSUE_NUM}/comments/{COMMENT_ID}
# That path 404s — GitHub's single-comment endpoint omits the issue number
#   /repos/{owner}/{repo}/issues/comments/{COMMENT_ID}
# The 404 was swallowed (2>/dev/null, 2>&1), so the comment was created once and never
# updated (frozen on its first run, prior-run history lost). Verified live: the bad path
# returns 404, the canonical path returns the comment.
#
# Behavioral testing of the full render path now lives in
# tests/test_cost_report.py (factory_core.cost_report unit + golden tests, #182).
# Since #507 the gh calls themselves live in GitHubTracker (find_comment / update_comment,
# argv parity-tested in tests/test_provider_tracker_parity.py); this file remains a static
# guard for the endpoint bug above: the single-comment endpoint must NOT carry an issue
# number. The list/create endpoint (/issues/{n}/comments, no trailing id) is fine.
#
# Run: bash tests/test_cost_report_endpoint.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
GH_TRACKER="${SCRIPT_DIR}/../scripts/factory_core/providers/tracker/github.py"
FAIL=0

# entrypoint.sh must reach the comment through the provider verbs, not inline gh.
if grep -nE 'gh (api|issue comment)' "$ENTRYPOINT"; then
  echo "  FAIL: entrypoint.sh calls gh for the cost-report comment inline (see above lines)"
  FAIL=1
elif grep -q 'tracker find-comment' "$ENTRYPOINT" && grep -q 'tracker update-comment' "$ENTRYPOINT" \
    && grep -q 'tracker add-comment' "$ENTRYPOINT"; then
  echo "  PASS: entrypoint.sh uses the tracker find/update/add-comment verbs"
else
  echo "  FAIL: entrypoint.sh is missing a tracker find-comment/update-comment/add-comment call"
  FAIL=1
fi

# A single-comment op = the path ends in .../comments/<something>. Such a path must never
# include the issue number segment (issues/{id}/comments/<id>).
if grep -nE 'issues/\{(id|issue_num|ISSUE_NUM)\}/comments/[^"]' "$GH_TRACKER"; then
  echo "  FAIL: single-comment endpoint includes the issue number (404s — see above lines)"
  FAIL=1
else
  echo "  PASS: no issue-number-prefixed single-comment endpoint"
fi

# And the canonical single-comment endpoint should be present (read + PATCH).
CANON=$(grep -cE 'issues/comments/\{comment_id\}' "$GH_TRACKER")
if [ "$CANON" -ge 2 ]; then
  echo "  PASS: canonical /issues/comments/{id} endpoint used (${CANON} occurrences)"
else
  echo "  FAIL: expected >=2 uses of /issues/comments/{id} (read + PATCH), found ${CANON}"
  FAIL=1
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]

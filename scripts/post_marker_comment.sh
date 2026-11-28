#!/usr/bin/env bash
# Post/update an idempotent marker comment on a tracker item, appending the standard
# footer. Extracted from refine-push/plan-push-and-advance (workflows/archon-dark-factory.yaml)
# where the mktemp/printf/tracker-comment/rm sequence was duplicated verbatim between the
# spec-pending-review and plan-pending-review gate-label-failure warnings.
#
# Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]
#   <issue-number>  numeric tracker item id
#   <marker-tag>    idempotency marker, e.g. "<!-- df-gate-label-failure -->" — passed
#                    both as the leading line of the posted body and as the
#                    `tracker comment --marker` value so a re-post updates in place
#                    rather than appending a new comment.
#   <body>          markdown body text, WITHOUT the marker-tag line or footer — both
#                    are added by this script.
#   [footer-kind]   footer to append: a factory_core/identity.py::_MARKERS key, passed
#                    to `factory_core/cli.py marker`, which validates it. Default
#                    `scheduler`. An unknown kind yields an empty footer; the comment
#                    still posts.
#
# The default is `scheduler` (not `refinement`) deliberately: the scheduler's
# elapsed_minutes_since_marker (scheduler_lib.sh) anchors the direct-to-pr grace clock
# on the *last* comment matching the "Refinement Pipeline" marker, so a caller must
# never pass `refinement` for a comment that should not reset the grace clock (issue
# #358 review). `scheduler` and `factory` do not match that pattern.
#
# Exit: propagates the tracker CLI's exit code (non-zero on a failed post/update);
# callers that want this to stay advisory-only append `|| true` at the call site.
set -uo pipefail

ISSUE_NUM="${1:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
MARKER_TAG="${2:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
BODY="${3:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
FOOTER_KIND="${4:-scheduler}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"
_PCLI_FACTORY_CORE="${SCRIPT_DIR}/factory_core/cli.py"

_FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker "$FOOTER_KIND" 2>/dev/null) \
  || { echo "post_marker_comment.sh: unknown footer-kind '$FOOTER_KIND'" >&2; _FOOTER=""; }
_FULL_BODY="${MARKER_TAG}
${BODY}

---
${_FOOTER}"

TMPFILE=$(mktemp /tmp/marker-comment-XXXXXX.md)
printf '%s' "$_FULL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "$MARKER_TAG" --body-file "$TMPFILE"
_RC=$?
rm -f "$TMPFILE"
exit "$_RC"

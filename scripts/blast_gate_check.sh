#!/usr/bin/env bash
# The one deterministic blast-radius checker (#551). Used by the blast-gate DAG node, the
# push-and-pr re-check and the close-preview merge check so the three cannot drift.
# It recomputes the verdict itself: it never reads an agent-written blast.md, and the
# signed-approval lookup lives in gate_blast_radius.py (spec "Match rule").
#
# Usage: blast_gate_check.sh --issue <N> --mode run|close [--out <blast.md>] [--hold]
#   --mode run    head = the clone's HEAD and current branch
#   --mode close  head = the single feat/issue-<N>-* branch on origin, checked out
#                 detached in a temporary worktree
#   --out         where the gate output is written; truncated first, and on an exit-2
#                 failure before the gate printed a STATUS: it gets a fail-closed stub
#                 (STATUS: HUMAN_REQUIRED, TRIGGER: checker-error, REASON: ...)
#   --hold        on a non-zero result, take the hold steps (spec C.1-C.5 for run,
#                 F.3 for close) before exiting. In run mode the hold marker goes to
#                 $FACTORY_BLAST_HOLD_FILE -- exported by entrypoint.sh, because Archon
#                 gives every bash node its own $ARTIFACTS_DIR -- falling back to
#                 $ARTIFACTS_DIR/blast-hold.json when unset (local runs).
#
# stdout: exactly one `HEAD_SHA=<sha>` line once the head is resolved (close-preview
#   pins the merge to it). Diagnostics and every hold-step call's output go to stderr.
# Exit 0 only when STATUS: is exactly PASS or SKIPPED. Exit 1 for HUMAN_REQUIRED. Exit 2
#   for anything else (empty output, crash, failed fetch, non-numeric issue, ambiguous
#   branch). ERROR is not a pass value: the gate never emits it. Callers treat 1 and 2
#   both as "stop" -- do not wrap this call in `|| true`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"
TRUSTED_REF="refs/blast-gate/trusted-main"
HEAD_REF="refs/blast-gate/head"
HOLD_MARKER="<!-- df-blast-hold -->"

ISSUE="" MODE="" OUT="" HOLD=false PR_NUM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --issue) ISSUE="${2:-}"; shift 2 ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --hold) HOLD=true; shift ;;
    # Close mode only: the PR's headRefName, when the caller (close-preview) already
    # resolved it, is more reliable than the feat/issue-<N>-* glob -- a stale branch from
    # an earlier attempt would otherwise make the glob ambiguous and fail closed for no
    # real reason. The glob stays as a fallback when --pr is absent or the lookup fails.
    --pr) PR_NUM="${2:-}"; shift 2 ;;
    *) echo "blast_gate_check.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

CLONE="${CLONE_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
APPROVALS_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals"
# Explicit URL, not the named "origin" remote's fetch direction: .git/config is writable by
# earlier agent phases (validate/conformance run with repo write access), so a remote whose
# `url` points at an attacker repo while `pushurl` still points at the real one would make
# that attacker repo's main and .factory/blast-approvers.asc the trusted base/key, yet pushes
# (and the PR they open) would still land on the real repo -- a convincing false PASS. The
# attack needs pushurl to still be correct (otherwise nothing real ever lands), so resolving
# the push URL and fetching/pushing through that -- instead of the bare remote name -- holds
# when url and pushurl have been split. It does not hold against url.*.insteadOf or
# refs/replace/* planted in the same clone: in-run tampering by a hostile agent is out of
# this check's reach (spec, known limits); the close-time check in a fresh clone is the
# enforcement point.
TRUSTED_URL="$(git -C "$CLONE" remote get-url --push origin 2>/dev/null)"
BRANCH="" HEAD_SHA="" REASON="" RESULT="" WT=""

_cleanup() {
  if [ -n "$WT" ]; then
    git -C "$CLONE" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
  fi
  [ -n "${RESULT:-}" ] && rm -f "$RESULT"
  [ -n "${HOT_TMP:-}" ] && rm -f "$HOT_TMP"
}
trap _cleanup EXIT

_fail() { REASON="$1"; echo "blast_gate_check.sh: $1" >&2; }

_check() {
  # Never leave an earlier (agent-written) blast.md in place: --out always ends up holding
  # this run's verdict, or the fail-closed stub written below.
  [ -n "$OUT" ] && : > "$OUT"
  case "$ISSUE" in ''|*[!0-9]*) _fail "issue number '${ISSUE}' is not numeric"; return 2 ;; esac
  case "$MODE" in run|close) ;; *) _fail "mode must be run or close, got '${MODE}'"; return 2 ;; esac
  [ -n "${FACTORY_REPO_SLUG:-}" ] || { _fail "FACTORY_REPO_SLUG is not set"; return 2; }

  local wt_dir hot
  if [ "$MODE" = "run" ]; then
    # Resolved before the fetch so a hold after a failed fetch still knows what to push.
    BRANCH=$(git -C "$CLONE" branch --show-current 2>/dev/null)
    HEAD_SHA=$(git -C "$CLONE" rev-parse --verify -q HEAD 2>/dev/null)
    [ -n "$BRANCH" ] && [ -n "$HEAD_SHA" ] || { _fail "no current branch/HEAD in ${CLONE}"; return 2; }
  fi

  # Trusted base: created here, every time, never the clone's own main/origin/main, and
  # fetched from an explicit URL rather than the "origin" remote (see TRUSTED_URL above).
  if ! timeout 60 git -C "$CLONE" fetch --no-tags --quiet "$TRUSTED_URL" \
      "+refs/heads/main:${TRUSTED_REF}"; then
    _fail "could not fetch origin main into ${TRUSTED_REF}"; return 2
  fi

  if [ "$MODE" = "run" ]; then
    wt_dir="$CLONE"
  else
    local heads count pr_branch=""
    # Prefer the PR's own headRefName when the caller already knows PR_NUM: exact and
    # unambiguous even when a stale feat/issue-<N>-* branch from an earlier attempt is
    # still on origin. Falls back to the glob below on any lookup failure.
    if [ -n "$PR_NUM" ]; then
      pr_branch=$(timeout 15 gh pr view "$PR_NUM" --repo "$FACTORY_REPO_SLUG" \
        --json headRefName -q .headRefName 2>/dev/null || true)
    fi
    if [ -n "$pr_branch" ]; then
      BRANCH="$pr_branch"
    else
      heads=$(timeout 30 git -C "$CLONE" ls-remote --heads origin "refs/heads/feat/issue-${ISSUE}-*" 2>/dev/null \
        | awk -v p="refs/heads/feat/issue-${ISSUE}-" 'index($2, p) == 1 {print $2}')
      count=$(printf '%s' "$heads" | grep -c . || true)
      [ "$count" -eq 1 ] || { _fail "expected exactly one feat/issue-${ISSUE}-* branch on origin, found ${count}"; return 2; }
      BRANCH="${heads#refs/heads/}"
    fi
    if ! timeout 60 git -C "$CLONE" fetch --no-tags --quiet origin "+refs/heads/${BRANCH}:${HEAD_REF}"; then
      _fail "could not fetch ${BRANCH}"; return 2
    fi
    HEAD_SHA=$(git -C "$CLONE" rev-parse --verify -q "${HEAD_REF}^{commit}")
    WT=$(mktemp -d /tmp/blast-gate-wt-XXXXXX)
    rmdir "$WT"
    git -C "$CLONE" worktree add --detach --quiet "$WT" "$HEAD_SHA" >/dev/null 2>&1 \
      || { WT=""; _fail "could not check out ${HEAD_SHA} in a worktree"; return 2; }
    wt_dir="$WT"
  fi
  # Hotspot membership feeds classify_file. Trusted main's report always counts. In run
  # mode the working tree's report counts as well (spec B.4: "as today, no change to what
  # triggers in-run") -- the run regenerates it, so a file this branch has just made a
  # hotspot is only listed there. Reading main alone in run mode let such a file pass
  # where validate Phase 0 used to hold (operator review of PR #571). The union can only
  # add a trigger, never remove one.
  HOT_TMP=$(mktemp /tmp/blast-gate-hot-XXXXXX)
  git -C "$CLONE" show "${TRUSTED_REF}:docs/codeindex-hotspots.md" > "$HOT_TMP" 2>/dev/null || : > "$HOT_TMP"
  if [ "$MODE" = "run" ] && [ -f "$CLONE/docs/codeindex-hotspots.md" ]; then
    { echo; cat "$CLONE/docs/codeindex-hotspots.md"; } >> "$HOT_TMP" 2>/dev/null ||
      { _fail "could not read the working-tree hotspot report"; return 2; }
  fi
  hot="$HOT_TMP"
  echo "HEAD_SHA=${HEAD_SHA}"

  local base changed shortstat added deleted lines gate
  base=$(git -C "$wt_dir" merge-base "$TRUSTED_REF" "$HEAD_SHA" 2>/dev/null) \
    || { _fail "no merge-base between ${TRUSTED_REF} and ${HEAD_SHA}"; return 2; }
  # Three-dot semantics, as validate Phase 0 always computed them.
  # core.quotePath=false: a non-ASCII path is otherwise C-quoted and no anchored trigger
  # pattern matches it. --no-renames: with rename detection only the destination is
  # listed, so moving a triggered file out of its directory hid it (operator review of
  # PR #571).
  changed=$(git -C "$wt_dir" -c core.quotePath=false diff --no-renames "$base" "$HEAD_SHA" --name-only 2>/dev/null) \
    || { _fail "git diff --name-only failed"; return 2; }
  # git still C-quotes a name holding a tab, newline, double quote, backslash or control
  # character; such a line matches no trigger pattern and cannot be bound to a blob by
  # name. Fail closed rather than judge a path the gate cannot read.
  if printf '%s\n' "$changed" | grep -q '^"'; then
    _fail "a changed file name needs quoting (tab, newline, quote, backslash or control character)"
    return 2
  fi
  shortstat=$(git -C "$wt_dir" diff "$base" "$HEAD_SHA" --shortstat 2>/dev/null) \
    || { _fail "git diff --shortstat failed"; return 2; }
  added=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)
  deleted=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)
  lines=$((added + deleted))

  # The baked gate decides; the clone copy only when the image has none (validate.md F9).
  gate="${BLAST_GATE_BAKED_DIR:-/opt/dark-factory/scripts}/gate_blast_radius.py"
  [ -f "$gate" ] || gate="${SCRIPT_DIR}/gate_blast_radius.py"
  RESULT=$(mktemp /tmp/blast-gate-out-XXXXXX)
  printf '%s\n' "$changed" | python3 "$gate" \
    --changed-files-stdin \
    --lines-changed "$lines" \
    --hotspots "$hot" \
    --config .claude/skills/refinement/config.yaml \
    --clone-dir "$wt_dir" \
    --base-ref "$base" \
    --issue "$ISSUE" \
    --repo "$FACTORY_REPO_SLUG" \
    --branch "$BRANCH" \
    --trusted-ref "$TRUSTED_REF" \
    --head-ref "$HEAD_SHA" \
    --approvals-dir "$APPROVALS_DIR" > "$RESULT"
  [ -n "$OUT" ] && cp "$RESULT" "$OUT"

  local status
  status=$(grep -m1 '^STATUS:' "$RESULT" | awk '{print $2}')
  case "$status" in
    PASS|SKIPPED) return 0 ;;
    HUMAN_REQUIRED) return 1 ;;
    *) _fail "gate produced no usable STATUS (got '${status:-<none>}')"; return 2 ;;
  esac
}

# --- Hold steps (only with --hold, only after a non-zero result) ---------------------

_field() { [ -n "${RESULT:-}" ] && [ -f "$RESULT" ] && sed -n "s/^$1: //p" "$RESULT" | head -1; }

_push_branch() {
  # Verified push without a PR (refine-push pattern, workflows/archon-dark-factory.yaml).
  PUSH_NOTE=""
  [ -n "$BRANCH" ] && [ -n "$HEAD_SHA" ] || { PUSH_NOTE="no branch to push"; return 1; }
  # BENCH_MODE=stub never pushes (push-and-pr's own contract).
  [ "${BENCH_MODE:-}" = "stub" ] && { PUSH_NOTE="BENCH_MODE=stub: not pushed"; return 1; }
  local err rc remote
  err=$(timeout 30 git -C "$CLONE" push "$TRUSTED_URL" "$BRANCH" 2>&1); rc=$?
  remote=$(timeout 10 git -C "$CLONE" ls-remote --exit-code --heads "$TRUSTED_URL" "refs/heads/$BRANCH" 2>/dev/null \
    | awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}')
  if [ -n "$remote" ] && [ "$remote" = "$HEAD_SHA" ]; then
    return 0
  fi
  [ "$rc" -eq 124 ] && err="push timed out after 30s"
  PUSH_NOTE=$(printf '%s' "${err:-origin has ${remote:-nothing}, expected $HEAD_SHA}" | tail -n 5 | tail -c 600 | iconv -c -f UTF-8 -t UTF-8)
  return 1
}

_write_request() {
  local paths tmp
  paths=$(_field TRIGGERED_PATHS)
  printf '%s' "${paths:-[]}" | jq -e 'type == "array"' >/dev/null 2>&1 || paths='[]'
  mkdir -p -m 0755 "$APPROVALS_DIR" 2>/dev/null || return 1
  tmp=$(mktemp "$APPROVALS_DIR/.${ISSUE}.request.XXXXXX") || return 1
  jq -cn --argjson issue "$ISSUE" --arg repo "${FACTORY_REPO_SLUG:-}" --arg branch "$BRANCH" \
    --arg head "$HEAD_SHA" --arg mode "$MODE" --argjson paths "$paths" \
    --arg at "$(date -u +%FT%TZ)" \
    '{schema: 1, issue: $issue, repo: $repo, branch: $branch, head_sha: $head, mode: $mode,
      triggered_paths: $paths, written_at: $at}' > "$tmp" \
    && chmod 0644 "$tmp" && mv -f "$tmp" "$APPROVALS_DIR/${ISSUE}.request.json" \
    || { rm -f "$tmp"; return 1; }
}

_hold() {
  local rc="$1" pushed=true where body files branch_line marker_ok=true
  case "$ISSUE" in ''|*[!0-9]*) echo "blast_gate_check.sh: cannot hold a non-numeric issue" >&2; return ;; esac

  # The marker records the decision, so it is written first: a node timeout in any later
  # step must still leave a hold, not an ordinary failure (spec C.5; steps are idempotent).
  local marker="${FACTORY_BLAST_HOLD_FILE:-${ARTIFACTS_DIR:+$ARTIFACTS_DIR/blast-hold.json}}"
  if [ "$MODE" = "run" ] && [ -n "$marker" ]; then
    jq -cn --argjson issue "$ISSUE" --arg head "$HEAD_SHA" --argjson exit "$rc" \
      '{issue: $issue, head_sha: $head, exit: $exit}' > "$marker" \
      || echo "blast_gate_check.sh: WARNING: could not write ${marker}" >&2
  fi

  if [ "$MODE" = "run" ]; then
    _push_branch || { pushed=false; marker_ok=false; }
  fi
  _write_request || echo "blast_gate_check.sh: WARNING: could not write ${APPROVALS_DIR}/${ISSUE}.request.json" >&2
  python3 "$_PCLI" tracker label --id "$ISSUE" --add needs-discussion >&2 \
    || { echo "blast_gate_check.sh: WARNING: could not add needs-discussion to #${ISSUE}" >&2; marker_ok=false; }
  if [ "$MODE" = "run" ]; then
    python3 "$_PCLI" tracker set-status --id "$ISSUE" --status blocked >&2 \
      || echo "blast_gate_check.sh: WARNING: could not set #${ISSUE} to Blocked" >&2
  fi

  # A push rejection (e.g. missing `workflow` scope) or a failed label leaves nothing a
  # human can act on: the clone's commits are unreviewable (not on origin), or the tracker
  # never actually shows needs-discussion. Remove the marker so entrypoint.sh's
  # _handle_blast_hold does not swallow this run as a hold -- it falls through to the
  # normal failure path (error signature, breaker accounting) instead.
  if [ "$MODE" = "run" ] && [ "$marker_ok" = false ] && [ -n "$marker" ]; then
    rm -f "$marker" 2>/dev/null || true
  fi

  if [ "$MODE" = "run" ]; then
    where="This run stops here: no PR is opened and nothing is merged."
  else
    where="Factory Close refused to merge the PR."
  fi
  if [ "$pushed" = true ] || [ "$MODE" = "close" ]; then
    branch_line="**Branch:** \`${BRANCH:-<unknown>}\` (on origin)"
  else
    branch_line="**Branch:** \`${BRANCH:-<unknown>}\` — **not on origin** (${PUSH_NOTE}). There is nothing to review until a run pushes it."
  fi
  files=$(_field_block)
  if [ "$rc" -eq 2 ]; then
    body="## Blast-Radius Gate — Held (checker failed)

The deterministic blast-radius checker could not produce a verdict, so the change is held (fail closed). ${where}

**Reason:** ${REASON:-unknown}
${branch_line}
**Head:** \`${HEAD_SHA:-<unknown>}\`

Fix the cause above, then remove \`needs-discussion\` to let the scheduler retry."
  else
    body="## Blast-Radius Gate — Held for human review

This change touches paths that need a human sign-off. ${where}

${branch_line}
**Head:** \`${HEAD_SHA}\`
**Trigger:** $(_field TRIGGER)
**Approval:** $(_field APPROVAL)

**Triggered files:**
${files}

### To approve

Review the diff of the triggered files on \`${BRANCH}\` at \`${HEAD_SHA}\`, then on the operator host run:

\`\`\`bash
approve-blast.sh ${ISSUE} --sha ${HEAD_SHA}
\`\`\`

It writes a signed approval bound to exactly this content and lifts \`needs-discussion\`; the next run passes the gate. A \`stale (...)\` approval above means only the named file changed since the last approval — review that delta.

Removing \`needs-discussion\` by hand does nothing useful: the next run recomputes the gate and holds again."
  fi
  bash "${SCRIPT_DIR}/post_marker_comment.sh" "$ISSUE" "$HOLD_MARKER" "$body" >&2 \
    || echo "blast_gate_check.sh: WARNING: could not post the hold comment on #${ISSUE}" >&2
}

_field_block() {
  [ -n "${RESULT:-}" ] && [ -f "$RESULT" ] || return 0
  sed -n '/^TRIGGERED_FILES:$/,/^LINES_CHANGED:/p' "$RESULT" | grep '^  - ' | head -20
}

_check
RC=$?
if [ "$RC" -eq 2 ] && [ -n "$OUT" ] && ! grep -q '^STATUS:' "$OUT" 2>/dev/null; then
  # The gate never ran (or printed nothing): record the fail-closed hold, never a pass.
  printf 'STATUS: HUMAN_REQUIRED\nGATE_TYPE: blast\nFINDINGS_COUNT: 0\nSEVERITY: critical\n---\nTRIGGER: checker-error\nREASON: %s\n' \
    "$(printf '%s' "${REASON:-unknown}" | tr '\n' ' ')" > "$OUT" 2>/dev/null || true
fi
if [ "$RC" -ne 0 ] && [ "$HOLD" = true ]; then
  _hold "$RC"
fi
exit "$RC"

#!/usr/bin/env bash
set -euo pipefail

# --- Instance identity (env-overridable; defaults = MarketHawk parity) ---
IDENTITY_SH="${IDENTITY_SH:-/opt/dark-factory/scripts/identity.sh}"
source "$IDENTITY_SH"

# --- Validate required environment (provider-aware; parent spec §4) ---
FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-/opt/dark-factory/scripts/factory_core/providers/cli.py}"
python3 "$FACTORY_PROVIDERS_CLI" preflight

# --- Configuration ---
REPO_URL="https://${GH_TOKEN}@github.com/${FACTORY_REPO_SLUG}.git"
CLONE_DIR="$FACTORY_CLONE_DIR"
FACTORY_NAME="${FACTORY_PRODUCT_NAME} Factory"
FACTORY_EMAIL="factory@${FACTORY_REPO}"

# --- Git identity ---
git config --global user.name "$FACTORY_NAME"
git config --global user.email "$FACTORY_EMAIL"

# --- GitHub CLI auth (GH_TOKEN env var is auto-detected by gh) ---
echo "GitHub auth: $(gh auth status 2>&1 | head -2 | tail -1 || echo 'using GH_TOKEN env var')"

# --- Project board constants (provided by identity.sh above) ---

# Bootstrap defaults for pre-clone concurrency guard — overridden by _entrypoint_cfg_apply post-clone.
FACTORY_WIP_LIMIT="${FACTORY_WIP_LIMIT:-1}"
CONFLICT_RESOLUTION_AI_TIER="${CONFLICT_RESOLUTION_AI_TIER:-true}"
SCHEDULER_STATE_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
SESSION_WINDOW_BACKOFF_ENABLED="${SESSION_WINDOW_BACKOFF_ENABLED:-true}"
SESSION_WINDOW_BUFFER_MINUTES="${SESSION_WINDOW_BUFFER_MINUTES:-5}"
SESSION_WINDOW_FALLBACK_MINUTES="${SESSION_WINDOW_FALLBACK_MINUTES:-30}"
# env-only by design, mirroring REFINE_MAX_RETRIES (scheduler.sh:17) — not threaded through
# _entrypoint_cfg_apply()/config.yaml; see Task 3's callout in the #33 plan for why.
DELIVERY_FAILURE_MAX_SECONDS="${DELIVERY_FAILURE_MAX_SECONDS:-30}"

# Read FACTORY_WIP_LIMIT and CONFLICT_RESOLUTION_AI_TIER from config.yaml post-clone.
# Env overrides are kept and logged; bootstrap defaults above handle pre-clone use.
_entrypoint_cfg_apply() {
  local cfg
  for cfg in "${CLONE_DIR}/.claude/skills/refinement/config.yaml" "/opt/refinement-skills/config.yaml"; do
    [ -f "$cfg" ] && break
    cfg=""
  done
  if [ -z "$cfg" ]; then
    echo "WARNING: config.yaml not found post-clone — keeping bootstrap defaults" >&2
    return 0
  fi

  _epcfg() {
    local var="$1" yq_expr="$2"
    local cfg_val
    cfg_val=$(yq "$yq_expr" "$cfg" 2>/dev/null || true)
    [ "${cfg_val:-null}" = "null" ] && return 0
    if [ "${!var}" != "$cfg_val" ]; then
      echo "[entrypoint-config] ${var}=${!var} (env/bootstrap override; config has '${cfg_val}')" >&2
    else
      export "${var}=${cfg_val}"
    fi
  }

  _epcfg FACTORY_WIP_LIMIT          '.scheduler.factory_wip_limit'
  _epcfg CONFLICT_RESOLUTION_AI_TIER '.conflict_resolution.ai_tier'
  _epcfg SESSION_WINDOW_BACKOFF_ENABLED   '.scheduler.session_window_backoff_enabled'
  _epcfg SESSION_WINDOW_BUFFER_MINUTES    '.scheduler.session_window_buffer_minutes'
  _epcfg SESSION_WINDOW_FALLBACK_MINUTES  '.scheduler.session_window_fallback_minutes'
  echo "[entrypoint-config] loaded from ${cfg}"
}

# --- Parse arguments ---
ARGUMENTS="${*}"
if [ -z "$ARGUMENTS" ] && [ "${ENTRYPOINT_SOURCE_ONLY:-0}" != "1" ]; then
  echo "Usage: docker compose --profile factory run --rm dark-factory \"Fix issue #3\""
  echo "       docker compose --profile factory run --rm dark-factory \"Continue issue #3\""
  echo "       docker compose --profile factory run --rm dark-factory \"Close issue #3\""
  echo "       docker compose --profile factory run --rm dark-factory \"Refine issue #3\""
  echo "       docker compose --profile factory run --rm dark-factory \"Plan issue #3\""
  echo "       docker compose --profile factory run --rm dark-factory \"Recheck main\""
  exit 1
fi

# --- Extract issue number and intent immediately (no AI needed) ---
# "Recheck main" carries no "#N" — the || true keeps the no-match grep (exit 1)
# from killing the script under set -euo pipefail.
ISSUE_NUM=$(echo "$ARGUMENTS" | grep -oP '#\K\d+' | head -1 || true)
INTENT=$(echo "$ARGUMENTS" | grep -oiP '^\s*\K(fix|continue|close|refine|plan|deconflict|recheck)' | head -1 | tr '[:upper:]' '[:lower:]' || true)
INTENT=${INTENT:-fix}
case "$ARGUMENTS" in
  "Fix main"|"fix main") INTENT="fix-main" ;;
esac

# --- Canonical run identity and artifact directory ---
# ARCHON_RUN_ID is not set by archon; always generate a UUID for correlation.
RUN_ID=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ARTIFACTS_DIR="${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs/${RUN_ID}"
export ARTIFACTS_DIR
mkdir -p "$ARTIFACTS_DIR"

# --- Model-proxy correlation pointer (best-effort; consumed by factory-model-proxy
# when FACTORY_MODEL_PROXY_ENABLED — see model_proxy.py's read_current_run()). Written
# unconditionally and cheaply; the proxy is a no-op reader when disabled.
#
# RUN_STAGE: single-phase intents (refine/plan/deconflict/close/fix-main/recheck) map
# 1:1 to the phase the whole container run performs, so the ledger can attribute every
# request in the run to that exact stage. Multi-phase intents (fix/continue traverse
# implement -> conformance -> code-review -> merge inside one container run) cannot be
# placed this way — investigated during planning: neither `archon workflow get --json`
# nor `archon workflow runs --json` expose per-node/per-step timestamps for this
# workflow's node style, so "unknown" is the honest, investigated answer for those two
# intents, not an assumption. ---
case "${INTENT:-unknown}" in
  refine|plan|deconflict|close|fix-main|recheck) RUN_STAGE="${INTENT}" ;;
  *) RUN_STAGE="unknown" ;;
esac
CURRENT_RUN_DIR="${CURRENT_RUN_DIR:-/var/lib/dark-factory}"
mkdir -p "$CURRENT_RUN_DIR" 2>/dev/null || true
printf '{"run_id":"%s","issue_number":%s,"intent":"%s","stage":"%s","started_at":"%s"}\n' \
  "$RUN_ID" "${ISSUE_NUM:-0}" "${INTENT:-unknown}" "$RUN_STAGE" "$RUN_STARTED_AT" \
  > "$CURRENT_RUN_DIR/current-run.json" 2>/dev/null || true

# --- Concurrency guard: cap factory containers at FACTORY_WIP_LIMIT ---
# RUNNING counts OTHER run containers (self excluded), so at-capacity is
# RUNNING >= limit. Must stay in sync with the scheduler's capacity guard —
# the scheduler dispatches into free slots and this backstop must not veto
# them (#347). The var arrives via the service env_file (.archon/.env).
FACTORY_WIP_LIMIT="${FACTORY_WIP_LIMIT:-1}"
MY_ID=$(cat /proc/self/cgroup 2>/dev/null | grep -oP '[a-f0-9]{64}' | head -1 || hostname)
RUNNING=$(docker ps --format '{{.ID}} {{.Names}}' 2>/dev/null \
  | grep "${FACTORY_RUN_PREFIX}" \
  | grep -vc "${MY_ID:0:12}" || true)
RUNNING=${RUNNING:-0}
if [ "$RUNNING" -ge "$FACTORY_WIP_LIMIT" ]; then
  echo "ERROR: ${RUNNING} other dark factory container(s) already running — at FACTORY_WIP_LIMIT=${FACTORY_WIP_LIMIT}." >&2
  echo "       Use 'docker ps --filter name=dark-factory' to see them." >&2
  exit 1
fi

# --- Helper: move issue to a board status (canonical name: ready|in_progress|in_review|
# blocked|done|backlog|refined) — thin adapter over factory_core.providers' Tracker,
# replacing the bash-native item-list/item-edit reimplementation of board.py's logic.
# Runs pre-clone (the very first call below fires before "git clone" at line ~497), so
# it always uses the baked /opt copy, never $CLONE_DIR — see the TARGET-PATH convention
# used post-clone elsewhere in this file.
set_board_status() {
  python3 /opt/dark-factory/scripts/factory_core/providers/cli.py \
    tracker set-status --id "$ISSUE_NUM" --status "$1"
}

# --- Move to "In Progress" immediately (skip for close) ---
if [ -n "$ISSUE_NUM" ] && [ "$INTENT" != "close" ] && [ "$INTENT" != "refine" ] && [ "$INTENT" != "plan" ] && [ "$INTENT" != "deconflict" ]; then
  echo "Moving issue #$ISSUE_NUM to In Progress..."
  set_board_status "in_progress" || echo "WARNING: Could not update project board"
fi

# --- Helper: post or update cost report on issue ---
COST_MARKER="<!-- dark-factory-cost-report -->"
REFINE_FAILURE_MARKER="<!-- df-refine-failure -->"
FACTORY_FAILURE_MARKER="<!-- df-factory-failure -->"
DF_POST_MORTEM_MARKER="<!-- df-post-mortem -->"

# Idempotent marker-comment upsert — thin adapter over factory_core.providers' Tracker
# (find-by-marker + PATCH/create, same semantics as board.py's post_or_update_comment).
# Uses the baked /opt copy, matching set_board_status above: on_failure (this function's
# main caller) is reachable via the ERR trap before "git clone" ever completes.
post_or_update_comment() {
  local marker="$1"
  local body="$2"
  local TMPFILE
  TMPFILE=$(mktemp /tmp/failure-comment-XXXXXX.md)
  echo "$body" > "$TMPFILE"
  python3 /opt/dark-factory/scripts/factory_core/providers/cli.py \
    tracker comment --id "$ISSUE_NUM" --marker "$marker" --body-file "$TMPFILE" 2>/dev/null || true
  rm -f "$TMPFILE"
}

run_post_mortem() {
  local exit_code="${1:-1}"
  local transcript_file="${2:-}"

  case "${INTENT:-fix}" in
    refine|plan|deconflict) return 0 ;;
  esac

  [ -z "${ISSUE_NUM:-}" ] && return 0

  local ARTIFACTS_BASE_DIR="${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs"

  local prompt
  prompt=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" post-mortem-gather \
    --artifacts-base "$ARTIFACTS_BASE_DIR" \
    --issue "$ISSUE_NUM" \
    --transcript-file "$transcript_file" \
    --exit-code "$exit_code" \
    --intent "${INTENT:-fix}")

  local post_mortem_text
  post_mortem_text=$(echo "$prompt" | claude -p --model claude-haiku-4-5-20251001 2>/dev/null || true)

  if [ -z "$post_mortem_text" ]; then
    post_mortem_text="Post-mortem generation failed — no output from haiku agent. Exit code was ${exit_code}. Check the factory logs for details."
  fi

  local PROMOTED_AT TEXTFILE
  PROMOTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  TEXTFILE=$(mktemp /tmp/postmortem-text-XXXXXX)
  echo "$post_mortem_text" > "$TEXTFILE"

  local title_json title
  title_json=$(python3 /opt/dark-factory/scripts/factory_core/providers/cli.py \
    tracker get --id "${ISSUE_NUM}" --fields title 2>/dev/null || echo '{}')
  title=$(echo "$title_json" | jq -r '.title // ""')

  local comment_body
  comment_body=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" post-mortem-format \
    --exit-code "$exit_code" \
    --intent "${INTENT:-fix}" \
    --promoted-at "$PROMOTED_AT" \
    --text-file "$TEXTFILE" \
    --issue "$ISSUE_NUM" \
    --title "$title" \
    --artifacts-dir "${ARTIFACTS_DIR:-}" \
    --product-name "${FACTORY_PRODUCT_NAME:-Dark Factory}" || true)
  rm -f "$TEXTFILE"

  post_or_update_comment "$DF_POST_MORTEM_MARKER" "$comment_body" || true
}

# Detects a session-window exhaustion in the captured run output via the Python
# session_window module (structured claude.rate_limit_event preferred, substring/regex
# fallback otherwise). On match: writes the shared pause sentinel, records a distinct
# "paused" run-record entry, and returns 0 so the caller exits clean WITHOUT flowing
# through on_failure or the success-path record assembly. Returns 1 (kill-switch off,
# or no match at all) so the caller falls through to the existing failure/sleep paths.
_handle_session_window_pause() {
  local tmp_out="$1"
  [ "${SESSION_WINDOW_BACKOFF_ENABLED:-true}" = "true" ] || return 1

  local sw_result sw_rc
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
    --tmp-out "$tmp_out" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
    --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" 2>&1)
  sw_rc=$?
  if [ "$sw_rc" -ne 0 ]; then
    echo "WARNING: session-window-check failed (exit ${sw_rc}) — path/import likely broken, falling through to legacy detection: ${sw_result}" >&2
    return 1
  fi

  local matched resume_epoch
  matched=$(echo "$sw_result" | grep -o 'matched=[a-z]*' | cut -d= -f2)
  resume_epoch=$(echo "$sw_result" | grep -o 'resume_epoch=[0-9]*' | cut -d= -f2)
  [ "$matched" = "true" ] || return 1

  local resume_iso
  resume_iso=$(date -u -d "@${resume_epoch}" +%FT%TZ 2>/dev/null || echo "unknown")
  echo "session-window exhausted — dispatch paused until ${resume_iso}"
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
    --run-id "${RUN_ID:-unknown}" \
    --issue "${ISSUE_NUM:-0}" \
    --intent "${INTENT:-unknown}" \
    --stage paused \
    --verdict paused || true
  return 0
}

# Maps the container's INTENT to the phase string the scheduler's retry keys and
# trip_to_blocked() already use (_make_key in factory_core/breaker.py): "resolve" for
# deconflict (not "deconflict" — matches the existing scheduler.sh call sites), "refine"
# and "plan" pass through unchanged, everything else (fix/continue/recheck/fix-main) maps
# to "implement" (the bare-issue-number key).
_failure_phase_for_intent() {
  case "${INTENT:-fix}" in
    refine) echo "refine" ;;
    plan) echo "plan" ;;
    deconflict) echo "resolve" ;;
    *) echo "implement" ;;
  esac
}

# Classifies the current failure and drops the signature file the scheduler reads back on
# its next poll (mirrors _handle_session_window_pause's sentinel-file pattern). Called from
# two places: (1) the main archon-workflow loop's real-failure branch, with the captured
# transcript — the functionally load-bearing call, since that is where a real task failure
# (test_failure, build_failure, oos_files) is actually observed; (2) both branches of
# on_failure() (the ERR trap), with no transcript, covering early/setup-phase crashes before
# the main loop ever runs — these classify as environmental:delivery_failure by construction
# (fast, zero commits, no artifact), which is the correct, conservative outcome.
_write_error_signature() {
  local phase="$1" exit_code="$2" text_file="${3:-}"
  [ -z "${ISSUE_NUM:-}" ] && return 0
  local elapsed_seconds=0
  if [ -n "${RUN_STARTED_AT:-}" ]; then
    local started_epoch now_epoch
    # Guard the parse: a failure here must not fall back to a 0 epoch, which would
    # make elapsed_seconds ~now() (billions of seconds) and permanently fail the
    # classifier's "elapsed_seconds < delivery_failure_max_seconds" check — silently
    # turning a genuine fast delivery-failure into a false substantive:unknown signal
    # (#33 review). Skip elapsed-based classification instead: leave elapsed_seconds
    # at its 0 default so the other signals (commits/dirty/artifact) decide.
    if started_epoch=$(date -u -d "$RUN_STARTED_AT" +%s 2>/dev/null); then
      now_epoch=$(date -u +%s)
      elapsed_seconds=$((now_epoch - started_epoch))
    else
      echo "WARNING: could not parse RUN_STARTED_AT='${RUN_STARTED_AT}' — skipping elapsed-based failure classification" >&2
    fi
  fi
  local commits_since_start=0
  if [ -n "${RUN_STARTED_AT:-}" ]; then
    commits_since_start=$(git log --oneline --since="$RUN_STARTED_AT" HEAD 2>/dev/null | wc -l | tr -d ' ')
  fi
  local dirty_flag="" artifact_flag=""
  [ -n "$(git status --porcelain 2>/dev/null)" ] && dirty_flag="--worktree-dirty"
  # Allowlist of genuine phase-deliverable filenames, not a denylist of
  # run-record.json alone: $ARTIFACTS_DIR always already contains workflow-written
  # context artifacts (issue.json, context-budget.json, token-opt-caps.env — present
  # before the phase command even starts) and, for implement-intent failures,
  # run_post_mortem() unconditionally writes factory-failures.jsonl (line 250) before
  # this helper runs. A denylist of only run-record.json would treat all of those as
  # "real work happened," making artifact_present effectively always true and the
  # delivery_failure conjunction unreachable — defeating the #279 carve-out this
  # spec exists to add. The list below reuses run_post_mortem's own known-deliverable
  # set (line 201: implementation.md conformance.md review.md plan.md) plus the
  # refine/plan/deconflict/Task-7 deliverables.
  if [ -n "${ARTIFACTS_DIR:-}" ]; then
    for f in implementation.md conformance.md review.md plan.md \
             refinement-status.md conflict_resolution.md \
             failure-diagnosis.md out-of-scope.md; do
      if [ -f "${ARTIFACTS_DIR}/${f}" ]; then
        artifact_flag="--artifact-present"
        break
      fi
    done
  fi
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
  # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" error-signature-write \
    --issue "$ISSUE_NUM" \
    --phase "$phase" \
    --exit-code "$exit_code" \
    --text-file "${text_file:-}" \
    --elapsed-seconds "$elapsed_seconds" \
    --commits-since-start "$commits_since_start" \
    $dirty_flag $artifact_flag \
    --delivery-failure-max-seconds "${DELIVERY_FAILURE_MAX_SECONDS:-30}" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    2>/dev/null || true
}

post_cost_report() {
  if [ -z "${ISSUE_NUM:-}" ]; then return; fi
  local RUN_RECORD_FILE="${ARTIFACTS_DIR:-}/run-record.json"
  if [ ! -f "$RUN_RECORD_FILE" ]; then return; fi

  echo "Posting cost report to issue #${ISSUE_NUM}..."

  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  if ! python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report-check \
      --run-record-file "$RUN_RECORD_FILE" \
      --run-id "${RUN_ID:-unknown}" \
      --issue "${ISSUE_NUM}"; then
    return
  fi

  # Find existing cost report comment by marker
  local COMMENT_ID
  COMMENT_ID=$(gh api "repos/${FACTORY_REPO_SLUG}/issues/${ISSUE_NUM}/comments" \
    --jq "[.[] | select(.body | contains(\"$COST_MARKER\"))] | last | .id // empty" 2>/dev/null || true)

  local PRIOR_BODY_FILE=""
  if [ -n "$COMMENT_ID" ]; then
    PRIOR_BODY_FILE=$(mktemp /tmp/prior-body-XXXXXX.md)
    # Single-comment endpoint omits the issue number: /issues/comments/{id}, NOT
    # /issues/{n}/comments/{id} (the latter 404s).
    gh api "repos/${FACTORY_REPO_SLUG}/issues/comments/${COMMENT_ID}" \
      --jq '.body' > "$PRIOR_BODY_FILE" 2>/dev/null || true
  fi

  local TIMESTAMP
  TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")

  # --intent/--product-name/--budget-file: see "Deviations from the spec" items
  # 1-2 above — render() cannot derive these from run-record.json alone.
  local BUDGET_FILE="${ARTIFACTS_DIR:-}/context-budget.json"
  local BUDGET_ARGS=()
  [ -f "$BUDGET_FILE" ] && BUDGET_ARGS=(--budget-file "$BUDGET_FILE")
  local PRIOR_ARGS=()
  [ -n "$PRIOR_BODY_FILE" ] && PRIOR_ARGS=(--prior-body-file "$PRIOR_BODY_FILE")

  local BODY
  BODY=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report-render \
    --run-record-file "$RUN_RECORD_FILE" \
    --timestamp "$TIMESTAMP" \
    --intent "${INTENT:-fix}" \
    --product-name "${FACTORY_PRODUCT_NAME:-Dark Factory}" \
    "${PRIOR_ARGS[@]}" "${BUDGET_ARGS[@]}" || true)
  [ -n "$PRIOR_BODY_FILE" ] && rm -f "$PRIOR_BODY_FILE"

  # Create or update the comment
  local TMPFILE
  TMPFILE=$(mktemp /tmp/cost-report-XXXXXX.md)
  echo "$BODY" > "$TMPFILE"

  if [ -n "$COMMENT_ID" ]; then
    if ! gh api "repos/${FACTORY_REPO_SLUG}/issues/comments/${COMMENT_ID}" \
        --method PATCH -F "body=@${TMPFILE}" >/dev/null; then
      echo "WARNING: Could not update cost report comment ${COMMENT_ID}"
    fi
  else
    gh issue comment "$ISSUE_NUM" --body-file "$TMPFILE" 2>/dev/null \
      || echo "WARNING: Could not post cost report"
  fi
  rm -f "$TMPFILE"
}

# --- Error handler: move ticket back to Ready and post comment ---
on_failure() {
  local EXIT_CODE=$?
  # Capture partial-failure record before any other action (non-fatal)
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
  # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
    --run-id "${RUN_ID:-unknown}" \
    --issue "${ISSUE_NUM:-0}" \
    --intent "${INTENT:-unknown}" \
    --stage "failed" \
    --verdict "failed" || true
  # Assemble a full run-record.json on the failure path too, so harness_economics'
  # outcome.state == "failed" (score 0.0) is actually reachable — previously only the
  # bare stage event above was written and cmd_assemble never ran on failure.
  if [ -n "${ARTIFACTS_DIR:-}" ]; then
    local FAIL_COST_JSON FAIL_COST_STDERR FAIL_COST_RC
    FAIL_COST_JSON=$(mktemp)
    FAIL_COST_STDERR=$(mktemp)
    set +e
    archon workflow cost --last --json --quiet > "$FAIL_COST_JSON" 2>"$FAIL_COST_STDERR"
    FAIL_COST_RC=$?
    set -e
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
      --run-id "${RUN_ID:-unknown}" \
      --issue "${ISSUE_NUM:-0}" \
      --intent "${INTENT:-unknown}" \
      --started-at "${RUN_STARTED_AT:-}" \
      --artifacts-dir "$ARTIFACTS_DIR" \
      --archon-cost-json "$FAIL_COST_JSON" \
      --archon-cost-exit-code "$FAIL_COST_RC" \
      --archon-cost-stderr-file "$FAIL_COST_STDERR" \
      --status failed \
      --out-file "$ARTIFACTS_DIR/run-record.json" || true
    rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"
  fi
  if [ -n "${ISSUE_NUM:-}" ] && [ "$INTENT" != "close" ]; then
    if [ "$INTENT" = "refine" ] || [ "$INTENT" = "plan" ] || [ "$INTENT" = "deconflict" ]; then
      # No board status change here — the scheduler's trip_to_blocked() handles the
      # Blocked transition after N attempts. Setting Blocked from on_failure would put
      # the issue in Blocked before the scheduler's counter accumulates; Priority 3
      # would then retry it as "Fix" (implement) — wrong intent for a pipeline phase.
      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
      echo "Refinement pipeline failed (exit $EXIT_CODE) for issue #$ISSUE_NUM"
      post_or_update_comment "$REFINE_FAILURE_MARKER" \
        "${REFINE_FAILURE_MARKER}
## Refinement Pipeline — Failed

The refinement pipeline encountered an error (exit code $EXIT_CODE) and could not complete.

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`

---
*Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline*"
    else
      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
      echo "Dark factory failed (exit $EXIT_CODE). Moving issue #$ISSUE_NUM back to Ready..."
      run_post_mortem "$EXIT_CODE" "${TMP_OUT:-}" || true
      set_board_status "blocked" 2>/dev/null || true
      post_or_update_comment "$FACTORY_FAILURE_MARKER" \
        "${FACTORY_FAILURE_MARKER}
## Dark Factory Run — Failed

The dark factory encountered an error (exit code $EXIT_CODE) and could not complete.
Issue has been moved to **Blocked**.

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`

---
*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*"
    fi
  fi
  # Cost report runs LAST and is non-fatal: a failure here (missing dependency,
  # cost-JSON schema drift) must never abort the trap before the Blocked transition
  # and failure comment above have run.
  post_cost_report || true
}
trap on_failure ERR

# =============================================================================
# --- Conflict resolution: thin adapter -> factory_core CLI ---
# =============================================================================

# Merge origin/main into HEAD using the tiered factory_core resolver.
# Returns 0 on clean merge or successful resolution, 1 after Tier-3 escalation.
_resolve_merge_conflicts() {
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
  # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" \
    deconflict --issue "$ISSUE_NUM" || return $?
}

# Guard: allow sourcing for unit tests without running the main execution block.
# Set ENTRYPOINT_SOURCE_ONLY=1 before sourcing. External commands (git, gh, docker,
# claude) must be stubbed by the test to prevent real side effects.
[ "${ENTRYPOINT_SOURCE_ONLY:-0}" = "1" ] && return 0

# --- Clone the repo ---
echo "Cloning ${FACTORY_REPO}..."
if [ -d "$CLONE_DIR" ]; then
  rm -rf "$CLONE_DIR"
fi
git clone "$REPO_URL" "$CLONE_DIR"
cd "$CLONE_DIR"

# --- Self-contained fallbacks: target repos without in-repo factory files (post-P3 cleanup) ---
# Copy baked factory pieces into the clone ONLY where the target repo does not provide them.
# Everything copied here is appended to .git/info/exclude so it can never be committed back.
_exclude_in_clone() {
  local rel="$1"
  local excl="$CLONE_DIR/.git/info/exclude"
  mkdir -p "$(dirname "$excl")"
  grep -qxF "$rel" "$excl" 2>/dev/null || echo "$rel" >> "$excl"
}
if [ ! -d "$CLONE_DIR/dark-factory/scripts" ]; then
  mkdir -p "$CLONE_DIR/dark-factory"
  cp -r /opt/dark-factory/scripts "$CLONE_DIR/dark-factory/scripts"
  _exclude_in_clone "dark-factory/scripts/"
  echo "[self-contained] baked scripts copied into clone (target repo has none)"
fi
if [ ! -d "$CLONE_DIR/.archon/workflows" ]; then
  mkdir -p "$CLONE_DIR/.archon"
  cp -r /opt/dark-factory/workflows "$CLONE_DIR/.archon/workflows"
  _exclude_in_clone ".archon/workflows/"
  echo "[self-contained] baked workflows copied into clone (target repo has none)"
fi
if [ ! -d "$CLONE_DIR/.archon/commands" ]; then
  mkdir -p "$CLONE_DIR/.archon"
  cp -r /opt/dark-factory/commands "$CLONE_DIR/.archon/commands"
  _exclude_in_clone ".archon/commands/"
  echo "[self-contained] baked commands copied into clone (target repo has none)"
fi

# --- Effective config: adapter token_optimization > clone config.yaml > baked defaults (df#14) ---
# Transition (clone config.yaml committed): no-op, clone file wins byte-identically.
# Post-cleanup: materializes baked←adapter config at the clone path all readers expect,
# git-excluded so it can never be committed back. Fail-open: never kills the run.
PYTHONPATH=/opt/dark-factory/scripts python3 -m factory_core.effective_config \
  --clone-dir "$CLONE_DIR" --materialize || true

# --- Apply config.yaml policy knobs post-clone (env overrides logged when active) ---
_entrypoint_cfg_apply

# --- Copy preview template and seed data into clone ---
# TARGET-PATH: dark-factory/ exists in the clone in both worlds — the target's own subtree
# until P3 cleanup, or created by the self-contained fallback copy above (df#14)
mkdir -p "$CLONE_DIR/dark-factory"
cp /opt/dark-factory/docker-compose.preview.yml "$CLONE_DIR/dark-factory/docker-compose.preview.yml"
cp -r /opt/dark-factory/seed/ "$CLONE_DIR/dark-factory/seed/"

# --- Install target project deps for local testing (target-conditional, #23) ---
# Targets without a Python backend or Node frontend (e.g. this repo as the P4
# dogfood self-target) must not die here under set -euo pipefail.
if [ -f "$CLONE_DIR/backend/requirements.txt" ]; then
  echo "Installing backend dependencies..."
  # --no-warn-script-location: pip installs as non-root into ~/.local/bin (off
  # PATH); harmless because all tools run via `python -m`, so mute the 20+ warnings.
  cd "$CLONE_DIR/backend" && pip install --quiet --no-warn-script-location -r requirements.txt
else
  echo "[deps] no backend/requirements.txt in target — skipping pip install"
fi
if [ -f "$CLONE_DIR/frontend/package.json" ]; then
  echo "Installing frontend dependencies..."
  cd "$CLONE_DIR/frontend" && npm install --silent
else
  echo "[deps] no frontend/package.json in target — skipping npm install"
fi
cd "$CLONE_DIR"

# --- Write factory-scoped Claude settings (gitignored, never committed) ---
# Uses the absolute codeindex path (required — Claude Code does not inherit shell PATH).
# disableWorkflows: the factory never uses Claude Code's Workflow tool — Archon owns
# orchestration and refine/plan spawn subagents via the Agent tool — so dropping that
# tool's (large) schema from each request trims input tokens at no functional cost.
# Kept here, NOT in the committed .claude/settings.json, so local dev sessions are
# unaffected and keep the Workflow tool.
CODEINDEX_BIN=$(which codeindex 2>/dev/null || true)
mkdir -p "$CLONE_DIR/.claude"
if [ -n "$CODEINDEX_BIN" ]; then
  printf '{\n  "disableWorkflows": true,\n  "mcpServers": {\n    "codeindex": { "command": "%s", "args": ["serve", "--mcp"] }\n  }\n}\n' \
    "$CODEINDEX_BIN" > "$CLONE_DIR/.claude/settings.local.json"
  echo "codeindex MCP registered at $CODEINDEX_BIN; Workflow tool disabled"
else
  printf '{\n  "disableWorkflows": true\n}\n' > "$CLONE_DIR/.claude/settings.local.json"
  echo "WARNING: codeindex not found; MCP server will not be registered (Workflow tool disabled)"
fi

# --- Install pre-commit hooks so codeindex-blast warn hook fires in the run log ---
pre-commit install --allow-missing-config 2>/dev/null || true

if [ "$INTENT" = "fix-main" ]; then
  echo "[fix-main] dispatched main-red auto-fix; repo cloned at ${CLONE_DIR}"
  if [ "${MAIN_RED_AUTOFIX_ENABLED:-false}" != "true" ]; then
    echo "[fix-main] disabled (MAIN_RED_AUTOFIX_ENABLED != true); exiting"
    exit 0
  fi
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" main-red-fix --once || true  # TARGET-PATH
  exit 0
fi

# --- Smoke gate: verify origin/main is green before any per-ticket work ---
# Applies to fix (new), continue, deconflict (resolve), and recheck intents.
# On red main: exits 0 (no per-ticket failure), files a regression ticket, writes sentinel.
# On green: cleans up any prior red state and proceeds.
if [ "$INTENT" = "fix" ] || [ "$INTENT" = "continue" ] || [ "$INTENT" = "deconflict" ] || [ "$INTENT" = "recheck" ]; then
  source /opt/dark-factory/scripts/hooks.sh
  run_hook --gate smoke-gate
fi

# --- Recheck flow: the run exists solely to re-evaluate the gate (#365) ---
# Reaching this line means the gate passed (on red, run_hook --gate smoke-gate exits 0
# inside _smoke_on_red). _smoke_on_green has already cleared the sentinel and closed
# the regression ticket — there is no per-ticket work to do.
if [ "$INTENT" = "recheck" ]; then
  echo "[recheck] main is green — sentinel cleared; done."
  exit 0
fi

# =============================================================================
# --- Deconflict flow: resolve → validate → push → report → exit ---
# 'continue' sync is handled by the archon workflow's de-conflict node.
# =============================================================================
if [ "$INTENT" = "deconflict" ]; then
  git fetch --all 2>/dev/null || true
  FEATURE_BRANCH=$(git branch -r 2>/dev/null | grep -E "origin/feat/issue-${ISSUE_NUM}-" | head -1 | tr -d ' ' | sed 's|origin/||')

  if [ -z "$FEATURE_BRANCH" ]; then
    echo "ERROR: No feature branch found for issue #${ISSUE_NUM}" >&2
    _conflict_escalate "No feature branch matching feat/issue-${ISSUE_NUM}-* was found."
    exit 0
  fi

  # The shared setup above (clone → cp baked seed/preview/settings into the tree) leaves the
  # working tree dirty. `git checkout <feature-branch>` then ABORTS when the branch touches
  # those paths (e.g. a seed-file PR like #207), and the old `|| true` masked the failure: the
  # run silently stayed on main, did a no-op "Already up to date" merge, then failed to push a
  # branch it never checked out (`src refspec ... does not match any`). Reset to a pristine tree
  # first. Scope clean to the copied dirs and never use -x, so gitignored node_modules survives
  # for the tsc validation below.
  git reset --hard HEAD >/dev/null 2>&1 || true
  git clean -fd dark-factory/ .claude/ >/dev/null 2>&1 || true  # TARGET-PATH: runs in $CLONE_DIR

  if ! git checkout "$FEATURE_BRANCH" 2>&1 \
       && ! git checkout -b "$FEATURE_BRANCH" "origin/$FEATURE_BRANCH" 2>&1; then
    _conflict_escalate "Could not check out branch ${FEATURE_BRANCH} for conflict resolution."
    exit 0
  fi

  # Hard guard: never run the merge/push on the wrong branch — this is the failure mode that
  # turned a routine conflict into a silent no-op-merge + failed-push loop until the breaker
  # tripped the PR to Blocked. If checkout didn't land us on the feature branch, escalate loudly.
  CURRENT_BRANCH=$(git branch --show-current)
  if [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
    _conflict_escalate "Checkout did not land on ${FEATURE_BRANCH} (HEAD on '${CURRENT_BRANCH}')."
    exit 0
  fi

  if ! _resolve_merge_conflicts; then
    # Tier 3 escalation already handled inside _resolve_merge_conflicts
    exit 0
  fi
fi

if [ "$INTENT" = "deconflict" ]; then
  # --- Validate: target hook if present, else inline tsc (parity fallback) ---
  DECONFLICT_VALIDATION="PASS"
  if [ -x "$CLONE_DIR/.factory/hooks/validate" ]; then
    echo "[deconflict] Running .factory/hooks/validate..."
    if ! run_hook --gate validate; then
      DECONFLICT_VALIDATION="FAIL"
      echo "[deconflict] validate hook failed — escalating to Blocked."
      _conflict_escalate "Validation failed after merge (.factory/hooks/validate). Run the hook locally to see errors."
      exit 0
    fi
  else
    echo "[deconflict] Running TypeScript validation..."
    if ! (cd "$CLONE_DIR/frontend" && npx tsc --noEmit 2>&1); then
      DECONFLICT_VALIDATION="FAIL"
      echo "[deconflict] TypeScript validation failed — escalating to Blocked."
      _conflict_escalate "TypeScript type errors after merge. Run 'cd frontend && npx tsc --noEmit' to see them."
      exit 0
    fi
  fi

  # --- Push the resolved branch ---
  echo "[deconflict] Pushing resolved branch ${FEATURE_BRANCH}..."
  git push origin "$FEATURE_BRANCH" 2>&1

  # --- Move board back to In Review ---
  set_board_status "in_review" 2>/dev/null || true

  # --- Write artifact ---
  cat > "$ARTIFACTS_DIR/conflict_resolution.md" << EOF
# Conflict Resolution — Issue #${ISSUE_NUM}

**Status:** RESOLVED
**Branch:** ${FEATURE_BRANCH}
**TypeScript validation:** ${DECONFLICT_VALIDATION}

Merged origin/main into the feature branch using the tiered resolution strategy.
EOF

  # --- Post success comment ---
  gh issue comment "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --body \
"## Dark Factory — Merge Conflicts Resolved

\`main\` has been merged into \`${FEATURE_BRANCH}\` and all conflicts were resolved automatically.

The branch has been pushed and is ready for re-review.

---
*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*" 2>/dev/null || true

  echo "[deconflict] Done — issue #${ISSUE_NUM} conflicts resolved and pushed."
  exit 0
fi

# --- Run via Archon workflow ---
export CLAUDE_BIN_PATH=/usr/bin/claude
export IS_SANDBOX=1
export ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1
echo "Starting dark factory: $ARGUMENTS"
while true; do
  set +e
  TMP_OUT=$(mktemp)
  archon workflow run archon-dark-factory "$ARGUMENTS" 2>&1 | tee "$TMP_OUT"
  EXIT_CODE=${PIPESTATUS[0]}
  set -e

  if [ "$EXIT_CODE" -ne 0 ]; then
    if _handle_session_window_pause "$TMP_OUT"; then
      rm -f "$TMP_OUT"
      exit 0
    fi
    if grep -qiE "usage limit|rate limit|429|credit balance|session limit" "$TMP_OUT"; then
      # Kill-switch fallback (SESSION_WINDOW_BACKOFF_ENABLED=false): old sleep-forever
      # behavior, unchanged.
      # Attempt to parse specific reset time from: "You've hit your session limit · resets 11:10pm (America/Toronto)"
      RESET_TIME=$(grep -ioP "resets\s+\K([0-9]{1,2}:[0-9]{2}[a-z]{2})" "$TMP_OUT" | head -1)
      RESET_TZ=$(grep -ioP "resets\s+[0-9]{1,2}:[0-9]{2}[a-z]{2}\s*\(\K([^)]+)" "$TMP_OUT" | head -1)
      
      SLEEP_SECS=300 # default to 5 mins if parsing fails
      if [ -n "$RESET_TIME" ]; then
        if [ -n "$RESET_TZ" ]; then
          TARGET_EPOCH=$(TZ="$RESET_TZ" date -d "$RESET_TIME" +%s 2>/dev/null || echo "")
        else
          TARGET_EPOCH=$(date -d "$RESET_TIME" +%s 2>/dev/null || echo "")
        fi
        
        if [ -n "$TARGET_EPOCH" ]; then
          NOW_EPOCH=$(date +%s)
          if [ "$TARGET_EPOCH" -lt "$NOW_EPOCH" ]; then
            TARGET_EPOCH=$((TARGET_EPOCH + 86400))
          fi
          SLEEP_SECS=$((TARGET_EPOCH - NOW_EPOCH + 60)) # Add 60s buffer to ensure it actually resets
          
          # Failsafe for absurd values (e.g., more than 24 hours or negative)
          if [ "$SLEEP_SECS" -lt 0 ] || [ "$SLEEP_SECS" -gt 90000 ]; then
            SLEEP_SECS=300
          fi
        fi
      fi

      echo "Claude Max subscription limit reached. Sleeping for ${SLEEP_SECS}s before retrying..."
      rm -f "$TMP_OUT"
      sleep "$SLEEP_SECS"
      echo "Waking up and retrying..."
      continue
    fi
    run_post_mortem "$EXIT_CODE" "$TMP_OUT" || true
    _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "$TMP_OUT"
    rm -f "$TMP_OUT"
    exit "$EXIT_CODE"
  fi
  rm -f "$TMP_OUT"
  break
done

# --- Capture archon cost data and assemble run record (non-fatal) ---
ARCHON_COST_JSON=$(mktemp)
ARCHON_COST_STDERR=$(mktemp)
set +e
archon workflow cost --last --json --quiet > "$ARCHON_COST_JSON" 2>"$ARCHON_COST_STDERR"
ARCHON_COST_RC=$?
set -e

# TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
# P3 cleanup, baked self-contained fallback copy afterwards (df#14)
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "${RUN_STARTED_AT:-}" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$ARCHON_COST_JSON" \
  --archon-cost-exit-code "$ARCHON_COST_RC" \
  --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
  --clone-dir "$CLONE_DIR" || true

rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR"

# --- Post cost report to GitHub issue (success path) — non-fatal ---
post_cost_report || true

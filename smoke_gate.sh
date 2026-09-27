#!/usr/bin/env bash
# Smoke gate: verifies origin/main is buildable before per-ticket factory work begins.
# Sourced by entrypoint.sh. Set SMOKE_GATE_SOURCE_ONLY=1 before sourcing in tests.
# On red main: exits 0 (no ERR trap, no per-ticket board/retry/breaker change).
# On green main: cleans up any prior red state and returns 0.

# Source instance identity (env-overridable; defaults = MarketHawk parity).
# Safe to source again if entrypoint.sh already sourced it — all vars use :- semantics.
source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/identity.sh"

SMOKE_STATE_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
SMOKE_MARKER="<!-- df-main-red -->"
PROVIDERS_CLI="$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/factory_core/providers/cli.py"
SMOKE_CORE_CLI="$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/factory_core/cli.py"
# Ticket-comment marker for the no-hook refusal (#436) — same style as entrypoint.sh's
# FACTORY_FAILURE_MARKER; upserted, so repeat dispatches update one comment.
SMOKE_HOOK_MISSING_MARKER="<!-- df-smoke-hook-missing -->"

# Runs tsc + python import on origin/main. Returns 0 on full pass, non-zero on first failure.
_smoke_check_main() {
  echo "[smoke_gate] Checking frontend TypeScript (tsc)..."
  if ! (cd "${CLONE_DIR:-$FACTORY_CLONE_DIR}/frontend" \
        && rm -f tsconfig.app.tsbuildinfo \
        && npx tsc -p tsconfig.app.json --noEmit 2>&1); then
    echo "[smoke_gate] tsc FAILED — main is red"
    return 1
  fi
  echo "[smoke_gate] Checking backend Python import graph..."
  # Settings() is instantiated at import time and requires DATABASE_URL /
  # POLYGON_API_KEY / JWT_SECRET_KEY (>=32 chars) / REDIS_PASSWORD (>=16 chars,
  # required no-default since #415 Redis requirepass; preview env contract, #190).
  # The gate verifies the import graph compiles, NOT that config is real —
  # without throwaway values the check is red in every factory container, so
  # every fix/continue/deconflict run false-latches the sentinel (#365). Same
  # pattern as docker-compose.preview.yml and ci.yml.
  if ! (cd "${CLONE_DIR:-$FACTORY_CLONE_DIR}/backend" \
        && DATABASE_URL="postgresql://smoke:smoke@localhost:5432/smoke" \
           POLYGON_API_KEY="smoke-gate-only-not-a-real-key" \
           JWT_SECRET_KEY="smoke-gate-only-not-secret-0123456789abcdef" \
           REDIS_PASSWORD="smoke-gate-only-not-a-real-redis-password" \
           python -c "import app.main" 2>&1); then
    echo "[smoke_gate] python import FAILED — main is red"
    return 1
  fi
  return 0
}

# Lists OPEN regression tickets bearing SMOKE_MARKER, oldest first, one number per
# line. GitHub is the source of truth: the local state files can be lost on container
# recreate, which historically duplicated tickets on red and stranded them open on
# green. Returns gh's exit code so callers can distinguish "no open tickets" (empty
# output, rc 0) from "could not ask GitHub" (rc non-zero → fall back to state file).
_smoke_list_open_red_issues() {
  gh issue list \
    --repo "$FACTORY_REPO_SLUG" \
    --label regression --state open --limit 100 \
    --json number,body \
    --jq "[ .[] | select(.body | contains(\"${SMOKE_MARKER}\")) | .number ] | sort | .[]" \
    2>/dev/null
}

# Writes sentinel, files or updates the regression ticket, then exits 0 (clean halt).
# At most ONE marker ticket stays open: an existing open ticket is adopted (oldest
# wins, so links/comments stay in one place) and duplicates are closed before
# commenting; a new ticket is created only when none is open.
_smoke_on_red() {
  echo "[smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)"
  mkdir -p "${SMOKE_STATE_DIR}"
  touch "${SMOKE_STATE_DIR}/main-is-red"
  # Stamp the recheck throttle: red was just confirmed, so the scheduler's first
  # "Recheck main" dispatch (#365) should wait a full MAIN_RED_RECHECK_MINUTES.
  touch "${SMOKE_STATE_DIR}/main-red-last-recheck"

  local ISSUE_FILE="${SMOKE_STATE_DIR}/main-is-red-issue"
  local FILE_NUM=""
  [ -f "$ISSUE_FILE" ] && FILE_NUM=$(cat "$ISSUE_FILE")

  # Set by hooks.sh run_hook when the check that just failed was the target's own
  # non-executable hook run via bash (#438) — say so in the ticket text.
  local HOOK_NOTE=""
  if [ -n "${HOOK_NOT_EXECUTABLE_NOTE:-}" ]; then
    HOOK_NOTE="

The failing check was the target's own smoke-gate hook, run with bash because it is not executable: ${HOOK_NOT_EXECUTABLE_NOTE}. Fix with \`git update-index --chmod=+x .factory/hooks/smoke-gate\`."
  fi

  local OPEN_NUMS="" LIST_OK=1
  OPEN_NUMS=$(_smoke_list_open_red_issues) && LIST_OK=0

  # Canonical ticket: the state-file one if present; otherwise adopt the oldest open
  # marker ticket from GitHub (state file lost, e.g. container recreate). Every other
  # open marker ticket is closed as a duplicate.
  local REGR_NUM="$FILE_NUM"
  if [ "$LIST_OK" -eq 0 ]; then
    if [ -z "$REGR_NUM" ] && [ -n "$OPEN_NUMS" ]; then
      REGR_NUM=$(echo "$OPEN_NUMS" | head -1)
    fi
    local DUP
    for DUP in $OPEN_NUMS; do
      [ "$DUP" = "$REGR_NUM" ] && continue
      gh issue close "$DUP" \
        --repo "$FACTORY_REPO_SLUG" \
        --comment "Duplicate of #${REGR_NUM} — only one main-red regression ticket stays open." \
        2>/dev/null || true
    done
  fi

  if [ -n "$REGR_NUM" ]; then
    echo "$REGR_NUM" > "$ISSUE_FILE"
    gh issue comment "$REGR_NUM" \
      --repo "$FACTORY_REPO_SLUG" \
      --body "main still red at $(date -u +%FT%TZ) — factory implementation runs remain paused.${HOOK_NOTE}" \
      2>/dev/null || true
  else
    local BODY_FILE
    BODY_FILE=$(mktemp /tmp/smoke-gate-regression-XXXXXX.md)
    cat > "$BODY_FILE" << EOF
${SMOKE_MARKER}

**main smoke check failed at $(date -u +%FT%TZ).**

The dark factory is pausing all implementation dispatches (Priority 1.5/2/3) until \`origin/main\` compiles cleanly.

This ticket closes automatically on the next green gate pass.${HOOK_NOTE}
EOF
    REGR_NUM=$(python3 "$PROVIDERS_CLI" tracker create \
      --title "main is red: tsc/python import failure" \
      --body-file "$BODY_FILE" \
      --labels regression 2>/dev/null || true)
    rm -f "$BODY_FILE"
    [ -n "$REGR_NUM" ] && echo "$REGR_NUM" > "$ISSUE_FILE"
  fi

  exit 0
}

# On green: removes sentinel (if present) and closes ALL open regression tickets —
# swept from GitHub, not just the state-file number, so tickets orphaned by a lost
# state dir still get closed instead of leaking open forever.
_smoke_on_green() {
  local ISSUE_FILE="${SMOKE_STATE_DIR}/main-is-red-issue"
  local FILE_NUM=""
  [ -f "$ISSUE_FILE" ] && FILE_NUM=$(cat "$ISSUE_FILE")

  local OPEN_NUMS=""
  OPEN_NUMS=$(_smoke_list_open_red_issues) || true

  if [ ! -f "${SMOKE_STATE_DIR}/main-is-red" ] && [ -z "$FILE_NUM" ] && [ -z "$OPEN_NUMS" ]; then
    return 0
  fi
  echo "[smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)"
  rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-red-last-recheck"

  # Union of the state-file ticket (covers a failed GitHub sweep) and the swept list.
  local REGR_NUM
  for REGR_NUM in $(printf '%s\n%s\n' "$FILE_NUM" "$OPEN_NUMS" | grep -E '^[0-9]+$' | sort -un); do
    gh issue close "$REGR_NUM" \
      --repo "$FACTORY_REPO_SLUG" \
      --comment "main smoke gate passed — closing regression ticket." \
      2>/dev/null || true
  done
  rm -f "$ISSUE_FILE"
}

# No-hook refusal (#436) — called by hooks.sh run_hook when the target has no
# .factory/hooks/smoke-gate PRESENT (absent, a directory, or zero-byte). A missing hook
# is a target misconfiguration, not a red main: this never writes the main-red state
# files, never calls _smoke_on_red/_smoke_on_green, and files no regression ticket.
# Signals: stderr; one durable line in ${SCHEDULER_STATE_DIR}/hook-warnings.log
# (dispatch is `run -d --rm`, so stderr dies with the container); a marker comment on
# the ticket when ISSUE_NUM is set; a health event LAST (guarded, never fatal).
# Returns 1 so entrypoint.sh's ERR trap (on_failure) fails the run. Must `return`,
# never `exit`: an ERR trap does not fire on exit, so an exit here would strand the
# ticket In Progress.
_smoke_hook_missing() {
  local hook="${CLONE_DIR:-}/.factory/hooks/smoke-gate"
  {
    echo "ERROR: [smoke_gate] smoke-gate-hook-missing path=${hook} — the target declares no smoke-gate hook; refusing this run."
    echo "[smoke_gate] main was NOT checked and has NOT been marked red (no main-is-red sentinel, no regression ticket)."
    echo "[smoke_gate] Fix: commit a non-empty, executable .factory/hooks/smoke-gate to the target repo. Start from templates/new-target/.factory/hooks/smoke-gate; see docs/onboarding-new-target.md step 3b."
  } >&2
  # Durable trace — #438's guards verbatim: only if the state dir exists, never mkdir
  # (CI asserts /var/lib/dark-factory stays empty), never fatal.
  local state_dir="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
  if [ -d "$state_dir" ]; then
    printf '%s smoke-gate-hook-missing %s issue=%s\n' \
      "$(date -u +%FT%TZ)" "$hook" "${ISSUE_NUM:-}" \
      2>/dev/null >> "${state_dir}/hook-warnings.log" || true
  fi
  # on_failure's generic comment says only "exit code N" (no transcript exists this
  # early), so the actionable text reaches the ticket through its own marker comment.
  # recheck has no ticket context → no comment.
  if [ -n "${ISSUE_NUM:-}" ]; then
    local BODY_FILE
    if BODY_FILE=$(mktemp /tmp/smoke-hook-missing-XXXXXX.md 2>/dev/null); then
      cat > "$BODY_FILE" << EOF
${SMOKE_HOOK_MISSING_MARKER}
## Dark Factory — Smoke-gate hook missing

This run was refused before any work started: the target repo has no \`.factory/hooks/smoke-gate\` hook (absent, a directory, or an empty file), so the factory cannot check whether \`main\` is healthy.

\`main\` was **not checked** and has **not** been marked red — no \`main-is-red\` sentinel, no regression ticket, and other tickets are not paused.

**To fix:** commit a non-empty, executable \`.factory/hooks/smoke-gate\` to the target repo (exit 0 = main is green). Start from the factory's \`templates/new-target/.factory/hooks/smoke-gate\` and follow \`docs/onboarding-new-target.md\` step 3b, then re-dispatch this ticket (a Blocked ticket: move it back to Ready).
EOF
      python3 "$PROVIDERS_CLI" tracker comment --id "$ISSUE_NUM" \
        --marker "$SMOKE_HOOK_MISSING_MARKER" --body-file "$BODY_FILE" >/dev/null 2>&1 || true
      rm -f "$BODY_FILE"
    fi
  fi
  python3 "$SMOKE_CORE_CLI" run-record health-event \
    --run-id "${RUN_ID:-unknown}" --issue "${ISSUE_NUM:-0}" \
    --event factory.smoke_gate.hook_missing \
    --detail "path=${hook}" >/dev/null 2>&1 || true
  return 1
}

# MarketHawk-parity check (tsc + python import) wrapped in the red/green state
# machinery. No longer the no-hook fallback: since #436, hooks.sh run_hook refuses a
# target with no smoke-gate hook (_smoke_hook_missing above) instead of running this.
# Kept for direct callers (run_smoke_gate, tests/test_smoke_gate.sh).
# Returns 0 on green (proceed); exits 0 on red (clean halt, no per-ticket failure).
_default_smoke_gate() {
  if _smoke_check_main; then
    _smoke_on_green
    return 0
  else
    _smoke_on_red
    # _smoke_on_red calls exit 0; unreachable
  fi
}

# Thin wrapper kept for backward compatibility (scheduler.sh, tests, direct calls).
run_smoke_gate() { _default_smoke_gate "$@"; }

# Source-only guard: when set, functions above are defined but no auto-exec code runs.
# Mirrors scheduler.sh's SCHEDULER_SOURCE_ONLY pattern for unit testing.
if [ "${SMOKE_GATE_SOURCE_ONLY:-0}" = "1" ]; then
  return 0
fi

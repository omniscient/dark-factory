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
      --body "main still red at $(date -u +%FT%TZ) — factory implementation runs remain paused." \
      2>/dev/null || true
  else
    local BODY_FILE
    BODY_FILE=$(mktemp /tmp/smoke-gate-regression-XXXXXX.md)
    cat > "$BODY_FILE" << EOF
${SMOKE_MARKER}

**main smoke check failed at $(date -u +%FT%TZ).**

The dark factory is pausing all implementation dispatches (Priority 1.5/2/3) until \`origin/main\` compiles cleanly.

This ticket closes automatically on the next green gate pass.
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

# Built-in default for the smoke-gate hook — called by hooks.sh run_hook when no
# target repo ships .factory/hooks/smoke-gate. Contains today's MarketHawk checks
# (tsc + python import). Parity invariant: MarketHawk needs zero hooks.
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

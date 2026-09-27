#!/usr/bin/env bash
# run_hook [--gate] <name> [args…] — target hook > built-in default. Gate = propagate exit code.
#
# Discovers per-repo hooks at ${CLONE_DIR}/.factory/hooks/<name>.
# A hook counts as present when it is a non-empty regular file ([ -f ] && [ -s ]).
# A present hook without the executable bit (e.g. committed from Windows as mode
# 100644) still runs — via `bash "$hook"`, shebang ignored — with a loud
# `hook-not-executable` warning on stderr plus a durable line in
# ${SCHEDULER_STATE_DIR}/hook-warnings.log (#438).
# Falls back to built-in defaults when no target hook is present (absent, a
# directory, or a zero-byte placeholder):
#   smoke-gate  →  _smoke_hook_missing: refuse the run, non-zero even without --gate (#436)
#   validate    →  no-op exit 0 (P2 moves MarketHawk's real validate into its adapter)
#   preview-up  →  no-op exit 0
#   preview-down → no-op exit 0
#
# Hook env contract (exported to the hook process):
#   CLONE_DIR, ARTIFACTS_DIR, ISSUE_NUM, FACTORY_REPO_SLUG
#
# Source smoke_gate.sh to load _smoke_hook_missing and the _smoke_on_red/_smoke_on_green
# state machinery (SMOKE_GATE_SOURCE_ONLY suppresses auto-exec).
SMOKE_GATE_SOURCE_ONLY=1 source "$(dirname "${BASH_SOURCE[0]:-$0}")/../smoke_gate.sh"

run_hook() {
  local gate=0
  [ "$1" = "--gate" ] && { gate=1; shift; }
  local name="$1"; shift || true
  local hook="${CLONE_DIR}/.factory/hooks/${name}"
  local rc=0
  # Dynamically scoped: _smoke_on_red (called below) appends it to the ticket text.
  local HOOK_NOT_EXECUTABLE_NOTE=""
  if [ -f "$hook" ] && [ -s "$hook" ]; then
    local -a invoke
    if [ -x "$hook" ]; then
      invoke=("$hook")
    else
      local crlf="no"; grep -q $'\r' "$hook" && crlf="yes"
      echo "WARNING: [hooks] hook-not-executable path=${hook} crlf=${crlf} — running it with bash (the hook's own shebang is ignored); fix with: git update-index --chmod=+x .factory/hooks/${name}" >&2
      # Durable trace beyond stderr: dispatch is `run -d --rm`, so the run's stderr
      # never reaches the scheduler log and dies with the container. Guarded, never
      # mkdir (CI asserts /var/lib/dark-factory stays empty), never fatal.
      local state_dir="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
      if [ -d "$state_dir" ]; then
        printf '%s hook-not-executable %s issue=%s crlf=%s\n' \
          "$(date -u +%FT%TZ)" "$hook" "${ISSUE_NUM:-}" "$crlf" \
          2>/dev/null >> "${state_dir}/hook-warnings.log" || true
      fi
      HOOK_NOT_EXECUTABLE_NOTE="hook-not-executable ${hook} (crlf=${crlf})"
      invoke=(bash "$hook")
    fi
    if [ "$name" = "smoke-gate" ]; then
      # Target hook supplies the CHECK only (exit 0 green / non-zero red).
      # Red/green STATE machinery (sentinel, regression ticket, clean-halt
      # exit 0) stays factory-side — identical semantics to the built-in gate.
      if CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
           FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@"; then
        _smoke_on_green
        rc=0
      else
        _smoke_on_red   # exits 0 (clean halt); unreachable after
      fi
    else
      CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
        FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@" || rc=$?
    fi
  else
    case "$name" in
      # No hook present → refuse the run (#436; provided by smoke_gate.sh). The
      # smoke-gate arm is --gate-only: return non-zero even without --gate, or the
      # non-gate `return 0` below would swallow the refusal and main goes unchecked.
      smoke-gate) _smoke_hook_missing "$@" || rc=$?; return "$rc" ;;
      *) rc=0 ;;                                          # no default → no-op
    esac
  fi
  if [ "$gate" = "1" ]; then return "$rc"; else return 0; fi
}

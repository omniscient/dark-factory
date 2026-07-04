#!/usr/bin/env bash
# run_hook [--gate] <name> [args…] — target hook > built-in default. Gate = propagate exit code.
#
# Discovers per-repo hooks at ${CLONE_DIR}/.factory/hooks/<name>.
# Falls back to built-in defaults when no target hook is present:
#   smoke-gate  →  _default_smoke_gate (MarketHawk tsc + backend-import checks)
#   validate    →  no-op exit 0 (P2 moves MarketHawk's real validate into its adapter)
#   preview-up  →  no-op exit 0
#   preview-down → no-op exit 0
#
# Hook env contract (exported to the hook process):
#   CLONE_DIR, ARTIFACTS_DIR, ISSUE_NUM, FACTORY_REPO_SLUG
#
# Source smoke_gate.sh to load _default_smoke_gate (SMOKE_GATE_SOURCE_ONLY suppresses auto-exec).
SMOKE_GATE_SOURCE_ONLY=1 source "$(dirname "${BASH_SOURCE[0]:-$0}")/../smoke_gate.sh"

run_hook() {
  local gate=0
  [ "$1" = "--gate" ] && { gate=1; shift; }
  local name="$1"; shift || true
  local hook="${CLONE_DIR}/.factory/hooks/${name}"
  local rc=0
  if [ -x "$hook" ]; then
    if [ "$name" = "smoke-gate" ]; then
      # Target hook supplies the CHECK only (exit 0 green / non-zero red).
      # Red/green STATE machinery (sentinel, regression ticket, clean-halt
      # exit 0) stays factory-side — identical semantics to the built-in gate.
      if CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
           FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@"; then
        _smoke_on_green
        rc=0
      else
        _smoke_on_red   # exits 0 (clean halt); unreachable after
      fi
    else
      CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
        FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@" || rc=$?
    fi
  else
    case "$name" in
      smoke-gate) _default_smoke_gate "$@" || rc=$? ;;   # provided by smoke_gate.sh
      *) rc=0 ;;                                          # no default → no-op
    esac
  fi
  if [ "$gate" = "1" ]; then return "$rc"; else return 0; fi
}

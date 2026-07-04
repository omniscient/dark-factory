#!/usr/bin/env bash
# Shared gate functions sourced by dark-factory-conformance.md and dark-factory-code-review.md.
# Do not add gate-specific logic here — only the three shared primitives.
# Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.

GATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/agent_roles.sh
source "${GATE_LIB_DIR}/agent_roles.sh"

route_memory_file() {
  local FILE="$1"
  # Try adapter for memory_routing (fail-open: fall back to hardcoded table on any failure).
  # --format keyvalue emits "pattern<TAB>target" lines for dict values.
  if command -v python3 >/dev/null 2>&1; then
    local ROUTING
    ROUTING=$(PYTHONPATH="$GATE_LIB_DIR" python3 -m factory_core.adapter \
      --clone-dir "${CLONE_DIR:-.}" \
      --get memory_routing \
      --format keyvalue 2>/dev/null)
    if [ -n "$ROUTING" ]; then
      local MATCHED=""
      while IFS=$'\t' read -r PATTERN TARGET; do
        [ -z "$PATTERN" ] && continue
        case "$FILE" in
          $PATTERN) MATCHED="$TARGET"; break ;;
        esac
      done <<< "$ROUTING"
      if [ -n "$MATCHED" ]; then
        echo "$MATCHED"
        return
      fi
    fi
  fi
  # Fallback: hardcoded routing table (used when python fails or no match in adapter table).
  case "$FILE" in
    backend/app/*)            echo ".archon/memory/backend-patterns.md" ;;
    frontend/src/*)           echo ".archon/memory/frontend-patterns.md" ;;
    .archon/*|dark-factory/*) echo ".archon/memory/dark-factory-ops.md" ;;
    ARCHITECTURE.md)          echo ".archon/memory/architecture.md" ;;
    *)                        echo ".archon/memory/codebase-patterns.md" ;;
  esac
}

write_memory_entry() {
  # Usage: write_memory_entry TARGET PATH_PREFIX VIOLATION_TEXT SOURCE ISSUE_NUM [AGENT_ROLE]
  # Delegates all write logic to memory_write.py (normalized dedup, cap, expiry, tagging).
  # ${BASH_SOURCE[0]} (not $0) is required because this file is sourced, not executed.
  local TARGET="$1" PATH_PREFIX="$2" TEXT="$3" SOURCE="$4" ISSUE="$5"
  python3 "$(dirname "${BASH_SOURCE[0]}")/memory_write.py" \
    --target "$TARGET" --path-prefix "$PATH_PREFIX" --text "$TEXT" \
    --source "$SOURCE" --issue "$ISSUE"
}

emit_verdict() {
  # Usage: emit_verdict GATE_TYPE STATUS FINDINGS_COUNT SEVERITY
  local GATE="$1" STATUS="$2" COUNT="$3" SEV="$4"
  printf "STATUS: %s\nGATE_TYPE: %s\nFINDINGS_COUNT: %s\nSEVERITY: %s\n" \
    "$STATUS" "$GATE" "$COUNT" "$SEV"
}

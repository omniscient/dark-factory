#!/usr/bin/env bash
# Covers #271: verdict_gate_check.sh's STATUS: parsing, the live needs-discussion
# re-check (messaging only, never the block decision), and the idempotent
# <!-- df-push-gate-failure --> marker comment on a true silent miss.
# Tracker CLI calls are stubbed (never hit the network), following the exported
# python3-function convention already used by tests/test_scheduler.sh.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="${REPO_ROOT}/scripts/verdict_gate_check.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

STUB_LOG="${WORK}/stub.log"
export STUB_LOG
_REAL_PY3="$(command -v python3)"
export _REAL_PY3
NEEDS_DISCUSSION_LABEL="false"
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *providers/cli.py*tracker\ get*)
      if [ "$NEEDS_DISCUSSION_LABEL" = "true" ]; then
        echo '{"labels":[{"name":"needs-discussion"}]}'
      else
        echo '{"labels":[]}'
      fi
      ;;
    *providers/cli.py*tracker\ comment*)
      return 0
      ;;
    *factory_core/cli.py*marker*)
      echo "*Posted by Test Factory Dark Factory*"
      ;;
    *)
      "$_REAL_PY3" "$@"
      ;;
  esac
}
export -f python3
export NEEDS_DISCUSSION_LABEL

_run() {
  local verdict_file="$1" issue="$2" label="$3"
  local rc=0
  : > "$STUB_LOG"
  bash "$GATE" "$verdict_file" "$issue" "$label" > "${WORK}/stdout.log" 2> "${WORK}/stderr.log" || rc=$?
  echo "$rc"
}

# --- Case 1: STATUS: PASS — proceed, no tracker calls at all -----------------
CASE1="${WORK}/case1.md"
printf 'STATUS: PASS\nGATE_TYPE: conformance\nFINDINGS_COUNT: 0\nSEVERITY: none\n' > "$CASE1"
RC=$(_run "$CASE1" "271" "Conformance (Gate 2)")
[ "$RC" = "0" ] || { echo "FAIL case1 exit code: $RC"; cat "${WORK}/stderr.log"; exit 1; }
[ ! -s "$STUB_LOG" ] || { echo "FAIL case1 expected no tracker calls on PASS, got:"; cat "$STUB_LOG"; exit 1; }

# --- Case 2: STATUS: SKIPPED — proceed -------------------------------------
CASE2="${WORK}/case2.md"
printf 'STATUS: SKIPPED\nREASON: conformance.enabled=false\n' > "$CASE2"
RC=$(_run "$CASE2" "271" "Conformance (Gate 2)")
[ "$RC" = "0" ] || { echo "FAIL case2 exit code: $RC"; cat "${WORK}/stderr.log"; exit 1; }

# --- Case 3: STATUS: ERROR (review.md fail-open) — proceed ------------------
CASE3="${WORK}/case3.md"
printf 'STATUS: ERROR\nREASON: no PR found\n' > "$CASE3"
RC=$(_run "$CASE3" "271" "Code Review (Gate 3)")
[ "$RC" = "0" ] || { echo "FAIL case3 exit code: $RC"; cat "${WORK}/stderr.log"; exit 1; }

# --- Case 4: STATUS: BLOCKED, needs-discussion absent — block, no comment ---
CASE4="${WORK}/case4.md"
printf 'STATUS: BLOCKED\nGATE_TYPE: conformance\nFINDINGS_COUNT: 2\nSEVERITY: critical\n' > "$CASE4"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE4" "271" "Conformance (Gate 2)")
[ "$RC" = "1" ] || { echo "FAIL case4 expected exit 1, got $RC"; cat "${WORK}/stderr.log"; exit 1; }
! grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case4: explicit BLOCKED must not post a comment"; cat "$STUB_LOG"; exit 1; }

# --- Case 5: STATUS: BLOCKED, needs-discussion present — block, no comment --
CASE5="${WORK}/case5.md"
printf 'STATUS: BLOCKED\nGATE_TYPE: code-review\nFINDINGS_COUNT: 1\nSEVERITY: high\n' > "$CASE5"
NEEDS_DISCUSSION_LABEL="true"
RC=$(_run "$CASE5" "271" "Code Review (Gate 3)")
[ "$RC" = "1" ] || { echo "FAIL case5 expected exit 1, got $RC"; cat "${WORK}/stderr.log"; exit 1; }
! grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case5: BLOCKED with label present must not post a comment"; cat "$STUB_LOG"; exit 1; }

# --- Case 6: missing file, needs-discussion present — block, no comment -----
CASE6="${WORK}/case6-missing.md"
NEEDS_DISCUSSION_LABEL="true"
RC=$(_run "$CASE6" "271" "Conformance (Gate 2)")
[ "$RC" = "1" ] || { echo "FAIL case6 expected exit 1, got $RC"; cat "${WORK}/stderr.log"; exit 1; }
! grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case6: missing file + needs-discussion present must not post a comment"; cat "$STUB_LOG"; exit 1; }

# --- Case 7: missing file, needs-discussion absent — block AND comment ------
CASE7="${WORK}/case7-missing.md"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE7" "271" "Conformance (Gate 2)")
[ "$RC" = "1" ] || { echo "FAIL case7 expected exit 1, got $RC"; cat "${WORK}/stderr.log"; exit 1; }
grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case7: true silent miss must post a failure comment"; cat "$STUB_LOG"; exit 1; }
grep -q -- "--marker" "$STUB_LOG" || { echo "FAIL case7: comment must use the --marker upsert primitive"; cat "$STUB_LOG"; exit 1; }
grep -q -- "df-push-gate-failure" "$STUB_LOG" || { echo "FAIL case7: comment must use the <!-- df-push-gate-failure --> marker (not #212's df-refine-failure)"; cat "$STUB_LOG"; exit 1; }

# --- Case 8: unparseable file (no STATUS: line), needs-discussion absent -----
CASE8="${WORK}/case8-garbage.md"
printf 'not a verdict file\n' > "$CASE8"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE8" "271" "Conformance (Gate 2)")
[ "$RC" = "1" ] || { echo "FAIL case8 expected exit 1, got $RC"; cat "${WORK}/stderr.log"; exit 1; }
grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case8: unparseable file must be treated as a true silent miss"; cat "$STUB_LOG"; exit 1; }

# --- Case 9: usage errors — zero, one, and two args (mirrors push_gate_check.sh's
# test_missing_prefix_arg_fails/test_missing_issue_arg_fails split) --------------
RC=0
bash "$GATE" >/dev/null 2>&1 || RC=$?
[ "$RC" != "0" ] || { echo "FAIL case9a expected nonzero exit on zero args, got $RC"; exit 1; }

RC=0
bash "$GATE" "${WORK}/case1.md" >/dev/null 2>&1 || RC=$?
[ "$RC" != "0" ] || { echo "FAIL case9b expected nonzero exit on missing issue-number arg, got $RC"; exit 1; }

RC=0
bash "$GATE" "${WORK}/case1.md" "271" >/dev/null 2>&1 || RC=$?
[ "$RC" != "0" ] || { echo "FAIL case9c expected nonzero exit on missing gate-label arg, got $RC"; exit 1; }

# --- Case 10: non-numeric issue number — fail closed without calling tracker ----
# Mirrors push_gate_check.sh's guard against a malformed "null"-style id reaching
# the tracker CLI / a grep regex.
CASE10="${WORK}/case10-missing.md"
: > "$STUB_LOG"
RC=0
bash "$GATE" "$CASE10" "not-a-number" "Conformance (Gate 2)" >/dev/null 2>&1 || RC=$?
[ "$RC" = "1" ] || { echo "FAIL case10 expected exit 1 on non-numeric issue number, got $RC"; exit 1; }
[ ! -s "$STUB_LOG" ] || { echo "FAIL case10 expected no tracker calls for a non-numeric issue number, got:"; cat "$STUB_LOG"; exit 1; }

# --- Case 11: verifier.py PASS artifact proceeds through the real gate ------
CASE11_DIR=$(mktemp -d)
VERIFIER11="${CASE11_DIR}/verifier.sh"
cat > "$VERIFIER11" <<'SCRIPT'
#!/usr/bin/env bash
exit 0
SCRIPT
chmod +x "$VERIFIER11"
CASE11_OUT="${WORK}/case11-loop-verdict.md"
PYTHONPATH="${REPO_ROOT}/scripts" python3 -m factory_core.verifier \
  --clone-dir "$CASE11_DIR" --loop-name "integration-loop" \
  --verifier-path "verifier.sh" --side-effect-level 1 \
  run --out "$CASE11_OUT"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE11_OUT" "271" "loop:integration-loop")
[ "$RC" = "0" ] || { echo "FAIL case11 expected exit 0 (verifier PASS), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
rm -rf "$CASE11_DIR"

# --- Case 12: verifier.py BLOCKED artifact (missing verifier path) blocks ----
CASE12_DIR=$(mktemp -d)
CASE12_OUT="${WORK}/case12-loop-verdict.md"
PYTHONPATH="${REPO_ROOT}/scripts" python3 -m factory_core.verifier \
  --clone-dir "$CASE12_DIR" --loop-name "integration-loop" \
  --verifier-path "does-not-exist.sh" --side-effect-level 1 \
  run --out "$CASE12_OUT"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE12_OUT" "271" "loop:integration-loop")
[ "$RC" = "1" ] || { echo "FAIL case12 expected exit 1 (verifier BLOCKED), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
rm -rf "$CASE12_DIR"

# --- Case 13: missing verifier-written artifact — true silent miss, blocks --
CASE13_OUT="${WORK}/case13-does-not-exist.md"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE13_OUT" "271" "loop:integration-loop")
[ "$RC" = "1" ] || { echo "FAIL case13 expected exit 1 (missing artifact), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case13: missing loop verdict must post a failure comment"; cat "$STUB_LOG"; exit 1; }

echo PASS

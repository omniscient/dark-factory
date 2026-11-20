#!/usr/bin/env bash
# scripts/run_config_env.sh (#180): apply_run_config_env replays scheduler.sh's former
# _set_cfg semantics (scheduler mode) and entrypoint.sh's former _epcfg (entrypoint mode).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/run_config_env.sh
source "$REPO_ROOT/scripts/run_config_env.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PASSED=0; FAILED=0
_assert() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED + 1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED + 1))
  fi
}

ENVF="$WORK/run-config.env"
cat > "$ENVF" <<'EOF'
# run-config.env (fixture)
# source: /fixture/config.yaml
T_UNSET=from-config
T_SET_DIFF=from-config
T_SET_EMPTY=from-config
T_SET_SAME=same
T_NULL
T_PIPE=migration|migrate|a=b
T_SUBST=$(touch pwned-marker)
bad-name=x
EOF

# _probe <mode>: apply in a subshell with a fixed starting env; print the resulting state.
_probe() {
  ( cd "$WORK" || exit 9
    unset T_UNSET T_NULL T_PIPE T_SUBST
    T_SET_DIFF=env-val; T_SET_EMPTY=""; T_SET_SAME=same
    apply_run_config_env "$ENVF" "$1" 2>"$WORK/$1.err" >"$WORK/$1.out"
    echo "rc=$?"
    echo "UNSET=${T_UNSET-<unset>}"; echo "DIFF=${T_SET_DIFF}"; echo "EMPTY=[${T_SET_EMPTY}]"
    echo "NULL=[${T_NULL-<unset>}]"; echo "PIPE=${T_PIPE-}"; echo "SUBST=${T_SUBST-}"
    echo "EXPORTED_UNSET=$(env | grep -c '^T_UNSET=')"
    echo "EXPORTED_SAME=$(env | grep -c '^T_SET_SAME=')" )
}
_field() { sed -n "s/^$1=//p" <<<"$2"; }

echo "--- scheduler mode (former _set_cfg) ---"
S=$(_probe scheduler)
_assert "rc 0" "0" "$(_field rc "$S")"
_assert "unset var takes the config value" "from-config" "$(_field UNSET "$S")"
_assert "and is exported" "1" "$(_field EXPORTED_UNSET "$S")"
_assert "set var wins" "env-val" "$(_field DIFF "$S")"
_assert "override is logged" "1" "$(grep -c "^\[config\] T_SET_DIFF=env-val (env override; config has 'from-config')$" "$WORK/scheduler.err")"
_assert "set-to-empty still wins" "[]" "$(_field EMPTY "$S")"
_assert "empty override is logged" "1" "$(grep -c "^\[config\] T_SET_EMPTY= (env override; config has 'from-config')$" "$WORK/scheduler.err")"
_assert "equal value is not logged" "0" "$(grep -c 'T_SET_SAME' "$WORK/scheduler.err")"
_assert "null exports as empty, not unset" "[]" "$(_field NULL "$S")"
_assert "value keeps | and =" "migration|migrate|a=b" "$(_field PIPE "$S")"
_assert "value is never evaluated" '$(touch pwned-marker)' "$(_field SUBST "$S")"
_assert "no command ran" "no" "$([ -e "$WORK/pwned-marker" ] && echo yes || echo no)"
_assert "malformed line skipped with a warning" "1" "$(grep -c 'skipping malformed run-config.env line: bad-name=x' "$WORK/scheduler.err")"
_assert "loaded-from line names the source" "[config] loaded from /fixture/config.yaml" "$(cat "$WORK/scheduler.out")"

echo "--- entrypoint mode (former _epcfg) ---"
E=$(_probe entrypoint)
_assert "rc 0" "0" "$(_field rc "$E")"
_assert "unset var takes the config value" "from-config" "$(_field UNSET "$E")"
_assert "set (bootstrap) var is kept" "env-val" "$(_field DIFF "$E")"
_assert "kept value is logged" "1" "$(grep -c "^\[entrypoint-config\] T_SET_DIFF=env-val (env/bootstrap override; config has 'from-config')$" "$WORK/entrypoint.err")"
_assert "set-to-empty is kept" "[]" "$(_field EMPTY "$E")"
_assert "null config value is skipped" "[<unset>]" "$(_field NULL "$E")"
_assert "equal value is (re-)exported" "1" "$(_field EXPORTED_SAME "$E")"
_assert "loaded-from line" "[entrypoint-config] loaded from /fixture/config.yaml" "$(cat "$WORK/entrypoint.out")"

echo "--- missing / empty / comment-only file, unknown mode ---"
apply_run_config_env "$WORK/nope.env" scheduler 2>/dev/null; _assert "missing file -> 1" "1" "$?"
: > "$WORK/empty.env"
apply_run_config_env "$WORK/empty.env" scheduler 2>/dev/null; _assert "empty file -> 1" "1" "$?"
printf '# source: x\n' > "$WORK/comments.env"
apply_run_config_env "$WORK/comments.env" entrypoint >/dev/null 2>&1; _assert "comment-only file -> 1" "1" "$?"
apply_run_config_env "$ENVF" bogus 2>/dev/null; _assert "unknown mode -> 2" "2" "$?"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]

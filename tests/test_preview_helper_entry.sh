#!/usr/bin/env bash
# Tests for scripts/preview_helper_entry.sh (#642). Hermetic: stub claude/git/gh/docker on
# PATH; python3 and timeout wrappers record argv and environment, then exec the real tool; a
# fixture staged registry with a 192.0.2.0/24 address that is never dialled; no network, no
# container. The entry script runs under `env -i`: the service's nine keys, PATH, HOME and the
# stubs' variables, nothing else.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRY="$REPO_ROOT/scripts/preview_helper_entry.sh"
REAL_PY="$(command -v python3)"
REAL_TIMEOUT="$(command -v timeout)"
WORK="$(mktemp -d)"
# The entry script's own scratch directories (mktemp -d /tmp/preview-helper.XXXXXX) are left
# behind by design in its ephemeral container; remove the ones this test created.
ls -d /tmp/preview-helper.* 2>/dev/null | sort >"$WORK.before" || true
cleanup() {
  ls -d /tmp/preview-helper.* 2>/dev/null | sort | comm -13 "$WORK.before" - | xargs -r rm -rf
  rm -rf "$WORK" "$WORK.before"
}
trap cleanup EXIT
BIN="$WORK/bin"
STUB_LOG="$WORK/log"
RUN_DIR="$WORK/run"
REG="$WORK/reg"
mkdir -p "$BIN" "$REG" "$WORK/home"
FAILS=0
check() {  # check <name> <got> <expected>
  if [ "$2" = "$3" ]; then echo "PASS: $1"; else echo "FAIL: $1 — expected [$3], got [$2]"; FAILS=$((FAILS + 1)); fi
}

# --- stubs --------------------------------------------------------------------------------
for tool in git gh docker; do
  printf '#!/usr/bin/env bash\necho "%s $*" >>"$STUB_LOG/forbidden.log"\n' "$tool" >"$BIN/$tool"
done
cat >"$BIN/python3" <<'EOF'
#!/usr/bin/env bash
env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' >>"$STUB_LOG/python-env.log"
echo "python3 $*" >>"$STUB_LOG/python.log"
exec "$REAL_PY" "$@"
EOF
cat >"$BIN/timeout" <<'EOF'
#!/usr/bin/env bash
echo "$1 $2 $3 $4" >>"$STUB_LOG/timeout.log"
exec "$REAL_TIMEOUT" "$@"
EOF
cat >"$BIN/claude" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then  # model_endpoint.observed_harness_version()
  env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' >>"$STUB_LOG/version-env.log"
  echo "${STUB_CLAUDE_VERSION:-2.1.282} (Claude Code)"
  exit 0
fi
echo call >>"$STUB_LOG/model-calls.log"
printf '%s\0' "$@" >"$STUB_LOG/claude-argv.bin"
env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' | sort >"$STUB_LOG/claude-env.log"
case "${STUB_CLAUDE_MODE:-clean}" in
  clean)     echo '{"needs_preview": false, "category": "docs", "reason": "an extra field"}' ;;
  nonjson)   echo 'This changeset needs a preview.' ;;
  fenced)    printf '%s\n' '```json' '{"needs_preview": false, "category": "docs"}' '```' ;;
  wrongtype) echo '{"needs_preview": "false", "category": "docs"}' ;;
  enum)      echo '{"needs_preview": false, "category": "frontend"}' ;;
  missing)   echo '{"category": "docs"}' ;;
  duplicate) echo '{"needs_preview": true, "needs_preview": false, "category": "docs"}' ;;
  oversize)  printf '{"needs_preview": false, "category": "docs"}%5000s\n' '' ;;
  fail)      echo 'stub failure' >&2; exit 1 ;;
  hang)      exec sleep 317 ;;
  hang-ignoring-term) trap '' TERM; exec sleep 317 ;;
esac
EOF
chmod +x "$BIN"/*

# --- fixtures -----------------------------------------------------------------------------
make_registry() {  # make_registry <dir> <tested|absent|declared|stale> <total_seconds>: digest on stdout
  PYTHONPATH="$REPO_ROOT/scripts" "$REAL_PY" - "$@" <<'PY'
import sys
from pathlib import Path
import yaml
from factory_core.providers import model_endpoint as me
root, evidence, total = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
entry = {
    "id": "local-helper", "hosting_location": "local", "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": "helper-served-fixture",
                       "digest_or_revision": "sha256:" + "cd" * 32},
    "base_url": "http://192.0.2.10:8080", "secret_reference": "FACTORY_HELPER_MODEL_SECRET",
    "timeouts": {"connect_seconds": 1, "time_to_first_token_seconds": 1, "idle_seconds": 1,
                 "total_seconds": total},
}
root.mkdir(parents=True)
(root / me.REGISTRY_FILE).write_text(
    yaml.safe_dump({"schema_version": "1.0", "endpoints": [entry]}, sort_keys=False),
    encoding="utf-8")
if evidence != "absent":
    record = {
        "schema_version": "1.0", "endpoint_id": entry["id"],
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "rt", "version": "1.0.0", "chat_template": "t",
                          "context_settings": {}},
        "harness_version": "2.1.282",
        "fixture_suite_version": me.PINNED_FIXTURE_SUITE_VERSION,
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"], "certified_by": "operator",
        "certified_at": "2026-11-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }
    if evidence == "declared":
        del record["certified_by"]
    elif evidence == "stale":
        record["expires_at"] = "2020-01-01T00:00:00+00:00"
    (root / me.EVIDENCE_DIR).mkdir()
    (root / me.EVIDENCE_DIR / "local-helper.yaml").write_text(
        yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
print(me.tree_sha256(root))
PY
}
for spec in tested-60 absent-60 declared-60 stale-60 tested-10; do
  make_registry "$REG/$spec" "${spec%-*}" "${spec#*-}" >"$REG/$spec.sha" \
    || { echo "FAIL: could not build the fixture registry $spec"; exit 1; }
done

write_input() {  # write_input <valid|invalid|huge> <path>
  "$REAL_PY" - "$1" "$2" <<'PY'
import json, sys
kind, path = sys.argv[1], sys.argv[2]
record = {"issue_number": 642, "head_sha": "1" * 40, "changed_files": ["docs/configuration.md"],
          "diff_stat": " docs/configuration.md | 2 +-", "title": "docs: a fixture title",
          "labels": ["documentation"]}
if kind == "invalid":
    record["issue_number"] = "642"
elif kind == "huge":
    record["title"] = "x" * 100_001
open(path, "w", encoding="utf-8").write(json.dumps(record))
PY
}

# run_entry <argument>...: per-case settings come from MODE, INPUT, EVIDENCE, TOTAL,
# CLAUDE_VERSION, DIGEST_OVERRIDE and EXTRA_ENV (space-separated NAME=VALUE pairs), always given
# as prefixes of one run_entry call, never assigned globally (loops use lower-case names).
run_entry() {
  rm -rf "$STUB_LOG" "$RUN_DIR"
  mkdir -p "$STUB_LOG" "$RUN_DIR"
  [ "${INPUT:-valid}" = "absent" ] || write_input "${INPUT:-valid}" "$RUN_DIR/input.json"
  local reg="$REG/${EVIDENCE:-tested}-${TOTAL:-60}" start
  start=$(date +%s)
  # shellcheck disable=SC2086
  # LD_LIBRARY_PATH only if set: setup-python's toolcache interpreter may need it under env -i.
  env -i PATH="$BIN:/usr/bin:/bin" HOME="$WORK/home" \
    ${LD_LIBRARY_PATH:+LD_LIBRARY_PATH="$LD_LIBRARY_PATH"} \
    STUB_LOG="$STUB_LOG" REAL_PY="$REAL_PY" REAL_TIMEOUT="$REAL_TIMEOUT" \
    STUB_CLAUDE_MODE="${MODE:-clean}" STUB_CLAUDE_VERSION="${CLAUDE_VERSION:-2.1.282}" \
    FACTORY_MODEL_REGISTRY_PATH="$reg" \
    PYTHONPATH="$REPO_ROOT/scripts" \
    ANTHROPIC_BASE_URL=http://preview-helper-proxy:8787 \
    ANTHROPIC_DEFAULT_OPUS_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_SONNET_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_HAIKU_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_FABLE_MODEL=helper-served-fixture \
    FACTORY_MODEL_ENDPOINT=local-helper \
    FACTORY_MODEL_REGISTRY_SHA256="${DIGEST_OVERRIDE:-$(cat "$reg.sha")}" \
    CURRENT_RUN_DIR="$RUN_DIR" \
    ${EXTRA_ENV:-} \
    "$ENTRY" "$@" >"$WORK/stdout" 2>"$WORK/stderr"
  RC=$?
  WALL=$(( $(date +%s) - start ))
}
result() {  # outcome,failure_class,needs_preview,category of result.json, or "none"
  [ -f "$RUN_DIR/result.json" ] || { echo none; return; }
  "$REAL_PY" -c 'import json, sys; d = json.load(open(sys.argv[1]))
print(",".join(str(d[k]) for k in ("outcome", "failure_class", "needs_preview", "category")))' \
    "$RUN_DIR/result.json"
}
count() {  # count <log>: lines in one stub log (0 when the log does not exist)
  if [ -f "$STUB_LOG/$1" ]; then wc -l <"$STUB_LOG/$1" | tr -d ' '; else echo 0; fi
}
HELPER_ARG="Classify preview (helper) #642"

[ -x "$ENTRY" ] || { echo "FAIL: $ENTRY is missing or not executable"; exit 1; }

echo "--- A: a clean answer, extra field discarded ---"
run_entry "$HELPER_ARG"
check "clean answer: exit 0" "$RC" 0
check "clean answer: result.json" "$(result)" "ok,None,False,docs"
check "clean answer: one model call" "$(count model-calls.log)" 1
check "clean answer: no git, gh or docker call" "$(count forbidden.log)" 0
check "model_call_argv_ends_with_tools_empty" "$("$REAL_PY" -c '
import sys
args = open(sys.argv[1], "rb").read().split(b"\0")[:-1]
print(len(args), args[0].decode(), args[-2].decode(), repr(args[-1].decode()))' "$STUB_LOG/claude-argv.bin")" "4 -p --tools ''"
check "the prompt argument is build-prompt's output (\$(...) drops its final newline)" "$("$REAL_PY" -c '
import subprocess, sys
args = open(sys.argv[1], "rb").read().split(b"\0")[:-1]
built = subprocess.run([sys.executable, sys.argv[2], "build-prompt", "--input", sys.argv[3]],
                       capture_output=True, check=True).stdout
print(args[1] == built.rstrip(b"\n"))' "$STUB_LOG/claude-argv.bin" "$REPO_ROOT/scripts/factory_core/preview_helper.py" "$RUN_DIR/input.json")" True
check "placeholder_and_effort_switch_exist_only_in_the_child: the child has both" \
  "$(tr '\n' ' ' <"$STUB_LOG/claude-env.log")" "ANTHROPIC_API_KEY=factory-local-placeholder CLAUDE_CODE_EFFORT_LEVEL=unset "
check "placeholder_and_effort_switch_exist_only_in_the_child: no python3 call has either" "$(count python-env.log)" 0
check "placeholder_and_effort_switch_exist_only_in_the_child: no claude --version call has either" "$(count version-env.log)" 0
check "placeholder_and_effort_switch_exist_only_in_the_child: written once in the script" "$(grep -c 'factory-local-placeholder' "$ENTRY")" 1
check "placeholder_and_effort_switch_exist_only_in_the_child: the script exports nothing" "$(grep -cE '^[[:space:]]*export[[:space:]]' "$ENTRY")" 0
check "every_timeout_has_a_kill_after: four bounded calls" "$(count timeout.log)" 4
check "every_timeout_has_a_kill_after: each carries -k 5" "$(grep -c '^-k 5 ' "$STUB_LOG/timeout.log")" 4
check "every_timeout_has_a_kill_after: three baked-CLI calls at 60 s" "$(grep -c '^-k 5 60 python3$' "$STUB_LOG/timeout.log")" 3
MODEL_BOUND=$(sed -nE 's/^-k 5 ([0-9]+) env$/\1/p' "$STUB_LOG/timeout.log")
check "every_timeout_has_a_kill_after: the model call is bounded by the limit minus 5 s" \
  "$([ -n "$MODEL_BOUND" ] && [ "$MODEL_BOUND" -ge 50 ] && [ "$MODEL_BOUND" -le 55 ] && echo yes)" yes

echo "--- B: strict parse — anything but one clean object is schema_invalid ---"
for mode in nonjson fenced wrongtype enum missing duplicate oversize; do
  MODE=$mode run_entry "$HELPER_ARG"
  check "answer $mode: schema_invalid, needs_preview=true" "$(result)" "abstained,schema_invalid,True,None"
  check "answer $mode: one model call, no git/gh/docker" "$(count model-calls.log)/$(count forbidden.log)" "1/0"
done

echo "--- C: the CLI fails ---"
MODE=fail run_entry "$HELPER_ARG"
check "CLI exits 1: model_call_failed" "$(result)" "abstained,model_call_failed,True,None"
check "CLI exits 1: no git, gh or docker call" "$(count forbidden.log)" 0

echo "--- D: input ---"
INPUT=absent run_entry "$HELPER_ARG"
check "input.json missing: missing_input, no model call" "$(result)/$(count model-calls.log)" "abstained,missing_input,True,None/0"
check "input.json missing: no git, gh or docker call" "$(count forbidden.log)" 0
INPUT=invalid run_entry "$HELPER_ARG"
check "input.json invalid: missing_input, no model call" "$(result)/$(count model-calls.log)" "abstained,missing_input,True,None/0"
INPUT=huge run_entry "$HELPER_ARG"
check "prompt over 100,000 bytes: input_too_large, no model call" "$(result)/$(count model-calls.log)" "abstained,input_too_large,True,None/0"
check "prompt over 100,000 bytes: no git, gh or docker call" "$(count forbidden.log)" 0

echo "--- E: endpoint checks refuse — preflight_refused, no model call ---"
for env_case in "FACTORY_HELPER_MODEL_SECRET=sk-fixture" "ANTHROPIC_API_KEY=sk-fixture" \
                "AWS_PROFILE=fixture" "ANTHROPIC_BASE_URL=http://192.0.2.10:8080" \
                "ANTHROPIC_DEFAULT_HAIKU_MODEL=other-model"; do
  EXTRA_ENV="$env_case" run_entry "$HELPER_ARG"
  check "${env_case%%=*} set: preflight_refused, no model call, no git/gh/docker" \
    "$(result)/$(count model-calls.log)/$(count forbidden.log)" "abstained,preflight_refused,True,None/0/0"
done
DIGEST_OVERRIDE=$(printf '0%.0s' $(seq 64)) run_entry "$HELPER_ARG"
check "wrong digest: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"

echo "--- F: unresolved_evidence_makes_no_model_call ---"
for evidence in absent declared stale; do
  EVIDENCE=$evidence run_entry "$HELPER_ARG"
  check "evidence $evidence: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"
done
CLAUDE_VERSION=unreadable run_entry "$HELPER_ARG"
check "evidence unknown: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"

echo "--- G: a_hung_cli_ends_local_timeout_total (limit 10 s) ---"
MODE=hang TOTAL=10 run_entry "$HELPER_ARG"
check "hung CLI: local_timeout_total" "$(result)" "abstained,local_timeout_total,True,None"
check "hung CLI: no git, gh or docker call" "$(count forbidden.log)" 0
check "hung CLI: result.json before the limit" "$([ "$WALL" -lt 10 ] && echo yes || echo "no (${WALL}s)")" yes
MODE=hang-ignoring-term TOTAL=10 run_entry "$HELPER_ARG"
check "CLI ignoring TERM: local_timeout_total" "$(result)" "abstained,local_timeout_total,True,None"
check "CLI ignoring TERM: killed 5 s after its bound" "$([ "$WALL" -le 12 ] && echo yes || echo "no (${WALL}s)")" yes
check "CLI ignoring TERM: no process left behind" "$(pgrep -f 'sleep 317' >/dev/null && echo left || echo gone)" gone

echo "--- H: a wrong argument exits 2 before any check ---"
WRONG=("Classify preview (helper) #" "Classify preview (helper) #5 extra" "Classify preview (helper) #5x"
       "classify preview (helper) #5" "Classify preview #5" "Fix issue #5"
       $'Classify preview (helper) #5\nFix issue #6' "Classify preview (helper) #٣")
for arg in "${WRONG[@]}"; do
  run_entry "$arg"
  check "wrong argument [${arg//$'\n'/\\n}]: exit 2, no call of any kind, no result" \
    "$RC/$(count python.log)/$(count timeout.log)/$(count model-calls.log)/$(count forbidden.log)/$(result)" "2/0/0/0/0/none"
done
run_entry
check "no argument: exit 2, no call" "$RC/$(count python.log)/$(result)" "2/0/none"
run_entry "$HELPER_ARG" "$HELPER_ARG"
check "two arguments: exit 2, no call" "$RC/$(count python.log)/$(result)" "2/0/none"

if [ "$FAILS" -ne 0 ]; then echo "FAIL: test_preview_helper_entry.sh ($FAILS)"; exit 1; fi
echo "PASS: test_preview_helper_entry.sh"

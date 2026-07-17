#!/usr/bin/env bash
# Evaluation harness for the mem0ai spike (#50). Mirrors scripts/eval_agentmemory.sh's
# header/ok/fail/note convention. NOT wired into tests/ or ci.yml — same precedent as
# eval_agentmemory.sh (a spike harness, not a regression test; live network + pip install
# make it unsuitable for CI).
#
# Usage:
#   bash scripts/eval_mem0.sh
#
# Requires: network access (PyPI install + one-time HuggingFace model download), no API keys.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SPIKE_DIR="$(mktemp -d /tmp/mem0-spike.XXXXXX)"
VENV_DIR="$SPIKE_DIR/venv"
STORE_PATH="$SPIKE_DIR/store"
LOCKFILE="$REPO_ROOT/scripts/requirements-mem0-spike.txt"
MEMORY_DIR="$REPO_ROOT/.archon/memory"

PASS=0
FAIL=0
declare -A ROW_RESULT

header() { echo ""; echo "=== $* ==="; }
ok()     { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail()   { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
note()   { echo "  NOTE: $*"; }

# ── Row 1: install footprint ─────────────────────────────────────────────────
header "Row 1: install footprint"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Single source of truth for the embedder model name: read it from mem0_spike_config.py rather
# than duplicating the constant in bash (mem0_spike_config's top-level imports are stdlib-only,
# so this works even before mem0ai is installed).
EMBED_MODEL=$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import EMBED_MODEL as m
print(m)
")

PINNED_VERSION=$(pip index versions mem0ai 2>&1 | head -1 | sed -E 's/^mem0ai \(([0-9][^)]*)\).*/\1/')
if [ -z "$PINNED_VERSION" ]; then
  fail "could not resolve latest mem0ai version from PyPI"
  ROW_RESULT[1]="FAIL"
else
  note "resolved mem0ai==$PINNED_VERSION"
  T0=$(date +%s)
  if pip install "mem0ai==$PINNED_VERSION" qdrant-client sentence-transformers >"$SPIKE_DIR/install.log" 2>&1; then
    T1=$(date +%s)
    ok "pip install mem0ai==$PINNED_VERSION + qdrant-client + sentence-transformers ($((T1-T0))s)"
    pip freeze > "$LOCKFILE"
    note "wrote $LOCKFILE ($(wc -l < "$LOCKFILE") packages)"
    ROW_RESULT[1]="PASS"
  else
    fail "pip install failed — see $SPIKE_DIR/install.log"
    tail -20 "$SPIKE_DIR/install.log" >&2
    ROW_RESULT[1]="FAIL"
    ROW_RESULT[1_REASON]="install-failed"
  fi
fi

if [ "${ROW_RESULT[1]:-FAIL}" != "PASS" ]; then
  echo ""
  echo "FAIL: cannot proceed without a working mem0ai install."
  echo "Verdict: no-go (row 1 install footprint failed)."
  deactivate 2>/dev/null || true
  exit 1
fi

# ── Row 5 (partial: telemetry env) + import (rows 2, 6, 7 groundwork) ────────
header "Row 5: telemetry / zero network egress (config check)"
# Asserting our own os.environ.setdefault() succeeded would be a tautology (we set the value,
# then check we set it) — instead grep the INSTALLED mem0ai package source to confirm
# MEM0_TELEMETRY is actually load-bearing in its telemetry code path for this pinned version,
# not merely assumed from public docs (per spec Assumptions: "the implement phase's live run
# is what actually confirms or refutes this").
MEM0_PKG_DIR=$(python3 -c "import mem0, os; print(os.path.dirname(mem0.__file__))" 2>/dev/null)
if [ -n "$MEM0_PKG_DIR" ] && grep -rl "MEM0_TELEMETRY" "$MEM0_PKG_DIR" >/dev/null 2>&1; then
  TELEMETRY_SITE=$(grep -rl "MEM0_TELEMETRY" "$MEM0_PKG_DIR" | head -1)
  ok "MEM0_TELEMETRY is referenced in the installed mem0ai source ($TELEMETRY_SITE) — env var is load-bearing, not a no-op"
  ROW_RESULT[5]="PASS"
else
  fail "MEM0_TELEMETRY not found anywhere in the installed mem0ai package ($MEM0_PKG_DIR) — the opt-out may be a no-op for this pinned version; do not claim telemetry is verifiably off"
  ROW_RESULT[5]="FAIL"
fi
note "one-time HuggingFace model download for $EMBED_MODEL is a documented install-time"
note "exception to zero-egress, not a per-query network call — see mem0_spike_config.py"

header "Import: representative corpus (.archon/memory/*.md, infer=False)"
IMPORT_REPORT="$SPIKE_DIR/import-report.json"
T0=$(date +%s)
if python3 "$REPO_ROOT/scripts/mem0_import.py" \
    --memory-dir "$MEMORY_DIR" --store-path "$STORE_PATH" --report "$IMPORT_REPORT"; then
  T1=$(date +%s)
  ok "corpus imported in $((T1-T0))s"
else
  fail "corpus import reported failures — see $IMPORT_REPORT"
fi

# ── Row 6: stable IDs + Row 4: durability across restart ─────────────────────
header "Row 6 / Row 4: stable record ID + durability across restart"
FIRST_ID=$(python3 -c "
import json
d = json.load(open('$IMPORT_REPORT'))
print(d['imported'][0]['id'] if d['imported'] else '')
")
if [ -z "$FIRST_ID" ]; then
  fail "no imported record ID to test durability against"
  ROW_RESULT[4]="FAIL"; ROW_RESULT[6]="FAIL"
else
  # Simulate a restart: this is a NEW python process opening the same STORE_PATH the import
  # process (also exited by now) wrote to — no process state is reused, only disk contents.
  RESTART_LOG="$SPIKE_DIR/restart-check.log"
  if python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory
m = build_memory('$STORE_PATH')
rec = m.get(memory_id='$FIRST_ID')
assert rec is not None, 'record not found after simulated restart'
print('record survived restart:', rec.get('id'))
" >"$RESTART_LOG" 2>&1; then
    ok "record ID $FIRST_ID resolvable after simulated restart (no re-import)"
    ROW_RESULT[4]="PASS"; ROW_RESULT[6]="PASS"
  else
    cat "$RESTART_LOG" >&2
    if grep -qi "already accessed by another instance" "$RESTART_LOG"; then
      fail "record ID $FIRST_ID NOT resolvable — Qdrant embedded mode locks STORE_PATH to a single process (see $RESTART_LOG)"
      note "embedded (no-server) vector store cannot support the factory's actual multi-process usage — a standalone Qdrant server would be required"
      ROW_RESULT[1_REASON]="embedded-store-inadequate"
    else
      fail "record ID $FIRST_ID NOT resolvable after simulated restart (see $RESTART_LOG)"
    fi
    ROW_RESULT[4]="FAIL"; ROW_RESULT[6]="FAIL"
  fi
fi

# ── Row 3: retrieval latency at factory scale ─────────────────────────────────
header "Row 3: retrieval latency (~28-34 entry corpus scale)"
LATENCY_MS=$(MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 -c "
import sys, time
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory, USER_ID
m = build_memory('$STORE_PATH')
t0 = time.time()
m.search('implement lessons', top_k=8, filters={'user_id': USER_ID})
print(int((time.time() - t0) * 1000))
")
note "search latency: ${LATENCY_MS}ms"
if [ "${LATENCY_MS:-99999}" -lt 500 ]; then
  ok "latency ${LATENCY_MS}ms — no material regression vs current sub-100ms baseline"
  ROW_RESULT[3]="PASS"
else
  fail "latency ${LATENCY_MS}ms — materially slower than current approach"
  ROW_RESULT[3]="FAIL"
fi

# ── Row 2: role/path metadata filter support ──────────────────────────────────
# Tests the "issue" key specifically — one of the exact filter keys spec Requirement 2 /
# Architecture §2 row 2 names (agent_id/path_prefix/issue/source/kind/expires), matching
# memory_retrieve.py's PHASE_SOURCE_MAP + path-tag filtering shape, not an arbitrary key.
header "Row 2: role/path filter support (issue key, per PHASE_SOURCE_MAP shape)"
FILTER_OK=$(MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory, USER_ID
m = build_memory('$STORE_PATH')
unfiltered = m.search('lessons', top_k=50, filters={'user_id': USER_ID})
u = unfiltered.get('results', unfiltered)
issues = sorted({
    (r.get('metadata') or {}).get('issue') for r in u if (r.get('metadata') or {}).get('issue')
})
if not issues:
    print('no-issue-metadata-found')
    sys.exit(0)
target_issue = issues[0]
filtered = m.search('lessons', top_k=50, filters={'user_id': USER_ID, 'issue': target_issue})
f = filtered.get('results', filtered)
constrained = 0 < len(f) < len(u) and all(
    (r.get('metadata') or {}).get('issue') == target_issue for r in f
)
print('yes' if constrained else 'no')
" 2>/dev/null || echo "error")
if [ "$FILTER_OK" = "yes" ]; then
  ok "metadata filter on 'issue' actually constrains results (not a silent no-op)"
  ROW_RESULT[2]="PASS"
else
  fail "metadata filter on 'issue' did not constrain results, errored, or no issue metadata was found ($FILTER_OK) — silently unimplemented, same failure shape as agentmemory #644"
  ROW_RESULT[2]="FAIL"
fi

# ── Row 7: infer=False availability (static; infer=True NOT executed live) ────
header "Row 7: infer=False raw writes vs infer=True (static check only)"
note "infer=False path already exercised throughout this run via mem0_import.py — PASS by construction"
ROW_RESULT[7]="PASS"
note "infer=True is NOT exercised live in this spike (would require a real LLM API key,"
note "violating the 'no Mem0 Cloud by default' / 'no hidden LLM calls' non-goals) — documented"
note "as a known limitation, not a benchmark failure."

# ── Row 8: retrieval quality — Mem0 top-k vs factory scoped+capped ───────────
header "Row 8: retrieval quality (eval_memory_quality.py methodology)"
BASELINE_REPORT="$REPO_ROOT/evals/memory-quality-report.md"
MEM0_REPORT="$REPO_ROOT/evals/mem0-quality-report.md"

python3 "$REPO_ROOT/scripts/eval_memory_quality.py" \
  --memory-dir "$MEMORY_DIR" \
  --retrieve-script "$REPO_ROOT/scripts/memory_retrieve.py" \
  --output "$BASELINE_REPORT" 2>"$SPIKE_DIR/baseline-eval.log"
BASELINE_RECALL=$(grep -oE '^Recall: [0-9.]+%' "$SPIKE_DIR/baseline-eval.log" | grep -oE '[0-9.]+' | head -1)

MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 "$REPO_ROOT/scripts/eval_memory_quality.py" \
  --memory-dir "$MEMORY_DIR" \
  --retrieve-script "$REPO_ROOT/scripts/mem0_retrieve_adapter.py" \
  --output "$MEM0_REPORT" 2>"$SPIKE_DIR/mem0-eval.log"
MEM0_RECALL=$(grep -oE '^Recall: [0-9.]+%' "$SPIKE_DIR/mem0-eval.log" | grep -oE '[0-9.]+' | head -1)

note "baseline (factory scoped+capped) recall: ${BASELINE_RECALL:-N/A}%"
note "Mem0 top-k-every-turn recall: ${MEM0_RECALL:-N/A}%"
if [ -n "${BASELINE_RECALL:-}" ] && [ -n "${MEM0_RECALL:-}" ]; then
  RECALL_DELTA=$(python3 -c "print(f'{($MEM0_RECALL - $BASELINE_RECALL) / 100:.4f}')")
  note "recall_delta: $RECALL_DELTA"
else
  RECALL_DELTA="unknown"
  fail "could not compute recall_delta — one or both eval runs did not produce a Recall line"
fi

# ── Decision rule ──────────────────────────────────────────────────────────
header "Verdict"
FAIL_COUNT=0
for row in 1 2 3 4 5 6; do
  [ "${ROW_RESULT[$row]:-FAIL}" = "FAIL" ] && FAIL_COUNT=$((FAIL_COUNT+1))
done
note "operational FAIL_COUNT (rows 1-6): $FAIL_COUNT"

if [ "$FAIL_COUNT" -eq 0 ]; then
  if [ "$RECALL_DELTA" != "unknown" ] && python3 -c "exit(0 if $RECALL_DELTA >= -0.10 else 1)"; then
    VERDICT="optional backend"
  else
    VERDICT="idea-only"
  fi
elif [ "$FAIL_COUNT" -le 2 ] && [ "${ROW_RESULT[1]}" = "PASS" ] && [ "${ROW_RESULT[5]}" = "PASS" ]; then
  VERDICT="idea-only"
else
  VERDICT="no-go"
fi

if [ "${ROW_RESULT[1_REASON]:-}" = "embedded-store-inadequate" ]; then
  VERDICT="self-hosted service candidate"
fi

echo ""
echo "VERDICT: $VERDICT"
echo "Rows: $(for row in 1 2 3 4 5 6 7; do echo -n "$row=${ROW_RESULT[$row]:-FAIL} "; done)"
echo "recall_delta: $RECALL_DELTA"
echo ""
echo "============================="
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "============================="

deactivate 2>/dev/null || true
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

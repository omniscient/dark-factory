# Plan: Gate `push-and-pr` and `status-in-review` on the Conformance/Code-Review Verdict File

**Issue:** #271
**Spec:** [docs/superpowers/specs/2026-07-28-conformance-review-gate-node-design.md](../specs/2026-07-28-conformance-review-gate-node-design.md)
**Status:** plan

## Goal

Stop `push-and-pr` from creating a PR — and `status-in-review` from moving an issue to
**In Review** — when the upstream `conformance`/`code-review` gate actually halted, even
though the DAG reports that upstream `command:` node as `dag_node_completed`. `conformance`
(Gate 2) and `code-review` (Gate 3) are Claude-agent turns, not raw subprocesses; a `command:`
node's internal `exit 1` does not reliably surface as node failure to the Archon executor —
the same bug class issue #212 already fixed once for `refine-push`/`plan-push-and-advance` by
gating on a durable, independently-checkable artifact instead of trusting node completion. This
plan applies the identical principle one gate later: two new `bash:` gate nodes read the
`STATUS:` verdict line each gate command already writes (via `scripts/gate_lib.sh`'s
`emit_verdict()`) to `conformance.md`/`review.md`, and block `push-and-pr`/`status-in-review` on
anything other than `PASS`/`SKIPPED`/`ERROR` — including a missing verdict file, which is the
literal shape of the reported bug (PR #270).

**Build constraint (carried from #212's plan):** `workflows/archon-dark-factory.yaml` is baked
into the factory image and only materializes into a fresh clone's `.archon/workflows/` when the
clone doesn't already provide its own copy. This plan's YAML edit takes effect on the next
`docker compose build` + scheduler/run image redeploy, not merely on merge. No task below
performs that redeploy — deployment is outside this plan's scope (`deploy/` is human-only per
`CLAUDE.md`).

## Architecture

Today: `push-and-pr` is `depends_on: [conformance]` (`workflows/archon-dark-factory.yaml:1131`,
default `all_success`); `status-in-review` is `depends_on: [push-and-pr, push-resolve,
code-review, revise-advisory]` (`:1195`, `trigger_rule: none_failed_min_one_success`, an
existing OR-join tolerating the `resolve` vs `new`/`continue` mutually-exclusive branches).
Mechanically this *should* skip `push-and-pr` when `conformance` fails — it doesn't, because
`conformance` doesn't actually fail at the node level even when its own Phase 5 halts (adds
`needs-discussion`, moves the board to Blocked, `exit 1`) — the DAG still sees
`dag_node_completed`.

The fix adds two thin `bash:` gate nodes, each delegating to one new generic script — the same
shape as the existing `enforce-budget-*` nodes wrapping `scripts/budget_gate.sh`, and sibling to
the #212-era `scripts/push_gate_check.sh`:

```
conformance ──▶ conformance-gate ──▶ push-and-pr ──▶ budget-code-review ──▶ ... ──▶ code-review ──▶ revise-advisory
                     │                                                                    │              │
              reads conformance.md                                                        └──▶ review-gate ◀┘
              STATUS: PASS|SKIPPED|ERROR → exit 0                                               reads review.md
              STATUS: BLOCKED|missing/unparseable → exit 1                                      same STATUS rule
                                                                                                       │
                                                                          push-and-pr ─────┐           ▼
                                                                          push-resolve ────┼──▶ status-in-review
                                                                          code-review ─────┤   (none_failed_min_one_success,
                                                                          revise-advisory ─┤    unchanged — additive edge)
                                                                          review-gate ─────┘
```

Unlike `push_gate_check.sh` (which always exits 0 and lets its caller branch on stdout, because
it's *finding* an artifact for a node that does other work too), `verdict_gate_check.sh`'s own
exit code **is** the gate signal — the calling nodes must not wrap it in `|| true`. The script
reuses the codebase's existing `tracker get --fields labels | jq -r '.labels[].name' | grep -Fxc
'needs-discussion'` idiom (already used inline in `refine-push`, `workflows/archon-dark-factory.yaml:437-439`)
for the live re-check, and the existing `tracker comment --marker --body-file` idiom for the
idempotent failure-comment upsert — but the label re-check here selects only the **message**
(post a comment or don't), never the pass/fail decision, which is a deliberate correction from
the #212 precedent (there the label selected the decision itself).

`status-in-review`'s `depends_on` change is **additive** (`+ review-gate`), not a replacement of
`code-review`/`revise-advisory` — keeping those as direct dependencies preserves today's
executor-level hard-failure blocking, since cascade-skip propagation through `review-gate` at a
`none_failed_min_one_success` join is unverified executor semantics. `report`'s `depends_on:
[status-in-review, code-review]` is unchanged (see spec's "Accepted trade-offs": when
`conformance-gate` blocks, `report`'s OR-join is skipped entirely and no run-summary posts —
accepted, matches the #212 precedent).

Neither new node needs a `trigger_rule`: `conformance-gate`'s only upstream (`conformance`) is
not part of a mutually-exclusive branch, and `review-gate`'s two upstreams (`code-review`,
`revise-advisory`) always run together in the `new`/`continue` branch (`revise-advisory` itself
always exits 0 — fail-open by design). So `scripts/check_workflow_dag.py`'s
`REQUIRED_OR_JOIN_NODES` sync tripwire needs no update — `status-in-review` already carries
`trigger_rule` today and that count doesn't change.

## Tech Stack

- Bash (`scripts/verdict_gate_check.sh`, two new DAG node bodies in
  `workflows/archon-dark-factory.yaml`)
- `scripts/factory_core/providers/cli.py` (existing `tracker get` / `tracker comment --marker`
  subcommands — no CLI changes needed) and `scripts/factory_core/cli.py`'s existing `marker`
  subcommand (footer text)
- Bash test harness with an exported `python3` stub function (`tests/test_verdict_gate_check.sh`),
  mirroring the established convention in `tests/test_scheduler.sh` (`export -f python3`,
  pattern-match on `*providers/cli.py*`) and `tests/test_budget_gate.sh` (case-based
  `mktemp -d` fixtures + `echo PASS`) — needed here (unlike #212's `push_gate_check.sh`, which
  is pure git/no network calls and gets a plain pytest fixture) because `verdict_gate_check.sh`
  itself calls the tracker CLI
- pytest (`tests/test_verdict_gate_dag.py` — static YAML content assertions, modeled on
  `tests/test_push_gate_dag.py`)

## File Structure

| File | Change |
|---|---|
| `scripts/verdict_gate_check.sh` | **New.** Verdict-file gate check, shared by both new DAG nodes. |
| `tests/test_verdict_gate_check.sh` | **New.** Bash execution tests against the script, tracker CLI stubbed via exported `python3` function. |
| `.github/workflows/ci.yml` | Add `- run: bash tests/test_verdict_gate_check.sh` under the `tests` job (a new `.sh` test file is invisible to `python -m pytest tests/` and must be wired explicitly — the exact gap #183 hit for `test_budget_gate.sh`). |
| `workflows/archon-dark-factory.yaml` | New `conformance-gate` node (~after L964); new `review-gate` node (~after L1179); `push-and-pr` `depends_on` → `[conformance-gate]` (~L1131); `status-in-review` `depends_on` → additive `+ review-gate` (~L1195). |
| `tests/test_verdict_gate_dag.py` | **New.** Static content assertions on the two new nodes and the two changed `depends_on` lists, mirroring `tests/test_push_gate_dag.py`. |
| `commands/dark-factory-conformance.md` | Phase 5 step 5 prose: note that enforcement now lives in `conformance-gate`, not this `exit 1`. |
| `commands/dark-factory-code-review.md` | Phase 6 step 6 prose: same doc update for `review-gate`. |

No other files are created or modified. `scripts/check_workflow_dag.py`'s
`REQUIRED_OR_JOIN_NODES` is explicitly untouched (see Architecture).

---

## Task 1: Add `scripts/verdict_gate_check.sh` (verdict-file gate check), test-first

**Files:** `tests/test_verdict_gate_check.sh`, `scripts/verdict_gate_check.sh`, `.github/workflows/ci.yml`

### Step 1.1 — write the failing test

Create `tests/test_verdict_gate_check.sh`:

```bash
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

echo PASS
```

### Step 1.2 — verify it fails

```bash
chmod +x tests/test_verdict_gate_check.sh
bash tests/test_verdict_gate_check.sh
```

Expected: fails immediately (`scripts/verdict_gate_check.sh: No such file or directory`) — the
script does not exist yet.

### Step 1.3 — implement `scripts/verdict_gate_check.sh`

```bash
#!/usr/bin/env bash
# Gate a downstream DAG node on the STATUS: verdict a gate command already wrote
# (scripts/gate_lib.sh's emit_verdict()) to conformance.md/review.md, instead of
# trusting the upstream command: node's own completion status — a command: node's
# internal `exit 1` does not reliably surface to the Archon executor (#212, reproduced
# again one gate later for push-and-pr/status-in-review in #271).
#
# Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>
#   <verdict-file>   e.g. $ARTIFACTS_DIR/conformance.md or $ARTIFACTS_DIR/review.md
#   <issue-number>   for the live needs-discussion re-check and the silent-death comment
#   <gate-label>     human string for the comment, e.g. "Conformance (Gate 2)"
#
# Exit 0 (proceed): the file exists and its STATUS: line is PASS, SKIPPED, or ERROR
#   (ERROR only appears in review.md — code_review.fail_open's contract is "never
#   block", per commands/dark-factory-code-review.md).
# Exit 1 (block): STATUS: is BLOCKED, or the file is missing/unparseable. A live
#   needs-discussion re-check decides messaging only, never the block decision:
#     - BLOCKED, or missing+needs-discussion-present: exit 1 quietly — the
#       originating phase (conformance/code-review/validate's blast-radius gate)
#       already posted its own comment.
#     - missing+needs-discussion-absent (true silent death, nothing upstream
#       explained anything): post an idempotent <!-- df-push-gate-failure --> marker
#       comment, then exit 1.
#
# The exit code IS the gate signal for the caller (unlike push_gate_check.sh, which
# always exits 0 and lets its caller branch on stdout) — do not wrap this call in `|| true`.
set -uo pipefail

VERDICT_FILE="${1:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"
ISSUE_NUM="${2:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"
GATE_LABEL="${3:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"

STATUS=""
if [ -f "$VERDICT_FILE" ]; then
  STATUS=$(grep -m1 '^STATUS:' "$VERDICT_FILE" 2>/dev/null | awk '{print $2}')
fi

case "$STATUS" in
  PASS|SKIPPED|ERROR)
    exit 0
    ;;
esac

# Guard against a non-numeric issue number (e.g. a stringified "null" from a bad
# tracker lookup) reaching the tracker CLI or a comment marker — fail closed with no
# tracker call, mirroring push_gate_check.sh's identical guard.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    echo "verdict_gate_check.sh: ${GATE_LABEL} — issue number '${ISSUE_NUM}' is not numeric; failing closed without a tracker call." >&2
    exit 1
    ;;
esac

# Blocking path: STATUS is BLOCKED, or missing/unparseable. Live-check needs-discussion
# for messaging only — this never changes the block decision itself.
HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE_NUM" --fields labels 2>/dev/null \
  | jq -r '.labels[].name' 2>/dev/null \
  | grep -Fxc 'needs-discussion' || true)

if [ "$STATUS" = "BLOCKED" ] || [ "${HAS_NEEDS_DISCUSSION:-0}" -gt 0 ]; then
  echo "verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); upstream already communicated, no comment needed." >&2
  exit 1
fi

echo "verdict_gate_check.sh: ${GATE_LABEL} verdict missing/unparseable for issue #${ISSUE_NUM} and no needs-discussion label — true silent death, posting failure comment." >&2
_FOOTER=$(python3 "${SCRIPT_DIR}/factory_core/cli.py" marker factory 2>/dev/null || echo "")
_FAIL_BODY="<!-- df-push-gate-failure -->
## ${GATE_LABEL} — Blocked

No verdict was recorded for this run (\`${VERDICT_FILE}\` is missing or unparseable). Treating this as a hard block rather than advancing silently.

\`\`\`bash
# Retry manually if needed
docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"
\`\`\`

---
${_FOOTER}"
TMPFILE=$(mktemp /tmp/push-gate-failure-XXXXXX.md)
printf '%s' "$_FAIL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "<!-- df-push-gate-failure -->" --body-file "$TMPFILE"
rm -f "$TMPFILE"
exit 1
```

```bash
chmod +x scripts/verdict_gate_check.sh
```

### Step 1.4 — verify the tests pass

```bash
bash tests/test_verdict_gate_check.sh
```

Expected output: `PASS`.

### Step 1.5 — wire the new `.sh` test into CI

`python -m pytest tests/ -v` never discovers `.sh` test files — each one needs its own explicit
line in `.github/workflows/ci.yml` (the exact gap issue #183 hit for `test_budget_gate.sh`,
fixed one commit before this plan). In `.github/workflows/ci.yml`, under the `tests` job, add a
line immediately after the existing `test_budget_gate.sh` line:

```yaml
      - run: bash tests/test_budget_gate.sh
      - run: bash tests/test_verdict_gate_check.sh
```

### Step 1.6 — commit

```bash
git add scripts/verdict_gate_check.sh tests/test_verdict_gate_check.sh .github/workflows/ci.yml
git commit -m "feat(workflow): add verdict_gate_check.sh — verdict-file gate check for push-and-pr/status-in-review (#271)"
```

---

## Task 2: Wire `conformance-gate` / `review-gate` into the DAG, test-first

**Files:** `tests/test_verdict_gate_dag.py`, `workflows/archon-dark-factory.yaml`

### Step 2.1 — write the failing tests

Create `tests/test_verdict_gate_dag.py`:

```python
"""Static content assertions for the conformance-gate/review-gate DAG nodes (#271),
mirroring the tests/test_push_gate_dag.py convention for testing DAG bash-node bodies
without executing them."""
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id,upstream,verdict_file,label", [
    ("conformance-gate", ["conformance"], "conformance.md", "Conformance (Gate 2)"),
    ("review-gate", ["code-review", "revise-advisory"], "review.md", "Code Review (Gate 3)"),
])
class TestVerdictGateNodes:
    def test_node_exists_and_calls_script(self, node_id, upstream, verdict_file, label):
        nodes = _workflow_nodes()
        assert node_id in nodes, f"'{node_id}' node not found in workflow"
        bash = nodes[node_id]["bash"]
        assert "verdict_gate_check.sh" in bash, f"'{node_id}' must call verdict_gate_check.sh"
        assert verdict_file in bash, f"'{node_id}' must pass the '{verdict_file}' artifact path"
        assert label in bash, f"'{node_id}' must pass the gate label '{label}'"

    def test_node_depends_on_and_when(self, node_id, upstream, verdict_file, label):
        node = _workflow_nodes()[node_id]
        assert node["depends_on"] == upstream
        assert "'new'" in node["when"] and "'continue'" in node["when"]
        assert node["timeout"] == 30000
        assert "trigger_rule" not in node

    def test_node_not_wrapped_in_or_true(self, node_id, upstream, verdict_file, label):
        bash = _workflow_nodes()[node_id]["bash"]
        gate_line = next(
            line for line in bash.splitlines()
            if line.strip().startswith("bash ") and "verdict_gate_check.sh" in line
        )
        assert "|| true" not in bash, \
            f"'{node_id}': verdict_gate_check.sh's exit code IS the gate signal, must not be swallowed"
        assert gate_line  # sanity: the line was actually found


def test_push_and_pr_depends_on_conformance_gate():
    node = _workflow_nodes()["push-and-pr"]
    assert node["depends_on"] == ["conformance-gate"]


def test_status_in_review_depends_on_is_additive():
    node = _workflow_nodes()["status-in-review"]
    assert node["depends_on"] == [
        "push-and-pr", "push-resolve", "code-review", "revise-advisory", "review-gate",
    ]
    assert node["trigger_rule"] == "none_failed_min_one_success"


def test_report_depends_on_unchanged():
    """report is the node most likely to be 'helpfully' rewired during implementation —
    the spec explicitly declares it untouched (report's OR-join skips entirely, with no
    task-list edit, when conformance-gate blocks upstream)."""
    node = _workflow_nodes()["report"]
    assert node["depends_on"] == ["status-in-review", "code-review"]


def test_dag_validator_passes():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check
    errors = check(_WORKFLOW)
    assert errors == [], "\n".join(errors)


def test_dag_or_join_node_count_unchanged():
    """conformance-gate/review-gate are plain all_success nodes; adding them must not
    change the count of trigger_rule-bearing nodes check_workflow_dag.py tracks."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import REQUIRED_OR_JOIN_NODES
    nodes = _workflow_nodes()
    with_rule = [n for n in nodes.values() if "trigger_rule" in n]
    assert len(with_rule) == len(REQUIRED_OR_JOIN_NODES)
```

### Step 2.2 — verify the tests fail

```bash
python -m pytest tests/test_verdict_gate_dag.py -v
```

Expected: every `TestVerdictGateNodes` case fails/errors (`conformance-gate`/`review-gate` don't
exist yet); `test_push_and_pr_depends_on_conformance_gate` and
`test_status_in_review_depends_on_is_additive` fail (current `depends_on` lists don't match);
`test_dag_validator_passes` and `test_dag_or_join_node_count_unchanged` already pass (unaffected
by this change).

### Step 2.3 — implement: add `conformance-gate`, rewire `push-and-pr`

In `workflows/archon-dark-factory.yaml`, insert a new node immediately after `conformance`
(currently ending at line 964) and before the `push-resolve` comment/node:

```yaml
  # Verdict gate: block push-and-pr when conformance's STATUS: verdict isn't PASS/SKIPPED/
  # ERROR — a command: node's internal exit 1 does not reliably surface to the DAG executor,
  # so this reads the durable conformance.md artifact directly instead (#212, #271).
  - id: conformance-gate
    bash: |
      # TARGET-PATH: verdict_gate_check.sh resolves under dark-factory/ in the clone.
      bash "${CLONE_DIR:-.}/dark-factory/scripts/verdict_gate_check.sh" \
        "$ARTIFACTS_DIR/conformance.md" \
        "$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")" \
        "Conformance (Gate 2)"
    depends_on: [conformance]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
```

Then change only the `depends_on` line of the existing `push-and-pr` node (its `bash:` body,
`when:`, and `timeout:` are unchanged):

```yaml
    depends_on: [conformance-gate]
```

### Step 2.4 — implement: add `review-gate`, rewire `status-in-review`

Insert a new node immediately after `revise-advisory` (currently ending at line 1179) and
before `status-in-review`:

```yaml
  # Verdict gate: block status-in-review when code-review's STATUS: verdict isn't PASS/
  # SKIPPED/ERROR — same durable-artifact pattern as conformance-gate, one gate later (#271).
  - id: review-gate
    bash: |
      # TARGET-PATH: verdict_gate_check.sh resolves under dark-factory/ in the clone.
      bash "${CLONE_DIR:-.}/dark-factory/scripts/verdict_gate_check.sh" \
        "$ARTIFACTS_DIR/review.md" \
        "$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")" \
        "Code Review (Gate 3)"
    depends_on: [code-review, revise-advisory]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
```

Then change only the `depends_on` line of the existing `status-in-review` node (its `bash:`
body, `trigger_rule:`, `when:`, and `timeout:` are unchanged) — additive, not a replacement:

```yaml
    depends_on: [push-and-pr, push-resolve, code-review, revise-advisory, review-gate]
```

### Step 2.5 — verify the tests pass

```bash
python -m pytest tests/test_verdict_gate_dag.py -v
```

Expected: all tests pass.

Run the full suite plus the DAG-check CI job's own commands, since the workflow YAML is shared
state across many test files (`test_push_gate_dag.py`, `test_budget_enforce_dag.py`, etc. all
parse the same file):

```bash
python -m pytest tests/ -v
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: no `FAILED` lines; both DAG-check scripts print their "passed" message with exit 0.

### Step 2.6 — commit

```bash
git add workflows/archon-dark-factory.yaml tests/test_verdict_gate_dag.py
git commit -m "fix(workflow): gate push-and-pr/status-in-review on conformance/code-review verdict files (#271)"
```

---

## Task 3: Update gate-command prose to reflect where enforcement actually lives

**Files:** `commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`

`commands/*.md` is the only tracked source for these command prompts — `.archon/commands/` is
**not** a git-tracked mirror in this repo (verified: `git ls-files .archon/` returns only
`.archon/memory/*`; `entrypoint.sh`'s `_exclude_in_clone` writes `.archon/commands/` into
`.git/info/exclude` at container start specifically so it can never be committed back). A prior
plan's claim of a `.archon/commands/` mirroring convention (cited from the #43 refine/plan
prompt-dedup plan) does not hold up against git history for this pair of files — do not add a
`.archon/commands/` edit or `git add` here; it will fail with "paths are ignored by one of your
.gitignore files". This task is doc-only: no test, since it's a prose-accuracy fix flagged as
non-blocking by the spec's "Open questions" section, folded in here at low cost rather than left
as a trivial follow-up.

### Step 3.1 — update `dark-factory-conformance.md`'s Phase 5 step 5

In `commands/dark-factory-conformance.md`, replace:

```
5. Exit non-zero (`exit 1`) — this prevents `push-and-pr` and `status-in-review` from running.
```

with:

```
5. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
   `conformance-gate` DAG node (`workflows/archon-dark-factory.yaml`), which reads this file's
   `STATUS:` line directly and blocks `push-and-pr` (and everything chained after it, including
   `status-in-review`) on anything other than `PASS`/`SKIPPED`/`ERROR` — a `command:` node's
   internal `exit 1` does not reliably surface as node failure to the DAG executor (#212, #271).
```

### Step 3.2 — update `dark-factory-code-review.md`'s Phase 6 step 6

In `commands/dark-factory-code-review.md`, replace:

```
6. Exit non-zero (`exit 1`) — this halts `status-in-review` (the issue stays Blocked instead of moving to In Review).
```

with:

```
6. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
   `review-gate` DAG node (`workflows/archon-dark-factory.yaml`), which reads this file's
   `STATUS:` line directly and blocks `status-in-review` on anything other than
   `PASS`/`SKIPPED`/`ERROR` — a `command:` node's internal `exit 1` does not reliably surface
   as node failure to the DAG executor (#212, #271).
```

### Step 3.3 — verify the edits landed

```bash
grep -A4 "^5. Exit non-zero" commands/dark-factory-conformance.md
grep -A4 "^6. Exit non-zero" commands/dark-factory-code-review.md
```

Expected: both print the updated prose referencing `conformance-gate`/`review-gate` respectively.

### Step 3.4 — commit

```bash
git add commands/dark-factory-conformance.md commands/dark-factory-code-review.md
git commit -m "docs(workflow): note conformance-gate/review-gate as the actual enforcement point (#271)"
```

---

## Validation summary (maps to spec's Requirements)

- **Req 1 (don't fix executor exit-code propagation):** not attempted — `verdict_gate_check.sh`
  reads the durable artifact file instead, per Task 1.
- **Req 2 (gate on the verdict file, not a fresh subagent call):** Task 1's script parses
  `STATUS:` from the file path passed in; no subagent is spawned.
- **Req 3 (both edges, one fix):** Task 2 adds both `conformance-gate` (→ `push-and-pr`) and
  `review-gate` (→ `status-in-review`) using the same script.
- **Req 4 (fail closed on ambiguity):** Task 1 Step 1.3's `case "$STATUS" in PASS|SKIPPED|ERROR)
  exit 0 ;; esac` falls through to the blocking path for every other value, including empty
  (missing/unparseable) — verified by Task 1's Cases 7-8.
- **Req 5 (label re-check selects messaging, not the decision):** Task 1 Step 1.3 always
  `exit 1`s on `BLOCKED` or missing/unparseable; the live label check only gates whether the
  comment-posting branch runs — verified by Task 1's Cases 4-7 (BLOCKED never comments
  regardless of the label; missing-file comments only when the label is absent).
- **Req 6 (`resolve` intent unaffected):** Task 2's new nodes carry the same `when:` restriction
  as `conformance`/`code-review` (`new`/`continue` only) — verified by
  `test_node_depends_on_and_when`.

## Known limitations (carried from spec, no code action)

- The underlying executor bug (a `command:` node's internal `exit 1` not surfacing as node
  failure) is external `archon` runtime infrastructure this repo doesn't own; this plan's
  verdict-file gate is the independent second layer the spec calls for, not a fix to the
  executor itself.
- `report`'s own `depends_on: [status-in-review, code-review]` OR-join is unchanged — when
  `conformance-gate` blocks, the whole downstream chain (including `code-review`) is skipped, so
  `report` is skipped too and no run-summary comment posts. Accepted trade-off, matching the
  #212 precedent; not fixed here to avoid widening this ticket into `report`'s own OR-join shape.
- This change requires an image rebuild + scheduler/run redeploy to take effect in production
  (see Goal's Build constraint) — no task in this plan performs that; it's a deployment action.
- The spec's Validation section also calls for a manual staging reproduction (force
  `conformance.md` absent with `needs-discussion` pre-applied → confirm `push-and-pr` is
  skipped and no PR is created; force `review.md` to `STATUS: BLOCKED` → confirm
  `status-in-review` is skipped). No task in this plan performs that — it requires a deployed
  staging run, which is a deployment action outside this plan's scope (same Build constraint as
  above). Task 1/2's execution-based test suites (a fixture-file/stubbed-tracker check for the
  script, a static-YAML check for the DAG wiring) are the automated substitute; the staging
  reproduction itself is left as a manual follow-up before/after the image redeploy.

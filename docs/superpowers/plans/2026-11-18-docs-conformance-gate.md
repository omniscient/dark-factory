# Docs Conformance Gate (Gate 2.5) — Implementation Plan

**Issue:** #535

**Revised:** 2026-11-19 (operator amendment after plan-gate review: rebased onto main f457c2c — workflow line numbers +28, docs/configuration.md full dotted keys and docs_refs_check.py, fail-closed on git diff failure, observe-mode exit code)

**Goal:** Every `new`/`continue` run authors documentation alongside the implementation
(implement Phase 4 writes a durable `doc-impact.md`), and an independent, single-pass,
read-only reviewer checks the docs against the **final post-Gate-2 diff** before
`push-and-pr`. The gate ships in `observe` mode (record + informational comment, never
block); `enforce` mode (fail closed on BLOCKED/ERROR/missing/unparseable) is fully
implemented and tested, and turning it on is #536.

**Architecture:** Four new DAG nodes between `conformance-gate` and `push-and-pr`
(`budget-docs-conformance` → `enforce-budget-docs-conformance` → `docs-conformance` →
`docs-conformance-gate`); a new command `commands/dark-factory-docs-conformance.md`; a new
baked reviewer rubric `refinement-skills/docs-conformance-reviewer-prompt.md`; a new gate
script `scripts/docs_verdict_gate_check.sh` (a sibling of `verdict_gate_check.sh` whose
`enforce` accept-list is PASS/SKIPPED only); a `docs-conformance` budget scenario; a
`docs_conformance` config block; implement Phase 4 rewritten to scan diff content and
always write `doc-impact.md`; reference docs updated.

**Tech Stack:** Bash, Python 3 (pytest, PyYAML), Archon workflow YAML, agent-executed
Markdown commands.

**Spec:** `docs/superpowers/specs/2026-11-17-docs-conformance-gate-design.md`

**Delivery:** this PR changes a gate. It goes through the normal human review; it carries no
`direct-to-pr` label and must not be auto-merged.

---

## Decisions this plan makes (read before starting)

Each was checked against the code on `main` while writing this plan; all code below was
run against a scratch copy of the repo (full `pytest tests/` green, both DAG checkers green).

1. **Config goes in `config/config.yaml`, not `.claude/skills/refinement/config.yaml`.**
   The latter is *not tracked*: `entrypoint.sh` materializes it per run from the baked
   `config/config.yaml` (`scripts/factory_core/effective_config.py`, merge order baked ←
   clone ← adapter) and git-excludes it. Every runtime reader (the command, the gate node,
   `budget_gate.sh`) still reads the materialized `.claude/skills/refinement/config.yaml`,
   as the spec says. Do not create or edit `.claude/skills/refinement/config.yaml`.
2. **No file under `.claude/skills/` is shipped.** The command implements the spec's
   clone-live-first lookup (`.claude/skills/docs-conformance/RUBRIC.md`, else the baked
   `/opt/refinement-skills/docs-conformance-reviewer-prompt.md`), but `.claude/skills/` is in
   the self-target adapter's `safety.hard_exclude_paths` (human-reviewed self-modification
   surface) and Gate 2's rubric flags any non-spec-named file there as `[OOS]` (a `SKILL.md`
   would be). For the self-target nothing is lost: workflow, commands, scripts and
   `/opt/refinement-skills/` all come from the same image. A shipped override skill can be a
   follow-up.
3. **The reviewed diff also excludes `docs/superpowers/**` and `docs/archive/**`.** These are
   the refine/plan artifacts `setup-branch` copies onto the feature branch and `push-and-pr`
   archives. They describe the *intended* change, not the documentation of it, and this
   ticket's own spec alone is ~10K tokens — more than the 6000-token ranked-diff cap. Policy
   Markdown (`commands/**`, `.claude/skills/**`, `refinement-skills/**`) stays in the diff.
   Known gap, documented in D2.5: an implementation-time edit to a *living* spec under
   `docs/superpowers/specs/` is not reviewed by this gate (rare — specs are refine's output).
   `budget-docs-conformance` measures the same pathspec, so its telemetry matches.
4. **Baseline/branch doc copies are handed over by path, not inlined.** Phase 2 writes the
   merge-base copy of each changed doc to `$ARTIFACTS_DIR/docs-conformance-baseline/<path>`
   and lists it beside the branch path; the reviewer (which has Read) reads both. This keeps
   the prompt bounded even for `docs/run-lifecycle.md` (~1200 lines) and does not depend on
   the diff cap.
5. **No `side_effect.phase_levels` key and no `_INTENT_PHASES` change.** Requirement 16: the
   node keeps `denied_tools: []`; it runs inside the `fix`/`continue` container whose level is
   already 5. Adding a phase-level key is a tool-permission surface change — left to #536.
6. **Budget:** `budgets.docs-conformance: 22000` (mirrors conformance) and
   `enforce.docs-conformance: false` (observe-only, like `implement`, until calibrated).
   `_DELIVERED_SAVINGS["docs-conformance"] = {"diff"}` because the command really runs
   `diff_rank.py` with the exported cap. `_SKILL_PROMPT_FILES` is **not** changed: adding the
   new rubric there would raise the measured `skill_prompts` tokens of the existing
   `conformance`/`code-review` scenarios, which are budget-enforced. Read the observe-mode
   `docs-conformance` telemetry with two known skews (for #536's calibration): its
   `skill_prompts` is the shared five-rubric figure, not the docs rubric, and its diff
   `baseline_tokens` comes from the `diff-ranking.json` Gate 2 wrote (the budget node runs
   before this command's own `diff_rank.py`) — the same ordering `budget-code-review` has.
   Conversely, this command's `diff_rank.py` overwrites `diff-ranking.json` before
   `budget-code-review` reads it, so code-review's diff `baseline_tokens` then comes from
   Gate 2.5's wider diff rather than Gate 2's (telemetry only; no enforcement input changes).
7. **`doc-impact.md` grammar** (spec left it to the plan): one
   `` - `<changed file or behavior>` -> UPDATED `<doc>#<section>` — … `` or
   `` - `<…>` -> NO_IMPACT: <reason> `` line per changed behavior, then exactly one
   `OVERALL: UPDATED` or `OVERALL: NO_IMPACT: <reason>` line. The command treats a missing or
   empty file, or one with no `OVERALL:` line, as a forced `BLOCKED` (Requirement 4); the
   reviewer judges completeness and whether each `NO_IMPACT` is defensible.
8. **`continue` remediation is wired.** The enforce-mode comment body starts with
   `## Docs Conformance (Gate 2.5) — Blocked` (marker on the next line — `tracker comment
   --marker` matches anywhere in the body) and that heading is registered in
   `scripts/comment_digest.py::_GATE_VERDICT_HEADINGS`, so a `Continue` run's digest surfaces
   it as the action-required verdict. `tests/test_comment_digest.py`'s drift test requires
   this registration for any `— Blocked` heading in `commands/`. Implement Phase 4 also reads
   the latest Gate 2.5 comment on `continue`.
9. **`STATUS: ERROR` is never written by this gate.** Refusal/timeout/tool-error/unparseable
   → `VERDICT: UNCERTAIN`, `STATUS: BLOCKED`. A missing rubric file (stale image) is a
   render failure → `UNCERTAIN` → BLOCKED, never an abort without a verdict. An **empty**
   reviewed diff is `STATUS: PASS` without a reviewer spawn or the `doc-impact.md` check —
   nothing changed, so there is nothing to document (the spec does not cover this case; D2.5
   in `docs/run-lifecycle.md` states it).
10. **Known limitation, documented in `docs/run-lifecycle.md`:** if the `docs-conformance`
    node itself fails in the executor (e.g. idle timeout) instead of writing a verdict,
    `docs-conformance-gate` and `push-and-pr` do not run, in either mode — the same exposure
    `conformance` has today. A skip-tolerant `trigger_rule` on the gate was rejected: it would
    let the gate race its upstream and would change `check_workflow_dag.py`'s OR-join
    tripwire.
11. **The enforce-mode silent-death comment reuses `<!-- df-push-gate-failure -->`**, as the
    spec specifies, so on one issue it upserts over an earlier `conformance-gate`
    silent-death comment (and vice versa). Harmless while the default is `observe`; #536 may
    want a gate-specific marker.
12. **For #536 (not done here):** `docs_conformance.enabled`/`.mode` are read from the
    agent-writable clone copy of the config, like `conformance.*` today. Before flipping to
    `enforce`, consider reading them the way the blast-radius gate does (baked config under
    the merge-base config) so a branch cannot switch its own gate off. Also for #536 or a
    follow-up: adding `docs-conformance.md` to `verifier.py`'s reserved `--out` basenames.

## Memory lessons applied

- `[AVOID]` reusing `verdict_gate_check.sh` unchanged for a new blocking gate → Task 1 adds a
  sibling script; a test pins that `ERROR` is outside its accept-list.
- `[AVOID]` assuming `_SECTION_REGISTRY` is generic → Task 2 adds the key (and the CLI flag;
  `--scenario` choices come from the registry, so without it the budget node errors).
- `[AVOID]` an observe knob that only skips one of the two side effects → Task 1 (gate
  script: no block, no tracker call) **and** Task 5 Phase 6 (command: no label, no board move).
- `[AVOID]` folding the check into Gate 2 / giving it a reconcile loop → own command + nodes,
  one spawn, no commits (Tasks 5, 6).
- `[AVOID]` trusting a prior claim without checking → every doc line citation below was
  re-derived from the edited files (Task 9 lists the exact shifts and how to re-check them).

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/docs_verdict_gate_check.sh` | Create | Gate 2.5 verdict gate: observe always exit 0; enforce exit 0 only on PASS/SKIPPED |
| `tests/test_docs_verdict_gate_check.py` | Create | Mode × verdict matrix, five stubbed semantic scenarios, fail-closed inputs |
| `scripts/context_budget.py` | Modify | `docs-conformance` scenario, `doc_impact_md` section, `--doc-impact-file` |
| `tests/test_context_budget.py` | Modify | Scenario/section/CLI tests |
| `scripts/factory_core/cost_report.py` | Modify | Comment only: diff is delivered to docs-conformance too |
| `tests/test_cost_report.py` | Modify | Delivered-set sync assertion |
| `config/config.yaml` | Modify | `docs_conformance` block; budget + enforce entries |
| `tests/test_budget_enforce_dag.py` | Modify | Config and node expectations for the new scenario |
| `scripts/budget_gate.sh` | Modify | Header comment: 6 nodes |
| `refinement-skills/docs-conformance-reviewer-prompt.md` | Create | Baked reviewer rubric |
| `commands/dark-factory-docs-conformance.md` | Create | Gate 2.5 command |
| `tests/test_docs_conformance_command.py` | Create | Static contract for the command and rubric |
| `tests/test_command_headless_contract.py` | Modify | Expect the new command file |
| `tests/test_verifier_contract_doc_referenced.py` | Modify | New command references the contract and pins Opus; contract row |
| `scripts/comment_digest.py` | Modify | Register the Gate 2.5 blocked heading |
| `tests/test_comment_digest.py` | Modify | Heading is recognized; observed comment is not |
| `workflows/archon-dark-factory.yaml` | Modify | Four new nodes; `push-and-pr` depends on both gates |
| `tests/test_docs_conformance_dag.py` | Create | Wiring + executes the real gate node body |
| `tests/test_verdict_gate_dag.py` | Modify | `push-and-pr` depends_on |
| `commands/dark-factory-implement.md` | Modify | Phase 4 rewrite (`doc-impact.md`), Phase 6 report bullet |
| `tests/test_implement_doc_impact.py` | Create | Phase 4 contract |
| `refinement-skills/VERIFIER-CONTRACT.md` | Modify | New checker row, refusal/verdict-schema notes |
| `docs/run-lifecycle.md`, `docs/features.md`, `docs/configuration.md` | Modify | Document the gate, failure/recovery, config; shift line citations |
| `docs/scheduler.md`, `docs/dark-factory-token-optimization.md`, `README.md` | Modify | Citation-only / one-phrase accuracy fixes forced by this change (no scheduler content change) |
| `tests/test_docs_conformance_reference_docs.py` | Create | Presence checks for the documentation |

Run every pytest command from the repo root with `PYTHONPATH=scripts` (as CI does).

---

## Task 1: `docs_verdict_gate_check.sh` — the fail-closed (enforce) / log-only (observe) gate script

**Files:** Create `tests/test_docs_verdict_gate_check.py`, create `scripts/docs_verdict_gate_check.sh`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_docs_verdict_gate_check.py`:

~~~~python
"""docs_verdict_gate_check.sh (#535, Gate 2.5): observe always proceeds, enforce fails
closed on everything but PASS/SKIPPED — including ERROR, which verdict_gate_check.sh
passes for Gate 3's fail_open contract.

CI has no model access, so the five semantic scenarios from the spec (Requirement 14)
are driven by stubbed docs-conformance.md verdicts standing in for what a real reviewer
would write. These tests prove the plumbing (parsing, the mode branch, no tracker side
effects in observe mode, exit 1 = no PR in enforce mode) — not that a reviewer judges
any scenario correctly; that is measured from recorded observe-mode verdicts (#536).
The tracker CLI is replaced by the exported python3-function precedent from
tests/test_push_gate_dag_exec.py, which logs every call.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE = _REPO_ROOT / "scripts" / "docs_verdict_gate_check.sh"
_LABEL = "Docs Conformance (Gate 2.5)"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "jq")),
    reason="needs a POSIX host with bash and jq",
)

_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      case "$*" in
        *"tracker get"*)
          if [ "${NEEDS_DISCUSSION:-false}" = "true" ]; then
            printf '{"labels":[{"name":"needs-discussion"}]}\n'
          else
            printf '{"labels":[]}\n'
          fi ;;
      esac
      return 0 ;;
    *factory_core/cli.py*marker*) echo "*Posted by Test Factory*" ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''


def _verdict(status, findings=0, severity="none", verdict="CONFORMS"):
    return (f"STATUS: {status}\nGATE_TYPE: docs-conformance\nFINDINGS_COUNT: {findings}\n"
            f"SEVERITY: {severity}\nVERDICT: {verdict}\n\n---\n\n(stubbed reviewer dialogue)\n")


# Requirement 11/14's five semantic scenarios, each with the verdict a correct reviewer
# would have written for it.
SCENARIOS = {
    "policy-markdown-change-documented": _verdict("PASS"),
    "scheduler-config-artifact-change-undocumented": _verdict("BLOCKED", 2, "high", "MATERIAL"),
    "target-structural-change-documented": _verdict("PASS", 1, "low", "MINOR"),
    "docs-neutral-defensible-no-impact": _verdict("PASS"),
    "misleading-but-present-doc-update": _verdict("BLOCKED", 1, "high", "MATERIAL"),
}

# Fail-closed inputs: everything a reviewer crash, refusal or bad write can leave behind.
FAIL_CLOSED = {
    "missing-file": None,
    "malformed-status": "STATUS:\nnot a verdict\n",
    "no-status-line": "the reviewer refused\n",
    "error-status": "STATUS: ERROR\nREASON: reviewer timed out\n",
    "uncertain-written-as-blocked": _verdict("BLOCKED", 1, "high", "UNCERTAIN"),
    "unknown-token": "STATUS: HUMAN_REQUIRED\n",
}


def _run(tmp_path, content, mode, issue="535", needs_discussion=False, extra_args=None):
    verdict = tmp_path / "docs-conformance.md"
    if content is not None:
        verdict.write_text(content)
    log = tmp_path / "pcli.log"
    log.write_text("")
    args = [str(verdict), issue, _LABEL] + ([] if mode is None else [mode])
    if extra_args is not None:
        args = extra_args
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    env = dict(os.environ, PCLI_LOG=str(log),
               NEEDS_DISCUSSION="true" if needs_discussion else "false")
    proc = subprocess.run(["bash", "-c", f"{_PRELUDE}\nbash '{_GATE}' {quoted}"],
                          env=env, capture_output=True, text=True)
    return proc.returncode, log.read_text(), proc.stderr


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_observe_mode_proceeds_for_every_scenario_with_no_side_effects(tmp_path, name):
    rc, log, err = _run(tmp_path, SCENARIOS[name], "observe")
    assert rc == 0
    assert log == "", f"observe mode must make no tracker call, got: {log}"
    assert "observe mode" in err


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_enforce_mode_blocks_exactly_the_blocked_scenarios(tmp_path, name):
    rc, log, _ = _run(tmp_path, SCENARIOS[name], "enforce")
    expect_block = SCENARIOS[name].startswith("STATUS: BLOCKED")
    assert rc == (1 if expect_block else 0), name
    assert "tracker comment" not in log, "a BLOCKED verdict already carries the command's own comment"


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_enforce_mode_fails_closed(tmp_path, name):
    rc, _, _ = _run(tmp_path, FAIL_CLOSED[name], "enforce")
    assert rc == 1, f"{name} must block push-and-pr in enforce mode"


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_observe_mode_logs_would_block_but_proceeds(tmp_path, name):
    rc, log, err = _run(tmp_path, FAIL_CLOSED[name], "observe")
    assert rc == 0
    assert log == ""
    assert "enforce would block" in err


def test_enforce_error_status_posts_marker_comment_when_unexplained(tmp_path):
    rc, log, _ = _run(tmp_path, FAIL_CLOSED["error-status"], "enforce")
    assert rc == 1
    assert "tracker comment" in log and "--marker <!-- df-push-gate-failure -->" in log


def test_enforce_missing_file_with_needs_discussion_blocks_quietly(tmp_path):
    rc, log, _ = _run(tmp_path, None, "enforce", needs_discussion=True)
    assert rc == 1
    assert "tracker comment" not in log


def test_enforce_skipped_proceeds(tmp_path):
    rc, log, _ = _run(tmp_path, "STATUS: SKIPPED\nREASON: docs_conformance.enabled=false\n", "enforce")
    assert rc == 0 and log == ""


@pytest.mark.parametrize("mode", [None, "", "Enforce", "block", "observe"])
def test_anything_but_literal_enforce_is_observe(tmp_path, mode):
    rc, log, err = _run(tmp_path, FAIL_CLOSED["error-status"], mode)
    assert rc == 0 and log == ""
    assert "observe mode" in err


def test_enforce_non_numeric_issue_fails_closed_without_tracker_call(tmp_path):
    rc, log, _ = _run(tmp_path, None, "enforce", issue="null")
    assert rc == 1 and log == ""


@pytest.mark.parametrize("argc", [0, 1, 2])
def test_usage_errors(tmp_path, argc):
    args = [str(tmp_path / "v.md"), "535", _LABEL][:argc]
    rc, _, _ = _run(tmp_path, None, None, extra_args=args)
    assert rc != 0


def test_script_is_not_verdict_gate_check():
    """ERROR must stay outside this script's accept-list (Requirement 3)."""
    text = _GATE.read_text(encoding="utf-8")
    assert "PASS|SKIPPED)" in text
    assert "PASS|SKIPPED|ERROR" not in text
~~~~

- [ ] **Step 2: Run it and confirm it fails.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_verdict_gate_check.py -q`
  Expected: `32 failed, 3 passed` (the script does not exist; only the three usage-error
  cases pass because `bash` of a missing file also exits non-zero).

- [ ] **Step 3: Create `scripts/docs_verdict_gate_check.sh`** and make it executable
  (`chmod +x scripts/docs_verdict_gate_check.sh`; `verdict_gate_check.sh` is `0755` too):

~~~~bash
#!/usr/bin/env bash
# Gate push-and-pr on the STATUS: verdict dark-factory-docs-conformance.md wrote to
# docs-conformance.md (Gate 2.5, #535). A sibling of verdict_gate_check.sh, not a flag on
# it: that script passes ERROR because Gate 3's code_review.fail_open contract says so,
# and this gate must never inherit that pass-through in enforce mode.
#
# Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode>]
#   <verdict-file>   $ARTIFACTS_DIR/docs-conformance.md
#   <issue-number>   for the live needs-discussion re-check and the silent-death comment
#   <gate-label>     human string for the comment, e.g. "Docs Conformance (Gate 2.5)"
#   <mode>           docs_conformance.mode: "enforce", or anything else (including
#                    omitted/blank) = "observe", the shipped default
#
# observe: always exit 0. Log to stderr what enforce mode would have decided. No tracker
#   call, no comment, no label check — the command's own Phase 6 already posted an
#   informational comment for a BLOCKED verdict.
# enforce: exit 0 only for STATUS: PASS or SKIPPED. BLOCKED, ERROR, missing or
#   unparseable exit 1. A live needs-discussion re-check decides messaging only:
#     - BLOCKED, or needs-discussion present: exit 1 quietly (the command already posted).
#     - otherwise (ERROR/missing/unparseable, nothing upstream explained it): post an
#       idempotent <!-- df-push-gate-failure --> marker comment, then exit 1.
#
# The exit code IS the gate signal — do not wrap this call in `|| true`.
set -uo pipefail

_USAGE="Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode>]"
VERDICT_FILE="${1:?$_USAGE}"
ISSUE_NUM="${2:?$_USAGE}"
GATE_LABEL="${3:?$_USAGE}"
MODE="${4:-observe}"
[ "$MODE" = "enforce" ] || MODE="observe"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"

STATUS=""
if [ -f "$VERDICT_FILE" ]; then
  STATUS=$(grep -m1 '^STATUS:' "$VERDICT_FILE" 2>/dev/null | awk '{print $2}')
fi

# One STATUS-to-decision mapping for both modes, so observe logs exactly what enforce does.
case "$STATUS" in
  PASS|SKIPPED) DECISION="proceed" ;;
  *)            DECISION="block" ;;
esac

if [ "$MODE" = "observe" ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} observe mode — enforce would ${DECISION} issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); proceeding." >&2
  exit 0
fi

[ "$DECISION" = "proceed" ] && exit 0

# Guard against a non-numeric issue number reaching the tracker CLI or a comment marker —
# fail closed with no tracker call, mirroring verdict_gate_check.sh.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    echo "docs_verdict_gate_check.sh: ${GATE_LABEL} — issue number '${ISSUE_NUM}' is not numeric; failing closed without a tracker call." >&2
    exit 1
    ;;
esac

HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE_NUM" --fields labels 2>/dev/null \
  | jq -r '.labels[].name' 2>/dev/null \
  | grep -Fxc 'needs-discussion' || true)

if [ "$STATUS" = "BLOCKED" ] || [ "${HAS_NEEDS_DISCUSSION:-0}" -gt 0 ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); upstream already communicated, no comment needed." >&2
  exit 1
fi

echo "docs_verdict_gate_check.sh: ${GATE_LABEL} verdict ${STATUS:-missing} for issue #${ISSUE_NUM} and no needs-discussion label — failing closed and posting failure comment." >&2
_FOOTER=$(python3 "${SCRIPT_DIR}/factory_core/cli.py" marker factory 2>/dev/null || echo "")
_FAIL_BODY="<!-- df-push-gate-failure -->
## ${GATE_LABEL} — Blocked

No usable verdict was recorded for this run (\`${VERDICT_FILE}\` is missing, unparseable, or \`STATUS: ${STATUS:-missing}\`). \`docs_conformance.mode\` is \`enforce\`, so this blocks the PR instead of advancing silently.

\`\`\`bash
# Retry manually if needed
docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"
\`\`\`

---
${_FOOTER}"
TMPFILE=$(mktemp /tmp/docs-gate-failure-XXXXXX.md)
printf '%s' "$_FAIL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "<!-- df-push-gate-failure -->" --body-file "$TMPFILE"
rm -f "$TMPFILE"
exit 1
~~~~

- [ ] **Step 4: Run the test again.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_verdict_gate_check.py -q` → `35 passed`.
  Also `bash -n scripts/docs_verdict_gate_check.sh` → no output, exit 0.

- [ ] **Step 5: Commit.**
  `git add scripts/docs_verdict_gate_check.sh tests/test_docs_verdict_gate_check.py && git commit -m "feat: add docs_verdict_gate_check.sh for Gate 2.5 observe/enforce (#535)"`

---

## Task 2: `docs-conformance` budget scenario in `context_budget.py`

**Files:** Modify `tests/test_context_budget.py`, `scripts/context_budget.py`,
`tests/test_cost_report.py`, `scripts/factory_core/cost_report.py`.

- [ ] **Step 1: Write the failing tests.** Append to the end of `tests/test_context_budget.py`:

~~~~python


# ── docs-conformance scenario (#535) ─────────────────────────────────────────

class TestDocsConformanceScenario:
    def test_registry_entry(self):
        assert cb._SECTION_REGISTRY["docs-conformance"] == ["skill_prompts", "diff", "doc_impact_md"]
        assert cb._DELIVERED_SAVINGS["docs-conformance"] == frozenset({"diff"})

    def test_doc_impact_included_and_hashed(self, tmp_path):
        doc_impact = tmp_path / "doc-impact.md"
        doc_impact.write_text("- `scripts/x.sh` -> NO_IMPACT: internal refactor\nOVERALL: NO_IMPACT: internal\n")
        result = run_budget(tmp_path, "docs-conformance",
                            diff_file=make_diff_file(tmp_path),
                            doc_impact_file=str(doc_impact))
        assert result["scenario"] == "docs-conformance"
        assert result["sections"]["doc_impact_md"]["status"] == "included"
        assert "doc-impact.md" in result["source_file_hashes"]
        assert result["sections"]["diff"]["status"] in ("included", "included_partial")

    def test_missing_doc_impact_is_dropped_not_fatal(self, tmp_path):
        result = run_budget(tmp_path, "docs-conformance",
                            diff_file=make_diff_file(tmp_path),
                            doc_impact_file=str(tmp_path / "absent.md"))
        assert result["sections"]["doc_impact_md"]["status"] == "dropped"
        assert "doc_impact_md" in result["dropped_sections"]

    def test_other_scenarios_unchanged(self):
        assert "doc_impact_md" not in cb._SECTION_REGISTRY["conformance"]
        assert "doc_impact_md" not in cb._SECTION_REGISTRY["code-review"]

    def test_cli_accepts_scenario_and_doc_impact_flag(self, tmp_path):
        import subprocess
        doc_impact = tmp_path / "doc-impact.md"
        doc_impact.write_text("OVERALL: UPDATED\n")
        out = tmp_path / "context-budget.docs-conformance.json"
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "context_budget.py"),
             "--scenario", "docs-conformance", "--issue-num", "535", "--run-id", "r1",
             "--artifacts-dir", str(tmp_path), "--clone-dir", str(tmp_path),
             "--doc-impact-file", str(doc_impact), "--diff-file", make_diff_file(tmp_path),
             "--out", str(out)],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(out.read_text())["sections"]["doc_impact_md"]["status"] == "included"
~~~~

  In `tests/test_cost_report.py`, `test_delivered_sets_in_sync_with_context_budget`, change
  `    assert diff == {"conformance", "code-review"}` to
  `    assert diff == {"conformance", "code-review", "docs-conformance"}`.

- [ ] **Step 2: Run and confirm failure.**
  `PYTHONPATH=scripts python -m pytest tests/test_context_budget.py tests/test_cost_report.py -q`
  Expected: `5 failed` (4 in `TestDocsConformanceScenario` — `KeyError`/`TypeError: unexpected
  keyword argument 'doc_impact_file'`/argparse `invalid choice` — plus the sync test).

- [ ] **Step 3: Implement** in `scripts/context_budget.py`:
  1. `_SECTION_REGISTRY`: after the `"code-review": ["skill_prompts", "issue_context", "diff"],` line add
     `    "docs-conformance": ["skill_prompts", "diff", "doc_impact_md"],`
  2. In the comment above `_DELIVERED_SAVINGS`, change
     `#   diff (conformance, code-review): the commands run diff_rank.py with the exported` to
     `#   diff (conformance, code-review, docs-conformance): the commands run diff_rank.py with the exported`
  3. `_DELIVERED_SAVINGS`: after `    "code-review": frozenset({"diff"}),` add
     `    "docs-conformance": frozenset({"diff"}),`
  4. `build_budget(...)` signature: after `    issue_text: str | None = None,` add
     `    doc_impact_file: str | None = None,`
  5. In the section loop, immediately before `        elif sec == "diff":` insert:

~~~~python
        elif sec == "doc_impact_md":
            # Measured-only, like implementation_md: the docs-conformance command reads
            # doc-impact.md in full (#535).
            sections[sec] = _included(_read_text(doc_impact_file), doc_impact_file)
            if doc_impact_file and sections[sec]["status"] == "included":
                h = te.hash_file(doc_impact_file)
                if h:
                    source_hashes["doc-impact.md"] = h

~~~~
  6. `main()`: after `    parser.add_argument("--diff-file")` add

~~~~python
    parser.add_argument("--doc-impact-file",
                        help="Path to doc-impact.md (used for docs-conformance scenario)")
~~~~
     and in the `build_budget(` call after `        comment_digest_file=args.comment_digest_file,` add
     `        doc_impact_file=args.doc_impact_file,`

  In `scripts/factory_core/cost_report.py` change the comment line
  `# conformance/code-review (diff_rank.py records no phase, every record counts).` to
  `# conformance/code-review/docs-conformance (diff_rank.py records no phase, every record counts).`

- [ ] **Step 4: Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_context_budget.py tests/test_cost_report.py -q` → `114 passed`.

- [ ] **Step 5: Commit.**
  `git add scripts/context_budget.py scripts/factory_core/cost_report.py tests/test_context_budget.py tests/test_cost_report.py && git commit -m "feat: register docs-conformance budget scenario and doc_impact_md section (#535)"`

---

## Task 3: `docs_conformance` config block and budget entries

**Files:** Modify `tests/test_budget_enforce_dag.py` (config tests only in this task),
`config/config.yaml`, `scripts/budget_gate.sh`.

- [ ] **Step 1: Update the config expectations** in `tests/test_budget_enforce_dag.py`:
  - in `test_config_budgets_has_all_scenarios` **and** `test_config_enforce_has_all_scenarios`, change
    `    for scenario in ("refine", "plan", "implement", "conformance", "code-review"):` to
    `    for scenario in ("refine", "plan", "implement", "conformance", "code-review", "docs-conformance"):`
  - in `test_config_budgets_t6_state`'s `expected`, after `        "code-review": 22000,` add `        "docs-conformance": 22000,`
  - in `test_config_enforce_t6_state`'s `expected`, after `        "code-review": True,` add `        "docs-conformance": False,`

- [ ] **Step 2: Run and confirm failure.**
  `PYTHONPATH=scripts python -m pytest tests/test_budget_enforce_dag.py -q` → `4 failed` (the four config tests).

- [ ] **Step 3: Edit `config/config.yaml`.**
  - After the `code_review:` block (after its `    - critical` line, before the blank line and `preview:`), insert:

~~~~yaml

docs_conformance:            # Gate 2.5 (#535): docs vs the final post-Gate-2 diff, before push-and-pr
  enabled: true              # false = the command writes STATUS: SKIPPED (passes in both modes)
  mode: observe              # observe = record the verdict, never block; enforce = fail closed on
                             # BLOCKED/ERROR/missing. Flipping to enforce is a separate reviewed change (#536)
~~~~
    (one blank line before `docs_conformance:` and one before `preview:` — the block adds exactly 5 lines.)
  - Under `token_optimization.budgets`, after the `    code-review: 22000 ...` line add
    `    docs-conformance: 22000    # #535: uncalibrated; mirrors conformance until observe-mode telemetry exists`
  - Under `token_optimization.enforce`, after the `    code-review: true ...` line add
    `    docs-conformance: false    # #535: observe until calibrated, like implement`

  In `scripts/budget_gate.sh`, replace the two header lines
  `# budget_enforce.py. One script backs all 5 enforce-budget-* workflow nodes` /
  `# (refine/plan/implement/conformance/code-review) — a faithful extraction of` with
  `# budget_enforce.py. One script backs all 6 enforce-budget-* workflow nodes` /
  `# (refine/plan/implement/conformance/docs-conformance/code-review) — a faithful extraction of`
  (same line count — `docs/configuration.md` cites `budget_gate.sh` by line).

- [ ] **Step 4: Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_budget_enforce_dag.py tests/test_side_effect_dag.py tests/test_token_optimization_flags.py -q` → 0 failed.
  `bash tests/test_budget_gate.sh` → last line `PASS`.
  `python3 -c "import yaml; c=yaml.safe_load(open('config/config.yaml')); print(c['docs_conformance'], c['token_optimization']['budgets']['docs-conformance'], c['token_optimization']['enforce']['docs-conformance'])"`
  → `{'enabled': True, 'mode': 'observe'} 22000 False`

- [ ] **Step 5: Commit.**
  `git add config/config.yaml scripts/budget_gate.sh tests/test_budget_enforce_dag.py && git commit -m "feat: add docs_conformance config (observe default) and budget scenario (#535)"`

---

## Task 4: Register the Gate 2.5 verdict heading for the `continue` digest

**Files:** Modify `tests/test_comment_digest.py`, `scripts/comment_digest.py`.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_comment_digest.py`:

~~~~python


def test_docs_conformance_blocked_comment_is_a_gate_verdict(monkeypatch):
    """#535: Gate 2.5's enforce-mode comment leads with its heading (the upsert marker is on
    the next line), so a Continue run's digest surfaces it as the action-required verdict."""
    import importlib, comment_digest as cd
    monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
    importlib.reload(cd)  # an earlier test reloads the module under another product name
    body = ("## Docs Conformance (Gate 2.5) — Blocked\n<!-- df-docs-conformance-blocked -->\n\n"
            "- [MATERIAL] `docs/x.md`#y — wrong\n\n---\n*Posted by MarketHawk Dark Factory*")
    assert cd._is_gate_verdict(body)
    observed = body.replace("— Blocked", "— Mismatch observed (informational)")
    assert not cd._is_gate_verdict(observed), "observe-mode comments are informational, not verdicts"
~~~~

- [ ] **Step 2: Run.** `PYTHONPATH=scripts python -m pytest tests/test_comment_digest.py -q` → `1 failed, 33 passed`.
  (Doing this before Task 5 keeps every commit green: once the command file exists, the
  file's drift test `test_gate_verdict_headings_cover_all_command_templates` requires this
  registration.)

- [ ] **Step 3: Implement.** In `scripts/comment_digest.py`, `_GATE_VERDICT_HEADINGS`, after
  `    "## Spec Conformance — Blocked",` add `    "## Docs Conformance (Gate 2.5) — Blocked",`.

- [ ] **Step 4: Run.** `PYTHONPATH=scripts python -m pytest tests/test_comment_digest.py -q` → `34 passed`.

- [ ] **Step 5: Commit.**
  `git add scripts/comment_digest.py tests/test_comment_digest.py && git commit -m "feat: surface Gate 2.5 blocked comment in the continue digest (#535)"`

---

## Task 5: Reviewer rubric and the `dark-factory-docs-conformance` command

**Files:** Create `tests/test_docs_conformance_command.py`,
`refinement-skills/docs-conformance-reviewer-prompt.md`,
`commands/dark-factory-docs-conformance.md`; modify
`tests/test_command_headless_contract.py`, `tests/test_verifier_contract_doc_referenced.py`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_docs_conformance_command.py`:

~~~~python
"""Static contract for Gate 2.5's command, rubric and skill files (#535). The command is
agent-executed Markdown, so — like tests/test_conformance_command_render_prompt.py — these
tests pin the load-bearing text: the diff that can see docs, the single Opus spawn, the
UNCERTAIN -> BLOCKED mapping, and the observe/enforce split of Phase 6's side effects."""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD = _REPO_ROOT / "commands" / "dark-factory-docs-conformance.md"
_BAKED = _REPO_ROOT / "refinement-skills" / "docs-conformance-reviewer-prompt.md"


def _cmd():
    return _CMD.read_text(encoding="utf-8")


def _phase(title_prefix):
    text = _cmd()
    start = text.index(f"## {title_prefix}")
    nxt = text.find("\n## Phase", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


def test_diff_sees_docs_but_keeps_noise_exclusions():
    phase2 = _phase("Phase 2")
    assert "':!docs/*.md'" not in _cmd(), "Gate 2.5 is the one gate that must see docs/*.md"
    assert "':!*.md'" not in _cmd()
    for token in ("':!*.lock'", "':!evals/*.md'", "':!bench/*.md'", "':!.archon/memory/**'",
                  "':!codeindex.json'", "':!symbolindex.json'"):
        assert token in phase2, token
    assert "git diff main...HEAD" in phase2
    assert "git diff main...HEAD failed" in phase2, "a git failure must not be read as an empty diff"


def test_policy_markdown_is_not_excluded_from_the_reviewed_diff():
    diff_block = _phase("Phase 2").split("# 2b.")[0]
    for path in ("commands/", ".claude/skills/", "refinement-skills/"):
        assert f"':!{path}" not in diff_block, f"{path} policy Markdown is behavior and must be reviewed"


def test_baseline_and_branch_doc_copies_are_collected():
    phase2 = _phase("Phase 2")
    assert "git merge-base main HEAD" in phase2
    assert 'git show "${BASE_REF}:${P}"' in phase2
    assert "docs_conformance_doc_files.md" in phase2


def test_single_opus_spawn_no_shadow_no_reconcile():
    text = _cmd()
    assert text.count("Spawn exactly one reviewer subagent") == 1
    assert "`claude-opus-5-5`" in text and "`opus` alias" in text
    assert "Glob/Grep/Read" in text
    assert "No shadow spawn" in text
    assert "max_reconcile_cycles" not in text and "RECONCILE" not in text
    assert "Do not\n   re-spawn, retry" in text or "Do not re-spawn" in text


def test_unparseable_maps_to_uncertain_blocked_never_pass():
    p45 = _phase("Phase 4")
    assert re.search(r'\*\)\s+VERDICT=UNCERTAIN; STATUS=BLOCKED', p45)
    assert 'VERDICT=MATERIAL;  STATUS=BLOCKED' in p45
    assert "STATUS=ERROR" not in _cmd(), "this gate never writes ERROR (enforce would block it anyway)"


def test_missing_doc_impact_forces_blocked():
    p45 = _phase("Phase 4")
    assert "DOC_IMPACT=missing" in p45
    assert "OVERALL: (UPDATED|NO_IMPACT: .+)" in p45
    assert 'emit_verdict "docs-conformance"' in p45


def test_observe_mode_has_no_label_or_board_side_effect():
    p6 = _phase("Phase 6")
    assert "<!-- df-docs-conformance-observed -->" in p6
    assert "<!-- df-docs-conformance-blocked -->" in p6
    side_effects = p6.split('if [ "$DOCS_MODE" = "enforce" ]; then')[-1]
    assert "--add needs-discussion" in side_effects and "--status blocked" in side_effects
    assert p6.count("--add needs-discussion") == 1 and p6.count("--status blocked") == 1, \
        "needs-discussion/board move must appear only in the final enforce-only branch"


def test_disabled_writes_skipped():
    assert "STATUS: SKIPPED\\nREASON: docs_conformance.enabled=false" in _phase("Phase 1")


def test_mode_parse_defaults_to_observe():
    assert _cmd().count("m if m == 'enforce' else 'observe'") == 2
    assert _cmd().count('|| echo "true observe"') == 2


def test_no_commit_or_push_in_this_command():
    text = _cmd()
    assert "git commit" not in text and "git push" not in text and "git add" not in text


def test_rubric_renders_with_exactly_the_three_slots(tmp_path):
    import sys
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from factory_core.prompt_render import render
    out = render(_BAKED.read_text(encoding="utf-8"),
                 {"DIFF_CONTENT": "D", "DOC_FILES": "F", "DOC_IMPACT": "I"})
    assert "**Verdict:**" in out
    assert "--set DIFF_CONTENT=@" in _cmd() and "--set DOC_FILES=@" in _cmd() and "--set DOC_IMPACT=@" in _cmd()


def test_rubric_asks_the_three_questions_and_audits_not_trusts():
    rubric = _BAKED.read_text(encoding="utf-8")
    assert "Form your own impact view first" in rubric
    assert "Check every doc claim" in rubric
    assert "Find omissions" in rubric
    assert "claim to audit" in rubric and "never evidence" in rubric
    assert "Never flag a doc change as out of scope" in rubric


def test_rubric_resolves_clone_live_first_then_baked():
    """The lookup follows VERIFIER-CONTRACT's clone-live-first pattern; #535 ships only the
    baked copy (no file under the human-reviewed .claude/skills/ surface)."""
    p3 = _phase("Phase 3")
    live = p3.index('[ -f ".claude/skills/docs-conformance/RUBRIC.md" ]')
    baked = p3.index('RUBRIC_FILE="/opt/refinement-skills/docs-conformance-reviewer-prompt.md"')
    assert live < baked
    assert not (_REPO_ROOT / ".claude" / "skills" / "docs-conformance").exists()
~~~~

  In `tests/test_command_headless_contract.py`, add `    "dark-factory-docs-conformance.md",`
  to `EXPECTED_COMMANDS` right after `    "dark-factory-conformance.md",`.

  In `tests/test_verifier_contract_doc_referenced.py`, add
  `    "commands/dark-factory-docs-conformance.md",` as the last entry of `_COMMAND_FILES`, and
  `    "commands/dark-factory-docs-conformance.md": 1,` as the last entry of `_INLINE_PIN_COUNTS`.

- [ ] **Step 2: Run and confirm failure.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_command.py tests/test_command_headless_contract.py tests/test_verifier_contract_doc_referenced.py -q`
  Expected: the 13 new tests fail (`FileNotFoundError`), plus
  `test_glob_finds_every_command_file`, `test_every_command_file_references_verifier_contract_doc`
  and `test_every_command_file_keeps_inline_model_pin`.

- [ ] **Step 3a: Create `refinement-skills/docs-conformance-reviewer-prompt.md`** (the
  Dockerfile's `COPY refinement-skills/ /opt/refinement-skills/` bakes it; no Dockerfile
  change). Only `$DIFF_CONTENT`, `$DOC_FILES` and `$DOC_IMPACT` may appear outside
  backticks — `prompt_render.py` fails on any other bare `$NAME`:

~~~~markdown
# Docs Conformance Reviewer

You are the documentation-conformance reviewer (Gate 2.5) for the dark factory pipeline.
The implementation has already passed spec conformance (Gate 2). Your only job is to judge
whether the branch's **documentation is true of the branch's actual final diff** — not
whether the code is good, and not whether a change is in scope.

## Input

You will be given:
- `$DIFF_CONTENT`: the final `main...HEAD` diff, ranked by risk tier. Unlike Gates 2 and 3 it
  includes `docs/*.md`. Code, config, workflow YAML and policy Markdown (`commands/**`,
  `.claude/skills/**`, `refinement-skills/**`) are all behavior: a change to them can need docs.
- `$DOC_FILES`: a table of every documentation file the diff touches, with the path of its
  baseline (`main`) copy and of its branch copy. Read both with your Read tool — compare the
  text before and after; do not judge from the diff hunk alone. A file listed in the diff as
  `[SUMMARIZED]` can also be read in full at its branch path.
- `$DOC_IMPACT`: the implement agent's documentation-impact assessment (`doc-impact.md`). It
  is a **claim to audit**, never evidence. It was written before validation and Gate 2, so
  the diff may have changed since.

You have Read, Grep and Glob. Use them to check claims against the code at its branch path.

## How to judge — in this order

1. **Form your own impact view first, before reading `$DOC_IMPACT`.** From the diff alone,
   list every operator- or developer-visible behavior the change adds, removes or alters: a
   workflow node or its order, a gate's pass/block rule, a config key or default, an env var,
   an artifact file, a label, a board status, a comment marker, a command's phase behavior,
   a CLI flag, a script's exit-code contract. For each, find the existing documentation that
   covers that area (Grep the repo's `README.md`, `docs/`, `ARCHITECTURE.md`,
   `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `CLAUDE.md`). A behavior needs a doc update
   when an existing doc describes that area and is now false or incomplete.
2. **Check every doc claim the branch adds or changes.** For each changed sentence, table row
   or code block in a documentation file: is it true of the diff? A present, well-formed doc
   update that describes behavior the diff does not implement is a material mismatch.
3. **Find omissions.** Any behavior from step 1 whose covering doc was not updated, and is now
   wrong or silent about a change an operator would act on, is a material mismatch.
4. **Audit `$DOC_IMPACT` last.** Missing, empty, or without an `OVERALL:` line is a material
   mismatch. Any `NO_IMPACT:` entry or `OVERALL: NO_IMPACT:` is defensible only if your step-1
   view agrees that no covering doc became false or incomplete; a missing mapping row, the
   file being "internal", or the agent's say-so is not a reason.

Do not decide from doc-file presence, changed-path matching, link or line-number checks, or the
implement agent's self-attestation. Never require an edit to an auto-generated doc (for example
`docs/database-schema.md`). Never flag a doc change as out of scope — scope is Gate 2's job.

## Severity

- **MATERIAL** — a doc claim is false of the diff; an affected behavior an operator or
  developer would act on is undocumented where a doc covers that area; a `NO_IMPACT` claim is
  not defensible; `$DOC_IMPACT` is missing, empty or incomplete.
- **MINOR** — wording, formatting, a stale line-number citation, or an omission nobody would
  act on.

## Output Format

Your entire response must follow this exact structure. Do not include any text outside it.

```
## Docs Conformance

**Verdict:** ✅ Conforms | ⚠️ Minor deviations (advisory) | ⛔ Material divergence

### Independent impact view
| Behavior changed (diff evidence) | Covering doc#section | Updated correctly? |
|---|---|---|
| <behavior> (`<file>`) | `<doc>#<section>` or none | Yes / No / n/a |

### Doc claims checked
| Doc#section | Claim | True of the diff? | Evidence |
|---|---|---|---|

**Deviations:**
- [MATERIAL] `<doc file>#<section>` — <the false or missing claim> — diff evidence: `<file>` <hunk or line> — suggested correction: <text>
- [MINOR] `<doc file>#<section>` — <issue>

(If there are no deviations, write: No deviations found.)

### doc-impact.md audit
<present/missing; each NO_IMPACT claim and whether it is defensible, with the reason>
```

If any `[MATERIAL]` bullet exists, the verdict is `⛔ Material divergence`. If only `[MINOR]`
bullets exist, the verdict is `⚠️ Minor deviations (advisory)`.

## Context

### Documentation files touched (baseline and branch copies)
$DOC_FILES

### doc-impact.md
$DOC_IMPACT

### Diff (final, post-Gate-2, ranked by risk tier)
$DIFF_CONTENT
~~~~

- [ ] **Step 3b: Create `commands/dark-factory-docs-conformance.md`.** The
  `<!-- headless-contract:begin -->` … `<!-- headless-contract:end -->` block must be
  byte-identical to the one in `commands/dark-factory-conformance.md` (copy it with
  `awk '/<!-- headless-contract:begin -->/,/<!-- headless-contract:end -->/' commands/dark-factory-conformance.md`
  if in doubt). Full file:

~~~~markdown
---
description: Verify the branch's documentation against its final post-Gate-2 diff before PR publication (Gate 2.5 — docs vs diff)
argument-hint: (no arguments - reads $ARTIFACTS_DIR/issue.json and $ARTIFACTS_DIR/doc-impact.md)
---

# Dark Factory — Docs Conformance

**Workflow ID**: $WORKFLOW_ID

---

## Invocation Contract

This file is the sanctioned Archon command entrypoint. If the runner delivers this
canonical command text inline, execute it as the authorized phase command after
verifying you are in the target checkout.

Issue context is not assumed to be present in chat. The workflow persists it at
`$ARTIFACTS_DIR/issue.json`; read that file for `resolved_number`, `intent`, title, body,
labels, and comments.

---

<!-- headless-contract:begin -->
## Headless Execution Contract

You are running with no human attached; an ended turn ends the process.

- **Never end your turn on a question or an offer.** There is no one to answer. Decide
  per this command's instructions, act, and record any reservations in the issue comment or commit
  message instead.
- **Persist this phase's artifact before your final turn ends.** Commit (and push, where
  this command says to) any repo file this phase owns; write or post any comment, label or
  `$ARTIFACTS_DIR` artifact it owns. Work left unpersisted when the turn ends is destroyed.
- **Turn end = process end.** Scheduled wakeups do not fire (do not use `ScheduleWakeup`),
  task-notifications never arrive, and pending subagent work is destroyed — and an ended
  turn is reported as success whether or not this phase's artifact exists.
- **To wait on a background subagent, poll inside your turn** (keep issuing tool calls)
  or do the work inline — never end the turn to "wait."
<!-- headless-contract:end -->

---

## What this gate is

Gate 2.5 runs after `conformance-gate` and before `push-and-pr` (#535). It asks one
question: is the documentation on this branch true of the branch's **final** diff — the
diff after validate's fixes and Gate 2's excisions and reconcile commits? It is
**single-pass and read-only**: one reviewer spawn, one verdict, no reconcile loop. This
session makes no edits, commits or pushes. Its only outputs are
`$ARTIFACTS_DIR/docs-conformance.md` (plus the `docs_conformance_*` working files below)
and, on a BLOCKED verdict, one issue comment — and, in `enforce` mode only, a board move
and the `needs-discussion` label. A docs fix is never made here: it would land after Gate 2
already passed. Remediation is a later `Continue` run, which re-runs implement, Gate 2 and
this gate.

`docs_conformance.mode` decides only the consequence of a BLOCKED verdict; this command
always builds the diff, spawns the reviewer and writes a real verdict in both modes.
`docs-conformance-gate` (`scripts/docs_verdict_gate_check.sh`) is what lets or stops
`push-and-pr`.

## Phase 1: LOAD

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
read -r DOCS_ENABLED DOCS_MODE < <(python3 -c "import yaml; d = yaml.safe_load(open('.claude/skills/refinement/config.yaml')) or {}; c = d.get('docs_conformance') or {}; m = c.get('mode', 'observe'); print(str(c.get('enabled', True)).lower(), m if m == 'enforce' else 'observe')" 2>/dev/null || echo "true observe")
echo "docs-conformance: enabled=${DOCS_ENABLED} mode=${DOCS_MODE}"
```

1. If `DOCS_ENABLED` is `false`, write the SKIPPED verdict and exit `0` — nothing else runs:
   ```bash
   printf "STATUS: SKIPPED\nREASON: docs_conformance.enabled=false\n" > "$ARTIFACTS_DIR/docs-conformance.md"
   ```
2. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for
   the reviewer subagent spawned in Phase 3; if the file is absent (image predates it),
   continue — the inline pin is authoritative.
3. Read `$ARTIFACTS_DIR/doc-impact.md` (implement Phase 4's handoff) if it exists. A missing
   or empty file does not stop this command: Phase 2 passes that fact to the reviewer, and
   Phase 4 turns it into a BLOCKED verdict whatever the reviewer says.

## Phase 2: BUILD THE EVIDENCE

Run this as one Bash call. The diff keeps Gates 2 and 3's noise exclusions but **not**
their `docs/*.md` exclusion — this is the one gate that reads doc content. It also drops the
refine/plan artifacts under `docs/superpowers/` and `docs/archive/`: they are workflow
artifacts that `push-and-pr` archives, not documentation of the change, and would crowd the
diff budget. Policy Markdown (`commands/**`, `.claude/skills/**`, `refinement-skills/**`)
stays in the diff as behavior.

```bash
BASE_REF=$(git merge-base main HEAD)

# 2a. The final diff, ranked like Gates 2/3 (fail-open to the raw diff).
RANK_IN=$(mktemp /tmp/docs_rank_in_XXXXXX.txt)
[ -f "$ARTIFACTS_DIR/token-opt-caps.env" ] && . "$ARTIFACTS_DIR/token-opt-caps.env" || true
if ! git diff main...HEAD \
  -- ':!*.lock' ':!evals/*.md' ':!bench/*.md' \
  ':!.archon/memory/**' \
  ':!codeindex.json' ':!symbolindex.json' \
  ':!docs/superpowers/**' ':!docs/archive/**' \
  2>/tmp/docs_diff_err.txt > "$RANK_IN"; then
  # git itself failed (e.g. no local main): never read that as an empty diff.
  printf 'REVIEWER_ERROR: git diff main...HEAD failed: %s\n' "$(head -c 400 /tmp/docs_diff_err.txt)" \
    > "$ARTIFACTS_DIR/docs_conformance_review.md"
  rm -f "$RANK_IN"
  echo "docs-conformance: git diff failed — Phase 4 writes UNCERTAIN/BLOCKED"
elif [ ! -s "$RANK_IN" ]; then
  printf "STATUS: PASS\nGATE_TYPE: docs-conformance\nFINDINGS_COUNT: 0\nSEVERITY: none\nVERDICT: CONFORMS\nREASON: empty diff\n" \
    > "$ARTIFACTS_DIR/docs-conformance.md"
  rm -f "$RANK_IN"
  echo "docs-conformance: empty diff — PASS, nothing to document"
else
  # TARGET-PATH: diff_rank.py resolves under dark-factory/ in the clone.
  python3 dark-factory/scripts/diff_rank.py \
    --diff "$RANK_IN" \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --config ".claude/skills/refinement/config.yaml" \
    --hotspots "docs/codeindex-hotspots.md" \
    2>/tmp/docs_rank_err.txt > "$ARTIFACTS_DIR/docs_conformance_diff.txt" \
    || { echo "diff_rank: ranking failed ($(cat /tmp/docs_rank_err.txt)) — using raw diff"; cp "$RANK_IN" "$ARTIFACTS_DIR/docs_conformance_diff.txt"; }
  rm -f "$RANK_IN"
fi

# 2b. Baseline and branch copies of every documentation file the diff touches.
DOC_PATHS=$(git diff main...HEAD --name-only -- '*.md' \
  ':!commands/**' ':!.claude/skills/**' ':!refinement-skills/**' \
  ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' \
  ':!docs/superpowers/**' ':!docs/archive/**' 2>/dev/null)
BASE_DIR="$ARTIFACTS_DIR/docs-conformance-baseline"
rm -rf "$BASE_DIR" && mkdir -p "$BASE_DIR"
{
  echo "| Doc file | Baseline (main) copy | Branch copy |"
  echo "|---|---|---|"
  printf '%s\n' "$DOC_PATHS" | while IFS= read -r P; do
    [ -z "$P" ] && continue
    if git cat-file -e "${BASE_REF}:${P}" 2>/dev/null; then
      mkdir -p "$BASE_DIR/$(dirname "$P")"
      git show "${BASE_REF}:${P}" > "$BASE_DIR/$P"
      BASE="\`$BASE_DIR/$P\`"
    else
      BASE="(new on this branch)"
    fi
    if [ -f "$P" ]; then BR="\`$(pwd)/$P\`"; else BR="(deleted on this branch)"; fi
    echo "| \`$P\` | $BASE | $BR |"
  done
  [ -n "$DOC_PATHS" ] || echo "| (no documentation file changed on this branch) | | |"
} > "$ARTIFACTS_DIR/docs_conformance_doc_files.md"

# 2c. The doc-impact claim to audit.
if [ -s "$ARTIFACTS_DIR/doc-impact.md" ] \
   && grep -qE '^OVERALL: (UPDATED|NO_IMPACT: .+)' "$ARTIFACTS_DIR/doc-impact.md"; then
  cp "$ARTIFACTS_DIR/doc-impact.md" "$ARTIFACTS_DIR/docs_conformance_doc_impact.md"
else
  {
    echo "(doc-impact.md is missing, empty, or has no OVERALL: line — this is itself a MATERIAL mismatch.)"
    if [ -s "$ARTIFACTS_DIR/doc-impact.md" ]; then
      printf '\nRaw content:\n\n'
      cat "$ARTIFACTS_DIR/doc-impact.md"
    fi
  } > "$ARTIFACTS_DIR/docs_conformance_doc_impact.md"
fi
```

If the empty-diff branch fired, `docs-conformance.md` is already written: skip to Phase 7.
If the git-diff-failed branch fired, skip Phase 3 entirely (its `rm -f` would delete the
`REVIEWER_ERROR:` file) and go to Phase 4–5, which writes `UNCERTAIN` → BLOCKED.

## Phase 3: REVIEW

1. Resolve the rubric clone-live-first and render the prompt:
   ```bash
   if [ -f ".claude/skills/docs-conformance/RUBRIC.md" ]; then
     RUBRIC_FILE=".claude/skills/docs-conformance/RUBRIC.md"
   else
     RUBRIC_FILE="/opt/refinement-skills/docs-conformance-reviewer-prompt.md"
   fi

   rm -f "$ARTIFACTS_DIR/docs_conformance_review.md"
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py render-prompt \
     --template "$RUBRIC_FILE" \
     --set DIFF_CONTENT=@"$ARTIFACTS_DIR/docs_conformance_diff.txt" \
     --set DOC_FILES=@"$ARTIFACTS_DIR/docs_conformance_doc_files.md" \
     --set DOC_IMPACT=@"$ARTIFACTS_DIR/docs_conformance_doc_impact.md" \
     --out "$ARTIFACTS_DIR/docs_conformance_prompt.md" \
     || printf 'REVIEWER_ERROR: render-prompt failed for %s\n' "$RUBRIC_FILE" > "$ARTIFACTS_DIR/docs_conformance_review.md"
   ```
   A render failure (including a missing rubric file) is not an abort: it leaves a
   `REVIEWER_ERROR:` file, skip step 2, and Phase 4 maps it to `UNCERTAIN` → BLOCKED.
2. Spawn exactly one reviewer subagent using the Agent tool:
   - `description`: "Docs conformance review: docs vs final diff"
   - `model`: `claude-opus-5-5` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.282 `opus` resolves to `claude-opus-5-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/docs_conformance_prompt.md`
   - No shadow spawn: VERIFIER-CONTRACT pairs shadows only with the two conformance reviewers.
3. Write the subagent's full response, verbatim, to `$ARTIFACTS_DIR/docs_conformance_review.md`
   with the Write tool. If the spawn errored, timed out, was refused, or returned nothing,
   write one line instead: `REVIEWER_ERROR: <the error text, or "empty response">`. Do not
   re-spawn, retry or summarize — this gate is single-pass.

## Phase 4–5: PARSE THE VERDICT AND WRITE IT

Run as one Bash call. It reads only files, so it does not depend on shell state from earlier
calls. Refusal, timeout, tool error and an unparseable response all map to `UNCERTAIN`, which
is `BLOCKED` — never `PASS` (VERIFIER-CONTRACT's refusal rule; there is no reconcile cycle to
consume first). A missing or incomplete `doc-impact.md` forces `BLOCKED` whatever the
reviewer said.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
read -r _EN DOCS_MODE < <(python3 -c "import yaml; d = yaml.safe_load(open('.claude/skills/refinement/config.yaml')) or {}; c = d.get('docs_conformance') or {}; m = c.get('mode', 'observe'); print(str(c.get('enabled', True)).lower(), m if m == 'enforce' else 'observe')" 2>/dev/null || echo "true observe")
REVIEW="$ARTIFACTS_DIR/docs_conformance_review.md"
VERDICT_LINE=$(grep -m1 '^\*\*Verdict:\*\*' "$REVIEW" 2>/dev/null || true)
MATERIAL=$(grep -c '^- \[MATERIAL\]' "$REVIEW" 2>/dev/null || true)
MINOR=$(grep -c '^- \[MINOR\]' "$REVIEW" 2>/dev/null || true)
MATERIAL=${MATERIAL:-0}; MINOR=${MINOR:-0}
REASON=""
case "$VERDICT_LINE" in
  *"⛔"*) VERDICT=MATERIAL;  STATUS=BLOCKED; COUNT=$((MATERIAL + MINOR)); SEV=high ;;
  *"⚠"*)  VERDICT=MINOR;     STATUS=PASS;    COUNT=$MINOR;              SEV=low ;;
  *"✅"*) VERDICT=CONFORMS;  STATUS=PASS;    COUNT=0;                   SEV=none ;;
  *)      VERDICT=UNCERTAIN; STATUS=BLOCKED; COUNT=1;                   SEV=high
          REASON="no parseable **Verdict:** line (refusal, timeout, tool error or bad output)" ;;
esac
DOC_IMPACT=present
if ! { [ -s "$ARTIFACTS_DIR/doc-impact.md" ] && grep -qE '^OVERALL: (UPDATED|NO_IMPACT: .+)' "$ARTIFACTS_DIR/doc-impact.md"; }; then
  DOC_IMPACT=missing
  if [ "$STATUS" = "PASS" ]; then
    STATUS=BLOCKED; COUNT=$((COUNT + 1)); SEV=high
    REASON="doc-impact.md is missing, empty, or has no OVERALL: line"
  fi
fi
{
  emit_verdict "docs-conformance" "$STATUS" "$COUNT" "$SEV"
  printf "VERDICT: %s\nMODE: %s\nDOC_IMPACT: %s\n" "$VERDICT" "$DOCS_MODE" "$DOC_IMPACT"
  [ -n "$REASON" ] && printf "REASON: %s\n" "$REASON"
  printf "\n---\n\n"
  cat "$REVIEW" 2>/dev/null || echo "(no reviewer output was recorded)"
} > "$ARTIFACTS_DIR/docs-conformance.md"
echo "docs-conformance: STATUS=${STATUS} VERDICT=${VERDICT} mode=${DOCS_MODE}"
```

If `STATUS` is `PASS`, skip to Phase 7. No comment is posted on a pass.

## Phase 6: ON BLOCKED — branch on `docs_conformance.mode`

Run as one Bash call. Each body starts with its `##` heading — `comment_digest.py` recognizes
a gate verdict only by a registered heading at the very start of the body, and the
upsert marker may sit anywhere in it. `observe` (the default) posts the same evidence as an informational
comment under its own marker and makes **no** label or board change, so a false block costs
no human touch. `enforce` posts the blocking comment, moves the board to **Blocked** and adds
`needs-discussion` — the same board/label consequence as Gate 2's BLOCKED path (no new
status or label).

```bash
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
DOCS_MODE=$(grep -m1 '^MODE:' "$ARTIFACTS_DIR/docs-conformance.md" | awk '{print $2}')
REASON=$(grep -m1 '^REASON:' "$ARTIFACTS_DIR/docs-conformance.md" | sed 's/^REASON: //')
FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
REVIEW_TEXT=$(cat "$ARTIFACTS_DIR/docs_conformance_review.md" 2>/dev/null || echo "(no reviewer output was recorded)")
NEXT_STEPS="### Next Steps

- Fix the documentation (or \`doc-impact.md\`'s assessment) to match the diff, then re-run: \`docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"\`. A \`Continue\` run re-runs implementation, Gate 2 and Gate 2.5 on the new diff.
- If the reviewer is wrong, say why in a comment; a human decides."
if [ "$DOCS_MODE" = "enforce" ]; then
  MARKER="<!-- df-docs-conformance-blocked -->"
  BODY="## Docs Conformance (Gate 2.5) — Blocked
${MARKER}

The documentation on this branch does not match its final diff, so the PR was not published (\`docs_conformance.mode: enforce\`).${REASON:+

**Reason:** ${REASON}}

${REVIEW_TEXT}

${NEXT_STEPS}

---
${FOOTER}"
else
  MARKER="<!-- df-docs-conformance-observed -->"
  BODY="## Docs Conformance (Gate 2.5) — Mismatch observed (informational)
${MARKER}

\`docs_conformance.mode\` is \`observe\`: this verdict would have blocked the PR in \`enforce\` mode ([#536](https://github.com/omniscient/dark-factory/issues/536)). Nothing was blocked, and no label or board change was made.${REASON:+

**Reason:** ${REASON}}

${REVIEW_TEXT}

---
${FOOTER}"
fi
TMPFILE=$(mktemp /tmp/df-docs-conformance-XXXXXX.md)
printf '%s' "$BODY" > "$TMPFILE"
python3 dark-factory/scripts/factory_core/providers/cli.py tracker comment \
  --id "$ISSUE_NUM" --marker "$MARKER" --body-file "$TMPFILE" || echo "WARNING: docs-conformance comment post failed"  # TARGET-PATH
rm -f "$TMPFILE"
if [ "$DOCS_MODE" = "enforce" ]; then
  python3 dark-factory/scripts/factory_core/providers/cli.py \
    tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
  python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
fi
```

## Phase 7: EXIT

Exit `0` after PASS or SKIPPED, and after BLOCKED when `MODE` is `observe`; exit `1` only
after BLOCKED in `enforce` mode. The exit code is
forward-compatibility only, as in `dark-factory-conformance.md` Phase 5: a `command:` node's
internal `exit 1` does not reliably surface to the DAG executor (#212, #271). The
`docs-conformance-gate` node reads `docs-conformance.md` and decides whether `push-and-pr`
runs.
~~~~

  Notes for whoever edits this file later: the `# TARGET-PATH` marker on the `diff_rank.py`
  call sits on its own line because a `\` followed by a comment is not a line continuation;
  every Bash block from Phase 4 on reads only files, because shell state does not survive
  between Bash tool calls; the `⚠` match deliberately omits the U+FE0F variation selector so
  both renderings of the emoji match.

- [ ] **Step 4: Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_command.py tests/test_command_headless_contract.py tests/test_verifier_contract_doc_referenced.py -q` → 0 failed.
  `PYTHONPATH=scripts python -m pytest tests/test_comment_digest.py -q` → `34 passed` (its drift
  test now sees the command's `— Blocked` heading, which Task 4 registered).

- [ ] **Step 5: Smoke-run the command's Bash blocks against a throwaway repo** (catches
  quoting mistakes the static tests cannot). From the repo root:

~~~~bash
REPO=$(pwd); T=$(mktemp -d); mkdir -p "$T/clone" "$T/arts"; cd "$T/clone"
git init -q -b main && git config user.email t@t && git config user.name t
mkdir -p docs commands scripts dark-factory .claude/skills/refinement
printf '# Lifecycle\nold\n' > docs/run-lifecycle.md; echo '# cmd' > commands/x.md; echo 'a=1' > scripts/a.sh
git add -A && git commit -qm base && git checkout -qb feat/issue-535-x
echo 'new gate text' >> docs/run-lifecycle.md; echo 'new phase' >> commands/x.md; echo 'b=2' >> scripts/a.sh
git add -A && git commit -qm change
ln -s "$REPO/scripts" dark-factory/scripts
printf 'docs_conformance:\n  enabled: true\n  mode: enforce\n' > .claude/skills/refinement/config.yaml
echo '{"resolved_number":535}' > "$T/arts/issue.json"
printf -- '- `scripts/a.sh` -> NO_IMPACT: internal\nOVERALL: UPDATED\n' > "$T/arts/doc-impact.md"
export ARTIFACTS_DIR="$T/arts"
python3 - "$REPO/commands/dark-factory-docs-conformance.md" "$T" <<'PY'
import re, sys
blocks = re.findall(r'```bash\n(.*?)```', open(sys.argv[1]).read(), re.S)
for i, b in enumerate(blocks):
    open(f"{sys.argv[2]}/blk{i}.sh", "w").write("\n".join(l[3:] if l.startswith("   ") else l for l in b.split("\n")))
PY
bash "$T/blk2.sh" && cat "$T/arts/docs_conformance_doc_files.md"
printf '**Verdict:** ⛔ Material divergence\n- [MATERIAL] `docs/run-lifecycle.md`#x — wrong\n' > "$T/arts/docs_conformance_review.md"
bash "$T/blk4.sh" && head -7 "$T/arts/docs-conformance.md"
cd "$REPO"
~~~~
  Expected: the doc-files table lists `docs/run-lifecycle.md` with a
  `…/docs-conformance-baseline/docs/run-lifecycle.md` baseline path and its branch path
  (and not `commands/x.md`); then `STATUS: BLOCKED`, `GATE_TYPE: docs-conformance`,
  `FINDINGS_COUNT: 1`, `SEVERITY: high`, `VERDICT: MATERIAL`, `MODE: enforce`,
  `DOC_IMPACT: present`.

- [ ] **Step 6: Commit.**
  `git add commands/dark-factory-docs-conformance.md refinement-skills/docs-conformance-reviewer-prompt.md tests/test_docs_conformance_command.py tests/test_command_headless_contract.py tests/test_verifier_contract_doc_referenced.py && git commit -m "feat: add dark-factory-docs-conformance command and reviewer rubric (#535)"`

---

## Task 6: Wire the four nodes into the DAG

**Files:** Create `tests/test_docs_conformance_dag.py`; modify `tests/test_verdict_gate_dag.py`,
`tests/test_budget_enforce_dag.py` (node expectations), `workflows/archon-dark-factory.yaml`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_docs_conformance_dag.py`:

~~~~python
"""DAG wiring for Gate 2.5 (#535): budget-docs-conformance -> enforce-budget-docs-conformance
-> docs-conformance -> docs-conformance-gate sit between conformance-gate and push-and-pr,
new/continue only, and push-and-pr (a plain all_success node) depends on both gates.

The executing tests run the real docs-conformance-gate bash body against a stub clone, so
"no PR is created" is proven the same way #271 proved it for conformance-gate: the gate
node exits 1, and push-and-pr — which has no trigger_rule — never runs after a failed
upstream.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_NEW_OR_CONTINUE = "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"


def _nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def test_chain_order_and_dependencies():
    n = _nodes()
    assert n["budget-docs-conformance"]["depends_on"] == ["conformance-gate"]
    assert n["enforce-budget-docs-conformance"]["depends_on"] == ["budget-docs-conformance"]
    assert n["docs-conformance"]["depends_on"] == ["enforce-budget-docs-conformance", "conformance-gate"]
    assert n["docs-conformance-gate"]["depends_on"] == ["docs-conformance"]


def test_push_and_pr_requires_both_gates_with_all_success():
    node = _nodes()["push-and-pr"]
    assert node["depends_on"] == ["conformance-gate", "docs-conformance-gate"]
    assert "trigger_rule" not in node, "a skip-tolerant rule would let a PR open past a failed gate"


@pytest.mark.parametrize("node_id", [
    "budget-docs-conformance", "enforce-budget-docs-conformance",
    "docs-conformance", "docs-conformance-gate",
])
def test_new_continue_only_and_no_trigger_rule(node_id):
    node = _nodes()[node_id]
    assert node["when"] == _NEW_OR_CONTINUE
    assert "trigger_rule" not in node


def test_docs_conformance_command_node():
    node = _nodes()["docs-conformance"]
    assert node["command"] == "dark-factory-docs-conformance"
    assert node["denied_tools"] == [], "Requirement 16: no tool-restriction change in this ticket"
    assert node["idle_timeout"] == 600000
    assert (_REPO_ROOT / "commands" / "dark-factory-docs-conformance.md").is_file()


def test_gate_node_calls_strict_script_not_shared_one():
    bash = _nodes()["docs-conformance-gate"]["bash"]
    assert "docs_verdict_gate_check.sh" in bash
    assert "/verdict_gate_check.sh" not in bash, "verdict_gate_check.sh passes ERROR (Requirement 3)"
    assert "docs-conformance.md" in bash and "Docs Conformance (Gate 2.5)" in bash
    assert "docs_conformance" in bash and "'observe'" in bash
    assert "|| true" not in bash, "the gate script's exit code IS the gate signal"
    assert _nodes()["docs-conformance-gate"]["timeout"] == 30000


def test_budget_nodes_use_docs_conformance_scenario():
    n = _nodes()
    assert "--scenario docs-conformance" in n["budget-docs-conformance"]["bash"]
    assert "--doc-impact-file" in n["budget-docs-conformance"]["bash"]
    assert "budget_gate.sh\" docs-conformance || true" in n["enforce-budget-docs-conformance"]["bash"]


def test_resolve_path_unchanged():
    assert _nodes()["push-resolve"]["depends_on"] == ["validate"]


def test_dag_validators_pass():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check as check_dag
    from check_workflow_when import check as check_when
    assert check_dag(_WORKFLOW) == []
    assert check_when(_WORKFLOW) == []


# ── executing the real gate node body ────────────────────────────────────────

_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      case "$*" in *"tracker get"*) printf '{"labels":[]}\n' ;; esac
      return 0 ;;
    *factory_core/cli.py*marker*) echo "*footer*" ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''


@pytest.fixture()
def clone(tmp_path):
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "jq")):
        pytest.skip("needs a POSIX host with bash and jq")
    root = tmp_path / "clone"
    (root / "dark-factory").mkdir(parents=True)
    (root / "dark-factory" / "scripts").symlink_to(_REPO_ROOT / "scripts")
    (root / ".claude" / "skills" / "refinement").mkdir(parents=True)
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "issue.json").write_text('{"resolved_number": 535}')
    return root, arts


def _run_gate(clone, mode_yaml, verdict):
    root, arts = clone
    cfg = root / ".claude" / "skills" / "refinement" / "config.yaml"
    if mode_yaml is None:
        cfg.unlink(missing_ok=True)
    else:
        cfg.write_text(mode_yaml)
    vf = arts / "docs-conformance.md"
    vf.unlink(missing_ok=True)
    if verdict is not None:
        vf.write_text(verdict)
    log = arts / "pcli.log"
    log.write_text("")
    env = dict(os.environ, CLONE_DIR=str(root), ARTIFACTS_DIR=str(arts), PCLI_LOG=str(log))
    bash = _nodes()["docs-conformance-gate"]["bash"]
    proc = subprocess.run(["bash", "-c", _PRELUDE + "\n" + bash], cwd=str(root),
                          env=env, capture_output=True, text=True)
    return proc.returncode, log.read_text()


_ENFORCE = "docs_conformance:\n  enabled: true\n  mode: enforce\n"
_OBSERVE = "docs_conformance:\n  enabled: true\n  mode: observe\n"


@pytest.mark.parametrize("verdict", [
    None, "garbage\n", "STATUS: ERROR\n", "STATUS: BLOCKED\nGATE_TYPE: docs-conformance\n",
])
def test_enforce_config_blocks_push_and_pr(clone, verdict):
    rc, _ = _run_gate(clone, _ENFORCE, verdict)
    assert rc == 1, "gate exit 1 skips push-and-pr: no PR is created"


@pytest.mark.parametrize("verdict", ["STATUS: PASS\n", "STATUS: SKIPPED\n"])
def test_enforce_config_passes_clean_verdicts(clone, verdict):
    rc, _ = _run_gate(clone, _ENFORCE, verdict)
    assert rc == 0


@pytest.mark.parametrize("cfg", [_OBSERVE, "docs_conformance:\n  enabled: true\n", "{}\n", ": not yaml [\n", None])
def test_observe_default_never_blocks(clone, cfg):
    rc, log = _run_gate(clone, cfg, "STATUS: BLOCKED\n")
    assert rc == 0, "observe (and every config-read failure) must not withhold the PR"
    assert log == ""
~~~~

  In `tests/test_verdict_gate_dag.py`, `test_push_and_pr_depends_on_conformance_gate`, change
  `    assert node["depends_on"] == ["conformance-gate"]` to
  `    assert node["depends_on"] == ["conformance-gate", "docs-conformance-gate"]  # #535 adds Gate 2.5`.

  In `tests/test_budget_enforce_dag.py`:
  - add `    "enforce-budget-docs-conformance",` as the last entry of each of the five
    `@pytest.mark.parametrize("node_id", [...])` lists that end with `    "enforce-budget-code-review",`
    (`test_enforce_budget_node_exists`, `_no_trigger_rule`, `_nonfatal`, `_clone_dir_fallback`, `_timeout`);
  - add `    ("enforce-budget-docs-conformance", "budget-docs-conformance"),` as the last entry of
    `test_enforce_budget_node_depends_on_budget`'s list;
  - add `    ("docs-conformance", "enforce-budget-docs-conformance", "budget-docs-conformance"),` as the
    last entry of `test_command_node_depends_on_enforce_not_budget`'s list;
  - after `test_enforce_budget_code_review_when`, add:

~~~~python


def test_enforce_budget_docs_conformance_when():
    nodes = _workflow_nodes()
    when = nodes.get("enforce-budget-docs-conformance", {}).get("when", "")
    assert "new" in when and "continue" in when, \
        "enforce-budget-docs-conformance must be gated on new|continue intent"
~~~~

- [ ] **Step 2: Run and confirm failure.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_dag.py tests/test_verdict_gate_dag.py tests/test_budget_enforce_dag.py -q`
  → `28 failed, 69 passed` (missing nodes → `KeyError`).

- [ ] **Step 3: Edit `workflows/archon-dark-factory.yaml`.**
  1. Insert this block **after the `    timeout: 30000` line that ends the
     `conformance-gate` node** (line 1168 on main f457c2c; locate it by content, not by
     number) and before the blank line + `  # push-resolve: …` comment. The
     inserted text below starts with a blank line and is exactly **59 lines**:

~~~~yaml

  # Context budget telemetry — pre-prompt token estimate for the docs-conformance phase (#535).
  # Runs after conformance-gate, so it measures the final post-Gate-2 diff, with the same
  # pathspec as dark-factory-docs-conformance.md Phase 2 (three-dot: telemetry, not OOS).
  - id: budget-docs-conformance
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      _CLONE="${CLONE_DIR:-.}"
      _RUN="${RUN_ID:-$(basename "${ARTIFACTS_DIR:-/tmp/budget}")}"
      DIFF_TMP=$(mktemp)
      git diff main...HEAD -- ':!*.lock' ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' ':!codeindex.json' ':!symbolindex.json' ':!docs/superpowers/**' ':!docs/archive/**' > "$DIFF_TMP" 2>/dev/null || true
      python3 "$_CLONE/dark-factory/scripts/context_budget.py" \
        --scenario docs-conformance \
        --issue-num "$ISSUE" \
        --run-id "$_RUN" \
        --artifacts-dir "$ARTIFACTS_DIR" \
        --clone-dir "$_CLONE" \
        --doc-impact-file "$ARTIFACTS_DIR/doc-impact.md" \
        --diff-file "$DIFF_TMP" \
        --out "$ARTIFACTS_DIR/context-budget.docs-conformance.json" || true
      rm -f "$DIFF_TMP"
    depends_on: [conformance-gate]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000

  - id: enforce-budget-docs-conformance
    bash: |
      bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_gate.sh" docs-conformance || true
    depends_on: [budget-docs-conformance]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000

  # Layer 3.6: Verify the docs describe the final post-Gate-2 diff (Gate 2.5 — docs vs diff, #535).
  # Single-pass and read-only by instruction; it sees docs/*.md, which Gates 2 and 3 exclude.
  - id: docs-conformance
    command: dark-factory-docs-conformance
    denied_tools: []  # #535: no phase_levels key of its own — runs in the fix/continue container at level 5; read-only by instruction only, tool restriction deferred to #536
    depends_on: [enforce-budget-docs-conformance, conformance-gate]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    idle_timeout: 600000

  # Verdict gate for Gate 2.5: reads docs-conformance.md like conformance-gate reads
  # conformance.md (#212, #271), but through docs_verdict_gate_check.sh, whose enforce mode
  # fails closed on ERROR too. docs_conformance.mode selects observe (default: log, never
  # block) or enforce; any read failure or other value is observe.
  - id: docs-conformance-gate
    bash: |
      _CLONE="${CLONE_DIR:-.}"
      DOCS_MODE=$(python3 -c "import sys, yaml; d = yaml.safe_load(open(sys.argv[1])) or {}; print((d.get('docs_conformance') or {}).get('mode', 'observe'))" \
        "$_CLONE/.claude/skills/refinement/config.yaml" 2>/dev/null || echo observe)
      # TARGET-PATH: docs_verdict_gate_check.sh resolves under dark-factory/ in the clone.
      bash "$_CLONE/dark-factory/scripts/docs_verdict_gate_check.sh" \
        "$ARTIFACTS_DIR/docs-conformance.md" \
        "$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")" \
        "Docs Conformance (Gate 2.5)" \
        "$DOCS_MODE"
    depends_on: [docs-conformance]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
~~~~
  2. In the `push-and-pr` node change `    depends_on: [conformance-gate]` (the one right after
     `        echo "Created PR #$PR_NUM_NEW"` / `      fi`) to
     `    depends_on: [conformance-gate, docs-conformance-gate]`. Leave it without a `trigger_rule`.

  Note: Archon substitutes `$<node-id>.output` text references even inside comments; the
  block above contains none besides the standard `$parse-intent.output.intent` in `when:`.

- [ ] **Step 4: Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_dag.py tests/test_verdict_gate_dag.py tests/test_budget_enforce_dag.py tests/test_side_effect_dag.py tests/test_push_gate_dag.py tests/test_push_gate_dag_exec.py -q` → 0 failed.
  `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml` → `DAG trigger_rule check passed for 1 workflow file(s).`
  `python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml` → `when: expression lint passed for 1 workflow file(s).`
  `grep -n "^  - id: \(budget-docs-conformance\|enforce-budget-docs-conformance\|docs-conformance\|docs-conformance-gate\|push-resolve\|push-and-pr\|report\)$" workflows/archon-dark-factory.yaml`
  → `1173`, `1194`, `1203`, `1214`, `1230`, `1252`, `1496` (Task 9's doc citations depend on these).

- [ ] **Step 5: Commit.**
  `git add workflows/archon-dark-factory.yaml tests/test_docs_conformance_dag.py tests/test_verdict_gate_dag.py tests/test_budget_enforce_dag.py && git commit -m "feat: wire Gate 2.5 docs-conformance nodes before push-and-pr (#535)"`

---

## Task 7: Implement Phase 4 DOCUMENT writes `doc-impact.md` from the diff's content

**Files:** Create `tests/test_implement_doc_impact.py`; modify `commands/dark-factory-implement.md`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_implement_doc_impact.py`:

~~~~python
"""implement Phase 4 DOCUMENT (#535): derives doc impact from the diff's content, not only
the #55 path table, and always hands Gate 2.5 a doc-impact.md with an OVERALL: line."""
from pathlib import Path

_CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-implement.md"


def _phase4():
    text = _CMD.read_text(encoding="utf-8")
    start = text.index("## Phase 4: DOCUMENT")
    return text[start:text.index("## Phase 5: MEMORY UPDATE")]


def test_no_silent_skip_when_no_mapping_row_matches():
    p4 = _phase4()
    assert "skip this phase entirely" not in p4
    assert "If a path matches no pattern, skip it." not in p4
    assert "is **not** evidence of no impact" in p4


def test_mapping_table_from_55_is_preserved():
    p4 = _phase4()
    for row in ("| `backend/app/models/*.py` | `ARCHITECTURE.md` | Database Models table |",
                "| `.env.example` | `ENV_VARIABLES.md` | Relevant section |",
                "| `frontend/src/pages/*.tsx` | `ARCHITECTURE.md` | Pages table |"):
        assert row in p4
    assert "`docs/database-schema.md` is auto-generated — never edit it." in p4
    assert 'git commit -m "docs: update architecture map for <feature-slug>"' in p4


def test_behavior_scan_covers_policy_markdown_and_reference_docs():
    p4 = _phase4()
    assert "read the diff content, not just the paths" in p4
    assert "`commands/**`" in p4
    for doc in ("docs/run-lifecycle.md", "docs/scheduler.md", "docs/features.md",
                "docs/configuration.md", "README.md"):
        assert doc in p4, doc


def test_doc_impact_artifact_contract():
    p4 = _phase4()
    assert "$ARTIFACTS_DIR/doc-impact.md` — always" in p4
    assert "OVERALL: UPDATED" in p4 and "OVERALL: NO_IMPACT: <reason" in p4
    assert "-> NO_IMPACT:" in p4 and "-> UPDATED" in p4
    assert "do not\n   commit it" in p4


def test_continue_reads_gate_2_5_feedback():
    p4 = _phase4()
    assert "df-docs-conformance-blocked" in p4 and "df-docs-conformance-observed" in p4


def test_report_points_at_doc_impact():
    text = _CMD.read_text(encoding="utf-8")
    report = text[text.index("## Phase 6: REPORT"):]
    assert "`OVERALL:` line from `$ARTIFACTS_DIR/doc-impact.md`" in report
    assert "5 bullets" in report and "5 standard bullets" in report
~~~~

- [ ] **Step 2: Run.** `PYTHONPATH=scripts python -m pytest tests/test_implement_doc_impact.py -q` → `5 failed, 1 passed`
  (the #55 mapping-table test already passes — it guards that the rewrite keeps it).

- [ ] **Step 3a: Replace Phase 4.** In `commands/dark-factory-implement.md`, replace everything
  from the line `## Phase 4: DOCUMENT` up to (not including) `## Phase 5: MEMORY UPDATE` with
  the text below (ending with one blank line). The mapping table, its rules and the
  `docs: update architecture map for <feature-slug>` commit message are #55's, kept as-is
  apart from the "matches no pattern, skip it" rule — so behavior for targets the table
  covers is unchanged, and #482 can still swap the table for an adapter-driven one.

~~~~markdown
## Phase 4: DOCUMENT

Documentation is part of the change. Gate 2.5 (`dark-factory-docs-conformance`, after Gate 2
and before `push-and-pr`) independently checks the branch's docs against the final diff and
audits the `doc-impact.md` you write here — so a skipped or hand-waved assessment is caught,
not trusted.

1. Get the authoritative changed-file list: `git diff main...HEAD --name-only`. Cross-check
   against the file list in your Phase 3 notes; the git list wins.
2. Classify each path against this mapping to produce the list of `(doc_file, section)` pairs to update:

   | Changed file pattern | Documentation target | Section |
   |---|---|---|
   | `backend/app/models/*.py` | `ARCHITECTURE.md` | Database Models table |
   | `backend/app/models/*.py` | `PROJECT_STRUCTURE.md` | `models/` directory entry |
   | `backend/app/routers/*.py` | `ARCHITECTURE.md` | Routers table |
   | `backend/app/routers/*.py` | `PROJECT_STRUCTURE.md` | `routers/` directory entry |
   | `backend/app/services/*.py` | `ARCHITECTURE.md` | Services table |
   | `backend/app/services/*.py` | `PROJECT_STRUCTURE.md` | `services/` directory entry |
   | `frontend/src/pages/*.tsx` | `ARCHITECTURE.md` | Pages table |
   | `.env.example` | `ENV_VARIABLES.md` | Relevant section |
   | `docker-compose.yml` (new service added/removed) | `ARCHITECTURE.md` | Service Topology section |
   | `CLAUDE.md`-affecting changes (new port, command, pattern) | `CLAUDE.md` | Relevant section |

   Rules:
   - A path that matches no row is **not** evidence of no impact — step 3 still covers it.
   - If a file was modified but nothing added/removed (e.g. only existing model fields changed), still read the current doc row and update it if the description is now inaccurate.
   - If a file was deleted, remove the corresponding doc row.
   - `CLAUDE.md` is only touched if the change adds/removes a developer-facing command, port, or architectural pattern. This is rare and requires explicit judgment.
   - `docs/database-schema.md` is auto-generated — never edit it. The same holds for any other
     doc that says it is generated.
3. **Behavior scan — read the diff content, not just the paths.** Run `git diff main...HEAD`
   and list every operator- or developer-visible behavior the change adds, removes or alters:
   a workflow node or its order, a gate's pass/block rule, a config key or default, an env
   var, an artifact file, a label, a board status, a comment marker, a command's phase
   behavior (policy Markdown under `commands/**`, `.claude/skills/**`, `refinement-skills/**`
   is behavior), a CLI flag, a script's exit-code contract, scheduler timing. For each one,
   find the docs that describe that area — Grep `README.md`, `docs/`, `ARCHITECTURE.md`,
   `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `CLAUDE.md`, and, where the repo has them, the
   reference docs `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md`,
   `docs/configuration.md` — and add a `(doc_file, section)` pair for every doc that is now
   false or incomplete. A touched doc that cites line numbers of a file you changed must have
   those citations re-derived from the changed file.
   - On `continue`, also read the latest "Docs Conformance (Gate 2.5)" comment on the issue
     (marker `df-docs-conformance-blocked` or `df-docs-conformance-observed`), if any, and
     treat each `[MATERIAL]` item in it as a pair to fix — after checking it against the
     current diff: these comments are upserted, so one can predate later commits.
4. For each `(doc_file, section)` pair from steps 2 and 3:
   a. Read the current section in full
   b. Read the changed source file(s) that triggered this pair
   c. Write the updated section content: add a new row, update an existing row, or remove a deleted entry. Read surrounding entries and match their style (inline comments, column widths, etc.)
5. Write `$ARTIFACTS_DIR/doc-impact.md` — always, even when nothing needed a doc change. One
   entry per changed behavior from steps 2–3, then exactly one `OVERALL:` line:
   ```markdown
   # Documentation impact — issue #<N>

   - `<changed file or behavior>` -> UPDATED `<doc file>#<section>` — <what the doc now says>
   - `<changed file or behavior>` -> NO_IMPACT: <why no existing doc describes it or became false>

   OVERALL: UPDATED
   ```
   Use `OVERALL: UPDATED` when any doc changed, otherwise `OVERALL: NO_IMPACT: <reason that
   covers the whole diff>`. A `NO_IMPACT` reason must say why no existing doc is now false or
   incomplete; "no mapping row matched" or "internal change" alone is not a reason. A missing
   file or a missing `OVERALL:` line makes Gate 2.5 block. This file is a run artifact — do not
   commit it.
6. If any doc changed, commit the doc changes: `git commit -m "docs: update architecture map for <feature-slug>"` — derive `<feature-slug>` from the branch name (e.g. `feat/issue-12-new-router` → `new-router`).

### PHASE_4_CHECKPOINT
- [ ] `git diff main...HEAD --name-only` classified against the mapping table
- [ ] Diff content scanned for behavior changes, including ones no mapping row covers
- [ ] All affected doc sections updated
- [ ] `$ARTIFACTS_DIR/doc-impact.md` written with one entry per changed behavior and an `OVERALL:` line
- [ ] `docs:` commit created, or `OVERALL: NO_IMPACT: <reason>` recorded

~~~~

- [ ] **Step 3b: Phase 6 REPORT** — add the documentation-impact bullet and make the
  "4 bullets" wording say 5. Run this from the repo root (save it outside the repo, e.g.
  `/tmp/impl6_535.py`; it asserts each anchor matches once):

~~~~python
from pathlib import Path

p = Path("commands/dark-factory-implement.md")
s = p.read_text(encoding="utf-8")
EDITS = [
    ("""- Migrations created (if any)
- Any decisions or trade-offs made
""", """- Migrations created (if any)
- Any decisions or trade-offs made
- Documentation impact: the `OVERALL:` line from `$ARTIFACTS_DIR/doc-impact.md` (the full assessment stays in that file)
"""),
    ("Keep a green-path report to the 4 bullets above (files, tests, migrations, decisions)",
     "Keep a green-path report to the 5 bullets above (files, tests, migrations, decisions, documentation impact)"),
    ("before the\n4 standard bullets", "before the\n5 standard bullets"),
]
for old, new in EDITS:
    assert s.count(old) == 1, f"expected 1 match: {old[:60]!r}"
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print(f"patched {p}: {len(EDITS)} edits")
~~~~
  Expected: `patched commands/dark-factory-implement.md: 3 edits`.

- [ ] **Step 4: Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_implement_doc_impact.py tests/test_command_headless_contract.py -q` → 0 failed.
  `grep -n "^## Phase\|^### PHASE_4" commands/dark-factory-implement.md` → Phase 4 at `218`,
  `### PHASE_4_CHECKPOINT` at `286`, Phase 5 at `293`, Phase 6 at `482`; `wc -l` → `499`
  (Task 9 cites these).

- [ ] **Step 5: Commit.**
  `git add commands/dark-factory-implement.md tests/test_implement_doc_impact.py && git commit -m "feat: implement Phase 4 derives doc impact from the diff and writes doc-impact.md (#535)"`

---

## Task 8: `VERIFIER-CONTRACT.md` — the new checker pair

**Files:** Modify `tests/test_verifier_contract_doc_referenced.py`, `refinement-skills/VERIFIER-CONTRACT.md`.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_verifier_contract_doc_referenced.py`:

~~~~python


def test_verifier_contract_covers_docs_conformance_gate():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    assert "| `docs-conformance`'s reviewer (Gate 2.5, #535) | `claude-opus-5-5` | — |" in content
    assert "scripts/docs_verdict_gate_check.sh" in content
    assert "by all five commands" in content
~~~~

- [ ] **Step 2: Run.** `PYTHONPATH=scripts python -m pytest tests/test_verifier_contract_doc_referenced.py -q` → `1 failed`.

- [ ] **Step 3: Edit `refinement-skills/VERIFIER-CONTRACT.md`** — six edits: the intro's
  checker list, a pin-table row, the refusal bullet (single-pass `UNCERTAIN` → BLOCKED; mode
  decides the consequence), the clone-live-first bullet, "four" → "five" commands, and the
  verdict-schema accept-list note. Save outside the repo (e.g. `/tmp/vc_535.py`) and run from
  the repo root:

~~~~python
from pathlib import Path

p = Path("refinement-skills/VERIFIER-CONTRACT.md")
s = p.read_text(encoding="utf-8")
EDITS = [
    ("""and Phase 3.5 conformance reviewer, `conformance`'s reviewer, `code-review`'s
reviewer) and by every""", """and Phase 3.5 conformance reviewer, `conformance`'s reviewer, `code-review`'s
reviewer, `docs-conformance`'s reviewer) and by every"""),
    ("""| `code-review`'s reviewer | `claude-opus-5-5` | — |
""", """| `code-review`'s reviewer | `claude-opus-5-5` | — |
| `docs-conformance`'s reviewer (Gate 2.5, #535) | `claude-opus-5-5` | — |
"""),
    ("""  passes — that is a documented fail-open, not a silent `PASS`. A shadow checker maps to""",
     """  passes — that is a documented fail-open, not a silent `PASS`. `docs-conformance` is
  single-pass: `UNCERTAIN` is written straight to `STATUS: BLOCKED` (no reconcile cycle to
  consume), and it never writes `ERROR`. Whether that BLOCKED stops `push-and-pr` is
  `docs_conformance.mode`'s call (`scripts/docs_verdict_gate_check.sh`): `enforce` blocks,
  `observe` (the default) records it and proceeds. A shadow checker maps to"""),
    ("""- **Clone-live-first resolution (rubric docs only):** `conformance`'s and
  `code-review`'s reviewer rubrics, and `plan`'s Phase 3.5 conformance rubric, read
  the live clone first (e.g. `.claude/skills/conformance/RUBRIC.md`), falling back to""",
     """- **Clone-live-first resolution (rubric docs only):** `conformance`'s,
  `code-review`'s and `docs-conformance`'s reviewer rubrics, and `plan`'s Phase 3.5
  conformance rubric, read the live clone first (e.g.
  `.claude/skills/conformance/RUBRIC.md`; no `docs-conformance` skill is shipped, so that
  one resolves to its baked copy unless a target adds one), falling back to"""),
    ("""`/opt/refinement-skills/VERIFIER-CONTRACT.md`, by all four commands — it is not""",
     """`/opt/refinement-skills/VERIFIER-CONTRACT.md`, by all five commands — it is not"""),
    ("""values (per `scripts/verdict_gate_check.sh`) are `PASS`/`SKIPPED`/`ERROR` (proceed)
and `BLOCKED` (block).""", """values (per `scripts/verdict_gate_check.sh`) are `PASS`/`SKIPPED`/`ERROR` (proceed)
and `BLOCKED` (block). `scripts/docs_verdict_gate_check.sh` (Gate 2.5) is stricter in
`enforce` mode — only `PASS`/`SKIPPED` proceed; `ERROR`, `BLOCKED`, missing and
unparseable all block — and proceeds on everything in `observe` mode."""),
]
for old, new in EDITS:
    assert s.count(old) == 1, f"expected 1 match: {old[:60]!r}"
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print(f"patched {p}: {len(EDITS)} edits")
~~~~
  Expected: `patched refinement-skills/VERIFIER-CONTRACT.md: 6 edits`.

- [ ] **Step 4: Run.** `PYTHONPATH=scripts python -m pytest tests/test_verifier_contract_doc_referenced.py -q` → `9 passed`.

- [ ] **Step 5: Commit.**
  `git add refinement-skills/VERIFIER-CONTRACT.md tests/test_verifier_contract_doc_referenced.py && git commit -m "docs: add Gate 2.5 reviewer to VERIFIER-CONTRACT (#535)"`

---

## Task 9: Reference docs — the gate, its failure/recovery path, config, and shifted citations

**Files:** Create `tests/test_docs_conformance_reference_docs.py`; modify `docs/run-lifecycle.md`,
`docs/features.md`, `docs/configuration.md`, `docs/scheduler.md`,
`docs/dark-factory-token-optimization.md`, `README.md`.

Why the citation edits: these docs cite `path:line`. Task 6 inserts 59 lines after
`workflows/archon-dark-factory.yaml:1140`, so every workflow citation ≥ 1141 moves by +59
(verified line-by-line on a scratch copy). Task 3 inserts 5 lines at `config/config.yaml:53`
and 1 line each after the `code-review` budget/enforce entries, so `config.yaml` citations
≥ 53 move by +5 and those ≥ 156 by +7. Task 2 moves `context_budget.py` citations ≥ 33 by
+1 / ≥ 55 by +2 / ≥ 220 by +3; Task 4 moves `comment_digest.py` citations ≥ 104 by +1; Task 7
moves `dark-factory-implement.md` citations ≥ 219. `docs/scheduler.md` gets one
citation-only fix (`config/config.yaml:115-117` → `:120-122`) — no scheduler content change,
so the spec's "scheduler.md out of scope" (no new status/label) still holds.

- [ ] **Step 1: Write the failing test.** Create `tests/test_docs_conformance_reference_docs.py`:

~~~~python
"""#535 acceptance: the gate and its failure/recovery path are documented in the existing
reference docs. Presence checks only — whether the prose is *true* is Gate 2.5's own job."""
from pathlib import Path

_DOCS = Path(__file__).resolve().parents[1] / "docs"


def _read(name):
    return (_DOCS / name).read_text(encoding="utf-8")


def test_run_lifecycle_documents_nodes_gate_and_recovery():
    text = _read("run-lifecycle.md")
    for token in ("`budget-docs-conformance`", "`enforce-budget-docs-conformance`",
                  "`docs-conformance`", "`docs-conformance-gate`", "**D2.5. Docs conformance, Gate 2.5**",
                  "scripts/docs_verdict_gate_check.sh", "`doc-impact.md`", "`docs-conformance.md`",
                  "df-docs-conformance-observed", "df-docs-conformance-blocked", "**Recovery:**",
                  "| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` |"):
        assert token in text, token


def test_configuration_documents_both_keys_and_budget_scenario():
    text = _read("configuration.md")
    for token in ("### `docs_conformance`", "`docs_conformance.enabled`", "`docs_conformance.mode`",
                  "`token_optimization.budgets.docs-conformance`",
                  "`token_optimization.enforce.docs-conformance`", "99 leaf keys"):
        assert token in text, token


def test_features_lists_the_gate():
    text = _read("features.md")
    assert "**Docs conformance gate (Gate 2.5).**" in text
    assert "`docs_conformance.enabled` (true), `.mode` (`observe`)" in text
~~~~

- [ ] **Step 2: Run.** `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py -q` → `3 failed`.

  **Rebase note (main f457c2c) — apply to the saved script before running it.** main moved
  `workflows/archon-dark-factory.yaml` by +28 lines (a80266f, at lines 482–641) without updating
  these docs, so the docs still carry the older numbers and the script's `old` strings still
  match. Keep every `old` string exactly as written. In every `new` string and in `D25`, add 28
  to each workflow line number ≥ 1145 (for example `:1175` → `:1203`, `:1224-1381` →
  `:1252-1409`, `:1468-1643` → `:1496-1671`). Leave workflow numbers below 1145 alone — that
  drift is pre-existing on main and is not this ticket's to fix. Citations of other files
  (`config.yaml`, the command files, `context_budget.py`, `comment_digest.py`) are already
  correct for main. The path .claude/skills/docs-conformance/RUBRIC.md is written without
  backticks in the doc text on purpose: `scripts/docs_refs_check.py` fails on a backtick path
  that is not in the repo — do not backtick it and do not add it to `NON_REPO_PATHS`.

- [ ] **Step 3: Apply the doc edits.** Save the script below **outside the repo** as
  `/tmp/docs_535.py` (it is a one-off and must not be committed) and run
  `python3 /tmp/docs_535.py .` from the repo root. Every replacement asserts it matches
  exactly once, so a stale anchor fails loudly instead of editing the wrong place; if one
  fails because `main` moved, make that edit by hand at the right spot and re-derive its line
  numbers with `grep -n`.

~~~~python
"""One-off doc patch for #535 (not committed). Every replacement must match exactly once."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")


def patch(rel, pairs):
    p = ROOT / rel
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, f"{rel}: expected 1 match, found {n}: {old[:70]!r}"
        s = s.replace(old, new)
    p.write_text(s, encoding="utf-8")
    print(f"patched {rel}: {len(pairs)} edits")


# ── docs/run-lifecycle.md ────────────────────────────────────────────────────
D25 = """**D2.5. Docs conformance, Gate 2.5** (`commands/dark-factory-docs-conformance.md`, then
the `docs-conformance-gate` node; #535). Runs after `conformance-gate`, so it sees the
final diff — after validate's fixes and Gate 2's excisions and reconcile commits. Single
pass and read-only by instruction: one reviewer, no reconcile loop, no commits.

1. `docs_conformance.enabled` false: write `STATUS: SKIPPED` to `docs-conformance.md` and
   stop (`:72-75`).
2. Build the evidence (`:83-168`): `git diff main...HEAD` with Gate 2's exclusions **except**
   `docs/*.md`, and also excluding `docs/superpowers/**` and `docs/archive/**` (refine/plan
   artifacts); ranked with `diff_rank.py`. An empty diff is `STATUS: PASS` without a
   review, even with no `doc-impact.md` (nothing to document). The baseline
   (merge-base) copy of every changed doc file is written under
   `docs-conformance-baseline/` and listed beside its branch path. Implement's
   `doc-impact.md` is passed as a claim to audit. Because all of `docs/superpowers/` is
   excluded, an edit to a living spec there is not reviewed by this gate.
3. An `opus` reviewer subagent applies the docs-conformance rubric
   (`/opt/refinement-skills/docs-conformance-reviewer-prompt.md`, unless the clone has a
   .claude/skills/docs-conformance/RUBRIC.md, which none ships today): it forms its own view of
   the behavior the diff changes, checks every changed doc claim against the diff, looks
   for undocumented behavior, and only then audits `doc-impact.md`'s `NO_IMPACT` claims
   (`:170-200`). No shadow reviewer.
4. Verdict (`:202-245`): "Conforms" or "Minor deviations" is `PASS`; "Material divergence"
   is `BLOCKED`; no parseable verdict (refusal, timeout, tool error, a missing rubric) is
   `UNCERTAIN`, written as `BLOCKED`. A missing or empty `doc-impact.md`, or one without an
   `OVERALL:` line, forces `BLOCKED`. `docs-conformance.md` starts with the four verdict
   lines, then `VERDICT`, `MODE`, `DOC_IMPACT`, an optional `REASON` and the reviewer output.

| Result | `observe` (default) | `enforce` |
|---|---|---|
| PASS / SKIPPED | nothing posted | nothing posted |
| BLOCKED (`:247-307`) | "Docs Conformance (Gate 2.5) — Mismatch observed (informational)" comment (marker `df-docs-conformance-observed`); no label, no board move | "Docs Conformance (Gate 2.5) — Blocked" comment (marker `df-docs-conformance-blocked`) with the evidence and next steps; board to **Blocked**; `needs-discussion` label |

**The `docs-conformance-gate` node enforces the result** with
`scripts/docs_verdict_gate_check.sh` (`workflows/archon-dark-factory.yaml:1186-1199`), not
`verdict_gate_check.sh`, because `ERROR` must not pass:

| First `STATUS:` line | `observe` | `enforce` |
|---|---|---|
| `PASS`, `SKIPPED` | Exit 0 | Exit 0 |
| `BLOCKED` | Exit 0, logs "enforce would block" | Exit 1, no comment (the command already posted one) |
| `ERROR`, missing or anything else | Exit 0, logs "enforce would block" | Exit 1; posts "Docs Conformance (Gate 2.5) — Blocked: no usable verdict" (marker `df-push-gate-failure`) unless the issue already has `needs-discussion` |

The mode comes from `docs_conformance.mode`; an unreadable config or any value other than
`enforce` is `observe`. On exit 1, `push-and-pr` and everything after it do not run:
nothing is pushed. **Recovery:** fix the docs (or the assessment) and run
`Continue issue #N`, which re-runs implement (Phase 4 reads the Gate 2.5 comment), Gate 2
and Gate 2.5 on the new diff — a docs fix never skips Gate 2. In either mode, if the
`docs-conformance` node itself fails in the executor (for example an idle timeout) rather
than writing a verdict, `docs-conformance-gate` does not run and neither does
`push-and-pr`, as with `conformance` today.

"""

patch("docs/run-lifecycle.md", [
    ("""    VA --> CF["budget + conformance"] --> CG{conformance-gate}
    CG -->|"PASS / SKIPPED / ERROR"| PR[push-and-pr]
    CG -->|otherwise| STOP1(["stop: no push"])
""", """    VA --> CF["budget + conformance"] --> CG{conformance-gate}
    CG -->|"PASS / SKIPPED / ERROR"| DF["budget + docs-conformance"] --> DG{docs-conformance-gate}
    CG -->|otherwise| STOP1(["stop: no push"])
    DG -->|"observe: always<br/>enforce: PASS / SKIPPED"| PR[push-and-pr]
    DG -->|"enforce: otherwise"| STOP3(["stop: no push"])
"""),
    ("""comment_digest.py:41-50,100-146,163-263`""", """comment_digest.py:41-50,100-147,164-264`"""),
    ("""Non-zero exit blocks everything downstream (`:1131-1140`) | `conformance.md` → exit code, possibly a marker comment |
""", """Non-zero exit blocks everything downstream (`:1131-1140`) | `conformance.md` → exit code, possibly a marker comment |
| `budget-docs-conformance` | bash, 30 s | `new` or `continue` | `conformance-gate` | Takes `git diff main...HEAD` after Gate 2, runs `context_budget.py --scenario docs-conformance`; failure ignored (`:1145-1164`) | `doc-impact.md`, diff → `context-budget.docs-conformance.json` |
| `enforce-budget-docs-conformance` | bash, 30 s | `new` or `continue` | `budget-docs-conformance` | `budget_gate.sh docs-conformance`; failure ignored (`:1166-1171`) | → `token-opt-caps.env` |
| `docs-conformance` | command `dark-factory-docs-conformance` (sonnet), idle 10 min | `new` or `continue` | `enforce-budget-docs-conformance`, `conformance-gate` | Gate 2.5: one reviewer checks the docs against the final diff (`:1175-1180`) | `doc-impact.md`, diff, doc files → `docs-conformance.md`, `docs-conformance-baseline/`, `docs_conformance_*` working files, `diff-ranking.json`, possibly a comment; in `enforce` mode also a label and board move |
| `docs-conformance-gate` | bash, 30 s | `new` or `continue` | `docs-conformance` | Reads `docs_conformance.mode`, then `scripts/docs_verdict_gate_check.sh docs-conformance.md <N> "Docs Conformance (Gate 2.5)" <mode>`. `observe` always exits 0; in `enforce` a non-zero exit blocks everything downstream (`:1186-1199`) | `docs-conformance.md`, config → exit code, possibly a marker comment |
"""),
    ("""| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate` | Archives the spec and plan, pushes, creates or updates the draft PR (below) (`:1165-1322`) |""",
     """| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` | Archives the spec and plan, pushes, creates or updates the draft PR (below) (`:1224-1381`) |"""),
    ("""looks up the PR number (`:1143-1162`)""", """looks up the PR number (`:1202-1221`)"""),
    ("""without pushing (`:1182-1186`).""", """without pushing (`:1241-1245`)."""),
    ("""`docs: archive spec/plan for issue #N` (`:1193-1212`).""", """`docs: archive spec/plan for issue #N` (`:1252-1271`)."""),
    ("""   origin afterwards (`:1216-1225`).""", """   origin afterwards (`:1275-1284`)."""),
    ("""   "Blast radius" section in the PR body (`:1228-1237`).""", """   "Blast radius" section in the PR body (`:1287-1296`)."""),
    ("""   for the issue (`:1257-1277`).""", """   for the issue (`:1316-1336`)."""),
    ("""   back (`:1278-1319`).""", """   back (`:1337-1378`)."""),
    ("""failure ignored (`:1325-1347`)""", """failure ignored (`:1384-1406`)"""),
    ("""failure ignored (`:1349-1354`)""", """failure ignored (`:1408-1413`)"""),
    ("""at or above the threshold block (`:1357-1362`)""", """at or above the threshold block (`:1416-1421`)"""),
    ("""the command commits and pushes (`:1365-1370`)""", """the command commits and pushes (`:1424-1429`)"""),
    ("""Non-zero exit blocks `status-in-review` (`:1374-1383`)""", """Non-zero exit blocks `status-in-review` (`:1433-1442`)"""),
    ("""No-op for `resolve` (`:1386-1407`)""", """No-op for `resolve` (`:1445-1466`)"""),
    ("""Posts the run summary comment (`:1409-1584`)""", """Posts the run summary comment (`:1468-1643`)"""),
    ("""`Continue`/`Close` commands (`:1431-1576`).""", """`Continue`/`Close` commands (`:1490-1635`)."""),
    ("""- **Document** (`:218-254`): update the architecture docs that map to the changed paths.
- **Memory** (`:256-443`): see [dark-factory-memory-v2.md](dark-factory-memory-v2.md).
- **Report** (`:445-461`): `implementation.md`.""",
     """- **Document** (`:218-291`): update the docs that map to the changed paths, then scan the
  diff content for behavior changes no mapping row covers (including `commands/**` policy
  Markdown and, where present, `docs/run-lifecycle.md`, `docs/scheduler.md`,
  `docs/features.md`, `docs/configuration.md`) and update those docs too. Always writes
  `doc-impact.md`: one `UPDATED` or `NO_IMPACT: <reason>` entry per changed behavior and an
  `OVERALL:` line, which Gate 2.5 audits. On `continue`, the latest Gate 2.5 comment's
  `[MATERIAL]` items are treated as docs to fix.
- **Memory** (`:293-480`): see [dark-factory-memory-v2.md](dark-factory-memory-v2.md).
- **Report** (`:482-499`): `implementation.md`, including the `doc-impact.md` `OVERALL:` line."""),
    ("""These three implement gates 2 and 3 and the follow-up; they are described with their
gates in [section 6, D2-D4](#d-implementation-gates).
""", """These three implement gates 2 and 3 and the follow-up; they are described with their
gates in [section 6, D2-D4](#d-implementation-gates).

### `dark-factory-docs-conformance`

Gate 2.5, between Gate 2 and `push-and-pr`; described in
[section 6, D2.5](#d-implementation-gates).
"""),
    ("""  `context-budget.<scenario>.json`, with `<scenario>` one of `refine`, `plan`, `implement`,
  `conformance`, `code-review`. Before""", """  `context-budget.<scenario>.json`, with `<scenario>` one of `refine`, `plan`, `implement`,
  `conformance`, `docs-conformance`, `code-review`. Before"""),
    ("""On exit 1, `push-and-pr` and everything after it do not run: nothing is pushed.

**D3. Code review, Gate 3**""", """On exit 1, `push-and-pr` and everything after it do not run: nothing is pushed.

""" + D25 + """**D3. Code review, Gate 3**"""),
    ("""| `context-budget.<scenario>.json` (`refine`, `plan`, `implement`, `conformance`, `code-review`) |""",
     """| `context-budget.<scenario>.json` (`refine`, `plan`, `implement`, `conformance`, `docs-conformance`, `code-review`) |"""),
    ("""| `token-opt-caps.env` | `enforce-budget-*` | `load_memory_context.sh`; conformance and code-review diff ranking |""",
     """| `token-opt-caps.env` | `enforce-budget-*` | `load_memory_context.sh`; conformance, docs-conformance and code-review diff ranking |"""),
    ("""| `implementation.md` | implement command | validate, conformance, `budget-conformance`, `report`, post-mortem |""",
     """| `implementation.md` | implement command | validate, conformance, `budget-conformance`, `report`, post-mortem |
| `doc-impact.md` | implement command (Phase 4) | docs-conformance command (audited as a claim, never trusted); `budget-docs-conformance` |"""),
    ("""| `diff-ranking.json` | `diff_rank.py` (conformance, then code review) |""",
     """| `diff-ranking.json` | `diff_rank.py` (conformance, then docs conformance, then code review) |"""),
    ("""| `conformance.md` | conformance command | `conformance-gate`, `report`, `run-record assemble`, post-mortem |""",
     """| `conformance.md` | conformance command | `conformance-gate`, `report`, `run-record assemble`, post-mortem |
| `docs_conformance_diff.txt`, `docs_conformance_doc_files.md`, `docs_conformance_doc_impact.md`, `docs_conformance_prompt.md`, `docs_conformance_review.md`, `docs-conformance-baseline/` | docs-conformance command | Reviewer subagent prompt and Read targets |
| `docs-conformance.md` | docs-conformance command | `docs-conformance-gate` |"""),
    ("""| Code review | "Code Review — Blocked" (draft PR and its review remain) | Blocked | `needs-discussion` |
| Verdict file missing | "Conformance (Gate 2) — Blocked" or "Code Review (Gate 3) — Blocked": no verdict was recorded | unchanged by the gate | none |""",
     """| Docs conformance (`docs_conformance.mode: enforce` only; `observe` posts an informational "Mismatch observed" comment and blocks nothing) | "Docs Conformance (Gate 2.5) — Blocked" | Blocked | `needs-discussion` |
| Code review | "Code Review — Blocked" (draft PR and its review remain) | Blocked | `needs-discussion` |
| Verdict file missing | "Conformance (Gate 2) — Blocked" or "Code Review (Gate 3) — Blocked": no verdict was recorded; "Docs Conformance (Gate 2.5) — Blocked" (enforce mode only) also for `STATUS: ERROR` | unchanged by the gate | none |"""),
    ("""the only reader is `report` (`workflows/archon-dark-factory.yaml:1471-1489`).""",
     """the only reader is `report` (`workflows/archon-dark-factory.yaml:1530-1548`)."""),
    ("""   When `conformance-gate` or `review-gate` exits 1 it is unknown""",
     """   When `conformance-gate`, `docs-conformance-gate` or `review-gate` exits 1 it is unknown"""),
    ("""   `scripts/comment_digest.py:123-134` records""", """   `scripts/comment_digest.py:124-135` records"""),
    ("""(`workflows/archon-dark-factory.yaml:1500-1502`)""", """(`workflows/archon-dark-factory.yaml:1559-1561`)"""),
    ("""    (`:916`, `:1181`) but no code""", """    (`:916`, `:1240`) but no code"""),
    ("""      (`workflows/archon-dark-factory.yaml:1501`).""", """      (`workflows/archon-dark-factory.yaml:1560`)."""),
    ("""`config/config.yaml:59` documents""", """`config/config.yaml:64` documents"""),
])

# ── docs/features.md ─────────────────────────────────────────────────────────
patch("docs/features.md", [
    ("""| **Implementation.** The implement agent (Opus) works the plan test-first, self-reviews, updates docs and memory, and commits. |""",
     """| **Implementation.** The implement agent (Opus) works the plan test-first, self-reviews, updates docs (mapped paths plus a diff-content behavior scan) and memory, writes `doc-impact.md` for Gate 2.5, and commits. |"""),
    ("""Node `push-and-pr` (`workflow:1165`): `codehost close-keyword` (`:1253`), `open-change` (`:1310`), `update-body` (`:1269`).""",
     """Node `push-and-pr` (`workflow:1224`): `codehost close-keyword` (`:1312`), `open-change` (`:1369`), `update-body` (`:1328`)."""),
    ("""Nodes `status-in-review` (`workflow:1386`), `report` (`:1409`).""", """Nodes `status-in-review` (`workflow:1445`), `report` (`:1468`)."""),
    ("""Nodes `code-review` (`workflow:1357`) and `review-gate` (`:1374`, `verdict_gate_check.sh` at `:1377`).""",
     """Nodes `code-review` (`workflow:1416`) and `review-gate` (`:1433`, `verdict_gate_check.sh` at `:1436`)."""),
    ("""Node `revise-advisory` (`workflow:1365`).""", """Node `revise-advisory` (`workflow:1424`)."""),
    ("""| `conformance.enabled` (true), `.max_reconcile_cycles` (3), `.block_on_material` (true) | [`run-lifecycle.md`](run-lifecycle.md) | none |
""", """| `conformance.enabled` (true), `.max_reconcile_cycles` (3), `.block_on_material` (true) | [`run-lifecycle.md`](run-lifecycle.md) | none |
| **Docs conformance gate (Gate 2.5).** After Gate 2, a reviewer subagent (Opus) checks that the branch's docs are true of the final diff, that no affected behavior is undocumented, and that implement's `doc-impact.md` no-impact claims hold. Single pass, read-only by instruction. In `observe` mode a mismatch is recorded and posted as an informational comment; in `enforce` mode a mismatch, a missing or unparseable verdict, or `ERROR` blocks the PR. | **Measured only** (ships in `observe` mode). Nodes `docs-conformance` (`workflow:1175`) and `docs-conformance-gate` (`:1186`, `docs_verdict_gate_check.sh` at `:1192`). Tests: `tests/test_docs_verdict_gate_check.py`, `tests/test_docs_conformance_dag.py`, `tests/test_docs_conformance_command.py`. | `commands/dark-factory-docs-conformance.md`; rubric `refinement-skills/docs-conformance-reviewer-prompt.md` (a clone-live .claude/skills/docs-conformance/RUBRIC.md wins if a target adds one, `:174`-`:178`); `scripts/docs_verdict_gate_check.sh` | `docs_conformance.enabled` (true), `.mode` (`observe`) | [`run-lifecycle.md`](run-lifecycle.md) | [#536](https://github.com/omniscient/dark-factory/issues/536): enforce-mode flip, breaker accounting, tool restriction |
"""),
    ("""used at `dark-factory-conformance.md:546` and `:694`. Test: `tests/test_verdict_gate_check.sh` (CI). | `scripts/factory_core/verdict.py` `parse_verdict` (`:20`); `scripts/verdict_gate_check.sh` |""",
     """used at `dark-factory-conformance.md:546` and `:694` and `dark-factory-docs-conformance.md:236`. Tests: `tests/test_verdict_gate_check.sh` (CI), `tests/test_docs_verdict_gate_check.py`. | `scripts/factory_core/verdict.py` `parse_verdict` (`:20`); `scripts/verdict_gate_check.sh`; `scripts/docs_verdict_gate_check.sh` (Gate 2.5: `ERROR` blocks in `enforce` mode) |"""),
    ("""`budget-conformance` (`:1082`), `budget-code-review` (`:1325`).""", """`budget-conformance` (`:1082`), `budget-docs-conformance` (`:1145`), `budget-code-review` (`:1384`)."""),
    ("""Each writes its own `context-budget.<scenario>.json` (`refine`, `plan`, `implement`, `conformance`, `code-review`; `continue` runs use `implement`).""",
     """Each writes its own `context-budget.<scenario>.json` (`refine`, `plan`, `implement`, `conformance`, `docs-conformance`, `code-review`; `continue` runs use `implement`)."""),
    ("""| **Live** for refine, plan, conformance and code-review; observe-only for implement. `budget_gate.sh` runs in the five `enforce-budget-*` nodes (`workflow:402`, `:442`, `:716`, `:1115`, `:1351`)""",
     """| **Live** for refine, plan, conformance and code-review; observe-only for implement and docs-conformance. `budget_gate.sh` runs in the six `enforce-budget-*` nodes (`workflow:402`, `:442`, `:716`, `:1115`, `:1168`, `:1410`)"""),
    ("""`dark-factory-conformance.md:201`, `dark-factory-code-review.md:110`. Test: `tests/test_budget_gate.sh` (CI).""",
     """`dark-factory-conformance.md:201`, `dark-factory-docs-conformance.md:97`, `dark-factory-code-review.md:110`. Test: `tests/test_budget_gate.sh` (CI)."""),
    ("""| `token_optimization.enforce_budgets` (true) and `token_optimization.enforce.<scenario>` (implement false, others true);""",
     """| `token_optimization.enforce_budgets` (true) and `token_optimization.enforce.<scenario>` (implement and docs-conformance false, others true);"""),
    ("""| **Diff ranking.** The diff given to the conformance and code-review reviewers is ranked by risk tier and truncated to a token cap, with safety-critical paths first. | **Live.** `dark-factory-code-review.md:116` and `dark-factory-conformance.md:203`;""",
     """| **Diff ranking.** The diff given to the conformance, docs-conformance and code-review reviewers is ranked by risk tier and truncated to a token cap, with safety-critical paths first. | **Live.** `dark-factory-code-review.md:116`, `dark-factory-conformance.md:203` and `dark-factory-docs-conformance.md:116`;"""),
    ("""(`context_budget.py:240`) and never injected into a prompt; the code says so at `context_budget.py:45`-`:47`.""",
     """(`context_budget.py:243`) and never injected into a prompt; the code says so at `context_budget.py:46`-`:48`."""),
    ("""`dark-factory-code-review.md:181`, `dark-factory-refine.md:132`.""", """`dark-factory-code-review.md:181`, `dark-factory-refine.md:132`, `dark-factory-docs-conformance.md:182`."""),
    ("""`workflow:185`, `:197`, `:251`, `:264`, `:900`, `:1253`, `:1310`, `:1546`)""", """`workflow:185`, `:197`, `:251`, `:264`, `:900`, `:1312`, `:1369`, `:1605`)"""),
    ("""the `gh pr view` reads in the workflow (`:114`, `:1262`)""", """the `gh pr view` reads in the workflow (`:114`, `:1321`)"""),
    ("""| **Claude skills: review rubrics.** The conformance and code-review rubrics are skills in the target repo, so a target can tune them; the gate commands read them from the clone and fall back to the baked prompt. |""",
     """| **Claude skills: review rubrics.** The conformance and code-review rubrics are skills in the target repo, so a target can tune them; the gate commands read them from the clone and fall back to the baked prompt. The docs-conformance command (Gate 2.5) does the same lookup for .claude/skills/docs-conformance/RUBRIC.md (`dark-factory-docs-conformance.md:174`), but no such skill is shipped: it uses the baked `refinement-skills/docs-conformance-reviewer-prompt.md`. |"""),
    ("""(`config.yaml:170`""", """(`config.yaml:177`"""),
    ("""appears in `config.yaml:179`""", """appears in `config.yaml:186`"""),
])

# ── docs/configuration.md ────────────────────────────────────────────────────
patch("docs/configuration.md", [
    ("""`workflows/archon-dark-factory.yaml:1463` (report text only)""", """`workflows/archon-dark-factory.yaml:1522` (report text only)"""),
    ("""`workflows/archon-dark-factory.yaml:1504` (report text)""", """`workflows/archon-dark-factory.yaml:1563` (report text)"""),
    ("""| `code_review.severity_order` | `low, medium, high, critical` | ascending severity ranking | `commands/dark-factory-code-review.md:60-64` (`yq`), passed as `--severity-order` to `scripts/code_review_payload.py:200` | none | no |
""", """| `code_review.severity_order` | `low, medium, high, critical` | ascending severity ranking | `commands/dark-factory-code-review.md:60-64` (`yq`), passed as `--severity-order` to `scripts/code_review_payload.py:200` | none | no |

### `docs_conformance`

Gate 2.5 (#535). Read from the run's config (`.claude/skills/refinement/config.yaml` in the
clone) by the `docs-conformance` command and, for `mode`, also by the
`docs-conformance-gate` node. An unreadable config, a missing block, or any `mode` other
than `enforce` means `enabled: true` and `observe`.

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `docs_conformance.enabled` | `true` | false = the command writes `STATUS: SKIPPED`, which passes in both modes | `commands/dark-factory-docs-conformance.md:68,72-75` (agent-read) | none | no |
| `docs_conformance.mode` | `observe` | `observe`: record the verdict and post an informational comment on a mismatch, never block. `enforce`: a BLOCKED, `ERROR`, missing or unparseable verdict blocks `push-and-pr`, with the blocking comment, a Blocked board move and `needs-discussion`. Turning `enforce` on is a separate, reviewed change ([#536](https://github.com/omniscient/dark-factory/issues/536)) | `commands/dark-factory-docs-conformance.md:68,213` (agent-read); `workflows/archon-dark-factory.yaml:1189-1190` (`docs-conformance-gate`), passed to `scripts/docs_verdict_gate_check.sh` | none | no |
"""),
    ("""`commands/dark-factory-code-review.md:110`). Scenarios are `refine`, `plan`, `implement`,
`conformance`, `code-review` (`workflows/archon-dark-factory.yaml:402,442,716,1115,1351`).""",
     """`commands/dark-factory-docs-conformance.md:97`, `commands/dark-factory-code-review.md:110`).
Scenarios are `refine`, `plan`, `implement`, `conformance`, `docs-conformance`, `code-review`
(`workflows/archon-dark-factory.yaml:402,442,716,1115,1168,1410`)."""),
    ("""| `token_optimization.budgets.code-review` | `22000` | same | `scripts/budget_gate.sh:46` | none |
""", """| `token_optimization.budgets.code-review` | `22000` | same | `scripts/budget_gate.sh:46` | none |
| `token_optimization.budgets.docs-conformance` | `22000` | same | `scripts/budget_gate.sh:46` | none |
"""),
    ("""| `token_optimization.enforce.implement` | `false` | same | `scripts/budget_gate.sh:45` | none |
""", """| `token_optimization.enforce.implement` | `false` | same | `scripts/budget_gate.sh:45` | none |
| `token_optimization.enforce.docs-conformance` | `false` | same | `scripts/budget_gate.sh:45` | none |
"""),
    ("""(`Dockerfile:152`). 95 leaf keys,""", """(`Dockerfile:152`). 99 leaf keys,"""),
    ("""Counting: `scheduler` 10, `refine` 3, `plan` 1, `direct_to_pr` 3, `conformance` 7, `code_review` 5,
`preview` 2, `conflict_resolution` 2, `blast_radius` 4, `dispatch_ceiling` 3, `epic_autopilot` 11,
`main_red_autofix` 5, `side_effect.phase_levels` 9, `token_optimization` 30 (`enforce_budgets`,
`default_budget_tokens`, 5 budgets, 5 enforce, `issue_context` 1, `architecture` 4, `memory` 5,
`comments` 4, `diff` 3, `escalation` 1): 95 leaf keys.""",
     """Counting: `scheduler` 10, `refine` 3, `plan` 1, `direct_to_pr` 3, `conformance` 7, `code_review` 5,
`docs_conformance` 2, `preview` 2, `conflict_resolution` 2, `blast_radius` 4, `dispatch_ceiling` 3,
`epic_autopilot` 11, `main_red_autofix` 5, `side_effect.phase_levels` 9, `token_optimization` 32
(`enforce_budgets`, `default_budget_tokens`, 6 budgets, 6 enforce, `issue_context` 1,
`architecture` 4, `memory` 5, `comments` 4, `diff` 3, `escalation` 1): 99 leaf keys."""),
    ("""`workflows/archon-dark-factory.yaml:803,917,1182`""", """`workflows/archon-dark-factory.yaml:803,917,1241`"""),
    ("""`config/config.yaml:59`""", """`config/config.yaml:64`"""),
    ("""`config/config.yaml:119,120,122`""", """`config/config.yaml:124,125,127`"""),
    ("""`config/config.yaml:170`""", """`config/config.yaml:177`"""),
    ("""`config/config.yaml:55`""", """`config/config.yaml:60`"""),
    ("""`config/config.yaml:90-93`""", """`config/config.yaml:95-98`"""),
    ("""`config/config.yaml:161,172`""", """`config/config.yaml:168,179`"""),
    ("""`config/config.yaml:166`""", """`config/config.yaml:173`"""),
])

# ── citation-only / one-phrase accuracy fixes forced by this change ──────────
patch("docs/scheduler.md", [
    ("""`config/config.yaml:115-117`""", """`config/config.yaml:120-122`"""),
])
patch("docs/dark-factory-token-optimization.md", [
    ("""| Active in conformance and code-review |""", """| Active in conformance, docs-conformance and code-review |"""),
    ("""- **Diff cap** (`diff.*`): conformance and code-review receive the ranked, truncated diff.""",
     """- **Diff cap** (`diff.*`): conformance, docs-conformance and code-review receive the ranked, truncated diff."""),
])
patch("README.md", [
    ("""to rank/prioritize a diff for the code-review and conformance reviewers""",
     """to rank/prioritize a diff for the code-review, conformance and docs-conformance reviewers"""),
])
~~~~

  Expected output:
  ```
  patched docs/run-lifecycle.md: 36 edits
  patched docs/features.md: 20 edits
  patched docs/configuration.md: 16 edits
  patched docs/scheduler.md: 1 edits
  patched docs/dark-factory-token-optimization.md: 2 edits
  patched README.md: 1 edits
  ```

- [ ] **Step 4: Verify the new citations point where they claim** (all must print the named line):

~~~~bash
W=workflows/archon-dark-factory.yaml; C=commands/dark-factory-docs-conformance.md
sed -n '1173p;1194p;1196p;1203p;1214p;1217p;1220p;1252p;1444p;1461p;1464p;1473p;1496p' $W
sed -n '68p;72p;97p;116p;174p;182p;213p;236p;247p' $C
sed -n '218p;293p;482p' commands/dark-factory-implement.md
sed -n '64p;120p;177p;186p' config/config.yaml
sed -n '46p;243p' scripts/context_budget.py; sed -n '124p;135p' scripts/comment_digest.py
~~~~
  Expected, in order: the `- id:` lines of `budget-docs-conformance`,
  `enforce-budget-docs-conformance`, its `budget_gate.sh … docs-conformance || true` line,
  `docs-conformance`, `docs-conformance-gate`, its `DOCS_MODE=$(python3 …` line, its
  `bash "$_CLONE/dark-factory/scripts/docs_verdict_gate_check.sh" \` line, `push-and-pr`,
  `code-review`, `review-gate`, its `verdict_gate_check.sh` line, `status-in-review`, `report`;
  then the command's `read -r DOCS_ENABLED…`, `1. If \`DOCS_ENABLED\`…`, the
  `token-opt-caps.env` source, `python3 dark-factory/scripts/diff_rank.py \`, the
  `.claude/skills/docs-conformance/RUBRIC.md` test, `… render-prompt \`, `read -r _EN DOCS_MODE…`,
  `emit_verdict "docs-conformance" …`, `## Phase 6: …`; then implement's `## Phase 4: DOCUMENT`,
  `## Phase 5: MEMORY UPDATE`, `## Phase 6: REPORT`; then the same `config.yaml` lines the
  docs described before (`ai_tier`, the side-effect comment, `comments.enabled`,
  `escalation.opus_only_for`); then `# NOT delivered: architecture_md…`,
  `result = aslice.slice_architecture(`, and the two `comment_digest.py` lines previously at
  123/134. Then:
  `PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py -q` → `3 passed`.
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  → `Docs reference check passed for 4 doc(s).` (CI runs this; it checks backtick paths, line
  ranges, the Layer 1 config keys and the node ids in `docs/features.md`).

- [ ] **Step 5: Commit.**
  `git add docs/run-lifecycle.md docs/features.md docs/configuration.md docs/scheduler.md docs/dark-factory-token-optimization.md README.md tests/test_docs_conformance_reference_docs.py && git commit -m "docs: document Gate 2.5 docs-conformance and its recovery path (#535)"`

---

## Task 10: Full verification and this ticket's own `doc-impact.md`

- [ ] **Step 1: Full suite.** `PYTHONPATH=scripts python -m pytest tests/ -q` → 0 failed
  (about 2640 tests on the scratch copy).
- [ ] **Step 2: CI parity.** `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml`
  and `python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml` pass;
  `bash tests/test_verdict_gate_check.sh` and `bash tests/test_budget_gate.sh` print `PASS`;
  `bash tests/test_gate_diff_md_visibility.sh` passes (Gate 2/3 diffs are untouched). Run the
  rest of the `tests/test_*.sh` scripts listed in `.github/workflows/ci.yml`; if one fails,
  run it on an unmodified `main` checkout in the same container before treating it as a
  regression (in the plan's scratch environment `test_identity.sh` and
  `test_provider_env_flags.sh` fail on `main` too, and three `test_entrypoint_*.sh` fail
  whenever the checkout lives under `/tmp`).
- [ ] **Step 3: Scope check.** `git diff --name-only main...HEAD` lists only the files in the
  File Structure table plus this ticket's spec/plan. In particular: nothing under
  `.claude/`, `deploy/`, `.github/`, `scripts/factory_core/side_effect.py`,
  `scripts/verdict_gate_check.sh`, `commands/dark-factory-conformance.md` or
  `commands/dark-factory-code-review.md` (Gate 2/3 and the side-effect profile are unchanged).
- [ ] **Step 4: Dogfood Phase 4.** This run's implement Phase 4 must itself write
  `$ARTIFACTS_DIR/doc-impact.md` in the Task 7 grammar — e.g.
  `` - `workflows/archon-dark-factory.yaml` -> UPDATED `docs/run-lifecycle.md#4.7 Conformance` — four Gate 2.5 nodes ``,
  one entry per changed behavior from Tasks 1–8, and `OVERALL: UPDATED`. (The image that runs
  this ticket predates the gate, so nothing reads it yet; writing it proves the instructions
  are followable.) In `implementation.md`'s decisions bullet, say that the
  `docs/scheduler.md`, `README.md` and `docs/dark-factory-token-optimization.md` edits are
  citation-only / one-phrase accuracy fixes forced by this change, so Gate 2 and the human
  reviewer do not read them as drift.

---

## Self-review

- [x] `**Issue:** #535` line present directly under the title.
- [x] No placeholders: every new file is given in full; every edit names its exact old and
  new text; every command has its expected output, taken from a scratch run.
- [x] Spec coverage: own command + nodes (R1, Tasks 5/6); single pass, Opus, no reconcile,
  UNCERTAIN→BLOCKED (R2, Task 5); new strict gate script, mode parameter, blank/unknown →
  observe (R3/R13, Task 1); `doc-impact.md` audited not trusted, missing → BLOCKED (R4,
  Tasks 5/7); diff without `':!docs/*.md'` (R5, Task 5); target + Dark Factory docs + policy
  Markdown (R6, Tasks 5/7); no OOS doc ticketing (R7 — the rubric forbids it, Gate 2 untouched);
  "Gate 2.5" label (R8); `new`/`continue` only (R9); both gates rerun on `continue`, no
  memoization (R10 — plain DAG nodes); five stubbed scenarios + fail-closed cases, no PR on
  block (R11/R14, Tasks 1/6); docs (R12, Tasks 8/9); observe default, enforce in #536 (R13,
  Task 3); `_SECTION_REGISTRY` entry (R15, Task 2); `denied_tools: []`, no tool-list change
  (R16, Task 6).
- [x] Deviations are listed under "Decisions" (1–4) with reasons.


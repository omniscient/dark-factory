# Implementation Plan: Headless execution contract inlined into every phase command file

**Issue:** #431
**Spec:** `docs/superpowers/specs/2026-09-26-headless-contract-command-files-design.md` (spec gate: APPROVE-WITH-AMENDMENTS, `f7e3f41`)
**Depends on:** nothing — every task builds against `main` as it exists today.

---

## Goal

Make the headless-agent discipline (never end a turn on a question; persist the artifact
before the turn ends; turn end = process end, no `ScheduleWakeup`; poll in-turn for
subagents) structurally present in all eight `commands/*.md` files, so a target whose own
`CLAUDE.md` lacks it (MarketHawk #388/#441) or a command that never reads `CLAUDE.md`
(conformance, code-review, revise-advisory, ceiling-revisit) is protected by default. A new
pytest module holds the one canonical copy of the block and fails CI on any omission,
duplication or drift.

## Architecture

- **One source of truth:** `HEADLESS_CONTRACT_BLOCK` in
  `tests/test_command_headless_contract.py` — the full text from
  `<!-- headless-contract:begin -->` through `<!-- headless-contract:end -->`, byte-for-byte
  the block in the spec's "Block format" section. No baked file, no runtime dependency.
- **Checker:** `_contract_errors(text)` — exactly one begin marker, exactly one end marker,
  begin before end, and the marked region (markers included) `==` the constant.
  `_failures(command_dir)` applies it to every `*.md` in a directory. The live test runs it
  over `Path(__file__).resolve().parents[1] / "commands"` (the `__file__`-anchored form of
  `tests/test_command_issue_context_contract.py:4`, not the cwd-relative
  `Path("commands")` of `tests/test_command_footer_migration.py:3`), after asserting the
  glob found all 8 expected names, so it can never pass vacuously. Placement is deliberately
  not asserted.
- **Insertion:** a one-shot `python3 -` heredoc (no file committed) that imports the
  constant from the test module — so the text written into the command files is the very
  string the test compares against — and inserts it at the spec's per-file anchors:
  - 6 files with `## Invocation Contract` (code-review, conformance, implement, plan,
    refine, validate): directly after the `---` rule that closes that section, followed by
    a new `---` rule, so the block is its own `---`-delimited section before the next
    heading (`## Phase 1: LOAD`, `## SCOPE BOUNDARY`, `## CRITICAL: …`, `## Phase 0: …`).
  - `ceiling-revisit.md`: after the "Env-driven, generic capability" blockquote, before
    `## Purpose`.
  - `dark-factory-revise-advisory.md`: after `**Workflow ID**: $WORKFLOW_ID`, before the
    `---` rule ahead of `## Phase 1: LOAD`.
- **Implement reminder:** `commands/dark-factory-implement.md`'s "Report discipline"
  sentence stops citing `CLAUDE.md` and points at the Headless Execution Contract above.
- **Prompt cost per phase:** the block is 17 lines / 1,034 bytes (~260 tokens), once per
  command file, plus 2 lines (a `---` rule) in the 6 Invocation-Contract files. Against
  the per-scenario budgets in `config/config.yaml` (refine/plan/implement 30000,
  conformance/code-review 22000) that is ~0.9-1.2%. It is not charged against the enforced
  budget at all — `scripts/budget_enforce.py` reserves only
  `claude_md + issue_context + architecture` and `scripts/context_budget.py` has no
  command-file section — so it consumes unmeasured headroom. Negligible at this size;
  record it (Task 6.3) so the pattern is not scaled up blindly.
- **Out of scope (spec):** `CLAUDE.md` (unchanged), `entrypoint.sh`, `workflows/`,
  `scripts/`, `config/`, `gate_*`, `.factory/adapter.yaml`, `deploy/**`,
  `tests/test_command_footer_migration.py`'s glob gap, run-record scoring (follow-up A),
  `idle_timeout` (follow-up B). Follow-ups A and B are *filed* as issues in Task 6
  (spec acceptance criterion 6), not implemented.

Pre-verified during planning on a scratch copy of this branch: the new test is red against
today's `commands/` (3 failed, 6 passed), green after the insertion script (9 passed), and
the full suite stays green with all edits applied (`2229 passed`), as do both DAG checks and
`tests/test_smoke_gate.sh` (27 passed). No existing test pins the reworded implement
sentence, and no existing test's `text.find("## Phase …")` anchor is disturbed.

## Tech Stack

Markdown command files; Python 3 + pytest (`python -m pytest tests/ -v`, as CI runs it).

## File Structure

| File | Change | Task |
|---|---|---|
| `tests/test_command_headless_contract.py` | **New** — canonical block constant, checker, live + negative tests | 1 |
| `commands/ceiling-revisit.md` | Insert block before `## Purpose` | 2 |
| `commands/dark-factory-code-review.md` | Insert block after `## Invocation Contract` section | 2 |
| `commands/dark-factory-conformance.md` | Insert block after `## Invocation Contract` section | 2 |
| `commands/dark-factory-implement.md` | Insert block after `## Invocation Contract` section; reword "Report discipline" reminder | 2, 3 |
| `commands/dark-factory-plan.md` | Insert block after `## Invocation Contract` section | 2 |
| `commands/dark-factory-refine.md` | Insert block after `## Invocation Contract` section | 2 |
| `commands/dark-factory-revise-advisory.md` | Insert block after `**Workflow ID**` line | 2 |
| `commands/dark-factory-validate.md` | Insert block after `## Invocation Contract` section | 2 |

Nothing else in the repo changes (spec acceptance criterion 5; checked in Task 5).
Task 6 creates two GitHub issues (added to the project board) and writes `$ARTIFACTS_DIR` files — no repo diff.

---

## Task 1: Canonical block + failing test

**Files:** `tests/test_command_headless_contract.py` (new)

### TDD Steps

- [ ] **Step 1.1 — write the test file.** Create `tests/test_command_headless_contract.py`
  with exactly this content (the block text between the triple quotes is the spec's "Block
  format" block verbatim — do not re-wrap it; the em dashes are U+2014):

````python
"""Every phase command file carries the same Headless Execution Contract block (#431).

Phase agents run headless: an ended turn ends the process. A target repo's own
CLAUDE.md may not carry those rules, and several commands never read CLAUDE.md at
all, so each commands/*.md carries them inline. HEADLESS_CONTRACT_BLOCK below is the
single source of truth for the block's text; a missing, duplicated or drifted copy
fails CI.
"""
import shutil
from pathlib import Path

COMMAND_DIR = Path(__file__).resolve().parents[1] / "commands"

EXPECTED_COMMANDS = {
    "ceiling-revisit.md",
    "dark-factory-code-review.md",
    "dark-factory-conformance.md",
    "dark-factory-implement.md",
    "dark-factory-plan.md",
    "dark-factory-refine.md",
    "dark-factory-revise-advisory.md",
    "dark-factory-validate.md",
}

BEGIN = "<!-- headless-contract:begin -->"
END = "<!-- headless-contract:end -->"

HEADLESS_CONTRACT_BLOCK = """\
<!-- headless-contract:begin -->
## Headless Execution Contract

You are running with no human attached; an ended turn ends the process.

- **Never end your turn on a question or an offer.** There is no one to answer. Decide
  per the spec/plan, act, and record any reservations in the issue comment or commit
  message instead.
- **Persist this phase's artifact before your final turn ends.** Commit (and push, where
  this command says to) any repo file this phase owns; write or post any comment, label or
  `$ARTIFACTS_DIR` artifact it owns. Work left unpersisted when the turn ends is destroyed.
- **Turn end = process end.** Scheduled wakeups do not fire (do not use `ScheduleWakeup`),
  task-notifications never arrive, and pending subagent work is destroyed — and an ended
  turn is reported as success whether or not this phase's artifact exists.
- **To wait on a background subagent, poll inside your turn** (keep issuing tool calls)
  or do the work inline — never end the turn to "wait."
<!-- headless-contract:end -->"""


def _contract_errors(text):
    """Return why `text` fails the contract; an empty list means it carries the exact block once."""
    begins, ends = text.count(BEGIN), text.count(END)
    if begins != 1 or ends != 1:
        return [f"expected exactly one marker pair, found {begins} begin / {ends} end"]
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    if stop <= start:
        return ["end marker precedes begin marker"]
    if text[start:stop] != HEADLESS_CONTRACT_BLOCK:
        return ["marked block differs from HEADLESS_CONTRACT_BLOCK"]
    return []


def _failures(command_dir):
    """Map each commands/*.md file under `command_dir` that fails the contract to its errors."""
    failures = {}
    for path in sorted(command_dir.glob("*.md")):
        errors = _contract_errors(path.read_text(encoding="utf-8"))
        if errors:
            failures[path.name] = errors
    return failures


def test_glob_finds_every_command_file():
    found = {path.name for path in COMMAND_DIR.glob("*.md")}
    missing = EXPECTED_COMMANDS - found
    assert not missing, f"{COMMAND_DIR} glob is missing command files: {sorted(missing)}"


def test_every_command_file_carries_the_exact_headless_contract():
    failures = _failures(COMMAND_DIR)
    assert not failures, (
        "every commands/*.md must carry HEADLESS_CONTRACT_BLOCK exactly once, byte-for-byte "
        f"(#431): {failures}"
    )


def test_block_states_the_four_generic_rules_only():
    for rule in (
        "Never end your turn on a question or an offer",
        "Persist this phase's artifact before your final turn ends",
        "Turn end = process end",
        "ScheduleWakeup",
        "poll inside your turn",
    ):
        assert rule in HEADLESS_CONTRACT_BLOCK, rule
    # CLAUDE.md-specific rules stay out of the generic block.
    assert "Hermes" not in HEADLESS_CONTRACT_BLOCK
    assert "sanctioned" not in HEADLESS_CONTRACT_BLOCK


def test_checker_accepts_the_exact_block():
    assert _contract_errors(f"# Title\n\n{HEADLESS_CONTRACT_BLOCK}\n\n## Next\n") == []


def test_checker_rejects_a_missing_block():
    assert _contract_errors("# Title\n\n## Phase 1: LOAD\n")


def test_checker_rejects_a_single_character_drift():
    drifted = HEADLESS_CONTRACT_BLOCK.replace("process end.", "process end!", 1)
    assert drifted != HEADLESS_CONTRACT_BLOCK
    assert _contract_errors(f"# Title\n\n{drifted}\n")


def test_checker_rejects_a_duplicated_block():
    assert _contract_errors(f"{HEADLESS_CONTRACT_BLOCK}\n\n{HEADLESS_CONTRACT_BLOCK}\n")


def test_real_command_dir_fails_when_a_block_is_deleted(tmp_path):
    copy = tmp_path / "commands"
    shutil.copytree(COMMAND_DIR, copy)
    target = copy / "dark-factory-conformance.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(HEADLESS_CONTRACT_BLOCK, "", 1),
        encoding="utf-8",
    )
    assert set(_failures(copy)) == {"dark-factory-conformance.md"}


def test_real_command_dir_fails_when_a_ninth_file_has_no_block(tmp_path):
    copy = tmp_path / "commands"
    shutil.copytree(COMMAND_DIR, copy)
    (copy / "new-phase.md").write_text("# New Phase\n\n## Phase 1: LOAD\n", encoding="utf-8")
    assert set(_failures(copy)) == {"new-phase.md"}
````

- [ ] **Step 1.2 — verify it fails for the right reason.**

```bash
python -m pytest tests/test_command_headless_contract.py -v
```

Expected: `3 failed, 6 passed`. The failures are
`test_every_command_file_carries_the_exact_headless_contract` (all 8 files listed with
`expected exactly one marker pair, found 0 begin / 0 end`),
`test_real_command_dir_fails_when_a_block_is_deleted` and
`test_real_command_dir_fails_when_a_ninth_file_has_no_block` (both see every real file
failing, not just the one they perturbed). `test_glob_finds_every_command_file` and the
five pure-checker tests pass. Any `ImportError`/`SyntaxError` means the file was mistyped —
fix before continuing. Do not commit yet (Task 2 commits test + blocks together so no
commit on the branch is red).

---

## Task 2: Insert the block into all 8 command files

**Files:** all 8 `commands/*.md` listed in the File Structure table

### Steps

- [ ] **Step 2.1 — run the one-shot insertion script** from the repo root. It imports the
  block from the Task 1 test module, so the inserted text cannot drift from the constant;
  every anchor is asserted, and it refuses to insert twice. Nothing from this step is saved
  as a file.

```bash
python3 - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, "tests")
from test_command_headless_contract import HEADLESS_CONTRACT_BLOCK as BLOCK

INVOCATION_CONTRACT_COMMANDS = [
    "dark-factory-code-review.md",
    "dark-factory-conformance.md",
    "dark-factory-implement.md",
    "dark-factory-plan.md",
    "dark-factory-refine.md",
    "dark-factory-validate.md",
]


def rewrite(name, edit):
    path = Path("commands") / name
    text = path.read_text(encoding="utf-8")
    assert "headless-contract:begin" not in text, f"{name} already carries the block"
    path.write_text(edit(text), encoding="utf-8")
    print(f"inserted: {name}")


def after_invocation_contract(text):
    # Insert right after the `---` rule that closes `## Invocation Contract`.
    section = text.index("## Invocation Contract\n")
    rule_end = text.index("\n---\n", section) + len("\n---\n")
    return text[:rule_end] + "\n" + BLOCK + "\n\n---\n" + text[rule_end:]


def before_purpose(text):
    # ceiling-revisit.md: after the "Env-driven" blockquote, before `## Purpose`.
    assert text.count("\n## Purpose\n") == 1
    at = text.index("\n## Purpose\n") + 1
    return text[:at] + BLOCK + "\n\n" + text[at:]


def after_workflow_id(text):
    # dark-factory-revise-advisory.md: after `**Workflow ID**`, before the `---` ahead of Phase 1.
    anchor = "**Workflow ID**: $WORKFLOW_ID\n\n---\n"
    assert text.count(anchor) == 1
    return text.replace(anchor, "**Workflow ID**: $WORKFLOW_ID\n\n" + BLOCK + "\n\n---\n", 1)


for name in INVOCATION_CONTRACT_COMMANDS:
    rewrite(name, after_invocation_contract)
rewrite("ceiling-revisit.md", before_purpose)
rewrite("dark-factory-revise-advisory.md", after_workflow_id)
PY
```

Expected output (8 lines):

```
inserted: dark-factory-code-review.md
inserted: dark-factory-conformance.md
inserted: dark-factory-implement.md
inserted: dark-factory-plan.md
inserted: dark-factory-refine.md
inserted: dark-factory-validate.md
inserted: ceiling-revisit.md
inserted: dark-factory-revise-advisory.md
```

An `AssertionError` or `ValueError` means an anchor moved on `main` since planning — stop,
locate the spec's anchor in that file by hand, and insert
`HEADLESS_CONTRACT_BLOCK` there with the same surrounding blank lines/`---` shape.

- [ ] **Step 2.2 — eyeball the three placement shapes.**

```bash
sed -n 10,44p commands/dark-factory-plan.md
sed -n 6,30p commands/ceiling-revisit.md
sed -n 6,30p commands/dark-factory-revise-advisory.md
```

Expected: in `dark-factory-plan.md`, `## Invocation Contract` … `---`, blank, the block,
blank, `---`, blank, `## SCOPE BOUNDARY`. In `ceiling-revisit.md`, the `> **Env-driven…`
blockquote, blank, the block, blank, `## Purpose`. In `dark-factory-revise-advisory.md`,
`**Workflow ID**: $WORKFLOW_ID`, blank, the block, blank, `---`, blank, `## Phase 1: LOAD`.

- [ ] **Step 2.3 — verify the test passes.**

```bash
python -m pytest tests/test_command_headless_contract.py -v
```

Expected: `9 passed`.

- [ ] **Step 2.4 — commit.**

```bash
git add tests/test_command_headless_contract.py commands/*.md
git commit -m "feat(commands): inline a byte-checked headless execution contract into every phase command (#431)

Every commands/*.md now carries the same <!-- headless-contract:begin/end --> block
(never end a turn on a question; persist the artifact before the turn ends; turn end =
process end, no ScheduleWakeup; poll in-turn for subagents), so a target whose CLAUDE.md
lacks the rules, or a command that never reads CLAUDE.md, is still protected.
tests/test_command_headless_contract.py holds the canonical text and fails on any
omission, duplicate or drift across the full commands/*.md glob."
```

---

## Task 3: Point the implement reminder at the in-file block

**Files:** `commands/dark-factory-implement.md` ("### Report discipline" subsection)

### Steps

- [ ] **Step 3.1 — confirm the old wording is present exactly once (the "failing" state).**

```bash
grep -c "CLAUDE.md\`'s \"never end your turn on a question\" rule" commands/dark-factory-implement.md
```

Expected: `1`.

- [ ] **Step 3.2 — edit.** In `commands/dark-factory-implement.md`, replace the line

```
`CLAUDE.md`'s "never end your turn on a question" rule; this run is headless.
```

with

```
the Headless Execution Contract above ("never end your turn on a question"); this run is headless.
```

(the preceding line, ending `…and no questions per`, is unchanged). Leave the file's other
`CLAUDE.md` mentions (Phase 1's "Read `CLAUDE.md`", doc-exemption lists) alone — only this
reminder moves.

- [ ] **Step 3.3 — verify.**

```bash
grep -c "CLAUDE.md\`'s \"never end your turn on a question\" rule" commands/dark-factory-implement.md
grep -n -B1 "this run is headless" commands/dark-factory-implement.md
python -m pytest tests/test_command_headless_contract.py tests/test_command_issue_context_contract.py -q
```

Expected: `0`; the two-line sentence `…and no questions per` / `the Headless Execution
Contract above ("never end your turn on a question"); this run is headless.`; all tests pass
(the edit sits outside the marked block, so the byte check is unaffected).

- [ ] **Step 3.4 — commit.**

```bash
git add commands/dark-factory-implement.md
git commit -m "docs(implement): point the report-discipline reminder at the in-file headless contract (#431)"
```

---

## Task 4: Hand-check the negative cases (spec acceptance criterion 2)

**Files:** none are changed — every perturbation below is reverted in the same step.

The automated `test_checker_rejects_*` / `test_real_command_dir_fails_*` tests already cover
these, but AC2 requires seeing the *real* test fail on the *real* tree. Run each block as-is.

**Run this task only after Tasks 2.4 and 3.4 have committed.** Each revert below is
`git checkout -- <path>`, which restores the file from the index: with the blocks still
uncommitted it would delete the inserted block instead of undoing the perturbation, and a
headless run has no second chance at that. If you reach this task with `git status` showing
the command files as modified, commit first.

- [ ] **Step 4.1 — (a) block deleted from one command file.**

```bash
python3 - <<'PY'
import re
from pathlib import Path
p = Path("commands/dark-factory-conformance.md")
t = p.read_text(encoding="utf-8")
p.write_text(re.sub(r"<!-- headless-contract:begin -->.*?<!-- headless-contract:end -->\n", "", t, count=1, flags=re.S), encoding="utf-8")
PY
python -m pytest tests/test_command_headless_contract.py -q -k exact_headless_contract; echo "exit=$?"
git checkout -- commands/dark-factory-conformance.md
```

Expected: `1 failed`, the message names `dark-factory-conformance.md` with
`found 0 begin / 0 end`, `exit=1`.

- [ ] **Step 4.2 — (b) one character changed inside a block.**

```bash
sed -i 's/^- \*\*Turn end = process end\.\*\*/- **Turn end = process end!**/' commands/ceiling-revisit.md
python -m pytest tests/test_command_headless_contract.py -q -k exact_headless_contract; echo "exit=$?"
git checkout -- commands/ceiling-revisit.md
```

Expected: `1 failed`, naming `ceiling-revisit.md` with
`marked block differs from HEADLESS_CONTRACT_BLOCK`, `exit=1`.

- [ ] **Step 4.3 — (c) a ninth command file with no block.**

```bash
printf '# Probe\n\n## Phase 1: LOAD\n' > commands/zz-probe.md
python -m pytest tests/test_command_headless_contract.py -q -k exact_headless_contract; echo "exit=$?"
rm commands/zz-probe.md
```

Expected: `1 failed`, naming `zz-probe.md`, `exit=1`.

- [ ] **Step 4.4 — confirm the tree is back to the Task 3 commit.**

```bash
git status --short commands/ tests/
python -m pytest tests/test_command_headless_contract.py -q
```

Expected: no output from `git status`; `9 passed`. Record the three observed failures (one
line each) for the "decisions" bullet in Task 6.

---

## Task 5: Full verification and diff-surface check (acceptance criteria 1, 4, 5)

**Files:** none changed.

- [ ] **Step 5.1 — full suite, as CI runs it.**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: `… passed` with no failures (≈2229 at planning time; the exact count drifts with
`main`).

- [ ] **Step 5.2 — DAG checks and the smoke-gate test (no workflow change is expected to
  affect them).**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
bash tests/test_smoke_gate.sh 2>&1 | tail -1
```

Expected: `DAG trigger_rule check passed for 1 workflow file(s).`,
`when: expression lint passed for 1 workflow file(s).`, `Results: N passed, 0 failed`.

AC4 names `bash smoke_gate.sh`; that script is sourced by `entrypoint.sh` at run time and
runs the target's own build/typecheck, so its CI-parity form is `bash tests/test_smoke_gate.sh`
(`.github/workflows/ci.yml`). Say so in `implementation.md` (Task 6.3) so the conformance
gate does not read the substitution as a deviation.

- [ ] **Step 5.3 — diff surface.** Use the three-dot form (`origin/main...HEAD`,
  merge-base semantics) so commits `main` landed independently after this branch forked
  are not reported as ours; exclude the spec/plan docs the refine phase carried onto the
  branch. Three-dot is this repo's required form for changed-file-*set* detection
  (`.archon/memory/codebase-patterns.md:37`, #266 — two-dot set detection is what made
  `oos_excise.sh` delete `scripts/factory_core/providers/*` on the #251 branch), and it
  is what `scripts/oos_excise.sh:29` and `commands/dark-factory-validate.md:131` already
  use. The two-dot entry at `.archon/memory/codebase-patterns.md:16` (#250) is scoped to
  single-file content-equality checks ("does main already carry this exact content"), not
  to set detection: two-dot against a `main` that advanced during the run lists *main's*
  new paths as differences, which reads as a scope violation that this branch never made.

```bash
git fetch -q origin main
git diff --name-only origin/main...HEAD -- . ':(exclude)docs/superpowers/'
```

Expected — exactly these 9 paths, nothing else:

```
commands/ceiling-revisit.md
commands/dark-factory-code-review.md
commands/dark-factory-conformance.md
commands/dark-factory-implement.md
commands/dark-factory-plan.md
commands/dark-factory-refine.md
commands/dark-factory-revise-advisory.md
commands/dark-factory-validate.md
tests/test_command_headless_contract.py
```

Any other path (in particular `CLAUDE.md`, `entrypoint.sh`, `workflows/`, `scripts/`,
`config/`, `.factory/`, `deploy/`) is a scope violation: revert it with
`git checkout origin/main -- <path>`, or, for a path this branch *added* (it does not
exist on `origin/main`, so the checkout fails), `git rm <path>` — then commit the revert.
Never `git checkout origin/main -- <path>` a path this branch did not touch: that imports
main's content onto the branch instead of reverting anything.

---

## Task 6: File follow-ups A and B, record the rollout caveat (acceptance criterion 6)

**Files:** none in the repo. Writes `$ARTIFACTS_DIR/followup-a.md`,
`$ARTIFACTS_DIR/followup-b.md`, and the Decisions bullet of `$ARTIFACTS_DIR/implementation.md`.

Both issues are filed **without** `ready-for-agent`: each touches breaker/scoring or DAG
timing and needs its own human-reviewed spec (CLAUDE.md hard limit). Filing is idempotent —
an issue with the exact title (any state) is reused, not duplicated.

- [ ] **Step 6.1 — write the two issue bodies.**

```bash
cat > "$ARTIFACTS_DIR/followup-a.md" <<'MD'
Split out of #431 (follow-up A in `docs/superpowers/specs/2026-09-26-headless-contract-command-files-design.md`).

## Problem

`scripts/factory_core/run_record.py::_compute_outcome` scores any `status == "completed"` run with no gate stages as `produced_ungated` / `1.0` — including a phase agent that ended its turn with zero artifact, and a node closed by `dag_node_completed_via_idle_timeout`. MarketHawk #388/#441 burned six plan runs this way before the breaker tripped, and the breaker message could only say "investigate the failure comments above".

## Suggested direction

Before assigning `produced_ungated`, check for the phase's expected artifact (spec/plan file on the branch); if absent, classify the run as `failed` so the breaker can report "agent ended its turn without committing". **Preserve the clean-abort exemption** the DAG already models: "no artifact + `needs-discussion` label" is a legitimate halt, only the label-less case is a silent death (`workflows/archon-dark-factory.yaml` refine-push and plan-push-and-advance). Scoring every artifact-less completion as a failure would make a legitimate needs-discussion halt trip the breaker.

Breaker/scoring-sensitive: needs its own reviewed spec; do not bundle.
MD
cat > "$ARTIFACTS_DIR/followup-b.md" <<'MD'
Split out of #431 (follow-up B in `docs/superpowers/specs/2026-09-26-headless-contract-command-files-design.md`).

## Problem

Archon completes a node **as success** when its SDK stream is silent for `idle_timeout` (`dag_node_completed_via_idle_timeout`). A phase orchestrator legitimately waiting on a long architect/reviewer subagent can be killed this way and look identical to a finished phase; the 17-minute #388 plan attempt on 2026-09-17 (96k output tokens, no plan) fits that shape.

## Suggested direction

Audit `idle_timeout` on every subagent-spawning node in `workflows/archon-dark-factory.yaml`: refine, plan, implement, conformance, code-review and revise-advisory (600000 ms each), and validate (300000 ms). Either raise it for subagent-heavy phases, or confirm (with evidence from a runner log) that subagent progress events reset the idle timer, and document the answer.

DAG-timing change: needs its own reviewed ticket; do not bundle.
MD
```

- [ ] **Step 6.2 — file each issue if not already present.**

```bash
file_followup() {
  local title="$1" body="$2" existing url num
  existing=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state all --limit 200 \
      --search "$title in:title" --json number,title \
    | jq -r --arg t "$title" '.[] | select(.title == $t) | .number' | head -1)
  if [ -n "$existing" ]; then
    echo "reused #$existing: $title"
    return 0
  fi
  # Same create-then-board-add shape as dark-factory-conformance.md's spillover filing.
  url=$(gh issue create --repo "$FACTORY_REPO_SLUG" \
    --title "$title" --body-file "$body" --label "Dark Factory") || return 1
  num=$(basename "$url")
  # TARGET-PATH
  python3 dark-factory/scripts/factory_core/cli.py board-add --issue "$num" --url "$url" \
    || echo "WARNING: board-add failed for follow-up #$num" >&2
  echo "filed #$num: $title"
}
file_followup 'run record: an ended turn with no phase artifact must not score `produced_ungated`' "$ARTIFACTS_DIR/followup-a.md"
file_followup 'workflow: audit `idle_timeout` against subagent-heavy phases' "$ARTIFACTS_DIR/followup-b.md"
```

Expected: two lines, each `filed #N: …` or `reused #N: …` (a board-add WARNING on stderr is
tolerated — the issue itself exists; note it in `implementation.md` so the operator adds it
to the board). If `gh issue create` fails for either title, do **not** stop and do not ask —
record "follow-up A/B NOT filed: <error>" at the top of `implementation.md` (per its "if
anything went sideways" rule) so the operator files it before Done.

- [ ] **Step 6.3 — record decisions in `$ARTIFACTS_DIR/implementation.md`.** In the
  "decisions or trade-offs" bullet, include, in substance:
  - The block lives in all 8 `commands/*.md`, canonical copy in
    `tests/test_command_headless_contract.py`; negatives hand-checked (Task 4 a/b/c
    observed failures).
  - **Not live at merge:** command files reach agents only via the baked image
    (`Dockerfile` copies `commands/` → `/opt/dark-factory/commands`; `entrypoint.sh`
    copies that into the clone's `.archon/commands/` only when the target ships none). No
    running target — MarketHawk included — gets the block until the image is rebuilt and
    published (human-only `publish.yml`). The closing/PR comment must say so.
  - **Residual gap accepted:** a target that ships its own `.archon/commands/` silently
    opts out (neither current target does).
  - Prompt cost: +17 lines / ~260 tokens per phase command (~1% of the 22000-30000
    per-scenario budgets), not charged against `budget_enforce.py`'s reservation.
  - `commands/` is on the factory-owned critical-diff floor
    (`scripts/factory_core/adapter_defaults.py:121`), so `diff_rank.py` ranks all 8 files
    as safety-relevant in review. That is visibility-only: `commands/` is excluded from
    the hard-blocking migration-seed floor (`_VISIBILITY_ONLY`, same file), so
    `gate_blast_radius.py` will not mark the PR HUMAN_REQUIRED for these paths.
  - Follow-up issue numbers for A and B (from Step 6.2), or the failure note.
  - Not done, by design: `CLAUDE.md` unchanged; `tests/test_command_footer_migration.py`
    glob gap left as a spillover candidate.
  - AC4 was checked via `bash tests/test_smoke_gate.sh` (CI-parity form of `smoke_gate.sh`,
    see Step 5.2).

No commit for this task (nothing in the repo changes); pushing the branch and opening the PR
belong to the workflow's `push-and-pr` node after this phase, not to this agent.

# Ceiling-revisit hygiene: fix stale L-bucket text and bake in a permanent XL-bucket duplicate/policy guard

**Issue:** #361

## Goal

Fix two stale-text/missing-guard gaps in the weekly ceiling-revisit pipeline that have each
required the same ad-hoc, never-committed patch for three consecutive cycles (#294, #332, #342):

1. `scripts/ceiling_revisit.py`'s `generate_report()` still emits "L=always-above-ceiling" /
   "`scheduler.sh`" text — stale since XL became the always-above-ceiling bucket
   (commit `4feef16`) and the function moved to `scripts/scheduler_lib.sh`.
2. `commands/ceiling-revisit.md` Phase 4 files an unconditional issue with the same stale text
   and no duplicate/policy guard, ignoring issue #331's operator policy decision that XL stays
   always-above-ceiling by policy.

Full rationale, decision table, and alternatives are in
[the approved spec](../specs/2026-09-04-ceiling-revisit-hygiene-design.md).

## Architecture

Both fixes are surgical edits to existing conditional branches in two files — no new files,
scripts, or config keys:

- `scripts/ceiling_revisit.py`: text-only fix inside `generate_report()`'s
  `l_bucket_needs_issue` branch.
- `commands/ceiling-revisit.md`: text fix to Phase 4's filed title/body, plus replacing Phase
  4's unconditional `gh issue create` with a `state`/`stateReason`-driven duplicate/policy guard
  that queries `gh issue list` once and branches per the spec's decision table.

Both files are covered by static-assertion tests (`tests/test_ceiling_revisit.py` for the
Python report text; a new `tests/test_ceiling_revisit_command.py` for the command-file prose,
matching the `tests/test_command_issue_number_mandate.py` convention — there is no
bash-execution test harness for `commands/*.md` in this repo).

## Tech Stack

Python 3 (stdlib only, `pytest`), Bash (in `commands/*.md` fenced blocks, not directly
executable/testable — verified via string assertions).

## File Structure

| File | Change |
|---|---|
| `scripts/ceiling_revisit.py` | Fix stale "L=" / `scheduler.sh` text in `generate_report()` (~lines 227-229) |
| `tests/test_ceiling_revisit.py` | Update `test_generate_report`'s stale-text assertions; add XL-text assertions |
| `commands/ceiling-revisit.md` | Fix Phase 4's filed title/body text; replace unconditional filing with duplicate/policy guard |
| `tests/test_ceiling_revisit_command.py` (new) | Static-assertion tests on `commands/ceiling-revisit.md` prose |

---

## Task 0 — Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-04-ceiling-revisit-hygiene-design.md`,
`docs/superpowers/plans/2026-09-04-ceiling-revisit-hygiene-plan.md`

Per the `[PATTERN]` memory lesson (issue #42) and the pending structural fix (#387): the
implement phase's `feat/issue-361-...` branch forks from `main`, so this ticket's own spec and
this plan file (both refine-branch-only, not on `main`) do **not** transfer automatically.
Without them, Gate 2 (conformance) falls back to `NO_SPEC=true` advisory-only review. Copy both
files onto the feat branch and commit them before starting Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=361
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-09-04-ceiling-revisit-hygiene-design.md \
  docs/superpowers/plans/2026-09-04-ceiling-revisit-hygiene-plan.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-361-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-09-04-ceiling-revisit-hygiene-design.md \
  docs/superpowers/plans/2026-09-04-ceiling-revisit-hygiene-plan.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-09-04-ceiling-revisit-hygiene-design.md && \
test -f docs/superpowers/plans/2026-09-04-ceiling-revisit-hygiene-plan.md && echo OK
git add docs/superpowers/specs/2026-09-04-ceiling-revisit-hygiene-design.md \
  docs/superpowers/plans/2026-09-04-ceiling-revisit-hygiene-plan.md
git commit -m "docs(#361): copy spec/plan onto the implementation branch"
```

---

## Task 1 — Fix stale L/scheduler.sh text in `ceiling_revisit.py`'s rendered report (R1, part of R5)

### Files
- `tests/test_ceiling_revisit.py`
- `scripts/ceiling_revisit.py`

### Step 1.1 — Write failing test assertions

Edit `tests/test_ceiling_revisit.py`. In `test_generate_report` (currently ~lines 132-136),
replace the stale-text assertions and add new ones:

```python
    # Bucket table rendered; keeps 'scheduler_lib.sh' (not prefixed with dark-factory/)
    assert "### Per-Bucket Triad" in report
    assert "| L+XL |" in report
    assert "`scripts/scheduler_lib.sh`" in report
    assert "dark-factory/scripts/scheduler_lib.sh" not in report

    # Actionable rule name is XL-specific, not the merged L+XL measured cohort.
    # Anchored on "The L=..." (not bare "L=always-above-ceiling") because that bare
    # substring is also present inside the correct "XL=always-above-ceiling" text.
    assert "XL=always-above-ceiling" in report
    assert "The L=always-above-ceiling" not in report
```

This replaces the existing block:
```python
    # Bucket table rendered; keeps 'scheduler.sh' (not prefixed with dark-factory/)
    assert "### Per-Bucket Triad" in report
    assert "| L+XL |" in report
    assert "`scheduler.sh`" in report
    assert "dark-factory/scheduler.sh" not in report
```

### Step 1.2 — Verify the test fails

```bash
python -m pytest tests/test_ceiling_revisit.py::test_generate_report -v
```

Expected: `FAILED` — `assert "`scripts/scheduler_lib.sh`" in report` fails (report still says
`` `scheduler.sh` ``), and/or `assert "XL=always-above-ceiling" in report` fails (report still
says only "The L=always-above-ceiling rule").

### Step 1.3 — Implement the fix

Edit `scripts/ceiling_revisit.py`. In `generate_report()`, replace (~lines 225-231):

```python
        if l_bucket_needs_issue:
            lines += [
                "**The L=always-above-ceiling rule may be overly conservative.**",
                "A separate code-change issue should be filed to revisit `is_above_ceiling()`"
                " in `scheduler.sh`.",
                "",
            ]
```

with:

```python
        if l_bucket_needs_issue:
            lines += [
                "**The XL=always-above-ceiling rule may be overly conservative.**",
                "A separate code-change issue should be filed to revisit `is_above_ceiling()`"
                " in `scripts/scheduler_lib.sh`.",
                "",
            ]
```

Do not touch the `### L-Bucket Observation` header or the "L+XL success rate" prose lines
(~lines 220, 224) — the measured cohort is genuinely the merged L+XL bucket; only the
actionable rule name and file citation are stale. Do not touch the `--keywords` argparse help
string at ~line 252 (out of scope per spec Alternative 5).

### Step 1.4 — Verify the test passes

```bash
python -m pytest tests/test_ceiling_revisit.py -v
```

Expected: all tests in the file `PASSED`.

### Step 1.5 — Commit

```bash
git add scripts/ceiling_revisit.py tests/test_ceiling_revisit.py
git commit -m "fix(ceiling-revisit): correct stale L-bucket/scheduler.sh report text (#361)

generate_report() named the L bucket and scheduler.sh as the always-above-ceiling
rule/file; XL has been the always-above-ceiling bucket since 4feef16 and the
function now lives in scripts/scheduler_lib.sh."
```

---

## Task 2 — Fix Phase 4's filed issue title/body text in `commands/ceiling-revisit.md` (R2, part of R5)

Edit only `commands/ceiling-revisit.md` — `.archon/commands/ceiling-revisit.md` is a gitignored
**runtime copy** (`git ls-files -- .archon/commands/` is empty), not a second source file. A
stale copy with the old "L="/`scheduler.sh` text may exist on disk there; do not edit it, and do
not let a repo-wide grep for the stale string steer an edit into it.

### Files
- `tests/test_ceiling_revisit_command.py` (new)
- `commands/ceiling-revisit.md`

### Step 2.1 — Write failing test (new file)

Create `tests/test_ceiling_revisit_command.py`:

```python
"""Static-assertion tests for commands/ceiling-revisit.md prose.

There is no bash-execution harness for command files in this repo (see
tests/test_command_issue_number_mandate.py for the established convention) —
these tests assert on the literal text of the fenced gh/jq commands instead.
"""
import re
from pathlib import Path

COMMAND_FILE = Path(__file__).resolve().parents[1] / "commands" / "ceiling-revisit.md"


def _text():
    return COMMAND_FILE.read_text(encoding="utf-8")


def test_filed_issue_title_is_corrected():
    text = _text()
    assert "Revisit XL=always-above-ceiling rule" in text, (
        "Phase 4 must file issues titled with the corrected XL rule name (#361)"
    )
    assert "Revisit L=always-above-ceiling rule" not in text, (
        "Phase 4 must not still file issues with the stale L rule name (#361)"
    )


def test_filed_issue_body_cites_scheduler_lib():
    text = _text()
    assert "scripts/scheduler_lib.sh" in text, (
        "Phase 4's filed issue body must cite scripts/scheduler_lib.sh, not scheduler.sh (#361)"
    )


def test_target_path_markers_preserved():
    text = _text()
    assert text.count("# TARGET-PATH") == 2, (
        "Phase 1's two '# TARGET-PATH' markers on the python3 dark-factory/scripts/... lines "
        "must survive this text/logic fix untouched (#361 is not a path fix)"
    )
```

### Step 2.2 — Verify the test fails

```bash
python -m pytest tests/test_ceiling_revisit_command.py -v
```

Expected: `FAILED` on both `test_filed_issue_title_is_corrected` (stale title still present)
and `test_filed_issue_body_cites_scheduler_lib` (only `scheduler.sh` present, not
`scripts/scheduler_lib.sh`); `test_target_path_markers_preserved` `PASSED` (a non-regression
guard — the markers are untouched by this task, so it's green from the start).

### Step 2.3 — Implement the fix

Edit `commands/ceiling-revisit.md`. Replace the Phase 4 `gh issue create` block's title and
body text (this edit is superseded structurally by Task 3's guard, but land the text fix as
its own commit first per TDD — Task 3 will move this corrected text inside the guard's `file`
branch):

Replace:
```
    --title "Revisit L=always-above-ceiling rule in is_above_ceiling() — scheduler.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #${ISSUE_NUM}, window ${SINCE}→${UNTIL})
found the L-bucket success rate exceeds 70% at n≥5. The L=always-above-ceiling rule
in \`scheduler.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scheduler.sh\` (~line 213).
- Assess whether the L-bucket ceiling should be relaxed (e.g. L+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scheduler.sh\`.
```

with:
```
    --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #${ISSUE_NUM}, window ${SINCE}→${UNTIL})
found the L+XL bucket success rate exceeds 70% at n≥5. The XL=always-above-ceiling rule
in \`scripts/scheduler_lib.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scripts/scheduler_lib.sh\`.
- Assess whether the XL-bucket ceiling should be relaxed (e.g. XL+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scripts/scheduler_lib.sh\`.
```

Note the `(~line 213)` reference is dropped per the spec (stale precision — `is_above_ceiling()`
is a named, greppable function).

### Step 2.4 — Verify the test passes

```bash
python -m pytest tests/test_ceiling_revisit_command.py -v
```

Expected: all three tests `PASSED`.

### Step 2.5 — Commit

```bash
git add commands/ceiling-revisit.md tests/test_ceiling_revisit_command.py
git commit -m "fix(ceiling-revisit): correct stale L-bucket/scheduler.sh text in filed issue (#361)

Phase 4's gh issue create independently carried the same stale rule name and file
citation as the report text fixed in the prior commit."
```

---

## Task 3 — Replace Phase 4's unconditional filing with a duplicate/policy guard (R3, R4, R5)

Note: Step 3.3 replaces the *entire* Phase 4 fenced block, including the title/body text just
landed in Task 2 — it re-emits that text verbatim inside the guard's `file` branch. This is
intentional TDD staging (each commit is independently green); don't try to preserve Task 2's
diff or minimize churn against it.

### Files
- `tests/test_ceiling_revisit_command.py`
- `commands/ceiling-revisit.md`

### Step 3.1 — Write failing test assertions

Append to `tests/test_ceiling_revisit_command.py`:

```python
def test_phase_4_has_duplicate_policy_guard():
    text = _text()
    assert "gh issue list" in text and "--state all" in text, (
        "Phase 4 must query the tracker for existing always-above-ceiling issues before "
        "filing, not file unconditionally (#361)"
    )
    assert "stateReason" in text and "NOT_PLANNED" in text, (
        "Phase 4 must branch on stateReason/NOT_PLANNED to distinguish a policy-declined "
        "issue (skip) from a completed cadence issue (file) — a purely textual comment "
        "without the actual gh/jq branch does not satisfy this (#361)"
    )


def test_guard_anchor_matches_filed_title_substring():
    text = _text()
    # The jq filter's search substring and the filed --title must share the same anchor
    # so the guard can never drift out of sync with what Phase 4 itself files (#361). Extract
    # both literals by regex (rather than asserting each in isolation) so this test would fail
    # if a future edit changed one anchor without the other.
    filter_match = re.search(r'test\("([^"]+)"', text)
    title_match = re.search(r'--title "Revisit ([^"]+) rule', text)
    assert filter_match and title_match, "guard filter or filed title not found"
    assert filter_match.group(1) in title_match.group(1), (
        f"guard anchor {filter_match.group(1)!r} must be a substring of the filed title "
        f"{title_match.group(1)!r} — they must never drift apart"
    )
```

### Step 3.2 — Verify the tests fail

```bash
python -m pytest tests/test_ceiling_revisit_command.py -v
```

Expected: `FAILED` on `test_phase_4_has_duplicate_policy_guard` (no `stateReason`/`NOT_PLANNED`
in the file yet) and `test_guard_anchor_matches_filed_title_substring` (no `gh issue list`
guard present yet).

### Step 3.3 — Implement the guard

Edit `commands/ceiling-revisit.md`. Replace the entire Phase 4 fenced block (the
`if [ "$L_NEEDS_ISSUE" = "True" ]; then ... fi` block, containing the title/body text already
corrected in Task 2) with:

```bash
if [ "$L_NEEDS_ISSUE" = "True" ]; then
  # Duplicate/policy guard (#361). "always-above-ceiling" is the stable substring across both
  # the L->XL rename and the scheduler.sh->scheduler_lib.sh split, and is guaranteed to match
  # the title this Phase itself files below — the guard and the filed title can never drift
  # apart as long as both are anchored to this same substring.
  MATCHES=$(gh issue list --repo "$REPO" --state all --limit 500 \
    --json number,title,state,stateReason \
    --jq '[.[] | select(.title | test("always-above-ceiling"; "i"))] | sort_by(-.number)')

  OPEN_MATCH=$(echo "$MATCHES" | jq -r '[.[] | select(.state=="OPEN")] | first.number // empty')
  NEWEST_NUM=$(echo "$MATCHES" | jq -r 'first.number // empty')
  NEWEST_REASON=$(echo "$MATCHES" | jq -r 'first.stateReason // empty')

  if [ -n "$OPEN_MATCH" ]; then
    XL_ACTION="skip-duplicate"; XL_CITE="$OPEN_MATCH"
  elif [ -n "$NEWEST_NUM" ] && [ "$NEWEST_REASON" = "NOT_PLANNED" ]; then
    XL_ACTION="skip-policy"; XL_CITE="$NEWEST_NUM"
  else
    XL_ACTION="file"
  fi

  if [ "$XL_ACTION" = "file" ]; then
    gh issue create \
      --repo "$REPO" \
      --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
      --body "## Purpose

The weekly dispatch ceiling analysis (issue #${ISSUE_NUM}, window ${SINCE}→${UNTIL})
found the L+XL bucket success rate exceeds 70% at n≥5. The XL=always-above-ceiling rule
in \`scripts/scheduler_lib.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scripts/scheduler_lib.sh\`.
- Assess whether the XL-bucket ceiling should be relaxed (e.g. XL+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scripts/scheduler_lib.sh\`.

## References

- Triggering analysis: issue #${ISSUE_NUM}
- Policy: the dispatch-ceiling revisit design (see the dispatch-ceiling design spec)

---
*Filed automatically by weekly ceiling revisit*" \
      --label "enhancement" \
      --label "priority: should-have"
  else
    REASON=$([ "$XL_ACTION" = "skip-policy" ] && echo "closed by operator policy decision" \
                                                || echo "already open, covering this observation")
    gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "XL-bucket success rate cleared the \
>70%-at-n>=5 threshold again this cycle, but issue #${XL_CITE} is ${REASON} — see #${XL_CITE} \
instead of filing a duplicate."
  fi
fi
```

Also update the Phase 4 heading and its one-line description just above the fenced block:

Replace:
```
## Phase 4 — File L-Bucket Code-Change Issue (conditional)

Only execute if `L_NEEDS_ISSUE` is `True`.
```

with:
```
## Phase 4 — File XL-Bucket Code-Change Issue, Guarded (conditional)

Only execute if `L_NEEDS_ISSUE` is `True`. Before filing, query the tracker once for an
existing `always-above-ceiling`-titled issue (open+closed) and branch on its
`state`/`stateReason`: an open match is a duplicate (skip), a closed/`NOT_PLANNED` match is a
prior operator policy decision (skip), otherwise file a new issue.
```

### Step 3.4 — Verify the tests pass

```bash
python -m pytest tests/test_ceiling_revisit_command.py -v
```

Expected: all 5 tests `PASSED`.

### Step 3.5 — Commit

```bash
git add commands/ceiling-revisit.md tests/test_ceiling_revisit_command.py
git commit -m "feat(ceiling-revisit): add permanent XL duplicate/policy guard to Phase 4 (#361)

Replaces unconditional issue filing with a state/stateReason-driven guard: an open
always-above-ceiling-titled issue is a duplicate, a closed/NOT_PLANNED one is a prior
operator policy decision — both skip filing and post a skip-note comment instead. #331
falls out of the query rather than being hardcoded, so a future policy decision needs
no command-file edit."
```

---

## Task 4 — Full verification pass

### Step 4.1 — Run the full test suite and CI parity checks

```bash
python -m pytest tests/ -v
bash tests/test_smoke_gate.sh
```

Expected: all tests `PASSED`, including:
- `tests/test_ceiling_revisit.py` (all, including the updated `test_generate_report`)
- `tests/test_ceiling_revisit_command.py` (all 5 tests)
- no regressions elsewhere

`test_smoke_gate.sh` exercises the same check CI runs (`.github/workflows/ci.yml`); this ticket
does not touch `workflows/archon-dark-factory.yaml`, so the DAG-check CI jobs do not need a
local re-run.

### Step 4.2 — Confirm no stale text remains anywhere in the two touched files

```bash
grep -rnE '(^|[^X])L=always-above-ceiling' scripts/ceiling_revisit.py commands/ceiling-revisit.md && echo "FOUND STALE TEXT" || echo "clean"
grep -n 'scheduler\.sh' scripts/ceiling_revisit.py commands/ceiling-revisit.md
```

Expected: first command prints `clean` (no matches — the pattern excludes `XL=always-above-ceiling`
by requiring the character before `L=` not be `X`). The second command must print exactly two
hits, both expected and not further action items:
- `scripts/ceiling_revisit.py:252: help="Pipe-delimited keyword list (default: scheduler.sh
  default)"` — the out-of-scope `--keywords` argparse help string (per spec Alternative 5).
- `commands/ceiling-revisit.md:<N>: # the L->XL rename and the scheduler.sh->scheduler_lib.sh
  split, ...` — the guard's own explanatory comment (Task 3 Step 3.3), which uses `scheduler.sh`
  only to name the historical rename it is guarding against, not as a stale citation. If any
  *other* hit appears in `commands/ceiling-revisit.md`, that is a real regression — investigate.

### Step 4.3 — No commit needed

This task is verification-only; nothing to commit if all checks pass. If a check fails,
return to the relevant task, fix, and commit a follow-up.

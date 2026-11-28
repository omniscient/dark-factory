# `post_marker_comment.sh` footer-kind parameter — Implementation Plan

**Issue:** #404

**Goal:** Give `scripts/post_marker_comment.sh` an optional 4th positional argument
`footer-kind` (default `scheduler`). Then replace the inlined
mktemp/printf/`tracker comment`/rm block in `commands/dark-factory-conformance.md` Phase 4 with
one `post_marker_comment.sh … factory || true` call. The posted comment stays byte-identical
and the 3 existing callers are not touched.

**Architecture:** One additive change to one script (`FOOTER_KIND="${4:-scheduler}"` passed
quoted to `factory_core/cli.py marker`, whose argparse `choices=list(identity._MARKERS)` stays
the only allow-list). One call-site swap in an agent-executed Markdown command. One new pytest
module that runs the real script against a stub `python3` on `PATH`. One updated
string-assertion test. Doc sync for the script's described default and for line citations
that move when the Phase 4 block shrinks by 8 lines.

**Tech Stack:** bash, Python 3 / pytest (`python -m pytest tests/ -v`, CI sets
`PYTHONPATH=scripts`), Markdown.

**Spec:** `docs/superpowers/specs/2026-11-28-post-marker-comment-footer-kind-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/test_post_marker_comment.py` | Create | Exec tests of the script: default footer kind, `factory` override, body assembly, `--marker` passthrough, empty footer on a failed `marker` call, exit-code propagation, real `cli.py marker` kind validation |
| `scripts/post_marker_comment.sh` | Modify | Add `FOOTER_KIND="${4:-scheduler}"`, pass it to `cli.py marker "$FOOTER_KIND"`, update header `Usage:`/default rationale and the `:?` usage strings |
| `tests/test_conformance_command_shadow_review.py` | Modify | Replace the `--body-file "$TMPFILE" \|\| true` assertion with call-shape and no-inline-copy guards |
| `commands/dark-factory-conformance.md` | Modify | Phase 4 only: drop `FOOTER=`/marker line/`mktemp`/`printf`/`tracker comment`/`rm`, trim `SHADOW_BODY`, call `post_marker_comment.sh … factory \|\| true` |
| `docs/run-lifecycle.md` | Modify | `:688-690` default-footer sentence and its `post_marker_comment.sh:` citation; `:794`, `:814-815`, `:819`, `:1319` conformance-command line citations |
| `docs/features.md` | Modify | `:134` (`write_memory_entry` citation); `:111` (`:734` BLOCKED `emit_verdict` citation) |
| `docs/configuration.md` | Modify | `:148` (`690-692,746-748` shadow re-resolution citations) |

**Explicitly not touched** (spec Requirement 3/4, Acceptance Criteria 2 and 4):
`workflows/**`, `scripts/blast_gate_check.sh`, `.github/**` (including `ci.yml`, since the new
test is a pytest and needs no CI registration), `scripts/factory_core/**`, `gate_*`,
`verdict_gate_check.sh`, breaker, budgets, `deploy/**`. Also untouched: everything in
`commands/dark-factory-conformance.md` above the `FOOTER=` line (`:594-603`: the #406
re-resolution snippet, the guard, the `conformance.md` append), the attestation write,
`emit_verdict`, every `STATUS:`/`SHADOW_*` line, and Phase 5. These tests must pass with no
edits: `tests/test_shadow_model_resolution.py`, `tests/test_push_gate_dag.py`,
`tests/test_push_gate_dag_exec.py` and `tests/test_ci_covers_shell_tests.py`.

**Implementation notes on the doc-citation set (read before Task 3).** Spec Requirement 7 says
to re-anchor every citation of a conformance-command line after the edited block, by content.
It names `features.md:134` and `run-lifecycle.md:814/819/1319`. It also says that
`docs/configuration.md` and `docs/features.md:111` "do not move". That last part is only partly
true on the current branch head. `configuration.md:148` also cites `:690-692,746-748` (the
Phase 5 re-resolution copies), and `features.md:111` also cites `:734` (the BLOCKED
`emit_verdict`). Both of those lines are below the edit, so both shift. This plan follows the
spec's governing rule and AC5 and re-anchors those two as well. The `:594-596` and `:586`
parts of those same cells are above the edit and stay as they are. Several citations were
already stale before this ticket (the #406 merge moved lines). For example `:748-752` points
at the BLOCKED `emit_verdict` region today, not at the step-5 "does not reliably surface"
text it describes. Re-anchoring by content fixes those as a side effect. Every target below
was computed from a dry run of the exact Phase 4 edit in Task 2 (796-line result, net −8
lines from `:625` on).

| Citation (doc:line) | Content anchor | Now on head | After Task 2 |
|---|---|---|---|
| `features.md:134` `dark-factory-conformance.md:656` | `write_memory_entry "$TARGET" …` call | `:660` | `:652` |
| `features.md:111` `:734` | `emit_verdict "conformance" "BLOCKED" …` | `:741` | `:733` |
| `run-lifecycle.md:814` PASS `:580-674` | `## Phase 4: PASS` heading → ``Exit `0`.`` line | `:581-678` | `:581-670` |
| `run-lifecycle.md:815` BLOCKED `:676-752` | `## Phase 5: BLOCKED` heading → end of step 5 ("does not reliably surface") | `:680-762` | `:672-754` |
| `run-lifecycle.md:819` and `:1319` `:748-752` | Phase 5 step 5 ("Exit non-zero … does not reliably surface") | `:758-762` | `:750-754` |
| `run-lifecycle.md:794` `(Phase 6, `:754-794`)` — a bare citation, not matched by a `dark-factory-conformance.md:` grep | `## Phase 6: ARTIFACT GUARD` heading → last line of the file | `:764-804` | `:756-796` |
| `configuration.md:148` `690-692,746-748` | Phase 5 `ROOT=`/`SHADOW_MODEL_DEFAULT=`/`SHADOW_MODEL_PIN=` copies 2 and 3 | `:690-692`, `:746-748` | `:682-684`, `:738-740` |
| `run-lifecycle.md:690` `post_marker_comment.sh:16-20` | header paragraph explaining the `scheduler` default / #358 grace clock | `:16-20` | `:20-24` (after Task 1) |

These stay unchanged on purpose. `configuration.md:148` `:594-596`, `features.md:111` `:586`,
`run-lifecycle.md:1394` `:587-589`, `run-lifecycle.md:663`/`:1400` and `configuration.md:304`
all sit above the edit. `features.md:107` `:593`-`:607` starts above the edit, and both
before and after the edit it spans the guard through the start of the shadow comment body.
`features.md:110` cites workflow call sites only. Task 3 step 1 re-greps first, so a later
change on main is caught.

**The doc line numbers in this plan (`run-lifecycle.md:688-690`, `:794`, `:814`, …;
`features.md:111`, `:134`; `configuration.md:148`) are locators only.** Other tickets move
them (#409 inserts lines in `docs/run-lifecycle.md` and rewrites the `dark-factory-plan.md`
citation on the same `configuration.md:148` row). Every doc edit in Task 3 is matched by its
citation text, never by doc line number, and each of those texts must match exactly once
(twice for `:748-752`). If a pattern does not match, find the citation by its content anchor
from the table; do not skip it. On `configuration.md:148` change only the
`dark-factory-conformance.md:…` substring and leave the rest of the row as found.

---

## Task 1: `post_marker_comment.sh` gains the `footer-kind` argument

**Files:**
- Create: `tests/test_post_marker_comment.py`
- Modify: `scripts/post_marker_comment.sh`

- [ ] **Step 1: Re-confirm the caller set (spec Assumption).**

  Run: `git grep -n "post_marker_comment.sh" -- workflows scripts commands`

  Expected: exactly `scripts/blast_gate_check.sh:312` and `workflows/archon-dark-factory.yaml`
  `:552`, `:562`, `:683`, `:693`. These are the 3 logical callers. The 4 YAML lines are the
  two marker kinds in `refine-push` and in `plan-push-and-advance`. The script's own
  `Usage`/`:?` lines may also show. If a new caller has appeared, check that it still wants
  the `scheduler` default (it gets it by omitting arg 4) and record it in the commit message.
  Do not edit it.

- [ ] **Step 2: Write the failing test module.** Create `tests/test_post_marker_comment.py`
  with exactly:

```python
"""post_marker_comment.sh footer-kind argument (#404).

Runs the real script with a stub `python3` executable first on PATH: the stub logs its
argv, answers `marker <kind>` with a fixed footer, copies the `--body-file` content to a
capture file (the script deletes its temp file right after the call), and exits with a
configurable code. Skips on non-POSIX hosts, like tests/test_push_gate_dag_exec.py.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "post_marker_comment.sh"
_TAG = "<!-- df-test-marker -->"
_BODY = "line one\n`$HOME` and $(date) stay literal"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="needs a POSIX host with bash",
)

# argv is logged one call per line, fields separated by \x1f, so paths and the marker
# tag (which contain spaces) split back cleanly.
_STUB = r'''#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$STUB_LOG"; printf '\n' >> "$STUB_LOG"
case "$2" in
  marker)
    [ "${STUB_MARKER_RC:-0}" -eq 0 ] || exit "$STUB_MARKER_RC"
    printf '*Stub footer %s*\n' "$3"
    exit 0 ;;
  tracker)
    _prev=""
    for _a in "$@"; do
      if [ "$_prev" = "--body-file" ]; then cat "$_a" > "$STUB_BODY"; fi
      _prev="$_a"
    done
    exit "${STUB_TRACKER_RC:-0}" ;;
esac
exit 97
'''


def _run(tmp_path, *args, marker_rc=0, tracker_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python3"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "argv.log"
    captured = tmp_path / "body.md"
    env = dict(
        os.environ,
        PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        STUB_LOG=str(log),
        STUB_BODY=str(captured),
        STUB_MARKER_RC=str(marker_rc),
        STUB_TRACKER_RC=str(tracker_rc),
    )
    proc = subprocess.run(["bash", str(_SCRIPT), *args], env=env,
                          capture_output=True, text=True)
    calls = [line.split("\x1f")[:-1]
             for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])]
    posted = captured.read_text(encoding="utf-8") if captured.exists() else None
    return proc, calls, posted


def _marker_calls(calls):
    return [c[1:] for c in calls if c[1:2] == ["marker"]]


def _tracker_call(calls):
    tracker = [c for c in calls if c[1:3] == ["tracker", "comment"]]
    assert len(tracker) == 1, calls
    return tracker[0]


def test_three_args_default_to_scheduler_footer(tmp_path):
    proc, calls, _ = _run(tmp_path, "7", _TAG, _BODY)
    assert proc.returncode == 0, proc.stderr
    assert _marker_calls(calls) == [["marker", "scheduler"]]
    assert calls[0][0].endswith("factory_core/cli.py")


def test_fourth_arg_selects_factory_footer(tmp_path):
    proc, calls, _ = _run(tmp_path, "7", _TAG, _BODY, "factory")
    assert proc.returncode == 0, proc.stderr
    assert _marker_calls(calls) == [["marker", "factory"]]


@pytest.mark.parametrize("kind", [None, "factory"])
def test_body_assembly_and_marker_passthrough(tmp_path, kind):
    args = ["7", _TAG, _BODY] + ([kind] if kind else [])
    proc, calls, posted = _run(tmp_path, *args)
    assert proc.returncode == 0, proc.stderr
    assert posted == f"{_TAG}\n{_BODY}\n\n---\n*Stub footer {kind or 'scheduler'}*"
    tracker = _tracker_call(calls)
    assert tracker[0].endswith("factory_core/providers/cli.py")
    assert tracker[tracker.index("--id") + 1] == "7"
    assert tracker[tracker.index("--marker") + 1] == _TAG


def test_failed_footer_resolution_still_posts_with_empty_footer(tmp_path):
    proc, calls, posted = _run(tmp_path, "7", _TAG, _BODY, "no-such-kind", marker_rc=2)
    assert _marker_calls(calls) == [["marker", "no-such-kind"]]
    assert posted == f"{_TAG}\n{_BODY}\n\n---\n"
    assert proc.returncode == 0, proc.stderr  # the tracker CLI's code, not the marker's


def test_tracker_exit_code_propagates(tmp_path):
    proc, _, posted = _run(tmp_path, "7", _TAG, _BODY, "factory", tracker_rc=3)
    assert posted is not None
    assert proc.returncode == 3


def test_real_marker_subcommand_validates_kind():
    cli = _REPO_ROOT / "scripts" / "factory_core" / "cli.py"
    ok = subprocess.run([sys.executable, str(cli), "marker", "factory"],
                        capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip().endswith("Dark Factory*")
    bad = subprocess.run([sys.executable, str(cli), "marker", "no-such-kind"],
                         capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert bad.returncode != 0
```

  The last test runs the real `cli.py`, with no stub. It pins the spec's statement that
  validation lives in `cli.py marker` (argparse `choices`), so the script needs no bash-side
  allow-list.

- [ ] **Step 3: Run it and confirm it fails for the right reason.**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_post_marker_comment.py -v`

  Expected: `3 failed, 4 passed`. The 3 failures are `test_fourth_arg_selects_factory_footer`,
  `test_body_assembly_and_marker_passthrough[factory]` and
  `test_failed_footer_resolution_still_posts_with_empty_footer`. All 3 fail because the
  script still calls `marker scheduler` whatever arg 4 is. The default-kind,
  `[None]`-assembly, exit-propagation and real-CLI tests already pass, which pins today's
  behaviour as the back-compat baseline.

- [ ] **Step 4: Implement.** Replace the contents of `scripts/post_marker_comment.sh` with exactly
  the following. The changes are: the header `Usage:` line, a new `[footer-kind]` argument
  block, the reworded default-rationale paragraph (#358, now saying never to pass
  `refinement`), the 3 `:?` usage strings, the new `FOOTER_KIND=` line, and
  `marker "$FOOTER_KIND"`. Everything else stays byte-identical: the marker-tag leading line,
  `---` separator, mktemp/printf/`tracker comment --marker`/rm, and `exit "$_RC"`.

```bash
#!/usr/bin/env bash
# Post/update an idempotent marker comment on a tracker item, appending the standard
# footer. Extracted from refine-push/plan-push-and-advance (workflows/archon-dark-factory.yaml)
# where the mktemp/printf/tracker-comment/rm sequence was duplicated verbatim between the
# spec-pending-review and plan-pending-review gate-label-failure warnings.
#
# Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]
#   <issue-number>  numeric tracker item id
#   <marker-tag>    idempotency marker, e.g. "<!-- df-gate-label-failure -->" — passed
#                    both as the leading line of the posted body and as the
#                    `tracker comment --marker` value so a re-post updates in place
#                    rather than appending a new comment.
#   <body>          markdown body text, WITHOUT the marker-tag line or footer — both
#                    are added by this script.
#   [footer-kind]   footer to append: a factory_core/identity.py::_MARKERS key, passed
#                    to `factory_core/cli.py marker`, which validates it. Default
#                    `scheduler`. An unknown kind yields an empty footer; the comment
#                    still posts.
#
# The default is `scheduler` (not `refinement`) deliberately: the scheduler's
# elapsed_minutes_since_marker (scheduler_lib.sh) anchors the direct-to-pr grace clock
# on the *last* comment matching the "Refinement Pipeline" marker, so a caller must
# never pass `refinement` for a comment that should not reset the grace clock (issue
# #358 review). `scheduler` and `factory` do not match that pattern.
#
# Exit: propagates the tracker CLI's exit code (non-zero on a failed post/update);
# callers that want this to stay advisory-only append `|| true` at the call site.
set -uo pipefail

ISSUE_NUM="${1:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
MARKER_TAG="${2:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
BODY="${3:?Usage: post_marker_comment.sh <issue-number> <marker-tag> <body> [footer-kind]}"
FOOTER_KIND="${4:-scheduler}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"
_PCLI_FACTORY_CORE="${SCRIPT_DIR}/factory_core/cli.py"

_FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker "$FOOTER_KIND" 2>/dev/null || echo "")
_FULL_BODY="${MARKER_TAG}
${BODY}

---
${_FOOTER}"

TMPFILE=$(mktemp /tmp/marker-comment-XXXXXX.md)
printf '%s' "$_FULL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "$MARKER_TAG" --body-file "$TMPFILE"
_RC=$?
rm -f "$TMPFILE"
exit "$_RC"
```

  Keep the file executable. Run `git diff --summary scripts/post_marker_comment.sh` and
  expect no `mode change` line. If the file was rewritten without the bit, run
  `chmod +x scripts/post_marker_comment.sh`.

- [ ] **Step 5: Run the new tests and the existing callers' tests.**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_post_marker_comment.py tests/test_push_gate_dag.py tests/test_push_gate_dag_exec.py -v`

  Expected: every test passes (`test_post_marker_comment.py`: `7 passed`). The two
  `test_push_gate_dag*` files pass **without edits**, which shows the 3-arg callers still
  get the scheduler footer (AC4).

- [ ] **Step 6: Confirm the header line numbers used in Task 3.**

  Run: `grep -n "^# The default is\|#358 review" scripts/post_marker_comment.sh`

  Expected: `20:# The default is `scheduler` (not `refinement`) deliberately: …` and
  `24:# #358 review). `scheduler` and `factory` do not match that pattern.` That makes the
  new citation `scripts/post_marker_comment.sh:20-24`.

- [ ] **Step 7: Commit.**

```bash
git add tests/test_post_marker_comment.py scripts/post_marker_comment.sh
git commit -m "feat(#404): post_marker_comment.sh optional footer-kind argument

Optional 4th positional arg (default scheduler) passed quoted to
factory_core/cli.py marker, which validates it against identity._MARKERS.
The 3 existing callers omit it and keep the scheduler footer (#358 grace clock)."
```

---

## Task 2: Conformance Phase 4 calls the helper instead of inlining it

**Files:**
- Modify: `tests/test_conformance_command_shadow_review.py`
- Modify: `commands/dark-factory-conformance.md`

- [ ] **Step 1: Update the existing assertion so it fails first.** In
  `tests/test_conformance_command_shadow_review.py`, replace the body of
  `test_phase_4_pass_emits_shadow_fields_and_marker_comment`. Change only that function:

```python
def test_phase_4_pass_emits_shadow_fields_and_marker_comment():
    text = CMD.read_text(encoding="utf-8")
    phase4_idx = text.find("## Phase 4: PASS")
    phase5_idx = text.find("## Phase 5: BLOCKED")
    section = text[phase4_idx:phase5_idx]
    assert "SHADOW_MODEL" in section
    assert "df-shadow-review" in section
    # #404: posted through the shared helper with the factory footer, not an inlined copy
    assert "post_marker_comment.sh" in section
    # Requirement 3: the advisory PASS-path comment must never block a PASS
    assert '"$SHADOW_BODY" factory || true' in section
    assert "mktemp" not in section, "inlined mktemp/tracker-comment block crept back"
    assert "## Shadow (Fable) Review — conformance" in section
    # the marker is the call's argument only; a second copy would mean it is also left in
    # SHADOW_BODY and posted twice
    assert section.count("<!-- df-shadow-review -->") == 1
```

- [ ] **Step 2: Run it and confirm it fails.**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_conformance_command_shadow_review.py -v`

  Expected: `1 failed, 7 passed`. The failure is
  `test_phase_4_pass_emits_shadow_fields_and_marker_comment` on
  `assert "post_marker_comment.sh" in section`.

- [ ] **Step 3: Replace the inline block.** In `commands/dark-factory-conformance.md` Phase 4,
  inside `if [ -n "${SHADOW_MODEL_PIN:-}" ]; then`, replace exactly these lines (`:604-623`
  on the branch head; locate them by content):

```bash
  FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
  SHADOW_BODY="<!-- df-shadow-review -->
## Shadow (Fable) Review — conformance

${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

\`\`\`
SHADOW_MODEL: ${SHADOW_MODEL_PIN}
SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
\`\`\`

---
$FOOTER"
  TMPFILE=$(mktemp /tmp/df-shadow-review-XXXXXX.md)
  printf '%s' "$SHADOW_BODY" > "$TMPFILE"
  python3 dark-factory/scripts/factory_core/providers/cli.py tracker comment \
    --id "$ISSUE_NUM" --marker "<!-- df-shadow-review -->" --body-file "$TMPFILE" || true  # TARGET-PATH — advisory post, never blocks PASS (Requirement 3)
  rm -f "$TMPFILE"
```

  with:

```bash
  SHADOW_BODY="## Shadow (Fable) Review — conformance

${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

\`\`\`
SHADOW_MODEL: ${SHADOW_MODEL_PIN}
SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
\`\`\`"
  bash dark-factory/scripts/post_marker_comment.sh "$ISSUE_NUM" "<!-- df-shadow-review -->" \
    "$SHADOW_BODY" factory || true  # TARGET-PATH — advisory post, never blocks PASS (Requirement 3)
```

  The lines before this block (`ROOT=` … `} >> "$ARTIFACTS_DIR/conformance.md"` and the
  blank line after it) and the `fi` / closing fence / prose after it stay byte-identical. The
  `dark-factory/scripts/…` relative path and the `# TARGET-PATH` tag match the line being
  replaced, which ran from the same cwd.

- [ ] **Step 4: Prove the posted body is byte-identical (spec Requirement 4).** This is a
  throwaway check and is not committed. It evaluates the old and new `SHADOW_BODY`
  assignments with fixed values and assembles the new one the way the script does
  (`<marker>\n<body>\n\n---\n<footer>`):

```bash
cat > /tmp/df404_byte_check.sh <<'SH'
export SHADOW_VERDICT_LINE='**Verdict:** ✅ Conforms' SHADOW_MODEL_PIN=claude-fable-5-1 SHADOW_STATUS=PASS SHADOW_FINDINGS_COUNT=0 SHADOW_SEVERITY=none
FOOTER='*Posted by Dark Factory Dark Factory*'
OLD=$(sed -n '/^  SHADOW_BODY="/,/"$/p' "$1")
NEW=$(sed -n '/^  SHADOW_BODY="/,/"$/p' "$2")
eval "$OLD"; OLD_POSTED="$SHADOW_BODY"
eval "$NEW"; NEW_POSTED="<!-- df-shadow-review -->
${SHADOW_BODY}

---
${FOOTER}"
if [ "$OLD_POSTED" = "$NEW_POSTED" ]; then echo BYTE-IDENTICAL; else echo DIFFERS; diff <(printf '%s' "$OLD_POSTED") <(printf '%s' "$NEW_POSTED"); fi
SH
git show origin/main:commands/dark-factory-conformance.md > /tmp/df404_old_conformance.md
bash /tmp/df404_byte_check.sh /tmp/df404_old_conformance.md commands/dark-factory-conformance.md
rm -f /tmp/df404_byte_check.sh /tmp/df404_old_conformance.md
```

  Expected output: `BYTE-IDENTICAL`. If it prints `DIFFERS`, fix the `SHADOW_BODY` trim. The
  usual cause is a leftover trailing blank line or a missing closing fence. Do not change the
  heading text to make the check pass.

- [ ] **Step 5: Run the conformance-command tests, including the #406 pin.**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_conformance_command_shadow_review.py tests/test_shadow_model_resolution.py tests/test_command_issue_context_contract.py -v`

  Expected: all pass, with `test_conformance_command_shadow_review.py` at `8 passed`.
  `tests/test_shadow_model_resolution.py` passes **without edits**. It pins the five
  re-resolution snippets and their guards, which this edit does not touch.

- [ ] **Step 6: Confirm the moved-line anchors used in Task 3.**

  Run: `grep -n 'write_memory_entry "\|emit_verdict "conformance" "BLOCKED"\|^## Phase 4\|^## Phase 5\|^Exit `0`\|^5\. Exit non-zero\|does not reliably surface\|SHADOW_MODEL_PIN="\${CONFORMANCE' commands/dark-factory-conformance.md`

  Expected (796-line file):
  ```
  78:    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
  581:## Phase 4: PASS — Write attestation
  596:SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
  652:    write_memory_entry "$TARGET" "$PATH_PREFIX" "$VIOLATION_TEXT" conformance "${ISSUE_NUM:-unknown}"
  670:Exit `0`. The `push-and-pr` and `report` nodes will proceed normally.
  672:## Phase 5: BLOCKED — Material divergence unresolved
  684:   SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
  733:     emit_verdict "conformance" "BLOCKED" "${MATERIAL_COUNT:-0}" "critical"
  740:   SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
  750:5. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
  754:   internal `exit 1` does not reliably surface as node failure to the DAG executor (#212, #271).
  ```
  If any number differs (main moved), use the observed numbers in Task 3 instead of the
  plan's table.

- [ ] **Step 7: Commit.**

```bash
git add tests/test_conformance_command_shadow_review.py commands/dark-factory-conformance.md
git commit -m "refactor(#404): conformance Phase 4 posts the shadow comment via post_marker_comment.sh

Replaces the inlined FOOTER/mktemp/printf/tracker-comment/rm block with
post_marker_comment.sh ... factory || true. Posted body is byte-identical
(verified), marker and update-in-place unchanged, || true kept (da7aba1 R3)."
```

---

## Task 3: Docs sync (default-footer sentence and moved line citations)

**Files:**
- Modify: `docs/run-lifecycle.md`
- Modify: `docs/features.md`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Re-grep the citation set (spec Requirement 7).**

  Run: `git grep -n "dark-factory-conformance.md:\|post_marker_comment.sh:" -- docs README.md ':!docs/archive' ':!docs/superpowers'`

  That grep does not see bare citations (`` `:NNN` `` with the file named earlier in the
  paragraph), so also list the bare ones in the conformance walkthrough of
  `docs/run-lifecycle.md` (the numbered list and Result table before the
  `**The `conformance-gate` node enforces the result**` paragraph):

  Run: ``awk '/^\*\*D2\. Conformance, Gate 2\*\*/,/conformance-gate` node enforces/' docs/run-lifecycle.md | grep -o -E '`:[0-9]+(-[0-9]+)?`'``

  (if the start pattern prints nothing, locate the walkthrough by hand: it is the numbered
  list ending in "8. **Artifact guard**" plus the PASS/BLOCKED Result table). Every bare
  citation there is a conformance-command line. On main at 2429106 the ones at or after `:625`
  are exactly `` `:754-794` `` (Phase 6), `` `:580-674` `` (PASS) and `` `:676-752` `` (BLOCKED);
  the rest are above the edit and do not move. The only other bare conformance-command
  citation in the docs is `` `:734` `` in `features.md:111`. Bare `` `:NNN` `` citations elsewhere
  in these docs cite the workflow, `entrypoint.sh` or `scheduler.sh`; leave them.

  Check the result against the Implementation-notes table above. Any citation of a
  conformance-command line at or after the old `:625` that is not in the table gets
  re-anchored the same way: find its content on `origin/main`, then find the new line in
  the working tree. Do not edit `docs/archive/**` or `docs/superpowers/**`. Those are
  historical records whose line numbers belonged to their own revision.

- [ ] **Step 2: `docs/run-lifecycle.md` default-footer sentence (`:688-690`).** Replace:

```markdown
The marker comments posted through `scripts/post_marker_comment.sh` use the scheduler
footer on purpose, so they do not reset the `direct-to-pr` grace clock
(`scripts/post_marker_comment.sh:16-20`).
```

  with:

```markdown
The marker comments posted through `scripts/post_marker_comment.sh` use the scheduler
footer by default (its optional fourth argument, `footer-kind`, selects another
`factory_core/identity.py` footer; the conformance PASS-path shadow comment passes
`factory`), so they do not reset the `direct-to-pr` grace clock, which only the
`refinement` footer moves (`scripts/post_marker_comment.sh:20-24`).
```

- [ ] **Step 3: `docs/run-lifecycle.md` conformance citations.**
  - `:814`/`:815`: change ``| PASS (`:580-674`) |`` to ``| PASS (`:581-670`) |`` and
    ``| BLOCKED (`:676-752`) |`` to ``| BLOCKED (`:672-754`) |``.
  - `:819`: change ``(`commands/dark-factory-conformance.md:748-752`)`` to
    ``(`commands/dark-factory-conformance.md:750-754`)``.
  - `:1319`: the same `:748-752` → `:750-754` change.
  - `:794`: change ``(Phase 6, `:754-794`)`` to ``(Phase 6, `:756-796`)`` (Phase 6 heading
    through the last line of the command file).

  Command (both `:748-752` occurrences are the same step-5 citation):
  `sed -i 's/(`:580-674`)/(`:581-670`)/; s/(`:676-752`)/(`:672-754`)/; s/(Phase 6, `:754-794`)/(Phase 6, `:756-796`)/; s/dark-factory-conformance\.md:748-752/dark-factory-conformance.md:750-754/g' docs/run-lifecycle.md`

- [ ] **Step 4: `docs/features.md`.**
  - `:134`: change ``from `dark-factory-conformance.md:656` `` to
    ``from `dark-factory-conformance.md:652` `` (the `write_memory_entry` call).
  - `:111`: change ``used at `dark-factory-conformance.md:586` and `:734` `` to
    ``used at `dark-factory-conformance.md:586` and `:733` `` (the BLOCKED `emit_verdict`;
    `:586` is above the edit and is not changed by this ticket).

  Command: `sed -i 's/dark-factory-conformance\.md:656`/dark-factory-conformance.md:652`/; s/dark-factory-conformance\.md:586` and `:734`/dark-factory-conformance.md:586` and `:733`/' docs/features.md`

- [ ] **Step 5: `docs/configuration.md:148`.** Change
  ``re-resolved inline before each shadow guard at `commands/dark-factory-conformance.md:594-596,690-692,746-748` ``
  to ``…`commands/dark-factory-conformance.md:594-596,682-684,738-740` ``.

  Command: `sed -i 's/dark-factory-conformance\.md:594-596,690-692,746-748/dark-factory-conformance.md:594-596,682-684,738-740/' docs/configuration.md`

- [ ] **Step 6: Verify every re-anchored citation lands on its content (AC5).**

```bash
F=commands/dark-factory-conformance.md
sed -n '581p;670p' $F          # ## Phase 4: PASS … / Exit `0`.
sed -n '672p;754p' $F          # ## Phase 5: BLOCKED … / … does not reliably surface …
sed -n '750,754p' $F           # step 5 "Exit non-zero … does not reliably surface"
sed -n '652p' $F               # write_memory_entry "$TARGET" …
sed -n '733p' $F               # emit_verdict "conformance" "BLOCKED" …
sed -n '682,684p;738,740p' $F  # ROOT= / SHADOW_MODEL_DEFAULT= / SHADOW_MODEL_PIN= (x2)
sed -n '20,24p' scripts/post_marker_comment.sh   # default-footer / #358 paragraph
sed -n '756p' $F; wc -l < $F   # ## Phase 6: ARTIFACT GUARD / 796
git diff -U0 -- docs/ | grep '^[-+][^-+]'
git grep -n -F -e '748-752' -e '580-674' -e '676-752' -e '754-794' -e 'conformance.md:656' -e '`:734`' -e '690-692,746-748' -e 'post_marker_comment.sh:16-20' -- docs/run-lifecycle.md docs/features.md docs/configuration.md
```

  Expected: each `sed` prints the content named in its comment. The `git diff` shows only the
  lines changed in Steps 2-5 (9 doc lines removed: 3 for the Step 2 sentence, the `:794`,
  `:814`, `:815`, `:819`, `:1319` lines of `run-lifecycle.md` as numbered on main at 2429106,
  `features.md:111`, `:134`, `configuration.md:148`). The final `git grep` prints nothing: no
  old citation is left.

- [ ] **Step 7: Run the docs reference check (CI step).**

  Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`

  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 8: Commit.**

```bash
git add docs/run-lifecycle.md docs/features.md docs/configuration.md
git commit -m "docs(#404): footer-kind default and re-anchored conformance line citations

run-lifecycle: post_marker_comment.sh footer is the default, not fixed;
citations below the shrunk Phase 4 block re-anchored by content."
```

---

## Task 4: Final verification

**Files:** none modified.

- [ ] **Step 1: Full test suite.**

  Run: `PYTHONPATH=scripts python -m pytest tests/ -v`

  Expected: no failures. In particular `tests/test_post_marker_comment.py` (7 passed),
  `tests/test_conformance_command_shadow_review.py` (8 passed),
  `tests/test_shadow_model_resolution.py`, `tests/test_push_gate_dag.py`,
  `tests/test_push_gate_dag_exec.py` and `tests/test_ci_covers_shell_tests.py` all pass,
  and none of the last four is modified (AC4).

- [ ] **Step 2: Protected surfaces untouched (AC2 and hard limits).**

  Run: `git diff origin/main --stat -- workflows/ scripts/blast_gate_check.sh .github/ scripts/factory_core/ deploy/ scripts/verdict_gate_check.sh config/`

  Expected: empty output.

- [ ] **Step 3: Scope check.**

  Run: `git diff origin/main --name-only`

  Expected: exactly the 7 files in the File Structure table. The refine-phase artifacts
  already on this branch may also appear (this ticket's spec under `docs/superpowers/specs/`,
  `.archon/memory/architecture.md`), as may this plan file. Nothing else.

- [ ] **Step 4: Tests unedited (AC4).**

  Run: `git diff origin/main --stat -- tests/test_shadow_model_resolution.py tests/test_push_gate_dag.py tests/test_push_gate_dag_exec.py tests/test_ci_covers_shell_tests.py`

  Expected: empty output.

- [ ] **Step 5: Only the Phase 4 test function changed.**

  Run: `git diff origin/main -U0 -- tests/test_conformance_command_shadow_review.py | grep '^@@'`

  Expected: every hunk header falls inside
  `test_phase_4_pass_emits_shadow_fields_and_marker_comment` (today `:39-47`). The other 7
  tests are byte-identical.

No commit in this task. If any step fails, fix the file in a follow-up commit (do not use
`git commit --amend`), then re-run this task.

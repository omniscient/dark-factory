# Implementation Plan: Code Review Gate — fix diff_rank.py over-summarization, the `|`-splitting parser bug, and add a zero-content abort

**Issue:** #403
**Revised:** 2026-09-14 (operator amendment after plan-gate review)
**Spec:** `docs/superpowers/specs/2026-09-14-code-review-diff-rank-empty-artifact-fix-design.md`

---

## Goal

On PR #398 (Gate 3), `commands/dark-factory-code-review.md` ranked a 7-file/~2k-token diff
against a 6k-token cap and produced a `review_diff.txt` that was **seven `[SUMMARIZED]` notices
and zero lines of code** — a diff that fit three times over its budget was summarized to
nothing, and the code-review subagent would have rubber-stamped an empty review. Two
compounding root causes plus one independent bug, all fixed here:

1. **R1/R2** — `scripts/diff_rank.py::build_ranked_diff()` unconditionally summarizes every
   `low`-tier file with no budget check at all, even when the whole diff comfortably fits the
   cap. This is the actual mechanism that zeroed PR #398's diff. Fix: skip all tier-based
   summarization when the whole diff fits the cap (emit everything in full, original order);
   above the cap, budget-check `low` the same way `high`/`medium` already are, processed last
   so it is the first tier squeezed out.
2. **R3** — `commands/dark-factory-code-review.md` never passes `--spec-file` to
   `diff_rank.py` (unlike the conformance command), so no file is ever protected by the
   `spec_named` high-tier signal in the still-over-cap case.
3. **R4** — `scripts/code_review_payload.py::parse_findings()` splits a finding's remainder on
   every literal `|` with no bound, so a description containing `|| true` gets corrupted into
   `|  | true` on rejoin.
4. **R5** — even after R1–R3, a genuinely oversized diff with no spec-named files can still
   ranked-summarize to zero content. Add a fail-closed zero-content predicate and wire the
   code-review command to abort (BLOCKED + `needs-discussion`, reusing the existing block
   machinery) instead of silently reviewing nothing.

No new services, schemas, or dependencies — all five fixes are localized edits to two Python
scripts and one command markdown file, plus tests.

## Architecture

```
scripts/diff_rank.py
  build_ranked_diff()
    + whole-diff-vs-cap passthrough branch (R1/R2), inserted right after per-file
      classification and tier bucketing, before the existing sort/budget logic —
      emits every file "full" in original diff order, sets under_cap_passthrough: true
    ~ the over-cap path's low-tier loop: unconditional _summary_low() call
      -> budget-checked (same pattern as the existing high/medium loops) (R1)
  + has_reviewable_content(text) -> bool                              (R5)
  + _check_nonempty_main(path) -> int                                 (R5)
  ~ parse_args(): --diff/--artifacts-dir become optional; + --check-nonempty PATH
  ~ main(): dispatches to _check_nonempty_main() when --check-nonempty is given,
    else validates --diff/--artifacts-dir are both present before ranking

scripts/code_review_payload.py
  parse_findings()
    ~ m.group(2).split("|")            -> m.group(2).split("|", 2)     (R4)
    ~ drop the lossy " | ".join(fields[2:]) rejoin — fields[2] is already
      the full untouched description under maxsplit=2

commands/dark-factory-code-review.md
  Phase 1  + step 10: resolve $SPEC_FILE (push_gate_check.sh primary,
             conformance's 2a/2b as fallback, no NO_SPEC escalation)        (R3)
  Phase 2  ~ diff_rank.py invocation gains ${SPEC_FILE:+--spec-file "$SPEC_FILE"} (R3)
           ~ the prose-only empty-file bullet becomes a real bash `[ ! -s ]` guard,
             followed by a `--check-nonempty` guard that sets ZERO_CONTENT=true
             on any non-zero exit                                          (R5)
  Phase 6  + third branch `### If ZERO_CONTENT=true`: zero-content abort —
             reuses the existing BLOCKED machinery (issue comment, tracker
             set-status blocked, needs-discussion label, emit_verdict), exempt
             from code_review.fail_open, skips Phases 3-5 and the BLOCKED
             branch's step 5 memory write                                  (R5)
```

## Tech Stack

Pure stdlib Python (`argparse`, `re`, `pathlib`) and Bash — matches every other
`scripts/*.py` module and `commands/*.md` file in this repo; no new dependency. Tests use
`pytest` + the existing in-process `run_main()` harness in `tests/test_diff_rank.py` and direct
function calls / `subprocess` in `tests/test_code_review_payload.py` and
`tests/test_code_review_command.py` (the established pattern for command-file text-pin
coverage, since an LLM-executed `.md` command can't be exercised at runtime by a test).

## File Structure

| File | Change |
|---|---|
| `scripts/diff_rank.py` | **Modified** — R1/R2 whole-diff passthrough branch; low-tier budget check over cap; R5 `has_reviewable_content`/`_check_nonempty_main`/`--check-nonempty` |
| `scripts/code_review_payload.py` | **Modified** — R4 bounded (`maxsplit=2`) pipe-split parser fix; docstring update |
| `commands/dark-factory-code-review.md` | **Modified** — R3 `$SPEC_FILE` resolution + `--spec-file` flag; R5 zero-content abort (third Phase 6 branch `### If ZERO_CONTENT=true`) |
| `tests/test_diff_rank.py` | **Modified** — new/retargeted tests for R1/R2/R5 |
| `tests/test_code_review_payload.py` | **Modified** — new regression tests for R4 |
| `tests/test_code_review_command.py` | **Modified** — new text-pin tests for R3/R5 |
| `tests/test_verdict.py` | **Modified** — golden-fixture count pin bumped 18 → 19 |
| `tests/fixtures/verdicts/review__blocked_zero_content.md`, `.expected.json` | **New** — golden fixture for R5's zero-content BLOCKED shape |

Not touched (out of scope per the spec): `scripts/context_budget.py` (already reads
`raw_diff_tokens` from `diff-ranking.json`, unaffected by R1/R2's accounting rule),
`commands/dark-factory-conformance.md` (its `diff_rank.py` call already passes `--spec-file`
and inherits R1/R2 by construction, since both live in `build_ranked_diff()`).

## Memory context reviewed

`scripts/load_memory_context.sh plan` returned three entries (`codebase-patterns.md`,
`architecture.md`), all scoped to spec-archiving conventions and prior memory-backend spikes —
none is a `[AVOID]`/`[FIX]` entry applicable to `diff_rank.py`, `code_review_payload.py`, or
the code-review command. No task below needed to bake in a memory lesson.

---

## Task 1: `diff_rank.py` — never summarize when the whole diff fits the cap (R1/R2)

**Files:** `scripts/diff_rank.py`, `tests/test_diff_rank.py`

### TDD Steps

1. Add the failing tests to `tests/test_diff_rank.py`, appended after
   `test_diff_ranking_json_critical_token_accounting` (lines 388-398 on origin/main, before the
   "Fail-open: missing --hotspots file" section):

```python
# ---------------------------------------------------------------------------
# R1/R2: whole-diff-vs-cap passthrough (#403)
# ---------------------------------------------------------------------------

def test_seven_file_diff_under_cap_not_summarized():
    """R1 acceptance test (from the issue): PR #398's failure mode was a
    7-file diff (~2,082 tokens) summarized to zero reviewable content under a
    6,000-token cap. Reproduce that shape and assert nothing gets summarized."""
    diff = "".join(
        make_diff(f"backend/app/services/module_{i}.py", added=30, removed=10, n_hunks=1)
        for i in range(7)
    )
    output, ranking = run_main(diff, token_cap=6000)
    assert "[SUMMARIZED" not in output
    assert len(ranking["files"]) == 7
    assert ranking["raw_diff_tokens"] < 6000
    assert all(f["included"] == "full" for f in ranking["files"])


def test_low_files_verbatim_when_whole_diff_fits_cap():
    """When the whole diff fits under the cap, a low-tier file passes through
    in full rather than being summarized (R1)."""
    diff = make_diff("tests/test_scanner.py", added=5, removed=2, n_hunks=1)
    output, ranking = run_main(diff, token_cap=6000)
    entry = ranking["files"][0]
    assert entry["included"] == "full"
    assert entry["risk_class"] == "low"
    assert "[SUMMARIZED" not in output


def test_under_cap_passthrough_marker_and_full_records():
    """R2: the passthrough marks itself in diff-ranking.json and every file
    record is 'included: full' with its real token count."""
    diff = (
        make_diff("tests/test_scanner.py", added=5, removed=2)
        + make_diff("backend/app/services/utils.py", added=10, removed=5)
    )
    _, ranking = run_main(diff, token_cap=6000)
    assert ranking["under_cap_passthrough"] is True
    for f in ranking["files"]:
        assert f["included"] == "full"
        assert f["estimated_tokens"] > 0


def test_passthrough_per_file_with_header_first_drops_pretriage_annotation():
    """R1/R2: passthrough output is built per-file (not raw diff_text), so a
    leading [Pre-triage] annotation (already stripped by parse_diff_files) never
    resurfaces, and the '# [diff-rank: ...]' header is still the first line."""
    annotation = "[Pre-triage] hunk-filter applied: 1 files / 1 hunks retained\n"
    diff = annotation + make_diff("backend/app/services/utils.py", added=5, removed=2)
    output, ranking = run_main(diff, token_cap=6000)
    lines = output.splitlines()
    assert lines[0].startswith("# [diff-rank:")
    assert "[Pre-triage]" not in output
    assert ranking["under_cap_passthrough"] is True
```

2. Run: `python -m pytest tests/test_diff_rank.py -k "passthrough or verbatim_when_whole_diff or seven_file" -v`
   → all four fail. `test_seven_file_diff_under_cap_not_summarized` and
   `test_low_files_verbatim_when_whole_diff_fits_cap` fail because the current code always
   summarizes the `low` tier unconditionally (`entry["included"] == "summary"`, not `"full"`,
   and `[SUMMARIZED` appears in stdout). The other two fail with `KeyError:
   'under_cap_passthrough'` (the marker doesn't exist yet).

3. In `scripts/diff_rank.py`, insert the passthrough branch immediately after the tier-bucket
   list comprehensions and before the existing `critical.sort(...)` call. Replace:

```python
    # Bucket by tier
    critical = [c for c in classified if c["tier"] == "critical"]
    high = [c for c in classified if c["tier"] == "high"]
    medium = [c for c in classified if c["tier"] == "medium"]
    low = [c for c in classified if c["tier"] == "low"]

    # Sort critical: blast_score desc, then lines desc
    critical.sort(key=lambda c: (-(c["blast_score"] or 0), -(c["file"]["added"] + c["file"]["removed"])))
```

   with:

```python
    # Bucket by tier
    critical = [c for c in classified if c["tier"] == "critical"]
    high = [c for c in classified if c["tier"] == "high"]
    medium = [c for c in classified if c["tier"] == "medium"]
    low = [c for c in classified if c["tier"] == "low"]

    # R1: never summarize when the whole diff fits the cap — emit every file in
    # full, in original file order (not tier-bucketed order). Classification above
    # still ran unconditionally, so diff-ranking.json stays complete (R2).
    if raw_diff_tokens <= token_cap:
        output_parts = []
        file_records = []
        critical_tokens = 0
        residual_tokens = 0
        for c in classified:
            text = "".join(c["file"]["lines"])
            tokens = estimate_tokens(text)
            if c["tier"] == "critical":
                critical_tokens += tokens
            else:
                residual_tokens += tokens
            output_parts.append(text)
            file_records.append({
                "path": c["file"]["path"],
                "risk_class": c["tier"],
                "signals": c["signals"],
                "blast_score": c["blast_score"],
                "lines_added": c["file"]["added"],
                "lines_removed": c["file"]["removed"],
                "hunk_count": c["file"]["hunks"],
                "included": "full",
                "estimated_tokens": tokens,
            })

        total_tokens = critical_tokens + residual_tokens
        header = (
            f"# [diff-rank: {len(files)} files — "
            f"{len(critical)} critical / {len(high)} high / "
            f"{len(medium)} medium / {len(low)} low, "
            f"est. {total_tokens} tokens (cap {token_cap})]\n"
        )
        ranked_diff = header + "".join(output_parts)
        ranking_info = {
            "token_cap": token_cap,
            "estimated_tokens_emitted": total_tokens,
            "critical_tokens": critical_tokens,
            "residual_tokens": residual_tokens,
            "raw_diff_tokens": raw_diff_tokens,
            "under_cap_passthrough": True,
            "files": file_records,
        }
        return ranked_diff, ranking_info

    # Sort critical: blast_score desc, then lines desc
    critical.sort(key=lambda c: (-(c["blast_score"] or 0), -(c["file"]["added"] + c["file"]["removed"])))
```

4. Run: `python -m pytest tests/test_diff_rank.py -k "passthrough or verbatim_when_whole_diff or seven_file" -v`
   → all four pass.

5. Run: `python -m pytest tests/test_diff_rank.py -v`. Expect new failures in
   `test_low_files_always_summarized_regardless_of_budget`,
   `test_summary_line_low_risk_format`,
   `test_summary_line_non_test_low_risk_not_labeled_test_only`, and
   `test_diff_ranking_json_file_entry_fields` — their default `token_cap=6000` now hits the
   new passthrough and stops summarizing. Retarget them immediately, in this same task (not
   deferred to Task 2), so the suite is green before this commit — Task 2's over-cap
   budget-check is a distinct, independently reviewable change and must not depend on this
   task leaving red tests behind.

   a. Rename `test_low_files_always_summarized_regardless_of_budget` to
      `test_low_files_summarized_when_diff_exceeds_cap` and shrink its `token_cap` so it is
      smaller than the low file itself (real budget pressure), not merely smaller than the
      whole diff — under R1's passthrough, a cap merely smaller than the whole diff no longer
      guarantees summarization once Task 2 also budget-checks `low` on the over-cap path.
      Replace:

```python
def test_low_files_always_summarized_regardless_of_budget():
    """Test file should always be summarized even with a huge cap."""
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    _, ranking = run_main(diff, token_cap=100000)
    entry = ranking["files"][0]
    assert entry["included"] == "summary"
    assert entry["risk_class"] == "low"
```

      with:

```python
def test_low_files_summarized_when_diff_exceeds_cap():
    """A low-tier file is summarized only when the remaining budget can't hold
    it — a cap smaller than the low file itself (not just smaller than the
    whole diff) exercises real budget pressure (R1)."""
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    _, ranking = run_main(diff, token_cap=5)
    entry = ranking["files"][0]
    assert entry["included"] == "summary"
    assert entry["risk_class"] == "low"
```

   b. Give `test_summary_line_low_risk_format`, `test_summary_line_non_test_low_risk_not_labeled_test_only`,
      and `test_diff_ranking_json_file_entry_fields` an explicit small `token_cap` so they keep
      exercising the summarization path (their small fixtures otherwise now fit comfortably
      under the default 6000 cap and hit the passthrough). Replace each `run_main(diff)` call:

```python
def test_summary_line_low_risk_format():
    # make_diff with n_hunks=2 creates 42 added lines per hunk (84 total) and 3 removed per hunk (6 total)
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    output, _ = run_main(diff, token_cap=5)
    assert "# [SUMMARIZED: low-risk test-only] tests/test_scanner.py — +84/-6 (2 hunks)" in output


def test_summary_line_non_test_low_risk_not_labeled_test_only():
    """A non-test file in the low tier is labeled 'low-risk', never 'test-only'
    (regression for the #669 code-review finding: non-test low files were being
    misreported as tests, which could reduce reviewer scrutiny)."""
    diff = make_diff("frontend/src/utils/format.ts", added=5, removed=2, n_hunks=1)
    output, ranking = run_main(diff, token_cap=5)
    entry = ranking["files"][0]
    assert entry["risk_class"] == "low"
    assert "test_file" not in entry["signals"]
    assert "# [SUMMARIZED: low-risk] frontend/src/utils/format.ts" in output
    assert "test-only" not in output
```

```python
def test_diff_ranking_json_file_entry_fields():
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    _, ranking = run_main(diff, token_cap=5)
    f = ranking["files"][0]
    assert f["path"] == "tests/test_scanner.py"
    assert f["risk_class"] == "low"
    assert f["signals"] == ["test_file"]
    # make_diff with n_hunks=2 creates added lines per hunk, so totals are added*n_hunks
    assert f["lines_added"] == 84  # 42 added lines × 2 hunks
    assert f["lines_removed"] == 6  # 3 removed lines × 2 hunks
    assert f["hunk_count"] == 2
    assert f["included"] == "summary"
    assert f["estimated_tokens"] == 0
```

   c. `test_high_files_fill_budget_then_summarize`, `test_summary_line_budget_exhausted_format`
      (already `token_cap=10`) and `test_diff_ranking_json_critical_token_accounting` (cap
      6000, the R2 accounting-rule pin — still correct under the passthrough since it only
      asserts `critical_tokens > 0` and `scanner_entry["included"] == "full"`) need no change.

6. Run: `python -m pytest tests/test_diff_rank.py -v` → all tests pass, no regressions. The
   suite is fully green at this commit.

7. Commit:
```bash
git add scripts/diff_rank.py tests/test_diff_rank.py
git commit -m "fix(diff_rank): never summarize when the whole diff fits the cap (#403 R1/R2)"
```

---

## Task 2: `diff_rank.py` — budget-check the low tier above the cap (R1)

**Files:** `scripts/diff_rank.py`, `tests/test_diff_rank.py`

### TDD Steps

1. Add the failing test to `tests/test_diff_rank.py`, appended after the tests added in
   Task 1:

```python
def test_low_file_full_when_budget_remains_over_cap():
    """R1: above the cap, low is budget-checked like high/medium — a small low
    file is emitted in full when enough budget remains after larger tiers."""
    diff = (
        make_diff("backend/app/routers/scanner.py", added=200, removed=100)
        + make_diff("tests/test_small.py", added=2, removed=1)
    )
    _, ranking = run_main(diff, token_cap=500)
    high_entry = next(f for f in ranking["files"] if "scanner.py" in f["path"])
    low_entry = next(f for f in ranking["files"] if "test_small.py" in f["path"])
    assert high_entry["included"] == "summary"
    assert low_entry["included"] == "full"
    assert low_entry["risk_class"] == "low"
```

2. Run: `python -m pytest tests/test_diff_rank.py::test_low_file_full_when_budget_remains_over_cap -v`
   → fails: `low_entry["included"] == "summary"` (the over-cap path still summarizes `low`
   unconditionally).

3. In `scripts/diff_rank.py`, replace the unconditional low-tier summarization loop:

```python
    # Summarize all low-tier files
    for c in low:
        _summary_low(c)
        file_records.append({
            "path": c["file"]["path"],
            "risk_class": "low",
            "signals": c["signals"],
            "blast_score": c["blast_score"],
            "lines_added": c["file"]["added"],
            "lines_removed": c["file"]["removed"],
            "hunk_count": c["file"]["hunks"],
            "included": "summary",
            "estimated_tokens": 0,
        })
```

   with the budget-checked version (same pattern as the `high`/`medium` loops above it):

```python
    # Fill remaining budget with low-tier files (processed last — first tier
    # squeezed out under budget pressure, per the issue's "summarize lowest-risk
    # first"; no longer unconditional — R1)
    for c in low:
        text = "".join(c["file"]["lines"])
        tokens = estimate_tokens(text)
        if budget >= tokens:
            t, included = _full(c, "low")
        else:
            t, included = _summary_low(c)
            tokens = t
        file_records.append({
            "path": c["file"]["path"],
            "risk_class": "low",
            "signals": c["signals"],
            "blast_score": c["blast_score"],
            "lines_added": c["file"]["added"],
            "lines_removed": c["file"]["removed"],
            "hunk_count": c["file"]["hunks"],
            "included": included,
            "estimated_tokens": tokens,
        })
```

4. Run: `python -m pytest tests/test_diff_rank.py::test_low_file_full_when_budget_remains_over_cap -v`
   → passes. This loop change cannot regress any test retargeted in Task 1: those tests use a
   cap smaller than the low file itself (still summarized either way), and no other test in
   the suite depends on "low always summarized while over-cap budget remains" — verified by
   running the previous step.

5. Run: `python -m pytest tests/test_diff_rank.py -v` → all tests pass, no regressions (the
   retargeting for R1's behavior change already happened in Task 1 step 5 — this task only
   adds the over-cap budget check and its own new test).

6. Commit:
```bash
git add scripts/diff_rank.py tests/test_diff_rank.py
git commit -m "fix(diff_rank): budget-check the low tier above the cap (#403 R1)"
```

---

## Task 3: `diff_rank.py` — zero-content predicate for `--check-nonempty` (R5)

**Files:** `scripts/diff_rank.py`, `tests/test_diff_rank.py`

### TDD Steps

1. No new imports. The tests below stay **in-process** — the same `patch("sys.argv", ...)`
   pattern `run_main()` already uses, plus the file's existing `pytest`, `patch`, and `Path`
   imports — so the module docstring's "All tests are in-process (no git/subprocess)"
   invariant stays true. (A `subprocess.run([sys.executable, diff_rank.py, ...])` variant was
   rejected at plan review: the child bypasses `tests/conftest.py`'s Windows `fcntl` stub and
   fails on the Windows host, and argparse's own `unrecognized arguments` exit `2` made the
   `>= 2` case pass vacuously before the implementation existed.)

2. Add the failing tests, appended at the end of `tests/test_diff_rank.py`:

```python
# ---------------------------------------------------------------------------
# R5: zero-content predicate (--check-nonempty)
# ---------------------------------------------------------------------------

def test_has_reviewable_content_true_for_payload_line():
    text = "# [diff-rank: 1 files — 0 critical / 1 high / 0 medium / 0 low, est. 5 tokens (cap 6000)]\n+added line\n"
    assert dr.has_reviewable_content(text) is True


def test_has_reviewable_content_false_for_all_summarized():
    text = (
        "# [diff-rank: 1 files — 0 critical / 0 high / 0 medium / 1 low, est. 0 tokens (cap 6000)]\n"
        "# [SUMMARIZED: low-risk] a.py — +1/-1 (1 hunks)\n"
    )
    assert dr.has_reviewable_content(text) is False


def test_has_reviewable_content_ignores_file_headers():
    text = "--- a/x.py\n+++ b/x.py\n"
    assert dr.has_reviewable_content(text) is False


def _run_check_nonempty(path):
    """Run `diff_rank.py --check-nonempty PATH` in-process (same sys.argv patching
    as run_main()); return the SystemExit code main() raised."""
    with patch("sys.argv", ["diff_rank.py", "--check-nonempty", str(path)]):
        with pytest.raises(SystemExit) as exc:
            dr.main()
    return exc.value.code


def test_check_nonempty_all_summarized_exits_1(tmp_path):
    review = tmp_path / "review_diff.txt"
    review.write_text(
        "# [diff-rank: 1 files — 0 critical / 0 high / 0 medium / 1 low, est. 0 tokens (cap 6000)]\n"
        "# [SUMMARIZED: low-risk] tests/test_x.py — +5/-2 (1 hunks)\n"
    )
    assert _run_check_nonempty(review) == 1


def test_check_nonempty_with_payload_line_exits_0(tmp_path):
    review = tmp_path / "review_diff.txt"
    review.write_text(
        "# [diff-rank: 1 files — 0 critical / 1 high / 0 medium / 0 low, est. 5 tokens (cap 6000)]\n"
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    assert _run_check_nonempty(review) == 0


def test_check_nonempty_missing_file_exits_ge_2(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.txt"
    code = _run_check_nonempty(missing)
    assert code >= 2
    # The helper-error path must announce itself on stderr (the command captures it into
    # the abort comment). This assertion is also what makes the test red before Task 3
    # lands: argparse's own missing/unrecognized-argument exit is already 2.
    assert "diff_rank --check-nonempty error" in capsys.readouterr().err
```

3. Run: `python -m pytest tests/test_diff_rank.py -k "has_reviewable_content or check_nonempty" -v`
   → all six fail. The three `has_reviewable_content` tests fail with `AttributeError: module
   'diff_rank' has no attribute 'has_reviewable_content'`. The three `--check-nonempty` tests
   fail because `parse_args()` still requires `--diff`/`--artifacts-dir`, so `main()` exits `2`
   through argparse: `exits_1` and `exits_0` see `2` instead of their expected code, and
   `exits_ge_2` passes its `>= 2` check but fails the stderr-marker assertion (argparse's usage
   error is not the helper's `diff_rank --check-nonempty error:` line).

4. In `scripts/diff_rank.py`, insert the new functions immediately after `build_ranked_diff()`'s
   `return ranked_diff, ranking_info` and before the `# CLI entry point` section comment.
   Replace:

```python
    return ranked_diff, ranking_info


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args():
```

   with:

```python
    return ranked_diff, ranking_info


# ---------------------------------------------------------------------------
# Zero-content predicate (R5) — used by the code-review command's post-ranking
# abort check, not by ranking itself.
# ---------------------------------------------------------------------------

def has_reviewable_content(text: str) -> bool:
    """Return True if text contains at least one diff payload line.

    A payload line starts with '+' or '-' and is not a '+++'/'---' file header.
    Neither a '# [SUMMARIZED: ...]' notice nor the '# [diff-rank: ...]' header
    ever matches this, so a ranked diff that is entirely summarized correctly
    reports no reviewable content.
    """
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            return True
    return False


def _check_nonempty_main(path: str) -> int:
    """Exit-code contract: 0 = content present, 1 = zero content, >=2 = helper error.

    The whole body is guarded so that *any* failure (unreadable file or an unexpected
    exception) reports as 2 — never as 1. The __main__ wrapper below maps stray
    exceptions to exit 1, which would mislabel a helper crash as "zero content".
    """
    try:
        text = Path(path).read_text(errors="replace")
        return 0 if has_reviewable_content(text) else 1
    except Exception as e:
        print(f"diff_rank --check-nonempty error: {e}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args():
```

5. In `parse_args()`, make `--diff`/`--artifacts-dir` optional and add `--check-nonempty`.
   Replace:

```python
def parse_args():
    p = argparse.ArgumentParser(description="Rank and chunk a unified diff by risk tier.")
    p.add_argument("--diff", required=True, help="Path to the input diff file")
    p.add_argument("--artifacts-dir", required=True, help="Directory to write diff-ranking.json")
    p.add_argument(
        "--config",
        default=".claude/skills/refinement/config.yaml",
        help="Path to refinement config yaml",
    )
    p.add_argument("--spec-file", default=None, help="Optional spec file to identify spec-named files")
    p.add_argument(
        "--hotspots",
        default="docs/codeindex-hotspots.md",
        help="Path to codeindex-hotspots.md",
    )
    p.add_argument(
        "--clone-dir",
        default=os.environ.get("CLONE_DIR", "."),
        help="Clone root for adapter.yaml lookup (default: $CLONE_DIR or '.')",
    )
    return p.parse_args()
```

   with:

```python
def parse_args():
    p = argparse.ArgumentParser(description="Rank and chunk a unified diff by risk tier.")
    p.add_argument("--diff", default=None, help="Path to the input diff file (required unless --check-nonempty is given)")
    p.add_argument("--artifacts-dir", default=None, help="Directory to write diff-ranking.json (required unless --check-nonempty is given)")
    p.add_argument(
        "--config",
        default=".claude/skills/refinement/config.yaml",
        help="Path to refinement config yaml",
    )
    p.add_argument("--spec-file", default=None, help="Optional spec file to identify spec-named files")
    p.add_argument(
        "--hotspots",
        default="docs/codeindex-hotspots.md",
        help="Path to codeindex-hotspots.md",
    )
    p.add_argument(
        "--clone-dir",
        default=os.environ.get("CLONE_DIR", "."),
        help="Clone root for adapter.yaml lookup (default: $CLONE_DIR or '.')",
    )
    p.add_argument(
        "--check-nonempty",
        default=None,
        metavar="PATH",
        help=(
            "Check whether PATH (a ranked review diff) contains at least one "
            "reviewable payload line; skips ranking entirely. Exit 0 if content is "
            "present, 1 if the file is all-[SUMMARIZED], >=2 on a helper error "
            "(unreadable file, bad arguments)."
        ),
    )
    return p.parse_args()
```

6. In `main()`, dispatch to the check-nonempty path before the existing ranking logic, and
   validate `--diff`/`--artifacts-dir` are present otherwise. Replace:

```python
def main():
    args = parse_args()

    diff_text = Path(args.diff).read_text(errors="replace")
    token_cap, score_floor, diff_enabled = load_config(args.config)
```

   with:

```python
def main():
    args = parse_args()

    if args.check_nonempty is not None:
        sys.exit(_check_nonempty_main(args.check_nonempty))

    if not args.diff or not args.artifacts_dir:
        print(
            "diff_rank error: --diff and --artifacts-dir are required unless "
            "--check-nonempty is given",
            file=sys.stderr,
        )
        sys.exit(2)

    diff_text = Path(args.diff).read_text(errors="replace")
    token_cap, score_floor, diff_enabled = load_config(args.config)
```

7. Update the module's top-of-file CLI docstring to document the new mode. Replace:

```python
CLI:
    python3 scripts/diff_rank.py \
      --diff <path>            \\
      --artifacts-dir <dir>    \\
      [--config <yaml>]        \\
      [--spec-file <path>]     \\
      [--hotspots <path>]

Writes the ranked diff string to stdout. Exits 0 on success; on any error
exits non-zero so the caller's '&&' falls back to the unranked diff.
"""
```

   with:

```python
CLI:
    python3 scripts/diff_rank.py \
      --diff <path>            \\
      --artifacts-dir <dir>    \\
      [--config <yaml>]        \\
      [--spec-file <path>]     \\
      [--hotspots <path>]

    python3 scripts/diff_rank.py --check-nonempty <ranked-diff-path>

Writes the ranked diff string to stdout. Exits 0 on success; on any error
exits non-zero so the caller's '&&' falls back to the unranked diff.
--check-nonempty skips ranking and instead checks an already-ranked diff file
for reviewable content (see has_reviewable_content()); exit 0 = content present,
1 = zero content, >=2 = helper error. Consumed by
commands/dark-factory-code-review.md's zero-content abort.
"""
```

8. Run: `python -m pytest tests/test_diff_rank.py -v` → all tests pass, including the six new
   ones and everything from Tasks 1–2.

9. Commit:
```bash
git add scripts/diff_rank.py tests/test_diff_rank.py
git commit -m "feat(diff_rank): add --check-nonempty zero-content predicate (#403 R5)"
```

---

## Task 4: `code_review_payload.py` — bound the finding-field split to `maxsplit=2` (R4)

**Files:** `scripts/code_review_payload.py`, `tests/test_code_review_payload.py`

### TDD Steps

1. Add the failing tests to `tests/test_code_review_payload.py`, appended after
   `test_parse_findings_line_zero_is_not_anchorable` (lines 55-58 on origin/main, before the `DIFF = """..."""`
   block):

```python
def test_parse_findings_description_with_double_pipe_shell_operator():
    """Regression (#403): a description containing '|| true' must survive
    byte-exact, not get corrupted into '|  | true' by an unbounded pipe split."""
    text = "- [medium] shell | commands/dark-factory-conformance.md:502 | uses `|| true` to absorb SIGPIPE"
    findings = crp.parse_findings(text)
    assert len(findings) == 1
    assert findings[0].description == "uses `|| true` to absorb SIGPIPE"


def test_parse_findings_description_with_markdown_table_fragment():
    """A description containing a markdown-table-like '|' sequence must also
    survive byte-exact (maxsplit=2 bounds the split to the first two pipes).
    The fixture deliberately uses non-canonical spacing around the pipes: an
    unbounded split + strip + ' | '.join silently re-canonicalizes it."""
    text = "- [low] docs | README.md:10 | table row looks like |col1|col2|"
    findings = crp.parse_findings(text)
    assert len(findings) == 1
    assert findings[0].description == "table row looks like |col1|col2|"
```

2. Run: `python -m pytest tests/test_code_review_payload.py -k "double_pipe or markdown_table" -v`
   → both fail. `test_parse_findings_description_with_double_pipe_shell_operator` asserts
   `description == "uses \`|| true\` to absorb SIGPIPE"` but gets
   `"uses \` |  | true\` to absorb SIGPIPE"` (the exact corruption the issue reported); the
   markdown-table test gets `"table row looks like | col1 | col2 |"` — the unbounded split
   strips every fragment and the `" | ".join` rejoin re-canonicalizes the spacing. (A fixture
   written with canonical `" | "` spacing would **not** be red: the rejoin reproduces it
   exactly and `description.strip()` at `scripts/code_review_payload.py:70` drops the trailing
   space — do not "tidy" the fixture back to that form.)

3. In `scripts/code_review_payload.py`, bound the split and drop the lossy rejoin. Replace:

```python
        severity = m.group(1).lower()
        fields = [p.strip() for p in m.group(2).split("|")]
        if len(fields) >= 3:
            category, loc, description = fields[0], fields[1], " | ".join(fields[2:])
        elif len(fields) == 2:
```

   with:

```python
        severity = m.group(1).lower()
        fields = [p.strip() for p in m.group(2).split("|", 2)]
        if len(fields) >= 3:
            category, loc, description = fields[0], fields[1], fields[2]
        elif len(fields) == 2:
```

4. Update the module docstring to note the description field may itself contain `|`. Replace:

```python
Finding wire format (one bullet per finding):
    - [severity] category | path:line | description
"""
```

   with:

```python
Finding wire format (one bullet per finding):
    - [severity] category | path:line | description

The description field is free text and may itself contain '|' (e.g. a shell
command like '|| true', or a markdown-table-like fragment) — parsing bounds the
split to the first two '|' so a literal pipe inside the description is never
treated as a field separator.
"""
```

5. Run: `python -m pytest tests/test_code_review_payload.py -v` → all tests pass, including the
   two new ones and every pre-existing test (the 2-field and 1-field branches are untouched).

6. Commit:
```bash
git add scripts/code_review_payload.py tests/test_code_review_payload.py
git commit -m "fix(code_review_payload): bound finding-field split to maxsplit=2 (#403 R4)"
```

---

## Task 5: `commands/dark-factory-code-review.md` — resolve and thread `$SPEC_FILE` (R3)

**Files:** `commands/dark-factory-code-review.md`, `tests/test_code_review_command.py`

### TDD Steps

1. Add the failing tests to `tests/test_code_review_command.py`, appended after
   `test_command_wires_the_contract`:

```python
def test_command_resolves_spec_file_via_push_gate_check():
    text = CMD.read_text(encoding="utf-8")
    assert 'push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM"' in text
    # resolved in Phase 1, before Phase 2 consumes it
    assert text.find("push_gate_check.sh") < text.find("## Phase 2")


def test_command_threads_spec_file_into_diff_rank():
    text = CMD.read_text(encoding="utf-8")
    assert '${SPEC_FILE:+--spec-file "$SPEC_FILE"}' in text
    diff_rank_pos = text.find("dark-factory/scripts/diff_rank.py")
    spec_flag_pos = text.find('${SPEC_FILE:+--spec-file "$SPEC_FILE"}')
    assert diff_rank_pos != -1 and spec_flag_pos != -1
    assert 0 < spec_flag_pos - diff_rank_pos < 400, \
        "the --spec-file flag must be part of the Phase 2 diff_rank.py invocation"
```

2. Run: `python -m pytest tests/test_code_review_command.py -k "spec_file" -v` → both fail:
   `push_gate_check.sh` is not named anywhere in the file, and the diff_rank.py invocation has
   no `--spec-file` flag.

3. In `commands/dark-factory-code-review.md`, add step 10 to Phase 1 (after step 9's `PR_NUM`
   resolution, before `## Phase 2: DIFF`). Replace:

```markdown
9. Determine `PR_NUM`:
   ```bash
   BRANCH=$(git branch --show-current)
   PR_NUM=$(gh pr list --repo "$FACTORY_REPO_SLUG" --head "$BRANCH" --json number --jq '.[0].number // empty')
   ```
   If `PR_NUM` is empty, write `STATUS: ERROR\nREASON: no PR found` to `$ARTIFACTS_DIR/review.md` and exit `0` (fail-open — never block the board on missing PR).

## Phase 2: DIFF
```

   with:

```markdown
9. Determine `PR_NUM`:
   ```bash
   BRANCH=$(git branch --show-current)
   PR_NUM=$(gh pr list --repo "$FACTORY_REPO_SLUG" --head "$BRANCH" --json number --jq '.[0].number // empty')
   ```
   If `PR_NUM` is empty, write `STATUS: ERROR\nREASON: no PR found` to `$ARTIFACTS_DIR/review.md` and exit `0` (fail-open — never block the board on missing PR).
10. Resolve `$SPEC_FILE` (R3) — primary lookup is the #390-sanctioned branch-scoped check
    already used elsewhere in the DAG for this same purpose:
    ```bash
    SPEC_FILE=$(bash dark-factory/scripts/push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM")  # TARGET-PATH
    ```
    If that prints nothing, fall back to `commands/dark-factory-conformance.md`'s 2a/2b lookups
    (its 2c sibling-spec scan is deliberately not reused here — #390 removed that same
    first-match scan from the workflow nodes):
    ```bash
    if [ -z "$SPEC_FILE" ]; then
      PLAN_COMMENT=$(gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json comments \
        | jq -r '[.comments[] | select(.body | test("Refinement Pipeline — Plan Generated"))] | last | .body // ""')
      SPEC_FILE=$(printf '%s' "$PLAN_COMMENT" \
        | grep -oP 'docs/superpowers/specs/[^\s\])"]+' | head -1)
    fi
    if [ -z "$SPEC_FILE" ]; then
      SPEC_FILE=$(grep '^SPEC_PATH:' "$ARTIFACTS_DIR/refinement-status.md" 2>/dev/null \
        | sed 's/^SPEC_PATH: //' | head -1)
    fi
    ```
    `SPEC_FILE` may still be empty after both fallbacks — that is fine and expected: unlike
    conformance, code-review's spec-file signal is advisory-only (it only promotes a file out
    of the `low` tier), so no `NO_SPEC` escalation applies here.

## Phase 2: DIFF
```

4. In Phase 2, add the `--spec-file` flag to the existing `diff_rank.py` invocation. Replace:

```markdown
python3 dark-factory/scripts/diff_rank.py \  # TARGET-PATH
  --diff "$RANK_IN" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --config ".claude/skills/refinement/config.yaml" \
  --hotspots "docs/codeindex-hotspots.md" \
```

   with:

```markdown
python3 dark-factory/scripts/diff_rank.py \  # TARGET-PATH
  --diff "$RANK_IN" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --config ".claude/skills/refinement/config.yaml" \
  ${SPEC_FILE:+--spec-file "$SPEC_FILE"} \
  --hotspots "docs/codeindex-hotspots.md" \
```

5. Run: `python -m pytest tests/test_code_review_command.py -v` → all tests pass.

6. Commit:
```bash
git add commands/dark-factory-code-review.md tests/test_code_review_command.py
git commit -m "feat(code-review-command): resolve and thread --spec-file into diff_rank.py (#403 R3)"
```

---

## Task 6: `commands/dark-factory-code-review.md` — zero-content abort (R5)

**Files:** `commands/dark-factory-code-review.md`, `tests/test_code_review_command.py`

### TDD Steps

1. Add the failing test to `tests/test_code_review_command.py`, appended after
   `test_command_threads_spec_file_into_diff_rank`:

```python
def test_command_zero_content_abort_exempt_from_fail_open():
    text = CMD.read_text(encoding="utf-8")
    # Not scoped: --check-nonempty is a brand-new flag introduced by this task, so it
    # can't be vacuously true against the unmodified file.
    assert "--check-nonempty" in text
    phase6 = text.find("## Phase 6")
    start = text.find("### If `ZERO_CONTENT=true`")
    assert phase6 != -1 and start > phase6, "zero-content branch must be a Phase 6 sub-branch"
    nxt = text.find("\n### ", start + 1)
    zero_content_section = text[start:] if nxt == -1 else text[start:nxt]
    # These three strings already exist in Phase 6's pre-existing BLOCKED branch, so they
    # must be checked scoped to the new third branch — an unscoped assertion would pass
    # even if the branch were never added at all.
    assert 'emit_verdict "code-review" "BLOCKED"' in zero_content_section
    assert "needs-discussion" in zero_content_section
    # the fail_open exemption must be stated explicitly (R5), not left implicit
    assert "fail_open" in zero_content_section and "exempt" in zero_content_section.lower()
    # the abort branch must not actually read the (nonexistent, no-subagent-ran) findings
    # file — the branch's own prose explains *why* it doesn't via the bare filename "review_
    # findings.md", so check for the operative cat-with-full-path construct instead, which
    # only appears where the file is actually read (the normal BLOCKED branch).
    assert 'cat "$ARTIFACTS_DIR/review_findings.md"' not in zero_content_section
```

2. Run: `python -m pytest tests/test_code_review_command.py::test_command_zero_content_abort_exempt_from_fail_open -v`
   → fails: `--check-nonempty` is not present in the file yet, so the first `assert` fails.

3. In `commands/dark-factory-code-review.md`, replace the diff_rank.py invocation block's
   trailing prose bullet (the Phase 6 branch itself is added in step 4). Replace:

```markdown
- The `diff-ranking.json` artifact in `$ARTIFACTS_DIR` records the budget allocation and which files were summarized.
- If `$ARTIFACTS_DIR/review_diff.txt` is empty, write `STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0` to `$ARTIFACTS_DIR/review.md` and exit `0` (nothing to review).

## Phase 3: REVIEW
```

   with:

```markdown
- The `diff-ranking.json` artifact in `$ARTIFACTS_DIR` records the budget allocation and which files were summarized.
- Both guards below are real bash file tests (`-s`), not prose. Keep the `--check-nonempty`
  call on a single line with `# TARGET-PATH` at the end — a `\` before the comment is not a
  line continuation and would bind `CHECK_RC` to the wrong command.
  ```bash
  if [ ! -s "$ARTIFACTS_DIR/review_diff.txt" ]; then
    printf "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0\n" > "$ARTIFACTS_DIR/review.md"
    exit 0
  fi

  python3 dark-factory/scripts/diff_rank.py --check-nonempty "$ARTIFACTS_DIR/review_diff.txt" 2>/tmp/check_nonempty_err.txt  # TARGET-PATH
  CHECK_RC=$?
  if [ "$CHECK_RC" -ne 0 ]; then
    CHECK_ERR=$(cat /tmp/check_nonempty_err.txt)
    ZERO_CONTENT=true
    echo "code-review: review_diff.txt has no reviewable content (helper exit $CHECK_RC) — aborting, see Phase 6"
  fi
  ```
  If the `[ ! -s ... ]` guard fired above (`review_diff.txt` was empty), `review.md` is already
  written with `STATUS: PASS` — this phase is complete. Do not continue to Phase 3, and do not
  fall through into the `--check-nonempty` check below it (empty and zero-content are two
  distinct, mutually exclusive outcomes, both terminal for this run).
- `diff_rank.py --check-nonempty` treats any non-zero exit (zero-content `1` or a helper
  error `>= 2`) identically — both route to Phase 6's `### If ZERO_CONTENT=true` branch. It
  never reads a helper crash as "has content" (fail-closed).
- If `ZERO_CONTENT=true`, skip Phases 3–5 and go directly to Phase 6's
  `### If ZERO_CONTENT=true` branch.

## Phase 3: REVIEW
```

4. Still in `commands/dark-factory-code-review.md`, add the third Phase 6 branch after the
   BLOCKED branch's step 6 (the last paragraph of the file), reusing that branch's machinery.
   Replace:

```markdown
6. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
   `review-gate` DAG node (`workflows/archon-dark-factory.yaml`), which reads this file's
   `STATUS:` line directly and blocks `status-in-review` on anything other than
   `PASS`/`SKIPPED`/`ERROR` — a `command:` node's internal `exit 1` does not reliably surface
   as node failure to the DAG executor (#212, #271).
```

   with:

```markdown
6. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
   `review-gate` DAG node (`workflows/archon-dark-factory.yaml`), which reads this file's
   `STATUS:` line directly and blocks `status-in-review` on anything other than
   `PASS`/`SKIPPED`/`ERROR` — a `command:` node's internal `exit 1` does not reliably surface
   as node failure to the DAG executor (#212, #271).

### If `ZERO_CONTENT=true` (zero-content abort, R5)

Runs only when Phase 2 set `ZERO_CONTENT=true`. This is input/precondition validation, not
reviewer-subagent failure — it fires before any subagent is spawned — so it is **exempt from
`code_review.fail_open`**: fail_open's documented contract
(`/opt/refinement-skills/VERIFIER-CONTRACT.md`) is scoped to a subagent that errored, timed
out, or returned unparseable output, and letting this case fall through to `STATUS: ERROR`
would let `scripts/verdict_gate_check.sh`'s `PASS|SKIPPED|ERROR) exit 0` arm pass a PR with
zero lines actually reviewed (and auto-merge it under `direct-to-pr`) — exactly the outcome
this fix exists to prevent. Phases 3–5 were skipped (no subagent ran, no payload to build,
nothing to post) and the BLOCKED branch's step 5 blocking-findings memory write is skipped
too (a tooling fault, not a code finding worth recording as a lesson).

1. Post a "Code Review — Blocked" comment on the issue:
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   gh issue comment "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --body "## Code Review — Blocked

   \`diff_rank.py\` summarized the entire diff to zero reviewable content — nothing was
   reviewed. See \`diff-ranking.json\` in this run's artifacts for the per-file breakdown.
   ${CHECK_ERR:+(helper error: \`${CHECK_ERR}\`)}

   ### Next Steps
   Re-run: \`docker compose --profile factory run --rm dark-factory \\\"Continue issue #${ISSUE_NUM}\\\"\`, or add \`needs-discussion\` if this is a false positive.

   ---
   ${FOOTER}"
   ```
2. Move the issue to **Blocked** on the project board:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
   ```
3. Add the `needs-discussion` label:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
   ```
4. Write to `$ARTIFACTS_DIR/review.md` — exactly this shape; no `cat review_findings.md`
   (no subagent ran, so that file does not exist):
   ```bash
   {
     emit_verdict "code-review" "BLOCKED" "0" "none"
     printf "BLOCKERS: 0\nADVISORY: 0\n"
     printf "REASON: diff_rank summarized the entire diff to zero reviewable content\n"
   } > "$ARTIFACTS_DIR/review.md"
   ```
5. Exit non-zero (`exit 1`) — kept for forward-compatibility only, mirroring the BLOCKED
   branch's step 6 note: the actual enforcement is the `review-gate` DAG node reading this
   file's `STATUS:` line directly.
```

5. Run: `python -m pytest tests/test_code_review_command.py -v` → all tests pass.

6. Commit:
```bash
git add commands/dark-factory-code-review.md tests/test_code_review_command.py
git commit -m "feat(code-review-command): abort on zero-content ranked diff (#403 R5)"
```

---

## Task 7: golden verdict fixture for the zero-content BLOCKED shape (R6)

**Files:** `tests/fixtures/verdicts/review__blocked_zero_content.md`,
`tests/fixtures/verdicts/review__blocked_zero_content.expected.json`, `tests/test_verdict.py`

### TDD Steps

1. Run: `python -m pytest tests/test_verdict.py::test_golden_corpus_byte_compat -v` → currently
   passes at 18 fixtures (baseline, confirms the pin before the change).

2. Create `tests/fixtures/verdicts/review__blocked_zero_content.md` with exactly the shape
   Task 6's Phase 6 `### If ZERO_CONTENT=true` step 4 writes:

```
STATUS: BLOCKED
GATE_TYPE: code-review
FINDINGS_COUNT: 0
SEVERITY: none
BLOCKERS: 0
ADVISORY: 0
REASON: diff_rank summarized the entire diff to zero reviewable content
```

3. Create `tests/fixtures/verdicts/review__blocked_zero_content.expected.json`:

```json
{"stage": "review", "verdict": "BLOCKED", "blockers": 0, "advisory": 0}
```

4. Bump the fixture-count pin in `tests/test_verdict.py` (line 92 on origin/main; the spec's
   `tests/test_verdict.py:93` cite is off by one). Replace:

```python
    assert len(md_files) == 18, "golden corpus fixture count changed unexpectedly"
```

   with:

```python
    assert len(md_files) == 19, "golden corpus fixture count changed unexpectedly"
```

5. Run: `python -m pytest tests/test_verdict.py -v` → passes, including
   `test_golden_corpus_byte_compat` at the new count of 19 and the new fixture's byte-compat
   round trip.

6. Commit:
```bash
git add tests/fixtures/verdicts/review__blocked_zero_content.md \
        tests/fixtures/verdicts/review__blocked_zero_content.expected.json \
        tests/test_verdict.py
git commit -m "test(verdict): add golden fixture for the zero-content BLOCKED review shape (#403)"
```

---

## Task 8: full verification pass

**Files:** none (verification only)

### Steps

1. Run the full test suite: `python -m pytest tests/ -v`
   Expected: every test passes, including all new/retargeted tests from Tasks 1–7 and every
   pre-existing test in `tests/test_diff_rank.py`, `tests/test_code_review_payload.py`,
   `tests/test_code_review_command.py`, `tests/test_code_review_command_render_prompt.py`,
   `tests/test_code_review_skill_files.py`, `tests/test_code_review_prompt.py`, and
   `tests/test_verdict.py`. Pay particular attention to
   `tests/test_context_budget.py::TestDiffRankFeatureDisabled::test_enabled_produces_ranking_header`
   (cap 9999, one-line diff — it now runs through Task 1's passthrough branch rather than the
   old per-tier path; it should still pass since the header stays the first line either way,
   per R2, but a failure here is the first place a passthrough header regression would show up
   outside `tests/test_diff_rank.py` itself).

2. Run the CI-parity smoke gate — `.github/workflows/ci.yml` runs `bash tests/test_smoke_gate.sh`
   (not bare `smoke_gate.sh`, which is meant to be *sourced* by `entrypoint.sh` and expects a
   MarketHawk-shaped clone with a `frontend/` directory and scheduler state — running it
   directly is not the CI-equivalent check):
   ```bash
   bash tests/test_smoke_gate.sh
   ```
   Expected: exits 0.

3. Run the workflow-DAG checks CI also runs, each against the DAG file itself (both scripts
   exit 2 with a `Usage:` message when called with no arguments — this plan doesn't touch the
   DAG, but the check still needs the file to lint):
   ```bash
   python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
   Expected: both exit 0 — this plan touches no DAG node definitions or `when:` conditions, so
   neither check should report a change.

4. Confirm no out-of-scope files were touched: `git diff origin/main...HEAD --stat` (three-dot,
   merge-base form — per the `codebase-patterns.md` #266 memory entry and the header comment in
   `scripts/push_gate_check.sh`, changed-file-*set* detection must use three-dot: two-dot
   `origin/main HEAD` also lists files `main` changed independently after this branch forked,
   which would make the expectation below fail spuriously. The #250 two-dot entry is scoped to
   single-file content-equality checks, not scope detection).
   Expected: only the nine files listed in **File Structure** above (eight rows, but the last
   row names two files — the fixture `.md` and its `.expected.json`) plus this plan document
   and the spec, both already committed by the refine/plan phases — no other paths.

5. No commit for this task — it is verification-only. If any step above fails, return to the
   task that owns the affected file and fix it there (do not patch symptoms in Task 8).

---

## Self-review checklist

- [x] `**Issue:** #403` line present directly under the title, before Goal/Architecture/Tech Stack.
- [x] File Structure table present.
- [x] Every task has: Files list, TDD steps (failing test → verify fail → implement → verify
      pass → commit).
- [x] No placeholders — every step has an exact code block and exact file path; no "TBD",
      "similar to Task N", or unfilled sections.
- [x] Exact commands given for every verification step, with expected outcomes described.
- [x] Memory context (Phase 1) reviewed; no applicable `[AVOID]`/`[FIX]` entries found for this
      changeset, recorded above under "Memory context reviewed".

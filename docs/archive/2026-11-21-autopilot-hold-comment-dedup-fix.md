# Fix: a Failed HOLD Comment Becomes a Permanent Dedup Baseline — Implementation Plan

**Issue:** #547

**Spec:** `docs/superpowers/specs/2026-11-21-autopilot-hold-comment-dedup-fix-design.md`

**Revised:** 2026-11-21 (operator amendment after plan gate review)

**Goal:** Stop `run_once`'s HOLD branch from storing the `input_digest` dedup baseline when
the HOLD comment was never posted. `LiveIO.comment` reports `gh`'s exit status as a `bool`
and prints one stderr line on failure. The HOLD branch stores the digest only when the
comment posted (`True`, or `None` from any IO that does not report) or was skipped because
the stored HOLD was already unchanged. `docs/scheduler.md` states both "never a baseline"
rules. Every `epic_autopilot.py` line citation the edit moves is re-mapped from grep, not
from an assumed offset.

**Architecture:** Two edits inside `scripts/factory_core/epic_autopilot.py`:
(1) `LiveIO.comment` becomes `-> bool`. It keeps `check=False`, returns
`result.returncode == 0` and prints `autopilot: comment on #<n> failed (gh exit <rc>)` to
stderr when the exit is non-zero. A new `import sys  # noqa: E402` sits beside the other
Live IO imports.
(2) The HOLD branch becomes
`posted = unchanged or io.comment(...) is not False`, then
`record_verdict(..., input_digest=input_digest if posted else None)`. Short-circuit `or`
keeps "no comment call when unchanged". `is not False` keeps `FakeIO` (returns `None`) and
every existing test behaving exactly as today. The ADVANCE branch, epic promotion, daily
cap and every gate are untouched.

**Tech Stack:** Python 3 stdlib (`subprocess`, `sys`), pytest (`monkeypatch`, `capsys`),
`scripts/docs_refs_check.py`.

---

## Read this first

This whole change was prototyped in a scratch worktree of this refine branch
(`3141350`, which carries `main`@`d7e9421`'s code unchanged). The prototype was never
committed. Measured results:

- Both new tests **fail** on the unmodified code
  (`assert 'input_digest' not in {... 'input_digest': '81245e6a0ed60f16'}` and
  `assert None is False`), and **pass** after the code edits:
  `tests/test_epic_autopilot.py` → `65 passed` (63 existing + 2 new).
- Full suite after all edits: `PYTHONPATH=scripts python -m pytest tests/ -q` →
  `2796 passed`.
- `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  → `Docs reference check passed for 4 doc(s).`

**Line-shift facts the prototype measured** (they correct one expectation in the spec).
The spec's Requirement 7 says citations *before* `LiveIO.comment` "are not expected to
move", though they must still be grep-confirmed. They **do** move, because the edit
touches code above them:

| Edit | Old lines | Net delta | Everything after it shifts by |
|---|---|---|---|
| HOLD branch (`run_once`) | 320-323 (4 lines) → 7 lines | +3 | +3 from old line 324 |
| `import sys  # noqa: E402` after `import subprocess` | after old 332 | +1 | +4 from old line 333 |
| `LiveIO.comment` body | 609 (1 line) → 6 lines | +5 | +9 from old line 610 |

Citations at `:60` (`hard_excluded`), `:188-189`, `:194` and `:257` (`run_once`) sit above
line 320 and do not move. Every other `epic_autopilot.py` citation in
`docs/configuration.md` and `docs/scheduler.md` moves. Task 4 re-derives each one by
grepping its textual anchor in the edited file. The expected values in Task 4's table are
the ones the prototype's grep produced. If your grep disagrees, the grep wins: your diff
shape differs from the one below, so re-derive.

`docs/scheduler.md` is cited by line number nowhere outside `docs/archive/` and
`docs/superpowers/`, so the +3 lines the Hold-bullet rewrite adds to `docs/scheduler.md`
itself need no re-map (checked with `grep -rn "scheduler\.md:[0-9]"`).

**Environment note:** if `python -m pytest` reports `No module named pytest`, run
`pip install --user pytest pyyaml aiohttp` first (the CI step at
`.github/workflows/ci.yml:12`).

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/epic_autopilot.py` | `import sys`; `LiveIO.comment` returns `bool` and logs failure to stderr; HOLD branch stores `input_digest` only when `posted` |
| `tests/test_epic_autopilot.py` | New `#547` block after `test_hold_dedup_does_not_change_selection`: `LATEST` constant, `FailingCommentIO(FakeIO)`, `test_live_comment_reports_gh_exit`, `test_failed_hold_comment_is_not_a_baseline` |
| `docs/scheduler.md` | Step 6 "Hold" bullet gains both "never a baseline" sentences; the `epic_autopilot.py` citations at lines 855 and 859 (≈858 and 862 after the bullet edit) re-mapped |
| `docs/configuration.md` | 17 `epic_autopilot.py` line citations re-mapped (lines 209-218, 424, 476, 478, 529, 530, 550, 608) |

No other file changes. No `ci.yml` edit: the new tests are pytest, which CI already runs
(`.github/workflows/ci.yml:13`), and `docs_refs_check.py` already runs at `ci.yml:60`.

---

## Task 1: `LiveIO.comment` reports the `gh` exit status

**Files:** `tests/test_epic_autopilot.py`, `scripts/factory_core/epic_autopilot.py`

- [ ] **Step 1.1 — Write the failing test.** In `tests/test_epic_autopilot.py`, find the
  line `# ── Task 4: pick_next_epic selector ─────────...` (currently line 390, directly
  after `test_hold_dedup_does_not_change_selection`). Insert this block immediately
  **before** that heading line, leaving two blank lines on either side:

  ```python
  # ── #547: a failed HOLD comment never becomes the dedup baseline ─────────────

  LATEST = "2026-06-23T03:00:00+00:00"   # 25h after LATER


  class FailingCommentIO(FakeIO):
      def comment(self, issue, body):
          super().comment(issue, body)
          return False


  def test_live_comment_reports_gh_exit(monkeypatch, capsys):
      import types
      rc = {"value": 1}
      monkeypatch.setattr(ap.subprocess, "run",
                          lambda *a, **k: types.SimpleNamespace(returncode=rc["value"]))
      live = ap.LiveIO("x")
      assert live.comment(402, "body") is False
      assert capsys.readouterr().err == "autopilot: comment on #402 failed (gh exit 1)\n"
      rc["value"] = 0
      assert live.comment(402, "body") is True
      captured = capsys.readouterr()
      assert captured.err == "" and captured.out == ""
  ```

  `is False` / `is True` are deliberate (spec Requirement 4a). The HOLD call site checks
  `is not False`, so an `int` or `None` return would count as success and silently undo
  the fix while Task 2's fake-IO test stayed green. Patching `ap.subprocess.run` patches
  the shared `subprocess` module; `monkeypatch` restores it after the test.

- [ ] **Step 1.2 — Verify it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py::test_live_comment_reports_gh_exit -q
  ```

  Expected: `1 failed`, with `AssertionError: assert None is False`.

- [ ] **Step 1.3 — Add the `sys` import.** In `scripts/factory_core/epic_autopilot.py`,
  in the Live IO import block (currently lines 331-335), insert one line between
  `import subprocess  # noqa: E402` and `import urllib.request  # noqa: E402`:

  ```python
  import os  # noqa: E402
  import subprocess  # noqa: E402
  import sys  # noqa: E402
  import urllib.request  # noqa: E402
  ```

- [ ] **Step 1.4 — Change `LiveIO.comment`.** Replace the method (currently lines
  604-609) with the following. The four-line `#249` comment is kept verbatim; only the
  return annotation and the body below it change:

  ```python
      def comment(self, issue: int, body: str) -> bool:
          # Not routed through get_tracker() (#249): this posts a plain, non-idempotent
          # comment (unlike board.post_or_update_comment) — Tracker's frozen ABC (#248
          # §5.1) has only upsert_comment (marker-based), which would change idempotency
          # semantics here (multiple advance/hold comments over a ticket's lifetime).
          result = subprocess.run(
              ["gh", "issue", "comment", str(issue), "--repo", OWNER, "--body", body], check=False)
          if result.returncode != 0:
              print(f"autopilot: comment on #{issue} failed (gh exit {result.returncode})",
                    file=sys.stderr)
          return result.returncode == 0
  ```

  Constraints, all from the spec:
  - `check=False` stays, so a non-zero exit is reported, never raised.
  - No timeout and no retry are added.
  - An `OSError` (`gh` missing) still propagates out of `run_once` before
    `record_verdict`, so no baseline is stored. That path is unchanged and out of scope.
  - The stderr text must stay exactly as written. `scheduler.sh:1384` captures stderr
    with `2>&1`, and `scheduler.sh:1386` sets `DISPATCHED` when the captured output
    contains `autopilot=advanced` or `autopilot=epic_started`. This line contains
    neither.

- [ ] **Step 1.5 — Verify it passes.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -q
  ```

  Expected: `64 passed`.

- [ ] **Step 1.6 — Commit.**

  ```bash
  git add scripts/factory_core/epic_autopilot.py tests/test_epic_autopilot.py
  git commit -m "fix(autopilot): LiveIO.comment reports gh exit status (#547)"
  ```

---

## Task 2: HOLD branch stores the digest only when the comment posted

**Files:** `tests/test_epic_autopilot.py`, `scripts/factory_core/epic_autopilot.py`

- [ ] **Step 2.1 — Write the failing test.** In `tests/test_epic_autopilot.py`, append
  this test to the `#547` block, directly after `test_live_comment_reports_gh_exit`
  (two blank lines before it, two blank lines before the `# ── Task 4:` heading):

  ```python
  def test_failed_hold_comment_is_not_a_baseline():
      cand = c(number=402)
      h, d = ap.spec_hash(cand["spec_text"]), _digest(cand)
      st = {}
      io = FailingCommentIO([cand], HOLD_REVIEW % "risky")
      out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
      assert len(io.comments) == 1 and out["outcome"] == "hold"
      assert "parked (HOLD)" in io.comments[0][1]
      entry = st["verdicts"]["402"]
      assert "input_digest" not in entry
      assert entry["verdict"] == "HOLD" and entry["spec_hash"] == h and entry["ts"] == NOW
      io2 = FakeIO([c(number=402)], HOLD_REVIEW % "risky, reworded")
      out2 = ap.run_once(TTL_CFG, io2, st, "2026-06-22", LATER)
      assert len(io2.comments) == 1 and out2["outcome"] == "hold"
      assert st["verdicts"]["402"]["input_digest"] == d
      io3 = FakeIO([c(number=402)], HOLD_REVIEW % "risky, reworded again")
      out3 = ap.run_once(TTL_CFG, io3, st, "2026-06-23", LATEST)
      assert io3.comments == [] and out3["outcome"] == "hold_unchanged"
  ```

  How the three cycles work (spec "New test" shape):
  - Cycle 1 runs at `NOW`. The comment fails, so the entry is still cached (verdict,
    spec hash and `ts`) but has no `input_digest`.
  - Cycle 2 runs at `LATER`, 25h on, with `TTL_CFG`. Inside the 24h TTL the candidate
    would be skipped by `cached_verdict` and the outcome would be `no_candidates`, so the
    time step is needed. The same spec and review input must now comment again, and the
    working `FakeIO` (returns `None`) stores the digest.
  - Cycle 3 runs at `LATEST`, a further 25h on, with unchanged input. It returns
    `hold_unchanged` with zero comments: the baseline is back after a successful post.

- [ ] **Step 2.2 — Verify it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py::test_failed_hold_comment_is_not_a_baseline -q
  ```

  Expected: `1 failed`, with
  `AssertionError: assert 'input_digest' not in {'spec_hash': ..., 'verdict': 'HOLD', 'ts': '2026-06-21T01:00:00+00:00', 'input_digest': ...}`.

- [ ] **Step 2.3 — Change the HOLD branch.** In `run_once` (currently lines 320-323), replace

  ```python
      if not unchanged:
          io.comment(cand["number"],
                     f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
      record_verdict(state, cand["number"], h, "HOLD", now_iso, input_digest=input_digest)
  ```

  with

  ```python
      # A failed post (LiveIO returns False) must not become the dedup baseline (#547).
      posted = unchanged or io.comment(
          cand["number"],
          f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}"
      ) is not False
      record_verdict(state, cand["number"], h, "HOLD", now_iso,
                     input_digest=input_digest if posted else None)
  ```

  The comment body f-string must stay byte-identical. Leave the `concerns`,
  `unparseable`, `input_digest` and `unchanged` lines above it, and the `return {...}`
  below it, unchanged. Python parses the expression as
  `unchanged or (io.comment(...) is not False)`, because `is not` binds tighter than
  `or`:
  - `unchanged` is `True`: `comment()` is never called, as today.
  - `comment()` returns `True` or `None` (every non-`LiveIO` IO): `posted`, digest stored,
    as today.
  - `comment()` returns `False`: not posted, so `record_verdict` gets
    `input_digest=None` and the entry has no digest.

  For an unparseable review `input_digest` is already `None`, so that path is unchanged
  and `test_unparseable_review_is_never_suppressed` keeps passing. Do not touch the
  ADVANCE branch (lines 304-314) or the epic-promotion `io.comment` (lines 288-290).
  Both are out of scope: neither stores a dedup baseline.

- [ ] **Step 2.4 — Verify it passes.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -q
  ```

  Expected: `65 passed`. No existing test was edited, and all of them still pass
  (Requirement 3).

- [ ] **Step 2.5 — Commit.**

  ```bash
  git add scripts/factory_core/epic_autopilot.py tests/test_epic_autopilot.py
  git commit -m "fix(autopilot): a failed HOLD comment no longer becomes the dedup baseline (#547)"
  ```

---

## Task 3: `docs/scheduler.md` step 6 "Hold" bullet

**Files:** `docs/scheduler.md`

- [ ] **Step 3.1 — Confirm the target text.**

  ```bash
  grep -n 'Hold: comment "parked (HOLD)"' docs/scheduler.md
  ```

  Expected: `617:   - Hold: comment "parked (HOLD)" unless the stored HOLD is unchanged (same spec and`
  (the bullet continues on line 618).

- [ ] **Step 3.2 — Rewrite the bullet.** Replace these two lines:

  ```markdown
     - Hold: comment "parked (HOLD)" unless the stored HOLD is unchanged (same spec and
       review input), and cache it. Not counted against the cap. A skipped comment reports `hold_unchanged`.
  ```

  with:

  ```markdown
     - Hold: comment "parked (HOLD)" unless the stored HOLD is unchanged (same spec and
       review input), and cache it. Not counted against the cap. A skipped comment reports
       `hold_unchanged`. An unparseable review always comments and never becomes a dedup
       baseline. If the HOLD comment fails to post, the verdict is cached without its
       review-input digest, so the next cycle comments again.
  ```

  The first sentence covers the existing behavior (Requirement 6a). The second covers
  the new behavior (Requirement 6b). Keep the bullet's indentation of 3 spaces plus
  `- `, with 5-space continuation lines.

- [ ] **Step 3.3 — Verify.**

  ```bash
  grep -c "never becomes a dedup" docs/scheduler.md
  grep -c "fails to post, the verdict is cached without its" docs/scheduler.md
  ```

  Expected: `1` and `1`.

- [ ] **Step 3.4 — Commit.**

  ```bash
  git add docs/scheduler.md
  git commit -m "docs(scheduler): HOLD dedup never baselines an unparseable review or a failed post (#547)"
  ```

---

## Task 4: Re-map the `epic_autopilot.py` line citations

**Files:** `docs/configuration.md`, `docs/scheduler.md`

This task runs only after Tasks 1-2 are committed. The values are read from the edited
code.

- [ ] **Step 4.1 — Read each anchor's new line from the edited file.**

  ```bash
  F=scripts/factory_core/epic_autopilot.py
  for pat in '^def hard_excluded' '^_EPIC_EXCLUDE_RE_BASE =' '^_FACTORY_SELF_RE =' \
      '^_FACTORY_SELF_PATHS =' '^def run_once' '^def _hard_exclude_paths' \
      '^    return list(_DEFAULT_EXCLUDE)' '^def _effective_exclude_paths' \
      'merged = list(_load_exclude_paths(os.environ.get("CLONE_DIR"' '^    return merged' \
      '^def _effective_sensitive_keywords' '^def _target_dir' \
      'explicit = os.environ.get("CLONE_DIR")' '^def _load_exclude_paths' \
      'tracker.add_label(str(child), "ready-for-agent")' 'INTERNAL_API_TOKEN' \
      'SCHEDULER_STATE_DIR' 'clone_dir = _target_dir()' 'EPIC_AUTOPILOT_OPT_OUT_LABEL' \
      'EPIC_AUTOPILOT_SIZE_CEILING' 'sensitive_keywords=_effective_sensitive_keywords' \
      'EPIC_AUTOPILOT_SENSITIVE_KEYWORDS")' 'EPIC_AUTOPILOT_HOLD_TTL_HOURS' \
      'EPIC_AUTOPILOT_START_EPICS' 'Fail-closed: self-improvement' \
      'EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "false")' 'EPIC_AUTOPILOT_CONFIDENCE_FLOOR' \
      'EPIC_AUTOPILOT_DAILY_CAP' 'EPIC_AUTOPILOT_MODEL'; do
    printf '%-58s %s\n' "$pat" "$(grep -n -- "$pat" "$F" | cut -d: -f1 | tr '\n' ' ')"
  done
  ```

  Expected output (prototype), in pattern order:
  `60`, `188`, `189`, `194`, `257`, `356`, `365 459`, `380`, `387`, `391`, `394`, `424`,
  `431`, `439`, `628`, `631`, `650`, `658`, `661`, `662`, `663`, `664`, `665`, `666`,
  `667`, `669`, `670`, `671`, `672`.

  `_effective_sensitive_keywords` (394) ends on line 406. The old range end, `403`, was
  the blank line after that function, and its counterpart is now `407`:
  `sed -n 406,407p "$F"` shows `    return "|".join(f"(?:{p})" for p in parts)`, then a
  blank line.

- [ ] **Step 4.2 — Confirm the mapping table.** Every row's "new" value must match the
  Step 4.1 grep. If a value differs, use the grep value in Step 4.3.

  | Doc:line | Old citation | Anchor | New citation |
  |---|---|---|---|
  | `configuration.md:209` | `epic_autopilot.py:663` | `EPIC_AUTOPILOT_MODEL` | `:672` |
  | `configuration.md:210` | `:662` | `EPIC_AUTOPILOT_DAILY_CAP` | `:671` |
  | `configuration.md:211` | `:661` | `EPIC_AUTOPILOT_CONFIDENCE_FLOOR` | `:670` |
  | `configuration.md:212` | `:656` | `EPIC_AUTOPILOT_HOLD_TTL_HOURS` | `:665` |
  | `configuration.md:213` | `:653` | `EPIC_AUTOPILOT_SIZE_CEILING` | `:662` |
  | `configuration.md:214` | `:657` | `EPIC_AUTOPILOT_START_EPICS` | `:666` |
  | `configuration.md:215` | `:658-660` | `Fail-closed` comment … `ALLOW_SELF_IMPROVEMENT", "false")` | `:667-669` |
  | `configuration.md:216` | `:390-403,654-655` | `def _effective_sensitive_keywords`…blank; `sensitive_keywords=`…`SENSITIVE_KEYWORDS")` | `:394-407,663-664` |
  | `configuration.md:216` | `(\`epic_autopilot.py:420\`)` | `def _target_dir` | `:424` |
  | `configuration.md:217` | `:652` | `EPIC_AUTOPILOT_OPT_OUT_LABEL` | `:661` |
  | `configuration.md:218` | `:435-455` | `def _load_exclude_paths` … 2nd `return list(_DEFAULT_EXCLUDE)` | `:439-459` |
  | `configuration.md:218` | `(\`:376\`)` | `def _effective_exclude_paths` | `:380` |
  | `configuration.md:424` | `:622` | `INTERNAL_API_TOKEN` | `:631` |
  | `configuration.md:476` | `:641` | `SCHEDULER_STATE_DIR` | `:650` |
  | `configuration.md:478` | `:383,427` | `merged = list(_load_exclude_paths(...CLONE_DIR`; `explicit = os.environ.get("CLONE_DIR")` | `:387,431` |
  | `configuration.md:529` | `:390-403` | as row 216 (first range) | `:394-407` |
  | `configuration.md:530` | `:352-361,376-387,435-455` | `def _hard_exclude_paths`…1st `return list(_DEFAULT_EXCLUDE)`; `def _effective_exclude_paths`…`return merged`; as row 218 | `:356-365,380-391,439-459` |
  | `configuration.md:550` | `:619` | `tracker.add_label(str(child), "ready-for-agent")` | `:628` |
  | `configuration.md:556` | `:188-189` | `_EPIC_EXCLUDE_RE_BASE`, `_FACTORY_SELF_RE` | unchanged |
  | `configuration.md:557` | `:194` | `_FACTORY_SELF_PATHS` | unchanged |
  | `configuration.md:608` | `:435-455` | as row 218 | `:439-459` |
  | `scheduler.md:855` (≈858 after Task 3) | `(\`epic_autopilot.py:420\`, \`:649\`)` | `def _target_dir`; `clone_dir = _target_dir()` | `(\`epic_autopilot.py:424\`, \`:658\`)` |
  | `scheduler.md:859` (≈862 after Task 3) | `(\`:376\`, \`:390\`)` (bare continuation of the `:855` citation) | `def _effective_exclude_paths`; `def _effective_sensitive_keywords` | `(\`:380\`, \`:394\`)` |
  | `features.md:69` | `run_once` (`:257`), `hard_excluded` (`:60`) | `def run_once`; `def hard_excluded` | unchanged |

  `scheduler.md:855` is the doc's line number on `main` (before Task 3). After Task 3
  adds 3 lines above it, the citation sits at about line 858. The Step 4.3 script matches
  on text, not on line numbers, so this does not matter to it. Note that `configuration.md:218` and `:608` both contain
  `` `epic_autopilot.py:435-455` ``.

- [ ] **Step 4.3 — Apply the replacements.** Every match is asserted unique, except
  `435-455`, which is asserted to occur exactly twice. If Step 4.2 found a different
  value for any row, edit that pair in the script before running it.

  ```bash
  python3 - <<'EOF'
  def sub(p, pairs):
      s = open(p).read()
      for old, new in pairs:
          assert s.count(old) == 1, (p, old, s.count(old))
          s = s.replace(old, new)
      open(p, "w").write(s)

  C = "docs/configuration.md"
  # Order matters: each pair must run before any later pair that produces the same
  # number (663->672 before 662->671 before 653->662; 661->670 before 652->661).
  # Otherwise a value would be re-mapped twice. Do not re-order.
  sub(C, [
      ("`epic_autopilot.py:663`", "`epic_autopilot.py:672`"),
      ("`epic_autopilot.py:662`", "`epic_autopilot.py:671`"),
      ("`epic_autopilot.py:661`", "`epic_autopilot.py:670`"),
      ("`epic_autopilot.py:656`", "`epic_autopilot.py:665`"),
      ("`epic_autopilot.py:653`", "`epic_autopilot.py:662`"),
      ("`epic_autopilot.py:657`", "`epic_autopilot.py:666`"),
      ("`epic_autopilot.py:658-660`", "`epic_autopilot.py:667-669`"),
      ("`epic_autopilot.py:390-403,654-655`", "`epic_autopilot.py:394-407,663-664`"),
      ("`_target_dir()` (`epic_autopilot.py:420`)", "`_target_dir()` (`epic_autopilot.py:424`)"),
      ("`epic_autopilot.py:652`", "`epic_autopilot.py:661`"),
      ("`_effective_exclude_paths` (`:376`)", "`_effective_exclude_paths` (`:380`)"),
      ("`epic_autopilot.py:622` (scheduler)", "`epic_autopilot.py:631` (scheduler)"),
      ("`epic_autopilot.py:641`", "`epic_autopilot.py:650`"),
      ("`epic_autopilot.py:383,427`", "`epic_autopilot.py:387,431`"),
      ("| `epic_autopilot.py:390-403` (adapter", "| `epic_autopilot.py:394-407` (adapter"),
      ("`epic_autopilot.py:352-361,376-387,435-455`", "`epic_autopilot.py:356-365,380-391,439-459`"),
      ("`epic_autopilot.py:619`", "`epic_autopilot.py:628`"),
  ])
  s = open(C).read()
  assert s.count("`epic_autopilot.py:435-455`") == 2
  open(C, "w").write(s.replace("`epic_autopilot.py:435-455`", "`epic_autopilot.py:439-459`"))

  sub("docs/scheduler.md", [
      ("(`epic_autopilot.py:420`, `:649`)", "(`epic_autopilot.py:424`, `:658`)"),
      ("(`:376`, `:390`)", "(`:380`, `:394`)"),
  ])
  EOF
  ```

  Expected: no output and exit 0. An `AssertionError` means the doc text drifted. Fix
  that pair by hand from the Step 4.2 table, then re-run only the failing part.

- [ ] **Step 4.4 — Spot-check content, not just range.** `docs_refs_check.py` only checks
  that a cited line is in range (spec Requirement 8), so read a sample of the cited
  lines:

  ```bash
  F=scripts/factory_core/epic_autopilot.py
  for n in 356 365 380 387 391 394 424 431 439 459 628 631 650 658 661 663 664 667 669 672; do
    printf '%s: ' $n; sed -n ${n}p "$F"
  done
  ```

  Expected (prefixes):
  - `356: def _hard_exclude_paths(`
  - `365:     return list(_DEFAULT_EXCLUDE)`
  - `380: def _effective_exclude_paths(`
  - `387:     merged = list(_load_exclude_paths(os.environ.get("CLONE_DIR", ".")))`
  - `391:     return merged`
  - `394: def _effective_sensitive_keywords(`
  - `424: def _target_dir()`
  - `431:     explicit = os.environ.get("CLONE_DIR")`
  - `439: def _load_exclude_paths(`
  - `459:     return list(_DEFAULT_EXCLUDE)`
  - `628:             tracker.add_label(str(child), "ready-for-agent")`
  - `631:         token = os.environ.get("INTERNAL_API_TOKEN", "")`
  - `650:     state_dir = os.environ.get("SCHEDULER_STATE_DIR", ...`
  - `658:     clone_dir = _target_dir()`
  - `661:         opt_out_label=...`
  - `663:         sensitive_keywords=_effective_sensitive_keywords(`
  - `664:             clone_dir, os.environ.get("EPIC_AUTOPILOT_SENSITIVE_KEYWORDS")),`
  - `667:         # Fail-closed: ...`
  - `669:             "EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "false")...`
  - `672:         model=os.environ.get("EPIC_AUTOPILOT_MODEL", ...`

- [ ] **Step 4.5 — Confirm no stale citation remains.**

  ```bash
  grep -hoE "epic_autopilot\.py:[0-9,-]+" docs/configuration.md docs/scheduler.md docs/features.md docs/run-lifecycle.md | sort | uniq -c
  ```

  Expected: none of the old values (`352-361`, `376-387`, `383,427`, `390-403`, `420`,
  `435-455`, `619`, `622`, `641`, `652`…`663`, `654-655`, `658-660`) appear. The
  remaining set is `188-189`, `194`, `356-365,380-391,439-459`, `387,431`, `394-407`,
  `394-407,663-664`, `424` (×2), `439-459` (×2), `628`, `631`, `650`, `661`, `662`,
  `665`, `666`, `667-669`, `670`, `671`, `672`.

  Bare continuation spans carry no path, so the grep above and `docs_refs_check.py` both
  miss them. Check them separately:

  ```bash
  grep -nE '\(`:(376|390|649)`|`:(376|390|649)`\)' docs/scheduler.md docs/configuration.md
  ```

  Expected: no output (grep exits 1).

- [ ] **Step 4.6 — Run the docs reference check.**

  ```bash
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```

  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 4.7 — Commit.**

  ```bash
  git add docs/configuration.md docs/scheduler.md
  git commit -m "docs: re-map epic_autopilot.py line citations after #547"
  ```

---

## Task 5: Full verification

**Files:** none (verification only)

- [ ] **Step 5.1 — Full pytest suite.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q
  ```

  Expected: all pass. The prototype gave `2796 passed`; the count will drift with `main`,
  but there must be `0 failed`.

- [ ] **Step 5.2 — Scope check.**

  ```bash
  git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory'
  ```

  Expected: exactly `docs/configuration.md`, `docs/scheduler.md`,
  `scripts/factory_core/epic_autopilot.py` and `tests/test_epic_autopilot.py`. Roughly
  15+/6− lines in the code file, 45+ in the test file, 17 changed lines in
  `configuration.md`, 5+/2− in `scheduler.md`. `config/config.yaml`, `scheduler.sh`,
  gates and the ADVANCE branch must not appear.

- [ ] **Step 5.3 — Behavior guard.** Confirm that the ADVANCE branch and the
  epic-promotion comment are byte-identical to `main`:

  ```bash
  git diff origin/main HEAD -- scripts/factory_core/epic_autopilot.py | grep -E '^[-+].*(advancing|starting epic|record_advance)' || echo "ADVANCE/epic paths untouched"
  ```

  Expected: `ADVANCE/epic paths untouched`.

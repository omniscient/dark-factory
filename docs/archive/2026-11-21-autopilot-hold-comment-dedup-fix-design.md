# Fix: a Failed HOLD Comment Becomes a Permanent Dedup Baseline (#547)

**Issue:** #547

Revised: 2026-11-21 (operator amendment after spec gate review)

## Overview / Problem Statement

#456 (merged as `6e3862b`, PR #546) added an `input_digest`-based dedup so that a repeat
`HOLD` verdict on an unchanged ticket doesn't re-post the same "parked (HOLD)" comment on
every TTL cycle. The dedup baseline is stored unconditionally, regardless of whether the
HOLD comment actually posted.

`scripts/factory_core/epic_autopilot.py::run_once`'s HOLD branch (current `main`, verified
against the live file — line numbers below are re-derived, not copied from the issue body,
per the standing re-verification lesson from #182):

```python
# lines 316-325
concerns = "; ".join(verdict.get("concerns", [])) or "not low-risk / low confidence"
unparseable = verdict.get("reasons") == _HOLD["reasons"]  # parse_verdict's _HOLD sentinel
input_digest = None if unparseable else spec_hash(prompt)
unchanged = hold_unchanged(state, cand["number"], h, input_digest)
if not unchanged:
    io.comment(cand["number"],
               f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
record_verdict(state, cand["number"], h, "HOLD", now_iso, input_digest=input_digest)
return {"outcome": "hold_unchanged" if unchanged else "hold",
        "issue": cand["number"], "reason": concerns}
```

`io.comment(...)`'s return value is discarded, and `record_verdict(...,
input_digest=input_digest)` runs on the next line regardless of whether the comment
posted. `LiveIO.comment` (lines 604-609) always returns `None`:

```python
def comment(self, issue: int, body: str) -> None:
    # Not routed through get_tracker() (#249): this posts a plain, non-idempotent
    # comment (unlike board.post_or_update_comment) — Tracker's frozen ABC (#248
    # §5.1) has only upsert_comment (marker-based), which would change idempotency
    # semantics here (multiple advance/hold comments over a ticket's lifetime).
    subprocess.run(["gh", "issue", "comment", str(issue), "--repo", OWNER, "--body", body], check=False)
```

`subprocess.run(..., check=False)` never raises on a non-zero exit (rate limit, network
blip, `gh` auth hiccup, GitHub outage), so a failed post is invisible both to the caller and
to any log. Net effect: the ticket's state is permanently (until the spec changes or the
ticket's review input otherwise changes) stuck believing a HOLD was shown to the user, when
it never was. Pre-#456 this class of failure self-healed on the next TTL cycle (no baseline
existed at all); #456 removed that self-heal for this one case.

Already correct and **not** part of this bug (confirmed by re-reading the code and the
existing test `test_unparseable_review_is_never_suppressed`,
`tests/test_epic_autopilot.py:356`): an *unparseable* review already never becomes a
baseline. `input_digest = None if unparseable else spec_hash(prompt)` means
`record_verdict`'s `if input_digest is not None` guard (line 162 of `record_verdict`,
reproduced below) never stores a digest for an unparseable review, and `hold_unchanged`
requires `input_digest_ is not None` to match — so an unparseable review always re-comments
already. The bug is specifically: a *parseable* HOLD whose comment post silently fails still
stores the digest baseline as if the comment had posted.

```python
# lines 157-164
def record_verdict(state: dict, issue: int, spec_hash_: str, verdict: str,
                   now_iso=None, *, input_digest=None) -> None:
    entry = {"spec_hash": spec_hash_, "verdict": verdict}
    if now_iso:
        entry["ts"] = now_iso
    if input_digest is not None:
        entry["input_digest"] = input_digest
    state.setdefault("verdicts", {})[str(issue)] = entry
```

## Requirements

1. `LiveIO.comment` must report success/failure to its caller instead of discarding it.
2. `run_once`'s HOLD branch must store `input_digest` only when the comment actually posted
   (or was legitimately skipped because the stored baseline was already unchanged) — never
   when a comment attempt was made and failed.
3. The fix must not change behavior for any IO implementation that doesn't yet report a
   bool from `comment()` (notably the existing `FakeIO` in `tests/test_epic_autopilot.py`,
   and any future IO adapter) — only `LiveIO` gains the real failure signal; everything
   else keeps behaving exactly as it does today.
4. A new test must simulate a failing `comment()` and assert the next cycle (same spec,
   same review input) comments again rather than returning `hold_unchanged`, and that the
   stored verdict entry has no `input_digest` after the failed post.

   4a. A second new test must exercise `LiveIO.comment` itself with `subprocess.run`
   monkeypatched (`monkeypatch.setattr(ap.subprocess, "run", ...)`): a result with
   `returncode=1` must make `comment()` return exactly `False` (`is False`), and
   `returncode=0` exactly `True` (`is True`). The call site's `is not False` check treats
   any non-bool return (an int return code, `None`) as success, so without this test the
   fix can regress silently while Requirement 4's fake-IO test stays green. The same test
   asserts Requirement 9's stderr line via `capsys`.
5. No change to the ADVANCE decision, the daily cap, or `epic_autopilot.enabled`/other
   gates. The ADVANCE branch (lines 304-314) doesn't use `input_digest`/`hold_unchanged` at
   all and is out of scope — confirmed by the issue body and re-verified against the code.
6. `docs/scheduler.md` step 6's "Hold" bullet must describe both: (a) the pre-existing
   invariant that an unparseable review always comments and never becomes a baseline, and
   (b) the new invariant that a failed comment post also never becomes a baseline. (a)
   alone, which is the issue's literal requested sentence, would under-describe what this
   fix actually does — the HOLD bullet should read as the full, current picture of the
   dedup behavior, not just the half this ticket touches.
7. Every absolute-line-number citation to `scripts/factory_core/epic_autopilot.py` in
   `docs/configuration.md` and `docs/scheduler.md` that sits at or after the edited
   `LiveIO.comment` method must be re-derived from the landed code, not hand-offset. Do
   this by grepping the fixed file for the same textual anchor each citation already names
   (a function signature, an assignment, a string literal) and reading off that match's new
   line number — not by assuming a uniform shift. Anchors to re-check (current → citing
   doc:line):
   - `epic_autopilot.py:619` (`tracker.add_label(str(child), "ready-for-agent")`) →
     `docs/configuration.md:550`
   - `epic_autopilot.py:622` (`INTERNAL_API_TOKEN` read) → `docs/configuration.md:424`
   - `epic_autopilot.py:641` (`SCHEDULER_STATE_DIR` default) → `docs/configuration.md:476`
   - `epic_autopilot.py:649` (bare continuation beside `:420`) → `docs/scheduler.md:855`
   - `epic_autopilot.py:652/653/654-655/656/657/658-660/661/662/663` (config table rows;
     `654-655` is the second half of the `390-403,654-655` citation on
     `docs/configuration.md:216`) → `docs/configuration.md:209-217`
   Citations that sit *before* `LiveIO.comment` (`:188-189`, `:194`, `:352-361`,
   `:376-387`, `:383`, `:390-403`, `:420/427`, `:435-455`) are not expected to move, but
   must still be grep-confirmed rather than assumed, since the exact line delta the fix
   introduces depends on the final diff shape.
8. Run `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md
   docs/features.md docs/configuration.md` after the doc edits. Note this check only
   verifies a cited line is within the file's current line count — it does **not** verify
   the cited line's content matches the prose, so passing it is necessary but not
   sufficient; the grep-and-read step in Requirement 7 is still required.
9. When `gh` exits non-zero, `LiveIO.comment` must print exactly one line to stderr,
   `autopilot: comment on #<issue> failed (gh exit <rc>)`, and print nothing on success.
   `scheduler.sh:1384` captures the autopilot's stderr (`2>&1`) into the scheduler log, so
   this is what attributes the failure next to the unchanged `autopilot=hold` summary
   line. The line must not contain `autopilot=advanced` or `autopilot=epic_started`
   (`scheduler.sh:1386` matches those substrings in the captured output to set
   `DISPATCHED`). Tested by Requirement 4a's `LiveIO.comment` test via `capsys`.

## Architecture / Approach

**Chosen approach — minimal IO-contract extension, scoped to `LiveIO` and the HOLD branch:**

- `LiveIO.comment` changes its return type from `None` to `bool`:

  ```python
  def comment(self, issue: int, body: str) -> bool:
      # Not routed through get_tracker() (#249): ... [existing comment unchanged]
      result = subprocess.run(
          ["gh", "issue", "comment", str(issue), "--repo", OWNER, "--body", body], check=False)
      if result.returncode != 0:
          print(f"autopilot: comment on #{issue} failed (gh exit {result.returncode})",
                file=sys.stderr)
      return result.returncode == 0
  ```

  `check=False` is preserved, so a non-zero `gh` exit is *reported*, not raised. On
  `False`, `LiveIO.comment` also prints one line to stderr (`autopilot: comment on
  #<issue> failed (gh exit <rc>)`, Requirement 9); `scheduler.sh:1384` captures stderr
  into the scheduler log, and the line must not contain `autopilot=advanced` or
  `autopilot=epic_started`. `sys` is not imported in `epic_autopilot.py` today; add
  `import sys  # noqa: E402` beside the existing Live IO imports (lines 331-333).
  Exceptions from `subprocess.run` (`OSError` when `gh` is missing) are unchanged and out
  of scope: they propagate out of `run_once` before `record_verdict`, so no baseline is
  stored. No timeout is added.

- `run_once`'s HOLD branch computes whether the digest should be stored using the issue's
  exact proposed expression, which relies on `or`'s short-circuit to preserve "only call
  `comment()` when `not unchanged`":

  ```python
  posted = unchanged or io.comment(
      cand["number"],
      f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}"
  ) is not False
  record_verdict(state, cand["number"], h, "HOLD", now_iso,
                  input_digest=input_digest if posted else None)
  ```

  When `unchanged` is `True`, `io.comment(...)` is never evaluated (short-circuit), matching
  today's behavior of skipping the comment. When `unchanged` is `False`, `io.comment(...)`
  runs and its result is compared `is not False`: `True` → posted; `False` (only `LiveIO`
  can produce this today) → not posted; `None` (every other current IO implementation,
  including `FakeIO`) → posted, i.e. unchanged behavior for every caller except `LiveIO`.
  `input_digest` is `None` here already when the review was unparseable (Requirement/
  existing behavior above), so `input_digest if posted else None` is a no-op refinement on
  top of that existing guard, not a replacement for it.

- Why `is not False` rather than `is True`/truthiness: a bare truthiness check (`if
  io.comment(...)`) would treat `None` as falsy and flip existing `FakeIO`-based tests to
  "never baseline," which is both an unrequested behavior change and would require editing
  every existing `FakeIO`-based test in `tests/test_epic_autopilot.py` that currently relies
  on the HOLD branch storing a digest. `is not False` is the minimal change that upgrades
  only `LiveIO`.

- **New test**, following the existing `FakeEpicIO(FakeIO)` subclassing pattern
  (`tests/test_epic_autopilot.py:459`): add a `FailingCommentIO(FakeIO)` (or equivalent)
  whose `comment()` records the call (for assertion) and returns `False`. Place it near the
  `#456` test block (`tests/test_epic_autopilot.py:273` onward) since it extends that same
  scenario. Test shape:
  1. Cycle 1: HOLD with a failing `comment()` → outcome `hold`, `comment()` was called once,
     the stored verdict entry has **no** `input_digest` key.
  2. Cycle 2 uses `TTL_CFG` and a `now_iso` 25h after cycle 1 (cycle 1 at the existing
     `NOW` constant, cycle 2 at `LATER`), same spec/review input, with a plain `FakeIO`:
     outcome must be `hold` with one comment, **not** `hold_unchanged`, and the entry now
     has `input_digest`. Inside the TTL the candidate is skipped by `cached_verdict`
     (`epic_autopilot.py:278-280`) and the outcome would be `no_candidates`, so the time
     advance is required.
  3. Cycle 3, a further 25h on (a new constant beside `LATER`), unchanged input:
     `hold_unchanged`, zero comments (baseline restored after a successful post).

- **New `LiveIO.comment` test** (Requirements 4a and 9): build `ap.LiveIO("x")`,
  monkeypatch `ap.subprocess.run` to return an object with `returncode=1`, and assert
  `comment(402, "body") is False` and that `capsys.readouterr().err` is exactly
  `autopilot: comment on #402 failed (gh exit 1)\n`; then with `returncode=0` assert
  `comment(...) is True` and that stderr and stdout are empty.

  All new tests are pytest and run in CI via `python -m pytest tests/ -v`
  (`.github/workflows/ci.yml:13`); no `.sh` test or `ci.yml` edit is needed.

- **Docs**: `docs/scheduler.md`'s step 6 "Hold" bullet (currently lines 617-618) becomes
  (illustrative wording, exact line numbers to be re-confirmed at implementation time per
  Requirement 7):

  > Hold: comment "parked (HOLD)" unless the stored HOLD is unchanged (same spec and review
  > input), and cache it. Not counted against the cap. A skipped comment reports
  > `hold_unchanged`. An unparseable review always comments and never becomes a dedup
  > baseline. If the HOLD comment fails to post, the verdict is cached without its
  > review-input digest, so the next cycle comments again.

- **`docs/configuration.md` line-citation re-map**: mechanical follow-up per Requirement 7
  — grep-confirm and correct each listed anchor after the code lands, then run
  `docs_refs_check.py` as a necessary-but-not-sufficient check.

## Alternatives Considered

1. **Widen the IO contract formally** (document that every `comment()` implementation must
   return `bool`, update `FakeIO` to explicitly `return True`, add a dedicated flag/subclass
   for simulating failure). Rejected: there is no existing `Protocol`/ABC for the IO
   classes in `epic_autopilot.py` — `LiveIO`, `FakeIO`, and `FakeEpicIO(FakeIO)` are
   duck-typed. Introducing a formal contract is a larger, separate change than this
   low-severity, narrowly-scoped bug warrants, and risks touching unrelated call sites
   (`deps_fixer.py`, `main_red_fixer.py` have their own, unrelated `comment()` methods).
   Confirmed via brainstorming Q&A below.
2. **Retry the comment post inline (e.g. a bounded retry loop in `LiveIO.comment`)
   instead of reporting failure outward.** Rejected: masks the same class of failure this
   bug is about (a `gh` call that fails for longer than a short retry window still ends up
   silently eating the baseline), adds latency/complexity to a kill-switched,
   disabled-by-default feature (`epic_autopilot.enabled: false`), and isn't what the issue
   asks for. The existing TTL-based re-review (`hold_ttl_hours`) is already the system's
   retry mechanism for this class of transient failure — fixing the baseline-corruption bug
   restores that mechanism's intended behavior rather than adding a second, redundant one.
3. **Track failure via an exception instead of a bool return.** Rejected: `check=False` (line 609)
   keeps a failed `gh` exit from crashing the autopilot cycle; converting to an exception would require
   a new try/except at the call site to preserve that fail-soft property, which is strictly
   more code than a bool return for the same outcome.

## Open Questions (non-blocking)

- Should the ADVANCE-branch and epic-promotion `io.comment(...)` calls (lines 307-309,
  288-290) also report/log a failed post? Out of scope for this ticket per the issue body
  ("No change to the advance decision, caps or gates") and confirmed in brainstorming:
  neither of those call sites stores a dedup baseline, so a failed post there is an
  isolated missed notification, not the baseline-corruption bug this ticket fixes. Worth a
  separate low-priority ticket if silent ADVANCE-notification loss becomes a problem in
  practice.

## Assumptions

- The exact line-number deltas introduced by the code diff are not fixed by this spec;
  Requirement 7's anchors are the authoritative way to re-locate each citation, not a
  precomputed offset table.
- `epic_autopilot.enabled` remains `false` by default (`config/config.yaml`); this fix does
  not change that kill-switch or require it to change.

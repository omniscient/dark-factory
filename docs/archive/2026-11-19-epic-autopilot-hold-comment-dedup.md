# Epic Autopilot HOLD Comment Dedup — Implementation Plan

**Issue:** #456

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax for tracking. Execute
> tasks in order — Task 3 edits doc lines by number and must run before Task 4 changes
> any line count in `docs/scheduler.md`.

**Goal:** When a TTL-expired re-review of a parked Epic Autopilot candidate returns HOLD
for a ticket whose spec text and review input are unchanged, do not post another
"parked (HOLD)" comment. Still refresh the cached verdict, and return a distinct
`hold_unchanged` outcome.

**Spec:** `docs/superpowers/specs/2026-11-19-epic-autopilot-hold-comment-dedup-design.md`
(revised 2026-11-19 at the spec gate; authoritative over the issue's original "concerns
digest" wording).

**Architecture:** The comparison key is `(spec_hash, verdict, input_digest)`, where
`input_digest = spec_hash(build_review_prompt(cand, allow_self))`, the digest of the exact
prompt sent to the reviewer. `record_verdict` gains an optional keyword-only
`input_digest`. A new pure predicate `hold_unchanged()` sits next to `cached_verdict`.
`run_once()` builds the prompt once and digests it. In the HOLD branch only the
`io.comment()` call becomes conditional; `record_verdict` still runs on every HOLD. An
unparseable review (the `_HOLD` sentinel, `reasons == ["unparseable"]`) stores no digest
and always comments.

**Tech Stack:** Python 3 (stdlib `hashlib`/`json`), pytest, Markdown docs, and CI's
`scripts/docs_refs_check.py`.

**Out of scope (spec):** option (a), which skips the re-review itself; any config knob;
acceptance item 4, the operator-side audit or cleanup of existing duplicate comments;
anything under `deploy/**`; the ADVANCE branch, `should_advance`, the daily cap and the
epic-start branch.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/epic_autopilot.py` | Modify | `record_verdict(..., *, input_digest=None)`; new `hold_unchanged()`; `run_once()` builds `prompt` once; conditional HOLD comment + `hold_unchanged` outcome |
| `tests/test_epic_autopilot.py` | Modify | 7 new tests: predicate, JSON round-trip, suppressed path, changed-ticket path, legacy entry, unparseable review, selection invariant |
| `docs/configuration.md` | Modify | Shift 21 `epic_autopilot.py:N` line citations that the code insertion moves (Task 3) |
| `docs/features.md` | Modify | Shift the `run_once` (`:237`) citation on line 68 (Task 3) |
| `docs/scheduler.md` | Modify | Shift 2 citation lines (`:845`, `:849`); step 6 "Hold:" bullet (`:608`); `autopilot=<outcome>` enum gains `hold_unchanged` (`:696`) |

Why Task 3 is included: the spec's Docs section lists only the two `docs/scheduler.md`
prose edits. But this change inserts 20 lines inside `epic_autopilot.py`'s lines 157–280
and 26 lines in total before line 300. Every reference-doc citation of a later line
would then point at the wrong code. `docs_refs_check.py` checks only that a cited line is
in range, so CI would not catch it; PR #533 had to re-map citations that drifted this
way. Task 3 is a mechanical consequence of this change with no behavior or prose change.
The new line numbers were verified line-by-line against a prototype of Tasks 1–2.

No `config/config.yaml` key is added, so `docs/configuration.md` gets no new row (spec).

---

## Task 1: `record_verdict` digest parameter + `hold_unchanged` predicate

**Files:**
- Modify: `scripts/factory_core/epic_autopilot.py:157-161` (`record_verdict`), then insert `hold_unchanged` directly after it
- Test: `tests/test_epic_autopilot.py` (new block inserted before the line `# ── Task 4: pick_next_epic selector ─────────────────────────────────────────`, currently `:273`)

- [ ] **Step 1: Write the failing tests**

In `tests/test_epic_autopilot.py`, insert the following block immediately **before** the
line `# ── Task 4: pick_next_epic selector ─────────────────────────────────────────`.
It sits after `test_cached_verdict_backward_compatible_without_timestamps`, so `FakeIO`
and `CFG` are already defined above it:

```python
# ── #456: HOLD comment dedup on TTL re-review ────────────────────────────────

TS0 = "2026-06-20T00:00:00+00:00"
NOW = "2026-06-21T01:00:00+00:00"      # 25h after TS0 → the cached HOLD has expired
LATER = "2026-06-22T02:00:00+00:00"    # 25h after NOW
HOLD_REVIEW = '{"decision":"HOLD","risk":"medium","confidence":0.5,"reasons":[],"concerns":["%s"]}'
TTL_CFG = dict(CFG, hold_ttl_hours=24)


def _digest(cand, allow_self=False):
    return ap.spec_hash(ap.build_review_prompt(cand, allow_self))


def test_hold_unchanged_predicate():
    st = {}
    h, d = ap.spec_hash("spec A"), ap.spec_hash("prompt A")
    assert not ap.hold_unchanged(st, 402, h, d)                       # no entry
    ap.record_verdict(st, 402, h, "HOLD", TS0)
    assert not ap.hold_unchanged(st, 402, h, d)                       # entry without digest
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=d)
    assert ap.hold_unchanged(st, 402, h, d)                           # all three match
    assert not ap.hold_unchanged(st, 402, h, None)                    # None digest argument
    assert not ap.hold_unchanged(st, 402, ap.spec_hash("spec B"), d)  # spec hash differs
    assert not ap.hold_unchanged(st, 402, h, ap.spec_hash("prompt B"))  # digest differs
    assert not ap.hold_unchanged(st, 403, h, d)                       # other issue
    ap.record_verdict(st, 402, h, "ADVANCE", TS0, input_digest=d)
    assert not ap.hold_unchanged(st, 402, h, d)                       # stored verdict not HOLD


def test_record_verdict_digest_survives_json_roundtrip():
    import json
    st = {}
    h, d = ap.spec_hash("spec A"), ap.spec_hash("prompt A")
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=d)
    st2 = json.loads(json.dumps(st))
    assert st2["verdicts"]["402"]["input_digest"] == d
    assert ap.hold_unchanged(st2, 402, h, d)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -v -k "hold_unchanged_predicate or digest_survives"`
(if it prints `No module named pytest`, first run `python -m pip install pytest pyyaml aiohttp`, the same install CI does)

Expected: 2 failed. `test_hold_unchanged_predicate` fails with
`AttributeError: module 'factory_core.epic_autopilot' has no attribute 'hold_unchanged'`.
`test_record_verdict_digest_survives_json_roundtrip` fails with
`TypeError: record_verdict() got an unexpected keyword argument 'input_digest'`.

- [ ] **Step 3: Implement `record_verdict`'s digest parameter**

In `scripts/factory_core/epic_autopilot.py`, replace the whole current `record_verdict`
(`:157-161`):

```python
def record_verdict(state: dict, issue: int, spec_hash_: str, verdict: str, now_iso=None) -> None:
    entry = {"spec_hash": spec_hash_, "verdict": verdict}
    if now_iso:
        entry["ts"] = now_iso
    state.setdefault("verdicts", {})[str(issue)] = entry
```

with:

```python
def record_verdict(state: dict, issue: int, spec_hash_: str, verdict: str,
                   now_iso=None, *, input_digest=None) -> None:
    entry = {"spec_hash": spec_hash_, "verdict": verdict}
    if now_iso:
        entry["ts"] = now_iso
    if input_digest is not None:
        entry["input_digest"] = input_digest
    state.setdefault("verdicts", {})[str(issue)] = entry
```

Existing call sites (`epic_autopilot.py:292` ADVANCE, `tests/test_epic_autopilot.py:155,249,259,269`) pass no `input_digest` and stay unmodified.

- [ ] **Step 4: Add the `hold_unchanged` predicate**

Directly after `record_verdict` and before the
`# ── Epic selector (pure; used by epic-starter workflow) ──────────────────────` comment,
insert this, keeping two blank lines before and after the function:

```python
def hold_unchanged(state: dict, issue: int, spec_hash_: str, input_digest_) -> bool:
    """True when the issue's last stored verdict was HOLD for the same spec and review input.

    Deliberately ignores ttl_hours (unlike cached_verdict): this runs *after* a fresh
    re-review has already happened (triggered by TTL expiry or a changed spec), to
    decide whether posting a comment would tell the user anything new — TTL expiry
    alone must not cause a repeat comment. A None digest on either side never matches,
    so legacy entries and unparseable reviews always comment.
    """
    entry = (state.get("verdicts") or {}).get(str(issue))
    return bool(entry
                and input_digest_ is not None
                and entry.get("verdict") == "HOLD"
                and entry.get("spec_hash") == spec_hash_
                and entry.get("input_digest") == input_digest_)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -v`

Expected: all pass (54 passed: the 52 existing tests plus 2 new); no existing test
modified.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/epic_autopilot.py tests/test_epic_autopilot.py
git commit -m "feat(autopilot): store a review-input digest with HOLD verdicts (#456)

record_verdict gains an optional keyword-only input_digest; new pure
hold_unchanged() predicate compares spec hash, HOLD verdict and digest.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Suppress the unchanged-HOLD comment in `run_once`

**Files:**
- Modify: `scripts/factory_core/epic_autopilot.py` — `run_once()` review call (`:280-282` before Task 1, `:300-302` after) and HOLD branch (`:295-299` before Task 1, `:315-319` after)
- Test: `tests/test_epic_autopilot.py` (append to the Task 1 block)

- [ ] **Step 1: Write the failing tests**

In `tests/test_epic_autopilot.py`, insert the following directly after
`test_record_verdict_digest_survives_json_roundtrip` (from Task 1), still before the
`# ── Task 4: pick_next_epic selector` line. All `run_once` cases use `TTL_CFG` with `TS0`
seeds and `NOW`, so the seeded HOLD has expired. This is required (spec Requirement 7):
plain `CFG` has no `hold_ttl_hours`, so `cached_verdict` would keep returning HOLD and the
run would end in `no_candidates`.

```python
def test_run_suppresses_unchanged_hold_comment():
    cand = c(number=402)
    h, d = ap.spec_hash(cand["spec_text"]), _digest(cand)
    st = {}
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=d)
    io = FakeIO([cand], HOLD_REVIEW % "worded differently this time")
    out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
    assert io.comments == []
    assert out["outcome"] == "hold_unchanged" and out["issue"] == 402
    entry = st["verdicts"]["402"]
    assert entry["ts"] == NOW and entry["input_digest"] == d
    assert entry["verdict"] == "HOLD" and entry["spec_hash"] == h


def test_run_comments_on_changed_ticket():
    h = ap.spec_hash(c(number=402)["spec_text"])
    d = _digest(c(number=402))
    st = {}
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=d)
    changed = c(number=402, labels=["ready-for-agent", "bug"])   # same spec text → same h
    io = FakeIO([changed], HOLD_REVIEW % "still risky")
    out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
    assert len(io.comments) == 1 and io.comments[0][0] == 402
    assert "parked (HOLD)" in io.comments[0][1]
    assert out["outcome"] == "hold"
    entry = st["verdicts"]["402"]
    assert entry["input_digest"] != d and entry["input_digest"] == _digest(changed)
    assert entry["spec_hash"] == h and entry["ts"] == NOW


def test_run_comments_once_on_legacy_hold_entry():
    cand = c(number=402)
    h, d = ap.spec_hash(cand["spec_text"]), _digest(cand)
    st = {}
    ap.record_verdict(st, 402, h, "HOLD", TS0)                   # pre-#456 entry: no digest
    io = FakeIO([cand], HOLD_REVIEW % "risky")
    out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
    assert len(io.comments) == 1 and out["outcome"] == "hold"
    assert st["verdicts"]["402"]["input_digest"] == d
    io2 = FakeIO([c(number=402)], HOLD_REVIEW % "risky, reworded")
    out2 = ap.run_once(TTL_CFG, io2, st, "2026-06-22", LATER)
    assert io2.comments == [] and out2["outcome"] == "hold_unchanged"


def test_unparseable_review_is_never_suppressed():
    cand = c(number=402)
    h, d = ap.spec_hash(cand["spec_text"]), _digest(cand)
    st = {}
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=d)
    io = FakeIO([cand], "garbage")
    out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
    assert len(io.comments) == 1 and out["outcome"] == "hold"
    assert "input_digest" not in st["verdicts"]["402"]
    assert ap.cached_verdict(st, 402, h, NOW, 24) == "HOLD"     # TTL caching as before
    io2 = FakeIO([c(number=402)], HOLD_REVIEW % "risky")
    out2 = ap.run_once(TTL_CFG, io2, st, "2026-06-22", LATER)
    assert len(io2.comments) == 1 and out2["outcome"] == "hold"


def test_hold_dedup_does_not_change_selection():
    c402, c403 = c(number=402), c(number=403)
    h = ap.spec_hash(c402["spec_text"])
    st = {}
    ap.record_verdict(st, 402, h, "HOLD", TS0, input_digest=_digest(c402))
    io = FakeIO([c402, c403], HOLD_REVIEW % "reworded")
    out = ap.run_once(TTL_CFG, io, st, "2026-06-21", NOW)
    assert out["outcome"] == "hold_unchanged" and out["issue"] == 402
    assert io.advanced == [] and io.comments == []
    assert ap.daily_remaining(st, 5, "2026-06-21") == 5
    assert ap.cached_verdict(st, 402, h, NOW, 24) == "HOLD"
    # next poll inside the TTL: 402 is cache-skipped, 403 is reviewed — same order a
    # posted HOLD would have produced
    io2 = FakeIO([c(number=402), c(number=403)], HOLD_REVIEW % "risky")
    out2 = ap.run_once(TTL_CFG, io2, st, "2026-06-21", "2026-06-21T02:00:00+00:00")
    assert out2["issue"] == 403 and out2["outcome"] == "hold"
    assert io2.advanced == [] and len(io2.comments) == 1
```

These cases use `c()`, which declares `target_paths`, so `hard_excluded` never sets
`scope_undeclared` on them. The prompt `_digest(cand)` builds outside `run_once` is
therefore byte-identical to the one `run_once` builds; `CFG` leaves `allow_self` False on
both sides.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -v -k "suppresses_unchanged or changed_ticket or legacy_hold or unparseable_review or does_not_change_selection"`

Expected, with Task 1 applied: 4 failed, 1 passed.

- `test_run_comments_on_changed_ticket` and `test_run_comments_once_on_legacy_hold_entry`
  fail with `KeyError: 'input_digest'`: the HOLD branch does not store a digest yet.
- `test_run_suppresses_unchanged_hold_comment` and
  `test_hold_dedup_does_not_change_selection` fail with `AssertionError`: the comment is
  still posted.
- `test_unparseable_review_is_never_suppressed` already passes. It pins behavior that
  must survive the change: the comment is always posted and no digest is stored. It is a
  regression guard, not a red test.

- [ ] **Step 3: Build the review prompt once in `run_once`**

In `run_once()`, replace:

```python
    cand = candidates[0]
    verdict = parse_verdict(io.review(build_review_prompt(cand, allow_self), cfg["model"]))
```

with:

```python
    cand = candidates[0]
    prompt = build_review_prompt(cand, allow_self)
    verdict = parse_verdict(io.review(prompt, cfg["model"]))
```

The next line, `h = spec_hash(cand.get("spec_text", ""))`, stays unchanged. Do not touch
the ADVANCE branch (`if should_advance(...)` through `return {"outcome": "advanced", ...}`).
Its `record_verdict(..., "ADVANCE", now_iso)` passes no digest.

- [ ] **Step 4: Make the HOLD comment conditional**

Replace the HOLD tail of `run_once()`:

```python
    concerns = "; ".join(verdict.get("concerns", [])) or "not low-risk / low confidence"
    io.comment(cand["number"],
               f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
    record_verdict(state, cand["number"], h, "HOLD", now_iso)
    return {"outcome": "hold", "issue": cand["number"], "reason": concerns}
```

with:

```python
    concerns = "; ".join(verdict.get("concerns", [])) or "not low-risk / low confidence"
    unparseable = verdict.get("reasons") == ["unparseable"]  # parse_verdict's _HOLD sentinel
    input_digest = None if unparseable else spec_hash(prompt)
    unchanged = hold_unchanged(state, cand["number"], h, input_digest)
    if not unchanged:
        io.comment(cand["number"],
                   f"\U0001f916 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
    record_verdict(state, cand["number"], h, "HOLD", now_iso, input_digest=input_digest)
    return {"outcome": "hold_unchanged" if unchanged else "hold",
            "issue": cand["number"], "reason": concerns}
```

The comment text is byte-identical to today. `record_verdict` runs on every HOLD with the
same `spec_hash`, `"HOLD"` and `ts` as before (spec Requirement 10). `main_once` prints
`autopilot={out['outcome']}` unchanged. `scheduler.sh:1393` matches only
`autopilot=advanced` / `autopilot=epic_started`, so `hold_unchanged` leaves `DISPATCHED`
empty, exactly as `hold` does. No shell change is needed.

- [ ] **Step 5: Run the whole autopilot test file**

Run: `PYTHONPATH=scripts python -m pytest tests/test_epic_autopilot.py -v`

Expected: all pass (59 passed: 52 existing + 7 new). `test_run_holds_medium_risk`,
`test_run_skips_cached_hold`, `test_hold_ttl_expires_and_reenables_review` and
`test_cached_verdict_backward_compatible_without_timestamps` must pass unmodified.

- [ ] **Step 6: Confirm the line layout Task 3 depends on**

Run:

```bash
wc -l scripts/factory_core/epic_autopilot.py
sed -n '188p;194p;376p;420p;435p;651p;658p;665p' scripts/factory_core/epic_autopilot.py
```

Expected: `674 scripts/factory_core/epic_autopilot.py`, then these lines in order:

```
_EPIC_EXCLUDE_RE_BASE = r"security|auth|authz|authn|trading|ibkr"
_FACTORY_SELF_PATHS = frozenset({"dark-factory/", ".archon/", "scheduler.sh", "factory_core/"})
def _effective_exclude_paths(clone_dir: str | None) -> list:
def _target_dir() -> str:
def _load_exclude_paths(clone_dir: str | None = None) -> list:
    clone_dir = _target_dir()
        hold_ttl_hours=float(os.environ.get("EPIC_AUTOPILOT_HOLD_TTL_HOURS", "24")),
        model=os.environ.get("EPIC_AUTOPILOT_MODEL", "claude-opus-5-5"))
```

If the count is not 674, a formatting difference crept in; for example, the blank lines
around `hold_unchanged` are not exactly two. Fix the layout to match Steps 3–4 and Task 1
Steps 3–4 exactly before going on. Task 3's offsets are +20 for old lines 164–280 and +26
for old lines ≥ 300.

- [ ] **Step 7: Commit**

```bash
git add scripts/factory_core/epic_autopilot.py tests/test_epic_autopilot.py
git commit -m "fix(autopilot): don't re-post an unchanged HOLD comment on TTL re-review (#456)

A re-review that HOLDs again with the same spec hash and the same review
prompt digest as the stored HOLD skips the comment but still refreshes the
cached verdict, returning outcome=hold_unchanged. Unparseable reviews and
pre-#456 entries without a digest always comment.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 3: Re-map the shifted `epic_autopilot.py` line citations

**Files:**
- Modify: `docs/configuration.md` (lines 196–205, 388, 440, 442, 493, 494, 514, 520, 521, 572)
- Modify: `docs/scheduler.md` (line 845, and the bare continuation (`:350`, `:364`) on line 849)
- Modify: `docs/features.md` (line 68, the `run_once` (`:237`) citation; `hard_excluded` (`:60`) on the same row is unaffected)

Run this task **before** Task 4. The script addresses doc lines by number, and Task 4
adds a line to `docs/scheduler.md` above lines 845 and 849.

- [ ] **Step 1: Apply the citation edits**

Run from the repo root. The script fails loudly, writing nothing for that doc, if any
expected old citation is not found exactly once on its line:

```bash
python3 - <<'EOF'
"""#456: shift epic_autopilot.py line citations by the lines the change inserted."""
from pathlib import Path

EDITS = [  # (doc, 1-based line, old substring, new substring)
    ("docs/configuration.md", 196, "epic_autopilot.py:639", "epic_autopilot.py:665"),
    ("docs/configuration.md", 197, "epic_autopilot.py:638", "epic_autopilot.py:664"),
    ("docs/configuration.md", 198, "epic_autopilot.py:637", "epic_autopilot.py:663"),
    ("docs/configuration.md", 199, "epic_autopilot.py:632", "epic_autopilot.py:658"),
    ("docs/configuration.md", 200, "epic_autopilot.py:629", "epic_autopilot.py:655"),
    ("docs/configuration.md", 201, "epic_autopilot.py:633", "epic_autopilot.py:659"),
    ("docs/configuration.md", 202, "epic_autopilot.py:634-636", "epic_autopilot.py:660-662"),
    ("docs/configuration.md", 203, "epic_autopilot.py:364-377,630-631", "epic_autopilot.py:390-403,656-657"),
    ("docs/configuration.md", 203, "epic_autopilot.py:394", "epic_autopilot.py:420"),
    ("docs/configuration.md", 204, "epic_autopilot.py:628", "epic_autopilot.py:654"),
    ("docs/configuration.md", 205, "epic_autopilot.py:409-428", "epic_autopilot.py:435-454"),
    ("docs/configuration.md", 205, "(`:350`)", "(`:376`)"),
    ("docs/configuration.md", 388, "epic_autopilot.py:598", "epic_autopilot.py:624"),
    ("docs/configuration.md", 440, "epic_autopilot.py:617", "epic_autopilot.py:643"),
    ("docs/configuration.md", 442, "epic_autopilot.py:357,401", "epic_autopilot.py:383,427"),
    ("docs/configuration.md", 493, "epic_autopilot.py:364-377", "epic_autopilot.py:390-403"),
    ("docs/configuration.md", 494, "epic_autopilot.py:326-335,350-361,409-428", "epic_autopilot.py:352-361,376-387,435-454"),
    ("docs/configuration.md", 514, "epic_autopilot.py:595", "epic_autopilot.py:621"),
    ("docs/configuration.md", 520, "epic_autopilot.py:168-169", "epic_autopilot.py:188-189"),
    ("docs/configuration.md", 521, "epic_autopilot.py:174", "epic_autopilot.py:194"),
    ("docs/configuration.md", 572, "epic_autopilot.py:409-428", "epic_autopilot.py:435-454"),
    ("docs/scheduler.md", 845, "(`epic_autopilot.py:394`, `:625`)", "(`epic_autopilot.py:420`, `:651`)"),
    ("docs/scheduler.md", 849, "(`:350`, `:364`)", "(`:376`, `:390`)"),
    ("docs/features.md", 68, "`run_once` (`:237`)", "`run_once` (`:257`)"),
]

for doc in sorted({e[0] for e in EDITS}):
    p = Path(doc)
    lines = p.read_text(encoding="utf-8").split("\n")
    for d, n, old, new in EDITS:
        if d != doc:
            continue
        assert lines[n - 1].count(old) == 1, f"{doc}:{n}: expected exactly one {old!r}"
        lines[n - 1] = lines[n - 1].replace(old, new)
    p.write_text("\n".join(lines), encoding="utf-8")
print(f"applied {len(EDITS)} citation edits")
EOF
```

Expected output: `applied 24 citation edits`

Each old→new pair cites the same source line before and after Tasks 1–2, because
everything moved by a fixed offset. Citations of lines ≤ 156, such as `hard_excluded`
(`:60`) on `docs/features.md:68`, are unchanged. The other bare `(:N)` spans in `docs/configuration.md` (`:76-103`, `:79`,
`:110`, `:296-302`, `:838`) cite other files and are not touched.

- [ ] **Step 2: Verify the diff and the CI reference check**

Run:

```bash
git diff --stat -- docs/
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: `3 files changed, 22 insertions(+), 22 deletions(-)`. That is 19 lines in
`docs/configuration.md` (21 edits; lines 203 and 205 carry two each), 2 in
`docs/scheduler.md` (845, and its continuation on 849) and 1 in `docs/features.md`. Then
`Docs reference check passed for 4 doc(s).`

Spot-check that the cited code is right: `sed -n 658p scripts/factory_core/epic_autopilot.py`
must print the `hold_ttl_hours=float(os.environ.get("EPIC_AUTOPILOT_HOLD_TTL_HOURS", "24")),`
line that `docs/configuration.md:199` now cites.

- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md docs/scheduler.md docs/features.md
git commit -m "docs: re-map epic_autopilot.py line citations shifted by #456

Mechanical +20/+26 offset for the lines moved by hold_unchanged and the
HOLD-branch change; no prose change.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 4: Document the dedup in `docs/scheduler.md`

**Files:**
- Modify: `docs/scheduler.md:608` (Epic autopilot step 6, "Hold:" bullet)
- Modify: `docs/scheduler.md:696` (`autopilot=<outcome>` log-line row)

- [ ] **Step 1: Update the "Hold:" bullet (`:608`)**

Replace the single line:

```
   - Hold: comment "parked (HOLD)" and cache it. Not counted against the cap.
```

with these two lines. The wording follows the spec; the second line adds the outcome
name:

```
   - Hold: comment "parked (HOLD)" unless the stored HOLD is unchanged (same spec and
     review input), and cache it. Not counted against the cap. A skipped comment reports `hold_unchanged`.
```

- [ ] **Step 2: Add `hold_unchanged` to the outcome enum (`:696` before Step 1, `:697` after)**

On the `| \`autopilot=<outcome> issue=#N\` |` row, replace the substring:

```
Epic autopilot result: `advanced`, `hold`, `epic_started`, `no_candidates`, `daily_cap_reached`.
```

with:

```
Epic autopilot result: `advanced`, `hold`, `hold_unchanged` (a repeat HOLD whose comment was skipped), `epic_started`, `no_candidates`, `daily_cap_reached`.
```

Leave the rest of the row, including its `scheduler.sh:1391-1392` citation, unchanged.

- [ ] **Step 3: Verify**

Run:

```bash
grep -n "hold_unchanged" docs/scheduler.md
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: two matches (lines 609 and 697), then
`Docs reference check passed for 4 doc(s).`

- [ ] **Step 4: Commit**

```bash
git add docs/scheduler.md
git commit -m "docs(scheduler): document the hold_unchanged autopilot outcome (#456)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 5: Full verification

**Files:** none modified.

- [ ] **Step 1: Full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -v`

Expected: the same pass count as `main` plus 7, with no new failures. If a test outside
`tests/test_epic_autopilot.py` fails, check it against `main` with `git stash`. A failure
that also happens on `main` is pre-existing; record it in the PR body and do not fix it
here.

- [ ] **Step 2: CI DAG and docs checks**

Run:

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: all three exit 0; the last prints `Docs reference check passed for 4 doc(s).`

- [ ] **Step 3: Scope check**

Run: `git diff --name-only origin/main HEAD`

Expected: exactly these files, plus the spec/plan docs carried over from the refine
branch:

```
docs/configuration.md
docs/features.md
docs/scheduler.md
scripts/factory_core/epic_autopilot.py
tests/test_epic_autopilot.py
```

Nothing under `deploy/`, `config/`, `scheduler.sh` or `.github/`.

---

## Requirement → task trace

| Spec requirement | Covered by |
|---|---|
| 1. Key `(spec_hash, verdict, input_digest)`; digest = `spec_hash(build_review_prompt(...))` | Task 1 Steps 3–4, Task 2 Steps 3–4 |
| 2. Skip only on a stored HOLD that matches; first HOLD and prior ADVANCE always comment | `hold_unchanged` (Task 1 Step 4); `test_hold_unchanged_predicate` (ADVANCE / no-entry cases); existing `test_run_holds_medium_risk` |
| 3. State refreshed on every HOLD | Task 2 Step 4 (unconditional `record_verdict`); `test_run_suppresses_unchanged_hold_comment` asserts `ts == NOW` |
| 4. Changed ticket or spec still comments; reworded concerns alone do not | `test_run_comments_on_changed_ticket`, `test_run_suppresses_unchanged_hold_comment`, predicate spec-hash case |
| 5. `hold_unchanged` outcome + `docs/scheduler.md:696` | Task 2 Step 4, Task 4 Step 2 |
| 6. No config knob | No `config/` change (Task 5 Step 3) |
| 7. Tests cover both paths with TTL-expired setup | Task 2 Step 1 (`TTL_CFG`, `TS0`, `NOW`) |
| 8. Legacy entries comment once; digest survives JSON | `test_run_comments_once_on_legacy_hold_entry`, `test_record_verdict_digest_survives_json_roundtrip` |
| 9. Unparseable review never suppressed, stores no digest | Task 2 Step 4 (`unparseable` guard); `test_unparseable_review_is_never_suppressed` |
| 10. Selection invariant | `test_hold_dedup_does_not_change_selection`; ADVANCE/epic/cap code untouched |
| Docs `:608` | Task 4 Step 1 |

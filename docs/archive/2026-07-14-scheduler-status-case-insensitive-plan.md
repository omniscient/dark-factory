# Plan: Case-Insensitive Board Status Matching in `get_items_by_status`

**Issue:** #275
**Spec:** `docs/superpowers/specs/2026-07-14-scheduler-status-case-insensitive-design.md`
**Status:** plan

## Goal

Fix `scheduler.sh`'s `get_items_by_status()` (line 565) so it compares the call site's
`status_name` against a board item's `status` case-insensitively, closing the defect
where sentence-case call-site literals (`"In review"`, `"In progress"`) never match this
instance's Title Case board options (`"In Review"`, `"In Progress"`), which silently
disables Priority 0/0.6/1/1.5, the direct-to-pr end-gate, and `IN_PROGRESS` WIP counting.
The fix is a single choke-point change (mirrors #211's `epic_autopilot.py` shape) plus a
regression test — no call-site or startup-validation changes (both explicitly out of
scope per the spec).

## Architecture

`get_items_by_status()` currently interpolates `status_name` as a raw shell string into a
double-quoted jq program and does an exact `==` compare:

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c "[.items[] | select(.status == \"$status_name\") | select(.content.type == \"Issue\")]"
}
```

It becomes a `jq --arg`-passed value compared via `ascii_downcase` on both sides, with a
`// ""` default guarding the null-status case (an item with no Status field assigned)
against a jq runtime error under `ascii_downcase`, which — since all six call sites
(`scheduler.sh:840-846`) assign via plain `VAR=$(...)` rather than `local`, and
`scheduler.sh` runs under `set -euo pipefail` — would otherwise propagate through `set -e`
and kill the whole poll cycle instead of just leaving one bucket empty:

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c --arg status_name "$status_name" \
    '[.items[] | select((.status // "" | ascii_downcase) == ($status_name | ascii_downcase)) | select(.content.type == "Issue")]'
}
```

All six call sites go through this one function, so the fix applies uniformly with zero
call-site changes. `BLOCKED`/`READY`/`BACKLOG`/`REFINED` are single-word, already
same-case at both ends, so case-insensitive comparison is a no-op for them — no
behavior-change risk for buckets that already work.

## Tech Stack

- Bash + `jq` (`scheduler.sh`)
- Existing bash test harness in `tests/test_scheduler.sh` (`SCHEDULER_SOURCE_ONLY=1 source
  scheduler.sh` + `assert_eq` runner, no pytest involved — this file is pure bash/jq, not
  Python)

## File Structure

| File | Change |
|---|---|
| `scheduler.sh` | `get_items_by_status()` (line 565): switch to `jq --arg` + `ascii_downcase` comparison with null-safe default |
| `tests/test_scheduler.sh` | Add new **Section Q** (after Section P, before Cleanup) with 4 `assert_eq` regression assertions for `get_items_by_status` |

---

## Task 1: Add the failing regression test, then implement the fix

**Files:** `tests/test_scheduler.sh`, `scheduler.sh`

### Step 1.1 — write the failing test

In `tests/test_scheduler.sh`, insert a new section immediately before the `# Cleanup`
section (after Section P ends, i.e. after the `PREFLIGHT_FAIL_OUT` assertions and before
the `# ==========================================\n# Cleanup` block):

```bash
# ==========================================
# Q: get_items_by_status case-insensitive matching (#275)
# ==========================================
echo ""
echo "--- Q: get_items_by_status case-insensitive matching ---"

_Q_FIXTURE='{"items":[
  {"status":"In Review","content":{"number":501,"title":"review item","type":"Issue"}},
  {"status":"In Progress","content":{"number":502,"title":"progress item","type":"Issue"}}
]}'

_Q_IN_REVIEW=$(get_items_by_status "$_Q_FIXTURE" "In review")
assert_eq "Q1: get_items_by_status: call-site 'In review' matches board's 'In Review'" \
  "1" "$(echo "$_Q_IN_REVIEW" | jq 'length')"

_Q_IN_PROGRESS=$(get_items_by_status "$_Q_FIXTURE" "In progress")
assert_eq "Q2: get_items_by_status: call-site 'In progress' matches board's 'In Progress'" \
  "1" "$(echo "$_Q_IN_PROGRESS" | jq 'length')"

export -f get_items_by_status
_Q_FIXTURE_NULL='{"items":[{"status":null,"content":{"number":503,"title":"unassigned item","type":"Issue"}}]}'
_Q_NULL_OUT=$(bash -c 'set -euo pipefail; get_items_by_status "$1" "$2"' _ "$_Q_FIXTURE_NULL" "In review" 2>&1)
_Q_NULL_EXIT=$?
assert_eq "Q3: get_items_by_status: null status does not raise under set -e" "0" "$_Q_NULL_EXIT"
assert_eq "Q4: get_items_by_status: null status excluded from bucket" "0" "$(echo "$_Q_NULL_OUT" | jq 'length')"
```

Notes on the fixture design (per spec Requirements 4-5):
- Both fixture items include `"content":{"type":"Issue"}` — `get_items_by_status` also
  filters on `.content.type == "Issue"` (`scheduler.sh:568`), so omitting it would produce
  an empty bucket for an unrelated reason and give a false pass/fail signal.
- Q3/Q4 use the isolated `bash -c 'set -euo pipefail; ...'` + `export -f` technique
  already established by Section P (`tests/test_scheduler.sh`'s preflight-abort test) to
  exercise `get_items_by_status` under the same `set -euo pipefail` semantics
  `scheduler.sh` itself runs under — this file's own top-level `set -uo pipefail` (no
  `-e`) would not reproduce the crash risk the spec is guarding against.
- Q3/Q4 assert on the *current* (pre-fix) exact-match behavior too, since `null ==
  "In review"` is already `false` with no type error — they exist to lock in that the
  fix's `// ""` guard does not regress this, not to reproduce a currently-observed crash.

### Step 1.2 — verify it fails

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): Q|^Results"
```

Expected output:

```
  FAIL: Q1: get_items_by_status: call-site 'In review' matches board's 'In Review' — expected='1' got='0'
  FAIL: Q2: get_items_by_status: call-site 'In progress' matches board's 'In Progress' — expected='1' got='0'
  PASS: Q3: get_items_by_status: null status does not raise under set -e
  PASS: Q4: get_items_by_status: null status excluded from bucket
Results: 104 passed, 4 failed
```

(Q1/Q2 fail — reproducing the exact reported defect. Q3/Q4 already pass under the
unfixed exact-match comparison, which is expected — they lock in null-safety, not the
reported bug. The other 2 pre-existing failures, `G2` and `I2`, are a baseline condition
unrelated to this change — see Step 1.4.)

### Step 1.3 — implement

In `scheduler.sh`, replace (line 565):

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c "[.items[] | select(.status == \"$status_name\") | select(.content.type == \"Issue\")]"
}
```

with:

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c --arg status_name "$status_name" \
    '[.items[] | select((.status // "" | ascii_downcase) == ($status_name | ascii_downcase)) | select(.content.type == "Issue")]'
}
```

No other line in this function or its six call sites (`scheduler.sh:840-846`) changes —
the call sites already pass plain literal strings compatible with `jq --arg`.

### Step 1.4 — verify it passes

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): Q|^Results"
```

Expected output:

```
  PASS: Q1: get_items_by_status: call-site 'In review' matches board's 'In Review'
  PASS: Q2: get_items_by_status: call-site 'In progress' matches board's 'In Progress'
  PASS: Q3: get_items_by_status: null status does not raise under set -e
  PASS: Q4: get_items_by_status: null status excluded from bucket
Results: 106 passed, 2 failed
```

The remaining 2 failures (`G2: advance: set_board_status REFINED`, `I2: advance:
set_board_status READY`) are a pre-existing baseline condition on this branch, verified
present against an unmodified `scheduler.sh`/`tests/test_scheduler.sh` during plan
authoring (before Section Q or the fix existed) and unrelated to `get_items_by_status` —
out of scope for this ticket to fix.

Also run the Python suite to confirm no cross-language regression (this change touches no
Python code, but CI runs this as part of the same gate):

```bash
python -m pytest tests/ -v
```

Expected output: all tests pass, no `FAILED` lines (unaffected by this bash-only change).

### Step 1.5 — commit

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "fix(scheduler): case-insensitive status matching in get_items_by_status (#275)"
```

---

## Validation summary (maps to spec's Requirements)

- **Requirement 1** (case-insensitive comparison fixing `IN_REVIEW`/`IN_PROGRESS`, and as
  a side effect hardening `BLOCKED`/`READY`/`BACKLOG`/`REFINED`): Task 1, Step 1.3 — one
  shared function, all six call sites benefit uniformly.
- **Requirement 2** (null-safe default before `ascii_downcase`): Task 1, Step 1.3 (`.status
  // ""`); verified by Step 1.1/1.2/1.4's Q3/Q4 assertions.
- **Requirement 3** (fix scoped to the shared helper only, not the four call-site
  literals): Task 1, Step 1.3 diff touches only `get_items_by_status`'s body; no call-site
  edits anywhere in this plan.
- **Requirement 4** (regression test reproducing the exact call-site/board casing
  mismatch): Task 1, Step 1.1 (Q1/Q2 fixture uses `"In Review"`/`"In Progress"` board
  casing against `"In review"`/`"In progress"` call-site literals).
- **Requirement 5** (test lives inside `tests/test_scheduler.sh`, using its existing
  `SCHEDULER_SOURCE_ONLY=1` + `assert_eq` harness, with `content.type: "Issue"` in the
  fixture): Task 1, Step 1.1.
- **Requirement 6** (startup board-schema validation out of scope): not implemented by
  this plan — no task touches startup validation, `image_check`, or the WIP-limits fetch.

## Known limitations (carried from spec, no code action)

- Startup-time board-schema validation is explicitly out of scope (spec Alternatives
  Considered #3); a follow-up ticket is recommended but not filed by this plan (refine/plan
  phases don't file issues).
- `tests/test_scheduler.sh` is not currently wired into `.github/workflows/ci.yml` or any
  `.factory/hooks/*` gate (spec Open Questions) — a pre-existing gap affecting the whole
  file, not introduced or fixed by this plan.
- The two pre-existing failures (`G2`, `I2`) in `tests/test_scheduler.sh` are unrelated to
  this change and are not fixed by this plan — flagged for awareness only.

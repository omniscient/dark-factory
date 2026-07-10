# Plan: Robust dependency-ref parsing in `dependencies_met()` (#204)

**Issue:** #204 · **Spec:** [`docs/superpowers/specs/2026-07-07-scheduler-dependency-parsing-design.md`](../specs/2026-07-07-scheduler-dependency-parsing-design.md)

## Goal

Replace the single-regex dependency extraction in `dependencies_met()`
(`scheduler.sh:623-658`) with a small preprocessing pipeline that:

- strips fenced code blocks and inline code spans before scanning (kills the
  fail-closed bug that stranded #389 and this ticket itself),
- tolerates bold/italic markup around the `Depends on:` label (kills the
  fail-open miss on markethawk #551-#559),
- recognizes `## Blocked by`-style bullet sections at any heading level
  (kills the fail-open miss on markethawk #440-#446), and
- captures every `#NNN` ref on a qualifying line, not just the first.

`dependencies_met()`'s external contract (signature, return codes, log line
formats, the per-dep board/`gh` resolution loop at `:633-658`) is unchanged.
Everything stays in `scheduler.sh` — no new file, no `scheduler_lib.sh`
extraction (that belongs to the separate, larger #185 refactor; see the
`[AVOID]` memory entry from this ticket's own refinement, which the spec
already accounts for).

## Architecture

A single new pure helper, `_scan_body_for_deps()`, is inserted immediately
above `dependencies_met()` inside the existing `SCHEDULER_SOURCE_ONLY`
sourceable region. `dependencies_met()` changes by exactly one line: its
`deps=$(...)` assignment now calls the helper instead of running the raw
regex inline. The helper is built up over three tasks, each ending in a
fully working, independently testable state:

1. **Task 1** — fence + inline-code stripping (extraction still
   single-match, same shape as today).
2. **Task 2** — bold/italic tolerance (strip literal `*`) + switch
   extraction to capture every ref on a qualifying line (multi-ref support
   for `Depends on:` lines).
3. **Task 3** — `## Blocked by` section scan, combined with the Task 2
   output; final accepted-formats doc comment added once the function
   reaches its complete form.

**Correction to the spec's illustrative code:** `scheduler.sh:2` sets
`set -euo pipefail`. The spec's sketch (lines 173-197 of the spec doc)
assigns `plain_deps=$(...)` / `blocked_deps=$(...)` / the final combine
without a trailing `|| true`. Each of those pipelines ends in a `grep` that
legitimately finds zero matches on ordinary, correct input (e.g. a body
with a `Blocked by` section but no `Depends on:` line has zero matches for
the `plain_deps` pipeline; a body with no dependencies at all, the N1 case,
has zero matches for *all three*). Under `pipefail`, `grep` returning 1
propagates through the pipeline's exit status, and a bare
`var=$(pipeline)` assignment inheriting a non-zero status trips `set -e`
and kills the calling script — this is precisely why the *existing* line
629 already ends `|| true`. Every new pipeline stage below restores that
`|| true` the spec's sketch omitted. This is a bug-for-bug-compatibility
fix within the spec's chosen approach, not a deviation from it.

## Tech Stack

Bash (GNU `grep -P`, `sed -E ... /I`, `awk`, `tr`) — same toolchain
`scheduler.sh` already relies on elsewhere. No new dependencies. Tests use
the existing `tests/test_scheduler.sh` harness (`SCHEDULER_SOURCE_ONLY=1`
sourcing, the `assert_eq` runner, the section-N `gh()`/board-fixture
stubs).

## File Structure

| File | Change |
|---|---|
| `scheduler.sh` | Add `_scan_body_for_deps()` (built incrementally across Tasks 1-3, final doc comment in Task 3); rewire `dependencies_met()`'s `deps=$(...)` line to call it |
| `tests/test_scheduler.sh` | Add cases N9-N19 to section N (inserted between existing N8 and N9); relocate existing N9 → N20 so the `gh()`-body-fetch-fail override + "Restore global gh stub" stay last; extend the shared `gh()` stub + fixture vars with a dep-202 branch (Task 3) |

**Important placement detail, verified by dry-running the whole suite (see
"Verification" below):** the existing N9 case (`body fetch fails`)
deliberately overrides `gh()` and then restores it to a bare
`gh() { return 0; }` stub as the *last* thing section N does — that
restored stub is what sections O onward rely on. It does **not** route by
issue number, so any new dependencies_met()-level test placed *after* the
restore silently gets an empty body/state from every `gh` call regardless
of the `_N_BODY`/`_N_DEP*_STATE` fixtures set — the test would pass or
fail for the wrong reason (confirmed by hand: doing this makes every
"blocks" assertion below fail, and every "doesn't block" assertion pass
vacuously). All new N-cases (Tasks 1-3) must therefore be inserted
**between the existing N8 and N9**, using the routing `gh()` stub that's
already active there; the original N9 shifts down to become the new N20,
keeping the override-then-restore sequence last.

---

## Task 1: Fence & inline-code stripping

Kills the fail-closed bug (quoted/illustrative refs in code fences or
inline code counting as real deps — the #389 and this-ticket's-own-body
incident).

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Steps

1. **Write failing tests.** In `tests/test_scheduler.sh`, insert
   **between the existing N8 block and the existing N9 block** (i.e.
   immediately after the `assert_eq "N8: ..."` line, before the `# N9: body
   fetch fails` comment — see the placement note in File Structure above:
   this must land before N9's `gh()` override/restore, not after it):

   ```bash

   # N9: fenced fake dep — closed fence does not count as a dependency
   _N_BODY="$(printf '```\nDepends on: #999\n```\n')"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
   assert_eq "N9: fenced fake dep → returns 0 (no real dep)" "0" "$_N_RET"

   # N10: unclosed fence — everything after an unclosed ``` is dropped
   _N_BODY="$(printf 'Some notes.\n```\nDepends on: #999\n')"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
   assert_eq "N10: unclosed fence → returns 0 (no real dep)" "0" "$_N_RET"

   # N11: inline code span — backtick-quoted example does not count
   _N_BODY="See \`Depends on: #999\` for the old format."
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
   assert_eq "N11: inline code span → returns 0 (no real dep)" "0" "$_N_RET"
   ```

   Then rename the existing `N9` case (the `body fetch fails` one, and its
   `gh()` override/restore) to `N20` — its two `assert_eq` description
   strings and log messages change from `N9:` to `N20:`, its position in
   the file does not otherwise move (it stays as the last case in section
   N, immediately before `# Restore global gh stub`).

2. **Verify RED.** Run:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -20
   ```

   Expected: `N9`, `N10`, `N11` report `FAIL — expected='0' got='1'`. The
   current regex has no fence/code-span awareness, so `#999` is extracted
   as a real (off-board, `gh` returns nothing for issue 999 in the shared
   stub) dependency and `dependencies_met` incorrectly returns 1.

3. **Implement.** In `scheduler.sh`, replace lines 622-629:

   ```bash
   # --- Dependency checking ---
   dependencies_met() {
     local issue_num="$1"
     local board_items="$2"
     local body
     body=$(gh issue view "$issue_num" --repo "$FACTORY_REPO_SLUG" --json body -q '.body' 2>/dev/null) || return 0
     local deps
     deps=$(echo "$body" | grep -oP 'Depends on:\s*#\K\d+' || true)
   ```

   with:

   ```bash
   # --- Dependency checking ---
   _scan_body_for_deps() {
     local body="$1"
     local stripped
     stripped=$(printf '%s\n' "$body" | awk '
       /^```/ { in_fence = !in_fence; next }
       in_fence { next }
       { print }
     ' | sed -E 's/`[^`]*`//g')
     printf '%s\n' "$stripped" | grep -oP 'Depends on:\s*#\K\d+' || true
   }

   dependencies_met() {
     local issue_num="$1"
     local board_items="$2"
     local body
     body=$(gh issue view "$issue_num" --repo "$FACTORY_REPO_SLUG" --json body -q '.body' 2>/dev/null) || return 0
     local deps
     deps=$(_scan_body_for_deps "$body")
   ```

4. **Verify GREEN.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -30
   ```

   Expected: `N1`-`N11` and `N20` (the renamed body-fetch-fail case) all
   report `PASS`. Two pre-existing, unrelated failures (`G2`, `I2` —
   `STATUS_REFINED`/`STATUS_READY: unbound variable`) are expected and
   present on `main` before this change; do not attempt to fix them here.

5. **Commit.**

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): strip code fences and inline code before dependency-ref scanning (#204)"
   ```

---

## Task 2: Bold/italic tolerance + multi-ref lines

Kills the fail-open miss on bold-label declarations (markethawk
#551-#559) and the first-match-only limitation on multi-ref lines.

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Steps

1. **Write failing tests.** Append after N11, before N20, in
   `tests/test_scheduler.sh`:

   ```bash

   # N12: bold label, bold wraps label+colon — **Depends on:** #200
   _N_BODY="**Depends on:** #200"
   > "$STUB_LOG"
   _N_OUTPUT=$(dependencies_met "100" "$_BOARD_200_WIP" 2>&1) && _N_RET=0 || _N_RET=1
   assert_eq "N12: bold label (**Depends on:**) → returns 1" "1" "$_N_RET"
   assert_eq "N12: bold label → dep_gate logged" \
     "1" "$(echo "$_N_OUTPUT" | grep -c 'dep_gate' || true)"

   # N13: bold label, bold wraps only the word — **Depends on**: #200
   _N_BODY="**Depends on**: #200"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
   assert_eq "N13: bold label (**Depends on**:) → returns 1" "1" "$_N_RET"

   # N14: plain label, bold ref — Depends on: **#200**
   _N_BODY="Depends on: **#200**"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
   assert_eq "N14: bold ref (Depends on: **#200**) → returns 1" "1" "$_N_RET"

   # N15: multi-ref line — Depends on: #200, #201 blocks on both (mirrors N7)
   _N_BODY="Depends on: #200, #201"
   _N_DEP201_STATE="OPEN"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
   assert_eq "N15: multi-ref line, second off-board OPEN → returns 1" "1" "$_N_RET"
   ```

2. **Verify RED.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -30
   ```

   Expected: N12-N14 `FAIL — expected='1' got='0'` (the label regex
   `Depends on:\s*#` does not match through the `**`/`*` markup, so `deps`
   comes back empty and the function short-circuits at `return 0`). N15
   `FAIL — expected='1' got='0'` (the current single-`\K` regex only
   captures `#200`, which is Done on the board, so nothing blocks).

3. **Implement.** In `scheduler.sh`, replace the `_scan_body_for_deps` body
   added in Task 1 with:

   ```bash
   _scan_body_for_deps() {
     local body="$1"
     local stripped
     stripped=$(printf '%s\n' "$body" | awk '
       /^```/ { in_fence = !in_fence; next }
       in_fence { next }
       { print }
     ' | sed -E 's/`[^`]*`//g' | tr -d '*')

     local plain_deps
     plain_deps=$(printf '%s\n' "$stripped" \
       | grep -inE 'depends[[:space:]]+on[[:space:]]*:' \
       | sed -E 's/.*depends[[:space:]]+on[[:space:]]*://I' \
       | grep -oP '#\K[0-9]+' || true)

     printf '%s\n' "$plain_deps"
   }
   ```

   Note the `|| true` on the `plain_deps` pipeline — required so a body
   with no `Depends on:` line at all (e.g. N1's `"No dependencies here"`)
   doesn't trip `set -e` on the `grep -oP` finding zero matches (see
   Architecture note above).

4. **Verify GREEN.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -40
   ```

   Expected: `N1`-`N15` and `N20` all `PASS`. Same two pre-existing `G2`/
   `I2` failures as Task 1, unrelated to this change.

5. **Commit.**

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): tolerate bold/italic Depends-on markup, capture every ref on a line (#204)"
   ```

---

## Task 3: `## Blocked by` bullet sections + doc comment

Kills the fail-open miss on `Blocked by`-section declarations
(markethawk #440-#446). Adds the accepted-formats doc comment now that the
function has reached its final form.

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Steps

1. **Extend the shared test fixtures.** The three-marker test below needs a
   third stubbed dependency issue (`#202`) alongside the existing `#200`/
   `#201`. In `tests/test_scheduler.sh`, in the section-N shared setup:

   - add `_N_DEP202_STATE=""` next to the existing `_N_DEP201_STATE=""`
     (current line 772):

     ```bash
     _N_DEP201_STATE=""
     _N_DEP202_STATE=""
     ```

   - add a `view 202` branch to the shared `gh()` stub (current lines
     775-787), alongside the existing `view 201` branch:

     ```bash
     gh() {
       echo "gh $*" >> "$STUB_LOG"
       if echo "$*" | grep -qE "view 100( |$)"; then
         printf '%s\n' "$_N_BODY"; return 0
       fi
       if echo "$*" | grep -qE "view 201( |$)"; then
         printf '%s\n' "$_N_DEP201_STATE"; return 0
       fi
       if echo "$*" | grep -qE "view 202( |$)"; then
         printf '%s\n' "$_N_DEP202_STATE"; return 0
       fi
       if echo "$*" | grep -qE "view 200( |$)"; then
         printf '%s\n' "$_N_DEP200_STATE"; return $_N_DEP200_GH_EXIT
       fi
       return 0
     }
     export -f gh
     ```

   This is purely additive — N1-N15 and N20 are unaffected.

2. **Write failing tests.** Append after N15, before N20, in
   `tests/test_scheduler.sh`:

   ```bash

   # N16: Blocked-by section, all three bullet markers (-, *, +), mixed
   # resolution paths — proves each marker is scanned and refs are checked
   _N_BODY="$(printf '## Blocked by\n- #200\n* #201\n+ #202\n')"
   _N_DEP201_STATE="CLOSED"
   _N_DEP202_STATE="OPEN"
   > "$STUB_LOG"
   _N_OUTPUT=$(dependencies_met "100" "$_BOARD_200_DONE" 2>&1) && _N_RET=0 || _N_RET=1
   assert_eq "N16: Blocked-by (-/*/+  markers) → returns 1" "1" "$_N_RET"
   assert_eq "N16: Blocked-by → blocked_by=#202 logged" \
     "1" "$(echo "$_N_OUTPUT" | grep -c 'blocked_by=#202' || true)"

   # N17: lowercase, deeper heading level — ### blocked by
   _N_BODY="$(printf '### blocked by\n- #200\n')"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
   assert_eq "N17: lowercase '### blocked by' heading → returns 1" "1" "$_N_RET"

   # N18: a following heading of any level ends the section
   _N_BODY="$(printf '## Blocked by\n- #200\n## Other\n- #201\n')"
   _N_DEP201_STATE="OPEN"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
   assert_eq "N18: heading ends Blocked-by section → returns 0 (#201 not scanned)" "0" "$_N_RET"

   # N19: multi-ref bullet under Blocked by — mirrors N7 shape
   _N_BODY="$(printf '## Blocked by\n- #200, #201\n')"
   _N_DEP201_STATE="OPEN"
   > "$STUB_LOG"
   dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
   assert_eq "N19: multi-ref bullet, second off-board OPEN → returns 1" "1" "$_N_RET"
   ```

3. **Verify RED.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -30
   ```

   Expected: N16, N17, N19 `FAIL — expected='1' got='0'` — `_scan_body_for_deps`
   has no `Blocked by` handling yet, so `deps` never contains
   #200/#201/#202 and every one of these bodies is treated as
   dependency-free. N18 (`heading ends Blocked-by section → returns 0`) is
   a negative assertion — it already **passes vacuously** at this stage,
   for the wrong reason (no `Blocked by` handling at all means #201 is
   never extracted regardless of section-termination logic, not because
   the termination rule works). That is expected and fine; N18 only
   becomes a meaningful, section-termination-driven assertion once Task
   3's implementation step below lands and N16/N17/N19 go green alongside
   it. Do not treat N18 "passing at RED" as a problem.

4. **Implement.** In `scheduler.sh`, replace the `_scan_body_for_deps`
   function (from Task 2) with its final form, adding the doc comment
   directly above it:

   ```bash
   # Accepted dependency declaration formats (see #204):
   #   - Plain:            Depends on: #123
   #   - Bold/italic:      **Depends on:** #123 / **Depends on**: #123 /
   #                       Depends on: **#123** / *Depends on:* #123
   #                       (any placement of * around the label/colon/ref)
   #   - Multi-ref line:   Depends on: #123, #124
   #   - Blocked-by block: a heading (any level #-######, case-insensitive) whose
   #                       text is "Blocked by", followed by -/*/+ bullets, each
   #                       possibly containing multiple #NNN refs, until the next
   #                       heading of any level
   # Text inside fenced code blocks (```) or inline code spans (`...`) is never
   # scanned — quoted/illustrative refs must not be treated as real dependencies.
   # An unclosed fence is treated as open through end-of-body (fail closed).
   _scan_body_for_deps() {
     local body="$1"
     local stripped
     stripped=$(printf '%s\n' "$body" | awk '
       /^```/ { in_fence = !in_fence; next }
       in_fence { next }
       { print }
     ' | sed -E 's/`[^`]*`//g' | tr -d '*')

     local plain_deps
     plain_deps=$(printf '%s\n' "$stripped" \
       | grep -inE 'depends[[:space:]]+on[[:space:]]*:' \
       | sed -E 's/.*depends[[:space:]]+on[[:space:]]*://I' \
       | grep -oP '#\K[0-9]+' || true)

     local blocked_deps
     blocked_deps=$(printf '%s\n' "$stripped" | awk '
       /^#{1,6}[[:space:]]*/ {
         if (tolower($0) ~ /^#{1,6}[[:space:]]*blocked by/) { insec = 1 } else { insec = 0 }
         next
       }
       insec { print }
     ' | grep -oP '#\K[0-9]+' || true)

     printf '%s\n%s\n' "$plain_deps" "$blocked_deps" | grep -v '^$' || true
   }
   ```

   Note the `|| true` on both `blocked_deps` (a body with no `Blocked by`
   section, e.g. every plain/bold-label test above, must not trip `set -e`)
   and on the final combine (a body with *no* deps at all, e.g. N1, would
   otherwise have `grep -v '^$'` match zero lines and trip `set -e`).

5. **Verify GREEN.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -50
   ```

   Expected: all of N1-N20 `PASS`. Same two pre-existing `G2`/`I2`
   failures as Tasks 1-2, unrelated to this change (confirmed present on
   `main` before any of this work started).

6. **Commit.**

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): recognize Blocked-by bullet sections, document accepted dep formats (#204)"
   ```

---

## Task 4: Full regression pass

Confirms Requirement 5 (existing plain-format behavior — the original
N1-N8, plus the relocated body-fetch-fail case, now N20 — is unchanged)
and that no other section of the suite regressed from the
`_scan_body_for_deps` extraction / `dependencies_met` rewiring.

**Files:** none (verification only)

### Steps

1. **Run the full scheduler test suite:**

   ```bash
   bash tests/test_scheduler.sh
   ```

   Expected: `Results: 99 passed, 2 failed` — the 2 failures are the
   pre-existing `G2`/`I2` (`STATUS_REFINED`/`STATUS_READY: unbound
   variable`) cases, confirmed present on `main` before this branch's
   changes and unrelated to `dependencies_met()`. All 20 `N*` cases pass.
   This exact task/plan (helper implementation + full N1-N20 test suite +
   the N9/N20 reordering above) was dry-run end-to-end against a scratch
   copy of the repo while writing this plan — the numbers above are
   observed, not projected. If your run shows any *other* failure, stop
   and treat it as a real regression, not something to wave off as
   "pre-existing."

2. **Run the full repo test suite** (per `.github/workflows/ci.yml`) to
   confirm no cross-file regression:

   ```bash
   bash tests/test_identity.sh
   bash tests/test_hooks.sh
   bash tests/test_smoke_gate.sh
   bash tests/test_run_compose.sh
   ```

   Expected: all pass (these files import/call `scheduler.sh` in ways
   unrelated to `dependencies_met`, so they should be unaffected — this
   step is a safety net, not expected to surface anything).

3. **Sanity-check the diff is scoped as planned:**

   ```bash
   git diff main...HEAD --stat
   ```

   Expected: only `scheduler.sh` and `tests/test_scheduler.sh` touched.

No commit in this task — it is verification-only. If step 1 or 2 surfaces
a failure, fix it under the task where the regression was introduced (do
not amend; add a new commit) before proceeding.

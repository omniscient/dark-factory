# Implementation Plan: Smoke-gate refuses the run (never latches main-red) when a target has no hook

**Issue:** #436

**Spec:** `docs/superpowers/specs/2026-09-27-issue-436-smoke-gate-refuse-dispatch-no-hook-design.md`

**Goal:** When `run_hook` finds no `.factory/hooks/smoke-gate` *present* (absent, a
directory, or zero-byte, by its own `[ -f ] && [ -s ]` test), the run is refused with an
actionable, durable signal. The MarketHawk tsc/python-import default does not run, and the
`main-is-red` sentinel and regression ticket are never touched. A present hook, executable or
not, keeps today's semantics unchanged.

**Architecture:** Add a new `_smoke_hook_missing` function to `smoke_gate.sh`. It logs to
stderr, appends one durable `hook-warnings.log` line, posts a `SMOKE_HOOK_MISSING_MARKER`
ticket comment when `ISSUE_NUM` is set, emits a health event last, and then does `return 1`,
never `exit`. `scripts/hooks.sh::run_hook`'s no-hook `smoke-gate` arm calls it instead of
`_default_smoke_gate` and returns its non-zero rc even without `--gate`. The failure then
propagates through `entrypoint.sh`'s existing `trap on_failure ERR` unchanged. `entrypoint.sh`
itself is not edited.

**Tech Stack:** Bash (`smoke_gate.sh`, `scripts/hooks.sh`), bash test scripts
(`tests/test_smoke_gate.sh`, `tests/test_hooks.sh`, both already in `.github/workflows/ci.yml`
lines 16-17), Markdown docs.

---

## Precondition: branch must contain #454

The spec's line references (`scripts/hooks.sh:31` `[ -f ] && [ -s ]`, `hooks.sh:67`/`:71`,
`smoke_gate.sh:161-164`, `README.md:232`, `docs/onboarding-new-target.md:87-90`/`:120-121`,
`tests/test_hooks.sh` cases 6-10) all describe `origin/main` at `6672bd5` (PR #454, #438).
This refine branch forked at `dba2b0c`, *before* #454. The implementation branch
(`feat/issue-436-*`, forked from `main`) must contain it:

```bash
git fetch origin main && git merge-base --is-ancestor 6672bd5 HEAD && echo "has #454"
```

Expected: `has #454`. If it prints nothing, run `git merge origin/main` first. Every edit
below anchors on post-#454 text and will not match otherwise.

Baseline (before any edit) on that branch:

```bash
bash tests/test_hooks.sh 2>/dev/null | tail -1        # → PASS
bash tests/test_smoke_gate.sh 2>/dev/null | tail -1   # → Results: 27 passed, 0 failed
```

## File Structure

| File | Change | Why |
|---|---|---|
| `smoke_gate.sh` | Add `SMOKE_CORE_CLI` + `SMOKE_HOOK_MISSING_MARKER` vars; add `_smoke_hook_missing()`; rewrite the stale doc block above `_default_smoke_gate` (the "Parity invariant: MarketHawk needs zero hooks" sentence). `_smoke_check_main`, `_default_smoke_gate`'s body, `run_smoke_gate`, `_smoke_on_red`, `_smoke_on_green` unchanged. | Req 2-4, Architecture |
| `scripts/hooks.sh` | No-hook `smoke-gate` arm → `_smoke_hook_missing "$@" \|\| rc=$?; return "$rc"` plus the `--gate`-only comment; header comment lines 2, 10-11, 12 and 20 updated to match | Architecture "entire behavioral pivot"; test_hooks bullet 4 |
| `tests/test_smoke_gate.sh` | New `assert_file_contains` helper; new Phase 7 exercising `_smoke_hook_missing` directly | Tests bullet 1 |
| `tests/test_hooks.sh` | Re-author case 9 (refusal instead of `DEFAULT_CHECKS == 3`), add 9d (no `--gate`), re-baseline `WARN_LINES` for case 10, add case 11 (non-exec carve-out). Cases 1-8 and 8b byte-for-byte unchanged. | Tests bullet 2 |
| `README.md` | Product-agnostic claim (`:26-27`); `Hooks` table smoke-gate row (`:232`); drop the "...or the built-in default" clause in the "smoke-gate is check-only" paragraph (`:253-255`) because it becomes false | Architecture doc updates |
| `docs/onboarding-new-target.md` | Step 3b paragraph (`:87-90`); step 3c sentence (`:120-121`) | Architecture doc updates |

**Spec reconciliation (state dir):** Requirement 2 says the no-hook path "must not write
to `SMOKE_STATE_DIR`". Requirement 4 requires a durable line in
`${SCHEDULER_STATE_DIR}/hook-warnings.log`. `SMOKE_STATE_DIR` *is*
`${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}` (`smoke_gate.sh:11`), so both cannot hold
literally. This plan reads Requirement 2 as scoped to the main-red state it protects: the
`main-is-red`, `main-red-last-recheck` and `main-is-red-issue` files, which are never written
and are asserted absent in Phase 7 and case 9. The only write is Requirement 4's append to
#438's existing `hook-warnings.log`, under #438's guards.

**Explicitly not touched:** `entrypoint.sh` (the existing `on_failure` handles the non-zero
rc), `scheduler.sh`, `templates/new-target/.factory/hooks/smoke-gate` (under the adapter's
`.factory/hooks/` hard-exclude; its stale comment is a docs follow-up per the spec),
`config/`, any `gate_*`/breaker/budget surface.

Memory lessons applied: none of the loaded memory entries (`codebase-patterns.md`,
`architecture.md`) apply to this change. The `git diff origin/main HEAD` two-dot OOS check is
used in Task 4 step 5.

---

## Task 1: `_smoke_hook_missing` in `smoke_gate.sh` (TDD via `tests/test_smoke_gate.sh` Phase 7)

**Files:** `tests/test_smoke_gate.sh`, `smoke_gate.sh`

- [ ] **Step 1: Add the `assert_file_contains` helper.** In `tests/test_smoke_gate.sh`,
  directly after the existing `assert_file_absent() { … }` function (4 lines ending
  `FAILED=$((FAILED+1)); fi` / `}`), insert:

```bash
assert_file_contains() {
  if grep -qF -- "$2" "$3" 2>/dev/null; then echo "  PASS: $1"; PASSED=$((PASSED+1))
  else echo "  FAIL: $1 — '$2' not found in $3" >&2; FAILED=$((FAILED+1)); fi
}
```

- [ ] **Step 2: Add Phase 7.** In `tests/test_smoke_gate.sh`, immediately **before** the
  line `# ---- Summary ----` (after `GH_LIST_OUTPUT=""; export GH_LIST_OUTPUT` and its
  following blank line), insert:

```bash
# ---- Phase 7: No hook present (#436) — refuse the run, never latch main-red ----
echo ""
echo "--- Phase 7: _smoke_hook_missing refuses without touching main-red state (#436) ---"
TSC_FAIL=0; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-is-red-issue" \
      "${SMOKE_STATE_DIR}/main-red-last-recheck" "${SMOKE_STATE_DIR}/hook-warnings.log"
# Capture the ticket comment body: _smoke_hook_missing deletes its --body-file after
# posting, so the stub copies it out. NOTE: this python3 override persists to the end.
MISSING_BODY=$(mktemp /tmp/smoke-missing-body-XXXXXX)
MISSING_ERR=$(mktemp /tmp/smoke-missing-err-XXXXXX)
# shellcheck disable=SC2317
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  local prev="" a
  for a in "$@"; do
    if [ "$prev" = "--body-file" ]; then cat "$a" >> "$MISSING_BODY"; fi
    prev="$a"
  done
  if echo "$*" | grep -q "tracker create"; then echo "999"; fi
  return 0
}
# $( ) is a subshell: an `exit` inside the function would skip the echo, so
# "returned:1" proves it RETURNS non-zero (entrypoint.sh's ERR trap never fires on exit).
MISSING_OUT=$( { ISSUE_NUM=436 _smoke_hook_missing 2> "$MISSING_ERR"; echo "returned:$?"; } )
assert_eq "no-hook path returns 1 (return, never exit)" "returned:1" "$(printf '%s\n' "$MISSING_OUT" | tail -1)"
assert_file_absent "no main-is-red sentinel on missing hook" "${SMOKE_STATE_DIR}/main-is-red"
assert_file_absent "no main-is-red-issue file on missing hook" "${SMOKE_STATE_DIR}/main-is-red-issue"
assert_file_absent "no recheck throttle stamp on missing hook" "${SMOKE_STATE_DIR}/main-red-last-recheck"
assert_eq "no regression ticket created on missing hook" "0" "$(grep -c "tracker create" "$STUB_LOG" 2>/dev/null || true)"
assert_eq "no gh issue calls on missing hook" "0" "$(grep -c "^gh issue" "$STUB_LOG" 2>/dev/null || true)"
assert_eq "no per-ticket retry/block/board calls from the hook itself" "0" \
  "$(grep -cE "increment_retry|trip_to_blocked|set_board_status" "$STUB_LOG" 2>/dev/null || true)"
for NEEDLE in "$CLONE_DIR/.factory/hooks/smoke-gate" "templates/new-target/.factory/hooks/smoke-gate" \
              "docs/onboarding-new-target.md step 3b" "NOT checked" "NOT been marked red"; do
  assert_file_contains "stderr names: $NEEDLE" "$NEEDLE" "$MISSING_ERR"
done
assert_eq "one durable hook-warnings.log line" "1" \
  "$(grep -c "smoke-gate-hook-missing $CLONE_DIR/.factory/hooks/smoke-gate issue=436" "${SMOKE_STATE_DIR}/hook-warnings.log" 2>/dev/null || true)"
assert_eq "marker comment posted once on the ticket" "1" \
  "$(grep -cF "tracker comment --id 436 --marker <!-- df-smoke-hook-missing -->" "$STUB_LOG" 2>/dev/null || true)"
for NEEDLE in "<!-- df-smoke-hook-missing -->" ".factory/hooks/smoke-gate" \
              "templates/new-target/.factory/hooks/smoke-gate" "docs/onboarding-new-target.md" "step 3b"; do
  assert_file_contains "comment body names: $NEEDLE" "$NEEDLE" "$MISSING_BODY"
done
assert_eq "health event emitted once" "1" \
  "$(grep -c "run-record health-event .*--event factory.smoke_gate.hook_missing" "$STUB_LOG" 2>/dev/null || true)"
assert_eq "health event is the LAST external call" "1" \
  "$(tail -1 "$STUB_LOG" | grep -c "factory.smoke_gate.hook_missing" || true)"

# No ticket context (recheck): log + health event only, no comment.
> "$STUB_LOG"
( unset ISSUE_NUM; _smoke_hook_missing ) 2>/dev/null
assert_eq "no comment without ISSUE_NUM" "0" "$(grep -c "tracker comment" "$STUB_LOG" 2>/dev/null || true)"
assert_eq "health event still emitted without ISSUE_NUM" "1" "$(grep -c "factory.smoke_gate.hook_missing" "$STUB_LOG" 2>/dev/null || true)"

# Absent state dir: never mkdir it (CI asserts /var/lib/dark-factory stays empty), still refuse.
NO_STATE="${SMOKE_STATE_DIR}/does-not-exist"
NS_RC=0
( SCHEDULER_STATE_DIR="$NO_STATE"; _smoke_hook_missing ) 2>/dev/null || NS_RC=$?
assert_eq "absent state dir: still returns 1" "1" "$NS_RC"
assert_eq "absent state dir: never created" "no" "$([ -e "$NO_STATE" ] && echo yes || echo no)"
rm -f "$MISSING_BODY" "$MISSING_ERR"

```

- [ ] **Step 3: Verify it fails.**

```bash
bash tests/test_smoke_gate.sh 2>&1 | tail -1; echo "rc=${PIPESTATUS[0]}"
```

Expected: `Results: 35 passed, 17 failed` and `rc=1`. The first Phase 7 failure reads
`FAIL: no-hook path returns 1 (return, never exit) — expected='returned:1' got='returned:127'`,
because the function does not exist yet.

- [ ] **Step 4: Add the two variables.** In `smoke_gate.sh`, directly after the existing line
  `PROVIDERS_CLI="$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/factory_core/providers/cli.py"`
  (line 13), insert:

```bash
SMOKE_CORE_CLI="$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/factory_core/cli.py"
# Ticket-comment marker for the no-hook refusal (#436) — same style as entrypoint.sh's
# FACTORY_FAILURE_MARKER; upserted, so repeat dispatches update one comment.
SMOKE_HOOK_MISSING_MARKER="<!-- df-smoke-hook-missing -->"
```

  (In a run, `hooks.sh` is sourced from `/opt/dark-factory/scripts/hooks.sh`, so this resolves
  to `/opt/dark-factory/scripts/factory_core/cli.py`, the same baked copy
  `entrypoint.sh:157` uses for its health event.)

- [ ] **Step 5: Add `_smoke_hook_missing` and fix the stale doc block.** In `smoke_gate.sh`,
  replace these four comment lines above `_default_smoke_gate() {` (post-#454 lines 161-164):

```bash
# Built-in default for the smoke-gate hook — called by hooks.sh run_hook when no
# target repo ships .factory/hooks/smoke-gate. Contains today's MarketHawk checks
# (tsc + python import). Parity invariant: MarketHawk needs zero hooks.
# Returns 0 on green (proceed); exits 0 on red (clean halt, no per-ticket failure).
```

  with the following block. `_default_smoke_gate() {` and everything after it stays
  unchanged.

```bash
# No-hook refusal (#436) — called by hooks.sh run_hook when the target has no
# .factory/hooks/smoke-gate PRESENT (absent, a directory, or zero-byte). A missing hook
# is a target misconfiguration, not a red main: this never writes the main-red state
# files, never calls _smoke_on_red/_smoke_on_green, and files no regression ticket.
# Signals: stderr; one durable line in ${SCHEDULER_STATE_DIR}/hook-warnings.log
# (dispatch is `run -d --rm`, so stderr dies with the container); a marker comment on
# the ticket when ISSUE_NUM is set; a health event LAST (guarded, never fatal).
# Returns 1 so entrypoint.sh's ERR trap (on_failure) fails the run. Must `return`,
# never `exit`: an ERR trap does not fire on exit, so an exit here would strand the
# ticket In Progress.
_smoke_hook_missing() {
  local hook="${CLONE_DIR:-}/.factory/hooks/smoke-gate"
  {
    echo "ERROR: [smoke_gate] smoke-gate-hook-missing path=${hook} — the target declares no smoke-gate hook; refusing this run."
    echo "[smoke_gate] main was NOT checked and has NOT been marked red (no main-is-red sentinel, no regression ticket)."
    echo "[smoke_gate] Fix: commit a non-empty, executable .factory/hooks/smoke-gate to the target repo. Start from templates/new-target/.factory/hooks/smoke-gate; see docs/onboarding-new-target.md step 3b."
  } >&2
  # Durable trace — #438's guards verbatim: only if the state dir exists, never mkdir
  # (CI asserts /var/lib/dark-factory stays empty), never fatal.
  local state_dir="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
  if [ -d "$state_dir" ]; then
    printf '%s smoke-gate-hook-missing %s issue=%s\n' \
      "$(date -u +%FT%TZ)" "$hook" "${ISSUE_NUM:-}" \
      2>/dev/null >> "${state_dir}/hook-warnings.log" || true
  fi
  # on_failure's generic comment says only "exit code N" (no transcript exists this
  # early), so the actionable text reaches the ticket through its own marker comment.
  # recheck has no ticket context → no comment.
  if [ -n "${ISSUE_NUM:-}" ]; then
    local BODY_FILE
    if BODY_FILE=$(mktemp /tmp/smoke-hook-missing-XXXXXX.md 2>/dev/null); then
      cat > "$BODY_FILE" << EOF
${SMOKE_HOOK_MISSING_MARKER}
## Dark Factory — Smoke-gate hook missing

This run was refused before any work started: the target repo has no \`.factory/hooks/smoke-gate\` hook (absent, a directory, or an empty file), so the factory cannot check whether \`main\` is healthy.

\`main\` was **not checked** and has **not** been marked red — no \`main-is-red\` sentinel, no regression ticket, and other tickets are not paused.

**To fix:** commit a non-empty, executable \`.factory/hooks/smoke-gate\` to the target repo (exit 0 = main is green). Start from the factory's \`templates/new-target/.factory/hooks/smoke-gate\` and follow \`docs/onboarding-new-target.md\` step 3b, then re-dispatch this ticket (a Blocked ticket: move it back to Ready).
EOF
      python3 "$PROVIDERS_CLI" tracker comment --id "$ISSUE_NUM" \
        --marker "$SMOKE_HOOK_MISSING_MARKER" --body-file "$BODY_FILE" >/dev/null 2>&1 || true
      rm -f "$BODY_FILE"
    fi
  fi
  python3 "$SMOKE_CORE_CLI" run-record health-event \
    --run-id "${RUN_ID:-unknown}" --issue "${ISSUE_NUM:-0}" \
    --event factory.smoke_gate.hook_missing \
    --detail "path=${hook}" >/dev/null 2>&1 || true
  return 1
}

# MarketHawk-parity check (tsc + python import) wrapped in the red/green state
# machinery. No longer the no-hook fallback: since #436, hooks.sh run_hook refuses a
# target with no smoke-gate hook (_smoke_hook_missing above) instead of running this.
# Kept for direct callers: run_smoke_gate, tests/test_smoke_gate.sh, and
# scripts/factory_core/main_red_fixer.py:173-177, which sources this file and calls
# _smoke_check_main directly so diagnosis cannot drift from the gate.
# Returns 0 on green (proceed); exits 0 on red (clean halt, no per-ticket failure).
```

  Notes for the implementer:
  - The message strings must not contain `omniscient/markethawk`. `tests/test_identity.sh:17`
    greps `smoke_gate.sh` for that slug.
  - `tracker comment` takes `--id --marker --body-file` (`scripts/factory_core/providers/cli.py:195-199`)
    and upserts by marker. `run-record health-event` takes `--run-id --issue(int) --event
    --detail KEY=VAL…` (`scripts/factory_core/run_record.py:854-858`).

- [ ] **Step 6: Verify it passes.**

```bash
bash -n smoke_gate.sh && bash tests/test_smoke_gate.sh 2>&1 | tail -1; echo "rc=${PIPESTATUS[0]}"
```

Expected: `Results: 52 passed, 0 failed` and `rc=0`. Phases 1-6 keep their 27 passes
unchanged.

- [ ] **Step 7: Commit.**

```bash
git add smoke_gate.sh tests/test_smoke_gate.sh
git commit -m "feat(smoke-gate): _smoke_hook_missing refuses a no-hook target without latching main-red (#436)"
```

---

## Task 2: Route `run_hook`'s no-hook smoke-gate arm to the refusal (TDD via `tests/test_hooks.sh`)

**Files:** `tests/test_hooks.sh`, `scripts/hooks.sh`

- [ ] **Step 1: Re-author case 9.** In `tests/test_hooks.sh`, replace the whole case-9 block
  (post-#454 lines 135-149, from `# 9) Negative case A …` through the line
  `[ "$(wc -l < "$WARN_LOG")" = "$WARN_LINES" ] || { echo "FAIL: absent/empty/dir hook must not write $WARN_LOG"; exit 1; }`),
  which is exactly:

```bash
# 9) Negative case A (a missing hook must not become a pass): absent, zero-byte, and
#    directory smoke-gate "hooks" all still run the built-in default, with no warning.
WARN_LINES=$(wc -l < "$WARN_LOG")
rm -f "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9a"
: > "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9b"
rm -f "$TMP/.factory/hooks/smoke-gate"; mkdir "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9c"
rmdir "$TMP/.factory/hooks/smoke-gate"
[ "$(wc -l < "$DEFAULT_CHECKS")" = "3" ] || { echo "FAIL: absent/empty/dir hook must run the built-in default"; exit 1; }
if grep -q "hook-not-executable" "$TMP/stderr-9a" "$TMP/stderr-9b" "$TMP/stderr-9c"; then
  echo "FAIL: absent/empty/dir hook must not emit hook-not-executable"; exit 1
fi
[ "$(wc -l < "$WARN_LOG")" = "$WARN_LINES" ] || { echo "FAIL: absent/empty/dir hook must not write $WARN_LOG"; exit 1; }
```

  with:

```bash
# 9) Negative case A (a missing hook must not become a pass), #436: absent, zero-byte,
#    and directory smoke-gate "hooks" are REFUSED — non-zero rc, the built-in check
#    never runs, no main-red state or regression ticket, one durable
#    smoke-gate-hook-missing line each, and no hook-not-executable warning.
# Clear 8/8b's red state so "nothing written" is observable.
rm -f "$SCHEDULER_STATE_DIR/main-is-red" "$SCHEDULER_STATE_DIR/main-is-red-issue" \
      "$SCHEDULER_STATE_DIR/main-red-last-recheck"
# Keep the refusal's ticket comment + health event offline too (#348): log, never delegate.
# NOTE: this python3 override persists to the end of the script and, unlike the stub
# above, does not fall through to real `python` — any case added below must not depend
# on python3 actually running.
# shellcheck disable=SC2317
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "tracker create"; then echo "999"; fi
  return 0
}
CREATES_BEFORE=$(grep -c "python3.*tracker create" "$STUB_LOG" || true)
WARN_LINES=$(wc -l < "$WARN_LOG")
rm -f "$TMP/.factory/hooks/smoke-gate"
RC=0; run_hook --gate smoke-gate 2> "$TMP/stderr-9a" || RC=$?
[ "$RC" != "0" ] || { echo "FAIL: absent smoke-gate hook must be refused (non-zero rc)"; exit 1; }
: > "$TMP/.factory/hooks/smoke-gate"
RC=0; run_hook --gate smoke-gate 2> "$TMP/stderr-9b" || RC=$?
[ "$RC" != "0" ] || { echo "FAIL: zero-byte smoke-gate hook must be refused (non-zero rc)"; exit 1; }
rm -f "$TMP/.factory/hooks/smoke-gate"; mkdir "$TMP/.factory/hooks/smoke-gate"
RC=0; ISSUE_NUM=436 run_hook --gate smoke-gate 2> "$TMP/stderr-9c" || RC=$?
[ "$RC" != "0" ] || { echo "FAIL: directory smoke-gate hook must be refused (non-zero rc)"; exit 1; }
rmdir "$TMP/.factory/hooks/smoke-gate"
# 9d) the smoke-gate arm is --gate-only: without --gate the refusal must STILL be non-zero
#     (run_hook's non-gate `return 0` would otherwise swallow it and main goes unchecked)
RC=0; run_hook smoke-gate 2> "$TMP/stderr-9d" || RC=$?
[ "$RC" != "0" ] || { echo "FAIL: smoke-gate refusal must be non-zero even without --gate"; exit 1; }
[ ! -s "$DEFAULT_CHECKS" ] || { echo "FAIL: absent/empty/dir hook must never run the built-in check"; exit 1; }
for F in main-is-red main-is-red-issue main-red-last-recheck; do
  [ ! -e "$SCHEDULER_STATE_DIR/$F" ] || { echo "FAIL: refusal must not write $F"; exit 1; }
done
[ "$(grep -c "python3.*tracker create" "$STUB_LOG" || true)" = "$CREATES_BEFORE" ] \
  || { echo "FAIL: refusal must not file a regression ticket"; exit 1; }
for E in 9a 9b 9c 9d; do
  grep -q "smoke-gate-hook-missing path=$TMP/.factory/hooks/smoke-gate" "$TMP/stderr-$E" \
    || { echo "FAIL: case $E must emit the smoke-gate-hook-missing message on stderr"; exit 1; }
done
if grep -q "hook-not-executable" "$TMP/stderr-9a" "$TMP/stderr-9b" "$TMP/stderr-9c" "$TMP/stderr-9d"; then
  echo "FAIL: absent/empty/dir hook must not emit hook-not-executable"; exit 1
fi
[ "$(grep -c "smoke-gate-hook-missing $TMP/.factory/hooks/smoke-gate" "$WARN_LOG")" = "4" ] \
  || { echo "FAIL: each refusal must append exactly one durable line to $WARN_LOG"; exit 1; }
[ "$(wc -l < "$WARN_LOG")" = "$((WARN_LINES + 4))" ] || { echo "FAIL: refusal must append only its own lines to $WARN_LOG"; exit 1; }
[ "$(grep -c "tracker comment --id 436 --marker <!-- df-smoke-hook-missing -->" "$STUB_LOG" || true)" = "1" ] \
  || { echo "FAIL: only the run with ISSUE_NUM set may post the marker comment"; exit 1; }
WARN_LINES=$(wc -l < "$WARN_LOG")   # re-baseline for case 10
```

  Case 10's two lines are **not** edited. They still compare against `$WARN_LINES`, which the
  last line above re-baselines after case 9's four new durable lines. Cases 1-8 and 8b are
  **not** edited. They are the MarketHawk no-regression proof, where a present hook's red/green
  latch is unchanged.

- [ ] **Step 2: Add case 11 (non-executable carve-out regression guard).** In
  `tests/test_hooks.sh`, insert the following immediately **before** the final line
  `echo PASS`:

```bash
# 11) #436 carve-out: a present-but-non-executable smoke-gate hook is NOT a missing hook —
#     it still runs via `bash "$hook"` with the #438 hook-not-executable warning.
MISSING_LINES=$(grep -c "smoke-gate-hook-missing" "$WARN_LOG" || true)
rm -f "$ARTIFACTS_DIR/smoke-hook-ran"
printf '#!/usr/bin/env bash\necho ran > "$ARTIFACTS_DIR/smoke-hook-ran"\nexit 0\n' > "$TMP/.factory/hooks/smoke-gate"
chmod -x "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-11"
[ -f "$ARTIFACTS_DIR/smoke-hook-ran" ] || { echo "FAIL: non-exec smoke-gate hook body must still run via bash"; exit 1; }
grep -q "hook-not-executable path=$TMP/.factory/hooks/smoke-gate" "$TMP/stderr-11" \
  || { echo "FAIL: non-exec smoke-gate hook must keep the hook-not-executable warning"; exit 1; }
if grep -q "smoke-gate-hook-missing" "$TMP/stderr-11"; then
  echo "FAIL: non-exec smoke-gate hook must not be refused as missing"; exit 1
fi
[ "$(grep -c "smoke-gate-hook-missing" "$WARN_LOG" || true)" = "$MISSING_LINES" ] \
  || { echo "FAIL: non-exec smoke-gate hook must not write a smoke-gate-hook-missing line"; exit 1; }
[ ! -s "$DEFAULT_CHECKS" ] || { echo "FAIL: non-exec smoke-gate hook must not run the built-in check"; exit 1; }
```

- [ ] **Step 3: Verify it fails.**

```bash
bash tests/test_hooks.sh 2>/dev/null | tail -1; echo "rc=${PIPESTATUS[0]}"
```

Expected: `FAIL: absent smoke-gate hook must be refused (non-zero rc)` and `rc=1`. The
no-hook arm still calls `_default_smoke_gate`, and the stubbed check returns green.

- [ ] **Step 4: Repoint the no-hook arm.** In `scripts/hooks.sh`, replace the line (post-#454
  line 67):

```bash
      smoke-gate) _default_smoke_gate "$@" || rc=$? ;;   # provided by smoke_gate.sh
```

  with:

```bash
      # No hook present → refuse the run (#436; provided by smoke_gate.sh). The
      # smoke-gate arm is --gate-only: return non-zero even without --gate, or the
      # non-gate `return 0` below would swallow the refusal and main goes unchecked.
      smoke-gate) _smoke_hook_missing "$@" || rc=$?; return "$rc" ;;
```

  Keep the `|| rc=$?` form rather than a bare call. `entrypoint.sh` has no `set -E`, so its
  ERR trap is not inherited into functions. A bare failing command inside `run_hook` under
  the caller's `set -e` could exit the script without `on_failure` running. With the `||`
  form the non-zero status surfaces only as `run_hook`'s own return value at the
  `entrypoint.sh:791` call site, where the top-level ERR trap fires.

- [ ] **Step 5: Update the four header comments in the same file.** All four are
  whole-line replacements. In `scripts/hooks.sh`, replace line 2 — both of its clauses
  become false for the smoke-gate arm, which no longer has a built-in default and now
  propagates its exit code even without `--gate`:

```bash
# run_hook [--gate] <name> [args…] — target hook > built-in default. Gate = propagate exit code.
```

  with:

```bash
# run_hook [--gate] <name> [args…] — target hook > built-in default; --gate propagates the
# exit code. Exception: the smoke-gate arm with no hook present always propagates (#436).
```

  Then replace lines 10-11 — this "falls back to built-in defaults" heading introduces
  the `smoke-gate` entry directly below it, and contradicts it once that entry becomes a
  refusal:

```bash
# Falls back to built-in defaults when no target hook is present (absent, a
# directory, or a zero-byte placeholder):
```

  with:

```bash
# When no target hook is present (absent, a directory, or a zero-byte placeholder):
```

  Then replace line 12:

```bash
#   smoke-gate  →  _default_smoke_gate (MarketHawk tsc + backend-import checks)
```

  with:

```bash
#   smoke-gate  →  _smoke_hook_missing: refuse the run, non-zero even without --gate (#436)
```

  and replace line 20:

```bash
# Source smoke_gate.sh to load _default_smoke_gate (SMOKE_GATE_SOURCE_ONLY suppresses auto-exec).
```

  with:

```bash
# Source smoke_gate.sh to load _smoke_hook_missing and the _smoke_on_red/_smoke_on_green
# state machinery (SMOKE_GATE_SOURCE_ONLY suppresses auto-exec).
```

- [ ] **Step 6: Verify it passes.**

```bash
bash -n scripts/hooks.sh && bash tests/test_hooks.sh 2>/dev/null | tail -1; echo "rc=${PIPESTATUS[0]}"
bash tests/test_smoke_gate.sh 2>/dev/null | tail -1
git diff HEAD -- scripts/hooks.sh | grep -c '^[-+][^-+]'
```

Expected: `PASS`, `rc=0`, `Results: 52 passed, 0 failed`, then `16`. The last count covers
6 removed and 10 added lines in `scripts/hooks.sh`. Nothing outside lines 2, 10-11, 12, 20
and 67 changed.

- [ ] **Step 7: Commit.**

```bash
git add scripts/hooks.sh tests/test_hooks.sh
git commit -m "fix(hooks): no smoke-gate hook present refuses the run instead of running the MarketHawk default (#436)"
```

---

## Task 3: Docs: README Hooks table and onboarding steps 3b/3c

**Files:** `README.md`, `docs/onboarding-new-target.md`

- [ ] **Step 1: README table row.** In `README.md` (post-#454 line 232), replace:

```markdown
| `smoke-gate` | Pre-dispatch | Yes (check-only) | Exit 0 = green, non-zero = red. Factory keeps sentinel + regression-ticket handling regardless of hook presence. Built-in default: tsc + backend import checks (MarketHawk). |
```

  with:

```markdown
| `smoke-gate` | Pre-dispatch | Yes (check-only) | **Required.** Exit 0 = green, non-zero = red; the factory keeps sentinel + regression-ticket handling. With no hook present (absent, a directory, or empty) the factory refuses the run: it fails with a `smoke-gate-hook-missing` message and ticket comment, and `main` is neither checked nor marked red (#436). Start from `templates/new-target/.factory/hooks/smoke-gate`. |
```

- [ ] **Step 2: README "smoke-gate is check-only" paragraph** (post-#454 lines 253-255). Its
  "…or the built-in default" clause would be false after Task 2, so replace:

```markdown
with exit 0 — stays factory-side and runs identically whether the check comes
from a target hook or the built-in default.  This means you never need to
replicate sentinel or ticket logic in your hook.
```

  with:

```markdown
with exit 0 — stays factory-side.  This means you never need to replicate
sentinel or ticket logic in your hook.
```

- [ ] **Step 2b: README product-agnostic claim** (post-#454 lines 26-27). The sentence
  still promises a MarketHawk built-in default for an absent hook, which Task 2 removes for
  `smoke-gate`. Whole-line replacement of both lines, moving the link onto its own line so
  nothing mid-line is disturbed. Replace:

```markdown
When they are absent the built-in defaults are MarketHawk's, so a new product
must supply its own — see [`docs/onboarding-new-target.md`](docs/onboarding-new-target.md)
```

  with:

```markdown
When the adapter is absent the built-in defaults are MarketHawk's, so a new product
must supply its own; a missing `smoke-gate` hook refuses the run outright (#436) — see
[`docs/onboarding-new-target.md`](docs/onboarding-new-target.md)
```

- [ ] **Step 3: Onboarding step 3b** (`docs/onboarding-new-target.md`, post-#454 lines
  87-90). This is the section the new message points operators to. Replace:

```markdown
Before each ticket, the factory checks that the default branch is healthy. **Without
this hook it runs MarketHawk's check** (`tsc` in `frontend/` and a Python import in
`backend/`). That check fails on any other layout, latches `main-is-red`, files a
regression ticket, and halts all dispatch (tracked in #436).
```

  with:

```markdown
Before each ticket, the factory runs this hook to check that the default branch is
healthy. **Without it the factory refuses the ticket's run** (#436): the run fails before
any work starts, the ticket gets a comment naming the missing hook (a `fix` or `continue`
run also moves it to **Blocked**), and a `smoke-gate-hook-missing` line lands in
`hook-warnings.log`. `main` is not checked and not marked red, and other tickets are not
paused.
```

- [ ] **Step 4: Onboarding step 3c** (post-#454 lines 120-121). Replace:

```markdown
An empty hook file is treated
as absent, so the built-in default runs.
```

  with:

```markdown
An empty hook file is treated
as absent, so the run is refused (see 3b).
```

  **Mid-line replacement — the only one in this plan.** The replaced text ends mid-line:
  on line 121 the words ` Set the bit anyway. On Windows, ` and the `git add` code span that
  follows them sit on the same line as `runs.` and must be preserved. Apply this one as a
  substring replacement; matching it as whole lines finds nothing.

- [ ] **Step 5: Verify no stale claims remain in the edited surfaces.**

```bash
grep -nE "MarketHawk needs zero hooks|Built-in default: tsc|it runs MarketHawk's check|so the built-in default runs|or the built-in default|Gate = propagate exit code" \
  README.md docs/onboarding-new-target.md smoke_gate.sh scripts/hooks.sh; echo "rc=$?"
grep -c "templates/new-target/.factory/hooks/smoke-gate" README.md
```

Expected: no matches and `rc=1`, then `1`.

- [ ] **Step 6: Commit.**

```bash
git add README.md docs/onboarding-new-target.md
git commit -m "docs(hooks): smoke-gate hook is required; no hook refuses the run (#436)"
```

---

## Task 4: Full verification and pre-merge checklist

**Files:** none modified.

- [ ] **Step 1: CI-parity shell tests.**

```bash
bash tests/test_hooks.sh 2>/dev/null | tail -1
bash tests/test_smoke_gate.sh 2>/dev/null | tail -1
```

Expected: `PASS`, then `Results: 52 passed, 0 failed`.

- [ ] **Step 2: Python suite** (CI runs exactly this).

```bash
PYTHONPATH=scripts python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: no failures. `tests/test_verify_skill_files.py`, `test_main_red_fixer.py`,
`test_side_effect.py` and `test_entrypoint_validate_hook_precheck.py` reference smoke-gate
names but none of the strings this plan changes. If `pytest` is not installed in the
container, record that in the commit or PR body instead of skipping silently.

- [ ] **Step 3: Other shell tests that read `smoke_gate.sh`/`entrypoint.sh`.** Compare
  against the pre-change baseline, not an absolute result. Measured in the factory container
  on a clean extract of `6672bd5`: `tests/test_identity.sh` **passes** before and after
  (rc 0 both ways); `tests/test_entrypoint_fix_main.sh` is **not listed in `ci.yml`** and
  already fails on unmodified `main` (rc 1) for environmental reasons. Compare each result
  to its own baseline, never to 0:

```bash
# The clone is a bind mount owned by another uid; without this git refuses to read it.
git config --global --add safe.directory "$(pwd)"
REF=6672bd5; git rev-parse --verify origin/main >/dev/null 2>&1 && REF=origin/main
BASE=$(mktemp -d) && git archive "$REF" | tar -x -C "$BASE"
for t in tests/test_identity.sh tests/test_entrypoint_fix_main.sh; do
  bash "$t" >/dev/null 2>&1; echo "$t now=$?"
  (cd "$BASE" && bash "$t" >/dev/null 2>&1; echo "$t baseline=$?")
done
rm -rf "$BASE"
```

Expected: `now` equals `baseline` for each test — `test_identity.sh` `0`/`0` and
`test_entrypoint_fix_main.sh` `1`/`1`. Without the `safe.directory` line `git archive` dies
with `fatal: detected dubious ownership`, `tar` then fails, and every `baseline` comes back
`127`, which looks like a regression but is not one.

- [ ] **Step 4: DAG checks** (CI's `dag-check` job; this change touches no workflow, so they
  must be unchanged):

```bash
python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml >/dev/null \
  && python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml >/dev/null; echo "rc=$?"
```

Expected: `rc=0`.

- [ ] **Step 5: Scope check.** Use the two-dot diff against `main`:

```bash
git diff --name-only origin/main HEAD | grep -vE '^docs/superpowers/(specs|plans)/'
```

Expected: exactly `README.md`, `docs/onboarding-new-target.md`, `scripts/hooks.sh`,
`smoke_gate.sh`, `tests/test_hooks.sh`, `tests/test_smoke_gate.sh`. In particular,
`entrypoint.sh`, `scheduler.sh`, `templates/**` and `.factory/**` are absent.

- [ ] **Step 6: Pre-merge checklist (blocking, from the spec).** Confirm every live target's
  `main` has a non-empty, executable hook:

```bash
for R in omniscient/markethawk omniscient/jobfinder; do
  gh api "repos/${R}/git/trees/main?recursive=1" \
    --jq '.tree[] | select(.path==".factory/hooks/smoke-gate") | {mode, size}'
done
```

Expected: `{"mode":"100755","size":<non-zero>}` for each (spec recorded MarketHawk
`100755`/1461, jobfinder `100755`/407 on 2026-09-27). Paste the output into the PR body. If a
target fails, do **not** change code. Record it in the PR body, because that target's hook
must be committed before rollout (spec Requirement 1).

- [ ] **Step 7: PR body notes** (no code). State these accepted limitations from the spec's
  "Known limitations":
  - the failure signature classifies as `environmental:delivery_failure`, and a
    second identical signature trips with a #279-flavoured reason. The
    `<!-- df-smoke-hook-missing -->` comment carries the real cause, and a `configuration:*`
    signature is a follow-up ticket;
  - on a hook-less target, every eligible ticket costs one failed dispatch, which is the
    accepted blast radius;
  - `templates/new-target/.factory/hooks/smoke-gate` lines 6-7 are still stale. They are
    hard-excluded and left for a docs follow-up.

---

## Self-review

- `**Issue:** #436` line present under the title.
- No placeholders. Every edit gives exact before/after text anchored on post-#454 content.
  Every command gives an expected output. The red/green counts (`35 passed, 17 failed` →
  `52 passed, 0 failed`; `FAIL: absent smoke-gate hook must be refused` → `PASS`) were measured
  by applying this exact plan to a scratch extract of `origin/main@6672bd5`.
- Spec coverage:
  - Req 1: trigger only in `run_hook`'s existing not-present `else` branch; the present
    non-exec path is untouched and guarded by case 11.
  - Req 2: no sentinel, `_smoke_on_*` or regression ticket (Phase 7 and case 9 asserts).
  - Req 3: `return 1`, never `exit` (Phase 7 `returned:1`), so `on_failure` is unchanged.
  - Req 4: stderr, durable `hook-warnings.log` line under #438's guards, marker comment
    only with `ISSUE_NUM`, and the health event last and guarded.
  - Architecture: `_default_smoke_gate`/`run_smoke_gate`/`_smoke_check_main` bodies are
    unchanged, the doc block, README and onboarding docs are updated, the template is out of
    scope, and the pre-merge `gh api` check is Task 4 step 6.
  - Tests: case 9 re-authored, cases 4-8 verbatim, case 11 carve-out, 9d no-`--gate`, and the
    `--gate`-only comment at the `hooks.sh` arm.

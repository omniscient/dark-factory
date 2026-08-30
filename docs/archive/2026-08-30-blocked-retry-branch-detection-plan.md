# Implementation Plan: Dispatch Continue (not Fix) from `stage_blocked_retry` when the feature branch already exists

**Issue:** omniscient/dark-factory#371
**Spec:** `docs/superpowers/specs/2026-08-30-blocked-retry-branch-detection-design.md`
**Depends on:** none — self-contained scheduler.sh + tests/test_scheduler.sh change.

---

## Goal

`stage_blocked_retry` (`scheduler.sh`) currently decides Continue vs. Fix for a Blocked
ticket using only `get_pr_for_issue` (a GitHub REST/GraphQL call). When a run finishes
implement → validate → conformance and pushes its `feat/issue-N-…` branch but dies
before/during `push-and-pr` (e.g. #366's GraphQL-quota exhaustion), no PR exists yet, so
the scheduler dispatches `Fix issue #N` — which re-implements from scratch and then fails
non-fast-forward pushing over the already-pushed branch. This plan adds a plain-`git
ls-remote` branch-existence probe (`branch_exists_for_issue`), OR'd with the existing
PR check (branch-check first, since it costs no API quota), so a pushed-but-PR-less
branch is detected and the scheduler dispatches `Continue issue #N` instead, reusing the
branch. No changes to `setup-branch`, `stage_rescue_blocked`, or any gate/breaker/budget
logic — this is a pure dispatch-path change.

## Architecture

```
stage_blocked_retry() (scheduler.sh)
  ... existing signature/pause/delivery-failure/ceiling handling (unchanged) ...
  │
  ▼
  if branch_exists_for_issue($ISSUE) non-empty   [NEW — git ls-remote, no API quota]
     OR get_pr_for_issue($ISSUE) non-empty        [existing — codehost find-change]
    → dispatch "Continue issue #N"                [setup-branch's existing continue
                                                     path already reuses the branch]
  else
    → dispatch "Fix issue #N"                     [unchanged: fresh branch from main]

branch_exists_for_issue($N):                       [NEW helper, next to get_pr_for_issue]
  url = `python3 $FACTORY_PROVIDERS_CLI codehost remote-url`   (token-embedded, never echoed)
  if url empty → echo "" (fail closed)
  GIT_TERMINAL_PROMPT=0 timeout 30 \
    git ls-remote --heads "$url" "refs/heads/feat/issue-${N}-*" 2>/dev/null \
    | head -1 | awk '{print $2}' || true            (fail closed on any error/stall)
```

## Tech Stack

- Bash (`scheduler.sh`), matching every existing helper in the file (`get_pr_for_issue`,
  `check_pr_mergeable`) — plain `python3 $FACTORY_PROVIDERS_CLI ...` for the token-bearing
  URL, then a literal `git` invocation (the CodeHost provider contract's principle 3:
  branch/ref existence is host-agnostic plain-git, stays outside the abstraction).
- `tests/test_scheduler.sh`'s existing `SCHEDULER_SOURCE_ONLY=1 source` + stub-function
  harness — no new test framework or dependency.

## File Structure

| File | Change |
|---|---|
| `scheduler.sh` | **Modified** — new `branch_exists_for_issue()` helper; `stage_blocked_retry`'s dispatch decision OR's it with the existing PR check |
| `tests/test_scheduler.sh` | **Modified** — `git` added to the stub/PATH-shim scaffolding; new section Y (helper-level `branch_exists_for_issue` tests) and section Z (dispatch-decision tests against the real `stage_blocked_retry` via `dispatch_stage`); one comment update on section V's hand-copied shadow |

Not touched: `workflows/archon-dark-factory.yaml` (`setup-branch`'s existing `continue`
path already reuses the branch correctly), `stage_rescue_blocked`, any `gate_*`/breaker/
budget file, `config/config.yaml`.

---

## Task 1: `branch_exists_for_issue()` helper + helper-level tests

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD Steps

1. Extend the test harness's command stubs so a `git` call can be intercepted both as an
   exported bash function (in-process) and as a subprocess (spawned by `timeout`, which
   execs a real binary and cannot see bash functions). In `tests/test_scheduler.sh`:

   Change (the `# ---- Stubs ----` block near the top):
   ```bash
   gh()               { echo "gh $*"               >> "$STUB_LOG"; return 0; }
   docker()           { echo "docker $*"           >> "$STUB_LOG"; return 0; }
   set_board_status() { echo "set_board_status $*" >> "$STUB_LOG"; return 0; }
   ```
   to:
   ```bash
   gh()               { echo "gh $*"               >> "$STUB_LOG"; return 0; }
   docker()           { echo "docker $*"           >> "$STUB_LOG"; return 0; }
   git()              { echo "git $*"              >> "$STUB_LOG"; return 0; }
   set_board_status() { echo "set_board_status $*" >> "$STUB_LOG"; return 0; }
   ```

   Change the export line right after the python3 stub definition:
   ```bash
   export -f gh docker set_board_status python3
   ```
   to:
   ```bash
   export -f gh docker git set_board_status python3
   ```

   Change the PATH-shim loop (`# ---- Subprocess-visible stubs ----` block):
   ```bash
   for _stub_cmd in gh docker; do
   ```
   to:
   ```bash
   for _stub_cmd in gh docker git; do
   ```

2. Verify these scaffolding-only edits don't break anything yet (no new assertions,
   should still be the pre-existing baseline):
   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -3
   ```
   Expected (baseline — two pre-existing, unrelated failures already present on `main`):
   ```
   Results: 234 passed, 2 failed
   ```

3. Write the failing test section. Insert immediately after the line
   `assert_eq "evaluate_stop wired 4x in scheduler.sh" "4" "$(grep -c 'evaluate_stop "\$ISSUE"' "$SCHED")"`
   (the last line of section X, immediately before the `# Cleanup` section) in
   `tests/test_scheduler.sh`:

   ```bash

   # ==========================================
   # Y: branch_exists_for_issue — helper-level git ls-remote probe (#371)
   # ==========================================
   echo ""
   echo "--- Y: branch_exists_for_issue — git ls-remote probe ---"

   # Y1: git prints a matching ref -> helper echoes the ref; the logged argv is captured
   # via the subprocess PATH-shim (SHIM_LOG), not STUB_LOG — `timeout` execs a real `git`
   # binary, not a bash function, so only a re-entrant PATH shim script (not an exported
   # bash function) is visible to it. The URL embeds a fake token to prove requirement 6
   # (never leaked) without touching a real credential.
   # Section N (its --id-routing python3 override, and the N20 variant it leaves behind) permanently overrides the `python3` stub with its own
   # --id-routing case and never restores the generic PROVIDERS_CLI_OUTPUT-echoing form —
   # reset_python3_stub() only clears the variable, not the function body. Redefine the
   # generic stub here so PROVIDERS_CLI_OUTPUT is honored again for this section.
   python3() {
     echo "python3 $*" >> "$STUB_LOG"
     case "$*" in
       *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
       *) "$_REAL_PY3" "$@" ;;
     esac
   }
   export -f python3
   PROVIDERS_CLI_OUTPUT="https://x-access-token:ghs_zzfaketoken371@github.com/omniscient/dark-factory.git"
   git() {
     echo "git $*" >> "$STUB_LOG"
     printf 'deadbeefcafefeed\trefs/heads/feat/issue-371-x\n'
     return 0
   }
   export -f git
   > "$STUB_LOG"
   Y1_OUT=$(branch_exists_for_issue 371)
   assert_eq "Y1: helper echoes the matched ref" "refs/heads/feat/issue-371-x" "$Y1_OUT"
   assert_eq "Y1b: git invoked with the expected ls-remote argv (via SHIM_LOG, not STUB_LOG)" \
     "1" "$(grep -c '^git ls-remote --heads https://x-access-token:ghs_zzfaketoken371@github.com/omniscient/dark-factory.git refs/heads/feat/issue-371-\*$' "$SHIM_LOG" || echo 0)"

   # Y2: git ls-remote exits non-zero (transport error) -> helper echoes empty, no crash
   git() { echo "git $*" >> "$STUB_LOG"; return 128; }
   export -f git
   Y2_OUT=$(branch_exists_for_issue 371)
   assert_eq "Y2: git ls-remote failure -> empty" "" "$Y2_OUT"

   # Y3: codehost remote-url itself returns empty (e.g. GH_TOKEN missing) -> empty, and git
   # is never invoked at all (checked via a SHIM_LOG line-count delta, since SHIM_LOG is a
   # suite-wide accumulating log with no reset hook).
   PROVIDERS_CLI_OUTPUT=""
   git() { echo "git $*" >> "$STUB_LOG"; return 0; }
   export -f git
   Y3_SHIM_BEFORE=$(wc -l < "$SHIM_LOG")
   Y3_OUT=$(branch_exists_for_issue 371)
   Y3_SHIM_AFTER=$(wc -l < "$SHIM_LOG")
   assert_eq "Y3: empty remote-url -> empty" "" "$Y3_OUT"
   assert_eq "Y3b: git never called when remote-url is empty" "$Y3_SHIM_BEFORE" "$Y3_SHIM_AFTER"

   reset_python3_stub
   git() { echo "git $*" >> "$STUB_LOG"; return 0; }
   export -f git
   > "$STUB_LOG"
   ```

4. Verify the new section fails. `scheduler.sh` runs under `set -euo pipefail`, which the
   test file inherits by sourcing it — so calling an undefined `branch_exists_for_issue`
   does **not** degrade to a clean per-assertion failure. It aborts the whole suite:
   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```
   Expected: a hard abort with `branch_exists_for_issue: command not found`, exit code
   127, and **no** `Results:` line at all (the `# Cleanup` block never runs either). This
   abort — not a `Y1`/`Y1b` FAIL line — is the correct red state to see before step 5.

5. Implement `branch_exists_for_issue()` in `scheduler.sh`. Insert it immediately before
   the existing `# --- PR lookup ---` comment block (i.e., right after
   `check_pr_mergeable`'s closing `}`):

   ```bash
   # --- Branch lookup: does a feat/issue-<N>-* branch exist on origin? ---
   # Plain-git probe (CodeHost contract principle 3: branch/ref existence is host-agnostic
   # and stays outside the provider abstraction, codehost/base.py). Runs over git's
   # smart-HTTP transport, not the GitHub REST/GraphQL API, so it costs no quota (#366) —
   # call this BEFORE get_pr_for_issue so the common recovery path never touches the
   # rate-limited API at all. Never echo $url — it embeds GH_TOKEN.
   # Bounded and prompt-free: a smart-HTTP stall must not hang the poll loop, and a 401
   # must fail closed (empty) instead of blocking on a credential prompt.
   branch_exists_for_issue() {
     local url
     url=$(python3 "$FACTORY_PROVIDERS_CLI" codehost remote-url 2>/dev/null) || true
     [ -n "$url" ] || { echo ""; return; }
     GIT_TERMINAL_PROMPT=0 timeout 30 git ls-remote --heads "$url" "refs/heads/feat/issue-${1}-*" 2>/dev/null | head -1 | awk '{print $2}' || true
   }
   ```

6. Verify the new section passes:
   ```bash
   bash tests/test_scheduler.sh 2>&1 | sed -n '/--- Y: branch_exists_for_issue/,/^Results/p'
   ```
   Expected: `Y1`, `Y1b`, `Y2`, `Y3`, `Y3b` all `PASS`.

7. Commit:
   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "feat(#371): add branch_exists_for_issue() git ls-remote probe"
   ```

## Task 2: Branch-aware dispatch decision in `stage_blocked_retry` + dispatch-level tests

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD Steps

1. Write the failing test section. Insert immediately after the Task 1 section Y block
   (before the `# Cleanup` section) in `tests/test_scheduler.sh`:

   ```bash

   # Y4: end-to-end via dispatch_stage stage_blocked_retry, exercising the REAL
   # (still-unstubbed) branch_exists_for_issue against a fake token-bearing URL — proves
   # requirement 6 (URL never leaked) at the actual dispatch call site, not just in the
   # helper's own return value. get_pr_for_issue is never reached here (OR short-circuits
   # once the branch probe is non-empty), so it needs no stub post-fix; pre-fix it is reached and hits section X's `get_pr_for_issue() { echo ""; }` restore, which is what makes Y4 red before Task 2 step 3.
   PROVIDERS_CLI_OUTPUT="https://x-access-token:ghs_zzfaketoken371@github.com/omniscient/dark-factory.git"
   git() { printf 'deadbeefcafefeed\trefs/heads/feat/issue-304-x\n'; return 0; }
   export -f git
   MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; RESCUED=""
   BLOCKED='[{"content":{"number":304},"labels":[],"status":"Blocked"}]'
   DISPATCHED=""
   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"
   Y4_STDOUT=$(dispatch_stage stage_blocked_retry 2>&1)
   assert_eq "Y4: real branch probe drives Continue end-to-end through dispatch_stage" \
     "1" "$(grep -c 'dispatch Continue issue #304' "$STUB_LOG" || echo 0)"
   assert_eq "Y4b: dispatch_stage's own stdout/stderr never contains the token-embedded URL" \
     "0" "$(echo "$Y4_STDOUT" | grep -c 'ghs_zzfaketoken371' || true)"
   assert_eq "Y4c: the dispatch log line itself never contains the token-embedded URL" \
     "0" "$(grep -c 'ghs_zzfaketoken371' "$STUB_LOG" || true)"

   reset_python3_stub
   git() { echo "git $*" >> "$STUB_LOG"; return 0; }
   export -f git
   > "$STUB_LOG"

   # ==========================================
   # Z: stage_blocked_retry — branch-aware dispatch decision (#371)
   # ==========================================
   echo ""
   echo "--- Z: stage_blocked_retry — dispatch Continue when the feat branch already exists ---"
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
   MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; RESCUED=""
   dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
   is_issue_running() { return 1; }
   export -f dispatch is_issue_running

   # Z1: branch exists, no PR -> Continue (the #371 case: pushed, PR creation failed)
   branch_exists_for_issue() { echo "deadbeef"; }
   get_pr_for_issue() { echo ""; }
   export -f branch_exists_for_issue get_pr_for_issue
   BLOCKED='[{"content":{"number":300},"labels":[],"status":"Blocked"}]'
   DISPATCHED=""
   dispatch_stage stage_blocked_retry > /dev/null
   assert_eq "Z1: branch exists, no PR -> Continue" \
     "1" "$(grep -c 'dispatch Continue issue #300' "$STUB_LOG" || echo 0)"
   assert_eq "Z1b: no Fix dispatched" "0" "$(grep -c 'dispatch Fix issue #300' "$STUB_LOG" || true)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # Z2: no branch, PR exists -> Continue (existing behavior preserved)
   branch_exists_for_issue() { echo ""; }
   get_pr_for_issue() { echo "501"; }
   export -f branch_exists_for_issue get_pr_for_issue
   BLOCKED='[{"content":{"number":301},"labels":[],"status":"Blocked"}]'
   DISPATCHED=""
   dispatch_stage stage_blocked_retry > /dev/null
   assert_eq "Z2: no branch, PR exists -> Continue" \
     "1" "$(grep -c 'dispatch Continue issue #301' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # Z3: neither branch nor PR -> Fix (unchanged behavior)
   branch_exists_for_issue() { echo ""; }
   get_pr_for_issue() { echo ""; }
   export -f branch_exists_for_issue get_pr_for_issue
   BLOCKED='[{"content":{"number":302},"labels":[],"status":"Blocked"}]'
   DISPATCHED=""
   dispatch_stage stage_blocked_retry > /dev/null
   assert_eq "Z3: neither branch nor PR -> Fix" \
     "1" "$(grep -c 'dispatch Fix issue #302' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # Z4: branch probe comes back empty (simulating a git-ls-remote error or absent
   # `remote-url`), PR exists -> still Continue via the get_pr_for_issue fallback, not a
   # crash and not a false Fix.
   branch_exists_for_issue() { echo ""; }
   get_pr_for_issue() { echo "502"; }
   export -f branch_exists_for_issue get_pr_for_issue
   BLOCKED='[{"content":{"number":303},"labels":[],"status":"Blocked"}]'
   DISPATCHED=""
   dispatch_stage stage_blocked_retry > /dev/null
   assert_eq "Z4: branch probe empty, PR exists -> Continue via fallback" \
     "1" "$(grep -c 'dispatch Continue issue #303' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # Restore stubs to section defaults
   get_pr_for_issue() { echo ""; }
   export -f get_pr_for_issue
   ```

2. Verify the new assertions fail against today's `stage_blocked_retry` (PR-only check):
   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -3
   ```
   Expected:
   ```
   Results: 244 passed, 5 failed
   ```
   Only `Y4`, `Z1`, and `Z1b` are new failures (branch-only signal is currently ignored,
   so both the end-to-end Y4 case and Z1 dispatch `Fix` instead of `Continue`) — plus the
   2 pre-existing `G2`/`I2` failures, for 5 total. `Y4b`/`Y4c` (the token-leak absence
   checks) and `Z2`/`Z3` already pass unmodified. **`Z4` also already passes before the
   fix** — branch-probe-empty + PR-present is exactly today's PR-only path, so it cannot
   fail pre-fix by construction; don't expect it in the failing set.

3. Update `stage_blocked_retry`'s dispatch decision in `scheduler.sh`. Change:
   ```bash
       # Branch-aware: a blocked item that already has a PR (e.g. red CI gated above, or a
       # continue run that failed mid-way) must be CONTINUED to reuse the existing branch.
       # Dispatching "Fix" would start a fresh branch that collides with the PR on push.
       if [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
   ```
   to:
   ```bash
       # Branch-aware: a blocked item whose feat branch already exists on origin (pushed but
       # PR creation failed, e.g. #366's GraphQL exhaustion — or a PR already exists, e.g. red
       # CI gated above) must be CONTINUED to reuse the existing branch. Dispatching "Fix"
       # would start a fresh branch from main that collides with the pushed branch on push
       # (#371). Branch probe first: no API quota cost, and a strict superset of the PR check
       # (a PR can't exist without its source branch).
       if [ -n "$(branch_exists_for_issue "$ISSUE")" ] || [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
   ```
   (the `if dispatch "Continue issue #${ISSUE}"; then ... else ... fi` body is unchanged).

4. Update section V's stale comment so its unmodified `get_pr_for_issue`-only tail isn't
   misread as covering the new branch-aware logic. In `tests/test_scheduler.sh`, change:
   ```bash
   export -f dispatch get_pr_for_issue

   _run_blocked_retry_body() {
   ```
   to:
   ```bash
   export -f dispatch get_pr_for_issue

   # Hand-copied shadow of the pre-#371 retry-accounting path only (signature/pause/
   # delivery-failure/ceiling handling) — it predates the dispatch_stage seam and still
   # ends on the unmodified get_pr_for_issue-only tail below. It does NOT cover the
   # Continue/Fix dispatch decision: that is exercised against the real function via
   # `dispatch_stage stage_blocked_retry` in section Z.
   _run_blocked_retry_body() {
   ```

5. Verify the full suite passes with no regressions:
   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -3
   ```
   Expected:
   ```
   Results: 247 passed, 2 failed
   ```
   (247 = 234 baseline + 13 new assertions across Y1/Y1b/Y2/Y3/Y3b/Y4/Y4b/Y4c/Z1/Z1b/Z2/Z3/Z4;
   the 2 failures — `G2`/`I2`, `set_board_status` grace-window assertions — are
   pre-existing on `main` and unrelated to this change.)

6. Run the full Python suite to confirm no incidental regression elsewhere:
   ```bash
   python -m pytest tests/ -v
   ```

7. Commit:
   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(#371): dispatch Continue from stage_blocked_retry when the feat branch already exists"
   ```

---

## Notes for the implementer

- The two new sections are lettered `Y` and `Z`, continuing the file's existing
  alphabetic section convention (last existing section is `X`).
- `timeout 30 git ls-remote ...` execs a real `git` binary via `execvp`, which cannot see
  bash functions — even exported ones. That's why `git` must be added to the PATH-shim
  loop (Task 1 step 1), matching the existing `gh`/`docker` pattern documented at
  `tests/test_scheduler.sh`'s `# ---- Subprocess-visible stubs ----` block. Argv from a
  shimmed subprocess call lands in `$SHIM_LOG`, not `$STUB_LOG` — an exported bash
  function invoked directly (no `timeout`) still logs to `$STUB_LOG` as usual. Get this
  wrong and the git-argv assertions will silently read the wrong log and appear to pass
  or fail for the wrong reason.
- Section N (`tests/test_scheduler.sh`, existing code, not modified by this plan)
  permanently overwrites the shared `python3` stub function with its own `--id`-routing
  version and never restores the original — `reset_python3_stub()` only clears the
  `PROVIDERS_CLI_OUTPUT` variable, not the function body. Any later section (ours
  included) that needs the generic `PROVIDERS_CLI_OUTPUT`-echoing `python3` stub back
  must redefine it inline, exactly as section Y's first block does. This was verified by
  hand (a first draft without this redefinition silently returned an empty URL from
  `branch_exists_for_issue` — no crash, no error, just a wrong empty result).
- Requirement 6 (never leak the token-embedded URL) is asserted at two levels: Y1
  confirms the helper's own stdout is only the ref, never the URL; Y4b/Y4c confirm the
  same at the `dispatch_stage` call site using a distinct fake-token string
  (`ghs_zzfaketoken371`) grepped out of both the captured stdout/stderr and `$STUB_LOG`.
- Do not use `"$(grep -c PATTERN FILE || echo 0)"` for an assertion that expects `"0"` —
  `grep -c` already prints `0` on no match and exits `1`, so `|| echo 0` doubles the
  output to `"0\n0"` and the assertion fails on a false mismatch. Use `|| true` for
  expected-`"0"` assertions (matches the file's own existing convention elsewhere, e.g.
  `"0" "$(grep -c ... "$STUB_LOG" || true)"`); reserve `|| echo 0` for expected-`"1"`
  assertions where it's just a safety net against `grep` erroring outright.

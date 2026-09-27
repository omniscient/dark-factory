# Implementation Plan: Run non-executable `.factory/hooks/*` via bash, loudly and durably, instead of silently falling back to the built-in default

**Issue:** #438

**Spec:** `docs/superpowers/specs/2026-09-26-hooks-non-executable-fallback-design.md` (amended at `f1b0ecb`: Req 9 entrypoint pre-check, Req 3/10/11 durable warning token, Req 5 non-empty-regular-file test, Req 12/13 test isolation and runner)

---

## Goal

Today `run_hook()` (`scripts/hooks.sh:23`) dispatches on one `[ -x "$hook" ]` check. It
treats a hook committed without the executable bit (mode `100644`, the Windows default) as
missing. `smoke-gate` then runs MarketHawk's `_default_smoke_gate`, which latches a false
`main-is-red`. Every other name silently no-ops. Separately, `entrypoint.sh:851` has its own
`[ -x .factory/hooks/validate ]` pre-check, which sends a non-executable validate hook into
MarketHawk's inline `npx tsc` and escalates the ticket to Blocked.

After this change:

- A hook counts as **present** when it is a non-empty regular file
  (`[ -f "$hook" ] && [ -s "$hook" ]`). A present hook always runs. If it is executable, it
  runs directly. If not, it runs as `bash "$hook" "$@"`, with the same env contract, the same
  `--gate` rc and the same `_smoke_on_green`/`_smoke_on_red` routing.
- A present-but-non-executable hook produces three traces, all carrying the stable token
  `hook-not-executable`:
  - a loud stderr warning that includes `crlf=<yes|no>`;
  - one durable line in `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/hook-warnings.log`;
  - for a failing smoke-gate hook, a note in the regression ticket body or follow-up comment.
- A hook that is absent, a directory, or a zero-byte placeholder still runs the built-in
  default, exactly as today (that branch belongs to #436).
- `entrypoint.sh`'s deconflict validate pre-check uses the same present test, so a
  non-executable validate hook reaches `run_hook`.
- Three doc statements that would become false are corrected: the `scripts/hooks.sh` header,
  `docs/onboarding-new-target.md` §3c and `README.md` §Hooks.

## Architecture

- **`scripts/hooks.sh` `run_hook()`:**
  - The outer condition `[ -x "$hook" ]` becomes `[ -f "$hook" ] && [ -s "$hook" ]` (Req 5).
    The `else` default branch is byte-for-byte unchanged.
  - Inside the present branch, a `local -a invoke` array holds the interpreter prefix:
    `("$hook")` when executable, `(bash "$hook")` when not (Req 1, 2, 4).
  - The non-executable sub-branch does three things before invoking:
    - prints the Req 3 warning (token, path, `crlf=` via `grep -q $'\r'` detection only);
    - appends the Req 10 durable line. The append is guarded by `[ -d "$state_dir" ]`, never
      runs `mkdir`, and ends in `2>/dev/null >> … || true` so it can never trip
      `entrypoint.sh`'s `set -e`. `2>/dev/null` comes **before** `>>` because redirections
      apply left to right; otherwise a permission-denied `>>` would still print its error;
    - sets `HOOK_NOT_EXECUTABLE_NOTE` (Req 11).
  - Both existing invocation sites swap `"$hook"` for `"${invoke[@]}"`. Every other part of
    the function is unchanged: the smoke-gate/other-name branch shapes, `|| rc=$?`, and the
    final `--gate` return.
- **`HOOK_NOT_EXECUTABLE_NOTE` is declared `local` in `run_hook`.** Bash `local` is
  dynamically scoped, so `_smoke_on_red`, which `run_hook` calls, can see it. It is reset to
  `""` on every `run_hook` call and never leaks into a later call or into a direct
  `run_smoke_gate`/`_default_smoke_gate` call. `_smoke_on_red` reads it as
  `${HOOK_NOT_EXECUTABLE_NOTE:-}`, which is safe under `set -u`.
- **`smoke_gate.sh` `_smoke_on_red()`:** builds `HOOK_NOTE` (empty unless
  `HOOK_NOT_EXECUTABLE_NOTE` is non-empty). It appends `HOOK_NOTE` to the new-ticket heredoc
  body and to the "main still red" follow-up comment. Only text is added: when the gate fires,
  its exit, the sentinel files and the ticket state machine are all unchanged (Req 11). This
  follows the `TEARDOWN_NOTE` pattern at `workflows/archon-dark-factory.yaml:214-222`.
- **`entrypoint.sh:851`:** `[ -x "$CLONE_DIR/.factory/hooks/validate" ]` becomes the same
  `-f`/`-s` test (Req 9). The inline-tsc `else` path for an absent hook is unchanged.
- **Warning text.** Req 3 fixes the template. The `path=` field uses the full resolved
  `${hook}`. The remediation uses the repo-relative `.factory/hooks/<name>`, which is the form
  an operator can paste into their own checkout.
- **Interaction with #436.** The two specs meet only at the `-f`/`-s` test. This plan never
  edits the `else` default branch, `_default_smoke_gate`, or `_smoke_check_main`.

## Tech Stack

Bash (`scripts/hooks.sh`, `smoke_gate.sh`, `entrypoint.sh`, `tests/test_hooks.sh`) and
Python/pytest (one new static test). `local -a` is already used in `scheduler.sh`, and the
container and CI run bash ≥ 5.

`tests/test_hooks.sh` runs in CI as `bash tests/test_hooks.sh` (`.github/workflows/ci.yml:16`).
That step runs **before** `/var/lib/dark-factory` is created (`ci.yml:23`), and the test
exports `SCHEDULER_STATE_DIR="$TMP/state"`, so the durable log lands in the test's temp dir.
The new `tests/test_entrypoint_validate_hook_precheck.py` is collected by
`python -m pytest tests/ -v`. No CI wiring change is needed.

**Memory lessons applied:**
- `codebase-patterns.md` `[AVOID]`: verify claimed artifacts rather than trusting commit
  messages. `d10e464`'s onboarding §3c and `templates/new-target/.gitattributes` were checked
  in the tree. Task 5 edits only the §3c sentence that Req 8 names, and keeps the chmod block.
- Test stubs for `gh`/`python3` stay offline (#348 lesson baked into `tests/test_hooks.sh`).
  The new red cases re-stub both, so they never reach the real providers CLI.

## File Structure

| File | Change | Purpose |
|---|---|---|
| `tests/test_hooks.sh` | Modify (append only) | New block after the existing exactly-one-`tracker create` assertions (`:53-56`). It covers: Neg-B log-absence check; cases 6/6b/6c (non-exec `validate`: body runs, token, durable line, rc 3 propagation, CRLF reported not normalised); 7/8/8b (non-exec `smoke-gate`: green clears the sentinel, red writes it, and the ticket body and follow-up comment both name the hook); 9 (Neg-A: absent/zero-byte/dir hook → default runs, no token); 10 (Neg-B explicit: an executable hook never warns). Cases 1-5 are untouched. |
| `scripts/hooks.sh` | Modify | Header contract; `run_hook`: `-f`/`-s` presence test, `invoke` prefix array, warning, durable line, `HOOK_NOT_EXECUTABLE_NOTE`. |
| `smoke_gate.sh` | Modify | `_smoke_on_red`: append `HOOK_NOTE` to the ticket body and follow-up comment text. |
| `entrypoint.sh` | Modify (1 line + 2 comment lines) | Deconflict validate pre-check `-x` → `-f`/`-s`. |
| `tests/test_entrypoint_validate_hook_precheck.py` | Create | Static checks on `entrypoint.sh`/`hooks.sh` text (Req 9/13), which run off-image under pytest. |
| `docs/onboarding-new-target.md` | Modify (§3c first paragraph) | Replace "silently ignored…MarketHawk default runs instead" with the new behavior; the chmod block is kept. |
| `README.md` | Modify (§Hooks, after line 225) | Note that a non-executable hook still runs, loudly, via `bash`, and that an empty file counts as absent. |

Explicitly **not** touched: `templates/new-target/**`, `.gitattributes`, `workflows/**`,
`scheduler.sh`, `scripts/factory_core/verifier.py`,
`refinement-skills/VERIFIER-CONTRACT.md`, the default branch, `_default_smoke_gate` and
`_smoke_check_main`.

---

## Task 1: Add failing `tests/test_hooks.sh` cases for non-executable hooks

**Files:** `tests/test_hooks.sh`

Placement and isolation (Req 12):
- The block goes **after** line 56 (the exactly-one-`tracker create` and `main-is-red-issue`
  assertions), so the extra red cases cannot break that count.
- Each case sets its own hook body and runs an explicit `chmod -x`/`chmod +x`. No case relies
  on a mode surviving a `printf` overwrite.
- Sentinel state is set explicitly before each smoke-gate case: `touch` before green; `rm -f`
  of the sentinel and issue file before the first red case.
- From case 7 onward, `_smoke_check_main` is replaced by a counter stub. Any fall-through to
  the built-in default is then observable (Req 12 Neg-A mechanism) while `_default_smoke_gate`
  keeps its real routing.

- [ ] **Step 1.1: Append the new cases.** In `tests/test_hooks.sh`, replace the final line

  ```bash
  echo PASS
  ```

  with exactly:

  ```bash
  # --- #438: present-but-non-executable hooks (cases below run AFTER the exactly-one
  # --- tracker-create assertion above; each case resets the sentinel/hook mode it needs).
  WARN_LOG="$SCHEDULER_STATE_DIR/hook-warnings.log"
  # Negative case B (no log noise): executable hooks in cases 2-5 took the direct-exec path,
  # which never warns — so no durable hook-not-executable line may exist yet.
  [ ! -e "$WARN_LOG" ] || { echo "FAIL: executable hooks must not write $WARN_LOG"; exit 1; }
  # 6) non-executable validate hook: body still runs (via bash) with the env contract, a
  #    hook-not-executable warning goes to stderr and one durable line to $WARN_LOG
  rm -f "$ARTIFACTS_DIR/hook-ran"
  printf '#!/usr/bin/env bash\necho "$CLONE_DIR" > "$ARTIFACTS_DIR/hook-ran"\n' > "$TMP/.factory/hooks/validate"
  chmod -x "$TMP/.factory/hooks/validate"
  ISSUE_NUM=438 run_hook validate 2> "$TMP/stderr-6"
  grep -q "$TMP" "$ARTIFACTS_DIR/hook-ran" || { echo "FAIL: non-exec hook body must run with the env contract"; exit 1; }
  grep -q "hook-not-executable path=$TMP/.factory/hooks/validate crlf=no" "$TMP/stderr-6" \
    || { echo "FAIL: non-exec hook must emit the hook-not-executable token + path on stderr"; exit 1; }
  [ "$(grep -c "hook-not-executable $TMP/.factory/hooks/validate issue=438 crlf=no" "$WARN_LOG")" = "1" ] \
    || { echo "FAIL: non-exec hook must append exactly one durable line to $WARN_LOG"; exit 1; }
  # 6b) exit-code propagation: a non-exec validate hook exiting 3 makes --gate return 3
  printf '#!/usr/bin/env bash\nexit 3\n' > "$TMP/.factory/hooks/validate"
  chmod -x "$TMP/.factory/hooks/validate"
  RC=0; run_hook --gate validate 2>/dev/null || RC=$?
  [ "$RC" = "3" ] || { echo "FAIL: --gate must propagate a non-exec hook's rc 3, got ${RC}"; exit 1; }
  # 6c) CRLF is reported (detection only, never normalised)
  printf '#!/usr/bin/env bash\r\ntrue\r\n' > "$TMP/.factory/hooks/validate"
  chmod -x "$TMP/.factory/hooks/validate"
  run_hook validate 2> "$TMP/stderr-6c"
  grep -q "hook-not-executable path=$TMP/.factory/hooks/validate crlf=yes" "$TMP/stderr-6c" \
    || { echo "FAIL: CRLF non-exec hook must be reported as crlf=yes"; exit 1; }
  grep -q $'\r' "$TMP/.factory/hooks/validate" || { echo "FAIL: run_hook must not normalise CRLF"; exit 1; }
  # The built-in default's check is counted from here on, so any fall-through is observable.
  # NOTE: this override persists to the end of the script.
  DEFAULT_CHECKS="$TMP/default-checks.log"; : > "$DEFAULT_CHECKS"
  # shellcheck disable=SC2317
  _smoke_check_main() { echo ran >> "$DEFAULT_CHECKS"; return 0; }
  # Capture what _smoke_on_red posts: new-ticket body (python3 tracker create --body-file)
  # and follow-up comment (gh issue comment --body). Both stubs stay offline (#348).
  RED_TEXT="$TMP/red-text.log"; : > "$RED_TEXT"
  # shellcheck disable=SC2317
  gh() { if [ "${1:-}" = "issue" ] && [ "${2:-}" = "comment" ]; then printf '%s\n' "$*" >> "$RED_TEXT"; fi; return 0; }
  # shellcheck disable=SC2317
  python3() {
    echo "python3 $*" >> "$STUB_LOG"
    if echo "$*" | grep -q "tracker create"; then
      local prev="" a
      for a in "$@"; do
        if [ "$prev" = "--body-file" ]; then cat "$a" >> "$RED_TEXT"; fi
        prev="$a"
      done
      echo "999"
      return 0
    fi
    python "$@"
  }
  # 7) non-executable smoke-gate GREEN clears the sentinel via _smoke_on_green
  touch "$SCHEDULER_STATE_DIR/main-is-red"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/.factory/hooks/smoke-gate"
  chmod -x "$TMP/.factory/hooks/smoke-gate"
  run_hook --gate smoke-gate 2> "$TMP/stderr-7"
  [ ! -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec green hook must clear sentinel"; exit 1; }
  grep -q "hook-not-executable path=$TMP/.factory/hooks/smoke-gate" "$TMP/stderr-7" \
    || { echo "FAIL: non-exec smoke-gate hook must warn"; exit 1; }
  # 8) non-executable smoke-gate RED (fresh state: green above removed sentinel + issue file)
  #    routes through _smoke_on_red: sentinel written, clean halt, new ticket names the hook
  rm -f "$SCHEDULER_STATE_DIR/main-is-red" "$SCHEDULER_STATE_DIR/main-is-red-issue"
  printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/.factory/hooks/smoke-gate"
  chmod -x "$TMP/.factory/hooks/smoke-gate"
  ( run_hook --gate smoke-gate 2>/dev/null )   # subshell: _smoke_on_red exits 0
  RC=$?
  [ "$RC" = "0" ] || { echo "FAIL: non-exec red smoke-gate must clean-halt with exit 0"; exit 1; }
  [ -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec red hook must write sentinel"; exit 1; }
  grep -q "hook-not-executable $TMP/.factory/hooks/smoke-gate" "$RED_TEXT" \
    || { echo "FAIL: red ticket body must name the non-exec hook"; exit 1; }
  # 8b) still red with a ticket on file (main-is-red-issue=999) → the follow-up comment names it too
  : > "$RED_TEXT"
  ( run_hook --gate smoke-gate 2>/dev/null )
  grep -q "hook-not-executable $TMP/.factory/hooks/smoke-gate" "$RED_TEXT" \
    || { echo "FAIL: red follow-up comment must name the non-exec hook"; exit 1; }
  [ ! -s "$DEFAULT_CHECKS" ] || { echo "FAIL: built-in default must not run while a non-empty hook file exists"; exit 1; }
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
  # 10) Negative case B (explicit): an executable hook never warns
  printf '#!/bin/sh\nexit 0\n' > "$TMP/.factory/hooks/validate"
  chmod +x "$TMP/.factory/hooks/validate"
  run_hook --gate validate 2> "$TMP/stderr-10"
  if grep -q "hook-not-executable" "$TMP/stderr-10"; then echo "FAIL: executable hook must not warn"; exit 1; fi
  [ "$(wc -l < "$WARN_LOG")" = "$WARN_LINES" ] || { echo "FAIL: executable hook must not write $WARN_LOG"; exit 1; }
  echo PASS
  ```

  How the cases map to the spec:
  - Neg-B pre-check plus case 10 → Req 3 ("must not fire on the executable path") and Req 12
    Neg-B. Cases 2-5 already ran executable hooks, so the absence of `hook-warnings.log` proves
    they emitted nothing on the warning path. The stderr warning and the durable line come from
    the same branch.
  - Case 6 → Req 6 (body runs with the env contract; token plus path on stderr; the no-op
    default is ruled out because the body's side effect is observed) and Req 10 (exactly one
    durable line with `issue=` and `crlf=`).
  - Case 6b → Req 12 exit-code propagation: rc is **exactly** 3.
  - Case 6c → Req 3 `crlf=yes` detection and Req 4 (the file is not normalised).
  - Cases 7, 8 and 8b → Req 7 (green goes through `_smoke_on_green`, red through
    `_smoke_on_red`, which writes the sentinel and exits 0) and Req 11 (token and path appear
    in both the new-ticket body and the follow-up comment). The empty `$DEFAULT_CHECKS` proves
    the default never ran (Req 1).
  - Case 9 → Req 5 and Req 12 Neg-A: absent, zero-byte and directory hooks each run the default
    once (counter = 3), with no token and no new durable line.

- [ ] **Step 1.2: Verify the new tests fail against the unchanged code.**

  ```bash
  bash tests/test_hooks.sh; echo "exit=$?"
  ```

  Expected (the two `[smoke_gate]` lines come from existing cases 4-5; the temp dir name
  varies):

  ```
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  grep: /tmp/tmp.XXXXXXXXXX/art/hook-ran: No such file or directory
  FAIL: non-exec hook body must run with the env contract
  exit=1
  ```

  This reproduces the bug: the old `[ -x ]` check sends the non-exec `validate` hook to the
  no-op default.

- [ ] **Step 1.3: Do not commit yet.** The tests are committed together with the fix in Task 3,
  so the branch never carries a red `bash tests/test_hooks.sh` commit.

---

## Task 2: `run_hook` — present test, `invoke` prefix, warning, durable line, note

**Files:** `scripts/hooks.sh`

- [ ] **Step 2.1: Update the header contract (Req 8).** In `scripts/hooks.sh`, replace

  ```bash
  # Falls back to built-in defaults when no target hook is present:
  ```

  with

  ```bash
  # A hook counts as present when it is a non-empty regular file ([ -f ] && [ -s ]).
  # A present hook without the executable bit (e.g. committed from Windows as mode
  # 100644) still runs — via `bash "$hook"`, shebang ignored — with a loud
  # `hook-not-executable` warning on stderr plus a durable line in
  # ${SCHEDULER_STATE_DIR}/hook-warnings.log (#438).
  # Falls back to built-in defaults when no target hook is present (absent, a
  # directory, or a zero-byte placeholder):
  ```

- [ ] **Step 2.2: Replace the presence check and build the `invoke` prefix.** In `run_hook()`,
  replace

  ```bash
    local rc=0
    if [ -x "$hook" ]; then
      if [ "$name" = "smoke-gate" ]; then
  ```

  with

  ```bash
    local rc=0
    # Dynamically scoped: _smoke_on_red (called below) appends it to the ticket text.
    local HOOK_NOT_EXECUTABLE_NOTE=""
    if [ -f "$hook" ] && [ -s "$hook" ]; then
      local -a invoke
      if [ -x "$hook" ]; then
        invoke=("$hook")
      else
        local crlf="no"; grep -q $'\r' "$hook" && crlf="yes"
        echo "WARNING: [hooks] hook-not-executable path=${hook} crlf=${crlf} — running it with bash (the hook's own shebang is ignored); fix with: git update-index --chmod=+x .factory/hooks/${name}" >&2
        # Durable trace beyond stderr: dispatch is `run -d --rm`, so the run's stderr
        # never reaches the scheduler log and dies with the container. Guarded, never
        # mkdir (CI asserts /var/lib/dark-factory stays empty), never fatal.
        local state_dir="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
        if [ -d "$state_dir" ]; then
          printf '%s hook-not-executable %s issue=%s crlf=%s\n' \
            "$(date -u +%FT%TZ)" "$hook" "${ISSUE_NUM:-}" "$crlf" \
            2>/dev/null >> "${state_dir}/hook-warnings.log" || true
        fi
        HOOK_NOT_EXECUTABLE_NOTE="hook-not-executable ${hook} (crlf=${crlf})"
        invoke=(bash "$hook")
      fi
      if [ "$name" = "smoke-gate" ]; then
  ```

- [ ] **Step 2.3: Route both invocation sites through `invoke`.** The string
  `FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@"` occurs exactly twice in
  `run_hook()`: the smoke-gate `if` (old line 29) and the non-smoke-gate `|| rc=$?` (old
  line 37). Replace **both** occurrences with:

  ```bash
  FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@"
  ```

  Keep everything else byte-for-byte: the smoke-gate comment block,
  `_smoke_on_green`/`rc=0`, `_smoke_on_red`, the `else case "$name" in … esac` default branch
  and the final `if [ "$gate" = "1" ] …` return.

  Check (fixed-string grep, so it doesn't depend on which grep is on PATH):
  `grep -cF '"${invoke[@]}" "$@"' scripts/hooks.sh` → `2`;
  `grep -cF '"$hook" "$@"' scripts/hooks.sh` → `0`.

- [ ] **Step 2.4: Verify progress. The remaining failure must be the ticket text (Task 3).**

  ```bash
  bash tests/test_hooks.sh 2>&1 | tail -3; echo "exit=${PIPESTATUS[0]}"
  ```

  Expected:

  ```
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  FAIL: red ticket body must name the non-exec hook
  exit=1
  ```

  The two `[smoke_gate]` lines here come from new cases 7 (green) and 8 (red). Cases 6, 6b,
  6c and 7 now pass. Case 8 fails only because `_smoke_on_red` does not yet use
  `HOOK_NOT_EXECUTABLE_NOTE`.

---

## Task 3: `_smoke_on_red` names the non-executable hook in the ticket text

**Files:** `smoke_gate.sh`

- [ ] **Step 3.1: Build `HOOK_NOTE`.** In `_smoke_on_red()`, replace

  ```bash
    local ISSUE_FILE="${SMOKE_STATE_DIR}/main-is-red-issue"
    local FILE_NUM=""
    [ -f "$ISSUE_FILE" ] && FILE_NUM=$(cat "$ISSUE_FILE")

    local OPEN_NUMS="" LIST_OK=1
  ```

  with

  ```bash
    local ISSUE_FILE="${SMOKE_STATE_DIR}/main-is-red-issue"
    local FILE_NUM=""
    [ -f "$ISSUE_FILE" ] && FILE_NUM=$(cat "$ISSUE_FILE")

    # Set by hooks.sh run_hook when the check that just failed was the target's own
    # non-executable hook run via bash (#438) — say so in the ticket text.
    local HOOK_NOTE=""
    if [ -n "${HOOK_NOT_EXECUTABLE_NOTE:-}" ]; then
      HOOK_NOTE="

  The failing check was the target's own smoke-gate hook, run with bash because it is not executable: ${HOOK_NOT_EXECUTABLE_NOTE}. Fix with \`git update-index --chmod=+x .factory/hooks/smoke-gate\`."
    fi

    local OPEN_NUMS="" LIST_OK=1
  ```

  (The quoted string spans three lines: two newlines, then the note line starting at column 0
  of the file. The leading two spaces above are only this plan's list indentation. In
  `smoke_gate.sh`, the line `The failing check was …` has **no** leading whitespace, so the
  ticket's Markdown does not render it as a code block.)

- [ ] **Step 3.2: Append it to the follow-up comment.** Replace

  ```bash
        --body "main still red at $(date -u +%FT%TZ) — factory implementation runs remain paused." \
  ```

  with

  ```bash
        --body "main still red at $(date -u +%FT%TZ) — factory implementation runs remain paused.${HOOK_NOTE}" \
  ```

- [ ] **Step 3.3: Append it to the new-ticket body.** Inside the `cat > "$BODY_FILE" << EOF`
  heredoc, replace

  ```
  This ticket closes automatically on the next green gate pass.
  EOF
  ```

  with

  ```
  This ticket closes automatically on the next green gate pass.${HOOK_NOTE}
  EOF
  ```

  Nothing else in `smoke_gate.sh` changes: the sentinel `touch`es, the `exit 0`, the ticket
  title, labels, adopt/close-duplicate logic, `_smoke_on_green`, `_default_smoke_gate` and
  `_smoke_check_main` all stay as they are.

- [ ] **Step 3.4: Verify all hook tests pass.**

  ```bash
  bash tests/test_hooks.sh; echo "exit=$?"
  ```

  Expected:

  ```
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  PASS
  exit=0
  ```

  The lines come from: cases 4 and 5; case 7 (green); cases 8 and 8b (red twice); and case
  9a (the default runs with the stub check green and clears the sentinel). Cases 9b and 9c
  print nothing because there is no sentinel left to clear. The `WARNING: [hooks]` lines are
  redirected to temp files or `/dev/null` by the tests.

- [ ] **Step 3.5: Run the sibling smoke-gate suite (it sources `smoke_gate.sh` directly).**

  ```bash
  bash tests/test_smoke_gate.sh 2>&1 | tail -1
  ```

  Expected: `Results: N passed, 0 failed` (27 passed when this plan was written; `0 failed` is
  what matters).

- [ ] **Step 3.6: Commit the tests and the fix together.**

  ```bash
  git add tests/test_hooks.sh scripts/hooks.sh smoke_gate.sh
  git commit -m "fix(hooks): run non-executable .factory/hooks/* via bash instead of the built-in default (#438)

  run_hook dispatched on [ -x \"\$hook\" ], so a hook committed as mode 100644
  (the Windows default) was treated as missing: smoke-gate ran the MarketHawk
  default (false main-is-red) and validate silently no-op'd. A hook is now
  present when it is a non-empty regular file; a present non-exec hook runs as
  bash \"\$hook\" with the same env contract, gate rc and green/red routing, and
  leaves a hook-not-executable trace on stderr (with crlf=), in
  \$SCHEDULER_STATE_DIR/hook-warnings.log, and in the main-is-red ticket text.
  Absent, directory and zero-byte hooks keep the built-in default (#436).

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 4: `entrypoint.sh` deconflict validate pre-check stops pre-empting `run_hook`

**Files:** `tests/test_entrypoint_validate_hook_precheck.py` (create), `entrypoint.sh`

- [ ] **Step 4.1: Write the static test.** Create
  `tests/test_entrypoint_validate_hook_precheck.py` with exactly:

  ```python
  """Static checks for #438: entrypoint.sh's deconflict validate pre-check must not pre-empt run_hook.

  A present-but-non-executable .factory/hooks/validate has to reach run_hook (which warns and runs
  it via bash) instead of falling into MarketHawk's inline `npx tsc --noEmit`. Verified as text so
  it runs off-image, in `python -m pytest tests/ -v`.
  """
  from pathlib import Path

  REPO_ROOT = Path(__file__).resolve().parents[1]
  ENTRYPOINT = (REPO_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
  HOOKS = (REPO_ROOT / "scripts" / "hooks.sh").read_text(encoding="utf-8")


  def test_validate_precheck_no_longer_requires_exec_bit():
      assert '[ -x "$CLONE_DIR/.factory/hooks/validate" ]' not in ENTRYPOINT


  def test_validate_precheck_uses_non_empty_regular_file_test():
      assert (
          'if [ -f "$CLONE_DIR/.factory/hooks/validate" ] && '
          '[ -s "$CLONE_DIR/.factory/hooks/validate" ]; then'
      ) in ENTRYPOINT


  def test_precheck_matches_run_hook_presence_test():
      # entrypoint.sh and run_hook must agree on what "a hook is present" means.
      assert 'if [ -f "$hook" ] && [ -s "$hook" ]; then' in HOOKS


  def test_validate_precheck_still_routes_through_run_hook_gate():
      precheck = ENTRYPOINT.index('[ -s "$CLONE_DIR/.factory/hooks/validate" ]')
      assert ENTRYPOINT.index("run_hook --gate validate", precheck) > precheck
  ```

- [ ] **Step 4.2: Verify it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_entrypoint_validate_hook_precheck.py -v 2>&1 | tail -6
  ```

  Expected: `3 failed, 1 passed`. The three `test_validate_precheck_*` tests fail (the `-x`
  string is still present; the `-f`/`-s` line is absent; `.index` raises `ValueError`).
  `test_precheck_matches_run_hook_presence_test` already passes because of Task 2.

- [ ] **Step 4.3: Change the pre-check.** In `entrypoint.sh` (inside
  `if [ "$INTENT" = "deconflict" ]; then`), replace

  ```bash
    if [ -x "$CLONE_DIR/.factory/hooks/validate" ]; then
  ```

  with

  ```bash
    # Same "present" test as run_hook (non-empty regular file), so a non-executable
    # validate hook reaches run_hook (warn + run via bash) instead of the inline tsc (#438).
    if [ -f "$CLONE_DIR/.factory/hooks/validate" ] && [ -s "$CLONE_DIR/.factory/hooks/validate" ]; then
  ```

  Nothing else in the block changes. The `run_hook --gate validate` call, the
  `_conflict_escalate` message and the whole `else` inline-tsc branch stay as they are (the
  absent-hook path belongs to #436/#222).

- [ ] **Step 4.4: Verify it passes, and check the syntax.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_entrypoint_validate_hook_precheck.py -v 2>&1 | tail -2
  bash -n entrypoint.sh && echo syntax-ok
  ```

  Expected: `4 passed`, then `syntax-ok`.

- [ ] **Step 4.5: Commit.**

  ```bash
  git add tests/test_entrypoint_validate_hook_precheck.py entrypoint.sh
  git commit -m "fix(deconflict): let a non-executable validate hook reach run_hook (#438)

  entrypoint.sh's own [ -x .factory/hooks/validate ] pre-check sent a mode-100644
  validate hook into MarketHawk's inline npx tsc and escalated the ticket to
  Blocked. Use the same non-empty-regular-file test as run_hook; the absent-hook
  inline-tsc fallback is unchanged.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 5: Correct the three shipped statements that became false (Req 8)

**Files:** `docs/onboarding-new-target.md`, `README.md` (the `scripts/hooks.sh` header was
already done in Step 2.1)

- [ ] **Step 5.1: Onboarding §3c.** In `docs/onboarding-new-target.md`, replace

  ```markdown
  The factory runs a hook only if `[ -x hook ]`. **A non-executable hook is silently
  ignored, and the MarketHawk default runs instead** (tracked in #438). On Windows,
  `git add` records mode 100644, so set the bit explicitly:
  ```

  with

  ```markdown
  A hook without the executable bit still runs, but only through `bash hook`, so its
  shebang is ignored. Each run also logs a loud `hook-not-executable` warning in three
  places: the run's stderr, a line in `hook-warnings.log` in the scheduler state
  directory (`/var/lib/dark-factory` by default), and the main-is-red ticket when a
  smoke-gate hook fails (#438). The warning reports `crlf=yes` if the hook has CRLF line
  endings; the container's bash does not tolerate them. An empty hook file is treated
  as absent, so the built-in default runs. Set the bit anyway. On Windows, `git add`
  records mode 100644, so set it explicitly:
  ```

  The `bash` block that follows (`git update-index --chmod=+x .factory/hooks/*` …) stays
  unchanged.

- [ ] **Step 5.2: README §Hooks.** In `README.md`, replace

  ```markdown
  Place executable scripts at `.factory/hooks/<name>` in the target repo.
  The factory discovers and runs them at the appropriate pipeline stage.
  ```

  with

  ```markdown
  Place executable scripts at `.factory/hooks/<name>` in the target repo.
  The factory discovers and runs them at the appropriate pipeline stage.
  A hook without the executable bit still runs, through `bash` with its shebang ignored,
  and logs a loud `hook-not-executable` warning. Fix it with
  `git update-index --chmod=+x .factory/hooks/<name>`. An empty hook file counts as absent.
  ```

- [ ] **Step 5.3: Verify no stale claim remains.**

  ```bash
  grep -rn "silently ignored, and the MarketHawk\|runs a hook only if" README.md docs/onboarding-new-target.md scripts/hooks.sh; echo "grep-exit=$?"
  ```

  Expected: no matches, `grep-exit=1`.

- [ ] **Step 5.4: Commit.**

  ```bash
  git add docs/onboarding-new-target.md README.md
  git commit -m "docs(hooks): non-executable hooks now run via bash with a hook-not-executable warning (#438)

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 6: Full CI-parity verification

**Files:** none (verification only)

- [ ] **Step 6.1: Shell suites that touch hooks or smoke gate.**

  ```bash
  bash tests/test_hooks.sh > /tmp/hooks.out 2>&1; echo "hooks=$? $(tail -1 /tmp/hooks.out)"
  bash tests/test_smoke_gate.sh > /tmp/smoke.out 2>&1; echo "smoke=$? $(tail -1 /tmp/smoke.out)"
  bash tests/test_side_effect_shims.sh >/dev/null 2>&1; echo "shims=$?"
  ```

  Expected: `hooks=0 PASS`, `smoke=0 Results: N passed, 0 failed`, `shims=0`.

- [ ] **Step 6.2: The Python suite exactly as CI runs it.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q 2>&1 | tail -3
  ```

  Expected: 0 failed. That includes the 4 new tests in
  `tests/test_entrypoint_validate_hook_precheck.py`. No other Python test pins the changed
  lines: `tests/test_verify_skill_files.py` references `tests/test_hooks.sh` only by name.

- [ ] **Step 6.3: The entrypoint shell suites.** They source `entrypoint.sh`. Syntax and
  behavior are unchanged outside deconflict validate.

  ```bash
  for t in tests/test_entrypoint_current_run.sh tests/test_entrypoint_preflight.sh tests/test_entrypoint_session_window.sh tests/test_entrypoint_error_signature.sh; do bash "$t" >/dev/null 2>&1 && echo "ok $t" || echo "FAIL $t"; done
  ```

  Expected: four `ok` lines. If `preflight`/`session_window`/`error_signature` fail only
  because `/var/lib/dark-factory` is missing locally, CI creates it at `ci.yml:23`. Compare
  against `git stash`-ed `main` before treating the failure as a regression.

- [ ] **Step 6.4: Workflow DAG checks** (CI `dag-check` job; no workflow change here, run for
  parity):

  ```bash
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && \
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml; echo "exit=$?"
  ```

  Expected: `exit=0`.

- [ ] **Step 6.5: Scope check.**

  ```bash
  git diff --stat origin/main HEAD -- . ':!docs/superpowers'
  ```

  Expected: exactly `README.md`, `docs/onboarding-new-target.md`, `entrypoint.sh`,
  `scripts/hooks.sh`, `smoke_gate.sh`, `tests/test_entrypoint_validate_hook_precheck.py` and
  `tests/test_hooks.sh`.

- [ ] **Step 6.6: If any step above fails**, fix it within the seven files above only. Re-run
  the failing task's verify step and all of Task 6, then commit the fix as a new commit (no
  amend) with a `fix(hooks): <what> (#438)` subject.

> Note (Req 13): on a Windows host, MSYS/Git-Bash strips `\r`, so case 6c's `crlf=yes`
> **detection** still passes there, but only Linux CI (or the same command inside
> `ghcr.io/omniscient/dark-factory:latest`) proves the bash-run behavior.

---

## Spec Requirement Traceability

| Spec requirement | Covered by |
|---|---|
| R1: non-exec runs via `bash "$hook" "$@"`, same env contract and routing, never the default | Steps 2.2-2.3; cases 6, 6b, 7, 8 (`$DEFAULT_CHECKS` empty) |
| R2: uniform across all four names and independent of `--gate` | Step 2.2 (single `invoke` path, no name check around it); case 6 (no `--gate`), 6b/7/8 (`--gate`); Task 4 closes the `validate` pre-emption |
| R3: loud stderr warning with the `hook-not-executable` token, path, `crlf=`, shebang-ignored note and remediation; every time; never on the exec path | Step 2.2; cases 6, 6c, 7 (token + path + crlf), Neg-B pre-check + case 10 |
| R4: `bash` only; no shebang parsing, no CRLF normalisation | Step 2.2 (`invoke=(bash "$hook")`, `grep -q` detection only); case 6c asserts the file still contains `\r` |
| R5: fallback gated on non-empty regular file; absent/dir/zero-byte unchanged; #436 boundary | Step 2.2 (`[ -f ] && [ -s ]`, `else` untouched); case 9 (absent, zero-byte, dir → default, no token); existing case 1 |
| R6: non-exec `validate` test (body runs, token on stderr, default does not run) | Case 6 |
| R7: non-exec `smoke-gate` green and red through `_smoke_on_green`/`_smoke_on_red` | Cases 7, 8 |
| R8: correct `docs/onboarding-new-target.md:115-117`, `scripts/hooks.sh:4-9` header, `README.md:224`; no templates/`.gitattributes` | Steps 5.1, 2.1, 5.2; File Structure "not touched" list |
| R9: `entrypoint.sh:851` uses the same presence test | Task 4 (static test + change) |
| R10: durable `<ts> hook-not-executable <path> issue=<N> crlf=<y/n>` line in `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/hook-warnings.log`, `[ -d ]`-guarded, no mkdir | Step 2.2; case 6 (exactly one line), case 9/10 (no new lines) |
| R11: token and path in `_smoke_on_red`'s posted text; no behavior change | Steps 2.2 (`HOOK_NOT_EXECUTABLE_NOTE`), 3.1-3.3; cases 8 (new-ticket body), 8b (follow-up comment) |
| R12: placement after `:53-56`, explicit state resets, Neg-A, Neg-B, rc-3 propagation | Task 1 placement notes; Neg-B pre-check, cases 6b, 9, 10 |
| R13: verification runners | Task 6 (`bash tests/test_hooks.sh`, pytest incl. the static R9 test, `test_smoke_gate.sh`, DAG checks) |

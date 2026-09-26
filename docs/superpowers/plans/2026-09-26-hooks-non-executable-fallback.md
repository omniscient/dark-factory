# Implementation Plan: Run non-executable `.factory/hooks/*` via bash instead of silently falling back to the built-in default

**Issue:** #438

**Spec:** `docs/superpowers/specs/2026-09-26-hooks-non-executable-fallback-design.md`

---

## Goal

`run_hook()` (`scripts/hooks.sh`) today dispatches on a single `[ -x "$hook" ]` check, so a
hook file that exists but was committed without the executable bit (mode `100644`, the
default for hooks authored on Windows) is treated as *missing*: `smoke-gate` runs the
MarketHawk built-in `_default_smoke_gate` (false `main-is-red` on any other target) and
every other hook name silently no-ops. After this change, `run_hook` falls back to the
built-in default **only when the file does not exist at all** (`[ -e "$hook" ]`); a present
but non-executable hook runs as `bash "$hook" "$@"` — same env contract, same
`_smoke_on_green`/`_smoke_on_red` routing, same `--gate` rc propagation — and prints a loud,
path-specific stderr warning naming the `git update-index --chmod=+x` remediation every time.

## Architecture

No new files, no new functions. The only production change is inside `run_hook()`:

- The outer condition changes from `[ -x "$hook" ]` to `[ -e "$hook" ]` — the default-fallback
  `else` branch (unchanged) now fires only for a genuinely absent file (spec Requirement 5).
- Inside the exists-branch, a `local -a invoke` array holds the interpreter prefix:
  `("$hook")` when executable, `(bash "$hook")` plus a stderr warning when not (Requirements
  1, 3, 4). Both existing invocation sites (smoke-gate's green/red `if`, and the
  non-smoke-gate `|| rc=$?`) swap `"$hook"` for `"${invoke[@]}"` — one code path for all four
  hook names and both gate modes (Requirement 2). The smoke-gate/non-smoke-gate branch shapes
  and the final `--gate` return are untouched.
- The file-header comment of `scripts/hooks.sh` is updated so it no longer claims the default
  runs whenever the hook is "not present" in the `-x` sense.

The warning text follows the spec's Requirement 3 example: the *full* resolved path
(`${hook}`, i.e. `${CLONE_DIR}/.factory/hooks/<name>`) identifies exactly which file is
non-executable, and the remediation command uses the repo-relative
`.factory/hooks/<name>` — the form an operator can paste into their own checkout (the
container's `CLONE_DIR` path does not exist on the operator's machine).

Fallback interpreter is `bash` only — no shebang parsing, no CRLF normalization
(Requirement 4). No change to onboarding docs, `.gitattributes`, or templates (Requirement 8;
already shipped in `d10e464`).

## Tech Stack

Bash (`scripts/hooks.sh`, `tests/test_hooks.sh`). `local -a` arrays are fine: the container
and CI runners use bash ≥ 4 (verified locally on bash 5.3). `tests/test_hooks.sh` already
runs in CI as `bash tests/test_hooks.sh` (`.github/workflows/ci.yml`) — no CI wiring change.

## File Structure

| File | Change | Purpose |
|---|---|---|
| `tests/test_hooks.sh` | Modify | Append cases 6, 6b, 7 (non-exec `validate` body runs + warning + gate rc; non-exec `smoke-gate` green/red through the same routing, default never runs). Existing cases 1-5 untouched. |
| `scripts/hooks.sh` | Modify | `run_hook`: `-x` → `-e` for the default fallback; `invoke=()` prefix array + warning; header comment. |

No other file is in scope. In particular: `scripts/factory_core/verifier.py`,
`scripts/cost_report_marker_check.py`, `entrypoint.sh`, `smoke_gate.sh`,
`docs/onboarding-new-target.md`, and `templates/**` are **not** touched.

---

## Task 1: Add failing tests for non-executable hooks

**Files:** `tests/test_hooks.sh`

Note on ordering inside the test file: the new cases run *after* existing case 5, which
leaves `main-is-red` written and `main-is-red-issue` = `999`, and after the existing
exactly-one-`tracker create` assertion at line 55. The new cases therefore must not add
assertions on the global `tracker create` count; they assert on the sentinel file and on a
dedicated `DEFAULT_SMOKE_GATE_RAN` marker instead.

- [ ] **Step 1.1: Append the new cases.** In `tests/test_hooks.sh`, replace the final line

  ```bash
  echo PASS
  ```

  with exactly:

  ```bash
  # 6) non-executable (mode 100644) validate hook still runs via bash, with a loud
  #    stderr warning — it must NOT silently fall through to the no-op default (#438)
  rm -f "$ARTIFACTS_DIR/hook-ran"
  printf '#!/usr/bin/env bash\necho "$CLONE_DIR" > "$ARTIFACTS_DIR/hook-ran"\n' > "$TMP/.factory/hooks/validate"
  chmod -x "$TMP/.factory/hooks/validate"
  run_hook validate 2> "$TMP/nonexec-stderr"
  grep -q "$TMP" "$ARTIFACTS_DIR/hook-ran" || { echo "FAIL: non-exec hook body must run with the env contract"; exit 1; }
  grep -q "$TMP/.factory/hooks/validate exists but is not executable" "$TMP/nonexec-stderr" \
    || { echo "FAIL: non-exec hook must warn on stderr naming its path"; exit 1; }
  grep -q "git update-index --chmod=+x .factory/hooks/validate" "$TMP/nonexec-stderr" \
    || { echo "FAIL: non-exec warning must name the remediation"; exit 1; }
  # 6b) --gate still propagates a non-exec hook's own failure exit code
  printf '#!/usr/bin/env bash\nexit 3\n' > "$TMP/.factory/hooks/validate"; chmod -x "$TMP/.factory/hooks/validate"
  if run_hook --gate validate 2>/dev/null; then echo "FAIL: gate must propagate non-exec hook failure"; exit 1; fi
  # 7) non-executable smoke-gate hook reuses the SAME _smoke_on_green/_smoke_on_red
  #    routing as the executable path — never the built-in MarketHawk default.
  #    Override the default after source so any fall-through is observable.
  #    NOTE: this override persists to the end of the script — any case added after
  #    this block must not depend on the real _default_smoke_gate.
  # shellcheck disable=SC2317
  _default_smoke_gate() { echo "DEFAULT_SMOKE_GATE_RAN" >> "$STUB_LOG"; return 0; }
  touch "$SCHEDULER_STATE_DIR/main-is-red"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/.factory/hooks/smoke-gate"
  chmod -x "$TMP/.factory/hooks/smoke-gate"
  run_hook --gate smoke-gate 2> "$TMP/nonexec-smoke-stderr"
  [ ! -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec green hook must clear sentinel"; exit 1; }
  grep -q "$TMP/.factory/hooks/smoke-gate exists but is not executable" "$TMP/nonexec-smoke-stderr" \
    || { echo "FAIL: non-exec smoke-gate hook must warn on stderr"; exit 1; }
  printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/.factory/hooks/smoke-gate"
  chmod -x "$TMP/.factory/hooks/smoke-gate"
  ( run_hook --gate smoke-gate 2>/dev/null )   # subshell: _smoke_on_red exits 0
  RC=$?
  [ "$RC" = "0" ] || { echo "FAIL: non-exec red smoke-gate must clean-halt with exit 0"; exit 1; }
  [ -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec red hook must write sentinel"; exit 1; }
  if grep -q "DEFAULT_SMOKE_GATE_RAN" "$STUB_LOG"; then echo "FAIL: built-in default must not run while the hook file exists"; exit 1; fi
  echo PASS
  ```

  Why each assertion maps to the spec:
  - Case 6 = Requirement 6 (body runs with env contract → `hook-ran` holds `$CLONE_DIR`;
    warning on stderr naming the path; the no-op default is distinguished by the body's
    side effect) + Requirement 3 (remediation text present).
  - Case 6b = Requirement 1's "for every other name: `rc=$?` as today" under `--gate`.
  - Case 7 = Requirement 7 (green clears the sentinel via `_smoke_on_green`; red writes the
    sentinel and clean-halts exit 0 via `_smoke_on_red`) + Requirement 1's "never fall
    through to `_default_smoke_gate`" made explicit by the overriding stub.

- [ ] **Step 1.2: Verify the new tests fail against the unchanged `run_hook`.**

  ```bash
  bash tests/test_hooks.sh; echo "exit=$?"
  ```

  Expected (the two `[smoke_gate]` lines come from existing cases 4-5):

  ```
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  grep: /tmp/tmp.XXXXXXXXXX/art/hook-ran: No such file or directory
  FAIL: non-exec hook body must run with the env contract
  exit=1
  ```

  This is the bug reproduced: the non-exec `validate` hook body never runs; the old code took
  the no-op default.

- [ ] **Step 1.3: Do not commit yet** — the test file is committed together with the fix in
  Task 2 so the branch never has a red `bash tests/test_hooks.sh` commit on it.

---

## Task 2: Implement the `-e`/`invoke` dispatch in `run_hook`

**Files:** `scripts/hooks.sh`

- [ ] **Step 2.1: Update the header comment.** In `scripts/hooks.sh`, replace

  ```bash
  # Falls back to built-in defaults when no target hook is present:
  ```

  with

  ```bash
  # A hook file that exists but lacks the executable bit (e.g. committed from Windows as
  # mode 100644) still runs — via `bash "$hook"`, with a loud stderr warning (#438).
  # Falls back to built-in defaults only when no target hook file exists at all:
  ```

- [ ] **Step 2.2: Switch the outer check to existence and build the `invoke` prefix.** In
  `run_hook()`, replace

  ```bash
    if [ -x "$hook" ]; then
      if [ "$name" = "smoke-gate" ]; then
  ```

  with

  ```bash
    if [ -e "$hook" ]; then
      # Present but not executable → still run it (via bash), never the default.
      local -a invoke
      if [ -x "$hook" ]; then
        invoke=("$hook")
      else
        echo "WARNING: [hooks] ${hook} exists but is not executable (mode 100644?) — running via bash; fix with: git update-index --chmod=+x .factory/hooks/${name}" >&2
        invoke=(bash "$hook")
      fi
      if [ "$name" = "smoke-gate" ]; then
  ```

- [ ] **Step 2.3: Route both invocation sites through `invoke`.** In `run_hook()`, the
  string `FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@"` occurs exactly twice
  (smoke-gate `if` at the old line 29; non-smoke-gate `|| rc=$?` at the old line 37). Replace
  **both** occurrences with:

  ```bash
  FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@"
  ```

  Leave everything else — the smoke-gate comment block, `_smoke_on_green`/`rc=0`,
  `_smoke_on_red`, the `|| rc=$?`, the `else case "$name" in … esac` default branch, and the
  final `if [ "$gate" = "1" ] …` return — byte-for-byte unchanged.

  Resulting function body (for reference / self-check):

  ```bash
  run_hook() {
    local gate=0
    [ "$1" = "--gate" ] && { gate=1; shift; }
    local name="$1"; shift || true
    local hook="${CLONE_DIR}/.factory/hooks/${name}"
    local rc=0
    if [ -e "$hook" ]; then
      # Present but not executable → still run it (via bash), never the default.
      local -a invoke
      if [ -x "$hook" ]; then
        invoke=("$hook")
      else
        echo "WARNING: [hooks] ${hook} exists but is not executable (mode 100644?) — running via bash; fix with: git update-index --chmod=+x .factory/hooks/${name}" >&2
        invoke=(bash "$hook")
      fi
      if [ "$name" = "smoke-gate" ]; then
        # Target hook supplies the CHECK only (exit 0 green / non-zero red).
        # Red/green STATE machinery (sentinel, regression ticket, clean-halt
        # exit 0) stays factory-side — identical semantics to the built-in gate.
        if CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
             FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@"; then
          _smoke_on_green
          rc=0
        else
          _smoke_on_red   # exits 0 (clean halt); unreachable after
        fi
      else
        CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
          FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@" || rc=$?
      fi
    else
      case "$name" in
        smoke-gate) _default_smoke_gate "$@" || rc=$? ;;   # provided by smoke_gate.sh
        *) rc=0 ;;                                          # no default → no-op
      esac
    fi
    if [ "$gate" = "1" ]; then return "$rc"; else return 0; fi
  }
  ```

- [ ] **Step 2.4: Verify the hook tests now pass.**

  ```bash
  bash tests/test_hooks.sh; echo "exit=$?"
  ```

  Expected:

  ```
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  [smoke_gate] main is GREEN — removing red sentinel and closing regression ticket(s)
  [smoke_gate] main is RED — halting factory run cleanly (exit 0, no per-ticket failure)
  PASS
  exit=0
  ```

  (Second GREEN/RED pair = new case 7 exercising `_smoke_on_green`/`_smoke_on_red` via the
  non-exec path. The `WARNING: [hooks] …` lines are redirected into the temp stderr files /
  `/dev/null` by the tests, so they do not appear here.)

- [ ] **Step 2.5: Confirm the diff is scoped.**

  ```bash
  git diff --stat
  ```

  Expected: exactly two files — `scripts/hooks.sh` and `tests/test_hooks.sh`.

- [ ] **Step 2.6: Commit.**

  ```bash
  git add scripts/hooks.sh tests/test_hooks.sh
  git commit -m "fix(hooks): run non-executable .factory/hooks/* via bash instead of the built-in default (#438)

  run_hook dispatched on [ -x \"\$hook\" ], so a hook committed as mode 100644
  (the Windows default) was treated as missing: smoke-gate ran the MarketHawk
  default (false main-is-red) and validate silently no-op'd. The default now
  fires only when the file is absent ([ -e ]); a present non-exec hook runs as
  bash \"\$hook\" with a loud stderr warning and the same env contract, gate rc,
  and _smoke_on_green/_smoke_on_red routing as the executable path."
  ```

---

## Task 3: Full CI-parity verification

**Files:** none (verification only)

- [ ] **Step 3.1: Run the sibling smoke-gate shell suite** (sources `smoke_gate.sh`, which
  `hooks.sh` also sources — guards against collateral breakage):

  ```bash
  bash tests/test_smoke_gate.sh 2>&1 | tail -1
  ```

  Expected: a `Results: N passed, 0 failed` line (27 passed at time of writing; the pass
  count may drift with unrelated changes on main — `0 failed` is what matters).

- [ ] **Step 3.2: Run the side-effect shim suite** (its stubs follow `test_hooks.sh`'s
  pattern):

  ```bash
  bash tests/test_side_effect_shims.sh; echo "exit=$?"
  ```

  Expected: `exit=0`.

- [ ] **Step 3.3: Run the Python suite exactly as CI does.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q 2>&1 | tail -3
  ```

  Expected: all tests pass, 0 failed (no Python test pins the `[ -x "$hook" ]` line;
  `tests/test_verify_skill_files.py` only references `tests/test_hooks.sh` by name).

- [ ] **Step 3.4: Workflow DAG checks** (CI `dag-check` job — unaffected, run for parity):

  ```bash
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && \
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml; echo "exit=$?"
  ```

  Expected: `exit=0`.

- [ ] **Step 3.5: If any step above fails**, fix within `scripts/hooks.sh` /
  `tests/test_hooks.sh` only, re-run Task 2 Step 2.4 and all of Task 3, then commit the fix
  as a new commit (no amend) with a `fix(hooks): <what> (#438)` subject. Do not modify any other file.

---

## Spec Requirement Traceability

| Spec requirement | Covered by |
|---|---|
| R1 — non-exec runs via `bash "$hook" "$@"`, same env contract & routing, never default | Task 2 Steps 2.2-2.3; tests 6, 6b, 7 |
| R2 — uniform across all four names, independent of `--gate` | Task 2 (single `invoke` path, no name check around it); test 6 (no `--gate`), 6b/7 (`--gate`) |
| R3 — loud, path-specific stderr warning with remediation, every invocation | Task 2 Step 2.2; test 6 + 7 stderr greps |
| R4 — `bash` only, no shebang parsing / CRLF stripping | Task 2 Step 2.2 (`invoke=(bash "$hook")`) |
| R5 — missing-entirely keeps default | Task 2 (`else` branch unchanged, now gated on `-e`); existing test 1 |
| R6 — non-exec `validate` test | Task 1 case 6 |
| R7 — non-exec `smoke-gate` green + red test | Task 1 case 7 |
| R8 — no docs/templates/`.gitattributes` change | File Structure (two files only); Task 2 Step 2.5 |

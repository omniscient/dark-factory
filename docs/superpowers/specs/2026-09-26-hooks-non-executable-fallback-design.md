# fix(hooks): non-executable `.factory/hooks/*` must still run, not silently fall back to the built-in default

**Issue:** #438

---

## Overview / Problem Statement

`run_hook()` (`scripts/hooks.sh:17-46`) resolves a target hook at
`${CLONE_DIR}/.factory/hooks/<name>` and dispatches on a single `[ -x "$hook" ]` check
(line 23). A hook file that *exists* but was committed without the executable bit is
indistinguishable, in that check, from a hook that doesn't exist at all — `run_hook`
falls through to the `else` branch (lines 39-43) and runs the built-in default instead:
`_default_smoke_gate` for `smoke-gate`, a silent no-op (`rc=0`) for every other name.

This is the default outcome for an operator who authors hooks on Windows: `git add`
records new files as mode `100644` unless `git update-index --chmod=+x` is run
explicitly. For `smoke-gate` specifically, the built-in default is MarketHawk's own
`tsc` + backend-import check (`smoke_gate.sh`), which fails outright on any other
target's layout — the operator's own (never-run) check is replaced by a check that is
guaranteed to fail, latching a false `main-is-red` sentinel and halting all dispatch on
that instance.

**Already shipped, out of this spec's scope:** the issue's second bullet (onboarding
docs telling operators to `git update-index --chmod=+x .factory/hooks/*` and add
`.factory/hooks/* text eol=lf` to `.gitattributes`) landed in commit `d10e464`
(`docs/onboarding-new-target.md` §3c, `templates/new-target/.gitattributes`) before this
refine run, and explicitly cites this issue by number. That's prevention for newly
onboarded targets; it does nothing for a target that already has a mis-permissioned hook
committed on its default branch today, which is the scenario `run_hook` itself must
handle. This spec covers only the remaining code-level fix in `run_hook`.

## Requirements

1. When `.factory/hooks/<name>` exists but is not executable, `run_hook` must still run
   it — via `bash "$hook" "$@"` — with the identical env contract (`CLONE_DIR`,
   `ARTIFACTS_DIR`, `ISSUE_NUM`, `FACTORY_REPO_SLUG`) and identical downstream routing
   (for `smoke-gate`: the target hook's exit code still drives `_smoke_on_green` /
   `_smoke_on_red`; for every other name: `rc=$?` as today) as the existing executable
   path. It must never fall through to `_default_smoke_gate` or the no-op default while
   the file exists.
2. This applies uniformly to all four hook names (`smoke-gate`, `validate`,
   `preview-up`, `preview-down`) and independently of whether the caller passed
   `--gate` — one code path, not a smoke-gate-specific carve-out. (`validate` is in fact
   already gated — `entrypoint.sh:853` calls `run_hook --gate validate` — so a
   smoke-gate-only fix would have left an equally-real bug on `validate`.)
   **Spec-review amendment:** a `run_hook`-only change does *not* actually reach the
   `validate` defect — `entrypoint.sh:851` guards that call with its own
   `[ -x "$CLONE_DIR/.factory/hooks/validate" ]` test and otherwise runs MarketHawk's
   inline `npx tsc --noEmit` in `frontend/` (`entrypoint.sh:859-865`), escalating the
   ticket to Blocked through `_conflict_escalate`. See Requirement 9.
3. `run_hook` must emit a loud warning to stderr naming the exact non-executable path,
   and the line must carry a **stable, machine-greppable token** — `hook-not-executable`
   — so operator tooling and the tests match on the token rather than on prose:
   ```
   WARNING: [hooks] hook-not-executable path=<CLONE_DIR>/.factory/hooks/<name> crlf=<yes|no> — running it with bash (the hook's own shebang is ignored); fix with: git update-index --chmod=+x .factory/hooks/<name>
   ```
   The line must state that the hook's shebang is being ignored (a non-executable
   `#!/usr/bin/env python3` hook is now fed to bash and will fail as a syntax error where
   previously the built-in default ran), and it must report whether the file has CRLF line
   endings — detection only (`grep -q $'\r' "$hook"`), never normalisation, see
   Requirement 4 and "Spec-review corrections". Measured in the factory image, a CRLF hook
   fails under the container's GNU bash with errors that never mention line endings, so
   this token is the operator's only diagnosis.
   The warning fires every time a non-executable hook is invoked (no dedup/once-only
   state) — `run_hook` has no persistent state today and adding any would be a
   disproportionate change for a log line. It must **not** fire on the executable path:
   no per-run log noise (asserted by a test, Requirement 12).
4. The fallback interpreter is `bash` only. No shebang parsing, no interpreter
   detection, no CRLF stripping/normalization. Every shipped hook template
   (`templates/new-target/.factory/hooks/{smoke-gate,validate}`) already declares
   `#!/usr/bin/env bash`; a hook that isn't actually bash-compatible fails visibly
   through its own exit code when run via `bash "$hook"`, which is an acceptable,
   honest failure mode (the operator's script fails on its own merits, not via a
   factory-injected default that was never theirs).
5. The fallback-to-default branch is gated on the hook being a **non-empty regular
   file** — `[ -f "$hook" ] && [ -s "$hook" ]` — not on `[ -e "$hook" ]`. Everything else
   (absent, a directory, or a zero-byte placeholder left by `touch
   .factory/hooks/smoke-gate` during onboarding) keeps today's behavior exactly: fall
   through to the built-in default. `-e` alone would (a) feed a directory to `bash`
   ("is a directory", non-zero → a false `main-is-red` on the smoke-gate name) and,
   worse, (b) turn an empty placeholder into a **silently green** gate, because `bash` on
   an empty file exits 0 and `_smoke_on_green` (`smoke_gate.sh:127-150`) then clears the
   sentinel — the exact "a missing hook must never become a silent pass" rule this ticket
   exists to protect. This spec changes only the present-but-not-executable branch.
   **Boundary with #436:** #436 owns *what* the absent/empty branch should do (make the
   built-in default target-neutral instead of MarketHawk-specific). This spec must not
   change that branch's behavior; the two specs meet at the `-f`/`-s` test and nowhere
   else, and neither contradicts the other.
6. Add a `tests/test_hooks.sh` case for a present-but-non-executable hook that asserts,
   for a non-smoke-gate name (`validate`, `chmod -x`):
   - the hook body actually executes (its side effect — e.g. a written marker file —
     is observed),
   - the `hook-not-executable` token and the hook path appear on the stderr warning
     (match the token, not the prose — Requirement 3),
   - the built-in default does not run (for `validate` this is implicit — no-op vs.
     hook-body-ran is what's being distinguished; for symmetry the smoke-gate path is
     covered by requirement 7 below).
7. Add a second `tests/test_hooks.sh` case for a present-but-non-executable `smoke-gate`
   hook, mirroring the existing executable-path green/red cases (lines 40-52): a
   non-executable green hook clears the `main-is-red` sentinel via `_smoke_on_green`; a
   non-executable red hook routes through `_smoke_on_red` (sentinel written, clean halt,
   exit 0) — proving the non-executable path reuses the exact same
   `_smoke_on_green`/`_smoke_on_red` routing as the executable path, not a parallel
   implementation of it.
8. Do not re-do the already-shipped prevention work (`templates/new-target/**`,
   `templates/new-target/.gitattributes`, or the chmod instructions themselves — see
   Overview). Three shipped statements do become **false** with this change and must be
   corrected in the same PR:
   - `docs/onboarding-new-target.md:115-117` — "The factory runs a hook only if
     `[ -x hook ]`. **A non-executable hook is silently ignored, and the MarketHawk
     default runs instead** (tracked in #438)". Keep the chmod step (still the right thing
     to do) and restate the behavior as warn-loudly-and-run-via-`bash`.
   - `scripts/hooks.sh:4-9` header contract — "Falls back to built-in defaults when no
     target hook is present" must spell out the present-but-non-executable case and the
     non-empty-regular-file test.
   - `README.md:224` ("Place executable scripts at `.factory/hooks/<name>`") — note that a
     non-executable hook still runs, loudly, via `bash`.
9. `entrypoint.sh:851` must stop pre-empting `run_hook`: replace its own
   `[ -x "$CLONE_DIR/.factory/hooks/validate" ]` test with the same non-empty
   regular-file test as Requirement 5, so a present-but-non-executable `validate` hook
   reaches `run_hook` (which warns and runs it) instead of falling into MarketHawk's
   inline `npx tsc --noEmit` in `frontend/` (`entrypoint.sh:859-865`) and escalating the
   ticket to Blocked via `_conflict_escalate`. Without this, the `validate` half of the
   defect Requirement 2 correctly identifies is not fixed at all. The absent-hook path
   (inline tsc fallback) stays exactly as it is — that is #436/#222 territory.
10. The warning must survive the run container. `scheduler.sh:377-378` dispatches with
    `docker compose run -d --rm`, so nothing a run prints reaches the scheduler log, and
    the container's own log is destroyed on exit (`docs/onboarding-new-target.md:218`).
    `run_hook` must therefore also append one durable line —
    `<UTC timestamp> hook-not-executable <hook path> issue=<ISSUE_NUM> crlf=<yes|no>` —
    to `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/hook-warnings.log`, the volume the
    scheduler and every run container share (`run-compose.yml:55`; `smoke_gate.sh:11`
    binds the same directory). Guard the append with `[ -d "<dir>" ]` and never `mkdir`
    it: `.github/workflows/ci.yml:27-28` asserts the suite leaves `/var/lib/dark-factory`
    empty. This mirrors the codebase's own "a durable trace beyond stderr, so an operator
    can spot a real bug in the issue history rather than only in run logs" precedent
    (`workflows/archon-dark-factory.yaml:210-222`).
11. When a non-executable `smoke-gate` hook drives the gate red, the operator-facing
    regression ticket must say so: the `hook-not-executable` token and the hook path must
    appear in the text `_smoke_on_red` posts (`smoke_gate.sh:97-119`) — e.g. a variable
    `run_hook` sets before invoking the hook, which `_smoke_on_red` appends when
    non-empty, mirroring the `TEARDOWN_NOTE` pattern at
    `workflows/archon-dark-factory.yaml:214-222`. Otherwise the operator sees only
    "main is red: tsc/python import failure" with no hint that what actually ran was their
    own never-chmod'd hook. This adds text to an existing comment only: no change to when
    the gate fires, what it returns, or the sentinel/ticket state machine.
12. Test placement and negative cases (extends Requirements 6-7):
    - The new cases go **after** the existing assertions at `tests/test_hooks.sh:53-56`:
      that block asserts *exactly one* stubbed `tracker create` for the whole file, and a
      second red smoke-gate case makes it two. Each new case must reset the state it
      depends on (sentinel file, hook mode) explicitly — `tests/test_hooks.sh:48`
      currently relies on the mode set at `:44` surviving a `printf` overwrite.
    - **Negative case A (a missing hook must not become a pass):** with no `smoke-gate`
      hook file, and again with a zero-byte one, the built-in default still runs (assert
      via a stubbed `_smoke_check_main` counter) and **no** `hook-not-executable` token is
      emitted.
    - **Negative case B (no log noise):** the existing executable-hook cases emit no
      `hook-not-executable` token.
    - **Exit-code propagation:** a non-executable `validate` hook exiting 3 still makes
      `run_hook --gate validate` return 3 (same assertion shape as
      `tests/test_hooks.sh:37-39`).
13. Verification, and where each check is actually enforced:
    - `bash tests/test_hooks.sh` — CI runs it on `ubuntu-latest`
      (`.github/workflows/ci.yml:16`). It is **not** part of `python -m pytest tests/ -v`,
      and on a Windows host MSYS/Git-Bash silently tolerates the `\r` this ticket is
      about, so only the Linux CI run (or the same command inside
      `ghcr.io/omniscient/dark-factory:latest`) proves anything.
    - `python -m pytest tests/ -v` — the Requirement 9 check belongs here as a static
      assertion over `entrypoint.sh`'s text in a `tests/*.py` file (the `-x`
      `.factory/hooks/validate` pre-check is gone), so it is verified off-image too.
    - `bash tests/test_smoke_gate.sh` — unchanged built-in-default behavior.
    - No workflow change is involved, so the `check_workflow_*.py` DAG gates are
      unaffected.

## Brainstorming Q&A

> **Q:** When `run_hook` finds a hook file at `.factory/hooks/<name>` that exists but
> lacks the executable bit, what should happen, concretely? The issue's own proposal
> offers two alternatives without picking one: (a) log a loud warning and still execute
> it via `bash "$hook" ...` (best-effort, same env contract as today), or (b) treat it as
> a failure and fail the gate. Which should `run_hook` implement, and should the answer
> differ between the gated `smoke-gate` hook (which has a built-in MarketHawk default to
> fall back to) versus the ungated hooks `validate`, `preview-up`, `preview-down`?
>
> **A:** Pick option (a) and apply it the same way to all four hook names. Print a loud
> warning to stderr, then run the hook as `bash "$hook" "$@"` with the same env contract
> and the same smoke-gate green/red routing as the executable path — never fall through
> to `_default_smoke_gate` or the no-op. Option (b) is wrong: for `smoke-gate`, failing
> the gate routes through `_smoke_on_red` and reproduces the exact false "main is red"
> halt the issue complains about; and the file being present makes operator intent
> clear — the bug is that it gets ignored, not that it exists. This still satisfies the
> fail-closed spirit of `VERIFIER-CONTRACT.md` (#301): the operator's own check actually
> runs, the warning means nothing is silent, and a genuinely broken script (e.g. CRLF
> breaking bash) fails visibly through its own exit code. Correction to the question's
> premise: `validate` is gated too (`entrypoint.sh:853`, `run_hook --gate validate`) —
> `preview-up`/`preview-down` aren't currently invoked via `run_hook` anywhere in the
> codebase (grep across `entrypoint.sh`, `workflows/*.yaml` found no call site), so
> they're dormant name-only entries for now, but the fix applies to `run_hook` uniformly
> regardless. Keep the fallback interpreter to `bash` only — no shebang parsing, no CRLF
> stripping. Add a `tests/test_hooks.sh` case for a present-but-non-executable hook
> asserting the hook body runs, the warning appears on stderr, and the default does not
> run.

## Architecture / Approach

**Current (`scripts/hooks.sh:17-46`):**

```bash
run_hook() {
  local gate=0
  [ "$1" = "--gate" ] && { gate=1; shift; }
  local name="$1"; shift || true
  local hook="${CLONE_DIR}/.factory/hooks/${name}"
  local rc=0
  if [ -x "$hook" ]; then
    if [ "$name" = "smoke-gate" ]; then
      if CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
           FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@"; then
        _smoke_on_green
        rc=0
      else
        _smoke_on_red
      fi
    else
      CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
        FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "$hook" "$@" || rc=$?
    fi
  else
    case "$name" in
      smoke-gate) _default_smoke_gate "$@" || rc=$? ;;
      *) rc=0 ;;
    esac
  fi
  if [ "$gate" = "1" ]; then return "$rc"; else return 0; fi
}
```

**Approach:** factor the "how do we invoke the resolved hook" decision out of the
executable-bit check, so the same invocation logic runs for both an executable hook and
a non-executable-but-present one — only the *interpreter prefix* differs (`"$hook"`
directly vs. `bash "$hook"`). Concretely: keep `[ -x "$hook" ]` to decide direct-exec vs.
`bash`-exec, but gate the *fallback-to-default* branch on a non-empty-regular-file test
(`[ -f "$hook" ] && [ -s "$hook" ]`, Requirement 5) instead of executability — the default
only fires when the file is absent, a directory, or a zero-byte placeholder:

```bash
run_hook() {
  local gate=0
  [ "$1" = "--gate" ] && { gate=1; shift; }
  local name="$1"; shift || true
  local hook="${CLONE_DIR}/.factory/hooks/${name}"
  local rc=0
  if [ -f "$hook" ] && [ -s "$hook" ]; then
    local -a invoke
    if [ -x "$hook" ]; then
      invoke=("$hook")
    else
      local crlf="no"; grep -q $'\r' "$hook" && crlf="yes"
      echo "WARNING: [hooks] hook-not-executable path=${hook} crlf=${crlf} — running it with bash (the hook's own shebang is ignored); fix with: git update-index --chmod=+x .factory/hooks/${name}" >&2
      # Durable trace beyond stderr (Req 10): dispatch is `run -d --rm`, so the run's
      # stderr never reaches the scheduler log and dies with the container. Guarded,
      # never mkdir — CI asserts /var/lib/dark-factory stays empty.
      local state_dir="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
      [ -d "$state_dir" ] && printf '%s hook-not-executable %s issue=%s crlf=%s\n' \
        "$(date -u +%FT%TZ)" "$hook" "${ISSUE_NUM:-}" "$crlf" \
        >> "${state_dir}/hook-warnings.log"
      # Consumed by _smoke_on_red's ticket text (Req 11).
      HOOK_NOT_EXECUTABLE_NOTE="hook-not-executable ${hook} (crlf=${crlf})"
      invoke=(bash "$hook")
    fi
    if [ "$name" = "smoke-gate" ]; then
      if CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
           FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@"; then
        _smoke_on_green
        rc=0
      else
        _smoke_on_red
      fi
    else
      CLONE_DIR="$CLONE_DIR" ARTIFACTS_DIR="${ARTIFACTS_DIR:-}" ISSUE_NUM="${ISSUE_NUM:-}" \
        FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-}" "${invoke[@]}" "$@" || rc=$?
    fi
  else
    case "$name" in
      smoke-gate) _default_smoke_gate "$@" || rc=$? ;;
      *) rc=0 ;;
    esac
  fi
  if [ "$gate" = "1" ]; then return "$rc"; else return 0; fi
}
```

This keeps every existing branch's shape (smoke-gate dual routing, gate/no-gate return)
untouched — the only new surface is the `invoke=(...)` array and the warning line — so
the diff stays small and the existing five `tests/test_hooks.sh` cases keep passing
unmodified. `local -a invoke` (bash arrays) is already used elsewhere in this codebase's
shell scripts, so it introduces no new bash-version dependency.

Two new `tests/test_hooks.sh` cases cover Requirements 6-7: a `chmod -x`'d `validate`
hook whose body still runs and whose non-exec state is warned about on stderr, and a
`chmod -x`'d `smoke-gate` hook exercised through both the green and red paths to confirm
`_smoke_on_green`/`_smoke_on_red` still fire identically to the executable-path cases
immediately above them in the same file.

## Alternatives Considered

1. **Chosen: run via `bash "$hook"` with a stderr warning, uniformly across all four hook
   names and both gate modes.** Matches the issue's stated preference (proposal bullet
   (a)), keeps `run_hook`'s existing default-fallback semantics reserved for "hook
   genuinely absent," and closes the exact failure mode in the issue (false
   `main-is-red`) rather than reproducing it. See Brainstorming Q&A.
2. **Fail the gate on a non-executable hook (proposal bullet (b)).** Rejected — for
   `smoke-gate` this routes through `_smoke_on_red` and produces the identical false
   "main is red" halt the issue is reporting as a bug; for `validate` it would block
   deconflict merges over a chmod bit rather than running the operator's actual check.
3. **Detect and fix the executable bit in-process (`chmod +x "$hook"` before running
   it directly).** Rejected: mutates a file inside a fresh clone silently, conflates
   "run the hook" with "repair the clone," and still doesn't handle the CRLF/bad-shebang
   half of the issue's root cause (a `chmod`'d CRLF script still fails on `#!/usr/bin/env
   bash\r`) — `bash "$hook"` sidesteps the shebang entirely, which is more robust for the
   common case (Windows mode-bit loss) without pretending to fix line endings. It is **not**
   more robust for CRLF: the container's GNU bash rejects `\r`-suffixed tokens just as
   surely as the kernel rejects a `\r`-suffixed shebang (see "Spec-review corrections"),
   which is why Requirement 3 makes the warning *report* CRLF instead.
4. **Detect CRLF and normalize/strip `\r` before running.** Rejected as disproportionate
   scope per Brainstorming Q&A — the docs-side fix (`.gitattributes` `eol=lf`, already
   shipped) is the intended prevention for CRLF, and a script broken by stray `\r`s fails
   through its own exit code. Corrected claim: `bash "$hook"` does **not** tolerate CRLF —
   measured in the factory image, `set -uo pipefail\r` fails with
   `set: pipefail: invalid option name` and `cd "${CLONE_DIR}"\r` with
   `No such file or directory`, and because the shipped template uses `set -uo pipefail`
   with no `-e` a CRLF hook can even run past both and exit 0. Normalisation stays out of
   scope, but the failure must be *diagnosable*, so Requirement 3 requires CRLF detection
   in the warning line (one `grep -q`, zero behavior change).

## Spec-review corrections (2026-09-26)

- **CRLF, measured.** Running a `\r`-terminated bash script with
  `bash <path>` inside `ghcr.io/omniscient/dark-factory:latest` produces
  `set: pipefail: invalid option name` and `cd: $'/home/factory\r': No such file or
  directory`, and the script still exited 0 because it used `set -uo pipefail` (no `-e`) —
  the same option line the shipped templates use
  (`templates/new-target/.factory/hooks/smoke-gate:7-8`). The CRLF tolerance one sees on a
  Windows host comes from MSYS bash stripping `\r`; the container's GNU bash does not.
  Consequence for this ticket: the exec-bit fix alone does not rescue a hook that lost both
  the mode bit and its line endings, which is the common Windows case — hence the
  `crlf=<yes|no>` field in Requirement 3's warning.
- **`VERIFIER-CONTRACT.md` (#301) does the opposite for verifiers, on purpose.**
  `refinement-skills/VERIFIER-CONTRACT.md:140` lists "a non-executable path" as a
  fail-closed condition and `scripts/factory_core/verifier.py:49-50` raises
  `VerifierError` on exactly that. The Brainstorming Q&A's appeal to that contract's
  "fail-closed spirit" should be read narrowly: a target verifier has **no** built-in
  default to fall into, so failing closed is its only safe move, whereas `run_hook` does
  have one whose failure *is* the false `main-is-red` this ticket reports. The divergence is
  deliberate; do not "harmonise" the two seams without a ticket.

## Open Questions (Non-blocking)

- `preview-up`/`preview-down` have no current call site anywhere in the codebase (only
  named in `hooks.sh`'s header comment and the README's hook table) — this spec doesn't
  investigate or change that; it's pre-existing and orthogonal to this bug.

## Assumptions

- The onboarding-docs bullet of the issue (chmod + `.gitattributes` guidance) is fully
  covered by the already-merged `d10e464` and needs no further changes — verified by
  reading `docs/onboarding-new-target.md` §3c and `templates/new-target/.gitattributes`
  directly, not by relying on the commit message alone.
- `bash` is always available in the factory run container for the `bash "$hook"`
  fallback — verified by `scripts/hooks.sh`'s own shebang (`#!/usr/bin/env bash`) and by
  every other factory-side script already assuming a bash runtime.

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
3. `run_hook` must emit a loud warning to stderr identifying the exact non-executable
   path and the fix, e.g.:
   ```
   WARNING: [hooks] .factory/hooks/<name> exists but is not executable (mode 100644?) — running via bash; fix with: git update-index --chmod=+x .factory/hooks/<name>
   ```
   The warning fires every time a non-executable hook is invoked (no dedup/once-only
   state) — `run_hook` has no persistent state today and adding any would be a
   disproportionate change for a log line.
4. The fallback interpreter is `bash` only. No shebang parsing, no interpreter
   detection, no CRLF stripping/normalization. Every shipped hook template
   (`templates/new-target/.factory/hooks/{smoke-gate,validate}`) already declares
   `#!/usr/bin/env bash`; a hook that isn't actually bash-compatible fails visibly
   through its own exit code when run via `bash "$hook"`, which is an acceptable,
   honest failure mode (the operator's script fails on its own merits, not via a
   factory-injected default that was never theirs).
5. A file that does not exist at all (`[ ! -e "$hook" ]`) keeps today's behavior exactly
   — fall through to the built-in default. This spec changes only the
   exists-but-not-executable branch; it does not touch the missing-entirely branch.
6. Add a `tests/test_hooks.sh` case for a present-but-non-executable hook that asserts,
   for a non-smoke-gate name (`validate`, `chmod -x`):
   - the hook body actually executes (its side effect — e.g. a written marker file —
     is observed),
   - a warning naming the hook path appears on stderr,
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
8. No change to `docs/onboarding-new-target.md`, `.gitattributes`, or any template —
   that half of the issue is already shipped (see Overview).

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
`bash`-exec, but gate the *fallback-to-default* branch on `[ -e "$hook" ]` instead
(existence, not executability) — the default only fires when the file is truly absent:

```bash
run_hook() {
  local gate=0
  [ "$1" = "--gate" ] && { gate=1; shift; }
  local name="$1"; shift || true
  local hook="${CLONE_DIR}/.factory/hooks/${name}"
  local rc=0
  if [ -e "$hook" ]; then
    local -a invoke
    if [ -x "$hook" ]; then
      invoke=("$hook")
    else
      echo "WARNING: [hooks] ${hook} exists but is not executable (mode 100644?) — running via bash; fix with: git update-index --chmod=+x ${hook}" >&2
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
   bash\r`) — `bash "$hook"` sidesteps the shebang entirely and is strictly more robust
   for the common case (Windows mode-bit loss) without pretending to fix line endings.
4. **Detect CRLF and normalize/strip `\r` before running.** Rejected as disproportionate
   scope per Brainstorming Q&A — the docs-side fix (`.gitattributes` `eol=lf`, already
   shipped) is the intended prevention for CRLF; `bash "$hook"` already tolerates most
   CRLF scripts far better than direct shebang execution would, and a script that's
   still broken by stray `\r`s fails visibly through its own exit code, which is
   sufficient defense-in-depth for this ticket.

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

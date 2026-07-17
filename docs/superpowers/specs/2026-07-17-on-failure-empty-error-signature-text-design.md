# fix(circuit-break): `on_failure` writes error signatures with empty text, defeating #33's environmental carve-out

**Issue:** omniscient/dark-factory#303
**Related, explicitly NOT this ticket's scope:** #33 (shipped the early-break circuit breaker +
environmental carve-out this bug defeats), #35 (session-window pause — supplies `RATE_LIMIT_RE`,
reused by `error_signature.py`), #279 (delivery_failure — the one `on_failure` classifier that
still works without text, and the reason `_write_error_signature`'s artifact-allowlist exists),
#292 (session-window failure-comment noise)

---

## Overview / Problem Statement

`entrypoint.sh`'s `on_failure()` (the `ERR` trap, registered at line 650) calls
`_write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""` at two call sites — line
609 (refine/plan/deconflict branch) and line 625 (implement/else branch) — always passing an empty
string as the third argument, `text_file`. That flows to
`scripts/factory_core/cli.py`'s `error-signature-write --text-file ""`, which leaves
`error_signature.classify()`'s `text` parameter as `""`. Every text-based classifier
(`_RATE_LIMIT_RE`, `_PREVIEW_INFRA_RE`, `_OOS_FILES_RE`, `_BUILD_FAILURE_RE`, `_TEST_FAILURE_RE`)
calls `.search(text)` against that empty string and can never match — only the
timing/commit/artifact-based `environmental:delivery_failure` branch (needs no text) still works
at these two call sites. Any `on_failure`-classified failure that isn't a fast, zero-commit,
zero-artifact death (i.e. `elapsed_seconds >= delivery_failure_max_seconds`, or a commit landed,
or the worktree is dirty, or a phase-deliverable artifact exists) falls through to
`substantive:unknown:{exit_code}`, even when the captured output plainly says e.g. "Claude session
limit reached — resets 9:20pm (UTC)" (which `session_window.RATE_LIMIT_RE` — reused by
`error_signature.py` as `_RATE_LIMIT_RE` — matches on `"session limit"`).

This was observed live on the first `#33` early-break engagement (2026-07-17 19:00Z): a genuine
session-window death logged `signature=substantive:unknown:1` instead of
`environmental:rate_limit`. Two consecutive such deaths on one ticket would collapse to the same
`substantive:unknown` signature twice and wrongly trip #33's early circuit-break to
`needs-discussion` — the exact false positive the environmental carve-out exists to prevent. In
the observed run this was harmless (the next retry succeeded after a genuine window reset), but
the defect is real and reachable: #35 pauses dispatch during exhaustion, but a retry dispatched
right after a reset that hasn't actually freed the window (or a 7-day-cap case) can die again,
reproducing the identical signature and tripping the breaker.

## Requirements

1. Both `_write_error_signature` call sites inside `on_failure()` (lines 609, 625) must pass the
   real captured workflow output as `text_file` whenever it is available, so the environmental
   (`rate_limit`, `preview_infra`) and substantive (`oos_files`, `build_failure`, `test_failure`)
   text classifiers in `error_signature.classify()` get a real chance to match instead of
   unconditionally falling through to `substantive:unknown`.
2. When no captured output exists yet — a genuine early/setup-phase crash before the main
   `archon workflow run` retry loop (`entrypoint.sh:895-949`) ever executes — the existing
   conservative behavior (empty text, decided by the timing/commit/artifact signals alone) must be
   preserved unchanged. This ticket does not change classification for that case.
3. The fix must not introduce a new failure mode under `entrypoint.sh`'s `set -euo pipefail`
   (line 2): referencing the capture variable when it is legitimately unset (the early-crash case
   in Requirement 2) must not itself raise an unset-variable error inside the trap handler.
4. `run_post_mortem()`'s parallel `""` argument at line 627 (implement/else branch only) must also
   be fixed to pass `"${TMP_OUT:-}"`, in scope for this ticket — see Brainstorming Q&A #1. This is
   the identical root-cause pattern (empty text where the same live global is available) at an
   adjacent call site in the same function; fixing the signature bug while leaving this one
   unfixed one line below would ship a half-fix and generate immediate spillover churn. Named here
   explicitly (not folded in silently) so the conformance gate's plan-fidelity check recognizes it
   as in-scope.
5. A regression test must feed the literal observed string
   (`"Claude session limit reached — resets 9:20pm (UTC)"`) through the fixed path and assert the
   resulting signature is `environmental:rate_limit`, per the issue's explicit ask. Two layers are
   required — see Brainstorming Q&A #2:
   - Keep/extend the Python-level CLI fixture test (`tests/test_factory_core_error_signature.py`)
     for the exact string, satisfying the issue's literal ask.
   - Add a new shell-level regression in `tests/test_entrypoint_error_signature.sh` that populates
     `TMP_OUT` with the literal string and drives it through the entrypoint's own
     `on_failure`/`_write_error_signature` wiring (not just `classify()`/the CLI directly), then
     asserts the resulting signature file is `environmental:rate_limit`. This is the layer that
     actually proves the `entrypoint.sh` fix, since `classify()` and the CLI were never broken —
     a Python-only test would stay green even if `entrypoint.sh` regressed to `""` again.

## Brainstorming Q&A

> **Q:** Should the fix simply change `on_failure()`'s two call sites to pass `"${TMP_OUT:-}"`
> instead of the literal `""` (reusing the existing script-global `$TMP_OUT` variable the main
> retry loop already populates with real captured workflow output), or does the codebase's
> architecture call for a more explicit/robust capture mechanism (e.g. persisting the last
> captured-output path to a state file) so the signal is available in strictly more scenarios? If
> `$TMP_OUT`-reuse is right, are there edge cases (e.g. `set -u` strictness) that make a bare
> `$TMP_OUT` reference unsafe?
>
> **A:** Use the `$TMP_OUT`-reuse approach — a state-file mechanism would be over-engineering for a
> bug scoped as "empty text defeats the classifiers." The main retry loop already treats `TMP_OUT`
> as a script-scope global (set at `entrypoint.sh:897`, non-`local`, and it is the exact variable
> the loop itself passes to `_write_error_signature` at line 943), so `on_failure` reading it back
> is consistent with how the file already threads captured output; `cli.py`'s
> `text_path.exists()` guard makes a stale/deleted path safe, so no extra persistence layer buys
> correctness in enough additional scenarios to justify the surface area. The `${TMP_OUT:-}` guard
> (not bare `$TMP_OUT`) is mandatory, not optional: `on_failure` legitimately fires for
> early/setup-phase crashes before `TMP_OUT` is ever assigned (line 897), and a bare `$TMP_OUT`
> reference would trip `set -u`'s unset-variable error inside the trap handler itself — corrupting
> the very failure path this ticket fixes. `${TMP_OUT:-}` correctly yields real text when `TMP_OUT`
> is populated-and-not-yet-deleted at trap time, and empty (the existing, correct fallback)
> otherwise.

> **Q:** `on_failure()` has a second, parallel empty-text bug at line 627:
> `run_post_mortem "$EXIT_CODE" ""` (implement/else branch only — the refine/plan/deconflict branch
> doesn't call `run_post_mortem` at all, since it early-returns for those intents). This produces a
> less useful "no transcript available" post-mortem comment instead of a real one. The issue's body
> and Fix section only discuss `_write_error_signature`'s bug. Should this ticket also fix
> `run_post_mortem`'s call site, or is that out of scope? Separately: is a pure Python-level test in
> `tests/test_factory_core_error_signature.py` sufficient for the issue's requested regression
> test, or does the fix also need a shell-level test exercising `on_failure()` end-to-end?
>
> **A:** Yes, include the `run_post_mortem` fix, but name it explicitly as its own requirement
> rather than folding it in silently — it is the identical root-cause pattern (empty text where the
> live global `$TMP_OUT` is available) at the same call site's immediate neighbor, a one-token
> change (`""` → `"${TMP_OUT:-}"`), and it directly restores the observability value of the
> on_failure post-mortem comment; shipping the signature fix while leaving the same bug one line
> below is exactly the half-fix that generates immediate spillover churn. Scope discipline here
> means "explicitly listed in the plan," not "minimal to a fault" — because the conformance gate
> excises anything the plan doesn't name, the spec must call this out by line/function so it
> survives the gate. This stays clear of the repo's hard limits (touches neither `gate_*`/breaker/
> budgets nor a human-only surface).
>
> On the test question: no, a pure Python CLI fixture test is not sufficient by itself.
> `error_signature.classify()` and the `error-signature-write` CLI already work correctly today
> (the issue itself confirms the regex matches the string) — a Python-only fixture test guards a
> layer that was never broken and would stay green even if `entrypoint.sh` regressed to `""` again.
> The actual bug lives in the `entrypoint.sh` wiring, and the repo already has the right harness for
> it: `tests/test_entrypoint_error_signature.sh` sources the script via
> `ENTRYPOINT_SOURCE_ONLY=1` and drives `_write_error_signature`/`_failure_phase_for_intent`
> directly. The spec requires a new shell-level regression there that writes the literal
> `"Claude session limit reached — resets 9:20pm (UTC)"` to a temp file, threads it through as
> `TMP_OUT`, and asserts the resulting signature file classifies as `environmental:rate_limit`.
> Keep the Python fixture test too (the issue explicitly asks for it), but the shell test is the one
> that actually proves the fix.

## Architecture / Approach

**Current (`entrypoint.sh:604-627`, both branches inside `on_failure()`):**

```bash
if [ "$INTENT" = "refine" ] || [ "$INTENT" = "plan" ] || [ "$INTENT" = "deconflict" ]; then
  _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
  ...
else
  _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
  ...
  run_post_mortem "$EXIT_CODE" "" || true
  ...
fi
```

**Fixed:**

```bash
if [ "$INTENT" = "refine" ] || [ "$INTENT" = "plan" ] || [ "$INTENT" = "deconflict" ]; then
  _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
  ...
else
  _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
  ...
  run_post_mortem "$EXIT_CODE" "${TMP_OUT:-}" || true
  ...
fi
```

All three call sites (both `_write_error_signature` sites plus the implement-branch
`run_post_mortem` site) switch their text/transcript argument from the literal `""` to
`"${TMP_OUT:-}"`. No other line in `on_failure()` changes. `TMP_OUT` is the same script-global variable the main retry loop
(`entrypoint.sh:895-949`) already creates fresh each iteration (`TMP_OUT=$(mktemp)`, line 897),
`tee`s the real `archon workflow run` stdout/stderr into (line 898), and passes as the *correct*
`text_file` argument at its own already-working call site (line 943:
`_write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "$TMP_OUT"`). Because bash
`trap` handlers execute in the same shell as the code that was running when the trap fired, and
`TMP_OUT` is not declared `local` anywhere, `on_failure()` sees the same value the main loop was
last using at the moment of failure:

- If the `ERR` trap fires while `TMP_OUT` is live and populated (a command failing later in the
  script after `TMP_OUT` was created but before it is cleaned up), `${TMP_OUT:-}` resolves to that
  real, already-captured output path — exactly the missing signal this ticket restores.
- If the trap fires before `TMP_OUT` is ever assigned (an early/setup-phase crash, per
  Requirement 2), `${TMP_OUT:-}` safely expands to `""` under `set -u`, preserving today's
  conservative fallback with no new failure mode (Requirement 3).
- If `TMP_OUT` points at a file already removed by `rm -f` (loop cleanup), `cli.py`'s
  `_error_signature_write` already handles this: `text = text_path.read_text(...) if
  text_path.exists() else ""` — no additional guarding is needed on the `entrypoint.sh` side.

This is a three-call-site, single-variable-substitution change with no new state, no new files, and
no behavior change for the case this ticket does not touch (early crashes, which keep classifying
via `environmental:delivery_failure` or the timing signals alone, unchanged).

## Alternatives Considered

1. **Chosen: reuse the existing `$TMP_OUT` script-global via `"${TMP_OUT:-}"`.** Minimal,
   consistent with how the main loop already threads this exact variable to the same function at
   its correct call site (line 943), safe under `set -u`, and requires no new persistence
   mechanism.
2. **Persist the last-captured-output path to a small state file (e.g. under
   `$SCHEDULER_STATE_DIR` or `$ARTIFACTS_DIR`) so the signal survives across `TMP_OUT`'s
   create/delete lifecycle in strictly more scenarios.** Rejected per Brainstorming Q&A #1 — adds a
   new persistence surface, a new failure mode (stale/corrupt state file), and does not close any
   materially different gap than the `$TMP_OUT`-reuse approach for the scenario this bug report
   describes; over-engineered for a bug whose root cause is a single hardcoded empty string.
3. **Redirect all of `entrypoint.sh`'s output to a fixed, well-known file for the whole script's
   lifetime (not just inside the retry loop), so `on_failure()` always has *something* to read
   regardless of where in the script it fires.** Rejected — much larger blast radius (touches every
   code path in the script, not just the two `on_failure` call sites), changes existing
   stdout/stderr behavior globally, and is unnecessary since Requirement 2 explicitly preserves
   today's (correct) no-text fallback for genuine early crashes.

## Open Questions (Non-blocking)

- None. Both design questions raised during brainstorming (whether to include the `run_post_mortem`
  fix, and the required test shape) were resolved during Q&A and are captured as Requirements 4 and
  5 above.

## Assumptions

- `TMP_OUT` remains a plain (non-`local`) script-global variable for the lifetime of
  `entrypoint.sh` — verified by reading `entrypoint.sh:895-949`; no other change on this branch
  scopes it more tightly.
- The `ERR` trap's visibility into script-global variables (same-shell execution, not a subshell)
  was verified empirically with a standalone bash repro during refinement: an explicit
  `exit "$EXIT_CODE"` (as used at `entrypoint.sh:945`, the main loop's own failure path) does
  **not** trigger the `ERR` trap, confirming the main loop's own `_write_error_signature` call
  (line 943, already correct) and `on_failure()`'s two calls (lines 609, 625, being fixed here) are
  reached via genuinely different code paths, not double-invocations of the same failure.
- `cli.py`'s `_error_signature_write` already treats a missing/stale `--text-file` path as empty
  text (`text_path.exists() else ""`) — verified by reading `scripts/factory_core/cli.py:109-114`
  and confirmed by the existing test
  `test_cli_error_signature_write_missing_text_file_is_empty_text`; no defensive check is needed on
  the `entrypoint.sh` side before passing `"${TMP_OUT:-}"`.

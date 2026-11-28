# `post_marker_comment.sh`: parameterize the footer kind, reuse it for the conformance PASS-path shadow comment

**Issue:** #404

## Overview / Problem Statement

`#394` added a durable PASS-path advisory comment to `commands/dark-factory-conformance.md`
Phase 4 (marker `<!-- df-shadow-review -->`, posted when the conformance shadow-model checker
ran). Rather than calling the existing `scripts/post_marker_comment.sh` helper — which already
exists precisely to deduplicate the mktemp/printf/`tracker comment`/rm sequence (extracted from
`refine-push`/`plan-push-and-advance` duplication) — `#394` re-inlined that whole sequence.
Gate 3 advisory A4 on PR #398 flagged this; the stated reason was that `post_marker_comment.sh`
"only knows its existing marker kind."

Reading the current script shows the gap precisely: `post_marker_comment.sh` already takes the
**idempotency marker tag** as a parameter (`<issue-number> <marker-tag> <body>`) — that part was
never the problem. What's hardcoded is the comment's **footer kind**: the script always resolves
its footer via `python3 factory_core/cli.py marker scheduler`, i.e. it always renders `*Posted by
{PRODUCT_NAME} Backlog Scheduler*`. `factory_core/identity.py::_MARKERS` defines six footer
kinds (`factory`, `scheduler`, `refinement`, `autopilot`, `main_red`, `deps_audit`); the
conformance Phase 4 shadow comment needs the `factory` footer (`*Posted by {PRODUCT_NAME} Dark
Factory*`), not `scheduler`. Since the script couldn't produce that footer, `#394` had no way to
call it and inlined the sequence instead.

This ticket adds a footer-kind parameter to `post_marker_comment.sh` and replaces the inlined
block in conformance Phase 4 with a call to it, while keeping every existing caller's behavior
byte-identical.

## Requirements

Derived from the issue body and the refinement Q&A:

1. **New parameter, not a new concept.** `post_marker_comment.sh` gains an optional 4th
   positional argument, `footer-kind`, defaulting to `scheduler` when omitted. It is passed
   straight through, double-quoted, as one argv element to `factory_core/cli.py marker
   "$FOOTER_KIND"` — no bash-side allow-list, because that sub-command already validates it
   (`cli.py:509`, `choices=list(identity._MARKERS)`), so `factory_core/identity.py::_MARKERS`
   stays the single source of truth. The value is never interpolated into the body, a pattern or
   a command string. An unknown kind makes `cli.py marker` exit non-zero; the script's existing
   `2>/dev/null || echo ""` fallback then yields an **empty footer and the comment still posts**
   (same lenient behaviour as a footer-resolution failure today). This is deliberate — the
   helper is advisory plumbing — and is pinned by a test (Requirement 5).
2. **Name it `footer-kind`, not `--marker`.** The issue's phrasing ("a marker-kind argument (or
   a `--marker` override)") is not binding on the literal flag name: `--marker` already names the
   idempotency tag on `providers/cli.py tracker comment` elsewhere in this codebase, and reusing
   it here for an unrelated concept (the footer line) would be actively confusing. Use a plain
   positional argument (the script has no flag parsing today), named `footer-kind` in the usage
   comment.
3. **Zero changes to existing callers.** All 3 current call sites —
   `workflows/archon-dark-factory.yaml`'s `<!-- df-gate-label-failure -->` and `<!-- df-push-failure -->`
   posts, and `scripts/blast_gate_check.sh:312`'s `$HOLD_MARKER` post — omit the new argument and
   keep getting the `scheduler` footer unchanged. This preserves the `#358` invariant that these
   comments must **not** reset the direct-to-pr grace clock: `scheduler.sh:279,325` calls
   `scheduler_lib.sh::elapsed_minutes_since_marker` with the pattern `Posted by <product>
   Refinement Pipeline` and takes the *last* matching comment, so only the `refinement` footer
   kind would move the clock. Neither `scheduler` nor `factory` matches that pattern. Their
   existing string-match assertions in `tests/test_push_gate_dag.py` /
   `tests/test_push_gate_dag_exec.py` must keep passing without edits. The marker-tag handling
   (leading body line + `tracker comment --marker`) is not touched, so comment lookup/update in
   place is unchanged for every marker.
4. **Replace the conformance Phase 4 inline block.** In `commands/dark-factory-conformance.md`
   Phase 4 (inside the `if [ -n "${SHADOW_MODEL_PIN:-}" ]` block; the lines to change are
   `:604-623` on main at 2429106), delete the `FOOTER=...` line, the `mktemp`/`printf`/`tracker
   comment`/`rm -f` lines, and trim the `SHADOW_BODY=` assignment, then post with a single call:
   ```bash
   bash dark-factory/scripts/post_marker_comment.sh "$ISSUE_NUM" "<!-- df-shadow-review -->" \
     "$SHADOW_BODY" factory || true  # TARGET-PATH — advisory post, never blocks PASS (Requirement 3)
   ```
   where `$SHADOW_BODY` keeps everything from the `## Shadow (Fable) Review — conformance`
   heading through the closing fence of the `SHADOW_*` block (heading, blank line, the
   `${SHADOW_VERDICT_LINE:-…}` line, blank line, fenced block) and drops only the leading
   `<!-- df-shadow-review -->` line and the trailing blank line/`---`/`$FOOTER`, since the script
   adds those. **The posted comment body must be byte-identical to today's**
   (`<marker>\n## Shadow (Fable) Review — conformance\n…\n```\n\n---\n<factory footer>`), and
   when/whether it posts is unchanged: same guard, same marker, same update-in-place via
   `tracker comment --marker` (the inline block already used it). The trailing `|| true` is kept
   at the call site: da7aba1's Requirement 3 ("the advisory post must never block a PASS") must
   hold exactly as it does today.

   **Do not touch anything above line 604.** The three-line shadow-model re-resolution snippet
   added by #406 (`ROOT=…` / `SHADOW_MODEL_DEFAULT=…` / `SHADOW_MODEL_PIN=…`, `:594-596`), the
   guard line (`:597`) and the `conformance.md` append (`:598-602`) stay byte-identical;
   `tests/test_shadow_model_resolution.py` pins the snippet (five identical copies, each
   preceding its guard in the same bash block) and must pass unedited. The attestation write,
   `emit_verdict`, every `STATUS:`/`SHADOW_*` line written to `conformance.md`, and Phase 5 are
   not changed.
5. **Test the new parameter.** Add a **pytest** module, `tests/test_post_marker_comment.py`,
   not a shell test. CI already runs `python -m pytest tests/`, so no `.github/workflows/ci.yml`
   edit is needed; a new `tests/test_*.sh` would have to be listed there
   (`tests/test_ci_covers_shell_tests.py`), and any `.github/workflows/` change is a
   HUMAN_REQUIRED blast-radius path (`.factory/adapter.yaml:48`) that would hold this ticket for
   no benefit. **This ticket must not modify `.github/workflows/ci.yml`.** The module runs the
   real script with `subprocess` and a stub `python3` executable placed first on `PATH` (the stub
   records its argv, prints a fixed footer for `marker <kind>`, copies the `--body-file` content
   to a capture file, and exits with a configurable code), skipping on non-POSIX hosts the way
   `tests/test_push_gate_dag_exec.py` does. It must cover:
   - a 3-arg call (no `footer-kind`) still resolves `marker scheduler` — the back-compat default;
   - a 4th arg of `factory` resolves `marker factory` instead;
   - the posted body is assembled as `<marker-tag>\n<body>\n\n---\n<footer>` regardless of which
     footer kind was requested, and `--marker` receives the marker tag unchanged;
   - when the `marker` sub-command fails (unknown kind), the comment still posts with an empty
     footer and the script's exit code is the tracker CLI's;
   - the underlying tracker CLI's non-zero exit code propagates as this script's exit code (the
     behavior `|| true` at call sites relies on to stay advisory-only).
6. **Update the existing conformance-command test.**
   `tests/test_conformance_command_shadow_review.py::test_phase_4_pass_emits_shadow_fields_and_marker_comment`
   currently asserts the literal `--body-file "$TMPFILE" || true` inside the Phase 4 section —
   that string disappears once the block is replaced. Update the assertion to check the section
   calls `post_marker_comment.sh`, passes `factory` as the footer-kind argument, and ends `||
   true`; additionally assert `mktemp` no longer appears in that section, so the inlined copy
   cannot silently creep back in a future edit; that `## Shadow (Fable) Review — conformance`
   is still in the section (the heading must survive the trim); and that
   `<!-- df-shadow-review -->` occurs exactly once in the section (as the call's marker
   argument — a second occurrence would mean the marker line was left in `SHADOW_BODY` and is
   posted twice).
7. **Sync the docs that describe the old behavior or cite moved lines.** The issue's "out of
   scope" list is about runtime behavior (gate scripts, breaker, budgets, tool permissions) and
   does not cover documentation:
   - `scripts/post_marker_comment.sh`'s own header comment: update the `Usage:` line to include
     `[footer-kind]`, and reword the paragraph currently justifying the hardcoded `scheduler`
     footer to instead describe it as the *default* and why (`#358`: the grace clock keys on the
     `refinement` footer, so a caller must never pass `refinement` for a comment that should not
     reset it).
   - **Line citations of `commands/dark-factory-conformance.md` below the edited block.** The
     replacement removes lines (about 8), so every citation of a line after `:623` shifts and the
     reference checker only range-checks, so it will not notice. Re-anchor each by content, not
     by arithmetic: `docs/features.md:134` (`dark-factory-conformance.md:656`, the
     `write_memory_entry` call), `docs/run-lifecycle.md:814` (`:580-674` PASS and `:676-752`
     BLOCKED ranges), `docs/run-lifecycle.md:819` and `:1319` (`:748-752`). Citations at or above
     `:596` (`docs/configuration.md`, `docs/features.md:111`, `docs/run-lifecycle.md:1394`) do
     not move. Re-run `git grep -n "dark-factory-conformance.md:" -- docs README.md` at implement
     time in case main moved again.
   - `docs/run-lifecycle.md:688-690`: currently states the script's footer is unconditionally
     `scheduler`; update to describe the new 4th-argument default, and re-check the
     `post_marker_comment.sh:16-20` line citation after the header-comment edit shifts line
     numbers.
   - `docs/features.md:110` needs no change — it cites workflow/script call-site line numbers,
     none of which move.

## Architecture / Approach

Minimal, additive change to one script and one call site:

- `scripts/post_marker_comment.sh`: add `FOOTER_KIND="${4:-scheduler}"` and change the footer
  resolution line from `python3 "$_PCLI_FACTORY_CORE" marker scheduler` to `python3
  "$_PCLI_FACTORY_CORE" marker "$FOOTER_KIND"`. Everything else (marker-tag line, body, `---`
  separator, `mktemp`/`printf`/`tracker comment --marker`/`rm`, exit-code propagation) is
  unchanged.
- `commands/dark-factory-conformance.md` Phase 4: drop the inlined `FOOTER`/`SHADOW_BODY`/
  `mktemp`/`tracker comment`/`rm` lines; keep the `SHADOW_MODEL_PIN`/`SHADOW_VERDICT_LINE`/
  `SHADOW_*` field resolution exactly as-is (that part isn't duplicated logic — it belongs to
  conformance, not to the marker-posting helper); call `post_marker_comment.sh` with the `factory`
  footer kind and the trimmed body content.
- No change to `factory_core/identity.py`, `factory_core/cli.py`, or any other caller.
- No change to `.github/workflows/**`, `workflows/**`, `gate_*`, `verdict_gate_check.sh`,
  breaker, budgets, tool allow/deny lists or `deploy/**`.

## Acceptance Criteria

1. `bash scripts/post_marker_comment.sh N TAG BODY` (3 args) invokes `cli.py marker scheduler`;
   with a 4th arg `factory` it invokes `cli.py marker factory`
   (`tests/test_post_marker_comment.py`).
2. `git diff origin/main -- workflows/ scripts/blast_gate_check.sh .github/` is empty.
3. Phase 4 of `commands/dark-factory-conformance.md` contains no `mktemp`, calls
   `post_marker_comment.sh … factory || true`, keeps the `## Shadow (Fable) Review — conformance`
   heading, and contains `<!-- df-shadow-review -->` exactly once
   (`tests/test_conformance_command_shadow_review.py`).
4. `tests/test_shadow_model_resolution.py`, `tests/test_push_gate_dag.py`,
   `tests/test_push_gate_dag_exec.py` and `tests/test_ci_covers_shell_tests.py` pass unedited.
5. Every doc citation listed in Requirement 7 points at the same content it pointed at before.

## Alternatives Considered

1. **`--marker <kind>` flag instead of a positional arg.** Rejected: the script has no existing
   flag-parsing machinery, and `--marker` already means something else (the idempotency tag) one
   layer down in `providers/cli.py tracker comment`. Introducing a same-named flag with a
   different meaning in a script that calls that exact CLI would be a readability trap.
2. **Leave the script as-is; only extract a *second* helper (`post_factory_marker_comment.sh`) for
   the `factory` footer case.** Rejected: this just relocates the duplication problem — a third
   footer kind would need a third near-identical script. A single parameterized script matches
   the existing `_MARKERS` dict's shape (one key selects one footer) and scales to all six kinds
   with zero new files.
3. **Change the default footer kind instead of adding a parameter (e.g. resolve it from the
   marker-tag's own naming convention).** Rejected: marker tags (`<!-- df-gate-label-failure -->`,
   `<!-- df-shadow-review -->`, etc.) have no structural relationship to footer kind today, and
   inventing one would be a larger, riskier refactor than the issue asked for, with no benefit
   over an explicit 4th argument.

## Open Questions (non-blocking)

- None. The brainstorming Q&A resolved both the parameter shape/name and the test/docs scope
  before this spec was written.

## Assumptions (flagged)

- Assumes `factory_core/identity.py::_MARKERS`'s six keys are stable for the duration of this
  ticket's implementation; the script intentionally does not duplicate that list, so a future
  addition to `_MARKERS` needs no change here.
- Assumes no other caller of `post_marker_comment.sh` exists outside the three cited above
  (confirmed via `grep -rn "post_marker_comment" .` against tracked files at refinement time,
  2026-11-28); a conformance-gate recheck at implement/conformance time should re-run that grep in
  case a concurrent ticket added a new caller.

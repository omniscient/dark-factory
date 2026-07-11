# Robust dependency-ref parsing in `dependencies_met()` (#204)

**Issue:** #204 · **Status:** spec-pending-review

## Overview

`dependencies_met()` (`scheduler.sh:623-658`) is the scheduler's cross-ticket
ordering gate: before dispatching an issue, it greps the issue body for declared
dependencies and blocks dispatch until each one reaches `Done` (or is closed/absent
from the board). The current extraction is a single regex:

```bash
deps=$(echo "$body" | grep -oP 'Depends on:\s*#\K\d+' || true)
```

This has one fail-open defect and one fail-closed defect, both observed in
production:

- **Fail-open:** the pattern only matches a bare `Depends on: #123` line. It misses
  bold-markup variants (`**Depends on:** #123`, seen on markethawk authz slices
  #551-#559) and `## Blocked by` bullet sections (seen on markethawk extension
  slices #440-#446) entirely — declared ordering silently goes unenforced. It also
  only captures the first `#\d+` on a line with multiple refs.
- **Fail-closed:** the regex runs over the raw body with no anchoring, so a
  quoted/illustrative `Depends on: #999` inside a code fence or inline-code span
  counts as a real dependency. This is the mechanism that stranded #389, and it
  live-reproduced on this ticket's own first body revision (its Problem section
  quoted digit examples; the scheduler read them as phantom deps and never
  dispatched the ticket despite `ready-for-agent` + `direct-to-pr`). The issue body
  visible today has been reworded with letter placeholders specifically to avoid
  re-triggering this while the ticket itself is being refined.

The fix rewrites the extraction into a small preprocessing pipeline — strip
fenced/inline code, tolerate markdown emphasis around the `Depends on:` label,
recognize `Blocked by` heading sections, and capture every ref on a line — while
keeping the function's external contract (signature, return codes, log line
formats) unchanged. Everything stays in `scheduler.sh`; no new file, no new
dependency, no interaction with the separate (unimplemented) `scheduler_lib.sh`
extraction proposed by #185.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **Bold/italic-tolerant `Depends on:` label matching, one pattern for all
   asterisk placements** (issue AC #1; Q&A #1). Recognize, without distinguishing
   between them:
   - `**Depends on:** #123` — bold wraps label + colon (the shape named in the
     issue, on markethawk #551-#559)
   - `**Depends on**: #123` — bold wraps only the label word
   - `Depends on: **#123**` — label is plain, the ref itself is bolded
   - `*Depends on:*` — single-asterisk/italic, same as double-asterisk/bold

   Implementation approach: strip every literal `*` character from the
   (fence/inline-code-stripped, see Requirement 3) body **before** label matching.
   Every one of the four shapes above collapses to the existing plain-text shape
   (`Depends on: #123` / `Depends on: #123` / `Depends on: #123` / `Depends on:`)
   once asterisks are removed — no separate regex branch per shape is needed. This
   is safe because the stripped copy is used only for ref-scanning, never
   re-emitted; stray asterisks elsewhere in the body (e.g. `*`-bullets, unrelated
   emphasis) are irrelevant to that scan.

2. **`## Blocked by` bullet sections, any heading level, case-insensitive, literal
   wording only** (issue AC #2; Q&A #2):
   - Heading match: any of `#` through `######` whose text reads "Blocked by"
     (case-insensitive: `blocked by`, `BLOCKED BY`, `## Blocked By`, etc. all
     match). Do **not** also match "Blocked on" or other synonyms — the issue names
     "Blocked by" specifically, and there's no in-repo template
     (`scripts/architecture_slice.py` emits no dependency lines today) that pins a
     different wording.
   - Section end: **any** subsequent heading line, regardless of level, ends the
     section — do not track relative heading depth. This is the simplest rule that
     satisfies AC #2 and matches how the section is actually delimited in practice
     (a short bullet block followed by the next real heading).
   - Bullet markers: `-`, `*`, and `+` (the three standard Markdown unordered-list
     markers) are all recognized. Only `-` has been observed in production, but
     accepting all three is a one-character-class difference in the same tolerant
     spirit as Requirement 1, and prevents the exact class of fail-open miss this
     ticket exists to fix.
   - Bullet content: capture every `#\d+` found anywhere on a qualifying line
     within the section — not just a bare `- #NNN`. `- #200 (backend)`,
     `- #200, #201`, and `- Depends on #200` under the heading all resolve.

3. **Code-fence and inline-code stripping runs once, before both extraction paths**
   (issue AC #4; Q&A #1, #2, #3). A single preprocessing pass produces the body
   that Requirements 1 and 2 both operate on:
   - Fenced blocks (` ``` ` to ` ``` `): stripped line-by-line via an `in_fence`
     toggle. An **unclosed** fence (odd number of ` ``` ` markers) leaves the toggle
     "on" through end-of-body — everything after the last unclosed fence-open is
     treated as inside-a-fence and dropped. This is the conservative,
     already-agreed direction (Q&A #3): the toggle-and-skip implementation yields
     this behavior with no extra code, and erring toward "ignore" can only fail
     open (a ticket dispatches when maybe it shouldn't), which is strictly less
     harmful than the fail-closed stranding bug (#389, and this ticket's own
     original body) that AC #4 exists to kill.
   - Inline code spans (`` `...` `` within a single line): stripped per-line after
     fence removal, so a quoted example like `` `Depends on: #999` `` inside prose
     never reaches the label/section matchers.
   - Both the `Depends on:` label scan (Requirement 1) and the `Blocked by`
     section scan (Requirement 2) run against this same stripped body — neither
     path re-implements its own fence/inline-code handling.

4. **Multi-ref lines yield every ref, not just the first** (issue AC #3; Q&A #1,
   #2). This falls out of Requirements 1 and 2 directly: once a line is confirmed
   to qualify (matches the label pattern, or falls inside a `Blocked by` section),
   every `#\d+` substring on that line — after the label, for `Depends on:` lines —
   is extracted, not just the first. A single line declaring
   `Depends on: #200, #201` blocks on both.

5. **Existing plain-format behavior is unchanged** (issue AC #5). The current
   `tests/test_scheduler.sh` section-N cases (N1-N9, lines ~760-879) — bare
   `Depends on: #200` lines, single and multi-line, on-board/off-board, dep found
   vs. `gh` failure — must continue to pass unmodified. The new pipeline is a
   strict superset: a body with no asterisks, no `Blocked by` heading, and no code
   fences reduces to exactly the extraction the current regex already does.

6. **New test coverage lives alongside the existing section-N cases in
   `tests/test_scheduler.sh`**, not in a new file (Q&A #3 — the fix stays in
   `scheduler.sh`, so its tests stay in the file that already sources it with
   `SCHEDULER_SOURCE_ONLY=1`). New cases to add:
   - Bold-label variants: `**Depends on:** #200`, `**Depends on**: #200`,
     `Depends on: **#200**` — each should behave identically to plain
     `Depends on: #200` (N2/N3 shape).
   - `Blocked by` section: a `## Blocked by` heading with `- #200` /  `* #201` /
     `+ #202` bullets, plus a case with a lowercase heading (`### blocked by`) and
     a case where a following heading of any level correctly ends the section
     (content after the next heading is NOT treated as a dep).
   - Multi-ref line: `Depends on: #200, #201` on one line blocks on both (mirrors
     N7/N8 but as a single line instead of two).
   - Fenced fake dep: a closed code fence containing `Depends on: #999` (unopened
     issue) does not block dispatch — this is the direct regression test for this
     ticket's own original-body incident (AC #4).
   - Unclosed fence: a body with a single, unclosed ` ``` ` marker followed by
     `Depends on: #999` does not block dispatch (Q&A #3's fail-closed-to-ignore
     rule).
   - Inline code span: a non-fenced line containing `` `Depends on: #999` `` inline
     does not block dispatch.

7. **Accepted formats documented in a comment above `dependencies_met()`** (issue's
   Solution section), enumerating: plain `Depends on: #N`, bold/italic-wrapped
   variants (asterisks tolerated anywhere around the label/colon/ref), multi-ref
   lines, `## Blocked by`-through-`######` bullet sections (`-`/`*`/`+` markers,
   ended by the next heading of any level), and the fence/inline-code exclusion.

## Architecture / Approach

All changes are confined to `scheduler.sh`, in and immediately above
`dependencies_met()` (currently `:623-658`). The dep-collection logic is replaced;
the rest of the function (per-dep board/`gh`-state resolution loop, log line
formats, return codes) is untouched.

### New preprocessing + extraction, replacing line 629

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
  # 1) drop fenced blocks (unclosed fence => drops to EOF), then inline spans,
  #    then all literal '*' so bold/italic collapses to plain text.
  stripped=$(printf '%s\n' "$body" | awk '
    /^```/ { in_fence = !in_fence; next }
    in_fence { next }
    { print }
  ' | sed -E 's/`[^`]*`//g' | tr -d '*')

  # 2) plain/bold "Depends on:" lines - capture every ref after the label
  local plain_deps
  plain_deps=$(printf '%s\n' "$stripped" \
    | grep -inE 'depends[[:space:]]+on[[:space:]]*:' \
    | sed -E 's/.*depends[[:space:]]+on[[:space:]]*://I' \
    | grep -oP '#\K[0-9]+')

  # 3) "Blocked by" section - any heading ends it, any of -/*/+ as bullets
  local blocked_deps
  blocked_deps=$(printf '%s\n' "$stripped" | awk '
    /^#{1,6}[[:space:]]*/ {
      if (tolower($0) ~ /^#{1,6}[[:space:]]*blocked by/) { insec = 1 } else { insec = 0 }
      next
    }
    insec { print }
  ' | grep -oP '#\K[0-9]+')

  printf '%s\n%s\n' "$plain_deps" "$blocked_deps" | grep -v '^$'
}
```

`dependencies_met()` then becomes:

```bash
dependencies_met() {
  local issue_num="$1"
  local board_items="$2"
  local body
  body=$(gh issue view "$issue_num" --repo "$FACTORY_REPO_SLUG" --json body -q '.body' 2>/dev/null) || return 0
  local deps
  deps=$(_scan_body_for_deps "$body")
  if [ -z "$deps" ]; then
    return 0
  fi
  # ... unchanged from here (per-dep board/gh resolution loop, :633-658)
```

Notes for the implementer:

- `_scan_body_for_deps` is a new, pure (no `gh`/`dispatch`/board side effects)
  helper defined immediately above `dependencies_met()`, inside the
  `SCHEDULER_SOURCE_ONLY` sourceable region — same file, same test-sourcing
  contract as today (Requirement 5/6; Q&A #3).
- The `sed -E '.../I'` case-insensitive substitution flag is a GNU sed extension;
  `scheduler.sh` already assumes GNU tools elsewhere (`grep -oP` is a GNU-grep PCRE
  extension), so this introduces no new portability constraint.
- Duplicate refs (the same `#N` matched by both the plain-line scan and the
  blocked-by scan, or twice within one multi-ref line) are not de-duplicated before
  the per-dep loop. The existing loop already tolerates this shape (nothing in the
  current code assumes uniqueness); a duplicate simply repeats one `gh`/board
  lookup and one gate check for the same number, which is harmless. De-duplication
  can be added by the implementer as a `sort -u` on the final `deps` list if
  preferred, but it is not required by any acceptance criterion.
- Ordering: refs are yielded in the order the plain-line scan finds them, followed
  by the order the blocked-by scan finds them. The existing per-dep loop's
  behavior does not depend on cross-dep ordering (each dep is independently
  resolved and the first non-`Done` dep short-circuits the return), so this is
  consistent with current semantics.

## Alternatives considered

1. **Extend the single `grep -oP` pattern with alternation instead of a
   multi-stage pipeline** (e.g. one giant PCRE covering plain, bold, and
   blocked-by shapes at once). Rejected: `Blocked by` sections are multi-line
   (heading + N bullets), which a single-line `grep -oP` pass structurally cannot
   express without also invoking `awk`/`sed` for the fence and section-scoping
   logic anyway — the "one regex" framing doesn't actually shrink the
   implementation, and a multi-stage pipeline keeps each concern (fence stripping,
   label tolerance, section scoping) independently testable and readable, per this
   ticket's own "document the accepted formats" requirement.

2. **Move `dependencies_met()` (and the new helper) into a new
   `scripts/scheduler_lib.sh` now**, anticipating #185's planned extraction.
   Rejected (Q&A #3): #185 ("Internal seams for the scheduler poll loop") is a
   separate, larger, not-yet-implemented refactor that owns exactly this kind of
   file move, including rewiring the `SCHEDULER_SOURCE_ONLY` guard and
   re-pointing test sourcing. Doing part of that here would inflate a `size: S`,
   `direct-to-pr` bug fix into overlapping scope with a different ticket. This
   spec leaves a clean, self-contained function in `scheduler.sh` that #185 can
   relocate wholesale, with no rework, once it lands.

3. **Treat an unclosed code fence as fail-open (i.e., still scan the trailing
   content for deps)** rather than fail-closed. Rejected (Q&A #3): the ticket's
   entire second failure mode is about quoted examples wrongly becoming real
   deps; erring toward "still ignore it" on the ambiguous unclosed-fence case is
   consistent with that intent, is the natural (zero-extra-code) output of the
   `in_fence` toggle implementation, and fails in the safer direction (a ticket
   dispatches when maybe it shouldn't, rather than stranding indefinitely).

4. **Recognize "Blocked on" as a synonym for "Blocked by"**, or require an exact
   `##` heading level. Rejected (Q&A #2): the issue names "Blocked by" literally
   and there's no in-repo template producing a different wording or a different
   heading level; adding a synonym or narrowing the heading-level match is
   speculative scope a size:S fix shouldn't carry.

## Open questions (non-blocking)

- Whether to de-duplicate the final `deps` list (see Architecture note above) is
  left to the implementer's judgment; no acceptance criterion requires it.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist yet on this branch
  (this refinement is the first to write to it here) — created as part of this
  spec's commit, matching the precedent of sibling `refine/*` branches' specs
  (e.g. `2026-07-07-single-source-safety-defaults-design.md`) that were also
  written against a base commit predating any merged spec.
- **[ASSUMPTION]** GNU `grep`/`sed`/`awk` are the available toolchain (already
  relied on by the existing `grep -oP` in `dependencies_met()` and elsewhere in
  `scheduler.sh`), so the `sed ... /I` case-insensitive flag and `awk` toggle
  pattern used in Requirement 3's implementation sketch are safe to rely on
  without a portability shim.
- **[ASSUMPTION]** No dependency-declaring issue body in production combines a
  `Blocked by` bullet with bold/italic markup around the `#NNN` ref inside that
  bullet (e.g. `- **#200**`) in a way that would matter differently from the
  already-covered "capture every `#\d+` on a qualifying line" rule — since
  asterisks are stripped globally before both extraction paths run (Requirement
  1), this combination is already handled without additional cases, but it hasn't
  been separately observed in production the way the two named formats have.

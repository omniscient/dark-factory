# `memory_write.py`: parameterize the `[AVOID]` tag with a `--kind` argument

**Issue:** #38

## Overview / Problem Statement

`scripts/memory_write.py` is the sole write path into `.archon/memory/*.md` for refine
(direct call) and for conformance/code-review (via `gate_lib.sh::write_memory_entry()`).
Per the memory contract (`docs/dark-factory-memory-contract.md` §1/§2) an authoritative
entry should carry one of three tags — `[PATTERN]` (validated approach, follow it),
`[AVOID]` (confirmed anti-pattern, never do this), or `[FIX]` (corrective action for a
known bug class) — chosen by the writer to match the lesson's actual content. Instead, the
entry-construction branch of `main()` hardcodes the tag:

```python
# scripts/memory_write.py:194-199
entry = (
    f"- [AVOID] {args.text} "
    f"<!-- issue:#{args.issue} date:{today_str} expires:{expires_str} "
    f"source:{safe_source} agent:{safe_source} scope:{scope} "
    f"path:{safe_path} -->"
)
```

Refine's Phase 5 §7 writes two kinds of lesson: (a) one entry per architectural decision
where a trade-off was weighed, and (b) codebase conventions found during context assembly.
Because the tag is always `[AVOID]`, a lesson whose whole content is "do X" (a convention,
or a chosen approach with no rejected alternative) reads as an anti-pattern — semantically
inverted for a future reader, exactly as filed in #38 (follow-up from #145/#652, flagged
non-blocking in #149's conformance pass). The refine command's own placeholder
(`--text "<the rejected or chosen approach and concrete reasoning>"`,
`commands/dark-factory-refine.md:200`) leaves it open which approach the `[AVOID]` tag
refers to.

Conformance and code-review are not semantically affected — both only ever record a
remediated MATERIAL violation / blocking finding, which is genuinely `[AVOID]`-shaped —
but both route through the same script and must keep working unchanged.

The #652 tool-limitation entry (`.archon/memory/dark-factory-ops.md:59`, written by
`implement`) and `docs/dark-factory-memory-contract.md` §4 both document the hardcode as
today's expected behavior. The docs are updated by this ticket; the memory entry is left
to expire (Requirement 4).

## Requirements

1. **`memory_write.py` gets a `--kind {pattern,avoid,fix}` argument** (`argparse`,
   `choices=["pattern", "avoid", "fix"]`, `default="avoid"`) that selects the emitted tag
   prefix (`PATTERN`/`AVOID`/`FIX`) in place of the hardcoded `[AVOID]` at
   `scripts/memory_write.py:194-199`. With the flag omitted, the script's output is
   byte-identical to today's. The value is a closed allowlist: `argparse` `choices`
   rejects anything else (exit 2, before any directory or file is created or written),
   and the tag is derived from the validated choice only, so no caller-supplied free text
   can reach the tag position of a memory line. Update the module docstring's usage block
   (`scripts/memory_write.py:8-14`) to list `--kind`.
2. **Tagging rule for refine (Phase 5 §7).** One entry per lesson, as today (not two).
   - **§7a, a decision where a trade-off was weighed: `--kind avoid`, and the body leads
     with the rejected approach** in the fixed order
     `Rejected approach: <what and why>. Chosen approach: <what to do instead>.`
     The `[AVOID]` tag then names the first-labelled (rejected) approach and the entry
     carries its replacement. This is the shape the existing #587 entry in
     `.archon/memory/architecture.md` already has.
   - **§7b, a convention (or any lesson) that contains no prohibition and no rejected
     alternative: `--kind pattern`**, body states the convention positively. If the
     lesson contains a prohibition or a rejected alternative, it is written per §7a
     (`--kind avoid`, rejected approach first), in whichever memory file §7b selects.
   - `--kind fix` exists for completeness (issue #38 asks for all three); the refine
     command does not use it.
   - **Why not `[PATTERN]` for a combined entry.** The readers that key on the tag cover
     `[AVOID]`/`[FIX]` only (see "Reader behaviour"). A combined entry tagged `[PATTERN]`
     would take its `Rejected approach:` half out of the plan reviewer's
     `[MEMORY-VIOLATION]` scan and out of the refine/plan AVOID rules — a check that
     exists today, because such entries are `[AVOID]`. Leading with the rejected approach
     makes the existing tag correct without removing any check and without touching any
     reader text.
   - **Self-contained, shell-safe snippet.** The current §7a snippet
     (`commands/dark-factory-refine.md:189-203`) has three defects, all fixed by the
     rewrite: the lesson is passed inside double quotes, so `$NAME`, `$(…)` and backticks
     in a lesson are expanded by the shell before `memory_write.py` sees them (observed
     2026-11-28: an entry naming `$ARTIFACTS_DIR` was written with the variable blanked);
     it uses `$ISSUE_NUM` without setting it, although shell state does not persist
     between Bash tool calls (it was set at `:83`, in another block); and its
     `\  # TARGET-PATH` line is not a line continuation (the backslash escapes a space),
     so the block does not run as written. Replace the block with exactly this (indented
     to match the list item):

     ```bash
     REPO_ROOT=$(git rev-parse --show-toplevel)
     ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
     MEMORY_WRITE="${REPO_ROOT}/dark-factory/scripts/memory_write.py"  # TARGET-PATH

     # One call per lesson; memory_write.py handles expiry cleanup, dedup and the R4 cap.
     # The quoted heredoc keeps the lesson literal: shell variables, command
     # substitutions and backticks in it are NOT expanded. Keep the body on the lines
     # between the two LESSON markers; the closing marker stands alone at the start of
     # its line (run this block without the list indentation).
     # A weighed decision: --kind avoid, rejected approach FIRST. The [AVOID] tag names it.
     LESSON_TEXT=$(cat <<'LESSON'
     Rejected approach: <what was rejected and the concrete reason>. Chosen approach: <what to do instead>.
     LESSON
     )
     python3 "$MEMORY_WRITE" \
       --kind avoid \
       --target .archon/memory/architecture.md \
       --path-prefix .archon/commands/ \
       --text "$LESSON_TEXT" \
       --source refine \
       --issue "$ISSUE_NUM"
     ```

     The "Tool limitation (#652)" comment (`:194-196`) is deleted. §7b gains one
     sentence: use the same block with the area file as `--target`, a matching
     `--path-prefix`, and `--kind pattern` with a positively stated body when the
     convention contains no prohibition (otherwise `--kind avoid`, rejected approach
     first).
   - **Command-file shape constraints.** The edit stays inside §7a/§7b (currently
     `commands/dark-factory-refine.md:186-208`). Archon-substituted names keep the bare
     form already used in this file (`$ARTIFACTS_DIR`, never `${ARTIFACTS_DIR}`);
     `$LESSON_TEXT`, `$MEMORY_WRITE`, `$ISSUE_NUM` and `${REPO_ROOT}` are ordinary shell
     variables. The placeholder lesson in the command text must not itself contain an
     Archon-substituted name. No other line of the command changes — in particular
     `:94` is **not** edited.
3. **`scripts/gate_lib.sh` is not modified (byte-identical).** `write_memory_entry()`
   (`scripts/gate_lib.sh:45-53`) keeps calling `memory_write.py` without `--kind` and so
   keeps writing `[AVOID]` through the default. No explicit flag is needed: the function
   resolves `memory_write.py` from its own directory (`$(dirname "${BASH_SOURCE[0]}")`),
   so the two files always come from the same checkout and cannot version-skew, and a
   test pins the behaviour (Requirement 6). Its two callers —
   `commands/dark-factory-conformance.md:652` and
   `commands/dark-factory-code-review.md:306` — are unchanged. This repo tracks no
   `dark-factory/scripts/` or `.archon/commands/` copies (those paths exist only in a
   run's clone); there is no mirror file to edit, and none may be created.
4. **No existing memory line is edited, retagged or deleted by this ticket.**
   `commands/dark-factory-implement.md:388-392` permits only expiry cleanup, R4 cap-drop
   and R5 invalidation on existing lines, and this ticket needs no exception to it:
   - Under Requirement 2 a rejected-first combined entry is correctly `[AVOID]`, so the
     #587 entry in `.archon/memory/architecture.md` stays as it is. The other
     refine-written `[AVOID]` entries are not judged one by one; they age out by TTL or
     cap eviction.
   - The #652 tool-limitation entry (`.archon/memory/dark-factory-ops.md:59`,
     `expires:2026-12-30`) is left to expire. It was true when written and is merely
     outdated, which the contract says is not an invalidation
     (`docs/dark-factory-memory-contract.md:96-97`); its own text is qualified ("until
     the tag is parameterized"); and deleting an unexpired line would be undone by the
     implement command's post-write guard, which re-appends unexpected deletions
     (`commands/dark-factory-implement.md:426-464`).
   - New entries written by the implement/conformance/code-review phases of this
     ticket's own run through the normal mechanisms are of course allowed.
5. **Documentation updates.** `docs/dark-factory-memory-contract.md` §4, "How each role
   writes": rewrite the Refine row (`:155`, tags `[AVOID]`, `[PATTERN]`; `[FIX]`
   available but unused) and the paragraph at `:159-161` to describe `--kind`, its
   default, the Requirement 2 tagging rule and that the gate path relies on the default;
   the Conformance/code-review row (`:156`, "`[AVOID]` only") stays true and is not
   changed. Also correct `:70` (state diagram: "memory_write.py AVOID entry") and `:121`
   (Refine row: "every refine entry is tagged `[AVOID]`"), and
   `docs/dark-factory-memory-v2.md:25` ("Writes new `[AVOID]` lessons") and `:36`
   ("`memory_write.py` (`[AVOID]` only)"). §4 must also record the reader behaviour
   described under "Reader behaviour" below.
   **Line citations of `commands/dark-factory-refine.md`.** The snippet rewrite changes
   the file's length from `:189` on, and `scripts/docs_refs_check.py` only checks that a
   cited line exists, so CI will not catch drift. Re-anchor each of these by content
   after editing the command: `docs/features.md:134` (`dark-factory-refine.md:197`, the
   line that invokes `memory_write.py` — after the rewrite, the `python3 "$MEMORY_WRITE"`
   line); `docs/run-lifecycle.md:536` (`:176-219`, Phase 5 step 7) and `:537`
   (`:221-287`, Phase 6 through end of file); `docs/configuration.md:128`
   (`dark-factory-refine.md:231`, the `SPEC_GRACE=` line), `:131` and `:772` (`:230`, the
   `grep -q "direct-to-pr"` line). Citations of lines above `:186` do not move.
   `docs/domain.md` needs no edit.
6. **Test coverage** (the plan enumerates concrete cases), pytest only, in
   `tests/test_memory_write.py` unless stated:
   - `--kind pattern` and `--kind fix` each emit the corresponding tag with the unchanged
     metadata comment;
   - omitting `--kind` emits `[AVOID]` and the written line is byte-identical to the
     `--kind avoid` line for the same arguments;
   - an invalid value (e.g. `--kind "AVOID] injected"`, `--kind AVOID`) exits non-zero,
     leaves an existing target byte-identical and creates no directory for a fresh one;
   - a `--kind pattern` write whose normalized text matches an existing `[AVOID]` line
     reinforces it and leaves its tag unchanged;
   - the cap counts and evicts by date regardless of kind;
   - **gate path:** sourcing `scripts/gate_lib.sh` in `bash` and calling
     `write_memory_entry` writes a line starting `- [AVOID] ` (skip on a non-POSIX host or
     when `bash` is missing, as `tests/test_shadow_model_resolution.py` does);
   - **refine snippet** (new `tests/test_refine_command_memory_snippet.py`): extract the
     §7a bash block from `commands/dark-factory-refine.md`, strip the list indentation,
     replace only the placeholder lesson line with a lesson containing `$HOME`,
     `$(echo x)`, a backtick pair and both quote characters, and run it in a temp git
     repo that has a copy of `scripts/memory_write.py` at
     `dark-factory/scripts/memory_write.py` and an `issue.json` under an exported
     `ARTIFACTS_DIR`, with `ISSUE_NUM`, `REPO_ROOT`, `LESSON_TEXT` and `MEMORY_WRITE`
     removed from the environment. Assert exit 0 and that
     `.archon/memory/architecture.md` holds one `- [AVOID] ` line with the lesson
     verbatim (unexpanded) and `issue:#<n>` from `issue.json`. Static assertions on the
     same block: it contains `--kind avoid`, `<<'LESSON'`, `Rejected approach:` before
     `Chosen approach:`, no `${ARTIFACTS_DIR}`, and the file no longer contains
     "Tool limitation (#652)". Same POSIX skip, extended to a missing `jq`.
   Do **not** add a `tests/test_*.sh` and do not edit `.github/workflows/ci.yml`.
   `tests/test_memory_write_gate.sh` and `tests/test_conformance_memory_write.sh` are not
   run by CI (`tests/test_ci_covers_shell_tests.py:72,79`); they need no edit and must
   still pass when run by hand.

## Acceptance Criteria

1. `python3 scripts/memory_write.py … --kind pattern|fix|avoid` writes `- [PATTERN] `,
   `- [FIX] `, `- [AVOID] ` respectively; without `--kind` it writes `- [AVOID] `; any
   other value exits 2 and writes nothing.
2. `git diff origin/main...HEAD -- scripts/gate_lib.sh scripts/memory_retrieve.py
   refinement-skills/ commands/dark-factory-plan.md commands/dark-factory-conformance.md
   commands/dark-factory-code-review.md commands/dark-factory-implement.md
   .github/ workflows/ config/ deploy/` is empty.
3. `git diff origin/main...HEAD -- .archon/memory/` shows no removed or modified line that this
   ticket's implementer edited by hand (only lines added, reinforced, expired or
   cap-evicted by the normal write mechanisms, plus the refine-phase commit already on
   the branch).
4. `git diff origin/main...HEAD -- commands/dark-factory-refine.md` shows changes only
   inside Phase 5 §7a/§7b; the §7a block runs verbatim (test in Requirement 6).
5. The docs named in Requirement 5 no longer state that `memory_write.py` writes only
   `[AVOID]`, and every listed citation points at the content it pointed at before.
6. `python -m pytest tests/ -v` passes, including the new cases, with no change to
   `ci.yml`.

## Architecture / Approach

A minimal, additive CLI change plus one command-snippet rewrite plus a documentation
correction — no new modules, no change to retrieval (`memory_retrieve.py`), no change to
`_ENTRY_RE` (`scripts/memory_write.py:27`), which already matches all three tags.

- **`scripts/memory_write.py`**: add `--kind` to `_parse_args()` (`:93-100`); thread
  `args.kind` into the entry-construction f-string (`:194-199`) as
  `f"- [{args.kind.upper()}] ..."`; update the docstring usage block. No change to the
  reinforcement path (`:164-187`): reinforcement only refreshes `date:`/`expires:` on a
  normalized-text match and never retags an existing line.
- **`commands/dark-factory-refine.md` Phase 5 §7a/§7b**: per Requirement 2.
- **`docs/dark-factory-memory-contract.md`, `docs/dark-factory-memory-v2.md`,
  `docs/features.md`, `docs/run-lifecycle.md`, `docs/configuration.md`**: per
  Requirement 5.
- **`tests/test_memory_write.py`, `tests/test_refine_command_memory_snippet.py`**: per
  Requirement 6.

Not touched: `scripts/gate_lib.sh`, `scripts/memory_retrieve.py`, `refinement-skills/**`,
every command other than `dark-factory-refine.md`, `.archon/memory/**` (by hand),
`deploy/**`, `gate_*`, verdict/blast gate scripts, breaker, budget config, tool
allow/deny lists, `workflows/**`, `.github/workflows/**`.

### Reader behaviour (explicit)

No script treats the three tags differently. `memory_retrieve.py` filters only
`PROVISIONAL`/`INVALID`, expiry, source and path
(`passes_line_filters`, `scripts/memory_retrieve.py:274-295`) and ranks by path
specificity, label boost and date (`:457-464`); its `AUTHORITATIVE_KINDS` constant
(`:75`) is not used in any filter. `memory_write.py`'s dedup, expiry and cap/eviction
match all three tags through `_ENTRY_RE`. So a tag changes neither what is retrieved into
agent context nor what is evicted.

Three pieces of prompt text key on the tag. **None of them is changed by this ticket:**

- `commands/dark-factory-refine.md:94` and `commands/dark-factory-plan.md:86` — "if a
  memory entry marks an approach as AVOID, do not specify/plan that approach".
- `refinement-skills/architect-prompt.md:36` — the plan reviewer runs its
  `[MEMORY-VIOLATION]` scan over `[AVOID]` and `[FIX]` entries only.

What changes for them, stated as a behaviour change:

- **Entries with a rejected approach or prohibition: no change.** They are still written
  `[AVOID]` and still covered by all three texts; the fixed `Rejected approach: …` lead
  makes it unambiguous which approach the tag refers to (today's placeholder allowed
  either order).
- **New refine entries with no prohibition at all** (positive conventions) are now
  written `[PATTERN]`. They are still retrieved, and `commands/dark-factory-plan.md:88-90`
  already tells the planner to bake `[PATTERN]` lessons into plan steps, but they are no
  longer in the architect's `[AVOID]`/`[FIX]` scan or under the two AVOID sentences.
  That is the fix #38 asks for: running an anti-pattern scan over a "do X" lesson is the
  inversion being reported. Nothing with a "don't" in it leaves the scan, because the
  rule in Requirement 2 keeps every such lesson `[AVOID]`.
- **Existing entries: no change** (Requirement 4).

Whether the architect prompt should also flag plans that contradict a `[PATTERN]` entry
is a reviewer-prompt change with its own blast radius and is left to a separate ticket.

## Alternatives Considered

1. **One combined entry tagged `[PATTERN]`, chosen approach first** (the first corrected
   draft of this spec). Rejected: it removes the entry's rejected half from the
   `[AVOID]`/`[FIX]`-only memory-violation scan and from the refine/plan AVOID rules,
   needed a compensating clause at `dark-factory-refine.md:94` that plan and the
   architect would not see, and required hand-retagging existing memory lines as an
   exception to `dark-factory-implement.md:388-392`.
2. **Split each decision into two entries** (`pattern` for the chosen approach, `avoid`
   for the rejected one). Rejected: `.archon/memory/architecture.md` is at the 30-entry
   R4 cap (33 on main; this refine pass's write evicted four), so doubling refine's
   per-decision write volume accelerates eviction of other lessons; it splits one
   dedup/reinforcement key into two that can drift; and it separates the "chosen
   *because* rejected" reasoning.
3. **Extend the scan clauses to `[PATTERN]`** (`architect-prompt.md:36`,
   `dark-factory-plan.md:86`, `dark-factory-refine.md:94`). Rejected for this ticket: it
   changes plan-review behaviour for every existing `[PATTERN]` entry (23 in
   `dark-factory-ops.md` alone), far beyond a `size: S` tagging fix.
4. **Pass `--kind avoid` explicitly in `gate_lib.sh`, or add a `KIND` parameter to
   `write_memory_entry()`.** Rejected: the default already yields `[AVOID]`, the two
   files cannot version-skew, and `gate_lib.sh` also holds `emit_verdict`; a gate library
   is not edited for a no-op. A gate that one day needs `[FIX]` gets its own ticket.
5. **Make `--kind` required with no default.** Rejected: it would force the `gate_lib.sh`
   edit above, and a target repo's lagging mirrored command file calling a newer script
   would hard-fail instead of degrading to today's behaviour.
6. **Delete or invalidate the #652 entry, or retag old entries by hand or by script.**
   Rejected: see Requirement 4.

## Open Questions (non-blocking)

- Whether `refinement-skills/architect-prompt.md` should check plans against `[PATTERN]`
  entries (see "Reader behaviour").
- The same double-quoted-text exposure exists wherever an agent fills `$VIOLATION_TEXT` /
  `$FINDING_TEXT` for the gate write path; not addressed here.
- Many other command snippets use the `\  # TARGET-PATH` form that is not a real line
  continuation; only the §7a block is fixed here.

## Assumptions

- The avoid-vs-pattern choice (Requirement 2) is made by the refine agent at write time
  from a mechanical question — does the lesson contain a prohibition or a rejected
  alternative? — the same way it already picks the memory file and `--path-prefix`.
- This refine pass's own two #38 memory entries on the branch record the superseded
  design (chosen-first, `[PATTERN]` for combined entries, `--kind avoid` inside
  `gate_lib.sh`). They are corrected on the refine branch by the operator together with
  this spec, before approval; the implementer does not edit them.
- `jq` is available in the run container (already used at
  `commands/dark-factory-refine.md:83`).

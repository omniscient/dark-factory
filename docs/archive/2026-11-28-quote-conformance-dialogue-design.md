# Quote checker dialogue so a `STATUS:` line in reviewer prose can't flip `run_record` or the report node

**Issue:** #405

## Overview / Problem statement

Three gate commands append raw reviewer prose, at column 0, below the verdict header of a
verdict artifact:

| Artifact | Writer (origin/main) | Prose source |
|---|---|---|
| `$ARTIFACTS_DIR/conformance.md` | `commands/dark-factory-conformance.md:591`, `:601` (Phase 4 PASS), `:743`, `:753` (Phase 5 BLOCKED) | `$CONFORMANCE_DIALOGUE`, `$SHADOW_DIALOGUE` |
| `$ARTIFACTS_DIR/review.md` | `commands/dark-factory-code-review.md:243` (PASS), `:282` (BLOCKED) | `cat "$ARTIFACTS_DIR/review_findings.md"` |
| `$ARTIFACTS_DIR/docs-conformance.md` | `commands/dark-factory-docs-conformance.md:241` | `cat "$REVIEW"` |

Opus and Fable both echo the verdict schema back in prose. A dialogue line that starts with
`STATUS:` (or `CYCLES:`, `VERDICT:`, `BLOCKERS:`, `ADVISORY:`, `NO_SPEC:`, `OOS_EXCISED:`,
`OOS_TICKETS:`) is then indistinguishable, to a line scanner, from the real header.

Readers of those artifacts, and which are exposed:

| Reader | Semantics | Exposed? |
|---|---|---|
| `scripts/verdict_gate_check.sh:38` (conformance-gate, review-gate) | `grep -m1 '^STATUS:'` — first match | No. `emit_verdict` writes the header first. |
| `scripts/docs_verdict_gate_check.sh:52` | `grep -m1` | No. |
| `scripts/factory_core/verdict.py:32-44` `parse_verdict`, called by `run_record.py:786` (`_parse_artifact_stage`) for `conformance` and `review` | no `break` — last `STATUS:` wins; `run_record.py:791-796` (`CYCLES:`) and `:804-814` (`BLOCKERS:`/`ADVISORY:`) are last-wins too | **Yes.** Feeds `_compute_outcome` (`run_record.py:845-880`): state `blocked` vs `delivered_*`, and the cycle/advisory score penalties. |
| `report` node, `workflows/archon-dark-factory.yaml:1658-1665` (conformance.md) and `:1709-1711` (review.md) | `grep '^FIELD:'` with no `-m1` | **Yes.** Two matches make a two-line value, the `case` falls to `*)` and the section is silently dropped. |

Verified by execution against origin/main code: a BLOCKED `conformance.md` whose raw dialogue
contains `STATUS: PASS` / `CYCLES: 7` is recorded by `_parse_artifact_stage` as
`verdict=PASS, cycles=7`, while the gate still reads `BLOCKED`.

`docs-conformance.md` is not read by `run_record` (`artifact_names`, `run_record.py:1159`)
nor by the report node; every reader of it is `-m1`. It is out of scope (see Non-goals).

`review.md` has the same exposure as `conformance.md` but is **out of scope here**: it is the
one writer that `cat`s a file raw, so quoting it changes what `review-gate` reads for one
input class (a NUL byte). That is a gate decision for its own ticket (see Non-goals). This
spec fixes `conformance.md` and the plan-side comments only.

**This spec makes no change to `scripts/verdict_gate_check.sh`, `scripts/gate_lib.sh`,
`scripts/factory_core/verdict.py`, `scripts/factory_core/run_record.py`,
`workflows/archon-dark-factory.yaml` or `commands/dark-factory-code-review.md`.**

## Requirements

### R1. Scope: seven embed sites in two command files

| # | File | Site | Embeds | Sink |
|---|---|---|---|---|
| 1 | `dark-factory-conformance.md` | Phase 4 (PASS) artifact write | conformance dialogue | `conformance.md` (run_record, report node) |
| 2 | `dark-factory-conformance.md` | Phase 4 (PASS) artifact write | shadow dialogue | same file |
| 3 | `dark-factory-conformance.md` | Phase 5 step 4 (BLOCKED) artifact write | conformance dialogue | same file |
| 4 | `dark-factory-conformance.md` | Phase 5 step 4 (BLOCKED) artifact write | shadow dialogue | same file |
| 5 | `dark-factory-conformance.md` | Phase 5 step 1 "Spec Conformance — Blocked" comment | both | issue comment (no parsing consumer today) |
| 6 | `dark-factory-plan.md` | Phase 3.5 step 8b "Blocked (Plan)" comment | both | issue comment (no parsing consumer today) |
| 7 | `dark-factory-plan.md` | Phase 4 publish comment, "Spec Conformance" section | both | issue comment (no parsing consumer today) |

Sites 1-4 are the parsing-vulnerability fix. Sites 5-7 are consistency and defence in
depth; the issue names site 7 explicitly. The issue's literal text names sites 1-2 and 7;
3-4 are in scope because the issue's stated goal (no `STATUS:` line in conformance reviewer
prose can flip `run_record` or the report node) is not met without them. Of the seven, only
sites 1-4 write a file any gate reads (`conformance.md`, `conformance-gate`); sites 5-7 are
issue comments, which no gate reads.

On main every one of the seven takes the dialogue from a bash variable (`"${CONFORMANCE_DIALOGUE}"`
/ `"${SHADOW_DIALOGUE}"` in `printf` or `--body` arguments at sites 1-5; agent-composed
comment text at sites 6-7). None of them `cat`s a file into the output.

### R2. Quote at the embed, from a file, with one standalone script

Add a new file `scripts/quote_dialogue.py`. Do **not** add the helper to `scripts/gate_lib.sh`:
that file holds `emit_verdict`, is sourced by every gate command, and is classed with the gate
scripts (`scripts/gate_` prefix, `scripts/factory_core/mechanism_candidate.py:114`). A
standalone script leaves it byte-identical and does not depend on a shell function surviving
into the Bash call that writes the artifact.

```python
#!/usr/bin/env python3
"""Blockquote reviewer prose so no line of it can be read as a verdict header field.

Usage: quote_dialogue.py [FILE]   (stdin when FILE is omitted)

Lines are split with str.splitlines(), the splitter factory_core/verdict.py::parse_verdict
uses, so the "> " prefix lands after every boundary that reader recognises (LF, CRLF, lone
CR, VT, FF, 0x1c-0x1e, NEL, U+2028, U+2029), not only after LF. NUL bytes are dropped (bash
drops them from a variable today; one NUL makes GNU grep treat the whole artifact as
binary). Never prints an unquoted line and always exits 0: a missing or unreadable FILE
prints a quoted placeholder.
"""
import sys


def quote(data: bytes) -> bytes:
    text = data.decode("utf-8", "replace").replace("\x00", "")
    out = "".join("> " + line + "\n" for line in text.splitlines()) or "> (empty)\n"
    return out.encode("utf-8")


def main(argv: list) -> int:
    try:
        if len(argv) > 1:
            with open(argv[1], "rb") as fh:
                data = fh.read()
        else:
            data = sys.stdin.buffer.read()
        out = quote(data)
    except Exception:
        out = b"> (no reviewer output was recorded)\n"
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Why not `printf '%s\n' "$1" | sed 's/^/> /'`: `sed` splits on `\n` only, but
`parse_verdict` uses `str.splitlines()` and `run_record.py:1190` reads with universal
newlines. Verified by execution: with the `sed` helper, a dialogue containing
`prose<CR>STATUS: PASS`, `prose<FF>STATUS: PASS`, `prose<VT>STATUS: PASS` or
`prose<U+2028>STATUS: PASS` still flips `_parse_artifact_stage` from `BLOCKED` to `PASS`.
With the script above all of those, plus CRLF, NEL, U+2029, `\x1c`, a NUL byte, invalid
UTF-8, an empty file, a missing file and a 2 MB input, leave exactly one `^STATUS:` line, the
header verdict, and first four lines byte-identical to `emit_verdict`'s output.

Why stdin from a file, not a shell variable: shell state does not persist across Bash tool
calls (`dark-factory-plan.md:190-193` says so), so `"${CONFORMANCE_DIALOGUE}"` in a write
block is only ever what the agent re-typed into that Bash call as an assignment. Reviewer
text containing `` ` ``, `$( )`, `\` or `%` then passes through agent-composed shell quoting.
`dark-factory-docs-conformance.md:197-213` and `dark-factory-code-review.md:192` already use
the safe pattern: the Write tool saves the reviewer response verbatim to a file and one Bash
call reads only files. This spec adopts it for the two conformance dialogues:

- `dark-factory-conformance.md` and `dark-factory-plan.md`: wherever the command says
  "append ... to `CONFORMANCE_DIALOGUE`" / "`SHADOW_DIALOGUE`" (conformance Step 3.1 steps
  5/5a, Phase 3.5 steps 7/7a; plan Phase 3.5 steps 6/6a, 8f/8f2), add: also save the full
  accumulated text, verbatim, with the Write tool to `$ARTIFACTS_DIR/conformance_dialogue.md`
  / `$ARTIFACTS_DIR/shadow_dialogue.md` (overwrite with the whole accumulated dialogue each
  time). Never write these two files with `echo`, `printf` or a here-document.
- The `CONFORMANCE_DIALOGUE` / `SHADOW_DIALOGUE` working values stay raw and keep every
  current use: conformance Phase 3.6's `[OOS]` scan (`:402-419`), the Verdict-line parse, and
  Phase 4's memory-write extraction. Only the embed sites change.

### R3. Call-site changes

Every embed becomes this one line, written `QUOTE FILE` below (run from the repo root, like
the `python3 dark-factory/scripts/factory_core/cli.py marker factory  # TARGET-PATH` call
Phase 4 already makes):

```bash
python3 dark-factory/scripts/quote_dialogue.py "FILE" || printf '> (dialogue could not be quoted)\n'  # TARGET-PATH
```

The script handles a missing, empty or unreadable file itself; the `||` arm covers a missing
script or interpreter. Verified by execution for each of those five cases: the header is
intact, `grep -c '^STATUS:'` is 1 and the gate's read is the header value. No write block may
test for, or wait on, a dialogue file before `emit_verdict`: if the agent never saved the
file, or saved an older cycle's text, the artifact still gets its header and a quoted body.

- Sites 1, 3: `printf "\n---\n\n%s\n" "${CONFORMANCE_DIALOGUE}"` becomes
  `printf "\n---\n\n"` followed by `QUOTE "$ARTIFACTS_DIR/conformance_dialogue.md"`.
- Sites 2, 4: `printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"`
  becomes `printf "\n---\n\n## Shadow (Fable) Review\n\n"` followed by
  `QUOTE "$ARTIFACTS_DIR/shadow_dialogue.md"`.
- Site 5: build the comment body in a temp file and post it with `--body-file` (the pattern
  Phase 4 already uses at `:619-623`), replacing the `$CONFORMANCE_DIALOGUE` /
  `$SHADOW_DIALOGUE` interpolations with `QUOTE` output. The first line of the body
  stays exactly `## Spec Conformance — Blocked` (`scripts/comment_digest.py:102` matches it)
  and all other body text is unchanged. The block needs no `source`.
- Sites 6, 7 (plan): these comments are composed by the agent from a template, not by a
  bash block. Add one self-contained Bash call before composing each: it writes
  `QUOTE` output for each dialogue to `$ARTIFACTS_DIR/conformance_dialogue.quoted.md`
  / `shadow_dialogue.quoted.md`. The template then says to paste those files' contents
  verbatim in place of `$CONFORMANCE_DIALOGUE` / `<full $SHADOW_DIALOGUE ...>`. Do not add a
  `source` line to the plan command.
- The `SHADOW_VERDICT_LINE` and fenced `SHADOW_*` lines in the comments are unchanged.

Invariants every edited block must keep:

1. The `emit_verdict ...` line stays the first statement of each artifact write, byte for
   byte, and the header `printf` lines after it are unchanged. `QUOTE` runs after
   them inside the same `{ ...; } >` group, so a script failure cannot suppress, reorder or
   alter the header.
2. #406: the three-line shadow-model re-resolution snippet (`ROOT=` / `SHADOW_MODEL_DEFAULT=`
   / `SHADOW_MODEL_PIN=`) stays byte-identical at all five sites, contiguous, and before
   its `if [ -n "${SHADOW_MODEL_PIN:-}" ]; then` guard in the same block. Insert nothing
   between those three lines. `tests/test_shadow_model_resolution.py` must pass unmodified
   (4 conformance snippets + 1 plan snippet, 3 guarded conformance blocks).

### R4. (removed) The report node is not edited

The first review added `-m1` to the report node's greps. That edit is observer-only (the
`report` node is terminal, `depends_on: [status-in-review, code-review]`, and only posts the
run comment), but it is redundant once R1-R3 hold, and `workflows/archon-dark-factory.yaml`
is the file that carries `conformance-gate` and `review-gate` (`^workflows/` is a
`critical_diff_paths` entry in `.factory/adapter.yaml`). It is left to a follow-up ticket.
R6 instead pins that the report node's expressions, as they are on main, read the header.

### R5. Gate safety (must hold; state it in the plan and the PR body)

- `scripts/verdict_gate_check.sh` is not edited. Its decision is a function of the first
  `^STATUS:` line only. That line is written by `emit_verdict` before any dialogue byte, and
  R3 invariant 1 keeps it byte-identical. Therefore the gate reads the header for every
  dialogue.
- **Zero gate-visible delta (requirement).** For every dialogue, `conformance-gate` must
  decide exactly as it does on main. The only way dialogue bytes can affect
  `verdict_gate_check.sh:38` is a NUL: GNU grep then treats the file as binary and the gate
  gets no usable `STATUS`. On main a NUL can never reach `conformance.md`, because the
  dialogue passes through a bash variable and bash drops NUL bytes (from an assignment and
  from `$(cat FILE)` alike). `quote_dialogue.py` drops NUL too, so the gate sees the same
  thing. Verified by execution: main's Phase 4 and Phase 5 step 4 blocks against the
  proposed ones, shadow pin set, 24 dialogues (11 separators x 2 header values, plus two
  with NUL inside a line): 48 artifact pairs, the gate's `grep -m1 '^STATUS:'` line and all
  bytes up to the first `---` identical in every pair, no NUL in any artifact on either side.
  Do not keep NUL in the script's output: reading the dialogue from a file would then add a
  false block that main does not have.
- `verdict.py::parse_verdict` is not edited. It also decides `verifier.py:77-91` (whose
  output feeds `handoff.py:471-479`); switching it to first-wins would turn a verifier stdout
  of `STATUS: PASS ... STATUS: BLOCKED` from BLOCKED into PASS. Last-wins has the mirror
  weakness (see Non-goals), so neither order is the safe one and any change there is a gate
  change for its own ticket. That is why the fix is on the write side.
- `run_record.py` is not edited. Its recorded verdict changes only for artifacts that
  contained a prose `STATUS:` line, and only towards the header (the gate's) value.

### R6. Tests (pytest only; no new `tests/*.sh`)

A new shell test would need either a `.github/workflows/ci.yml` step or an entry in
`tests/test_ci_covers_shell_tests.py::CI_EXEMPT_SHELL_TESTS` (the pattern the first draft
cited, `tests/test_conformance_memory_write.sh`, is itself CI-exempt, i.e. not run). Use
pytest, with the POSIX/bash skip `tests/test_shadow_model_resolution.py::_resolve` uses.

New `tests/test_quote_dialogue.py`:

1. Script (imported and called directly, so it also runs without bash): for each of LF,
   CRLF, lone CR, VT, FF, `\x1c`, NEL, U+2028, U+2029, a fenced block, a line already
   starting `> `, text with `` ` ``, `$(id)`, `%s`, `\n` literals, a NUL byte, invalid UTF-8
   and empty input, every `str.splitlines()` line of the output starts with `> `, the output
   contains no NUL, and shell metacharacters come out literally. A missing file path prints
   the placeholder and exits 0.
2. End to end: extract the artifact-write bash from conformance Phase 4 and conformance
   Phase 5 step 4, run each with dialogue files containing
   `STATUS: <opposite>`, `CYCLES: 9`, `VERDICT: X` behind each
   separator from (1), and assert: the first four lines equal `emit_verdict`'s output;
   `grep -c '^STATUS:'` is 1; the gate's own expression
   (`grep -m1 '^STATUS:' FILE | awk '{print $2}'`, `verdict_gate_check.sh:38`) equals the
   header; the report node's expressions as on main (`grep '^FIELD:' FILE | cut -d' ' -f2`
   for `STATUS`, `VERDICT`, `CYCLES`) each return one line, the header
   value; `run_record._parse_artifact_stage` returns the header verdict and cycles.
   The conformance blocks must run with the shadow pin non-empty
   (`CONFORMANCE_SHADOW_MODEL=claude-fable-5-1`) so sites 2 and 4 are exercised, and the
   test asserts the `## Shadow (Fable) Review` section is present; the tracker and footer
   calls are stubbed (no network call may run). Also run each block once with the dialogue
   files absent and once with `dark-factory/scripts/quote_dialogue.py` absent: same header
   assertions, quoted placeholder body.
3. Static: in the two command files no artifact or comment write interpolates
   `${CONFORMANCE_DIALOGUE}`, `$CONFORMANCE_DIALOGUE`, `${SHADOW_DIALOGUE}` or
   `$SHADOW_DIALOGUE`.
4. Gate differential (pins R5's zero-delta requirement): for each input from (1), including
   NUL at a line start, inside a line and inside the word `STATUS`, write the artifact twice:
   once with the extracted block from (2), once with a reference writer kept in the test
   that reproduces main (`printf "\n---\n\n%s\n" "$(cat FILE)"` for each dialogue, same
   `emit_verdict` and header `printf` lines). Assert the output of
   `grep -m1 '^STATUS:' FILE | awk '{print $2}'` and the bytes up to the first `---` line are
   identical between the two, for both PASS and BLOCKED headers, and that neither file
   contains a NUL.

`tests/fixtures/verdicts/`: add `conformance__pass_quoted_dialogue` and
`conformance__blocked_quoted_dialogue` (`.md` + `.expected.json`), each with quoted hostile
prose, and change `tests/test_verdict.py::test_golden_corpus_byte_compat`'s count from 19
to 21.

Must stay green unmodified: `tests/test_shadow_model_resolution.py`,
`tests/test_run_record.py` (including
`test_parse_artifact_conformance_keeps_last_status_line_wins`),
`tests/test_verdict_gate_check.sh`, `tests/test_verdict_gate_dag.py`,
`tests/test_conformance_command_artifact_guard.py`, `tests/test_report_code_review_section.py`,
`tests/test_code_review_command.py`. `tests/test_conformance_command_shadow_review.py` and
`tests/test_plan_command_shadow_conformance.py` may need their string pins updated; if so the
plan lists each changed assertion.

### R7. Docs

- `refinement-skills/VERIFIER-CONTRACT.md`: one paragraph stating that reviewer prose is
  embedded in `conformance.md` or a conformance comment only through
  `scripts/quote_dialogue.py`, and that `review.md` does not do this yet.
- Line citations: the edits shift lines in both command files and nothing checks more
  than range. After editing, re-point every `commands/dark-factory-conformance.md:N`,
  and `commands/dark-factory-plan.md:N` citation
  at or below the first edited line in
  `docs/` (outside `docs/archive/`). Known today: `docs/run-lifecycle.md:819` and `:1319`
  (`conformance.md:748-752`, already stale, the text is at `:758-762`), `:1394`
  (`:587-589`, text is at `:588-590`), `:710`
  (`plan.md:240-268`), `docs/configuration.md:129,132,772` (`plan.md:298`, `:299`). #409
  (PR #658) re-points several of these; take the list from the tree the plan is written on.

## Acceptance criteria

1. No line of reviewer prose in `conformance.md` starts at column 0, under
   either `\n` splitting or `str.splitlines()` (R6 tests 1-2).
2. For every hostile input in R6, the gate's read (`verdict_gate_check.sh:38`'s expression),
   `run_record._parse_artifact_stage` and the report node's unmodified reads agree, and equal
   the value passed to `emit_verdict`.
3. `git diff origin/main -- scripts/verdict_gate_check.sh scripts/docs_verdict_gate_check.sh scripts/gate_lib.sh scripts/factory_core/ workflows/ commands/dark-factory-code-review.md commands/dark-factory-docs-conformance.md`
   is empty.
4. No change to `.github/workflows/ci.yml`, `deploy/**` or `tests/*.sh`.
5. `python -m pytest tests/ -v` and the workflow DAG checks pass.
6. Zero gate-visible delta: R6 test 4 passes, i.e. for every corpus dialogue (NUL included)
   `conformance-gate`'s read of the new artifact is identical to its read of the artifact
   main would have written.

## Blast radius and sequencing

- This change touches one gate command (`dark-factory-conformance.md`), the plan command and
  adds one script. If the blast-radius gate holds
  it for a human, that is intended; do not shrink the change to avoid the hold.
- No dependency on #238 or #652: `run_record.py` is not edited.
- Order: #409, then #404, then this ticket. The issue body carries `Depends on: #404`
  (#404 already carries `Depends on: #409`).
  - #409 (PR #658, open) is not confined to the text before Phase 3.5: it inserts lines
    directly under Phase 3.5 step 8b's "If `CONFORMANCE_CYCLE > MAX_CYCLES`" (site 6),
    renumbers Phase 4's steps (the publish comment, site 7, moves from step 6 to step 7) and
    re-points `docs/run-lifecycle.md` / `docs/configuration.md` citations that R7 also edits.
  - #404 replaces the inlined tracker-comment sequence in the same fenced block as sites 1-2.
    It is small and already planned; landing it first means R6 test 2 stubs the call that
    will actually be there.
  - Write this ticket's plan on a main that contains both. All anchors here are content based
    (the `printf` strings and step names), not line numbers.

## Alternatives considered

1. **`sed 's/^/> /'` on a shell variable, helper in `gate_lib.sh`** (first draft). Rejected: misses every
   `splitlines()` boundary other than `\n` (reproduced), and depends on agent-composed shell
   quoting of reviewer text.
2. **Fence the dialogue.** Rejected: a reviewer's own fenced block closes it early.
3. **Make `parse_verdict` first-wins.** Rejected: it also gates handoff and verifier output,
   where first-wins is weaker (R5).
4. **Bound `_parse_artifact_stage` to the text above the first `---`.** Sound and
   gate-aligned, but `run_record.py` is contended (#238, #652). Left as an optional follow-up.
5. **A `quote_dialogue` shell function in `gate_lib.sh`** (first review). Works, but edits the
   gate library for no gain over a standalone script (R2).
6. **`-m1` on the report node's greps** (first review's R4). Observer-only and harmless, but
   redundant here and it edits the gate-carrying workflow file. Follow-up ticket.

## Non-goals

- `docs-conformance.md` (`dark-factory-docs-conformance.md:241`): no last-wins or un-`-m1`
  reader exists. Its Phase 6 `grep -m1 '^REASON:'` can pick up a prose `REASON:` line when
  the header has none (cosmetic, comment text only). Follow-up ticket, not this one.
- `review.md` (`commands/dark-factory-code-review.md:243`, `:282`, `cat review_findings.md`):
  same observer exposure (`run_record` last-wins for `STATUS:`, `BLOCKERS:`, `ADVISORY:`; the
  report node). Not fixed here: unlike the conformance sites it copies a file raw, so a NUL
  in the findings makes `review-gate` block today, and quoting it would make the gate read
  the header instead. Gate ticket of its own, owner's decision.
- `verifier.py:77` (`normalize_verdict`) is last-wins on a target verifier's raw stdout:
  `STATUS: BLOCKED ... STATUS: PASS` with exit 0 normalizes to PASS (verified by execution).
  That reader decides (handoff intake), unlike the observers this spec protects. It is not
  reachable from reviewer prose in the ticket pipeline. Gate ticket of its own; nothing here
  touches it.
- The report node's un-`-m1` greps (R4).
- The 65,536-character GitHub comment limit for long dialogues (pre-existing).
- No `.archon/memory/*` change.

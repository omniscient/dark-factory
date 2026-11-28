# `report` node: read verdict header fields with `grep -m1`

**Issue:** #661

## Overview / Problem Statement

The `report` DAG node (`workflows/archon-dark-factory.yaml:1625-1800`, the last node in
the file, `depends_on: [status-in-review, code-review]`) parses header fields out of two
upstream verdict artifacts using plain `grep '^FIELD:' <file> | cut`:

- `conformance.md` — `STATUS`, `VERDICT`, `CYCLES`, `NO_SPEC`, `OOS_EXCISED`,
  `OOS_TICKETS` (6 call sites, lines 1658/1661/1662/1663/1664/1665).
- `review.md` — `STATUS`, `BLOCKERS`, `ADVISORY` (3 call sites, lines
  1709/1710/1711).

None of these 9 call sites pass `-m1`. If either artifact contains a second column-0
match for one of these field names, `grep` returns both lines and the shell variable
becomes multi-line. Two things then go wrong:

- A duplicated `STATUS:` matches no explicit `case` branch, falls through to `*)`, and
  the whole section (`### Spec Conformance` or `### Code Review`) is silently dropped
  from the posted run comment.
- A duplicated `BLOCKERS:`/`ADVISORY:` (or any of the five other `conformance.md`
  fields) is printed with an embedded newline, e.g. `⛔ Blocked — 2⏎7 blocking
  finding(s)`.

The gate for the same artifact, `scripts/verdict_gate_check.sh:38`, already does
`grep -m1 '^STATUS:' | awk '{print $2}'` and acts on the first match only. The report can
therefore describe a run differently than the gate that decided it.

#405 (merged) removed the one known cause of a duplicated line in `conformance.md` on the
write side (the dialogue is blockquoted, `commands/dark-factory-conformance.md:595-608`).
`review.md` still appends `review_findings.md` raw after the header
(`commands/dark-factory-code-review.md:236-244` and `:275-283`); hardening that writer is
#659, a gate ticket parked for the owner. This ticket is the read-side counterpart, for
the `report` node only. It does not depend on #659 and #659 does not depend on it.

## Why first-match is the right read

In every writer the header is the first thing in the file, written before any free text:

- `conformance.md` PASS: `emit_verdict` then `VERDICT/CYCLES/NO_SPEC/OOS_EXCISED/
  OOS_TICKETS`, then `---`, then the quoted dialogue
  (`commands/dark-factory-conformance.md:600-608`). BLOCKED: `emit_verdict`,
  `VERDICT/CYCLES`, `---`, dialogue (`:755-760`). The appended shadow block uses
  `SHADOW_*` names, which `^STATUS:` does not match.
- `review.md`: every write starts with `STATUS:` (via `emit_verdict`,
  `scripts/gate_lib.sh:55-60`, or a literal `printf`), followed by `BLOCKERS:` and
  `ADVISORY:` where present, before `---` and the raw findings
  (`commands/dark-factory-code-review.md:54,72,136,194,217,236-244,275-283,363-370`).
  The zero-reviewable-content write (`:363-370`) is header only: `STATUS: BLOCKED`,
  `BLOCKERS: 0`, `ADVISORY: 0`, `REASON:`.

No writer legitimately repeats one of the nine fields, and no writer puts the header
anywhere but first. The first match is therefore always the header, which is also what
the gate reads.

## Requirements

1. Add `-m1` to the 9 in-scope `grep '^FIELD:'` call sites inside the `report` node (the
   6 `conformance.md` reads + the 3 `review.md` reads listed above). For an artifact with
   a single line per field, the posted comment is byte-identical to today's; `-m1` only
   changes behaviour when a field line is duplicated.
2. Touch nothing else in `workflows/archon-dark-factory.yaml`: not the `blast.md` reads
   (2 call sites, lines 1689-1690) in this same node, not the `conflict_resolution.md`
   reads (4 call sites, lines 1750-1754) in this same node, not the node's `depends_on`,
   `trigger_rule`, `when` or `timeout`, and no other node. Each of the 9 edits inserts
   ` -m1` inside an existing line: the file stays at 1800 lines, so no `file:line`
   citation of the workflow in `docs/` shifts and no doc edit is needed.
3. Confinement is asserted structurally, not by eye (see Test design, T5).
4. Add a behavioural (executing) test proving the fix (see Test design, T1-T4).
5. `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml` and
   `python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml` both pass,
   and `python -m pytest tests/ -v` passes. No `.github/workflows/ci.yml` edit.
6. The PR changes exactly two files: `workflows/archon-dark-factory.yaml` and the new
   test file (plus the factory's own spec/plan archive and memory commits).

## Gate safety (verified at origin/main 378b426)

- No node lists `report` in `depends_on`, and no node references `$report.output`. It is
  the last of the 49 nodes.
- The node's only side effect is one `tracker add-comment` call
  (`workflows/archon-dark-factory.yaml:1762`). It sets no label, status or board column
  and writes no artifact. Nothing in `scripts/`, `scheduler.sh` or `entrypoint.sh` parses
  the `## Dark Factory Run — …` comment or its `### Spec Conformance` / `### Code Review`
  sections.
- The gates read the artifacts themselves, not the report:
  `scripts/verdict_gate_check.sh:38` for `conformance.md` and `review.md`.
- Exit status: the nine values are assigned as `VAR=$(grep … | cut …)`. `grep` reads the
  file directly (there is no upstream `cat` to receive a SIGPIPE) and `cut` reads its
  input to the end, so `-m1` introduces no SIGPIPE. `grep -m1` exits 0 on a match and 1
  on no match, exactly as plain `grep` does, so the pipeline status for a present field
  and for a MISSING field is unchanged under any combination of `errexit`/`pipefail`. The
  node sets neither option itself.

**Hold expectation.** `workflows/` is in `.factory/adapter.yaml`'s
`safety.critical_diff_paths` (`:34`), so the blast-radius gate will hold this PR for a
human. That hold is expected and correct; the implement phase must not try to avoid it.

## Why out of scope (confirmed, not re-litigated here)

- `scripts/verdict_gate_check.sh` and every other gate script — the gate already uses
  `-m1`; it is the reference behaviour this ticket mirrors, not a target of the edit.
- `scripts/factory_core/run_record.py` — it scores `review.md` through
  `verdict.parse_verdict` and its own `BLOCKERS:`/`ADVISORY:` loop (`:803-813`). It is not
  edited here. (Observation only, for #659's owner: that loop has no `break`, so it keeps
  the LAST match, which differs from the gate's first match.)
- The `report` node's `blast.md` reads (`BLAST_STATUS`, `BLAST_TRIGGER`) — `blast.md` is
  written only by the deterministic checker: `scripts/gate_blast_radius.py:587-604`
  prints the header itself and `scripts/blast_gate_check.sh:185` copies it to `--out`
  (`:325` writes a fail-closed stub). It never contains prose. The issue explicitly
  excludes it.
- The `report` node's `conflict_resolution.md` reads — a different `KEY=VALUE` format
  (`cut -d=`), not mentioned by the issue.
- CRLF artifacts. `-m1` does not change how a `\r` is handled: on Linux `cut -d' ' -f2`
  yields `PASS\r`, which matches no branch, before and after this change (the gate's
  `awk '{print $2}'` keeps the `\r` too). No writer emits CRLF. The test pins "unchanged"
  for this case (T4); it is not fixed here.

## Architecture / Approach

Mechanical, line-local edit: for each of the 9 call sites, change
`grep '^FIELD:' "$ARTIFACTS_DIR/<artifact>"` to `grep -m1 '^FIELD:'
"$ARTIFACTS_DIR/<artifact>"`. No other part of any line changes (the trailing
`| cut -d... -f...` stays as-is). Concretely, in the `report` node's `bash:` block:

```
# conformance.md (6 sites)
CONFORMANCE_STATUS=$(grep -m1 '^STATUS:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
...
CONFORMANCE_VERDICT=$(grep -m1 '^VERDICT:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
CONFORMANCE_CYCLES=$(grep -m1 '^CYCLES:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
NO_SPEC=$(grep -m1 '^NO_SPEC:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
OOS_EXCISED=$(grep -m1 '^OOS_EXCISED:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
OOS_TICKETS=$(grep -m1 '^OOS_TICKETS:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2-)

# review.md (3 sites)
CR_STATUS=$(grep -m1 '^STATUS:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
CR_BLOCKERS=$(grep -m1 '^BLOCKERS:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
CR_ADVISORY=$(grep -m1 '^ADVISORY:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
```

### Test design

Add `tests/test_report_node_header_reads_exec.py`, following the execution-test precedent
for a DAG node's embedded bash (`tests/test_push_gate_dag_exec.py`, #473) rather than the
static-assertion precedent (`tests/test_report_code_review_section.py`, which only checks
that three substrings exist in the workflow text), because the acceptance criterion is
behavioural.

As a `tests/*.py` file it is auto-discovered by `python -m pytest tests/ -v` with no
`.github/workflows/ci.yml` edit.

Harness (mirrors `test_push_gate_dag_exec.py`'s fixture/stub pattern):

- `pytestmark = pytest.mark.skipif(...)` guarding on a POSIX host with `bash`, `git`,
  `jq` available. T5 and T6 below are pure Python and must NOT be under that skip.
- Take the `report` node's `bash:` text from the parsed YAML (`yaml.safe_load`), the same
  `_node_bash(node_id)` helper shape as the precedent. The text contains the Archon
  placeholder `$parse-intent.output.intent`; replace it with the literal `new` before
  running, and do not run under `set -u`.
- Build a temp `ARTIFACTS_DIR` with `issue.json` (`resolved_number`, no `epic_number`)
  and the case's `conformance.md` / `review.md`.
- Run with `bash -c` inside a throwaway git checkout on a named branch (the node calls
  `git branch --show-current`).
- Stub `python3` as a shell function prepended to the same `bash -c` string: `codehost
  find-change` prints nothing; `tracker add-comment` writes `"$*"` (which carries the
  inline `--body=…`) to a log file; `marker factory` prints a fixed string. No network.
- A helper derives the pre-change script from the real one by replacing `grep -m1 ` with
  `grep ` (the "legacy" script), so cases can compare the two.

Cases:

- **T1 duplicated `STATUS:`.** `conformance.md` = a full PASS header (`STATUS: PASS`,
  `VERDICT:`, `CYCLES:`, `NO_SPEC: false`, `OOS_EXCISED: 0`, `OOS_TICKETS:`), `---`, an
  attestation line, then a column-0 `STATUS: BLOCKED`. `review.md` = `STATUS: PASS`,
  `BLOCKERS: 0`, `ADVISORY: 3`, `---`, then a column-0 `STATUS: BLOCKED`. Assert the
  logged body contains `### Spec Conformance` followed by the attestation line, and
  `### Code Review` with `✅ Passed — 3 advisory finding(s), 0 blocking.` Assert the legacy
  script on the same fixture contains neither heading (proves the test detects the bug).
- **T2 duplicated `BLOCKERS:` / `ADVISORY:`.** `review.md` = `STATUS: BLOCKED`,
  `BLOCKERS: 2`, `ADVISORY: 1`, `---`, then column-0 `BLOCKERS: 7` and `ADVISORY: 9`:
  assert `⛔ Blocked — 2 blocking finding(s).` on one line. Same with `STATUS: PASS` /
  `ADVISORY: 3`: assert `✅ Passed — 3 advisory finding(s), 0 blocking.` on one line.
- **T3 single-header artifacts are byte-identical.** For each of: PASS conformance + PASS
  review; BLOCKED conformance (`STATUS: BLOCKED`, `VERDICT: MATERIAL`, `CYCLES:`) +
  BLOCKED review; SKIPPED both (`STATUS: SKIPPED\nREASON: …`, which has no `BLOCKERS:` /
  `ADVISORY:` line, i.e. the missing-field case); the zero-reviewable-content `review.md`;
  `STATUS: ERROR` review; a file with no `STATUS:` line; and no artifact at all — assert
  the logged body and the exit status of the real script equal those of the legacy
  script. Also assert the expected wording directly for two of them: BLOCKED `review.md`
  renders `⛔ Blocked — 2 blocking finding(s).`, and BLOCKED `conformance.md` renders NO
  `### Spec Conformance` section (the node has no `BLOCKED` branch for conformance; that
  is today's behaviour and stays).
- **T4 CRLF.** The PASS fixtures of T3 written with `\r\n`: assert real == legacy (body
  and exit status). No assertion on what is rendered.
- **T5 confinement (pure Python, structural).** (a) In the `report` node's bash, the set
  of lines containing `grep -m1` is exactly the 9 sites named above, and the `blast.md`
  and `conflict_resolution.md` reads contain no `-m1`. (b) When `git rev-parse
  origin/main` resolves, load the workflow at `origin/main` (`git show`) and at the
  working tree, and assert: the node id lists are equal; every node other than `report`
  is equal (`==` on the parsed dicts); every key of `report` other than `bash` is equal;
  the working-tree `report` bash with `grep -m1 ` replaced by `grep ` equals the
  `origin/main` `report` bash; and both files have the same line count. Skip (b) when
  `origin/main` is not resolvable (CI's depth-1 checkout); it runs in the factory clone
  and locally. The implement and conformance phases must run (b) un-skipped and record
  the result in the PR body.
- **T6 DAG checks (pure Python).** `check_workflow_dag.check(workflow)` returns no errors
  (as `tests/test_verdict_gate_dag.py` does), and `scripts/check_workflow_when.py` run on
  the workflow exits 0.

## Alternatives Considered

1. **`grep -m1` on all 9 call sites (chosen).** Minimal, mirrors the gate's own
   first-match semantics exactly, zero behaviour change for the single-header case.
2. **Pipe through `head -1` instead of adding `-m1`.** Equivalent result, extra process,
   a second idiom for the same problem, and a real SIGPIPE path for `grep`. Rejected.
3. **Replace all header parsing with `scripts/factory_core/verdict.py parse_verdict()`.**
   Would fix the class centrally, but it is a larger, differently-reviewed change to a
   critical-path file and not what the issue's safety argument covers. Follow-up ticket
   at most, not this one.

## Open Questions (non-blocking)

- None. Hardening `review.md`'s writer is #659 and does not block this read-side fix.

## Assumptions

- The line numbers cited are from `main` at 378b426; the fix targets the 9 call sites by
  field name and artifact, not by literal line number.
- How Archon invokes a `bash:` node (with or without `errexit`/`pipefail`) is not
  documented in this repo. It does not matter here: T3/T4 assert real == legacy, and the
  Gate safety section shows `-m1` changes neither the pipeline's exit status nor its
  output for a single or a missing match.

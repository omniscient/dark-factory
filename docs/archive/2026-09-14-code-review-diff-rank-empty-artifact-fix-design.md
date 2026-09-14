# Code Review Gate — fix diff_rank.py over-summarization, the `|`-splitting parser bug, and add a zero-content abort

**Issue:** #403
**Revised:** 2026-09-14 (operator amendment after spec-gate review)

---

## Overview / Problem Statement

On PR #398 (Gate 3, 2026-09-05), `commands/dark-factory-code-review.md` Phase 2 ranked a
7-file, ~2,082-token diff against a 6,000-token cap and produced `review_diff.txt` containing
**seven one-line `[SUMMARIZED]` notices and zero lines of actual code** — a diff that fits
three times over its budget was summarized to nothing. The code-review subagent would have
rubber-stamped an empty review had the orchestrator not noticed and substituted a hand-built
`git diff main...HEAD`.

Two root causes compound the failure, plus one independent bug in the same pipeline:

1. `commands/dark-factory-code-review.md` never passes `--spec-file` to `scripts/diff_rank.py`
   (unlike `commands/dark-factory-conformance.md`, which does), so no file is ever protected
   by the `spec_named` high-tier signal.
2. Independent of (1): `scripts/diff_rank.py::build_ranked_diff()` unconditionally
   summarizes every `low`-tier file with no budget check at all
   (`for c in low: _summary_low(c)`) — even when the whole diff comfortably fits the cap.
   This is the actual mechanism that zeroed out PR #398's diff, and it would still fire even
   with `--spec-file` wired up, for any touched file whose path isn't literally named in the
   spec text.
3. `scripts/code_review_payload.py::parse_findings()` splits a finding bullet's remainder on
   every literal `|`, so a finding description containing `|| true` gets corrupted into
   `|  | true` when fields are rejoined with `" | "`.

## Requirements

**R1 — `diff_rank.py`: never summarize when the whole diff fits the cap; above the cap,
summarize lowest-risk files first.**
In `build_ranked_diff()`, when `raw_diff_tokens <= token_cap` (computed over the *entire*
input diff, not per-tier), skip all tier-based summarization and emit every file — critical,
high, medium, **and low** — in full, in original file order. Passthrough output is built
per-file — `"".join(f["lines"])` for each entry of `parse_diff_files()`'s result, in `files`
order — so leading non-diff lines (e.g. conformance's `[Pre-triage]` annotation, which
`parse_diff_files()` already drops) stay dropped; it is never the raw `diff_text`. The
`# [diff-rank: ...]` header remains the first line (see R2). This is a blanket rule, not
scoped to spec-named files only: the `low` tier's existing unconditional-summarization
behavior (`_summary_low`, no budget check — `scripts/diff_rank.py:507-509`) is itself the
primary bug mechanism from PR #398 and must not be preserved as "always summarize low
regardless of budget."

On the over-cap path (`raw_diff_tokens > token_cap`), the low tier is no longer summarized
unconditionally either: process `low` after `medium` with the same budget check the
`high`/`medium` loops already use (`_full` when `budget >= tokens`, else `_summary_low`).
Because low is processed last, it is the first tier squeezed out under budget pressure —
that is what the issue's "summarize lowest-risk files first" means. Critical still bypasses
the cap; `high` (spec-named first) still fills before `medium` before `low`.

Acceptance test (from the issue): a 7-file/~2k-token diff under a 6k-token cap must pass
through with **no `[SUMMARIZED` substring anywhere in stdout**.

**R2 — `diff_rank.py`: keep classification and `diff-ranking.json` intact under the passthrough.**
Even when R1's shortcut fires, still run `classify_file()` on every file and still write a
complete `diff-ranking.json` (downstream `scripts/context_budget.py:309-314` reads
`raw_diff_tokens` from it for savings accounting; no other consumer on main reads the file).
Each file record's `included` becomes `"full"` with its real `estimated_tokens`; add a
boolean marker (e.g. `"under_cap_passthrough": true`) at the top level of the ranking dict,
distinct from the existing `"diff_enabled": false` marker used by the unrelated
`diff_enabled=False` bypass. Token accounting under the passthrough keeps its existing
per-tier meaning: `critical_tokens` = sum of critical-tier file tokens, `residual_tokens` =
sum of every other file's tokens, `estimated_tokens_emitted` = their sum, `token_cap`
retained. `tests/test_diff_rank.py::test_diff_ranking_json_critical_token_accounting`
(cap 6000, ~180-token fixture — it now runs through the passthrough) asserts
`critical_tokens > 0` and is unaffected only under this rule.

Keep the `# [diff-rank: ...]` header as the first line of stdout in both the passthrough and
normal-ranking paths. This is a **new invariant for the passthrough**: the existing
`diff_enabled=False` bypass (`scripts/diff_rank.py:365-368`) returns raw `diff_text` with no
header, and the passthrough must not copy that shape. `test_header_line_is_first_line`,
`tests/test_context_budget.py::TestDiffRankFeatureDisabled::test_enabled_produces_ranking_header`
(cap 9999, one-line diff — it will exercise the passthrough), and
`commands/dark-factory-conformance.md`'s documented `$FILTER_ANNOTATION`/`$TRIAGED_DIFF`
ordering contract (:173-177) all pin this.

**R3 — `commands/dark-factory-code-review.md`: thread `--spec-file` through Phase 2.**
Resolve `$SPEC_FILE` in Phase 1. The primary lookup is the #390-sanctioned branch-scoped
check the workflow already uses for `budget-conformance` and `push-and-pr`
(`workflows/archon-dark-factory.yaml:417`, `:459`, `:522`):

```bash
SPEC_FILE=$(bash dark-factory/scripts/push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM")  # TARGET-PATH
```

Contract (`scripts/push_gate_check.sh` header comment): arguments `<artifact-prefix>
<issue-number> [<ref>]`, `<ref>` defaulting to `HEAD`; stdout is the path of the first
committed file under the prefix on `origin/main...HEAD` that names the issue (issue number in
the filename, `#N` in the committed content, or a per-commit subject association), or empty;
it always exits 0. At Gate 3 the branch is checked out and already pushed (`push-and-pr`
ran), so the default `HEAD` ref is exactly what this phase has — the same invocation serves
here without a ref argument.

**Correction (2026-09-14, Gate 3 finding on PR #428):** at Gate 3 the spec is no longer under
`docs/superpowers/specs/`. `push-and-pr` (the DAG node before `code-review`) has already archived
it to `docs/archive/` on the branch, so the specs-prefix lookup prints nothing in the real pipeline
and the 2a/2b fallbacks name a path that no longer exists (`_extract_spec_names` then fail-opens to
an empty set). The lookup order is therefore specs prefix, then **archive prefix**
(`push_gate_check.sh "docs/archive/" "$ISSUE_NUM"`), then 2a/2b; and a resolved path that does
not exist is re-pointed at `docs/archive/$(basename "$SPEC_FILE")` when that file exists.

Fallbacks, only when that prints nothing: reuse `commands/dark-factory-conformance.md`'s 2a
("Refinement Pipeline — Plan Generated" comment, :68-72) and 2b (`SPEC_PATH:` line in
`$ARTIFACTS_DIR/refinement-status.md`, :84-85) as conformance already implements them (no
test on main covers those steps). Do **not** copy 2c: its `ls | grep -im1 <first 20 chars of
title>` scan is the same first-match sibling-spec pick #390 removed from the workflow nodes
(`tests/test_push_gate_dag.py:119-140`). If nothing resolves, `SPEC_FILE=""` — no `NO_SPEC`
escalation, since code-review is advisory-only on this signal.

Pass `${SPEC_FILE:+--spec-file "$SPEC_FILE"}` into the existing `diff_rank.py` invocation in
Phase 2 (`commands/dark-factory-code-review.md:67-72`, which currently has no `--spec-file`,
unlike `commands/dark-factory-conformance.md:188`). This remains in scope alongside R1/R2
because it is what protects a spec-named file in the genuinely-over-cap case, which R1 does
not address (R1 only helps when the whole diff fits; above the cap, tier classification still
matters and `spec_named` is still the only signal that promotes a file out of `low`).

Coverage honesty: `spec_named` promotes a file to `high`, and `high` is budget-checked
(`scripts/diff_rank.py:466-473`) — a spec-named file can still be
`budget-exhausted`-summarized once `critical` (cap-exempt) and earlier `high` files have
consumed the budget. The issue's "keep at least the hunks of every file the spec names" is
therefore **not guaranteed above the cap**. This is acceptable because (a) spec-named files
sort first within `high` (`_high_key`, :395-406), so they are the last non-critical files to
be squeezed out; (b) a cap-exempt tier for spec-named files would reopen the unbounded
reviewer prompts `token_optimization.diff.max_review_tokens` exists to prevent; and (c) when
the squeeze leaves zero reviewable content, R5 escalates to a human instead of passing
silently — and when it leaves some, the reviewer still sees the highest-risk files, which
is the ranker's purpose.

**R4 — `code_review_payload.py`: fix the pipe-splitting parser bug.**
In `parse_findings()`, bound the split to `m.group(2).split("|", 2)` (maxsplit=2) instead of
the current unbounded `split("|")`, and remove the now-unnecessary `" | ".join(fields[2:])`
rejoin — with `maxsplit=2`, `fields[2]` (if present) already is the full, untouched
description. This is a parser fix, not a wire-format or emitter change: the `- [severity]
category | path:line | description` bullet format is retained as-is, and the reviewer
subagent's rubric prompt is unchanged. Update the module docstring's finding-format
description to note explicitly that the description field is free text and may itself
contain `|`.

**R5 — `commands/dark-factory-code-review.md`: abort when `review_diff.txt` has no reviewable
content.**
Immediately after the existing empty-file guard in Phase 2 — the prose bullet at
`commands/dark-factory-code-review.md:81` ("If `$ARTIFACTS_DIR/review_diff.txt` is empty,
write `STATUS: PASS…`"); there is no bash `-z` test for it on main — add a content-aware
check. Express both guards in bash as file tests: `[ ! -s "$ARTIFACTS_DIR/review_diff.txt" ]`
for the existing empty case, then the helper below for the non-empty case — not `-z` on a
variable. If the file is non-empty but contains **no diff payload line** (no line starting
with `+` or `-` other than the `+++`/`---` file headers) outside the `# [SUMMARIZED: ...]`
notices, treat this as a zero-content ranking failure. Implement the predicate as a small,
unit-tested helper (e.g. a `--check-nonempty`-style function in `scripts/diff_rank.py` or a
tiny sibling script) rather than inline prose/bash, consistent with how the rest of this gate
is built.

Helper exit-code contract (fail-closed — this is the one place the fix itself could fail
silently): `0` = reviewable content present, `1` = zero content, `>= 2` = helper error
(crash, unreadable file, bad arguments). The command treats **any non-zero exit** as the
zero-content abort; it must never read a helper crash as "has content". The abort comment
includes the helper's captured stderr. R6 adds a unit test for the `>= 2` path.

On a zero-content result:
- Do **not** invoke `code_review.fail_open` / write `STATUS: ERROR` — this check runs in
  Phase 2, before any reviewer subagent is spawned, so it is input/precondition validation,
  not reviewer failure. `fail_open`'s documented contract (`config/config.yaml:45`,
  `refinement-skills/VERIFIER-CONTRACT.md:43-45`) is scoped to a subagent that errored, timed
  out, or returned unparseable output; `scripts/verdict_gate_check.sh:42-44`'s
  `PASS|SKIPPED|ERROR) exit 0` arm would let the
  PR advance (and auto-merge under `direct-to-pr`) with zero lines actually reviewed —
  exactly the outcome this fix exists to prevent. State this exemption explicitly in the
  command file so a future "harmonize with fail_open" edit doesn't silently reopen the bug.
- Reuse the existing Phase 6 BLOCKED path already in `dark-factory-code-review.md`
  (issue comment → `tracker set-status blocked` → `needs-discussion` label →
  `emit_verdict "code-review" "BLOCKED"`, :175-209) rather than inventing a new
  `STATUS: UNCERTAIN` token. `review.md` on this path is exactly:
  `emit_verdict "code-review" "BLOCKED" "0" "none"`, then `BLOCKERS: 0`, `ADVISORY: 0`,
  `REASON: diff_rank summarized the entire diff to zero reviewable content`. It must **not**
  `cat "$ARTIFACTS_DIR/review_findings.md"` as the existing step 4 does (:207) — no subagent
  ran, so that file does not exist. `BLOCKERS:`/`ADVISORY:` are read by
  `scripts/factory_core/run_record.py:400-411` and the `report` node
  (`workflows/archon-dark-factory.yaml:1355-1358`). If a golden fixture is added under
  `tests/fixtures/verdicts/` for this shape, bump the fixture-count pin in
  `tests/test_verdict.py:93` (currently 18). Put the same explanation in the issue comment
  body; the issue's "abort with UNCERTAIN" describes the human-escalation *semantics*, and
  BLOCKED + `needs-discussion` is this codebase's existing spelling of that semantics.
- Use a comment distinct from the normal findings-block comment: state that `diff_rank.py`
  summarized the entire diff to zero reviewable content, point at `diff-ranking.json` for
  detail, and give the standard re-run command.
- Skip Phases 4–5 (no findings exist to build a payload from or post).
- Skip the Phase 6 step 5 blocking-findings memory write — this is a tooling fault, not a
  code finding worth recording as a lesson.
- No raw-diff fallback for the residual case (diff genuinely exceeds the cap and every file
  landed in `low`/`medium` summary with none spec-named) — after R1 and R3 ship, this can only
  happen on a large diff with no spec-named files inside it, where "a human decides whether
  this merges unreviewed" is the correct outcome, not silently blowing past
  `token_optimization.diff.max_review_tokens`.

**R6 — Tests.**
- `tests/test_diff_rank.py`: add the issue's stated acceptance test (7-file/~2k-token diff,
  6k cap, assert no `[SUMMARIZED` substring in stdout). Retarget (do not delete) the tests
  whose fixtures assumed unconditional low-tier summarization under a cap the diff already
  fits:
  - `test_low_files_always_summarized_regardless_of_budget` → rename to
    `test_low_files_summarized_when_diff_exceeds_cap` and use a `token_cap` smaller than the
    low file itself (not merely smaller than the whole diff) — under R1's budget-checked low
    tier, a low file is summarized only when the remaining budget cannot hold it, so a cap
    that the diff exceeds but the low file fits would now emit it in full and the test would
    stop pinning summarization (real budget pressure still needs coverage).
  - Add a new sibling `test_low_files_verbatim_when_whole_diff_fits_cap` covering the
    now-correct opposite case.
  - `test_summary_line_low_risk_format`, `test_summary_line_non_test_low_risk_not_labeled_test_only`,
    and `test_diff_ranking_json_file_entry_fields` currently rely on the default (6000)
    token cap comfortably exceeding their small fixture diffs, which now trips the R1
    passthrough. Pass each an explicit small `token_cap` so they keep exercising the
    summarization path they were written to test.
  - `test_high_files_fill_budget_then_summarize` and `test_summary_line_budget_exhausted_format`
    already use `token_cap=10` and are unaffected — leave them as-is.
  - `test_diff_ranking_json_critical_token_accounting` (cap 6000, ~180-token fixture) now
    runs through the passthrough and asserts `critical_tokens > 0`; it is unaffected only
    under R2's accounting rule — leave it as-is and treat it as the pin for that rule.
  - Add a test for R2's `under_cap_passthrough` marker and `included: "full"` records.
  - Add a test that the passthrough output is per-file with the header first and drops a
    leading `[Pre-triage]` annotation line (R1/R2 per-file emission).
  - Add an over-cap test that a low file is emitted in full when budget remains after
    `high`/`medium` (R1's budget-checked low tier).
  - Add unit tests for R5's zero-content predicate: all-`[SUMMARIZED]` input → exit 1;
    input with at least one payload line → exit 0; unreadable/missing file → exit `>= 2`.
- `tests/test_code_review_payload.py`: add a regression fixture with a finding description
  containing `|| true` (and one containing a markdown-table-like fragment), asserting a
  byte-exact round trip of the description text.
- Command-file coverage for R3/R5 is **Python text-pin tests** under `tests/` (collected by
  `python -m pytest tests/`, the style of `tests/test_code_review_command.py` and
  `tests/test_code_review_command_render_prompt.py`): assert `--spec-file` appears in the
  Phase 2 `diff_rank.py` invocation, that `push_gate_check.sh "docs/superpowers/specs/"`
  is named in Phase 1, and that the zero-content branch names
  `emit_verdict "code-review" "BLOCKED"`, `needs-discussion`, and the `fail_open` exemption
  sentence. An LLM-executed command file cannot be exercised at runtime by a test, so no
  test claims to observe the board move or the label being applied. No new `.sh` test: CI
  runs `.sh` tests only when listed per file in `.github/workflows/ci.yml` (e.g.
  `tests/test_conformance_memory_write.sh` is not listed and never runs there), and the
  Windows host cannot run them; if one is added anyway it must be appended to `ci.yml`.

## Architecture / Approach

No new services, schemas, or dependencies. All five fixes are localized:

- `scripts/diff_rank.py`: one new whole-diff-vs-cap check near the top of
  `build_ranked_diff()` (after the existing `diff_enabled` bypass, before per-tier bucketing),
  reusing the already-computed `raw_diff_tokens`, emitting per-file with the header first;
  classification still runs unconditionally so `diff-ranking.json` stays complete. On the
  over-cap path the `low` loop gains the same budget check as `medium`. Because this lives in
  `build_ranked_diff()`, it applies to `commands/dark-factory-conformance.md`'s call (:184)
  as well as code-review's — by construction, not by a separate change.
- `commands/dark-factory-code-review.md`: Phase 1 gains the `$SPEC_FILE` resolution block
  (`push_gate_check.sh` first, conformance's 2a/2b as fallbacks); Phase 2 gains the
  `--spec-file` flag and the post-empty-file content check with its fail-closed helper
  contract; Phase 6 gains a third branch reusing the existing BLOCKED machinery with the
  `review.md` shape given in R5.
- `scripts/code_review_payload.py`: a one-line `split` bound plus removing the now-dead
  rejoin branch.

Everything stays pure stdlib Python / bash, consistent with the rest of `scripts/`.

## Alternatives Considered

- **Escape/quote `|` in the reviewer's emitted findings** (rejected for R4): pushes
  correctness onto LLM instruction-following, with silent corruption on any non-compliant
  emission, plus added rubric token cost every Gate 3 run.
- **Switch the finding wire format to JSON lines** (rejected for R4): the rubric prose is
  duplicated across a clone-live file and a baked `/opt/refinement-skills/` fallback that a
  test forces to stay line-identical, so a format change is coupled to an image rebuild across
  every target instance; two tests pin the literal string (`tests/test_code_review_prompt.py:11`,
  `tests/test_code_review_skill_files.py:39`) and a third
  (`tests/test_code_review_skill_files.py::test_rubric_matches_source_except_delabel`) pins the
  baked copy line-identical; JSON-in-markdown
  trades a solved delimiter problem for unsolved ones (code fences, unescaped
  newlines/backticks, trailing commas) with no remaining benefit once the parser is bounded.
- **Scope R1's fix narrowly to only the interaction with `--spec-file`** (i.e., leave
  unconditional low-tier summarization in place and rely solely on R3): rejected — this
  does not fix the reported incident. PR #398's diff was zeroed by the low-tier
  unconditional-summarization mechanism itself; `--spec-file` alone only protects files whose
  path string literally appears in the spec text, leaving every other touched file (including
  non-boilerplate low-tier files — the classifier's `low` tier is not test-file-specific)
  exposed to the same failure mode.
- **Map the R5 abort onto `code_review.fail_open`'s `STATUS: ERROR` path**: rejected —
  `scripts/verdict_gate_check.sh:42-44` treats `ERROR` as non-blocking, which would let a PR with zero
  reviewed content advance (and auto-merge under `direct-to-pr`), the exact outcome this fix
  exists to prevent.

## Open Questions (non-blocking)

- R1 lives in `build_ranked_diff()`, so it already applies to
  `commands/dark-factory-conformance.md`'s `diff_rank.py` call (:184) by construction — there
  is nothing to "extend". The only remaining question is whether Gate 2 *wants* different
  behavior (e.g. keeping low-tier summarization for token savings even under the cap);
  nothing in this issue asks for that, and conformance's own `[Pre-triage]` ordering contract
  is preserved by R1's per-file emission. Flag for a follow-up only if Gate 2 prompt sizes
  become a problem.
- Follow-up, not this ticket: the pre-existing "`review_diff.txt` empty → `STATUS: PASS`"
  bullet (`commands/dark-factory-code-review.md:81`) is itself a silent pass if
  `git diff main...HEAD 2>/dev/null` (:62-66) fails — an empty `$RANK_IN` yields an empty
  ranked diff and a PASS with nothing reviewed. Out of #403's stated scope.
- Follow-up, not this ticket: on the R5 path the `report` node
  (`workflows/archon-dark-factory.yaml:1355-1364`) will print "⛔ Blocked — 0 blocking
  finding(s) … see PR review comments" — misleading (no PR review was posted), though not
  silent. The issue comment R5 posts carries the real explanation; adjusting the report
  wording touches the workflow YAML and is left to a follow-up.

## Assumptions

- `token_optimization.diff.max_review_tokens` (config default 6000) is the correct budget to
  compare the whole diff against for R1; no separate/tighter threshold is introduced.
- `scripts/push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM"` resolves the spec at
  Gate 3 for any branch that carried its spec commit (`transfer_refine_artifacts.sh`, #387,
  guarantees that on the fresh-fork paths); conformance's 2a/2b fallbacks cover the rest.
  Code-review's Gate 3 always runs after conformance in the DAG, so a spec approved for this
  ticket is expected to already be discoverable by that lookup.
- R5's zero-content predicate ("no diff payload line outside `[SUMMARIZED]` notices, outside
  `+++`/`---` headers") is sufficient to detect the failure mode without false-positiving on a
  legitimately tiny real diff — a genuinely tiny diff will pass through verbatim under R1 and
  never reach the all-summarized state R5 checks for.

# Fix `_VERDICT_MISSING_RE` Gaps: docs-gate Overlap and Unparseable-Status Matching

**Issue:** #591

---

## Overview / Problem Statement

#373 added `_VERDICT_MISSING_RE` to `scripts/factory_core/error_signature.py::classify()`
so that a run ending in a missing/unparseable gate verdict is signed
`substantive:verdict_missing` (breaker-visible) instead of being shadowed by earlier,
non-fatal preview noise or falling into `substantive:unknown:<exit_code>`. The operator
review of PR #590 (#373's implementation) found two gaps in that regex, both reported
directly against `scripts/factory_core/error_signature.py`:

1. **The prefix is not left-anchored.** `_VERDICT_MISSING_RE` starts with
   `verdict_gate_check\.sh: `, matched via `.search()` with no boundary at the start.
   `scripts/docs_verdict_gate_check.sh` (Gate 2.5, added by #535) is a **sibling**
   script, not a flag on the first — its own stderr lines all contain the substring
   `verdict_gate_check.sh: ` because its basename, `docs_verdict_gate_check.sh`, embeds
   that substring. Today this is inert: `docs_conformance.mode` defaults to `observe`
   (`config/config.yaml`), and the only line that prints in the common case says
   "enforce **would block**" (singular, no `s`), which does not match the regex's
   `blocks issue #\d+ \(STATUS=missing\)` alternative. But `docs_verdict_gate_check.sh`'s
   enforce-mode lines **do** say `blocks issue #N (STATUS=missing...)` verbatim, and
   would match the moment `docs_conformance.mode` flips to `enforce` (tracked separately
   as #536) — silently pulling Gate 2.5 deaths into the same breaker-visible signature
   as a Gate 2/3 verdict miss, with no one having decided that was correct.
2. **Unparseable non-empty status.** `verdict_gate_check.sh`'s quiet-branch echo embeds
   `STATUS=${STATUS:-missing}` verbatim. The regex's second alternative only matches the
   literal value `missing`. A real `BLOCKED` verdict is deliberately excluded (existing
   negative test). But a quiet-branch line whose `STATUS` is neither empty nor `BLOCKED`
   — e.g. a truncated or corrupted verdict-file write that leaves a garbled token in the
   `STATUS:` line — is not matched by either alternative and falls through to whatever
   the rest of `classify()`'s chain happens to match, or to `substantive:unknown`.

Per the issue body and confirmed in Q&A below, this ticket is **classifier-only**: no
edit to either gate script's exit-code/blocking behavior, `breaker.py`, or
`workflows/archon-dark-factory.yaml`. Only `error_signature.py`'s regex/`classify()`
logic and its test coverage change, plus the comment and documentation follow-ups listed
under Architecture / Approach.

## Requirements

(From the issue body and brainstorming Q&A.)

1. **Exclude `docs_verdict_gate_check.sh` from `_VERDICT_MISSING_RE`, decided now, not
   deferred to #536.** Anchor the script-name prefix with a leading `\b` word boundary
   (`\bverdict_gate_check\.sh: `) rather than a lookbehind: `_` is a word character, so
   `\b` already rejects the `docs_` prefix (no boundary exists between `_` and the
   following `v`) while still matching a bare occurrence or a path-prefixed one (e.g.
   `scripts/verdict_gate_check.sh:`, where the boundary before `v` is `/`, a non-word
   character). Rationale (Q1): the `docs_` overlap is a substring accident from #373, not
   a design choice; including it would silently feed Gate 2.5 deaths into
   `substantive:verdict_missing`, changing what the breaker's two-consecutive-match trip
   sees for a gate that is not even enforced yet — a side-effect gate/breaker change
   CLAUDE.md requires its own reviewed ticket for. #536, when it flips
   `docs_conformance.mode` to `enforce`, is the right place to decide whether a missing
   docs-conformance verdict gets its own signature (e.g. `substantive:docs_verdict_missing`)
   with that context in view; this ticket only prevents the accidental, undecided overlap.
2. **New negative coupling tests proving the docs-gate exclusion**, mirroring #373's
   existing style (read the real script's echo lines at test time, render them,
   assert on `_VERDICT_MISSING_RE`/`classify()`):
   - Render `scripts/docs_verdict_gate_check.sh`'s enforce-mode quiet-branch echo
     (`"docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (${_WHY}); upstream already communicated, no comment needed."`)
     and its enforce-mode silent-death echo, each with `_WHY` substituted to
     `STATUS=missing`, and assert neither matches `_VERDICT_MISSING_RE` / classifies as
     `substantive:verdict_missing`.
   - Render the observe-mode echo
     (`"docs_verdict_gate_check.sh: ${GATE_LABEL} observe mode — enforce would ${DECISION} issue #${ISSUE_NUM} (${_WHY}); proceeding."`)
     with `DECISION=block` and `_WHY=STATUS=missing`, and assert it does not match
     either — both because of the `docs_` exclusion and because "would block" (no `s`,
     today's actual shipped wording) does not match the `blocks` alternative; a future
     reword of either script must not accidentally start matching.
   - Render the enforce-path silent-death echo
     (`"docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (${_WHY}) and no needs-discussion label — failing closed and posting failure comment."`)
     with `_WHY` set to
     `STATUS=missing; the branch changed during the read-only docs-conformance node: HEAD moved`
     (the head/index tripwire form, which takes this path in every mode, observe
     included) and again with `_WHY` set to `STATUS=PAS`, and assert neither matches.
     Once Requirement 3 widens the `STATUS=` alternative, the leading `\b` is the only
     thing keeping these two lines out, so they need their own cases.
   - A style-matching test (per the #373 precedent of
     `test_gate_script_silent_death_echo_carries_classifier_cross_reference`) is not
     required for `docs_verdict_gate_check.sh` since it is deliberately excluded, not
     coupled.
3. **Match an unparseable/garbled non-empty `STATUS` in `verdict_gate_check.sh`'s
   quiet-branch line as the same signature, `substantive:verdict_missing`** — do not
   introduce a distinct `substantive:verdict_unparseable` (or similar) signature.
   Rationale (Q2): the gate script's own silent-death line already treats "missing" and
   "unparseable" as one case in its wording ("verdict missing/unparseable"); splitting
   the classifier signature would let a ticket whose verdict file alternates between
   absent and corrupted across consecutive attempts dodge
   `breaker.py::record_failure_signature()`'s stuck check (which requires two
   *consecutive, identical* `substantive:`-prefixed signatures) — a side-effect weakening
   of the breaker that CLAUDE.md's hard limits forbid introducing incidentally. If
   telemetry ever needs the absent-vs-corrupted distinction, that is its own reviewed
   ticket.
4. **The exclusion of real verdict tokens from the quiet-branch match must be derived
   from `scripts/factory_core/verdict.py`'s own constants**, not re-typed as magic
   strings in the regex, so a future addition to `verdict.py`'s token set is excluded
   automatically instead of silently being misclassified:
   - `GATING_BLOCK_STATUSES = {"BLOCKED"}` and `LEGACY_STATUSES = {"HUMAN_REQUIRED", "FAIL"}`
     (both defined in `verdict.py`, confirmed present) must be excluded — a quiet-branch
     `STATUS=` value equal to any of these is a real verdict, not a missing/unparseable
     one. Known asymmetry, kept as today's behavior: `verdict_gate_check.sh` itself
     treats `HUMAN_REQUIRED`/`FAIL` as missing/unparseable, so the same token without a
     `needs-discussion` label reaches the silent-death line and does sign
     `substantive:verdict_missing`; only the quiet-branch line is excluded.
   - `GATING_PASS_STATUSES = {"PASS", "SKIPPED", "ERROR"}` can never reach the
     quiet-branch echo (`verdict_gate_check.sh` exits 0 for those three earlier, before
     the quiet-branch logic runs) — excluding them from the regex too is harmless but not
     required for correctness.
   - Matching is case-sensitive, mirroring the gate script's own case-sensitive
     `[ "$STATUS" = "BLOCKED" ]` comparison: a lowercase `blocked` is not a real verdict
     to the gate script either, so it must match as unparseable (i.e. classify as
     `substantive:verdict_missing`), not be excluded.
   - The excluded-token check must be anchored at the closing `)` (the field has no
     internal whitespace — `STATUS` is parsed via `awk '{print $2}'` — so `)` is the only
     valid terminator), so that a prefix-garbled value like `BLOCKEDX` still matches as
     unparseable rather than being accidentally excluded by a bare substring check
     against `BLOCKED`.
5. **New tests covering the unparseable-status match**, extending
   `test_verdict_missing_regex_matches_gate_script_quiet_branch_wording`'s existing
   render-the-real-echo style:
   - Must match (classify as `substantive:verdict_missing`): a garbled non-empty token
     such as `PAS` or `BLOCKEDX`, in addition to the existing `missing` case; a
     lowercase `blocked` (Requirement 4's case-sensitivity rule); and a quiet-branch line
     whose prefix is path-qualified (`scripts/verdict_gate_check.sh: `), proving the
     leading `\b` still matches after a `/`.
   - Must NOT match (unchanged from today): `BLOCKED`. New negative cases: `HUMAN_REQUIRED`
     and `FAIL`.
6. **No change to `scripts/verdict_gate_check.sh`'s or
   `scripts/docs_verdict_gate_check.sh`'s behavior, `breaker.py`, or
   `workflows/archon-dark-factory.yaml`.** Both gate scripts' pass/block decisions and
   `verdict.py`'s parsing are unchanged; only `error_signature.py`'s regex/`classify()`
   and its tests change, plus the comment and documentation follow-ups listed under
   Architecture / Approach (one of them a comment-only edit in
   `scripts/verdict_gate_check.sh`). `tests/test_verdict_gate_check.sh` (listed in
   `.github/workflows/ci.yml`) and `tests/test_docs_verdict_gate_check.py` (run by CI's
   pytest step) must stay green unmodified.
7. **Ranking and existing anchoring are unchanged.** `_VERDICT_MISSING_RE` stays between
   `_RATE_LIMIT_RE` and `_PREVIEW_INFRA_RE` in `classify()`'s chain (#373's ordering,
   untouched by this ticket). The `verdict_gate_check.sh: ` prefix anchor and the
   `.{0,80}` gap are unchanged except for the new leading `\b`.

**Where the tests run.** All new/changed tests are pytest, added to
`tests/test_factory_core_error_signature.py`; they run in CI's
`python -m pytest tests/ -v` step and on a developer host. No `.sh`-style CI-listed test
needs a new case, since neither gate script's behavior changes.

## Architecture / Approach

**One code file changes:** `scripts/factory_core/error_signature.py` (plus its tests,
and the comment and documentation follow-ups listed at the end of this section). Add an
import of the canonical verdict-token constants, derive the exclusion alternation from
them, and widen `_VERDICT_MISSING_RE`:

```python
from .verdict import GATING_BLOCK_STATUSES, LEGACY_STATUSES

# Real, non-missing/unparseable verdict tokens that must never be mistaken for a
# missing one in the quiet-branch STATUS= field. Sourced from verdict.py's own
# constants (not re-typed here) so a future addition to that enum is excluded
# automatically. GATING_PASS_STATUSES is not included: those three statuses make
# verdict_gate_check.sh exit 0 before the quiet-branch echo is ever reached, so they
# can never appear in this field.
_REAL_VERDICT_TOKENS_RE = "|".join(sorted(GATING_BLOCK_STATUSES | LEGACY_STATUSES))

_VERDICT_MISSING_RE = re.compile(
    r"\bverdict_gate_check\.sh: .{0,80}"
    r"(?:verdict missing/unparseable for issue #\d+"
    rf"|blocks issue #\d+ \(STATUS=(?!(?:{_REAL_VERDICT_TOKENS_RE})\))[^)\s]*\))"
)
```

Walking the two changes against the gaps:

- **Gap 1 (docs-gate overlap):** the new leading `\b` requires a word boundary
  immediately before `verdict_gate_check.sh:`. In `docs_verdict_gate_check.sh:`, the
  character immediately before the embedded `verdict_gate_check.sh:` substring is `_`
  (from `docs_`) — both `_` and the following `v` are word characters, so no boundary
  exists there and `.search()` cannot anchor the match at that position. A bare
  occurrence (string start, or preceded by whitespace/newline/`/` as in a path-prefixed
  `scripts/verdict_gate_check.sh:`) still has a boundary and matches exactly as before.
- **Gap 2 (unparseable status):** the second alternative's `STATUS=` capture is now
  `(?!(?:BLOCKED|HUMAN_REQUIRED|FAIL)\))[^)\s]*\)` (tokens alphabetized by `sorted()` at
  import time — the exact textual order is incidental, not semantic; `[^)\s]*` rather
  than `[^)]*` so the token cannot span whitespace or a newline). The negative
  lookahead only rejects the position where `STATUS=` is followed by exactly one of the
  excluded tokens and then `)` — so `STATUS=missing)` matches (unchanged from today),
  `STATUS=BLOCKED)` / `STATUS=HUMAN_REQUIRED)` / `STATUS=FAIL)` are excluded (unchanged
  behavior for `BLOCKED`, new exclusions for the two legacy tokens), and any other token
  — `STATUS=PAS)`, `STATUS=BLOCKEDX)`, `STATUS=garbled123)` — matches as
  `substantive:verdict_missing`, closing the gap.

No other line in `classify()` changes. The function's control flow, argument signature,
and the `write_signature()` helper are untouched.

**Comment additions (Requirement 1-adjacent, no test enforces prose beyond the coupling
tests themselves):**
- Above `_VERDICT_MISSING_RE`: note that `docs_verdict_gate_check.sh` is deliberately
  excluded via the `\b` anchor pending #536's own decision on that gate's signature, so a
  future reader doesn't "fix" the exclusion as an oversight.
- Above the new `_REAL_VERDICT_TOKENS_RE` line: note it is sourced from `verdict.py` so
  the two files don't drift.

**Comment and documentation follow-ups (no behavior change):**
- `scripts/factory_core/error_signature.py`: rewrite the existing comment above
  `_VERDICT_MISSING_RE` (`:18-26` on main), which says the quiet-branch line matches
  "when it reports STATUS=missing", to say it matches a missing or unparseable status
  and excludes the real verdict tokens.
- `scripts/verdict_gate_check.sh:68-71`: comment-only update of "when it prints
  STATUS=missing" to the same wording. Keep the `error_signature.py` reference within
  the four lines above the silent-death echo
  (`test_gate_script_silent_death_echo_carries_classifier_cross_reference` requires it).
  No executable line changes.
- `docs/run-lifecycle.md`, failed-run step 4 (`:1115-1122` on main): update "either of
  the two missing-verdict stderr lines" to cover an unparseable quiet-branch status, and
  re-map the `scripts/factory_core/error_signature.py:55-89` citation to `classify()`'s
  line range after the new import, constant and comments shift it.

## Alternatives Considered

1. **Exclude `docs_verdict_gate_check.sh` with a negative lookbehind, `(?<!docs_)`**
   (the issue body's literal suggestion). Rejected in favor of a leading `\b`: a
   lookbehind only rejects the exact literal `docs_` immediately before the match,
   while `\b` generically rejects *any* `<word>_` prefix (and still correctly matches a
   path-prefixed occurrence), matching the existing anchoring philosophy (anchor on
   structure, not on enumerating every known bad prefix).
2. **Make the docs-gate overlap intentional** — treat a missing docs-conformance
   verdict as `substantive:verdict_missing` too, with a positive coupling test against
   `docs_verdict_gate_check.sh`'s wording. Rejected (Q1): this is a breaker-visible
   classification decision for a gate (`docs_conformance`) that is not enforced today,
   and CLAUDE.md requires gate/breaker-adjacent changes to get their own reviewed
   ticket — that is #536's scope, not this classifier-gap-fix ticket's.
3. **A distinct `substantive:verdict_unparseable` signature for Gap 2**, so telemetry
   could distinguish "verdict file absent" from "verdict file present but corrupted".
   Rejected (Q2): no current consumer needs the distinction, and splitting the signature
   would let an alternating absent/corrupted failure pattern dodge
   `breaker.py`'s consecutive-exact-match stuck check — a silent weakening of the
   breaker's trip sensitivity, which is explicitly against CLAUDE.md's hard limits on
   this kind of side effect.
4. **Hand-type the excluded tokens (`BLOCKED`, `HUMAN_REQUIRED`, `FAIL`) directly into
   the regex literal**, skipping the `verdict.py` import. Rejected: `verdict.py` is
   already the single documented source of truth for the verdict schema
   (per its own module docstring and VERIFIER-CONTRACT.md); importing its constants
   means a future addition to the real-verdict enum there is excluded here
   automatically, instead of requiring a second, easily-missed edit to stay in sync.

## Open Questions (non-blocking)

- The issue body's "Unverified assumption" section — no raw captured run log has yet
  shown an actual `verdict_gate_check.sh:` line in `$TMP_OUT`, the evidence for Gap 1/2
  mattering at all is the #580 post-mortem quote — is carried over as a standing
  action item, not a requirement of this ticket: the next time a run fails at Gate 2
  with a missing verdict, confirm `error-signatures/<issue>.implement.sig`
  reads `substantive:verdict_missing` and record the result against this issue or #373.
  This ticket does not (and cannot, from refine) manufacture that evidence.
- Whether `#536` should ultimately mint a distinct `substantive:docs_verdict_missing`
  signature for a docs-gate verdict miss, or simply accept
  `substantive:verdict_missing` once it has had its own review — left entirely to #536;
  this ticket takes no position beyond "not automatically, not via this regex."

## Assumptions (flagged)

- `GATING_BLOCK_STATUSES` and `LEGACY_STATUSES` in `scripts/factory_core/verdict.py`
  remain the complete, authoritative enumeration of non-missing/unparseable `STATUS`
  tokens reachable in `verdict_gate_check.sh`'s quiet branch. If a future change adds a
  new real-verdict token anywhere in the verdict-writing contract (`gate_lib.sh::emit_verdict`,
  a new verifier output mode) without also adding it to one of these two `verdict.py`
  sets, this classifier would misclassify that token's quiet-branch line as
  `substantive:verdict_missing` — a false substantive signal, not a false negative, so it
  fails loud (over-counts toward the breaker) rather than silently under-counting.
- `verdict_gate_check.sh`'s `STATUS` variable, parsed via `awk '{print $2}'` from the
  verdict file's `^STATUS:` line, never contains whitespace or a literal `)` character.
  This is assumed from the current `emit_verdict`/`parse_verdict` contract (STATUS is a
  single free token); a future verdict writer that emits a `STATUS:` value containing
  whitespace or `)` would not match the `[^)\s]*\)` token pattern in the widened regex
  and would fall back to today's classification.

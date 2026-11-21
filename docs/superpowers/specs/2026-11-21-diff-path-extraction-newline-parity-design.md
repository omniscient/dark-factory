# Fix: diff path extraction splits on more line breaks than `git apply` (#552)

**Issue:** #552

**Revised:** 2026-11-21 (operator amendment after spec gate review)

## Overview / Problem Statement

`scripts/factory_core/mechanism_candidate.py:271`, inside `extract_diff_paths`, iterates
`diff.splitlines()`. Python's `str.splitlines()` splits on `\n`, `\r`, `\r\n`, `\f`, `\v`,
`\x1c`-`\x1e`, `\x85`, U+2028, and U+2029; `git apply` splits only on `\n`. The function's own
docstring already states the intended contract — "Hunk bodies are consumed by their `@@
-a,b +c,d @@` counts, exactly as `git apply` reads them" — so `.splitlines()` is a bug
against that contract, not a deliberate choice.

Because the two parsers disagree about where a line ends, a crafted diff can put one of
these non-`\n` separators after the last body line of a hunk, followed on the same
`\n`-delimited line by a fake `@@` header (`+new\x0c@@ -1,1 +1,1 @@`). `git apply` reads that
as one body line, sees the hunk as complete, and treats the `---`/`+++`/`@@` lines that
follow as a new file's header and hunk. `extract_diff_paths` instead sees the fake `@@` as a
line of its own, opens a second hunk for the first file, and consumes the real `---`/`+++`
lines as that hunk's `-`/`+` body. The validator under-reports
the diff's touched-path set (e.g. only an H0 doc path) while `git apply` would additionally
patch a file the smuggled block names (e.g. `scheduler.sh`), silently defeating the
risk-classification this function exists to support.

No caller outside this module exists on `main` today (confirmed at d7e9421). #465 (the
harness candidate materializer) is the first consumer and is not exposed: its plan was
amended to re-verify the applied workspace against the validated path set independently.
This ticket closes the root cause in the validator itself so a later consumer (#544) is not
exposed. This is a hardening fix to an already fail-closed parser; it adds no new
functionality.

## Requirements

1. `extract_diff_paths` must split a diff into lines the same way `git apply` does: on `\n`
   only. None of `\r`, `\f`, `\v`, `\x1c`, `\x1d`, `\x1e`, `\x85`, U+2028, or U+2029 may be
   treated as a line boundary.
2. The fix must not introduce a phantom trailing empty line for the (overwhelmingly common)
   case of a diff string that ends in `\n` — see Architecture/Approach for why a bare
   `diff.split("\n")` is unsafe here, not just imprecise.
3. No other parsing branch in `extract_diff_paths`'s state machine changes, except as
   Requirements 7 and 8 specify (two checks that the old splitting kept fail-closed only by
   accident, and that must stay fail-closed once separators remain inside a line). The existing
   "nothing may follow an exhausted hunk" check (`_fail(f"unexpected line {line!r} after a
   hunk ...")`) is what actually rejects a smuggled header once lines are split correctly;
   this ticket is a line-splitting primitive fix, not a new detection rule.
4. Scope is exactly `scripts/factory_core/mechanism_candidate.py` and
   `tests/test_mechanism_candidate.py`, per the issue. No other `.splitlines()` call exists
   in this module (confirmed: line 271 is the only occurrence in the file) or needs changing.
5. Tests must prove parity with `git apply`, not merely that the function doesn't crash:
   - One case per separator listed in the issue (`\r`, `\f`, `\v`, `\x1c`, `\x1d`, `\x1e`,
     `\x85`, U+2028, U+2029), using the issue's reproduction shape exactly (see Tests),
     asserting both that `git apply` really patches the hidden path (`"scheduler.sh" in
     _git_apply_paths(diff, tmp_path)`, so the fixture is a live attack, not a vacuous
     string) and that the extractor rejects the diff (`pytest.raises(...,
     match="after a hunk")`). This spec chooses **reject**, not report, consistently with
     Requirement 3; these diffs must never be asserted with set equality.
   - A guard for the trailing-empty-line hazard (Requirement 2): a diff whose `@@` counts
     promise one more line per side than the body supplies, but which still ends with a
     literal `\n`, must raise `"diff ends inside a hunk"`, not validate.
   - One case per separator for Requirement 7, three cases for Requirement 8, and one CRLF
     case for Requirement 6 (see Tests).
6. `git apply`'s own CRLF/`\r` tolerance is out of scope to replicate beyond Requirement 1: a
   literal `\r` embedded in or at the end of an otherwise well-formed header line (e.g. a
   diff generated with CRLF line endings) validates on `main` today, because `splitlines()`
   swallows the `\r\n`, and is rejected after this change: the header regexes
   (`_OLD_HEADER_RE`/`_NEW_HEADER_RE`/`_GIT_HEADER_RE`) are all anchored with `\S+...$` and
   `\r` is whitespace, so the line `_fail`s as a malformed header. This is an intended,
   fail-closed behaviour change (`git apply` itself accepts such a diff) and is covered by a
   test. A `\n`-delimited diff whose hunk-body lines end in `\r` (an edit of a CRLF file)
   still validates.
7. Tool-frontmatter detection must not be weakened by Requirement 1. On `main`, a changed
   line such as `+description: x\rallowed-tools: Bash` in a `commands/*.md` file is rejected
   (the fragment after the separator is an unexpected line). With `\n`-only splitting it
   becomes one changed line that `_TOOL_FRONTMATTER_RE` (`^[+-]\s*...`) does not match, so
   the candidate would classify H1 instead of H3, while a YAML frontmatter reader may treat
   `\r`, `\x85`, U+2028 and U+2029 as line breaks. `validate_candidate` must therefore flag a
   path as tool-frontmatter-changing when any `str.splitlines()` fragment of a changed
   line's content (the line without its leading `+`/`-`) matches
   `^\s*(?:allowed-tools|disallowed-tools)\s*:`. Using `splitlines()` here is deliberate: for
   detection, the wider separator set is the conservative reading.
8. A header line starting with `old mode `, `new mode `, `deleted file mode ` or
   `new file mode ` must match `_MODE_HEADER_RE` in full with a mode in
   `_REGULAR_FILE_MODES`; otherwise the diff is rejected with the existing "only regular
   file modes 100644/100755 are accepted" message. Today a mode line that fails
   `_MODE_HEADER_RE` falls through to `_EXTENDED_HEADER_RE`, which matches on the prefix
   alone and accepts the line with no mode check. `new file mode 120000 ` (trailing space or
   tab) therefore already validates on `main` while `git apply` creates a symlink (it accepts
   any `isspace` byte after the mode), and Requirement 1 alone would add
   `new file mode 120000\r` to that set. The pre-existing space/tab variant is closed in this
   ticket by operator decision.

## Architecture / Approach

**Chosen approach — strip one trailing `\n`, then split on `\n` only:**

```python
# line 271, inside extract_diff_paths
lines = diff[:-1].split("\n") if diff.endswith("\n") else diff.split("\n")
for line in lines:
    ...
```

Why not a bare `diff.split("\n")`: `str.splitlines()` never produces a trailing empty
element for text ending in a separator (`"a\nb\n".splitlines() == ["a", "b"]`), but
`str.split("\n")` does (`"a\nb\n".split("\n") == ["a", "b", ""]`). That phantom `""` is not
inert here: inside an unexhausted hunk, line 280's `if line == "" or tag == " "` already
treats a bare empty string as a valid context line (git's own leniency for a context line
representing a blank source line whose trailing space got stripped). If a diff is truncated
— missing its last promised hunk-body line — but still ends with `\n` (the normal case for
any diff produced by a tool), a bare `.split("\n")`'s manufactured trailing `""` would get
silently consumed as that missing context line, satisfying the hunk's `@@` counts and
bypassing the existing `"diff ends inside a hunk"` rejection (line 347-348) for a diff that
should fail. Stripping exactly one trailing `\n` before splitting reproduces
`splitlines()`'s "no phantom trailing element" property for the one separator (`\n`) that
should still end a line, while refusing to treat any other character as a boundary — this is
the precise intersection of "matches git's line semantics" and "doesn't regress the
truncated-diff check."

Apart from Requirement 8's mode-line branch, no other line in the function changes. Once
lines are split this way, every one of the
issue's separators (`\r`, `\f`, `\v`, `\x1c`-`\x1e`, `\x85`, U+2028, U+2029) stays inside
whichever line it appeared in as ordinary content:
- Inside a hunk body, it's swallowed by the hunk's tag/count accounting exactly as `git
  apply` would see it (one content line, not two).
- The issue's fake `@@` header, glued after a separator to the end of a hunk-body line, is
  consumed as that same line's content, so the hunk is exhausted exactly where `git apply`
  says it is. The next genuinely-newline-delimited line (the real `--- a/scheduler.sh`) is
  what the existing "unexpected line ... after a hunk" check rejects — the same mechanism
  that already rejects the module's `_SMUGGLED` fixture today for the
  already-newline-delimited case.

No new state is needed in the parser. The change is the splitting primitive (the docstring
then describes a contract that is actually true), plus two small fail-closed adjustments:

- Requirement 7, in `validate_candidate` (the `frontmatter = {...}` comprehension): test
  every `ln[1:].splitlines()` fragment against `^\s*(?:allowed-tools|disallowed-tools)\s*:`
  instead of testing the whole changed line against `_TOOL_FRONTMATTER_RE`.
- Requirement 8, in `extract_diff_paths`'s header branch: enter the mode branch on the
  `old mode |new mode |deleted file mode |new file mode ` prefix, and reject unless
  `_MODE_HEADER_RE` matches and the mode is in `_REGULAR_FILE_MODES`.

**Belt-and-suspenders rejection check — not adopted:** the issue's alternative proposal
("reject a diff where `diff.splitlines()` differs from `diff.split(\"\\n\")`") was
considered and rejected. Once the extractor's own line semantics match `git apply`'s, this
check adds no additional safety — the smuggling vector it would guard against no longer
exists — while it actively risks rejecting legitimate diffs: a file literally containing a
form feed (`\f`) or vertical tab (`\v`) character, or a diff generated with CRLF line
endings, would make `splitlines()` and `split("\n")` disagree on an otherwise honest, fully
rejectable-or-acceptable-on-its-own-merits diff, for no safety benefit once Requirement 1 is
met.

Two checks did depend on the old splitting (tool-frontmatter detection and the mode-line
check). Requirements 7 and 8 close those two specifically rather than adopting this blanket
rejection.

**Tests** (`tests/test_mechanism_candidate.py`):

All fixtures reuse the existing `_diff`, `_DOC`, `_record`, `_EMPTY_SAFETY` and
`_git_apply_paths` helpers. `SEPS` is `\r`, `\x0b`, `\x0c`, `\x1c`, `\x1d`, `\x1e`, `\x85`,
`\u2028`, `\u2029`.

```python
HIDDEN = "@@ -1,1 +1,1 @@\n--- a/scheduler.sh\n+++ b/scheduler.sh\n@@ -1,1 +1,1 @@\n-safe\n+pwned\n"
hidden_header = _diff(_DOC)[:-1] + sep + HIDDEN                       # T1, per sep
truncated = _diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,2 +1,2 @@")  # T2
frontmatter = _diff("commands/dark-factory-plan.md", "-description: x",
                    "+description: x" + sep + "allowed-tools: Bash")   # T3, per sep
mode_line = (f"diff --git a/{_DOC} b/{_DOC}\nnew file mode 120000{tail}\n--- /dev/null\n"
             f"+++ b/{_DOC}\n@@ -0,0 +1 @@\n+/etc/passwd\n")           # T4, tail in " ", "\t", "\r"
crlf = _diff(_DOC).replace("\n", "\r\n")                               # T5
```

- **T1** (Requirements 1, 3, 5), parametrized over `SEPS`: assert `"scheduler.sh" in
  _git_apply_paths(hidden_header, tmp_path)`, then `pytest.raises(mc.MechanismCandidateError,
  match="after a hunk")` around `mc.extract_diff_paths(hidden_header)`. The diff must reach
  git byte-exact (true for `_git_apply_paths` on the Linux CI runner). Do not use the shape
  `sep + _diff("scheduler.sh")`: with a `diff --git` line after the separator, `main`
  already reports both paths, so that shape does not reproduce the under-report.
- **T2** (Requirement 2): `mc.extract_diff_paths(truncated)` raises matching
  `"ends inside a hunk"`. The hunk must owe a line on *both* sides; dropping `+new` or
  shortening it to `+ne` does not exercise the hazard.
- **T3** (Requirement 7), parametrized over `SEPS`: `mc.validate_candidate(_record(
  editable_path_allowlist=[cmd], diff=frontmatter, risk_class="H2"), safety=_EMPTY_SAFETY)`
  raises matching `"derived risk class H3"`, as the existing `\n`-delimited frontmatter case
  in `test_risk_class_underclaim_rejected` does.
- **T4** (Requirement 8), parametrized over the three tails: `mc.extract_diff_paths(mode_line)`
  raises matching `"only regular file modes"`.
- **T5** (Requirement 6): `mc.extract_diff_paths(crlf)` raises matching
  `"malformed diff --git header"`.

Measured red/green (pytest, scratch copies of `origin/main` at e707c1f): `main` is the
module unchanged, `split only` adds only Requirement 1's change, `full` adds Requirements 7
and 8, `bare split` is rejected Alternative 1.

| Test | `main` | `split only` | `full` | `bare split` |
|---|---|---|---|---|
| T1 (9 cases) | red | green | green | green |
| T2 | green | green | green | red |
| T3 (9 cases) | red | red | green | red |
| T4 space, tab | red | red | green | red |
| T4 `\r` | green | red | green | red |
| T5 | red | green | green | green |

T2 and T4 `\r` are guards rather than red-then-green tests: `main` already rejects both, and
they exist to fail an implementation that uses a bare `split("\n")` (T2) or that ships
Requirement 1 without Requirement 8 (T4 `\r`). Every other case is red on `main` and green
on the full change.
- Run `python -m pytest tests/test_mechanism_candidate.py -v` locally before pushing; CI runs
  the same suite with `PYTHONPATH=scripts` per `.github/workflows/ci.yml`.

## Alternatives Considered

1. **Bare `diff.split("\n")` with no trailing-newline handling.** Rejected: as detailed
   above, it manufactures a phantom trailing `""` element for any diff ending in `\n` (the
   normal case), which line 280's existing context-line leniency (`line == ""`) would
   silently treat as a missing hunk-body line, letting a truncated/malformed diff bypass the
   `"diff ends inside a hunk"` check. This is a correctness regression the fix itself would
   introduce, not a pre-existing risk.
2. **Explicit `splitlines()`-vs-`split("\n")` divergence check (the issue's alternative
   proposal), instead of or in addition to fixing the splitting primitive.** Rejected per
   Architecture/Approach: once the primitive itself matches `git apply`, the check is
   redundant for the smuggling vector it targets and would reject legitimate diffs containing
   a literal `\f`/`\v`/CRLF with no corresponding safety gain. Confirmed via brainstorming
   Q&A below.
3. **Normalize all `git apply`-equivalent separators in the diff before parsing (e.g.
   translate `\r\n` to `\n`) rather than just not treating non-`\n` separators as
   boundaries.** Rejected: `git apply` does not normalize these characters away either — it
   treats them as ordinary byte content within a line — so translating them would make the
   extractor diverge from `git apply` in the opposite direction (treating two different byte
   sequences as identical) instead of matching it exactly, which is the function's stated
   contract.

## Open Questions (non-blocking)

- None. The issue's reproduction, proposed fix, and scope are all directly actionable; the
  one open design choice (primitive fix vs. divergence-rejection, and the trailing-newline
  hazard within the primitive fix) was resolved in brainstorming (see Q&A below).

## Brainstorming Q&A

> **Q:** The issue proposes two alternative fixes: (a) change `extract_diff_paths` to split
> on `"\n"` only (via `diff.split("\n")`), relying on the function's existing "nothing may
> follow an exhausted hunk" check to reject any smuggled header that a non-`\n` separator
> would otherwise have exposed as a false line break; or (b) explicitly detect and reject any
> diff where `diff.splitlines()` disagrees with `diff.split("\n")` (e.g. a pre-check that
> fails closed on any divergence) in addition to or instead of switching the primitive. Which
> should the spec adopt as the actual fix, and why? Specifically: is switching the single
> `.splitlines()` call on line 271 to `.split("\n")` sufficient on its own to make
> `extract_diff_paths` agree with `git apply` on every one of the issue's listed separators
> (`\r`, `\f`, `\v`, `\x1c`-`\x1e`, `\x85`, U+2028, U+2029), given the parser's existing
> hunk-exhaustion check? Or is a belt-and-suspenders explicit-rejection check also warranted,
> and if so what should it reject and where?
>
> **A:** Adopt (a) by itself: replace line 271's `diff.splitlines()` with a split on `"\n"`
> only, but drop the single empty string left after the diff's final newline (e.g. `lines =
> diff.split("\n"); if lines and lines[-1] == "": lines.pop()`). A bare `diff.split("\n")`
> creates a phantom `""` element, and in a hunk that still owes a line, `line == ""` counts as
> a context line on both sides (line 280). That would let a diff missing its last line pass
> validation when git would see it as truncated. With only that change,
> `extract_diff_paths` walks exactly the lines `git apply` sees, for every separator in the
> issue: `\r`, `\f`, `\v`, `\x1c`-`\x1e`, `\x85`, U+2028 and U+2029 all stay inside a line as
> ordinary content. The existing count-driven state machine and its "unexpected line … after
> a hunk" check then cover the rest, and any `\r` or `\x85` left in a header line is still
> rejected by the header regexes or the `isspace()` path check. Don't add the check that
> rejects any diff where `splitlines()` and `split("\n")` disagree: it would wrongly reject
> legitimate diffs of CRLF files or files containing form feeds, and it adds nothing once
> both parsers share git's line semantics. Instead, extend `test_diff_paths_match_git_apply`
> with the issue's repro for each separator, checked against the `_git_apply_paths` oracle,
> and add one test that a diff ending exactly at a hunk boundary with a trailing `\n` still
> gives the same result.

## Assumptions

- `git apply`'s CRLF tolerance (if any) is not a target this fix needs to replicate; a
  literal `\r` left inside a header or path by this fix's line-splitting continues to fail
  closed via the existing header regexes / whitespace check, per Requirement 6. The one
  header line where that did not hold (a mode line) is closed by Requirement 8.
- The exact test names/structure (which parametrize table each new case lands in) are left to
  implementation to fit the existing `tests/test_mechanism_candidate.py` style; Requirement 5
  fixes the required coverage, not the exact function/table names.

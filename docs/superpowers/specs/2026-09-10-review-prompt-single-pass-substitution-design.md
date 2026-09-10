# Single-pass placeholder substitution for reviewer/architect prompts

**Issue:** #400

## Overview / Problem statement

The conformance, architect, code-review, and product-owner reviewer prompts are all built the
same way: a phase command (`commands/*.md`) tells the *orchestrating LLM agent* to take a
template file (e.g. `.claude/skills/conformance/RUBRIC.md`) and produce the `prompt` string for
an `Agent` tool call by describing a sequence of independent substitutions in prose — "prompt:
`RUBRIC_CONTENT` with `$ARTIFACT_KIND` replaced with `PLAN`, `$SPEC_CONTENT` replaced with the
spec file contents, `$ARTIFACT_CONTENT` replaced with `$PLAN_CONTENT`" (`commands/dark-factory-
plan.md` Phase 3.5). There is no deterministic script backing this today — the orchestrator
performs the substitution by hand while composing the tool call.

Doing the substitutions as a chain of independent passes is unsafe: once the spec text has been
inlined by the `$SPEC_CONTENT` step, the next step (`$ARTIFACT_CONTENT`) re-scans the *entire*
string built so far, including the spec text that was just spliced in. If the spec itself quotes
one of the later placeholder tokens in prose — exactly what happens when a spec *documents this
class of bug*, e.g. #394's spec quoting `$SPEC_CONTENT`/`$ARTIFACT_CONTENT` in its Requirements
and Architecture sections — that later step matches inside the already-inlined spec text too,
splicing the (much larger) artifact/plan content into the middle of the spec. This garbled the
spec the reviewer was judging against and inflated the prompt roughly 3x on both reviewer passes
(Opus gate and Fable shadow) for #394; both were hand-patched around it at the time.

This is now a live risk for the *current* ticket as well: this spec, by necessity, quotes
`$SPEC_CONTENT`, `$ARTIFACT_CONTENT`, `$PLAN_CONTENT`, `$ISSUE_CONTEXT`, `$QA_HISTORY`, and
`$QUESTION` verbatim in prose (see Architecture below), so every reviewer pass this ticket goes
through — the plan's architect review, the plan's and implementation's conformance reviews, and
code review — is a live reproduction case for the exact bug being fixed.

Confirmed while auditing the codebase for this spec: an equivalent, already-correct
implementation of the fix already exists in this repo, just not wired into the production path.
`evals/skill_flow_spotcheck.py` (the #48 Tier-1 live-toggle spot-check harness) independently
implements `render_conformance_prompt`/`render_code_review_prompt` using a `string.Template`
subclass and `.safe_substitute(**all_values_at_once)` — a genuinely single-pass substitution,
because `string.Template.safe_substitute` performs one regex scan over the *original* template
text and never re-scans text it has already inserted. That harness is not affected by this bug.
The production command-file prose, however, was never updated to match.

## Requirements (from Q&A)

Two architectural questions were brainstormed with two independent product-owner passes over
the issue and codebase (full Q&A in the pipeline comment); both converged on the same answers:

1. **The fix must be a real, unit-testable function, not a reworded prose instruction.** The
   issue's own acceptance bar — "add a test with a spec fixture that literally contains
   `$SPEC_CONTENT` and assert the assembled prompt contains the spec text exactly once and the
   artifact exactly once" — is not achievable with pytest against prose that only an LLM
   executes at runtime. This repo's existing convention for prose-only command files is
   presence-assertion tests (`tests/test_rubric_skill_security.py`,
   `tests/test_conformance_prompt_formatter_rule.py`) that grep for required strings/ordering;
   those cannot assert anything about *assembled output*. A prior hand-patch of this exact bug
   (#394) already shows prose alone doesn't hold once an agent is composing the string by hand.
   Extracting the substitution into a script closes both gaps at once and is also what makes it
   cheap to apply the same fix at every call site (Requirement 2).
2. **Scope covers every call site with this shape, not just the two the issue names.** The issue
   names `commands/dark-factory-plan.md` Phase 3.5 and `commands/dark-factory-conformance.md`
   Step 3.1 (both feed `.claude/skills/conformance/RUBRIC.md`'s `$ARTIFACT_KIND`/
   `$SPEC_CONTENT`/`$ARTIFACT_CONTENT`). Auditing every placeholder-substitution site in the
   pipeline turned up three more with the identical chained-substitution shape:
   `commands/dark-factory-plan.md` Phase 3 (`refinement-skills/architect-prompt.md`'s
   `$SPEC_CONTENT`/`$PLAN_CONTENT`), `commands/dark-factory-code-review.md` Phase 3
   (`.claude/skills/code-review/RUBRIC.md`'s `$ISSUE_CONTEXT`/`$DIFF_CONTENT` — the reviewer
   pointed out this one is the *most* likely to fire next, since any diff touching these very
   command files literally contains the token `$DIFF_CONTENT`), and
   `commands/dark-factory-refine.md` Phase 4 (`refinement-skills/product-owner-prompt.md`'s
   `$ISSUE_CONTEXT`/`$QA_HISTORY`/`$QUESTION` — the mechanism that produced this very Q&A).
   Once one shared helper exists, fixing all five call sites (across four templates) costs the
   same as fixing two: each site changes from "replace $X with Y, then replace $Z with W" prose
   to one call to the shared renderer with all values supplied together. CLAUDE.md's scope rule
   is "touch only what the plan lists," not "only what the issue lists" — the plan lists all
   five sites explicitly so the conformance gate doesn't excise the other three as spillover.
3. **Reuse the proven technique, don't invent a new one.** `evals/skill_flow_spotcheck.py`
   already solves this with `string.Template` + `.safe_substitute()`. The new production helper
   uses the identical mechanism for consistency and because it's already validated stdlib
   behavior, rather than hand-rolling a regex substitution. `evals/skill_flow_spotcheck.py`
   itself is out of scope for this ticket — it does not have the bug, and it is a deliberately
   standalone harness (zero existing imports from `scripts/factory_core/` today); deduplicating
   it against the new shared helper is a plausible follow-up, not required to fix #400.
4. **Prompt template *content* files are unchanged.** The fix is entirely in how the command
   files assemble the prompt string — `.claude/skills/conformance/RUBRIC.md`,
   `.claude/skills/code-review/RUBRIC.md`, `refinement-skills/architect-prompt.md`, and
   `refinement-skills/product-owner-prompt.md` keep their `$PLACEHOLDER` tokens exactly as they
   are today. This avoids touching `.claude/skills/conformance/RUBRIC.md`'s byte-identity
   requirement against `refinement-skills/conformance-reviewer-prompt.md`
   (`tests/test_conformance_skill_files.py::test_rubric_matches_source_prompt_content`).
5. **Canonical command files live at `commands/*.md`, not `.archon/commands/*.md`.**
   `entrypoint.sh:675-678` copies the baked `/opt/dark-factory/commands` into
   `$CLONE_DIR/.archon/commands` only `if [ ! -d ... ]` and then excludes it from the clone diff
   — it is a per-run runtime shadow of the image's baked copy of this repo's own `commands/`
   directory, not a second source of truth (confirmed against `docs/superpowers/specs/2026-09-
   08-boundary-bypass-prevention-a6-design.md`, which lists `.archon/commands/**` as one of the
   paths that *shadows* baked factory enforcement and treats it as a factory-owned boundary
   path for exactly this reason). Editing `.archon/commands/` would be invisible upstream.
6. **A real regression test, matching the issue's literal acceptance criterion.** A pytest test
   builds a template containing `$SPEC_CONTENT` and `$ARTIFACT_CONTENT`, supplies a
   `SPEC_CONTENT` value that itself contains the literal substring `$ARTIFACT_CONTENT` (mirroring
   #394's spec), and asserts the rendered output contains the `ARTIFACT_CONTENT` value's text
   exactly once and the `SPEC_CONTENT` value's text exactly once, with no re-splicing.

## Architecture / Approach

**1. New shared helper — `scripts/factory_core/prompt_render.py`:**

```python
"""Single-pass $PLACEHOLDER substitution for reviewer/architect/product-owner prompt
templates (#400). Chained independent substitution — fill $SPEC_CONTENT, then separately
fill $ARTIFACT_CONTENT into the already-spec-inlined result — re-scans the spec text the
first pass just inserted; if the spec quotes $ARTIFACT_CONTENT in prose, the second pass
matches inside it too and splices the artifact into the middle of the spec (#394).

string.Template.safe_substitute avoids this: it performs one regex scan over the *original*
template and is never re-run against text it has already inserted. Same technique already
proven correct in evals/skill_flow_spotcheck.py's render_conformance_prompt (#48) — this
module is the production counterpart, invoked from commands/*.md via cli.py, not a
duplicate implementation.
"""
import string


class PromptTemplate(string.Template):
    delimiter = "$"


def render(template_text: str, **values: str) -> str:
    """Substitute every $NAME in `values` into `template_text` in one pass.
    Unknown $TOKENS not present in `values` are left untouched (safe_substitute)."""
    return PromptTemplate(template_text).safe_substitute(**values)
```

**2. New `cli.py` subcommand — `render-prompt`** (`scripts/factory_core/cli.py`, following the
existing `sub.add_parser(...)` / `set_defaults(func=...)` pattern used by every other
subcommand):

```
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template <path-to-template.md> \
  --set NAME=literal-value \
  --set NAME=@path-to-file-with-large-content \
  --out <path-to-write-assembled-prompt>
```

- `--set NAME=VALUE`: literal value (used for short values like `ARTIFACT_KIND=PLAN`).
- `--set NAME=@FILE`: reads the value from a file (used for large content — spec text, plan
  text, diff text, issue context, QA history — mirroring how `diff_rank.py` and
  `code_review_payload.py` already take large content via file arguments rather than argv, and
  avoiding any argv-length concern).
- `--out FILE`: write the assembled prompt there; omit to write to stdout.
- Implementation is a thin wrapper: read the template, build the `values` dict from `--set`
  entries, call `prompt_render.render(template_text, **values)`, write the result.

**3. Command-file changes — five call sites, four templates, one shared mechanism.** Each site
changes from "prompt: `<content>` with `$X` replaced with `Y`, `$Z` replaced with `W`" to:
"run `render-prompt` with all of that cycle's values, then pass the contents of `--out` verbatim
as the Agent tool's `prompt` argument." Worked example — `commands/dark-factory-conformance.md`
Step 3.1 (today's text is quoted in the Overview above):

```bash
python3 dark-factory/scripts/factory_core/cli.py render-prompt \  # TARGET-PATH
  --template "$RUBRIC_SOURCE" \
  --set ARTIFACT_KIND=IMPLEMENTATION \
  --set SPEC_CONTENT=@"$SPEC_CONTENT_FILE" \
  --set ARTIFACT_CONTENT=@"$ARTIFACTS_DIR/conformance_artifact_content.md" \
  --out "$ARTIFACTS_DIR/conformance_prompt.md"
```
(`$RUBRIC_SOURCE` is whichever of the clone-live/baked paths Step 3.1 already resolved;
`$SPEC_CONTENT_FILE` and the artifact-content file are written out by the existing steps that
today hold those values as shell variables — Step 3.1 already builds `$ARTIFACT_CONTENT` as a
heredoc, so that heredoc's output is written to a file instead of a variable used only for
in-place substitution.) The Agent tool's `prompt` is then the verbatim contents of
`$ARTIFACTS_DIR/conformance_prompt.md`. This applies identically to the shadow spawn (Step 3.1's
5a) and every reconcile-cycle re-spawn — same command, updated `--set` values for that cycle.

The other four sites follow the same shape:

| Template | Placeholders | Host command(s) |
|---|---|---|
| `.claude/skills/conformance/RUBRIC.md` | `$ARTIFACT_KIND`, `$SPEC_CONTENT`, `$ARTIFACT_CONTENT` | `commands/dark-factory-plan.md` Phase 3.5 (+ shadow 6a, reconcile 8e/f2); `commands/dark-factory-conformance.md` Step 3.1 (+ shadow 5a, reconcile respawn) |
| `refinement-skills/architect-prompt.md` | `$SPEC_CONTENT`, `$PLAN_CONTENT` | `commands/dark-factory-plan.md` Phase 3 (+ re-spawns on "Issues Found") |
| `.claude/skills/code-review/RUBRIC.md` | `$ISSUE_CONTEXT`, `$DIFF_CONTENT` | `commands/dark-factory-code-review.md` Phase 3 |
| `refinement-skills/product-owner-prompt.md` | `$ISSUE_CONTEXT`, `$QA_HISTORY`, `$QUESTION` | `commands/dark-factory-refine.md` Phase 4 |

**4. Tests:**

- `tests/test_prompt_render.py` (new): unit tests directly on `prompt_render.render`, including
  the issue's literal regression scenario — a `SPEC_CONTENT` value containing the literal text
  `$ARTIFACT_CONTENT`, asserting the rendered output contains the artifact text exactly once and
  the spec text exactly once (no re-splicing), plus a passthrough test for unknown `$TOKENS` and
  an order-independence test (dict key order doesn't change the result).
- `tests/test_cli_render_prompt.py` (new, or extend an existing `cli.py`-invocation test file if
  one already covers subcommand dispatch): exercises the `render-prompt` subcommand end-to-end —
  literal `--set` values, `@file` values, `--out` file writing.
- Update the existing presence-assertion tests that read `commands/dark-factory-plan.md` /
  `commands/dark-factory-conformance.md` / `commands/dark-factory-code-review.md` /
  `commands/dark-factory-refine.md` text (`tests/test_conformance_command_rubric_fallback.py`,
  `tests/test_plan_command_conformance_rubric_fallback.py`,
  `tests/test_conformance_command_shadow_review.py`,
  `tests/test_plan_command_shadow_conformance.py`) only if a specific assertion is invalidated by
  the wording change — a pass over these during implementation shows none of them assert the old
  "replaced with" phrasing itself (they check clone-live-fallback ordering, shadow-spawn
  description strings, and `SHADOW_DIALOGUE` scoping, all of which are unaffected), so they are
  expected to stay green unmodified; call this out explicitly in the plan so the conformance gate
  doesn't flag an untouched test file as a missed requirement.

## Alternatives considered

- **Prose-only fix** (reword the "replaced with" instructions to specify a safe order or
  `$`-escaping technique, verified only by a presence-assertion test). Rejected: cannot satisfy
  the issue's own acceptance criterion (a runtime assertion on assembled output), and this exact
  class of bug already survived one prior hand-patch (#394) while still prose-only — a rule an
  LLM must execute by hand on every spawn is not the same as a rule a script enforces.
- **Escape `$` in inlined content before each subsequent substitution step**, keeping the
  chained-substitution structure otherwise intact. Rejected: still requires new deterministic
  code (an escape/unescape step) to be testable, and is strictly more complex than switching to
  a single combined substitution call — the chain itself is the hazard, not the lack of escaping.
- **Scope to only the two sites the issue names.** Rejected per Requirement 2 — three more call
  sites share the identical hazard, one of them (code review) more likely to be hit soon than
  the two named sites, and the marginal cost of covering all five once the helper exists is
  minimal.
- **Deduplicate `evals/skill_flow_spotcheck.py` against the new helper in this same ticket.**
  Rejected for this ticket per Requirement 3 — that file doesn't have the bug, refactoring it
  introduces a new production-code dependency into a deliberately standalone eval harness, and
  it's not needed to close #400. Left as a non-blocking open question below.

## Open questions (non-blocking)

- Should `evals/skill_flow_spotcheck.py`'s `render_conformance_prompt`/`render_code_review_prompt`
  be refactored to import `scripts/factory_core/prompt_render.py` in a follow-up ticket, so there
  is exactly one implementation of this substitution instead of two (independently correct)
  ones? Deferred — not required to fix #400.
- Should the `render-prompt` CLI subcommand validate that every `$PLACEHOLDER` present in the
  template was supplied a value (fail loud on a missing `--set`), versus today's proposed
  `safe_substitute` behavior of silently leaving an unfilled placeholder as literal text? The
  current templates never have optional placeholders, so this doesn't change behavior for any
  known template today, but a stricter mode could catch a future typo'd `--set` name earlier.
  Left for planning/implementation to decide; either choice satisfies this spec's requirements.

## Assumptions

- [ASSUMPTION] `string.Template`'s default `idpattern` (`[_a-zA-Z][_a-zA-Z0-9]*`) is sufficient
  for every placeholder name in use today (`ARTIFACT_KIND`, `SPEC_CONTENT`, `ARTIFACT_CONTENT`,
  `PLAN_CONTENT`, `ISSUE_CONTEXT`, `DIFF_CONTENT`, `QA_HISTORY`, `QUESTION`) — confirmed by
  inspection, all are `[A-Z_]+`.
- [ASSUMPTION] None of the four templates' surrounding prose contains an incidental `$WORD` token
  that collides with one of these placeholder names but was not meant to be substituted —
  confirmed by inspection of all four template files during Phase 3 context assembly.

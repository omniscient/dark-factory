# Single-pass placeholder substitution for reviewer/architect prompts

**Operator spec gate:** 2026-09-10 (second pass) — approved with amendments after the first
version was rejected. The backtick-exclusion design was verified by running its own algorithm
against all five real templates: every template's bare-slot set is closed and matches its
`--set` keys exactly. Three amendments below.

**Issue:** #400

## Overview / problem statement

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
string built so far, including the spec text just spliced in. If the spec itself quotes one of
the later placeholder tokens in prose — exactly what happens when a spec *documents this class of
bug*, e.g. #394's spec quoting `$SPEC_CONTENT`/`$ARTIFACT_CONTENT` — that later step matches
inside the already-inlined spec text too, splicing the (much larger) artifact/plan content into
the middle of the spec. This garbled the spec the reviewer was judging against and inflated the
prompt roughly 3x on both reviewer passes (Opus gate and Fable shadow) for #394; both were
hand-patched around it at the time. This spec is itself a live reproduction case: it necessarily
quotes `$SPEC_CONTENT`, `$ARTIFACT_CONTENT`, `$PLAN_CONTENT`, `$ISSUE_CONTEXT`, `$QA_HISTORY`,
`$QUESTION`, `{FINDINGS_TEXT}`, and `{DIFF_CONTENT}` verbatim in prose, so every reviewer pass
this ticket goes through is a live exercise of the exact bug being fixed.

### Why the first attempt at this spec was rejected

The first version of this spec (rejected at the spec gate, 2026-09-10) proposed reusing
`string.Template.safe_substitute`, mirroring `evals/skill_flow_spotcheck.py`'s independent
(and, in isolation, correct) implementation of the same idea. That is **wrong for the real
templates**: both `.claude/skills/conformance/RUBRIC.md` and `.claude/skills/code-review/
RUBRIC.md` document their own placeholders in an `## Input` legend, using the placeholder token
itself, backtick-quoted:

```
.claude/skills/conformance/RUBRIC.md
  :8-10   legend, backtick-quoted: `$ARTIFACT_KIND` / `$SPEC_CONTENT` / `$ARTIFACT_CONTENT`
  :90,93,95,96                     real (bare) slots
.claude/skills/code-review/RUBRIC.md
  :10-11  legend, backtick-quoted: `$ISSUE_CONTEXT` / `$DIFF_CONTENT`
  :61,96  prose references, backtick-quoted (e.g. `` `bash -c "...$VAR..."` ``, `` `$DIFF_CONTENT` ``)
  :104,107                        real (bare) slots
```

`safe_substitute` replaces *every* occurrence, legend and real slot alike. Run against the real
files (not a synthetic fixture — the failure mode is invisible on a template with no legend),
that inlines the spec/artifact twice and the issue/diff twice on every single conformance and
code-review run, and destroys the `## Input` legend the reviewer reads to understand its own
inputs. That fails this issue's own "exactly once" acceptance bar and turns an *occasional*
~3x inflation (today it needs a spec that happens to quote a token) into a **guaranteed** one.

Fixing this at the template layer (e.g. escaping the legend as `$$ARTIFACT_KIND`) is not an
option: `.claude/skills/**` is a hard-excluded path (`.factory/adapter.yaml` `hard_exclude_paths`,
CLAUDE.md Hard limits) and requires human sign-off, not an agent-approved change, and it would
also require editing the byte-identical `refinement-skills/conformance-reviewer-prompt.md` twin
to keep `tests/test_conformance_skill_files.py::test_rubric_matches_source_prompt_content` green.
The fix must therefore live entirely in *how the command files assemble the prompt* — never in
the templates themselves.

## Requirements (from Q&A)

Two rounds of product-owner brainstorming (round 1: whether to script the fix and how wide to
scope it; round 2, this re-refinement: how to make the substitution itself correct against the
real templates) converged on:

1. **The fix must be a real, unit-testable function, not a reworded prose instruction**
   (unchanged from round 1) — the issue's acceptance bar (a pytest assertion on assembled output)
   is not achievable with prose an LLM executes by hand at runtime, and #394 already shows prose
   alone doesn't hold once an agent is composing the string manually.
2. **Substitution must distinguish real (bare) slots from legend/prose that quotes the same
   token, using the templates' own existing convention: legend/prose occurrences are always
   backtick-quoted; real slots never are.** Verified by direct inspection of all four `$`-style
   templates (line numbers above, plus `refinement-skills/architect-prompt.md` and
   `refinement-skills/product-owner-prompt.md`, which have no legend at all, so this rule is a
   no-op there — harmless, not just harmless-by-luck) and the `commands/dark-factory-revise-
   advisory.md` prompt chunk (`{}`-style; no legend, no backtick-quoted occurrences of
   `FINDINGS_TEXT`/`DIFF_CONTENT` anywhere in it, so backtick-exclusion is a no-op there too).
   This is **not** `string.Template` — it is a purpose-built regex substitution, because
   `string.Template.safe_substitute` has no concept of "quoted, don't touch" and cannot be
   configured to add one.
3. **Explicit per-call-site delimiter selection (`dollar` vs `brace`), not auto-detection or a
   combined regex.** Five call sites use bare `$NAME`; one (`dark-factory-revise-advisory.md`)
   uses bare `{NAME}`. Auto-detecting/handling both simultaneously in one pass is rejected: at
   least one `$`-style template already contains literal brace-shaped prose unrelated to
   substitution (`refinement-skills/product-owner-prompt.md:38`, `` /api/{resource} `` in an
   example answer), so a combined-delimiter scan would treat that as a substitution candidate the
   moment a `--set` key happened to be named `resource`. This is inert today only by coincidence
   of naming, and this is prompt-construction plumbing feeding a merge-gating reviewer verdict —
   not a place to rely on coincidence. An explicit `--delimiter` per call site is also
   self-documenting: each of the six command-file call sites states which syntax its template
   uses, rather than leaving that fact implicit.
4. **A mismatch between the template's bare slots and the supplied `--set` values fails loudly,
   in both directions** — a `--set` key with no matching bare slot (typo, or a stale value from a
   prior edit) AND a bare slot with no matching `--set` key. `string.Template.safe_substitute`'s
   traditional passthrough-of-unmatched-tokens behavior is explicitly rejected here: a typo'd
   `--set` name would silently leave the literal placeholder text (e.g. `$SPEC_CONTENT`) in the
   prompt the reviewer judges against, and that verdict gates a PR merge. The four templates'
   bare-slot sets are closed and verified by inspection (listed in Architecture below), so this is
   a checkable contract today, not a source of false failures, and it doubles as a drift guard if
   a template's slots ever change without the command file being updated to match.
   **Every command-file call site must treat a nonzero `render-prompt` exit as a hard stop for
   that phase** — no fallback to hand-substituting the raw template, no proceeding with an
   unrendered or partially-rendered prompt. This is a template/call-site wiring bug, not a
   reviewer-subagent failure, so it must never be absorbed into `code_review.fail_open` or the
   `UNCERTAIN`/`STATUS: ERROR` handling reserved for an actual reviewer subagent erroring —
   conflating the two would mask a genuine mechanical defect as an inconclusive review.
5. **Scope covers six call sites across five templates, not the two the issue names** (round 1
   found three more; this re-refinement's audit found a sixth): both feed
   `.claude/skills/conformance/RUBRIC.md` (`dark-factory-plan.md` Phase 3.5 + shadow/reconcile
   re-spawns; `dark-factory-conformance.md` Step 3.1 + shadow/reconcile re-spawns);
   `refinement-skills/architect-prompt.md` (`dark-factory-plan.md` Phase 3, `dollar`);
   `.claude/skills/code-review/RUBRIC.md` (`dark-factory-code-review.md` Phase 3, `dollar`);
   `refinement-skills/product-owner-prompt.md` (`dark-factory-refine.md` Phase 4, `dollar`); and
   `commands/dark-factory-revise-advisory.md`'s own embedded fix-agent prompt (Phase 3, `brace`
   — the sixth site, missed by the first spec attempt). `FINDINGS_TEXT` is human-readable review
   prose built from `.advisory[].description` in `review_result.json` and routinely quotes diff
   text (code, JSON) verbatim — a live instance of the identical hazard, not a hypothetical one.
   Once the shared mechanism exists, covering all six costs the same as covering two; a follow-up
   ticket would itself be refined through the very product-owner-prompt substitution that has the
   bug, so deferring any of these sites means the *next* phase this ticket goes through can
   reproduce it.
6. **Prompt template *content* files are unchanged.** `.claude/skills/conformance/RUBRIC.md`,
   `.claude/skills/code-review/RUBRIC.md`, `refinement-skills/architect-prompt.md`, and
   `refinement-skills/product-owner-prompt.md` keep their `$PLACEHOLDER` tokens exactly as they
   are today — this avoids the byte-identity hazard described above. `commands/dark-factory-
   revise-advisory.md`'s embedded prompt text is also byte-unchanged; only *where it is assembled
   from* changes (Architecture, point 4).
7. **Canonical command files live at `commands/*.md`, not `.archon/commands/*.md`.**
   `entrypoint.sh` copies the baked `/opt/dark-factory/commands` into `$CLONE_DIR/.archon/commands`
   only `if [ ! -d ... ]` and excludes it from the clone diff — it is a per-run runtime shadow of
   the image's baked copy of this repo's own `commands/` directory, not a second source of truth.
   Editing `.archon/commands/` would be invisible upstream.
8. **`$MEMORY_CONTEXT` is prepended, never substituted, and prepending happens strictly after
   rendering.** `commands/dark-factory-plan.md` Phase 3 prepends a `## Memory: Accumulated
   Patterns` section to the architect prompt ahead of the spec/plan content; this is the only one
   of the six call sites that has a memory-context step. The render call must never receive
   `MEMORY_CONTEXT` as a `--set` value (it isn't a template slot), and the prepend must happen to
   the *output* of `render-prompt`, never to the raw template before rendering — prepending first
   would re-inline arbitrary memory-entry prose ahead of the single-pass scan and could reproduce
   this exact bug from a new direction if a memory entry itself quotes `$SPEC_CONTENT`/
   `$PLAN_CONTENT` (plausible, since memory entries document lessons from tickets like this one).
9. **This narrows the failure mode; it does not make prompt assembly fully deterministic.**
   Every call site still requires the orchestrating LLM agent to read `render-prompt`'s `--out`
   file and copy its contents into the `Agent` tool's `prompt` argument by hand — nothing in this
   repo asserts `Agent.prompt == <rendered file contents>`. This closes the chained-re-scan/legend
   hazard (a mechanical bug in *how the string was built*) but does not remove the possibility of
   the orchestrating agent transcribing, summarizing, or truncating the file when composing the
   tool call. The spec should not be read as claiming full determinism it doesn't deliver.
10. **A real regression test matching the issue's literal acceptance criterion.** A pytest test
    builds a template containing `$SPEC_CONTENT` and `$ARTIFACT_CONTENT`, supplies a
    `SPEC_CONTENT` value that itself contains the literal substring `$ARTIFACT_CONTENT` (mirroring
    #394), and asserts the rendered output contains the `ARTIFACT_CONTENT` value's text exactly
    once and the `SPEC_CONTENT` value's text exactly once. A second test does the same against a
    template containing a backtick-quoted legend occurrence of a token (mirroring the real
    RUBRIC.md files) and asserts the legend text is preserved unchanged while the bare slot is
    substituted — this is the scenario the first spec's design did not survive.

## Architecture / Approach

**1. New shared helper — `scripts/factory_core/prompt_render.py`:**

```python
"""Single-pass, backtick-excluding $NAME / {NAME} substitution for reviewer/architect/
product-owner/fix-agent prompt templates (#400).

Chained independent substitution — fill $SPEC_CONTENT, then separately fill $ARTIFACT_CONTENT
into the already-spec-inlined result — re-scans the spec text the first pass just inserted; if
the spec quotes $ARTIFACT_CONTENT in prose, the second pass matches inside it too and splices the
artifact into the middle of the spec (#394). A single combined-pass substitution over the
*original* template text avoids that.

That alone is not sufficient against the real templates: .claude/skills/conformance/RUBRIC.md
and .claude/skills/code-review/RUBRIC.md document their own placeholders in an `## Input` legend,
quoting the token itself in backticks. A blind single-pass substitute (e.g.
string.Template.safe_substitute) still replaces the legend along with the real slot. This module
additionally treats any single-line, backtick-quoted span as protected text and never substitutes
inside it -- verified by inspection to separate every real (bare) slot from every legend/prose
occurrence in all five templates this module renders.
"""
import re


class PromptRenderError(ValueError):
    """Raised when the template's bare (non-backticked) slots and the supplied `values` keys
    don't match exactly. A mismatch always means either a typo'd key or a template slot nobody
    filled -- this feeds a merge-gating reviewer prompt, so it must fail loud, never substitute
    partially or pass through a literal $NAME/{NAME} left in the rendered text."""


_TOKEN_RE = {
    "dollar": re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)"),
    "brace": re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}"),
}
# Single-line inline-code spans only (matches how both real RUBRIC.md legends quote their
# placeholders) -- not general markdown parsing, not triple-backtick fences.
_BACKTICK_SPAN_RE = re.compile(r"`[^`\n]*`")


def _segments(template_text: str):
    """Splits template_text into (text, protected) pairs -- protected=True for backtick-quoted
    spans, which are copied through untouched and never scanned for tokens."""
    pos = 0
    for span in _BACKTICK_SPAN_RE.finditer(template_text):
        yield template_text[pos:span.start()], False
        yield span.group(0), True
        pos = span.end()
    yield template_text[pos:], False


def render(template_text: str, values: dict[str, str], delimiter: str = "dollar") -> str:
    token_re = _TOKEN_RE[delimiter]
    segments = list(_segments(template_text))

    bare_names: set[str] = set()
    for text, protected in segments:
        if not protected:
            bare_names.update(m.group(1) for m in token_re.finditer(text))

    missing = bare_names - values.keys()
    unused = values.keys() - bare_names
    if missing or unused:
        raise PromptRenderError(
            f"template/--set mismatch (delimiter={delimiter}): "
            f"missing={sorted(missing)} unused={sorted(unused)}"
        )

    return "".join(
        text if protected else token_re.sub(lambda m: values[m.group(1)], text)
        for text, protected in segments
    )
```

`render()` scans the *original* `template_text` exactly once per segment; substituted values are
inserted as literal replacement text and are never re-scanned for further tokens (this is what
closes the #394 chained-splice hazard — `re.sub` does not rescan its own replacement output).
Note this depends on passing a **function** to `re.sub`: a function's return value is used
literally, so a value containing `\1`, `\g<0>` or a stray backslash cannot be reinterpreted as a
backreference. A plain string replacement would reintroduce a corruption hazard on exactly the
inputs this ticket handles (diffs, specs). Keep the lambda.

The backtick-exclusion pass is what additionally closes the legend-inflation hazard the first
spec's design missed.

**Known limitation, and it cuts both ways (operator gate).** `_BACKTICK_SPAN_RE` matches
**single-line** inline-code spans only; it does *not* protect triple-backtick fenced blocks. That
is **required**, not incidental: site 5's `{DIFF_CONTENT}` slot lives inside a ```diff fence
(`commands/dark-factory-revise-advisory.md:76-79`) and must be substituted. Protecting fences
would break that site outright.

The cost of that choice must be stated rather than discovered later: **a placeholder name
appearing inside a fenced block in any template will be substituted, and Requirement 4's
fail-loud check cannot catch it.** A fenced `$SPEC_CONTENT` is a *bare* occurrence whose name
matches a supplied key, so `missing` and `unused` are both empty and `render()` silently inlines
the spec twice — this ticket's own bug, returning through a new door, in the one place its
guard is blind. No template does this today (verified: the bare-slot inventory below is exactly
the intended slot set for all five).

Requirement 11 adds the guard that makes that verification durable rather than a one-time
inspection.

11. **Drift guard pinning each template's bare-slot set.** A test enumerates, for each of the five
    templates, the bare (non-backtick-protected) token names under that template's declared
    delimiter, and asserts the set equals the expected literal set:

    | Template | Delimiter | Expected bare set |
    |---|---|---|
    | `.claude/skills/conformance/RUBRIC.md` | `dollar` | `{ARTIFACT_KIND, SPEC_CONTENT, ARTIFACT_CONTENT}` |
    | `.claude/skills/code-review/RUBRIC.md` | `dollar` | `{ISSUE_CONTEXT, DIFF_CONTENT}` |
    | `refinement-skills/architect-prompt.md` | `dollar` | `{SPEC_CONTENT, PLAN_CONTENT}` |
    | `refinement-skills/product-owner-prompt.md` | `dollar` | `{ISSUE_CONTEXT, QA_HISTORY, QUESTION}` |
    | site 5's embedded chunk | `brace` | `{FINDINGS_TEXT, DIFF_CONTENT}` |

    (Verified at the gate by executing the spec's own `_segments`/`render` logic against
    `origin/main`; all five match, and `product-owner-prompt.md` additionally yields `{resource}`
    under `brace` — inert because that site declares `dollar`, and exactly the collision
    Requirement 3 cites for refusing auto-detection.)

    This is the only thing that turns "no template does this today" into a property that stays
    true. Without it, adding a fenced example of a placeholder to a RUBRIC — a natural thing for
    a future author to do — silently doubles the reviewer's prompt with nothing reporting it.

**2. New `cli.py` subcommand — `render-prompt`** (`scripts/factory_core/cli.py`, following the
existing `sub.add_parser(...)` pattern):

```
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template <path-to-template.md> \
  --delimiter dollar|brace \        # default: dollar
  --set NAME=literal-value \
  --set NAME=@path-to-file-with-large-content \
  --out <path-to-write-assembled-prompt>
```

- `--set NAME=VALUE`: literal value (e.g. `ARTIFACT_KIND=PLAN`).
- `--set NAME=@FILE`: reads the value from a file (spec text, plan text, diff text, issue
  context, QA history, findings text — mirroring how `diff_rank.py` and `code_review_payload.py`
  already take large content via file arguments rather than argv).
- `--out FILE`: write the assembled prompt there; omit to write to stdout.
- On a `PromptRenderError`, print the message to stderr and exit non-zero. The command does not
  partially write `--out` on failure.
- Implementation: read the template, build the `values` dict from `--set` entries (resolving
  `@file` values), call `prompt_render.render(template_text, values, delimiter)`, write the
  result.

**3. Command-file changes — six call sites, five templates, one shared mechanism.** Each site
changes from "prompt: `<content>` with `$X` replaced with `Y`, `$Z` replaced with `W`" to: "run
`render-prompt` with all of that cycle's values; if it exits non-zero, stop the phase — do not
fall back to a hand-assembled prompt; otherwise pass the contents of `--out` verbatim as the
`Agent` tool's `prompt` argument."

| # | Template | Delimiter | Placeholders (bare slots, verified) | Host command(s) |
|---|---|---|---|---|
| 1 | `.claude/skills/conformance/RUBRIC.md` | `dollar` | `ARTIFACT_KIND`, `SPEC_CONTENT`, `ARTIFACT_CONTENT` (lines 90,93,95,96; legend at 8-10 backtick-quoted) | `dark-factory-plan.md` Phase 3.5 (+ shadow 6a, reconcile 8e/f2); `dark-factory-conformance.md` Step 3.1 (+ shadow 5a, reconcile re-spawn) |
| 2 | `refinement-skills/architect-prompt.md` | `dollar` | `SPEC_CONTENT`, `PLAN_CONTENT` (lines 66, 69; no legend) | `dark-factory-plan.md` Phase 3 (+ re-spawns on "Issues Found") |
| 3 | `.claude/skills/code-review/RUBRIC.md` | `dollar` | `ISSUE_CONTEXT`, `DIFF_CONTENT` (lines 104, 107; legend at 10-11 and prose refs at 61, 96 backtick-quoted) | `dark-factory-code-review.md` Phase 3 |
| 4 | `refinement-skills/product-owner-prompt.md` | `dollar` | `ISSUE_CONTEXT`, `QA_HISTORY`, `QUESTION` (lines 43, 46, 49; no legend; unrelated brace prose at line 38 is untouched because delimiter is `dollar`, not `brace`) | `dark-factory-refine.md` Phase 4 |
| 5 | Fix-agent prompt chunk, `commands/dark-factory-revise-advisory.md` Phase 3 | `brace` | `FINDINGS_TEXT`, `DIFF_CONTENT` (no legend; verified no backtick-quoted occurrences of either name anywhere in the chunk) | `dark-factory-revise-advisory.md` Phase 3 |

**Worked example — `commands/dark-factory-conformance.md` Step 3.1** (today's chained-prose text
is quoted in the Overview above; using the actual variable names this command already has —
`RUBRIC_CONTENT`, resolved by the existing clone-live-first step, and `SPEC_FILE`, a path that is
empty when `NO_SPEC=true`, not `RUBRIC_SOURCE`/`SPEC_CONTENT_FILE`, which don't exist):

```bash
if [ -n "$SPEC_FILE" ]; then
  SPEC_CONTENT_PATH="$SPEC_FILE"
else
  # NO_SPEC=true: review runs advisory-only against the issue body instead of a spec file.
  gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json body --jq '.body' \
    > "$ARTIFACTS_DIR/no_spec_issue_body.md"
  SPEC_CONTENT_PATH="$ARTIFACTS_DIR/no_spec_issue_body.md"
fi

printf '%s' "$RUBRIC_CONTENT" > "$ARTIFACTS_DIR/conformance_rubric.md"

# TARGET-PATH
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template "$ARTIFACTS_DIR/conformance_rubric.md" \
  --set ARTIFACT_KIND=IMPLEMENTATION \
  --set SPEC_CONTENT=@"$SPEC_CONTENT_PATH" \
  --set ARTIFACT_CONTENT=@"$ARTIFACTS_DIR/conformance_artifact_content.md" \
  --out "$ARTIFACTS_DIR/conformance_prompt.md" \
  || { echo "render-prompt failed — aborting conformance phase (see stderr above)"; exit 1; }
```

(`$ARTIFACTS_DIR/conformance_artifact_content.md` is the existing Step 3.1 `$ARTIFACT_CONTENT`
heredoc, written to a file instead of held only as a shell variable.) The Agent tool's `prompt` is
then the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`. This applies identically to
the shadow spawn (5a) and every reconcile-cycle re-spawn — same command, updated `--set` values.

**4. Sixth site — `commands/dark-factory-revise-advisory.md` Phase 3.** The fix-agent prompt is
currently embedded as a literal fenced block directly in the command file's own prose (Phase 3,
today's text), the only one of the six sites with no separate template file. Rather than
extracting it into a new standalone template file, the command file writes that same literal
boilerplate to an artifacts-dir file via heredoc, then renders it.

**Two authoring constraints for this site (operator gate).** The heredoc body contains a
```diff fence, so the surrounding markdown code block in `commands/dark-factory-revise-advisory.md`
must use a **four-backtick fence or `~~~`** — a three-backtick outer fence is terminated early by
the inner one, and the rest of the phase instructions would render as prose. And the delimiter
must stay **quoted** (`<<'PROMPT_EOF'`, as written): an unquoted heredoc would expand `$` and
backticks in the boilerplate before `render-prompt` ever sees it.

```bash
cat > "$ARTIFACTS_DIR/revise_advisory_template.md" <<'PROMPT_EOF'
You are a software engineer addressing advisory findings from a code review.
Your task: fix each finding below by editing the relevant source files.

## Advisory Findings

{FINDINGS_TEXT}

## Diff context (what was changed in this PR)

```diff
{DIFF_CONTENT}
```

## Instructions

For each finding:
1. Read the file at the given path.
2. Apply the fix the finding describes. Stay minimal — fix exactly what is flagged, no refactors.
3. Write the updated file.

When done, output a brief summary of what you changed (one line per finding).
Do NOT commit or push — the workflow handles that.
Do NOT modify test files unless the finding explicitly targets a test file.
PROMPT_EOF

printf '%s' "$FINDINGS_TEXT" > "$ARTIFACTS_DIR/revise_advisory_findings.md"
printf '%s' "$DIFF_CONTENT" > "$ARTIFACTS_DIR/revise_advisory_diff.md"

# TARGET-PATH
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template "$ARTIFACTS_DIR/revise_advisory_template.md" \
  --delimiter brace \
  --set FINDINGS_TEXT=@"$ARTIFACTS_DIR/revise_advisory_findings.md" \
  --set DIFF_CONTENT=@"$ARTIFACTS_DIR/revise_advisory_diff.md" \
  --out "$ARTIFACTS_DIR/revise_advisory_prompt.md" \
  || { echo "render-prompt failed — aborting revise-advisory phase (see stderr above)"; exit 1; }
```

The current line "Replace `{FINDINGS_TEXT}` with `$FINDINGS_TEXT` and `{DIFF_CONTENT}` with
`$DIFF_CONTENT`." is **deleted**, not reworded — it described the hand-substitution this ticket
removes.

**5. Memory-context ordering (`dark-factory-plan.md` Phase 3, the only site with a memory step):**
render the architect prompt first, then prepend:

```bash
# TARGET-PATH
python3 dark-factory/scripts/factory_core/cli.py render-prompt \
  --template /opt/refinement-skills/architect-prompt.md \
  --set SPEC_CONTENT=@"$SPEC_FILE" \
  --set PLAN_CONTENT=@"$ARTIFACTS_DIR/plan_content.md" \
  --out "$ARTIFACTS_DIR/architect_prompt_body.md" \
  || { echo "render-prompt failed — aborting plan phase (see stderr above)"; exit 1; }

if [ -n "$MEMORY_CONTEXT" ]; then
  { printf '## Memory: Accumulated Patterns\n%s\n\n---\n' "$MEMORY_CONTEXT"; \
    cat "$ARTIFACTS_DIR/architect_prompt_body.md"; } > "$ARTIFACTS_DIR/architect_prompt.md"
else
  cp "$ARTIFACTS_DIR/architect_prompt_body.md" "$ARTIFACTS_DIR/architect_prompt.md"
fi
```

`$MEMORY_CONTEXT` is never passed to `render-prompt` as a `--set` value (it is not a template
slot) and is concatenated only after rendering completes — so a memory entry that itself quotes
`$SPEC_CONTENT`/`$PLAN_CONTENT` (plausible, once this ticket's lessons are written to memory)
cannot be re-scanned for tokens.

**6. Tests:**

- `tests/test_prompt_render.py` (new): unit tests directly on `prompt_render.render`:
  - The issue's literal regression scenario — a `SPEC_CONTENT` value containing the literal text
    `$ARTIFACT_CONTENT`; assert the rendered output contains the artifact text exactly once and
    the spec text exactly once.
  - A backtick-legend scenario mirroring the real RUBRIC.md files — a template with a
    backtick-quoted legend occurrence of `$SPEC_CONTENT` and a bare slot elsewhere; assert the
    legend text is preserved unchanged and only the bare slot is substituted.
  - `delimiter="brace"` equivalents of both scenarios above.
  - A missing-`--set` case and an unused-`--set` case, each asserting `PromptRenderError`.
  - An order-independence test (dict key iteration order doesn't change the result).
- `tests/test_cli_render_prompt.py` (new): exercises the `render-prompt` subcommand end-to-end —
  literal `--set` values, `@file` values, `--out` file writing, non-zero exit + stderr message on
  a `PromptRenderError`.
- Run the existing presence-assertion command-text tests
  (`tests/test_conformance_command_rubric_fallback.py`,
  `tests/test_plan_command_conformance_rubric_fallback.py`,
  `tests/test_conformance_command_shadow_review.py`,
  `tests/test_plan_command_shadow_conformance.py`) and update only if a specific assertion is
  invalidated by the wording change — a pass over these shows none assert the old "replaced with"
  phrasing itself (they check clone-live-fallback ordering, shadow-spawn description strings, and
  `SHADOW_DIALOGUE` scoping, all unaffected); call this out in the plan so the conformance gate
  doesn't flag an untouched test file as a missed requirement.
- `tests/test_code_review_prompt.py` and `tests/test_conformance_skill_files.py` assert against
  the RUBRIC.md files' own content, which is unchanged — expected to stay green unmodified.

## Alternatives considered

- **`string.Template.safe_substitute` over the whole template** (the first spec's proposal).
  Rejected: proven wrong against the real RUBRIC.md files, which quote their own placeholders in
  a backtick-quoted legend that `safe_substitute` cannot distinguish from a real slot — it would
  guarantee the ~3x inflation on every run instead of fixing it.
- **Escape the legend in the templates** (`$$ARTIFACT_KIND` or similar), keeping
  `string.Template`. Rejected: requires editing `.claude/skills/**`, a hard-excluded path needing
  human sign-off, plus its byte-identical `refinement-skills/` twin to keep the identity test
  green — not something this ticket can decide unilaterally.
- **Prose-only fix** (reword the "replaced with" instructions, verified only by a
  presence-assertion test). Rejected: cannot satisfy the issue's own acceptance criterion (a
  runtime assertion on assembled output), and this exact class of bug already survived one prior
  hand-patch (#394) while still prose-only.
- **Auto-detect or simultaneously handle both `$` and `{}` delimiters in one combined regex.**
  Rejected: at least one `$`-style template already contains unrelated brace-shaped prose
  (`product-owner-prompt.md:38`, `/api/{resource}`); a combined pass would treat that as a
  substitution candidate the moment a `--set` key happened to share that name. An explicit
  per-call-site `--delimiter` avoids relying on that coincidence.
- **Silent passthrough of unmatched/unused tokens** (`safe_substitute`'s traditional behavior, the
  first spec's open non-blocking question). Rejected on reconsideration: a typo'd `--set` name
  would silently ship a prompt containing the literal placeholder text to a subagent whose verdict
  gates a PR merge; failing loud in both directions is checkable today (the bare-slot sets are
  closed and verified) and catches future template/call-site drift for free.
- **Scope to only the two sites the issue names, or the five the first spec found.** Rejected —
  the sixth site (`dark-factory-revise-advisory.md`) shares the identical hazard with live,
  diff-quoting input; deferring it risks reproducing the bug the next time this exact ticket class
  is refined.
- **Extract the revise-advisory prompt into a new standalone `refinement-skills/` template file**,
  matching how the other four templates are organized. Considered and not chosen for this ticket:
  it is the only site where the prompt text lives inline in the command file rather than a
  separate file, and writing the same literal text to an `$ARTIFACTS_DIR` file at render time
  achieves the identical single-pass, backtick-excluding substitution without introducing a new
  file location or a baked-vs-clone-live resolution question that doesn't otherwise exist for this
  site.
- **Deduplicate `evals/skill_flow_spotcheck.py` against the new helper in this ticket.** Rejected
  for this ticket: that harness still uses `string.Template.safe_substitute` directly and, per the
  operator's rejection finding, has never actually been exercised against the real RUBRIC.md files
  (its own tests use synthetic templates with no legend) — it likely carries the same latent
  legend-inflation defect this ticket fixes in production, but it is a standalone eval harness
  with zero existing imports from `scripts/factory_core/`, not on the merge-gating path, and
  fixing/deduplicating it is a plausible non-blocking follow-up (left as an open question below),
  not required to close #400.

## Open questions (non-blocking)

- Should `evals/skill_flow_spotcheck.py`'s `render_conformance_prompt`/`render_code_review_prompt`
  be refactored to import `scripts/factory_core/prompt_render.py` (and, separately, have its own
  latent legend-inflation defect fixed) in a follow-up ticket? Deferred — not required to fix
  #400, and that harness is not part of the merge-gating path.
- Should `render-prompt` support the same regression-shaped fixture harness (`.claude/skills/
  refinement/config.yaml`-style bench) so future template edits get an automatic real-file check
  rather than relying on the unit tests' synthetic mirrors of the real legend shape? Left for
  planning/implementation.

## Assumptions

- [ASSUMPTION] Every placeholder name in use today across all five templates matches
  `[A-Za-z_][A-Za-z0-9_]*` (all are `[A-Z_]+`) — confirmed by inspection.
- [ASSUMPTION] The bare-slot sets listed in Architecture point 3's table are exhaustive and closed
  for all five templates as they exist today — confirmed by inspection during this refinement's
  Phase 3 context assembly (not by a generic markdown parse of arbitrary future template edits;
  a future template change that adds a slot or a new backtick-quoted legend entry stays correct
  automatically as long as the same bare-vs-backtick-quoted convention is followed, and the
  fail-loud mismatch check in Architecture point 4 will surface a violation of that convention).
- [ASSUMPTION] `_BACKTICK_SPAN_RE`'s single-line, single-backtick-pair matching (no triple-backtick
  fence awareness) is sufficient for all five templates: verified by inspection that no
  placeholder token in any of the five templates sits on the same line as a stray/unpaired
  backtick fence marker, so the fence markers in `.claude/skills/code-review/RUBRIC.md`'s Output
  Format block and `commands/dark-factory-revise-advisory.md`'s `` ```diff `` block never
  interfere with token detection on the lines that actually hold `{FINDINGS_TEXT}`/
  `{DIFF_CONTENT}` or any other real slot.

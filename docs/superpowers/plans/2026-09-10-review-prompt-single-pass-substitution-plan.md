# Implementation Plan: Single-pass placeholder substitution for reviewer/architect prompts

**Issue:** #400
**Spec:** `docs/superpowers/specs/2026-09-10-review-prompt-single-pass-substitution-design.md`
**Related:** #394 (live reproduction of the chained-splice bug)

---

## Goal

Replace the hand-substitution prose ("prompt: `RUBRIC_CONTENT` with `$X` replaced with `Y`, `$Z`
replaced with `W`") at all six reviewer/architect/product-owner/fix-agent prompt-assembly call
sites with a single deterministic mechanism: a new `scripts/factory_core/prompt_render.py`
module (`render()`), invoked via a new `render-prompt` subcommand on
`scripts/factory_core/cli.py`. `render()` scans each template's *original* text exactly once,
substituting only bare (non-backtick-quoted) `$NAME`/`{NAME}` tokens, so a spec/artifact/findings
value that itself quotes a placeholder token in prose can never be re-scanned and spliced a
second time (#394), and a RUBRIC's own backtick-quoted `## Input` legend is never mistaken for a
real slot. A `--set`/template mismatch in either direction raises `PromptRenderError` and exits
non-zero — every command-file call site treats that as a hard stop for the phase, never a
fallback to hand-substitution.

## Architecture

```
scripts/factory_core/prompt_render.py                    (new)
  PromptRenderError(ValueError)
  _TOKEN_RE = {"dollar": ..., "brace": ...}
  _BACKTICK_SPAN_RE                                        (single-line inline-code spans only)
  _segments(template_text) -> Iterator[(text, protected: bool)]
  render(template_text, values, delimiter="dollar") -> str  (raises PromptRenderError on
                                                               missing/unused keys)
        │ imported by
        ▼
scripts/factory_core/cli.py
  + sub.add_parser("render-prompt")
      --template PATH --delimiter {dollar,brace} --set NAME=VALUE|NAME=@FILE (repeatable) --out PATH
  + _render_prompt(args)   (reads template, resolves @file values, calls render(), writes --out
                             or stdout; PromptRenderError -> stderr + exit 1, no partial --out)

commands/dark-factory-plan.md          Phase 3 (architect) + Phase 3.5 (conformance, + shadow
                                        6a, + reconcile 8d2/e/f2) call `render-prompt` instead of
                                        prose substitution
commands/dark-factory-conformance.md   Step 3.1 (+ shadow 5a) + Phase 3.5 reconcile loop
commands/dark-factory-code-review.md   Phase 3
commands/dark-factory-refine.md        Phase 4
commands/dark-factory-revise-advisory.md  Phase 3 (embedded template written to a heredoc file,
                                           then rendered with `--delimiter brace`)
```

| # | Template | Delimiter | Bare slots | Host command(s) |
|---|---|---|---|---|
| 1 | `.claude/skills/conformance/RUBRIC.md` | `dollar` | `ARTIFACT_KIND`, `SPEC_CONTENT`, `ARTIFACT_CONTENT` | `dark-factory-plan.md` Phase 3.5; `dark-factory-conformance.md` Step 3.1 |
| 2 | `refinement-skills/architect-prompt.md` | `dollar` | `SPEC_CONTENT`, `PLAN_CONTENT` | `dark-factory-plan.md` Phase 3 |
| 3 | `.claude/skills/code-review/RUBRIC.md` | `dollar` | `ISSUE_CONTEXT`, `DIFF_CONTENT` | `dark-factory-code-review.md` Phase 3 |
| 4 | `refinement-skills/product-owner-prompt.md` | `dollar` | `ISSUE_CONTEXT`, `QA_HISTORY`, `QUESTION` | `dark-factory-refine.md` Phase 4 |
| 5 | embedded chunk, `commands/dark-factory-revise-advisory.md` Phase 3 | `brace` | `FINDINGS_TEXT`, `DIFF_CONTENT` | `dark-factory-revise-advisory.md` Phase 3 |

Template *content* files (rows 1-4) are byte-unchanged — only where each command file assembles
the prompt *from* changes. Row 5's embedded boilerplate text is also byte-unchanged; it moves
from an inline fenced block to a heredoc written at `$ARTIFACTS_DIR/revise_advisory_template.md`
before rendering.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/prompt_render.py` | New — `render()`, `PromptRenderError` |
| `tests/test_prompt_render.py` | New — unit tests + drift guard (Requirement 11, 4 templates) |
| `scripts/factory_core/cli.py` | Modified — `render-prompt` subcommand |
| `tests/test_cli_render_prompt.py` | New — end-to-end CLI tests |
| `commands/dark-factory-plan.md` | Modified — Phase 3 + Phase 3.5 use `render-prompt` |
| `tests/test_plan_command_render_prompt.py` | New — presence assertions |
| `commands/dark-factory-conformance.md` | Modified — Step 3.1 + reconcile loop use `render-prompt` |
| `tests/test_conformance_command_render_prompt.py` | New — presence assertions |
| `commands/dark-factory-code-review.md` | Modified — Phase 3 uses `render-prompt` |
| `tests/test_code_review_command_render_prompt.py` | New — presence assertions |
| `commands/dark-factory-refine.md` | Modified — Phase 4 uses `render-prompt` |
| `tests/test_refine_command_render_prompt.py` | New — presence assertions |
| `commands/dark-factory-revise-advisory.md` | Modified — Phase 3 heredoc + `render-prompt --delimiter brace` |
| `tests/test_revise_advisory_command_render_prompt.py` | New — presence assertions + embedded-template drift guard |

---

## Task 1: `scripts/factory_core/prompt_render.py` — the render engine

**Files:** `scripts/factory_core/prompt_render.py` (new), `tests/test_prompt_render.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_prompt_render.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core.prompt_render import render, PromptRenderError, _segments, _TOKEN_RE

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_single_pass_prevents_chained_splice():
    # #394's exact failure shape: the spec text itself quotes the later placeholder.
    template = "### Spec\n$SPEC_CONTENT\n\n### Artifact\n$ARTIFACT_CONTENT\n"
    spec_text = "The bug occurs when a spec quotes $ARTIFACT_CONTENT in prose."
    artifact_text = "diff --git a/x.py b/x.py\n+ real artifact content"
    result = render(template, {"SPEC_CONTENT": spec_text, "ARTIFACT_CONTENT": artifact_text})
    assert result.count(spec_text) == 1
    assert result.count(artifact_text) == 1


def test_backtick_quoted_legend_untouched_bare_slot_substituted():
    # Mirrors the real RUBRIC.md files' `## Input` legend shape.
    template = (
        "## Input\n"
        "- `$SPEC_CONTENT`: the full spec text\n"
        "- `$ARTIFACT_CONTENT`: the artifact text\n\n"
        "### Spec\n$SPEC_CONTENT\n\n### Artifact\n$ARTIFACT_CONTENT\n"
    )
    result = render(template, {"SPEC_CONTENT": "SPEC-BODY", "ARTIFACT_CONTENT": "ARTIFACT-BODY"})
    assert "- `$SPEC_CONTENT`: the full spec text" in result
    assert "- `$ARTIFACT_CONTENT`: the artifact text" in result
    assert "### Spec\nSPEC-BODY" in result
    assert "### Artifact\nARTIFACT-BODY" in result


def test_single_pass_prevents_chained_splice_brace_delimiter():
    template = "Findings:\n{FINDINGS_TEXT}\n\nDiff:\n{DIFF_CONTENT}\n"
    findings_text = "The diff below quotes {DIFF_CONTENT} verbatim."
    diff_text = "+ real diff line"
    result = render(
        template, {"FINDINGS_TEXT": findings_text, "DIFF_CONTENT": diff_text}, delimiter="brace"
    )
    assert result.count(findings_text) == 1
    assert result.count(diff_text) == 1


def test_backtick_quoted_legend_untouched_bare_slot_substituted_brace_delimiter():
    template = (
        "## Input\n- `{FINDINGS_TEXT}`: the findings text\n\nFindings:\n{FINDINGS_TEXT}\n"
    )
    result = render(template, {"FINDINGS_TEXT": "FINDINGS-BODY"}, delimiter="brace")
    assert "- `{FINDINGS_TEXT}`: the findings text" in result
    assert "Findings:\nFINDINGS-BODY" in result


def test_missing_set_key_raises():
    template = "$SPEC_CONTENT and $ARTIFACT_CONTENT"
    with pytest.raises(PromptRenderError):
        render(template, {"SPEC_CONTENT": "x"})


def test_unused_set_key_raises():
    template = "$SPEC_CONTENT"
    with pytest.raises(PromptRenderError):
        render(template, {"SPEC_CONTENT": "x", "ARTIFACT_CONTENT": "y"})


def test_render_order_independent_of_dict_iteration():
    template = "$A / $B / $C"
    values_1 = {"A": "1", "B": "2", "C": "3"}
    values_2 = {"C": "3", "A": "1", "B": "2"}
    assert render(template, values_1) == render(template, values_2) == "1 / 2 / 3"


# --- Requirement 11: drift guard pinning each template's bare-slot set ---
# (site 5's embedded {}-delimiter chunk is guarded in test_revise_advisory_command_render_prompt.py
# — Task 7 — since it doesn't exist as a standalone file until that command-file edit lands.)
EXPECTED_BARE_SLOTS = [
    (REPO_ROOT / ".claude/skills/conformance/RUBRIC.md", "dollar",
     {"ARTIFACT_KIND", "SPEC_CONTENT", "ARTIFACT_CONTENT"}),
    (REPO_ROOT / ".claude/skills/code-review/RUBRIC.md", "dollar",
     {"ISSUE_CONTEXT", "DIFF_CONTENT"}),
    (REPO_ROOT / "refinement-skills/architect-prompt.md", "dollar",
     {"SPEC_CONTENT", "PLAN_CONTENT"}),
    (REPO_ROOT / "refinement-skills/product-owner-prompt.md", "dollar",
     {"ISSUE_CONTEXT", "QA_HISTORY", "QUESTION"}),
]


def _bare_names(text, delimiter):
    token_re = _TOKEN_RE[delimiter]
    names = set()
    for segment_text, protected in _segments(text):
        if not protected:
            names.update(m.group(1) for m in token_re.finditer(segment_text))
    return names


@pytest.mark.parametrize("path,delimiter,expected", EXPECTED_BARE_SLOTS)
def test_real_template_bare_slot_sets_match_expected(path, delimiter, expected):
    assert _bare_names(path.read_text(encoding="utf-8"), delimiter) == expected
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_prompt_render.py -x -v
   ```
   Expected: `ModuleNotFoundError: No module named 'factory_core.prompt_render'` (collection error).

3. Implement `scripts/factory_core/prompt_render.py`:

```python
"""Single-pass, backtick-excluding $NAME / {NAME} substitution for reviewer/architect/
product-owner/fix-agent prompt templates (#400).

Chained independent substitution -- fill $SPEC_CONTENT, then separately fill $ARTIFACT_CONTENT
into the already-spec-inlined result -- re-scans the spec text the first pass just inserted; if
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


def render(template_text: str, values: dict, delimiter: str = "dollar") -> str:
    token_re = _TOKEN_RE[delimiter]
    segments = list(_segments(template_text))

    bare_names = set()
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

4. Verify pass:
   ```bash
   python -m pytest tests/test_prompt_render.py -v
   ```
   Expected: all tests pass (11 collected: 7 direct + 4 parametrized drift-guard cases).

5. Commit:
   ```bash
   git add scripts/factory_core/prompt_render.py tests/test_prompt_render.py
   git commit -m "feat(#400): single-pass, backtick-excluding prompt_render.render()"
   ```

---

## Task 2: `render-prompt` CLI subcommand

**Files:** `scripts/factory_core/cli.py` (modified), `tests/test_cli_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_cli_render_prompt.py
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "factory_core" / "cli.py"


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(CLI), "render-prompt", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_render_prompt_literal_set_values_writes_out_file(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("Hello $NAME, kind is $KIND\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", "NAME=World", "--set", "KIND=PLAN",
         "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "Hello World, kind is PLAN\n"


def test_render_prompt_at_file_set_value(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("Body:\n$BODY\n", encoding="utf-8")
    body_file = tmp_path / "body.md"
    body_file.write_text("large content here", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", f"BODY=@{body_file}", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "Body:\nlarge content here\n"


def test_render_prompt_missing_set_key_exits_nonzero_with_stderr_and_no_partial_out(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("$NAME and $KIND\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", "NAME=World", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()
    assert not out.exists()


def test_render_prompt_brace_delimiter(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("{A}-{B}\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--delimiter", "brace",
         "--set", "A=1", "--set", "B=2", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "1-2\n"


def test_render_prompt_prints_to_stdout_when_no_out(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("$NAME\n", encoding="utf-8")
    result = _run(["--template", str(template), "--set", "NAME=World"], cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout == "World\n"
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_cli_render_prompt.py -x -v
   ```
   Expected: every test fails — `argparse: error: argument cmd: invalid choice: 'render-prompt'`
   (nonzero exit, but not the failure mode any test expects).

3. Implement. In `scripts/factory_core/cli.py`, add a handler function near the other
   `_*` handlers (e.g. after `_markers_regex`):

```python
def _render_prompt(args):
    from factory_core.prompt_render import render, PromptRenderError

    template_text = Path(args.template).read_text(encoding="utf-8")
    values = {}
    for item in args.set:
        name, _, value = item.partition("=")
        if value.startswith("@"):
            value = Path(value[1:]).read_text(encoding="utf-8")
        values[name] = value

    try:
        rendered = render(template_text, values, delimiter=args.delimiter)
    except PromptRenderError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
```

   Then, in `main()`, add the subparser registration immediately before the
   `parsed = parser.parse_args()` line:

```python
    rp = sub.add_parser("render-prompt")
    rp.add_argument("--template", required=True)
    rp.add_argument("--delimiter", choices=["dollar", "brace"], default="dollar")
    rp.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    rp.add_argument("--out", default="")
    rp.set_defaults(func=_render_prompt)

```

4. Verify pass:
   ```bash
   python -m pytest tests/test_cli_render_prompt.py tests/test_prompt_render.py tests/test_factory_core_cli.py -v
   ```
   Expected: all pass — including the pre-existing `test_factory_core_cli.py` suite (unaffected).

5. Commit:
   ```bash
   git add scripts/factory_core/cli.py tests/test_cli_render_prompt.py
   git commit -m "feat(#400): add render-prompt CLI subcommand"
   ```

---

## Task 3: `commands/dark-factory-plan.md` — Phase 3 (architect) + Phase 3.5 (conformance)

**Files:** `commands/dark-factory-plan.md` (modified), `tests/test_plan_command_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_plan_command_render_prompt.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"


def test_phase1_binds_spec_file_variable():
    text = CMD.read_text(encoding="utf-8")
    assert "SPEC_FILE" in text


def test_phase2_binds_plan_file_variable():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 2: PLAN WRITING")
    phase2 = text[idx:text.find("## Phase 3: ARCHITECT REVIEW")]
    assert "PLAN_FILE" in phase2


def test_phase3_architect_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    assert "with `$SPEC_CONTENT` and `$PLAN_CONTENT` replaced with the actual file contents" \
        not in text
    idx = text.find("## Phase 3: ARCHITECT REVIEW")
    phase3 = text[idx:text.find("## Phase 3.5")]
    assert "render-prompt" in phase3
    assert "--template /opt/refinement-skills/architect-prompt.md" in phase3
    assert '--set SPEC_CONTENT=@"$SPEC_FILE"' in phase3
    assert '--set PLAN_CONTENT=@"$PLAN_FILE"' in phase3
    assert "architect_prompt.md" in phase3
    assert "render-prompt failed" in phase3


def test_phase35_conformance_uses_render_prompt():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: CONFORMANCE REVIEW")
    phase35 = text[idx:text.find("## Phase 4")]
    assert "--set ARTIFACT_KIND=PLAN" in phase35
    assert '--set SPEC_CONTENT=@"$SPEC_FILE"' in phase35
    assert '--set ARTIFACT_CONTENT=@"$PLAN_FILE"' in phase35
    assert "conformance_prompt.md" in phase35
    assert "render-prompt failed" in phase35
    assert "- `$ARTIFACT_KIND` replaced with `PLAN`" not in phase35


def test_phase35_shadow_and_reconcile_reuse_rendered_prompt_file():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: CONFORMANCE REVIEW")
    phase35 = text[idx:text.find("## Phase 4")]
    assert "conformance_prompt.md` the Opus call just read this cycle" in phase35
    reconcile_idx = phase35.find("**Reconcile loop** (only if MATERIAL)")
    assert reconcile_idx != -1
    reconcile = phase35[reconcile_idx:]
    assert "render-prompt" in reconcile
    assert "conformance_prompt.md" in reconcile
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_plan_command_render_prompt.py -x -v
   ```
   Expected: `test_phase1_binds_spec_file_variable` passes by coincidence only if `SPEC_FILE`
   already appears elsewhere; `test_phase2_binds_plan_file_variable` and the `render-prompt`
   assertions fail (string not found).

3. Implement. In `commands/dark-factory-plan.md`:

   **Phase 1** — after step 6 ("Read the spec file..."), add a new step 6a binding the
   discovered path (Phase 3/3.5 need a file path, not just the read content):

   Replace:
   ```
   6. Read the spec file (fallback branch of step 1, if `## spec` was absent or empty from the pack)
   7. Compute the affected file set and load memory context:
   ```
   With:
   ```
   6. Read the spec file (fallback branch of step 1, if `## spec` was absent or empty from the pack)
   6a. Bind the discovered spec file's path to `SPEC_FILE` — used by Phase 3's and Phase 3.5's
       `render-prompt` calls below (`--set SPEC_CONTENT=@"$SPEC_FILE"`). If the context pack's
       `## spec` section was used instead of a discovered file (step 1), write its text to
       `$ARTIFACTS_DIR/spec_content.md` and set `SPEC_FILE="$ARTIFACTS_DIR/spec_content.md"`.
   7. Compute the affected file set and load memory context:
   ```

   **Phase 2** — bind the saved plan file's own path too (Phase 3/3.5 read the plan's current
   on-disk content directly via this path — never through a `$PLAN_CONTENT` shell variable,
   which is never assigned anywhere in this file; `$PLAN_CONTENT` in the prose below and
   elsewhere in this command always means "the plan document's current text", not an exported
   variable):

   Replace:
   ```
   Write a full implementation plan following these conventions:
   - Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
   ```
   With:
   ```
   Write a full implementation plan following these conventions:
   - Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, and bind that path to `PLAN_FILE`
     — used by Phase 3's and Phase 3.5's `render-prompt` calls below
     (`--set PLAN_CONTENT=@"$PLAN_FILE"`). Because `PLAN_FILE` points at the on-disk file rather
     than a copy, every later revision (an architect "Issues Found" fix, or a conformance
     reconcile-loop edit) is automatically picked up by the next `render-prompt` call with no
     separate re-copy step — just re-run the same `render-prompt` invocation after saving the
     revised plan.
   ```

   **Phase 3** — replace the spawn block:

   Replace:
   ````
   Prepend `$MEMORY_CONTEXT` to the architect prompt as a "## Memory: Accumulated Patterns" section immediately before the Spec and Plan content. If `$MEMORY_CONTEXT` is empty (no relevant files exist yet), omit the section entirely.

   Spawn an architect subagent using the Agent tool:
   - `description`: "Architect review: validate plan against spec"
   - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every re-spawn in the review cycle below too)
   - `prompt`: Content of `architect-prompt.md` with `$SPEC_CONTENT` and `$PLAN_CONTENT` replaced with the actual file contents, and with `$MEMORY_CONTEXT` prepended as shown:

     ```
     ## Memory: Accumulated Patterns
     $MEMORY_CONTEXT

     ---
     [architect-prompt.md content with $SPEC_CONTENT and $PLAN_CONTENT filled in]
     ```
   ````
   With:
   ````
   Render the architect prompt via `render-prompt`, then prepend `$MEMORY_CONTEXT` — never the
   other way around (prepending first could re-inline a memory entry's own mention of
   `$SPEC_CONTENT`/`$PLAN_CONTENT` into the single-pass scan and reproduce this ticket's bug from
   a new direction). `$MEMORY_CONTEXT` itself is prose, not a shell variable that survives into
   this Bash call — `load_memory_context.sh` (Phase 1 step 7) already writes its resolved text to
   `$ARTIFACTS_DIR/memory-context.md`, so read that file rather than the variable (never inline
   memory-entry text into a double-quoted shell string either — memory entries routinely contain
   backticks and `$`, the same command-substitution hazard `render-prompt` exists to avoid).
   `load_memory_context.sh` always writes a trailing newline even when empty, so `[ -s ]` is not a
   valid emptiness test; strip whitespace first:

   ```bash
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py render-prompt \
     --template /opt/refinement-skills/architect-prompt.md \
     --set SPEC_CONTENT=@"$SPEC_FILE" \
     --set PLAN_CONTENT=@"$PLAN_FILE" \
     --out "$ARTIFACTS_DIR/architect_prompt_body.md" \
     || { echo "render-prompt failed — aborting plan phase (see stderr above)"; exit 1; }

   if [ -n "$(tr -d '[:space:]' < "$ARTIFACTS_DIR/memory-context.md")" ]; then
     { printf '## Memory: Accumulated Patterns\n'; cat "$ARTIFACTS_DIR/memory-context.md"; \
       printf '\n---\n'; cat "$ARTIFACTS_DIR/architect_prompt_body.md"; } \
       > "$ARTIFACTS_DIR/architect_prompt.md"
   else
     cp "$ARTIFACTS_DIR/architect_prompt_body.md" "$ARTIFACTS_DIR/architect_prompt.md"
   fi
   ```

   Spawn an architect subagent using the Agent tool:
   - `description`: "Architect review: validate plan against spec"
   - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every re-spawn in the review cycle below too)
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/architect_prompt.md`
   ````

   Immediately below, under "### If architect returns 'Issues Found':", note that step 1
   ("Fix each issue in the plan") saves the revision to `$PLAN_FILE` (the same on-disk path
   from Phase 2 — the plan is always edited in place, never copied), then step 1a re-runs the
   `render-prompt` block above before step 2's re-spawn:

   Replace:
   ```
   ### If architect returns "Issues Found":
   1. Fix each issue in the plan
   2. Re-spawn the architect subagent for re-review
   ```
   With:
   ```
   ### If architect returns "Issues Found":
   1. Fix each issue in the plan (edit `$PLAN_FILE` in place)
   1a. Re-run the `render-prompt` block above unchanged — since it reads `$PLAN_FILE` directly,
       it picks up the revision from step 1 automatically. A `render-prompt` failure here is a
       hard stop for the phase, identically to the first pass.
   2. Re-spawn the architect subagent for re-review, with the verbatim contents of
      `$ARTIFACTS_DIR/architect_prompt.md` produced by step 1a
   ```

   **Phase 3.5** — replace steps 4-6 (add a render step before the spawn):

   Replace:
   ````
   4. Build the artifact content: the plan document text is `$PLAN_CONTENT`
   4a. Resolve the shadow model pin (Requirement 7: env explicitly set, even to empty, wins;
       unset falls back to the config default — `${VAR-default}`, not `${VAR:-default}`, so an
       explicit empty string is preserved rather than replaced):
       ```bash
       SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
       SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
       ```
   5. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: plan vs spec (cycle N)"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
      - `prompt`: `RUBRIC_CONTENT` (resolved in step 1) with:
        - `$ARTIFACT_KIND` replaced with `PLAN`
        - `$SPEC_CONTENT` replaced with the spec file contents
        - `$ARTIFACT_CONTENT` replaced with `$PLAN_CONTENT`
   6. Append the subagent's output to `CONFORMANCE_DIALOGUE`
   ````
   With:
   ````
   4. The artifact under review is the plan document on disk at `$PLAN_FILE` (bound in Phase 2).
   4a. Resolve the shadow model pin (Requirement 7: env explicitly set, even to empty, wins;
       unset falls back to the config default — `${VAR-default}`, not `${VAR:-default}`, so an
       explicit empty string is preserved rather than replaced):
       ```bash
       SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
       SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
       ```
   4b. Render the conformance prompt. `RUBRIC_CONTENT` (step 1) is prose, not a shell variable —
       no earlier step in this file ever assigns it in bash, and shell state does not persist
       across separate Bash tool calls, so a `printf '%s' "$RUBRIC_CONTENT"` here would write an
       empty file. Re-resolve the same clone-live-first *path* as a real variable instead, and
       pass it straight to `--template` (reads `$PLAN_FILE` directly for the artifact — the
       on-disk path bound in Phase 2, never a `plan_content.md` copy):
       ```bash
       if [ -f ".claude/skills/conformance/RUBRIC.md" ]; then
         RUBRIC_FILE=".claude/skills/conformance/RUBRIC.md"
       else
         RUBRIC_FILE="/opt/refinement-skills/conformance-reviewer-prompt.md"
       fi

       # TARGET-PATH
       python3 dark-factory/scripts/factory_core/cli.py render-prompt \
         --template "$RUBRIC_FILE" \
         --set ARTIFACT_KIND=PLAN \
         --set SPEC_CONTENT=@"$SPEC_FILE" \
         --set ARTIFACT_CONTENT=@"$PLAN_FILE" \
         --out "$ARTIFACTS_DIR/conformance_prompt.md" \
         || { echo "render-prompt failed — aborting plan phase (see stderr above)"; exit 1; }
       ```
   5. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: plan vs spec (cycle N)"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
      - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
   6. Append the subagent's output to `CONFORMANCE_DIALOGUE`
   ````

   Then update the shadow spawn (6a)'s prompt line:

   Replace:
   ```
      - `prompt`: identical `RUBRIC_CONTENT` with the same `$ARTIFACT_KIND`/`$SPEC_CONTENT`/
        `$ARTIFACT_CONTENT` substitution used for the Opus call this cycle
   ```
   With:
   ```
      - `prompt`: the identical verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md` the Opus call just read this cycle
   ```

   Then, in the **Reconcile loop** (step 8), replace sub-steps d-e:

   Replace:
   ```
      d. Revise the plan to address each MATERIAL deviation (update the plan file, re-read it)
      e. Re-spawn the conformance reviewer subagent (same prompt format, updated `$PLAN_CONTENT`)
   ```
   With:
   ```
      d. Revise the plan to address each MATERIAL deviation (edit `$PLAN_FILE` in place, re-read it)
      d2. Re-render the conformance prompt: re-run step 4b's `render-prompt` invocation
          unchanged — since it reads `$PLAN_FILE` directly, it picks up step d's revision
          automatically. A `render-prompt` failure here is a hard stop for the phase, identically
          to 4b.
      e. Re-spawn the conformance reviewer subagent with the verbatim contents of
         `$ARTIFACTS_DIR/conformance_prompt.md` produced by step d2
   ```

   And in f2 (the shadow reconcile re-spawn), update the prompt description:

   Replace:
   ```
      f2. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (step 8e's shadow
          counterpart — same prompt format, updated `$PLAN_CONTENT`, identical to step 6a but for
          this reconcile cycle). Append its response to `SHADOW_DIALOGUE` with a `---` separator
   ```
   With:
   ```
      f2. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (step 8e's shadow
          counterpart — prompt is the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
          produced by step d2, identical to step 6a but for this reconcile cycle). Append its
          response to `SHADOW_DIALOGUE` with a `---` separator
   ```

4. Verify pass:
   ```bash
   python -m pytest tests/test_plan_command_render_prompt.py tests/test_plan_command_conformance_rubric_fallback.py tests/test_plan_command_shadow_conformance.py tests/test_plan_command_context_pack.py -v
   ```
   Expected: all pass, including the three pre-existing plan-command test files (unaffected
   sections — Phase 1 fallback ordering, shadow-spawn description strings, and
   `SHADOW_DIALOGUE` scoping are untouched by this task's edits).

5. Commit:
   ```bash
   git add commands/dark-factory-plan.md tests/test_plan_command_render_prompt.py
   git commit -m "feat(#400): render-prompt for plan.md's architect and conformance prompts"
   ```

---

## Task 4: `commands/dark-factory-conformance.md` — Step 3.1 + reconcile loop

**Files:** `commands/dark-factory-conformance.md` (modified), `tests/test_conformance_command_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_conformance_command_render_prompt.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


def test_step31_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "- `$ARTIFACT_KIND` replaced with `IMPLEMENTATION`" not in step31
    assert "--set ARTIFACT_KIND=IMPLEMENTATION" in step31
    assert '--set SPEC_CONTENT=@"$SPEC_CONTENT_PATH"' in step31
    assert "conformance_artifact_content.md" in step31
    assert "conformance_prompt.md" in step31
    assert "render-prompt failed" in step31


def test_step31_handles_no_spec_fallback_path():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "SPEC_CONTENT_PATH" in step31
    assert "no_spec_issue_body.md" in step31


def test_step31_shadow_reuses_rendered_prompt_file():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "conformance_prompt.md` the Opus call just read in step 4" in step31


def test_reconcile_loop_re_renders_before_respawn():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: RECONCILE LOOP")
    reconcile = text[idx:text.find("## Phase 4: PASS")]
    assert "render-prompt" in reconcile
    assert "conformance_prompt.md" in reconcile
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_conformance_command_render_prompt.py -x -v
   ```
   Expected: all fail (strings not found).

3. Implement. In `commands/dark-factory-conformance.md`:

   **Step 3.1** — replace the artifact-content build + spawn (items 2-4):

   Replace:
   ````
   2. Build `$ARTIFACT_CONTENT`:
      ```
      ### Implementation Summary
      <contents of $ARTIFACTS_DIR/implementation.md, or "No implementation summary found.">

      ### Out-of-Scope Log (from implement agent)
      <contents of $ARTIFACTS_DIR/out-of-scope.md, or "None recorded.">

      ### Diff (pre-triaged, ranked by risk tier)
      $FILTER_ANNOTATION
      $TRIAGED_DIFF
      ```

   3. Set `CONFORMANCE_CYCLE=0`, `CONFORMANCE_DIALOGUE=""`, and `SHADOW_DIALOGUE=""`

   4. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: code vs spec"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
      - `prompt`: `RUBRIC_CONTENT` (resolved in Phase 1 step 3) with:
        - `$ARTIFACT_KIND` replaced with `IMPLEMENTATION`
        - `$SPEC_CONTENT` replaced with the spec file contents (or issue body if `NO_SPEC=true`)
        - `$ARTIFACT_CONTENT` replaced with the artifact content from Step 3.1
   ````
   With:
   ````
   2. Build `$ARTIFACT_CONTENT` and write it to a file. The heredoc below is **intentionally
      flush-left**, not indented under this list item — a `<<ARTIFACT_EOF` (no `-`) heredoc
      requires its closing delimiter to have zero leading whitespace to be recognized as the
      terminator; an indented `   ARTIFACT_EOF` would never match and the heredoc would swallow
      the rest of the script (the same reason Step 3's heredoc in `dark-factory-revise-advisory.md`
      Phase 3, Task 7 below, is flush-left rather than nested under its surrounding prose):

```bash
cat > "$ARTIFACTS_DIR/conformance_artifact_content.md" <<ARTIFACT_EOF
### Implementation Summary
$(cat "$ARTIFACTS_DIR/implementation.md" 2>/dev/null || echo "No implementation summary found.")

### Out-of-Scope Log (from implement agent)
$(cat "$ARTIFACTS_DIR/out-of-scope.md" 2>/dev/null || echo "None recorded.")

### Diff (pre-triaged, ranked by risk tier)
$FILTER_ANNOTATION
$TRIAGED_DIFF
ARTIFACT_EOF
```

   3. Set `CONFORMANCE_CYCLE=0`, `CONFORMANCE_DIALOGUE=""`, and `SHADOW_DIALOGUE=""`

   3a. Resolve the spec content path (Requirement 6's `SPEC_CONTENT_PATH` — a path, not the
       `SPEC_CONTENT` template slot name) and render the conformance prompt. This `if [ -n
       "$SPEC_FILE" ]` check relies on `$SPEC_FILE` (Phase 2) still being live in this Bash
       invocation — the same pre-existing assumption every other reference to `$SPEC_FILE`,
       `$ISSUE_NUM`, and `$ARTIFACTS_DIR` across this command file already makes; this task does
       not change that assumption, only reuses it. `RUBRIC_CONTENT` (Phase 1 step 3) is prose,
       not a shell variable, so it is re-resolved here as a real `RUBRIC_FILE` path instead of
       `printf`'d (which would silently write an empty file — no earlier step assigns
       `RUBRIC_CONTENT` in bash):
       ```bash
       if [ -n "$SPEC_FILE" ]; then
         SPEC_CONTENT_PATH="$SPEC_FILE"
       else
         # NO_SPEC=true: review runs advisory-only against the issue body instead of a spec file.
         gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json body --jq '.body' \
           > "$ARTIFACTS_DIR/no_spec_issue_body.md"
         SPEC_CONTENT_PATH="$ARTIFACTS_DIR/no_spec_issue_body.md"
       fi

       if [ -f ".claude/skills/conformance/RUBRIC.md" ]; then
         RUBRIC_FILE=".claude/skills/conformance/RUBRIC.md"
       else
         RUBRIC_FILE="/opt/refinement-skills/conformance-reviewer-prompt.md"
       fi

       # TARGET-PATH
       python3 dark-factory/scripts/factory_core/cli.py render-prompt \
         --template "$RUBRIC_FILE" \
         --set ARTIFACT_KIND=IMPLEMENTATION \
         --set SPEC_CONTENT=@"$SPEC_CONTENT_PATH" \
         --set ARTIFACT_CONTENT=@"$ARTIFACTS_DIR/conformance_artifact_content.md" \
         --out "$ARTIFACTS_DIR/conformance_prompt.md" \
         || { echo "render-prompt failed — aborting conformance phase (see stderr above)"; exit 1; }
       ```

   4. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: code vs spec"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
      - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
   ````

   Then update the shadow spawn (5a)'s prompt line:

   Replace:
   ```
      - `prompt`: identical `RUBRIC_CONTENT` with the same `$ARTIFACT_KIND`/`$SPEC_CONTENT`/
        `$ARTIFACT_CONTENT` substitution used for the Opus call in step 4
   ```
   With:
   ```
      - `prompt`: the identical verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md` the Opus call just read in step 4
   ```

   **Phase 3.5: RECONCILE LOOP** — replace steps 4-6:

   Replace:
   ````
   4. Fix the code to align with the spec:
      - Write a failing test that targets the missing/wrong behavior
      - Run the test to confirm it fails: `cd backend && python -m pytest <test_path> -x -v`
      - Implement the fix
      - Run the test to confirm it passes
      - Commit: `git add -A && git commit -m "fix: align implementation with spec (conformance cycle $CONFORMANCE_CYCLE)"`
   5. Re-get the diff:
      ```bash
      git diff main...HEAD -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' 2>/dev/null | head -1000
      ```
   6. Re-spawn the conformance reviewer subagent (same prompt format, updated diff)
   ````
   With:
   ````
   4. Fix the code to align with the spec:
      - Write a failing test that targets the missing/wrong behavior
      - Run the test to confirm it fails: `cd backend && python -m pytest <test_path> -x -v`
      - Implement the fix
      - Run the test to confirm it passes
      - Commit: `git add -A && git commit -m "fix: align implementation with spec (conformance cycle $CONFORMANCE_CYCLE)"`
   5. Re-get the diff:
      ```bash
      git diff main...HEAD -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' 2>/dev/null | head -1000 > "$ARTIFACTS_DIR/conformance_reconcile_diff.txt"
      TRIAGED_DIFF=$(cat "$ARTIFACTS_DIR/conformance_reconcile_diff.txt")
      ```
   5a. Re-render the conformance prompt: rebuild `$ARTIFACTS_DIR/conformance_artifact_content.md`
       with the updated `$TRIAGED_DIFF` (same heredoc as Step 3.1 item 2), then re-run Step 3.1's
       item 3a `render-prompt` invocation unchanged — a failure here is a hard stop for the phase,
       identically to Step 3.1.
   6. Re-spawn the conformance reviewer subagent with the verbatim contents of
      `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a
   ````

   And update 7a's shadow re-spawn description:

   Replace:
   ```
   7a. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (mirroring Step
       3.1's 5a for this cycle, `$ARTIFACT_KIND=IMPLEMENTATION`, updated diff). Prepend
   ```
   With:
   ```
   7a. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (mirroring Step
       3.1's 5a for this cycle — prompt is the verbatim contents of
       `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a). Prepend
   ```

4. Verify pass:
   ```bash
   python -m pytest tests/test_conformance_command_render_prompt.py tests/test_conformance_command_rubric_fallback.py tests/test_conformance_command_shadow_review.py -v
   ```
   Expected: all pass, including the two pre-existing conformance-command test files.

5. Commit:
   ```bash
   git add commands/dark-factory-conformance.md tests/test_conformance_command_render_prompt.py
   git commit -m "feat(#400): render-prompt for conformance.md's Step 3.1 and reconcile loop"
   ```

---

## Task 5: `commands/dark-factory-code-review.md` — Phase 3

**Files:** `commands/dark-factory-code-review.md` (modified), `tests/test_code_review_command_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_code_review_command_render_prompt.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-code-review.md"


def test_phase3_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: REVIEW")
    phase3 = text[idx:text.find("## Phase 4: BUILD PAYLOAD")]
    assert "with `$ISSUE_CONTEXT` replaced by the issue context from step 1" not in phase3
    assert "render-prompt" in phase3
    assert '--set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/code_review_issue_context.md"' in phase3
    assert '--set DIFF_CONTENT=@"$ARTIFACTS_DIR/review_diff.txt"' in phase3
    assert "code_review_prompt.md" in phase3
    assert "render-prompt failed" in phase3
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_code_review_command_render_prompt.py -x -v
   ```
   Expected: fails (strings not found).

3. Implement. In `commands/dark-factory-code-review.md`, replace Phase 3 items 1-3:

   Replace:
   ````
   1. Build `$ISSUE_CONTEXT` = issue title + body:
      ```bash
      gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json title,body \
        --jq '"Title: \(.title)\n\n\(.body)"'
      ```
   2. Read the code-review rubric, clone-live-first: `.claude/skills/code-review/RUBRIC.md`,
      falling back to `/opt/refinement-skills/code-review-reviewer-prompt.md` if the clone-live
      file is absent. Store the resolved text as `RUBRIC_CONTENT`.
   3. Spawn a code-reviewer subagent using the Agent tool:
      - `description`: "Code review: diff vs correctness/security"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract
      - `prompt`: `RUBRIC_CONTENT` (resolved in step 2) with `$ISSUE_CONTEXT` replaced by the issue context from step 1 and `$DIFF_CONTENT` replaced by the contents of `$ARTIFACTS_DIR/review_diff.txt`.
   ````
   With:
   ````
   1. Build the issue context and save it to a file (`render-prompt`'s `@file` values need a
      path, not an inline shell variable — an issue body can be large):
      ```bash
      gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json title,body \
        --jq '"Title: \(.title)\n\n\(.body)"' > "$ARTIFACTS_DIR/code_review_issue_context.md"
      ```
   2. Read the code-review rubric, clone-live-first: `.claude/skills/code-review/RUBRIC.md`,
      falling back to `/opt/refinement-skills/code-review-reviewer-prompt.md` if the clone-live
      file is absent. Store the resolved text as `RUBRIC_CONTENT`.
   2a. Render the code-review prompt. `RUBRIC_CONTENT` (step 2) is prose, not a shell variable —
       re-resolve the same clone-live-first path as a real `RUBRIC_FILE` and pass it straight to
       `--template` (a `printf '%s' "$RUBRIC_CONTENT"` here would silently write an empty file,
       since no earlier step assigns `RUBRIC_CONTENT` in bash):
       ```bash
       if [ -f ".claude/skills/code-review/RUBRIC.md" ]; then
         RUBRIC_FILE=".claude/skills/code-review/RUBRIC.md"
       else
         RUBRIC_FILE="/opt/refinement-skills/code-review-reviewer-prompt.md"
       fi

       # TARGET-PATH
       python3 dark-factory/scripts/factory_core/cli.py render-prompt \
         --template "$RUBRIC_FILE" \
         --set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/code_review_issue_context.md" \
         --set DIFF_CONTENT=@"$ARTIFACTS_DIR/review_diff.txt" \
         --out "$ARTIFACTS_DIR/code_review_prompt.md" \
         || { echo "render-prompt failed — aborting code-review phase (see stderr above)"; exit 1; }
       ```
   3. Spawn a code-reviewer subagent using the Agent tool:
      - `description`: "Code review: diff vs correctness/security"
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract
      - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/code_review_prompt.md`
   ````

4. Verify pass:
   ```bash
   python -m pytest tests/test_code_review_command_render_prompt.py tests/test_code_review_prompt.py -v
   ```
   Expected: all pass, including the pre-existing `test_code_review_prompt.py` (asserts only
   against `RUBRIC.md` content, which is unchanged).

5. Commit:
   ```bash
   git add commands/dark-factory-code-review.md tests/test_code_review_command_render_prompt.py
   git commit -m "feat(#400): render-prompt for code-review.md's Phase 3"
   ```

---

## Task 6: `commands/dark-factory-refine.md` — Phase 4

**Files:** `commands/dark-factory-refine.md` (modified), `tests/test_refine_command_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_refine_command_render_prompt.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-refine.md"


def test_phase4_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 4: BRAINSTORMING LOOP")
    phase4 = text[idx:]
    assert "with the $ISSUE_CONTEXT, $QA_HISTORY, and $QUESTION placeholders replaced" \
        not in phase4
    assert "render-prompt" in phase4
    assert "--template /opt/refinement-skills/product-owner-prompt.md" in phase4
    assert '--set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/refine_issue_context.md"' in phase4
    assert '--set QA_HISTORY=@"$ARTIFACTS_DIR/refine_qa_history.md"' in phase4
    assert '--set QUESTION=@"$ARTIFACTS_DIR/refine_question.md"' in phase4
    assert "refine_product_owner_prompt.md" in phase4
    assert "render-prompt failed" in phase4


def test_phase4_notes_per_iteration_rerender():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 4: BRAINSTORMING LOOP")
    phase4 = text[idx:]
    assert "repeat step 2 in full" in phase4
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_refine_command_render_prompt.py -x -v
   ```
   Expected: fails (strings not found).

3. Implement. In `commands/dark-factory-refine.md`, Phase 4, replace steps 2 and 4:

   Replace:
   ```
   2. For each question, spawn a product-owner subagent using the Agent tool:
      - `description`: "Product owner: <short question summary>"
      - `prompt`: Content of `product-owner-prompt.md` with the $ISSUE_CONTEXT, $QA_HISTORY, and $QUESTION placeholders replaced with actual values
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (do not let it inherit the orchestrator's model)
   3. If the subagent returns a response starting with `UNCERTAIN:`:
      - Post a comment on the issue explaining the question and context gathered so far
      - Run: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
      - Write a brief summary to `$ARTIFACTS_DIR/refinement-status.md` noting the abort reason
      - Exit cleanly (exit code 0)
   4. Record the answer and continue until you have enough information
   ```
   With:
   ````
   2. For each question, render the product-owner prompt, then spawn a subagent using the Agent
      tool. `$ISSUE_CONTEXT`/`$QA_HISTORY`/`$QUESTION` are values you (the orchestrating agent)
      are holding, not exported shell variables — Phase 3 built the context summary and this
      loop builds the running Q&A history and each new question yourself, in your own context,
      so materialize them with the Write tool before rendering:
      - Write `$ARTIFACTS_DIR/refine_issue_context.md` — the context summary from Phase 3.
      - Write `$ARTIFACTS_DIR/refine_qa_history.md` — every prior question/answer pair from this
        loop so far (empty file on the first question).
      - Write `$ARTIFACTS_DIR/refine_question.md` — the question you just formulated in step 1.
      ```bash
      # TARGET-PATH
      python3 dark-factory/scripts/factory_core/cli.py render-prompt \
        --template /opt/refinement-skills/product-owner-prompt.md \
        --set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/refine_issue_context.md" \
        --set QA_HISTORY=@"$ARTIFACTS_DIR/refine_qa_history.md" \
        --set QUESTION=@"$ARTIFACTS_DIR/refine_question.md" \
        --out "$ARTIFACTS_DIR/refine_product_owner_prompt.md" \
        || { echo "render-prompt failed — aborting refine phase (see stderr above)"; exit 1; }
      ```
      - `description`: "Product owner: <short question summary>"
      - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/refine_product_owner_prompt.md`
      - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (do not let it inherit the orchestrator's model)
   3. If the subagent returns a response starting with `UNCERTAIN:`:
      - Post a comment on the issue explaining the question and context gathered so far
      - Run: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
      - Write a brief summary to `$ARTIFACTS_DIR/refinement-status.md` noting the abort reason
      - Exit cleanly (exit code 0)
   4. Record the answer and continue until you have enough information — repeat step 2 in full
      for every new question (re-write all three files, since `$QA_HISTORY` grows with each
      answer, then re-run `render-prompt`; never reuse a prior iteration's rendered prompt)
   ````

4. Verify pass:
   ```bash
   python -m pytest tests/test_refine_command_render_prompt.py -v
   ```
   Expected: all pass. (`grep -rl "product-owner-prompt" tests/` returns only
   `tests/test_no_markethawk_hardcoding.py`, which asserts the template has no hardcoded
   "MarketHawk" string — unrelated to Phase 4's prose, so no pre-existing test asserts on the
   text this task changes.)

5. Commit:
   ```bash
   git add commands/dark-factory-refine.md tests/test_refine_command_render_prompt.py
   git commit -m "feat(#400): render-prompt for refine.md's Phase 4 product-owner spawn"
   ```

---

## Task 7: `commands/dark-factory-revise-advisory.md` — Phase 3 (sixth call site, brace delimiter)

**Files:** `commands/dark-factory-revise-advisory.md` (modified), `tests/test_revise_advisory_command_render_prompt.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_revise_advisory_command_render_prompt.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.prompt_render import _segments, _TOKEN_RE

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-revise-advisory.md"


def test_phase3_uses_render_prompt_with_brace_delimiter():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: SPAWN FIX AGENT")
    phase3 = text[idx:text.find("## Phase 4: COMMIT AND PUSH")]
    assert "Replace `{FINDINGS_TEXT}` with `$FINDINGS_TEXT`" not in phase3
    assert "render-prompt" in phase3
    assert "--delimiter brace" in phase3
    assert '--set FINDINGS_TEXT=@"$ARTIFACTS_DIR/revise_advisory_findings.md"' in phase3
    assert '--set DIFF_CONTENT=@"$ARTIFACTS_DIR/revise_advisory_diff.md"' in phase3
    assert "revise_advisory_prompt.md" in phase3
    assert "render-prompt failed" in phase3


def test_phase3_heredoc_uses_quoted_delimiter():
    text = CMD.read_text(encoding="utf-8")
    assert "<<'PROMPT_EOF'" in text


def test_phase3_outer_fence_is_four_backticks_not_three():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: SPAWN FIX AGENT")
    phase3 = text[idx:text.find("## Phase 4: COMMIT AND PUSH")]
    assert "````bash" in phase3


def test_embedded_template_bare_slots_match_expected_drift_guard():
    text = CMD.read_text(encoding="utf-8")
    start_marker = "<<'PROMPT_EOF'\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index("\nPROMPT_EOF", start)
    template_text = text[start:end]

    token_re = _TOKEN_RE["brace"]
    bare = set()
    for segment_text, protected in _segments(template_text):
        if not protected:
            bare.update(m.group(1) for m in token_re.finditer(segment_text))
    assert bare == {"FINDINGS_TEXT", "DIFF_CONTENT"}
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_revise_advisory_command_render_prompt.py -x -v
   ```
   Expected: all fail — the first three on missing strings, the fourth (`test_embedded_...`)
   with `ValueError` from `str.index` (no `<<'PROMPT_EOF'` heredoc marker exists yet).

3. Implement. In `commands/dark-factory-revise-advisory.md`, replace the entire Phase 3 section:

   Replace:
   ````
   ## Phase 3: SPAWN FIX AGENT

   Spawn a subagent using the Agent tool:

   - `description`: "Revise advisory findings: ${ADVISORY_COUNT} item(s)"
   - `model`: inherit (do not override — Sonnet is appropriate for targeted edits)
   - `prompt`:

   ```
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
   ```

   Replace `{FINDINGS_TEXT}` with `$FINDINGS_TEXT` and `{DIFF_CONTENT}` with `$DIFF_CONTENT`.

   Save the agent's summary output to `$ARTIFACTS_DIR/revise_summary.txt`.

   If the agent errors or returns empty output → log a warning and proceed to Phase 4 (the diff
   check will detect no changes and exit 0 cleanly).
   ````
   With:
   `````
   ## Phase 3: SPAWN FIX AGENT

   Render the fix-agent prompt from a heredoc-written template, then spawn a subagent using the
   Agent tool. The template's boilerplate is byte-unchanged from before this ticket; only where
   it is assembled from changes. The outer fence below is **four backticks, not three** — the
   heredoc body contains a ` ```diff ` fence, and a three-backtick outer fence would be
   terminated early by it, silently turning the rest of this phase's instructions into prose.

   ````bash
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
   ````

   Spawn a subagent using the Agent tool:

   - `description`: "Revise advisory findings: ${ADVISORY_COUNT} item(s)"
   - `model`: inherit (do not override — Sonnet is appropriate for targeted edits)
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/revise_advisory_prompt.md`

   The quoted heredoc delimiter (`<<'PROMPT_EOF'`) is required, not stylistic — an unquoted
   delimiter would let the shell expand `$` and backticks in the boilerplate before
   `render-prompt` ever sees the template text.

   Save the agent's summary output to `$ARTIFACTS_DIR/revise_summary.txt`.

   If the agent errors or returns empty output → log a warning and proceed to Phase 4 (the diff
   check will detect no changes and exit 0 cleanly).
   `````

4. Verify pass:
   ```bash
   python -m pytest tests/test_revise_advisory_command_render_prompt.py -v
   ```
   Expected: all pass.

5. Commit:
   ```bash
   git add commands/dark-factory-revise-advisory.md tests/test_revise_advisory_command_render_prompt.py
   git commit -m "feat(#400): render-prompt for revise-advisory.md's embedded fix-agent prompt"
   ```

---

## Task 8: Full-suite verification and self-review

**Files:** none (verification only).

### Steps

1. Run the full test suite:
   ```bash
   python -m pytest tests/ -v
   ```
   Expected: all tests pass, including every file touched or added in Tasks 1-7 and the full
   pre-existing suite (no regressions).

2. Run the specific presence-assertion regression set called out in the spec (Requirement/Task 6
   of the spec's Architecture section) to confirm none needed changes beyond what Tasks 3-6 made:
   ```bash
   python -m pytest tests/test_conformance_command_rubric_fallback.py tests/test_plan_command_conformance_rubric_fallback.py tests/test_conformance_command_shadow_review.py tests/test_plan_command_shadow_conformance.py tests/test_code_review_prompt.py tests/test_conformance_skill_files.py -v
   ```
   Expected: all pass unmodified.

3. Run the exact checks CI runs (`.github/workflows/ci.yml` lines 13, 17, 46-47):
   ```bash
   python -m pytest tests/ -v
   bash tests/test_smoke_gate.sh
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
   Expected: all pass — no DAG node or `command:` reference changed, only the prose/bash inside
   existing `commands/*.md` phase instructions. (`check_workflow_dag.py`/`check_workflow_when.py`
   both require the workflow path as an argument — running them with no arguments exits 2 with a
   usage error, not a pass.)

4. Self-review against this plan's own conventions:
   - Confirm every new/modified command-file call site uses `render-prompt` and none silently
     falls back to hand-substitution on a nonzero exit (grep check):
     ```bash
     grep -c "render-prompt failed" commands/dark-factory-plan.md commands/dark-factory-conformance.md commands/dark-factory-code-review.md commands/dark-factory-refine.md commands/dark-factory-revise-advisory.md
     ```
     Expected: nonzero count in each of the five files.
   - Confirm no `.claude/skills/**` file was touched (hard-excluded path):
     ```bash
     git diff --stat origin/main -- .claude/skills/
     ```
     Expected: empty output.
   - Confirm the five template *content* files are byte-unchanged from `main`:
     ```bash
     git diff origin/main -- .claude/skills/conformance/RUBRIC.md .claude/skills/code-review/RUBRIC.md refinement-skills/architect-prompt.md refinement-skills/product-owner-prompt.md
     ```
     Expected: empty output.
   - Confirm no edit landed in the per-run `.archon/commands/` shadow (spec Requirement 7 — the
     canonical source is `commands/*.md`):
     ```bash
     git diff origin/main -- .archon/commands/
     ```
     Expected: empty output.
   - Confirm this plan document itself starts with the required `**Issue:** #400` line and
     contains no `TBD`/`TODO`/placeholder code blocks.

No commit for this task (verification only) — if any check fails, fix the specific task above
and re-run Task 8 from step 1.

---

## Notes for the architect / conformance reviewers

- This plan implements Requirements 1-11 and the six-call-site inventory from the spec's
  Architecture section verbatim (module code in Task 1 is copied from the spec's own verified
  `prompt_render.py` listing).
- Requirement 9 (this narrows, not eliminates, the LLM-in-the-loop failure mode) is honored:
  every command-file edit's final spawn step says "the verbatim contents of `$ARTIFACTS_DIR/
  ....md`" — the orchestrating agent still reads that file and copies it into the `Agent` tool's
  `prompt` argument by hand. No task claims or introduces an assertion that `Agent.prompt`
  equals the rendered file.
- Requirement 5's sixth call site (`dark-factory-revise-advisory.md`) is covered in Task 7,
  including the four-backtick outer-fence fix the spec's Architecture point 4 calls out as
  required (not incidental) for that site.
- No task touches `.claude/skills/**`, `deploy/**`, or any `gate_*`/breaker/budget file — this
  stays entirely within `SCOPE BOUNDARY` for `implement` (`scripts/factory_core/`, `commands/*.md`,
  `tests/`), consistent with the spec's explicit rejection of a template-layer fix.

## Assumptions

- [ASSUMPTION] Every placeholder name in use today across all five templates matches
  `[A-Za-z_][A-Za-z0-9_]*` (all are `[A-Z_]+`) — confirmed by inspection during spec gate and
  re-confirmed against the live files while writing this plan (Task 1/3-7's `grep -n` output
  above).
- [ASSUMPTION] `_BACKTICK_SPAN_RE`'s single-line, single-backtick-pair matching (no
  triple-backtick fence awareness) is sufficient for all five templates — no placeholder token
  in any of the five sits on the same line as a stray/unpaired backtick fence marker.

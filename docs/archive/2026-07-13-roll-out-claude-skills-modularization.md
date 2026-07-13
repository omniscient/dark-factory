# Implementation Plan: Roll Out Dark Factory Claude Skills Modularization with Compatibility Wrappers

**Issue:** omniscient/dark-factory#49
**Spec:** `docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md`
**Depends on:** omniscient/dark-factory#48 (CLOSED)

---

## Goal

Close the gap between #48's evaluation (no scenario cleared for default-on skill invocation)
and #49's acceptance criteria by (a) making the spec itself the durable, protected
rollout-status runbook, and (b) adding **runtime** tests — not just static string-order
assertions — that actually exercise #44's existing clone-live-first/baked-fallback rubric
resolution for the `conformance` and `code-review` scenarios, plus telemetry-labeling
verification in `scripts/context_budget.py`. No new wrapper mechanism, no changes to
`.archon/workflows/archon-dark-factory.yaml`, no Tier 2 (refine/plan_narrative/continue) work.

## Architecture

The spec (`docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md`)
already contains the full Rollout Status table, standing advisory-only policy, rollback steps,
and #218 forward obligation — it *is* the runbook; this plan adds no new prose sections to it.
This plan's work is exclusively: (1) protect that spec from accidental archival the same way
#42's policy spec is protected — a `README.md` link plus a doc-shape/content-pin test — and
(2) add runtime tests for the two behaviors the spec's Requirements 2/3 call out as currently
unverified at runtime.

**Tasks 3–5 add verification tests only, with no corresponding "implementation" step.** They
exercise `_resolve_skill_prompt`/`_probe_skill_prompts`/`build_budget` logic that #44 already
built and that already passes — per the spec's explicit framing ("this is verification of
existing behavior, not new plumbing," Architecture §2) and Alternatives §2 (documentation-only
was rejected because the criterion is testable, not because behavior needs to change). Each of
those tasks is still TDD in spirit: the test is written, run, and its pass is the verification
artifact — there is no red state to induce because no production code changes.

No `bench/run_suite.sh` parity run is warranted: this is a Tier 0 change (tests + one README
link; zero behavior change to any phase command, DAG node, or resolution logic). Standard
`conformance:` + `code_review:` gates only.

## Tech Stack

Python (`pytest`, using the existing `monkeypatch`/`tmp_path` fixtures and the
`run_budget`/`make_*` helpers already in `tests/test_context_budget.py`), Markdown (`README.md`
link). No new scripts or dependencies.

---

## File Structure

| File | Change |
|---|---|
| `docs/superpowers/plans/2026-07-13-roll-out-claude-skills-modularization.md` | New — this plan |
| `README.md` | Modified — add a `## Further reading` link to the #49 rollout-status runbook spec, mirroring the existing #42 link |
| `tests/test_claude_skills_rollout_doc.py` | New — doc-shape/content-pin test for the #49 rollout runbook (mirrors `tests/test_claude_skills_policy_doc.py`) |
| `tests/test_conformance_rubric_baked_fallback_runtime.py` | New — runtime verification that `_resolve_skill_prompt` falls back to the baked conformance rubric when the clone-live `RUBRIC.md` is absent |
| `tests/test_code_review_rubric_baked_fallback_runtime.py` | New — same, for the code-review rubric |
| `tests/test_context_budget.py` | Modified — add scenario-labeled `skill_prompts` telemetry verification tests for `conformance`/`code-review` under both clone-live-present and clone-live-absent conditions |

---

## Memory Context Applied

Three accumulated-memory lessons from `dark-factory/scripts/load_memory_context.sh plan` are
baked into this plan (not left as a separate advisory section):

1. **`codebase-patterns.md` [PATTERN] (issue #42, PR #215 lesson):** a later `implement`-phase
   agent must itself copy this plan and the spec onto the `feat/issue-49-*` branch and commit
   them — they do not transfer automatically from this `refine/issue-49-*` branch. When the
   ticket is later archived, **only this plan document** moves to `docs/archive/`; the spec
   stays at its current `docs/superpowers/specs/` path because it is explicitly the ticket's
   living runbook deliverable, not a one-shot implementation spec. This is standard
   implement-phase behavior (not a task step in this plan, which only produces the plan
   document) — flagged here so the implement-phase agent that reads this plan is not surprised,
   and so the archive step at completion does not repeat PR #215's mistake of archiving both
   files.
2. **`codebase-patterns.md` [PATTERN] (issue #149):** memory context loaded for this plan phase
   was scoped selectively (only the files relevant to this change), not loaded unconditionally —
   consistent with this plan touching only `tests/`, `README.md`, and `docs/superpowers/plans/`.
3. **`codebase-patterns.md` [PATTERN] (issue #250):** not directly applicable to this plan (no
   OOS-scope check is performed by the plan phase itself), but flagged for the implement-phase
   agent: if it needs to verify a file is genuinely out of this plan's scope, use
   `git diff origin/main HEAD -- <file>` (two-dot), not the three-dot form, to avoid
   false-positive OOS hits.

---

## Task 1: Add failing doc-shape test for the rollout runbook

**Files:** `tests/test_claude_skills_rollout_doc.py` (new)

1. Write the test file:

```python
"""Regression test for issue #49 — the rollout-status runbook for Dark Factory Claude Skills
modularization. The runbook is the spec itself (docs/superpowers/specs/2026-07-13-roll-out-...),
kept living and never archived, per the #42/PR #215 pattern in
`.archon/memory/codebase-patterns.md`. Pins its durable claims to exact substrings so a future
edit that silently drops the advisory-only stance, the Tier 2 ineligibility, or the rollback
steps fails CI instead of passing silently — and pins the README link that protects the doc
from CLAUDE.md's archive-excision rule ("never archive a doc that tests or README still
reference").
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-13-roll-out-dark-factory-claude-skills-design.md"
README = REPO_ROOT / "README.md"


def _spec_text():
    assert SPEC.exists(), f"{SPEC} does not exist — issue #49's rollout runbook must live here"
    return SPEC.read_text(encoding="utf-8")


def test_runbook_exists_at_durable_specs_path():
    assert SPEC.exists()
    assert SPEC.is_relative_to(REPO_ROOT / "docs" / "superpowers" / "specs")


def test_status_line_marks_doc_as_living_not_archived():
    text = _spec_text()
    assert "living reference — not archived on completion" in text


def test_rollout_status_table_pins_tier1_advisory_only_and_tier2_ineligible():
    text = _spec_text()
    assert "**Advisory-only.**" in text
    assert "**Advisory-only**, same terms." in text
    assert "**Not rollout-eligible.**" in text


def test_standing_policy_states_no_default_on_or_blocking():
    text = _spec_text()
    assert (
        "**Standing policy:** no scenario goes default-on or blocking on native Skill-tool "
        "invocation." in text
    )


def test_rollback_steps_and_forward_obligation_sections_present():
    text = _spec_text()
    assert "### 3. Rollback steps" in text
    assert "### 4. Forward obligation for #218" in text
    assert "Baked-copy staleness hazard" in text


def test_readme_links_the_rollout_runbook_spec():
    text = README.read_text(encoding="utf-8")
    assert (
        "docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md" in text
    ), "README.md's ## Further reading list must link the #49 rollout-status runbook spec"
```

2. Verify it fails (the README link does not exist yet — every other assertion already passes
   since the spec content was written during refinement):

```bash
python -m pytest tests/test_claude_skills_rollout_doc.py -v
```

Expected output: `5 passed, 1 failed` — `test_readme_links_the_rollout_runbook_spec` fails with
an `AssertionError`; all doc-content-pin tests pass because the spec already carries that
content.

3. Commit:

```bash
git add tests/test_claude_skills_rollout_doc.py
git commit -m "test(rollout-doc): add failing content/README-link pins for #49 runbook"
```

---

## Task 2: Link the rollout runbook from README.md

**Files:** `README.md` (modified)

1. In the `## Further reading` list, add a new line immediately after the existing #42 policy
   spec link (currently the line containing
   `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`):

```markdown
- [`docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md`](docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md) — Claude Skills rollout-status runbook: per-scenario advisory state and rollback steps
```

2. Verify Task 1's test suite passes:

```bash
python -m pytest tests/test_claude_skills_rollout_doc.py -v
```

Expected output: `6 passed`.

3. Commit:

```bash
git add README.md
git commit -m "docs(readme): link the #49 Claude Skills rollout-status runbook"
```

---

## Task 3: Add runtime fallback-verification test for the conformance rubric

**Files:** `tests/test_conformance_rubric_baked_fallback_runtime.py` (new)

Per spec Requirement 2: exercise `scripts/context_budget.py`'s existing
`_resolve_skill_prompt` clone-live-first/baked-fallback logic directly and per-scenario (the
three existing static tests — `tests/test_conformance_command_rubric_fallback.py`,
`tests/test_code_review_command.py`, `tests/test_plan_command_conformance_rubric_fallback.py` —
only assert path-string order inside `commands/*.md`, never simulate the clone-live file being
absent).

1. Write the test file:

```python
"""Runtime verification (issue #49) that the conformance rubric's clone-live/baked-fallback
resolution — built by #44 in scripts/context_budget.py's _resolve_skill_prompt — actually
falls back to the baked /opt/refinement-skills/conformance-reviewer-prompt.md copy when the
clone-live .claude/skills/conformance/RUBRIC.md file is absent, rather than failing, returning
None, or silently degrading to a no-op. This exercises the behavior the three existing static
tests (test_conformance_command_rubric_fallback.py, test_plan_command_conformance_rubric_fallback.py)
only assert as path-string ordering, never at runtime. No production code changes — this
verifies #44's existing resolution logic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_budget as cb


def test_falls_back_to_baked_conformance_prompt_when_clone_live_rubric_absent(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"  # no .claude/skills/conformance/RUBRIC.md present
    clone_dir.mkdir()

    result = cb._resolve_skill_prompt(
        str(clone_dir), "conformance-reviewer-prompt.md", "conformance/RUBRIC.md"
    )

    assert result == "BAKED conformance rubric content", (
        "clone-live RUBRIC.md absent must fall back to the baked copy, not fail or return "
        "empty/None"
    )


def test_prefers_clone_live_conformance_rubric_when_present(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"
    skill_dir = clone_dir / ".claude" / "skills" / "conformance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE conformance rubric content")

    result = cb._resolve_skill_prompt(
        str(clone_dir), "conformance-reviewer-prompt.md", "conformance/RUBRIC.md"
    )

    assert result == "CLONE-LIVE conformance rubric content"
```

2. Run it and confirm both pass immediately — this verifies pre-existing #44 behavior, so there
   is no red state to induce:

```bash
python -m pytest tests/test_conformance_rubric_baked_fallback_runtime.py -v
```

Expected output: `2 passed`.

3. Commit:

```bash
git add tests/test_conformance_rubric_baked_fallback_runtime.py
git commit -m "test(conformance-rubric): runtime-verify clone-live/baked-fallback resolution"
```

---

## Task 4: Add runtime fallback-verification test for the code-review rubric

**Files:** `tests/test_code_review_rubric_baked_fallback_runtime.py` (new)

Same as Task 3, for the code-review rubric (per spec Requirement 2, both Tier 1 scenarios are
in scope).

1. Write the test file:

```python
"""Runtime verification (issue #49) that the code-review rubric's clone-live/baked-fallback
resolution — built by #44 in scripts/context_budget.py's _resolve_skill_prompt — actually
falls back to the baked /opt/refinement-skills/code-review-reviewer-prompt.md copy when the
clone-live .claude/skills/code-review/RUBRIC.md file is absent, rather than failing, returning
None, or silently degrading to a no-op. This exercises the behavior tests/test_code_review_command.py
only asserts as path-string ordering, never at runtime. No production code changes — this
verifies #44's existing resolution logic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_budget as cb


def test_falls_back_to_baked_code_review_prompt_when_clone_live_rubric_absent(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"  # no .claude/skills/code-review/RUBRIC.md present
    clone_dir.mkdir()

    result = cb._resolve_skill_prompt(
        str(clone_dir), "code-review-reviewer-prompt.md", "code-review/RUBRIC.md"
    )

    assert result == "BAKED code-review rubric content", (
        "clone-live RUBRIC.md absent must fall back to the baked copy, not fail or return "
        "empty/None"
    )


def test_prefers_clone_live_code_review_rubric_when_present(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"
    skill_dir = clone_dir / ".claude" / "skills" / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE code-review rubric content")

    result = cb._resolve_skill_prompt(
        str(clone_dir), "code-review-reviewer-prompt.md", "code-review/RUBRIC.md"
    )

    assert result == "CLONE-LIVE code-review rubric content"
```

2. Run it and confirm both pass immediately:

```bash
python -m pytest tests/test_code_review_rubric_baked_fallback_runtime.py -v
```

Expected output: `2 passed`.

3. Commit:

```bash
git add tests/test_code_review_rubric_baked_fallback_runtime.py
git commit -m "test(code-review-rubric): runtime-verify clone-live/baked-fallback resolution"
```

---

## Task 5: Add scenario-labeled telemetry verification tests to `context_budget.py`

**Files:** `tests/test_context_budget.py` (modified — append to the existing
`# ── skill_prompts clone-live-first / baked-fallback resolution ──` section)

Per spec Requirement 3: confirm `build_budget`'s `skill_prompts` section is reported as
`included` (not `dropped`) with the correct `scenario` key, for **both** the `conformance` and
`code-review` scenarios, under **both** clone-live-present and clone-live-absent conditions.
The existing `test_skill_prompts_*` tests in this file call `_probe_skill_prompts` directly and
never assert the outer `scenario` field — this closes that gap by going through
`build_budget`/`run_budget` (the existing test helper) instead.

1. Append these four tests to `tests/test_context_budget.py`, directly after
   `test_skill_prompts_dropped_when_nothing_resolves` (the last function in the existing
   skill-prompts section):

```python
def test_conformance_scenario_labels_skill_prompts_included_on_baked_fallback(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))
    # run_dir has no .claude/skills/conformance/RUBRIC.md -> clone-live absent

    result = run_budget(run_dir, "conformance")

    assert result["scenario"] == "conformance"
    assert result["sections"]["skill_prompts"]["status"] == "included", (
        "baked-fallback must still report skill_prompts as included, not dropped, for the "
        "conformance scenario"
    )


def test_conformance_scenario_labels_skill_prompts_included_on_clone_live(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))
    skill_dir = run_dir / ".claude" / "skills" / "conformance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE conformance content")

    result = run_budget(run_dir, "conformance")

    assert result["scenario"] == "conformance"
    assert result["sections"]["skill_prompts"]["status"] == "included"


def test_code_review_scenario_labels_skill_prompts_included_on_baked_fallback(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))
    # run_dir has no .claude/skills/code-review/RUBRIC.md -> clone-live absent

    result = run_budget(run_dir, "code-review")

    assert result["scenario"] == "code-review"
    assert result["sections"]["skill_prompts"]["status"] == "included", (
        "baked-fallback must still report skill_prompts as included, not dropped, for the "
        "code-review scenario"
    )


def test_code_review_scenario_labels_skill_prompts_included_on_clone_live(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))
    skill_dir = run_dir / ".claude" / "skills" / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE code-review content")

    result = run_budget(run_dir, "code-review")

    assert result["scenario"] == "code-review"
    assert result["sections"]["skill_prompts"]["status"] == "included"
```

2. Run the full file and confirm all pass, including the 4 new tests and the pre-existing ones
   (unaffected):

```bash
python -m pytest tests/test_context_budget.py -v
```

Expected output: all tests pass — pre-existing count plus 4 new (`test_conformance_scenario_
labels_skill_prompts_included_on_baked_fallback`, `..._on_clone_live`,
`test_code_review_scenario_labels_skill_prompts_included_on_baked_fallback`, `..._on_clone_live`).

3. Commit:

```bash
git add tests/test_context_budget.py
git commit -m "test(context-budget): verify scenario-labeled skill_prompts telemetry for conformance/code-review"
```

---

## Task 6: Full-suite verification

**Files:** none (verification only)

1. Run the complete test suite to confirm no regression:

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

Expected output: all tests pass, including the 3 new files from Tasks 1/3/4 and the 4 appended
tests from Task 5, with no change to any pre-existing test's pass/fail status.

2. Run the workflow-DAG gate scripts (unaffected by this change, but part of the standard
   pre-publish check):

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected output: both exit 0 with no errors — this change touches no workflow YAML and no
`command:` id, satisfying spec Requirement 6 ("preserve existing command messages") by absence
of change, not by a new test.

3. No commit in this task — it is a verification checkpoint confirming Tasks 1–5 left the repo
   green before the plan is published.

---

## Out of Scope (explicitly, per spec)

- No new wrapper/alias mechanism, config toggle, or third fallback tier — #44's
  clone-live-first/baked-fallback is the only compatibility wrapper this ticket relies on or
  extends (spec Requirement 5, Alternatives §4).
- No changes to `.archon/workflows/archon-dark-factory.yaml` or any `command:` id (spec
  Requirement 6).
- No skill-modularized alternative for any Tier 2 scenario (`refine`, `plan_narrative`,
  `continue`) — `.claude/skills/refinement/` gets no new `SKILL.md` (spec Requirement 4,
  Alternatives §1).
- No changes to `scripts/context_budget.py`'s `_SECTION_REGISTRY` or `_SKILL_PROMPT_FILES`, and
  no changes to the resolution logic itself (`_resolve_skill_prompt`, `_probe_skill_prompts`) —
  this ticket verifies existing behavior only (spec Architecture §2).
- No automated baked-copy staleness detection (e.g. a tracked-vs-baked hash-comparison smoke
  check) — documented as a hazard in the spec's rollback steps, not built here (spec Open
  Questions).
- No re-run of #48's Tier 1 live A/B spot-check (blocked on `ANTHROPIC_API_KEY` availability in
  the evaluation environment, and explicitly not this ticket's job per spec Open Questions).
- No new test pinning the literal `Refine issue #N`-style dispatch strings in
  `archon-dark-factory.yaml` — considered and explicitly deferred (spec Alternatives §5).

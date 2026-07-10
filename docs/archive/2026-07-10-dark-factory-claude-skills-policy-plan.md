# Plan: Dark Factory Claude Skills Conventions and Safety Policy

**Issue:** omniscient/dark-factory#42 — Define Dark Factory Claude Skills conventions and safety
policy
**Spec:** [docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md](../specs/2026-07-10-dark-factory-claude-skills-design.md)

## Goal

The spec's own Overview is explicit: *"This ticket produces the **policy document only**. The
consolidation actions it recommends (renaming `refinement-skills/` → `.claude/skills/refinement/`,
adding `allowed-tools` frontmatter, updating `.factory/adapter.yaml` exclusion lists) are called
out explicitly as **follow-up implementation tickets** under the parent epic, not implemented
here."* The spec's `## Architecture / Policy` section (§1–§8) already states the taxonomy,
`disable-model-invocation`/`user-invocable` rules, `allowed-tools` tiering and `Bash(*)` ban, the
compact-script injection requirement, and the `.claude/skills/**` / `.claude/settings.json` /
`.factory/hooks/**` review expectations — i.e. the spec document itself, already committed at
`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md` (commit `71d817b`), *is*
the design/policy doc the issue's first acceptance criterion asks for, sitting at an appropriate
docs path.

This plan does **not** create a second, duplicate living-ops document (e.g. a
`docs/dark-factory-claude-skills.md`) restating the same §1–§8 policy in different prose. Doing
so would not be requested anywhere in the spec's own Requirements list (`## Requirements`, items
1–9) or its Architecture/Policy section, and the spec's `## Alternatives Considered` explicitly
rejects doing *more* than the taxonomy-plus-hygiene-by-analogy approach it chose ("Rejected as
too weak" is about doing *less*, not about adding a second artifact) — inventing an unrequested
second document would itself be exactly the kind of silent scope addition the conformance
reviewer is charged with catching.

What *is* still missing, and is genuine, verifiable implementation work for this ticket:

1. Nothing in the repository mechanically pins the spec's five acceptance-criteria-bearing rules
   in place — a future edit to the spec (or a careless "cleanup") could silently weaken the
   `disable-model-invocation` default, reintroduce a `Bash(*)`-style grant example, or drop a path
   from the Review Expectations list, and nothing would fail. This plan adds a small pytest
   regression test that locks in the five acceptance-criteria strings verbatim, so CI catches any
   future silent weakening of this policy.
2. The repo's `README.md` `## Further reading` list already links the two other durable
   Dark-Factory-specific policy/contract docs (`docs/dark-factory-token-optimization.md`,
   `docs/dark-factory-memory-contract.md`) but does not yet link this spec, so a reader following
   that list has no path to the Claude Skills policy at all. This is a one-line, documentation-map
   fix squarely inside the conformance reviewer's own "Documentation exception" (updates to
   `README.md` that document something added by in-scope work are in-scope housekeeping, not an
   out-of-scope change).

## Architecture

This is a documentation-accuracy ticket: no backend, frontend, database, DAG, or scheduler changes.
Two files are touched: a new test file, and one `README.md` bullet. Because there is no
application code to test, the "write failing test → implement → verify pass" TDD shape from
`architect-prompt.md`'s conventions is adapted mechanically, following the same pattern used by
the sibling issue #41 plan (`docs/superpowers/plans/2026-07-10-dark-factory-prompt-surface-inventory-plan.md`):
the "test" is a pytest module asserting exact substrings from the spec and from `README.md`; the
"failure" is the one assertion that is genuinely false today (the missing `README.md` link) —
the other assertions pass immediately on first run because the policy content they pin already
exists in the committed spec, which is expected and is called out explicitly in Task 1 Step 2
below (mirroring how #41's Task 3 spot-checks confirmed several claims already accurate with no
correction needed).

## Tech Stack

- Python 3 stdlib only (`pathlib.Path`, string containment assertions) — no new dependencies.
- `pytest`, already the project's test runner (`python -m pytest tests/ -v` in
  `.github/workflows/ci.yml`); the new file is auto-discovered, no CI workflow edit needed.

## File Structure

| File | Change |
|---|---|
| `tests/test_claude_skills_policy_doc.py` | **New** — regression test pinning the spec's five acceptance-criteria rules and the `README.md` cross-link |
| `README.md` | Modified — one new bullet under `## Further reading` linking the spec |

No other files are created or modified. In particular, no changes are made to
`refinement-skills/`, `.claude/skills/refinement/`, `.factory/adapter.yaml`, or any
`commands/*.md` file — those are the spec's explicit follow-up tickets, out of scope here.

---

## Task 1: Pin the #42 policy content with a regression test, and link it from README

**Files:**
- `tests/test_claude_skills_policy_doc.py` (new)
- `README.md`

### Step 1 — Write the test file and confirm it fails (the README assertion, specifically)

Create `tests/test_claude_skills_policy_doc.py`:

```python
"""Regression test for issue #42 — Dark Factory Claude Skills conventions and safety policy.

Pins the spec's five acceptance-criteria-bearing rules to exact substrings so a future edit
that silently weakens the policy (e.g. loosening the disable-model-invocation default, or
dropping a path from Review Expectations) fails CI instead of passing silently.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-10-dark-factory-claude-skills-design.md"
README = REPO_ROOT / "README.md"


def _spec_text():
    assert SPEC.exists(), f"{SPEC} does not exist — issue #42's policy doc must live here"
    return SPEC.read_text(encoding="utf-8")


def test_ac1_policy_doc_exists_at_appropriate_docs_path():
    # Acceptance criterion 1: "Add a design/policy doc under an appropriate docs path."
    assert SPEC.exists()
    assert SPEC.is_relative_to(REPO_ROOT / "docs")


def test_ac2_side_effecting_skills_require_disable_model_invocation_by_default():
    # Acceptance criterion 2: implement/merge/close/deploy-like skills must not be
    # auto-triggered unless explicitly justified.
    text = _spec_text()
    assert "structurally incapable" in text
    assert "is **mandatory by default**" in text
    assert "# justification:" in text


def test_ac3_bare_bash_wildcard_is_banned():
    # Acceptance criterion 3: ban broad `allowed-tools: Bash(*)` style permissions.
    text = _spec_text()
    assert "banned unconditionally" in text
    assert "Bash(gh:*)" in text
    assert "Bash(git:*)" in text


def test_ac4_dynamic_injection_requires_compact_artifact_scripts():
    # Acceptance criterion 4: require dynamic injection to call compact artifact scripts,
    # not raw cat/git diff/comment dumps.
    text = _spec_text()
    assert "Raw `cat ARCHITECTURE.md`, raw `git diff`, and" in text
    for script in (
        "architecture_slice.py",
        "memory_retrieve.py",
        "comment_digest.py",
        "diff_rank.py",
    ):
        assert script in text, f"{script} missing from injection policy"


def test_ac5_review_expectations_cover_required_paths():
    # Acceptance criterion 5: define review expectations for .claude/skills/**,
    # .claude/settings.json, hooks, and plugin config.
    text = _spec_text()
    assert "### 7. Review Expectations" in text
    assert ".claude/skills/**" in text
    assert ".claude/settings.json" in text
    assert ".factory/hooks/**" in text


def test_readme_links_the_policy_spec():
    text = README.read_text(encoding="utf-8")
    assert "docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md" in text, (
        "README.md's ## Further reading list must link the #42 Claude Skills policy spec"
    )
```

Run it:

```bash
python -m pytest tests/test_claude_skills_policy_doc.py -v
```

Expected output — five pass, one fail (`test_readme_links_the_policy_spec`, since `README.md`
does not yet mention the spec):

```
tests/test_claude_skills_policy_doc.py::test_ac1_policy_doc_exists_at_appropriate_docs_path PASSED
tests/test_claude_skills_policy_doc.py::test_ac2_side_effecting_skills_require_disable_model_invocation_by_default PASSED
tests/test_claude_skills_policy_doc.py::test_ac3_bare_bash_wildcard_is_banned PASSED
tests/test_claude_skills_policy_doc.py::test_ac4_dynamic_injection_requires_compact_artifact_scripts PASSED
tests/test_claude_skills_policy_doc.py::test_ac5_review_expectations_cover_required_paths PASSED
tests/test_claude_skills_policy_doc.py::test_readme_links_the_policy_spec FAILED
```

The five immediate passes are expected and correct, not a test-authoring mistake: the spec
committed by the refine phase (commit `71d817b`) already contains the acceptance-criteria content
those assertions pin — there is no code change needed to make them true, only a regression
guard so they stay true. Only the `README.md` cross-link is genuinely new work.

### Step 2 — Add the README cross-link

In `README.md`, under `## Further reading`, add a new bullet directly after the
`docs/dark-factory-memory-contract.md` line:

```diff
 - [`docs/dark-factory-token-optimization.md`](docs/dark-factory-token-optimization.md) — token optimization operator guide
 - [`docs/dark-factory-memory-contract.md`](docs/dark-factory-memory-contract.md) — memory schema and lifecycle
+- [`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`](docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md) — Claude Skills naming, safety, and tool-permission policy
 - [`config/config.yaml`](config/config.yaml) — all policy knobs with inline documentation
```

### Step 3 — Re-run the test and confirm it fully passes

```bash
python -m pytest tests/test_claude_skills_policy_doc.py -v
```

Expected output:

```
tests/test_claude_skills_policy_doc.py::test_ac1_policy_doc_exists_at_appropriate_docs_path PASSED
tests/test_claude_skills_policy_doc.py::test_ac2_side_effecting_skills_require_disable_model_invocation_by_default PASSED
tests/test_claude_skills_policy_doc.py::test_ac3_bare_bash_wildcard_is_banned PASSED
tests/test_claude_skills_policy_doc.py::test_ac4_dynamic_injection_requires_compact_artifact_scripts PASSED
tests/test_claude_skills_policy_doc.py::test_ac5_review_expectations_cover_required_paths PASSED
tests/test_claude_skills_policy_doc.py::test_readme_links_the_policy_spec PASSED

======================== 6 passed in 0.XXs ========================
```

Also run the full suite once to confirm no regressions elsewhere:

```bash
python -m pytest tests/ -v
```

Expected: all pre-existing tests still pass; the new file adds 6 passing tests to the total.

### Step 4 — Commit

```bash
git add tests/test_claude_skills_policy_doc.py README.md
git commit -m "test(docs): pin #42 Claude Skills policy content and link spec from README"
```

---

## Completion Checklist

- [ ] `tests/test_claude_skills_policy_doc.py` added, pinning all five acceptance-criteria rules from the approved spec
- [ ] `README.md` `## Further reading` links `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`
- [ ] `python -m pytest tests/test_claude_skills_policy_doc.py -v` passes (6/6)
- [ ] `python -m pytest tests/ -v` shows no regressions
- [ ] One commit made on the current branch
- [ ] No changes to `refinement-skills/`, `.claude/skills/refinement/`, `.factory/adapter.yaml`, or any `commands/*.md` file — those are the spec's explicit follow-up tickets, out of scope here

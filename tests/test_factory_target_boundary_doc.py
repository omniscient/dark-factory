"""Drift guard for docs/factory-target-boundary.md (#201): every `path::symbol` citation
must resolve to a real file and a real symbol name in that file, and every required
section header must be present. Extend this file's REQUIRED_SECTIONS/assertions as the
doc grows; never let a citation go unpinned."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "factory-target-boundary.md"

CITATION_RE = re.compile(r"`([\w./-]+\.(?:py|sh))::([A-Za-z_][A-Za-z0-9_.]*)`")

REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
    "## Handoff manifest (A5)",
    "## Bypass prevention (A6)",
    "## Trust model",
    "## What is declared vs. what runs",
    "## Known gaps",
]


def _doc_text() -> str:
    assert DOC_PATH.is_file(), f"{DOC_PATH} does not exist"
    return DOC_PATH.read_text(encoding="utf-8")


def _normalized(content: str) -> str:
    """Collapse whitespace runs (including markdown line wraps) to a single space,
    so multi-word phrase assertions don't break when prose re-wraps across lines."""
    return re.sub(r"\s+", " ", content)


def _section(content: str, header: str) -> str:
    """Slice out one `## `-headed section's body, up to (not including) the next
    `## ` header or end of file — so a claim can be pinned to the section that is
    supposed to make it, not just found anywhere in the doc.

    Anchored to a full line (^...$, MULTILINE), not a bare substring search: the doc
    inline-mentions some of these headers in backticks in prose (e.g. the Overview's
    reference to `## Handoff manifest (A5)`), and content.index(header) would happily
    match that mention instead of the real section, silently slicing the wrong text.
    """
    match = re.search(r"^" + re.escape(header) + r"$", content, re.MULTILINE)
    assert match, f"section header not found on its own line: {header}"
    start = match.start()
    rest_start = match.end()
    next_idx = content.find("\n## ", rest_start)
    return content[start:] if next_idx == -1 else content[start:next_idx]


def test_doc_exists_and_has_required_sections():
    # Anchored to a full line (re.MULTILINE ^...$), not a bare substring check: the
    # Overview prose references some of these headers inline in backticks (e.g. when
    # explaining what docs/adapter-authoring-guide.md already covers), and a substring
    # check would pass on that mention even if the doc's own real section were deleted.
    content = _doc_text()
    for header in REQUIRED_SECTIONS:
        pattern = r"^" + re.escape(header) + r"$"
        assert re.search(pattern, content, re.MULTILINE), f"missing section: {header}"


def test_doc_citations_resolve_to_real_symbols():
    content = _doc_text()
    citations = CITATION_RE.findall(content)
    assert citations, "expected at least one path::symbol citation"
    for path_str, symbol in citations:
        target = REPO_ROOT / path_str
        assert target.is_file(), f"citation path does not exist: {path_str}"
        text = target.read_text(encoding="utf-8")
        # Symbols may be dotted (e.g. `Adapter.load`) to name a method on a class;
        # the attribute lookup itself isn't present verbatim in source, so verify the
        # last component (the actual def/attr name) rather than the full dotted path.
        last_component = symbol.rsplit(".", 1)[-1]
        assert last_component in text, f"symbol {symbol!r} not found in {path_str}"


def test_non_negotiables_cite_the_factory_owned_enforcement_sites():
    # Scoped to the Non-negotiables section itself: "adapter.py" appears in nearly every
    # section of this doc, so a doc-wide substring check would pass even if this section
    # never named the three enforcement sites.
    content = _doc_text()
    section = _section(content, "## Non-negotiables")
    # gate_blast_radius is the fourth site and the one that actually fires on every
    # self-target PR (operator review of PR #417); the count is pinned here so the doc
    # cannot quietly drop back to three.
    for symbol in (
        "adapter.py",
        "resolve_and_run",
        "producing_loop_factory_owned",
        "_boundary_escalation_findings",
    ):
        assert symbol in section


def test_live_trading_is_not_stated_as_a_factory_wide_non_negotiable():
    # Scoped to Non-negotiables, and checked alongside the "live trading" mention it
    # would actually modify -- a bare doc-wide ban on the phrase "permanently excluded"
    # doesn't test that live trading specifically isn't framed as forever off-limits.
    content = _doc_text()
    section = _normalized(_section(content, "## Non-negotiables"))
    assert "live trading" in section, "expected Non-negotiables to discuss live trading"
    assert "permanently excluded" not in section, (
        "live trading must not be stated as permanently excluded in Non-negotiables"
    )


def test_non_negotiables_cites_the_deploy_publish_exclusion_mechanism():
    content = _normalized(_section(_doc_text(), "## Non-negotiables"))
    assert "migration_seed_auth_patterns" in content
    assert "^deploy/" in content


def test_a2_section_links_to_authoring_guide_instead_of_restating_table():
    # Scoped to the A2 section body itself, not "anywhere in the doc" — the Overview
    # section already mentions docs/adapter-authoring-guide.md, so a doc-wide substring
    # check would pass before this task's own content exists (no red phase).
    content = _doc_text()
    section = _section(content, "## Side-effect levels and enforced profiles (A2)")
    assert "docs/adapter-authoring-guide.md" in section
    assert "_PROFILES" not in section, "A2 section must link out, not restate the level table"


def test_a3_section_names_verdict_schema_tokens():
    content = _doc_text()
    section = _section(content, "## Verifier contract (A3)")
    for token in ("STATUS", "GATE_TYPE", "FINDINGS_COUNT", "SEVERITY"):
        assert token in section, f"A3 section missing verdict-schema token: {token}"


def test_a6_names_the_floor_the_semantic_diff_and_the_kill_switch():
    # Uses _normalized() (collapses line-wrap whitespace) because these are prose
    # phrases spanning a hard-wrapped markdown paragraph, not single-line tokens.
    content = _normalized(_doc_text())
    for phrase in (
        "FACTORY_OWNED_CRITICAL_DIFF_FLOOR",
        "_boundary_escalation_findings",
        "never suppresses the floor or the semantic adapter diff",
    ):
        assert phrase in content, f"missing A6 phrase: {phrase}"


def test_trust_model_states_the_path_shim_limit_plainly():
    content = _normalized(_doc_text())
    assert "`PATH` shim" in content
    assert "not a security boundary against a deliberately hostile agent" in content
    assert "#196/D3" in content


def test_declared_vs_runs_names_phase_levels_config_key():
    content = _doc_text()
    assert "side_effect.phase_levels" in content
    section = _section(content, "## What is declared vs. what runs")
    assert "scripts/factory_core/side_effect.py::_PROFILES" in section, (
        "must name what level 5 actively enforces, not just say levels constrain nothing"
    )


def test_known_gaps_names_open_issues_and_ods():
    content = _doc_text()
    for token in ("#374", "#407", "#412", "#411", "OD1", "OD2", "OD3",
                  "Unpinned threshold literal"):
        assert token in content, f"missing known-gap reference: {token}"


def test_never_list_verbs_in_doc_match_side_effect_module():
    """The doc restates level 5's git/gh never-list verbatim -- the one table it
    duplicates rather than links. Pin it, or a change to _GH_NEVER/_GIT_NEVER leaves the
    doc silently wrong (operator plan gate, F4)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from factory_core import side_effect

    # Scoped to the never-list paragraph itself, not the whole doc: "auth" (one of the
    # short _GH_NEVER entries) is a substring of "authoritative"/"adapter-authoring-guide",
    # which appear elsewhere in the doc, so a doc-wide check would still pass even if the
    # never-list paragraph dropped "auth" outright.
    section = _normalized(_section(_doc_text(), "## What is declared vs. what runs"))
    start = section.index("regardless of level")
    end = section.index("the same list", start)
    never_list_text = section[start:end]

    expected_verbs = tuple(side_effect._GH_NEVER) + tuple(side_effect._GIT_NEVER)
    for verb in expected_verbs:
        assert verb in never_list_text, f"doc's never-list is missing {verb!r}"

    # Also catch the reverse drift: a verb quoted in the doc's never-list that the module
    # no longer denies (e.g. left behind after _GH_NEVER/_GIT_NEVER shrinks).
    quoted_tokens = re.findall(r"`([^`]+)`", never_list_text)
    doc_verbs = {
        token[len("git "):] if token.startswith("git ")
        else token[len("gh "):] if token.startswith("gh ")
        else token
        for token in quoted_tokens
    }
    stale = doc_verbs - set(expected_verbs)
    assert not stale, f"doc's never-list has verbs the module no longer denies: {stale}"


def test_every_doc_path_reference_exists():
    """CITATION_RE pins only .py/.sh symbols. The doc also cites design records and
    commands by path -- including specs still in the in-flight docs/superpowers/specs/
    tier, which a later archive step moves. Pin those too (operator plan gate, F5)."""
    content = _doc_text()
    refs = set(
        re.findall(
            r"`((?:docs|refinement-skills|commands|workflows|config|tests|scripts"
            r"|\.factory|\.archon|\.github)/[\w./-]+"
            r"\.(?:md|yaml|yml|sh|py))`",
            content,
        )
    )
    # The shims have no extension, so the suffix-anchored regex above never reaches
    # them; match them by their directory instead, so a mutated `scripts/shims/git-GONE`
    # is still checked rather than silently skipped. Repo-root docs have no directory
    # prefix at all and are matched as literals. (Operator review of PR #417: before
    # this, repointing `scripts/shims/git` at a nonexistent file passed 15/15.)
    refs.update(re.findall(r"`(scripts/shims/[\w.-]+)`", content))
    for literal in ("README.md", "CLAUDE.md"):
        if f"`{literal}`" in content:
            refs.add(literal)
    assert refs, "no path references found -- the regex or the doc changed shape"
    for rel in sorted(refs):
        assert (REPO_ROOT / rel).is_file(), f"doc cites a path that does not exist: {rel}"


def test_handoff_reason_code_is_real():
    """`producing_loop_factory_owned` is quoted bare in the doc; tie it to its source."""
    src = (REPO_ROOT / "scripts" / "factory_core" / "handoff.py").read_text(encoding="utf-8")
    assert "producing_loop_factory_owned" in src


def test_readme_links_to_boundary_doc_near_loops_row():
    # Uses every occurrence (via finditer), not str.index's first match: a TOC entry or
    # any earlier mention of either anchor would make a first-occurrence proximity check
    # fail spuriously even though the real table row and pointer sit right next to
    # each other.
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/factory-target-boundary.md" in readme
    loops_positions = [m.start() for m in re.finditer(r"\| `loops` \|", readme)]
    link_positions = [
        m.start() for m in re.finditer(re.escape("docs/factory-target-boundary.md"), readme)
    ]
    assert loops_positions, "no `loops` table row found"
    assert link_positions, "no docs/factory-target-boundary.md reference found"
    loops_lines = [readme.count("\n", 0, idx) for idx in loops_positions]
    link_lines = [readme.count("\n", 0, idx) for idx in link_positions]
    assert any(
        abs(loop_line - link_line) <= 4
        for loop_line in loops_lines
        for link_line in link_lines
    ), "pointer should be within a few lines of the `loops` table row"

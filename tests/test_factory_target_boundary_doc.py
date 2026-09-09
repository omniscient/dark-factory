"""Drift guard for docs/factory-target-boundary.md (#201): every `path::symbol` citation
must resolve to a real file and a real symbol name in that file, and every required
section header must be present. Extend this file's REQUIRED_SECTIONS/assertions as the
doc grows; never let a citation go unpinned."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "factory-target-boundary.md"

CITATION_RE = re.compile(r"`([\w./-]+\.(?:py|sh))::([A-Za-z_][A-Za-z0-9_]*)`")

REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
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
    supposed to make it, not just found anywhere in the doc."""
    start = content.index(header)
    rest_start = start + len(header)
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
        assert symbol in text, f"symbol {symbol!r} not found in {path_str}"


def test_non_negotiables_cite_the_three_factory_owned_enforcement_sites():
    content = _doc_text()
    for symbol in ("adapter.py", "resolve_and_run", "producing_loop_factory_owned"):
        assert symbol in content


def test_live_trading_is_not_stated_as_a_factory_wide_non_negotiable():
    content = _doc_text()
    assert "permanently excluded" not in content


def test_non_negotiables_cites_the_deploy_publish_exclusion_mechanism():
    content = _normalized(_doc_text())
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

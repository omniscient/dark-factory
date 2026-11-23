"""Drift guard for docs/hybrid-execution-contract.md (#556), the durable hybrid cloud + local
model execution contract. Pins the decision report's required sections, its version and
acceptance-status lines, the responsibility-map owners, the four versioned contracts, the #239
spec delta and the fail-closed invariants to exact substrings, and checks that every cited repo
file still resolves. Presence checks only: whether the prose is true is the reviewers' job.

The design spec this doc was promoted from is archived to docs/archive/ by push-and-pr, so
nothing here (or in the doc) may reference the spec's docs/superpowers/specs/ path."""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "hybrid-execution-contract.md"

REQUIRED_SECTIONS = [
    "## About this document",
    "## Invariants (index)",
    "## Overview / Problem Statement",
    "## Requirements",
    "## Architecture / Approach",
    "### 4.1 Responsibility map — nine initiative slices to acceptance predicates",
    "### 4.2 Actor / worker / checker coexistence and nested Agent-alias behavior",
    "### 4.3 Confidentiality, destination policy, and non-exporting outage behavior",
    "### 4.4 Request correlation",
    "### 4.5 Capability evidence",
    "### 4.6 Timeouts and cancellation (policy)",
    "### 4.7 Versioned contracts and ledger extension",
    "### 4.8 Compatibility matrix — helper boundary vs. full Claude-harness compatibility",
    "### 4.9 Exact spec delta for #239",
    "### 4.10 Budgets, breaker and session-window invariants",
    "### 4.11 v1 selected path and accepted operator defaults",
    "### 4.12 Risks and rollout/rollback rules",
    "## Alternatives Considered",
]

SLICE_OWNERS = ["#557", "#558", "#559", "#560", "#561", "#562", "#239", "#238", "#563", "#564"]

FAIL_CLOSED_INVARIANTS = [
    "blocks and escalates — it is never silently rerouted",
    "A verdict produced inside a local-routed container is void",
    "never retries against a cloud",
    "`local_only` work never auto-merges",
    "no grace period",
    "`cost_usd: null`",
    "must never write the shared session-window pause",
    "byte-identical to today's `github/github/anthropic` default",
]

CITATION_RE = re.compile(r"`([\w.][\w./-]*\.(?:py|sh|md|ya?ml))`")

# Citations in the doc that intentionally use a short/partial path rather than the
# full repo-relative one, for prose readability. Each must still resolve to exactly
# one tracked file by path suffix.
PARTIAL_CITATION_ALLOWLIST = {
    "VERIFIER-CONTRACT.md",
    "budget_gate.sh",
    "model_proxy.py",
    "providers/__init__.py",
    "providers/codehost/gitlab.py",
    "providers/model.py",
    "run_record.py",
    "session_window.py",
}


def _doc_text() -> str:
    assert DOC_PATH.is_file(), f"{DOC_PATH} does not exist — #556's decision report must live here"
    return DOC_PATH.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Body of `heading` up to the next ##/### heading."""
    assert heading + "\n" in text, f"missing section: {heading}"
    rest = text[text.index(heading + "\n") + len(heading) + 1:]
    nxt = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def test_header_carries_issue_version_and_acceptance_status():
    head = " ".join(_doc_text().split("\n## ", 1)[0].split())
    assert "**Issue:** #556" in head
    assert '**Contract version:** `hybrid_contract: "1.0"`' in head
    assert "**Status:** Accepted" in head
    assert "the #556 PR merged and this is the accepted copy on `main`" in head
    assert "confirmed as written by the owner in #580" in head


def test_required_sections_present_in_order():
    text = _doc_text()
    positions = []
    for heading in REQUIRED_SECTIONS:
        assert re.search(rf"^{re.escape(heading)}$", text, re.MULTILINE), f"missing section: {heading}"
        positions.append(text.index(heading + "\n"))
    assert positions == sorted(positions), "required sections are out of order"


def test_responsibility_map_names_every_slice_owner():
    section = _section(_doc_text(), "### 4.1 Responsibility map — nine initiative slices to acceptance predicates")
    first_cells = [line.split("|")[1] for line in section.splitlines() if line.startswith("| ")]
    for owner in SLICE_OWNERS:
        assert any(f"({owner}" in cell for cell in first_cells), f"no responsibility-map row owned by {owner}"


def test_four_contracts_are_independently_versioned():
    section = _section(_doc_text(), "### 4.7 Versioned contracts and ledger extension")
    assert '`"MAJOR.MINOR"`' in section
    for contract, owner in (("Configuration", "#557"), ("Request", "#558"), ("Result", "#558"),
                            ("Capability evidence", "#561/#562")):
        assert f"| {contract} | {owner} |" in section, f"{contract} contract row missing"
    assert "major bumps are a hard reject" in section


def test_capability_evidence_schema_never_qualifies_a_checker():
    section = _section(_doc_text(), "### 4.5 Capability evidence")
    assert 'schema_version: "1.0"' in section
    roles = re.search(r"^\s*qualified_roles: \[([^\]]*)\]", section, re.MULTILINE)
    assert roles and [r.strip() for r in roles.group(1).split(",")] == ["actor"]
    for field in ("endpoint_id:", "model_identity:", "fixture_suite_version:", "expires_at:",
                  "invalidated_by:", "harness_version:"):
        assert field in section, f"capability-evidence field {field} missing"
    assert "unsupported" in section


def test_spec_delta_for_239_lists_items_12_to_15():
    section = _section(_doc_text(), "### 4.9 Exact spec delta for #239")
    for n in (12, 13, 14, 15):
        assert re.search(rf"^{n}\. ", section, re.MULTILINE), f"#239 delta item {n} missing"


def test_fail_closed_invariants_pinned():
    text = " ".join(_doc_text().split())
    for phrase in FAIL_CLOSED_INVARIANTS:
        assert phrase in text, f"fail-closed invariant weakened or removed: {phrase}"


def test_section_4_11_has_five_accepted_decisions():
    section = _section(_doc_text(), "### 4.11 v1 selected path and accepted operator defaults")
    items = re.split(r"^\d+\. ", section, flags=re.MULTILINE)[1:]
    assert len(items) == 5, f"expected five §4.11 decisions, found {len(items)}"
    for n, item in enumerate(items, 1):
        assert "Accepted operator default (2026-11-22" in " ".join(item.split()), (
            f"§4.11 decision {n} does not carry the accepted-default marker")


def test_invariants_index_cites_existing_sections():
    text = _doc_text()
    body = _section(text, "## Invariants (index)")
    items = [item.strip() for item in re.split(r"^\d+\. ", body, flags=re.MULTILINE)[1:]]
    assert len(items) >= 15, f"expected at least 15 indexed invariants, found {len(items)}"
    headings = set(re.findall(r"^### (4\.\d+) ", text, re.MULTILINE))
    for item in items:
        cited = re.findall(r"§(4\.\d+)", item)
        assert cited, f"invariant does not cite a section: {item[:60]}"
        for sec in cited:
            assert sec in headings, f"invariant cites missing §{sec}: {item[:60]}"


def _tracked_files() -> list:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


def test_cited_repo_files_resolve():
    text = _doc_text()
    files = _tracked_files()
    missing = []
    for tok in sorted(set(CITATION_RE.findall(text))):
        if (REPO_ROOT / tok).is_file():
            continue
        if tok in PARTIAL_CITATION_ALLOWLIST and any(f.endswith("/" + tok) for f in files):
            continue
        missing.append(tok)
    assert not missing, f"cited files no longer resolve: {missing}"


def test_doc_does_not_cite_the_pre_archive_spec_path():
    assert "docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md" not in _doc_text()


def test_readme_further_reading_links_the_contract():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "[`docs/hybrid-execution-contract.md`](docs/hybrid-execution-contract.md)" in readme


def test_provider_design_model_axis_points_at_the_contract():
    text = (REPO_ROOT / "docs" / "provider-abstraction-design.md").read_text(encoding="utf-8")
    start_heading = "### 7.5 Selection, preflight, caveats"
    end_heading = "## 8. Scope boundaries"
    assert start_heading in text, f"missing section: {start_heading}"
    assert end_heading in text, f"missing section: {end_heading}"
    section = text[text.index(start_heading):text.index(end_heading)]
    assert "[`docs/hybrid-execution-contract.md`](hybrid-execution-contract.md)" in section
    assert "(#556)" in section

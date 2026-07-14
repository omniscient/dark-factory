from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPO_ROOT / "docs" / "adapter-authoring-guide.md"


def _text():
    return GUIDE.read_text(encoding="utf-8")


def test_guide_exists_with_required_top_level_sections():
    text = _text()
    for heading in (
        "## Overview",
        "## Tracker adapter",
        "## Code-host adapter",
        "## Model-endpoint adapter",
        "## Cross-axis concerns",
        "## Worked example: GitLab CodeHost seam proof",
    ):
        assert heading in text, f"missing section: {heading}"


def test_guide_documents_tracker_required_methods():
    text = _text()
    for method in (
        "list_work_items", "get_item", "get_comments", "get_children", "set_status",
        "add_label", "remove_label", "upsert_comment", "create_item", "resolve_item",
        "get_status_limits", "get_rate_budget",
    ):
        assert f"`{method}" in text, f"missing tracker method: {method}"
    assert "FACTORY_STATUS_BACKLOG" in text
    assert "ready, in_progress, in_review, blocked, done, backlog, refined" in text


def test_guide_documents_codehost_required_methods():
    text = _text()
    for method in (
        "remote_url", "find_change_for", "open_change", "update_change_body", "mark_ready",
        "merge_change", "get_change_checks", "get_change_mergeable", "get_change_reviews",
        "get_change_inline_comments", "close_keyword",
    ):
        assert f"`{method}" in text, f"missing code-host method: {method}"


def test_guide_documents_model_endpoint_paths():
    text = _text()
    for token in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "databricks", "openai", "bedrock", "vertex"):
        assert token in text


def test_guide_documents_cross_axis_concerns():
    text = _text()
    assert "host.merge_change(id)" in text
    assert "tracker.resolve_item(issue_id)" in text
    assert "FACTORY_TRACKER" in text and "FACTORY_CODEHOST" in text and "FACTORY_MODEL_PROVIDER" in text


def test_guide_links_gitlab_worked_example():
    text = _text()
    assert "scripts/factory_core/providers/codehost/gitlab.py" in text
    assert "test_provider_codehost_gitlab.py" in text or "test_provider_codehost_contract.py" in text


def test_guide_cites_design_doc_sections():
    text = _text()
    assert "provider-abstraction-design.md" in text
    for section in ("§5.1", "§5.2", "§6.1", "§6.3", "§7"):
        assert section in text, f"missing design-doc citation: {section}"

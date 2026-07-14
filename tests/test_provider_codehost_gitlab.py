import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import identity


def test_remote_url_uses_oauth2_form_with_default_host(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-faketoken")
    url = GitLabCodeHost().remote_url()
    assert url == f"https://oauth2:glpat-faketoken@gitlab.com/{identity.SLUG}.git"


def test_remote_url_honors_self_hosted_base_url(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("GITLAB_TOKEN", "glpat-faketoken")
    monkeypatch.setenv("GITLAB_BASE_URL", "gitlab.example.com")
    url = GitLabCodeHost().remote_url()
    assert url == f"https://oauth2:glpat-faketoken@gitlab.example.com/{identity.SLUG}.git"


def test_close_keyword_empty_when_gitlab_not_the_tracker(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("FACTORY_TRACKER", "github")
    assert GitLabCodeHost().close_keyword("99") == ""


def test_close_keyword_empty_by_default(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.delenv("FACTORY_TRACKER", raising=False)
    assert GitLabCodeHost().close_keyword("99") == ""


def test_close_keyword_present_when_gitlab_is_also_the_tracker(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("FACTORY_TRACKER", "gitlab")
    assert GitLabCodeHost().close_keyword("99") == "Closes #99"


def test_draft_title_adds_prefix_once():
    from factory_core.providers.codehost.gitlab import _draft_title

    assert _draft_title("Fix the thing") == "Draft: Fix the thing"
    assert _draft_title("Draft: Fix the thing") == "Draft: Fix the thing"


def test_strip_draft_prefix_removes_it():
    from factory_core.providers.codehost.gitlab import _strip_draft_prefix

    assert _strip_draft_prefix("Draft: Fix the thing") == "Fix the thing"
    assert _strip_draft_prefix("Fix the thing") == "Fix the thing"


def test_validate_change_id_accepts_opaque_gitlab_shape():
    from factory_core.providers.codehost.gitlab import _validate_change_id

    assert _validate_change_id("group/project!42") == "group/project!42"


def test_validate_change_id_rejects_non_string():
    from factory_core.providers.codehost.gitlab import _validate_change_id

    with pytest.raises(ValueError):
        _validate_change_id(42)

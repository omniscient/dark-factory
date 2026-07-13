import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def test_get_tracker_returns_github_tracker():
    from factory_core.providers import get_tracker
    from factory_core.providers.tracker.github import GitHubTracker
    assert isinstance(get_tracker(), GitHubTracker)


def test_get_codehost_returns_github_codehost():
    from factory_core.providers import get_codehost
    from factory_core.providers.codehost.github import GitHubCodeHost
    assert isinstance(get_codehost(), GitHubCodeHost)


def test_get_tracker_explicit_github_selection(monkeypatch):
    from factory_core.providers import get_tracker
    from factory_core.providers.tracker.github import GitHubTracker
    monkeypatch.setenv("FACTORY_TRACKER", "github")
    assert isinstance(get_tracker(), GitHubTracker)


def test_get_codehost_explicit_github_selection(monkeypatch):
    from factory_core.providers import get_codehost
    from factory_core.providers.codehost.github import GitHubCodeHost
    monkeypatch.setenv("FACTORY_CODEHOST", "github")
    assert isinstance(get_codehost(), GitHubCodeHost)


def test_get_tracker_unknown_raises(monkeypatch):
    from factory_core.providers import ProviderConfigError, get_tracker
    monkeypatch.setenv("FACTORY_TRACKER", "jira")
    with pytest.raises(ProviderConfigError, match="Unknown FACTORY_TRACKER 'jira'"):
        get_tracker()


def test_get_codehost_unknown_raises(monkeypatch):
    from factory_core.providers import ProviderConfigError, get_codehost
    monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
    with pytest.raises(ProviderConfigError, match="Unknown FACTORY_CODEHOST 'gitlab'"):
        get_codehost()


def test_preflight_default_env_fails_on_missing_tokens(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    problems = preflight()
    assert "GH_TOKEN is not set. Add it to .archon/.env" in problems
    assert "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" in problems


def test_preflight_default_env_passes_with_tokens_set(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    assert preflight() == []


def test_preflight_missing_gh_token_flags_both_tracker_and_codehost(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    problems = preflight()
    assert problems.count("GH_TOKEN is not set. Add it to .archon/.env") == 2


def test_preflight_unknown_tracker_codehost_model_all_reported(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.setenv("FACTORY_TRACKER", "jira")
    monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
    monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "cohere")
    problems = preflight()
    assert "Unknown FACTORY_TRACKER 'jira'" in problems
    assert "Unknown FACTORY_CODEHOST 'gitlab'" in problems
    assert "Unknown FACTORY_MODEL_PROVIDER 'cohere'" in problems


def test_preflight_databricks_not_yet_implemented(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "databricks")
    problems = preflight()
    assert any("requires the model gateway" in p and "databricks" in p for p in problems)

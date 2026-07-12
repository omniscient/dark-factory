import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_get_tracker_returns_github_tracker():
    from factory_core.providers import get_tracker
    from factory_core.providers.tracker.github import GitHubTracker
    assert isinstance(get_tracker(), GitHubTracker)


def test_get_codehost_returns_github_codehost():
    from factory_core.providers import get_codehost
    from factory_core.providers.codehost.github import GitHubCodeHost
    assert isinstance(get_codehost(), GitHubCodeHost)

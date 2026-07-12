"""Provider registry (parent spec docs/provider-abstraction-design.md §4).

No selection logic in this ticket — always returns the GitHub reference adapters.
FACTORY_TRACKER / FACTORY_CODEHOST env-based selection is parent spec step 3.
"""


def get_tracker():
    from factory_core.providers.tracker.github import GitHubTracker
    return GitHubTracker()


def get_codehost():
    from factory_core.providers.codehost.github import GitHubCodeHost
    return GitHubCodeHost()

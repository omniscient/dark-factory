"""Provider registry (parent spec docs/provider-abstraction-design.md §4).

Selection is env-driven: FACTORY_TRACKER / FACTORY_CODEHOST /
FACTORY_MODEL_PROVIDER (parent spec step 3), defaulting to today's
github/github/anthropic — unset env is byte-identical to the pre-#250
hardcoded-GitHub behavior.
"""
import os

from factory_core.providers import model
from factory_core.providers.codehost.github import GitHubCodeHost
from factory_core.providers.tracker.github import GitHubTracker

_TRACKERS = {"github": GitHubTracker}    # extended by later tickets (jira, ...)
_CODEHOSTS = {"github": GitHubCodeHost}  # extended by later tickets (gitlab, ...)


class ProviderConfigError(Exception):
    """A selected FACTORY_TRACKER/FACTORY_CODEHOST/FACTORY_MODEL_PROVIDER is unknown."""


def get_tracker():
    name = os.environ.get("FACTORY_TRACKER", "github")
    cls = _TRACKERS.get(name)
    if cls is None:
        raise ProviderConfigError(f"Unknown FACTORY_TRACKER '{name}'")
    return cls()


def get_codehost():
    name = os.environ.get("FACTORY_CODEHOST", "github")
    cls = _CODEHOSTS.get(name)
    if cls is None:
        raise ProviderConfigError(f"Unknown FACTORY_CODEHOST '{name}'")
    return cls()


def _missing_env(required: list[str]) -> list[str]:
    return [f"{var} is not set. Add it to .archon/.env" for var in required if not os.environ.get(var)]


def preflight() -> list[str]:
    """Return a list of human-readable problems; empty list == OK."""
    problems = []

    tracker_name = os.environ.get("FACTORY_TRACKER", "github")
    tracker_cls = _TRACKERS.get(tracker_name)
    if tracker_cls is None:
        problems.append(f"Unknown FACTORY_TRACKER '{tracker_name}'")
    else:
        problems += _missing_env(tracker_cls.required_env())

    codehost_name = os.environ.get("FACTORY_CODEHOST", "github")
    codehost_cls = _CODEHOSTS.get(codehost_name)
    if codehost_cls is None:
        problems.append(f"Unknown FACTORY_CODEHOST '{codehost_name}'")
    else:
        problems += _missing_env(codehost_cls.required_env())

    model_name = os.environ.get("FACTORY_MODEL_PROVIDER", "anthropic")
    problems += model.preflight(model_name)

    return problems

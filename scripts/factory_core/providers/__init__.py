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
from factory_core.providers.tracker.jira import JiraTracker

_TRACKERS = {"github": GitHubTracker, "jira": JiraTracker}
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


# Shared with model.py, which defines the canonical implementation to avoid drift
# between the two preflight paths.
_missing_env = model._missing


def preflight(side: str = "run") -> list[str]:
    """Return a list of human-readable problems; empty list == OK.

    side="scheduler" is scheduler.sh's boot check; the default is the run container's
    (entrypoint.sh). The two differ only when FACTORY_MODEL_ENDPOINT is set (#557).
    """
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
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
    if endpoint_id:
        problems += _endpoint_preflight(side, endpoint_id, model_name)
        return problems
    if os.environ.get("FACTORY_MODEL_REGISTRY_SHA256"):
        problems.append("FACTORY_MODEL_REGISTRY_SHA256 is set but FACTORY_MODEL_ENDPOINT is not "
                        "— a registry digest without an endpoint selection is refused")
    problems += model.preflight(model_name)

    return problems


def _endpoint_preflight(side: str, endpoint_id: str, model_name: str) -> list[str]:
    # Imported here so the default path (FACTORY_MODEL_ENDPOINT unset) never loads it.
    from factory_core.providers import model_endpoint

    if side == "scheduler":
        registry_dir = model_endpoint.SOURCE_REGISTRY_DIR
    else:
        registry_dir = model_endpoint.staged_registry_dir()
    # Read before preflight(), so its closing digest check also covers this read.
    hosting = model_endpoint.hosting_location_of(registry_dir, endpoint_id)
    if side == "scheduler":
        problems = model_endpoint.scheduler_preflight(registry_dir, endpoint_id)
    else:
        problems = model_endpoint.preflight(
            registry_dir, registry_dir / model_endpoint.EVIDENCE_DIR, endpoint_id)
    # A local selection holds no cloud model credential, so the cloud credential check is
    # skipped for it; a cloud selection runs it in addition to the endpoint checks. An
    # unknown id or invalid/unverified registry (hosting is None) already fails visibly via
    # the endpoint checks above — running the cloud check too would add unrelated
    # credential errors to that failure.
    if hosting == "cloud":
        problems += model.preflight(model_name)
    return problems

"""#489: every provider env var must reach dispatched run containers.

Run containers read .archon/.env via env_file and get nothing from the scheduler's
instance.env except what scheduler.sh forwards. A compose `environment:` entry
cannot do this safely (`VAR: ${VAR:-}` blanks the env_file value; bare `VAR:`
deletes it), so scripts/provider_env.sh forwards each set variable as
`docker compose run -e VAR`, which leaves env_file in control when unset.
"""
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

# Names a provider needs that are deliberately NOT forwarded, with the reason.
ALLOWLIST = {
    "GH_TOKEN": "secret delivered via .archon/.env; forwarding the scheduler's value could "
                "change which credential a default deployment uses",
    "CLAUDE_CODE_OAUTH_TOKEN": "secret delivered via .archon/.env (default anthropic path)",
    "ANTHROPIC_API_KEY": "secret delivered via .archon/.env (default anthropic path)",
}
_PLATFORM = "platform-injected marker describing the scheduler's own runtime, not config for the run"
ALLOWLIST.update({n: _PLATFORM for n in (
    "AWS_EXECUTION_ENV", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "K_SERVICE", "GAE_APPLICATION",
    "FUNCTION_TARGET", "KUBERNETES_SERVICE_HOST")})
SELECTION = ["FACTORY_TRACKER", "FACTORY_CODEHOST", "FACTORY_MODEL_PROVIDER"]


def _forwarded() -> set[str]:
    text = (REPO / "scripts" / "provider_env.sh").read_text(encoding="utf-8")
    m = re.search(r"PROVIDER_PASSTHROUGH_VARS=\((.*?)\n\)", text, re.S)
    assert m, "PROVIDER_PASSTHROUGH_VARS array missing from scripts/provider_env.sh"
    return set(re.sub(r"#.*", "", m.group(1)).split())


def _required_names() -> set[str]:
    from factory_core.providers import model
    from factory_core.providers.codehost.github import GitHubCodeHost
    from factory_core.providers.codehost.gitlab import GitLabCodeHost
    from factory_core.providers.tracker.github import GitHubTracker
    from factory_core.providers.tracker.jira import JiraTracker

    names = set(SELECTION)
    for cls in (GitHubTracker, JiraTracker, GitHubCodeHost, GitLabCodeHost):
        names.update(cls.required_env())
    # Model descriptors expose no required_env(); recover the names from what each
    # provider's preflight reports with an empty environment.
    saved = dict(os.environ)
    try:
        os.environ.clear()
        for provider in model._MODEL_PROVIDERS:
            for problem in model.preflight(provider):
                names.update(re.findall(r"\b([A-Z][A-Z0-9_]{3,}) is not set", problem))
                for pair in re.findall(r"Set ([A-Z_]+) or ([A-Z_]+) in", problem):
                    names.update(pair)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    names.update(model._AWS_ALT_AUTH_SIGNALS)
    names.update(model._GCP_ALT_AUTH_SIGNALS)
    return names


def test_every_provider_env_is_forwarded_or_allowlisted():
    forwarded = _forwarded()
    missing = sorted(n for n in _required_names() if n not in forwarded and n not in ALLOWLIST)
    assert not missing, f"provider env not passed to run containers (#489): {missing}"


def test_allowlisted_secrets_are_not_forwarded():
    assert not set(ALLOWLIST) & _forwarded()


def test_selection_and_optional_vars_forwarded():
    f = _forwarded()
    assert set(SELECTION) <= f
    assert {"JIRA_ISSUE_TYPE", "JIRA_REQUEST_TIMEOUT_SECONDS", "GITLAB_BASE_URL"} <= f


def test_forwarded_vars_not_also_in_compose_environment():
    compose = (REPO / "run-compose.yml").read_text(encoding="utf-8")
    for name in _forwarded():
        assert not re.search(rf"^\s+{name}:", compose, re.M), \
            f"{name} in run-compose.yml environment: would override or blank env_file"


def test_dispatch_uses_provider_env_flags():
    sched = (REPO / "scheduler.sh").read_text(encoding="utf-8")
    assert "scripts/provider_env.sh" in sched
    dispatch = re.search(r"^dispatch\(\) \{.*?^\}", sched, re.S | re.M).group(0)
    assert "provider_env_flags" in dispatch and '"${env_flags[@]}"' in dispatch


def test_model_endpoint_selection_forwarded_but_digest_and_path_are_not():
    # #557: the registry id is instance config; the digest is per dispatch (dispatch() adds it
    # with its own -e) and FACTORY_MODEL_REGISTRY_PATH is a test-only override.
    f = _forwarded()
    assert "FACTORY_MODEL_ENDPOINT" in f
    assert not {"FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"} & f

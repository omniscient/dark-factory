"""Model-endpoint provider descriptors (parent spec
docs/provider-abstraction-design.md §7). No behavioral ABC: Claude Code
itself reads ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/CLAUDE_CODE_USE_BEDROCK/
CLAUDE_CODE_USE_VERTEX natively (§7.1) — this module only declares what
boot preflight must validate per selected provider."""
import os


def _missing(names: list[str]) -> list[str]:
    """Shared by scripts/factory_core/providers/__init__.py (imported from there as
    `_missing_env`) so both places check .archon/.env-sourced config vars the same way."""
    return [f"{name} is not set. Add it to .archon/.env" for name in names if not os.environ.get(name)]


def _missing_cloud_cred(names: list[str], hint: str) -> list[str]:
    """Like _missing, but for vars that normally come from a cloud provider's own
    credential chain rather than .archon/.env — the message must point users there."""
    return [f"{name} is not set ({hint})" for name in names if not os.environ.get(name)]


def _anthropic_check() -> list[str]:
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return ["Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    return []


# Env vars that signal AWS/GCP credentials are supplied by something other than a
# static key pair / service-account key file: IAM instance profiles, ECS task roles,
# EKS IRSA, Lambda execution roles, Application Default Credentials, and workload
# identity on GKE/Cloud Run/Cloud Functions. A bare EC2 instance profile or GCE
# metadata-server ADC has no env-var signal at all and can't be detected without a
# network call to the metadata service, which preflight intentionally avoids — those
# cases fall through to the static-var check below.
_AWS_ALT_AUTH_SIGNALS = (
    "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SESSION_TOKEN", "AWS_EXECUTION_ENV",
)
_GCP_ALT_AUTH_SIGNALS = (
    "GOOGLE_CLOUD_PROJECT", "K_SERVICE", "GAE_APPLICATION", "FUNCTION_TARGET",
    "KUBERNETES_SERVICE_HOST",
)


def _bedrock_check() -> list[str]:
    problems = _missing(["CLAUDE_CODE_USE_BEDROCK", "AWS_REGION"])
    if not any(os.environ.get(v) for v in _AWS_ALT_AUTH_SIGNALS):
        problems += _missing_cloud_cred(
            ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "set it, or configure an IAM role/instance profile via the AWS credential chain",
        )
    return problems


def _vertex_check() -> list[str]:
    problems = _missing(["CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION"])
    if not any(os.environ.get(v) for v in _GCP_ALT_AUTH_SIGNALS):
        problems += _missing_cloud_cred(
            ["GOOGLE_APPLICATION_CREDENTIALS"],
            "set it, or configure Application Default Credentials / workload identity via the GCP credential chain",
        )
    return problems


def _not_yet_implemented(name: str) -> list[str]:
    return [f"FACTORY_MODEL_PROVIDER={name} requires the model gateway, which is not yet "
            f"implemented — see docs/provider-abstraction-design.md §11 step 5"]


_MODEL_PROVIDERS = {
    "anthropic": _anthropic_check,
    "bedrock": _bedrock_check,
    "vertex": _vertex_check,
    "databricks": lambda: _not_yet_implemented("databricks"),
    "openai": lambda: _not_yet_implemented("openai"),
}


def preflight(name: str) -> list[str]:
    check = _MODEL_PROVIDERS.get(name)
    if check is None:
        return [f"Unknown FACTORY_MODEL_PROVIDER '{name}'"]
    return check()

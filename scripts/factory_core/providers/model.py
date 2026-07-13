"""Model-endpoint provider descriptors (parent spec
docs/provider-abstraction-design.md §7). No behavioral ABC: Claude Code
itself reads ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/CLAUDE_CODE_USE_BEDROCK/
CLAUDE_CODE_USE_VERTEX natively (§7.1) — this module only declares what
boot preflight must validate per selected provider."""
import os


def _missing(names: list[str]) -> list[str]:
    return [f"{name} is not set. Add it to .archon/.env" for name in names if not os.environ.get(name)]


def _anthropic_check() -> list[str]:
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return ["Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    return []


def _bedrock_check() -> list[str]:
    return _missing(["CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"])


def _vertex_check() -> list[str]:
    return _missing([
        "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS",
    ])


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

"""Tests for per-feature token optimization flags (R1) — T1, T2, T6."""
import pathlib
import sys

import pytest
import yaml

CONFIG = pathlib.Path(__file__).resolve().parents[1] / "config/config.yaml"


def _tok_opt():
    return yaml.safe_load(CONFIG.read_text()).get("token_optimization", {})


# ── T1: config.yaml enabled flags ────────────────────────────────────────────

def test_architecture_enabled_flag_exists():
    assert "enabled" in _tok_opt().get("architecture", {}), \
        "architecture sub-section must have an 'enabled' key"


def test_memory_enabled_flag_exists():
    assert "enabled" in _tok_opt().get("memory", {}), \
        "memory sub-section must have an 'enabled' key"


def test_comments_enabled_flag_exists():
    assert "enabled" in _tok_opt().get("comments", {}), \
        "comments sub-section must have an 'enabled' key"


def test_diff_enabled_flag_exists():
    assert "enabled" in _tok_opt().get("diff", {}), \
        "diff sub-section must have an 'enabled' key"


# ── T2: scheduler.sh env var wiring (#180: via effective_config.ENV_WHITELIST) ──

def _scheduler_wired(var, dotted):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from factory_core import effective_config as ec
    return (var, dotted) in {(v, d) for v, d, readers in ec.ENV_WHITELIST
                             if "scheduler" in readers}


def test_scheduler_wires_architecture_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED",
                            "token_optimization.architecture.enabled")


def test_scheduler_wires_memory_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_MEMORY_ENABLED", "token_optimization.memory.enabled")


def test_scheduler_wires_comments_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_COMMENTS_ENABLED",
                            "token_optimization.comments.enabled")


def test_scheduler_wires_diff_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_DIFF_ENABLED", "token_optimization.diff.enabled")


# ── T6: workflow YAML comment digest gate ─────────────────────────────────────

def test_workflow_gates_comment_digest_on_env_var():
    content = (
        pathlib.Path(__file__).resolve().parents[1]
        / "workflows/archon-dark-factory.yaml"
    ).read_text(encoding="utf-8")
    assert "TOKEN_OPTIMIZATION_COMMENTS_ENABLED" in content


# ── #487: dead keys must not creep back into config.yaml ─────────────────────

_DEAD_ENV_NAMES = (
    "TOKEN_OPTIMIZATION_ENABLED",
    "TOKEN_OPTIMIZATION_DEFAULT_BUDGET_TOKENS",
    "TOKEN_OPTIMIZATION_CHEAP_MODEL_FIRST",
)


def _repo_text(*rel):
    root = pathlib.Path(__file__).resolve().parents[1]
    parts = []
    for r in rel:
        base = root / r
        files = [base] if base.is_file() else list(base.rglob("*"))
        for p in files:
            if p.is_file() and p.suffix in {".py", ".sh", ".yaml", ".yml", ".md"}:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_removed_dead_keys_not_in_config():
    """token_optimization.enabled and escalation.cheap_model_first have no reader (#487)."""
    to = _tok_opt()
    assert "enabled" not in to, "top-level token_optimization.enabled has no reader"
    assert "cheap_model_first" not in (to.get("escalation") or {}), \
        "escalation.cheap_model_first has no reader"


def test_removed_env_names_not_advertised_in_config():
    text = CONFIG.read_text(encoding="utf-8")
    for name in _DEAD_ENV_NAMES:
        assert name not in text, f"{name} has no reader; do not advertise it in config.yaml"


def test_removed_env_names_have_no_reader():
    code = _repo_text("scripts", "scheduler.sh", "entrypoint.sh", "workflows", "commands")
    for name in _DEAD_ENV_NAMES:
        assert name not in code, f"{name} is read somewhere; restore the key and its docs"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_anthropic_passes_with_oauth_token(monkeypatch):
    from factory_core.providers import model
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert model.preflight("anthropic") == []


def test_anthropic_passes_with_api_key(monkeypatch):
    from factory_core.providers import model
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert model.preflight("anthropic") == []


def test_anthropic_fails_with_neither_token(monkeypatch):
    from factory_core.providers import model
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert model.preflight("anthropic") == [
        "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"
    ]


def test_bedrock_passes_with_full_env(monkeypatch):
    from factory_core.providers import model
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert model.preflight("bedrock") == []


def test_bedrock_fails_with_nothing_set(monkeypatch):
    from factory_core.providers import model
    for var in ("CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        monkeypatch.delenv(var, raising=False)
    problems = model.preflight("bedrock")
    assert len(problems) == 4
    assert "AWS_REGION is not set. Add it to .archon/.env" in problems


def test_vertex_passes_with_full_env(monkeypatch):
    from factory_core.providers import model
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")
    assert model.preflight("vertex") == []


def test_vertex_fails_with_nothing_set(monkeypatch):
    from factory_core.providers import model
    for var in ("CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(var, raising=False)
    assert len(model.preflight("vertex")) == 4


def test_databricks_not_yet_implemented():
    from factory_core.providers import model
    problems = model.preflight("databricks")
    assert len(problems) == 1
    assert "databricks" in problems[0]
    assert "not yet" in problems[0]
    assert "docs/provider-abstraction-design.md" in problems[0]


def test_openai_not_yet_implemented():
    from factory_core.providers import model
    problems = model.preflight("openai")
    assert len(problems) == 1
    assert "openai" in problems[0]
    assert "not yet" in problems[0]


def test_unknown_model_provider():
    from factory_core.providers import model
    assert model.preflight("cohere") == ["Unknown FACTORY_MODEL_PROVIDER 'cohere'"]

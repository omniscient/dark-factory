"""Hosting shadow runner, worker and schema module (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md
(Tests 1-19 and 35-37). Hermetic: the only endpoints are mock_server.start() and the in-test
aiohttp stub, both on 127.0.0.1; gh is never invoked.
"""
import ast
import asyncio
import inspect
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))

import hosting_shadow_schema as schema  # noqa: E402
from factory_core.providers import model_endpoint  # noqa: E402

EXAMPLES = BENCH / "fixtures" / "preview_classifier" / "examples"
EXAMPLE_FIXTURES = EXAMPLES / "fixtures.yaml"
SECRET_ENV = "SHADOW_TEST_KEY"
SECRET = "sentinel-secret-5f0c2a"
SERVED = "stub-model"
NEW_MODULES = ("hosting_shadow_schema.py", "shadow_runner.py", "_shadow_worker.py",
               "compare_hosting_variants.py")


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """CI and developer shells often carry these; only tests that set them deliberately may
    see them, so a refusal is exercised, never bypassed."""
    for name in (*schema.REPO_TOKENS, *model_endpoint.CLOUD_CREDENTIALS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(SECRET_ENV, SECRET)


def local_arm(url, arm_id="local-a", secret_env=SECRET_ENV, served=SERVED):
    return {"arm_id": arm_id, "arm_kind": "local", "endpoint_id": "stub-endpoint",
            "base_url": url, "secret_env": secret_env,
            "model_identity": {"served_name": served, "digest_or_revision": "stub-digest",
                               "quantisation": "stub-quant"},
            "runtime_stack": {"name": "stub-runtime", "version": "0.0.0",
                              "chat_template": "stub-template",
                              "context_settings": {"context_tokens": 8192}},
            "hardware": "stub-hardware"}


def write_arms(tmp_path, *arms, name="arms.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                                             *arms]}))
    return path


def write_fixtures(tmp_path, fixtures, name="fixtures.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"schema_version": "1.0", "preview_enabled": True,
                                    "fixtures": fixtures}))
    return path


def fixture(fid, paths=("backend/app/x.py",), title="t", labels=()):
    return {"fixture_id": fid, "provenance": "synthetic", "split": "dev",
            "name_only": list(paths), "stat": "", "title": title, "labels": list(labels),
            "labels_source": "synthetic"}


# 4
@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_local_arm_with_cloud_credential_name_raises(tmp_path, name):
    with pytest.raises(schema.ShadowSchemaError, match="cloud model credential"):
        schema.load_arms(write_arms(tmp_path, local_arm("http://127.0.0.1:9", secret_env=name)))


# 5
def test_unknown_arm_kind_raises(tmp_path):
    for kind in ("cloud", "anything-else"):
        path = tmp_path / f"{kind}.yaml"
        path.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                                                 {"arm_id": "x", "arm_kind": kind}]}))
        with pytest.raises(schema.ShadowSchemaError, match="arm_kind"):
            schema.load_arms(path)
    none = tmp_path / "none.yaml"
    none.write_text(yaml.safe_dump({"arms": [local_arm("http://127.0.0.1:9")]}))
    two = tmp_path / "two.yaml"
    two.write_text(yaml.safe_dump({"arms": [{"arm_id": "r1", "arm_kind": "rules_only"},
                                            {"arm_id": "r2", "arm_kind": "rules_only"}]}))
    for path in (none, two):
        with pytest.raises(schema.ShadowSchemaError, match="exactly one rules_only"):
            schema.load_arms(path)


# 16
def test_run_order_is_deterministic_and_balanced():
    fixtures = [f"f-{i}" for i in range(7)]
    arms = ["a", "b", "c"]
    first = schema.run_order(fixtures, arms, 3, seed=11)
    assert first == schema.run_order(fixtures, arms, 3, seed=11)
    assert first != schema.run_order(fixtures, arms, 3, seed=12)
    assert [u["run_order_position"] for u in first] == list(range(len(first)))
    for r in range(3):
        rep = [u for u in first if u["repetition"] == r]
        assert sorted({u["fixture_id"] for u in rep}) == sorted(fixtures)
        for position in range(len(arms)):
            counts = {arm: 0 for arm in arms}
            for start in range(0, len(rep), len(arms)):
                counts[rep[start + position]["arm_id"]] += 1
            assert max(counts.values()) - min(counts.values()) <= 1


# 18
def test_parse_verdict_is_strict():
    assert schema.parse_verdict('{"needs_preview": false, "category": "docs"}') is False
    assert schema.parse_verdict(' {"needs_preview": true}\n') is True
    for text in ('```json\n{"needs_preview": true}\n```', 'Sure: {"needs_preview": true}',
                 '{"needs_preview": "true"}', '{"category": "docs"}',
                 '{"needs_preview": true, "needs_preview": false}',
                 '{"needs_preview": true, "score": NaN}', '[true]', "", None):
        assert schema.parse_verdict(text) is None, text


# 19
@pytest.mark.parametrize("paths,expected", [
    ([], True),
    (["backend/app/main.py"], True), (["alembic/versions/0001.py"], True),
    (["frontend/src/App.tsx"], True), (["dark-factory/seed/x.sql"], True),
    (["requirements.txt"], True), (["web/package.json"], True), (["package-lock.json"], True),
    (["Dockerfile"], True), (["deploy/Dockerfile.dev"], True),
    (["docker-compose.preview.yml"], True), ([".env.example"], True),
    (["docs/x.txt"], False), (["Docs/x.txt"], False), (["guide.md"], False),
    (["a/b/README.md"], False), (["x.mdx"], False), (["LICENSE"], False),
    ([".archon/memory/a.md"], False), ([".claude/skills/x/SKILL.md"], False),
    (["workflows/archon-dark-factory.yaml"], False), (["workflows/sub/x.yml"], False),
    (["backend/tests/test_a.py"], False), (["dark-factory/tests/t.sh"], False),
    (["web/a.test.ts"], False), (["web/a.test.tsx"], False), (["web/a.spec.ts"], False),
    (["web/a.spec.tsx"], False), (["conftest.py"], False), (["pkg/a_test.py"], False),
    ([".github/workflows/ci.yml"], False), ([".gitignore"], False),
    ([".pre-commit-config.yaml"], False), ([".eslintrc.json"], False),
    ([".prettierrc"], False), (["ruff.toml"], False), ([".ruff.toml"], False),
    ([".flake8"], False), (["mypy.ini"], False), ([".mypy.ini"], False),
    ([".editorconfig"], False),
    (["docs/a.md", "backend/app/x.py"], True), (["docs/a.md", "scripts/x.py"], True),
    (["DOCS/x.txt"], True), (["docs/Dockerfile.dev"], True), (["scripts/x.py"], True),
])
def test_rules_only_verdict_table(paths, expected):
    assert schema.rules_only_verdict(paths) is expected


RUBRIC_STUB = ("K: " + schema.KILL_SWITCH_LINE + "\nC: " + schema.CHANGESET_TARGET
               + "\nT: " + schema.TITLE_TARGET + "\nL: " + schema.LABELS_TARGET + "\n")


# 17
def test_render_prompt_substitutions(tmp_path):
    fx = fixture("f-1", paths=("a.py", "b.md"), title="t " + schema.LABELS_TARGET + " "
                 + schema.CHANGESET_TARGET, labels=("bug", "docs"))
    fx["stat"] = " a.py | 1 +"
    rendered = schema.render_prompt(RUBRIC_STUB, fx)
    assert ("C: === Changed files (main...HEAD) ===\na.py\nb.md\n=== Stat ===\n a.py | 1 +"
            in rendered)
    assert "T: t $fetch-issue.output.labels $preview-changeset.output\n" in rendered
    assert 'L: ["bug", "docs"]\n' in rendered
    assert "K: " + schema.KILL_SWITCH_STATED in rendered
    assert rendered.endswith("\n\n" + schema.OUTPUT_CONTRACT_SUFFIX)
    for broken in (RUBRIC_STUB.replace(schema.TITLE_TARGET, "x"),
                   RUBRIC_STUB + schema.LABELS_TARGET):
        with pytest.raises(schema.ShadowSchemaError, match="exactly once"):
            schema.render_prompt(broken, fx)

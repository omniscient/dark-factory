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
import shadow_runner  # noqa: E402
from factory_core.capability_suite import mock_server  # noqa: E402
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
    with serve(verdict_handler()) as stub:
        launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
               write_fixtures(tmp_path, [fixture("f-1")]))
    (request,) = stub.requests
    assert set(request["body"]) == {"model", "max_tokens", "temperature", "messages"}
    assert request["body"]["model"] == SERVED
    assert request["body"]["messages"][0]["content"].endswith(schema.OUTPUT_CONTRACT_SUFFIX)


class Stub:
    """An aiohttp app on its own loop and thread (the launcher blocks in subprocess.run)."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.url = None

    async def _serve(self, request):
        body = await request.read()
        self.requests.append({"headers": {k.lower(): v for k, v in request.headers.items()},
                              "body": json.loads(body) if body else None})
        return await self.handler(request, self.requests[-1]["body"])

    async def _start(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._serve)
        self._runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=1.0)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{self._runner.addresses[0][1]}"


@contextmanager
def serve(handler):
    stub = Stub(handler)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    stub.url = asyncio.run_coroutine_threadsafe(stub._start(), loop).result(10)
    try:
        yield stub
    finally:
        asyncio.run_coroutine_threadsafe(stub._runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


@contextmanager
def serve_mock():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    runner, url = asyncio.run_coroutine_threadsafe(mock_server.start(), loop).result(10)
    try:
        yield runner, url
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


def message(text, model=SERVED, usage=None):
    return {"id": "msg_stub", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": usage if usage is not None else {"input_tokens": 40, "output_tokens": 9}}


def verdict_handler(needs_preview=True, model=SERVED, usage=None):
    async def handler(request, body):
        text = json.dumps({"needs_preview": needs_preview, "category": "code",
                           "reason": "stub"})
        return web.json_response(message(text, model, usage))
    return handler


def launch(tmp_path, arms_path, fixtures_path=EXAMPLE_FIXTURES, repetitions=1, seed=1,
           out="out"):
    out_dir = tmp_path / out
    shadow_runner.main(["--fixtures", str(fixtures_path), "--arms", str(arms_path),
                        "--out-dir", str(out_dir), "--repetitions", str(repetitions),
                        "--seed", str(seed)])
    rows = [json.loads(line) for line in (out_dir / "rows.jsonl").read_text().splitlines()]
    return out_dir, rows


def local_rows(rows):
    return [r for r in rows if r["arm_kind"] == "local"]


class Captured:
    """A stand-in for subprocess.run that records the call and prints a canned worker reply."""

    def __init__(self, raise_timeout=False):
        self.calls = []
        self.raise_timeout = raise_timeout

    def __call__(self, argv, **kwargs):
        job = Path(argv[-1])
        self.calls.append({"argv": argv, "env": dict(kwargs["env"]), "cwd": Path(kwargs["cwd"]),
                           "job_path": job, "job": json.loads(job.read_text()),
                           "home": Path(kwargs["env"]["HOME"]), "kwargs": kwargs})
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        out = dict.fromkeys(shadow_runner.WORKER_KEYS)
        out.update({"http_status": 200, "served_name_verified": True,
                    "response_text": '{"needs_preview": true}', "duration_ms": 5,
                    "started_at": "2026-11-27T00:00:00+00:00",
                    "completed_at": "2026-11-27T00:00:01+00:00"})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(out), stderr="")


# 1
def test_worker_env_is_exactly_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-copy")
    monkeypatch.setenv("AWS_PROFILE", "do-not-copy")
    captured = Captured()
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")))
    assert captured.calls
    for call in captured.calls:
        assert set(call["env"]) == {"PATH", "HOME", "SHADOW_BASE_URL", SECRET_ENV}
        assert call["env"][SECRET_ENV] == SECRET
        assert call["kwargs"]["stdin"] is subprocess.DEVNULL


# 2
@pytest.mark.parametrize("name", schema.REPO_TOKENS)
def test_launcher_refuses_github_tokens(tmp_path, monkeypatch, name):
    with serve(verdict_handler()) as stub:
        arms = write_arms(tmp_path, local_arm(stub.url))
        monkeypatch.setenv(name, "ghp_x")
        with pytest.raises(shadow_runner.LauncherRefusal, match=name):
            launch(tmp_path, arms)
        assert stub.requests == []
        monkeypatch.setenv(name, "")  # an empty value does not count as set
        launch(tmp_path, arms, out="empty-is-unset")
    options = {o for a in shadow_runner._parser()._actions for o in a.option_strings}
    assert options == {"-h", "--help", "--fixtures", "--arms", "--out-dir", "--repetitions",
                       "--seed"}
    assert list(inspect.signature(shadow_runner.main).parameters) == ["argv"]
    assert list(inspect.signature(shadow_runner.check_environment).parameters) == []


# 3
@pytest.mark.parametrize("name", model_endpoint.CLOUD_CREDENTIALS)
def test_launcher_refuses_cloud_credentials_in_env(tmp_path, monkeypatch, name):
    with serve(verdict_handler()) as stub:
        monkeypatch.setenv(name, "x")
        with pytest.raises(shadow_runner.LauncherRefusal, match=name):
            launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
        assert stub.requests == []


# 6
def test_secret_value_never_leaves_the_header(tmp_path, monkeypatch):
    real_run = subprocess.run
    seen = []

    def spy(argv, **kwargs):
        seen.append({"argv": argv, "job": Path(argv[-1]).read_text()})
        proc = real_run(argv, **kwargs)
        seen[-1].update(stdout=proc.stdout, stderr=proc.stderr)
        return proc

    monkeypatch.setattr(shadow_runner.subprocess, "run", spy)
    with serve(verdict_handler()) as stub:
        out_dir, _ = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
    assert seen
    for call in seen:
        assert SECRET not in " ".join(call["argv"])
        assert SECRET not in call["job"]
        assert SECRET not in call["stdout"] and SECRET not in call["stderr"]
    assert SECRET not in (out_dir / "rows.jsonl").read_text()
    assert SECRET not in (out_dir / "manifest.json").read_text()
    assert {r["headers"]["x-api-key"] for r in stub.requests} == {SECRET}


# 7
def test_base_url_is_written_nowhere(tmp_path):
    with serve(verdict_handler()) as stub:
        out_dir, _ = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
    host_port = stub.url.split("//", 1)[1]
    for name in ("rows.jsonl", "manifest.json"):
        text = (out_dir / name).read_text()
        assert stub.url not in text and host_port not in text
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert [a.get("base_url_scope") for a in manifest["arms"]] == [None, "same-host"]


# 8
def test_paths_inside_a_git_tree_raise(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    outside_arms = write_arms(tmp_path, local_arm("http://127.0.0.1:9"))
    inside_arms = write_arms(repo, local_arm("http://127.0.0.1:9"))
    with pytest.raises(shadow_runner.LauncherRefusal, match="--arms"):
        launch(tmp_path, inside_arms)
    with pytest.raises(shadow_runner.LauncherRefusal, match="--out-dir"):
        launch(tmp_path, outside_arms, out="repo/nested/out")
    for bad in ("run-records/out", "model-registry/out"):
        with pytest.raises(shadow_runner.LauncherRefusal, match="run-records"):
            launch(tmp_path, outside_arms, out=bad)
    (tmp_path / "has-runs" / "sub").mkdir(parents=True)
    (tmp_path / "has-runs" / "sub" / "runs.jsonl").write_text("")
    with pytest.raises(shadow_runner.LauncherRefusal, match="runs.jsonl"):
        launch(tmp_path, outside_arms, out="has-runs")
    (tmp_path / "rerun").mkdir()
    (tmp_path / "rerun" / "rows.jsonl").write_text("")
    with pytest.raises(shadow_runner.LauncherRefusal, match="rows.jsonl"):
        launch(tmp_path, outside_arms, out="rerun")


# 9
@pytest.mark.parametrize("fault", ["status_500", "dropped_connection"])
def test_local_failure_makes_no_request_to_any_other_host(tmp_path, fault):
    async def failing(request, body):
        if fault == "status_500":
            return web.json_response({"type": "error"}, status=500)
        request.transport.close()
        return web.Response()

    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as decoy, serve(failing) as endpoint:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(endpoint.url)), fixtures)
        assert len(endpoint.requests) == 1
        assert decoy.requests == []
    (row,) = local_rows(rows)
    assert row["parse_status"] == "no_response"
    assert row["worker_error"] is None
    if fault == "status_500":
        assert row["http_status"] == 500 and row["failure_class"] is None
    else:
        assert row["failure_class"] == "endpoint_unreachable"


# 10
def test_redirect_is_not_followed(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as target:
        async def redirect(request, body):
            raise web.HTTPFound(target.url + "/v1/messages")

        with serve(redirect) as endpoint:
            _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(endpoint.url)), fixtures)
        assert target.requests == []
    (row,) = local_rows(rows)
    assert row["http_status"] == 302
    assert row["failure_class"] == "protocol_incompatibility"
    assert row["parse_status"] == "no_response"


# 11
def test_wrong_served_model_is_a_failure(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler(model="some-other-model")) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    (row,) = local_rows(rows)
    assert row["failure_class"] == "protocol_incompatibility"
    assert row["served_name_verified"] is False
    assert row["parse_status"] == "no_response"
    with serve_mock() as (_, url):
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(
            url, served=mock_server.SERVED_NAME), name="mock-arms.yaml"), fixtures, out="mock")
    (row,) = local_rows(rows)
    assert row["failure_class"] is None
    assert row["served_name_verified"] is True
    assert row["response_text"] == "ok"
    assert row["parse_status"] == "schema_invalid"  # the mock answers "ok", not a verdict


# 12
def test_malformed_body_and_timeout_rows_are_retained(tmp_path, monkeypatch):
    async def malformed(request, body):
        return web.Response(body=b'{"type": "message", "content": [',
                            content_type="application/json")

    async def stall(request, body):
        await asyncio.sleep(5)
        return web.json_response(message("{}"))

    fixtures = write_fixtures(tmp_path, [fixture("f-1"), fixture("f-2")])
    with serve(malformed) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    assert len(rows) == 4
    assert {r["failure_class"] for r in local_rows(rows)} == {"protocol_incompatibility"}
    monkeypatch.setitem(schema.REQUEST_PARAMS, "total_seconds", 1)
    monkeypatch.setitem(schema.REQUEST_PARAMS, "connect_seconds", 1)
    with serve(stall) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url), name="a2.yaml"),
                         fixtures, out="stalled")
    assert len(rows) == 4
    assert {r["failure_class"] for r in local_rows(rows)} == {"timeout_total"}
    assert {r["parse_status"] for r in local_rows(rows)} == {"no_response"}


# 13
def test_usage_fields_absent_are_null(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    (row,) = local_rows(rows)
    assert row["gen_ai.usage.input_tokens"] == 40 and row["gen_ai.usage.output_tokens"] == 9
    assert row["gen_ai.usage.cache_read_input_tokens"] is None
    assert row["gen_ai.usage.cache_creation_input_tokens"] is None
    usage = {"input_tokens": 3, "output_tokens": 9, "cache_read_input_tokens": 37,
             "cache_creation_input_tokens": 0}
    with serve(verdict_handler(usage=usage)) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url), name="a2.yaml"),
                         fixtures, out="cached")
    (row,) = local_rows(rows)
    assert row["gen_ai.usage.cache_read_input_tokens"] == 37
    assert row["gen_ai.usage.cache_creation_input_tokens"] == 0


BANNED_SEGMENTS = {"side_effect", "board", "cli", "codehost", "tracker", "run_record",
                   "cost_report", "breaker", "deconflict", "handoff", "main_red_fixer",
                   "rescue", "epic_autopilot", "git", "github", "gh"}
BENCH_MODULES = {"hosting_shadow_schema", "shadow_runner", "compare_hosting_variants",
                 "compare_variants", "find_eligible", "_shadow_worker"}


def _imported(path):
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


# 14
def test_runtime_modules_name_no_banned_import():
    for name in NEW_MODULES:
        for imported in _imported(BENCH / name):
            assert not BANNED_SEGMENTS & set(imported.split(".")), (name, imported)
    worker_imports = _imported(BENCH / "_shadow_worker.py")
    assert "subprocess" not in worker_imports
    assert not BENCH_MODULES & {i.split(".")[0] for i in worker_imports}
    assert {i.split(".")[0] for i in worker_imports} <= {
        "asyncio", "json", "os", "sys", "time", "datetime", "pathlib", "aiohttp",
        "factory_core"}


# 15
def test_launcher_never_opens_the_labels_file(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures.yaml"
    fixtures.write_bytes(EXAMPLE_FIXTURES.read_bytes())  # no oracle_labels.yaml beside it
    captured = Captured()
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")), fixtures)
    options = {o for a in shadow_runner._parser()._actions for o in a.option_strings}
    assert not any("label" in o for o in options)
    for call in captured.calls:
        assert set(call["job"]) == {"prompt", "served_name", "endpoint_id", "secret_env",
                                    "request_params"}


# 35
@pytest.mark.parametrize("timeout", [False, True])
def test_worker_invocation_and_temp_cleanup(tmp_path, monkeypatch, timeout):
    captured = Captured(raise_timeout=timeout)
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    _, rows = launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")),
                     write_fixtures(tmp_path, [fixture("f-1")]))
    (call,) = captured.calls
    assert call["argv"] == [sys.executable, "-I", str((BENCH / "_shadow_worker.py").resolve()),
                            str(call["job_path"].resolve())]
    assert call["job_path"].is_absolute()
    assert call["kwargs"]["timeout"] == schema.REQUEST_PARAMS["total_seconds"] + 30
    for path in (call["job_path"], call["home"], call["cwd"], call["job_path"].parent):
        assert not path.exists()
    (row,) = local_rows(rows)
    if timeout:
        assert row["worker_error"] == "timeout" and row["parse_status"] == "no_response"
    else:
        assert row["worker_error"] is None and row["parse_status"] == "parsed"


# 36
def test_response_text_redacts_the_secret(tmp_path):
    async def echo(request, body):
        return web.json_response(message("key=" + request.headers["x-api-key"]))

    with serve(echo) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
                         write_fixtures(tmp_path, [fixture("f-1")]))
    (row,) = local_rows(rows)
    assert row["response_text"] == "key=[redacted]"
    assert SECRET not in json.dumps(rows)


# 37
def test_cold_or_warm_values(tmp_path):
    with serve(verdict_handler()) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
                         write_fixtures(tmp_path, [fixture("f-1"), fixture("f-2")]),
                         repetitions=2)
    ordered = sorted(local_rows(rows), key=lambda r: r["run_order_position"])
    assert [r["cold_or_warm"] for r in ordered] == ["unknown", "not_first", "not_first",
                                                    "not_first"]
    assert {r["cold_or_warm"] for r in rows if r["arm_kind"] == "rules_only"} == {None}

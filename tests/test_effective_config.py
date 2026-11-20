import sys
sys.path.insert(0, "scripts")
import yaml
from factory_core import effective_config as ec

BAKED = {
    "scheduler": {"factory_wip_limit": 1},
    "token_optimization": {
        "enabled": True,
        "budgets": {"refine": 30000, "plan": 30000},
    },
}


def _mk_baked(tmp_path, data=BAKED):
    p = tmp_path / "baked-config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


def _mk_clone(tmp_path):
    c = tmp_path / "clone"
    c.mkdir()
    return c


def _write_clone_cfg(clone, data):
    d = clone / ".claude" / "skills" / "refinement"
    d.mkdir(parents=True)
    p = d / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _write_adapter(clone, text):
    d = clone / ".factory"
    d.mkdir()
    (d / "adapter.yaml").write_text(text, encoding="utf-8")


# ── resolve ────────────────────────────────────────────────────────────────────

def test_no_adapter_no_clone_config_returns_baked(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    merged, sources = ec.resolve(str(clone), baked)
    assert merged == BAKED
    assert sources["token_optimization"] == "baked"


def test_clone_config_wins_over_baked_and_is_never_dirtied(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    exclude = clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("# pre-existing\n", encoding="utf-8")
    cfg_path = _write_clone_cfg(
        clone, {"token_optimization": {"budgets": {"refine": 25000}},
                "dispatch_ceiling": {"keywords": "clone-kw"}})  # a non-token_optimization knob
    original_bytes = cfg_path.read_bytes()

    merged, sources = ec.resolve(str(clone), baked)
    assert merged["token_optimization"]["budgets"]["refine"] == 25000
    # deep-merge: baked siblings survive
    assert merged["token_optimization"]["budgets"]["plan"] == 30000
    assert merged["token_optimization"]["enabled"] is True
    assert merged["dispatch_ceiling"]["keywords"] == "clone-kw"
    assert sources["token_optimization"] == "clone"

    summary = ec.materialize(str(clone), baked, str(tmp_path / "artifacts"))
    assert "left in place" in summary
    assert "token_optimization source: clone" in summary
    # byte-parity: the committed clone file is untouched, and nothing new is git-excluded
    assert cfg_path.read_bytes() == original_bytes
    assert exclude.read_text(encoding="utf-8") == "# pre-existing\n"


def test_adapter_wins_over_clone_and_baked_deep_merge(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    _write_clone_cfg(clone, {"token_optimization": {"budgets": {"refine": 25000}}})
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 12345\n")
    merged, sources = ec.resolve(str(clone), baked)
    assert merged["token_optimization"]["budgets"]["refine"] == 12345
    # sibling keys from clone/baked survive the deep-merge
    assert merged["token_optimization"]["budgets"]["plan"] == 30000
    assert merged["token_optimization"]["enabled"] is True
    assert sources["token_optimization"] == "adapter"


def test_invalid_adapter_fails_open_without_overrides(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    _write_adapter(clone, "token_optimization: [not, a, map]\n")
    merged, sources = ec.resolve(str(clone), baked)  # must not raise
    assert merged == BAKED
    assert sources["token_optimization"] == "baked"
    assert "skipping adapter overrides" in capsys.readouterr().err


def test_adapter_key_absent_from_adapter_file_is_not_adapter_sourced(tmp_path):
    """An adapter file that does NOT set token_optimization must not claim the block."""
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    _write_adapter(clone, "safety:\n  sensitive_keywords: 'payments'\n")
    merged, sources = ec.resolve(str(clone), baked)
    assert merged["token_optimization"] == BAKED["token_optimization"]
    assert sources["token_optimization"] == "baked"


# ── materialize ────────────────────────────────────────────────────────────────

def test_materialize_writes_merged_config_and_git_exclude_once(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()  # fake .git dir — enough for info/exclude handling
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 12345\n")

    summary = ec.materialize(str(clone), baked)
    assert "materialized from baked defaults" in summary
    assert "token_optimization source: adapter" in summary

    cfg_path = clone / ".claude" / "skills" / "refinement" / "config.yaml"
    assert cfg_path.is_file()
    written = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert written["token_optimization"]["budgets"]["refine"] == 12345
    assert written["token_optimization"]["budgets"]["plan"] == 30000  # baked sibling
    assert written["scheduler"] == BAKED["scheduler"]

    # exclude line present exactly once, even after a second materialize()
    ec.materialize(str(clone), baked)
    excl = (clone / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert excl.splitlines().count(".claude/skills/refinement/config.yaml") == 1


def test_materialize_no_adapter_no_clone_config_uses_baked(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    summary = ec.materialize(str(clone), baked)
    assert "materialized from baked defaults" in summary
    assert "token_optimization source: baked" in summary
    cfg_path = clone / ".claude" / "skills" / "refinement" / "config.yaml"
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8")) == BAKED


def test_drift_warning_when_adapter_differs_from_clone_config(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    _write_clone_cfg(clone, {"token_optimization": {"budgets": {"refine": 25000}}})
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 99999\n")
    summary = ec.materialize(str(clone), baked)
    assert "left in place" in summary
    err = capsys.readouterr().err
    assert "adapter/token_optimization drifts from clone config.yaml" in err
    assert "re-sync .factory/adapter.yaml" in err


def test_no_drift_warning_when_adapter_mirrors_clone_config(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    _write_clone_cfg(clone, {"token_optimization": {"budgets": {"refine": 25000}}})
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 25000\n")
    ec.materialize(str(clone), baked)
    assert "drifts" not in capsys.readouterr().err


def test_missing_baked_file_warns_and_returns_empty(tmp_path, capsys):
    clone = _mk_clone(tmp_path)
    merged, sources = ec.resolve(str(clone), str(tmp_path / "nope.yaml"))
    assert merged == {}
    assert sources["token_optimization"] == "baked"
    assert "baked config missing" in capsys.readouterr().err


# ── CLI ────────────────────────────────────────────────────────────────────────

def test_cli_print_dumps_merged_yaml(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    old_argv = sys.argv[:]
    try:
        sys.argv = ["effective_config", "--clone-dir", str(clone),
                    "--baked", baked, "--print"]
        try:
            ec.main()
        except SystemExit as exc:
            assert exc.code == 0
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert yaml.safe_load(out) == BAKED


def test_cli_materialize_exits_zero_even_on_bad_clone_dir(tmp_path, capsys):
    """Fail-open: a resolution failure must never kill the run (exit 0)."""
    baked = _mk_baked(tmp_path)
    old_argv = sys.argv[:]
    try:
        sys.argv = ["effective_config", "--clone-dir",
                    str(tmp_path / "does-not-exist"), "--baked", baked, "--materialize"]
        try:
            ec.main()
        except SystemExit as exc:
            assert exc.code == 0
    finally:
        sys.argv = old_argv


# ── #180: run-config.json / run-config.env ─────────────────────────────────────
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
REPO_CONFIG = str(REPO / "config" / "config.yaml")
GOLDEN = json.loads((REPO / "tests" / "fixtures" / "run_config_env_golden.json")
                    .read_text(encoding="utf-8"))
MH_ADAPTER = "token_optimization:\n  enforce:\n    implement: true\n"


def _parse_env(text):
    """Mirror of scripts/run_config_env.sh's parse: VAR=value, bare VAR = None (null)."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
        else:
            out[line] = None
    return out


def _run_cli(argv):
    old = sys.argv[:]
    sys.argv = ["effective_config", *argv]
    try:
        ec.main()
    except SystemExit as exc:
        return exc.code
    finally:
        sys.argv = old
    return None


def _env_of(out_dir):
    return _parse_env((out_dir / "run-config.env").read_text(encoding="utf-8"))


def _json_of(out_dir):
    return json.loads((out_dir / "run-config.json").read_text(encoding="utf-8"))


def test_whitelist_covers_exactly_the_golden_variables():
    for reader in ("scheduler", "entrypoint"):
        rows = [v for v, _, readers in ec.ENV_WHITELIST if reader in readers]
        assert rows == list(GOLDEN[reader]), reader


def test_scheduler_candidates_match_former_resolve_config_yaml_order():
    assert ec.SCHEDULER_CANDIDATES == (
        "/workspace/project/config/config.yaml",
        "/opt/dark-factory/config/config.yaml",
        "/opt/refinement-skills/config.yaml",
    )


def test_render_env_scalars_nulls_and_projection():
    cfg = {"scheduler": {"poll_interval": 60},
           "dispatch_ceiling": {"enabled": True, "keywords": "a|b=c"},
           "epic_autopilot": {"confidence_floor": 0.7, "enabled": False},
           "conflict_resolution": {"ai_tier": True}}
    text = ec.render_env(cfg, "scheduler", "/x/config.yaml")
    assert "# source: /x/config.yaml" in text.splitlines()
    env = _parse_env(text)
    assert len(env) == 33
    assert env["POLL_INTERVAL"] == "60"
    assert env["DISPATCH_CEILING_ENABLED"] == "true"
    assert env["EPIC_AUTOPILOT_ENABLED"] == "false"
    assert env["ABOVE_CEILING_KEYWORDS"] == "a|b=c"
    assert env["EPIC_AUTOPILOT_CONFIDENCE_FLOOR"] == "0.7"
    assert env["MAX_RETRIES"] is None                      # absent key -> bare name
    assert "CONFLICT_RESOLUTION_AI_TIER" not in env        # entrypoint-only row
    ep = _parse_env(ec.render_env(cfg, "entrypoint", "x"))
    assert list(ep) == ["FACTORY_WIP_LIMIT", "CONFLICT_RESOLUTION_AI_TIER",
                        "SESSION_WINDOW_BACKOFF_ENABLED", "SESSION_WINDOW_BUFFER_MINUTES",
                        "SESSION_WINDOW_FALLBACK_MINUTES"]
    assert ep["CONFLICT_RESOLUTION_AI_TIER"] == "true"


def test_render_env_non_scalar_is_null_with_warning(capsys):
    env = _parse_env(ec.render_env({"scheduler": {"poll_interval": [1, 2]}}, "scheduler", "x"))
    assert env["POLL_INTERVAL"] is None
    assert "POLL_INTERVAL is not a single-line scalar" in capsys.readouterr().err


def test_scheduler_mode_first_match_never_reads_clone(tmp_path, monkeypatch):
    """Requirement 2 / safety invariant 2: whole-file first match; no baked fill-in, no clone."""
    first = tmp_path / "project-config.yaml"
    first.write_text(yaml.safe_dump({"dispatch_ceiling": {"keywords": "first-only"},
                                     "epic_autopilot": {"enabled": True}}), encoding="utf-8")
    baked = tmp_path / "baked-config.yaml"
    baked.write_text(yaml.safe_dump({
        "scheduler": {"poll_interval": 60},
        "dispatch_ceiling": {"enabled": True, "label": "above-ceiling", "keywords": "baked"},
        "epic_autopilot": {"enabled": False}}), encoding="utf-8")
    # A clone-shaped cwd: the old "." default would have probed ./.claude and ./.factory.
    cwd = _mk_clone(tmp_path)
    _write_clone_cfg(cwd, {"dispatch_ceiling": {"keywords": "clone"}})
    _write_adapter(cwd, "token_optimization:\n  budgets:\n    refine: 1\n")
    monkeypatch.chdir(cwd)
    out = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(out),
                     "--candidate", str(first), "--candidate", str(baked)]) == 0
    env = _env_of(out)
    assert env["ABOVE_CEILING_KEYWORDS"] == "first-only"
    assert env["EPIC_AUTOPILOT_ENABLED"] == "true"
    assert env["DISPATCH_CEILING_ENABLED"] is None   # absent from the first file: empty,
    assert env["ABOVE_CEILING_LABEL"] is None        # never the baked value
    assert env["POLL_INTERVAL"] is None
    assert _json_of(out) == yaml.safe_load(first.read_text(encoding="utf-8"))
    assert f"# source: {first}" in (out / "run-config.env").read_text(encoding="utf-8")


def test_scheduler_mode_no_candidate_exits_1_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(out),
                     "--candidate", str(tmp_path / "missing.yaml")]) == 1
    assert not (out / "run-config.env").exists()
    assert not (out / "run-config.json").exists()
    assert "config.yaml not found" in capsys.readouterr().err


def test_scheduler_mode_materialize_requires_out_dir(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("scheduler: {poll_interval: 1}\n", encoding="utf-8")
    assert _run_cli(["--materialize", "--candidate", str(cfg)]) == 2
    assert "scheduler mode --materialize needs --out-dir" in capsys.readouterr().err


def test_run_config_env_matches_golden_fixture(tmp_path):
    """Safety invariant 1: the 33 scheduler + 5 entrypoint values the old yq readers produced
    from config/config.yaml (tests/fixtures/run_config_env_golden.json). A deliberate change
    to one of these config values must update the fixture in the same commit."""
    sched = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(sched), "--candidate", REPO_CONFIG]) == 0
    env = _env_of(sched)
    assert env == GOLDEN["scheduler"]
    assert list(env) == list(GOLDEN["scheduler"])  # apply/log order preserved
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    run_out = tmp_path / "artifacts"
    ec.materialize(str(clone), REPO_CONFIG, str(run_out))
    assert _env_of(run_out) == GOLDEN["entrypoint"]


@pytest.mark.parametrize("adapter_text", [None, MH_ADAPTER], ids=["self", "markethawk"])
def test_run_config_json_equals_clone_side_materialization(tmp_path, adapter_text):
    """Requirement 8 / safety invariant 1: run-config.json == the clone-side YAML of the
    same run, so budget_gate.sh (run-config.json) and budget_enforce.py (clone file) agree."""
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    if adapter_text:
        _write_adapter(clone, adapter_text)
    out = tmp_path / "artifacts"
    ec.materialize(str(clone), REPO_CONFIG, str(out))
    clone_side = yaml.safe_load(
        (clone / ".claude" / "skills" / "refinement" / "config.yaml").read_text(encoding="utf-8"))
    run_cfg = _json_of(out)
    assert run_cfg == clone_side
    expected_impl = True if adapter_text else yaml.safe_load(
        open(REPO_CONFIG, encoding="utf-8"))["token_optimization"]["enforce"]["implement"]
    assert run_cfg["token_optimization"]["enforce"]["implement"] is expected_impl


def test_run_config_json_is_committed_clone_copy_alone(tmp_path):
    """Requirement 2: a committed copy is used alone — no baked fill-in, no adapter overlay."""
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    copy = {"dispatch_ceiling": {"keywords": "clone-kw"}}
    _write_clone_cfg(clone, copy)
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 12345\n")
    out = tmp_path / "artifacts"
    summary = ec.materialize(str(clone), baked, str(out))
    assert "left in place" in summary
    assert _json_of(out) == copy
    assert _env_of(out)["FACTORY_WIP_LIMIT"] is None   # baked has it; the copy does not


def test_materialize_artifact_write_failure_is_fail_open(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    summary = ec.materialize(str(clone), baked, str(blocker))
    assert "materialized from baked defaults" in summary
    assert (clone / ".claude" / "skills" / "refinement" / "config.yaml").is_file()
    assert "cannot write run-config artifacts" in capsys.readouterr().err


def test_cli_run_mode_writes_artifacts_with_out_dir(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    out = tmp_path / "artifacts"
    assert _run_cli(["--clone-dir", str(clone), "--baked", baked,
                     "--materialize", "--out-dir", str(out)]) == 0
    assert _json_of(out) == BAKED
    assert _env_of(out)["FACTORY_WIP_LIMIT"] == "1"


def test_load_run_config_prefers_exported_json_then_resolver(tmp_path, monkeypatch):
    clone = _mk_clone(tmp_path)
    _write_clone_cfg(clone, {"dispatch_ceiling": {"keywords": "from-clone"}})
    monkeypatch.delenv(ec.RUN_CONFIG_JSON_ENV, raising=False)
    assert ec.load_run_config(str(clone)) == {"dispatch_ceiling": {"keywords": "from-clone"}}
    rc = tmp_path / "run-config.json"
    rc.write_text(json.dumps({"dispatch_ceiling": {"keywords": "from-json"}}), encoding="utf-8")
    monkeypatch.setenv(ec.RUN_CONFIG_JSON_ENV, str(rc))
    assert ec.load_run_config(str(clone))["dispatch_ceiling"]["keywords"] == "from-json"
    rc.write_text("{not json", encoding="utf-8")
    assert ec.load_run_config(str(clone))["dispatch_ceiling"]["keywords"] == "from-clone"


def test_first_match_list_first_nonempty_file_wins_without_merge(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("epic_autopilot:\n  hard_exclude_paths: []\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text('epic_autopilot:\n  hard_exclude_paths: ["b/", "\\"q/\\""]\n', encoding="utf-8")
    c = tmp_path / "c.yaml"
    c.write_text('epic_autopilot:\n  hard_exclude_paths: ["c/"]\n', encoding="utf-8")
    paths = [str(tmp_path / "missing.yaml"), str(a), str(b), str(c)]
    assert ec.first_match_list(paths, "epic_autopilot.hard_exclude_paths") == ["b/", "q/"]
    assert ec.first_match_list([str(a)], "epic_autopilot.hard_exclude_paths") == []


@pytest.mark.xfail(strict=True, reason="#180: green after Task 6")
def test_scheduler_and_entrypoint_have_no_per_knob_yq_reads():
    """Acceptance criteria: read_config / the entrypoint reader no longer call yq per knob."""
    for rel in ("scheduler.sh", "entrypoint.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for gone in ("_set_cfg", "_epcfg", "_entrypoint_cfg_apply", "resolve_config_yaml",
                     "CONFIG_YAML_PATHS"):
            assert gone not in text, f"{rel} still mentions {gone}"
        assert not re.search(r"\byq\b", text), f"{rel} still runs yq"


@pytest.mark.xfail(strict=True, reason="#180: green after Task 6")
def test_entrypoint_wires_run_config_after_materialize():
    ep = (REPO / "entrypoint.sh").read_text(encoding="utf-8")
    order = ['--materialize --out-dir "$ARTIFACTS_DIR"',
             'export FACTORY_RUN_CONFIG_JSON="$ARTIFACTS_DIR/run-config.json"',
             'apply_run_config_env "$ARTIFACTS_DIR/run-config.env" entrypoint',
             "\n_compute_side_effect_level\n"]
    idx = [ep.find(s) for s in order]
    assert -1 not in idx, dict(zip(order, idx))
    assert idx == sorted(idx)


def test_no_reader_takes_side_effect_or_blast_radius_from_run_config():
    """Safety invariant 2: the trust-boundary readers never consume the new artifacts."""
    needles = ("run-config", "run_config", "RUN_CONFIG")
    for rel in ("scripts/gate_blast_radius.py", "scripts/factory_core/side_effect.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not any(n in text for n in needles), rel
    ep = (REPO / "entrypoint.sh").read_text(encoding="utf-8")
    m = re.search(r"^_compute_side_effect_level\(\) \{\n(.*?)^\}", ep, re.S | re.M)
    assert m, "_compute_side_effect_level() not found in entrypoint.sh"
    assert not any(n in m.group(1) for n in needles)

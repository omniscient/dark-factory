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
    cfg_path = _write_clone_cfg(
        clone, {"token_optimization": {"budgets": {"refine": 25000}}})
    original_bytes = cfg_path.read_bytes()

    merged, sources = ec.resolve(str(clone), baked)
    assert merged["token_optimization"]["budgets"]["refine"] == 25000
    # deep-merge: baked siblings survive
    assert merged["token_optimization"]["budgets"]["plan"] == 30000
    assert merged["token_optimization"]["enabled"] is True
    assert sources["token_optimization"] == "clone"

    summary = ec.materialize(str(clone), baked)
    assert "left in place" in summary
    assert "token_optimization source: clone" in summary
    # byte-parity: the committed clone file is untouched
    assert cfg_path.read_bytes() == original_bytes


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

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import side_effect


def test_levels_table_has_six_entries():
    assert set(side_effect.LEVELS) == {1, 2, 3, 4, 5, 6}
    assert side_effect.LEVELS[1] == "read-only research"
    assert side_effect.LEVELS[6] == "external production side effect"


@pytest.mark.parametrize("value,expected", [
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
    (6, 1), (7, 1), (0, 1), (-1, 1), (None, 1), ("2", 1), (True, 1), (False, 1),
])
def test_effective_level_d4_default(value, expected):
    assert side_effect.effective_level(value) == expected


def test_profile_for_level_1_removes_write_tools():
    p = side_effect.profile_for(1)
    assert p.denied_tools == ["Write", "Edit", "MultiEdit", "NotebookEdit"]
    assert p.gh_mode == "allow"
    assert "api:GET" in p.gh_allowed
    assert "view" in p.gh_allowed and "list" in p.gh_allowed
    # R1 (amended): the level-5 never-list is denied at EVERY level and checked before the
    # allow-list -- `gh secret list` / `gh auth status` must not ride in on "list"/"status".
    for verb in ("secret", "auth", "ssh-key", "gpg-key", "api:DELETE"):
        assert verb in p.gh_denied
    assert "remote add" in p.git_denied and "remote set-url" in p.git_denied
    assert p.push_scope == "denied"
    # R5 fail-closed: level 1 must be allow-list, not deny-list, for git too — a git
    # verb R1's table never names (checkout/reset/clean/stash/config/apply/...) must
    # be denied by default, not silently pass. Caught in architect review cycle 2.
    assert p.git_mode == "allow"
    assert "log" in p.git_allowed and "status" in p.git_allowed
    assert "checkout" not in p.git_allowed and "reset" not in p.git_allowed


def test_profile_for_level_2_writes_locally_only():
    p = side_effect.profile_for(2)
    assert p.denied_tools == []
    assert "push" in p.git_denied and "tag" in p.git_denied
    assert "remote add" not in p.git_denied  # level 1 only
    # Levels 2-3 stay deny-list for git (ordinary local writes -- add/commit/checkout -b
    # -- must keep working; only the enumerated remote-facing verbs are denied).
    assert p.git_mode == "deny"
    assert p.gh_mode == "allow"
    assert "issue create" not in p.gh_allowed  # level 3 only


def test_profile_for_level_3_allows_issue_ops_only():
    p = side_effect.profile_for(3)
    assert p.gh_mode == "allow"
    for verb in ("issue create", "issue comment", "issue edit"):
        assert verb in p.gh_allowed
    assert "pr create" not in p.gh_allowed


def test_profile_for_level_4_own_branch_push_only():
    p = side_effect.profile_for(4)
    assert p.denied_tools == []
    assert p.push_scope == "own_branch_only"
    assert p.gh_mode == "deny"
    for verb in ("pr create", "pr merge", "pr ready", "pr review", "pr close",
                 "release", "repo", "secret", "auth"):
        assert verb in p.gh_denied
    assert "api:non-GET" in p.gh_denied
    for verb in ("ssh-key", "gpg-key", "api:DELETE"):  # never-list at every level
        assert verb in p.gh_denied


def test_profile_for_level_5_never_list_only():
    p = side_effect.profile_for(5)
    assert p.denied_tools == []
    assert p.push_scope == "unrestricted"
    assert p.git_denied == ["push --delete", "push :refspec-delete"]
    assert p.gh_mode == "deny"
    for verb in ("repo delete", "repo archive", "repo rename", "secret", "auth",
                 "ssh-key", "gpg-key"):
        assert verb in p.gh_denied
    assert "api:DELETE" in p.gh_denied
    assert "pr create" not in p.gh_denied  # level 5 explicitly allows PR creation


def test_profile_for_level_6_raises():
    with pytest.raises(ValueError):
        side_effect.profile_for(6)


def test_profile_for_out_of_range_raises():
    with pytest.raises(ValueError):
        side_effect.profile_for(0)


def test_factory_owned_min_level_pins_to_verifier():
    from factory_core import verifier
    assert side_effect.FACTORY_OWNED_MIN_LEVEL == verifier._FACTORY_OWNED_MIN_LEVEL


def test_target_definable_max_level():
    assert side_effect.TARGET_DEFINABLE_MAX_LEVEL == 3
    assert side_effect.TARGET_DEFINABLE_MAX_LEVEL == side_effect.FACTORY_OWNED_MIN_LEVEL - 1


@pytest.mark.parametrize("intent,phases", [
    ("refine", ["refine"]),
    ("plan", ["plan"]),
    # entrypoint.sh's own $INTENT vocabulary (entrypoint.sh:87: fix|continue|close|refine|
    # plan|deconflict|recheck, plus the fix-main special case) -- NOT the DAG's
    # parse-intent vocabulary (new|continue|close|refine|plan|resolve). entrypoint.sh:701's
    # own comment draws the correspondence explicitly ("fix (new)... deconflict
    # (resolve)"), but intent_phases() is fed entrypoint.sh's raw $INTENT (it runs before
    # archon/parse-intent ever executes), so it must be keyed on THAT vocabulary. Using
    # "new"/"resolve" here (the spec's own R3 prose, which reused the DAG's naming without
    # the distinction) would make the actual `fix`/`deconflict` intents fall through to
    # intent_phases' [] default -> level 1 -> the shim denying every git/gh call the
    # implement/deconflict phases need. Caught in architect review, cycle 3.
    ("fix", ["implement", "validate", "conformance", "code-review", "revise-advisory"]),
    ("continue", ["implement", "validate", "conformance", "code-review", "revise-advisory"]),
    ("deconflict", ["deconflict"]),
    ("fix-main", ["fix-main"]),
    ("close", []),
    ("recheck", []),  # entrypoint.sh:713: exits after the smoke-gate check, never reaches archon
    ("unknown-intent", []),
])
def test_intent_phases(intent, phases):
    assert side_effect.intent_phases(intent) == phases


def test_phase_level_reads_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    plan: 4\n")
    assert side_effect.phase_level("plan", str(cfg), env={}) == 4


def test_phase_level_env_override(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    plan: 5\n")
    assert side_effect.phase_level(
        "plan", str(cfg), env={"SIDE_EFFECT_LEVEL_PLAN": "2"}) == 2


def test_phase_level_missing_config_defaults_to_1(tmp_path, capsys):
    assert side_effect.phase_level("plan", str(tmp_path / "missing.yaml"), env={}) == 1
    assert "defaulting to 1" in capsys.readouterr().err


def test_phase_level_hyphenated_phase_name(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    code_review: 4\n")
    assert side_effect.phase_level("code-review", str(cfg), env={}) == 4


def test_render_cli_prints_json(tmp_path, capsys):
    side_effect.main(["render", "--level", "4"])
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    assert data["level"] == 4
    assert data["push_scope"] == "own_branch_only"
    assert data["profile_version"] == "v1"


def test_render_cli_level_6_raises():
    # F14: render must never silently downgrade an out-of-range level to level 1's profile;
    # the shim treats a render failure as "cannot resolve profile" and denies (fail closed).
    with pytest.raises(ValueError):
        side_effect.main(["render", "--level", "6"])


def test_effective_container_level_cli_is_max_over_phases(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "side_effect:\n  phase_levels:\n"
        "    implement: 4\n    validate: 5\n    conformance: 3\n    code_review: 4\n"
    )
    side_effect.main(["effective-container-level", "--intent", "fix", "--config", str(cfg)])
    assert capsys.readouterr().out.strip() == "5"


def test_effective_container_level_empty_phase_set_defaults_to_1(tmp_path, capsys):
    side_effect.main(["effective-container-level", "--intent", "close"])
    assert capsys.readouterr().out.strip() == "1"


import yaml as _yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_has_side_effect_phase_levels():
    cfg = _yaml.safe_load((_REPO_ROOT / "config/config.yaml").read_text())
    levels = cfg["side_effect"]["phase_levels"]
    for phase in ("refine", "plan", "implement", "validate", "conformance",
                  "code_review", "revise_advisory", "deconflict", "fix_main"):
        assert levels[phase] == 5, f"{phase} must start at level 5 (no v1 regression)"

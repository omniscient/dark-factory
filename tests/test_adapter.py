import copy
import re
import sys
sys.path.insert(0, "scripts")
import pytest
import yaml
from factory_core import adapter, adapter_defaults


def test_no_adapter_file_returns_defaults(tmp_path):
    merged = adapter.load(str(tmp_path))
    assert merged == adapter_defaults.DEFAULTS


def test_adapter_overrides_deep_merge(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "schema_version: 1\nsafety:\n  sensitive_keywords: 'payments|pii'\n")
    merged = adapter.load(str(tmp_path))
    assert merged["safety"]["sensitive_keywords"] == "payments|pii"
    # untouched siblings survive the merge
    assert merged["safety"]["dispatch_ceiling_keywords"] == \
        adapter_defaults.DEFAULTS["safety"]["dispatch_ceiling_keywords"]


def test_invalid_yaml_raises_adapter_error(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("{broken: [")
    with pytest.raises(adapter.AdapterError):
        adapter.load(str(tmp_path))


def test_wrong_type_raises_adapter_error(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\nsafety: 'not-a-map'\n")
    with pytest.raises(adapter.AdapterError):
        adapter.load(str(tmp_path))


def test_unknown_keys_warn_not_fail(tmp_path, capsys):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\nfuture_feature: {a: 1}\n")
    merged = adapter.load(str(tmp_path))
    assert "future_feature" in merged            # carried through
    assert "unknown adapter key" in capsys.readouterr().err


def test_dotted_get(tmp_path):
    assert adapter.get(str(tmp_path), "deconflict.migrations_dir") == "alembic/versions/"


def test_loops_default_is_empty_list(tmp_path):
    """Absent adapter file merges to loops: [] (additive parity default)."""
    merged = adapter.load(str(tmp_path))
    assert merged["loops"] == []


def test_schema_version_1_without_loops_merges_to_empty_list(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\n")
    merged = adapter.load(str(tmp_path))
    assert merged["loops"] == []


def test_repo_board_labels_now_warn_not_error(tmp_path, capsys):
    """repo/board/labels are no longer known keys — they fall through to the
    generic unknown-top-level-key warn-and-carry path, not AdapterError."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "repo: 'org/name'\nboard: 'Project X'\nlabels: ['a', 'b']\n")
    merged = adapter.load(str(tmp_path))
    assert merged["repo"] == "org/name"
    assert merged["board"] == "Project X"
    assert merged["labels"] == ["a", "b"]
    err = capsys.readouterr().err
    assert "unknown adapter key 'repo'" in err
    assert "unknown adapter key 'board'" in err
    assert "unknown adapter key 'labels'" in err


_VALID_LOOP_ENTRY = """
loops:
  - name: nightly-scan-triage
    purpose: Triage overnight scanner false positives
    side_effect_level: 2
    discovery:
      trigger: 'cron:0 6 * * *'
      inputs: ["scanner_output.json"]
    handoff:
      outputs: ["triage_report.md"]
      manifest: handoffs/triage_handoff.py
    verification:
      verifier: verifiers/triage_verifier.py
      stop_condition: stop_conditions/triage_stop.py
    persistence:
      artifacts: [".factory/state/triage.json"]
    scheduling:
      failure_behavior: escalate_to_human
"""


def test_valid_loop_entry_parses(tmp_path, capsys):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
    merged = adapter.load(str(tmp_path))
    assert len(merged["loops"]) == 1
    entry = merged["loops"][0]
    assert entry["name"] == "nightly-scan-triage"
    assert entry["side_effect_level"] == 2
    assert entry["discovery"] == {
        "trigger": "cron:0 6 * * *", "inputs": ["scanner_output.json"]}
    assert entry["handoff"] == {
        "outputs": ["triage_report.md"], "manifest": "handoffs/triage_handoff.py"}
    assert entry["verification"] == {
        "verifier": "verifiers/triage_verifier.py",
        "stop_condition": "stop_conditions/triage_stop.py"}
    assert entry["persistence"] == {"artifacts": [".factory/state/triage.json"]}
    assert entry["scheduling"] == {"failure_behavior": "escalate_to_human"}
    assert "unknown adapter key 'loops'" not in capsys.readouterr().err


def test_loops_get_dotted_path_returns_verbatim(tmp_path):
    """Acceptance criterion: the R1 shape is returned verbatim by adapter.get(),
    not just adapter.load()."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
    loops = adapter.get(str(tmp_path), "loops")
    assert loops[0]["discovery"] == {
        "trigger": "cron:0 6 * * *", "inputs": ["scanner_output.json"]}


def test_loops_independent_of_schema_version(tmp_path):
    """A schema_version: 1 file with a valid loops: entry still parses —
    schema_version never gates loops:."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\n" + _VALID_LOOP_ENTRY)
    merged = adapter.load(str(tmp_path))
    assert len(merged["loops"]) == 1


def test_loop_entry_not_a_mapping_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("loops:\n  - 'not-a-mapping'\n")
    with pytest.raises(adapter.AdapterError, match=r"loops\[0\] must be a mapping"):
        adapter.load(str(tmp_path))


def test_loops_not_a_list_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("loops:\n  name: not-a-list\n")
    with pytest.raises(adapter.AdapterError, match=r"loops.*must be a list"):
        adapter.load(str(tmp_path))


def test_loop_entry_missing_required_top_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    entry = _VALID_LOOP_ENTRY.replace(
        "    purpose: Triage overnight scanner false positives\n", "")
    (d / "adapter.yaml").write_text(entry)
    with pytest.raises(adapter.AdapterError, match=r"missing required field 'purpose'"):
        adapter.load(str(tmp_path))


def test_loop_entry_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    entry = _VALID_LOOP_ENTRY + "    extra_typo_field: oops\n"
    (d / "adapter.yaml").write_text(entry)
    with pytest.raises(adapter.AdapterError, match=r"unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


_MOVE_BLOCKS = ["discovery", "handoff", "verification", "persistence", "scheduling"]
_MOVE_BLOCK_REQUIRED_FIELDS = [
    ("discovery", "trigger"), ("discovery", "inputs"),
    ("handoff", "manifest"), ("handoff", "outputs"),
    ("verification", "verifier"), ("verification", "stop_condition"),
    ("persistence", "artifacts"),
    ("scheduling", "failure_behavior"),
]
_MOVE_BLOCK_STR_FIELDS = [
    ("discovery", "trigger"), ("handoff", "manifest"),
    ("verification", "verifier"), ("verification", "stop_condition"),
    ("scheduling", "failure_behavior"),
]
_MOVE_BLOCK_LIST_FIELDS = [
    ("discovery", "inputs"), ("handoff", "outputs"), ("persistence", "artifacts"),
]


@pytest.mark.parametrize("block", _MOVE_BLOCKS)
def test_loop_entry_missing_required_block_raises(tmp_path, block):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    del parsed["loops"][0][block]
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=re.escape(f"missing required block '{block}'")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block", _MOVE_BLOCKS)
def test_loop_move_block_not_a_mapping_raises(tmp_path, block):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][block] = "not-a-mapping"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=re.escape(f"block '{block}' must be a mapping")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block", _MOVE_BLOCKS)
def test_loop_move_block_null_value_raises(tmp_path, block):
    """A block key present but with no YAML value (`discovery:` alone) parses
    to None, which must be rejected as 'not a mapping', not silently accepted
    as 'absent' — None is a real parsed value here, distinct from the key
    being missing entirely."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][block] = None
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=re.escape(f"block '{block}' must be a mapping")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block", _MOVE_BLOCKS)
def test_loop_move_block_unknown_field_raises(tmp_path, block):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][block]["extra_typo_field"] = "oops"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block '{block}': unknown field 'extra_typo_field'")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block,field", _MOVE_BLOCK_REQUIRED_FIELDS)
def test_loop_move_block_missing_required_field_raises(tmp_path, block, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    del parsed["loops"][0][block][field]
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block '{block}': missing required field '{field}'")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block,field", _MOVE_BLOCK_STR_FIELDS)
def test_loop_move_block_string_field_wrong_type_raises(tmp_path, block, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][block][field] = 42
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block '{block}': field '{field}' must be a non-empty string")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("block,field", _MOVE_BLOCK_LIST_FIELDS)
def test_loop_move_block_list_field_wrong_type_raises(tmp_path, block, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][block][field] = "not-a-list"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block '{block}': field '{field}' must be a list of strings")):
        adapter.load(str(tmp_path))


def test_flat_a1_shaped_entry_fails(tmp_path):
    """An A1-shaped flat entry (pre-A1.5) fails with the first missing move
    block — there is no dual-form fallback (spec R1)."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "loops:\n"
        "  - name: nightly-scan-triage\n"
        "    purpose: Triage overnight scanner false positives\n"
        "    trigger: 'cron:0 6 * * *'\n"
        "    inputs: []\n"
        "    outputs: []\n"
        "    artifacts: []\n"
        "    verifier: verifiers/triage_verifier.py\n"
        "    stop_condition: stop_conditions/triage_stop.py\n"
        "    failure_behavior: escalate_to_human\n"
        "    side_effect_level: 2\n"
        "    handoff: handoffs/triage_handoff.py\n"
    )
    with pytest.raises(adapter.AdapterError, match=r"missing required block 'discovery'"):
        adapter.load(str(tmp_path))


def test_dark_factory_own_adapter_yaml_loads_clean_after_reshape():
    """Spec R1 fail-open precondition: the live .factory/adapter.yaml declares no
    loops:, so the breaking reshape must not raise on it (an AdapterError would
    silently drop every safety override back to DEFAULTS)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((repo_root / ".factory" / "adapter.yaml").read_text())
    assert "loops" not in raw
    merged = adapter.load(str(repo_root))
    assert merged["loops"] == []
    assert merged["safety"]["hard_exclude_paths"] == raw["safety"]["hard_exclude_paths"]


def test_role_card_valid_parses(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {
        "name": "Triage Agent",
        "responsibilities": ["classify false positives"],
        "non_responsibilities": ["patch the scanner"],
        "output_schema": "schemas/triage_report.json",
        "fallback_path": "manual-review:security-team",
        "observability": ["triage.completed", "triage.escalated"],
    }
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["role_card"]["name"] == "Triage Agent"


def test_role_card_empty_dict_missing_name_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'role_card': missing required field 'name'"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", ["name", "output_schema", "fallback_path"])
def test_role_card_string_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: 42}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block 'role_card': field '{field}' must be a non-empty string")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", ["responsibilities", "non_responsibilities", "observability"])
def test_role_card_list_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: "not-a-list"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block 'role_card': field '{field}' must be a list of strings")):
        adapter.load(str(tmp_path))


def test_role_card_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {"name": "Triage Agent", "extra_typo_field": "oops"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'role_card': unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


def test_economics_empty_dict_accepted(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["economics"] = {}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["economics"] == {}


def test_economics_valid_parses(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["economics"] = {
        "context_offload_required": True,
        "feature_demand": "high",
        "model_capability_floor": "sonnet",
    }
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["economics"]["context_offload_required"] is True


@pytest.mark.parametrize("bad_bool", [1, "yes"])
def test_economics_context_offload_required_rejects_non_bool(tmp_path, bad_bool):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["economics"] = {"context_offload_required": bad_bool}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'economics': field 'context_offload_required' must be a bool"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", ["feature_demand", "model_capability_floor"])
def test_economics_string_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["economics"] = {field: 42}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block 'economics': field '{field}' must be a non-empty string")):
        adapter.load(str(tmp_path))


def test_economics_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["economics"] = {"extra_typo_field": "oops"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'economics': unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


def test_skills_empty_dict_accepted(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["skills"] = {}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["skills"] == {}


@pytest.mark.parametrize("field", ["primary", "supplemental", "forbidden", "eval_cases"])
def test_skills_list_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["skills"] = {field: "not-a-list"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block 'skills': field '{field}' must be a list of strings")):
        adapter.load(str(tmp_path))


def test_skills_valid_parses(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["skills"] = {
        "primary": ["triage-classifier"],
        "supplemental": ["log-search"],
        "forbidden": ["deploy"],
        "eval_cases": ["evals/triage_case_1.yaml"],
    }
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["skills"]["primary"] == ["triage-classifier"]


def test_skills_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["skills"] = {"extra_typo_field": "oops"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'skills': unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", ["allowed_tools", "forbidden_tools"])
def test_role_card_tool_fields_permanently_excluded(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: ["bash"]}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(
        adapter.AdapterError,
        match=re.escape(
            f"role_card field '{field}' is a tool allow/deny declaration and is "
            f"permanently excluded from adapter.yaml")):
        adapter.load(str(tmp_path))


def test_budget_caps_empty_dict_missing_max_tokens_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["budget_caps"] = {}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'budget_caps': missing required field 'max_tokens'"):
        adapter.load(str(tmp_path))


def test_budget_caps_max_tokens_bool_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["budget_caps"] = {"max_tokens": True}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'budget_caps': field 'max_tokens' must be an int >= 1"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", ["max_tokens", "max_retry_spend"])
def test_budget_caps_int_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, field: "many"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"block 'budget_caps': field '{field}' must be an int >= 1")):
        adapter.load(str(tmp_path))


def test_budget_caps_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "extra_typo_field": "oops"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"block 'budget_caps': unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


def test_human_checkpoint_wrong_type_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["human_checkpoint"] = 42
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=r"field 'human_checkpoint' must be a non-empty string"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("sel", [4, 5, 6])
def test_side_effect_level_high_without_budget_caps_raises(tmp_path, sel):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = sel
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape(f"side_effect_level {sel} >= 4 requires 'budget_caps'")):
        adapter.load(str(tmp_path))


def test_side_effect_level_high_with_budget_caps_missing_human_checkpoint_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = 4
    parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError,
                        match=re.escape("side_effect_level 4 >= 4 requires 'human_checkpoint'")):
        adapter.load(str(tmp_path))


def test_side_effect_level_high_with_both_caps_accepted(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = 6
    parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "max_retry_spend": 10000}
    parsed["loops"][0]["human_checkpoint"] = "manual-approval:slack-#factory-ops"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["side_effect_level"] == 6


def test_side_effect_level_3_without_either_accepted(tmp_path):
    """Below the R4 threshold: no budget_caps/human_checkpoint required."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = 3
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    merged = adapter.load(str(tmp_path))
    assert merged["loops"][0]["side_effect_level"] == 3


def test_loop_entry_all_optional_blocks_parses(tmp_path):
    """Acceptance criterion: the R1 example entry, with every optional field
    declared at once, parses and round-trips through adapter.get()."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = 4
    parsed["loops"][0]["human_checkpoint"] = "manual-approval:slack-#factory-ops"
    parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "max_retry_spend": 10000}
    parsed["loops"][0]["role_card"] = {"name": "Triage Agent"}
    parsed["loops"][0]["economics"] = {"feature_demand": "high"}
    parsed["loops"][0]["skills"] = {"primary": ["triage-classifier"]}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    loops = adapter.get(str(tmp_path), "loops")
    entry = loops[0]
    assert entry["human_checkpoint"] == "manual-approval:slack-#factory-ops"
    assert entry["budget_caps"]["max_tokens"] == 50000
    assert entry["role_card"]["name"] == "Triage Agent"
    assert entry["economics"]["feature_demand"] == "high"
    assert entry["skills"]["primary"] == ["triage-classifier"]


@pytest.mark.parametrize("bad_level", [0, 7, -1, 100])
def test_loop_entry_side_effect_level_out_of_range_raises(tmp_path, bad_level):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = bad_level
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
        adapter.load(str(tmp_path))


def test_loop_entry_side_effect_level_non_int_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = "two"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("bad_bool", [True, False])
def test_loop_entry_side_effect_level_bool_raises(tmp_path, bad_bool):
    """bool is a subclass of int in Python; side_effect_level: true/false must not
    slip through as a valid level (true would otherwise pass as level 1)."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = bad_bool
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
        adapter.load(str(tmp_path))


def test_duplicate_loop_names_raise(tmp_path):
    """Two loop entries sharing the same name are ambiguous for A2-A5 enforcement
    and run-record provenance, which key on name — must be rejected."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    second = copy.deepcopy(parsed["loops"][0])
    parsed["loops"].append(second)  # same name as the first entry
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=r"duplicate loop name 'nightly-scan-triage'"):
        adapter.load(str(tmp_path))


def test_loop_entry_memory_intervention_reserved_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["memory_intervention"] = {"policy": "whatever"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=r"reserved for epic #241"):
        adapter.load(str(tmp_path))


def test_loop_entry_contract_reserved_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["contract"] = {"objective": "whatever"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(
        adapter.AdapterError,
        match=r"reserved for a follow-up child of epic #194"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("reserved_key, match", [
    ("contract", r"reserved for a follow-up child of epic #194"),
    ("memory_intervention", r"reserved for epic #241"),
])
def test_loop_entry_reserved_key_beats_missing_blocks(tmp_path, reserved_key, match):
    """Gate 3 (#301) regression: the reserved-key scan is hoisted ahead of the
    five-move block loop, so an entry that is BOTH incomplete and carries a
    reserved key reports the R5 reserved message, not 'missing required block'."""
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    entry = parsed["loops"][0]
    for block in ("discovery", "handoff", "verification", "persistence", "scheduling"):
        entry.pop(block, None)
    entry[reserved_key] = {"anything": "at all"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=match):
        adapter.load(str(tmp_path))


def test_mechanism_candidates_top_level_reserved_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "mechanism_candidates:\n  - id: mc-1\n    target_loop: nightly-scan-triage\n")
    with pytest.raises(adapter.AdapterError, match=r"mechanism_candidates.*reserved"):
        adapter.load(str(tmp_path))


# ── Parity tests: pin verbatim copies to their source constants ────────────────

def test_components_parity():
    """adapter_defaults.DEFAULTS['components'] must equal COMPONENT_SECTION_MAP verbatim."""
    sys.path.insert(0, "scripts")
    from architecture_slice import COMPONENT_SECTION_MAP
    assert adapter_defaults.DEFAULTS["components"] == COMPONENT_SECTION_MAP


def test_critical_diff_paths_parity():
    """adapter_defaults critical_diff_paths must match diff_rank.SAFETY_PATH_PATTERNS strings."""
    sys.path.insert(0, "scripts")
    from diff_rank import SAFETY_PATH_PATTERNS
    expected = [p.pattern for p in SAFETY_PATH_PATTERNS]
    assert adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"] == expected


# ── Consumer 1: architecture_slice._component_section_map ─────────────────────

def test_component_section_map_default_parity(tmp_path):
    """Without adapter file, _component_section_map returns DEFAULTS['components']."""
    sys.path.insert(0, "scripts")
    import architecture_slice as a
    assert a._component_section_map(str(tmp_path)) == adapter_defaults.DEFAULTS["components"]


def test_component_section_map_adapter_override(tmp_path):
    """With adapter file overriding components, merged result is returned."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "components:\n  api: ['Overview', 'API Layer']\n")
    sys.path.insert(0, "scripts")
    import architecture_slice as a
    m = a._component_section_map(str(tmp_path))
    assert m["api"] == ["Overview", "API Layer"]
    assert "backend" in m  # defaults still merged in


# ── Consumer 6: adapter CLI --format keyvalue (gate_lib.sh support) ───────────

def test_adapter_cli_keyvalue_format(tmp_path, capsys):
    """--format keyvalue emits tab-separated key\\tvalue lines for dict values."""
    import sys as _sys
    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["adapter", "--clone-dir", str(tmp_path),
                     "--get", "memory_routing", "--format", "keyvalue"]
        adapter.main()
    except SystemExit:
        pass
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    # Default memory_routing has at least one entry; each line must be key<TAB>value
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) > 0
    for line in lines:
        assert "\t" in line, f"Expected tab-separated line, got: {line!r}"


def test_adapter_cli_keyvalue_format_override(tmp_path, capsys):
    """--format keyvalue reflects adapter.yaml override for dict values."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "memory_routing:\n  custom/path/*: '.archon/memory/custom.md'\n")
    import sys as _sys
    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["adapter", "--clone-dir", str(tmp_path),
                     "--get", "memory_routing", "--format", "keyvalue"]
        adapter.main()
    except SystemExit:
        pass
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    assert "custom/path/*\t.archon/memory/custom.md" in out


# ── Consumer 2: diff_rank._safety_path_patterns ────────────────────────────────

def test_safety_path_patterns_default_parity(tmp_path):
    """Without adapter file, _safety_path_patterns returns compiled patterns from DEFAULTS."""
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    patterns = dr._safety_path_patterns(str(tmp_path))
    assert [p.pattern for p in patterns] == adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]


def test_safety_path_patterns_adapter_override(tmp_path):
    """With adapter override, returns overridden compiled patterns (deep-merged)."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  critical_diff_paths:\n    - '^custom/path/'\n")
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    patterns = dr._safety_path_patterns(str(tmp_path))
    pattern_strings = [p.pattern for p in patterns]
    assert "^custom/path/" in pattern_strings


# ── Consumer 3: gate_blast_radius._migration_seed_auth_patterns ────────────────

def test_migration_seed_auth_patterns_default_parity(tmp_path):
    """Without adapter file, _migration_seed_auth_patterns returns DEFAULTS patterns."""
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    patterns = gbr._migration_seed_auth_patterns(str(tmp_path))
    assert [p.pattern for p in patterns] == adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]


def test_migration_seed_auth_patterns_adapter_override(tmp_path):
    """With adapter override, returns overridden compiled patterns."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  migration_seed_auth_patterns:\n    - '^custom/migrations/'\n")
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    patterns = gbr._migration_seed_auth_patterns(str(tmp_path))
    pattern_strings = [p.pattern for p in patterns]
    assert "^custom/migrations/" in pattern_strings


# ── Consumer 4: epic_autopilot._hard_exclude_paths + _sensitive_keywords ───────

def test_hard_exclude_paths_default_parity(tmp_path):
    """Without adapter file, _hard_exclude_paths returns DEFAULTS safety.hard_exclude_paths."""
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    assert ap._hard_exclude_paths(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]


def test_hard_exclude_paths_adapter_override(tmp_path):
    """With adapter override, returns overridden list (deep-merged)."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  hard_exclude_paths:\n    - 'custom/excluded/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    paths = ap._hard_exclude_paths(str(tmp_path))
    assert "custom/excluded/" in paths


def test_sensitive_keywords_default_parity(tmp_path):
    """Without adapter file, _sensitive_keywords returns DEFAULTS safety.sensitive_keywords."""
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    assert ap._sensitive_keywords(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["sensitive_keywords"]


def test_sensitive_keywords_adapter_override(tmp_path):
    """With adapter override, returns overridden sensitive_keywords string."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  sensitive_keywords: 'payments|pci'\n")
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    kw = ap._sensitive_keywords(str(tmp_path))
    assert kw == "payments|pci"


# ── Consumer 5: main_red_fixer._main_red_allowed_paths ─────────────────────────

def test_main_red_allowed_paths_default_parity(tmp_path):
    """Without adapter file, _main_red_allowed_paths returns DEFAULTS safety.main_red_allowed_paths."""
    sys.path.insert(0, "scripts")
    from factory_core import main_red_fixer as mf
    assert mf._main_red_allowed_paths(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["main_red_allowed_paths"]


def test_main_red_allowed_paths_adapter_override(tmp_path):
    """With adapter override, returns overridden allowed_paths list."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  main_red_allowed_paths:\n    - 'custom/'\n    - 'other/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import main_red_fixer as mf
    paths = mf._main_red_allowed_paths(str(tmp_path))
    assert "custom/" in paths
    assert "other/" in paths


# ── Consumer 7: deconflict._deconflict_models_init + _deconflict_migrations_dir ─

def test_deconflict_models_init_default_parity(tmp_path):
    """Without adapter file, _deconflict_models_init returns DEFAULTS deconflict.models_init."""
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_models_init(str(tmp_path)) == adapter_defaults.DEFAULTS["deconflict"]["models_init"]


def test_deconflict_models_init_adapter_override(tmp_path):
    """With adapter override, returns overridden models_init path."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "deconflict:\n  models_init: 'src/models/__init__.py'\n")
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_models_init(str(tmp_path)) == "src/models/__init__.py"


def test_deconflict_migrations_dir_default_parity(tmp_path):
    """Without adapter file, _deconflict_migrations_dir returns DEFAULTS deconflict.migrations_dir."""
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_migrations_dir(str(tmp_path)) == adapter_defaults.DEFAULTS["deconflict"]["migrations_dir"]


def test_deconflict_migrations_dir_adapter_override(tmp_path):
    """With adapter override, returns overridden migrations_dir path."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "deconflict:\n  migrations_dir: 'db/migrations/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_migrations_dir(str(tmp_path)) == "db/migrations/"


# ── Skill-security safety globs (#46) ──────────────────────────────────────

def test_skill_security_globs_in_defaults_hard_exclude_paths():
    paths = adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]
    assert any(".claude/skills/" in p for p in paths)
    assert any("settings.json" in p for p in paths)
    assert any(".mcp.json" in p for p in paths)
    assert any(".claude/plugins/" in p for p in paths)
    assert any(".claude-plugin/" in p for p in paths)
    assert any(".factory/hooks/" in p for p in paths)


def test_skill_security_globs_in_defaults_critical_diff_paths():
    import re
    patterns = adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]
    for p in patterns:
        re.compile(p)  # every entry must be a valid regex
    joined = "|".join(patterns)
    assert "claude/skills" in joined
    assert re.search(r"settings\\?\.json", joined)  # dot is regex-escaped in these patterns
    assert "factory/hooks" in joined
    assert any("SKILL" in p for p in patterns), "SKILL.md must appear (visibility only)"


def test_skill_md_not_in_migration_seed_auth_patterns():
    """SKILL.md must never be a path-level HUMAN_REQUIRED trigger — see spec Q2/A2."""
    patterns = adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]
    assert not any("SKILL" in p for p in patterns)


def test_skill_scripts_and_settings_in_migration_seed_auth_patterns():
    import re
    patterns = [re.compile(p) for p in adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]]
    assert any(p.search(".claude/skills/code-review/scripts/foo.py") for p in patterns)
    assert any(p.search(".claude/settings.json") for p in patterns)
    assert any(p.search(".factory/hooks/validate") for p in patterns)


# ── SKILL_SECURITY_TOKENS parity (diff_rank.py / gate_blast_radius.py) ─────────

def test_skill_security_tokens_parity():
    """diff_rank._SKILL_SECURITY_TOKENS and gate_blast_radius._SKILL_SECURITY_TOKENS
    must both be the literal adapter_defaults.SKILL_SECURITY_TOKENS object, not copies."""
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    import gate_blast_radius as gbr
    assert dr._SKILL_SECURITY_TOKENS is adapter_defaults.SKILL_SECURITY_TOKENS
    assert gbr._SKILL_SECURITY_TOKENS is adapter_defaults.SKILL_SECURITY_TOKENS


# ── config.yaml drift guard (#184) ──────────────────────────────────────────
# config.yaml keeps its own copy of these safety constants for operator
# visibility; these tests guarantee it cannot silently diverge from
# adapter_defaults.DEFAULTS.

def test_config_yaml_sensitive_keywords_matches_defaults():
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((repo_root / "config" / "config.yaml").read_text())
    assert cfg["epic_autopilot"]["sensitive_keywords"] == \
        adapter_defaults.DEFAULTS["safety"]["sensitive_keywords"]


def test_config_yaml_hard_exclude_paths_matches_defaults():
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((repo_root / "config" / "config.yaml").read_text())
    assert cfg["epic_autopilot"]["hard_exclude_paths"] == \
        adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]


def test_config_yaml_dispatch_ceiling_keywords_matches_defaults():
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((repo_root / "config" / "config.yaml").read_text())
    assert cfg["dispatch_ceiling"]["keywords"] == \
        adapter_defaults.DEFAULTS["safety"]["dispatch_ceiling_keywords"]


def test_dark_factory_own_adapter_yaml_has_skill_security_globs():
    """Guards the A4 merge-semantics gap: .factory/adapter.yaml list-replaces DEFAULTS,
    so it must carry the skill-security globs independently, not just inherit them."""
    import re
    import yaml
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((repo_root / ".factory" / "adapter.yaml").read_text())
    for key in ("hard_exclude_paths", "critical_diff_paths", "migration_seed_auth_patterns"):
        joined = "|".join(data["safety"][key])
        assert ".claude/skills" in joined, f"{key} missing .claude/skills glob"
        # dot is regex-escaped in the two pattern-based lists but not in hard_exclude_paths
        assert re.search(r"settings\\?\.json", joined), f"{key} missing settings.json glob"
        assert "factory/hooks" in joined, f"{key} missing .factory/hooks glob"
    assert not any("SKILL" in p for p in data["safety"]["migration_seed_auth_patterns"])

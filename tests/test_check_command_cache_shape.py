"""Tests for check_command_cache_shape.py — the phase-command cache-shape lint (#236).

R1 forbids the brace form of Archon-substituted names (it resolves to the wrong artifacts
directory in a command node's shell); R2 ratchets each file's first volatile line against
the committed baseline; R3 keeps the baseline complete. ``--json`` emits the static
prompt-shape metadata.
"""

import hashlib
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from check_command_cache_shape import DEFAULT_BASELINE, check, shape  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMANDS_DIR = _REPO_ROOT / "commands"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup(tmp_path: Path, files: dict, baseline: dict) -> tuple[Path, Path]:
    commands = tmp_path / "commands"
    commands.mkdir()
    for name, text in files.items():
        (commands / f"{name}.md").write_bytes(text.encode("utf-8"))
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    return commands, baseline_path


def _one(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cmd.md"
    p.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
    return p


def _names(tmp_path: Path, text: str) -> dict:
    return {f["name"]: f["count"] for f in shape(_one(tmp_path, text))["cache_busting_fields"]}


# ---------------------------------------------------------------------------
# Token matching (mirrors Archon)
# ---------------------------------------------------------------------------

def test_plain_names_have_no_word_boundary(tmp_path):
    assert _names(tmp_path, "$ARTIFACTS_DIRX\n") == {"ARTIFACTS_DIR": 1}


def test_context_names_have_lookahead(tmp_path):
    assert _names(tmp_path, "$CONTEXTS $ISSUE_CONTEXT_X\n") == {}


def test_issue_context_reported_as_itself(tmp_path):
    assert _names(tmp_path, "$ISSUE_CONTEXT and $CONTEXT\n") == {"ISSUE_CONTEXT": 1, "CONTEXT": 1}


def test_node_output_references(tmp_path):
    assert _names(tmp_path, "$plan.output then $plan.output.field\n") == {"NODE_OUTPUT": 2}


# ---------------------------------------------------------------------------
# Prompt-shape metadata
# ---------------------------------------------------------------------------

def test_prefix_hash_is_sha256_of_bytes_before_first_token(tmp_path):
    prefix = "---\ndescription: é\n"
    meta = shape(_one(tmp_path, prefix + "x $ARTIFACTS_DIR tail\n"))
    stable = (prefix + "x ").encode("utf-8")
    assert meta["prefix_hash"] == hashlib.sha256(stable).hexdigest()
    assert meta["stable_prefix_bytes"] == len(stable)
    assert meta["volatile_tail_bytes"] == len("$ARTIFACTS_DIR tail\n")
    assert meta["volatile_tail_tokens_est"] == len("$ARTIFACTS_DIR tail\n") // 4
    assert meta["first_volatile_line"] == 3


def test_prefix_hash_ignores_tail_changes(tmp_path):
    a = shape(_one(tmp_path, "head\n$WORKFLOW_ID tail one\n"))["prefix_hash"]
    b = shape(_one(tmp_path, "head\n$WORKFLOW_ID tail two\n"))["prefix_hash"]
    assert a == b


def test_prefix_hash_changes_with_prefix(tmp_path):
    a = shape(_one(tmp_path, "head\n$WORKFLOW_ID\n"))["prefix_hash"]
    b = shape(_one(tmp_path, "heaD\n$WORKFLOW_ID\n"))["prefix_hash"]
    assert a != b


def test_cache_busting_fields_counts_and_first_lines(tmp_path):
    text = "a\n$WORKFLOW_ID\n$ARTIFACTS_DIR $ARTIFACTS_DIR\n$ARTIFACTS_DIR\n"
    meta = shape(_one(tmp_path, text))
    assert meta["command"] == "cmd"
    assert meta["cache_busting_fields"] == [
        {"name": "WORKFLOW_ID", "count": 1, "first_line": 2},
        {"name": "ARTIFACTS_DIR", "count": 3, "first_line": 3},
    ]


def test_file_with_no_token_is_all_prefix(tmp_path):
    meta = shape(_one(tmp_path, "one\ntwo\n"))
    assert meta["first_volatile_line"] == 3      # line count + 1
    assert meta["volatile_tail_bytes"] == 0
    assert meta["cache_busting_fields"] == []
    assert meta["prefix_hash"] == hashlib.sha256(b"one\ntwo\n").hexdigest()


# ---------------------------------------------------------------------------
# R1: brace form
# ---------------------------------------------------------------------------

def test_r1_brace_form_fails(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "x\n${ARTIFACTS_DIR}/issue.json\n"}, {"a": 3})
    errors = check(commands, baseline)
    assert len(errors) == 1
    assert "R1" in errors[0] and "a.md:2" in errors[0]


def test_r1_brace_form_with_default_fails(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "x\n${ARTIFACTS_DIR:-x}\n"}, {"a": 3})
    errors = check(commands, baseline)
    assert len(errors) == 1 and "R1" in errors[0]


def test_r1_unrelated_brace_variables_pass(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "${OTHER} ${CONTEXT_PACK}\n"}, {"a": 2})
    assert check(commands, baseline) == []


# ---------------------------------------------------------------------------
# R2: prefix ratchet
# ---------------------------------------------------------------------------

def test_r2_token_earlier_than_baseline_fails(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "one\n$ARTIFACTS_DIR\n"}, {"a": 3})
    errors = check(commands, baseline)
    assert len(errors) == 1
    assert "R2" in errors[0] and "line 2" in errors[0] and "line 3" in errors[0]


def test_r2_token_at_baseline_passes(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "one\ntwo\n$ARTIFACTS_DIR\n"}, {"a": 3})
    assert check(commands, baseline) == []


def test_r2_token_later_than_baseline_passes(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "one\ntwo\nthree\n$WORKFLOW_ID\n"}, {"a": 3})
    assert check(commands, baseline) == []


def test_r2_file_with_no_token_passes(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "one\ntwo\n"}, {"a": 3})
    assert check(commands, baseline) == []


# ---------------------------------------------------------------------------
# R3: baseline complete
# ---------------------------------------------------------------------------

def test_r3_missing_entry_fails_and_names_fix(tmp_path):
    commands, baseline = _setup(
        tmp_path, {"a": "x\n", "new-cmd": "one\n$ARTIFACTS_DIR\n"}, {"a": 2}
    )
    errors = check(commands, baseline)
    assert len(errors) == 1
    assert "R3" in errors[0]
    assert str(baseline) in errors[0]
    assert '"new-cmd": 2' in errors[0]


def test_r3_orphan_entry_fails(tmp_path):
    commands, baseline = _setup(tmp_path, {"a": "x\n"}, {"a": 2, "gone": 3})
    errors = check(commands, baseline)
    assert len(errors) == 1
    assert "R3" in errors[0] and '"gone"' in errors[0] and str(baseline) in errors[0]


def test_unreadable_baseline_fails(tmp_path):
    commands, _ = _setup(tmp_path, {"a": "x\n"}, {})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert check(commands, bad) != []
    assert check(commands, tmp_path / "missing.json") != []


# ---------------------------------------------------------------------------
# Real tree
# ---------------------------------------------------------------------------

def test_real_commands_pass():
    assert check(_COMMANDS_DIR) == []


def test_baseline_matches_committed_commands():
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    current = {p.stem: shape(p)["first_volatile_line"] for p in sorted(_COMMANDS_DIR.glob("*.md"))}
    assert baseline == current, (
        f"{DEFAULT_BASELINE.name} is out of date. A command file's first volatile line moved "
        "(a line was added or removed above its first $TOKEN) or a command file was added or "
        "removed. Set the baseline to these values in the same commit: "
        + json.dumps(current, sort_keys=True)
    )

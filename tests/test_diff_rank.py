"""Tests for dark-factory/scripts/diff_rank.py.

All tests are in-process (no git/subprocess). A run_main() helper patches
sys.argv and sys.stdout so main() can be exercised end-to-end with temp files.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# In-process import matching fmt_hunk_filter.py's self-contained pattern
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diff_rank as dr          # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_cwd(tmp_path, monkeypatch):
    """Run from an adapter-free directory.

    This repo carries its own .factory/adapter.yaml (self-target dogfood);
    the default clone_dir="." lookup would pick it up and override the
    MarketHawk-parity defaults these tests assert.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLONE_DIR", raising=False)

import gate_blast_radius as gbr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(token_cap=6000, score_floor=5.0):
    return {
        "token_optimization": {"diff": {"max_review_tokens": token_cap}},
        "blast_radius": {"hotspot_score_floor": score_floor},
    }


def make_diff(path, added=5, removed=2, n_hunks=1):
    """Build a minimal synthetic unified diff for one file."""
    hunks = ""
    for i in range(n_hunks):
        offset = 1 + i * max(added, removed)
        hunks += f"@@ -{offset},{removed} +{offset},{added} @@\n"
        for _ in range(removed):
            hunks += "-old line\n"
        for _ in range(added):
            hunks += "+new line\n"
        for _ in range(2):
            hunks += " ctx line\n"
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index aaa..bbb 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"{hunks}"
    )


def run_main(
    diff_content,
    token_cap=6000,
    score_floor=5.0,
    hotspots_content="",
    spec_content=None,
    missing_hotspots=False,
):
    """Run dr.main() in-process; return (stdout_str, ranking_dict_or_None)."""
    with tempfile.TemporaryDirectory() as artifacts_dir:
        with (
            tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as df,
            tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cf,
            tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as hf,
        ):
            df.write(diff_content)
            df.flush()
            yaml.dump(make_config(token_cap, score_floor), cf)
            cf.flush()
            hf.write(hotspots_content)
            hf.flush()

            hotspots_path = "/tmp/nonexistent_diffrank_XXXX.md" if missing_hotspots else hf.name

            argv = [
                "diff_rank.py",
                "--diff", df.name,
                "--artifacts-dir", artifacts_dir,
                "--config", cf.name,
                "--hotspots", hotspots_path,
            ]

            if spec_content is not None:
                with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as sf:
                    sf.write(spec_content)
                    sf.flush()
                    argv += ["--spec-file", sf.name]

            buf = io.StringIO()
            with patch("sys.argv", argv), patch("sys.stdout", buf):
                try:
                    dr.main()
                except SystemExit:
                    pass

            output = buf.getvalue()
            ranking_path = Path(artifacts_dir) / "diff-ranking.json"
            ranking = (
                json.loads(ranking_path.read_text())
                if ranking_path.exists()
                else None
            )

    return output, ranking


# ---------------------------------------------------------------------------
# Risk classification — direct calls to dr.classify_file()
# ---------------------------------------------------------------------------

def test_classify_auth_path_is_critical():
    tier, signals, _ = dr.classify_file(
        "backend/app/routers/auth/router.py", set(), set(), 5.0
    )
    assert tier == "critical"
    assert any("auth" in s for s in signals)


def test_classify_auth_core_is_critical():
    tier, signals, _ = dr.classify_file(
        "backend/app/core/auth/jwt.py", set(), set(), 5.0
    )
    assert tier == "critical"


def test_classify_migration_is_critical():
    tier, signals, _ = dr.classify_file(
        "alembic/versions/001_add_col.py", set(), set(), 5.0
    )
    assert tier == "critical"


def test_classify_trading_service_is_critical():
    tier, signals, _ = dr.classify_file(
        "backend/app/services/trading/order.py", set(), set(), 5.0
    )
    assert tier == "critical"


def test_classify_trading_tasks_is_critical():
    tier, signals, _ = dr.classify_file(
        "backend/app/tasks/trading.py", set(), set(), 5.0
    )
    assert tier == "critical"


def test_classify_dark_factory_path_is_critical():
    tier, signals, _ = dr.classify_file(
        "dark-factory/scripts/some_script.py", set(), set(), 5.0
    )
    assert tier == "critical"


def test_classify_hotspot_above_floor_is_critical():
    hotspot_paths = {"backend/app/services/scanner.py"}
    tier, signals, _ = dr.classify_file(
        "backend/app/services/scanner.py", hotspot_paths, set(), 5.0
    )
    assert tier == "critical"
    assert "hotspot" in signals


def test_classify_hotspot_below_floor_is_not_critical():
    # File appears in hotspot_paths only when at/above floor — if not in set, not critical
    tier, _, _ = dr.classify_file(
        "backend/app/services/scanner.py", set(), set(), 5.0
    )
    # Not critical via path patterns; lands in high/medium/low depending on context
    assert tier != "critical"


def test_classify_router_is_high():
    tier, signals, _ = dr.classify_file(
        "backend/app/routers/scanner.py", set(), set(), 5.0
    )
    assert tier == "high"
    assert "api_endpoint" in signals


def test_classify_spec_named_is_high():
    spec_names = {"dark-factory/scripts/diff_rank.py"}
    tier, signals, _ = dr.classify_file(
        "dark-factory/scripts/diff_rank.py", set(), spec_names, 5.0
    )
    # Note: dark-factory/ is critical via safety path; spec-named only matters for non-safety files
    # Use a non-safety path for this test:
    spec_names2 = {"backend/app/services/stock_data.py"}
    tier2, signals2, _ = dr.classify_file(
        "backend/app/services/stock_data.py", set(), spec_names2, 5.0, total_lines=10
    )
    assert tier2 == "high"
    assert "spec_named" in signals2


def test_classify_dependency_file_is_high():
    tier, signals, _ = dr.classify_file(
        "backend/requirements.txt", set(), set(), 5.0
    )
    assert tier == "high"
    assert "dependency" in signals


def test_classify_package_json_is_high():
    tier, signals, _ = dr.classify_file(
        "frontend/package.json", set(), set(), 5.0
    )
    assert tier == "high"
    assert "dependency" in signals


def test_classify_test_file_is_low():
    tier, signals, _ = dr.classify_file(
        "tests/test_scanner.py", set(), set(), 5.0
    )
    assert tier == "low"
    assert "test_file" in signals


def test_classify_spec_test_file_is_low():
    tier, signals, _ = dr.classify_file(
        "dark-factory/tests/test_blast_radius.py", set(), set(), 5.0
    )
    assert tier == "low"
    assert "test_file" in signals


def test_classify_ts_test_file_is_low():
    tier, signals, _ = dr.classify_file(
        "frontend/src/components/Scanner.test.ts", set(), set(), 5.0
    )
    assert tier == "low"


def test_classify_large_non_test_non_critical_is_medium():
    tier, _, _ = dr.classify_file(
        "backend/app/services/stock_data.py", set(), set(), 5.0, total_lines=100
    )
    assert tier == "medium"


def test_classify_small_non_test_non_critical_is_low():
    tier, _, _ = dr.classify_file(
        "backend/app/services/stock_data.py", set(), set(), 5.0, total_lines=10
    )
    assert tier == "low"


# ---------------------------------------------------------------------------
# Token budget — via run_main()
# ---------------------------------------------------------------------------

def test_critical_files_bypass_token_cap():
    """Auth file is critical and must be fully included even with a 1-token cap."""
    diff = make_diff("backend/app/routers/auth/router.py", added=200, removed=50)
    _, ranking = run_main(diff, token_cap=1)
    assert ranking is not None
    auth_entry = next(
        f for f in ranking["files"] if "auth" in f["path"]
    )
    assert auth_entry["included"] == "full"
    assert auth_entry["risk_class"] == "critical"


def test_high_files_fill_budget_then_summarize():
    """Router file (high) exceeds tiny cap → summarized."""
    diff = make_diff("backend/app/routers/scanner.py", added=200, removed=100)
    output, ranking = run_main(diff, token_cap=10)
    entry = next(f for f in ranking["files"] if "scanner.py" in f["path"])
    assert entry["included"] == "summary"
    assert "budget-exhausted" in output


def test_high_file_included_when_budget_allows():
    """Router file included in full when budget is large enough."""
    diff = make_diff("backend/app/routers/scanner.py", added=5, removed=2)
    _, ranking = run_main(diff, token_cap=100000)
    entry = next(f for f in ranking["files"] if "scanner.py" in f["path"])
    assert entry["included"] == "full"


def test_low_files_summarized_when_diff_exceeds_cap():
    """A low-tier file is summarized only when the remaining budget can't hold
    it — a cap smaller than the low file itself (not just smaller than the
    whole diff) exercises real budget pressure (R1)."""
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    _, ranking = run_main(diff, token_cap=5)
    entry = ranking["files"][0]
    assert entry["included"] == "summary"
    assert entry["risk_class"] == "low"


# ---------------------------------------------------------------------------
# Summary line format
# ---------------------------------------------------------------------------

def test_summary_line_low_risk_format():
    # make_diff with n_hunks=2 creates 42 added lines per hunk (84 total) and 3 removed per hunk (6 total)
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    output, _ = run_main(diff, token_cap=5)
    assert "# [SUMMARIZED: low-risk test-only] tests/test_scanner.py — +84/-6 (2 hunks)" in output


def test_summary_line_non_test_low_risk_not_labeled_test_only():
    """A non-test file in the low tier is labeled 'low-risk', never 'test-only'
    (regression for the #669 code-review finding: non-test low files were being
    misreported as tests, which could reduce reviewer scrutiny)."""
    diff = make_diff("frontend/src/utils/format.ts", added=5, removed=2, n_hunks=1)
    output, ranking = run_main(diff, token_cap=5)
    entry = ranking["files"][0]
    assert entry["risk_class"] == "low"
    assert "test_file" not in entry["signals"]
    assert "# [SUMMARIZED: low-risk] frontend/src/utils/format.ts" in output
    assert "test-only" not in output


def test_summary_line_budget_exhausted_format():
    diff = make_diff("backend/app/routers/scanner.py", added=200, removed=100, n_hunks=5)
    output, _ = run_main(diff, token_cap=10)
    assert "# [SUMMARIZED: budget-exhausted] backend/app/routers/scanner.py" in output


# ---------------------------------------------------------------------------
# Header line
# ---------------------------------------------------------------------------

def test_header_line_is_first_line():
    diff = make_diff("backend/app/services/utils.py", added=10, removed=5)
    output, _ = run_main(diff)
    assert output.splitlines()[0].startswith("# [diff-rank:")


def test_header_line_contains_tier_counts_and_token_info():
    diff = (
        make_diff("backend/app/routers/auth.py", added=5, removed=2)
        + make_diff("tests/test_scanner.py", added=3, removed=1)
    )
    output, _ = run_main(diff)
    first_line = output.splitlines()[0]
    assert "critical" in first_line
    assert "low" in first_line
    assert "tokens (cap" in first_line


# ---------------------------------------------------------------------------
# diff-ranking.json structure
# ---------------------------------------------------------------------------

def test_diff_ranking_json_written_on_run():
    diff = make_diff("backend/app/routers/scanner.py")
    _, ranking = run_main(diff)
    assert ranking is not None
    assert "token_cap" in ranking
    assert "estimated_tokens_emitted" in ranking
    assert "critical_tokens" in ranking
    assert "residual_tokens" in ranking
    assert "files" in ranking


def test_diff_ranking_json_file_entry_fields():
    diff = make_diff("tests/test_scanner.py", added=42, removed=3, n_hunks=2)
    _, ranking = run_main(diff, token_cap=5)
    f = ranking["files"][0]
    assert f["path"] == "tests/test_scanner.py"
    assert f["risk_class"] == "low"
    assert f["signals"] == ["test_file"]
    # make_diff with n_hunks=2 creates added lines per hunk, so totals are added*n_hunks
    assert f["lines_added"] == 84  # 42 added lines × 2 hunks
    assert f["lines_removed"] == 6  # 3 removed lines × 2 hunks
    assert f["hunk_count"] == 2
    assert f["included"] == "summary"
    assert f["estimated_tokens"] == 0


def test_diff_ranking_json_critical_token_accounting():
    """critical_tokens tracks bypass-cap usage; residual_tokens tracks budget usage."""
    diff = (
        make_diff("backend/app/routers/auth.py", added=40, removed=10)
        + make_diff("backend/app/routers/scanner.py", added=5, removed=2)
    )
    _, ranking = run_main(diff, token_cap=6000)
    assert ranking["critical_tokens"] > 0
    # scanner.py (high, small) should fit in budget
    scanner_entry = next(f for f in ranking["files"] if "scanner.py" in f["path"])
    assert scanner_entry["included"] == "full"


# ---------------------------------------------------------------------------
# R1/R2: whole-diff-vs-cap passthrough (#403)
# ---------------------------------------------------------------------------

def test_seven_file_diff_under_cap_not_summarized():
    """R1 acceptance test (from the issue): PR #398's failure mode was a
    7-file diff (~2,082 tokens) summarized to zero reviewable content under a
    6,000-token cap. Reproduce that shape and assert nothing gets summarized."""
    diff = "".join(
        make_diff(f"backend/app/services/module_{i}.py", added=30, removed=10, n_hunks=1)
        for i in range(7)
    )
    output, ranking = run_main(diff, token_cap=6000)
    assert "[SUMMARIZED" not in output
    assert len(ranking["files"]) == 7
    assert ranking["raw_diff_tokens"] < 6000
    assert all(f["included"] == "full" for f in ranking["files"])


def test_low_files_verbatim_when_whole_diff_fits_cap():
    """When the whole diff fits under the cap, a low-tier file passes through
    in full rather than being summarized (R1)."""
    diff = make_diff("tests/test_scanner.py", added=5, removed=2, n_hunks=1)
    output, ranking = run_main(diff, token_cap=6000)
    entry = ranking["files"][0]
    assert entry["included"] == "full"
    assert entry["risk_class"] == "low"
    assert "[SUMMARIZED" not in output


def test_under_cap_passthrough_marker_and_full_records():
    """R2: the passthrough marks itself in diff-ranking.json and every file
    record is 'included: full' with its real token count."""
    diff = (
        make_diff("tests/test_scanner.py", added=5, removed=2)
        + make_diff("backend/app/services/utils.py", added=10, removed=5)
    )
    _, ranking = run_main(diff, token_cap=6000)
    assert ranking["under_cap_passthrough"] is True
    for f in ranking["files"]:
        assert f["included"] == "full"
        assert f["estimated_tokens"] > 0


def test_passthrough_per_file_with_header_first_drops_pretriage_annotation():
    """R1/R2: passthrough output is built per-file (not raw diff_text), so a
    leading [Pre-triage] annotation (already stripped by parse_diff_files) never
    resurfaces, and the '# [diff-rank: ...]' header is still the first line."""
    annotation = "[Pre-triage] hunk-filter applied: 1 files / 1 hunks retained\n"
    diff = annotation + make_diff("backend/app/services/utils.py", added=5, removed=2)
    output, ranking = run_main(diff, token_cap=6000)
    lines = output.splitlines()
    assert lines[0].startswith("# [diff-rank:")
    assert "[Pre-triage]" not in output
    assert ranking["under_cap_passthrough"] is True


def test_low_file_full_when_budget_remains_over_cap():
    """R1: above the cap, low is budget-checked like high/medium — a small low
    file is emitted in full when enough budget remains after larger tiers."""
    diff = (
        make_diff("backend/app/routers/scanner.py", added=200, removed=100)
        + make_diff("tests/test_small.py", added=2, removed=1)
    )
    _, ranking = run_main(diff, token_cap=500)
    high_entry = next(f for f in ranking["files"] if "scanner.py" in f["path"])
    low_entry = next(f for f in ranking["files"] if "test_small.py" in f["path"])
    assert high_entry["included"] == "summary"
    assert low_entry["included"] == "full"
    assert low_entry["risk_class"] == "low"


# ---------------------------------------------------------------------------
# Fail-open: missing --hotspots file
# ---------------------------------------------------------------------------

def test_fail_open_missing_hotspots_proceeds():
    """On missing hotspots file, script proceeds without blast scores (not an error)."""
    diff = make_diff("backend/app/services/utils.py", added=10, removed=5)
    output, ranking = run_main(diff, missing_hotspots=True)
    # Script should still produce output — missing hotspots is a soft fallback
    assert ranking is not None
    assert ranking["files"][0]["blast_score"] is None


# ---------------------------------------------------------------------------
# Empty diff
# ---------------------------------------------------------------------------

def test_empty_diff_empty_stdout():
    output, ranking = run_main("")
    assert output.strip() == ""
    assert ranking is not None
    assert ranking["files"] == []
    assert ranking["estimated_tokens_emitted"] == 0


# ---------------------------------------------------------------------------
# parse_diff_files tolerates leading [Pre-triage] annotation
# ---------------------------------------------------------------------------

def test_parse_diff_files_tolerates_leading_pretriage_annotation():
    """Lines before the first 'diff --git' header (e.g. [Pre-triage] from fmt_hunk_filter)
    must not corrupt the file list — they are silently skipped because cur is None."""
    annotation = "[Pre-triage] hunk-filter applied: 2 files / 3 hunks retained\n"
    diff = annotation + make_diff("backend/app/routers/scanner.py", added=3, removed=1)
    output, ranking = run_main(diff)
    # Only the actual diff file should appear; the annotation must not be parsed as a file
    assert ranking is not None
    assert len(ranking["files"]) == 1
    assert ranking["files"][0]["path"] == "backend/app/routers/scanner.py"


# ---------------------------------------------------------------------------
# parse_hotspots import agreement
# ---------------------------------------------------------------------------

def test_parse_hotspots_import_agrees_with_gate_blast_radius():
    """The parse_hotspots imported in diff_rank must equal gate_blast_radius.parse_hotspots."""
    hotspots_content = (
        "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
        "    3.1  frontend/src/api/client.ts  (1d / 5t)  100 loc\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as hf:
        hf.write(hotspots_content)
        hf.flush()
        result_dr = dr.parse_hotspots(hf.name, 5.0)
        result_gbr = gbr.parse_hotspots(hf.name, 5.0)
    assert isinstance(result_dr, set)   # must be a set, not a dict (scores are separate)
    assert result_dr == result_gbr
    assert "backend/app/services/scanner.py" in result_dr
    assert "frontend/src/api/client.ts" not in result_dr  # score 3.1 < 5.0 floor


# ── T4: diff_enabled flag — load_config 3-tuple and build_ranked_diff bypass ─

def _make_raw_diff():
    return (
        "diff --git a/backend/app/routers/scanner.py b/backend/app/routers/scanner.py\n"
        "index aaa..bbb 100644\n"
        "--- a/backend/app/routers/scanner.py\n"
        "+++ b/backend/app/routers/scanner.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import os\n"
        "+import sys\n"
        " def foo(): pass\n"
        " def bar(): pass\n"
    )


def test_load_config_returns_enabled_true_by_default():
    """load_config() must return diff_enabled=True when key is absent."""
    token_cap, score_floor, diff_enabled = dr.load_config("/nonexistent/path.yaml")
    assert diff_enabled is True


def test_load_config_reads_enabled_false(tmp_path):
    """load_config() must return diff_enabled=False when config sets it false."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("token_optimization:\n  diff:\n    enabled: false\n    max_review_tokens: 6000\n")
    token_cap, score_floor, diff_enabled = dr.load_config(str(cfg))
    assert diff_enabled is False


def test_build_ranked_diff_bypasses_when_disabled():
    """When diff_enabled=False, build_ranked_diff returns the raw diff unchanged."""
    raw = _make_raw_diff()
    ranked, info = dr.build_ranked_diff(
        diff_text=raw,
        token_cap=6000,
        hotspot_paths=set(),
        hotspot_scores={},
        spec_names=set(),
        score_floor=5.0,
        diff_enabled=False,
    )
    assert ranked == raw
    assert info.get("diff_enabled") is False


def test_build_ranked_diff_includes_raw_diff_tokens():
    """ranking_info must include raw_diff_tokens (baseline for savings computation)."""
    raw = _make_raw_diff()
    _, info = dr.build_ranked_diff(
        diff_text=raw,
        token_cap=6000,
        hotspot_paths=set(),
        hotspot_scores={},
        spec_names=set(),
        score_floor=5.0,
    )
    assert "raw_diff_tokens" in info
    assert info["raw_diff_tokens"] >= 0


# ── T5: TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS env override ───────────────

def test_load_config_env_override_token_cap(tmp_path, monkeypatch):
    """TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS env var overrides config token_cap."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("token_optimization:\n  diff:\n    max_review_tokens: 6000\n")
    monkeypatch.setenv("TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS", "500")
    token_cap, _, _ = dr.load_config(str(cfg))
    assert token_cap == 500


def test_load_config_env_override_takes_precedence_over_config(tmp_path, monkeypatch):
    """Env override wins even when config has a higher value."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("token_optimization:\n  diff:\n    max_review_tokens: 9000\n")
    monkeypatch.setenv("TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS", "100")
    token_cap, _, _ = dr.load_config(str(cfg))
    assert token_cap == 100


def test_load_config_no_env_override_uses_config(tmp_path, monkeypatch):
    """When env var is unset, config value is returned (no override)."""
    monkeypatch.delenv("TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("token_optimization:\n  diff:\n    max_review_tokens: 7500\n")
    token_cap, _, _ = dr.load_config(str(cfg))
    assert token_cap == 7500


def test_critical_tier_files_cap_immune(monkeypatch):
    """Critical-tier files (auth, migration, dark-factory) bypass even an env-overridden cap."""
    monkeypatch.setenv("TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS", "1")
    diff = make_diff("backend/app/routers/auth/router.py", added=200, removed=50)
    output, ranking = run_main(diff, token_cap=1)
    auth_entry = next(f for f in ranking["files"] if "auth" in f["path"])
    # Critical files are always emitted in full, bypassing the token cap
    assert auth_entry["included"] == "full"
    assert auth_entry["risk_class"] == "critical"


def test_safety_signal_skill_security_path():
    assert dr._safety_signal(".claude/settings.json") == "skill_security_path"
    assert dr._safety_signal(".claude/settings.local.json") == "skill_security_path"
    assert dr._safety_signal(".claude/skills/code-review/scripts/foo.py") == "skill_security_path"
    assert dr._safety_signal(".claude/skills/code-review/SKILL.md") == "skill_security_path"
    assert dr._safety_signal(".factory/hooks/validate") == "skill_security_path"


def test_safety_signal_dark_factory_path_unaffected():
    """.factory/hooks/ must not collide with the dark-factory/ factory_path signal."""
    assert dr._safety_signal("dark-factory/scheduler.sh") == "factory_path"


def test_classify_file_skill_security_is_critical_tier():
    tier, signals, _ = dr.classify_file(".claude/settings.json", set(), set(), 5.0, total_lines=10)
    assert tier == "critical"
    assert "skill_security_path" in signals


# ---------------------------------------------------------------------------
# R5: zero-content predicate (--check-nonempty)
# ---------------------------------------------------------------------------

def test_has_reviewable_content_true_for_payload_line():
    text = "# [diff-rank: 1 files — 0 critical / 1 high / 0 medium / 0 low, est. 5 tokens (cap 6000)]\n+added line\n"
    assert dr.has_reviewable_content(text) is True


def test_has_reviewable_content_false_for_all_summarized():
    text = (
        "# [diff-rank: 1 files — 0 critical / 0 high / 0 medium / 1 low, est. 0 tokens (cap 6000)]\n"
        "# [SUMMARIZED: low-risk] a.py — +1/-1 (1 hunks)\n"
    )
    assert dr.has_reviewable_content(text) is False


def test_has_reviewable_content_ignores_file_headers():
    text = "--- a/x.py\n+++ b/x.py\n"
    assert dr.has_reviewable_content(text) is False


def _run_check_nonempty(path):
    """Run `diff_rank.py --check-nonempty PATH` in-process (same sys.argv patching
    as run_main()); return the SystemExit code main() raised."""
    with patch("sys.argv", ["diff_rank.py", "--check-nonempty", str(path)]):
        with pytest.raises(SystemExit) as exc:
            dr.main()
    return exc.value.code


def test_check_nonempty_all_summarized_exits_1(tmp_path):
    review = tmp_path / "review_diff.txt"
    review.write_text(
        "# [diff-rank: 1 files — 0 critical / 0 high / 0 medium / 1 low, est. 0 tokens (cap 6000)]\n"
        "# [SUMMARIZED: low-risk] tests/test_x.py — +5/-2 (1 hunks)\n"
    )
    assert _run_check_nonempty(review) == 1


def test_check_nonempty_with_payload_line_exits_0(tmp_path):
    review = tmp_path / "review_diff.txt"
    review.write_text(
        "# [diff-rank: 1 files — 0 critical / 1 high / 0 medium / 0 low, est. 5 tokens (cap 6000)]\n"
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    assert _run_check_nonempty(review) == 0


def test_check_nonempty_missing_file_exits_ge_2(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.txt"
    code = _run_check_nonempty(missing)
    assert code >= 2
    # The helper-error path must announce itself on stderr (the command captures it into
    # the abort comment). This assertion is also what makes the test red before Task 3
    # lands: argparse's own missing/unrecognized-argument exit is already 2.
    assert "diff_rank --check-nonempty error" in capsys.readouterr().err

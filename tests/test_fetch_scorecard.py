"""Tests for scripts/fetch_scorecard.py (pure functions only).

No subprocess / gh / git is exercised here — the subprocess wrappers
(_gh/_git/fetch_prs/compute_churn) are validated by live runs, not units.

Hermetic-env note: fetch_scorecard.FACTORY_EMAIL / REPO resolve from env at
import time. Tests that build commit dicts monkeypatch fetch_scorecard.FACTORY_EMAIL
to a known value rather than depending on ambient env.
"""
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# In-process import matching the diff_rank test's self-contained pattern.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_scorecard as fs  # noqa: E402

FACTORY = "factory@testrepo"
FOREIGN = "human@example.com"


def _factory_commit():
    return {"authors": [{"email": FACTORY}]}


def _foreign_commit():
    return {"authors": [{"email": FOREIGN}]}


# ── 1. factory fingerprint ────────────────────────────────────────────────────
def test_is_factory_commit_and_pr(monkeypatch):
    monkeypatch.setattr(fs, "FACTORY_EMAIL", FACTORY)
    assert fs.is_factory_commit(_factory_commit()) is True
    assert fs.is_factory_commit(_foreign_commit()) is False
    assert fs.is_factory_pr({"commits": [_foreign_commit(), _factory_commit()]}) is True
    assert fs.is_factory_pr({"commits": [_foreign_commit()]}) is False
    assert fs.is_factory_pr({"commits": []}) is False


# ── 2. triad classification ───────────────────────────────────────────────────
def test_classify_pr_states(monkeypatch):
    monkeypatch.setattr(fs, "FACTORY_EMAIL", FACTORY)
    assert fs.classify_pr({"state": "OPEN", "commits": [_factory_commit()]}) == "open"
    assert fs.classify_pr({"state": "CLOSED", "commits": [_factory_commit()]}) == "closed"
    # MERGED with only factory commits → clean
    assert (
        fs.classify_pr({"state": "MERGED", "commits": [_factory_commit()], "labels": []})
        == "merged_clean"
    )
    # MERGED with a non-factory (human) commit → merged_with_edits
    assert (
        fs.classify_pr(
            {"state": "MERGED", "commits": [_factory_commit(), _foreign_commit()], "labels": []}
        )
        == "merged_with_edits"
    )
    # MERGED clean but carrying the merged-with-edits label → override
    assert (
        fs.classify_pr(
            {
                "state": "MERGED",
                "commits": [_factory_commit()],
                "labels": [{"name": fs.EDITS_LABEL}],
            }
        )
        == "merged_with_edits"
    )


# ── 3. linked issue number ────────────────────────────────────────────────────
def test_linked_issue_number():
    assert fs.linked_issue_number("feat/issue-42-foo") == 42
    assert fs.linked_issue_number("chore/no-issue-here") is None
    assert fs.linked_issue_number(None) is None


# ── 4. numstat parsing ────────────────────────────────────────────────────────
def test_parse_numstat_skips_binary_and_zero():
    output = (
        "10\t2\tfile_a.py\n"
        "-\t-\tbinary.png\n"      # binary → skipped
        "0\t5\tfile_b.py\n"       # zero adds → skipped
        "3\t1\tfile_c.py\n"
        "garbage line without tabs\n"  # malformed → skipped
    )
    assert fs.parse_numstat(output) == {"file_a.py": 10, "file_c.py": 3}


# ── 5. blame survival counting ────────────────────────────────────────────────
def test_count_surviving_lines():
    sha = "abc123"
    blame = (
        "abc123 1 1 3\n"     # header for sha
        "\tcontent one\n"     # tab-prefixed content — ignored
        "abc123 2 2\n"        # header for sha
        "\tcontent two\n"
        "def456 3 3\n"        # header for a different sha
        "\tother content\n"
    )
    assert fs.count_surviving_lines(blame, sha) == 2
    assert fs.count_surviving_lines(blame, "def456") == 1
    assert fs.count_surviving_lines(blame, "zzz999") == 0


# ── 6. regression counting ────────────────────────────────────────────────────
def test_count_regressions_window_and_label():
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    items = [
        {"labels": [{"name": fs.REGRESSION_LABEL}], "createdAt": "2026-06-10T00:00:00Z"},  # in
        {"labels": [{"name": fs.REGRESSION_LABEL}], "createdAt": "2026-05-10T00:00:00Z"},  # out
        {"labels": [{"name": "bug"}], "createdAt": "2026-06-10T00:00:00Z"},                # no label
    ]
    assert fs.count_regressions(items, since, until) == 1


# ── 7. scorecard assembly ─────────────────────────────────────────────────────
def test_build_scorecard(monkeypatch):
    monkeypatch.setattr(fs, "FACTORY_EMAIL", FACTORY)
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

    prs = [
        {  # #10 merged clean, S
            "number": 10, "title": "add scanner", "headRefName": "feat/issue-1-foo",
            "state": "MERGED", "createdAt": "2026-06-05T10:00:00Z",
            "mergedAt": "2026-06-06T10:00:00Z", "labels": [],
            "commits": [_factory_commit()],
        },
        {  # #12 merged with edits, M
            "number": 12, "title": "fix migration", "headRefName": "feat/issue-2-bar",
            "state": "MERGED", "createdAt": "2026-06-07T10:00:00Z",
            "mergedAt": "2026-06-08T10:00:00Z", "labels": [],
            "commits": [_factory_commit(), _foreign_commit()],
        },
        {  # #11 closed, M
            "number": 11, "title": "refactor thing", "headRefName": "feat/issue-3-baz",
            "state": "CLOSED", "createdAt": "2026-06-09T10:00:00Z",
            "mergedAt": None, "labels": [],
            "commits": [_factory_commit()],
        },
        {  # #13 open, unknown size (no issue size mapping)
            "number": 13, "title": "wip perf", "headRefName": "feat/issue-4-qux",
            "state": "OPEN", "createdAt": "2026-06-11T10:00:00Z",
            "mergedAt": None, "labels": [],
            "commits": [_factory_commit()],
        },
        {  # #99 non-factory PR → excluded entirely
            "number": 99, "title": "external contrib", "headRefName": "feat/issue-5-ext",
            "state": "MERGED", "createdAt": "2026-06-12T10:00:00Z",
            "mergedAt": "2026-06-13T10:00:00Z", "labels": [],
            "commits": [_foreign_commit()],
        },
    ]
    issue_sizes = {1: "S", 2: "M", 3: "M"}  # 4 → unknown

    sc = fs.build_scorecard(prs, issue_sizes, regression_count=2, churn={}, since=since, until=until)

    assert sc["triad"]["merged_clean"] == 1
    assert sc["triad"]["merged_with_edits"] == 1
    assert sc["triad"]["closed"] == 1
    assert sc["triad"]["open"] == 1
    # merged=2, resolved=3 → 66.7
    assert sc["triad"]["merge_rate_pct"] == 66.7

    assert sc["by_size"]["S"]["merged_clean"] == 1
    assert sc["by_size"]["M"]["merged_with_edits"] == 1
    assert sc["by_size"]["M"]["closed"] == 1
    assert sc["by_size"]["unknown"]["open"] == 1
    assert "unknown" in sc["by_size"]

    # 2 factory PRs merged in window (#10, #12); regression_count=2 → 100.0
    assert sc["rework"]["merged_factory_prs"] == 2
    assert sc["rework"]["rework_rate_pct"] == 100.0

    # pr_rows sorted by number; #99 excluded (non-factory)
    assert [r["number"] for r in sc["prs"]] == [10, 11, 12, 13]


# ── 8. env-driven repo/email resolution (import-time) ─────────────────────────
def test_env_override_repo_and_email(monkeypatch):
    monkeypatch.setenv("FACTORY_REPO_SLUG", "o/r2")
    monkeypatch.delenv("FACTORY_EMAIL", raising=False)
    monkeypatch.delenv("FACTORY_REPO", raising=False)
    importlib.reload(fs)
    try:
        assert fs.REPO == "o/r2"
        assert fs.FACTORY_EMAIL == "factory@r2"
        assert fs._OWNER_REPO == "o/r2"
    finally:
        # Restore the module to its clean (ambient-env) import state so other
        # tests and other files see the parity defaults again.
        monkeypatch.undo()
        importlib.reload(fs)

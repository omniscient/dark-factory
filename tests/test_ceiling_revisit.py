"""Tests for scripts/ceiling_revisit.py (pure functions + report generation).

No subprocess / gh / git is exercised — the fetch step (which shells out to
fetch_scorecard.py) is only reached in main() and is not unit-tested here.
"""
import importlib
import json
import sys
from pathlib import Path

# In-process import matching the diff_rank test's self-contained pattern.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ceiling_revisit as cr  # noqa: E402


def _triad(clean=0, edits=0, closed=0, open_=0):
    return {"merged_clean": clean, "merged_with_edits": edits, "closed": closed, "open": open_}


# ── 1. success_rate ───────────────────────────────────────────────────────────
def test_success_rate():
    # (2 + 1) / (2 + 1 + 1) = 0.75; open PRs excluded from denominator
    assert cr.success_rate(_triad(clean=2, edits=1, closed=1, open_=3)) == 0.75
    # decided == 0 → None (open-only)
    assert cr.success_rate(_triad(open_=5)) is None


# ── 2. classify_keyword ───────────────────────────────────────────────────────
def test_classify_keyword():
    # n < 5 → insufficient
    assert cr.classify_keyword(3, 0.5, 0.6) == "insufficient data — no change"
    # rate is None → insufficient
    assert cr.classify_keyword(10, None, 0.6) == "insufficient data — no change"
    # rate >= baseline → remove (no longer discriminative)
    assert cr.classify_keyword(10, 0.7, 0.6) == "remove"
    assert cr.classify_keyword(10, 0.6, 0.6) == "remove"
    # rate < baseline - 0.15 → keep
    assert cr.classify_keyword(10, 0.40, 0.6) == "keep"
    # between (baseline-0.15 <= rate < baseline) → ambiguous
    assert cr.classify_keyword(10, 0.50, 0.6) == "ambiguous — leave unchanged"


# ── 3. build_bucket_table ─────────────────────────────────────────────────────
def test_build_bucket_table_merges_l_and_xl():
    by_size = {
        "S": _triad(clean=3, edits=1, closed=1, open_=0),   # decided 5, rate 0.8
        "M": _triad(clean=2, edits=0, closed=2, open_=1),   # decided 4, rate 0.5
        "L": _triad(clean=1, edits=0, closed=1, open_=0),
        "XL": _triad(clean=1, edits=1, closed=0, open_=1),
    }
    table = cr.build_bucket_table(by_size)

    assert table["S"]["n"] == 5
    assert table["S"]["rate"] == 0.8
    assert table["M"]["n"] == 4
    assert table["M"]["rate"] == 0.5

    # L and XL are merged into a single "L+XL" bucket
    assert "L" not in table
    assert "XL" not in table
    assert "L+XL" in table
    # merged: clean 2, edits 1, closed 1 → decided 4, rate 3/4 = 0.75
    assert table["L+XL"]["merged_clean"] == 2
    assert table["L+XL"]["merged_with_edits"] == 1
    assert table["L+XL"]["closed"] == 1
    assert table["L+XL"]["n"] == 4
    assert table["L+XL"]["rate"] == 0.75


# ── 4. find_new_keyword_candidates ────────────────────────────────────────────
def test_find_new_keyword_candidates():
    # 5 closed M-PRs whose titles all share the token "webhook" (not covered by
    # existing keywords) and "migration" (already covered → must be excluded).
    prs = [
        {"size": "M", "classification": "closed", "title": f"add webhook migration {i}"}
        for i in range(5)
    ]
    candidates = cr.find_new_keyword_candidates(prs, "migration|refactor", m_baseline=0.5)
    kws = {c["keyword"]: c for c in candidates}

    assert "webhook" in kws
    assert kws["webhook"]["n"] == 5
    assert kws["webhook"]["decision"] == "add candidate"
    # already matched by the existing "migration" keyword → excluded
    assert "migration" not in kws


def test_find_new_keyword_candidates_below_threshold():
    # Only 4 occurrences (< 5) → no candidate surfaced.
    prs = [
        {"size": "M", "classification": "closed", "title": f"add webhook thing {i}"}
        for i in range(4)
    ]
    assert cr.find_new_keyword_candidates(prs, "migration", m_baseline=0.5) == []


# ── 5. generate_report ────────────────────────────────────────────────────────
def _write_scorecard(tmp_path):
    """Scorecard where 'refactor' M-cohort success (1.0) >= M baseline (0.5),
    and L+XL success (5/6 ≈ 0.833, n=6) triggers the L-bucket issue."""
    scorecard = {
        "by_size": {
            "M": _triad(clean=5, closed=5),   # decided 10, rate 0.5 → M baseline
            "L": _triad(clean=4, closed=1),
            "XL": _triad(clean=1),
        },
        "prs": [
            {"number": i, "size": "M", "classification": "merged_clean",
             "title": f"refactor module {i}"}
            for i in range(1, 6)   # 5 merged M PRs matching 'refactor'
        ],
    }
    path = tmp_path / "scorecard.json"
    path.write_text(json.dumps(scorecard), encoding="utf-8")
    return path


def test_generate_report(tmp_path):
    path = _write_scorecard(tmp_path)
    report, keywords_to_remove, new_candidates, l_needs_issue = cr.generate_report(
        "2026-06-01", "2026-06-30", str(path), keywords="refactor"
    )

    # 'refactor' cohort: 5 decided, rate 1.0 >= baseline 0.5 → remove
    assert keywords_to_remove == ["refactor"]
    assert "Keywords recommended for removal" in report
    assert "remove" in report

    # L+XL rate 5/6 > 0.70 and n=6 >= 5 → issue warranted
    assert l_needs_issue is True

    # Bucket table rendered; keeps 'scheduler.sh' (not prefixed with dark-factory/)
    assert "### Per-Bucket Triad" in report
    assert "| L+XL |" in report
    assert "`scheduler.sh`" in report
    assert "dark-factory/scheduler.sh" not in report

    # No closed M PRs → no add-candidates
    assert new_candidates == []

    # Footer carries the (default) product name and the fixed title.
    assert "Weekly Ceiling Revisit" in report


def test_generate_report_footer_uses_product_name(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_PRODUCT_NAME", "AcmeCorp")
    importlib.reload(cr)
    try:
        path = _write_scorecard(tmp_path)
        report, *_ = cr.generate_report(
            "2026-06-01", "2026-06-30", str(path), keywords="refactor"
        )
        assert "*Posted by AcmeCorp Weekly Ceiling Revisit*" in report
    finally:
        monkeypatch.undo()
        importlib.reload(cr)

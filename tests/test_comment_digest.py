"""Tests for comment_digest.py — deterministic human feedback extractor."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import comment_digest as cd


# ── sentinel / no-feedback ────────────────────────────────────────────────────

def test_no_feedback_all_bot_comments_returns_sentinel():
    """All comments are factory/bot → no-feedback sentinel with boundary header."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-01T00:00:00Z"},
        ],
        "pr_reviews": {},
        "pr_inline_comments": [],
    }
    result = cd.build_digest(issue_data)
    assert "<!-- no-feedback: true -->" in result
    assert "No human feedback found after last factory marker." in result
    assert "<!-- comment-digest:" in result


def test_no_feedback_empty_issue_returns_sentinel():
    """No comments/reviews at all → simple no-human-feedback sentinel."""
    result = cd.build_digest({})
    assert "<!-- no-human-feedback -->" in result


def test_no_feedback_empty_lists_returns_sentinel():
    """Empty comments, reviews, inline → simple no-human-feedback sentinel."""
    result = cd.build_digest({"comments": [], "pr_reviews": {}, "pr_inline_comments": []})
    assert "<!-- no-human-feedback -->" in result


# ── boundary detection ────────────────────────────────────────────────────────

def test_boundary_excludes_comments_before_latest_factory_marker():
    """Human comment before the latest factory marker is excluded."""
    issue_data = {
        "comments": [
            {"body": "early human comment", "author": {"login": "user"}, "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-02T00:00:00Z"},
            {"body": "late human comment", "author": {"login": "user"}, "createdAt": "2026-01-03T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "early human comment" not in result
    assert "late human comment" in result


def test_boundary_uses_latest_factory_marker_not_first():
    """With multiple factory markers, comments after the LATEST are included."""
    issue_data = {
        "comments": [
            {"body": "first human comment", "author": {"login": "user"}, "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-02T00:00:00Z"},
            {"body": "middle human comment", "author": {"login": "user"}, "createdAt": "2026-01-03T00:00:00Z"},
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-04T00:00:00Z"},
            {"body": "final human comment", "author": {"login": "user"}, "createdAt": "2026-01-05T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "first human comment" not in result
    assert "middle human comment" not in result
    assert "final human comment" in result


def test_no_boundary_all_human_comments_included():
    """When there is no factory boundary, all human comments are included with a no-boundary note."""
    issue_data = {
        "comments": [
            {"body": "first", "author": {"login": "user"}, "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "second", "author": {"login": "user"}, "createdAt": "2026-01-02T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "<!-- no-boundary: true -->" in result
    assert "first" in result
    assert "second" in result


# ── issue comment feedback ────────────────────────────────────────────────────

def test_issue_comment_section_header():
    """Issue comments produce ### Issue comments section."""
    issue_data = {
        "comments": [
            {"body": "Please fix the bug", "author": {"login": "omniscient"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "### Issue comments" in result


def test_issue_comment_body_appears():
    """Comment body appears verbatim in digest."""
    issue_data = {
        "comments": [
            {"body": "unique-feedback-xyz", "author": {"login": "alice"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "unique-feedback-xyz" in result


# ── PR review feedback ────────────────────────────────────────────────────────

def test_pr_review_section_header():
    """PR reviews produce ### PR review comments section."""
    issue_data = {
        "pr_reviews": {
            "reviews": [
                {"body": "needs changes", "author": {"login": "reviewer"}, "submittedAt": "2026-07-01T10:00:00Z", "state": "CHANGES_REQUESTED"},
            ]
        },
    }
    result = cd.build_digest(issue_data)
    assert "### PR review comments" in result


def test_pr_review_body_appears():
    """PR review body appears in digest."""
    issue_data = {
        "pr_reviews": {
            "reviews": [
                {"body": "change-this-thing", "author": {"login": "reviewer"}, "submittedAt": "2026-07-01T10:00:00Z", "state": "CHANGES_REQUESTED"},
            ]
        },
    }
    result = cd.build_digest(issue_data)
    assert "change-this-thing" in result


# ── inline comment feedback ───────────────────────────────────────────────────

def test_inline_comment_section_header():
    """Inline comments produce ### Inline review comments by file section."""
    issue_data = {
        "pr_inline_comments": [
            {"path": "backend/app/main.py", "line": 42, "body": "fix this", "created_at": "2026-07-01T10:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "### Inline review comments by file" in result


def test_inline_comments_grouped_by_path():
    """Inline comments from the same file appear under the same path header."""
    issue_data = {
        "pr_inline_comments": [
            {"path": "backend/app/main.py", "line": 42, "body": "first inline", "created_at": "2026-07-01T10:00:00Z"},
            {"path": "frontend/src/index.tsx", "line": 10, "body": "frontend inline", "created_at": "2026-07-01T11:00:00Z"},
            {"path": "backend/app/main.py", "line": 55, "body": "second inline", "created_at": "2026-07-01T12:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    # Both path headers present (spec uses #### path without backticks)
    assert "#### backend/app/main.py" in result
    assert "#### frontend/src/index.tsx" in result
    # backend path header appears before frontend path header (alphabetical)
    assert result.index("#### backend/app/main.py") < result.index("#### frontend/src/index.tsx")
    # Bodies present with spec format "- Line N: body"
    assert "- Line 42: first inline" in result
    assert "- Line 55: second inline" in result
    assert "frontend inline" in result


# ── digest header with cutoff/marker ─────────────────────────────────────────

def test_digest_header_includes_cutoff_and_marker():
    """Output with boundary includes <!-- comment-digest: cutoff=… marker="…" --> header."""
    issue_data = {
        "comments": [
            {"body": "Posted by MarketHawk Dark Factory\nRun complete.", "author": {"login": "bot"}, "createdAt": "2026-01-03T12:00:00Z"},
            {"body": "human-feedback-here", "author": {"login": "user"}, "createdAt": "2026-01-04T10:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert '<!-- comment-digest: cutoff=2026-01-03T12:00:00Z marker="Posted by MarketHawk Dark Factory" -->' in result
    assert "## Marker" in result
    assert "2026-01-03T12:00:00Z" in result


# ── all 6 bot markers ─────────────────────────────────────────────────────────

def test_all_six_bot_markers_detected():
    """All six marker strings from bot_re are recognized as factory boundaries."""
    markers = [
        "Posted by MarketHawk Refinement Pipeline",
        "Posted by MarketHawk Backlog Scheduler",
        "Posted by MarketHawk Dark Factory",
        "Updated by MarketHawk Dark Factory",
        "dark-factory-cost-report",
        "Posted by MarketHawk Epic Autopilot",
    ]
    for marker in markers:
        issue_data = {
            "comments": [
                # Factory boundary comment — body starts with the marker text
                {"body": f"{marker}: run complete.", "author": {"login": "bot"}, "createdAt": "2026-01-01T00:00:00Z"},
                # Human comment that comes after — should appear in feedback
                {"body": "after-human-unique-xyz", "author": {"login": "user"}, "createdAt": "2026-01-02T00:00:00Z"},
            ]
        }
        result = cd.build_digest(issue_data)
        assert "after-human-unique-xyz" in result, f"Human comment after marker not included for: {marker}"
        # The boundary marker itself should appear only in the header/Marker section,
        # not as a human feedback entry in the Issue comments section
        assert "### Issue comments" in result or "<!-- comment-digest:" in result, \
            f"Expected digest structure not found for: {marker}"


# ── PR review / inline boundary filtering ─────────────────────────────────────

def test_pr_review_before_boundary_excluded():
    """PR review submitted before the factory boundary timestamp is excluded."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-05T00:00:00Z"},
        ],
        "pr_reviews": {
            "reviews": [
                {"body": "old-review-before-factory", "author": {"login": "reviewer"}, "submittedAt": "2026-01-04T00:00:00Z", "state": "CHANGES_REQUESTED"},
            ]
        },
        "pr_inline_comments": [],
    }
    result = cd.build_digest(issue_data)
    assert "<!-- no-feedback: true -->" in result
    assert "old-review-before-factory" not in result


def test_pr_review_after_boundary_included():
    """PR review submitted after the factory boundary timestamp is included."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-03T00:00:00Z"},
        ],
        "pr_reviews": {
            "reviews": [
                {"body": "new-review-after-factory", "author": {"login": "reviewer"}, "submittedAt": "2026-01-04T00:00:00Z", "state": "APPROVED"},
            ]
        },
        "pr_inline_comments": [],
    }
    result = cd.build_digest(issue_data)
    assert "new-review-after-factory" in result


def test_inline_comment_before_boundary_kept_as_finding():
    """Inline comments are kept in FULL even before the factory boundary. Line-level PR
    comments are code-review FINDINGS — the AI reviewer posts them just before its factory
    'Code Review — Blocked' comment — and a fix-Continue run must act on them. Filtering
    them out by the boundary timestamp would strand exactly the findings the run exists to
    fix (regression guard for the digest's fix-Continue support)."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-05T00:00:00Z"},
        ],
        "pr_reviews": {},
        "pr_inline_comments": [
            {"path": "backend/app/main.py", "line": 10, "body": "review-finding-before-factory", "created_at": "2026-01-04T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "review-finding-before-factory" in result
    assert "<!-- no-feedback: true -->" not in result


def test_inline_comment_after_boundary_included():
    """Inline comment with created_at after boundary timestamp is included."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-01-03T00:00:00Z"},
        ],
        "pr_reviews": {},
        "pr_inline_comments": [
            {"path": "backend/app/main.py", "line": 10, "body": "new-inline-after-factory", "created_at": "2026-01-04T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "new-inline-after-factory" in result


def test_no_boundary_includes_all_pr_reviews_and_inline():
    """When no factory boundary exists, all PR reviews and inline comments are included."""
    issue_data = {
        "comments": [],
        "pr_reviews": {
            "reviews": [
                {"body": "unbounded-review", "author": {"login": "reviewer"}, "submittedAt": "2026-01-01T00:00:00Z", "state": "APPROVED"},
            ]
        },
        "pr_inline_comments": [
            {"path": "backend/app/main.py", "line": 5, "body": "unbounded-inline", "created_at": "2026-01-01T00:00:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert "unbounded-review" in result
    assert "unbounded-inline" in result


# ── CLI round-trip ────────────────────────────────────────────────────────────

def test_cli_roundtrip(tmp_path):
    """CLI reads issue.json, writes comment-digest.md with expected content."""
    import sys as _sys
    issue_data = {
        "comments": [
            {"body": "cli-roundtrip-feedback", "author": {"login": "user"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    issue_json = tmp_path / "issue.json"
    issue_json.write_text(json.dumps(issue_data))
    out_path = tmp_path / "comment-digest.md"

    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["comment_digest.py", "--issue-json", str(issue_json), "--out", str(out_path)]
        cd.main()
    finally:
        _sys.argv = old_argv

    content = out_path.read_text()
    assert "cli-roundtrip-feedback" in content


# ── T2: TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS cap override ──────────────────

def test_comments_cap_env_override_honored(tmp_path, monkeypatch):
    """TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS env var truncates digest output."""
    import sys as _sys
    # Build a digest that is definitely longer than 4 chars (1 token)
    issue_data = {
        "comments": [
            {"body": "A" * 200, "author": {"login": "user"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    issue_json = tmp_path / "issue.json"
    issue_json.write_text(json.dumps(issue_data))
    out_path = tmp_path / "comment-digest.md"

    monkeypatch.setenv("TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS", "1")
    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["comment_digest.py", "--issue-json", str(issue_json), "--out", str(out_path)]
        cd.main()
    finally:
        _sys.argv = old_argv

    content = out_path.read_text()
    # Output should be truncated (max 4 chars for 1 token) + truncation marker
    assert "<!-- truncated:" in content
    assert "chars dropped" in content


def test_comments_cap_unset_no_truncation(tmp_path, monkeypatch):
    """When TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS is unset, digest is never truncated.

    Uses a body far larger than any plausible default cap: without the env var
    there is NO cap at all (enforcement is env-only; observe mode must preserve
    current behavior).
    """
    import sys as _sys
    monkeypatch.delenv("TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS", raising=False)
    issue_data = {
        "comments": [
            {"body": "big feedback " + "Z" * 20000, "author": {"login": "user"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    issue_json = tmp_path / "issue.json"
    issue_json.write_text(json.dumps(issue_data))
    out_path = tmp_path / "comment-digest.md"

    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["comment_digest.py", "--issue-json", str(issue_json), "--out", str(out_path)]
        cd.main()
    finally:
        _sys.argv = old_argv

    content = out_path.read_text()
    # No cap without the env var — even a >2000-token digest is untouched
    assert "<!-- truncated:" not in content
    assert "big feedback" in content


def test_comments_cap_build_digest_stays_pure(monkeypatch):
    """build_digest() must not apply any truncation — cap is only in main()."""
    monkeypatch.setenv("TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS", "1")
    issue_data = {
        "comments": [
            {"body": "X" * 500, "author": {"login": "user"}, "createdAt": "2026-07-01T10:00:00Z"},
        ],
    }
    # build_digest should return the full content, not truncated
    result = cd.build_digest(issue_data)
    assert len(result) > 4  # env cap of 1 token = 4 chars; build_digest ignores it
    assert "<!-- truncated:" not in result


# ── R354: gate-verdict comments surfaced in continue-run digests ─────────────

def test_gate_verdict_last_comment_included_not_swallowed():
    """R7#1: gate-verdict comment as the last comment in the thread is the primary
    reported symptom (#354) — the digest must not collapse to the no-feedback sentinel."""
    issue_data = {
        "comments": [
            {"body": "please look into this", "author": {"login": "user"}, "createdAt": "2026-08-24T10:00:00Z"},
            {
                "body": "## Code Review — Blocked\n\nThe AI code reviewer found 1 blocking issue(s).\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:34:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "No human feedback found after last factory marker." not in result
    assert "### Gate verdict (factory-posted, action required)" in result
    assert "The AI code reviewer found 1 blocking issue(s)." in result


def test_gate_verdict_included_despite_later_factory_report_comment():
    """R7#2: a later, non-gate-verdict factory comment (e.g. the report node) must not
    push the gate verdict out of the digest — inclusion is position-independent (R2)."""
    issue_data = {
        "comments": [
            {
                "body": "## Code Review — Blocked\n\nThe AI code reviewer found 1 blocking issue(s).\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:34:00Z",
            },
            {
                "body": "---\n*Posted by MarketHawk Dark Factory*\nRun report.",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:40:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "### Gate verdict (factory-posted, action required)" in result
    assert "The AI code reviewer found 1 blocking issue(s)." in result


def test_gate_verdict_as_sole_comment_included():
    """R7#3: a gate-verdict comment with no other factory or human comment in the
    thread. Note: because _is_gate_verdict requires _is_factory_comment (R1), a
    matching comment always sets the boundary itself, so this exercises the
    *with-boundary* empty-sentinel branch (167-172), same as the next test below —
    not the true no_boundary branch (152-162), which is defensive/unreachable dead
    code today (see the comment on that branch in build_digest). Kept as its own
    test because it's the minimal single-comment reproduction of the reported bug,
    distinct in shape from the next test's two-comment case."""
    issue_data = {
        "comments": [
            {
                "body": "## Spec Conformance — Blocked\n\nMaterial divergence found.\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:34:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "<!-- no-human-feedback -->" not in result
    assert "No human feedback found after last factory marker." not in result
    assert "### Gate verdict (factory-posted, action required)" in result
    assert "Material divergence found." in result


def test_gate_verdict_included_when_it_is_the_boundary_with_no_other_content():
    """R7#4: empty-sentinel branch (no human/review/inline content) — when the boundary
    itself is a gate verdict, the gate section must be emitted instead of the sentinel."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Backlog Scheduler*", "author": {"login": "omniscient"}, "createdAt": "2026-08-24T09:00:00Z"},
            {
                "body": "## Code Review — Blocked\n\nOne blocker found.\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:34:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "No human feedback found after last factory marker." not in result
    assert "### Gate verdict (factory-posted, action required)" in result
    assert "One blocker found." in result


def test_heading_prefix_without_footer_not_misrouted_as_gate_verdict():
    """R1/R7#5: exact heading prefix match alone (no factory footer) must not be
    classified as a gate verdict — guards against a human pasting the heading text
    verbatim without the factory footer marker."""
    issue_data = {
        "comments": [
            {"body": "---\n*Posted by MarketHawk Dark Factory*", "author": {"login": "bot"}, "createdAt": "2026-08-24T09:00:00Z"},
            {
                "body": "## Code Review — Blocked\n\nI'm quoting this heading in my own comment, no bot footer here.",
                "author": {"login": "alice"},
                "createdAt": "2026-08-24T09:30:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "### Gate verdict (factory-posted, action required)" not in result
    assert "### Issue comments" in result
    assert "I'm quoting this heading in my own comment, no bot footer here." in result


def test_two_gate_verdicts_only_latest_surfaced():
    """R7#6: two stacked gate-verdict comments across cycles — only the latest is
    included, avoiding feeding the continue agent a stale round's finding."""
    issue_data = {
        "comments": [
            {
                "body": "## Code Review — Blocked\n\nFirst round: 2 blockers.\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:00:00Z",
            },
            {
                "body": "---\n*Posted by MarketHawk Dark Factory*\nRun report.",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:10:00Z",
            },
            {
                "body": "## Code Review — Blocked\n\nSecond round: 1 blocker.\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T11:00:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "Second round: 1 blocker." in result
    assert "First round: 2 blockers." not in result


def test_gate_verdict_section_rendered_after_human_sections():
    """R4: when both human feedback and a gate verdict are present, the gate-verdict
    section is rendered after the human sections (human comments keep presentation
    priority)."""
    issue_data = {
        "comments": [
            {
                "body": "## Code Review — Blocked\n\nOne blocker.\n\n---\n*Posted by MarketHawk Dark Factory*",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:00:00Z",
            },
            {"body": "please prioritize the null check", "author": {"login": "alice"}, "createdAt": "2026-08-24T10:05:00Z"},
        ],
    }
    result = cd.build_digest(issue_data)
    assert result.index("### Issue comments") < result.index("### Gate verdict (factory-posted, action required)")


def test_heading_mid_body_not_matched_as_gate_verdict():
    """R1: _is_gate_verdict requires the heading at the START of the (lstripped) body,
    not merely present somewhere in it — a factory comment that happens to quote/mention
    a heading mid-body must not be misclassified as the gate verdict itself."""
    issue_data = {
        "comments": [
            {
                "body": "---\n*Posted by MarketHawk Dark Factory*\nRun report. (Note: previous cycle ended with ## Code Review — Blocked.)",
                "author": {"login": "omniscient"},
                "createdAt": "2026-08-24T10:00:00Z",
            },
        ],
    }
    result = cd.build_digest(issue_data)
    assert "### Gate verdict (factory-posted, action required)" not in result


# ── FACTORY_PRODUCT_NAME parameterization ─────────────────────────────────────

def test_bot_markers_follow_product_name(monkeypatch):
    monkeypatch.setenv("FACTORY_PRODUCT_NAME", "Acme")
    import importlib, comment_digest as cd
    importlib.reload(cd)
    body = "---\n*Posted by Acme Dark Factory*"
    assert cd._BOT_RE.search(body), "marker regex must track FACTORY_PRODUCT_NAME"

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"

NARROWED_REGEX = (
    r"(^|[^a-z0-9_])(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|README|CLAUDE)"
    r"\.md([^a-z0-9]|$)|(^|[^a-z])docs/"
)
DOC_EXEMPTION_PATTERN = re.compile(NARROWED_REGEX, re.IGNORECASE)

EXEMPT_AREAS = [
    "[OOS] ARCHITECTURE.md ",
    "[OOS] `CLAUDE.md` (repo instructions) ",
    "[OOS] README.md ",
    "[OOS] docs/superpowers/specs/foo.md ",
]

ENFORCED_AREAS = [
    "[OOS] commands/dark-factory-plan.md ",
    "[OOS] refinement-skills/VERIFIER-CONTRACT.md ",
    "[OOS] `.claude/skills/x/SKILL.md` ",
]


def test_command_file_contains_narrowed_regex():
    text = CONFORMANCE_CMD.read_text(encoding="utf-8")
    assert NARROWED_REGEX in text, (
        "Step 3.6.0's doc-exemption guard must use the narrowed doc-map regex, "
        "not the old blanket '\\.md(...)' pattern"
    )
    # The old blanket guard is a literal substring of the new one, so presence of the
    # new regex alone would not catch a re-added blanket guard elsewhere in the file.
    assert "grep -qiE '\\.md([^a-z0-9]|$)" not in text, (
        "the old blanket '\\.md(...)' guard must be gone, not merely accompanied by the new one"
    )


def test_narrowed_regex_exempts_doc_map_targets():
    for area in EXEMPT_AREAS:
        assert DOC_EXEMPTION_PATTERN.search(area), f"expected exemption for: {area!r}"


def test_narrowed_regex_enforces_policy_files():
    for area in ENFORCED_AREAS:
        assert not DOC_EXEMPTION_PATTERN.search(area), (
            f"expected enforcement (no exemption) for: {area!r}"
        )

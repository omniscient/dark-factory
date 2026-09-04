# Implementation Plan: Continue-run comment digest must surface factory-posted gate-verdict findings

**Issue:** omniscient/dark-factory#354
**Spec:** `docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md`
**Depends on:** none

---

## Goal

Fix `scripts/comment_digest.py` so that a Gate-3 ("## Code Review — Blocked") or Gate-2
("## Spec Conformance — Blocked") comment is never classified purely as factory noise. Today
the bot-noise filter (`_is_factory_comment`) treats it like a routine checkpoint comment; when
it's the newest comment in the thread it becomes the digest's own boundary marker, which
produces `"No human feedback found after last factory marker."` — collapsed by the
`summarize-feedback` DAG node into `{"summary": "No specific feedback found."}` — and a
`continue` run resumes blind. Add a new, position-independent `_is_gate_verdict` predicate that
always surfaces the latest gate-verdict comment in its own digest section, regardless of where
it falls relative to the existing boundary computation, which itself stays untouched.

Note: the `summarize-feedback` DAG node's own haiku prompt (unchanged — out of scope per R6)
may still emit a cosmetic `{"summary": "No specific feedback found."}` for the
`acknowledge-continue` issue comment even after this fix, since that node's prompt still
describes the digest as "human-authored feedback only." This is harmless: the implement
command's continue path reads `comment-digest.md` directly as its feedback source
(`commands/dark-factory-implement.md:61-68`), not the `summarize-feedback` summary, so the
substantive fix (the agent actually sees the gate finding) lands regardless of that cosmetic
text.

## Architecture

```
scripts/comment_digest.py
  _GATE_VERDICT_HEADINGS               (new — literal 2-entry heading tuple)
  _is_gate_verdict(body) -> bool       (new — heading prefix AND _is_factory_comment)
  _latest_gate_verdict(comments)       (new — scans full array, returns latest match or None)
  _gate_verdict_section(gate_verdict)  (new — renders the "### Gate verdict ..." section)
  build_digest(issue_data)             (modified — computes gate_verdict/gate_section once,
                                         appends gate_section in all three return paths;
                                         last_factory_idx/boundary_ts computation UNCHANGED)

tests/test_comment_digest.py
  8 new regression tests (R7 items 1-6 + R4 ordering + R1 mid-body-heading guard)
  1 new drift test: commands/*.md "— Blocked" headings ⊆ _GATE_VERDICT_HEADINGS
```

No changes to `workflows/archon-dark-factory.yaml` or `commands/dark-factory-implement.md`
(R6) — both are out of scope per the spec and the issue's own `critical_diff_paths` flag.

## Tech Stack

- Python stdlib only (`re`) — matches `comment_digest.py`'s existing zero-dependency style.
- `pytest`, run via `python -m pytest tests/ -v` — existing framework and convention
  (`tests/test_comment_digest.py` already has 24 tests in this exact style).

## File Structure

| File | Change |
|---|---|
| `scripts/comment_digest.py` | **Modified** — new predicate/finder/renderer + `build_digest` wiring |
| `tests/test_comment_digest.py` | **Modified** — 9 new tests (8 regression + 1 drift); 24 existing → 33 total |

Not touched: `workflows/archon-dark-factory.yaml`, `commands/dark-factory-implement.md`,
`commands/dark-factory-code-review.md`, `commands/dark-factory-conformance.md`,
`commands/dark-factory-plan.md` (read-only reference for the drift test).

---

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md`,
`docs/superpowers/plans/2026-09-04-continue-digest-gate-verdict-plan.md`

Per the `[PATTERN]` memory lesson (issue #42) — the same Task 0 that #381, #382, #384 and
#358's plans needed: the implement phase's `feat/issue-354-...` branch forks from `main`, so
this ticket's own spec and this plan file (both refine-branch-only, not on `main`) do **not**
transfer automatically. Without them, Gate 2 (conformance) falls back to `NO_SPEC=true`
advisory-only review. Copy both files onto the feat branch and commit them before starting
Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=354
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md \
  docs/superpowers/plans/2026-09-04-continue-digest-gate-verdict-plan.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-354-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md \
  docs/superpowers/plans/2026-09-04-continue-digest-gate-verdict-plan.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md && \
test -f docs/superpowers/plans/2026-09-04-continue-digest-gate-verdict-plan.md && echo OK
git add docs/superpowers/specs/2026-09-04-continue-digest-gate-verdict-design.md \
  docs/superpowers/plans/2026-09-04-continue-digest-gate-verdict-plan.md
git commit -m "docs(#354): copy spec/plan onto the implementation branch"
```

---

## Task 1: Gate-verdict predicate, finder, section renderer, and `build_digest` wiring

**Files:** `scripts/comment_digest.py`, `tests/test_comment_digest.py`

### TDD Steps

1. Insert these 8 tests into `tests/test_comment_digest.py` **immediately after**
   `test_comments_cap_build_digest_stays_pure` (ends at line 403) and **before** the
   `# ── FACTORY_PRODUCT_NAME parameterization ──` section header (line 406) — i.e. before
   `test_bot_markers_follow_product_name`, not after it. This ordering matters:
   `test_bot_markers_follow_product_name` (`tests/test_comment_digest.py:408-413`) does
   `monkeypatch.setenv("FACTORY_PRODUCT_NAME", "Acme"); importlib.reload(cd)`, which rebinds
   the module-level `cd._BOT_RE` to an "Acme"-only pattern. `monkeypatch` restores the env var
   on teardown but nothing reloads the module back, so `cd._BOT_RE` stays pointed at "Acme" for
   the rest of the pytest session. Every fixture below it that hardcodes
   `*Posted by MarketHawk Dark Factory*` (all of the new tests here do) would then fail
   `_is_factory_comment` and silently produce false negatives if placed after that test. Insert
   before it, not after, to avoid this entirely — do not touch
   `test_bot_markers_follow_product_name` itself:

```python
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
```

2. Verify the new tests fail (the predicate/section don't exist yet — expect `AttributeError`
   or assertion failures):

```bash
cd /workspace/dark-factory
python -m pytest tests/test_comment_digest.py -v -k "gate_verdict or heading_prefix or heading_mid_body"
```
Expected: 6 failed, 2 passed. The 6 failures are the tests asserting `"### Gate verdict..."`
**is** present — that section doesn't exist yet. The 2 passes
(`test_heading_prefix_without_footer_not_misrouted_as_gate_verdict` and
`test_heading_mid_body_not_matched_as_gate_verdict`) assert its **absence**, which is already
true against the unpatched code by construction — they pass now and must stay passing after
the fix, so they aren't a sign of a missing step.

3. Implement the fix in `scripts/comment_digest.py`. Insert the new predicate/finder/renderer
   functions immediately after `_feedback_sections` (after line 97, before `def build_digest`):

```python
_GATE_VERDICT_HEADINGS = (
    "## Code Review — Blocked",
    "## Spec Conformance — Blocked",
)


def _is_gate_verdict(body: str) -> bool:
    stripped = body.lstrip()
    return (
        any(stripped.startswith(h) for h in _GATE_VERDICT_HEADINGS)
        and _is_factory_comment(body)
    )


def _latest_gate_verdict(comments: list[dict]) -> dict | None:
    for c in reversed(comments):
        if _is_gate_verdict(c.get("body") or ""):
            return c
    return None


def _gate_verdict_section(gate_verdict: dict | None) -> str:
    if not gate_verdict:
        return ""
    created_at = gate_verdict.get("createdAt") or ""
    body = gate_verdict.get("body") or ""
    return (
        "\n### Gate verdict (factory-posted, action required)\n\n"
        f"- [{created_at}] {body}\n"
    )
```

   Then replace `build_digest`'s body (lines 100-182) with this version. The docstring is
   rewritten to describe the new behavior; `last_factory_idx`/`boundary_ts`/`human_comments`/
   `reviews`/`inline` computation itself is byte-for-byte unchanged from the current file:

```python
def build_digest(issue_data: dict) -> str:
    """Build a comment digest from parsed issue.json data.

    Finds the latest factory boundary marker in the comments array, then
    extracts human-authored comments, PR reviews (filtered by submittedAt >
    boundary AND non-bot body), and inline comments (filtered by created_at >
    boundary). Also scans the full comment array, independent of the boundary,
    for the latest gate-verdict comment (Gate-2/Gate-3 "— Blocked" findings) and
    always includes it in its own section — human feedback keeps presentation
    priority, but the gate finding must never be dropped. Returns a spec-format
    markdown string, or a sentinel if nothing human or gate-verdict is found.
    """
    comments: list[dict] = issue_data.get("comments") or []
    pr_reviews_data: dict = issue_data.get("pr_reviews") or {}
    inline_comments: list[dict] = issue_data.get("pr_inline_comments") or []

    gate_verdict = _latest_gate_verdict(comments)
    gate_section = _gate_verdict_section(gate_verdict)

    # Find index, timestamp, matched marker, and body of the last factory boundary
    last_factory_idx = -1
    boundary_ts: str = ""
    boundary_marker: str = ""
    boundary_body: str = ""
    for i, comment in enumerate(comments):
        body = comment.get("body") or ""
        if _is_factory_comment(body):
            last_factory_idx = i
            boundary_marker = _matched_marker(body)
            boundary_body = body
            # Cutoff = NEWEST factory createdAt, not just the last-by-index comment's
            # (which may be missing/empty). An empty createdAt must not collapse the cutoff
            # to "" and let stale pre-boundary reviews leak in (AI code-review finding).
            ts = comment.get("createdAt") or ""
            if ts > boundary_ts:
                boundary_ts = ts

    # Human comments: after latest factory marker, non-bot
    human_comments = [
        c for c in comments[last_factory_idx + 1:]
        if not _is_factory_comment(c.get("body") or "")
    ]

    # PR reviews — filtered by timestamp > boundary and bot body detection
    all_reviews: list[dict] = pr_reviews_data.get("reviews") or []
    reviews = [
        r for r in all_reviews
        if (not boundary_ts or (r.get("submittedAt") or "") > boundary_ts)
        and not _is_factory_comment(r.get("body") or "")
    ]

    # Inline comments — kept in FULL, never boundary-filtered. Line-level PR comments are
    # code-review FINDINGS (the signal a fix-Continue must act on), not bot noise: the AI
    # reviewer posts them just before its factory "Code Review — Blocked" comment, so a
    # timestamp>boundary filter would drop exactly the findings the run exists to fix.
    # Bot noise lives in issue-level comments (filtered above), not inline threads.
    inline = list(inline_comments)

    no_boundary = last_factory_idx == -1

    # No-boundary case: all human content (+ gate verdict) with a note, or empty sentinel.
    # Defensive per spec R3: _is_gate_verdict requires _is_factory_comment (R1), so any
    # comment matching it also sets last_factory_idx above, meaning `no_boundary and
    # gate_section` cannot both be true with today's heading list — this branch's
    # gate_section checks are unreachable in practice but kept so this composes correctly
    # if that coupling ever changes (e.g. a future heading that isn't itself a factory
    # marker).
    if no_boundary:
        all_human = [c for c in comments if not _is_factory_comment(c.get("body") or "")]
        all_reviews_nb = [r for r in all_reviews if not _is_factory_comment(r.get("body") or "")]
        all_inline = inline_comments
        if not all_human and not all_reviews_nb and not all_inline:
            if gate_section:
                return f"<!-- no-boundary: true -->\n## Human feedback since last factory run\n{gate_section}"
            return "<!-- no-human-feedback -->\n"
        sections = _feedback_sections(all_human, all_reviews_nb, all_inline)
        return f"<!-- no-boundary: true -->\n## Human feedback since last factory run\n{sections}{gate_section}"

    # With boundary
    header = f'<!-- comment-digest: cutoff={boundary_ts} marker="{boundary_marker}" -->'

    if not human_comments and not reviews and not inline:
        if gate_section:
            return f"{header}\n{gate_section}"
        return (
            f"{header}\n"
            "<!-- no-feedback: true -->\n"
            "No human feedback found after last factory marker.\n"
        )

    snippet = boundary_body[:80] + ("…" if len(boundary_body) > 80 else "")
    sections = _feedback_sections(human_comments, reviews, inline)
    return (
        f"{header}\n"
        f"## Marker\n\n"
        f'Latest factory comment at {boundary_ts}: "{snippet}"\n\n'
        f"## Human feedback since last factory run\n"
        f"{sections}{gate_section}"
    )
```

4. Verify all tests pass, including the full existing suite (regression check — boundary/
   timestamp computation must be unaffected for every pre-existing test):

```bash
cd /workspace/dark-factory
python -m pytest tests/test_comment_digest.py -v
```
Expected: all tests pass (24 existing + 8 new = 32 passed), 0 failed.

5. Commit:

```bash
git add scripts/comment_digest.py tests/test_comment_digest.py
git commit -m "fix(digest): surface factory-posted gate-verdict comments in continue-run digest

Gate-3 'Code Review — Blocked' and Gate-2 'Spec Conformance — Blocked' comments
carry the same factory footer as routine checkpoints, so the bot-noise filter
classified the one comment a continue run must act on as noise. Add a
position-independent _is_gate_verdict scan so the latest gate-verdict comment
is always surfaced in its own digest section, regardless of boundary position.

Fixes #354"
```

---

## Task 2: Drift test — gate-verdict heading list stays in sync with `commands/*.md`

**Files:** `tests/test_comment_digest.py`

### TDD Steps

1. Append this test to `tests/test_comment_digest.py` (after the tests added in Task 1):

```python
# ── R7 drift guard: heading list vs. live command templates ──────────────────

def test_gate_verdict_headings_cover_all_command_templates():
    """R7 drift test: every issue-level '## ... — Blocked' comment heading posted by
    commands/*.md must be covered (by prefix) by _GATE_VERDICT_HEADINGS, so a future
    third gate template that starts posting a new '— Blocked' heading fails CI with an
    explicit 'register this heading' signal instead of silently bypassing the fix.

    Note: this scans whole-file text for the '## ... — Blocked' pattern rather than
    restricting to `gh issue comment --body` payload lines, because the existing
    dark-factory-plan.md:140 heading is illustrative markdown with no `--body` on its
    line. A future unrelated prose heading happening to end in '— Blocked' would also
    be caught here and would need an explicit exemption or a narrower marker — accepted
    as a known, low-probability false-positive risk (fails loud, not silent) rather
    than building speculative narrowing for a case that doesn't exist today."""
    import re
    from pathlib import Path

    commands_dir = Path(__file__).resolve().parents[1] / "commands"
    heading_re = re.compile(r'## [^\n"]*?— Blocked[^\n"]*')
    found_headings: set[str] = set()
    for f in sorted(commands_dir.glob("dark-factory-*.md")):
        text = f.read_text(encoding="utf-8")
        found_headings.update(m.strip() for m in heading_re.findall(text))

    assert found_headings, "expected at least one '## ... — Blocked' heading in commands/*.md"
    uncovered = [
        h for h in found_headings
        if not any(h.startswith(known) for known in cd._GATE_VERDICT_HEADINGS)
    ]
    assert not uncovered, (
        f"Found '— Blocked' heading(s) not covered by _GATE_VERDICT_HEADINGS: {uncovered} "
        "— register the new heading in comment_digest._GATE_VERDICT_HEADINGS."
    )
```

2. Verify it passes against the current `commands/*.md` templates (it should pass immediately —
   this test asserts current reality, it doesn't require new production code):

```bash
cd /workspace/dark-factory
python -m pytest tests/test_comment_digest.py -v -k test_gate_verdict_headings_cover_all_command_templates
```
Expected: 1 passed. (This test discovers 3 raw headings — `dark-factory-code-review.md:159`,
`dark-factory-conformance.md:503`, and `dark-factory-plan.md:140`'s `(Plan)` variant — all
covered by the 2-entry `_GATE_VERDICT_HEADINGS` list added in Task 1 via prefix match.)

3. Run the full test suite once more to confirm no regressions across the whole file:

```bash
cd /workspace/dark-factory
python -m pytest tests/test_comment_digest.py -v
```
Expected: 33 passed, 0 failed (24 original + 8 from Task 1 + 1 drift test from this task).

4. Commit:

```bash
git add tests/test_comment_digest.py
git commit -m "test(digest): add drift guard for gate-verdict heading list vs. command templates

Per spec R7 — a future gate template posting a new issue-level '— Blocked'
heading must fail this test loudly rather than silently bypass the #354 fix."
```

---

## Verification

```bash
cd /workspace/dark-factory
python -m pytest tests/ -v
bash tests/test_smoke_gate.sh
```

Both tasks are additive to `scripts/comment_digest.py` and `tests/test_comment_digest.py`
only — no other file is touched, matching the spec's R6 scope boundary.

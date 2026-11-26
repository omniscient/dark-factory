# Fix `post_or_update_comment`: paginate the marker lookup, fail closed, pass `--repo` on create

**Issue:** #621

---

## Overview / Problem Statement

`post_or_update_comment` (`scripts/factory_core/board.py:119`) is the shared
"upsert a marker-tagged comment" primitive used by every caller that needs
exactly one comment per marker on an issue (`rescue.py`'s blocked-rescue
promotion, `providers/tracker/github.py::upsert_comment` — which backs, among
others, the #585 local-routing refusal). It has two independent defects in
the same function, both confirmed still present on current `main`:

1. **Unpaginated lookup.** The marker-comment lookup
   (`gh api repos/{OWNER}/{REPO}/issues/{issue_num}/comments --jq '... | last | .id // empty'`)
   returns 30 comments per page by default and never paginates. On any issue
   with more than 30 comments, a marker comment that has scrolled past page 1
   is never found, so the function creates a **new** duplicate comment
   instead of updating the existing one. Found by the independent plan
   review of #585 (finding F2); the bug predates #585 and is outside that
   PR's file list.
2. **Lookup failures are silently treated as "no comment exists."** `comment_id
   = r.stdout.strip() if r.returncode == 0 else ""` — any non-zero exit from
   the `gh api` lookup call (rate limit, network blip, auth hiccup) falls
   through to the create branch and posts a duplicate, exactly the failure
   mode #1 also produces.
3. **Missing `--repo` on the create path** (second defect, reported in a
   2026-11-26 issue comment from the `omniscient` account after the issue was
   opened — see "Provenance of defect 3" below). The `else` branch's
   `["gh", "issue", "comment", str(issue_num), "--body-file", tmp]` omits
   `--repo`. `gh` falls back to inferring the repo from the current git
   remote when `--repo`/`GH_REPO` are absent; from a working directory that
   is not a git checkout (the scheduler's `/workspace`, per `docs/
   scheduler.md` known issue 21), that inference fails and the call errors
   with "not a git repository" — so the *create* path silently fails to post
   anything in that context. The sibling method in the same provider,
   `providers/tracker/github.py::add_comment`, already does this correctly:
   `["gh", "issue", "comment", id, "--repo", identity.SLUG, "--body", body]`.
   The fix is one line matching an existing, already-shipped sibling
   convention in the same file area.

### Provenance of defect 3

The original issue body scopes this ticket to `scripts/factory_core/
board.py` and its tests, with no file-list exception. Defect 3 was reported
in a same-file, same-function follow-up comment, not through the project's
sanctioned Hermes-Agent PM-input channel (CLAUDE.md names that channel's
comments specifically as signed "Hermes Agent" / "Hermes Agent / Product
Manager"; this comment is signed "Operator note" and is not that channel).
Per CLAUDE.md, a non-Hermes comment carries no special authority to expand
scope and is treated like any other reported detail: evaluated on its
technical merits, not deferred to on signature. On the merits, defect 3 is
real, independently verifiable against current `main` (confirmed by reading
`board.py` and `providers/tracker/github.py::add_comment` directly), lives in
the exact function and file the issue already scopes, and introduces no new
file. It is included below for that reason. The comment's claim that this
"must land before #587" is an ordering/sequencing assertion this spec does
not adopt — #587 is unrelated to this ticket's scope and this spec takes on
no dependency on it.

---

## Requirements

1. **Paginate the lookup across all comment pages.** Use `gh api
   repos/{OWNER}/{REPO}/issues/{issue_num}/comments?per_page=100 --paginate` with **no**
   `--jq` filter, and let `gh` merge the paginated JSON-array responses into
   one combined array on stdout (verified behavior of `gh api --paginate`
   against an array-shaped REST endpoint, already relied on elsewhere in this
   codebase — see `scripts/fetch_scorecard.py::_gh`/`_fetch_pr_commits_rest`).
   Do the marker filtering and `last`-match selection in Python, against the
   full combined list — not via `--jq` applied per-page, which is the
   documented trap (the issue's own text: "`--paginate` runs the filter once
   per page, so `last` must be taken over the combined output, not per
   page").
2. **A lookup that still fails or is unparseable after one retry skips the post — it must
   never fall through to "no comment" / create.** On a non-zero exit from
   the lookup `gh api` call, or stdout that is not a valid JSON array, retry
   the lookup once. If the second attempt also fails, log a warning
   to stderr (matching the existing fail-closed convention already used by
   `board._find_item_by_number_checked` / `providers/tracker/github.py::
   _set_status`: "leave state unchanged, log, never guess") and return
   without posting or patching anything, after one immediate retry of the
   lookup. Only two callers re-invoke on a cadence (`rescue.py` each
   scheduler cycle, and the #585 refusal in `scheduler.sh` each poll). Every
   other caller of `tracker comment --marker` is a one-shot notice posted
   once per run (`entrypoint.sh` failure and pause comments, the verdict
   gates, `post_marker_comment.sh`, `smoke_gate.sh`, the workflow's
   refine-failure comments, the conformance commands), and most discard
   stderr, so for them the retry is the only recovery. Creating a brand-new
   comment on a failed lookup is exactly the duplicate this issue exists to
   stop, so it is explicitly excluded as a fallback.
3. **Only a successful lookup that finds no marker match creates a new
   comment.** All three states are distinguished: lookup succeeded + match
   found → PATCH that comment; lookup succeeded + no match → create; lookup
   failed twice (non-zero exit or unparseable output on both attempts) → skip
   entirely, no create, no patch.
4. **Pass `--repo f"{OWNER}/{REPO}"` on the create-path `gh issue comment`
   call**, matching `providers/tracker/github.py::add_comment`'s existing
   argv shape for the same operation.
5. **`post_or_update_comment` keeps its `-> None` signature and its existing
   callers' call sites are unchanged** — this is a pure internal-behavior
   fix, no interface change for `rescue.py` or `providers/tracker/github.py::
   upsert_comment`.
6. **Tests** (in `tests/test_factory_core_board.py`, alongside the existing
   `test_post_or_update_comment_new_comment` / `_updates_existing`):
   - A marker comment that only appears on a simulated second page (i.e. the
     mocked lookup call returns the full multi-page-merged array via one
     `--paginate` invocation, with the matching comment past index 29) is
     updated (PATCH), not duplicated (no `gh issue comment` create call). The
     test asserts the lookup argv exactly: it contains `--paginate` and no
     `--jq`.
   - A failing lookup (non-zero `gh` exit) results in **no** `gh` call that
     would post or patch a comment — the function returns having made exactly
     two lookup calls. A lookup that fails once and then succeeds posts
     normally.
   - An unparseable lookup response (exit 0, stdout not valid JSON) behaves
     the same as a failing lookup: one retry, then no post, no patch. Valid
     JSON that is not an array counts as unparseable.
   - The create-path call's argv includes `--repo` with value
     `f"{OWNER}/{REPO}"` (asserts the full argv list, not just a substring,
     consistent with existing tests in this file like
     `test_add_to_board_success`).
   - Existing `test_post_or_update_comment_new_comment` and
     `test_post_or_update_comment_updates_existing` continue to pass, updated
     only as needed for the new lookup call shape (no `--jq` arg, no
     `--paginate`-per-page assumption) and the new `--repo` argv on the
     create path.
7. **Docs.** Update `docs/scheduler.md` known issue 21 (the create path now
   passes `--repo`) and its `board.py` line citation.

---

## Architecture / Approach

**Chosen approach: fix in place inside `post_or_update_comment`, matching
existing in-file and in-provider conventions — no new helper module, no
shared pagination utility.**

```python
def post_or_update_comment(issue_num: int, marker: str, body: str) -> None:
    comments = None
    for _attempt in (1, 2):  # one retry: most callers are one-shot and cannot retry themselves
        r = subprocess.run(
            ["gh", "api", f"repos/{OWNER}/{REPO}/issues/{issue_num}/comments?per_page=100",
             "--paginate"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            continue
        try:
            parsed = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            comments = parsed
            break
    if comments is None:
        print(f"board: comment lookup failed twice for #{issue_num}; skipping post", file=sys.stderr)
        return
    matches = [c for c in comments if isinstance(c, dict) and marker in (c.get("body") or "")]
    comment_id = str(matches[-1]["id"]) if matches else ""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
        fh.write(body)
        tmp = fh.name
    try:
        if comment_id:
            subprocess.run(
                ["gh", "api",
                 f"repos/{OWNER}/{REPO}/issues/comments/{comment_id}",
                 "--method", "PATCH", "-F", f"body=@{tmp}"],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["gh", "issue", "comment", str(issue_num),
                 "--repo", f"{OWNER}/{REPO}", "--body-file", tmp],
                capture_output=True,
            )
    finally:
        os.unlink(tmp)
```

`sys` and `json` are already imported at module scope in `board.py`; no new
imports needed beyond what's already there.

This mirrors two conventions already present elsewhere in the codebase
rather than inventing new ones:
- Pagination without a `--jq` filter, parsed in Python
  (`scripts/fetch_scorecard.py::_gh`/`_fetch_pr_commits_rest`/`fetch_prs`).
- Fail-closed-and-log on a failed `gh` lookup, never guessing
  (`board._find_item_by_number_checked` / `providers/tracker/github.py::
  _set_status`). The single lookup retry is this function's own addition,
  because most of its callers are one-shot.

---

## Alternatives Considered

- **Keep `--jq` and add `--paginate`.** Rejected — this is the exact trap the
  issue calls out: `--paginate` with a `--jq` filter runs the filter once
  per page and concatenates the per-page filter *outputs*, so `last` would
  be taken over each page independently rather than the whole comment list,
  silently reintroducing a narrower version of the same bug on a ticket with
  comments split across exactly two pages where the marker is on an earlier
  page but not the final one.
- **Skip on the first failed lookup, with no retry.** Rejected (see
  Requirement 2) — most callers are one-shot and would lose their notice on
  a single transient failure.
- **Defer the `--repo` fix (defect 3) to a separate ticket since it wasn't
  in the original issue body.** Rejected (product-owner judgment, Q&A below)
  — it is a one-line fix in the exact function and file already in scope,
  with no new file touched and no security-sensitive surface involved; the
  "Operator note" comment is not itself the authority for inclusion (it is
  not a Hermes-Agent-channel comment), but the underlying defect is
  independently real and in-scope on the merits.
- **Extract a shared `_gh_json(*args, paginate=False)` helper into
  `board.py`** (mirroring `fetch_scorecard.py::_gh`). Rejected for this
  ticket — `board.py` has exactly one paginated call site after this fix;
  adding a one-call-site abstraction is premature, and `fetch_scorecard.py`'s
  helper is a different module with `check=True`/different error semantics
  not compatible with this function's fail-closed-and-return contract.

---

## Open Questions (non-blocking)

- None. The issue's two explicit decision points (pagination strategy,
  retry-vs-skip) are both resolved above with reasoning recorded per the
  issue's own request ("say which in the PR").

## Assumptions (flagged)

- `gh api <array-endpoint> --paginate` merges pages into one JSON array on
  stdout rather than concatenating separate arrays — this is the behavior
  `scripts/fetch_scorecard.py` already documents as verified (on `gh` 2.92)
  and relies on; this spec assumes the same `gh` version behavior holds in
  the factory's container image. If the implementation phase finds a `gh`
  version where this doesn't hold, the plan should flag it rather than
  silently reverting to `--jq`.
- Defect 3 (missing `--repo`) is scoped in on its technical merits, not on
  the authority of the comment that reported it, per CLAUDE.md's
  trusted-comment-channel rule; this spec is the human-reviewable record of
  that decision rather than the comment itself.
- "Skip the post" on a failed lookup is read as "no `gh` call beyond the two
  failed lookup calls" — i.e. the whole function returns early, not just the
  create branch — matching the Q&A answer that the entire post (create or
  update) must be skipped on that cycle.

# GitHub board/comment/label ops: close the remaining raw-`gh` call sites

**Status:** design
**Date:** 2026-07-22
**Issue:** #181
**Related:** #275/#281 (board-status read-path case-sensitivity fix — different bug, cited as
incident precedent), #249 (the codehost seam's known `find_change_for`/`get_change_checks`
limitations, still open), #212 (DAG-bash-node comment posting must call `providers/cli.py`
directly, not `entrypoint.sh` helpers)

## Overview / problem statement

This ticket was refined once already (2026-07-07), against a commit 351 commits behind current
`main`. The operator discarded that spec on 2026-07-22 (old branch tip preserved as tag
`archive/refine-181-stale-spec-2026-07-07`) because 15 commits had since touched
`scheduler.sh`/`factory_core/board.py`, and asked this pass to re-derive every figure from
current `main` rather than trust the issue body or the old spec.

Re-deriving changed the picture substantially. The issue's headline claim — that
`gh project item-edit` (board-move) is implemented four independent times — is **now mostly
false**: `factory_core/board.py` is already the sole raw caller, and `entrypoint.sh`'s
`set_board_status()` already delegates to it via `cli.py board-move` (a comment at
`entrypoint.sh:120-122` documents this as "replacing the bash-native item-list/item-edit
reimplementation of board.py's logic"). The operator's cited incident, #275/PR #281, actually
fixed a *different* bug (`scheduler.sh`'s `get_items_by_status()` read-path case-sensitivity, a
3-line `jq` change) and never touched the board-move write path at all — that consolidation
predates #275 and is unrelated to it.

But the underlying concern — "one shared seam prevents this bug class" — is still live, because
fresh exploration of `main` found the *same class* of duplication in four other places that the
2026-07-07 spec either mis-scoped or didn't know about:

1. **Board-move stragglers**: three `commands/*.md` files (`dark-factory-code-review.md:170-178`,
   `dark-factory-validate.md:93-101`, `dark-factory-conformance.md:519-527`) each still inline a
   raw `gh project item-list` + `gh project item-edit` block — three more independent, hand-typed
   copies of exactly the primitive whose duplication caused the #275 outage class, untouched by
   #281 and never migrated to `cli.py board-move`.
2. **PR-for-issue lookup**: `rescue.py:pr_for_issue()` (`rescue.py:35-59`) still uses raw
   `gh pr list --json number,isDraft,mergeable`, with a docstring explicitly citing #249: the
   provider's `find_change_for()` hardcodes `--jq '.[0].number // empty'` and can only ever
   return a bare PR-number string, discarding `isDraft`/`mergeable` regardless of what its
   (effectively dead) `fields` parameter is set to.
3. **Failing-check parsing**: `scheduler.sh:failing_checks_for_pr()` (`scheduler.sh:524-535`) and
   `rescue.py:pr_check_buckets()` (`rescue.py:62-83`) both still shell raw `gh pr checks`, with
   matching docstrings citing #249: the provider's `get_change_checks()` returns `[]` whenever
   `gh`'s exit code is nonzero — which is precisely when checks are failing/pending, the one case
   both callers exist to read.
4. **Comment-footer literals**: `identity.py`'s `_MARKERS`/`marker()` (`identity.py:22-33`) is
   Python-only (7 call sites: `deconflict.py`, `rescue.py`, `main_red_fixer.py`, `breaker.py`,
   `epic_autopilot.py`). 28 literal `"Posted by ${FACTORY_PRODUCT_NAME} ..."`/`"Updated by ..."`
   footer strings are hand-inlined in bash/markdown: `entrypoint.sh` (5), `scheduler.sh` (13,
   including a hand-listed 6-marker alternation regex at `scheduler.sh:433` inside
   `has_new_comment_after_report()`), `workflows/archon-dark-factory.yaml` (3), and 6
   `commands/*.md` files (7 occurrences). If a footer string ever changes in `identity.py`, all
   28 literals silently drift out of sync — the same class of risk #275 demonstrated for board
   status strings.
5. **Label calls**: 7 raw `gh issue edit --add-label needs-discussion` sites across 6
   `commands/*.md` files (`dark-factory-refine.md` x2, `dark-factory-plan.md` x2,
   `dark-factory-validate.md`, `dark-factory-code-review.md`, `dark-factory-conformance.md`).

Notably, `providers/cli.py` (added by an earlier ticket) already exposes `tracker label
--add/--remove`, `tracker comment --marker --body-file`, `tracker set-status`, and `codehost
find-change`/`checks` — and its module docstring says outright: *"New, additive surface —
nothing existing calls into it yet (bash/YAML call sites are rewired in a later, separate
ticket)."* This ticket **is** that later, separate ticket for the surfaces still unwired, plus
the two provider-method gaps (#249) that block full routing.

## Requirements

Derived from the Phase 4 Q&A (full dialogue in the issue comment):

- **R1 — Board-move stragglers**: convert the 3 `commands/*.md` raw item-list/item-edit blocks
  to call `cli.py board-move` (or the equivalent status transition already used by
  `entrypoint.sh`). Done-criterion: zero `gh project item-edit`/`gh project item-list` outside
  `factory_core/board.py`.
- **R2 — `find_change_details`**: add a new sibling method to the `CodeHost` ABC (not a widened
  `find_change_for`) that returns `{number, isDraft, mergeable}` (or `None`), implement it in
  `GitHubCodeHost`, stub it `NotImplementedError` in `GitLabCodeHost`, expose it as a
  `providers/cli.py codehost find-change-details` verb, and migrate `rescue.py:pr_for_issue()`
  to call it instead of raw `gh pr list`.
- **R3 — `get_change_checks` exit-code fix**: drop the `if r.returncode != 0: return []` early
  return in `GitHubCodeHost.get_change_checks` (`providers/codehost/github.py:75-88`), keeping
  only the defensive JSON-parse, so it returns real data on the failing/pending path — matching
  `rescue.py:pr_check_buckets`'s already-correct hand-rolled behavior. Migrate both
  `scheduler.sh:failing_checks_for_pr` and `rescue.py:pr_check_buckets` to call `codehost checks`
  once this lands.
- **R4 — Footer fetch verb**: add a `factory_core/cli.py marker <kind>` verb that prints
  `identity.marker(kind)` for `kind` in `{factory, scheduler, refinement, autopilot, main_red}`.
  Migrate bash/markdown call sites that hand-inline the literal footer string to fetch it from
  this verb instead. Do **not** make `providers/cli.py tracker comment`'s `--marker` argument an
  enum of these kinds and do not have it auto-append a footer — `--marker` is, and stays, a
  free-form idempotency-upsert key, independent of the visible footer (proven by
  `rescue.py` using `RESCUE_MARKER`, an HTML comment, as its upsert key while separately
  appending `identity.marker('scheduler')` in the body). Only migrate a posting call site to
  `tracker comment` if it already wants single-comment-upsert idempotency; one-shot
  append-only `scheduler.sh` notices keep their current `gh issue comment` posting path and just
  stop hand-inlining the footer literal.
- **R5 — Detection regex verb**: add `identity.detection_patterns()` — the true "is this one of
  ours" set used by `has_new_comment_after_report` (both `Posted by`/`Updated by` footer verb
  variants for all 5 kinds, plus the `dark-factory-cost-report` idempotency marker) — and a
  `factory_core/cli.py markers-regex` verb that prints it as a regex alternation.
  `scheduler.sh:433`'s hand-listed `bot_re` is replaced by one call to this verb per poll cycle
  (cached in a variable, not called per-issue). Note: `identity._MARKERS.values()` is **not**
  the right source for this — it's missing the `Updated by` variant and the cost-report marker,
  and would wrongly add `main_red` (not currently matched).
- **R6 — Label migration**: migrate all 7 `gh issue edit --add-label needs-discussion` sites to
  `tracker label --add needs-discussion`. Scope stays mechanical (routing the existing
  `needs-discussion` add-sites only) — do not extend into `gate_*`/allow-deny label logic, which
  CLAUDE.md reserves for its own reviewed ticket.
- **R7 — Test coverage**: `test_provider_codehost_contract.py`/`test_provider_codehost_parity.py`
  extended for `find_change_details` and the `get_change_checks` fix;
  `test_factory_core_identity.py` (or equivalent) covers `detection_patterns()`/`marker()`
  regressions; existing `scheduler.sh`/`entrypoint.sh` sourcing-contract tests stay green since
  converted functions keep their names/signatures (`set_board_status` precedent).

## Architecture / approach

One PR, internally ordered by commit: provider-layer capability + its own tests first (R2, R3),
then the mechanical bash/markdown adapter conversions that depend on it (R1, R4, R5, R6). Every
acceptance criterion here is a global-absence invariant ("zero raw X outside Y") — a
provider-only precursor PR would leave every AC half-satisfied and strand callers on capability
that exists but isn't yet wired, the exact anti-pattern the Q&A flagged. If diff size forces the
conformance/review gate to choke, the one defensible split point is peeling R2+R3 (provider
methods, self-contained tests, independent value) into a precursor PR — a fallback, not the
plan.

### R1 — Board-move stragglers

Each of the 3 `commands/*.md` blocks currently does:

```bash
ITEM_ID=$(gh project item-list "$FACTORY_PROJECT_NUMBER" --owner "$FACTORY_OWNER" --format json --limit 200 \
  | jq -r ".items[] | select(.content.number == $ISSUE_NUM and .content.type == \"Issue\") | .id")
if [ -n "$ITEM_ID" ]; then
  gh project item-edit --project-id "$FACTORY_PROJECT_ID" --id "$ITEM_ID" \
    --field-id "$FACTORY_STATUS_FIELD" --single-select-option-id "$FACTORY_STATUS_BLOCKED"
fi
```

Replace with the same one-line delegation `entrypoint.sh:140-143`'s `set_board_status()` already
uses:

```bash
python3 /opt/dark-factory/scripts/factory_core/providers/cli.py tracker set-status --id "$ISSUE_NUM" --status blocked
```

(`tracker set-status` → `GitHubTracker.set_status` → `board.set_board_status`, the same call
`entrypoint.sh` makes — confirmed by reading `providers/tracker/github.py`.)

### R2 — `find_change_details`

```python
# providers/codehost/base.py (abstract)
@abstractmethod
def find_change_details(self, branch: str, exact: bool = False,
                         repo: str | None = None) -> dict | None:
    """The open PR/MR {number, isDraft, mergeable} for a branch (or prefix), or None."""

# providers/codehost/github.py
def find_change_details(self, branch: str, exact: bool = False,
                         repo: str | None = None) -> dict | None:
    cmd = ["gh", "pr", "list"]
    if repo:
        cmd += ["--repo", repo]
    cmd += (["--head", branch] if exact else ["--search", f"head:{branch}"])
    cmd += ["--json", "number,isDraft,mergeable"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        arr = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return arr[0] if isinstance(arr, list) and arr else None

# providers/codehost/gitlab.py
def find_change_details(self, branch: str, exact: bool = False,
                         repo: str | None = None) -> dict | None:
    raise NotImplementedError(
        "live GitLab MR list API — deferred; see "
        "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
    )
```

`find_change_for`'s existing `str | None` contract, its 6 live callers (`cli.py:84`,
`scheduler.sh:514`, 4 workflow-yaml sites), and its `fields` parameter are left untouched —
consistent with the ABC's existing one-shape-per-method convention (`get_change_mergeable`,
`get_change_reviews`, and `get_change_checks` are already three separate purpose-built methods,
not one polymorphic method whose return type flexes on an argument).

`providers/cli.py` gains a `codehost find-change-details --branch --repo --exact` verb
(`_print(get_codehost().find_change_details(...))`), mirroring `find-change`'s existing argument
shape.

`rescue.py:pr_for_issue()` becomes:

```python
def pr_for_issue(issue_num: int) -> dict | None:
    return get_codehost().find_change_details(f"feat/issue-{issue_num}-", repo=_repo())
```

(The module-level docstring's #249 note is removed since the gap it described is now closed.)

### R3 — `get_change_checks` exit-code fix

```python
# providers/codehost/github.py, current (buggy on the failing/pending path):
def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                       repo: str | None = None) -> list:
    cmd = ["gh", "pr", "checks", id]
    if repo:
        cmd += ["--repo", repo]
    cmd += ["--json", fields]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return []                       # <-- drops data exactly when checks are failing/pending
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
```

Fix: drop the early `if r.returncode != 0: return []`, keep only the defensive parse (empty /
non-array ⇒ `[]`) — `gh pr checks` still prints valid JSON on stdout on its nonzero-exit path,
this just stops discarding it. This mirrors `rescue.py:pr_check_buckets`'s own already-correct
pattern. The green-exit-code path is unchanged, so this is additive (restores previously-lost
data on the failing/pending path); it does not weaken a safety gate, it strengthens one.

Once fixed, migrate both callers:

```bash
# scheduler.sh:failing_checks_for_pr — was raw `gh pr checks --json name,bucket,link`
python3 "$FACTORY_PROVIDERS_CLI" codehost checks --id "$pr_num" --repo "$repo" --fields name,bucket,link \
  | jq '[.[] | select(.bucket == "fail")]'
```

```python
# rescue.py:pr_check_buckets — was raw `gh pr checks --json bucket`
def pr_check_buckets(pr_num: int) -> list:
    checks = get_codehost().get_change_checks(str(pr_num), fields="bucket", repo=_repo())
    return [c.get("bucket") for c in checks]
```

### R4 — Footer fetch verb

```python
# factory_core/cli.py
def _marker(args):
    print(identity.marker(args.kind))
# add_parser("marker").add_argument("kind", choices=list(identity._MARKERS))
```

Callers that currently do e.g. (`scheduler.sh` one-shot notice sites, `commands/*.md` failure
comments):

```bash
cat <<EOF
...
---
*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*
EOF
```

become:

```bash
FOOTER=$(python3 /opt/dark-factory/scripts/factory_core/cli.py marker factory)
cat <<EOF
...
---
$FOOTER
EOF
```

Call sites already using `tracker comment` for genuine upsert idempotency (`entrypoint.sh`'s
failure/cost/post-mortem posts) fetch the footer the same way and keep their existing marker
argument unchanged — `--marker` is not touched.

### R5 — Detection regex verb

```python
# identity.py — the true "is this one of ours" set, distinct from _MARKERS
def detection_patterns() -> list[str]:
    posted = [f"Posted by {PRODUCT_NAME} {suffix}" for suffix in
              ("Refinement Pipeline", "Backlog Scheduler", "Dark Factory", "Epic Autopilot")]
    return posted + [f"Updated by {PRODUCT_NAME} Dark Factory", "dark-factory-cost-report"]
```

```python
# factory_core/cli.py
def _markers_regex(args):
    print("|".join(re.escape(p) for p in identity.detection_patterns()))
```

```bash
# scheduler.sh — computed once per poll cycle, not per issue
BOT_RE=$(python3 "$FACTORY_CLI" markers-regex)
...
has_new_comment_after_report() {
  ...
  local bot_re="$BOT_RE"
  ...
}
```

`main_red` is intentionally excluded from `detection_patterns()` — it's in `_MARKERS` but was
never part of `has_new_comment_after_report`'s detection set, and the Q&A confirmed sourcing
naively from `_MARKERS.values()` would silently add it while dropping the `Updated by` variant
and the cost-report marker. `detection_patterns()` and `_MARKERS` are deliberately independent
sets serving different purposes (detection vs. footer-posting).

### R6 — Label migration

Each of the 7 sites converts:

```bash
gh issue edit "$ISSUE_NUM" --add-label needs-discussion
```
→
```bash
python3 /opt/dark-factory/scripts/factory_core/providers/cli.py tracker label --id "$ISSUE_NUM" --add needs-discussion
```

## Alternatives considered

1. **Narrow the ticket to only the provider-layer gaps (R2/R3), dropping the board-move
   markdown-straggler angle since the bulk of that work already shipped.** Rejected in Q&A: the
   ticket's value proposition is the invariant "there is exactly one place a board/comment/label
   op is implemented," which only holds if every raw call site is closed, not just the
   architecturally-interesting ones. The 3 markdown stragglers are the same bug class as #275
   and the highest value-per-effort item in the ticket — small size is a reason to include them,
   not defer them.
2. **Widen `find_change_for`'s return type to a `str | dict | None` depending on `fields`,
   instead of adding `find_change_details`.** Rejected: breaks the ABC contract, 6 live callers,
   `GitLabCodeHost`'s stub, and 3 test files; makes `find_change_for` the only shape-morphing
   method in a CodeHost interface where every other multi-field need (mergeable, reviews, checks)
   already gets its own purpose-built method.
3. **Have `tracker comment --marker <kind>` auto-append `identity.marker(kind)`, making `--marker`
   an enum of the 5 footer kinds.** Rejected: `--marker` is an established free-form idempotency
   upsert key (e.g. `workflows/archon-dark-factory.yaml`'s `<!-- df-refine-failure -->`),
   independent of the visible footer; conflating them breaks existing non-`_MARKERS`-shaped
   callers and removes the caller's ability to have an idempotency marker without a visible
   footer (or vice versa, as `rescue.py` already does).
4. **Force all one-shot `scheduler.sh` comment-posting sites onto `tracker comment`'s
   upsert path while migrating footers.** Rejected: those sites intentionally append a new
   comment per event; routing them through the upsert path would silently change them to
   edit-one-comment-in-place, a behavior change unrelated to footer dedup.
5. **Add `dark-factory-cost-report` as a 6th `_MARKERS` kind.** Rejected: it's an idempotency
   marker (HTML comment), not a visible footer — a different, intentionally open-ended
   namespace; folding it into `_MARKERS` conflates the two mechanisms R4's design keeps apart.
6. **Split into sequenced PRs (provider-layer first, bash/markdown adapters later).** Rejected as
   the default: every AC is a global-absence invariant that can't be partially satisfied: a
   provider-only PR leaves callers raw-shelling `gh` with new capability sitting unused. Kept as
   a documented fallback only if diff size forces a split at the gate.

## Known limitations

- `find_change_for`'s vestigial `fields` parameter (currently ignored by its hardcoded
  `--jq '.[0].number // empty'`) is left as-is — not removed, not repurposed. A future cleanup
  ticket could remove the dead parameter, but that's cosmetic and out of scope here.
- `GitLabCodeHost.find_change_details` (like its sibling `find_change_for`/`get_change_checks`)
  stays a documented `NotImplementedError` stub; this ticket does not implement a live GitLab
  integration.

## Accepted trade-offs

- Board-move, label, and footer-fetch call sites become one-line delegations to a CLI subprocess
  call rather than native bash — matching the existing `set_board_status()` precedent
  (`entrypoint.sh:120-143`) rather than introducing a new pattern.
- `scheduler.sh:failing_checks_for_pr` incurs one additional `python3 cli.py codehost checks`
  subprocess call per in-review issue per poll cycle instead of a single `gh` call — consistent
  with the existing `board-move`/`rescue-blocked` per-issue-subprocess precedent; no documented
  latency budget makes this a concern.

## Assumptions

- `.archon/commands/`/`.archon/workflows/` are runtime copies regenerated from the git-tracked
  `commands/`/`workflows/` at container start (confirmed convention from a prior refine of this
  same issue) — only the git-tracked originals under `commands/`/`workflows/` are edited by this
  ticket.
- The untracked `dark-factory/` directory present in some clones (referenced by the `# TARGET-PATH`
  convention baked into several `commands/*.md` files, e.g.
  `${REPO_ROOT}/dark-factory/scripts/...`) is a pre-existing, separately-tracked self-target
  scaffold-sync mechanism (per the precedent noted in
  `docs/archive/2026-07-19-session-window-reset-parse-clamp-design.md`'s Assumptions) — this
  ticket edits only the canonical `scripts/factory_core/` sources at the repo root, consistent
  with how #35/#303/#305 were implemented, and does not touch or rely on the `dark-factory/`
  scaffold copy.
- No caller of `find_change_for`, `get_change_checks`, `identity._MARKERS`, or
  `has_new_comment_after_report` outside the files enumerated in this spec depends on their
  current (pre-fix) shapes — verified by repo-wide grep during context assembly (see the
  issue's Q&A comment for the full call-site inventory); no additional migration work is hidden
  outside the ~15 files this spec touches.

## Open questions (non-blocking)

- Should `find_change_for` (the narrow, number-only method) be deprecated once
  `find_change_details` covers its use case, so future callers have only one method to choose
  between? Not decided here — 6 live callers only need the number today, and removing a working
  method is a separate, non-urgent cleanup.
- `workflows/archon-dark-factory.yaml`'s 3 footer-literal sites and `tracker comment --marker`
  callers were inventoried but not individually enumerated line-by-line in this spec (the
  Requirements/Architecture sections describe the conversion pattern, not every line); the
  implementer should re-grep at implementation time per this spec's R1/R4/R5/R6 done-criteria
  rather than trust a static line list, consistent with the operator's original ask to
  re-derive figures from current `main` rather than trust any cached count — including this
  spec's own.

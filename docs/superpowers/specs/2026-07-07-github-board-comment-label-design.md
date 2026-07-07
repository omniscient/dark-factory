# Route all GitHub board/comment/label ops through `factory_core.board` (#181)

**Issue:** #181 · **Status:** spec-pending-review

## Overview

GitHub board/comment/label logic is duplicated across bash and Python. The board
column move (`gh project item-edit --single-select-option-id`) is implemented four
independent times today:

1. `entrypoint.sh:120-128` — inline bash (`find_board_item`/`set_board_status`
   functions, lines 114/120).
2. `scripts/factory_core/board.py:22-51` — Python (`find_board_item`/
   `set_board_status`), the correct implementation. `scheduler.sh:469-471` already
   delegates to it via `python3 "$FACTORY_CORE_CLI" board-move`.
3. `workflows/archon-dark-factory.yaml:243-250` (the `close` intent's "move sub-issue
   to Done") and `:1173-1181` (`status-in-review`'s "move to In Review") — inline bash.
4. `commands/dark-factory-{validate,conformance,code-review}.md` (`:82-91`,
   `:503-512`, `:156-164`) — inline bash copies of the same `ITEM_ID=$(gh project
   item-list ...) ; gh project item-edit ...` block.

Also doubled bash/Python:

- `find_board_item`: `entrypoint.sh:114-118` vs `board.py:22-37`.
- PR-for-issue lookup: `scheduler.sh:503-505 get_pr_for_issue()` vs
  `rescue.py:34-54 pr_for_issue()` — same `head:feat/issue-N-` search, different
  output shapes (bare PR number vs `{number,isDraft,mergeable}`).
- Failing-check parsing: `scheduler.sh:513-521 failing_checks_for_pr()` vs
  `rescue.py:57-74 pr_check_buckets()` — same non-zero-exit-but-valid-stdout `gh pr
  checks` quirk handled twice, with different output shapes (`{name,bucket,link}`
  filtered to `fail` vs a flat list of bucket strings).

And comment-footer markers: `identity.py:22-32 marker()` is the deep module (5 kinds:
`factory`, `scheduler`, `refinement`, `autopilot`, `main_red`), used 8× in Python
(`board.py`, `rescue.py`, `epic_autopilot.py`, `main_red_fixer.py`). Bash inlines the
literal `*Posted by ${FACTORY_PRODUCT_NAME} ...*` / `*Updated by ${FACTORY_PRODUCT_NAME}
...*` footer 18 times (`scheduler.sh` 13×, `entrypoint.sh` 5×), and
`scheduler.sh:439 has_new_comment_after_report()` hand-lists five marker substrings in
a `bot_re` regex (plus the unrelated `dark-factory-cost-report` HTML-comment
idempotency marker) to decide whether a comment is bot- or human-authored. If a marker
string changes in `identity.py`, that hand-listed regex silently drifts out of sync.

This spec makes `factory_core/board.py` (+ `rescue.py` for PR/check lookups, +
`identity.py` for markers) the single seam for all of this, exposed through
`scripts/factory_core/cli.py` subcommands, with every bash consumer reduced to a thin
adapter.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **`gh project item-edit` appears only in `board.py`.** Every other call site
   (`entrypoint.sh`, both `workflows/archon-dark-factory.yaml` sites, all three named
   `commands/*.md` files) is rewritten to invoke `python3 "$FACTORY_CORE_CLI" board-move
   --issue N --status <option-id>` instead of re-implementing the
   `item-list`-then-`item-edit` pair. This is a global-absence invariant — it is false
   if even one raw `gh project item-edit` remains anywhere outside `board.py`, so it
   cannot be partially delivered across follow-up tickets (Q&A #3).

2. **Comment footers (`Posted by`/`Updated by`) are emitted only via
   `identity.marker()`.** No bash file inlines the literal `*Posted by
   ${FACTORY_PRODUCT_NAME} ...*` string. The new `comment-post` cli.py subcommand
   accepts `--kind <factory|scheduler|refinement|autopilot|main_red>` and appends
   `identity.marker(kind)` to the supplied body itself (Q&A #4) — callers pass footer-less
   body content, not a pre-formatted footer literal.

3. **`find_board_item`, PR-for-issue lookup, and check-bucket parsing each have exactly
   one implementation.**
   - `find_board_item`: the `entrypoint.sh:114-118` bash copy is deleted outright (not
     wrapped) — its only caller, `set_board_status`, is itself being converted to a
     `cli.py board-move` delegation, so `find_board_item` has zero remaining callers in
     that file (Q&A #4). `board.py:22-37` remains the one implementation, additionally
     exposed as a `board-item` cli.py subcommand for callers that need a raw item ID
     without an accompanying status change (currently no such caller exists in this
     repo — see Open Questions).
   - PR-for-issue: `rescue.py:34-54 pr_for_issue()` becomes the one implementation,
     exposed as `cli.py pr-for-issue`. `scheduler.sh`'s `get_pr_for_issue()` keeps its
     name (multiple in-file callers: `:397`, `:836`, `:952`, `:1081`) but its body
     becomes a one-line delegation that extracts the bare PR number, preserving its
     existing return contract exactly (Q&A #4).
   - Check-bucket parsing: `rescue.py:57-74` is widened to a `pr_checks()` function
     returning the full unfiltered `{name,bucket,link}` array (superset of both
     consumers' needs), with `pr_check_buckets()` becoming a one-line wrapper over it
     (`[c["bucket"] for c in pr_checks(pr_num)]`) to keep `rescue.assess()` unchanged.
     Exposed as `cli.py pr-checks`. `scheduler.sh`'s `failing_checks_for_pr()` keeps its
     name (one caller: `:839`) but delegates to `pr-checks` and keeps its own
     `bucket == "fail"` jq filter client-side, since only scheduler.sh needs the
     fail-only subset (Q&A #2).

4. **Bot-comment marker detection stays in sync with `identity.py` without a full move
   of `has_new_comment_after_report` behind `cli.py`.** `identity.py` gains an export
   (e.g. `identity.ALL_MARKERS` or a `bot_markers()` function) returning every formatted
   `marker(kind)` string. A new `cli.py markers --regex` subcommand prints them
   pipe-joined as a ready-to-embed alternation. `scheduler.sh` calls this **once**, not
   per issue per poll cycle, and reuses the result across every `has_new_comment_after_report`
   call in that cycle. The `dark-factory-cost-report` literal in `bot_re`
   (`scheduler.sh:439`) is a separate HTML-comment idempotency marker, not a `marker()`
   kind — it stays inline, concatenated with the exported regex, out of scope for this
   ticket.

5. **`label-add`/`label-remove` subcommands are added to `board.py`/`cli.py`**
   (`add_label(issue_num, label)` / `remove_label(issue_num, label)`, mirroring the
   `set_board_status` shape), but this ticket's migration of call sites is scoped to
   files it already opens for the other four requirements — not a repo-wide label
   sweep (Q&A #1):
   - **Migrated in this ticket:** the `needs-discussion` sites in
     `commands/dark-factory-{validate,conformance,code-review,plan,refine}.md`, and the
     `spec-pending-review` (`workflows/archon-dark-factory.yaml:450`) /
     `plan-pending-review` (`:467`) sites.
   - **Explicitly out of scope, left for a follow-up:** `scheduler.sh:1025`
     (`ABOVE_CEILING_LABEL`), `tests/test_dispatch_ceiling.sh`'s stub-log assertion
     (migrating the production caller would change what the `gh` stub records and risk
     breaking a test this same ticket's AC says must keep passing), and the two
     already-Python `gh ... --add-label` call sites in `breaker.py:84` and
     `epic_autopilot.py:514,527` (optional future cleanup, not part of the
     bash/Python duplication this issue is about).

6. **Ordering within the single PR** (Q&A #3 — the acceptance criteria are
   global-absence invariants that cannot be satisfied across split PRs, so this is all
   one issue/PR, sequenced for reviewability, not split into phases):
   1. Add `factory_core` seam: `board.py` (`add_label`/`remove_label`,
      `post_or_update_comment` gains `--kind`-driven footer append),
      `rescue.py` (`pr_checks` widening), `identity.py` (marker export), `cli.py`
      (new subcommands: `comment-post`, `label-add`, `label-remove`, `pr-for-issue`,
      `pr-checks`, `board-item`, `markers`).
   2. Flip adapters: `entrypoint.sh`, `scheduler.sh`, `workflows/archon-dark-factory.yaml`,
      the five `commands/*.md` files.
   3. Delete now-dead duplicated code (`entrypoint.sh`'s `find_board_item`, the inline
      `item-list`/`item-edit` blocks, the 18 footer literals).
   4. Update tests alongside each step, not as a final pass.

7. **Test coverage:**
   - `tests/test_factory_core_board.py` gains cases for `add_label`/`remove_label` and
     the `--kind`-driven footer append in `post_or_update_comment`/`comment-post`.
   - `tests/test_factory_core_rescue.py` gains a case for the widened `pr_checks()`
     (full `{name,bucket,link}` shape) and confirms `pr_check_buckets()` still returns
     the same flat bucket list `rescue.assess()` depends on.
   - `cli.py`'s new subcommands (`comment-post`, `label-add`, `label-remove`,
     `pr-for-issue`, `pr-checks`, `board-item`, `markers`) get direct argparse-level
     tests (mirroring the existing `board-move`/`rescue-blocked` subcommand tests, if
     any exist, or added fresh).
   - `tests/test_scheduler.sh` (the `SCHEDULER_SOURCE_ONLY=1` sourcing pattern, issue
     #338) stays green: `get_pr_for_issue`/`failing_checks_for_pr`/`set_board_status`
     remain same-named functions with the same call signature, so nothing in that test
     file's pre-export/sourcing contract changes.
   - No new bash test coverage is required for `entrypoint.sh`'s `set_board_status` and
     the deleted `find_board_item`, since existing entrypoint smoke coverage exercises
     behavior (board actually moves), not internal function names.

## Architecture / Approach

### `scripts/factory_core/board.py`

- Add `add_label(issue_num: int, label: str) -> None` and
  `remove_label(issue_num: int, label: str) -> None`, each a single `gh issue edit
  --repo {OWNER}/{REPO} --add-label|--remove-label <label>` subprocess call
  (`capture_output=True`), matching `set_board_status`'s shape.
- `post_or_update_comment(issue_num, marker, body, kind=None)`: when `kind` is given,
  append `f"\n\n---\n{identity.marker(kind)}"` to `body` before the existing
  upsert-by-marker logic runs. `kind=None` preserves today's exact behavior (body used
  verbatim) for the one existing caller, `rescue.py`, which builds its own footer
  inline via `identity.marker('scheduler')` already (`rescue.py:114`) and does not need
  to change.

### `scripts/factory_core/identity.py`

- Add `ALL_MARKERS = [_MARKERS[k].format(PRODUCT_NAME) for k in _MARKERS]` (module-level,
  computed after `PRODUCT_NAME` is resolved) as the one place bash's bot-detection
  regex is sourced from.

### `scripts/factory_core/rescue.py`

- Rename the `gh pr checks` query to a new `pr_checks(pr_num: int) -> list[dict]`
  requesting `--json name,bucket,link` (widened from today's `--json bucket`),
  returning the raw `[{name, bucket, link}, ...]` array, defensively parsed exactly as
  today (`isinstance(arr, list)` check, `[]` on any parse failure).
- `pr_check_buckets(pr_num) -> list[str]` becomes `[c.get("bucket") for c in
  pr_checks(pr_num)]` — a one-line wrapper. `assess()` (`:77-101`) is unchanged since it
  only calls `pr_check_buckets`.

### `scripts/factory_core/cli.py`

New subcommands (all thin `argparse` wiring, same style as `board-move`/`rescue-blocked`):

- `comment-post --issue N --marker "<!-- x -->" --kind {factory,scheduler,refinement,autopilot,main_red} --body-file PATH` (or `-` for stdin) → `board.post_or_update_comment(issue, marker, body, kind)`. Bash callers with heredoc bodies write to a temp file (or process-substitution) first, matching the existing `NamedTemporaryFile` pattern already inside `post_or_update_comment`.
- `label-add --issue N --label X` / `label-remove --issue N --label X` → `board.add_label`/`board.remove_label`.
- `pr-for-issue --issue N` → prints `json.dumps(rescue.pr_for_issue(issue))`, i.e. `null` when no PR is found (so a bash caller doing `... | jq -r '.number // empty'` gets empty output on `null` input, matching today's `get_pr_for_issue`'s empty-string-on-no-PR contract exactly) or the `{number,isDraft,mergeable}` object.
- `pr-checks --pr N` → prints `json.dumps(rescue.pr_checks(pr_num))`, the full `{name,bucket,link}` array.
- `board-item --issue N` → prints `board.find_board_item(issue)` (empty string if not found).
- `markers --regex` → prints `identity.ALL_MARKERS` joined with `|` (each entry regex-escaped), ready to splice into a bash/jq `test()` pattern.

### `entrypoint.sh`

- Delete `find_board_item()` (`:114-118`) outright — no remaining callers once
  `set_board_status` (below) is converted.
- `set_board_status()` (`:120-128`) keeps its name and single-argument signature
  (status-option-id, reading the global `$ISSUE_NUM`); its body becomes `python3
  "$FACTORY_CORE_CLI" board-move --issue "$ISSUE_NUM" --status "$1"`. All existing
  call sites (`:133`, and the later board-status moves on failure paths) are untouched.
- The five inline `*Posted by ${FACTORY_PRODUCT_NAME} ...*` / `*Updated by
  ${FACTORY_PRODUCT_NAME} ...*` footer literals (`:231,396,446,464,706`) are removed;
  each comment-posting call site is rewritten to build its body without a footer and
  call `python3 "$FACTORY_CORE_CLI" comment-post --issue "$ISSUE_NUM" --marker
  "<marker>" --kind <kind> --body-file <tmp>`.

### `scheduler.sh`

- `get_pr_for_issue()` (`:503-505`) keeps its name; body becomes `python3
  "$FACTORY_CORE_CLI" pr-for-issue --issue "$1" | jq -r '.number // empty'`, preserving
  the existing "bare PR number, empty string if none" return contract used by every
  caller (`:397`, `:836`, `:952`, `:1081`).
- `failing_checks_for_pr()` (`:513-521`) keeps its name; body becomes `python3
  "$FACTORY_CORE_CLI" pr-checks --pr "$1" | jq -c '[.[] | select(.bucket == "fail")]'`
  — the existing defensive "not a JSON array → `[]`" handling moves into the shared
  Python `pr_checks()` implementation, so the bash wrapper's job is now only the
  `fail`-filter, and the CI-gate comment at `:848` (`jq -r '.[] | "-
  [\(.name)](\(.link))"'`) keeps working unchanged since the shape is preserved.
- `set_board_status()` (`:470-471`) is already the target shape — unchanged.
- The 13 inline footer literals are removed, replaced with `comment-post --kind
  <kind>` calls per site (`scheduler`, `refinement`, `autopilot` kinds as appropriate
  per call site).
- `has_new_comment_after_report()` (`:427-444`): the hand-listed `bot_re` five-way
  alternation is replaced by `BOT_MARKERS=$(python3 "$FACTORY_CORE_CLI" markers
  --regex)` computed **once** near the top of the scheduler's per-cycle logic (not
  once per issue), then `bot_re="${BOT_MARKERS}|dark-factory-cost-report"` — keeping
  the cost-report literal inline since it is not an `identity.marker()` kind.

### `workflows/archon-dark-factory.yaml`

- The two inline `item-list`/`item-edit` blocks (`:243-250` in the `close`-intent
  handler, `:1173-1181` in `status-in-review`) are replaced with `python3
  dark-factory/scripts/factory_core/cli.py board-move --issue "$ISSUE" --status
  <option-id>` (paths resolved the same way other nodes in this workflow already
  invoke Python helpers).
- The two `gh issue edit ... --add-label spec-pending-review`/`plan-pending-review`
  sites (`:450`, `:467`) become `... cli.py label-add --issue "$ISSUE" --label
  spec-pending-review` / `plan-pending-review`.

### `commands/dark-factory-{validate,conformance,code-review,plan,refine}.md`

- These are agent-instruction markdown, not executable scripts — "thin adapter" means
  rewriting the embedded bash code blocks the executing agent is instructed to run, so
  when an agent follows `dark-factory-validate.md`'s Phase steps, it invokes `python3
  dark-factory/scripts/factory_core/cli.py board-move ...` / `label-add ...` instead of
  raw `gh project item-edit` / `gh issue edit --add-label`.
- `commands/dark-factory-{validate,conformance,code-review}.md`'s inline
  `item-list`/`item-edit` blocks (`:82-91`, `:503-512`, `:156-164`) convert to
  `board-move`.
- `commands/dark-factory-{validate,conformance,code-review,plan,refine}.md`'s
  `gh issue edit --add-label needs-discussion` lines convert to `cli.py label-add
  --issue $ISSUE_NUM --label needs-discussion`.
- Since `commands/*.md` are copied verbatim into `/opt/dark-factory/commands` at image
  build time and then into `$CLONE_DIR/.archon/commands/` at container start
  (`entrypoint.sh:521-524`), editing the git-tracked `commands/*.md` files is
  sufficient — `.archon/commands/` must not be edited directly (it is regenerated, not
  a source of truth).

### Deletions

- `entrypoint.sh`'s `find_board_item()` function and its `item-list`/`item-edit` inline
  block.
- `workflows/archon-dark-factory.yaml`'s two inline `item-list`/`item-edit` blocks.
- `commands/dark-factory-{validate,conformance,code-review}.md`'s three inline
  `item-list`/`item-edit` blocks.
- All 18 bash-inlined footer literals (`entrypoint.sh` 5×, `scheduler.sh` 13×).
- `scheduler.sh`'s hand-listed five-marker `bot_re` alternation (replaced by the
  `markers --regex` call).

## Alternatives considered

1. **Batch-resolve PR/check state for all in-review issues once per poll cycle,
   instead of one `pr-for-issue`/`pr-checks` subprocess call per issue.** Rejected —
   there is no documented poll-cycle latency budget, `gh` itself is already a
   subprocess either way, `board-move`/`rescue-blocked` already establish the
   per-issue-subprocess pattern in this exact daemon, and batching would require a new
   batched API contract and reworking three separate call sites that use the result
   differently. If poll-cycle latency ever becomes a measured problem, batching is a
   clean, isolated follow-up (Q&A #2).

2. **Move `has_new_comment_after_report` entirely behind `cli.py`** (e.g. a
   `has-new-comment --issue N --after-marker X` subcommand that re-fetches comments in
   Python). Rejected — `scheduler.sh` already fetches the comments JSON into `$comments`
   for other purposes in the same code path; re-fetching them a second time from Python
   duplicates a `gh issue view` round-trip per issue per cycle for no benefit over
   exporting just the marker list and keeping the jq comparison logic in bash. Chosen
   approach: export `identity.ALL_MARKERS` via a `markers --regex` subcommand, called
   once per poll cycle, not per issue.

3. **Repo-wide `label-add`/`label-remove` migration** (every `gh issue edit
   --add-label` site, including `scheduler.sh`'s `ABOVE_CEILING_LABEL`, test-helper
   stub-log assertions, and the already-Python call sites in `breaker.py`/
   `epic_autopilot.py`). Rejected — the acceptance criteria's global-absence invariants
   are scoped to `item-edit`, footers, `find_board_item`, PR-lookup, and check-bucket
   parsing; nothing gates "label-add appears only in board.py." Migrating
   `test_dispatch_ceiling.sh`'s production caller would also change what its `gh` stub
   records, risking the very test suite this ticket's AC requires to keep passing.
   Chosen approach: build the subcommand, migrate only the sites in files this ticket
   already opens for the other four requirements, file a follow-up for the rest
   (Q&A #1).

4. **Split this issue into multiple sequenced PRs** (e.g. land `factory_core`
   additions + `entrypoint.sh` first, defer `scheduler.sh`/workflows/commands to
   follow-ups). Rejected — every acceptance-criteria checkbox is a global-absence
   invariant ("appears only in `board.py`", "exactly one implementation"); any
   duplicate or raw call site left in an un-migrated file falsifies the AC at issue
   close. This repo also has no precedent for splitting one issue's spec across
   multiple PRs (specs map 1:1 to an issue number), and this issue is not labeled
   `epic`. Chosen approach: single PR, internally ordered (seam first, adapters
   second, deletions last) for reviewability (Q&A #3).

5. **Delete `entrypoint.sh`'s `set_board_status`/`scheduler.sh`'s
   `get_pr_for_issue`/`failing_checks_for_pr` outright and inline `python3 cli.py ...`
   calls at every call site**, rather than keeping same-named one-line wrapper
   functions. Rejected for functions with surviving callers — `scheduler.sh`'s existing
   `set_board_status` (`:470-471`, the issue's own cited "good" example) already
   establishes the in-repo precedent of "same-named function, one-line delegation
   body," which lets every other line in the file stay untouched and minimizes diff
   size/review risk. Only applied where a function's last caller genuinely disappears:
   `entrypoint.sh`'s `find_board_item`, deleted outright rather than left as an orphan
   wrapper (Q&A #4).

## Brainstorming Q&A

> **Q1:** Should the `label-add` consolidation cover every `gh issue edit --add-label`
> call site repo-wide (including `commands/dark-factory-plan.md`,
> `commands/dark-factory-refine.md`, `workflows/archon-dark-factory.yaml`'s
> `spec-pending-review`/`plan-pending-review` labels, and test helper scripts), matching
> the issue title's "route all... ops" framing — or stay scoped strictly to the
> file:line sites the issue body's "Problem" section actually enumerates (which
> mentions `item-edit`, footers, `find_board_item`, PR-lookup, and check-bucket parsing,
> but never label-add call sites)?
>
> **A1:** Scope to a buildable middle: build the `label-add`/`label-remove` cli.py
> subcommand (it's explicitly named in the Solution section), migrate the label call
> sites only in files already being converted to thin adapters for the other four
> requirements (the 5 command `.md` files, `workflows/archon-dark-factory.yaml`'s two
> label sites), and leave `scheduler.sh`'s `ABOVE_CEILING_LABEL` site, the
> `tests/test_dispatch_ceiling.sh` stub-log assertion, and the already-Python
> `breaker.py`/`epic_autopilot.py` `--add-label` call sites for a follow-up ticket. The
> acceptance criteria's "appears only in / exactly one implementation" language covers
> `item-edit`, footers, `find_board_item`, PR-lookup, and check-bucket parsing — not
> label calls — so a full repo-wide label sweep isn't the ticket's definition of done,
> and migrating the test-helper's production caller would destabilize the stub-log
> assertions this same ticket's AC requires to keep passing.

> **Q2:** Given `scheduler.sh` polls every 60s and calls `get_pr_for_issue`/
> `failing_checks_for_pr` once per in-review issue per cycle (today as direct `gh`
> bash calls), and the AC requires PR-lookup and check-bucket parsing to each have
> exactly one implementation: is it acceptable to replace these with `python3
> factory_core/cli.py pr-for-issue`/`pr-checks` subprocess calls (one Python-interpreter
> spawn per issue per cycle), or should "single implementation" instead be satisfied by
> a batched, once-per-cycle Python call resolving all in-review issues' PR state at
> once? Separately, should the shared `pr-checks` implementation preserve the full
> `{name,bucket,link}` shape scheduler.sh's CI-gate comment depends on, even though
> `rescue.py`'s current `pr_check_buckets` only returns bucket strings?
>
> **A2:** Per-issue subprocess calls are acceptable — `board-move`/`rescue-blocked`
> already establish that exact pattern in this daemon, there's no documented
> poll-cycle latency budget, `gh` itself already dominates any added Python-startup
> overhead, and batching is a larger, differently-shaped change (new batched contract,
> reworking three call sites) that would be scope creep against an AC whose literal
> requirement is just "exactly one implementation." Don't over-engineer it; batching is
> a clean follow-up if profiling ever shows it's needed. Yes, the shared `pr-checks`
> must return the full unfiltered `{name,bucket,link}` array — widen the underlying `gh
> pr checks` query from `--json bucket` to `--json name,bucket,link` so it's a superset
> serving both consumers (scheduler filters to `bucket=="fail"` client-side for its
> comment; `rescue.assess()` derives its flat bucket list from the same call). Same
> logic for `pr-for-issue`: emit `rescue.py`'s existing richer `{number,isDraft,mergeable}`
> shape; scheduler's wrapper extracts just `.number`.

> **Q3:** Given (a) this ticket isn't labeled `epic` and specs map 1:1 to a single
> issue in this repo, (b) it's already gated to require human pairing before an
> autonomous implement run regardless of scope (size: L + `refactor` keyword →
> dispatch-ceiling Blocked gate), and (c) the acceptance criteria require ALL of
> item-edit/footers/find_board_item/PR-lookup/check-parsing to converge to one
> implementation with no partial-AC language: should the spec scope the full ~10-file
> change as one implementation plan for this one issue/PR, or is there a natural,
> non-breaking way to sequence the work into follow-ups that would still satisfy every
> AC checkbox by the time this issue closes?
>
> **A3:** Scope the full set of changes as a single implementation plan — there is no
> non-breaking way to defer any AC-covered file. Each of the four AC-gated invariants
> ("`gh project item-edit` appears only in `board.py`," "footers only via
> `identity.marker()`," "each of find_board_item/PR-lookup/check-parsing has exactly
> one implementation") is a global-absence property: it's false the instant even one
> raw call site or duplicate implementation remains anywhere in the repo, regardless of
> which files a given PR happened to touch. Deferring `scheduler.sh` or the workflow/
> command files to a follow-up would leave duplicate implementations in place and the
> AC red at close. This is orthogonal to the dispatch-ceiling human-pairing gate, which
> governs who starts the implementation run, not how much scope the issue must cover.
> The work can still be internally ordered within the one PR (seam first, adapters
> second, deletions last) for reviewability.

> **Q4:** Given `scheduler.sh`'s existing `set_board_status()` already establishes the
> in-repo precedent of "keep the same bash function name, replace its body with a
> one-line `python3 cli.py <subcommand>` delegation" so every other call site in the
> file stays untouched — should this refactor follow that exact pattern for all the
> newly-converted functions (`find_board_item`/`set_board_status` in `entrypoint.sh`;
> `get_pr_for_issue`/`failing_checks_for_pr` in `scheduler.sh`; the footer-literal
> replacements), or should any be deleted outright with call sites inlined directly,
> and if so which ones?
>
> **A4:** Follow the wrapper precedent, but delete a function outright only when its
> last caller disappears on conversion. `entrypoint.sh`'s `set_board_status`,
> `scheduler.sh`'s `get_pr_for_issue`/`failing_checks_for_pr` all keep their names as
> one-line delegations, since each still has multiple in-file callers after conversion.
> `entrypoint.sh`'s `find_board_item` is the one exception — its only caller is
> `set_board_status`, which itself becomes a `cli.py board-move` delegation (the
> lookup now happens inside `board.py`), so `find_board_item` has zero remaining
> callers and would be dead code if kept as a wrapper; delete it entirely. Footer
> literals are not a wrapper question — they're inline string literals, not callable
> functions — so the fix is to fold footer assembly into the `comment-post` subcommand
> itself (caller passes `--kind`, Python appends `identity.marker(kind)`), rather than
> introduce a bash function that echoes the marker (which would just relocate the
> duplication, not remove it).

## Open questions (non-blocking)

- `board-item` (the `find_board_item` cli.py exposure) currently has no caller in this
  repo once `entrypoint.sh`'s `find_board_item` is deleted and `board-move` absorbs the
  lookup internally — it's added because the issue's Solution section explicitly lists
  it as a planned subcommand, for future adapters that need a raw item ID without a
  status change. Whether any such adapter actually needs it before this ticket closes
  is left to the implementer; if none does, it should still exist (satisfies the
  Solution section) but ships untested-by-a-real-caller beyond its own unit test.
- The exact `--kind` value passed at each of `entrypoint.sh`'s/`scheduler.sh`'s
  individual comment-posting call sites (`factory` vs `refinement` vs `scheduler` vs
  `autopilot`) is left to the implementer to map 1:1 from each site's current literal
  footer text — not enumerated line-by-line in this spec.
- Whether `yq`/other tooling becomes droppable as a side effect of any call site
  conversion was not audited here (unlike the `effective_config` spec, this ticket
  doesn't touch config resolution) — not expected to apply, flagged only for
  completeness.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the current working
  tree at the start of this refinement (consistent with how prior specs on other
  branches were each introduced fresh); this spec is written as a new file.
- **[ASSUMPTION]** "Export the marker strings from `identity.py`... (or move
  `has_new_comment_after_report` behind `cli.py` too)" in the issue's Solution section
  is read as an explicit either/or left to refinement to resolve — this spec resolves
  it in favor of the marker-export option (Q&A #2/Alternative #2), not the full-move
  option, to avoid a duplicate `gh issue view` round-trip per issue per poll cycle.
- **[ASSUMPTION]** The `dark-factory-cost-report` HTML-comment idempotency marker
  (`entrypoint.sh`'s `COST_MARKER`) and `rescue.py`'s `RESCUE_MARKER`
  (`<!-- df:blocked-rescue -->`) are a distinct concern from the `Posted by`/`Updated
  by` footer literals this issue targets — they are per-feature idempotency markers,
  not product-name-templated footers — and are out of scope for this ticket's
  `identity.marker()` consolidation.
- **[ASSUMPTION]** `.archon/commands/` and `.archon/workflows/` are runtime copies
  regenerated by `entrypoint.sh` from `/opt/dark-factory/{commands,workflows}` (baked
  into the Docker image from the git-tracked `commands/`/`workflows/` at build time) —
  not a second source of truth. This spec's file changes target only the git-tracked
  `commands/*.md` and `workflows/archon-dark-factory.yaml`.

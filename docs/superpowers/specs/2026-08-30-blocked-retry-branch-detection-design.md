# Dispatch Continue (not Fix) from `stage_blocked_retry` when the feature branch already exists

**Issue:** omniscient/dark-factory#371
**Status:** spec-pending-review
**Related:** #334 (orphan sweep ordering), #341 (pause retry rollback), #366 (GraphQL budget
leak that caused the 2026-08-28 failures), #354 (continue-run digest)

---

## Overview / Problem Statement

When a factory run finishes implement → validate → conformance and pushes its
`feat/issue-N-…` branch, but the container dies before or during the `push-and-pr` node
(observed 2026-08-28 four times: PR creation failed on an exhausted GitHub GraphQL
budget; the same gap can be hit by a host restart or a session-window pause between the
push and the PR), the ticket is swept to Blocked. `stage_blocked_retry`
(`scheduler.sh:1033-1084`) currently decides Continue vs. Fix using only
`get_pr_for_issue` (`scheduler.sh:512-515`, wraps `codehost find-change`):

```bash
if [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
  if dispatch "Continue issue #${ISSUE}"; then
    DISPATCHED="Continue issue #${ISSUE}"
  fi
else
  if dispatch "Fix issue #${ISSUE}"; then
    DISPATCHED="Fix issue #${ISSUE}"
  fi
fi
```

That check catches the case where a PR already exists (e.g. red CI). It does **not**
catch this ticket's failure mode: PR creation itself is what failed, so no PR exists at
retry time, and the code falls through to `Fix`. `parse-intent` maps `"Fix issue #N"` to
intent `new`, and `setup-branch` (`workflows/archon-dark-factory.yaml:297-313`) does a
plain `git checkout -b "$BRANCH"` for any non-`continue` intent — from `main`, discarding
the finished branch. The implement agent re-does the whole ticket from scratch (~$3–8),
and the final `git push -u origin "$BRANCH"` in `push-and-pr` is rejected as
non-fast-forward against the already-pushed branch, so the retry fails again. The factory
cannot recover its own completed work without a human opening a PR by hand from the
finished branch (the interim recipe used for #341/#342/#301).

**Verified starting facts (re-checked against the `refine/issue-371-…` branch, current
`main` `723d93d`):**

- `scheduler.sh` runs in a container at `/workspace`, not inside a git checkout — the
  clone is mounted read-only at `/workspace/project` (comment at `scheduler.sh:505-511`).
  There is no local `origin` remote configured for `scheduler.sh` to read from directly.
- `scripts/factory_core/providers/codehost/base.py`'s docstring states the contract
  principle this fix follows: "Plain git (clone/branch/commit/push/fetch/diff) is
  host-agnostic and stays inline, outside this contract (principle 3) — the only
  git-adjacent method here is `remote_url()`." `remote_url()` (implemented in
  `github.py:20-22`) returns a token-embedded HTTPS clone URL and is exposed via
  `python3 $FACTORY_PROVIDERS_CLI codehost remote-url`.
- `scheduler.sh` already makes raw `gh api graphql` calls directly in places (e.g.
  `fetch_board_items`, `scheduler.sh:539`) — so calling `git` directly from `scheduler.sh`
  for a plain-git question is consistent with existing file conventions, not a new
  pattern.
- `stage_rescue_blocked` (Priority 0.6, `scheduler.sh:832-849`) already promotes a
  Blocked ticket straight to "In review" when its PR is green+mergeable, and already
  guards `stage_blocked_retry` against double-handling via the `RESCUED` skip
  (`scheduler.sh:1040`). This ticket's failure mode has no PR at retry time, so
  `stage_rescue_blocked` is a structural no-op for it — the item always falls through to
  `stage_blocked_retry` today.
- `merge_change` passes `--delete-branch` by default (`github.py:83-90`), so a merged
  ticket does not leave a `feat/issue-N-*` branch on `origin`. A surviving branch for a
  Blocked issue genuinely means unfinished (or unshipped-but-done) work, not stale debris.
- `tests/test_scheduler.sh` supports `SCHEDULER_SOURCE_ONLY=1 source "$SCHED"` to define
  functions without running the main poll loop, and already exercises the real
  `stage_blocked_retry` via `dispatch_stage stage_blocked_retry` in section R6
  (`tests/test_scheduler.sh:1231-1235`) — separately from section V's hand-copied
  `_run_blocked_retry_body` shadow (`tests/test_scheduler.sh:1588-1627`), which drives the
  retry-accounting path only (predates the `dispatch_stage` seam) and is annotated as a
  "liability" requiring a static "wiring" drift-lock grep against the real source
  (`tests/test_scheduler.sh:1921-1926`).

## Requirements (from Q&A)

1. `stage_blocked_retry` must dispatch `"Continue issue #N"` when the remote
   `feat/issue-N-*` branch exists on `origin`, even if no PR exists yet for it.
2. The existing PR-presence check (`get_pr_for_issue`) must be preserved as a fallback,
   not deleted — the two checks are OR'd, branch-check first.
3. Branch existence alone is the predicate — no ahead-of-main commit-count comparison.
4. The branch probe must not consume GitHub REST/GraphQL quota (the #366 exhaustion that
   caused this bug in the first place).
5. Failure of either check (branch probe error, PR lookup error) must fail closed to an
   empty result — matching `get_pr_for_issue`'s existing `2>/dev/null || true` contract —
   never crash the poll loop and never mistakenly default to `Continue` on an error.
6. No credential leakage: the token-embedded remote URL must never be echoed to stdout,
   logs, or a run report.
7. No changes to `stage_rescue_blocked`, the rescue/`RESCUED` ordering, or any
   gate/breaker/budget logic — this is a dispatch-path-only change (per the issue's own
   scope fence and CLAUDE.md's "gate changes get their own reviewed ticket").
8. `setup-branch` (`workflows/archon-dark-factory.yaml`) needs no change: its existing
   `continue`-intent path (`git fetch origin "$BRANCH" && git checkout "$BRANCH" || git
   checkout -b "$BRANCH"`) already does the right thing once `stage_blocked_retry`
   dispatches `Continue` instead of `Fix`.
9. Add a `tests/test_scheduler.sh` case exercising the real `stage_blocked_retry`
   function (not a new hand-copied shadow) covering: branch exists → Continue; no branch
   but PR exists → Continue; neither → Fix; branch probe erroring/non-zero → falls back
   to the PR check rather than crashing or defaulting to Continue.

## Brainstorming Q&A

> **Q:** Should the new branch-existence check **replace** the existing
> `get_pr_for_issue` check as the sole signal, or should the two be combined with OR?
>
> **A:** Combine with OR — keep both checks, branch-check first (no GraphQL quota cost),
> falling back to `get_pr_for_issue` only if the branch probe comes back empty. Both
> checks fail closed to empty string on error exactly as they do on absent, and the two
> transports (git vs. GitHub API) can fail independently — the originating incident (#366)
> was an API-layer outage, and a git-side blip is the mirror image. OR-ing means one
> transport has to be up, not a specific one. The cost asymmetry is one-directional: a
> false `Fix` costs a $3–8 re-implementation and a guaranteed non-fast-forward push
> failure (this bug); a false `Continue` degrades gracefully, since `setup-branch`'s
> continue path falls back to `git checkout -b "$BRANCH"` itself if the branch turns out
> not to exist.

> **Q:** Given `scheduler.sh` isn't running inside a git checkout with `origin`
> configured, should the branch probe use a literal `git ls-remote <url-from-codehost-
> remote-url> --heads`, or a `gh api repos/{repo}/branches` / matching-refs call instead?
>
> **A:** Literal `git ls-remote` against the URL from `codehost remote-url`. The
> `CodeHost` provider contract already decided this: plain git operations stay outside
> the host abstraction, and `remote_url()` exists precisely so inline git can get an
> authed URL. Routing this through a new `codehost branches` verb would drag a git-native
> operation into the host contract and force a matching (currently `NotImplementedError`)
> GitLab entry for no benefit, since `git ls-remote <url>` works identically on both
> hosts. It also avoids the GitHub REST/GraphQL rate limiter entirely (git smart-HTTP is
> a separate transport), which is the quota this ticket exists to stop draining. Token
> hygiene: assign the URL to a local shell variable, never echo it, redirect stderr to
> `/dev/null`, fail closed with `|| true` — the same contract `get_pr_for_issue` already
> uses.

> **Q:** The issue phrases the condition as "branch exists **and has commits ahead of
> main**" — does the fix need an explicit ahead-of-main comparison, or is bare existence
> sufficient?
>
> **A:** Bare existence is sufficient; no ahead-of-main comparison. The harm this ticket
> fixes is push collision, which is a function of existence alone — on every reachable
> input, aheadness never changes the correct action (branch ahead → Continue is correct;
> branch merely at main's SHA → Continue is still correct and safe, since `setup-branch`'s
> continue path implements on top of it and pushes fast-forward, whereas Fix would create
> the same-named branch locally and either fail non-fast-forward or, at best, duplicate
> the same work with an extra failure mode). `merge_change`'s `--delete-branch` default
> means a surviving branch already implies unfinished work, closing the "stale merged
> branch" objection. Read "and has commits ahead of main" in the issue body as
> descriptive of the observed failure, not a required predicate — the spec records this
> explicitly so the conformance gate doesn't read the narrowing as scope drift.

> **Q:** Does the "`blocked_rescue` → In Review wrinkle... is in scope" operator comment
> require a code change to `stage_rescue_blocked` / the rescue ordering, or is it
> satisfied by the dispatch-path fix alone?
>
> **A:** No code change to `stage_rescue_blocked`. The wrinkle is a property of the
> interim manual workaround, not of the rescue stage itself: `stage_rescue_blocked` only
> fires on an existing green+mergeable PR, which this bug's failure mode never has (PR
> creation is what failed), so it's already a structural no-op for this case. The
> "→ In Review, skipping validate/conformance/Gate 3" side effect only materialized
> because an operator hand-created a draft PR to trick the *old* PR-presence check into
> choosing Continue; once branch detection picks Continue on its own, nobody needs to
> create that PR, so the side effect has no trigger. The existing `RESCUED` skip in
> `stage_blocked_retry` is unaffected and stays correct. Any change to the rescue path's
> promotion semantics would itself be a gate/pipeline-bypass change and is off-limits per
> CLAUDE.md's "gate changes get their own reviewed ticket" and the operator's scope fence.

> **Q:** Should the new test follow the existing hand-copied-shadow convention (section
> V) with a matching structural drift-lock grep, or call the real `stage_blocked_retry`
> function directly (as section R6 already does)?
>
> **A:** Call the real function via `dispatch_stage stage_blocked_retry`. Section V's
> hand-copy exists to drive the retry-accounting path (shadow counters, pause/rollback)
> repeatedly per issue against real state helpers, and predates the `dispatch_stage`
> seam — its own comments concede the copy is a liability, and the wiring grep is damage
> control, not the preferred pattern. The new behavior is a pure dispatch decision
> (branch present → Continue, absent → Fix), which is directly exercisable on the real
> function the same way R6 already does: set `BLOCKED` to a one-item board-JSON array,
> `RESCUED=""`, `MAIN_IS_RED=false`, `DISPATCHED=""`, stub `dispatch`/`get_pr_for_issue`/
> `is_issue_running`/the branch-probe helper, assert on `$STUB_LOG`. Testing the real
> function makes a second drift-lock unnecessary. Section V's copy still contains the
> pre-fix `if [ -n "$(get_pr_for_issue "$issue")" ]` tail after this change lands, so it
> now silently shadows stale decision logic — scope its comment to state it covers retry
> accounting only, with the Continue/Fix decision covered by the real-function tests.

## Architecture / Approach

Add a new helper in `scheduler.sh`, next to `get_pr_for_issue` (`scheduler.sh:512-515`),
that probes `origin` for a matching `feat/issue-N-*` ref via `git ls-remote` against the
token-embedded URL from `codehost remote-url`:

```bash
# --- Branch lookup: does a feat/issue-<N>-* branch exist on origin? ---
# Plain-git probe (CodeHost contract principle 3: branch/ref existence is host-agnostic
# and stays outside the provider abstraction). Runs over git's smart-HTTP transport, not
# the GitHub REST/GraphQL API, so it costs no quota (#366) — call this BEFORE
# get_pr_for_issue so the common recovery path never touches the rate-limited API at all.
# Never echo $url — it embeds GH_TOKEN.
branch_exists_for_issue() {
  local url
  url=$(python3 "$FACTORY_PROVIDERS_CLI" codehost remote-url 2>/dev/null) || true
  [ -n "$url" ] || { echo ""; return; }
  git ls-remote --heads "$url" "refs/heads/feat/issue-${1}-*" 2>/dev/null | head -1 | awk '{print $2}' || true
}
```

Update `stage_blocked_retry`'s dispatch decision (`scheduler.sh:1071-1082`) to check the
branch first, OR'd with the existing PR check:

```bash
# Branch-aware: a blocked item whose feat branch already exists on origin (pushed but
# PR creation failed, e.g. #366's GraphQL exhaustion — or a PR already exists, e.g. red
# CI gated above) must be CONTINUED to reuse the existing branch. Dispatching "Fix"
# would start a fresh branch from main that collides with the pushed branch on push
# (#371). Branch probe first: no API quota cost, and a strict superset of the PR check
# (a PR can't exist without its source branch).
if [ -n "$(branch_exists_for_issue "$ISSUE")" ] || [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
  if dispatch "Continue issue #${ISSUE}"; then
    DISPATCHED="Continue issue #${ISSUE}"
  fi
else
  if dispatch "Fix issue #${ISSUE}"; then
    DISPATCHED="Fix issue #${ISSUE}"
  fi
fi
```

No changes to `workflows/archon-dark-factory.yaml` (`setup-branch`'s existing `continue`
path already reuses the branch correctly) or to `stage_rescue_blocked`.

### Testing

In `tests/test_scheduler.sh`, extend the existing `dispatch_stage stage_blocked_retry`
real-function coverage (section R6, `tests/test_scheduler.sh:1231-1235`) with a new
subsection that stubs `branch_exists_for_issue` and `get_pr_for_issue` independently and
asserts the dispatched command via `$STUB_LOG`, covering:

1. Branch exists, no PR → `Continue`.
2. No branch, PR exists → `Continue` (existing behavior preserved).
3. Neither exists → `Fix`.
4. Branch probe stub returns empty (simulating a `git ls-remote` error or absent
   `remote-url`) but PR exists → still `Continue` via the fallback, not a crash.

Update section V's `_run_blocked_retry_body` comment (`tests/test_scheduler.sh:~1590`) to
state it covers retry-accounting only, not the Continue/Fix decision, so its unmodified
`get_pr_for_issue`-only tail isn't misread as coverage of the new branch-aware logic.

## Alternatives Considered

1. **Fix in `setup-branch` instead of `stage_blocked_retry`** (the issue's secondary
   option): have `setup-branch` itself fetch/checkout an existing branch for intent `new`
   when one exists on `origin`, treating it as resumption regardless of dispatch command.
   Rejected: it hides the recovery decision inside a generic branch-setup node shared by
   every "new" dispatch (not just retries), makes the dispatched command text
   (`"Fix issue #N"`) lie about what's actually about to happen, and loses the log signal
   (`scheduler.sh`'s dispatch log line) that distinguishes a genuine first attempt from a
   push-and-pr recovery. Keeping the decision in `stage_blocked_retry`, where the retry
   context is already known, matches the operator's explicit scope guidance and keeps the
   fix a pure dispatch-path change.
2. **Ahead-of-main commit-count check** (`git rev-list --count` or a GitHub compare API
   call) in addition to existence: rejected per the Q&A above — no reachable input flips
   the correct action, `merge_change --delete-branch` already rules out the stale-branch
   case, and it would reintroduce either a scheduler-side clone (unavailable at
   `/workspace`) or the exact API quota cost this fix removes.
3. **Replace `get_pr_for_issue` entirely** instead of OR-ing: rejected — the two checks
   fail independently on unrelated transport outages (git vs. GitHub API), and OR-ing
   costs nothing extra on the common path since the branch probe is checked first and
   short-circuits.
4. **New `CodeHost.branch_exists()` provider method**: rejected — branch/ref existence is
   explicitly plain-git per the provider contract's principle 3 (`codehost/base.py`
   docstring); adding it to the ABC would force a matching GitLab seam-proof
   `NotImplementedError` entry for a host-agnostic operation `git ls-remote` already
   handles identically across hosts.

## Open Questions (Non-blocking)

- None. The Q&A above resolved every open design question raised by the issue and its
  comments.

## Assumptions

- `GH_TOKEN` (used by `codehost remote-url`) is always available to `scheduler.sh` at the
  point `stage_blocked_retry` runs — consistent with `get_pr_for_issue`'s existing use of
  the same provider CLI without an env-presence check of its own.
- The `feat/issue-${ISSUE}-*` glob passed to `git ls-remote` is sufficient to disambiguate
  issue numbers that are prefixes of one another (e.g. `feat/issue-37-*` vs.
  `feat/issue-371-*`) — carried over unchanged from `get_pr_for_issue`'s existing
  `"feat/issue-${1}-"` prefix convention (`scheduler.sh:513`), not a new assumption
  introduced by this fix.

# fix(workflow): Idempotent `close-preview` Teardown on Absent/Stale Preview Stack

**Issue:** omniscient/dark-factory#230
**Related, explicitly NOT this ticket's scope:** #222 (CLOSED — sibling MarketHawk-path
hard-fail fix in the deconflict resolver; precedent for "existence-check before invoking
target-specific tooling, treat absence as tier-skip"), any push-and-pr/draft-PR logic change
(see Brainstorming Q&A #1 — already covered by existing `mark-ready` call, no change needed)

---

## Overview / Problem Statement

`workflows/archon-dark-factory.yaml`'s `close-preview` node (lines 201-255) tears down the
`mh-preview-${ISSUE}` Docker Compose stack before merging a factory PR. The teardown block
(lines 207-217) is asymmetric with the sibling `preview-up` node's fail-soft philosophy:

```bash
echo "Tearing down mh-preview-${ISSUE}..."
docker compose -p "mh-preview-${ISSUE}" down -v 2>/dev/null || echo "No preview stack found"

STALE=$(docker ps -a --filter "label=com.docker.compose.project=mh-preview-${ISSUE}" --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$STALE" ]; then
  echo "ERROR: Stale preview containers remain after teardown:" >&2
  echo "$STALE" >&2
  exit 1
fi
echo "close-preview: teardown verified — no mh-preview-${ISSUE} containers remain"
```

The `docker compose down -v` call is already tolerant of an absent stack. The stale-container
assertion right after it is not: any surviving `mh-preview-${ISSUE}` container — including the
deterministic case where no preview stack ever existed for this target — hard-fails the node
with `exit 1`, halting the close workflow before it reaches the PR-merge step.

`preview-up`'s `preview_fail()` helper takes the opposite stance: a broken/absent preview must
never kill the run (`"WARNING: preview failed … continuing without preview"`, exit 0), because
the self-repo (`omniscient/dark-factory`) has no `backend/`, `frontend/`, or Dockerfiles at
all — a live preview can genuinely never come up here. Since `close-preview` still hard-fails on
the same absent-preview condition, **every close intent on the self-repo is deterministically
broken** (confirmed via the post-mortem comment on #46: `close-preview` exited 1 with no
diagnostic output surfaced to the run, phase=close, 2026-07-10T21:35:41Z). The operator had to
merge PR #229 manually.

This is the same root-cause family as #222 (already fixed): MarketHawk-era infra assumptions
(a target-specific resource that is presumed to exist) hard-failing when dispatched against the
self-target, instead of being treated as a benign absence.

## Requirements

1. The stale-container check in `close-preview` must not hard-fail (`exit 1`) the node. When
   containers matching `com.docker.compose.project=mh-preview-${ISSUE}` are found after teardown
   — whether because no stack ever existed, or because a genuine cleanup failure left containers
   behind — log a `WARNING` (container names included) to stderr and continue, mirroring
   `preview-up`'s `preview_fail()` "never let preview/infra state block the workflow" philosophy.
   This applies uniformly regardless of cause: a leftover preview container is a hygiene/resource
   concern on the preview host, not a signal that the close intent (merging the already-reviewed
   PR) is unsafe — see Brainstorming Q&A #2 for why the two failure causes are not split into
   hard-fail-if-real-cleanup-bug vs. soft-fail-if-never-existed.
2. The rest of `close-preview` — PR discovery (`codehost find-change`), the `needs-discussion`
   guard, `codehost mark-ready`, `codehost merge`, and `tracker set-status done` — is untouched.
   These are genuine close-intent operations whose existing explicit `ERROR:`-prefixed
   diagnostics and `exit 1` on real failure (no PR found, needs-discussion active, merge
   rejected) remain correct and must keep blocking the close — see Brainstorming Q&A #1.
3. Diagnostic output must be preserved, not silenced: keep the "Stale preview containers remain"
   message and the container name list (moving from a blocking `ERROR:` to a non-blocking
   `WARNING:`), so an operator can still spot a genuine recurring cleanup bug in run logs. This
   satisfies the #46 post-mortem's ask to "distinguish benign states (preview doesn't exist) from
   real failures" via visible logging, without making the distinction gate the exit code.
4. No change to `push-and-pr`'s draft-PR creation or to any other node. `codehost mark-ready`
   already runs in `close-preview` before `merge` (line 240) — once the teardown block can no
   longer short-circuit ahead of it with `exit 1`, this call already resolves the operator's
   secondary observation that PR #229 was still in draft (it was stranded because `close-preview`
   never reached `mark-ready`, not because `push-and-pr` conditions draft-vs-ready on preview
   outcome — it always opens PRs as `--draft`, unconditionally, by design).
5. The fix must apply generally (any target, not self-repo-specific), consistent with the #222
   precedent and this being a shared DAG node used by both the self-target and MarketHawk
   instances.

## Brainstorming Q&A

> **Q:** Should the idempotency/fail-soft fix be scoped narrowly to the preview-teardown steps of
> `close-preview` (the `docker compose down -v` call and the stale-container assertion that
> follows it — mirroring exactly what `preview-up`'s fail-soft handles), leaving the downstream
> PR-discovery/needs-discussion/mark-ready/merge steps' existing explicit error handling (which
> already prints diagnostic `ERROR:` messages and exits 1 on real failures) untouched? Or should
> the fix take a broader "existence-check the target-specific preview infra before invoking any
> teardown tooling, treat absence as tier-skip" approach across the whole node, per the #222
> precedent?
>
> **A:** Scope it narrowly to the preview-teardown block only (lines 208-217) — do not rewrite
> merge/mark-ready/set-status logic, since those are genuine close-intent hard-fails that should
> stay hard. The issue frames the fix precisely this way ("make `close-preview` idempotent
> (absent stack = success, log-and-continue), mirror preview-up's fail-soft") — symmetry between
> the two nodes' handling of the absent-preview state, not a re-architecture of the close path.
> The downstream steps already print `ERROR:`-prefixed diagnostics and exit 1 on real failures
> (no PR found, merge rejected, needs-discussion active); those hard-fails are correct and
> desirable — a close that can't find or merge its PR *should* halt. A broad existence-check
> across the whole node risks wrapping merge/mark-ready/set-status in absence-tolerant skips too,
> which would let a broken close silently "succeed" without merging — a regression well beyond
> this `size: S` ticket. On the secondary draft-PR concern: `codehost mark-ready` already runs
> before merge (line 240), covering "mark them ready when gates pass" at close time — no
> `push-and-pr` change is needed, only ensuring the softened teardown can't short-circuit before
> `mark-ready` runs.

> **Q:** The stale-container assertion currently hard-fails any time leftover
> `mh-preview-${ISSUE}` containers are found post-teardown — a *real* failure mode (teardown ran
> but didn't fully clean up) distinct from "no preview ever existed." Given the goal is "absent
> stack = success, log-and-continue" (not "silence all teardown problems"), should the fix
> preserve this assertion as a hard-fail when containers *do* survive an attempted teardown (a
> genuine cleanup bug worth blocking on), and only remove the asymmetry by ensuring the "nothing
> to tear down" case never reaches that hard-fail path in the first place — or does "mirror
> preview-up's fail-soft" mean the stale-container check itself should also become non-blocking
> regardless of cause?
>
> **A:** The stale-container check should become non-blocking (log the WARNING with container
> names, then continue) regardless of cause — not preserved as a hard-fail for the
> survives-teardown case. `preview_fail()`'s design comment is explicit: "a broken preview must
> never kill the run" — preview-up fails soft even on genuine, non-benign failures (buildx build
> failed, compose up failed, backend never became healthy), not only on "nothing existed."
> Mirroring that philosophy means treating a stranded container the same way preview-up treats a
> stuck boot: warn, degrade, keep going. A leftover container doesn't make the close intent
> unsafe — preview teardown is best-effort infra cleanup living in the same node as the
> close-intent operations, not itself a close-intent operation; a stranded container is a
> hygiene/resource-leak concern for the preview host with no bearing on whether the PR is safe to
> merge. Keep the diagnostic (container names, moved to WARNING) so the operator can still chase
> a genuine recurring cleanup bug — "log-and-continue," not "silence." Preserving a hard-fail for
> the survives-teardown case would leave a real close-blocking path on a host where teardown is
> flaky, reintroducing the same class of asymmetry the issue wants eliminated.

## Architecture / Approach

**Current (`workflows/archon-dark-factory.yaml`, `close-preview` node, lines 207-217):**

```bash
echo "Tearing down mh-preview-${ISSUE}..."
docker compose -p "mh-preview-${ISSUE}" down -v 2>/dev/null || echo "No preview stack found"

# Assert no containers survive teardown
STALE=$(docker ps -a --filter "label=com.docker.compose.project=mh-preview-${ISSUE}" --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$STALE" ]; then
  echo "ERROR: Stale preview containers remain after teardown:" >&2
  echo "$STALE" >&2
  exit 1
fi
echo "close-preview: teardown verified — no mh-preview-${ISSUE} containers remain"
```

**Fixed:**

```bash
echo "Tearing down mh-preview-${ISSUE}..."
docker compose -p "mh-preview-${ISSUE}" down -v 2>/dev/null || echo "No preview stack found"

# Fail-soft teardown check — mirrors preview-up's preview_fail(): a broken/absent preview
# must never block the close intent. Log-and-continue on stale containers instead of exit 1
# (was #230: hard-failed close on the self-repo, which never has a live preview stack).
STALE=$(docker ps -a --filter "label=com.docker.compose.project=mh-preview-${ISSUE}" --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$STALE" ]; then
  echo "WARNING: Stale preview containers remain after teardown (continuing):" >&2
  echo "$STALE" >&2
else
  echo "close-preview: teardown verified — no mh-preview-${ISSUE} containers remain"
fi
```

Everything after this block (PR discovery, `needs-discussion` guard, `mark-ready`, `merge`,
`set-status done`, closing comment) is unchanged — those steps' existing `ERROR:` + `exit 1`
handling is the correct, intentional hard-fail surface for the close intent itself.

## Alternatives Considered

1. **Chosen: soften only the stale-container assertion to WARNING + continue, leave everything
   else in `close-preview` unchanged.** Minimal, matches the issue's stated ask exactly, keeps
   the genuine close-intent hard-fails (PR discovery/merge/needs-discussion) intact, fits
   `size: S`.
2. **Broad existence-check across the whole node (per #222's literal pattern): skip all teardown
   *and* PR-merge logic when no target-specific preview infra is detected.** Rejected per
   Brainstorming Q&A #1 — would let a close silently "succeed" without ever discovering or
   merging the PR, a functional regression far outside this ticket's scope.
3. **Add explicit `push-and-pr` logic to mark PRs ready specifically when preview failed.**
   Rejected — `push-and-pr` already opens every PR as `--draft` unconditionally (not
   conditioned on preview outcome), and `close-preview`'s own `mark-ready` call (line 240,
   unchanged by this ticket) already promotes the PR out of draft once teardown can no longer
   short-circuit ahead of it. A separate `push-and-pr` change would duplicate that responsibility
   for no behavioral gain.

## Open Questions (Non-blocking)

- None.

## Assumptions

- The self-repo (`omniscient/dark-factory`) will continue to have no `backend/`/`frontend/`
  directories or Dockerfiles, so `close-preview` will keep hitting the "nothing to tear down"
  path on every self-repo close intent for the foreseeable future — this fix makes that the
  steady-state success path rather than a recurring failure.
- `docker ps`/`docker compose` remaining reachable (the socket/proxy being up) is out of scope to
  re-verify here; if the `docker ps` call itself fails, the existing `2>/dev/null || true` already
  yields an empty `STALE` and this fix does not change that behavior.
- The observed #46 failure and this teardown block are the same failure point — inferred from the
  post-mortem's phase/timestamp match and "before the merge step" framing in the issue, not from
  a captured stack trace (the post-mortem comment itself reports no diagnostic output was
  surfaced from the run).

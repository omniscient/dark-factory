# Scheduler End-Gate: Refuse Auto-Merge and Grace-Advance on a Non-Cloud Endpoint (#585)

**Issue:** #585

**Status:** re-refined 2026-11-25 after an operator rejection of the first spec (commit
`6991292`, no longer reachable — the branch was reset). The rejection and nine numbered owner
decisions are recorded verbatim in the issue body; this spec implements them as binding
constraints, not as options to re-evaluate.

---

## Why the first spec was rejected, and what changed

The first spec made the scheduler's end-gate scan `request-ledger.jsonl` and
`run-records/*.json` for a per-issue `hosting_location`/`payload_class` trail, and auto-merge
when that scan found nothing. An independent operator review rejected it: both stores sit on
`dark_factory_state`, a volume every run container mounts **read-write**. A local-routed worker
could delete or corrupt its own evidence before the scheduler ever looked, and "ledger absent
or zero rows → proceed" made that the *default*, fail-open outcome. The review also found gaps
the first spec didn't cover at all (grace-timer advance, several merge/close paths) and
mechanical bugs (wrong contract invariant numbers, no tests, the close retry counter consumed
by a refusal, a worker-controlled value flowing into a signed comment).

The owner's reply resolves this by naming a signal that **is** tamper-resistant and was already
sitting unused: the scheduler's own `FACTORY_MODEL_ENDPOINT`, resolved against its read-only
registry mount — the exact resolution `model_endpoint.scheduler_preflight()` already performs
at boot. That single fact (owner decision 1) removes the need for any per-issue evidence at
all: this spec reads **no run-writable file**. It is a smaller change than the first draft, not
a bigger one.

## Overview / Problem Statement

`docs/hybrid-execution-contract.md` Invariant 7 (§4.3, restated at §4.11 decision 5): neither a
local-pinned dispatch's output nor any `local_only`-classed payload may ever auto-merge; the
merge is always a human action. Nothing in `scheduler.sh` enforces this today — `dispatch_close`
(the sole place `Close issue #N` is dispatched) and the grace-timer advance functions
(`spec_advance_check`, `plan_advance_check`) have no awareness of model-endpoint hosting at all.

This spec adds one scheduler-side, instance-wide gate: **while this instance's
`FACTORY_MODEL_ENDPOINT` is set and does not resolve to a definite `cloud` registry entry,
every automated merge dispatch and every grace-timer auto-advance in scope is refused,
regardless of which issue it is.** When `FACTORY_MODEL_ENDPOINT` is unset, behavior is
byte-identical to today — the gate's code path is not even reached.

## Requirements

Restating the issue's nine owner decisions as spec requirements (decision numbers kept for
traceability):

1. **Signal.** Resolve hosting via `model_endpoint.scheduler_preflight(SOURCE_REGISTRY_DIR,
   endpoint_id)` — the scheduler's read-only `/etc/dark-factory/model-registry` mount, not the
   run container's staged (writable) copy. `endpoint_id` is `$FACTORY_MODEL_ENDPOINT`.
   - Unset (`""`) → gate is a no-op; zero file reads; identical to current behavior.
   - Set, resolves to `hosting == "cloud"` → proceed (the only passing case).
   - Set, resolves to anything else — `hosting == "local"`, `hosting is None` (unknown id, or
     `RegistryError` from an unreadable/invalid registry file) — → refuse, instance-wide, for
     every issue this poll touches.
   - **Accepted residual** (owner decision 1, stated as acceptable, not a gap to fix here): the
     gate has no memory. If an instance is switched from a non-cloud endpoint back to a cloud
     one (or back to unset), work merged/advanced-adjacent to the earlier non-cloud window is no
     longer blocked. A future ticket may revisit this; it is explicitly out of scope now.
2. **No run-writable file may ever be the reason a merge/advance proceeds.** This spec's design
   satisfies this trivially: it reads no run-writable file at all (no ledger, no run records,
   no `current-run.json`, no `runs.jsonl`). If a future ticket adds a supplementary per-issue
   check on top of this gate, it must only ever *add* a refusal, never relax this one.
3. **Legacy ledger rows** (pre-#558, no `hosting_location` field) are not consulted by this
   design and therefore cannot be a block — moot under requirement 2, recorded for traceability.
4. **Grace-advance is in scope.** Both `spec_advance_check` and `plan_advance_check` get the
   same guard as the merge paths, not just `dispatch_close`.
5. **Out of scope, filed separately:** `Fix main`/`Audit deps` self-merge (#615); an
   `close-preview`-node-internal check (#616). This spec does not touch
   `workflows/archon-dark-factory.yaml`, `main_red_fixer.py`, or `deps_fixer.py`, and has no
   dependency on #586/#587/#611/#559.
6. **Refusal mechanics:**
   - The guard runs *before* `evaluate_stop` inside `dispatch_close`, so a refusal never
     consumes the `N:close` breaker counter.
   - Dedup is by marker comment (not label-only), so a label call that fails doesn't cause a
     fresh comment next poll.
   - The comment contains no worker-controlled free text. (This design shows no `run_id` or any
     other per-issue worker-sourced value — see requirement 1 — so this is satisfied by
     construction; if a future change ever does embed a `run_id`, it must be validated against
     `run_record.py`'s `_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")` first.)
   - On refusal: apply `ready-for-human`; leave the PR/ticket otherwise exactly where it was
     (board status untouched, breaker not tripped — nothing failed).
   - The comment ends with the scheduler and factory identity footers, so the scheduler's own
     comment readers (`has_new_comment_after_report`, `get_new_comments`) never take the
     refusal for human feedback. Without them a later poll would reset a retry counter, remove
     the gate label and re-dispatch Refine/Plan, or hand the refusal text to the comment
     classifier.
7. **Tests** are hermetic, failing-first, in `tests/test_scheduler.sh`'s existing stub-CLI style,
   covering: endpoint-local refused on APPROVED, on MERGE, and on each grace path; endpoint set
   but unresolvable (unknown id, unreadable/invalid registry, resolver failure, resolver exit 0
   with unexpected output) refused the same way; endpoint unset leaves dispatch unchanged with
   the ledger present, absent and legacy; a second poll with the same refused state posts no
   second comment and leaves retry counters unchanged. New Python (the CLI subcommand below)
   gets pytest coverage alongside the existing `test_provider_model_endpoint.py` suite. The
   full list is in the "Test plan" section.
8. **Docs:** `docs/scheduler.md` (the "Grace-timer auto-advance" and "End gate and auto-merge
   with `direct-to-pr`" subsections) and `docs/triage-labels.md` get updated. Contract citations
   in this spec and its docs updates are Invariant 7 (no-auto-merge) and Invariant 10
   (unattributable correlation) — the first spec cited Invariant 4 for both, which is wrong.
   Every `scheduler.sh` line number in this spec was re-read against current `main` on
   2026-11-25 (see citations throughout) rather than carried over from the superseded spec.
   Concretely:
   - `docs/scheduler.md`, the two subsections above (at lines 547 and 576 on `main`): describe
     the guard, state the accepted residual from requirement 1, and refresh every
     `scheduler.sh:` line reference that shifts because of this change (on `main` those are at
     `docs/scheduler.md` lines 549-550 and 587).
   - `docs/triage-labels.md`, both `ready-for-human` rows (lines 10 and 62 on `main`): add
     "also applied by the scheduler when it refuses an automated merge or advance (#585)".
   - Both documents say how a human clears a refusal: merge the PR by hand, or move the card
     and remove the gate label, and then remove `ready-for-human`. Removing only
     `ready-for-human` while the instance is still non-cloud is refused again on the next poll
     that reaches the guard.
9. **Safety.** This change only ever adds a refusal; it does not relax any existing gate,
   breaker, budget, or label semantic, and does not touch `deploy/**`, `config/config.yaml`
   defaults, or tool allow/deny lists. This ticket is itself never `direct-to-pr`.
   `scheduler.sh` is a reviewed hotspot — a human signature is expected on the resulting PR
   before merge, same as any other `scheduler.sh` change.

### Scope correction found during this pass (not in the original issue or owner decisions)

Both grace-timer functions only run their elapsed/advance branch **inside**
`has_direct_to_pr_label "$item"` (`scheduler.sh:270`/`spec_advance_check`,
`scheduler.sh:311`/`plan_advance_check` — confirmed live against `main`, 2026-11-25). A ticket
without `direct-to-pr` never reaches a grace-advance at all; its spec/plan gate can only be
cleared by a human removing the pending-review label. So "grace-advance in scope" (requirement
4) concretely means: **this gate only has any effect on grace-advance for `direct-to-pr`
tickets** — which is exactly the population the "auto-advance with nobody approving" risk
described in §4.3/Invariant 7 is about. No `direct-to-pr` ticket is exempted by this spec; no
non-`direct-to-pr` ticket is newly affected.

### Grace-advance refusal mechanics (clarified this pass; not explicit in the owner decisions)

Decision 6 describes refusal in terms of a merge. Asked whether a refused grace-advance should
mirror that (label + comment) or silently skip the poll, the product-owner answer
is to mirror it exactly, for two reasons: (a) because of the scope correction above, a
refused grace-advance only ever happens to a `direct-to-pr` ticket — precisely the one case
where no human is otherwise in the loop at this gate, so silence would leave it stuck
indefinitely with only a log line to explain why; (b) `ready-for-human` is already in
`SKIP_LABELS` (`scheduler.sh:9`) and both grace functions return early on `has_skip_label`
(`scheduler.sh:251`/`287`), so applying it on refusal is self-dedupeing — the same "for free"
mechanism the merge path already relies on — and the marker-comment dedup only needs to cover
an operator manually removing the label while the instance is still non-cloud. The
`spec-pending-review`/`plan-pending-review` label is left in place (the ticket really is still
mid-review); only the auto-advance is refused, not the human "Approve" path, which does not go
through `scheduler.sh` at all.

## Architecture / Approach

### 1. A read-only, instance-wide hosting check

Add a new CLI subcommand next to the existing `model-endpoints show`/`model-endpoints stage`
pair in `scripts/factory_core/providers/cli.py` (`_model_endpoints_show`/`_model_endpoints_stage`,
registered at `cli.py:433-444`):

```python
def _model_endpoints_hosting_check(args):
    # #585: scheduler-side, read-only-mount resolution — never the staged (run-writable) copy.
    from factory_core.providers import model_endpoint
    problems, hosting = model_endpoint.scheduler_preflight(
        model_endpoint.SOURCE_REGISTRY_DIR, args.endpoint_id
    )
    if hosting == "cloud":
        print("hosting=cloud")
        return
    if hosting == "local":
        print(f"hosting=local reason=endpoint '{args.endpoint_id}' hosting_location=local")
    else:
        reason = "; ".join(problems) if problems else f"endpoint '{args.endpoint_id}' not found in registry"
        print(f"hosting=unknown reason={reason}")
    sys.exit(1)
```

registered as `model-endpoints hosting-check --endpoint-id ID`. It calls the exact function
decision 1 names (`scheduler_preflight`), against the exact path decision 1 names
(`SOURCE_REGISTRY_DIR`, the scheduler's read-only bind mount — `model_endpoint.py:33`, mounted
at `deploy/docker-compose.yml:96`), and classifies only `hosting == "cloud"` as a pass, exiting
0 and printing exactly `hosting=cloud` only in that case. The `reason=` text is for an operator
who runs the subcommand by hand; the scheduler never copies it anywhere. The posted comment
shows only one of two fixed tokens, `hosting=local` or `hosting=unresolved`, chosen by the bash
predicate below, so requirement 6's "no worker-controlled free text" holds without needing the
`run_id` regex guard at all (nothing here is a run id), and no registry-validation string, path
or Python traceback can reach a scheduler-signed comment.

### 2. One bash predicate, called from three places

In `scheduler.sh`, add:

```bash
# #585 / hybrid-execution-contract Invariant 7: refuse automated merge/advance while this
# instance's own model endpoint isn't confirmed cloud-hosted. Scheduler-side, instance-wide,
# reads only the read-only registry mount — never a run-writable file (decision 2).
cloud_endpoint_confirmed() {
  if [ -z "${FACTORY_MODEL_ENDPOINT:-}" ]; then
    return 0
  fi
  local out="" rc=0
  out=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints hosting-check \
       --endpoint-id "$FACTORY_MODEL_ENDPOINT" 2>/dev/null) || rc=$?
  if [ "$rc" -eq 0 ] && [ "$out" = "hosting=cloud" ]; then
    return 0
  fi
  case "$out" in
    "hosting=local "*) LOCAL_ROUTING_REFUSAL_REASON="hosting=local" ;;
    *) LOCAL_ROUTING_REFUSAL_REASON="hosting=unresolved" ;;
  esac
  echo "[$(date -u +%FT%TZ)] local_routing_gate=refuse ${LOCAL_ROUTING_REFUSAL_REASON} rc=${rc}" >&2
  return 1
}
```

The `[ -z ... ]` short-circuit means the unset case (the common case today) never spawns a
subprocess and never touches new code at all — requirement 1's "byte-identical to today" is
structural, not just behavioral. When the endpoint is set, the only passing outcome is exit
status 0 **and** standard output equal to exactly `hosting=cloud`. Everything else is refused: a
local entry, an unknown id, an unreadable or invalid registry, a missing subcommand, a CLI crash,
any non-zero exit, and exit 0 with empty or unexpected output. Exit status alone never means
cloud. This is deliberately fail-closed, consistent with requirement 9 (more conservative only)
and cheap to be wrong about, since the fallback is always "a human can still merge/advance by
hand." The `out=$(...) || rc=$?` form keeps a failing resolver from ending the scheduler under
`set -euo pipefail` and from firing the ERR trap. The resolver's standard error is discarded and
its `reason=` text is not kept: the scheduler log line carries the fixed token and the exit
status, and an operator who wants the detail runs `model-endpoints hosting-check` by hand.

The predicate resolves on every call, not once at boot and not once per poll, so a registry
that changes or becomes unreadable after boot is refused from the next guard call on.

A second helper posts the refusal, reused by all three call sites:

```bash
# Idempotent (marker-dedup) refusal: same pattern entrypoint.sh/board.py already use for
# post-or-update comments, applied here for the first time inside scheduler.sh itself.
refuse_local_routing() {
  local issue_num="$1" action="$2"  # action: "merge" | "spec-advance" | "plan-advance"
  python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" --add ready-for-human || true
  local f_sched f_fact
  f_sched=$(python3 "$FACTORY_CORE_CLI" marker scheduler 2>/dev/null) \
    || f_sched="*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*"
  f_fact=$(python3 "$FACTORY_CORE_CLI" marker factory 2>/dev/null) \
    || f_fact="*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*"
  local body
  body=$(cat <<EOF
## Scheduler — automated ${action} refused

This instance's selected model endpoint is not confirmed cloud-hosted
(\`${LOCAL_ROUTING_REFUSAL_REASON}\`). Per \`docs/hybrid-execution-contract.md\` Invariant 7,
work is never auto-merged or auto-advanced while that's true — a human must take this action
directly.

---
${f_sched}
${f_fact}
<!-- df-local-routing-refusal -->
EOF
)
  local tmpfile
  tmpfile=$(mktemp /tmp/local-routing-refusal-XXXXXX.md) || return 0
  echo "$body" > "$tmpfile"
  python3 "$FACTORY_PROVIDERS_CLI" tracker comment --id "$issue_num" \
    --marker "<!-- df-local-routing-refusal -->" --body-file "$tmpfile" 2>/dev/null || true
  rm -f "$tmpfile"
}
```

(Mirrors `entrypoint.sh`'s `post_or_update_comment()` / `board.py`'s `post_or_update_comment()`
— `tracker comment --marker` upserts by finding an existing comment containing the marker and
PATCHing it, so a second refusal on the same issue never creates a second comment.)

The two footer lines are load-bearing, in the way `deps_fixer.footer()` already documents for
its own comments. Every factory comment is posted from the same account, so the scheduler tells
its own comments from human ones by footer only:

- `has_new_comment_after_report` (`scripts/scheduler_lib.sh:85-108`) treats any comment after
  the last Refinement Pipeline report that does not match `BOT_RE` as reviewer feedback. Both
  grace functions test that **before** the grace branch (`scheduler.sh:253-269`,
  `scheduler.sh:289-305`), and on feedback they reset the phase retry counter, remove the gate
  label and dispatch `Refine issue #N` / `Plan issue #N`. The scheduler footer matches `BOT_RE`,
  so a refusal is never that feedback.
- `get_new_comments` (`scheduler.sh:773-796`) treats everything after the last
  `Posted by <product> Dark Factory` comment as new human input for the comment classifier. The
  factory footer makes the refusal that last comment, so neither the refusal text nor the
  `MERGE` comment that led to it is classified again.

Each footer falls back to the literal text `identity.py` produces if the `marker` call fails, so
a failed call cannot post a refusal without footers. The label call and the comment call are
both best-effort (`|| true`), and a failed `mktemp` returns without posting: the grace call
sites are not inside an `if`, so an unguarded failure there would end the scheduler under
`set -e`. None of these failures lets the merge or advance proceed; the caller has already
taken the refusal branch.

With `ready-for-human` removed by a human while the instance is still non-cloud, the next poll
behaves as follows. On a grace path the guard is reached again, the advance is refused again and
the same marker comment is updated in place; no retry counter is reset, no gate label is removed
and no Refine/Plan run is dispatched. On the end-gate `APPROVED` path the merge is refused again
the same way. On the comment-classifier path nothing happens until a human posts a new comment.

**Call site 1 — `dispatch_close` (`scheduler.sh:368-380`; its header comment is at 359-367).** Insert between the
`is_issue_running` check and the `evaluate_stop` call, per requirement 6 (never consumes the
close retry counter):

```bash
dispatch_close() {
  local issue_num="$1"
  is_issue_running "$issue_num" && return 1
  if ! cloud_endpoint_confirmed; then
    refuse_local_routing "$issue_num" "merge"
    return 1
  fi
  local eval_result
  eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")
  case "$eval_result" in
    *stopped=true*)
      trip_to_blocked "$issue_num" "close" "retry limit of ${MAX_RETRIES} reached for close/merge"
      return 1
      ;;
  esac
  dispatch "Close issue #${issue_num}"
}
```

Both of `dispatch_close`'s callers — `end_gate_check`'s `APPROVED` branch
(`scheduler.sh:336-341`) and `stage_review_triage`'s `MERGE` branch (`scheduler.sh:1159-1163`) —
are covered by this one change, and the existing "AB9" static-drift-lock test
(`tests/test_scheduler.sh:2416-2427`, asserting exactly one raw `Close issue` dispatch and
exactly two call sites into `dispatch_close`) continues to guarantee no future third dispatch
path bypasses it.

**Call sites 2 & 3 — `spec_advance_check` (`scheduler.sh:248-282`) / `plan_advance_check`
(`scheduler.sh:284-323`).** Each gets the guard immediately inside its
`elapsed >= *_GRACE_MINUTES` branch, before the label-removal/`set_board_status` pair:

```bash
    if [ -n "$elapsed" ] && [ "$elapsed" -ge "$SPEC_GRACE_MINUTES" ]; then
      if ! cloud_endpoint_confirmed; then
        refuse_local_routing "$issue_num" "spec-advance"
      else
        echo "[$(date -u +%FT%TZ)] spec_auto_advance issue=#${issue_num} elapsed=${elapsed}m grace=${SPEC_GRACE_MINUTES}m action=advance_to_refined"
        python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" --remove "spec-pending-review" || true
        set_board_status "$issue_num" "$FACTORY_STATUS_REFINED" || true
      fi
    else
      echo "[$(date -u +%FT%TZ)] spec_grace_window issue=#${issue_num} elapsed=${elapsed:-unknown}m grace=${SPEC_GRACE_MINUTES}m action=waiting"
    fi
```

(`plan_advance_check` gets the structurally identical change around `scheduler.sh:314-318`,
with `action="plan-advance"` and `$FACTORY_STATUS_READY`.) `refuse_local_routing` applies
`ready-for-human`, which both functions already return early on via `has_skip_label`
(`scheduler.sh:251`/`287`) — so a refused ticket is not re-evaluated by this path on the next
poll, same dedup-for-free mechanism the merge path gets from `SKIP_LABELS` already containing
`ready-for-human` (`scheduler.sh:9`). The existing `spec-pending-review`/`plan-pending-review`
label is left untouched, so the ticket still reads as "mid-review," not "ready to merge."

### 3. Why no ledger/run-record read anywhere

This is the main structural difference from the rejected spec. Decision 1 makes the
scheduler's own env var + read-only registry mount the entire signal; decisions 2–3 are
guardrails on a *supplementary* per-issue check this design does not add (the "accepted
residual" framing in decision 1 is the owner explicitly choosing not to require one). Dropping
the ledger scan also removes every concrete defect the operator review raised against the first
draft: there is no run-writable file whose absence or corruption can flip the gate's answer, and
no `run_id` ever reaches a posted comment, so the safe-run-id-regex guard (requirement 6) is
satisfied by never needing it.

## Test plan

All tests are hermetic and written failing first. The bash tests go in `tests/test_scheduler.sh`
in its existing style: `scheduler.sh` is sourced with `SCHEDULER_SOURCE_ONLY=1` and `python3` is
a shell function that logs its arguments and returns a canned output and exit status.

Both helpers (`cloud_endpoint_confirmed`, `refuse_local_routing`) are defined beside
`dispatch_close`, before the `SCHEDULER_SOURCE_ONLY` guard (`scheduler.sh:1477`), so the tests
can call them and the three call sites directly.

The `local` branch is reached only by stubbing the `hosting-check` call. No test and no code in
this change bypasses or edits `_NO_LOCAL_DISPATCH`.

1. **Refusal, per resolver outcome.** Stub cases for `model-endpoints hosting-check`:
   `hosting=local reason=...` with exit 1; `hosting=unknown reason=...` with exit 1 for an
   unknown id; the same for an unreadable or invalid registry; non-zero exit with empty output
   (resolver crash or missing subcommand); exit 0 with unexpected output; exit 0 with empty
   output. Each case is run on all four paths: end-gate `APPROVED`, classifier `MERGE`, spec
   grace and plan grace.
2. **Assertions for every refusal.** No `dispatch` call; no `evaluate_stop` call; no
   `trip_to_blocked` call; no `set_board_status` call; no `tracker label --remove`; one
   `tracker label --add ready-for-human`; one `tracker comment --marker
   "<!-- df-local-routing-refusal -->"` call. The body file passed to that call contains the
   fixed token (`hosting=local` or `hosting=unresolved`), both footers and the marker, and none
   of the stubbed `reason=` text.
3. **Second poll.** Repeating the same refused poll leaves `STATE_FILE` byte-identical, makes no
   `tracker add-comment` call, and makes the same single `tracker comment --marker` call (an
   update in place, never a new comment).
4. **The refusal is not human feedback.** With a comment list whose last entry is the refusal
   body, `has_new_comment_after_report` returns `no` and `get_new_comments` returns `[]`.
5. **`ready-for-human` removed by a human on a non-cloud instance.** With the refusal comment as
   the latest comment, the item no longer carrying `ready-for-human`, and the resolver still
   non-cloud, the next poll of `spec_advance_check` and of `plan_advance_check` resets no retry
   counter, removes no gate label and dispatches neither `Refine issue` nor `Plan issue`. It
   refuses again and updates the same marker comment.
6. **A label call that fails.** With `tracker label --add` stubbed to fail, the refusal still
   posts its marker comment, and the next poll behaves as in tests 3 and 5.
7. **Cloud.** With the stub printing `hosting=cloud` and exiting 0, all four paths behave
   exactly as they do with the endpoint unset.
8. **Unset (the byte-identical pin).** With `FACTORY_MODEL_ENDPOINT` unset or empty, run all
   four paths with a request ledger present, absent, and present with legacy rows that have no
   `hosting_location`. The stub log contains zero `hosting-check` calls, no ledger or run-record
   path is opened, and the existing sections G, I and AB of `tests/test_scheduler.sh` pass
   unmodified. AB9 still counts one raw `Close issue` dispatch and two `dispatch_close` callers.
9. **Pytest for the new subcommand**, in `tests/test_provider_model_endpoint.py`: a cloud entry
   prints exactly `hosting=cloud` and exits 0; a local entry, an unknown id, a missing registry
   and an invalid registry each exit 1 and never print `hosting=cloud`.

## Files touched

- `scheduler.sh`: the two helpers and the three guarded call sites.
- `scripts/factory_core/providers/cli.py`: the `model-endpoints hosting-check` subcommand.
- `tests/test_scheduler.sh` and `tests/test_provider_model_endpoint.py`: the tests above.
- `docs/scheduler.md` and `docs/triage-labels.md`: the updates in requirement 8.

Nothing else. In particular not `deploy/**`, `.github/workflows/*`, `config/config.yaml`,
`workflows/archon-dark-factory.yaml`, `main_red_fixer.py`, `deps_fixer.py`, `model_endpoint.py`,
`.factory/adapter.yaml` or any tool allow/deny list.

## Alternatives Considered

1. **Per-issue ledger/run-record evidence scan (the rejected first draft).** Rejected by the
   operator review for being fail-open on a run-writable input; would also require re-deriving
   a per-issue correlation story (`run_id`→`issue_number`) that decision 1 makes unnecessary.
   Not revisited here per instruction from the issue body.
2. **Cache the hosting resolution once per poll loop iteration** (compute at the top of
   `scheduler.sh`'s main loop, store in a global, have the three call sites read the global)
   instead of re-invoking the CLI at each call site. Rejected for this pass: the registry file
   is tiny and local, the scheduler already re-reads the same mount once per *dispatch*
   (`model-endpoints stage`, called from `dispatch()`) without a caching layer
   (`scheduler_preflight` itself runs only once, at boot, from `scheduler.sh:45`), resolving on
   every call is what catches a registry that changes after boot, and the three call sites run at different points in a
   poll (not necessarily all three per item), so a single hoisted check would need its own
   plumbing through every call path for a saving that isn't measurable at current poll volumes.
   Could be revisited if profiling ever shows the registry read is hot.
3. **A single shared helper for all three call sites** (one function that both decides and
   posts, parameterized by action/board-state) instead of a predicate (`cloud_endpoint_confirmed`)
   plus a shared poster (`refuse_local_routing`). Rejected: `dispatch_close` needs to `return 1`
   without touching board status or `trip_to_blocked`; the grace functions need to fall into an
   `else` that does the normal advance. Splitting predicate from action keeps each call site's
   control flow a small, local diff instead of threading more parameters through one function.
4. **New label instead of reusing `ready-for-human`.** Rejected: `ready-for-human` already means
   "a human must act on this PR/ticket directly, scheduler stop re-evaluating it" and is already
   in `SKIP_LABELS` — introducing a second label with the same effect would need its own
   `docs/triage-labels.md` entry and its own `SKIP_LABELS` wiring for no behavioral gain.

## Safety invariants (hard requirements)

- No existing gate, breaker, budget, or label semantic is relaxed by this change (requirement 9).
- The new code path is unreachable when `FACTORY_MODEL_ENDPOINT` is unset — today's default on
  every instance that hasn't opted into #557/#558's model-endpoint registry.
- `dispatch_close`'s refusal never touches `evaluate_stop`/the `N:close` breaker counter
  (requirement 6); grace-timer refusal never calls `set_board_status` or removes the
  pending-review label.
- `trip_to_blocked` is never called on refusal — nothing failed; board status is left exactly
  where it was.
- The posted comment contains zero worker-controlled or per-issue-correlated content — only
  fixed template text, one of two fixed hosting tokens and the identity footers.
- Only exit status 0 together with the exact output `hosting=cloud` lets a merge or advance
  proceed when an endpoint is selected; a failing resolver neither ends the scheduler nor is
  read as cloud.
- This ticket's own PR is never `direct-to-pr` and requires a human signature
  (`scheduler.sh` is a reviewed hotspot).

## Open Questions (non-blocking)

- Should a future ticket add the per-issue supplementary check decisions 2–3 anticipate (to
  close the "switch back to cloud un-blocks retroactively" residual from decision 1)? Decision 1
  explicitly accepts the residual for now; left for a future ticket if ever revisited.
- Should `cloud_endpoint_confirmed`'s result be cached per poll iteration rather than
  re-resolved at each call site? See Alternative 2 — revisit only if profiling shows it matters.

## Assumptions (flagged)

- Read live against this branch's merge-base with `main` on 2026-11-25. `model_endpoint.py`'s
  `scheduler_preflight`/`SOURCE_REGISTRY_DIR`, `scheduler.sh`'s `dispatch_close`/
  `spec_advance_check`/`plan_advance_check`/`SKIP_LABELS`/`trip_to_blocked`, and the
  `tracker label`/`tracker comment --marker` CLI surface are assumed stable at the line numbers
  cited above; re-verify before implementing if `main` has moved significantly since this date.
- `scheduler_preflight()`'s `problems` list is used only for the `reason=` text the subcommand
  prints for an operator. The scheduler discards it, so what `_validate_entry` echoes cannot
  reach a posted comment.
- On `main`, `preflight --side scheduler` (`scheduler.sh:45`) exits the scheduler at boot for
  any non-cloud selection (`_NO_LOCAL_DISPATCH`, unknown id, invalid registry). The refusal
  branches are therefore reachable only when the registry changes or becomes unreadable after
  boot, which per-call resolution catches, and in tests by stubbing. No bypass of
  `_NO_LOCAL_DISPATCH` is added.
- No helper-dispatch instance exists yet that actually sets `FACTORY_MODEL_ENDPOINT` to a local
  entry in production (#587 is the first), so this gate is exercised only by test fixtures until
  #587 lands — consistent with the first spec's assumption, still true today.

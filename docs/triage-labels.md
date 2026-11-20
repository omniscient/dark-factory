# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation. The scheduler skips the ticket at every stage (refine, plan, dispatch, retry, rescue, orphan sweep, grace advance), and epic autopilot will not advance it |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`needs-info` and `wontfix` are human-only triage roles: no factory code reads or applies them. `scripts/bootstrap_target.sh` creates `needs-info` on a new target but not `wontfix` (on GitHub it is a default repo label).

## Relationship to factory workflow labels

The factory uses separate labels for its execution pipeline (`spec-pending-review`, `plan-pending-review`). These are not triage labels — they track workflow state after triage is complete. Progress between phases is a **board status**, not a label. A typical issue flows:

1. `needs-triage` (intake)
2. Triaged → `ready-for-agent` or `ready-for-human`
3. Backlog + `ready-for-agent` → the factory refines it and applies `spec-pending-review`
4. Spec approved → board status **Refined**, `spec-pending-review` removed → the factory plans it and applies `plan-pending-review`
5. Plan approved → board status **Ready**, `plan-pending-review` removed → **In Progress** → **In Review** → **Done**

Approving a spec or plan means moving the board item and removing the gate label; an item that still carries a `*-pending-review` label is skipped by the plan and refine stages. A `spec-approved` label exists (`bootstrap_target.sh` creates it) but nothing reads or applies it: adding it to an issue has no effect.

Do not conflate the two label sets.

## Opt-in refinement gate

The backlog scheduler auto-refines Backlog issues **only when they carry the `ready-for-agent` label or the `direct-to-pr` label** (see Workflow flags). Unlabelled Backlog items are left for triage and are never automatically dispatched to the refinement pipeline.

This prevents new issues from being auto-refined during the labelling window (the root cause of the `omniscient/markethawk#159` dispatch loop, which predates this repo's extraction). Apply `ready-for-agent` to a Backlog issue once it is triaged and fully specified for agent work.

The `spec-pending-review` re-refine-on-feedback path is unaffected — it handles feedback on an already-refined issue and does not require an opt-in label.

## Workflow flags

These labels change scheduler *behavior* for a ticket already past triage. They are not
triage roles — apply them after the issue is `ready-for-agent` or `direct-to-pr` (which
implies entry and straight-through flow).

| Label | Meaning |
|-------|---------|
| `spec-pending-review` | Spec posted, waiting for human approval. Auto-advances after `SPEC_GRACE_MINUTES` **only** if the ticket also carries `direct-to-pr`; otherwise it waits indefinitely |
| `plan-pending-review` | Plan posted, waiting for human approval. Auto-advances after `PLAN_GRACE_MINUTES` **only** if the ticket also carries `direct-to-pr` and is below the dispatch ceiling (`is_below_ceiling` in `scripts/scheduler_lib.sh`); otherwise it waits indefinitely |
| `direct-to-pr` | **Opt-in**: ticket is admitted to the pipeline _and_ runs straight-through. Spec and plan checkpoints become async (grace-windowed); PR approval is the single end gate. Combine with `SPEC_GRACE_MINUTES=0` / `PLAN_GRACE_MINUTES=0` for pure auto-flow. |
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. The `FACTORY_MANIFEST_LABEL` override itself is rejected if its case-folded value *contains* `ready-for-agent`, `-pending-review`, `direct-to-pr`, or the case-folded, non-empty `DIRECT_TO_PR_LABEL` env value (when set) — substring containment, matching the scheduler's own unanchored `grep -qi` label matching — so it can never be set to smuggle an issue into an existing gate or escalation state. |

**Operator note on renaming `direct-to-pr`:** the canonical source for this label is `config/config.yaml`'s `direct_to_pr.label`, and that value is materialized into the scheduler process's env (`DIRECT_TO_PR_LABEL`) only by `scheduler.sh`'s `read_config` — it is not automatically propagated to the dispatched run container. If you rename `direct_to_pr.label` in `config.yaml`, you must *also* set `DIRECT_TO_PR_LABEL` explicitly in the run container's env for the `handoff.py intake` deny-list above to recognize the renamed label. Run containers read `<PROJECT_DIR>/.archon/.env`, not the scheduler's `deploy/instance.env`, and `run-compose.yml` does not pass `DIRECT_TO_PR_LABEL` through, so `.archon/.env` is where it has to go. Without that, `handoff.py` still denies the literal string `direct-to-pr` but silently fails to deny the renamed label, and an operator-renamed `direct_to_pr.label` could be smuggled through as a `FACTORY_MANIFEST_LABEL` override.

## Factory-applied and factory-read labels

Every label the scheduler, gates or phase agents apply or branch on. `scripts/bootstrap_target.sh` (the `LABELS` array) creates all of these on a new target except where noted. Labels already described above are listed here only for completeness.

| Label | Applied by | Read by / effect |
|-------|-----------|------------------|
| `needs-discussion` | Refine, plan, validate (blast-radius block), conformance and code-review commands when they block; the circuit breaker when it trips | **Halts all automation for the ticket.** In the scheduler's skip list for every stage, and in the refine/plan skip lists. Remove it to resume |
| `ready-for-agent` | Humans (triage); epic autopilot on an epic's open children | Opt-in gate for auto-refinement of Backlog items |
| `ready-for-human` | Humans | Scheduler parks the ticket at every stage, including refine and plan; epic autopilot will not advance it |
| `epic` | Humans | Scheduler never dispatches the epic itself; epic autopilot works on its children |
| `spec-pending-review`, `plan-pending-review` | The run DAG, after the spec/plan artifact is pushed | Gate labels; see Workflow flags |
| `direct-to-pr` | Humans; epic autopilot | Straight-through flow; see Workflow flags |
| `above-ceiling` | Scheduler, when a Ready item is above the dispatch ceiling (size XL, or size M with a ceiling keyword in the title). The item is also moved to Blocked | Stops the ceiling comment and board move from repeating each poll. Name is `dispatch_ceiling.label` in `config/config.yaml` (env `ABOVE_CEILING_LABEL`) |
| `size: S`, `size: M`, `size: L`, `size: XL` | Humans; `commands/ceiling-revisit.md` on the tickets it files | Dispatch ceiling, plan grace auto-advance, epic autopilot eligibility, scorecard buckets. Note the space after the colon |
| `priority: must-have`, `priority: should-have`, `priority: could-have` | Humans; `commands/ceiling-revisit.md` | Epic autopilot ranks candidate epics by it (`_priority_rank` in `epic_autopilot.py`). Not read by `scheduler.sh` |
| `no-autopilot` | Humans | Per-ticket veto for epic autopilot. Name is `epic_autopilot.opt_out_label` (env `EPIC_AUTOPILOT_OPT_OUT_LABEL`) |
| `scope-spillover` | Conformance gate, together with `needs-triage`, on tickets it files for out-of-scope changes | Name is `conformance.backlog_label` in `config/config.yaml` |
| `needs-triage` | Conformance spillover tickets; `handoff.py intake` | Triage role (top table) |
| `manifest-intake` | `handoff.py intake` | See Workflow flags. **Not created by `bootstrap_target.sh`** |
| `regression` | Smoke gate, on the main-red regression ticket it files | Smoke gate finds and closes its own open regression tickets by this label |
| `factory-regression` | Circuit breaker, together with `needs-discussion`, when it trips | Scorecard counts issues carrying it (env `FACTORY_REGRESSION_LABEL`) |
| `merged-with-edits` | Humans | Scorecard manual override marking a factory PR as merged with human edits (env `FACTORY_EDITS_LABEL`). **Not created by `bootstrap_target.sh`** |
| `enhancement` | `commands/ceiling-revisit.md` on the tickets it files | Not read. **Not created by `bootstrap_target.sh`** (a GitHub default label) |
| `cost-report-lost` | Created by `bootstrap_target.sh`; no apply site was found in this repo's scripts, commands or workflow | — |
| `spec-approved`, `needs-info` | Created by `bootstrap_target.sh`; never applied by the factory | Never read |

## Labels with no factory effect

Every other label in the tracker is for humans only: no factory code reads or applies it.
As of 2026-11-17 in this repo these are:

- **Topic tags:** `Dark Factory`, `bug`, `documentation`, `foundation`, `infrastructure`,
  `observability`, `performance`, `security`, `testing`, `analytics`.
- **One-off batches:** `architecture-audit`, `architecture-audit-v2`, `architecture-audit-v3`,
  `architecture-audit-v4`, `security-audit-2026-06-12`.
- **GitHub defaults:** `duplicate`, `invalid`, `question`, `wontfix`, `good first issue`,
  `help wanted`.

They are not free, for two reasons:

- **Only the first 10 labels of an issue are fetched** by the scheduler's board query
  (`labels(first: 10)` in `scripts/factory_core/providers/tracker/github.py`). On an issue
  with more than 10 labels, a functional label such as `needs-discussion` or `epic` can fall
  outside what the scheduler sees. Keep issues at 10 labels or fewer until that limit is
  lifted (tracked in [#529](https://github.com/omniscient/dark-factory/issues/529)).
- **The scheduler matches label names as substrings** (see `docs/scheduler.md`). A new label
  whose name contains `epic`, `needs-discussion` or another functional label name acts as
  that label.

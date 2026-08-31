# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## Relationship to Archon workflow labels

Archon uses separate labels for its execution pipeline (`spec-pending-review`, `spec-approved`, `plan-pending-review`). These are not triage labels — they track workflow state after triage is complete. A typical issue flows:

1. `needs-triage` (intake)
2. Triaged → `ready-for-agent` or `ready-for-human`
3. Archon picks it up → `spec-pending-review` → `spec-approved` → `plan-pending-review` → done

Do not conflate the two label sets.

## Opt-in refinement gate

The backlog scheduler auto-refines Backlog issues **only when they carry the `ready-for-agent` label**. Unlabelled Backlog items are left for triage and are never automatically dispatched to the refinement pipeline.

This prevents new issues from being auto-refined during the labelling window (the root cause of the #159 dispatch loop). Apply `ready-for-agent` to a Backlog issue once it is triaged and fully specified for agent work.

The `spec-pending-review` re-refine-on-feedback path is unaffected — it handles feedback on an already-refined issue and does not require an opt-in label.

## Workflow flags

These labels change scheduler *behavior* for a ticket already past triage. They are not
triage roles — apply them after the issue is `ready-for-agent` or `direct-to-pr` (which
implies entry and straight-through flow).

| Label | Meaning |
|-------|---------|
| `spec-pending-review` | Spec posted, waiting for human board-move or `SPEC_GRACE_MINUTES` auto-advance |
| `plan-pending-review` | Plan posted, waiting for human board-move or `PLAN_GRACE_MINUTES` auto-advance |
| `direct-to-pr` | **Opt-in**: ticket is admitted to the pipeline _and_ runs straight-through. Spec and plan checkpoints become async (grace-windowed); PR approval is the single end gate. Combine with `SPEC_GRACE_MINUTES=0` / `PLAN_GRACE_MINUTES=0` for pure auto-flow. |
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. The `FACTORY_MANIFEST_LABEL` override itself is rejected if its case-folded value *contains* `ready-for-agent`, `-pending-review`, `direct-to-pr`, or the case-folded, non-empty `DIRECT_TO_PR_LABEL` env value (when set) — substring containment, matching the scheduler's own unanchored `grep -qi` label matching — so it can never be set to smuggle an issue into an existing gate or escalation state. |

**Operator note on renaming `direct-to-pr`:** the canonical source for this label is `config/config.yaml`'s `direct_to_pr.label`, and that value is materialized into the scheduler process's env (`DIRECT_TO_PR_LABEL`) only by `scheduler.sh`'s `_set_cfg` — it is not automatically propagated to the dispatched run container. If you rename `direct_to_pr.label` in `config.yaml`, you must *also* set `DIRECT_TO_PR_LABEL` explicitly in the run container's env (`instance.env`) for the `handoff.py intake` deny-list above to recognize the renamed label. Without that, `handoff.py` still denies the literal string `direct-to-pr` but silently fails to deny the renamed label, and an operator-renamed `direct_to_pr.label` could be smuggled through as a `FACTORY_MANIFEST_LABEL` override.

# Harness candidate contract and mechanism lineage — schema and lifecycle
**Revised:** 2026-10-21 (operator amendment after spec gate — one-way quarantine, default-deny risk classifier, diff containment, lineage-event validator, test inventory, citation fixes; plan-gate amendments: non-candidate approvers, tombstone op/status consistency)

**Issue:** #464 · **Epic:** #463 (Add a governed recursive harness-improvement capability)

---

## Overview / Problem statement

Epic #463 adopts a bounded **Propose → Probe → Compose** pattern for harness
self-improvement, explicitly *not* autonomous self-modification:

```text
immutable baseline + observed failure
  -> scoped candidate packages
  -> isolated matched probes
  -> independent verification
  -> evidence-linked composition or KEEP_BASE
  -> offline replay
  -> shadow
  -> advisory human-approved PR
  -> optional quarantined experience entry
```

No model-created candidate may modify the active harness, promotion tests, verifier,
permission policy, or production credentials. The repo already reserves the seam this
needs: `scripts/factory_core/adapter.py` hard-rejects a `mechanism_candidates` top-level
`.factory/adapter.yaml` key with a message pointing at "a future Bilevel Autoresearch
design ticket" (this one), and `scripts/state_governance_audit.py` (#190, shipped)
already reserves `mechanism_lineage` as a `state_type` in its append-only state-lineage
event envelope — unexercised by any fixture or check today. Note that the audit script's
`STATE_TYPES`, `OPERATIONS` and `ACTIONABILITIES` sets are *declared but not consumed* by
any of its five checks (the checks `.get()` fields and never validate enums or reject
unknown keys), which is why this ticket's `validate_lineage_event()` imports those
constants and enforces them itself (see "Lineage-event validation" below).

#464 is **data and lifecycle only**: define the versioned schema for a candidate record
(`mechanism_candidates`) and for its append-only audit trail (`mechanism_lineage`), the
H0–H3 risk classification, and forbidden-edit-path enforcement. It does not execute,
probe, promote, or activate any candidate, and does not add a loop dispatcher — those are
epic #463's later children.

## Requirements (from Q&A)

1. Hand-rolled Python validation, no `jsonschema` dependency — a new standalone module
   `scripts/factory_core/mechanism_candidate.py` (own `MechanismCandidateError`),
   mirroring `adapter.py`'s per-block validator-function style, plus reusable
   `validate_transition(from_state, to_state, event)` and `validate_lineage_event(event)`
   helpers. The **only** change to `adapter.py` is the
   `_RESERVED_TOP_FIELDS["mechanism_candidates"]` message string (requirement 4);
   `adapter.py` does not import the new module and owns no candidate validation logic.
2. `mechanism_lineage` events reuse `state_governance_audit.py`'s existing envelope
   verbatim (`entity_id`, `event_id`, `idempotency_key`, `operation`, `state_type`,
   `authority`, `scope`, `provenance`, `mutability`, `recoverability`, `actionability`,
   optional `subject_entity_id`) — no parallel envelope shape. `state_type:
   "mechanism_lineage"` uses the enum value #190 already reserved in `STATE_TYPES`; since
   no #190 check consumes that enum, `validate_lineage_event()` (requirement 9) is what
   actually rejects a wrong `state_type`, `operation`, or `actionability`.
3. `mechanism_candidates`/`mechanism_lineage` *records* live as committed artifact files
   under a new `evals/mechanism-candidates/fixtures/` directory (candidate records as one
   `*.json` per record, lineage as append-only `*.jsonl`, plus a `manifest.json` of
   expected pass/fail per file) — mirroring `evals/state-governance/fixtures/`. No live
   runtime store is introduced; nothing generates candidates yet.
4. `.factory/adapter.yaml`'s `mechanism_candidates` top-level key **stays hard-rejected**
   in this ticket — its rejection message is updated to cite
   `scripts/factory_core/mechanism_candidate.py` instead of "a future ... ticket," and
   **keeps the token `reserved`** so the existing pin
   `tests/test_adapter.py::test_mechanism_candidates_top_level_reserved_raises`
   (`match=r"mechanism_candidates.*reserved"`) stays green unchanged. This ticket adds no
   consumer for a declared adapter.yaml value, so accepting the key now would only add
   unconsumed, unvalidated surface. (See Assumptions — this reads "behind the adapter
   key" as "the schema a future loop-dispatcher ticket will accept," not as "unreject the
   key now.")
5. Risk class (H0–H3) is **derived** from which paths a candidate's diff touches, using
   epic #463's H0–H3 definitions and a **default-deny** classifier: a path is H3 unless it
   matches an explicit H0/H1/H2 allowlist committed in `mechanism_candidate.py`, and H3
   regardless if it matches the *resolved* safety lists from `adapter.load()` — never
   trusted from a self-declared field alone. A declared `risk_class` lower than the
   derived class is rejected. A derived `H3` is **unconditionally rejected** — there is no
   valid accepted H3 candidate record, by design.
6. `candidate_digest` is a `sha256:<64-hex>` content address the validator recomputes
   from a fixed set of identity-bearing fields (canonical JSON) and compares against the
   record's own value — mismatch fails closed. Every digest-valued field and every
   element of a digest-list field (`parent_digest`, `baseline.*_digest`,
   `dependency_closure[]`, `supersession.superseded_by`, `supersession.supersedes[]`)
   is checked by **one shared** regex `^sha256:[0-9a-f]{64}$` (lowercase only).
   `parent_digest` must never equal its own record's `candidate_digest` (no
   self-parenting), and every non-null `parent_digest` and every `dependency_closure`
   entry must resolve to a `candidate_digest` present in the validated fixture set
   (unresolved references fail closed). Actual digest *generation* against real
   harness/repo/model/verifier/tool/policy/library content is deferred to the future
   Propose/Probe tooling ticket; #464 validates shape, the
   recomputation-from-declared-fields invariant, and the two structural rules only.
7. Lifecycle state machine `{draft, quarantined, probed, composed, shadow, advisory,
   revoked}` with an explicit allowed-transition table; `revoked` is terminal and
   **`quarantined` is one-way**: its only exit is `-> revoked`. Re-proposal after
   quarantine is a *new* `candidate_id` whose `parent_digest` is the quarantined record's
   `candidate_digest`. No `permission_epoch` escalation or other quarantine-exit
   approval primitive exists in this ticket.
8. A candidate can never authorize its own state transition — enforced by
   `validate_transition()` itself (not only by `_resolve_approval()`'s same-`event_id`
   guard): for every state-advancing transition (`-> probed | composed | shadow |
   advisory`) the event's `authority.approval_record` must resolve per
   `_resolve_approval()` **and** the resolved approver event must not belong to the
   candidate's own `entity_id` (its own creation/update events never authorize), **and**
   for `-> shadow` and `-> advisory` the resolved approver's `authority.actor` must be
   `"human"`. The resolved approver event must belong to a non-candidate entity (an
   `entity_id` that is not any candidate record's `candidate_id`); candidate records never
   authorize each other.
9. Unknown fields at any schema level, and unknown lifecycle states, fail closed
   (hand-rolled unknown-key rejection, mirroring `adapter.py`'s `_validate_subblock`).
   This applies to lineage events too: `validate_lineage_event()` rejects unknown keys at
   every nesting level of the #190 envelope, because `state_governance_audit.py` does
   not.
10. Fixtures required by acceptance criteria: one candidate record per lifecycle state
    plus a valid lineage chain (positive); illegal transition, candidate-self-approval
    (own creation event as `approval_record`), non-human advisory approval,
    forbidden-path (H3) edit, diff-outside-allowlist scope-widening, #190 scope-widening,
    digest-mismatch, wrong `schema_version`, and unresolved `parent_digest` (negative) —
    plus a compatibility test that runs the `mechanism_lineage` chain fixture through
    `state_governance_audit.py`'s five existing checks **and** asserts
    `"mechanism_lineage" in state_governance_audit.STATE_TYPES` and that every
    `operation` the mapping table emits is in `state_governance_audit.OPERATIONS` (the
    five checks alone never read `state_type`, so running them proves only that the
    fields they `.get()` are present; the enum assertions are what prove the reservation
    is honoured).
11. No loop dispatcher, no candidate execution/promotion/activation, no scheduler or
    GitHub-mutation code is introduced by this ticket.

## Architecture / Approach

### File layout

```text
dark-factory/scripts/factory_core/mechanism_candidate.py   # schema + validators + CLI
tests/test_mechanism_candidate.py                           # unit tests
evals/mechanism-candidates/fixtures/
  candidate-draft.json
  candidate-quarantined.json
  candidate-probed.json
  candidate-composed.json
  candidate-shadow.json
  candidate-advisory.json
  candidate-revoked.json
  lineage-valid-chain.jsonl
  lineage-illegal-transition.jsonl
  lineage-self-approval.jsonl            # own creation event used as approval_record
  lineage-advisory-nonhuman.jsonl        # -> advisory approved by a non-human actor
  lineage-scope-widening.jsonl           # #190 scope width widening (issue/pr -> repo-wide)
  candidate-forbidden-path.json          # diff touches an H3 path
  candidate-scope-widening.json          # diff path outside editable_path_allowlist
  candidate-digest-mismatch.json
  candidate-schema-version.json          # schema_version != 1
  candidate-parent-unresolved.json       # parent_digest not present in the fixture set
  manifest.json
```

`mechanism_candidate.py` carries both an importable API (`validate_candidate(record)`,
`validate_transition(from_state, to_state, event)`, `validate_lineage_event(event)`,
`classify_risk(touched_paths)`, `canonical_digest(obj)`) and a small `argparse` CLI
(`--fixtures <dir-or-file> [--out-dir <dir>]`), so both this ticket's tests and a future
loop-dispatcher ticket call the same entry point. The CLI is modelled on
`state_governance_audit.py` but **deliberately diverges** from it: `--out-dir` is
optional (the audit script requires it), and the process exits non-zero on any failed
fixture (the audit script always exits zero and reports `STATUS` on stderr), because this
validator is a fail-closed gate input, not a scorecard writer.

### `mechanism_candidates` record shape

```text
schema_version: int                       # 1
candidate_id: str                         # opaque stable id, used as mechanism_lineage entity_id
candidate_digest: "sha256:<64-hex>"       # recomputed over the identity-bearing fields below
parent_digest: "sha256:<64-hex>" | null   # must not equal candidate_digest; must resolve in the fixture set

baseline:
  harness_digest / repo_digest / task_digest / model_runtime_digest /
  tool_digest / verifier_digest / policy_digest / library_digest: "sha256:<64-hex>"

source: { issue: int, run_id: str }
observed_failure: { description: str, evidence_refs: [str] }
invariant_scope: str                      # named invariant this candidate addresses
domain_scope: { repo: str, issue: int|null, pr: int|null, agent_role: str }  # #190 scope shape

risk_class: "H0" | "H1" | "H2" | "H3"     # declared; validator derives and cross-checks
editable_path_allowlist: [str]            # literal repo-relative file paths or dir prefixes ending in "/"; no globs
diff: str                                 # exact unified diff; non-empty; every path must sit inside the allowlist
dependency_closure: ["sha256:<64-hex>"]   # candidate_digests of prerequisite candidates; each must resolve
expected_behavioral_hypothesis: str

resource_envelope: { cpu_limit: str|null, memory_limit_mb: int|null, max_duration_seconds: int|null }
permission_envelope: { tool_allow: [str], tool_deny: [str] }   # descriptive only — see Assumptions
network_envelope: { egress_allowed: bool, allowed_hosts: [str] }
filesystem_envelope: { read_paths: [str], write_paths: [str] }
side_effect_envelope: { side_effect_level: int }  # 1..5 inclusive; bool rejected. side_effect.py (#196) defines 1-6 but
                                                  # rejects 6 at adapter/manifest validation and effective_level() fails
                                                  # closed outside 1-5, so 6 is rejected here too

probe_evidence_refs: [{ type: "probe"|"composition", ref: str, observed_at: str }]
contradictions: [str]
confidence: float                         # 0.0 <= x <= 1.0; bool and NaN rejected (bool is an int subclass)
reviewer: { actor: str, identity: str|null }
approver: { actor: str, identity: str|null }

lifecycle_state: "draft"|"quarantined"|"probed"|"composed"|"shadow"|"advisory"|"revoked"
supersession: { superseded_by: "sha256:<64-hex>"|null, supersedes: ["sha256:<64-hex>"] }
expiry: { expires_at: str|null }
rollback_handle: str|null
```

**Digest-bearing fields** (the set `canonical_digest()` hashes to produce
`candidate_digest`): `baseline`, `source`, `observed_failure`, `invariant_scope`,
`domain_scope`, `editable_path_allowlist`, `diff`, `dependency_closure`,
`expected_behavioral_hypothesis`, `resource_envelope`, `permission_envelope`,
`network_envelope`, `filesystem_envelope`, `side_effect_envelope`. Lifecycle/governance
bookkeeping fields (`candidate_id`, `candidate_digest`, `parent_digest`, `risk_class`
[derived, not hashed], `lifecycle_state`, evidence/review/supersession/expiry/rollback
fields, `schema_version`) are excluded, so the digest stays stable across lifecycle
advancement while the candidate's substantive proposal doesn't change.

**Canonicalization rule:** UTF-8 bytes of
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, sha256-hashed,
formatted as `"sha256:" + hexdigest` (full 64 lowercase hex characters — not the
truncated digests used elsewhere in the repo: `handoff.py`, `epic_autopilot.py` and
`scripts/memory_import.py` all use `hexdigest()[:16]`, `scripts/token_estimate.py` uses
`[:12]` — because these are identity/lineage keys, not display truncations). The same
`^sha256:[0-9a-f]{64}$` check is applied to every digest-valued field and list element
(requirement 6).

### Risk classification (H0–H3)

Epic #463's editable-surface policy **governs** the meaning of each class; this spec only
maps explicit repo path families onto it:

| Class (epic #463 definition) | Path families realised in this ticket |
|---|---|
| **H0** — tracer-eligible, bounded non-authoritative prompt/tool-guidance fragments | `docs/superpowers/` only |
| **H1** — human approval required: routing, model choice, context policy, adapter configuration, permissions | `commands/*.md` prose only. Any edit to tool frontmatter (`allowed-tools:` / `disallowed-tools:`) inside those files forces H3. Adapter configuration (epic H1) has **no editable realisation in this ticket**: `.factory/` is H3 below, and `config/config.yaml` stays H3 because budgets live there. |
| **H2** — full supply-chain review: scripts, plugins, dependencies, subagents, workflow/DAG | `scripts/*.py` and `scripts/*.sh` **outside** the H3 entries below, `workflows/` |
| **H3** — optimizer-forbidden: verifiers, hidden tests, promotion logic, budgets, security policy, credentials, approval machinery | The explicit enumeration below, plus anything matching the resolved safety lists, plus **everything not matched by H0/H1/H2** (default-deny) |

`classify_risk(touched_paths)` derives a class from the union of
`editable_path_allowlist` and every path extracted from `diff` (see "Diff-path
extraction and containment"), and is **default-deny**: a path is H3 unless it matches an
explicit H0/H1/H2 allowlist committed as constants in `mechanism_candidate.py`, and H3
regardless of that allowlist if it matches the resolved safety lists. The record's class
is the maximum over all paths.

**H3 enumeration (literal, part of this spec — no longer an open question).** Committed
in `mechanism_candidate.py` as repo-relative prefixes: `tests/`, `evals/`, `config/`,
`scripts/factory_core/`, `scripts/gate_*`, `scripts/budget_*`,
`scripts/verdict_gate_check.sh`, `scripts/push_gate_check.sh`,
`scripts/state_governance_audit.py`, `scheduler.sh`, `entrypoint.sh`, `Dockerfile`,
`run-compose.yml`, `deploy/`, `.github/`, `.factory/`, `.claude/`, `.archon/`,
`refinement-skills/`. Rationale per entry: `tests/` and `evals/` (including this ticket's
own `evals/mechanism-candidates/fixtures/` and #190's `evals/state-governance/fixtures/`)
are candidate-writable test oracles, which epic #463 says never authorize promotion;
`config/` holds budgets and autopilot policy; `refinement-skills/` holds the reviewer
rubrics the conformance and code-review gates execute (verifiers); the rest are gates,
budget machinery, the lineage verifier itself, dispatch, credentials/instance surface,
CI, the adapter, skills/settings/hooks, and factory memory. Tool-permission
declarations are H3 wherever they live: adapter.yaml `loops[].role_card` keys
`allowed_tools` / `forbidden_tools` are permanently reserved in `adapter.py`
(`_ROLE_CARD_RESERVED_FIELDS`) and never accepted, and skill `allowed-tools:`
frontmatter under `.claude/skills/` is H3 via both the `.claude/` entry and the resolved
safety lists.

**Resolved safety lists.** The validator reads `safety.hard_exclude_paths` and
`safety.critical_diff_paths` from `adapter.load(clone_dir)` — the merged, floored result
— **not** from raw `adapter_defaults.DEFAULTS`. `adapter.load()` applies
`_apply_boundary_floor()`, which unions `FACTORY_OWNED_CRITICAL_DIFF_FLOOR`
(`^\.factory/hooks/`, `^\.factory/adapter\.yaml$`, `^\.claude/`, `^workflows/`,
`^commands/`, `^\.archon/commands/`, `^\.archon/workflows/`, `^dark-factory/scripts/`)
into every target's `critical_diff_paths`, and the self-target `.factory/adapter.yaml`
*replaces* (list-valued keys are not deep-merged) `hard_exclude_paths` with
`deploy/instances/`, `.github/workflows/publish.yml`, `.claude/...`, and sets
`critical_diff_paths` to `^scheduler\.sh$`, `^entrypoint\.sh$`, `^workflows/`,
`^run-compose\.yml$`, `^Dockerfile$`, `^deploy/`, .... Raw
`adapter_defaults.DEFAULTS["safety"]` values are **MarketHawk constants** (`dark-factory/`,
`.archon/`, `scheduler.sh`, `factory_core/`, `app/core/auth`, `app/services/trading`,
`^alembic/versions/`, `^backend/app/core/auth`, ...): in the dark-factory self repo
several match nothing, and `factory_core/` only catches `scripts/factory_core/` under
the substring semantics of `epic_autopilot.hard_excluded()`. Note the floor puts
`^commands/` and `^workflows/` into `critical_diff_paths`; because the resolved lists
are checked *first* and force H3, an H1 `commands/*.md` or H2 `workflows/` candidate
would be rejected on the self target as the floor stands today. That is the intended
fail-closed outcome for this data-only ticket; relaxing it (e.g. a per-class exemption
for the floor's visibility-only entries, OD1 in #200) is a decision for the future
loop-dispatcher ticket, not something `classify_risk()` does on its own.

**Matching, specified exactly.** Each path is normalised with `posixpath.normpath` and
then: `str.startswith(prefix)` for every H0/H1/H2/H3 prefix entry and every
`hard_exclude_paths` entry (prefix, not the substring test `epic_autopilot` applies to
issue-declared paths); `re.search(pattern, path)` for every `critical_diff_paths` entry
(they are regexes, exactly as `diff_rank.SAFETY_PATH_PATTERNS` and
`gate_blast_radius.classify_file()` compile them); and a **case-folded** comparison
(`path.casefold().startswith(entry.casefold())`) against every H3 prefix entry so that
`.Claude/Settings.json` on a case-insensitive host cannot dodge `.claude/`. No glob
matching exists anywhere in the repo's gates and none is introduced here; there is no
`gate_conformance.py` — conformance is a skill/command node — so the only path matchers
this ticket mirrors are the two named above.

A record's declared `risk_class` must be **greater than or equal to** the derived class;
a lower declaration is rejected (underclaiming risk is a validation failure, not a
warning). A derived H3 is rejected regardless of what `risk_class` declares.

### Diff-path extraction and containment

`diff` is an exact unified diff and is the only thing a candidate proposes to change, so
its paths are extracted and checked before classification. The rules are:

1. **Extraction.** For every file block, parse all of `diff --git a/X b/X`, `--- a/X`,
   `+++ b/X`, `rename from X` / `rename to X`, and `copy from X` / `copy to X` (the repo's
   only existing diff parser, `diff_rank.py`, reads `+++ b/` alone, which is not enough:
   `git apply` consults the `diff --git` line). `/dev/null` is not a path. All names in
   one file block must agree after normalisation; a block whose header names disagree
   rejects the record.
2. **Normalisation and rejection.** Paths are normalised with `posixpath.normpath`.
   A record is rejected if any extracted path is absolute, contains a backslash, starts
   with `../` or contains any `..` segment, is C-quoted or escaped (git's `"a/\303\251"`
   form), or differs only by case from an H3 entry.
3. **Allowlist shape.** `editable_path_allowlist` entries are literal repo-relative file
   paths or directory prefixes ending in `/`; no globs (`*`, `?`, `**`, `[`) are
   accepted. An empty allowlist is valid shape but then no diff path can be contained,
   so such a record fails rule 4.
4. **Containment.** Every extracted diff path must `startswith` at least one allowlist
   entry; otherwise the record is rejected as scope widening
   (`candidate-scope-widening.json`). This is the candidate-level meaning of "scope
   widening"; the #190 `scope`-width meaning is checked separately on lineage events by
   `check_scope_non_expansion` (`lineage-scope-widening.jsonl`).
5. **Empty diff.** An empty or whitespace-only `diff` is rejected.

### Lifecycle state machine

States: `draft`, `quarantined`, `probed`, `composed`, `shadow`, `advisory`, `revoked`
(terminal — no outgoing edges).

Allowed transitions:

```text
draft       -> probed | quarantined | revoked
probed      -> composed | quarantined | revoked
composed    -> shadow | quarantined | revoked
shadow      -> advisory | quarantined | revoked
advisory    -> quarantined | revoked
quarantined -> revoked                               # one-way: no other exit
```

Any transition not in this table is rejected by `validate_transition()`. **Quarantine is
one-way.** A quarantined candidate can only be revoked; re-proposal after quarantine is a
new `candidate_id` whose `parent_digest` is the quarantined record's `candidate_digest`,
starting again at `draft`. This keeps the lineage consistent with
`check_deletion_propagation`, which sets `tombstoned` on the first tombstone-class op for
an entity and never clears it, so no `act` with `mutability.status: active` is ever
valid for that entity again — exactly the property quarantine should have.

**Approval rule (owned by `validate_transition()`, new logic in this ticket).** For every
state-advancing transition (`-> probed | composed | shadow | advisory`) the transition
event must carry an `authority.approval_record` that:

1. resolves per `state_governance_audit._resolve_approval()` as-is — present, not the
   event's own `event_id`, previously seen in file order, and linked to the candidate's
   `entity_id` (same `entity_id` or `subject_entity_id` pointing at it); **and**
2. resolves to an approver event whose own `entity_id` is **not** the candidate's
   `entity_id` — the candidate's own creation/update events never authorize, which
   `_resolve_approval()` alone does not guarantee (it accepts a same-entity, non-human
   event of sufficient epoch). The resolved approver event must belong to a non-candidate
   entity (an `entity_id` that is not any candidate record's `candidate_id`); candidate
   records never authorize each other; **and**
3. for `-> shadow` and `-> advisory`, resolves to an approver event whose
   `authority.actor` is `"human"` (the epoch-only branch of `_resolve_approval()` is not
   sufficient for these two edges).

Transitions to `quarantined` and `revoked` need no `approval_record` (anyone may stop a
candidate; nobody may advance one unapproved). `check_authority_monotonicity` and
`check_scope_non_expansion` continue to apply on top of this rule whenever an event also
raises `permission_epoch` or widens `scope`; they are not the mechanism by which
advancement is authorised.

### `mechanism_lineage` event mapping

Each lifecycle transition emits one `state_governance_audit.py`-shaped event,
`entity_id` = the candidate's `candidate_id`, `state_type: "mechanism_lineage"`:

| Transition / action | `operation` |
|---|---|
| candidate created (`-> draft`) | `write` |
| `-> probed` | `validate` |
| `-> composed`, `-> shadow`, `-> advisory` | `act` |
| `-> quarantined` | `quarantine` (already a tombstone-class op in `_TOMBSTONE_OPS`) |
| `-> revoked` | `tombstone` |
| expiry firing | `tombstone` |

Mapping `quarantined`/`revoked`/expiry onto `_TOMBSTONE_OPS` is deliberate and is now
genuinely consistent with `check_deletion_propagation`: because quarantine is one-way and
`revoked` is terminal, no state-advancing `act` is ever emitted for that `entity_id`
after a tombstone-class op, so the check's never-cleared `tombstoned` flag matches the
lifecycle exactly. `validate_transition()` — not the #190 check — is what rejects an
attempted post-quarantine advancement; the #190 check is a second, independent net that
would also catch an `act` with `mutability.status: active` if one were ever written.
Note that `_ACTIVE_STATUSES` is the literal `{"active"}` only, so `validate_lineage_event()`
restricts `mutability.status` to a closed enum (`active`, `tombstoned`, `quarantined`,
`superseded`) to stop a misspelt status from passing the #190 check silently.

### Lineage-event validation

`validate_lineage_event(event)` is required because `state_governance_audit.py` validates
nothing about event shape — its five checks `.get()` fields and never reject an unknown
key, an unknown `state_type`, or a non-enum `operation`. The function enforces:

- the envelope's required keys exactly (`event_id`, `idempotency_key`, `operation`,
  `state_type`, `entity_id`, `authority`, `scope`, `provenance`, `mutability`,
  `recoverability`, `actionability`; optional `subject_entity_id`), with unknown keys
  rejected at every nesting level (`authority`, `scope`, `provenance`, `mutability`,
  `recoverability`), mirroring `_validate_subblock`;
- `state_type == "mechanism_lineage"` and
  `state_type in state_governance_audit.STATE_TYPES`;
- `operation in state_governance_audit.OPERATIONS` and, for lineage events, restricted
  further to the five operations the mapping table emits (`write`, `validate`, `act`,
  `quarantine`, `tombstone`);
- `actionability in state_governance_audit.ACTIONABILITIES`;
- `mutability.status` in the closed enum above;
- tombstone op/status consistency: an event whose `operation` is in
  `state_governance_audit._TOMBSTONE_OPS` must carry `mutability.status: "quarantined"`
  (for `quarantine`) or `"tombstoned"` (for `tombstone`), and no other operation may carry
  either status;
- `scope` has the #190 shape `{repo, issue, pr, agent_role}` and equals the candidate's
  `domain_scope` for the `entity_id` it names.

The constants are **imported from** `state_governance_audit` (`STATE_TYPES`,
`OPERATIONS`, `ACTIONABILITIES`, `_TOMBSTONE_OPS`), never copied, so a future #190 change
is picked up here automatically.

### Acceptance-criteria mapping

| Acceptance criterion | How this spec satisfies it |
|---|---|
| Versioned JSON/YAML schema + validation tests | `mechanism_candidate.py` + `tests/test_mechanism_candidate.py`, `schema_version: 1` |
| Content digests cover every comparison-relevant input | `baseline.*_digest` (8 fields) + `candidate_digest` over defined field set |
| H0–H3 + immutable/forbidden-path rules machine-enforced | Default-deny `classify_risk()` over epic #463's definitions, literal H3 enumeration + resolved `adapter.load()` safety lists, diff-path extraction/containment, unconditional H3 rejection |
| Lineage preserves parent/composition/evidence/approval/supersession/expiry/revocation/rollback | Reuses #190 envelope fields directly (`authority`, `provenance`, `mutability.supersedes`, `recoverability.rollback_handle`) + candidate's own `supersession`/`expiry`/`rollback_handle`; shape enforced by `validate_lineage_event()` |
| Invalid transition / digest mismatch / scope widening / self-approval fixtures fail closed | `evals/mechanism-candidates/fixtures/*` negative fixtures (requirement 10): both scope-widening meanings (diff outside allowlist; #190 scope width) and both self-approval meanings (own `event_id`; own creation event) |
| Existing adapters without `mechanism_candidates` stay compatible | Key stays rejected — unchanged behavior, only the error message text changes (token `reserved` kept; `test_adapter.py` pin unchanged) |
| No execution/promotion/activation/scheduler/GitHub mutation | No such code is written; only schema, validator, fixtures, tests |

### Test inventory

All tests are pytest under `tests/` (`tests/test_mechanism_candidate.py`, plus the
existing `tests/test_adapter.py` pin), so CI's `python -m pytest tests/ -v` covers every
one of them with **no `.sh` test and no `ci.yml` wiring** required. Nothing here needs
Docker or a Linux host.

| Test | What it proves |
|---|---|
| `test_fixture_manifest_expectations` | Every file in `evals/mechanism-candidates/fixtures/manifest.json` validates to its declared pass/fail (mirrors `tests/test_state_governance_fixtures.py`); every fixture file is named in the manifest and vice versa |
| `test_canonical_digest_determinism` | Same object with different key order, unicode, and whitespace produces one digest; a one-byte change produces a different digest; output matches `^sha256:[0-9a-f]{64}$` |
| `test_candidate_digest_recomputed` | `candidate-digest-mismatch.json` fails; every positive fixture's `candidate_digest` equals `canonical_digest()` over the digest-bearing field set |
| `test_transition_table_exhaustive` | Every ordered `(from, to)` pair not in the table is rejected; `revoked` and `quarantined` have no exits other than `quarantined -> revoked`; an unknown state name is rejected |
| `test_unknown_keys_fail_closed` | An extra key at top level, inside `baseline`, inside each `*_envelope`, inside `domain_scope`, and inside each lineage-envelope block is rejected |
| `test_risk_class_underclaim_rejected` | Declared `H0` on a derived `H1` record fails; declared `H2` on a derived `H1` record passes |
| `test_h3_unconditional` | `candidate-forbidden-path.json` fails even with `risk_class: "H3"` declared; one case per H3 enumeration entry, one per resolved-safety-list match, one for an unmatched path (default-deny), one for a case-only variant (`.Claude/`) |
| `test_diff_path_extraction` | Header-name disagreement, `..` segment, absolute path, backslash, C-quoted name, and empty diff are each rejected; `rename to` / `copy to` targets are classified |
| `test_diff_within_allowlist` | `candidate-scope-widening.json` fails; a glob in `editable_path_allowlist` is rejected as shape |
| `test_no_self_parenting` | `parent_digest == candidate_digest` fails |
| `test_parent_and_dependency_resolve` | `candidate-parent-unresolved.json` fails; an unresolved `dependency_closure` entry fails |
| `test_schema_version` | `candidate-schema-version.json` (`schema_version != 1`) fails |
| `test_field_ranges` | `side_effect_level` 0, 6, and `True` rejected; `confidence` `-0.1`, `1.1`, `NaN`, `True` rejected |
| `test_self_approval_rejected` | `lineage-self-approval.jsonl` fails (own creation event as `approval_record`); own `event_id` as `approval_record` fails |
| `test_advisory_requires_human` | `lineage-advisory-nonhuman.jsonl` fails; the same chain with a `"human"` approver passes |
| `test_lineage_event_validation` | Wrong `state_type`, non-enum `operation`, non-enum `actionability`, misspelt `mutability.status`, and an unknown key inside `authority` are each rejected |
| `test_state_governance_compatibility` | `lineage-valid-chain.jsonl` passes all five `state_governance_audit` checks via `compute_scorecard`; `"mechanism_lineage" in STATE_TYPES`; every operation in the mapping table is in `OPERATIONS`; `"quarantine" in _TOMBSTONE_OPS` |
| `test_adapter_message_cites_module` (in `tests/test_adapter.py`) | The reserved-key error names `scripts/factory_core/mechanism_candidate.py` and still matches `r"mechanism_candidates.*reserved"`; `adapter` has no `mechanism_candidate` import |
| `test_cli_exit_code` | CLI exits non-zero on a failing fixture dir and zero on the positive set; `--out-dir` optional |

## Alternatives considered

- **jsonschema-based validation.** Rejected: no `jsonschema` dependency exists in the
  repo today, and none of the tooling deps the Dockerfile installs (`packaging`,
  `codeindex`, `pre-commit`, `pyyaml`, `aiohttp`) are version-pinned, so adding another
  unpinned schema library is the wrong direction; JSON Schema also can't express the
  lifecycle-transition table, the approval rule, or the forbidden-path cross-checks
  against the resolved `adapter.load()` safety lists — those end up as Python either way,
  so a schema library would only cover the easy half.
- **Storing candidate/lineage records in `.archon/memory/` or directly in
  `.factory/adapter.yaml`.** Rejected: `.archon/` holds factory memory, not validated
  data, and nothing yet generates or consumes a declared adapter.yaml value — accepting
  the key now would add unconsumed, unvalidated surface for zero behavior.
- **Self-declared, trusted `risk_class` field.** Rejected per epic #463's own framing
  (candidate payloads are untrusted data) — the class is derived from touched paths and
  a lower self-declaration is treated as a validation failure, not advisory.
- **Per-field, unspecified digest algorithms.** Rejected in favor of one mandated
  sha256-over-canonical-JSON scheme, so two schema-valid records of the same content
  always produce the same digest and `candidate_digest` is mechanically re-checkable.

## Open questions (non-blocking)

- Whether a re-proposal after quarantine (a new candidate whose `parent_digest` is a
  quarantined record) should require a cooldown/TTL (mirroring `epic_autopilot`'s
  `hold_ttl_hours` pattern) is deferred to the future loop-dispatcher ticket — #464 only
  validates the parent link, not a time bound.
- Whether the boundary floor's visibility-only `^commands/` / `^workflows/` entries get a
  per-class exemption so H1/H2 candidates are possible on the self target is for the
  loop-dispatcher ticket (see "Resolved safety lists"); `classify_risk()` in this ticket
  honours the floor as-is.
- Standardizing `network_envelope`/`permission_envelope` vocabulary (e.g. CIDR vs.
  hostname matching) beyond this ticket's shape-only validation is left to whichever
  future ticket builds real probe sandboxing.

## Assumptions (flagged)

- **Adapter-key interpretation.** The issue text ("Add the accepted schema behind the
  currently rejected `mechanism_candidates` adapter key") is read here as "define the
  schema a future loop-dispatcher ticket will accept behind that key," not as "unreject
  the key in this ticket." This follows directly from the Q&A (no consumer exists yet,
  and `.archon/memory/architecture.md`'s #195 precedent treats accepting
  unvalidated/unconsumed config surface as the thing to avoid) but is a reading of
  ambiguous issue phrasing, not a literal instruction, and is flagged for spec review.
- **`permission_envelope`/`network_envelope` are non-authoritative.** In this ticket they
  are descriptive metadata on a candidate record only — no gate, tool-permission system,
  or execution path reads them as an actual grant. Real enforcement of what a probe may
  touch remains the responsibility of whatever sandboxing mechanism a future
  probe-execution ticket builds; this is consistent with CLAUDE.md's rule that tool
  allow/deny-list surfaces are never created as a side effect of a schema-only ticket.
  `permission_envelope.tool_allow` / `tool_deny` are never read by any gate, and a future
  ticket may not promote them to grants without its own human-reviewed spec.

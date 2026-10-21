# Harness candidate contract and mechanism lineage — schema and lifecycle

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
event envelope — unexercised by any fixture or check today.

#464 is **data and lifecycle only**: define the versioned schema for a candidate record
(`mechanism_candidates`) and for its append-only audit trail (`mechanism_lineage`), the
H0–H3 risk classification, and forbidden-edit-path enforcement. It does not execute,
probe, promote, or activate any candidate, and does not add a loop dispatcher — those are
epic #463's later children.

## Requirements (from Q&A)

1. Hand-rolled Python validation, no `jsonschema` dependency — a new standalone module
   `scripts/factory_core/mechanism_candidate.py` (own `MechanismCandidateError`),
   mirroring `adapter.py`'s per-block validator-function style, plus a reusable
   `validate_transition(from_state, to_state, event)` helper. `adapter.py` only delegates
   to it; it does not own validation logic itself.
2. `mechanism_lineage` events reuse `state_governance_audit.py`'s existing envelope
   verbatim (`entity_id`, `event_id`, `idempotency_key`, `operation`, `state_type`,
   `authority`, `scope`, `provenance`, `mutability`, `recoverability`, `actionability`) —
   no parallel envelope shape. `state_type: "mechanism_lineage"` activates the enum value
   #190 already reserved.
3. `mechanism_candidates`/`mechanism_lineage` *records* live as committed artifact files
   under a new `evals/mechanism-candidates/fixtures/` directory (candidate records as one
   `*.json` per record, lineage as append-only `*.jsonl`, plus a `manifest.json` of
   expected pass/fail per file) — mirroring `evals/state-governance/fixtures/`. No live
   runtime store is introduced; nothing generates candidates yet.
4. `.factory/adapter.yaml`'s `mechanism_candidates` top-level key **stays hard-rejected**
   in this ticket — its rejection message is updated to cite the new schema module
   instead of "a future ... ticket." This ticket adds no consumer for a declared
   adapter.yaml value, so accepting the key now would only add unconsumed, unvalidated
   surface. (See Assumptions — this reads "behind the adapter key" as "the schema a
   future loop-dispatcher ticket will accept," not as "unreject the key now.")
5. Risk class (H0–H3) is **derived** from which paths a candidate's diff touches, checked
   against `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]` /
   `["critical_diff_paths"]` plus named verifier/promotion/budget/credential/
   approval-machinery files — never trusted from a self-declared field alone. A declared
   `risk_class` lower than the derived class is rejected. A derived `H3` is **unconditionally
   rejected** — there is no valid accepted H3 candidate record, by design.
6. `candidate_digest` is a `sha256:<64-hex>` content address the validator recomputes
   from a fixed set of identity-bearing fields (canonical JSON) and compares against the
   record's own value — mismatch fails closed. `parent_digest` uses the same format and
   must never equal its own record's `candidate_digest` (no self-parenting). Actual
   digest *generation* against real harness/repo/model/verifier/tool/policy/library
   content is deferred to the future Propose/Probe tooling ticket; #464 validates shape
   and the recomputation-from-declared-fields invariant only.
7. Lifecycle state machine `{draft, quarantined, probed, composed, shadow, advisory,
   revoked}` with an explicit allowed-transition table; `revoked` is terminal. Exiting
   `quarantined` requires a human-resolved authority escalation, reusing #190's existing
   `permission_epoch` / `approval_record` / `_resolve_approval()` machinery rather than a
   new parallel approval primitive.
8. A candidate can never authorize its own state transition — directly reuses
   `_resolve_approval()`'s existing self-approval guard (an `approval_record` equal to the
   event's own `event_id` never authorizes).
9. Unknown fields at any schema level, and unknown lifecycle states, fail closed
   (hand-rolled unknown-key rejection, mirroring `adapter.py`'s `_validate_subblock`).
10. Fixtures required by acceptance criteria: one candidate record per lifecycle state
    plus a valid lineage chain (positive); illegal transition, candidate-self-approval,
    forbidden-path (H3) edit, scope-widening, and digest-mismatch (negative) — plus a test
    that runs a `mechanism_lineage` fixture directly through
    `state_governance_audit.py`'s five existing checks, proving envelope compatibility
    rather than asserting it.
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
  lineage-self-approval.jsonl
  lineage-scope-widening.jsonl
  candidate-forbidden-path.json
  candidate-digest-mismatch.json
  manifest.json
```

`mechanism_candidate.py` carries both an importable API (`validate_candidate(record)`,
`validate_transition(from_state, to_state, event)`, `classify_risk(touched_paths)`,
`canonical_digest(obj)`) and a small `argparse` CLI modelled on
`state_governance_audit.py` (`--fixtures <dir-or-file> [--out-dir <dir>]`, non-zero exit
on any failure), so both this ticket's tests and a future loop-dispatcher ticket call the
same entry point.

### `mechanism_candidates` record shape

```text
schema_version: int                       # 1
candidate_id: str                         # opaque stable id, used as mechanism_lineage entity_id
candidate_digest: "sha256:<64-hex>"       # recomputed over the identity-bearing fields below
parent_digest: "sha256:<64-hex>" | null   # must not equal candidate_digest

baseline:
  harness_digest / repo_digest / task_digest / model_runtime_digest /
  tool_digest / verifier_digest / policy_digest / library_digest: "sha256:<64-hex>"

source: { issue: int, run_id: str }
observed_failure: { description: str, evidence_refs: [str] }
invariant_scope: str                      # named invariant this candidate addresses
domain_scope: { repo: str, issue: int|null, pr: int|null, agent_role: str }  # #190 scope shape

risk_class: "H0" | "H1" | "H2" | "H3"     # declared; validator derives and cross-checks
editable_path_allowlist: [str]            # path globs/prefixes the diff may touch
diff: str                                 # exact unified diff
dependency_closure: [str]                 # digests of prerequisite candidates, if any
expected_behavioral_hypothesis: str

resource_envelope: { cpu_limit: str|null, memory_limit_mb: int|null, max_duration_seconds: int|null }
permission_envelope: { tool_allow: [str], tool_deny: [str] }   # descriptive only — see Assumptions
network_envelope: { egress_allowed: bool, allowed_hosts: [str] }
filesystem_envelope: { read_paths: [str], write_paths: [str] }
side_effect_envelope: { side_effect_level: int }  # cross-references side_effect.py's 1-6 scale, #196

probe_evidence_refs: [{ type: "probe"|"composition", ref: str, observed_at: str }]
contradictions: [str]
confidence: float                         # 0.0-1.0
reviewer: { actor: str, identity: str|null }
approver: { actor: str, identity: str|null }

lifecycle_state: "draft"|"quarantined"|"probed"|"composed"|"shadow"|"advisory"|"revoked"
supersession: { superseded_by: "sha256:<64-hex>"|null, supersedes: [str] }
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
12/16-char truncated digests used elsewhere in the repo, e.g. `handoff.py`,
`epic_autopilot.py`, `memory_import.py` — because these are identity/lineage keys, not
display truncations).

### Risk classification (H0–H3)

`classify_risk(touched_paths)` derives a class from the union of
`editable_path_allowlist` and any paths named in `diff`:

- **H3** (unconditionally rejected): any path matching
  `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]` or `["critical_diff_paths"]`
  (already covers `dark-factory/`, `.archon/`, `scheduler.sh`, `factory_core/`,
  `app/core/auth`, `app/services/trading`, `.claude/skills/`, `.claude/settings*.json`,
  `.mcp.json`, etc.), plus any verifier/promotion/budget/credential/approval-machinery
  file named by epic #463's security constraints, plus any `role_card.allowed_tools` /
  `forbidden_tools`-style permission frontmatter edit. The validator reads these lists
  from `adapter_defaults.DEFAULTS` directly rather than keeping its own copy, using the
  same prefix/glob matching the conformance/blast-radius gates already use. Unmatched
  unknown paths classify at the *higher* of the surrounding candidates — i.e. fail safe
  toward H2, not H0.
- **H2**: executable/dispatch code outside the H3 lists — `scripts/*.py` outside
  `factory_core/`, `workflows/*.yaml`.
- **H1**: prompt-text-only edits to `commands/*.md`, `refinement-skills/` prose (any
  touch to tool-permission frontmatter within these forces H3, per above).
- **H0**: `docs/` and `evals/**/fixtures/` only.

A record's declared `risk_class` must be **greater than or equal to** the derived class;
a lower declaration is rejected (underclaiming risk is a validation failure, not a
warning). A derived H3 is rejected regardless of what `risk_class` declares.

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
quarantined -> probed | composed | shadow | revoked   # non-revoked exits require escalation (below)
```

Any transition not in this table is rejected by `validate_transition()`. Exiting
`quarantined` to a non-terminal state is modeled as a `permission_epoch` escalation on the
transition's `mechanism_lineage` event, reusing #190's `check_authority_monotonicity` /
`_resolve_approval()` as-is: the escalating event's `approval_record` must resolve to a
previously-seen, non-self event linked to the same `entity_id` whose `authority.actor` is
`"human"` (or whose `permission_epoch` is already `>=` the new value). A self-referencing,
dangling, later, or unlinked `approval_record` — or one from a non-human actor at an
insufficient epoch — fails the transition closed. This is the existing check, not new
logic: #464 only needs lineage events to carry the right shape for it to apply.

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

Mapping `quarantined`/`revoked`/expiry onto `_TOMBSTONE_OPS` is deliberate: it means
`state_governance_audit.py`'s existing `check_deletion_propagation` already guarantees
that no later `act` event can treat a quarantined or revoked candidate as
`mutability.status: active`, with zero new code.

### Acceptance-criteria mapping

| Acceptance criterion | How this spec satisfies it |
|---|---|
| Versioned JSON/YAML schema + validation tests | `mechanism_candidate.py` + `tests/test_mechanism_candidate.py`, `schema_version: 1` |
| Content digests cover every comparison-relevant input | `baseline.*_digest` (8 fields) + `candidate_digest` over defined field set |
| H0–H3 + immutable/forbidden-path rules machine-enforced | `classify_risk()`, unconditional H3 rejection |
| Lineage preserves parent/composition/evidence/approval/supersession/expiry/revocation/rollback | Reuses #190 envelope fields directly (`authority`, `provenance`, `mutability.supersedes`, `recoverability.rollback_handle`) + candidate's own `supersession`/`expiry`/`rollback_handle` |
| Invalid transition / digest mismatch / scope widening / self-approval fixtures fail closed | `evals/mechanism-candidates/fixtures/*` negative fixtures (requirement 10) |
| Existing adapters without `mechanism_candidates` stay compatible | Key stays rejected — unchanged behavior, only the error message text changes |
| No execution/promotion/activation/scheduler/GitHub mutation | No such code is written; only schema, validator, fixtures, tests |

## Alternatives considered

- **jsonschema-based validation.** Rejected: no dependency exists in the repo today
  (Dockerfile pins only pyyaml for this tooling), and JSON Schema can't express the
  lifecycle-transition table, self-approval rejection, or forbidden-path cross-checks
  against `adapter_defaults.DEFAULTS` — those end up as Python either way, so a schema
  library would only cover the easy half.
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

- The exact enumeration of "verifier/promotion/budget/credential/approval-machinery
  files" beyond the existing `hard_exclude_paths`/`critical_diff_paths` lists is left for
  the implement phase to enumerate precisely against the current file tree (this spec
  names the categories; plan/implement should produce the literal list).
- Whether quarantine-exit should additionally require a cooldown/TTL (mirroring
  `epic_autopilot`'s `hold_ttl_hours` pattern) is deferred to the future loop-dispatcher
  ticket — #464 only requires human-resolved approval, not a time bound.
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
- **Overloading `permission_epoch` for quarantine-exit.** Modeling "exit quarantine"
  as a `permission_epoch` escalation (reusing #190's existing authority-monotonicity
  check) assumes the plan/implement phase is comfortable giving `permission_epoch` this
  second meaning rather than inventing a parallel approval primitive; flagged for
  architect review at the plan phase.

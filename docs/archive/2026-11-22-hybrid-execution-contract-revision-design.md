# Revise the Hybrid Execution Contract After Post-Merge Review (#556)

**Issue:** #580

**Revised:** 2026-11-23 (operator amendments after spec gate and after plan gate)

---

## Overview / Problem Statement

`docs/hybrid-execution-contract.md` (#556, merged via PR #574) is the accepted decision
report for Dark Factory's hybrid cloud+local model execution initiative. A post-merge
independent review found no blocking defect and no weakened safety surface, but thirteen
should-fix items that would mislead the downstream implementation tickets it gates (#557–#564,
all either `Depends on: #556` or, for #557, now also `Depends on: #580`). The owner confirmed
the five §4.11 decisions as written on 2026-11-22; this ticket is a **docs-only revision** of
the existing contract file plus the matching assertions in
`tests/test_hybrid_execution_contract_doc.py` — no code, config, workflow, gate, or deploy
change, and no change to the five decisions themselves.

Every citation below was re-verified against `origin/main` during this refinement pass (not
trusted from the issue body or the existing contract text):

- `scripts/factory_core/session_window.py`: `is_session_window_failure` keys session-pause
  detection on a rejected structured `claude.rate_limit_event` (classified by
  `_is_exhaustion_event`) or on usage/session/credit-exhaustion phrasing
  (`_SESSION_EXHAUSTION_RE`) — never on a bare HTTP 429. A bare 429 matches only the broader
  `RATE_LIMIT_RE`, which `error_signature.py` uses for the breaker's
  `environmental:rate_limit` bucket.
- `scripts/factory_core/run_record.py` coerces a missing node cost to `0`
  (`n.get("costUsd") or n.get("cost_usd") or 0`; line 272 on `origin/main` at `829eaaa`). #453
  landed in PR #582 without moving it: a command or prompt node that reports no usage, or that
  started but is absent from the cost report, is now re-recorded as `cost_capture: "missing"`
  with a null cost. The contract text cites the expression, not the line.
- `scripts/factory_core/side_effect.py` implements enforced permission profiles for levels
  1–5 today — `#196`'s enforcement is **not** unbuilt, contradicting the contract's current
  Alternatives-4 text.
- `deploy/instance.env.example`: `ANTHROPIC_BASE_URL` is commented out by default, set only
  when opting into `FACTORY_MODEL_PROXY_ENABLED`; default cloud auth is
  `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` (not `ANTHROPIC_AUTH_TOKEN`, which nothing on
  `main` sets).
- Live-read `gh issue view` on #557–#564, #238, #453: #558 declares `Depends on: #557` only
  (not `#238`); #557 now also declares `Depends on: #580`; #564 declares
  `Depends on: #563, #239, #238, #453, #526, #447`; #453 is already an existing bug (cost
  report under-reporting gate-terminated/missing-usage nodes) carrying a hybrid-execution
  priority addendum naming it a must-have evidence prerequisite for #564; #238 carries an
  approved hybrid-execution addendum proposing failure classes that overlap this contract's
  §4.6 minimum list; #562's scope is explicitly "offline/shadow evaluation only; no production
  default/alias change"; #561's acceptance criteria want capability evidence to "distinguish
  declared, tested, unsupported, stale and unknown"; #559's goal text ("role/node-scoped
  execution selection") and acceptance criterion 1 ("a local worker and cloud checker
  concurrently") read as finer-grained than the contract's own container-isolation unit; #563's
  candidate workload (the preview classifier) runs today as a DAG node inside the same
  container as every other node, not a separately dispatched one.

## Requirements

Distilled from the issue's thirteen required revisions, confirmed by the brainstorming Q&A
(full dialogue in the Phase 6 issue comment):

1. Flip the header and all five §4.11 decision markers from provisional to accepted, citing
   #580; update the two test assertions pinning the old wording.
2. Scope the checker-independence rule to a local-routed actor's work; state cloud-routed
   same-run checkers are unaffected; collapse the proof to one definition.
3. Correct the §4.10 `session_window.py` citation; name the local-aware-detector fix (#586) as
   a §4.12 rollout precondition, not a design to build here.
4. Add the missing credential invariant: a local-routed container holds no cloud model
   credential and no tracker/code-host write credential unless its own reviewed spec grants
   one.
5. Define "helper dispatch" concretely; state plainly that no #557–#564 ticket builds the
   first instance and name #587 as the ticket that does; reconcile wording with
   #559/#563/#564.
6. Separate "`local_only`/local-pinned never retries on cloud" from "this payload is
   confidential"; resolve the unknown-vs-default and fallback-vs-permission contradictions;
   state whether a cloud checker may read `cloud_ok` output that ran locally.
7. Name owners: dispatch-record schema → #558 (§4.7 Result row); payload-class field and
   dispatch-time predicate → #557; no-auto-merge scheduler end-gate check → #585 (its own
   human-reviewed gate ticket, named here and not designed).
8. Resolve the §4.6/#238/#558 dependency mismatch by declaring the §4.6 list an interim
   enumeration #558 implements directly (since #558 does not and should not block on #238).
9. Make capability evidence explicitly per-endpoint/long-lived with agent-write prohibited;
   add four `invalidated_by` fields; align `results` states and `qualified_roles` with §4.4's
   `role` enum and #561's acceptance criteria.
10. Name the owner of the `cost_usd: null` fix for local runs — #558 (every node of a
    local-routed run is `null`); #453's missing-cost fix has landed — and require it before any
    local run is recorded.
11. Align §4.12's rollout rule with #562 (GO is offline/shadow-only, not itself a dispatch)
    and the #563→#564 ordering.
12. Scope `safety.sensitive_keywords` reuse to local-eligibility decisions only, stated
    normatively in §4.3 (today it only appears in the historical Q&A and in a dangling
    Alternatives cross-reference to a §4.3 statement that doesn't exist).
13. Fix the eight nits: §196 enforcement-exists correction; the `ANTHROPIC_BASE_URL`/
    `ANTHROPIC_AUTH_TOKEN` default-state correction; decision 2's literal-model-ID footnote;
    the Invariant-5/Q2 mechanism wording; the test's line-wrapped-citation blind spot and its
    weakened five-decisions count; and the `provider-abstraction-design.md` §7.5 pointer's
    false "every checker" claim.

### Brainstorming Q&A

> **Q1 (helper-dispatch definition and #559/#563/#564 reconciliation):** Required revision 5
> asks for a concrete definition of "helper dispatch", the ticket that builds the first one, and
> reconciled wording for #559/#563/#564.
>
> **A1:** No ticket in #557-#564 builds the first helper dispatch — their scopes are
> infrastructure/fixtures/deployment/testing, not "build one concrete helper-dispatch workload".
> Record this as a tracked gap with a follow-up ticket stub (title: "Build first checker-free
> helper dispatch (separately dispatched container; output accepted by a cloud-routed check in a
> different dispatch)", `Depends on: #557, #558, #562`) rather than retroactively naming an
> existing ticket. Concrete definition for §4.11: a helper dispatch is a separately scheduled run
> container with its own `.archon/.env`/endpoint config, doing non-gating work with no
> in-container checker; its output is only an artifact until a cloud-routed check in a
> *different* dispatch accepts it, proven by the dispatch record. Recommended issue-body
> additions: #559's "Scope and boundaries" gets a reconciliation paragraph stating role/node-
> scoped selection binds a dispatch's single endpoint to a declared role (not a request-level
> router mixing endpoints in one container), and its acceptance criterion 1 is edited to say "a
> local worker dispatch and a cloud checker dispatch" (two separate dispatches, not one run).
> #563's "Scope and boundaries" gets a prerequisite paragraph noting the preview classifier is
> today a DAG node of the same container as gated phases (not dispatch-isolated), so its local
> arm must run in #563's own offline/shadow runner only, or #563 should pick an already-
> dispatch-isolated workload. #564's "Scope and boundaries" gets a paragraph restating decision 3
> (checker-free helper dispatch only, no gated phase run locally, independent checker is a
> separate cloud dispatch, `local_only` output never auto-merges) plus a `Depends on:` line on
> the first-helper-dispatch follow-up once filed.
> (Filed 2026-11-23 as #587.)

> **Q2 (`local_only` dual meaning; unowned mechanisms):** Required revisions 6 and 7 ask to
> separate "never retries on cloud" from "confidential", resolve two apparent contradictions
> around default/unknown payload class, decide whether a cloud checker may read `cloud_ok`
> output that ran locally, and name owners for the dispatch-record schema and the no-auto-merge
> check.
>
> **A2:** Use a declared-vs-effective-class split: `payload_class` (declared, operator-config
> only, optional) is distinct from the effective class computed once at dispatch (`local`
> destination → `local_only` unconditionally; cloud destination → the declared class, or
> `cloud_ok` if none declared — matching today). "Unknown/missing → block + escalate" narrows to:
> a declared value outside {cloud_ok, local_only}; a registry entry with invalid/missing
> `hosting_location`/`accepts_payload_classes`; or an effective class not a member of the
> target's `accepts_payload_classes` (e.g. a declared-`local_only` ticket aimed at a cloud
> entry). An absent declared class on a cloud dispatch never blocks (decision 4 stands). A cloud
> checker *may* read the output of a local dispatch whose declared class is `cloud_ok` or absent
> (output inherits input confidentiality, already cloud-eligible) — v1 helper dispatches (§4.11
> decision 3) are therefore limited to work whose declared class is `cloud_ok` or absent; a
> declared-`local_only`
> dispatch's output may only be checked by a human or a separate local dispatch, consistent with
> never-auto-merge. Introduce the derived (never operator-set) term "local-pinned" for
> `hosting_location: local` dispatches to carry the two local-specific rules (no cloud retry
> ever; no auto-merge ever) separately from `payload_class` (confidentiality/export only);
> the decision bodies keep their wording; Invariant 7 restates decision 5 as "neither a
> local-pinned dispatch's output nor any `local_only` payload ever auto-merges" (strictly
> stronger, weakens nothing). The
> "never retries" vs "operator permission" tension resolves by noting a later cloud run is a new
> dispatch decision by operator config, never a runtime fallback of the local-pinned dispatch
> itself. Owners: dispatch-record schema (`hosting_location`, `endpoint_id`, `correlation`) is
> #558's Result-contract row in §4.7 (its own acceptance criteria already commit to this);
> payload-class field + dispatch-time predicate is #557 (not #558, which explicitly excludes
> eligibility decisions; #557's scope already covers destination properties and preflight) —
> recommend a #557 acceptance criterion addition making this explicit. The no-auto-merge
> scheduler end-gate check has no existing owner among #557-564; record it as a tracked gap
> needing its own human-reviewed gate ticket (stub: "Scheduler end-gate: refuse auto-merge/
> grace-advance for local-pinned or `local_only` work", `Depends on: #558`), and note both the
> first-helper-dispatch follow-up (Q1) and #564 should depend on it once filed.
> (Filed 2026-11-23 as #585; #587 depends on it.)

> **Q3 (capability-evidence vocabulary and `qualified_roles` alignment):** Required revision 9
> asks where the long-lived per-endpoint evidence record lives, what "agents cannot write it"
> means operationally, additive `invalidated_by` fields, and alignment of result states and
> `qualified_roles` with §4.4 and #561.
>
> **A3:** `qualified_roles: [worker]` becomes `qualified_roles: [actor]` to match §4.4's
> normative enum (§4.2 already treats worker/orchestrator as kinds of actor); add prose that v1
> evidence may only ever list `actor`, never `checker`/`monitor`, and that `actor` qualification
> under decision 3 covers a checker-free helper dispatch only (not full-agent or checker
> capability, per #561). Per-dimension `results` values become `pass | fail | unsupported |
> not_tested` (`unsupported` = the endpoint's protocol/runtime cannot do this at all, proven by a
> fixture); a separate, never-stored *derived* evidence state (`tested | declared | stale |
> unknown`), computed at preflight read-time from `expires_at`/`invalidated_by` fingerprint
> comparison, carries #561's "declared/stale/unknown" vocabulary instead of trying to store it
> per dimension. Fail-closed rule: a role is eligible only if the derived state is `tested` and
> every dimension that role requires is `pass`; every other combination blocks and escalates.
> The record lives one-per-endpoint, keyed by `endpoint_id`, as a sibling record next to (not a
> field of) the #557 registry entry, on the same operator-controlled/human-only instance-config
> surface as registry entries and payload-class grants (exact path left to #557) — never in the
> per-run record or ledger, which may only cite it by `endpoint_id`+version. "Agents cannot write
> it" means: only an operator acting on #562's human-gated certification run (never scheduler-
> dispatched) may create/update a record; no scheduler-dispatched container may be able to write
> it (mechanism left to #557/#560); no factory phase agent may create or edit one regardless of privilege; a record-shaped artifact
> appearing in a branch/PR/comment/run-artifact is `self_report` and is ignored; preflight rejects
> a record missing `certified_by` or an unmatched `fixture_suite_version`. `invalidated_by` gains
> four additive fields (none replaced): `quantisation` (on `model_identity` — distinct from
> `model_digest`, which may not cover a pre-quantised weights file or on-the-fly quantisation),
> `chat_template` and `context_settings` (on `runtime_stack` — runtime launch parameters that can
> change independently of `runtime_version`), and a new top-level `harness_version` (the pinned
> Claude CLI/SDK version, distinct from both `fixture_suite_version` and `runtime_version`). Final
> list: `[model_digest, quantisation, runtime_version, chat_template, context_settings,
> harness_version, fixture_suite_version, registry_config]`.

### Operator decisions (owner-delegated, 2026-11-23)

The spec gate raised three questions. The operator answered them on 2026-11-23 under the
owner's delegation; none changes a §4.11 decision.

1. **May a cloud checker read the output of local work?** Yes, when the work's declared payload
   class is `cloud_ok` or absent. The inputs were already cloud-eligible, and without this #564
   has no checker path. Output of declared-`local_only` work is checked by a human or a separate
   local dispatch only (R6).
2. **Are the three follow-up tickets filed now, so the contract cites numbers?** Yes: #585
   (scheduler end-gate: refuse auto-merge and grace-advance for local-routed or `local_only`
   work; a gate ticket), #586 (session-window detection and the rate-limit breaker signature
   made local-endpoint-aware), and #587 (first checker-free helper dispatch with a cloud-routed
   acceptance check; `Depends on: #557, #558, #562, #585, #586`). None carries `ready-for-agent`;
   #585 and #586 never get `direct-to-pr`. This spec names them as owners and designs none of
   them.
3. **Who owns `cost_usd: null`?** Split: #453 records a missing cost as `null`; #558 records
   `null` for every node of a local-routed run (R10). #453 landed in PR #582 on 2026-11-23, so
   #558's half is the only one still open.

The same amendment pass applied spec-gate amendments A1–A10. It corrected A2 and A3 of the Q&A
above in place where they said "effective" for "declared" and prescribed a read-only mount, and
it added the filed ticket numbers.

Plan-gate amendments P1–P4 followed on 2026-11-23. P1: R10 and R11 describe #453 as landed and
name #558 as the open cost owner. P2: R7 rewrites the §4.7 storage sentence, which still put all
four contracts in the per-run record. P3: R2 scopes Invariant 2 and §4.2's "Endpoint-role
binding" bullet to a local-routed actor. P4: R2 re-points the §4.1 #559 row from Q1 to §4.2, and
the contract's "Revised (#580)" line is dated 2026-11-23.

## Architecture / Approach

Apply the following edits to `docs/hybrid-execution-contract.md`. Section references are to the
file's current (merged) numbering; nothing outside the listed anchors changes, and the
historical "Brainstorming Q&A" reproduced under "Requirements" in that file is refinement-time
history per its own "About this document" framing — it is **not** edited by this revision; only
the normative `## Architecture / Approach` subsections, the header, one sentence added to
"About this document" (R2), the Invariants index, one Overview sentence (R13b), the Alternatives
list, one Assumptions bullet (R13g), and the `provider-abstraction-design.md` §7.5 pointer are.

### R1 — Status: provisional → accepted

Header block — replace:
> **Status:** Proposed; accepted on the merge of the #556 PR by a human reviewer. Until that
> merge, no downstream implementation spec (#557–#564, or #239's re-refinement) may be written
> against this contract; the copy of this file on `main` is the accepted one. The five §4.11
> decisions are provisional operator defaults pending owner confirmation at that review.

with:
> **Status:** Accepted — the #556 PR merged and this is the accepted copy on `main`. The five
> §4.11 decisions, carried at merge as provisional operator defaults pending owner confirmation,
> were confirmed as written by the owner in #580 (2026-11-22); downstream implementation specs
> (#557–#564, #239's re-refinement) may be written against this contract.

Add a second `**Revised:**` line directly after the existing one (keep the existing line
byte-identical — it documents the #556 PR's own spec-gate pass):
> **Revised (#580):** 2026-11-23 — post-merge independent-review amendments: acceptance status
> (header, §4.11), checker-independence scope (§4.2, Invariant 3), session-window citation and
> rollout precondition (§4.10, §4.12), credential invariant (§4.3, new Invariant 16),
> helper-dispatch definition (§4.11 decision 3), `local_only`/local-pinned split (§4.3,
> Invariants 4–7), mechanism owners (§4.2, §4.3, §4.7), §4.6/#238 interim enumeration,
> capability-evidence storage/vocabulary (§4.5), `cost_usd` owner (§4.10), rollout alignment
> (§4.12), keyword-signal scope (§4.3), and nits (Overview, About this document, §4.11
> decision 2, Alternatives 4, Assumptions, Invariant 5, the test's line-wrapped-citation and
> five-decisions checks, and the `docs/provider-abstraction-design.md` §7.5 pointer).

(Write the pointer's path in full, as above: the doc test resolves every backticked file
citation in the contract, and a bare `provider-abstraction-design.md` is not on its
`PARTIAL_CITATION_ALLOWLIST`.)

Each of the five §4.11 decision markers — replace every occurrence of:
> *Provisional operator default (2026-11-22), pending owner confirmation at PR review.*

with:
> *Accepted operator default (2026-11-22; confirmed by the owner in #580).*

§4.11 heading — replace:
> ### 4.11 v1 selected path and provisional operator defaults

with:
> ### 4.11 v1 selected path and accepted operator defaults

and make the same change to that heading's entry in the test's `REQUIRED_SECTIONS` list.

§4.11 intro — replace its last two sentences:
> The five decisions below are each a **Provisional operator default
> (2026-11-22), pending owner confirmation at PR review**. Downstream tickets build against them as
> written; a changed decision is a revision of this spec, not a downstream re-decision.

with:
> The five decisions below are accepted operator defaults, confirmed as written by the owner in
> #580. Downstream tickets build against them as written; a changed decision is a revision of
> this contract, not a downstream re-decision.

(The intro deliberately does not repeat the marker phrase, so the marker appears exactly once
per decision.)

`tests/test_hybrid_execution_contract_doc.py` — in
`test_header_carries_issue_version_and_acceptance_status`, replace:
```python
    assert "**Status:** Proposed" in head
    assert "merge of the #556 PR by a human reviewer" in head
    assert "provisional operator defaults pending owner confirmation" in head
```
with:
```python
    assert "**Status:** Accepted" in head
    assert "the #556 PR merged and this is the accepted copy on `main`" in head
    assert "confirmed as written by the owner in #580" in head
```
Also change that test's first line to
`head = " ".join(_doc_text().split("\n## ", 1)[0].split())`, so a re-wrapped header line cannot
break a pin.

Replace the whole of `test_fail_closed_invariants_pinned`:
```python
def test_fail_closed_invariants_pinned():
    text = _doc_text()
    for phrase in FAIL_CLOSED_INVARIANTS:
        assert phrase in text, f"fail-closed invariant weakened or removed: {phrase}"
    assert text.count("Provisional operator default (2026-11-22)") >= 1
```
with these two tests (the pins are matched against whitespace-normalised text, and the five
decisions are checked by structure, not by a string count):
```python
def test_fail_closed_invariants_pinned():
    text = " ".join(_doc_text().split())
    for phrase in FAIL_CLOSED_INVARIANTS:
        assert phrase in text, f"fail-closed invariant weakened or removed: {phrase}"


def test_section_4_11_has_five_accepted_decisions():
    section = _section(_doc_text(), "### 4.11 v1 selected path and accepted operator defaults")
    items = re.split(r"^\d+\. ", section, flags=re.MULTILINE)[1:]
    assert len(items) == 5, f"expected five §4.11 decisions, found {len(items)}"
    for n, item in enumerate(items, 1):
        assert "Accepted operator default (2026-11-22" in " ".join(item.split()), (
            f"§4.11 decision {n} does not carry the accepted-default marker")
```
(The header test's two assertions and the old `>= 1` count are the "two test assertions" the
issue names; the `REQUIRED_SECTIONS` heading entry is the only other place the old wording is
pinned. The doc test file goes from 12 tests to 13.)

### R2 — Checker-independence: scope to local-routed actors, one definition

Invariants index — replace item 3:
> 3. Checker independence is proven by the dispatch record (`hosting_location: cloud` and an
>    `endpoint_id` different from the actor's), never by an agent's own claim; a row with
>    `correlation: "unknown"` is never that proof (§4.2, §4.4).

with:
> 3. This independence check applies only to a local-routed actor's work: for such work, a gate
>    verdict is accepted only if the dispatch record of the run that produced it shows
>    `hosting_location: cloud` and an `endpoint_id` different from the actor's dispatch, proven
>    by the record, never by an agent's own claim; a row with `correlation: "unknown"` is never
>    that proof. A cloud-routed actor's same-run checker (today's `conformance`/
>    `docs-conformance`/`code-review` nodes, and `refine`/`plan`'s in-process subagent checkers)
>    is unchanged by this contract (§4.2, §4.4).

§4.2 — in the "The refusal is enforced by the harness, not by the model" bullet, replace:
> The testable rule is: a gate verdict is accepted only if the dispatch record of the run that
> produced it has `hosting_location: cloud` and an `endpoint_id` different from the actor's
> dispatch. A verdict produced inside a local-routed container is void — it can neither pass nor
> fail a gate — and the ticket escalates to `ready-for-human`. That check is a
> `deterministic_check`-provenance event evaluated outside the agent.

with:
> The testable rule is: **for work produced by a local-routed actor**, a gate verdict is accepted
> only if the dispatch record of the run that produced it has `hosting_location: cloud` and an
> `endpoint_id` different from the actor's dispatch. This bullet is the single, normative
> statement of the proof; Invariant 3 restates it. A verdict produced inside a local-routed
> container is void —
> it can neither pass nor fail a gate — and the ticket escalates to `ready-for-human`. That check
> is a `deterministic_check`-provenance event evaluated outside the agent. A cloud-routed actor's
> checker that happens to run in the same dispatch (today's `conformance`/`docs-conformance`/
> `code-review` nodes, and `refine`/`plan`'s in-process subagent checkers) needs no separate
> dispatch and is unaffected: this rule changes nothing about today's all-cloud path.

Invariants index — replace item 2:
> 2. An independent checker is always a separate dispatch, cloud-routed by default. A nested
>    Agent-tool subagent of a local-routed parent is never a checker, and a verdict produced
>    inside a local-routed container is void (§4.2).

with:
> 2. For a local-routed actor's work, an independent checker is always a separate dispatch,
>    cloud-routed by default. A nested Agent-tool subagent of a local-routed parent is never a
>    checker, and a verdict produced inside a local-routed container is void (§4.2).

§4.2 — in the "Endpoint-role binding happens at dispatch" bullet, replace:
> It may never also host an independent `checker` for the same unit of work

with:
> A local-routed container may never also host an independent `checker` for the same unit of
> work

(Re-wrap the bullet. Without these two edits, Invariant 2 and this bullet would still say every
checker is a separate dispatch, next to the "needs no separate dispatch" sentence above.)

§4.1 — in the #559 row, replace:
> Consumes Q1's dispatch-record independence proof

with:
> Consumes §4.2's dispatch-record independence proof

(The "Consequence for today's DAG" bullet immediately below is already consistent with this
scoping and is unchanged. §4.2 stays normative and the index restates it, as the index's own
preamble requires: "Where the wording differs, the cited section governs.")

"About this document" — append to its second paragraph, after "…this contract's own scope limits
are Requirement 7 and §4.10.":
> Where a Brainstorming Q&A answer differs from §4.1–§4.12 as revised by #580, the section
> governs — notably Q1's "different secret reference" proof, Q2's unscoped checker-endpoint
> check and "unknown on either side" wording, and Q3(a)'s `qualified_roles` and result states.

### R3 — Session-window citation fix

§4.10 — replace the 429/session-window bullet:
> A local endpoint's rate-limit or HTTP 429 must never write the shared session-window pause
> sentinel: `session_window.py` detects exhaustion from 429/rate-limit text in a run's captured
> stdout, so a local fault could otherwise pause cloud work. Such an event is a local failure
> under §4.6. The change that guarantees this touches session-window logic and therefore needs
> its own human-reviewed ticket; it may not land as a side effect of #557–#561.

with:
> A local endpoint's rate-limit or HTTP 429 must never write the shared session-window pause
> sentinel: `session_window.py` detects session-window exhaustion from usage/session/credit-
> exhaustion phrasing or a rejected structured `claude.rate_limit_event` marker
> (`is_session_window_failure`) — not from a bare HTTP 429 (a bare 429 only feeds the breaker's
> `environmental:rate_limit` signature via the separate, broader `RATE_LIMIT_RE`, never the
> stricter session-pause detector). A local endpoint that emits usage/session/credit-exhaustion
> phrasing, or a fabricated `claude.rate_limit_event` marker, in captured stdout could still trip
> the shared pause by coincidence. Such an event is a local failure under §4.6. The change that
> makes the session-window detector local-endpoint-aware touches session-window logic and
> therefore needs its own human-reviewed ticket (#586), a rollout precondition in §4.12, and may
> not land as a side effect of #557–#561.

### R4 — Missing credential invariant

§4.3 — add a new bullet immediately after the existing "Credential stripping" bullet:
> - **Credential scope for a local-routed container.** A local-routed container is issued no
>   cloud model credential at all (not merely stripped before forwarding) and sends none to the
>   local endpoint. It is issued no tracker or code-host write credential (e.g. no `GH_TOKEN`
>   with push/comment/label scope) unless its own reviewed implementation spec explicitly grants
>   one — #557 and #558 already require this for the endpoint/runtime-adapter surfaces they own;
>   this contract states it as a cross-cutting invariant so #559–#564 inherit it too.

Invariants index — append as item 16 (keeps `len(items) >= 15` true and avoids renumbering):
> 16. A local-routed run container holds no cloud model credential and sends none to the local
>     endpoint; it holds no tracker or code-host write credential unless its own reviewed spec
>     explicitly grants one (§4.3).

### R5 — Helper dispatch definition (§4.11 decision 3)

Replace §4.11 decision 3's body (keep the "Accepted operator default..." marker from R1):
> None of the existing gated phases (refine, plan, implement, conformance, docs-conformance,
> code-review) may be local-routed, because each runs its checker in the same container (§4.2).
> Only new helper dispatches that have no checker are eligible. Running a real phase locally
> needs gates split into a separate dispatch; that is its own reviewed gate ticket and is out of
> scope for this contract and for #557–#561.

with:
> None of the existing gated phases (refine, plan, implement, conformance, docs-conformance,
> code-review) may be local-routed, because each runs its checker in the same container (§4.2).
> Only new **helper dispatches** are eligible — defined here concretely: a helper dispatch is a
> separately scheduled run container with its own endpoint config, doing non-gating work with no
> in-container checker, whose output is only an artifact until a cloud-routed check in a
> *different* dispatch accepts it (proven by the dispatch record, §4.2). In v1 a helper dispatch
> is limited to work whose declared payload class is `cloud_ok` or absent (§4.3). No ticket
> among #557–#564 builds the first concrete helper-dispatch instance — their scopes are
> registry/preflight, runtime adapter, route isolation, deploy profile, fixtures, certification,
> offline/shadow economics comparison, and the opt-in canary. #587 builds it, and depends on
> #585 (the no-auto-merge end-gate) and #586 (local-aware session-window detection).
> Running a real phase locally needs gates split into a separate dispatch; that is its own
> reviewed gate ticket and is out of scope for this contract and for #557–#561.

(Cite issue numbers, never this refine spec's `docs/superpowers/specs/` path — that file is
archived to `docs/archive/` when this ticket's PR is pushed, per the doc test's own "nothing here
may reference the spec's `docs/superpowers/specs/` path" rule; issue numbers are the durable
pointers.)

#### Operator follow-ups (issue-body edits, applied by the operator when the contract PR merges)

These are not part of the contract file and are not applied by the implement phase. #585, #586
and #587 were filed on 2026-11-23 and need no edit. The text in each block below is paste-ready.

**#557** — append to "Acceptance criteria":

```markdown
- [ ] Implement the declared payload-class field, the dispatch-time predicate and its recorded decision (contract §4.3), and the operator-controlled storage surface for per-endpoint capability-evidence records (contract §4.5). No scheduler-dispatched container can write either.
```

**#558** — append to "Acceptance criteria":

```markdown
- [ ] Record `cost_usd: null` for every node of a local-routed run, even when the harness reports a Claude-priced figure (contract §4.10); never zero.
```

**#559** — append to "Scope and boundaries":

```markdown
Reconciliation with the hybrid execution contract (#556, revised by #580): in v1 the isolation unit is the dispatched run container, which has exactly one endpoint config. "Role/node-scoped selection" here means binding a dispatch's single endpoint to a declared role and rejecting nodes or nested Agent calls whose role does not match. It is not a request-level router that mixes endpoints inside one container; that is the contract's Q1 option (b) and needs its own human-reviewed spec. A "local worker + cloud checker" demonstration means two separate dispatches, where the checker's dispatch record shows `hosting_location: cloud` and an `endpoint_id` different from the worker's. Acceptance criteria 3 and 6 are read per dispatch: nested Agent calls inherit the dispatch's endpoint, recorded per request, and are allowed for actor work only.
```

**#559** — replace acceptance criterion 1 ("A hermetic run demonstrates a local worker and cloud
checker concurrently with exact role/request attribution and no cross-role credential leakage.")
with:

```markdown
- [ ] A hermetic test demonstrates a local worker dispatch and a separate cloud checker dispatch with exact role/request attribution and no cross-role credential leakage.
```

**#563** — append to "Scope and boundaries":

```markdown
Prerequisite (contract §4.11 decision 3): the preview classifier runs today as a DAG node inside the same run container as gated phases, so it is not dispatch-isolated and cannot have its own local-routed arm in production. For this ticket the local arm must run in the offline/shadow runner (its own credential-isolated container, artifact-only output), which this ticket already requires. Any production-path local preview classification first needs the classifier extracted into a separate helper dispatch (#587). Choosing another workload that is already a separate dispatch is acceptable.
```

**#564** — add three lines to the `Depends on:` block at the top of the body:

```markdown
Depends on: #587
Depends on: #585
Depends on: #586
```

**#564** — append to "Scope and boundaries":

```markdown
Per contract §4.11 decision 3, the canary workload must be a checker-free helper dispatch in its own container (#587), and its "independent cloud checker" must be a separate cloud-routed dispatch proven by the dispatch record. The canary uses work whose declared payload class is `cloud_ok` or absent, because a cloud checker may not read the output of declared-`local_only` work. Its output is local-pinned and never auto-merges (decision 5, enforced by #585). There is no cloud fallback: a later cloud run is a new operator dispatch decision, never a retry of the failed local dispatch. Running any existing gated phase (refine/plan/implement/conformance/docs-conformance/code-review) locally is out of scope until a separate reviewed gate-split ticket lands.
```

### R6 — `local_only` dual meaning

§4.3 — replace the six bullets from "**Payload class**" through "**Non-exporting outage
behavior**" (inclusive — i.e. everything up to, but not including, the existing "Credential
stripping" bullet) with:
> - **Declared payload class** (`payload_class`: `cloud_ok` / `local_only`, optional) is set per
>   issue/run only by operator-controlled config (gitignored instance config or a human-reviewed
>   adapter key) — never by a label, comment, or model output (all of which are `self_report`
>   under #288's taxonomy). It is stricter-only: no label, comment or model output can loosen it.
> - **Destination class** is declared per endpoint-registry entry (#557): hosting location
>   (`hosting_location`: `local`/`cloud`) and which payload classes it may receive
>   (`accepts_payload_classes`, a non-empty subset of `cloud_ok`/`local_only`). A `cloud` entry
>   may never list `local_only`; a `local` entry must list `local_only`. The payload-class field
>   and the dispatch-time predicate below are #557's to implement (not #558, whose scope excludes
>   eligibility decisions).
> - **Effective class**, computed once, deterministically, at dispatch time: a
>   `local`-destination dispatch is `local_only` unconditionally, whatever the declared class; a
>   `cloud`-destination dispatch is the declared class if one is set, else `cloud_ok` — exactly
>   as today's unmarked ticket (§4.11 decision 4). An absent declared class on a cloud dispatch
>   never blocks.
> - **`local-pinned`** is a derived, never-operator-set property of a dispatch record
>   (`hosting_location: local`), distinct from `payload_class`. It carries two rules that hold
>   for *every* local dispatch regardless of declared class: no cloud retry or fallback, ever
>   (Invariant 6), and no auto-merge, ever (§4.11 decision 5). `payload_class` carries only
>   confidentiality/export eligibility — this separates "never retries on cloud" (a
>   `local-pinned` property) from "this payload is confidential" (a `payload_class` property),
>   the two meanings `local_only` previously conflated.
> - **Dispatch-time check:** a dispatch is allowed if and only if the *effective* class is a
>   member of the target entry's `accepts_payload_classes`. Blocks + escalates on: a declared
>   value outside `{cloud_ok, local_only}`; a registry entry with invalid/missing
>   `hosting_location` or `accepts_payload_classes`; or an effective class absent from the
>   target's `accepts_payload_classes` (e.g. a declared-`local_only` ticket aimed at a cloud
>   entry). Any of these three cases always blocks and escalates — it is never silently
>   rerouted to a default; only an absent *declared* class on a cloud dispatch has a default
>   (`cloud_ok`), and that is not a missing/unknown case. This check's decision (payload class,
>   endpoint id, secret reference, allow/deny, reason) is recorded in the existing run-record
>   ledger extension (§4.7), never a new store.
> - **May a cloud checker read the output of a `cloud_ok` ticket that ran locally?** Yes — a
>   `local`-routed dispatch whose **declared** class is `cloud_ok` or absent is `local-pinned` (no
>   retry, no auto-merge) but not confidential; a separate cloud-routed checker dispatch may read its
>   output, since the output inherits the confidentiality of inputs that were already
>   cloud-eligible. A declared-`local_only` dispatch's output may be checked only by a human or a
>   separate local dispatch — never a cloud checker. **Consequence:** v1 helper dispatches (§4.11
>   decision 3) are limited to work whose declared class is `cloud_ok` or absent, since that is
>   the only case with a valid independent-checker path.
> - **No auto-merge (§4.11 decision 5, restated — strictly stronger, weakens nothing):** neither
>   a `local-pinned` dispatch's output nor any `local_only`-classed payload ever auto-merges (no
>   `direct-to-pr` end-gate auto-merge, no grace-timer advance to merge); the merge is always a
>   human action. The scheduler end-gate check that enforces this is not owned by #557–#564; it
>   needs its own human-reviewed gate ticket (#585).
> - **Non-exporting outage behavior:** a `local-pinned` dispatch that fails (outage, OOM,
>   protocol incompatibility, timeout, stale/missing capability evidence) never retries against
>   a cloud endpoint — this is a property of being local-pinned, not of `payload_class`. It
>   records the failure under one of #238's new failure classes and abstains/escalates. Two
>   local failures, a model's stated confidence, or text in the task itself cannot grant export
>   permission for a future dispatch — only operator-controlled config can, and such a later
>   cloud run is always a *new* dispatch decision, never a runtime fallback of the failed
>   local-pinned dispatch itself.

Invariants index — replace items 4–7:
> 4. A dispatch is allowed only if the payload class is a member of the target entry's
>    `accepts_payload_classes`; unknown or missing on either side blocks and escalates (§4.3).
> 5. Payload class is stricter-only: no label, comment or model output can loosen it; only
>    operator-controlled config can grant `cloud_ok` (§4.3).
> 6. A local-routed dispatch never retries on, or falls back to, cloud. Every local failure,
>    timeout or cancellation resolves to a #238 failure class and abstains or escalates (§4.3,
>    §4.6).
> 7. `local_only` work never auto-merges; the merge is always a human action (§4.3; provisional
>    operator default, §4.11 decision 5).

with:
> 4. A dispatch is allowed only if its *effective* payload class is a member of the target
>    entry's `accepts_payload_classes`; an out-of-enum declared value, an invalid registry
>    entry, or an effective class absent from `accepts_payload_classes` blocks and escalates. An
>    absent declared class on a cloud dispatch defaults to `cloud_ok` and never blocks (§4.3).
> 5. Payload class is stricter-only: no label, comment or model output can loosen it; only
>    operator-controlled config (gitignored instance config or a human-reviewed adapter key) can
>    set or change the declared class (§4.3).
> 6. Every dispatch with `hosting_location: local` is `local-pinned` and never retries on, or
>    falls back to, cloud — a later cloud run is always a new dispatch decision, never a
>    fallback of the failed one. Every local failure, timeout or cancellation resolves to a #238
>    failure class and abstains or escalates (§4.3, §4.6).
> 7. Neither a `local-pinned` dispatch's output nor any `local_only`-classed payload ever
>    auto-merges; the merge is always a human action (§4.3; accepted operator default, §4.11
>    decision 5).

**Required test update for this edit (beyond R1's two assertions):** the rewritten
dispatch-time-check bullet no longer contains the literal string `"Unknown or missing on either
side → block + escalate."` (its only occurrence in the file today) — this is intentional, since
that blanket phrasing is exactly the contradiction this revision resolves (an absent *declared*
class on a cloud dispatch no longer blocks). In `tests/test_hybrid_execution_contract_doc.py`'s
`FAIL_CLOSED_INVARIANTS` list, replace:
```python
    "Unknown or missing on either side → block + escalate.",
```
with:
```python
    "blocks and escalates — it is never silently rerouted",
```
(matching the new sentence added to the dispatch-time-check bullet above). No other
`FAIL_CLOSED_INVARIANTS` entries need a text change: `"`local_only` work never auto-merges"`
and `"never retries against a cloud"` both still occur verbatim elsewhere in the file (§4.11
decision 5's own body, which R1–R13 do not alter per the issue's "do not change the five
decisions" constraint, and R6's rewritten "Non-exporting outage behavior" bullet,
respectively). All three pins are matched against whitespace-normalised text (R1's rewrite of
`test_fail_closed_invariants_pinned`), so a line re-wrapped inside a pinned phrase cannot break
them — as copied from this spec, "never retries against / a cloud endpoint" and "never silently
/ rerouted" both break across a line. Confirm all three resolve before treating this test as
green.

### R7 — Unowned mechanisms

§4.2 — in the testable-rule bullet (already edited by R2), append one clause: "(the dispatch
record's schema — `hosting_location`, `endpoint_id`, `correlation` — is #558's Result-contract
row, §4.7)".

§4.7 — in the table, replace the Result row's "Carries" cell:
> Outcome, failure class (if any), destination actually used

with:
> Outcome, failure class (if any), destination actually used, and the dispatch-record fields
> §4.2's independence check relies on (`hosting_location`, `endpoint_id`, `correlation`)

§4.7 — in the paragraph under the table, replace:
> All four are specified here only; #557/#558/
> #561 implement them as new additive top-level fields on `run_record.py`'s existing per-run
> record, following the `harness_economics`/`memory_trace` extension idiom — no new datastore
> (consistent with the #207 architecture-memory entry below).

with:
> All four are specified here only. #558 implements the Request and Result contracts as new
> additive top-level fields on `run_record.py`'s existing per-run record, following the
> `harness_economics`/`memory_trace` extension idiom — no new datastore (consistent with the
> #207 architecture-memory entry below). The Configuration contract (#557) and the
> capability-evidence record (#561/#562) are per-endpoint: they live on the operator-controlled
> instance-config surface (§4.5), and the per-run record cites them by `endpoint_id` and
> `schema_version`.

(Without this edit §4.7 would still put the capability-evidence record in the per-run record,
which R9's §4.5 forbids.)

(Payload-class field/predicate → #557, and the no-auto-merge end-gate check → #585, are
already stated in R6's rewritten §4.3 bullets above — no separate edit needed.)

### R8 — Failure-class ordering: interim enumeration

§4.6 — replace the closing paragraph:
> The class names are #238's to choose, but its taxonomy must distinguish at least these
> local-endpoint outcomes: endpoint unreachable; timeout (connect, time-to-first-token, idle,
> total); cancellation; protocol incompatibility; capability evidence missing, expired or
> mismatched; destination-policy denial. None of them is retryable against a different endpoint.

with:
> The class names are #238's to choose. Until #238 lands, the list below is this contract's
> **interim enumeration**, which #558 implements directly (#558 depends only on #557, not on
> #238, so it is not blocked waiting for #238's taxonomy). #238's taxonomy must distinguish at
> least these local-endpoint outcomes: endpoint
> unreachable; timeout (connect, time-to-first-token, idle, total); cancellation; protocol
> incompatibility; capability evidence missing, expired or mismatched; destination-policy
> denial. None of them is retryable against a different endpoint. When #238 ships its own class
> names, #558's interim enumeration is superseded by them, not duplicated alongside them.

### R9 — Capability evidence: storage, write authority, vocabulary

§4.5 — replace the entire section body (keep the `### 4.5 Capability evidence` heading) with:
> Per Q3(a), tightened by #580's post-merge review. A capability-evidence record is
> **per-endpoint and long-lived, not a per-run record**: one record per `endpoint_id`, stored as
> a sibling record next to (not a field of) its #557 registry entry, on the same
> operator-controlled, human-only instance-config surface as the registry entries and
> payload-class grants (exact path left to #557's own refinement) — never in the per-run record
> or ledger, which may only cite it by `endpoint_id` + `schema_version`.
>
> **Who may write it:** only an operator, acting on the output of #562's human-gated
> certification run (`ready-for-human`, never scheduler-dispatched) executing #561's pinned
> fixtures. No factory phase agent may create or edit a record, however privileged; no
> scheduler-dispatched container may be able to write it (the mechanism is #557's and #560's to
> specify). A record-shaped artifact appearing in
> a branch, PR diff, issue comment or run artifact is `self_report` and is ignored. Preflight
> rejects a record missing `certified_by` or whose `fixture_suite_version` does not match the
> currently pinned suite.
>
> ```text
> capability_evidence:
>   schema_version: "1.0"
>   endpoint_id: <#557 registry key>
>   model_identity: {served_name, digest_or_revision, quantisation}
>   runtime_stack: {name, version, chat_template, context_settings}
>   harness_version: <pinned Claude CLI/SDK version>
>   fixture_suite_version: <pinned commit/digest>
>   results: {<capability_dimension>: pass | fail | unsupported | not_tested, ...}
>   qualified_roles: [actor]          # §4.4's enum; never "checker" or "monitor" — see §4.2
>   certified_by: <operator identity>
>   certified_at: <ISO8601>
>   expires_at: <ISO8601>
>   invalidated_by: [model_digest, quantisation, runtime_version, chat_template, context_settings,
>                    harness_version, fixture_suite_version, registry_config]
> ```
>
> `qualified_roles` uses §4.4's normative `role` enum: v1 evidence may only ever list `actor`
> (§4.2 already treats `worker`/`orchestrator` as kinds of `actor`), never `checker` or
> `monitor`. `actor` qualification under §4.11 decision 3 certifies a checker-free helper
> dispatch only — it never advertises full-agent or checker capability (#561).
>
> Per-dimension `results` values are `pass | fail | unsupported | not_tested` (`unsupported`
> means the endpoint's protocol/runtime cannot do this at all, proven by a fixture). A separate,
> derived evidence state — `tested | declared | stale | unknown` — is computed at preflight read
> time from `expires_at`/`invalidated_by` fingerprint comparison and is never stored or
> operator-set: `declared` (the registry entry declares a capability but no valid record exists
> yet), `stale` (`expires_at` has passed, or any `invalidated_by` fingerprint differs from what
> is observed), `unknown` (no record exists, or a fingerprint cannot be resolved), `tested`
> (none of the above). A role is eligible only if the derived state is `tested` and every
> dimension that role requires is `pass`; every other combination (per-dimension
> `fail`/`unsupported`/`not_tested`, or derived `declared`/`stale`/`unknown`) fails closed —
> missing, expired, failed, or mismatched evidence blocks and escalates exactly like a failed
> certification, no grace period.
>
> Capability dimensions (minimum set #561's fixtures must cover): protocol compatibility,
> tool-call fidelity, streaming behavior, context-length handling, role-isolation (confirms the
> endpoint cannot be mistaken for an independent checker), and timeout/cancellation behavior
> (§4.6).

`tests/test_hybrid_execution_contract_doc.py` — in
`test_capability_evidence_schema_never_qualifies_a_checker`, replace:
```python
    assert "qualified_roles: [worker]" in section
    for field in ("endpoint_id:", "model_identity:", "fixture_suite_version:", "expires_at:",
                  "invalidated_by:"):
        assert field in section, f"capability-evidence field {field} missing"
```
with:
```python
    roles = re.search(r"^\s*qualified_roles: \[([^\]]*)\]", section, re.MULTILINE)
    assert roles and [r.strip() for r in roles.group(1).split(",")] == ["actor"]
    for field in ("endpoint_id:", "model_identity:", "fixture_suite_version:", "expires_at:",
                  "invalidated_by:", "harness_version:"):
        assert field in section, f"capability-evidence field {field} missing"
    assert "unsupported" in section
```

### R10 — `cost_usd: null` owner

§4.10 — append to the existing "Local token counts are recorded..." bullet:
> `run_record.py` still parses a missing node cost as `0`
> (`n.get("costUsd") or n.get("cost_usd") or 0`); since #453 it then re-records a command or
> prompt node that reported no usage, or that started but is absent from the cost report, as
> `cost_capture: "missing"` with `cost_usd: null`. That does not cover a local-routed node for
> which the harness reports a Claude-priced figure: #558 owns recording `null` for every node of
> a local-routed run, and that lands before any local run is recorded in `runs.jsonl` or a cost
> report.

### R11 — Rollout rule alignment

§4.12 — replace the "**Rollout rules.**" paragraph:
> Downstream tickets are opted in one at a time, in the order of their `Depends on:` lines.
> Merging any of them activates nothing: local endpoint selection is off by default, and is
> enabled only by operator-controlled instance config (a human-only surface —
> `deploy/instances/**` is never edited by a factory agent) after #562 has produced valid
> capability evidence for that endpoint. #562 and #564 are human-run and never scheduler-
> dispatched.

with:
> Downstream tickets are opted in one at a time, in the order of their `Depends on:` lines.
> Merging any of them activates nothing. #562's certification run and #563's offline/shadow
> runner are not dispatches in this contract's sense: they run outside the scheduler against
> non-sensitive fixtures, so the no-evidence-no-dispatch rule does not apply to them. #562's GO
> covers offline/shadow evaluation only (no production routing, no alias change); #563 extends
> that comparison with economics data, still with no write authority over production routing. A
> real workload runs locally only inside #564's canary, under the operator's prior written
> approval of that one lane. That approval requires #563's comparison, #239's static routing and
> #238's failure policy active for the lane, the first helper dispatch (#587), the no-auto-merge
> end-gate (#585) and the local-cost fix (#558, §4.10; #453's missing-cost fix has landed); the
> lane is enabled only via
> operator-controlled instance config (a human-only surface — `deploy/instances/**` is never
> edited by a factory agent). #564's signed GO/NO-GO is required before any wider use. A rollout
> precondition not owned by #557–#564: the session-window detector must be made
> local-endpoint-aware (#586, §4.10) before any local dispatch runs, so a local fault's captured
> stdout cannot trip the shared pause sentinel. #562 and #564 are human-run and never
> scheduler-dispatched.

### R12 — Keyword signal scoped to local-eligibility

§4.3 — add a new bullet (placement: after the new Credential-scope bullet from R4, before
"Unqualified/unverifiable routes..."):
> - **`safety.sensitive_keywords` reuse (stricter-only, local-eligibility only):**
>   `.factory/adapter.yaml`'s existing `safety.sensitive_keywords` regex may be reused as a
>   *stricter-only* signal restricted to **local-eligibility decisions**: a hit never changes the
>   declared class and is evaluated only when a ticket is considered for a local dispatch. That
>   dispatch's output is then treated as declared-`local_only` (human or local check only, so not
>   a v1 helper-dispatch candidate) and the ticket escalates. It never authorizes anything, never
>   affects a cloud-routed dispatch (today's `github/github/anthropic` path is completely
>   unchanged by this signal), and never loosens any class. It is advisory/evadable
>   regex over ticket text — not an egress control — so it is never the sole mechanism gating a
>   local dispatch; the dispatch-time predicate above is.

(This also resolves the dangling Alternatives-3 cross-reference to "a stricter-only signal
(§4.3)", which previously pointed at a statement §4.3 didn't actually contain.)

### R13 — Nits

a. Alternatives Considered, item 4 — replace:
> that config governs which tools/commands a phase may execute (#196's unbuilt enforcement
> scope), an orthogonal concern to where model-request data is sent

with:
> that config governs which tools/commands a phase may execute (enforced today by the per-level
> permission profiles in `scripts/factory_core/side_effect.py`, #196), an orthogonal concern to
> where model-request data is sent

(The path is written in full because a bare `side_effect.py` is not on the doc test's
`PARTIAL_CITATION_ALLOWLIST` and would fail `test_cited_repo_files_resolve`.)

b. Overview — replace:
> Every dispatched run container gets exactly one `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`
> pair from `.archon/.env`, read once by Claude Code at process start;

with:
> Every dispatched run container's auth comes from exactly one `.archon/.env` by default —
> `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, not `ANTHROPIC_AUTH_TOKEN` (which nothing on
> `main` sets), and with `ANTHROPIC_BASE_URL` unset (today's direct-to-Anthropic default;
> `ANTHROPIC_BASE_URL` is set only when opting into the `model_proxy.py` ledger via
> `FACTORY_MODEL_PROXY_ENABLED`) — read once by Claude Code at process start;

c. §4.11 decision 2 — append a note (the decision's own sentences are unchanged):
> Note (#580): the literal cloud model IDs pinned in `config/config.yaml` —
> `conformance.shadow_model` (`claude-fable-5-1`) and `epic_autopilot.model`,
> `main_red_autofix.model` and `deps_audit.model` (`claude-opus-5-5`) — are untouched and do not
> resolve through the tier-alias mapping above, so the alias preflight does not see them. A
> request from a local-routed container that names any model other than the certified one,
> including a literal cloud model ID, is refused, never remapped (#559).

d. Already resolved by R6's rewrite of Invariant 5, which now cites both operator-controlled
mechanisms ("gitignored instance config or a human-reviewed adapter key") matching Q2's
original answer wording — no separate edit.

e. `tests/test_hybrid_execution_contract_doc.py::test_cited_repo_files_resolve` — the current
`CITATION_RE.findall(text)` call misses any backtick-quoted citation whose content is
line-wrapped (confirmed on current `main`: `` `scripts/factory_core/\n  providers/model.py` ``
and `` `docs/superpowers/specs/\n  2026-11-19-effective-config-widening-design.md` `` both span
two physical lines and are silently skipped today). Fix by collapsing whitespace inside each
backtick span before searching:
```python
def test_cited_repo_files_resolve():
    text = _doc_text()
    collapsed = re.sub(r"`([^`]*)`", lambda m: "`" + re.sub(r"\s+", "", m.group(1)) + "`", text)
    files = _tracked_files()
    missing = []
    for tok in sorted(set(CITATION_RE.findall(collapsed))):
        if (REPO_ROOT / tok).is_file():
            continue
        if tok in PARTIAL_CITATION_ALLOWLIST and any(f.endswith("/" + tok) for f in files):
            continue
        missing.append(tok)
    assert not missing, f"cited files no longer resolve: {missing}"
```
(The weakened "no longer checks there are five decisions" half of this nit is R1's
`test_section_4_11_has_five_accepted_decisions` above. After R13g below, the first of the two
wrapped citations is the only one left in the file.)

f. `docs/provider-abstraction-design.md` §7.5 — in the "> **Hybrid cloud + local (#556).**"
pointer paragraph, replace:
> As a provisional operator default pending owner confirmation (§4.11 of the contract), v1
> supports one local server that speaks the Anthropic Messages API through `ANTHROPIC_BASE_URL`
> (no translation layer), isolates endpoints per dispatched run container, keeps every
> independent checker on a separate cloud-routed dispatch, and never falls back from a local
> failure to cloud.

with:
> As an accepted operator default (§4.11 of the contract, confirmed in #580), v1 supports one
> local server that speaks the Anthropic Messages API through `ANTHROPIC_BASE_URL` (no
> translation layer), isolates endpoints per dispatched run container, and — whenever a
> local-routed actor is involved — requires that actor's independent checker to be a separate
> cloud-routed dispatch (today's all-cloud checkers, e.g. `conformance`/`code-review`, are
> unaffected and keep running in-process), and never falls back from a local failure to cloud.

(`test_provider_design_model_axis_points_at_the_contract` only pins the markdown link and
`(#556)`, both preserved — no test change needed for this nit.)

g. Assumptions (flagged), first bullet — replace:
> (no stale-branch drift of the kind `docs/superpowers/specs/
>   2026-11-19-effective-config-widening-design.md` had to correct for).

with:
> (no stale-branch drift of the kind the 2026-11-19 effective-config-widening refinement, #180,
>   had to correct for).

(That path is a live spec which `push-and-pr` archives when #180's PR is pushed. Once R13e makes
the doc test resolve line-wrapped citations, the archive move would turn that PR's CI red — the
PR #215 hazard the original plan avoided for this contract's own spec. An issue number does not
move.)

## Alternatives Considered

1. **Rewrite the whole contract from scratch** instead of a targeted revision pass. Rejected —
   the post-merge review found no blocking defect and no weakened safety surface; a full rewrite
   would re-churn fourteen already-reviewed sections for thirteen should-fix items, most of
   which are narrow textual corrections with a single verified root cause.
2. **Fold the §4.5 capability-evidence storage location into #557's own refinement** instead of
   stating it here. Rejected — the issue's revision 9 explicitly asks this contract to say where
   the record lives (even if the exact path is left to #557); leaving it fully unstated here
   would re-open the "is it per-run or per-endpoint" ambiguity the review flagged.
3. **Resolve the helper-dispatch and no-auto-merge gaps by silently assigning them to the
   nearest-scoped existing ticket** (e.g. folding "build the first helper dispatch" into #558,
   or the no-auto-merge gate into #559). Rejected per Q1/Q2 — each existing ticket's own scope
   text excludes the gap's work; misassigning it would mislead that ticket's future refinement
   rather than fix the misleading-ness this revision exists to remove.
4. **Add a configurable confidentiality field on top of `payload_class`** instead of the derived,
   non-configurable `local-pinned` term. Rejected per Q2 — every local dispatch is unconditionally
   local-pinned already (decision 4), so a configurable field would add surface with no case it
   actually distinguishes.

## Open Questions (non-blocking)

- Exact storage path for the per-endpoint capability-evidence record and the endpoint registry
  (§4.5/§4.7) — left to #557's own refinement, as before.
- Whether #238's eventual failure-class names will textually match this contract's §4.6 interim
  enumeration closely enough to need no further contract revision, or will themselves require a
  follow-up revision of this file — not knowable until #238 is refined.
- None on the follow-up tickets: #585, #586 and #587 were filed on 2026-11-23 (see "Operator
  decisions" above), so the contract cites them by number.

## Assumptions (flagged)

- This checkout's state (`docs/hybrid-execution-contract.md`, `docs/provider-abstraction-
  design.md`, `tests/test_hybrid_execution_contract_doc.py`, `scripts/factory_core/
  session_window.py`, `run_record.py`, `side_effect.py`, `deploy/instance.env.example`) was read
  live on 2026-11-22 and is assumed representative of `main`; the refine branch
  (`refine/issue-580-...`) forked at the current `main` tip (`4584159`), so no re-sync drift
  check was needed (contrast the `#180` precedent in `.archon/memory/architecture.md`).
- Issues #238, #453, #557–#564 were read live via `gh issue view` at refinement time
  (2026-11-22) and are assumed stable; if any has since been substantially re-scoped, the owner
  attributions in R7/R8/R10/R11 and the issue-body-edit recommendations in R5 should be
  re-checked against the current body before being relied on.
- The "two test assertions that pin the old wording" (revision 1) are read as exactly the two
  identified in R1 (the header test's `"provisional operator defaults pending owner
  confirmation"` line and the fail-closed test's `text.count("Provisional operator default
  (2026-11-22)")` line) — no other test file references that phrase.
- The historical "Brainstorming Q&A" dialogue reproduced in the contract file's own
  `## Requirements` section is treated as refinement-time history that this revision does not
  edit, per the file's own "About this document" framing ("the body keeps its refinement-time
  wording"); only the normative `## Architecture / Approach` subsections (and the header,
  Invariants index, Alternatives, and the `provider-abstraction-design.md` pointer) are revised.
  R2 adds one sentence to "About this document" saying the sections govern where a Q&A answer
  differs.

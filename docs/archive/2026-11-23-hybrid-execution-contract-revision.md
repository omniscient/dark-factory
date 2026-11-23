# Hybrid Execution Contract Revision (#580) — Implementation Plan

**Issue:** #580

**Revised:** 2026-11-23 (operator amendment after plan gate)

**Goal:** Apply the thirteen post-merge review revisions (R1–R13) from the approved spec
`docs/superpowers/specs/2026-11-22-hybrid-execution-contract-revision-design.md` to
`docs/hybrid-execution-contract.md`, the matching assertions in
`tests/test_hybrid_execution_contract_doc.py`, and the one §7.5 pointer paragraph in
`docs/provider-abstraction-design.md`. The edit is docs and doc-test only, and none of the five
§4.11 decisions changes.

**Architecture:** A targeted text-revision pass. Each task maps to one spec revision (R1–R13). Where a
revision changes what the doc test pins (R1, R6, R9, R13e), the test is edited first and run to
show it fails, then the doc is edited and the test run to show it passes. Where a revision is prose
only, a `grep` confirms the old text is there before the edit and gone after it, and the full doc
test is re-run to show nothing else broke.

**Tech Stack:** Markdown, Python 3 + pytest (`tests/test_hybrid_execution_contract_doc.py`).

---

## Conventions for every task

- **Branch:** the implement phase runs on `feat/issue-580-*`. The spec and this plan are already
  there because `setup-branch` copies them over (`scripts/transfer_refine_artifacts.sh`, #387).
- **Test command:** run
  `PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v` from the repo
  root. The file has 12 tests today and will have 13 after Task 1.
- **Quoting:** the spec shows replacement text inside `> ` blockquotes. That is only how the spec
  presents it. The **doc text** blocks in this plan are the literal bytes to write: no `> `
  prefix, wrapped at ≤100 columns like the rest of the file, and list continuation lines indented
  to match their item (2 spaces for `-` bullets, 3 spaces for numbered items).
- **One wrapping rule.** Every pin (`FAIL_CLOSED_INVARIANTS`, the header test, the five-decision
  test) is whitespace-normalised after Task 1, so a pinned phrase may wrap. One layout rule still
  applies, because the tests' regexes rely on line starts: inside §4.11 and the Invariants index,
  never let a line start at column 0 with digits followed by `. ` unless it is a real list item.
  Both tests split on `^\d+\. `. A line that starts with an issue reference such as `#587` is
  fine. The doc already has several (e.g. `#561 implement them…`), and `#` followed by a digit is
  neither a Markdown heading nor a match for `_section()`'s `^#{2,3} `. Never add a line that
  starts with `## ` or `### `, though.
- **Edits:** use exact old-string → new-string replacement. Each task quotes the **current**
  text, which is what the file contains once the earlier tasks have run.
- **Scope:** touch only `docs/hybrid-execution-contract.md`,
  `tests/test_hybrid_execution_contract_doc.py` and `docs/provider-abstraction-design.md`. Never
  touch `scripts/`, `config/`, `workflows/`, `scheduler.sh`, `deploy/**`, any `gate_*`, breaker or
  budget surface.
- **Not part of implement:** the spec's "Operator follow-ups (issue-body edits)" subsection, which
  covers edits to the bodies of #557, #558, #559, #563 and #564. The operator applies those by
  hand after the contract PR merges. Do not edit GitHub issues from this phase.
- **Cite issues, not the spec path:** never cite this ticket's spec path from the contract.
  Push-and-pr archives the spec, so cite issue numbers (#580, #585, #586, #587) instead.
- **Commits:** one commit per task. Each message ends with the attribution trailer the run
  provides.

## File Structure

| File | Change | Tasks |
|---|---|---|
| `docs/hybrid-execution-contract.md` | Header, About, Invariants index (items 2–7, new 16), Overview sentence, §4.1 (#559 row), §4.2, §4.3, §4.5, §4.6, §4.7, §4.10, §4.11, §4.12, Alternatives 4, Assumptions bullet 1 | 1–13 |
| `tests/test_hybrid_execution_contract_doc.py` | Header test pins, `REQUIRED_SECTIONS` §4.11 heading, `FAIL_CLOSED_INVARIANTS` pin swap, fail-closed test normalised, new five-decisions test, capability-evidence test, citation test collapses wrapped spans | 1, 6, 9, 13 |
| `docs/provider-abstraction-design.md` | §7.5 "Hybrid cloud + local (#556)" pointer sentence (line 271, a single physical line) | 13 |

---

## Task 0: Baseline

**Files:** none modified.

- [ ] **Step 1: Confirm the branch and a green baseline**

```bash
git branch --show-current          # expect: feat/issue-580-...
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
```
Expected: `12 passed`.

- [ ] **Step 2: Confirm the citations the spec relies on are still true on `origin/main`**

```bash
git fetch -q origin main
git show origin/main:scripts/factory_core/session_window.py | grep -n "def is_session_window_failure\|^_SESSION_EXHAUSTION_RE\|^RATE_LIMIT_RE\|def _is_exhaustion_event"
git show origin/main:scripts/factory_core/error_signature.py | grep -n "environmental:rate_limit"
git show origin/main:scripts/factory_core/run_record.py | grep -n 'n.get("costUsd") or n.get("cost_usd") or 0'
git show origin/main:scripts/factory_core/side_effect.py | head -8 | grep -n "enforced permission profiles"
git grep -n "ANTHROPIC_AUTH_TOKEN" origin/main -- . ':!docs' ':!tests'
```
Expected: one line per grep pattern (four lines for the first command, one each for the next
three). The last command finds only a docstring
mention in `scripts/factory_core/providers/model.py`, which means nothing *sets* it. If any of these
fail, stop and record the drift in the PR body. Do not invent new citation text.

---

## Task 1: R1 — Status provisional → accepted (header, §4.11, tests)

**Files:** `tests/test_hybrid_execution_contract_doc.py`, `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Update the test first**

In `REQUIRED_SECTIONS`, replace:
```python
    "### 4.11 v1 selected path and provisional operator defaults",
```
with:
```python
    "### 4.11 v1 selected path and accepted operator defaults",
```

Replace the whole `test_header_carries_issue_version_and_acceptance_status` function with:
```python
def test_header_carries_issue_version_and_acceptance_status():
    head = " ".join(_doc_text().split("\n## ", 1)[0].split())
    assert "**Issue:** #556" in head
    assert '**Contract version:** `hybrid_contract: "1.0"`' in head
    assert "**Status:** Accepted" in head
    assert "the #556 PR merged and this is the accepted copy on `main`" in head
    assert "confirmed as written by the owner in #580" in head
```

Replace the whole `test_fail_closed_invariants_pinned` function with these two functions:
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

- [ ] **Step 2: Run it and see it fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
```
Expected: 13 collected, with 3 failures: `test_header_carries_issue_version_and_acceptance_status`,
`test_required_sections_present_in_order` (missing the accepted heading) and
`test_section_4_11_has_five_accepted_decisions` (missing section).

- [ ] **Step 3: Edit the header (doc lines 7–16)**

Replace:
```
**Status:** Proposed; accepted on the merge of the #556 PR by a human reviewer. Until that
merge, no downstream implementation spec (#557–#564, or #239's re-refinement) may be written
against this contract; the copy of this file on `main` is the accepted one. The five §4.11
decisions are provisional operator defaults pending owner confirmation at that review.
```
with:
```
**Status:** Accepted — the #556 PR merged and this is the accepted copy on `main`. The five
§4.11 decisions, carried at merge as provisional operator defaults pending owner confirmation,
were confirmed as written by the owner in #580 (2026-11-22); downstream implementation specs
(#557–#564, #239's re-refinement) may be written against this contract.
```

Leave the existing `**Revised:** 2026-11-22 — spec-gate review amendments: …` paragraph (5 lines)
byte-identical. Directly after its last line (`(§4.1), and a corrected #288 taxonomy citation
(Alternatives 6).`), insert a blank line and then this paragraph. It must come before the blank
line and `---` that follow:
```
**Revised (#580):** 2026-11-23 — post-merge independent-review amendments: acceptance status
(header, §4.11), checker-independence scope (§4.2, Invariant 3), session-window citation and
rollout precondition (§4.10, §4.12), credential invariant (§4.3, new Invariant 16),
helper-dispatch definition (§4.11 decision 3), `local_only`/local-pinned split (§4.3,
Invariants 4–7), mechanism owners (§4.2, §4.3, §4.7), §4.6/#238 interim enumeration,
capability-evidence storage/vocabulary (§4.5), `cost_usd` owner (§4.10), rollout alignment
(§4.12), keyword-signal scope (§4.3), and nits (Overview, About this document, §4.11
decision 2, Alternatives 4, Assumptions, Invariant 5, the test's line-wrapped-citation and
five-decisions checks, and the `docs/provider-abstraction-design.md` §7.5 pointer).
```
(Write the `docs/provider-abstraction-design.md` path in full. A bare filename is not on
`PARTIAL_CITATION_ALLOWLIST`.)

- [ ] **Step 4: Edit §4.11 (heading, intro, five markers)**

Replace the block that runs from `### 4.11 v1 selected path and provisional operator defaults`
through decision 5's last line `   always a human action (§4.3).` with:
```
### 4.11 v1 selected path and accepted operator defaults

The issue requires this contract to select the minimum supported cloud + one local path and to
define model/tier resolution. The five decisions below are accepted operator defaults, confirmed
as written by the owner in #580. Downstream tickets build against them as written; a changed
decision is a revision of this contract, not a downstream re-decision.

1. **v1 local protocol** — *Accepted operator default (2026-11-22; confirmed by the owner
   in #580).* The cloud path is unchanged (`anthropic`, plus the existing native
   `bedrock`/`vertex` paths). The one supported local path is a local server that speaks the
   Anthropic Messages API, reached through `ANTHROPIC_BASE_URL`, with no translation layer. An
   OpenAI-shape or other non-Messages backend is out of scope for v1; #558 builds no translator.
2. **Tier resolution on a local endpoint** — *Accepted operator default (2026-11-22; confirmed
   by the owner in #580).* All four tier aliases
   (`ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`) resolve to the one certified local model
   named in the registry entry's model identity. An unset alias, or one that resolves to anything
   other than the certified model, fails preflight. Cloud alias pins in `run-compose.yml` are
   untouched.
3. **v1 local-eligible work** — *Accepted operator default (2026-11-22; confirmed by the
   owner in #580).* None of the existing gated phases (refine, plan, implement,
   conformance, docs-conformance, code-review) may be local-routed, because each runs its
   checker in the same container (§4.2). Only new helper dispatches that have no checker are
   eligible. Running a real phase locally needs gates split into a separate dispatch; that is its
   own reviewed gate ticket and is out of scope for this contract and for #557–#561.
4. **Default payload class** — *Accepted operator default (2026-11-22; confirmed by the owner
   in #580).* An unmarked ticket is `cloud_ok` on cloud, as today. Anything
   dispatched locally is `local_only` (§4.3).
5. **Merge authority for `local_only` work** — *Accepted operator default (2026-11-22;
   confirmed by the owner in #580).* `local_only` work never auto-merges; the merge is
   always a human action (§4.3).
```
Only the heading, the intro's last two sentences and the five markers change. Decision bodies
stay word-for-word, which the issue's "Do not change the five decisions" constraint requires.
Decision 3's body is rewritten in Task 5, and decision 2 gets a note in Task 13.

- [ ] **Step 5: Run it and see it pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
grep -c "Provisional operator default" docs/hybrid-execution-contract.md
```
Expected: `13 passed`. The grep prints `0`.

- [ ] **Step 6: Commit**

```bash
git add docs/hybrid-execution-contract.md tests/test_hybrid_execution_contract_doc.py
git commit -m "docs(contract #580): R1 — mark contract and §4.11 decisions accepted"
```

---

## Task 2: R2 + R7(§4.2) — Scope checker independence to local-routed actors

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm the old text is there**

```bash
grep -n "3. Checker independence is proven by the dispatch record" docs/hybrid-execution-contract.md
grep -n "testable rule is: a gate verdict is accepted only if" docs/hybrid-execution-contract.md
```
Expected: one hit each.

- [ ] **Step 2: Replace Invariants-index item 3**

Replace:
```
3. Checker independence is proven by the dispatch record (`hosting_location: cloud` and an
   `endpoint_id` different from the actor's), never by an agent's own claim; a row with
   `correlation: "unknown"` is never that proof (§4.2, §4.4).
```
with:
```
3. This independence check applies only to a local-routed actor's work: for such work, a gate
   verdict is accepted only if the dispatch record of the run that produced it shows
   `hosting_location: cloud` and an `endpoint_id` different from the actor's dispatch, proven
   by the record, never by an agent's own claim; a row with `correlation: "unknown"` is never
   that proof. A cloud-routed actor's same-run checker (today's `conformance`/
   `docs-conformance`/`code-review` nodes, and `refine`/`plan`'s in-process subagent checkers)
   is unchanged by this contract (§4.2, §4.4).
```

- [ ] **Step 3: Replace the §4.2 "refusal is enforced by the harness" bullet**

This step also carries R7's §4.2 clause, which names the dispatch-record schema's owner. Replace:
```
- **The refusal is enforced by the harness, not by the model.** A local-routed orchestrator
  declining to spawn a checker is only `self_report`; it is not what the contract relies on. The
  testable rule is: a gate verdict is accepted only if the dispatch record of the run that
  produced it has `hosting_location: cloud` and an `endpoint_id` different from the actor's
  dispatch. A verdict produced inside a local-routed container is void — it can neither pass nor
  fail a gate — and the ticket escalates to `ready-for-human`. That check is a
  `deterministic_check`-provenance event evaluated outside the agent.
```
with:
```
- **The refusal is enforced by the harness, not by the model.** A local-routed orchestrator
  declining to spawn a checker is only `self_report`; it is not what the contract relies on. The
  testable rule is: **for work produced by a local-routed actor**, a gate verdict is accepted
  only if the dispatch record of the run that produced it has `hosting_location: cloud` and an
  `endpoint_id` different from the actor's dispatch (the dispatch record's schema —
  `hosting_location`, `endpoint_id`, `correlation` — is #558's Result-contract row, §4.7). This
  bullet is the single, normative statement of the proof; Invariant 3 restates it. A verdict
  produced inside a local-routed container is void — it can neither pass nor fail a gate — and
  the ticket escalates to `ready-for-human`. That check is a `deterministic_check`-provenance
  event evaluated outside the agent. A cloud-routed actor's checker that happens to run in the
  same dispatch (today's `conformance`/`docs-conformance`/`code-review` nodes, and
  `refine`/`plan`'s in-process subagent checkers) needs no separate dispatch and is unaffected:
  this rule changes nothing about today's all-cloud path.
```
Leave the "Consequence for today's DAG" bullet below it unchanged, as the spec directs.

- [ ] **Step 4: Scope the two remaining "always a separate dispatch" statements, and re-point the §4.1 #559 row**

Without this step, Invariant 2 and §4.2's "Endpoint-role binding" bullet would still say every
checker is a separate dispatch, next to Step 3's "needs no separate dispatch".

Replace Invariants-index item 2:
```
2. An independent checker is always a separate dispatch, cloud-routed by default. A nested
   Agent-tool subagent of a local-routed parent is never a checker, and a verdict produced
   inside a local-routed container is void (§4.2).
```
with:
```
2. For a local-routed actor's work, an independent checker is always a separate dispatch,
   cloud-routed by default. A nested Agent-tool subagent of a local-routed parent is never a
   checker, and a verdict produced inside a local-routed container is void (§4.2).
```

In §4.2's "Endpoint-role binding happens at dispatch" bullet, replace its last four lines:
```
  `actor`/`worker`/`orchestrator` roles for that dispatch. It may never also host an independent
  `checker` for the same unit of work — that checker must be a **separate dispatch**, and by
  default remains cloud-routed (per the issue's "independent verification stays correctly
  cloud-routed" definition of done).
```
with:
```
  `actor`/`worker`/`orchestrator` roles for that dispatch. A local-routed container may never
  also host an independent `checker` for the same unit of work — that checker must be a
  **separate dispatch**, and by default remains cloud-routed (per the issue's "independent
  verification stays correctly cloud-routed" definition of done).
```

In the §4.1 table's #559 row (one physical line), replace this exact substring:
```
Consumes Q1's dispatch-record independence proof
```
with:
```
Consumes §4.2's dispatch-record independence proof
```
Keep the rest of that row byte-identical. Step 5 below says Q1's proof is superseded by §4.2, so
the row must not point at Q1.

- [ ] **Step 5: Append the governance sentence to "About this document"**

Replace the line:
```
here; this contract's own scope limits are Requirement 7 and §4.10.
```
with:
```
here; this contract's own scope limits are Requirement 7 and §4.10. Where a Brainstorming Q&A
answer differs from §4.1–§4.12 as revised by #580, the section governs — notably Q1's
"different secret reference" proof, Q2's unscoped checker-endpoint check and "unknown on either
side" wording, and Q3(a)'s `qualified_roles` and result states.
```

- [ ] **Step 6: Verify**

```bash
grep -n "for work produced by a local-routed actor" docs/hybrid-execution-contract.md
grep -n "This independence check applies only to a local-routed actor's work" docs/hybrid-execution-contract.md
grep -n "the section governs — notably Q1" docs/hybrid-execution-contract.md
grep -n "^2\. For a local-routed actor's work, an independent checker" docs/hybrid-execution-contract.md
grep -n "A local-routed container may never$" docs/hybrid-execution-contract.md
grep -n "Consumes §4.2's dispatch-record independence proof" docs/hybrid-execution-contract.md
grep -c "Consumes Q1's dispatch-record independence proof" docs/hybrid-execution-contract.md
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
```
Expected: one hit for each of the six `grep -n` commands, `0` from the `grep -c`, then
`13 passed`.

- [ ] **Step 7: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R2 — scope checker independence to local-routed actors"
```

---

## Task 3: R3 — Correct the §4.10 session-window citation

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm the old text is there**

```bash
grep -n "detects exhaustion from 429/rate-limit text" docs/hybrid-execution-contract.md
```
Expected: one hit.

- [ ] **Step 2: Replace the §4.10 429 bullet**

Replace:
```
- A local endpoint's rate-limit or HTTP 429 must never write the shared session-window pause
  sentinel: `session_window.py` detects exhaustion from 429/rate-limit text in a run's captured
  stdout, so a local fault could otherwise pause cloud work. Such an event is a local failure
  under §4.6. The change that guarantees this touches session-window logic and therefore needs
  its own human-reviewed ticket; it may not land as a side effect of #557–#561.
```
with:
```
- A local endpoint's rate-limit or HTTP 429 must never write the shared session-window pause
  sentinel: `session_window.py` detects session-window exhaustion from usage/session/credit-
  exhaustion phrasing or a rejected structured `claude.rate_limit_event` marker
  (`is_session_window_failure`) — not from a bare HTTP 429 (a bare 429 only feeds the breaker's
  `environmental:rate_limit` signature via the separate, broader `RATE_LIMIT_RE`, never the
  stricter session-pause detector). A local endpoint that emits usage/session/credit-exhaustion
  phrasing, or a fabricated `claude.rate_limit_event` marker, in captured stdout could still trip
  the shared pause by coincidence. Such an event is a local failure under §4.6. The change that
  makes the session-window detector local-endpoint-aware touches session-window logic and
  therefore needs its own human-reviewed ticket (#586), a rollout precondition in §4.12, and may
  not land as a side effect of #557–#561.
```
The pin "must never write the shared session-window pause" stays verbatim.

- [ ] **Step 3: Verify**

```bash
grep -c "detects exhaustion from 429/rate-limit text" docs/hybrid-execution-contract.md   # expect 0
grep -n "is_session_window_failure" docs/hybrid-execution-contract.md                    # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v       # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R3 — correct session-window detector citation, name #586"
```

---

## Task 4: R4 — Credential invariant for local-routed containers

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm it is absent**

```bash
grep -c "Credential scope for a local-routed container" docs/hybrid-execution-contract.md   # expect 0
```

- [ ] **Step 2: Add the §4.3 bullet**

Find the existing `- **Credential stripping:** …` bullet in §4.3. It ends with the line
`  this contract specifies must be added to that redaction set by whichever ticket implements it.`
Insert this bullet directly after that line and before
`- **Unqualified/unverifiable routes must abstain or escalate, never fail open.**`:
```
- **Credential scope for a local-routed container.** A local-routed container is issued no
  cloud model credential at all (not merely stripped before forwarding) and sends none to the
  local endpoint. It is issued no tracker or code-host write credential (e.g. no `GH_TOKEN`
  with push/comment/label scope) unless its own reviewed implementation spec explicitly grants
  one — #557 and #558 already require this for the endpoint/runtime-adapter surfaces they own;
  this contract states it as a cross-cutting invariant so #559–#564 inherit it too.
```

- [ ] **Step 3: Append Invariants-index item 16**

Directly after item 15's last line
(`    in-flight local run fails closed and is never re-run on cloud automatically (§4.12).`), and
before the blank line and `---`, insert:
```
16. A local-routed run container holds no cloud model credential and sends none to the local
    endpoint; it holds no tracker or code-host write credential unless its own reviewed spec
    explicitly grants one (§4.3).
```

- [ ] **Step 4: Verify**

```bash
grep -n "Credential scope for a local-routed container" docs/hybrid-execution-contract.md   # expect 1 hit
grep -n "^16\. A local-routed run container" docs/hybrid-execution-contract.md               # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v           # expect 13 passed
```
`test_invariants_index_cites_existing_sections` now sees 16 items, and item 16 cites §4.3.

- [ ] **Step 5: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R4 — add local-routed credential-scope invariant"
```

---

## Task 5: R5 — Define "helper dispatch" (§4.11 decision 3)

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm the old body is there**

```bash
grep -n "Only new helper dispatches that have no checker are" docs/hybrid-execution-contract.md   # expect 1 hit
```

- [ ] **Step 2: Replace decision 3 (marker kept from Task 1)**

Replace:
```
3. **v1 local-eligible work** — *Accepted operator default (2026-11-22; confirmed by the
   owner in #580).* None of the existing gated phases (refine, plan, implement,
   conformance, docs-conformance, code-review) may be local-routed, because each runs its
   checker in the same container (§4.2). Only new helper dispatches that have no checker are
   eligible. Running a real phase locally needs gates split into a separate dispatch; that is its
   own reviewed gate ticket and is out of scope for this contract and for #557–#561.
```
with:
```
3. **v1 local-eligible work** — *Accepted operator default (2026-11-22; confirmed by the
   owner in #580).* None of the existing gated phases (refine, plan, implement, conformance,
   docs-conformance, code-review) may be local-routed, because each runs its checker in the same
   container (§4.2). Only new **helper dispatches** are eligible — defined here concretely: a
   helper dispatch is a separately scheduled run container with its own endpoint config, doing
   non-gating work with no in-container checker, whose output is only an artifact until a
   cloud-routed check in a *different* dispatch accepts it (proven by the dispatch record,
   §4.2). In v1 a helper dispatch is limited to work whose declared payload class is `cloud_ok`
   or absent (§4.3). No ticket among #557–#564 builds the first concrete helper-dispatch
   instance — their scopes are registry/preflight, runtime adapter, route isolation, deploy
   profile, fixtures, certification, offline/shadow economics comparison, and the opt-in
   canary. #587 builds it, and depends on #585 (the no-auto-merge end-gate) and #586
   (local-aware session-window detection). Running a real phase locally needs gates split into
   a separate dispatch; that is its own reviewed gate ticket and is out of scope for this
   contract and for #557–#561.
```
- [ ] **Step 3: Verify**

```bash
grep -n "a helper dispatch is a separately scheduled run container" docs/hybrid-execution-contract.md   # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v                         # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R5 — define helper dispatch, name #587 as first instance"
```

---

## Task 6: R6 — Separate `local_only` (confidentiality) from local-pinned (no retry / no auto-merge)

**Files:** `tests/test_hybrid_execution_contract_doc.py`, `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Swap the fail-closed pin in the test**

In `FAIL_CLOSED_INVARIANTS`, replace:
```python
    "Unknown or missing on either side → block + escalate.",
```
with:
```python
    "blocks and escalates — it is never silently rerouted",
```

- [ ] **Step 2: Run it and see it fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py::test_fail_closed_invariants_pinned -v
```
Expected: FAIL with `fail-closed invariant weakened or removed: blocks and escalates — it is never
silently rerouted`.

- [ ] **Step 3: Replace the first six §4.3 bullets**

In §4.3, replace everything from `- **Payload class** (`cloud_ok` / `local_only`) is set per
issue/run…` through the end of the `- **Non-exporting outage behavior:** …` bullet (its last line
is `  grant above).`). The replaced block is exactly these lines of the current doc:
```
- **Payload class** (`cloud_ok` / `local_only`) is set per issue/run, stricter-only, grantable
  only by operator-controlled config — never by a label, comment, or model output (all of which
  are `self_report` under #288's taxonomy).
- **Destination class** is declared per endpoint-registry entry (#557): hosting location
  (`hosting_location`: `local`/`cloud`) and which payload classes it may receive
  (`accepts_payload_classes`, a non-empty subset of `cloud_ok`/`local_only`). A `cloud` entry
  may never list `local_only`.
- **Dispatch-time check:** a dispatch is allowed if and only if the payload class is a member of
  the target entry's `accepts_payload_classes`. `local_only` is therefore eligible for local
  entries only; `cloud_ok` is eligible for either. Evaluated once, deterministically, before
  dispatch. Unknown or missing on either side → block + escalate.
- **Default classes (§4.11 decision 4):** a ticket with no explicit class is `cloud_ok` when
  dispatched to cloud, exactly as today. Any dispatch to a local entry is treated as `local_only`
  from dispatch onward, whatever the ticket's class — it never retries on, or falls back to,
  cloud.
- **No auto-merge for `local_only` (§4.11 decision 5):** work whose payload class is
  `local_only` never auto-merges (no `direct-to-pr` end-gate auto-merge, no grace-timer advance
  to merge); the merge is always a human action. This check's
  decision (payload class, endpoint id, secret reference, allow/deny, reason) is recorded in the
  existing run-record ledger extension (§4.7), never a new store.
- **Non-exporting outage behavior:** a `local_only` dispatch that fails (outage, OOM, protocol
  incompatibility, timeout, stale/missing capability evidence) never retries against a cloud
  endpoint. It records the failure under one of #238's new failure classes and abstains/
  escalates. Two local failures, a model's stated confidence, or text in the task itself cannot
  grant export permission — only operator-controlled config can (same rule as the payload-class
  grant above).
```
Replace it with:
```
- **Declared payload class** (`payload_class`: `cloud_ok` / `local_only`, optional) is set per
  issue/run only by operator-controlled config (gitignored instance config or a human-reviewed
  adapter key) — never by a label, comment, or model output (all of which are `self_report`
  under #288's taxonomy). It is stricter-only: no label, comment or model output can loosen it.
- **Destination class** is declared per endpoint-registry entry (#557): hosting location
  (`hosting_location`: `local`/`cloud`) and which payload classes it may receive
  (`accepts_payload_classes`, a non-empty subset of `cloud_ok`/`local_only`). A `cloud` entry
  may never list `local_only`; a `local` entry must list `local_only`. The payload-class field
  and the dispatch-time predicate below are #557's to implement (not #558, whose scope excludes
  eligibility decisions).
- **Effective class**, computed once, deterministically, at dispatch time: a
  `local`-destination dispatch is `local_only` unconditionally, whatever the declared class; a
  `cloud`-destination dispatch is the declared class if one is set, else `cloud_ok` — exactly
  as today's unmarked ticket (§4.11 decision 4). An absent declared class on a cloud dispatch
  never blocks.
- **`local-pinned`** is a derived, never-operator-set property of a dispatch record
  (`hosting_location: local`), distinct from `payload_class`. It carries two rules that hold
  for *every* local dispatch regardless of declared class: no cloud retry or fallback, ever
  (Invariant 6), and no auto-merge, ever (§4.11 decision 5). `payload_class` carries only
  confidentiality/export eligibility — this separates "never retries on cloud" (a
  `local-pinned` property) from "this payload is confidential" (a `payload_class` property),
  the two meanings `local_only` previously conflated.
- **Dispatch-time check:** a dispatch is allowed if and only if the *effective* class is a
  member of the target entry's `accepts_payload_classes`. Blocks + escalates on: a declared
  value outside `{cloud_ok, local_only}`; a registry entry with invalid/missing
  `hosting_location` or `accepts_payload_classes`; or an effective class absent from the
  target's `accepts_payload_classes` (e.g. a declared-`local_only` ticket aimed at a cloud
  entry). Any of these three cases always blocks and escalates — it is never silently
  rerouted to a default; only an absent *declared* class on a cloud dispatch has a default
  (`cloud_ok`), and that is not a missing/unknown case. This check's decision (payload class,
  endpoint id, secret reference, allow/deny, reason) is recorded in the existing run-record
  ledger extension (§4.7), never a new store.
- **May a cloud checker read the output of a `cloud_ok` ticket that ran locally?** Yes — a
  `local`-routed dispatch whose **declared** class is `cloud_ok` or absent is `local-pinned` (no
  retry, no auto-merge) but not confidential; a separate cloud-routed checker dispatch may read
  its output, since the output inherits the confidentiality of inputs that were already
  cloud-eligible. A declared-`local_only` dispatch's output may be checked only by a human or a
  separate local dispatch — never a cloud checker. **Consequence:** v1 helper dispatches (§4.11
  decision 3) are limited to work whose declared class is `cloud_ok` or absent, since that is
  the only case with a valid independent-checker path.
- **No auto-merge (§4.11 decision 5, restated — strictly stronger, weakens nothing):** neither
  a `local-pinned` dispatch's output nor any `local_only`-classed payload ever auto-merges (no
  `direct-to-pr` end-gate auto-merge, no grace-timer advance to merge); the merge is always a
  human action. The scheduler end-gate check that enforces this is not owned by #557–#564; it
  needs its own human-reviewed gate ticket (#585).
- **Non-exporting outage behavior:** a `local-pinned` dispatch that fails (outage, OOM,
  protocol incompatibility, timeout, stale/missing capability evidence) never retries against
  a cloud endpoint — this is a property of being local-pinned, not of `payload_class`. It
  records the failure under one of #238's new failure classes and abstains/escalates. Two
  local failures, a model's stated confidence, or text in the task itself cannot grant export
  permission for a future dispatch — only operator-controlled config can, and such a later
  cloud run is always a *new* dispatch decision, never a runtime fallback of the failed
  local-pinned dispatch itself.
```

- [ ] **Step 4: Replace Invariants-index items 4–7**

Replace:
```
4. A dispatch is allowed only if the payload class is a member of the target entry's
   `accepts_payload_classes`; unknown or missing on either side blocks and escalates (§4.3).
5. Payload class is stricter-only: no label, comment or model output can loosen it; only
   operator-controlled config can grant `cloud_ok` (§4.3).
6. A local-routed dispatch never retries on, or falls back to, cloud. Every local failure,
   timeout or cancellation resolves to a #238 failure class and abstains or escalates (§4.3,
   §4.6).
7. `local_only` work never auto-merges; the merge is always a human action (§4.3; provisional operator default, §4.11 decision 5).
```
with:
```
4. A dispatch is allowed only if its *effective* payload class is a member of the target
   entry's `accepts_payload_classes`; an out-of-enum declared value, an invalid registry
   entry, or an effective class absent from `accepts_payload_classes` blocks and escalates. An
   absent declared class on a cloud dispatch defaults to `cloud_ok` and never blocks (§4.3).
5. Payload class is stricter-only: no label, comment or model output can loosen it; only
   operator-controlled config (gitignored instance config or a human-reviewed adapter key) can
   set or change the declared class (§4.3).
6. Every dispatch with `hosting_location: local` is `local-pinned` and never retries on, or
   falls back to, cloud — a later cloud run is always a new dispatch decision, never a
   fallback of the failed one. Every local failure, timeout or cancellation resolves to a #238
   failure class and abstains or escalates (§4.3, §4.6).
7. Neither a `local-pinned` dispatch's output nor any `local_only`-classed payload ever
   auto-merges; the merge is always a human action (§4.3; accepted operator default, §4.11
   decision 5).
```
This new item 5 also closes R13d, the Invariant-5/Q2 wording nit, so that nit needs no separate edit.

- [ ] **Step 5: Run it and see it pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
grep -c "Unknown or missing on either side" docs/hybrid-execution-contract.md
python3 - <<'EOF'
t = " ".join(open("docs/hybrid-execution-contract.md", encoding="utf-8").read().split())
for p in ("blocks and escalates — it is never silently rerouted", "never retries against a cloud",
          "`local_only` work never auto-merges"):
    print(p, "->", p in t)
EOF
```
Expected: `13 passed`, the grep prints `0`, and all three phrases print `True`.
(`` `local_only` work never auto-merges `` still appears verbatim in §4.11 decision 5's body,
which this ticket does not change.)

- [ ] **Step 6: Commit**

```bash
git add docs/hybrid-execution-contract.md tests/test_hybrid_execution_contract_doc.py
git commit -m "docs(contract #580): R6 — split declared/effective class and local-pinned"
```

---

## Task 7: R7 — Dispatch-record fields on the §4.7 Result row

**Files:** `docs/hybrid-execution-contract.md`

Task 2 already placed R7's §4.2 clause. Task 6 already states R7's other two owners in §4.3:
payload-class field and predicate → #557, and the no-auto-merge end-gate → #585.

- [ ] **Step 1: Confirm the old row is there**

```bash
grep -n "^| Result | #558 | Outcome, failure class (if any), destination actually used |$" docs/hybrid-execution-contract.md   # expect 1 hit
```

- [ ] **Step 2: Replace the row (one table line)**

Replace:
```
| Result | #558 | Outcome, failure class (if any), destination actually used |
```
with:
```
| Result | #558 | Outcome, failure class (if any), destination actually used, and the dispatch-record fields §4.2's independence check relies on (`hosting_location`, `endpoint_id`, `correlation`) |
```
The row must stay one physical line because it is a table row. `test_four_contracts_are_independently_versioned`
pins `| Result | #558 |`, and that prefix is kept.

- [ ] **Step 3: Replace the §4.7 storage sentence**

The paragraph under the table still says all four contracts are fields on the per-run record.
Task 9 makes §4.5 say the capability-evidence record is never in the per-run record, so this
sentence has to change with it. Replace:
```
evidence or an unknown payload/destination class. All four are specified here only; #557/#558/
#561 implement them as new additive top-level fields on `run_record.py`'s existing per-run
record, following the `harness_economics`/`memory_trace` extension idiom — no new datastore
(consistent with the #207 architecture-memory entry below).
```
with:
```
evidence or an unknown payload/destination class. All four are specified here only. #558
implements the Request and Result contracts as new additive top-level fields on
`run_record.py`'s existing per-run record, following the `harness_economics`/`memory_trace`
extension idiom — no new datastore (consistent with the #207 architecture-memory entry below).
The Configuration contract (#557) and the capability-evidence record (#561/#562) are
per-endpoint: they live on the operator-controlled instance-config surface (§4.5), and the
per-run record cites them by `endpoint_id` and `schema_version`.
```
`test_four_contracts_are_independently_versioned` pins `"major bumps are a hard reject"`, which
is in the sentence before this one and stays.

- [ ] **Step 4: Verify**

```bash
grep -n "dispatch-record fields §4.2's independence check relies on" docs/hybrid-execution-contract.md   # expect 1 hit
grep -n "implements the Request and Result contracts as new additive" docs/hybrid-execution-contract.md   # expect 1 hit
grep -c "#561 implement them as new additive top-level fields" docs/hybrid-execution-contract.md          # expect 0
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v                      # expect 13 passed
```

- [ ] **Step 5: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R7 — dispatch-record schema on #558's Result row; per-endpoint contracts off the run record"
```

---

## Task 8: R8 — §4.6 interim failure-class enumeration

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm the old text is there**

```bash
grep -n "The class names are #238's to choose, but its taxonomy must distinguish" docs/hybrid-execution-contract.md   # expect 1 hit
```

- [ ] **Step 2: Replace §4.6's closing paragraph**

Replace:
```
The class names are #238's to choose, but its taxonomy must distinguish at least these
local-endpoint outcomes: endpoint unreachable; timeout (connect, time-to-first-token, idle,
total); cancellation; protocol incompatibility; capability evidence missing, expired or
mismatched; destination-policy denial. None of them is retryable against a different endpoint.
```
with:
```
The class names are #238's to choose. Until #238 lands, the list below is this contract's
**interim enumeration**, which #558 implements directly (#558 depends only on #557, not on
#238, so it is not blocked waiting for #238's taxonomy). #238's taxonomy must distinguish at
least these local-endpoint outcomes: endpoint unreachable; timeout (connect,
time-to-first-token, idle, total); cancellation; protocol incompatibility; capability evidence
missing, expired or mismatched; destination-policy denial. None of them is retryable against a
different endpoint. When #238 ships its own class names, #558's interim enumeration is
superseded by them, not duplicated alongside them.
```
- [ ] **Step 3: Verify**

```bash
grep -c "interim enumeration" docs/hybrid-execution-contract.md                     # expect 3 (two §4.6 lines + the Revised (#580) line)
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v   # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R8 — declare §4.6 list the interim enumeration #558 implements"
```

---

## Task 9: R9 — Capability evidence: storage, write authority, vocabulary

**Files:** `tests/test_hybrid_execution_contract_doc.py`, `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Update the test first**

In `test_capability_evidence_schema_never_qualifies_a_checker`, replace:
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
Keep the function's first assertion (`'schema_version: "1.0"' in section`) as it is.

- [ ] **Step 2: Run it and see it fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py::test_capability_evidence_schema_never_qualifies_a_checker -v
```
Expected: FAIL on the `roles` assertion, because the current value is `[worker]`.

- [ ] **Step 3: Replace the §4.5 body**

Keep the `### 4.5 Capability evidence` heading. Replace everything between it and
`### 4.6 Timeouts and cancellation (policy)`, which is the current "Per Q3(a). A capability-evidence
record…" paragraph, the ```` ```text ```` schema block and the "Capability dimensions…" paragraph.
Write this instead, with a blank line before the next heading:
````
Per Q3(a), tightened by #580's post-merge review. A capability-evidence record is
**per-endpoint and long-lived, not a per-run record**: one record per `endpoint_id`, stored as
a sibling record next to (not a field of) its #557 registry entry, on the same
operator-controlled, human-only instance-config surface as the registry entries and
payload-class grants (exact path left to #557's own refinement) — never in the per-run record
or ledger, which may only cite it by `endpoint_id` + `schema_version`.

**Who may write it:** only an operator, acting on the output of #562's human-gated
certification run (`ready-for-human`, never scheduler-dispatched) executing #561's pinned
fixtures. No factory phase agent may create or edit a record, however privileged; no
scheduler-dispatched container may be able to write it (the mechanism is #557's and #560's to
specify). A record-shaped artifact appearing in a branch, PR diff, issue comment or run
artifact is `self_report` and is ignored. Preflight rejects a record missing `certified_by` or
whose `fixture_suite_version` does not match the currently pinned suite.

```text
capability_evidence:
  schema_version: "1.0"
  endpoint_id: <#557 registry key>
  model_identity: {served_name, digest_or_revision, quantisation}
  runtime_stack: {name, version, chat_template, context_settings}
  harness_version: <pinned Claude CLI/SDK version>
  fixture_suite_version: <pinned commit/digest>
  results: {<capability_dimension>: pass | fail | unsupported | not_tested, ...}
  qualified_roles: [actor]          # §4.4's enum; never "checker" or "monitor" — see §4.2
  certified_by: <operator identity>
  certified_at: <ISO8601>
  expires_at: <ISO8601>
  invalidated_by: [model_digest, quantisation, runtime_version, chat_template, context_settings,
                   harness_version, fixture_suite_version, registry_config]
```

`qualified_roles` uses §4.4's normative `role` enum: v1 evidence may only ever list `actor`
(§4.2 already treats `worker`/`orchestrator` as kinds of `actor`), never `checker` or
`monitor`. `actor` qualification under §4.11 decision 3 certifies a checker-free helper
dispatch only — it never advertises full-agent or checker capability (#561).

Per-dimension `results` values are `pass | fail | unsupported | not_tested` (`unsupported`
means the endpoint's protocol/runtime cannot do this at all, proven by a fixture). A separate,
derived evidence state — `tested | declared | stale | unknown` — is computed at preflight read
time from `expires_at`/`invalidated_by` fingerprint comparison and is never stored or
operator-set: `declared` (the registry entry declares a capability but no valid record exists
yet), `stale` (`expires_at` has passed, or any `invalidated_by` fingerprint differs from what
is observed), `unknown` (no record exists, or a fingerprint cannot be resolved), `tested`
(none of the above). A role is eligible only if the derived state is `tested` and every
dimension that role requires is `pass`; every other combination (per-dimension
`fail`/`unsupported`/`not_tested`, or derived `declared`/`stale`/`unknown`) fails closed —
missing, expired, failed, or mismatched evidence blocks and escalates exactly like a failed
certification, no grace period.

Capability dimensions (minimum set #561's fixtures must cover): protocol compatibility,
tool-call fidelity, streaming behavior, context-length handling, role-isolation (confirms the
endpoint cannot be mistaken for an independent checker), and timeout/cancellation behavior
(§4.6).
````
(The outer four-backtick fence is only this plan's quoting. In the doc, the schema sits in an
ordinary ```` ```text ```` … ```` ``` ```` fence, the same as today.)

- [ ] **Step 4: Run it and see it pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
grep -c "qualified_roles: \[worker\]" docs/hybrid-execution-contract.md
```
Expected: `13 passed`, and the grep prints `0`.

- [ ] **Step 5: Commit**

```bash
git add docs/hybrid-execution-contract.md tests/test_hybrid_execution_contract_doc.py
git commit -m "docs(contract #580): R9 — capability evidence per-endpoint, actor-only, derived states"
```

---

## Task 10: R10 — Name the owner of `cost_usd: null` for local runs

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm it is absent**

```bash
grep -c 'since #453 it then re-records' docs/hybrid-execution-contract.md   # expect 0
```

- [ ] **Step 2: Extend the §4.10 cost bullet**

Replace:
```
- Local token counts are recorded. Local cost is recorded as `cost_usd: null` — never zero and
  never Claude-priced — so economics reports cannot read local work as free (§4.8).
```
with:
```
- Local token counts are recorded. Local cost is recorded as `cost_usd: null` — never zero and
  never Claude-priced — so economics reports cannot read local work as free (§4.8).
  `run_record.py` still parses a missing node cost as `0`
  (`n.get("costUsd") or n.get("cost_usd") or 0`); since #453 it then re-records a command or
  prompt node that reported no usage, or that started but is absent from the cost report, as
  `cost_capture: "missing"` with `cost_usd: null`. That does not cover a local-routed node for
  which the harness reports a Claude-priced figure: #558 owns recording `null` for every node
  of a local-routed run, and that lands before any local run is recorded in `runs.jsonl` or a
  cost report.
```
#453 landed on `main` in PR #582 after the spec gate, so the contract describes it as done, not
as an owner of future work. Cite the expression, not a line number: #453 did not move it (it is
still `run_record.py:272` on `origin/main` at `829eaaa`), but a line number drifts.

- [ ] **Step 3: Verify**

```bash
grep -n 'since #453 it then re-records' docs/hybrid-execution-contract.md                 # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v         # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R10 — name #558 as the local cost_usd null owner (#453 landed)"
```

---

## Task 11: R11 — Align the §4.12 rollout rule with #562/#563/#564

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm the old text is there**

```bash
grep -n "after #562 has produced valid" docs/hybrid-execution-contract.md   # expect 1 hit
```

- [ ] **Step 2: Replace the "Rollout rules." paragraph**

Replace:
```
**Rollout rules.** Downstream tickets are opted in one at a time, in the order of their
`Depends on:` lines. Merging any of them activates nothing: local endpoint selection is off by
default, and is enabled only by operator-controlled instance config (a human-only surface —
`deploy/instances/**` is never edited by a factory agent) after #562 has produced valid
capability evidence for that endpoint. #562 and #564 are human-run and never scheduler-dispatched.
```
with:
```
**Rollout rules.** Downstream tickets are opted in one at a time, in the order of their
`Depends on:` lines. Merging any of them activates nothing. #562's certification run and #563's
offline/shadow runner are not dispatches in this contract's sense: they run outside the
scheduler against non-sensitive fixtures, so the no-evidence-no-dispatch rule does not apply to
them. #562's GO covers offline/shadow evaluation only (no production routing, no alias change);
#563 extends that comparison with economics data, still with no write authority over production
routing. A real workload runs locally only inside #564's canary, under the operator's prior
written approval of that one lane. That approval requires #563's comparison, #239's static
routing and #238's failure policy active for the lane, the first helper dispatch (#587), the
no-auto-merge end-gate (#585) and the local-cost fix (#558, §4.10; #453's missing-cost fix has
landed); the lane is enabled only via operator-controlled instance config (a human-only
surface — `deploy/instances/**` is never edited by a factory agent). #564's signed GO/NO-GO is
required before any wider use. A rollout precondition not owned by #557–#564: the
session-window detector must be made local-endpoint-aware (#586, §4.10) before any local
dispatch runs, so a local fault's captured stdout cannot trip the shared pause sentinel. #562
and #564 are human-run and never scheduler-dispatched.
```
A line in this paragraph begins with `#563`. That is fine; see the wrapping rule in Conventions.

- [ ] **Step 3: Verify**

```bash
grep -n "#562's GO covers offline/shadow evaluation only" docs/hybrid-execution-contract.md   # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v            # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R11 — align rollout rule with #562/#563/#564, #586 precondition"
```

---

## Task 12: R12 — Limit the keyword signal to local-eligibility decisions

**Files:** `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Confirm it is absent from §4.3**

```bash
grep -c "safety.sensitive_keywords\` reuse (stricter-only, local-eligibility only)" docs/hybrid-execution-contract.md   # expect 0
```

- [ ] **Step 2: Add the §4.3 bullet**

Directly after the "Credential scope for a local-routed container" bullet (from Task 4), and
before `- **Unqualified/unverifiable routes must abstain or escalate, never fail open.**`, insert:
```
- **`safety.sensitive_keywords` reuse (stricter-only, local-eligibility only):**
  `.factory/adapter.yaml`'s existing `safety.sensitive_keywords` regex may be reused as a
  *stricter-only* signal restricted to **local-eligibility decisions**: a hit never changes the
  declared class and is evaluated only when a ticket is considered for a local dispatch. That
  dispatch's output is then treated as declared-`local_only` (human or local check only, so not
  a v1 helper-dispatch candidate) and the ticket escalates. It never authorizes anything, never
  affects a cloud-routed dispatch (today's `github/github/anthropic` path is completely
  unchanged by this signal), and never loosens any class. It is advisory/evadable regex over
  ticket text — not an egress control — so it is never the sole mechanism gating a local
  dispatch; the dispatch-time predicate above is.
```
This gives Alternatives 3's existing "stricter-only signal (§4.3)" reference a real target, so
Alternatives 3 needs no edit.

- [ ] **Step 3: Verify**

```bash
grep -n "restricted to \*\*local-eligibility decisions\*\*" docs/hybrid-execution-contract.md   # expect 1 hit
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v              # expect 13 passed
```

- [ ] **Step 4: Commit**

```bash
git add docs/hybrid-execution-contract.md
git commit -m "docs(contract #580): R12 — scope sensitive_keywords signal to local eligibility"
```

---

## Task 13: R13 — Nits (a, b, c, e, f, g; d already done in Task 6)

**Files:** `tests/test_hybrid_execution_contract_doc.py`, `docs/hybrid-execution-contract.md`,
`docs/provider-abstraction-design.md`

- [ ] **Step 1 (R13e, test first): Collapse whitespace inside backtick spans before matching citations**

Replace the whole `test_cited_repo_files_resolve` function with:
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

- [ ] **Step 2: Run it**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
```
Expected: `13 passed`. Both line-wrapped citations still resolve today:
`scripts/factory_core/providers/model.py`, and
`docs/superpowers/specs/2026-11-19-effective-config-widening-design.md`, which is still a live spec.
That second one is a latent hazard: push-and-pr will archive it when #180's PR is pushed, and
step 6 below removes it.
Check that the new code really sees it:
```bash
python3 - <<'EOF'
import re
t = open("docs/hybrid-execution-contract.md", encoding="utf-8").read()
c = re.sub(r"`([^`]*)`", lambda m: "`" + re.sub(r"\s+", "", m.group(1)) + "`", t)
R = re.compile(r"`([\w.][\w./-]*\.(?:py|sh|md|ya?ml))`")
print(sorted(set(R.findall(c)) - set(R.findall(t))))
EOF
```
Expected (before step 6): `['docs/superpowers/specs/2026-11-19-effective-config-widening-design.md']`.

- [ ] **Step 3 (R13a): Alternatives item 4**

Replace:
```
   governs which tools/commands a phase may execute (#196's unbuilt enforcement scope), an
   orthogonal concern to where model-request data is sent; conflating the two would make a
```
with:
```
   governs which tools/commands a phase may execute (enforced today by the per-level permission
   profiles in `scripts/factory_core/side_effect.py`, #196), an orthogonal concern to where
   model-request data is sent; conflating the two would make a
```
(Write the path in full. A bare `side_effect.py` is not on `PARTIAL_CITATION_ALLOWLIST`.)

- [ ] **Step 4 (R13b): Overview sentence**

Replace:
```
`anthropic`), documented in `docs/provider-abstraction-design.md` §7. Every dispatched run
container gets exactly one `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` pair from
`.archon/.env`, read once by Claude Code at process start; `ANTHROPIC_DEFAULT_{OPUS,SONNET,
```
with:
```
`anthropic`), documented in `docs/provider-abstraction-design.md` §7. Every dispatched run
container's auth comes from exactly one `.archon/.env` by default — `CLAUDE_CODE_OAUTH_TOKEN`/
`ANTHROPIC_API_KEY`, not `ANTHROPIC_AUTH_TOKEN` (which nothing on `main` sets), and with
`ANTHROPIC_BASE_URL` unset (today's direct-to-Anthropic default; `ANTHROPIC_BASE_URL` is set
only when opting into the `model_proxy.py` ledger via `FACTORY_MODEL_PROXY_ENABLED`) — read
once by Claude Code at process start; `ANTHROPIC_DEFAULT_{OPUS,SONNET,
```
(The next line, `HAIKU,FABLE}_MODEL` (`run-compose.yml`) likewise…, stays unchanged.)

- [ ] **Step 5 (R13c): Note on §4.11 decision 2 (decision text unchanged)**

Replace decision 2's final lines:
```
   other than the certified model, fails preflight. Cloud alias pins in `run-compose.yml` are
   untouched.
3. **v1 local-eligible work**
```
with:
```
   other than the certified model, fails preflight. Cloud alias pins in `run-compose.yml` are
   untouched.

   Note (#580): the literal cloud model IDs pinned in `config/config.yaml` —
   `conformance.shadow_model` (`claude-fable-5-1`) and `epic_autopilot.model`,
   `main_red_autofix.model` and `deps_audit.model` (`claude-opus-5-5`) — are untouched and do
   not resolve through the tier-alias mapping above, so the alias preflight does not see them. A
   request from a local-routed container that names any model other than the certified one,
   including a literal cloud model ID, is refused, never remapped (#559).

3. **v1 local-eligible work**
```
The note is indented 3 spaces so it stays inside item 2. `test_section_4_11_has_five_accepted_decisions`
must still count exactly 5 items. All four config keys were checked against `origin/main`
`config/config.yaml`: lines 40, 84, 124 and 139.

- [ ] **Step 6 (R13g): First Assumptions bullet**

Replace:
```
  the merge-base with `main` (no stale-branch drift of the kind `docs/superpowers/specs/
  2026-11-19-effective-config-widening-design.md` had to correct for).
```
with:
```
  the merge-base with `main` (no stale-branch drift of the kind the 2026-11-19
  effective-config-widening refinement, #180, had to correct for).
```

- [ ] **Step 7 (R13f): `docs/provider-abstraction-design.md` §7.5 pointer**

Line 271 is one physical line. Inside it, replace this exact substring:
```
As a provisional operator default pending owner confirmation (§4.11 of the contract), v1 supports one local server that speaks the Anthropic Messages API through `ANTHROPIC_BASE_URL` (no translation layer), isolates endpoints per dispatched run container, keeps every independent checker on a separate cloud-routed dispatch, and never falls back from a local failure to cloud.
```
with:
```
As an accepted operator default (§4.11 of the contract, confirmed in #580), v1 supports one local server that speaks the Anthropic Messages API through `ANTHROPIC_BASE_URL` (no translation layer), isolates endpoints per dispatched run container, and — whenever a local-routed actor is involved — requires that actor's independent checker to be a separate cloud-routed dispatch (today's all-cloud checkers, e.g. `conformance`/`code-review`, are unaffected and keep running in-process), and never falls back from a local failure to cloud.
```
Keep the rest of line 271 byte-identical: the leading
`> **Hybrid cloud + local (#556).** … [`docs/hybrid-execution-contract.md`](hybrid-execution-contract.md), not by the gateway design above. `
and the trailing ` The contract is a decision report: nothing in it is implemented yet (#557–#564).`
`test_provider_design_model_axis_points_at_the_contract` pins only the link and `(#556)`.

- [ ] **Step 8: Verify**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
grep -c "unbuilt enforcement scope" docs/hybrid-execution-contract.md                 # expect 0
grep -c "ANTHROPIC_AUTH_TOKEN\` pair" docs/hybrid-execution-contract.md                # expect 0
grep -c "docs/superpowers/specs/" docs/hybrid-execution-contract.md                    # expect 0
grep -c "keeps every independent checker on a separate" docs/provider-abstraction-design.md   # expect 0
grep -n "Note (#580): the literal cloud model IDs" docs/hybrid-execution-contract.md   # expect 1 hit
```
Expected: `13 passed`, then the counts noted beside each grep.

- [ ] **Step 9: Commit**

```bash
git add docs/hybrid-execution-contract.md docs/provider-abstraction-design.md tests/test_hybrid_execution_contract_doc.py
git commit -m "docs(contract #580): R13 — nits (#196 enforcement, auth default, model IDs, citations, §7.5 pointer)"
```

---

## Task 14: Final verification and scope check

**Files:** none modified, unless a check fails.

- [ ] **Step 1: Whole-file sanity checks**

```bash
grep -n "Provisional operator default\|provisional operator default" docs/hybrid-execution-contract.md docs/provider-abstraction-design.md
```
Expected: hits **only** in two places: the header Status sentence ("carried at merge as provisional
operator defaults pending owner confirmation", which is historical) and the byte-identical original
`**Revised:**` line ("path and provisional operator defaults (§4.11)"). There should be no hit in
§4.11, the Invariants index or `provider-abstraction-design.md`.

```bash
grep -cE "^#{2,3} " docs/hybrid-execution-contract.md   # expect 21, the same as origin/main
awk 'length > 100 && !/^\|/ {print FILENAME": "FNR": "length}' docs/hybrid-execution-contract.md
```
Expected: the heading count equals the same command run against `git show
origin/main:docs/hybrid-execution-contract.md`, so no heading was added or lost. The `awk` output
should list nothing newly added. Long lines that already existed, such as the Credential-stripping
bullet's line, are fine.

- [ ] **Step 2: Doc test, then the full suite**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -v
PYTHONPATH=scripts python -m pytest tests/ -q
```
Expected: `13 passed`, then a green full suite. If an unrelated test fails, check it on
`origin/main` first. A failure that already exists there is not this ticket's to fix; note it in
the PR body.

- [ ] **Step 3: Scope check against `origin/main` (three-dot for scope; two-dot per memory PATTERN #250)**

```bash
git fetch -q origin main
git diff --name-only origin/main...HEAD   # scope evidence: what this branch changed since it forked
git diff --name-only origin/main HEAD     # two-dot: also lists what main changed after the fork
```
Expected from the three-dot diff: only these files.
- `docs/hybrid-execution-contract.md`
- `docs/provider-abstraction-design.md`
- `tests/test_hybrid_execution_contract_doc.py`
- this ticket's refine/plan artifacts: `docs/superpowers/specs/2026-11-22-hybrid-execution-contract-revision-design.md`,
  `docs/superpowers/plans/2026-11-23-hybrid-execution-contract-revision.md` and
  `.archon/memory/architecture.md`
- The two-dot diff may list more: any file changed on `main` after the fork. #581 and #582 both
  merged after the refine branch forked, so `entrypoint.sh`, `scripts/factory_core/run_record.py`,
  `scripts/factory_core/cost_report.py` and their tests and docs can appear there. They show up
  only because `main` moved, not because this ticket changed them, and none of them may appear in
  the three-dot diff.

There must be nothing under `scripts/`, `config/`, `workflows/`, `scheduler.sh`, `deploy/**` or any
gate, breaker or budget file.

- [ ] **Step 4: Confirm the five decisions are unchanged**

```bash
python3 - <<'EOF'
import re, subprocess
def items(src):
    sec = src.split("### 4.11 ", 1)[1].split("\n### 4.12 ", 1)[0]
    out = []
    for it in re.split(r"^\d+\. ", sec, flags=re.MULTILINE)[1:]:
        body = " ".join(it.split())
        body = re.sub(r"\*(Provisional|Accepted) operator default[^*]*\*", "", body)
        out.append(body.split(" Note (#580):")[0])
    return out
old = items(subprocess.run(["git","show","origin/main:docs/hybrid-execution-contract.md"],capture_output=True,text=True).stdout)
new = items(open("docs/hybrid-execution-contract.md", encoding="utf-8").read())
for n,(o,w) in enumerate(zip(old,new),1):
    print(n, "unchanged" if o == w else "CHANGED")
EOF
```
Expected: decisions 1, 2, 4 and 5 print `unchanged`. Decision 3 prints `CHANGED`, which is
intended: R5 extends its body with the helper-dispatch definition the issue asks for, and the
operative rule ("None of the existing gated phases … may be local-routed … Only new helper
dispatches … are eligible") stays. Any other `CHANGED` is a defect. Fix it before pushing.

- [ ] **Step 5: Commit any fix-ups**

If steps 1–4 needed a correction:
```bash
git add docs/hybrid-execution-contract.md docs/provider-abstraction-design.md tests/test_hybrid_execution_contract_doc.py
git commit -m "docs(contract #580): final verification fix-ups"
```

---

## Notes and reservations (for reviewers, no action required)

- **Invariant 2 and §4.2's "Endpoint-role binding" bullet are scoped too (plan gate, P3).** The
  first draft of this plan left them alone; read alone, each said a checker "is always a separate
  dispatch", next to the new "needs no separate dispatch" sentence in the same section. Task 2
  Step 4 now scopes both to a local-routed actor, and the spec's R2 lists the same edits.
- **Plan-gate amendments (2026-11-23).** P1: #453 landed in PR #582, so Tasks 10 and 11 name #558
  as the one open cost owner. P2: Task 7 rewrites the §4.7 storage sentence that contradicted
  §4.5. P3: as above. P4: the §4.1 #559 row cites §4.2, Task 14's scope check uses the three-dot
  diff, and the contract's "Revised (#580)" line is dated 2026-11-23. The amended end state was
  run on a scratch copy of `origin/main` at `829eaaa`: every anchor matched once and the doc test
  gave `13 passed`.
- The issue-body edits for #557, #558, #559, #563 and #564 come from the spec's R5 "Operator
  follow-ups" subsection. They are operator actions after merge, not implement-phase work.
- Push-and-pr archives this plan to `docs/archive/`. Nothing in the contract or its test cites the
  plan's or the spec's path, so the archive move cannot turn CI red (the PR #215 hazard).

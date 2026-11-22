# Hybrid Model Execution Contract — Publish the Decision Report Implementation Plan

**Issue:** #556

**Goal:** Publish the reviewed #556 hybrid cloud + local execution contract as a durable,
drift-guarded decision report at `docs/hybrid-execution-contract.md`, linked from the README and
from the model-endpoint axis of `docs/provider-abstraction-design.md`, so it survives the
automatic archival of the refinement spec and gives downstream tickets (#557–#564, #239's
re-refinement) one stable path to build against.

**Architecture:** Documentation plus one pytest drift guard. No runtime, gate, breaker, budget,
deployment, config or model-default change (spec Requirement 7). The new doc is the spec body
reproduced **verbatim** (built mechanically by a short Python script, then checked with `diff`),
behind a new header carrying `hybrid_contract: "1.0"` and an explicit **Status: Proposed** line
that names the human merge of the #556 PR as the acceptance record (Requirement 7), an
"About this document" authority note, and an "Invariants (index)" section that lists the
contract's fail-closed rules with § pointers (Requirement 1's "explicit invariants"). The drift
guard follows the `tests/test_factory_target_boundary_doc.py` / `tests/test_claude_skills_policy_doc.py`
precedent: required sections in order, exact-substring pins on the fail-closed invariants, and
resolution of every cited repo file.

**Why a separate durable doc rather than pinning the spec:** `push-and-pr`
(`workflows/archon-dark-factory.yaml`, "Archive spec/plan for this issue before pushing") runs
`push_gate_check.sh` and `git mv`s this issue's spec **and** plan into `docs/archive/` on every
push. A test or README link pinned to the `docs/superpowers/specs/` path would break CI the
moment the PR is pushed (memory `codebase-patterns.md`: archiving a pinned spec broke CI on
PR #215). The archived spec stays as design history; the durable doc is authoritative.

**Tech Stack:** Markdown, Python 3 + pytest (CI: `PYTHONPATH=scripts python -m pytest tests/ -v`).

**Working directory:** every command below uses paths relative to the clone root. Shell cwd
does not persist between tool calls, so start each Bash call with `cd "$(git rev-parse --show-toplevel)"`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `tests/test_hybrid_execution_contract_doc.py` | Create | Drift guard: header/status/version lines, required sections in order, §4.1 slice owners, §4.5 evidence schema, §4.7 contract rows, §4.9 items 12–15, fail-closed invariant phrases, invariants-index § citations, cited-file resolution, no pre-archive spec path, README + provider-doc links |
| `docs/hybrid-execution-contract.md` | Create | Durable decision report: new header (Issue, Contract version, Status) + spec's Revised line + "About this document" + "Invariants (index)" + spec body from "## Overview / Problem Statement" to EOF, verbatim |
| `docs/provider-abstraction-design.md` | Modify (§7.5, one blockquote) | Point the model-endpoint axis at the contract for any local endpoint |
| `README.md` | Modify ("Further reading", one line) | Make the contract discoverable |

Not touched (scope discipline): `scripts/**`, `config/**`, `.factory/adapter.yaml`, `commands/**`,
`workflows/**`, `run-compose.yml`, `deploy/**`, `.github/**`, and the spec file itself
(`push-and-pr` archives it automatically — do **not** `git mv` it by hand). No comments or label
changes on #239 or #557–#564; they consume the contract through their own `Depends on: #556`
refinement.

---

## Task 1: Publish the durable decision report with a drift guard

**Files:**
- Create: `tests/test_hybrid_execution_contract_doc.py`
- Create: `docs/hybrid-execution-contract.md`

- [ ] **Step 1: Write the failing drift-guard test**

Create `tests/test_hybrid_execution_contract_doc.py` with exactly this content:

```python
"""Drift guard for docs/hybrid-execution-contract.md (#556), the durable hybrid cloud + local
model execution contract. Pins the decision report's required sections, its version and
acceptance-status lines, the responsibility-map owners, the four versioned contracts, the #239
spec delta and the fail-closed invariants to exact substrings, and checks that every cited repo
file still resolves. Presence checks only: whether the prose is true is the reviewers' job.

The design spec this doc was promoted from is archived to docs/archive/ by push-and-pr, so
nothing here (or in the doc) may reference the spec's docs/superpowers/specs/ path."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "hybrid-execution-contract.md"

REQUIRED_SECTIONS = [
    "## About this document",
    "## Invariants (index)",
    "## Overview / Problem Statement",
    "## Requirements",
    "## Architecture / Approach",
    "### 4.1 Responsibility map — nine initiative slices to acceptance predicates",
    "### 4.2 Actor / worker / checker coexistence and nested Agent-alias behavior",
    "### 4.3 Confidentiality, destination policy, and non-exporting outage behavior",
    "### 4.4 Request correlation",
    "### 4.5 Capability evidence",
    "### 4.6 Timeouts and cancellation (policy)",
    "### 4.7 Versioned contracts and ledger extension",
    "### 4.8 Compatibility matrix — helper boundary vs. full Claude-harness compatibility",
    "### 4.9 Exact spec delta for #239",
    "### 4.10 Budgets, breaker and session-window invariants",
    "### 4.11 v1 selected path and provisional operator defaults",
    "### 4.12 Risks and rollout/rollback rules",
    "## Alternatives Considered",
]

SLICE_OWNERS = ["#557", "#558", "#559", "#560", "#561", "#562", "#239", "#238", "#563", "#564"]

FAIL_CLOSED_INVARIANTS = [
    "Unknown or missing on either side → block + escalate.",
    "A verdict produced inside a local-routed container is void",
    "never retries against a cloud",
    "`local_only` work never auto-merges",
    "no grace period",
    "`cost_usd: null`",
    "must never write the shared session-window pause",
    "byte-identical to today's `github/github/anthropic` default",
]

CITATION_RE = re.compile(r"`([\w.][\w./-]*\.(?:py|sh|md|ya?ml))`")


def _doc_text() -> str:
    assert DOC_PATH.is_file(), f"{DOC_PATH} does not exist — #556's decision report must live here"
    return DOC_PATH.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Body of `heading` up to the next ##/### heading."""
    rest = text[text.index(heading + "\n") + len(heading) + 1:]
    nxt = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def test_header_carries_issue_version_and_acceptance_status():
    head = _doc_text().split("\n## ", 1)[0]
    assert "**Issue:** #556" in head
    assert '**Contract version:** `hybrid_contract: "1.0"`' in head
    assert "**Status:** Proposed" in head
    assert "merge of the #556 PR by a human reviewer" in head


def test_required_sections_present_in_order():
    text = _doc_text()
    positions = []
    for heading in REQUIRED_SECTIONS:
        assert re.search(rf"^{re.escape(heading)}$", text, re.MULTILINE), f"missing section: {heading}"
        positions.append(text.index(heading + "\n"))
    assert positions == sorted(positions), "required sections are out of order"


def test_responsibility_map_names_every_slice_owner():
    section = _section(_doc_text(), "### 4.1 Responsibility map — nine initiative slices to acceptance predicates")
    first_cells = [line.split("|")[1] for line in section.splitlines() if line.startswith("| ")]
    for owner in SLICE_OWNERS:
        assert any(f"({owner}" in cell for cell in first_cells), f"no responsibility-map row owned by {owner}"


def test_four_contracts_are_independently_versioned():
    section = _section(_doc_text(), "### 4.7 Versioned contracts and ledger extension")
    assert '`"MAJOR.MINOR"`' in section
    for contract, owner in (("Configuration", "#557"), ("Request", "#558"), ("Result", "#558"),
                            ("Capability evidence", "#561/#562")):
        assert f"| {contract} | {owner} |" in section, f"{contract} contract row missing"
    assert "major bumps are a hard reject" in section


def test_capability_evidence_schema_never_qualifies_a_checker():
    section = _section(_doc_text(), "### 4.5 Capability evidence")
    assert 'schema_version: "1.0"' in section
    assert "qualified_roles: [worker]" in section
    for field in ("endpoint_id:", "model_identity:", "fixture_suite_version:", "expires_at:",
                  "invalidated_by:"):
        assert field in section, f"capability-evidence field {field} missing"


def test_spec_delta_for_239_lists_items_12_to_15():
    section = _section(_doc_text(), "### 4.9 Exact spec delta for #239")
    for n in (12, 13, 14, 15):
        assert re.search(rf"^{n}\. ", section, re.MULTILINE), f"#239 delta item {n} missing"


def test_fail_closed_invariants_pinned():
    text = _doc_text()
    for phrase in FAIL_CLOSED_INVARIANTS:
        assert phrase in text, f"fail-closed invariant weakened or removed: {phrase}"


def test_invariants_index_cites_existing_sections():
    text = _doc_text()
    body = _section(text, "## Invariants (index)")
    items = [item.strip() for item in re.split(r"^\d+\. ", body, flags=re.MULTILINE)[1:]]
    assert len(items) >= 15, f"expected at least 15 indexed invariants, found {len(items)}"
    headings = set(re.findall(r"^### (4\.\d+) ", text, re.MULTILINE))
    for item in items:
        cited = re.findall(r"§(4\.\d+)", item)
        assert cited, f"invariant does not cite a section: {item[:60]}"
        for sec in cited:
            assert sec in headings, f"invariant cites missing §{sec}: {item[:60]}"


def test_cited_repo_files_resolve():
    text = _doc_text()
    files = [p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.rglob("*")
             if p.is_file() and ".git" not in p.parts]
    missing = [tok for tok in sorted(set(CITATION_RE.findall(text)))
               if not (REPO_ROOT / tok).is_file() and not any(f.endswith("/" + tok) for f in files)]
    assert not missing, f"cited files no longer resolve: {missing}"


def test_doc_does_not_cite_the_pre_archive_spec_path():
    assert "docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md" not in _doc_text()
```

- [ ] **Step 2: Run it and verify it fails**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -q
```

Expected: `10 failed`, each with `AssertionError: .../docs/hybrid-execution-contract.md does not exist — #556's decision report must live here`.

- [ ] **Step 3: Write the new header and front matter to scratch files**

These are not repo files; they are inputs to Step 4's build script.

```bash
mkdir -p /tmp/hec
cat > /tmp/hec/header.md <<'HEC_EOF'
# Hybrid Model Execution Contract — Cloud + Local

**Issue:** #556

**Contract version:** `hybrid_contract: "1.0"`

**Status:** Proposed; accepted on the merge of the #556 PR by a human reviewer. Until that
merge, no downstream implementation spec (#557–#564, or #239's re-refinement) may be written
against this contract; the copy of this file on `main` is the accepted one. The five §4.11
decisions are provisional operator defaults pending owner confirmation at that review.

HEC_EOF
cat > /tmp/hec/front.md <<'HEC_EOF'
## About this document

This is the durable decision report #556 asked for. Everything from "Overview / Problem
Statement" down is the reviewed refinement spec for #556, reproduced verbatim, so "this
document" and "this spec" in that text both mean this file. The spec itself is archived to
`docs/archive/` when the #556 PR is pushed and is design history only; from then on this file
is authoritative. A changed decision is a reviewed revision of this file, not a downstream
re-decision (§4.11). This contract changes no code, config, gate, breaker, budget, deployment
or model default; the downstream tickets in §4.1 consume it, each through its own reviewed
refinement.

The body keeps its refinement-time wording. Statements about what "this phase" may output
describe the refinement phase that produced it. References to precedents "reproduced below"
(#189, #207) and to "§Scope boundary" point at refinement-time context that is not reproduced
here; this contract's own scope limits are Requirement 7 and §4.10.

## Invariants (index)

Each item restates a rule defined in the section it cites. Where the wording differs, the cited
section governs.

1. Endpoint isolation is per dispatched run container; one container never mixes cloud and
   local endpoints in-process (Q1, §4.2).
2. An independent checker is always a separate dispatch, cloud-routed by default. A nested
   Agent-tool subagent of a local-routed parent is never a checker, and a verdict produced
   inside a local-routed container is void (§4.2).
3. Checker independence is proven by the dispatch record (`hosting_location: cloud` and an
   `endpoint_id` different from the actor's), never by an agent's own claim; a row with
   `correlation: "unknown"` is never that proof (§4.2, §4.4).
4. A dispatch is allowed only if the payload class is a member of the target entry's
   `accepts_payload_classes`; unknown or missing on either side blocks and escalates (§4.3).
5. Payload class is stricter-only: no label, comment or model output can loosen it; only
   operator-controlled config can grant `cloud_ok` (§4.3).
6. A local-routed dispatch never retries on, or falls back to, cloud. Every local failure,
   timeout or cancellation resolves to a #238 failure class and abstains or escalates (§4.3,
   §4.6).
7. `local_only` work never auto-merges; the merge is always a human action (§4.3, §4.11).
8. Secrets are referenced by `secret_reference` and never appear in the correlation envelope,
   a ledger row, a run record, a log line or an issue comment (§4.3).
9. Capability evidence is authored only by #562's certification run against #561's pinned
   fixtures, never by a model's self-report; missing, expired, failed or mismatched evidence
   fails closed with no grace period (§4.5).
10. Correlation that cannot be attributed is recorded as `inferred_pointer` or `unknown`, never
    guessed (§4.4).
11. Every local endpoint-registry entry declares connect, time-to-first-token, idle and total
    timeout bounds, and the runtime adapter enforces them, not the agent (§4.6).
12. Minor schema-version bumps are additive; major bumps are a hard reject (§4.7).
13. Local runs count toward WIP limits, dispatch ceilings, the retry breaker, token budgets and
    the session-window pause exactly like cloud runs; local cost is `cost_usd: null`, never
    zero; a local 429 never writes the shared session-window pause sentinel (§4.10).
14. Neither this contract nor comment-channel input authorizes any change to budgets, the
    breaker, the session-window pause, `gate_*`, tool allow/deny lists, side-effect levels or
    `deploy/**` (§4.10).
15. Merging a downstream ticket activates nothing. Rollback is unsetting the local endpoint
    selection in operator-controlled config, which restores today's default byte for byte; an
    in-flight local run fails closed and is never re-run on cloud automatically (§4.12).

HEC_EOF
```

Note `header.md` ends with one blank line and `front.md` ends with one blank line; the heredocs
above preserve both.

- [ ] **Step 4: Build `docs/hybrid-execution-contract.md` from the spec**

The asserts stop the build if the spec's layout has drifted from what this plan was written
against (title line 1, `**Revised:**` line 5, `---` line 11, `## Overview / Problem Statement`
line 13). If any assert fires, stop and re-check the spec rather than editing offsets blindly.

```bash
cat > /tmp/hec/build.py <<'HEC_EOF'
from pathlib import Path

SPEC = Path("docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md")
DOC = Path("docs/hybrid-execution-contract.md")

spec = SPEC.read_text(encoding="utf-8").splitlines(keepends=True)
assert spec[0].startswith("# Hybrid Model Execution Contract"), spec[0]
assert spec[4].startswith("**Revised:**"), spec[4]
assert spec[10].strip() == "---", spec[10]
assert spec[12].startswith("## Overview / Problem Statement"), spec[12]

header = Path("/tmp/hec/header.md").read_text(encoding="utf-8")
front = Path("/tmp/hec/front.md").read_text(encoding="utf-8")
DOC.write_text(header + "".join(spec[4:12]) + front + "---\n\n" + "".join(spec[12:]),
               encoding="utf-8")
print(f"wrote {DOC} ({len(DOC.read_text(encoding='utf-8').splitlines())} lines)")
HEC_EOF
python3 /tmp/hec/build.py
```

Expected: `wrote docs/hybrid-execution-contract.md (595 lines)`.

- [ ] **Step 5: Verify the body is verbatim**

```bash
diff <(sed -n '79,$p' docs/hybrid-execution-contract.md) \
     <(sed -n '13,$p' docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md) \
  && diff <(sed -n '12,16p' docs/hybrid-execution-contract.md) \
          <(sed -n '5,9p' docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md) \
  && echo VERBATIM-OK
```

Expected: `VERBATIM-OK` and no diff output. Line 79 of the new doc must be
`## Overview / Problem Statement` (`sed -n 79p docs/hybrid-execution-contract.md`).

- [ ] **Step 6: Run the drift guard and verify it passes**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -q
```

Expected: `10 passed`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_hybrid_execution_contract_doc.py docs/hybrid-execution-contract.md
git commit -m "docs(contract): publish durable hybrid execution contract decision report (#556)

Promotes the reviewed #556 spec body verbatim to docs/hybrid-execution-contract.md behind a
Status: Proposed header (acceptance = human merge of the #556 PR), an authority note and an
invariants index, with a pytest drift guard. The spec itself is archived by push-and-pr.
No runtime, gate, breaker, budget, deployment or model-default change."
```

---

## Task 2: Link the contract from the README and the model-endpoint axis

**Files:**
- Modify: `tests/test_hybrid_execution_contract_doc.py` (append two tests)
- Modify: `docs/provider-abstraction-design.md` (§7.5, after the "Caveat 2" bullet)
- Modify: `README.md` ("Further reading", after the last bullet)

- [ ] **Step 1: Append the failing link tests**

Append to the end of `tests/test_hybrid_execution_contract_doc.py`:

```python


def test_readme_further_reading_links_the_contract():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "[`docs/hybrid-execution-contract.md`](docs/hybrid-execution-contract.md)" in readme


def test_provider_design_model_axis_points_at_the_contract():
    text = (REPO_ROOT / "docs" / "provider-abstraction-design.md").read_text(encoding="utf-8")
    section = text[text.index("### 7.5 Selection, preflight, caveats"):text.index("## 8. Scope boundaries")]
    assert "[`docs/hybrid-execution-contract.md`](hybrid-execution-contract.md)" in section
    assert "(#556)" in section
```

- [ ] **Step 2: Run and verify the two new tests fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -q
```

Expected: `2 failed, 10 passed` (`test_readme_further_reading_links_the_contract`,
`test_provider_design_model_axis_points_at_the_contract`).

- [ ] **Step 3: Add the §7.5 pointer to `docs/provider-abstraction-design.md`**

Edit — replace this exact text (end of §7.5, unique in the file):

```markdown
it runs, but coding quality is a separate, measured question, not an assumed equivalence.

## 8. Scope boundaries
```

with:

```markdown
it runs, but coding quality is a separate, measured question, not an assumed equivalence.

> **Hybrid cloud + local (#556).** Adding a *local* endpoint alongside the cloud paths is governed by [`docs/hybrid-execution-contract.md`](hybrid-execution-contract.md), not by the gateway design above. v1 supports one local server that speaks the Anthropic Messages API through `ANTHROPIC_BASE_URL` (no translation layer), isolates endpoints per dispatched run container, keeps every independent checker on a separate cloud-routed dispatch, and never falls back from a local failure to cloud. The contract is a decision report: nothing in it is implemented yet (#557–#564).

## 8. Scope boundaries
```

(One unwrapped line, matching the file's existing `> **Implementation status:** …` blockquotes.)

- [ ] **Step 4: Add the README "Further reading" line**

Append one line after the current last line of `README.md`
(``- [`docs/adapter-authoring-guide.md`](docs/adapter-authoring-guide.md) — how to write a tracker, code-host, or model-endpoint adapter``):

```markdown
- [`docs/hybrid-execution-contract.md`](docs/hybrid-execution-contract.md) — hybrid cloud + local model execution contract (#556): decision report, not yet implemented
```

- [ ] **Step 5: Run and verify all drift-guard tests pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -q
```

Expected: `12 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_hybrid_execution_contract_doc.py docs/provider-abstraction-design.md README.md
git commit -m "docs(contract): link hybrid execution contract from README and provider design §7.5 (#556)"
```

---

## Task 3: Full-suite and archive-safety verification

**Files:** none modified (verification only; nothing to commit).

- [ ] **Step 1: Run the full test suite**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

Expected: all pass, 0 failed (2877 passed when this plan was dry-run on a scratch copy; the
exact count moves with `main`).

- [ ] **Step 2: Confirm nothing durable pins the pre-archive spec path**

`push-and-pr` will move the spec to `docs/archive/`; only the drift guard's own negative
assertion may name its current path. `-I` and `--exclude-dir=__pycache__` keep pytest's cached
`.pyc` of that test (which contains the same string) out of the result.

```bash
grep -rlnI "docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md" \
  README.md docs scripts tests commands workflows config \
  --exclude-dir=superpowers --exclude-dir=archive --exclude-dir=__pycache__ \
  | grep -v '^tests/test_hybrid_execution_contract_doc.py$' || echo ARCHIVE-SAFE
```

Expected: `ARCHIVE-SAFE`.

- [ ] **Step 3: Confirm the archived layout still passes**

```bash
git status --porcelain | grep -q . && { echo "tree not clean"; exit 1; }
mkdir -p docs/archive
git mv docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md docs/archive/
PYTHONPATH=scripts python -m pytest tests/test_hybrid_execution_contract_doc.py -q
git mv docs/archive/2026-11-21-hybrid-execution-contract-design.md docs/superpowers/specs/
git status --porcelain
```

Expected: `12 passed`, and the final `git status --porcelain` prints nothing (the trial move is
fully undone — the real archive move belongs to `push-and-pr`, not to this task).

- [ ] **Step 4: Confirm scope**

```bash
git diff --name-only origin/main...HEAD | sort
```

Expected: exactly these four implementation paths —

```text
README.md
docs/hybrid-execution-contract.md
docs/provider-abstraction-design.md
tests/test_hybrid_execution_contract_doc.py
```

— plus only refine/plan-phase artifacts already on the branch
(`docs/superpowers/specs/2026-11-21-hybrid-execution-contract-design.md`,
`docs/superpowers/plans/2026-11-22-hybrid-execution-contract.md`, and any `.archon/memory/*.md`).
Any other path is out of scope and must be reverted before pushing. Before reverting an
unexpected path, confirm with the two-dot form `git diff origin/main HEAD -- <path>`: if that
prints nothing, `main` already carries the same content and the path is not a real change
(memory `codebase-patterns.md`, #250).

---

## Self-review

- Spec Requirement 1 (durable decision report with evidence, responsibility map, invariants,
  compatibility matrix, versioned contracts, risks, rollout/rollback): Task 1 — verbatim body
  carries Overview evidence and §4.1–§4.12; the new "Invariants (index)" makes the invariants
  explicit; the drift guard pins each section.
- Requirements 2–6 (§4.1 map and separation of concerns, §4.2 coexistence/refusal, §4.3/§4.6
  confidentiality, credential stripping, correlation and outage behavior, #288 taxonomy, §4.9
  #239 delta): carried verbatim; slice owners, contract rows, evidence schema, #239 items 12–15
  and the fail-closed phrases are pinned by tests.
- Requirement 7 (human acceptance before downstream specs; no runtime/gate/breaker/deployment/
  model-default change): the **Status: Proposed** header names the human merge of the #556 PR
  as the acceptance record; File Structure touches only docs and one test.
- Memory lessons baked in: spec archival vs. pinned paths (Task 3 Steps 2–3); verify cited
  artifacts actually exist rather than trusting prose (`test_cited_repo_files_resolve`).
- No placeholders; every step has exact content, commands and expected output. The
  `**Issue:** #556` line is present under the title.

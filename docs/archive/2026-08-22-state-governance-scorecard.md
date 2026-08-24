# Add an always-on state governance scorecard for Dark Factory persistent state — Implementation Plan

**Issue:** omniscient/dark-factory#190
**Spec:** `docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md`

---

## Goal

Ship the spec's **hard-capped, code-buildable subset** (spec Requirement 3): a standalone
`scripts/state_governance_audit.py` implementing exactly 5 deterministic governance checks
(authority monotonicity, scope non-expansion, deletion propagation, provenance
preservation, rollback traceability) over a committed synthetic fixture corpus, plus a
byte-stable committed sample scorecard. The spec itself already delivers AC1 (paper
summary), AC2 (10-class inventory), the event/snapshot schema (AC3), the retrospective
(AC6), and the recommendation (AC7) as spec prose — this plan does not re-derive those.
No changes to `config/config.yaml`, `entrypoint.sh`, or the workflow DAG (spec
Requirement 7 / CLAUDE.md's "gate changes get their own reviewed ticket").

## Architecture

```
evals/state-governance/fixtures/*.jsonl   (11 files: 10 check pass/fail + 1 combined)
evals/state-governance/fixtures/manifest.json  (fixture -> {check, expected_verdict})
                    │
                    ▼
scripts/state_governance_audit.py --fixtures <dir-or-file> --out-dir <dir> [--now] [--run-id]
    load_events()            — parse JSONL, preserve file order (order = chronology)
    check_authority_monotonicity(events)
    check_scope_non_expansion(events)
    check_deletion_propagation(events)
    check_provenance_preservation(events)
    check_rollback_traceability(events)
    compute_scorecard(events, now, run_id)  — aggregate STATUS/score
    write_json() / write_markdown()
                    │
                    ▼
evals/state-governance/sample/state-governance-scorecard.{json,md}  (committed, byte-stable)
```

Exit code is always 0 (matches `gate_blast_radius.py`'s "Exit 0 always — the caller reads
STATUS from the output" convention). This ticket wires no caller — advisory only, per
spec Recommendation.

### Design notes (operationalization decisions, not open questions)

The spec's check descriptions are precise about *what* to detect but leave 3 mechanical
details to the implementation; each is pinned here so no task below is ambiguous:

1. **"later retrieve/act event" (check 3, deletion propagation):** the event envelope's
   `operation` enum (spec §Event/snapshot schema, reused verbatim from the issue) has no
   separate `retrieve` value — `act` is the only read/consumption operation in the schema.
   The check therefore treats `operation: act` as the qualifying read.
2. **Scope "width" (check 2, scope non-expansion):** operationalized as a 2-level order —
   width 0 (narrow) when `scope.issue` or `scope.pr` is set, width 1 (wide/repo-wide)
   otherwise. "Widened without an explicit new authorizing event of equal or higher
   permission_epoch" means: for consecutive events sharing an `entity_id` (file order),
   if width increases, the widening event's own `authority.permission_epoch` must be `>=`
   the previous event's epoch in the same group.
3. **`approval_record` resolution (check 1, authority monotonicity):** `approval_record`
   is treated as the `event_id` of the authorizing event *when it resolves to one inside
   the same fixture corpus*. An unresolved `approval_record` (e.g. a real external
   `issue-comment-or-run-id` reference, per the issue's original field description) is not
   flagged — a single-source synthetic corpus has no way to independently verify an
   external reference, and that gap is provenance-preservation's concern (check 4), not
   authority-monotonicity's.

## Tech Stack

- Python 3 stdlib only (`argparse`, `json`, `pathlib`, `datetime`) — matches every
  comparable script (`fetch_scorecard.py`, `gate_blast_radius.py`, `eval_memory_quality.py`).
- `pytest` for both test files, matching `tests/test_eval_memory_quality.py`'s
  no-subprocess, inline-fixture-data convention for check-function unit tests.
- JSONL for the event corpus (append-only, one event per line — matches `runs.jsonl` /
  `.archon/memory/index.jsonl` convention).

## File Structure

| File | Change |
|---|---|
| `scripts/state_governance_audit.py` | **New** — 5 check functions, scorecard aggregation, JSON/MD report writers, CLI |
| `evals/state-governance/fixtures/*.jsonl` | **New** — 11 files (10 check pass/fail + 1 combined `realistic-run-01.jsonl`) |
| `evals/state-governance/fixtures/manifest.json` | **New** — fixture filename → `{check, expected_verdict}` |
| `evals/state-governance/sample/state-governance-scorecard.json` | **New** — committed, deterministic sample output |
| `evals/state-governance/sample/state-governance-scorecard.md` | **New** — committed, deterministic sample output |
| `tests/test_state_governance_audit.py` | **New** — check-function unit tests, manifest cross-check, byte-stable regeneration test |
| `tests/test_state_governance_fixtures.py` | **New** — corpus/schema/manifest invariant tests, modeled on `tests/test_behavioral_state_fixtures.py` (spec §Fixture corpus) |

---

## Task 1 — Carry the spec onto the feat branch (+ add its missing Follow-ups section); scaffold the module skeleton and the failing corpus validator test

**Files:** `docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md` (copied),
`scripts/state_governance_audit.py` (new, skeleton), `tests/test_state_governance_fixtures.py` (new)

### Steps

1. This plan and its spec were committed on the sibling `refine/issue-190-...` branch;
   they do not transfer automatically onto this ticket's `feat/issue-190-...` branch
   (memory pattern from #42/#212 — `.archon/memory/codebase-patterns.md`). A plain
   `git clone` (per `entrypoint.sh`) only creates `main` as a local branch, so `git show`
   needs the branch fetched and referenced via its remote-tracking name — a bare local
   branch name here fails with `fatal: invalid object name` (the repo already handles this
   exact case at `entrypoint.sh`'s `git checkout -b "$B" "origin/$B"` fallback):
   ```bash
   mkdir -p docs/superpowers/specs docs/superpowers/plans
   git fetch origin --quiet
   git show origin/refine/issue-190-add-always-on-state-governance-scorecard:docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md > docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md
   git show origin/refine/issue-190-add-always-on-state-governance-scorecard:docs/superpowers/plans/2026-08-22-state-governance-scorecard.md > docs/superpowers/plans/2026-08-22-state-governance-scorecard.md
   git add docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md docs/superpowers/plans/2026-08-22-state-governance-scorecard.md
   git commit -m "docs: bring over approved spec/plan for issue #190"
   ```

2. The copied-in spec references a `## Follow-ups` heading five times (deferred
   `rollback-ledger.jsonl` work, the live-capture adapters, and the three real defects
   found during refinement) but never defines that heading — add it, consolidating what
   those five references already point at, so the deferred scope has a recorded
   destination. This step's heredoc is executed verbatim, unlike the plan's other code
   blocks (which are written *into* a file) — dedent it to column 0 before running, or the
   embedded Python raises `IndentationError`:
   ```bash
   python3 - <<'PYEOF'
   from pathlib import Path

   spec_path = Path("docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md")
   text = spec_path.read_text(encoding="utf-8")

   follow_ups = '''## Follow-ups

   Named, out-of-scope-for-this-ticket work referenced elsewhere in this spec:

   - **Live-capture adapters** (§Alternatives considered #2): wire `memory_write.py`,
     `run_record.py`, and `entrypoint.sh` to emit real `state-lineage.jsonl` events instead
     of the synthetic fixture corpus this ticket ships. Touches `entrypoint.sh`/the
     workflow DAG, so it needs its own reviewed ticket per CLAUDE.md.
   - **`rollback-ledger.jsonl`** (Requirement 10): the issue's proposed per-run runtime
     rollback-ledger artifact. Depends on the live-capture adapters above existing first.
   - **Fix the three real defects found during this ticket's context assembly** (§Fixture
     corpus, §Alternatives considered #4): `.archon/memory/index.jsonl` rows omitting
     `id`/`source_file`/`path_prefixes` (provenance-preservation gap),
     `memory_maintain.py`'s expiry/supersession never propagating to `index.jsonl`
     (deletion-propagation gap), and `memory_write.py:95`'s hardcoded
     `"project": "markethawk"` (scope-non-expansion gap). This ticket's job is the
     detector, not the fix — these are filed separately.

   '''

   marker = "## Assumptions (flagged)"
   assert marker in text, "spec is missing the expected trailing ## Assumptions section"
   text = text.replace(marker, follow_ups + marker, 1)
   spec_path.write_text(text, encoding="utf-8")
   PYEOF
   git add docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md
   git commit -m "docs(spec): add missing Follow-ups section referenced by 5 existing cross-references (#190)"
   ```

3. Write the module skeleton — constants, the loader, and the full stdlib import block up
   front (matching `gate_blast_radius.py`/`eval_memory_quality.py`'s top-of-file import
   convention; `argparse`/`sys` stay unused until Task 7 wires the CLI, which is fine —
   Tasks 2-6 build the 5 check functions incrementally, each a self-contained, tested
   increment, before Task 7 assembles them):
   ```python
   #!/usr/bin/env python3
   """State governance audit — deterministic scorecard over synthetic state-lineage fixtures (#190).

   Reads a directory (or single file) of state-lineage.jsonl event envelopes and computes 5
   deterministic governance checks: authority monotonicity, scope non-expansion, deletion
   propagation, provenance preservation, and rollback traceability. Advisory only — this
   script wires no caller and always exits 0; the caller reads STATUS/score from the output.

   Usage:
       python3 scripts/state_governance_audit.py \\
           --fixtures evals/state-governance/fixtures/ \\
           --out-dir  "$ARTIFACTS_DIR" \\
           [--now 2026-08-22T00:00:00Z] \\
           [--run-id sample-run]

   (The committed sample under evals/state-governance/sample/ is generated differently —
   from the single combined fixture only, with fixed --now/--run-id — see Task 8. Pointing
   --fixtures at the whole fixtures/ directory aggregates all 11 files, including the 10
   check-isolated pass/fail cases, into one run; that is the real multi-entity-corpus
   invocation this docstring documents, not the sample-regeneration invocation.)
   """
   import argparse
   import json
   import sys
   from datetime import datetime, timezone
   from pathlib import Path

   CHECK_NAMES = [
       "authority_monotonicity",
       "scope_non_expansion",
       "deletion_propagation",
       "provenance_preservation",
       "rollback_traceability",
   ]

   # Reserved state_type enum — harness_economics (#234) and memory_intervention (#241) are
   # reserved identifiers owned by other epics; no fixtures/checks exercise them here.
   STATE_TYPES = {
       "memory", "issue", "project_status", "branch", "pr", "skill", "permission",
       "artifact", "external_commitment", "mechanism_lineage", "harness_economics",
       "memory_intervention",
   }

   OPERATIONS = {
       "write", "update", "delete", "tombstone", "share", "unshare", "validate",
       "quarantine", "deny", "rollback", "act",
   }

   ACTIONABILITIES = {
       "evidence", "advisory", "policy", "permission", "skill", "external_commitment",
   }

   _TOMBSTONE_OPS = {"delete", "tombstone", "quarantine"}
   _ACTIVE_STATUSES = {"active"}
   _HIGH_ACTIONABILITY = {"policy", "permission", "skill", "external_commitment"}


   def load_events(path):
       """Read one or more state-lineage.jsonl files (dir glob or single file), preserving file order."""
       p = Path(path)
       if p.is_dir():
           paths = sorted(p.glob("*.jsonl"))
       else:
           paths = [p]
       events = []
       for fp in paths:
           for line in fp.read_text(encoding="utf-8").splitlines():
               line = line.strip()
               if not line:
                   continue
               events.append(json.loads(line))
       return events


   def _group_by_entity(events):
       groups = {}
       for e in events:
           groups.setdefault(e.get("entity_id"), []).append(e)
       return groups
   ```

4. Write `tests/test_state_governance_fixtures.py` in full (it references
   `evals/state-governance/fixtures/` and `evals/state-governance/sample/`, neither of
   which exist yet — this is the RED state Tasks 2-8 turn GREEN, mirroring
   `tests/test_behavioral_state_fixtures.py`'s relationship to its own corpus):
   ```python
   """Schema and corpus-invariant tests for the state-governance fixture corpus (#190).

   Modeled on tests/test_behavioral_state_fixtures.py (#242). Guards the synthetic
   state-lineage.jsonl corpus scripts/state_governance_audit.py's 5 checks are validated
   against: every event must carry the full envelope from
   docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md, and the corpus
   must stay within its 11-file hard cap (10 pass/fail + 1 combined).
   """

   import json
   import sys
   from pathlib import Path

   import pytest

   sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

   from state_governance_audit import ACTIONABILITIES, CHECK_NAMES, OPERATIONS, STATE_TYPES  # noqa: E402

   _REPO_ROOT = Path(__file__).resolve().parents[1]
   _EVAL_DIR = _REPO_ROOT / "evals" / "state-governance"
   _FIXTURES_DIR = _EVAL_DIR / "fixtures"
   _SAMPLE_DIR = _EVAL_DIR / "sample"
   _MANIFEST_FILE = _FIXTURES_DIR / "manifest.json"

   REQUIRED_EVENT_KEYS = {
       "event_id", "idempotency_key", "operation", "state_type", "entity_id",
       "authority", "scope", "provenance", "mutability", "recoverability", "actionability",
   }

   COMBINED_FIXTURE = "realistic-run-01.jsonl"


   def _fixture_paths():
       if not _FIXTURES_DIR.is_dir():
           return []
       return sorted(_FIXTURES_DIR.glob("*.jsonl"))


   def _load_jsonl(path):
       events = []
       for line in path.read_text(encoding="utf-8").splitlines():
           line = line.strip()
           if line:
               events.append(json.loads(line))
       return events


   class TestFixtureCorpus:
       def test_fixtures_dir_exists(self):
           assert _FIXTURES_DIR.is_dir(), f"{_FIXTURES_DIR} does not exist"

       def test_corpus_has_exactly_11_files(self):
           paths = _fixture_paths()
           assert len(paths) == 11, (
               f"Expected the hard-capped 11 fixture files (10 pass/fail + 1 combined), got {len(paths)}"
           )

       @pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
       def test_fixture_events_conform_to_envelope_schema(self, path):
           events = _load_jsonl(path)
           assert events, f"{path.name} has no events"
           for event in events:
               missing = REQUIRED_EVENT_KEYS - set(event.keys())
               assert not missing, f"{path.name}/{event.get('event_id')} missing keys: {missing}"
               assert event["state_type"] in STATE_TYPES, (
                   f"{path.name}/{event['event_id']} has unknown state_type {event['state_type']!r}"
               )
               assert event["operation"] in OPERATIONS, (
                   f"{path.name}/{event['event_id']} has unknown operation {event['operation']!r}"
               )
               assert event["actionability"] in ACTIONABILITIES, (
                   f"{path.name}/{event['event_id']} has unknown actionability {event['actionability']!r}"
               )


   class TestManifest:
       def test_manifest_file_exists(self):
           assert _MANIFEST_FILE.exists()

       def test_manifest_covers_every_fixture_file(self):
           manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
           fixture_names = {p.name for p in _fixture_paths()}
           assert set(manifest.keys()) == fixture_names

       def test_every_check_has_a_pass_and_fail_fixture(self):
           manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
           by_check = {}
           for fname, spec in manifest.items():
               if spec["check"] == "combined":
                   continue
               by_check.setdefault(spec["check"], set()).add(spec["expected_verdict"])
           missing = set(CHECK_NAMES) - set(by_check.keys())
           assert not missing, f"No fixtures at all for checks: {missing}"
           for check in CHECK_NAMES:
               assert by_check[check] == {"PASS", "FAIL"}, (
                   f"{check} must have both a PASS and a FAIL fixture, got {by_check[check]}"
               )

       def test_combined_fixture_is_declared(self):
           manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
           assert COMBINED_FIXTURE in manifest
           assert manifest[COMBINED_FIXTURE]["check"] == "combined"


   class TestSampleArtifacts:
       def test_sample_json_exists(self):
           assert (_SAMPLE_DIR / "state-governance-scorecard.json").exists()

       def test_sample_md_exists(self):
           assert (_SAMPLE_DIR / "state-governance-scorecard.md").exists()

       def test_sample_json_has_the_minimum_output_contract(self):
           data = json.loads((_SAMPLE_DIR / "state-governance-scorecard.json").read_text(encoding="utf-8"))
           assert data["STATUS"] in ("PASS", "WARN", "FAIL")
           assert isinstance(data["score"], int) and 0 <= data["score"] <= 100
           assert [c["name"] for c in data["checks"]] == CHECK_NAMES
           for c in data["checks"]:
               assert c["verdict"] in ("PASS", "WARN", "FAIL")
               assert isinstance(c["violations"], list)
   ```

5. Run it and confirm it fails red (`evals/state-governance/fixtures/` doesn't exist yet):
   ```bash
   python -m pytest tests/test_state_governance_fixtures.py -v
   ```
   Expected: `TestFixtureCorpus::test_fixtures_dir_exists` fails (`_FIXTURES_DIR.is_dir()`
   is `False`); `test_fixture_events_conform_to_envelope_schema` is skipped (empty
   parametrize set, no fixtures on disk yet to collect); every other test fails (manifest
   and sample files don't exist). That failure/skip set is the RED state Tasks 2-8 turn GREEN.

   **Acceptance bar per task:** this commit — and every commit through Task 7 — leaves
   `python -m pytest tests/ -v` (the CI command) partially red, because
   `test_corpus_has_exactly_11_files` and `TestSampleArtifacts` can't pass until the 11th
   fixture and the committed sample land in Task 8. This is the same progressive
   red-to-green shape as the already-merged `docs/archive/2026-07-16-behavioral-state-decay-baseline-plan.md`
   (#242), whose own corpus validator is committed red in its Task 1 and only turns fully
   green in its Task 10. The per-task acceptance gate through Task 7 is the task's own
   scoped `pytest -k <ClassName>` run (each task's step 2 and step 4); the blanket
   `tests/ -v` green state is Task 8's exit criterion and Task 9's final verification.

6. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_fixtures.py
   git commit -m "test(state-governance): scaffold audit module skeleton + failing corpus validator (#190)"
   ```

---

## Task 2 — Check 1: authority monotonicity

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (new),
`evals/state-governance/fixtures/authority-monotonicity-{pass,fail}.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (new)

### TDD Steps

1. Write `tests/test_state_governance_audit.py` (new file — this is the first check, so it
   starts with the shared `_event()` builder plus this check's test class only; Tasks 3-6
   append one class each):
   ```python
   """Tests for scripts/state_governance_audit.py's 5 deterministic checks (#190).

   No subprocess, no network — check-function tests use inline event dicts; the
   regeneration test (Task 8) diffs freshly-computed output against the committed sample.
   """

   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

   from state_governance_audit import check_authority_monotonicity  # noqa: E402


   def _event(**overrides):
       """Build a minimal, schema-complete event envelope with sane defaults."""
       base = {
           "event_id": "evt-1",
           "idempotency_key": "issue-190:evt-1",
           "operation": "write",
           "state_type": "memory",
           "entity_id": "memory:x",
           "authority": {"actor": "refine", "permission_epoch": 1, "approval_record": None},
           "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"},
           "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-1", "commit": None},
           "mutability": {"status": "active", "supersedes": [], "conflicts_with": []},
           "recoverability": {"transaction_id": None, "rollback_handle": None, "external_effects": []},
           "actionability": "advisory",
       }
       base.update(overrides)
       return base


   class TestAuthorityMonotonicity:
       def test_equal_epoch_passes(self):
           e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 2, "approval_record": None})
           e2 = _event(event_id="a2", authority={"actor": "x", "permission_epoch": 2, "approval_record": "a1"})
           verdict, violations = check_authority_monotonicity([e1, e2])
           assert verdict == "PASS"
           assert violations == []

       def test_inflated_epoch_fails(self):
           e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 2, "approval_record": None})
           e2 = _event(event_id="a2", authority={"actor": "x", "permission_epoch": 5, "approval_record": "a1"})
           verdict, violations = check_authority_monotonicity([e1, e2])
           assert verdict == "FAIL"
           assert len(violations) == 1
           assert violations[0]["event_id"] == "a2"

       def test_unresolved_approval_record_is_not_a_violation(self):
           e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 5, "approval_record": "no-such-event"})
           verdict, violations = check_authority_monotonicity([e1])
           assert verdict == "PASS"
   ```

2. Run it and confirm it fails red (`check_authority_monotonicity` doesn't exist yet):
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v
   ```

3. Append to `scripts/state_governance_audit.py`:
   ```python
   def check_authority_monotonicity(events):
       """An event's authority.permission_epoch must not exceed the epoch of the event its
       approval_record claims to derive from (approval_record holds another event's event_id)."""
       by_id = {e.get("event_id"): e for e in events}
       violations = []
       for e in events:
           auth = e.get("authority") or {}
           epoch = auth.get("permission_epoch")
           approval_record = auth.get("approval_record")
           if epoch is None or not approval_record:
               continue
           ref = by_id.get(approval_record)
           if ref is None:
               continue
           ref_epoch = (ref.get("authority") or {}).get("permission_epoch")
           if ref_epoch is not None and epoch > ref_epoch:
               violations.append({
                   "event_id": e.get("event_id"),
                   "entity_id": e.get("entity_id"),
                   "reason": (
                       f"permission_epoch {epoch} exceeds approval_record "
                       f"{approval_record}'s epoch {ref_epoch}"
                   ),
               })
       verdict = "FAIL" if violations else "PASS"
       return verdict, violations
   ```

4. Run again, confirm green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v
   ```

5. Add the fixtures:
   ```bash
   mkdir -p evals/state-governance/fixtures
   ```
   `evals/state-governance/fixtures/authority-monotonicity-pass.jsonl`:
   ```
   {"event_id": "evt-am-1", "idempotency_key": "issue-190:evt-am-1", "operation": "validate", "state_type": "project_status", "entity_id": "issue:am-pass-01", "authority": {"actor": "scheduler", "permission_epoch": 2, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-am-pass-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   {"event_id": "evt-am-2", "idempotency_key": "issue-190:evt-am-2", "operation": "act", "state_type": "project_status", "entity_id": "issue:am-pass-01", "authority": {"actor": "scheduler", "permission_epoch": 2, "approval_record": "evt-am-1"}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-am-pass-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": "txn-am-pass-1", "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   ```
   `evals/state-governance/fixtures/authority-monotonicity-fail.jsonl`:
   ```
   {"event_id": "evt-am-3", "idempotency_key": "issue-190:evt-am-3", "operation": "validate", "state_type": "project_status", "entity_id": "issue:am-fail-01", "authority": {"actor": "scheduler", "permission_epoch": 2, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-am-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   {"event_id": "evt-am-4", "idempotency_key": "issue-190:evt-am-4", "operation": "act", "state_type": "project_status", "entity_id": "issue:am-fail-01", "authority": {"actor": "scheduler", "permission_epoch": 5, "approval_record": "evt-am-3"}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-am-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": "txn-am-fail-1", "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   ```

6. Create `evals/state-governance/fixtures/manifest.json`:
   ```json
   {
     "authority-monotonicity-pass.jsonl": {"check": "authority_monotonicity", "expected_verdict": "PASS"},
     "authority-monotonicity-fail.jsonl": {"check": "authority_monotonicity", "expected_verdict": "FAIL"}
   }
   ```

7. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py evals/state-governance/fixtures/authority-monotonicity-pass.jsonl evals/state-governance/fixtures/authority-monotonicity-fail.jsonl evals/state-governance/fixtures/manifest.json
   git commit -m "feat(state-governance): add authority_monotonicity check + fixtures (#190)"
   ```

---

## Task 3 — Check 2: scope non-expansion

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (append class),
`evals/state-governance/fixtures/scope-non-expansion-{pass,fail}.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (update)

### TDD Steps

1. Append to `tests/test_state_governance_audit.py` (add the import and this class):
   ```python
   from state_governance_audit import check_scope_non_expansion  # noqa: E402
   ```
   ```python
   class TestScopeNonExpansion:
       def test_widen_with_equal_epoch_passes(self):
           e1 = _event(event_id="s1", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 3, "approval_record": None},
                        scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
           e2 = _event(event_id="s2", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 3, "approval_record": "s1"},
                        scope={"repo": "omniscient/dark-factory", "issue": None, "pr": None, "agent_role": "refine"})
           verdict, violations = check_scope_non_expansion([e1, e2])
           assert verdict == "PASS"

       def test_widen_with_lower_epoch_fails(self):
           e1 = _event(event_id="s1", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 2, "approval_record": None},
                        scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
           e2 = _event(event_id="s2", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                        scope={"repo": "omniscient/markethawk", "issue": None, "pr": None, "agent_role": "refine"})
           verdict, violations = check_scope_non_expansion([e1, e2])
           assert verdict == "FAIL"
           assert violations[0]["event_id"] == "s2"

       def test_narrowing_never_violates(self):
           e1 = _event(event_id="s1", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                        scope={"repo": "omniscient/dark-factory", "issue": None, "pr": None, "agent_role": "refine"})
           e2 = _event(event_id="s2", entity_id="memory:y",
                        authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                        scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
           verdict, violations = check_scope_non_expansion([e1, e2])
           assert verdict == "PASS"
   ```

2. Run, confirm red:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k ScopeNonExpansion
   ```

3. Append to `scripts/state_governance_audit.py`:
   ```python
   def _scope_width(scope):
       """0 = narrow (issue- or pr-scoped), 1 = wide (repo-wide, no issue/pr)."""
       scope = scope or {}
       if scope.get("issue") or scope.get("pr"):
           return 0
       return 1


   def check_scope_non_expansion(events):
       """Across events sharing an entity_id (in file order), scope must not widen without a
       new authorizing event of equal or higher permission_epoch than the prior event."""
       violations = []
       for entity_id, group in _group_by_entity(events).items():
           if entity_id is None:
               continue
           prev = None
           for e in group:
               width = _scope_width(e.get("scope"))
               epoch = (e.get("authority") or {}).get("permission_epoch")
               if prev is not None:
                   prev_width, prev_epoch = prev
                   if width > prev_width and (
                       epoch is None or prev_epoch is None or epoch < prev_epoch
                   ):
                       violations.append({
                           "event_id": e.get("event_id"),
                           "entity_id": entity_id,
                           "reason": (
                               f"scope widened (width {prev_width} -> {width}) without an "
                               f"authorizing event of equal or higher permission_epoch "
                               f"(prev={prev_epoch}, this={epoch})"
                           ),
                       })
               prev = (width, epoch)
       verdict = "FAIL" if violations else "PASS"
       return verdict, violations
   ```

4. Run again, confirm green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k ScopeNonExpansion
   ```

5. Add the fixtures. `evals/state-governance/fixtures/scope-non-expansion-pass.jsonl`:
   ```
   {"event_id": "evt-sne-1", "idempotency_key": "issue-190:evt-sne-1", "operation": "write", "state_type": "memory", "entity_id": "memory:sne-pass-01", "authority": {"actor": "refine", "permission_epoch": 2, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "refine"}, "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-sne-pass-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-sne-2", "idempotency_key": "issue-190:evt-sne-2", "operation": "share", "state_type": "memory", "entity_id": "memory:sne-pass-01", "authority": {"actor": "human", "permission_epoch": 3, "approval_record": "evt-sne-1"}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "refine"}, "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-sne-pass-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```
   `evals/state-governance/fixtures/scope-non-expansion-fail.jsonl` — grounded in the real
   `memory_write.py:95` defect (`_write_index` hardcodes `"project": "markethawk"`
   regardless of the actual target repo, spec §Fixture corpus):
   ```
   {"event_id": "evt-sne-3", "idempotency_key": "issue-190:evt-sne-3", "operation": "write", "state_type": "memory", "entity_id": "memory:sne-fail-01", "authority": {"actor": "refine", "permission_epoch": 2, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "refine"}, "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-sne-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-sne-4", "idempotency_key": "issue-190:evt-sne-4", "operation": "write", "state_type": "memory", "entity_id": "memory:sne-fail-01", "authority": {"actor": "refine", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/markethawk", "issue": null, "pr": null, "agent_role": "refine"}, "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-sne-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```

6. Update `evals/state-governance/fixtures/manifest.json`:
   ```json
   {
     "authority-monotonicity-pass.jsonl": {"check": "authority_monotonicity", "expected_verdict": "PASS"},
     "authority-monotonicity-fail.jsonl": {"check": "authority_monotonicity", "expected_verdict": "FAIL"},
     "scope-non-expansion-pass.jsonl": {"check": "scope_non_expansion", "expected_verdict": "PASS"},
     "scope-non-expansion-fail.jsonl": {"check": "scope_non_expansion", "expected_verdict": "FAIL"}
   }
   ```

7. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py evals/state-governance/fixtures/scope-non-expansion-pass.jsonl evals/state-governance/fixtures/scope-non-expansion-fail.jsonl evals/state-governance/fixtures/manifest.json
   git commit -m "feat(state-governance): add scope_non_expansion check + fixtures (#190)"
   ```

---

## Task 4 — Check 3: deletion propagation

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (append class),
`evals/state-governance/fixtures/deletion-propagation-{pass,fail}.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (update)

### TDD Steps

1. Append to `tests/test_state_governance_audit.py`:
   ```python
   from state_governance_audit import check_deletion_propagation  # noqa: E402
   ```
   ```python
   class TestDeletionPropagation:
       def test_act_after_tombstone_reflecting_status_passes(self):
           e1 = _event(event_id="d1", entity_id="memory:z", operation="tombstone",
                        mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
           e2 = _event(event_id="d2", entity_id="memory:z", operation="act",
                        mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
           verdict, violations = check_deletion_propagation([e1, e2])
           assert verdict == "PASS"

       def test_act_after_tombstone_ignoring_status_fails(self):
           e1 = _event(event_id="d1", entity_id="memory:z", operation="tombstone",
                        mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
           e2 = _event(event_id="d2", entity_id="memory:z", operation="act",
                        mutability={"status": "active", "supersedes": [], "conflicts_with": []})
           verdict, violations = check_deletion_propagation([e1, e2])
           assert verdict == "FAIL"
           assert violations[0]["event_id"] == "d2"

       def test_act_before_any_tombstone_is_fine(self):
           e1 = _event(event_id="d1", entity_id="memory:z", operation="act",
                        mutability={"status": "active", "supersedes": [], "conflicts_with": []})
           verdict, violations = check_deletion_propagation([e1])
           assert verdict == "PASS"
   ```

2. Run, confirm red:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k DeletionPropagation
   ```

3. Append to `scripts/state_governance_audit.py`:
   ```python
   def check_deletion_propagation(events):
       """Every tombstone/delete/quarantine event must be reflected in any later 'act' event
       reading the same entity_id; an 'act' event that still reports mutability.status=active
       after a tombstone ignores it."""
       violations = []
       for entity_id, group in _group_by_entity(events).items():
           if entity_id is None:
               continue
           tombstoned = False
           for e in group:
               op = e.get("operation")
               if op in _TOMBSTONE_OPS:
                   tombstoned = True
                   continue
               if op == "act" and tombstoned:
                   status = (e.get("mutability") or {}).get("status")
                   if status in _ACTIVE_STATUSES:
                       violations.append({
                           "event_id": e.get("event_id"),
                           "entity_id": entity_id,
                           "reason": (
                               "act event reads entity as mutability.status=active after a "
                               "tombstone/delete/quarantine event"
                           ),
                       })
       verdict = "FAIL" if violations else "PASS"
       return verdict, violations
   ```

4. Run again, confirm green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k DeletionPropagation
   ```

5. Add the fixtures. `evals/state-governance/fixtures/deletion-propagation-pass.jsonl`:
   ```
   {"event_id": "evt-dp-1", "idempotency_key": "issue-190:evt-dp-1", "operation": "tombstone", "state_type": "memory", "entity_id": "memory:dp-pass-01", "authority": {"actor": "memory_maintain", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_maintain.py", "trust_tier": "reviewed", "run_id": "run-dp-pass-1", "commit": null}, "mutability": {"status": "tombstoned", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-dp-2", "idempotency_key": "issue-190:evt-dp-2", "operation": "act", "state_type": "memory", "entity_id": "memory:dp-pass-01", "authority": {"actor": "memory_retrieve", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_retrieve.py", "trust_tier": "reviewed", "run_id": "run-dp-pass-1", "commit": null}, "mutability": {"status": "tombstoned", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```
   `evals/state-governance/fixtures/deletion-propagation-fail.jsonl` — grounded in the real
   `memory_maintain.py` defect (expiry/dedup/promote rewrite the markdown files but never
   touch `index.jsonl`, spec §Fixture corpus):
   ```
   {"event_id": "evt-dp-3", "idempotency_key": "issue-190:evt-dp-3", "operation": "tombstone", "state_type": "memory", "entity_id": "memory:dp-fail-01", "authority": {"actor": "memory_maintain", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_maintain.py", "trust_tier": "reviewed", "run_id": "run-dp-fail-1", "commit": null}, "mutability": {"status": "tombstoned", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-dp-4", "idempotency_key": "issue-190:evt-dp-4", "operation": "act", "state_type": "memory", "entity_id": "memory:dp-fail-01", "authority": {"actor": "memory_retrieve", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_retrieve.py", "trust_tier": "reviewed", "run_id": "run-dp-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```

6. Update `evals/state-governance/fixtures/manifest.json` (add two entries to the existing object):
   ```json
   "deletion-propagation-pass.jsonl": {"check": "deletion_propagation", "expected_verdict": "PASS"},
   "deletion-propagation-fail.jsonl": {"check": "deletion_propagation", "expected_verdict": "FAIL"}
   ```

7. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py evals/state-governance/fixtures/deletion-propagation-pass.jsonl evals/state-governance/fixtures/deletion-propagation-fail.jsonl evals/state-governance/fixtures/manifest.json
   git commit -m "feat(state-governance): add deletion_propagation check + fixtures (#190)"
   ```

---

## Task 5 — Check 4: provenance preservation

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (append class),
`evals/state-governance/fixtures/provenance-preservation-{pass,fail}.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (update)

### TDD Steps

1. Append to `tests/test_state_governance_audit.py`:
   ```python
   from state_governance_audit import check_provenance_preservation  # noqa: E402
   ```
   ```python
   class TestProvenancePreservation:
       def test_evidence_actionability_is_exempt(self):
           e1 = _event(event_id="p1", actionability="evidence",
                        provenance={"source": None, "trust_tier": None, "run_id": None, "commit": None})
           verdict, violations = check_provenance_preservation([e1])
           assert verdict == "PASS"

       def test_advisory_missing_source_fails(self):
           e1 = _event(event_id="p1", actionability="advisory",
                        provenance={"source": None, "trust_tier": None, "run_id": None, "commit": None})
           verdict, violations = check_provenance_preservation([e1])
           assert verdict == "FAIL"

       def test_policy_missing_trust_tier_fails(self):
           e1 = _event(event_id="p1", actionability="policy",
                        provenance={"source": "git", "trust_tier": "untrusted", "run_id": None, "commit": "abc"})
           verdict, violations = check_provenance_preservation([e1])
           assert verdict == "FAIL"

       def test_policy_with_reviewed_trust_tier_passes(self):
           e1 = _event(event_id="p1", actionability="policy",
                        provenance={"source": "git", "trust_tier": "reviewed", "run_id": None, "commit": "abc"})
           verdict, violations = check_provenance_preservation([e1])
           assert verdict == "PASS"
   ```

2. Run, confirm red:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k ProvenancePreservation
   ```

3. Append to `scripts/state_governance_audit.py`:
   ```python
   def check_provenance_preservation(events):
       """Every event with actionability != evidence must carry a non-null provenance.source
       and (run_id or commit); policy/permission/skill/external_commitment additionally need
       trust_tier in {trusted, reviewed}."""
       violations = []
       for e in events:
           actionability = e.get("actionability")
           if actionability is None or actionability == "evidence":
               continue
           prov = e.get("provenance") or {}
           source = prov.get("source")
           has_join_key = bool(prov.get("run_id") or prov.get("commit"))
           if not source or not has_join_key:
               violations.append({
                   "event_id": e.get("event_id"),
                   "entity_id": e.get("entity_id"),
                   "reason": "missing provenance.source or both run_id/commit join keys",
               })
               continue
           if actionability in _HIGH_ACTIONABILITY:
               trust_tier = prov.get("trust_tier")
               if trust_tier not in ("trusted", "reviewed"):
                   violations.append({
                       "event_id": e.get("event_id"),
                       "entity_id": e.get("entity_id"),
                       "reason": (
                           f"actionability={actionability} requires provenance.trust_tier "
                           f"trusted|reviewed, got {trust_tier!r}"
                       ),
                   })
       verdict = "FAIL" if violations else "PASS"
       return verdict, violations
   ```

4. Run again, confirm green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k ProvenancePreservation
   ```

5. Add the fixtures. `evals/state-governance/fixtures/provenance-preservation-pass.jsonl`:
   ```
   {"event_id": "evt-pp-1", "idempotency_key": "issue-190:evt-pp-1", "operation": "update", "state_type": "skill", "entity_id": "skill:conformance-rubric", "authority": {"actor": "human", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": 330, "agent_role": "human"}, "provenance": {"source": "git", "trust_tier": "reviewed", "run_id": null, "commit": "9f1c3ab"}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": "git-revert-9f1c3ab", "external_effects": []}, "actionability": "policy"}
   ```
   `evals/state-governance/fixtures/provenance-preservation-fail.jsonl` — grounded in the
   real `.archon/memory/index.jsonl` defect (`_write_index` omits `id`/`source_file`/
   `path_prefixes`, so `memory_retrieve.scan_index` silently `continue`s past every row it
   writes — provenance recorded but not retrievable, spec §Fixture corpus):
   ```
   {"event_id": "evt-pp-2", "idempotency_key": "issue-190:evt-pp-2", "operation": "write", "state_type": "memory", "entity_id": "memory:pp-fail-01", "authority": {"actor": "refine", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "refine"}, "provenance": {"source": null, "trust_tier": null, "run_id": null, "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```

6. Update `evals/state-governance/fixtures/manifest.json` (add two entries):
   ```json
   "provenance-preservation-pass.jsonl": {"check": "provenance_preservation", "expected_verdict": "PASS"},
   "provenance-preservation-fail.jsonl": {"check": "provenance_preservation", "expected_verdict": "FAIL"}
   ```

7. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py evals/state-governance/fixtures/provenance-preservation-pass.jsonl evals/state-governance/fixtures/provenance-preservation-fail.jsonl evals/state-governance/fixtures/manifest.json
   git commit -m "feat(state-governance): add provenance_preservation check + fixtures (#190)"
   ```

---

## Task 6 — Check 5: rollback traceability

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (append class),
`evals/state-governance/fixtures/rollback-traceability-{pass,fail}.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (update)

### TDD Steps

1. Append to `tests/test_state_governance_audit.py`:
   ```python
   from state_governance_audit import check_rollback_traceability  # noqa: E402
   ```
   ```python
   class TestRollbackTraceability:
       def test_advisory_actionability_is_exempt(self):
           e1 = _event(event_id="r1", actionability="advisory",
                        recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
           verdict, violations = check_rollback_traceability([e1])
           assert verdict == "PASS"

       def test_external_commitment_without_handle_fails(self):
           e1 = _event(event_id="r1", actionability="external_commitment",
                        recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
           verdict, violations = check_rollback_traceability([e1])
           assert verdict == "FAIL"

       def test_external_commitment_with_transaction_id_passes(self):
           e1 = _event(event_id="r1", actionability="external_commitment",
                        recoverability={"transaction_id": "txn-1", "rollback_handle": None, "external_effects": []})
           verdict, violations = check_rollback_traceability([e1])
           assert verdict == "PASS"
   ```

2. Run, confirm red:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k RollbackTraceability
   ```

3. Append to `scripts/state_governance_audit.py`:
   ```python
   def check_rollback_traceability(events):
       """Every event with actionability in {policy, permission, skill, external_commitment}
       must carry a non-null recoverability.rollback_handle or transaction_id."""
       violations = []
       for e in events:
           actionability = e.get("actionability")
           if actionability not in _HIGH_ACTIONABILITY:
               continue
           rec = e.get("recoverability") or {}
           if not rec.get("rollback_handle") and not rec.get("transaction_id"):
               violations.append({
                   "event_id": e.get("event_id"),
                   "entity_id": e.get("entity_id"),
                   "reason": (
                       f"actionability={actionability} has no recoverability.rollback_handle "
                       f"or transaction_id"
                   ),
               })
       verdict = "FAIL" if violations else "PASS"
       return verdict, violations
   ```

4. Run again, confirm green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k RollbackTraceability
   ```

5. Add the fixtures. `evals/state-governance/fixtures/rollback-traceability-pass.jsonl`:
   ```
   {"event_id": "evt-rt-1", "idempotency_key": "issue-190:evt-rt-1", "operation": "act", "state_type": "external_commitment", "entity_id": "external_commitment:pr-330", "authority": {"actor": "entrypoint", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 292, "pr": 330, "agent_role": "implement"}, "provenance": {"source": "entrypoint.sh", "trust_tier": "trusted", "run_id": "run-rt-pass-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": "revert-pr-330", "external_effects": ["comment"]}, "actionability": "external_commitment"}
   ```
   `evals/state-governance/fixtures/rollback-traceability-fail.jsonl` — anchored to the
   #305 retrospective (spec §Retrospective: a lossy transform destroyed the traceable
   input, so nothing downstream could reconstruct/undo it):
   ```
   {"event_id": "evt-rt-2", "idempotency_key": "issue-190:evt-rt-2", "operation": "act", "state_type": "external_commitment", "entity_id": "external_commitment:pr-999", "authority": {"actor": "entrypoint", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 305, "pr": 999, "agent_role": "implement"}, "provenance": {"source": "entrypoint.sh", "trust_tier": "trusted", "run_id": "run-rt-fail-1", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": ["comment"]}, "actionability": "external_commitment"}
   ```

6. Update `evals/state-governance/fixtures/manifest.json` (add two entries — the manifest
   now has 10 total keys, one pass/fail pair per check):
   ```json
   "rollback-traceability-pass.jsonl": {"check": "rollback_traceability", "expected_verdict": "PASS"},
   "rollback-traceability-fail.jsonl": {"check": "rollback_traceability", "expected_verdict": "FAIL"}
   ```
   The full `manifest.json` at the end of this task:
   ```json
   {
     "authority-monotonicity-pass.jsonl": {"check": "authority_monotonicity", "expected_verdict": "PASS"},
     "authority-monotonicity-fail.jsonl": {"check": "authority_monotonicity", "expected_verdict": "FAIL"},
     "scope-non-expansion-pass.jsonl": {"check": "scope_non_expansion", "expected_verdict": "PASS"},
     "scope-non-expansion-fail.jsonl": {"check": "scope_non_expansion", "expected_verdict": "FAIL"},
     "deletion-propagation-pass.jsonl": {"check": "deletion_propagation", "expected_verdict": "PASS"},
     "deletion-propagation-fail.jsonl": {"check": "deletion_propagation", "expected_verdict": "FAIL"},
     "provenance-preservation-pass.jsonl": {"check": "provenance_preservation", "expected_verdict": "PASS"},
     "provenance-preservation-fail.jsonl": {"check": "provenance_preservation", "expected_verdict": "FAIL"},
     "rollback-traceability-pass.jsonl": {"check": "rollback_traceability", "expected_verdict": "PASS"},
     "rollback-traceability-fail.jsonl": {"check": "rollback_traceability", "expected_verdict": "FAIL"}
   }
   ```

7. Run the full corpus test. `TestManifest::test_manifest_file_exists`,
   `::test_manifest_covers_every_fixture_file`, and `::test_every_check_has_a_pass_and_fail_fixture`
   now pass; `TestManifest::test_combined_fixture_is_declared`,
   `test_corpus_has_exactly_11_files`, and `TestSampleArtifacts` still fail (the 11th
   combined fixture and the sample outputs land in Task 8):
   ```bash
   python -m pytest tests/test_state_governance_audit.py tests/test_state_governance_fixtures.py -v
   ```

8. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py evals/state-governance/fixtures/rollback-traceability-pass.jsonl evals/state-governance/fixtures/rollback-traceability-fail.jsonl evals/state-governance/fixtures/manifest.json
   git commit -m "feat(state-governance): add rollback_traceability check + fixtures (#190)"
   ```

---

## Task 7 — Scorecard aggregation, report writers, and CLI

**Files:** `scripts/state_governance_audit.py` (append), `tests/test_state_governance_audit.py` (append classes)

### TDD Steps

1. Append to `tests/test_state_governance_audit.py`:
   ```python
   import json  # add to the top-level imports

   from state_governance_audit import (  # noqa: E402
       CHECK_FUNCS,
       CHECK_NAMES,
       compute_scorecard,
       load_events,
   )

   _REPO_ROOT = Path(__file__).resolve().parents[1]
   _FIXTURES_DIR = _REPO_ROOT / "evals" / "state-governance" / "fixtures"
   _MANIFEST = _FIXTURES_DIR / "manifest.json"
   ```
   ```python
   class TestComputeScorecard:
       def test_all_pass_scores_100(self):
           e1 = _event(event_id="e1", actionability="evidence")
           scorecard = compute_scorecard([e1], now="2026-01-01T00:00:00Z", run_id="t")
           assert scorecard["STATUS"] == "PASS"
           assert scorecard["score"] == 100
           assert [c["name"] for c in scorecard["checks"]] == CHECK_NAMES

       def test_one_failing_check_drops_score_and_status(self):
           e1 = _event(event_id="e1", actionability="external_commitment",
                        recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
           scorecard = compute_scorecard([e1], now="2026-01-01T00:00:00Z", run_id="t")
           assert scorecard["STATUS"] == "FAIL"
           assert scorecard["score"] == 80


   class TestManifestFixtures:
       def test_every_manifest_entry_matches_its_check_function(self):
           manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
           for fname, spec in manifest.items():
               if spec["check"] == "combined":
                   continue
               events = load_events(_FIXTURES_DIR / fname)
               verdict, _ = CHECK_FUNCS[spec["check"]](events)
               assert verdict == spec["expected_verdict"], (
                   f"{fname}: expected {spec['expected_verdict']}, got {verdict}"
               )
   ```

2. Run, confirm red (`compute_scorecard`/`CHECK_FUNCS` don't exist yet):
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v -k "ComputeScorecard or ManifestFixtures"
   ```

3. Append to `scripts/state_governance_audit.py` (`argparse`/`sys`/`datetime` are already
   imported at the top of the file from Task 1's skeleton):
   ```python
   CHECK_FUNCS = {
       "authority_monotonicity": check_authority_monotonicity,
       "scope_non_expansion": check_scope_non_expansion,
       "deletion_propagation": check_deletion_propagation,
       "provenance_preservation": check_provenance_preservation,
       "rollback_traceability": check_rollback_traceability,
   }


   def compute_scorecard(events, now, run_id):
       checks = []
       fail_count = 0
       for name in CHECK_NAMES:
           verdict, violations = CHECK_FUNCS[name](events)
           if verdict == "FAIL":
               fail_count += 1
           checks.append({"name": name, "verdict": verdict, "violations": violations})
       status = "FAIL" if fail_count else "PASS"
       score = round(100 * (len(CHECK_NAMES) - fail_count) / len(CHECK_NAMES))
       return {
           "STATUS": status,
           "generated_at": now,
           "run_id": run_id,
           "event_count": len(events),
           "checks": checks,
           "score": score,
       }


   def write_json(scorecard, out_path):
       out_path.write_text(json.dumps(scorecard, indent=2, sort_keys=False) + "\n", encoding="utf-8")


   def write_markdown(scorecard, out_path):
       lines = [
           "# Dark Factory State Governance Scorecard",
           "",
           f"Generated: {scorecard['generated_at']}",
           f"Run: {scorecard['run_id']}",
           "",
           f"**STATUS:** {scorecard['STATUS']} — **Score:** {scorecard['score']}/100 "
           f"— **Events evaluated:** {scorecard['event_count']}",
           "",
           "## Checks",
           "",
           "| Check | Verdict | Violations |",
           "|---|---|---|",
       ]
       for c in scorecard["checks"]:
           lines.append(f"| {c['name']} | {c['verdict']} | {len(c['violations'])} |")
       lines.append("")
       for c in scorecard["checks"]:
           if not c["violations"]:
               continue
           lines.append(f"### {c['name']} violations")
           lines.append("")
           for v in c["violations"]:
               lines.append(f"- `{v.get('entity_id')}` / `{v.get('event_id')}`: {v.get('reason')}")
           lines.append("")
       out_path.write_text("\n".join(lines), encoding="utf-8")


   def parse_args():
       p = argparse.ArgumentParser(description=__doc__)
       p.add_argument("--fixtures", required=True, help="Directory of *.jsonl fixtures, or a single .jsonl file")
       p.add_argument("--out-dir", required=True, help="Directory to write state-governance-scorecard.{json,md}")
       p.add_argument("--now", default="", help="Fixed generated_at timestamp (default: current UTC time)")
       p.add_argument("--run-id", default="", help="Fixed run_id for the scorecard (default: 'adhoc')")
       return p.parse_args()


   def main():
       args = parse_args()
       now = args.now
       if not now:
           now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
       run_id = args.run_id or "adhoc"

       events = load_events(args.fixtures)
       scorecard = compute_scorecard(events, now, run_id)

       out_dir = Path(args.out_dir)
       out_dir.mkdir(parents=True, exist_ok=True)
       write_json(scorecard, out_dir / "state-governance-scorecard.json")
       write_markdown(scorecard, out_dir / "state-governance-scorecard.md")

       print(f"STATUS: {scorecard['STATUS']}", file=sys.stderr)
       print(f"score: {scorecard['score']}/100 over {len(CHECK_NAMES)} checks", file=sys.stderr)


   if __name__ == "__main__":
       main()
   ```

4. Run again, confirm green (all 10 manifest fixtures now cross-checked against their real
   check function):
   ```bash
   python -m pytest tests/test_state_governance_audit.py -v
   ```

5. Commit:
   ```bash
   git add scripts/state_governance_audit.py tests/test_state_governance_audit.py
   git commit -m "feat(state-governance): add scorecard aggregation, report writers, and CLI (#190)"
   ```

---

## Task 8 — Combined fixture, committed sample scorecard, byte-stable regeneration test

**Files:** `evals/state-governance/fixtures/realistic-run-01.jsonl` (new),
`evals/state-governance/fixtures/manifest.json` (update),
`evals/state-governance/sample/state-governance-scorecard.json` (new),
`evals/state-governance/sample/state-governance-scorecard.md` (new),
`tests/test_state_governance_audit.py` (append class)

### Steps

1. Add `evals/state-governance/fixtures/realistic-run-01.jsonl` — one issue lifecycle
   (write → validate → act, clean on all 5 checks) plus one memory lifecycle reproducing
   the real `memory_maintain.py` → `index.jsonl` deletion-propagation gap (write →
   tombstone → act with a stale `active` status), so this fixture deliberately fails
   exactly 1 of the 5 checks — a realistic, non-trivial mixed result rather than an
   all-green or all-red toy case:
   ```
   {"event_id": "evt-real-a", "idempotency_key": "issue-190:evt-real-a", "operation": "write", "state_type": "project_status", "entity_id": "issue:190", "authority": {"actor": "scheduler", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-realistic-01", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   {"event_id": "evt-real-b", "idempotency_key": "issue-190:evt-real-b", "operation": "validate", "state_type": "project_status", "entity_id": "issue:190", "authority": {"actor": "scheduler", "permission_epoch": 1, "approval_record": "evt-real-a"}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "reviewed", "run_id": null, "commit": "b3dd920"}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": "revert-label-190", "external_effects": []}, "actionability": "policy"}
   {"event_id": "evt-real-c", "idempotency_key": "issue-190:evt-real-c", "operation": "act", "state_type": "project_status", "entity_id": "issue:190", "authority": {"actor": "scheduler", "permission_epoch": 1, "approval_record": "evt-real-b"}, "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": null, "agent_role": "scheduler"}, "provenance": {"source": "github", "trust_tier": "trusted", "run_id": "run-realistic-01", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": "txn-real-c", "rollback_handle": null, "external_effects": []}, "actionability": "evidence"}
   {"event_id": "evt-real-d", "idempotency_key": "issue-190:evt-real-d", "operation": "write", "state_type": "memory", "entity_id": "memory:index-defect", "authority": {"actor": "refine", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "refine"}, "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-realistic-01", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-real-e", "idempotency_key": "issue-190:evt-real-e", "operation": "tombstone", "state_type": "memory", "entity_id": "memory:index-defect", "authority": {"actor": "memory_maintain", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_maintain.py", "trust_tier": "reviewed", "run_id": null, "commit": "b3dd920"}, "mutability": {"status": "tombstoned", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   {"event_id": "evt-real-f", "idempotency_key": "issue-190:evt-real-f", "operation": "act", "state_type": "memory", "entity_id": "memory:index-defect", "authority": {"actor": "memory_retrieve", "permission_epoch": 1, "approval_record": null}, "scope": {"repo": "omniscient/dark-factory", "issue": null, "pr": null, "agent_role": "implement"}, "provenance": {"source": "memory_retrieve.py", "trust_tier": "reviewed", "run_id": "run-realistic-01", "commit": null}, "mutability": {"status": "active", "supersedes": [], "conflicts_with": []}, "recoverability": {"transaction_id": null, "rollback_handle": null, "external_effects": []}, "actionability": "advisory"}
   ```

2. Add the final entry to `evals/state-governance/fixtures/manifest.json` (11 keys total):
   ```json
   "realistic-run-01.jsonl": {"check": "combined", "expected_verdict": "FAIL"}
   ```

3. Generate the committed sample by actually running the script (fixed `--now`/`--run-id`
   for byte-stable output, matching `eval_memory_quality.py --timestamp`):
   ```bash
   mkdir -p evals/state-governance/sample
   python3 scripts/state_governance_audit.py \
     --fixtures evals/state-governance/fixtures/realistic-run-01.jsonl \
     --out-dir evals/state-governance/sample \
     --now 2026-08-22T00:00:00Z \
     --run-id state-governance-sample-v1
   ```
   Expected stderr: `STATUS: FAIL` / `score: 80/100 over 5 checks`. This produces
   `evals/state-governance/sample/state-governance-scorecard.json`:
   ```json
   {
     "STATUS": "FAIL",
     "generated_at": "2026-08-22T00:00:00Z",
     "run_id": "state-governance-sample-v1",
     "event_count": 6,
     "checks": [
       {
         "name": "authority_monotonicity",
         "verdict": "PASS",
         "violations": []
       },
       {
         "name": "scope_non_expansion",
         "verdict": "PASS",
         "violations": []
       },
       {
         "name": "deletion_propagation",
         "verdict": "FAIL",
         "violations": [
           {
             "event_id": "evt-real-f",
             "entity_id": "memory:index-defect",
             "reason": "act event reads entity as mutability.status=active after a tombstone/delete/quarantine event"
           }
         ]
       },
       {
         "name": "provenance_preservation",
         "verdict": "PASS",
         "violations": []
       },
       {
         "name": "rollback_traceability",
         "verdict": "PASS",
         "violations": []
       }
     ],
     "score": 80
   }
   ```
   and `evals/state-governance/sample/state-governance-scorecard.md`:
   ```markdown
   # Dark Factory State Governance Scorecard

   Generated: 2026-08-22T00:00:00Z
   Run: state-governance-sample-v1

   **STATUS:** FAIL — **Score:** 80/100 — **Events evaluated:** 6

   ## Checks

   | Check | Verdict | Violations |
   |---|---|---|
   | authority_monotonicity | PASS | 0 |
   | scope_non_expansion | PASS | 0 |
   | deletion_propagation | FAIL | 1 |
   | provenance_preservation | PASS | 0 |
   | rollback_traceability | PASS | 0 |

   ### deletion_propagation violations

   - `memory:index-defect` / `evt-real-f`: act event reads entity as mutability.status=active after a tombstone/delete/quarantine event
   ```
   These two generated files are the ones to `git add` — do not hand-transcribe them,
   use the script's actual output so a future regeneration is guaranteed to match.

4. Append the regeneration test to `tests/test_state_governance_audit.py`:
   ```python
   from state_governance_audit import write_json, write_markdown  # noqa: E402

   _SAMPLE_DIR = _REPO_ROOT / "evals" / "state-governance" / "sample"
   ```
   ```python
   class TestSampleRegeneration:
       def test_sample_matches_fresh_regeneration(self):
           events = load_events(_FIXTURES_DIR / "realistic-run-01.jsonl")
           scorecard = compute_scorecard(events, now="2026-08-22T00:00:00Z", run_id="state-governance-sample-v1")

           import tempfile
           with tempfile.TemporaryDirectory() as tmp:
               tmp_path = Path(tmp)
               write_json(scorecard, tmp_path / "state-governance-scorecard.json")
               write_markdown(scorecard, tmp_path / "state-governance-scorecard.md")

               fresh_json = (tmp_path / "state-governance-scorecard.json").read_text(encoding="utf-8")
               fresh_md = (tmp_path / "state-governance-scorecard.md").read_text(encoding="utf-8")

           committed_json = (_SAMPLE_DIR / "state-governance-scorecard.json").read_text(encoding="utf-8")
           committed_md = (_SAMPLE_DIR / "state-governance-scorecard.md").read_text(encoding="utf-8")

           assert fresh_json == committed_json
           assert fresh_md == committed_md
   ```

5. Run the full pair of test files — everything should now be green:
   ```bash
   python -m pytest tests/test_state_governance_audit.py tests/test_state_governance_fixtures.py -v
   ```
   Expected: all tests pass, including `test_corpus_has_exactly_11_files` and
   `TestSampleArtifacts`.

6. Commit:
   ```bash
   git add evals/state-governance/fixtures/realistic-run-01.jsonl evals/state-governance/fixtures/manifest.json evals/state-governance/sample/state-governance-scorecard.json evals/state-governance/sample/state-governance-scorecard.md tests/test_state_governance_audit.py
   git commit -m "feat(state-governance): add combined fixture, committed sample scorecard, regeneration test (#190)"
   ```

---

## Task 9 — Full-suite verification

**Files:** none (verification only)

1. Run the full test suite exactly as CI does:
   ```bash
   python -m pytest tests/ -v
   ```
   Confirm no regressions outside the two new `test_state_governance_*.py` files and that
   both are fully green.

2. Run the CI jobs relevant to this change as `.github/workflows/ci.yml` does (this ticket
   touches no workflow/DAG file, but CI runs these alongside pytest). Use
   `tests/test_smoke_gate.sh`, not the bare `smoke_gate.sh` — the bare script is the live,
   side-effecting production gate (it runs real `gh issue`/`tracker` calls and mutates
   scheduler/breaker state on disk when invoked directly); the test wrapper sources it
   under the `SMOKE_GATE_SOURCE_ONLY=1` guard CI actually exercises:
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   bash tests/test_smoke_gate.sh
   ```

3. Confirm determinism holds outside the test harness too — re-run the CLI a second time
   into a scratch directory and diff against the committed sample:
   ```bash
   python3 scripts/state_governance_audit.py \
     --fixtures evals/state-governance/fixtures/realistic-run-01.jsonl \
     --out-dir /tmp/state-governance-verify \
     --now 2026-08-22T00:00:00Z \
     --run-id state-governance-sample-v1
   diff /tmp/state-governance-verify/state-governance-scorecard.json evals/state-governance/sample/state-governance-scorecard.json
   diff /tmp/state-governance-verify/state-governance-scorecard.md evals/state-governance/sample/state-governance-scorecard.md
   ```
   Expected: both `diff` commands produce no output.

4. Confirm the final tree only contains the planned files. Use the two-dot form — memory
  (Three-dot per memory `[PATTERN]` #266 — set detection with two-dot flags files `main` changed independently after the fork, the exact deletion incident on the #251 branch. #250's two-dot rule covers single-file content equality only.)
   independently after this branch forked, producing false-positive OOS hits:
   ```bash
   git diff --name-only origin/main...HEAD
   ```
   Expected: `docs/superpowers/plans/2026-08-22-state-governance-scorecard.md`,
   `docs/superpowers/specs/2026-08-21-state-governance-scorecard-design.md`,
   `scripts/state_governance_audit.py`, `evals/state-governance/fixtures/*.jsonl` (11
   files), `evals/state-governance/fixtures/manifest.json`,
   `evals/state-governance/sample/state-governance-scorecard.{json,md}`,
   `tests/test_state_governance_audit.py`, `tests/test_state_governance_fixtures.py` —
   nothing else. No changes to `config/config.yaml`, `entrypoint.sh`,
   `workflows/archon-dark-factory.yaml`, or `deploy/**`.

5. No further commit needed if step 4 is clean; if any stray file appears, remove it and
   commit the removal before moving on.

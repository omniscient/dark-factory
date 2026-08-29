# Implementation Plan: Verifier abstraction (A3) — shared verdict contract and target-registered check-only verifiers

**Issue:** omniscient/dark-factory#197
**Spec:** `docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md`
**Depends on:** #301 (A1.5 loop-schema restructuring) for Task 8 only — see that task's
gating note. Every other task builds against `main` as it exists today.

---

## Goal

Extract the verdict schema (`STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY`) implied today by
`gate_lib.sh::emit_verdict` and `run_record.py::_parse_artifact_stage`'s four hand-rolled
branches into one documented, byte-compatible Python module (`verdict.py`), then build a
target-registered check-only verifier resolver (`verifier.py`) on top of it — modeled on
`hooks.sh::run_hook`'s target-over-default, check-only, factory-owns-side-effects
precedent — so a future loop-schema entry's `verification.verifier` can plug into the same
fail-closed gating machinery (`verdict_gate_check.sh`) the factory's own gates already
trust. No behavior change to the four existing maker/checker pairs; no new DAG node.

## Architecture

```
scripts/factory_core/verdict.py            (new — canonical schema, pure functions)
  parse_verdict(content) -> dict | None
  format_verdict(gate_type, status, findings_count, severity) -> str
  GATING_PASS_STATUSES / GATING_BLOCK_STATUSES / LEGACY_STATUSES / SEVERITY_LEVELS
        │ delegated to by
        ▼
scripts/factory_core/run_record.py::_parse_artifact_stage
  (thin per-name wrapper: verdict.parse_verdict() for STATUS, then each name's own
   CYCLES/BLOCKERS/ADVISORY/CONFLICT_VERDICT= overlay — unchanged detail extraction)

scripts/factory_core/verifier.py           (new — target-verifier resolver)
  resolve_verifier(clone_dir, verifier_path) -> resolved path
  run_verifier(resolved_path, env, timeout) -> (exit_code, stdout)   [fails closed via VerifierError]
  normalize_verdict(exit_code, stdout, gate_type) -> verdict text    (structured | bare-exit-code)
  assert_verifier_independent(loop_entry)                            (path-disjointness rule)
  resolve_and_run(...) -> verdict text                                (end-to-end primitive)
  CLI: python3 -m factory_core.verifier --clone-dir . --loop-name X --verifier-path Y --side-effect-level N run --out <path>
        │
        │ verdict text written to a caller-supplied artifact path
        ▼
scripts/verdict_gate_check.sh              (unmodified — already fail-closed on BLOCKED/missing)

scripts/factory_core/adapter.py::load()
  ... existing _validate_loop(entry, i) ...
  + verifier.assert_verifier_independent(entry)   (Task 8, gated on #301 landing)
```

## Tech Stack

- Python stdlib only for `verdict.py`/`verifier.py` (`subprocess`, `os`, `argparse`) —
  matches every other `scripts/factory_core/*.py` module; no new dependency.
- `pytest` for `tests/test_verdict.py`, `tests/test_verifier.py`, additive
  `tests/test_run_record.py`/`tests/test_adapter.py` cases — existing framework.
- Bash for the integration test appended to `tests/test_verdict_gate_check.sh` — matches
  that file's existing stubbed-`python3`-function convention.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/verdict.py` | **New** — canonical verdict schema |
| `tests/test_verdict.py` | **New** — schema unit tests + golden-corpus byte-compat test |
| `tests/fixtures/verdicts/*.md` + `*.expected.json` | **New** — golden corpus (Requirement 9) |
| `scripts/factory_core/run_record.py` | **Modified** — `_parse_artifact_stage` delegates to `verdict.parse_verdict` |
| `tests/test_run_record.py` | **Modified** — one additive round-trip test; all existing tests unmodified |
| `scripts/factory_core/verifier.py` | **New** — verifier resolver/runner/independence-check/CLI |
| `tests/fixtures/verifiers/*.sh` | **New** — executable fixture verifier scripts |
| `tests/test_verifier.py` | **New** — verifier resolver/runner/independence/CLI tests |
| `refinement-skills/VERIFIER-CONTRACT.md` | **New** — shared checker-invocation + verifier-registration contract |
| `refinement-skills/SKILL.md` | **Modified** — link to the new contract doc |
| `tests/test_verifier_contract_doc_referenced.py` | **New** — asserts all four command files + `gate_lib.sh` reference the contract doc |
| `commands/dark-factory-refine.md` | **Modified** — pin sentence → contract-doc reference |
| `commands/dark-factory-plan.md` | **Modified** — both pin sites → contract-doc reference |
| `commands/dark-factory-conformance.md` | **Modified** — pin sentence → contract-doc reference |
| `commands/dark-factory-code-review.md` | **Modified** — pin sentence → contract-doc reference |
| `scripts/gate_lib.sh` | **Modified** — header comment points at `verdict.py` |
| `tests/test_verdict_gate_check.sh` | **Modified** — appended integration test (Requirement 8c) |
| `scripts/factory_core/adapter.py` | **Modified** (Task 8, gated on #301) — additive `assert_verifier_independent` call |
| `tests/test_adapter.py` | **Modified** (Task 8, gated on #301) — additive independence cases |

Not touched (Requirement 9): `scripts/verdict_gate_check.sh`, `scripts/gate_blast_radius.py`,
`scripts/budget_gate.sh`, `scripts/push_gate_check.sh`, `scripts/oos_excise.sh`,
`config/config.yaml`, `workflows/*.yaml`.

---

## Task 1: `scripts/factory_core/verdict.py` — canonical schema

**Files:** `scripts/factory_core/verdict.py` (new), `tests/test_verdict.py` (new)

### TDD Steps

1. Write the failing test file `tests/test_verdict.py`:

```python
import pytest
from factory_core import verdict


def test_parse_basic_status_only():
    result = verdict.parse_verdict("STATUS: PASS\n")
    assert result == {"status": "PASS"}


def test_parse_full_four_line_shape():
    content = "STATUS: BLOCKED\nGATE_TYPE: conformance\nFINDINGS_COUNT: 2\nSEVERITY: critical\n"
    assert verdict.parse_verdict(content) == {
        "status": "BLOCKED", "gate_type": "conformance",
        "findings_count": 2, "severity": "critical",
    }


def test_parse_missing_status_returns_none():
    assert verdict.parse_verdict("no status line here\n") is None


def test_parse_empty_content_returns_none():
    assert verdict.parse_verdict("") is None
    assert verdict.parse_verdict("   \n") is None


def test_parse_never_raises_on_unknown_status():
    # HUMAN_REQUIRED (blast) and FAIL (validation) are documented legacy tokens —
    # returned verbatim, never rejected or normalized (Requirement 1).
    assert verdict.parse_verdict("STATUS: HUMAN_REQUIRED\n") == {"status": "HUMAN_REQUIRED"}
    assert verdict.parse_verdict("STATUS: something-a-target-loop-invented\n") == {
        "status": "something-a-target-loop-invented"
    }


def test_parse_malformed_findings_count_is_skipped_not_raised():
    result = verdict.parse_verdict("STATUS: PASS\nFINDINGS_COUNT: not-a-number\n")
    assert result == {"status": "PASS"}  # findings_count silently absent, no raise


def test_format_verdict_matches_gate_lib_emit_verdict_shape():
    text = verdict.format_verdict("code-review", "PASS", 0, "none")
    assert text == "STATUS: PASS\nGATE_TYPE: code-review\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_format_then_parse_roundtrips_an_invented_gate_type():
    text = verdict.format_verdict("loop:nightly-scan-triage", "BLOCKED", 1, "high")
    assert verdict.parse_verdict(text) == {
        "status": "BLOCKED", "gate_type": "loop:nightly-scan-triage",
        "findings_count": 1, "severity": "high",
    }


def test_documented_constants():
    assert verdict.GATING_PASS_STATUSES == {"PASS", "SKIPPED", "ERROR"}
    assert verdict.GATING_BLOCK_STATUSES == {"BLOCKED"}
    assert verdict.LEGACY_STATUSES == {"HUMAN_REQUIRED", "FAIL"}
    assert verdict.SEVERITY_LEVELS == ("none", "low", "medium", "high", "critical")
```

2. Verify it fails (module doesn't exist yet):
   ```bash
   cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_verdict.py -x -q
   ```
   Expected: `ModuleNotFoundError: No module named 'factory_core.verdict'` (or collection error).

3. Implement `scripts/factory_core/verdict.py`:

```python
"""Canonical verdict schema: STATUS / GATE_TYPE / FINDINGS_COUNT / SEVERITY.

Single documented source of truth for scripts/gate_lib.sh::emit_verdict (bash) and
every Python verdict writer/reader (run_record.py, verifier.py). See
refinement-skills/VERIFIER-CONTRACT.md for the non-Python-reading restatement.

STATUS is a free token (never validated/rejected/normalized by parse_verdict).
Its *gating* values, per scripts/verdict_gate_check.sh, are PASS/SKIPPED/ERROR
(proceed) and BLOCKED (block). HUMAN_REQUIRED (gate_blast_radius.py) and FAIL
(dark-factory-validate.md's prose) are documented legacy tokens outside that gate's
enum but still parsed verbatim by every reader in this repo.
"""

GATING_PASS_STATUSES = {"PASS", "SKIPPED", "ERROR"}
GATING_BLOCK_STATUSES = {"BLOCKED"}
LEGACY_STATUSES = {"HUMAN_REQUIRED", "FAIL"}
SEVERITY_LEVELS = ("none", "low", "medium", "high", "critical")


def parse_verdict(content: str) -> "dict | None":
    """Generic STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY line parser.

    GATE_TYPE/FINDINGS_COUNT/SEVERITY are optional on parse (three review.md writer
    paths omit them today) — only STATUS is required for a non-None result. Returns
    None when no STATUS: line is present at all; callers apply their own per-writer
    loose-fallback heuristic in that case (see run_record.py). Never raises on an
    unrecognized STATUS token or a malformed FINDINGS_COUNT.
    """
    if not content.strip():
        return None
    result: dict = {}
    for line in content.splitlines():
        if line.startswith("STATUS:"):
            result["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("GATE_TYPE:"):
            result["gate_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("FINDINGS_COUNT:"):
            try:
                result["findings_count"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("SEVERITY:"):
            result["severity"] = line.split(":", 1)[1].strip()
    return result if "status" in result else None


def format_verdict(gate_type: str, status: str, findings_count: int, severity: str) -> str:
    """Python-side sibling of gate_lib.sh::emit_verdict — byte-identical shape."""
    return (
        f"STATUS: {status}\n"
        f"GATE_TYPE: {gate_type}\n"
        f"FINDINGS_COUNT: {findings_count}\n"
        f"SEVERITY: {severity}\n"
    )
```

4. Run the test again, verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verdict.py -x -q
   ```
   Expected: `9 passed`.

5. Commit:
   ```bash
   git add scripts/factory_core/verdict.py tests/test_verdict.py
   git commit -m "feat(gates): add canonical verdict.py schema module (#197)"
   ```

---

## Task 2: Golden corpus — behaviour-preservation invariant (Requirement 9)

**Files:** `tests/fixtures/verdicts/*.md` (new, 15 files), `tests/fixtures/verdicts/*.expected.json`
(new, 15 files), `tests/test_verdict.py` (modified — append `test_golden_corpus_byte_compat`)

### TDD Steps

1. Create the fixture directory and all 15 `.md` files. Each captures a real writer
   path's exact literal output as it exists on `main` today (before this ticket's
   refactor), per file/line references already verified in the spec's "Verified
   starting facts":

```bash
mkdir -p tests/fixtures/verdicts
```

```bash
cat > tests/fixtures/verdicts/conformance__pass_with_cycles.md <<'EOF'
STATUS: PASS
GATE_TYPE: conformance
FINDINGS_COUNT: 0
SEVERITY: none
VERDICT: Conforms
CYCLES: 2
NO_SPEC: false
OOS_EXCISED: 0
OOS_TICKETS: 

---

Cycle 1: Material divergence — missing test coverage for X.
Cycle 2: Conforms.
EOF

cat > tests/fixtures/verdicts/conformance__blocked_critical.md <<'EOF'
STATUS: BLOCKED
GATE_TYPE: conformance
FINDINGS_COUNT: 1
SEVERITY: critical
VERDICT: MATERIAL
CYCLES: 3

---

Cycle 3: Material divergence — approach does not match spec's chosen design.
EOF

cat > tests/fixtures/verdicts/conformance__skipped.md <<'EOF'
STATUS: SKIPPED
REASON: conformance.enabled=false
EOF

cat > tests/fixtures/verdicts/review__empty_diff_pass.md <<'EOF'
STATUS: PASS
BLOCKERS: 0
ADVISORY: 0
EOF

cat > tests/fixtures/verdicts/review__fail_open_error.md <<'EOF'
STATUS: ERROR
BLOCKERS: 0
ADVISORY: 0
EOF

cat > tests/fixtures/verdicts/review__zero_findings_pass.md <<'EOF'
STATUS: PASS
BLOCKERS: 0
ADVISORY: 0
EOF

cat > tests/fixtures/verdicts/review__emit_verdict_pass_threshold.md <<'EOF'
STATUS: PASS
GATE_TYPE: code-review
FINDINGS_COUNT: 0
SEVERITY: none
BLOCKERS: 0
ADVISORY: 2
THRESHOLD: high

---

No blocking findings. 2 advisory findings below threshold.
EOF

cat > tests/fixtures/verdicts/review__blocked.md <<'EOF'
STATUS: BLOCKED
GATE_TYPE: code-review
FINDINGS_COUNT: 3
SEVERITY: high
BLOCKERS: 3
ADVISORY: 1
THRESHOLD: high

---

3 blocking findings at severity >= high.
EOF

cat > tests/fixtures/verdicts/blast__skipped.md <<'EOF'
STATUS: SKIPPED
GATE_TYPE: blast
FINDINGS_COUNT: 0
SEVERITY: none
---
TRIGGER: none
TRIGGERED_FILES:
LINES_CHANGED: 0
EOF

cat > tests/fixtures/verdicts/blast__pass.md <<'EOF'
STATUS: PASS
GATE_TYPE: blast
FINDINGS_COUNT: 0
SEVERITY: none
---
TRIGGER: none
TRIGGERED_FILES:
LINES_CHANGED: 12
EOF

cat > tests/fixtures/verdicts/blast__human_required.md <<'EOF'
STATUS: HUMAN_REQUIRED
GATE_TYPE: blast
FINDINGS_COUNT: 1
SEVERITY: critical
---
TRIGGER: hotspot
TRIGGERED_FILES:
  - scheduler.sh (category: hotspot)
LINES_CHANGED: 42
EOF

cat > tests/fixtures/verdicts/validation__prose_pass.md <<'EOF'
### Backend validation
pytest: 128 passed

### Frontend validation
tsc: no errors

Final status: PASS
EOF

cat > tests/fixtures/verdicts/validation__prose_fail.md <<'EOF'
### Backend validation
pytest: 3 failed, 125 passed

### Frontend validation
tsc: 2 errors

Final status: FAIL
EOF

cat > tests/fixtures/verdicts/conflict_resolution__verdict_eq.md <<'EOF'
CONFLICT_VERDICT=tier1
FILES_CONFLICTED=2
TIER1_RESOLVED=2
TIER2_RESOLVED=0
ESCALATED=0
EOF

cat > tests/fixtures/verdicts/conflict_resolution__status_bold_legacy.md <<'EOF'
Merge conflict resolution summary

**Status:** resolved

(Legacy free-form format — no current writer produces this; the parser branch is
preserved and exercised by this fixture per spec Requirement 9.)
EOF
```

2. Create the matching `.expected.json` files — the JSON result each fixture must
   produce, hand-traced against `_parse_artifact_stage`'s *current, unmodified*
   branches (or `verdict.parse_verdict` directly for `blast__*`, since `blast` is not
   in `run_record.py`'s `artifact_names` and is never routed through
   `_parse_artifact_stage`):

```bash
cat > tests/fixtures/verdicts/conformance__pass_with_cycles.expected.json <<'EOF'
{"stage": "conformance", "verdict": "PASS", "cycles": 2}
EOF
cat > tests/fixtures/verdicts/conformance__blocked_critical.expected.json <<'EOF'
{"stage": "conformance", "verdict": "BLOCKED", "cycles": 3}
EOF
cat > tests/fixtures/verdicts/conformance__skipped.expected.json <<'EOF'
{"stage": "conformance", "verdict": "SKIPPED"}
EOF
cat > tests/fixtures/verdicts/review__empty_diff_pass.expected.json <<'EOF'
{"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 0}
EOF
cat > tests/fixtures/verdicts/review__fail_open_error.expected.json <<'EOF'
{"stage": "review", "verdict": "ERROR", "blockers": 0, "advisory": 0}
EOF
cat > tests/fixtures/verdicts/review__zero_findings_pass.expected.json <<'EOF'
{"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 0}
EOF
cat > tests/fixtures/verdicts/review__emit_verdict_pass_threshold.expected.json <<'EOF'
{"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 2}
EOF
cat > tests/fixtures/verdicts/review__blocked.expected.json <<'EOF'
{"stage": "review", "verdict": "BLOCKED", "blockers": 3, "advisory": 1}
EOF
cat > tests/fixtures/verdicts/blast__skipped.expected.json <<'EOF'
{"status": "SKIPPED", "gate_type": "blast", "findings_count": 0, "severity": "none"}
EOF
cat > tests/fixtures/verdicts/blast__pass.expected.json <<'EOF'
{"status": "PASS", "gate_type": "blast", "findings_count": 0, "severity": "none"}
EOF
cat > tests/fixtures/verdicts/blast__human_required.expected.json <<'EOF'
{"status": "HUMAN_REQUIRED", "gate_type": "blast", "findings_count": 1, "severity": "critical"}
EOF
cat > tests/fixtures/verdicts/validation__prose_pass.expected.json <<'EOF'
{"stage": "validation", "verdict": "PASS"}
EOF
cat > tests/fixtures/verdicts/validation__prose_fail.expected.json <<'EOF'
{"stage": "validation", "verdict": "FAIL"}
EOF
cat > tests/fixtures/verdicts/conflict_resolution__verdict_eq.expected.json <<'EOF'
{"stage": "conflict_resolution", "verdict": "tier1"}
EOF
cat > tests/fixtures/verdicts/conflict_resolution__status_bold_legacy.expected.json <<'EOF'
{"stage": "conflict_resolution", "verdict": "resolved"}
EOF
```

3. Add `import json` and `import pathlib` to the top of `tests/test_verdict.py`,
   alongside the existing `import pytest` / `from factory_core import verdict`, and
   add `from factory_core import run_record` there too:

```python
import json
import pathlib

import pytest
from factory_core import run_record, verdict
```

   Then append `test_golden_corpus_byte_compat` to the end of `tests/test_verdict.py`:

```python
_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "verdicts"

# blast.md is never routed through run_record._parse_artifact_stage (it is not in
# run_record.artifact_names) — its fixtures assert against verdict.parse_verdict
# directly, proving only the shared *schema* stays byte-compatible for that writer.
_SCHEMA_ONLY_PREFIXES = ("blast__",)


def _stage_name(fixture_path: pathlib.Path) -> str:
    return fixture_path.name.split("__", 1)[0]


def test_golden_corpus_byte_compat():
    md_files = sorted(_FIXTURES_DIR.glob("*.md"))
    assert len(md_files) == 15, "golden corpus fixture count changed unexpectedly"
    for md_path in md_files:
        expected_path = md_path.with_suffix("").with_suffix(".expected.json")
        content = md_path.read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        if md_path.name.startswith(_SCHEMA_ONLY_PREFIXES):
            actual = verdict.parse_verdict(content)
        else:
            actual = run_record._parse_artifact_stage(_stage_name(md_path), content)
        assert actual == expected, f"{md_path.name}: expected {expected}, got {actual}"
```

4. Run it now, against **today's unmodified** `run_record.py` (Task 1 already landed
   `verdict.py`, but `run_record.py` hasn't been refactored yet — this proves the
   fixtures/expected-JSON pairs are correct against the *current* behavior before any
   refactor, which is the whole point of a golden corpus):
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verdict.py::test_golden_corpus_byte_compat -x -q
   ```
   Expected: `1 passed`. (If any fixture disagrees with its expected JSON, fix the
   `.expected.json` file to match `main`'s real current behavior, never the reverse —
   the golden corpus must reflect reality, not a wish.)

5. Commit:
   ```bash
   git add tests/fixtures/verdicts/ tests/test_verdict.py
   git commit -m "test(gates): add golden corpus for verdict byte-compatibility (#197)"
   ```

---

## Task 3: Refactor `run_record.py::_parse_artifact_stage` to delegate

**Files:** `scripts/factory_core/run_record.py` (modified), `tests/test_run_record.py` (modified)

### TDD Steps

1. Add one failing additive test to `tests/test_run_record.py` (append near the
   existing `test_parse_artifact_*` tests, e.g. after `test_parse_artifact_missing_returns_none`):

```python
def test_parse_artifact_stage_generic_fallback_for_unseen_gate_type():
    # A name run_record.py has no bespoke overlay for (e.g. a future target-loop
    # GATE_TYPE) still round-trips through the shared generic parser — proving the
    # refactor's delegation, not just the four hardcoded names, actually works.
    content = "STATUS: BLOCKED\nGATE_TYPE: loop:nightly-scan-triage\nFINDINGS_COUNT: 1\nSEVERITY: high\n"
    result = run_record._parse_artifact_stage("loop:nightly-scan-triage", content)
    assert result["stage"] == "loop:nightly-scan-triage"
    assert result["verdict"] == "BLOCKED"


def test_parse_artifact_validation_keeps_first_status_line_wins():
    # validation's original loop does `break` on the first STATUS: match (first-wins);
    # conformance/review deliberately have no break (last-wins). The shared
    # verdict.parse_verdict is last-wins throughout, so validation must NOT be routed
    # through it for STATUS extraction -- this pins that the refactor preserves
    # validation's distinct precedence rather than silently changing it.
    content = "STATUS: PASS\nsome prose\nSTATUS: FAIL\n"
    result = run_record._parse_artifact_stage("validation", content)
    assert result["verdict"] == "PASS"


def test_parse_artifact_conformance_keeps_last_status_line_wins():
    # conformance's original loop has no break -- last STATUS: line wins. Pin this
    # explicitly so the shared parser's last-wins semantics are the *intended* match
    # for conformance/review, not an accidental side effect of delegation.
    content = "STATUS: PASS\nSTATUS: BLOCKED\nCYCLES: 1\n"
    result = run_record._parse_artifact_stage("conformance", content)
    assert result["verdict"] == "BLOCKED"
```

2. Run the full existing file to establish the baseline (all current tests pass,
   the new one fails or errors since the generic fallback branch doesn't exist yet):
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_run_record.py -x -q
   ```

3. Refactor `_parse_artifact_stage` in `scripts/factory_core/run_record.py`. Add the
   import near the top (alongside the existing `from . import model_proxy`):

```python
from . import model_proxy
from . import verdict as _verdict
```

   Replace the whole `_parse_artifact_stage` function body with:

```python
def _parse_artifact_stage(name: str, content: str) -> "dict | None":
    """Extract stage verdict and metadata from a verdict artifact .md file.

    Thin per-name wrapper over verdict.parse_verdict's generic STATUS/GATE_TYPE/
    FINDINGS_COUNT/SEVERITY parse for the names whose original loop already had
    last-STATUS-line-wins semantics (conformance/review), each keeping its own
    extra-detail overlay (cycles/blockers/advisory) and its own pre-existing
    loose-fallback heuristic, byte-identical to before this refactor.

    validation's original loop instead `break`s on the *first* STATUS: match
    (first-wins) -- a different precedence than the shared generic parser's
    last-wins scan -- so it keeps its own bespoke first-wins loop rather than
    delegating, to avoid silently changing behavior for hypothetical multi-STATUS
    content. conflict_resolution never used the STATUS: schema at all
    (CONFLICT_VERDICT=/**Status:** instead) and stays fully independent, exactly
    as before. A name this function has no bespoke overlay for (e.g. a target
    loop's own GATE_TYPE) falls through to the generic parser directly — new
    capability, not exercised by cmd_assemble's fixed artifact_names today.
    """
    if not content.strip():
        return None

    lines = content.splitlines()

    if name == "validation":
        verdict = None
        for line in lines:
            if line.startswith("STATUS:"):
                verdict = line.split(":", 1)[1].strip()
                break
        if verdict is None:
            verdict = (
                "PASS" if "PASS" in content else ("FAIL" if "FAIL" in content else None)
            )
        if verdict is None:
            return None
        return {"stage": name, "verdict": verdict}

    if name == "conflict_resolution":
        verdict = None
        for line in lines:
            if line.startswith("CONFLICT_VERDICT="):
                verdict = line.split("=", 1)[1].strip()
                break
            if "**Status:**" in line:
                verdict = line.split("**Status:**", 1)[1].strip().strip("*").strip()
                break
        if verdict is None:
            verdict = "RESOLVED" if "RESOLVED" in content else "none"
        return {"stage": name, "verdict": verdict}

    # conformance, review, and any name with no bespoke overlay share the generic
    # parser's last-STATUS-line-wins semantics -- matching conformance/review's
    # original no-break loops exactly.
    parsed = _verdict.parse_verdict(content) or {}
    verdict = parsed.get("status")
    detail: dict = {}

    if name == "conformance":
        for line in lines:
            if line.startswith("CYCLES:"):
                try:
                    detail["cycles"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if verdict is None:
            if "⛔" in content:
                verdict = "BLOCKED"
            elif "Conforms" in content or "Minor" in content or "PASS" in content:
                verdict = "PASS"

    elif name == "review":
        for line in lines:
            if line.startswith("BLOCKERS:"):
                try:
                    detail["blockers"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("ADVISORY:"):
                try:
                    detail["advisory"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if verdict is None:
            verdict = (
                "PASS" if "PASS" in content else ("BLOCKED" if "BLOCKED" in content else None)
            )

    # else: no bespoke overlay for this name — verdict/detail stay exactly what the
    # generic parser produced (the new fallback path).

    if verdict is None:
        return None

    result: dict = {"stage": name, "verdict": verdict}
    result.update(detail)
    return result
```

4. Run the full test file again, verify everything (old + new) passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_run_record.py -x -q
   ```
   Expected: all tests pass, including every pre-existing `test_parse_artifact_*`,
   `test_outcome_*` test unmodified, plus the three new tests (generic fallback,
   validation first-wins, conformance last-wins).

5. Re-run the Task 2 golden corpus test to confirm the refactor didn't change any of
   the four real writer paths' parse results:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verdict.py::test_golden_corpus_byte_compat -x -q
   ```
   Expected: `1 passed` (identical result to Task 2 step 4 — this is the actual proof
   of Requirement 9's behaviour-preservation invariant post-refactor).

6. Commit:
   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "refactor(gates): delegate _parse_artifact_stage to verdict.parse_verdict (#197)"
   ```

---

## Task 4: `scripts/factory_core/verifier.py` — resolve/run/normalize

**Files:** `scripts/factory_core/verifier.py` (new), `tests/fixtures/verifiers/*.sh` (new),
`tests/test_verifier.py` (new)

### TDD Steps

1. Create the fixture verifier scripts:

```bash
mkdir -p tests/fixtures/verifiers

cat > tests/fixtures/verifiers/structured_pass.sh <<'EOF'
#!/usr/bin/env bash
printf 'STATUS: PASS\nGATE_TYPE: ignored-by-normalize_verdict\nFINDINGS_COUNT: 0\nSEVERITY: none\n'
exit 0
EOF

cat > tests/fixtures/verifiers/structured_blocked.sh <<'EOF'
#!/usr/bin/env bash
printf 'STATUS: BLOCKED\nGATE_TYPE: ignored-by-normalize_verdict\nFINDINGS_COUNT: 2\nSEVERITY: high\n'
exit 1
EOF

cat > tests/fixtures/verifiers/structured_error.sh <<'EOF'
#!/usr/bin/env bash
printf 'STATUS: ERROR\nGATE_TYPE: ignored-by-normalize_verdict\nFINDINGS_COUNT: 0\nSEVERITY: none\n'
exit 0
EOF

cat > tests/fixtures/verifiers/bare_exit_0.sh <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

cat > tests/fixtures/verifiers/bare_exit_1.sh <<'EOF'
#!/usr/bin/env bash
exit 1
EOF

cat > tests/fixtures/verifiers/sleeper.sh <<'EOF'
#!/usr/bin/env bash
sleep 5
exit 0
EOF

cat > tests/fixtures/verifiers/env_check.sh <<'EOF'
#!/usr/bin/env bash
{
  echo "CLONE_DIR=$CLONE_DIR"
  echo "ARTIFACTS_DIR=$ARTIFACTS_DIR"
  echo "ISSUE_NUM=$ISSUE_NUM"
  echo "LOOP_NAME=$LOOP_NAME"
  echo "FACTORY_REPO_SLUG=$FACTORY_REPO_SLUG"
} > "$ENV_DUMP_PATH"
printf 'STATUS: PASS\nGATE_TYPE: x\nFINDINGS_COUNT: 0\nSEVERITY: none\n'
exit 0
EOF

chmod +x tests/fixtures/verifiers/*.sh
```

2. Write the failing test file `tests/test_verifier.py` (this task's slice: resolve/run/normalize only):

```python
import os
import pathlib
import pytest

from factory_core import verifier

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "verifiers"


def _env(tmp_path):
    return {**os.environ, "CLONE_DIR": str(tmp_path), "ARTIFACTS_DIR": str(tmp_path),
            "ISSUE_NUM": "197", "LOOP_NAME": "test-loop", "FACTORY_REPO_SLUG": "omniscient/dark-factory"}


def test_resolve_verifier_joins_clone_dir():
    resolved = verifier.resolve_verifier("/clone", "scripts/my-verifier.sh")
    assert resolved == "/clone/scripts/my-verifier.sh"


def test_run_verifier_structured_pass(tmp_path):
    exit_code, stdout = verifier.run_verifier(str(_FIXTURES / "structured_pass.sh"), _env(tmp_path))
    assert exit_code == 0
    assert stdout.startswith("STATUS: PASS")


def test_run_verifier_missing_path_raises(tmp_path):
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(tmp_path / "does-not-exist.sh"), _env(tmp_path))


def test_run_verifier_non_executable_path_raises(tmp_path):
    p = tmp_path / "not-executable.sh"
    p.write_text("#!/usr/bin/env bash\nexit 0\n")
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(p), _env(tmp_path))


def test_run_verifier_timeout_raises(tmp_path):
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(_FIXTURES / "sleeper.sh"), _env(tmp_path), timeout=1)


def test_normalize_verdict_structured_ignores_exit_code_and_renamespaces_gate_type():
    stdout = "STATUS: BLOCKED\nGATE_TYPE: whatever\nFINDINGS_COUNT: 2\nSEVERITY: high\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 2\nSEVERITY: high\n"


def test_normalize_verdict_bare_exit_0_synthesizes_pass():
    text = verifier.normalize_verdict(exit_code=0, stdout="", gate_type="loop:my-loop")
    assert text == "STATUS: PASS\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_normalize_verdict_bare_nonzero_synthesizes_blocked_high():
    text = verifier.normalize_verdict(exit_code=1, stdout="", gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"


def test_normalize_verdict_structured_error_is_not_pass_through():
    # Requirement 4: ERROR is reserved for "the verifier self-reported it could
    # not complete" and is explicitly NOT auto-pass-through for target verifiers
    # (unlike code_review.fail_open's advisory-on-error convention) -- verbatim
    # ERROR would sail through verdict_gate_check.sh's PASS/SKIPPED/ERROR proceed
    # set, defeating AC3's "missing/failing cannot hand off" default.
    stdout = "STATUS: ERROR\nGATE_TYPE: whatever\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"
```

3. Verify failure (module doesn't exist):
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verifier.py -x -q
   ```

4. Implement `scripts/factory_core/verifier.py` (this task's slice):

```python
"""Target-registered check-only verifier resolution + invocation.

Generalizes hooks.sh::run_hook's target-over-default, check-only, factory-owns-
side-effects precedent from a fixed .factory/hooks/<name> convention to an
arbitrary adapter-declared path (a loop entry's verification.verifier field, #301).
See refinement-skills/VERIFIER-CONTRACT.md for the full registration contract.
"""
import os
import subprocess

from . import verdict as _verdict

DEFAULT_TIMEOUT_SECONDS = 300


class VerifierError(Exception):
    """Raised on any condition verifier.py must fail closed for: missing path,
    non-executable path, timeout, or a process that could not be started."""


def resolve_verifier(clone_dir: str, verifier_path: str) -> str:
    """Resolve an adapter-declared verifier path relative to clone_dir.

    Unlike hooks.sh::run_hook, a target verifier has no built-in factory default to
    fall back to — a missing/non-executable result is a fail-closed condition
    (Requirement 4), not a no-op.
    """
    return os.path.join(clone_dir, verifier_path)


def run_verifier(resolved_path: str, env: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> "tuple[int, str]":
    """Execute resolved_path with env; returns (exit_code, stdout).

    Raises VerifierError on missing/non-executable path, timeout, or a process that
    could not be started — callers must catch this and synthesize STATUS: BLOCKED
    (Requirement 4), never let it surface as an unhandled crash.
    """
    if not os.path.isfile(resolved_path) or not os.access(resolved_path, os.X_OK):
        raise VerifierError(f"verifier path missing or not executable: {resolved_path}")
    try:
        proc = subprocess.run(
            [resolved_path], env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerifierError(f"verifier timed out after {timeout}s: {resolved_path}") from exc
    except OSError as exc:
        raise VerifierError(f"verifier process could not be started: {exc}") from exc
    return proc.returncode, proc.stdout


def normalize_verdict(exit_code: int, stdout: str, gate_type: str) -> str:
    """Structured vs. bare-exit-code dispatch (Requirement 4).

    Structured: stdout already begins with a STATUS: line — parsed through the
    shared schema, then re-namespaced onto gate_type (never trusted verbatim from
    the verifier's own stdout, per Requirement 4). A self-reported STATUS: ERROR
    is remapped to BLOCKED/high -- ERROR is not auto-pass-through for target
    verifiers (unlike code_review.fail_open's advisory-on-error default); AC3
    requires "missing/failing cannot hand off" as the default, not an opt-in.
    Bare-exit-code: no structured output — exit 0 synthesizes PASS, non-zero
    synthesizes BLOCKED/high, mirroring smoke-gate's exit-code-only convention.
    """
    if stdout.lstrip().startswith("STATUS:"):
        parsed = _verdict.parse_verdict(stdout) or {}
        status = parsed.get("status", "ERROR")
        if status == "ERROR":
            return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
        return _verdict.format_verdict(
            gate_type, status, parsed.get("findings_count", 0), parsed.get("severity", "none"),
        )
    if exit_code == 0:
        return _verdict.format_verdict(gate_type, "PASS", 0, "none")
    return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
```

5. Run the tests, verify they pass:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verifier.py -x -q
   ```
   Expected: `9 passed`.

6. Commit:
   ```bash
   git add scripts/factory_core/verifier.py tests/fixtures/verifiers/ tests/test_verifier.py
   git commit -m "feat(gates): add verifier.py resolve/run/normalize primitives (#197)"
   ```

---

## Task 5: `assert_verifier_independent`, `resolve_and_run`, and the CLI

**Files:** `scripts/factory_core/verifier.py` (modified — append), `tests/test_verifier.py`
(modified — append)

### TDD Steps

1. Add `import subprocess` and `import sys` to the top of `tests/test_verifier.py`,
   alongside the existing `import os` / `import pathlib` / `import pytest` /
   `from factory_core import verifier`. Then append the following failing tests to
   the end of `tests/test_verifier.py`:

```python
def _loop_entry(**overrides):
    entry = {
        "name": "nightly-scan-triage",
        "discovery": {"trigger": "cron:0 6 * * *", "inputs": ["scripts/scanner.py"]},
        "handoff": {"outputs": ["artifacts/scan-report.md"], "manifest": "artifacts/manifest.json"},
        "verification": {"verifier": "scripts/verify-scan.sh", "stop_condition": "manifest present"},
        "persistence": {"artifacts": ["artifacts/scan-history.jsonl"]},
        "scheduling": {"failure_behavior": "retry-once"},
        "side_effect_level": 2,
    }
    entry.update(overrides)
    return entry


def test_assert_verifier_independent_passes_when_disjoint():
    verifier.assert_verifier_independent(_loop_entry())  # no raise


def test_assert_verifier_independent_rejects_manifest_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["handoff"]["manifest"]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_rejects_outputs_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["handoff"]["outputs"][0]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_rejects_persistence_artifacts_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["persistence"]["artifacts"][0]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_normalizes_paths_before_comparing():
    entry = _loop_entry()
    entry["verification"]["verifier"] = "./artifacts/../artifacts/manifest.json"
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_resolve_and_run_env_contract(tmp_path, monkeypatch):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "env_check.sh").read_text())
    verifier_script.chmod(0o755)
    dump_path = tmp_path / "envdump.txt"
    monkeypatch.setenv("ENV_DUMP_PATH", str(dump_path))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="test-loop", verifier_path="verifier.sh",
        issue_num="197", factory_repo_slug="omniscient/dark-factory", side_effect_level=1,
    )
    dumped = dump_path.read_text()
    assert f"CLONE_DIR={tmp_path}" in dumped
    assert f"ARTIFACTS_DIR={tmp_path}" in dumped
    assert "ISSUE_NUM=197" in dumped
    assert "LOOP_NAME=test-loop" in dumped
    assert "FACTORY_REPO_SLUG=omniscient/dark-factory" in dumped


def test_resolve_and_run_gate_type_namespaced_to_loop(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=1,
    )
    assert "GATE_TYPE: loop:my-loop" in text
    assert "ignored-by-normalize_verdict" not in text


def test_resolve_and_run_fails_closed_on_missing_verifier(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="does-not-exist.sh", side_effect_level=1,
    )
    assert "STATUS: BLOCKED" in text
    assert "GATE_TYPE: loop:my-loop" in text


def test_resolve_and_run_fails_closed_when_side_effect_level_undetermined(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="anything.sh", side_effect_level=None,
    )
    assert "STATUS: BLOCKED" in text


def test_resolve_and_run_fails_closed_for_factory_owned_level(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=4,
    )
    assert "STATUS: BLOCKED" in text
    assert "#196" in text


def test_resolve_and_run_records_required_profile_level_1(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=1,
    )
    assert "REQUIRED_PROFILE: level-1" in text


def test_resolve_and_run_records_side_effect_level_on_success(tmp_path):
    # Requirement 6(a): side_effect_level is recorded on the verdict so a future
    # #196 enforcement layer has something to check against.
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=2,
    )
    assert "SIDE_EFFECT_LEVEL: 2" in text


def test_resolve_and_run_records_side_effect_level_when_factory_owned(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="anything.sh", side_effect_level=5,
    )
    assert "SIDE_EFFECT_LEVEL: 5" in text


def test_cli_default_timeout_is_300():
    assert verifier.DEFAULT_TIMEOUT_SECONDS == 300


def test_cli_refuses_reserved_out_basenames(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    out_path = tmp_path / "review.md"
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(tmp_path), "--loop-name", "my-loop",
         "--verifier-path", "verifier.sh", "--side-effect-level", "1",
         "run", "--out", str(out_path)],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert not out_path.exists()


def test_cli_writes_verdict_to_out_path(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    out_path = tmp_path / "loop-verdict.md"
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(tmp_path), "--loop-name", "my-loop",
         "--verifier-path", "verifier.sh", "--side-effect-level", "1",
         "run", "--out", str(out_path)],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "STATUS: PASS" in out_path.read_text()
    assert "GATE_TYPE: loop:my-loop" in out_path.read_text()


def test_cli_timeout_override(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(_FIXTURES), "--loop-name", "my-loop",
         "--verifier-path", "sleeper.sh", "--side-effect-level", "1", "--timeout", "1",
         "run", "--out", str(tmp_path / "out.md")],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 0  # CLI itself succeeds; the timeout is recorded as BLOCKED
    assert "STATUS: BLOCKED" in (tmp_path / "out.md").read_text()
```

2. Verify these fail (functions/CLI don't exist yet):
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verifier.py -x -q
   ```

3. Add `import argparse` and `import sys` to the top of `scripts/factory_core/verifier.py`,
   alongside the existing `import os` / `import subprocess` (do not add them later in
   the file):

```python
import argparse
import os
import subprocess
import sys

from . import verdict as _verdict
```

   Then append the rest to the end of `scripts/factory_core/verifier.py`:

```python
# GATE_TYPE basenames the ticket-lifecycle pipeline already owns; a target verdict
# artifact must never collide with one of these (Requirement 4).
_RESERVED_OUT_BASENAMES = {
    "validation.md", "conformance.md", "review.md",
    "conflict_resolution.md", "blast.md",
}

# side_effect_level range that is factory-owned until #196 ships real
# permission-profile enforcement (per #193).
_FACTORY_OWNED_MIN_LEVEL = 4


def assert_verifier_independent(loop_entry: dict) -> None:
    """Path-disjointness rule (Requirement 5): a loop's verifier must not be the
    loop's own handoff producer or a file it writes.

    owned = {handoff.manifest} ∪ set(handoff.outputs) ∪ set(persistence.artifacts)
    String/path comparison only (os.path.normpath) — no filesystem access, no
    existence check, consistent with #301's opaque-reference treatment of these
    fields. This is the declaration-time half of maker≠checker; the load-bearing
    half is that the verifier always runs as a separate check-only process whose
    verdict the factory parses and acts on (#189's clean-room-grader principle).
    """
    verifier_path = (loop_entry.get("verification") or {}).get("verifier")
    handoff = loop_entry.get("handoff") or {}
    persistence = loop_entry.get("persistence") or {}
    owned = set()
    manifest = handoff.get("manifest")
    if manifest:
        owned.add(os.path.normpath(manifest))
    for p in handoff.get("outputs") or []:
        owned.add(os.path.normpath(p))
    for p in persistence.get("artifacts") or []:
        owned.add(os.path.normpath(p))
    if verifier_path and os.path.normpath(verifier_path) in owned:
        name = loop_entry.get("name", "?")
        raise VerifierError(
            f"loop '{name}': verifier '{verifier_path}' must not be a path the loop "
            f"itself owns (handoff.manifest / handoff.outputs / persistence.artifacts)"
        )


def resolve_and_run(
    *, clone_dir: str, loop_name: str, verifier_path: str,
    issue_num: str = "", factory_repo_slug: str = "",
    side_effect_level: "int | None" = None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """End-to-end: resolve, run, normalize, record required_profile + side_effect_level.

    Fails closed (STATUS: BLOCKED) on every non-PASS-able condition: missing/
    non-executable path, timeout, undetermined side_effect_level, or a
    side_effect_level in the factory-owned range (Requirement 6) — never silently
    skips (AC3). Records SIDE_EFFECT_LEVEL on every verdict where a level was
    resolved, so a future #196 enforcement layer has something to check against
    (Requirement 6a); an undetermined level has no level to record. This is the
    primitive a future dispatcher, the CLI below, or a test calls per declared loop.
    """
    gate_type = f"loop:{loop_name}"

    if side_effect_level is None:
        return (
            _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
            + "REQUIRED_PROFILE: undetermined\nREASON: side_effect_level not resolved\n"
        )
    if side_effect_level >= _FACTORY_OWNED_MIN_LEVEL:
        return (
            _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
            + f"REQUIRED_PROFILE: factory-owned\nSIDE_EFFECT_LEVEL: {side_effect_level}\n"
            + "REASON: factory-owned level requires #196 profile enforcement\n"
        )

    resolved = resolve_verifier(clone_dir, verifier_path)
    env = dict(os.environ)
    env.update({
        "CLONE_DIR": clone_dir,
        "ARTIFACTS_DIR": env.get("ARTIFACTS_DIR", ""),
        "ISSUE_NUM": issue_num,
        "FACTORY_REPO_SLUG": factory_repo_slug,
        "LOOP_NAME": loop_name,
    })
    profile_suffix = f"REQUIRED_PROFILE: level-1\nSIDE_EFFECT_LEVEL: {side_effect_level}\n"
    try:
        exit_code, stdout = run_verifier(resolved, env, timeout=timeout)
    except VerifierError:
        return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high") + profile_suffix

    return normalize_verdict(exit_code, stdout, gate_type) + profile_suffix


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve and run a target-registered check-only verifier")
    p.add_argument("--clone-dir", required=True)
    p.add_argument("--loop-name", required=True)
    p.add_argument("--verifier-path", required=True)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--issue-num", default=os.environ.get("ISSUE_NUM", ""))
    p.add_argument("--factory-repo-slug", default=os.environ.get("FACTORY_REPO_SLUG", ""))
    p.add_argument("--side-effect-level", type=int, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--out", required=True)
    args = p.parse_args()

    out_basename = os.path.basename(args.out)
    if out_basename in _RESERVED_OUT_BASENAMES:
        print(
            f"verifier: --out basename '{out_basename}' is reserved for the "
            f"ticket-lifecycle pipeline artifacts", file=sys.stderr,
        )
        sys.exit(2)

    verdict_text = resolve_and_run(
        clone_dir=args.clone_dir, loop_name=args.loop_name, verifier_path=args.verifier_path,
        issue_num=args.issue_num, factory_repo_slug=args.factory_repo_slug,
        side_effect_level=args.side_effect_level, timeout=args.timeout,
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(verdict_text)


if __name__ == "__main__":
    main()
```

4. Run the full test file, verify all pass:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verifier.py -x -q
   ```
   Expected: `26 passed` (9 from Task 4 + 17 new).

5. Commit:
   ```bash
   git add scripts/factory_core/verifier.py tests/test_verifier.py
   git commit -m "feat(gates): add verifier.py independence check, resolve_and_run, CLI (#197)"
   ```

---

## Task 6: Shared checker-invocation doc + de-duplicate the four command files

**Files:** `refinement-skills/VERIFIER-CONTRACT.md` (new), `refinement-skills/SKILL.md`
(modified), `commands/dark-factory-refine.md` (modified), `commands/dark-factory-plan.md`
(modified, 2 sites), `commands/dark-factory-conformance.md` (modified),
`commands/dark-factory-code-review.md` (modified), `scripts/gate_lib.sh` (modified)

This task is documentation deduplication only — no tool grant/restriction is
introduced or removed, no model pin changes (Requirement 3).

### Steps

1. Create `refinement-skills/VERIFIER-CONTRACT.md`:

~~~markdown
# Verifier / checker-invocation contract

Shared by every checker subagent spawn (`refine`'s product-owner, `plan`'s architect
and Phase 3.5 conformance reviewer, `conformance`'s reviewer, `code-review`'s
reviewer) and by every target-registered check-only verifier (`verification.verifier`
on a `.factory/adapter.yaml` `loops:` entry, #301). See
`docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md` for the full
design; this doc is the operational reference command authors and target-repo authors
read directly.

## Checker subagent invocation (Opus-pinned pairs)

- **Model pin:** always `claude-opus-4-8` for the checker subagent — never let it
  inherit the orchestrator's model. This applies to every re-spawn in a reconcile
  loop, not just the first spawn.
- **Read access:** the checker subagent needs `Glob`, `Grep`, and `Read` to explore
  the codebase it is reviewing. No tool restriction is introduced or documented as
  existing beyond this — tool allow/deny changes are a separate, reviewed concern
  (CLAUDE.md).
- **Clone-live-first resolution (rubric docs only):** `conformance`'s and
  `code-review`'s reviewer rubrics, and `plan`'s Phase 3.5 conformance rubric, read
  the live clone first (e.g. `.claude/skills/conformance/RUBRIC.md`), falling back to
  the baked copy under `/opt/refinement-skills/*.md` only if the clone-live file is
  absent — this lets a target repo override a rubric without a factory image
  rebuild. `refine`'s product-owner prompt and `plan`'s Phase 3 architect prompt are
  **not** part of this pattern: they keep reading their baked `/opt/refinement-
  skills/{product-owner,architect}-prompt.md` copies as-is, unchanged by this
  ticket. This contract doc itself (`VERIFIER-CONTRACT.md`) is always read at its
  fixed baked path, `/opt/refinement-skills/VERIFIER-CONTRACT.md`, by all four
  commands — it is not itself subject to clone-live-first resolution.

## Verdict schema

`STATUS` / `GATE_TYPE` / `FINDINGS_COUNT` / `SEVERITY` — canonically implemented in
`scripts/factory_core/verdict.py` (`parse_verdict`/`format_verdict`) and
`scripts/gate_lib.sh::emit_verdict` (bash). `STATUS` is a free token; its *gating*
values (per `scripts/verdict_gate_check.sh`) are `PASS`/`SKIPPED`/`ERROR` (proceed)
and `BLOCKED` (block). `HUMAN_REQUIRED` and `FAIL` are documented legacy tokens
returned verbatim, never rejected or normalized. `GATE_TYPE`/`FINDINGS_COUNT`/
`SEVERITY` are optional on parse, required on emit. `SEVERITY` ∈
`{none, low, medium, high, critical}`.

## Target-verifier registration contract

A target repo declares a check-only verifier via a loop entry's
`verification.verifier` field (an opaque path, resolved relative to the clone root
by `scripts/factory_core/verifier.py::resolve_verifier`). Invocation:

```bash
python3 -m factory_core.verifier \
  --clone-dir "$CLONE_DIR" --loop-name <loop name> \
  --verifier-path <verification.verifier path> --side-effect-level <loop's resolved level> \
  run --out <artifact path>
```

`--side-effect-level` has no default (`None`) and `resolve_and_run` fails closed when
it is absent — a caller must always resolve and pass the loop's actual level.

- **Env contract** (exported to the verifier process, mirroring
  `hooks.sh::run_hook`'s existing four-variable contract plus one addition):
  `CLONE_DIR`, `ARTIFACTS_DIR`, `ISSUE_NUM`, `FACTORY_REPO_SLUG`, `LOOP_NAME`.
- **Output modes:**
  - *Structured* — stdout begins with a `STATUS:` line: parsed through the shared
    schema. `GATE_TYPE` is always rewritten to `loop:<loop name>` — never trusted
    verbatim from the verifier's own stdout. A structured `STATUS: PASS` is trusted
    even if the process exited non-zero (the exit code is only consulted in
    bare-exit-code mode) — a verifier that prints `PASS` and then crashes is not
    caught by its exit code; write verifiers to report their real result in stdout.
  - *Bare-exit-code* — no structured stdout: exit `0` synthesizes `STATUS: PASS`,
    non-zero synthesizes `STATUS: BLOCKED, FINDINGS_COUNT: 1, SEVERITY: high`. This
    mirrors `smoke-gate`'s existing bare-exit-code convention as the low-effort
    on-ramp for a target's first verifier.
- **Fail-closed defaults:** a missing path, a non-executable path, a timeout
  (`--timeout`, default 300s), or a process that cannot be started all produce
  `STATUS: BLOCKED` — never a silent skip. `ERROR` is reserved for "the verifier ran
  and self-reported it could not complete" and is **not** auto-pass-through for
  target verifiers (unlike `code_review.fail_open`'s advisory-on-error default).
- **Reserved output names:** `verifier.py`'s `--out` refuses the basenames
  `validation.md`, `conformance.md`, `review.md`, `conflict_resolution.md`,
  `blast.md` — a target verdict can only *add* a `BLOCK` on its own loop's handoff
  and is never read by `conformance-gate`/`review-gate`.
- **Maker≠checker:** a loop's declared `verification.verifier` must not equal
  `handoff.manifest`, and must not be a member of `handoff.outputs` or
  `persistence.artifacts` — enforced by `verifier.assert_verifier_independent()`,
  called from `adapter.py::load()`. This is a declaration-time string check; the
  load-bearing half is that the verifier always runs as a separate check-only
  process whose verdict the factory (not the loop) parses and acts on.
- **Permission profile:** `verifier.py` records `REQUIRED_PROFILE: level-1` and
  `SIDE_EFFECT_LEVEL: <n>` on every verdict where a level was resolved, and fails
  closed if a loop's `side_effect_level` cannot be resolved. It does not itself
  sandbox or restrict the verifier process — that enforcement is `#196`'s chartered
  scope. Loops with `side_effect_level >= 4` are factory-owned and fail closed
  rather than executing a target path, until `#196` ships.
~~~

2. Append a "Verifier Contract" bullet to `refinement-skills/SKILL.md`'s "Prompt
   Files" section:

   Find:
   ```
   - `orchestrator-prompt.md` — Persona stub for the brainstorming orchestrator — full process lives in `dark-factory-refine.md`
   ```
   Replace with:
   ```
   - `orchestrator-prompt.md` — Persona stub for the brainstorming orchestrator — full process lives in `dark-factory-refine.md`

   See `refinement-skills/VERIFIER-CONTRACT.md` for the shared checker-invocation
   contract (model pin, read access, clone-live-first resolution) and the
   target-registered verifier registration contract.
   ```

**Path note:** every existing command file reads its baked cross-cutting persona/prompt
docs (`architect-prompt.md`, `product-owner-prompt.md`) at the absolute baked path
`/opt/refinement-skills/<file>.md` (`Dockerfile:143` bakes `refinement-skills/` there
wholesale) — never a clone-relative `refinement-skills/...` path, since the phase
agent's cwd is the *target clone*, which does not necessarily carry this factory
repo's own `refinement-skills/` directory (e.g. the MarketHawk target). `VERIFIER-
CONTRACT.md` follows that same existing convention (not the separate clone-live-first
`.claude/skills/*/RUBRIC.md` pattern, which only applies to *rubric* docs): every
reference below uses `/opt/refinement-skills/VERIFIER-CONTRACT.md`.

3. In `commands/dark-factory-refine.md`, find (Phase 1, step 4):
   ```
   4. Read `/opt/refinement-skills/product-owner-prompt.md` — you will pass this to subagents
   ```
   Replace with:
   ```
   4. Read `/opt/refinement-skills/product-owner-prompt.md` — you will pass this to subagents
   5. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for the product-owner subagent spawned in Phase 4
   ```
   (Renumber the original steps 5-6 that follow to 6-7.)

   Then find:
   ```
      - `model`: `claude-opus-4-8` — **always** pin this subagent to Opus 4.8 (do not let it inherit the orchestrator's model)
      - The subagent needs Glob, Grep, and Read tools to explore the codebase
   ```
   Replace with:
   ```
      - `model` and tool access: per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (Opus 4.8 pin, Glob/Grep/Read)
   ```

4. In `commands/dark-factory-plan.md`, find (Phase 1, step 3):
   ```
   3. Read `/opt/refinement-skills/architect-prompt.md` — you will pass this to the review subagent
   ```
   Replace with:
   ```
   3. Read `/opt/refinement-skills/architect-prompt.md` — you will pass this to the review subagent
   4. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for both the architect subagent (Phase 3) and the Phase 3.5 conformance reviewer subagent
   ```
   (Renumber the original steps 4-7 that follow to 5-8.)

   This shifts the spec-discovery steps referenced by step 1's cross-reference.
   Find:
   ```
   1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
      `## claude_md` section in place of reading `CLAUDE.md` directly, and its `## spec` section in
      place of the spec-file discovery glob below. For any section that is empty or absent from the
      pack, fall back to the existing behavior: read `CLAUDE.md` directly, and discover/read the spec
      via steps 4-5. No DAG node currently produces `context-pack.md` for the `plan` scenario, so this
   ```
   Replace `via steps 4-5` with `via steps 5-6` (the rest of the paragraph is unchanged) —
   old step 4 ("Find the spec file") and old step 5 ("Read the spec file") are now 5 and 6.

   Then find (Phase 3):
   ```
   - `model`: `claude-opus-4-8` — **always** pin this subagent to Opus 4.8 (applies to every re-spawn in the review cycle below too; do not let it inherit the orchestrator's model)
   ```
   Replace with:
   ```
   - `model` and tool access: per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every re-spawn in the review cycle below too)
   ```

   Find (Phase 3.5, step 5):
   ```
      - `model`: `claude-opus-4-8` — **always** pin this subagent to Opus 4.8 (applies to every reconcile re-spawn too; do not let it inherit the orchestrator's model)
   ```
   Replace with:
   ```
      - `model` and tool access: per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
   ```

5. In `commands/dark-factory-conformance.md`, find (Phase 1, step 3):
   ```
   3. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
      falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
      file is absent. Store the resolved text as `RUBRIC_CONTENT`.
   ```
   Replace with:
   ```
   3. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
      falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
      file is absent. Store the resolved text as `RUBRIC_CONTENT`.
   4. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for the conformance reviewer subagent spawned in Phase 3
   ```
   (Renumber the original steps 4-10 that follow to 5-11.)

   Then find:
   ```
      - `model`: `claude-opus-4-8` — **always** pin this subagent to Opus 4.8 (applies to every reconcile re-spawn in Phase 3.5 too; do not let it inherit the orchestrator's model)
   ```
   Replace with:
   ```
      - `model` and tool access: per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
   ```

6. In `commands/dark-factory-code-review.md`, find (Phase 1, step 2's sibling — insert
   after step 2's `Exit cleanly` line, before step 3):
   ```
   3. Extract `BLOCK_THRESHOLD` from `code_review.block_threshold` (default: `high`).
   ```
   Replace with:
   ```
   3. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for the code-reviewer subagent spawned in Phase 3
   4. Extract `BLOCK_THRESHOLD` from `code_review.block_threshold` (default: `high`).
   ```
   The live file's Phase 1 has a pre-existing numbering duplicate after the original
   step 3 (two items both numbered `6.`: "Extract `SEVERITY_ORDER_CSV`..." and
   "Determine `ISSUE_NUM`..."), unrelated to this edit. Renumber every original step
   from old-3's sibling onward explicitly to the sequential target below (old step
   numbers as they appear in the live file, top to bottom) — this also incidentally
   fixes the pre-existing duplicate as a side effect of making the insertion coherent:
   - old `4.` ("Extract `FAIL_OPEN`...") → `5.`
   - old `5.` ("Extract `MAX_FINDINGS`...") → `6.`
   - old `6.` ("Extract `SEVERITY_ORDER_CSV`...", the first of the two `6.`s) → `7.`
   - old `6.` ("Determine `ISSUE_NUM`...", the second/duplicate `6.`) → `8.`
   - old `7.` ("Determine `PR_NUM`...") → `9.`

   Then find:
   ```
      - `model`: `claude-opus-4-8` — **always** pin this subagent to Opus 4.8; do not let it inherit the orchestrator's model.
   ```
   Replace with:
   ```
      - `model` and tool access: per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract
   ```

7. In `scripts/gate_lib.sh`, find the header comment:
   ```
   #!/usr/bin/env bash
   # Shared gate functions sourced by dark-factory-conformance.md and dark-factory-code-review.md.
   # Do not add gate-specific logic here — only the three shared primitives.
   # Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.
   ```
   Replace with:
   ```
   #!/usr/bin/env bash
   # Shared gate functions sourced by dark-factory-conformance.md and dark-factory-code-review.md.
   # Do not add gate-specific logic here — only the three shared primitives.
   # emit_verdict()'s STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY shape is canonically
   # documented in scripts/factory_core/verdict.py (the Python-side parser/formatter)
   # and /opt/refinement-skills/VERIFIER-CONTRACT.md — keep this file's format byte-identical.
   # Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.
   ```

8. Write the failing test `tests/test_verifier_contract_doc_referenced.py` (matching
   the existing content-assertion convention of `tests/test_conformance_command_rubric_fallback.py`
   / `tests/test_code_review_command.py`), then make it pass by the edits above —
   this is the concrete AC1/Requirement-7 check the architect review flagged as
   missing:

```python
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent

_COMMAND_FILES = [
    "commands/dark-factory-refine.md",
    "commands/dark-factory-plan.md",
    "commands/dark-factory-conformance.md",
    "commands/dark-factory-code-review.md",
]


def test_every_command_file_references_verifier_contract_doc():
    for rel_path in _COMMAND_FILES:
        content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "/opt/refinement-skills/VERIFIER-CONTRACT.md" in content, (
            f"{rel_path} does not reference the shared checker-invocation contract doc"
        )


def test_plan_command_references_contract_doc_at_both_pin_sites():
    content = (REPO_ROOT / "commands/dark-factory-plan.md").read_text(encoding="utf-8")
    assert content.count("/opt/refinement-skills/VERIFIER-CONTRACT.md") >= 3  # Phase 1 read + 2 pin sites


def test_gate_lib_header_references_verdict_schema_docs():
    content = (REPO_ROOT / "scripts/gate_lib.sh").read_text(encoding="utf-8")
    assert "verdict.py" in content
    assert "VERIFIER-CONTRACT.md" in content


def test_verifier_contract_doc_exists_and_documents_env_contract():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    for token in ("CLONE_DIR", "ARTIFACTS_DIR", "ISSUE_NUM", "FACTORY_REPO_SLUG", "LOOP_NAME"):
        assert token in content
```

   Run it, verify all 4 pass after the edits above:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verifier_contract_doc_referenced.py -x -q
   ```
   Expected: `4 passed`.

9. Run the full pytest suite once to confirm no other test couples to the literal
   pin sentence text (verified in the plan's research phase: `test_epic_autopilot_config.sh`,
   `test_skill_flow_spotcheck.py`, `test_main_red_fixer.py`, `test_cost_report.py`,
   `test_epic_autopilot.py` all reference `claude-opus-4-8` in unrelated contexts, not
   command-file content):
   ```bash
   PYTHONPATH=scripts python -m pytest tests/ -k "skill or command or gate_lib" -q
   ```
   Expected: no failures.

10. Commit:
   ```bash
   git add refinement-skills/VERIFIER-CONTRACT.md refinement-skills/SKILL.md \
     commands/dark-factory-refine.md commands/dark-factory-plan.md \
     commands/dark-factory-conformance.md commands/dark-factory-code-review.md \
     scripts/gate_lib.sh tests/test_verifier_contract_doc_referenced.py
   git commit -m "docs(gates): de-duplicate checker-invocation contract into VERIFIER-CONTRACT.md (#197)"
   ```

---

## Task 7: Integration test — verifier.py through the real verdict_gate_check.sh

**Files:** `tests/test_verdict_gate_check.sh` (modified — append)

Proves Requirement 8c / AC3 end-to-end: a `verifier.py`-written artifact piped through
the **real, unmodified** `verdict_gate_check.sh` subprocess.

### Steps

1. Append to the end of `tests/test_verdict_gate_check.sh`, before the final `echo PASS`
   (move `echo PASS` to after these new cases):

```bash
# --- Case 11: verifier.py PASS artifact proceeds through the real gate ------
CASE11_DIR=$(mktemp -d)
VERIFIER11="${CASE11_DIR}/verifier.sh"
cat > "$VERIFIER11" <<'SCRIPT'
#!/usr/bin/env bash
exit 0
SCRIPT
chmod +x "$VERIFIER11"
CASE11_OUT="${WORK}/case11-loop-verdict.md"
PYTHONPATH="${REPO_ROOT}/scripts" python3 -m factory_core.verifier \
  --clone-dir "$CASE11_DIR" --loop-name "integration-loop" \
  --verifier-path "verifier.sh" --side-effect-level 1 \
  run --out "$CASE11_OUT"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE11_OUT" "271" "loop:integration-loop")
[ "$RC" = "0" ] || { echo "FAIL case11 expected exit 0 (verifier PASS), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
rm -rf "$CASE11_DIR"

# --- Case 12: verifier.py BLOCKED artifact (missing verifier path) blocks ----
CASE12_DIR=$(mktemp -d)
CASE12_OUT="${WORK}/case12-loop-verdict.md"
PYTHONPATH="${REPO_ROOT}/scripts" python3 -m factory_core.verifier \
  --clone-dir "$CASE12_DIR" --loop-name "integration-loop" \
  --verifier-path "does-not-exist.sh" --side-effect-level 1 \
  run --out "$CASE12_OUT"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE12_OUT" "271" "loop:integration-loop")
[ "$RC" = "1" ] || { echo "FAIL case12 expected exit 1 (verifier BLOCKED), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
rm -rf "$CASE12_DIR"

# --- Case 13: missing verifier-written artifact — true silent miss, blocks --
CASE13_OUT="${WORK}/case13-does-not-exist.md"
NEEDS_DISCUSSION_LABEL="false"
RC=$(_run "$CASE13_OUT" "271" "loop:integration-loop")
[ "$RC" = "1" ] || { echo "FAIL case13 expected exit 1 (missing artifact), got $RC"; cat "${WORK}/stderr.log"; exit 1; }
grep -q "tracker comment" "$STUB_LOG" || { echo "FAIL case13: missing loop verdict must post a failure comment"; cat "$STUB_LOG"; exit 1; }

echo PASS
```

   (Remove the pre-existing standalone `echo PASS` line that previously ended the
   file, since it now appears once at the end of this appended block.)

2. Run it directly:
   ```bash
   bash tests/test_verdict_gate_check.sh
   ```
   Expected: `PASS` printed once, no `FAIL` lines.

3. This file is already enumerated explicitly in `.github/workflows/ci.yml` — no CI
   file change needed. Confirm:
   ```bash
   grep -n "test_verdict_gate_check.sh" .github/workflows/ci.yml
   ```
   Expected: one match, unchanged.

4. Commit:
   ```bash
   git add tests/test_verdict_gate_check.sh
   git commit -m "test(gates): integration-test verifier.py through the real verdict_gate_check.sh (#197)"
   ```

---

## Task 8: Wire `assert_verifier_independent` into `adapter.py::load()` (gated on #301)

**Files:** `scripts/factory_core/adapter.py` (modified), `tests/test_adapter.py` (modified)

**Gating note:** #301's nested loop schema (`discovery`/`handoff`/`verification`/
`persistence`/`scheduling`) has not merged as of this plan being written —
`scripts/factory_core/adapter.py` on `main` still validates the flat v2 shape
(`name`/`purpose`/`trigger`/`inputs`/`outputs`/`artifacts`/`verifier`/`stop_condition`/
`failure_behavior`/`side_effect_level`/`handoff`, all in `_LOOP_REQUIRED_FIELDS`). The
issue's own `Depends on: #301` line means the scheduler will not dispatch this ticket's
implementation until #301 is Done, so by the time this task is actually executed #301
should already be on `main` — but **before writing a single line of this task**, re-run
this check and treat its output as authoritative over the description below:

```bash
git fetch origin main
git show origin/main:scripts/factory_core/adapter.py | grep -n "_LOOP_REQUIRED_FIELDS\|_validate_loop\|discovery\|verification"
```

If `main` still shows the flat v2 shape (no `discovery`/`verification`/`handoff`-nested
blocks), **stop this task**, leave `verifier.assert_verifier_independent` as an
unwired, independently-tested function (Task 5 already covers it against #301's
documented shape), and note in the plan-completion comment that Task 8 is deferred
until #301 lands. Do not invent a call site against a schema that doesn't exist on
`main` yet.

If `main` shows the nested shape matching #301's approved spec, proceed:

### TDD Steps

1. Add a failing test to `tests/test_adapter.py` (adjust the loop-entry fixture field
   names to whatever `main`'s actual merged `_validate_loop` requires, confirmed in the
   gating check above — the shape below assumes #301 landed exactly as its spec
   documents):

```python
def test_adapter_load_rejects_loop_whose_verifier_is_its_own_manifest(tmp_path):
    d = tmp_path
    (d / ".factory").mkdir()
    (d / ".factory" / "adapter.yaml").write_text("""
loops:
  - name: nightly-scan-triage
    discovery: {trigger: "cron:0 6 * * *", inputs: ["scripts/scanner.py"]}
    handoff: {outputs: ["artifacts/scan-report.md"], manifest: "artifacts/manifest.json"}
    verification: {verifier: "artifacts/manifest.json", stop_condition: "manifest present"}
    persistence: {artifacts: ["artifacts/scan-history.jsonl"]}
    scheduling: {failure_behavior: "retry-once"}
    side_effect_level: 2
""")
    with pytest.raises(adapter.AdapterError, match=r"verifier.*must not be a path the loop"):
        adapter.load(str(d))


def test_adapter_load_accepts_loop_with_independent_verifier(tmp_path):
    d = tmp_path
    (d / ".factory").mkdir()
    (d / ".factory" / "adapter.yaml").write_text("""
loops:
  - name: nightly-scan-triage
    discovery: {trigger: "cron:0 6 * * *", inputs: ["scripts/scanner.py"]}
    handoff: {outputs: ["artifacts/scan-report.md"], manifest: "artifacts/manifest.json"}
    verification: {verifier: "scripts/verify-scan.sh", stop_condition: "manifest present"}
    persistence: {artifacts: ["artifacts/scan-history.jsonl"]}
    scheduling: {failure_behavior: "retry-once"}
    side_effect_level: 2
""")
    merged = adapter.load(str(d))
    assert merged["loops"][0]["name"] == "nightly-scan-triage"
```

2. Verify they fail (the independence check isn't wired in yet).

3. In `scripts/factory_core/adapter.py`, add the import and the one-line additive
   call immediately after the existing `_validate_loop(entry, i)` call inside `load()`:

```python
from . import adapter_defaults
from . import verifier as _verifier
```

   ```python
       for i, entry in enumerate(data["loops"]):
           _validate_loop(entry, i)
           try:
               _verifier.assert_verifier_independent(entry)
           except _verifier.VerifierError as exc:
               raise AdapterError(str(exc)) from exc
           name = entry.get("name")
   ```

4. Run the new tests plus the full existing `test_adapter.py`:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_adapter.py -x -q
   ```
   Expected: all pass, including every pre-existing loop-validation test unmodified.

5. Commit:
   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(gates): wire assert_verifier_independent into adapter.py::load() (#197, post-#301)"
   ```

---

## Final verification checklist

1. Run the full suite (matching `.github/workflows/ci.yml:13-14`'s `PYTHONPATH=scripts`
   env — `tests/conftest.py` does not add `scripts/` to `sys.path` itself):
   ```bash
   cd /workspace/dark-factory
   PYTHONPATH=scripts python -m pytest tests/ -v
   ```
2. Run the shell tests CI runs explicitly (at minimum the modified/relevant ones):
   ```bash
   bash tests/test_verdict_gate_check.sh
   bash smoke_gate.sh || true   # sanity, not part of this ticket's file list
   ```
3. Run the DAG checks (no `workflows/*.yaml` change expected, but confirm untouched).
   Use `origin/main` (fetched), not local `main`, so a stale local branch can't
   produce a false clean/dirty result (per `.archon/memory/codebase-patterns.md`'s
   git-diff-base guidance):
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   git fetch origin main
   git diff --stat origin/main HEAD -- workflows/  # expect no output
   ```
4. Confirm the "not in this ticket's file list" set is untouched:
   ```bash
   git diff --stat origin/main HEAD -- scripts/verdict_gate_check.sh scripts/gate_blast_radius.py \
     scripts/budget_gate.sh scripts/push_gate_check.sh scripts/oos_excise.sh \
     config/config.yaml workflows/
   ```
   Expected: no output.
5. Confirm acceptance criteria:
   - AC1 (Requirement 7): all four pairs reference `/opt/refinement-skills/VERIFIER-CONTRACT.md`
     (proven by `tests/test_verifier_contract_doc_referenced.py`); verdict producers
     parse/emit through `verdict.py`; golden corpus green.
   - AC2/AC3: `tests/test_verifier.py` + Task 7's integration test demonstrate a
     target verifier's verdict is parsed, recorded, and fails closed through the
     real `verdict_gate_check.sh`.
6. Reminder for the *implement* phase (not this plan phase, per
   `.archon/memory/codebase-patterns.md`'s `[PATTERN]` on #42): when this plan and
   its spec are executed on `feat/issue-197-*`, that phase must itself copy
   `docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md` and this
   plan file onto the implement branch and commit them — they do not transfer
   automatically from the `refine/issue-197-*` branch.

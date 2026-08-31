# Implementation Plan: Handoff manifest (A5) review follow-ups — tests, docs, edge cases

**Issue:** omniscient/dark-factory#381
**Spec:** `docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md`
**Depends on:** nothing — builds against `main` as it exists today (PR #380 / issue #199 already merged).

---

## Goal

Harden the already-shipped A5 handoff-manifest intake path (`scripts/factory_core/handoff.py`)
with the six advisory follow-ups from the independent operator review of PR #380: three missing
reject-path tests, one undocumented reason code, one incomplete label deny-list, one real (if
narrow) verdict-filename collision, one leftover cross-domain memory entry, and one comment nit.
No accept-path behavior change; no new schema fields; no new reason codes beyond documenting one
(`internal_error`) that already exists in code.

## Architecture

```
scripts/factory_core/handoff.py
  + import hashlib
  + _verdict_filename(producing_loop, artifact_id) -> str   (new helper, item 4)
      sha256("<producing_loop>\0<artifact_id>")[:16] suffix closes the charset collision
      _ID_RE permits inside either field ("-" is legal in both).
  intake()
      verdict_out = ... f"loop-{producing_loop}-{artifact_id}.md"   -- replaced by --
      verdict_out = ... _verdict_filename(producing_loop, artifact_id)
      FACTORY_MANIFEST_LABEL validation                              (item 3: + deny-list)
      two `_record_intake(...)` calls in except arms                 (item 6: + comment)

tests/test_handoff.py
  + test_verdict_filename_closes_charset_collision / _is_deterministic   (item 4)
  ~ test_intake_accepts_and_creates_issue's hardcoded filename assertion  (item 4, regex now)
  + test_intake_rejects_gate_shaped_manifest_label_override (parametrized)  (item 3)
  + test_intake_rejects_malformed_manifest_label_override_as_internal_error (parametrized)  (item 1)
  + test_intake_records_internal_error_for_unwritable_artifacts_dir          (item 1)
  + test_default_create_issue_timeout_expired_fails_closed                  (item 1)

docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md
  + `internal_error` row in the Reason codes table                    (item 2)
  ~ two stale `$ARTIFACTS_DIR/loop-<producing_loop>.md` refs -> _verdict_filename shape (item 4)

docs/adapter-authoring-guide.md
  + `internal_error` row in the Handoff manifest (A5) reason-code table  (item 2)

tests/test_adapter_authoring_guide.py
  + "internal_error" token in test_guide_documents_handoff_manifest_a5_section  (item 2)

docs/triage-labels.md
  ~ manifest-intake row: + one sentence on the deny-list                (item 3)

.archon/memory/codebase-patterns.md
  - one leftover MarketHawk `exit_date` entry                          (item 5)
```

## Tech Stack

- Python stdlib only (`hashlib`, already-used `re`/`subprocess`) — matches every other
  `scripts/factory_core/*.py` module; no new dependency.
- `pytest` for `tests/test_handoff.py` (additive cases + one assertion update) and
  `tests/test_adapter_authoring_guide.py` (one additive token). Existing framework, existing
  hermetic fixtures (`_hermetic_run_record` autouse fixture, `tests/fixtures/verifiers/*.sh`).

## File Structure

| File | Change |
|---|---|
| `docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md` | **Copied** (Task 0) — this ticket's own spec, refine-branch-only, doesn't exist on `main` |
| `docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md` | **Copied** (Task 0) — this plan file, same reason |
| `scripts/factory_core/handoff.py` | **Modified** — `_verdict_filename` helper, deny-list check, comment nit |
| `tests/test_handoff.py` | **Modified** — 6 new tests, 1 updated assertion, `import re` added |
| `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md` | **Modified** — `internal_error` row, 2 stale filename refs fixed |
| `docs/adapter-authoring-guide.md` | **Modified** — `internal_error` row |
| `tests/test_adapter_authoring_guide.py` | **Modified** — 1 additive token |
| `docs/triage-labels.md` | **Modified** — 1 sentence on the `manifest-intake` row |
| `.archon/memory/codebase-patterns.md` | **Modified** — 1 line removed |

Not touched: `scripts/factory_core/verifier.py`, `scripts/factory_core/verdict.py`,
`scripts/factory_core/adapter.py`, `scripts/factory_core/providers/cli.py`, `.factory/adapter.yaml`,
any schema/CLI surface, `HANDOFF_ACCEPT_STATUSES`, the R2-R6 sequencing.

---

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md`,
`docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md`

Per the accumulated `[PATTERN]` memory lesson (issue #42): when a refine-phase spec/plan was
approved on a sibling `refine/issue-N-...` branch, the implement phase's `feat/issue-N-...`
branch forks from `main` (see `workflows/archon-dark-factory.yaml`'s `setup-branch` step) — the
refine branch's own commits, including this ticket's own new spec
(`docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md`, not yet on `main`)
and this plan document, do **not** transfer automatically. Gate 2 (conformance,
`commands/dark-factory-conformance.md` Phase 2) locates the spec by scanning
`docs/superpowers/specs/` in the local clone (or a path parsed from the "Plan Generated" issue
comment) — if this file is missing from the feat branch, conformance falls back to
`NO_SPEC=true` advisory-only review instead of checking against the real spec. Copy both files
onto the feat branch and commit them before starting Task 1.

(Note: `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md` — the A5
design spec that Task 4 edits — is already merged to `main` via #199/PR #380, so it needs no
copy step; only this ticket's *own* 2026-08-31 spec and this plan file are refine-branch-only.)

### Steps

1. Determine the refine branch name the same way `workflows/archon-dark-factory.yaml`'s
   `setup-refine-branch` step computed it (issue number + slugified title), and copy the two
   files from it:

```bash
ISSUE=381
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md \
  docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-381-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md \
  docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md
```

2. Verify both files landed:

```bash
test -f docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md && \
test -f docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md && \
echo OK
```

   Expected: `OK`.

3. Commit:

```bash
git add docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md \
  docs/superpowers/plans/2026-08-31-handoff-a5-review-followups-plan.md
git commit -m "docs(#381): copy spec/plan onto the implementation branch"
```

---

## Task 1: `_verdict_filename` helper — close the verdict-filename charset collision (item 4)

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Add two failing tests to `tests/test_handoff.py`, immediately after
   `test_cross_check_rejects_factory_owned_level` (currently ends at line 374) and before
   `test_render_body_contains_origin_banner`:

```python
def test_verdict_filename_closes_charset_collision():
    # _ID_RE (^[A-Za-z0-9._-]+$) permits "-" inside either field, so a fixed separator
    # alone can't distinguish these two pairs -- the hash suffix must.
    a = handoff._verdict_filename("a-b", "c")
    b = handoff._verdict_filename("a", "b-c")
    assert a != b


def test_verdict_filename_is_deterministic():
    first = handoff._verdict_filename("nightly-scan-triage", "scan-2026-08-30-001")
    second = handoff._verdict_filename("nightly-scan-triage", "scan-2026-08-30-001")
    assert first == second
```

2. Confirm red:

```bash
python -m pytest tests/test_handoff.py -k test_verdict_filename -v
```

   Expected: 2 failures, `AttributeError: module 'factory_core.handoff' has no attribute
   '_verdict_filename'`.

3. Implement in `scripts/factory_core/handoff.py`. Add the import (alphabetical, between
   `argparse` and `json`):

```python
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
```

4. Add the helper immediately after `cross_check` (currently ends at line 260) and before
   `render_body` (currently starts at line 263):

```python
def _verdict_filename(producing_loop: str, artifact_id: str) -> str:
    """Deterministic, collision-free verdict filename. `_ID_RE` permits "-" inside both
    producing_loop and artifact_id, so a fixed separator alone can't distinguish
    ("a-b", "c") from ("a", "b-c") -- the `\\0` byte (excluded by _ID_RE from both
    fields) makes the hash input injective. Hashing the *rendered* stem
    (producing_loop + "-" + artifact_id) would just reproduce the same ambiguity this
    exists to close."""
    digest = hashlib.sha256(f"{producing_loop}\0{artifact_id}".encode("utf-8")).hexdigest()[:16]
    stem = f"loop-{producing_loop}-{artifact_id}"[:200]
    return f"{stem}-{digest}.md"
```

5. Wire it into `intake()`. Replace:

```python
        verdict_out = os.path.join(artifacts_dir, f"loop-{producing_loop}-{artifact_id}.md")
```

   with:

```python
        verdict_out = os.path.join(artifacts_dir, _verdict_filename(producing_loop, artifact_id))
```

   and update the comment immediately above that line (currently reads "artifact_id (not just
   producing_loop) is in the filename so a second manifest handed off from the same producing
   loop into the same ARTIFACTS_DIR cannot overwrite the first verdict file out from under an
   issue that already cites it (both fields are charset-validated to ^[A-Za-z0-9._-]+$ by
   validate_manifest, R2)."). New text:

```python
        # artifact_id (not just producing_loop) is in the filename so a second manifest
        # handed off from the same producing loop into the same ARTIFACTS_DIR cannot
        # overwrite the first verdict file out from under an issue that already cites it;
        # _verdict_filename's hash suffix closes the remaining charset-collision case
        # (both fields are charset-validated to ^[A-Za-z0-9._-]+$ by validate_manifest, R2).
        verdict_out = os.path.join(artifacts_dir, _verdict_filename(producing_loop, artifact_id))
```

6. Confirm the two new tests go green:

```bash
python -m pytest tests/test_handoff.py -k test_verdict_filename -v
```

   Expected: 2 passed.

7. Step 5's change breaks the existing hardcoded-filename assertion in
   `test_intake_accepts_and_creates_issue`. Add `import re` to the top of
   `tests/test_handoff.py` (with the other stdlib imports: `json, os, pathlib, re, sys`), then
   replace:

```python
    verdict_path = artifacts_dir / "loop-nightly-scan-triage-scan-2026-08-30-001.md"
    assert verdict_path.exists()
    assert "STATUS: PASS" in verdict_path.read_text()
```

   with:

```python
    matches = list(artifacts_dir.glob("loop-nightly-scan-triage-scan-2026-08-30-001-*.md"))
    assert len(matches) == 1
    verdict_path = matches[0]
    assert re.fullmatch(
        r"loop-nightly-scan-triage-scan-2026-08-30-001-[0-9a-f]{16}\.md", verdict_path.name
    )
    assert "STATUS: PASS" in verdict_path.read_text()
```

8. Verify:

```bash
python -m pytest tests/test_handoff.py -v
```

   Expected: all tests in the file pass (this also proves step 7's fix, since that test was
   red immediately after step 5).

9. Commit:

```bash
git add scripts/factory_core/handoff.py tests/test_handoff.py
git commit -m "fix(#381): close verdict-filename charset collision with a sha256 suffix"
```

---

## Task 2: Deny-list gate labels in the `FACTORY_MANIFEST_LABEL` override (item 3)

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Add a failing parametrized test to `tests/test_handoff.py`, immediately after
   `test_intake_manifest_label_env_override` (currently ends at line 584):

```python
@pytest.mark.parametrize("label", [
    "ready-for-agent",
    "READY-FOR-AGENT",
    "spec-pending-review",
    "plan-pending-review",
    "triage-pending-review",  # same *-pending-review shape, not one of today's two literals
])
def test_intake_rejects_gate_shaped_manifest_label_override(tmp_path, monkeypatch, label):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", label)
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []
```

2. Confirm red:

```bash
python -m pytest tests/test_handoff.py -k test_intake_rejects_gate_shaped_manifest_label_override -v
```

   Expected: failures for all 5 params (`Failed: DID NOT RAISE`) since none of these labels
   currently trip the comma/whitespace/empty check.

3. Implement in `scripts/factory_core/handoff.py`. Replace:

```python
        if not FACTORY_MANIFEST_LABEL or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL):
            raise ValueError(
                f"FACTORY_MANIFEST_LABEL override must be a single label with no comma "
                f"or whitespace, got: {FACTORY_MANIFEST_LABEL!r}"
            )
```

   with:

```python
        _manifest_label = FACTORY_MANIFEST_LABEL.lower()
        if (
            not FACTORY_MANIFEST_LABEL
            or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL)
            or _manifest_label == "ready-for-agent"
            or _manifest_label.endswith("-pending-review")
        ):
            raise ValueError(
                f"FACTORY_MANIFEST_LABEL override must be a single label with no comma "
                f"or whitespace, and must not be a gate label (ready-for-agent or "
                f"*-pending-review), got: {FACTORY_MANIFEST_LABEL!r}"
            )
```

   and extend the comment immediately above that block (currently ends "...which
   docs/triage-labels.md requires never be applied together with manifest-intake. Reject
   before building the label string.") by appending:

```python
        # Also reject the override being SET to a gate label itself (ready-for-agent, or
        # any *-pending-review shape, case-folded -- scheduler.sh matches gate labels with
        # grep -qi at scheduler.sh:1144/1209) so a misconfigured override can't smuggle a
        # manifest-intake issue into an existing gate state.
```

4. Confirm green:

```bash
python -m pytest tests/test_handoff.py -v
```

   Expected: all tests pass, including the existing `test_intake_manifest_label_env_override`
   (`custom-intake` — proves the deny-list doesn't over-match an unrelated override).

5. Commit:

```bash
git add scripts/factory_core/handoff.py tests/test_handoff.py
git commit -m "fix(#381): reject ready-for-agent and *-pending-review in FACTORY_MANIFEST_LABEL"
```

---

## Task 3: Reject-path test coverage for `internal_error` / `issue_create_failed` (item 1)

**Files:** `tests/test_handoff.py`

These three paths already execute correctly in production; this task is pure test-coverage
addition with **no production-code change**, so there is no red step against `handoff.py` — each
test should pass on first run against the code as it stands after Task 2.

### Steps

1. Add three tests to `tests/test_handoff.py`, immediately after Task 2's
   `test_intake_rejects_gate_shaped_manifest_label_override`:

```python
@pytest.mark.parametrize("label", ["needs,extra", "has space", ""])
def test_intake_rejects_malformed_manifest_label_override_as_internal_error(tmp_path, monkeypatch, label):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", label)
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []

    # R6: a malformed override must still produce an auditable runs.jsonl row, not just
    # a raised exception -- the autouse _hermetic_run_record fixture already points
    # JSONL_PATH at tmp_path / "runs.jsonl", mirroring test_intake_records_runs_jsonl_row_on_reject.
    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "internal_error"
    assert rec["detail"]["created_issue"] == ""


def test_intake_records_internal_error_for_unwritable_artifacts_dir(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    # A file (not a directory) sitting at the artifacts-dir path makes
    # os.makedirs(..., exist_ok=True) raise FileExistsError (an OSError) -- the same
    # failure shape as a genuinely unwritable/read-only ARTIFACTS_DIR mount.
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.write_text("not a directory")

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(artifacts_dir),
            create_issue=_stub_create_issue(),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"

    # R6, same as above: the OSError arm must still write a runs.jsonl row. This file
    # lives under tmp_path directly (not under the unwritable artifacts_dir), so the
    # write is unaffected by the failure being tested.
    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "internal_error"


def test_default_create_issue_timeout_expired_fails_closed(monkeypatch):
    def fake_run(argv, **kw):
        raise handoff.subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 300))

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    assert handoff._default_create_issue("t", "b", "needs-triage,manifest-intake") == ""
```

2. Run and confirm all three pass immediately (no code change expected):

```bash
python -m pytest tests/test_handoff.py -k "internal_error or timeout_expired" -v
```

   Expected: 5 passed (3 parametrized `internal_error`-override cases + the unwritable-dir case
   + the TimeoutExpired case).

3. Run the full file once more to confirm no regressions:

```bash
python -m pytest tests/test_handoff.py -v
```

4. Commit:

```bash
git add tests/test_handoff.py
git commit -m "test(#381): cover the internal_error and TimeoutExpired reject paths"
```

---

## Task 4: Document `internal_error` + fix stale filename references (item 2 + item 4 docs)

**Files:** `tests/test_adapter_authoring_guide.py`, `docs/adapter-authoring-guide.md`,
`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`

### TDD Steps

1. Add the doc-guard token first (red): in `tests/test_adapter_authoring_guide.py`, in
   `test_guide_documents_handoff_manifest_a5_section`'s token tuple, add `"internal_error"`
   immediately after `"issue_create_failed",`:

```python
        "schema_invalid", "unsafe_string", "body_contains_fence", "body_too_large",
        "issue_create_failed", "internal_error",
    ):
```

2. Confirm red:

```bash
python -m pytest tests/test_adapter_authoring_guide.py -k test_guide_documents_handoff_manifest_a5_section -v
```

   Expected: fail, `AssertionError: missing A5 token: internal_error`.

3. Add the row to `docs/adapter-authoring-guide.md`'s "Handoff manifest (A5)" reason-code table
   (currently lines 254-265), immediately after the `issue_create_failed` row:

```markdown
| `issue_create_failed` | `create_issue` returned an empty/falsy result |
| `internal_error` | Any failure that is not itself an R2-R5 manifest rejection (e.g. an unwritable `--artifacts-dir`, a malformed `FACTORY_MANIFEST_LABEL` override) — still produces a `runs.jsonl` row (R6), fail-closed |
```

4. Confirm green:

```bash
python -m pytest tests/test_adapter_authoring_guide.py -v
```

5. Add the matching row to the A5 design spec's own reason-code table in
   `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md` (currently lines
   143-154), immediately after the `issue_create_failed` row:

```markdown
| `issue_create_failed` | `create_issue` returned empty/falsy (R5) |
| `internal_error` | Any failure that is not itself an R2-R5 manifest rejection (e.g. an unwritable `--artifacts-dir`, a malformed `FACTORY_MANIFEST_LABEL` override) — still produces a `runs.jsonl` row (R6), fail-closed |
```

6. Fix the two stale filename references in the same spec file (already out of date against
   the merged #380 code, before this ticket's own change). First, the R4 section (currently
   spec lines 187-190):

   Replace:

   ```
   clone-relative path safety, missing/non-executable/timeout → `BLOCKED`, `ERROR` → `BLOCKED`,
   exit-code-wins-over-status, level ≥ 4 → `BLOCKED`). The verdict is written to
   `$ARTIFACTS_DIR/loop-<producing_loop>.md` (factory-owned, outside the clone; never one of
   `verifier._RESERVED_OUT_BASENAMES`), and *that* path — not the manifest's `verifier_verdict.path`
   ```

   Replace with:

   ```
   clone-relative path safety, missing/non-executable/timeout → `BLOCKED`, `ERROR` → `BLOCKED`,
   exit-code-wins-over-status, level ≥ 4 → `BLOCKED`). The verdict is written to
   `$ARTIFACTS_DIR/<filename>` where `<filename>` is `_verdict_filename(producing_loop,
   artifact_id)` — `loop-<producing_loop>-<artifact_id>-<16-hex-char-sha256-digest>.md`
   (factory-owned, outside the clone; never one of `verifier._RESERVED_OUT_BASENAMES`), and
   *that* path — not the manifest's `verifier_verdict.path`
   ```

7. Second stale reference, in the R5 issue-body-shape block (currently around line 269):

   Replace:

   ```
   - Verifier verdict: `$ARTIFACTS_DIR/loop-<producing_loop>.md` — STATUS: PASS (produced by intake, R4)
   ```

   with:

   ```
   - Verifier verdict: `$ARTIFACTS_DIR/<_verdict_filename(producing_loop, artifact_id)>` — STATUS: PASS (produced by intake, R4)
   ```

8. Run the full suite once more (docs-only changes shouldn't affect anything but the guard
   test):

```bash
python -m pytest tests/test_adapter_authoring_guide.py tests/test_handoff.py -v
```

9. Commit:

```bash
git add tests/test_adapter_authoring_guide.py docs/adapter-authoring-guide.md \
  docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md
git commit -m "docs(#381): document internal_error reason code, fix stale verdict-filename refs"
```

---

## Task 5: Remove leftover MarketHawk memory entry (item 5)

**Files:** `.archon/memory/codebase-patterns.md`

No test covers this file's content (verified: only structural/routing tests reference
`codebase-patterns.md` by name, not by content — `tests/test_memory_write.py`,
`tests/test_memory_retrieve.py`, `tests/test_conformance_memory_write.sh`). Direct edit only.

### Steps

1. Remove the single leftover line (currently present in the file):

```
- [AVOID] exit_date omission: always assign exit_date_val after _simulate_trade returns — unassigned date fields silently persist NULL <!-- issue:#301 date:2026-06-13 expires:2026-12-13 source:conformance path:backend/app/services/ -->
```

   This is a MarketHawk-domain (`backend/app/services/`) entry that commit `3f66e27` intended
   to prune alongside three siblings from the same `#301` batch but missed — dark-factory-domain
   memory carrying zero relevance to this repo.

2. Verify the line is gone and the file still parses as expected by its own tests:

```bash
grep -c "exit_date omission" .archon/memory/codebase-patterns.md   # expect: 0
python -m pytest tests/test_memory_write.py tests/test_memory_retrieve.py -v
```

3. Commit:

```bash
git add .archon/memory/codebase-patterns.md
git commit -m "chore(#381): remove leftover MarketHawk exit_date memory entry"
```

---

## Task 6: Deny-list doc sentence + comment nit (item 3 doc + item 6)

**Files:** `docs/triage-labels.md`, `scripts/factory_core/handoff.py`

No test covers either change (a doc sentence and a code comment); direct edits.

### Steps

1. In `docs/triage-labels.md`'s Workflow flags table, extend the existing `manifest-intake`
   row (currently: `| \`manifest-intake\` | Applied by \`handoff.py intake\` (A5) alongside
   \`needs-triage\` on every GitHub issue created from a target-loop artifact handoff manifest.
   Env-overridable via \`FACTORY_MANIFEST_LABEL\`. Never applied together with
   \`ready-for-agent\` — a manifest-created issue always starts at triage. |`) by appending a
   sentence:

```markdown
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. The `FACTORY_MANIFEST_LABEL` override itself rejects `ready-for-agent` and any `*-pending-review` label (case-folded), so it can never be set to smuggle an issue into an existing gate state. |
```

2. In `scripts/factory_core/handoff.py`'s `intake()`, add a one-line comment above each of the
   two `_record_intake(...)` calls in the `except` arms (currently the calls inside `except
   HandoffError as exc:` and `except Exception as exc:`). Above the first (in `except
   HandoffError as exc:`):

```python
    except HandoffError as exc:
        # If _record_intake itself raises (e.g. an unwritable ledger), that new exception
        # replaces this one -- fail-closed but rowless; acceptable, not a bug.
        _record_intake(
            manifest_path=manifest_path, artifact_id=artifact_id, producing_loop=producing_loop,
            issue=0, verdict="REJECTED", reject_reason=exc.code, created_issue="",
            verdict_path=verdict_out or "", reason=exc.message,
        )
        raise
```

   Above the second (in `except Exception as exc:`), same comment, inserted between the
   existing 4-line rationale comment already there and the `_record_intake(...)` call (do not
   remove the existing comment — this block currently reads, unchanged through the `except
   Exception as exc:` line and the 4-line comment, then gets the new 2-line comment inserted
   immediately before `_record_intake(`):

```python
    except Exception as exc:
        # Anything other than HandoffError here (os.makedirs/open() raising OSError on
        # e.g. a read-only ARTIFACTS_DIR mount, or a config error like the label check
        # above) must still close the same audit gap the AdapterError branch above
        # closes: a runs.jsonl row, not an uncaught traceback and no trace at all.
        # If _record_intake itself raises (e.g. an unwritable ledger), that new exception
        # replaces this one -- fail-closed but rowless; acceptable, not a bug.
        _record_intake(
            manifest_path=manifest_path, artifact_id=artifact_id, producing_loop=producing_loop,
            issue=0, verdict="REJECTED", reject_reason="internal_error", created_issue="",
            verdict_path=verdict_out or "", reason=str(exc),
        )
        raise HandoffError("internal_error", f"unexpected error during intake: {exc}") from exc
```

3. Run the full file's tests once more to confirm the comment-only change didn't break
   anything:

```bash
python -m pytest tests/test_handoff.py -v
```

4. Commit:

```bash
git add docs/triage-labels.md scripts/factory_core/handoff.py
git commit -m "docs(#381): note the manifest-label deny-list; comment the rowless fail-closed case"
```

---

## Task 7: Full verification pass

**Files:** none (verification only)

### Steps

1. Run the full test suite (matches CI exactly, per `CLAUDE.md`):

```bash
python -m pytest tests/ -v
```

   Expected: all tests pass, including every test added/modified in Tasks 1-4.

2. Confirm no out-of-scope files were touched:

```bash
git diff origin/main...HEAD --stat
```

   Expected file list: exactly the seven files in the File Structure table above, plus the two
   files Task 0 copied onto this branch (this plan doc and this ticket's own spec, both under
   `docs/superpowers/`) — those two are legitimately part of this diff since `feat/issue-381-*`
   forked from `main`, which never had them.

3. No commit needed for this task — verification only.

# Implementation Plan: Harden the `FACTORY_MANIFEST_LABEL` override guard

**Issue:** omniscient/dark-factory#384
**Spec:** `docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md`
**Depends on:** #381 (shipped, PR #383) — the guard this ticket hardens already exists on `main`.

---

## Goal

Harden the `FACTORY_MANIFEST_LABEL` override guard in `scripts/factory_core/handoff.py::intake()`
per the three Gate-3 advisories deferred from PR #383: switch the deny check from exact/suffix
matching to substring containment (matching the scheduler's own unanchored `grep -qi`), add
`direct-to-pr` (plus any env-configured `DIRECT_TO_PR_LABEL` rename) to the deny-list, and hoist
the whole validation block to the top of `intake()` so a misconfigured override fails before
paying a verifier subprocess or writing an orphan verdict file. No behavior change to any other
`intake()` code path; no changes to `scheduler_lib.sh`/`scheduler.sh`.

## Architecture

```
scripts/factory_core/handoff.py :: intake()
  try:
      artifact_id = "unknown"; producing_loop = None; verdict_out = None
      + [RELOCATED] FACTORY_MANIFEST_LABEL / DIRECT_TO_PR_LABEL containment guard
        (runs first -- depends only on process env, never on manifest content)
      manifest = read_manifest(...)
      ...
      run_verifier(...)                    <- guard now runs *before* this, not after
      ...
      - [REMOVED FROM HERE] old exact/suffix guard (was just before `labels = ...`)
      labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
      issue_id = create_issue(...)
  except HandoffError / except Exception:
      _record_intake(...)                  <- unchanged failure contract (R6)
```

## Tech Stack

Python stdlib only (`os`, `re` — both already imported in `handoff.py`); `pytest` for
`tests/test_handoff.py`. No new dependencies, no new files.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/handoff.py` | **Modified** — relocate + rewrite the deny-list check |
| `tests/test_handoff.py` | **Modified** — additive containment/direct-to-pr/hoisting test cases |
| `docs/triage-labels.md` | **Modified** — `manifest-intake` row describes the new containment deny-list |

Not touched: `scripts/scheduler_lib.sh`, `scheduler.sh`, `.factory/adapter.yaml`, any other
`scripts/factory_core/*.py` module.

---

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md`,
`docs/superpowers/plans/2026-08-31-harden-factory-manifest-label-guard-plan.md`

Per the `[PATTERN]` memory lesson (issue #42) and the same Task 0 that #381's plan needed: the
implement phase's `feat/issue-384-...` branch forks from `main`, so this ticket's own spec and
this plan file (both refine-branch-only, not on `main`) do **not** transfer automatically. Gate 2
(conformance) locates the spec by scanning `docs/superpowers/specs/` in the local clone — if the
file is missing from the feat branch, conformance falls back to `NO_SPEC=true` advisory-only
review instead of checking against the real spec. Copy both files onto the feat branch and
commit them before starting Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=384
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md \
  docs/superpowers/plans/2026-08-31-harden-factory-manifest-label-guard-plan.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-384-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md \
  docs/superpowers/plans/2026-08-31-harden-factory-manifest-label-guard-plan.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md && \
test -f docs/superpowers/plans/2026-08-31-harden-factory-manifest-label-guard-plan.md && echo OK
git add docs/superpowers/specs/2026-08-31-harden-factory-manifest-label-guard-design.md \
  docs/superpowers/plans/2026-08-31-harden-factory-manifest-label-guard-plan.md
git commit -m "docs(#384): copy spec/plan onto the implementation branch"
```

---

## Task 1: Failing tests for containment, `direct-to-pr`, and hoisting

**Files:** `tests/test_handoff.py` (modified)

### TDD Steps

1. Extend the existing gate-shaped-override parametrize list (around line 606) to cover
   substring containment (not just exact-match/suffix) and the new `direct-to-pr` shape.
   Replace:

```python
@pytest.mark.parametrize("label", [
    "ready-for-agent",
    "READY-FOR-AGENT",
    "spec-pending-review",
    "plan-pending-review",
    "triage-pending-review",  # same *-pending-review shape, not one of today's two literals
])
def test_intake_rejects_gate_shaped_manifest_label_override(tmp_path, monkeypatch, label):
```

   with:

```python
@pytest.mark.parametrize("label", [
    "ready-for-agent",
    "READY-FOR-AGENT",
    "spec-pending-review",
    "plan-pending-review",
    "triage-pending-review",  # same *-pending-review shape, not one of today's two literals
    "manifest-intake-ready-for-agent",  # substring containment, not exact match (Gate-3 finding 1)
    "xx-pending-review-yy",  # containment anywhere in the string, not just a suffix (finding 1)
    "direct-to-pr",  # Gate-3 finding 2
    "DIRECT-TO-PR",
    "manifest-intake-direct-to-pr",  # substring containment of direct-to-pr
])
def test_intake_rejects_gate_shaped_manifest_label_override(tmp_path, monkeypatch, label):
```

   (Function body is unchanged — only the parametrize list grows.)

2. Add three new tests directly after
   `test_intake_rejects_gate_shaped_manifest_label_override`, covering the
   `DIRECT_TO_PR_LABEL` env-override interaction (spec Requirements + Brainstorming Q&A):

```python
def test_intake_rejects_manifest_label_matching_renamed_direct_to_pr_env(tmp_path, monkeypatch):
    """Gate-3 finding 2: an operator-renamed DIRECT_TO_PR_LABEL must also be denied,
    not just the canonical literal."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "ship-it")
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


def test_intake_still_denies_canonical_direct_to_pr_after_env_rename(tmp_path, monkeypatch):
    """A DIRECT_TO_PR_LABEL rename must not un-deny the canonical literal 'direct-to-pr'."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "direct-to-pr")
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


def test_intake_blank_direct_to_pr_label_env_does_not_reject_default_override(tmp_path, monkeypatch):
    """An empty/whitespace-only DIRECT_TO_PR_LABEL must not contribute an empty-string
    needle that vacuously matches (and rejects) every override, including the default
    'manifest-intake'."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "   ")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert result.accepted is True
    assert create_issue.calls[0]["labels"] == "needs-triage,manifest-intake"


def test_intake_nonmatching_direct_to_pr_label_env_does_not_reject_default_override(tmp_path, monkeypatch):
    """Symmetric to the blank-env case above: a DIRECT_TO_PR_LABEL that is set but does
    not appear in the override must not over-match and reject a normal override either."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert result.accepted is True
    assert create_issue.calls[0]["labels"] == "needs-triage,manifest-intake"
```

3. Add one more test directly after `test_intake_rejects_malformed_manifest_label_override_as_internal_error`
   (around line 663), proving both the hoisting (Gate-3 finding 3) and the updated error
   message (spec Requirements: "name all three denied shapes"):

```python
def test_intake_validates_manifest_label_before_reading_manifest(tmp_path, monkeypatch):
    """Gate-3 finding 3: the override check must run before read_manifest(), so a
    misconfigured override is caught without paying manifest/verifier work. Proven by
    pointing manifest_path at a file that doesn't exist: if validation ran after
    read_manifest (the old order), the missing-file check (schema_invalid) would fire
    first instead of the label check (internal_error)."""
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "ready-for-agent")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()

    artifacts_dir = tmp_path / "artifacts"
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), "does-not-exist.yaml", artifacts_dir=str(artifacts_dir),
            create_issue=_stub_create_issue(),
        )
    assert exc.value.code == "internal_error"
    assert "ready-for-agent" in exc.value.message
    assert "pending-review" in exc.value.message
    assert "direct-to-pr" in exc.value.message
    # Gate-3 finding 3's actual harm: no verdict file orphaned on the artifacts mount,
    # because the rejection fires before run_verifier() ever writes one.
    assert not artifacts_dir.exists()
```

4. Run the file and confirm the expected red/green split:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -q
   ```
   Expected: the pre-existing parametrized cases in
   `test_intake_rejects_gate_shaped_manifest_label_override` (the original five labels)
   stay green (today's exact/suffix check already catches them). The five newly added
   parametrize cases (`manifest-intake-ready-for-agent`, `xx-pending-review-yy`,
   `direct-to-pr`, `DIRECT-TO-PR`, `manifest-intake-direct-to-pr`) are red — today's guard
   is exact/suffix-only and doesn't deny `direct-to-pr` at all.
   `test_intake_rejects_manifest_label_matching_renamed_direct_to_pr_env` and
   `test_intake_still_denies_canonical_direct_to_pr_after_env_rename` are red (no
   `DIRECT_TO_PR_LABEL` handling exists yet).
   `test_intake_blank_direct_to_pr_label_env_does_not_reject_default_override` and
   `test_intake_nonmatching_direct_to_pr_label_env_does_not_reject_default_override` are
   green already (nothing reads `DIRECT_TO_PR_LABEL` yet, so neither can reject on it) —
   both are characterization tests that must **stay** green after Task 2, not red ones
   you're fixing.
   `test_intake_validates_manifest_label_before_reading_manifest` is red on the
   `exc.value.code == "internal_error"` assertion specifically: today the guard runs
   after `read_manifest()`, so the missing manifest file raises `schema_invalid` before
   the label is ever checked. The `not artifacts_dir.exists()` assertion already passes
   today too (this rejection path never reaches `run_verifier()`/`os.makedirs` either
   way) — it's a regression guard for Task 2, not part of today's red signal.

5. Do not commit yet — Task 2 makes these green.

---

## Task 2: Relocate and rewrite the deny-list check in `handoff.py`

**Files:** `scripts/factory_core/handoff.py` (modified)

### TDD Steps

1. Remove the old check from its current location (immediately before
   `labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"`, currently lines ~424-446). Delete
   this whole block, comments included:

```python
        # FACTORY_MANIFEST_LABEL is env-supplied (operator/deploy config, not manifest
        # input), but it is interpolated straight into a comma-joined label string that
        # providers/cli.py::_tracker_create splits on "," -- an override containing a
        # comma (e.g. "manifest-intake,ready-for-agent") would silently smuggle in an
        # extra label and could opt a target-loop-authored issue into ready-for-agent,
        # which docs/triage-labels.md requires never be applied together with
        # manifest-intake. Reject before building the label string.
        # Also reject the override being SET to a gate label itself (ready-for-agent, or
        # any *-pending-review shape, lower-cased -- scheduler.sh matches gate labels with
        # grep -qi at scheduler.sh:1144/1209) so a misconfigured override can't smuggle a
        # manifest-intake issue into an existing gate state.
        label_folded = FACTORY_MANIFEST_LABEL.lower()
        if (
            not FACTORY_MANIFEST_LABEL
            or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL)
            or label_folded == "ready-for-agent"
            or label_folded.endswith("-pending-review")
        ):
            raise ValueError(
                f"FACTORY_MANIFEST_LABEL override must be a single label with no comma "
                f"or whitespace, and must not be a gate label (ready-for-agent or "
                f"*-pending-review), got: {FACTORY_MANIFEST_LABEL!r}"
            )
        labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
```

   leaving just:

```python
        labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
```

   in that spot.

2. Insert the relocated, rewritten check at the top of `intake()`'s `try:` block, right
   after the three default assignments and before `manifest = read_manifest(...)`.
   Replace:

```python
    artifact_id = "unknown"
    producing_loop = None
    verdict_out = None
    try:
        manifest = read_manifest(clone_dir, manifest_path)
```

   with:

```python
    artifact_id = "unknown"
    producing_loop = None
    verdict_out = None
    try:
        # FACTORY_MANIFEST_LABEL / DIRECT_TO_PR_LABEL validation depends only on process
        # env, never on manifest content -- run it first so a misconfigured override
        # fails before paying a verifier subprocess or writing an orphan verdict file
        # (Gate-3 finding 3, #384). It is interpolated straight into a comma-joined label
        # string that providers/cli.py::_tracker_create splits on "," -- an override
        # containing a comma or whitespace would silently smuggle in an extra label.
        # Containment (not exact/suffix) matching mirrors scheduler_lib.sh's own
        # unanchored `grep -qi` label matchers (has_opt_in_refine_label,
        # has_direct_to_pr_label, the inline *-pending-review greps), so this guard is at
        # least as strict as the dispatch predicates it defends against (Gate-3 finding
        # 1). direct-to-pr is denied in addition to ready-for-agent/*-pending-review
        # because it is a strictly wider escalation: grace-timer auto-advance past
        # spec/plan review *and* end-gate auto-merge (Gate-3 finding 2). This must stay
        # inside this try so the ValueError is caught by the generic `except Exception`
        # arm below and still produces a runs.jsonl row (R6) + HandoffError, the same
        # failure contract #381 established -- only artifact_id/producing_loop now read
        # their pre-read_manifest defaults for this rejection path (see spec Assumptions).
        label_folded = FACTORY_MANIFEST_LABEL.lower()
        direct_to_pr_folded = os.environ.get("DIRECT_TO_PR_LABEL", "").strip().lower()
        deny_substrings = ["ready-for-agent", "-pending-review", "direct-to-pr"]
        if direct_to_pr_folded:
            deny_substrings.append(direct_to_pr_folded)
        if (
            not FACTORY_MANIFEST_LABEL
            or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL)
            or any(needle in label_folded for needle in deny_substrings)
        ):
            raise ValueError(
                f"FACTORY_MANIFEST_LABEL override must be a single label with no comma or "
                f"whitespace, and must not contain a gate/escalation label shape "
                f"(ready-for-agent, *-pending-review, or direct-to-pr), got: "
                f"{FACTORY_MANIFEST_LABEL!r}"
            )

        manifest = read_manifest(clone_dir, manifest_path)
```

3. Run the full test file:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -q
   ```
   Expected: all tests pass, including every case added in Task 1 and every pre-existing
   test in the file (in particular `test_intake_accepts_and_creates_issue`,
   `test_intake_manifest_label_env_override`, and the two original #381 tests
   `test_intake_rejects_gate_shaped_manifest_label_override` /
   `test_intake_rejects_malformed_manifest_label_override_as_internal_error`, which don't
   assert `artifact_id`/`origin` and so hold unchanged per the spec's Assumptions).

4. Run the full suite to confirm no other test imports or depends on the old check's
   exact position or wording:
   ```bash
   python -m pytest tests/ -q
   ```
   Expected: all tests pass.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "fix(handoff): harden FACTORY_MANIFEST_LABEL guard to containment match, deny direct-to-pr, validate early"
   ```

---

## Task 3: Update `docs/triage-labels.md`

**Files:** `docs/triage-labels.md` (modified)

### TDD Steps (docs-only — no test framework applies; "verify" means re-reading the rendered row)

1. Read the current `manifest-intake` row (line 44):

```
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. The `FACTORY_MANIFEST_LABEL` override itself rejects `ready-for-agent` and any `*-pending-review` label (case-folded), so it can never be set to smuggle an issue into an existing gate state. |
```

2. Replace the last sentence of that row (starting at `The \`FACTORY_MANIFEST_LABEL\` override itself rejects...`) with:

```
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. The `FACTORY_MANIFEST_LABEL` override itself is rejected if its case-folded value *contains* `ready-for-agent`, `-pending-review`, `direct-to-pr`, or the case-folded, non-empty `DIRECT_TO_PR_LABEL` env value (when set) — substring containment, matching the scheduler's own unanchored `grep -qi` label matching — so it can never be set to smuggle an issue into an existing gate or escalation state. |
```

3. Verify by re-reading line 44 of the file and confirming it names all three denied
   shapes (`ready-for-agent`, `-pending-review`, `direct-to-pr`) and describes containment
   (not exact/suffix) matching:
   ```bash
   sed -n '44p' docs/triage-labels.md
   ```

4. Commit:
   ```bash
   git add docs/triage-labels.md
   git commit -m "docs(triage-labels): describe the containment-based manifest-intake deny-list"
   ```

---

## Final verification

```bash
python -m pytest tests/ -v
bash smoke_gate.sh
git diff origin/main...HEAD --stat
```

Both suites must pass with no new failures before publishing. The diff stat must show exactly
the three files Tasks 1-3 modify plus the two docs files Task 0 copied onto this branch
(this plan and this ticket's own spec) — anything else is out of scope.

# Deterministic, Durable Blast-Radius Approval — Implementation Plan

**Issue:** #551

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `HUMAN_REQUIRED` blast-radius verdict can be cleared only by a gpg-signed approval record bound to the exact content of the triggered files. The verdict is enforced by deterministic bash at three points (the new `blast-gate` node, a `push-and-pr` re-check, and the `close-preview` merge check). A hold is not counted as a run failure.

**Architecture:** `scripts/gate_blast_radius.py` gains the spec's match rule (signature verified against `main`'s `.factory/blast-approvers.asc`, then a `<mode> <blob>` binding per triggered path). A new `scripts/blast_gate_check.sh` is the one checker every enforcement point calls. It fetches the trusted base, resolves the head (`run` = HEAD, `close` = the PR branch on origin, in a temporary worktree), runs the baked gate and maps `STATUS:` to exit 0/1/2. With `--hold` it also takes the hold steps. `entrypoint.sh` turns a `blast-hold.json` marker into exit 0 with no error signature. The validate and conformance agents stop deciding anything about the gate.

**Tech Stack:** Python 3.12 (stdlib only plus the existing `yaml`), bash, git plumbing (`ls-tree`, `merge-base`, `worktree`), GnuPG 2.4 (`gnupg` is already in the image, `Dockerfile:15`), `gh pr merge --match-head-commit`, Archon workflow YAML, pytest and the repo's bash test convention.

**Spec:** `docs/superpowers/specs/2026-11-21-blast-radius-durable-approval-design.md` (revised 2026-11-21 after the operator's spec-gate amendments). This plan replaces the one removed in `cf63951`, which was written against the pre-amendment spec (patch-id binding, `verdict_gate_check.sh`, `approve-blast.sh` in factory scope). None of that design survives here.

**Delivery split (spec "Delivery split and authorship").** This plan is the **factory scope only**. It must not create or edit `.claude/skills/operator/**` (including `approve-blast.sh`, `PLAYBOOK.md`, `GOTCHAS.md`) or `.factory/blast-approvers.asc`. Those belong to the separate, hand-reviewed operator PR. Until both PRs are on `main` and the image is republished, every `HUMAN_REQUIRED` diff stays held, which is the safe direction. This PR edits gate files and trips the blast-radius gate under the old flow, so the owner merges it by hand.

**How the code blocks were verified:** every patch below was generated from a working implementation on a scratch worktree at `origin/main` `5724b75`. The plan was then replayed from top to bottom on a fresh worktree. Every red step failed as stated, every green step passed, and the final state passed the full `pytest tests/` run, every `tests/test_*.sh` that CI lists, both workflow-DAG checks and `docs_refs_check.py`. Two CI shell tests, `tests/test_identity.sh` and `tests/test_provider_env_flags.sh`, fail in the factory container on unmodified `main` as well, because the container exports `JIRA_*` and similar variables. They pass on the clean CI runner and are not affected by this plan.

**Applying patches:** each implementation step is one `git apply` heredoc, run from the repo root. If a hunk does not apply because `main` moved, apply the same change by hand. Each patch is small and self-describing, and the step's prose says what it does. Never apply with `--reject` and continue silently.

---

## Plan-time decisions beyond the spec's wording

Each one either fills a gap the spec leaves open or follows the spec's intent more safely than its literal text would. None of them weakens a gate.

1. **The hold steps live in `blast_gate_check.sh --hold`, not in YAML.** Spec C.1-C.6 are needed by `blast-gate` and by the `push-and-pr` re-check ("the same six hold steps"), and F.3 is a subset of them. One flag on the one checker keeps the three enforcement points from drifting, and makes the steps testable by actually executing them (`tests/test_blast_gate_check.sh`). The spec's interface `blast_gate_check.sh --issue <N> --mode run|close [--out <blast.md>]` is unchanged; `--hold` is additive. On stdout the checker prints exactly one `HEAD_SHA=<sha>` line, which `close-preview` pins the merge to. All diagnostics, and the stdout of every tracker and comment call made during a hold, go to stderr, so a hold cannot add extra lines to that stdout.
2. **The gate prints a `TRIGGERED_PATHS: <json list>` line next to `APPROVAL:`, but only with `--issue` and only on the HUMAN_REQUIRED lookup path.** The review request (`<issue>.request.json`, `triggered_paths`) needs the exact path set the match rule used, including `.factory/adapter.yaml` on boundary escalation. Parsing the human-oriented `TRIGGERED_FILES:` lines would be fragile. Without `--issue` the output is still byte-identical to today, as the spec requires.
3. **An approved PASS prints `SEVERITY: none`.** `FINDINGS_COUNT` is unchanged, and `TRIGGER:` and `TRIGGERED_FILES:` are still printed (spec match rule step 7).
4. **Node timeouts.** `blast-gate` gets 120 s instead of the spec's 60 s. The checker's fetch (capped at 60 s), the verified push (30 s plus a 10 s check) and three tracker calls must fit, and a node timeout cannot be caught: it would leave a half-taken hold with no `blast-hold.json`, so the run would count as an ordinary failure. `close-preview` goes from 30 s to 180 s for the same reason: two fetches capped at 60 s each, an origin head listing capped at 30 s, a worktree and the gate now run before the merge (architect cycle 2). A close timeout still fails closed. `push-and-pr` goes from 30 s to 180 s: the re-check now runs before its existing push, `codeindex impact` and PR creation (architect cycle 1).
5. **Every enforcement point prefers the baked checker.** Each one uses `/opt/dark-factory/scripts/blast_gate_check.sh` and falls back to the clone's `dark-factory/scripts/` copy only when it is absent, mirroring how validate Phase 0 already prefers the baked gate. Inside the checker the baked gate is `${BLAST_GATE_BAKED_DIR:-/opt/dark-factory/scripts}/gate_blast_radius.py`. `BLAST_GATE_BAKED_DIR` is a test seam that is never set in the image, the same convention as `FACTORY_CONFIG_PATH`. The factory container itself has an old baked copy without `--issue`, so the tests need the seam.
6. **`BENCH_MODE=stub` holds do not push.** The hold still labels, comments and writes `blast-hold.json`. This keeps `push-and-pr`'s existing "stub never pushes" contract.
7. **A checker failure (exit 2) posts a distinct "checker failed" hold comment.** It carries the reason and says to fix the cause and then remove `needs-discussion` to retry. There is no diff to approve in that state. The normal HUMAN_REQUIRED comment says, as the spec requires, that removing the label by hand does nothing useful.
8. **Holds push in every run mode, including `resolve` and `continue` with an existing PR,** exactly as spec C.1 says. Pushing updates that PR's branch, but nothing can merge it: the close-time check refuses without a matching signed approval. The pre-amendment plan's "no push on resolve" exception is dropped because this spec adds the close-time check that made it necessary.
9. **The match-rule plumbing is pinned.** The merge base is computed inside the gate as `git merge-base <trusted-ref> <head-ref>`, the same base the checker diffs against. `blob_entry()` uses `git --literal-pathspecs ls-tree -z --full-tree`, so a path is never a glob, never cwd-relative and never broken by quoting. Strings from the signed record are reduced to one printable line before printing (`_clean`), so a record cannot forge a `STATUS:` line in `blast.md`. `gpgconf --kill all` runs after each verify, because verify starts a `gpg-agent` per homedir. Cleanup errors are swallowed, so they can never decide a verdict.
10. **The spec's flagged assumptions, checked at plan time.**
    - `gh pr merge --match-head-commit` exists in the image's `gh` (2.105.0), so no read-after-merge fallback is needed.
    - `run_record.py`'s `record` subcommand accepts free-form `--stage`/`--verdict` (no enumeration, `scripts/factory_core/run_record.py:852-853`), so no run-record change is needed.
    - The factory clone is a full clone (`entrypoint.sh:793`), so `merge-base` always resolves.
    - The GitLab provider's `merge_change` is still a `NotImplementedError` stub. It gains the parameter, so the signature-conformance test holds, and a comment notes that it maps to the MR API's `sha`.
11. **No spec path is cited from code.** `push-and-pr` archives this ticket's spec to `docs/archive/` (memory `[PATTERN]`, #42/PR #215: "never archive a doc that tests or README still reference"). Code and docs therefore cite "the #551 spec", never its path.
12. **The hold marker crosses directories through `FACTORY_BLAST_HOLD_FILE`, and it is written first** (architect cycle 1). Archon gives every bash node its own `$ARTIFACTS_DIR`, which is not the entrypoint's (`entrypoint.sh:835-837`). A marker written to the node's `$ARTIFACTS_DIR` would therefore never be seen by `_handle_blast_hold`, and every hold would be counted as a failure.
    - `entrypoint.sh` exports `FACTORY_BLAST_HOLD_FILE="$ARTIFACTS_DIR/blast-hold.json"` next to the existing `FACTORY_RUN_CONFIG_JSON` export. Both the checker and `_handle_blast_hold` use it, falling back to `$ARTIFACTS_DIR/blast-hold.json` only when it is unset (local runs).
    - The spec's "`$ARTIFACTS_DIR` is per run, so the marker cannot leak" still holds, because the path is the entrypoint's own per-run directory. The handler never scans `artifacts/runs/*`.
    - The checker writes the marker before any other hold step. The marker records the decision, so a node timeout in a later step still leaves a hold rather than an ordinary failure. Each step is idempotent and none depends on another, so the order is free.
    - **`_handle_blast_hold` accepts a marker only for the run's own issue** (`.issue` must equal `$ISSUE_NUM`), and both new tests `unset FACTORY_BLAST_HOLD_FILE` first (architect cycle 2). The export reaches every node and every agent Bash subprocess. Without these two guards, a factory run that executes `tests/test_blast_gate_check.sh` (implement, verify, or Task 10 of a future self-target plan) would get a fake marker written into its own artifacts, and a later real failure would then exit 0 with no error signature. That would weaken the breaker as a side effect, which CLAUDE.md forbids.
13. **The `report` node is not changed.** The spec does not name it. On an approved PASS it still prints "✅ Passed — no high-risk files detected". That is misleading but harmless, and it is recorded below as a follow-up rather than added out of scope.

## Known residuals (for the PR body)

- **If validate's own `exit 1` ever does surface as node failure** (#212 says it does not today), `blast-gate`'s default trigger rule would skip it. The run would then end as an ordinary failure with no hold comment. This is the same Archon-semantics exposure `conformance-gate` already has (`docs/run-lifecycle.md` item 5). The spec forbids a `trigger_rule` on `blast-gate`, so none is added.
- **`report` wording on an approved pass** (decision 13). Suggested follow-up ticket.
- **Behaviour change to announce (spec F):** PRs already In Review that touch triggered paths will be refused by factory Close until `approve-blast.sh` (operator PR) has been run for them.
- **Hand merges are unchecked**, as the spec's threat model states: an operator `gh pr merge` bypasses the close-time check.
- **A checker failure (exit 2) needs a human.** A transient `git fetch` blip now holds the run with `needs-discussion`, and someone must clear it. This is fail-closed by design; mention it in the PR body's behaviour-change note (architect cycle 3 advisory).
- **A hold on a `Continue` with an existing PR pushes to that PR's branch** (decision 8, spec C.1 versus E's "before any push to a PR"). Nothing can merge it without a matching signed approval, and `needs-discussion` is set. State this choice in the PR body.
- **Hand-run `Fix issue #<epic>`:** `fetch-issue` resolves to a sub-issue, so the marker names the sub-issue while `ISSUE_NUM` is the epic. That hold is then counted as an ordinary failure, which is the safe direction. The scheduler never dispatches epics (`SKIP_LABELS`).
- **A `push-and-pr` re-check hold pushes the branch after the archive commit**, so the post-approval `Continue` finds the spec under `docs/archive/`, not `docs/superpowers/specs/`, and conformance's spec lookup reads only the latter. This is the same thing that already happens on any `Continue` after a PR exists. Spec E fixes the order (the re-check runs after the archive commit), so it is not changed here (architect cycle 2 advisory).
- **Close mode reads `safety.migration_seed_auth_patterns` from the PR head's working tree** (`adapter.get(clone_dir)`, unchanged pre-existing behaviour, spec Requirement 9). The factory-owned floor and the boundary-escalation diff bound this. Whether the close-time check should read the patterns from `trusted-main` is a follow-up question (architect cycle 1 advisory).

## Memory lessons applied

- `[PATTERN]` archive rules (#42): decision 11, and Task 10's grep that no test or doc cites the spec path.
- `[PATTERN]` two-dot diffs for scope checks (#250): Task 10 uses `git diff origin/main HEAD` (two-dot) for the out-of-scope check.
- `[AVOID]` gate node before anything has pushed (#551 refine memory): `blast-gate` pushes the branch itself before it holds (Task 4 `_push_branch`, Task 5 node).
- `[AVOID]` "use `verdict_gate_check.sh` on `blast.md`" (#551 refine memory): **superseded by the amended spec** (Alternative 8: it passes `ERROR` and trusts an agent-written file). Not followed. `verdict_gate_check.sh` is not touched.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `scripts/factory_core/adapter_defaults.py` | Add `^\.factory/blast-approvers\.asc$` to `FACTORY_OWNED_CRITICAL_DIFF_FLOOR` (flows into the blocking floor) | 1 |
| `tests/test_adapter.py` | Floor shape test: 9 critical / 6 blocking entries, new pattern in both | 1 |
| `scripts/gate_blast_radius.py` | `--issue/--approvals-dir/--repo/--branch/--trusted-ref/--head-ref`; `blob_entry`, `canonical_bytes`, `verify_signature`, `_match_approval`, `evaluate_approval`; `APPROVAL:` and `TRIGGERED_PATHS:` lines; `head_ref` threaded into the boundary diff | 2 |
| `tests/test_blast_radius.py` | Approver-key trigger test (Task 1); approval section with throwaway gpg keys (Task 2) | 1, 2 |
| `scripts/factory_core/providers/codehost/base.py`, `github.py`, `gitlab.py` | `merge_change(..., match_head=None)`; GitHub passes `--match-head-commit` | 3 |
| `scripts/factory_core/providers/cli.py` | `codehost merge --match-head`, passed only when given | 3 |
| `tests/test_provider_cli.py`, `tests/test_provider_codehost_parity.py` | Head pin passed through, and omitted when absent | 3 |
| `scripts/blast_gate_check.sh` (new, 0755) | The one deterministic checker plus `--hold` | 4 |
| `tests/test_blast_gate_check.sh` (new, 0755) | Exit mapping, head resolution, hotspot sources, signed approval end to end, hold steps | 4 |
| `.github/workflows/ci.yml` | Run `tests/test_blast_gate_check.sh` (Task 4) and `tests/test_entrypoint_blast_hold.sh` (Task 8) | 4, 8 |
| `workflows/archon-dark-factory.yaml` | New `blast-gate` node (120 s); `budget-conformance`/`conformance`/`push-resolve` behind it; `push-and-pr` re-check (timeout 30 s → 180 s); `close-preview` check, head-pinned merge, record cleanup (timeout 30 s → 180 s) | 5 |
| `tests/test_blast_gate_dag.py` (new) | Static DAG assertions | 5 |
| `tests/test_docs_conformance_dag.py` | `push-resolve` now depends on `blast-gate` | 5 |
| `commands/dark-factory-validate.md` | Phase 0 calls the checker; no comment, label or board move | 6 |
| `tests/test_validate_blast_gate_baseref.py` | Rewritten for the checker call; base-ref and baked-gate pins move to the checker | 6 |
| `commands/dark-factory-conformance.md` | Phase 1: "Conformance does not adjudicate the blast-radius gate" | 7 |
| `tests/test_conformance_blast_adjudication.py` (new) | Pins that paragraph and the unchanged Phase 5 writes | 7 |
| `entrypoint.sh` | Export `FACTORY_BLAST_HOLD_FILE`; `_handle_blast_hold`, called right after the session-window check | 8 |
| `tests/test_entrypoint_blast_hold.sh` (new, 0755) | Hold exits clean, writes no signature, leaves counters alone; loop order pinned | 8 |
| `docs/run-lifecycle.md`, `docs/features.md`, `docs/factory-target-boundary.md`, `docs/configuration.md` | As-built docs; open items 3 and 4 marked resolved in place | 9 |
| `tests/test_docs_conformance_reference_docs.py` | Its pinned `push-and-pr` row now reads `bash, 180 s` | 9 |

Not touched (scope fence, checked in Task 10): `scheduler.sh`, `scripts/verdict_gate_check.sh`, `scripts/check_workflow_dag.py` (no `REQUIRED_OR_JOIN_NODES` change), `commands/dark-factory-code-review.md`, `commands/dark-factory-docs-conformance.md`, `deploy/**`, `.github/workflows/publish.yml`, `.claude/**`, `.factory/blast-approvers.asc`, and the trigger computation in `gate_blast_radius.py` (old lines 327-360).

---

### Task 1: Put the approver key on the hard-trigger floor

**Files:**
- Modify: `tests/test_adapter.py` (`test_boundary_floor_constants_shape`)
- Modify: `tests/test_blast_radius.py` (new `test_blast_approvers_key_is_a_hard_trigger`)
- Modify: `scripts/factory_core/adapter_defaults.py`

Spec I / Requirement 9. This is the only change to what triggers the gate. The pattern is not in `_VISIBILITY_ONLY`, so it lands in `FACTORY_OWNED_MIGRATION_SEED_FLOOR` and any PR that adds or changes approver keys is `HUMAN_REQUIRED`. It is classified `migration-seed`, because none of `SKILL_SECURITY_TOKENS` matches it.

- [ ] **Step 1: Write the failing tests**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_adapter.py b/tests/test_adapter.py
index 42c6355..6a8e9f6 100644
--- a/tests/test_adapter.py
+++ b/tests/test_adapter.py
@@ -1127,9 +1127,10 @@ def test_boundary_floor_constants_shape():
         r"^\.factory/hooks/", r"^\.factory/adapter\.yaml$", r"^\.claude/",
         r"^workflows/", r"^commands/", r"^\.archon/commands/",
         r"^\.archon/workflows/", r"^dark-factory/scripts/",
+        r"^\.factory/blast-approvers\.asc$",
     ):
         assert pat in floor, f"{pat} missing from FACTORY_OWNED_CRITICAL_DIFF_FLOOR"
-    assert len(floor) == 8
+    assert len(floor) == 9
     # adapter.yaml itself and workflows/commands are visibility-only (OD1/OD2):
     # present in the critical-diff floor, excluded from the blocking floor.
     for visibility_only in (r"^\.factory/adapter\.yaml$", r"^workflows/", r"^commands/"):
@@ -1137,9 +1138,10 @@ def test_boundary_floor_constants_shape():
     for hard_trigger in (
         r"^\.factory/hooks/", r"^\.claude/", r"^\.archon/commands/",
         r"^\.archon/workflows/", r"^dark-factory/scripts/",
+        r"^\.factory/blast-approvers\.asc$",
     ):
         assert hard_trigger in blocking
-    assert len(blocking) == 5
+    assert len(blocking) == 6
     # blocking floor is derived from the critical-diff floor, not hand-duplicated
     assert set(blocking) <= set(floor)
 
diff --git a/tests/test_blast_radius.py b/tests/test_blast_radius.py
index 4faf8cf..b92e021 100644
--- a/tests/test_blast_radius.py
+++ b/tests/test_blast_radius.py
@@ -285,6 +285,13 @@ def test_skill_md_now_triggers_via_broadened_claude_floor(run_script):
     assert out["TRIGGER"] == "skill-security"
 
 
+def test_blast_approvers_key_is_a_hard_trigger(run_script):
+    """#551 Requirement 9: changing the approver key set always needs a human."""
+    out = run_script([".factory/blast-approvers.asc"])
+    assert out["STATUS"] == "HUMAN_REQUIRED"
+    assert ".factory/blast-approvers.asc (category: migration-seed)" in out["_stdout"]
+
+
 def test_migration_file_trigger_label_still_migration_seed(run_script):
     out = run_script(["alembic/versions/abc123_add_col.py"])
     assert out["STATUS"] == "HUMAN_REQUIRED"
PATCH
~~~~

- [ ] **Step 2: Run them and confirm they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py::test_boundary_floor_constants_shape tests/test_blast_radius.py::test_blast_approvers_key_is_a_hard_trigger -q -p no:cacheprovider 2>&1 | tail -3
```

Expected:

```text
FAILED tests/test_adapter.py::test_boundary_floor_constants_shape - AssertionError: ^\.factory/blast-approvers\.asc$ missing from FACTORY_OWNED_CRITICAL_DIFF_FLOOR
FAILED tests/test_blast_radius.py::test_blast_approvers_key_is_a_hard_trigger - AssertionError: assert 'PASS' == 'HUMAN_REQUIRED'
2 failed in ...
```

- [ ] **Step 3: Add the floor entry**

~~~~bash
git apply <<'PATCH'
diff --git a/scripts/factory_core/adapter_defaults.py b/scripts/factory_core/adapter_defaults.py
index 50b765a..a3c2eae 100644
--- a/scripts/factory_core/adapter_defaults.py
+++ b/scripts/factory_core/adapter_defaults.py
@@ -130,6 +130,10 @@ FACTORY_OWNED_CRITICAL_DIFF_FLOOR = [
     r"^\.archon/commands/",
     r"^\.archon/workflows/",
     r"^dark-factory/scripts/",
+    # The blast-radius approver public key(s) (#551): changing who may approve a
+    # HUMAN_REQUIRED diff always needs a human. Not visibility-only, so it lands in the
+    # blocking floor below.
+    r"^\.factory/blast-approvers\.asc$",
 ]
 
 # migration_seed_auth_patterns is the hard-blocking (HUMAN_REQUIRED) list.
PATCH
~~~~

- [ ] **Step 4: Run them and confirm they pass, with the rest of both files**

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py tests/test_blast_radius.py tests/test_diff_rank.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `... passed` with no failures.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/adapter_defaults.py tests/test_adapter.py tests/test_blast_radius.py
git commit -q -m "feat(#551): approver key path joins the blast-radius hard-trigger floor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Signed, content-bound approval matching in `gate_blast_radius.py`

**Files:**
- Modify: `tests/test_blast_radius.py` (imports; new approval section at the end)
- Modify: `scripts/gate_blast_radius.py`

The spec's match rule is implemented here once, and every other piece calls it through the gate.

- The rule runs only when the existing logic yields `HUMAN_REQUIRED` and `--issue` is given. It can only turn that verdict into `PASS`.
- First failing step wins:
  1. no record → `none`;
  2. signature → `error (signature)`;
  3. canonical/schema/shape → `error (record)`;
  4. issue/repo/branch → `stale (identity)`;
  5. per triggered path, `new trigger: <p>` or `changed since approval: <p>`;
  6. size trigger → `stale (size)`;
  7. `matched (<by> at <at>, head <sha>)`.
- A triggered path is `.factory/adapter.yaml` when boundary escalation fired, plus every file trigger.
- `evaluate_approval` lets no exception escape.
- The trigger computation itself is untouched, and without `--issue` the output is byte-identical.

The tests generate three throwaway ed25519 keys: `op` (the approver), `other` (never trusted) and `old` (created with a faked 2020 clock and a one-day expiry, so it gives `EXPKEYSIG`). They fail rather than skip when `gpg` is absent. They cover every case the spec lists: matched, no record, no `--issue`, missing `.sig`, untrusted key, tampered record, expired key, a key only in the working tree, non-canonical bytes, wrong issue/repo/branch, new trigger, head blob changed, base blob changed (main moves underneath while the branch keeps its content), mode-only change, deleted file (`absent`), size trigger with and without matching trees, and the two review regressions (re-indented line, relocated hunk). They also cover subset semantics, boundary escalation, PASS never being looked up, and the no-raise wrapper.

- [ ] **Step 1: Write the failing tests**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_blast_radius.py b/tests/test_blast_radius.py
index b92e021..8e81c2d 100644
--- a/tests/test_blast_radius.py
+++ b/tests/test_blast_radius.py
@@ -1,4 +1,7 @@
 """Tests for gate_blast_radius.py — deterministic file classifier."""
+import json
+import os
+import shutil
 import subprocess
 import sys
 import tempfile
@@ -597,3 +600,369 @@ def test_boundary_diff_file_absent_at_resolvable_base_ref_is_not_an_error(tmp_pa
     )
     assert proc.returncode == 0, proc.stderr
     assert "STATUS: PASS" in proc.stdout
+
+
+# ── Signed, content-bound approval (#551) ─────────────────────────────────────
+# Throwaway keys are generated here; the operator's real key is never needed. The
+# tests fail (not skip) without gpg -- the spec requires it.
+
+import gate_blast_radius as gbr  # noqa: E402
+
+_APPROVAL_REPO = "omniscient/dark-factory"
+_APPROVAL_BRANCH = "feat/issue-551-x"
+_TRUSTED = "refs/blast-gate/trusted-main"
+_SKILL = ".claude/skills/x/run.sh"
+_MIGRATION = "alembic/versions/a.py"
+
+
+def _gpg(home, *args, input=None):
+    return subprocess.run(["gpg", "--homedir", str(home), "--batch", *args],
+                          capture_output=True, input=input, check=True)
+
+
+@pytest.fixture(scope="module")
+def keys():
+    """`op` (the approver), `other` (never in the trusted blob), `old` (created in 2020
+    with a one-day expiry, so it is expired now)."""
+    assert shutil.which("gpg"), "gpg is required for the #551 approval tests"
+    base = Path(tempfile.mkdtemp(prefix="bgk-"))
+    homes = {}
+    for name in ("op", "other", "old"):
+        home = base / name
+        home.mkdir(mode=0o700)
+        faked = ["--faked-system-time", "20200101T000000"] if name == "old" else []
+        _gpg(home, *faked, "--passphrase", "", "--quick-gen-key",
+             f"{name} <{name}@example.test>", "ed25519", "sign", "1d" if name == "old" else "1y")
+        homes[name] = home
+    yield homes
+    for home in homes.values():
+        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], capture_output=True)
+    shutil.rmtree(base, ignore_errors=True)
+
+
+def _pubkey(home) -> bytes:
+    return _gpg(home, "--armor", "--export").stdout
+
+
+def _sign(home, data: bytes, faked=None) -> bytes:
+    extra = ["--faked-system-time", faked] if faked else []
+    return _gpg(home, *extra, "--detach-sign", "-o", "-", input=data).stdout
+
+
+class _ApprovalRepo:
+    """main (with the trusted key blob) -> feat/issue-551-x editing two floor paths."""
+
+    def __init__(self, root: Path, keys, key_blob: bytes, commit_key=True):
+        self.root, self.keys = root, keys
+        self.approvals = root.parent / f"{root.name}-approvals"
+        self.approvals.mkdir()
+        _init_repo(root)
+        _write_config(root)
+        for rel, text in ((_SKILL, "echo v0\n"), (_MIGRATION, "v0 = 0\n"),
+                          ("src/app.py", "print(0)\n"), (".claude/skills/x/old.sh", "old\n")):
+            (root / rel).parent.mkdir(parents=True, exist_ok=True)
+            (root / rel).write_text(text)
+        key_path = root / ".factory" / "blast-approvers.asc"
+        key_path.parent.mkdir(parents=True, exist_ok=True)
+        key_path.write_bytes(key_blob)
+        if not commit_key:
+            _git(root, "add", "-A", "--", ".", ":!.factory/blast-approvers.asc")
+        else:
+            _git(root, "add", "-A")
+        _git(root, "commit", "-q", "-m", "main")
+        _git(root, "update-ref", _TRUSTED, "main")
+        _git(root, "checkout", "-q", "-b", _APPROVAL_BRANCH)
+        self.edit({_SKILL: "echo v1\n", _MIGRATION: "v0 = 1\n"}, "feat")
+
+    def edit(self, files: dict, msg: str):
+        for rel, text in files.items():
+            (self.root / rel).write_text(text)
+        _git(self.root, "add", "-A")
+        _git(self.root, "commit", "-q", "-m", msg)
+
+    def git(self, *args) -> str:
+        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
+                              capture_output=True, text=True).stdout.strip()
+
+    def base(self) -> str:
+        return self.git("merge-base", _TRUSTED, "HEAD")
+
+    def record(self, paths=(_SKILL, _MIGRATION), **overrides) -> dict:
+        base = self.base()
+        rec = {
+            "schema": 1, "issue": 551, "repo": _APPROVAL_REPO, "branch": _APPROVAL_BRANCH,
+            "head_sha": self.git("rev-parse", "HEAD"), "base_sha": base,
+            "files": {p: {"base": gbr.blob_entry(str(self.root), base, p),
+                          "head": gbr.blob_entry(str(self.root), "HEAD", p)} for p in paths},
+            "size": None, "trigger": "skill-security", "approved_at": "2026-11-21T14:03:00Z",
+            "approved_by": "Test Operator", "approvals_count": 1, "previous_head_sha": None,
+        }
+        rec.update(overrides)
+        return rec
+
+    def write(self, rec, signer="op", data=None, sign_data=None, faked=None, sig=True):
+        data = gbr.canonical_bytes(rec) if data is None else data
+        (self.approvals / "551.json").write_bytes(data)
+        if sig:
+            sig_bytes = _sign(self.keys[signer], data if sign_data is None else sign_data, faked)
+            (self.approvals / "551.json.sig").write_bytes(sig_bytes)
+
+    def run(self, issue=551, repo=_APPROVAL_REPO, branch=_APPROVAL_BRANCH, lines=10,
+            approval_args=True) -> dict:
+        base = self.base()
+        changed = self.git("diff", "--name-only", base, "HEAD")
+        hot = self.root.parent / f"{self.root.name}-hot.md"
+        hot.write_text("")
+        cmd = [sys.executable, str(SCRIPT), "--changed-files-stdin",
+               "--lines-changed", str(lines), "--hotspots", str(hot),
+               "--config", _CONFIG_REL, "--clone-dir", str(self.root), "--base-ref", base]
+        if approval_args:
+            cmd += ["--issue", str(issue), "--repo", repo, "--branch", branch,
+                    "--trusted-ref", _TRUSTED, "--head-ref", "HEAD",
+                    "--approvals-dir", str(self.approvals)]
+        proc = subprocess.run(cmd, input=changed, capture_output=True, text=True)
+        assert proc.returncode == 0, proc.stderr
+        return _parse(proc.stdout)
+
+
+@pytest.fixture
+def arepo(tmp_path, keys, monkeypatch):
+    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
+    root = tmp_path / "repo"
+    root.mkdir()
+    return _ApprovalRepo(root, keys, _pubkey(keys["op"]) + _pubkey(keys["old"]))
+
+
+def test_approval_matched_turns_human_required_into_pass(arepo):
+    arepo.write(arepo.record())
+    out = arepo.run()
+    assert out["STATUS"] == "PASS"
+    assert out["SEVERITY"] == "none"
+    head = arepo.git("rev-parse", "HEAD")
+    assert out["APPROVAL"] == f"matched (Test Operator at 2026-11-21T14:03:00Z, head {head})"
+    assert out["TRIGGER"] == "skill-security"
+    assert f"  - {_SKILL} (category: skill-security)" in out["_stdout"]
+    assert sorted(json.loads(out["TRIGGERED_PATHS"])) == sorted([_MIGRATION, _SKILL])
+
+
+def test_approval_no_record_is_none(arepo):
+    out = arepo.run()
+    assert out["STATUS"] == "HUMAN_REQUIRED"
+    assert out["APPROVAL"] == "none"
+
+
+def test_approval_without_issue_is_byte_identical_and_ignores_records(arepo):
+    arepo.write(arepo.record())
+    out = arepo.run(approval_args=False)
+    assert out["STATUS"] == "HUMAN_REQUIRED"
+    assert "APPROVAL" not in out and "TRIGGERED_PATHS" not in out
+    assert out["_stdout"].splitlines()[-1].startswith("LINES_CHANGED: ")
+
+
+def test_approval_missing_sig_is_signature_error(arepo):
+    arepo.write(arepo.record(), sig=False)
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")
+
+
+def test_approval_signed_by_untrusted_key_is_signature_error(arepo):
+    arepo.write(arepo.record(), signer="other")
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")
+
+
+def test_approval_tampered_record_is_signature_error(arepo):
+    good = gbr.canonical_bytes(arepo.record())
+    tampered = gbr.canonical_bytes(arepo.record(approved_by="Mallory"))
+    arepo.write(None, data=tampered, sign_data=good)
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")
+
+
+def test_approval_expired_key_is_signature_error(arepo):
+    arepo.write(arepo.record(), signer="old", faked="20200101T010000")
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")
+
+
+def test_approval_key_only_in_working_tree_is_signature_error(tmp_path, keys, monkeypatch):
+    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
+    root = tmp_path / "repo"
+    root.mkdir()
+    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]), commit_key=False)
+    assert (root / ".factory" / "blast-approvers.asc").is_file()
+    repo.write(repo.record())
+    out = repo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")
+
+
+def test_approval_non_canonical_bytes_is_record_error(arepo):
+    pretty = (json.dumps(arepo.record(), indent=2, sort_keys=True) + "\n").encode()
+    arepo.write(None, data=pretty)
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (record)")
+
+
+def test_approval_wrong_schema_is_record_error(arepo):
+    arepo.write(arepo.record(schema=2))
+    assert arepo.run()["APPROVAL"] == "error (record)"
+
+
+@pytest.mark.parametrize("field,value", [
+    ("issue", 552), ("repo", "omniscient/markethawk"), ("branch", "feat/issue-551-other"),
+])
+def test_approval_identity_mismatch_is_stale(arepo, field, value):
+    arepo.write(arepo.record(**{field: value}))
+    out = arepo.run()
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "stale (identity)")
+
+
+def test_approval_new_trigger_is_stale(arepo):
+    arepo.write(arepo.record(paths=(_SKILL,)))
+    out = arepo.run()
+    assert out["STATUS"] == "HUMAN_REQUIRED"
+    assert out["APPROVAL"] == f"stale (new trigger: {_MIGRATION})"
+
+
+def test_approval_may_list_more_paths_than_currently_triggered(arepo):
+    arepo.write(arepo.record(paths=(_SKILL, _MIGRATION, ".claude/skills/x/old.sh")))
+    assert arepo.run()["STATUS"] == "PASS"
+
+
+def test_approval_head_blob_changed_is_stale(arepo):
+    arepo.write(arepo.record())
+    arepo.edit({_SKILL: "echo v2\n"}, "later edit")
+    out = arepo.run()
+    assert out["STATUS"] == "HUMAN_REQUIRED"
+    assert out["APPROVAL"] == f"stale (changed since approval: {_SKILL})"
+
+
+def test_approval_base_blob_changed_is_stale(arepo):
+    """main changes the triggered file underneath; the branch keeps its own content."""
+    arepo.write(arepo.record())
+    arepo.git("checkout", "-q", "main")
+    arepo.edit({_SKILL: "echo main-v1\n"}, "main moves")
+    arepo.git("update-ref", _TRUSTED, "main")
+    arepo.git("checkout", "-q", _APPROVAL_BRANCH)
+    arepo.git("merge", "-q", "-s", "ours", "-m", "merge main", "main")
+    assert (arepo.root / _SKILL).read_text() == "echo v1\n"
+    out = arepo.run()
+    assert out["APPROVAL"] == f"stale (changed since approval: {_SKILL})"
+
+
+def test_approval_unrelated_later_commit_still_matches(arepo):
+    arepo.write(arepo.record())
+    arepo.edit({"src/app.py": "print(1)\n"}, "unrelated")
+    assert arepo.run()["STATUS"] == "PASS"
+
+
+def test_approval_mode_only_change_is_stale(arepo):
+    arepo.write(arepo.record())
+    os.chmod(arepo.root / _SKILL, 0o755)
+    _git(arepo.root, "add", "-A")
+    _git(arepo.root, "commit", "-q", "-m", "chmod +x")
+    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"
+
+
+def test_approval_deleted_file_binds_absent(arepo):
+    old = ".claude/skills/x/old.sh"
+    _git(arepo.root, "rm", "-q", old)
+    _git(arepo.root, "commit", "-q", "-m", "delete")
+    rec = arepo.record(paths=(_SKILL, _MIGRATION, old))
+    assert rec["files"][old]["head"] == "absent"
+    assert rec["files"][old]["base"].startswith("100644 ")
+    arepo.write(rec)
+    assert arepo.run()["STATUS"] == "PASS"
+    arepo.write(arepo.record(paths=(_SKILL, _MIGRATION)))
+    assert arepo.run()["APPROVAL"] == f"stale (new trigger: {old})"
+
+
+def test_approval_reindented_line_is_stale(arepo):
+    """#551 review: a patch-id ignores whitespace; the blob binding must not."""
+    arepo.edit({_SKILL: "if x; then\n  echo v1\nfi\n"}, "inside if")
+    arepo.write(arepo.record())
+    arepo.edit({_SKILL: "if x; then\necho v1\nfi\n"}, "dedent")
+    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"
+
+
+def test_approval_relocated_hunk_is_stale(arepo):
+    """#551 review: the same hunk in another block has the same patch-id."""
+    arepo.edit({_SKILL: "job_a:\n  run: build\njob_b:\n  run: test\n  extra: 1\n"}, "hunk in job_b")
+    arepo.write(arepo.record())
+    arepo.edit({_SKILL: "job_a:\n  run: build\n  extra: 1\njob_b:\n  run: test\n"}, "hunk in job_a")
+    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"
+
+
+def test_approval_boundary_escalation_requires_adapter_entry(tmp_path, keys, monkeypatch):
+    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
+    root = tmp_path / "repo"
+    root.mkdir()
+    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]))
+    (root / ".factory" / "adapter.yaml").write_text(yaml.dump({"safety": {"x": 1}}))
+    _git(root, "add", "-A")
+    _git(root, "commit", "-q", "-m", "escalate")
+    repo.write(repo.record())
+    out = repo.run()
+    assert out["TRIGGER"] == "boundary-escalation"
+    assert out["APPROVAL"] == "stale (new trigger: .factory/adapter.yaml)"
+    repo.write(repo.record(paths=(_SKILL, _MIGRATION, ".factory/adapter.yaml")))
+    assert repo.run()["STATUS"] == "PASS"
+
+
+def _size_repo(tmp_path, keys, monkeypatch):
+    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
+    root = tmp_path / "repo"
+    root.mkdir()
+    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]))
+    _write_config(root, {"size_budget_blocks": True, "size_budget_lines": 5})
+    _git(root, "checkout", "-q", "main")
+    _git(root, "add", "-A")
+    _git(root, "commit", "-q", "-m", "size blocks")
+    _git(root, "update-ref", _TRUSTED, "main")
+    _git(root, "checkout", "-q", _APPROVAL_BRANCH)
+    _git(root, "merge", "-q", "-m", "merge main", "main")
+    return repo
+
+
+def _size_of(repo, lines=50):
+    return {"lines_changed": lines,
+            "head_tree": repo.git("rev-parse", "HEAD^{tree}"),
+            "base_tree": repo.git("rev-parse", f"{repo.base()}^{{tree}}")}
+
+
+def test_approval_size_trigger_matches_both_trees(tmp_path, keys, monkeypatch):
+    repo = _size_repo(tmp_path, keys, monkeypatch)
+    repo.write(repo.record(size=_size_of(repo)))
+    out = repo.run(lines=50)
+    assert "[size] 50 lines > 5 budget" in out["_stdout"]
+    assert out["STATUS"] == "PASS"
+
+
+@pytest.mark.parametrize("mutate", [
+    lambda s: None,
+    lambda s: {**s, "lines_changed": 49},
+    lambda s: {**s, "head_tree": "0" * 40},
+    lambda s: {**s, "base_tree": "0" * 40},
+])
+def test_approval_size_trigger_mismatch_is_stale(tmp_path, keys, monkeypatch, mutate):
+    repo = _size_repo(tmp_path, keys, monkeypatch)
+    repo.write(repo.record(size=mutate(_size_of(repo))))
+    out = repo.run(lines=50)
+    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "stale (size)")
+
+
+def test_approval_never_consulted_for_pass(arepo, tmp_path):
+    """A PASS diff is never looked up -- not even an APPROVAL: none line."""
+    _git(arepo.root, "checkout", "-q", "main")
+    _git(arepo.root, "checkout", "-q", "-b", "clean")
+    arepo.edit({"src/app.py": "print(2)\n"}, "clean change")
+    out = arepo.run(branch="clean")
+    assert out["STATUS"] == "PASS"
+    assert "APPROVAL" not in out
+
+
+def test_evaluate_approval_never_raises(monkeypatch):
+    def boom(*a, **k):
+        raise RuntimeError("unexpected")
+    monkeypatch.setattr(gbr, "_match_approval", boom)
+    assert gbr.evaluate_approval(None, [], False, 0) == "error (RuntimeError)"
PATCH
~~~~

- [ ] **Step 2: Run them and confirm they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_blast_radius.py -k approval -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `30 failed, 42 deselected in ...`. The gate rejects `--issue` as an unrecognized argument (exit 2), and `gbr.blob_entry` / `gbr.evaluate_approval` do not exist yet.

- [ ] **Step 3: Implement the approval lookup**

The patch:
- adds the six optional arguments;
- threads `head_ref` into `_boundary_escalation_findings` (default `"HEAD"`, so the existing message `adapter.yaml unparseable at HEAD` is unchanged for existing callers);
- adds the approval block above `main()`;
- in `main()`, evaluates it after the status is computed and prints `APPROVAL:` / `TRIGGERED_PATHS:` after `LINES_CHANGED:`.

~~~~bash
git apply <<'PATCH'
diff --git a/scripts/gate_blast_radius.py b/scripts/gate_blast_radius.py
index 5fa8ace..78e1d29 100644
--- a/scripts/gate_blast_radius.py
+++ b/scripts/gate_blast_radius.py
@@ -4,12 +4,19 @@ Reads a newline-separated list of changed file paths from stdin.
 Writes a blast.md-format verdict to stdout.
 
 Exit 0 always — the caller reads STATUS from the output.
+
+With --issue (#551) a HUMAN_REQUIRED verdict is checked against a signed, content-bound
+operator approval record (the #551 spec's "Interface contract"); a match turns it into
+PASS. Without --issue the output is byte-identical to the pre-#551 gate.
 """
 import argparse
+import json
 import os
 import re
+import shutil
 import subprocess
 import sys
+import tempfile
 from pathlib import Path
 
 # Make factory_core resolvable regardless of caller CWD/invocation style
@@ -54,6 +61,21 @@ def parse_args() -> argparse.Namespace:
         help="Git ref to read blast_radius.* config (Requirement 8) and the pre-PR "
              "adapter.yaml snapshot from; never the working tree",
     )
+    # --- #551: signed approval matching (all optional; absent --issue = pre-#551 output) ---
+    p.add_argument("--issue", type=int, default=None,
+                   help="Issue number; enables the signed-approval check on HUMAN_REQUIRED")
+    p.add_argument(
+        "--approvals-dir",
+        default=os.path.join(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"),
+                             "blast-approvals"),
+        help="Directory holding <issue>.json + <issue>.json.sig",
+    )
+    p.add_argument("--repo", default=None, help="owner/name the record must name")
+    p.add_argument("--branch", default=None, help="Branch the record must name")
+    p.add_argument("--trusted-ref", default="refs/blast-gate/trusted-main",
+                   help="Ref the approver public key and merge-base are read from")
+    p.add_argument("--head-ref", default="HEAD",
+                   help="Commit under review (boundary-escalation HEAD side and blob binding)")
     return p.parse_args()
 
 
@@ -245,19 +267,19 @@ def _adapter_snapshot(clone_dir: str, ref: str) -> tuple:
         return None, False
 
 
-def _boundary_escalation_findings(clone_dir: str, base_ref: str) -> list:
-    """Semantic diff of .factory/adapter.yaml, base_ref vs. HEAD (Requirement 5).
+def _boundary_escalation_findings(clone_dir: str, base_ref: str, head_ref: str = "HEAD") -> list:
+    """Semantic diff of .factory/adapter.yaml, base_ref vs. head_ref (Requirement 5).
     Both sides come from `git show`; the working tree is never consulted, so an
     escalation that is committed but reverted on disk is still caught. Only called by
     main() when that file is in the changed set."""
     from factory_core import side_effect as _side_effect
 
     old, old_ok = _adapter_snapshot(clone_dir, base_ref)
-    new, new_ok = _adapter_snapshot(clone_dir, "HEAD")
+    new, new_ok = _adapter_snapshot(clone_dir, head_ref)
     if not old_ok:
         return [f"adapter.yaml unparseable at {base_ref}"]
     if not new_ok:
-        return ["adapter.yaml unparseable at HEAD"]
+        return [f"adapter.yaml unparseable at {head_ref}"]
 
     findings = []
     # `or {}` normalizes "no safety: key at all" (a brand-new adapter.yaml, or the
@@ -315,6 +337,178 @@ def _boundary_escalation_findings(clone_dir: str, base_ref: str) -> list:
     return findings
 
 
+# --- #551: signed, content-bound approval (spec "Interface contract") -----------------
+# The match rule is implemented here and nowhere else. It only ever runs on a
+# HUMAN_REQUIRED verdict and can only turn it into PASS; every failure path leaves
+# HUMAN_REQUIRED and is reported on the APPROVAL: line.
+
+APPROVERS_KEY_PATH = ".factory/blast-approvers.asc"
+_BAD_GPG_STATUS = {"EXPSIG", "EXPKEYSIG", "REVKEYSIG", "BADSIG", "ERRSIG"}
+_MAX_RECORD_BYTES = 1 << 20
+
+
+class _ApprovalError(Exception):
+    """Carries the APPROVAL: line suffix for a non-matching outcome."""
+
+    def __init__(self, line: str):
+        super().__init__(line)
+        self.line = line
+
+
+def _git_out(clone_dir: str, *args: str) -> str:
+    proc = subprocess.run(
+        ["git", "-C", clone_dir, "--literal-pathspecs", *args],
+        capture_output=True, text=True, timeout=30,
+        env={**os.environ, "LC_ALL": "C"},
+    )
+    if proc.returncode != 0:
+        raise _ApprovalError(f"error (git {args[0]})")
+    return proc.stdout
+
+
+def blob_entry(clone_dir: str, commit: str, path: str) -> str:
+    """`<mode> <oid>` of `path` at `commit` (fields 1 and 3 of `git ls-tree`), or `absent`."""
+    out = _git_out(clone_dir, "ls-tree", "-z", "--full-tree", commit, "--", path)
+    for entry in out.split("\0"):
+        meta, _, name = entry.partition("\t")
+        if name == path:
+            fields = meta.split()
+            return f"{fields[0]} {fields[2]}"
+    return "absent"
+
+
+def canonical_bytes(record: dict) -> bytes:
+    """The exact on-disk form of a record: sorted, compact, ASCII JSON plus one newline."""
+    return json.dumps(record, sort_keys=True, separators=(",", ":"),
+                      ensure_ascii=True).encode("ascii") + b"\n"
+
+
+def verify_signature(clone_dir: str, trusted_ref: str, data: bytes, sig: bytes) -> bool:
+    """Detached-signature check against the key blob at <trusted_ref>:.factory/blast-approvers.asc.
+
+    Fresh keyring holding that blob only; valid means every command exits 0, exactly one
+    GOODSIG, a VALIDSIG, and none of _BAD_GPG_STATUS (an expired key exits 0 with VALIDSIG
+    but EXPKEYSIG, so the exit code alone is not enough)."""
+    home = tempfile.mkdtemp(prefix="bg-")
+    try:
+        os.chmod(home, 0o700)
+        key = subprocess.run(
+            ["git", "-C", clone_dir, "show", f"{trusted_ref}:{APPROVERS_KEY_PATH}"],
+            capture_output=True, timeout=30,
+        )
+        if key.returncode != 0 or not key.stdout.strip():
+            return False
+        asc, rec, sigf = (os.path.join(home, n) for n in ("approvers.asc", "rec", "rec.sig"))
+        for path, blob in ((asc, key.stdout), (rec, data), (sigf, sig)):
+            with open(path, "wb") as f:
+                f.write(blob)
+        imp = subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--import", asc],
+                             capture_output=True, timeout=30)
+        if imp.returncode != 0:
+            return False
+        ver = subprocess.run(
+            ["gpg", "--homedir", home, "--batch", "--status-fd", "1", "--verify", sigf, rec],
+            capture_output=True, timeout=30,
+        )
+        if ver.returncode != 0:
+            return False
+        tokens = [ln.split()[1] for ln in ver.stdout.decode("utf-8", "replace").splitlines()
+                  if ln.startswith("[GNUPG:] ") and len(ln.split()) > 1]
+        return (tokens.count("GOODSIG") == 1 and "VALIDSIG" in tokens
+                and not _BAD_GPG_STATUS.intersection(tokens))
+    except (OSError, subprocess.SubprocessError):
+        return False
+    finally:
+        try:  # verify can start an agent for this homedir; never let cleanup decide
+            subprocess.run(["gpgconf", "--homedir", home, "--kill", "all"],
+                           capture_output=True, timeout=10, check=False)
+        except (OSError, subprocess.SubprocessError):
+            pass
+        shutil.rmtree(home, ignore_errors=True)
+
+
+def _clean(value) -> str:
+    """One printable line — signed strings still never get to forge a STATUS: line."""
+    return re.sub(r"[\x00-\x1f\x7f]", "?", str(value))[:200]
+
+
+def _valid_record(rec) -> bool:
+    if not isinstance(rec, dict):
+        return False
+    if type(rec.get("schema")) is not int or rec["schema"] != 1:
+        return False
+    if type(rec.get("issue")) is not int:
+        return False
+    if not all(isinstance(rec.get(k), str) for k in ("repo", "branch")):
+        return False
+    files = rec.get("files")
+    if not isinstance(files, dict):
+        return False
+    for path, entry in files.items():
+        if not (isinstance(entry, dict) and isinstance(entry.get("base"), str)
+                and isinstance(entry.get("head"), str)):
+            return False
+    size = rec.get("size")
+    if size is not None:
+        if not isinstance(size, dict) or type(size.get("lines_changed")) is not int:
+            return False
+        if not all(isinstance(size.get(k), str) for k in ("head_tree", "base_tree")):
+            return False
+    return True
+
+
+def _match_approval(args, approval_paths: list, size_trigger: bool, lines_changed: int) -> str:
+    """Spec match rule steps 1-7; returns the APPROVAL: suffix. First failing step wins."""
+    rec_path = Path(args.approvals_dir) / f"{args.issue}.json"
+    if not rec_path.is_file():
+        return "none"
+    try:
+        data = rec_path.read_bytes()
+        sig = Path(str(rec_path) + ".sig").read_bytes()
+    except OSError:
+        return "error (signature)"
+    if not verify_signature(args.clone_dir, args.trusted_ref, data, sig):
+        return "error (signature)"
+    try:
+        if len(data) > _MAX_RECORD_BYTES:
+            raise ValueError("oversized")
+        rec = json.loads(data.decode("ascii"))
+        if not _valid_record(rec) or canonical_bytes(rec) != data:
+            raise ValueError("not canonical schema 1")
+    except (ValueError, UnicodeDecodeError):
+        return "error (record)"
+    if (rec["issue"], rec["repo"], rec["branch"]) != (args.issue, args.repo, args.branch):
+        return "stale (identity)"
+
+    clone = args.clone_dir
+    base = _git_out(clone, "merge-base", args.trusted_ref, args.head_ref).strip()
+    for path in approval_paths:
+        entry = rec["files"].get(path)
+        if entry is None:
+            return f"stale (new trigger: {_clean(path)})"
+        if (entry["base"] != blob_entry(clone, base, path)
+                or entry["head"] != blob_entry(clone, args.head_ref, path)):
+            return f"stale (changed since approval: {_clean(path)})"
+    if size_trigger:
+        size = rec["size"]
+        if size is None or size["lines_changed"] != lines_changed \
+                or size["head_tree"] != _git_out(clone, "rev-parse", f"{args.head_ref}^{{tree}}").strip() \
+                or size["base_tree"] != _git_out(clone, "rev-parse", f"{base}^{{tree}}").strip():
+            return "stale (size)"
+    return (f"matched ({_clean(rec.get('approved_by', '?'))} at "
+            f"{_clean(rec.get('approved_at', '?'))}, head {_clean(rec.get('head_sha', '?'))})")
+
+
+def evaluate_approval(args, approval_paths: list, size_trigger: bool, lines_changed: int) -> str:
+    """No exception escapes: anything unexpected is `error (<reason>)`."""
+    try:
+        return _match_approval(args, approval_paths, size_trigger, lines_changed)
+    except _ApprovalError as exc:
+        return exc.line
+    except Exception as exc:  # noqa: BLE001 -- the gate must never crash past this point
+        return f"error ({type(exc).__name__})"
+
+
 def main() -> None:
     args = parse_args()
     cfg = load_config(args.config, args.clone_dir, args.base_ref)
@@ -346,7 +540,7 @@ def main() -> None:
 
     boundary_findings = []
     if ".factory/adapter.yaml" in changed_files:
-        boundary_findings = _boundary_escalation_findings(clone_dir, args.base_ref)
+        boundary_findings = _boundary_escalation_findings(clone_dir, args.base_ref, args.head_ref)
 
     hard_trigger = bool(triggered)
     size_trigger = enabled and size_blocks and lines_changed > size_budget
@@ -359,6 +553,17 @@ def main() -> None:
     else:
         status = "PASS"
 
+    # #551: only a HUMAN_REQUIRED verdict is ever looked up, and only with --issue.
+    approval_line = None
+    approval_paths = []
+    if status == "HUMAN_REQUIRED" and args.issue is not None:
+        approval_paths = [fpath for fpath, _ in triggered]
+        if boundary_trigger and ".factory/adapter.yaml" not in approval_paths:
+            approval_paths.append(".factory/adapter.yaml")
+        approval_line = evaluate_approval(args, approval_paths, size_trigger, lines_changed)
+        if approval_line.startswith("matched "):
+            status = "PASS"
+
     severity = "critical" if status == "HUMAN_REQUIRED" else "none"
     findings_count = len(triggered) + len(boundary_findings) + (1 if size_trigger else 0)
 
@@ -391,6 +596,9 @@ def main() -> None:
     if size_trigger:
         print(f"  - [size] {lines_changed} lines > {size_budget} budget")
     print(f"LINES_CHANGED: {lines_changed}")
+    if approval_line is not None:
+        print(f"APPROVAL: {approval_line}")
+        print(f"TRIGGERED_PATHS: {json.dumps(approval_paths)}")
 
 
 if __name__ == "__main__":
PATCH
~~~~

- [ ] **Step 4: Run them and confirm they pass, and that the existing gate tests are unchanged**

```bash
PYTHONPATH=scripts python -m pytest tests/test_blast_radius.py tests/test_diff_rank.py tests/test_adapter.py -q -p no:cacheprovider 2>&1 | tail -1
ps aux | grep -c "[g]pg-agent --homedir /tmp/bg" || true
```

Expected: `... passed` with no failures, then `0`. No agent outlives a verify.

- [ ] **Step 5: Commit**

```bash
git add scripts/gate_blast_radius.py tests/test_blast_radius.py
git commit -q -m "feat(#551): gate honours a gpg-signed, blob-bound blast-radius approval

Without --issue the output is byte-identical. With it, a HUMAN_REQUIRED verdict is
checked against <issue>.json(.sig) per the spec's match rule; only a valid signature
from main's .factory/blast-approvers.asc plus an exact <mode> <blob> match at base and
head turns it into PASS.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `codehost merge --match-head`

**Files:**
- Modify: `tests/test_provider_codehost_parity.py`, `tests/test_provider_cli.py`
- Modify: `scripts/factory_core/providers/codehost/base.py`, `github.py`, `gitlab.py`, `scripts/factory_core/providers/cli.py`

Spec F.2: the close-time merge is pinned to the head that was checked. The CLI passes `match_head` only when `--match-head` is given. The existing fakes in `tests/test_provider_cli.py` and `main_red_fixer.py`'s call therefore keep working unchanged, and `tests/test_provider_signature_conformance.py` keeps every provider's signature aligned with the ABC.

- [ ] **Step 1: Write the failing tests**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_provider_cli.py b/tests/test_provider_cli.py
index f7ed721..6c3a452 100644
--- a/tests/test_provider_cli.py
+++ b/tests/test_provider_cli.py
@@ -397,3 +397,27 @@ def test_codehost_close_keyword_empty_prints_blank_line(monkeypatch, capsys):
     monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "close-keyword", "--id", "42"])
     cli_mod.main()
     assert capsys.readouterr().out.strip() == ""
+
+
+def test_codehost_merge_match_head_passed_through_and_omitted_when_absent(monkeypatch):
+    """#551: --match-head reaches merge_change; without it the kwarg is not passed at all,
+    so a provider that predates the parameter keeps working."""
+    import factory_core.providers.cli as cli_mod
+
+    calls = []
+
+    class _FakeCodeHost:
+        def merge_change(self, id, **kw):
+            calls.append((id, kw))
+            return True
+    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
+
+    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "merge", "--id", "7",
+                                      "--match-head", "b" * 40])
+    cli_mod.main()
+    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "merge", "--id", "7"])
+    cli_mod.main()
+
+    assert calls[0] == ("7", {"strategy": "merge", "delete_branch": True, "repo": None,
+                              "match_head": "b" * 40})
+    assert calls[1] == ("7", {"strategy": "merge", "delete_branch": True, "repo": None})
diff --git a/tests/test_provider_codehost_parity.py b/tests/test_provider_codehost_parity.py
index 96c5efc..c3524e6 100644
--- a/tests/test_provider_codehost_parity.py
+++ b/tests/test_provider_codehost_parity.py
@@ -160,6 +160,15 @@ def test_merge_change_matches_run_dag_close_preview(monkeypatch):
     assert ok is True
 
 
+def test_merge_change_pins_match_head_commit(monkeypatch):
+    """#551: close-preview merges only the head its blast-radius check verified."""
+    calls = []
+    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
+    GitHubCodeHost().merge_change("9", match_head="a" * 40)
+    assert calls[0] == ["gh", "pr", "merge", "9", "--merge", "--delete-branch",
+                        "--match-head-commit", "a" * 40]
+
+
 def test_merge_change_matches_main_red_fixer(monkeypatch):
     calls = []
     monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
PATCH
~~~~

- [ ] **Step 2: Run them and confirm they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_cli.py tests/test_provider_codehost_parity.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `2 failed, 58 passed in ...`. There is an unrecognized `--match-head` argument, and an unexpected keyword `match_head`.

- [ ] **Step 3: Implement the pin**

~~~~bash
git apply <<'PATCH'
diff --git a/scripts/factory_core/providers/cli.py b/scripts/factory_core/providers/cli.py
index db8bb4d..62f8221 100644
--- a/scripts/factory_core/providers/cli.py
+++ b/scripts/factory_core/providers/cli.py
@@ -195,8 +195,11 @@ def _codehost_mark_ready(args):
 
 
 def _codehost_merge(args):
+    # match_head only when given, so providers/fakes predating #551 keep working.
+    extra = {"match_head": args.match_head} if args.match_head else {}
     _print(get_codehost().merge_change(
-        args.id, strategy=args.strategy, delete_branch=args.delete_branch, repo=args.repo))
+        args.id, strategy=args.strategy, delete_branch=args.delete_branch, repo=args.repo,
+        **extra))
 
 
 def _codehost_checks(args):
@@ -355,6 +358,8 @@ def main():
     cm.add_argument("--strategy", default="merge")
     cm.add_argument("--delete-branch", action=argparse.BooleanOptionalAction, default=True)
     cm.add_argument("--repo")
+    cm.add_argument("--match-head", default=None,
+                    help="Refuse the merge unless the change head is exactly this sha (#551)")
     cm.set_defaults(func=_codehost_merge)
 
     cc = csub.add_parser("checks")
diff --git a/scripts/factory_core/providers/codehost/base.py b/scripts/factory_core/providers/codehost/base.py
index c38c9d4..442a734 100644
--- a/scripts/factory_core/providers/codehost/base.py
+++ b/scripts/factory_core/providers/codehost/base.py
@@ -39,8 +39,10 @@ class CodeHost(ABC):
 
     @abstractmethod
     def merge_change(self, id: str, strategy: str = "merge",
-                      delete_branch: bool = True, repo: str | None = None) -> bool:
-        ...
+                      delete_branch: bool = True, repo: str | None = None,
+                      match_head: str | None = None) -> bool:
+        """Merge; with match_head, refuse unless the change's head is exactly that sha
+        (#551: the close-time blast-radius check pins the head it verified)."""
 
     @abstractmethod
     def get_change_checks(self, id: str, fields: str = "name,bucket,link",
diff --git a/scripts/factory_core/providers/codehost/github.py b/scripts/factory_core/providers/codehost/github.py
index cc27528..9290238 100644
--- a/scripts/factory_core/providers/codehost/github.py
+++ b/scripts/factory_core/providers/codehost/github.py
@@ -88,13 +88,15 @@ class GitHubCodeHost(CodeHost):
         subprocess.run(cmd, capture_output=True)
 
     def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
-                      repo: str | None = None) -> bool:
+                      repo: str | None = None, match_head: str | None = None) -> bool:
         cmd = ["gh", "pr", "merge", id]
         if repo:
             cmd += ["--repo", repo]
         cmd.append(f"--{strategy}")
         if delete_branch:
             cmd.append("--delete-branch")
+        if match_head:
+            cmd += ["--match-head-commit", match_head]
         r = subprocess.run(cmd, capture_output=True)
         return r.returncode == 0
 
diff --git a/scripts/factory_core/providers/codehost/gitlab.py b/scripts/factory_core/providers/codehost/gitlab.py
index 3579e3c..c05f569 100644
--- a/scripts/factory_core/providers/codehost/gitlab.py
+++ b/scripts/factory_core/providers/codehost/gitlab.py
@@ -90,7 +90,8 @@ class GitLabCodeHost(CodeHost):
         )
 
     def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
-                      repo: str | None = None) -> bool:
+                      repo: str | None = None, match_head: str | None = None) -> bool:
+        # match_head maps to the MR merge API's `sha` parameter once this is live (#551).
         _validate_change_id(id)
         raise NotImplementedError(
             "live GitLab MR merge API — deferred; see "
PATCH
~~~~

- [ ] **Step 4: Run them and confirm they pass, with the provider contract tests**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_cli.py tests/test_provider_codehost_parity.py tests/test_provider_signature_conformance.py tests/test_provider_codehost_contract.py tests/test_provider_codehost_gitlab.py tests/test_provider_codehost_base.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `132 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/providers tests/test_provider_cli.py tests/test_provider_codehost_parity.py
git commit -q -m "feat(#551): codehost merge --match-head pins the merged head

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/blast_gate_check.sh`, the one deterministic checker

**Files:**
- Create: `tests/test_blast_gate_check.sh` (0755); modify `.github/workflows/ci.yml` (one `run:` line after `tests/test_verdict_gate_check.sh`). A `.sh` test that CI does not list never runs.
- Create: `scripts/blast_gate_check.sh` (0755)

What the checker does (spec B, plus decisions 1, 5 and 6):

1. Validates the arguments. A non-numeric issue, a bad mode, or an unset `FACTORY_REPO_SLUG` exits 2.
2. In `run` mode, reads the branch and HEAD first.
3. Fetches `+refs/heads/main:refs/blast-gate/trusted-main` with a 60 s cap. A failure exits 2.
4. In `close` mode, finds exactly one `refs/heads/feat/issue-<N>-*` branch on origin (an `awk` prefix match, so `feat/issue-770-*` never counts for 77; zero or two or more exits 2). It fetches that branch to `refs/blast-gate/head` and checks it out detached in a `mktemp` worktree, which the EXIT trap removes. It then prints `HEAD_SHA=`.
5. Computes the merge base against the trusted ref, then the changed files and line count, with the same three-dot semantics Phase 0 used.
6. Picks the hotspots file: the working tree's in `run` mode, `git show trusted-main:docs/codeindex-hotspots.md` in `close` mode.
7. Runs the baked gate with `--issue --repo --branch --trusted-ref --head-ref --approvals-dir` and copies the output to `--out`.
8. Exits 0 only for `PASS`/`SKIPPED`, 1 for `HUMAN_REQUIRED`, and 2 otherwise. `ERROR` is not a pass.
9. Truncates `--out` first, so an agent-written `blast.md` from validate never survives for `report`. On an exit-2 failure before the gate printed a `STATUS:`, it writes a fail-closed stub (`STATUS: HUMAN_REQUIRED`, `TRIGGER: checker-error`, `REASON: ...`; architect cycle 2).

With `--hold` and a non-zero result:
- `run` mode first writes the hold marker `{"issue","head_sha","exit"}` to `${FACTORY_BLAST_HOLD_FILE:-$ARTIFACTS_DIR/blast-hold.json}` (decision 12). It then takes spec C.1-C.4: verified push with no PR (refine-push pattern), `<issue>.request.json` written atomically, `needs-discussion`, Blocked, and the `<!-- df-blast-hold -->` comment through `post_marker_comment.sh`.
- `close` mode takes F.3: the request (`mode: close`), the label and the comment. There is no push, no board move and no marker.
- The stdout of every hold-step call goes to stderr.

The caller exits 1.

The test runs real git against a local bare `origin`. Tracker calls are stubbed with an exported `python3` function, the `tests/test_verdict_gate_check.sh` convention, which passes every other `python3` call through to the real interpreter.

- [ ] **Step 1: Write the failing test and register it in CI**

~~~~bash
git apply <<'PATCH'
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 0eb4ab8..f507dd7 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -45,6 +45,7 @@ jobs:
       - run: bash tests/test_config_deletion.sh
       - run: bash tests/test_epic_autopilot_config.sh
       - run: bash tests/test_verdict_gate_check.sh
+      - run: bash tests/test_blast_gate_check.sh
       - run: bash tests/test_budget_context.sh
       - run: bash tests/test_scheduler_lib.sh
       - run: bash tests/test_gate_diff_md_visibility.sh
diff --git a/tests/test_blast_gate_check.sh b/tests/test_blast_gate_check.sh
new file mode 100755
index 0000000..2f20761
--- /dev/null
+++ b/tests/test_blast_gate_check.sh
@@ -0,0 +1,216 @@
+#!/usr/bin/env bash
+# Covers #551: scripts/blast_gate_check.sh — exit 0/1/2 mapping (ERROR and empty output
+# are 2), fetch failure is 2, run vs close head resolution, ambiguous branch refused,
+# hotspots source per mode, a signed approval passing end to end, and the --hold steps.
+# Tracker calls are stubbed with an exported python3 function (tests/test_verdict_gate_check.sh
+# convention); git runs for real against a local bare "origin".
+# Run: bash tests/test_blast_gate_check.sh
+set -uo pipefail
+
+REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
+CHECK="${REPO_ROOT}/scripts/blast_gate_check.sh"
+
+WORK=$(mktemp -d /tmp/bgc-test-XXXXXX)
+export GNUPGHOME="$WORK/gnupg"
+cleanup() {
+  gpgconf --homedir "$GNUPGHOME" --kill all >/dev/null 2>&1 || true
+  rm -rf "$WORK"
+}
+trap cleanup EXIT
+
+command -v gpg >/dev/null || { echo "FAIL: gpg is required"; exit 1; }
+
+PASSED=0; FAILED=0
+ok()   { echo "  PASS: $1"; PASSED=$((PASSED+1)); }
+bad()  { echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); }
+check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
+
+export STUB_LOG="$WORK/stub.log"
+_REAL_PY3="$(command -v python3)"
+export _REAL_PY3
+python3() {
+  case "$*" in
+    *providers/cli.py*tracker\ label*)
+      # Records whether the hold marker already exists when the first side effect runs.
+      [ -f "${FACTORY_BLAST_HOLD_FILE:-$ARTIFACTS_DIR/blast-hold.json}" ] && echo "marker-before-label" >> "$STUB_LOG"
+      echo "providers $*" >> "$STUB_LOG"; echo "tracker-stdout-noise"; return 0 ;;
+    *providers/cli.py*) echo "providers $*" >> "$STUB_LOG"; echo "tracker-stdout-noise"; return 0 ;;
+    *factory_core/cli.py*marker*) echo "*Posted by Test*" ;;
+    *) "$_REAL_PY3" "$@" ;;
+  esac
+}
+export -f python3
+
+# Never inherit a live run's hold-marker path (#551 architect cycle 2): a factory run that
+# executes this test must not get a fake hold marker written into its own artifacts.
+unset FACTORY_BLAST_HOLD_FILE
+export FACTORY_REPO_SLUG="test/repo"
+export SCHEDULER_STATE_DIR="$WORK/state"
+export ARTIFACTS_DIR="$WORK/artifacts"
+export FACTORY_CONFIG_PATH="$WORK/absent-baked.yaml"
+export BLAST_GATE_BAKED_DIR="$WORK/no-baked-gate"
+mkdir -p "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"
+
+g() { git -C "$CLONE_DIR" "$@"; }
+
+# --- Fixture: origin with main (trusted key) + feature branches --------------------
+mkdir -m 700 "$GNUPGHOME"
+gpg --batch --passphrase '' --quick-gen-key 'Op <op@example.test>' ed25519 sign 1y >/dev/null 2>&1
+git init -q --bare -b main "$WORK/origin.git"
+export CLONE_DIR="$WORK/clone"
+git clone -q "$WORK/origin.git" "$CLONE_DIR" 2>/dev/null
+g config user.email t@example.test; g config user.name T
+mkdir -p "$CLONE_DIR/.claude/skills/refinement" "$CLONE_DIR/.claude/skills/x" "$CLONE_DIR/.factory" "$CLONE_DIR/src"
+printf 'blast_radius:\n  enabled: true\n  hotspot_score_floor: 5.0\n  size_budget_lines: 400\n  size_budget_blocks: false\n' \
+  > "$CLONE_DIR/.claude/skills/refinement/config.yaml"
+echo "echo v0" > "$CLONE_DIR/.claude/skills/x/run.sh"
+echo "print(0)" > "$CLONE_DIR/src/app.py"
+gpg --batch --armor --export > "$CLONE_DIR/.factory/blast-approvers.asc"
+g add -A; g commit -qm main; g push -q origin main 2>/dev/null
+
+g checkout -qb feat/issue-77-risky
+echo "echo v1" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam risky
+g push -q origin feat/issue-77-risky 2>/dev/null
+RISKY_SHA=$(g rev-parse HEAD)
+g checkout -q main; g checkout -qb feat/issue-78-clean
+echo "print(1)" > "$CLONE_DIR/src/app.py"; g commit -qam clean
+g push -q origin feat/issue-78-clean 2>/dev/null
+for b in feat/issue-79-a feat/issue-79-b feat/issue-770-other; do g push -q origin "HEAD:refs/heads/$b" 2>/dev/null; done
+
+_run() {  # _run <args...>  -> sets RC, OUT_TXT
+  : > "$STUB_LOG"
+  OUT_TXT=$(bash "$CHECK" "$@" 2>"$WORK/stderr.log"); RC=$?
+}
+
+echo "--- run mode: verdict mapping ---"
+g checkout -q feat/issue-78-clean
+_run --issue 78 --mode run --out "$WORK/b.md"
+check "PASS → exit 0" '[ "$RC" = 0 ]'
+check "PASS → HEAD_SHA printed" '[ "$OUT_TXT" = "HEAD_SHA=$(g rev-parse HEAD)" ]'
+check "PASS → --out written" 'grep -qx "STATUS: PASS" "$WORK/b.md"'
+check "PASS → no tracker calls" '[ ! -s "$STUB_LOG" ]'
+check "trusted ref created by the checker" 'g rev-parse -q --verify refs/blast-gate/trusted-main >/dev/null'
+
+g checkout -q feat/issue-77-risky
+_run --issue 77 --mode run --out "$WORK/b.md"
+check "HUMAN_REQUIRED → exit 1" '[ "$RC" = 1 ]'
+check "HUMAN_REQUIRED → APPROVAL: none" 'grep -qx "APPROVAL: none" "$WORK/b.md"'
+check "no --hold → no tracker calls" '[ ! -s "$STUB_LOG" ]'
+
+echo "--- run mode: hotspots come from the working tree ---"
+g checkout -q feat/issue-78-clean
+mkdir -p "$CLONE_DIR/docs"; echo "  9.0  src/app.py  (1d / 1t)  1 loc" > "$CLONE_DIR/docs/codeindex-hotspots.md"
+_run --issue 78 --mode run
+check "working-tree hotspot → exit 1 in run mode" '[ "$RC" = 1 ]'
+_run --issue 78 --mode close
+check "close mode reads main's hotspots (none) → exit 0" '[ "$RC" = 0 ]'
+rm -rf "$CLONE_DIR/docs"
+
+echo "--- signed approval passes end to end ---"
+g checkout -q feat/issue-77-risky
+mkdir -p "$SCHEDULER_STATE_DIR/blast-approvals"
+BASE=$(g merge-base refs/blast-gate/trusted-main HEAD)
+REC="$SCHEDULER_STATE_DIR/blast-approvals/77.json"
+PYTHONPATH="$REPO_ROOT/scripts" "$_REAL_PY3" - "$CLONE_DIR" "$BASE" "$REC" <<'EOF'
+import sys, gate_blast_radius as g
+clone, base, out = sys.argv[1:]
+p = ".claude/skills/x/run.sh"
+rec = {"schema": 1, "issue": 77, "repo": "test/repo", "branch": "feat/issue-77-risky",
+       "head_sha": "x", "base_sha": base, "size": None, "trigger": "skill-security",
+       "files": {p: {"base": g.blob_entry(clone, base, p), "head": g.blob_entry(clone, "HEAD", p)}},
+       "approved_at": "2026-11-21T00:00:00Z", "approved_by": "Op", "approvals_count": 1,
+       "previous_head_sha": None}
+open(out, "wb").write(g.canonical_bytes(rec))
+EOF
+gpg --batch --yes --detach-sign -o "$REC.sig" "$REC" 2>/dev/null
+_run --issue 77 --mode run --out "$WORK/b.md"
+check "matching signed approval → exit 0" '[ "$RC" = 0 ]'
+check "matching signed approval → APPROVAL: matched" 'grep -q "^APPROVAL: matched (Op at " "$WORK/b.md"'
+_run --issue 77 --mode close
+check "same approval honoured in close mode" '[ "$RC" = 0 ]'
+rm -f "$REC" "$REC.sig"
+
+echo "--- exit 2 cases ---"
+_run --issue abc --mode run
+check "non-numeric issue → exit 2" '[ "$RC" = 2 ]'
+_run --issue 77 --mode sideways
+check "bad mode → exit 2" '[ "$RC" = 2 ]'
+mkdir -p "$WORK/fake-gate"
+printf 'print("STATUS: ERROR")\n' > "$WORK/fake-gate/gate_blast_radius.py"
+BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run --out "$WORK/b.md"
+check "STATUS: ERROR → exit 2 (not a pass value)" '[ "$RC" = 2 ]'
+check "baked gate preferred over the clone copy" 'grep -qx "STATUS: ERROR" "$WORK/b.md"'
+printf '' > "$WORK/fake-gate/gate_blast_radius.py"
+BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run
+check "empty gate output → exit 2" '[ "$RC" = 2 ]'
+g remote set-url origin "$WORK/missing.git"
+echo "STATUS: PASS" > "$WORK/stale.md"
+_run --issue 77 --mode run --out "$WORK/stale.md"
+check "fetch failure → exit 2" '[ "$RC" = 2 ]'
+check "fetch failure → stale --out replaced by a fail-closed stub" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/stale.md" && grep -qx "TRIGGER: checker-error" "$WORK/stale.md"'
+g remote set-url origin "$WORK/origin.git"
+
+echo "--- close mode: head resolution ---"
+g checkout -q main
+_run --issue 77 --mode close --out "$WORK/b.md"
+check "close resolves origin's feat/issue-77-* (not local HEAD) → exit 1" '[ "$RC" = 1 ]'
+check "close prints the origin head" '[ "$OUT_TXT" = "HEAD_SHA=$RISKY_SHA" ]'
+check "close ignores feat/issue-770-*" '! grep -q "found" "$WORK/stderr.log"'
+check "temporary worktree removed" '[ "$(g worktree list | wc -l)" = 1 ]'
+_run --issue 79 --mode close
+check "two feat/issue-79-* branches → exit 2" '[ "$RC" = 2 ]'
+_run --issue 80 --mode close
+check "no feat/issue-80-* branch → exit 2" '[ "$RC" = 2 ]'
+
+echo "--- --hold, run mode ---"
+g checkout -q feat/issue-77-risky
+echo "echo v2" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam "local only"
+LOCAL_SHA=$(g rev-parse HEAD)
+rm -f "$ARTIFACTS_DIR/blast-hold.json"
+_run --issue 77 --mode run --out "$WORK/b.md" --hold
+check "hold keeps exit 1" '[ "$RC" = 1 ]'
+check "hold stdout is only the HEAD_SHA line" '[ "$OUT_TXT" = "HEAD_SHA=$LOCAL_SHA" ]'
+check "marker written before the first tracker call" 'grep -q "marker-before-label" "$STUB_LOG"'
+check "hold pushed the branch (no PR)" '[ "$(git -C "$WORK/origin.git" rev-parse refs/heads/feat/issue-77-risky)" = "$LOCAL_SHA" ]'
+REQ="$SCHEDULER_STATE_DIR/blast-approvals/77.request.json"
+check "request written" '[ "$(jq -r .mode "$REQ")" = run ] && [ "$(jq -r .head_sha "$REQ")" = "$LOCAL_SHA" ]'
+check "request lists the triggered path" '[ "$(jq -c .triggered_paths "$REQ")" = "[\".claude/skills/x/run.sh\"]" ]'
+check "needs-discussion added" 'grep -q "tracker label --id 77 --add needs-discussion" "$STUB_LOG"'
+check "board set to Blocked" 'grep -q "tracker set-status --id 77 --status blocked" "$STUB_LOG"'
+check "df-blast-hold comment posted" 'grep -q "tracker comment --id 77 --marker <!-- df-blast-hold -->" "$STUB_LOG"'
+check "no PR call" '! grep -q "codehost" "$STUB_LOG"'
+check "blast-hold.json written" '[ "$(jq -c . "$ARTIFACTS_DIR/blast-hold.json")" = "{\"issue\":77,\"head_sha\":\"$LOCAL_SHA\",\"exit\":1}" ]'
+
+echo "--- --hold writes the marker to FACTORY_BLAST_HOLD_FILE (entrypoint's directory) ---"
+mkdir -p "$WORK/entrypoint-artifacts"
+rm -f "$ARTIFACTS_DIR/blast-hold.json"
+FACTORY_BLAST_HOLD_FILE="$WORK/entrypoint-artifacts/blast-hold.json" _run --issue 77 --mode run --hold
+check "marker at FACTORY_BLAST_HOLD_FILE" '[ "$(jq -r .head_sha "$WORK/entrypoint-artifacts/blast-hold.json")" = "$LOCAL_SHA" ]'
+check "nothing in the node's own ARTIFACTS_DIR" '[ ! -f "$ARTIFACTS_DIR/blast-hold.json" ]'
+
+echo "--- --hold under BENCH_MODE=stub does not push ---"
+echo "echo v3" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam "bench local"
+BENCH_MODE=stub _run --issue 77 --mode run --hold
+check "bench hold exit 1" '[ "$RC" = 1 ]'
+check "bench hold did not push" '[ "$(git -C "$WORK/origin.git" rev-parse refs/heads/feat/issue-77-risky)" = "$LOCAL_SHA" ]'
+
+echo "--- --hold, close mode ---"
+rm -f "$ARTIFACTS_DIR/blast-hold.json"
+g checkout -q main
+_run --issue 77 --mode close --hold
+check "close hold exit 1" '[ "$RC" = 1 ]'
+check "close request mode" '[ "$(jq -r .mode "$REQ")" = close ]'
+check "close hold labels" 'grep -q "tracker label --id 77 --add needs-discussion" "$STUB_LOG"'
+check "close hold does not move the board" '! grep -q "set-status" "$STUB_LOG"'
+check "close hold writes no blast-hold.json" '[ ! -f "$ARTIFACTS_DIR/blast-hold.json" ]'
+
+echo "--- --hold on a checker failure (exit 2) ---"
+g checkout -q feat/issue-77-risky
+BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run --hold
+check "checker failure hold keeps exit 2" '[ "$RC" = 2 ]'
+check "checker failure still labels" 'grep -q "add needs-discussion" "$STUB_LOG"'
+check "checker failure blast-hold.json exit 2" '[ "$(jq -r .exit "$ARTIFACTS_DIR/blast-hold.json")" = 2 ]'
+
+echo ""
+echo "Results: ${PASSED} passed, ${FAILED} failed"
+[ "$FAILED" -eq 0 ]
PATCH
~~~~

- [ ] **Step 2: Run it and confirm it fails**

```bash
bash tests/test_blast_gate_check.sh 2>&1 | tail -1
```

Expected: `Results: 8 passed, 41 failed`. Every invocation hits `bash: .../scripts/blast_gate_check.sh: No such file or directory`; the passes are negative assertions (no tracker calls, no extra worktree, ...) that a missing script trivially satisfies.

- [ ] **Step 3: Create the checker**

~~~~bash
git apply <<'PATCH'
diff --git a/scripts/blast_gate_check.sh b/scripts/blast_gate_check.sh
new file mode 100755
index 0000000..3c160e2
--- /dev/null
+++ b/scripts/blast_gate_check.sh
@@ -0,0 +1,273 @@
+#!/usr/bin/env bash
+# The one deterministic blast-radius checker (#551). Used by the blast-gate DAG node, the
+# push-and-pr re-check and the close-preview merge check so the three cannot drift.
+# It recomputes the verdict itself: it never reads an agent-written blast.md, and the
+# signed-approval lookup lives in gate_blast_radius.py (spec "Match rule").
+#
+# Usage: blast_gate_check.sh --issue <N> --mode run|close [--out <blast.md>] [--hold]
+#   --mode run    head = the clone's HEAD and current branch
+#   --mode close  head = the single feat/issue-<N>-* branch on origin, checked out
+#                 detached in a temporary worktree
+#   --out         where the gate output is written; truncated first, and on an exit-2
+#                 failure before the gate printed a STATUS: it gets a fail-closed stub
+#                 (STATUS: HUMAN_REQUIRED, TRIGGER: checker-error, REASON: ...)
+#   --hold        on a non-zero result, take the hold steps (spec C.1-C.5 for run,
+#                 F.3 for close) before exiting. In run mode the hold marker goes to
+#                 $FACTORY_BLAST_HOLD_FILE -- exported by entrypoint.sh, because Archon
+#                 gives every bash node its own $ARTIFACTS_DIR -- falling back to
+#                 $ARTIFACTS_DIR/blast-hold.json when unset (local runs).
+#
+# stdout: exactly one `HEAD_SHA=<sha>` line once the head is resolved (close-preview
+#   pins the merge to it). Diagnostics and every hold-step call's output go to stderr.
+# Exit 0 only when STATUS: is exactly PASS or SKIPPED. Exit 1 for HUMAN_REQUIRED. Exit 2
+#   for anything else (empty output, crash, failed fetch, non-numeric issue, ambiguous
+#   branch). ERROR is not a pass value: the gate never emits it. Callers treat 1 and 2
+#   both as "stop" -- do not wrap this call in `|| true`.
+set -uo pipefail
+
+SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
+_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"
+TRUSTED_REF="refs/blast-gate/trusted-main"
+HEAD_REF="refs/blast-gate/head"
+HOLD_MARKER="<!-- df-blast-hold -->"
+
+ISSUE="" MODE="" OUT="" HOLD=false
+while [ $# -gt 0 ]; do
+  case "$1" in
+    --issue) ISSUE="${2:-}"; shift 2 ;;
+    --mode) MODE="${2:-}"; shift 2 ;;
+    --out) OUT="${2:-}"; shift 2 ;;
+    --hold) HOLD=true; shift ;;
+    *) echo "blast_gate_check.sh: unknown argument '$1'" >&2; exit 2 ;;
+  esac
+done
+
+CLONE="${CLONE_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
+APPROVALS_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals"
+BRANCH="" HEAD_SHA="" REASON="" RESULT="" WT=""
+
+_cleanup() {
+  if [ -n "$WT" ]; then
+    git -C "$CLONE" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
+  fi
+  [ -n "${RESULT:-}" ] && rm -f "$RESULT"
+  [ -n "${HOT_TMP:-}" ] && rm -f "$HOT_TMP"
+}
+trap _cleanup EXIT
+
+_fail() { REASON="$1"; echo "blast_gate_check.sh: $1" >&2; }
+
+_check() {
+  # Never leave an earlier (agent-written) blast.md in place: --out always ends up holding
+  # this run's verdict, or the fail-closed stub written below.
+  [ -n "$OUT" ] && : > "$OUT"
+  case "$ISSUE" in ''|*[!0-9]*) _fail "issue number '${ISSUE}' is not numeric"; return 2 ;; esac
+  case "$MODE" in run|close) ;; *) _fail "mode must be run or close, got '${MODE}'"; return 2 ;; esac
+  [ -n "${FACTORY_REPO_SLUG:-}" ] || { _fail "FACTORY_REPO_SLUG is not set"; return 2; }
+
+  local wt_dir hot
+  if [ "$MODE" = "run" ]; then
+    # Resolved before the fetch so a hold after a failed fetch still knows what to push.
+    BRANCH=$(git -C "$CLONE" branch --show-current 2>/dev/null)
+    HEAD_SHA=$(git -C "$CLONE" rev-parse --verify -q HEAD 2>/dev/null)
+    [ -n "$BRANCH" ] && [ -n "$HEAD_SHA" ] || { _fail "no current branch/HEAD in ${CLONE}"; return 2; }
+  fi
+
+  # Trusted base: created here, every time, never the clone's own main/origin/main.
+  if ! timeout 60 git -C "$CLONE" fetch --no-tags --quiet origin \
+      "+refs/heads/main:${TRUSTED_REF}"; then
+    _fail "could not fetch origin main into ${TRUSTED_REF}"; return 2
+  fi
+
+  if [ "$MODE" = "run" ]; then
+    wt_dir="$CLONE"
+    hot="$CLONE/docs/codeindex-hotspots.md"
+  else
+    local heads count
+    heads=$(timeout 30 git -C "$CLONE" ls-remote --heads origin "refs/heads/feat/issue-${ISSUE}-*" 2>/dev/null \
+      | awk -v p="refs/heads/feat/issue-${ISSUE}-" 'index($2, p) == 1 {print $2}')
+    count=$(printf '%s' "$heads" | grep -c . || true)
+    [ "$count" -eq 1 ] || { _fail "expected exactly one feat/issue-${ISSUE}-* branch on origin, found ${count}"; return 2; }
+    BRANCH="${heads#refs/heads/}"
+    if ! timeout 60 git -C "$CLONE" fetch --no-tags --quiet origin "+refs/heads/${BRANCH}:${HEAD_REF}"; then
+      _fail "could not fetch ${BRANCH}"; return 2
+    fi
+    HEAD_SHA=$(git -C "$CLONE" rev-parse --verify -q "${HEAD_REF}^{commit}")
+    WT=$(mktemp -d /tmp/blast-gate-wt-XXXXXX)
+    rmdir "$WT"
+    git -C "$CLONE" worktree add --detach --quiet "$WT" "$HEAD_SHA" >/dev/null 2>&1 \
+      || { WT=""; _fail "could not check out ${HEAD_SHA} in a worktree"; return 2; }
+    wt_dir="$WT"
+    HOT_TMP=$(mktemp /tmp/blast-gate-hot-XXXXXX)
+    git -C "$CLONE" show "${TRUSTED_REF}:docs/codeindex-hotspots.md" > "$HOT_TMP" 2>/dev/null || : > "$HOT_TMP"
+    hot="$HOT_TMP"
+  fi
+  echo "HEAD_SHA=${HEAD_SHA}"
+
+  local base changed shortstat added deleted lines gate
+  base=$(git -C "$wt_dir" merge-base "$TRUSTED_REF" "$HEAD_SHA" 2>/dev/null) \
+    || { _fail "no merge-base between ${TRUSTED_REF} and ${HEAD_SHA}"; return 2; }
+  # Three-dot semantics, as validate Phase 0 always computed them.
+  changed=$(git -C "$wt_dir" diff "$base" "$HEAD_SHA" --name-only 2>/dev/null) \
+    || { _fail "git diff --name-only failed"; return 2; }
+  shortstat=$(git -C "$wt_dir" diff "$base" "$HEAD_SHA" --shortstat 2>/dev/null) \
+    || { _fail "git diff --shortstat failed"; return 2; }
+  added=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)
+  deleted=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)
+  lines=$((added + deleted))
+
+  # The baked gate decides; the clone copy only when the image has none (validate.md F9).
+  gate="${BLAST_GATE_BAKED_DIR:-/opt/dark-factory/scripts}/gate_blast_radius.py"
+  [ -f "$gate" ] || gate="${SCRIPT_DIR}/gate_blast_radius.py"
+  RESULT=$(mktemp /tmp/blast-gate-out-XXXXXX)
+  printf '%s\n' "$changed" | python3 "$gate" \
+    --changed-files-stdin \
+    --lines-changed "$lines" \
+    --hotspots "$hot" \
+    --config .claude/skills/refinement/config.yaml \
+    --clone-dir "$wt_dir" \
+    --base-ref "$base" \
+    --issue "$ISSUE" \
+    --repo "$FACTORY_REPO_SLUG" \
+    --branch "$BRANCH" \
+    --trusted-ref "$TRUSTED_REF" \
+    --head-ref "$HEAD_SHA" \
+    --approvals-dir "$APPROVALS_DIR" > "$RESULT"
+  [ -n "$OUT" ] && cp "$RESULT" "$OUT"
+
+  local status
+  status=$(grep -m1 '^STATUS:' "$RESULT" | awk '{print $2}')
+  case "$status" in
+    PASS|SKIPPED) return 0 ;;
+    HUMAN_REQUIRED) return 1 ;;
+    *) _fail "gate produced no usable STATUS (got '${status:-<none>}')"; return 2 ;;
+  esac
+}
+
+# --- Hold steps (only with --hold, only after a non-zero result) ---------------------
+
+_field() { [ -n "${RESULT:-}" ] && [ -f "$RESULT" ] && sed -n "s/^$1: //p" "$RESULT" | head -1; }
+
+_push_branch() {
+  # Verified push without a PR (refine-push pattern, workflows/archon-dark-factory.yaml).
+  PUSH_NOTE=""
+  [ -n "$BRANCH" ] && [ -n "$HEAD_SHA" ] || { PUSH_NOTE="no branch to push"; return 1; }
+  # BENCH_MODE=stub never pushes (push-and-pr's own contract).
+  [ "${BENCH_MODE:-}" = "stub" ] && { PUSH_NOTE="BENCH_MODE=stub: not pushed"; return 1; }
+  local err rc remote
+  err=$(timeout 30 git -C "$CLONE" push -u origin "$BRANCH" 2>&1); rc=$?
+  remote=$(timeout 10 git -C "$CLONE" ls-remote --exit-code --heads origin "refs/heads/$BRANCH" 2>/dev/null \
+    | awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}')
+  if [ -n "$remote" ] && [ "$remote" = "$HEAD_SHA" ]; then
+    return 0
+  fi
+  [ "$rc" -eq 124 ] && err="push timed out after 30s"
+  PUSH_NOTE=$(printf '%s' "${err:-origin has ${remote:-nothing}, expected $HEAD_SHA}" | tail -n 5 | tail -c 600 | iconv -c -f UTF-8 -t UTF-8)
+  return 1
+}
+
+_write_request() {
+  local paths tmp
+  paths=$(_field TRIGGERED_PATHS)
+  printf '%s' "${paths:-[]}" | jq -e 'type == "array"' >/dev/null 2>&1 || paths='[]'
+  mkdir -p -m 0755 "$APPROVALS_DIR" 2>/dev/null || return 1
+  tmp=$(mktemp "$APPROVALS_DIR/.${ISSUE}.request.XXXXXX") || return 1
+  jq -cn --argjson issue "$ISSUE" --arg repo "${FACTORY_REPO_SLUG:-}" --arg branch "$BRANCH" \
+    --arg head "$HEAD_SHA" --arg mode "$MODE" --argjson paths "$paths" \
+    --arg at "$(date -u +%FT%TZ)" \
+    '{schema: 1, issue: $issue, repo: $repo, branch: $branch, head_sha: $head, mode: $mode,
+      triggered_paths: $paths, written_at: $at}' > "$tmp" \
+    && chmod 0644 "$tmp" && mv -f "$tmp" "$APPROVALS_DIR/${ISSUE}.request.json" \
+    || { rm -f "$tmp"; return 1; }
+}
+
+_hold() {
+  local rc="$1" pushed=true where body files branch_line
+  case "$ISSUE" in ''|*[!0-9]*) echo "blast_gate_check.sh: cannot hold a non-numeric issue" >&2; return ;; esac
+
+  # The marker records the decision, so it is written first: a node timeout in any later
+  # step must still leave a hold, not an ordinary failure (spec C.5; steps are idempotent).
+  local marker="${FACTORY_BLAST_HOLD_FILE:-${ARTIFACTS_DIR:+$ARTIFACTS_DIR/blast-hold.json}}"
+  if [ "$MODE" = "run" ] && [ -n "$marker" ]; then
+    jq -cn --argjson issue "$ISSUE" --arg head "$HEAD_SHA" --argjson exit "$rc" \
+      '{issue: $issue, head_sha: $head, exit: $exit}' > "$marker" \
+      || echo "blast_gate_check.sh: WARNING: could not write ${marker}" >&2
+  fi
+
+  if [ "$MODE" = "run" ]; then
+    _push_branch || pushed=false
+  fi
+  _write_request || echo "blast_gate_check.sh: WARNING: could not write ${APPROVALS_DIR}/${ISSUE}.request.json" >&2
+  python3 "$_PCLI" tracker label --id "$ISSUE" --add needs-discussion >&2 \
+    || echo "blast_gate_check.sh: WARNING: could not add needs-discussion to #${ISSUE}" >&2
+  if [ "$MODE" = "run" ]; then
+    python3 "$_PCLI" tracker set-status --id "$ISSUE" --status blocked >&2 \
+      || echo "blast_gate_check.sh: WARNING: could not set #${ISSUE} to Blocked" >&2
+  fi
+
+  if [ "$MODE" = "run" ]; then
+    where="This run stops here: no PR is opened and nothing is merged."
+  else
+    where="Factory Close refused to merge the PR."
+  fi
+  if [ "$pushed" = true ] || [ "$MODE" = "close" ]; then
+    branch_line="**Branch:** \`${BRANCH:-<unknown>}\` (on origin)"
+  else
+    branch_line="**Branch:** \`${BRANCH:-<unknown>}\` — **not on origin** (${PUSH_NOTE}). There is nothing to review until a run pushes it."
+  fi
+  files=$(_field_block)
+  if [ "$rc" -eq 2 ]; then
+    body="## Blast-Radius Gate — Held (checker failed)
+
+The deterministic blast-radius checker could not produce a verdict, so the change is held (fail closed). ${where}
+
+**Reason:** ${REASON:-unknown}
+${branch_line}
+**Head:** \`${HEAD_SHA:-<unknown>}\`
+
+Fix the cause above, then remove \`needs-discussion\` to let the scheduler retry."
+  else
+    body="## Blast-Radius Gate — Held for human review
+
+This change touches paths that need a human sign-off. ${where}
+
+${branch_line}
+**Head:** \`${HEAD_SHA}\`
+**Trigger:** $(_field TRIGGER)
+**Approval:** $(_field APPROVAL)
+
+**Triggered files:**
+${files}
+
+### To approve
+
+Review the diff of the triggered files on \`${BRANCH}\` at \`${HEAD_SHA}\`, then on the operator host run:
+
+\`\`\`bash
+approve-blast.sh ${ISSUE} --sha ${HEAD_SHA}
+\`\`\`
+
+It writes a signed approval bound to exactly this content and lifts \`needs-discussion\`; the next run passes the gate. A \`stale (...)\` approval above means only the named file changed since the last approval — review that delta.
+
+Removing \`needs-discussion\` by hand does nothing useful: the next run recomputes the gate and holds again."
+  fi
+  bash "${SCRIPT_DIR}/post_marker_comment.sh" "$ISSUE" "$HOLD_MARKER" "$body" >&2 \
+    || echo "blast_gate_check.sh: WARNING: could not post the hold comment on #${ISSUE}" >&2
+}
+
+_field_block() {
+  [ -n "${RESULT:-}" ] && [ -f "$RESULT" ] || return 0
+  sed -n '/^TRIGGERED_FILES:$/,/^LINES_CHANGED:/p' "$RESULT" | grep '^  - ' | head -20
+}
+
+_check
+RC=$?
+if [ "$RC" -eq 2 ] && [ -n "$OUT" ] && ! grep -q '^STATUS:' "$OUT" 2>/dev/null; then
+  # The gate never ran (or printed nothing): record the fail-closed hold, never a pass.
+  printf 'STATUS: HUMAN_REQUIRED\nGATE_TYPE: blast\nFINDINGS_COUNT: 0\nSEVERITY: critical\n---\nTRIGGER: checker-error\nREASON: %s\n' \
+    "$(printf '%s' "${REASON:-unknown}" | tr '\n' ' ')" > "$OUT" 2>/dev/null || true
+fi
+if [ "$RC" -ne 0 ] && [ "$HOLD" = true ]; then
+  _hold "$RC"
+fi
+exit "$RC"
PATCH
~~~~

- [ ] **Step 4: Run it and confirm it passes**

```bash
bash tests/test_blast_gate_check.sh 2>&1 | tail -1
ls -l scripts/blast_gate_check.sh | cut -c1-10
```

Expected: `Results: 49 passed, 0 failed`, then `-rwxr-xr-x`.

- [ ] **Step 5: Commit**

```bash
git add scripts/blast_gate_check.sh tests/test_blast_gate_check.sh .github/workflows/ci.yml
git commit -q -m "feat(#551): blast_gate_check.sh — the one deterministic blast-radius checker

Recomputes the verdict on HEAD (run) or the PR branch on origin (close), against
origin main fetched into refs/blast-gate/trusted-main; exit 0 only for PASS/SKIPPED.
--hold pushes the branch (no PR), writes the review request, labels, comments and
writes blast-hold.json.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Wire the checker into the DAG

**Files:**
- Create: `tests/test_blast_gate_dag.py`; modify `tests/test_docs_conformance_dag.py` (`test_resolve_path_unchanged` now expects `blast-gate`)
- Modify: `workflows/archon-dark-factory.yaml`

The patch makes these changes:

- **New `blast-gate` bash node, spec C.** It sits right after `validate`, runs for `new`/`continue`/`resolve`, has `depends_on: [validate]` and no `trigger_rule`, and runs `--mode run --out "$ARTIFACTS_DIR/blast.md" --hold`, overwriting whatever validate left there.
- **Re-pointing**, verified against the current file:
  - `budget-conformance` `[validate]` → `[blast-gate]`;
  - `conformance` `[enforce-budget-conformance, validate]` → `[enforce-budget-conformance, blast-gate]`;
  - `push-resolve` `[validate]` → `[blast-gate]`.
- **`push-and-pr`, spec E.** The re-check sits after the archive commit and before `git push -u origin`. It is downstream of the `BENCH_MODE=stub` early exit, so stub runs skip it along with the push. Timeout goes from 30 s to 180 s (decision 4).
- **`close-preview`, spec F.**
  - The check sits between the unchanged `needs-discussion` refusal and `mark-ready`/merge.
  - It refuses on any non-zero exit or an empty `HEAD_SHA`.
  - The merge is pinned with `--match-head "$BLAST_HEAD"`.
  - After a successful merge it deletes `<issue>.json`, `.json.sig` and `.request.json` (best effort).
  - Timeout is 180 s.

`blast-gate` has one upstream and no `trigger_rule`, so `scripts/check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` and its count tripwire are unchanged.

- [ ] **Step 1: Write the failing tests**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_blast_gate_dag.py b/tests/test_blast_gate_dag.py
new file mode 100644
index 0000000..d203ed7
--- /dev/null
+++ b/tests/test_blast_gate_dag.py
@@ -0,0 +1,88 @@
+"""Static assertions for the #551 blast-radius enforcement points in the workflow DAG:
+the blast-gate node, the nodes re-pointed behind it, the push-and-pr re-check and the
+close-preview merge check (tests/test_verdict_gate_dag.py convention)."""
+from pathlib import Path
+
+import yaml
+
+_REPO_ROOT = Path(__file__).resolve().parents[1]
+_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
+
+
+def _nodes():
+    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
+    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}
+
+
+def _index(text: str, needle: str) -> int:
+    assert needle in text, f"{needle!r} not found"
+    return text.index(needle)
+
+
+def test_blast_gate_is_a_bash_node_after_validate():
+    node = _nodes()["blast-gate"]
+    assert "bash" in node and "command" not in node
+    assert node["depends_on"] == ["validate"]
+    assert "trigger_rule" not in node
+    for intent in ("'new'", "'continue'", "'resolve'"):
+        assert intent in node["when"]
+
+
+def test_blast_gate_runs_the_checker_in_run_mode_with_hold():
+    bash = _nodes()["blast-gate"]["bash"]
+    assert "blast_gate_check.sh" in bash
+    assert "--mode run" in bash and "--hold" in bash
+    assert '--out "$ARTIFACTS_DIR/blast.md"' in bash
+    assert "verdict_gate_check.sh" not in bash, "blast-gate must not trust an agent-written blast.md"
+    assert "|| true" not in bash
+
+
+def test_nodes_behind_blast_gate():
+    nodes = _nodes()
+    assert nodes["budget-conformance"]["depends_on"] == ["blast-gate"]
+    assert nodes["conformance"]["depends_on"] == ["enforce-budget-conformance", "blast-gate"]
+    assert nodes["push-resolve"]["depends_on"] == ["blast-gate"]
+
+
+def test_push_and_pr_rechecks_after_archive_and_before_push():
+    bash = _nodes()["push-and-pr"]["bash"]
+    archive = _index(bash, 'git commit -m "docs: archive spec/plan')
+    check = _index(bash, 'bash "$_BGC" --issue "$ISSUE" --mode run')
+    push = _index(bash, 'git push -u origin "$BRANCH"')
+    create = _index(bash, "codehost open-change")
+    assert archive < check < push < create
+    stub_exit = _index(bash, 'if [ "${BENCH_MODE:-}" = "stub" ]')
+    assert stub_exit < check, "BENCH_MODE=stub skips the re-check along with the push"
+
+
+def test_close_preview_checks_before_merge_and_pins_the_head():
+    bash = _nodes()["close-preview"]["bash"]
+    refusal = _index(bash, "BLOCKED — needs-discussion label present")
+    check = _index(bash, "--mode close")
+    merge = _index(bash, "codehost merge --id")
+    assert refusal < check < merge
+    assert '--match-head "$BLAST_HEAD"' in bash
+    assert 'grep -c \'needs-discussion\'' in bash, "the existing label refusal stays"
+
+
+def test_close_preview_deletes_the_record_after_merge():
+    bash = _nodes()["close-preview"]["bash"]
+    merged = _index(bash, 'echo "PR #$PR_NUM merged successfully"')
+    cleanup = _index(bash, 'rm -f "$_BA/${ISSUE}.json"')
+    assert merged < cleanup
+
+
+def test_timeouts_fit_the_checker():
+    """A node timeout is not catchable: it would end a hold halfway (plan decision 4)."""
+    nodes = _nodes()
+    assert nodes["blast-gate"]["timeout"] == 120000
+    assert nodes["close-preview"]["timeout"] == 180000
+    assert nodes["push-and-pr"]["timeout"] == 180000
+
+
+def test_checker_prefers_the_baked_copy_everywhere():
+    nodes = _nodes()
+    for node_id in ("blast-gate", "push-and-pr", "close-preview"):
+        bash = nodes[node_id]["bash"]
+        assert "_BGC=/opt/dark-factory/scripts/blast_gate_check.sh" in bash, node_id
+        assert '[ -f "$_BGC" ] || _BGC=' in bash, node_id
diff --git a/tests/test_docs_conformance_dag.py b/tests/test_docs_conformance_dag.py
index 71c7c1b..ad47ec3 100644
--- a/tests/test_docs_conformance_dag.py
+++ b/tests/test_docs_conformance_dag.py
@@ -79,7 +79,8 @@ def test_budget_nodes_use_docs_conformance_scenario():
 
 
 def test_resolve_path_unchanged():
-    assert _nodes()["push-resolve"]["depends_on"] == ["validate"]
+    # docs-conformance never joins the resolve path; push-resolve waits on blast-gate (#551).
+    assert _nodes()["push-resolve"]["depends_on"] == ["blast-gate"]
 
 
 def test_dag_validators_pass():
PATCH
~~~~

- [ ] **Step 2: Run them and confirm they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_blast_gate_dag.py tests/test_docs_conformance_dag.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `9 failed, 37 passed in ...`. Eight are `KeyError: 'blast-gate'`, a missing needle or the old `push-and-pr` timeout; one is `test_resolve_path_unchanged`.

- [ ] **Step 3: Edit the workflow**

~~~~bash
git apply <<'PATCH'
diff --git a/workflows/archon-dark-factory.yaml b/workflows/archon-dark-factory.yaml
index eaaecc2..e678f50 100644
--- a/workflows/archon-dark-factory.yaml
+++ b/workflows/archon-dark-factory.yaml
@@ -244,16 +244,33 @@ nodes:
         exit 1
       fi
 
+      # #551: agent-free blast-radius check on the PR head (fresh clone, no agent has run).
+      # Without a valid signed approval for a HUMAN_REQUIRED diff the merge is refused,
+      # whatever the labels say; a checker failure also refuses (fail closed). The merge is
+      # pinned to the head that was checked, so a push in between cannot merge unchecked.
+      _BGC=/opt/dark-factory/scripts/blast_gate_check.sh
+      [ -f "$_BGC" ] || _BGC="${CLONE_DIR:-.}/dark-factory/scripts/blast_gate_check.sh"  # TARGET-PATH
+      BLAST_RC=0
+      BLAST_OUT=$(bash "$_BGC" --issue "$ISSUE" --mode close --out "$ARTIFACTS_DIR/blast.md" --hold) || BLAST_RC=$?
+      BLAST_HEAD=$(printf '%s\n' "$BLAST_OUT" | sed -n 's/^HEAD_SHA=//p' | tail -1)
+      if [ "$BLAST_RC" -ne 0 ] || [ -z "$BLAST_HEAD" ]; then
+        echo "close-preview: BLOCKED — blast-radius check did not pass for issue #$ISSUE (exit $BLAST_RC); see the df-blast-hold comment"
+        exit 1
+      fi
+
       # Mark ready (factory creates PRs as drafts) then merge to main.
       # codehost merge always exits 0 (its Python process never raises on a failed
       # merge) — merge_change's own bool return is the real signal, so check stdout.
       python3 "$_PCLI" codehost mark-ready --id "$PR_NUM" 2>/dev/null || true
-      MERGE_OK=$(python3 "$_PCLI" codehost merge --id "$PR_NUM")
+      MERGE_OK=$(python3 "$_PCLI" codehost merge --id "$PR_NUM" --match-head "$BLAST_HEAD")
       if [ "$MERGE_OK" != "true" ]; then
-        echo "ERROR: PR #$PR_NUM could not be merged (check approval status or conflicts)"
+        echo "ERROR: PR #$PR_NUM could not be merged (check approval status, conflicts, or a push after the blast-radius check)"
         exit 1
       fi
       echo "PR #$PR_NUM merged successfully"
+      # A record must not outlive its ticket (best effort).
+      _BA="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals"
+      rm -f "$_BA/${ISSUE}.json" "$_BA/${ISSUE}.json.sig" "$_BA/${ISSUE}.request.json" 2>/dev/null || true
 
       # Move the sub-issue to Done (never the epic — it stays In Progress)
       if python3 "$_PCLI" tracker set-status --id "$ISSUE" --status done; then
@@ -265,7 +282,10 @@ nodes:
       echo "CLOSED"
     depends_on: [parse-intent, fetch-issue]
     when: "$parse-intent.output.intent == 'close'"
-    timeout: 30000
+    # 180s (was 30s): the #551 blast-radius check fetches main and the PR branch (60s cap
+    # each), lists origin heads (30s cap) and runs the gate in a temporary worktree
+    # before the merge; a timeout still fails closed (no merge).
+    timeout: 180000
 
   - id: post-merge-update-codeindex
     bash: |
@@ -1106,6 +1126,24 @@ nodes:
     when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue' || $parse-intent.output.intent == 'resolve'"
     idle_timeout: 300000
 
+  # Deterministic blast-radius enforcement (#551, #407). Re-runs the gate itself on the
+  # current HEAD; it never reads the blast.md the validate agent wrote (it overwrites it).
+  # On a hold blast_gate_check.sh --hold pushes the branch (no PR), writes the review
+  # request, adds needs-discussion, sets Blocked, posts the df-blast-hold comment and
+  # writes blast-hold.json, which entrypoint.sh treats as a hold, not a failure.
+  - id: blast-gate
+    bash: |
+      # deterministic: recomputes the verdict; never reads an agent-written blast.md
+      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
+      _BGC=/opt/dark-factory/scripts/blast_gate_check.sh
+      [ -f "$_BGC" ] || _BGC="${CLONE_DIR:-.}/dark-factory/scripts/blast_gate_check.sh"  # TARGET-PATH
+      bash "$_BGC" --issue "$ISSUE" --mode run --out "$ARTIFACTS_DIR/blast.md" --hold
+    depends_on: [validate]
+    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue' || $parse-intent.output.intent == 'resolve'"
+    # 120s, not the spec's 60s: fetch (60s cap) + verified push (30s + 10s) + three tracker
+    # calls must fit, and a node timeout is not catchable -- it would end the hold halfway.
+    timeout: 120000
+
   # Context budget telemetry — captures pre-prompt token estimate for conformance phase
   - id: budget-conformance
     bash: |
@@ -1134,7 +1172,7 @@ nodes:
         --diff-file "$DIFF_TMP" \
         --out "$ARTIFACTS_DIR/context-budget.conformance.json" || true
       rm -f "$DIFF_TMP"
-    depends_on: [validate]
+    depends_on: [blast-gate]
     when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
     timeout: 30000
 
@@ -1149,7 +1187,7 @@ nodes:
   - id: conformance
     command: dark-factory-conformance
     denied_tools: []  # #196: side_effect_level 5 (config/config.yaml side_effect.phase_levels.conformance) — profile removes no tools
-    depends_on: [enforce-budget-conformance, validate]
+    depends_on: [enforce-budget-conformance, blast-gate]
     when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
     idle_timeout: 600000
 
@@ -1288,7 +1326,7 @@ nodes:
         codehost find-change --branch "$BRANCH" --repo "$FACTORY_REPO_SLUG" --exact)
       echo "Pushed branch ${BRANCH} to PR #${PR_NUM:-unknown}"
       echo "PR_NUM=${PR_NUM:-}"
-    depends_on: [validate]
+    depends_on: [blast-gate]
     when: "$parse-intent.output.intent == 'resolve'"
     timeout: 30000
 
@@ -1342,6 +1380,17 @@ nodes:
         git commit -m "docs: archive spec/plan for issue #${ISSUE}"
       fi
 
+      # #551: validate's fixes, conformance's reconcile commits and the archive commit above
+      # all landed after blast-gate -- re-check the exact HEAD about to be pushed. A hold
+      # pushes the branch, labels, comments and writes blast-hold.json, then stops here:
+      # no push to a PR, no gh pr create.
+      _BGC=/opt/dark-factory/scripts/blast_gate_check.sh
+      [ -f "$_BGC" ] || _BGC="${CLONE_DIR:-.}/dark-factory/scripts/blast_gate_check.sh"  # TARGET-PATH
+      if ! bash "$_BGC" --issue "$ISSUE" --mode run --out "$ARTIFACTS_DIR/blast.md" --hold; then
+        echo "push-and-pr: held by the blast-radius re-check for issue #${ISSUE} — no PR push, no PR created" >&2
+        exit 1
+      fi
+
       # Fail loudly if the push is rejected (e.g. token missing 'workflow' scope for .github/workflows
       # changes). Previously the rejection was swallowed and a misleading success comment was posted.
       if ! git push -u origin "$BRANCH"; then
@@ -1450,7 +1499,10 @@ nodes:
       fi
     depends_on: [conformance-gate, docs-conformance-gate]
     when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
-    timeout: 30000
+    # 180s (was 30s): the #551 re-check (fetch capped at 60s, gate, and on a hold a 40s
+    # verified push plus tracker calls) now runs before the existing push and PR creation;
+    # a node timeout is not catchable.
+    timeout: 180000
 
   # Context budget telemetry — captures pre-prompt token estimate for code-review phase
   - id: budget-code-review
PATCH
~~~~

- [ ] **Step 4: Run them, the other DAG tests and both DAG validators, and confirm they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_blast_gate_dag.py tests/test_docs_conformance_dag.py tests/test_verdict_gate_dag.py tests/test_budget_enforce_dag.py tests/test_workflow_or_join.py tests/test_push_gate_dag.py -q -p no:cacheprovider 2>&1 | tail -1
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: `163 passed in ...`, then `DAG trigger_rule check passed for 1 workflow file(s).` and `when: expression lint passed for 1 workflow file(s).`

- [ ] **Step 5: Commit**

```bash
git add workflows/archon-dark-factory.yaml tests/test_blast_gate_dag.py tests/test_docs_conformance_dag.py
git commit -q -m "feat(#551): blast-gate node, push-and-pr re-check, head-pinned close check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Validate Phase 0 calls the checker and stops judging

**Files:**
- Modify: `tests/test_validate_blast_gate_baseref.py`
- Modify: `commands/dark-factory-validate.md` (Phase 0 bash block)

Spec D.
- Phase 0 runs the checker in `run` mode without `--hold`. On a non-zero exit it stops validation with `exit 1`.
- It no longer comments, labels or moves the board. The `blast-gate` node does that, so the agent is off the signal path.
- The non-existent `"Validate issue #N"` instruction is gone.
- The kill-switch paragraph is kept unchanged (`test_no_working_tree_kill_switch_precheck` still pins it).
- The "baked gate preferred" and `--base-ref`/`--clone-dir` pins move to the checker, where the invocation now lives.

- [ ] **Step 1: Rewrite the tests**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_validate_blast_gate_baseref.py b/tests/test_validate_blast_gate_baseref.py
index ff748ad..e36ab1c 100644
--- a/tests/test_validate_blast_gate_baseref.py
+++ b/tests/test_validate_blast_gate_baseref.py
@@ -1,18 +1,26 @@
-"""Static-assertion tests for commands/dark-factory-validate.md Phase 0 prose.
+"""Static-assertion tests for commands/dark-factory-validate.md Phase 0 prose and the
+checker it calls (scripts/blast_gate_check.sh, #551).
 
 No bash-execution harness exists for command files in this repo (see
 tests/test_ceiling_revisit_command.py) -- these assert on the literal fenced-block
-text instead.
+text instead. The checker itself is executed by tests/test_blast_gate_check.sh.
 """
 from pathlib import Path
 
-COMMAND_FILE = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-validate.md"
+_ROOT = Path(__file__).resolve().parents[1]
+COMMAND_FILE = _ROOT / "commands" / "dark-factory-validate.md"
+CHECKER = _ROOT / "scripts" / "blast_gate_check.sh"
 
 
 def _text():
     return COMMAND_FILE.read_text(encoding="utf-8")
 
 
+def _phase0():
+    text = _text()
+    return text[text.index("## Phase 0:"):text.index("## Phase 1:")]
+
+
 def test_no_working_tree_kill_switch_precheck():
     """Requirement 8 (F1): Phase 0 must not read blast_radius.enabled from the working
     tree and skip the gate invocation before it runs -- that would silently reopen the
@@ -22,15 +30,35 @@ def test_no_working_tree_kill_switch_precheck():
     assert "d.get('blast_radius', {}).get('enabled'" not in text
 
 
-def test_gate_invoked_with_base_ref_and_clone_dir():
-    text = _text()
-    assert "--base-ref \"$BASE_SHA\"" in text
-    assert "--clone-dir \"$REPO_ROOT\"" in text
-    assert 'BASE_SHA=$(git merge-base main HEAD' in text
+def test_phase0_calls_the_checker_not_the_gate():
+    phase0 = _phase0()
+    assert '--issue "$ISSUE_NUM" --mode run --out "$ARTIFACTS_DIR/blast.md"' in phase0
+    assert "--changed-files-stdin" not in phase0, "Phase 0 must not invoke the gate inline any more"
+    assert "GATE_SCRIPT" not in phase0
+    assert "--hold" not in phase0, "the hold steps belong to the blast-gate node"
 
 
-def test_gate_prefers_baked_script_with_fallback():
-    text = _text()
-    assert '/opt/dark-factory/scripts/gate_blast_radius.py' in text
-    assert 'dark-factory/scripts/gate_blast_radius.py' in text
-    assert '[ -f "$GATE_SCRIPT" ]' in text
+def test_phase0_prefers_the_baked_checker():
+    phase0 = _phase0()
+    assert "_BGC=/opt/dark-factory/scripts/blast_gate_check.sh" in phase0
+    assert '[ -f "$_BGC" ] || _BGC="dark-factory/scripts/blast_gate_check.sh"' in phase0
+
+
+def test_phase0_no_longer_comments_labels_or_moves_the_board():
+    phase0 = _phase0()
+    for forbidden in ("gh issue comment", "tracker label", "set-status", "Validate issue"):
+        assert forbidden not in phase0, forbidden
+
+
+def test_checker_passes_base_ref_clone_dir_and_trusted_merge_base():
+    text = CHECKER.read_text(encoding="utf-8")
+    assert '--base-ref "$base"' in text
+    assert '--clone-dir "$wt_dir"' in text
+    assert 'merge-base "$TRUSTED_REF" "$HEAD_SHA"' in text
+    assert '"+refs/heads/main:${TRUSTED_REF}"' in text
+
+
+def test_checker_prefers_baked_gate_with_fallback():
+    text = CHECKER.read_text(encoding="utf-8")
+    assert '/opt/dark-factory/scripts}/gate_blast_radius.py' in text
+    assert '[ -f "$gate" ] || gate="${SCRIPT_DIR}/gate_blast_radius.py"' in text
PATCH
~~~~

- [ ] **Step 2: Run them and confirm they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_validate_blast_gate_baseref.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `3 failed, 3 passed in ...`. The three Phase 0 tests fail; the kill-switch test and the two checker tests pass, because Task 4 already landed the checker.

- [ ] **Step 3: Replace the Phase 0 gate block**

~~~~bash
git apply <<'PATCH'
diff --git a/commands/dark-factory-validate.md b/commands/dark-factory-validate.md
index a67caa8..c72e31a 100644
--- a/commands/dark-factory-validate.md
+++ b/commands/dark-factory-validate.md
@@ -57,67 +57,31 @@ shell-level pre-check that reads `.claude/skills/refinement/config.yaml` and ski
 invocation; that would silently re-open the bypass this ticket closes.
 
 ```bash
-# 1. Get changed files and real line count
-CHANGED=$(git diff main...HEAD --name-only 2>/dev/null || echo "")
-ADDED=$(git diff main...HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)
-DELETED=$(git diff main...HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)
-LINES=$((ADDED + DELETED))
-
-# 2. Run the blast-radius checker — pass real line count via --lines-changed.
-# Prefer the baked copy (operator review F9): a target that tracks its own
-# dark-factory/scripts/gate_blast_radius.py (the F2 floor's shadowing-path entry
-# detects that at PR time) must not be the copy that decides its own gate. Falls back
-# to the clone-relative copy when the baked path is absent (local/non-container runs)
-# -- spec's explicitly-sanctioned fallback (F9), not a silent skip. --clone-dir points
-# at the actual clone either way, so adapter.yaml/git-history lookups resolve there.
-REPO_ROOT=$(git rev-parse --show-toplevel)
-BASE_SHA=$(git merge-base main HEAD 2>/dev/null || echo main)
-GATE_SCRIPT="/opt/dark-factory/scripts/gate_blast_radius.py"
-[ -f "$GATE_SCRIPT" ] || GATE_SCRIPT="dark-factory/scripts/gate_blast_radius.py"  # TARGET-PATH
-echo "$CHANGED" | python3 "$GATE_SCRIPT" \
-  --changed-files-stdin \
-  --lines-changed "$LINES" \
-  --hotspots docs/codeindex-hotspots.md \
-  --config .claude/skills/refinement/config.yaml \
-  --clone-dir "$REPO_ROOT" \
-  --base-ref "$BASE_SHA" \
-  > "$ARTIFACTS_DIR/blast.md"
-
-# 3. Read verdict — guard with || true so grep's exit-1-on-no-match doesn't abort under set -e
-BLAST_STATUS=$(grep '^STATUS:' "$ARTIFACTS_DIR/blast.md" | cut -d' ' -f2 || true)
-BLAST_TRIGGER=$(grep '^TRIGGER:' "$ARTIFACTS_DIR/blast.md" | cut -d' ' -f2- || true)
-BLAST_FILES=$(grep '^\s*-' "$ARTIFACTS_DIR/blast.md" | head -10 || true)
-
-# 4. Block on HUMAN_REQUIRED
-if [ "$BLAST_STATUS" = "HUMAN_REQUIRED" ]; then
-  FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
-  gh issue comment "$ISSUE_NUM" --body "$(cat <<EOF
-## Blast-Radius Gate — BLOCKED
-
-The blast-radius gate has flagged this change as requiring human review before it can auto-merge.
-
-**Trigger:** $BLAST_TRIGGER
-
-**Triggered files:**
-$BLAST_FILES
-
-Remove the \`needs-discussion\` label after reviewing and approving the risk, then re-run validate:
-\`\`\`
-docker compose --profile factory run --rm dark-factory "Validate issue #$ISSUE_NUM"
-\`\`\`
----
-$FOOTER
-EOF
-)"
-  python3 dark-factory/scripts/factory_core/providers/cli.py \
-    tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
-  # Move to Blocked on the project board
-  python3 dark-factory/scripts/factory_core/providers/cli.py \
-    tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
+# Run the one deterministic checker (#551) -- the same script the blast-gate DAG node,
+# the push-and-pr re-check and the close-time check run. It fetches origin main into
+# refs/blast-gate/trusted-main, computes the three-dot changed-file list and line count,
+# runs the baked gate_blast_radius.py (clone copy only when the image has none) with
+# --issue so a signed operator approval is honoured, and writes blast.md.
+_BGC=/opt/dark-factory/scripts/blast_gate_check.sh
+[ -f "$_BGC" ] || _BGC="dark-factory/scripts/blast_gate_check.sh"  # TARGET-PATH
+BLAST_RC=0
+bash "$_BGC" --issue "$ISSUE_NUM" --mode run --out "$ARTIFACTS_DIR/blast.md" || BLAST_RC=$?
+
+# Exit 1 = HUMAN_REQUIRED (no matching signed approval), exit 2 = checker failure.
+# Either way stop validating. Do not comment, label or move the board: the blast-gate
+# node right after this phase does all of that deterministically, whatever this phase does.
+if [ "$BLAST_RC" -ne 0 ]; then
+  echo "Blast-radius gate: held (exit $BLAST_RC) — see $ARTIFACTS_DIR/blast.md; blast-gate takes the hold steps."
   exit 1
 fi
 ```
 
+If the checker exits non-zero, end this phase there. Do not post a comment, add a label,
+change the board, push, or try to judge whether an earlier human review "resolved" the
+block — an operator approval is honoured only through the signed record the checker
+already looked up. On exit 0 (`PASS`, `SKIPPED`, or `PASS` on a matched approval)
+continue with Phase 1.
+
 ## Phase 1: LOAD
 
 Read the implementation context:
PATCH
~~~~

- [ ] **Step 4: Run them, with the command contract tests, and confirm they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_validate_blast_gate_baseref.py tests/test_command_headless_contract.py tests/test_command_issue_context_contract.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `19 passed in ...`

- [ ] **Step 5: Commit**

```bash
git add commands/dark-factory-validate.md tests/test_validate_blast_gate_baseref.py
git commit -q -m "feat(#551): validate Phase 0 runs blast_gate_check.sh and leaves the hold to blast-gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Conformance stops adjudicating the blast-radius gate

**Files:**
- Create: `tests/test_conformance_blast_adjudication.py`
- Modify: `commands/dark-factory-conformance.md` (Phase 1, after the opening bash block)

Spec H. The paragraph ships in the same PR as node C, never before it. Phase 5's own `needs-discussion`/`Blocked` writes are unchanged and pinned. No change is made to `dark-factory-code-review.md` or `dark-factory-docs-conformance.md`.

- [ ] **Step 1: Write the failing test**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_conformance_blast_adjudication.py b/tests/test_conformance_blast_adjudication.py
new file mode 100644
index 0000000..421eee0
--- /dev/null
+++ b/tests/test_conformance_blast_adjudication.py
@@ -0,0 +1,25 @@
+"""#551: the conformance phase must not decide whether the blast-radius gate was
+"genuinely resolved" -- enforcement is deterministic (blast-gate node, push-and-pr
+re-check, close-time check). Static assertions on the command text."""
+from pathlib import Path
+
+_CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-conformance.md"
+
+
+def _text():
+    return _CMD.read_text(encoding="utf-8")
+
+
+def test_phase1_states_conformance_does_not_adjudicate_blast_radius():
+    text = _text()
+    phase1 = text[text.index("## Phase 1: LOAD"):text.index("## Phase 2:")]
+    assert "Conformance does not adjudicate the blast-radius gate." in phase1
+    for name in ("`blast.md`", "`blast-gate`", "`push-and-pr` re-check", "close-time check"):
+        assert name in phase1, name
+
+
+def test_phase5_label_and_board_writes_unchanged():
+    text = _text()
+    phase5 = text[text.index("## Phase 5: BLOCKED"):]
+    assert "tracker label --id $ISSUE_NUM --add needs-discussion" in phase5
+    assert 'tracker set-status --id "$ISSUE_NUM" --status blocked' in phase5
PATCH
~~~~

- [ ] **Step 2: Run it and confirm it fails**

```bash
PYTHONPATH=scripts python -m pytest tests/test_conformance_blast_adjudication.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `1 failed, 1 passed in ...`

- [ ] **Step 3: Add the paragraph**

~~~~bash
git apply <<'PATCH'
diff --git a/commands/dark-factory-conformance.md b/commands/dark-factory-conformance.md
index ea40964..d755b8b 100644
--- a/commands/dark-factory-conformance.md
+++ b/commands/dark-factory-conformance.md
@@ -49,6 +49,13 @@ source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
 ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
 ```
 
+**Conformance does not adjudicate the blast-radius gate.** Once dispatched it runs its
+review; it does not consult `blast.md`, a blast-radius comment, or a blast-radius
+`needs-discussion`/`Blocked` state to decide whether to run. Enforcement is the
+`blast-gate` node, the `push-and-pr` re-check and the close-time check (#551) — the DAG
+only reaches this phase after `blast-gate` passed. Your own Phase 5 writes of
+`needs-discussion` and `Blocked` are unchanged.
+
 1. Read `.claude/skills/refinement/config.yaml` and extract the `conformance` block.
 2. If `conformance.enabled` is `false`:
    - Write `$ARTIFACTS_DIR/conformance.md` with content: `STATUS: SKIPPED\nREASON: conformance.enabled=false`
PATCH
~~~~

- [ ] **Step 4: Run it and confirm it passes**

```bash
PYTHONPATH=scripts python -m pytest tests/test_conformance_blast_adjudication.py tests/test_command_headless_contract.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `... passed` with no failures.

- [ ] **Step 5: Commit**

```bash
git add commands/dark-factory-conformance.md tests/test_conformance_blast_adjudication.py
git commit -q -m "feat(#551): conformance does not adjudicate the blast-radius gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: A hold is not a failure (`entrypoint.sh`)

**Files:**
- Create: `tests/test_entrypoint_blast_hold.sh` (0755); modify `.github/workflows/ci.yml` (one `run:` line after `tests/test_entrypoint_session_window.sh`, inside the `/var/lib/dark-factory` scratch block). The test uses its own `SCHEDULER_STATE_DIR`, so CI's "none of the entrypoint tests touched `/var/lib/dark-factory`" assertion still holds.
- Modify: `entrypoint.sh`

Spec G.
- `entrypoint.sh` exports `FACTORY_BLAST_HOLD_FILE="$ARTIFACTS_DIR/blast-hold.json"` (the entrypoint's own run directory) next to `FACTORY_RUN_CONFIG_JSON`, before the workflow loop (decision 12).
- `_handle_blast_hold` is defined next to `_handle_session_window_pause`, and the retry loop calls it right after the session-window check.
- With the marker present and naming this run's issue, it records a run-record row (`--stage blast_hold --verdict human_required`, `head_sha` detail), posts the cost report and returns 0. The loop then exits 0, with no `run_post_mortem`, no `_write_error_signature` and no `on_failure`.
- Unlike the session-window pause, it writes nothing for the scheduler. With no signature, `check_failure_signature` has nothing to compare, and with no rollback a hold never resets or decrements a counter (owner decision).
- `scheduler.sh` is not changed.

The main loop cannot be executed from a test (see `tests/test_entrypoint_session_window.sh`), so section D of the test pins the export and the call order statically. Section A2 is the production layout: the marker sits at `FACTORY_BLAST_HOLD_FILE` while `ARTIFACTS_DIR` points elsewhere.

Like `tests/test_entrypoint_session_window.sh`, this test resolves `cli.py` through `CLONE_DIR="$(dirname "$REPO_ROOT")"`, so **the checkout directory must be named `dark-factory`**, as it is on CI and in the factory clone. In a differently named scratch directory some run-record checks fail for that reason alone.

- [ ] **Step 1: Write the failing test and register it in CI**

~~~~bash
git apply <<'PATCH'
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index f507dd7..49e02fa 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -27,6 +27,7 @@ jobs:
       - run: bash tests/test_entrypoint_clone_url.sh
       - run: bash tests/test_entrypoint_staged_excludes.sh
       - run: bash tests/test_entrypoint_session_window.sh
+      - run: bash tests/test_entrypoint_blast_hold.sh
       - run: bash tests/test_entrypoint_error_signature.sh
       - run: bash tests/test_entrypoint_adapter_validate.sh
       - run: bash tests/test_entrypoint_conflict_escalate.sh
diff --git a/tests/test_entrypoint_blast_hold.sh b/tests/test_entrypoint_blast_hold.sh
new file mode 100755
index 0000000..2398d8f
--- /dev/null
+++ b/tests/test_entrypoint_blast_hold.sh
@@ -0,0 +1,136 @@
+#!/usr/bin/env bash
+# Verifies _handle_blast_hold() (#551): with $ARTIFACTS_DIR/blast-hold.json present it
+# records a blast_hold run-record row, posts the cost report and returns 0 -- writing no
+# error signature and leaving retry counters untouched. Without the marker it returns 1
+# so the failure path runs unchanged. The main retry loop is not executable here
+# (mirrors tests/test_entrypoint_session_window.sh), so its call order is pinned by a
+# static check at the end.
+# Run: bash tests/test_entrypoint_blast_hold.sh
+set -uo pipefail
+
+SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
+REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
+
+export IDENTITY_SH="${IDENTITY_SH:-$REPO_ROOT/scripts/identity.sh}"
+export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$REPO_ROOT/scripts/factory_core/providers/cli.py}"
+export GH_TOKEN="stub-token"
+export CLAUDE_CODE_OAUTH_TOKEN="stub-token"
+
+git() { return 0; }
+export -f git
+gh() { echo "stub-title"; return 0; }
+export -f gh
+docker() { return 0; }
+export -f docker
+claude() { echo "stub"; return 0; }
+export -f claude
+
+# Never inherit a live run's hold-marker path (#551 architect cycle 2).
+unset FACTORY_BLAST_HOLD_FILE
+CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-bh-rundir-XXXXXX)
+export CURRENT_RUN_DIR
+SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-bh-statedir-XXXXXX)
+export SCHEDULER_STATE_DIR
+
+ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
+
+trap - ERR
+set +e; set +u; set +o pipefail
+
+# Same TARGET-PATH layout trick as tests/test_entrypoint_session_window.sh.
+CLONE_DIR="$(dirname "$REPO_ROOT")"
+ISSUE_NUM=551
+INTENT=continue
+RUN_ID=test-run-bh-1
+export SCHEDULER_STATE_DIR
+
+PASSED=0; FAILED=0
+check() {
+  if eval "$2"; then echo "  PASS: $1"; PASSED=$((PASSED+1));
+  else echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); fi
+}
+
+COST_REPORT_CALLS=0
+post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }
+SIG_CALLS=0
+_write_error_signature() { SIG_CALLS=$((SIG_CALLS+1)); }
+POST_MORTEM_CALLS=0
+run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
+
+echo "--- A: marker present -> hold, not a failure ---"
+ARTIFACTS_DIR=$(mktemp -d /tmp/ep-bh-artifacts-XXXXXX)
+echo '{"issue":551,"head_sha":"abc123def","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
+echo '{"retries":{"551":2}}' > "$SCHEDULER_STATE_DIR/scheduler-state.json"
+BEFORE=$(cat "$SCHEDULER_STATE_DIR/scheduler-state.json")
+_handle_blast_hold
+RC=$?
+check "returns 0" '[ "$RC" = 0 ]'
+check "cost report posted once" '[ "$COST_REPORT_CALLS" = 1 ]'
+check "no error signature written" '[ "$SIG_CALLS" = 0 ] && [ ! -d "$SCHEDULER_STATE_DIR/error-signatures" ]'
+check "no post-mortem" '[ "$POST_MORTEM_CALLS" = 0 ]'
+check "retry counters untouched" '[ "$(cat "$SCHEDULER_STATE_DIR/scheduler-state.json")" = "$BEFORE" ]'
+ROW=$(grep '"run_id": "test-run-bh-1"' "$SCHEDULER_STATE_DIR/runs.jsonl" 2>/dev/null | tail -1)
+check "blast_hold run-record row" '[ "$(echo "$ROW" | jq -r .stage)" = blast_hold ]'
+check "verdict human_required" '[ "$(echo "$ROW" | jq -r .verdict)" = human_required ]'
+check "row carries the held head" '[ "$(echo "$ROW" | jq -r .detail.head_sha)" = abc123def ]'
+
+echo "--- A2: marker at FACTORY_BLAST_HOLD_FILE, ARTIFACTS_DIR elsewhere (the production layout) ---"
+# Archon gives each bash node its own ARTIFACTS_DIR, so writer and reader never share it.
+NODE_DIR=$(mktemp -d /tmp/ep-bh-node-XXXXXX)
+echo '{"issue":551,"head_sha":"feedface0","exit":1}' > "$NODE_DIR/blast-hold.json"
+rm -f "$ARTIFACTS_DIR/blast-hold.json"
+COST_REPORT_CALLS=0
+FACTORY_BLAST_HOLD_FILE="$NODE_DIR/blast-hold.json" _handle_blast_hold
+RC=$?
+check "returns 0 from the exported path" '[ "$RC" = 0 ] && [ "$COST_REPORT_CALLS" = 1 ]'
+FACTORY_BLAST_HOLD_FILE="$NODE_DIR/missing.json" _handle_blast_hold
+RC=$?
+check "exported path wins over ARTIFACTS_DIR (absent there -> 1)" '[ "$RC" = 1 ]'
+rm -rf "$NODE_DIR"
+
+echo "--- A3: a marker for another issue is ignored ---"
+echo '{"issue":77,"head_sha":"0ther","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
+COST_REPORT_CALLS=0
+_handle_blast_hold
+RC=$?
+check "other issue's marker → returns 1" '[ "$RC" = 1 ] && [ "$COST_REPORT_CALLS" = 0 ]'
+SAVED_ISSUE="$ISSUE_NUM"; unset ISSUE_NUM
+echo '{"issue":551,"head_sha":"x","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
+_handle_blast_hold
+RC=$?
+check "ISSUE_NUM unset → returns 1" '[ "$RC" = 1 ]'
+ISSUE_NUM="$SAVED_ISSUE"
+
+echo "--- B: no marker -> falls through ---"
+rm -f "$ARTIFACTS_DIR/blast-hold.json"
+COST_REPORT_CALLS=0
+_handle_blast_hold
+RC=$?
+check "returns 1" '[ "$RC" = 1 ]'
+check "no cost report from the hold path" '[ "$COST_REPORT_CALLS" = 0 ]'
+
+echo "--- C: ARTIFACTS_DIR unset -> falls through ---"
+SAVED="$ARTIFACTS_DIR"; unset ARTIFACTS_DIR
+_handle_blast_hold
+RC=$?
+check "returns 1" '[ "$RC" = 1 ]'
+ARTIFACTS_DIR="$SAVED"
+
+echo "--- D: main loop checks the hold right after the session-window pause ---"
+LOOP=$(awk '/^while true; do$/,/^done$/' "$REPO_ROOT/entrypoint.sh")
+SW=$(printf '%s\n' "$LOOP" | grep -n '_handle_session_window_pause "\$TMP_OUT"' | head -1 | cut -d: -f1)
+BH=$(printf '%s\n' "$LOOP" | grep -n '_handle_blast_hold' | head -1 | cut -d: -f1)
+PM=$(printf '%s\n' "$LOOP" | grep -n 'run_post_mortem' | head -1 | cut -d: -f1)
+SIG=$(printf '%s\n' "$LOOP" | grep -n '_write_error_signature' | head -1 | cut -d: -f1)
+check "hold check present in the loop" '[ -n "$BH" ]'
+check "after the session-window check" '[ "$SW" -lt "$BH" ]'
+check "before post-mortem and the error signature" '[ "$BH" -lt "$PM" ] && [ "$BH" -lt "$SIG" ]'
+EXPORT=$(grep -n '^export FACTORY_BLAST_HOLD_FILE="\$ARTIFACTS_DIR/blast-hold.json"$' "$REPO_ROOT/entrypoint.sh" | head -1 | cut -d: -f1)
+LOOP_START=$(grep -n '^while true; do$' "$REPO_ROOT/entrypoint.sh" | head -1 | cut -d: -f1)
+check "FACTORY_BLAST_HOLD_FILE exported before the workflow loop" '[ -n "$EXPORT" ] && [ "$EXPORT" -lt "$LOOP_START" ]'
+check "hold branch exits 0" '[ -n "$BH" ] && printf "%s\n" "$LOOP" | sed -n "${BH},$((BH+3))p" | grep -q "exit 0"'
+
+rm -rf "$ARTIFACTS_DIR" "$SCHEDULER_STATE_DIR" "$CURRENT_RUN_DIR"
+echo ""
+echo "Results: ${PASSED} passed, ${FAILED} failed"
+[ "$FAILED" -eq 0 ]
PATCH
~~~~

- [ ] **Step 2: Run it and confirm it fails**

```bash
bash tests/test_entrypoint_blast_hold.sh 2>&1 | tail -1
```

Expected: `Results: 4 passed, 16 failed`. `_handle_blast_hold: command not found`, and there is no export and no hold check in the loop. The four passes are the negative assertions that already hold.

- [ ] **Step 3: Add the handler and call it from the loop**

~~~~bash
git apply <<'PATCH'
diff --git a/entrypoint.sh b/entrypoint.sh
index c372fae..112fa51 100755
--- a/entrypoint.sh
+++ b/entrypoint.sh
@@ -348,6 +348,39 @@ print("SESSION_WINDOW_MATCH_BRANCH=" + shlex.quote(str(d.get("branch", ""))))
   return 0
 }
 
+# A blast-radius hold (#551) is a human-review stop, not a failure. blast_gate_check.sh
+# --hold (blast-gate node or the push-and-pr re-check) writes $FACTORY_BLAST_HOLD_FILE
+# (exported below, in this run's own $ARTIFACTS_DIR) before it pushes, labels, sets
+# Blocked and comments. On that marker: record a
+# blast_hold run-record row, post the cost report, and return 0 so the caller exits 0
+# WITHOUT run_post_mortem, _write_error_signature or on_failure. Unlike the session-window
+# pause, nothing is written for the scheduler either: no signature (check_failure_signature
+# has nothing to compare) and no retry rollback (a hold never resets or decrements a
+# counter). $ARTIFACTS_DIR is per run, so the marker cannot leak from an earlier run.
+# Returns 1 when there is no marker, so the caller falls through unchanged.
+_handle_blast_hold() {
+  local marker="${FACTORY_BLAST_HOLD_FILE:-${ARTIFACTS_DIR:+$ARTIFACTS_DIR/blast-hold.json}}"
+  [ -n "$marker" ] && [ -f "$marker" ] || return 1
+  # Only this run's own issue: a marker for any other issue (e.g. one a test wrote through
+  # an inherited FACTORY_BLAST_HOLD_FILE) must never turn a real failure into exit 0.
+  local held_issue head
+  held_issue=$(jq -r '.issue // ""' "$marker" 2>/dev/null || echo "")
+  [ -n "${ISSUE_NUM:-}" ] && [ "$held_issue" = "$ISSUE_NUM" ] || return 1
+  head=$(jq -r '.head_sha // ""' "$marker" 2>/dev/null || echo "")
+  echo "blast-radius hold for issue #${ISSUE_NUM:-?} at ${head:-<unknown>} — waiting for operator approval (not a failure)"
+  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
+    --run-id "${RUN_ID:-unknown}" \
+    --issue "${ISSUE_NUM:-0}" \
+    --intent "${INTENT:-unknown}" \
+    --stage blast_hold \
+    --verdict human_required \
+    --detail "head_sha=${head}" \
+    --side-effect-level "${FACTORY_SIDE_EFFECT_LEVEL:-1}" \
+    --side-effect-profile "${FACTORY_SIDE_EFFECT_PROFILE_VERSION:-unknown}" || true
+  post_cost_report || true
+  return 0
+}
+
 # Maps the container's INTENT to the phase string the scheduler's retry keys and
 # trip_to_blocked() already use (_make_key in factory_core/breaker.py): "resolve" for
 # deconflict (not "deconflict" — matches the existing scheduler.sh call sites), "refine"
@@ -807,6 +840,9 @@ PYTHONPATH=/opt/dark-factory/scripts python3 -m factory_core.effective_config \
 # Archon gives every bash node its own $ARTIFACTS_DIR (the workflow run's directory, not this
 # one), so budget_gate.sh and architecture_slice.py find run-config.json through this export.
 export FACTORY_RUN_CONFIG_JSON="$ARTIFACTS_DIR/run-config.json"
+# Same reason (#551): blast_gate_check.sh --hold, running inside a bash node, writes the
+# hold marker here, and _handle_blast_hold reads it from here after the workflow exits.
+export FACTORY_BLAST_HOLD_FILE="$ARTIFACTS_DIR/blast-hold.json"
 
 # --- Apply config.yaml policy knobs post-clone (env/bootstrap values kept and logged) ---
 source /opt/dark-factory/scripts/run_config_env.sh
@@ -1032,6 +1068,10 @@ while true; do
       rm -f "$TMP_OUT"
       exit 0
     fi
+    if _handle_blast_hold; then
+      rm -f "$TMP_OUT"
+      exit 0
+    fi
     if python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
         --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
       # Kill-switch fallback (SESSION_WINDOW_BACKOFF_ENABLED=false): old sleep-forever
PATCH
~~~~

- [ ] **Step 4: Run it and the neighbouring entrypoint tests, and confirm they pass**

```bash
bash -n entrypoint.sh && echo syntax-ok
bash tests/test_entrypoint_blast_hold.sh 2>&1 | tail -1
bash tests/test_entrypoint_session_window.sh 2>&1 | tail -1
bash tests/test_entrypoint_error_signature.sh 2>&1 | tail -1
```

Expected: `syntax-ok`, then `Results: 20 passed, 0 failed`, then the two existing tests' unchanged all-pass summary lines.

- [ ] **Step 5: Commit**

```bash
git add entrypoint.sh tests/test_entrypoint_blast_hold.sh .github/workflows/ci.yml
git commit -q -m "feat(#551): a blast-radius hold exits 0 without an error signature

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: As-built docs (outside `.claude/`)

**Files:**
- Modify: `tests/test_docs_conformance_reference_docs.py`. It pins the `push-and-pr` row of `docs/run-lifecycle.md` verbatim, including `bash, 30 s`; Task 5 made that 180 s.
- Modify: `docs/run-lifecycle.md`:
  - DAG diagram;
  - `validate`, `budget-conformance`, `conformance` and `push-resolve` rows, plus a new `blast-gate` row;
  - the `push-and-pr` steps;
  - gate D1 (input, approval lookup, and an outcome table per enforcement point);
  - gate E1;
  - section F (the hold);
  - the artifact table (`blast.md`, `blast-hold.json`);
  - the state-dir list;
  - open items 3 and 4, marked "Resolved by #551" in place with the original findings kept.
- Modify: `docs/features.md`: the blast-radius row. Node ids after "Nodes" are checked against the DAG by `docs_refs_check.py`.
- Modify: `docs/factory-target-boundary.md`: the #374/#407 open items become one resolved item that keeps the hand-merge caveat.
- Modify: `docs/configuration.md`: a state-path row for `blast-approvals/`, and `BLAST_GATE_BAKED_DIR` among the test seams.

The approver key file is named in backticks only as `blast-approvers.asc` under `.factory/`. A backticked `.factory/blast-approvers.asc` makes `docs_refs_check.py` fail with "no such file", because that file is delivered by the operator PR. New text cites no line numbers. Pre-existing citation drift caused by the inserted workflow lines is not re-mapped: the check is range-only and still passes.

- [ ] **Step 1: Update the pinned row**

~~~~bash
git apply <<'PATCH'
diff --git a/tests/test_docs_conformance_reference_docs.py b/tests/test_docs_conformance_reference_docs.py
index 80a8bf2..c9b3e1c 100644
--- a/tests/test_docs_conformance_reference_docs.py
+++ b/tests/test_docs_conformance_reference_docs.py
@@ -20,7 +20,7 @@ def test_run_lifecycle_documents_nodes_gate_and_recovery():
                   "`docs-conformance`", "`docs-conformance-gate`", "**D2.5. Docs conformance, Gate 2.5**",
                   "scripts/docs_verdict_gate_check.sh", "`doc-impact.md`", "`docs-conformance.md`",
                   "df-docs-conformance-observed", "df-docs-conformance-blocked", "**Recovery:**",
-                  "| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` |"):
+                  "| `push-and-pr` | bash, 180 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` |"):
         assert token in text, token
 
 
PATCH
~~~~

- [ ] **Step 2: Run it and confirm it fails**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `1 failed, 3 passed in ...`. The doc still says `bash, 30 s`.

- [ ] **Step 3: Apply the doc changes**

~~~~bash
git apply <<'PATCH'
diff --git a/docs/configuration.md b/docs/configuration.md
index 5f63647..89db8dc 100644
--- a/docs/configuration.md
+++ b/docs/configuration.md
@@ -493,10 +493,12 @@ containers (`run-compose.yml:31-47`).
 |---|---|---|---|
 | `SCHEDULER_STATE_DIR` | `/var/lib/dark-factory` | `scheduler.sh:10`, `entrypoint.sh:36`, `breaker.py`, `epic_autopilot.py:653`, `main_red_fixer.py:279` | shared state volume; do not change independently of the volume mount |
 | `STATE_FILE` | `${SCHEDULER_STATE_DIR}/scheduler-state.json` | `scripts/factory_core/breaker.py:15`, `scripts/factory_core/cli.py:47` | breaker state |
+| (path) blast-radius approvals | `${SCHEDULER_STATE_DIR}/blast-approvals/` — `<issue>.json` + `<issue>.json.sig` (signed, written only by the operator's `approve-blast.sh`), `<issue>.request.json` (unsigned review request written on a hold) | `scripts/gate_blast_radius.py` (`--approvals-dir`), `scripts/blast_gate_check.sh` | `factory:factory`, dir `0755`, files `0644`; the public key is `blast-approvers.asc` under `.factory/` in the target repo, read only from origin `main` (#551) |
 | `CLONE_DIR` | `.` | `epic_autopilot.py:390,434` (`_effective_exclude_paths`, `_target_dir`), `main_red_fixer.py:278-292`, `adapter.py:306`, `diff_rank.py`, `budget_gate.sh:39` | set by `entrypoint.sh` in runs. The scheduler does not set it; epic autopilot then uses `/workspace/project` if that holds an adapter (`_SCHEDULER_TARGET_DIR`, [PR #524](https://github.com/omniscient/dark-factory/pull/524)) |
 | `ARTIFACTS_DIR` | per run | `entrypoint.sh:115`; `budget_gate.sh:31` (required) | |
 | `FACTORY_CORE_CLI`, `FACTORY_PROVIDERS_CLI` | baked paths | `scheduler.sh:19-20`, `entrypoint.sh:9` | override seams |
-| `IDENTITY_SH`, `FACTORY_CONFIG_PATH`, `FACTORY_SCRIPTS_DIR` | baked paths | `entrypoint.sh:5,61-62`; `gate_blast_radius.py:60,69` | "never set in the image"; test seams |
+| `FACTORY_BLAST_HOLD_FILE` | `$ARTIFACTS_DIR/blast-hold.json` (the entrypoint's own run directory) | `entrypoint.sh` (export, `_handle_blast_hold`), `scripts/blast_gate_check.sh` | hold marker handoff across Archon's per-node `ARTIFACTS_DIR` (#551); never set by hand |
+| `IDENTITY_SH`, `FACTORY_CONFIG_PATH`, `FACTORY_SCRIPTS_DIR`, `BLAST_GATE_BAKED_DIR` | baked paths | `entrypoint.sh:5,61-62`; `gate_blast_radius.py:60,69`; `scripts/blast_gate_check.sh` | "never set in the image"; test seams |
 | `SCHEDULER_SOURCE_ONLY`, `ENTRYPOINT_SOURCE_ONLY` | `0` | `scheduler.sh:1440`, `entrypoint.sh:89,753` | tests only |
 | `MODEL_PROXY_PORT` `MODEL_PROXY_UPSTREAM` `MODEL_PROXY_LEDGER_PATH` `MODEL_PROXY_CURRENT_RUN_PATH` `MODEL_PROXY_MAX_BYTES` `MODEL_PROXY_BACKUP_COUNT` `RAW_ARTIFACT_DIR` `RAW_ARTIFACT_RETENTION_DAYS` `CURRENT_RUN_DIR` `SEQ_URL` | `8787`, Anthropic API, `/var/lib/dark-factory/...`, 100 MiB, 3, ..., 7 days | `scripts/factory_core/model_proxy.py:26-41`; `run_record.py:27,483` | proxy and ledger; `run-compose.yml:79-85` sets the proxy's |
 | `FACTORY_EDITS_LABEL`, `FACTORY_REGRESSION_LABEL`, `FACTORY_EMAIL` | `merged-with-edits`, `factory-regression`, `factory@<repo>` | `scripts/fetch_scorecard.py:26-28` | scorecard tooling |
diff --git a/docs/factory-target-boundary.md b/docs/factory-target-boundary.md
index e270574..e74e7c3 100644
--- a/docs/factory-target-boundary.md
+++ b/docs/factory-target-boundary.md
@@ -270,11 +270,12 @@ writing:
   bare `4`, not `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL`, and no test
   pins the two together (`tests/test_side_effect.py` pins only `scripts/factory_core/verifier.py`'s
   private copy). Retuning the constant would silently leave adapter validation at 4.
-- **#374** — a `HUMAN_REQUIRED` block from `scripts/gate_blast_radius.py` has no approval
-  memory; re-running validate after an operator clears `needs-discussion` re-blocks
-  identically.
-- **#407** — nothing deterministic consumes `blast.md`; `scripts/verdict_gate_check.sh`
-  only guards `conformance.md`/`review.md`.
+- **#374, #407 — resolved by #551.** A `HUMAN_REQUIRED` block is cleared only by a signed,
+  content-bound approval record (`approve-blast.sh`, operator host), and
+  `scripts/blast_gate_check.sh` enforces it deterministically in the `blast-gate` node,
+  the `push-and-pr` re-check and the `close-preview` merge check. The approver key path
+  (`blast-approvers.asc` under `.factory/`) is on the hard-trigger floor. Still open: an operator
+  hand-merge (`gh pr merge`) is not checked.
 - **#412** — fail-closed findings from
   `scripts/gate_blast_radius.py::_adapter_snapshot` carry no diagnostic beyond
   "adapter.yaml unparseable at `<ref>`", and the base ref is validated against HEAD's
diff --git a/docs/features.md b/docs/features.md
index 7ed11b1..beb6d08 100644
--- a/docs/features.md
+++ b/docs/features.md
@@ -99,7 +99,7 @@ intent inline (`recheck`, `deconflict`, `fix-main`) or runs the workflow
 | Feature | Status and evidence | Entry points | Enabled by (default) | Documented in | Open limits |
 |---|---|---|---|---|---|
 | **Adapter validation.** A target whose `.factory/adapter.yaml` fails to parse or validate has its runs refused, with a ticket comment. The scheduler also warns once at startup. | **Live.** `_adapter_invalid_refuse` called at `entrypoint.sh:769`; startup warning at `scheduler.sh:1501`. Test: `tests/test_entrypoint_adapter_validate.sh` (CI). | `python -m factory_core.adapter --validate` | Always on | [README](../README.md), [`onboarding-new-target.md`](onboarding-new-target.md) | none |
-| **Blast-radius gate.** Before validation, the diff is checked against the hotspot report, the migration/seed/auth patterns and a size budget. A hit returns `HUMAN_REQUIRED` and blocks the run. | **Live.** Phase 0 of `commands/dark-factory-validate.md` (`:44`-`:95`) runs `gate_blast_radius.py`, preferring the baked copy (`:75`). | `scripts/gate_blast_radius.py` `main` (`:318`), `classify_file` (`:178`) | `blast_radius.enabled` (true), `.hotspot_score_floor` (5.0), `.size_budget_lines` (400), `.size_budget_blocks` (false, so size alone is advisory); adapter `safety.migration_seed_auth_patterns` | [`factory-target-boundary.md`](factory-target-boundary.md) | none |
+| **Blast-radius gate.** Before validation, the diff is checked against the hotspot report, the migration/seed/auth patterns and a size budget. A hit returns `HUMAN_REQUIRED` and holds the run until an operator records a signed, content-bound approval with `approve-blast.sh` (#551). | **Live.** `scripts/blast_gate_check.sh` runs the baked `gate_blast_radius.py` from validate Phase 0, Nodes `blast-gate`, `push-and-pr` and `close-preview`; a hold pushes the branch without a PR and is not counted as a failure. | `scripts/gate_blast_radius.py` `main`, `classify_file`, `evaluate_approval`; `scripts/blast_gate_check.sh` | `blast_radius.enabled` (true), `.hotspot_score_floor` (5.0), `.size_budget_lines` (400), `.size_budget_blocks` (false, so size alone is advisory); adapter `safety.migration_seed_auth_patterns` | [`factory-target-boundary.md`](factory-target-boundary.md) | none |
 | **Conformance gate.** A reviewer subagent (Opus) checks the diff against the approved spec. Material divergence starts a bounded reconcile loop; if it is still unresolved the run is blocked. A separate node fails closed when no verdict was written. | **Live.** Nodes `conformance` (`workflow:1121`) and `conformance-gate` (`:1131`, `verdict_gate_check.sh` at `:1134`). | `commands/dark-factory-conformance.md`; rubric `.claude/skills/conformance/RUBRIC.md`, falling back to `refinement-skills/conformance-reviewer-prompt.md` (`:270`-`:273`) | `conformance.enabled` (true), `.max_reconcile_cycles` (3), `.block_on_material` (true) | [`run-lifecycle.md`](run-lifecycle.md) | none |
 | **Docs conformance gate (Gate 2.5).** After Gate 2, a reviewer subagent (Opus) checks that the branch's docs are true of the final diff, that no affected behavior is undocumented, and that implement's `doc-impact.md` no-impact claims hold. Single pass, read-only by instruction. In `observe` mode a mismatch is recorded and posted as an informational comment; in `enforce` mode a mismatch, a missing or unparseable verdict, or `ERROR` blocks the PR. HEAD, the index and `git status` are compared with a baseline recorded before the review node: a commit or a staged change made during it stops the push in every mode (it would bypass Gate 2), while an untracked or unstaged change blocks in `enforce` and is only logged in `observe`. | **Measured only** (ships in `observe` mode). Nodes `docs-conformance` (`workflow:1212`) and `docs-conformance-gate` (`:1224`, `docs_verdict_gate_check.sh` at `:1262`). Tests: `tests/test_docs_verdict_gate_check.py`, `tests/test_docs_conformance_dag.py`, `tests/test_docs_conformance_command.py`. | `commands/dark-factory-docs-conformance.md`; rubric `refinement-skills/docs-conformance-reviewer-prompt.md` (a clone-live .claude/skills/docs-conformance/RUBRIC.md wins if a target adds one, `:174`-`:178`); `scripts/docs_verdict_gate_check.sh` | `docs_conformance.enabled` (true), `.mode` (`observe`) | [`run-lifecycle.md`](run-lifecycle.md) | [#536](https://github.com/omniscient/dark-factory/issues/536): enforce-mode flip, breaker accounting, tool restriction |
 | **Out-of-scope excision and spillover tickets.** Changes outside the plan are reverted from the branch and filed as backlog tickets labelled `scope-spillover`, deduplicated against existing ones. Refine and plan runs also strip files outside their allowed paths. | **Live.** Phase 3.6 of `commands/dark-factory-conformance.md` (`:332`-`:502`): `dedupe_oos.py` (`:422`), `cli.py board-add` (`:463`). `oos_excise.sh` is called from `dark-factory-refine.md:171` and `dark-factory-plan.md:308`. | `scripts/dedupe_oos.py`, `scripts/oos_excise.sh` | `conformance.scope_enforcement` (true), `.excise_out_of_scope` (true), `.backlog_label` (`scope-spillover`) | [CLAUDE.md](../CLAUDE.md) | none |
diff --git a/docs/run-lifecycle.md b/docs/run-lifecycle.md
index 6c1952f..5253ce6 100644
--- a/docs/run-lifecycle.md
+++ b/docs/run-lifecycle.md
@@ -263,8 +263,9 @@ flowchart TD
     IM --> RC[regen-codeindex]
     RC -->|continue| DC[de-conflict]
     RC --> PC[preview-changeset] --> CP[classify-preview] --> PU[preview-up]
-    PU --> VA["validate<br/>incl. blast-radius gate"]
-    VA --> CF["budget + conformance"] --> CG{conformance-gate}
+    PU --> VA["validate<br/>incl. blast-radius check"] --> BG{blast-gate}
+    BG -->|"PASS / SKIPPED"| CF["budget + conformance"] --> CG{conformance-gate}
+    BG -->|otherwise| HOLD(["hold: branch pushed, no PR"])
     CG -->|"PASS / SKIPPED / ERROR"| DF["budget + docs-conformance"] --> DG{docs-conformance-gate}
     CG -->|otherwise| STOP1(["stop: no push"])
     DG -->|"observe: any verdict<br/>enforce: PASS / SKIPPED<br/>never if HEAD or index moved"| PR[push-and-pr]
@@ -274,7 +275,7 @@ flowchart TD
     RG -->|otherwise| STOP2(["stop: board not moved"])
 
     FI -->|close| CA[close-announce]
-    FI -->|close| CL["close-preview<br/>merge PR, board to Done"] --> PM[post-merge-update-codeindex]
+    FI -->|close| CL["close-preview<br/>blast-radius check, merge PR<br/>pinned to that head, board to Done"] --> PM[post-merge-update-codeindex]
 ```
 
 The diagram leaves out the `resolve` branch and `bench-mode-probe`; both are in the
@@ -370,15 +371,16 @@ always exits 0 (`scripts/transfer_refine_artifacts.sh:26-93`).
 
 | Node | Kind | Runs when | Depends on | What it does | Reads → writes |
 |---|---|---|---|---|---|
-| `validate` | command `dark-factory-validate` (sonnet), idle 5 min, OR-join | `new`, `continue` or `resolve` | `preview-up`, `preview-up-resolve` | Blast-radius gate, then tests, type-check and endpoint checks; fixes failures and re-runs (`:1071-1079`) | `implementation.md`, `preview_env.sh`, hotspots file → `blast.md`, `memory-context.md`, `memory-trace.json`, `validation.md`, possibly fix commits |
+| `validate` | command `dark-factory-validate` (sonnet), idle 5 min, OR-join | `new`, `continue` or `resolve` | `preview-up`, `preview-up-resolve` | Blast-radius check (`scripts/blast_gate_check.sh`; stops on a non-zero exit and leaves the hold to `blast-gate`), then tests, type-check and endpoint checks; fixes failures and re-runs (`:1071-1079`) | `implementation.md`, `preview_env.sh`, hotspots file → `blast.md`, `memory-context.md`, `memory-trace.json`, `validation.md`, possibly fix commits |
 
 ### 4.7 Conformance
 
 | Node | Kind | Runs when | Depends on | What it does | Reads → writes |
 |---|---|---|---|---|---|
-| `budget-conformance` | bash, 30 s | `new` or `continue` | `validate` | Locates the spec, takes `git diff main...HEAD`, runs `context_budget.py --scenario conformance`; failure ignored (`:1082-1111`) | spec, `implementation.md`, diff → `context-budget.conformance.json` |
+| `blast-gate` | bash, 120 s | `new`, `continue` or `resolve` | `validate` | Re-runs the gate itself on HEAD with `scripts/blast_gate_check.sh --mode run --hold` (never reads the agent's `blast.md`). Non-zero exit: pushes the branch without a PR, writes the review request, adds `needs-discussion`, moves the board to **Blocked**, posts the `df-blast-hold` comment, writes `blast-hold.json` and blocks everything downstream ([gate D1](#d-implementation-gates)) | git, signed approval record → `blast.md`, `blast-hold.json`, `<issue>.request.json`, remote branch, comment, label |
+| `budget-conformance` | bash, 30 s | `new` or `continue` | `blast-gate` | Locates the spec, takes `git diff main...HEAD`, runs `context_budget.py --scenario conformance`; failure ignored (`:1082-1111`) | spec, `implementation.md`, diff → `context-budget.conformance.json` |
 | `enforce-budget-conformance` | bash, 30 s | `new` or `continue` | `budget-conformance` | `budget_gate.sh conformance`; failure ignored (`:1113-1118`) | → `token-opt-caps.env` |
-| `conformance` | command `dark-factory-conformance` (sonnet), idle 10 min | `new` or `continue` | `enforce-budget-conformance`, `validate` | Gate 2: reviews the diff against the spec, removes out-of-scope changes, reconciles material divergences (`:1121-1126`) | spec, `implementation.md`, `out-of-scope.md`, diff → `conformance.md`, `diff-ranking.json`, working files, possibly commits, tickets, comments, labels |
+| `conformance` | command `dark-factory-conformance` (sonnet), idle 10 min | `new` or `continue` | `enforce-budget-conformance`, `blast-gate` | Gate 2: reviews the diff against the spec, removes out-of-scope changes, reconciles material divergences (`:1121-1126`) | spec, `implementation.md`, `out-of-scope.md`, diff → `conformance.md`, `diff-ranking.json`, working files, possibly commits, tickets, comments, labels |
 | `conformance-gate` | bash, 30 s | `new` or `continue` | `conformance` | `scripts/verdict_gate_check.sh conformance.md <N> "Conformance (Gate 2)"`. Non-zero exit blocks everything downstream (`:1131-1140`) | `conformance.md` → exit code, possibly a marker comment |
 | `budget-docs-conformance` | bash, 30 s | `new` or `continue` | `conformance-gate` | Takes `git diff main...HEAD` after Gate 2, runs `context_budget.py --scenario docs-conformance`; failure ignored. Then records HEAD, the index tree and `git status --porcelain` as the read-only baseline for `docs-conformance-gate` (`:1173-1201`) | `doc-impact.md`, diff → `context-budget.docs-conformance.json`, `docs-conformance-pre-head.txt`, `docs-conformance-pre-index.txt`, `docs-conformance-pre-status.txt` |
 | `enforce-budget-docs-conformance` | bash, 30 s | `new` or `continue` | `budget-docs-conformance` | `budget_gate.sh docs-conformance`; failure ignored (`:1203-1208`) | → `token-opt-caps.env` |
@@ -389,20 +391,24 @@ always exits 0 (`scripts/transfer_refine_artifacts.sh:26-93`).
 
 | Node | Kind | Runs when | Depends on | What it does | Reads → writes |
 |---|---|---|---|---|---|
-| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` | Archives the spec and plan, pushes, creates or updates the draft PR (below) (`:1296-1453`) | `issue.json`, `preview_env.sh`, git → remote branch, PR |
-| `push-resolve` | bash, 30 s | `resolve` | `validate` | Pushes the current branch, verifies it is on origin, looks up the PR number (`:1274-1293`) | git → remote branch |
+| `push-and-pr` | bash, 180 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` | Archives the spec and plan, pushes, creates or updates the draft PR (below) (`:1296-1453`) | `issue.json`, `preview_env.sh`, git → remote branch, PR |
+| `push-resolve` | bash, 30 s | `resolve` | `blast-gate` | Pushes the current branch, verifies it is on origin, looks up the PR number (`:1274-1293`) | git → remote branch |
 
 `push-and-pr` in detail:
 
 1. `BENCH_MODE=stub`: print a marker and exit 0 without pushing (`:1313-1317`).
 2. Locate this branch's spec and plan with `push_gate_check.sh`; `git mv` each to
    `docs/archive/` and commit `docs: archive spec/plan for issue #N` (`:1324-1343`).
-3. `git push -u origin <branch>`; exit 1 if the push fails or the branch is not on
+3. Re-check the exact HEAD about to be pushed with `scripts/blast_gate_check.sh --mode run
+   --hold` (#551): validate's fixes, conformance's reconcile commits and the archive commit
+   all land after `blast-gate`. A hold takes the same steps as `blast-gate` and exits 1
+   before any push to a PR or PR creation.
+4. `git push -u origin <branch>`; exit 1 if the push fails or the branch is not on
    origin afterwards (`:1347-1356`).
-4. If `codeindex` is available, run `codeindex impact` on up to 50 changed files for a
+5. If `codeindex` is available, run `codeindex impact` on up to 50 changed files for a
    "Blast radius" section in the PR body (`:1359-1368`). This is advisory text, not the
    blast-radius gate.
-5. If a PR already exists for the branch: make sure its body carries the closing keyword
+6. If a PR already exists for the branch: make sure its body carries the closing keyword
    for the issue (`:1388-1408`). Otherwise open a **draft** PR titled with the issue
    title, with the closing keyword, a summary line, the preview section, the
    `Continue`/`Close` commands and the blast-radius section; exit 1 if no PR id comes
@@ -437,7 +443,7 @@ are none, and the `Continue`/`Close` commands (`:1562-1707`).
 | Node | Kind | Runs when | Depends on | What it does | Reads → writes |
 |---|---|---|---|---|---|
 | `close-announce` | bash, 15 s | `close` | `fetch-issue` | Posts "Merging PR and tearing down preview environment." through `tracker add-comment` (`:192-201`) | → issue comment |
-| `close-preview` | bash, 30 s | `close` | `parse-intent`, `fetch-issue` | Tears down the preview, merges the PR, moves the issue to Done (below) (`:203-268`) | `issue.json` → merged PR, board, issue comment |
+| `close-preview` | bash, 180 s | `close` | `parse-intent`, `fetch-issue` | Tears down the preview, merges the PR, moves the issue to Done (below) (`:203-268`) | `issue.json` → merged PR, board, issue comment |
 | `post-merge-update-codeindex` | bash, 120 s | `close` | `close-preview` | Checks out `main` at `origin/main`, regenerates `docs/codeindex-hotspots.md`, and if it changed commits `chore: refresh codeindex hotspots (post-merge #N)` and pushes to `main`; a failed push is a warning (`:270-302`) | repo → commit on `main` |
 
 `close-preview` in detail:
@@ -676,13 +682,17 @@ the push gate takes its "clean abort" branch:
 
 ### D. Implementation gates
 
-**D1. Blast-radius gate** (validate command Phase 0; `scripts/gate_blast_radius.py`).
+**D1. Blast-radius gate** (`scripts/blast_gate_check.sh` running `scripts/gate_blast_radius.py`;
+called by validate Phase 0, the `blast-gate` node, the `push-and-pr` re-check and
+`close-preview`).
 
-Input: the files changed in `git diff main...HEAD`, the added-plus-deleted line count,
-and `docs/codeindex-hotspots.md`. Config is read from the image's baked config layered
+Input: the files changed between the merge base with `refs/blast-gate/trusted-main` (origin
+`main`, fetched by the checker every time) and the head, the added-plus-deleted line
+count, and the hotspot report: the working-tree `docs/codeindex-hotspots.md` in a run,
+`main`'s committed copy at Close. Config is read from the image's baked config layered
 under the config at the merge base, never from the working tree, so a change cannot
-disable its own gate (`scripts/gate_blast_radius.py:63-103`). The baked script copy is
-preferred over the clone's (`commands/dark-factory-validate.md:66-76`).
+disable its own gate (`scripts/gate_blast_radius.py:63-103`). The baked script copies are
+preferred over the clone's.
 
 `STATUS: HUMAN_REQUIRED` when any of these holds (`scripts/gate_blast_radius.py:340-360`):
 
@@ -697,17 +707,27 @@ preferred over the clone's (`commands/dark-factory-validate.md:66-76`).
 - the line count exceeds `blast_radius.size_budget_lines` **and**
   `blast_radius.size_budget_blocks` is true.
 
-Otherwise `SKIPPED` when disabled, else `PASS`. The script always exits 0 and writes its
-verdict to stdout, which the command saves as `blast.md`.
-
-| Verdict | Outcome (`commands/dark-factory-validate.md:86-118`) |
+Otherwise `SKIPPED` when disabled, else `PASS`. A `HUMAN_REQUIRED` verdict is then looked
+up against the operator's signed approval record,
+`/var/lib/dark-factory/blast-approvals/<issue>.json` plus `.json.sig` (#551). The record
+is valid only with a detached gpg signature from a key in
+`main:.factory/blast-approvers.asc`, and it binds the `<mode> <blob>` of every triggered
+file at the merge base and at the head (both trees when the size trigger is active). A
+match turns the verdict into `PASS` with `APPROVAL: matched (...)`. Anything else keeps
+`HUMAN_REQUIRED` with `APPROVAL: none`, `error (signature)`, `error (record)` or
+`stale (...)`. The approval is written only by the operator's host-side
+`approve-blast.sh`, never by a comment or a label. The gate always exits 0; the checker
+exits 0 for `PASS`/`SKIPPED`, 1 for `HUMAN_REQUIRED` and 2 when it could not decide.
+
+| Where | Non-zero outcome |
 |---|---|
-| `PASS`, `SKIPPED` | Validation continues. `report` later prints a one-line blast-radius section |
-| `HUMAN_REQUIRED` | Post "Blast-Radius Gate — BLOCKED" with the trigger and up to 10 triggered files; add `needs-discussion`; move the board to **Blocked**; `exit 1` |
+| validate Phase 0 | Stops validating; posts nothing |
+| `blast-gate` node | Pushes the branch without a PR, writes `<issue>.request.json`, adds `needs-discussion`, moves the board to **Blocked**, posts the `df-blast-hold` comment (branch, head, trigger, files, `APPROVAL:` line, the `approve-blast.sh <N> --sha <head>` instruction), writes `blast-hold.json`, exits 1 |
+| `push-and-pr` re-check | The same, before any push to a PR or PR creation |
+| `close-preview` | No merge; request (mode `close`), `needs-discussion`, comment; exits 1 (gate E1) |
 
-No workflow node reads `blast.md` to enforce this; see
-[oddity 3](#open-questions-and-oddities). The `needs-discussion` label does make
-`close-preview` refuse to merge (gate E1).
+The entrypoint treats `blast-hold.json` as a hold, not a failure (section F). The
+conformance command does not adjudicate this gate.
 
 **D2. Conformance, Gate 2** (`commands/dark-factory-conformance.md`, then the
 `conformance-gate` node).
@@ -896,7 +916,11 @@ conformance and code review have run; nothing re-runs those on the revised code.
 
 **E1. Merge refusal** (`close-preview`, `workflows/archon-dark-factory.yaml:236-255`).
 `Close issue #N` exits 1 without merging if the issue has `needs-discussion`, if no PR
-exists, or if the merge call does not report success. This is the one place where a gate
+exists, if the blast-radius check on the PR head does not pass (gate D1; a
+`HUMAN_REQUIRED` diff needs a valid signed approval, and a checker failure also refuses),
+or if the merge call does not report success. The merge is pinned to the head that was
+checked (`codehost merge --match-head`), and a successful merge deletes the issue's
+approval record. This is the one place where a gate
 label set earlier (blast radius, conformance, code review, breaker) stops a merge.
 The failure surfaces as a failed workflow ([section 9](#9-how-a-run-ends)).
 
@@ -909,6 +933,15 @@ writes the resume time to `session-window-paused` in the state dir, drops an
 entry to `runs.jsonl`, and exits 0. Tuned by `scheduler.session_window_backoff_enabled`,
 `scheduler.session_window_buffer_minutes`, `scheduler.session_window_fallback_minutes`.
 
+Next it checks for the hold marker (#551, `_handle_blast_hold`): `blast-hold.json` in the
+entrypoint's own artifacts directory, exported as `FACTORY_BLAST_HOLD_FILE` before the
+workflow starts because Archon gives bash nodes a different `ARTIFACTS_DIR`. A
+blast-radius hold writes it before any of its other steps. A marker that names a
+different issue than the run's is ignored. On a match it appends a `blast_hold` / `human_required` entry to
+`runs.jsonl`, posts the cost report and exits 0: no post-mortem, no error signature, and
+no change to any retry counter. While `needs-discussion` is on the issue the scheduler
+skips it; after approval the next dispatch counts as one ordinary attempt.
+
 With the backoff disabled, a rate-limit match makes the entrypoint sleep until the
 parsed reset time plus 60 s (300 s when the time cannot be parsed or the result is
 negative or above 90000 s) and re-run the whole workflow
@@ -975,7 +1008,8 @@ pushed, posted, or written to `/var/lib/dark-factory`.
 | `out-of-scope.md` | implement command; `oos_excise.sh` | conformance command |
 | `failure-diagnosis.md` | implement command (blocked exit) | Posted as an issue comment; failure classification |
 | `preview_env.sh`, `preview_build.log` | `preview-up`, `preview-up-resolve` | validate, `push-and-pr`, `report` |
-| `blast.md` | validate command (from `gate_blast_radius.py`) | validate command; `report` |
+| `blast.md` | `blast_gate_check.sh` (validate Phase 0, then overwritten by `blast-gate`, `push-and-pr`, `close-preview`) | `report` |
+| `blast-hold.json` (entrypoint's directory, via `FACTORY_BLAST_HOLD_FILE`) | `blast_gate_check.sh --hold` (`blast-gate`, `push-and-pr`) | `entrypoint.sh` `_handle_blast_hold` |
 | `validation.md` | validate command | `run-record assemble` |
 | `conformance_artifact_content.md`, `conformance_prompt.md`, `no_spec_issue_body.md`, `conformance_reconcile_diff.txt` | conformance command | Reviewer subagent prompt |
 | `diff-ranking.json` | `diff_rank.py` (conformance, then docs conformance, then code review) | `context_budget.py`; humans debugging a zero-content block |
@@ -994,7 +1028,9 @@ pushed, posted, or written to `/var/lib/dark-factory`.
 State written outside the artifacts directory, under `/var/lib/dark-factory`:
 `runs.jsonl`, `current-run.json`, `main-is-red`, `main-red-last-recheck`,
 `main-is-red-issue`, `session-window-paused`, `error-signatures/<issue>.<phase>.sig`,
-`hook-warnings.log`.
+`hook-warnings.log`, `blast-approvals/<issue>.request.json` (written on a blast-radius
+hold; the signed `<issue>.json` and `.json.sig` beside it are written only by the
+operator's `approve-blast.sh` and deleted by `close-preview` after the merge).
 
 ---
 
@@ -1146,7 +1182,9 @@ The scheduler-side oddities are tracked in
    (`scripts/factory_core/deconflict.py:311-320`), but the only node depending on
    `de-conflict` is `preview-up-resolve`.
 
-3. **The blast-radius block has no enforcing DAG node.** The validate command writes
+3. **Resolved by #551.** The `blast-gate` node, the `push-and-pr` re-check and the
+   `close-preview` check now enforce the gate deterministically (gate D1). The original
+   finding: **the blast-radius block has no enforcing DAG node.** The validate command writes
    `blast.md` and does `exit 1` on `HUMAN_REQUIRED`
    (`commands/dark-factory-validate.md:91-118`). The conformance and code-review commands
    both state that a command node's internal `exit 1` "does not reliably surface as node
@@ -1157,7 +1195,8 @@ The scheduler-side oddities are tracked in
    would proceed and move a Blocked issue to In Review. The `needs-discussion` label
    would still stop `close-preview` from merging.
 
-4. **The blast-radius comment names a command that does not exist.** It tells the human
+4. **Resolved by #551**: the `df-blast-hold` comment names `approve-blast.sh` instead. The
+   original finding: **the blast-radius comment names a command that does not exist.** It tells the human
    to re-run `"Validate issue #N"` (`commands/dark-factory-validate.md:104-107`). The
    entrypoint's intent pattern has no `validate` (`entrypoint.sh:103`), so that string
    falls through to `fix`, and what the workflow's `parse-intent` would make of it is a
PATCH
~~~~

- [ ] **Step 4: Run the pin test and the docs reference check, and confirm they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py -q -p no:cacheprovider 2>&1 | tail -1
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: `4 passed in ...`, then `Docs reference check passed for 4 doc(s).`

- [ ] **Step 5: Commit**

```bash
git add docs/run-lifecycle.md docs/features.md docs/factory-target-boundary.md docs/configuration.md tests/test_docs_conformance_reference_docs.py
git commit -q -m "docs(#551): blast-gate, signed approvals and the hold path, as built

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Full CI-parity verification and scope check

**Files:** none (verification only)

- [ ] **Step 1: Full pytest suite**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: `... passed` with no failures. On the plan-time replay at `5724b75` this was `3068 passed`.

- [ ] **Step 2: Every shell test CI lists, both DAG checks, the docs check**

```bash
for t in $(grep -oE "bash tests/test_[a-z0-9_]+\.sh" .github/workflows/ci.yml | awk '{print $2}'); do
  timeout 300 bash "$t" >/dev/null 2>&1 || echo "FAIL $t"
done; echo shell-tests-done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: `shell-tests-done` with no `FAIL` lines, followed by the three pass lines. In the factory container only, `FAIL tests/test_identity.sh` and `FAIL tests/test_provider_env_flags.sh` also appear. Both fail identically on unmodified `main` because the container exports `JIRA_*` and similar variables, and both pass on the CI runner. Confirm this with `git stash -u && bash tests/test_identity.sh; git stash pop` if in doubt. No other failure is acceptable.

- [ ] **Step 3: Scope check (two-dot, per memory `[PATTERN]` #250)**

```bash
git diff --name-only origin/main HEAD | grep -v '^docs/superpowers/' | sort
git diff --name-only origin/main HEAD | grep -E '^(scheduler\.sh|scripts/verdict_gate_check\.sh|scripts/check_workflow_dag\.py|deploy/|\.github/workflows/publish\.yml|\.claude/|\.factory/|commands/dark-factory-(code-review|docs-conformance)\.md)' || echo "fence-ok"
git grep -n "2026-11-21-blast-radius-durable-approval" -- ':!docs/superpowers/' || echo "no-spec-path-citations"
```

Expected: exactly these 27 paths, then `fence-ok`, then `no-spec-path-citations`:

```text
.github/workflows/ci.yml
commands/dark-factory-conformance.md
commands/dark-factory-validate.md
docs/configuration.md
docs/factory-target-boundary.md
docs/features.md
docs/run-lifecycle.md
entrypoint.sh
scripts/blast_gate_check.sh
scripts/factory_core/adapter_defaults.py
scripts/factory_core/providers/cli.py
scripts/factory_core/providers/codehost/base.py
scripts/factory_core/providers/codehost/github.py
scripts/factory_core/providers/codehost/gitlab.py
scripts/gate_blast_radius.py
tests/test_adapter.py
tests/test_blast_gate_check.sh
tests/test_blast_gate_dag.py
tests/test_blast_radius.py
tests/test_conformance_blast_adjudication.py
tests/test_docs_conformance_dag.py
tests/test_docs_conformance_reference_docs.py
tests/test_entrypoint_blast_hold.sh
tests/test_provider_cli.py
tests/test_provider_codehost_parity.py
tests/test_validate_blast_gate_baseref.py
workflows/archon-dark-factory.yaml
```

- [ ] **Step 4: Record the residuals in the PR body.** Copy "Known residuals" from the top of this plan into the PR description, and add: "Operator follow-up PR required: `approve-blast.sh`, key runbook, `PLAYBOOK.md`/`GOTCHAS.md`, `.factory/blast-approvers.asc` (spec 'Architecture — operator scope'). Until it merges and the image is republished, every `HUMAN_REQUIRED` diff stays held." There is nothing to commit in this task.

# Run record: resolve gate-stage artifacts from Archon's per-workflow-run directory

**Issue:** #573
Revised: 2026-11-22 (operator amendment after spec gate)

---

## Overview / Problem Statement

`scripts/factory_core/run_record.py::cmd_assemble()` (lines 690-848 on current `main`)
is invoked from two places: the success path (`entrypoint.sh:1090-1102`) and the failure
trap (`entrypoint.sh:559-572`, `--status failed`), each with its own
`archon workflow cost --last` capture. Its `artifact_names` loop (lines 696-705):

```python
artifact_names = ["validation", "conformance", "review", "conflict_resolution"]
for name in artifact_names:
    md_path = artifacts_dir / f"{name}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        artifacts[name] = content
        stage = _parse_artifact_stage(name, content)
        if stage:
            stages.append(stage)
```

reads every name from `artifacts_dir = pathlib.Path(args.artifacts_dir)` — the one and
only directory `--artifacts-dir` points at, which is `entrypoint.sh`'s own
`$ARTIFACTS_DIR` (`entrypoint.sh:114-116`:
`~/.archon/workspaces/<owner>/<repo>/artifacts/runs/<RUN_ID>`, exported once near the
top of the script, before `archon workflow run` is invoked).

`validation.md`, `conformance.md` and `review.md` — exactly `GATE_STAGE_NAMES`
(`run_record.py:31`), the three stage names `_compute_outcome` (`run_record.py:429`)
consults to decide `blocked` vs. `delivered_clean`/`delivered_with_findings` vs.
`produced_ungated` — are never written into that directory in production. They are
written by Archon DAG nodes (the `validate`, `conformance` and `code-review` command
nodes, whose prompts have `$ARTIFACTS_DIR` text-substituted by Archon with the workflow
run's directory) into **Archon's own per-node
`$ARTIFACTS_DIR`**, which is `runs/<workflow run id>` — a different, sibling directory
of the entrypoint's `runs/<RUN_ID>` (`entrypoint.sh:807-809`'s own comment: "Archon
gives every bash node its own `$ARTIFACTS_DIR` (the workflow run's directory, not this
one)"). Every *other* reader of `conformance.md`/`review.md` in
`workflows/archon-dark-factory.yaml` (the `conformance-gate`, `report`,
`docs-conformance-gate` and `review-gate` nodes) reads them correctly, because those
readers are themselves DAG nodes sharing that same per-node directory. `cmd_assemble`
is the sole consumer that disagrees — it runs from `entrypoint.sh`'s own, non-DAG-node
shell context, after the workflow has already exited, so it only ever sees the
entrypoint's directory. The result: **the `md_path.exists()` check at line 700 is
always false for these three files in production**, `stages` stays empty for every
run, and `_compute_outcome`'s `elif not gate_stages:` branch (line 455) always wins for
a completed run — every completed `implement`-shaped run (`fix`/`continue`) is scored
`produced_ungated`/`1.0`, identically to a run that never ran any gate at all. Runs
whose `conformance-gate`/`review-gate` node exits 1 fail the workflow and are already
scored `failed`/0.0 through the failure trap; the fix does not change that state, it
only populates their `stages`. What changes is that completed runs become
`delivered_clean`/`delivered_with_findings`, and a completed run with validation `FAIL`
becomes `blocked`. Per the issue's own evidence (40 most
recent self-instance run records): all 8 completed `fix`/`continue` records score
`produced_ungated`/`1.0`, and 0 of 40 carry `push_result` (the `push_result` fold reads
the same wrong directory — R2b).

**This is the same bug class #447 already fixed, but only for one artifact**
(`push-result.json`, `refine`/`plan` runs only — `run_record.py:739-765`, added
`_archon_workflow_run_id()` and `_push_result_is_fresh()`, see
`docs/archive/2026-11-22-run-record-artifact-scoring-design.md` R3). #447's own spec
flagged the correction at the time ("the DAG nodes write `push-result.json` to
archon's per-workflow-run directory... a sibling of the `--artifacts-dir` that
`cmd_assemble` receives") but scoped the fix to `push-result.json`'s existence check
alone, leaving the `push_result` fold for this ticket (R2b). This ticket also
generalizes the same resolution technique to the three `GATE_STAGE_NAMES` files, which
is what actually drives the `blocked`/`delivered_*` scoring that matters for the bulk
of the factory's runs (every `implement` run), not just the artifact-existence check
for `refine`/`plan`.

### Related: scope boundaries (what this ticket does and does not touch)

- **`conflict_resolution.md`** stays on today's single-directory lookup
  (`artifacts_dir / "conflict_resolution.md"`, unchanged). It has two writers:
  `entrypoint.sh:992` (deconflict intent, entrypoint's own directory — found today) and
  the `de-conflict` DAG node via `deconflict.py:338` (`continue`/`resolve`, Archon's
  directory — not found today). The second is the same defect, but the file is not a
  gate stage and the issue does not name it; it is left for a follow-up ticket. It is
  not a `GATE_STAGE_NAMES`
  member (`run_record.py:31`: only `("validation", "conformance", "review")`), so it
  never feeds `_compute_outcome`'s scoring regardless.
- **`push-result.json`'s `refine`/`plan`-only intent gate** (`run_record.py:747`) is not
  widened: only `refine-push` (`workflows/archon-dark-factory.yaml:463`) and
  `plan-push-and-advance` (`:594`) write the file; the implement path's `push-and-pr`
  node (`:1296-1455`) never writes one, so widening the gate to `fix`/`continue` would
  be inert. The separate `push_result` **fold** (`run_record.py:808-813`) is in scope —
  see R2b. It reads `artifacts_dir / "push-result.json"`, the entrypoint's directory,
  so no production record carries `push_result` and `cost_report.py`'s
  `**Push:** FAILED` line never fires. #447 deferred exactly this to this ticket
  (`docs/archive/2026-11-22-run-record-artifact-scoring.md:1120`).
- **`scripts/factory_core/post_mortem.py`**'s own, structurally similar
  `_ARTIFACT_FILES` lookup (`implementation.md`/`conformance.md`/`review.md`/
  `plan.md`) is a different mechanism: `entrypoint.sh::run_post_mortem()`
  (`entrypoint.sh:220-234`) passes it the `runs/` **base** directory, not a specific
  run's directory, and `post-mortem-gather`'s `_find_run_dir()` searches it for the
  newest run directory whose `issue.json` matches the issue number — not a copy of
  this bug. It is also never named in the issue. Out of scope; a follow-up ticket if
  it turns out to need the same fix.
- **`POLICY_VERSION`** (`run_record.py:30`, currently `"1.1"`) is not bumped. #447
  bumped it because it changed `_compute_outcome`'s own state-machine logic (added
  `clean_abort`). This ticket changes only *which files feed* the existing, unchanged
  `_compute_outcome`/`_parse_artifact_stage` logic — the policy itself is identical
  before and after.

---

## Requirements

### R1 — Resolve `workflow_run_id` once, reuse for every artifact

Hoist the `workflow_run_id = _archon_workflow_run_id(archon_path)` call
(`run_record.py:670-687`, already intent-agnostic — it only depends on
`args.archon_cost_json`, which is captured unconditionally for every intent at
`entrypoint.sh:1080-1102` and, on the failure path, at `entrypoint.sh:551-572`) to
before the `artifact_names` loop, computed exactly once per `cmd_assemble` call. The
`archon_path = ...` assignment it depends on (`run_record.py:707`, currently after the
loop) moves above the loop with it. Remove the second, duplicate computation currently inside the
`if args.intent in ("refine", "plan")` block (`run_record.py:749`) and have it reuse
the same value.

### R2 — Apply #447's "default-to-own-dir, override-with-fresh-sibling" pattern to the three `GATE_STAGE_NAMES` files

For each of `"validation"`, `"conformance"`, `"review"` (not `"conflict_resolution"` —
see "Related" above), resolve the path to read as:

1. Default: `artifacts_dir / f"{name}.md"` (today's lookup, unchanged as the fallback).
2. If `workflow_run_id is not None`: compute `sibling = artifacts_dir.parent /
   workflow_run_id / f"{name}.md"`. If `sibling.exists()` and it passes the freshness
   guard (R3), **use `sibling` instead of the default** — mirroring #447's
   `push-result.json` precedence exactly (a fresh sibling wins over the entrypoint's
   own directory, never the reverse).
3. Read/parse from whichever path won, exactly as today (`md_path.exists()` →
   `read_text()` → `_parse_artifact_stage(name, content)` → append to `stages`).

This applies for **every intent**, not gated to `refine`/`plan` — the bug affects
`fix`/`continue` runs (the ones that actually produce `validation.md`/`conformance.md`/
`review.md`), which is the issue's whole motivating evidence. There is no existing
intent gate on this loop today; none is added.

In production, nothing writes these three files into the entrypoint's own
`$ARTIFACTS_DIR` — only the DAG commands write them, to their own per-node
`$ARTIFACTS_DIR` (`dark-factory-validate.md:243`, `dark-factory-conformance.md`'s
`conformance.md` write around `:542-558`, `dark-factory-code-review.md`'s `review.md`
write around `:236/275/363`). So step 1's own-dir default exists for symmetry with
#447's pattern, for the no-cost-JSON-capture case (`workflow_run_id is None`), and to
keep today's direct-same-directory tests passing unchanged — not because it is
expected to win in production.

### R2b — `push_result` fold uses the resolved path

For `refine`/`plan`, the fold at `run_record.py:808-813` reads the same
`artifact_push_path` the #447 block already resolved (own-dir default, fresh sibling
override) instead of recomputing `artifacts_dir / "push-result.json"`. Other intents
keep the own-dir path. Malformed JSON still yields no `push_result` key. Tests: flip
`test_assemble_refine_finds_push_result_in_workflow_run_dir`
(`tests/test_run_record.py:1710-1712`) to
`assert rec["push_result"] == {"node": "refine-push", "pushed": True}`; add a fresh
sibling `{"pushed": false, "error": "x"}` case asserting the key is folded; a stale
sibling yields no key; `test_assemble_non_refine_plan_ignores_workflow_run_dir`
(`:1918`) stays unmodified.

### R3 — Freshness guard, generalized

Rename `_push_result_is_fresh(push_path, started_at)` (`run_record.py:652-667`) to a
name that reflects its now-general use, e.g. `_artifact_is_fresh`, since its body
already has no `push-result.json`-specific logic (it only compares `push_path`'s mtime
against `started_at`). Keep its exact semantics: unparseable `started_at` or an
unreadable mtime fails closed (not fresh) — `archon workflow cost --last` can hand back
an *earlier* workflow run's id when the current run was never recorded there yet, and
without this guard a stale sibling directory's leftover `review.md` (say,
`STATUS: BLOCKED` from a previous run) could be mistaken for the current run's artifact and
wrongly score the run `blocked` — worse than today's `produced_ungated`, not better.
Update the one comment referencing the old name by its old name
(`tests/test_run_record.py:1733`) to match.

### R4 — One scoring-neutral lookup-evidence field

Record one top-level, scoring-neutral `gate_artifact_lookup` object on every assemble:
`{"workflow_run_id": <id or null>, "workflow_dir_exists": <bool>, "found": [names read
from the sibling], "stale": [names present in the sibling but failing the freshness
guard], "own_dir": [names read from --artifacts-dir]}`. It is not passed to
`_compute_outcome` and does not appear in `outcome.evidence`. When
`status == "completed"`, intent is `fix` or `continue`, and no gate stage was parsed,
also print one
`WARNING: run-record: no gate-stage artifacts found (workflow_run_id=…, workflow_dir_exists=…)`
line to stderr.

Why: this ticket fixes a silent failure, and without the field the fix's own failure
modes stay silent. No usable cost JSON, an id that resolves to a `runs/<id>/` holding
none of the files, and files that fail the freshness guard all still produce a
completed `fix`/`continue` run with `gate_stages: []` and `produced_ungated`/`1.0` —
the exact signature of the bug. The field covers the three `GATE_STAGE_NAMES` only
(`push-result.json` keeps #447's `artifact_check`), and it is also what separates
post-fix records from the historical blind-scored ones, since `POLICY_VERSION` is not
bumped. Also add a code comment next to the
loop explaining the two-directory resolution and citing #573, matching the existing
comment style at `run_record.py:739-745` (citing #447).

### R5 — Test coverage

Extend `tests/test_run_record.py`, reusing the existing production-accurate fixture
helpers rather than inventing new ones. All cases are pytest in
`tests/test_run_record.py`, run by CI's `python -m pytest tests/ -v`
(`.github/workflows/ci.yml:13`) and runnable on the Windows host; no `.sh` test and no
`ci.yml` edit is needed.

- `_split_artifact_dirs(tmp_path, monkeypatch, *, intent)` (`:1670`) already builds the
  entrypoint-dir/workflow-dir sibling layout plus a cost JSON declaring
  `"run_id": "wf-run"`, for any intent — use it for `"fix"`/`"continue"`
  (`entrypoint.sh:103-104`: `fix` is the default and `continue` is matched; the
  workflow's own `new` never reaches `--intent`) in addition to the existing
  `"refine"`/`"plan"` uses.
- Generalize `_write_workflow_push_result(workflow_dir, *, mtime)` (`:1737`) into a
  name-and-content-parameterized helper (e.g. `_write_workflow_artifact(dir, name,
  content, *, mtime)`), reusing `_ASSEMBLE_STARTED_EPOCH` (`:1734`) for freshness-math
  tests.
- `_forbid_lookup(monkeypatch)` (`:1695`) must still be used in every new test for a
  non-`refine`/`plan` intent, since the gate-stage directory fix must never trigger the
  `needs-discussion` live lookup (that lookup stays `refine`/`plan`-only, per #447 R4 —
  untouched by this ticket).
- New cases to add:
  - Fresh `validation.md`/`conformance.md`/`review.md` present only in the sibling
    workflow directory → all three are parsed into `stages`, and
    `harness_economics.outcome.state` is `delivered_clean` (or
    `delivered_with_findings` when `conformance.md`'s `CYCLES` or `review.md`'s
    `ADVISORY` are nonzero), not `produced_ungated`; `gate_artifact_lookup` records
    `workflow_run_id: "wf-run"`, `workflow_dir_exists: true` and all three names under
    `found`. This is the direct regression test for the bug.
  - Parametrized: sibling `review.md` = `STATUS: BLOCKED\nBLOCKERS: 1\nADVISORY: 0`;
    sibling `conformance.md` = `STATUS: BLOCKED`; sibling `validation.md` =
    `STATUS: FAIL` — each scores `blocked`/0.0. A `review.md` of
    `STATUS: PASS\nBLOCKERS: 1` does not (the verdict, not the count, blocks:
    `_BLOCKING_VERDICTS`, `run_record.py:32-36`).
  - A **stale** sibling (`mtime < started_at`) is ignored; with no file in the
    entrypoint's own directory either, the run stays `produced_ungated` (falls through
    to zero gate stages) — pins R3's freshness guard for the generalized case, not
    just `push-result.json`. The name is listed under `gate_artifact_lookup.stale`,
    and for a `fix`/`continue` intent the R4 warning is emitted.
  - `workflow_run_id` resolves but the sibling directory is absent → the run stays
    `produced_ungated`, `gate_artifact_lookup.workflow_dir_exists` is false, and the R4
    warning is emitted (R4's silent-path pin).
  - An unparseable `started_at` ignores the sibling (mirrors the existing
    `push-result.json` parametrized case around `:1781`).
  - `conflict_resolution.md` placed only in the workflow sibling directory is **not**
    picked up (known gap, follow-up ticket; pinned only so the change is deliberate
    when it comes); one placed in the entrypoint's
    own directory still is (confirms "Related" is unchanged).
  - A file present in *both* directories, fresh: the sibling wins (matches #447's
    existing override precedence, pinned here for the generalized case too).
  - `--status failed` with fresh sibling gate files → `stages` populated, state
    `failed`, score 0.0 (the failure-trap call site, `entrypoint.sh:559-572`).
  - A realistic free-form `validation.md` (no `STATUS:` line; contains both a
    per-check `PASS` and `Final status: FAIL`) in the sibling directory: pins today's
    parser result (`PASS`, via the substring heuristic at `run_record.py:361-363`) so
    the follow-up that fixes the heuristic changes it deliberately (see Assumptions).
  - The R2b `push_result` fold cases: the flipped
    `test_assemble_refine_finds_push_result_in_workflow_run_dir`, a fresh sibling
    `{"pushed": false, "error": "x"}` that is folded, and a stale sibling that yields
    no key.
- **Existing tests that must keep passing unmodified** (regression pins; if any of
  them needs editing, that signals this change went out of scope — the one deliberate
  exception is `test_assemble_refine_finds_push_result_in_workflow_run_dir`, flipped
  by R2b):
  - `test_assemble_non_refine_plan_intents_unchanged` (`:1901`, parametrized over
    `"close"`, `"new"`, `"deconflict"`, `"fix-main"`) — these fixtures write no
    artifact files at all, so the directory-resolution change has no effect; `outcome`
    stays `produced_ungated`/`1.0` with `gate_stages: []`.
  - `test_assemble_non_refine_plan_ignores_workflow_run_dir` (`:1918`) — pins that
    `push-result.json`'s own intent gate is untouched; unaffected since R2b changes
    the fold for `refine`/`plan` only and R1's hoist only moves *where*
    `workflow_run_id` is computed, not what it's used for there.
  - Every direct, same-directory `validation.md`/`conformance.md`/`review.md` test
    already in the file (e.g. around `test_assemble_builds_run_record`, `:633-640`)
    keeps passing: with no cost JSON (or no `archon_cost_json` arg) `workflow_run_id`
    resolves to `None` and step 2 of R2 never fires, so the own-dir default is used
    exactly as before.

### R6 — Docs

`docs/run-lifecycle.md` currently describes this incorrectly in three places and must be
corrected (`scripts/docs_refs_check.py` convention — keep prose accurate to behavior,
not just to line numbers):

- §7 step 2 (around line 929): "`factory_core/cli.py run-record assemble` builds
  `run-record.json`... it parses `validation.md`, `conformance.md`, `review.md` and
  `conflict_resolution.md` into stage verdicts" needs a clause noting that the first
  three are read from Archon's per-workflow-run directory (falling back to the
  entrypoint's own `ARTIFACTS_DIR`), mirroring how line 971's `run-config.json` row
  already documents the same two-directory fact for `budget_gate.sh`/
  `architecture_slice.py`. The same step also gains the `gate_artifact_lookup` field
  (R4) and the note that, for `refine`/`plan`, `push_result` is folded from the
  resolved path (R2b).
  - §8's artifacts table (around lines 977-994): the `validation.md`, `conformance.md`
  and `review.md` rows' "Read by" column lists `run-record assemble` as if it reads
  them from this directory; either annotate those three rows or add a short note
  under the table pointing at §7's corrected text, so a future reader doesn't
  re-derive this bug by hand the way #573 itself had to. The `push-result.json` row
  (around line 977) gets the same treatment for R2b.
- Re-map the `run_record.py` line citations that this change shifts:
  `docs/run-lifecycle.md:929` (`run_record.py:690-848`),
  `docs/configuration.md:555` (`run_record.py:735`), and `docs/features.md:139`
  (`cmd_assemble` `:690`, `run_record.py:1001`, `:1006`). `docs_refs_check.py` is range-only
  and will not catch them. Correct §7's "Only when `archon workflow run` exits 0" to
  say that assemble also runs on the failure path.

Exact line numbers will have shifted by the time the plan phase re-derives them against
whatever `main` commit it branches from (per the #182 lesson already applied
throughout this spec); re-verify before editing, don't trust the citations above
verbatim.

---

## Architecture / Approach

**Chosen approach: extend `cmd_assemble`'s existing `artifact_names` loop in place,
reusing #447's already-generic helpers.** No new resolution mechanism, no new file
(one new scoring-neutral record field, R4), no workflow
YAML or `entrypoint.sh` change — `archon_cost_json`/`archon_cost_exit_code`/
`archon_cost_stderr_file` are already passed to `cmd_assemble` at both call sites
for every intent (`entrypoint.sh:559-572`, `:1090-1102`).

```
cmd_assemble (entrypoint.sh calls this from the success path and from the failure trap):
  artifacts_dir = Path(args.artifacts_dir)            # entrypoint's own runs/<RUN_ID>
  archon_path = Path(args.archon_cost_json) if args.archon_cost_json else None   # moved above the loop (R1)
  workflow_run_id = _archon_workflow_run_id(archon_path)   # hoisted (R1); None if cost JSON missing/unparseable

  for name in ("validation", "conformance", "review"):    # GATE_STAGE_NAMES, not conflict_resolution
      md_path = artifacts_dir / f"{name}.md"               # default (R2 step 1)
      if workflow_run_id is not None:
          sibling = artifacts_dir.parent / workflow_run_id / f"{name}.md"
          if sibling.exists() and _artifact_is_fresh(sibling, args.started_at):  # R3
              md_path = sibling                            # fresh sibling wins (R2 step 2)
          # R4: note the name under found / stale / own_dir for gate_artifact_lookup
      if md_path.exists():
          content = md_path.read_text(encoding="utf-8")
          artifacts[name] = content
          stage = _parse_artifact_stage(name, content)
          if stage:
              stages.append(stage)

  # conflict_resolution.md: unchanged, single-directory lookup (own dir only)
  md_path = artifacts_dir / "conflict_resolution.md"
  ...

  # push-result.json block (existing #447 logic): reuses the same workflow_run_id
  # computed above instead of recomputing it
  if args.intent in ("refine", "plan"):
      ...  # unchanged logic, just no second _archon_workflow_run_id() call

  # R4: top-level, scoring-neutral; never passed to _compute_outcome
  run_record["gate_artifact_lookup"] = {workflow_run_id, workflow_dir_exists, found, stale, own_dir}
  # + stderr WARNING when status == completed, intent in (fix, continue), no gate stage

  # push_result fold (R2b): refine/plan read artifact_push_path (resolved above);
  # every other intent keeps artifacts_dir / "push-result.json"
```

`_compute_outcome`/`_compute_harness_economics` are untouched — they already consume
whatever ends up in `stages`; the fix is entirely upstream of them, in *which file* each
stage's content comes from.

---

## Alternatives Considered

- **Always prefer the sibling directory unconditionally, skip the own-dir check.**
  Rejected: breaks existing same-directory tests that write artifacts directly into
  `artifacts_dir` with no cost JSON/`workflow_run_id`, and diverges from #447's
  established precedent for no benefit — nothing in production ever writes these three
  files into the entrypoint's own directory, so the own-dir branch is a no-op fallback,
  not a competing source of truth that needs to be bypassed.
- **Bundle `conflict_resolution.md` into the same sibling-fallback logic "for
  consistency."** Rejected for this ticket: its `continue`/`resolve` writer
  (`deconflict.py:338`, via the `de-conflict` node) does land in Archon's directory, so
  the same defect exists there, but the file is not a `GATE_STAGE_NAMES` member and the
  issue does not name it; a follow-up ticket carries it.
- **Widen `push-result.json`'s intent gate to `fix`/`continue`.** Rejected: confirmed
  by direct inspection of `workflows/archon-dark-factory.yaml` that no implement-path
  node ever writes `push-result.json`; widening the gate would be inert.
- **Bump `POLICY_VERSION` to `"1.2"`.** Rejected: the policy (the `_compute_outcome`
  state machine) is unchanged; only the correctness of its inputs improves. A version
  bump communicates "the rules changed," which would be misleading here.
- **Fix `post_mortem.py`'s `_ARTIFACT_FILES` lookup in the same change.** Rejected:
  confirmed it is fed the `runs/` base directory and performs its own search
  (`_find_run_dir()`), not a copy of this bug, and it is not named by the issue; a
  separate ticket if it turns out to need a fix after closer investigation.

---

## Open Questions (non-blocking)

- Exact rename for `_push_result_is_fresh` (`_artifact_is_fresh` is illustrative) is
  left to the plan/implement phase, as long as the semantics in R3 hold.
- Whether `docs/run-lifecycle.md`'s §8 table should annotate the three affected rows
  inline versus adding one shared footnote is left to the plan phase's judgment (R6).

## Assumptions (flagged)

- The implement path's intent value(s) that reach `cmd_assemble` with non-empty
  `validation.md`/`conformance.md`/`review.md` are `fix`/`continue` (per the issue's
  own evidence: "all 8 completed `fix`/`continue` records"). Confirmed at the operator
  amendment against `entrypoint.sh:103-104`: `fix` is the default and `continue` is
  matched; the workflow's own `new` never reaches `--intent`.
- `_archon_workflow_run_id`'s existing fail-closed behavior (returns `None` on a
  missing, unparseable, or unsafe-`run_id` cost JSON) is sufficient for this ticket;
  no change to that helper's own logic is needed, only to how many call sites share its
  one resolved value (R1).
- All code and line citations in this document were re-derived directly against
  current `main` during this refine pass (2026-11-22), per the standing lesson (see
  `.archon/memory/architecture.md`'s #182 entry) to never trust a cited line number
  without re-checking it against the live checkout. The operator amendment re-checked
  them against `origin/main` at `4584159` and corrected four (`cmd_assemble`'s extent,
  the `md_path.exists()` line, `push-and-pr`'s extent, the second call site).
- `_parse_artifact_stage` has never parsed a production file. `validation.md` is
  free-form (`commands/dark-factory-validate.md:243-247`) and falls to the substring
  heuristic at `run_record.py:361-363`, which reads a report containing both `PASS` and
  `Final status: FAIL` as `PASS`. Not fixed here; R5 adds a fixture with that shape
  pinning today's result, and a follow-up ticket covers the heuristic.
- For the owner: completed implement-run scores will fall below 1.0 for the first time
  (conformance-cycle and review-advisory penalties go live) under an unchanged
  `POLICY_VERSION`, and `gate_artifact_lookup` is what distinguishes post-fix records
  from the historical blind-scored ones.

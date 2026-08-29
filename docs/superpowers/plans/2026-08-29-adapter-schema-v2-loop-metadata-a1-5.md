# Implementation Plan: Adapter schema v2 — research-driven loop metadata blocks (A1.5)

**Issue:** #301 · **Epic:** #194 · **Depends on:** #195 (A1, shipped) ·
**Spec:** [docs/superpowers/specs/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md](../specs/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md)
**Status:** plan-pending-review

## Goal

Reshape `.factory/adapter.yaml`'s `loops:` entry from A1's flat 11-field mapping into
the Loop Engineering five-move shape (`discovery`/`handoff`/`verification`/
`persistence`/`scheduling`, all required, plus optional `human_checkpoint`/
`budget_caps`), add three optional research-driven metadata sub-blocks (`role_card`,
`economics`, `skills`), and add a `side_effect_level >= 4` conditional-requiredness
rule for `budget_caps`/`human_checkpoint`. This is a **breaking reshape, not additive**
— `loops:` has zero shipped usage anywhere, so no live adapter can fail on it (verified
in the spec). Parse/validate/surface only; no runtime enforcement (that stays with
epics #196–#199). `role_card.allowed_tools`/`forbidden_tools` are permanently excluded
(security-surface carve-out), and a `contract:` field (from a #311 research comment) is
rejected via the reserved-field mechanism rather than designed here.

## Architecture

- `scripts/factory_core/adapter.py`:
  - Replace `_LOOP_REQUIRED_FIELDS`/`_LOOP_STRING_FIELDS`/`_LOOP_LIST_FIELDS` with:
    `_LOOP_ENTRY_TOP_FIELDS` (required scalars `name`/`purpose`/`side_effect_level`,
    as a tuple — deterministic iteration order for error messages),
    `_LOOP_MOVE_BLOCKS` (the 5 required sub-block names, in validation order),
    `_LOOP_OPTIONAL_ENTRY_FIELDS` (grows across Tasks 3 and 5: `role_card`/
    `economics`/`skills`, then `budget_caps`/`human_checkpoint`), and
    `_LOOP_KNOWN_ENTRY_FIELDS` (the union, replacing the old membership set).
  - New generic `_validate_subblock(entry, index, name, block, *, str_fields=(),
    list_fields=(), int_fields=(), bool_fields=(), required_fields=(),
    reserved_fields=None)` helper, reused for all nine nested blocks (5 moves +
    `budget_caps` + `role_card`/`economics`/`skills`). It distinguishes an **absent**
    block key (`block not in entry` → `None`, valid — optional/not-yet-required) from
    a block key present but **null** (`discovery:` with no value in YAML — `None` is a
    real parsed value here, not Python's sentinel for "missing"; falls through to the
    `isinstance(val, dict)` check, which correctly raises `must be a mapping`).
    `reserved_fields` is the generic mechanism `role_card` uses for the R3
    `allowed_tools`/`forbidden_tools` exclusion — same check-before-generic-
    unknown-field pattern as the existing top-level/loop-entry reserved-field checks,
    implemented one nesting level deeper.
  - New `_ROLE_CARD_RESERVED_FIELDS = {"allowed_tools", "forbidden_tools"}` constant
    (Task 4).
  - Rewrite `_validate_loop(entry, index)` incrementally across Tasks 2-6 (see each
    task's diff): (1) check the 5 move blocks are present, (2) check top-level-of-entry
    keys are known (reserved-field check first), (3) check the 3 required scalars are
    present and well-typed, (4) validate each move block's internal shape via
    `_validate_subblock`, (5) validate the optional `role_card`/`economics`/`skills`
    (Task 3) then `budget_caps`/`human_checkpoint` (Task 5), (6) apply the R4
    conditional-requiredness rule last (Task 5 — needs `budget_caps`/
    `human_checkpoint` presence already resolved).
  - Generalize `_RESERVED_LOOP_FIELDS` values from bare epic numbers to full
    descriptions (`"memory_intervention": "epic #241 (per-loop memory intervention)"`)
    and add `"contract": "a follow-up child of epic #194 (completion-contract
    extension recommended by #311)"`; update the message template to
    `field '{key}' is reserved for {desc} and is not accepted in this schema; remove
    it` (Task 6).
  - Fix the stale pre-archive spec path in the `load()` comment (currently cites
    `docs/superpowers/specs/2026-07-07-adapter-schema-v2-loops-design.md`, which moved
    to `docs/archive/...` when A1 shipped) — Task 2.
  - No change to `_KNOWN_TOP`, `_MAP_KEYS`, `_RESERVED_TOP_FIELDS`, `_deep_merge`, or
    `load()`'s control flow around `loops:` (still validated independent of
    `schema_version`).
- `scripts/factory_core/adapter_defaults.py`: no change (`"loops": []` already there).
- `scripts/factory_core/run_record.py`: no change (`adapter.get(clone_dir, "loops") or
  []` already passes the shape through verbatim — R7).
- `tests/test_adapter.py`: reshape `_VALID_LOOP_ENTRY` to the five-move nested form;
  replace the `_LOOP_STRING_FIELDS`/`_LOOP_LIST_FIELDS`-parametrized tests with
  explicit per-block-field tables; add coverage for missing/malformed/null blocks, the
  three metadata blocks, `budget_caps`/`human_checkpoint`, R4, R5's `contract`, and the
  `adapter.get()` dotted-path/all-optional-blocks acceptance criteria.
- `tests/test_run_record.py`: reshape `test_assemble_surfaces_loops_from_adapter`'s
  fixture YAML and expected dict to the new nesting (only this one test's shape
  changes — R7). This reshape happens in **Task 1** (not a separate later task) so
  that Task 2's implementation commit leaves the *entire* suite green, not just
  `test_adapter.py`.
- `README.md`: add a `loops` row to the "adapter.yaml keys" table; fix the stale
  `schema_version` row.

## Tech Stack

Python 3 (stdlib `argparse`/`copy`/`os`/`sys` + `pyyaml`, the project's sole pinned
dependency for this tooling), `pytest` for tests. No new dependency (spec R6 — stays
`isinstance`-only, no `jsonschema`). Entirely within `factory_core`. CI pins Python
3.12 (`.github/workflows/ci.yml`); the Dockerfile runs 3.14. `factory_core` already
uses bare PEP-604 (`X | None`) annotations elsewhere (`deconflict.py`, `rescue.py`),
so `_validate_subblock`'s `-> dict | None` return annotation is safe on both.

## File Structure

| Path | Change |
|---|---|
| `scripts/factory_core/adapter.py` | Replace flat loop constants with five-move + optional-block shape; generic `_validate_subblock` helper; incrementally rewritten `_validate_loop` (Tasks 2-6); generalized `_RESERVED_LOOP_FIELDS`; stale comment fix |
| `tests/test_adapter.py` | Reshape `_VALID_LOOP_ENTRY`; replace flat-field parametrized tests with per-block tables; add role_card/economics/skills/budget_caps/human_checkpoint/R4/R5/get()/all-optional-blocks coverage |
| `tests/test_run_record.py` | Reshape `test_assemble_surfaces_loops_from_adapter`'s fixture + expected dict (Task 1) |
| `README.md` | Add `loops` row; fix `schema_version` row |

All commands below run from the repo root (`/workspace/dark-factory`). Per accumulated
memory (`.archon/memory/codebase-patterns.md`, issue #42), this spec and this plan do
**not** transfer automatically from this `refine/issue-301-...` branch to the
`feat/issue-301-...` branch the implementation phase runs on — the first thing the
implement phase must do is copy `docs/superpowers/specs/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md`
and this plan file onto its branch and commit them, before starting Task 1.

---

## Task 1: Write the full R1 (five-move) test suite — RED

**Files:** `tests/test_adapter.py`, `tests/test_run_record.py`

Writes every test for the five-move restructuring (shape, missing/malformed/null
blocks, wrong-typed fields, the flat-A1-rejection acceptance criterion, and the
`adapter.get()` dotted-path criterion) *and* reshapes the one `test_run_record.py`
fixture that depends on the loop-entry shape — all before any `adapter.py` change, so
Task 2's implementation commit is the single point where the whole suite goes green
(not just `test_adapter.py`).

### Steps

1. **Replace the fixture** — in `tests/test_adapter.py`, replace `_VALID_LOOP_ENTRY`
   (lines 81-94) with:

   ```python
   _VALID_LOOP_ENTRY = """
   loops:
     - name: nightly-scan-triage
       purpose: Triage overnight scanner false positives
       side_effect_level: 2
       discovery:
         trigger: 'cron:0 6 * * *'
         inputs: ["scanner_output.json"]
       handoff:
         outputs: ["triage_report.md"]
         manifest: handoffs/triage_handoff.py
       verification:
         verifier: verifiers/triage_verifier.py
         stop_condition: stop_conditions/triage_stop.py
       persistence:
         artifacts: [".factory/state/triage.json"]
       scheduling:
         failure_behavior: escalate_to_human
   """
   ```

2. **Update the shape-dependent assertion** — `test_valid_loop_entry_parses` (was
   lines 97-104):

   ```python
   def test_valid_loop_entry_parses(tmp_path, capsys):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
       merged = adapter.load(str(tmp_path))
       assert len(merged["loops"]) == 1
       entry = merged["loops"][0]
       assert entry["name"] == "nightly-scan-triage"
       assert entry["side_effect_level"] == 2
       assert entry["discovery"] == {
           "trigger": "cron:0 6 * * *", "inputs": ["scanner_output.json"]}
       assert entry["handoff"] == {
           "outputs": ["triage_report.md"], "manifest": "handoffs/triage_handoff.py"}
       assert entry["verification"] == {
           "verifier": "verifiers/triage_verifier.py",
           "stop_condition": "stop_conditions/triage_stop.py"}
       assert entry["persistence"] == {"artifacts": [".factory/state/triage.json"]}
       assert entry["scheduling"] == {"failure_behavior": "escalate_to_human"}
       assert "unknown adapter key 'loops'" not in capsys.readouterr().err


   def test_loops_get_dotted_path_returns_verbatim(tmp_path):
       """Acceptance criterion: the R1 shape is returned verbatim by adapter.get(),
       not just adapter.load()."""
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
       loops = adapter.get(str(tmp_path), "loops")
       assert loops[0]["discovery"] == {
           "trigger": "cron:0 6 * * *", "inputs": ["scanner_output.json"]}
   ```

3. **Replace `test_loop_entry_missing_required_field_raises`** (was lines 130-135) with
   the reindented equivalent:

   ```python
   def test_loop_entry_missing_required_top_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       entry = _VALID_LOOP_ENTRY.replace(
           "    purpose: Triage overnight scanner false positives\n", "")
       (d / "adapter.yaml").write_text(entry)
       with pytest.raises(adapter.AdapterError, match=r"missing required field 'purpose'"):
           adapter.load(str(tmp_path))
   ```

4. **Delete the two flat-field parametrized tests** that reference module constants
   the reshape removes: `test_loop_entry_string_field_wrong_type_raises`
   (lines 146-153) and `test_loop_entry_list_field_wrong_type_raises` (lines 156-163).

5. **Leave unchanged** (top-level-only checks, unaffected *in their final form* by the
   internal reshape). Most of these consume the reshaped `_VALID_LOOP_ENTRY` and are
   therefore transiently red for the rest of this task's red step too, going green
   together with everything else in Task 2: `test_loop_entry_unknown_field_raises`,
   `test_loops_independent_of_schema_version`,
   `test_loop_entry_side_effect_level_out_of_range_raises`,
   `test_loop_entry_side_effect_level_non_int_raises`,
   `test_loop_entry_side_effect_level_bool_raises`, `test_duplicate_loop_names_raise`,
   `test_loop_entry_memory_intervention_reserved_raises`. Three write their own literal
   YAML instead of using the fixture, so they stay green throughout — untouched by the
   red step at all: `test_loop_entry_not_a_mapping_raises`, `test_loops_not_a_list_raises`,
   `test_mechanism_candidates_top_level_reserved_raises`.

6. **Add per-move-block coverage** — append after
   `test_loop_entry_unknown_field_raises`. Block names are hardcoded (not
   `adapter._LOOP_MOVE_BLOCKS`) because that constant doesn't exist until Task 2:

   ```python
   _MOVE_BLOCKS = ["discovery", "handoff", "verification", "persistence", "scheduling"]
   _MOVE_BLOCK_REQUIRED_FIELDS = [
       ("discovery", "trigger"), ("discovery", "inputs"),
       ("handoff", "manifest"), ("handoff", "outputs"),
       ("verification", "verifier"), ("verification", "stop_condition"),
       ("persistence", "artifacts"),
       ("scheduling", "failure_behavior"),
   ]
   _MOVE_BLOCK_STR_FIELDS = [
       ("discovery", "trigger"), ("handoff", "manifest"),
       ("verification", "verifier"), ("verification", "stop_condition"),
       ("scheduling", "failure_behavior"),
   ]
   _MOVE_BLOCK_LIST_FIELDS = [
       ("discovery", "inputs"), ("handoff", "outputs"), ("persistence", "artifacts"),
   ]


   @pytest.mark.parametrize("block", _MOVE_BLOCKS)
   def test_loop_entry_missing_required_block_raises(tmp_path, block):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       del parsed["loops"][0][block]
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=re.escape(f"missing required block '{block}'")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block", _MOVE_BLOCKS)
   def test_loop_move_block_not_a_mapping_raises(tmp_path, block):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][block] = "not-a-mapping"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=re.escape(f"block '{block}' must be a mapping")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block", _MOVE_BLOCKS)
   def test_loop_move_block_null_value_raises(tmp_path, block):
       """A block key present but with no YAML value (`discovery:` alone) parses
       to None, which must be rejected as 'not a mapping', not silently accepted
       as 'absent' — None is a real parsed value here, distinct from the key
       being missing entirely."""
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][block] = None
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=re.escape(f"block '{block}' must be a mapping")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block", _MOVE_BLOCKS)
   def test_loop_move_block_unknown_field_raises(tmp_path, block):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][block]["extra_typo_field"] = "oops"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block '{block}': unknown field 'extra_typo_field'")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block,field", _MOVE_BLOCK_REQUIRED_FIELDS)
   def test_loop_move_block_missing_required_field_raises(tmp_path, block, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       del parsed["loops"][0][block][field]
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block '{block}': missing required field '{field}'")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block,field", _MOVE_BLOCK_STR_FIELDS)
   def test_loop_move_block_string_field_wrong_type_raises(tmp_path, block, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][block][field] = 42
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block '{block}': field '{field}' must be a non-empty string")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("block,field", _MOVE_BLOCK_LIST_FIELDS)
   def test_loop_move_block_list_field_wrong_type_raises(tmp_path, block, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][block][field] = "not-a-list"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block '{block}': field '{field}' must be a list of strings")):
           adapter.load(str(tmp_path))


   def test_flat_a1_shaped_entry_fails(tmp_path):
       """An A1-shaped flat entry (pre-A1.5) fails with the first missing move
       block — there is no dual-form fallback (spec R1)."""
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(
           "loops:\n"
           "  - name: nightly-scan-triage\n"
           "    purpose: Triage overnight scanner false positives\n"
           "    trigger: 'cron:0 6 * * *'\n"
           "    inputs: []\n"
           "    outputs: []\n"
           "    artifacts: []\n"
           "    verifier: verifiers/triage_verifier.py\n"
           "    stop_condition: stop_conditions/triage_stop.py\n"
           "    failure_behavior: escalate_to_human\n"
           "    side_effect_level: 2\n"
           "    handoff: handoffs/triage_handoff.py\n"
       )
       with pytest.raises(adapter.AdapterError, match=r"missing required block 'discovery'"):
           adapter.load(str(tmp_path))
   ```

7. **Reshape `test_run_record.py`'s loop-surfacing fixture** — replace
   `test_assemble_surfaces_loops_from_adapter` (`tests/test_run_record.py:1035-1074`)
   with:

   ```python
   def test_assemble_surfaces_loops_from_adapter(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       clone_dir = tmp_path / "clone"
       (clone_dir / ".factory").mkdir(parents=True)
       (clone_dir / ".factory" / "adapter.yaml").write_text(
           "loops:\n"
           "  - name: nightly-scan-triage\n"
           "    purpose: Triage overnight scanner false positives\n"
           "    side_effect_level: 2\n"
           "    discovery:\n"
           "      trigger: 'cron:0 6 * * *'\n"
           "      inputs: []\n"
           "    handoff:\n"
           "      outputs: []\n"
           "      manifest: handoffs/triage_handoff.py\n"
           "    verification:\n"
           "      verifier: verifiers/triage_verifier.py\n"
           "      stop_condition: stop_conditions/triage_stop.py\n"
           "    persistence:\n"
           "      artifacts: []\n"
           "    scheduling:\n"
           "      failure_behavior: escalate_to_human\n"
       )

       artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
       out = tmp_path / "run-record.json"
       args = _AssembleArgs(artifacts_dir, out)
       args.clone_dir = str(clone_dir)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       assert rec["loops"] == [{
           "name": "nightly-scan-triage",
           "purpose": "Triage overnight scanner false positives",
           "side_effect_level": 2,
           "discovery": {"trigger": "cron:0 6 * * *", "inputs": []},
           "handoff": {"outputs": [], "manifest": "handoffs/triage_handoff.py"},
           "verification": {
               "verifier": "verifiers/triage_verifier.py",
               "stop_condition": "stop_conditions/triage_stop.py",
           },
           "persistence": {"artifacts": []},
           "scheduling": {"failure_behavior": "escalate_to_human"},
       }]
   ```

   `test_assemble_no_adapter_file_loops_empty`,
   `test_assemble_malformed_adapter_loops_empty_fail_open`, and
   `test_assemble_default_clone_dir_when_unset` are unaffected — leave them as-is.

8. **Verify red:**

   ```bash
   python -m pytest tests/test_adapter.py tests/test_run_record.py -v
   ```

   Expected: many failures. In `test_adapter.py`, every new/reshaped test fails because
   today's `adapter.py` (A1's flat-field validator) doesn't recognize nested blocks —
   most raise `unknown field 'discovery'` (or similar) instead of the expected
   messages, and `test_valid_loop_entry_parses`/`test_loops_get_dotted_path_returns_verbatim`
   fail with an unexpected `AdapterError` (`unknown field 'discovery'`) — A1's validator
   rejects the nested `discovery:` block before `load()`/`get()` ever returns, so there
   is no flat-shaped result to assert against. In `test_run_record.py`,
   `test_assemble_surfaces_loops_from_adapter` fails because the new nested YAML fails
   `adapter.load()`'s validation, `run_record.py` fails open to `loops: []`, and
   `rec["loops"] == [{...}]` doesn't match. 0 passed among the touched tests; the rest
   of each file (parity tests, non-loop tests) still passes.

9. **Commit:**

   ```bash
   git add tests/test_adapter.py tests/test_run_record.py
   git commit -m "test(adapter): five-move loop-entry shape coverage — RED (#301 R1)"
   ```

## Task 2: Implement the five-move restructuring — GREEN

**Files:** `scripts/factory_core/adapter.py`

Makes every test from Task 1 pass. Implements spec R1 and the `_validate_subblock`
helper from R6/Architecture, scoped to only what Task 1's tests need: `str_fields`/
`list_fields`/`int_fields`/`bool_fields`/`required_fields` params exist now (all
`_validate_subblock` calls Task 2 itself adds use only `str_fields`/`list_fields`/
`required_fields`; `int_fields`/`bool_fields` are exercised starting Task 5/Task 3
respectively). The `reserved_fields` parameter is deliberately **not** added yet — it
has no driving test until Task 4 (R3) — and gets added there as its own diff. Also
does the R1 "may fix in passing" stale-comment cleanup.

### Steps

1. **Implement** — in `scripts/factory_core/adapter.py`, replace lines 12-20
   (`_LOOP_REQUIRED_FIELDS`/`_LOOP_STRING_FIELDS`/`_LOOP_LIST_FIELDS`) with:

   ```python
   _LOOP_ENTRY_TOP_FIELDS = ("name", "purpose", "side_effect_level")
   _LOOP_MOVE_BLOCKS = ("discovery", "handoff", "verification", "persistence", "scheduling")
   _LOOP_OPTIONAL_ENTRY_FIELDS = set()  # grows in Tasks 3 and 5
   _LOOP_KNOWN_ENTRY_FIELDS = (
       set(_LOOP_ENTRY_TOP_FIELDS) | set(_LOOP_MOVE_BLOCKS) | _LOOP_OPTIONAL_ENTRY_FIELDS
   )
   ```

2. Add the generic sub-block validator, immediately before `_validate_loop`:

   ```python
   def _validate_subblock(entry, index, name, block, *, str_fields=(), list_fields=(),
                           int_fields=(), bool_fields=(), required_fields=()) -> dict | None:
       if block not in entry:
           return None
       val = entry[block]
       if not isinstance(val, dict):
           raise AdapterError(f"loops[{index}] ('{name}'): block '{block}' must be a mapping")
       known = set(str_fields) | set(list_fields) | set(int_fields) | set(bool_fields)
       for key in val:
           if key not in known:
               raise AdapterError(f"loops[{index}] ('{name}'): block '{block}': unknown field '{key}'")
       for field in required_fields:
           if field not in val:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): block '{block}': missing required field '{field}'")
       for field in str_fields:
           if field in val:
               v = val[field]
               if not isinstance(v, str) or not v:
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a non-empty string")
       for field in list_fields:
           if field in val:
               v = val[field]
               if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a list of strings")
       for field in int_fields:
           if field in val:
               v = val[field]
               if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be an int >= 1")
       for field in bool_fields:
           if field in val:
               v = val[field]
               if not isinstance(v, bool):
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a bool")
       return val
   ```

   The `if block not in entry: return None` / `val = entry[block]` guard (rather than
   `entry.get(block)` collapsed with an `is None` check) is deliberate: PyYAML parses a
   block key written with no value (`discovery:` alone) to `None`, which is a real
   parsed value distinct from the key being absent — it must fall through to the
   `isinstance(val, dict)` check below and raise `must be a mapping`, not be treated as
   "block omitted."

3. Replace `_validate_loop` (old lines 37-66) with:

   ```python
   def _validate_loop(entry, index: int) -> None:
       if not isinstance(entry, dict):
           raise AdapterError(f"loops[{index}] must be a mapping, got {type(entry).__name__}")
       name = entry.get("name", "?")

       for block in _LOOP_MOVE_BLOCKS:
           if block not in entry:
               raise AdapterError(f"loops[{index}] ('{name}'): missing required block '{block}'")

       for key in entry:
           if key not in _LOOP_KNOWN_ENTRY_FIELDS:
               if key in _RESERVED_LOOP_FIELDS:
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): field '{key}' is reserved for epic "
                       f"{_RESERVED_LOOP_FIELDS[key]} (per-loop memory intervention) and is "
                       f"not accepted in schema v2; remove it"
                   )
               raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")

       for field in _LOOP_ENTRY_TOP_FIELDS:
           if field not in entry:
               raise AdapterError(f"loops[{index}] ('{name}'): missing required field '{field}'")
       for field in ("name", "purpose"):
           val = entry[field]
           if not isinstance(val, str) or not val:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): field '{field}' must be a non-empty string")
       # side_effect_level scale (owned by #193, enforced by #196 — reproduced here
       # only as a range check, not redefined): 1=read-only research, 2=artifact
       # writing, 3=ticket creation, 4=code modification, 5=PR creation,
       # 6=external production side effect (A2 rejects 6; A1.5 does not).
       sel = entry["side_effect_level"]
       if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6):
           raise AdapterError(
               f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int between 1 and 6")

       _validate_subblock(entry, index, name, "discovery",
                           str_fields=("trigger",), list_fields=("inputs",),
                           required_fields=("trigger", "inputs"))
       _validate_subblock(entry, index, name, "handoff",
                           str_fields=("manifest",), list_fields=("outputs",),
                           required_fields=("manifest", "outputs"))
       _validate_subblock(entry, index, name, "verification",
                           str_fields=("verifier", "stop_condition"),
                           required_fields=("verifier", "stop_condition"))
       _validate_subblock(entry, index, name, "persistence",
                           list_fields=("artifacts",), required_fields=("artifacts",))
       _validate_subblock(entry, index, name, "scheduling",
                           str_fields=("failure_behavior",),
                           required_fields=("failure_behavior",))
   ```

   (This is the *current* end state of `_validate_loop` after Task 2 only — Tasks 3-6
   each append more validation before the function's end, shown as diffs in place.)

4. **Fix the stale comment** (spec: "the implementer may fix it in passing") — in
   `load()`, change:

   ```python
   # docs/superpowers/specs/2026-07-07-adapter-schema-v2-loops-design.md),
   ```

   to:

   ```python
   # docs/archive/2026-07-07-adapter-schema-v2-loops-design.md),
   ```

5. **Verify green — full suite, not just `test_adapter.py`** (Task 1 reshaped
   `test_run_record.py` too, so this is the one commit where both files must pass
   together):

   ```bash
   python -m pytest tests/ -v
   ```

   Expected: exit code 0, 0 failed.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py
   git commit -m "feat(adapter): five-move loop-entry restructuring, generic sub-block validator (#301 R1)"
   ```

## Task 3: `role_card`/`economics`/`skills` optional metadata blocks (R2)

**Files:** `tests/test_adapter.py`, `scripts/factory_core/adapter.py`

### Steps

1. **Write failing tests** — add to `tests/test_adapter.py`:

   ```python
   def test_role_card_valid_parses(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {
           "name": "Triage Agent",
           "responsibilities": ["classify false positives"],
           "non_responsibilities": ["patch the scanner"],
           "output_schema": "schemas/triage_report.json",
           "fallback_path": "manual-review:security-team",
           "observability": ["triage.completed", "triage.escalated"],
       }
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["role_card"]["name"] == "Triage Agent"


   def test_role_card_empty_dict_missing_name_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'role_card': missing required field 'name'"):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("field", ["name", "output_schema", "fallback_path"])
   def test_role_card_string_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: 42}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'role_card': field '{field}' must be a non-empty string")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("field", ["responsibilities", "non_responsibilities", "observability"])
   def test_role_card_list_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: "not-a-list"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'role_card': field '{field}' must be a list of strings")):
           adapter.load(str(tmp_path))


   def test_role_card_unknown_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {"name": "Triage Agent", "extra_typo_field": "oops"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'role_card': unknown field 'extra_typo_field'"):
           adapter.load(str(tmp_path))


   def test_economics_empty_dict_accepted(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["economics"] = {}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["economics"] == {}


   def test_economics_valid_parses(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["economics"] = {
           "context_offload_required": True,
           "feature_demand": "high",
           "model_capability_floor": "sonnet",
       }
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["economics"]["context_offload_required"] is True


   @pytest.mark.parametrize("bad_bool", [1, "yes"])
   def test_economics_context_offload_required_rejects_non_bool(tmp_path, bad_bool):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["economics"] = {"context_offload_required": bad_bool}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'economics': field 'context_offload_required' must be a bool"):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("field", ["feature_demand", "model_capability_floor"])
   def test_economics_string_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["economics"] = {field: 42}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'economics': field '{field}' must be a non-empty string")):
           adapter.load(str(tmp_path))


   def test_economics_unknown_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["economics"] = {"extra_typo_field": "oops"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'economics': unknown field 'extra_typo_field'"):
           adapter.load(str(tmp_path))


   def test_skills_empty_dict_accepted(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["skills"] = {}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["skills"] == {}


   @pytest.mark.parametrize("field", ["primary", "supplemental", "forbidden", "eval_cases"])
   def test_skills_list_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["skills"] = {field: "not-a-list"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'skills': field '{field}' must be a list of strings")):
           adapter.load(str(tmp_path))


   def test_skills_valid_parses(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["skills"] = {
           "primary": ["triage-classifier"],
           "supplemental": ["log-search"],
           "forbidden": ["deploy"],
           "eval_cases": ["evals/triage_case_1.yaml"],
       }
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["skills"]["primary"] == ["triage-classifier"]


   def test_skills_unknown_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["skills"] = {"extra_typo_field": "oops"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'skills': unknown field 'extra_typo_field'"):
           adapter.load(str(tmp_path))
   ```

2. **Verify red:**

   ```bash
   python -m pytest tests/test_adapter.py -k "role_card or economics or skills" -v
   ```

   Expected: every test fails with `unknown field 'role_card'` (or `'economics'`/
   `'skills'`) — Task 2's `_LOOP_KNOWN_ENTRY_FIELDS` doesn't include these names yet.

3. **Implement** — in `scripts/factory_core/adapter.py`:
   - Change `_LOOP_OPTIONAL_ENTRY_FIELDS = set()` to:

     ```python
     _LOOP_OPTIONAL_ENTRY_FIELDS = {"role_card", "economics", "skills"}
     ```

   - Append to `_validate_loop`, immediately after the `scheduling` `_validate_subblock`
     call added in Task 2:

     ```python
       _validate_subblock(entry, index, name, "role_card",
                           str_fields=("name", "output_schema", "fallback_path"),
                           list_fields=("responsibilities", "non_responsibilities", "observability"),
                           required_fields=("name",))
       _validate_subblock(entry, index, name, "economics",
                           str_fields=("feature_demand", "model_capability_floor"),
                           bool_fields=("context_offload_required",))
       _validate_subblock(entry, index, name, "skills",
                           list_fields=("primary", "supplemental", "forbidden", "eval_cases"))
     ```

4. **Verify green:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: exit code 0, 0 failed.

5. **Commit:**

   ```bash
   git add tests/test_adapter.py scripts/factory_core/adapter.py
   git commit -m "feat(adapter): role_card/economics/skills metadata blocks (#301 R2)"
   ```

## Task 4: `role_card.allowed_tools`/`forbidden_tools` permanent exclusion (R3)

**Files:** `tests/test_adapter.py`, `scripts/factory_core/adapter.py`

### Steps

1. **Write failing test** — add to `tests/test_adapter.py`:

   ```python
   @pytest.mark.parametrize("field", ["allowed_tools", "forbidden_tools"])
   def test_role_card_tool_fields_permanently_excluded(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["role_card"] = {"name": "Triage Agent", field: ["bash"]}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(
           adapter.AdapterError,
           match=re.escape(
               f"role_card field '{field}' is a tool allow/deny declaration and is "
               f"permanently excluded from adapter.yaml")):
           adapter.load(str(tmp_path))
   ```

2. **Verify red:**

   ```bash
   python -m pytest tests/test_adapter.py -k tool_fields_permanently_excluded -v
   ```

   Expected: both parametrized cases fail — Task 3's `role_card` call has no
   `reserved_fields`, so `allowed_tools`/`forbidden_tools` currently fall through to
   the generic `block 'role_card': unknown field 'allowed_tools'` message instead of
   the R3-specific one.

3. **Implement** — in `scripts/factory_core/adapter.py`:
   - Task 2 built `_validate_subblock` without a `reserved_fields` parameter, since
     nothing needed it yet — R3 is the first consumer. Add it now. Change the
     signature from:

     ```python
     def _validate_subblock(entry, index, name, block, *, str_fields=(), list_fields=(),
                             int_fields=(), bool_fields=(), required_fields=()) -> dict | None:
     ```

     to:

     ```python
     def _validate_subblock(entry, index, name, block, *, str_fields=(), list_fields=(),
                             int_fields=(), bool_fields=(), required_fields=(),
                             reserved_fields=None) -> dict | None:
     ```

   - Add the reserved-field check as the first branch inside the `for key in val:`
     unknown-field loop, before the existing `if key not in known:` check:

     ```python
        for key in val:
            if reserved_fields and key in reserved_fields:
                raise AdapterError(
                    f"loops[{index}] ('{name}'): {block} field '{key}' is a tool allow/deny "
                    f"declaration and is permanently excluded from adapter.yaml (CLAUDE.md § "
                    f"Trusted comment channels); remove it"
                )
            if key not in known:
                raise AdapterError(f"loops[{index}] ('{name}'): block '{block}': unknown field '{key}'")
     ```

   - Add, next to `_RESERVED_LOOP_FIELDS` near the top of the module:

     ```python
     # role_card fields that are tool allow/deny declarations — permanently excluded
     # (CLAUDE.md § Trusted comment channels; comment-channel input may never
     # authorize tool allow/deny surfaces). Not a deferral: there is no ticket to
     # point to, unlike _RESERVED_LOOP_FIELDS.
     _ROLE_CARD_RESERVED_FIELDS = {"allowed_tools", "forbidden_tools"}
     ```

   - Change the `role_card` `_validate_subblock` call (added in Task 3) to add
     `reserved_fields`:

     ```python
       _validate_subblock(entry, index, name, "role_card",
                           str_fields=("name", "output_schema", "fallback_path"),
                           list_fields=("responsibilities", "non_responsibilities", "observability"),
                           required_fields=("name",),
                           reserved_fields=_ROLE_CARD_RESERVED_FIELDS)
     ```

4. **Verify green:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: exit code 0, 0 failed.

5. **Commit:**

   ```bash
   git add tests/test_adapter.py scripts/factory_core/adapter.py
   git commit -m "feat(adapter): permanently exclude role_card.allowed_tools/forbidden_tools (#301 R3)"
   ```

## Task 5: `human_checkpoint`/`budget_caps` and the R4 conditional-requiredness rule

**Files:** `tests/test_adapter.py`, `scripts/factory_core/adapter.py`

Also adds the "R1 example entry with every optional field" acceptance-criteria test,
since this is the task where the last optional block (`budget_caps`) lands and all
five optional blocks first coexist.

### Steps

1. **Write failing tests** — add to `tests/test_adapter.py`:

   ```python
   def test_budget_caps_empty_dict_missing_max_tokens_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["budget_caps"] = {}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'budget_caps': missing required field 'max_tokens'"):
           adapter.load(str(tmp_path))


   def test_budget_caps_max_tokens_bool_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["budget_caps"] = {"max_tokens": True}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'budget_caps': field 'max_tokens' must be an int >= 1"):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("field", ["max_tokens", "max_retry_spend"])
   def test_budget_caps_int_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, field: "many"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"block 'budget_caps': field '{field}' must be an int >= 1")):
           adapter.load(str(tmp_path))


   def test_budget_caps_unknown_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "extra_typo_field": "oops"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"block 'budget_caps': unknown field 'extra_typo_field'"):
           adapter.load(str(tmp_path))


   def test_human_checkpoint_wrong_type_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["human_checkpoint"] = 42
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=r"field 'human_checkpoint' must be a non-empty string"):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("sel", [4, 5, 6])
   def test_side_effect_level_high_without_budget_caps_raises(tmp_path, sel):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = sel
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape(f"side_effect_level {sel} >= 4 requires 'budget_caps'")):
           adapter.load(str(tmp_path))


   def test_side_effect_level_high_with_budget_caps_missing_human_checkpoint_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = 4
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError,
                           match=re.escape("side_effect_level 4 >= 4 requires 'human_checkpoint'")):
           adapter.load(str(tmp_path))


   def test_side_effect_level_high_with_both_caps_accepted(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = 6
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "max_retry_spend": 10000}
       parsed["loops"][0]["human_checkpoint"] = "manual-approval:slack-#factory-ops"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["side_effect_level"] == 6


   def test_side_effect_level_3_without_either_accepted(tmp_path):
       """Below the R4 threshold: no budget_caps/human_checkpoint required."""
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = 3
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       merged = adapter.load(str(tmp_path))
       assert merged["loops"][0]["side_effect_level"] == 3


   def test_loop_entry_all_optional_blocks_parses(tmp_path):
       """Acceptance criterion: the R1 example entry, with every optional field
       declared at once, parses and round-trips through adapter.get()."""
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = 4
       parsed["loops"][0]["human_checkpoint"] = "manual-approval:slack-#factory-ops"
       parsed["loops"][0]["budget_caps"] = {"max_tokens": 50000, "max_retry_spend": 10000}
       parsed["loops"][0]["role_card"] = {"name": "Triage Agent"}
       parsed["loops"][0]["economics"] = {"feature_demand": "high"}
       parsed["loops"][0]["skills"] = {"primary": ["triage-classifier"]}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       loops = adapter.get(str(tmp_path), "loops")
       entry = loops[0]
       assert entry["human_checkpoint"] == "manual-approval:slack-#factory-ops"
       assert entry["budget_caps"]["max_tokens"] == 50000
       assert entry["role_card"]["name"] == "Triage Agent"
       assert entry["economics"]["feature_demand"] == "high"
       assert entry["skills"]["primary"] == ["triage-classifier"]
   ```

2. **Verify red:**

   ```bash
   python -m pytest tests/test_adapter.py -k "budget_caps or human_checkpoint or side_effect_level_high or side_effect_level_3 or all_optional_blocks" -v
   ```

   Expected: every case fails except `test_side_effect_level_3_without_either_accepted`
   (see below) — but not all for the same reason. Cases that declare `budget_caps`
   and/or `human_checkpoint` (the `budget_caps_*`, `human_checkpoint_wrong_type`,
   `side_effect_level_high_with_budget_caps_missing_human_checkpoint`,
   `side_effect_level_high_with_both_caps_accepted`, and `all_optional_blocks` cases)
   fail on a message mismatch: neither field is in `_LOOP_OPTIONAL_ENTRY_FIELDS` yet,
   so the actual error is `unknown field 'budget_caps'` (or `'human_checkpoint'`), not
   what each test expects. (Note `yaml.dump`'s default `sort_keys=True` re-sorts entry
   keys alphabetically when the fixture round-trips through YAML, so for
   `all_optional_blocks` the first unknown key `adapter.load()` hits is `budget_caps`,
   not `human_checkpoint`, even though the test sets `human_checkpoint` first in
   Python.) The three `test_side_effect_level_high_without_budget_caps_raises[4/5/6]`
   cases declare neither field, so the entry loads without error and they fail on
   `pytest.raises` itself (no exception raised — no R4 check exists yet).
   `test_side_effect_level_3_without_either_accepted` is the sole case that
   **already passes** at this state — it declares no optional block and only sets
   `side_effect_level: 3`, which was already valid before this task. It isn't a
   red/green case for R4; it's the control that later proves the R4 rule doesn't
   over-trigger below level 4.

3. **Implement** — in `scripts/factory_core/adapter.py`:
   - Change `_LOOP_OPTIONAL_ENTRY_FIELDS` (from Task 3) to:

     ```python
     _LOOP_OPTIONAL_ENTRY_FIELDS = {"role_card", "economics", "skills", "budget_caps", "human_checkpoint"}
     ```

   - Append to `_validate_loop`, after the `economics`/`skills` `_validate_subblock`
     calls (added in Task 3) and before the function returns:

     ```python
       if "human_checkpoint" in entry:
           v = entry["human_checkpoint"]
           if not isinstance(v, str) or not v:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): field 'human_checkpoint' must be a non-empty string")

       _validate_subblock(entry, index, name, "budget_caps",
                           int_fields=("max_tokens", "max_retry_spend"),
                           required_fields=("max_tokens",))

       if sel >= 4:
           if "budget_caps" not in entry:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): side_effect_level {sel} >= 4 requires 'budget_caps'")
           if "human_checkpoint" not in entry:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): side_effect_level {sel} >= 4 requires 'human_checkpoint'")
     ```

     (`sel` is already bound earlier in `_validate_loop`, from Task 2's
     `side_effect_level` range check.)

4. **Verify green:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: exit code 0, 0 failed.

5. **Commit:**

   ```bash
   git add tests/test_adapter.py scripts/factory_core/adapter.py
   git commit -m "feat(adapter): budget_caps/human_checkpoint and R4 conditional-requiredness (#301 R4)"
   ```

## Task 6: `contract:` reserved-field rejection (R5)

**Files:** `tests/test_adapter.py`, `scripts/factory_core/adapter.py`

### Steps

1. **Write failing test** — add to `tests/test_adapter.py`, near
   `test_loop_entry_memory_intervention_reserved_raises`:

   ```python
   def test_loop_entry_contract_reserved_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["contract"] = {"objective": "whatever"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(
           adapter.AdapterError,
           match=r"reserved for a follow-up child of epic #194"):
           adapter.load(str(tmp_path))
   ```

2. **Verify red:**

   ```bash
   python -m pytest tests/test_adapter.py -k contract_reserved -v
   ```

   Expected: fails with `unknown field 'contract'` — `contract` isn't in
   `_RESERVED_LOOP_FIELDS` yet.

3. **Implement** — in `scripts/factory_core/adapter.py`, replace the current
   `_RESERVED_LOOP_FIELDS = {"memory_intervention": "#241"}` (unchanged since A1) with:

   ```python
   _RESERVED_LOOP_FIELDS = {
       "memory_intervention": "epic #241 (per-loop memory intervention)",
       "contract": "a follow-up child of epic #194 (completion-contract extension recommended by #311)",
   }
   ```

   and change the reserved-field branch inside `_validate_loop`'s top-level-of-entry
   unknown-key loop from:

   ```python
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): field '{key}' is reserved for epic "
                       f"{_RESERVED_LOOP_FIELDS[key]} (per-loop memory intervention) and is "
                       f"not accepted in schema v2; remove it"
                   )
   ```

   to:

   ```python
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): field '{key}' is reserved for "
                       f"{_RESERVED_LOOP_FIELDS[key]} and is not accepted in this schema; remove it"
                   )
   ```

4. **Verify green — including the existing `memory_intervention` regression:**

   ```bash
   python -m pytest tests/test_adapter.py -k "contract_reserved or memory_intervention_reserved" -v
   ```

   Expected: `2 passed` — the new `contract` rejection, and confirmation that the
   generalized message (`reserved for epic #241 (per-loop memory intervention) and is
   not accepted in this schema; remove it`) still contains the substring `reserved for
   epic #241` the pre-existing `test_loop_entry_memory_intervention_reserved_raises`
   matches on.

5. **Run the full file:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: exit code 0, 0 failed.

6. **Commit:**

   ```bash
   git add tests/test_adapter.py scripts/factory_core/adapter.py
   git commit -m "feat(adapter): contract: reserved-field rejection, generalize reserved-field messages (#301 R5)"
   ```

## Task 7: README `adapter.yaml keys` table — add `loops` row, fix `schema_version` row

**Files:** `README.md`

Satisfies the spec's final acceptance-criteria bullet. Documentation-only; no test.

### Steps

1. **Edit** `README.md` — in the "adapter.yaml keys" table (currently lines 160-176),
   change line 164 from:

   ```markdown
   | `schema_version` | `int` | Must be `1`. |
   ```

   to:

   ```markdown
   | `schema_version` | `int` | Inert metadata; never gates validation. |
   ```

   and add a new row after the `token_optimization` row (currently line 174), before
   the closing `All keys are optional...` sentence:

   ```markdown
   | `loops` | `list[map]` | Declarative loop entries (Loop Engineering five-move shape: `discovery`/`handoff`/`verification`/`persistence`/`scheduling`, all required, plus optional `human_checkpoint`/`budget_caps` and optional metadata `role_card`/`economics`/`skills`); parse/validate/surface only, no runtime enforcement yet. See `docs/superpowers/specs/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md`. |
   ```

2. **Verify by inspection** (no automated test covers README prose):

   ```bash
   grep -in "loops\|schema_version.*inert" README.md
   ```

   Expected: both the new `loops` row and the corrected `schema_version` row appear.

3. **Commit:**

   ```bash
   git add README.md
   git commit -m "docs(readme): adapter.yaml loops row, fix stale schema_version description (#301)"
   ```

## Task 8: Full suite + acceptance-criteria sweep

**Files:** none (verification only)

### Steps

1. **Run the full test suite:**

   ```bash
   python -m pytest tests/ -v
   ```

   Expected: exit code 0, 0 failed. This is the test portion of CI's gate per
   `CLAUDE.md`'s Conventions section (`python -m pytest tests/ -v`); CI additionally
   runs `smoke_gate.sh` and the workflow DAG checks, unaffected here since this change
   touches no scheduler/workflow files.

2. **Spot-check the stale-comment fix landed** (spec asked for it "in passing" — verify
   it's not forgotten):

   ```bash
   grep -n "docs/archive/2026-07-07-adapter-schema-v2-loops-design.md" scripts/factory_core/adapter.py
   ```

   Expected: one match, inside the `load()` comment block.

3. **Confirm README landed:**

   ```bash
   grep -n "| \`loops\` |" README.md
   ```

   Expected: one match.

4. **Sweep the spec's acceptance-criteria checklist** — every bullet in the spec's
   "Acceptance criteria" section maps to a test added in Tasks 1-6: five-move
   parse/reject (Task 1), block presence/mapping/null-value/unknown-field/field-type
   errors (Task 1), flat-A1 rejection (Task 1), `adapter.get()` verbatim passthrough
   (Task 1), metadata block validation including `context_offload_required`/
   `max_tokens` bool exclusions (Tasks 3, 5), `role_card`/`economics`/`skills`
   empty-dict semantics (Task 3), R3 tool-field exclusion (Task 4), R4
   conditional-requiredness at 4/5/6 and pass-through at 3 (Task 5), the "every
   optional field at once" entry (Task 5), R5 `contract`/`memory_intervention`
   reservation (Task 6), unchanged duplicate-name/not-a-list/not-a-mapping errors
   (untouched A1 tests, still passing per Task 1 step 5), unchanged no-`loops:` parity
   (untouched A1 tests), README table (Task 7). No gaps remain.

5. No commit — this task only verifies Tasks 1-7's commits are collectively green.

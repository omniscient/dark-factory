# Implementation Plan: Adapter schema v2 — validated `loops:` declaration block (A1)

**Issue:** #195 · **Epic:** #194 · **Spec:** [docs/superpowers/specs/2026-07-07-adapter-schema-v2-loops-design.md](../specs/2026-07-07-adapter-schema-v2-loops-design.md)
**Status:** plan-pending-review

## Goal

Extend `.factory/adapter.yaml` to schema v2 with a validated `loops:` declaration block:
each entry declares a named, bounded agentic loop (trigger, inputs/outputs, artifacts,
verifier/stop-condition/handoff references, failure behavior, side-effect level),
structurally validated with hand-rolled Python (no new dependency), surfaced in the run
record for provenance. v1 adapters and repos with no adapter file stay byte-identical to
today. The dead reserved top-level keys `repo`/`board`/`labels` are removed. Two
reserved-but-unshipped names (`loops[].memory_intervention` → epic #241,
top-level `mechanism_candidates` → a future design ticket) get targeted rejection errors
instead of silent accept-or-warn. Loops are parsed and validated only — no execution,
verification, or enforcement (that's epics #196–#199, A2–A5).

## Architecture

- `scripts/factory_core/adapter_defaults.py` gains one additive default key: `"loops": []`.
- `scripts/factory_core/adapter.py`:
  - `_KNOWN_TOP` drops `repo`/`board`/`labels`, gains `loops`.
  - `_MAP_KEYS` drops `board`/`labels` (`repo` was never in it).
  - New constants: `_LOOP_REQUIRED_FIELDS`, `_LOOP_STRING_FIELDS`, `_LOOP_LIST_FIELDS`,
    `_RESERVED_LOOP_FIELDS`, `_RESERVED_TOP_FIELDS`.
  - New `_validate_loop(entry, index)` helper raising `AdapterError` with actionable,
    loop-indexed/named messages.
  - `load()` calls `_validate_loop` for every `loops:` entry before `_deep_merge`, and
    consults `_RESERVED_TOP_FIELDS` before the existing unknown-top-level-key warning.
- `scripts/factory_core/run_record.py`: `cmd_assemble()` gains a `--clone-dir` argument
  and a fail-open read of `adapter.get(clone_dir, "loops")`, surfaced as
  `run_record["loops"]`.
- `entrypoint.sh`: the existing `run-record assemble` invocation gains
  `--clone-dir "$CLONE_DIR"`.
- No changes to `_deep_merge` — list-valued overrides already fully replace the base list.
- No new dependency (`jsonschema` or otherwise); validation is plain `isinstance`/
  key-membership checks in the same `AdapterError`-raising style `adapter.py` already uses.

## Tech Stack

Python 3 (stdlib `argparse`/`copy`/`os`/`sys` + `pyyaml`, the project's sole pinned
dependency for this tooling), `pytest` for tests. No frontend/backend framework surface —
this is entirely within `factory_core`.

## File Structure

| Path | Change |
|---|---|
| `scripts/factory_core/adapter_defaults.py` | Add `"loops": []` to `DEFAULTS` |
| `scripts/factory_core/adapter.py` | New constants, `_validate_loop()`, reserved-key checks, `_KNOWN_TOP`/`_MAP_KEYS` edits |
| `scripts/factory_core/run_record.py` | `--clone-dir` arg on `assemble` subparser, fail-open `loops` read in `cmd_assemble()` |
| `entrypoint.sh` | Add `--clone-dir "$CLONE_DIR"` to the `run-record assemble` call |
| `tests/test_adapter.py` | New coverage per spec's test list |
| `tests/test_run_record.py` | `_AssembleArgs.clone_dir` default + new `cmd_assemble()` coverage |

All commands below run from the repo root (`/workspace/dark-factory`) on the
`refine/issue-195-feat-adapter---schema-v2-----validated-s` branch, which the
implementation phase continues on directly (per this command's own working branch).

---

## Task 1: Additive `loops: []` default in `adapter_defaults.py`

**Files:** `scripts/factory_core/adapter_defaults.py`, `tests/test_adapter.py`

Satisfies spec Requirement 6 (parity — additive-only change) and the
`adapter_defaults.DEFAULTS["loops"] == []` architecture note.

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   def test_loops_default_is_empty_list(tmp_path):
       """Absent adapter file merges to loops: [] (additive parity default)."""
       merged = adapter.load(str(tmp_path))
       assert merged["loops"] == []


   def test_schema_version_1_without_loops_merges_to_empty_list(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text("schema_version: 1\n")
       merged = adapter.load(str(tmp_path))
       assert merged["loops"] == []
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k "loops_default_is_empty_list or schema_version_1_without_loops" -v
   ```

   Expected: `KeyError: 'loops'` (or `AssertionError` if `.get` were used) — `DEFAULTS` has
   no `"loops"` key yet.

3. **Implement** — edit `scripts/factory_core/adapter_defaults.py`:

   ```python
   "deconflict": {
       "models_init": "backend/app/models/__init__.py",
       "migrations_dir": "alembic/versions/",
   },
   "loops": [],
   }
   ```

   (Add the `"loops": []` line as the last entry of `DEFAULTS`, right after the existing
   `"deconflict"` block, before the closing `}`.)

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k "loops_default_is_empty_list or schema_version_1_without_loops" -v
   ```

   Expected: `2 passed`.

5. **Run full existing suite to confirm no regression:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all prior tests (`test_no_adapter_file_returns_defaults`,
   `test_adapter_overrides_deep_merge`, etc.) still pass — `test_no_adapter_file_returns_defaults`
   compares `merged == adapter_defaults.DEFAULTS`, which still holds since both sides now
   include `"loops": []`.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter_defaults.py tests/test_adapter.py
   git commit -m "feat(adapter): add additive loops: [] default (#195)"
   ```

---

## Task 2: Remove dead `repo`/`board`/`labels` reserved keys

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

Satisfies spec Requirement 7.

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   def test_repo_board_labels_now_warn_not_error(tmp_path, capsys):
       """repo/board/labels are no longer known keys — they fall through to the
       generic unknown-top-level-key warn-and-carry path, not AdapterError."""
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(
           "repo: 'org/name'\nboard: 'Project X'\nlabels: ['a', 'b']\n")
       merged = adapter.load(str(tmp_path))
       assert merged["repo"] == "org/name"
       assert merged["board"] == "Project X"
       assert merged["labels"] == ["a", "b"]
       err = capsys.readouterr().err
       assert "unknown adapter key 'repo'" in err
       assert "unknown adapter key 'board'" in err
       assert "unknown adapter key 'labels'" in err
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k test_repo_board_labels_now_warn_not_error -v
   ```

   Expected: `AssertionError` — today `board`/`labels` are in `_MAP_KEYS`, so
   `board: 'Project X'` (a string, not a mapping) raises `AdapterError` instead of
   merging; no "unknown adapter key" warning is emitted for any of the three.

3. **Implement** — edit `scripts/factory_core/adapter.py`:

   ```python
   _KNOWN_TOP = {"schema_version", "components", "safety", "memory_routing", "deconflict",
                 "token_optimization"}
   _MAP_KEYS = {"components", "safety", "memory_routing", "deconflict", "token_optimization"}
   ```

   (Replaces the existing `_KNOWN_TOP`/`_MAP_KEYS` definitions — `loops` is added to
   `_KNOWN_TOP` in Task 3, not here, to keep this task's diff scoped to the removal.)

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k test_repo_board_labels_now_warn_not_error -v
   ```

   Expected: `1 passed`.

5. **Run full suite:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all tests pass, including `test_unknown_keys_warn_not_fail` (uses
   `future_feature`, unaffected by this change).

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "fix(adapter): remove dead repo/board/labels reserved keys (#195)"
   ```

---

## Task 3: `loops:` schema constants + `_validate_loop()` happy path + wiring

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

Satisfies spec Requirements 1, 2, 4 and the "valid `loops:` entry parses" acceptance
criterion.

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   _VALID_LOOP_ENTRY = """
   loops:
     - name: nightly-scan-triage
       purpose: Triage overnight scanner false positives
       trigger: 'cron:0 6 * * *'
       inputs: ["scanner_output.json"]
       outputs: ["triage_report.md"]
       artifacts: [".factory/state/triage.json"]
       verifier: verifiers/triage_verifier.py
       stop_condition: stop_conditions/triage_stop.py
       failure_behavior: escalate_to_human
       side_effect_level: 2
       handoff: handoffs/triage_handoff.py
   """


   def test_valid_loop_entry_parses(tmp_path, capsys):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
       merged = adapter.load(str(tmp_path))
       assert len(merged["loops"]) == 1
       assert merged["loops"][0]["name"] == "nightly-scan-triage"
       assert merged["loops"][0]["side_effect_level"] == 2
       assert "unknown adapter key 'loops'" not in capsys.readouterr().err


   def test_loops_independent_of_schema_version(tmp_path):
       """A schema_version: 1 file with a valid loops: entry still parses —
       schema_version never gates loops:."""
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text("schema_version: 1\n" + _VALID_LOOP_ENTRY)
       merged = adapter.load(str(tmp_path))
       assert len(merged["loops"]) == 1
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k "valid_loop_entry_parses or loops_independent_of_schema_version" -v
   ```

   Expected: `test_valid_loop_entry_parses` fails on the last assertion —
   `merged["loops"][0]["name"]` already resolves correctly today (an unvalidated list
   value merges straight through `_deep_merge`, since `loops` isn't in `_MAP_KEYS`
   either), but `load()` still prints `adapter: warning — unknown adapter key 'loops'`
   to stderr because `loops` is not yet in `_KNOWN_TOP`, so the `capsys` assertion fails.
   `test_loops_independent_of_schema_version` fails the same way.

3. **Implement** — edit `scripts/factory_core/adapter.py`. First, update `_KNOWN_TOP` to
   include `loops` (continuing from Task 2's edit):

   ```python
   _KNOWN_TOP = {"schema_version", "components", "safety", "memory_routing", "deconflict",
                 "token_optimization", "loops"}
   _MAP_KEYS = {"components", "safety", "memory_routing", "deconflict", "token_optimization"}

   _LOOP_REQUIRED_FIELDS = {
       "name", "purpose", "trigger", "inputs", "outputs", "artifacts",
       "verifier", "stop_condition", "failure_behavior", "side_effect_level", "handoff",
   }
   _LOOP_STRING_FIELDS = {
       "name", "purpose", "trigger", "verifier", "stop_condition",
       "failure_behavior", "handoff",
   }
   _LOOP_LIST_FIELDS = {"inputs", "outputs", "artifacts"}


   def _validate_loop(entry, index: int) -> None:
       if not isinstance(entry, dict):
           raise AdapterError(f"loops[{index}] must be a mapping, got {type(entry).__name__}")
       name = entry.get("name", "?")
       for field in _LOOP_REQUIRED_FIELDS:
           if field not in entry:
               raise AdapterError(f"loops[{index}] ('{name}'): missing required field '{field}'")
       for field in _LOOP_STRING_FIELDS:
           val = entry[field]
           if not isinstance(val, str) or not val:
               raise AdapterError(
                   f"loops[{index}] ('{name}'): field '{field}' must be a non-empty string")
       for field in _LOOP_LIST_FIELDS:
           val = entry[field]
           if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
               raise AdapterError(
                   f"loops[{index}] ('{name}'): field '{field}' must be a list of strings")
       sel = entry["side_effect_level"]
       if not isinstance(sel, int) or not (1 <= sel <= 6):
           raise AdapterError(
               f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int between 1 and 6")
   ```

   Then wire it into `load()`, after the existing per-top-level-key loop and before the
   `return _deep_merge(...)` line:

   ```python
       for k, v in data.items():
           if k not in _KNOWN_TOP:
               print(f"adapter: warning — unknown adapter key '{k}' (carried through)", file=sys.stderr)
           if k in _MAP_KEYS and not isinstance(v, dict):
               raise AdapterError(f"adapter key '{k}' must be a mapping, got {type(v).__name__}")
       if "loops" in data:
           if not isinstance(data["loops"], list):
               raise AdapterError(f"adapter key 'loops' must be a list, got {type(data['loops']).__name__}")
           for i, entry in enumerate(data["loops"]):
               _validate_loop(entry, i)
       return _deep_merge(adapter_defaults.DEFAULTS, data)
   ```

   (Note: unknown-field and reserved-field checks inside `_validate_loop` are added in
   Tasks 4 and 6 — this step only wires required-field/type validation and the happy
   path, so `_validate_loop` above is intentionally incomplete pending those tasks.)

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k "valid_loop_entry_parses or loops_independent_of_schema_version" -v
   ```

   Expected: `2 passed`, no stderr warning about `loops` being unknown.

5. **Run full suite:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all tests pass.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(adapter): validate loops: entries (required fields, types) (#195)"
   ```

---

## Task 4: `_validate_loop()` — not-a-mapping / missing-field / unknown-field errors

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

Satisfies spec Requirement 3 (unknown-field strictness scoped to `loops:` entries) and
Requirement 5 (no regression to top-level warn-and-carry).

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   def test_loop_entry_not_a_mapping_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text("loops:\n  - 'not-a-mapping'\n")
       with pytest.raises(adapter.AdapterError, match=r"loops\[0\] must be a mapping"):
           adapter.load(str(tmp_path))


   def test_loops_not_a_list_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text("loops:\n  name: not-a-list\n")
       with pytest.raises(adapter.AdapterError, match=r"loops.*must be a list"):
           adapter.load(str(tmp_path))


   def test_loop_entry_missing_required_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       entry = _VALID_LOOP_ENTRY.replace("      purpose: Triage overnight scanner false positives\n", "")
       (d / "adapter.yaml").write_text(entry)
       with pytest.raises(adapter.AdapterError, match=r"missing required field 'purpose'"):
           adapter.load(str(tmp_path))


   def test_loop_entry_unknown_field_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       entry = _VALID_LOOP_ENTRY + "      extra_typo_field: oops\n"
       (d / "adapter.yaml").write_text(entry)
       with pytest.raises(adapter.AdapterError, match=r"unknown field 'extra_typo_field'"):
           adapter.load(str(tmp_path))
   ```

   The pre-existing `test_unknown_keys_warn_not_fail` (line ~37, using the non-reserved
   key `future_feature`) already guards the top-level warn-and-carry path and needs no
   change here — it stays green through this task untouched.

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k "not_a_mapping_raises or not_a_list_raises or missing_required_field_raises or unknown_field_raises" -v
   ```

   Expected: `test_loop_entry_not_a_mapping_raises`, `test_loops_not_a_list_raises`, and
   `test_loop_entry_missing_required_field_raises` pass already (Task 3 covers these);
   `test_loop_entry_unknown_field_raises` fails — `_validate_loop` has no unknown-field
   check yet, so `extra_typo_field` is silently accepted.

3. **Implement** — edit `scripts/factory_core/adapter.py`, inside `_validate_loop`, add
   the unknown-field loop immediately after the `name = entry.get("name", "?")` line and
   before the missing-required-field loop:

   ```python
       name = entry.get("name", "?")
       for key in entry:
           if key not in _LOOP_REQUIRED_FIELDS:
               raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")
       for field in _LOOP_REQUIRED_FIELDS:
   ```

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k "not_a_mapping_raises or not_a_list_raises or missing_required_field_raises or unknown_field_raises or unknown_keys_warn_not_fail" -v
   ```

   Expected: `4 passed` (the `unknown_keys_warn_not_fail` filter matches the pre-existing
   test, confirming it's unaffected).

5. **Run full suite:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all pass.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(adapter): reject unknown fields inside loops: entries (#195)"
   ```

---

## Task 5: `_validate_loop()` — field-type validation coverage

**Files:** `tests/test_adapter.py` (implementation already lands in Task 3 — this task is
test-only, closing out the remaining spec-mandated type-check assertions)

Satisfies the spec's "wrong type per field group" and `side_effect_level` range test
coverage.

### Steps

1. **Write failing-then-passing test** (implementation already exists from Task 3 — write
   the test and confirm it passes immediately, per TDD-for-already-implemented-behavior;
   if any of these fail, that indicates a Task 3 implementation gap to fix here). Add
   `import re` and `import yaml` to the top of `tests/test_adapter.py` alongside the
   existing imports, then append:

   ```python
   @pytest.mark.parametrize("field", sorted(adapter._LOOP_STRING_FIELDS))
   def test_loop_entry_string_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][field] = 42
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=re.escape(f"field '{field}' must be a non-empty string")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("field", sorted(adapter._LOOP_LIST_FIELDS))
   def test_loop_entry_list_field_wrong_type_raises(tmp_path, field):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0][field] = "not-a-list"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=re.escape(f"field '{field}' must be a list of strings")):
           adapter.load(str(tmp_path))


   @pytest.mark.parametrize("bad_level", [0, 7, -1, 100])
   def test_loop_entry_side_effect_level_out_of_range_raises(tmp_path, bad_level):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = bad_level
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
           adapter.load(str(tmp_path))


   def test_loop_entry_side_effect_level_non_int_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = "two"
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
           adapter.load(str(tmp_path))
   ```

2. **Verify it fails (if it does):**

   ```bash
   python -m pytest tests/test_adapter.py -k "wrong_type_raises or side_effect_level" -v
   ```

   Expected: all pass immediately, since Task 3 already implements the type checks —
   this step is a confirmation run, not a red-then-green cycle. If any fail, fix
   `_validate_loop` in `scripts/factory_core/adapter.py` to match (e.g. an off-by-one in
   the range check) before proceeding.

3. **N/A — no implementation step** (covered by Task 3).

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all tests pass, including the new parametrized cases (`-v` will list each
   `field`/`bad_level` variant).

5. **Commit:**

   ```bash
   git add tests/test_adapter.py
   git commit -m "test(adapter): cover loops: field-type and side_effect_level range validation (#195)"
   ```

---

## Task 6: Reserved loop-entry field `memory_intervention` → epic #241

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

Satisfies spec Requirement 3a.

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   def test_loop_entry_memory_intervention_reserved_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["memory_intervention"] = {"policy": "whatever"}
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(adapter.AdapterError, match=r"reserved for epic #241"):
           adapter.load(str(tmp_path))
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k memory_intervention_reserved -v
   ```

   Expected: fails — today `memory_intervention` hits the generic "unknown field" branch
   from Task 4, whose message doesn't contain "reserved for epic #241".

3. **Implement** — edit `scripts/factory_core/adapter.py`. Add the reserved-field
   constant near the other `_LOOP_*` constants:

   ```python
   # Per-loop-entry field names reserved for a tracked-but-unshipped extension.
   # Rejected with a targeted message so the extension point is discoverable
   # without A1 accepting unvalidated content. Consulted before the generic
   # unknown-field error in _validate_loop.
   _RESERVED_LOOP_FIELDS = {"memory_intervention": "#241"}
   ```

   Then update the unknown-field loop inside `_validate_loop` (added in Task 4) to check
   `_RESERVED_LOOP_FIELDS` first:

   ```python
       for key in entry:
           if key not in _LOOP_REQUIRED_FIELDS:
               if key in _RESERVED_LOOP_FIELDS:
                   raise AdapterError(
                       f"loops[{index}] ('{name}'): field '{key}' is reserved for epic "
                       f"{_RESERVED_LOOP_FIELDS[key]} (per-loop memory intervention) and is "
                       f"not accepted in schema v2; remove it"
                   )
               raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")
   ```

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k memory_intervention_reserved -v
   ```

   Expected: `1 passed`.

5. **Run full suite** (confirm `test_loop_entry_unknown_field_raises` from Task 4 still
   passes — `extra_typo_field` is not in `_RESERVED_LOOP_FIELDS`, so it still hits the
   generic branch):

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all pass.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(adapter): reserve loops[].memory_intervention for epic #241 (#195)"
   ```

---

## Task 7: Reserved top-level field `mechanism_candidates`

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

Satisfies spec Requirement 5a.

### Steps

1. **Write failing test** — append to `tests/test_adapter.py`:

   ```python
   def test_mechanism_candidates_top_level_reserved_raises(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       (d / "adapter.yaml").write_text(
           "mechanism_candidates:\n  - id: mc-1\n    target_loop: nightly-scan-triage\n")
       with pytest.raises(adapter.AdapterError, match=r"mechanism_candidates.*reserved"):
           adapter.load(str(tmp_path))
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_adapter.py -k mechanism_candidates_top_level_reserved -v
   ```

   Expected: fails — today `mechanism_candidates` hits the generic top-level
   warn-and-carry path (prints a warning, merges through, no `AdapterError`).

3. **Implement** — edit `scripts/factory_core/adapter.py`. Add the reserved top-level
   constant near `_RESERVED_LOOP_FIELDS`:

   ```python
   # Top-level key names reserved for a tracked future design ticket. Unlike a
   # generic unknown top-level key (which warns and carries — v1 parity), a named
   # reserved key is hard-rejected: it has no v1 history, so strictness here is
   # parity-safe, and warn-and-carry would deep-merge unvalidated content into config.
   _RESERVED_TOP_FIELDS = {
       "mechanism_candidates": "a future Bilevel Autoresearch design ticket",
   }
   ```

   Then update `load()`'s per-top-level-key loop to check `_RESERVED_TOP_FIELDS` before
   the `k not in _KNOWN_TOP` warning:

   ```python
       for k, v in data.items():
           if k in _RESERVED_TOP_FIELDS:
               raise AdapterError(
                   f"adapter key '{k}' is reserved for {_RESERVED_TOP_FIELDS[k]} and is "
                   f"not accepted in schema v2; remove it"
               )
           if k not in _KNOWN_TOP:
               print(f"adapter: warning — unknown adapter key '{k}' (carried through)", file=sys.stderr)
           if k in _MAP_KEYS and not isinstance(v, dict):
               raise AdapterError(f"adapter key '{k}' must be a mapping, got {type(v).__name__}")
   ```

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_adapter.py -k mechanism_candidates_top_level_reserved -v
   ```

   Expected: `1 passed`.

5. **Run full suite** (confirm `test_unknown_keys_warn_not_fail` and
   `test_repo_board_labels_now_warn_not_error` from Task 2 still pass — none of
   `future_feature`/`repo`/`board`/`labels` are in `_RESERVED_TOP_FIELDS`):

   ```bash
   python -m pytest tests/test_adapter.py -v
   ```

   Expected: all pass — this is the full `test_adapter.py` suite, now including every
   case from the spec's "new coverage" list.

6. **Commit:**

   ```bash
   git add scripts/factory_core/adapter.py tests/test_adapter.py
   git commit -m "feat(adapter): reserve top-level mechanism_candidates key (#195)"
   ```

---

## Task 8: `run_record.py` — `--clone-dir` and fail-open `loops` surfacing

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

Satisfies spec Requirement 8.

### Steps

1. **Write failing test** — append to `tests/test_run_record.py`:

   ```python
   def test_assemble_surfaces_loops_from_adapter(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)

       clone_dir = tmp_path / "clone"
       (clone_dir / ".factory").mkdir(parents=True)
       (clone_dir / ".factory" / "adapter.yaml").write_text(
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

       artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
       out = tmp_path / "run-record.json"
       args = _AssembleArgs(artifacts_dir, out)
       args.clone_dir = str(clone_dir)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       assert rec["loops"] == [{
           "name": "nightly-scan-triage",
           "purpose": "Triage overnight scanner false positives",
           "trigger": "cron:0 6 * * *",
           "inputs": [], "outputs": [], "artifacts": [],
           "verifier": "verifiers/triage_verifier.py",
           "stop_condition": "stop_conditions/triage_stop.py",
           "failure_behavior": "escalate_to_human",
           "side_effect_level": 2,
           "handoff": "handoffs/triage_handoff.py",
       }]


   def test_assemble_no_adapter_file_loops_empty(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)

       clone_dir = tmp_path / "clone"; clone_dir.mkdir()
       artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
       out = tmp_path / "run-record.json"
       args = _AssembleArgs(artifacts_dir, out)
       args.clone_dir = str(clone_dir)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       assert rec["loops"] == []


   def test_assemble_malformed_adapter_loops_empty_fail_open(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)

       clone_dir = tmp_path / "clone"
       (clone_dir / ".factory").mkdir(parents=True)
       (clone_dir / ".factory" / "adapter.yaml").write_text("{broken: [")

       artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
       out = tmp_path / "run-record.json"
       args = _AssembleArgs(artifacts_dir, out)
       args.clone_dir = str(clone_dir)
       rr.cmd_assemble(args)  # must not raise

       rec = json.loads(out.read_text())
       assert rec["loops"] == []


   def test_assemble_default_clone_dir_when_unset(tmp_path, monkeypatch):
       """_AssembleArgs instances that don't set clone_dir explicitly still work
       (class attribute default '.') — existing tests in this file rely on this."""
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)

       artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
       out = tmp_path / "run-record.json"
       args = _AssembleArgs(artifacts_dir, out)
       rr.cmd_assemble(args)  # must not raise even without explicit clone_dir

       rec = json.loads(out.read_text())
       assert "loops" in rec
   ```

2. **Verify it fails:**

   ```bash
   python -m pytest tests/test_run_record.py -k "surfaces_loops or no_adapter_file_loops_empty or malformed_adapter_loops_empty or default_clone_dir_when_unset" -v
   ```

   Expected: all four fail with `KeyError: 'loops'` on `rec["loops"]` — setting
   `args.clone_dir` as an instance attribute succeeds (Python allows it even though the
   class doesn't declare it), but `cmd_assemble()` doesn't read `args.clone_dir` or
   populate `run_record["loops"]` yet, so the key is simply absent from the output JSON.

3. **Implement:**

   a. Edit `scripts/factory_core/run_record.py` — add the `--clone-dir` argument to the
      `assemble` subparser in `main()`:

      ```python
          a = sub.add_parser("assemble", help="Assemble end-of-run record from artifacts")
          a.add_argument("--run-id", required=True)
          a.add_argument("--issue", type=int, required=True)
          a.add_argument("--intent", required=True)
          a.add_argument("--started-at", default="")
          a.add_argument("--artifacts-dir", required=True)
          a.add_argument("--archon-cost-json")
          a.add_argument("--out-file", required=True)
          a.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
      ```

   b. In `cmd_assemble()`, add the fail-open `loops` read alongside the existing
      `nodes`/`totals` construction, and add `"loops": loops` to the `run_record` dict:

      ```python
      def cmd_assemble(args) -> None:
          artifacts_dir = pathlib.Path(args.artifacts_dir)
          out_file = pathlib.Path(args.out_file)

          stages = []
          artifacts: dict = {}
          artifact_names = ["validation", "conformance", "review", "conflict_resolution"]

          for name in artifact_names:
              md_path = artifacts_dir / f"{name}.md"
              if md_path.exists():
                  content = md_path.read_text(encoding="utf-8")
                  artifacts[name] = content
                  stage = _parse_artifact_stage(name, content)
                  if stage:
                      stages.append(stage)

          archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
          nodes = _parse_archon_cost(archon_path)

          totals_in = sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes)
          totals_out = sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes)
          totals_cost = sum(n.get("cost_usd", 0) for n in nodes)

          from . import adapter
          try:
              loops = adapter.get(args.clone_dir, "loops") or []
          except Exception:
              loops = []

          run_record = {
              "run_id": args.run_id,
              "issue_number": args.issue,
              "intent": args.intent,
              "started_at": args.started_at or _timestamp(),
              "completed_at": _timestamp(),
              "status": "completed",
              "stages": stages,
              "nodes": nodes,
              "artifacts": artifacts,
              "loops": loops,
              "totals": {
                  "gen_ai.usage.input_tokens": totals_in,
                  "gen_ai.usage.output_tokens": totals_out,
                  "cost_usd": totals_cost,
              },
          }
      ```

      (Only the `from . import adapter` block, the `try/except` assigning `loops`, and
      the `"loops": loops,` line inside the `run_record` dict literal are new — the rest
      of the function body is unchanged and shown here only for placement context.)

   c. Edit `tests/test_run_record.py` — add a `clone_dir` class attribute default to
      `_AssembleArgs` so existing call sites that don't set it explicitly keep working:

      ```python
      class _AssembleArgs:
          run_id = "abc123"
          issue = 333
          intent = "new"
          started_at = "2026-06-12T04:00:00Z"
          archon_cost_json = None
          clone_dir = "."

          def __init__(self, artifacts_dir, out_file):
              self.artifacts_dir = str(artifacts_dir)
              self.out_file = str(out_file)
      ```

4. **Verify it passes:**

   ```bash
   python -m pytest tests/test_run_record.py -k "surfaces_loops or no_adapter_file_loops_empty or malformed_adapter_loops_empty or default_clone_dir_when_unset" -v
   ```

   Expected: `4 passed`.

5. **Run full suite:**

   ```bash
   python -m pytest tests/test_run_record.py -v
   python -m pytest tests/ -v
   ```

   Expected: all tests across both files pass — `test_assemble_builds_run_record` and
   other pre-existing `cmd_assemble` tests still pass since `clone_dir` now defaults to
   `"."`, which resolves to the process's real CWD (the repo root, when pytest is run as
   shown). This repo's own `.factory/adapter.yaml` exists there (`schema_version: 1`, no
   `loops:` key — the dark-factory self-target adapter), so `adapter.get(".", "loops")`
   merges it over `adapter_defaults.DEFAULTS` and still resolves to `loops: []` via the
   additive parity default from Task 1; `rec["loops"] == []` holds for every pre-existing
   test that doesn't set `clone_dir`, regardless of whether a real adapter file is present
   at CWD.

6. **Commit:**

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(run-record): surface adapter loops: in run record, fail-open (#195)"
   ```

---

## Task 9: Wire `--clone-dir` through `entrypoint.sh`

**Files:** `entrypoint.sh`

Satisfies the remaining half of spec Requirement 8 (production wiring, no test — this is
a plain shell CLI-flag addition to an already-`|| true`-guarded call).

### Steps

1. **Confirm current invocation** (no test framework covers `entrypoint.sh` directly —
   this step is a manual read-verify, not a pytest run):

   ```bash
   grep -n -A8 "run-record assemble" entrypoint.sh
   ```

   Expected output includes the existing flags without `--clone-dir`:
   ```
   python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
     --run-id "${RUN_ID:-unknown}" \
     --issue "$ISSUE_NUM" \
     --intent "$INTENT" \
     --started-at "${RUN_STARTED_AT:-}" \
     --artifacts-dir "$ARTIFACTS_DIR" \
     --archon-cost-json "$ARCHON_COST_JSON" \
     --out-file "$ARTIFACTS_DIR/run-record.json" || true
   ```

2. **Implement** — edit `entrypoint.sh`, adding `--clone-dir "$CLONE_DIR"` as a new line
   before `--out-file` (order among flags doesn't matter to `argparse`, but keeping
   `--out-file`/`|| true` last preserves the existing diff shape):

   ```bash
   python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
     --run-id "${RUN_ID:-unknown}" \
     --issue "$ISSUE_NUM" \
     --intent "$INTENT" \
     --started-at "${RUN_STARTED_AT:-}" \
     --artifacts-dir "$ARTIFACTS_DIR" \
     --archon-cost-json "$ARCHON_COST_JSON" \
     --out-file "$ARTIFACTS_DIR/run-record.json" \
     --clone-dir "$CLONE_DIR" || true
   ```

   `$CLONE_DIR` is already in scope at this point in the script (set at the top:
   `CLONE_DIR="$FACTORY_CLONE_DIR"`, line 9).

3. **Verify:**

   ```bash
   grep -n -A9 "run-record assemble" entrypoint.sh
   bash -n entrypoint.sh
   ```

   Expected: the grep shows `--clone-dir "$CLONE_DIR"` as a new line before `|| true`;
   `bash -n` (syntax check only, no execution) exits `0` with no output.

4. **Run the full Python test suite as a final regression pass for this ticket:**

   ```bash
   python -m pytest tests/ -v
   ```

   Expected: every test in `tests/test_adapter.py` and `tests/test_run_record.py` passes,
   plus the full pre-existing suite (`tests/` covers more than these two files) shows no
   regressions.

5. **Commit:**

   ```bash
   git add entrypoint.sh
   git commit -m "feat(entrypoint): pass --clone-dir to run-record assemble (#195)"
   ```

---

## Acceptance Criteria Traceability

| Acceptance criterion | Covered by |
|---|---|
| A v2 adapter with a valid `loops:` entry parses; invalid entries fail with actionable errors | Tasks 3, 4, 5 |
| A v1 adapter and an absent adapter behave byte-identically to today | Tasks 1, 2 (existing suite re-run each task) |
| `repo`/`board`/`labels` are either functional or gone | Task 2 (gone) |
| Loop declarations appear in the run record for provenance | Task 8 |
| (Spec addendum) Reserved-key mechanism for `memory_intervention`/`mechanism_candidates` | Tasks 6, 7 |
| `entrypoint.sh` wiring | Task 9 |

## Out of Scope (per spec)

- Executing, verifying, or enforcing declared loops (A2–A5, epics #196–#199).
- `role_card`, five-move restructuring, `skills`, `economics`, conditional-requiredness
  (deferred to a follow-up ticket, "A1.5" — not filed by this plan; filing it is a
  maintainer/scheduler action outside this command's file-output scope).
- Any `role_card.allowed_tools`/`forbidden_tools`-style tool permission surface —
  excluded outright per `CLAUDE.md`'s Trusted comment channels security-surface carve-out.

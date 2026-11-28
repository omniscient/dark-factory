# Prompt Cache-Shape Contract (Phase-Command Lint) Implementation Plan

**Issue:** #236

> **For agentic workers:** execute task by task, in order. Each step is one small action.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, stdlib-only lint
(`scripts/check_command_cache_shape.py`) that defines and enforces the stable-prefix /
volatile-tail contract for `commands/*.md`. R1 forbids the brace form of any
Archon-substituted name. R2 ratchets each file's first volatile line against a committed
baseline. R3 keeps that baseline complete. `--json` emits static prompt-shape metadata. The
plan also wires the lint into CI's `dag-check` job and documents the contract, the
measurement procedure and rollback in `docs/prompt-cache-shape.md`.

**Spec:** `docs/superpowers/specs/2026-11-28-prompt-cache-shape-contract-design.md`
(operator-approved at `092cc0c`).

**Architecture:** A single new script has the same shape as `scripts/check_workflow_when.py`:
`check(commands_dir, baseline_path=None) -> list[str]` (empty = pass) and `main(argv) -> int`,
plus a `shape(path) -> dict` metadata function. Its token regexes copy Archon's
`substituteWorkflowVariables()` / `substituteNodeOutputRefs()` exactly, pinned at Archon commit
`74372446` (the commit `Dockerfile` pins). They are module constants. The baseline is a
committed JSON object that maps each command stem to its first-volatile line. Everything is
static (a function of the committed files): no per-run artifact, no DAG node, no change to the
budget pipeline.

**Tech Stack:** Python 3.12 standard library only (`argparse`, `hashlib`, `json`, `re`,
`pathlib`). The CI `dag-check` job installs only `pyyaml`. Tests use pytest's `tmp_path` and
`capsys`.

**Hard scope rules (from the spec and CLAUDE.md):**
- **No `commands/*.md` change.** Acceptance criterion 2: `git diff origin/main --stat -- commands/` is empty.
  This includes the known `$ISSUE_CONTEXT` defect at `commands/dark-factory-refine.md:122`,
  which is recorded and **not** fixed here.
- Do not touch `scripts/context_budget.py`, `scripts/budget_gate.sh`, `budget_enforce.py`, any
  `gate_*`, the breaker, `config/config.yaml`, tool allow/deny lists, `.claude/skills/**`,
  `deploy/**` or `.github/workflows/publish.yml`.
- Do not edit `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` or
  `docs/configuration.md`. That holds even though the `features.md` CI row says "the two
  workflow DAG checks": the spec forbids edits to those four range-checked files.
- **Never** rewrite `$ARTIFACTS_DIR` as `${ARTIFACTS_DIR}` anywhere in `commands/`. The brace
  form resolves to the entrypoint's directory, not the workflow run's. That is the whole point
  of R1.
- `.github/workflows/ci.yml` gets exactly one added line, and nothing else changes there.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/check_command_cache_shape.py` | new | Vocabulary constants, `shape()`, `check()` (R1–R3), `main()` / `--json` |
| `scripts/command_cache_shape_baseline.json` | new | Committed first-volatile-line floor per command stem |
| `tests/test_check_command_cache_shape.py` | new | pytest: matching, metadata, R1–R3, CLI, real tree |
| `.github/workflows/ci.yml` | +1 line | Run the lint in the `dag-check` job |
| `docs/prompt-cache-shape.md` | new | Contract, brace-form warning, rules, metadata, measurement + limitation, skills classification, rollback. No `file:line` citations |

Nothing else is created or modified.

**Verified facts at plan time and again at `origin/main` `2429106` (after #406). The token
counts are a snapshot: a later merge that edits a command body (for example #409 on
`dark-factory-plan.md`) changes them, and that is not a failure. Only the first-volatile-line
column feeds the baseline:**

| Command file | first volatile line | bare tokens |
|---|---|---|
| `ceiling-revisit.md` | 23 | ARTIFACTS_DIR×1 |
| `dark-factory-code-review.md` | 3 | ARTIFACTS_DIR×43, WORKFLOW_ID×1 |
| `dark-factory-conformance.md` | 3 | ARTIFACTS_DIR×42, WORKFLOW_ID×1 |
| `dark-factory-docs-conformance.md` | 3 | ARTIFACTS_DIR×40, WORKFLOW_ID×1 |
| `dark-factory-implement.md` | 3 | ARTIFACTS_DIR×25, WORKFLOW_ID×1 |
| `dark-factory-plan.md` | 3 | ARTIFACTS_DIR×25, WORKFLOW_ID×1 |
| `dark-factory-refine.md` | 3 | ARTIFACTS_DIR×17, WORKFLOW_ID×1, ISSUE_CONTEXT×1 |
| `dark-factory-revise-advisory.md` | 8 | WORKFLOW_ID×1, ARTIFACTS_DIR×18 |
| `dark-factory-validate.md` | 3 | ARTIFACTS_DIR×18, WORKFLOW_ID×1 |

That is 237 tokens in the 8 phase commands, plus 1 in `ceiling-revisit.md`. There are zero
brace-form occurrences and zero `$<node>.output` references.

---

## Task 0: Preflight

**Files:** none (read-only)

- [ ] **Step 1: Confirm branch and clean tree**

  Run: `git status --short && git branch --show-current`
  Expected: no tracked changes in `status` (no ` M`/`A ` lines). The branch is `feat/issue-236-…`. The spec and this
  plan are already present and committed, copied by `setup-branch` /
  `transfer_refine_artifacts.sh`. Untracked files left by an earlier workflow step would show
  here. Do not add them to any commit in this plan.

- [ ] **Step 2: Confirm the command-file inventory and no brace forms**

  Run:
  ```bash
  ls commands/*.md | wc -l
  grep -nE '\$\{(WORKFLOW_ID|ARTIFACTS_DIR|BASE_BRANCH|DOCS_DIR|USER_MESSAGE|ARGUMENTS|LOOP_USER_INPUT|REJECTION_REASON|LOOP_PREV_OUTPUT|CONTEXT|EXTERNAL_CONTEXT|ISSUE_CONTEXT)([^A-Za-z0-9_]|$)' commands/*.md | wc -l
  ```
  Expected: `9`, then `0`. If the file count differs, the baseline in Task 3 must list
  exactly the files present. Compute each value with the Task 1 script
  (`python scripts/check_command_cache_shape.py --json commands`) and do not copy the table
  above.

- [ ] **Step 3: Confirm pytest is available**

  Run: `python -m pytest --version`
  Expected: `pytest <version>`. If it is missing: `pip install pytest pyyaml`.

---

## Task 1: Token matching and `shape()` metadata

**Files:**
- Create: `tests/test_check_command_cache_shape.py`
- Create: `scripts/check_command_cache_shape.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_check_command_cache_shape.py`:

  ```python
  """Tests for check_command_cache_shape.py — the phase-command cache-shape lint (#236).

  R1 forbids the brace form of Archon-substituted names (it resolves to the wrong artifacts
  directory in a command node's shell); R2 ratchets each file's first volatile line against
  the committed baseline; R3 keeps the baseline complete. ``--json`` emits the static
  prompt-shape metadata.
  """

  import hashlib
  import json
  import sys
  from pathlib import Path

  _SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
  if str(_SCRIPTS_DIR) not in sys.path:
      sys.path.insert(0, str(_SCRIPTS_DIR))

  from check_command_cache_shape import shape  # noqa: E402

  _REPO_ROOT = Path(__file__).resolve().parents[1]
  _COMMANDS_DIR = _REPO_ROOT / "commands"


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _setup(tmp_path: Path, files: dict, baseline: dict) -> tuple[Path, Path]:
      commands = tmp_path / "commands"
      commands.mkdir()
      for name, text in files.items():
          (commands / f"{name}.md").write_bytes(text.encode("utf-8"))
      baseline_path = tmp_path / "baseline.json"
      baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
      return commands, baseline_path


  def _one(tmp_path: Path, text: str) -> Path:
      p = tmp_path / "cmd.md"
      p.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
      return p


  def _names(tmp_path: Path, text: str) -> dict:
      return {f["name"]: f["count"] for f in shape(_one(tmp_path, text))["cache_busting_fields"]}


  # ---------------------------------------------------------------------------
  # Token matching (mirrors Archon)
  # ---------------------------------------------------------------------------

  def test_plain_names_have_no_word_boundary(tmp_path):
      assert _names(tmp_path, "$ARTIFACTS_DIRX\n") == {"ARTIFACTS_DIR": 1}


  def test_context_names_have_lookahead(tmp_path):
      assert _names(tmp_path, "$CONTEXTS $ISSUE_CONTEXT_X\n") == {}


  def test_issue_context_reported_as_itself(tmp_path):
      assert _names(tmp_path, "$ISSUE_CONTEXT and $CONTEXT\n") == {"ISSUE_CONTEXT": 1, "CONTEXT": 1}


  def test_node_output_references(tmp_path):
      assert _names(tmp_path, "$plan.output then $plan.output.field\n") == {"NODE_OUTPUT": 2}


  # ---------------------------------------------------------------------------
  # Prompt-shape metadata
  # ---------------------------------------------------------------------------

  def test_prefix_hash_is_sha256_of_bytes_before_first_token(tmp_path):
      prefix = "---\ndescription: é\n"
      meta = shape(_one(tmp_path, prefix + "x $ARTIFACTS_DIR tail\n"))
      stable = (prefix + "x ").encode("utf-8")
      assert meta["prefix_hash"] == hashlib.sha256(stable).hexdigest()
      assert meta["stable_prefix_bytes"] == len(stable)
      assert meta["volatile_tail_bytes"] == len("$ARTIFACTS_DIR tail\n")
      assert meta["volatile_tail_tokens_est"] == len("$ARTIFACTS_DIR tail\n") // 4
      assert meta["first_volatile_line"] == 3


  def test_prefix_hash_ignores_tail_changes(tmp_path):
      a = shape(_one(tmp_path, "head\n$WORKFLOW_ID tail one\n"))["prefix_hash"]
      b = shape(_one(tmp_path, "head\n$WORKFLOW_ID tail two\n"))["prefix_hash"]
      assert a == b


  def test_prefix_hash_changes_with_prefix(tmp_path):
      a = shape(_one(tmp_path, "head\n$WORKFLOW_ID\n"))["prefix_hash"]
      b = shape(_one(tmp_path, "heaD\n$WORKFLOW_ID\n"))["prefix_hash"]
      assert a != b


  def test_cache_busting_fields_counts_and_first_lines(tmp_path):
      text = "a\n$WORKFLOW_ID\n$ARTIFACTS_DIR $ARTIFACTS_DIR\n$ARTIFACTS_DIR\n"
      meta = shape(_one(tmp_path, text))
      assert meta["command"] == "cmd"
      assert meta["cache_busting_fields"] == [
          {"name": "WORKFLOW_ID", "count": 1, "first_line": 2},
          {"name": "ARTIFACTS_DIR", "count": 3, "first_line": 3},
      ]


  def test_file_with_no_token_is_all_prefix(tmp_path):
      meta = shape(_one(tmp_path, "one\ntwo\n"))
      assert meta["first_volatile_line"] == 3      # line count + 1
      assert meta["volatile_tail_bytes"] == 0
      assert meta["cache_busting_fields"] == []
      assert meta["prefix_hash"] == hashlib.sha256(b"one\ntwo\n").hexdigest()
  ```

  (`_setup` and `_COMMANDS_DIR` are unused until Tasks 2–3. That is intentional, so later
  tasks only append.)

- [ ] **Step 2: Run the tests and verify they fail**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: collection error `ModuleNotFoundError: No module named 'check_command_cache_shape'`.

- [ ] **Step 3: Implement constants and `shape()`**

  Create `scripts/check_command_cache_shape.py`:

  ```python
  """Lint the prompt cache shape of Archon phase command files (#236).

  Archon reads ``commands/<name>.md`` verbatim and substitutes a fixed vocabulary of bare
  ``$NAME`` tokens (and ``$<node-id>.output`` references) across the whole text before
  sending it as a command node's first user message. The substituted values are per-run,
  so a rendered command differs between runs from its first such token onward. Provider
  prompt caching is prefix-based, so only the position of that first token matters.

  Contract (see docs/prompt-cache-shape.md):
    - stable_prefix: the bytes before the first bare token or node-output reference
    - volatile_tail: everything from that token to the end of the file
    - cache_busting_fields: the bare names in the file, with count and first line

  Rules, applied to every ``commands/*.md``:
    - R1: no brace form ``${NAME`` of a vocabulary name. Archon never substitutes it, and
      in a command node's shell it resolves to the entrypoint's ARTIFACTS_DIR, not the
      workflow run's directory, so the phase would read and write the wrong place.
    - R2: ``first_volatile_line`` must be >= the file's committed baseline (a ratchet).
    - R3: the baseline has exactly one entry per command file.

  Usage (CI)::

      python scripts/check_command_cache_shape.py commands
      python scripts/check_command_cache_shape.py --json commands   # prompt-shape metadata

  API::

      from check_command_cache_shape import check, shape
      errors = check(commands_dir)   # returns [] on success
  """

  from __future__ import annotations

  import argparse
  import hashlib
  import json
  import re
  import sys
  from pathlib import Path
  from typing import Optional, Union

  # Mirrors substituteWorkflowVariables() (packages/workflows/src/executor-shared.ts) and
  # substituteNodeOutputRefs() (packages/workflows/src/dag-executor.ts) at the Archon
  # commit the Dockerfile pins (74372446). Re-check these constants if the pin moves.
  PLAIN_NAMES = (
      "WORKFLOW_ID",
      "ARTIFACTS_DIR",
      "BASE_BRANCH",
      "DOCS_DIR",
      "USER_MESSAGE",
      "ARGUMENTS",
      "LOOP_USER_INPUT",
      "REJECTION_REASON",
      "LOOP_PREV_OUTPUT",
  )
  CONTEXT_NAMES = ("CONTEXT", "EXTERNAL_CONTEXT", "ISSUE_CONTEXT")
  VOCABULARY = PLAIN_NAMES + CONTEXT_NAMES
  NODE_OUTPUT = "NODE_OUTPUT"

  # Plain names carry no word-boundary guard in Archon ($ARTIFACTS_DIRX is substituted);
  # the context names carry a negative lookahead ($CONTEXTS is not).
  TOKEN_RE = re.compile(
      r"\$(?:"
      r"(?P<plain>" + "|".join(PLAIN_NAMES) + r")"
      r"|(?P<context>" + "|".join(CONTEXT_NAMES) + r")(?![A-Za-z0-9_])"
      r"|(?P<node>[a-zA-Z_][a-zA-Z0-9_-]*\.output)"
      r")"
  )
  # The identifier must end at the name, so ${CONTEXT_PACK} or ${OTHER} are not flagged.
  BRACE_RE = re.compile(r"\$\{(?P<name>" + "|".join(VOCABULARY) + r")(?![A-Za-z0-9_])")

  DEFAULT_BASELINE = Path(__file__).resolve().parent / "command_cache_shape_baseline.json"


  def _line_of(text: str, offset: int) -> int:
      return text.count("\n", 0, offset) + 1


  def shape(path: Union[str, Path]) -> dict:
      """Return the static prompt-shape metadata for one command file."""
      path = Path(path)
      data = path.read_bytes()
      text = data.decode("utf-8")
      matches = list(TOKEN_RE.finditer(text))
      if matches:
          start = matches[0].start()
          first_volatile_line = _line_of(text, start)
      else:
          start = len(text)
          first_volatile_line = len(text.splitlines()) + 1
      prefix = text[:start].encode("utf-8")
      tail_bytes = len(data) - len(prefix)

      fields: dict[str, dict] = {}
      for m in matches:
          name = m.group("plain") or m.group("context") or NODE_OUTPUT
          entry = fields.setdefault(
              name, {"name": name, "count": 0, "first_line": _line_of(text, m.start())}
          )
          entry["count"] += 1

      return {
          "command": path.stem,
          "prefix_hash": hashlib.sha256(prefix).hexdigest(),
          "stable_prefix_bytes": len(prefix),
          "volatile_tail_bytes": tail_bytes,
          "volatile_tail_tokens_est": tail_bytes // 4,  # estimate: bytes / 4
          "first_volatile_line": first_volatile_line,
          "cache_busting_fields": list(fields.values()),
      }
  ```

  Notes baked in from the spec:
  - `read_bytes()` + `decode` is used so that no newline translation happens. The prefix hash
    covers the exact committed bytes, CRLF included.
  - `cache_busting_fields` is ordered by first occurrence (dict insertion order).
  - Do **not** add a `\b` or lookahead to the plain names. Archon has none, and
    `test_plain_names_have_no_word_boundary` pins that.

- [ ] **Step 4: Run the tests and verify they pass**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: `9 passed`.

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/check_command_cache_shape.py tests/test_check_command_cache_shape.py
  git commit -m "feat(#236): command cache-shape metadata (stable prefix / volatile tail)"
  ```

---

## Task 2: `check()` — rules R1, R2, R3

**Files:**
- Modify: `tests/test_check_command_cache_shape.py`
- Modify: `scripts/check_command_cache_shape.py`

- [ ] **Step 1: Extend the test import**

  In `tests/test_check_command_cache_shape.py` replace
  ```python
  from check_command_cache_shape import shape  # noqa: E402
  ```
  with
  ```python
  from check_command_cache_shape import check, shape  # noqa: E402
  ```

- [ ] **Step 2: Append the failing rule tests**

  Append to `tests/test_check_command_cache_shape.py`:

  ```python
  # ---------------------------------------------------------------------------
  # R1: brace form
  # ---------------------------------------------------------------------------

  def test_r1_brace_form_fails(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "x\n${ARTIFACTS_DIR}/issue.json\n"}, {"a": 3})
      errors = check(commands, baseline)
      assert len(errors) == 1
      assert "R1" in errors[0] and "a.md:2" in errors[0]


  def test_r1_brace_form_with_default_fails(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "x\n${ARTIFACTS_DIR:-x}\n"}, {"a": 3})
      errors = check(commands, baseline)
      assert len(errors) == 1 and "R1" in errors[0]


  def test_r1_unrelated_brace_variables_pass(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "${OTHER} ${CONTEXT_PACK}\n"}, {"a": 2})
      assert check(commands, baseline) == []


  # ---------------------------------------------------------------------------
  # R2: prefix ratchet
  # ---------------------------------------------------------------------------

  def test_r2_token_earlier_than_baseline_fails(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "one\n$ARTIFACTS_DIR\n"}, {"a": 3})
      errors = check(commands, baseline)
      assert len(errors) == 1
      assert "R2" in errors[0] and "line 2" in errors[0] and "line 3" in errors[0]


  def test_r2_token_at_baseline_passes(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "one\ntwo\n$ARTIFACTS_DIR\n"}, {"a": 3})
      assert check(commands, baseline) == []


  def test_r2_token_later_than_baseline_passes(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "one\ntwo\nthree\n$WORKFLOW_ID\n"}, {"a": 3})
      assert check(commands, baseline) == []


  def test_r2_file_with_no_token_passes(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "one\ntwo\n"}, {"a": 3})
      assert check(commands, baseline) == []


  # ---------------------------------------------------------------------------
  # R3: baseline complete
  # ---------------------------------------------------------------------------

  def test_r3_missing_entry_fails_and_names_fix(tmp_path):
      commands, baseline = _setup(
          tmp_path, {"a": "x\n", "new-cmd": "one\n$ARTIFACTS_DIR\n"}, {"a": 2}
      )
      errors = check(commands, baseline)
      assert len(errors) == 1
      assert "R3" in errors[0]
      assert str(baseline) in errors[0]
      assert '"new-cmd": 2' in errors[0]


  def test_r3_orphan_entry_fails(tmp_path):
      commands, baseline = _setup(tmp_path, {"a": "x\n"}, {"a": 2, "gone": 3})
      errors = check(commands, baseline)
      assert len(errors) == 1
      assert "R3" in errors[0] and '"gone"' in errors[0] and str(baseline) in errors[0]


  def test_unreadable_baseline_fails(tmp_path):
      commands, _ = _setup(tmp_path, {"a": "x\n"}, {})
      bad = tmp_path / "bad.json"
      bad.write_text("{not json", encoding="utf-8")
      assert check(commands, bad) != []
      assert check(commands, tmp_path / "missing.json") != []
  ```

- [ ] **Step 3: Run the tests and verify the new ones fail**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: collection error `ImportError: cannot import name 'check' from 'check_command_cache_shape'`.

- [ ] **Step 4: Implement `_load_baseline()` and `check()`**

  Append to `scripts/check_command_cache_shape.py` (after `shape()`):

  ```python
  def _load_baseline(baseline: Path) -> tuple[dict[str, int], list[str]]:
      try:
          data = json.loads(baseline.read_text(encoding="utf-8"))
      except OSError as exc:
          return {}, [f"{baseline}: cannot read baseline: {exc}"]
      except json.JSONDecodeError as exc:
          return {}, [f"{baseline}: invalid JSON: {exc}"]
      if not isinstance(data, dict) or not all(
          isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
          for k, v in data.items()
      ):
          return {}, [f"{baseline}: must be a JSON object mapping command name to line number"]
      return data, []


  def check(
      commands_dir: Union[str, Path], baseline_path: Optional[Union[str, Path]] = None
  ) -> list[str]:
      """Apply R1-R3 to every ``*.md`` in *commands_dir*.

      Returns a (possibly empty) list of human-readable error strings.
      Returns ``[]`` when every file passes.
      """
      commands_dir = Path(commands_dir)
      baseline = Path(baseline_path) if baseline_path is not None else DEFAULT_BASELINE
      if not commands_dir.is_dir():
          return [f"{commands_dir}: not a directory"]
      floors, errors = _load_baseline(baseline)
      if errors:
          return errors

      files = sorted(commands_dir.glob("*.md"))
      for path in files:
          text = path.read_text(encoding="utf-8")
          for m in BRACE_RE.finditer(text):
              errors.append(
                  f"{path}:{_line_of(text, m.start())}: R1: brace form ${{{m.group('name')} "
                  f"is forbidden - Archon substitutes only the bare ${m.group('name')}; the "
                  "brace form reaches the agent unsubstituted and resolves to the wrong "
                  "directory in its shell (see docs/prompt-cache-shape.md)"
              )
          first = shape(path)["first_volatile_line"]
          name = path.stem
          if name not in floors:
              errors.append(
                  f"{path}: R3: no entry in {baseline} - add \"{name}\": {first} to "
                  f"{baseline} in the same commit"
              )
          elif first < floors[name]:
              errors.append(
                  f"{path}: R2: first volatile token is on line {first}, earlier than the "
                  f"baseline line {floors[name]} in {baseline} - move the token back below "
                  "the stable prefix (lowering the baseline needs its own reviewed ticket)"
              )

      names = {p.stem for p in files}
      for orphan in sorted(set(floors) - names):
          errors.append(
              f"{baseline}: R3: entry \"{orphan}\" has no {commands_dir}/{orphan}.md - "
              "remove the entry in the same commit"
          )
      return errors
  ```

  About R1's lookahead: the spec says "any `${NAME` where `NAME` is in the vocabulary". The
  `(?![A-Za-z0-9_])` makes "NAME" mean the whole identifier, so `${CONTEXT_PACK}` (a different
  variable) is not flagged. `${ARTIFACTS_DIR}` and `${ARTIFACTS_DIR:-x}` still are. Both
  cases are pinned by the tests above.

- [ ] **Step 5: Run the tests and verify they pass**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: `19 passed`.

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/check_command_cache_shape.py tests/test_check_command_cache_shape.py
  git commit -F - <<'MSG'
  feat(#236): cache-shape lint rules R1 (no brace form), R2 (ratchet), R3 (baseline complete)

  Only R1 guards a real failure today: the brace form would point a phase at
  the entrypoint's ARTIFACTS_DIR instead of the workflow run's. R2's baseline
  is a snapshot, not a cache contract; it matters once a later ticket raises
  a file's number.
  MSG
  ```

---

## Task 3: Committed baseline + real-tree tests

**Files:**
- Create: `scripts/command_cache_shape_baseline.json`
- Modify: `tests/test_check_command_cache_shape.py`

- [ ] **Step 1: Extend the test import**

  Replace
  ```python
  from check_command_cache_shape import check, shape  # noqa: E402
  ```
  with
  ```python
  from check_command_cache_shape import DEFAULT_BASELINE, check, shape  # noqa: E402
  ```

- [ ] **Step 2: Append the failing real-tree tests**

  Append to `tests/test_check_command_cache_shape.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Real tree
  # ---------------------------------------------------------------------------

  def test_real_commands_pass():
      assert check(_COMMANDS_DIR) == []


  def test_baseline_matches_committed_commands():
      baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
      current = {p.stem: shape(p)["first_volatile_line"] for p in sorted(_COMMANDS_DIR.glob("*.md"))}
      assert baseline == current, (
          f"{DEFAULT_BASELINE.name} is out of date. A command file's first volatile line moved "
          "(a line was added or removed above its first $TOKEN) or a command file was added or "
          "removed. Set the baseline to these values in the same commit: "
          + json.dumps(current, sort_keys=True)
      )
  ```

- [ ] **Step 3: Run and verify they fail**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q -k "real_commands_pass or baseline_matches"`
  Expected: 2 failed. `test_real_commands_pass` reports `cannot read baseline`, and
  `test_baseline_matches_committed_commands` reports `FileNotFoundError`.

- [ ] **Step 4: Create the baseline**

  Create `scripts/command_cache_shape_baseline.json` with today's values (Task 0 step 2
  confirmed the inventory). If the cross-check below prints a different mapping, the
  cross-check output is the baseline to commit:

  ```json
  {
    "ceiling-revisit": 23,
    "dark-factory-code-review": 3,
    "dark-factory-conformance": 3,
    "dark-factory-docs-conformance": 3,
    "dark-factory-implement": 3,
    "dark-factory-plan": 3,
    "dark-factory-refine": 3,
    "dark-factory-revise-advisory": 8,
    "dark-factory-validate": 3
  }
  ```

  Cross-check against the script itself (it must print the same mapping):
  ```bash
  PYTHONPATH=scripts python -c "import json,pathlib; from check_command_cache_shape import shape; print(json.dumps({p.stem: shape(p)['first_volatile_line'] for p in sorted(pathlib.Path('commands').glob('*.md'))}, indent=2))"
  ```

- [ ] **Step 5: Run and verify all pass**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: `21 passed`.

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/command_cache_shape_baseline.json tests/test_check_command_cache_shape.py
  git commit -m "feat(#236): commit cache-shape baseline for commands/*.md"
  ```

---

## Task 4: CLI — `main()` and `--json`

**Files:**
- Modify: `tests/test_check_command_cache_shape.py`
- Modify: `scripts/check_command_cache_shape.py`

- [ ] **Step 1: Extend the test import**

  Replace
  ```python
  from check_command_cache_shape import DEFAULT_BASELINE, check, shape  # noqa: E402
  ```
  with
  ```python
  from check_command_cache_shape import DEFAULT_BASELINE, check, main, shape  # noqa: E402
  ```

- [ ] **Step 2: Append the failing CLI tests**

  Append to `tests/test_check_command_cache_shape.py`:

  ```python
  # ---------------------------------------------------------------------------
  # CLI
  # ---------------------------------------------------------------------------

  def test_main_json_emits_metadata(tmp_path, capsys):
      commands, baseline = _setup(tmp_path, {"a": "x\n$ARTIFACTS_DIR\n"}, {"a": 2})
      assert main(["--json", "--baseline", str(baseline), str(commands)]) == 0
      out = json.loads(capsys.readouterr().out)
      assert [m["command"] for m in out] == ["a"]
      assert out[0]["first_volatile_line"] == 2
      assert out[0]["cache_busting_fields"] == [{"name": "ARTIFACTS_DIR", "count": 1, "first_line": 2}]


  def test_main_returns_1_on_violation(tmp_path, capsys):
      commands, baseline = _setup(tmp_path, {"a": "${ARTIFACTS_DIR}\n"}, {"a": 2})
      assert main(["--baseline", str(baseline), str(commands)]) == 1
      assert "R1" in capsys.readouterr().err


  def test_main_passes_on_real_tree(capsys):
      assert main([str(_COMMANDS_DIR)]) == 0
      assert "passed" in capsys.readouterr().out
  ```

- [ ] **Step 3: Run and verify they fail**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: collection error `ImportError: cannot import name 'main'`.

- [ ] **Step 4: Implement `main()`**

  Append to `scripts/check_command_cache_shape.py`:

  ```python
  def main(argv: Optional[list[str]] = None) -> int:
      parser = argparse.ArgumentParser(
          description="Lint the prompt cache shape of Archon phase command files."
      )
      parser.add_argument("commands_dir")
      parser.add_argument("--baseline", default=None, help="baseline JSON (default: next to this script)")
      parser.add_argument("--json", action="store_true", help="print per-command prompt-shape metadata")
      args = parser.parse_args(argv)

      if args.json and Path(args.commands_dir).is_dir():
          metadata = [shape(p) for p in sorted(Path(args.commands_dir).glob("*.md"))]
          print(json.dumps(metadata, indent=2))

      errors = check(args.commands_dir, args.baseline)
      if errors:
          print("Command cache-shape lint failed:", file=sys.stderr)
          for err in errors:
              print(f"  {err}", file=sys.stderr)
          return 1

      if not args.json:
          print(f"Command cache-shape lint passed for {args.commands_dir}.")
      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```

  (With `--json`, stdout is pure JSON so it can be piped to `jq`. Violations still go to
  stderr and still give exit 1.)

- [ ] **Step 5: Run and verify they pass, then smoke the CLI**

  Run: `PYTHONPATH=scripts python -m pytest tests/test_check_command_cache_shape.py -q`
  Expected: `24 passed`.

  Run: `python scripts/check_command_cache_shape.py commands; echo rc=$?`
  Expected: `Command cache-shape lint passed for commands.` then `rc=0`.

  Run: `python scripts/check_command_cache_shape.py --json commands | python -c "import json,sys; d=json.load(sys.stdin); print(len(d), sum(f['count'] for m in d for f in m['cache_busting_fields']))"`
  Expected: `9 238` at `origin/main` `2429106` (9 files; 237 tokens in the 8 phase commands + 1
  in `ceiling-revisit.md`). If `origin/main` has moved, the second number may differ. Check it
  against `grep -oE '\$(WORKFLOW_ID|ARTIFACTS_DIR|ISSUE_CONTEXT)' commands/*.md | wc -l` and
  carry on; do not edit any command file to make it match.

  Run: `python scripts/check_command_cache_shape.py --json commands | python -c "import json,sys; print([f for m in json.load(sys.stdin) if m['command']=='dark-factory-refine' for f in m['cache_busting_fields'] if f['name']=='ISSUE_CONTEXT'])"`
  Expected: `[{'name': 'ISSUE_CONTEXT', 'count': 1, 'first_line': 122}]` (the line number may
  shift if `origin/main` moved). This is the known
  defect, now visible. It is **not** fixed here.

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/check_command_cache_shape.py tests/test_check_command_cache_shape.py
  git commit -F - <<'MSG'
  feat(#236): cache-shape lint CLI with --json prompt-shape metadata

  Recorded, not fixed (no commands/*.md change in this ticket):
  commands/dark-factory-refine.md names $ISSUE_CONTEXT in prose; Archon blanks
  it, so the agent reads empty backticks there. --json reports it as
  ISSUE_CONTEXT, first_line 122.
  MSG
  ```

---

## Task 5: CI wiring (one line)

**Files:**
- Modify: `.github/workflows/ci.yml` (the `dag-check` job only)

- [ ] **Step 1: Add the step**

  In `.github/workflows/ci.yml`, directly after the existing line
  ```yaml
        - run: python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  ```
  insert
  ```yaml
        - run: python scripts/check_command_cache_shape.py commands
  ```
  Use the same 6-space indentation. Change nothing else in the file.

- [ ] **Step 2: Verify the diff is exactly one added line**

  Run: `git diff --numstat -- .github/workflows/ci.yml`
  Expected: `1	0	.github/workflows/ci.yml`

  Run: `python -c "import yaml; s=yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']['dag-check']['steps']; print([x.get('run') for x in s if 'cache_shape' in str(x.get('run'))])"`
  Expected: `['python scripts/check_command_cache_shape.py commands']`

- [ ] **Step 3: Verify the shell-test CI guard still passes**

  Run: `python -m pytest tests/test_ci_covers_shell_tests.py -q`
  Expected: all passed. No new `tests/*.sh` is added, so the guard is unaffected.

- [ ] **Step 4: Commit (with the hold explanation in the message body)**

  The commit message body is where the spec's §3 note lives ("the hold is caused by this one
  line"). This run has no agent-written PR body: see Task 7 step 4.

  ```bash
  git add .github/workflows/ci.yml
  git commit -F - <<'MSG'
  ci(#236): run command cache-shape lint in dag-check

  This one added line in .github/workflows/ci.yml is the only reason this change
  is on the blast-radius hold. No gate, breaker, budget, config, tool-list or
  commands/*.md file is touched.

  Rollback: delete this line (or revert this commit). There is no runtime switch.

  If this commit is ever dropped because a push of workflow files is refused, the
  remaining manual step is: add
  `- run: python scripts/check_command_cache_shape.py commands` after the
  check_workflow_when.py line in the dag-check job.
  MSG
  ```

  **Who pushes, and what a refusal means.** The implement agent does not push. The branch is
  pushed by the deterministic `blast-gate` node (`blast_gate_check.sh`, when it holds, which
  this diff will) or by the `push-and-pr` node. Neither retries. A refused push fails the run,
  and the container's unpushed commits are lost. There is no in-run recovery path, so do
  **not** script one: no `reset`, `revert` or `rebase` of this commit. The factory account has
  pushed `ci.yml` changes before (#624, #633, #642), so this risk is accepted on that
  precedent, as the spec's last assumption does.

---

## Task 6: `docs/prompt-cache-shape.md`

**Files:**
- Create: `docs/prompt-cache-shape.md`

No test is involved: this is a doc. Acceptance criterion 6 is checked by the `grep` in
step 2.

- [ ] **Step 1: Write the doc**

  Create `docs/prompt-cache-shape.md` with exactly this content. It contains no `path:N`
  line citations, so it stays stable across edits and never needs `docs_refs_check.py`.

  ````markdown
  # Prompt cache shape for phase commands

  Issue #236. Design spec: `2026-11-28-prompt-cache-shape-contract-design.md`, under
  `docs/superpowers/specs/` until the ticket's PR archives it to `docs/archive/`.
  Enforced by `scripts/check_command_cache_shape.py`, run in CI's `dag-check` job.

  ## Why this exists

  Each phase of a factory run (refine, plan, implement, validate, conformance,
  docs-conformance, code-review, revise-advisory) is an Archon `command:` node. Archon reads
  `commands/<name>.md` verbatim, frontmatter included. It replaces a fixed vocabulary of
  `$NAME` tokens across the whole text and sends the result as the node's first user message.
  Those values are per run, so the rendered command differs between runs from its first token
  onward.

  Provider prompt caching is prefix-based. A request reuses cached content only up to the first
  byte that differs from an earlier cached request, and only within the cache lifetime. So:

  1. Only the position of the **first** volatile token matters. Removing later occurrences
     while leaving the first changes nothing.
  2. Within one run the rendered command is constant, so cache reads between turns inside a
     node are unaffected. What is at stake is whether a node's first request can reuse command
     text cached by an earlier run of the same phase. That also needs everything ahead of the
     command text (tool definitions, the harness system prompt, `CLAUDE.md`) to be
     byte-identical across runs. This repo does not establish that.

  Nothing here claims a saving. The lint makes the shape measurable and blocks one unsafe
  "fix".

  ## Substituted vocabulary

  Archon substitutes these names, bare only, never in brace form:

  ```
  $WORKFLOW_ID  $ARTIFACTS_DIR  $BASE_BRANCH  $DOCS_DIR
  $USER_MESSAGE  $ARGUMENTS  $LOOP_USER_INPUT  $REJECTION_REASON  $LOOP_PREV_OUTPUT
  $CONTEXT  $EXTERNAL_CONTEXT  $ISSUE_CONTEXT
  ```

  The first nine have no word-boundary guard, so `$ARTIFACTS_DIRX` is substituted. The three
  context names do have one, so `$CONTEXTS` is not. `archon workflow run` passes no issue
  context, so the context names become the empty string. Archon also replaces
  `$<node-id>.output` and `$<node-id>.output.<field>`. The lint treats those as volatile and
  reports them as `NODE_OUTPUT`.

  The list mirrors the Archon source at the commit `Dockerfile` pins (`74372446`). If that pin
  moves, re-check the constants at the top of the lint script.

  ## The contract

  For each `commands/*.md`, as committed:

  - **stable_prefix** — the bytes from the start of the file up to, but not including, the
    first bare vocabulary token or node-output reference.
  - **volatile_tail** — everything from that token to the end of the file.
  - **cache_busting_fields** — the vocabulary names that occur bare in the file, with
    occurrence count and first line.
  - **Forbidden anywhere in a command file:** the brace form `${NAME` of a vocabulary name.

  The target shape puts the following in the prefix: factory identity, phase contract, output
  schema, tool and safety policy, adapter summary, and skill/role names. It puts the following
  in the tail: issue number, comments, diff, run id, command output and time. Today's phase
  commands are far from that: their first token is on line 3 (line 8 for revise-advisory, and
  line 23 for `ceiling-revisit.md`, which is not loaded as a node). No text has been moved
  yet. The lint records where each file stands and stops regression.

  ## Do not use the brace form

  Rewriting `$ARTIFACTS_DIR` as `${ARTIFACTS_DIR}` looks harmless but breaks every phase:

  - Archon substitutes only the bare form, so the brace form reaches the model unsubstituted.
  - In prose, or in a Read/Write tool path, the agent gets a literal placeholder.
  - In the agent's shell, `${ARTIFACTS_DIR}` resolves from the container environment. That
    holds the entrypoint's `runs/<RUN_ID>` directory, not the workflow run's directory, where
    the bash nodes wrote `issue.json`, `context-budget.*.json` and the rest. Archon sets
    `ARTIFACTS_DIR` only for bash-node subprocesses.

  The phase would read and write the wrong directory. Rule R1 blocks this. It would become safe
  only if Archon exported the same `ARTIFACTS_DIR` to command-node agents, which is an Archon
  change.

  This applies to `commands/*.md` only. Bash nodes in `workflows/archon-dark-factory.yaml`
  legitimately use `${ARTIFACTS_DIR:-…}`, because Archon does set the variable there.

  ## The lint

  ```bash
  python scripts/check_command_cache_shape.py commands          # rules; exit 1 on violation
  python scripts/check_command_cache_shape.py --json commands   # per-command metadata
  ```

  - **R1 (no brace form).** Any `${NAME` of a vocabulary name is a violation. This is the rule
    with real value today.
  - **R2 (prefix ratchet).** A file's `first_volatile_line` must be at least the value recorded
    for it in `scripts/command_cache_shape_baseline.json`. The value is the 1-based line of
    the first bare token, or the line count + 1 if the file has none. Today's baseline is a
    snapshot (3 for most files), not a cache guarantee. R2 starts to protect something once a
    later change raises a file's number.
  - **R3 (baseline complete).** Every `commands/*.md` needs a baseline entry and every entry
    needs a file. The error message prints the exact entry to add or remove.

  Updating the baseline:

  - **Adding or renaming a command file:** add, rename or remove its entry in the same commit.
    The R3 message prints the line to add.
  - **Improving a file**, for example moving its first token later: raise its number in the
    same commit. `tests/test_check_command_cache_shape.py` asserts that the baseline equals
    the current values exactly.
  - **Lowering a number** means a prompt now busts the cache earlier. Do it only in a reviewed
    ticket that says why.

  ## Prompt-shape metadata

  `--json` prints one object per command file:

  ```json
  {
    "command": "dark-factory-plan",
    "prefix_hash": "<sha256 hex of the stable_prefix bytes>",
    "stable_prefix_bytes": 0,
    "volatile_tail_bytes": 0,
    "volatile_tail_tokens_est": 0,
    "first_volatile_line": 3,
    "cache_busting_fields": [{"name": "ARTIFACTS_DIR", "count": 25, "first_line": 3}]
  }
  ```

  `volatile_tail_tokens_est` is `volatile_tail_bytes // 4`, an estimate. The metadata is
  static: it is a function of the committed file and is the same for every run of a commit.
  No per-run artifact is written.

  ## Measuring cache effect

  Use the per-node fields already in each run's `run-record.json` (#645):
  `gen_ai.usage.cache_read_input_tokens` and `gen_ai.usage.cache_creation_input_tokens` on
  each entry of `nodes`. Do **not** use `harness_economics.ledger_mechanics.cache_hit_ratio`.
  It is computed from model-proxy ledger rows whose cache columns are hardcoded to zero.

  Procedure for one phase, for example `plan` (node id `plan`, command `dark-factory-plan`):

  1. Upper bound on what a perfect prefix could save per run of that node:
     `volatile_tail_tokens_est` × (cache-write price − cache-read price).
     ```bash
     python scripts/check_command_cache_shape.py --json commands \
       | jq '.[] | select(.command=="dark-factory-plan") | .volatile_tail_tokens_est'
     ```
  2. Compare it with that node's cache-creation tokens in recent run records. Join on run id
     **and** node id, because a run-level total mixes phases:
     ```bash
     jq '{run_id, node: (.nodes[] | select(.node_id=="plan")
          | {cache_creation: ."gen_ai.usage.cache_creation_input_tokens",
             cache_read: ."gen_ai.usage.cache_read_input_tokens"})}' \
       <artifacts-root>/runs/<run-id>/run-record.json
     ```

  **Limitation.** Per-node figures sum every turn of the node. They cannot isolate a cross-run
  prefix hit on the first request. They bound the possible gain; they do not prove a realised
  one. Restructuring prompts is worth a ticket only if the bound is a meaningful share of the
  node's cache-creation tokens, and that call is the operator's. A cross-run hit is impossible
  whatever the command file's shape if the harness system prompt carries per-run values
  (working directory, date, branch). It also needs a second run of the same phase on the same
  model inside the cache lifetime.

  ## Other prompt surfaces

  - `refinement-skills/` persona templates are rendered mid-run by
    `scripts/factory_core/prompt_render.py`. It never substitutes inside backtick spans and
    fails on any slot/value mismatch. These templates are never part of a command node's
    first-request prefix and are not linted.
  - Reviewer rubrics (`.claude/skills/{conformance,code-review}/RUBRIC.md`) are read from disk
    during a run, and no template engine touches them. They are not linted.
  - There is no role-card schema to align with (#42 deferred it).
  - Anything later loaded by Archon as a `command:` node lives in `commands/` and is covered
    automatically.

  ## Not covered

  - The issue's "forbidden in stable prefix" list (dates, random ids, file listings, log
    snippets) is not detected. Only Archon-substituted tokens are.
  - The three inline `prompt:` nodes in the workflow YAML go through the same substitution
    but are not linted.
  - No prompt text has been moved toward the target shape.
  - `commands/dark-factory-refine.md` names `$ISSUE_CONTEXT` in prose. Archon blanks it, so
    the agent reads empty backticks there. `--json` shows it. It is not yet fixed.

  ## Rollback

  The lint changes no prompt text, so it cannot change phase behaviour. If it is noisy or
  wrong, delete its `- run: python scripts/check_command_cache_shape.py commands` line from
  the `dag-check` job in `.github/workflows/ci.yml`, or revert the commit. There is no runtime
  switch or config key, and nothing to coordinate with a running scheduler.
  ````

- [ ] **Step 2: Verify the required sections and the absence of line citations**

  Run:
  ```bash
  grep -c -e '## Do not use the brace form' -e '## Measuring cache effect' -e '\*\*Limitation.\*\*' -e '## Rollback' docs/prompt-cache-shape.md
  grep -nE '`[^`]+\.(py|sh|md|ts|yaml|yml|json):[0-9]' docs/prompt-cache-shape.md | wc -l
  ```
  Expected: `4`, then `0`.

- [ ] **Step 3: Commit**

  ```bash
  git add docs/prompt-cache-shape.md
  git commit -m "docs(#236): prompt cache-shape contract, measurement procedure, rollback"
  ```

---

## Task 7: Full verification and PR notes

**Files:** none (verification only)

- [ ] **Step 1: Full Python suite**

  Run: `PYTHONPATH=scripts python -m pytest tests/ -q`
  Expected: all pass. That includes the 24 new tests in `tests/test_check_command_cache_shape.py`.

- [ ] **Step 2: CI `dag-check` parity**

  Run:
  ```bash
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml \
    && python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml \
    && python scripts/check_command_cache_shape.py commands \
    && python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```
  Expected: every command exits 0. The third prints `Command cache-shape lint passed for commands.`

- [ ] **Step 3: Scope checks**

  Run:
  ```bash
  git diff origin/main --stat -- commands/
  git diff origin/main --name-only
  ```
  Expected: the first prints nothing (acceptance criterion 2). The second lists only:
  `.github/workflows/ci.yml`, `docs/prompt-cache-shape.md`,
  `scripts/check_command_cache_shape.py`, `scripts/command_cache_shape_baseline.json`,
  `tests/test_check_command_cache_shape.py`, plus the spec and this plan, carried over from
  the refine branch. Use the two-dot form shown
  here: three-dot gives false out-of-scope hits.

- [ ] **Step 4: Notes for the human reviewer: commit messages only**

  The PR body is a fixed template built by the `push-and-pr` node. This run will also stop at
  the blast-radius hold before any PR is opened. The hold comment lists the triggered files,
  so it shows `.github/workflows/ci.yml` as the only trigger. **Do not** create or edit a PR
  (`gh pr create` / `gh pr edit`), post an issue comment, or change the `push-and-pr`
  template. Reviewer notes go in commit message bodies:
  - The Task 5 `ci(#236)` commit already says the hold is caused by its one line, gives the
    rollback, and gives the manual step for the case where the commit is dropped.
  - The Task 4 commit body records the unfixed `$ISSUE_CONTEXT` defect, and the Task 2 commit
    body says what the rules are worth (only R1 guards a real failure today; R2's baseline is
    a snapshot). Confirm both:
    ```bash
    git log --format=%B origin/main..HEAD | grep -c -e 'ISSUE_CONTEXT' -e 'Only R1 guards' -e 'only reason this change'
    ```
    Expected: `3` or more.

---

## Acceptance-criteria trace

| Spec acceptance criterion | Task |
|---|---|
| 1. `check_command_cache_shape.py commands` exits 0 | Task 4 step 5, Task 7 step 2 |
| 2. `git diff origin/main --stat -- commands/` empty | Task 7 step 3 |
| 3. Test coverage: R1 (`${ARTIFACTS_DIR}`, `${ARTIFACTS_DIR:-x}` fail; `${OTHER}` passes) | Task 2 |
| 3. R2 earlier fails / equal-or-later passes / no-token passes | Task 2 |
| 3. R3 missing + orphan fail; message names baseline file and entry | Task 2 |
| 3. Matching: `$ARTIFACTS_DIRX`, `$CONTEXTS`, `$ISSUE_CONTEXT_X`, `ISSUE_CONTEXT` vs `CONTEXT`, `NODE_OUTPUT` | Task 1 |
| 3. `--json`: hash = sha256 of pre-token bytes; tail change keeps hash; prefix change alters it; counts | Task 1, Task 4 |
| 3. Real tree: `check("commands") == []`; baseline matches committed files | Task 3 |
| 4. CI runs the lint in `dag-check` | Task 5 |
| 5. Full pytest, both DAG checks, `docs_refs_check.py` pass | Task 7 |
| 6. Doc has brace-form warning, measurement + limitation, rollback | Task 6 |

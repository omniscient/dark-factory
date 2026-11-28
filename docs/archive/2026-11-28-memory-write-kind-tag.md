# Implementation Plan: `memory_write.py` `--kind` argument (stop hardcoding `[AVOID]`)

**Issue:** #38

**Spec:** `docs/superpowers/specs/2026-11-28-memory-write-kind-tag-design.md`

---

## Goal

`scripts/memory_write.py` gets a `--kind {pattern,avoid,fix}` argument (default `avoid`)
that picks the emitted tag in place of the hardcoded `[AVOID]`. Refine's Phase 5 step 7a
memory snippet is rewritten as a self-contained, shell-safe block that passes
`--kind avoid` with a rejected-approach-first body; step 7b gains one sentence telling
refine to use `--kind pattern` for conventions with no prohibition. The memory docs stop
saying `memory_write.py` writes only `[AVOID]`, and the `commands/dark-factory-refine.md`
line citations that move are re-anchored.

## Architecture

- **Additive CLI change.** One new `argparse` option with `choices=["pattern", "avoid",
  "fix"]` and `default="avoid"`. The tag is derived from the validated choice
  (`args.kind.upper()`), so no caller text reaches the tag position. If the flag is
  omitted, the output is byte-identical to today's. An invalid value exits 2 in
  `parse_args()`, before `main()` creates a directory or touches a file.
- **No change** to `_ENTRY_RE` (it already matches all three tags), the reinforcement
  path (it refreshes `date:`/`expires:` and never retags), the cap/eviction logic, or
  `scripts/memory_retrieve.py`.
- **`scripts/gate_lib.sh` stays byte-identical.** `write_memory_entry()` passes no
  `--kind` and keeps writing `[AVOID]` through the default. A new pytest pins this.
- **Refine snippet.** The lesson goes into a quoted heredoc (`<<'LESSON'`) so `$VAR`,
  `$(…)` and backticks stay literal. `ISSUE_NUM` is set inside the block, because shell
  state does not carry across Bash tool calls. The `\  # TARGET-PATH` line, which is not a
  real line continuation, is replaced by a `MEMORY_WRITE=…  # TARGET-PATH` assignment.
- **Memory store untouched by hand.** No line of `.archon/memory/**` is edited, retagged
  or deleted (spec Requirement 4).

## Tech Stack

Python 3 (`argparse`), pytest, bash (agent-executed Markdown snippet), Markdown docs.

---

## File Structure

| File | Change |
|---|---|
| `scripts/memory_write.py` | Add `--kind` to `_parse_args()`; thread it into the entry f-string; update the docstring usage block and exit codes |
| `tests/test_memory_write.py` | Add `import os`; new `TestKind` class (tag emission, default byte-identity, invalid values, reinforcement without retag, kind-agnostic cap); new gate-path pin test |
| `tests/test_refine_command_memory_snippet.py` | **New** — static shape checks plus a verbatim run of the step 7a bash block |
| `commands/dark-factory-refine.md` | Phase 5 step 7a block rewritten; step 7b gains one sentence. No other line changes (in particular `:94` is not edited) |
| `docs/dark-factory-memory-contract.md` | `:70` state diagram, `:121` Refine threshold row, `:155` Refine "how each role writes" row, `:159-161` paragraph rewritten (incl. reader behaviour) |
| `docs/dark-factory-memory-v2.md` | `:25` Writer row, `:36` Refine row |
| `docs/features.md` | `:134` citation `dark-factory-refine.md:197` → `:204` |
| `docs/run-lifecycle.md` | `:536` `(:176-219)` → `(:176-230)`; `:537` `(:221-287)` → `(:232-298)` |
| `docs/configuration.md` | `:128` `dark-factory-refine.md:231` → `:242`; `:131` and `:772` `dark-factory-refine.md:230` → `:241` (only the `dark-factory-refine.md` citations; the `dark-factory-plan.md` ones on `:129`, `:132`, `:772` belong to #405) |

**Must not change** (spec Acceptance Criterion 2): `scripts/gate_lib.sh`,
`scripts/memory_retrieve.py`, `refinement-skills/**`, `commands/dark-factory-plan.md`,
`commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`,
`commands/dark-factory-implement.md`, `.github/**`, `workflows/**`, `config/**`,
`deploy/**`, and `.archon/memory/**` (no hand edits). Do not add a `tests/test_*.sh` file
and do not edit `.github/workflows/ci.yml`. This repo tracks no `dark-factory/scripts/`
or `.archon/commands/` copies. If a run's clone has a `dark-factory/` directory, it is an
untracked runtime copy: never edit it, never `git add` it.

**Copying code from this plan.** Every code block inside a task step is indented two
spaces to nest it under its list item. Drop exactly those two leading spaces when
copying. The `commands/dark-factory-refine.md` blocks in Task 3 keep their own 3-space
list indentation inside the file, so they appear here with five leading spaces and
land in the file with three.

**Test runner.** All commands run from the repo root. CI runs
`PYTHONPATH=scripts python -m pytest tests/ -v`. If `pytest`/`aiohttp` are missing
locally, install them first (`pip install pytest pyyaml aiohttp`, as CI does).

---

## Task 1: `--kind` argument in `memory_write.py`

**Files:** `tests/test_memory_write.py`, `scripts/memory_write.py`

- [ ] **Step 1.1 — add `import os` to the test module.** In `tests/test_memory_write.py`,
  change the import block (lines 6-8):

  ```python
  import re
  import shutil
  import subprocess
  ```
  to
  ```python
  import os
  import re
  import shutil
  import subprocess
  ```
  (`os` is used by the gate-path test in Task 2. Add it now so the import block is edited
  only once.)

- [ ] **Step 1.2 — write the failing tests.** Append this to the end of
  `tests/test_memory_write.py`, after the last line of `class TestSanitization`, separated
  by two blank lines:

  ```python
  # ── --kind tag selection (#38) ──────────────────────────────────────────────

  def _entry_lines(md):
      """Authoritative [PATTERN]/[AVOID]/[FIX] lines of *md*, in file order."""
      return [l for l in md.read_text().splitlines()
              if re.match(r"^- \[(PATTERN|AVOID|FIX)\] ", l)]


  _KIND_ARGS = ("--path-prefix", "backend/app/", "--text", "prefer fixtures over mocks",
                "--source", "refine", "--issue", "38")


  class TestKind:
      @pytest.mark.parametrize("kind,tag", [("pattern", "PATTERN"), ("fix", "FIX"),
                                            ("avoid", "AVOID")])
      def test_kind_selects_tag_with_unchanged_metadata(self, md_empty, kind, tag):
          result = run("--target", str(md_empty), *_KIND_ARGS, "--kind", kind)
          assert result.returncode == 0, result.stderr
          [line] = _entry_lines(md_empty)
          today = date.today().strftime("%Y-%m-%d")
          assert line.startswith(
              f"- [{tag}] prefer fixtures over mocks <!-- issue:#38 date:{today} expires:")
          assert line.endswith(
              " source:refine agent:refine scope:backend path:backend/app/ -->")

      def test_omitted_kind_is_byte_identical_to_kind_avoid(self, tmp_path):
          seed = "# Backend Patterns\n\n---\n<!-- PROVISIONAL -->\n"
          default_md = tmp_path / "default" / "backend-patterns.md"
          explicit_md = tmp_path / "explicit" / "backend-patterns.md"
          for md in (default_md, explicit_md):
              md.parent.mkdir()
              md.write_text(seed)
          assert run("--target", str(default_md), *_KIND_ARGS).returncode == 0
          assert run("--target", str(explicit_md), *_KIND_ARGS,
                     "--kind", "avoid").returncode == 0
          assert default_md.read_bytes() == explicit_md.read_bytes()
          [line] = _entry_lines(default_md)
          assert line.startswith("- [AVOID] prefer fixtures over mocks <!-- ")

      @pytest.mark.parametrize("bad", ["AVOID] injected", "AVOID", "provisional", ""])
      def test_invalid_kind_exits_2_and_leaves_target_untouched(self, md_empty, bad):
          before = md_empty.read_bytes()
          result = run("--target", str(md_empty), *_KIND_ARGS, "--kind", bad)
          assert result.returncode == 2
          assert "invalid choice" in result.stderr
          assert md_empty.read_bytes() == before

      def test_invalid_kind_creates_no_directory(self, tmp_path):
          mem_dir = tmp_path / "fresh" / ".archon" / "memory"
          result = run("--target", str(mem_dir / "backend-patterns.md"), *_KIND_ARGS,
                       "--kind", "AVOID] injected")
          assert result.returncode == 2
          assert not (tmp_path / "fresh").exists()

      def test_pattern_write_reinforces_existing_avoid_without_retagging(self, md_empty):
          assert run("--target", str(md_empty), *_KIND_ARGS,
                     "--kind", "avoid").returncode == 0
          content = re.sub(r"date:\d{4}-\d{2}-\d{2}", "date:2025-01-01",
                           md_empty.read_text())
          md_empty.write_text(content)
          result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                       "--text", "Prefer  Fixtures over MOCKS.", "--source", "refine",
                       "--issue", "39", "--kind", "pattern")
          assert result.returncode == 0, result.stderr
          assert "reinforced existing entry" in result.stdout
          [line] = _entry_lines(md_empty)
          assert line.startswith("- [AVOID] prefer fixtures over mocks <!-- issue:#38 ")
          assert f"date:{date.today().strftime('%Y-%m-%d')}" in line

      def test_cap_evicts_oldest_by_date_regardless_of_kind(self, tmp_path):
          md = tmp_path / "architecture.md"
          kinds = ("PATTERN", "AVOID", "FIX")
          lines = ["# Architecture\n\n"]
          for i in range(30):
              day = "2025-12-31" if i == 12 else f"2026-03-{i + 1:02d}"
              lines.append(f"- [{kinds[i % 3]}] Old {i:02d} "
                           f"<!-- issue:#1 date:{day} expires:2027-01-01 source:refine -->\n")
          lines.append("---\n<!-- PROVISIONAL -->\n")
          md.write_text("".join(lines))
          result = run("--target", str(md), "--path-prefix", "docs/", "--text", "new fix",
                       "--source", "refine", "--issue", "38", "--kind", "fix")
          assert result.returncode == 0, result.stderr
          entries = _entry_lines(md)
          assert len(entries) == 30
          assert not any("Old 12 " in l for l in entries)  # the oldest, a [PATTERN] line
          assert sum(l.startswith("- [FIX] new fix <!-- ") for l in entries) == 1
          assert sum(l.startswith("- [PATTERN] ") for l in entries) == 9
          assert sum(l.startswith("- [AVOID] ") for l in entries) == 10
  ```

  Expected counts in the cap test: 30 seeded entries (10 per kind), with `Old 12` (a
  `[PATTERN]`, dated `2025-12-31`) the oldest. It sits mid-file, so eviction has to go by
  date and not by kind or file position. After the write: 9 `[PATTERN]`, 10 `[AVOID]` and
  11 `[FIX]` lines (10 old plus the new one). That makes 31 before eviction and 30 after.

- [ ] **Step 1.3 — verify they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_memory_write.py -k TestKind -v
  ```
  Expected: `10 failed, 1 passed`. Today's `argparse` rejects the unknown `--kind` with
  exit 2 (`unrecognized arguments`), so every case that expects exit 0 fails. The four
  `test_invalid_kind_exits_2_and_leaves_target_untouched` parametrizations fail on the
  `"invalid choice" in result.stderr` assertion. The one that passes,
  `test_invalid_kind_creates_no_directory`, passes for the wrong reason (unknown flag
  instead of rejected choice). That is acceptable, because Step 1.5 must keep it passing.

- [ ] **Step 1.4 — implement.** In `scripts/memory_write.py`:

  a. Docstring usage block (lines 8-19). Replace
  ```
          --issue       <issue number>

  Exit 0: write succeeded, or intentionally skipped (dedup/reinforcement). At the
  30-entry cap the oldest authoritative entries are evicted to make room.
  Exit 1: markdown I/O error.
  ```
  with
  ```
          --issue       <issue number>                            \\
          [--kind       <pattern|avoid|fix>]   (default: avoid)

  Exit 0: write succeeded, or intentionally skipped (dedup/reinforcement). At the
  30-entry cap the oldest authoritative entries are evicted to make room.
  Exit 1: markdown I/O error.
  Exit 2: invalid arguments (argparse), e.g. --kind outside pattern|avoid|fix;
          nothing is created or written.
  ```
  (The `\\` is literal in the source file. The docstring is a normal string, so `\\`
  renders as one backslash, matching the lines above it.)

  b. `_parse_args()`: after the line
  `    p.add_argument("--issue", required=True, type=int)` insert
  ```python
      # Closed allowlist: the tag is derived from the validated choice only, so no
      # caller text reaches the tag position. Default avoid keeps callers that omit
      # the flag (gate_lib.sh::write_memory_entry) byte-identical to the old output.
      p.add_argument("--kind", choices=["pattern", "avoid", "fix"], default="avoid",
                     help="entry tag: pattern -> [PATTERN], avoid -> [AVOID], fix -> [FIX]")
  ```

  c. Entry construction (currently line 195). Replace
  ```python
              f"- [AVOID] {args.text} "
  ```
  with
  ```python
              f"- [{args.kind.upper()}] {args.text} "
  ```
  Change nothing else in `main()`.

- [ ] **Step 1.5 — verify they pass, and that nothing else broke.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_memory_write.py -v
  ```
  Expected: `49 passed` (the 38 existing tests plus 11 new `TestKind` cases, counting
  parametrizations).

  ```bash
  python3 scripts/memory_write.py --target /tmp/kind-smoke/.archon/memory/architecture.md \
    --path-prefix docs/ --text x --source refine --issue 1 --kind 'AVOID] injected'; echo "rc=$?"; ls /tmp/kind-smoke 2>&1
  ```
  Expected: `memory_write.py: error: argument --kind: invalid choice: 'AVOID] injected' (choose from …)`
  (the choice list is printed quoted or unquoted depending on the Python version),
  `rc=2`, and `ls: cannot access '/tmp/kind-smoke': No such file or directory`.

- [ ] **Step 1.6 — commit.**

  ```bash
  git add scripts/memory_write.py tests/test_memory_write.py
  git commit -m "feat(#38): memory_write.py --kind {pattern,avoid,fix}, default avoid"
  ```

## Task 2: Pin the gate path (`gate_lib.sh` unchanged → still `[AVOID]`)

**Files:** `tests/test_memory_write.py` (`scripts/gate_lib.sh` is **not** modified)

This pins behaviour that already exists, so there is no red phase. The test guards the
spec's decision to leave `gate_lib.sh` byte-identical and rely on the default.

- [ ] **Step 2.1 — write the test.** Append to the end of `tests/test_memory_write.py`
  (after `class TestKind`, two blank lines):

  ```python
  def test_gate_lib_write_memory_entry_still_writes_avoid(md_empty):
      """gate_lib.sh passes no --kind; the default must keep the gate path [AVOID]."""
      if os.name != "posix" or shutil.which("bash") is None:
          pytest.skip("needs a POSIX host with bash")
      gate_lib = SCRIPT.parent / "gate_lib.sh"
      proc = subprocess.run(
          ["bash", "-c",
           'source "$1" && write_memory_entry "$2" dark-factory/scripts/ '
           '"avoid bad pattern" conformance 38',
           "bash", str(gate_lib), str(md_empty)],
          capture_output=True, text=True,
      )
      assert proc.returncode == 0, proc.stderr
      [line] = _entry_lines(md_empty)
      assert line.startswith("- [AVOID] avoid bad pattern <!-- issue:#38 ")
  ```

- [ ] **Step 2.2 — run it.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_memory_write.py -k gate_lib -v
  ```
  Expected: `1 passed` (`SKIPPED` only on a non-POSIX host or one without `bash`).

- [ ] **Step 2.3 — prove it guards the default.** Temporarily change `default="avoid"` to
  `default="pattern"` in `scripts/memory_write.py` and re-run the Step 2.2 command.
  Expected: **FAIL** (`- [PATTERN] avoid bad pattern …`). Revert the change, re-run, and
  confirm `1 passed`. `git diff --stat` must now list only `tests/test_memory_write.py`
  (no leftover change to `scripts/memory_write.py`).

- [ ] **Step 2.4 — confirm `gate_lib.sh` is untouched.**

  ```bash
  git diff --quiet origin/main...HEAD -- scripts/gate_lib.sh && echo unchanged
  ```
  Expected: `unchanged`.

- [ ] **Step 2.5 — commit.**

  ```bash
  git add tests/test_memory_write.py
  git commit -m "test(#38): pin gate_lib.sh write_memory_entry to the [AVOID] default"
  ```

## Task 3: Rewrite refine Phase 5 step 7a/7b

**Files:** `tests/test_refine_command_memory_snippet.py` (new),
`commands/dark-factory-refine.md`

- [ ] **Step 3.1 — write the failing test.** Create
  `tests/test_refine_command_memory_snippet.py` with exactly:

  ```python
  """Refine Phase 5 step 7a memory-write snippet (#38). The block is agent-executed
  Markdown, so — like tests/test_shadow_model_resolution.py — the behavioral case extracts
  it and runs it verbatim against a fixture clone, and the static cases pin its shape."""
  import json
  import os
  import shutil
  import subprocess
  import textwrap
  from pathlib import Path

  import pytest

  _REPO_ROOT = Path(__file__).resolve().parents[1]
  _REFINE = _REPO_ROOT / "commands" / "dark-factory-refine.md"
  _WRITER = _REPO_ROOT / "scripts" / "memory_write.py"
  _PLACEHOLDER = ("Rejected approach: <what was rejected and the concrete reason>. "
                  "Chosen approach: <what to do instead>.")
  _LESSON = ("Rejected approach: expanding $HOME, $(echo x) and `date` inside \"double\" "
             "and 'single' quotes. Chosen approach: keep the lesson literal.")
  _SHELL_VARS = ("ISSUE_NUM", "REPO_ROOT", "LESSON_TEXT", "MEMORY_WRITE")


  def _step_7a():
      text = _REFINE.read_text(encoding="utf-8")
      start = text.index("   a. For each architectural decision made during Phase 4 Q&A")
      return text[start:text.index("   b. For any codebase convention", start)]


  def _block():
      body = _step_7a().split("```bash\n", 1)[1].split("```", 1)[0]
      return textwrap.dedent(body)


  def test_block_shape():
      block = _block()
      assert "--kind avoid" in block
      assert "<<'LESSON'" in block
      assert block.index("Rejected approach:") < block.index("Chosen approach:")
      assert _PLACEHOLDER in block.splitlines()
      assert "${ARTIFACTS_DIR}" not in block
      assert '"$ARTIFACTS_DIR/issue.json"' in block


  def test_tool_limitation_note_removed():
      assert "Tool limitation (#652)" not in _REFINE.read_text(encoding="utf-8")


  def test_step_7b_names_kind_pattern():
      text = _REFINE.read_text(encoding="utf-8")
      start = text.index("   b. For any codebase convention")
      step_7b = text[start:text.index("   c. Do NOT write to", start)]
      assert "`--kind pattern`" in step_7b
      assert "`--kind avoid`" in step_7b


  def test_block_runs_verbatim_and_keeps_lesson_literal(tmp_path):
      if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git", "jq")):
          pytest.skip("needs a POSIX host with bash, git and jq")
      block = _block()
      assert block.count(_PLACEHOLDER) == 1
      block = block.replace(_PLACEHOLDER, _LESSON)

      run_env = {k: v for k, v in os.environ.items()
                 if k not in _SHELL_VARS and not k.startswith("GIT_")}
      clone = tmp_path / "clone"
      subprocess.run(["git", "init", "-q", str(clone)], check=True, env=run_env)
      writer = clone / "dark-factory" / "scripts" / "memory_write.py"
      writer.parent.mkdir(parents=True)
      shutil.copy(_WRITER, writer)
      artifacts = tmp_path / "artifacts"
      artifacts.mkdir()
      (artifacts / "issue.json").write_text(json.dumps({"resolved_number": 4242}),
                                            encoding="utf-8")
      run_env["ARTIFACTS_DIR"] = str(artifacts)

      proc = subprocess.run(["bash", "-c", block], cwd=str(clone), env=run_env,
                            capture_output=True, text=True)
      assert proc.returncode == 0, proc.stderr

      lines = (clone / ".archon" / "memory" / "architecture.md").read_text(
          encoding="utf-8").splitlines()
      entries = [l for l in lines if l.startswith("- [")]
      assert len(entries) == 1, lines
      assert entries[0].startswith(f"- [AVOID] {_LESSON} <!-- issue:#4242 ")
      assert "source:refine" in entries[0]
      assert "path:.archon/commands/" in entries[0]
  ```

  Notes for the implementer:
  - The environment of a factory run may already export `ARTIFACTS_DIR` (pointing at
    another run). The test overwrites it with its own fixture directory, and it strips
    `ISSUE_NUM`/`REPO_ROOT`/`LESSON_TEXT`/`MEMORY_WRITE` and every `GIT_*` variable, so
    the block must define everything it uses.
  - `textwrap.dedent` removes the 3-space list indentation. Inside the command file, the
    closing `LESSON` marker is indented, which is why the block's own comment says to run
    it without the list indentation.
  - The lesson uses balanced quote pairs only, so the test stays portable across bash
    versions.

- [ ] **Step 3.2 — verify it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_refine_command_memory_snippet.py -v
  ```
  Expected: all 4 **FAIL**. The current block has no `--kind avoid`, no heredoc and no
  placeholder line, the file still contains `Tool limitation (#652)`, and step 7b has no
  `--kind`.

- [ ] **Step 3.3 — rewrite the step 7a block.** In `commands/dark-factory-refine.md`,
  replace the block at lines 189-203, which currently reads exactly:

  ````
     ```bash
     REPO_ROOT=$(git rev-parse --show-toplevel)

     # Write the rejected approach to architecture.md.
     # memory_write.py handles expiry cleanup, dedup and the R4 cap internally.
     # Tool limitation (#652): memory_write.py always writes [AVOID] entries; both the
     # chosen-approach and rejected-approach lessons are written as [AVOID]. This is a
     # known tool constraint, not a silent substitution.
     python3 "${REPO_ROOT}/dark-factory/scripts/memory_write.py" \  # TARGET-PATH
       --target .archon/memory/architecture.md \
       --path-prefix .archon/commands/ \
       --text "<the rejected or chosen approach and concrete reasoning>" \
       --source refine \
       --issue "$ISSUE_NUM"
     ```
  ````

  with exactly (3-space list indentation preserved, 23 lines, now lines 189-211):

  ````
     ```bash
     REPO_ROOT=$(git rev-parse --show-toplevel)
     ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
     MEMORY_WRITE="${REPO_ROOT}/dark-factory/scripts/memory_write.py"  # TARGET-PATH

     # One call per lesson; memory_write.py handles expiry cleanup, dedup and the R4 cap.
     # The quoted heredoc keeps the lesson literal: shell variables, command
     # substitutions and backticks in it are NOT expanded. Keep the body on the lines
     # between the two LESSON markers; the closing marker stands alone at the start of
     # its line (run this block without the list indentation).
     # A weighed decision: --kind avoid, rejected approach FIRST. The [AVOID] tag names it.
     LESSON_TEXT=$(cat <<'LESSON'
     Rejected approach: <what was rejected and the concrete reason>. Chosen approach: <what to do instead>.
     LESSON
     )
     python3 "$MEMORY_WRITE" \
       --kind avoid \
       --target .archon/memory/architecture.md \
       --path-prefix .archon/commands/ \
       --text "$LESSON_TEXT" \
       --source refine \
       --issue "$ISSUE_NUM"
     ```
  ````

  Leave the two lines above the block ("a. For each architectural decision …" /
  "weighed (why approach X over approach Y), call `memory_write.py` once per lesson:")
  unchanged.

- [ ] **Step 3.4 — add the step 7b sentence.** Directly after the line
  ```
     Choose `--path-prefix` to match the convention's scope (e.g. `backend/app/` for backend).
  ```
  insert these three lines (same 3-space indentation; one sentence):
  ```
     Use the block above with the area file as `--target`, a matching `--path-prefix`, and
     `--kind pattern` with a positively stated body when the convention contains no prohibition
     or rejected alternative (otherwise `--kind avoid`, rejected approach first).
  ```

- [ ] **Step 3.5 — verify it passes and the edit stays inside 7a/7b.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_refine_command_memory_snippet.py tests/test_refine_command_updates.py tests/test_refine_command_render_prompt.py tests/test_command_issue_context_contract.py tests/test_command_headless_contract.py tests/test_command_issue_number_mandate.py tests/test_orchestrator_prompt_stub.py -v
  ```
  Expected: all pass.

  ```bash
  git diff -U0 -- commands/dark-factory-refine.md | grep '^@@'
  wc -l commands/dark-factory-refine.md
  ```
  Expected: exactly three hunks, `@@ -191,7 +191,15 @@`, `@@ -200 +208 @@` (step 7a
  block) and `@@ -208,0 +217,3 @@` (step 7b sentence), and
  `298 commands/dark-factory-refine.md` (was 287; +8 for the block, +3 for 7b). Line 94
  (`AVOID` entries are especially relevant …) is unchanged. The exact `@@` headers depend
  on git's diff algorithm (these are from the default `myers`). If a non-default
  `diff.algorithm` splits the hunks differently, the real test is that every hunk falls
  inside old lines 186-208 and the file has 298 lines.

- [ ] **Step 3.6 — commit.**

  ```bash
  git add commands/dark-factory-refine.md tests/test_refine_command_memory_snippet.py
  git commit -m "fix(#38): refine memory snippet passes --kind, keeps lesson literal"
  ```

## Task 4: Memory docs describe `--kind`

**Files:** `docs/dark-factory-memory-contract.md`, `docs/dark-factory-memory-v2.md`

Documentation-only. The red/green check is a grep that finds the stale claims before the
edit and finds none after it.

- [ ] **Step 4.1 — confirm the stale claims are present.**

  ```bash
  grep -n 'memory_write.py AVOID entry\|every refine entry is tagged\|always writes `- \[AVOID\]\|Writes new `\[AVOID\]` lessons\|(`\[AVOID\]` only)' docs/dark-factory-memory-contract.md docs/dark-factory-memory-v2.md
  ```
  Expected: 5 hits (contract `:70`, `:121`, `:159`; v2 `:25`, `:36`).

- [ ] **Step 4.2 — `docs/dark-factory-memory-contract.md` `:70`** (state diagram). Replace
  ```
      [*] --> active : memory_write.py AVOID entry, or implement agent shell append (runtime-proven)
  ```
  with
  ```
      [*] --> active : memory_write.py entry (PATTERN, AVOID or FIX), or implement agent shell append (runtime-proven)
  ```
  Mermaid transition labels must not contain `--`, so the label says "PATTERN, AVOID or
  FIX" rather than `--kind`.

- [ ] **Step 4.3 — contract `:121`** (Refine per-phase threshold row). In that row,
  replace the trailing sentence
  ```
  Written through `memory_write.py`, so every refine entry is tagged `[AVOID]` (see scoping matrix). |
  ```
  with
  ```
  Written through `memory_write.py`: a weighed decision as one `[AVOID]` entry that leads with the rejected approach, a convention with no prohibition as `[PATTERN]` (see "How each role writes"). |
  ```

- [ ] **Step 4.4 — contract `:155`** (Refine row of "How each role writes"). Replace
  ```
  | Refine | `memory_write.py`, called directly from the command | `[AVOID]` only |
  ```
  with
  ```
  | Refine | `memory_write.py`, called directly from the command | `[AVOID]`, `[PATTERN]`; `[FIX]` available but unused |
  ```
  Leave the Conformance/code-review row (`:156`, "`[AVOID]` only") unchanged. It is
  still true.

- [ ] **Step 4.5 — contract `:159-161`.** Replace the paragraph
  ```
  `memory_write.py` always writes `- [AVOID] …`; it never writes `[PATTERN]`, `[FIX]` or
  `[PROVISIONAL]`. Refine therefore records both the chosen and the rejected approach as
  `[AVOID]` entries, which the refine command documents as a known tool limitation.
  ```
  with these two paragraphs:
  ```
  `memory_write.py` writes one `- [PATTERN] …`, `- [AVOID] …` or `- [FIX] …` line per call,
  selected by `--kind {pattern,avoid,fix}` (default `avoid`; any other value exits 2 before
  anything is created or written). It never writes `[PROVISIONAL]` or `[INVALID]`, and
  reinforcing an existing line (normalized-text match) refreshes its dates without changing
  its tag. Refine passes `--kind` explicitly (`commands/dark-factory-refine.md`, Phase 5
  step 7): a decision where a trade-off was weighed is one `--kind avoid` entry whose body
  leads with `Rejected approach: … Chosen approach: …`, so the tag names the rejected
  approach; a convention with no prohibition and no rejected alternative is `--kind
  pattern`, stated positively; refine does not use `--kind fix`. The gate path passes no
  `--kind`: `gate_lib.sh::write_memory_entry()` relies on the default, so conformance and
  code-review entries stay `[AVOID]`.

  Reader behaviour: no script treats the three tags differently. `memory_retrieve.py`
  filters only `[PROVISIONAL]`/`[INVALID]`, expiry, source and path, and `memory_write.py`'s
  dedup, expiry and cap match all three tags, so a tag changes neither what is retrieved nor
  what is evicted. Three prompt texts do key on the tag: the AVOID rules in
  `commands/dark-factory-refine.md` and `commands/dark-factory-plan.md` ("if a memory entry
  marks an approach as AVOID …") and the architect's `[MEMORY-VIOLATION]` scan in
  `refinement-skills/architect-prompt.md`, which covers `[AVOID]` and `[FIX]` entries only. A
  refine `[PATTERN]` entry is still retrieved, and the plan command bakes `[PATTERN]` lessons
  into plan steps, but it is outside that scan and those rules; every lesson with a
  prohibition or a rejected alternative stays `[AVOID]` and inside them.
  ```
  These paragraphs deliberately carry no `file:line` citations, so later edits elsewhere
  cannot make them drift.

- [ ] **Step 4.6 — `docs/dark-factory-memory-v2.md`.** At `:25` (Writer row), replace
  `| Writes new `[AVOID]` lessons to the markdown files` with
  `| Writes new `[PATTERN]`/`[AVOID]`/`[FIX]` lessons (`--kind`, default `avoid`) to the markdown files`.
  The rest of the row is unchanged. At `:36` (Refine row), replace
  `| `memory_write.py` (`[AVOID]` only) |` with
  `| `memory_write.py` (`--kind avoid` or `--kind pattern`) |`.

- [ ] **Step 4.7 — verify.** Re-run the Step 4.1 grep. Expected: no output (exit 1).
  Then:
  ```bash
  grep -c -- '--kind' docs/dark-factory-memory-contract.md docs/dark-factory-memory-v2.md
  ```
  Expected: contract `≥ 5`, v2 `≥ 2`.

- [ ] **Step 4.8 — commit.**

  ```bash
  git add docs/dark-factory-memory-contract.md docs/dark-factory-memory-v2.md
  git commit -m "docs(#38): memory contract/v2 describe memory_write.py --kind"
  ```

## Task 5: Re-anchor moved `dark-factory-refine.md` citations

**Files:** `docs/features.md`, `docs/run-lifecycle.md`, `docs/configuration.md`

`scripts/docs_refs_check.py` only checks that a cited line exists, so CI cannot catch this
drift. Anchor each citation by content.

- [ ] **Step 5.1 — find the new line numbers by content** (a failing check: the old
  numbers no longer point at this content):

  ```bash
  grep -n 'python3 "\$MEMORY_WRITE"' commands/dark-factory-refine.md
  grep -n '^7\. Append memory entries' commands/dark-factory-refine.md
  grep -n 'skip the commit\.$' commands/dark-factory-refine.md
  grep -n '^## Phase 6: PUBLISH' commands/dark-factory-refine.md
  grep -n 'grep -q "direct-to-pr"' commands/dark-factory-refine.md
  grep -n '^   SPEC_GRACE=' commands/dark-factory-refine.md
  wc -l < commands/dark-factory-refine.md
  ```
  Expected: `204`, `176` (step 7, unchanged start), `230`, `232`, `241`, `242`, `298`.
  If any number differs, use the grep output in the steps below instead of these.

- [ ] **Step 5.2 — `docs/features.md:134`.** Replace `Write: `dark-factory-refine.md:197`;`
  with `Write: `dark-factory-refine.md:204`;`. Do not touch the other citations in the
  same cell (`gate_lib.sh:45`, `dark-factory-conformance.md:…`,
  `dark-factory-code-review.md:288`, `gate_lib.sh:11`). They point into files this
  ticket does not change. Main has since moved the conformance one from `:656` to
  `:652`, so take whatever value the feat branch already has.

- [ ] **Step 5.3 — `docs/run-lifecycle.md:536-537`.** Replace `- **Memory** (`:176-219`)`
  with `- **Memory** (`:176-230`)`, and `- **Publish** (`:221-287`)` with
  `- **Publish** (`:232-298`)`.

- [ ] **Step 5.4 — `docs/configuration.md`.** `:128`: replace
  `(`commands/dark-factory-refine.md:231`, agent-read)` with
  `(`commands/dark-factory-refine.md:242`, agent-read)`. `:131`: replace
  `hardcoded in `commands/dark-factory-refine.md:230` and` with
  `hardcoded in `commands/dark-factory-refine.md:241` and`. `:772`: replace
  `| `commands/dark-factory-refine.md:230`, ` with
  `| `commands/dark-factory-refine.md:241`, ` (the row's first citation only).
  Do not touch the `commands/dark-factory-plan.md:…` citation that follows it on `:772`,
  nor the ones on `:129` and `:132`: #405 re-points them (`:390` → `:423`, `:391` →
  `:424`), so match on the `dark-factory-refine.md` text alone and keep whatever
  `dark-factory-plan.md` value the branch already has.

- [ ] **Step 5.5 — verify.**

  ```bash
  python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  sed -n '204p;176p;230p;232p;241p;242p;298p' commands/dark-factory-refine.md
  grep -rn 'dark-factory-refine.md:\(197\|230\|231\)\b\|:176-219\|:221-287' docs/features.md docs/run-lifecycle.md docs/configuration.md
  ```
  Expected: `Docs reference check passed for 4 doc(s).` The `sed` output shows, in file
  order: `7. Append memory entries …` (176), `python3 "$MEMORY_WRITE" \` (204),
  `new), skip the commit.` (230), `## Phase 6: PUBLISH` (232), the
  `grep -q "direct-to-pr"` line (241), the `SPEC_GRACE=` line (242), and the closing
  `` ``` `` of the status block (298). The final `grep` prints nothing.
  Citations of `dark-factory-refine.md` lines above `:186` (e.g. `:171`, `:132`, `:69`,
  `:100-105`, `:143-147`) did not move and stay as they are. `docs/domain.md` needs no edit.

- [ ] **Step 5.6 — commit.**

  ```bash
  git add docs/features.md docs/run-lifecycle.md docs/configuration.md
  git commit -m "docs(#38): re-anchor dark-factory-refine.md citations after snippet rewrite"
  ```

## Task 6: Final verification against the acceptance criteria

**Files:** none (verification only; nothing to commit unless a check fails)

- [ ] **Step 6.1 — AC1 (CLI behaviour), smoke run.**

  ```bash
  rm -rf /tmp/kind-ac1 && mkdir -p /tmp/kind-ac1
  for k in pattern fix avoid; do python3 scripts/memory_write.py --target /tmp/kind-ac1/$k.md --path-prefix docs/ --text "lesson $k" --source refine --issue 38 --kind $k; done
  python3 scripts/memory_write.py --target /tmp/kind-ac1/none.md --path-prefix docs/ --text "lesson none" --source refine --issue 38
  python3 scripts/memory_write.py --target /tmp/kind-ac1/bad.md --path-prefix docs/ --text "lesson bad" --source refine --issue 38 --kind AVOID; echo "bad rc=$?"
  head -c 14 /tmp/kind-ac1/*.md; echo; ls /tmp/kind-ac1
  ```
  Expected: `fix.md` starts with `- [FIX] lesson`, `pattern.md` with `- [PATTERN] lesson`,
  and `avoid.md`/`none.md` with `- [AVOID] lesson`. Also `bad rc=2`, and there is no
  `bad.md`.

- [ ] **Step 6.2 — AC2 (forbidden surfaces untouched).** Use the spec's **three-dot**
  form, which diffs against the merge-base and so shows only this branch's own changes.
  The two-dot form (`origin/main HEAD`) would also list every file main changed after
  the branch forked. During planning it flagged `commands/dark-factory-conformance.md`
  for exactly this reason (main merged #663), even though this branch never touched it.
  The `codebase-patterns.md` memory (#250) recommends two-dot for a different question:
  whether a file is net-identical to main, as used by OOS triage. If Step 6.2 lists a
  file, run the two-dot form on just that file to tell the two cases apart.

  ```bash
  git fetch -q origin main
  git diff --stat origin/main...HEAD -- scripts/gate_lib.sh scripts/memory_retrieve.py refinement-skills/ commands/dark-factory-plan.md commands/dark-factory-conformance.md commands/dark-factory-code-review.md commands/dark-factory-implement.md .github/ workflows/ config/ deploy/
  ```
  Expected: no output.

- [ ] **Step 6.3 — AC3 (no hand edits to memory).**

  ```bash
  git log --oneline origin/main..HEAD -- .archon/memory/
  git diff origin/main...HEAD -- .archon/memory/ | grep '^-[^-]'
  ```
  Expected for the `git log`: none of the Task 1-5 commits (`feat(#38)…`, `test(#38)…`, `fix(#38)…`,
  `docs(#38): memory contract…`, `docs(#38): re-anchor…`) is listed. Only refine-phase
  commits carried over from the refine branch (if any), or entries written later by this
  ticket's implement/conformance/code-review phases through their normal mechanisms,
  may appear. Expected for the `grep`: no removed line, other than an expiry or cap
  eviction made by `memory_write.py` itself or a line a carried-over refine-phase commit
  replaced. Never a line removed or retagged by hand.

- [ ] **Step 6.4 — AC4 (refine command changes only inside step 7a/7b).**

  ```bash
  git diff -U0 origin/main...HEAD -- commands/dark-factory-refine.md | grep '^@@'
  ```
  Expected: the same three hunks as Step 3.5, all inside old lines 186-208 (step 7a/7b).

- [ ] **Step 6.5 — AC5/AC6 and the hand-run shell tests.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -v 2>&1 | tail -3
  bash tests/test_memory_write_gate.sh | tail -1
  bash tests/test_conformance_memory_write.sh | tail -1
  git diff --quiet origin/main...HEAD -- .github/workflows/ci.yml && echo "ci.yml unchanged"
  ```
  Expected: the full suite passes with no failures (about 4530 tests on the current
  branch). The two shell tests are not run by CI but must still pass:
  `Results: 8 passed, 0 failed` and `Results: 14 passed, 0 failed`. Last line:
  `ci.yml unchanged`.

- [ ] **Step 6.6 — scope check.**

  ```bash
  git diff --name-only origin/main...HEAD
  ```
  Expected: the nine files in the File Structure table, plus the refine/plan artifacts
  carried onto the branch (`docs/superpowers/specs/2026-11-28-memory-write-kind-tag-design.md`,
  this plan, and `.archon/memory/*.md` only if carried over or written by a normal
  mechanism). Nothing else.

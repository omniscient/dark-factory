# Implementation Plan: memory_write.py — create `.archon/memory` on first run, resolve `project` dynamically

**Issue:** #444

**Spec:** `docs/superpowers/specs/2026-09-27-issue-444-memory-write-first-run-and-project-name-design.md`
**Related:** #443 (parent; Wave 3 keeps the sibling `markethawk` hardcodes and the implement
phase's shell-append memory path)

---

## Goal

Fix the two defects in `scripts/memory_write.py`:

1. On a fresh target, `.archon/memory/` does not exist, and nothing creates it. The markdown
   write fails with `FileNotFoundError`, exits 1, and every best-effort call site silently
   drops the memory. Fix: `target.parent.mkdir(parents=True, exist_ok=True)`, placed after
   the empty-`--text` guard. It prints `memory-write: created <abs dir>` when it created the
   directory. If the mkdir fails, the script prints one stderr line and exits 1, with no
   traceback.
2. `_write_index()` hardcodes `"project": "markethawk"`. Fix: resolve the value with
   `FACTORY_REPO` → `FACTORY_PRODUCT_NAME` → `git -C <target.parent> remote get-url origin`
   (bare repo name) → `"unknown"`. Print a one-line stderr warning on the `unknown` rung and
   another when the env-derived name does not match the git origin (case-insensitive
   compare). Never echo the remote URL.

## Architecture

The runtime change is limited to one file, `scripts/memory_write.py`:

- Top-level imports gain `import os` and `import subprocess` (both stdlib). **Both are
  required.** The new handler `except (OSError, subprocess.SubprocessError)` dereferences
  `subprocess` in the except clause itself. If only `os` were imported, every write would die
  with an uncaught `NameError` (spec, Architecture §2).
- New `_git_origin_name(start_dir)`: returns the bare repo name from `origin`, or `None`.
  It never raises. The URL stays inside this function; only the derived name is returned.
- New `_resolve_project(start_dir)`: implements the fallback chain and both warnings.
- `_write_index()` gets a new trailing `project` parameter in place of the literal. It stays a
  pure function of its inputs.
- `main()` gets two additions:
  - the mkdir block, between the empty-`--text` guard and the `target.is_dir()` check;
  - `project = _resolve_project(target.parent)`, computed once, directly before the single
    `_write_index(...)` call inside the existing `if not skip_index:` branch.

  Because the resolver is called only on the path that actually writes an `index.jsonl` row,
  a dedup-reinforce or cap-skip run spends no `git` subprocess and emits no provenance
  warning for a row it never writes. It still runs at most once per invocation, as the spec
  requires, and it runs after the mkdir, so `target.parent` exists when `git -C` runs.

There are two supporting edits. The tests go in `tests/test_memory_write.py`. The docs change
is the one-line `project` row in `docs/dark-factory-memory-contract.md`, which the spec's Open
Questions hands to this ticket's PR.

Out of scope (spec, "Knowingly Deferred"). Do **not** touch any of these:

- `scripts/memory_import.py:236`
- `scripts/memory_retrieve.py:563`
- their tests: `tests/test_memory_import.py:233` and `tests/test_memory_retrieve.py:994,1019`
- `commands/dark-factory-implement.md`'s shell-append memory path
- any existing `index.jsonl` rows
- `tests/test_memory_write_gate.sh`

## Tech Stack

Python 3 stdlib only (`argparse`, `os`, `re`, `subprocess`, `pathlib`, …). Tests use pytest's
`tmp_path` and `monkeypatch`, and invoke the script as a subprocess through the existing
`run()` helper, which inherits `os.environ`. Some tests shell out to the real `git` binary to
build fixture repos. They are skipped if `git` is not on `PATH`.

## Memory lessons applied

- Two-dot OOS check (`codebase-patterns.md`, #250): Task 5 uses
  `git diff origin/main HEAD --stat` (two-dot) to confirm that only the three planned files
  changed.
- Spec path durability (`codebase-patterns.md`, #42): the spec
  `docs/superpowers/specs/2026-09-27-issue-444-…-design.md` stays where it is. Only this plan
  may be archived later. Nothing in this plan references the spec from tests or the README.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/memory_write.py` | Modify | Add `os`/`subprocess` imports, `_git_origin_name()`, `_resolve_project()`, the `project` param on `_write_index()`, and the mkdir block plus resolver call in `main()` |
| `tests/test_memory_write.py` | Modify | Make the existing `project` assertion env-injected. Add `TestDirectoryCreation` (4 tests) and `TestProjectResolution` (11 tests) plus a `_git_repo()` helper |
| `docs/dark-factory-memory-contract.md` | Modify (1 line) | Replace the stale "hardcoded by convention" `project` row with the resolution chain |

No other file is touched.

---

## Task 1: Create `.archon/memory/` on first write

**Files:** `tests/test_memory_write.py`, `scripts/memory_write.py`

- [ ] **Step 1.1: Write the failing tests.** Append this class to `tests/test_memory_write.py`,
  after `TestIndexJsonl` and before the `# ── sanitization` banner:

```python
# ── first-run directory creation (#444) ─────────────────────────────────────

class TestDirectoryCreation:
    def test_fresh_target_creates_dir_markdown_and_index(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        target = mem_dir / "backend-patterns.md"
        assert not mem_dir.exists()
        result = run("--target", str(target), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "refine", "--issue", "444")
        assert result.returncode == 0, result.stderr
        assert "[AVOID] avoid mocks" in target.read_text()
        assert (mem_dir / "index.jsonl").exists()
        assert f"memory-write: created {mem_dir.resolve()}" in result.stdout

    def test_existing_dir_prints_no_created_line(self, md_empty):
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "refine", "--issue", "444")
        assert result.returncode == 0
        assert "memory-write: created" not in result.stdout

    def test_empty_text_does_not_create_dir(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        result = run("--target", str(mem_dir / "backend-patterns.md"),
                     "--path-prefix", "backend/app/", "--text", "",
                     "--source", "refine", "--issue", "444")
        assert result.returncode == 1
        assert not mem_dir.exists()
        assert not (tmp_path / "fresh").exists()

    def test_mkdir_failure_is_one_stderr_line_exit_1(self, tmp_path):
        # .archon/memory exists as a FILE: exist_ok=True does not swallow FileExistsError.
        archon = tmp_path / ".archon"
        archon.mkdir()
        (archon / "memory").write_text("not a directory\n")
        result = run("--target", str(archon / "memory" / "backend-patterns.md"),
                     "--path-prefix", "backend/app/", "--text", "avoid mocks",
                     "--source", "refine", "--issue", "444")
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        lines = result.stderr.strip().splitlines()
        assert len(lines) == 1, result.stderr
        assert lines[0].startswith(f"memory-write: error: cannot create {archon / 'memory'}: ")
```

- [ ] **Step 1.2: Verify the tests fail.**

```bash
python -m pytest tests/test_memory_write.py::TestDirectoryCreation -v
```

  Expected: `test_fresh_target_creates_dir_markdown_and_index` FAILS (returncode 1,
  `memory-write: error writing … No such file or directory`).
  `test_mkdir_failure_is_one_stderr_line_exit_1` FAILS: stderr is
  `memory-write: error writing …: [Errno 20] Not a directory`, which does not start with
  `memory-write: error: cannot create`. The other two already pass, since they are guards
  against regressions this task could introduce. Result: `2 failed, 2 passed`.

- [ ] **Step 1.3: Implement.** In `scripts/memory_write.py` `main()`, find this block:

```python
    if not args.text.strip():
        print("memory-write: error: --text is empty", file=sys.stderr)
        sys.exit(1)
```

  Insert the following directly after it, before the `# Sanitize: collapse whitespace …`
  comment. That keeps it after the empty-text guard and before the `target.is_dir()` check,
  per spec Requirement 1:

```python

    # Create .archon/memory/ on a fresh target (#444). Placed after the empty-text
    # guard so an invalid-input run never creates the directory as a side effect.
    # OSError also covers FileExistsError when .archon/memory is a file, which
    # exist_ok=True does not swallow.
    created = not target.parent.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"memory-write: error: cannot create {target.parent}: {exc}", file=sys.stderr)
        sys.exit(1)
    if created:
        print(f"memory-write: created {target.parent.resolve()}")
```

- [ ] **Step 1.4: Verify the tests pass.**

```bash
python -m pytest tests/test_memory_write.py -v
```

  Expected: every test passes. That is the 4 new tests plus all 33 pre-existing ones
  (`37 passed`; `test_index_io_error_is_nonfatal` may instead be the one `failed` if the
  suite runs as root, where `chmod 000` does not block writes. That is pre-existing and
  unrelated, so confirm it also fails on `origin/main` before moving on).

- [ ] **Step 1.5: Commit.**

```bash
git add scripts/memory_write.py tests/test_memory_write.py
git commit -m "fix(memory): #444 — memory_write.py creates .archon/memory on first run"
```

---

## Task 2: Resolve `project` from env → git origin → `unknown`, with warnings

**Files:** `tests/test_memory_write.py`, `scripts/memory_write.py`

- [ ] **Step 2.1: Make the existing assertion env-injected** (spec Requirement 5). In
  `TestIndexJsonl.test_index_record_has_required_fields`, change the signature to take
  `monkeypatch`, and inject the project explicitly. Replace:

```python
    def test_index_record_has_required_fields(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
```

  with:

```python
    def test_index_record_has_required_fields(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "jobfinder")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
```

  and replace `        assert record["project"] == "markethawk"` with
  `        assert record["project"] == "jobfinder"`.

- [ ] **Step 2.2: Add the test-file imports and git helper.** Add `import shutil` to the
  import block at the top of `tests/test_memory_write.py`, placed alphabetically between
  `import re` and `import subprocess`. Then add the following directly after the existing
  `run()` helper, before the `# ── fixtures` banner:

```python
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git_repo(path, origin=None):
    """Create a real git repo at *path*, optionally with an origin remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if origin is not None:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin],
                       check=True, capture_output=True)
    return path


def _project_of(target):
    """The project field of the single index.jsonl row next to *target*."""
    lines = (target.parent / "index.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1, lines
    return json.loads(lines[0])["project"]
```

- [ ] **Step 2.3: Write the failing tests.** Append this class directly after
  `TestDirectoryCreation`, before the `# ── sanitization` banner:

```python
# ── project resolution (#444) ───────────────────────────────────────────────

MISMATCH = "memory-write: WARNING: project '"
UNRESOLVED = (
    "memory-write: WARNING: project unresolved (FACTORY_REPO/FACTORY_PRODUCT_NAME "
    "unset, no git origin) - writing project:unknown"
)


def _write(target, text="avoid mocks"):
    return run("--target", str(target), "--path-prefix", "backend/app/",
               "--text", text, "--source", "refine", "--issue", "444")


class TestProjectResolution:
    # ── env rungs: tmp_path is typically not a git worktree (git lookup → None) ──

    def test_factory_repo_wins(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "jobfinder")
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        result = _write(md_empty)
        assert result.returncode == 0
        assert _project_of(md_empty) == "jobfinder"

    def test_product_name_used_as_is_when_repo_unset(self, md_empty, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        result = _write(md_empty)
        assert result.returncode == 0
        assert _project_of(md_empty) == "MarketHawk"

    def test_blank_factory_repo_falls_through(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "   ")
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        _write(md_empty)
        assert _project_of(md_empty) == "MarketHawk"

    # ── git-origin rung ──

    @requires_git
    def test_git_origin_https(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0, result.stderr
        assert _project_of(target) == "jobfinder"
        assert "WARNING" not in result.stderr

    @requires_git
    def test_git_origin_scp_style_without_path_slash(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "git@github.com:jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        _write(target)
        assert _project_of(target) == "jobfinder"

    # ── terminal rung ──

    @requires_git
    def test_unknown_when_no_env_and_no_origin(self, tmp_path, monkeypatch):
        # A fresh repo with NO origin: `git remote get-url origin` exits non-zero
        # regardless of any git ancestry the host has.
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "unknown"
        assert "Traceback" not in result.stderr
        assert result.stderr.strip().splitlines() == [UNRESOLVED]

    # ── mismatch warning (Requirement 6) ──

    @requires_git
    def test_env_vs_origin_mismatch_warns_once_and_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "markethawk")
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "markethawk"
        assert result.stderr.strip().splitlines() == [
            "memory-write: WARNING: project 'markethawk' does not match git origin "
            "'jobfinder' (FACTORY_REPO defaulted?)"
        ]

    @requires_git
    def test_case_only_difference_does_not_warn(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/markethawk.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "MarketHawk"
        assert MISMATCH not in result.stderr

    @requires_git
    def test_no_mismatch_warning_on_dedup_skip(self, tmp_path, monkeypatch):
        # Resolver runs only when an index row is written; a reinforce is silent.
        monkeypatch.setenv("FACTORY_REPO", "markethawk")
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        _write(target)
        result = _write(target, text="AVOID MOCKS.")
        assert result.returncode == 0
        assert MISMATCH not in result.stderr

    # ── security rider: never echo the remote URL (token lives in it) ──

    @requires_git
    def test_token_in_origin_url_never_printed(self, tmp_path, monkeypatch):
        secret = "ghp_SECRETTOKEN444"
        monkeypatch.setenv("FACTORY_REPO", "markethawk")  # force the mismatch diagnostic
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo",
                         f"https://{secret}@github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert "'jobfinder'" in result.stderr
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert secret not in (target.parent / "index.jsonl").read_text()

    @requires_git
    def test_token_in_origin_url_not_recorded_via_git_rung(self, tmp_path, monkeypatch):
        secret = "ghp_SECRETTOKEN444"
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo",
                         f"https://{secret}@github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert _project_of(target) == "jobfinder"
        assert secret not in result.stdout + result.stderr
```

- [ ] **Step 2.4: Verify the tests fail.**

```bash
python -m pytest tests/test_memory_write.py::TestProjectResolution tests/test_memory_write.py::TestIndexJsonl::test_index_record_has_required_fields -v
```

  Expected failures. Each test gets `"markethawk"` back from the hardcode, or finds no
  warning line in stderr:
  - `test_index_record_has_required_fields`
  - `test_factory_repo_wins`
  - `test_product_name_used_as_is_when_repo_unset`
  - `test_blank_factory_repo_falls_through`
  - `test_git_origin_https`
  - `test_git_origin_scp_style_without_path_slash`
  - `test_unknown_when_no_env_and_no_origin`
  - `test_env_vs_origin_mismatch_warns_once_and_env_wins`
  - `test_case_only_difference_does_not_warn`
  - `test_token_in_origin_url_never_printed`
  - `test_token_in_origin_url_not_recorded_via_git_rung`

  `test_no_mismatch_warning_on_dedup_skip` passes vacuously today, as a regression guard.
  Result: `11 failed, 1 passed`.

- [ ] **Step 2.5: Add the imports.** In `scripts/memory_write.py`, change the import block
  from:

```python
import argparse
import calendar
import json
import re
import sys
from datetime import date
from pathlib import Path
```

  to:

```python
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
```

- [ ] **Step 2.6: Add the resolver functions.** Insert both directly above
  `def _write_index(`:

```python
def _git_origin_name(start_dir):
    """Bare repo name from origin, or None. Never raises; never echoes the URL.

    In run containers origin embeds the GitHub token (entrypoint.sh clones from
    https://${GH_TOKEN}@github.com/...), so the URL must never leave this function.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(start_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    if not url:
        return None
    # Split on ':' too, so scp-style remotes with no path slash still resolve.
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or None


def _resolve_project(start_dir):
    """index.jsonl project: FACTORY_REPO -> FACTORY_PRODUCT_NAME -> git origin -> "unknown".

    Every outcome returns a string; unresolved or mismatched values warn on stderr
    (one line) instead of failing the write — the field is provenance only.
    """
    env_name = (os.environ.get("FACTORY_REPO", "").strip()
                or os.environ.get("FACTORY_PRODUCT_NAME", "").strip())
    git_name = _git_origin_name(start_dir)
    if env_name:
        if git_name and git_name.lower() != env_name.lower():
            print(
                f"memory-write: WARNING: project '{env_name}' does not match git origin "
                f"'{git_name}' (FACTORY_REPO defaulted?)",
                file=sys.stderr,
            )
        return env_name
    if git_name:
        return git_name
    print(
        "memory-write: WARNING: project unresolved (FACTORY_REPO/FACTORY_PRODUCT_NAME "
        "unset, no git origin) - writing project:unknown",
        file=sys.stderr,
    )
    return "unknown"
```

- [ ] **Step 2.7: Thread `project` through `_write_index()`.** Change:

```python
def _write_index(index_path, args, agent_id, scope, today_str, expires_str):
    record = {
        "project": "markethawk",
```

  to:

```python
def _write_index(index_path, args, agent_id, scope, today_str, expires_str, project):
    record = {
        "project": project,
```

- [ ] **Step 2.8: Call the resolver from `main()`.** Change the Step 6 block at the end of
  `main()` from:

```python
    if not skip_index:
        _write_index(target.parent / "index.jsonl", args, agent_id, scope, today_str, expires_str)
```

  to:

```python
    if not skip_index:
        project = _resolve_project(target.parent)
        _write_index(
            target.parent / "index.jsonl", args, agent_id, scope, today_str, expires_str, project
        )
```

- [ ] **Step 2.9: Verify the tests pass.** Run with both vars unset, then with the values a
  run container sets (spec Requirement 4):

```bash
env -u FACTORY_REPO -u FACTORY_PRODUCT_NAME python -m pytest tests/test_memory_write.py -v
FACTORY_REPO=dark-factory FACTORY_PRODUCT_NAME=MarketHawk python -m pytest tests/test_memory_write.py -v
```

  Expected: both runs report `48 passed`. That is 33 pre-existing, 4 from Task 1, and 11 from
  this task. The only allowed exception is the pre-existing root/`chmod` caveat noted in
  Step 1.4.

- [ ] **Step 2.10: Import-sanity smoke check.** This guards the `NameError` failure mode the
  spec calls out:

```bash
python3 -c "import ast,sys; t=ast.parse(open('scripts/memory_write.py').read()); names={a.name for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names}; sys.exit(0 if {'os','subprocess'} <= names else 1)" && echo imports-ok
```

  Expected output: `imports-ok`

- [ ] **Step 2.11: Commit.**

```bash
git add scripts/memory_write.py tests/test_memory_write.py
git commit -m "fix(memory): #444 — resolve index.jsonl project from FACTORY_REPO/FACTORY_PRODUCT_NAME/git origin"
```

---

## Task 3: Update the stale `project` row in the memory contract

**Files:** `docs/dark-factory-memory-contract.md`

This is a docs-only change and has no test. It is the one-line fix that the spec's Open
Questions hands to this PR.

- [ ] **Step 3.1: Edit.** Replace line 34:

```markdown
| `project` | string | *(implicit — file lives in `omniscient/markethawk` repo; hardcoded by convention)* |
```

  with:

```markdown
| `project` | string | *(none in flat-file; `index.jsonl` only — `memory_write.py` resolves `FACTORY_REPO` → `FACTORY_PRODUCT_NAME` → git `origin` repo name → `unknown`, #444)* |
```

- [ ] **Step 3.2: Verify.**

```bash
grep -n '^| `project`' docs/dark-factory-memory-contract.md
```

  Expected: exactly one line, containing `FACTORY_REPO` and `#444`, with no
  `hardcoded by convention`.

- [ ] **Step 3.3: Commit.**

```bash
git add docs/dark-factory-memory-contract.md
git commit -m "docs(memory): #444 — contract: project is resolved, not hardcoded"
```

---

## Task 4: End-to-end first-run smoke (manual, no commit)

**Files:** none (scratch dir only)

- [ ] **Step 4.1: Simulate a fresh jobfinder target.**

```bash
T=$(mktemp -d) && git init -q "$T" && git -C "$T" remote add origin https://x-token@github.com/omniscient/jobfinder.git
env -u FACTORY_REPO -u FACTORY_PRODUCT_NAME python3 scripts/memory_write.py \
  --target "$T/.archon/memory/backend-patterns.md" --path-prefix backend/ \
  --text "smoke lesson" --source refine --issue 444; echo "rc=$?"
jq -r .project "$T/.archon/memory/index.jsonl"
FACTORY_REPO=markethawk python3 scripts/memory_write.py \
  --target "$T/.archon/memory/backend-patterns.md" --path-prefix backend/ \
  --text "second lesson" --source refine --issue 444 2>&1 | grep -c x-token; rm -rf "$T"
```

  Expected output, in order:
  1. `memory-write: created /tmp/…/.archon/memory`
  2. `rc=0`
  3. `jobfinder`
  4. `0`, meaning the token never appears, even in the mismatch warning.

---

## Task 5: Full verification

**Files:** none

- [ ] **Step 5.1: Run the full suite, the way CI does.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

  Expected: no new failures compared with `origin/main`. In particular, `test_memory_import.py`
  and `test_memory_retrieve.py` still assert `"markethawk"` for their own out-of-scope
  hardcodes and must stay green untouched.

- [ ] **Step 5.2: Scope check.** Use the two-dot form (see the memory lesson from #250):

```bash
git diff origin/main HEAD --stat -- . ':!docs/superpowers/'
```

  Expected: exactly three files:
  - `docs/dark-factory-memory-contract.md`
  - `scripts/memory_write.py`
  - `tests/test_memory_write.py`

- [ ] **Step 5.3: Confirm the sibling hardcodes are untouched** (deferred to #443 Wave 3).

```bash
git diff origin/main HEAD -- scripts/memory_import.py scripts/memory_retrieve.py commands/ tests/test_memory_write_gate.sh | wc -l
```

  Expected: `0`

---

## Self-review

- `**Issue:** #444` line present directly under the title: ✅
- No placeholders. Every code step has literal code, and every command has its expected
  output: ✅
- Spec coverage:
  - Req 1 (mkdir placement, `created` line): Task 1
  - Req 2 (fallback chain, scp split, `unknown`): Task 2
  - Req 3 (no schema change): `project` stays a string, and only the contract's mapping-column
    prose changes (Task 3)
  - Req 4 (tests per rung, env-controlled, both env states, deterministic git fixtures, nothing
    added to `test_memory_write_gate.sh`): Tasks 1–2, Step 2.9, Step 5.3
  - Req 5 (env-injected existing assertion): Step 2.1
  - Req 6 (single-line mkdir error, `unknown` warning, case-insensitive mismatch warning, no URL
    echo): Steps 1.3, 2.3, and 2.6
  - Both imports: Steps 2.5 and 2.10
  - Open Question (contract doc): Task 3

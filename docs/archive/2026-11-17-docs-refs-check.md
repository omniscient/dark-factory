# Deterministic CI checks for reference docs — Implementation Plan

**Issue:** #537

**Goal:** Add `scripts/docs_refs_check.py`, a deterministic (no model call) CI check that fails a
pull request when `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` or
`docs/configuration.md` points at a path, line range, config key or workflow node that no longer
exists — and make the four docs pass it on `main`.

**Architecture:** One dependency-light script (`pyyaml` only, already installed in the `dag-check`
job) with three independently testable internal checks — `_check_path_refs` (all four docs),
`_check_config_keys` (`configuration.md` only) and `_check_feature_nodes` (`features.md` only) —
dispatched per doc by file name from a `check(doc_paths, repo_root, config_path, workflow_path)
-> list[str]` library function, plus a `main()` CLI that prints every error and exits 1. Same
shape as `scripts/check_workflow_dag.py` / `scripts/check_workflow_when.py`, and wired as one more
step in the existing `dag-check` job.

**Tech Stack:** Python 3.12, `pyyaml`, `git ls-files` (with a directory-walk fallback), pytest.

**Spec:** [`docs/superpowers/specs/2026-11-17-docs-refs-check-design.md`](../specs/2026-11-17-docs-refs-check-design.md)

---

## Planning findings that shape the implementation

All of the code below was prototyped against the real four docs, both in this run's container
and in a fresh `git clone` (which is what the CI `dag-check` job sees), before this plan was
written. Each of the decisions below fixes something a literal reading of the spec got
wrong on the real docs. Each one is a narrowing or precision fix, not a scope change.

1. **The file index comes from git, not a raw directory walk.** The spec's walk with a
   hardcoded exclusion list gives different results in a run container and in CI. A run stages
   ignored files into its clone (`.archon/commands/`, `.archon/workflows/`,
   `.claude/skills/refinement/config.yaml`, `.claude/settings.local.json`, the nested
   `dark-factory/`; see `git status --ignored`). Two of them duplicate real basenames, so
   `` `config.yaml` `` and `` `dark-factory-refine.md:171` `` become falsely ambiguous locally
   but resolve fine in CI. `_repo_files()` therefore lists `git ls-files -co --exclude-standard`
   (tracked files plus untracked files that are not ignored). That is exactly what CI checks out,
   plus files a new branch has not committed yet. It falls back to `rglob` only when the root is
   not in a git repo (the `tmp_path` unit tests). The spec's exclusions (`.git`, `node_modules`,
   `.venv`, `__pycache__`, nested `dark-factory/`) are still applied on top.
   - `dark-factory/` is excluded **only at the top level**, because
     `deploy/instances/dark-factory/instance.env` is a real tracked file.
   - `tests/fixtures/` is left out of the basename/suffix search (not out of literal-path
     resolution). The new fixture trees copy the repo layout (`docs/features.md`,
     `config/config.yaml`, …), and real refs such as `` `features.md` `` would otherwise become
     ambiguous.
2. **Path candidates are tightened so the gate has no false positives.** A naive "contains `/` or
   ends in a known extension" rule reports 275 errors on the current docs. Almost all of them are
   not repo paths: `origin/main`, `frontend/`, `/opt/...`, run artifacts such as
   `` `implementation.md` ``, and `.archon/.env`. The rules that bring the current docs down to
   the 3 genuine breaks:
   - A span with a `/` counts as a path only when its first segment is a top-level entry of the
     repo index (`scripts/`, `docs/`, `.archon/`, …). Spans starting with `/` or `~`, containing
     `..`, or containing glob/template characters (`*`, `<`, `$`, `{`) never match `_REF`.
   - `NON_REPO_PATHS` skips the operator-local gitignored files (`.archon/.env`,
     `deploy/instance.env`) and the run-staged files listed in point 1. A fresh clone has none of
     them, but the docs describe them on purpose.
   - A **bare file name with no line number** that matches nothing is skipped. These are run
     artifacts (`` `blast.md` ``) or target-repo files (`` `ARCHITECTURE.md` ``). A bare name
     **with** a line number that matches nothing is still an error, and so is any multi-segment
     path that matches nothing.
   - Basename search is generalized to a **path-suffix search**. `` `providers/tracker/github.py` ``
     resolves to `scripts/factory_core/providers/tracker/github.py`, where a basename search on
     `github.py` would be ambiguous between tracker and codehost. For a one-segment span this is
     exactly the spec's basename search.
3. **Config-key rows need full keys.** In the `### token_optimization` table of
   `docs/configuration.md`, keys are written relative to the section (`enforce_budgets`,
   `budgets.refine`, `.plan`, `.implement` in one cell). Under the spec's rule (Requirement 3:
   first cell is one full dotted key) that is 19 "documented but missing" plus 30 "undocumented"
   errors. Task 5 rewrites that table to one fully qualified key per row (30 rows). The key
   regex is also widened from `[a-z_]+` to `[a-z_][a-z0-9_-]*` per segment, because
   `token_optimization.budgets.code-review` is a real leaf.
4. **The node lead-in matches lower case too** (`` workflow node `parse-intent` ``,
   `` nodes `budget-refine` … ``), with a `(?<![\w-])` look-behind so `` Per-node `model:` `` is
   not a lead-in. All 33 extracted ids currently exist.
5. **Fixture trees copy the repo layout** (`docs/configuration.md`, `docs/features.md`,
   `config/config.yaml`, `workflows/archon-dark-factory.yaml`, `scripts/tool.sh`) instead of the
   spec's `{doc.md,config.yaml,workflow.yaml}`, because the checks are dispatched on the file
   names `configuration.md` and `features.md`. The `fail/` tree's config break is the "key added
   without documentation" direction. The other direction (documented key removed) is covered by
   its own unit test.
6. **Real breakage to fix (Requirement 7):** the three bare `cli.py` refs
   (`docs/scheduler.md`, `docs/features.md`, `docs/configuration.md`) are ambiguous between
   `scripts/factory_core/cli.py` and `scripts/factory_core/providers/cli.py`, and all three mean
   the former (verified: `scripts/factory_core/cli.py:47` is `_breaker_get`'s `STATE_FILE` read,
   `:115` is `loop_entry=None`). The token_optimization table is the other break. No line
   reference is currently out of range.

Measured result: with Tasks 1–6 applied, the CI command passes on the four docs in both
environments. The full `pytest tests/` suite passes (2538 tests in the prototype). Mutation
checks fail as intended: deleting `scripts/scheduler_lib.sh`, truncating `scheduler.sh` to 900
lines, adding an undocumented `config.yaml` key, and renaming the `revise-advisory` node.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/docs_refs_check.py` | Create | `check()` library function + CLI; path/line, config-key and workflow-node checks |
| `tests/test_docs_refs_check.py` | Create | Unit tests per check (pass + fail each) plus pass/fail fixture-tree and CLI tests |
| `tests/fixtures/docs_refs_check/pass/**` | Create | Synthetic repo-shaped tree on which every check passes |
| `tests/fixtures/docs_refs_check/fail/**` | Create | Same tree with exactly one break per check category (4 errors) |
| `docs/configuration.md` | Modify | Full-key `token_optimization` rows; one-sentence note on the check; `cli.py:47` → full path |
| `docs/features.md` | Modify | `## CI` row names the new check; `cli.py:115` → full path |
| `docs/scheduler.md` | Modify | `` (`cli.py`, `` → `` (`factory_core/cli.py`, `` |
| `.github/workflows/ci.yml` | Modify | One new step in the existing `dag-check` job (job name unchanged) |

Nothing else is touched. In particular: no change to `scripts/check_workflow_*.py`,
`.factory/hooks/*`, any `gate_*`, breaker or budget code, `deploy/instances/**` or
`.github/workflows/publish.yml`.

Every command below runs from the repo root of the clone. Tests run with `PYTHONPATH=scripts` as
in CI.

---

## Task 1: Fixture trees

**Files:**
- Create: `tests/fixtures/docs_refs_check/pass/scripts/tool.sh`
- Create: `tests/fixtures/docs_refs_check/pass/workflows/archon-dark-factory.yaml`
- Create: `tests/fixtures/docs_refs_check/pass/config/config.yaml`
- Create: `tests/fixtures/docs_refs_check/pass/docs/configuration.md`
- Create: `tests/fixtures/docs_refs_check/pass/docs/features.md`
- Create: `tests/fixtures/docs_refs_check/fail/**` (copy of `pass/` with two files replaced)

- [ ] **Step 1: Create `tests/fixtures/docs_refs_check/pass/scripts/tool.sh`** (exactly 6 lines — the fail fixture's `4-9` range depends on it)

```bash
#!/usr/bin/env bash
# Fixture target for docs_refs_check path and line references.
set -euo pipefail
echo "one"
echo "two"
echo "three"
```

- [ ] **Step 2: Create `tests/fixtures/docs_refs_check/pass/workflows/archon-dark-factory.yaml`**

```yaml
name: docs-refs-fixture
nodes:
  - id: fetch
    bash: echo fetch
  - id: build-step
    depends_on: [fetch]
    bash: echo build
  - id: report
    depends_on: [build-step]
    bash: echo report
```

- [ ] **Step 3: Create `tests/fixtures/docs_refs_check/pass/config/config.yaml`** (the list value `skip_labels` is one leaf; `code-review` exercises the hyphenated-key regex)

```yaml
scheduler:
  poll_interval: 60
  skip_labels: [wontfix, duplicate]
budgets:
  code-review: 22000
```

- [ ] **Step 4: Create `tests/fixtures/docs_refs_check/pass/docs/configuration.md`** (the `## Layer 2` row proves the key scan is scoped to the `## Layer 1` section)

````markdown
# Fixture configuration reference

## Layer 1: `config/config.yaml`

| Key | Default | Reader |
|---|---|---|
| `scheduler.poll_interval` | `60` | `scripts/tool.sh:4` |
| `scheduler.skip_labels` | `[wontfix, duplicate]` | **unread** |
| `budgets.code-review` | `22000` | `tool.sh:1-2` |

## Layer 2: environment variables

| Key | Default |
|---|---|
| `not_a.config_key` | `x` |
````

- [ ] **Step 5: Create `tests/fixtures/docs_refs_check/pass/docs/features.md`** (covers: node list with `(`workflow:N`)` asides, `workflow:` alias, comma chain `1,4-6`, a directory ref, and every skip rule — bare artifact name, absolute path, `NON_REPO_PATHS` entry, bare `:N` continuation span, non-node hyphenated spans)

````markdown
# Fixture features

| Feature | Where |
|---|---|
| **Build.** | Nodes `fetch` (`workflow:3`) and `build-step` (`:5`, via `scripts/tool.sh`). Node `report` (`workflow:8-10`). |
| **Tooling.** | `scripts/tool.sh:1,4-6`, the `scripts/` directory, run artifact `implementation.md`, absolute `/opt/x.md`, local `.archon/.env`, continuation `:586-591`. |
| **Hooks.** | The `smoke-gate` hook and the `direct-to-pr` label are not nodes. |
````

- [ ] **Step 6: Create the `fail/` tree as a copy of `pass/`**

```bash
cp -r tests/fixtures/docs_refs_check/pass/. tests/fixtures/docs_refs_check/fail/
```

- [ ] **Step 7: Overwrite `tests/fixtures/docs_refs_check/fail/config/config.yaml`** (adds the undocumented `scheduler.max_retries` — config break)

```yaml
scheduler:
  poll_interval: 60
  max_retries: 3
  skip_labels: [wontfix, duplicate]
budgets:
  code-review: 22000
```

- [ ] **Step 8: Overwrite `tests/fixtures/docs_refs_check/fail/docs/features.md`** (line 5: `scripts/missing.sh` — path break — and `deploy-step` — node break; line 6: `4-9` — line-range break, since `tool.sh` has 6 lines)

````markdown
# Fixture features

| Feature | Where |
|---|---|
| **Build.** | Nodes `fetch` (`workflow:3`) and `build-step` (`:5`, via `scripts/missing.sh`). Node `deploy-step` (`workflow:8-10`). |
| **Tooling.** | `scripts/tool.sh:1,4-9`, the `scripts/` directory, run artifact `implementation.md`, absolute `/opt/x.md`, local `.archon/.env`, continuation `:586-591`. |
| **Hooks.** | The `smoke-gate` hook and the `direct-to-pr` label are not nodes. |
````

- [ ] **Step 9: Verify the tree**

Run: `find tests/fixtures/docs_refs_check -type f | sort`
Expected (10 files):
```
tests/fixtures/docs_refs_check/fail/config/config.yaml
tests/fixtures/docs_refs_check/fail/docs/configuration.md
tests/fixtures/docs_refs_check/fail/docs/features.md
tests/fixtures/docs_refs_check/fail/scripts/tool.sh
tests/fixtures/docs_refs_check/fail/workflows/archon-dark-factory.yaml
tests/fixtures/docs_refs_check/pass/config/config.yaml
tests/fixtures/docs_refs_check/pass/docs/configuration.md
tests/fixtures/docs_refs_check/pass/docs/features.md
tests/fixtures/docs_refs_check/pass/scripts/tool.sh
tests/fixtures/docs_refs_check/pass/workflows/archon-dark-factory.yaml
```

- [ ] **Step 10: Commit**

```bash
git add tests/fixtures/docs_refs_check
git commit -m "test(docs-refs): add pass/fail fixture trees for docs_refs_check (#537)"
```

---

## Task 2: Path and line-range check

**Files:**
- Create: `tests/test_docs_refs_check.py`
- Create: `scripts/docs_refs_check.py`

- [ ] **Step 1: Write the failing tests — create `tests/test_docs_refs_check.py`**

```python
"""Tests for docs_refs_check.py — deterministic reference-doc checks (#537).

The pass/fail fixture trees under tests/fixtures/docs_refs_check/ mirror the repo layout
(docs/, scripts/, config/, workflows/) so check() runs on them with its defaults; fail/
carries exactly one broken reference per check category.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from docs_refs_check import check, main  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docs_refs_check"


def _fixture_docs(name: str) -> tuple[Path, list[Path]]:
    root = _FIXTURES / name
    return root, [root / "docs" / "configuration.md", root / "docs" / "features.md"]


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _paths_only(root: Path, doc_text: str) -> list[str]:
    """Run check() on a doc named notes.md, so only the path/line check applies."""
    doc = root / "notes.md"
    doc.write_text(doc_text, encoding="utf-8")
    return check([doc], root, root / "none.yaml", root / "none.yaml")


# ---------------------------------------------------------------------------
# Path and line references
# ---------------------------------------------------------------------------

def test_line_ranges_and_comma_chains(tmp_path):
    root = _tree(tmp_path, {"pkg/f.sh": "a\nb\nc\nd\n"})
    assert _paths_only(root, "`pkg/f.sh:1,3-4` `pkg/f.sh:4`\n") == []
    errors = _paths_only(root, "`pkg/f.sh:2,5` `pkg/f.sh:3-2` `pkg/f.sh:0`\n")
    assert len(errors) == 3, errors
    assert any(
        e.endswith("notes.md:1: `pkg/f.sh:2,5`: line 5 is out of range (pkg/f.sh has 4 lines)")
        for e in errors
    )


def test_directory_refs_and_directory_with_line_number(tmp_path):
    root = _tree(tmp_path, {"pkg/sub/f.py": "x\n"})
    assert _paths_only(root, "`pkg/sub/` `pkg/`\n") == []
    errors = _paths_only(root, "`pkg/sub:1`\n")
    assert errors == ["%s:1: `pkg/sub:1`: pkg/sub is a directory, not a file" % (root / "notes.md")]


def test_basename_and_suffix_resolution(tmp_path):
    root = _tree(tmp_path, {"a/x.py": "1\n", "b/x.py": "1\n", "pkg/sub/y.py": "1\n"})
    assert _paths_only(root, "`sub/y.py:1` `y.py:1` `a/x.py:1`\n") == []
    errors = _paths_only(root, "`x.py:1`\n")
    assert len(errors) == 1 and "is ambiguous (a/x.py, b/x.py)" in errors[0]


def test_workflow_alias(tmp_path):
    root = _tree(tmp_path, {"workflows/archon-dark-factory.yaml": "nodes: []\n"})
    assert _paths_only(root, "`workflow:1`\n") == []
    assert len(_paths_only(root, "`workflow:2`\n")) == 1
    assert _paths_only(root, "the `workflow` word alone\n") == []


def test_missing_paths(tmp_path):
    root = _tree(tmp_path, {"scripts/real.sh": "x\n"})
    errors = _paths_only(root, "`scripts/gone.sh` `gone.py:3` `pkg/gone.py`\n")
    assert len(errors) == 3, errors


def test_spans_that_are_not_repo_paths_are_skipped(tmp_path):
    root = _tree(tmp_path, {"scripts/real.sh": "x\n"})
    doc = (
        "`implementation.md` `/opt/refinement-skills/config.yaml` `origin/main` "
        "`frontend/` `.archon/.env` `deploy/instance.env` `.archon/commands` "
        "`../README.md` `:586-591` `scripts/*.sh` `${ARTIFACTS_DIR}/plan.md` `poll_interval`\n"
    )
    assert _paths_only(root, doc) == []


def test_nested_dark_factory_dir_and_fixtures_are_not_searched(tmp_path):
    root = _tree(tmp_path, {
        "scripts/factory_core/adapter.py": "1\n",
        "dark-factory/scripts/factory_core/adapter.py": "1\n",
        "tests/fixtures/t/scripts/factory_core/adapter.py": "1\n",
        "deploy/instances/dark-factory/instance.env": "X=1\n",
    })
    assert _paths_only(root, "`adapter.py:1` `deploy/instances/dark-factory/instance.env`\n") == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_index_ignores_ignored_and_deleted_files(tmp_path):
    root = _tree(tmp_path, {
        ".gitignore": "staged/\n",
        "scripts/x.py": "1\n",
        "staged/x.py": "1\n",
        "scripts/deleted.py": "1\n",
    })
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    (root / "scripts" / "deleted.py").unlink()
    assert _paths_only(root, "`x.py:1`\n") == []
    assert len(_paths_only(root, "`scripts/deleted.py`\n")) == 1


```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'docs_refs_check'`.

- [ ] **Step 3: Implement — create `scripts/docs_refs_check.py`**

(`yaml` and `Iterator` are imported now and first used in Tasks 3–4. `check()` already resolves
`config`/`workflow` defaults, and Tasks 3–4 add the two dispatch lines that use them.)

```python
"""Deterministic reference checks for the reference docs (#537).

``docs/run-lifecycle.md``, ``docs/scheduler.md``, ``docs/features.md`` and
``docs/configuration.md`` cite the code heavily. PR #533 had to re-map most of those
citations after #519, #524 and #525 changed behaviour without a doc update. This check
fails CI when a reference no longer resolves:

  - every backtick repo path (``path``, ``path:N``, ``path:N-M``, ``path:N,M-K``) exists,
    and every cited line is within the file's current line count (range only — whether
    the line still says what the prose claims is not checked);
  - ``configuration.md`` only: the ``## Layer 1`` table keys and the leaf keys of
    ``config/config.yaml`` match in both directions;
  - ``features.md`` only: every id named after a "Node"/"Nodes" lead-in is a node id in
    the workflow DAG.

Not checked (v1): bare continuation spans such as ``:586-591`` that carry no path.

Usage (CI)::

    python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md \\
        docs/features.md docs/configuration.md

API::

    from docs_refs_check import check
    errors = check(doc_paths, repo_root, config_path, workflow_path)   # [] on success
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

import yaml

PathLike = Union[str, Path]

# A bare span counts as a path when it ends in one of these (or starts with a repo dir).
PATH_EXTENSIONS = (".py", ".sh", ".yaml", ".yml", ".md")

# docs/features.md writes `workflow:408` for the DAG file.
WORKFLOW_ALIAS = "workflow"
WORKFLOW_FILE = "workflows/archon-dark-factory.yaml"

# Never indexed, at any depth.
EXCLUDED_DIRS = frozenset({".git", "node_modules", ".venv", "__pycache__"})
# Never indexed at the top level: a run stages its own seed/fixture copy of the factory
# into dark-factory/ (it holds a second scripts/factory_core/adapter.py, so bare
# `adapter.py:N` refs would resolve ambiguously). deploy/instances/dark-factory/ is real.
EXCLUDED_TOP_LEVEL = ("dark-factory/",)
# Not searched by basename/suffix: fixture trees mirror the repo layout on purpose.
SEARCH_EXCLUDED_PREFIXES = ("tests/fixtures/",)
# Paths the docs name that are never in a checkout: operator-local gitignored files, and
# files entrypoint.sh stages into a run's clone and git-excludes.
NON_REPO_PATHS = (
    ".archon/.env",
    "deploy/instance.env",
    ".archon/commands",
    ".archon/workflows",
    ".claude/skills/refinement/config.yaml",
    ".claude/settings.local.json",
)

_SPAN = re.compile(r"`([^`\n]+)`")
_REF = re.compile(
    r"^(?P<path>[A-Za-z0-9_.-][A-Za-z0-9_./-]*?)"
    r"(?::(?P<spec>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?$"
)


def _is_non_repo(rel: str) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in NON_REPO_PATHS)


def _repo_files(repo_root: Path) -> set[str]:
    """Repo-relative paths of the files a CI checkout of *repo_root* would contain.

    Uses git (tracked plus untracked-not-ignored) so ignored, run-staged files in a local
    clone never count; falls back to a directory walk when *repo_root* is not in a repo.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout
        names: Iterable[str] = (n for n in out.split("\0") if n)
    except (OSError, subprocess.CalledProcessError):
        names = (p.relative_to(repo_root).as_posix() for p in repo_root.rglob("*"))
    return {
        n for n in names
        if (repo_root / n).is_file()
        and not EXCLUDED_DIRS.intersection(n.split("/")[:-1])
        and not n.startswith(EXCLUDED_TOP_LEVEL)
        and not _is_non_repo(n)
    }


def _check_linespec(where: str, span: str, rel: str, spec: str, n_lines: int) -> list[str]:
    errors = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        first, last = int(lo), int(hi or lo)
        if not 1 <= first <= last <= n_lines:
            errors.append(
                f"{where}: `{span}`: line {part} is out of range ({rel} has {n_lines} lines)"
            )
    return errors


def _check_path_refs(doc: str, text: str, repo_root: Path, files: set[str]) -> list[str]:
    dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    top = {f.split("/")[0] for f in files}
    searchable = sorted(f for f in files if not f.startswith(SEARCH_EXCLUDED_PREFIXES))
    line_counts: dict[str, int] = {}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{doc}:{lineno}"
        for span in _SPAN.findall(line):
            m = _REF.match(span)
            if not m:
                continue
            path, spec = m["path"], m["spec"]
            if path == WORKFLOW_ALIAS and spec:
                path = WORKFLOW_FILE
            elif ".." in path or not (
                path.endswith(PATH_EXTENSIONS) or ("/" in path and path.split("/")[0] in top)
            ):
                continue
            rel = path.rstrip("/")
            if _is_non_repo(rel):
                continue
            if rel in files or rel in dirs:
                resolved = rel
            else:
                matches = [f for f in searchable if f.endswith("/" + rel)]
                if len(matches) > 1:
                    errors.append(
                        f"{where}: `{span}` is ambiguous ({', '.join(matches)}); "
                        "write the full repo-relative path"
                    )
                    continue
                if not matches:
                    # A bare file name with no line number that matches nothing is a run
                    # artifact or a target-repo file (`implementation.md`), not a repo path.
                    if "/" in rel or spec:
                        errors.append(f"{where}: `{span}`: no such file or directory in the repo")
                    continue
                resolved = matches[0]
            if not spec:
                continue
            if resolved in dirs:
                errors.append(f"{where}: `{span}`: {resolved} is a directory, not a file")
                continue
            if resolved not in line_counts:
                line_counts[resolved] = len(
                    (repo_root / resolved).read_text(encoding="utf-8", errors="replace").splitlines()
                )
            errors.extend(_check_linespec(where, span, resolved, spec, line_counts[resolved]))
    return errors


def check(
    doc_paths: Iterable[PathLike],
    repo_root: PathLike = ".",
    config_path: Optional[PathLike] = None,
    workflow_path: Optional[PathLike] = None,
) -> list[str]:
    """Check every reference in *doc_paths*. Returns ``[]`` when all resolve."""
    root = Path(repo_root)
    config = Path(config_path) if config_path else root / "config" / "config.yaml"
    workflow = Path(workflow_path) if workflow_path else root / WORKFLOW_FILE
    files = _repo_files(root)
    errors: list[str] = []
    for doc_path in doc_paths:
        doc = Path(doc_path)
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{doc}: cannot read file: {exc}")
            continue
        errors.extend(_check_path_refs(str(doc), text, root, files))
    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("docs", nargs="+", metavar="DOC")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default=None)
    parser.add_argument("--workflow", default=None)
    args = parser.parse_args(argv)

    errors = check(args.docs, args.repo_root, args.config, args.workflow)
    if errors:
        print("Docs reference check failed:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print(f"Docs reference check passed for {len(args.docs)} doc(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v`
Expected: `8 passed` (`test_line_ranges_and_comma_chains`, `test_directory_refs_and_directory_with_line_number`,
`test_basename_and_suffix_resolution`, `test_workflow_alias`, `test_missing_paths`,
`test_spans_that_are_not_repo_paths_are_skipped`, `test_nested_dark_factory_dir_and_fixtures_are_not_searched`,
`test_git_index_ignores_ignored_and_deleted_files`).

- [ ] **Step 5: Commit**

```bash
git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
git commit -m "feat(docs-refs): path and line-range reference check (#537)"
```

---

## Task 3: Config-key round-trip check (`configuration.md` only)

**Files:**
- Modify: `tests/test_docs_refs_check.py` (append)
- Modify: `scripts/docs_refs_check.py`

- [ ] **Step 1: Write the failing tests — append to the end of `tests/test_docs_refs_check.py`**

```python
# ---------------------------------------------------------------------------
# Config keys (configuration.md only)
# ---------------------------------------------------------------------------

_CONFIG_DOC = (
    "# Config\n\n## Layer 1: `config/config.yaml`\n\n"
    "| Key | Default |\n|---|---|\n"
    "| `a.b` | `1` |\n| `a.items` | `[x]` |\n| `budgets.code-review` | `2` |\n"
    "\n## Layer 2: env\n\n| Key | Default |\n|---|---|\n| `env.only` | `1` |\n"
)


def _config_check(tmp_path: Path, doc: str, config: str) -> list[str]:
    root = _tree(tmp_path, {"docs/configuration.md": doc, "config/config.yaml": config})
    return check([root / "docs" / "configuration.md"], root)


def test_config_keys_round_trip(tmp_path):
    config = "a:\n  b: 1\n  items: [x, y]\nbudgets:\n  code-review: 2\n"
    assert _config_check(tmp_path, _CONFIG_DOC, config) == []


def test_config_key_documented_but_removed(tmp_path):
    errors = _config_check(tmp_path, _CONFIG_DOC, "a:\n  items: [x]\nbudgets:\n  code-review: 2\n")
    assert len(errors) == 1 and "configuration.md:7: config key `a.b` is documented but is not in" in errors[0]


def test_config_key_added_without_docs(tmp_path):
    config = "a:\n  b: 1\n  c: 3\n  items: [x]\nbudgets:\n  code-review: 2\n"
    errors = _config_check(tmp_path, _CONFIG_DOC, config)
    assert len(errors) == 1 and "config key `a.c`" in errors[0] and "is not documented" in errors[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v -k config`
Expected: `test_config_key_documented_but_removed` and `test_config_key_added_without_docs` FAIL
(`assert 0 == 1`: no config check runs yet); `test_config_keys_round_trip` passes trivially.

- [ ] **Step 3: Implement — in `scripts/docs_refs_check.py`, insert this block immediately above `def check(`**

```python
_KEY_ROW = re.compile(r"^\|\s*`([a-z_][a-z0-9_-]*(?:\.[a-z_][a-z0-9_-]*)+)`\s*\|")


def _config_leaves(data: dict, prefix: str = "") -> Iterator[str]:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            yield from _config_leaves(value, path)
        else:
            yield path


def _check_config_keys(doc: str, text: str, config_path: Path) -> list[str]:
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"{config_path}: cannot load config: {exc}"]
    if not isinstance(data, dict):
        return [f"{config_path}: top level is not a mapping"]
    documented: dict[str, int] = {}
    in_layer1 = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            in_layer1 = line.startswith("## Layer 1")
        elif in_layer1 and (m := _KEY_ROW.match(line)):
            documented.setdefault(m[1], lineno)
    leaves = set(_config_leaves(data))
    errors = [
        f"{doc}:{lineno}: config key `{key}` is documented but is not in {config_path}"
        for key, lineno in documented.items() if key not in leaves
    ]
    errors += [
        f"{doc}: config key `{key}` in {config_path} is not documented in the Layer 1 tables"
        for key in sorted(leaves - documented.keys())
    ]
    return errors


```

- [ ] **Step 4: Dispatch it — in `check()`, directly below the line `        errors.extend(_check_path_refs(str(doc), text, root, files))`, add:**

```python
        if doc.name == "configuration.md":
            errors.extend(_check_config_keys(str(doc), text, config))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v`
Expected: `11 passed`.

- [ ] **Step 6: Commit**

```bash
git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
git commit -m "feat(docs-refs): config-key round-trip check for configuration.md (#537)"
```

---

## Task 4: Workflow-node check (`features.md` only), fixture-tree and CLI tests

**Files:**
- Modify: `tests/test_docs_refs_check.py` (append)
- Modify: `scripts/docs_refs_check.py`

- [ ] **Step 1: Write the failing tests — append to the end of `tests/test_docs_refs_check.py`**

```python
# ---------------------------------------------------------------------------
# Workflow nodes (features.md only)
# ---------------------------------------------------------------------------

_WORKFLOW = "nodes:\n  - id: alpha\n  - id: beta-step\n  - id: gamma\n"


def _node_check(tmp_path: Path, doc: str, name: str = "features.md") -> list[str]:
    root = _tree(tmp_path, {f"docs/{name}": doc, "workflows/archon-dark-factory.yaml": _WORKFLOW})
    return check([root / "docs" / name], root)


def test_node_lists_and_asides(tmp_path):
    doc = (
        "Nodes `alpha` (`workflow:1`; `mark-ready` `:2`), `beta-step` and `gamma`.\n"
        "workflow node `alpha`. Per-node `model:` pins. The `smoke-gate` hook.\n"
    )
    assert _node_check(tmp_path, doc) == []


def test_unknown_node_is_reported(tmp_path):
    errors = _node_check(tmp_path, "Nodes `alpha` (`workflow:1`) and `delta`.\n")
    assert len(errors) == 1 and "features.md:1: workflow node `delta` is not a node id" in errors[0]


def test_node_check_only_runs_on_features_md(tmp_path):
    assert _node_check(tmp_path, "Node `delta`.\n", name="scheduler.md") == []


# ---------------------------------------------------------------------------
# Fixture trees: one pass, one fail with one break per check
# ---------------------------------------------------------------------------

def test_pass_fixture_has_no_errors():
    root, docs = _fixture_docs("pass")
    assert check(docs, root) == []


def test_fail_fixture_reports_one_error_per_check():
    root, docs = _fixture_docs("fail")
    errors = check(docs, root)
    assert len(errors) == 4, errors
    joined = "\n".join(errors)
    assert "features.md:5: `scripts/missing.sh`: no such file or directory" in joined
    assert "features.md:6: `scripts/tool.sh:1,4-9`: line 4-9 is out of range" in joined
    assert "config key `scheduler.max_retries`" in joined and "is not documented" in joined
    assert "features.md:5: workflow node `deploy-step` is not a node id" in joined


def test_cli_exit_codes(capsys):
    root, docs = _fixture_docs("pass")
    assert main([*map(str, docs), "--repo-root", str(root)]) == 0
    root, docs = _fixture_docs("fail")
    assert main([*map(str, docs), "--repo-root", str(root)]) == 1
    assert "deploy-step" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v`
Expected: `test_unknown_node_is_reported`, `test_fail_fixture_reports_one_error_per_check`
(`assert 3 == 4` — the node break is not yet detected) and `test_cli_exit_codes` (no
`deploy-step` in stderr) FAIL; the rest pass.

- [ ] **Step 3: Implement — in `scripts/docs_refs_check.py`, insert this block immediately above `def check(`**

```python
_NODE_LEAD = re.compile(r"(?<![\w-])[Nn]odes?\s+(?=`)")
_NODE_ID = re.compile(r"`([a-z][a-z0-9_-]*)`")
_NODE_SEP = re.compile(r"\s*(?:,\s*and\s+|,\s*|\s+and\s+)(?=`)")


def _skip_aside(line: str, i: int) -> int:
    """Return the index just past a parenthetical aside starting at *i*, else *i*."""
    j = i
    while j < len(line) and line[j] == " ":
        j += 1
    if j >= len(line) or line[j] != "(":
        return i
    depth = 0
    for k in range(j, len(line)):
        if line[k] == "(":
            depth += 1
        elif line[k] == ")":
            depth -= 1
            if depth == 0:
                return k + 1
    return len(line)


def _check_feature_nodes(doc: str, text: str, workflow_path: Path) -> list[str]:
    try:
        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"{workflow_path}: cannot load workflow: {exc}"]
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return [f"{workflow_path}: 'nodes' is not a list"]
    ids = {n["id"] for n in nodes if isinstance(n, dict) and "id" in n}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for lead in _NODE_LEAD.finditer(line):
            i = lead.end()
            while m := _NODE_ID.match(line, i):
                if m[1] not in ids:
                    errors.append(
                        f"{doc}:{lineno}: workflow node `{m[1]}` is not a node id in {workflow_path}"
                    )
                i = _skip_aside(line, m.end())
                sep = _NODE_SEP.match(line, i)
                if not sep:
                    break
                i = sep.end()
    return errors


```

- [ ] **Step 4: Dispatch it — in `check()`, directly below the two `configuration.md` lines added in Task 3, add:**

```python
        if doc.name == "features.md":
            errors.extend(_check_feature_nodes(str(doc), text, workflow))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v`
Expected: `17 passed`.

- [ ] **Step 6: Commit**

```bash
git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
git commit -m "feat(docs-refs): workflow-node check for features.md (#537)"
```

---

## Task 5: Make the four real docs pass

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/features.md`
- Modify: `docs/scheduler.md`

- [ ] **Step 1: Run the checker on the real docs to see the breakage**

Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md; echo "exit=$?"`
Expected: `Docs reference check failed:` followed by 52 errors, then `exit=1`. They are: 3 ×
`` `cli.py…` is ambiguous (scripts/factory_core/cli.py, scripts/factory_core/providers/cli.py) ``
(`docs/scheduler.md:25`, `docs/features.md:64`, `docs/configuration.md:430`), 19 ×
`config key `<relative key>` is documented but is not in config/config.yaml`
(`docs/configuration.md:269-287`), and 30 × `config key `token_optimization.…` … is not
documented in the Layer 1 tables`. If `main` has moved since this plan was written and the
list differs, fix every reported error using the same rules (full repo-relative path for
ambiguity, corrected line number for out-of-range, full key per row for config). Never
work around an error by editing the checker.

Steps 2–4 are exact, unique substring replacements. Each old/new string is the whole content
of its one-line code block, with no leading or trailing whitespace. In `docs/scheduler.md` the
match sits at the end of a line, so do not add a trailing space.

- [ ] **Step 2: Fix `docs/scheduler.md`** (the "Source files:" list under `scripts/factory_core/`, line 25). Replace

```text
(`cli.py`,
```

with

```text
(`factory_core/cli.py`,
```

- [ ] **Step 3: Fix `docs/features.md`** (breaker row, line 64). Replace

```text
`loop_entry=None` (`cli.py:115`;
```

with

```text
`loop_entry=None` (`scripts/factory_core/cli.py:115`;
```

- [ ] **Step 4: Fix `docs/configuration.md`** (`STATE_FILE` row, line 430). Replace

```text
`scripts/factory_core/breaker.py:15`, `cli.py:47` |
```

with

```text
`scripts/factory_core/breaker.py:15`, `scripts/factory_core/cli.py:47` |
```

- [ ] **Step 5: Rewrite the `### token_optimization` table rows in `docs/configuration.md`.** Keep
the header and separator lines (`| Key | Default | Controls | Reader | Env override (scope) |`,
`|---|---|---|---|---|`) and the prose above them unchanged. Replace the 24 data rows, from the
row starting `` | `enforce_budgets` | `` through the row starting
`` | `escalation.opus_only_for` | `` inclusive, with exactly these 30 rows (one fully
qualified key per row; the three combined rows are split, `each` is dropped from the defaults,
and the second and later rows of a split say `same`):

````markdown
| `token_optimization.enforce_budgets` | `true` | global switch: enforce only for scenarios whose `enforce.<s>` is also true | `scripts/budget_gate.sh:44` | `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false\|0\|no` forces observe, can never force enforce (`budget_gate.sh:50-51`) (run) |
| `token_optimization.default_budget_tokens` | `30000` | budget when `budgets.<s>` is absent | `scripts/budget_gate.sh:46`; `evals/token_opt_eval.py:282-298` | none |
| `token_optimization.budgets.refine` | `30000` | per-scenario token budget | `scripts/budget_gate.sh:46` | none |
| `token_optimization.budgets.plan` | `30000` | same | `scripts/budget_gate.sh:46` | none |
| `token_optimization.budgets.implement` | `30000` | same | `scripts/budget_gate.sh:46` | none |
| `token_optimization.budgets.conformance` | `22000` | same | `scripts/budget_gate.sh:46` | none |
| `token_optimization.budgets.code-review` | `22000` | same | `scripts/budget_gate.sh:46` | none |
| `token_optimization.enforce.refine` | `true` | enforce for the scenario | `scripts/budget_gate.sh:45` | none (only the global kill-switch above) |
| `token_optimization.enforce.plan` | `true` | same | `scripts/budget_gate.sh:45` | none (only the global kill-switch above) |
| `token_optimization.enforce.conformance` | `true` | same | `scripts/budget_gate.sh:45` | none (only the global kill-switch above) |
| `token_optimization.enforce.code-review` | `true` | same | `scripts/budget_gate.sh:45` | none (only the global kill-switch above) |
| `token_optimization.enforce.implement` | `false` | same | `scripts/budget_gate.sh:45` | none |
| `token_optimization.issue_context.reserve_tokens` | `2000` | floor reserved for issue context | `scripts/budget_enforce.py:137,183` | none |
| `token_optimization.architecture.enabled` | `true` | false = skip slicing, load full ARCHITECTURE.md | `scripts/architecture_slice.py:152-162` (env, then config) | `TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED` (run) |
| `token_optimization.architecture.mode` | `slice` | **unread** | none | none |
| `token_optimization.architecture.max_tokens` | `5000` | derivation input for `budget_enforce.py`, not a live cap | `scripts/budget_enforce.py:57-60,138`. The live cap is env only: `scripts/architecture_slice.py:165-182` | `TOKEN_OPTIMIZATION_ARCHITECTURE_MAX_TOKENS` (run; set by the gate in enforce mode) |
| `token_optimization.architecture.min_tokens` | `2500` | proportional-distribution floor | `scripts/budget_enforce.py:57-60,138` | none |
| `token_optimization.memory.enabled` | `true` | false = skip top-k cap | `scripts/memory_retrieve.py:110-130` (env, then config) | `TOKEN_OPTIMIZATION_MEMORY_ENABLED` (run) |
| `token_optimization.memory.mode` | `top_k` | **unread** | none | none |
| `token_optimization.memory.max_entries` | `8` | entries surfaced per phase | `scripts/memory_retrieve.py:157-160,474` (config only; code default `TOP_K_DEFAULT = 8` at `:78`) | none |
| `token_optimization.memory.max_tokens` | `1500` | token ceiling for memory context | `scripts/memory_retrieve.py:163-190`: env, then config, then `TOKEN_BUDGET_DEFAULT = 1500` (`:79`); also a derivation input (`budget_enforce.py:63-66`) | `TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS` (run) |
| `token_optimization.memory.min_tokens` | `750` | distribution floor | `scripts/budget_enforce.py:63-66,139` | none |
| `token_optimization.comments.enabled` | `true` | the comment-digest gate | **Not read from config.** The only reader is the workflow node, which checks the env var alone: `workflows/archon-dark-factory.yaml:143-146` | `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` (run); `_set_cfg` exports it in the scheduler only (D4) |
| `token_optimization.comments.digest_after_factory_marker` | `true` | **unread** | none | none |
| `token_optimization.comments.max_tokens` | `2000` | derivation input, not a live cap | `scripts/budget_enforce.py:71-74,140`; live cap is env only at `scripts/comment_digest.py:30` | `TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS` (run; set by the gate) |
| `token_optimization.comments.min_tokens` | `1000` | distribution floor | `scripts/budget_enforce.py:71-74,140` | none |
| `token_optimization.diff.enabled` | `true` | false = emit the full diff | `scripts/diff_rank.py:140-147` (env, then config) | `TOKEN_OPTIMIZATION_DIFF_ENABLED` (run) |
| `token_optimization.diff.max_review_tokens` | `6000` | diff context cap for code review | `scripts/diff_rank.py:133-136,151-157` (config, then env wins) | `TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS` (run) |
| `token_optimization.diff.min_review_tokens` | `3000` | distribution floor | `scripts/budget_enforce.py:79-84,141` | none |
| `token_optimization.escalation.opus_only_for` | `security, trading, auth, high_blast_radius, material_conformance_uncertainty` | **unread** (the YAML comment already says reserved) | none | none |
````

Leave the "Counting:" paragraph below the table unchanged. It already says
`token_optimization` 30.

- [ ] **Step 6: Add the authoring note to `docs/configuration.md`** — directly after the line `` keys is `scheduler.sh:66-98`. `` (in the `## Layer 1: `config/config.yaml`` intro), insert a blank line and this paragraph:

````markdown
Every key in these tables is written out in full (`token_optimization.budgets.refine`, not
`budgets.refine`): `scripts/docs_refs_check.py` matches them against the leaf keys of
`config/config.yaml` in CI, in both directions, so a key added, renamed or removed without a
table row fails the pull request.
````

- [ ] **Step 7: Re-run the checker to verify the four docs pass**

Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md; echo "exit=$?"`
Expected: `Docs reference check passed for 4 doc(s).` then `exit=0`.

- [ ] **Step 8: Commit**

```bash
git add docs/configuration.md docs/features.md docs/scheduler.md
git commit -m "docs: fix the references docs_refs_check reports as broken (#537)"
```

---

## Task 6: CI wiring and the `## CI` row in `docs/features.md`

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/features.md`

- [ ] **Step 1: Add the CI step.** In `.github/workflows/ci.yml`, in the existing `dag-check` job (do not rename it — it may be a required branch-protection check), add one line directly below `      - run: python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml`:

```yaml
      - run: python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

The `dag-check` job afterwards reads:

```yaml
  dag-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install pyyaml
      - run: python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
      - run: python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
      - run: python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

- [ ] **Step 2: Verify the YAML still parses and the job has the step**

Run: `python -c "import yaml; s=yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']['dag-check']['steps']; print(s[-1]['run'])"`
Expected: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`

- [ ] **Step 3: Update the `## CI` row in `docs/features.md`.** Replace the exact text:

````markdown
| **CI.** Pull requests run the Python suite, 27 shell tests, the two workflow DAG checks and a Docker build. | **Live.** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). | `scripts/check_workflow_dag.py`, `scripts/check_workflow_when.py` |
````

with:

````markdown
| **CI.** Pull requests run the Python suite, 27 shell tests, the two workflow DAG checks, the reference-docs check (paths, line ranges, config keys and node ids in `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` and `docs/configuration.md`) and a Docker build. | **Live.** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). | `scripts/check_workflow_dag.py`, `scripts/check_workflow_when.py`, `scripts/docs_refs_check.py` |
````

(the rest of the row — `| Pull requests to `main` | [CLAUDE.md](../CLAUDE.md) | 23 `tests/test_*.sh` … |` — is unchanged).

- [ ] **Step 4: Run the full `dag-check` job locally**

Run:
```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && \
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml && \
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: all three print their "passed" line; the last is `Docs reference check passed for 4 doc(s).`

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml docs/features.md
git commit -m "ci: run docs_refs_check in the dag-check job; list it in features.md (#537)"
```

---

## Task 7: Final verification (no new files)

- [ ] **Step 1: Full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q`
Expected: all pass (the new file adds 17 tests). In particular, no other test breaks
because of the new `tests/fixtures/docs_refs_check/` tree.

- [ ] **Step 2: CI-parity run in a fresh clone.** A fresh clone has none of this container's
run-staged, git-ignored files, which is exactly the CI checkout:

```bash
rm -rf /tmp/docs-refs-ci && git clone -q . /tmp/docs-refs-ci && \
  (cd /tmp/docs-refs-ci && python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md)
```
Expected: `Docs reference check passed for 4 doc(s).` (run after the Task 6 commit, since a clone sees only committed work)

- [ ] **Step 3: Mutation check — the gate bites (AC1, AC3).** In the throwaway clone:

```bash
cd /tmp/docs-refs-ci && D="docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md"
rm scripts/scheduler_lib.sh; python scripts/docs_refs_check.py $D; echo "exit=$?"; git checkout -q scripts/scheduler_lib.sh
printf 'newblock:\n  knob: 1\n' >> config/config.yaml; python scripts/docs_refs_check.py $D; echo "exit=$?"; git checkout -q config/config.yaml
cd - && rm -rf /tmp/docs-refs-ci
```
Expected: first run reports `` `scripts/scheduler_lib.sh`: no such file or directory in the repo `` lines and `exit=1`; second reports `` config key `newblock.knob` in config/config.yaml is not documented in the Layer 1 tables `` and `exit=1`.

- [ ] **Step 4: Scope check** (two-dot diff against `origin/main`, per the codebase-patterns
memory: three-dot shows false out-of-scope hits from commits `main` merged after the branch
diverged)

Run: `git fetch -q origin main && git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory'`
Expected: exactly `.github/workflows/ci.yml`, `docs/configuration.md`, `docs/features.md`,
`docs/scheduler.md`, `scripts/docs_refs_check.py`, `tests/test_docs_refs_check.py` and the 10
`tests/fixtures/docs_refs_check/**` files.

---

## Known v1 gaps (documented, not silently omitted)

- Range-only line validation. A line number that is still in range but now points at different
  code is not caught. The symbol/content-anchor check is the spec's deferred follow-up.
- Bare continuation spans (`` `:586-591` ``) carry no path and are not checked.
- A bare file name **without** a line number (`` `fetch_scorecard.py` ``) is checked when it
  resolves, but is silently skipped when it matches nothing, because the docs use the same form
  for run artifacts and target-repo files. Full-path and line-numbered references are always
  checked. So deleting `entrypoint.sh` would not be caught by its 19 bare, line-number-free
  mentions, though its line-numbered refs (`entrypoint.sh:912`, …) would fail. An allowlist of
  run-artifact names, which would turn the skip back into an error, is a possible follow-up.
- A span containing `/` counts as a path only when its first segment is a top-level entry of the
  repo. If a whole top-level directory is deleted (for example `evals/`), its `evals/...` refs
  are skipped rather than reported.
- `NON_REPO_PATHS` is a hardcoded list. If `entrypoint.sh` starts staging a new git-excluded
  file that the docs name, the list needs a new entry. The CI failure message names the path.

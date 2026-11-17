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

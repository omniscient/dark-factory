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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Index:
    """The repo file set plus the lookups path resolution needs."""

    files: frozenset[str]
    dirs: frozenset[str]
    top: frozenset[str]
    searchable: tuple[str, ...]


def _index(files: set[str]) -> _Index:
    return _Index(
        files=frozenset(files),
        dirs=frozenset(
            "/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)
        ),
        top=frozenset(f.split("/")[0] for f in files),
        searchable=tuple(sorted(f for f in files if not f.startswith(SEARCH_EXCLUDED_PREFIXES))),
    )


def _resolve(span: str, idx: _Index) -> Optional[tuple[str, Optional[str], Optional[str], Optional[str]]]:
    """Classify one backtick span.

    Returns ``None`` when the span is not a repo path, else ``(path as written, line spec,
    resolved repo path, error)`` where exactly one of the last two is ``None``.
    """
    m = _REF.match(span)
    if not m:
        return None
    path, spec = m["path"], m["spec"]
    if path == WORKFLOW_ALIAS and spec:
        rel = WORKFLOW_FILE
    elif ".." in path or not (
        path.endswith(PATH_EXTENSIONS) or ("/" in path and path.split("/")[0] in idx.top)
    ):
        return None
    else:
        rel = path.rstrip("/")
    if _is_non_repo(rel):
        return None
    if rel in idx.files or rel in idx.dirs:
        return path, spec, rel, None
    matches = [f for f in idx.searchable if f.endswith("/" + rel)]
    if len(matches) > 1:
        return path, spec, None, (
            f"`{span}` is ambiguous ({', '.join(matches)}); write the full repo-relative path"
        )
    if not matches:
        # A bare file name with no line number that matches nothing is a run
        # artifact or a target-repo file (`implementation.md`), not a repo path.
        if "/" in rel or spec:
            return path, spec, None, f"`{span}`: no such file or directory in the repo"
        return None
    return path, spec, matches[0], None


def _read_lines(repo_root: Path, rel: str) -> list[str]:
    return (repo_root / rel).read_text(encoding="utf-8", errors="replace").splitlines()


def _check_path_refs(doc: str, text: str, repo_root: Path, idx: _Index) -> list[str]:
    line_counts: dict[str, int] = {}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{doc}:{lineno}"
        for span in _SPAN.findall(line):
            ref = _resolve(span, idx)
            if ref is None:
                continue
            _, spec, resolved, error = ref
            if error:
                errors.append(f"{where}: {error}")
                continue
            if not spec:
                continue
            if resolved in idx.dirs:
                errors.append(f"{where}: `{span}`: {resolved} is a directory, not a file")
                continue
            if resolved not in line_counts:
                line_counts[resolved] = len(_read_lines(repo_root, resolved))
            errors.extend(_check_linespec(where, span, resolved, spec, line_counts[resolved]))
    return errors


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
    idx = _index(_repo_files(root))
    errors: list[str] = []
    for doc_path in doc_paths:
        doc = Path(doc_path)
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{doc}: cannot read file: {exc}")
            continue
        errors.extend(_check_path_refs(str(doc), text, root, idx))
        if doc.name == "configuration.md":
            errors.extend(_check_config_keys(str(doc), text, config))
        if doc.name == "features.md":
            errors.extend(_check_feature_nodes(str(doc), text, workflow))
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

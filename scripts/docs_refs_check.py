"""Deterministic reference checks for the reference docs (#537, #662).

``docs/run-lifecycle.md``, ``docs/scheduler.md``, ``docs/features.md`` and
``docs/configuration.md`` cite the code heavily. PR #533 had to re-map most of those
citations after #519, #524 and #525 changed behaviour without a doc update. This check
fails CI when a reference no longer resolves:

  - every backtick repo path (``path``, ``path:N``, ``path:N-M``, ``path:N,M-K``) exists,
    and every cited line is within the file's current line count;
  - citations into ``commands/*.md`` (full path or basename form) are also checked by
    content: ``docs/.citation-lock.yaml`` holds a sha256 of the cited lines per citation
    part, and the check fails when a part has no entry, its lines no longer hold the
    locked content, or an entry is no longer cited. Without a lock file this content
    check is off and only the range is checked. Citations into any other file stay
    range-only;
  - ``configuration.md`` only: the ``## Layer 1`` table keys and the leaf keys of
    ``config/config.yaml`` match in both directions;
  - ``features.md`` only: every id named after a "Node"/"Nodes" lead-in is a node id in
    the workflow DAG.

Not checked: bare continuation spans such as ``:586-591`` that carry no path. They are
not locked, and ``--rederive`` does not shift them.

Usage (CI)::

    python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md \\
        docs/features.md docs/configuration.md

After a ``commands/*.md`` edit, repair moved citations (docs and lock; it never locks
content that differs from the locked content)::

    python scripts/docs_refs_check.py --rederive --base origin/main docs/run-lifecycle.md \\
        docs/scheduler.md docs/features.md docs/configuration.md

``--seed`` locks every unlocked citation part at its current content, marked
``seeded: true``; ``--accept DOC CITATION`` locks one citation at its current content
(the only way changed content gets into the lock); ``--lock-file`` overrides the lock path.

API::

    from docs_refs_check import check
    errors = check(doc_paths, repo_root, config_path, workflow_path)   # [] on success
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from itertools import zip_longest
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


# ---------------------------------------------------------------------------
# Citation lock: content check for citations into commands/*.md (#662)
# ---------------------------------------------------------------------------

LOCK_FILE = "docs/.citation-lock.yaml"
REFERENCE_DOCS = (
    "docs/run-lifecycle.md",
    "docs/scheduler.md",
    "docs/features.md",
    "docs/configuration.md",
)
REPAIR_COMMAND = (
    "python scripts/docs_refs_check.py --rederive --base origin/main " + " ".join(REFERENCE_DOCS)
)
EXCERPT_CHARS = 80
_COVERED = re.compile(r"commands/[^/]+\.md")
_LOCK_HEADER = (
    "# Citation lock for commands/*.md line citations (#662). Written by\n"
    "# scripts/docs_refs_check.py --seed / --rederive / --accept only; do not edit by hand.\n"
)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@", re.M)

Key = tuple[str, str, int, int]  # (doc, target, first line, last line)


@dataclass(frozen=True)
class _Part:
    """One part of a citation into commands/*.md (a comma chain has one part per item)."""

    doc: str
    lineno: int
    span: str
    target: str
    first: int
    last: int

    @property
    def key(self) -> Key:
        return (self.doc, self.target, self.first, self.last)


@dataclass(frozen=True)
class _Entry:
    doc: str
    target: str
    first: int
    last: int
    hash: str
    excerpt: str
    seeded: bool = False

    @property
    def key(self) -> Key:
        return (self.doc, self.target, self.first, self.last)


def _lines_text(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}-{last}"


def _spec_parts(spec: str) -> list[tuple[str, int, int]]:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.append((part, int(lo), int(hi or lo)))
    return out


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(line.strip() for line in lines).encode("utf-8")).hexdigest()


def _all_blank(lines: list[str]) -> bool:
    """True when every line is empty once stripped (a blank range hashes like any other
    blank range of the same length, so locking it would silently match any blank lines)."""
    return all(not line.strip() for line in lines)


class _Targets:
    """Cached lines of cited target files, with hash lookups."""

    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root
        self._lines: dict[str, list[str]] = {}

    def lines(self, rel: str) -> list[str]:
        if rel not in self._lines:
            self._lines[rel] = _read_lines(self.root, rel)
        return self._lines[rel]

    def digest(self, rel: str, first: int, last: int) -> Optional[str]:
        lines = self.lines(rel)
        if not 1 <= first <= last <= len(lines):
            return None
        return _digest(lines[first - 1:last])

    def blank(self, rel: str, first: int, last: int) -> bool:
        return _all_blank(self.lines(rel)[first - 1:last])

    def excerpt(self, rel: str, first: int) -> str:
        return self.lines(rel)[first - 1].strip()[:EXCERPT_CHARS]

    def find(self, rel: str, digest: str, count: int) -> list[int]:
        """First lines of every *count*-line window of *rel* whose hash is *digest*."""
        lines = self.lines(rel)
        return [
            i + 1 for i in range(len(lines) - count + 1)
            if _digest(lines[i:i + count]) == digest
        ]

    def entry(self, part: _Part, seeded: bool = False) -> _Entry:
        digest = self.digest(part.target, part.first, part.last)
        assert digest is not None, part
        return _Entry(part.doc, part.target, part.first, part.last, digest,
                      self.excerpt(part.target, part.first), seeded)


def _doc_key(doc: Path, repo_root: Path) -> str:
    try:
        return doc.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return doc.as_posix()


def _parts(doc_key: str, text: str, idx: _Index) -> list[_Part]:
    """Every part of every citation into commands/*.md in *text*, in doc order."""
    parts: list[_Part] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for span in _SPAN.findall(line):
            ref = _resolve(span, idx)
            if ref is None:
                continue
            _, spec, target, error = ref
            if error or not spec or not _COVERED.fullmatch(target or ""):
                continue
            parts.extend(
                _Part(doc_key, lineno, span, target, first, last)
                for _, first, last in _spec_parts(spec)
            )
    return parts


def _load_lock(path: Path) -> dict[Key, _Entry]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise ValueError("'entries' is not a list")
    entries: dict[Key, _Entry] = {}
    for item in raw:
        try:
            first, last = item["lines"]
            entry = _Entry(str(item["doc"]), str(item["target"]), int(first), int(last),
                           str(item["hash"]), str(item.get("excerpt", "")),
                           item.get("seeded") is True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed entry {item!r}") from exc
        entries[entry.key] = entry
    return entries


def _dump_lock(entries: dict[Key, _Entry]) -> str:
    ordered = sorted(entries.values(), key=lambda e: e.key)
    if not ordered:
        return _LOCK_HEADER + "entries: []\n"
    out = [_LOCK_HEADER, "entries:\n"]
    for e in ordered:
        out.append(f"  - doc: {json.dumps(e.doc, ensure_ascii=False)}\n")
        out.append(f"    target: {json.dumps(e.target, ensure_ascii=False)}\n")
        out.append(f"    lines: [{e.first}, {e.last}]\n")
        out.append(f"    hash: {json.dumps(e.hash)}\n")
        out.append(f"    excerpt: {json.dumps(e.excerpt, ensure_ascii=False)}\n")
        if e.seeded:
            out.append("    seeded: true\n")
    return "".join(out)


def _write_lock(path: Path, entries: dict[Key, _Entry]) -> bool:
    """Write *entries* to *path*; returns whether the bytes changed."""
    text = _dump_lock(entries)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _content_errors(parts: list[_Part], entries: dict[Key, _Entry], targets: _Targets,
                    doc_keys: set[str]) -> list[str]:
    errors: list[str] = []
    for p in parts:
        where = f"{p.doc}:{p.lineno}: `{p.span}`: {p.target}:{_lines_text(p.first, p.last)}"
        entry = entries.get(p.key)
        if entry is None:
            if (targets.digest(p.target, p.first, p.last) is not None
                    and targets.blank(p.target, p.first, p.last)):
                errors.append(f"{where} is blank; pick a non-blank anchor "
                              f"(a blank range is never locked, so no repair command applies)")
            else:
                errors.append(f"{where} has no entry in {LOCK_FILE}; run: {REPAIR_COMMAND}")
        elif targets.digest(p.target, p.first, p.last) != entry.hash:
            errors.append(
                f'{where} no longer holds the locked content (locked: "{entry.excerpt}"); '
                f"run: {REPAIR_COMMAND}"
            )
    cited = {p.key for p in parts}
    for e in sorted(entries.values(), key=lambda e: e.key):
        if e.doc in doc_keys and e.key not in cited:
            errors.append(
                f'{e.doc}: lock entry {e.target}:{_lines_text(e.first, e.last)} ("{e.excerpt}") '
                f"is no longer cited; run: {REPAIR_COMMAND}"
            )
    return errors


def _load_docs(doc_paths: Iterable[PathLike], repo_root: Path) -> list[tuple[Path, str, str]]:
    docs = []
    for doc_path in doc_paths:
        doc = Path(doc_path)
        # newline="" disables universal-newline translation so a rewrite (see rederive())
        # round-trips a doc's original line endings instead of silently flattening CRLF to LF.
        with open(doc, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        docs.append((doc, _doc_key(doc, repo_root), text))
    return docs


def seed(doc_paths: Iterable[PathLike], repo_root: PathLike = ".",
         lock_path: Optional[PathLike] = None) -> int:
    """Lock every unlocked citation part at its current content. Returns entries added."""
    root = Path(repo_root)
    lock = Path(lock_path) if lock_path else root / LOCK_FILE
    entries = _load_lock(lock) if lock.is_file() else {}
    targets, idx = _Targets(root), _index(_repo_files(root))
    added = 0
    for _, key, text in _load_docs(doc_paths, root):
        for p in _parts(key, text, idx):
            if p.key in entries or targets.digest(p.target, p.first, p.last) is None:
                continue
            if targets.blank(p.target, p.first, p.last):
                # An all-blank range hashes like any other blank range; locking it would
                # match any blank line in the target, so the content check does nothing.
                continue
            entries[p.key] = targets.entry(p, seeded=True)
            added += 1
    _write_lock(lock, entries)
    return added


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=True,
    ).stdout


def _base_position(
    repo_root: Path, base: str, target: str, first: int, last: int,
) -> tuple[Optional[int], Optional[str]]:
    """Where lines *first*-*last* of *target* at ``merge-base(base, HEAD)`` are now.

    Returns ``(None, None)`` when a hunk touches the range (legitimately ambiguous), or
    ``(None, reason)`` when *base* itself could not be resolved or diffed -- a real
    failure, not mere ambiguity -- else ``(new first line, None)``.
    """
    if base.startswith("-"):
        # Never hand a value that looks like a git option to merge-base/diff.
        return None, f"refusing to treat {base!r} as a git ref (starts with '-')"
    try:
        merge_base = _git(repo_root, "merge-base", base, "HEAD").strip()
        diff = _git(repo_root, "diff", "-U0", "--no-color", "--no-ext-diff", merge_base, "--", target)
    except (OSError, subprocess.CalledProcessError) as exc:
        return None, f"could not resolve or diff --base {base!r}: {exc}"
    except UnicodeDecodeError as exc:
        return None, f"could not decode git output for --base {base!r}: {exc}"
    delta = 0
    for m in _HUNK.finditer(diff):
        start = int(m[1])
        old_count = int(m[2]) if m[2] is not None else 1
        new_count = int(m[3]) if m[3] is not None else 1
        if old_count == 0:  # pure insertion after old line `start`
            if start >= last:
                continue
            if start >= first:
                return None, None
        else:
            if start > last:
                continue
            if start + old_count - 1 >= first:
                return None, None
        delta += new_count - old_count
    return first + delta, None


def _whereabouts(entry: _Entry, targets: _Targets) -> str:
    count = entry.last - entry.first + 1
    found = targets.find(entry.target, entry.hash, count)
    if len(found) == 1:
        return f"locked content is now at {entry.target}:{_lines_text(found[0], found[0] + count - 1)}"
    if not found:
        return f"the locked content is no longer in {entry.target} unchanged; re-read the sentence"
    return f"the locked content occurs {len(found)} times in {entry.target}"


def _rewrite(text: str, moves: dict[tuple[str, int, int], tuple[int, int]],
             idx: _Index) -> tuple[str, list[tuple[int, str, str]]]:
    """Apply *moves* ``{(target, first, last): (first, last)}`` to every citation in *text*."""
    changes: list[tuple[int, str, str]] = []
    out: list[str] = []
    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        def repl(m: re.Match) -> str:
            span = m[1]
            ref = _resolve(span, idx)
            if ref is None or ref[3] or not ref[1] or not _COVERED.fullmatch(ref[2] or ""):
                return m[0]
            path, spec, target, _ = ref
            new = []
            for raw, first, last in _spec_parts(spec):
                if (target, first, last) not in moves:
                    new.append(raw)
                    continue
                nfirst, nlast = moves[(target, first, last)]
                new.append(f"{nfirst}-{nlast}" if "-" in raw else str(nfirst))
            new_span = f"{path}:{','.join(new)}"
            if new_span == span:
                return m[0]
            changes.append((lineno, span, new_span))
            return f"`{new_span}`"
        out.append(_SPAN.sub(repl, line))
    return "".join(out), changes


def _rederive(docs: list[tuple[Path, str, str]], entries: dict[Key, _Entry], targets: _Targets,
              idx: _Index, repo_root: Path, base: Optional[str]):
    """Decide every repair. Returns ``(new entries, {doc path: new text}, printed, failures)``."""
    parts = [p for _, key, text in docs for p in _parts(key, text, idx)]
    first_part: dict[Key, _Part] = {}
    for p in parts:
        first_part.setdefault(p.key, p)
    failures: list[str] = []

    # Moved, unchanged: a cited entry whose lines no longer hold its hash.
    moved: dict[Key, _Entry] = {}
    for key in sorted(first_part):
        entry = entries.get(key)
        if entry is None or targets.digest(entry.target, entry.first, entry.last) == entry.hash:
            continue
        p = first_part[key]
        count = entry.last - entry.first + 1
        found = targets.find(entry.target, entry.hash, count)
        pos = found[0] if len(found) == 1 else None
        base_error: Optional[str] = None
        if pos is None and len(found) > 1 and base:
            shifted, base_error = _base_position(repo_root, base, entry.target, entry.first, entry.last)
            pos = shifted if shifted in found else None
        if pos is not None:
            moved[key] = replace(entry, first=pos, last=pos + count - 1)
            continue
        where = f'{p.doc}:{p.lineno}: `{p.span}` ("{entry.excerpt}")'
        if not found:
            failures.append(f"{where}: the cited range was edited; re-read the sentence against "
                            f"{entry.target} and correct the citation, or lock it with --accept")
        else:
            at = ", ".join(_lines_text(f, f + count - 1) for f in found)
            if base:
                why = "the --base diff does not pick one"
                if base_error:
                    why += f" ({base_error})"
            else:
                why = "pass --base REF to pick one"
            failures.append(f"{where}: ambiguous, the locked content occurs at "
                            f"{entry.target}:{at}; {why}")

    # Moved entries may not land on lines locked for other content (cascade until stable).
    while True:
        result = {k: e for k, e in entries.items() if k not in moved}
        clash = None
        for old_key, entry in sorted(moved.items()):
            prev = result.get(entry.key)
            if prev is not None and prev.hash != entry.hash:
                clash = old_key
                break
            if prev is not None:  # same content: keep `seeded` if either side has it
                entry = replace(entry, seeded=entry.seeded or prev.seeded)
            result[entry.key] = entry
        if clash is None:
            break
        p, entry = first_part[clash], moved.pop(clash)
        failures.append(f'{p.doc}:{p.lineno}: `{p.span}` ("{entries[clash].excerpt}"): locked '
                        f"content is now at {entry.target}:{_lines_text(entry.first, entry.last)}, "
                        "but those lines are locked for other content")

    moves: dict[str, dict[tuple[str, int, int], tuple[int, int]]] = {}
    for old_key, entry in moved.items():
        moves.setdefault(old_key[0], {})[old_key[1:]] = (entry.first, entry.last)

    # Group what is still unlocked or uncited by (doc, target).
    doc_keys = {key for _, key, _ in docs}
    cited_after: dict[Key, _Part] = {}
    for p in parts:
        nfirst, nlast = moves.get(p.doc, {}).get((p.target, p.first, p.last), (p.first, p.last))
        cited_after.setdefault((p.doc, p.target, nfirst, nlast), p)
    groups: dict[tuple[str, str], tuple[list[Key], list[Key]]] = {}
    for key in sorted(cited_after):
        if key not in result:
            groups.setdefault(key[:2], ([], []))[0].append(key)
    for key in sorted(result):
        if key[0] in doc_keys and key not in cited_after:
            groups.setdefault(key[:2], ([], []))[1].append(key)

    for (doc, target), (unlocked, orphans) in sorted(groups.items()):
        # Hand-shifted: an orphan whose hash is the content now cited.
        for key in list(unlocked):
            digest = targets.digest(target, key[2], key[3])
            match = next((o for o in orphans if result[o].hash == digest), None)
            if match is not None:
                result[key] = replace(result.pop(match), first=key[2], last=key[3])
                orphans.remove(match)
                unlocked.remove(key)
        if unlocked and orphans:
            for u, o in zip_longest(unlocked, orphans):
                if u is None:
                    e = result[o]
                    failures.append(f'{doc}: lock entry {target}:{_lines_text(e.first, e.last)} '
                                    f'("{e.excerpt}") is no longer cited; {_whereabouts(e, targets)}')
                    continue
                p = cited_after[u]
                msg = (f"{p.doc}:{p.lineno}: `{p.span}`: {target}:{_lines_text(u[2], u[3])} "
                       "is not the locked content")
                if o is not None:
                    e = result[o]
                    msg += (f' (locked: "{e.excerpt}" at {target}:{_lines_text(e.first, e.last)}); '
                            f"{_whereabouts(e, targets)}")
                failures.append(msg)
            continue
        for key in unlocked:  # new prose
            if (targets.digest(target, key[2], key[3]) is not None
                    and not targets.blank(target, key[2], key[3])):
                result[key] = targets.entry(cited_after[key])
        for key in orphans:  # removed prose
            del result[key]

    texts: dict[Path, str] = {}
    printed: list[str] = []
    for path, key, text in docs:
        if key not in moves:
            continue
        new_text, changes = _rewrite(text, moves[key], idx)
        if new_text != text:
            texts[path] = new_text
        printed.extend(f"{key}:{lineno}: `{old}` -> `{new}`" for lineno, old, new in changes)
    return result, texts, printed, failures


def rederive(doc_paths: Iterable[PathLike], repo_root: PathLike = ".",
             lock_path: Optional[PathLike] = None, base: Optional[str] = None) -> tuple[list[str], list[str]]:
    """Repair moved citations in place. Returns ``(printed changes, failures)``."""
    root = Path(repo_root)
    lock = Path(lock_path) if lock_path else root / LOCK_FILE
    if not lock.is_file():
        return [], [f"{lock}: no citation lock; nothing to re-derive (run --seed to create one)"]
    entries = _load_lock(lock)
    idx = _index(_repo_files(root))
    docs = _load_docs(doc_paths, root)
    result, texts, printed, failures = _rederive(docs, entries, _Targets(root), idx, root, base)
    # Write the lock before the docs: if a crash lands between the two writes, the lock
    # already matches the intended new positions and a re-run (or the next check()) will
    # surface any doc that didn't get rewritten, instead of leaving stale lock entries that
    # silently pass against a doc that has already moved on.
    if _write_lock(lock, result):
        printed.append(f"{LOCK_FILE}: updated")
    for path, text in texts.items():
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return printed, failures


def accept(doc_path: PathLike, citation: str, repo_root: PathLike = ".",
           lock_path: Optional[PathLike] = None) -> tuple[list[str], list[str]]:
    """Lock every part of *citation* in *doc_path* at its current content."""
    root = Path(repo_root)
    lock = Path(lock_path) if lock_path else root / LOCK_FILE
    citation = citation.strip("`")
    if not lock.is_file():
        return [], [f"{lock}: no citation lock; nothing accepted"]
    try:
        (_, key, text), = _load_docs([doc_path], root)
    except OSError as exc:
        return [], [f"{doc_path}: cannot read file: {exc}; nothing accepted"]
    targets, idx = _Targets(root), _index(_repo_files(root))
    parts = [p for p in _parts(key, text, idx) if p.span == citation]
    if not parts:
        return [], [f"{key}: `{citation}` is not a commands/*.md citation in this doc; nothing accepted"]
    bad = [p for p in parts if targets.digest(p.target, p.first, p.last) is None]
    if bad:
        return [], [f"{key}:{bad[0].lineno}: `{citation}`: line {_lines_text(bad[0].first, bad[0].last)} "
                    f"is out of range; nothing accepted"]
    blank = [p for p in parts if targets.blank(p.target, p.first, p.last)]
    if blank:
        return [], [f"{key}:{blank[0].lineno}: `{citation}`: line "
                    f"{_lines_text(blank[0].first, blank[0].last)} is blank; pick a non-blank "
                    f"anchor, nothing accepted"]
    entries = _load_lock(lock)
    printed = []
    for p in {p.key: p for p in parts}.values():
        old = entries.get(p.key)
        entries[p.key] = targets.entry(p)
        printed.append(f"accepted {key}:{p.lineno} {citation}: "
                       f"{old.excerpt if old else '(no entry)'} -> {entries[p.key].excerpt}")
    _write_lock(lock, entries)
    return printed, []


def seeded_count(lock_path: Path) -> int:
    try:
        return sum(e.seeded for e in _load_lock(lock_path).values())
    except (OSError, yaml.YAMLError, ValueError):
        return 0


def check(
    doc_paths: Iterable[PathLike],
    repo_root: PathLike = ".",
    config_path: Optional[PathLike] = None,
    workflow_path: Optional[PathLike] = None,
    *,
    lock_path: Optional[PathLike] = None,
) -> list[str]:
    """Check every reference in *doc_paths*. Returns ``[]`` when all resolve.

    The ``commands/*.md`` content check runs only when the lock file (default
    ``<repo_root>/docs/.citation-lock.yaml``) exists.
    """
    root = Path(repo_root)
    config = Path(config_path) if config_path else root / "config" / "config.yaml"
    workflow = Path(workflow_path) if workflow_path else root / WORKFLOW_FILE
    lock = Path(lock_path) if lock_path else root / LOCK_FILE
    idx = _index(_repo_files(root))
    errors: list[str] = []
    entries: Optional[dict[Key, _Entry]] = None
    if lock.is_file():
        try:
            entries = _load_lock(lock)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            errors.append(f"{lock}: cannot load citation lock: {exc}")
    parts: list[_Part] = []
    doc_keys: set[str] = set()
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
        if entries is not None:
            key = _doc_key(doc, root)
            doc_keys.add(key)
            parts.extend(_parts(key, text, idx))
    if entries is not None:
        errors.extend(_content_errors(parts, entries, _Targets(root), doc_keys))
    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("docs", nargs="*", metavar="DOC")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default=None)
    parser.add_argument("--workflow", default=None)
    parser.add_argument("--lock-file", default=None,
                        help=f"citation lock (default: <repo-root>/{LOCK_FILE})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seed", action="store_true",
                      help="lock every unlocked commands/*.md citation part at its current content")
    mode.add_argument("--rederive", action="store_true",
                      help="relocate moved citations by content; never locks changed content")
    mode.add_argument("--accept", nargs=2, metavar=("DOC", "CITATION"),
                      help="lock one citation (all its parts) at its current content")
    parser.add_argument("--base", default=None, metavar="REF",
                        help="with --rederive: git ref whose diff picks between identical candidates")
    args = parser.parse_args(argv)
    if args.base and not args.rederive:
        parser.error("--base requires --rederive")
    if not args.accept and not args.docs:
        parser.error("at least one DOC is required")
    if args.accept and args.docs:
        parser.error(f"--accept takes its DOC as part of --accept DOC CITATION; "
                      f"unexpected extra argument(s): {' '.join(args.docs)}")
    root = Path(args.repo_root)
    lock = Path(args.lock_file) if args.lock_file else root / LOCK_FILE

    if (args.accept or args.seed or args.rederive) and lock.is_file():
        try:
            _load_lock(lock)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"{lock}: cannot load citation lock: {exc}; restore it from git, "
                  "do not edit it by hand", file=sys.stderr)
            return 1

    if args.accept:
        printed, failures = accept(args.accept[0], args.accept[1], root, lock)
        for line in printed:
            print(line)
        for line in failures:
            print(line, file=sys.stderr)
        return 1 if failures else 0

    failures: list[str] = []
    if args.seed:
        print(f"--seed added {seed(args.docs, root, lock)} entries to {lock}")
    elif args.rederive:
        printed, failures = rederive(args.docs, root, lock, args.base)
        for line in printed:
            print(line)
        if failures:
            print("Citation re-derive could not repair:", file=sys.stderr)
            for line in failures:
                print(f"  {line}", file=sys.stderr)

    errors = check(args.docs, root, args.config, args.workflow, lock_path=lock)
    if lock.is_file() and (n := seeded_count(lock)):
        print(f"{n} citation(s) were locked at seed and have not been read against their target")
    if errors:
        print("Docs reference check failed:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    if failures:
        return 1
    print(f"Docs reference check passed for {len(args.docs)} doc(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

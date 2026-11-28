# Content-aware `commands/*.md` citation check: lock file, `--rederive`, advisory-fix hook — Implementation Plan

**Issue:** #662

**Revised:** 2026-11-28 (operator amendment after plan-gate review, which executed every task on
`origin/main` 378b426: an unreadable lock file is now a one-line error in `--seed`,
`--rederive` and `--accept` instead of a traceback, with a test; test counts updated; counts
confirmed after #38)

> **For agentic workers:** execute the tasks in order. Each task is TDD: write the failing
> test, run it and see it fail, implement, run it and see it pass, commit. Do not skip the
> "see it fail" step. Do not hand-edit `docs/.citation-lock.yaml` at any point: the tool
> writes it.

**Goal:** Make `scripts/docs_refs_check.py` fail when a `commands/*.md` line citation in the
four reference docs no longer points at the content it pointed at, give it a `--rederive`
mode that repairs pure line shifts without ever locking changed content, and have the
advisory-fix phase run that repair when it edits a cited file.

**Architecture:** A tool-written content-hash lock (`docs/.citation-lock.yaml`), one entry
per distinct `(doc, target, first, last)` citation part into `commands/*.md`. `check()`
runs the content check only when the lock file exists (so trees without a lock behave
exactly as today). `--rederive` relocates moved content by sliding a hash window; a
`git diff -U0` from `merge-base(--base, HEAD)` is used only to choose between several
hash-confirmed positions. `--seed` pins every unlocked part (`seeded: true`); `--accept DOC
CITATION` is the only path that locks changed content. A repo-level pytest makes the
failure visible in implement/validate runs. All in one script; no new dependency.

**Tech Stack:** Python 3.12 (CI), `pyyaml` (already a dependency), `hashlib`, `json`,
`git` CLI via `subprocess`; pytest; Markdown command file.

**Spec:** `docs/superpowers/specs/2026-11-28-content-aware-docs-citation-check-design.md`
(approved at b027f1c). The manual audit of existing citations is **not** in this ticket (#666).

---

## Preconditions and decisions baked into this plan

- **`Depends on: #38`.** #38 hand-shifts six citations in `commands/dark-factory-refine.md`.
  The lock must be seeded from a `main` that already contains it. Task 0 checks this. #38
  merged as 378b426. The counts below (84 parts, 80 distinct entries, 8 cited command files,
  202 lines in the edited command file) were measured at 4e2b1bf and confirmed unchanged at
  378b426 by executing this plan there; if `main` has moved again, report the counts the tool
  prints, do not force them to 80.
- **No citation number is changed by hand** (spec requirement 6), with one exception: the
  bare span `:60-189` in the D4 paragraph of `docs/run-lifecycle.md`, which becomes
  `:60-<new last line of commands/dark-factory-revise-advisory.md>` (Task 7).
- **Bare continuation spans are untouched** (requirement 7): not locked, not rewritten.
- **Out of scope / never touch:** `.github/**` (no `ci.yml` edit — the existing `dag-check`
  line turns the content check on because the lock exists at the default path),
  `workflows/**`, `CLAUDE.md`, `deploy/**`, any `gate_*` file, breaker or budget surface,
  `Dockerfile`, `dark-factory/` (a staged copy, not ours), any other `commands/*.md` file.
- **Hold expectation:** this ticket edits `scripts/docs_refs_check.py` and
  `commands/dark-factory-revise-advisory.md`. A blast-radius `HUMAN_REQUIRED` hold on either
  is expected and accepted; do not restructure the change to avoid it.
- **Design choices the spec left open (decided here):**
  - Orphan detection is limited to lock entries whose `doc` is one of the docs passed to
    `check()` (calling it on a subset of docs must not flag the others' entries).
  - A covered citation whose lines are out of range gets both the existing range error and
    a content error (the content error carries the repair command).
  - `--rederive` / `--accept` with no lock file exit 1 with a message and change nothing;
    `--seed` creates the lock. `--accept` does not run the full check (run it separately).
  - In a mixed `(doc, target)` group (unlocked citation + orphan), the failure messages pair
    unlocked citations with orphans in line order, so each message can name
    `locked content is now at <target>:<lines>`; pairing is for messages only.
  - A moved entry may never land on lines whose existing entry has a different hash
    (that would lock changed content for the other citation); that case fails instead.
    When it lands on an entry with the same hash, the two merge and `seeded` is kept if
    either had it (only `--accept` removes `seeded`).
  - The lock file starts with a two-line `#` comment header naming the writer.
  - A lock file that cannot be parsed is a one-line `cannot load citation lock` error and
    exit 1 in every mode, and no mode rewrites it (restore it from git).
  - The Phase 4 hook also checks `[ -f scripts/docs_refs_check.py ]` (belt and braces for
    target clones); it remains `$`-free.
- **Memory lessons applied:** use the two-dot form `git diff origin/main HEAD -- <file>` for
  the out-of-scope check in Task 8 (three-dot gives false positives); the design spec stays
  at its `docs/superpowers/specs/` path — only the plan is archived later, never the spec.
- `pytest` may be missing in the container: `python -m pip install --user pytest pyyaml` if
  `python -m pytest --version` fails. CI runs `python -m pytest tests/ -v` with
  `PYTHONPATH=scripts`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/docs_refs_check.py` | Modify | Path resolution refactor (`_Index`, `_resolve`), lock read/write, content check in `check()`, `seed`/`rederive`/`accept`, CLI modes, docstring |
| `tests/test_docs_refs_check.py` | Modify | `tmp_path` tests for every lock behaviour; two repo-level tests (requirement 9) |
| `docs/.citation-lock.yaml` | Create (tool-written by `--seed`, Task 6) | One entry per covered citation part, all `seeded: true` |
| `commands/dark-factory-revise-advisory.md` | Modify (Phase 4 only) | Fail-open, `$`-free `--rederive --base HEAD` step before `git add -u` |
| `docs/features.md` | Modify (line 168, CI row only) | Name the content check and the `--rederive` command |
| `docs/run-lifecycle.md` | Modify (D4 paragraph only) | One sentence for the new Phase 4 step; bare span `:60-189` → new last line |

The existing `tests/fixtures/docs_refs_check/pass` and `fail` trees have no lock and are
left as they are.

---

## Task 0: Preconditions

**Files:** none (read-only checks).

- [ ] **Step 1: #38 is merged and the branch contains current `main`.**
  ```bash
  gh issue view 38 --repo "$FACTORY_REPO_SLUG" --json state --jq .state
  git fetch origin main && git merge-base --is-ancestor origin/main HEAD && echo "contains main"
  ```
  Expected: `CLOSED` and `contains main`. If #38 is still open, stop the implementation per
  the dependency gate (do not seed from a `main` without it). If the branch does not contain
  `origin/main`, `git merge origin/main` first.

- [ ] **Step 2: Baseline is green.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```
  Expected: `17 passed`; `Docs reference check passed for 4 doc(s).`

---

## Task 1: Refactor path resolution (no behaviour change)

The content check and `--rederive` must resolve a citation exactly the way the range check
does (including basename forms like `` `dark-factory-plan.md:217` ``). Extract the
resolution into `_Index` + `_resolve` and make `_check_path_refs` use it. The existing 17
tests are the guard for this step (pure refactor: they pass before and after).

**Files:** Modify `scripts/docs_refs_check.py`.

- [ ] **Step 1: Add the import.** Replace
  ```python
  import sys
  from pathlib import Path
  ```
  with
  ```python
  import sys
  from dataclasses import dataclass
  from pathlib import Path
  ```

- [ ] **Step 2: Replace the whole `_check_path_refs` function** (from
  `def _check_path_refs(doc: str, text: str, repo_root: Path, files: set[str]) -> list[str]:` through its `return errors`, i.e. up to the blank lines before
  `_KEY_ROW = `) with:
```python
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
```

- [ ] **Step 3: Update `check()`'s two call-site lines.** Replace
  `    files = _repo_files(root)` with `    idx = _index(_repo_files(root))`, and
  `errors.extend(_check_path_refs(str(doc), text, root, files))` with
  `errors.extend(_check_path_refs(str(doc), text, root, idx))`.

- [ ] **Step 4: Verify no behaviour change.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```
  Expected: `17 passed`; `Docs reference check passed for 4 doc(s).`

- [ ] **Step 5: Commit.**
  ```bash
  git add scripts/docs_refs_check.py
  git commit -m "refactor(#662): extract docs_refs_check path resolution into _resolve"
  ```

---

## Task 2: Lock file, content check in `check()`, `--seed` primitive

Requirements 1, 2 and 5 (the `seed()` function; the CLI comes in Task 4).

**Files:** Modify `tests/test_docs_refs_check.py`, `scripts/docs_refs_check.py`.

- [ ] **Step 1: Test imports.** In `tests/test_docs_refs_check.py`:
  - first docstring line: `(#537).` → `(#537, #662).`
  - after `from pathlib import Path` add `from typing import Optional`
  - after `import pytest` add `import yaml`
  - replace `from docs_refs_check import check, main  # noqa: E402` with
    ```python
    from docs_refs_check import LOCK_FILE, REPAIR_COMMAND, check, main, seed  # noqa: E402
    ```

- [ ] **Step 2: Append the failing tests** to the end of `tests/test_docs_refs_check.py`:
```python
# ---------------------------------------------------------------------------
# Citation lock: content check for commands/*.md citations (#662)
# ---------------------------------------------------------------------------

_CMD = "".join(f"line {i}\n" for i in range(1, 21))  # 20 distinct lines


def _lock_tree(tmp_path: Path, doc: str, cmd: str = _CMD, extra: Optional[dict] = None) -> Path:
    files = {"commands/c.md": cmd, "docs/notes.md": doc, **(extra or {})}
    return _tree(tmp_path, files)


def _doc(root: Path) -> Path:
    return root / "docs" / "notes.md"


def _content(root: Path) -> list[str]:
    """check() on docs/notes.md only, with config/workflow checks pointed at nothing."""
    return check([_doc(root)], root, root / "none.yaml", root / "none.yaml")


def _seeded(root: Path) -> Path:
    seed([_doc(root)], root)
    return root / LOCK_FILE


def _insert(root: Path, at: int, lines: list[str], rel: str = "commands/c.md") -> None:
    p = root / rel
    old = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text("".join(old[:at] + [f"{x}\n" for x in lines] + old[at:]), encoding="utf-8")


def test_no_lock_file_means_range_only(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _insert(root, 0, ["new"])
    assert _content(root) == []
    assert not (root / LOCK_FILE).exists()


def test_seed_writes_sorted_seeded_entries_and_is_byte_stable(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:7-8` and `commands/c.md:3`\n")
    lock = _seeded(root)
    text = lock.read_text(encoding="utf-8")
    assert text.endswith("seeded: true\n") and not text.endswith("\n\n")
    data = yaml.safe_load(text)
    assert [e["lines"] for e in data["entries"]] == [[3, 3], [7, 8]]
    assert list(data["entries"][0]) == ["doc", "target", "lines", "hash", "excerpt", "seeded"]
    assert data["entries"][0]["doc"] == "docs/notes.md"
    assert data["entries"][0]["target"] == "commands/c.md"
    assert data["entries"][0]["excerpt"] == "line 3"
    assert '  - doc: "docs/notes.md"\n' in text
    assert _content(root) == []
    seed([_doc(root)], root)
    assert lock.read_text(encoding="utf-8") == text


def test_seed_never_overwrites_an_entry(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    before = lock.read_text(encoding="utf-8")
    (root / "commands" / "c.md").write_text(_CMD.replace("line 3\n", "edited\n"), encoding="utf-8")
    seed([_doc(root)], root)
    assert lock.read_text(encoding="utf-8") == before


def test_hash_match_and_mismatch(tmp_path):
    root = _lock_tree(tmp_path, "intro\n`commands/c.md:3-4`\n")
    _seeded(root)
    assert _content(root) == []
    (root / "commands" / "c.md").write_text(_CMD.replace("line 4\n", "edited\n"), encoding="utf-8")
    errors = _content(root)
    assert errors == [
        'docs/notes.md:2: `commands/c.md:3-4`: commands/c.md:3-4 no longer holds the locked '
        f'content (locked: "line 3"); run: {REPAIR_COMMAND}'
    ]


def test_messages_never_claim_verification(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _seeded(root)
    _insert(root, 0, ["new"])
    errors = _content(root)
    assert errors and not any("verified" in e or "audited" in e for e in errors)


def test_missing_entry_and_orphan_entry(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:9`\n", encoding="utf-8")
    errors = _content(root)
    assert len(errors) == 1 and "commands/c.md:9 has no entry in docs/.citation-lock.yaml" in errors[0]
    assert errors[0].endswith(REPAIR_COMMAND)
    _doc(root).write_text("no citations left\n", encoding="utf-8")
    errors = _content(root)
    assert len(errors) == 1
    assert 'lock entry commands/c.md:3 ("line 3") is no longer cited' in errors[0]


def test_crlf_and_reindented_target_lines_hash_the_same(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3-4`\n")
    _seeded(root)
    crlf = _CMD.replace("line 3\n", "    line 3\t\n").replace("\n", "\r\n")
    (root / "commands" / "c.md").write_bytes(crlf.encode("utf-8"))
    assert _content(root) == []


def test_basename_citation_is_covered(tmp_path):
    root = _lock_tree(tmp_path, "`c.md:3`\n")
    lock = _seeded(root)
    assert "commands/c.md" in lock.read_text(encoding="utf-8")
    _insert(root, 0, ["new"])
    assert len(_content(root)) == 1


def test_non_commands_citations_stay_range_only(tmp_path):
    files = {
        "scripts/s.py": "a\nb\n", "scheduler.sh": "a\nb\n", "entrypoint.sh": "a\nb\n",
        "workflows/archon-dark-factory.yaml": "nodes: []\nx: 1\n", "commands/sub/n.md": "a\nb\n",
    }
    doc = "`commands/c.md:3` `scripts/s.py:2` `scheduler.sh:2` `entrypoint.sh:1` `workflow:2` `commands/sub/n.md:2`\n"
    root = _lock_tree(tmp_path, doc, extra=files)
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 1
    for rel in files:
        _insert(root, 0, ["new"], rel)
    assert _content(root) == []
```

- [ ] **Step 3: Run and see it fail.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  ```
  Expected: collection error, `ImportError: cannot import name 'LOCK_FILE' from 'docs_refs_check'`.

- [ ] **Step 4: Add imports to the script.** Replace `import argparse\nimport re` with
  ```python
  import argparse
  import hashlib
  import json
  import re
  ```

- [ ] **Step 5: Insert the lock section** directly before `def check(` in
  `scripts/docs_refs_check.py` (after `_check_feature_nodes`):
```python
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
        docs.append((doc, _doc_key(doc, repo_root), doc.read_text(encoding="utf-8")))
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
            if p.key not in entries and targets.digest(p.target, p.first, p.last) is not None:
                entries[p.key] = targets.entry(p, seeded=True)
                added += 1
    _write_lock(lock, entries)
    return added
```

- [ ] **Step 6: Replace the whole `check()` function** (up to the blank lines before
  `def main(`) with:
```python
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
```

- [ ] **Step 7: Run and see it pass.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```
  Expected: `26 passed`; the CLI still passes (no lock file exists yet, so range-only).

- [ ] **Step 8: Commit.**
  ```bash
  git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
  git commit -m "feat(#662): citation lock and content check for commands/*.md citations"
  ```

---

## Task 3: `rederive()` — relocation that never locks changed content

Requirement 3. Rules, in order: moved-unchanged (unique hash window, or `--base` tie-break),
hand-shifted (orphan with equal hash re-keyed), new prose (group without orphans), removed
prose (group without unlocked citations); everything else (zero matches, unresolved
ambiguity, mixed group, landing on lines locked for other content) changes nothing for that
citation and is reported. Docs and lock are written only after all decisions.

**Files:** Modify `tests/test_docs_refs_check.py`, `scripts/docs_refs_check.py`.

- [ ] **Step 1: Test import.** Change the import to
  `from docs_refs_check import LOCK_FILE, REPAIR_COMMAND, check, main, rederive, seed  # noqa: E402`.

- [ ] **Step 2: Append the failing tests:**
```python
def test_comma_chain_locked_and_rewritten_per_part(tmp_path):
    root = _lock_tree(tmp_path, "see `commands/c.md:3,7-8,12`\n")
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 3
    (root / "commands" / "c.md").write_text(
        _CMD.replace("line 10\n", "line 10\nnew a\nnew b\n"), encoding="utf-8")
    assert len(_content(root)) == 1
    printed, failures = rederive([_doc(root)], root)
    assert failures == []
    assert _doc(root).read_text(encoding="utf-8") == "see `commands/c.md:3,7-8,14`\n"
    assert printed[0] == "docs/notes.md:1: `commands/c.md:3,7-8,12` -> `commands/c.md:3,7-8,14`"


def _git_tree(tmp_path: Path, doc: str, cmd: str) -> Path:
    root = _lock_tree(tmp_path, doc, cmd)
    _seeded(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "base"], check=True)
    return root


_BRACES = "a\n{\nb\n{\nc\n{\nd\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_repeated_line_is_ambiguous_without_base_and_relocated_with_it(tmp_path):
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    lock = root / LOCK_FILE
    _insert(root, 0, ["new"])  # the cited `{` is now one of lines 3, 5 and 7
    doc_before, lock_before = _doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1
    assert "ambiguous, the locked content occurs at commands/c.md:3, 5, 7" in failures[0]
    assert (_doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")) == (doc_before, lock_before)
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert failures == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:5`\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_base_diff_touching_the_range_is_ambiguous(tmp_path):
    root = _git_tree(tmp_path, "`commands/c.md:4-5`\n", "a\n{\nx\n{\nx\n{\nx\n")
    # Edit line 5: the locked pair still occurs at 2-3 and 6-7, but the hunk touches 4-5.
    (root / "commands" / "c.md").write_text("a\n{\nx\n{\ny\n{\nx\n", encoding="utf-8")
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert printed == [] and len(failures) == 1 and "the --base diff does not pick one" in failures[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_unavailable_is_ambiguous(tmp_path, monkeypatch):
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    _insert(root, 0, ["new"])
    import docs_refs_check

    def no_git(*args, **kwargs):
        raise OSError("git not found")
    monkeypatch.setattr(docs_refs_check.subprocess, "run", no_git)
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert printed == [] and len(failures) == 1 and "does not pick one" in failures[0]


def test_hand_shifted_citation_is_rekeyed_and_keeps_seeded(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:5`\n")
    lock = _seeded(root)
    _insert(root, 0, ["new"])
    # Citation 3 was shifted by hand, correctly; citation 5 was not touched.
    _doc(root).write_text("`commands/c.md:4` `commands/c.md:5`\n", encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert failures == [] and _content(root) == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4` `commands/c.md:6`\n"
    entries = yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]
    assert [(e["lines"], e.get("seeded")) for e in entries] == [([4, 4], True), ([6, 6], True)]


def test_new_prose_is_locked_without_seeded(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:12`\n", encoding="utf-8")
    assert rederive([_doc(root)], root)[1] == []
    assert _content(root) == []
    entries = {tuple(e["lines"]): e for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]}
    assert entries[(3, 3)].get("seeded") is True and "seeded" not in entries[(12, 12)]


def test_removed_prose_alone_removes_the_orphan(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:5`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3`\n", encoding="utf-8")
    assert rederive([_doc(root)], root)[1] == []
    assert [e["lines"] for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]] == [[3, 3]]


def test_mixed_group_fails_and_writes_nothing(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:9`\n", encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1
    assert "commands/c.md:9 is not the locked content" in failures[0]
    assert "locked content is now at commands/c.md:3" in failures[0]
    assert lock.read_text(encoding="utf-8") == before
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:9`\n"


def test_wrong_hand_delta_reports_correct_lines_and_locks_nothing(tmp_path):
    # Tonight's case: five citations, 16 lines inserted above, doc shifted by 15 instead.
    root = _lock_tree(tmp_path, "`commands/c.md:3` `:x` `commands/c.md:5` `commands/c.md:7-8`\n"
                                "`commands/c.md:10` `commands/c.md:12`\n")
    lock = _seeded(root)
    _insert(root, 0, [f"inserted {i}" for i in range(16)])
    wrong = "`commands/c.md:18` `:x` `commands/c.md:20` `commands/c.md:22-23`\n`commands/c.md:25` `commands/c.md:27`\n"
    _doc(root).write_text(wrong, encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 5
    for now in ("19", "21", "23-24", "26", "28"):
        assert any(f"locked content is now at commands/c.md:{now}" in f for f in failures), now
    assert lock.read_text(encoding="utf-8") == before
    assert _doc(root).read_text(encoding="utf-8") == wrong


def test_zero_matches_fails_and_changes_nothing(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    (root / "commands" / "c.md").write_text(_CMD.replace("line 3\n", ""), encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1 and lock.read_text(encoding="utf-8") == before


def test_same_citation_twice_in_one_doc(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\nagain `commands/c.md:3`\n")
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 1
    _insert(root, 0, ["new"])
    assert len(_content(root)) == 2
    printed, failures = rederive([_doc(root)], root)
    assert failures == [] and len(printed) == 3  # two rewrites + lock updated
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4`\nagain `commands/c.md:4`\n"


def test_failing_citation_alongside_a_fixable_one(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:9`\n")
    _seeded(root)
    (root / "commands" / "c.md").write_text("new\n" + _CMD.replace("line 9\n", "edited\n"), encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert len(failures) == 1 and "commands/c.md:9" in failures[0]
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4` `commands/c.md:9`\n"


def test_moved_content_never_lands_on_lines_locked_for_other_content(tmp_path):
    # Line 3 moves onto line 4, whose citation was edited in place: must not lock it.
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:4`\n")
    lock = _seeded(root)
    (root / "commands" / "c.md").write_text(_CMD.replace("line 4\n", "").replace("line 3\n", "x\nline 3\n"),
                                            encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert len(failures) == 2 and any("but those lines are locked for other content" in f for f in failures)
    assert lock.read_text(encoding="utf-8") == before
```

- [ ] **Step 3: Run and see it fail.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  ```
  Expected: collection error, `ImportError: cannot import name 'rederive'`.

- [ ] **Step 4: Imports.** Replace `from dataclasses import dataclass` with
  ```python
  from dataclasses import dataclass, replace
  from itertools import zip_longest
  ```

- [ ] **Step 5: Insert the re-derive code** directly after the `seed()` function (before
  `def check(`):
```python
def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=True,
    ).stdout


def _base_position(repo_root: Path, base: str, target: str, first: int, last: int) -> Optional[int]:
    """Where lines *first*-*last* of *target* at ``merge-base(base, HEAD)`` are now.

    ``None`` when git or the base is unavailable or a hunk touches the range.
    """
    try:
        merge_base = _git(repo_root, "merge-base", base, "HEAD").strip()
        diff = _git(repo_root, "diff", "-U0", "--no-color", "--no-ext-diff", merge_base, "--", target)
    except (OSError, subprocess.CalledProcessError):
        return None
    delta = 0
    for m in _HUNK.finditer(diff):
        start = int(m[1])
        old_count = int(m[2]) if m[2] is not None else 1
        new_count = int(m[3]) if m[3] is not None else 1
        if old_count == 0:  # pure insertion after old line `start`
            if start >= last:
                continue
            if start >= first:
                return None
        else:
            if start > last:
                continue
            if start + old_count - 1 >= first:
                return None
        delta += new_count - old_count
    return first + delta


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
        if pos is None and len(found) > 1 and base:
            shifted = _base_position(repo_root, base, entry.target, entry.first, entry.last)
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
            why = "the --base diff does not pick one" if base else "pass --base REF to pick one"
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
            if targets.digest(target, key[2], key[3]) is not None:
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
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
    if _write_lock(lock, result):
        printed.append(f"{LOCK_FILE}: updated")
    return printed, failures
```

- [ ] **Step 6: Run and see it pass.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  ```
  Expected: `39 passed` (the three git tests are skipped only if `git` is absent).

- [ ] **Step 7: Commit.**
  ```bash
  git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
  git commit -m "feat(#662): docs_refs_check --rederive relocates moved citations by content"
  ```

---

## Task 4: `accept()`, CLI modes, seeded note, docstring

Requirements 2 (CLI messages, `--lock-file`), 4 (`--accept`), 5 (seeded note) and the
module docstring half of requirement 10.

**Files:** Modify `tests/test_docs_refs_check.py`, `scripts/docs_refs_check.py`.

- [ ] **Step 1: Test import.** Change the import to
  `from docs_refs_check import LOCK_FILE, REPAIR_COMMAND, accept, check, main, rederive, seed  # noqa: E402`.

- [ ] **Step 2: Append the failing tests:**
```python
def test_edited_range_fails_rederive_then_accept_locks_it(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3-4` and `commands/c.md:9`\n")
    lock = _seeded(root)
    (root / "commands" / "c.md").write_text(_CMD.replace("line 4\n", "edited\n"), encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1 and "the cited range was edited" in failures[0]
    assert lock.read_text(encoding="utf-8") == before
    printed, failures = accept(_doc(root), "commands/c.md:3-4", root)
    assert failures == []
    assert printed == ["accepted docs/notes.md:1 commands/c.md:3-4: line 3 -> line 3"]
    assert _content(root) == []
    entries = yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]
    assert [e.get("seeded") for e in entries] == [None, True]


def test_accept_adds_a_missing_entry_and_refuses_unknown_citations(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:9`\n", encoding="utf-8")
    printed, failures = accept(_doc(root), "`commands/c.md:9`", root)
    assert printed == ["accepted docs/notes.md:1 commands/c.md:9: (no entry) -> line 9"]
    assert _content(root) == []
    before = lock.read_text(encoding="utf-8")
    printed, failures = accept(_doc(root), "commands/c.md:11", root)
    assert printed == [] and "not a commands/*.md citation in this doc" in failures[0]
    assert lock.read_text(encoding="utf-8") == before


def test_insert_five_lines_then_rederive_shifts_every_citation(tmp_path):
    doc = "a `commands/c.md:3` b\nc `c.md:5-6` d `scripts/s.py:1` `:2`\n`commands/c.md:3`\n"
    root = _lock_tree(tmp_path, doc, extra={"scripts/s.py": "x\ny\n"})
    lock = _seeded(root)
    _insert(root, 0, [f"top {i}" for i in range(5)])
    errors = _content(root)
    assert len(errors) == 3 and all(e.endswith(REPAIR_COMMAND) for e in errors)
    # Not a git repo, but every match is unique, so --base is never consulted.
    assert main([str(_doc(root)), "--repo-root", str(root), "--rederive", "--base", "HEAD",
                 "--config", str(root / "none.yaml"), "--workflow", str(root / "none.yaml")]) == 0
    assert _doc(root).read_text(encoding="utf-8") == (
        "a `commands/c.md:8` b\nc `c.md:10-11` d `scripts/s.py:1` `:2`\n`commands/c.md:8`\n"
    )
    assert _content(root) == []
    doc_after, lock_after = _doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")
    assert rederive([_doc(root)], root) == ([], [])
    assert (_doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")) == (doc_after, lock_after)
    assert "seeded: true" in lock_after


def test_cli_modes_and_exit_codes(tmp_path, capsys):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    common = ["--repo-root", str(root), "--config", str(root / "none.yaml"),
              "--workflow", str(root / "none.yaml")]
    doc = str(_doc(root))
    assert main([doc, "--rederive", *common]) == 1  # no lock yet
    assert main([doc, "--seed", *common]) == 0
    out = capsys.readouterr().out
    assert "1 citation(s) were locked at seed and have not been read against their target" in out
    assert main([doc, *common]) == 0
    _insert(root, 0, ["new"])
    assert main([doc, *common]) == 1
    assert REPAIR_COMMAND in capsys.readouterr().err
    assert main([doc, "--rederive", *common]) == 0
    assert "`commands/c.md:3` -> `commands/c.md:4`" in capsys.readouterr().out
    assert main(["--accept", doc, "commands/c.md:99", *common]) == 1
    assert main(["--accept", doc, "commands/c.md:4", *common]) == 0
    assert "accepted docs/notes.md:1 commands/c.md:4: line 3 -> line 3" in capsys.readouterr().out
    assert main([doc, *common]) == 0
    assert "locked at seed" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([doc, "--base", "HEAD", *common])
    with pytest.raises(SystemExit):
        main([doc, "--seed", "--rederive", *common])


def test_unreadable_lock_is_a_one_line_error_in_every_mode(tmp_path, capsys):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    common = ["--repo-root", str(root), "--config", str(root / "none.yaml"),
              "--workflow", str(root / "none.yaml")]
    doc = str(_doc(root))
    for bad in ("entries: [1, 2\n", "entries: 5\n", "entries:\n  - doc: x\n"):
        lock.write_text(bad, encoding="utf-8")
        for mode in ([doc], [doc, "--seed"], [doc, "--rederive"], ["--accept", doc, "commands/c.md:3"]):
            capsys.readouterr()
            assert main([*mode, *common]) == 1
            assert "cannot load citation lock" in capsys.readouterr().err
        assert lock.read_text(encoding="utf-8") == bad
```

- [ ] **Step 3: Run and see it fail.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  ```
  Expected: collection error, `ImportError: cannot import name 'accept'`.

- [ ] **Step 4: Insert `accept()` and `seeded_count()`** directly after the `rederive()`
  function (before `def check(`):
```python
def accept(doc_path: PathLike, citation: str, repo_root: PathLike = ".",
           lock_path: Optional[PathLike] = None) -> tuple[list[str], list[str]]:
    """Lock every part of *citation* in *doc_path* at its current content."""
    root = Path(repo_root)
    lock = Path(lock_path) if lock_path else root / LOCK_FILE
    citation = citation.strip("`")
    if not lock.is_file():
        return [], [f"{lock}: no citation lock; nothing accepted"]
    (_, key, text), = _load_docs([doc_path], root)
    targets, idx = _Targets(root), _index(_repo_files(root))
    parts = [p for p in _parts(key, text, idx) if p.span == citation]
    if not parts:
        return [], [f"{key}: `{citation}` is not a commands/*.md citation in this doc; nothing accepted"]
    bad = [p for p in parts if targets.digest(p.target, p.first, p.last) is None]
    if bad:
        return [], [f"{key}:{bad[0].lineno}: `{citation}`: line {_lines_text(bad[0].first, bad[0].last)} "
                    f"is out of range; nothing accepted"]
    entries = _load_lock(lock)
    printed = []
    for p in {p.key: p for p in parts}.values():
        old = entries.get(p.key)
        entries[p.key] = targets.entry(p)
        printed.append(f"accepted {key}:{parts[0].lineno} {citation}: "
                       f"{old.excerpt if old else '(no entry)'} -> {entries[p.key].excerpt}")
    _write_lock(lock, entries)
    return printed, []


def seeded_count(lock_path: Path) -> int:
    try:
        return sum(e.seeded for e in _load_lock(lock_path).values())
    except (OSError, yaml.YAMLError, ValueError):
        return 0
```

- [ ] **Step 5: Replace the whole `main()` function** (up to the blank lines before
  `if __name__ == "__main__":`) with:
```python
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
```

- [ ] **Step 6: Replace the module docstring** (everything from the first `"""` to the
  closing `"""` before `from __future__ import annotations`) with:
```python
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
```

  (`main()` uses its first line as the argparse description. The words "verified" and
  "audited" appear nowhere in the script.)

- [ ] **Step 7: Run and see it pass.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  grep -nE 'verified|audited' scripts/docs_refs_check.py || echo "none"
  ```
  Expected: `44 passed`; `Docs reference check passed for 4 doc(s).`; `none`.

- [ ] **Step 8: Commit.**
  ```bash
  git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
  git commit -m "feat(#662): docs_refs_check --seed/--rederive/--accept CLI and seeded note"
  ```

---

## Task 5: Advisory-fix hook (`commands/dark-factory-revise-advisory.md` Phase 4)

Requirement 8. The inserted text contains **no `$` character** (so it adds no Archon
variable and cannot move the first substituted token, `$WORKFLOW_ID` on line 8, which the
#236 cache-shape lint ratchets). It runs after the "no changes" check and before
`git add -u`, uses `--base HEAD` (the fix agent's edits are still uncommitted, so
`merge-base HEAD HEAD` = `HEAD` and the diff is exactly the fix), calls the clone's
`scripts/docs_refs_check.py`, never `--accept`, and never edits `commands/`.

**Files:** Modify `commands/dark-factory-revise-advisory.md`.

- [ ] **Step 1: Write the failing check** (this is a prose command file; the guard is a
  shell assertion, not a new pytest file — the plan's scope does not add a test file here):
  ```bash
  grep -c 'rederive --base HEAD' commands/dark-factory-revise-advisory.md
  ```
  Expected now: `0`.

- [ ] **Step 2: Edit Phase 4.** Replace exactly this text:
````markdown
2. Stage all modified tracked files (no untracked):
   ```bash
   git add -u
   ```
3. Commit:
   ```bash
   git commit -m "fix(review): address ${ADVISORY_COUNT} advisory finding(s) from AI code review"
   ```
4. Push:
````
  with:
````markdown
2. Re-derive doc citations the fix moved (#662). Only when the clone has
   `docs/.citation-lock.yaml`; skipped for a clone without it (the MarketHawk instance,
   a branch cut before #662). Fail-open: a non-zero exit is logged and the phase continues
   to its commit. It edits only the four reference docs and the lock file, never a file
   under `commands/`; both are tracked, so its changes ride in the `git add -u` below:
   ```bash
   if [ -f docs/.citation-lock.yaml ] && [ -f scripts/docs_refs_check.py ]; then
     python3 scripts/docs_refs_check.py --rederive --base HEAD docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
       || echo "revise-advisory: WARNING — citation re-derive did not pass (continuing; see its output)"
   else
     echo "revise-advisory: no docs/.citation-lock.yaml in this clone — citation re-derive skipped"
   fi
   ```
3. Stage all modified tracked files (no untracked):
   ```bash
   git add -u
   ```
4. Commit:
   ```bash
   git commit -m "fix(review): address ${ADVISORY_COUNT} advisory finding(s) from AI code review"
   ```
5. Push:
````
  (The `\` at the end of the `python3` line is a literal shell line continuation.)

- [ ] **Step 3: Verify.**
  ```bash
  grep -c 'rederive --base HEAD' commands/dark-factory-revise-advisory.md
  git diff -U0 -- commands/dark-factory-revise-advisory.md | grep '^+[^+]' | grep -c '\$' || true
  wc -l < commands/dark-factory-revise-advisory.md
  python -m pytest tests/test_revise_advisory_command_render_prompt.py tests/test_command_headless_contract.py tests/test_side_effect.py -q
  ```
  Expected: `1`; `0` (no `$` on any added line); `202` (189 + 13 lines — note the actual
  number, Task 7 uses it); tests pass.

- [ ] **Step 4: Commit.**
  ```bash
  git add commands/dark-factory-revise-advisory.md
  git commit -m "feat(#662): revise-advisory re-derives doc citations before its commit"
  ```

---

## Task 6: Seed the lock and add the repo-level tests (requirement 9)

The lock is generated by `--seed` from this branch's own head, so the check passes on the
PR and on the merge commit. It is seeded before the Task 7 doc edits only because that new
doc text names `docs/.citation-lock.yaml`, which the path check requires to exist; those
edits change no covered citation, and the content check run in Task 7 proves it. Never
delete and re-seed after this task; if `main` moves, see Task 8 Step 4. If any `commands/*.md` file or covered
citation is edited after this task, run `--rederive --base origin/main`, never `--seed` over a
deleted lock.

**Files:** Modify `tests/test_docs_refs_check.py`; Create `docs/.citation-lock.yaml`
(tool-written).

- [ ] **Step 1: Append the failing repo-level tests:**
```python
# ---------------------------------------------------------------------------
# This repo's own docs and lock (#662 requirement 9)
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_REFERENCE_DOCS = [_REPO / d for d in
                   ("docs/run-lifecycle.md", "docs/scheduler.md", "docs/features.md", "docs/configuration.md")]


def test_repo_docs_pass_the_reference_check_with_the_citation_lock():
    errors = check(_REFERENCE_DOCS, _REPO)
    assert errors == [], "\n".join(errors)


def test_repo_citation_lock_exists_and_covers_every_commands_citation():
    import docs_refs_check as d

    lock = _REPO / LOCK_FILE
    assert lock.is_file(), f"{LOCK_FILE} is missing: the commands/*.md content check is off"
    idx = d._index(d._repo_files(_REPO))
    covered = {
        p.key for doc in _REFERENCE_DOCS
        for p in d._parts(d._doc_key(doc, _REPO), doc.read_text(encoding="utf-8"), idx)
    }
    locked = set(d._load_lock(lock))
    assert covered and covered <= locked, sorted(covered - locked)
```

- [ ] **Step 2: Run and see it fail.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  ```
  Expected: `1 failed, 45 passed` —
  `test_repo_citation_lock_exists_and_covers_every_commands_citation` fails with
  `docs/.citation-lock.yaml is missing`. (The other repo test passes: no lock means
  range-only. Deleting the lock later must make this test fail, which is the point. After
  Task 7 adds doc text naming `docs/.citation-lock.yaml`, deleting the lock fails both repo
  tests: two path errors plus the coverage assertion.)

- [ ] **Step 3: Report the covered-part counts** (for the PR body):
  ```bash
  PYTHONPATH=scripts python -c 'from pathlib import Path; import docs_refs_check as d; idx = d._index(d._repo_files(Path("."))); ps = [p for doc in d.REFERENCE_DOCS for p in d._parts(doc, Path(doc).read_text(encoding="utf-8"), idx)]; print("parts", len(ps), "distinct", len({p.key for p in ps}), "targets", len({p.target for p in ps}))'
  ```
  Expected at 4e2b1bf: `parts 84 distinct 80 targets 8` (the spec says all nine command files
  are cited; `commands/dark-factory-revise-advisory.md` is only cited without a line number,
  which is why it gets no entry). After #38 the numbers may differ;
  record what is printed.

- [ ] **Step 4: Seed.**
  ```bash
  python scripts/docs_refs_check.py --seed docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  cp docs/.citation-lock.yaml /tmp/lock-1
  python scripts/docs_refs_check.py --seed docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  cmp /tmp/lock-1 docs/.citation-lock.yaml && echo "byte-stable"
  grep -c '^  - doc:' docs/.citation-lock.yaml; grep -c '^    seeded: true$' docs/.citation-lock.yaml
  ```
  Expected: `--seed added 80 entries to docs/.citation-lock.yaml`, then
  `80 citation(s) were locked at seed and have not been read against their target`,
  `Docs reference check passed for 4 doc(s).`; second run `--seed added 0 entries ...`;
  `byte-stable`; the two counts are equal to each other and to the "distinct" count from
  Step 3. No entry targets `commands/dark-factory-revise-advisory.md`
  (`grep -c 'target: "commands/dark-factory-revise-advisory.md"' docs/.citation-lock.yaml` → `0`).

- [ ] **Step 5: Run and see it pass.**
  ```bash
  python -m pytest tests/test_docs_refs_check.py -q
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md; echo "exit=$?"
  ```
  Expected: `46 passed`; the seeded note plus `Docs reference check passed for 4 doc(s).`,
  `exit=0`.

- [ ] **Step 6: Commit.**
  ```bash
  git add tests/test_docs_refs_check.py docs/.citation-lock.yaml
  git commit -m "feat(#662): seed docs/.citation-lock.yaml and add repo-level citation tests"
  ```

---

## Task 7: Docs — features.md CI row and run-lifecycle.md D4 paragraph

Requirement 10 (docs half). These edits change no path-carrying citation number; the only
number changed is the bare span `:60-189`.

**Files:** Modify `docs/features.md` (line 168 only), `docs/run-lifecycle.md` (D4 only).

- [ ] **Step 1: features.md CI row.** On the `| **CI.** ...` row (line 168), replace
````text
the reference-docs check (paths, line ranges, config keys and node ids in `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` and `docs/configuration.md`) and a Docker build.
````
  with
````text
the reference-docs check (paths, line ranges, config keys and node ids in `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` and `docs/configuration.md`; citations into `commands/*.md` are also checked by content against the hashes in `docs/.citation-lock.yaml`, and after a command-file edit `python scripts/docs_refs_check.py --rederive --base origin/main` with the same four docs repairs the ones that moved) and a Docker build.
````

- [ ] **Step 2: run-lifecycle.md D4 paragraph** (`**D4. Revise advisory**`, around lines
  964-971). Replace
````text
with `git add -u`, pushes, posts an "Advisory Findings Addressed" PR review comment and
writes `revise_advisory.md` (`:60-189`). These changes are pushed after validation,
conformance and code review have run; nothing re-runs those on the revised code.
````
  with (use the line count from Task 5 Step 3 in place of `202` if it differs):
````text
with `git add -u`, pushes, posts an "Advisory Findings Addressed" PR review comment and
writes `revise_advisory.md` (`:60-202`). Before staging, when the clone has
`docs/.citation-lock.yaml`, it runs `scripts/docs_refs_check.py --rederive --base HEAD` on
the four reference docs (fail-open) so `commands/*.md` citations the fix moved are
re-derived in the same commit. These changes are pushed after validation,
conformance and code review have run; nothing re-runs those on the revised code.
````

- [ ] **Step 3: Verify** (the lock exists now, so this runs the content check too and
  proves the edits moved no covered citation):
  ```bash
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md; echo "exit=$?"
  python -m pytest tests/test_docs_refs_check.py -q
  git diff -- docs/features.md docs/run-lifecycle.md | grep -E '^[-+][^-+]' | grep -oE '`[^`]*commands/[^`]*:[0-9][^`]*`' || echo "no path-carrying commands citation changed"
  git diff --quiet -- docs/.citation-lock.yaml && echo "lock unchanged"
  ```
  Expected: the seeded note, `Docs reference check passed for 4 doc(s).`, `exit=0`;
  `46 passed`; `no path-carrying commands citation changed`; `lock unchanged`.

- [ ] **Step 4: Commit.**
  ```bash
  git add docs/features.md docs/run-lifecycle.md
  git commit -m "docs(#662): document the commands/*.md content check and the advisory re-derive step"
  ```

---

## Task 8: Verification, acceptance checks, merge precondition

**Files:** none modified (unless Step 4 applies, which modifies only docs and the lock via
the tool).

- [ ] **Step 1: Full suite and CI parity.**
  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```
  Expected: all pass (prototype on 4e2b1bf: `4641 passed`), both DAG checks exit 0, the
  docs check passes and prints the seeded note.

- [ ] **Step 2: Acceptance scenario on a throwaway clone** (never on the branch):
  ```bash
  rm -rf /tmp/acc && git clone -q --local . /tmp/acc && cd /tmp/acc
  D="docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md"
  (printf 'x1\nx2\nx3\nx4\nx5\n'; cat commands/dark-factory-plan.md) > /tmp/p && cp /tmp/p commands/dark-factory-plan.md
  python scripts/docs_refs_check.py $D; echo "exit=$?"
  python scripts/docs_refs_check.py --rederive --base HEAD $D; echo "exit=$?"
  git diff --stat -- docs/
  python scripts/docs_refs_check.py --rederive --base HEAD $D | grep -c -- ' -> ' || true
  rm -f docs/.citation-lock.yaml && python scripts/docs_refs_check.py $D 2>&1 | tee /tmp/nolock.txt
  grep -c 'no such file or directory' /tmp/nolock.txt; grep -cE 'citation lock|lock entry|locked' /tmp/nolock.txt || true
  python -m pytest tests/test_docs_refs_check.py -q -k repo_citation_lock 2>&1 | tail -1
  cd - && rm -rf /tmp/acc
  ```
  Expected (at 4e2b1bf): first check `exit=1` with one error per affected citation (12),
  each ending in the `--rederive` command; `--rederive` prints 12 `old -> new` lines (each
  shifted by exactly five), `docs/.citation-lock.yaml: updated`, `exit=0`; docs diff touches
  only those citation numbers; the second `--rederive` prints `0` rewrites. Without the lock the checker is range-only
  again: it reports **exactly two** errors, both
  `` `docs/.citation-lock.yaml`: no such file or directory in the repo `` (the Task 7 doc
  text names the deleted file, at `docs/features.md:168` and in the D4 paragraph), so the
  counts are `2` and `0` (no content, entry or orphan error, no seeded note), and the two
  errors are at `docs/features.md:168` and the D4 line of `docs/run-lifecycle.md` (971 at
  378b426) — any other path error is a real problem; this exit 1
  is the path check working, **not** the content check running — do not change the docs or
  the script over it. The repo lock test then fails (`1 failed`). The no-lock behaviour on a
  tree that does not name the lock is covered by `test_no_lock_file_means_range_only`.

- [ ] **Step 3: Scope checks** (two-dot diff, per memory):
  ```bash
  git fetch origin main
  git diff --name-only origin/main HEAD
  git diff origin/main HEAD -- .github workflows CLAUDE.md deploy Dockerfile | wc -l
  git diff -U0 origin/main HEAD -- commands/ | grep '^+[^+]' | grep -c '\$' || true
  git diff --stat origin/main HEAD -- docs/ ':!docs/superpowers'
  ```
  Expected: changed files are exactly `commands/dark-factory-revise-advisory.md`,
  `docs/.citation-lock.yaml`, `docs/features.md`, `docs/run-lifecycle.md`,
  `scripts/docs_refs_check.py`, `tests/test_docs_refs_check.py` (plus the refine artifacts
  under `docs/superpowers/` and `.archon/memory/` already on the branch); `0`; `0`; under
  `docs/` only the lock, the features.md CI row and the D4 paragraph.

- [ ] **Step 4: Merge precondition (spec Rollout).** Before handing off:
  ```bash
  git fetch origin main
  git merge-base --is-ancestor origin/main HEAD && echo "contains main" || echo "main moved"
  ```
  If `main moved`: `git merge origin/main`, then
  `python scripts/docs_refs_check.py --rederive --base origin/main docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md` and re-run Step 1.
  **Never delete and re-seed the lock.** If `--rederive` reports a citation it cannot repair,
  read the cited lines and the sentence, fix the doc citation (or, only if the sentence still
  holds for the new content, `--accept` that one citation and list it in the PR body).
  Commit any changes as `fix(#662): re-derive citations after merging main`.

- [ ] **Step 5: PR body notes** (for whoever opens the PR):
  - covered-part / distinct-entry / target counts from Task 6 Step 3 and the entry count
    from Task 6 Step 4, all `seeded: true`;
  - `--accept` used: none (or the list, if Step 4 needed it);
  - bare continuation spans are not covered (requirement 7); the audit is #666;
  - merge precondition (b), an operator check at merge time: no open PR under a signed hold
    changes a `commands/*.md` file or a covered citation; if one does, it merges first;
  - an expected blast-radius hold on `scripts/docs_refs_check.py` /
    `commands/dark-factory-revise-advisory.md` is accepted (spec "Hold expectation");
  - repair command for in-flight branches:
    `python scripts/docs_refs_check.py --rederive --base origin/main docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`.

---

## Spec requirement → task map

| Spec item | Task |
|---|---|
| R1 lock file format, keys, sort order, JSON-quoted scalars, single trailing newline, byte-stable | 2 (`_dump_lock`, seed tests), 6 |
| R2 content check, no-lock = range-only, orphan, messages with repair command, `--lock-file`, no "verified"/"audited" | 2, 4 |
| R3 `--rederive [--base]` rules and invariant | 3, 4 (CLI) |
| R4 `--accept DOC CITATION` | 4 |
| R5 `--seed`, `seeded` marker, stdout note | 2, 4, 6 |
| R6 no hand re-derivation; seed from `main` | 6, 8 |
| R7 bare spans unchanged | 2 (`_parts` ignores them), test `test_insert_five_lines...` keeps `` `:2` `` |
| R8 revise-advisory Phase 4 hook, `$`-free, fail-open | 5 |
| R9 repo-level pytest | 6 |
| R10 docstring, features.md CI row, run-lifecycle D4 + `:60-189` | 4, 7 |
| Acceptance criteria | 6, 7, 8 (Steps 1-3); unit tests in 2-4 |

# memory_write.py: create .archon/memory on first run and stop hardcoding the project name

**Issue:** #444

Split out of #443 (finding 1); the rest of #443's findings stay for Wave 3.

---

## Overview / Problem Statement

`scripts/memory_write.py` is the standalone, zero-dependency CLI that every gate phase
(the refine phase command directly — `commands/dark-factory-refine.md:197` — and the
conformance and code-review gates through `gate_lib.sh::write_memory_entry()`, at
`commands/dark-factory-conformance.md:617` and `commands/dark-factory-code-review.md:307`)
shells out to for writing `.archon/memory/*.md` entries and
their `index.jsonl` provenance rows. Two independent defects, both confirmed by reading
`scripts/memory_write.py` directly:

1. **Missing directory creation (`scripts/memory_write.py:171-175` and `:211-215`, the two
   markdown writes; `:108-113`, the `index.jsonl` append).** None of the three ever calls
   `mkdir`. (`:139-143` is the *read* block — guarded by `target.exists()`, it cannot fail
   on a missing parent, so it is not a site of this defect.) On a fresh target,
   nothing else in the pipeline creates `.archon/memory/` ahead of the first write, so
   `target.write_text(...)` raises `FileNotFoundError` (a subclass of `OSError`), which is
   caught and turned into `memory-write: error writing ...`, exit 1. Every call site treats
   this script as best-effort, so the observable symptom is exactly the issue's report:
   memory writes silently never happen on a fresh target. Observed on the jobfinder
   onboarding run (2026-09-26, session `factorytest-31`).
2. **Hardcoded `"project": "markethawk"` (`scripts/memory_write.py:95`)**, inside
   `_write_index()`'s `index.jsonl` record. This is a previously-named, previously-deferred
   defect (`docs/archive/2026-08-21-state-governance-scorecard-design.md`,
   "scope-non-expansion-fail") — every non-MarketHawk Dark Factory instance (jobfinder, or
   this repo self-targeting as `dark-factory`) writes mis-scoped provenance into its own
   memory ledger.

Both are one-file, contained fixes with no gate/budget/breaker/config surface involved,
matching the issue's own "Size: S" framing.

---

## Requirements

Distilled from the issue body and the Q&A below:

1. `memory_write.py` creates `.archon/memory/` (and any missing parents) before either the
   markdown write or the `index.jsonl` append — `target.parent.mkdir(parents=True,
   exist_ok=True)`, called once in `main()` **after** the empty-`--text` guard
   (`scripts/memory_write.py:124-126`) and **before** the existing `target.is_dir()` check
   (`:136`), so an invalid-input run no longer creates the directory as a side effect. When
   the directory did not already exist, print `memory-write: created <absolute dir>` to
   stdout, so a wrong-cwd invocation is visible in the phase log instead of silently
   materializing a stray `.archon/memory/` tree.
2. `_write_index()`'s `"project"` field is resolved dynamically instead of the `"markethawk"`
   literal, via a three-step fallback chain:
   - `FACTORY_REPO` env var, if set and non-empty (bare repo name, e.g. `markethawk`,
     `dark-factory`, `jobfinder` — matches the existing convention in
     `scripts/identity.sh` / `scripts/factory_core/identity.py`).
   - else `FACTORY_PRODUCT_NAME` env var, if set and non-empty, used as-is (display name,
     e.g. `MarketHawk`) — no case or format normalization.
   - else the git remote: run `git -C <target.parent> remote get-url origin` (after the
     `mkdir` from Requirement 1, so the directory is guaranteed to exist) and take the last
     path segment of the URL — `re.split(r"[/:]", url.rstrip("/"))[-1]` — with any
     trailing `.git` stripped (e.g. `git@github.com:omniscient/jobfinder.git` →
     `jobfinder`). Splitting on `:` as well as `/` keeps scp-style remotes that carry no
     path slash (`git@github.com:jobfinder.git`) from falling through; `re` is already
     imported (`scripts/memory_write.py:21`).
   - else, if the git lookup itself fails for any reason (no `.git`, no `origin` remote,
     git not installed, non-zero exit, empty stdout, timeout), fall back to the literal
     `"unknown"`. The lookup must never raise — a provenance-field lookup failing the write
     would reproduce the exact class of bug this issue exists to fix.
3. No change to the memory record schema (`docs/dark-factory-memory-contract.md`) — this is
   a value-resolution fix, not a field-shape change. `project` remains a single string field
   in the `index.jsonl` record.
4. A unit test for each defect (directory auto-creation; each rung of the fallback chain,
   including the terminal `"unknown"` case) added to `tests/test_memory_write.py`, in the
   existing pure-filesystem, no-mocking, subprocess-invocation style. Each fallback-rung
   test controls the environment explicitly, because the existing `run()` helper
   (`tests/test_memory_write.py:18-24`) calls `subprocess.run` with no `env=`, so the child
   inherits `os.environ`: `monkeypatch.setenv(...)` for the `FACTORY_REPO` and
   `FACTORY_PRODUCT_NAME` rungs, and `monkeypatch.delenv("FACTORY_REPO", raising=False)`
   plus `monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)` for the git-remote and
   `"unknown"` rungs. (pytest's `monkeypatch` mutates `os.environ`, which `run()` inherits,
   so `run()` itself needs no new parameter.) The suite must be green both with those two
   vars unset and with them set — run it once with `FACTORY_REPO=dark-factory` and
   `FACTORY_PRODUCT_NAME=MarketHawk` exported. Without this, the fallback tests pass on a
   developer host and in CI but fail inside every run container, where `run-compose.yml:33`
   and `:44` set both vars.
   Where these tests run: `python -m pytest tests/ -v` in CI
   (`.github/workflows/ci.yml:13`, which sets only `PYTHONPATH`) and inside the factory
   container; host-Windows pytest is unreliable and is not the gate. Nothing is added to
   `tests/test_memory_write_gate.sh` — that file is not in `ci.yml`'s bash-test list, so it
   does not run in CI at all.
   The two git-dependent rungs are deterministic by construction rather than by
   environment-scrubbing: the terminal-`"unknown"` test `git init`s a fresh directory under
   `tmp_path` with **no** `origin` remote and points `--target` inside it, so `git remote
   get-url origin` exits non-zero whatever git ancestry the host happens to have; the
   positive git rung does `git init` plus `git remote add origin
   https://github.com/omniscient/jobfinder.git` and asserts `project == "jobfinder"`.
5. The existing `TestIndexJsonl::test_index_record_has_required_fields` assertion
   (`record["project"] == "markethawk"`) is updated to assert against an explicitly
   env-injected value rather than a literal that happened to match the old hardcode.
6. **Every failure of the new code paths emits exactly one stderr line, and never a
   traceback.** This is the requirement that stops the fix from reproducing the class of bug
   it repairs: the gate call sites throw the exit status away and log success regardless —
   `commands/dark-factory-code-review.md:307-309` and
   `commands/dark-factory-conformance.md:617-619` call `write_memory_entry` with no status
   check and then unconditionally `echo "memory-write: wrote [AVOID] to $TARGET"` — so
   anything this script does not say out loud is invisible.
   - Wrap the Requirement 1 `mkdir` in `try/except OSError` and exit 1 with
     `memory-write: error: cannot create {target.parent}: {exc}`, matching the existing
     message style at `scripts/memory_write.py:136-138`. `OSError` is the right class: it
     covers EACCES/EROFS and also `FileExistsError`, which `exist_ok=True` does **not**
     swallow when `.archon/memory` exists as a *file* rather than a directory.
   - When `_resolve_project()` returns the terminal `unknown`, print this to stderr and let
     the write succeed:
     `memory-write: WARNING: project unresolved (FACTORY_REPO/FACTORY_PRODUCT_NAME unset, no git origin) - writing project:unknown`
   - When the env-resolved project (from `FACTORY_REPO` or `FACTORY_PRODUCT_NAME`) differs
     from the git-origin-derived name and the origin lookup itself succeeded, print this
     once to stderr and let the write succeed:
     `memory-write: WARNING: project '<resolved>' does not match git origin '<derived>' (FACTORY_REPO defaulted?)`
     Compare case-insensitively, so the legitimate `FACTORY_PRODUCT_NAME=MarketHawk` against
     origin `markethawk` pair does not warn. This is the check that would have caught the
     jobfinder case: `FACTORY_REPO` is never actually unset in a run container —
     `run-compose.yml:33` hard-defaults it to `markethawk` (as do `scripts/identity.sh:4`
     and `scripts/factory_core/identity.py:5`) — so an instance.env that omits it silently
     resolves to `markethawk` again and the git-remote rung never fires. The cost is one
     extra `git` invocation per memory write, bounded by the same `timeout=5`.
   - **Security rider:** no diagnostic may echo the remote URL or `result.stdout`. In run
     containers origin is cloned from
     `https://${GH_TOKEN}@github.com/${FACTORY_REPO_SLUG}.git` (`entrypoint.sh:13`, cloned
     at `entrypoint.sh:684`), so printing it would write the GitHub token into the phase log
     and into the run artifacts. Only the derived bare name is ever printed — the token
     sits in the URL's authority component and is discarded by the split.

---

## Brainstorming Q&A

**Q1:** The issue specifies the fallback chain for `project`: `FACTORY_REPO` →
`FACTORY_PRODUCT_NAME` → "the git remote". Three sub-decisions are needed: (1) Format —
bare repo name or `owner/repo` slug from the git remote? (2) Scope of the git lookup — walk
up from `--target`'s parent, or rely on cwd? (3) Final fallback if `FACTORY_REPO`/
`FACTORY_PRODUCT_NAME` are unset and the git lookup itself fails (no `.git`, no `origin`,
git not installed) — a hardcoded terminal default, or let it raise?

**A1:** Use the bare repo name (last path segment of the remote URL, `.git` suffix
stripped, no case normalization) to match `FACTORY_REPO`'s convention; `FACTORY_PRODUCT_NAME`
is used as-is since it's a display name. Run `git -C <target.parent> remote get-url origin`
(after the new `mkdir`), not a manual walk-up — git already searches upward from a
directory on its own. Use `subprocess.run(..., capture_output=True, text=True, timeout=5)`,
stdlib only (keeps the script's zero-dependency property). End with a literal `"unknown"`
terminal fallback: catch `OSError`, `subprocess.SubprocessError`, non-zero exit, and empty
output — the provenance lookup must never raise or the write fails for the exact reason
this issue exists. Tests set and clear the environment explicitly with `monkeypatch` for the
two env rungs (the `run()` helper inherits `os.environ`, and every run container sets both
vars). The terminal-fallback test is deterministic by construction — `git init` a fresh
directory under `tmp_path` with **no** `origin` remote, so `git remote get-url origin` exits
non-zero whatever git ancestry the host has — rather than relying on
`GIT_CEILING_DIRECTORIES`, which only stops the upward walk *above* the paths it lists, does
nothing when the start directory is itself inside a worktree, and is a `:`-separated path
list with different semantics on Windows. A second test does `git init` plus `git remote add
origin` in `tmp_path` to exercise the git rung positively. The existing
`test_index_record_has_required_fields` assertion should become env-injected, not a
literal default.

---

## Architecture / Approach

Both fixes land in `scripts/memory_write.py` only; no other file's runtime behavior changes.

**1. Directory creation.** In `main()`, after the empty-`--text` guard
(`scripts/memory_write.py:124-126`) and before the existing `target.is_dir()` check
(`:136`), add:

```python
created = not target.parent.exists()
try:
    target.parent.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    print(f"memory-write: error: cannot create {target.parent}: {exc}", file=sys.stderr)
    sys.exit(1)
if created:
    print(f"memory-write: created {target.parent.resolve()}")
```

This covers both the markdown write (`target.write_text`) and the `index.jsonl` write
(`target.parent / "index.jsonl"`), since both live under the same now-guaranteed-to-exist
directory. `exist_ok=True` makes this a no-op on every subsequent (non-fresh-target) call,
matching the script's existing idempotent style.

**2. Project resolution.** Add a small resolver function, called once per invocation from
`main()` and passed into `_write_index()` in place of the literal:

```python
def _git_origin_name(start_dir):
    """Bare repo name from origin, or None. Never raises; never echoes the URL."""
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
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or None


def _resolve_project(start_dir):
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

`start_dir` is `target.parent` (guaranteed to exist by Requirement 1). It is named
`start_dir`, not `repo_root`, because it is only where git's own upward search starts —
`.archon/memory`, not a repo root. `_git_origin_name()` is split out of `_resolve_project()`
so one lookup serves both the third fallback rung and Requirement 6's mismatch warning, and
so the URL never escapes that helper.
`_write_index()` takes the resolved project string as a new parameter instead of reading
`os.environ` or hardcoding a literal itself, keeping it a pure function of its inputs (same
style as its existing `agent_id`/`scope`/`today_str`/`expires_str` parameters).

No new third-party dependencies, but **both** `import os` and `import subprocess` must be
added to the top-level import block (`scripts/memory_write.py:18-24`, which today imports
only `argparse`, `calendar`, `json`, `re`, `sys`, `datetime.date` and `pathlib.Path`); both
are stdlib, so the zero-dependency property holds. Neither is available today. Adding only
`import os` would raise `NameError: name 'subprocess' is not defined`, and because the
handler is `except (OSError, subprocess.SubprocessError)` the except clause itself
dereferences `subprocess`, so that NameError would not be caught: every memory write would
die with a traceback and exit 1, turning a first-run failure into an every-run failure.

---

## Scope: Knowingly Deferred (Not Spillover)

Named here so the conformance gate reads these as deliberate non-goals, not spillover:

- **The implement phase's memory writes are not fixed by this ticket.** Only the refine
  command invokes `memory_write.py` directly; conformance and code-review reach it through
  `gate_lib.sh::write_memory_entry()`. The implement command writes memory with a shell
  append instead — `echo '- [PATTERN] ...' >> .archon/memory/backend-patterns.md`
  (`commands/dark-factory-implement.md:346-347`, under an explicit "NEVER use the Write or
  Edit tool on a memory file" rule) — so that path still fails on a fresh target where the
  directory does not exist. Out of scope for #444, whose title scopes it to
  `memory_write.py`; handed to #443 Wave 3.
- **The two sibling `markethawk` hardcodes stay.** `scripts/memory_import.py:236`
  (`project="markethawk"` default), which writes to the *same* `.archon/memory/index.jsonl`,
  and `scripts/memory_retrieve.py:563` (`"project": "markethawk"` in the retrieval trace)
  are knowingly left to #443 Wave 3. The consequence to accept, not fix here: the ledger
  becomes mixed — new rows carry the resolved project while the 37 rows already in this
  repo's `.archon/memory/index.jsonl` all say `markethawk`.
- **No backfill or migration of existing `index.jsonl` rows.** They are left exactly as they
  are; this ticket changes only what new rows record.
- **No reader is affected by the value change.** `scan_index()`
  (`scripts/memory_retrieve.py:307-345`) filters on `status`, `expires_at`, `source_file`,
  `path_prefixes` and `agent_id` only — nothing anywhere branches on `project`, and the only
  other occurrences in the tree are the two writers above. The field is provenance, so the
  `"unknown"` rung and any mismatch are recording problems rather than retrieval problems,
  which is exactly why Requirement 6 specifies warnings and not a hard failure.

---

## Alternatives Considered

1. **Import `scripts/factory_core/identity.py` for `REPO`/`PRODUCT_NAME` instead of reading
   `os.environ` directly.** Rejected: `memory_write.py` is de facto a zero-dependency,
   standalone CLI — per its established invocation from bare `python3 memory_write.py` in
   `gate_lib.sh:52` with no `PYTHONPATH` setup. (De facto, not stated: the module docstring
   at `scripts/memory_write.py:2-17` documents usage and exit codes and makes no
   zero-dependency claim.) Importing a
   `factory_core` module would be a new coupling this ticket has no mandate to introduce,
   and would require sys.path surgery inside a script other tools also invoke directly.
2. **Only fall back to `FACTORY_REPO`/`FACTORY_PRODUCT_NAME`, no git-remote step, erroring
   or defaulting to `"unknown"` immediately if both are unset.** Rejected: the issue
   explicitly asks for a third fallback rung ("the git remote"), and in the real pipeline
   `FACTORY_REPO` is essentially always set by `identity.sh` before any phase command runs
   — the git-remote path exists for robustness in ad-hoc/manual/local invocations of the
   script outside the full container bootstrap, where skipping it would silently regress to
   worse behavior than today's already-wrong hardcode.
3. **Take `--repo-root` as an explicit new CLI flag instead of deriving it from
   `target.parent`.** Rejected: no caller today has an independent notion of "repo root"
   to pass in that isn't already implied by the `--target` path's location under
   `.archon/memory/`, and adding a new required/optional flag to every call site
   (`gate_lib.sh`, four phase commands) is more surface than this fix needs — `git`'s own
   upward directory search from `target.parent` gets the same result for free.

---

## Open Questions (Non-blocking)

- `docs/dark-factory-memory-contract.md` §1's schema field table documents `project` as
  "*(implicit — file lives in `omniscient/markethawk` repo; hardcoded by convention)*". That
  line becomes stale once this ships. Updating it is outside refine's scope-boundary
  allowlist (`docs/superpowers/specs/`, `.archon/memory/` only) — flagged here for the
  implement phase to update as part of this same ticket's PR, since it is a one-line,
  same-topic doc fix directly caused by this change, not a separate spillover ticket.

---

## Assumptions (Flagged)

- Every real call site invokes `memory_write.py` with cwd equal to the repo clone root and
  `--target` as a path under `.archon/memory/` within that same clone (confirmed by reading
  `gate_lib.sh::write_memory_entry()` and the phase-command call sites) — so
  `target.parent`'s nearest `.git` ancestor (found via git's own upward search) is always
  the correct repo for the git-remote fallback rung. No case was found where `--target`
  points outside the invoking repo's working tree.
- `"unknown"` as the terminal fallback value is treated as an acceptable, non-blocking
  provenance value (mirroring the script's existing "never fail the write over provenance"
  philosophy for `index.jsonl`, e.g. the existing `_write_index` I/O-error handling that
  warns to stderr but does not set exit 1) — not a value any downstream consumer of
  `index.jsonl` currently branches on specially. Requirement 6 makes it visible rather than
  silent: the write still succeeds, but the run says why the project was unresolved.

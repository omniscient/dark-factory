# memory_write.py: create .archon/memory on first run and stop hardcoding the project name

**Issue:** #444

Split out of #443 (finding 1); the rest of #443's findings stay for Wave 3.

---

## Overview / Problem Statement

`scripts/memory_write.py` is the standalone, zero-dependency CLI that every gate phase
(`gate_lib.sh::write_memory_entry()`, and the refine/implement/conformance/code-review
phase commands directly) shells out to for writing `.archon/memory/*.md` entries and
their `index.jsonl` provenance rows. Two independent defects, both confirmed by reading
`scripts/memory_write.py` directly:

1. **Missing directory creation (`scripts/memory_write.py:139-143` / `:210-215`).** Neither
   the markdown write nor the `index.jsonl` write ever calls `mkdir`. On a fresh target,
   nothing else in the pipeline creates `.archon/memory/` ahead of the first write, so
   `target.write_text(...)` raises `FileNotFoundError` (a subclass of `OSError`), which is
   caught and turned into `memory-write: error writing ...`, exit 1. Every call site treats
   this script as best-effort, so the observable symptom is exactly the issue's report:
   memory writes silently never happen on a fresh target. Reproduced on the jobfinder
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
   exist_ok=True)`, called once in `main()` before the existing `target.is_dir()` /
   `target.exists()` checks run.
2. `_write_index()`'s `"project"` field is resolved dynamically instead of the `"markethawk"`
   literal, via a three-step fallback chain:
   - `FACTORY_REPO` env var, if set and non-empty (bare repo name, e.g. `markethawk`,
     `dark-factory`, `jobfinder` — matches the existing convention in
     `scripts/identity.sh` / `scripts/factory_core/identity.py`).
   - else `FACTORY_PRODUCT_NAME` env var, if set and non-empty, used as-is (display name,
     e.g. `MarketHawk`) — no case or format normalization.
   - else the git remote: run `git -C <target.parent> remote get-url origin` (after the
     `mkdir` from Requirement 1, so the directory is guaranteed to exist) and take the last
     path segment of the URL with any trailing `.git` stripped (e.g.
     `git@github.com:omniscient/jobfinder.git` → `jobfinder`).
   - else, if the git lookup itself fails for any reason (no `.git`, no `origin` remote,
     git not installed, non-zero exit, empty stdout, timeout), fall back to the literal
     `"unknown"`. The lookup must never raise — a provenance-field lookup failing the write
     would reproduce the exact class of bug this issue exists to fix.
3. No change to the memory record schema (`docs/dark-factory-memory-contract.md`) — this is
   a value-resolution fix, not a field-shape change. `project` remains a single string field
   in the `index.jsonl` record.
4. A unit test for each defect (directory auto-creation; each rung of the fallback chain,
   including the terminal `"unknown"` case) added to `tests/test_memory_write.py`, in the
   existing pure-filesystem, no-mocking, subprocess-invocation style.
5. The existing `TestIndexJsonl::test_index_record_has_required_fields` assertion
   (`record["project"] == "markethawk"`) is updated to assert against an explicitly
   env-injected value rather than a literal that happened to match the old hardcode.

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
this issue exists. Tests should set `FACTORY_REPO` explicitly via env for the main-path
cases; the terminal-fallback test should isolate from any real git ancestry via
`GIT_CEILING_DIRECTORIES`; an optional test may `git init` + `git remote add origin` in
`tmp_path` to exercise the git-fallback path directly. The existing
`test_index_record_has_required_fields` assertion should become env-injected, not a
literal default.

---

## Architecture / Approach

Both fixes land in `scripts/memory_write.py` only; no other file's runtime behavior changes.

**1. Directory creation.** In `main()`, immediately after computing `target = Path(args.target)`
and before the existing `target.is_dir()` check, add:

```python
target.parent.mkdir(parents=True, exist_ok=True)
```

This covers both the markdown write (`target.write_text`) and the `index.jsonl` write
(`target.parent / "index.jsonl"`), since both live under the same now-guaranteed-to-exist
directory. `exist_ok=True` makes this a no-op on every subsequent (non-fresh-target) call,
matching the script's existing idempotent style.

**2. Project resolution.** Add a small resolver function, called once per invocation from
`main()` and passed into `_write_index()` in place of the literal:

```python
def _resolve_project(repo_root):
    repo = os.environ.get("FACTORY_REPO", "").strip()
    if repo:
        return repo
    product = os.environ.get("FACTORY_PRODUCT_NAME", "").strip()
    if product:
        return product
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    url = result.stdout.strip()
    if not url:
        return "unknown"
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "unknown"
```

`repo_root` is `target.parent` (already resolved and guaranteed to exist by Requirement 1).
`_write_index()` takes the resolved project string as a new parameter instead of reading
`os.environ` or hardcoding a literal itself, keeping it a pure function of its inputs (same
style as its existing `agent_id`/`scope`/`today_str`/`expires_str` parameters).

No new third-party dependencies; `subprocess` and `os` are already implicitly available
(`os` needs a new top-level import — not currently imported in this file).

---

## Alternatives Considered

1. **Import `scripts/factory_core/identity.py` for `REPO`/`PRODUCT_NAME` instead of reading
   `os.environ` directly.** Rejected: `memory_write.py` is deliberately a zero-dependency,
   standalone CLI (per its own module docstring and its established invocation from bare
   `python3 memory_write.py` in `gate_lib.sh` with no `PYTHONPATH` setup) — importing a
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
  `index.jsonl` currently branches on specially.

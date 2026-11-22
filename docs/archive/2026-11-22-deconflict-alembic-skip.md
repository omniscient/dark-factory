# Deconflict: skip the alembic multi-head check on backend-less targets, escalate on real failures — Implementation Plan

**Issue:** #576

**Revised:** 2026-11-22 (operator amendment after plan gate review)

> **For agentic workers:** execute task-by-task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `resolve_merge_conflicts()` must stop crashing with `FileNotFoundError` on targets with no `backend/` (dark-factory's self-target). When the adapter-configured alembic.ini (`deconflict.alembic_ini`, default `backend/alembic.ini`) is not a file, the alembic multi-head check is skipped with a log line. When it is a file, the check runs in the ini's directory, stages the generated merge revision where alembic actually wrote it, and routes any failure through `_escalate()` instead of crashing.

**Architecture:** One new adapter default key (`DEFAULTS["deconflict"]["alembic_ini"]`) plus a `_deconflict_alembic_ini(clone_dir)` reader, the same shape as `_deconflict_models_init` / `_deconflict_migrations_dir`. The unconditional block at `scripts/factory_core/deconflict.py:280-295` moves into a module-level helper `_alembic_multihead_check(issue_num, clone_dir, conflicted) -> str | None`, which returns an escalation reason or `None`. A second small helper, `_untracked_py(clone_dir, rel_dir)`, lists untracked `*.py` files. The call site in `resolve_merge_conflicts()` aborts the merge, calls `_escalate()`, writes the `escalate` artifact and returns 1 when the helper returns a reason. That is the same sequence as the function's two existing escalation sites. The spec allows this restructuring of its sketch ("the plan may restructure it"); the behaviours of spec requirements 1, 4 and 5 are unchanged.

**Tech Stack:** Python 3 (`subprocess`, `os.path`), pytest with `monkeypatch` / `capsys`. CI runs `python -m pytest tests/ -v` with `PYTHONPATH=scripts` and `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`.

**Spec:** `docs/superpowers/specs/2026-11-22-deconflict-alembic-skip-design.md`

**Not in scope (spec requirement 7 / CLAUDE.md hard limits):** no edits to `config/config.yaml` (`conflict_resolution:`), `scripts/factory_core/board.py`, breaker or dispatch-ceiling logic, `entrypoint.sh`, `.factory/adapter.yaml` (spec requirement 2), or the seed copy under `dark-factory/scripts/`.

**Known stale after this change, deliberately untouched:** the `deconflict.py:143` comment citations in `config/config.yaml:158` and `scripts/factory_core/side_effect.py:188` (the `claude -p` call moves to `:156`). Both files are gate-adjacent; do not edit them here. Follow-up ticket.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/adapter_defaults.py` | Modify (1 line in `DEFAULTS["deconflict"]`) | New default `alembic_ini: "backend/alembic.ini"` |
| `scripts/factory_core/deconflict.py` | Modify | `_DEFAULT_ALEMBIC_INI`, `_deconflict_alembic_ini()`, `_untracked_py()`, `_alembic_multihead_check()`; replace the unconditional block in `resolve_merge_conflicts()` with a guarded call plus escalation |
| `tests/test_adapter.py` | Modify (append 2 tests after `test_deconflict_migrations_dir_adapter_override`) | Default parity and adapter override for `deconflict.alembic_ini` |
| `tests/test_factory_core_deconflict.py` | Modify (append a fixture helper block and 8 tests, 9 cases) | Spec requirement 6 (a)–(f) |
| `docs/configuration.md` | Modify (adapter-keys row, line 554; citation on line 527) | Add `deconflict.alembic_ini`; refresh the `deconflict.py` line range (spec §5); re-map the `adapter_defaults.py` citation shifted by the new default line |
| `README.md` | Modify (adapter table row, line 230) | Name the new `alembic_ini` key next to `models_init` / `migrations_dir` |
| `docs/run-lifecycle.md` | Modify (deconflict section, lines 186-216, 1148) | Describe the new skip/escalate behaviour in place of "run `alembic merge heads` (`:280-295`)"; re-map the `deconflict.py` citations this change shifts |
| `docs/features.md` | Modify (line 94) | Re-map the shifted `tier1` / `tier2` line citations |

The three extra doc files are the only plan additions beyond the spec's own file list. They document the behaviour this ticket changes: `run-lifecycle.md:194-195` currently states the unconditional `alembic merge heads` as fact. They also carry `deconflict.py` line citations that this edit shifts by 13 to 58 lines. `docs_refs_check.py` checks only that a cited line is in range, so it would not catch the stale citations.

---

## Task 1: `deconflict.alembic_ini` adapter key and reader

**Files:**
- Modify: `scripts/factory_core/adapter_defaults.py:110-113`
- Modify: `scripts/factory_core/deconflict.py:17-43`
- Test: `tests/test_adapter.py` (after line 1068, `test_deconflict_migrations_dir_adapter_override`)

- [ ] **Step 1: Write the failing tests.** In `tests/test_adapter.py`, directly after the body of `test_deconflict_migrations_dir_adapter_override` (ends `assert dc._deconflict_migrations_dir(str(tmp_path)) == "db/migrations/"`) and before the `# ── Skill-security safety globs (#46)` banner, insert:

```python


def test_deconflict_alembic_ini_default_parity(tmp_path):
    """Without adapter file, _deconflict_alembic_ini returns DEFAULTS deconflict.alembic_ini."""
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_alembic_ini(str(tmp_path)) == adapter_defaults.DEFAULTS["deconflict"]["alembic_ini"]


def test_deconflict_alembic_ini_adapter_override(tmp_path):
    """With adapter override, returns overridden alembic_ini path."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "deconflict:\n  alembic_ini: 'services/api/alembic.ini'\n")
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_alembic_ini(str(tmp_path)) == "services/api/alembic.ini"
```

(The two blank lines before the first `def` and the two after the second keep PEP 8 spacing against the neighbouring test and the banner.)

- [ ] **Step 2: Run them and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py -q -k alembic_ini
```
Expected: `2 failed`. The errors are `AttributeError: module 'factory_core.deconflict' has no attribute '_deconflict_alembic_ini'`, or `KeyError: 'alembic_ini'` for the parity test.

- [ ] **Step 3: Add the default.** In `scripts/factory_core/adapter_defaults.py`, change the `"deconflict"` mapping to:

```python
    "deconflict": {
        "models_init": "backend/app/models/__init__.py",
        "migrations_dir": "alembic/versions/",
        "alembic_ini": "backend/alembic.ini",
    },
```

- [ ] **Step 4: Add the reader.** In `scripts/factory_core/deconflict.py`, add the constant below `_DEFAULT_MIGRATIONS_DIR` (line 18):

```python
_DEFAULT_ALEMBIC_INI = _AD["deconflict"]["alembic_ini"]
```

and, directly after `_deconflict_migrations_dir()` (which ends `return _DEFAULT_MIGRATIONS_DIR`) and before `def tier1(`, add:

```python


def _deconflict_alembic_ini(clone_dir: str | None = None) -> str:
    """Return deconflict.alembic_ini from adapter at use-time, falling back to default."""
    try:
        from . import adapter
        val = adapter.get(clone_dir or ".", "deconflict.alembic_ini")
        if val is not None and isinstance(val, str):
            return val
    except Exception:
        pass
    return _DEFAULT_ALEMBIC_INI
```

(Two blank lines on both sides, as between the existing helpers. Afterwards `grep -n "^def tier1" scripts/factory_core/deconflict.py` prints `58:def tier1(...`.)

- [ ] **Step 5: Run the adapter tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py -q
```
Expected: `162 passed`. That count includes `test_dotted_get`, and the defaults-equality test at the top of the file, which deep-copies `DEFAULTS` and so picks up the new key automatically.

- [ ] **Step 6: Commit.**

```bash
git add scripts/factory_core/adapter_defaults.py scripts/factory_core/deconflict.py tests/test_adapter.py
git commit -m "feat(#576): add deconflict.alembic_ini adapter key and reader"
```

---

## Task 2: Guard, relocate and wrap the alembic multi-head check

**Files:**
- Modify: `scripts/factory_core/deconflict.py` (new helpers above `def resolve_merge_conflicts(`; replace the block that starts `# Alembic multi-head check after tier1`)
- Test: `tests/test_factory_core_deconflict.py` (append at end of file, after `test_resolve_escalation_on_no_conflicted_files`)

Coverage map to spec requirement 6:

| Spec | Test |
|---|---|
| (a) no `backend/` | `test_resolve_skips_alembic_check_when_no_backend` |
| (a) `backend/` without alembic.ini | `test_resolve_skips_alembic_check_when_backend_has_no_ini` |
| (b) adapter override | `test_resolve_alembic_check_respects_adapter_override` |
| (c) check raises / exits non-zero | `test_resolve_escalates_on_alembic_check_failure[exc0/exc1]` (`OSError`, `CalledProcessError`) |
| (d) single head | `test_resolve_alembic_single_head_does_not_merge` |
| (e) multi-head stages generated file | `test_resolve_alembic_multi_head_merges_and_stages_generated_revision` |
| (e) multi-head, no new file | `test_resolve_escalates_when_alembic_merge_generates_no_file` |
| (f) migration conflict, no ini | `test_resolve_escalates_on_migration_conflict_without_alembic_ini` |

Fixture notes, baked into the helper below:
- The fake `subprocess.run` raises `FileNotFoundError` when given a `cwd` that does not exist, as the real one does. Without the fix, the no-backend tests therefore fail with the exact #576 traceback (`No such file or directory: '<tmp>/backend'`).
- Every case conflicts on `codeindex.json`, except (f), which conflicts on a migration. Tier 1 resolves `codeindex.json` by `git checkout --theirs` (the fake returns 0), so no Tier 2 / `claude` call happens. `hard_grep_survivors(tmp_path)` finds nothing, and a passing run ends in verdict `tier1`.
- `_escalate` is replaced with a recorder via `monkeypatch.setattr(dc, "_escalate", ...)`, so no board or `gh` call is made. The spec's "assert `_escalate` is called" is checked on the recorded reason (`escalations[0][3]`).
- A fake does not honour `check=True`, so the `CalledProcessError` case raises it from the fake itself (spec §4).
- MarketHawk's real layout (`script_location = app/alembic`) is used for the generated revision: `backend/app/alembic/versions/<rev>.py`. The stray file shows that the before/after listing keeps any pre-existing untracked file out of the commit.

- [ ] **Step 1: Write the failing tests.** Append to the end of `tests/test_factory_core_deconflict.py` (after `test_resolve_escalation_on_no_conflicted_files`, which ends with `assert "ESCALATED=1" in artifact`). `os`, `subprocess` and `pytest` are already imported at the top of the file.

```python


# ---------------------------------------------------------------------------
# resolve_merge_conflicts — alembic multi-head check (#576)
# ---------------------------------------------------------------------------

_HEADS = ["python", "-m", "alembic", "heads"]
_MERGE_HEADS = ["python", "-m", "alembic", "merge", "heads"]


def _cp(rc=0, stdout=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _fake_conflicted_run(calls, conflicted=("codeindex.json",), heads_stdout="",
                         untracked=(), heads_exc=None):
    """subprocess.run fake: merge origin/main conflicts on `conflicted`; Tier 1
    resolves them; `alembic heads` prints heads_stdout (or raises heads_exc);
    each `git ls-files --others` call returns the next entry of `untracked`."""
    listings = list(untracked)

    def fake_run(cmd, **kw):
        cmd = list(cmd)
        cwd = kw.get("cwd")
        calls.append((cmd, cwd))
        if cwd is not None and not os.path.isdir(cwd):
            # real subprocess.run raises before exec on a missing cwd (the #576 crash)
            raise FileNotFoundError(2, "No such file or directory", cwd)
        if cmd[:2] == ["git", "merge"] and "origin/main" in cmd:
            return _cp(1, "CONFLICT")
        if cmd[:2] == ["git", "diff"] and "--diff-filter=U" in cmd:
            return _cp(0, "\n".join(conflicted) + "\n")
        if cmd[:4] == _HEADS:
            if heads_exc is not None:
                raise heads_exc
            return _cp(0, heads_stdout)
        if cmd[:3] == ["git", "ls-files", "--others"]:
            return _cp(0, listings.pop(0) if listings else "")
        return _cp(0)

    return fake_run


def _run_resolve(tmp_path, monkeypatch, fake_run):
    """Run resolve_merge_conflicts against tmp_path; return (rc, artifact, escalations)."""
    from factory_core import deconflict as dc
    escalations = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dc, "_escalate", lambda *a: escalations.append(a))
    artifact_dir = tmp_path / "artifacts"
    rc = dc.resolve_merge_conflicts(42, str(tmp_path), "omniscient", "dark-factory",
                                    str(artifact_dir))
    artifact = (artifact_dir / "conflict_resolution.md").read_text()
    return rc, artifact, escalations


def _alembic_calls(calls):
    return [(c, cwd) for c, cwd in calls if "alembic" in c]


def _write_ini(tmp_path, rel="backend/alembic.ini"):
    ini = tmp_path / rel
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text("[alembic]\nscript_location = app/alembic\n")
    return ini


def test_resolve_skips_alembic_check_when_no_backend(tmp_path, monkeypatch, capsys):
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls))
    assert rc == 0
    assert _alembic_calls(calls) == []
    assert escalations == []
    assert "CONFLICT_VERDICT=tier1" in artifact
    assert any(c[:2] == ["git", "commit"] for c, _ in calls)
    assert "[deconflict] alembic check skipped: backend/alembic.ini not found" \
        in capsys.readouterr().out


def test_resolve_skips_alembic_check_when_backend_has_no_ini(tmp_path, monkeypatch, capsys):
    (tmp_path / "backend").mkdir()
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls))
    assert rc == 0
    assert _alembic_calls(calls) == []
    assert escalations == []
    assert "CONFLICT_VERDICT=tier1" in artifact
    assert "alembic check skipped: backend/alembic.ini not found" in capsys.readouterr().out


def test_resolve_alembic_check_respects_adapter_override(tmp_path, monkeypatch):
    _write_ini(tmp_path, "services/api/alembic.ini")
    (tmp_path / ".factory").mkdir()
    (tmp_path / ".factory" / "adapter.yaml").write_text(
        "deconflict:\n  alembic_ini: 'services/api/alembic.ini'\n")
    calls = []
    rc, _, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="abc123 (head)\n"))
    assert rc == 0
    assert escalations == []
    assert _alembic_calls(calls) == [(_HEADS, str(tmp_path / "services" / "api"))]


@pytest.mark.parametrize("exc", [
    OSError("No module named alembic"),
    subprocess.CalledProcessError(1, _HEADS),
])
def test_resolve_escalates_on_alembic_check_failure(tmp_path, monkeypatch, exc):
    _write_ini(tmp_path)
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls, heads_exc=exc))
    assert rc == 1
    assert len(escalations) == 1
    assert "Alembic multi-head check failed unexpectedly" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert not any(c[:2] == ["git", "commit"] for c, _ in calls)
    assert "CONFLICT_VERDICT=escalate" in artifact
    assert "ESCALATED=1" in artifact


def test_resolve_alembic_single_head_does_not_merge(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    calls = []
    rc, _, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="abc123 (head)\n"))
    assert rc == 0
    assert escalations == []
    assert _alembic_calls(calls) == [(_HEADS, str(tmp_path / "backend"))]
    assert not any(c[:3] == ["git", "add", "--"] for c, _ in calls)


def test_resolve_alembic_multi_head_merges_and_stages_generated_revision(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    stray = "backend/app/alembic/versions/stray_local.py"
    rev = "backend/app/alembic/versions/ccc333_merge_branches_issue_42.py"
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="aaa111 (head)\nbbb222 (head)\n",
                             untracked=[stray + "\n", f"{stray}\n{rev}\n"]))
    assert rc == 0
    assert escalations == []
    backend = str(tmp_path / "backend")
    merge = [(c, cwd) for c, cwd in calls if c[:5] == _MERGE_HEADS]
    assert merge == [(_MERGE_HEADS + ["-m", "merge_branches_issue_42"], backend)]
    listings = [c for c, _ in calls if c[:3] == ["git", "ls-files", "--others"]]
    assert listings and all(c[-2:] == ["--", "backend"] for c in listings)
    assert (["git", "add", "--", rev], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=tier1" in artifact


def test_resolve_escalates_when_alembic_merge_generates_no_file(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="aaa111 (head)\nbbb222 (head)\n",
                             untracked=["", ""]))
    assert rc == 1
    assert len(escalations) == 1
    assert "produced no new revision file" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=escalate" in artifact


def test_resolve_escalates_on_migration_conflict_without_alembic_ini(tmp_path, monkeypatch):
    migration = "backend/app/alembic/versions/abc123_add_col.py"
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls, conflicted=(migration,)))
    assert rc == 1
    assert _alembic_calls(calls) == []
    assert len(escalations) == 1
    assert migration in escalations[0][3]
    assert "deconflict.alembic_ini" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=escalate" in artifact
```

- [ ] **Step 2: Run them and confirm the expected failures.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_deconflict.py -q -k "test_resolve and (alembic or migration)"
```
Expected: `8 failed, 1 passed`. `test_resolve_alembic_single_head_does_not_merge` passes against the old code: with `backend/` present and one head, the old block does nothing either, so that test pins existing behaviour. The two skip tests and the override test fail with `FileNotFoundError: [Errno 2] No such file or directory: '.../backend'`, the #576 crash. The escalation tests fail on `rc == 1` / `len(escalations) == 1` (old code swallows the failure or crashes). The multi-head staging test fails because the old code runs `git add backend/alembic/versions/`, not the generated file.

- [ ] **Step 3: Add the two helpers.** In `scripts/factory_core/deconflict.py`, directly above `def resolve_merge_conflicts(` and below `_escalate()`, insert:

```python
def _untracked_py(clone_dir: str, rel_dir: str) -> set:
    """Untracked, non-ignored *.py paths (clone-relative) under rel_dir."""
    out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", rel_dir],
        cwd=clone_dir, capture_output=True, text=True, check=True,
    ).stdout
    return {p for p in out.splitlines() if p.endswith(".py")}


def _alembic_multihead_check(issue_num: int, clone_dir: str, conflicted: list) -> str | None:
    """Merge alembic heads after Tier 1. Returns an escalation reason, or None to continue.

    No alembic.ini at deconflict.alembic_ini: skip (logged), unless a conflicted file
    is a migration, which means the configured path is wrong.
    """
    ini_rel = _deconflict_alembic_ini(clone_dir)
    ini_path = os.path.join(clone_dir, ini_rel)
    if not os.path.isfile(ini_path):
        print(f"[deconflict] alembic check skipped: {ini_rel} not found", flush=True)
        migrations_dir = _deconflict_migrations_dir(clone_dir)
        migration_conflicts = [f for f in conflicted
                               if migrations_dir in f and f.endswith(".py")]
        if migration_conflicts:
            return (f"Conflicted migration files ({', '.join(migration_conflicts)}) but no "
                    f"alembic.ini at {ini_rel}; check deconflict.alembic_ini.")
        return None

    backend_dir = os.path.dirname(ini_path)
    backend_rel = os.path.relpath(backend_dir, clone_dir)
    try:
        r3 = subprocess.run(
            ["python", "-m", "alembic", "heads"],
            cwd=backend_dir, capture_output=True, text=True, check=True,
        )
        heads = [ln for ln in r3.stdout.splitlines()
                 if ln and ln[:1] in "0123456789abcdef"]
        if len(heads) > 1:
            before = _untracked_py(clone_dir, backend_rel)
            subprocess.run(
                ["python", "-m", "alembic", "merge", "heads",
                 "-m", f"merge_branches_issue_{issue_num}"],
                cwd=backend_dir, capture_output=True, check=True,
            )
            generated = sorted(_untracked_py(clone_dir, backend_rel) - before)
            if not generated:
                raise RuntimeError("alembic merge heads produced no new revision file")
            subprocess.run(["git", "add", "--", *generated],
                           cwd=clone_dir, capture_output=True, check=True)
    except Exception as exc:
        return f"Alembic multi-head check failed unexpectedly: {exc}"
    return None


```

Design points (spec requirements 1, 4, 5):
- The skip is decided by `os.path.isfile`, never by catching the missing-`cwd` `FileNotFoundError` (spec, alternative 1, rejected).
- The skip always prints the log line, including the migration-conflict escalation case.
- The migration-conflict test uses the same predicate as `tier1` (`deconflict.py:94`): `_deconflict_migrations_dir(clone_dir) in filepath and filepath.endswith(".py")`.
- The staging path comes from the ini's directory listing, never from `deconflict.migrations_dir`. That key is a substring pattern, not a path.
- All three subprocess calls in the `try` that can fail use `check=True` (`alembic heads`, `alembic merge heads`, `git add`), and so do the two `git ls-files` listings inside `_untracked_py`. Unlike the best-effort `git add` calls elsewhere in this file, the revision `git add` is checked.
- `except Exception` catches `OSError` / `FileNotFoundError` (alembic or python not runnable), `CalledProcessError` (non-zero exit) and the `RuntimeError` for "no new file".

- [ ] **Step 4: Replace the unconditional block.** In `resolve_merge_conflicts()`, replace everything from `    # Alembic multi-head check after tier1` through `                       cwd=clone_dir, capture_output=True)` (the old `git add backend/alembic/versions/` call, the last line before the blank line and `    tier2_count = 0`) with:

```python
    # Alembic multi-head check after tier1
    alembic_failure = _alembic_multihead_check(issue_num, clone_dir, conflicted)
    if alembic_failure:
        subprocess.run(["git", "merge", "--abort"], cwd=clone_dir, capture_output=True)
        _escalate(issue_num, owner, repo, alembic_failure)
        _write_artifact(artifact_path, "escalate", len(conflicted),
                        tier1_count, 0, 1)
        return 1
```

(`tier2_count` is not assigned yet at this point and Tier 2 has not run, so the artifact records `TIER2_RESOLVED=0` as a literal.) After this step there must be no remaining reference to the literal `"backend"` directory in the file:

```bash
grep -n '"backend"\|backend/alembic/versions' scripts/factory_core/deconflict.py
```
Expected: no output, exit status 1.

- [ ] **Step 5: Run the deconflict tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_deconflict.py tests/test_adapter.py -q
```
Expected: `188 passed` (26 in `test_factory_core_deconflict.py`, 162 in `test_adapter.py`). This includes the unchanged `test_resolve_clean_merge_writes_artifact` and `test_resolve_escalation_on_no_conflicted_files`, which return before the alembic block.

- [ ] **Step 6: Confirm the new line layout** that Task 3's doc citations depend on:

```bash
grep -n '^def \|# Alembic multi-head check\|^    return 0' scripts/factory_core/deconflict.py
```
Expected (exact):
```
22:def _deconflict_models_init(clone_dir: str | None = None) -> str:
34:def _deconflict_migrations_dir(clone_dir: str | None = None) -> str:
46:def _deconflict_alembic_ini(clone_dir: str | None = None) -> str:
58:def tier1(filepath: str, clone_dir: str) -> bool:
118:def tier2(filepath: str, issue_num: int, owner: str, repo: str, clone_dir: str) -> bool:
191:def hard_grep_survivors(root: str) -> list:
211:def _write_artifact(path: Path, verdict: str, files: int,
223:def _escalate(issue_num: int, owner: str, repo: str, reason: str) -> None:
246:def _untracked_py(clone_dir: str, rel_dir: str) -> set:
255:def _alembic_multihead_check(issue_num: int, clone_dir: str, conflicted: list) -> str | None:
299:def resolve_merge_conflicts(
346:    # Alembic multi-head check after tier1
395:    return 0
```
If any number differs, use the actual numbers in Task 3. The ranges there are derived from these anchors. Each range runs from a `def` line to the line before the next blank-line gap.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/deconflict.py tests/test_factory_core_deconflict.py
git commit -m "fix(#576): skip alembic multi-head check when alembic.ini is absent, escalate on failure, stage the generated revision"
```

---

## Task 3: Docs — adapter key and re-mapped deconflict citations

**Files:**
- Modify: `docs/configuration.md:527,554`
- Modify: `README.md:230`
- Modify: `docs/run-lifecycle.md:186,188,194-195,196,200,205,216,1148`
- Modify: `docs/features.md:94`

Each step is an exact single-occurrence replacement.

- [ ] **Step 1: `docs/configuration.md` adapter-keys row** (spec §5). Replace

```
| `deconflict.models_init`, `deconflict.migrations_dir` | Tier-1 conflict resolution file paths | `scripts/factory_core/deconflict.py:21-45` |
```
with
```
| `deconflict.models_init`, `deconflict.migrations_dir`, `deconflict.alembic_ini` | Tier-1 conflict resolution file paths; the alembic.ini whose directory the post-Tier-1 multi-head check runs in (not a file: check skipped, unless a migration file is conflicted, which escalates) | `scripts/factory_core/deconflict.py:22-55` |
```

Also in `docs/configuration.md` (line 527), replace `` `adapter_defaults.py:122-146` `` with `` `adapter_defaults.py:123-147` `` (one occurrence): the new `alembic_ini` default line shifts the factory-owned floor lists down by one. Confirm:

```bash
grep -n '^FACTORY_OWNED_CRITICAL_DIFF_FLOOR' scripts/factory_core/adapter_defaults.py
```
Expected: `123:FACTORY_OWNED_CRITICAL_DIFF_FLOOR = [`. If the number differs, use the actual start line and the line of the closing `]` of `FACTORY_OWNED_MIGRATION_SEED_FLOOR`.

- [ ] **Step 2: `README.md` adapter table row.** Replace

```
| `deconflict` | map | Paths for models index (`models_init`) and migrations dir (`migrations_dir`) used by the deconflict guard. |
```
with
```
| `deconflict` | map | Paths for models index (`models_init`), migrations dir (`migrations_dir`) and alembic config (`alembic_ini`; the multi-head check is skipped when it is not a file) used by the deconflict guard. |
```

- [ ] **Step 3: `docs/features.md:94` citations.** Replace `` `scripts/factory_core/deconflict.py` `tier1` (`:45`), `tier2` (`:105`) `` with `` `scripts/factory_core/deconflict.py` `tier1` (`:58`), `tier2` (`:118`) ``.

- [ ] **Step 4: `docs/run-lifecycle.md` deconflict section.** Apply these replacements:

| Old | New |
|---|---|
| `` `scripts/factory_core/deconflict.py:233-337`): `` | `` `scripts/factory_core/deconflict.py:299-395`): `` |
| `` per conflicted file (`scripts/factory_core/deconflict.py:45-102`): take `` | `` per conflicted file (`scripts/factory_core/deconflict.py:58-115`): take `` |
| `` for files Tier 1 did not handle (`:105-175`, `:297-306`) `` | `` for files Tier 1 did not handle (`:118-188`, `:355-364`) `` |
| `` source-like file (`:178-195`) `` | `` source-like file (`:191-208`) `` |
| `` `TIER2_RESOLVED`, `ESCALATED` (`:198-207`). `` | `` `TIER2_RESOLVED`, `ESCALATED` (`:211-220`). `` |
| `` `scripts/factory_core/deconflict.py:210-230` for resolver failures `` | `` `scripts/factory_core/deconflict.py:223-243` for resolver failures `` |
| `` (`scripts/factory_core/deconflict.py:311-320`), but the only node `` | `` (`scripts/factory_core/deconflict.py:368-378`), but the only node `` |

and replace the two-line bullet

```
   - If more than one alembic head exists afterwards, run `alembic merge heads`
     (`:280-295`).
```
with
```
   - **Alembic multi-head check** (`:246-296`, called at `:346-353`): skipped, with an
     `alembic check skipped` log line, when `deconflict.alembic_ini` (default
     `backend/alembic.ini`) is not a file; but a conflicted migration file with no
     alembic.ini escalates. Otherwise, in the ini's directory, if more than one alembic
     head exists run `alembic merge heads` and stage the new revision file. Any failure
     in the check: `git merge --abort`, escalate, return 1.
```

- [ ] **Step 5: Run the CI docs check.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 6: Confirm no stale citation is left.**

```bash
grep -n 'deconflict.py:21-45\|deconflict.py:233-337\|deconflict.py:45-102\|(`:280-295`)\|`tier1` (`:45`)\|adapter_defaults.py:122-146' docs/*.md README.md
```
Expected: no output.

- [ ] **Step 7: Commit.**

```bash
git add docs/configuration.md README.md docs/run-lifecycle.md docs/features.md
git commit -m "docs(#576): document deconflict.alembic_ini and re-map shifted deconflict.py citations"
```

---

## Task 4: Full verification

**Files:** none modified.

- [ ] **Step 1: Full test suite (CI parity).**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```
Expected: all pass, no failures. 3084 passed at plan time: the baseline plus 11 new cases, 2 adapter + 9 deconflict.

- [ ] **Step 2: Scope check.** The diff against main must touch only the plan-listed files (plus the spec/plan docs already on the branch):

```bash
git diff --name-only origin/main HEAD
```
Expected (any order):
```
README.md
docs/configuration.md
docs/features.md
docs/run-lifecycle.md
docs/superpowers/plans/2026-11-22-deconflict-alembic-skip.md
docs/superpowers/specs/2026-11-22-deconflict-alembic-skip-design.md
scripts/factory_core/adapter_defaults.py
scripts/factory_core/deconflict.py
tests/test_adapter.py
tests/test_factory_core_deconflict.py
```
In particular `config/config.yaml`, `scripts/factory_core/board.py`, `entrypoint.sh` and `.factory/adapter.yaml` must not appear (spec requirements 2 and 7).

- [ ] **Step 3: Self-target sanity.** The default path does not exist in this repo, so the skip fires here with zero configuration:

```bash
PYTHONPATH=scripts python -c "from factory_core import deconflict as dc; import os; p = dc._deconflict_alembic_ini('.'); print(p, os.path.isfile(p))"
```
Expected: `backend/alembic.ini False`.

---

## Self-review

- `**Issue:** #576` line present under the title.
- Every spec requirement is mapped. R1: skip with log line, plus the migration-conflict escalation (Task 2 helper, tests a/f). R2: no `.factory/adapter.yaml` change (Task 4 Step 2). R3: single path key, no boolean (Task 1). R4: cwd from the ini's directory, before/after untracked listing, checked `git add`, zero-new-file escalation (Task 2, test e). R5: `try/except Exception` leading to abort, `_escalate`, `escalate` artifact and `return 1` (Task 2, test c). R6: tests (a)–(f) plus the two adapter tests (Tasks 1–2). R7: no gate/breaker/config change (Task 4 Step 2).
- No placeholders. The code and test blocks were dry-run against a scratch copy of this branch: 8 failed / 1 passed before the change, `188 passed` for the two files after it, full suite 3084 passed, and `docs_refs_check.py` passed.

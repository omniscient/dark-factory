# docs-refs: revert the advisory hook to `--base HEAD`, fix the blank-citation message, add the missing tests — Implementation Plan

**Issue:** #669

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put `commands/dark-factory-revise-advisory.md`'s citation re-derive back on an
unconditional `--rederive --base HEAD`, make `check()` tell the author to pick a non-blank
anchor (instead of naming a repair command that cannot help) when an unlocked citation
points at a blank range, and add the unit tests PR #667's advisory commit (`15b786e`) never
got.

**Architecture:** A byte-identical revert of one shell block in a command file, a
message-only branch inside `_content_errors()` that reuses the existing
`_Targets.digest()`/`_Targets.blank()` helpers, and flat `def test_*` additions to the
existing `tests/test_docs_refs_check.py`. No new module, no lock-format change, no CLI
change.

**Tech Stack:** Python 3 (`scripts/docs_refs_check.py`), pytest, git (for the
`_git_tree` fixtures), Markdown/Bash (the command file).

**Spec:** `docs/superpowers/specs/2026-11-28-docs-refs-check-revert-advisory-hook-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `commands/dark-factory-revise-advisory.md` | Modify (Phase 4 step 2 block only, -5 lines) | Advisory-fix hook: re-derive citations against `HEAD` |
| `scripts/docs_refs_check.py` | Modify (`_content_errors()`, `entry is None` branch only) | Blank-range message for an unlocked citation |
| `tests/test_docs_refs_check.py` | Modify (append tests + 3 small helpers/constants) | Coverage for R3 and for every behaviour `15b786e` added |
| `docs/run-lifecycle.md`, `docs/.citation-lock.yaml` | **Only if #666 merged first** (Task 1 Step 6) | Locked citations into the hook file |

Nothing under `.github/`, `workflows/`, `deploy/` changes. The module docstring example
(`scripts/docs_refs_check.py:32`) and `REPAIR_COMMAND` (`scripts/docs_refs_check.py:317-319`)
keep `--base origin/main` **on purpose** (spec R1 "Do not touch") — do not "fix" them to
match the hook.

**Invariant tests that must stay unmodified and passing** (spec R4 table): 
`test_edited_range_fails_rederive_then_accept_locks_it`,
`test_zero_matches_fails_and_changes_nothing`,
`test_repeated_line_is_ambiguous_without_base_and_relocated_with_it`,
`test_base_diff_touching_the_range_is_ambiguous`,
`test_accept_adds_a_missing_entry_and_refuses_unknown_citations`,
`test_no_lock_file_means_range_only`,
`test_seed_writes_sorted_seeded_entries_and_is_byte_stable`,
`test_hand_shifted_citation_is_rekeyed_and_keeps_seeded`,
`test_missing_entry_and_orphan_entry` (spec R4 item 6). If any change in this plan
requires editing one of them, stop: that change is out of scope.

**Note on TDD for characterization tests.** Tasks 3–7 add coverage for behaviour that is
already on `main` (from `15b786e`), so those tests are expected to **pass on first run**.
Tasks 3–6 each have a "prove it bites" step for their key guard(s): it temporarily breaks
the behaviour in the working tree, confirms the targeted test fails, then restores only
the script with `git checkout -- scripts/docs_refs_check.py` and checks
`git diff --exit-code -- scripts/docs_refs_check.py` is clean. In Tasks 3–6 the only
uncommitted change at that point is the new test code, which the restore does not touch;
in Task 2 the bite step runs after the commit because the script itself changed. A
mutation is never committed. Task 7 has no bite step: its second half (the branch base is
ambiguous) is itself the negative case. Tasks 1 and 2 change behaviour and follow strict
red → green.

Run pytest from the repo root. The commands below set `PYTHONPATH=scripts`, matching
`CLAUDE.md`'s convention; CI's own step is a plain `python -m pytest tests/ -v`
(`.github/workflows/ci.yml`), which also works because the test file inserts `scripts/`
into `sys.path` itself. Do not edit CI.

**Bytecode hazard in the bite steps.** A mutation can keep the file size unchanged (Task
6's block swap does), and `git checkout` can restore the file within the same second, so
a `scripts/__pycache__/docs_refs_check.*.pyc` compiled from the mutated source would
still look valid and make later runs behave like the mutation (seen while dry-running
this plan: `test_rederive_writes_the_lock_before_the_docs` failed after a correct
restore). Every bite step therefore runs pytest with `python -B` and deletes
`scripts/__pycache__` (untracked, gitignored) before the mutated run and after the
restore. Keep both when running them.

---

## Task 1: Revert the advisory hook to `--base HEAD` (spec R1, R2, R4 item 14)

**Files:**
- Modify: `commands/dark-factory-revise-advisory.md` (Phase 4 step 2 bash block, currently lines 155-165)
- Modify: `tests/test_docs_refs_check.py` (append to the "This repo's own docs and lock" section, at end of file)

- [ ] **Step 1: Write the failing text test.** Append at the very end of
  `tests/test_docs_refs_check.py` (after
  `test_repo_citation_lock_exists_and_covers_every_commands_citation`, so it can use
  `_REPO`):

```python


def test_revise_advisory_hook_rederives_against_head():
    # #669: the hook runs before the advisory fix is committed, so HEAD is the base whose
    # diff is exactly the advisory edit; origin/main would double-count earlier re-derives.
    text = (_REPO / "commands" / "dark-factory-revise-advisory.md").read_text(encoding="utf-8")
    assert text.count("--rederive --base HEAD") == 1
    assert "REDERIVE_BASE" not in text
    assert "origin/main" not in text
```

- [ ] **Step 2: Verify it fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py::test_revise_advisory_hook_rederives_against_head -v
```

Expected: `FAILED` — `assert 0 == 1` (the file currently has `--rederive --base "$REDERIVE_BASE"`).

- [ ] **Step 3: Revert the hook block.** In `commands/dark-factory-revise-advisory.md`,
  use an exact-string edit (Edit tool) to replace:

```bash
     if git rev-parse --verify origin/main >/dev/null 2>&1; then
       REDERIVE_BASE=origin/main
     else
       REDERIVE_BASE=HEAD
     fi
     python3 scripts/docs_refs_check.py --rederive --base "$REDERIVE_BASE" docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
```

with:

```bash
     python3 scripts/docs_refs_check.py --rederive --base HEAD docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
```

  Keep the 5-space indentation exactly (the block sits inside a numbered list item).
  Touch no other line: the surrounding `if [ -f docs/.citation-lock.yaml ] ...`,
  `|| echo "revise-advisory: WARNING ..."`, `else`/`fi` lines stay byte-identical. Do not
  add any `$` or `${...}` anywhere (the file's first Archon vocabulary token is on line 8
  and `scripts/command_cache_shape_baseline.json` ratchets it).

- [ ] **Step 4: Verify the revert is byte-identical to the pre-regression file and lands at 202 lines.**

```bash
test "$(wc -l < commands/dark-factory-revise-advisory.md)" -eq 202 && echo "202 lines"
sed -n '202p' commands/dark-factory-revise-advisory.md
grep -c -- '--rederive --base HEAD' commands/dark-factory-revise-advisory.md
grep -c 'origin/main\|REDERIVE_BASE' commands/dark-factory-revise-advisory.md
git diff --stat -- commands/dark-factory-revise-advisory.md
git show 15b786e^:commands/dark-factory-revise-advisory.md | diff - commands/dark-factory-revise-advisory.md && echo "identical to pre-15b786e"
```

Expected output, in order: `202 lines`, `Exit 0.`, `1`, `0`,
` commands/dark-factory-revise-advisory.md | 7 +------` / `1 file changed, 1 insertion(+), 6 deletions(-)`,
`identical to pre-15b786e`. (The last `diff` is valid only while no other commit has
touched this file since `15b786e`; if it prints a diff, check that every differing hunk is
outside Phase 4 step 2 and comes from a later `main` commit — the four checks above it are
the authoritative ones.)

- [ ] **Step 5: Verify the test passes.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py::test_revise_advisory_hook_rederives_against_head -v
```

Expected: `1 passed`.

- [ ] **Step 6: Check whether #666 merged first (spec R2 conditional).**

```bash
grep -c 'commands/dark-factory-revise-advisory.md' docs/.citation-lock.yaml || true
```

  (`grep -c` prints `0` and exits 1 when there is no match; the `|| true` keeps that from
  reading as a failed step.)

  - Output `0` (expected at plan time: the lock has no entry targeting the hook file, and
    `docs/run-lifecycle.md:970-971` cites it only through the bare spans `` `:37-56` `` /
    `` `:60-202` ``, which the tool neither checks nor shifts): **no doc or lock edit.**
    With the file back at 202 lines, `:60-202` and the "`--base HEAD`" prose are correct
    again. Go to Step 7.
  - Output `> 0` (#666 landed and locked full citations into the hook file): run
    ```bash
    python scripts/docs_refs_check.py --rederive --base origin/main docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
    ```
    This run exits 1 by design: it reports the tail citation as "the cited range was
    edited" and as out of range (the file has 202 lines again). For each citation
    reported as "the cited range was edited": re-read the sentence in
    `docs/run-lifecycle.md`, set the range by hand (the file-tail range now ends at
    `202`), then lock it with one explicit call per citation:
    `python scripts/docs_refs_check.py --accept docs/run-lifecycle.md <citation>`.
    `--accept` adds the new entry but leaves the old one (the pre-edit range) in the lock
    as an orphan, so **run the same `--rederive --base origin/main` command a second
    time**: it removes the orphan, prints `docs/.citation-lock.yaml: updated` and
    `Docs reference check passed for 4 doc(s).`, and exits 0. Without that second run
    Step 7 fails with "lock entry ... is no longer cited".
    **Never `--seed`.** Never edit `docs/.citation-lock.yaml` by hand. Stage both files
    with this task's commit.

- [ ] **Step 7: Verify the reference docs and the command-file lint still pass.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "test_repo_ or revise_advisory_hook" -v
[ -f scripts/check_command_cache_shape.py ] && python scripts/check_command_cache_shape.py commands
```

Expected: `Docs reference check passed for 4 doc(s).`; 3 passed
(`test_repo_docs_pass_the_reference_check_with_the_citation_lock`,
`test_repo_citation_lock_exists_and_covers_every_commands_citation`,
`test_revise_advisory_hook_rederives_against_head`); the cache-shape check (present on
`main` since #236) exits 0 — the edit is far below line 8 and removes no vocabulary token.

- [ ] **Step 8: Commit.**

```bash
git add commands/dark-factory-revise-advisory.md tests/test_docs_refs_check.py
git commit -m "fix(#669): revert revise-advisory citation re-derive to --base HEAD"
```

(If Step 6 took the `> 0` branch, also `git add docs/run-lifecycle.md docs/.citation-lock.yaml`.)

---

## Task 2: Blank-citation message in `_content_errors()` (spec R3, R4 items 4–6)

**Files:**
- Modify: `scripts/docs_refs_check.py` (`_content_errors()`, the `if entry is None:` branch, currently lines 498-499)
- Modify: `tests/test_docs_refs_check.py` (insert after `test_missing_entry_and_orphan_entry`)

- [ ] **Step 1: Add the blank-target constant and the two message tests.** Insert directly
  after `test_missing_entry_and_orphan_entry` (before
  `test_crlf_and_reindented_target_lines_hash_the_same`):

```python


_BLANK_CMD = _CMD.replace("line 5\n", "\n")  # commands/c.md with line 5 blank


def test_unlocked_blank_citation_names_the_anchor_not_the_repair_command(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n", _BLANK_CMD)
    _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:5`\n", encoding="utf-8")
    errors = _content(root)
    assert len(errors) == 1, errors
    assert "commands/c.md:5 is blank; pick a non-blank anchor" in errors[0]
    assert not any(s in errors[0] for s in ("run:", "--rederive", REPAIR_COMMAND))


def test_unlocked_out_of_range_citation_is_not_reported_as_blank(tmp_path):
    # _Targets.blank() slices, so an out-of-range range is "blank" unless guarded.
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:25`\n", encoding="utf-8")
    errors = _content(root)
    assert any("commands/c.md:25 has no entry" in e and e.endswith(REPAIR_COMMAND) for e in errors), errors
    assert not any("is blank" in e for e in errors)
```

- [ ] **Step 2: Verify the blank test fails and the guard test passes.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "unlocked_blank_citation or unlocked_out_of_range" -v
```

Expected: `test_unlocked_blank_citation_names_the_anchor_not_the_repair_command FAILED`
(the error says `has no entry in docs/.citation-lock.yaml; run: python scripts/docs_refs_check.py --rederive ...`);
`test_unlocked_out_of_range_citation_is_not_reported_as_blank PASSED` (it guards the
`digest(...) is not None` check added in Step 3 — Step 6 proves it bites).

- [ ] **Step 3: Implement the message branch.** In `scripts/docs_refs_check.py`,
  `_content_errors()`, replace:

```python
        if entry is None:
            errors.append(f"{where} has no entry in {LOCK_FILE}; run: {REPAIR_COMMAND}")
```

with:

```python
        if entry is None:
            if (targets.digest(p.target, p.first, p.last) is not None
                    and targets.blank(p.target, p.first, p.last)):
                errors.append(f"{where} is blank; pick a non-blank anchor "
                              f"(a blank range is never locked, so no repair command applies)")
            else:
                errors.append(f"{where} has no entry in {LOCK_FILE}; run: {REPAIR_COMMAND}")
```

  Leave the `elif targets.digest(...) != entry.hash:` ("no longer holds the locked
  content") branch and the orphan loop exactly as they are. Do not touch the module
  docstring or `REPAIR_COMMAND`.

- [ ] **Step 4: Verify both pass, and the unchanged-message test still passes unmodified.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "unlocked_blank_citation or unlocked_out_of_range or test_missing_entry_and_orphan_entry or test_hash_match_and_mismatch" -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/docs_refs_check.py tests/test_docs_refs_check.py
git commit -m "fix(#669): blank-range citation message names a non-blank anchor, not the repair command"
```

- [ ] **Step 6: Prove the guard test bites (after the commit, working tree only).** The
  mutation is discarded with `git checkout`, which is only safe once Step 5 has committed
  the real change:

```bash
sed -i 's/            if (targets.digest(p.target, p.first, p.last) is not None$/            if (True/' scripts/docs_refs_check.py
grep -n '            if (True$' scripts/docs_refs_check.py
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k unlocked_out_of_range -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__
git diff --exit-code && echo clean
```

  Expected: the `grep` prints one line (`499:            if (True`); pytest `1 failed`;
  then `clean`.

---

## Task 3: Blank ranges are never locked — `seed()`, new prose, `--accept` (spec R4 items 1–3)

**Files:**
- Modify: `tests/test_docs_refs_check.py` (insert directly after the two tests added in Task 2)

- [ ] **Step 1: Write the tests.** Insert after
  `test_unlocked_out_of_range_citation_is_not_reported_as_blank`:

```python


def test_seed_never_locks_an_all_blank_range(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:5`\n", _BLANK_CMD)
    assert seed([_doc(root)], root) == 1
    lock = root / LOCK_FILE
    assert [e["lines"] for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]] == [[3, 3]]


def test_new_prose_never_locks_an_all_blank_range(tmp_path):
    # Contrast test_new_prose_is_locked_without_seeded: a blank new citation gains no entry.
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n", _BLANK_CMD)
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:5`\n", encoding="utf-8")
    assert rederive([_doc(root)], root)[1] == []
    assert [e["lines"] for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]] == [[3, 3]]


def test_accept_refuses_a_blank_citation(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n", _BLANK_CMD)
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:5`\n", encoding="utf-8")
    before = lock.read_bytes()
    printed, failures = accept(_doc(root), "commands/c.md:5", root)
    assert printed == [] and len(failures) == 1
    assert "is blank" in failures[0] and "pick a non-blank anchor" in failures[0]
    assert lock.read_bytes() == before
```

- [ ] **Step 2: Run them.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "never_locks_an_all_blank or accept_refuses_a_blank" -v
```

Expected: `3 passed` (characterization of `15b786e`'s behaviour).

- [ ] **Step 3: Prove the seed and new-prose tests bite (working tree only).** Two
  separate mutations, one per guard; only the script is restored, the uncommitted tests
  stay:

```bash
python3 - <<'EOF'
p = "scripts/docs_refs_check.py"
s = open(p, encoding="utf-8").read()
old = "            if targets.blank(p.target, p.first, p.last):\n                # An all-blank"
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, "            if False:\n                # An all-blank"))
EOF
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k never_locks_an_all_blank -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__

python3 - <<'EOF'
p = "scripts/docs_refs_check.py"
s = open(p, encoding="utf-8").read()
old = "                    and not targets.blank(target, key[2], key[3])):"
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, "                    and True):"))
EOF
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k never_locks_an_all_blank -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__
git diff --exit-code -- scripts/docs_refs_check.py && echo clean
```

Expected: first run `1 failed, 1 passed` (`test_seed_never_locks_an_all_blank_range`
fails); second run `1 failed, 1 passed` (`test_new_prose_never_locks_an_all_blank_range`
fails); then `clean`.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_docs_refs_check.py
git commit -m "test(#669): blank citation ranges are never locked by seed, rederive or accept"
```

---

## Task 4: CRLF round-trip, `--base` leading dash, base-resolution failure (spec R4 items 7–9)

**Files:**
- Modify: `tests/test_docs_refs_check.py`

- [ ] **Step 1: Write the CRLF test.** Insert directly after
  `test_crlf_and_reindented_target_lines_hash_the_same` (that test covers a CRLF
  *target*; this one covers a CRLF *doc* being rewritten):

```python


def test_crlf_doc_round_trips_through_rederive(tmp_path):
    root = _lock_tree(tmp_path, "")
    _doc(root).write_bytes(b"intro\r\n`commands/c.md:3`\r\nend\r\n")
    _seeded(root)
    _insert(root, 0, ["new"])
    printed, failures = rederive([_doc(root)], root)
    assert failures == []
    assert _doc(root).read_bytes() == b"intro\r\n`commands/c.md:4`\r\nend\r\n"
```

- [ ] **Step 2: Write the two `--base` tests.** Insert directly after
  `test_git_unavailable_is_ambiguous`:

```python


def test_base_starting_with_a_dash_is_refused_without_calling_git(tmp_path, monkeypatch):
    import docs_refs_check

    def no_git(*args, **kwargs):
        raise AssertionError("git must not be called for a base starting with '-'")
    monkeypatch.setattr(docs_refs_check, "_git", no_git)
    pos, reason = docs_refs_check._base_position(tmp_path, "-x", "commands/c.md", 1, 1)
    assert pos is None and reason is not None and "starts with '-'" in reason


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("base, reason", [
    ("no-such-ref", "could not resolve or diff --base 'no-such-ref'"),
    ("-x", "starts with '-'"),
])
def test_base_resolution_failure_is_named_in_the_ambiguous_message(tmp_path, base, reason):
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    lock = root / LOCK_FILE
    _insert(root, 0, ["new"])
    doc_before, lock_before = _doc(root).read_bytes(), lock.read_bytes()
    printed, failures = rederive([_doc(root)], root, base=base)
    assert printed == [] and len(failures) == 1, failures
    assert "ambiguous" in failures[0] and "the --base diff does not pick one" in failures[0]
    assert reason in failures[0]
    assert (_doc(root).read_bytes(), lock.read_bytes()) == (doc_before, lock_before)
```

- [ ] **Step 3: Run them.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "crlf_doc_round_trips or base_starting_with_a_dash or base_resolution_failure" -v
```

Expected: `4 passed` (the parametrized test counts twice).

- [ ] **Step 4: Prove the CRLF test bites (working tree only).**

```bash
python3 - <<'EOF'
p = "scripts/docs_refs_check.py"
s = open(p, encoding="utf-8").read()
old = 'with open(doc, "r", encoding="utf-8", newline="") as f:'
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, 'with open(doc, "r", encoding="utf-8") as f:'))
EOF
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k crlf_doc_round_trips -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__
git diff --exit-code -- scripts/docs_refs_check.py && echo clean
```

Expected: `1 failed`, then `clean`.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_docs_refs_check.py
git commit -m "test(#669): CRLF doc round-trip and --base refusal/resolution-failure messages"
```

---

## Task 5: `--accept` CLI and printed lines (spec R4 items 10–11)

**Files:**
- Modify: `tests/test_docs_refs_check.py` (insert after `test_accept_adds_a_missing_entry_and_refuses_unknown_citations`)

- [ ] **Step 1: Write the tests.**

```python


def test_accept_rejects_an_extra_positional_doc(tmp_path, capsys):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    before = lock.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["--accept", str(_doc(root)), "commands/c.md:3", "extra.md", "--repo-root", str(root)])
    assert exc.value.code == 2
    assert "unexpected extra argument(s): extra.md" in capsys.readouterr().err
    assert lock.read_bytes() == before


def test_accept_prints_a_doc_line_of_the_citation_it_accepted(tmp_path):
    # accept() de-duplicates parts by key, so a citation written on two doc lines prints
    # one line per distinct part. Which of the two lines is named is not asserted.
    root = _lock_tree(tmp_path, "`commands/c.md:12`\n\n`commands/c.md:3,9`\nagain `commands/c.md:3,9`\n")
    _seeded(root)
    printed, failures = accept(_doc(root), "commands/c.md:3,9", root)
    assert failures == [] and len(printed) == 2, printed
    for line in printed:
        assert line.startswith(("accepted docs/notes.md:3 ", "accepted docs/notes.md:4 ")), line
        assert "docs/notes.md:1 " not in line
    assert sorted(line.rsplit(": ", 1)[1] for line in printed) == ["line 3 -> line 3", "line 9 -> line 9"]
```

  Do **not** change `accept()` to make a stricter assertion pass (spec R4 item 11).

- [ ] **Step 2: Run them.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k "accept_rejects_an_extra or accept_prints_a_doc_line" -v
```

Expected: `2 passed`.

- [ ] **Step 3: Prove the extra-argument test bites (working tree only).**

```bash
python3 - <<'EOF'
p = "scripts/docs_refs_check.py"
s = open(p, encoding="utf-8").read()
old = "    if args.accept and args.docs:\n"
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, "    if False:\n"))
EOF
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k accept_rejects_an_extra -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__
git diff --exit-code -- scripts/docs_refs_check.py && echo clean
```

Expected: `1 failed`, then `clean`.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_docs_refs_check.py
git commit -m "test(#669): --accept rejects extra positionals and prints the accepted citation's doc line"
```

---

## Task 6: The lock is written before the docs (spec R4 item 12)

**Files:**
- Modify: `tests/test_docs_refs_check.py` (insert after `test_failing_citation_alongside_a_fixable_one`)

- [ ] **Step 1: Write the test.** `_write_lock` uses `Path.write_text`, which does not go
  through the module-global `open`, so patching `docs_refs_check.open` only blocks the doc
  writes:

```python


def test_rederive_writes_the_lock_before_the_docs(tmp_path, monkeypatch):
    import builtins
    import docs_refs_check

    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _insert(root, 0, ["new"])

    def no_doc_writes(file, mode="r", *args, **kwargs):
        if "w" in mode:
            raise OSError("simulated crash writing a doc")
        return builtins.open(file, mode, *args, **kwargs)
    monkeypatch.setattr(docs_refs_check, "open", no_doc_writes, raising=False)
    with pytest.raises(OSError, match="simulated crash"):
        rederive([_doc(root)], root)
    assert [e["lines"] for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]] == [[4, 4]]
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:3`\n"
```

- [ ] **Step 2: Run it.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k rederive_writes_the_lock_before -v
```

Expected: `1 passed`.

- [ ] **Step 3: Prove it bites (working tree only)** — swap the two write blocks in `rederive()`:

```bash
python3 - <<'EOF'
p = "scripts/docs_refs_check.py"
s = open(p, encoding="utf-8").read()
lock_w = '    if _write_lock(lock, result):\n        printed.append(f"{LOCK_FILE}: updated")\n'
docs_w = ('    for path, text in texts.items():\n'
          '        with open(path, "w", encoding="utf-8", newline="") as f:\n'
          '            f.write(text)\n')
assert s.count(lock_w + docs_w) == 1
open(p, "w", encoding="utf-8").write(s.replace(lock_w + docs_w, docs_w + lock_w))
EOF
rm -rf scripts/__pycache__ && PYTHONPATH=scripts python -B -m pytest tests/test_docs_refs_check.py -k rederive_writes_the_lock_before -q
git checkout -- scripts/docs_refs_check.py && rm -rf scripts/__pycache__
git diff --exit-code -- scripts/docs_refs_check.py && echo clean
```

Expected: `1 failed` (the lock still holds `[3, 3]`), then `clean`.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_docs_refs_check.py
git commit -m "test(#669): rederive writes the lock before the docs"
```

---

## Task 7: Hook base behaviour — `HEAD` vs the branch base (spec R4 item 13, "Why `HEAD`")

**Files:**
- Modify: `tests/test_docs_refs_check.py` (insert after the Task 4 `test_base_resolution_failure_is_named_in_the_ambiguous_message`)

The scenario mirrors a real run: commit A holds a seeded lock (the branch base); the
implement phase inserts lines above a repeated line and re-derives, then commits (B); the
advisory fix inserts more lines, uncommitted, and the hook re-derives. The insert counts
are **odd on purpose** (3, then 1) with `_BRACES` (`{` on even lines): an even advisory
shift would leave a `{` at the locked line, so nothing would count as moved; an even
implement shift would make the double-counted position land on another `{` candidate.
With 3 + 1, `HEAD` gives 7 + 1 = 8 (correct) and A gives 7 + 4 = 11 (not a candidate of
6, 8, 10), so A is ambiguous.

- [ ] **Step 1: Write the helpers and the test.**

```python


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", message], check=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _implement_then_advisory(tmp_path: Path) -> tuple[Path, str]:
    """A: seeded lock. B: 3 lines inserted above and re-derived. Then 1 more, uncommitted."""
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    base_a = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    _insert(root, 0, ["impl 1", "impl 2", "impl 3"])
    assert rederive([_doc(root)], root, base=base_a)[1] == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:7`\n"
    _commit(root, "implement")
    _insert(root, 0, ["advisory 1"])
    return root, base_a


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_hook_base_head_relocates_after_an_earlier_rederive_and_the_branch_base_does_not(tmp_path):
    root, _ = _implement_then_advisory(tmp_path / "head")
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert failures == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:8`\n"

    root, base_a = _implement_then_advisory(tmp_path / "branch-base")
    lock = root / LOCK_FILE
    doc_before, lock_before = _doc(root).read_bytes(), lock.read_bytes()
    printed, failures = rederive([_doc(root)], root, base=base_a)
    assert printed == [] and len(failures) == 1 and "ambiguous" in failures[0], failures
    assert (_doc(root).read_bytes(), lock.read_bytes()) == (doc_before, lock_before)
```

- [ ] **Step 2: Run it.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -k hook_base_head_relocates -v
```

Expected: `1 passed`. (This documents the tool behaviour that justifies Task 1; it passes
independently of the command-file text, which Task 1's text test pins.)

- [ ] **Step 3: Commit.**

```bash
git add tests/test_docs_refs_check.py
git commit -m "test(#669): hook base HEAD relocates after an earlier rederive; the branch base is ambiguous"
```

---

## Task 8: Full verification and scope check

**Files:** none modified.

- [ ] **Step 1: Full test file, including the unmodified invariant tests.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -v
```

Expected: all pass; 14 more collected items than before this ticket (Task 1: 1,
Task 2: 2, Task 3: 3, Task 4: 4 — the parametrized test counts twice — Task 5: 2,
Task 6: 1, Task 7: 1).

- [ ] **Step 2: Full suite and CI-parity checks.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
[ -f scripts/check_command_cache_shape.py ] && python scripts/check_command_cache_shape.py commands
```

Expected: no failures; `Docs reference check passed for 4 doc(s).`; cache-shape check
exits 0.

- [ ] **Step 3: Invariant tests untouched.** The existing tests must be unmodified — the
  diff to the test file must be additions only:

```bash
git diff origin/main HEAD --numstat -- tests/test_docs_refs_check.py
```

Expected: second column (deletions) is `0`.

- [ ] **Step 4: Scope check (two-dot diff, so commits `main` merged independently don't show up).**

```bash
git diff origin/main HEAD --stat
```

Expected: only `commands/dark-factory-revise-advisory.md`, `scripts/docs_refs_check.py`,
`tests/test_docs_refs_check.py` (plus this ticket's spec/plan docs, and
`docs/run-lifecycle.md`/`docs/.citation-lock.yaml` only if Task 1 Step 6 took the
"#666 merged first" branch). Nothing under `.github/`, `workflows/`, `deploy/`.
`git diff origin/main HEAD -- scripts/docs_refs_check.py` shows only the `_content_errors()`
hunk (+6/-1); the docstring line 32 and `REPAIR_COMMAND` are unchanged.

If the stat lists files this ticket never touched, `origin/main` has moved since the
branch was forked; confirm with the branch-only view
`git diff "$(git merge-base origin/main HEAD)" HEAD --stat`, which lists only what this
branch changed. Files that appear only in the two-dot view are not this ticket's.

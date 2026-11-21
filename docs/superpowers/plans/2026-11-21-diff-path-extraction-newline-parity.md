# Diff Path Extraction Newline Parity Implementation Plan

**Issue:** #552

**Revised:** 2026-11-21 (operator amendment after plan gate review)

> **For agentic workers:** Execute tasks in order. Steps use checkbox (`- [ ]`) syntax for
> tracking. Each task ends in exactly one commit. Do not reorder Task 1 after Task 2 — see
> "Why this order" below.

**Goal:** Make `extract_diff_paths` split a diff into lines exactly as `git apply` does
(`\n` only), so a non-`\n` separator (`\r`, `\f`, `\v`, `\x1c`-`\x1e`, `\x85`, U+2028,
U+2029) can no longer hide a real `---`/`+++` file header from the validator — and close the
two checks that the old splitting kept fail-closed only by accident (tool-frontmatter
detection and mode-line validation).

**Architecture:** Three small edits in `scripts/factory_core/mechanism_candidate.py`:
(1) `extract_diff_paths` replaces `diff.splitlines()` with "strip exactly one trailing `\n`,
then `split("\n")`" (no phantom trailing `""`); (2) the mode-header branch is entered on the
`old mode |new mode |deleted file mode |new file mode ` prefix and rejects unless
`_MODE_HEADER_RE` matches with a regular-file mode; (3) `validate_candidate` tests every
`str.splitlines()` fragment of a changed line's content (sans leading `+`/`-`) against an
un-prefixed `_TOOL_FRONTMATTER_RE`. No other parser branch changes. Spec:
`docs/superpowers/specs/2026-11-21-diff-path-extraction-newline-parity-design.md`.

**Tech Stack:** Python 3 stdlib (`re`), pytest, real `git apply --numstat` as the test oracle
(existing `_git_apply_paths` helper).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/mechanism_candidate.py` | Modify | `_MODE_HEADER_PREFIXES` constant + mode branch (Req 8); `\n`-only split + docstring (Req 1-3); `_TOOL_FRONTMATTER_RE` pattern + comment, frontmatter comprehension in `validate_candidate` (Req 7) |
| `tests/test_mechanism_candidate.py` | Modify | T1 (`test_separator_cannot_hide_a_file_header`, 9 cases), T2 (annotate the existing `"ends inside a hunk"` row as the Req 2 guard), T3 (`test_tool_frontmatter_after_separator_forces_h3`, 9 cases), T4 (3 rows in `test_diff_path_extraction`), T5 (1 row in `test_diff_path_extraction`), plus one honest CRLF-body parity case in `test_diff_paths_match_git_apply` |
| `docs/configuration.md` | Modify | Re-map the citation `mechanism_candidate.py:373-407` to `:381-415` (line 530); the edits shift it by 8 lines and `docs_refs_check.py` only checks ranges |

No other file changes beyond that one citation re-map. `dark-factory/scripts/...` (an untracked runtime copy in some
containers) is **not** edited — only the tracked `scripts/` tree.

### Why this order

- Task 1 (Req 8) must land before the line-split change: with `\n`-only splitting alone,
  `new file mode 120000\r` would start validating (T4 `\r` goes red). Landing Req 8 first
  means no commit on the branch ever accepts it.
- Req 1 and Req 7 land in **one** commit (Task 2): with the split change alone, a
  `commands/*.md` edit such as `+description: x\rallowed-tools: Bash` classifies H1 instead
  of being rejected; no commit on the branch may carry that weakening.

### Test-ID reference (spec "Tests" section → this plan)

| Spec | Where it lands |
|---|---|
| T1 (9 seps) | new `test_separator_cannot_hide_a_file_header` (Task 2) |
| T2 | existing row `(_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,2 +1,2 @@"), "ends inside a hunk")` in `test_diff_path_extraction` — already present; Task 2 adds a comment pinning it as the Req 2 guard |
| T3 (9 seps) | new `test_tool_frontmatter_after_separator_forces_h3` (Task 2) |
| T4 (`" "`, `"\t"`, `"\r"`) | 3 new rows in `test_diff_path_extraction` (Task 1) |
| T5 | 1 new row in `test_diff_path_extraction` (Task 2) |

---

## Task 1: Reject any mode line that fails `_MODE_HEADER_RE` (Requirement 8)

**Files:**
- Modify: `tests/test_mechanism_candidate.py` (the `test_diff_path_extraction` parametrize list)
- Modify: `scripts/factory_core/mechanism_candidate.py` (constants block near `_MODE_HEADER_RE`; header branch of `extract_diff_paths`)

- [ ] **Step 1: Write the failing test rows (T4)**

In `tests/test_mechanism_candidate.py`, find the last row of the
`@pytest.mark.parametrize("diff, match", [...])` list above `def test_diff_path_extraction`:

```python
    (f"diff --git a/{_DOC} b/{_DOC}\nold mode 100644\nnew mode 120000\n", "only regular file modes"),
])
```

Replace it with:

```python
    (f"diff --git a/{_DOC} b/{_DOC}\nold mode 100644\nnew mode 120000\n", "only regular file modes"),
    # git accepts any whitespace after the mode, so a mode line that fails _MODE_HEADER_RE
    # must be rejected, not waved through as a generic extended header (#552).
    *[(f"diff --git a/{_DOC} b/{_DOC}\nnew file mode 120000{tail}\n--- /dev/null\n+++ b/{_DOC}\n"
       "@@ -0,0 +1 @@\n+/etc/passwd\n", "only regular file modes") for tail in (" ", "\t", "\r")],
])
```

- [ ] **Step 2: Run the tests to verify the space/tab rows fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q -k test_diff_path_extraction`

Expected: `2 failed, 22 passed` — the failures are
`test_diff_path_extraction[diff20-only regular file modes]` (trailing space) and
`[diff21-only regular file modes]` (trailing tab), each `Failed: DID NOT RAISE`. The `\r`
row (`diff22`) passes on `main` (it is a guard for Task 2's split change).

- [ ] **Step 3: Add the mode-prefix constant**

In `scripts/factory_core/mechanism_candidate.py`, replace:

```python
_MODE_HEADER_RE = re.compile(r"^(?:old mode|new mode|deleted file mode|new file mode) (\S+)$")
```

with:

```python
_MODE_HEADER_RE = re.compile(r"^(?:old mode|new mode|deleted file mode|new file mode) (\S+)$")
_MODE_HEADER_PREFIXES = ("old mode ", "new mode ", "deleted file mode ", "new file mode ")
```

- [ ] **Step 4: Enter the mode branch on the prefix, not the full match**

In `extract_diff_paths`, replace:

```python
        elif _MODE_HEADER_RE.match(line):
            mode = _MODE_HEADER_RE.match(line).group(1)
            if mode not in _REGULAR_FILE_MODES:
```

with:

```python
        elif line.startswith(_MODE_HEADER_PREFIXES):
            m = _MODE_HEADER_RE.match(line)
            if not m or m.group(1) not in _REGULAR_FILE_MODES:
```

Leave the `_fail(...)` body (the "only regular file modes 100644/100755 are accepted"
message) and `_EXTENDED_HEADER_RE` untouched — mode lines simply never reach the
`_EXTENDED_HEADER_RE` fallback any more.

- [ ] **Step 5: Run the module's tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q`

Expected: `103 passed`

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/mechanism_candidate.py tests/test_mechanism_candidate.py
git commit -m "fix(mechanism-candidate): reject mode lines that fail the mode regex (#552)

A mode header with trailing whitespace (new file mode 120000<space|tab>) fell
through to _EXTENDED_HEADER_RE and validated while git apply creates a symlink.
Enter the mode branch on the prefix and require a full _MODE_HEADER_RE match
with a regular-file mode. Lands before the \\n-only split so the \\r variant is
never accepted on this branch."
```

---

## Task 2: Split on `\n` only, keep tool-frontmatter detection fail-closed (Requirements 1, 2, 3, 6, 7)

**Files:**
- Modify: `tests/test_mechanism_candidate.py` (`test_diff_path_extraction` list; `test_diff_paths_match_git_apply`; new tests after it and after `test_risk_class_underclaim_rejected`)
- Modify: `scripts/factory_core/mechanism_candidate.py` (`_TOOL_FRONTMATTER_RE`; `extract_diff_paths` docstring + split; `validate_candidate` frontmatter comprehension)

- [ ] **Step 1: Annotate the existing T2 guard row and add the T5 row**

In the `test_diff_path_extraction` parametrize list, replace:

```python
    (_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,2 +1,2 @@"), "ends inside a hunk"),
```

with:

```python
    # Owes a line on both sides yet ends in "\n": a bare diff.split("\n") would accept its
    # phantom trailing "" as the missing blank context line (#552).
    (_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,2 +1,2 @@"), "ends inside a hunk"),
```

Then replace:

```python
    (f"diff --git a/{_DOC} b/{_DOC}\nGIT binary patch\nliteral 5\n", "binary patches"),
```

with:

```python
    (f"diff --git a/{_DOC} b/{_DOC}\nGIT binary patch\nliteral 5\n", "binary patches"),
    # Lines split on "\n" only, so a CRLF diff's "\r" stays in its header line (#552).
    (_diff(_DOC).replace("\n", "\r\n"), "malformed diff --git header"),
```

- [ ] **Step 2: Add the honest CRLF-body parity case and the T1 test**

In `test_diff_paths_match_git_apply`, replace:

```python
    for diff in (two, tricky, _diff(_DOC)):
        assert set(mc.extract_diff_paths(diff)) == _git_apply_paths(diff, tmp_path)
```

with:

```python
    crlf_body = _diff(_DOC, "-old\r", "+new\x0c\r")  # an honest edit of a CRLF file (#552)
    for diff in (two, tricky, _diff(_DOC), crlf_body):
        assert set(mc.extract_diff_paths(diff)) == _git_apply_paths(diff, tmp_path)


# Every character str.splitlines() treats as a line boundary besides "\n" (#552).
_SEPS = ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
_HIDDEN = ("@@ -1,1 +1,1 @@\n--- a/scheduler.sh\n+++ b/scheduler.sh\n"
           "@@ -1,1 +1,1 @@\n-safe\n+pwned\n")


@pytest.mark.parametrize("sep", _SEPS)
def test_separator_cannot_hide_a_file_header(sep, tmp_path):
    """`git apply` splits on "\\n" only, so a fake @@ glued after a separator is body content."""
    diff = _diff(_DOC)[:-1] + sep + _HIDDEN
    assert "scheduler.sh" in _git_apply_paths(diff, tmp_path)  # a live attack on git's reading
    with pytest.raises(mc.MechanismCandidateError, match="after a hunk"):
        mc.extract_diff_paths(diff)
```

(Keep the two blank lines before `# ── Risk classification ──` that follow.) The T1 shape is
the issue's reproduction exactly: `_diff(_DOC)[:-1]` ends in `+new`, then the separator,
then the fake `@@` and the real `scheduler.sh` block. Do **not** use
`sep + _diff("scheduler.sh")` — with a `diff --git` line after the separator, `main` already
reports both paths. `_git_apply_paths` passes the diff to git as UTF-8 text over stdin, so
it reaches git byte-exact on the Linux CI runner.

- [ ] **Step 3: Add the T3 test**

Directly after `test_risk_class_underclaim_rejected` (whose last statement is the
`frontmatter = _diff(cmd, "-allowed-tools: Read", "+allowed-tools: Read, Bash")` /
`pytest.raises(..., match="derived risk class H3")` block), add:

```python


@pytest.mark.parametrize("sep", _SEPS)
def test_tool_frontmatter_after_separator_forces_h3(sep):
    """A YAML reader may break a line where git does not; detection takes the wider reading."""
    cmd = "commands/dark-factory-plan.md"
    frontmatter = _diff(cmd, "-description: x", "+description: x" + sep + "allowed-tools: Bash")
    with pytest.raises(mc.MechanismCandidateError, match="derived risk class H3"):
        mc.validate_candidate(_record(editable_path_allowlist=[cmd], diff=frontmatter, risk_class="H2"),
                              safety=_EMPTY_SAFETY)
```

(Two blank lines before and after, matching the file's top-level spacing; the next
function is `test_h3_unconditional`.)

- [ ] **Step 4: Run the tests to verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q`

Expected: `19 failed, 103 passed` —
- `test_diff_path_extraction[diff16-malformed diff --git header]` (T5): `DID NOT RAISE`
  (`splitlines()` swallows `\r\n`, so the CRLF diff validates on `main`);
- 9 × `test_separator_cannot_hide_a_file_header[...]` (T1): `DID NOT RAISE` (the extractor
  reports only `docs/superpowers/notes/t.md` while git also patches `scheduler.sh`);
- 9 × `test_tool_frontmatter_after_separator_forces_h3[...]` (T3): the regex `match` fails
  (`"unexpected line 'allowed-tools: Bash' after a hunk"` is raised instead of
  `"derived risk class H3"`).

The new `crlf_body` parity case and the annotated T2 row pass (both are guards).
The `diffNN` parameter IDs here and in Task 1 Step 2 assume rows are inserted exactly where
these steps say; they are informative — the pass/fail **counts** are binding.

- [ ] **Step 5: Split on `\n` only (Requirements 1, 2)**

In `extract_diff_paths`'s docstring, replace:

```python
    `\\ No newline` marker — a smuggled `---`/`+++` triplet is rejected, never skipped.
```

with:

```python
    `\\ No newline` marker — a smuggled `---`/`+++` triplet is rejected, never skipped.
    Lines are split on `\\n` only, as `git apply` splits them (#552): `\\r`, `\\f`, `\\v`,
    `\\x1c`-`\\x1e`, `\\x85`, U+2028 and U+2029 are line content, never a line boundary.
```

The docstring is a plain (non-raw) string, so backslashes are doubled exactly as the
existing `\\ No newline` already is; `help(mc.extract_diff_paths)` then renders `\n`,
`\r`, … literally rather than as control characters.

Then replace:

```python
    for line in diff.splitlines():
```

with:

```python
    # Drop exactly one final "\n" so a terminated diff has no phantom trailing "" line,
    # which the hunk loop would otherwise accept as a missing blank context line.
    lines = diff[:-1].split("\n") if diff.endswith("\n") else diff.split("\n")
    for line in lines:
```

Do not change any other branch of the loop. Do **not** use a bare `diff.split("\n")` — it
fails the T2 row (`ends inside a hunk`), verified.

- [ ] **Step 6: Run the tests — only T3 should still fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q`

Expected: `9 failed, 113 passed` — exactly the 9
`test_tool_frontmatter_after_separator_forces_h3[...]` cases, now failing with
`DID NOT RAISE` (the separator stays inside one changed line, so the path classifies H1).
This is the weakening Requirement 7 closes; do not commit in this state.

- [ ] **Step 7: Match tool frontmatter per `splitlines()` fragment (Requirement 7)**

In `scripts/factory_core/mechanism_candidate.py`, replace:

```python
# A changed allowed-tools:/disallowed-tools: line in a commands/*.md file forces H3.
_TOOL_FRONTMATTER_RE = re.compile(r"^[+-]\s*(?:allowed-tools|disallowed-tools)\s*:")
```

with:

```python
# A changed allowed-tools:/disallowed-tools: line in a commands/*.md file forces H3. Matched
# against every str.splitlines() fragment of a changed line's content (#552): a YAML reader
# may break a line on \r, \x85, U+2028 or U+2029 even though git does not.
_TOOL_FRONTMATTER_RE = re.compile(r"^\s*(?:allowed-tools|disallowed-tools)\s*:")
```

Then in `validate_candidate`, replace:

```python
    frontmatter = {p for p, lines in changed.items()
                   if any(_TOOL_FRONTMATTER_RE.match(ln) for ln in lines)}
```

with:

```python
    frontmatter = {p for p, lines in changed.items()
                   if any(_TOOL_FRONTMATTER_RE.match(frag)
                          for ln in lines for frag in ln[1:].splitlines())}
```

`ln[1:]` drops the `+`/`-` tag (every entry in `changes` starts with one), which is why the
`[+-]` prefix leaves the regex. `splitlines()` is deliberate here — for detection the wider
separator set is the conservative reading. `_TOOL_FRONTMATTER_RE` has no other user
(`grep -rn _TOOL_FRONTMATTER_RE scripts tests` → only these two lines).

- [ ] **Step 8: Run the module's tests to verify they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q`

Expected: `122 passed` (includes the unchanged `test_fixture_diffs_match_git_apply` over
every `evals/mechanism-candidates/fixtures/candidate-*.json` diff, and the existing
`\n`-delimited frontmatter case in `test_risk_class_underclaim_rejected`).

- [ ] **Step 8b: Re-map the shifted doc citation.** In `docs/configuration.md`, replace
  `mechanism_candidate.py:373-407` with `mechanism_candidate.py:381-415` (one occurrence).
  Verify: `sed -n '381p;415p' scripts/factory_core/mechanism_candidate.py` prints the
  `load_safety` docstring's first line and the
  `if any(p.startswith(pre) for pre in safety["hard_exclude_paths"]):` line. If the two
  lines are elsewhere, cite where they actually are. Add `docs/configuration.md` to this
  step's commit.

- [ ] **Step 9: Commit**

```bash
git add scripts/factory_core/mechanism_candidate.py tests/test_mechanism_candidate.py docs/configuration.md
git commit -m "fix(mechanism-candidate): split diffs on \\n only, as git apply does (#552)

str.splitlines() also breaks on \\r, \\f, \\v, \\x1c-\\x1e, \\x85, U+2028 and
U+2029, so a separator glued after a hunk-body line could open a fake hunk that
swallowed a real ---/+++ header (scheduler.sh) while git apply patched it.
Strip one trailing \\n and split on \\n only (a bare split would accept a
truncated hunk via its phantom trailing \"\"). Tool-frontmatter detection now
checks every splitlines() fragment of a changed line so a smuggled
allowed-tools: still forces H3. CRLF-encoded headers are now rejected
(fail closed); CRLF hunk bodies still validate."
```

---

## Task 3: Full-suite verification and push

**Files:** none modified.

- [ ] **Step 1: Confirm scope**

Run: `git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory'`

Expected: exactly three files (the two below plus `docs/configuration.md`) —
`scripts/factory_core/mechanism_candidate.py` and `tests/test_mechanism_candidate.py`.
(The spec/plan under `docs/superpowers/` and `.archon/memory/` come from the refine/plan
phases and are excluded from this check.)

- [ ] **Step 2: Confirm no `.splitlines()` remains in the diff walker**

Run: `grep -n "splitlines" scripts/factory_core/mechanism_candidate.py`

Expected: exactly two hits, both outside `extract_diff_paths` —
1. the `_TOOL_FRONTMATTER_RE` comment added in Task 2 Step 7
   (`# against every str.splitlines() fragment of a changed line's content (#552): ...`);
   this comment documents Requirement 7 and must stay;
2. `for ln in lines for frag in ln[1:].splitlines())}` in `validate_candidate`'s
   frontmatter comprehension.

`extract_diff_paths` (the diff walker) has no `splitlines` call.

- [ ] **Step 3: Run the full suite as CI does**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q`

Expected: all tests pass (no new failures relative to `main`; the module's own file reports
`122 passed` within the total).

- [ ] **Step 4: Push**

```bash
git push -u origin "$(git branch --show-current)"
```

Expected: the branch is pushed; CI (`.github/workflows/ci.yml`) runs
`python -m pytest tests/ -v` with `PYTHONPATH=scripts`.

---

## Self-review

- Issue line present (`**Issue:** #552`) under the title.
- Every requirement maps to a task: Req 1/2/3 → Task 2 Step 5; Req 4 → Task 3 Steps 1-2;
  Req 5 → T1-T5 rows/tests (Tasks 1-2); Req 6 → T5 + `crlf_body` parity case; Req 7 →
  Task 2 Step 7 + T3; Req 8 → Task 1 + T4.
- Every red/green count above was measured on a scratch copy of this branch's `HEAD`
  (module unchanged from `origin/main` e707c1f): Task 1 red `2 failed, 22 passed` → green
  `103 passed`; Task 2 red `19 failed, 103 passed` → split-only `9 failed, 113 passed` →
  green `122 passed`. A bare `diff.split("\n")` substituted into the final module fails only
  the T2 row, confirming it as the Req 2 guard.
- Not adopted, per spec: the `splitlines()`-vs-`split("\n")` divergence rejection; any
  `\r\n` normalisation.

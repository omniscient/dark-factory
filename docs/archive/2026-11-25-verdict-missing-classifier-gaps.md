# Fix `_VERDICT_MISSING_RE` Gaps (docs-gate overlap, unparseable status) Implementation Plan

**Issue:** #591

**Goal:** Close the two gaps the operator review of PR #590 found in
`scripts/factory_core/error_signature.py::_VERDICT_MISSING_RE`:
(1) the sibling `docs_verdict_gate_check.sh` (Gate 2.5) must not match, because its basename
contains the `verdict_gate_check.sh: ` prefix; (2) a quiet-branch `STATUS=<garbled>` line must sign
`substantive:verdict_missing` like `STATUS=missing`, while the real verdict tokens from
`verdict.py` stay excluded.

**Architecture:** This changes the classifier only. `error_signature.py` imports
`GATING_BLOCK_STATUSES | LEGACY_STATUSES` from `verdict.py`, builds an exclusion alternation from
them (`_REAL_VERDICT_TOKENS_RE`), and widens the quiet-branch alternative to
`STATUS=(?!(?:<tokens>)\))[^)\s]*\)`. A leading `\b` goes in front of `verdict_gate_check\.sh: `.
`classify()`'s control flow and check order do not change. Neither gate script's behaviour
changes. `breaker.py` and `workflows/archon-dark-factory.yaml` are not touched. The only other
edits are a comment in `scripts/verdict_gate_check.sh` and a sentence plus a re-mapped line
citation in `docs/run-lifecycle.md`.

**Tech Stack:** Python 3 `re`, pytest, bash (comment-only edit), Markdown.

**Spec:** `docs/superpowers/specs/2026-11-25-verdict-missing-classifier-gaps-design.md`

---

## File Structure

| File | Change | Spec requirement |
|---|---|---|
| `scripts/factory_core/error_signature.py` | Import `GATING_BLOCK_STATUSES` and `LEGACY_STATUSES` from `.verdict`. Add `_REAL_VERDICT_TOKENS_RE` with its comment. Widen the quiet-branch alternative of `_VERDICT_MISSING_RE` (Task 1). Add the leading `\b` (Task 2). Rewrite the comment above `_VERDICT_MISSING_RE` | 1, 3, 4, 7 |
| `tests/test_factory_core_error_signature.py` | Extend `test_verdict_missing_regex_matches_gate_script_quiet_branch_wording`. Add unparseable-status, real-token, path-prefix and docs-gate exclusion tests | 2, 5 |
| `scripts/verdict_gate_check.sh` | Comment only, lines 68-71: "when it prints STATUS=missing" becomes "when its STATUS is missing or unparseable (#591)". The `error_signature.py` reference stays within the 4 lines above the silent-death echo. No executable line changes | 6 (comment follow-up) |
| `docs/run-lifecycle.md` | Failed-run step 4 (lines 1118-1122): cover the unparseable quiet-branch status. Re-map `error_signature.py:55-89` to `classify()`'s post-edit range (`:70-104` per Step 2.3's grep) | Architecture follow-up |

Do **not** edit `scripts/docs_verdict_gate_check.sh`, `scripts/factory_core/verdict.py`,
`breaker.py`, `workflows/archon-dark-factory.yaml`, `tests/test_verdict_gate_check.sh`, or
`tests/test_docs_verdict_gate_check.py` (Requirement 6). Do **not** edit the untracked
`dark-factory/scripts/` mirror in the clone: it is a runtime TARGET-PATH copy, not the source.

Environment notes for the implementer:
- If `python3 -m pytest` reports `No module named pytest`, run `pip install --user pytest pyyaml aiohttp`
  first. That is the same set CI installs.
- Every test command below uses `PYTHONPATH=scripts`, matching CI.
- Test-file hygiene, carried over from #373: the existing tests build gate lines at runtime
  (`_GATE_PREFIX = "verdict_gate_check.sh" + ": "`). That way the test file never contains a
  matching line contiguously, and a run that `cat`s it does not sign itself `verdict_missing`.
  The new tests below follow the same rule. Never write the literal
  `verdict_gate_check.sh: … blocks issue #<digits> (STATUS=…)` contiguously in a `.py`
  or `.sh` source file.

---

## Task 1: Gap 2, matching an unparseable quiet-branch STATUS (Requirements 3, 4, 5)

**Files:**
- Modify: `tests/test_factory_core_error_signature.py`
- Modify: `scripts/factory_core/error_signature.py`

- [ ] **Step 1.1: Extend the quiet-branch coupling test (render the real echo).**
  In `tests/test_factory_core_error_signature.py`, inside
  `test_verdict_missing_regex_matches_gate_script_quiet_branch_wording`, replace the final two lines:

  ```python
      blocked_matches = bool(_VERDICT_MISSING_RE.search(render("BLOCKED")))
      assert not blocked_matches, "a STATUS=BLOCKED quiet-branch line must not match"
  ```

  with:

  ```python
      for status in ("PAS", "BLOCKEDX", "blocked"):  # unparseable: same signature (#591)
          unparseable_matches = bool(_VERDICT_MISSING_RE.search(render(status)))
          assert unparseable_matches, f"quiet-branch STATUS={status} must match as unparseable"
      for status in ("BLOCKED", "HUMAN_REQUIRED", "FAIL"):  # real verdicts
          real_matches = bool(_VERDICT_MISSING_RE.search(render(status)))
          assert not real_matches, f"a STATUS={status} quiet-branch line must not match"
      path_matches = bool(_VERDICT_MISSING_RE.search("scripts/" + render("missing")))
      assert path_matches, "a path-prefixed quiet-branch line must still match (#591)"
  ```

  The path-prefixed assertion already passes today. It ties Requirement 5's path case to the
  real echo and protects it once Task 2 adds the leading `\b`.

- [ ] **Step 1.2: Append the new Gap 2 tests** to the end of
  `tests/test_factory_core_error_signature.py`:

  ```python


  # --- #591: unparseable quiet-branch status, real-verdict tokens, path prefix ----------


  def test_verdict_missing_matches_quiet_branch_unparseable_status():
      # A garbled non-empty STATUS is one case with "missing" (same signature, so an
      # absent/corrupted alternation still trips the breaker's consecutive-match check).
      # Lowercase "blocked" is not a real verdict to the gate's case-sensitive test either.
      for status in ("PAS", "BLOCKEDX", "blocked", "garbled123"):
          sig = _classify(text=_BENIGN_PREVIEW + _QUIET_HEAD + status + _QUIET_TAIL + "\n")
          assert sig == "substantive:verdict_missing", status


  def test_verdict_missing_ignores_every_real_verdict_token_from_verdict_py():
      # The exclusion is derived from verdict.py, so every token in those two sets (not a
      # hand-typed copy) must stay out of the quiet-branch match.
      from factory_core.verdict import GATING_BLOCK_STATUSES, LEGACY_STATUSES
      assert {"BLOCKED", "HUMAN_REQUIRED", "FAIL"} <= GATING_BLOCK_STATUSES | LEGACY_STATUSES
      for status in sorted(GATING_BLOCK_STATUSES | LEGACY_STATUSES):
          sig = _classify(text=_BENIGN_PREVIEW + _QUIET_HEAD + status + _QUIET_TAIL + "\n")
          assert sig == "environmental:preview_infra", status
  ```

  (`_QUIET_HEAD`, `_QUIET_TAIL`, `_BENIGN_PREVIEW` and `_classify` already exist in this
  file. The second test expects `environmental:preview_infra` because the preview line
  that follows is the next classifier in the chain. This matches the existing
  `test_verdict_missing_ignores_quiet_branch_status_blocked`.)

- [ ] **Step 1.3: Run the tests and confirm they fail.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_core_error_signature.py -q
  ```

  Expected: `2 failed, 32 passed`. The failures are
  `test_verdict_missing_regex_matches_gate_script_quiet_branch_wording` (message
  `quiet-branch STATUS=PAS must match as unparseable`) and
  `test_verdict_missing_matches_quiet_branch_unparseable_status`. All other tests pass,
  including `test_verdict_missing_ignores_every_real_verdict_token_from_verdict_py`, which
  is already true today.

- [ ] **Step 1.4: Implement the widened quiet-branch alternative.** In
  `scripts/factory_core/error_signature.py`:

  (a) Below the existing `from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE` line
  (line 10), add the import. `verdict.py` has no imports of its own, so this cannot create a cycle.

  ```python
  from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE
  from .verdict import GATING_BLOCK_STATUSES, LEGACY_STATUSES
  ```

  (b) Replace the whole block from the comment line `# Matches the two stderr lines
  scripts/verdict_gate_check.sh prints when a gate phase` through the closing `)` of
  `_VERDICT_MISSING_RE` (lines 18-30 on main) with the following. Task 2 adds the `\b`, so it
  is not here yet.

  ```python
  # Real, non-missing/unparseable verdict tokens that must never be mistaken for a
  # missing one in the quiet-branch STATUS= field. Sourced from verdict.py's own
  # constants (not re-typed here) so a future addition to that enum is excluded
  # automatically. GATING_PASS_STATUSES is not included: those three statuses make
  # verdict_gate_check.sh exit 0 before the quiet-branch echo is ever reached, so they
  # can never appear in this field.
  _REAL_VERDICT_TOKENS_RE = "|".join(sorted(GATING_BLOCK_STATUSES | LEGACY_STATUSES))
  # Matches the two stderr lines scripts/verdict_gate_check.sh prints when a gate phase
  # never wrote a usable verdict (#373, #591): the "true silent death" line (verdict
  # missing/unparseable, no needs-discussion label) and the quiet-branch line when its
  # STATUS= field is missing or unparseable (needs-discussion present). A quiet-branch
  # STATUS= that is exactly one of the real verdict tokens above is a real verdict and must
  # not match; the check is case-sensitive like the gate's own comparison and anchored at
  # the closing ")", so "blocked" or "BLOCKEDX" still match. Anchored on that script's own
  # "verdict_gate_check.sh: " log prefix and a literal issue number, so a transcript that
  # merely quotes the phrase (e.g. `cat` of the script, whose source has "#${ISSUE_NUM}")
  # is not mis-signed. Wording is coupled to both echoes;
  # tests/test_factory_core_error_signature.py fails CI if either side drifts.
  # Deliberately no re.IGNORECASE: it is our own literal.
  _VERDICT_MISSING_RE = re.compile(
      r"verdict_gate_check\.sh: .{0,80}"
      r"(?:verdict missing/unparseable for issue #\d+"
      rf"|blocks issue #\d+ \(STATUS=(?!(?:{_REAL_VERDICT_TOKENS_RE})\))[^)\s]*\))"
  )
  ```

  How it works: the negative lookahead rejects only `STATUS=` followed by exactly
  `BLOCKED`, `FAIL` or `HUMAN_REQUIRED` and then `)`. `sorted()` fixes the order, which has no
  meaning. `[^)\s]*` keeps the token from spanning whitespace or a newline. So:
  - `missing` still matches.
  - `PAS`, `BLOCKEDX`, `blocked` and `garbled123` now match.
  - `BLOCKED`, `HUMAN_REQUIRED` and `FAIL` do not match.

  `classify()` is unchanged. Its `_VERDICT_MISSING_RE.search(text)` check stays between
  `_RATE_LIMIT_RE` and `_PREVIEW_INFRA_RE` (Requirement 7).

- [ ] **Step 1.5: Run the tests and confirm they pass.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_core_error_signature.py -q
  ```

  Expected: `34 passed`.

- [ ] **Step 1.6: Commit.**

  ```bash
  git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py
  git commit -m "fix(#591): sign an unparseable quiet-branch STATUS as verdict_missing

  Widen _VERDICT_MISSING_RE's quiet-branch alternative from the literal
  STATUS=missing to any token except the real verdicts, derived from
  verdict.py's GATING_BLOCK_STATUSES | LEGACY_STATUSES. The match is
  case-sensitive and anchored at ')'. Same signature as missing, so an
  absent/corrupted alternation still trips the breaker's stuck check."
  ```

---

## Task 2: Gap 1, excluding `docs_verdict_gate_check.sh` with a leading `\b` (Requirements 1, 2, 5, 7)

**Files:**
- Modify: `tests/test_factory_core_error_signature.py`
- Modify: `scripts/factory_core/error_signature.py`

- [ ] **Step 2.1: Append the path-prefix and docs-gate tests** to the end of
  `tests/test_factory_core_error_signature.py`. They read the real
  `scripts/docs_verdict_gate_check.sh` echo lines at test time, the same way #373's tests read
  `verdict_gate_check.sh`. The docs-gate label is the one the workflow passes
  (`workflows/archon-dark-factory.yaml`, `"Docs Conformance (Gate 2.5)"`).

  ```python


  def test_verdict_missing_matches_path_prefixed_gate_lines():
      # "/" is not a word character, so the leading \b still matches after a path.
      for line in ("scripts/" + _QUIET_MISSING_LINE, "dark-factory/scripts/" + _GATE_LINE):
          sig = _classify(text=line + "\n")
          assert sig == "substantive:verdict_missing", line


  _DOCS_GATE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docs_verdict_gate_check.sh"


  def _docs_gate_echo(marker):
      # The one docs_verdict_gate_check.sh echo containing `marker`, unquoted.
      import re
      hits = [line.strip() for line in _DOCS_GATE_SCRIPT.read_text(encoding="utf-8").splitlines()
              if line.strip().startswith("echo ") and marker in line]
      assert len(hits) == 1, f"expected one {marker!r} echo in {_DOCS_GATE_SCRIPT}, got {len(hits)}"
      m = re.match(r'echo "(.*)" >&2$', hits[0])
      assert m, hits[0]
      return m.group(1)


  def _render_docs_echo(template, why, decision="block"):
      return (template
              .replace("${GATE_LABEL}", "Docs Conformance (Gate 2.5)")
              .replace("${ISSUE_NUM}", "197")
              .replace("${DECISION}", decision)
              .replace("${_WHY}", why))


  def test_verdict_missing_regex_excludes_docs_gate_enforce_lines():
      # docs_verdict_gate_check.sh embeds "verdict_gate_check.sh: " in its own prefix. Its
      # enforce-mode lines must not sign substantive:verdict_missing (#591; #536 decides).
      from factory_core.error_signature import _VERDICT_MISSING_RE
      for marker in ("upstream already communicated", "failing closed and posting failure comment"):
          template = _docs_gate_echo(marker)
          assert "${_WHY}" in template, f"docs-gate {marker!r} echo no longer prints _WHY"
          for why in ("STATUS=missing", "STATUS=PAS"):
              rendered = _render_docs_echo(template, why)
              assert not _VERDICT_MISSING_RE.search(rendered), rendered
              assert _classify(text=rendered) != "substantive:verdict_missing", rendered
              # Not vacuous: without the docs_ prefix the same line would match, so the
              # leading \b is what keeps it out.
              bare = rendered.replace("docs_verdict_gate_check.sh", "verdict_gate_check.sh", 1)
              assert _VERDICT_MISSING_RE.search(bare), bare


  def test_verdict_missing_regex_excludes_docs_gate_observe_line():
      # Today's shipped wording ("enforce would block") must stay out for both reasons.
      from factory_core.error_signature import _VERDICT_MISSING_RE
      rendered = _render_docs_echo(_docs_gate_echo("observe mode"), "STATUS=missing")
      assert "would block issue #197" in rendered, rendered
      assert not _VERDICT_MISSING_RE.search(rendered), rendered
      assert _classify(text=rendered) != "substantive:verdict_missing", rendered


  def test_verdict_missing_regex_excludes_docs_gate_tripwire_line():
      # The head/index tripwire takes the silent-death path in every mode, observe included.
      from factory_core.error_signature import _VERDICT_MISSING_RE
      template = _docs_gate_echo("failing closed and posting failure comment")
      for why in (
          "STATUS=missing; the branch changed during the read-only docs-conformance node: HEAD moved",
          "STATUS=PAS",
      ):
          rendered = _render_docs_echo(template, why)
          assert not _VERDICT_MISSING_RE.search(rendered), rendered
          assert _classify(text=rendered) != "substantive:verdict_missing", rendered
  ```

  Coverage against spec Requirement 2:
  - Enforce-mode quiet branch and silent death with `STATUS=missing`, plus `STATUS=PAS` for
    both: `..._enforce_lines`.
  - Observe-mode `would block` with `STATUS=missing`: `..._observe_line`.
  - Head/index tripwire `_WHY` and `STATUS=PAS` under the silent-death echo: `..._tripwire_line`.

  The tripwire `STATUS=missing; …` form is also kept out by `[^)\s]*`, because the token
  stops at the space and never reaches `)`. The `STATUS=PAS` form is kept out only by `\b`. The
  `bare` assertions in `..._enforce_lines` show that removing the `docs_` prefix makes the
  same lines match, so the tests are not passing trivially.
  `docs_verdict_gate_check.sh` is excluded on purpose, not coupled to the regex, so it gets no
  cross-reference comment test.

- [ ] **Step 2.2: Run the tests and confirm they fail.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_core_error_signature.py -q
  ```

  Expected: `2 failed, 36 passed`. The failures are
  `test_verdict_missing_regex_excludes_docs_gate_enforce_lines` (the docs `STATUS=missing`
  lines match today) and `test_verdict_missing_regex_excludes_docs_gate_tripwire_line`
  (`STATUS=PAS` matches after Task 1's widening). `..._observe_line` and
  `..._path_prefixed_gate_lines` already pass.

- [ ] **Step 2.3: Add the leading `\b` and the comment explaining it.** In
  `scripts/factory_core/error_signature.py`:

  (a) Change the first pattern line of `_VERDICT_MISSING_RE` from

  ```python
      r"verdict_gate_check\.sh: .{0,80}"
  ```

  to

  ```python
      r"\bverdict_gate_check\.sh: .{0,80}"
  ```

  The `.{0,80}` gap and everything after it stay unchanged (Requirement 7).

  (b) In the comment above `_VERDICT_MISSING_RE`, replace these three lines (the last three
  comment lines written in Step 1.4):

  ```python
  # is not mis-signed. Wording is coupled to both echoes;
  # tests/test_factory_core_error_signature.py fails CI if either side drifts.
  # Deliberately no re.IGNORECASE: it is our own literal.
  ```

  with:

  ```python
  # is not mis-signed. The leading \b deliberately excludes the sibling
  # docs_verdict_gate_check.sh (Gate 2.5), whose basename embeds the same prefix: "_" is a
  # word character, so no boundary precedes the embedded "verdict_". Whether a docs-gate
  # verdict miss gets a signature is #536's decision, not an oversight to fix here. Wording
  # is coupled to both echoes; tests/test_factory_core_error_signature.py fails CI if
  # either side drifts. Deliberately no re.IGNORECASE: it is our own literal.
  ```

  After this step, lines 19-45 of the file (from `_REAL_VERDICT_TOKENS_RE`'s comment to the
  closing `)` of `_VERDICT_MISSING_RE`) read:

  ```python
  # Real, non-missing/unparseable verdict tokens that must never be mistaken for a
  # missing one in the quiet-branch STATUS= field. Sourced from verdict.py's own
  # constants (not re-typed here) so a future addition to that enum is excluded
  # automatically. GATING_PASS_STATUSES is not included: those three statuses make
  # verdict_gate_check.sh exit 0 before the quiet-branch echo is ever reached, so they
  # can never appear in this field.
  _REAL_VERDICT_TOKENS_RE = "|".join(sorted(GATING_BLOCK_STATUSES | LEGACY_STATUSES))
  # Matches the two stderr lines scripts/verdict_gate_check.sh prints when a gate phase
  # never wrote a usable verdict (#373, #591): the "true silent death" line (verdict
  # missing/unparseable, no needs-discussion label) and the quiet-branch line when its
  # STATUS= field is missing or unparseable (needs-discussion present). A quiet-branch
  # STATUS= that is exactly one of the real verdict tokens above is a real verdict and must
  # not match; the check is case-sensitive like the gate's own comparison and anchored at
  # the closing ")", so "blocked" or "BLOCKEDX" still match. Anchored on that script's own
  # "verdict_gate_check.sh: " log prefix and a literal issue number, so a transcript that
  # merely quotes the phrase (e.g. `cat` of the script, whose source has "#${ISSUE_NUM}")
  # is not mis-signed. The leading \b deliberately excludes the sibling
  # docs_verdict_gate_check.sh (Gate 2.5), whose basename embeds the same prefix: "_" is a
  # word character, so no boundary precedes the embedded "verdict_". Whether a docs-gate
  # verdict miss gets a signature is #536's decision, not an oversight to fix here. Wording
  # is coupled to both echoes; tests/test_factory_core_error_signature.py fails CI if
  # either side drifts. Deliberately no re.IGNORECASE: it is our own literal.
  _VERDICT_MISSING_RE = re.compile(
      r"\bverdict_gate_check\.sh: .{0,80}"
      r"(?:verdict missing/unparseable for issue #\d+"
      rf"|blocks issue #\d+ \(STATUS=(?!(?:{_REAL_VERDICT_TOKENS_RE})\))[^)\s]*\))"
  )
  ```

  `def classify(` then sits at line 70
  and its last line, `return f"substantive:unknown:{exit_code}"`, at line 104. Task 3's doc
  citation depends on those numbers. Confirm them with
  `grep -n '^def classify\|substantive:unknown:{exit_code}' scripts/factory_core/error_signature.py`,
  which should print `70:` and `104:`. If the numbers differ, use whatever grep reports in Task 3.

- [ ] **Step 2.4: Run the tests and confirm they pass. Also confirm no source file matches the regex.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_core_error_signature.py -q
  PYTHONPATH=scripts python3 -c "
  from factory_core.error_signature import _VERDICT_MISSING_RE as R
  for f in ['tests/test_factory_core_error_signature.py', 'scripts/factory_core/error_signature.py',
            'scripts/verdict_gate_check.sh', 'scripts/docs_verdict_gate_check.sh']:
      print(f, R.search(open(f).read()))
  "
  ```

  Expected: `38 passed`, then four lines each ending in `None`. A `cat` of any of these files
  must not sign the run `verdict_missing`.

- [ ] **Step 2.5: Commit.**

  ```bash
  git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py
  git commit -m "fix(#591): exclude docs_verdict_gate_check.sh from _VERDICT_MISSING_RE

  Anchor the prefix with a leading \b: '_' is a word character, so the
  docs_ basename no longer matches, while bare and path-prefixed
  verdict_gate_check.sh lines still do. The docs gate's enforce-mode
  lines would otherwise be signed substantive:verdict_missing once
  docs_conformance.mode flips to enforce. That decision is #536's.
  Negative tests render the real docs-gate echoes."
  ```

---

## Task 3: Comment and documentation follow-ups (Requirement 6, Architecture follow-ups)

**Files:**
- Modify: `scripts/verdict_gate_check.sh` (comment only)
- Modify: `docs/run-lifecycle.md`

- [ ] **Step 3.1: Update the gate-script comment.** In `scripts/verdict_gate_check.sh`, replace
  lines 70-71:

  ```bash
  # exact stderr line, and from the quiet-branch echo above when it prints STATUS=missing.
  # Rewording either one fails tests/test_factory_core_error_signature.py.
  ```

  with:

  ```bash
  # exact stderr line, and from the quiet-branch echo above when its STATUS is missing or
  # unparseable (#591). Rewording either one fails tests/test_factory_core_error_signature.py.
  ```

  Lines 68-69 stay unchanged, including `error_signature.py` on line 68. The comment is still
  4 lines directly above the silent-death `echo`, so
  `test_gate_script_silent_death_echo_carries_classifier_cross_reference` (window
  `lines[i-4:i]`) keeps passing. No executable line changes.

- [ ] **Step 3.2: Update `docs/run-lifecycle.md` failed-run step 4.** Replace lines 1118-1122:

  ```markdown
     `environmental:rate_limit`, `substantive:verdict_missing` (either of the two
     missing-verdict stderr lines of `scripts/verdict_gate_check.sh`, ranked above the
     preview noise every self-target run leaves, #373), `environmental:preview_infra`, and
     `substantive:oos_files|build_failure|test_failure|unknown:<exit code>`
     (`entrypoint.sh:418-477`, `scripts/factory_core/error_signature.py:55-89`).
  ```

  with:

  ```markdown
     `environmental:rate_limit`, `substantive:verdict_missing` (either of the two
     missing-verdict stderr lines of `scripts/verdict_gate_check.sh`, the quiet-branch one
     whenever its status is missing or unparseable rather than a real verdict token, #591;
     ranked above the preview noise every self-target run leaves, #373),
     `environmental:preview_infra`, and
     `substantive:oos_files|build_failure|test_failure|unknown:<exit code>`
     (`entrypoint.sh:418-477`, `scripts/factory_core/error_signature.py:70-104`).
  ```

  `70-104` is `classify()`'s range after Task 2, from Step 2.3's grep. Use the grep's numbers
  if they differ. `entrypoint.sh:418-477` is unchanged because this ticket does not touch
  `entrypoint.sh`.

- [ ] **Step 3.3: Verify the comment test, CI's docs reference check, and the gate script's own test.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_core_error_signature.py -q
  python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  bash tests/test_verdict_gate_check.sh 2>&1 | tail -2
  ```

  Expected:
  - `38 passed`
  - `Docs reference check passed for 4 doc(s).`
  - The test script ends in `PASS`. It must pass without modification, since the edit is comment-only.

- [ ] **Step 3.4: Commit.**

  ```bash
  git add scripts/verdict_gate_check.sh docs/run-lifecycle.md
  git commit -m "docs(#591): describe the unparseable-status match; re-map classify() citation

  Comment-only update to verdict_gate_check.sh's classifier cross-reference
  (no executable change), and run-lifecycle.md failed-run step 4 now
  covers an unparseable quiet-branch status and cites
  error_signature.py:70-104."
  ```

---

## Task 4: Full verification and scope check (Requirements 6, 7)

**Files:** none modified.

- [ ] **Step 4.1: Run the full pytest suite, as CI does.**

  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/ -q 2>&1 | tail -3
  ```

  Expected: `tests/test_factory_core_error_signature.py` (38 tests) and
  `tests/test_docs_verdict_gate_check.py` pass unmodified.
  - If anything else fails, re-run it on `origin/main`
    (`git worktree add /tmp/df-main origin/main`, then run the same test there).
  - A failure that also occurs on main is pre-existing. Record it in the implementation
    report and do not fix it here (scope discipline).
  - A failure that occurs only on this branch is a regression. Fix it before continuing.
  - Known example from the prototype: `tests/test_hybrid_execution_contract_doc.py::test_cited_repo_files_resolve`
    failed both with and without this change in a bare `git archive` checkout (3450 passed with
    the change, 3444 on the baseline). It does not involve the files this plan touches.

- [ ] **Step 4.2: Run the CI shell tests that touch this area, and the two DAG checks.**

  ```bash
  bash tests/test_verdict_gate_check.sh 2>&1 | tail -1
  bash tests/test_entrypoint_error_signature.sh 2>&1 | tail -1
  python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
  python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  ```

  Expected:
  - `PASS`
  - `Results: 14 passed, 0 failed`
  - `DAG trigger_rule check passed for 1 workflow file(s).`
  - `when: expression lint passed for 1 workflow file(s).`

  `test_entrypoint_error_signature.sh` sources `entrypoint.sh`, which resolves some scripts
  through image or clone paths (`/opt/dark-factory/scripts`, the `dark-factory/` mirror). It
  depends on the environment, and in a bare `git archive` checkout without those paths it fails
  7 of 14 on main too. Treat it as a regression only if it passes on `origin/main` in the same
  environment and fails here. Its C2 case, the silent-death line, matches under both the old
  and the new regex.

- [ ] **Step 4.3: Scope check. Only the four planned files changed.**

  ```bash
  git diff --name-only origin/main...HEAD -- . ':!docs/superpowers/' ':!.archon/memory/'
  ```

  Expected output, exactly these four paths in any order:

  ```
  docs/run-lifecycle.md
  scripts/factory_core/error_signature.py
  scripts/verdict_gate_check.sh
  tests/test_factory_core_error_signature.py
  ```

  Then check that the gate script's only change is in comments:

  ```bash
  git diff origin/main...HEAD -- scripts/verdict_gate_check.sh | grep '^[-+][^-+]' | grep -v '^[-+]#'
  ```

  Expected: no output. Every added or removed line is a `#` comment.

- [ ] **Step 4.4: Carry the open question forward.** Do not add any code for this. In the
  implementation report or PR body, restate the issue's standing action item: the next time a
  run fails at Gate 2 with a missing verdict, confirm that
  `error-signatures/<issue>.implement.sig` reads `substantive:verdict_missing`, and record
  the result on #591 or #373. Note also that #536 owns any decision about a docs-gate
  signature.

  Note the known asymmetry (spec Requirement 4): `HUMAN_REQUIRED`/`FAIL` without
  `needs-discussion` still sign `verdict_missing` via the silent-death line; only the
  quiet-branch line is excluded.

  Also record in the PR body: any later commit that changes
  `scripts/factory_core/error_signature.py` (including an automated `fix(review)` commit)
  must re-run
  `grep -n '^def classify\|substantive:unknown:{exit_code}' scripts/factory_core/error_signature.py`
  and re-map the `docs/run-lifecycle.md` citation, because `docs_refs_check.py` does not
  detect a shifted range (it only checks that the cited lines exist). Such a commit must
  also keep the comment above the silent-death echo within four lines of
  `error_signature.py`, keep gate lines non-contiguous in the test file, and not add
  `re.IGNORECASE`.

---

## Self-Review

- `**Issue:** #591` is directly under the title.
- No placeholders. Every code step shows the exact code. The `70-104` citation comes from the
  prototype and is re-checked by the Step 2.3 grep.
- Spec coverage:
  - R1 (`\b` exclusion): Task 2.
  - R2 (docs-gate negative tests: enforce quiet and silent-death lines, observe line,
    tripwire, `STATUS=PAS`): Step 2.1.
  - R3 (same signature for unparseable): Task 1.
  - R4 (tokens from `verdict.py`, case-sensitive, anchored at `)`): Step 1.4, plus tests for
    `blocked` and `BLOCKEDX`.
  - R5 (`PAS`, `BLOCKEDX`, `blocked` and path-prefixed lines match; `BLOCKED`,
    `HUMAN_REQUIRED` and `FAIL` don't): Steps 1.1, 1.2 and 2.1.
  - R6 (no behaviour change, gate tests unmodified): Steps 3.3, 4.2 and 4.3.
  - R7 (ranking and `.{0,80}` unchanged): `classify()` is not edited; Step 2.3(a).
  - Comment and doc follow-ups: Steps 1.4, 2.3(b), 3.1 and 3.2.
- Prototype check (run in a scratch checkout before publishing):
  - The old regex fails the Task 1 and Task 2 tests at Steps 1.3 and 2.2.
  - The final code passes all 38 tests in this file, `tests/test_docs_verdict_gate_check.py`,
    `tests/test_verdict_gate_check.sh`, `docs_refs_check.py` and both DAG checks.
  - No source file matches the regex.

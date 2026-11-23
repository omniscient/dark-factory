# `substantive:verdict_missing` Classification and Conformance Artifact Guard — Implementation Plan

**Issue:** #373

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec
`docs/superpowers/specs/2026-11-23-verdict-missing-classifier-and-conformance-artifact-guard-design.md`
(revised 2026-11-23 after the spec gate). It has two parts:

- **Part A (classifier):** `scripts/factory_core/error_signature.py::classify()` gets a new
  `_VERDICT_MISSING_RE` that returns `substantive:verdict_missing`. The check order becomes
  delivery_failure → rate_limit → **verdict_missing** → preview_infra → the substantive
  regexes → unknown. A pytest ties the regex to `scripts/verdict_gate_check.sh`'s wording.
  Only a comment is added to the gate script; its behavior does not change.
- **Part B (conformance command):** `commands/dark-factory-conformance.md` gets three
  additions. All four reviewer dispatch sites run in the foreground
  (`run_in_background: false`). The Phase 3.5 reconcile loop re-spawns the reviewer without
  touching the code on a no-response cycle. A new final **Phase 6: ARTIFACT GUARD** never
  writes a verdict itself: it re-enters the real Phase 4/5, or it leaves `conformance.md`
  absent and exits 1.

**Architecture:** In Part A, one existing `if` branch moves and one new compiled regex and
`if` branch go in. `classify()`'s control flow is otherwise unchanged. In Part B, the
command file gets additive prose and bash edits in four places. Phase 3 step 7's verdict
parsing, the shadow verdict mapping and what Phase 4/5 write are not touched. The tests are
pytest static-text checks on the command file, plus one new case in the CI-listed
`tests/test_entrypoint_error_signature.sh`.

**Tech Stack:** Python 3.12+ (CI pins 3.12), bash, pytest.

**Hard limits honored (spec Requirement 11):**

- `scripts/verdict_gate_check.sh` gets a comment and nothing else. Its block/pass decision
  is unchanged, and `tests/test_verdict_gate_check.sh` must stay green **unmodified**.
- No edits to `scripts/factory_core/breaker.py`, `workflows/archon-dark-factory.yaml`,
  `config/**`, `deploy/**` or `.github/workflows/**`.
- No edit inside the `<!-- headless-contract:begin/end -->` block of any command file.
- This is a gate-path ticket. The PR goes to Frank (a human) for the merge, so do not add
  `direct-to-pr`.

**Self-classification hazard (applies to every task).** If this ticket's own implement run
fails, its whole transcript goes through `classify()`. That includes anything the agent
`cat`s and every pytest failure dump. So **no file this plan creates or edits may contain
the gate's log line as one contiguous string**:

- The pytest fixture builds the line at runtime from `"verdict_gate_check.sh" + ": " + ...`.
- The bash fixture uses `"verdict_gate_check.sh"": ..."`; adjacent quoted strings
  concatenate when the shell runs, but not in the source.
- The gate script's own source is safe: it has `#${ISSUE_NUM}`, not digits, and the regex
  needs `#\d+`.

Keep it that way in any comment or prose you write.

---

## File Structure

| File | Change | Spec requirement |
|---|---|---|
| `scripts/factory_core/error_signature.py` | Add `_VERDICT_MISSING_RE` with a cross-reference comment. Reorder `classify()`: rate_limit moves above preview_infra and verdict_missing goes in between. Add a precedence note near `SESSION_WINDOW_PAUSE_SIGNATURE` | 1, 2, 5 |
| `scripts/verdict_gate_check.sh` | Comment-only: a cross-reference above the "true silent death" `echo` | 2 |
| `tests/test_factory_core_error_signature.py` | New tests: the #197 transcript shape, a prefix-anchoring negative, rate-limit precedence (both orders), rate-limit over preview, wording coupling to the gate script | 2, 3, 4 |
| `tests/test_entrypoint_error_signature.sh` | New section `C2`: preview-infra token + gate line → `.sig` carries `substantive:verdict_missing` | 6 (part 1) |
| `commands/dark-factory-conformance.md` | Foreground-dispatch text at 4 sites; Phase 3.5 step 4 no-response conditional; new final `## Phase 6: ARTIFACT GUARD` | 7, 8, 9 |
| `tests/test_conformance_command_artifact_guard.py` | **New.** Static-text assertions for the command edits | 10 |
| `docs/run-lifecycle.md`, `docs/features.md` | Doc map: add the new signature class and its check order; refresh the line refs this change shifts | (implement DOCUMENT step; in-scope housekeeping) |
| PR description | Requirement 6 verification result; an unverified-assumption flag | 6 (part 2) |

---

## Task 1: Check the stderr-reaches-transcript assumption first (Requirement 6, part 2)

The classifier fix only helps if the `conformance-gate` `bash:` node's stderr line lands in
`entrypoint.sh`'s `TMP_OUT` (the `tee` of `archon workflow run ... 2>&1`). This task runs
first so that a contradiction stops the run before any code is written.

**Files:** none (read-only investigation). The result goes into
`$ARTIFACTS_DIR/implementation.md` and, later, the PR description.

- [ ] **Step 1: Confirm that `STATUS=missing` comes only from the gate's stderr.**

  ```bash
  grep -rn 'STATUS=\${STATUS:-missing}' scripts/
  grep -rln 'STATUS=missing' scripts/ entrypoint.sh workflows/ commands/ | grep -vE '^scripts/(docs_)?verdict_gate_check\.sh$' || echo "no other emitter"
  ```

  Expected: the first command prints **two** hits, one per `bash:` verdict-gate script:
  - `scripts/docs_verdict_gate_check.sh` (`_WHY="STATUS=${STATUS:-missing}"`, which that
    script writes to stderr);
  - `scripts/verdict_gate_check.sh` (the quiet-branch `echo ... >&2`).

  The second command prints `no other emitter`. Its `grep -vE` drops both gate scripts.
  Why this matters: the issue body (#197) and the MarketHawk #439 comment both quote
  `STATUS=missing` from captured container logs. The container log is `entrypoint.sh`'s
  stdout, the `tee` of the Archon output. That text is emitted only by the two `bash:`
  verdict-gate scripts' stderr, so a `bash:` gate node's stderr reached that stream. In both
  quoted cases it was `conformance-gate`'s quiet branch. This is indirect evidence.

- [ ] **Step 2: Look for a captured log of a real failed run that you can read from
  inside the container.**

  ```bash
  grep -rlE '^verdict_gate_check\.sh: ' /var/lib/dark-factory /home/factory/.archon/workspaces/*/*/logs 2>/dev/null | head -5
  ```

  Note: the run records in `/var/lib/dark-factory/run-records/*.json` hold parsed node
  events, not the raw transcript. The Archon `logs/*.jsonl` files hold per-container node
  events only. A hit here is unlikely, but check anyway.

- [ ] **Step 3: Decide and record.** Write one of these three outcomes to
  `$ARTIFACTS_DIR/implementation.md` under a `## Requirement 6 verification` heading. Copy it
  into the PR description in Task 11.
  - **FOUND:** a captured failed-run log has a line that begins `verdict_gate_check.sh: `.
    Record the path and proceed.
  - **NOT AVAILABLE FROM THE RUN CONTAINER:** there are no hits, and no captured
    container log of a failed run is reachable. Record "not directly verified from inside
    the run container". Cite Step 1's indirect evidence (the gate's quiet-branch stderr
    text `STATUS=missing` is quoted from container logs in #373's body and in the
    2026-09-16 #439 comment). Proceed, but carry it into the PR description as an
    **Unverified assumption** for the human merger. The operator's 2026-11-23 spec-gate
    amendment explicitly keeps this as a flagged assumption.
  - **CONTRADICTED:** a captured log of a run whose `conformance-gate` failed has **no**
    `verdict_gate_check.sh:` line. **Stop.** Do not do Tasks 2–10. Post an issue comment
    that quotes the evidence and says the classifier cannot fire until the gate node
    surfaces its message, which is a gate change that needs its own ticket. Add
    `needs-discussion`
    (`python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id 373 --add needs-discussion`).
    End the phase per the implement command's blocked path.

---

## Task 2: Classifier — failing tests, then the regex and reorder (Requirements 1, 3, 4, 5, 6 part 1)

**Files:**
- `tests/test_factory_core_error_signature.py` (append)
- `tests/test_entrypoint_error_signature.sh` (insert section `C2`)
- `scripts/factory_core/error_signature.py`

- [ ] **Step 1: Append the failing unit tests** to the end of
  `tests/test_factory_core_error_signature.py`:

  ```python


  # --- #373: substantive:verdict_missing ---------------------------------------------
  # The gate line is assembled at runtime so this source file never contains it
  # contiguously: a run that prints this file (cat, a pytest failure dump) must not
  # itself classify as verdict_missing.
  _GATE_PREFIX = "verdict_gate_check.sh" + ": "
  _GATE_LINE = (
      _GATE_PREFIX + "Conformance (Gate 2) verdict missing/unparseable for issue #197 and no "
      "needs-discussion label — true silent death, posting failure comment."
  )
  # Degraded-preview noise every self-target implement run leaves early in its transcript.
  # "docker compose" is a real _PREVIEW_INFRA_RE alternative (the human-facing sentence
  # "Preview environment failed to start" is not).
  _BENIGN_PREVIEW = "preview: docker compose up failed — continuing in degraded mode\n"
  # Same string test_rate_limit() above already proves classifies as rate_limit.
  _RATE_LIMIT_TEXT = "Error: you have hit your usage limit for this session"


  def test_verdict_missing_fixture_preview_text_alone_is_preview_infra():
      # Proves the #197 fixture below really exercises the ordering.
      assert _classify(text=_BENIGN_PREVIEW) == "environmental:preview_infra"


  def test_verdict_missing_outranks_earlier_preview_infra_noise():
      # #197 shape: non-fatal preview warning early, the gate's silent-death line last.
      text = _BENIGN_PREVIEW + "implement: all 7 plan tasks complete, pushed\n" + _GATE_LINE + "\n"
      sig = _classify(text=text)  # bind first: keep the fixture text out of a failure dump
      assert sig == "substantive:verdict_missing"


  def test_verdict_missing_requires_gate_script_prefix():
      bare = "Conformance (Gate 2) verdict missing/unparseable for issue #197 and no needs-discussion label"
      assert _classify(text=bare) != "substantive:verdict_missing"
      # The gate script's own source line (as `cat` would print it) has no digits after '#'.
      source = 'echo "verdict_gate_check.sh: ${GATE_LABEL} verdict missing/unparseable for issue #${ISSUE_NUM} and"'
      assert _classify(text=source) != "substantive:verdict_missing"


  def test_rate_limit_outranks_verdict_missing_in_either_order():
      # Operator decision (2026-11-23): a rate-limit death that ends in a missing verdict
      # stays environmental and never counts toward the breaker's substantive trip.
      for text in (_RATE_LIMIT_TEXT + "\n" + _GATE_LINE, _GATE_LINE + "\n" + _RATE_LIMIT_TEXT):
          sig = _classify(text=text)
          assert sig == "environmental:rate_limit"


  def test_rate_limit_outranks_preview_infra():
      # Pins the one intentional reordering of the two pre-existing environmental checks.
      assert _classify(text=_BENIGN_PREVIEW + _RATE_LIMIT_TEXT) == "environmental:rate_limit"
  ```

- [ ] **Step 2: Insert the failing entrypoint case.** In
  `tests/test_entrypoint_error_signature.sh`, insert this block right after section C's
  closing line `rm -f "${ARTIFACTS_DIR}/implementation.md"` and before
  `echo "--- D: pre-existing workflow context files ...`. At that point `TMP_OUT` still
  exists; it is removed after section E:

  ```bash

  echo ""
  echo "--- C2: a missing conformance verdict outranks earlier preview-infra noise (#373) ---"
  # #197 shape: degraded-preview toolchain output early, the gate's silent-death line last.
  # The gate line is split across two adjacent quoted strings so this file never contains
  # it contiguously (a run that prints this test must not itself sign verdict_missing).
  rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir2b-XXXXXX)
  echo "placeholder" > "${ARTIFACTS_DIR}/implementation.md"
  {
    echo "preview: docker compose up failed — continuing in degraded mode"
    echo "verdict_gate_check.sh"": Conformance (Gate 2) verdict missing/unparseable for issue #33 and no needs-discussion label — true silent death, posting failure comment."
  } > "$TMP_OUT"
  _write_error_signature "implement" 1 "$TMP_OUT"
  SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
  assert_true "classified substantive:verdict_missing" \
    "grep -q 'substantive:verdict_missing' '$SIG_FILE'"
  rm -f "${ARTIFACTS_DIR}/implementation.md"
  ```

- [ ] **Step 3: Run both and verify they fail.** If `python -m pytest` reports
  `No module named pytest`, first run `pip install pytest pyyaml`, the same as CI.

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v -k "verdict_missing or outranks"
  bash tests/test_entrypoint_error_signature.sh
  ```

  Expected pytest result: `test_verdict_missing_outranks_earlier_preview_infra_noise` FAILS
  (`'environmental:preview_infra' == 'substantive:verdict_missing'`) and
  `test_rate_limit_outranks_preview_infra` FAILS (`environmental:preview_infra`). The other
  three pass: the fixture sanity check, the negative check and the rate-limit-vs-gate check,
  which already holds today. Expected bash result: `FAIL: classified
  substantive:verdict_missing`, then `Results: N passed, 1 failed`, with a non-zero exit.

- [ ] **Step 4: Implement the regex.** In `scripts/factory_core/error_signature.py`, insert
  this right after the `_PREVIEW_INFRA_RE = re.compile(...)` block (after its closing `)`)
  and before `_OOS_FILES_RE`:

  ```python
  # Matches the stderr line scripts/verdict_gate_check.sh prints on its "true silent death"
  # path (verdict file missing/unparseable, no needs-discussion label) — the run-ending
  # cause when a gate phase never wrote its verdict (#373). Anchored on that script's own
  # "verdict_gate_check.sh: " log prefix and a literal issue number, so a transcript that
  # merely quotes the phrase (e.g. `cat` of the script, whose source has "#${ISSUE_NUM}")
  # is not mis-signed. Wording is coupled to that echo; tests/test_factory_core_error_signature.py
  # fails CI if either side drifts. Deliberately no re.IGNORECASE: it is our own literal.
  _VERDICT_MISSING_RE = re.compile(
      r"verdict_gate_check\.sh: .{0,80}verdict missing/unparseable for issue #\d+"
  )
  ```

- [ ] **Step 5: Reorder `classify()`.** Replace:

  ```python
      if _PREVIEW_INFRA_RE.search(text):
          return "environmental:preview_infra"
      if _RATE_LIMIT_RE.search(text):
          return "environmental:rate_limit"
      if _OOS_FILES_RE.search(text):
  ```

  with:

  ```python
      if _RATE_LIMIT_RE.search(text):
          return "environmental:rate_limit"
      # Above _PREVIEW_INFRA_RE: the degraded-preview path every self-target run takes leaves
      # non-fatal toolchain output (docker compose, buildkit) early in the transcript, which
      # used to shadow the run-ending missing verdict (#197). Below _RATE_LIMIT_RE: a
      # rate-limit death that ends in a missing verdict stays environmental and never counts
      # toward the breaker's two-consecutive-substantive trip (operator decision, #373).
      if _VERDICT_MISSING_RE.search(text):
          return "substantive:verdict_missing"
      if _PREVIEW_INFRA_RE.search(text):
          return "environmental:preview_infra"
      if _OOS_FILES_RE.search(text):
  ```

- [ ] **Step 6: Add the Requirement 5 precedence note.** Insert this directly after the line
  `SESSION_WINDOW_PAUSE_SIGNATURE = "environmental:session_window_pause"`:

  ```python
  # Precedence note (#373): _VERDICT_MISSING_RE's place in classify() cannot race this path.
  # entrypoint.sh's _handle_session_window_pause() runs before classify() is ever invoked
  # and, on a genuine match, writes this signature directly and returns — a real
  # session-window pause never reaches classify()'s regex chain. There is no ordering
  # conflict between the two paths to "fix".
  ```

- [ ] **Step 7: Run and verify that everything passes.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v
  bash tests/test_entrypoint_error_signature.sh
  ```

  Expected: every test in the pytest file passes, including the pre-existing
  `test_preview_infra_checked_before_build_failure` and `test_rate_limit`. The bash run
  prints `PASS: classified substantive:verdict_missing`, then `Results: N passed, 0 failed`,
  and exits 0.

- [ ] **Step 8: Commit.**

  ```bash
  git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py tests/test_entrypoint_error_signature.sh
  git commit -m "fix(gates): classify a missing gate verdict as substantive:verdict_missing above preview_infra (#373)"
  ```

---

## Task 3: Tie the regex to the gate script's wording and add the cross-reference comment (Requirement 2)

**Files:**
- `tests/test_factory_core_error_signature.py` (append)
- `scripts/verdict_gate_check.sh` (comment only)

- [ ] **Step 1: Append the failing coupling tests** to
  `tests/test_factory_core_error_signature.py`:

  ```python


  _GATE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verdict_gate_check.sh"


  def _silent_death_echo():
      lines = _GATE_SCRIPT.read_text(encoding="utf-8").splitlines()
      hits = [i for i, line in enumerate(lines)
              if line.startswith("echo ") and "true silent death" in line]
      assert len(hits) == 1, f"expected one silent-death echo in {_GATE_SCRIPT}, got {hits}"
      return lines, hits[0]


  def test_verdict_missing_regex_matches_gate_script_wording():
      # Rewording either verdict_gate_check.sh's echo or _VERDICT_MISSING_RE fails here
      # instead of silently breaking the classification (#373).
      import re
      from factory_core.error_signature import _VERDICT_MISSING_RE
      lines, i = _silent_death_echo()
      m = re.match(r'echo "(.*)" >&2$', lines[i])
      assert m, lines[i]
      rendered = (m.group(1)
                  .replace("${GATE_LABEL}", "Conformance (Gate 2)")
                  .replace("${ISSUE_NUM}", "197"))
      assert _VERDICT_MISSING_RE.search(rendered), rendered  # printed only when it does NOT match
      sig = _classify(text=rendered)
      assert sig == "substantive:verdict_missing"


  def test_gate_script_silent_death_echo_carries_classifier_cross_reference():
      lines, i = _silent_death_echo()
      assert "error_signature.py" in "\n".join(lines[max(0, i - 4):i])
  ```

- [ ] **Step 2: Run and verify.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v -k "gate_script"
  ```

  Expected: `test_verdict_missing_regex_matches_gate_script_wording` PASSES, because Task 2
  already added the regex. `test_gate_script_silent_death_echo_carries_classifier_cross_reference`
  FAILS (`assert 'error_signature.py' in ...`).

- [ ] **Step 3: Add the comment** in `scripts/verdict_gate_check.sh`, immediately above the
  line that begins `echo "verdict_gate_check.sh: ${GATE_LABEL} verdict missing/unparseable`
  (today line 68, the line after the `fi` that closes the quiet branch):

  ```bash
  # Wording coupled to scripts/factory_core/error_signature.py's _VERDICT_MISSING_RE (#373):
  # entrypoint.sh's failure classifier signs the run substantive:verdict_missing from this
  # exact stderr line. Rewording it fails tests/test_factory_core_error_signature.py.
  ```

  Add nothing else: no other line in the file changes.

- [ ] **Step 4: Run and verify that everything passes, including the gate script's own unmodified test.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v
  bash tests/test_verdict_gate_check.sh
  git diff --stat -- tests/test_verdict_gate_check.sh   # expected: empty
  git diff -U0 -- scripts/verdict_gate_check.sh | grep '^[+-][^+-]' | grep -v '^+#'   # expected: empty (comment-only)
  ```

  Expected: all pytest tests pass; `test_verdict_gate_check.sh` reports no failures and
  exits 0; both diff checks print nothing.

- [ ] **Step 5: Commit.**

  ```bash
  git add scripts/verdict_gate_check.sh tests/test_factory_core_error_signature.py
  git commit -m "test(gates): couple _VERDICT_MISSING_RE to verdict_gate_check.sh wording (#373)"
  ```

---

## Task 4: Conformance command — foreground reviewer dispatch at all four sites (Requirement 8)

**Files:**
- `tests/test_conformance_command_artifact_guard.py` (**new**)
- `commands/dark-factory-conformance.md`

- [ ] **Step 1: Create the test file with the dispatch-site and headless-block tests.**
  `tests/test_conformance_command_artifact_guard.py`:

  ```python
  """#373: static content checks on commands/dark-factory-conformance.md — foreground
  reviewer dispatch (Requirement 8), the Phase 3.5 no-response retry (Requirement 7) and the
  Phase 6 artifact guard (Requirement 9). These prove the instructions are present and
  correctly placed; they prove nothing about whether a live agent obeys them."""
  import importlib.util
  import re
  from pathlib import Path

  REPO_ROOT = Path(__file__).resolve().parents[1]
  CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


  def _text():
      return CMD.read_text(encoding="utf-8")


  def _between(text, start, end):
      i = text.index(start)
      return text[i:text.index(end, i + len(start))]


  def _flat(text):
      # Collapse whitespace so a phrase still matches after the prose is re-wrapped.
      return " ".join(text.split())


  def _step_3_1():
      return _between(_text(), "### Step 3.1", "## Phase 3.6:")


  def _phase_3_5():
      return _between(_text(), "## Phase 3.5: RECONCILE LOOP", "## Phase 4: PASS")


  # --- Requirement 8: one assertion per dispatch site, each scoped to that step's text ---

  def test_step_3_1_opus_dispatch_is_foreground():
      site = _flat(_between(_step_3_1(), "4. Spawn a conformance reviewer subagent",
                            "5. Append the subagent's output"))
      assert "run_in_background: false" in site
      assert "poll inside your turn" in site
      assert "never enter Phase 4 or Phase 5" in site


  def test_step_3_1_shadow_dispatch_is_foreground():
      site = _flat(_between(_step_3_1(), "5a. If `$SHADOW_MODEL_PIN`",
                            "6. Parse the **`## Out-of-Scope Changes`**"))
      assert "run_in_background: false" in site


  def test_reconcile_opus_respawn_is_foreground():
      site = _flat(_between(_phase_3_5(), "6. Re-spawn the conformance reviewer subagent",
                            "7. Prepend"))
      assert "run_in_background: false" in site
      assert "poll inside your turn" in site
      assert "never enter Phase 4 or Phase 5" in site


  def test_reconcile_shadow_respawn_is_foreground():
      site = _flat(_between(_phase_3_5(), "7a. If `$SHADOW_MODEL_PIN`", "8. Parse verdict again"))
      assert "run_in_background: false" in site


  def test_headless_contract_block_unchanged():
      # HEADLESS_CONTRACT_BLOCK is the canonical pre-change text (#431's single source).
      spec = importlib.util.spec_from_file_location(
          "_headless_contract", REPO_ROOT / "tests" / "test_command_headless_contract.py")
      mod = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(mod)
      assert _text().count(mod.HEADLESS_CONTRACT_BLOCK) == 1
  ```

- [ ] **Step 2: Run and verify that it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py -v
  ```

  Expected: the four `*_is_foreground` tests FAIL on `assert 'run_in_background: false' in site`.
  `test_headless_contract_block_unchanged` PASSES.

- [ ] **Step 3: Edit Step 3.1 step 4 (Opus).** In `commands/dark-factory-conformance.md`,
  replace:

  ```markdown
     - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`

  5. Append the subagent's output to `CONFORMANCE_DIALOGUE`
  ```

  with:

  ```markdown
     - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`
     - **Foreground dispatch (#373):** pass `run_in_background: false` on this Agent tool
       call. The reviewer's response text is required before any later step executes. If
       the tool returns only a launch acknowledgement instead of the review, poll inside your
       turn (keep issuing tool calls) until the response arrives — never end the turn to
       wait. Never state a verdict, and never enter Phase 4 or Phase 5, without this step's
       (Opus) response text in hand for the current cycle.

  5. Append the subagent's output to `CONFORMANCE_DIALOGUE`
  ```

- [ ] **Step 4: Edit Step 3.1 step 5a (shadow).** Replace:

  ```markdown
     - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
     - Any tool error, timeout, or refusal is caught here — it never blocks, delays, or
  ```

  with:

  ```markdown
     - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
     - **Foreground dispatch (#373):** pass `run_in_background: false` on this Agent tool
       call too, and do not end your turn until it has returned — a result or an error,
       either is fine. If the tool returns only a launch acknowledgement, poll inside your
       turn until it returns. The non-gating contract below is unchanged: a shadow error,
       timeout or refusal still degrades to `SHADOW_STATUS: UNCERTAIN` and never blocks or
       retries the Opus verdict handling.
     - Any tool error, timeout, or refusal is caught here — it never blocks, delays, or
  ```

- [ ] **Step 5: Edit Phase 3.5 steps 6 and 7a.** Replace:

  ```markdown
  6. Re-spawn the conformance reviewer subagent with the verbatim contents of
     `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a
  ```

  with:

  ```markdown
  6. Re-spawn the conformance reviewer subagent with the verbatim contents of
     `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a (on a no-response cycle per
     step 4, the unchanged prompt from the previous cycle)
     - **Foreground dispatch (#373):** pass `run_in_background: false` on this Agent tool
       call. The reviewer's response text is required before any later step executes. If
       the tool returns only a launch acknowledgement instead of the review, poll inside your
       turn (keep issuing tool calls) until the response arrives — never end the turn to
       wait. Never state a verdict, and never enter Phase 4 or Phase 5, without this
       re-spawn's (Opus) response text in hand for the current cycle.
  ```

  Then replace:

  ```markdown
      `SHADOW_VERDICT_LINE` from this cycle's response (best-effort `UNCERTAIN` on any error).
  8. Parse verdict again → loop back to step 1
  ```

  with:

  ```markdown
      `SHADOW_VERDICT_LINE` from this cycle's response (best-effort `UNCERTAIN` on any error).
      - **Foreground dispatch (#373):** pass `run_in_background: false` on this shadow
        re-spawn too, and do not end your turn until it has returned — a result or an error.
        If the tool returns only a launch acknowledgement, poll inside your turn until it
        returns. The non-gating contract is unchanged: a shadow error, timeout or refusal
        still degrades to `SHADOW_STATUS: UNCERTAIN` and never blocks or retries the Opus
        verdict handling.
  8. Parse verdict again → loop back to step 1
  ```

  Do not change the model-pin, prompt or read-access text at any of the four sites.

- [ ] **Step 6: Run and verify that everything passes, including the existing command tests.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py tests/test_conformance_command_shadow_review.py tests/test_command_headless_contract.py tests/test_verifier_contract_doc_referenced.py tests/test_conformance_blast_adjudication.py -v
  ```

  Expected: all pass.

- [ ] **Step 7: Commit.**

  ```bash
  git add commands/dark-factory-conformance.md tests/test_conformance_command_artifact_guard.py
  git commit -m "fix(conformance): dispatch all four reviewer subagents in the foreground (#373)"
  ```

---

## Task 5: Conformance command — Phase 3.5 no-response cycle (Requirement 7)

**Files:**
- `tests/test_conformance_command_artifact_guard.py` (append)
- `commands/dark-factory-conformance.md`

- [ ] **Step 1: Append the failing test.**

  ```python


  # --- Requirement 7: no-response cycle skips the code fix, re-spawns, shares the budget ---

  def test_reconcile_step_4_no_response_cycle_precedes_code_fix():
      section = _flat(_phase_3_5())
      step4 = section.find("4. Decide whether this cycle has a finding to fix")
      cond = section.find("**No-response cycle (#373):**")
      fix = section.find("Fix the code to align with the spec")
      assert -1 not in (step4, cond, fix)
      assert step4 < cond < fix
      branch = section[cond:fix]
      assert "go straight to step 6" in branch
      assert "MAX_CYCLES" in branch
  ```

- [ ] **Step 2: Run and verify that it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py -v -k no_response
  ```

  Expected: FAIL. `cond` is `-1`, so `assert -1 not in (...)` fails.

- [ ] **Step 3: Edit Phase 3.5 step 4.** Replace:

  ```markdown
  4. Fix the code to align with the spec:
     - Write a failing test that targets the missing/wrong behavior
     - Run the test to confirm it fails: `cd backend && python -m pytest <test_path> -x -v`
     - Implement the fix
     - Run the test to confirm it passes
     - Commit: `git add -A && git commit -m "fix: align implementation with spec (conformance cycle $CONFORMANCE_CYCLE)"`
  ```

  with:

  ```markdown
  4. Decide whether this cycle has a finding to fix:
     - **No-response cycle (#373):** if this cycle was entered because the reviewer
       subagent's Agent tool call errored, timed out, or returned an empty/no-response result
       — not a parseable finding to act on — skip the code-fix and re-diff steps (the rest
       of this step, step 5 and step 5a) and go straight to step 6: re-spawn the reviewer
       with the identical `$ARTIFACTS_DIR/conformance_prompt.md`. This retry still consumes
       the cycle step 1 just counted against `MAX_CYCLES` — there is no separate retry
       budget, and step 2's `MAX_CYCLES` exit to Phase 5 still applies.
     - Otherwise: Fix the code to align with the spec:
       - Write a failing test that targets the missing/wrong behavior
       - Run the test to confirm it fails: `cd backend && python -m pytest <test_path> -x -v`
       - Implement the fix
       - Run the test to confirm it passes
       - Commit: `git add -A && git commit -m "fix: align implementation with spec (conformance cycle $CONFORMANCE_CYCLE)"`
  ```

- [ ] **Step 4: Run and verify that it passes.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py tests/test_conformance_command_shadow_review.py -v
  ```

  Expected: all pass.

- [ ] **Step 5: Commit.**

  ```bash
  git add commands/dark-factory-conformance.md tests/test_conformance_command_artifact_guard.py
  git commit -m "fix(conformance): re-spawn the reviewer without a code fix on a no-response cycle (#373)"
  ```

---

## Task 6: Conformance command — Phase 6 ARTIFACT GUARD (Requirement 9)

**Files:**
- `tests/test_conformance_command_artifact_guard.py` (append)
- `commands/dark-factory-conformance.md`

- [ ] **Step 1: Append the failing tests.**

  ```python


  # --- Requirement 9: Phase 6 is last and never synthesizes a verdict ---

  def _phase_6():
      text = _text()
      return text[text.index("## Phase 6: ARTIFACT GUARD"):]


  def test_phase_6_follows_phase_5_and_is_last():
      text = _text()
      p5 = text.find("## Phase 5: BLOCKED")
      p6 = text.find("## Phase 6: ARTIFACT GUARD")
      assert p5 != -1 and p6 > p5
      assert not re.search(r"^## ", text[p6 + len("## Phase 6"):], re.M), \
          "Phase 6 must be the file's last top-level section"


  def test_phase_6_never_synthesizes_a_verdict():
      section = _flat(_phase_6())
      assert "emit_verdict" not in section
      for literal in ("STATUS: BLOCKED", "STATUS: PASS", "STATUS: ERROR"):
          assert literal not in section, literal
      assert "leave the file absent" in section
      assert "grep -q '^STATUS:'" in section
      assert "exit 1" in section
  ```

- [ ] **Step 2: Run and verify that they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py -v -k phase_6
  ```

  Expected: both FAIL. `p6` is `-1` in the first test; the second raises
  `ValueError: substring not found`.

- [ ] **Step 3: Append Phase 6** to the very end of `commands/dark-factory-conformance.md`,
  after Phase 5 step 5's last line (`... does not reliably surface as node failure to the DAG
  executor (#212, #271).`). Leave exactly one blank line before the heading:

  ````markdown

  ## Phase 6: ARTIFACT GUARD

  Run this section last, on every path, before your turn ends: after Phase 4's "Exit `0`",
  after Phase 5 step 5, and after Phase 1 step 2's SKIPPED exit. Those "exit" lines mean that
  phase's own work is done; they do not skip this check. This guard never writes a verdict of
  its own — the only steps that write `$ARTIFACTS_DIR/conformance.md` are Phase 1 step 2,
  Phase 4 and Phase 5 (#373).

  1. Check the artifact:
     ```bash
     if test -f "$ARTIFACTS_DIR/conformance.md" && grep -q '^STATUS:' "$ARTIFACTS_DIR/conformance.md"; then
       echo "artifact-guard: conformance.md carries a STATUS line — nothing to do"
     else
       echo "artifact-guard: conformance.md is missing or has no STATUS line"
     fi
     ```
  2. If it carries a `STATUS:` line, stop here. Never overwrite or edit an existing verdict.
  3. If it is missing and a reviewer dispatch (Step 3.1 step 4 or 5a, Phase 3.5 step 6 or 7a)
     has not returned yet, wait for it per that step's foreground-dispatch rule — poll inside
     your turn, never end the turn to wait — then continue with 4.
  4. If it is missing and you hold the step-4 (Opus) reviewer response for the current cycle,
     go back and act on it **now** under Step 3.1 step 7's existing rules: a conforming
     verdict runs Phase 4; a material or inconclusive one re-enters the Phase 3.5 reconcile
     loop, which itself ends in Phase 4 or Phase 5. Then repeat step 1. The verdict written
     is the reviewer's real one, produced by the existing Phase 4/5 code — this section adds
     no new way to reach a pass.
  5. Otherwise — no usable reviewer response exists and the reconcile budget cannot produce
     one — leave the file absent and fail loudly:
     ```bash
     echo "dark-factory-conformance: artifact guard — $ARTIFACTS_DIR/conformance.md was never written and no reviewer response is available to write it from; leaving it absent so conformance-gate blocks and posts its failure comment." >&2
     exit 1
     ```
     Do not create the file. `conformance-gate` (`scripts/verdict_gate_check.sh`) then takes
     its missing-verdict path unchanged: it posts the `<!-- df-push-gate-failure -->` comment
     and logs the line `scripts/factory_core/error_signature.py` signs
     `substantive:verdict_missing`. A file written here would instead send the gate down its
     quiet "upstream already communicated" branch, suppressing that comment and that log
     line — the silent, misclassified failure #373 fixes.
  ````

  Check before saving: the section must contain no `emit_verdict`, and none of the three
  literal strings "STATUS:" + " BLOCKED", "STATUS:" + " PASS" or "STATUS:" + " ERROR".

- [ ] **Step 4: Run and verify that everything passes.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_conformance_command_artifact_guard.py tests/test_conformance_command_shadow_review.py tests/test_conformance_blast_adjudication.py tests/test_command_headless_contract.py -v
  ```

  Expected: all pass. The existing `test_phase_5_blocked_folds_shadow_block` and
  `test_phase5_label_and_board_writes_unchanged` slice `text[phase5_idx:]`, so they now
  include Phase 6. They still pass because they only assert presence of Phase 5 strings,
  and Phase 6 contains no `insert before ### Next Steps`.

- [ ] **Step 5: Commit.**

  ```bash
  git add commands/dark-factory-conformance.md tests/test_conformance_command_artifact_guard.py
  git commit -m "fix(conformance): add Phase 6 artifact guard that never synthesizes a verdict (#373)"
  ```

---

## Task 7: Doc map — signature class list and shifted line refs

`docs/run-lifecycle.md:1107-1112` lists the signature classes and cites
`error_signature.py:37-64`. Several refs in `docs/run-lifecycle.md` and `docs/features.md`
point into `commands/dark-factory-conformance.md` past old line 296, or into
`scripts/verdict_gate_check.sh`. Tasks 2–6 shift all of them. Refs at or before
conformance line 296 do not move: `:60`–`:77`, `:151-220`, `:181`, `:208`, `:210`, `:284`.
`scripts/docs_refs_check.py` only range-checks line numbers, so CI stays green either way;
the refs still have to be true.

**Files:** `docs/run-lifecycle.md`, `docs/features.md`

- [ ] **Step 1: Get the new line numbers.** Run this in one Bash call. Each anchor is the
  text at the old cited line, and `head -1` takes the first match:

  ```bash
  C=commands/dark-factory-conformance.md
  _at() { grep -nF -- "$1" "$2" | head -1 | cut -d: -f1; }
  echo "S=$(grep -n '^def classify' scripts/factory_core/error_signature.py | cut -d: -f1)   # old 37"
  echo "E=$(grep -n 'return f"substantive:unknown' scripts/factory_core/error_signature.py | cut -d: -f1)   # old 64"
  echo "SH5A_START=$(_at '5a. If `$SHADOW_MODEL_PIN` is non-empty, spawn a second' $C)   # old 300"
  echo "SH5A_END=$(_at 'If `$SHADOW_MODEL_PIN` is empty, skip this step entirely for this cycle.' $C)   # old 316"
  echo "P=$(_at 'emit_verdict "conformance" "PASS"' $C)   # old 553"
  echo "P4_OOS_START=$(_at 'printf "VERDICT: %s\nCYCLES: %s\nNO_SPEC' $C)   # old 554"
  echo "P4_OOS_END=$(_at '"${OOS_EXCISED:-0}" "${OOS_TICKETS:-}"' $C)   # old 556"
  echo "P4SH_START=$(_at 'if [ -n "${SHADOW_MODEL_PIN:-}" ]; then' $C)   # old 560"
  echo "P4SH_END=$(_at 'SHADOW_MODEL: ${SHADOW_MODEL_PIN}' $C)   # old 574"
  echo "MEM=$(_at 'write_memory_entry "$TARGET"' $C)   # old 623"
  echo "B=$(_at 'emit_verdict "conformance" "BLOCKED"' $C)   # old 701"
  echo "P5X_START=$(_at '5. Exit non-zero (`exit 1`)' $C)   # old 715"
  echo "P5X_END=$(_at 'internal `exit 1` does not reliably surface as node failure to the DAG executor (#212, #271).' $C)   # old 719"
  echo "SPILL=$(_at 'SPILLOVER_TICKETS=""' $C)   # old 444"
  echo "EXCISED=$(_at 'EXCISED_COUNT=<number of successfully excised changes>' $C)   # old 502"
  D=$(_at '5. Re-get the diff:' $C); echo "REDIFF=$D-$((D+4))   # old 526-530"
  echo "GATE_END=$(wc -l < scripts/verdict_gate_check.sh)   # old 86"
  ```

  Every value must be non-empty and greater than its old number. If one is empty, the
  anchor text was changed by mistake: re-check the task that touched it.

- [ ] **Step 2: Edit `docs/run-lifecycle.md`.** Replace the class sentence:

  ```markdown
     dir for the scheduler. Classes: `environmental:delivery_failure` (run shorter than
     `DELIVERY_FAILURE_MAX_SECONDS`, no commits, clean tree, no phase artifact),
     `environmental:preview_infra`, `environmental:rate_limit`, and
     `substantive:oos_files|build_failure|test_failure|unknown:<exit code>`
     (`entrypoint.sh:418-477`, `scripts/factory_core/error_signature.py:37-64`).
  ```

  with this text, putting the real S and E from Step 1 in place of `S-E`:

  ```markdown
     dir for the scheduler. Classes, in check order: `environmental:delivery_failure` (run
     shorter than `DELIVERY_FAILURE_MAX_SECONDS`, no commits, clean tree, no phase artifact),
     `environmental:rate_limit`, `substantive:verdict_missing` (a gate's missing/unparseable
     verdict line from `scripts/verdict_gate_check.sh`, ranked above the non-fatal preview
     noise every self-target run leaves, #373), `environmental:preview_infra`, and
     `substantive:oos_files|build_failure|test_failure|unknown:<exit code>`
     (`entrypoint.sh:418-477`, `scripts/factory_core/error_signature.py:S-E`).
  ```

- [ ] **Step 3: Replace the shifted refs** with the Step 1 values. Change only the numbers,
  never the surrounding prose:

  | Doc line (today) | Old ref text | New ref text |
  |---|---|---|
  | `docs/features.md:107` | `` `commands/dark-factory-conformance.md:300`-`:316` and `:560`-`:574` `` | `` `commands/dark-factory-conformance.md:SH5A_START`-`:SH5A_END` and `:P4SH_START`-`:P4SH_END` `` |
  | `docs/features.md:111` | `` `dark-factory-conformance.md:553` and `:701` `` | `` `dark-factory-conformance.md:P` and `:B` `` |
  | `docs/features.md:134` | `` `dark-factory-conformance.md:623` `` | `` `dark-factory-conformance.md:MEM` `` |
  | `docs/run-lifecycle.md:786` | `` `commands/dark-factory-conformance.md:715-719` `` and `` `scripts/verdict_gate_check.sh:36-86` `` | `` `commands/dark-factory-conformance.md:P5X_START-P5X_END` `` and `` `scripts/verdict_gate_check.sh:36-GATE_END` `` |
  | `docs/run-lifecycle.md:1214` | `` `commands/dark-factory-conformance.md:715-719` `` | `` `commands/dark-factory-conformance.md:P5X_START-P5X_END` `` |
  | `docs/run-lifecycle.md:1289-1290` | `` `commands/dark-factory-conformance.md:554-556` ``, `` (`:444`, `:502`) `` | `` `commands/dark-factory-conformance.md:P4_OOS_START-P4_OOS_END` ``, `` (`:SPILL`, `:EXCISED`) `` |
  | `docs/run-lifecycle.md:1297` | `` (`:526-530`) `` | `` (`:REDIFF`) `` |

  (The `docs/run-lifecycle.md` line numbers are from before Step 2. Step 2 grows lines
  1107–1112 by two lines, so 786 stays where it is, and 1214, 1289–1290 and 1297 each move
  down by 2. Find them by their text.)

- [ ] **Step 4: Verify.**

  ```bash
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  sed -n "$(grep -n '^def classify' scripts/factory_core/error_signature.py | cut -d: -f1)p" scripts/factory_core/error_signature.py
  ```

  ```bash
  grep -nE 'dark-factory-conformance\.md:(553|623|715-719|554-556)|md:300`-`:316|:560`-`:574|verdict_gate_check\.sh:36-86|error_signature\.py:37-64|\(`:444`, `:502`\)|\(`:526-530`\)' docs/run-lifecycle.md docs/features.md || echo "no stale refs"
  ```

  Expected: `docs_refs_check.py` exits 0 with no errors, the `sed` prints `def classify(`,
  and the last command prints `no stale refs`.

- [ ] **Step 5: Commit.**

  ```bash
  git add docs/run-lifecycle.md docs/features.md
  git commit -m "docs: add substantive:verdict_missing to the signature class list; refresh line refs (#373)"
  ```

---

## Task 8: Full verification and scope check (Requirements 10, 11)

**Files:** none. If a check fails, fix it in the task that owns the file.

- [ ] **Step 1: Run the full pytest suite** the way CI runs it. It takes about 2.5 minutes
  (3,200+ tests), so give the Bash call a 600 s timeout. Do not end the turn while it runs.

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q
  ```

  Expected: no failures.

- [ ] **Step 2: Run the CI-listed shell tests this ticket depends on.**

  ```bash
  bash tests/test_entrypoint_error_signature.sh && bash tests/test_verdict_gate_check.sh
  ```

  Expected: both end with `0 failed` and exit 0.

- [ ] **Step 3: Run the workflow DAG checks.** The workflow is unchanged; this only
  confirms nothing drifted.

  ```bash
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  ```

  Expected: both exit 0.

- [ ] **Step 4: Scope check against main.**

  ```bash
  git diff --name-only origin/main HEAD
  git diff origin/main HEAD -- scripts/factory_core/breaker.py workflows/archon-dark-factory.yaml tests/test_verdict_gate_check.sh config/ deploy/ .github/ | wc -l
  ```

  Expected: the first list has only the spec, this plan, `.archon/memory/architecture.md`
  (from refine), the files in the File Structure table, and whatever `implementation.md`
  bookkeeping the implement command adds. The second prints `0`.

- [ ] **Step 5: Self-classification check.** No changed file may contain the gate line
  contiguously (see the hazard note at the top).

  ```bash
  PYTHONPATH=scripts python - <<'PY'
  import os, subprocess
  from factory_core.error_signature import _VERDICT_MISSING_RE
  files = subprocess.run(["git", "diff", "--name-only", "origin/main", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.split()
  hits = [f for f in files if os.path.exists(f)
          and _VERDICT_MISSING_RE.search(open(f, encoding="utf-8", errors="replace").read())]
  print("hits:", hits)
  PY
  ```

  Expected: `hits: []`.

---

## Task 9: PR description notes (Requirement 6 part 2, reservations)

**Files:** none in the repo. This text goes into the PR body that the implement/push phase
writes.

- [ ] **Step 1: Add the PR-body sections below.** Fill in the outcome from Task 1 Step 3.

  ```markdown
  ## Requirement 6 verification (stderr → transcript)
  <FOUND / NOT AVAILABLE FROM THE RUN CONTAINER — copied verbatim from Task 1 Step 3 in implementation.md>

  ## Unverified assumptions (for the human merger)
  - The Archon executor forwards the `conformance-gate` `bash:` node's stderr into the stream
    `entrypoint.sh` tees to `TMP_OUT`. Indirect evidence: `STATUS=missing` is quoted from
    container logs in #373 and #439, and that text is emitted only by the two `bash:`
    verdict-gate scripts' stderr (`verdict_gate_check.sh`, `docs_verdict_gate_check.sh`).
  - Requirements 8/9 are agent instructions, not enforced mechanisms; the tests are static
    text checks only.

  ## Reservation (not in scope; candidate follow-up)
  - `_VERDICT_MISSING_RE` matches only the gate's *silent-death* line (needs-discussion
    absent), as the spec requires. The #197 run itself was Blast-Radius-parked, so
    needs-discussion was likely present and the gate took its *quiet* branch
    (`... blocks issue #N (STATUS=missing); upstream already communicated ...`), which this
    regex does not match. Since #551 the DAG only reaches conformance after `blast-gate`
    passes, so that combination should now be rare. Whether the quiet-branch
    `STATUS=missing` line should also classify is a separate decision.
  - `verdict_gate_check.sh` also backs `review-gate` (Gate 3,
    `workflows/archon-dark-factory.yaml`), so a missing code-review verdict is also signed
    `substantive:verdict_missing`. That is intended by the spec's `<gate label>` wording.
    `docs_verdict_gate_check.sh` lines never contain `verdict missing/unparseable`, so they
    do not match.
  ```

---

## Self-review

- [x] `**Issue:** #373` line present under the title.
- [x] Every spec requirement maps to a task: R1 → Task 2 Steps 4–5; R2 → Task 3; R3 → Task 2
  Step 1; R4 → Task 2 Step 1; R5 → Task 2 Step 6; R6 → Task 2 Step 2 (part 1) + Task 1 +
  Task 9 (part 2); R7 → Task 5; R8 → Task 4; R9 → Task 6; R10 → Tasks 4–6
  (`tests/test_conformance_command_artifact_guard.py`); R11 → Task 3 Step 4 + Task 8 Step 4.
- [x] No placeholders. The only runtime-computed values are Task 7's line numbers. Step 1
  gives the exact anchor command for each, and Step 3 maps each one to its doc ref. Task 9's outcome text is copied from Task 1.
- [x] No file in this plan's edits contains the gate's log line contiguously.
- [x] Dry run (planner, 2026-11-23, scratch worktree off `2d9c7cf`): every edit block in
  Tasks 2–7 applied cleanly as written. Each "verify fail" step failed exactly as stated and
  each "verify pass" step passed. The full suite gave 3271 passed. Also green:
  `test_entrypoint_error_signature.sh` (14/0), `test_verdict_gate_check.sh`,
  `docs_refs_check.py` and `check_workflow_dag.py`. The Task 8 Step 5 self-classification
  scan found no hits. After architect cycle 1, the revised Task 1 grep and Task 7's
  anchor script were dry-run again. All 17 anchors resolved to the expected shifted lines,
  and the Step 4 stale-ref grep printed `no stale refs`.

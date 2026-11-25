import json
import subprocess
import sys
import sys as _sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.error_signature import classify, write_signature


def _classify(text="", exit_code=1, elapsed=999, commits=1, dirty=False, artifact=True, **kw):
    return classify(
        text, exit_code,
        elapsed_seconds=elapsed,
        commits_since_start=commits,
        worktree_dirty=dirty,
        artifact_present=artifact,
        **kw,
    )


def test_delivery_failure_conjunction():
    sig = _classify(text="anything", elapsed=5, commits=0, dirty=False, artifact=False)
    assert sig == "environmental:delivery_failure"


def test_delivery_failure_requires_all_four_conjuncts():
    # elapsed under threshold but a commit landed — not delivery_failure
    assert _classify(text="", elapsed=5, commits=1, dirty=False, artifact=False) != \
        "environmental:delivery_failure"
    # elapsed under threshold, zero commits, clean tree, but an artifact exists
    assert _classify(text="", elapsed=5, commits=0, dirty=False, artifact=True) != \
        "environmental:delivery_failure"
    # elapsed at/over threshold
    assert _classify(text="", elapsed=30, commits=0, dirty=False, artifact=False) != \
        "environmental:delivery_failure"


def test_delivery_failure_max_seconds_is_tunable():
    sig = _classify(text="", elapsed=45, commits=0, dirty=False, artifact=False,
                     delivery_failure_max_seconds=60)
    assert sig == "environmental:delivery_failure"


def test_preview_infra_checked_before_build_failure():
    # Contains both a toolchain string and build-ish language; must classify preview_infra.
    sig = _classify(text="failed to solve: process /bin/sh -c npm ERR! build failed")
    assert sig == "environmental:preview_infra"


def test_rate_limit():
    sig = _classify(text="Error: you have hit your usage limit for this session", exit_code=1)
    assert sig == "environmental:rate_limit"


def test_oos_files():
    sig = _classify(text="OOS gate: excising out-of-scope files: foo.py", exit_code=1)
    assert sig == "substantive:oos_files:1"


def test_build_failure():
    sig = _classify(text="npm ERR! code ELIFECYCLE\nnpm ERR! Exit status 1", exit_code=1)
    assert sig == "substantive:build_failure:1"


def test_test_failure():
    sig = _classify(text="FAILED tests/test_foo.py::test_bar - AssertionError", exit_code=1)
    assert sig == "substantive:test_failure:1"


def test_unknown_fallback():
    sig = _classify(text="some completely unrelated stack trace", exit_code=3)
    assert sig == "substantive:unknown:3"


def test_environmental_signatures_have_no_exit_code_suffix():
    assert ":" not in _classify(text="", elapsed=5, commits=0, dirty=False,
                                 artifact=False).split("environmental:")[1]
    assert _classify(text="rate limit exceeded", elapsed=120) == "environmental:rate_limit"


def test_write_signature_atomic(tmp_path):
    write_signature(42, "implement", "substantive:test_failure:1", 1, tmp_path)
    sig_file = tmp_path / "error-signatures" / "42.implement.sig"
    assert sig_file.exists()
    data = json.loads(sig_file.read_text())
    assert data == {"signature": "substantive:test_failure:1", "phase": "implement", "exit_code": 1}


def test_write_signature_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "state"
    write_signature(7, "plan", "environmental:rate_limit", 1, target)
    assert (target / "error-signatures" / "7.plan.sig").exists()


CLI = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py")


def test_cli_error_signature_write_end_to_end(tmp_path):
    text_file = tmp_path / "out.txt"
    text_file.write_text("FAILED tests/test_foo.py::test_bar - AssertionError")
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "implement", "--exit-code", "1",
         "--text-file", str(text_file),
         "--elapsed-seconds", "120", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=substantive:test_failure:1" in result.stdout
    sig_file = tmp_path / "error-signatures" / "9.implement.sig"
    assert json.loads(sig_file.read_text())["signature"] == "substantive:test_failure:1"


def test_rate_limit_session_limit_string(tmp_path):
    # Literal string observed in the #303 failure (Fix #195's first live engagement):
    # entrypoint.sh's on_failure() passed an empty text_file, so this never reached the
    # RATE_LIMIT_RE classifier and fell through to substantive:unknown.
    text_file = tmp_path / "out.txt"
    text_file.write_text("Claude session limit reached — resets 9:20pm (UTC)")
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "33", "--phase", "implement", "--exit-code", "1",
         "--text-file", str(text_file),
         "--elapsed-seconds", "300", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=environmental:rate_limit" in result.stdout
    sig_file = tmp_path / "error-signatures" / "33.implement.sig"
    assert json.loads(sig_file.read_text())["signature"] == "environmental:rate_limit"


def test_classify_rejects_sha_embedded_429_negative():
    assert _classify(text="fixed in abc4291f2e") != "environmental:rate_limit"


def test_classify_rejects_dollar_figure_negative():
    assert _classify(text="cost $0.429 total this run") != "environmental:rate_limit"


def test_classify_rejects_issue_reference_negative():
    assert _classify(text="see #429 for context") != "environmental:rate_limit"


def test_classify_http_429_rate_limit_exceeded_is_environmental():
    # Transient-429-still-classifies-for-the-breaker case (Decision 3): real for the
    # breaker even though the paired is_session_window_failure test in
    # test_factory_core_session_window.py asserts False for the same string.
    assert _classify(text="HTTP 429 — rate limit exceeded") == "environmental:rate_limit"


def test_cli_error_signature_write_missing_text_file_is_empty_text(tmp_path):
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "plan", "--exit-code", "1",
         "--text-file", str(tmp_path / "nonexistent.txt"),
         "--elapsed-seconds", "5", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=environmental:delivery_failure" in result.stdout


def test_session_window_pause_signature_constant():
    from factory_core.error_signature import SESSION_WINDOW_PAUSE_SIGNATURE
    assert SESSION_WINDOW_PAUSE_SIGNATURE == "environmental:session_window_pause"


def test_cli_session_window_pause_signature_write_end_to_end(tmp_path):
    result = subprocess.run(
        [_sys.executable, CLI, "session-window-pause-signature-write",
         "--issue", "19", "--phase", "plan",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    sig_file = tmp_path / "error-signatures" / "19.plan.sig"
    data = json.loads(sig_file.read_text())
    assert data == {
        "signature": "environmental:session_window_pause",
        "phase": "plan",
        "exit_code": 0,
    }


def test_cli_session_window_pause_signature_write_independent_of_classify(tmp_path):
    # Direct write, not routed through classify() — arbitrary/absent text must not change
    # the outcome (contrast test_cli_error_signature_write_missing_text_file_is_empty_text,
    # which DOES route through classify() and yields environmental:delivery_failure).
    result = subprocess.run(
        [_sys.executable, CLI, "session-window-pause-signature-write",
         "--issue", "20", "--phase", "refine",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    sig_file = tmp_path / "error-signatures" / "20.refine.sig"
    assert json.loads(sig_file.read_text())["signature"] == "environmental:session_window_pause"


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
# The gate's quiet branch (needs-discussion present). It reports a missing verdict as
# STATUS=missing — the shape #197 most likely took — and a real one as STATUS=BLOCKED.
_QUIET_HEAD = _GATE_PREFIX + "Conformance (Gate 2) blocks issue #197 (STATUS="
_QUIET_TAIL = "); upstream already communicated, no comment needed."
_QUIET_MISSING_LINE = _QUIET_HEAD + "missing" + _QUIET_TAIL
_QUIET_BLOCKED_LINE = _QUIET_HEAD + "BLOCKED" + _QUIET_TAIL


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


def test_verdict_missing_matches_quiet_branch_status_missing():
    # Second operator amendment (#373): the quiet-branch line also signs verdict_missing.
    sig = _classify(text=_BENIGN_PREVIEW + _QUIET_MISSING_LINE + "\n")
    assert sig == "substantive:verdict_missing"


def test_verdict_missing_ignores_quiet_branch_status_blocked():
    # A real BLOCKED verdict is not a missing one: today's classification is unchanged.
    alone = _classify(text=_QUIET_BLOCKED_LINE + "\n")
    assert alone != "substantive:verdict_missing"
    with_preview = _classify(text=_BENIGN_PREVIEW + _QUIET_BLOCKED_LINE + "\n")
    assert with_preview == "environmental:preview_infra"


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


def test_verdict_missing_regex_matches_gate_script_quiet_branch_wording():
    # Couples the regex to the quiet-branch echo too: STATUS=missing must match,
    # STATUS=BLOCKED (a real verdict) must not.
    import re
    from factory_core.error_signature import _VERDICT_MISSING_RE
    hits = [line.strip() for line in _GATE_SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("echo ") and "upstream already communicated" in line]
    assert len(hits) == 1, f"expected one quiet-branch echo in {_GATE_SCRIPT}, got {len(hits)}"
    m = re.match(r'echo "(.*)" >&2$', hits[0])
    assert m, hits[0]
    assert "${STATUS:-missing}" in m.group(1), "quiet-branch echo no longer prints STATUS"

    def render(status):
        return (m.group(1)
                .replace("${GATE_LABEL}", "Conformance (Gate 2)")
                .replace("${ISSUE_NUM}", "197")
                .replace("${STATUS:-missing}", status))

    missing_matches = bool(_VERDICT_MISSING_RE.search(render("missing")))
    assert missing_matches, "quiet-branch STATUS=missing wording drifted from the regex"
    sig = _classify(text=render("missing"))
    assert sig == "substantive:verdict_missing"
    for status in ("PAS", "BLOCKEDX", "blocked"):  # unparseable: same signature (#591)
        unparseable_matches = bool(_VERDICT_MISSING_RE.search(render(status)))
        assert unparseable_matches, f"quiet-branch STATUS={status} must match as unparseable"
    for status in ("BLOCKED", "HUMAN_REQUIRED", "FAIL"):  # real verdicts
        real_matches = bool(_VERDICT_MISSING_RE.search(render(status)))
        assert not real_matches, f"a STATUS={status} quiet-branch line must not match"
    path_matches = bool(_VERDICT_MISSING_RE.search("scripts/" + render("missing")))
    assert path_matches, "a path-prefixed quiet-branch line must still match (#591)"


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

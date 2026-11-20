"""Guard (#426): every tests/*.sh must run in CI or be explicitly exempted.

.github/workflows/ci.yml wires shell tests one `- run: bash tests/X.sh` step at a time, so
an unwired file is indistinguishable from a passing one. This file fails when a shell test
is neither referenced in a live ci.yml run: step nor listed in CI_EXEMPT_SHELL_TESTS, and
when an exemption goes stale (the file was wired in or deleted)."""
import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_SH_REF_PATTERN = re.compile(r"tests/[a-z_0-9]+\.sh")


def _referenced_in_ci(ci_text: str) -> set:
    """Shell tests named in the run: string of a live step. YAML comments never count."""
    refs = set()
    for job in ((yaml.safe_load(ci_text) or {}).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if str(step.get("if", "")).strip().lower() == "false":
                continue
            for m in _SH_REF_PATTERN.findall(str(step.get("run") or "")):
                refs.add(m.split("/", 1)[1])
    return refs


def _violations(sh_files: set, referenced: set, exempt) -> tuple:
    """Pure: (unaccounted, stale). No filesystem access, so it is testable synthetically."""
    unaccounted = sh_files - referenced - set(exempt)
    stale = {name for name in exempt if name in referenced or name not in sh_files}
    return unaccounted, stale


def _live() -> tuple:
    sh_files = {p.name for p in (_REPO_ROOT / "tests").glob("*.sh")}
    referenced = _referenced_in_ci(_CI_YML.read_text(encoding="utf-8"))
    # A wrong path or a parse that finds nothing must not pass vacuously.
    assert sh_files, "no tests/*.sh found — wrong repo root?"
    assert referenced, "no shell test found in any ci.yml run: step — parse broken?"
    return sh_files, referenced


# tests/*.sh not yet wired into .github/workflows/ci.yml, each with a reason.
# Seeded 2026-11-20 (#426) from:
#   comm -23 \
#     <(git ls-tree HEAD tests/ --name-only | grep '\.sh$' | sed 's|tests/||' | sort) \
#     <(git show HEAD:.github/workflows/ci.yml | grep -oE 'tests/[a-z_0-9]+\.sh' | sed 's|tests/||' | sort -u)
# Each reason is the observed result of one standalone run with credentials stripped:
#   env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$(mktemp -d)" \
#     SCHEDULER_STATE_DIR="$(mktemp -d)" timeout 120 bash tests/X.sh
# either "passes standalone — wire in follow-up" or "fails: <first failure>".
# Wiring a test into ci.yml means deleting its entry here (test_no_stale_exemptions).
CI_EXEMPT_SHELL_TESTS = {
    "test_159_regression.sh": "fails: set_board_status called (→ Blocked) — expected='1' got='0\\n0'",
    "test_431_telemetry_isolation.sh": "passes standalone — wire in follow-up",
    "test_close_preview_teardown.sh": "passes standalone — wire in follow-up",
    "test_codeindex_config.sh": "fails: implement depends on update-codeindex — pattern not found in workflows/archon-dark-factory.yaml",
    "test_config_deletion.sh": "passes standalone — wire in follow-up",
    "test_conformance_memory_write.sh": "passes standalone — wire in follow-up",
    "test_dispatch_ceiling.sh": "fails: L → always above — expected='0' got='1'",
    "test_entrypoint_fix_main.sh": "fails: fix-main route (896) not before smoke gate (679)",
    "test_epic_autopilot_config.sh": "passes standalone — wire in follow-up",
    "test_failing_checks_for_pr.sh": "passes standalone — wire in follow-up",
    "test_has_new_comment_after_report.sh": "passes standalone — wire in follow-up",
    "test_load_memory.sh": "passes standalone — wire in follow-up",
    "test_memory_integration.sh": "passes standalone — wire in follow-up",
    "test_memory_write_gate.sh": "passes standalone — wire in follow-up",
    "test_off_board_detection.sh": "passes standalone — wire in follow-up",
    "test_preview_differentiator.sh": "passes standalone — wire in follow-up",
    "test_restore_drill.sh": "fails: R2: no-backup exits 2 (expected 0); spec requires exit 0 on fresh-deploy skip",
    "test_scheduler.sh": "fails: line 411: STATUS_REFINED: unbound variable",
    "test_scheduler_autopilot_guard.sh": "passes standalone — wire in follow-up",
    "test_scheduler_ceiling.sh": "passes standalone — wire in follow-up",
    "test_scheduler_main_red_fixer.sh": "passes standalone — wire in follow-up",
    "test_scheduler_pagination.sh": "passes standalone — wire in follow-up",
    "test_session_window_gate.sh": "fails: 'Priority 1.5:' gate (351) does not precede breaker calls (33)",
}


# --- live-repo checks ---

def test_every_shell_test_is_wired_or_exempt():
    unaccounted, _ = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
    assert not unaccounted, (
        f"tests/*.sh not in ci.yml and not in CI_EXEMPT_SHELL_TESTS: {sorted(unaccounted)} — "
        "add a `- run: bash tests/<file>` step to .github/workflows/ci.yml, or exempt it "
        "here with a reason"
    )


def test_no_stale_exemptions():
    _, stale = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
    assert not stale, (
        f"CI_EXEMPT_SHELL_TESTS entries now stale (wired into ci.yml or deleted): {sorted(stale)}"
    )


def test_exemptions_have_reasons():
    empty = [name for name, reason in CI_EXEMPT_SHELL_TESTS.items() if not reason.strip()]
    assert not empty, f"CI_EXEMPT_SHELL_TESTS entries missing a reason: {empty}"


# --- synthetic checks: prove the guard can fail ---

def test_new_unwired_file_is_unaccounted():
    assert _violations({"a.sh", "new.sh"}, {"a.sh"}, {}) == ({"new.sh"}, set())


def test_exempt_file_that_is_wired_is_stale():
    assert _violations({"a.sh"}, {"a.sh"}, {"a.sh": "r"}) == (set(), {"a.sh"})


def test_exempt_file_that_is_missing_is_stale():
    assert _violations({"a.sh"}, {"a.sh"}, {"gone.sh": "r"}) == (set(), {"gone.sh"})


def test_commented_out_or_disabled_step_is_not_referenced():
    ci = (
        "jobs:\n  tests:\n    steps:\n"
        "      - run: bash tests/test_live.sh\n"
        "      # - run: bash tests/test_commented.sh\n"
        "      - run: bash tests/test_off.sh\n        if: false\n"
    )
    assert _referenced_in_ci(ci) == {"test_live.sh"}

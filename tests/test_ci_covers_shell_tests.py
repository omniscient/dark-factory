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

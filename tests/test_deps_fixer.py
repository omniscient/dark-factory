import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from factory_core import adapter_defaults  # noqa: E402
from factory_core import deps_fixer as df  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GLOBS = adapter_defaults.DEFAULTS["safety"]["deps_allowed_paths"]
BLOCKED = df.BLOCKED_PATHS

RED = ("pip\trequests\t2.31.0\t2.32.0\tGHSA-9wx4-h78v-vm56\thigh\n"
       "npm\taxios\t<1.7.4\t1.7.4\tGHSA-xxxx-yyyy-zzzz\thigh\n")
NOFIX = "pip\tpython-jose\t3.3.0\t\tPYSEC-2026-4298\tunknown\n"


# ── pure core: parsing ──────────────────────────────────────────────────────

def test_parse_advisories_tab_separated_and_ignores_noise():
    out = df.parse_advisories("Collecting pip-audit\n" + RED + "\n" + NOFIX + "bad\tline\n")
    assert [a["id"] for a in out] == ["GHSA-9wx4-h78v-vm56", "GHSA-xxxx-yyyy-zzzz", "PYSEC-2026-4298"]
    assert out[0] == {"ecosystem": "pip", "package": "requests", "installed": "2.31.0",
                      "fixed_in": "2.32.0", "id": "GHSA-9wx4-h78v-vm56", "severity": "high"}
    assert out[2]["fixed_in"] == ""          # empty fixed_in survives (no-fix advisory)


def test_advisory_fields_are_sanitised():
    # fields come from external feeds: no markup reaches the fix-agent prompt or a GitHub body
    line = "npm\tevil\t1.0.0\t9.9 `x` <!-- c --> [l](u)\tGHSA-1 <!-- df-deps-audit -->\thigh\n"
    a = df.parse_advisories(line)[0]
    assert "`" not in a["fixed_in"] and "[" not in a["fixed_in"] and "!" not in a["id"]
    assert len(df.parse_advisories("npm\t" + "p" * 500 + "\t1\t2\tID\thigh\n")[0]["package"]) == 120
    rng = df.parse_advisories("npm\taxios\t<1.7.4 || >=2.0.0 <2.1.0\t1.7.4\tGHSA-a\thigh\n")
    assert rng[0]["installed"] == "<1.7.4 || >=2.0.0 <2.1.0"       # real npm ranges survive
    assert "<1.7.4 \\|\\| >=2.0.0" in df.advisory_table(rng)         # pipes escaped in the table


def test_read_audit_rc_contract():
    assert df.read_audit(0, RED) == []          # exit 0 = clean, whatever stdout says
    assert len(df.read_audit(1, RED)) == 2
    assert df.read_audit(2, "Traceback: boom") is None   # red but unparseable → error


def test_advisory_set_hash_is_order_and_duplicate_insensitive():
    a = df.parse_advisories(RED)
    assert df.advisory_set_hash(a) == df.advisory_set_hash(list(reversed(a)) + a[:1])
    assert df.advisory_set_hash(a) != df.advisory_set_hash(df.parse_advisories(RED + NOFIX))
    assert len(df.advisory_set_hash(a)) == 12


# ── pure core: scope ────────────────────────────────────────────────────────

def test_scope_allowed_for_manifests_and_lockfiles():
    changed = ["backend/requirements.txt", "frontend/package.json",
               "frontend/package-lock.json", "package.json"]
    assert df.classify_deps_scope(changed, GLOBS, BLOCKED) == "allowed"


def test_scope_protected_for_ci_workflow_even_with_allowed_paths():
    changed = ["frontend/package.json", ".github/workflows/ci.yml"]
    assert df.classify_deps_scope(changed, GLOBS, BLOCKED) == "protected"


def test_scope_protected_for_factory_hook_ignore_list():
    assert df.classify_deps_scope([".factory/hooks/deps-audit"], GLOBS, BLOCKED) == "protected"


def test_scope_unknown_for_source_file_and_empty():
    assert df.classify_deps_scope(["backend/app/main.py"], GLOBS, BLOCKED) == "unknown"
    assert df.classify_deps_scope([], GLOBS, BLOCKED) == "unknown"


def test_scope_basename_glob_does_not_cross_directories():
    assert df.classify_deps_scope(["docs/requirements-x/notes.txt"], GLOBS, BLOCKED) == "unknown"
    assert df.classify_deps_scope(["requirements-dev.txt"], GLOBS, BLOCKED) == "allowed"


def test_scope_allowed_entry_is_exact_path_not_substring():
    # root package.json is allowed; a sibling whose path CONTAINS it must not ride along
    changed = ["package.json", "frontend/package.json.bak"]
    assert df.classify_deps_scope(changed, GLOBS, BLOCKED) == "unknown"


def test_classify_scope_is_reused_not_copied():
    from factory_core import main_red_fixer as mf
    assert df.classify_scope is mf.classify_scope


# ── pure core: attempts + rendering ─────────────────────────────────────────

def test_attempts_keyed_on_advisory_set_hash():
    st = {}
    df.record_attempt(st, "aaa")
    df.record_attempt(st, "aaa")
    assert df.attempts_for(st, "aaa") == 2
    assert df.attempts_for(st, "bbb") == 0       # a NEW advisory set starts from zero


def test_build_fix_prompt_names_scope_and_no_fix_rule():
    p = df.build_fix_prompt(df.parse_advisories(NOFIX), GLOBS, BLOCKED)
    assert "PYSEC-2026-4298" in p and "**/package-lock.json" in p and ".github/" in p
    assert df.NOTES_FILE in p and "audit" in p and "ignore" in p


def test_pr_title_and_body():
    adv = df.parse_advisories(RED)
    assert df.pr_title(adv) == "deps: resolve 2 advisories (GHSA-9wx4-h78v-vm56, GHSA-xxxx-yyyy-zzzz)"
    body = df.pr_body(7, adv, [], ["frontend/package.json"], "fixed-by-agent", "ok", "")
    assert body.startswith("Refs #7") and "Closes" not in body
    assert "`frontend/package.json`" in body
    assert "Dependency-Health Loop" in body and "Posted by" in body
    partial = df.pr_body(7, adv, df.parse_advisories(NOFIX), ["a/requirements.txt"],
                         "fixed-by-agent", "ok", "ignore PYSEC-2026-4298: no fix")
    assert partial.startswith("Refs #7") and "ignore PYSEC-2026-4298" in partial


def test_tracking_issue_body_carries_marker_and_hash():
    adv = df.parse_advisories(RED)
    body = df.tracking_issue_body(adv, df.advisory_set_hash(adv))
    assert df.TRACKING_MARKER in body and df.advisory_set_hash(adv) in body
    assert "GHSA-9wx4-h78v-vm56" in body


# ── adapter lookup ──────────────────────────────────────────────────────────

def test_deps_allowed_paths_default_parity(tmp_path):
    assert df._deps_allowed_paths(str(tmp_path)) == GLOBS


def test_deps_allowed_paths_adapter_override(tmp_path):
    d = tmp_path / ".factory"
    d.mkdir()
    (d / "adapter.yaml").write_text("safety:\n  deps_allowed_paths:\n    - 'poetry.lock'\n")
    assert df._deps_allowed_paths(str(tmp_path)) == ["poetry.lock"]

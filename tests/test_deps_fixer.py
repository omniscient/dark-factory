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


# ── run_once state machine (FakeIO) ─────────────────────────────────────────

class FakeIO:
    def __init__(self, audits=(RED,), present=True, changed=None, ci="green",
                 merge_ok=True, direct_to_pr=False, notes="", issue=42, open_tracking=True,
                 halted=False):
        # audits: successive deps-audit stdouts; "" = clean (rc 0), "!" = unparseable red
        self._audits = list(audits)
        self._present = present
        self._changed = ["frontend/package-lock.json"] if changed is None else changed
        self._ci = ci
        self._merge_ok = merge_ok
        self._d2p = direct_to_pr
        self._notes = notes
        self._issue = issue
        self._open_tracking = open_tracking
        self._halted = halted
        self.calls, self.prompts, self.prs, self.merged = [], [], [], []
        self.escalations, self.labels, self.comments, self.closed = [], [], [], []

    def audit_hook_present(self):
        return self._present

    def run_audit(self):
        self.calls.append("audit")
        out = self._audits.pop(0) if len(self._audits) > 1 else self._audits[0]
        if out == "":
            return 0, ""
        if out == "!":
            return 2, "Traceback"
        return 1, out

    def open_tracking_issues(self):
        return [42] if self._open_tracking else []

    def set_done(self, issue):
        self.calls.append(f"done:{issue}")

    def close_issue(self, issue, comment):
        self.calls.append(f"close:{issue}")
        self.closed.append(issue)

    def find_or_create_tracking_issue(self, advisories, h):
        return self._issue

    def is_halted(self, issue):
        return self._halted

    def start_branch(self):
        self.calls.append("branch")
        return "deps/audit-20261119"

    def run_deterministic_fix(self):
        self.calls.append("deps-fix")

    def apply_agent_fix(self, prompt):
        self.prompts.append(prompt)
        return self._notes

    def changed_paths(self):
        return self._changed

    def run_tests(self):
        return "validate hook exit=0"

    def open_pr(self, branch, title, body):
        self.prs.append((branch, title, body))
        return 900

    def move_to_in_review(self, issue):
        self.calls.append(f"in_review:{issue}")

    def poll_ci(self, pr):
        return self._ci

    def has_direct_to_pr(self, issue):
        return self._d2p

    def merge(self, pr):
        self.merged.append(pr)
        return self._merge_ok

    def add_label(self, issue, label):
        self.labels.append((issue, label))

    def comment(self, issue, body):
        self.comments.append((issue, body))

    def escalate(self, issue, reason, h, pr=None):
        self.escalations.append((issue, reason, pr))


CFG = dict(max_attempts=3, model="claude-opus-5-5", auto_merge=False,
           allowed_paths=GLOBS, blocked_paths=BLOCKED)


def test_inactive_when_no_hook():
    io = FakeIO(present=False)
    out = df.run_once(CFG, io, {})
    assert out["outcome"] == "inactive" and io.calls == [] and io.prs == []


def test_audit_runs_on_fresh_branch_checkout():
    io = FakeIO(audits=("",))
    df.run_once(CFG, io, {})
    assert io.calls[:2] == ["branch", "audit"]      # origin/main reset happens before the audit


def test_tracking_issue_failure_is_error_without_attempt():
    io = FakeIO(issue=None)
    st = {}
    out = df.run_once(CFG, io, st)
    assert out["outcome"] == "error" and out["reason"] == "tracking-issue"
    assert st == {} and io.prs == [] and "deps-fix" not in io.calls


def test_needs_discussion_on_tracking_issue_holds_the_loop():
    io = FakeIO(halted=True)
    st = {}
    out = df.run_once(CFG, io, st)
    assert out == {"outcome": "held", "issue": 42, "pr": None, "reason": "needs-discussion"}
    assert st == {} and io.prs == [] and io.prompts == [] and io.calls == ["branch", "audit"]


def test_clean_moves_tracking_issue_to_done_then_closes():
    io = FakeIO(audits=("",))
    out = df.run_once(CFG, io, {})
    assert out == {"outcome": "clean", "issue": 42, "pr": None}
    assert io.closed == [42] and io.prs == []
    # Done BEFORE close: a closed issue left In review holds a MAX_IN_REVIEW slot forever
    assert io.calls.index("done:42") < io.calls.index("close:42")


def test_clean_with_no_tracking_issue_is_quiet():
    io = FakeIO(audits=("",), open_tracking=False)
    out = df.run_once(CFG, io, {})
    assert out == {"outcome": "clean", "issue": None, "pr": None} and io.closed == []


def test_unparseable_audit_is_error_not_clean():
    io = FakeIO(audits=("!",))
    out = df.run_once(CFG, io, {})
    assert out["outcome"] == "error" and io.closed == [] and io.prs == []


def test_fixed_deterministically_skips_agent_and_does_not_merge():
    io = FakeIO(audits=(RED, ""))
    st = {}
    out = df.run_once(CFG, io, st)
    assert out["outcome"] == "fixed-deterministically" and out["pr"] == 900
    assert io.prompts == []                          # no claude -p pass
    assert io.merged == []                           # auto_merge false, no direct-to-pr
    assert "in_review:42" in io.calls                # tracking issue moved to In review
    assert (42, "ready-for-human") in io.labels      # scheduler stages skip it while awaiting approval
    assert io.prs[0][2].startswith("Refs #42")       # never Closes — clean audit closes + Done
    h = df.advisory_set_hash(df.parse_advisories(RED))
    assert df.attempts_for(st, h) == 1


def test_fixed_by_agent_when_deterministic_pass_leaves_advisories():
    io = FakeIO(audits=(RED, RED, ""))
    out = df.run_once(CFG, io, {})
    assert out["outcome"] == "fixed-by-agent" and len(io.prompts) == 1
    assert io.calls.count("deps-fix") == 1 and io.calls.count("audit") == 3
    assert io.merged == []


def test_no_fix_version_opens_pr_with_notes_and_red_ci_needs_human():
    io = FakeIO(audits=(NOFIX, NOFIX, NOFIX), changed=["backend/requirements.txt"],
                ci="red", notes="Proposed: --ignore-vuln PYSEC-2026-4298 (no fixed release)")
    out = df.run_once(CFG, io, {})
    assert out["outcome"] == "fixed-by-agent" and out["remaining"] == 1
    body = io.prs[0][2]
    assert body.startswith("Refs #42") and "--ignore-vuln PYSEC-2026-4298" in body
    assert (42, "ready-for-human") in io.labels and io.merged == []


def test_no_fix_escalates_on_empty_diff_without_pr():
    io = FakeIO(audits=(RED, RED, RED), changed=[])
    out = df.run_once(CFG, io, {})
    assert out == {"outcome": "escalated", "issue": 42, "pr": None, "reason": "no-fix"}
    assert io.prs == [] and io.escalations[0][1] == "no-fix"


def test_protected_scope_escalates_without_pr():
    io = FakeIO(audits=(RED, RED, ""), changed=[".github/workflows/ci.yml", "package.json"])
    out = df.run_once(CFG, io, {})
    assert out["outcome"] == "escalated" and out["reason"] == "scope:protected"
    assert io.prs == [] and io.merged == []


def test_cap_escalates_once_without_attempting():
    h = df.advisory_set_hash(df.parse_advisories(RED))
    st = {"sets": {h: {"attempts": 3}}}
    io = FakeIO()
    out1 = df.run_once(CFG, io, st)
    out2 = df.run_once(CFG, io, st)
    assert out1["reason"] == "cap" and out2["reason"] == "cap"
    assert len(io.escalations) == 1                  # escalation deduped
    assert io.prs == [] and io.prompts == [] and "deps-fix" not in io.calls


def test_new_advisory_set_is_not_capped_by_old_set():
    old = df.advisory_set_hash(df.parse_advisories(NOFIX))
    io = FakeIO(audits=(RED, ""))
    out = df.run_once(CFG, io, {"sets": {old: {"attempts": 3, "cap_escalated": True}}})
    assert out["outcome"] == "fixed-deterministically"


def test_green_ci_merges_only_when_auto_merge_or_direct_to_pr():
    io = FakeIO(audits=(RED, ""))
    df.run_once(dict(CFG, auto_merge=True), io, {})
    assert io.merged == [900]
    io = FakeIO(audits=(RED, ""), direct_to_pr=True)
    out = df.run_once(CFG, io, {})
    assert io.merged == [900] and out["merged"] is True
    io = FakeIO(audits=(RED, ""), ci="red", direct_to_pr=True)
    df.run_once(dict(CFG, auto_merge=True), io, {})
    assert io.merged == []                           # never merge on red


def test_merge_failure_escalates_with_pr():
    io = FakeIO(audits=(RED, ""), merge_ok=False)
    out = df.run_once(dict(CFG, auto_merge=True), io, {})
    assert out["merged"] is False and io.escalations[0][1:] == ("merge-failed", 900)


def test_lifecycle_red_pr_not_merged_then_clean_closes():
    """#484 acceptance: red → tracking issue + PR in review, not merged; next clean audit closes."""
    st = {}
    io = FakeIO(audits=(RED, ""))
    first = df.run_once(CFG, io, st)
    assert first["issue"] == 42 and first["pr"] == 900 and first["merged"] is False
    assert "in_review:42" in io.calls and io.merged == []
    io2 = FakeIO(audits=("",))
    second = df.run_once(CFG, io2, st)
    assert second["outcome"] == "clean" and io2.closed == [42] and "done:42" in io2.calls

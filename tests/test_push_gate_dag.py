"""Static content assertions for the artifact-gated refine-push/plan-push-and-advance
DAG nodes (#212), mirroring the tests/test_budget_enforce_dag.py convention for testing
DAG bash-node bodies without executing them."""
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id,prefix,label,noun", [
    ("refine-push", "docs/superpowers/specs/", "spec-pending-review", "spec"),
    ("plan-push-and-advance", "docs/superpowers/plans/", "plan-pending-review", "plan"),
])
class TestPushGateNodes:
    def test_node_calls_push_gate_check_script(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "push_gate_check.sh" in bash, f"'{node_id}' must call push_gate_check.sh"
        assert prefix in bash, f"'{node_id}' must pass artifact prefix '{prefix}'"

    def test_node_checks_needs_discussion_live(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "needs-discussion" in bash, \
            f"'{node_id}' must check the live needs-discussion label"

    def test_node_posts_failure_marker_on_miss(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "df-refine-failure" in bash, \
            f"'{node_id}' must post the <!-- df-refine-failure --> marker comment on a true miss"
        assert "tracker comment" in bash and "--marker" in bash, \
            f"'{node_id}' must use the tracker comment --marker upsert primitive"

    def test_node_gates_push_and_label_behind_artifact_check(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        gate_pos = bash.find("push_gate_check.sh")
        push_pos = bash.find("git push")
        label_pos = bash.find(f"--add {label}")
        assert gate_pos != -1 and push_pos != -1 and label_pos != -1
        assert gate_pos < push_pos, \
            f"'{node_id}': push_gate_check.sh must run before git push"
        assert gate_pos < label_pos, \
            f"'{node_id}': push_gate_check.sh must run before the gate label is applied"

    def test_node_guards_label_call_and_warns_on_failure(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        guard = f'if python3 "$_PCLI" tracker label --id "$ISSUE" --add {label}'
        assert guard in bash, f"'{node_id}': the gate-label call must be guarded by an if/else"

        # The marker comment is posted via the shared post_marker_comment.sh helper (not an
        # inline tracker-comment call) so the mktemp/footer/rm sequence lives in one place.
        marker_call = 'post_marker_comment.sh" "$ISSUE" "<!-- df-gate-label-failure -->"'
        assert marker_call in bash, \
            f"'{node_id}' must post the <!-- df-gate-label-failure --> marker comment " \
            "(via post_marker_comment.sh) on label failure"

        # the label-failure branch is the `if`'s else-clause, not the artifact-miss else-clause:
        # it must appear between the guard and that guard's own closing `fi`, and it must not
        # contain the artifact-miss marker (spec R6: the two markers are distinct and unrelated).
        # Match "else"/"fi" as whole stripped lines, not bare substrings — a substring search
        # for "fi" would falsely match inside "marker refinement" (the _FOOTER line), which
        # appears before the real closing fi and would truncate the branch too early.
        lines = bash.split("\n")
        guard_line_idx = next(i for i, l in enumerate(lines) if guard in l)
        else_line_idx = next(
            i for i in range(guard_line_idx, len(lines)) if lines[i].strip() == "else"
        )
        fi_line_idx = next(
            i for i in range(else_line_idx, len(lines)) if lines[i].strip() == "fi"
        )
        label_failure_branch = "\n".join(lines[else_line_idx:fi_line_idx])
        assert "df-gate-label-failure" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must post the df-gate-label-failure marker"
        assert "df-refine-failure" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not reuse the df-refine-failure marker " \
            "(that marker means 'no artifact, retry safe' and must not be overloaded)"

        # the label-failure branch warns (log echo) and does not exit 1 (push already succeeded)
        assert "WARNING:" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must log a WARNING echo"
        assert "exit 1" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not exit 1 — the push already " \
            "succeeded and is the node's load-bearing side effect"

        # the warn-advisory comment upsert is || true-guarded. The post_marker_comment.sh
        # invocation is a backslash-continued multi-line command (issue/marker/body args), so
        # the guard is on its final line rather than necessarily the line containing the
        # marker_call text itself — check it appears anywhere after the call starts.
        comment_pos = label_failure_branch.index(marker_call)
        assert "|| true" in label_failure_branch[comment_pos:], \
            f"'{node_id}': the gate-label-failure marker comment must be || true-guarded"

    def test_node_depends_on_and_when_unchanged(self, node_id, prefix, label, noun):
        node = _workflow_nodes()[node_id]
        upstream = "refine" if node_id == "refine-push" else "plan"
        intent = "refine" if node_id == "refine-push" else "plan"
        assert node["depends_on"] == [upstream]
        assert intent in node["when"]
        # 60s (raised from 30s): the label-failure branch's post_marker_comment.sh call adds
        # a get-comments + create/update gh round trip on top of the push+label, and it fires
        # exactly when the GitHub API is slowest (rate exhaustion) — see workflow comment.
        assert node["timeout"] == 60000


def test_dag_validator_passes():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check
    errors = check(_WORKFLOW)
    assert errors == [], "\n".join(errors)


# ── #390: every SPEC_FILE/PLAN_FILE lookup resolves through push_gate_check.sh ──────
# A directory-wide `grep -rl "#N" | head -1` treats any sibling spec that merely mentions
# #N as a candidate (three specs on main mention #196); it archived issue #197's spec
# during #199. push_gate_check.sh only ever prints a file this branch changed under the
# prefix, so the wrong artifact can no longer be planned against, measured, or archived.
_RAW_FIRST_MATCH_GREP = 'grep -rl "#${ISSUE}"'


@pytest.mark.parametrize("node_id,prefixes", [
    ("budget-plan", ["docs/superpowers/specs/"]),
    ("budget-conformance", ["docs/superpowers/specs/"]),
    ("push-and-pr", ["docs/superpowers/specs/", "docs/superpowers/plans/"]),
])
class TestArtifactLookupNodes:
    def test_node_resolves_artifacts_via_push_gate_check(self, node_id, prefixes):
        bash = _workflow_nodes()[node_id]["bash"]
        for prefix in prefixes:
            assert f'push_gate_check.sh" "{prefix}"' in bash, (
                f"'{node_id}' must resolve '{prefix}' through push_gate_check.sh (#390)")

    def test_node_has_no_directory_wide_first_match_grep(self, node_id, prefixes):
        bash = _workflow_nodes()[node_id]["bash"]
        assert _RAW_FIRST_MATCH_GREP not in bash, (
            f"'{node_id}' still uses the first-match grep that picks sibling specs (#390)")


class TestSetupBranchTransfersRefineArtifacts:
    """#387: setup-branch must call transfer_refine_artifacts.sh on its two genuine
    fresh-fork paths (intent=new; intent=continue's no-remote-branch fallback), and
    must NOT call it on branch reuse or on setup-branch-resolve."""

    def test_calls_transfer_script(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        assert "transfer_refine_artifacts.sh" in bash

    def test_transfer_call_is_inside_new_branch_guard(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        lines = bash.split("\n")
        guard_idx = next(i for i, l in enumerate(lines) if 'NEW_BRANCH" = "true"' in l)
        fi_idx = next(i for i in range(guard_idx, len(lines)) if lines[i].strip() == "fi")
        guarded_block = "\n".join(lines[guard_idx:fi_idx])
        assert "transfer_refine_artifacts.sh" in guarded_block, (
            "transfer_refine_artifacts.sh must run only inside the NEW_BRANCH guard"
        )
        assert "|| true" in guarded_block, (
            "the transfer call must be || true-guarded (defense-in-depth on top of "
            "the script's own unconditional exit 0)"
        )

    def test_both_checkout_b_sites_set_new_branch_true(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        assert bash.count('git checkout -b "$BRANCH"') == 2, (
            "setup-branch must retain both checkout -b sites (new-intent path, "
            "continue's no-remote-branch fallback)"
        )
        assert bash.count("NEW_BRANCH=true") == 2, (
            "both checkout -b sites must set NEW_BRANCH=true"
        )

    def test_branch_reuse_path_does_not_set_new_branch_true(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        reuse_idx = bash.index('git fetch origin "$BRANCH" 2>/dev/null && git checkout "$BRANCH"')
        else_idx = bash.index("else", reuse_idx)
        reuse_branch = bash[reuse_idx:else_idx]
        assert "NEW_BRANCH=true" not in reuse_branch

    def test_setup_branch_resolve_untouched(self):
        bash = _workflow_nodes()["setup-branch-resolve"]["bash"]
        assert "transfer_refine_artifacts.sh" not in bash
        assert "NEW_BRANCH" not in bash

    def test_setup_branch_depends_on_and_when_unchanged(self):
        node = _workflow_nodes()["setup-branch"]
        assert node["depends_on"] == ["parse-intent", "fetch-issue"]
        assert node["when"] == "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"

    def test_setup_branch_timeout_raised_for_network_call(self):
        # #387: transfer_refine_artifacts.sh adds a git fetch + two push_gate_check.sh
        # passes over the refine branch's full commit history; 15s was already tight
        # for a fresh checkout -b. Raised to 30s, then to 60s (code-review advisory) to
        # give the fetch/gate-check/checkout/commit sequence headroom against a slow
        # origin without turning a best-effort copy into a hard node failure.
        node = _workflow_nodes()["setup-branch"]
        assert node["timeout"] == 60000

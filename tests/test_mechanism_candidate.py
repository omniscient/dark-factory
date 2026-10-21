"""Tests for scripts/factory_core/mechanism_candidate.py — harness candidate contract and
mechanism lineage (#464). No network; the only subprocess is the CLI exit-code test.
"""
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import state_governance_audit as sga  # noqa: E402
from factory_core import mechanism_candidate as mc  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "mechanism-candidates" / "fixtures"
_MANIFEST = _FIXTURES_DIR / "manifest.json"


# ── Digests ─────────────────────────────────────────────────────────────────────

def test_canonical_digest_determinism():
    a = {"b": [1, "é"], "a": {"y": None, "x": True}}
    b = json.loads('{ "a" : { "x" : true, "y" : null }, "b" : [ 1, "\\u00e9" ] }')
    assert mc.canonical_digest(a) == mc.canonical_digest(b)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", mc.canonical_digest(a))
    assert mc.canonical_digest({"k": "v"}) != mc.canonical_digest({"k": "w"})
    assert mc.DIGEST_RE.pattern == r"^sha256:[0-9a-f]{64}$"


# ── Diff extraction ─────────────────────────────────────────────────────────────

_DOC = "docs/superpowers/notes/t.md"


def _diff(path, minus="-old", plus="+new"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n{minus}\n{plus}\n"


_SMUGGLED = (f"diff --git a/{_DOC} b/{_DOC}\n--- a/{_DOC}\n+++ b/{_DOC}\n@@ -1 +1 @@\n-a\n+b\n"
             "--- a/tests/x.py\n+++ b/tests/x.py\n@@ -1 +1 @@\n-c\n+d\n")

_RENAME = ("diff --git a/docs/superpowers/a.md b/tests/a.md\nsimilarity index 100%\n"
           "rename from docs/superpowers/a.md\nrename to tests/a.md\n")


@pytest.mark.parametrize("diff, match", [
    ("", "diff is empty"),
    ("  \n\t\n", "diff is empty"),
    ("diff --git a/docs/superpowers/a.md b/docs/superpowers/b.md\n@@ -1 +1 @@\n-a\n+b\n", "disagree"),
    ("diff --git a/docs/superpowers/a.md b/docs/superpowers/a.md\n--- a/docs/superpowers/a.md\n"
     "+++ b/tests/a.md\n@@ -1 +1 @@\n-a\n+b\n", "disagree"),
    (_diff("docs/superpowers/../../tests/x.py"), r"'\.\.' segment"),
    ("diff --git a//etc/passwd b//etc/passwd\n@@ -1 +1 @@\n-a\n+b\n", "absolute"),
    (_diff("docs\\superpowers\\x.md"), "backslash"),
    ('diff --git "a/docs/superpowers/\\303\\251.md" "b/docs/superpowers/\\303\\251.md"\n', "C-quoted"),
    ("--- a/docs/superpowers/x.md\n+++ b/docs/superpowers/x.md\n@@ -1 +1 @@\n-a\n+b\n", "before its first"),
    # A second ---/+++/@@ triplet after an exhausted hunk is a new file to `git apply`.
    (_SMUGGLED, "after a hunk"),
    (_diff(_DOC) + "+extra\n", "after a hunk"),  # more body lines than the @@ counts
    (_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,2 +1,2 @@"), "ends inside a hunk"),
    (_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ -1,1 +1,1 @@").replace("-old", "*old"), "malformed hunk line"),
    (_diff(_DOC).replace("@@ -1,1 +1,1 @@", "@@ bogus @@"), "malformed hunk header"),
    (f"diff --git a/{_DOC} b/{_DOC}\nfrobnicate yes\n", "unrecognised diff header line"),
    (f"diff --git a/{_DOC} b/{_DOC}\nGIT binary patch\nliteral 5\n", "binary patches"),
    (_diff(_DOC) + "\n" + _diff("tests/x.py"), "after the blank line"),
])
def test_diff_path_extraction(diff, match):
    with pytest.raises(mc.MechanismCandidateError, match=match):
        mc.extract_diff_paths(diff)


def test_diff_path_extraction_rename_copy_and_hunks():
    assert set(mc.extract_diff_paths(_RENAME)) == {"docs/superpowers/a.md", "tests/a.md"}
    copy_ = ("diff --git a/docs/superpowers/a.md b/docs/superpowers/b.md\nsimilarity index 100%\n"
             "copy from docs/superpowers/a.md\ncopy to docs/superpowers/b.md\n")
    assert set(mc.extract_diff_paths(copy_)) == {"docs/superpowers/a.md", "docs/superpowers/b.md"}
    # A hunk line that looks like a file header is content, not a new file block.
    tricky = _diff(_DOC, "--- a/tests/x.py", "+++ b/tests/x.py")
    assert mc.extract_diff_paths(tricky) == {_DOC: ["--- a/tests/x.py", "+++ b/tests/x.py"]}
    # New file: /dev/null is not a path.
    new = (f"diff --git a/{_DOC} b/{_DOC}\nnew file mode 100644\n--- /dev/null\n+++ b/{_DOC}\n"
           "@@ -0,0 +1 @@\n+x\n")
    assert set(mc.extract_diff_paths(new)) == {_DOC}
    # Two well-formed blocks, a "\\ No newline" marker, and a trailing blank line.
    two = _diff(_DOC) + "\\ No newline at end of file\n" + _diff("scripts/diff_rank.py") + "\n"
    assert set(mc.extract_diff_paths(two)) == {_DOC, "scripts/diff_rank.py"}


def _git_apply_paths(diff, cwd):
    out = subprocess.run(["git", "apply", "--numstat", "-"], input=diff, cwd=cwd,
                         capture_output=True, text=True, check=True).stdout
    return {line.split("\t")[2] for line in out.splitlines()}


def test_diff_paths_match_git_apply(tmp_path):
    """The extractor sees exactly the files `git apply` would touch, or rejects the diff."""
    assert _git_apply_paths(_SMUGGLED, tmp_path) == {_DOC, "tests/x.py"}  # git sees two files
    with pytest.raises(mc.MechanismCandidateError):
        mc.extract_diff_paths(_SMUGGLED)  # ...so the extractor must not silently see one
    two = _diff(_DOC) + _diff("scripts/diff_rank.py")
    tricky = _diff(_DOC, "--- a/tests/x.py", "+++ b/tests/x.py")
    for diff in (two, tricky, _diff(_DOC)):
        assert set(mc.extract_diff_paths(diff)) == _git_apply_paths(diff, tmp_path)


# ── Risk classification ─────────────────────────────────────────────────────────

_SELF_SAFETY = mc.load_safety(str(_REPO_ROOT))
_EMPTY_SAFETY = {"hard_exclude_paths": [], "critical_diff_paths": []}


def _h3_probe_path(prefix):
    return prefix + "x.py" if prefix.endswith(("/", "_")) else prefix


@pytest.mark.parametrize("prefix", mc.H3_PREFIXES)
def test_h3_enumeration_entry(prefix):
    # Empty safety lists: the committed enumeration alone must force H3.
    assert mc.classify_path(_h3_probe_path(prefix), _EMPTY_SAFETY) == "H3"


def test_classify_path_allowlists_safety_lists_and_default_deny():
    assert mc.classify_path("docs/superpowers/specs/x.md", _EMPTY_SAFETY) == "H0"
    assert mc.classify_path("commands/dark-factory-plan.md", _EMPTY_SAFETY) == "H1"
    assert mc.classify_path("commands/dark-factory-plan.md", _EMPTY_SAFETY, tool_frontmatter=True) == "H3"
    assert mc.classify_path("scripts/diff_rank.py", _EMPTY_SAFETY) == "H2"
    assert mc.classify_path("workflows/archon-dark-factory.yaml", _EMPTY_SAFETY) == "H2"
    # Resolved self-target lists (boundary floor ^workflows/, ^commands/) win over H1/H2.
    assert mc.classify_path("workflows/archon-dark-factory.yaml", _SELF_SAFETY) == "H3"
    assert mc.classify_path("commands/dark-factory-plan.md", _SELF_SAFETY) == "H3"
    assert mc.classify_path("scripts/diff_rank.py", _SELF_SAFETY) == "H2"
    custom = {"hard_exclude_paths": ["docs/superpowers/secret/"],
              "critical_diff_paths": [r"^scripts/diff_rank\.py$"]}
    assert mc.classify_path("docs/superpowers/secret/x.md", custom) == "H3"
    assert mc.classify_path("scripts/diff_rank.py", custom) == "H3"
    # Default-deny: nothing in an H0/H1/H2 allowlist.
    for path in ("README.md", "templates/x.md", "scripts/shims/x.sh", "docs/x.md", "commands/sub/x.md"):
        assert mc.classify_path(path, _EMPTY_SAFETY) == "H3", path
    assert mc.classify_risk([], _EMPTY_SAFETY) == "H3"
    # Case-only variants cannot dodge an H3 prefix.
    assert mc.classify_path(".Claude/Settings.json", _EMPTY_SAFETY) == "H3"
    assert mc.classify_path("Tests/test_x.py", _EMPTY_SAFETY) == "H3"
    assert mc.classify_risk(["docs/superpowers/x.md", "scripts/diff_rank.py"], _EMPTY_SAFETY) == "H2"
    assert mc.classify_risk(["commands/a.md"], _EMPTY_SAFETY, ["commands/a.md"]) == "H3"


def test_safety_lists_fail_closed():
    with pytest.raises(mc.MechanismCandidateError, match="list of strings"):
        mc.classify_risk(["docs/superpowers/x.md"], {"hard_exclude_paths": "deploy/", "critical_diff_paths": []})
    with pytest.raises(mc.MechanismCandidateError, match="invalid regex"):
        mc.classify_risk(["docs/superpowers/x.md"], {"hard_exclude_paths": [], "critical_diff_paths": ["("]})


# ── Candidate records ───────────────────────────────────────────────────────────

def _load(name):
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _rehash(record):
    record["candidate_digest"] = mc.candidate_digest_of(record)
    return record


def _record(**overrides):
    """A valid H0 draft record (candidate-probed.json's shape) with overrides applied."""
    r = _load("candidate-probed.json")
    r.update({"candidate_id": "mc-test", "lifecycle_state": "draft", "parent_digest": None,
              "dependency_closure": [], "editable_path_allowlist": [_DOC], "diff": _diff(_DOC),
              "risk_class": "H0"})
    r.update(overrides)
    return _rehash(r)


def test_candidate_digest_recomputed():
    for name in ("candidate-draft.json", "candidate-quarantined.json", "candidate-probed.json",
                 "candidate-composed.json", "candidate-shadow.json", "candidate-advisory.json",
                 "candidate-revoked.json"):
        r = _load(name)
        assert r["candidate_digest"] == mc.canonical_digest({k: r[k] for k in mc.DIGEST_FIELDS}), name
    with pytest.raises(mc.MechanismCandidateError, match="candidate_digest mismatch"):
        mc.validate_candidate(_load("candidate-digest-mismatch.json"), safety=_SELF_SAFETY)
    r = _record()
    r["diff"] = r["diff"].replace("+new", "+newer")  # identity-bearing edit, digest not updated
    with pytest.raises(mc.MechanismCandidateError, match="candidate_digest mismatch"):
        mc.validate_candidate(r, safety=_SELF_SAFETY)
    r = _record()
    r["lifecycle_state"] = "probed"  # bookkeeping edit: digest still valid
    assert mc.validate_candidate(r, safety=_SELF_SAFETY) == "H0"
    for bad in ("sha256:" + "A" * 64, "sha256:" + "a" * 63, "md5:" + "a" * 64,
                "sha256:" + "a" * 64 + "\n"):
        with pytest.raises(mc.MechanismCandidateError, match="sha256"):
            mc.validate_candidate(_record(parent_digest=bad), safety=_SELF_SAFETY)
        with pytest.raises(mc.MechanismCandidateError, match="supersession.superseded_by"):
            mc.validate_candidate(_record(supersession={"superseded_by": bad, "supersedes": []}),
                                  safety=_SELF_SAFETY)
        with pytest.raises(mc.MechanismCandidateError, match=r"supersession.supersedes\[0\]"):
            mc.validate_candidate(_record(supersession={"superseded_by": None, "supersedes": [bad]}),
                                  safety=_SELF_SAFETY)


def test_fixture_diffs_match_git_apply(tmp_path):
    for name in sorted(p.name for p in _FIXTURES_DIR.glob("candidate-*.json")):
        diff = _load(name)["diff"]
        assert set(mc.extract_diff_paths(diff)) == _git_apply_paths(diff, tmp_path), name


def test_unknown_lifecycle_state_and_risk_class():
    with pytest.raises(mc.MechanismCandidateError, match="unknown lifecycle_state"):
        mc.validate_candidate(_record(lifecycle_state="promoted"), safety=_SELF_SAFETY)
    with pytest.raises(mc.MechanismCandidateError, match="unknown risk_class"):
        mc.validate_candidate(_record(risk_class="H4"), safety=_SELF_SAFETY)


def test_risk_class_underclaim_rejected():
    cmd = "commands/dark-factory-plan.md"
    with pytest.raises(mc.MechanismCandidateError, match="underclaims derived class H1"):
        mc.validate_candidate(_record(editable_path_allowlist=[cmd], diff=_diff(cmd), risk_class="H0"),
                              safety=_EMPTY_SAFETY)
    assert mc.validate_candidate(
        _record(editable_path_allowlist=[cmd], diff=_diff(cmd), risk_class="H2"),
        safety=_EMPTY_SAFETY) == "H1"
    frontmatter = _diff(cmd, "-allowed-tools: Read", "+allowed-tools: Read, Bash")
    with pytest.raises(mc.MechanismCandidateError, match="derived risk class H3"):
        mc.validate_candidate(_record(editable_path_allowlist=[cmd], diff=frontmatter, risk_class="H2"),
                              safety=_EMPTY_SAFETY)


def test_h3_unconditional():
    with pytest.raises(mc.MechanismCandidateError, match="derived risk class H3 is forbidden"):
        mc.validate_candidate(_load("candidate-forbidden-path.json"), safety=_SELF_SAFETY)
    # A declared H3 is still rejected: there is no valid H3 record.
    r = _record(editable_path_allowlist=["tests/test_x.py"], diff=_diff("tests/test_x.py"), risk_class="H3")
    with pytest.raises(mc.MechanismCandidateError, match="derived risk class H3 is forbidden"):
        mc.validate_candidate(r, safety=_SELF_SAFETY)
    # rename-to target is classified: an H0 file renamed into tests/ is H3.
    r = _record(editable_path_allowlist=["docs/superpowers/a.md", "tests/a.md"], diff=_RENAME, risk_class="H3")
    with pytest.raises(mc.MechanismCandidateError, match=r"derived risk class H3 is forbidden.*tests/a\.md"):
        mc.validate_candidate(r, safety=_SELF_SAFETY)
    # copy-to target is classified too.
    copy_ = ("diff --git a/docs/superpowers/a.md b/tests/a.md\nsimilarity index 100%\n"
             "copy from docs/superpowers/a.md\ncopy to tests/a.md\n")
    r = _record(editable_path_allowlist=["docs/superpowers/a.md", "tests/a.md"], diff=copy_, risk_class="H3")
    with pytest.raises(mc.MechanismCandidateError, match=r"derived risk class H3 is forbidden.*tests/a\.md"):
        mc.validate_candidate(r, safety=_SELF_SAFETY)
    # A self-target floor match (^workflows/) is H3 even though workflows/ is H2 by allowlist.
    wf = "workflows/archon-dark-factory.yaml"
    with pytest.raises(mc.MechanismCandidateError, match="derived risk class H3 is forbidden"):
        mc.validate_candidate(_record(editable_path_allowlist=[wf], diff=_diff(wf), risk_class="H2"),
                              safety=_SELF_SAFETY)


def test_diff_within_allowlist():
    with pytest.raises(mc.MechanismCandidateError, match="outside editable_path_allowlist"):
        mc.validate_candidate(_load("candidate-scope-widening.json"), safety=_SELF_SAFETY)
    with pytest.raises(mc.MechanismCandidateError, match="outside editable_path_allowlist"):
        mc.validate_candidate(_record(editable_path_allowlist=[]), safety=_SELF_SAFETY)
    # A literal file entry is not a prefix.
    with pytest.raises(mc.MechanismCandidateError, match="outside editable_path_allowlist"):
        mc.validate_candidate(_record(editable_path_allowlist=["docs/superpowers/notes/t"]), safety=_SELF_SAFETY)
    assert mc.validate_candidate(_record(editable_path_allowlist=["docs/superpowers/"]), safety=_SELF_SAFETY) == "H0"
    for bad in ("docs/superpowers/*.md", "docs/**", "docs/superpowers/[ab].md", "docs/?.md"):
        with pytest.raises(mc.MechanismCandidateError, match="glob"):
            mc.validate_candidate(_record(editable_path_allowlist=[bad]), safety=_SELF_SAFETY)
    for bad in ("./docs/superpowers/", "docs//superpowers/", "/docs/superpowers/", "docs/../tests/"):
        with pytest.raises(mc.MechanismCandidateError, match="editable_path_allowlist entry"):
            mc.validate_candidate(_record(editable_path_allowlist=[bad]), safety=_SELF_SAFETY)
    with pytest.raises(mc.MechanismCandidateError, match="diff is empty"):
        mc.validate_candidate(_record(diff=""), safety=_SELF_SAFETY)


def test_no_self_parenting():
    r = _record()
    r["parent_digest"] = r["candidate_digest"]  # parent_digest is not identity-bearing
    with pytest.raises(mc.MechanismCandidateError, match="self-parenting"):
        mc.validate_candidate(r, safety=_SELF_SAFETY, known_digests={r["candidate_digest"]})


def test_parent_and_dependency_resolve():
    with pytest.raises(mc.MechanismCandidateError, match="parent_digest .* does not resolve"):
        mc.validate_candidate(_load("candidate-parent-unresolved.json"), safety=_SELF_SAFETY)
    probed = _load("candidate-probed.json")
    composed = _load("candidate-composed.json")
    with pytest.raises(mc.MechanismCandidateError, match="dependency_closure entry .* does not resolve"):
        mc.validate_candidate(composed, safety=_SELF_SAFETY)
    assert mc.validate_candidate(composed, safety=_SELF_SAFETY,
                                 known_digests={probed["candidate_digest"]}) == "H0"
    draft = _load("candidate-draft.json")  # re-proposal of the quarantined record
    assert draft["parent_digest"] == _load("candidate-quarantined.json")["candidate_digest"]


def test_schema_version():
    with pytest.raises(mc.MechanismCandidateError, match="schema_version must be 1"):
        mc.validate_candidate(_load("candidate-schema-version.json"), safety=_SELF_SAFETY)
    for bad in (True, "1", None):
        with pytest.raises(mc.MechanismCandidateError, match="schema_version must be 1"):
            mc.validate_candidate(_record(schema_version=bad), safety=_SELF_SAFETY)


def test_field_ranges():
    for level in (0, 6, True, 2.0):
        r = _record(side_effect_envelope={"side_effect_level": level})
        with pytest.raises(mc.MechanismCandidateError, match="side_effect_level"):
            mc.validate_candidate(r, safety=_SELF_SAFETY)
    for conf in (-0.1, 1.1, math.nan, math.inf, True, "0.5"):
        with pytest.raises(mc.MechanismCandidateError, match="confidence"):
            mc.validate_candidate(_record(confidence=conf), safety=_SELF_SAFETY)
    for conf in (0, 1, 0.0, 1.0):
        assert mc.validate_candidate(_record(confidence=conf), safety=_SELF_SAFETY) == "H0"

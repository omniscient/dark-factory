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

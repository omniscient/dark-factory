"""Tests for scripts/factory_core/materializer.py — harness candidate materializer, Phase A
(#465). No namespaces, no bwrap, no network and no skips: nothing here executes the
sandbox argv, so the argv-level fixture checks test the translator, not the boundary.
Baselines are throwaway git repositories built with git plumbing under tmp_path; git
never runs against this checkout (Phase B's CI step mounts it read-only under another
uid, where git would refuse it as dubious ownership).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from factory_core import materializer as mz  # noqa: E402
from factory_core import mechanism_candidate as mc  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "materializer" / "fixtures"

_GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "materializer-test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "materializer-test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

_DOC = "docs/superpowers/notes/t.md"
_TOOL = "scripts/tool.py"


def _diff(path, minus, plus):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n{minus}\n{plus}\n"


_DOC_DIFF = _diff(_DOC, "-old", "+new")
_TOOL_DIFF = _diff(_TOOL, "-MAX = 3", "+MAX = 5")
_CLEAN_FILES = {
    "README.md": {"content": "synthetic materializer baseline (#465)\n"},
    _DOC: {"content": "old\n"},
    _TOOL: {"content": "MAX = 3\n"},
    "scripts/run.sh": {"content": "#!/bin/sh\necho ok\n", "mode": "100755"},
}


# ── Config ceilings ─────────────────────────────────────────────────────────────

def test_load_config_reads_the_committed_block():
    cfg = mz.load_config()
    assert set(cfg) == set(mz.CONFIG_KEYS)
    assert all(isinstance(v, int) and v >= 1 for v in cfg.values())
    assert mz.DEFAULT_CONFIG_PATH == _REPO_ROOT / "config" / "config.yaml"


@pytest.mark.parametrize("block, match", [
    (None, "missing or not a mapping"),
    ([], "missing or not a mapping"),
    ({"memory_limit_mb": 1, "max_duration_seconds": 1, "max_workspace_bytes": 1}, "max_nproc"),
    ({"memory_limit_mb": 1, "max_duration_seconds": 1, "max_workspace_bytes": 1, "max_nproc": 1,
      "egress": True}, "unknown materializer keys"),
    ({"memory_limit_mb": True, "max_duration_seconds": 1, "max_workspace_bytes": 1, "max_nproc": 1},
     "memory_limit_mb must be an int"),
    ({"memory_limit_mb": 0, "max_duration_seconds": 1, "max_workspace_bytes": 1, "max_nproc": 1},
     "memory_limit_mb must be an int"),
    ({"memory_limit_mb": "1", "max_duration_seconds": 1, "max_workspace_bytes": 1, "max_nproc": 1},
     "memory_limit_mb must be an int"),
])
def test_config_fails_closed(block, match):
    with pytest.raises(mz.MaterializerError, match=match):
        mz.check_config(block)


def test_load_config_missing_block_fails_closed(tmp_path):
    (tmp_path / "c.yaml").write_text("token_optimization: {}\n", encoding="utf-8")
    with pytest.raises(mz.MaterializerError, match="missing or not a mapping"):
        mz.load_config(tmp_path / "c.yaml")
    with pytest.raises(mz.MaterializerError, match="cannot load"):
        mz.load_config(tmp_path / "absent.yaml")


# ── Envelope policy ─────────────────────────────────────────────────────────────

_CFG = {"memory_limit_mb": 1024, "max_duration_seconds": 100, "max_workspace_bytes": 4096,
        "max_nproc": 16}


def test_resolve_limits_ceilings_apply_downward_only():
    def lim(**res):
        return mz.resolve_limits({"resource_envelope": {"cpu_limit": None, "memory_limit_mb": None,
                                                        "max_duration_seconds": None, **res}}, _CFG)
    assert lim() == {"memory_limit_mb": 1024, "max_duration_seconds": 100, "cpu_seconds": 100,
                     "max_nproc": 16, "max_file_bytes": 4096}
    assert lim(memory_limit_mb=512, max_duration_seconds=30, cpu_limit="2") == {
        "memory_limit_mb": 512, "max_duration_seconds": 30, "cpu_seconds": 60,
        "max_nproc": 16, "max_file_bytes": 4096}
    with pytest.raises(mz.MaterializerError, match="memory_limit_mb 2048 exceeds"):
        lim(memory_limit_mb=2048)
    with pytest.raises(mz.MaterializerError, match="max_duration_seconds 101 exceeds"):
        lim(max_duration_seconds=101)


def test_resolve_limits_does_not_depend_on_the_host_cpu_count(monkeypatch):
    """Host capacity is Phase B's check (#544); the same record must resolve to the same
    limits, hence the same sealed verdict, on any host."""
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    limits = mz.resolve_limits({"resource_envelope": {"cpu_limit": "999", "memory_limit_mb": None,
                                                      "max_duration_seconds": None}}, _CFG)
    assert limits["cpu_seconds"] == 999 * 100


@pytest.mark.parametrize("cpu", ["0", "500m", "1.5", " 1", "1\n", "", "1000", "-1", "2 cores", 2, True])
def test_cpu_limit_unsupported_formats_fail_closed(cpu):
    with pytest.raises(mz.MaterializerError, match="cpu_limit .* is not a supported format"):
        mz.resolve_limits({"resource_envelope": {"cpu_limit": cpu, "memory_limit_mb": None,
                                                 "max_duration_seconds": None}}, _CFG)


@pytest.mark.parametrize("net", [{"egress_allowed": True, "allowed_hosts": []},
                                 {"egress_allowed": False, "allowed_hosts": ["api.anthropic.com"]}])
def test_declared_egress_is_refused(net):
    with pytest.raises(mz.MaterializerError, match="declares egress"):
        mz.check_network({"network_envelope": net})
    mz.check_network({"network_envelope": {"egress_allowed": False, "allowed_hosts": []}})


# ── Throwaway baselines ─────────────────────────────────────────────────────────

def _git(cwd, *args, data=None):
    return subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True,
                          check=True, env=_GIT_ENV).stdout.decode().strip()


def _make_repo(tmp_path, files=None, name="repo", message="baseline"):
    """A git repo whose one commit holds exactly `files` ({path: spec}); the working tree
    stays empty. spec: {"content": str} | {"zeros": n} | {"symlink": target} |
    {"gitlink": sha}, plus an optional "mode"."""
    repo = Path(tmp_path) / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for path, spec in sorted((files if files is not None else _CLEAN_FILES).items()):
        if "gitlink" in spec:
            mode, sha = "160000", spec["gitlink"]
        else:
            if "symlink" in spec:
                mode, data = "120000", spec["symlink"].encode()
            elif "zeros" in spec:
                mode, data = spec.get("mode", "100644"), b"\0" * spec["zeros"]
            else:
                mode, data = spec.get("mode", "100644"), spec["content"].encode()
            sha = _git(repo, "hash-object", "-w", "--stdin", data=data)
        _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}")
    tree = _git(repo, "write-tree")
    commit = _git(repo, "commit-tree", tree, "-m", message)
    return mz.Baseline(commit=commit, repo_root=str(repo))


@pytest.fixture
def scratch_tmp(tmp_path, monkeypatch):
    """Point tempfile (hence mkdtemp workspaces) at an empty dir the test can inspect."""
    d = tmp_path / "scratch"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    monkeypatch.delenv("ARTIFACTS_DIR", raising=False)
    return d


def _record(baseline=None, **overrides):
    """A valid H0 draft record for the clean baseline, overrides applied, digests restamped."""
    r = {
        "schema_version": 1, "candidate_id": "mz-test", "candidate_digest": None,
        "parent_digest": None,
        "baseline": {k: mc.canonical_digest(f"synthetic {k}") for k in mc._BASELINE_FIELDS},
        "source": {"issue": 465, "run_id": "run-materializer-test"},
        "observed_failure": {"description": "synthetic", "evidence_refs": []},
        "invariant_scope": "materializer-test",
        "domain_scope": {"repo": "omniscient/dark-factory", "issue": 465, "pr": None,
                         "agent_role": "implement"},
        "risk_class": "H0", "editable_path_allowlist": ["docs/superpowers/notes/"],
        "diff": _DOC_DIFF, "dependency_closure": [],
        "expected_behavioral_hypothesis": "synthetic",
        "resource_envelope": {"cpu_limit": "1", "memory_limit_mb": 512, "max_duration_seconds": 60},
        "permission_envelope": {"tool_allow": [], "tool_deny": []},
        "network_envelope": {"egress_allowed": False, "allowed_hosts": []},
        "filesystem_envelope": {"read_paths": [], "write_paths": ["docs/superpowers/notes/"]},
        "side_effect_envelope": {"side_effect_level": 1},
        "probe_evidence_refs": [], "contradictions": [], "confidence": 0.5,
        "reviewer": {"actor": "verifier", "identity": None},
        "approver": {"actor": "human", "identity": None},
        "lifecycle_state": "draft",
        "supersession": {"superseded_by": None, "supersedes": []},
        "expiry": {"expires_at": None}, "rollback_handle": None,
    }
    r.update(overrides)
    if baseline is not None:
        r["baseline"] = {**r["baseline"],
                         "repo_digest": mz.compute_repo_digest(baseline.commit, baseline.repo_root)}
    r["candidate_digest"] = mc.candidate_digest_of(r)
    return r


def _h2(baseline=None, **overrides):
    return _record(baseline, risk_class="H2", editable_path_allowlist=[_TOOL], diff=_TOOL_DIFF,
                   filesystem_envelope={"read_paths": [], "write_paths": [_TOOL]}, **overrides)


# ── Baseline digesting ──────────────────────────────────────────────────────────

def test_repo_digest_is_the_canonical_manifest_digest(tmp_path):
    b = _make_repo(tmp_path, {"a.txt": {"content": "x"}, "bin/run": {"content": "y", "mode": "100755"},
                              "l": {"symlink": "a.txt"}})
    expected = mc.canonical_digest({
        "a.txt": {"mode": "100644", "sha256": "sha256:" + hashlib.sha256(b"x").hexdigest()},
        "bin/run": {"mode": "100755", "sha256": "sha256:" + hashlib.sha256(b"y").hexdigest()},
        "l": {"mode": "120000", "target": "a.txt",
              "sha256": "sha256:" + hashlib.sha256(b"a.txt").hexdigest()},
    })
    assert mz.compute_repo_digest(b.commit, b.repo_root) == expected


def test_repo_digest_is_content_addressed_and_ignores_the_working_tree(tmp_path):
    a = _make_repo(tmp_path, name="a", message="one")
    b = _make_repo(tmp_path, name="b", message="two")
    assert a.commit != b.commit
    digest = mz.compute_repo_digest(a.commit, a.repo_root)
    assert digest == mz.compute_repo_digest(b.commit, b.repo_root)
    (Path(a.repo_root) / "untracked.txt").write_text("staged run-time file", encoding="utf-8")
    assert mz.compute_repo_digest(a.commit, a.repo_root) == digest
    exec_bit = {**_CLEAN_FILES, _DOC: {"content": "old\n", "mode": "100755"}}
    c = _make_repo(tmp_path, exec_bit, name="c")
    assert mz.compute_repo_digest(c.commit, c.repo_root) != digest


def test_replace_refs_do_not_change_the_baseline(tmp_path):
    b = _make_repo(tmp_path)
    digest = mz.compute_repo_digest(b.commit, b.repo_root)
    original = _git(b.repo_root, "rev-parse", f"{b.commit}:{_DOC}")
    forged = _git(b.repo_root, "hash-object", "-w", "--stdin", data=b"forged\n")
    _git(b.repo_root, "replace", original, forged)
    assert _git(b.repo_root, "cat-file", "-p", original) == "forged"  # git itself is fooled
    assert mz.compute_repo_digest(b.commit, b.repo_root) == digest


@pytest.mark.parametrize("commit, match", [
    ("HEAD", "full 40-hex"), ("abc123", "full 40-hex"), ("A" * 40, "full 40-hex"),
    ("0" * 40, "git ls-tree failed"),
])
def test_baseline_commit_must_be_a_full_sha_in_the_repo(tmp_path, commit, match):
    b = _make_repo(tmp_path)
    with pytest.raises(mz.MaterializerError, match=match):
        mz.compute_repo_digest(commit, b.repo_root)


def test_tree_sha_is_not_a_commit(tmp_path):
    b = _make_repo(tmp_path)
    tree = _git(b.repo_root, "rev-parse", f"{b.commit}^{{tree}}")
    with pytest.raises(mz.MaterializerError, match="git ls-tree failed"):
        mz.compute_repo_digest(tree, b.repo_root)


@pytest.mark.parametrize("files, match", [
    ({"vendor/lib": {"gitlink": "0123456789abcdef0123456789abcdef01234567"}}, r"gitlink \(submodule\)"),
    ({"m.bin": {"content": "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n"}},
     "Git LFS pointer"),
    ({"d/escape": {"symlink": "../../outside"}}, "resolves outside the snapshot"),
    ({"abs": {"symlink": "/etc/passwd"}}, "resolves outside the snapshot"),
    ({"loop": {"symlink": "loop"}}, "resolves outside the snapshot"),
    # Lexically inside, really outside: deep/up -> .. is the root, so via/.. leaves it.
    ({"deep/up": {"symlink": ".."}, "via": {"symlink": "deep/up/.."}}, "resolves outside the snapshot"),
])
def test_baseline_refusals(tmp_path, files, match):
    b = _make_repo(tmp_path, {**_CLEAN_FILES, **files})
    with pytest.raises(mz.MaterializerError, match=match):
        mz.compute_repo_digest(b.commit, b.repo_root)


def test_resolve_in_tree():
    links = {"d/up": "..", "d/s": "../scripts", "abs": "/x", "a": "b", "b": "a"}
    assert mz.resolve_in_tree("d/s", links) == "scripts"
    assert mz.resolve_in_tree("d/up", links) == ""
    assert mz.resolve_in_tree("d/up/..", links) is None
    assert mz.resolve_in_tree("abs", links) is None
    assert mz.resolve_in_tree("a", links) is None
    assert mz.resolve_in_tree("plain/file", links) == "plain/file"


def test_expanded_size_cap_checked_before_reading(tmp_path):
    b = _make_repo(tmp_path, {**_CLEAN_FILES, "docs/superpowers/zeros.bin": {"zeros": 4096}})
    with pytest.raises(mz.MaterializerError, match="over materializer.max_workspace_bytes"):
        mz.compute_repo_digest(b.commit, b.repo_root, max_bytes=4096)


# ── Workspace construction ──────────────────────────────────────────────────────

def test_build_workspace_applies_the_diff_to_a_fresh_snapshot(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path, {**_CLEAN_FILES, "docs/superpowers/link": {"symlink": "notes/t.md"}})
    ws = mz.build_workspace(_record(b), b)
    try:
        root = Path(ws)
        assert root.parent == scratch_tmp
        assert not mz._is_within(ws, b.repo_root)
        assert (root / _DOC).read_text(encoding="utf-8") == "new\n"
        assert (root / _TOOL).read_text(encoding="utf-8") == "MAX = 3\n"
        assert os.stat(root / "scripts/run.sh").st_mode & stat.S_IXUSR
        assert not os.stat(root / _TOOL).st_mode & stat.S_IXUSR
        assert os.readlink(root / "docs/superpowers/link") == "notes/t.md"
        assert not (root / ".git").exists()
        assert os.listdir(b.repo_root) == [".git"]  # the baseline clone is never written
    finally:
        mz.remove_workspace(ws)
    assert os.listdir(scratch_tmp) == []


def test_diff_that_does_not_apply_fails_and_leaves_nothing(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    r = _record(b, diff=_diff(_DOC, "-not the baseline", "+new"))
    with pytest.raises(mz.MaterializerError, match="does not apply cleanly"):
        mz.build_workspace(r, b)
    assert os.listdir(scratch_tmp) == []


def test_failed_build_reports_a_leaked_workspace(tmp_path, scratch_tmp, monkeypatch):
    b = _make_repo(tmp_path)
    real = mz.remove_workspace

    def stuck(ws):
        raise OSError("device busy")
    monkeypatch.setattr(mz, "remove_workspace", stuck)
    with pytest.raises(mz.MaterializerError,
                       match="(?s)does not apply cleanly.*was not removed: device busy"):
        mz.build_workspace(_record(b, diff=_diff(_DOC, "-not the baseline", "+new")), b)
    for leftover in os.listdir(scratch_tmp):
        real(str(scratch_tmp / leftover))


def test_inherited_git_env_is_ignored(tmp_path, scratch_tmp, monkeypatch):
    other = _make_repo(tmp_path, {"decoy.txt": {"content": "another repository\n"}}, name="other")
    b = _make_repo(tmp_path)
    record = _record(b)
    monkeypatch.setenv("GIT_DIR", str(Path(other.repo_root) / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", other.repo_root)
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "decoy-index"))
    assert mz.compute_repo_digest(b.commit, b.repo_root) == record["baseline"]["repo_digest"]
    ws = mz.build_workspace(record, b)
    try:
        assert (Path(ws) / _DOC).read_text(encoding="utf-8") == "new\n"
        assert os.listdir(other.repo_root) == [".git"]
    finally:
        mz.remove_workspace(ws)


def test_baseline_shaped_like_a_bare_repo_cannot_configure_git_apply(tmp_path, scratch_tmp):
    """Top-level HEAD/objects/refs/config would make git treat the workspace as a bare
    repo and load its config; here that config would turn a whitespace warning into an
    error. safe.bareRepository=explicit keeps git a plain patch tool."""
    hostile = {**_CLEAN_FILES, "HEAD": {"content": "ref: refs/heads/main\n"},
               "objects/info/keep": {"content": ""}, "refs/heads/keep": {"content": ""},
               "config": {"content": "[core]\n\tbare = true\n[apply]\n\twhitespace = error\n"}}
    b = _make_repo(tmp_path, hostile)
    ws = mz.build_workspace(_record(b, diff=_diff(_DOC, "-old", "+new  ")), b)
    try:
        assert (Path(ws) / _DOC).read_text(encoding="utf-8") == "new  \n"
    finally:
        mz.remove_workspace(ws)


def test_workspace_cap_trips_during_construction(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path, {**_CLEAN_FILES, "docs/superpowers/zeros.bin": {"zeros": 8192}})
    with pytest.raises(mz.MaterializerError, match="over materializer.max_workspace_bytes"):
        mz.build_workspace(_record(b), b, config={**_CFG, "max_workspace_bytes": 8192})
    assert os.listdir(scratch_tmp) == []


def test_workspace_cap_counts_the_applied_diff(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    total = sum(len(spec["content"].encode()) for spec in _CLEAN_FILES.values())
    cfg = {**_CFG, "max_workspace_bytes": total}
    mz.remove_workspace(mz.build_workspace(_record(b), b, config=cfg))  # same-size edit fits
    grow = _record(b, diff=_diff(_DOC, "-old", "+new and longer"))
    with pytest.raises(mz.MaterializerError, match="over materializer.max_workspace_bytes"):
        mz.build_workspace(grow, b, config=cfg)
    assert os.listdir(scratch_tmp) == []


def test_workspace_inside_the_clone_or_artifacts_is_refused(tmp_path, monkeypatch):
    b = _make_repo(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", b.repo_root)
    with pytest.raises(mz.MaterializerError, match="must be outside the clone"):
        mz.build_workspace(_record(b), b)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(artifacts))
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))
    with pytest.raises(mz.MaterializerError, match="must be outside the clone"):
        mz.build_workspace(_record(b), b)
    assert os.listdir(artifacts) == []
    assert os.listdir(b.repo_root) == [".git"]


def _smuggle(sep, target=_TOOL):
    """Docs-only by #464's reading, but git also patches `target`: `sep` is a line break
    for str.splitlines() and not for git, so the fake @@ header after it makes the parser
    swallow the real ---/+++ header pair as hunk content."""
    return (f"diff --git a/{_DOC} b/{_DOC}\n--- a/{_DOC}\n+++ b/{_DOC}\n@@ -1,1 +1,1 @@\n-old\n"
            f"+new{sep}@@ -1,1 +1,1 @@\n--- a/{target}\n+++ b/{target}\n@@ -1,1 +1,1 @@\n"
            f"-MAX = 3\n+MAX = 99999\n")


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028",
                                 "\u2029", "\r"])
def test_diff_with_a_non_newline_line_break_is_refused(tmp_path, scratch_tmp, sep):
    b = _make_repo(tmp_path)
    r = _record(b, diff=_smuggle(sep))
    assert sorted(mc.extract_diff_paths(r["diff"])) == [_DOC]  # #464 sees a docs-only H0 diff
    with pytest.raises(mz.MaterializerError, match="line break other than"):
        mz.build_workspace(r, b)
    assert os.listdir(scratch_tmp) == []


def test_applied_paths_must_be_the_validated_paths(tmp_path, scratch_tmp, monkeypatch):
    """The outcome is checked even when a parser differential gets past the line check."""
    b = _make_repo(tmp_path)
    monkeypatch.setattr(mz, "_check_diff_lines", lambda diff: None)
    with pytest.raises(mz.MaterializerError,
                       match=r"changed paths the validated diff does not name: \['scripts/tool.py'\]"):
        mz.build_workspace(_record(b, diff=_smuggle("\x0c")), b)
    assert os.listdir(scratch_tmp) == []


def test_diff_cannot_retarget_a_baseline_symlink(tmp_path, scratch_tmp):
    """A plain hunk on a symlink path rewrites its target. No mode header is involved, so
    #464 accepts it, and the baseline's own symlink check has already run."""
    link = "docs/superpowers/notes/link"
    b = _make_repo(tmp_path, {**_CLEAN_FILES, link: {"symlink": "t.md"}})
    eof = "\\ No newline at end of file\n"
    diff = (f"diff --git a/{link} b/{link}\n--- a/{link}\n+++ b/{link}\n@@ -1 +1 @@\n"
            f"-t.md\n{eof}+/etc/passwd\n{eof}")
    with pytest.raises(mz.MaterializerError, match="added or retargeted the symlink"):
        mz.build_workspace(_record(b, diff=diff), b)
    assert os.listdir(scratch_tmp) == []


def test_verify_applied_refuses_a_removed_symlink(tmp_path, scratch_tmp):
    """Called directly: #464 rejects `deleted file mode 120000`, so no validated diff
    reaches this branch through git. The check does not depend on that."""
    link = "docs/superpowers/notes/link"
    b = _make_repo(tmp_path, {**_CLEAN_FILES, link: {"symlink": "t.md"}})
    r = _record(b)
    entries, blobs, _ = mz._load_baseline(b.commit, b.repo_root, _CFG["max_workspace_bytes"])
    ws = mz.build_workspace(r, b)
    try:
        mz._verify_applied(ws, r, entries, blobs)  # the honest result passes
        os.unlink(os.path.join(ws, *link.split("/")))
        with pytest.raises(mz.MaterializerError, match="removed the symlink"):
            mz._verify_applied(ws, r, entries, blobs)
    finally:
        mz.remove_workspace(ws)


def test_verify_applied_refuses_a_symlink_replaced_by_a_regular_file(tmp_path, scratch_tmp):
    """Called directly, like the removed-symlink case: #464 rejects the mode headers a type
    change needs. A regular file holding the very bytes of the link target is still not the
    baseline symlink, and is refused even though the path is declared and allowlisted."""
    link = "docs/superpowers/notes/link"
    b = _make_repo(tmp_path, {**_CLEAN_FILES, link: {"symlink": "t.md"}})
    r = _record(b)
    r["diff"] += _diff(link, "-t.md", "+t.md")  # only read by extract_diff_paths() here
    assert link in mc.extract_diff_paths(r["diff"])
    entries, blobs, _ = mz._load_baseline(b.commit, b.repo_root, _CFG["max_workspace_bytes"])
    ws = mz.build_workspace(_record(b), b)
    try:
        dest = os.path.join(ws, *link.split("/"))
        os.unlink(dest)
        Path(dest).write_bytes(b"t.md")
        with pytest.raises(mz.MaterializerError, match="replaced the symlink"):
            mz._verify_applied(ws, r, entries, blobs)
    finally:
        mz.remove_workspace(ws)


# ── bwrap argv translation ──────────────────────────────────────────────────────

def _binds(argv):
    out, i, end = [], 0, argv.index("--")
    while i < end:
        if argv[i] in ("--bind", "--ro-bind"):
            out.append(tuple(argv[i:i + 3]))
            i += 3
        else:
            i += 1
    return out


def test_bwrap_argv_exact_order(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    r = _record(b, filesystem_envelope={"read_paths": [], "write_paths": ["docs/superpowers/notes/t.md"]})
    ws = mz.build_workspace(r, b)
    try:
        argv = mz.build_bwrap_argv(r, ws, _CFG, argv=["python3", "-c", "pass"])
        base = [x for d in mz.base_bind_dirs() for x in ("--ro-bind", d, d)]
        assert argv == [
            "bwrap", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup-try", "--unshare-net", "--disable-userns",
            "--die-with-parent", "--new-session",
            "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/home/sandbox",
            "--setenv", "TMPDIR", "/tmp",
            *base,
            "--size", "4096", "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--size", "4096", "--tmpfs", "/home/sandbox",
            "--ro-bind", ws, "/workspace",
            "--bind", os.path.join(ws, "docs", "superpowers", "notes", "t.md"),
            "/workspace/docs/superpowers/notes/t.md",
            "--chdir", "/workspace", "--",
            "prlimit", f"--as={512 * 1024 * 1024}", "--cpu=60", "--nproc=16", "--fsize=4096", "--",
            "python3", "-c", "pass",
        ]
        assert ("--ro-bind", "/", "/") not in _binds(argv)
    finally:
        mz.remove_workspace(ws)


def test_base_bind_dirs_lib64_only_if_present():
    assert mz.base_bind_dirs(lambda p: True) == ["/usr", "/bin", "/lib", "/lib64"]
    assert mz.base_bind_dirs(lambda p: False) == ["/usr", "/bin", "/lib"]


@pytest.mark.parametrize("entry, match", [
    ("/etc", "is absolute"),
    ("docs/superpowers/notes/../../../scripts", r"'\.\.' segment"),
    ("docs\\superpowers", "backslash"),
    ("docs/superpowers/notes/\x01", "control character"),
    ("docs/superpowers/notes//t.md", "not a normalised"),
    ("./docs/superpowers/notes/t.md", "not a normalised"),
    ("", "is empty"),
    ("scripts/tool.py", "not under editable_path_allowlist"),
    ("docs/superpowers/notes/absent.md", "does not exist in the workspace"),
])
def test_write_paths_containment(tmp_path, scratch_tmp, entry, match):
    b = _make_repo(tmp_path)
    r = _record(b, filesystem_envelope={"read_paths": [], "write_paths": [entry]})
    ws = mz.build_workspace(_record(b), b)
    try:
        with pytest.raises(mz.MaterializerError, match=match):
            mz.build_bwrap_argv(r, ws, _CFG)
    finally:
        mz.remove_workspace(ws)


def test_write_path_through_a_symlink_is_refused(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path, {**_CLEAN_FILES, "docs/superpowers/link": {"symlink": "../../scripts"}})
    r = _record(b, editable_path_allowlist=["docs/superpowers/"],
                filesystem_envelope={"read_paths": [], "write_paths": ["docs/superpowers/link"]})
    ws = mz.build_workspace(r, b)
    try:
        with pytest.raises(mz.MaterializerError, match="passes through a symlink"):
            mz.build_bwrap_argv(r, ws, _CFG)
    finally:
        mz.remove_workspace(ws)


def test_write_paths_dedupe_and_directory_entries(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    r = _record(b, filesystem_envelope={"read_paths": [], "write_paths": [
        "docs/superpowers/notes/t.md", "docs/superpowers/notes/", "docs/superpowers/notes/t.md"]})
    ws = mz.build_workspace(r, b)
    try:
        assert mz.validate_write_paths(r, ws) == ["docs/superpowers/notes", "docs/superpowers/notes/t.md"]
    finally:
        mz.remove_workspace(ws)


def test_build_bwrap_argv_refuses_egress_and_bad_limits(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    ws = mz.build_workspace(_record(b), b)
    try:
        with pytest.raises(mz.MaterializerError, match="declares egress"):
            mz.build_bwrap_argv(_record(b, network_envelope={"egress_allowed": True, "allowed_hosts": []}),
                                ws, _CFG)
        with pytest.raises(mz.MaterializerError, match="cpu_limit"):
            mz.build_bwrap_argv(_record(b, resource_envelope={"cpu_limit": "500m", "memory_limit_mb": None,
                                                              "max_duration_seconds": None}), ws, _CFG)
    finally:
        mz.remove_workspace(ws)


# ── materialize() and the artifact bundle ───────────────────────────────────────

_BUNDLE_KEYS = {"candidate_id", "candidate_digest", "risk_class", "baseline", "diff",
                "materialized_at", "provenance", "verdict", "bundle_digest"}


def test_materialize_pass_bundle(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    r = _record(b)
    out = tmp_path / "out"
    bundle = mz.materialize(r, baseline=b, out_dir=out)
    assert set(bundle) == _BUNDLE_KEYS
    assert bundle["verdict"] == {"status": "PASS", "gate_type": "materializer", "findings_count": 0,
                                 "severity": "none", "reason": bundle["verdict"]["reason"]}
    assert "nothing executed" in bundle["verdict"]["reason"]
    assert bundle["candidate_id"] == "mz-test" and bundle["candidate_digest"] == r["candidate_digest"]
    assert bundle["risk_class"] == "H0"
    assert bundle["diff"] == r["diff"]
    assert bundle["baseline"]["commit"] == b.commit
    assert bundle["baseline"]["repo_digest"] == bundle["baseline"]["computed_repo_digest"]
    assert {k: bundle["baseline"][k] for k in mc._BASELINE_FIELDS} == r["baseline"]
    prov = bundle["provenance"]
    assert prov["bwrap_available"] is None
    assert prov["materializer_version"] == mz.MATERIALIZER_VERSION
    assert mc.DIGEST_RE.fullmatch(prov["module_digest"])
    assert mc.DIGEST_RE.fullmatch(prov["invocation_digest"])
    assert prov["limits"]["memory_limit_mb"] == 512
    assert mz.verify_bundle(bundle)
    on_disk = out / "bundle.json"
    assert json.loads(on_disk.read_text(encoding="utf-8")) == bundle
    assert not os.stat(on_disk).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert os.listdir(scratch_tmp) == []
    assert os.listdir(b.repo_root) == [".git"]


def test_materialize_accepts_h2(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    bundle = mz.materialize(_h2(b), baseline=b, out_dir=tmp_path / "out")
    assert bundle["verdict"]["status"] == "PASS"
    assert bundle["risk_class"] == "H2"


def test_bundle_tamper_is_detected(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    bundle = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")
    assert mz.verify_bundle(bundle)
    for key, value in (("diff", "tampered"), ("verdict", {"status": "PASS"}), ("risk_class", "H0x")):
        assert not mz.verify_bundle({**bundle, key: value})
    assert not mz.verify_bundle({k: v for k, v in bundle.items() if k != "bundle_digest"})
    assert not mz.verify_bundle("not a bundle")


def test_invocation_digest_is_independent_of_the_workspace_path(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    one = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "one")
    two = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "two")
    assert one["provenance"]["invocation_digest"] == two["provenance"]["invocation_digest"]


def test_refusals_are_blocked_bundles_with_no_workspace(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    cases = [
        (_record(b, risk_class="H4"), "unknown risk_class"),
        ({"not": "a record"}, "schema_version"),
        ([], "candidate record must be a mapping"),
        (_record(b, parent_digest=mc.canonical_digest("parent")), "does not resolve"),
        (_record(b, resource_envelope={"cpu_limit": "1", "memory_limit_mb": 10 ** 6,
                                       "max_duration_seconds": None}), "exceeds the materializer"),
        (_record(b, diff=_diff(_DOC, "-not the baseline", "+new")), "does not apply cleanly"),
    ]
    for i, (record, match) in enumerate(cases):
        bundle = mz.materialize(record, baseline=b, out_dir=tmp_path / f"out{i}")
        v = bundle["verdict"]
        assert (v["status"], v["findings_count"], v["severity"]) == ("BLOCKED", 1, "high"), match
        assert match in v["reason"]
        assert bundle["provenance"]["bwrap_available"] is None
        assert mz.verify_bundle(bundle)
    assert os.listdir(scratch_tmp) == []


def test_bad_baseline_is_blocked(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    for baseline, match in ((mz.Baseline("HEAD", b.repo_root), "full 40-hex"),
                            (mz.Baseline(b.commit, str(tmp_path / "nope")), "is not a directory"),
                            (("commit", "root"), "must be a Baseline")):
        bundle = mz.materialize(_record(b), baseline=baseline, out_dir=tmp_path / "out")
        assert bundle["verdict"]["status"] == "BLOCKED"
        assert match in bundle["verdict"]["reason"]


def test_repo_root_must_be_the_clone_top_level(tmp_path, scratch_tmp):
    """A subdirectory of the clone as repo_root would move the "inside the clone" checks and
    the adapter safety lookup off the clone root. It is refused, and nothing lands in the
    clone: exit 2 without a bundle for an out-dir inside it, a BLOCKED bundle elsewhere."""
    b = _make_repo(tmp_path)
    r = _record(b)
    sub = Path(b.repo_root) / "sub"
    sub.mkdir()
    nested = mz.Baseline(b.commit, str(sub))
    with pytest.raises(mz.MaterializerError, match="is not the top level of its clone"):
        mz.compute_repo_digest(b.commit, sub)
    with pytest.raises(mz.MaterializerError, match="is inside the baseline clone"):
        mz.materialize(r, baseline=nested, out_dir=Path(b.repo_root) / "o1")
    bundle = mz.materialize(r, baseline=nested, out_dir=tmp_path / "out")
    assert bundle["verdict"]["status"] == "BLOCKED" and mz.verify_bundle(bundle)
    assert "is not the top level of its clone" in bundle["verdict"]["reason"]
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps(r), encoding="utf-8")
    base = ["materialize", "--candidate", str(cand), "--commit", b.commit, "--repo-root", str(sub)]
    env = {"TMPDIR": str(scratch_tmp)}
    inside = _cli(base + ["--out-dir", str(Path(b.repo_root) / "o2")], env)
    assert inside.returncode == 2 and "is inside the baseline clone" in inside.stderr
    outside = _cli(base + ["--out-dir", str(tmp_path / "o3")], env)
    assert outside.returncode == 1 and "is not the top level of its clone" in outside.stderr
    assert (tmp_path / "o3" / "bundle.json").is_file()
    assert sorted(os.listdir(b.repo_root)) == [".git", "sub"] and os.listdir(sub) == []
    assert os.listdir(scratch_tmp) == []


def test_known_digests_resolve_the_parent(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    parent = mc.canonical_digest("parent")
    bundle = mz.materialize(_record(b, parent_digest=parent), baseline=b,
                            out_dir=tmp_path / "out", known_digests=[parent])
    assert bundle["verdict"]["status"] == "PASS"


def test_unexpected_errors_fail_closed(tmp_path, scratch_tmp, monkeypatch):
    b = _make_repo(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mz, "build_bwrap_argv", boom)
    bundle = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")
    assert bundle["verdict"]["status"] == "BLOCKED"
    assert bundle["verdict"]["reason"] == "RuntimeError: boom"
    assert os.listdir(scratch_tmp) == []


def test_verify_bundle_is_false_for_a_bundle_that_cannot_be_digested(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    bundle = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")
    assert mz.verify_bundle(bundle) is True
    for extra in ("\ud800", {"k\udfff": 1}, {1, 2}, b"bytes"):
        assert mz.verify_bundle({**bundle, "extra": extra}) is False
    assert mz.verify_bundle({**bundle, "bundle_digest": "\ud800"}) is False


def test_untrusted_lone_surrogates_still_yield_a_sealed_bundle(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    surrogate_diff = json.loads(json.dumps(_record(b)).replace('"diff": "', '"diff": "\\ud800', 1))
    bad_digest = _record(b)
    bad_digest["baseline"]["repo_digest"] = "\ud800"
    cases = [(surrogate_diff, b, "diff"), (bad_digest, b, "baseline"),
             (_record(b), mz.Baseline("\udcff" * 40, b.repo_root), "baseline")]
    for i, (record, baseline, key) in enumerate(cases):
        bundle = mz.materialize(record, baseline=baseline, out_dir=tmp_path / f"out{i}")
        assert bundle["verdict"]["status"] == "BLOCKED"
        assert mz.verify_bundle(bundle)
        assert json.loads((tmp_path / f"out{i}" / "bundle.json").read_text(encoding="utf-8")) == bundle
    assert bundle["baseline"]["commit"] is None
    assert os.listdir(scratch_tmp) == []


def test_lone_surrogate_candidate_id_is_blocked_never_pass(tmp_path, scratch_tmp):
    """candidate_id is not identity-bearing, so the record still validates with its digest
    intact; the bundle could only say candidate_id null, which must never be a PASS."""
    b = _make_repo(tmp_path)
    record = json.loads(json.dumps(_record(b)).replace('"candidate_id": "mz-test"',
                                                       '"candidate_id": "mz\\ud800"', 1))
    assert record["candidate_id"] == "mz\ud800"
    assert mc.validate_candidate(record, safety=mc.load_safety(b.repo_root)) == "H0"
    bundle = mz.materialize(record, baseline=b, out_dir=tmp_path / "out")
    assert bundle["verdict"]["status"] == "BLOCKED"
    assert "candidate_id is not UTF-8 encodable" in bundle["verdict"]["reason"]
    assert bundle["candidate_id"] is None and mz.verify_bundle(bundle)
    assert os.listdir(scratch_tmp) == []


def test_cleanup_failure_is_blocked_not_raised(tmp_path, scratch_tmp, monkeypatch):
    b = _make_repo(tmp_path)
    real = mz.remove_workspace

    def stuck(ws):
        raise OSError("device busy")
    monkeypatch.setattr(mz, "remove_workspace", stuck)
    bundle = mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")
    assert bundle["verdict"]["status"] == "BLOCKED"
    assert "was not removed: device busy" in bundle["verdict"]["reason"]
    assert mz.verify_bundle(bundle)
    for leftover in os.listdir(scratch_tmp):
        real(str(scratch_tmp / leftover))


def test_materialize_executes_nothing_from_the_workspace(tmp_path, scratch_tmp, monkeypatch):
    b = _make_repo(tmp_path)
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)
    monkeypatch.setattr(mz.subprocess, "run", spy)
    assert mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")["verdict"]["status"] == "PASS"
    assert seen and all(cmd[0] == "git" for cmd in seen)
    assert all(arg not in ("bwrap", "prlimit") for cmd in seen for arg in cmd)


def test_out_dir_resolution(tmp_path, scratch_tmp, monkeypatch):
    b = _make_repo(tmp_path)
    with pytest.raises(mz.MaterializerError, match="out_dir is required"):
        mz.materialize(_record(b), baseline=b)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))
    mz.materialize(_record(b), baseline=b)
    assert (artifacts / "materializer" / "mz-test" / "bundle.json").is_file()
    for bad in ("../escape", "a/b", "", None, 7):
        with pytest.raises(mz.MaterializerError, match="not safe for the default bundle path"):
            mz.materialize({**_record(b), "candidate_id": bad}, baseline=b)
    assert not (tmp_path / "escape").exists()
    with pytest.raises(mz.MaterializerError, match="inside the baseline clone"):
        mz.materialize(_record(b), baseline=b, out_dir=Path(b.repo_root) / "bundle")
    assert os.listdir(b.repo_root) == [".git"]
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(mz.MaterializerError, match="cannot write bundle"):
        mz.materialize(_record(b), baseline=b, out_dir=not_a_dir)
    for unusable in (tmp_path / "nul\0dir", tmp_path / "\ud800"):
        with pytest.raises(mz.MaterializerError, match="not a usable path|cannot write bundle"):
            mz.materialize(_record(b), baseline=b, out_dir=unusable)
    assert os.listdir(scratch_tmp) == []


def test_rematerialize_replaces_the_read_only_bundle(tmp_path, scratch_tmp):
    b = _make_repo(tmp_path)
    mz.materialize(_record(b), baseline=b, out_dir=tmp_path / "out")
    second = mz.materialize(_h2(b), baseline=b, out_dir=tmp_path / "out")
    assert json.loads((tmp_path / "out" / "bundle.json").read_text(encoding="utf-8")) == second
    assert sorted(os.listdir(tmp_path / "out")) == ["bundle.json"]


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _cli(args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "ARTIFACTS_DIR"}
    env["PYTHONPATH"] = str(_REPO_ROOT / "scripts")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "factory_core.materializer", *args],
                          capture_output=True, text=True, env=env, cwd=_REPO_ROOT)


def test_cli_exit_codes(tmp_path):
    b = _make_repo(tmp_path)
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(_record(b)), encoding="utf-8")
    bad.write_text(json.dumps(_record(b, network_envelope={"egress_allowed": True, "allowed_hosts": []})),
                   encoding="utf-8")
    base = ["materialize", "--commit", b.commit, "--repo-root", b.repo_root]
    env = {"TMPDIR": str(tmp_path)}
    ok = _cli(base + ["--candidate", str(good), "--out-dir", str(tmp_path / "o1")], env)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout == "STATUS: PASS\nGATE_TYPE: materializer\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    blocked = _cli(base + ["--candidate", str(bad), "--out-dir", str(tmp_path / "o2")], env)
    assert blocked.returncode == 1
    assert blocked.stdout.startswith("STATUS: BLOCKED\n") and "declares egress" in blocked.stderr
    assert (tmp_path / "o2" / "bundle.json").is_file()
    no_out = _cli(base + ["--candidate", str(good)], env)
    assert no_out.returncode == 2 and "out_dir is required" in no_out.stderr
    unreadable = _cli(base + ["--candidate", str(tmp_path / "absent.json"), "--out-dir", str(tmp_path / "o3")], env)
    assert unreadable.returncode == 2 and "cannot read candidate" in unreadable.stderr
    unwritable = _cli(base + ["--candidate", str(good), "--out-dir", str(good)], env)
    assert unwritable.returncode == 2 and "cannot write bundle" in unwritable.stderr
    non_utf8 = ["materialize", "--commit", os.fsdecode(b"\xff"), "--repo-root", b.repo_root]
    garbled = _cli(non_utf8 + ["--candidate", str(good), "--out-dir", str(tmp_path / "o4")], env)
    assert garbled.returncode == 1 and "full 40-hex" in garbled.stderr
    assert (tmp_path / "o4" / "bundle.json").is_file()
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    nested = _cli(base + ["--candidate", str(deep), "--out-dir", str(tmp_path / "o5")], env)
    assert nested.returncode == 2 and "cannot read candidate" in nested.stderr
    assert not (tmp_path / "o5").exists()
    huge = tmp_path / "huge.json"
    huge.write_bytes(b" " * (mz._MAX_CANDIDATE_BYTES + 1))
    oversized = _cli(base + ["--candidate", str(huge), "--out-dir", str(tmp_path / "o6")], env)
    assert oversized.returncode == 2 and "cannot read candidate" in oversized.stderr
    assert not (tmp_path / "o6").exists()


# ── Fixtures (evals/materializer/fixtures/) ─────────────────────────────────────

_MANIFEST = json.loads((_FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
_BASELINES = json.loads((_FIXTURES_DIR / "baseline-manifest.json").read_text(encoding="utf-8"))
_ARGV_FIXTURES = ("attack-credential-canary", "attack-network-testnet", "attack-docker-socket",
                  "attack-path-escape", "attack-git-mutation", "attack-resource-exhaustion")


def _baseline_files(name):
    spec = _BASELINES[name]
    return {**(_baseline_files(spec["extends"]) if spec["extends"] else {}), **spec["files"]}


def _fixture(name):
    return json.loads((_FIXTURES_DIR / f"candidate-{name}.json").read_text(encoding="utf-8"))


def test_fixture_set_is_complete():
    names = set(_MANIFEST)
    assert len(names) == 18
    assert len([n for n in names if n.startswith("attack-")]) == 14
    assert len([n for n in names if n.startswith("refuse-")]) == 4
    on_disk = {p.name[len("candidate-"):-len(".json")] for p in _FIXTURES_DIR.glob("candidate-*.json")}
    assert on_disk == names
    assert _baseline_files("clean") == _CLEAN_FILES
    assert {n for n, e in _MANIFEST.items() if "A" in e["phases"]} == names - {"refuse-h2-run"}
    assert all(_MANIFEST[n]["phase_a_check"] == "argv" for n in _ARGV_FIXTURES)


@pytest.mark.parametrize("name", sorted(_MANIFEST))
def test_fixture_digests_are_current(tmp_path, name):
    record = _fixture(name)
    assert record["candidate_id"] == f"mz-{name}"
    assert record["candidate_digest"] == mc.candidate_digest_of(record)
    b = _make_repo(tmp_path, _baseline_files(_MANIFEST[name]["baseline"]))
    try:
        computed = mz.compute_repo_digest(b.commit, b.repo_root)
    except mz.MaterializerError:
        return  # a refused baseline has no digest to match
    if name == "refuse-digest-mismatch":
        assert record["baseline"]["repo_digest"] != computed
    else:
        assert record["baseline"]["repo_digest"] == computed


@pytest.mark.parametrize("name", sorted(n for n, e in _MANIFEST.items() if "A" in e["phases"]))
def test_phase_a_fixture_verdicts(tmp_path, scratch_tmp, name):
    entry = _MANIFEST[name]
    b = _make_repo(tmp_path, _baseline_files(entry["baseline"]))
    cfg = {**mz.load_config(), **entry.get("config", {})}
    bundle = mz.materialize(_fixture(name), baseline=b, out_dir=tmp_path / "out", config=cfg)
    assert bundle["verdict"]["status"] == entry["materialize"], bundle["verdict"]["reason"]
    if "error_match" in entry:
        assert re.search(entry["error_match"], bundle["verdict"]["reason"]), bundle["verdict"]["reason"]
    if entry["materialize"] == "BLOCKED":
        assert bundle["provenance"]["invocation_digest"] is None  # refused before any argv
    assert mz.verify_bundle(bundle)
    assert os.listdir(scratch_tmp) == []
    assert os.listdir(b.repo_root) == [".git"]


def test_refuse_h2_run_fixture_materializes_as_h2(tmp_path, scratch_tmp):
    """materialize() accepts H2; the run() refusal this fixture is named for is Phase B."""
    b = _make_repo(tmp_path, _baseline_files("clean"))
    bundle = mz.materialize(_fixture("refuse-h2-run"), baseline=b, out_dir=tmp_path / "out")
    assert (bundle["verdict"]["status"], bundle["risk_class"]) == ("PASS", "H2")


_HOSTILE_ENV = {
    "GH_TOKEN": "CANARY-465-gh", "ANTHROPIC_API_KEY": "CANARY-465-anthropic",
    "CLAUDE_CODE_OAUTH_TOKEN": "CANARY-465-oauth", "DOCKER_HOST": "tcp://docker-socket-proxy:2375",
    "BUILDKIT_HOST": "tcp://buildkitd:8372", "BUILDKIT_PORT": "8372", "SEQ_URL": "http://seq:5341",
    "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
}


@pytest.mark.parametrize("name", _ARGV_FIXTURES)
def test_execution_fixture_argv(tmp_path, scratch_tmp, monkeypatch, name):
    """Argv-level only: checks the translator, not that the boundary holds (Phase B)."""
    home, artifacts = tmp_path / "home", tmp_path / "artifacts"
    for root in (home / ".config" / "gh", home / ".claude", artifacts):
        root.mkdir(parents=True)
        (root / "canary.txt").write_text("CANARY-465", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))
    for k, v in _HOSTILE_ENV.items():
        monkeypatch.setenv(k, v)
    record = _fixture(name)
    b = _make_repo(tmp_path, _baseline_files(_MANIFEST[name]["baseline"]))
    cfg = mz.load_config()
    ws = mz.build_workspace(record, b, config=cfg)
    try:
        argv = mz.build_bwrap_argv(record, ws, cfg)
        pre = argv[:argv.index("--")]
        joined = "\0".join(a.replace(ws, "<ws>") for a in argv)  # tmp names never match
        setenv = [pre[i + 1] for i, a in enumerate(pre) if a == "--setenv"]
        binds = _binds(argv)
        assert argv[0] == "bwrap" and setenv == ["PATH", "HOME", "TMPDIR"]
        assert {src for _, src, _ in binds} <= set(mz.base_bind_dirs()) | {ws} | {
            os.path.join(ws, *rel.split("/")) for rel in mz.validate_write_paths(record, ws)}
        if name == "attack-credential-canary":
            assert pre.index("--clearenv") < pre.index("--setenv")
            for needle in ("CANARY-465", str(home), str(artifacts), ".config/gh", ".claude",
                           ".gitconfig", ".archon", b.repo_root):
                assert needle not in joined, needle
        elif name == "attack-network-testnet":
            assert "--unshare-net" in pre and "--share-net" not in argv
        elif name == "attack-docker-socket":
            assert "docker" not in joined.lower() and "buildkit" not in joined.lower()
            for k, v in _HOSTILE_ENV.items():
                assert k not in joined and v not in joined, k
        elif name == "attack-path-escape":
            assert [(s, d) for f, s, d in binds if f == "--bind"] == [
                (os.path.join(ws, *_DOC.split("/")), f"/workspace/{_DOC}")]
            assert ("--ro-bind", "/", "/") not in binds
            assert (pre.index("--tmpfs") < pre.index(ws) < pre.index("--bind")
                    < pre.index("--chdir"))
        elif name == "attack-git-mutation":
            assert not (Path(ws) / ".git").exists()
            assert not any(a.endswith("/.git") or "/.git/" in a or ".gitconfig" in a for a in argv)
            assert b.repo_root not in joined
        elif name == "attack-resource-exhaustion":
            tail = argv[argv.index("--") + 1:]
            assert tail == ["prlimit", f"--as={256 * 1024 * 1024}", "--cpu=60",
                            f"--nproc={cfg['max_nproc']}", f"--fsize={cfg['max_workspace_bytes']}", "--"]
    finally:
        mz.remove_workspace(ws)

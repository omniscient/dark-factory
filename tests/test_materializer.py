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
            "--unshare-cgroup-try", "--unshare-net", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/home/sandbox",
            "--setenv", "TMPDIR", "/tmp",
            *base,
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/home/sandbox",
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

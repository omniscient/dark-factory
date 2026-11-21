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

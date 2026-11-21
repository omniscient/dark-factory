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

"""Harness candidate materializer, Phase A (#465, epic #463).

Turns a mechanism_candidate.py-valid record into a disposable workspace built from an
exact, digested baseline commit with the candidate's diff applied, writes an immutable
artifact bundle, and translates the record's envelopes into the bwrap argument list the
Phase B executor (#544) will launch. Phase A executes nothing from the workspace, needs
no namespaces, and makes no claim that the sandbox boundary holds: its argv is checked
by tests, never run. Candidate payloads are untrusted data; every check fails closed.
Spec: docs/superpowers/specs/2026-10-22-harness-candidate-materializer-design.md

Usage:
    PYTHONPATH=scripts python3 -m factory_core.materializer materialize \\
        --candidate CANDIDATE.json --commit <40-hex sha> --repo-root <clone> \\
        [--out-dir DIR] [--known-digest sha256:...]...

Exit 0 on a PASS bundle, 1 on a BLOCKED bundle, 2 when no bundle can be written (an
unreadable candidate file, no --out-dir and no $ARTIFACTS_DIR, an unsafe candidate_id
for the default path, an output directory inside the baseline clone, or a write error).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import mechanism_candidate as mc
from . import verdict as _verdict


class MaterializerError(Exception):
    pass


MATERIALIZER_VERSION = "1"
GATE_TYPE = "materializer"

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
CONFIG_KEYS = ("memory_limit_mb", "max_duration_seconds", "max_workspace_bytes", "max_nproc")

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")  # run_record.py's _SAFE_RUN_ID_RE
_CPU_LIMIT_RE = re.compile(r"[1-9][0-9]{0,2}")  # bare integer CPU count, 1-999
_LFS_HEADER = b"version https://git-lfs.github.com/spec/"
_FILE_MODES = frozenset({"100644", "100755"})
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
_MAX_SYMLINK_HOPS = 40
_ACCEPTED_RISK = ("H0", "H1", "H2")  # materialize(); Phase B's run() accepts H0-H1 only

SANDBOX_WORKSPACE = "/workspace"
SANDBOX_HOME = "/home/sandbox"
SANDBOX_PATH = "/usr/bin:/bin"
BASE_BIND_DIRS = ("/usr", "/bin", "/lib")
OPTIONAL_BASE_BIND_DIRS = ("/lib64",)


@dataclass(frozen=True)
class Baseline:
    """The pinned baseline: a full commit SHA in the clone at repo_root. Caller-supplied,
    because a candidate record carries only digests, never a git ref."""
    commit: str
    repo_root: str


def _fail(msg):
    raise MaterializerError(msg)


def _error_text(exc):
    if isinstance(exc, (MaterializerError, mc.MechanismCandidateError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _is_within(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


# ── Config ceilings ─────────────────────────────────────────────────────────────

def check_config(block):
    """The four `materializer:` ceilings, each an int >= 1; anything else fails closed."""
    if not isinstance(block, dict):
        _fail("config: the materializer block is missing or not a mapping")
    unknown = sorted(set(block) - set(CONFIG_KEYS))
    if unknown:
        _fail(f"config: unknown materializer keys {unknown}")
    for key in CONFIG_KEYS:
        if not mc._is_int(block.get(key)) or block[key] < 1:
            _fail(f"config: materializer.{key} must be an int >= 1, got {block.get(key)!r}")
    return {k: block[k] for k in CONFIG_KEYS}


def load_config(path=None):
    """The `materializer:` block of config/config.yaml (this checkout's, by default)."""
    import yaml
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"config: cannot load {p}: {exc}")
    return check_config(data.get("materializer") if isinstance(data, dict) else None)


def _config(config):
    return load_config() if config is None else check_config(config)

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


# ── Envelope policy ─────────────────────────────────────────────────────────────

def check_network(candidate):
    """Declared egress is refused, never downgraded: the sandbox has no network."""
    net = candidate["network_envelope"]
    if net["egress_allowed"] or net["allowed_hosts"]:
        _fail("network_envelope declares egress (egress_allowed or allowed_hosts); the "
              "sandbox has no network by construction, and declared egress is refused, "
              "never silently downgraded")


def resolve_limits(candidate, config):
    """Per-process rlimit values. A candidate may lower a config ceiling, never raise it."""
    config = check_config(config)
    res = candidate["resource_envelope"]
    limits = {}
    for key in ("memory_limit_mb", "max_duration_seconds"):
        declared = res.get(key)
        if declared is None:
            limits[key] = config[key]
        elif not mc._is_int(declared) or declared < 1:
            _fail(f"resource_envelope.{key} must be an int >= 1 or null, got {declared!r}")
        elif declared > config[key]:
            _fail(f"resource_envelope.{key} {declared} exceeds the materializer.{key} "
                  f"ceiling {config[key]}")
        else:
            limits[key] = declared
    cpu = res.get("cpu_limit")
    if cpu is None:
        cpus = 1
    elif isinstance(cpu, str) and _CPU_LIMIT_RE.fullmatch(cpu):
        cpus = int(cpu)
    else:
        _fail(f"resource_envelope.cpu_limit {cpu!r} is not a supported format "
              f"(a bare integer CPU count from 1 to 999)")
    limits["cpu_seconds"] = cpus * limits["max_duration_seconds"]
    limits["max_nproc"] = config["max_nproc"]
    limits["max_file_bytes"] = config["max_workspace_bytes"]
    return limits


# ── Baseline digesting ──────────────────────────────────────────────────────────

def _check_baseline(baseline):
    if not isinstance(baseline, Baseline):
        _fail("baseline must be a Baseline(commit, repo_root)")
    if not isinstance(baseline.commit, str) or not _COMMIT_RE.fullmatch(baseline.commit):
        _fail(f"baseline.commit must be a full 40-hex commit SHA, got {baseline.commit!r}")
    if not isinstance(baseline.repo_root, str) or not os.path.isdir(baseline.repo_root):
        _fail(f"baseline.repo_root {baseline.repo_root!r} is not a directory")


def _git_env():
    """os.environ minus every inherited GIT_* variable (GIT_DIR, GIT_OBJECT_DIRECTORY,
    GIT_INDEX_FILE, ... would silently retarget git at another repository), with replace
    refs disabled (refs/replace/* would let cat-file serve other content for a sha) and
    no lazy fetch (a partial-clone baseline must never reach the network)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _git(repo_root, *args, data=None):
    try:
        proc = subprocess.run(["git", "-C", str(repo_root), *args], input=data,
                              capture_output=True, check=False, env=_git_env())
    except OSError as exc:
        _fail(f"cannot run git: {exc}")
    if proc.returncode != 0:
        _fail(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _cap_msg(size, cap):
    return f"workspace expands to {size} bytes, over materializer.max_workspace_bytes ({cap})"


def _list_tree(commit, repo_root):
    out = _git(repo_root, "ls-tree", "-r", "-l", "-z", "--full-tree", f"{commit}^{{commit}}")
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, raw = rec.partition(b"\t")
        mode, _kind, sha, size = meta.decode("ascii").split()
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            _fail(f"baseline path {raw!r} is not UTF-8")
        try:
            mc._check_path_text(path, "baseline path")
        except mc.MechanismCandidateError as exc:
            _fail(str(exc))
        if any(part.casefold() == ".git" for part in path.split("/")):
            _fail(f"baseline path {path!r} has a .git component")
        if mode == _GITLINK_MODE:
            _fail(f"baseline entry {path!r} is a gitlink (submodule); submodules are "
                  f"refused, never flattened")
        if mode not in _FILE_MODES and mode != _SYMLINK_MODE:
            _fail(f"baseline entry {path!r} has unsupported mode {mode}")
        if not size.isdigit():  # ls-tree -l prints BAD for a blob it cannot read
            _fail(f"baseline entry {path!r} has no readable blob (ls-tree size {size!r})")
        entries.append((path, mode, sha, int(size)))
    if not entries:
        _fail(f"baseline commit {commit} has an empty tree")
    return entries


def _read_blobs(repo_root, entries, max_bytes):
    """Blob contents by path, via one `git cat-file --batch`. The expanded size is checked
    against the cap from the tree listing before any content is read."""
    total = sum(size for _, _, _, size in entries)
    if total > max_bytes:
        _fail(_cap_msg(total, max_bytes))
    out = _git(repo_root, "cat-file", "--batch",
               data="".join(f"{sha}\n" for _, _, sha, _ in entries).encode("ascii"))
    blobs, pos = {}, 0
    try:
        for path, _, sha, size in entries:
            nl = out.index(b"\n", pos)
            if out[pos:nl].decode("ascii").split() != [sha, "blob", str(size)]:
                _fail(f"git cat-file returned an unexpected header for {path!r}")
            blobs[path] = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
    except ValueError:
        _fail("git cat-file output ended early")
    return blobs


def resolve_in_tree(path, links):
    """Resolve repo-relative `path` against a virtual tree whose symlinks are `links`
    ({path: target}), component by component like realpath. None when the walk leaves
    the root, hits an absolute target, or exceeds the hop limit (a loop)."""
    parts, todo, hops = [], path.split("/"), 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
        target = links.get("/".join(parts))
        if target is not None:
            hops += 1
            if hops > _MAX_SYMLINK_HOPS or target.startswith("/"):
                return None
            parts.pop()
            todo = target.split("/") + todo
    return "/".join(parts)


def _load_baseline(commit, repo_root, max_bytes):
    """(entries, blobs, manifest) for the pinned commit. Refusal order: gitlink, then
    Git LFS pointer, then a symlink resolving outside the snapshot."""
    entries = _list_tree(commit, repo_root)
    blobs = _read_blobs(repo_root, entries, max_bytes)
    links = {}
    for path, mode, _, _ in entries:
        if mode == _SYMLINK_MODE:
            try:
                links[path] = blobs[path].decode("utf-8")
            except UnicodeDecodeError:
                _fail(f"baseline symlink {path!r} has a non-UTF-8 target")
        elif blobs[path].startswith(_LFS_HEADER):
            _fail(f"baseline entry {path!r} is a Git LFS pointer; LFS content is refused, "
                  f"never fetched")
    for path, target in sorted(links.items()):
        if resolve_in_tree(path, links) is None:
            _fail(f"baseline symlink {path!r} -> {target!r} resolves outside the snapshot "
                  f"(or loops)")
    manifest = {}
    for path, mode, _, _ in entries:
        entry = {"mode": mode, "sha256": "sha256:" + hashlib.sha256(blobs[path]).hexdigest()}
        if mode == _SYMLINK_MODE:
            entry["target"] = links[path]
        manifest[path] = entry
    return entries, blobs, manifest


def compute_repo_digest(commit, repo_root, *, max_bytes=None):
    """repo_digest of a pinned commit: canonical_digest over {path: {mode, sha256[, target]}}
    from `git ls-tree -r` + `git cat-file` (never `git archive`, never the working tree)."""
    _check_baseline(Baseline(commit, str(repo_root)))
    cap = load_config()["max_workspace_bytes"] if max_bytes is None else max_bytes
    _, _, manifest = _load_baseline(commit, repo_root, cap)
    return mc.canonical_digest(manifest)

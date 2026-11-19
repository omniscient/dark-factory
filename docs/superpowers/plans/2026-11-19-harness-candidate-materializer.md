# Harness Candidate Materializer (Phase A) Implementation Plan

**Issue:** #465

> **For agentic workers:** this plan is executed task by task by the factory's implement
> phase. Steps use checkbox (`- [ ]`) syntax for tracking. Run every command from the
> repository root. Each task is TDD: write the failing test, watch it fail, implement,
> watch it pass, commit.

**Goal:** Ship Phase A of the harness candidate materializer. Given a
`mechanism_candidate.py`-valid record and a caller-supplied `Baseline(commit, repo_root)`,
`materialize()` re-validates the record, recomputes the baseline `repo_digest` from the pinned
commit, builds a disposable `mkdtemp()` workspace with the candidate diff applied, translates the
envelopes into the `bwrap` argv that Phase B (#544) will launch, writes a self-describing
artifact bundle, and removes the workspace. It executes nothing and needs no namespaces.

**Architecture:** One new module, `scripts/factory_core/materializer.py`. It has an importable
API and a thin `argparse` CLI, the same shape as `mechanism_candidate.py`. It imports
`mechanism_candidate` (`validate_candidate`, `canonical_digest`, `load_safety`,
`candidate_digest_of` and the private path helpers `_check_path_text`, `_normalise`,
`_contained`, `_is_int`) and `verdict` (`format_verdict`, `parse_verdict`) and re-implements
neither. The baseline is read with `git ls-tree -r -l -z` and one `git cat-file --batch`, never
`git archive` and never the working tree. Ceilings live in a new top-level `materializer:` block
appended to the end of `config/config.yaml`, with one Layer 1 row per key in
`docs/configuration.md`. Eighteen synthetic fixtures live under `evals/materializer/fixtures/`.

**Tech Stack:** Python 3.12 standard library, PyYAML (already a CI dependency), git plumbing
commands, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-harness-candidate-materializer-design.md` (the
design of record for both phases; this plan covers **Phase A only**).

---

## Before you start

- **Branch base.** The `feat/issue-465-*` branch forks from `main`. `main` (at or after
  `f457c2c`) has `scripts/docs_refs_check.py`, `docs/configuration.md`, and the `dag-check`
  CI step that runs the docs check. The refine branch this plan was written on forked from an
  older `main` (`e0f5b41`) that has neither. Task 0 checks they exist before any step relies on
  them.
- **Do not archive the spec.** When the PR-push step archives this plan into `docs/archive/`,
  the spec must stay at `docs/superpowers/specs/2026-10-22-harness-candidate-materializer-design.md`.
  It is the design of record for the Phase B follow-up #544, and this plan's docs row links to
  it.
- **Scope checks use two-dot diffs** (`git diff origin/main HEAD -- <path>`). The three-dot
  form also lists files `main` changed after the branch forked, which shows up as false
  out-of-scope hits.
- **Phase B is out of scope.** Do **not** add `preflight_probe()`, `run()`, the CLI's
  `run -- <argv>` subcommand, the `live_sandbox` marker, any `tests/conftest.py` change, or any
  `.github/workflows/ci.yml` change. Do not touch `run-compose.yml`, `Dockerfile`, `deploy/**`,
  `.github/workflows/publish.yml`, `.claude/**` or `.factory/**`. The conformance gate excises
  Phase B work that appears in a #465 change.
- **Tests never run git against this checkout.** Phase B's CI step will mount the checkout
  read-only under a different uid, and git refuses that as dubious ownership. Every baseline in
  `tests/test_materializer.py` is a throwaway repo under `tmp_path`, built with git plumbing.
  Do not add a test that digests `_REPO_ROOT`.
- **No skips.** Every Phase A test runs in `ci.yml`'s existing `tests` job, with no `bwrap` and
  no namespaces. Do not add `pytest.skip`/`skipif`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/factory_core/materializer.py` | Create | `Baseline`, config ceilings, envelope policy (`check_network`, `resolve_limits`), baseline digesting (`compute_repo_digest`, `resolve_in_tree`), workspace construction (`build_workspace`, `remove_workspace`), argv translation (`validate_write_paths`, `base_bind_dirs`, `build_bwrap_argv`, `invocation_digest`), the bundle (`materialize`, `seal_bundle`, `verify_bundle`, `resolve_out_dir`), CLI `materialize` |
| `tests/test_materializer.py` | Create | Unit tests: no namespaces, no skips, throwaway git baselines only |
| `config/config.yaml` | Modify (append at end) | New top-level `materializer:` block with 4 leaf keys |
| `docs/configuration.md` | Modify | Leaf-key count 95 → 99, a `### materializer` Layer 1 table (4 rows) |
| `evals/materializer/fixtures/manifest.json` | Create | Expected verdict and phase for each of the 18 fixtures |
| `evals/materializer/fixtures/baseline-manifest.json` | Create | The synthetic baselines (`clean` plus 5 variants) that the fixtures name |
| `evals/materializer/fixtures/candidate-<fixture>.json` | Create (18, generated) | One full candidate record per fixture, with real digests |

`config/config.yaml` gets the block **appended at the end of the file**, after
`token_optimization:`, so no existing line moves. `docs/configuration.md` and older specs cite
`config/config.yaml:<line>`.

## Design decisions fixed by this plan

These are the spec's open details, settled so that the code below is unambiguous:

- **Ceilings** (`config/config.yaml`): `memory_limit_mb: 4096`, `max_duration_seconds: 1800`,
  `max_workspace_bytes: 268435456` (256 MiB; this repo's tree is about 10 MB), `max_nproc: 128`.
  If a candidate declares a value above a ceiling, the result is `BLOCKED`. The value is never
  clamped. `null` means the ceiling.
- **`cpu_limit`**: the supported format is a bare integer CPU count from `"1"` to `"999"`.
  `RLIMIT_CPU` = that count × the resolved `max_duration_seconds`. `null` means one CPU. Any
  other string or type is `BLOCKED`.
- **`RLIMIT_FSIZE`** = `max_workspace_bytes`. `RLIMIT_AS` = `memory_limit_mb` × 1 MiB.
- **Size cap**: checked against the expanded sizes in the tree listing before any blob is read,
  again while writing the snapshot, and again after `git apply` (a host-side walk that never
  follows symlinks).
- **Digest manifest**: `{path: {"mode", "sha256"}}`, plus `"target"` for a symlink, hashed with
  `canonical_digest()`. It is content-addressed: two commits with identical trees have the same
  `repo_digest`.
- **Refusal order** (Requirement 8): gitlink, then LFS pointer, then a symlink that resolves
  outside the snapshot. Symlinks are resolved component by component against the virtual tree,
  so `deep/up -> ..` followed by `via -> deep/up/..` is caught. A lexical check would miss it.
- **Git hygiene**: every git call drops inherited `GIT_*` variables (an inherited `GIT_DIR`
  would retarget `git -C <clone>` at another repository). Every call also sets
  `GIT_NO_REPLACE_OBJECTS=1` (a `refs/replace/*` entry would make `cat-file` serve forged
  content) and `GIT_NO_LAZY_FETCH=1` (a partial-clone baseline must never fetch). `git apply`
  additionally sets:
  - `GIT_CEILING_DIRECTORIES` to the workspace's parent;
  - `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`, so no host `apply.*` config
    changes the result;
  - `-c safe.bareRepository=explicit`, because a baseline with top-level `HEAD`, `objects/`,
    `refs/` and `config` would otherwise be used as a bare repository and its `config` loaded.

  The prototype confirmed that last case: by default, an injected `apply.whitespace=error`
  took effect. The `ls-tree`/`cat-file` calls keep the host's global config, because
  production may rely on a global `safe.directory`.
- **Same blobs**: `materialize()` writes the workspace from the in-memory blobs it just
  digested. It never re-reads them.
- **`write_paths`**: each entry is checked with #464's own path rules (`_check_path_text`,
  `_normalise`). It must be under `editable_path_allowlist` (`_contained`), exist in the
  workspace, have no symlink component, and resolve inside the workspace. The list is
  de-duplicated and sorted, so parents bind before children. The bind source is always
  `<workspace>/<entry>`.
- **Bundle**: `bundle.json`, written atomically through a unique `mkstemp` temp file and set
  read-only (`0444`). A re-materialization replaces it. Every untrusted string copied into the
  bundle (`candidate_id`, `candidate_digest`, `diff`, the declared baseline digests, `commit`)
  goes through `_text()`, which turns a non-UTF-8-encodable string (a JSON lone surrogate, or a
  surrogateescape argv byte) into `null`. The reason text is backslash-escaped. So
  `canonical_digest()` can always seal a refusal, and a BLOCKED bundle is always written. A
  failure to remove the workspace is reported in a BLOCKED reason, never raised. That includes a
  failed build: `_build_workspace` re-raises its original error with the leaked path appended.
  An unusable bundle path (NUL byte, unencodable name) is a `MaterializerError`. The CLI treats a
  candidate file it cannot load (unreadable, invalid JSON, or nested deeply enough to raise
  `RecursionError`) as exit 2. `provenance` = `materializer_version`, `module_digest` (sha256 of
  `materializer.py`), `bwrap_available: null`, `invocation_digest` (the argv with the random
  workspace path normalised to `<workspace>`, or `null` if refused before any argv), and
  `limits`. `baseline` = the declared block, plus `commit` and `computed_repo_digest`.
  `verdict` = `parse_verdict(format_verdict(...))` plus `reason`: PASS → 0 findings, severity
  `none`; BLOCKED → 1 finding, severity `high`. `bundle_digest` is computed last.
- **Output path**: an explicit `out_dir`, else `$ARTIFACTS_DIR/materializer/<candidate_id>/`,
  with `candidate_id` matching `^[A-Za-z0-9_-]+$`. With neither available, `materialize()` raises
  `MaterializerError`. It also raises for a bundle directory inside the baseline clone, and when
  writing `bundle.json` fails (any `OSError`). These are the only raises, and each one means
  "no bundle was written". Every other failure is a `BLOCKED` bundle, never an uncaught
  exception.
- **CLI exit codes**: 0 PASS, 1 BLOCKED (bundle written), 2 when no bundle was written
  (unreadable candidate file, no output location, unsafe default-path id, output inside the
  clone, or a write error).

---

### Task 0: Preflight — confirm the base the plan assumes

**Files:** none (read-only checks; no commit).

- [ ] **Step 1: Confirm the files this plan edits or relies on exist**

```bash
ls scripts/docs_refs_check.py docs/configuration.md scripts/factory_core/mechanism_candidate.py \
   scripts/factory_core/verdict.py docs/superpowers/specs/2026-10-22-harness-candidate-materializer-design.md
grep -n "95 leaf keys" docs/configuration.md
tail -3 config/config.yaml
ls scripts/factory_core/materializer.py tests/test_materializer.py evals/materializer 2>&1 | grep -c "No such file"
```

Expected: the five paths are listed. `95 leaf keys` appears on two lines (about 81 and 304).
The last line of `config/config.yaml` is `      - material_conformance_uncertainty`. The final
count is `3` (none of the new files exist yet). If `docs_refs_check.py` or
`docs/configuration.md` is missing, the branch did not fork from current `main`. Rebase onto
`origin/main` before continuing.

- [ ] **Step 2: Confirm the #464 baseline is green**

```bash
PYTHONPATH=scripts python -m pytest tests/test_mechanism_candidate.py -q
```

Expected: `... passed` with no failures.

---

### Task 1: Module skeleton, config ceilings, config block and docs rows

**Files:**
- Create: `tests/test_materializer.py`
- Create: `scripts/factory_core/materializer.py`
- Modify: `config/config.yaml` (append at end)
- Modify: `docs/configuration.md`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_materializer.py` with exactly this
  content (the header, shared constants, and the config tests):

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `1 error`, a collection error (`ImportError while importing test module`,
`cannot import name 'materializer' from 'factory_core'`).

- [ ] **Step 3: Create the module.** Create `scripts/factory_core/materializer.py` with exactly
  this content (the header, `Baseline`, the shared helpers, and the config section):

```python
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
```

- [ ] **Step 4: Run the tests. The committed block does not exist yet.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `1 failed, 8 passed`. The failure is `test_load_config_reads_the_committed_block`,
with `config: the materializer block is missing or not a mapping`.

- [ ] **Step 5: Append the config block** to the very end of `config/config.yaml`:

```bash
cat >> config/config.yaml <<'EOF'

materializer:
  # Harness candidate materializer ceilings (#465; scripts/factory_core/materializer.py).
  # A candidate's resource_envelope may lower these, never raise them. rlimits are
  # per-process, not a sandbox-wide cap; max_nproc bounds the fork-bomb multiplier.
  # Read only by materializer.py; no DAG node, scheduler path or gate calls it yet.
  # No env override, no adapter overlay.
  memory_limit_mb: 4096         # RLIMIT_AS ceiling per sandboxed process (MiB)
  max_duration_seconds: 1800    # wall-clock ceiling; RLIMIT_CPU = cpu_limit x this
  max_workspace_bytes: 268435456  # expanded-size cap on the workspace (256 MiB); also RLIMIT_FSIZE
  max_nproc: 128                # RLIMIT_NPROC ceiling inside the sandbox
EOF
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `9 passed`.

- [ ] **Step 7: Run the docs reference check to verify it fails**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: exit 1, `Docs reference check failed:`, and four lines, one per new key, of the form
``docs/configuration.md: config key `materializer.max_duration_seconds` in <config path> is not documented in the Layer 1 tables``.
The check prints whatever config path it resolved, which may be absolute.

- [ ] **Step 8: Document the keys** in `docs/configuration.md`, with three edits:

  1. In the Layer 1 intro, replace `` (`Dockerfile:152`). 95 leaf keys, `` with
     `` (`Dockerfile:152`). 99 leaf keys, ``.
  2. In the `token_optimization` counting paragraph, replace
     ``` `comments` 4, `diff` 3, `escalation` 1): 95 leaf keys.``` with
     ``` `comments` 4, `diff` 3, `escalation` 1), `materializer` 4: 99 leaf keys.```
  3. Insert this section immediately **before** the `### Unread keys (summary)` heading (after
     the counting paragraph and its blank line):

```markdown
### `materializer`

Ceilings for the harness candidate materializer (#465,
[spec](superpowers/specs/2026-10-22-harness-candidate-materializer-design.md)). Read only by
`scripts/factory_core/materializer.py` (`load_config`, from this checkout's
`config/config.yaml`); no DAG node, scheduler path or gate calls the materializer yet. A
candidate's `resource_envelope` may lower a ceiling, never raise it (a larger declared value is
a `BLOCKED` refusal). The rlimits are per process, not a sandbox-wide cap. Phase A only builds
the `prlimit` argv from these values; nothing applies them until the Phase B executor (#544).
No env overrides, no adapter overlay.

| Key | Default | Controls | Reader | Env override (scope) |
|---|---|---|---|---|
| `materializer.memory_limit_mb` | `4096` | `RLIMIT_AS` ceiling per sandboxed process (MiB) | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |
| `materializer.max_duration_seconds` | `1800` | wall-clock ceiling; `RLIMIT_CPU` is the candidate's integer `cpu_limit` times this | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |
| `materializer.max_workspace_bytes` | `268435456` | expanded-size cap on the baseline and the built workspace; also `RLIMIT_FSIZE` | `scripts/factory_core/materializer.py` (`build_workspace`, `resolve_limits`) | none |
| `materializer.max_nproc` | `128` | `RLIMIT_NPROC` ceiling inside the sandbox (bounds the fork-bomb memory multiplier) | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |
```

The table names `resolve_limits` and `build_workspace` as readers. Those functions arrive in
Tasks 2 and 4. `docs_refs_check.py` checks paths, not symbol names, so this is expected.

- [ ] **Step 9: Run the docs check to verify it passes**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 10: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py config/config.yaml docs/configuration.md
git commit -m "feat(materializer): module skeleton and materializer config ceilings (#465)"
```

---

### Task 2: Envelope policy — declared egress refusal and rlimit resolution

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `14 failed, 9 passed` (`AttributeError: module 'factory_core.materializer' has no
attribute 'resolve_limits'` / `'check_network'`).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `23 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): refuse declared egress and resolve rlimits under config ceilings (#465)"
```

---

### Task 3: Baseline digesting from a pinned commit

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_materializer.py`. This adds the
  throwaway-baseline helpers (`_git`, `_make_repo`, the `scratch_tmp` fixture, `_record`, `_h2`)
  that every later task uses, plus the digest tests:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `16 failed, 23 passed` (`AttributeError: ... has no attribute
'compute_repo_digest'` / `'resolve_in_tree'`; `Baseline` already exists from Task 1).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `39 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): compute repo_digest from a pinned commit; refuse gitlink, LFS and escaping symlinks (#465)"
```

---

### Task 4: Workspace construction

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `8 failed, 39 passed` (`AttributeError: ... has no attribute 'build_workspace'`).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
# ── Workspace construction ──────────────────────────────────────────────────────

def _write_snapshot(workspace, entries, blobs, cap):
    """Write the digested blobs with their recorded modes; symlinks last, so no regular
    file is ever written through one."""
    written = 0
    for path, mode, _, _ in sorted(entries, key=lambda e: e[1] == _SYMLINK_MODE):
        dest = os.path.join(workspace, *path.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == _SYMLINK_MODE:
            os.symlink(blobs[path].decode("utf-8"), dest)
            continue
        written += len(blobs[path])
        if written > cap:
            _fail(_cap_msg(written, cap))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(dest, flags, 0o755 if mode == "100755" else 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(blobs[path])


def _apply_diff(workspace, diff):
    """`git apply --check`, then `git apply`, in the snapshot (no .git, so git runs as a
    plain patch tool). Inherited GIT_* variables are dropped, repository discovery stops
    at the workspace, a baseline shaped like a bare repository (top-level HEAD, objects/,
    refs/, config) is never used as one, and no system/global config applies — so no
    other repository and no host or baseline config can steer the apply."""
    env = _git_env()
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(workspace))
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    for args in (("apply", "--check"), ("apply",)):
        proc = subprocess.run(["git", "-c", "safe.bareRepository=explicit", *args, "-"],
                              cwd=workspace, input=diff.encode("utf-8"),
                              capture_output=True, env=env, check=False)
        if proc.returncode != 0:
            _fail("candidate diff does not apply cleanly to the baseline: "
                  + proc.stderr.decode("utf-8", "replace").strip())


def _tree_bytes(root):
    """Total size of everything under root; never follows a symlink."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            if not stat.S_ISDIR(st.st_mode):
                total += st.st_size
    return total


def remove_workspace(workspace):
    if workspace and os.path.lexists(workspace):
        shutil.rmtree(workspace)


def _cleanup(workspace):
    """remove_workspace(), reporting a failure instead of raising it, so the original
    refusal survives and the leaked workspace is named."""
    try:
        remove_workspace(workspace)
    except OSError as exc:
        return f"workspace {workspace} was not removed: {exc}"
    return None


def _build_workspace(candidate, baseline, entries, blobs, cap):
    workspace = tempfile.mkdtemp(prefix="materializer-")
    try:
        roots = [baseline.repo_root] + [d for d in (os.environ.get("ARTIFACTS_DIR"),) if d]
        for root in roots:
            if _is_within(workspace, root):
                _fail(f"workspace {workspace} is inside {root}; it must be outside the "
                      f"clone and $ARTIFACTS_DIR")
        _write_snapshot(workspace, entries, blobs, cap)
        _apply_diff(workspace, candidate["diff"])
        size = _tree_bytes(workspace)
        if size > cap:
            _fail(_cap_msg(size, cap))
    except BaseException as exc:
        leftover = _cleanup(workspace)
        if leftover and isinstance(exc, Exception):
            raise MaterializerError(f"{_error_text(exc)}; {leftover}") from exc
        raise
    return workspace


def build_workspace(candidate, baseline, *, config=None):
    """A fresh mkdtemp() workspace: the pinned commit's blobs plus the candidate diff.
    Outside the clone and $ARTIFACTS_DIR; no .git. The size cap is checked on the tree
    listing before any blob is read, while writing, and after the diff is applied. On
    any failure the workspace is removed before the error propagates; on success the
    caller owns remove_workspace()."""
    _check_baseline(baseline)
    cap = _config(config)["max_workspace_bytes"]
    entries, blobs, _ = _load_baseline(baseline.commit, baseline.repo_root, cap)
    return _build_workspace(candidate, baseline, entries, blobs, cap)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `47 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): build a disposable workspace from the digested blobs plus the candidate diff (#465)"
```

---

### Task 5: bwrap argv translation (pure; never executed in Phase A)

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `14 failed, 47 passed` (`AttributeError: ... has no attribute 'build_bwrap_argv'` /
`'base_bind_dirs'` / `'validate_write_paths'`).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
# ── bwrap argv translation (pure; Phase B launches it) ──────────────────────────

def validate_write_paths(candidate, workspace):
    """filesystem_envelope.write_paths as validated workspace-relative paths (no trailing
    '/', sorted). Each entry is a relative, normalised repo path under
    editable_path_allowlist that exists in the workspace with no symlink component."""
    allowlist = candidate["editable_path_allowlist"]
    name = "filesystem_envelope.write_paths entry"
    out = set()
    for entry in candidate["filesystem_envelope"]["write_paths"]:
        try:
            mc._check_path_text(entry, name)
        except mc.MechanismCandidateError as exc:
            _fail(str(exc))
        if mc._normalise(entry) != entry or entry in (".", "./"):
            _fail(f"{name} {entry!r} is not a normalised repo-relative path")
        if not mc._contained(entry, allowlist):
            _fail(f"{name} {entry!r} is not under editable_path_allowlist")
        rel = entry.rstrip("/")
        cur = workspace
        for part in rel.split("/"):
            cur = os.path.join(cur, part)
            if os.path.islink(cur):
                _fail(f"{name} {entry!r} passes through a symlink ({part!r})")
        if not os.path.exists(cur):
            _fail(f"{name} {entry!r} does not exist in the workspace")
        if not _is_within(cur, workspace) or os.path.realpath(cur) == os.path.realpath(workspace):
            _fail(f"{name} {entry!r} does not resolve inside the workspace")
        out.add(rel)
    return sorted(out)


def base_bind_dirs(exists=os.path.exists):
    """The minimum host directories needed to exec python3 or /bin/sh in the sandbox."""
    return [*BASE_BIND_DIRS, *(d for d in OPTIONAL_BASE_BIND_DIRS if exists(d))]


def build_bwrap_argv(candidate, workspace, config, *, argv=()):
    """The complete bwrap argument list for `argv` in `workspace`. Executes nothing.

    Bind sources are only the base dirs, the workspace, and validated workspace
    subpaths — never `/`, $HOME, $ARTIFACTS_DIR, the clone, or any credential path, by
    construction. The env is cleared to PATH/HOME/TMPDIR. prlimit sits after `--` so
    the rlimits bind the sandboxed child, never bwrap itself."""
    check_network(candidate)
    limits = resolve_limits(candidate, config)
    writes = validate_write_paths(candidate, workspace)
    args = ["bwrap", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup-try", "--unshare-net", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", SANDBOX_PATH, "--setenv", "HOME", SANDBOX_HOME,
            "--setenv", "TMPDIR", "/tmp"]
    for d in base_bind_dirs():
        args += ["--ro-bind", d, d]
    args += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--tmpfs", SANDBOX_HOME]
    args += ["--ro-bind", workspace, SANDBOX_WORKSPACE]
    for rel in writes:
        args += ["--bind", os.path.join(workspace, *rel.split("/")), f"{SANDBOX_WORKSPACE}/{rel}"]
    args += ["--chdir", SANDBOX_WORKSPACE, "--",
             "prlimit", f"--as={limits['memory_limit_mb'] * 1024 * 1024}",
             f"--cpu={limits['cpu_seconds']}", f"--nproc={limits['max_nproc']}",
             f"--fsize={limits['max_file_bytes']}", "--", *argv]
    return args


def invocation_digest(args, workspace):
    """Digest of the resolved argv with the random workspace path normalised away."""
    return mc.canonical_digest([a.replace(workspace, "<workspace>") for a in args])
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `61 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): translate envelopes into an explicit-allowlist bwrap argv with validated write_paths (#465)"
```

---

### Task 6: `materialize()` and the self-describing artifact bundle

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `13 failed, 61 passed` (`AttributeError: ... has no attribute 'materialize'` /
`'verify_bundle'`).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
# ── Artifact bundle ─────────────────────────────────────────────────────────────

def _verdict_block(status, reason):
    findings, severity = (0, "none") if status == "PASS" else (1, "high")
    block = _verdict.parse_verdict(_verdict.format_verdict(GATE_TYPE, status, findings, severity))
    block["reason"] = reason
    return block


def _module_digest():
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def seal_bundle(bundle):
    """Add bundle_digest: canonical_digest over the bundle with that key absent."""
    body = {k: v for k, v in bundle.items() if k != "bundle_digest"}
    return {**body, "bundle_digest": mc.canonical_digest(body)}


def verify_bundle(bundle):
    """True when bundle_digest still matches the rest of the bundle."""
    if not isinstance(bundle, dict) or "bundle_digest" not in bundle:
        return False
    return seal_bundle(bundle)["bundle_digest"] == bundle["bundle_digest"]


def resolve_out_dir(candidate, out_dir=None):
    """out_dir, else $ARTIFACTS_DIR/materializer/<candidate_id>/ (sanitized id); never
    the current directory."""
    if out_dir:
        return Path(out_dir)
    artifacts = os.environ.get("ARTIFACTS_DIR")
    if not artifacts:
        _fail("out_dir is required when $ARTIFACTS_DIR is unset (no fallback to the "
              "current directory)")
    cid = candidate.get("candidate_id") if isinstance(candidate, dict) else None
    if not isinstance(cid, str) or not _SAFE_ID_RE.fullmatch(cid):
        _fail(f"candidate_id {cid!r} is not safe for the default bundle path "
              f"(^[A-Za-z0-9_-]+$)")
    return Path(artifacts) / "materializer" / cid


def _write_bundle(dest, bundle):
    """Atomic, read-only bundle.json (a unique temp name, so concurrent writers never
    interleave). Any OSError is a MaterializerError: no bundle was written, which callers
    (and the CLI's exit 2) must never mistake for a BLOCKED one."""
    path = dest / "bundle.json"
    tmp = None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest, prefix=".bundle-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o444)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:  # ValueError: NUL or unencodable path
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        _fail(f"cannot write bundle to {dest}: {exc}")
    return path


def _text(v):
    """v if it is a str that encodes as UTF-8, else None. JSON (and surrogateescape argv)
    can carry lone surrogates, which canonical_digest() cannot seal; untrusted fields are
    copied into the bundle only through this."""
    if not isinstance(v, str):
        return None
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return v



def materialize(candidate, *, baseline, out_dir=None, config=None, known_digests=()):
    """Validate, digest, build and remove one candidate workspace; write and return its
    bundle. Accepts H0-H2. Every refusal is a BLOCKED bundle, never an exception; only
    an unwritable bundle location or a failed bundle write raises MaterializerError.
    Executes nothing from the workspace and never probes for bwrap
    (provenance.bwrap_available is null)."""
    dest = resolve_out_dir(candidate, out_dir)
    try:
        inside = isinstance(baseline, Baseline) and isinstance(baseline.repo_root, str) \
            and os.path.isdir(baseline.repo_root) and _is_within(dest, baseline.repo_root)
    except (OSError, ValueError) as exc:  # ValueError: NUL or unencodable path
        _fail(f"bundle directory {dest!r} is not a usable path: {exc}")
    if inside:
        _fail(f"bundle directory {dest} is inside the baseline clone {baseline.repo_root}")
    rec = candidate if isinstance(candidate, dict) else {}
    declared = rec.get("baseline") if isinstance(rec.get("baseline"), dict) else {}
    commit = baseline.commit if isinstance(baseline, Baseline) else None
    risk = computed = inv = limits = None
    workspace = None
    try:
        _check_baseline(baseline)
        cfg = _config(config)
        # The spec's safety source: the clone's working-tree .factory/adapter.yaml, not
        # content of the pinned commit (that is what repo_digest covers).
        risk = mc.validate_candidate(candidate, safety=mc.load_safety(baseline.repo_root),
                                     known_digests=known_digests)
        if risk not in _ACCEPTED_RISK:
            _fail(f"risk class {risk} is not materializable")
        check_network(candidate)
        limits = resolve_limits(candidate, cfg)
        cap = cfg["max_workspace_bytes"]
        entries, blobs, manifest = _load_baseline(baseline.commit, baseline.repo_root, cap)
        computed = mc.canonical_digest(manifest)
        if computed != candidate["baseline"]["repo_digest"]:
            _fail(f"baseline repo_digest mismatch: commit {baseline.commit} hashes to "
                  f"{computed}, the candidate declares {candidate['baseline']['repo_digest']}")
        # The workspace is written from the very blobs just digested, never a re-read.
        workspace = _build_workspace(candidate, baseline, entries, blobs, cap)
        inv = invocation_digest(build_bwrap_argv(candidate, workspace, cfg), workspace)
        status, reason = "PASS", ("workspace built from the digested baseline with the "
                                  "candidate diff applied; nothing executed")
    except Exception as exc:  # noqa: BLE001 — every failure is a BLOCKED bundle, never a crash
        status, reason = "BLOCKED", _error_text(exc)
    finally:
        leftover = _cleanup(workspace)
    if leftover:
        status, reason = "BLOCKED", f"{reason}; {leftover}"
    bundle = seal_bundle({
        "candidate_id": _text(rec.get("candidate_id")),
        "candidate_digest": _text(rec.get("candidate_digest")),
        "risk_class": risk,
        "baseline": {**{k: _text(declared.get(k)) for k in mc._BASELINE_FIELDS},
                     "commit": _text(commit), "computed_repo_digest": computed},
        "diff": _text(rec.get("diff")),
        "materialized_at": datetime.datetime.now(datetime.timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "materializer_version": MATERIALIZER_VERSION,
            "module_digest": _module_digest(),
            "bwrap_available": None,
            "invocation_digest": inv,
            "limits": limits,
        },
        "verdict": _verdict_block(status, reason.encode("utf-8", "backslashreplace")
                                         .decode("utf-8")),
    })
    _write_bundle(dest, bundle)
    return bundle
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `74 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): materialize() writes a sealed bundle and always removes its workspace (#465)"
```

---

### Task 7: CLI `materialize` subcommand

**Files:**
- Modify: `tests/test_materializer.py` (append)
- Modify: `scripts/factory_core/materializer.py` (append)

- [ ] **Step 1: Write the failing test.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `1 failed, 74 passed`. `test_cli_exit_codes` fails because `python -m
factory_core.materializer` exits 0 having done nothing, so `ok.stdout` is empty.

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/materializer.py`:

```python
# ── CLI ─────────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("materialize", help="build, digest and bundle a candidate workspace "
                                           "(executes nothing)")
    m.add_argument("--candidate", required=True, help="candidate record JSON file")
    m.add_argument("--commit", required=True, help="full 40-hex baseline commit SHA")
    m.add_argument("--repo-root", required=True, help="clone containing --commit")
    m.add_argument("--out-dir", default="",
                   help="bundle directory (default $ARTIFACTS_DIR/materializer/<candidate_id>/)")
    m.add_argument("--known-digest", action="append", default=[],
                   help="a candidate_digest parent/dependency digests may resolve to (repeatable)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # RecursionError: deep nesting
        print(f"materializer: cannot read candidate {args.candidate!r}: {exc}", file=sys.stderr)
        return 2
    try:
        bundle = materialize(candidate, baseline=Baseline(args.commit, args.repo_root),
                             out_dir=args.out_dir or None, known_digests=args.known_digest)
    except MaterializerError as exc:
        print(f"materializer: {exc}", file=sys.stderr)
        return 2
    v = bundle["verdict"]
    sys.stdout.write(_verdict.format_verdict(GATE_TYPE, v["status"], v["findings_count"],
                                             v["severity"]))
    if v["status"] != "PASS":
        print(f"BLOCKED: {v['reason']}", file=sys.stderr)
    return 0 if v["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `75 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/materializer.py tests/test_materializer.py
git commit -m "feat(materializer): CLI materialize subcommand with PASS/BLOCKED/unwritable exit codes (#465)"
```

---

### Task 8: The 18 synthetic fixtures under `evals/materializer/fixtures/`

Fourteen `attack-*` and four `refuse-*` fixtures, all committed in Phase A. Phase A runs 17 of
them through `materialize()`: eleven decided without execution, plus six execution fixtures
checked at the argv level only. Those argv checks test the translator. They are **not** evidence
that the boundary holds. `refuse-h2-run` is a Phase B fixture. Phase A only asserts that
`materialize()` accepts it as H2.

**Files:**
- Create: `evals/materializer/fixtures/baseline-manifest.json`
- Create: `evals/materializer/fixtures/manifest.json`
- Create (generated): `evals/materializer/fixtures/candidate-<fixture>.json` × 18
- Modify: `tests/test_materializer.py` (append)

- [ ] **Step 1: Create `evals/materializer/fixtures/baseline-manifest.json`** with exactly:

```json
{
  "clean": {
    "extends": null,
    "files": {
      "README.md": {"content": "synthetic materializer baseline (#465)\n"},
      "docs/superpowers/notes/t.md": {"content": "old\n"},
      "scripts/tool.py": {"content": "MAX = 3\n"},
      "scripts/run.sh": {"content": "#!/bin/sh\necho ok\n", "mode": "100755"}
    }
  },
  "write-path-symlink": {
    "extends": "clean",
    "files": {"docs/superpowers/link": {"symlink": "../../scripts"}}
  },
  "symlink-escape": {
    "extends": "clean",
    "files": {"docs/superpowers/escape": {"symlink": "../../../outside"}}
  },
  "submodule": {
    "extends": "clean",
    "files": {"vendor/lib": {"gitlink": "0123456789abcdef0123456789abcdef01234567"}}
  },
  "lfs-pointer": {
    "extends": "clean",
    "files": {"docs/superpowers/model.bin": {"content": "version https://git-lfs.github.com/spec/v1\noid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\nsize 12345\n"}}
  },
  "archive-bomb": {
    "extends": "clean",
    "files": {"docs/superpowers/zeros.bin": {"zeros": 2097152}}
  }
}
```

- [ ] **Step 2: Create `evals/materializer/fixtures/manifest.json`** with exactly:

```json
{
  "attack-credential-canary": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-network-testnet": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-docker-socket": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-path-escape": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-git-mutation": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-resource-exhaustion": {"phases": ["A", "B"], "baseline": "clean", "materialize": "PASS", "phase_a_check": "argv"},
  "attack-write-path-absolute": {"phases": ["A", "B"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "is absolute"},
  "attack-write-path-dotdot": {"phases": ["A", "B"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "'\\.\\.' segment"},
  "attack-write-path-symlink": {"phases": ["A", "B"], "baseline": "write-path-symlink", "materialize": "BLOCKED", "error_match": "passes through a symlink"},
  "attack-symlink-escape": {"phases": ["A"], "baseline": "symlink-escape", "materialize": "BLOCKED", "error_match": "resolves outside the snapshot"},
  "attack-submodule": {"phases": ["A"], "baseline": "submodule", "materialize": "BLOCKED", "error_match": "gitlink \\(submodule\\)"},
  "attack-lfs-pointer": {"phases": ["A"], "baseline": "lfs-pointer", "materialize": "BLOCKED", "error_match": "Git LFS pointer"},
  "attack-archive-bomb": {"phases": ["A"], "baseline": "archive-bomb", "config": {"max_workspace_bytes": 1048576}, "materialize": "BLOCKED", "error_match": "over materializer.max_workspace_bytes"},
  "attack-h3-surface": {"phases": ["A"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "derived risk class H3 is forbidden"},
  "refuse-egress-declared": {"phases": ["A"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "declares egress"},
  "refuse-digest-mismatch": {"phases": ["A"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "repo_digest mismatch"},
  "refuse-cpu-limit-format": {"phases": ["A"], "baseline": "clean", "materialize": "BLOCKED", "error_match": "cpu_limit '500m' is not a supported format"},
  "refuse-h2-run": {"phases": ["B"], "baseline": "clean", "materialize": "PASS", "run": "BLOCKED", "run_error_match": "H2 execution quarantine policy not yet defined"}
}
```

- [ ] **Step 3: Write the failing tests.** Append to `tests/test_materializer.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `43 failed, 75 passed`. The candidate files do not exist yet, so the failures are
`FileNotFoundError: ... candidate-<fixture>.json`, plus the empty `on_disk` set in
`test_fixture_set_is_complete`.

- [ ] **Step 5: Generate the 18 candidate records.** This is a one-off script written to `/tmp`
  that is never committed. Run it from the repository root. It builds each fixture's named baseline as a throwaway repo, stamps the real
  `baseline.repo_digest` (except for `refuse-digest-mismatch`, which keeps its deliberately wrong
  synthetic digest), then stamps `candidate_digest`. If the files go stale later,
  `test_fixture_digests_are_current` fails.

```bash
cat > /tmp/materializer_gen_fixtures.py <<'EOF'
"""One-off: write evals/materializer/fixtures/candidate-*.json from manifest.json and
baseline-manifest.json, with real repo/candidate digests. Not committed; the
test_fixture_digests_are_current test catches any drift."""
import json, sys, tempfile
from pathlib import Path
sys.path[:0] = ["scripts", "tests"]
import test_materializer as t
from factory_core import materializer as mz
from factory_core import mechanism_candidate as mc

FIX = Path("evals/materializer/fixtures")
manifest = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
OVERRIDES = {
    "attack-path-escape": {"filesystem_envelope": {"read_paths": [], "write_paths": [t._DOC]}},
    "attack-resource-exhaustion": {"h2": True, "resource_envelope": {"cpu_limit": "2", "memory_limit_mb": 256, "max_duration_seconds": 30}},
    "attack-write-path-absolute": {"filesystem_envelope": {"read_paths": [], "write_paths": ["/etc"]}},
    "attack-write-path-dotdot": {"filesystem_envelope": {"read_paths": [], "write_paths": ["docs/superpowers/notes/../../../scripts"]}},
    "attack-write-path-symlink": {"editable_path_allowlist": ["docs/superpowers/"], "filesystem_envelope": {"read_paths": [], "write_paths": ["docs/superpowers/link"]}},
    "attack-h3-surface": {"risk_class": "H2", "editable_path_allowlist": ["tests/test_x.py"], "diff": t._diff("tests/test_x.py", "-old", "+new")},
    "refuse-egress-declared": {"network_envelope": {"egress_allowed": True, "allowed_hosts": []}},
    "refuse-cpu-limit-format": {"resource_envelope": {"cpu_limit": "500m", "memory_limit_mb": None, "max_duration_seconds": None}},
    "refuse-h2-run": {"h2": True},
}
with tempfile.TemporaryDirectory() as tmp:
    for name, entry in manifest.items():
        over = dict(OVERRIDES.get(name, {}))
        make = t._h2 if over.pop("h2", False) else t._record
        record = make(None, candidate_id=f"mz-{name}", **over)
        b = t._make_repo(tmp, t._baseline_files(entry["baseline"]), name=name)
        if name != "refuse-digest-mismatch":
            try:
                record["baseline"]["repo_digest"] = mz.compute_repo_digest(b.commit, b.repo_root)
            except mz.MaterializerError:  # refused baselines: any shape-valid digest
                clean = t._make_repo(tmp, t._baseline_files("clean"), name=name + "-clean")
                record["baseline"]["repo_digest"] = mz.compute_repo_digest(clean.commit, clean.repo_root)
        record["candidate_digest"] = mc.candidate_digest_of(record)
        (FIX / f"candidate-{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print("wrote", f"candidate-{name}.json")
EOF
python3 /tmp/materializer_gen_fixtures.py
```

Expected: 18 lines, `wrote candidate-attack-credential-canary.json` through
`wrote candidate-refuse-h2-run.json`.

- [ ] **Step 6: Verify the generator is deterministic**

```bash
md5sum evals/materializer/fixtures/candidate-*.json | md5sum
python3 /tmp/materializer_gen_fixtures.py >/dev/null
md5sum evals/materializer/fixtures/candidate-*.json | md5sum
rm -f /tmp/materializer_gen_fixtures.py
```

Expected: the two printed hashes are identical. Digests depend on content only, never on commit
SHAs or timestamps.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q
```

Expected: `118 passed`.

- [ ] **Step 8: Commit**

```bash
git add evals/materializer/fixtures/ tests/test_materializer.py
git commit -m "test(materializer): 18 synthetic attack/refuse fixtures with manifest and argv-level checks (#465)"
```

---

### Task 9: Final verification and scope check

**Files:** none (verification only; commit only if a fix is needed).

- [ ] **Step 1: Run the full suite, the same way CI does**

```bash
PYTHONPATH=scripts python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: `... passed` and no failures (the prototype run of this plan against `main` was
`2665 passed`; the exact count moves with `main`). `tests/test_materializer.py` reports
`118 passed` and no skips:

```bash
PYTHONPATH=scripts python -m pytest tests/test_materializer.py -q -rs 2>&1 | tail -1
```

Expected: `118 passed` (no `skipped`).

- [ ] **Step 2: Run the `dag-check` job's steps**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: all three exit 0. The last prints `Docs reference check passed for 4 doc(s).`

- [ ] **Step 3: Confirm the CLI end to end on a throwaway baseline.** This is a manual smoke
  test. It builds its own repo, and git never touches this checkout:

```bash
T=$(mktemp -d) && git init -q "$T/repo" \
 && SHA=$(printf 'old\n' | git -C "$T/repo" hash-object -w --stdin) \
 && git -C "$T/repo" update-index --add --cacheinfo "100644,$SHA,docs/superpowers/notes/t.md" \
 && C=$(GIT_AUTHOR_NAME=s GIT_AUTHOR_EMAIL=s@example.invalid GIT_COMMITTER_NAME=s GIT_COMMITTER_EMAIL=s@example.invalid \
        git -C "$T/repo" commit-tree "$(git -C "$T/repo" write-tree)" -m smoke) \
 && PYTHONPATH=scripts python3 -m factory_core.materializer materialize \
      --candidate evals/materializer/fixtures/candidate-refuse-egress-declared.json \
      --commit "$C" --repo-root "$T/repo" --out-dir "$T/out"; echo "exit=$?"; \
 python3 -c "import json,sys; b=json.load(open(sys.argv[1])); print(b['verdict']['status'], b['provenance']['bwrap_available'])" "$T/out/bundle.json"; \
 ls "${TMPDIR:-/tmp}" | grep -c '^materializer-'; rm -rf "$T"
```

Expected: stdout `STATUS: BLOCKED` / `GATE_TYPE: materializer` / `FINDINGS_COUNT: 1` /
`SEVERITY: high`; stderr `BLOCKED: network_envelope declares egress ...`; `exit=1`;
`BLOCKED None`; and `0` (no workspace left behind).

- [ ] **Step 4: Scope check: only the planned files changed (two-dot diff)**

```bash
git fetch -q origin main
git diff --name-only origin/main HEAD | grep -v '^docs/superpowers/\(specs\|plans\)/' | sort
```

Expected, exactly:

```text
config/config.yaml
docs/configuration.md
evals/materializer/fixtures/baseline-manifest.json
evals/materializer/fixtures/candidate-attack-archive-bomb.json
evals/materializer/fixtures/candidate-attack-credential-canary.json
evals/materializer/fixtures/candidate-attack-docker-socket.json
evals/materializer/fixtures/candidate-attack-git-mutation.json
evals/materializer/fixtures/candidate-attack-h3-surface.json
evals/materializer/fixtures/candidate-attack-lfs-pointer.json
evals/materializer/fixtures/candidate-attack-network-testnet.json
evals/materializer/fixtures/candidate-attack-path-escape.json
evals/materializer/fixtures/candidate-attack-resource-exhaustion.json
evals/materializer/fixtures/candidate-attack-submodule.json
evals/materializer/fixtures/candidate-attack-symlink-escape.json
evals/materializer/fixtures/candidate-attack-write-path-absolute.json
evals/materializer/fixtures/candidate-attack-write-path-dotdot.json
evals/materializer/fixtures/candidate-attack-write-path-symlink.json
evals/materializer/fixtures/candidate-refuse-cpu-limit-format.json
evals/materializer/fixtures/candidate-refuse-digest-mismatch.json
evals/materializer/fixtures/candidate-refuse-egress-declared.json
evals/materializer/fixtures/candidate-refuse-h2-run.json
evals/materializer/fixtures/manifest.json
scripts/factory_core/materializer.py
tests/test_materializer.py
```

- [ ] **Step 5: Confirm the Phase B surfaces are untouched and absent**

```bash
git diff --stat origin/main HEAD -- .github/ run-compose.yml Dockerfile deploy/ tests/conftest.py .claude/ .factory/ workflows/ scheduler.sh entrypoint.sh
grep -nE "def (run|preflight_probe)\b|live_sandbox|MATERIALIZER_REQUIRE_LIVE" scripts/factory_core/materializer.py tests/test_materializer.py
git diff origin/main HEAD -- config/config.yaml | grep '^-[^-]' | wc -l
```

Expected: the first command prints nothing. The second prints nothing. The third prints `0`
(the config change is append-only).

- [ ] **Step 6: If any step above required a fix, commit it**

```bash
git status --porcelain
```

Expected: empty. If it is not, commit only files from the Step 4 list:
`git add <those files> && git commit -m "fix(materializer): <what was fixed> (#465)"`.

---

## Spec coverage (Phase A)

| Spec item | Where |
|---|---|
| R1/R2/R3 argv translation: namespaces, `--unshare-net`, `--clearenv` + PATH/HOME/TMPDIR allowlist | Task 5 `build_bwrap_argv`; `test_bwrap_argv_exact_order`; argv fixtures `attack-network-testnet`, `attack-credential-canary`, `attack-docker-socket` |
| R2 declared egress refused, never downgraded | Task 2 `check_network`; `refuse-egress-declared` |
| R4 explicit bind allowlist in the spec's order, never `--ro-bind / /`; `write_paths` containment | Task 5; `test_write_paths_containment`, `attack-write-path-{absolute,dotdot,symlink}`, `attack-path-escape` |
| R6 ceilings, downward-only, `cpu_limit` allowlist, per-process rlimits after `--`, config block + Layer 1 rows | Tasks 1, 2, 5; `refuse-cpu-limit-format`, `attack-resource-exhaustion` |
| R7 importable `materialize()` + CLI `materialize` | Tasks 6, 7 |
| R8 `Baseline(commit, repo_root)`; `ls-tree` + `cat-file`, never `git archive`/working tree; gitlink/LFS/symlink refusals; digest re-check | Task 3; `attack-submodule`, `attack-lfs-pointer`, `attack-symlink-escape`, `refuse-digest-mismatch` |
| R9 `validate_candidate(safety=load_safety(repo_root), known_digests)`; H3 unreachable; H0–H2 accepted | Task 6; `attack-h3-surface`, `test_materialize_accepts_h2`, `refuse-h2-run` (materialize half) |
| R10 ephemeral sealed bundle, `provenance`, `bundle_digest`, sanitized default path, workspace outside clone/`$ARTIFACTS_DIR`, always removed | Tasks 4, 6 |
| Archive-bomb expansion cap | Tasks 3, 4; `attack-archive-bomb`, `test_workspace_cap_counts_the_applied_diff` |
| `attack-git-mutation`: no `.git` in the workspace | Task 4 and the Task 8 argv check |
| R11 no adapter consumer, gate, DAG or scheduler surface | Task 9 Step 5 |
| R12 synthetic fixtures only; argv-level checks are not boundary evidence | Task 8 |
| R13 deferred scope (dependency allowlists, static checks, generated-file inventory, SBOM, `read_paths`) | not built; nothing in this plan touches them |
| Phase A excludes `preflight_probe()`, `run()`, `ci.yml` | Task 9 Step 5 |

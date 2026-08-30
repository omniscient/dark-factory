# Implementation Plan: Artifact handoff manifest — target-loop output to factory ticket, with origin attribution (A5)

**Issue:** omniscient/dark-factory#199
**Spec:** `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`
**Depends on:** #195 (A1, shipped), #301 (A1.5, shipped) — both already on `main`; no gating on this
plan's tasks.

---

## Goal

Define the A5 artifact-handoff-manifest format `.factory/adapter.yaml`'s `handoff.manifest` field
(#301) currently only validates as a non-empty string, and build the intake path that turns a
validated manifest into a `needs-triage`-labeled GitHub issue: a new dependency-free module
`scripts/factory_core/handoff.py` (schema validation + adapter cross-check + intake-produced
verifier gating + issue rendering + audit trail + CLI), plus `origin: factory|target-loop:<name>`
attribution added to `run_record.py record` and `verifier.py::resolve_and_run`. No live target
loop exists yet (loops remain execution-inert per A1.5) — every test in this plan is hermetic,
exercising the module against synthetic fixtures, never a real `gh`, network call, or state dir.

## Architecture

```
scripts/factory_core/handoff.py                    (new)
  HandoffError(code, message)                        canonical error shape (mirrors AdapterError/VerifierError)
  HANDOFF_ACCEPT_STATUSES = {"PASS"}
  read_manifest(clone_dir, manifest_path) -> dict     R1/R2: clone-relative resolve, 256KiB cap, YAML parse
  validate_manifest(manifest) -> None                 R2: shape/type/limit/reason-code checks
  cross_check(manifest, loops) -> dict                R3: producing_loop / side_effect_level vs adapter
  render_body(manifest, verdict_path) -> str           R5: origin banner + fenced body + provenance + JSON block
  intake(clone_dir, manifest_path, *, artifacts_dir,
         create_issue=None, run_verifier=None) -> IntakeResult
                                                       R2 -> R3 -> R4 -> R5 -> R6 orchestration
  CLI: validate | intake subcommands
        │ R3 reads                     │ R4 runs (never reads a manifest-referenced file)
        ▼                              ▼
scripts/factory_core/adapter.py    scripts/factory_core/verifier.py::resolve_and_run
  adapter.get(clone_dir, "loops")     (#301's already-sanctioned check-only execution surface;
  (unmodified)                         also gains the ORIGIN: line here, R7)
        │ R6 records (in-process, not subprocess)
        ▼
scripts/factory_core/run_record.py::cmd_record
  (gains --origin flag, R7; intake's sole post-#199 --origin target-loop:<name> caller)
        │ R5 creates via
        ▼
scripts/factory_core/providers/cli.py  ("tracker create" subcommand, unmodified, injectable)
```

## Tech Stack

- Python stdlib + `pyyaml` (already a dependency, used by `adapter.py`) for `handoff.py` —
  hand-rolled `isinstance` validation, no new dependency, matching `adapter.py`/`verifier.py`'s
  house style.
- `pytest` for `tests/test_handoff.py` (new) and additive cases in `tests/test_verifier.py`,
  `tests/test_run_record.py`, `tests/test_adapter_authoring_guide.py`.
- Markdown for `docs/triage-labels.md` and `docs/adapter-authoring-guide.md` additions,
  `refinement-skills/VERIFIER-CONTRACT.md` addition.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/handoff.py` | **New** — manifest validation + intake orchestration + CLI |
| `tests/test_handoff.py` | **New** — schema validation, cross-check, render, intake, CLI tests |
| `tests/fixtures/verifiers/handoff_pass.sh` | **New** — hermetic fixture verifier (STATUS: PASS) |
| `tests/fixtures/verifiers/handoff_blocked.sh` | **New** — hermetic fixture verifier (STATUS: BLOCKED) |
| `docs/triage-labels.md` | **Modified** — new `manifest-intake` row |
| `docs/adapter-authoring-guide.md` | **Modified** — new "Handoff manifest (A5)" section |
| `tests/test_adapter_authoring_guide.py` | **Modified** — additive assertions for the new section |

Not touched (blast-radius hotspot footprint, per the operator's #199 comment): `adapter.py`,
`breaker.py`, `verdict.py`, and — since the R7 origin-attribution slice shipped separately as
#378 — `verifier.py`, `run_record.py` and `VERIFIER-CONTRACT.md` are **not modified by this
ticket either** (this ticket `Depends on: #378`; Tasks 10, 11 and 14 below are skip-stubs). The
conformance gate treats any edit to those files as out of scope. `cmd_assemble` in
`run_record.py` is not touched (R7 — no caller exists or is chartered).

---

## Task 1: `handoff.py` scaffold + `read_manifest` (R1/R2 file-level checks)

**Files:** `scripts/factory_core/handoff.py` (new), `tests/test_handoff.py` (new)

### TDD Steps

1. Write the failing test file `tests/test_handoff.py`:

```python
import pathlib
import sys

# .factory/hooks/{validate,smoke-gate} run `python -m pytest tests/ -q` with no
# PYTHONPATH=scripts -- self-insert so this file collects there too, not only in CI.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import handoff


def _write_manifest(tmp_path, name="manifest.yaml", text=""):
    (tmp_path / ".factory").mkdir(exist_ok=True)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_read_manifest_rejects_absolute_path(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "/etc/passwd")
    assert exc.value.code == "schema_invalid"
    assert "absolute" in exc.value.message


def test_read_manifest_rejects_escaping_path(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "../outside.yaml")
    assert exc.value.code == "schema_invalid"
    assert "escapes" in exc.value.message


def test_read_manifest_rejects_missing_file(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "does-not-exist.yaml")
    assert exc.value.code == "schema_invalid"


def test_read_manifest_rejects_oversize_file_before_parsing(tmp_path):
    _write_manifest(tmp_path, text="artifact_id: " + ("x" * (handoff.MAX_MANIFEST_BYTES + 1)))
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"
    assert "byte" in exc.value.message.lower()


def test_read_manifest_rejects_non_mapping_top_level(tmp_path):
    _write_manifest(tmp_path, text="- just\n- a\n- list\n")
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"
    assert "mapping" in exc.value.message


def test_read_manifest_rejects_unparseable_yaml(tmp_path):
    _write_manifest(tmp_path, text="key: [unclosed")
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"


def test_read_manifest_returns_parsed_dict(tmp_path):
    _write_manifest(tmp_path, text="artifact_id: scan-001\nside_effect_level: 2\n")
    data = handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert data == {"artifact_id": "scan-001", "side_effect_level": 2}
```

2. Verify it fails:
   ```bash
   cd "$(git rev-parse --show-toplevel)" && PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q
   ```
   Expected: `ModuleNotFoundError: No module named 'factory_core.handoff'` (or collection error).

3. Implement `scripts/factory_core/handoff.py`:

```python
"""Artifact handoff manifest (A5): schema validation + intake orchestration + CLI.

Turns a target loop's validated manifest into a factory-created GitHub issue, gated on a
verdict *intake itself produces* by running the loop's declared A3 verifier -- never on a
file the manifest merely references (maker never validates maker). See
docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import namedtuple

import yaml

from . import adapter as _adapter
from . import identity as _identity
from . import run_record as _run_record
from . import verdict as _verdict
from . import verifier as _verifier


class HandoffError(Exception):
    """Raised on any R2-R5 rejection. `code` is a closed reason-code token recorded
    verbatim in runs.jsonl's detail.reject_reason (R6); `message` is human-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# Admission set for the verdict *intake itself produces* by running the loop's A3
# verifier (R4). Deliberately not verdict.GATING_PASS_STATUSES ({PASS, SKIPPED, ERROR}):
# intake is terminal and one-way (mints new backlog work with no downstream gate behind
# it), so SKIPPED ("verification did not happen") must not admit -- that would reopen
# the "maker never validates maker" gap this ticket exists to close.
HANDOFF_ACCEPT_STATUSES = {"PASS"}

MAX_MANIFEST_BYTES = 256 * 1024
MAX_ID_LEN = 128
MAX_TITLE_LEN = 200
MAX_BODY_BYTES = 32 * 1024
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_LEN = 512
MAX_RENDERED_BODY_LEN = 60_000

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

FACTORY_MANIFEST_LABEL = os.environ.get("FACTORY_MANIFEST_LABEL", "manifest-intake")

IntakeResult = namedtuple("IntakeResult", ["accepted", "issue_id", "artifact_id"])


def _resolve_manifest_path(clone_dir: str, manifest_path: str) -> str:
    """Same clone-relative rule as verifier.py::resolve_verifier (R1's Approach note),
    reimplemented here (not imported) because it must raise HandoffError, not
    VerifierError -- the two error hierarchies are deliberately not unified."""
    if os.path.isabs(manifest_path):
        raise HandoffError(
            "schema_invalid",
            f"manifest path must be relative to CLONE_DIR, got absolute: {manifest_path}",
        )
    root = os.path.realpath(clone_dir)
    resolved = os.path.realpath(os.path.join(root, manifest_path))
    if os.path.commonpath([root, resolved]) != root:
        raise HandoffError("schema_invalid", f"manifest path escapes CLONE_DIR: {manifest_path}")
    return resolved


def read_manifest(clone_dir: str, manifest_path: str) -> dict:
    """R1/R2: resolve, read-cap, parse. Never executes the manifest file."""
    resolved = _resolve_manifest_path(clone_dir, manifest_path)
    if not os.path.isfile(resolved):
        raise HandoffError("schema_invalid", f"manifest file not found: {manifest_path}")
    size = os.path.getsize(resolved)
    if size > MAX_MANIFEST_BYTES:
        raise HandoffError(
            "schema_invalid",
            f"manifest file is {size} bytes, exceeds the {MAX_MANIFEST_BYTES}-byte cap",
        )
    try:
        # open/read sit INSIDE the try: a non-UTF-8 manifest must fail closed as
        # schema_invalid (and get its R6 row), not escape as an uncaught UnicodeDecodeError.
        with open(resolved, encoding="utf-8") as fh:
            raw = fh.read()
        data = yaml.safe_load(raw)
    except Exception as exc:
        raise HandoffError("schema_invalid", f"manifest unreadable/unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise HandoffError("schema_invalid", "manifest top level must be a mapping")
    return data


if __name__ == "__main__":
    pass
```

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q
   ```
   Expected: `7 passed`.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): handoff.py scaffold + read_manifest (R1/R2 file-level checks)"
   ```

---

**Task 1 addendum (operator review):** also add to `tests/test_handoff.py` in step 1:

```python
def test_read_manifest_rejects_undecodable_bytes(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(handoff.HandoffError) as excinfo:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert excinfo.value.reason == "schema_invalid"
```

---

## Task 2: `validate_manifest` — required/unknown fields, `schema_version`, `artifact_id`, `producing_loop`, `side_effect_level`

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def _valid_manifest(**overrides):
    manifest = {
        "schema_version": 1,
        "artifact_id": "scan-2026-08-30-001",
        "producing_loop": "nightly-scan-triage",
        "side_effect_level": 2,
        "source_references": ["scanner_output.json"],
        "acceptance_thresholds": ["false_positive_rate < 0.05"],
        "proposed_ticket": {
            "title": "Triage: 3 new findings in payments module",
            "body": "## Findings\nSomething was found.\n",
        },
    }
    manifest.update(overrides)
    return manifest


def test_validate_manifest_accepts_minimal_valid_manifest():
    handoff.validate_manifest(_valid_manifest())  # no raise


def test_validate_manifest_rejects_unknown_top_level_key():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(mystery="x"))
    assert exc.value.code == "schema_invalid"
    assert "mystery" in exc.value.message


def test_validate_manifest_rejects_missing_required_field():
    manifest = _valid_manifest()
    del manifest["artifact_id"]
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"
    assert "artifact_id" in exc.value.message


def test_validate_manifest_rejects_non_int_schema_version():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(schema_version="1"))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_schema_version_not_1():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(schema_version=2))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_empty_id_field(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ""}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_too_long(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "x" * (handoff.MAX_ID_LEN + 1)}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_bad_characters(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "not a valid id!"}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_with_backtick_as_unsafe_string(field):
    # Spec's R2 reason-code table lists artifact_id/producing_loop among the
    # unsafe_string fields -- distinct from the generic schema_invalid the charset
    # regex would otherwise raise for the same backtick-containing input.
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "has`tick"}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_rejects_non_int_side_effect_level():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(side_effect_level=True))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("level", [0, 7, -1])
def test_validate_manifest_rejects_out_of_range_side_effect_level(level):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(side_effect_level=level))
    assert exc.value.code == "schema_invalid"
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k validate_manifest
   ```
   Expected: `AttributeError: module 'factory_core.handoff' has no attribute 'validate_manifest'`.

3. Implement in `scripts/factory_core/handoff.py` (append after `read_manifest`):

```python
_REQUIRED_TOP = (
    "schema_version", "artifact_id", "producing_loop", "side_effect_level",
    "source_references", "acceptance_thresholds", "proposed_ticket",
)
_OPTIONAL_TOP = ("verifier_verdict",)
_KNOWN_TOP = set(_REQUIRED_TOP) | set(_OPTIONAL_TOP)


def _check_unsafe_string(value: str, field: str) -> None:
    """R2/R5: a string rendered outside a fenced block must not carry a backtick
    (breaks out of an inline code span) or a newline (breaks out of a single-line
    provenance bullet). Defined here (not in a later task) because artifact_id and
    producing_loop -- both validated in this task -- are on the spec's R2 unsafe_string
    field list alongside the list-item/verifier_verdict.path fields later tasks add."""
    if "`" in value or "\n" in value:
        raise HandoffError("unsafe_string", f"field '{field}' must not contain a backtick or newline")


def validate_manifest(manifest: dict) -> None:
    """R2: full shape/type/limit validation of an already-parsed manifest dict.
    Raises HandoffError(code, message) on the first violation found; code is drawn
    from the closed reason-code list in the spec's R2 table."""
    for key in manifest:
        if key not in _KNOWN_TOP:
            raise HandoffError("schema_invalid", f"unknown field '{key}'")
    for field in _REQUIRED_TOP:
        if field not in manifest:
            raise HandoffError("schema_invalid", f"missing required field '{field}'")

    sv = manifest["schema_version"]
    if isinstance(sv, bool) or sv != 1:
        raise HandoffError("schema_invalid", "field 'schema_version' must be the int 1")

    for field in ("artifact_id", "producing_loop"):
        v = manifest[field]
        if not isinstance(v, str) or not v or len(v) > MAX_ID_LEN:
            raise HandoffError(
                "schema_invalid",
                f"field '{field}' must be a non-empty string of at most {MAX_ID_LEN} chars",
            )
        # Checked before the charset regex so a backtick/newline is reported as
        # unsafe_string (the spec's R2 table lists artifact_id/producing_loop among
        # the unsafe_string fields), not the generic schema_invalid the regex below
        # would otherwise raise for the same input -- "more specific codes take
        # precedence" per the spec's schema_invalid definition.
        _check_unsafe_string(v, field)
        if not _ID_RE.match(v):
            raise HandoffError(
                "schema_invalid", f"field '{field}' must match ^[A-Za-z0-9._-]+$"
            )

    sel = manifest["side_effect_level"]
    if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6):
        raise HandoffError(
            "schema_invalid", "field 'side_effect_level' must be an int between 1 and 6"
        )
```

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k validate_manifest
   ```
   Expected: `17 passed`. (`test_validate_manifest_accepts_minimal_valid_manifest` also passes
   at this stage: `validate_manifest` doesn't yet inspect `source_references`/
   `acceptance_thresholds`/`proposed_ticket` contents, but `_valid_manifest()` already shapes
   them correctly, so nothing raises.)

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): validate_manifest — top-level shape, schema_version, ids, side_effect_level"
   ```

---

## Task 3: `validate_manifest` — `source_references` / `acceptance_thresholds` lists, `unsafe_string`

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_non_list_field(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "not-a-list"}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_non_string_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: [123]}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_too_many_list_items(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["x"] * (handoff.MAX_LIST_ITEMS + 1)}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_oversize_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["x" * (handoff.MAX_LIST_ITEM_LEN + 1)]}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_backtick_in_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["has`backtick"]}))
    assert exc.value.code == "unsafe_string"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_newline_in_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["has\nnewline"]}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_accepts_empty_lists():
    handoff.validate_manifest(
        _valid_manifest(source_references=[], acceptance_thresholds=[])
    )  # no raise
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k "source_references or acceptance_thresholds"
   ```
   Expected: failures — `validate_manifest` doesn't check list fields yet (either no error
   raised, or `AttributeError`/`KeyError` further down before reaching `proposed_ticket`).

3. Implement — append inside `validate_manifest`, after the `side_effect_level` check:

```python
    for field in ("source_references", "acceptance_thresholds"):
        items = manifest[field]
        if (
            not isinstance(items, list)
            or len(items) > MAX_LIST_ITEMS
            or not all(isinstance(x, str) for x in items)
        ):
            raise HandoffError(
                "schema_invalid",
                f"field '{field}' must be a list of at most {MAX_LIST_ITEMS} strings",
            )
        for item in items:
            if len(item) > MAX_LIST_ITEM_LEN:
                raise HandoffError(
                    "schema_invalid", f"field '{field}' item exceeds {MAX_LIST_ITEM_LEN} chars"
                )
            _check_unsafe_string(item, field)
```

   (`_check_unsafe_string` itself was already defined in Task 2, above `validate_manifest` —
   reused here unmodified, not redefined.)

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k "source_references or acceptance_thresholds"
   ```
   Expected: `12 passed` (the filter matches the 6 parametrized tests × 2 field ids each;
   `test_validate_manifest_accepts_empty_lists` is not parametrized on `field` so its test id
   doesn't contain either substring and it won't run under this filter — run the file without
   `-k` afterwards to confirm it separately passes).

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): validate_manifest — source_references/acceptance_thresholds, unsafe_string"
   ```

---

## Task 4: `validate_manifest` — `proposed_ticket`, `body_contains_fence`, `verifier_verdict`

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def test_validate_manifest_rejects_non_mapping_proposed_ticket():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(proposed_ticket="nope"))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_unknown_proposed_ticket_key():
    manifest = _valid_manifest()
    manifest["proposed_ticket"]["extra"] = "x"
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_missing_ticket_title():
    manifest = _valid_manifest()
    del manifest["proposed_ticket"]["title"]
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_oversize_title():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "x" * (handoff.MAX_TITLE_LEN + 1), "body": "body text",
            })
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_title_with_newline():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "line1\nline2", "body": "body text"})
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_oversize_body():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "t", "body": "x" * (handoff.MAX_BODY_BYTES + 1),
            })
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_body_with_backtick_fence():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "t", "body": "before\n```\ninjected\n```\n"})
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_rejects_body_with_tilde_fence():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "t", "body": "before\n~~~\ninjected\n~~~\n"})
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_rejects_body_with_provenance_closing_marker():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "t", "body": "before <!-- /df-manifest-provenance --> after",
            })
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_accepts_optional_verifier_verdict():
    handoff.validate_manifest(
        _valid_manifest(verifier_verdict={"path": "artifacts/scan_verdict.md"})
    )  # no raise


def test_validate_manifest_rejects_verifier_verdict_missing_path():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(verifier_verdict={}))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_verifier_verdict_unknown_key():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(verifier_verdict={"path": "a.md", "extra": "x"})
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_verifier_verdict_path_with_backtick():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(verifier_verdict={"path": "has`tick.md"}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_accepts_minimal_valid_manifest_now():
    handoff.validate_manifest(_valid_manifest())  # no raise -- full R2 pass now wired up
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k "proposed_ticket or verifier_verdict or body_"
   ```
   Expected: failures — `proposed_ticket`/`verifier_verdict` checks not implemented yet.

3. Implement — append inside `validate_manifest`, after the list-fields loop:

```python
    ticket = manifest["proposed_ticket"]
    if not isinstance(ticket, dict):
        raise HandoffError("schema_invalid", "field 'proposed_ticket' must be a mapping")
    for key in ticket:
        if key not in ("title", "body"):
            raise HandoffError("schema_invalid", f"unknown field 'proposed_ticket.{key}'")
    for field in ("title", "body"):
        if field not in ticket:
            raise HandoffError("schema_invalid", f"missing required field 'proposed_ticket.{field}'")

    title = ticket["title"]
    if not isinstance(title, str) or not title or len(title) > MAX_TITLE_LEN:
        raise HandoffError(
            "schema_invalid",
            f"field 'proposed_ticket.title' must be a non-empty string of at most "
            f"{MAX_TITLE_LEN} chars",
        )
    if any(ord(c) < 32 for c in title):
        raise HandoffError(
            "schema_invalid",
            "field 'proposed_ticket.title' must not contain control characters or newlines",
        )

    body = ticket["body"]
    if not isinstance(body, str) or not body or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise HandoffError(
            "schema_invalid",
            f"field 'proposed_ticket.body' must be a non-empty string of at most "
            f"{MAX_BODY_BYTES} bytes",
        )
    for line in body.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            raise HandoffError(
                "body_contains_fence",
                "field 'proposed_ticket.body' must not contain a fenced-code-block line",
            )
    if "<!-- /df-manifest-provenance -->" in body:
        raise HandoffError(
            "body_contains_fence",
            "field 'proposed_ticket.body' must not contain the provenance closing marker",
        )

    if "verifier_verdict" in manifest:
        vv = manifest["verifier_verdict"]
        if not isinstance(vv, dict):
            raise HandoffError("schema_invalid", "field 'verifier_verdict' must be a mapping")
        for key in vv:
            if key != "path":
                raise HandoffError("schema_invalid", f"unknown field 'verifier_verdict.{key}'")
        if "path" not in vv:
            raise HandoffError("schema_invalid", "missing required field 'verifier_verdict.path'")
        path = vv["path"]
        if not isinstance(path, str) or not path:
            raise HandoffError("schema_invalid", "field 'verifier_verdict.path' must be a non-empty string")
        _check_unsafe_string(path, "verifier_verdict.path")
```

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q
   ```
   Expected: all tests added in Tasks 1-4 pass (every test in the file so far, since Task 4
   completes R2 validation) — check the pytest summary line reports zero failures rather than
   matching an exact count, since that count shifts if any prior task's test list changes.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): validate_manifest — proposed_ticket, body_contains_fence, verifier_verdict"
   ```

---

## Task 5: `cross_check` — R3 adapter cross-validation

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def _loop_entry(**overrides):
    """Mirrors tests/test_verifier.py::_loop_entry -- the minimal valid A1.5 loop shape."""
    entry = {
        "name": "nightly-scan-triage",
        "purpose": "nightly scan triage",
        "discovery": {"trigger": "cron:0 6 * * *", "inputs": ["scripts/scanner.py"]},
        "handoff": {"outputs": ["artifacts/scan-report.md"], "manifest": "artifacts/manifest.yaml"},
        "verification": {"verifier": "scripts/verify-scan.sh", "stop_condition": "manifest present"},
        "persistence": {"artifacts": ["artifacts/scan-history.jsonl"]},
        "scheduling": {"failure_behavior": "retry-once"},
        "side_effect_level": 2,
    }
    entry.update(overrides)
    return entry


def test_cross_check_returns_matched_loop_entry():
    loops = [_loop_entry()]
    matched = handoff.cross_check(_valid_manifest(), loops)
    assert matched["name"] == "nightly-scan-triage"


def test_cross_check_rejects_unknown_producing_loop():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(_valid_manifest(producing_loop="ghost-loop"), [_loop_entry()])
    assert exc.value.code == "unknown_producing_loop"


def test_cross_check_rejects_when_no_loops_declared():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(_valid_manifest(), None)
    assert exc.value.code == "unknown_producing_loop"


def test_cross_check_rejects_side_effect_level_mismatch():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(
            _valid_manifest(side_effect_level=3), [_loop_entry(side_effect_level=2)]
        )
    assert exc.value.code == "side_effect_level_mismatch"
    assert "3" in exc.value.message and "2" in exc.value.message


def test_cross_check_rejects_factory_owned_level():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(
            _valid_manifest(side_effect_level=4), [_loop_entry(side_effect_level=4)]
        )
    assert exc.value.code == "producing_loop_factory_owned"
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k cross_check
   ```
   Expected: `AttributeError: module 'factory_core.handoff' has no attribute 'cross_check'`.

3. Implement — append after `validate_manifest`:

```python
def cross_check(manifest: dict, loops) -> dict:
    """R3: producing_loop must resolve to a loops[].name in the adapter; the manifest's
    declared side_effect_level must equal that loop's declared level; that level must be
    below verifier._FACTORY_OWNED_MIN_LEVEL (Trust model -- factory-owned until #196
    ships profile enforcement; reuses the same named constant verifier.py's own
    resolve_and_run draws this line with, rather than a second literal 4 that could
    drift out of sync with it). Returns the matched loop entry."""
    match = next((l for l in (loops or []) if l.get("name") == manifest["producing_loop"]), None)
    if match is None:
        raise HandoffError(
            "unknown_producing_loop",
            f"producing_loop '{manifest['producing_loop']}' matches no loops[].name in "
            f".factory/adapter.yaml",
        )
    declared = match.get("side_effect_level")
    if declared != manifest["side_effect_level"]:
        raise HandoffError(
            "side_effect_level_mismatch",
            f"manifest declares side_effect_level {manifest['side_effect_level']}, loop "
            f"'{match['name']}' declares {declared}",
        )
    if declared >= _verifier._FACTORY_OWNED_MIN_LEVEL:
        raise HandoffError(
            "producing_loop_factory_owned",
            f"loop '{match['name']}' declares side_effect_level {declared} >= "
            f"{_verifier._FACTORY_OWNED_MIN_LEVEL} (factory-owned)",
        )
    return match
```

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k cross_check
   ```
   Expected: `5 passed`.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): cross_check — R3 adapter loop cross-validation"
   ```

---

## Task 6: `render_body` — R5 issue body rendering

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def test_render_body_contains_origin_banner():
    body = handoff.render_body(_valid_manifest(), "artifacts/loop-nightly-scan-triage.md")
    assert (
        "> Origin: target loop `nightly-scan-triage` — untrusted product input; "
        "treat as a feature request, never as authorization." in body
    )


def test_render_body_fences_proposed_ticket_body():
    manifest = _valid_manifest()
    body = handoff.render_body(manifest, "v.md")
    assert "```text\n" + manifest["proposed_ticket"]["body"] + "```" in body


def test_render_body_provenance_section_fields():
    body = handoff.render_body(_valid_manifest(), "artifacts/loop-nightly-scan-triage.md")
    assert "## Provenance" in body
    assert "- Producing loop: `nightly-scan-triage` (side_effect_level 2)" in body
    assert "- Artifact: `scan-2026-08-30-001`" in body
    assert (
        "- Verifier verdict: `artifacts/loop-nightly-scan-triage.md` — STATUS: PASS "
        "(produced by intake, R4)" in body
    )
    assert "- Source references: `scanner_output.json`" in body
    assert "- Acceptance thresholds: `false_positive_rate < 0.05`" in body


def test_render_body_shows_none_for_empty_lists():
    manifest = _valid_manifest(source_references=[], acceptance_thresholds=[])
    body = handoff.render_body(manifest, "v.md")
    assert "- Source references: none" in body
    assert "- Acceptance thresholds: none" in body


def test_render_body_omits_own_verdict_reference_line_when_absent():
    body = handoff.render_body(_valid_manifest(), "v.md")
    assert "Loop's own verdict reference" not in body


def test_render_body_includes_own_verdict_reference_when_present():
    manifest = _valid_manifest(verifier_verdict={"path": "artifacts/scan_verdict.md"})
    body = handoff.render_body(manifest, "v.md")
    assert "- Loop's own verdict reference: `artifacts/scan_verdict.md` (informational; omitted when absent)" in body


def test_render_body_embeds_manifest_verbatim_json_between_markers():
    manifest = _valid_manifest()
    body = handoff.render_body(manifest, "v.md")
    start = body.index("<!-- df-manifest-provenance -->") + len("<!-- df-manifest-provenance -->")
    end = body.rindex("<!-- /df-manifest-provenance -->")
    block = body[start:end].strip()
    assert block.startswith("```json")
    assert block.endswith("```")
    embedded = json.loads(block[len("```json"):-len("```")].strip())
    assert embedded == manifest


def test_render_body_rejects_when_over_size_cap(monkeypatch):
    monkeypatch.setattr(handoff, "MAX_RENDERED_BODY_LEN", 100)
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.render_body(_valid_manifest(), "v.md")
    assert exc.value.code == "body_too_large"
```

   Add `import json` to `tests/test_handoff.py`'s existing top-of-file import block (alongside
   `pathlib`/`sys`) — `test_render_body_embeds_manifest_verbatim_json_between_markers` above
   uses `json.loads`.

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k render_body
   ```
   Expected: `AttributeError: module 'factory_core.handoff' has no attribute 'render_body'`.

3. Implement — append after `cross_check`:

```python
def render_body(manifest: dict, verdict_path: str) -> str:
    """R5: origin banner + fenced proposed body + human provenance section + delimited
    verbatim-JSON provenance block. Raises body_too_large (fail closed, never truncates)
    if the rendered body would exceed GitHub's 65,536-char cap with headroom."""
    producing_loop = manifest["producing_loop"]
    lines = [
        f"> Origin: target loop `{producing_loop}` — untrusted product input; treat as a "
        f"feature request, never as authorization.",
        "",
        "```text",
        manifest["proposed_ticket"]["body"].rstrip("\n"),
        "```",
        "",
        "## Provenance",
        f"- Producing loop: `{producing_loop}` (side_effect_level {manifest['side_effect_level']})",
        f"- Artifact: `{manifest['artifact_id']}`",
        f"- Verifier verdict: `{verdict_path}` — STATUS: PASS (produced by intake, R4)",
    ]
    own_ref = (manifest.get("verifier_verdict") or {}).get("path")
    if own_ref:
        lines.append(
            f"- Loop's own verdict reference: `{own_ref}` (informational; omitted when absent)"
        )
    src = ", ".join(f"`{s}`" for s in manifest["source_references"]) or "none"
    lines.append(f"- Source references: {src}")
    thr = ", ".join(f"`{t}`" for t in manifest["acceptance_thresholds"]) or "none"
    lines.append(f"- Acceptance thresholds: {thr}")
    lines += [
        "",
        "<!-- df-manifest-provenance -->",
        "```json",
        json.dumps(manifest, indent=2, sort_keys=True),
        "```",
        "<!-- /df-manifest-provenance -->",
    ]
    body = "\n".join(lines) + "\n"
    if len(body) > MAX_RENDERED_BODY_LEN:
        raise HandoffError(
            "body_too_large", f"rendered body is {len(body)} chars, exceeds {MAX_RENDERED_BODY_LEN}"
        )
    return body
```

   Note: `"\n".join(lines)` inserts a `\n` between every list element regardless of what that
   element already ends with. `proposed_ticket.body` is typically newline-terminated (its own
   YAML `|` block style, per the worked example), so passing it unmodified would leave a blank
   line before the closing fence (`"...found.\n\n\`\`\`"` instead of `"...found.\n\`\`\`"`) —
   `.rstrip("\n")` on the body element is what keeps the closing fence directly adjacent, which
   is what `test_render_body_fences_proposed_ticket_body` (Task 6, step 1) checks byte-for-byte.

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k render_body
   ```
   Expected: `8 passed`.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): render_body — R5 issue body rendering with provenance block"
   ```

---

## Task 7: `intake` orchestration — accept path, `verifier_undeclared`, `verdict_not_passing`, `issue_create_failed`

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`, `tests/fixtures/verifiers/handoff_pass.sh` (new), `tests/fixtures/verifiers/handoff_blocked.sh` (new)

### TDD Steps

1. Add two fixture verifier scripts (mirroring `tests/fixtures/verifiers/structured_pass.sh` /
   `structured_blocked.sh`, reused hermetically per R6's "verifier is a fixture script under
   tmp_path" statement):

   `tests/fixtures/verifiers/handoff_pass.sh`:
   ```bash
   #!/usr/bin/env bash
   printf 'STATUS: PASS\nGATE_TYPE: ignored-by-normalize_verdict\nFINDINGS_COUNT: 0\nSEVERITY: none\n'
   exit 0
   ```

   `tests/fixtures/verifiers/handoff_blocked.sh`:
   ```bash
   #!/usr/bin/env bash
   printf 'STATUS: BLOCKED\nGATE_TYPE: ignored\nFINDINGS_COUNT: 1\nSEVERITY: high\nREASON: findings exceed threshold\n'
   exit 1
   ```

   Note: this fixture's `REASON:` line is illustrative, not exercised end-to-end by
   `test_intake_rejects_verdict_not_passing` below — `verifier.py::normalize_verdict`'s
   structured-BLOCKED branch (pre-existing #197 behavior, unmodified by this ticket) re-emits
   only `STATUS`/`GATE_TYPE`/`FINDINGS_COUNT`/`SEVERITY` via `format_verdict`, so a target
   verifier's own `REASON:` text does not survive into the verdict `intake()` reads.
   `intake()`'s `REASON:` echo in its `verdict_not_passing` message is real code, reachable via
   `resolve_and_run`'s two early fail-closed paths (undetermined / factory-owned
   `side_effect_level`), which do emit a literal `REASON:` line — it's just not this fixture's
   path. `test_intake_rejects_verdict_not_passing`'s `"BLOCKED" in exc.value.message` assertion
   holds regardless.

   Make both executable:
   ```bash
   chmod +x tests/fixtures/verifiers/handoff_pass.sh tests/fixtures/verifiers/handoff_blocked.sh
   ```

2. Append to `tests/test_handoff.py`:

```python
from factory_core import run_record as _run_record
from factory_core import verifier as _verifier

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "verifiers"


@pytest.fixture(autouse=True)
def _hermetic_run_record(tmp_path, monkeypatch):
    """Every test in this file exercises intake(), and from Task 8 onward intake()
    unconditionally calls run_record.cmd_record (R6) on both the accept and reject
    path. Autouse + per-test tmp_path keeps every test in this file off the real
    SCHEDULER_STATE_DIR and off the network (R6's Hermetic-test statement), without
    each test needing its own monkeypatch boilerplate. A test that needs to inspect
    the written jsonl content overrides JSONL_PATH again with its own path (Task 8) —
    monkeypatch stacking makes that safe."""
    monkeypatch.setattr(_run_record, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    # #362 rule: every test that can write a ledger row pins SCHEDULER_STATE_DIR (attr and
    # env) to tmp_path, so neither this process nor any child can reach /var/lib/dark-factory.
    monkeypatch.setattr(_run_record, "SCHEDULER_STATE_DIR", tmp_path / "scheduler-state")
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))


def _write_manifest_file(clone_dir, manifest, name="manifest.yaml"):
    import yaml as _yaml
    path = pathlib.Path(clone_dir) / name
    path.write_text(_yaml.safe_dump(manifest), encoding="utf-8")
    return name


def _stub_create_issue(issue_id="4242"):
    calls = []

    def _create(title, body, labels):
        calls.append({"title": title, "body": body, "labels": labels})
        return issue_id

    _create.calls = calls
    return _create


def test_intake_accepts_and_creates_issue(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest = _valid_manifest()
    manifest_name = _write_manifest_file(clone_dir, manifest)

    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(artifacts_dir),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )

    assert result.accepted is True
    assert result.issue_id == "4242"
    assert len(create_issue.calls) == 1
    call = create_issue.calls[0]
    assert call["title"] == "[intake] Triage: 3 new findings in payments module"
    assert call["labels"] == "needs-triage,manifest-intake"
    assert "df-manifest-provenance" in call["body"]
    assert (artifacts_dir / "loop-nightly-scan-triage.md").exists()
    assert "STATUS: PASS" in (artifacts_dir / "loop-nightly-scan-triage.md").read_text()


def test_intake_rejects_verifier_undeclared(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"stop_condition": "n/a"})  # no 'verifier' key

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[loop],
        )
    assert exc.value.code == "verifier_undeclared"


def test_intake_rejects_verdict_not_passing(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_blocked.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "verdict_not_passing"
    assert "BLOCKED" in exc.value.message


def test_intake_rejects_issue_create_failed_on_empty_return(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(issue_id=""),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "issue_create_failed"


def test_intake_manifest_label_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "custom-intake")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert create_issue.calls[0]["labels"] == "needs-triage,custom-intake"
```

   Note: `adapter_loops` is an injectable test seam (defaults to `None`, in which case `intake`
   calls `_adapter.get(clone_dir, "loops")` for real) so these tests don't need a full
   `.factory/adapter.yaml` fixture file on disk — see the implementation below. This keeps Task
   7's tests focused on intake's own orchestration logic; Task 9's CLI test exercises the real
   `_adapter.get` path end-to-end. The `_run_record`/`_verifier` imports and the
   `_hermetic_run_record` autouse fixture above are added to `tests/test_handoff.py` now (this
   is the first task in the file that calls `intake`, which reaches `_verifier.resolve_and_run`
   directly and — from Task 8 onward — `_run_record.cmd_record` on every call).

3. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k intake
   ```
   Expected: `TypeError: intake() got an unexpected keyword argument` (or `AttributeError` — `intake` doesn't exist yet).

4. Implement — append after `render_body`:

```python
def _default_create_issue(title: str, body: str, labels: str) -> str:
    """Default create_issue callable: shells out to providers/cli.py's `tracker create`
    subcommand, mirroring smoke_gate.sh's existing subprocess invocation of the same CLI."""
    cli_path = os.path.join(os.path.dirname(__file__), "providers", "cli.py")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        body_path = fh.name
    try:
        result = subprocess.run(
            [sys.executable, cli_path, "tracker", "create",
             "--title", title, "--body-file", body_path, "--labels", labels],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(body_path)
    if result.returncode != 0:
        return ""  # fail closed even if stdout carries stray text
    return (result.stdout or "").strip()


def intake(
    clone_dir: str, manifest_path: str, *, artifacts_dir: str,
    create_issue=None, run_verifier=None, adapter_loops=None,
) -> IntakeResult:
    """R2 -> R3 -> R4 -> R5 orchestration (R6 audit trail wired in Task 8).

    create_issue: (title, body, labels) -> issue_id str; defaults to the real tracker CLI.
    run_verifier: kwargs -> verdict text str; defaults to verifier.resolve_and_run.
    adapter_loops: injectable override for adapter.get(clone_dir, "loops") (test seam);
    None means "look it up for real."
    """
    create_issue = create_issue or _default_create_issue
    run_verifier = run_verifier or _verifier.resolve_and_run

    manifest = read_manifest(clone_dir, manifest_path)
    validate_manifest(manifest)

    loops = adapter_loops if adapter_loops is not None else _adapter.get(clone_dir, "loops")
    loop_entry = cross_check(manifest, loops)
    producing_loop = manifest["producing_loop"]

    verifier_path = (loop_entry.get("verification") or {}).get("verifier")
    if not verifier_path:
        raise HandoffError(
            "verifier_undeclared", f"loop '{producing_loop}' declares no verification.verifier"
        )

    verdict_out = os.path.join(artifacts_dir, f"loop-{producing_loop}.md")
    verdict_text = run_verifier(
        clone_dir=clone_dir, loop_name=producing_loop, verifier_path=verifier_path,
        side_effect_level=loop_entry["side_effect_level"], issue_num="",
        factory_repo_slug=_identity.SLUG,
    )
    os.makedirs(os.path.dirname(verdict_out) or ".", exist_ok=True)
    with open(verdict_out, "w", encoding="utf-8") as fh:
        fh.write(verdict_text)

    parsed = _verdict.parse_verdict(verdict_text) or {}
    status = parsed.get("status")
    if status not in HANDOFF_ACCEPT_STATUSES:
        reason = f"observed STATUS: {status}"
        for line in verdict_text.splitlines():
            if line.startswith("REASON:"):
                reason += f"; {line}"
                break
        raise HandoffError("verdict_not_passing", reason)

    body = render_body(manifest, verdict_out)
    title = f"[intake] {manifest['proposed_ticket']['title']}"
    labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
    issue_id = create_issue(title, body, labels)
    if not issue_id:
        raise HandoffError("issue_create_failed", "tracker create_item returned an empty result")

    return IntakeResult(accepted=True, issue_id=issue_id, artifact_id=manifest["artifact_id"])
```

5. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k intake
   ```
   Expected: `5 passed`.

6. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py tests/fixtures/verifiers/handoff_pass.sh tests/fixtures/verifiers/handoff_blocked.sh
   git commit -m "feat(#199): intake orchestration — R2-R5 accept path + reject reason codes"
   ```

---

**Task 7 addendum (operator review):** `_default_create_issue` is the one `gh`-reaching
function; give it a test that never spawns the real CLI. Append to `tests/test_handoff.py`:

```python
def test_default_create_issue_argv_and_fail_closed(monkeypatch, tmp_path):
    calls = []

    class _R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def fake_run(argv, **kw):
        calls.append(argv)
        return _R(0, "123\n") if len(calls) == 1 else _R(1, "stray text")

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    assert handoff._default_create_issue("t", "b", "needs-triage,manifest-intake") == "123"
    argv = calls[0]
    assert argv[0] == sys.executable and argv[1].endswith(os.path.join("providers", "cli.py"))
    assert argv[2:4] == ["tracker", "create"]
    assert "--title" in argv and "--body-file" in argv
    assert argv[argv.index("--labels") + 1] == "needs-triage,manifest-intake"
    assert handoff._default_create_issue("t", "b", "x") == ""  # rc != 0 -> fail closed
```

---

## Task 8: R6 audit trail — `runs.jsonl` row (accept and reject) with `origin`

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def test_intake_records_runs_jsonl_row_on_accept(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=_stub_create_issue(issue_id="99"),
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["intent"] == "intake"
    assert rec["stage"] == "manifest_intake"
    assert rec["verdict"] == "ACCEPTED"
    assert rec["issue_number"] == 99
    assert rec["origin"] == "target-loop:nightly-scan-triage"
    assert rec["detail"]["artifact_id"] == "scan-2026-08-30-001"
    assert rec["detail"]["created_issue"] == 99
    assert rec["detail"]["reject_reason"] == ""


def test_intake_records_runs_jsonl_row_on_reject(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["issue_number"] == 0
    assert rec["origin"] == "target-loop:ghost-loop"
    assert rec["detail"]["reject_reason"] == "unknown_producing_loop"
    assert rec["detail"]["created_issue"] == ""


def test_intake_records_origin_factory_when_producing_loop_unreadable(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    bad_manifest = _valid_manifest()
    del bad_manifest["producing_loop"]  # R2 schema_invalid fires before producing_loop is read
    manifest_name = _write_manifest_file(clone_dir, bad_manifest)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    assert exc.value.code == "schema_invalid"

    rec = _json.loads(jsonl.read_text().strip())
    assert rec["origin"] == "factory"
    assert rec["detail"]["artifact_id"] == "scan-2026-08-30-001"  # read before producing_loop


def test_intake_run_id_defaults_to_intake_artifact_id(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    monkeypatch.delenv("RUN_ID", raising=False)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    rec = _json.loads(jsonl.read_text().strip())
    assert rec["run_id"] == "intake-scan-2026-08-30-001"


def test_intake_run_id_uses_env_when_set(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    monkeypatch.setenv("RUN_ID", "abc-999")

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    rec = _json.loads(jsonl.read_text().strip())
    assert rec["run_id"] == "abc-999"


def test_intake_records_reject_row_for_malformed_adapter_yaml(tmp_path, monkeypatch):
    """Reachable purely from target-controlled input: the target authors its own
    .factory/adapter.yaml. adapter.get() raises AdapterError (not HandoffError) on a
    malformed file -- this must still produce a runs.jsonl reject row (R6/AC2), not
    an uncaught traceback that leaves no audit trail."""
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    (clone_dir / ".factory" / "adapter.yaml").write_text("key: [unclosed")  # unparseable

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(),  # adapter_loops NOT passed -- exercises the real _adapter.get() path
        )
    assert exc.value.code == "unknown_producing_loop"

    rec = _json.loads(jsonl.read_text().strip())
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "unknown_producing_loop"
```

   `_run_record` was already imported in Task 7 (alongside the `_hermetic_run_record` autouse
   fixture) — the tests above reference `_run_record.JSONL_PATH`/`_run_record._post_seq`
   directly, overriding the autouse fixture's `tmp_path`-scoped defaults with their own `jsonl`
   variable so they can assert on its contents (monkeypatch stacking makes this safe).

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k "runs_jsonl or run_id or malformed_adapter"
   ```
   Expected: `AssertionError` (jsonl file empty — `intake` doesn't call `cmd_record` yet) or
   `FileNotFoundError` if `jsonl.read_text()` runs before any write;
   `test_intake_records_reject_row_for_malformed_adapter_yaml` fails with an uncaught
   `factory_core.adapter.AdapterError` instead of the expected `HandoffError`.

3. Implement — wrap `intake`'s body in a `try`/`except HandoffError` that records before
   re-raising, and record again on the success path. Replace the whole `intake` function body
   with:

```python
def intake(
    clone_dir: str, manifest_path: str, *, artifacts_dir: str,
    create_issue=None, run_verifier=None, adapter_loops=None,
) -> IntakeResult:
    """R2 -> R3 -> R4 -> R5 -> R6 orchestration. Every failure is recorded to runs.jsonl
    (R6) via run_record.cmd_record, called in-process (not a subprocess), then re-raised
    as HandoffError. On success, one ACCEPTED row is recorded after the issue is created.

    create_issue: (title, body, labels) -> issue_id str; defaults to the real tracker CLI.
    run_verifier: kwargs -> verdict text str; defaults to verifier.resolve_and_run.
    adapter_loops: injectable override for adapter.get(clone_dir, "loops") (test seam);
    None means "look it up for real."
    """
    create_issue = create_issue or _default_create_issue
    run_verifier = run_verifier or _verifier.resolve_and_run

    artifact_id = "unknown"
    producing_loop = None
    try:
        manifest = read_manifest(clone_dir, manifest_path)
        if isinstance(manifest.get("artifact_id"), str) and manifest["artifact_id"]:
            artifact_id = manifest["artifact_id"]
        validate_manifest(manifest)
        artifact_id = manifest["artifact_id"]
        producing_loop = manifest["producing_loop"]

        if adapter_loops is not None:
            loops = adapter_loops
        else:
            try:
                loops = _adapter.get(clone_dir, "loops")
            except _adapter.AdapterError as exc:
                # adapter.get() raises AdapterError (not HandoffError) on a malformed
                # target .factory/adapter.yaml -- unparseable YAML, a bad loop entry,
                # etc. This is target-controlled input reachable from an intake call,
                # and it must still produce a runs.jsonl row (R6/AC2), not an uncaught
                # traceback. The producing loop cannot be confirmed either way, which
                # is exactly what unknown_producing_loop means.
                raise HandoffError(
                    "unknown_producing_loop", f"adapter.yaml could not be loaded: {exc}"
                ) from exc
        loop_entry = cross_check(manifest, loops)

        verifier_path = (loop_entry.get("verification") or {}).get("verifier")
        if not verifier_path:
            raise HandoffError(
                "verifier_undeclared", f"loop '{producing_loop}' declares no verification.verifier"
            )

        verdict_out = os.path.join(artifacts_dir, f"loop-{producing_loop}.md")
        verdict_text = run_verifier(
            clone_dir=clone_dir, loop_name=producing_loop, verifier_path=verifier_path,
            side_effect_level=loop_entry["side_effect_level"], issue_num="",
            factory_repo_slug=_identity.SLUG,
        )
        os.makedirs(os.path.dirname(verdict_out) or ".", exist_ok=True)
        with open(verdict_out, "w", encoding="utf-8") as fh:
            fh.write(verdict_text)

        parsed = _verdict.parse_verdict(verdict_text) or {}
        status = parsed.get("status")
        if status not in HANDOFF_ACCEPT_STATUSES:
            reason = f"observed STATUS: {status}"
            for line in verdict_text.splitlines():
                if line.startswith("REASON:"):
                    reason += f"; {line}"
                    break
            raise HandoffError("verdict_not_passing", reason)

        body = render_body(manifest, verdict_out)
        title = f"[intake] {manifest['proposed_ticket']['title']}"
        labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
        issue_id = create_issue(title, body, labels)
        if not issue_id:
            raise HandoffError("issue_create_failed", "tracker create_item returned an empty result")
    except HandoffError as exc:
        _record_intake(
            manifest_path=manifest_path, artifact_id=artifact_id, producing_loop=producing_loop,
            issue=0, verdict="REJECTED", reject_reason=exc.code, created_issue="",
        )
        raise

    # GitHubTracker.create_item returns a numeric string, but the Tracker ABC's ids
    # are opaque everywhere (docs/adapter-authoring-guide.md) -- e.g. JiraTracker
    # returns "PROJ-123". run_record.py's --issue is an existing int-typed field
    # (unrelated to this ticket), so a non-digit id is recorded as issue=0 with the
    # real id preserved verbatim in detail.created_issue, rather than crashing after
    # the issue has already been created.
    issue_num = int(issue_id) if str(issue_id).isdigit() else 0
    _record_intake(
        manifest_path=manifest_path, artifact_id=artifact_id, producing_loop=producing_loop,
        issue=issue_num, verdict="ACCEPTED", reject_reason="", created_issue=issue_id,
    )
    return IntakeResult(accepted=True, issue_id=issue_id, artifact_id=artifact_id)


def _record_intake(
    *, manifest_path: str, artifact_id: str, producing_loop, issue: int, verdict: str,
    reject_reason: str, created_issue,
) -> None:
    """R6: writes intake's own accept/reject decision as a runs.jsonl row -- the entire
    audit trail for a rejected manifest, which otherwise creates no GitHub issue and
    would leave no trace anywhere. Calls run_record.cmd_record in-process (an
    argparse.Namespace, not a subprocess) so tests can monkeypatch JSONL_PATH/_post_seq
    exactly as tests/test_run_record.py already does."""
    run_id = os.environ.get("RUN_ID") or f"intake-{artifact_id}"
    origin = f"target-loop:{producing_loop}" if producing_loop else "factory"
    ns = argparse.Namespace(
        run_id=run_id, issue=issue, intent="intake", stage="manifest_intake", verdict=verdict,
        tokens_in=None, tokens_out=None, cost_usd=None, duration_ms=None,
        detail=[
            f"manifest_path={manifest_path}", f"artifact_id={artifact_id}",
            f"created_issue={created_issue}", f"reject_reason={reject_reason}",
        ],
        origin=origin,
    )
    _run_record.cmd_record(ns)
```

   Delete the old (Task 7) `intake` function definition entirely — this replaces it in full.

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q
   ```
   Expected: all tests in the file pass.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): R6 audit trail — runs.jsonl row on accept/reject, origin attribution"
   ```

---

## Task 9: CLI (`validate` / `intake` subcommands)

**Files:** `scripts/factory_core/handoff.py`, `tests/test_handoff.py`

### TDD Steps

1. Append to `tests/test_handoff.py`:

```python
def test_cli_validate_ok(tmp_path, capsys):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    handoff.main([
        "--clone-dir", str(clone_dir), "validate", "--manifest-path", manifest_name,
    ])
    assert "manifest OK" in capsys.readouterr().out


def test_cli_validate_invalid_exits_nonzero(tmp_path, capsys):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest = _valid_manifest()
    del manifest["artifact_id"]
    manifest_name = _write_manifest_file(clone_dir, manifest)
    with pytest.raises(SystemExit) as exc:
        handoff.main([
            "--clone-dir", str(clone_dir), "validate", "--manifest-path", manifest_name,
        ])
    assert exc.value.code == 1
    assert "schema_invalid" in capsys.readouterr().err


def test_cli_intake_end_to_end_with_real_adapter_yaml(tmp_path, monkeypatch, capsys):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    import yaml as _yaml
    adapter_doc = {"schema_version": 2, "loops": [_loop_entry(
        verification={"verifier": "verify.sh", "stop_condition": "n/a"},
    )]}
    (clone_dir / ".factory" / "adapter.yaml").write_text(_yaml.safe_dump(adapter_doc))

    artifacts_dir = tmp_path / "artifacts"
    calls = []
    monkeypatch.setattr(
        handoff, "_default_create_issue",
        lambda title, body, labels: (calls.append((title, body, labels)), "5150")[1],
    )

    handoff.main([
        "--clone-dir", str(clone_dir), "intake",
        "--manifest-path", manifest_name, "--artifacts-dir", str(artifacts_dir),
    ])
    assert "intake OK" in capsys.readouterr().out
    assert len(calls) == 1


def test_cli_intake_rejects_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    import yaml as _yaml
    adapter_doc = {"schema_version": 2, "loops": [_loop_entry()]}
    (clone_dir / ".factory" / "adapter.yaml").write_text(_yaml.safe_dump(adapter_doc))

    with pytest.raises(SystemExit) as exc:
        handoff.main([
            "--clone-dir", str(clone_dir), "intake",
            "--manifest-path", manifest_name, "--artifacts-dir", str(tmp_path / "artifacts"),
        ])
    assert exc.value.code == 1
    assert "unknown_producing_loop" in capsys.readouterr().err
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q -k cli_
   ```
   Expected: `AttributeError: module 'factory_core.handoff' has no attribute 'main'`.

3. Implement — append at the end of `scripts/factory_core/handoff.py`, replacing the placeholder
   `if __name__ == "__main__": pass` block from Task 1:

```python
def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Validate and intake an artifact handoff manifest (A5)")
    p.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--manifest-path", required=True)

    i = sub.add_parser("intake")
    i.add_argument("--manifest-path", required=True)
    i.add_argument("--artifacts-dir", default=os.environ.get("ARTIFACTS_DIR", "."))

    args = p.parse_args(argv)
    if args.cmd == "validate":
        try:
            manifest = read_manifest(args.clone_dir, args.manifest_path)
            validate_manifest(manifest)
            print("manifest OK")
        except HandoffError as exc:
            print(f"manifest INVALID [{exc.code}]: {exc.message}", file=sys.stderr)
            sys.exit(1)
    elif args.cmd == "intake":
        try:
            result = intake(args.clone_dir, args.manifest_path, artifacts_dir=args.artifacts_dir)
            print(f"intake OK: issue {result.issue_id}")
        except HandoffError as exc:
            print(f"intake REJECTED [{exc.code}]: {exc.message}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
```

   Remove the now-redundant `if __name__ == "__main__": pass` block from Task 1 (this task's
   `main()` + guard replaces it).

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_handoff.py -x -q
   ```
   Expected: full file passes — check the pytest summary line reports zero failures rather than
   matching an exact count, since that count shifts if any prior task's test list changes.

5. Commit:
   ```bash
   git add scripts/factory_core/handoff.py tests/test_handoff.py
   git commit -m "feat(#199): handoff.py CLI — validate/intake subcommands"
   ```

---

## Task 10: `verifier.py` — `ORIGIN:` suffix line on all four `resolve_and_run` return points

**Shipped ahead of this ticket by #378 (PR #379) — SKIP; do not re-implement.** `scripts/factory_core/verifier.py` already appends `ORIGIN: target-loop:<loop_name>` on all four `resolve_and_run` return points, with tests in `tests/test_verifier.py`. This ticket carries `Depends on: #378`, so the implement run starts from a main that already contains it; the implementer must not touch the files this task used to name. Task numbers are kept stable so cross-references below remain valid.

---

## Task 11: `run_record.py` — `--origin` flag on the `record` subcommand

**Shipped ahead of this ticket by #378 (PR #379) — SKIP; do not re-implement.** `scripts/factory_core/run_record.py` `record` already accepts `--origin` (default `factory`) and writes the `origin` field on every row, with tests in `tests/test_run_record.py` — Task 8's `rec["origin"]` assertions rely on this. This ticket carries `Depends on: #378`, so the implement run starts from a main that already contains it; the implementer must not touch the files this task used to name. Task numbers are kept stable so cross-references below remain valid.

---

## Task 12: `docs/triage-labels.md` — `manifest-intake` row

**Files:** `docs/triage-labels.md`

### Steps

`docs/triage-labels.md` has no test file (unlike `docs/adapter-authoring-guide.md`, which
Task 13 guards with `tests/test_adapter_authoring_guide.py`) — verify this task by direct read.

1. Edit the "Workflow flags" table in `docs/triage-labels.md`, adding a new row after
   `direct-to-pr`:

```markdown
| `manifest-intake` | Applied by `handoff.py intake` (A5) alongside `needs-triage` on every GitHub issue created from a target-loop artifact handoff manifest. Env-overridable via `FACTORY_MANIFEST_LABEL`. Never applied together with `ready-for-agent` — a manifest-created issue always starts at triage. |
```

2. Read the file back to confirm the row landed in the right table:
   ```bash
   grep -n "manifest-intake" docs/triage-labels.md
   ```

3. Commit:
   ```bash
   git add docs/triage-labels.md
   git commit -m "docs(#199): triage-labels — manifest-intake workflow-flag row"
   ```

---

## Task 13: `docs/adapter-authoring-guide.md` — "Handoff manifest (A5)" section

**Files:** `docs/adapter-authoring-guide.md`, `tests/test_adapter_authoring_guide.py`

### TDD Steps

1. Append to `tests/test_adapter_authoring_guide.py`:

```python
def test_guide_documents_handoff_manifest_a5_section():
    text = _text()
    assert "## Handoff manifest (A5)" in text
    for token in (
        "schema_version", "artifact_id", "producing_loop", "side_effect_level",
        "source_references", "acceptance_thresholds", "proposed_ticket",
        "scripts/factory_core/handoff.py",
        "unknown_producing_loop", "side_effect_level_mismatch",
        "producing_loop_factory_owned", "verifier_undeclared", "verdict_not_passing",
        "schema_invalid", "unsafe_string", "body_contains_fence", "body_too_large",
        "issue_create_failed",
    ):
        assert token in text, f"missing A5 token: {token}"
    assert "never executed" in text or "never runs it" in text
```

2. Verify it fails:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_adapter_authoring_guide.py -x -q -k handoff_manifest
   ```
   Expected: `assert "## Handoff manifest (A5)" in text` fails (section doesn't exist yet).

3. Implement — append a new section to `docs/adapter-authoring-guide.md`, after the existing
   "## Worked example: GitLab CodeHost seam proof" section (at the end of the file). This block
   is wrapped in a 4-backtick fence (not 3) because it contains an inner ` ```yaml ` example —
   a 3-backtick outer fence would be closed early by that inner block's own closing ` ``` `,
   the same nested-fence rule the spec itself uses at its R2/R5 code blocks:

````markdown

## Handoff manifest (A5)

A loop entry's `handoff.manifest` field (A1.5) points at a flat YAML file the target loop's own
tooling already wrote. Unlike `verification.verifier`, the manifest is **never executed** —
`scripts/factory_core/handoff.py` reads and validates it, never runs it. (The A1.5 spec's
`handoffs/triage_handoff.py`-shaped example predates this decision and is a stale illustration
in the archived spec; this section is the current, authoritative shape.)

### Schema

```yaml
schema_version: 1
artifact_id: scan-2026-08-30-001            # non-empty string, opaque, ^[A-Za-z0-9._-]+$, <=128 chars
producing_loop: nightly-scan-triage         # must match a loops[].name in .factory/adapter.yaml
side_effect_level: 2                        # int 1-6; must equal that loop's declared side_effect_level
verifier_verdict:                           # OPTIONAL, informational only -- never gated on
  path: artifacts/scan_verdict.md
source_references:                          # list of strings, <=50 items, <=512 chars each
  - scanner_output.json
acceptance_thresholds:                      # list of strings, same limits
  - "false_positive_rate < 0.05"
proposed_ticket:
  title: "Triage: 3 new findings in payments module"   # <=200 chars, no newlines/control chars
  body: |                                                # <=32 KiB, no fence lines, no provenance marker
    ## Findings
    ...
```

Unknown top-level keys (and unknown keys inside `verifier_verdict`/`proposed_ticket`) are a hard
rejection. The manifest file itself is capped at 256 KiB, checked before YAML parsing.

### Intake path

`scripts/factory_core/handoff.py intake` runs: R2 (schema validation) -> R3 (cross-check
`producing_loop`/`side_effect_level` against the adapter's `loops:` entries) -> R4 (runs the
loop's declared A3 verifier itself via `verifier.resolve_and_run` and gates on `STATUS: PASS`
only -- never trusts a verdict file the manifest merely references) -> R5 (creates a GitHub
issue via the existing `tracker create` primitive, labeled exactly `needs-triage,manifest-intake`
-- never `ready-for-agent`) -> R6 (records an accept/reject row to `runs.jsonl` via
`run_record.cmd_record`, in-process, for every manifest processed).

### Reason codes

| Code | Meaning |
|---|---|
| `schema_invalid` | Shape/type/required/unknown-key/file-size violation |
| `unsafe_string` | A string rendered outside a fence contains a backtick or newline |
| `body_contains_fence` | `proposed_ticket.body` contains a fence line or the provenance closing marker |
| `unknown_producing_loop` | `producing_loop` matches no `loops[].name` in the adapter |
| `side_effect_level_mismatch` | Manifest's declared level != the loop's declared level |
| `producing_loop_factory_owned` | Loop's declared level >= 4 (factory-owned until #196) |
| `verifier_undeclared` | Loop entry has no `verification.verifier` to run |
| `verdict_not_passing` | Intake-produced verdict `STATUS` != `PASS` |
| `body_too_large` | Rendered issue body would exceed 60,000 chars |
| `issue_create_failed` | `create_issue` returned an empty/falsy result |

### Trust boundary

Only `handoff.py intake`, running with the factory's own tracker credentials, ever calls
`tracker create`. A manifest may set no labels beyond the fixed pair, no assignee/milestone/
project, and no dependency edges — the proposed ticket body is always rendered inside a fenced
code block (`scheduler.sh::_scan_body_for_deps` skips fenced code when scanning for
`Depends on:`), and every string rendered outside that fence is checked for backtick/newline
injection (`unsafe_string`). See
`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md` for the full design.
````

4. Verify it passes:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_adapter_authoring_guide.py -q
   ```
   Expected: all tests pass, including pre-existing ones (file grew, no existing section removed).

5. Commit:
   ```bash
   git add docs/adapter-authoring-guide.md tests/test_adapter_authoring_guide.py
   git commit -m "docs(#199): adapter-authoring-guide — Handoff manifest (A5) section"
   ```

---

## Task 14: `refinement-skills/VERIFIER-CONTRACT.md` — document the `ORIGIN:` verdict line

**Shipped ahead of this ticket by #378 (PR #379) — SKIP; do not re-implement.** `refinement-skills/VERIFIER-CONTRACT.md` already documents the `ORIGIN:` verdict line and the `origin` ledger field; `tests/test_verifier_contract_doc_referenced.py` already guards both tokens. This ticket carries `Depends on: #378`, so the implement run starts from a main that already contains it; the implementer must not touch the files this task used to name. Task numbers are kept stable so cross-references below remain valid.

---

## Task 15: Full test suite + hermetic-leak check

**Files:** none (verification only)

### Steps

1. Run the full suite exactly as CI does:
   ```bash
   cd "$(git rev-parse --show-toplevel)" && python -m pytest tests/ -v
   ```
   Expected: all tests pass, including every test added in Tasks 1-14 and every pre-existing
   test (no regressions in `tests/test_verifier.py`, `tests/test_run_record.py`,
   `tests/test_adapter.py`, `tests/test_adapter_authoring_guide.py`).

2. Confirm no test in `tests/test_handoff.py` touches the real state dir, a real `gh`, or the
   network (R6's Hermetic-test statement):
   ```bash
   grep -n "SCHEDULER_STATE_DIR\|subprocess.run.*gh \|urllib" tests/test_handoff.py
   ```
   Expected: the only `SCHEDULER_STATE_DIR` matches are the two lines of the autouse
   `_hermetic_run_record` fixture (Task 7); no `gh`/`urllib` matches. Every test in the file is covered by the `_hermetic_run_record`
   autouse fixture added in Task 7 (redirects `run_record.JSONL_PATH` to a per-test `tmp_path`
   and no-ops `_post_seq`), and `create_issue` is always a stub or a monkeypatched
   `handoff._default_create_issue` in every `intake()`-calling test — never the real
   `_default_create_issue` subprocess path.

3. Run the CI bash suites that touch `run_record` and verdict parsing (required, exit 0):
   ```bash
   bash tests/test_run_record_hermetic.sh && bash tests/test_verdict_gate_check.sh && bash tests/test_smoke_gate.sh
   ```
   Expected: `OK` / `PASS` / `Results: … 0 failed` — the hermetic guard must not flag
   `tests/test_handoff.py`, and the verdict-gate suite must be unaffected.

4. No commit — this task is verification-only. If step 1 surfaces a regression, fix it in the
   task where it was introduced (amend that task's commit is not appropriate per repo
   convention — create a small follow-up commit instead) and re-run.

---

## Summary

12 live tasks (10, 11 and 14 shipped ahead as #378 — skip-stubs above), ~11 commits (Task 15
is verification-only). Implements every requirement in the spec (R1-R7): a pure-read manifest
validator (R1/R2), adapter cross-check (R3), intake-produced verifier gating (R4), tracker-create
issue rendering with injection containment (R5), a `runs.jsonl` audit trail for every
accept/reject decision (R6), and — consuming #378's `ORIGIN:` line and `--origin` flag — origin
attribution on intake rows (R7). No changes to `adapter.py`, `breaker.py`, `verdict.py`,
`verifier.py`, `run_record.py` or `VERIFIER-CONTRACT.md`: this ticket stays entirely outside
the Blast-Radius hotspot set, so its implement run is expected to fit the factory window and
clear validate without a human gate.

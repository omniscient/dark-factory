# Failure-Spend Taxonomy and Retry/Fallback Governance — Implementation Plan

**Issue:** #238

> **For agentic workers:** Execute task-by-task, in order. Each step is one small action.
> TDD throughout: write the failing test, run it and see it fail, implement, run it and see
> it pass, commit. Touch only the files listed in the File Structure table.

**Goal:** Add one declarative failure-class table (`scripts/factory_core/failure_taxonomy.py`)
that `error_signature.classify()`'s signatures, `model_adapter.py`'s nine local-endpoint
classes and `preview_helper.py`'s two enums all map onto, and record the class of each
failed run in `harness_economics.failure_spend.by_class`. This ticket declares policy and
records the class. It enforces nothing new.

**Architecture:** A new stdlib-only module owns the table (`FAILURE_TAXONOMY`), the
signature→class lookup (`SIGNATURE_TO_CLASS`, `class_for_signature()`) and the nine
local-endpoint constants, which `model_adapter.py` now re-exports with the same values and
order. `run_record.py` adds the ledger part of `by_class` at assembly, and a new
`run-record annotate-failure` verb adds the signature's class afterwards. `entrypoint.sh`'s
`_write_error_signature` records the signature it already writes in a global, and
`on_failure()` makes one bounded annotate call with it, after the failure comment and
before the cost report. The order and argv of every
existing call stay as they are.

**Tech Stack:** Python 3 (stdlib `dataclasses`, `types.MappingProxyType`, `tempfile`, `stat`),
bash, pytest, GitHub Actions (`.github/workflows/ci.yml`).

**Spec:** `docs/superpowers/specs/2026-11-27-failure-spend-taxonomy-design.md` (approved at
`b763763`).

---

## Hard constraints (from the spec, CLAUDE.md and the operator review)

- No edit to `scripts/factory_core/breaker.py`, `scripts/factory_core/error_signature.py`,
  `scheduler.sh`, `scripts/factory_core/preview_helper.py`, `scripts/factory_core/model_proxy.py`,
  `scripts/factory_core/cli.py`, `scripts/budget_gate.sh`, `scripts/factory_core/deconflict.py`,
  `deploy/**`, `.github/workflows/publish.yml`.
- No persisted string is renamed. `model_adapter.FAILURE_CLASSES` keeps its exact values and order.
- `failure_taxonomy.py` imports nothing from `factory_core.providers`. `model_adapter`
  imports from it, never the other way round.
- In `entrypoint.sh`, `_write_error_signature` keeps its position, argv and inputs. It is not
  moved earlier: an earlier call would shorten `elapsed_seconds` and could turn a
  `substantive:*` failure into an uncounted `environmental:delivery_failure`, which is a
  breaker behaviour change. `run-record assemble`'s argv is unchanged. The workflow loop's
  failure branch (`run_post_mortem` / `_write_error_signature` / `exit "$EXIT_CODE"`) and the
  success-path assemble are **not edited** (project memory, AVOID: that branch is not reached
  on an ordinary workflow failure, so recording there would be dead code).
- Only `session_window_pause` is `enforced=True`. Every `cross_endpoint_retry`,
  `fallback_model_allowed` and `fallback_phase_allowed` is `False`. No multiplier is `0.0`.
- The implementation will stop at the blast-radius hold (`STATUS: HUMAN_REQUIRED`, triggers
  `hotspot` for `scripts/factory_core/run_record.py` and `migration-seed` for
  `.github/workflows/ci.yml`). The operator expects this. Do not try to avoid it.

## Base: this plan is implemented after #645 (plan review by execution, 2026-11-27)

#645 (run-record model/cache capture) merges first and edits the same two files. This plan
was executed literally on #645's signed head (`dc25fd0`) and on plain `main` (`d459e67`).
On plain `main` every anchor applies. On #645's code three things differ, and the steps
below are written for that base:

1. **Task 6 Step 3, imports.** #645 added `from . import session_usage` between
   `from . import model_proxy` and `from . import verdict as _verdict`, so the two-line old
   text no longer exists. The step now adds one line after `from . import verdict as _verdict`.
2. **Task 8 Step 1, `EXPECTED_ASSEMBLE`.** #645 added `--transcripts-dir
   "${HOME}/.claude/projects"` to the failure-path `run-record assemble` call (after
   `--archon-log-file`) and routed it through `_cli_with_hosting_fallback`. The guard
   `assemble_argv_is_unchanged` pins today's argv, so its expected string carries that
   argument. Measured without this change: red `15 passed, 9 failed`, green
   `23 passed, 1 failed`; with it: `16 passed, 8 failed` and `24 passed, 0 failed`.
3. **Task 8 Step 1, hermeticity.** `on_failure` now calls `_keep_run_transcripts`, which
   copies `$HOME/.claude/projects`. Like #645's other three `on_failure` harnesses, the new
   harness stubs it to a no-op.

`tests/test_run_record.py` line numbers in Task 5 Step 1 are one higher after #645 from
`test_parse_archon_cost_basic` onward; the step's old and new strings are what to match.
In Task 6 Step 3, "at the end of the dispatch chain" is after #645's `backfill-usage` branch.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/factory_core/failure_taxonomy.py` | Create | `FailureClass` dataclass, `FAILURE_TAXONOMY`, `SIGNATURE_TO_CLASS`, `class_for_signature()`, the nine local constants + `FAILURE_CLASSES`, preview-helper tuples, breaker-outcome constants |
| `tests/test_failure_taxonomy.py` | Create | Spec tests 1, 2, 3, 3a, 3b, 3c, 3d |
| `scripts/factory_core/providers/model_adapter.py` | Modify | Replace the nine constants + `FAILURE_CLASSES` with a re-export from `failure_taxonomy` (no logic change) |
| `scripts/factory_core/run_record.py` | Modify | `POLICY_VERSION` 1.1→1.2; `by_class` ledger part in `_compute_harness_economics`; `annotate-failure` verb |
| `tests/test_run_record.py` | Modify | Update the exact-dict and version assertions; spec tests 4, 4a, 4b, 5, 8, 9 |
| `entrypoint.sh` | Modify | `_write_error_signature` records the signature it wrote in `ERROR_SIGNATURE_WRITTEN`; `on_failure` makes one bounded annotate call with it |
| `tests/test_entrypoint_failure_signature.sh` | Create | Spec test 6 (trap-level cases + guards) |
| `.github/workflows/ci.yml` | Modify | One `- run: bash tests/test_entrypoint_failure_signature.sh` step |

Environment for every Python command below: run from the repo root with `PYTHONPATH=scripts`
(what CI sets).

---

## Task 1: Taxonomy module — dataclass and local-endpoint classes

**Files:**
- Create: `scripts/factory_core/failure_taxonomy.py`
- Create: `tests/test_failure_taxonomy.py`

- [ ] **Step 1: Write the failing tests (spec tests 3a and the literal-order half of 3)**

Create `tests/test_failure_taxonomy.py`:

```python
"""Failure-spend taxonomy (#238): the declared table, the signature mapping and the
guarantees that nothing outside the module reads its policy fields.
Spec: docs/superpowers/specs/2026-11-27-failure-spend-taxonomy-design.md."""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from factory_core import failure_taxonomy as ft  # noqa: E402

# model_adapter.py's order before #238, written out: comparing the two modules alone
# would pass for any order once one re-exports the other (spec test 3).
_LOCAL_CLASSES_IN_ORDER = (
    "endpoint_unreachable", "timeout_connect", "timeout_time_to_first_token", "timeout_idle",
    "timeout_total", "cancellation", "protocol_incompatibility", "capability_evidence_invalid",
    "destination_policy_denial",
)


def test_failure_taxonomy_preserves_model_adapter_order():
    assert ft.FAILURE_CLASSES == _LOCAL_CLASSES_IN_ORDER
    assert all(c in ft.FAILURE_TAXONOMY for c in ft.FAILURE_CLASSES)
    assert {ft.FAILURE_TAXONOMY[c].detector for c in ft.FAILURE_CLASSES} == {"model_adapter"}


def test_failure_taxonomy_imports_nothing_from_providers():
    code = ("import sys, factory_core.failure_taxonomy; "
            "print(sorted(m for m in sys.modules "
            "if m.endswith('.model_adapter') or m.endswith('.model_endpoint')))")
    env = {**os.environ, "PYTHONPATH": str(REPO / "scripts")}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "[]"
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'factory_core.failure_taxonomy'`.

- [ ] **Step 3: Create the module with the dataclass and the §2a rows**

Create `scripts/factory_core/failure_taxonomy.py`:

```python
"""Failure-spend taxonomy (#238): one declared policy record per failure class.

Declares and records; enforces nothing. breaker.py, error_signature.py and scheduler.sh
read none of it (tests/test_failure_taxonomy.py::test_nothing_reads_the_policy_fields).
`enforced` says whether today's code already behaves as a row declares; `enforced_today`
names what actually governs that failure now. Enforcement is follow-up work with its own
reviewed spec (hybrid-execution contract §4.10). Spec:
docs/superpowers/specs/2026-11-27-failure-spend-taxonomy-design.md.

Imports nothing from .providers: the default cloud path must not load model_adapter by
importing this module. model_adapter re-exports its failure-class constants from here.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional

STATE_TRANSITIONS = ("continue", "trip_to_blocked", "abstain_escalate")
DETECTORS = ("error_signature", "model_adapter", "none_yet")


@dataclass(frozen=True)
class FailureClass:
    retryable: bool                 # may be redispatched on the SAME destination
    cross_endpoint_retry: bool      # always False in #238 (contract §4.3)
    max_attempts: Optional[int]     # None = the phase's existing flat ceiling
    fallback_model_allowed: bool
    fallback_phase_allowed: bool
    requires_human: bool
    state_transition: str           # one of STATE_TRANSITIONS
    token_budget_multiplier: Optional[float]  # None = no value declared; never 0.0
    detector: str                   # one of DETECTORS
    enforced: bool                  # today's code already produces this whole record
    enforced_today: str             # what governs this failure today (repo-relative file:line) or "none"


def _row(*, retryable, max_attempts, requires_human, state_transition, multiplier, detector,
         enforced_today, enforced=False):
    # No class in #238 authorizes a different destination or a fallback, so those three
    # fields are fixed here rather than declared per row.
    return FailureClass(
        retryable=retryable, cross_endpoint_retry=False, max_attempts=max_attempts,
        fallback_model_allowed=False, fallback_phase_allowed=False,
        requires_human=requires_human, state_transition=state_transition,
        token_budget_multiplier=multiplier, detector=detector,
        enforced=enforced, enforced_today=enforced_today,
    )


_BREAKER = "scripts/factory_core/breaker.py"
_CEILING = f"{_BREAKER}:168 evaluate_stop_condition (flat ceiling)"
_CEILING_AND_STUCK = f"{_CEILING} + {_BREAKER}:330 record_failure_signature (#373 stuck)"

# Contract §4.6's local-endpoint classes, persisted verbatim in the request ledger's
# failure_class column. Values and FAILURE_CLASSES' order are load-bearing:
# preview_helper.resolve_outcome and run_record._compute_model_dispatch iterate it.
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
TIMEOUT_CONNECT = "timeout_connect"
TIMEOUT_TIME_TO_FIRST_TOKEN = "timeout_time_to_first_token"
TIMEOUT_IDLE = "timeout_idle"
TIMEOUT_TOTAL = "timeout_total"
CANCELLATION = "cancellation"
PROTOCOL_INCOMPATIBILITY = "protocol_incompatibility"
# Startup-only: evidence problems fail load_verified_entry(), never a request (#558 spec Req. 14).
CAPABILITY_EVIDENCE_INVALID = "capability_evidence_invalid"
DESTINATION_POLICY_DENIAL = "destination_policy_denial"
FAILURE_CLASSES = (
    ENDPOINT_UNREACHABLE, TIMEOUT_CONNECT, TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_IDLE,
    TIMEOUT_TOTAL, CANCELLATION, PROTOCOL_INCOMPATIBILITY, CAPABILITY_EVIDENCE_INVALID,
    DESTINATION_POLICY_DENIAL,
)


def _local(**kw):
    kw.setdefault("requires_human", True)
    kw.setdefault("state_transition", "abstain_escalate")
    kw.setdefault("multiplier", 1.0)
    kw.setdefault("enforced_today", _CEILING_AND_STUCK)
    return _row(retryable=False, max_attempts=0, detector="model_adapter", **kw)


# Spec §2a. The real stop is the phase's flat ceiling plus #373 stuck detection; nothing
# reads max_attempts/fallback_*/the multiplier, so every row is enforced=False.
_LOCAL_ROWS = {
    ENDPOINT_UNREACHABLE: _local(),
    TIMEOUT_CONNECT: _local(),
    TIMEOUT_TIME_TO_FIRST_TOKEN: _local(),
    TIMEOUT_IDLE: _local(),
    TIMEOUT_TOTAL: _local(),
    CANCELLATION: _local(requires_human=False, state_transition="continue", multiplier=None,
                         enforced_today=f"{_CEILING} (no special case)"),
    PROTOCOL_INCOMPATIBILITY: _local(),
    CAPABILITY_EVIDENCE_INVALID: _local(enforced_today=(
        "scripts/factory_core/providers/model_endpoint.py:747 load_verified_entry() fails the "
        f"proxy at startup; the run then fails like any other and is counted by {_CEILING}")),
    DESTINATION_POLICY_DENIAL: _local(),
}


def _merge(*groups):
    merged = {}
    for group in groups:
        for key, value in group.items():
            if key in merged:
                raise ValueError(f"failure class declared twice: {key}")
            merged[key] = value
    return MappingProxyType(merged)


# The single source of truth: no other module redeclares these fields.
FAILURE_TAXONOMY = _merge(_LOCAL_ROWS)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/failure_taxonomy.py tests/test_failure_taxonomy.py
git commit -m "feat(#238): failure_taxonomy module with the local-endpoint classes"
```

---

## Task 2: Factory-native classes and `class_for_signature()`

**Files:**
- Modify: `scripts/factory_core/failure_taxonomy.py`
- Modify: `tests/test_failure_taxonomy.py`

- [ ] **Step 1: Write the failing tests (spec tests 1 and 2)**

Append to `tests/test_failure_taxonomy.py`:

```python
from factory_core.error_signature import SESSION_WINDOW_PAUSE_SIGNATURE, classify  # noqa: E402

_ERROR_SIGNATURE_PY = REPO / "scripts" / "factory_core" / "error_signature.py"

# One input per classify() branch, in the function's own order (error_signature.py:80-126).
_CLASSIFY_CASES = [
    (dict(text="HTTP 429 Too Many Requests", hosting_location="local"), "endpoint_unreachable"),
    (dict(text="", elapsed_seconds=5, commits_since_start=0, artifact_present=False),
     "delivery_failure"),
    (dict(text="rate limit exceeded"), "model_rate_limit"),
    (dict(text="verdict_gate_check.sh: Gate 1 verdict missing/unparseable for issue #12 and "
               "no needs-discussion label"), "verdict_missing"),
    (dict(text="failed to solve: process /bin/sh -c npm ERR! build failed"), "preview_infra"),
    (dict(text="OOS gate: excising out-of-scope files: foo.py"), "scope_spillover"),
    (dict(text="npm ERR! code ELIFECYCLE"), "build_failure"),
    (dict(text="FAILED tests/test_foo.py::test_bar - AssertionError"), "test_failure"),
    (dict(text="nothing recognisable here"), "substantive_unknown"),
]


def _sig(text="", exit_code=1, elapsed_seconds=999, commits_since_start=1,
         worktree_dirty=False, artifact_present=True, hosting_location="cloud"):
    return classify(text, exit_code, elapsed_seconds=elapsed_seconds,
                    commits_since_start=commits_since_start, worktree_dirty=worktree_dirty,
                    artifact_present=artifact_present, hosting_location=hosting_location)


def test_class_for_signature_covers_every_classify_return():
    tree = ast.parse(_ERROR_SIGNATURE_PY.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "classify")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    # A new classify() branch fails here until it gets a case (and a mapping) below.
    assert len(returns) == len(_CLASSIFY_CASES)
    for kwargs, expected in _CLASSIFY_CASES:
        sig = _sig(**kwargs)
        key = ":".join(sig.split(":")[:2])
        assert key in ft.SIGNATURE_TO_CLASS, f"unmapped classify() return: {sig}"
        assert ft.class_for_signature(sig) == expected, sig
    assert ft.class_for_signature(SESSION_WINDOW_PAUSE_SIGNATURE) == "session_window_pause"
    assert len(ft.SIGNATURE_TO_CLASS) == 10
    assert set(ft.SIGNATURE_TO_CLASS.values()) <= set(ft.FAILURE_TAXONOMY)
    # Only genuinely unmapped input takes the default.
    assert ft.class_for_signature("substantive:brand_new_kind:1") == "substantive_unknown"
    assert ft.class_for_signature("garbage") == "substantive_unknown"


@pytest.mark.parametrize("sig,expected", [
    ("substantive:build_failure:1", "build_failure"),
    ("substantive:oos_files:2", "scope_spillover"),
    ("substantive:test_failure:1", "test_failure"),
    ("substantive:unknown:137", "substantive_unknown"),
    ("substantive:endpoint_unreachable:1", "endpoint_unreachable"),
    ("environmental:delivery_failure", "delivery_failure"),
])
def test_class_for_signature_strips_exit_code_suffix(sig, expected):
    assert ft.class_for_signature(sig) == expected


def test_endpoint_unreachable_signature_is_not_retryable():
    # The first pass let this signature fall through to a retryable default (operator review).
    assert ft.FAILURE_TAXONOMY[ft.class_for_signature("substantive:endpoint_unreachable:1")].retryable is False


def test_issue_names_without_a_detector_are_never_produced():
    for name in ("github_rate_limit", "model_timeout", "tool_timeout"):
        assert ft.FAILURE_TAXONOMY[name].detector == "none_yet"
        assert name not in ft.SIGNATURE_TO_CLASS.values()
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: the new tests fail with `AttributeError: module 'factory_core.failure_taxonomy' has no attribute 'SIGNATURE_TO_CLASS'` (or `KeyError: 'github_rate_limit'`). Task 1's 2 tests still pass.

- [ ] **Step 3: Add the §2b rows, the mapping and the function**

In `scripts/factory_core/failure_taxonomy.py`, insert after `_LOCAL_ROWS` (before `def _merge`):

```python
def _native(*, retryable=True, max_attempts=None, requires_human=False,
            state_transition="continue", multiplier=1.0, detector="error_signature",
            enforced_today=_CEILING_AND_STUCK, enforced=False):
    return _row(retryable=retryable, max_attempts=max_attempts, requires_human=requires_human,
                state_transition=state_transition, multiplier=multiplier, detector=detector,
                enforced_today=enforced_today, enforced=enforced)


# Spec §2b: classes error_signature.classify() produces (its strings stay byte-identical).
# build_failure / verdict_missing / test_failure keep the classifier's own names (not the
# issue's adapter_contract_error / output_truncated / test_timeout). model_timeout,
# tool_timeout and github_rate_limit are the issue's names and have no detector yet.
_NATIVE_ROWS = {
    "delivery_failure": _native(multiplier=0.5, enforced_today=(
        "scheduler.sh:579-596 retry_or_skip_delivery_failure: not counted against the retry "
        "key; a shadow <key>:delivery counter trips at the phase ceiling")),
    "model_rate_limit": _native(multiplier=0.5, enforced_today=f"{_CEILING} only"),
    "preview_infra": _native(multiplier=0.5, enforced_today=f"{_CEILING} only"),
    "session_window_pause": _native(multiplier=None, enforced=True, enforced_today=(
        "scheduler.sh:551-560 rollback_paused_retry un-counts the paused attempt, and the "
        "session-window-paused sentinel holds dispatch until its resume time")),
    "verdict_missing": _native(),
    "scope_spillover": _native(retryable=False, max_attempts=0, enforced_today=(
        f"{_CEILING_AND_STUCK}: the scheduler has no case for this signature and redispatches "
        "it like any other substantive failure; scripts/oos_excise.sh excises files inside a "
        "run, it does not stop a retry")),
    "build_failure": _native(),
    "test_failure": _native(),
    "substantive_unknown": _native(),
    "model_timeout": _native(retryable=False, max_attempts=0, requires_human=True,
                             state_transition="abstain_escalate", detector="none_yet",
                             enforced_today="none"),
    "tool_timeout": _native(retryable=False, max_attempts=0, requires_human=True,
                            state_transition="abstain_escalate", detector="none_yet",
                            enforced_today="none"),
    "github_rate_limit": _native(multiplier=0.5, detector="none_yet", enforced_today=(
        f"{_CEILING} only, as for model_rate_limit: both sign environmental:rate_limit today")),
}

DEFAULT_CLASS = "substantive_unknown"
# Keyed on a signature's first two colon-separated fields: classify() appends :<exit_code>
# to oos_files, build_failure, test_failure, unknown and endpoint_unreachable.
SIGNATURE_TO_CLASS = MappingProxyType({
    "environmental:delivery_failure": "delivery_failure",
    "environmental:rate_limit": "model_rate_limit",
    "environmental:preview_infra": "preview_infra",
    "environmental:session_window_pause": "session_window_pause",
    "substantive:verdict_missing": "verdict_missing",
    "substantive:oos_files": "scope_spillover",
    "substantive:build_failure": "build_failure",
    "substantive:test_failure": "test_failure",
    "substantive:unknown": DEFAULT_CLASS,
    "substantive:endpoint_unreachable": ENDPOINT_UNREACHABLE,
})


def class_for_signature(sig: str) -> str:
    """The FAILURE_TAXONOMY key for an error_signature string; DEFAULT_CLASS if unmapped."""
    if not isinstance(sig, str):
        return DEFAULT_CLASS
    parts = sig.split(":")
    key = ":".join(parts[:2]) if len(parts) >= 2 else sig
    return SIGNATURE_TO_CLASS.get(key, DEFAULT_CLASS)
```

Then change the last line of the module to:

```python
FAILURE_TAXONOMY = _merge(_LOCAL_ROWS, _NATIVE_ROWS)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: `11 passed` (Task 1's 2, plus 1 + 6 parametrized + 1 + 1), 0 failed.

- [ ] **Step 5: Confirm error_signature's own tests are untouched**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -q`
Expected: all pass. `git diff --stat -- scripts/factory_core/error_signature.py` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/failure_taxonomy.py tests/test_failure_taxonomy.py
git commit -m "feat(#238): factory-native failure classes and class_for_signature()"
```

---

## Task 3: Tagging-only classes, preview-helper classes, breaker outcomes and the policy-honesty test

**Files:**
- Modify: `scripts/factory_core/failure_taxonomy.py`
- Modify: `tests/test_failure_taxonomy.py`

- [ ] **Step 1: Write the failing tests (spec tests 3b and 3c, and the §2d constants)**

Append to `tests/test_failure_taxonomy.py`:

```python
_FILE_TOKEN_RE = re.compile(r"(?:[\w.-]+/)*[\w-]+\.(?:py|sh|yaml|yml|md)\b")


def test_enforced_rows_declare_no_unenforced_policy():
    enforced = {k for k, v in ft.FAILURE_TAXONOMY.items() if v.enforced}
    assert enforced == {"session_window_pause"}
    for key, row in ft.FAILURE_TAXONOMY.items():
        if row.enforced:
            assert row.max_attempts is None, key
            assert row.token_budget_multiplier is None, key
        assert row.cross_endpoint_retry is False, key
        assert row.fallback_model_allowed is False, key
        assert row.fallback_phase_allowed is False, key
        assert row.token_budget_multiplier != 0.0, key
        assert row.state_transition in ft.STATE_TRANSITIONS, key
        assert row.detector in ft.DETECTORS, key
        assert row.enforced_today, key
        for token in _FILE_TOKEN_RE.findall(row.enforced_today):
            assert (REPO / token).is_file(), f"{key}: enforced_today names missing file {token}"


def test_preview_helper_classes_are_declared():
    from factory_core import preview_helper
    assert ft.PREVIEW_ENTRY_FAILURE_CLASSES == preview_helper.ENTRY_FAILURE_CLASSES
    assert ft.PREVIEW_SCHEDULER_FAILURE_CLASSES == preview_helper.SCHEDULER_FAILURE_CLASSES
    for cls in preview_helper.ENTRY_FAILURE_CLASSES + preview_helper.SCHEDULER_FAILURE_CLASSES:
        assert cls in ft.FAILURE_TAXONOMY, cls
        assert ft.FAILURE_TAXONOMY[cls].state_transition == "abstain_escalate"


def test_tagging_only_gate_classes_are_declared_and_not_enforced():
    for name in ("ci_red", "main_red", "merge_conflict", "context_overflow", "budget_exceeded",
                 "conformance_block", "security_block", "missing_secret"):
        row = ft.FAILURE_TAXONOMY[name]
        assert row.enforced is False and row.detector == "none_yet", name
        assert name not in ft.SIGNATURE_TO_CLASS.values(), name


def test_breaker_outcomes_are_not_taxonomy_keys():
    assert ft.BREAKER_OUTCOME_REPEAT_LOOP == "repeat_tool_call_loop"
    assert ft.BREAKER_OUTCOME_HUMAN_REQUIRED == "human_required"
    for outcome in (ft.BREAKER_OUTCOME_REPEAT_LOOP, ft.BREAKER_OUTCOME_HUMAN_REQUIRED):
        assert outcome not in ft.FAILURE_TAXONOMY
        assert outcome not in ft.SIGNATURE_TO_CLASS.values()
        assert outcome not in ft.FAILURE_CLASSES
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: the 4 new tests fail (`AttributeError ... 'PREVIEW_ENTRY_FAILURE_CLASSES'`, `KeyError: 'ci_red'`, `... 'BREAKER_OUTCOME_REPEAT_LOOP'`). `test_enforced_rows_declare_no_unenforced_policy` may already pass. That is fine, because it pins rows that already exist.

- [ ] **Step 3: Add §2c, §2d and §4**

In `scripts/factory_core/failure_taxonomy.py`, insert after `_NATIVE_ROWS` (before `DEFAULT_CLASS`):

```python
def _tag(*, requires_human, state_transition, enforced_today):
    # Neutral placeholders, not statements about the gates (spec §2c): every row is
    # enforced=False because the gate owns its own halt and was not matched to this record.
    return _row(retryable=False, max_attempts=None, requires_human=requires_human,
                state_transition=state_transition, multiplier=None, detector="none_yet",
                enforced_today=enforced_today)


# Spec §2c: tagging-only names for failures a gate or sentinel already handles.
_TAG_ROWS = {
    "ci_red": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "scheduler.sh:1114-1124 stage_ci_gate moves the ticket to Blocked; it is then retried "
        f"up to {_CEILING}")),
    "main_red": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "the main-is-red sentinel pauses implementation dispatch; main_red_recheck_check "
        "clears it; main_red_fixer_check is off by default (scheduler.sh:188)")),
    "merge_conflict": _tag(requires_human=True, state_transition="trip_to_blocked",
                           enforced_today=(
        "scripts/factory_core/deconflict.py tiers 1-3 own their escalation; the declared halt "
        "was not verified against them")),
    "context_overflow": _tag(requires_human=False, state_transition="continue",
                             enforced_today="scripts/budget_enforce.py (a warning, no halt)"),
    "budget_exceeded": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "scripts/budget_gate.sh in enforce mode writes context caps to token-opt-caps.env; it "
        "does not stop the run")),
    "conformance_block": _tag(requires_human=False, state_transition="continue",
                              enforced_today=(
        "the conformance/docs-conformance reconcile loop owns STATUS: BLOCKED; not verified "
        "against the code")),
    "security_block": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "the code-review gate owns a SEVERITY: critical finding; not verified against the code")),
    "missing_secret": _tag(requires_human=True, state_transition="trip_to_blocked",
                           enforced_today=(
        "preflight fails the run at startup; the run then takes the ordinary failure path "
        "(typically environmental:delivery_failure), not a direct trip")),
}

# Spec §4: preview_helper.py's two closed enums (#642), verbatim and in order. It is not
# imported here; tests/test_failure_taxonomy.py pins these tuples against it.
PREVIEW_ENTRY_FAILURE_CLASSES = ("preflight_refused", "missing_input", "input_too_large",
                                 "local_timeout_total", "model_call_failed", "schema_invalid")
PREVIEW_SCHEDULER_FAILURE_CLASSES = (
    "registry_stage_failed", "endpoint_not_resolved", "proxy_start_failed", "dispatch_failed",
    "wall_clock_exceeded", "kill_switch", "helper_result_missing", "helper_result_invalid",
    "ledger_row_missing", "ledger_unavailable", "ledger_identity_mismatch",
    "unclassified_failure",
)
# Restates #642's design: every helper failure abstains, is never retried for the same head
# SHA and never retried on cloud. #587's lane enforces it and must update this record.
_PREVIEW_HELPER_ROW = _row(
    retryable=False, max_attempts=0, requires_human=False, state_transition="abstain_escalate",
    multiplier=None, detector="none_yet",
    enforced_today="none: preview_helper is not dispatched on main (#642); #587's lane will "
                   "own its own stop")
_PREVIEW_ROWS = {c: _PREVIEW_HELPER_ROW
                 for c in PREVIEW_ENTRY_FAILURE_CLASSES + PREVIEW_SCHEDULER_FAILURE_CLASSES}

# Spec §2d: what the breaker decides across attempts, never what failed on one attempt.
# Not FAILURE_TAXONOMY keys and never a SIGNATURE_TO_CLASS value; for tagging a run's
# terminal outcome only. "Repeated identical failures" is detected at phase-attempt
# granularity (breaker.py:330, #373), not per tool call.
BREAKER_OUTCOME_REPEAT_LOOP = "repeat_tool_call_loop"
BREAKER_OUTCOME_HUMAN_REQUIRED = "human_required"  # trip_to_blocked() adds needs-discussion
```

Change the last line of the module to:

```python
FAILURE_TAXONOMY = _merge(_LOCAL_ROWS, _NATIVE_ROWS, _TAG_ROWS, _PREVIEW_ROWS)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: `15 passed`, 0 failed.

- [ ] **Step 5: Confirm preview_helper is untouched and still passes**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q -k preview_helper`
Expected: all pass. `git diff --stat -- scripts/factory_core/preview_helper.py` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/failure_taxonomy.py tests/test_failure_taxonomy.py
git commit -m "feat(#238): tagging-only, preview-helper and breaker-outcome entries in the taxonomy"
```

---

## Task 4: `model_adapter.py` re-exports from the taxonomy, and nothing reads the policy

**Files:**
- Modify: `scripts/factory_core/providers/model_adapter.py:17-48`
- Modify: `tests/test_failure_taxonomy.py`

- [ ] **Step 1: Write the failing test (the identity half of spec test 3)**

Append to `tests/test_failure_taxonomy.py`:

```python
def test_model_adapter_re_exports_the_taxonomy_constants():
    from factory_core.providers import model_adapter as ma
    assert ma.FAILURE_CLASSES is ft.FAILURE_CLASSES
    assert ma.FAILURE_CLASSES == _LOCAL_CLASSES_IN_ORDER
    for name in ("ENDPOINT_UNREACHABLE", "TIMEOUT_CONNECT", "TIMEOUT_TIME_TO_FIRST_TOKEN",
                 "TIMEOUT_IDLE", "TIMEOUT_TOTAL", "CANCELLATION", "PROTOCOL_INCOMPATIBILITY",
                 "CAPABILITY_EVIDENCE_INVALID", "DESTINATION_POLICY_DENIAL"):
        assert getattr(ma, name) is getattr(ft, name), name
```

- [ ] **Step 2: Run it and see it fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py::test_model_adapter_re_exports_the_taxonomy_constants -v`
Expected: FAIL on `assert ma.FAILURE_CLASSES is ft.FAILURE_CLASSES` (equal tuples, different objects).

- [ ] **Step 3: Replace the constants with a re-export**

In `scripts/factory_core/providers/model_adapter.py`, change the import line

```python
from factory_core.providers import model_endpoint
```

to

```python
from factory_core.providers import model_endpoint
# Contract §4.6's failure classes: owned (with their declared policy) by
# factory_core/failure_taxonomy.py (#238); re-exported here, values and order unchanged.
from factory_core.failure_taxonomy import (  # noqa: F401
    CANCELLATION, CAPABILITY_EVIDENCE_INVALID, DESTINATION_POLICY_DENIAL, ENDPOINT_UNREACHABLE,
    FAILURE_CLASSES, PROTOCOL_INCOMPATIBILITY, TIMEOUT_CONNECT, TIMEOUT_IDLE,
    TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_TOTAL,
)
```

and delete this whole block (currently lines 33-48), leaving `_MESSAGE_STOP_RE = ...` directly after `NOT_READY_STATUSES = (502, 503, 504)` and its blank line:

```python
# Contract §4.6's interim failure classes; #238's names supersede these when it ships.
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
TIMEOUT_CONNECT = "timeout_connect"
TIMEOUT_TIME_TO_FIRST_TOKEN = "timeout_time_to_first_token"
TIMEOUT_IDLE = "timeout_idle"
TIMEOUT_TOTAL = "timeout_total"
CANCELLATION = "cancellation"
PROTOCOL_INCOMPATIBILITY = "protocol_incompatibility"
# Startup-only: evidence problems fail load_verified_entry(), never a request (spec Req. 14).
CAPABILITY_EVIDENCE_INVALID = "capability_evidence_invalid"
DESTINATION_POLICY_DENIAL = "destination_policy_denial"
FAILURE_CLASSES = (
    ENDPOINT_UNREACHABLE, TIMEOUT_CONNECT, TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_IDLE,
    TIMEOUT_TOTAL, CANCELLATION, PROTOCOL_INCOMPATIBILITY, CAPABILITY_EVIDENCE_INVALID,
    DESTINATION_POLICY_DENIAL,
)
```

(The "Startup-only" comment moved into `failure_taxonomy.py` in Task 1.) Do not touch any
other line of `model_adapter.py`.

- [ ] **Step 4: Run it and see it pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -v`
Expected: all pass.

- [ ] **Step 5: Add spec test 3d (nothing reads the policy fields)**

Append to `tests/test_failure_taxonomy.py`:

```python
def _scanned_files():
    files = [p for p in (REPO / "scripts").rglob("*")
             if p.is_file() and p.suffix in (".py", ".sh")]
    return files + [REPO / "scheduler.sh", REPO / "entrypoint.sh"]


def test_nothing_reads_the_policy_fields():
    # A later enforcement ticket has to edit this test; that is the point (spec test 3d).
    texts = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace")
             for p in _scanned_files()}
    mention_module = {f for f, t in texts.items() if "failure_taxonomy" in t}
    assert mention_module <= {
        "scripts/factory_core/failure_taxonomy.py",
        "scripts/factory_core/providers/model_adapter.py",
        "scripts/factory_core/run_record.py",
    }, mention_module
    mention_table = {f for f, t in texts.items() if "FAILURE_TAXONOMY" in t}
    assert mention_table <= {"scripts/factory_core/failure_taxonomy.py"}, mention_table
    for f in ("scripts/factory_core/breaker.py", "scripts/factory_core/error_signature.py",
              "scheduler.sh"):
        assert "failure_taxonomy" not in texts[f] and "FAILURE_TAXONOMY" not in texts[f], f
```

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py::test_nothing_reads_the_policy_fields -v`
Expected: PASS. It guards the end state, so it passes now and must keep passing after Task 7.

- [ ] **Step 6: Run every consumer of the nine constants**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py tests/test_capability_suite.py tests/test_factory_core_error_signature.py tests/test_run_record.py -q`
Expected: all pass, with no edit to any of these files. `error_signature.py`'s deferred
`from .providers.model_adapter import ENDPOINT_UNREACHABLE` still resolves through the re-export.

- [ ] **Step 7: Commit**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_failure_taxonomy.py
git commit -m "refactor(#238): model_adapter re-exports its failure classes from failure_taxonomy"
```

---

## Task 5: `harness_economics` — `POLICY_VERSION` 1.2 and the ledger part of `by_class`

**Files:**
- Modify: `scripts/factory_core/run_record.py:30`, `:847-851`
- Modify: `tests/test_run_record.py`

- [ ] **Step 1: Update the existing assertions this change breaks (they become the failing tests)**

In `tests/test_run_record.py` (line numbers as of `b763763`):

- `:905` `assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only"}` → `assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only", "by_class": {}}`
- `:990` `assert he["failure_spend"] == {"tokens": 150, "basis": "whole_run"}` → `assert he["failure_spend"] == {"tokens": 150, "basis": "whole_run", "by_class": {}}`
- `:1510-1511` rename `def test_policy_version_is_1_1():` → `def test_policy_version_is_1_2():` and `assert rr.POLICY_VERSION == "1.1"` → `assert rr.POLICY_VERSION == "1.2"`
- `:1581` `assert he["policy_version"] == "1.1"` → `assert he["policy_version"] == "1.2"`
- `:1584`, `:1598`, `:1606` `{"tokens": 0, "basis": "retry_only"}` → `{"tokens": 0, "basis": "retry_only", "by_class": {}}`
- `:1591` `{"tokens": 500, "basis": "whole_run"}` → `{"tokens": 500, "basis": "whole_run", "by_class": {}}`
- `:2405` `assert he["policy_version"] == "1.1"` → `assert he["policy_version"] == "1.2"`

`:1912`, `:1927` and `:2417` assert only `["basis"]` and need no edit; the spec listed them
as possibly affected. Confirm with
`grep -n 'failure_spend\|"1.1"' tests/test_run_record.py` that no other exact
`failure_spend` dict or `"1.1"` remains.

Then append the new test (after `test_harness_economics_without_artifact_check_unchanged`):

```python
def test_failure_spend_by_class_counts_ledger_failure_rows(tmp_path):
    rows = [
        {"run_id": "run-447", "status": 504, "failure_class": "timeout_idle"},
        {"run_id": "run-447", "status": 504, "failure_class": "timeout_idle"},
        {"run_id": "run-447", "status": 200, "failure_class": None},
        {"run_id": "run-447", "status": 403, "failure_class": "destination_policy_denial"},
    ]
    he = _economics(tmp_path, ledger=(rows, True))
    assert he["failure_spend"]["by_class"] == {
        "timeout_idle": {"tokens": None, "basis": "ledger_rows", "request_count": 2},
        "destination_policy_denial": {"tokens": None, "basis": "ledger_rows", "request_count": 1},
    }
    # No ledger: an empty dict, never a missing key.
    assert _economics(tmp_path)["failure_spend"]["by_class"] == {}
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k "harness_economics or policy_version or by_class"`
Expected: failures on the edited assertions (missing `by_class`, `"1.1" != "1.2"`) and on the new test (`KeyError: 'by_class'`).

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`:

`:30` `POLICY_VERSION = "1.1"` → `POLICY_VERSION = "1.2"`

In `_compute_harness_economics`, directly after

```python
    if outcome["state"] in ("failed", "blocked"):
        failure_spend = {"tokens": tokens_per_task, "basis": "whole_run"}
    else:
        failure_spend = {"tokens": retry_spend["tokens"] or 0, "basis": "retry_only"}
```

insert:

```python
    # #238 (policy 1.2): failure spend per failure class. Ledger rows count requests per
    # adapter class here; `run-record annotate-failure` adds the run's own phase-level class
    # (from its error signature) to the assembled record afterwards. Observed spend only:
    # a class's declared token_budget_multiplier lives in failure_taxonomy, never here.
    by_class: dict = {}
    if ledger_available:
        for row in rows:
            rc = row.get("failure_class")
            if not rc:
                continue
            entry = by_class.setdefault(rc, {"tokens": None, "basis": "ledger_rows"})
            entry["request_count"] = entry.get("request_count", 0) + 1
    failure_spend["by_class"] = by_class
```

Leave `_compute_outcome`'s docstring (`:675`, "policy_version 1.1") as it is. It names
the version that introduced the outcome policy, and 1.2 does not change that policy.

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#238): failure_spend.by_class from ledger rows; POLICY_VERSION 1.2"
```

---

## Task 6: `run-record annotate-failure`

**Files:**
- Modify: `scripts/factory_core/run_record.py` (imports; new functions after `cmd_assemble`; `main()`)
- Modify: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests (spec tests 4, 4a, 4b, 5)**

Append to `tests/test_run_record.py`:

```python
# ---------------------------------------------------------------------------
# annotate-failure (#238)
# ---------------------------------------------------------------------------

def _assemble_failed(tmp_path, monkeypatch, ledger=None):
    """A failed run abc123 with 150 tokens: failure_spend {tokens: 150, basis: whole_run}."""
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", ledger or tmp_path / "no-ledger.jsonl")
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "abc123",
        "nodes": [{"nodeId": "implement", "inputTokens": 100, "outputTokens": 50,
                   "costUsd": 0.01, "durationMs": 1000, "modelUsage": {}}],
        "totals": {"costUsd": 0.01, "inputTokens": 100, "outputTokens": 50},
    }))
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(cost_json)
    args.status = "failed"
    rr.cmd_assemble(args)
    return out


def _annotate(monkeypatch, out, sig, run_id="abc123"):
    monkeypatch.setattr(sys, "argv", ["run_record", "annotate-failure", "--run-id", run_id,
                                      "--out-file", str(out), "--failure-signature", sig])
    rr.main()


def _by_class(path):
    return json.loads(Path(path).read_text())["harness_economics"]["failure_spend"]["by_class"]


def _jsonl_rows(tmp_path):
    p = tmp_path / "runs.jsonl"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_annotate_failure_sets_by_class_from_signature(tmp_path, monkeypatch):
    import stat as _stat
    out = _assemble_failed(tmp_path, monkeypatch)
    durable = rr._durable_run_record_path("abc123")
    before = {p: json.loads(p.read_text()) for p in (out, durable)}
    modes = {p: _stat.S_IMODE(p.stat().st_mode) for p in (out, durable)}
    rows_before = _jsonl_rows(tmp_path)

    _annotate(monkeypatch, out, "substantive:build_failure:1")

    for p in (out, durable):
        after = json.loads(p.read_text())
        assert after["harness_economics"]["failure_spend"]["by_class"] == {
            "build_failure": {"tokens": 150, "basis": "whole_run"}}
        after["harness_economics"]["failure_spend"]["by_class"] = {}
        assert after == before[p]  # no other key changed
        assert _stat.S_IMODE(p.stat().st_mode) == modes[p]
    assert _jsonl_rows(tmp_path) == rows_before  # nothing appended to runs.jsonl


def test_annotate_failure_leaves_a_bad_record_alone(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    _annotate(monkeypatch, missing, "substantive:unknown:1")
    assert not missing.exists()
    for name, body in (("garbage.json", "not json{"),
                       ("no-he.json", json.dumps({"run_id": "abc123"})),
                       ("list.json", json.dumps([1, 2]))):
        p = tmp_path / name
        p.write_text(body)
        mtime = p.stat().st_mtime_ns
        _annotate(monkeypatch, p, "substantive:unknown:1")
        assert p.read_text() == body and p.stat().st_mtime_ns == mtime, name
    # Through the real CLI: exit status 0 on a missing record.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "scripts"),
           "SCHEDULER_STATE_DIR": str(tmp_path / "cli-state")}
    import subprocess
    cli = Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"
    res = subprocess.run([sys.executable, str(cli), "run-record", "annotate-failure",
                          "--run-id", "abc123", "--out-file", str(missing),
                          "--failure-signature", "substantive:unknown:1"],
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_annotate_failure_never_copies_one_record_onto_another(tmp_path, monkeypatch):
    out = _assemble_failed(tmp_path, monkeypatch)
    durable = rr._durable_run_record_path("abc123")
    other_rec = json.loads(out.read_text())
    other_rec["run_id"] = "other-run"
    other = tmp_path / "other-run.json"
    other.write_text(json.dumps(other_rec, indent=2))
    other_bytes = other.read_bytes()

    # Out-file is a symlink to another run's record: not followed; the durable copy is
    # annotated from its own content.
    out.unlink()
    out.symlink_to(other)
    _annotate(monkeypatch, out, "substantive:build_failure:1")
    assert out.is_symlink() and os.readlink(out) == str(other)
    assert other.read_bytes() == other_bytes
    durable_rec = json.loads(durable.read_text())
    assert durable_rec["run_id"] == "abc123"
    assert _by_class(durable) == {"build_failure": {"tokens": 150, "basis": "whole_run"}}

    # A regular record whose run_id is not --run-id is not rewritten.
    stranger = tmp_path / "stranger.json"
    stranger.write_text(json.dumps(other_rec))
    stranger_bytes = stranger.read_bytes()
    _annotate(monkeypatch, stranger, "substantive:test_failure:1")
    assert stranger.read_bytes() == stranger_bytes

    # A durable path that is a symlink is left alone.
    target = tmp_path / "durable-target.json"
    target.write_text(durable.read_text())
    target_bytes = target.read_bytes()
    durable.unlink()
    durable.symlink_to(target)
    _annotate(monkeypatch, stranger, "substantive:test_failure:1")
    assert durable.is_symlink() and target.read_bytes() == target_bytes


@pytest.mark.skipif(_is_root() or sys.platform == "win32",
                    reason="chmod does not restrict root; POSIX permissions only")
def test_annotate_failure_read_only_durable_dir_still_annotates_out_file(tmp_path, monkeypatch, capsys):
    out = _assemble_failed(tmp_path, monkeypatch)
    durable = rr._durable_run_record_path("abc123")
    durable.parent.chmod(0o555)
    try:
        _annotate(monkeypatch, out, "substantive:build_failure:1")
    finally:
        durable.parent.chmod(0o755)
    assert _by_class(out) == {"build_failure": {"tokens": 150, "basis": "whole_run"}}
    assert _by_class(durable) == {}
    assert not [p for p in durable.parent.iterdir() if p.name.endswith(".tmp")]
    assert not [p for p in out.parent.iterdir() if p.name.endswith(".tmp")]
    assert "annotate-failure could not rewrite" in capsys.readouterr().err


def test_failure_spend_by_class_adds_ledger_request_counts(tmp_path, monkeypatch):
    ledger = tmp_path / "request-ledger.jsonl"
    row = {"run_id": "abc123", "status": 504, "failure_class": "timeout_idle",
           "hosting_location": "local", "endpoint_id": "local-test",
           "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0}
    ledger.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    out = _assemble_failed(tmp_path, monkeypatch, ledger=ledger)
    _annotate(monkeypatch, out, "substantive:unknown:1")
    assert _by_class(out) == {
        "substantive_unknown": {"tokens": 150, "basis": "whole_run"},
        "timeout_idle": {"tokens": None, "basis": "ledger_rows", "request_count": 2},
    }


def test_annotate_failure_keeps_the_ledger_request_count_of_the_same_class(tmp_path, monkeypatch):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps({"run_id": "abc123", "status": 504,
                                  "failure_class": "endpoint_unreachable",
                                  "hosting_location": "local", "endpoint_id": "local-test"}) + "\n")
    out = _assemble_failed(tmp_path, monkeypatch, ledger=ledger)
    _annotate(monkeypatch, out, "substantive:endpoint_unreachable:1")
    assert _by_class(out) == {"endpoint_unreachable": {
        "tokens": 150, "basis": "whole_run", "request_count": 1}}
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k annotate_failure`
Expected: failures with `SystemExit: 2` (argparse: `invalid choice: 'annotate-failure'`). Note that
`test_annotate_failure_leaves_a_bad_record_alone` also fails this way.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`:

Imports. In the stdlib block, add `import stat` directly after `import re` and
`import tempfile` directly after `import sys`. Neither is imported today. Then, directly
after the line

```python
from . import verdict as _verdict
```

add

```python
from .failure_taxonomy import class_for_signature
```

(#645 put `from . import session_usage` between `model_proxy` and `verdict`; leave it.)

Add these two functions directly after `cmd_assemble` (before `def _build_issue_economics`):

```python
def _annotate_failure_class(path: "pathlib.Path | None", run_id: str, failure_class: str) -> None:
    """Set failure_spend.by_class[failure_class] from this record's own failure_spend (#238).

    Skips, without writing, anything that is not a regular file (a symlink is never
    followed), does not parse to a JSON object for run_id, or has no failure_spend dict.
    Rewrites through a temp file in the same directory and os.replace, keeping the file's
    mode; an OSError leaves the file as it was and removes the temp file.
    """
    if path is None:
        return
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(record, dict) or record.get("run_id") != run_id:
        return
    he = record.get("harness_economics")
    fs = he.get("failure_spend") if isinstance(he, dict) else None
    if not isinstance(fs, dict):
        return
    by_class = fs.get("by_class")
    if not isinstance(by_class, dict):
        by_class = fs["by_class"] = {}
    entry = by_class.get(failure_class)
    if not isinstance(entry, dict):
        entry = by_class[failure_class] = {}
    # tokens/basis are the run's whole-run spend; a request_count the ledger part already
    # counted for the same class (ledger requests, not whole-run) is kept alongside.
    entry.update({"tokens": fs.get("tokens"), "basis": fs.get("basis")})
    tmp = None
    try:
        tmp_fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2))
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        print(f"run-record: annotate-failure could not rewrite {path}: {exc}", file=sys.stderr)


def cmd_annotate_failure(args) -> None:
    """Record the failure signature's class on this run's assembled record (#238).

    Called by entrypoint.sh's on_failure after the signature write, which runs after
    `assemble`. The out-file and the durable copy are each annotated from their own content;
    one is never copied onto the other. Appends nothing to runs.jsonl, posts nothing to Seq
    (assembling again would do both) and always exits 0.
    """
    failure_class = class_for_signature(args.failure_signature)
    for path in (pathlib.Path(args.out_file), _durable_run_record_path(args.run_id)):
        try:
            _annotate_failure_class(path, args.run_id, failure_class)
        except Exception as exc:  # never fail the ERR trap that calls this
            print(f"run-record: annotate-failure skipped {path}: {exc}", file=sys.stderr)
```

In `main()`, after the `backfill-economics` subparser block add:

```python
    af = sub.add_parser("annotate-failure",
                        help="Add the failure signature's class to an assembled record (#238)")
    af.add_argument("--run-id", required=True)
    af.add_argument("--out-file", required=True)
    af.add_argument("--failure-signature", required=True)
```

and at the end of the dispatch chain add:

```python
    elif parsed.cmd == "annotate-failure":
        cmd_annotate_failure(parsed)
```

`cli.py` needs no edit: its `run-record` sub-command passes the remaining argv to
`run_record.main()` (`cli.py:127-130`, `:412-414`).

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
Expected: all pass. As root (factory container), the read-only test is reported `skipped`.

- [ ] **Step 5: Re-run the taxonomy guard (run_record.py now mentions failure_taxonomy)**

Run: `PYTHONPATH=scripts python -m pytest tests/test_failure_taxonomy.py -q`
Expected: all pass, including `test_nothing_reads_the_policy_fields` and
`test_failure_taxonomy_imports_nothing_from_providers`. The second one imports
`factory_core/__init__.py`, which imports `run_record`, which now imports `failure_taxonomy`.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#238): run-record annotate-failure records the signature's failure class"
```

---

## Task 7: Hermetic hybrid cases and the additive-bump regression (spec tests 8 and 9)

**Files:**
- Modify: `tests/test_run_record.py`

These tests cover the record side. The proxy side (no cloud call, no retry elsewhere) is
already covered by existing hermetic tests, which must pass unmodified:
`tests/test_factory_model_proxy.py::test_local_endpoint_unreachable_fails_closed_without_retrying_elsewhere`,
`::test_local_stall_after_the_first_chunk_hits_the_idle_bound`,
`::test_local_cancellation_writes_its_row_and_re_raises`, and the
`destination_policy_denial` cases in `tests/test_provider_model_adapter.py`.

- [ ] **Step 1: Write the tests**

Append to `tests/test_run_record.py`:

```python
# Spec test 8: four hybrid failures (outage, OOM/stalled stream, forbidden destination,
# cancelled/partial stream). Each: the ledger class is one of the nine verbatim values,
# the record reports a single local destination, the outcome is failed, and no class
# authorizes a different destination or a fallback.
_HYBRID_CASES = [
    ("local_endpoint_outage", "endpoint_unreachable", "substantive:endpoint_unreachable:1"),
    ("local_oom_stalled_stream", "timeout_idle", "substantive:unknown:1"),
    ("forbidden_destination", "destination_policy_denial", "substantive:unknown:1"),
    ("cancelled_partial_stream", "cancellation", "substantive:unknown:1"),
]


@pytest.mark.parametrize("failure_class,signature", [c[1:] for c in _HYBRID_CASES],
                         ids=[c[0] for c in _HYBRID_CASES])
def test_hybrid_failure_is_recorded_without_export_or_backend_switch(
        failure_class, signature, tmp_path, monkeypatch):
    from factory_core import failure_taxonomy as ft
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps({
        "run_id": "abc123", "status": 502, "failure_class": failure_class,
        "hosting_location": "local", "endpoint_id": "local-test", "role": "worker",
        "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0,
    }) + "\n" + json.dumps({  # another run's cloud request: must never be correlated here
        "run_id": "some-other-run", "status": 200, "failure_class": None,
        "hosting_location": "cloud", "endpoint_id": None,
        "gen_ai.usage.input_tokens": 5, "gen_ai.usage.output_tokens": 5,
    }) + "\n")
    out = _assemble_failed(tmp_path, monkeypatch, ledger=ledger)
    _annotate(monkeypatch, out, signature)

    rec = json.loads(out.read_text())
    result = rec["model_dispatch"]["result"]
    assert failure_class in ft.FAILURE_CLASSES                       # (a)
    assert result["failure_classes_seen"] == [failure_class]
    assert result["hosting_location"] == "local"                     # (b) one, local destination
    assert result["endpoint_id"] == "local-test"
    assert result["request_count"] == 1                              # the cloud row is not this run's
    assert rec["harness_economics"]["ledger_rows_correlated"] == 1
    assert result["outcome"] == "failed"                             # (c)
    cls = ft.FAILURE_TAXONOMY[failure_class]                          # (d)
    assert cls.cross_endpoint_retry is False
    assert cls.fallback_model_allowed is False and cls.fallback_phase_allowed is False
    by_class = rec["harness_economics"]["failure_spend"]["by_class"]
    assert by_class[failure_class]["request_count"] == 1
    assert by_class[ft.class_for_signature(signature)]["basis"] == "whole_run"


def test_policy_version_bump_is_additive(tmp_path, monkeypatch):
    # A pre-1.2 record (no by_class) still rolls up and can still be annotated.
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps({
        "run_id": "old-run", "issue_number": 238, "intent": "fix", "stage": "implement",
        "status": 200, "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 1,
    }) + "\n")
    record_path = tmp_path / "runs" / "old-run" / "run-record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({
        "run_id": "old-run", "totals": {"cost_usd": 0.5},
        "harness_economics": {"policy_version": "1.1", "outcome": {"state": "failed"},
                              "factory_cpm": 0.0,
                              "failure_spend": {"tokens": 2, "basis": "whole_run"}},
    }))
    result = rr._build_issue_economics(238, ledger_path=ledger, artifacts_root=tmp_path / "runs")
    entry = result["runs"]["old-run"]
    assert (entry["outcome_state"], entry["cost_usd"], entry["factory_cpm"]) == ("failed", 0.5, 0.0)
    _annotate(monkeypatch, record_path, "substantive:build_failure:1", run_id="old-run")
    assert _by_class(record_path) == {"build_failure": {"tokens": 2, "basis": "whole_run"}}
```

- [ ] **Step 2: Run them**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k "hybrid or additive"`
Expected: `5 passed`. These tests pin behaviour built in Tasks 1–6, so they pass on first
run. If one fails, the defect is in Tasks 5–6. Fix it there; do not loosen the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_record.py
git commit -m "test(#238): hermetic hybrid failure cases and the additive policy-version bump"
```

---

## Task 8: `entrypoint.sh` — capture the signature and annotate once, after the comment

**Files:**
- Create: `tests/test_entrypoint_failure_signature.sh`
- Modify: `entrypoint.sh` (`_write_error_signature`'s header comment, first line and tail; `on_failure`)
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the failing shell test (spec test 6)**

Create `tests/test_entrypoint_failure_signature.sh`:

```bash
#!/usr/bin/env bash
# Verifies on_failure()'s failure-class annotation (#238): the signature it already writes is
# handed to `run-record annotate-failure` exactly once, after the board move and the failure
# comment and before the cost report, under a 20 s bound. The order and argv of every existing
# call and the container's exit code are unchanged. Runs the real on_failure and
# _write_error_signature around the workflow loop's own construct, as
# tests/test_entrypoint_blast_hold.sh section E does.
# Run: bash tests/test_entrypoint_failure_signature.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export IDENTITY_SH="${IDENTITY_SH:-$REPO_ROOT/scripts/identity.sh}"
export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$REPO_ROOT/scripts/factory_core/providers/cli.py}"
export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() { return 0; }
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

unset FACTORY_BLAST_HOLD_FILE DF_ENDPOINT_HOSTING LEDGER_WRITE_WARNING
WORK=$(mktemp -d /tmp/ep-fs-XXXXXX)
mkdir -p "$WORK/rundir" "$WORK/state"
# Both exported at their first mention, before sourcing: tests/test_run_record_hermetic.sh
# lints exactly that form (df#300, #362).
export CURRENT_RUN_DIR="$WORK/rundir"
export SCHEDULER_STATE_DIR="$WORK/state"

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
trap 'rm -rf "$WORK"' EXIT
set +e; set +u; set +o pipefail
# #645: on_failure keeps the run's session transcripts from $HOME/.claude/projects. This
# harness is not about that and must never copy the machine's own transcripts.
_keep_run_transcripts() { :; }

PASSED=0; FAILED=0
check() {
  if eval "$2"; then echo "  PASS: $1"; PASSED=$((PASSED+1));
  else echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); fi
}

CLONE_DIR="$WORK/clone"   # every python3 call is stubbed; only the path string matters
CLI="$CLONE_DIR/dark-factory/scripts/factory_core/cli.py"
ARTIFACTS_DIR="$WORK/artifacts"; mkdir -p "$ARTIFACTS_DIR"
RUN_ID=test-run-fs-1
RUN_STARTED_AT=""         # elapsed and commits 0: a deterministic signature-write argv
ARGUMENTS="Fix issue #238"
SIG_VALUE="substantive:build_failure:3"

# `timeout` execs python3 from PATH, so the annotate call reaches this stub and never the
# python3 function below, which serves every direct call.
BIN="$WORK/bin"; mkdir -p "$BIN"
cat > "$BIN/python3" <<'STUB'
#!/usr/bin/env bash
echo "annotate $*" >> "$FS_LOG"
case "${FS_ANNOTATE_MODE:-ok}" in
  exit1) exit 1 ;;
  exit2) exit 2 ;;
  hang) sleep 60 ;;
esac
exit 0
STUB
chmod +x "$BIN/python3"

_fs_py() {
  case "$*" in
    *" error-signature-write "*)
      echo "sigwrite $*" >> "$FS_LOG"
      # fail: what the verb does on an unwritable state volume (exit 1, empty stdout)
      [ "${FS_SIG_MODE:-ok}" = ok ] || return 1
      echo "signature=$SIG_VALUE"
      return 0 ;;
    *" run-record assemble "*) echo "assemble $*" >> "$FS_LOG" ;;
    *" run-record record "*) echo "record $*" >> "$FS_LOG" ;;
    *" marker "*) echo "*stub footer*" ;;
    *) echo "py $*" >> "$FS_LOG" ;;
  esac
  return 0
}

_trapped_run() {  # _trapped_run <case>; exit status is what the container would exit with
  FS_LOG="$WORK/log.$1"; : > "$FS_LOG"; export FS_LOG
  (
    python3() { _fs_py "$@"; }
    git() {  # FS_GIT_LOG_FAILS=yes: `git log` fails as it does outside a repository
      [ "${FS_GIT_LOG_FAILS:-no}" = yes ] && [ "${1:-}" = log ] && return 128
      return 0
    }
    archon() { [ "${2:-}" = run ] && return 3; return 1; }
    run_post_mortem() { echo "post_mortem" >> "$FS_LOG"; }
    set_board_status() { echo "board $*" >> "$FS_LOG"; }
    post_or_update_comment() { echo "comment" >> "$FS_LOG"; }
    post_cost_report() { echo "cost" >> "$FS_LOG"; }
    _handle_session_window_pause() { [ "${FS_PAUSE:-no}" = yes ]; }
    _handle_blast_hold() { return 1; }
    export PATH="$BIN:$PATH" TMPDIR="$WORK/tmp"
    mkdir -p "$TMPDIR"
    set -eo pipefail
    trap on_failure ERR
    set +e
    TMP_OUT="$WORK/archon.out"
    archon workflow run archon-dark-factory "$ARGUMENTS" 2>&1 | tee "$TMP_OUT"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e
    if [ "$EXIT_CODE" -ne 0 ]; then
      if _handle_session_window_pause "$TMP_OUT"; then exit 0; fi
      echo "loop_branch" >> "$FS_LOG"
      exit 97
    fi
    exit 0
  ) > "$WORK/out.$1" 2>&1
}
_tags() { cut -d' ' -f1 "$WORK/log.$1" | tr '\n' ' ' | sed 's/ $//'; }
_line() { grep "^$2 " "$WORK/log.$1"; }
_count() { grep -c "^$2 " "$WORK/log.$1"; }

EXPECTED_ANNOTATE="annotate $CLI run-record annotate-failure --run-id $RUN_ID --out-file $ARTIFACTS_DIR/run-record.json --failure-signature $SIG_VALUE"
# Today's argv, written out (guards: these must not change).
EXPECTED_ASSEMBLE="assemble $CLI run-record assemble --run-id $RUN_ID --issue 238 --intent fix --started-at  --artifacts-dir $ARTIFACTS_DIR --archon-cost-json <TMP> --archon-cost-exit-code 1 --archon-cost-stderr-file <TMP> --status failed --out-file $ARTIFACTS_DIR/run-record.json --archon-log-file $WORK/archon.out --transcripts-dir $HOME/.claude/projects --clone-dir $CLONE_DIR --side-effect-level ${FACTORY_SIDE_EFFECT_LEVEL:-1} --side-effect-profile ${FACTORY_SIDE_EFFECT_PROFILE_VERSION:-unknown}"
EXPECTED_SIGWRITE="sigwrite $CLI error-signature-write --issue 238 --phase implement --exit-code 3 --text-file $WORK/archon.out --elapsed-seconds 0 --commits-since-start 0 --delivery-failure-max-seconds ${DELIVERY_FAILURE_MAX_SECONDS:-30} --state-dir $SCHEDULER_STATE_DIR"

echo "--- A: implement-path failure ---"
ISSUE_NUM=238; INTENT=fix
_trapped_run a; RC=$?
check "exit_code_is_the_workflows (3 in, 3 out)" '[ "$RC" = 3 ]'
check "annotate_runs_after_the_signature_write_with_its_value" \
  '[ "$(_count a annotate)" = 1 ] && [ "$(_line a annotate)" = "$EXPECTED_ANNOTATE" ]'
check "annotate_runs_after_the_comment_and_before_the_cost_report" \
  '[ "$(_tags a)" = "record assemble sigwrite post_mortem board comment annotate cost" ]'
check "assemble_argv_is_unchanged" \
  '[ "$(_line a assemble | sed "s#$WORK/tmp/tmp\.[A-Za-z0-9]*#<TMP>#g")" = "$EXPECTED_ASSEMBLE" ]'
check "signature_write_argv_and_position_are_unchanged" \
  '[ "$(_count a sigwrite)" = 1 ] && [ "$(_line a sigwrite)" = "$EXPECTED_SIGWRITE" ]'
check "loop_failure_branch_is_not_reached_after_the_trap" '! grep -q "^loop_branch" "$WORK/log.a"'
check "signature_line_is_still_printed" 'grep -qxF "signature=$SIG_VALUE" "$WORK/out.a"'

echo "--- B: annotate exits 1 or 2 (an older clone cli.py) -> exit code unchanged ---"
for mode in exit1 exit2; do
  FS_ANNOTATE_MODE=$mode _trapped_run "b-$mode"; RC=$?
  check "exit_code_is_the_workflows with annotate $mode" '[ "$RC" = 3 ]'
  check "board move and comment still made ($mode)" \
    '[ "$(_count "b-$mode" board)" = 1 ] && grep -qx comment "$WORK/log.b-$mode"'
done
unset FS_ANNOTATE_MODE

echo "--- C: the verb printed nothing (unwritable state volume) ---"
FS_SIG_MODE=fail _trapped_run c; RC=$?
check "no_signature_means_no_annotate" '[ "$(_count c annotate)" = 0 ]'
check "failure comment and board move still made" \
  '[ "$(_tags c)" = "record assemble sigwrite post_mortem board comment cost" ] && [ "$RC" = 3 ]'

echo "--- D: a hung annotate (20 s bound) ---"
START=$(date +%s)
FS_ANNOTATE_MODE=hang _trapped_run d; RC=$?
ELAPSED=$(( $(date +%s) - START ))
check "a_hung_annotate_does_not_stop_the_board_move_or_the_comment" \
  '[ "$(_tags d)" = "record assemble sigwrite post_mortem board comment annotate cost" ] && [ "$RC" = 3 ]'
check "bounded: done in under 40 s (took ${ELAPSED}s)" '[ "$ELAPSED" -lt 40 ]'

echo "--- E: refine/plan/deconflict branch ---"
INTENT=plan
_trapped_run e; RC=$?
check "plan path annotates after the comment" \
  '[ "$(_tags e)" = "record assemble sigwrite comment annotate cost" ] && [ "$RC" = 3 ]'
check "plan path passes the same signature" '[ "$(_line e annotate)" = "$EXPECTED_ANNOTATE" ]'
INTENT=fix

echo "--- F: session-window pause ---"
FS_PAUSE=yes _trapped_run f; RC=$?
check "pause_path_makes_no_annotate_call" \
  '[ "$(_count f annotate)" = 0 ] && [ "$(_count f sigwrite)" = 0 ] && [ "$RC" = 0 ]'

echo "--- G: an intent without an issue ---"
SAVED_ISSUE="$ISSUE_NUM"; unset ISSUE_NUM; INTENT=recheck
_trapped_run g; RC=$?
check "issue_less_intent_makes_no_annotate_call" \
  '[ "$(_tags g)" = "record assemble cost" ] && [ "$RC" = 3 ]'
ISSUE_NUM="$SAVED_ISSUE"; INTENT=fix

echo "--- I: git log fails (a failure before the clone exists: cwd is not a repository) ---"
# Today errexit + pipefail end the trap at _write_error_signature's `git log | wc -l`, in
# on_failure's own shell, before the board move and the comment (exit 128). Running the
# function in a command substitution would clear -e and change that; it must not change.
FS_GIT_LOG_FAILS=yes RUN_STARTED_AT=$(date -u -d '-5 minutes' +%Y-%m-%dT%H:%M:%SZ) _trapped_run i; RC=$?
check "failure_before_the_clone_behaves_as_today (record, assemble, exit 128)" \
  '[ "$(_tags i)" = "record assemble" ] && [ "$RC" = 128 ]'

echo "--- H: static — one annotate call, inside on_failure; call sites unchanged ---"
ANN=$(grep -n 'run-record annotate-failure' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
ONF=$(grep -n '^on_failure() {$' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
TRAP=$(grep -n '^trap on_failure ERR$' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
check "exactly one annotate call site" '[ "$(printf "%s\n" "$ANN" | grep -c .)" = 1 ]'
check "it is inside on_failure" '[ "$ONF" -lt "$ANN" ] && [ "$ANN" -lt "$TRAP" ]'
check "bounded with timeout -k 5 20" \
  'sed -n "${ANN}p" "$REPO_ROOT/entrypoint.sh" | grep -q "timeout -k 5 20 python3"'
# Matches the exact 6-space-indented line on purpose: a re-indent of on_failure fails this
# guard, and then only needs the expected line updated.
check "both on_failure signature writes are still plain calls in the trap's own shell" \
  '[ "$(grep -cxF "      _write_error_signature \"\$(_failure_phase_for_intent)\" \"\$EXIT_CODE\" \"\${TMP_OUT:-}\"" "$REPO_ROOT/entrypoint.sh")" = 2 ]'

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

Notes for the implementer:
- The subshell uses `set -eo pipefail` (not `-u`), like `test_entrypoint_blast_hold.sh`
  section E. The behaviour under test is the trap's errexit handling, and `-u` adds no
  coverage here.
- The ERR trap fires on the failed `archon … | tee` pipeline even under `set +e`, and
  `on_failure` re-enables errexit. So the subshell exits with archon's code when the handler
  returns, and the `loop_branch` line is never written (case A's guard).
- Case I is the architect-review guard (cycle 1). A plan draft that wrapped the call as
  `SIG=$(_write_error_signature …) || true` was measured against it: that wrapper made the
  trap carry on past a failing `git log` (log `record assemble sigwrite post_mortem board
  comment annotate cost`, exit 3 instead of `record assemble`, exit 128). That is why the
  signature comes back through a global and not through stdout.

- [ ] **Step 2: Run it and see the right checks fail**

Run: `bash tests/test_entrypoint_failure_signature.sh`
Expected on today's `entrypoint.sh`: `Results: 16 passed, 8 failed`. The guards pass:
`exit_code_is_the_workflows`, `assemble_argv_is_unchanged`,
`signature_write_argv_and_position_are_unchanged`,
`loop_failure_branch_is_not_reached_after_the_trap`, `signature_line_is_still_printed`,
`no_signature_means_no_annotate`, `pause_path_makes_no_annotate_call`,
`issue_less_intent_makes_no_annotate_call`, `failure_before_the_clone_behaves_as_today` and
the call-sites check. Every check that expects an `annotate` line, and the first three checks
of section H, FAIL. The script exits non-zero.

If a guard fails on today's code, the harness is wrong, not the entrypoint. Fix the harness
before going on. For example, if sourcing sets `FACTORY_SIDE_EFFECT_LEVEL` to a non-default,
the expected strings already expand it.

- [ ] **Step 3: `_write_error_signature` records the signature it wrote in a global**

The callers keep calling this function as a plain command in their own shell. Do **not**
wrap a call site in `$(…)`: a command substitution clears `-e`, so a failure that ends the
trap today (for example `git log` failing before the clone exists) would let it carry on and
move the board (architect review, cycle 1; case I pins it). Instead the function sets a
global, the pattern `on_failure` already uses for `LEDGER_WRITE_WARNING` ("helper sets a
global, the comment builder reads it back").

In `entrypoint.sh`, replace the comment above `_write_error_signature` (currently `:520-527`):

```bash
# Classifies the current failure and drops the signature file the scheduler reads back on
# its next poll (mirrors _handle_session_window_pause's sentinel-file pattern). Called from
# two places: (1) the main archon-workflow loop's real-failure branch, with the captured
# transcript — the functionally load-bearing call, since that is where a real task failure
# (test_failure, build_failure, oos_files) is actually observed; (2) both branches of
# on_failure() (the ERR trap), with no transcript, covering early/setup-phase crashes before
# the main loop ever runs — these classify as environmental:delivery_failure by construction
# (fast, zero commits, no artifact), which is the correct, conservative outcome.
```

with:

```bash
# Classifies the current failure and drops the signature file the scheduler reads back on
# its next poll (mirrors _handle_session_window_pause's sentinel-file pattern). Called from
# two places: (1) both branches of on_failure() (the ERR trap). On an ordinary workflow
# failure this is the call that runs, with the workflow's captured transcript: bash fires
# ERR on the failed pipeline and on_failure's own `set -e` ends the shell when it returns
# (#238). A crash before the workflow has no transcript and classifies as
# environmental:delivery_failure by construction (fast, zero commits, no artifact).
# (2) The workflow loop's failure branch, reached only when on_failure returned for a
# session-window pause and the loop's own pause check then disagrees.
# Sets ERROR_SIGNATURE_WRITTEN to the signature written, or "" (#238). Call it as a plain
# command, never in $(…): errexit must still apply inside it.
```

Directly after the function's first line, `local phase="$1" exit_code="$2" text_file="${3:-}"`, add:

```bash
  ERROR_SIGNATURE_WRITTEN=""
```

Then replace the tail of the function (currently `:586-596`):

```bash
  if [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ]; then
    _cli_with_hosting_fallback "${es_args[@]}" --hosting-location local >/dev/null 2>&1 || true
  else
    # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
    # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "${es_args[@]}" \
      2>/dev/null || true
  fi
}
```

with:

```bash
  local es_out=""
  if [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ]; then
    es_out=$(_cli_with_hosting_fallback "${es_args[@]}" --hosting-location local 2>/dev/null) || true
  else
    # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
    # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
    es_out=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "${es_args[@]}" \
      2>/dev/null) || true
    # Printed to the container log as before; the local path discards it, as before.
    [ -z "$es_out" ] || printf '%s\n' "$es_out"
  fi
  # #238: the verb prints `signature=<sig>` after a successful write (cli.py:214); no such
  # line means nothing was written.
  ERROR_SIGNATURE_WRITTEN=$(printf '%s\n' "$es_out" | sed -n 's/^signature=//p' | tail -n 1)
}
```

The verb's argv is unchanged on both paths, and so is the function's stdout: the cloud path
still prints what the verb printed, and the local path still prints nothing. Only the
verb's stdout is captured. `_cli_with_hosting_fallback` folds its stderr into its output,
which is why the `sed` keeps only the `signature=` line.

- [ ] **Step 4: `on_failure` resets the global; its call sites stay as they are**

In `on_failure`, directly after the `LEDGER_NOTE` lines:

```bash
  local LEDGER_NOTE=""
  [ -n "${LEDGER_WRITE_WARNING:-}" ] && LEDGER_NOTE="

> ⚠️ ${LEDGER_WRITE_WARNING}"
```

add:

```bash
  # #238: set by _write_error_signature below; read by the annotate step at the end. Reset
  # here as well as in the function: the issue-less and close paths never call it and must
  # not reuse a stale value.
  ERROR_SIGNATURE_WRITTEN=""
```

Do **not** edit either `_write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"`
line in `on_failure`, or the call in the workflow loop's failure branch.

- [ ] **Step 5: The one bounded annotate call**

In `on_failure`, immediately before

```bash
  # Cost report runs LAST and is non-fatal: a failure here (missing dependency,
  # cost-JSON schema drift) must never abort the trap before the Blocked transition
  # and failure comment above have run.
  post_cost_report || true
}
trap on_failure ERR
```

insert:

```bash
  # #238: add the written signature's failure class to the record assembled above. Last
  # before the cost report and bounded: nothing new may stand between a failure and its
  # board move and comment. Any exit status is ignored; a clone cli.py that predates the
  # verb only loses the annotation. TARGET-PATH: as above.
  if [ -n "${ERROR_SIGNATURE_WRITTEN:-}" ] && [ -n "${ARTIFACTS_DIR:-}" ]; then
    timeout -k 5 20 python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record annotate-failure \
      --run-id "${RUN_ID:-unknown}" --out-file "$ARTIFACTS_DIR/run-record.json" \
      --failure-signature "$ERROR_SIGNATURE_WRITTEN" >/dev/null 2>&1 || true
  fi
```

The pause branch (`return`) and the blast-hold branch (`exit 0`) leave `on_failure` before
this point, so neither is annotated. That is intended (spec §5a "Coverage"). A target that
ships its own older `dark-factory/scripts/` copy (one without the verb) exits 2 here and is
not annotated, which spec §5a already accepts ("an unknown `annotate-failure` verb only
loses the annotation"). No baked-copy retry is added: it would put a second process inside
the 20 s bound for a case the spec scopes out.

**Deviation from the spec's §5a wording, recorded for review.** §5a describes the call sites
as `SIG=$(_write_error_signature …) || true` followed by `echo "signature=$SIG"`. The plan
keeps the call sites unchanged and returns the value through `ERROR_SIGNATURE_WRITTEN`
instead. The spec's wording would break its own stated invariants. It requires that the
exit code, order and argv of every existing call stay unchanged, and that a failure before
the clone exists gets no class. Case I measured that wording changing both the exit code
(128 → 3) and the order (a new board move). Everything else in §5a holds as written: one
annotate call, after the comment, before the cost report, `timeout -k 5 20`, `|| true`.

- [ ] **Step 6: Run the new test and see it pass**

Run: `bash tests/test_entrypoint_failure_signature.sh`
Expected: `Results: 24 passed, 0 failed`. Case D takes about 20 s.

- [ ] **Step 7: Run the existing entrypoint tests that exercise these functions**

Run:
```bash
for t in test_entrypoint_error_signature test_entrypoint_hosting_fallback test_entrypoint_blast_hold \
         test_entrypoint_session_window test_entrypoint_preflight test_entrypoint_cost_capture_log_thread \
         test_run_record_hermetic; do
  SCHEDULER_STATE_DIR="$(mktemp -d)" bash "tests/$t.sh" > "/tmp/$t.log" 2>&1 && echo "ok $t" || { echo "FAIL $t"; tail -20 "/tmp/$t.log"; }
done
```
Expected: `ok` for all seven, with no edit to any of them. `test_run_record_hermetic.sh` lints
every `tests/test_*.sh`, including the new one: `SCHEDULER_STATE_DIR` and `CURRENT_RUN_DIR`
must be exported at their first mention, as `export VAR=...`, before the `source` line. `_write_error_signature`'s
stdout is unchanged (Step 3), so no existing assertion can see a difference.

- [ ] **Step 8: Wire the test into CI**

In `.github/workflows/ci.yml`, directly after

```yaml
      - run: bash tests/test_entrypoint_hosting_fallback.sh
```

add

```yaml
      - run: bash tests/test_entrypoint_failure_signature.sh
```

This is inside the block that the "Assert none of the entrypoint tests touched
/var/lib/dark-factory" step checks. The test keeps all state under its own `mktemp -d`. Do
not add the file to `CI_EXEMPT_SHELL_TESTS`.

Run: `PYTHONPATH=scripts python -m pytest tests/test_ci_covers_shell_tests.py -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add entrypoint.sh tests/test_entrypoint_failure_signature.sh .github/workflows/ci.yml
git commit -m "feat(#238): on_failure annotates the failed run record with its failure class"
```

---

## Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q`
Expected: no failures (skips are fine). Run it in the git clone:
`tests/test_hybrid_execution_contract_doc.py::test_cited_repo_files_resolve` shells out to
`git ls-files` and fails outside a work tree.

- [ ] **Step 2: Unchanged-behaviour evidence (acceptance criteria met by existing behaviour)**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py tests/test_factory_core_error_signature.py -v -k "record_failure_signature_second_matching_substantive_is_stuck or trip_to_blocked_moves_to_blocked or trip_to_blocked_adds_both_labels or trip_to_blocked_posts_comment or error_signature"`
Expected: all pass. Then `git diff origin/main -- scripts/factory_core/breaker.py scripts/factory_core/error_signature.py scheduler.sh scripts/factory_core/preview_helper.py scripts/factory_core/model_proxy.py scripts/factory_core/cli.py tests/test_factory_core_breaker.py tests/test_factory_core_error_signature.py deploy .github/workflows/publish.yml`
prints nothing.

- [ ] **Step 3: The CI shell tests and DAG checks**

Run: `bash tests/test_entrypoint_failure_signature.sh && bash tests/test_entrypoint_error_signature.sh && bash tests/test_run_record_hermetic.sh && bash tests/test_cost_report_harness_economics.sh`
Expected: each ends `0 failed` (or its own success line) and exits 0.

Run the `dag-check` job's commands from `.github/workflows/ci.yml`:
`python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml && python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
Expected: exit 0. Nothing in this plan touches the workflow YAML or those docs. This is a
regression check.

- [ ] **Step 4: Scope check**

Run: `git diff --name-only origin/main...HEAD -- . ':!docs/superpowers/specs/' ':!docs/superpowers/plans/' ':!.archon/memory/'`
Expected: exactly these eight paths:
```
.github/workflows/ci.yml
entrypoint.sh
scripts/factory_core/failure_taxonomy.py
scripts/factory_core/providers/model_adapter.py
scripts/factory_core/run_record.py
tests/test_entrypoint_failure_signature.sh
tests/test_failure_taxonomy.py
tests/test_run_record.py
```
If another path shows up, check it with the two-dot form
(`git diff origin/main HEAD -- <path>`, project memory) before treating it as out of scope.

- [ ] **Step 5: Commit any fix-ups**

If Steps 1–4 forced a fix, commit it with a message naming the failing check, e.g.
`git commit -m "fix(#238): <what> (found by <check>)"`. Otherwise there is nothing to commit.

---

## Acceptance criteria → tests

| Acceptance criterion | Test(s) |
|---|---|
| Retry/fallback paths classify failure before spending another model turn (classification is now mapped and durably recorded; when and whether a retry happens is unchanged) | `test_class_for_signature_covers_every_classify_return`, `test_class_for_signature_strips_exit_code_suffix`, `test_annotate_failure_sets_by_class_from_signature`, `test_entrypoint_failure_signature.sh` case A |
| Repeated byte-identical failing tool calls trip a visible breaker (phase-attempt granularity, #373) | existing `tests/test_factory_core_breaker.py::test_record_failure_signature_second_matching_substantive_is_stuck`, unmodified; `test_breaker_outcomes_are_not_taxonomy_keys` |
| Failure-spend tokens/cost recorded in the harness economics ledger | `test_failure_spend_by_class_counts_ledger_failure_rows`, `test_failure_spend_by_class_adds_ledger_request_counts`, `test_annotate_failure_*` |
| Existing scheduler retry/circuit-breaker behaviour remains compatible | `tests/test_factory_core_breaker.py` and `tests/test_factory_core_error_signature.py` unmodified; `test_nothing_reads_the_policy_fields`; `test_failure_taxonomy_preserves_model_adapter_order`; the guards in `test_entrypoint_failure_signature.sh` |
| Human-required failures halt durably (existing `trip_to_blocked` path) | existing `::test_trip_to_blocked_moves_to_blocked`, `::test_trip_to_blocked_adds_both_labels`, `::test_trip_to_blocked_posts_comment`; `test_enforced_rows_declare_no_unenforced_policy` |
| Hermetic outage/OOM/timeout/forbidden destination: no export, no retry storm, no hidden backend switch, no gate bypass | `test_hybrid_failure_is_recorded_without_export_or_backend_switch` (4 cases) + existing proxy tests named in Task 7 |
| Failed attempts and destination feed the run record, including terminal failures | `test_hybrid_failure_*`, `test_entrypoint_failure_signature.sh` cases A, E |
| Default-cloud behaviour unchanged | `test_failure_taxonomy_imports_nothing_from_providers`, `assemble_argv_is_unchanged`, `signature_write_argv_and_position_are_unchanged` |

## Out of scope (named follow-ups; each needs its own human-reviewed spec)

Reading the taxonomy at retry time (`breaker.py::evaluate_stop_condition` using
`max_attempts`), fallback-model/fallback-phase dispatch, `token_budget_multiplier`
enforcement, a per-tool-call `repeat_tool_call_loop` detector, live wiring of the tagging-only
gate classes, splitting `github_rate_limit` from `model_rate_limit`, whether a fast local
outage should keep classifying as `delivery_failure`, the Proactive Memory Agent classes
(#241), and why 25 of 85 failed run ids have no retained record. `run-record
backfill-economics` will drop an annotated class from the artifacts copy it rewrites. That is
its documented degrade-only behaviour and is not changed here.

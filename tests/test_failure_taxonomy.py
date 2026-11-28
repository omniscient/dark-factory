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

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

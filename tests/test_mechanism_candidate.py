"""Tests for scripts/factory_core/mechanism_candidate.py — harness candidate contract and
mechanism lineage (#464). No network; the only subprocess is the CLI exit-code test.
"""
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import state_governance_audit as sga  # noqa: E402
from factory_core import mechanism_candidate as mc  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "mechanism-candidates" / "fixtures"
_MANIFEST = _FIXTURES_DIR / "manifest.json"


# ── Digests ─────────────────────────────────────────────────────────────────────

def test_canonical_digest_determinism():
    a = {"b": [1, "é"], "a": {"y": None, "x": True}}
    b = json.loads('{ "a" : { "x" : true, "y" : null }, "b" : [ 1, "\\u00e9" ] }')
    assert mc.canonical_digest(a) == mc.canonical_digest(b)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", mc.canonical_digest(a))
    assert mc.canonical_digest({"k": "v"}) != mc.canonical_digest({"k": "w"})
    assert mc.DIGEST_RE.pattern == r"^sha256:[0-9a-f]{64}$"

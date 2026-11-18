"""The starter adapter shipped in templates/new-target must load as-is.

Every adapter reader fails open to built-in defaults on a parse error, so an
unparseable template silently discards a new target's whole adapter (#492).
"""
import shutil
from pathlib import Path

from factory_core import adapter

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "new-target" / ".factory"


def test_template_adapter_loads(tmp_path):
    shutil.copytree(TEMPLATE, tmp_path / ".factory")
    data = adapter.load(str(tmp_path))
    assert r"^\.factory/hooks/" in data["safety"]["critical_diff_paths"]
    assert r"^\.mcp\.json$" in data["safety"]["migration_seed_auth_patterns"]

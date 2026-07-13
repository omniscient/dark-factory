import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def test_codehost_is_abstract_with_required_ops():
    from factory_core.providers.codehost.base import CodeHost

    required = {
        "remote_url", "find_change_for", "open_change", "update_change_body",
        "mark_ready", "merge_change", "get_change_checks", "get_change_mergeable",
        "get_change_reviews", "get_change_inline_comments", "close_keyword",
    }
    assert required.issubset(CodeHost.__abstractmethods__)
    with pytest.raises(TypeError):
        CodeHost()

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


def test_codehost_degradable_ops_have_safe_defaults():
    from factory_core.providers.codehost.base import CodeHost

    class _Bare(CodeHost):
        def remote_url(self): return ""
        def find_change_for(self, branch): return None
        def open_change(self, source, target, title, body, draft=False): return "1"
        def update_change_body(self, id, body): return True
        def mark_ready(self, id): pass
        def merge_change(self, id, strategy="merge", delete_branch=True): return True
        def get_change_checks(self, id): return []
        def get_change_mergeable(self, id): return "UNKNOWN"
        def get_change_reviews(self, id): return ""
        def get_change_inline_comments(self, id): return []
        def close_keyword(self, issue_id): return ""

    assert _Bare.required_env() == []

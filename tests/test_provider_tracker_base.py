import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def test_tracker_is_abstract_with_required_ops():
    from factory_core.providers.tracker.base import Tracker

    required = {
        "list_work_items", "get_item", "get_comments", "get_children",
        "set_status", "add_label", "remove_label", "upsert_comment",
        "create_item", "resolve_item",
    }
    assert required.issubset(Tracker.__abstractmethods__)
    with pytest.raises(TypeError):
        Tracker()


def test_tracker_degradable_ops_have_safe_defaults():
    from factory_core.providers.tracker.base import Tracker

    class _Bare(Tracker):
        def list_work_items(self, statuses, labels=None): return []
        def get_item(self, id): return {}
        def get_comments(self, id): return []
        def get_children(self, epic_id): return []
        def set_status(self, id, canonical): pass
        def add_label(self, id, name): pass
        def remove_label(self, id, name): pass
        def upsert_comment(self, id, marker, body): pass
        def create_item(self, title, body, labels=None): return "1"
        def resolve_item(self, id): pass

    bare = _Bare()
    assert bare.get_status_limits() == {}
    assert bare.get_rate_budget() == {"remaining": None, "reset": None, "used": None, "limit": None}
    assert _Bare.required_env() == []

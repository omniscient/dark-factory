"""Tracker provider contract (parent spec docs/provider-abstraction-design.md §5.1).

IDs are opaque strings everywhere — no int() coercion anywhere in a conforming
implementation. `get_status_limits`/`get_rate_budget` are "degradable" (principle 4):
concrete, non-abstract methods with safe defaults so a minimal adapter needs no
override to have a working low floor.
"""
from abc import ABC, abstractmethod


class Tracker(ABC):
    @abstractmethod
    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        """Poll-loop discovery: work items whose canonical status is in `statuses`,
        optionally further filtered to items carrying every label in `labels`."""

    @abstractmethod
    def get_item(self, id: str) -> dict:
        """Title/body/labels (+ comments, adapter-dependent) for a single item."""

    @abstractmethod
    def get_comments(self, id: str) -> list:
        """The comment thread for an item."""

    @abstractmethod
    def get_children(self, epic_id: str) -> list:
        """Epic -> children (sub-issues / epic-link issues)."""

    @abstractmethod
    def set_status(self, id: str, canonical: str) -> bool:
        """Move an item to one of the seven canonical statuses. Returns True iff
        the item's status actually changed; False for "not found on the board /
        no valid transition" -- implementations must not raise for that case.
        For "operation failed" (e.g. a transport/API error attempting the move),
        an implementation may either return False or raise, depending on
        whether the underlying transport already distinguishes that failure
        mode for callers (see JiraTracker.set_status, which lets `_request`'s
        RuntimeError on HTTP errors propagate by design; callers are expected
        to handle it)."""

    @abstractmethod
    def add_label(self, id: str, name: str) -> bool:
        """True iff the label was applied. The meaning of False is
        implementation-dependent: GitHub returns False on a failed gh/API call
        (transport failures do not raise there), while Jira has no false-on-failure
        path at all -- `JiraTracker.add_label` returns True unconditionally and lets
        `_request`'s RuntimeError on HTTP errors propagate instead (same distinction
        as `set_status`). Callers that need attempt-all-then-report semantics across
        both providers must catch RuntimeError as well as check the return value."""

    @abstractmethod
    def remove_label(self, id: str, name: str) -> bool:
        """True iff the label was removed; see add_label docstring for the
        per-provider difference in how failure is signaled (False vs. raise)."""

    @abstractmethod
    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        """Idempotent marker-comment: create if absent, else update in place."""

    @abstractmethod
    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        """Create a new item (e.g. a regression ticket); returns its opaque id."""

    @abstractmethod
    def resolve_item(self, id: str) -> None:
        """Explicit close-on-merge / Done transition."""

    def get_status_limits(self) -> dict:
        """WIP limits per canonical status. Degradable: safe default is 'no limits known'."""
        return {}

    def get_rate_budget(self) -> dict:
        """Throttle signal for the poll loop. Degradable: safe default is 'unknown'."""
        return {"remaining": None, "reset": None, "used": None, "limit": None}

    @classmethod
    def required_env(cls) -> list[str]:
        """Env vars this adapter needs present at boot. Degradable: [] by default."""
        return []

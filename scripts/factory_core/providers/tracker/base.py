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
    def get_item(self, id: str, fields: tuple | None = None) -> dict:
        """Title/body/labels/state (+ comments, adapter-dependent) for a single item.
        `fields` is a tuple of field names (e.g. ("body",) or ("state",)); the result
        contains exactly those keys. None means the adapter's default set. `state` is
        "OPEN" or "CLOSED"."""

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
    def add_comment(self, id: str, body: str) -> bool:
        """Append a new comment (never edits an existing one; contrast `upsert_comment`).
        True iff the comment was posted. GitHub returns False on a failed gh call (no
        raise); Jira lets `_request`'s RuntimeError propagate (same split as `add_label`)."""

    @abstractmethod
    def find_comment(self, id: str, marker: str) -> dict | None:
        """The newest comment on `id` whose body contains `marker`, as
        {"id": str, "body": str}; None when there is none or the lookup failed
        (callers treat the two the same: "post a fresh one")."""

    @abstractmethod
    def update_comment(self, id: str, comment_id: str, body: str) -> bool:
        """Replace the body of the comment `comment_id` (as returned by `find_comment`)
        on item `id`. True iff updated; failure signalling as for `add_comment`."""

    @abstractmethod
    def list_board_items(self) -> dict:
        """Poll-loop snapshot of every item on the board, in the shape the scheduler's jq
        filters consume: {"items": [{"content": {"number", "title", "type": "Issue"},
        "labels": [str], "status": <board status display name or None>}]}. One call
        sequence per poll (paged internally). Raises RuntimeError when the board cannot
        be read at all, so the poll loop can skip the cycle."""

    @abstractmethod
    def list_items(self, label: str, limit: int = 200) -> list:
        """Ids (opaque strings) of OPEN items carrying `label`, at most `limit`, whether
        or not they are on the board (off-board detection). Raises RuntimeError when
        the lookup fails or returns something that is not a list."""

    @abstractmethod
    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        """Create a new item (e.g. a regression ticket); returns its opaque id."""

    @abstractmethod
    def resolve_item(self, id: str) -> None:
        """Explicit close-on-merge / Done transition."""

    def get_states(self, ids: list[str]) -> dict:
        """Batched state read (#607): mapping of each requested id -> "OPEN"/"CLOSED";
        None when the item does not exist or its state is unknown. Callers treat
        None as unmet (fail closed).

        Contract for non-GitHub providers: the degradable per-id `get_item` read loop
        below is a conforming implementation — a provider that raises on transport
        failure (Jira) maps the failed id to None, and a provider that swallows it
        (GitHub) maps the missing `state` key to None. Providers with a batch-capable
        API may override with fewer calls (GitHub: one aliased GraphQL query per
        chunk). IDs are opaque strings and flow through unmodified, like every id
        in this contract."""
        out: dict = {}
        for i in ids:
            key = str(i)
            try:
                item = self.get_item(key, fields=("state",))
            except Exception:
                out[key] = None
                continue
            out[key] = item.get("state") if isinstance(item, dict) else None
        return out

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

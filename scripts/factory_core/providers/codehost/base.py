"""CodeHost provider contract (parent spec docs/provider-abstraction-design.md §6.1).

Plain git (clone/branch/commit/push/fetch/diff) is host-agnostic and stays inline,
outside this contract (principle 3) — the only git-adjacent method here is
remote_url(). All ids are opaque strings.
"""
from abc import ABC, abstractmethod


class CodeHost(ABC):
    @abstractmethod
    def remote_url(self) -> str:
        """Auth-embedded clone/push URL for this host."""

    @abstractmethod
    def find_change_for(self, branch: str, exact: bool = False,
                         repo: str | None = None) -> str | None:
        """The open PR/MR id for a branch (or branch prefix), or None. `exact` selects
        an exact branch match instead of a prefix match; `repo` is an optional
        `owner/name` override."""

    @abstractmethod
    def find_change_details(self, branch: str, exact: bool = False,
                             repo: str | None = None) -> dict | None:
        """The open PR/MR {number, isDraft, mergeable} for a branch (or prefix), or None."""

    @abstractmethod
    def open_change(self, source: str | None, target: str | None, title: str, body: str,
                     draft: bool = False, repo: str | None = None) -> str:
        """Create a PR/MR; returns its opaque id."""

    @abstractmethod
    def update_change_body(self, id: str, body: str) -> bool:
        ...

    @abstractmethod
    def mark_ready(self, id: str, repo: str | None = None) -> None:
        """Draft -> ready for review."""

    @abstractmethod
    def merge_change(self, id: str, strategy: str = "merge",
                      delete_branch: bool = True, repo: str | None = None,
                      match_head: str | None = None) -> bool:
        """Merge; with match_head, refuse unless the change's head is exactly that sha
        (#551: the close-time blast-radius check pins the head it verified)."""

    @abstractmethod
    def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                           repo: str | None = None) -> list:
        """CI check results: [{name, bucket, ...}]. `fields` is a comma-separated
        string of check fields."""

    @abstractmethod
    def get_change_mergeable(self, id: str, repo: str | None = None) -> str:
        """Conflict-gate enum, e.g. MERGEABLE / CONFLICTING / UNKNOWN."""

    @abstractmethod
    def get_change_reviews(self, id: str, repo: str | None = None) -> str:
        """Latest review-state signal, e.g. APPROVED / CHANGES_REQUESTED / ''."""

    @abstractmethod
    def get_change_inline_comments(self, id: str, repo: str | None = None) -> list:
        """Inline review feedback: [{path, line, body, created_at}]."""

    @abstractmethod
    def close_keyword(self, issue_id: str) -> str:
        """Body snippet that auto-closes issue_id on merge — only meaningful when
        tracker == code host (parent spec §6.4); '' otherwise."""

    @classmethod
    def required_env(cls) -> list[str]:
        """Env vars this adapter needs present at boot. Degradable: [] by default."""
        return []

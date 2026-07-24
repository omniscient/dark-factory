"""GitLabCodeHost — seam proof (design doc §6.3) that the CodeHost ABC (design
doc §6.1) is not GitHub-shaped. Pure-mapping methods below (remote_url,
close_keyword, and the private draft-prefix/id-validation helpers) run for
real and are unit-tested; every HTTP-backed operation raises
NotImplementedError — a full, live-validated GitLab implementation is an
explicit follow-up ticket, filed only if requested (see
docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof).
"""
import os

from factory_core import identity
from factory_core.providers.codehost.base import CodeHost

_DRAFT_PREFIX = "Draft: "

# _draft_title / _strip_draft_prefix are demonstration-only: they are unit-tested
# to prove the draft-prefix mapping design (design doc §6.3), but open_change/
# mark_ready — their only real call sites — currently raise NotImplementedError,
# so neither helper is wired into a live code path yet. Intentionally dead until
# the live GitLab implementation lands (see
# docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof).


def _draft_title(title: str) -> str:
    return title if title.startswith(_DRAFT_PREFIX) else f"{_DRAFT_PREFIX}{title}"


def _strip_draft_prefix(title: str) -> str:
    return title[len(_DRAFT_PREFIX):] if title.startswith(_DRAFT_PREFIX) else title


def _validate_change_id(change_id: str) -> str:
    """Opaque-string contract (design doc principle 5): a GitLab MR id is
    `<group/project>!<iid>` — never coerce it to int anywhere in this adapter."""
    if not isinstance(change_id, str) or not change_id:
        raise ValueError(f"GitLab change id must be a non-empty opaque string, got {change_id!r}")
    return change_id


class GitLabCodeHost(CodeHost):
    """GitLab MR seam proof — see docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof."""

    @classmethod
    def required_env(cls) -> list[str]:
        return ["GITLAB_TOKEN"]

    def remote_url(self) -> str:
        token = os.environ.get("GITLAB_TOKEN", "")
        host = os.environ.get("GITLAB_BASE_URL", "gitlab.com")
        return f"https://oauth2:{token}@{host}/{identity.SLUG}.git"

    def close_keyword(self, issue_id: str) -> str:
        if os.environ.get("FACTORY_TRACKER", "github") != "gitlab":
            return ""
        return f"Closes #{issue_id}"

    def find_change_for(self, branch: str) -> str | None:
        raise NotImplementedError(
            "live GitLab MR list API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def find_change_details(self, branch: str, exact: bool = False,
                             repo: str | None = None) -> dict | None:
        raise NotImplementedError(
            "live GitLab MR list API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def open_change(self, source: str, target: str, title: str, body: str,
                     draft: bool = False) -> str:
        raise NotImplementedError(
            "live GitLab MR create API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def update_change_body(self, change_id: str, body: str) -> bool:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab MR update API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def mark_ready(self, change_id: str) -> None:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab MR Draft-prefix removal API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def merge_change(self, change_id: str, strategy: str = "merge", delete_branch: bool = True) -> bool:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab MR merge API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def get_change_checks(self, change_id: str) -> list:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab pipelines API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def get_change_mergeable(self, change_id: str) -> str:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab merge_status/has_conflicts API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def get_change_reviews(self, change_id: str) -> str:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab approvals API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def get_change_inline_comments(self, change_id: str) -> list:
        _validate_change_id(change_id)
        raise NotImplementedError(
            "live GitLab discussions API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

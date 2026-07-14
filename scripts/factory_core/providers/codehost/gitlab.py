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


def _draft_title(title: str) -> str:
    return title if title.startswith(_DRAFT_PREFIX) else f"{_DRAFT_PREFIX}{title}"


def _strip_draft_prefix(title: str) -> str:
    return title[len(_DRAFT_PREFIX):] if title.startswith(_DRAFT_PREFIX) else title


def _validate_change_id(id: str) -> str:
    """Opaque-string contract (design doc principle 5): a GitLab MR id is
    `<group/project>!<iid>` — never coerce it to int anywhere in this adapter."""
    if not isinstance(id, str) or not id:
        raise ValueError(f"GitLab change id must be a non-empty opaque string, got {id!r}")
    return id


class GitLabCodeHost(CodeHost):
    """GitLab MR seam proof — see docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof."""

    def remote_url(self) -> str:
        token = os.environ.get("GITLAB_TOKEN", "")
        host = os.environ.get("GITLAB_BASE_URL", "gitlab.com")
        return f"https://oauth2:{token}@{host}/{identity.SLUG}.git"

    def close_keyword(self, issue_id: str) -> str:
        if os.environ.get("FACTORY_TRACKER", "github") != "gitlab":
            return ""
        return f"Closes #{issue_id}"

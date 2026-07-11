"""GitHubCodeHost — mechanical extraction of today's gh pr / gh api ...pulls... calls.
Most current CodeHost-shaped operations live as inline strings in
workflows/archon-dark-factory.yaml and entrypoint.sh, not any factory_core Python
module — that YAML/bash text is the golden baseline these argv constants are
transcribed from (spec Architecture section, "CodeHost: no existing Python home")."""
import os
import re
import subprocess

from factory_core import identity
from factory_core.providers.codehost.base import CodeHost


class GitHubCodeHost(CodeHost):
    def remote_url(self) -> str:
        token = os.environ.get("GH_TOKEN", "")
        return f"https://{token}@github.com/{identity.SLUG}.git"

    def find_change_for(self, branch: str, exact: bool = False,
                         repo: str | None = None, fields: str = "number") -> str | None:
        cmd = ["gh", "pr", "list"]
        if repo:
            cmd += ["--repo", repo]
        if exact:
            cmd += ["--head", branch]
        else:
            cmd += ["--search", f"head:{branch}"]
        cmd += ["--json", fields, "--jq", ".[0].number // empty"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = r.stdout.strip() if r.returncode == 0 else ""
        return out or None

    def open_change(self, source: str | None, target: str | None, title: str, body: str,
                     draft: bool = False, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "create"]
        if repo:
            cmd += ["--repo", repo]
        if target:
            cmd += ["--base", target]
        if source:
            cmd += ["--head", source]
        cmd += ["--title", title, "--body", body]
        if draft:
            cmd.append("--draft")
        r = subprocess.run(cmd, capture_output=True, text=True)
        m = re.search(r"/pull/(\d+)", r.stdout or "")
        return m.group(1) if m else (r.stdout or "").strip()

    def update_change_body(self, id: str, body: str) -> None:
        subprocess.run(["gh", "pr", "edit", id, "--body", body], capture_output=True)

    def mark_ready(self, id: str, repo: str | None = None) -> None:
        cmd = ["gh", "pr", "ready", id]
        if repo:
            cmd += ["--repo", repo]
        subprocess.run(cmd, capture_output=True)

    # --- Stubs for not-yet-implemented CodeHost ops ---
    # Same rationale as GitHubTracker's Task 5 stubs: a stub override removes
    # a method from CodeHost.__abstractmethods__, so GitHubCodeHost is
    # instantiable starting now (needed for this task's own tests, which call
    # `GitHubCodeHost()` directly). Task 13 replaces every stub below with a
    # real implementation.
    def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
                      repo: str | None = None) -> bool:
        raise NotImplementedError  # Task 13

    def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                           repo: str | None = None) -> list:
        raise NotImplementedError  # Task 13

    def get_change_mergeable(self, id: str, repo: str | None = None) -> str:
        raise NotImplementedError  # Task 13

    def get_change_reviews(self, id: str, repo: str | None = None) -> str:
        raise NotImplementedError  # Task 13

    def get_change_inline_comments(self, id: str, repo: str | None = None) -> list:
        raise NotImplementedError  # Task 13

    def close_keyword(self, issue_id: str) -> str:
        raise NotImplementedError  # Task 13

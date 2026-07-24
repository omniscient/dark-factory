"""GitHubCodeHost — mechanical extraction of today's gh pr / gh api ...pulls... calls.
Most current CodeHost-shaped operations live as inline strings in
workflows/archon-dark-factory.yaml and entrypoint.sh, not any factory_core Python
module — that YAML/bash text is the golden baseline these argv constants are
transcribed from (spec Architecture section, "CodeHost: no existing Python home")."""
import json
import os
import re
import subprocess

from factory_core import identity
from factory_core.providers.codehost.base import CodeHost


class GitHubCodeHost(CodeHost):
    @classmethod
    def required_env(cls) -> list[str]:
        return ["GH_TOKEN"]

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

    def find_change_details(self, branch: str, exact: bool = False,
                             repo: str | None = None) -> dict | None:
        cmd = ["gh", "pr", "list"]
        if repo:
            cmd += ["--repo", repo]
        if exact:
            cmd += ["--head", branch]
        else:
            cmd += ["--search", f"head:{branch}"]
        cmd += ["--json", "number,isDraft,mergeable"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        try:
            arr = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
        return arr[0] if isinstance(arr, list) and arr else None

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

    def update_change_body(self, id: str, body: str) -> bool:
        r = subprocess.run(["gh", "pr", "edit", id, "--body", body], capture_output=True)
        return r.returncode == 0

    def mark_ready(self, id: str, repo: str | None = None) -> None:
        cmd = ["gh", "pr", "ready", id]
        if repo:
            cmd += ["--repo", repo]
        subprocess.run(cmd, capture_output=True)

    def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
                      repo: str | None = None) -> bool:
        cmd = ["gh", "pr", "merge", id]
        if repo:
            cmd += ["--repo", repo]
        cmd.append(f"--{strategy}")
        if delete_branch:
            cmd.append("--delete-branch")
        r = subprocess.run(cmd, capture_output=True)
        return r.returncode == 0

    def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                           repo: str | None = None) -> list:
        cmd = ["gh", "pr", "checks", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", fields]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return []
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def get_change_mergeable(self, id: str, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "view", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", "mergeable", "--jq", ".mergeable"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        result = r.stdout.strip() if r.returncode == 0 else ""
        return result or "UNKNOWN"

    def get_change_reviews(self, id: str, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "view", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", "reviews", "--jq",
                '[.reviews[] | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")] | last | .state // ""']
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def get_change_inline_comments(self, id: str, repo: str | None = None) -> list:
        slug = repo or identity.SLUG
        r = subprocess.run(
            ["gh", "api", f"repos/{slug}/pulls/{id}/comments",
             "--jq", "[.[] | {path: .path, line: .line, body: .body, created_at: .created_at}]"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return []
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return []

    def close_keyword(self, issue_id: str) -> str:
        return f"Closes #{issue_id}"

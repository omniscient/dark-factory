"""Dependency-Health Loop — scheduled advisory audit, bounded fix, reviewable PR.

Runs the target's `.factory/hooks/deps-audit` check on origin/main; when advisories are
present, files (or updates) one tracking issue, runs the optional deterministic
`deps-fix` hook, then at most one bounded `claude -p` pass constrained to
`safety.deps_allowed_paths`, and opens a PR that is NOT merged unless
DEPS_AUDIT_AUTO_MERGE=true or the tracking issue carries `direct-to-pr`. See
docs/superpowers/specs/2026-11-07-deps-audit-loop-design.md (#484). Ships OFF.

Pure core + injected IO, same split as main_red_fixer.py (whose classify_scope and
ci_status are reused, not copied).
"""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import time

from . import identity
from .adapter_defaults import DEFAULTS as _AD
from .main_red_fixer import LiveIO as _MainRedLiveIO
from .main_red_fixer import classify_scope

_DEFAULT_ALLOWED_PATHS = _AD["safety"]["deps_allowed_paths"]

# Substring tokens the fix may never touch (classify_scope's blocked list — dominates).
# CI workflows and audit ignore lists live under these; the allowlist globs are the
# positive constraint, this is the fail-closed backstop.
BLOCKED_PATHS = [".github/", ".factory/", ".claude/", "dark-factory/",
                 "scheduler.sh", "entrypoint.sh"]

TRACKING_MARKER = "<!-- df-deps-audit -->"
NOTES_FILE = ".deps-fix-notes.md"  # agent-written PR-body notes; read + deleted, never committed
FIELDS = ("ecosystem", "package", "installed", "fixed_in", "id", "severity")


def _deps_allowed_paths(clone_dir: str | None = None) -> list:
    """Return safety.deps_allowed_paths from the adapter at use-time, falling back to defaults."""
    try:
        from . import adapter
        val = adapter.get(clone_dir or ".", "safety.deps_allowed_paths")
        if val is not None and isinstance(val, list):
            return val
    except Exception:
        pass
    return list(_DEFAULT_ALLOWED_PATHS)


# Advisory fields come from external feeds (registry metadata relayed by the hook): keep
# package / version / range / id characters only, so no backticks, markup, HTML comments or
# brackets reach the fix-agent prompt or a GitHub body.
_UNSAFE = re.compile(r"[^A-Za-z0-9@/._:<>=~^*,+| -]")


def _clean(v: str) -> str:
    return _UNSAFE.sub("?", v.strip())[:120]


def parse_advisories(stdout: str) -> list:
    """Hook stdout → advisory dicts. One advisory per line, exactly six TAB-separated
    fields (fixed_in may be empty); any other line (install noise, banners) is ignored."""
    out = []
    for line in (stdout or "").splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) != len(FIELDS) or not parts[4].strip():
            continue
        out.append(dict(zip(FIELDS, (_clean(p) for p in parts))))
    return out


def read_audit(rc: int, stdout: str):
    """Hook result → advisory list, or None when the hook failed without a parseable
    report (an error, never 'clean'). Exit 0 is clean by contract, whatever stdout says."""
    if rc == 0:
        return []
    advisories = parse_advisories(stdout)
    return advisories or None


def advisory_set_hash(advisories: list) -> str:
    """Stable 12-hex identity of the advisory-id SET (order/duplicate-insensitive)."""
    ids = sorted({a["id"] for a in advisories})
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:12]


def _glob_match(path: str, glob: str) -> bool:
    """`**/<name>` (no further `/`) matches the BASENAME at any depth, repo root included,
    so `**/requirements*.txt` never admits `docs/requirements-x/notes.txt`. Any other glob
    is a full-path fnmatch (a leading `**/` may also match at the root)."""
    tail = glob[3:] if glob.startswith("**/") else None
    if tail is not None and "/" not in tail:
        return fnmatch.fnmatchcase(os.path.basename(path), tail)
    return fnmatch.fnmatchcase(path, glob) or (tail is not None and fnmatch.fnmatchcase(path, tail))


def allowed_matches(changed_paths: list, globs: list) -> list:
    """Changed paths matching any allowlist glob."""
    return [p for p in changed_paths if any(_glob_match(p, g) for g in globs)]


def classify_deps_scope(changed_paths: list, globs: list, blocked: list) -> str:
    """main_red_fixer.classify_scope over a glob allowlist. classify_scope matches allowed
    entries by substring, so each path is NUL-wrapped: an allowed entry then matches only
    its own exact path (root `package.json` must not admit `frontend/package.json.bak`),
    while blocked substrings still match anywhere inside the path."""
    wrap = lambda p: f"\0{p}\0"  # noqa: E731
    return classify_scope([wrap(p) for p in changed_paths],
                          [wrap(p) for p in allowed_matches(changed_paths, globs)],
                          blocked)


def attempts_for(state: dict, h: str) -> int:
    return (state.get("sets") or {}).get(h, {}).get("attempts", 0)


def record_attempt(state: dict, h: str) -> None:
    d = state.setdefault("sets", {}).setdefault(h, {"attempts": 0})
    d["attempts"] = d.get("attempts", 0) + 1


def cap_escalated(state: dict, h: str) -> bool:
    return (state.get("sets") or {}).get(h, {}).get("cap_escalated", False)


def mark_cap_escalated(state: dict, h: str) -> None:
    state.setdefault("sets", {}).setdefault(h, {"attempts": 0})["cap_escalated"] = True


def advisory_table(advisories: list) -> str:
    rows = ["| Ecosystem | Package | Installed | Fixed in | Advisory | Severity |",
            "|---|---|---|---|---|---|"]
    for a in advisories:
        rows.append("| " + " | ".join((a[f] or "—").replace("|", "\\|") for f in FIELDS) + " |")
    return "\n".join(rows)


def footer() -> str:
    """Every issue/PR/comment this loop posts. The factory marker is load-bearing: the
    tracking issue sits In review, and scheduler.sh::get_new_comments treats everything
    after the last 'Posted by <product> Dark Factory' comment as new human input."""
    return f"---\n{identity.marker('deps_audit')}\n{identity.marker('factory')}"


def tracking_issue_body(advisories: list, h: str) -> str:
    return (f"{TRACKING_MARKER}\n<!-- df-deps-audit-hash:{h} -->\n"
            f"The dependency audit (`.factory/hooks/deps-audit`) reports "
            f"**{len(advisories)}** advisory(ies) on `origin/main`:\n\n"
            f"{advisory_table(advisories)}\n\n"
            f"The dependency-health loop will attempt a fix and open a PR for approval. "
            f"It never merges without approval unless `DEPS_AUDIT_AUTO_MERGE=true` or this "
            f"issue carries `direct-to-pr`.\n\n{footer()}")


def pr_title(advisories: list) -> str:
    ids = sorted({a["id"] for a in advisories})
    shown = ", ".join(ids[:5]) + (", …" if len(ids) > 5 else "")
    return f"deps: resolve {len(ids)} advisories ({shown})"


def pr_body(issue: int, advisories: list, remaining: list, changed: list,
            fix_path: str, tests: str, notes: str) -> str:
    # Refs, never Closes: a GitHub auto-close would leave the tracking issue In review on the
    # board (its "Item closed" workflow is off), holding a MAX_IN_REVIEW slot forever. The next
    # clean audit closes it AND moves it to Done (run_once's clean branch).
    parts = [f"Refs #{issue}", "",
             "Automated dependency-health fix — **not merged without approval**.", "",
             "### Advisories on origin/main", advisory_table(advisories), "",
             "### What changed",
             f"Fix path: `{fix_path}`. Files:", ""]
    parts += [f"- `{p}`" for p in changed]
    if remaining:
        parts += ["", "### Still reported after the fix (needs a human decision)",
                  advisory_table(remaining)]
    if notes.strip():
        parts += ["", "### Fix-agent notes (proposed ignores / justifications)", notes.strip()]
    parts += ["", "### Local test run", "```", tests[-4000:], "```", "", footer()]
    return "\n".join(parts)


def clean_comment() -> str:
    return ("\u2705 **Dependency-Health Loop** — the audit is clean on `origin/main`; "
            f"moving to Done and closing.\n\n{footer()}")


def ci_comment(pr: int, ci: str, merged: bool) -> str:
    if merged:
        msg = f"PR #{pr} CI is green and auto-merge was enabled — merged."
    elif ci == "green":
        msg = f"PR #{pr} CI is green — awaiting approval (not auto-merged)."
    elif ci == "red":
        msg = f"PR #{pr} CI is red — needs a human decision (`ready-for-human`)."
    else:
        msg = f"PR #{pr} CI is still `{ci}` after the bounded wait — left open for review."
    return f"\U0001f6e1️ **Dependency-Health Loop** — {msg}\n\n{footer()}"


def build_fix_prompt(remaining: list, allowed: list, blocked: list) -> str:
    return f"""The dependency audit (.factory/hooks/deps-audit) still reports advisories after the
deterministic fix pass. Resolve them with the SMALLEST, safest dependency change.

Advisories (ecosystem / package / installed / fixed_in / id / severity):
{advisory_table(remaining)}

You MAY edit ONLY files matching: {', '.join(allowed)}.
You may bump, pin, add an override/resolution, or swap a package, and regenerate the
matching lockfile (e.g. `npm install --package-lock-only`).
You MUST NOT edit: {', '.join(blocked)} — and never touch CI workflow files or any audit
ignore list, wherever it lives.
For an advisory with NO fixed version, do NOT ignore it anywhere: write a proposed ignore
entry plus a one-paragraph justification into ./{NOTES_FILE} (it goes into the PR body and
is not committed) and leave that advisory red for a human decision.

Make the minimal change, then stop. Do not commit, push, or open a PR — the harness does that.
"""

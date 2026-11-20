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
        # Code spans: a scoped npm name (@types/node) must not @-mention an account and a
        # leftover <tag> must not render. _clean has already removed every backtick.
        rows.append("| " + " | ".join(
            ("`" + a[f].replace("|", "\\|") + "`") if a[f] else "—" for f in FIELDS) + " |")
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


FENCE_LIMIT = 4000


def fenced(text: str, keep: str = "head", limit: int = FENCE_LIMIT) -> str:
    """Free text (fix-agent notes, hook output) as an inert code block for a GitHub body.
    Capped at `limit` characters with a truncation marker (keep="tail" keeps the end, where
    a test run prints its result). Every backtick becomes an apostrophe, so nothing inside
    can close the fence: `Closes #N`, `@name` and markup stay literal text."""
    text = (text or "").replace("`", "'")
    if len(text) > limit:
        cut = len(text) - limit
        if keep == "tail":
            text = f"[… truncated, {cut} characters omitted]\n" + text[-limit:]
        else:
            text = text[:limit] + f"\n[… truncated, {cut} characters omitted]"
    return f"```\n{text}\n```"


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
        parts += ["", "### Fix-agent notes (proposed ignores / justifications)",
                  fenced(notes.strip(), keep="head")]
    parts += ["", "### Local test run", fenced(tests, keep="tail"), "", footer()]
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


def run_once(cfg: dict, io, state: dict) -> dict:
    """One bounded audit → fix → PR pass. Outcomes: inactive | error | held | clean |
    escalated (reason cap | no-fix | scope:<s>) | fixed-deterministically | fixed-by-agent."""
    if not io.audit_hook_present():
        return {"outcome": "inactive", "issue": None, "pr": None}
    branch = io.start_branch()  # pristine origin/main checkout — the audit runs on it too
    advisories = read_audit(*io.run_audit())
    if advisories is None:
        return {"outcome": "error", "issue": None, "pr": None, "reason": "audit-unparseable"}
    if not advisories:
        closed = io.open_tracking_issues()
        for n in closed:
            io.set_done(n)  # before closing: a closed issue left In review holds a slot forever
            io.close_issue(n, clean_comment())
        return {"outcome": "clean", "issue": closed[0] if closed else None, "pr": None}

    h = advisory_set_hash(advisories)
    issue = io.find_or_create_tracking_issue(advisories, h)
    if issue is None:
        return {"outcome": "error", "issue": None, "pr": None, "reason": "tracking-issue"}
    if io.is_halted(issue):  # needs-discussion halts ALL automation, this loop included
        return {"outcome": "held", "issue": issue, "pr": None, "reason": "needs-discussion"}
    if attempts_for(state, h) >= cfg["max_attempts"]:
        if not cap_escalated(state, h):
            io.escalate(issue, "cap", h)
            mark_cap_escalated(state, h)
        return {"outcome": "escalated", "issue": issue, "pr": None, "reason": "cap"}
    record_attempt(state, h)

    io.run_deterministic_fix()
    remaining = read_audit(*io.run_audit())
    remaining = advisories if remaining is None else remaining  # unparseable → still red
    fix_path, notes = "fixed-deterministically", ""
    if remaining:
        notes = io.apply_agent_fix(build_fix_prompt(remaining, cfg["allowed_paths"],
                                                    cfg["blocked_paths"])) or ""
        after = read_audit(*io.run_audit())
        remaining = remaining if after is None else after
        fix_path = "fixed-by-agent"

    changed = io.changed_paths()
    if not changed:
        io.escalate(issue, "no-fix", h)
        return {"outcome": "escalated", "issue": issue, "pr": None, "reason": "no-fix"}
    scope = classify_deps_scope(changed, cfg["allowed_paths"], cfg["blocked_paths"])
    if scope != "allowed":
        io.escalate(issue, f"scope:{scope}", h)
        return {"outcome": "escalated", "issue": issue, "pr": None, "reason": f"scope:{scope}"}

    tests = io.run_tests()
    pr = io.open_pr(branch, pr_title(advisories),
                    pr_body(issue, advisories, remaining, changed, fix_path, tests, notes))
    # SKIP_LABELS member, applied BEFORE the In review move: keeps scheduler.sh's In-review
    # stages — notably stage_review_triage's comment classifier — off the tracking issue while
    # a human decides on the PR (a "looks good" comment must not dispatch Close/Continue).
    io.add_label(issue, "ready-for-human")
    io.move_to_in_review(issue)
    ci = io.poll_ci(pr)
    merged = False
    if ci == "green" and (cfg["auto_merge"] or io.has_direct_to_pr(issue)):
        merged = bool(io.merge(pr))
        if not merged:
            io.escalate(issue, "merge-failed", h, pr=pr)
    io.comment(issue, ci_comment(pr, ci, merged))
    return {"outcome": fix_path, "issue": issue, "pr": pr, "ci": ci, "merged": merged,
            "remaining": len(remaining)}


# ── Live IO (fixture-tested for the hook path; the rest exercised in manual validation) ──
OWNER = identity.SLUG


def _hooks_sh() -> str:
    """FACTORY_HOOKS_SH override, else the baked image copy entrypoint.sh sources, else
    the copy beside this package (repo checkout / tests)."""
    env = os.environ.get("FACTORY_HOOKS_SH")
    if env:
        return env
    baked = "/opt/dark-factory/scripts/hooks.sh"
    if os.path.isfile(baked):
        return baked
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks.sh")


class LiveIO(_MainRedLiveIO):
    """Reuses main_red_fixer.LiveIO's poll_ci / merge / notify; overrides the rest."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.clone_dir = os.path.abspath(cfg.get("clone_dir") or identity.CLONE_DIR)

    def _sh(self, cmd, timeout=600, stdin=None):
        return subprocess.run(cmd, cwd=self.clone_dir, input=stdin, capture_output=True,
                              text=True, timeout=timeout,
                              env={**os.environ, "CLONE_DIR": self.clone_dir})

    def _hook(self, name, gate):
        flag = "--gate " if gate else ""
        r = self._sh(["bash", "-c", f'source "$1" && run_hook {flag}{name}', "_", _hooks_sh()],
                     timeout=1800)
        return r.returncode, r.stdout or "", r.stderr or ""

    def _hook_present(self, name):
        p = os.path.join(self.clone_dir, ".factory", "hooks", name)
        return os.path.isfile(p) and os.path.getsize(p) > 0  # same "present" test as run_hook

    def audit_hook_present(self):
        return self._hook_present("deps-audit")

    def run_audit(self):
        rc, out, _ = self._hook("deps-audit", gate=True)
        return rc, out

    def run_deterministic_fix(self):
        self._hook("deps-fix", gate=False)  # absent hook → run_hook's default no-op

    def start_branch(self):
        branch = f"deps/audit-{time.strftime('%Y%m%d', time.gmtime())}"
        self._sh(["git", "fetch", "origin", "main"], timeout=120)
        self._sh(["git", "checkout", "-B", branch, "origin/main"])
        # entrypoint.sh's pre-route `npm install` / `pip install` can rewrite a tracked
        # lockfile; without this the audit (and changed_paths) would see that drift as a fix.
        # Untracked, git-excluded run-time files survive a hard reset.
        self._sh(["git", "reset", "--hard", "origin/main"])
        return branch

    def apply_agent_fix(self, prompt):
        r = self._sh(["claude", "-p", "--model", self.cfg["model"],
                      "--allowedTools", "Edit,Write,Read,Bash,Grep,Glob"],
                     stdin=prompt, timeout=self.cfg.get("agent_timeout", 1200))
        if r.returncode != 0:
            # A dead session (usage window, auth, crash) is not "no fix exists": do not
            # label the issue ready-for-human for it. main_once still saves the attempt.
            raise RuntimeError(f"fix agent exited {r.returncode}: {(r.stderr or '')[-300:].strip()}")
        path = os.path.join(self.clone_dir, NOTES_FILE)
        try:
            with open(path) as f:
                notes = f.read()
            os.remove(path)
        except OSError:
            notes = ""
        return notes

    def changed_paths(self):
        self._sh(["git", "add", "-A"])
        # --no-renames: a rename must list BOTH paths, or `git mv <ci workflow> <allowed name>`
        # would pass the scope check as a single allowed path.
        r = self._sh(["git", "diff", "--cached", "--name-only", "--no-renames", "origin/main", "--"])
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]

    def run_tests(self):
        if not self._hook_present("validate"):
            return "No .factory/hooks/validate hook — no local test run; CI is the gate."
        rc, out, err = self._hook("validate", gate=True)
        return f"validate hook exit={rc}\n{(out + err).strip()}"

    def _list_open(self, extra):
        # --author @me: only issues the factory's own account opened. The repo may be public,
        # and anyone can open an issue whose body starts with the marker; adopting it would
        # overwrite its body, label it, board it, and close it on the next clean audit.
        r = self._sh(["gh", "issue", "list", "--repo", OWNER, "--state", "open",
                      "--author", "@me", *extra,
                      "--limit", "500", "--json", "number,body"], timeout=60)
        try:
            items = json.loads(r.stdout or "[]")
        except Exception:
            items = []
        # startswith, not `in`: an issue that merely quotes the marker is not ours to edit or close
        return sorted(i["number"] for i in items if (i.get("body") or "").startswith(TRACKING_MARKER))

    def open_tracking_issues(self):
        # Cheap labelled lookup first; the unfiltered fallback still finds an issue that was
        # created without labels (repo lacked them — see find_or_create_tracking_issue).
        return self._list_open(["--label", "dependencies"]) or self._list_open([])

    def find_or_create_tracking_issue(self, advisories, h):
        from .providers import get_tracker
        body = tracking_issue_body(advisories, h)
        open_ = self.open_tracking_issues()
        if open_:
            n = open_[0]
            self._sh(["gh", "issue", "edit", str(n), "--repo", OWNER, "--body", body], timeout=60)
            return n
        title = f"deps: {len(advisories)} dependency advisories on main"
        num = (get_tracker().create_item(title, body, ["dependencies", "security"])
               or get_tracker().create_item(title, body))  # labels missing in repo → retry bare
        return int(num) if str(num).isdigit() else None

    def set_done(self, issue):
        from .providers import get_tracker
        get_tracker().set_status(str(issue), "done")  # not on the board → logged no-op

    def close_issue(self, issue, comment):
        from .providers import get_tracker
        get_tracker().resolve_item(str(issue), comment=comment)

    def open_pr(self, branch, title, body):
        from .providers import get_codehost
        self._sh(["git", "add", "-A"])
        commit = self._sh(["git", "commit", "-m", title])
        if commit.returncode != 0:
            raise RuntimeError(f"git commit failed on {branch}: {(commit.stderr or commit.stdout).strip()}")
        push = self._sh(["git", "push", "-u", "origin", branch, "--force-with-lease"])
        if push.returncode != 0:
            raise RuntimeError(f"git push failed for {branch}: {push.stderr.strip()}")
        pr_str = get_codehost().open_change(branch, "main", title, body, draft=False, repo=OWNER)
        if not pr_str.isdigit():
            raise RuntimeError(f"could not parse PR number from gh output: {pr_str}")
        return int(pr_str)

    def move_to_in_review(self, issue):
        from . import board
        from .providers import get_tracker
        board.add_to_board(issue, f"https://github.com/{OWNER}/issues/{issue}")
        get_tracker().set_status(str(issue), "in_review")

    def _labels(self, issue):
        r = self._sh(["gh", "issue", "view", str(issue), "--repo", OWNER,
                      "--json", "labels", "--jq", ".labels[].name"], timeout=60)
        return (r.stdout or "").split()

    def has_direct_to_pr(self, issue):
        return "direct-to-pr" in self._labels(issue)

    def is_halted(self, issue):
        return "needs-discussion" in self._labels(issue)

    def add_label(self, issue, label):
        from .providers import get_tracker
        get_tracker().add_label(str(issue), label)

    def comment(self, issue, body):
        self._sh(["gh", "issue", "comment", str(issue), "--repo", OWNER, "--body", body],
                 timeout=60)

    def escalate(self, issue, reason, h, pr=None):
        pr_txt = f" PR #{pr} left open for review." if pr else ""
        self.add_label(issue, "ready-for-human")
        self.comment(issue, f"\U0001f6e1️ **Dependency-Health Loop** — escalating to a human "
                            f"(reason: {reason}).{pr_txt}\n\n{footer()}")
        self.notify("Dependency-health loop needs a human",
                    f"Tracking issue #{issue}: {reason}.{pr_txt}", "warning", f"deps-audit-{h}")


def main_once() -> int:
    state_dir = os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory")
    path = os.path.join(state_dir, "deps-fixer-state.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        state = {}
    clone_dir = os.path.abspath(os.environ.get("CLONE_DIR") or identity.CLONE_DIR)
    cfg = dict(
        clone_dir=clone_dir,
        max_attempts=int(os.environ.get("DEPS_AUDIT_MAX_ATTEMPTS", "3")),
        model=os.environ.get("DEPS_AUDIT_MODEL", "claude-opus-5-5"),
        ci_wait_minutes=int(os.environ.get("DEPS_AUDIT_CI_WAIT_MINUTES", "20")),
        auto_merge=os.environ.get("DEPS_AUDIT_AUTO_MERGE", "false") == "true",
        agent_timeout=1200,
        allowed_paths=_deps_allowed_paths(clone_dir),
        blocked_paths=list(BLOCKED_PATHS))
    try:
        out = run_once(cfg, LiveIO(cfg), state)
    finally:
        try:
            with open(path, "w") as f:
                json.dump(state, f)
        except Exception:
            pass
    print(f"deps_fixer={out['outcome']} issue=#{out.get('issue')} pr=#{out.get('pr')}")
    return 0

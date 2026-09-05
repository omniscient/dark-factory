"""Side-effect levels 1-6 and their enforced permission profiles (#196).

Single source of truth for level semantics: the DAG's denied_tools (Layer A), the
git/gh shim (Layer B), the run record (Layer C), handoff.py's target-definable check,
and a future loop runner all read this module; nothing re-declares the table.

Levels 1-5 have a profile (this module's job); level 6 is rejected at adapter/manifest
validation (#196 R2) and has no profile here — see adapter.py / handoff.py.

R8.1 (Agent-tool subagents inherit denied_tools): verified in Task 0 of the #196 plan
before this module was written, via the Claude Agent SDK's own type declarations
(sdk.d.ts AgentDefinition.tools: "If omitted, inherits all tools from parent" — a
built-in Task-tool subagent without its own tools/disallowedTools override inherits
the parent session's already-disallowedTools-filtered tool list). A live probe attempt
in the planning sandbox was blocked (nested `claude` subprocess exits immediately), so
source inspection was used instead — same method as R8.2 below.
R8.2 (the CLAUDECODE discriminator the shim in scripts/shims/ relies on) was verified by
direct inspection of the factory image's own archon source (provider.ts / dag-executor.ts)
plus a live env check — see the same task.
"""
import argparse
import dataclasses
import json
import os
import sys

LEVELS = {
    1: "read-only research",
    2: "artifact writing",
    3: "GitHub ticket creation",
    4: "code modification",
    5: "PR creation",
    6: "external production side effect",
}

# Owned by #193, enforced here. verifier.py keeps its own literal 4 (Blast-Radius
# hotspot, not touched by this ticket); tests/test_side_effect.py pins the two constants
# together so they cannot silently drift apart.
FACTORY_OWNED_MIN_LEVEL = 4
TARGET_DEFINABLE_MAX_LEVEL = FACTORY_OWNED_MIN_LEVEL - 1


@dataclasses.dataclass(frozen=True)
class Profile:
    """One level's enforced permission profile.

    denied_tools / git_denied / gh_allowed / gh_denied / profile_version are the four
    fields R1 names explicitly. push_scope, gh_mode, git_mode and git_allowed are this
    module's own additions, needed because two things R1's flat verb-list phrasing
    can't express on its own:

    1. R1's level-4/5 git-push rule ("own branch only", "never force/delete", "never
       main") isn't a flat verb string -- push_scope carries it.
    2. R5 requires *fail-closed* enforcement -- "unknown verbs at levels 1-3 are
       denied" -- but R1's level-1 git_denied list (commit/push/tag/remote add|set-url)
       is not exhaustive: git has other local-mutating verbs (checkout, reset, clean,
       stash, config, apply, merge, rebase, ...) R1 never names. A pure deny-list would
       let all of those through at level 1, silently violating "read-only research" and
       R5's fail-closed clause in the same stroke -- a gap caught in architect review
       (cycle 2) before any code shipped. gh already needed an allow/deny mode switch
       (gh_mode) for exactly this reason; git_mode extends the same fix to git, but
       *only* at level 1: levels 2-3 ("artifact writing" / "GitHub ticket creation")
       explicitly permit ordinary local git usage (add, commit, checkout -b, stash) and
       only forbid the enumerated remote-facing verbs, so gating them the same way
       level 1 is gated would break the writing/ticket-creation phases R1 says must
       work. Only level 1's name and net-effect text ("can read and run read-only
       commands") unambiguously call for allow-list semantics.

    git_denied / gh_denied / gh_allowed / git_allowed entries are verb strings: a
    single word matches the command's first argument only (e.g. "push" matches `git
    push ...`, "secret" matches `gh secret <anything>`); a two-word string matches the
    first two arguments exactly (e.g. "remote add" matches `git remote add ...` but not
    `git remote -v`). "api:GET" / "api:non-GET" / "api:DELETE" are sentinels the gh
    shim special-cases (they depend on flag parsing, not verb position). Deny checks
    always run before an allow-list gate, so e.g. level 1's specific "remote add"/
    "remote set-url" denials take precedence over "remote" being read-permitted, and the
    never-list (present in gh_denied at every level) is checked before gh_allowed.
    """
    level: int
    name: str
    denied_tools: list
    git_denied: list
    push_scope: str   # "denied" | "own_branch_only" | "unrestricted"
    git_mode: str     # "allow" (deny by default; only git_allowed passes, after git_denied is checked) | "deny" (allow by default; only git_denied blocks)
    git_allowed: list
    gh_mode: str      # "allow" (deny by default; only gh_allowed passes) | "deny" (allow by default; only gh_denied blocks)
    gh_allowed: list
    gh_denied: list
    profile_version: str = "v1"


# R1 (amended after plan review): the level-5 never-list is denied at EVERY level and is
# checked before any allow-list, so level 1's "everything except view/list/status/search"
# does not admit `gh secret list` / `gh auth status` through the VERB2-alone read match.
_GH_NEVER = ["repo delete", "repo archive", "repo rename", "secret", "auth",
             "ssh-key", "gpg-key", "api:DELETE"]
_GIT_NEVER = ["push --delete", "push :refspec-delete"]
_READ_VERBS = ["view", "list", "status", "search", "api:GET"]

# Level 1 only (see the Profile docstring for why levels 2-3 stay deny-list-only):
# the read-only verbs a "read-only research" agent may run. "remote" is allowed here
# as a bare top-level verb (git remote -v / show); its "add"/"set-url" sub-verbs are
# still blocked because git_denied's specific two-word checks run first.
_GIT_READ_VERBS = ["log", "status", "diff", "show", "branch", "remote", "fetch",
                   "blame", "describe", "rev-parse", "symbolic-ref", "ls-files",
                   "ls-tree", "cat-file", "shortlog", "reflog", "grep", "help",
                   "version"]

_PROFILES = {
    1: Profile(
        level=1, name=LEVELS[1],
        denied_tools=["Write", "Edit", "MultiEdit", "NotebookEdit"],
        git_denied=["commit", "push", "tag", "remote add", "remote set-url"],
        push_scope="denied",
        git_mode="allow", git_allowed=list(_GIT_READ_VERBS),
        gh_mode="allow", gh_allowed=list(_READ_VERBS), gh_denied=list(_GH_NEVER),
    ),
    2: Profile(
        level=2, name=LEVELS[2],
        denied_tools=[],
        git_denied=["push", "tag", "remote set-url"],
        push_scope="denied",
        git_mode="deny", git_allowed=[],
        gh_mode="allow", gh_allowed=list(_READ_VERBS), gh_denied=list(_GH_NEVER),
    ),
    3: Profile(
        level=3, name=LEVELS[3],
        denied_tools=[],
        git_denied=["push", "tag", "remote set-url"],
        push_scope="denied",
        git_mode="deny", git_allowed=[],
        gh_mode="allow",
        gh_allowed=list(_READ_VERBS) + ["issue create", "issue comment", "issue edit"],
        gh_denied=list(_GH_NEVER),
    ),
    4: Profile(
        level=4, name=LEVELS[4],
        denied_tools=[],
        git_denied=[],
        push_scope="own_branch_only",
        git_mode="deny", git_allowed=[],
        gh_mode="deny",
        gh_allowed=[],
        gh_denied=["pr create", "pr merge", "pr ready", "pr review", "pr close",
                   "release", "repo", "secret", "auth", "api:non-GET"]
                  + ["ssh-key", "gpg-key", "api:DELETE"],  # rest of the never-list
    ),
    5: Profile(
        level=5, name=LEVELS[5],
        denied_tools=[],
        git_denied=list(_GIT_NEVER),
        push_scope="unrestricted",
        git_mode="deny", git_allowed=[],
        gh_mode="deny",
        gh_allowed=[],
        gh_denied=list(_GH_NEVER),
    ),
}


def effective_level(value) -> int:
    """D4: None, non-int, bool, or out of the profiled 1-5 range -> 1 (fail closed)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    if not (1 <= value <= 5):
        return 1
    return value


def profile_for(level: int) -> Profile:
    """Levels 1-5 only. Callers pass an already-normalized level: profile_for(effective_level(x))."""
    if level not in _PROFILES:
        raise ValueError(f"no profile for level {level} (valid: 1-5; level 6 is rejected at validation)")
    return _PROFILES[level]


# Keyed on entrypoint.sh's own $INTENT vocabulary (entrypoint.sh:87-91: fix, continue,
# close, refine, plan, deconflict, recheck, fix-main), NOT the DAG's parse-intent
# vocabulary (new, continue, close, refine, plan, resolve) -- intent_phases() is called
# from entrypoint.sh BEFORE archon/parse-intent ever runs, on the raw regex-parsed
# $INTENT. entrypoint.sh:701's comment gives the correspondence for a human reader
# ("fix (new)... deconflict (resolve)"); it is not a second vocabulary this function
# should accept. "recheck" is intentionally absent (defaults to [] -> level 1): it exits
# after the smoke-gate check (entrypoint.sh:713) and never reaches archon, so no Claude
# session or git/gh call is ever made under it. "deconflict" and "fix-main" both resolve
# inside entrypoint.sh's own bash logic without going through archon, but BOTH start a
# live `claude -p` session whose Bash-tool subprocesses carry CLAUDECODE=1 and therefore
# hit the shim (F10): deconflict.py:143 (the AI conflict-resolution tier, on by default
# via CONFLICT_RESOLUTION_AI_TIER) and main_red_fixer.py:190 (the main-red fix agent).
# Both are mapped to a level-5 phase set (D2's "no regression" default; spec R3) -- NOT
# left at level 1's fail-closed default, which would deny those sessions' local git work.
_INTENT_PHASES = {
    "refine": ["refine"],
    "plan": ["plan"],
    "fix": ["implement", "validate", "conformance", "code-review", "revise-advisory"],
    "continue": ["implement", "validate", "conformance", "code-review", "revise-advisory"],
    "deconflict": ["deconflict"],
    "fix-main": ["fix-main"],
    "close": [],
}


def intent_phases(intent: str) -> list:
    """The phase set a run container executes for this intent (unknown intent -> [])."""
    return list(_INTENT_PHASES.get(intent, []))


def phase_level(phase: str, config_path, env=None) -> int:
    """Resolve one phase's configured level: SIDE_EFFECT_LEVEL_<PHASE> env override,
    else config.yaml's side_effect.phase_levels[<phase, hyphens->underscores>], else 1
    (fail closed, with a stderr warning) per D4/R3."""
    env = os.environ if env is None else env
    key = phase.replace("-", "_")
    override = env.get(f"SIDE_EFFECT_LEVEL_{key.upper()}")
    if override is not None:
        try:
            return effective_level(int(override))
        except ValueError:
            print(f"side_effect: invalid SIDE_EFFECT_LEVEL_{key.upper()}={override!r} — ignoring",
                  file=sys.stderr)

    cfg = {}
    if config_path and os.path.isfile(config_path):
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"side_effect: cannot read {config_path}: {exc}", file=sys.stderr)

    raw = ((cfg.get("side_effect") or {}).get("phase_levels") or {}).get(key)
    if raw is None:
        print(f"side_effect: no configured level for phase '{phase}' — defaulting to 1",
              file=sys.stderr)
    return effective_level(raw)


def _profile_json(level: int) -> dict:
    # No effective_level() normalization here (F14): `render` is what the shim calls with
    # the raw env value, and a bad level must surface as an error (-> shim denies, fail
    # closed), never as level 1's profile rendered under the wrong number.
    return dataclasses.asdict(profile_for(level))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Side-effect level semantics (#196)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="Print a level's profile as JSON")
    r.add_argument("--level", type=int, required=True)

    ip = sub.add_parser("intent-phases", help="Print the phase list for an intent")
    ip.add_argument("--intent", required=True)

    pl = sub.add_parser("phase-level", help="Resolve one phase's configured level")
    pl.add_argument("--phase", required=True)
    pl.add_argument("--config", default=None)

    ecl = sub.add_parser("effective-container-level",
                          help="Max configured level over an intent's phase set")
    ecl.add_argument("--intent", required=True)
    ecl.add_argument("--config", default=None)

    args = p.parse_args(argv)
    if args.cmd == "render":
        print(json.dumps(_profile_json(args.level)))
    elif args.cmd == "intent-phases":
        print(" ".join(intent_phases(args.intent)))
    elif args.cmd == "phase-level":
        print(phase_level(args.phase, args.config))
    elif args.cmd == "effective-container-level":
        phases = intent_phases(args.intent)
        if not phases:
            print(1)
        else:
            print(max(phase_level(ph, args.config) for ph in phases))


if __name__ == "__main__":
    main()

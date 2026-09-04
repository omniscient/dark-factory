# Side-effect levels 1–6 with per-level enforced permission profiles (A2)

**Issue:** #196

## Goal

Turn `side_effect_level` (1–6, already validated by #195/#301's adapter schema) from a
declared-but-unenforced integer into an actually-applied permission boundary on every
factory run container: which Claude tools a phase agent is offered (Layer A), which
`git`/`gh` verbs its Bash-tool subprocesses may execute (Layer B), and an audit trail
recording the profile that applied (Layer C). Level 6 (external production side effect) is
rejected at adapter/manifest validation — out of scope for v1 per #194. All seven existing
Claude phase-agent nodes (`refine`, `plan`, `implement`, `validate`, `conformance`,
`code-review`, `revise-advisory`) are declared at level 5 in v1, matching today's
unrestricted behavior exactly (no regression) — tightening any phase to level 4 is a
separate, later, reviewed change (F2).

**Plan-level addition beyond the spec's literal enumeration:** R1/R3/R4's text names six
phases (`refine, plan, implement, validate, conformance, code-review`); `revise-advisory`
(`workflows/archon-dark-factory.yaml:1192`, `command: dark-factory-revise-advisory`) is a
seventh `command:` node — a Claude phase agent that edits code to auto-address advisory
code-review findings — that the spec's enumeration omits. This plan adds it everywhere the
spec lists the six, applying D2's already-decided principle ("all current factory phases
start at level 5, no regression") symmetrically rather than leaving one current phase
undeclared and undetectable. This is purely additive (level 5 removes no tools either
way) and narrows a gap rather than widening any permission surface, so it does not need a
fresh human-reviewed spec under `CLAUDE.md`'s tool-allow/deny-surface rule — but it is
flagged here explicitly, once, so the conformance reviewer and any human reader can see
exactly where the plan diverges from the spec's literal text and why.

## Architecture

**Layer A (tool removal).** `side_effect.py` (new, the single source of truth for level
semantics) computes a `Profile` per level. The seven phase-agent DAG nodes in
`workflows/archon-dark-factory.yaml` each get an explicit `denied_tools:` key set to that
profile's `denied_tools` list — at level 5 this is `[]`, explicit so its absence is
detectable by a DAG test. Archon's Claude provider already maps a node's `denied_tools` to
the Claude Agent SDK's `disallowedTools` (`provider.ts:376-378`), which the SDK enforces
under `bypassPermissions` (confirmed: this is not a permission-prompt mechanism, so
`bypassPermissions` does not bypass it).

**Layer B (git/gh command shim).** New `scripts/shims/git` and `scripts/shims/gh` — bash,
no dependencies — are prepended onto `PATH` for the `archon workflow run` invocation only.
Each shim activates *only* when both `FACTORY_SIDE_EFFECT_LEVEL` and `CLAUDECODE=1` are
present in its own process environment (verified in Task 0 to be the exact, and only,
discriminator between a phase agent's own Bash-tool subprocess and one of archon's DAG
`bash:` nodes, which run the *same* `PATH` but never carry `CLAUDECODE`). When active, the
shim resolves the real binary (skipping its own directory on `PATH`), calls
`side_effect.py render` once to get the level's profile as JSON, and either execs the real
binary or exits 1 with a denial message — after best-effort recording a
`side_effect.denied` health event.

**Layer C (audit).** `run_record.py`'s per-run record (both the `record` and `assemble`
subcommands, which are the two code paths that append to `runs.jsonl`) gains
`side_effect_level` and `side_effect_profile` fields, populated from
`FACTORY_SIDE_EFFECT_LEVEL` / `FACTORY_SIDE_EFFECT_PROFILE_VERSION` via `entrypoint.sh`,
defaulting to `1` / `"unknown"` when absent (fail closed — a row can never claim a wider
profile than it can prove).

**Config.** `config/config.yaml` gains a `side_effect.phase_levels` map (all seven phases +
`deconflict` at `5`), with a `SIDE_EFFECT_LEVEL_<PHASE>` env override per phase.
`entrypoint.sh` computes the container's *effective* level as the max over the phase set
its `INTENT` dispatches (`side_effect.py intent-phases`), before `archon workflow run`
starts, and exports it once for the whole run.

## Tech Stack

Python 3.12 (`scripts/factory_core/`), bash (`scripts/shims/`, `entrypoint.sh`), YAML
(`workflows/archon-dark-factory.yaml`, `config/config.yaml`), pytest (`tests/*.py`), bash
test harness (`tests/*.sh`, pattern: `tests/test_hooks.sh` / `tests/test_smoke_gate.sh`).

## File Structure

| Path | Change |
|---|---|
| `scripts/factory_core/side_effect.py` | New — levels, `Profile`, `profile_for`, `effective_level`, `intent_phases`, `phase_level`, CLI |
| `scripts/shims/git` | New — Layer B git verb shim |
| `scripts/shims/gh` | New — Layer B gh verb shim |
| `tests/test_side_effect.py` | New — table, D4 default, verifier-constant pin |
| `tests/test_side_effect_dag.py` | New — R4 DAG `denied_tools` assertions |
| `tests/test_side_effect_shims.sh` | New — R5 shim matrix |
| `scripts/factory_core/adapter.py` | R2 — level 6 rejected (1–5 range + dedicated message) |
| `scripts/factory_core/handoff.py` | R2 — manifest range 1–5; R7 — import `FACTORY_OWNED_MIN_LEVEL` from `side_effect` |
| `scripts/factory_core/run_record.py` | R6 — `side_effect_level`/`side_effect_profile` fields + CLI flags |
| `entrypoint.sh` | R3 — compute/export/log; R5 — `PATH` prepend + plumb new flags |
| `workflows/archon-dark-factory.yaml` | R4 — `denied_tools: []` on 6 phase nodes |
| `config/config.yaml` | R3 — `side_effect.phase_levels` block |
| `docs/adapter-authoring-guide.md` | R7 — "Side-effect levels" section |
| `.github/workflows/ci.yml` | Wire `test_side_effect_shims.sh` into `tests:` job |
| `tests/test_adapter.py` | Update fixtures for the 1–5 range (Task 2) |
| `tests/test_handoff.py` | Add level-6-rejection test (Task 3) |
| `tests/test_run_record.py` | Add `side_effect_level`/`side_effect_profile` tests (Task 4) |

## Process note (adapter.py is a Blast-Radius hotspot)

`scripts/factory_core/adapter.py` is flagged by `gate_blast_radius.py`
(`hotspot_score_floor: 5.0` in `config/config.yaml`). Per the spec, this PR takes the
**operator-review path** used for #197/#198: operator review stands in for conformance +
Gate 3, and the PR is merged by hand rather than through the automated `push-and-pr` →
`conformance-gate` → `review-gate` → auto-merge chain. Flag this explicitly in the PR
description at implementation time.

---

## Task 0 — Pre-implementation verification (R8)

No code changes. This task's job is to make sure the plan's Task 6 (the shim) rests on a
verified foundation, per the spec's explicit instruction: *"If this discriminator does not
hold, stop and amend this spec — do not invent another one in the plan."*

**R8.2 (the `CLAUDECODE` discriminator) — already verified during planning, by direct
inspection of the factory image's own archon source and a live check:**

- A live `env | grep CLAUDECODE` inside this planning session's own Bash tool (itself a
  Claude Code Bash-tool subprocess) printed `CLAUDECODE=1`.
- `/opt/archon/packages/providers/src/claude/provider.ts:143-149` (`buildFirstEventHangDiagnostics`)
  and its neighboring comment (`buildSubprocessEnv`, ~line 85-99: *"process.env is already
  clean at this point: stripCwdEnv() at entry point removed CWD .env keys + CLAUDECODE
  markers"*) confirm archon strips `CLAUDECODE` from its own process env at startup, and
  the Claude subprocess env it builds for `command:` nodes is `{ ...process.env }` — i.e.
  archon never sets `CLAUDECODE` itself; the `claude` CLI binary sets it for its own process
  (and therefore for the Bash tool's child processes) once it starts, which is exactly what
  the live check above observed.
- `/opt/archon/packages/workflows/src/dag-executor.ts:1321-1327` (bash node execution):
  `subprocessEnv = { ...process.env, ARTIFACTS_DIR, LOG_DIR, BASE_BRANCH, ...envVars }`,
  passed to `execFileAsync('bash', ['-c', finalScript], { cwd, timeout, env: subprocessEnv })`
  — a plain `bash -c`, never the `claude` binary, and no `CLAUDECODE` key is ever added.

**Conclusion: the discriminator holds.** `command:` nodes' Bash-tool subprocesses carry
`CLAUDECODE=1`; `bash:` nodes never do. Proceed with Task 6 as designed.

**R8.3 (never-list verbs unused today) — already verified:**

```bash
grep -nE "gh (repo (delete|archive|rename)|secret|auth|ssh-key|gpg-key|api -X DELETE)|git push .*(--delete|:.*)" \
  workflows/archon-dark-factory.yaml commands/*.md
```

returned nothing. The level-5 never-list matches nothing the DAG or `commands/*.md` do
today — level 5 (all current phases) cannot regress.

**R8.4 (GitHub calls all shell out through `gh`) — already verified:**

```bash
grep -n "subprocess\|requests\.\|httpx\|urllib" scripts/factory_core/providers/tracker/github.py \
  scripts/factory_core/providers/codehost/github.py
```

Every call in both files is `subprocess.run(["gh", ...])`. A broader repo grep for
`api.github.com` / direct HTTP clients found only the Jira tracker adapter (a different
tracker, not in play for this GitHub-hosted repo) and non-GitHub endpoints (Seq logging,
the internal model proxy) — no direct-HTTPS shim-bypass exists today. **F5 (shim-bypass
follow-up) has nothing to list.**

**R8.1 (disallowedTools removal applies to Agent-tool subagents) — NOT yet verified; this
plan's scope boundary (docs-only) prohibits running a live DAG node from the plan phase.
This is the literal first step of Task 1's implementation, run before any other code
changes:**

1. Add a temporary scratch node to a throwaway local copy of the workflow (not committed):
   `{ id: probe, command: some-harmless-command, denied_tools: [Write] }`, or more directly,
   from a local shell with archon available: create a one-node scratch workflow file under
   `/tmp` (outside the repo, never committed) with a `command:` node declaring
   `denied_tools: [Write]`, and a prompt instructing the agent to (a) attempt to report
   whether `Write` is in its own tool list, then (b) spawn a subagent via the `Agent` tool
   and have the subagent report the same. Run it with
   `archon workflow run <scratch> --cwd /tmp/scratch --no-worktree "probe"`.
2. Record the observed result (Write absent for both the top-level agent and the subagent,
   or not) as a one-line comment at the top of `scripts/factory_core/side_effect.py`
   pointing at this task.
3. **If `Write` is NOT removed for the top-level agent**: stop — Layer A does not work as
   designed; escalate (do not silently fall back to some other mechanism the spec didn't
   choose).
4. **If `Write` is removed for the top-level agent but NOT for the subagent**: this is a
   narrower gap than a full stop-and-amend, since Task 9's seven phase nodes rarely spawn
   subagents mid-node in a way that matters for `Write`/`Edit`/`MultiEdit`/`NotebookEdit`
   removal specifically (only level 1 removes any tools at all, and level 1 is not assigned
   to any phase in v1) — record the gap as a new line in the spec's "Known limitations and
   follow-ups" section (a small doc edit, not a design change) and proceed.

---

## Task 1 — `side_effect.py`: level semantics and the profile table (R1, D4)

### Step 1.1 — failing test

Create `tests/test_side_effect.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import side_effect


def test_levels_table_has_six_entries():
    assert set(side_effect.LEVELS) == {1, 2, 3, 4, 5, 6}
    assert side_effect.LEVELS[1] == "read-only research"
    assert side_effect.LEVELS[6] == "external production side effect"


@pytest.mark.parametrize("value,expected", [
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
    (6, 1), (7, 1), (0, 1), (-1, 1), (None, 1), ("2", 1), (True, 1), (False, 1),
])
def test_effective_level_d4_default(value, expected):
    assert side_effect.effective_level(value) == expected


def test_profile_for_level_1_removes_write_tools():
    p = side_effect.profile_for(1)
    assert p.denied_tools == ["Write", "Edit", "MultiEdit", "NotebookEdit"]
    assert p.gh_mode == "allow"
    assert "api:GET" in p.gh_allowed
    assert "view" in p.gh_allowed and "list" in p.gh_allowed
    assert "remote add" in p.git_denied and "remote set-url" in p.git_denied
    assert p.push_scope == "denied"
    # R5 fail-closed: level 1 must be allow-list, not deny-list, for git too — a git
    # verb R1's table never names (checkout/reset/clean/stash/config/apply/...) must
    # be denied by default, not silently pass. Caught in architect review cycle 2.
    assert p.git_mode == "allow"
    assert "log" in p.git_allowed and "status" in p.git_allowed
    assert "checkout" not in p.git_allowed and "reset" not in p.git_allowed


def test_profile_for_level_2_writes_locally_only():
    p = side_effect.profile_for(2)
    assert p.denied_tools == []
    assert "push" in p.git_denied and "tag" in p.git_denied
    assert "remote add" not in p.git_denied  # level 1 only
    # Levels 2-3 stay deny-list for git (ordinary local writes -- add/commit/checkout -b
    # -- must keep working; only the enumerated remote-facing verbs are denied).
    assert p.git_mode == "deny"
    assert p.gh_mode == "allow"
    assert "issue create" not in p.gh_allowed  # level 3 only


def test_profile_for_level_3_allows_issue_ops_only():
    p = side_effect.profile_for(3)
    assert p.gh_mode == "allow"
    for verb in ("issue create", "issue comment", "issue edit"):
        assert verb in p.gh_allowed
    assert "pr create" not in p.gh_allowed


def test_profile_for_level_4_own_branch_push_only():
    p = side_effect.profile_for(4)
    assert p.denied_tools == []
    assert p.push_scope == "own_branch_only"
    assert p.gh_mode == "deny"
    for verb in ("pr create", "pr merge", "pr ready", "pr review", "pr close",
                 "release", "repo", "secret", "auth"):
        assert verb in p.gh_denied
    assert "api:non-GET" in p.gh_denied


def test_profile_for_level_5_never_list_only():
    p = side_effect.profile_for(5)
    assert p.denied_tools == []
    assert p.push_scope == "unrestricted"
    assert p.git_denied == ["push --delete", "push :refspec-delete"]
    assert p.gh_mode == "deny"
    for verb in ("repo delete", "repo archive", "repo rename", "secret", "auth",
                 "ssh-key", "gpg-key"):
        assert verb in p.gh_denied
    assert "api:DELETE" in p.gh_denied
    assert "pr create" not in p.gh_denied  # level 5 explicitly allows PR creation


def test_profile_for_level_6_raises():
    with pytest.raises(ValueError):
        side_effect.profile_for(6)


def test_profile_for_out_of_range_raises():
    with pytest.raises(ValueError):
        side_effect.profile_for(0)


def test_factory_owned_min_level_pins_to_verifier():
    from factory_core import verifier
    assert side_effect.FACTORY_OWNED_MIN_LEVEL == verifier._FACTORY_OWNED_MIN_LEVEL


def test_target_definable_max_level():
    assert side_effect.TARGET_DEFINABLE_MAX_LEVEL == 3
    assert side_effect.TARGET_DEFINABLE_MAX_LEVEL == side_effect.FACTORY_OWNED_MIN_LEVEL - 1


@pytest.mark.parametrize("intent,phases", [
    ("refine", ["refine"]),
    ("plan", ["plan"]),
    # entrypoint.sh's own $INTENT vocabulary (entrypoint.sh:87: fix|continue|close|refine|
    # plan|deconflict|recheck, plus the fix-main special case) -- NOT the DAG's
    # parse-intent vocabulary (new|continue|close|refine|plan|resolve). entrypoint.sh:701's
    # own comment draws the correspondence explicitly ("fix (new)... deconflict
    # (resolve)"), but intent_phases() is fed entrypoint.sh's raw $INTENT (it runs before
    # archon/parse-intent ever executes), so it must be keyed on THAT vocabulary. Using
    # "new"/"resolve" here (the spec's own R3 prose, which reused the DAG's naming without
    # the distinction) would make the actual `fix`/`deconflict` intents fall through to
    # intent_phases' [] default -> level 1 -> the shim denying every git/gh call the
    # implement/deconflict phases need. Caught in architect review, cycle 3.
    ("fix", ["implement", "validate", "conformance", "code-review", "revise-advisory"]),
    ("continue", ["implement", "validate", "conformance", "code-review", "revise-advisory"]),
    ("deconflict", ["deconflict"]),
    ("fix-main", ["fix-main"]),
    ("close", []),
    ("recheck", []),  # entrypoint.sh:713: exits after the smoke-gate check, never reaches archon
    ("unknown-intent", []),
])
def test_intent_phases(intent, phases):
    assert side_effect.intent_phases(intent) == phases


def test_phase_level_reads_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    plan: 4\n")
    assert side_effect.phase_level("plan", str(cfg), env={}) == 4


def test_phase_level_env_override(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    plan: 5\n")
    assert side_effect.phase_level(
        "plan", str(cfg), env={"SIDE_EFFECT_LEVEL_PLAN": "2"}) == 2


def test_phase_level_missing_config_defaults_to_1(tmp_path, capsys):
    assert side_effect.phase_level("plan", str(tmp_path / "missing.yaml"), env={}) == 1
    assert "defaulting to 1" in capsys.readouterr().err


def test_phase_level_hyphenated_phase_name(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("side_effect:\n  phase_levels:\n    code_review: 4\n")
    assert side_effect.phase_level("code-review", str(cfg), env={}) == 4


def test_render_cli_prints_json(tmp_path, capsys):
    side_effect.main(["render", "--level", "4"])
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    assert data["level"] == 4
    assert data["push_scope"] == "own_branch_only"
    assert data["profile_version"] == "v1"


def test_effective_container_level_cli_is_max_over_phases(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "side_effect:\n  phase_levels:\n"
        "    implement: 4\n    validate: 5\n    conformance: 3\n    code_review: 4\n"
    )
    side_effect.main(["effective-container-level", "--intent", "fix", "--config", str(cfg)])
    assert capsys.readouterr().out.strip() == "5"


def test_effective_container_level_empty_phase_set_defaults_to_1(tmp_path, capsys):
    side_effect.main(["effective-container-level", "--intent", "close"])
    assert capsys.readouterr().out.strip() == "1"
```

### Step 1.2 — verify failure

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect.py -v
```

Expected: `ModuleNotFoundError: No module named 'factory_core.side_effect'` (or collection
error) — confirms the test fails for the right reason.

### Step 1.3 — implement

Create `scripts/factory_core/side_effect.py`:

```python
"""Side-effect levels 1-6 and their enforced permission profiles (#196).

Single source of truth for level semantics: the DAG's denied_tools (Layer A), the
git/gh shim (Layer B), the run record (Layer C), handoff.py's target-definable check,
and a future loop runner all read this module; nothing re-declares the table.

Levels 1-5 have a profile (this module's job); level 6 is rejected at adapter/manifest
validation (#196 R2) and has no profile here — see adapter.py / handoff.py.

R8.1 (Agent-tool subagents inherit denied_tools): verified in Task 0 of the #196 plan
before this module was written. R8.2 (the CLAUDECODE discriminator the shim in
scripts/shims/ relies on) was verified by direct inspection of the factory image's own
archon source (provider.ts / dag-executor.ts) plus a live env check — see the same task.
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
    "remote set-url" denials take precedence over "remote" being read-permitted.
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


# Spec-faithful non-monotonicity, noted here so it doesn't read as an oversight: R1's
# level-1 wording ("everything except view/list/status/search") literally permits
# `gh secret list` / `gh auth status`, even though level 5's never-list forbids `gh
# secret *` / `gh auth *` outright -- a stricter level (1) can read more gh state than a
# looser one (5) blocks writing to. This is what the spec's table says; not a bug.
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
        gh_mode="allow", gh_allowed=list(_READ_VERBS), gh_denied=[],
    ),
    2: Profile(
        level=2, name=LEVELS[2],
        denied_tools=[],
        git_denied=["push", "tag", "remote set-url"],
        push_scope="denied",
        git_mode="deny", git_allowed=[],
        gh_mode="allow", gh_allowed=list(_READ_VERBS), gh_denied=[],
    ),
    3: Profile(
        level=3, name=LEVELS[3],
        denied_tools=[],
        git_denied=["push", "tag", "remote set-url"],
        push_scope="denied",
        git_mode="deny", git_allowed=[],
        gh_mode="allow",
        gh_allowed=list(_READ_VERBS) + ["issue create", "issue comment", "issue edit"],
        gh_denied=[],
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
                   "release", "repo", "secret", "auth", "api:non-GET"],
    ),
    5: Profile(
        level=5, name=LEVELS[5],
        denied_tools=[],
        git_denied=["push --delete", "push :refspec-delete"],
        push_scope="unrestricted",
        git_mode="deny", git_allowed=[],
        gh_mode="deny",
        gh_allowed=[],
        gh_denied=["repo delete", "repo archive", "repo rename", "secret", "auth",
                   "ssh-key", "gpg-key", "api:DELETE"],
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
# entirely inside entrypoint.sh's own bash logic without going through archon either, but
# "fix-main" does invoke a live, unsandboxed `claude -p` session (main_red_fixer.py:190)
# whose Bash-tool subprocesses DO carry CLAUDECODE -- both are mapped defensively to a
# level-5 phase set (matching D2's "no regression" default) rather than left at level 1's
# fail-closed default, in case a future change makes either path CLAUDECODE-visible.
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
    return dataclasses.asdict(profile_for(effective_level(level)))


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
```

### Step 1.4 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect.py -v
```

Expected: all tests pass.

### Step 1.5 — commit

```bash
git add scripts/factory_core/side_effect.py tests/test_side_effect.py
git commit -m "feat(#196): side_effect.py — level semantics and enforced profiles (R1)"
```

---

## Task 2 — adapter.py: reject level 6 at validation (R2, D1)

### Step 2.1 — failing tests

Edit `tests/test_adapter.py`:

1. Line 565 parametrize: drop `6` (level 6 no longer reaches the budget_caps check — it's
   rejected earlier by the new range check):
   ```python
   @pytest.mark.parametrize("sel", [4, 5])
   def test_side_effect_level_high_without_budget_caps_raises(tmp_path, sel):
   ```
2. `test_side_effect_level_high_with_both_caps_accepted` (line 588): change
   `parsed["loops"][0]["side_effect_level"] = 6` to `= 5`, and the trailing assertion to
   `assert merged["loops"][0]["side_effect_level"] == 5`.
3. The three `match="side_effect_level' must be an int between 1 and 6"` strings (lines
   636, 645, 657) become `"side_effect_level' must be an int between 1 and 5"`.
4. Add a new test for the dedicated level-6 message:
   ```python
   def test_loop_entry_side_effect_level_6_rejected_with_scope_message(tmp_path):
       d = tmp_path / ".factory"; d.mkdir()
       parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
       parsed["loops"][0]["side_effect_level"] = 6
       (d / "adapter.yaml").write_text(yaml.dump(parsed))
       with pytest.raises(
           adapter.AdapterError,
           match=re.escape(
               "side_effect_level 6 (external production side effect) is out of scope "
               "for v1 (#194); declare 1-5"
           ),
       ):
           adapter.load(str(tmp_path))
   ```

### Step 2.2 — verify failure

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py -k side_effect_level -v
```

Expected: the new level-6 test fails (`AdapterError` not raised — level 6 still accepted
by the old 1–6 range check); the edited tests fail on message-text mismatch.

### Step 2.3 — implement

Edit `scripts/factory_core/adapter.py`. Replace the comment block and range check (the
block starting `# side_effect_level scale ... sel = entry["side_effect_level"] ...`):

```python
    # side_effect_level scale (owned by #193; enforced by #196 R4-R6). 1=read-only
    # research, 2=artifact writing, 3=ticket creation, 4=code modification,
    # 5=PR creation, 6=external production side effect. #196/D1 rejects 6 here — out
    # of scope for v1 (#194); A1.5 (#301) left the rejection to this ticket.
    sel = entry["side_effect_level"]
    if isinstance(sel, bool) or not isinstance(sel, int):
        raise AdapterError(
            f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int "
            f"between 1 and 5")
    if sel == 6:
        raise AdapterError(
            f"loops[{index}] ('{name}'): side_effect_level 6 (external production side "
            f"effect) is out of scope for v1 (#194); declare 1-5")
    if not (1 <= sel <= 5):
        raise AdapterError(
            f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int "
            f"between 1 and 5")
```

### Step 2.4 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter.py -v
```

Expected: all tests pass, including the new level-6 test.

### Step 2.5 — commit

```bash
git add scripts/factory_core/adapter.py tests/test_adapter.py
git commit -m "fix(#196): adapter.py rejects side_effect_level 6 at validation (R2/D1)"
```

---

## Task 3 — handoff.py: manifest range 1–5, import `FACTORY_OWNED_MIN_LEVEL` (R2, R7)

### Step 3.1 — failing tests

Edit `tests/test_handoff.py` — add after `test_validate_manifest_rejects_out_of_range_side_effect_level`:

```python
def test_validate_manifest_rejects_level_6_with_out_of_scope_message():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(side_effect_level=6))
    assert exc.value.code == "schema_invalid"
    assert "out of scope for v1" in str(exc.value)
```

### Step 3.2 — verify failure

```bash
PYTHONPATH=scripts python -m pytest tests/test_handoff.py -k side_effect_level -v
```

Expected: the new test fails — `validate_manifest` still accepts level 6 today (raises
nothing).

### Step 3.3 — implement

In `scripts/factory_core/handoff.py`:

1. Add the import (top of file, alongside the existing `from . import ...` block):
   ```python
   from . import side_effect as _side_effect
   ```
2. Find the manifest `side_effect_level` check inside `validate_manifest` (currently
   `if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6): raise
   HandoffError("schema_invalid", "field 'side_effect_level' must be an int between 1 and
   6")`) and replace it:
   ```python
       sel = manifest["side_effect_level"]
       if isinstance(sel, bool) or not isinstance(sel, int):
           raise HandoffError(
               "schema_invalid", "field 'side_effect_level' must be an int between 1 and 5")
       if sel == 6:
           raise HandoffError(
               "schema_invalid",
               "field 'side_effect_level' 6 (external production side effect) is out of "
               "scope for v1 (#194); declare 1-5",
           )
       if not (1 <= sel <= 5):
           raise HandoffError(
               "schema_invalid", "field 'side_effect_level' must be an int between 1 and 5")
   ```
3. In `cross_check`, replace the two `_verifier._FACTORY_OWNED_MIN_LEVEL` references with
   `_side_effect.FACTORY_OWNED_MIN_LEVEL` (the `_verifier` import stays — `cross_check`'s
   surrounding code still uses `_verifier.DEFAULT_TIMEOUT_SECONDS` and
   `_verifier.resolve_and_run` elsewhere in the file):
   ```python
       if declared >= _side_effect.FACTORY_OWNED_MIN_LEVEL:
           raise HandoffError(
               "producing_loop_factory_owned",
               f"loop '{match['name']}' declares side_effect_level {declared} >= "
               f"{_side_effect.FACTORY_OWNED_MIN_LEVEL} (factory-owned)",
           )
   ```

### Step 3.4 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_handoff.py -v
```

Expected: all tests pass.

### Step 3.5 — commit

```bash
git add scripts/factory_core/handoff.py tests/test_handoff.py
git commit -m "fix(#196): handoff.py manifest range 1-5, import FACTORY_OWNED_MIN_LEVEL (R2/R7)"
```

---

## Task 4 — run_record.py: `side_effect_level` / `side_effect_profile` (R6)

### Step 4.1 — failing tests

Edit `tests/test_run_record.py` — add after `test_record_writes_jsonl`:

```python
class _RecordArgsWithProfile(_RecordArgs):
    side_effect_level = 4
    side_effect_profile = "v1"


def test_record_writes_side_effect_fields(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    rr.cmd_record(_RecordArgsWithProfile())

    rec = json.loads(jsonl.read_text().strip())
    assert rec["side_effect_level"] == 4
    assert rec["side_effect_profile"] == "v1"


def test_record_side_effect_fields_default_when_absent(tmp_path, monkeypatch):
    """getattr guard, same precedent as 'origin' — a bare Namespace without the flags
    (no CLI --side-effect-level/--side-effect-profile) must never crash, and must never
    claim a wider profile than it can prove (R6 fail-closed default)."""
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    rr.cmd_record(_RecordArgs())

    rec = json.loads(jsonl.read_text().strip())
    assert rec["side_effect_level"] == 1
    assert rec["side_effect_profile"] == "unknown"
```

Add an assemble-path test next to the file's existing assemble tests (all of which use the
`_AssembleArgs` helper at line 545 and patch both `rr.JSONL_PATH` and `rr.LEDGER_PATH` —
the latter is required for hermeticity: without it, `cmd_assemble` falls through to the
module's real default ledger path, which is exactly what `tests/test_run_record_hermetic.sh`
exists to prevent). Subclass `_AssembleArgs` rather than hand-rolling a namespace, so the
new test tracks the file's own convention and constructor signature:

```python
class _AssembleArgsWithProfile(_AssembleArgs):
    side_effect_level = 5
    side_effect_profile = "v1"


def test_assemble_stage_records_carry_side_effect_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "validation.md").write_text("STATUS: PASS\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgsWithProfile(tmp_path, out)
    rr.cmd_assemble(args)

    lines = [json.loads(l) for l in (tmp_path / "runs.jsonl").read_text().strip().splitlines()]
    assert lines and all(l["side_effect_level"] == 5 for l in lines)
    assert all(l["side_effect_profile"] == "v1" for l in lines)
```

### Step 4.2 — verify failure

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k side_effect -v
```

Expected: `KeyError: 'side_effect_level'` (fields not yet written).

### Step 4.3 — implement

In `scripts/factory_core/run_record.py`:

1. In `cmd_record`'s `record: dict = {...}` block (immediately after the existing
   `"origin": getattr(args, "origin", None) or "factory",` line), add:
   ```python
           "side_effect_level": getattr(args, "side_effect_level", None) or 1,
           "side_effect_profile": getattr(args, "side_effect_profile", None) or "unknown",
   ```
2. In `cmd_assemble`, in the per-stage `record: dict = {...}` block (the one built inside
   `for stage in stages:`), add the same two lines, reading from `args`:
   ```python
           "side_effect_level": getattr(args, "side_effect_level", None) or 1,
           "side_effect_profile": getattr(args, "side_effect_profile", None) or "unknown",
   ```
3. Add the matching CLI flags to both subparsers in `main()`:
   ```python
   r.add_argument("--side-effect-level", type=int, default=None)
   r.add_argument("--side-effect-profile", default=None)
   ```
   (on the `record` subparser, alongside `--origin`), and:
   ```python
   a.add_argument("--side-effect-level", type=int, default=None)
   a.add_argument("--side-effect-profile", default=None)
   ```
   (on the `assemble` subparser, alongside `--clone-dir`).

### Step 4.4 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all tests pass.

### Step 4.5 — commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#196): run_record.py records side_effect_level/profile per run (R6)"
```

---

## Task 5 — `config/config.yaml`: `side_effect.phase_levels` (R3)

### Step 5.1 — failing test

Add to `tests/test_side_effect.py` (append):

```python
import yaml as _yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_has_side_effect_phase_levels():
    cfg = _yaml.safe_load((_REPO_ROOT / "config/config.yaml").read_text())
    levels = cfg["side_effect"]["phase_levels"]
    for phase in ("refine", "plan", "implement", "validate", "conformance",
                  "code_review", "revise_advisory", "deconflict", "fix_main"):
        assert levels[phase] == 5, f"{phase} must start at level 5 (no v1 regression)"
```

### Step 5.2 — verify failure

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect.py -k config_yaml_has -v
```

Expected: `KeyError: 'side_effect'`.

### Step 5.3 — implement

Edit `config/config.yaml`, adding a new top-level block (placed after `main_red_autofix:`
and before `token_optimization:`, matching the file's existing top-level key ordering):

```yaml
side_effect:
  # Side-effect level each factory phase runs at (#196). 1..5; 6 is never valid here
  # (adapter.py/handoff.py reject 6 outright — see side_effect.py). All phases start at
  # 5 (today's unrestricted behavior). Tightening any phase (e.g. refine/plan -> 4) is a
  # separate, later, reviewed change (see #196's F2 follow-up).
  # env: SIDE_EFFECT_LEVEL_<PHASE> overrides one phase (e.g. SIDE_EFFECT_LEVEL_REFINE=4).
  phase_levels:
    refine: 5
    plan: 5
    implement: 5
    validate: 5
    conformance: 5
    code_review: 5
    revise_advisory: 5  # #196 plan-level addition (revise-advisory node) — see Architecture note
    deconflict: 5  # entrypoint.sh's own $INTENT=deconflict flow — resolves/pushes inline, never through archon
    fix_main: 5  # entrypoint.sh's $INTENT=fix-main -- a standalone `claude -p` session (main_red_fixer.py), not a DAG node
```

**Note on `.claude/skills/refinement/config.yaml`** (raised in architect review, cycle 2):
that path is *not* a second committed copy of this config — `git ls-files
.claude/skills/refinement/` shows it untracked; it is `effective_config.py`'s materialized
output (baked `config/config.yaml` deep-merged with `.factory/adapter.yaml` overrides,
written into the clone and git-excluded, per `entrypoint.sh`'s "Effective config" step).
It will carry this ticket's `side_effect:` block automatically once the image containing
this change is rebuilt, the same way it already carries `conformance:`/`code_review:`/
every other block — no second edit needed, and none of this ticket's tests should assert
against that generated path. The materialization step's own fail-open behavior
(`|| true`) is a pre-existing property shared by every config block, not something this
ticket introduces or needs to special-case.

### Step 5.4 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect.py -v
```

Expected: all tests pass.

### Step 5.5 — commit

```bash
git add config/config.yaml tests/test_side_effect.py
git commit -m "feat(#196): config.yaml side_effect.phase_levels block (R3)"
```

---

## Task 6 — `scripts/shims/git` and `scripts/shims/gh` (R5)

Design notes (read before implementing):

- Activation: **both** `FACTORY_SIDE_EFFECT_LEVEL` and `CLAUDECODE=1` must be set, per
  Task 0's verified discriminator. Otherwise transparent passthrough.
- The shim calls `side_effect.py render --level "$FACTORY_SIDE_EFFECT_LEVEL"` once per
  invocation and parses the JSON with `jq` — it never re-declares the table (Trust model:
  single source of truth).
- `push_scope=own_branch_only` (level 4) is not exercised by any v1 phase (all seven are
  level 5) — it only matters for a future level-4 phase (F2) or loop runner (R7). Rather
  than requiring `FACTORY_RUN_BRANCH` to be pre-computed by `entrypoint.sh` before the
  branch exists (it doesn't — DAG nodes create it mid-run), the shim treats
  `FACTORY_RUN_BRANCH` as an optional pin (a future runner can set it) and otherwise
  determines "own branch" dynamically via `git symbolic-ref --short HEAD` at push time
  (the branch is always checked out by the time a real `git push` runs). Task 7's test
  matrix exercises level 4 directly with `FACTORY_RUN_BRANCH` set explicitly.

### Step 6.1 — create the shims (tests come with Task 7, since they're one matrix)

Create `scripts/shims/git`:

```bash
#!/usr/bin/env bash
# side-effect guard (#196/R5): PATH-shadows `git`. Passthrough unless BOTH
# FACTORY_SIDE_EFFECT_LEVEL and CLAUDECODE=1 are set in this process's own env — the
# discriminator verified in the #196 plan's Task 0 between a phase agent's Bash-tool
# subprocess (both set) and an archon DAG bash: node (neither set to both).
set -euo pipefail

SHIM_DIR="$(cd "$(dirname "$0")" && pwd)"

_real_bin() {
  local name="$1" d
  IFS=':' read -ra _dirs <<< "$PATH"
  for d in "${_dirs[@]}"; do
    [ "$d" = "$SHIM_DIR" ] && continue
    if [ -x "$d/$name" ]; then echo "$d/$name"; return 0; fi
  done
  return 1
}

REAL_GIT=$(_real_bin git) || { echo "side-effect guard: real git not found on PATH" >&2; exit 1; }

if [ -z "${FACTORY_SIDE_EFFECT_LEVEL:-}" ] || [ "${CLAUDECODE:-}" != "1" ]; then
  exec "$REAL_GIT" "$@"
fi

LEVEL="$FACTORY_SIDE_EFFECT_LEVEL"
# Resolved relative to this script's own location, NOT $CLONE_DIR: CLONE_DIR is a plain
# (non-exported) shell variable inside entrypoint.sh and is never inherited by a phase
# agent's own Bash-tool subprocess (confirmed empirically in the #196 plan's Task 0 and
# by workflows/archon-dark-factory.yaml:355's own comment for bash: nodes) — a
# ${CLONE_DIR:-.}-relative path would silently fall back to "." and break the instant an
# agent `cd`s anywhere else, denying every git/gh call for the rest of that session.
# scripts/shims/<tool> always sits at <root>/scripts/shims/, so <root>/scripts is exactly
# one directory up from SHIM_DIR, in both the baked (/opt/dark-factory/scripts) and clone
# ($CLONE_DIR/dark-factory/scripts) layouts.
FACTORY_CORE_DIR="$(cd "$SHIM_DIR/.." && pwd)/factory_core"
CLI="$FACTORY_CORE_DIR/side_effect.py"
PROFILE_JSON=$(python3 "$CLI" render --level "$LEVEL" 2>/dev/null) || {
  echo "side-effect guard: cannot resolve profile for level ${LEVEL}" >&2
  exit 1
}
LEVEL_NAME=$(jq -r '.name' <<<"$PROFILE_JSON")
PUSH_SCOPE=$(jq -r '.push_scope' <<<"$PROFILE_JSON")
GIT_MODE=$(jq -r '.git_mode' <<<"$PROFILE_JSON")
mapfile -t GIT_DENIED < <(jq -r '.git_denied[]' <<<"$PROFILE_JSON")
mapfile -t GIT_ALLOWED < <(jq -r '.git_allowed[]' <<<"$PROFILE_JSON")

_emit_denied() {
  local verb="$1"
  local artdir="${ARTIFACTS_DIR:-}"
  [ -n "$artdir" ] || return 0
  local run issue
  run=$(basename "$artdir")
  issue=$(jq -r '.resolved_number // empty' "$artdir/issue.json" 2>/dev/null || true)
  [ -n "$issue" ] || issue=0
  python3 "$FACTORY_CORE_DIR/cli.py" run-record health-event \
    --run-id "$run" --issue "$issue" --event side_effect.denied \
    --detail tool=git "verb=${verb}" "level=${LEVEL}" >/dev/null 2>&1 || true
}

deny() {
  echo "side-effect guard: '$1' is denied at level ${LEVEL} (${LEVEL_NAME}); see docs/factory-target-boundary.md" >&2
  _emit_denied "$1"
  exit 1
}

_in_list() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [ "$x" = "$needle" ] && return 0; done
  return 1
}

VERB1="${1:-}"
VERB2="${2:-}"

case "$VERB1" in
  commit) _in_list "commit" "${GIT_DENIED[@]}" && deny "commit" ;;
  tag)    _in_list "tag" "${GIT_DENIED[@]}" && deny "tag" ;;
  remote)
    case "$VERB2" in
      add)     _in_list "remote add" "${GIT_DENIED[@]}" && deny "remote add" ;;
      set-url) _in_list "remote set-url" "${GIT_DENIED[@]}" && deny "remote set-url" ;;
    esac
    ;;
  push)
    case "$PUSH_SCOPE" in
      denied)
        deny "push"
        ;;
      own_branch_only)
        FORCE=0; DELETE=0; REMOTE=""; REFSPEC=""
        for arg in "${@:2}"; do
          case "$arg" in
            --force|--force-with-lease*|-f) FORCE=1 ;;
            --delete|-d) DELETE=1 ;;
            -*) : ;;
            *) if [ -z "$REMOTE" ]; then REMOTE="$arg"; else REFSPEC="$arg"; fi ;;
          esac
        done
        [ "$FORCE" = "1" ] && deny "push --force"
        [ "$DELETE" = "1" ] && deny "push --delete"
        case "$REFSPEC" in :*) deny "push (refspec delete)" ;; esac
        TARGET_BRANCH="${REFSPEC#*:}"
        [ "$TARGET_BRANCH" = "$REFSPEC" ] && TARGET_BRANCH="$REFSPEC"
        # HEAD/refs/heads/<x> normalization: v1 dead code (no phase runs at level 4 —
        # D2), kept correct anyway since F2 (tighten refine/plan -> 4) will exercise
        # `git push origin HEAD`, the DAG's actual push idiom, immediately.
        case "$TARGET_BRANCH" in
          HEAD) TARGET_BRANCH=$("$REAL_GIT" symbolic-ref --short HEAD 2>/dev/null || echo "") ;;
          refs/heads/*) TARGET_BRANCH="${TARGET_BRANCH#refs/heads/}" ;;
        esac
        # $REAL_GIT, never bare `git` -- SHIM_DIR is still first on PATH here, so a
        # bare `git symbolic-ref` would re-enter this shim (render+jq again) before
        # falling through; harmless today but a real hazard once this verb table grows.
        [ -z "$TARGET_BRANCH" ] && TARGET_BRANCH=$("$REAL_GIT" symbolic-ref --short HEAD 2>/dev/null || echo "")
        OWN_BRANCH="${FACTORY_RUN_BRANCH:-$("$REAL_GIT" symbolic-ref --short HEAD 2>/dev/null || echo "")}"
        [ "$TARGET_BRANCH" = "main" ] && deny "push (main)"
        if [ -n "$TARGET_BRANCH" ] && [ "$TARGET_BRANCH" != "$OWN_BRANCH" ]; then
          deny "push (not own branch)"
        fi
        ;;
      unrestricted)
        # Read git_denied instead of hardcoding --delete/:* — the Trust model section
        # is explicit that nothing may re-declare the table side_effect.py owns.
        for arg in "${@:2}"; do
          case "$arg" in
            --delete|-d) _in_list "push --delete" "${GIT_DENIED[@]}" && deny "push --delete" ;;
            :*) _in_list "push :refspec-delete" "${GIT_DENIED[@]}" && deny "push (refspec delete)" ;;
          esac
        done
        ;;
    esac
    ;;
esac

# R5 fail-closed default (level 1 only — see the Profile docstring for why levels 2-5
# stay pure deny-list): any verb not already denied above and not in git_allowed is
# denied, not silently passed through. This is what actually stops `git checkout`,
# `git reset --hard`, `git clean -fd`, `git stash`, `git config`, `git apply`, etc. at
# level 1 — none of which R1's flat git_denied list names.
if [ "$GIT_MODE" = "allow" ] && ! _in_list "$VERB1" "${GIT_ALLOWED[@]}"; then
  deny "$VERB1"
fi

exec "$REAL_GIT" "$@"
```

Create `scripts/shims/gh`:

```bash
#!/usr/bin/env bash
# side-effect guard (#196/R5): PATH-shadows `gh`. Same activation rule as scripts/shims/git.
set -euo pipefail

SHIM_DIR="$(cd "$(dirname "$0")" && pwd)"

_real_bin() {
  local name="$1" d
  IFS=':' read -ra _dirs <<< "$PATH"
  for d in "${_dirs[@]}"; do
    [ "$d" = "$SHIM_DIR" ] && continue
    if [ -x "$d/$name" ]; then echo "$d/$name"; return 0; fi
  done
  return 1
}

REAL_GH=$(_real_bin gh) || { echo "side-effect guard: real gh not found on PATH" >&2; exit 1; }

if [ -z "${FACTORY_SIDE_EFFECT_LEVEL:-}" ] || [ "${CLAUDECODE:-}" != "1" ]; then
  exec "$REAL_GH" "$@"
fi

LEVEL="$FACTORY_SIDE_EFFECT_LEVEL"
# Resolved relative to this script's own location, not $CLONE_DIR — same rationale as
# scripts/shims/git (see its matching comment): CLONE_DIR is never inherited by a phase
# agent's own Bash-tool subprocess.
FACTORY_CORE_DIR="$(cd "$SHIM_DIR/.." && pwd)/factory_core"
CLI="$FACTORY_CORE_DIR/side_effect.py"
PROFILE_JSON=$(python3 "$CLI" render --level "$LEVEL" 2>/dev/null) || {
  echo "side-effect guard: cannot resolve profile for level ${LEVEL}" >&2
  exit 1
}
LEVEL_NAME=$(jq -r '.name' <<<"$PROFILE_JSON")
GH_MODE=$(jq -r '.gh_mode' <<<"$PROFILE_JSON")
mapfile -t GH_ALLOWED < <(jq -r '.gh_allowed[]' <<<"$PROFILE_JSON")
mapfile -t GH_DENIED  < <(jq -r '.gh_denied[]'  <<<"$PROFILE_JSON")

_emit_denied() {
  local verb="$1"
  local artdir="${ARTIFACTS_DIR:-}"
  [ -n "$artdir" ] || return 0
  local run issue
  run=$(basename "$artdir")
  issue=$(jq -r '.resolved_number // empty' "$artdir/issue.json" 2>/dev/null || true)
  [ -n "$issue" ] || issue=0
  python3 "$FACTORY_CORE_DIR/cli.py" run-record health-event \
    --run-id "$run" --issue "$issue" --event side_effect.denied \
    --detail tool=gh "verb=${verb}" "level=${LEVEL}" >/dev/null 2>&1 || true
}

deny() {
  echo "side-effect guard: '$1' is denied at level ${LEVEL} (${LEVEL_NAME}); see docs/factory-target-boundary.md" >&2
  _emit_denied "$1"
  exit 1
}

_in_list() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [ "$x" = "$needle" ] && return 0; done
  return 1
}

VERB1="${1:-}"
VERB2="${2:-}"
TWO_WORD="$VERB1 $VERB2"

if [ "$VERB1" = "api" ]; then
  METHOD="GET"; HAS_BODY=0; TAKE_NEXT=0
  for arg in "${@:2}"; do
    if [ "$TAKE_NEXT" = "1" ]; then METHOD="$arg"; TAKE_NEXT=0; continue; fi
    case "$arg" in
      -X|--method) TAKE_NEXT=1 ;;
      -X*) METHOD="${arg#-X}" ;;
      --method=*) METHOD="${arg#--method=}" ;;
      -f*|--field*|-F*|--raw-field*|--input*) HAS_BODY=1 ;;
    esac
  done
  METHOD=$(printf '%s' "$METHOD" | tr '[:lower:]' '[:upper:]')
  if [ "$GH_MODE" = "allow" ]; then
    if [ "$METHOD" != "GET" ] || [ "$HAS_BODY" = "1" ]; then
      deny "api ${METHOD}"
    fi
  else
    if [ "$METHOD" = "DELETE" ] && _in_list "api:DELETE" "${GH_DENIED[@]}"; then
      deny "api -X DELETE"
    fi
    if [ "$METHOD" != "GET" ] && _in_list "api:non-GET" "${GH_DENIED[@]}"; then
      deny "api ${METHOD}"
    fi
  fi
  exec "$REAL_GH" "$@"
fi

DISPLAY_VERB="$VERB1"
[ -n "$VERB2" ] && DISPLAY_VERB="$TWO_WORD"

if [ "$GH_MODE" = "allow" ]; then
  # Three checks, not two: gh_allowed mixes single-word read verbs that must match in
  # EITHER position ("view"/"list"/"status"/"search" — e.g. `gh issue view`, `gh pr
  # list`, bare `gh status`) with exact two-word grants ("issue create" etc., which
  # must NOT become "anything ending in create" — `gh pr create` must stay denied).
  # VERB2-alone is safe to check unconditionally: "create"/"comment"/"edit" never
  # appear as bare single-word entries in any level's gh_allowed list, only as the
  # second half of a two-word entry, so this can't accidentally admit `gh pr create`.
  if _in_list "$VERB1" "${GH_ALLOWED[@]}" || _in_list "$VERB2" "${GH_ALLOWED[@]}" \
      || _in_list "$TWO_WORD" "${GH_ALLOWED[@]}"; then
    exec "$REAL_GH" "$@"
  fi
  deny "$DISPLAY_VERB"
else
  if _in_list "$VERB1" "${GH_DENIED[@]}" || _in_list "$TWO_WORD" "${GH_DENIED[@]}"; then
    deny "$DISPLAY_VERB"
  fi
  exec "$REAL_GH" "$@"
fi
```

### Step 6.2 — make executable

```bash
mkdir -p scripts/shims
chmod +x scripts/shims/git scripts/shims/gh
```

(Tests and the pass/fail cycle for these two files are Task 7, since a shim without its
test matrix is unverifiable in isolation — the TDD loop here is written as one unit.)

### Step 6.3 — commit is folded into Task 7's commit (shim + its test land together)

---

## Task 7 — `tests/test_side_effect_shims.sh` (R5) + CI wiring

### Step 7.1 — write the test (this is the "failing test" for Task 6's shims)

Create `tests/test_side_effect_shims.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Hermeticity: when this script itself runs from inside a factory container (Step 7.4's
# implementer, or a future CI run under the real entrypoint), ARTIFACTS_DIR is already set
# in the ambient environment. Every check() call below except the dedicated health-event
# block must run with it unset, or _emit_denied's `[ -n "$artdir" ] || return 0` guard
# would fire on every deny case and try to reach a real run-record CLI path.
unset ARTIFACTS_DIR || true

# The shims resolve side_effect.py/cli.py relative to their OWN location (scripts/shims/
# is a sibling of scripts/factory_core/ in the real repo), never via $CLONE_DIR — see the
# comment in scripts/shims/git itself. That means this test invokes the shim straight
# from this checkout's real scripts/shims/, which in turn always resolves
# scripts/factory_core/side_effect.py from this same real checkout too: exactly what we
# want (the actual implementation under test, not a stale fixture copy), and simpler than
# reproducing a synthetic $CLONE_DIR/dark-factory/... layout.
#
# The one thing that must NOT run for real is `cli.py run-record health-event` (it would
# append to the real runs.jsonl / hit the real Seq endpoint) -- shadow python3 itself with
# a function (same pattern as tests/test_hooks.sh's gh()/python3() stubs), delegating
# every other call (side_effect.py render, etc.) straight through to the real binary.
STUB_LOG="$TMP/health-event-calls.log"
python3() {
  if printf '%s\n' "$*" | grep -q "run-record health-event"; then
    echo "$*" >> "$STUB_LOG"
    return 0
  fi
  command python3 "$@"
}
export -f python3
export STUB_LOG

# Real binary stubs the shim must exec through when allowed.
mkdir -p "$TMP/real"
cat > "$TMP/real/git" <<'EOF'
#!/usr/bin/env bash
echo "REAL_GIT $*"
EOF
cat > "$TMP/real/gh" <<'EOF'
#!/usr/bin/env bash
echo "REAL_GH $*"
EOF
chmod +x "$TMP/real/git" "$TMP/real/gh"

SHIM_DIR="$REPO_ROOT/scripts/shims"
export PATH="$SHIM_DIR:$TMP/real:$PATH"

PASS=0; FAIL=0
check() {
  # usage: check <allow|deny> <cmd...>   e.g. check allow git log
  local expect="$1"; shift
  local out rc
  set +e
  out=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$expect" = "allow" ]; then
    if [ "$rc" = "0" ] && echo "$out" | grep -q "^REAL_"; then
      PASS=$((PASS+1))
    else
      echo "FAIL (expected allow): $* -> rc=$rc out=$out"; FAIL=$((FAIL+1))
    fi
  else
    if [ "$rc" != "0" ] && echo "$out" | grep -q "side-effect guard"; then
      PASS=$((PASS+1))
    else
      echo "FAIL (expected deny): $* -> rc=$rc out=$out"; FAIL=$((FAIL+1))
    fi
  fi
}

# --- Activation matrix: no level var -> passthrough regardless of CLAUDECODE ---
unset FACTORY_SIDE_EFFECT_LEVEL || true
CLAUDECODE=1 check allow git log
CLAUDECODE=1 check allow git commit -m x
CLAUDECODE=1 check allow gh pr create

# --- Activation matrix: level var set but no CLAUDECODE -> passthrough ---
# Explicit `unset CLAUDECODE`, not just "don't export it": this script is itself run
# from inside a Claude Code Bash tool at Step 7.4, which inherits CLAUDECODE=1 from
# its own parent process — without the unset, these two checks would silently inherit
# an ambient CLAUDECODE=1 and assert the wrong thing (activation instead of passthrough).
unset CLAUDECODE || true
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr create

# --- Level 1: read-only research ---
export CLAUDECODE=1
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git log
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git tag v1
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote add x https://example.com
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote set-url origin https://example.com
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git remote -v
# R5 fail-closed default at level 1: verbs R1's table never names must still deny by
# default (git_mode=allow), not silently pass — the gap architect review cycle 2 caught.
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git checkout -b x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git reset --hard
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git clean -fd
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git stash
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git config user.name x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git apply patch.diff
# Exercises the VERB2-alone match: gh_allowed's "view"/"list" must match as either
# gh's first word (bare `gh status`) or second word (`gh issue view`, `gh pr list`) —
# the earlier draft of this shim only checked VERB1 and the literal two-word
# concatenation, which denied both of these (caught by architect review, cycle 1).
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh issue view 1
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr list
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh api repos/o/r/issues
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh api repos/o/r/issues -X POST -f title=x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh pr create

# --- Level 2: artifact writing ---
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git tag v1
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git remote add x https://example.com
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git remote set-url origin https://example.com
# git_mode=deny at level 2 (unlike level 1's allow-list): ordinary local writes stay
# unrestricted — only the enumerated remote-facing verbs above are denied.
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git checkout -b x
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git stash
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh issue create --title x

# --- Level 3: GitHub ticket creation ---
FACTORY_SIDE_EFFECT_LEVEL=3 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue comment 1 --body hi
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue edit 1 --add-label x
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  gh pr create

# --- Level 4: code modification (own branch push only) ---
# No real git repo needed here: FACTORY_RUN_BRANCH is pinned explicitly below, so the
# shim's own_branch_only branch never falls back to `git symbolic-ref` for these three
# cases (bash short-circuits ${FACTORY_RUN_BRANCH:-...} once the var is set) — and `git`
# on PATH at this point is the stub (which just echoes its argv), not a real repo, so an
# `init`/`checkout -b` here would be theater, not a real fixture.
export FACTORY_RUN_BRANCH="feat/issue-196-x"
FACTORY_SIDE_EFFECT_LEVEL=4 sh -c "cd '$TMP' && git push origin feat/issue-196-x" \
  && PASS=$((PASS+1)) || { echo "FAIL: level4 own-branch push should allow"; FAIL=$((FAIL+1)); }
FACTORY_SIDE_EFFECT_LEVEL=4 sh -c "cd '$TMP' && ! git push origin main" \
  && PASS=$((PASS+1)) || { echo "FAIL: level4 push to main must deny"; FAIL=$((FAIL+1)); }
FACTORY_SIDE_EFFECT_LEVEL=4 sh -c "cd '$TMP' && ! git push --force origin feat/issue-196-x" \
  && PASS=$((PASS+1)) || { echo "FAIL: level4 --force must deny"; FAIL=$((FAIL+1)); }
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh pr create
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh repo delete o/r
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh api repos/o/r -X POST
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh api repos/o/r
unset FACTORY_RUN_BRANCH

# --- Level 5: PR creation (never-list only) ---
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh pr create
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git push origin some-branch
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  git push --delete origin some-branch
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh repo delete o/r
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh secret set X
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh auth login
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh api repos/o/r -X DELETE
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh repo view o/r

# --- Health-event emission on denial (R5's {tool, verb, level} audit line) ---
# The blocks above never set ARTIFACTS_DIR, so _emit_denied's early
# `[ -n "$artdir" ] || return 0` guard always short-circuits there and the python3()
# stub above (which only logs run-record health-event calls) never actually sees one —
# cover it explicitly here.
HE_TMP="$TMP/health-event-artifacts"
mkdir -p "$HE_TMP"
echo '{"resolved_number": 42}' > "$HE_TMP/issue.json"
rm -f "$TMP/health-event-calls.log"
ARTIFACTS_DIR="$HE_TMP" FACTORY_SIDE_EFFECT_LEVEL=1 git commit -m x >/dev/null 2>&1 || true
if [ -f "$TMP/health-event-calls.log" ] \
    && grep -q "run-record health-event" "$TMP/health-event-calls.log" \
    && grep -q "side_effect.denied" "$TMP/health-event-calls.log" \
    && grep -q "issue 42" "$TMP/health-event-calls.log"; then
  PASS=$((PASS+1))
else
  echo "FAIL: denial did not emit a run-record health-event"; cat "$TMP/health-event-calls.log" 2>/dev/null; FAIL=$((FAIL+1))
fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ] && echo PASS
exit $([ "$FAIL" = "0" ] && echo 0 || echo 1)
```

### Step 7.2 — verify failure

```bash
chmod +x tests/test_side_effect_shims.sh
bash tests/test_side_effect_shims.sh
```

Expected: fails (`scripts/shims/git`/`gh` don't exist yet — `exec: git: not found` style
errors, or a permission error). If Task 6 was implemented first, skip straight to 7.3.

### Step 7.3 — implement

Do Task 6's Step 6.1–6.2 now if not already done.

### Step 7.4 — verify pass

```bash
bash tests/test_side_effect_shims.sh
```

Expected: `PASS=<n> FAIL=0` then `PASS`.

### Step 7.5 — wire into CI

Edit `.github/workflows/ci.yml`, `tests:` job, add a line immediately after
`- run: bash tests/test_smoke_gate.sh`:

```yaml
      - run: bash tests/test_smoke_gate.sh
      - run: bash tests/test_side_effect_shims.sh
```

### Step 7.6 — commit

```bash
git add scripts/shims/git scripts/shims/gh tests/test_side_effect_shims.sh .github/workflows/ci.yml
git commit -m "feat(#196): git/gh side-effect command shim + CI wiring (R5)"
```

---

## Task 8 — `entrypoint.sh`: compute/export/log (R3), PATH prepend (R5), plumb R6 flags

### Step 8.1 — failing test

`entrypoint.sh` doesn't have a unit-testable "compute the level" seam today the way
`_entrypoint_cfg_apply` is tested. `tests/test_entrypoint_current_run.sh` (read in full
before editing) establishes the exact scaffolding every case in that file must reuse:
`GH_TOKEN`/`CLAUDE_CODE_OAUTH_TOKEN`/`IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` exports and
`git`/`gh`/`docker` stub functions at the top (already in scope for every case in the
file), an `assert_true "desc" "condition"` helper that increments `PASSED`/`FAILED`
(**not** a subshell `exit 1`, which the file's `set -uo pipefail` — no `-e` — would not
propagate as a failure), `CLONE_DIR` is set from `FACTORY_CLONE_DIR` at `entrypoint.sh:14`
(exporting `CLONE_DIR` directly before sourcing has no effect — it gets overwritten), and
the file's own trailing `echo "Results: ..."` / `[ "$FAILED" -eq 0 ]` lines are the
script's actual exit status, so a new case must be inserted **before** them, not appended
after.

Insert this new case immediately before the file's existing `# Cleanup` /
`echo ""` / `echo "Results: ..."` tail (i.e. right after Case 2's
`assert_true "multi-phase intent 'fix' -> stage='unknown'" ...` line):

```bash
# --- #196: FACTORY_SIDE_EFFECT_LEVEL is computed and exported before archon runs ---
TMP_SE=$(mktemp -d /tmp/196-clone-XXXXXX)
mkdir -p "$TMP_SE/.claude/skills/refinement"
cat > "$TMP_SE/.claude/skills/refinement/config.yaml" <<'EOF'
side_effect:
  phase_levels:
    plan: 5
    implement: 5
    validate: 5
    conformance: 5
    code_review: 5
    revise_advisory: 5
EOF
mkdir -p "$TMP_SE/dark-factory/scripts/factory_core"
cp "$SCRIPT_DIR/../scripts/factory_core/side_effect.py" "$TMP_SE/dark-factory/scripts/factory_core/"

ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
FACTORY_CLONE_DIR="$TMP_SE" ARGUMENTS="Plan issue #1" \
  ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #1"

trap - ERR
set +e; set +u; set +o pipefail

# entrypoint.sh:14 sets CLONE_DIR="$FACTORY_CLONE_DIR" during the source above, so
# CLONE_DIR is already $TMP_SE in this shell — no need to re-set it.
_compute_side_effect_level >/dev/null

assert_true "#196 single-phase intent 'plan' -> level 5" "[ '$FACTORY_SIDE_EFFECT_LEVEL' = '5' ]"
assert_true "#196 profile version is v1" "[ '$FACTORY_SIDE_EFFECT_PROFILE_VERSION' = 'v1' ]"

# entrypoint.sh's own $INTENT vocabulary uses "fix" (not the DAG's "new") for a first-time
# implement dispatch -- this is exactly the mismatch architect review (cycle 3) caught:
# intent_phases() must be fed entrypoint.sh's actual $INTENT or every real `Fix issue #N`
# run silently resolves to level 1 and the shim denies every git/gh call the implement
# phase needs.
unset FACTORY_SIDE_EFFECT_LEVEL FACTORY_SIDE_EFFECT_PROFILE_VERSION
FACTORY_CLONE_DIR="$TMP_SE" ARGUMENTS="Fix issue #1" \
  ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh" "Fix issue #1"
trap - ERR
set +e; set +u; set +o pipefail
_compute_side_effect_level >/dev/null
assert_true "#196 multi-phase intent 'fix' -> level 5 (not 1)" "[ '$FACTORY_SIDE_EFFECT_LEVEL' = '5' ]"

rm -rf "$TMP_SE" "$ARTIFACTS_DIR"
set -uo pipefail
```

### Step 8.2 — verify failure

```bash
bash tests/test_entrypoint_current_run.sh
```

Expected: fails — `_compute_side_effect_level: command not found`.

### Step 8.3 — implement

In `entrypoint.sh`, add a new function near `_entrypoint_cfg_apply` (after its
definition, before the `--- Parse arguments ---` section):

```bash
# Compute and export the container's effective side-effect level (#196/R3) + prepend
# the git/gh shim onto PATH (#196/R5). Single source of truth: side_effect.py. Must run
# post-clone (needs CLONE_DIR/config.yaml) and before `archon workflow run` (both the
# export and the PATH prepend must be visible to every node's subprocess).
_compute_side_effect_level() {
  local cfg=""
  for cfg in "${CLONE_DIR}/.claude/skills/refinement/config.yaml" "/opt/refinement-skills/config.yaml"; do
    [ -f "$cfg" ] && break
    cfg=""
  done
  local se_cli="${CLONE_DIR}/dark-factory/scripts/factory_core/side_effect.py"
  local err
  err=$(mktemp)
  if FACTORY_SIDE_EFFECT_LEVEL=$(python3 "$se_cli" effective-container-level \
      --intent "${INTENT:-unknown}" ${cfg:+--config "$cfg"} 2>"$err"); then
    :
  else
    echo "WARNING: side-effect level resolution failed ($(cat "$err")) — defaulting to 1" >&2
    FACTORY_SIDE_EFFECT_LEVEL=1
  fi
  rm -f "$err"
  FACTORY_SIDE_EFFECT_PROFILE_VERSION="v1"
  export FACTORY_SIDE_EFFECT_LEVEL FACTORY_SIDE_EFFECT_PROFILE_VERSION
  local phases
  phases=$(python3 "$se_cli" intent-phases --intent "${INTENT:-unknown}" 2>/dev/null || echo "")
  echo "side_effect_level=${FACTORY_SIDE_EFFECT_LEVEL} profile=${FACTORY_SIDE_EFFECT_PROFILE_VERSION} phases=${phases}"
  export PATH="${CLONE_DIR}/dark-factory/scripts/shims:${PATH}"
}
```

Call it right after `_entrypoint_cfg_apply` (existing line `_entrypoint_cfg_apply`, just
after the "Apply config.yaml policy knobs post-clone" comment block):

```bash
# --- Apply config.yaml policy knobs post-clone (env overrides logged when active) ---
_entrypoint_cfg_apply

# --- Compute effective side-effect level + PATH shim (#196/R3, R5) ---
_compute_side_effect_level
```

Now plumb the two new flags at all four `run-record record`/`run-record assemble` call
sites (grep `run-record record\|run-record assemble` to find them precisely — they are the
two `run-record record` calls inside `_handle_session_window_pause` and `on_failure`, and
the two `run-record assemble` calls inside `on_failure`'s failed-with-artifacts branch and
the main success path). Add to each:

```bash
    --side-effect-level "${FACTORY_SIDE_EFFECT_LEVEL:-1}" \
    --side-effect-profile "${FACTORY_SIDE_EFFECT_PROFILE_VERSION:-unknown}" \
```

immediately before the closing `|| true` (or `--clone-dir "$CLONE_DIR" || true` for the
success-path `assemble` call). Example for the success-path `assemble` call:

```bash
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "${RUN_STARTED_AT:-}" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$ARCHON_COST_JSON" \
  --archon-cost-exit-code "$ARCHON_COST_RC" \
  --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
  --side-effect-level "${FACTORY_SIDE_EFFECT_LEVEL:-1}" \
  --side-effect-profile "${FACTORY_SIDE_EFFECT_PROFILE_VERSION:-unknown}" \
  --clone-dir "$CLONE_DIR" || true
```

Apply the same two-line addition to the other three call sites (both `run-record record`
calls, and the failed-with-artifacts `run-record assemble` call), each immediately before
its own trailing `|| true` line.

### Step 8.4 — verify pass

```bash
bash tests/test_entrypoint_current_run.sh
```

Expected: `Results: <n> passed, 0 failed` including the three new `#196 ...` assertions, and
the file's existing checks all still pass.

### Step 8.5 — commit

```bash
git add entrypoint.sh tests/test_entrypoint_current_run.sh
git commit -m "feat(#196): entrypoint.sh computes/exports side-effect level, shims PATH (R3/R5)"
```

---

## Task 9 — `workflows/archon-dark-factory.yaml`: `denied_tools: []` on seven phase nodes (R4)

### Step 9.1 — failing test (this is Task 10, written first per TDD)

See Task 10 below — write `tests/test_side_effect_dag.py` first, verify it fails, then come
back and do this task's Step 9.2.

### Step 9.2 — implement

Edit `workflows/archon-dark-factory.yaml`. For each of the seven phase-agent `command:`
nodes, add `denied_tools: []` directly under `command:`. All seven are level 5 in v1, whose
profile removes no tools — so every one of these seven additions is the same literal `[]`.
(`revise-advisory` is the plan-level addition beyond the spec's literal six — see the note
in the Architecture section above.)

1. `refine` node (currently):
   ```yaml
     - id: refine
       command: dark-factory-refine
       depends_on: [enforce-budget-refine, setup-refine-branch, fetch-issue]
       when: "$parse-intent.output.intent == 'refine'"
       idle_timeout: 600000
   ```
   becomes:
   ```yaml
     - id: refine
       command: dark-factory-refine
       denied_tools: []  # #196: side_effect_level 5 (config/config.yaml side_effect.phase_levels.refine) — profile removes no tools
       depends_on: [enforce-budget-refine, setup-refine-branch, fetch-issue]
       when: "$parse-intent.output.intent == 'refine'"
       idle_timeout: 600000
   ```
2. `plan` node — same pattern, add `denied_tools: []` with the comment referencing
   `phase_levels.plan`.
3. `implement` node — add `denied_tools: []` referencing `phase_levels.implement`.
4. `validate` node — add `denied_tools: []` referencing `phase_levels.validate`.
5. `conformance` node — add `denied_tools: []` referencing `phase_levels.conformance`.
6. `code-review` node — add `denied_tools: []` referencing `phase_levels.code_review`
   (note the underscore in the config key vs. the hyphenated node id — call this out
   explicitly in the comment since it's the one place the two spellings sit side by side:
   `# #196: side_effect_level 5 (config/config.yaml side_effect.phase_levels.code_review)`).
7. `revise-advisory` node (currently):
   ```yaml
     - id: revise-advisory
       command: dark-factory-revise-advisory
       depends_on: [code-review]
       when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
       idle_timeout: 600000
   ```
   becomes:
   ```yaml
     - id: revise-advisory
       command: dark-factory-revise-advisory
       denied_tools: []  # #196: side_effect_level 5 (config/config.yaml side_effect.phase_levels.revise_advisory) — profile removes no tools; plan-level addition, see Architecture note
       depends_on: [code-review]
       when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
       idle_timeout: 600000
   ```

### Step 9.3 — verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect_dag.py -v
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: all pass — `denied_tools` is a schema-known DAG node field
(`dag-node.ts:142`), so the structural checks accept it without modification.

### Step 9.4 — commit

```bash
git add workflows/archon-dark-factory.yaml
git commit -m "feat(#196): explicit denied_tools: [] on seven phase-agent DAG nodes (R4)"
```

---

## Task 10 — `tests/test_side_effect_dag.py` (R4)

### Step 10.1 — write the test (pattern: `tests/test_budget_enforce_dag.py`)

Create `tests/test_side_effect_dag.py`:

```python
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import side_effect

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _REPO_ROOT / "config/config.yaml"
_WORKFLOW = _REPO_ROOT / "workflows/archon-dark-factory.yaml"

_PHASE_NODES = ("refine", "plan", "implement", "validate", "conformance", "code-review",
                 "revise-advisory")


def _config():
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id", _PHASE_NODES)
def test_phase_node_declares_denied_tools(node_id):
    nodes = _workflow_nodes()
    assert node_id in nodes, f"DAG node '{node_id}' not found"
    assert "denied_tools" in nodes[node_id], (
        f"phase node '{node_id}' must declare denied_tools explicitly (#196/R4) — "
        f"its absence must be detectable, not silently mean 'nothing removed'"
    )


@pytest.mark.parametrize("node_id", _PHASE_NODES)
def test_phase_node_denied_tools_matches_configured_level(node_id):
    nodes = _workflow_nodes()
    phase_levels = _config()["side_effect"]["phase_levels"]
    config_key = node_id.replace("-", "_")
    level = side_effect.effective_level(phase_levels[config_key])
    expected = side_effect.profile_for(level).denied_tools
    assert nodes[node_id]["denied_tools"] == expected, (
        f"'{node_id}' denied_tools must equal profile_for(level {level}).denied_tools "
        f"({expected}); the DAG is static YAML and must be kept honest against config.yaml"
    )
```

### Step 10.2 — verify failure (before Task 9's Step 9.2)

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect_dag.py -v
```

Expected: `test_phase_node_declares_denied_tools` fails for all 7 parametrized node ids
(each node exists in the DAG, but `'denied_tools' in nodes[<id>]` is `False` — none of
them declare the key yet). Fails as designed.

### Step 10.3 — go implement Task 9's Step 9.2, then verify pass

```bash
PYTHONPATH=scripts python -m pytest tests/test_side_effect_dag.py -v
```

Expected: all 14 parametrized cases pass (7 nodes × 2 tests each).

### Step 10.4 — commit

```bash
git add tests/test_side_effect_dag.py
git commit -m "test(#196): DAG-vs-config consistency check for denied_tools (R4)"
```

(If Task 9 was already committed separately, this test file can be included in that same
commit instead of a standalone one — either ordering is fine since both are needed
together for green CI.)

---

## Task 11 — `docs/adapter-authoring-guide.md`: "Side-effect levels" section (R7)

### Step 11.1 — no test (documentation-only task); implement directly

First, fix a doc/reality mismatch R2 would otherwise leave behind: `docs/adapter-authoring-guide.md:225`
currently reads (in the manifest schema example):

```yaml
side_effect_level: 2                        # int 1-6; must equal that loop's declared side_effect_level
```

Change `1-6` to `1-5` so this file doesn't contradict its own new section below the moment
R2 ships:

```yaml
side_effect_level: 2                        # int 1-5; must equal that loop's declared side_effect_level
```

Then append a new `## Side-effect levels` section to `docs/adapter-authoring-guide.md`
(place it after the existing loop-entry-shape section that documents `side_effect_level`,
or at the end of the file if no better anchor exists — check the file's current structure
for where `side_effect_level` is already mentioned, if anywhere, and put this next to it):

```markdown
## Side-effect levels

Every `loops[]` entry declares `side_effect_level` (1–5; 6 is rejected at validation — out
of scope for v1, #194). `scripts/factory_core/side_effect.py` is the single source of
truth for what each level enforces:

| Level | Name | Layer A — tools removed | Layer B — shim denies | Net effect |
|---|---|---|---|---|
| 1 | read-only research | `Write`, `Edit`, `MultiEdit`, `NotebookEdit` | `git`: `commit`, `push`, `tag`, `remote add/set-url`; `gh`: everything except `view`, `list`, `status`, `search`, `api` with method GET and no body | Can read and run read-only commands. |
| 2 | artifact writing | none | `git`: `push`, `tag`, `remote set-url`; `gh`: all mutating verbs (as level 1) | Writes files and local commits; nothing leaves the container. |
| 3 | GitHub ticket creation | none | `git`: as level 2; `gh`: as level 2 except `issue create`/`issue comment`/`issue edit` allowed | Can file and annotate issues; cannot modify code. |
| 4 | code modification | none | `gh`: `pr create/merge/ready/review/close`, `release *`, `repo *`, `secret *`, `auth *`, `api` non-GET; `git push` only to the run's own branch, never `--force`/`--delete`, never `main` | Commits and pushes its branch; cannot open or merge PRs. |
| 5 | PR creation | none | never-list only: `gh repo delete/archive/rename`, `gh secret *`, `gh auth *`, `gh ssh-key *`, `gh gpg-key *`, `gh api -X DELETE`, `git push --delete`/refspec deletions | Full PR-creation workflow (today's implement/push-and-pr behavior). |
| 6 | external production side effect | — | rejected at validation | Out of scope for v1. |

`side_effect.effective_level(value)` fails closed: an undeclared, non-int, boolean, or
out-of-1–5-range level resolves to **1** (the most restrictive profile), never to an open
default. `FACTORY_OWNED_MIN_LEVEL = 4` — a loop declaring level ≥ 4 is factory-owned and is
rejected by the manifest-handoff path (`handoff.py::cross_check`) until a real loop runner
applies `side_effect.profile_for()` itself (tracked as #196's F3 follow-up).

**What this does and does not claim.** The shim (`scripts/shims/git`, `scripts/shims/gh`)
is a `PATH` shim guarding the two CLIs a Bash-tool subprocess would otherwise reach
directly; a process invoking `/usr/bin/git` by absolute path bypasses it. v1 is a policy
boundary against mistaken or prompt-injected behavior, not a security boundary against a
deliberately hostile agent — see `docs/factory-target-boundary.md` (#201) for the full
trust-model writeup, and #196's spec (`docs/superpowers/specs/2026-09-04-side-effect-levels-permission-profiles-a2-design.md`)
Known limitations section for the credential-scoping follow-up (F1) that closes this gap.
```

### Step 11.2 — commit

```bash
git add docs/adapter-authoring-guide.md
git commit -m "docs(#196): side-effect levels section in adapter authoring guide (R7)"
```

---

## Task 12 — Full-suite verification (AC disposition)

No new code. Run the complete gate set the acceptance criteria require, in order, and
record the results in the PR description:

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
bash tests/test_identity.sh
bash tests/test_hooks.sh
bash tests/test_smoke_gate.sh
bash tests/test_side_effect_shims.sh
bash tests/test_run_compose.sh
bash tests/test_model_proxy_compose.sh
bash tests/test_model_proxy_smoke.sh
bash tests/test_entrypoint_current_run.sh
bash tests/test_entrypoint_session_window.sh
bash tests/test_entrypoint_error_signature.sh
bash tests/test_cost_report_endpoint.sh
bash tests/test_cost_report_harness_economics.sh
bash tests/test_run_record_hermetic.sh
bash tests/test_entrypoint_cost_report_regression.sh
bash tests/test_budget_gate.sh
bash tests/test_verdict_gate_check.sh
bash tests/test_budget_context.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: every command exits 0. This is the full CI `tests:` + `dag-check:` job set
(mirroring `.github/workflows/ci.yml` exactly, now including Task 7's added shim test)
plus `PYTHONPATH=scripts python -m pytest tests/ -v` per `CLAUDE.md`'s conventions.

**Live dry runs (spec's AC disposition table, "Existing factory phases run unchanged"
row, explicitly requires these beyond the automated suite above — they are the only way to
observe `entrypoint.sh`'s new `side_effect_level=... profile=... phases=...` log line and
the `PATH` shim actually active in a real container, which no unit test exercises
end-to-end). Three runs, not two: `refine`/`plan` alone would not have caught architect
review's cycle-3 finding that entrypoint.sh's real `$INTENT` vocabulary (`fix`, not the
DAG's `new`) must be what `intent_phases()` is keyed on — a `Fix` dry run is the one that
actually exercises the `implement`/`validate`/`conformance`/`code-review`/`revise-advisory`
phase set and Layer B under a real container:**

1. Open a scratch throwaway GitHub issue on this repo (title doesn't matter, e.g. "scratch:
   #196 dry run — safe to close").
2. `docker compose --profile factory run --rm dark-factory "Refine issue #<scratch-N>"` —
   confirm the run logs one `side_effect_level=5 profile=v1 phases=refine` line, the refine
   phase completes and pushes its branch exactly as before this ticket (no behavior change
   — level 5 removes nothing), and no `side-effect guard:` denial lines appear in the log.
3. `docker compose --profile factory run --rm dark-factory "Plan issue #<scratch-N>"` —
   same check, expecting `phases=plan`.
4. `docker compose --profile factory run --rm dark-factory "Fix issue #<scratch-N>"` —
   confirm the log line reads `phases=implement validate conformance code-review
   revise-advisory` (not empty, not defaulting to level 1), the implement/validate/
   conformance/code-review phases complete exactly as before this ticket, and no
   `side-effect guard:` denial appears anywhere in the run log — this is the check that
   would have caught the intent-vocabulary bug found in architect review cycle 3.
5. Close/delete the scratch issue and its branch afterward; this step produces no code and
   is not committed.

Then, since `scripts/factory_core/adapter.py` is a Blast-Radius hotspot (see the Process
note above the task list), the implementing PR must go through operator review rather than
the automated conformance/code-review gate chain — note this explicitly in the PR
description, as #197/#198 did.

No commit for this task (verification only).

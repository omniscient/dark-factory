# Dependency-Health Loop — scheduled advisory audit, bounded fix, reviewable PR

**Issue:** #484

## Overview / Problem statement

MarketHawk's CI has been red since 2026-10-16 on `pip-audit` and `npm audit --audit-level=high`
alone — no code change caused it, yet every PR (including docs-only #903, the factory's own
#900, and every open Dependabot PR #883–#911) inherits the failure. Nothing in Dark Factory
treats "CI red because of a dependency advisory" as a distinct, actionable signal:

1. The only red-`main` detector is the `smoke-gate` hook (tsc + python import); a new CVE
   against an unchanged lockfile never latches `main-is-red`, so nothing reruns automatically.
2. Target CI runs on `pull_request` only, so GitHub's `main` branch shows green forever.
3. `scheduler.sh`'s P0 CI gate (`stage_ci_gate`, scheduler.sh:986-1011) moves *any* ticket with a
   red PR check to Blocked, without asking whether the failure predates the branch.
4. Nothing watches or rebases stale Dependabot PRs.

This has already needed two hand-filed tickets (markethawk#848, #889 — the second sat
un-opted-in for 17 days). The operator (Frank, 2026-11-07) wants a structural fix: a loop that
detects advisories, attempts a fix, and opens a reviewable PR — not another one-off ticket.

## Requirements (from Q&A)

Three architectural questions were brainstormed with a product-owner review of the codebase
(full Q&A in the pipeline comment); the resolutions below are load-bearing.

1. **Scope: this ticket ships the core detect-and-fix loop only.** The `deps-audit`/`deps-fix`
   hook contract + `templates/new-target/` template, the scheduler `deps_audit_check()`
   dispatch, `deps_fixer.py`'s container flow, and the `deps_audit:` config/adapter schema
   (items 1, 2, 3, 5 of the issue). The **Dependabot shepherd** (item 4) and **P0 CI-gate
   hygiene** (item 6) are explicitly **out of scope** for this ticket and are filed as separate
   follow-ups against the same issue family (#484) — see Alternatives/Out-of-scope below. The
   issue's own Acceptance list only exercises the core loop, and both deferred items are
   gate-adjacent changes (loosening the P0 gate's retry counter; a new Dependabot auto-merge
   route) that CLAUDE.md requires to get their own reviewed ticket rather than riding in as a
   side effect of this one. The `deps_audit:` config block therefore ships only `enabled`,
   `interval_hours`, `model`, `max_attempts`, `ci_wait_minutes`, `auto_merge` — no
   `dependabot_rebase`/`dependabot_auto_merge` knobs until the shepherd ticket exists to use them.

2. **Self-repo end-to-end testing uses fixtures, never dark-factory's own tracked
   `.factory/hooks/`.** `.factory/adapter.yaml`'s `safety.hard_exclude_paths` already lists
   `.factory/hooks/` as a human-in-the-loop, self-modification surface (`.factory/
   adapter.yaml:29`, mirrored in `config/config.yaml`'s `epic_autopilot.hard_exclude_paths:109`).
   This ticket's plan/implementation must not create or edit anything under dark-factory's own
   `.factory/hooks/`. Instead:
   - `tests/test_deps_fixer.py` drives `run_once` through a `FakeIO`, covering the full
     lifecycle (red → tracking issue filed → PR opened, ticket moved to In review → not merged
     while `auto_merge` is false and `direct-to-pr` is absent → ticket closed on the next clean
     audit), plus protected-scope refusal and attempt-cap escalation. Mirrors
     `tests/test_main_red_fixer.py`'s existing FakeIO pattern.
   - A fixture integration test builds a `mktemp -d` throwaway `CLONE_DIR` with a seeded fake
     `.factory/hooks/deps-audit` (exits non-zero with a canned advisory report) and exercises it
     through `run_hook --gate deps-audit`; the same fixture with the hook absent proves
     "inactive, rc=0, no refusal". Mirrors `tests/test_hooks.sh`'s existing `mktemp -d` pattern.
   - A scheduler dry-run test for `deps_audit_check`, modelled on
     `tests/test_scheduler_main_red_fixer.sh` (kill-switch, dedupe, throttle, `skip=open_deps_pr`).
   - `templates/new-target/.factory/hooks/deps-audit` (the install-time stub, modelled on
     `templates/new-target/.factory/hooks/smoke-gate`'s `# EDIT:` marker) **is** in scope —
     `templates/` is not a hard-excluded path.
   - A genuinely live self-repo e2e (a real `.factory/hooks/deps-audit` seeded on dark-factory
     itself) requires a human to add that hook as an out-of-band, non-blocking follow-up; it is
     not a merge condition for this ticket (see Open questions).

3. **Config/activation wiring copies the `main_red_autofix` precedent exactly.**
   `config/config.yaml`'s `main_red_autofix:` block documents hardcoded env-var defaults only —
   it is never parsed by any running code path (`entrypoint.sh`'s `_entrypoint_cfg_apply()`,
   entrypoint.sh:45-74, threads only an explicit small allowlist — `FACTORY_WIP_LIMIT`,
   `CONFLICT_RESOLUTION_AI_TIER`, `SESSION_WINDOW_*` — into the container). The new
   `deps_audit:` block follows the same pattern byte-for-byte:
   - Real kill-switch: `DEPS_AUDIT_ENABLED`, set in `.archon/.env` (loaded via
     `run-compose.yml:23-24`'s `env_file`). `config.yaml`'s `enabled: false` is documentation
     only — flipping it alone does nothing, exactly as `main_red_autofix`'s existing comment
     (config.yaml:115-117) already warns for its own block.
   - Real tunables: `DEPS_AUDIT_MODEL`, `DEPS_AUDIT_MAX_ATTEMPTS`, `DEPS_AUDIT_CI_WAIT_MINUTES`,
     `DEPS_AUDIT_INTERVAL_HOURS`, `DEPS_AUDIT_AUTO_MERGE` — read via `os.environ.get(...,
     <hardcoded default>)` in `deps_fixer.py` (mirroring `main_red_fixer.py:278-285`) and as
     bare env vars in `scheduler.sh` (mirroring `scheduler.sh:238`). None of these are added to
     `_set_cfg` or `_entrypoint_cfg_apply`'s allowlist.
   - `entrypoint.sh` gains an `INTENT=audit-deps` branch, mirroring the existing `fix-main`
     branch (entrypoint.sh:791-798): checks `DEPS_AUDIT_ENABLED`, exits 0 early if not `"true"`,
     else calls `cli.py deps-fix --once`.

4. **New required `side_effect.phase_levels` entry: `audit_deps: 5`.** The agentic fix pass
   (step 4 of the container flow, a bounded `claude -p` session) is structurally identical to
   `main_red_fixer.py`'s own agent session — a standalone `claude -p` call outside the DAG,
   never through Archon. `side_effect.py`'s `phase_level()` (side_effect.py:208-234) fail-closes
   to level 1 for any phase absent from `config.yaml`'s `side_effect.phase_levels`, and level 1
   would block the `git push`/`gh pr create` calls this flow needs via the git/gh PATH shim
   (`side_effect.py:185-191` documents exactly this failure mode for `fix-main`/`deconflict`).
   Required:
   - `scripts/factory_core/side_effect.py`: add `"audit-deps": ["audit-deps"]` to
     `_INTENT_PHASES` (side_effect.py:192-200).
   - `config/config.yaml`: add `audit_deps: 5  # entrypoint.sh's $INTENT=audit-deps -- a
     standalone claude -p session (deps_fixer.py), not a DAG node` to `side_effect.phase_levels`,
     beside `fix_main`.
   - `tests/test_side_effect.py`: extend the intent-phases parametrize with
     `("audit-deps", ["audit-deps"])` and add `audit_deps` to
     `test_config_yaml_has_side_effect_phase_levels`'s expected key list.

## Architecture / Approach

### 1. Hook contract — `.factory/hooks/deps-audit` (check-only), optional `.factory/hooks/deps-fix`

`scripts/hooks.sh`'s `run_hook` (hooks.sh:24-76) is already generic enough to need **zero code
changes** for a new hook name: its missing-hook `case` statement (hooks.sh:66-74) has a default
arm (`*) rc=0 ;;`) that no-ops for any name other than `smoke-gate` — the "absent hook ⇒ feature
inactive, no refusal" behavior the issue asks for is already the default for every hook except
`smoke-gate`. The deps-audit flow simply calls `run_hook --gate deps-audit` (and, if present,
`run_hook deps-fix`) and reads the existing env contract (`CLONE_DIR, ARTIFACTS_DIR, ISSUE_NUM,
FACTORY_REPO_SLUG`).

Contract (documented in `templates/new-target/.factory/hooks/deps-audit`, modelled on the
existing `smoke-gate` template's `# EDIT:` stub):
- Exit 0 = clean, non-zero = advisories present.
- stdout = one advisory per line, tab-separated: `ecosystem\tpackage\tinstalled\tfixed_in\tid\tseverity`.
- Must mirror the target's own CI audit steps (same ignore list) so "hook green" ⇔ "CI audit
  steps green".
- Optional `deps-fix` hook: a deterministic first pass (e.g. `npm audit fix` without `--force`,
  `pip-audit --fix`); no hook present ⇒ skip straight to the agentic pass.

### 2. Scheduler — `deps_audit_check()`

Added to `scheduler.sh` alongside `main_red_fixer_check()`/`main_red_recheck_check()`
(scheduler.sh:211-246), called from the same site as `main_red_fixer_check()` — after the
`FACTORY_WIP_LIMIT` capacity guard (scheduler.sh:1570-1579) and the `SESSION_WINDOW_PAUSED`
check, so it shares the single factory-container concurrency slot with ticket dispatch but is
exempt from ticket-level limits (`MAX_IN_PROGRESS`/`REFINE_WIP_LIMIT`), exactly like
`main_red_fixer_check`. It is independent of the `MAIN_IS_RED` gate (advisories are a distinct
signal) and runs every cycle regardless of that sentinel's state:

```sh
DEPS_AUDIT_STAMP_FILE="${SCHEDULER_STATE_DIR}/deps-audit-last-run"

is_deps_audit_running() {
  docker ps --no-trunc --format '{{.Command}}' 2>/dev/null | grep -q 'Audit deps' && return 0
  return 1
}

deps_audit_due() {
  [ -f "$DEPS_AUDIT_STAMP_FILE" ] || return 0
  local last now
  last=$(stat -c %Y "$DEPS_AUDIT_STAMP_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  [ $(( now - last )) -ge $(( ${DEPS_AUDIT_INTERVAL_HOURS:-24} * 3600 )) ]
}

has_open_deps_pr() {
  # branch prefix `deps/audit-` OR an open PR carrying identity.marker('deps_audit')
  python3 "$FACTORY_PROVIDERS_CLI" codehost find-open-pr --repo "$FACTORY_REPO_SLUG" \
    --branch-prefix "deps/audit-" >/dev/null 2>&1
}

deps_audit_check() {
  [ "${DEPS_AUDIT_ENABLED:-false}" = "true" ] || return 0
  is_deps_audit_running && return 0
  deps_audit_due || return 0
  if has_open_deps_pr; then
    echo "[$(date -u +%FT%TZ)] deps_audit=skip reason=open_deps_pr"
    return 0
  fi
  if dispatch "Audit deps"; then
    DISPATCHED="Audit deps"
    touch "$DEPS_AUDIT_STAMP_FILE"
    echo "[$(date -u +%FT%TZ)] deps_audit=dispatched interval=${DEPS_AUDIT_INTERVAL_HOURS:-24}h"
  fi
}
```

`entrypoint.sh` maps the dispatch command `"Audit deps"` to `INTENT=audit-deps` (alongside the
existing `"Fix main"|"fix main") INTENT="fix-main"` arm at entrypoint.sh:138), which — after the
`DEPS_AUDIT_ENABLED` check described in Requirement 3 — calls `cli.py deps-fix --once`.

### 3. Container flow — `scripts/factory_core/deps_fixer.py` (pure core + injected IO)

Mirrors `main_red_fixer.py`'s split exactly:

- **Pure core** (unit-testable, no IO):
  - `parse_advisories(stdout: str) -> list[dict]` — parses the hook's tab-separated lines into
    `{ecosystem, package, installed, fixed_in, id, severity}`.
  - `advisory_set_hash(advisories: list[dict]) -> str` — stable hash of the sorted `id` set, used
    to dedupe the tracking issue (a new advisory set updates the existing open issue rather than
    filing a duplicate — same intent as `smoke_gate.sh`'s single-open-regression-ticket pattern,
    `smoke_gate.sh:48-103`, but keyed on advisory-set identity rather than "at most one open").
  - `classify_scope(changed_paths, allowed, blocked)` — **reused from `main_red_fixer.py`**
    (import it directly; do not duplicate the fail-closed logic) against
    `safety.deps_allowed_paths` (new adapter key, same shape/lookup pattern as the existing
    `safety.main_red_allowed_paths` / `_main_red_allowed_paths()`, `main_red_fixer.py:25-34`).
    Default: `**/requirements*.txt`, `**/pyproject.toml`, `**/package.json`,
    `**/package-lock.json`, `**/yarn.lock`, `**/pnpm-lock.yaml`.
  - `ci_status(checks)` — **reused from `main_red_fixer.py`** unchanged.
  - `attempts_for`/`record_attempt`/`should_escalate`/`cap_escalated` — same shape as
    `main_red_fixer.py:64-85`, keyed on `advisory_set_hash` instead of a regression-issue number
    (state file `${SCHEDULER_STATE_DIR}/deps-fixer-state.json`), so a *new* advisory set resets
    the attempt counter rather than inheriting a stale cap.
  - `run_once(cfg, io, state) -> dict` — the outcome state machine:
    1. `advisories = io.run_audit_hook()`. Empty ⇒ `io.close_tracking_issue_if_open()`,
       return `{outcome: "clean"}`.
    2. `issue = io.find_or_create_tracking_issue(advisories, advisory_set_hash(advisories))`
       (labels `dependencies`, `security`; body = advisory table; identity marker
       `identity.marker('deps_audit')`).
    3. Attempt-cap check (same shape as `main_red_fixer.py:110-116`) — cap reached ⇒
       `io.escalate_cap(issue)`, return `{outcome: "escalated", reason: "cap"}`.
    4. `io.run_deterministic_fix()` (the optional `deps-fix` hook, no-op if absent) →
       `remaining = io.run_audit_hook()` again. Clean ⇒ skip to step 6.
    5. Still red ⇒ `io.apply_agent_fix(build_fix_prompt(remaining, cfg["allowed_paths"]))` — one
       bounded `claude -p` pass, model from `cfg["model"]` (default `claude-opus-5-5`),
       constrained to `Edit,Write,Read,Bash,Grep,Glob` exactly like
       `main_red_fixer.py:apply_fix` (main_red_fixer.py:188-192). For an advisory with no fixed
       version, the prompt instructs the agent to write a proposed ignore + justification into
       the PR body text (not into any CI workflow or ignore-list file) and leave that advisory's
       check red for human decision — it must **never** touch CI workflow files or audit
       ignore-list config itself (CLAUDE.md: never weaken gates as a side effect).
    6. `changed = io.changed_paths()`. Empty **and** nothing fixed deterministically ⇒
       `io.escalate_scope(issue, "no-fix")`, return `{outcome: "escalated", reason: "no-fix"}`.
    7. `scope = classify_scope(changed, cfg["allowed_paths"], cfg["blocked_paths"])`. Not
       `"allowed"` ⇒ escalate (`reason: f"scope:{scope}"`), no PR — identical to
       `main_red_fixer.py:129-132`.
    8. `io.run_tests()` (the target's own test commands, the same ones `implement` uses) —
       result included verbatim in the PR body, regardless of pass/fail (CI is the real gate;
       this is informational).
    9. `pr = io.open_pr(branch, issue, advisories, remaining, test_output)` — body: advisory
       table, what changed and why, test output, identity marker. **Never merges** here unless
       `cfg["auto_merge"]` is true **and** CI polls green (mirrors `main_red_fixer.py:136-140`
       exactly, but gated on the new `auto_merge` flag, default `false` — see Requirement 1).
       Moves the tracking issue to **In review** with the PR linked either way.
    10. `ci = io.poll_ci(pr)` (reused `poll_ci`/`ci_status` shape, `ci_wait_minutes` bound).
        `ci == "red"` ⇒ label `ready-for-human`, comment the outcome.
    11. Return `{outcome: "fixed-deterministically"|"fixed-by-agent"|"no-fix-escalate"|"cap",
        issue, pr}`.
- **LiveIO**: `run_audit_hook()` (`run_hook --gate deps-audit`, captures stdout for
  `parse_advisories`), `run_deterministic_fix()` (`run_hook deps-fix`), `find_or_create_tracking_issue`,
  `close_tracking_issue_if_open`, `apply_agent_fix`, `changed_paths`, `run_tests`, `open_pr`,
  `poll_ci`, `merge`, `escalate_cap`, `escalate_scope`, `comment`, `notify` — same shapes as
  `main_red_fixer.py`'s `LiveIO` (main_red_fixer.py:157-266), reusing its helpers
  (`_run`, `get_codehost()`, `identity.marker`) rather than re-implementing them.

### 4. Config (`config/config.yaml`)

```yaml
deps_audit:
  # kill-switch — ships OFF. Flipping `enabled` here alone does NOT turn the feature on: the
  # dispatched "Audit deps" container reads DEPS_AUDIT_ENABLED from .archon/.env, NOT this file
  # (same convention as main_red_autofix — see that block's comment).
  enabled: false
  interval_hours: 24       # env: DEPS_AUDIT_INTERVAL_HOURS
  model: claude-opus-5-5   # env: DEPS_AUDIT_MODEL
  max_attempts: 3          # env: DEPS_AUDIT_MAX_ATTEMPTS
  ci_wait_minutes: 20       # env: DEPS_AUDIT_CI_WAIT_MINUTES
  auto_merge: false        # env: DEPS_AUDIT_AUTO_MERGE — merge-on-green only when true

side_effect:
  phase_levels:
    # ... existing entries unchanged ...
    audit_deps: 5  # entrypoint.sh's $INTENT=audit-deps -- a standalone claude -p session
                   # (deps_fixer.py), not a DAG node
```

### 5. Adapter (`.factory/adapter.yaml` schema, `scripts/factory_core/adapter_defaults.py`)

New `safety.deps_allowed_paths` key, read at use-time exactly like `main_red_allowed_paths`
(`_main_red_allowed_paths()`, main_red_fixer.py:25-34) — add a sibling `_deps_allowed_paths()`
helper (or generalize the existing one to take the adapter key as a parameter; implementer's
choice, not load-bearing). Default (in `adapter_defaults.py::DEFAULTS["safety"]`):
`["**/requirements*.txt", "**/pyproject.toml", "**/package.json", "**/package-lock.json",
"**/yarn.lock", "**/pnpm-lock.yaml"]`. `hard_exclude_paths` is **not** widened — `.github/
workflows/**` stays blocked for the agent (CLAUDE.md gate-weakening rule); the agentic pass's
own prompt additionally instructs it never to touch CI config (Architecture §3 step 5).

### 6. Identity marker

Add `"deps_audit": "*{} Dependency-Health Loop*"` to `scripts/factory_core/identity.py`'s
`_MARKERS` dict (identity.py:22-27), alongside `"main_red"`. Used on the tracking issue, the PR
body, and every comment this flow posts, exactly like `main_red`'s existing usage.

## Alternatives considered

- **Ship the full six-item design in one ticket.** Rejected (Requirement 1): the governance
  risk of bundling two gate-adjacent changes (P0 retry-counter relaxation, Dependabot
  auto-merge) into a feature ticket outweighs the convenience of one PR, and the issue's own
  Acceptance list doesn't require it.
- **Auto-merge on green by default**, matching `main_red_fixer.py`'s own behavior. Rejected —
  the issue explicitly asks for PR-for-approval as the default, with `auto_merge`/`direct-to-pr`
  as the only opt-in route; dependency bumps (unlike pipeline-recovery fixes) don't carry the
  same urgency justification that earned `main_red_fixer` its default auto-merge.
- **Duplicate `classify_scope`/`ci_status` inside `deps_fixer.py`** instead of importing them
  from `main_red_fixer.py`. Rejected — identical logic, no reason to drift; import directly.

## Open questions (non-blocking)

- A genuinely live self-repo end-to-end run (real seeded `.factory/hooks/deps-audit` on
  dark-factory itself, not a fixture) requires a human to add that hook to this repo's tracked
  `.factory/hooks/` — a hard-excluded, human-reviewed surface. Tracked as a follow-up; not a
  merge condition for this ticket (Requirement 2).
- Dependabot shepherd (issue item 4) and P0 CI-gate hygiene (issue item 6) are deferred to
  separate follow-up tickets against #484 (Requirement 1) — not authored here.
- Exact `find-or-create tracking issue` / dedupe-on-advisory-hash implementation detail (how a
  changed advisory set updates vs. replaces the existing open issue's body) is left to the plan
  phase; the state-machine contract above (Architecture §3) is the binding requirement.

## Assumptions (flagged)

- `safety.deps_allowed_paths` follows the exact lookup/fallback pattern of the existing
  `safety.main_red_allowed_paths` (adapter-first, `adapter_defaults.py` fallback on any read
  error) — no new adapter-loading mechanism is introduced.
- The deterministic `deps-fix` hook is optional; its absence is not an error, and the flow
  proceeds straight to the agentic pass (Architecture §3, step 4).
- `run_tests()` in the container flow reuses whatever test-invocation mechanism `implement`
  already uses for the target (not re-specified here — out of this ticket's scope to redesign).

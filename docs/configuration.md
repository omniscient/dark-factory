# Configuration reference

One reference for every configuration knob in Dark Factory: where it is set, which code reads
it, and what overrides what. Everything here was drafted from the code on `origin/main`
(commit `254c9d2`), not from the comments in `config/config.yaml`, and was revised against
`64cd44a` for [PR #519](https://github.com/omniscient/dark-factory/pull/519) (per-scenario
budget files), [PR #524](https://github.com/omniscient/dark-factory/pull/524) (the
autopilot adapter lookup) and [PR #525](https://github.com/omniscient/dark-factory/pull/525)
(issue comments and the board read through the provider CLI). Where a comment disagrees
with the code, this document follows the code and the disagreement is listed in
[Discrepancies found](#discrepancies-found).

Related docs: [`docs/scheduler.md`](scheduler.md) (poll loop and dispatch decisions),
[`docs/run-lifecycle.md`](run-lifecycle.md) (what a run container does),
[`docs/features.md`](features.md) (feature switches and what they do),
[`docs/dark-factory-token-optimization.md`](dark-factory-token-optimization.md) (token budget
semantics), [`docs/adapter-authoring-guide.md`](adapter-authoring-guide.md#the-adapter-file-what-factoryadapteryaml-validation-accepts)
(the `.factory/adapter.yaml` keys and validation rules; the rest of that guide covers provider
adapters), [`docs/onboarding-new-target.md`](onboarding-new-target.md).

Conventions used in the tables:

- **Reader** is `path:line` of the code that actually consumes the value. "Agent-read" means the
  value is read by an LLM phase agent following instructions in a `commands/*.md` file or a
  workflow prompt, not by a script.
- **Env override** names an environment variable only if the reading code honours it. The
  scope says where it is effective: **scheduler** (the `backlog-scheduler` container),
  **run** (dispatched run containers), or **both**.
- **Adapter** says whether `.factory/adapter.yaml` can override the key (see
  [Layer 3](#layer-3-the-target-adapter-factoryadapteryaml)).
- **unread** marks a key nothing reads. The grep used is given in each section's verification
  note.

## The four layers and who wins

There are two different processes with two different views of configuration. Most confusion
comes from mixing them up.

| Process | Config file it reads | Env file it gets | Source |
|---|---|---|---|
| Scheduler (`backlog-scheduler`) | first existing of `/workspace/project/config/config.yaml` (the bind-mounted target checkout), `/opt/dark-factory/config/config.yaml` (baked), `/opt/refinement-skills/config.yaml` (legacy). One file, no merging. Resolved by `factory_core.effective_config`'s scheduler mode into `$SCHEDULER_STATE_DIR/run-config.{json,env}`. | `deploy/instance.env` via compose `env_file:` | `scripts/factory_core/effective_config.py:33-37`, `scheduler.sh:35-42` (`read_config`), `deploy/docker-compose.yml` |
| Run container (`entrypoint.sh` and everything it starts) | `<clone>/.claude/skills/refinement/config.yaml`: the target's committed copy if it has one, otherwise a copy materialised from the baked file plus the adapter's `token_optimization` block. Side-effect levels and the blast-radius gate additionally read the baked file directly. The same resolution is also written to `$ARTIFACTS_DIR/run-config.{json,env}`; `budget_gate.sh` and `architecture_slice.py` read `run-config.json` through `FACTORY_RUN_CONFIG_JSON`, the entrypoint knobs come from `run-config.env`. | `<PROJECT_DIR>/.archon/.env` (copied by the scheduler to `/opt/dark-factory/.archon/.env`), plus `run-compose.yml` `environment:`, plus `-e NAME` flags from `dispatch()` | `entrypoint.sh:61,865`, `scripts/factory_core/effective_config.py`, `scheduler.sh:47-59,388-408` |

```
SCHEDULER PROCESS                                RUN CONTAINER (one per dispatch)
------------------                               --------------------------------
config.yaml   (target checkout > baked)           config.yaml   (clone's committed copy, else
   |                                                             baked + adapter token_optimization)
   v  run-config.env -> apply_run_config_env          |
env var already set (instance.env)? -> env WINS       v  readers use the file directly (python/yq)
   |                                                  |   a few knobs: env var checked FIRST
   v                                                  v
scheduler.sh / scripts/*.py in scheduler          run-time env, highest first:
                                                       dispatch() `-e NAME` forwards (provider vars)
   dispatch()  ---- provider vars, non-empty ---->     > run-compose.yml `environment:`
   (instance.env is NOT read by run containers)        > .archon/.env (env_file) > code defaults
```

Precedence, highest first:

| # | Layer | Applies to | Notes |
|---|---|---|---|
| 1 | Process environment | Scheduler: `instance.env`. Run: `dispatch()` `-e` flags, then `run-compose.yml` `environment:`, then `.archon/.env` (`scripts/provider_env.sh:9-13`). | For the 33 config keys the scheduler takes from `run-config.env`, a variable that is *set at all* (even to the empty string) in the scheduler's environment beats the config value (`scripts/run_config_env.sh:47-53`); a differing value is logged as `[config] VAR=... (env override; ...)`. In run containers each reader decides; most read the config file only, a few check an env var first. |
| 2 | Target-committed config | Run: `<clone>/.claude/skills/refinement/config.yaml`. Scheduler: `<PROJECT_DIR>/config/config.yaml`. | When the target commits the run-side copy, `effective_config` writes nothing into the clone, `run-config.json` is that file alone, and it wins over the baked file **and over the adapter's `token_optimization` overlay** (`effective_config.py:287-320`). On the scheduler side the target's file replaces the baked one entirely (no merging). |
| 3 | Adapter (`.factory/adapter.yaml`) | Run only. The `token_optimization` block is merged over the baked config when the clone has no committed config. `safety`, `components`, `memory_routing`, `deconflict` are read by their own consumers, not merged into config. | See Layer 3. The adapter does **not** override scheduler policy keys. |
| 4 | Baked `config/config.yaml` | Both, as the fallback | Changes need an image rebuild and publish, and for the scheduler a recreate. |
| 5 | Code defaults | Wherever a reader has a `.get(key, default)` / `${VAR:-default}` | Listed per key as "code default" when it differs from the YAML. |

Two consequences worth knowing:

- Editing `config/config.yaml` in a repo checkout that is bind-mounted as `/workspace/project`
  (the self-target) changes the **scheduler's** view after a scheduler restart, with no image
  rebuild, but does not change what run containers see: they use the baked copy (or the
  clone's own copy).
- `deploy/instance.env.example` sets `FACTORY_WIP_LIMIT=1` and `POLL_INTERVAL=60` uncommented.
  Copied as-is, those override `scheduler.factory_wip_limit` and `scheduler.poll_interval`
  in `config.yaml` permanently.

## Layer 1: `config/config.yaml`

The file is baked at `/opt/dark-factory/config/config.yaml` (`Dockerfile:152`). 110 leaf keys,
documented below section by section. The scheduler's env projection is `apply_run_config_env`
(`scripts/run_config_env.sh`); the list of exported keys is `ENV_WHITELIST`
(`scripts/factory_core/effective_config.py:43-80`).

Every key in these tables is written out in full (`token_optimization.budgets.refine`, not
`budgets.refine`): `scripts/docs_refs_check.py` matches them against the leaf keys of
`config/config.yaml` in CI, in both directions, so a key added, renamed or removed without a
table row fails the pull request.

Verification note for the "unread" claims: I ran
`grep -rnE "<key>|<ENV_NAME>" scheduler.sh entrypoint.sh scripts workflows commands evals bench run-compose.yml Dockerfile`
for every key and every candidate environment variable, and followed every hit to the reading
code. Keys with no hit other than their own definition are marked **unread**.

### `scheduler`

Env override works in the scheduler only (via `read_config`), except the three `session_window_*`
keys, which are read by the run container (see Discrepancies D1).

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `scheduler.poll_interval` | `60` | seconds between board polls | `scheduler.sh:1549,1578,1660` | `POLL_INTERVAL` (scheduler) | no |
| `scheduler.max_retries` | `3` | per-issue retry ceiling for implement, conflict-resolve and Close/merge dispatches (the `N:close` counter, via `dispatch_close`) before the breaker trips to Blocked (refine and plan use the env-only `REFINE_MAX_RETRIES`, see Layer 4) | `scheduler.sh:372,1082,1094,1101,1238,1249` | `MAX_RETRIES` (scheduler) | no |
| `scheduler.rate_limit_floor` | `200` | pause polling while the GraphQL remaining budget is at or below this | `scheduler.sh:70-76` (`check_rate_limit`) | `RATE_LIMIT_FLOOR` (scheduler) | no |
| `scheduler.factory_wip_limit` | `1` | max concurrent run containers (scheduler dispatch guard) | `scheduler.sh:128-129,1572-1577` | `FACTORY_WIP_LIMIT` (scheduler). The run container has its own guard at `entrypoint.sh:169-175` that reads `FACTORY_WIP_LIMIT` from env only, default 1; set it in `.archon/.env` too or a second concurrent run exits with an error | no |
| `scheduler.main_red_recheck_enabled` | `true` | periodically dispatch "Recheck main" so a red-main latch can clear | `scheduler.sh:156` | `MAIN_RED_RECHECK_ENABLED` (scheduler) | no |
| `scheduler.main_red_recheck_minutes` | `20` | throttle between recheck dispatches | `scheduler.sh:146,162` | `MAIN_RED_RECHECK_MINUTES` (scheduler) | no |
| `scheduler.blocked_rescue_enabled` | `true` | promote a Blocked ticket whose PR is green and mergeable instead of re-running it | `scheduler.sh:1019` (code default `true`) | `BLOCKED_RESCUE_ENABLED` (scheduler) | no |
| `scheduler.session_window_backoff_enabled` | `true` | pause and resume around Claude session-window exhaustion instead of sleeping | `entrypoint.sh:280` (bootstrap default `:37`) | `SESSION_WINDOW_BACKOFF_ENABLED` (run, via `.archon/.env`); the config value cannot change it (D1) | no |
| `scheduler.session_window_buffer_minutes` | `5` | minutes added to the parsed reset time | `entrypoint.sh:288` (bootstrap default `:38`) | `SESSION_WINDOW_BUFFER_MINUTES` (run); config value inert (D1) | no |
| `scheduler.session_window_fallback_minutes` | `30` | pause length when the reset time cannot be parsed | `entrypoint.sh:289` (bootstrap default `:39`) | `SESSION_WINDOW_FALLBACK_MINUTES` (run); config value inert (D1) | no |

### `refine` and `plan`

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `refine.wip_limit` | `2` | max concurrent refine and plan runs | `scheduler.sh:1283,1291,1347,1358` | `REFINE_WIP_LIMIT` (scheduler) | no |
| `refine.skip_labels` | `needs-discussion, epic, spec-pending-review` | nothing: **unread**. The real list is the hardcoded `REFINE_SKIP_LABELS` (`scheduler.sh:23`) | none | none | no |
| `refine.min_issue_body_length` | `20` | **unread** | none | none | no |
| `plan.skip_labels` | `needs-discussion, epic, plan-pending-review` | **unread**; see `REFINE_SKIP_LABELS` | none | none | no |

### `direct_to_pr`

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `direct_to_pr.label` | `direct-to-pr` | label that enables grace-timer auto-advance and end-gate auto-merge | `scripts/scheduler_lib.sh:32` (matched with `grep -qi`, so it is a regex); also `scripts/factory_core/handoff.py:401` (plain substring, only in a process that imports `handoff`; nothing in `scheduler.sh`/`entrypoint.sh` does) | `DIRECT_TO_PR_LABEL` (scheduler) | no |
| `direct_to_pr.spec_grace_minutes` | `30` | minutes before a `spec-pending-review` ticket on a direct-to-pr issue auto-advances; `0` = next poll | `scheduler.sh:273-279`; the refine agent also reads it from the run's config to print the countdown (`commands/dark-factory-refine.md:231`, agent-read) | `SPEC_GRACE_MINUTES` (scheduler only; the agent's countdown ignores it) | no |
| `direct_to_pr.plan_grace_minutes` | `30` | same for `plan-pending-review` | `scheduler.sh:314-320`; `commands/dark-factory-plan.md:298` (agent-read) | `PLAN_GRACE_MINUTES` (scheduler only) | no |

The `direct-to-pr` literal is also hardcoded in `commands/dark-factory-refine.md:230` and
`commands/dark-factory-plan.md:297`, so renaming the label in config does not change those checks.

### `conformance` and `code_review`

All keys here are read by phase agents following the command files, from the run's config
(`.claude/skills/refinement/config.yaml` in the clone). None has an env override except
`shadow_model`.

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `conformance.enabled` | `true` | false = skip the conformance gate and the plan-phase plan-vs-spec check | `commands/dark-factory-conformance.md:60`, `commands/dark-factory-plan.md:174` (agent-read); `workflows/archon-dark-factory.yaml:1649` (report text only) | none | no |
| `conformance.max_reconcile_cycles` | `3` | reconcile cycles before escalating | `commands/dark-factory-conformance.md:68`, `commands/dark-factory-plan.md:179` (agent-read) | none | no |
| `conformance.block_on_material` | `true` | material findings block | `commands/dark-factory-conformance.md:69` (agent-read) | none | no |
| `conformance.scope_enforcement` | `true` | detect out-of-scope changes | `commands/dark-factory-conformance.md:70` (agent-read) | none | no |
| `conformance.excise_out_of_scope` | `true` | revert out-of-scope changes (false = file a ticket only) | `commands/dark-factory-conformance.md:71` (agent-read) | none | no |
| `conformance.backlog_label` | `scope-spillover` | label on auto-created spillover tickets | `commands/dark-factory-conformance.md:72` (agent-read) | none | no |
| `conformance.shadow_model` | `claude-fable-5-1` | shadow reviewer model; the env var, when set (even empty), wins and an empty value spawns no shadow | `commands/dark-factory-conformance.md:76-77`, `commands/dark-factory-plan.md:186-187` (agent-read, python reads the clone's config; env expansion is `${CONFORMANCE_SHADOW_MODEL-default}`) | `CONFORMANCE_SHADOW_MODEL` (run, via `.archon/.env`) | no |
| `code_review.enabled` | `true` | false = skip Gate 3 | `commands/dark-factory-code-review.md:53`; `workflows/archon-dark-factory.yaml:1690` (report text) | none | no |
| `code_review.block_threshold` | `high` | severity at or above which findings block | `commands/dark-factory-code-review.md:57`; `scripts/code_review_payload.py:152-155,200-206` | none | no |
| `code_review.fail_open` | `true` | reviewer error becomes advisory | `commands/dark-factory-code-review.md:58` (agent-read) | none | no |
| `code_review.max_findings` | `50` | cap on inline comments | `commands/dark-factory-code-review.md:59`; `scripts/code_review_payload.py:152,167` | none | no |
| `code_review.severity_order` | `low, medium, high, critical` | ascending severity ranking | `commands/dark-factory-code-review.md:60-64` (`yq`), passed as `--severity-order` to `scripts/code_review_payload.py:200` | none | no |

### `docs_conformance`

Gate 2.5 (#535). Read from the run's config (`.claude/skills/refinement/config.yaml` in the
clone) by the `docs-conformance` command and, for `mode`, also by the
`docs-conformance-gate` node. An unreadable config, a missing block, or any `mode` other
than `enforce` means `enabled: true` and `observe`.

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `docs_conformance.enabled` | `true` | false = the command writes `STATUS: SKIPPED`, which passes in both modes | `commands/dark-factory-docs-conformance.md:68,72-75` (agent-read) | none | no |
| `docs_conformance.mode` | `observe` | `observe`: record the verdict and post an informational comment on a mismatch; the docs verdict never blocks. `enforce`: a BLOCKED, `ERROR`, missing or unparseable verdict blocks `push-and-pr`, with the blocking comment, a Blocked board move and `needs-discussion`; it also blocks when `git status` changed during the `docs-conformance` node, which `observe` only logs. Outside this key: a commit or a staged change made during that node stops the push in every mode. Turning `enforce` on is a separate, reviewed change ([#536](https://github.com/omniscient/dark-factory/issues/536)) | `commands/dark-factory-docs-conformance.md:68,213` (agent-read); `workflows/archon-dark-factory.yaml:1300-1301` (`docs-conformance-gate`), passed to `scripts/docs_verdict_gate_check.sh` | none | no |

### `preview`, `conflict_resolution`

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `preview.enabled` | `true` | false = always build the preview | `workflows/archon-dark-factory.yaml:841-842` (the `classify-preview` prompt tells the agent to read it; agent-read) | none | no |
| `preview.model` | `haiku` | **unread**. The classifier model is hardcoded `model: haiku` at `workflows/archon-dark-factory.yaml:870` | none | none | no |
| `conflict_resolution.enabled` | `true` | false = disable the P1.5 conflict scanner | `scheduler.sh:1067` (code default `true`) | `CONFLICT_RESOLUTION_ENABLED` (scheduler) | no |
| `conflict_resolution.ai_tier` | `true` | intended: false = mechanical (Tier 1) resolution only. **Not honoured**: see D2 | `entrypoint.sh:35` sets the variable and that bootstrap value always wins (the `CONFLICT_RESOLUTION_AI_TIER` row of `ENV_WHITELIST`, `effective_config.py:77`, is only compared with it and logged when it differs, see D1), but nothing consumes it: the only call (`entrypoint.sh:775`) never passes `--no-ai-tier` (`scripts/factory_core/cli.py:40,364`) and `scripts/factory_core/deconflict.py` reads no env | `CONFLICT_RESOLUTION_AI_TIER` is set but never read (run) | no |

### `blast_radius`

Read by `scripts/gate_blast_radius.py`. `load_config` (`:98-125`) layers the **baked** block
(`/opt/dark-factory/config/config.yaml`, or `FACTORY_CONFIG_PATH`) under the block committed at
the PR's **base ref** (`git show <base-ref>:.claude/skills/refinement/config.yaml`), key by key.
It never reads the working tree, so a PR cannot loosen its own gate. No env overrides.

| Key | Default | Controls | Reader | Adapter |
|---|---|---|---|---|
| `blast_radius.enabled` | `true` | kill-switch for the gate | `scripts/gate_blast_radius.py:515` | no |
| `blast_radius.hotspot_score_floor` | `5.0` | codeindex score at or above which a touched file is a hotspot | `scripts/gate_blast_radius.py:517`; `scripts/diff_rank.py:138` (from the clone's config) | no |
| `blast_radius.size_budget_lines` | `400` | added plus deleted line threshold (see the YAML note: 0 does not disable it) | `scripts/gate_blast_radius.py:518,549` | no |
| `blast_radius.size_budget_blocks` | `false` | true = size alone blocks, false = advisory | `scripts/gate_blast_radius.py:519,549` | no |

### `dispatch_ceiling`

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `dispatch_ceiling.enabled` | `true` | kill-switch | `scheduler.sh:308,1182` | `DISPATCH_CEILING_ENABLED` (scheduler) | no |
| `dispatch_ceiling.label` | `above-ceiling` | label applied when parked | `scheduler.sh:1186,1198`; `scripts/scheduler_lib.sh:57` | `ABOVE_CEILING_LABEL` (scheduler) | no |
| `dispatch_ceiling.keywords` | `migration\|migrate\|performance\|perf\|architectur\|refactor` | title regex that parks `size: M` tickets (`size: XL` always parks, `L`/`S`/unlabelled never) | `scripts/scheduler_lib.sh:41-52`; also `scripts/architecture_slice.py:123-124,299` (from the clone's config; the adapter's `safety.dispatch_ceiling_keywords` only supplies the fallback there, via `adapter_defaults`, not via the adapter file) | `ABOVE_CEILING_KEYWORDS` (scheduler only; writing it to `.archon/.env` does not reach the scheduler, see D5) | no |

### `epic_autopilot`

Runs in the scheduler process (`stage_epic_autopilot`, `scheduler.sh:1410-1416`, which calls
`factory_core/cli.py epic-autopilot --once` -> `scripts/factory_core/epic_autopilot.py:main_once`,
lines 582-607). `main_once` reads **only env vars** (the code defaults below), which `read_config`
fills from config. So all env overrides are scheduler-scoped and the only way a config value
reaches the reader is through `read_config`.

| Key | YAML default | Code default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|---|
| `epic_autopilot.enabled` | `false` | `false` | master kill-switch | `scheduler.sh:1411` | `EPIC_AUTOPILOT_ENABLED` (scheduler) | no |
| `epic_autopilot.model` | `claude-opus-5-5` | same | reviewer model | `epic_autopilot.py:675` | `EPIC_AUTOPILOT_MODEL` (scheduler) | no |
| `epic_autopilot.daily_cap` | `5` | `5` | max autonomous advances per UTC day | `epic_autopilot.py:674` | `EPIC_AUTOPILOT_DAILY_CAP` (scheduler) | no |
| `epic_autopilot.confidence_floor` | `0.7` | `0.7` | minimum confidence to ADVANCE | `epic_autopilot.py:673` | `EPIC_AUTOPILOT_CONFIDENCE_FLOOR` (scheduler) | no |
| `epic_autopilot.hold_ttl_hours` | `24` | `24` | re-review a cached HOLD after this long | `epic_autopilot.py:668` | `EPIC_AUTOPILOT_HOLD_TTL_HOURS` (scheduler) | no |
| `epic_autopilot.size_ceiling` | `XL` | `XL` | drop only at or above this size | `epic_autopilot.py:665` | `EPIC_AUTOPILOT_SIZE_CEILING` (scheduler) | no |
| `epic_autopilot.start_epics` | `true` | `true` | promote the next Ready epic when no child is advanceable | `epic_autopilot.py:669` | `EPIC_AUTOPILOT_START_EPICS` (scheduler) | no |
| `epic_autopilot.allow_self_improvement` | `true` | `false` (fail closed) | factory-self work is autopilot-eligible | `epic_autopilot.py:670-672` | `EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT` (scheduler) | no |
| `epic_autopilot.sensitive_keywords` | trading/auth regex | adapter default | ticket text matching this regex is excluded | `epic_autopilot.py:397-410,666-667`: the **union** of the env/config value and the adapter's `safety.sensitive_keywords`, never narrower than either | `EPIC_AUTOPILOT_SENSITIVE_KEYWORDS` (scheduler) | yes, additively: `safety.sensitive_keywords` is unioned in. The adapter is read from `_target_dir()` (`epic_autopilot.py:427`): `CLONE_DIR` if set, else `/workspace/project` when it holds `.factory/adapter.yaml`, else `.` ([PR #524](https://github.com/omniscient/dark-factory/pull/524)). The scheduler sets no `CLONE_DIR`, so it reads the mounted target's adapter; before that PR it resolved to `adapter_defaults` (discrepancy D8, scheduler oddity 11) |
| `epic_autopilot.opt_out_label` | `no-autopilot` | `no-autopilot` | per-ticket veto label | `epic_autopilot.py:664` | `EPIC_AUTOPILOT_OPT_OUT_LABEL` (scheduler) | no |
| `epic_autopilot.hard_exclude_paths` | 15-entry list | `adapter_defaults` list | spec target paths that exclude a ticket | `epic_autopilot.py:442-462` reads the adapter first (`safety.hard_exclude_paths`, which always resolves to a list, defaults included) and only then the first config file with a non-empty list (`effective_config.first_match_list`, same file order). The config key is therefore reached only if the adapter import raises. `main_once` passes `_effective_exclude_paths` (`:383`), the **union** of that lookup from `CLONE_DIR` (or `.`) and from `_target_dir()`, so reading the target's adapter never drops an exclusion. Also read by `scripts/architecture_slice.py:131-134` (the run's `run-config.json`) for the full-doc redaction fallback | none | yes: the adapter list **replaces** the default list within one lookup (lists replace, see Layer 3); the autopilot then unions the lookups described in the Reader column |

### `main_red_autofix`

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `main_red_autofix.enabled` | `false` | kill-switch for the auto-fixer | two readers with separate env: the scheduler dispatches "Fix main" only if `MAIN_RED_AUTOFIX_ENABLED=true` (`scheduler.sh:182`), and the run container exits at once unless **its** `MAIN_RED_AUTOFIX_ENABLED` is `true` (`entrypoint.sh:937`) | `MAIN_RED_AUTOFIX_ENABLED` (both; the run side only from `.archon/.env`, it is not forwarded) | no |
| `main_red_autofix.model` | `claude-opus-5-5` | fixer model | `scripts/factory_core/main_red_fixer.py:289` (env, then code default `claude-opus-5-5`); the config value never reaches it (D3) | `MAIN_RED_AUTOFIX_MODEL` (run only) | no |
| `main_red_autofix.max_attempts` | `3` | fix to CI cycles per red event | `main_red_fixer.py:288` (env, code default `3`); config value never reaches it (D3) | `MAIN_RED_AUTOFIX_MAX_ATTEMPTS` (run only) | no |
| `main_red_autofix.throttle_minutes` | `15` | minimum minutes between fixer dispatches | `scheduler.sh:178` (code default 15) | `MAIN_RED_AUTOFIX_THROTTLE_MIN` (scheduler) | no |
| `main_red_autofix.ci_wait_minutes` | `20` | bounded wait for branch CI | `main_red_fixer.py:290` (env, code default `20`); not in `ENV_WHITELIST` at all (D3) | `MAIN_RED_AUTOFIX_CI_WAIT_MINUTES` (run only) | no |

`MAIN_RED_AUTOFIX_AGENT_TIMEOUT` (`main_red_fixer.py:291`, default 1200 s) has no config key.

### `deps_audit`

Dependency-health loop (#484). Every key in this block is **documentation only**: no code path
reads `config/config.yaml` for it, and none of the variables is in `ENV_WHITELIST` (`effective_config.py`). The
loop has two halves with separate environments, and both must be set to turn it on.

| Key | Default | Controls | Reader | Env override (scope) | Adapter |
|---|---|---|---|---|---|
| `deps_audit.enabled` | `false` | kill-switch for the loop | **unread**. The scheduler dispatches `Audit deps` only if `DEPS_AUDIT_ENABLED=true` in its own environment (`deps_audit_check` in `scheduler.sh`), and the run container exits at once unless **its** `DEPS_AUDIT_ENABLED` is `true` (`entrypoint.sh`, the `audit-deps` branch) | `DEPS_AUDIT_ENABLED` (scheduler: `deploy/instance.env`; run: `.archon/.env`; neither side forwards it, set both) | no |
| `deps_audit.interval_hours` | `24` | minimum hours between `Audit deps` dispatches | **unread**; `deps_audit_due` in `scheduler.sh` (env, code default 24; an empty, non-numeric, zero or longer-than-4-digit value also means 24; the value is read as base 10, so `08` is 8) | `DEPS_AUDIT_INTERVAL_HOURS` (scheduler, `deploy/instance.env`) | no |
| `deps_audit.model` | `claude-opus-5-5` | fix-agent model | **unread**; `scripts/factory_core/deps_fixer.py` `main_once` (env, code default) | `DEPS_AUDIT_MODEL` (run only) | no |
| `deps_audit.max_attempts` | `3` | fix attempts per advisory set before `ready-for-human` | **unread**; `deps_fixer.py` `main_once` (env, code default `3`) | `DEPS_AUDIT_MAX_ATTEMPTS` (run only) | no |
| `deps_audit.ci_wait_minutes` | `20` | bounded wait for the deps PR's CI | **unread**; `deps_fixer.py` `main_once` (env, code default `20`) | `DEPS_AUDIT_CI_WAIT_MINUTES` (run only) | no |
| `deps_audit.auto_merge` | `false` | merge the deps PR on green CI without approval | **unread**; `deps_fixer.py` `main_once` (env, code default `false`) | `DEPS_AUDIT_AUTO_MERGE` (run only) | `safety.deps_allowed_paths` bounds what the fix may touch |

### `side_effect.phase_levels`

Each phase starts at `5`. Reader: `scripts/factory_core/side_effect.py:209-231` (`phase_level`), called
by `entrypoint.sh:61-66` (`_compute_side_effect_level`) against the **baked** config only
(`FACTORY_CONFIG_PATH`, default `/opt/dark-factory/config/config.yaml`): never the clone's
copy or the adapter, by design. The container's level is the **maximum** over the phases its
intent runs (`side_effect.py:273-277`, with the intent to phase map at `:192-206`); a missing,
bool, non-int or out-of-range value falls back to level 1 (`side_effect.py:161-166`).

Env override: `SIDE_EFFECT_LEVEL_<PHASE>` (phase upper-cased, hyphens to underscores, e.g.
`SIDE_EFFECT_LEVEL_CODE_REVIEW`), honoured in **run** containers (`side_effect.py:215-222`) via
`.archon/.env`; not in the scheduler. An invalid value is ignored with a warning. Adapter:
no (`loops[].side_effect_level` in the adapter is a separate setting for adapter-declared loops).

| Keys | Default | Phase run by intent (`side_effect.py:192-204`) |
|---|---|---|
| `side_effect.phase_levels.refine` | `5` | `refine` |
| `side_effect.phase_levels.plan` | `5` | `plan` |
| `side_effect.phase_levels.implement` | `5` | `fix`, `continue` |
| `side_effect.phase_levels.validate` | `5` | `fix`, `continue` |
| `side_effect.phase_levels.conformance` | `5` | `fix`, `continue` |
| `side_effect.phase_levels.code_review` | `5` | `fix`, `continue` |
| `side_effect.phase_levels.revise_advisory` | `5` | `fix`, `continue` |
| `side_effect.phase_levels.deconflict` | `5` | `deconflict` |
| `side_effect.phase_levels.fix_main` | `5` | `fix-main` |
| `side_effect.phase_levels.audit_deps` | `2` | `audit-deps` |

### `token_optimization`

See [`docs/dark-factory-token-optimization.md`](dark-factory-token-optimization.md) for what
the budgets mean. Everything here is read in **run** containers from the run's config file.
Adapter: yes for the whole block (`token_optimization` is the one tunable block,
`effective_config.py:20`), subject to the clone-copy rule in Layer 3. The four
`TOKEN_OPTIMIZATION_*_ENABLED` variables are exported by the scheduler's `read_config`
(the four `TOKEN_OPTIMIZATION_*_ENABLED` rows of `ENV_WHITELIST`, `effective_config.py:73-76`) but every reader runs in a run container, so those exports reach
nothing; set the variable in `.archon/.env` for it to matter.

`budget_gate.sh` (`scripts/budget_gate.sh:40-62`) reads `enforce_budgets`, `enforce.<scenario>`,
`budgets.<scenario>` and `default_budget_tokens` from `run-config.json`, derives `observe` or `enforce` mode and writes
`export TOKEN_OPTIMIZATION_*_MAX_TOKENS=N` lines to `token-opt-caps.env`
(`scripts/budget_enforce.py:316-334`). It reads the scenario's own
`context-budget.<scenario>.json` (one file per scenario since
[PR #519](https://github.com/omniscient/dark-factory/pull/519); the measuring nodes write
them, and the cost report aggregates them); they are run artifacts, not settings. The caps
file is what the later phases source
(`scripts/load_memory_context.sh:25`, `commands/dark-factory-conformance.md:208`,
`commands/dark-factory-docs-conformance.md:97`, `commands/dark-factory-code-review.md:110`).
Scenarios are `refine`, `plan`, `implement`, `conformance`, `docs-conformance`, `code-review`
(`workflows/archon-dark-factory.yaml:425,465,739,1156,1246,1537`).

| Key | Default | Controls | Reader | Env override (scope) |
|---|---|---|---|---|
| `token_optimization.enforce_budgets` | `true` | global switch: enforce only for scenarios whose `enforce.<s>` is also true | `scripts/budget_gate.sh:50` (`run-config.json`) | `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false\|0\|no` forces observe, can never force enforce (`budget_gate.sh:56-57`) (run) |
| `token_optimization.default_budget_tokens` | `30000` | budget when `budgets.<s>` is absent | `scripts/budget_gate.sh:52` (`run-config.json`); `evals/token_opt_eval.py:282-298` | none |
| `token_optimization.budgets.refine` | `30000` | per-scenario token budget | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.budgets.plan` | `30000` | same | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.budgets.implement` | `30000` | same | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.budgets.conformance` | `22000` | same | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.budgets.code-review` | `22000` | same | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.budgets.docs-conformance` | `22000` | same | `scripts/budget_gate.sh:52` (`run-config.json`) | none |
| `token_optimization.enforce.refine` | `true` | enforce for the scenario | `scripts/budget_gate.sh:51` (`run-config.json`) | none (only the global kill-switch above) |
| `token_optimization.enforce.plan` | `true` | same | `scripts/budget_gate.sh:51` (`run-config.json`) | none (only the global kill-switch above) |
| `token_optimization.enforce.conformance` | `true` | same | `scripts/budget_gate.sh:51` (`run-config.json`) | none (only the global kill-switch above) |
| `token_optimization.enforce.code-review` | `true` | same | `scripts/budget_gate.sh:51` (`run-config.json`) | none (only the global kill-switch above) |
| `token_optimization.enforce.implement` | `false` | same | `scripts/budget_gate.sh:51` (`run-config.json`) | none |
| `token_optimization.enforce.docs-conformance` | `false` | same | `scripts/budget_gate.sh:51` (`run-config.json`) | none |
| `token_optimization.issue_context.reserve_tokens` | `2000` | floor reserved for issue context | `scripts/budget_enforce.py:137,183` | none |
| `token_optimization.architecture.enabled` | `true` | false = skip slicing, load full ARCHITECTURE.md | `scripts/architecture_slice.py:138-148` (env, then config) | `TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED` (run) |
| `token_optimization.architecture.mode` | `slice` | **unread** | none | none |
| `token_optimization.architecture.max_tokens` | `5000` | derivation input for `budget_enforce.py`, not a live cap | `scripts/budget_enforce.py:57-60,138`. The live cap is env only: `scripts/architecture_slice.py:151-168` | `TOKEN_OPTIMIZATION_ARCHITECTURE_MAX_TOKENS` (run; set by the gate in enforce mode) |
| `token_optimization.architecture.min_tokens` | `2500` | proportional-distribution floor | `scripts/budget_enforce.py:57-60,138` | none |
| `token_optimization.memory.enabled` | `true` | false = skip top-k cap | `scripts/memory_retrieve.py:110-130` (env, then config) | `TOKEN_OPTIMIZATION_MEMORY_ENABLED` (run) |
| `token_optimization.memory.mode` | `top_k` | **unread** | none | none |
| `token_optimization.memory.max_entries` | `8` | entries surfaced per phase | `scripts/memory_retrieve.py:157-160,474` (config only; code default `TOP_K_DEFAULT = 8` at `:78`) | none |
| `token_optimization.memory.max_tokens` | `1500` | token ceiling for memory context | `scripts/memory_retrieve.py:163-190`: env, then config, then `TOKEN_BUDGET_DEFAULT = 1500` (`:79`); also a derivation input (`budget_enforce.py:63-66`) | `TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS` (run) |
| `token_optimization.memory.min_tokens` | `750` | distribution floor | `scripts/budget_enforce.py:63-66,139` | none |
| `token_optimization.comments.enabled` | `true` | the comment-digest gate | **Not read from config.** The only reader is the workflow node, which checks the env var alone: `workflows/archon-dark-factory.yaml:143-146` | `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` (run); `read_config` exports it in the scheduler only (D4) |
| `token_optimization.comments.digest_after_factory_marker` | `true` | **unread** | none | none |
| `token_optimization.comments.max_tokens` | `2000` | derivation input, not a live cap | `scripts/budget_enforce.py:71-74,140`; live cap is env only at `scripts/comment_digest.py:30` | `TOKEN_OPTIMIZATION_COMMENTS_MAX_TOKENS` (run; set by the gate) |
| `token_optimization.comments.min_tokens` | `1000` | distribution floor | `scripts/budget_enforce.py:71-74,140` | none |
| `token_optimization.diff.enabled` | `true` | false = emit the full diff | `scripts/diff_rank.py:140-147` (env, then config) | `TOKEN_OPTIMIZATION_DIFF_ENABLED` (run) |
| `token_optimization.diff.max_review_tokens` | `6000` | diff context cap for code review | `scripts/diff_rank.py:133-136,151-157` (config, then env wins) | `TOKEN_OPTIMIZATION_DIFF_MAX_REVIEW_TOKENS` (run) |
| `token_optimization.diff.min_review_tokens` | `3000` | distribution floor | `scripts/budget_enforce.py:79-84,141` | none |
| `token_optimization.escalation.opus_only_for` | `security, trading, auth, high_blast_radius, material_conformance_uncertainty` | **unread** (the YAML comment already says reserved) | none | none |

Counting: `scheduler` 10, `refine` 3, `plan` 1, `direct_to_pr` 3, `conformance` 7, `code_review` 5,
`docs_conformance` 2, `preview` 2, `conflict_resolution` 2, `blast_radius` 4, `dispatch_ceiling` 3,
`epic_autopilot` 11, `main_red_autofix` 5, `deps_audit` 6, `side_effect.phase_levels` 10, `token_optimization` 32
(`enforce_budgets`, `default_budget_tokens`, 6 budgets, 6 enforce, `issue_context` 1,
`architecture` 4, `memory` 5, `comments` 4, `diff` 3, `escalation` 1), `materializer` 4: 110 leaf keys.

### `materializer`

Ceilings for the harness candidate materializer (#465,
[spec](archive/2026-10-22-harness-candidate-materializer-design.md)). Read only by
`scripts/factory_core/materializer.py` (`load_config`, from this checkout's
`config/config.yaml`); no DAG node, scheduler path or gate calls the materializer yet. A
candidate's `resource_envelope` may lower a ceiling, never raise it (a larger declared value is
a `BLOCKED` refusal). The rlimits are per process, not a sandbox-wide cap. Phase A only builds
the `prlimit` argv from these values; nothing applies them until the Phase B executor (#544).
No env overrides, no adapter overlay.

| Key | Default | Controls | Reader | Env override (scope) |
|---|---|---|---|---|
| `materializer.memory_limit_mb` | `4096` | `RLIMIT_AS` ceiling per sandboxed process (MiB) | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |
| `materializer.max_duration_seconds` | `1800` | wall-clock ceiling; `RLIMIT_CPU` is the candidate's integer `cpu_limit` times this | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |
| `materializer.max_workspace_bytes` | `268435456` | expanded-size cap on the baseline and the built workspace; also `RLIMIT_FSIZE` | `scripts/factory_core/materializer.py` (`build_workspace`, `resolve_limits`) | none |
| `materializer.max_nproc` | `128` | `RLIMIT_NPROC` ceiling for sandboxed processes (bounds the fork-bomb memory multiplier). The kernel accounts it per user, not per sandbox; whether processes outside the sandbox count is not verified in Phase A, and a sandbox-scoped bound (cgroup `pids.max`) is a Phase B item | `scripts/factory_core/materializer.py` (`resolve_limits`) | none |

### Unread keys (summary)

Nothing in `scripts/`, `scheduler.sh`, `entrypoint.sh`, `workflows/`, `commands/`, `evals/`,
`bench/`, `run-compose.yml` or the `Dockerfile` reads:
`refine.skip_labels`, `refine.min_issue_body_length`, `plan.skip_labels`, `preview.model`,
`token_optimization.architecture.mode`, `token_optimization.memory.mode`,
`token_optimization.comments.digest_after_factory_marker`,
`token_optimization.escalation.opus_only_for`.

Keys whose value is read from config but never reaches the consumer that needs it (a distinct
failure: the key exists and is exported or parsed, yet changing it changes nothing):
`conflict_resolution.ai_tier` (D2), `scheduler.session_window_*` and the config side of
`scheduler.factory_wip_limit` in run containers (D1), `main_red_autofix.model`,
`.max_attempts`, `.ci_wait_minutes` (D3), `token_optimization.comments.enabled` (D4).

## Layer 2: environment variables

### Which file goes where

| File | Read by | Written by | Typical contents |
|---|---|---|---|
| `deploy/instance.env` (template `deploy/instance.env.example`) | **Scheduler only**, as compose `env_file:` (`deploy/docker-compose.yml`, `FACTORY_INSTANCE_ENV` selects another file name) and, for compose interpolation, `--env-file` | operator | scheduler credentials, instance identity, scheduler tuning, provider selection, proxy flags |
| `<PROJECT_DIR>/.archon/.env` | **Run containers**, as `env_file:` in `run-compose.yml:23-25`. The scheduler never loads it into its own environment; it only copies it to `/opt/dark-factory/.archon/.env` at startup (`scheduler.sh:53-59`) | operator | run credentials (`GH_TOKEN`, Claude token), run-side tuning |
| `run-compose.yml` `environment:` | run containers | image (baked) | identity, `DOCKER_HOST`, `SEQ_URL`, model alias pins, BuildKit address |

Rules that follow from the code:

1. **Scheduler tuning goes in `instance.env`, run tuning in `.archon/.env`.** Putting a
   scheduler knob such as `ABOVE_CEILING_KEYWORDS` or `EPIC_AUTOPILOT_ENABLED` in `.archon/.env`
   has no effect on the scheduler; putting a run knob such as `SESSION_WINDOW_BUFFER_MINUTES`
   or `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` in `instance.env` has none on runs, unless it is on
   the forwarded list below.
2. **`read_config` → `apply_run_config_env` (`scripts/run_config_env.sh`)**: for each of 33 config keys, if the variable is set in
   the scheduler's environment at all (`${!var+x}`), the env value wins and is only logged when it
   differs; otherwise the config value is exported. An absent YAML key exports an empty string.
   Since #180 the file is parsed by PyYAML, so every unquoted YAML boolean spelling (`True`,
   `TRUE`, `yes`, `on`; `False`, `No`, `off`) is exported as `true` or `false`. Before #180 the
   `yq` read passed the literal text through, and the scheduler's `= "true"` comparisons
   matched only the literal `true`.
   The list: `POLL_INTERVAL MAX_RETRIES RATE_LIMIT_FLOOR FACTORY_WIP_LIMIT MAIN_RED_RECHECK_ENABLED
   MAIN_RED_RECHECK_MINUTES BLOCKED_RESCUE_ENABLED REFINE_WIP_LIMIT DIRECT_TO_PR_LABEL
   SPEC_GRACE_MINUTES PLAN_GRACE_MINUTES CONFLICT_RESOLUTION_ENABLED DISPATCH_CEILING_ENABLED
   ABOVE_CEILING_LABEL ABOVE_CEILING_KEYWORDS`, the ten `EPIC_AUTOPILOT_*` (`ENABLED MODEL
   DAILY_CAP CONFIDENCE_FLOOR OPT_OUT_LABEL HOLD_TTL_HOURS SIZE_CEILING START_EPICS
   SENSITIVE_KEYWORDS ALLOW_SELF_IMPROVEMENT`), `MAIN_RED_AUTOFIX_ENABLED MAIN_RED_AUTOFIX_MODEL
   MAIN_RED_AUTOFIX_MAX_ATTEMPTS MAIN_RED_AUTOFIX_THROTTLE_MIN`, and
   `TOKEN_OPTIMIZATION_{ARCHITECTURE,MEMORY,COMMENTS,DIFF}_ENABLED`. The variables are exported
   into the scheduler process only; they are **not** forwarded to run containers.
3. **`dispatch()` forwards** (`scheduler.sh:388-408`): `--profile factory`, plus `--profile
   factory-model-proxy` when `FACTORY_MODEL_PROXY_ENABLED=true`, plus one `-e NAME` for each
   variable in `PROVIDER_PASSTHROUGH_VARS` (`scripts/provider_env.sh:26-41`) that is set and
   non-empty in the scheduler. A forwarded value beats `.archon/.env`; unset or empty leaves
   `.archon/.env` in control. When `FACTORY_MODEL_ENDPOINT` is set, `dispatch()` also stages the
   model endpoint registry and adds `-e FACTORY_MODEL_REGISTRY_SHA256` (see "Model endpoint
   registry" below). The list: `FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER FACTORY_MODEL_ENDPOINT`,
   `JIRA_BASE_URL JIRA_PROJECT_KEY JIRA_TOKEN JIRA_EPIC_LINK_FIELD JIRA_ISSUE_TYPE
   JIRA_REQUEST_TIMEOUT_SECONDS`, `GITLAB_TOKEN GITLAB_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK
   AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_PROFILE AWS_ROLE_ARN
   AWS_WEB_IDENTITY_TOKEN_FILE AWS_SESSION_TOKEN`, `CLAUDE_CODE_USE_VERTEX
   ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION GOOGLE_APPLICATION_CREDENTIALS
   GOOGLE_CLOUD_PROJECT`. Deliberately **not** forwarded: `GH_TOKEN`,
   `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` and platform markers
   (`scripts/provider_env.sh:15-22`).
4. **`run-compose.yml` `environment:`** (`run-compose.yml:26-57`) beats `env_file:`. It sets, with
   the scheduler's value or the shown default (interpolation reads the scheduler process
   environment): `DOCKER_HOST`, `SEQ_URL` (`http://seq:5341`), `FACTORY_OWNER`, `FACTORY_REPO`,
   `FACTORY_PROJECT_ID`, `FACTORY_PROJECT_NUMBER`, `FACTORY_STATUS_FIELD`, the seven
   `FACTORY_STATUS_*` option ids, `FACTORY_PRODUCT_NAME`, `FACTORY_CLONE_DIR`,
   `FACTORY_RUN_PREFIX`, `FACTORY_IMAGE`, the four model alias pins
   `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`, and `BUILDKIT_HOST`/`BUILDKIT_PORT`.
   Its defaults are MarketHawk's identity values, so a target must set them in `instance.env`.
   The compose file also reads `FACTORY_INSTANCE`, `FACTORY_BUILD_NETWORK` (`:110`),
   `RAW_ARTIFACT_CAPTURE` and `RAW_ARTIFACT_RETENTION_DAYS` (`:83-84`, proxy service only).

### Variables not derived from a config key

Grep used: `os.environ`/`os.getenv` across `scripts/`, `evals/`, `bench/`, and `${VAR` patterns
across `scheduler.sh`, `entrypoint.sh`, `scripts/*.sh`, `workflows/`, `commands/`; then excluded
the variables that `read_config` derives from config. "File" is where it must be set to be
effective.

**Credentials**

| Variable | Reader | File |
|---|---|---|
| `GH_TOKEN` | `scripts/factory_core/providers/codehost/github.py:21`; boot preflight in both processes | `instance.env` (scheduler) **and** `.archon/.env` (runs; never forwarded) |
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | `scripts/factory_core/providers/model.py:22` (preflight); the `claude` CLI | both files, same as above |
| `JIRA_TOKEN`, `GITLAB_TOKEN` | `providers/tracker/jira.py:38`, `providers/codehost/gitlab.py:48` | `instance.env` (forwarded) or `.archon/.env` |
| `INTERNAL_API_TOKEN` | `epic_autopilot.py:634` (scheduler), `main_red_fixer.py:253` (run); optional notifications, no token means silently skipped | respective file |
| `POLYGON_API_KEY` | `workflows/archon-dark-factory.yaml:988` (preview stack) | `.archon/.env` |
| `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT` | `providers/model.py:34-57` (preflight alternatives); forwarded (list above) | `instance.env` |

**Provider selection** (all forwarded by `dispatch()`; scheduler preflight reads only `instance.env`)

| Variable | Default | Reader | File |
|---|---|---|---|
| `FACTORY_TRACKER` | `github` (`github`, `jira`) | `providers/__init__.py:24,52` | `instance.env` |
| `FACTORY_CODEHOST` | `github` | `providers/__init__.py:32,59` | `instance.env` |
| `FACTORY_MODEL_PROVIDER` | `anthropic` (`anthropic`, `bedrock`, `vertex`) | `providers/__init__.py:66` | `instance.env` |
| `FACTORY_MODEL_ENDPOINT` | unset (a registry `id`) | `scripts/factory_core/providers/__init__.py`, `scripts/factory_core/providers/model_endpoint.py`; see "Model endpoint registry" below | `instance.env` |
| `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_EPIC_LINK_FIELD` | none | `providers/tracker/jira.py:36-39` | `instance.env` |
| `JIRA_ISSUE_TYPE` / `JIRA_REQUEST_TIMEOUT_SECONDS` | `Task` / `30` | `jira.py:40-41` | `instance.env` |
| `GITLAB_BASE_URL` | `gitlab.com` | `codehost/gitlab.py:49` | `instance.env` |
| `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `AWS_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION` | none | `providers/model.py:46,56`; the CLI itself | `instance.env` |
| `ANTHROPIC_BASE_URL` | unset | read by the `claude` CLI; to route through the proxy set it in `.archon/.env` only (`deploy/instance.env.example:143-148`) | `.archon/.env` |

**Instance identity** (`scripts/identity.sh:3-23`; defaults are MarketHawk's, which will not work for another target)

| Variable | Default | Notes |
|---|---|---|
| `FACTORY_OWNER`, `FACTORY_REPO` | `omniscient`, `markethawk` | `FACTORY_REPO_SLUG` is derived (`identity.sh:5`) |
| `FACTORY_PROJECT_ID`, `FACTORY_PROJECT_NUMBER` | MarketHawk board | Projects v2 board |
| `FACTORY_STATUS_FIELD`, `FACTORY_STATUS_{READY,IN_PROGRESS,IN_REVIEW,BLOCKED,DONE,BACKLOG,REFINED}` | MarketHawk ids | column option ids |
| `FACTORY_PRODUCT_NAME` | `MarketHawk` | also read at `scripts/comment_digest.py`, `scripts/ceiling_revisit.py`, `entrypoint.sh:20` |
| `FACTORY_CLONE_DIR` | `/workspace/${FACTORY_REPO}` | clone path in the run container (`entrypoint.sh:19`) |
| `FACTORY_RUN_PREFIX` | `${FACTORY_INSTANCE:-dark-factory}-dark-factory-run-` | WIP guard greps `docker ps` for this (`scheduler.sh:124`, `entrypoint.sh:172`) |
| `FACTORY_INSTANCE` | `dark-factory` | compose project and state volume name (`deploy/docker-compose.yml`, `run-compose.yml:18,114`); must also be in `instance.env` |
| `FACTORY_IMAGE`, `IMAGE_TAG`, `IMAGE_REF` | `ghcr.io/omniscient/dark-factory:${IMAGE_TAG:-latest}` | `identity.sh:23`, `deploy/docker-compose.yml`; `IMAGE_REF` pins a digest |
| `PROJECT_DIR` | parent of `deploy/` | host path bind-mounted at `/workspace/project` (read-only) |

All of these must be in `instance.env`; `run-compose.yml` copies the identity ones into run
containers (`run-compose.yml:31-47`).

**Feature flags and switches**

| Variable | Default | Reader | File |
|---|---|---|---|
| `FACTORY_MODEL_PROXY_ENABLED` | `false` | `scheduler.sh:392` (adds the `factory-model-proxy` compose profile) | `instance.env` |
| `RAW_ARTIFACT_CAPTURE` | `false` | `scripts/factory_core/model_proxy.py:39`; compose passes it in (`run-compose.yml:83`) | `instance.env` |
| `BENCH_MODE` | unset (`stub`, `full`) | `workflows/archon-dark-factory.yaml:826,940,1354` (stubs preview and PR creation) | `.archon/.env` / bench harness |
| `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` | unset | `scripts/budget_gate.sh:56` (kill-only) | `.archon/.env` |
| `FACTORY_MANIFEST_LABEL` | `manifest-intake` | `scripts/factory_core/handoff.py:57` | process that imports `handoff` |
| `SIDE_EFFECT_LEVEL_<PHASE>` | unset | `side_effect.py:215` | `.archon/.env` |
| `CONFORMANCE_SHADOW_MODEL` | unset | `commands/dark-factory-conformance.md:77` | `.archon/.env` |

(The config-derived switches such as `EPIC_AUTOPILOT_ENABLED` are in the Layer 1 tables.)

**Paths and test seams**

| Variable | Default | Reader | Notes |
|---|---|---|---|
| `SCHEDULER_STATE_DIR` | `/var/lib/dark-factory` | `scheduler.sh:10`, `entrypoint.sh:36`, `breaker.py`, `epic_autopilot.py:653`, `main_red_fixer.py:279` | shared state volume; do not change independently of the volume mount |
| `STATE_FILE` | `${SCHEDULER_STATE_DIR}/scheduler-state.json` | `scripts/factory_core/breaker.py:15`, `scripts/factory_core/cli.py:47` | breaker state |
| (path) blast-radius approvals | `${SCHEDULER_STATE_DIR}/blast-approvals/` — `<issue>.json` + `<issue>.json.sig` (signed, written only by the operator's `approve-blast.sh`), `<issue>.request.json` (unsigned review request written on a hold) | `scripts/gate_blast_radius.py` (`--approvals-dir`), `scripts/blast_gate_check.sh` | `factory:factory`, dir `0755`, files `0644`; the public key is `blast-approvers.asc` under `.factory/` in the target repo, read only from origin `main` (#551) |
| `CLONE_DIR` | `.` | `epic_autopilot.py:390,434` (`_effective_exclude_paths`, `_target_dir`), `main_red_fixer.py:278-292`, `adapter.py:306`, `diff_rank.py`, `budget_gate.sh:39` | set by `entrypoint.sh` in runs. The scheduler does not set it; epic autopilot then uses `/workspace/project` if that holds an adapter (`_SCHEDULER_TARGET_DIR`, [PR #524](https://github.com/omniscient/dark-factory/pull/524)) |
| `ARTIFACTS_DIR` | per run | `entrypoint.sh:115`; `budget_gate.sh:31` (required) | |
| `FACTORY_CORE_CLI`, `FACTORY_PROVIDERS_CLI` | baked paths | `scheduler.sh:19-20`, `entrypoint.sh:9` | override seams |
| `FACTORY_BLAST_HOLD_FILE` | `$ARTIFACTS_DIR/blast-hold.json` (the entrypoint's own run directory) | `entrypoint.sh` (export, `_handle_blast_hold`), `scripts/blast_gate_check.sh` | hold marker handoff across Archon's per-node `ARTIFACTS_DIR` (#551); never set by hand |
| `IDENTITY_SH`, `FACTORY_CONFIG_PATH`, `FACTORY_SCRIPTS_DIR`, `BLAST_GATE_BAKED_DIR` | baked paths | `entrypoint.sh:5,61-62`; `gate_blast_radius.py:82,91`; `scripts/blast_gate_check.sh` | "never set in the image"; test seams |
| `SCHEDULER_SOURCE_ONLY`, `ENTRYPOINT_SOURCE_ONLY` | `0` | `scheduler.sh:1468`, `entrypoint.sh:89,813` | tests only |
| `MODEL_PROXY_PORT` `MODEL_PROXY_UPSTREAM` `MODEL_PROXY_LEDGER_PATH` `MODEL_PROXY_CURRENT_RUN_PATH` `MODEL_PROXY_MAX_BYTES` `MODEL_PROXY_BACKUP_COUNT` `RAW_ARTIFACT_DIR` `RAW_ARTIFACT_RETENTION_DAYS` `CURRENT_RUN_DIR` `SEQ_URL` | `8787`, Anthropic API, `/var/lib/dark-factory/...`, 100 MiB, 3, ..., 7 days | `scripts/factory_core/model_proxy.py:26-41`; `run_record.py:28,716` | proxy and ledger; `run-compose.yml:79-85` sets the proxy's |
| `FACTORY_EDITS_LABEL`, `FACTORY_REGRESSION_LABEL`, `FACTORY_EMAIL` | `merged-with-edits`, `factory-regression`, `factory@<repo>` | `scripts/fetch_scorecard.py:26-28` | scorecard tooling |
| `MEM0_SPIKE_STORE_PATH`, `AGENTMEMORY_*` | none | `scripts/mem0_retrieve_adapter.py:46`, `scripts/eval_agentmemory.sh` | memory spike tooling, not part of runs |

**Tuning**

| Variable | Default | Reader | File |
|---|---|---|---|
| `REFINE_MAX_RETRIES` | `3` | `scheduler.sh:24` (refine and plan breaker ceiling) | `instance.env` |
| `STALE_PREVIEW_WARN_COUNT` | `3` | `scheduler.sh:1514-1522` (non-numeric resets to 3) | `instance.env` |
| `DELIVERY_FAILURE_MAX_SECONDS` | `30` | `entrypoint.sh:42,474`; env-only by design | `.archon/.env` |
| `MAIN_RED_AUTOFIX_AGENT_TIMEOUT` | `1200` | `main_red_fixer.py:291` | `.archon/.env` |
| `BUILDKIT_HOST`, `BUILDKIT_PORT` | `buildkit`, `1234` | `workflows/archon-dark-factory.yaml:1001`; set via `run-compose.yml:56-57` | `instance.env` |
| `FACTORY_BUILD_NETWORK` | `<instance>_dark-factory-net` | `run-compose.yml:110` | `instance.env` |
| `RUN_ID`, `RUN_STARTED_AT`, `ISSUE_NUM`, `INTENT` | per run | `entrypoint.sh` internal | not operator-settable |

### Model endpoint registry (#557)

Optional. With `FACTORY_MODEL_ENDPOINT` unset (the default) nothing below is read, staged or
checked, and dispatch argv and env are unchanged. Design:
`docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md`.

| Variable | Set where | Read by | Notes |
|---|---|---|---|
| `FACTORY_MODEL_ENDPOINT` | scheduler's `instance.env` | scheduler boot preflight; forwarded to runs by `dispatch()` | selects one registry `id`; an unknown id fails preflight, no other endpoint is chosen |
| `FACTORY_MODEL_REGISTRY_DIR` | compose interpolation at `up` time (the `--env-file` file or the shell), like `PROJECT_DIR` | `deploy/docker-compose.yml` | host path of this instance's own `model-registry/` directory; unset mounts the empty `deploy/model-registry.empty/` |
| `FACTORY_MODEL_REGISTRY_SHA256` | never set by hand | run-container preflight | computed by `dispatch()` per run and passed only with `docker compose run -e`; not in `PROVIDER_PASSTHROUGH_VARS` |
| the variable a `secret_reference` names | cloud entry: the run container's `.archon/.env` (for example `ANTHROPIC_API_KEY`); local entry: not `.archon/.env`, defined by #558/#587 | run-container preflight (presence only) | the registry stores the name, never the value |
| `FACTORY_MODEL_REGISTRY_PATH` | tests only | `model_endpoint.staged_registry_dir()` | overrides the staged path; never forwarded |

The registry lives on the human-only instance surface,
`deploy/instances/<instance>/model-registry/model-endpoints.yaml`, with one
`capability-evidence/<endpoint_id>.yaml` per local entry beside it. The scheduler mounts that
directory read-only at `/etc/dark-factory/model-registry`. Before each dispatch it copies it to
`/var/lib/dark-factory/model-registry` on the shared state volume and passes the copy's SHA-256
to that run. The run container's preflight fails if the digest is missing or does not match,
and also if a digest arrives with no endpoint selected. A local selection also fails if any
cloud model credential is set in the container, if `ANTHROPIC_BASE_URL` or any of the four
`ANTHROPIC_DEFAULT_*_MODEL` aliases does not match the entry, if its capability evidence is
not `tested`, and in every case until #587 ships a local-eligible dispatch.

Inspect the effective configuration without printing secret values:
`python3 /opt/dark-factory/scripts/factory_core/providers/cli.py model-endpoints show --from-source`
in the scheduler container (the read-only mount), or `model-endpoints show` in a run container
(the staged copy, checked against its digest). Each `secret_reference` prints as its name and
whether it is set. With an endpoint selected, the README's offline check
(`providers/cli.py preflight` with `--env-file deploy/instance.env`) fails on the missing
per-dispatch digest by design. Check offline with `preflight --side scheduler` and the
registry directory mounted at `/etc/dark-factory/model-registry`
(`-v <host model-registry dir>:/etc/dark-factory/model-registry:ro`), or with
`model-endpoints show --from-source`.

**Back to default cloud:** remove `FACTORY_MODEL_ENDPOINT` from `instance.env` (and
`FACTORY_MODEL_REGISTRY_DIR` from the interpolation env), then recreate the scheduler with
`docker compose -f deploy/docker-compose.yml up -d`. No staged copy is read while
`FACTORY_MODEL_ENDPOINT` is unset. The leftover `/var/lib/dark-factory/model-registry` is
inert and can be deleted.

## Layer 3: the target adapter (`.factory/adapter.yaml`)

The adapter is a file in the **target** repo. For the adapter's own keys, schema and validation
rules, see [the adapter file section of the authoring guide](adapter-authoring-guide.md#the-adapter-file-what-factoryadapteryaml-validation-accepts);
the table below only says how those keys relate to configuration.

**Resolution.** `scripts/factory_core/adapter.py:242-294` (`load`): no file means
`adapter_defaults.DEFAULTS` (MarketHawk's constants); a file is validated and then
`_deep_merge`d over the defaults (`adapter.py:209-216`), then the factory-owned boundary floor is
unioned into `safety.critical_diff_paths` and `safety.migration_seed_auth_patterns`
(`adapter.py:218-239`, `adapter_defaults.py:123-151`), which no adapter can remove. `adapter.get`
(`:296-302`) resolves a dotted path against the result. Consumers call it at use time and fall
back to defaults on any error.

**Merge versus replace.**

- Mappings **merge** key by key (`safety`, `memory_routing`, `deconflict`, `token_optimization`).
- Lists and scalars **replace** (`_deep_merge` copies the override). A target that declares
  `safety.hard_exclude_paths` loses the default list entirely and must repeat what it still wants.
- `components` is **replaced** when declared, including `components: {}`
  (`adapter.py:289-292`), so a target can opt out of the MarketHawk-shaped default sections.
- `loops` has no default (`[]`), is validated whenever present, and is a list.
- Unknown top-level keys warn and are carried through; `mechanism_candidates` is rejected
  (`adapter.py:54-58,259-265`).

**What the adapter can override**

| Adapter block | Effect on configuration | Consumers |
|---|---|---|
| `token_optimization` (the only block that overlays `config.yaml`) | Merged over the run's config by `effective_config.resolve` (`effective_config.py:124-142`): baked, then the clone's committed config, then the adapter block. Written to the clone only when the clone has **no** `.claude/skills/refinement/config.yaml` (`effective_config.py:287-320`). When the clone has one it wins unchanged and a drift warning is printed. Fail-open: an invalid adapter skips the overlay | every `token_optimization` reader above |
| `safety.sensitive_keywords` | unioned with `epic_autopilot.sensitive_keywords` | `epic_autopilot.py:397-410` (adapter directory: `_target_dir()`, see the `epic_autopilot` table) |
| `safety.hard_exclude_paths` | replaces `epic_autopilot.hard_exclude_paths` in the autopilot's lookup order; the autopilot unions the lookups from `CLONE_DIR`/`.` and from `_target_dir()` (`_effective_exclude_paths`) | `epic_autopilot.py:359-368,383-394,442-462`; `mechanism_candidate.py:381-415` |
| `safety.critical_diff_paths`, `safety.migration_seed_auth_patterns` | pattern lists for diff ranking and the blast-radius gate, with the floor unioned in | `scripts/diff_rank.py:70-84`, `scripts/gate_blast_radius.py:167-182` |
| `safety.main_red_allowed_paths` | paths the main-red fixer may edit | `main_red_fixer.py:25-33,292` |
| `safety.dispatch_ceiling_keywords` | **not read from the adapter**: only `adapter_defaults` supplies the fallback to `architecture_slice.py:83` | none effective |
| `components` | ARCHITECTURE.md slicing map (replaced when declared) | `scripts/architecture_slice.py:28-41` |
| `memory_routing` | glob to memory-file routing | `scripts/gate_lib.sh:19` |
| `deconflict.models_init`, `deconflict.migrations_dir`, `deconflict.alembic_ini` | Tier-1 conflict resolution file paths; the alembic.ini whose directory the post-Tier-1 multi-head check runs in (not a file: check skipped, unless a migration file is conflicted, which escalates) | `scripts/factory_core/deconflict.py:22-55` |
| `loops` | adapter-declared loops, validated; consumed by the handoff and run-record code | `handoff.py:437`, `run_record.py:1004` |

Everything else in Layer 1 (all scheduler policy, conformance, code review, blast radius,
dispatch ceiling, side-effect levels, main-red autofix) is **not** adapter-overridable. The
blast-radius gate and side-effect levels deliberately read factory-owned config only.

## Layer 4: hardcoded values that behave like configuration

| Value | Where | What it does |
|---|---|---|
| `SKIP_LABELS="needs-discussion,epic,ready-for-human"` | `scheduler.sh:9` (used at `:645`) | labels that exclude a ticket from dispatch, retry, rescue and the orphan sweep |
| `REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-pending-review,plan-pending-review"` | `scheduler.sh:23`; matched in `scripts/scheduler_lib.sh:10-20` | refine/plan skip list; replaces the unread `refine.skip_labels` and `plan.skip_labels` |
| `REFINE_MAX_RETRIES` default `3` | `scheduler.sh:24` | env-only retry ceiling for refine and plan |
| `ready-for-agent` opt-in label | `scripts/scheduler_lib.sh:26-27`; `scheduler.sh:628` (`--limit 200`), `:1351`; `epic_autopilot.py:631` | opt-in to refinement; also applied to epic children |
| `spec-pending-review`, `plan-pending-review` | `scheduler.sh:257,276,293,317` | gate labels removed on approval or grace expiry |
| size labels `size: XL/L/M/S` | `scripts/scheduler_lib.sh:37-52` | dispatch ceiling classification |
| `direct-to-pr` literal | `commands/dark-factory-refine.md:230`, `commands/dark-factory-plan.md:297` | direct-to-pr checks in phase agents, independent of `direct_to_pr.label` |
| `MAX_SESSION_WINDOW_HOURS = 5` | `scripts/factory_core/session_window.py:137` | cap on a session-window pause |
| `model: haiku` for `classify-preview` and `summarize-feedback` | `workflows/archon-dark-factory.yaml:870,169` | replaces the unread `preview.model` |
| Epic-selector exclude regex `security\|auth\|authz\|authn\|trading\|ibkr` plus `dark.?factory\|scheduler\|factory.self` | `scripts/factory_core/epic_autopilot.py:188-189` | which epics autopilot may start; factory-self terms drop when `allow_self_improvement` is on |
| Factory-self path prefixes `dark-factory/ .archon/ scheduler.sh factory_core/` | `epic_autopilot.py:194` | dropped from the exclude list when self-improvement is on |
| `_DEFAULT_EXCLUDE_PATHS` (four trading/auth paths) | `scripts/architecture_slice.py:85-90` | full-doc redaction fallback when config lacks `hard_exclude_paths` |
| `TOP_K_DEFAULT = 8`, `TOKEN_BUDGET_DEFAULT = 1500` | `scripts/memory_retrieve.py:78-79` | code defaults for memory caps |
| `_HARDCODED` budget fallbacks (`5000/2500`, `1500/750`, `2000/1000`, `6000/3000`, reserve `2000`) | `scripts/budget_enforce.py:29-33` | used when the config cannot be read |
| `MAIN_RED_AUTOFIX_AGENT_TIMEOUT` default `1200` | `main_red_fixer.py:291` | agent wall-clock limit |
| Compose defaults for MarketHawk identity and model alias pins | `run-compose.yml:31-53`; `scripts/identity.sh:3-18` | applied whenever an identity or alias variable is missing |
| Hotspot, size and gate fallbacks `5.0`, `400`, `False` | `scripts/gate_blast_radius.py:517-519` | when neither layer sets the key |
| `GraphQL items(first: 100)`, `labels(first: 10)` | `providers/tracker/github.py:27,37` (the query moved there from `scheduler.sh` in [PR #525](https://github.com/omniscient/dark-factory/pull/525); the scheduler calls `tracker board-items`) | board paging; items are paged 100 at a time; labels beyond the first 10 per item are not seen |
| `ABOVE_CEILING` keyword fallback copy | `commands/ceiling-revisit.md:95`, `scripts/ceiling_revisit.py` | mirrors the config default for the weekly revisit |

## How to change a setting safely

| Goal | Right mechanism | Takes effect |
|---|---|---|
| Change a scheduler policy knob (poll, WIP, grace, ceiling, autopilot) | Set the env var in `deploy/instance.env` (listed in Layer 2 rule 2), or edit `config/config.yaml` in the checkout bind-mounted at `/workspace/project`, or edit the baked file and publish an image | **Recreate the scheduler container** (`docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d`). `read_config` runs once at startup (`scheduler.sh:1473`), and `instance.env` is read only at container creation. A restart of the same container re-reads the config file but not a changed `instance.env`. |
| Change a run-container knob that has an env override (session window, `SIDE_EFFECT_LEVEL_*`, `TOKEN_OPTIMIZATION_*`, `MAIN_RED_AUTOFIX_ENABLED`, `FACTORY_WIP_LIMIT`) | Put it in `<PROJECT_DIR>/.archon/.env` | The scheduler copies that file at **startup** (`scheduler.sh:53-56`), so recreate or restart the scheduler, then the next dispatched run uses it |
| Change a run-side config key with no env override (conformance, code review, blast radius, side-effect levels, preview, budgets) | Edit `config/config.yaml` on `main`, rebuild and publish the image (`COPY config/` at `Dockerfile:152`). A target can override most by committing its own `.claude/skills/refinement/config.yaml` (blast radius reads it from the base ref) | The next run started from the new image. The scheduler also needs recreating to pick up the new image tag |
| Tune token budgets for one target | `token_optimization` in the target's `.factory/adapter.yaml` (only effective if the target has no committed run config, Layer 3) | Next run |
| Change safety lists, components, memory routing, deconflict paths | the adapter, remembering lists replace | Next run (read at use time) |
| Select a provider or add provider credentials | `instance.env` (forwarded to runs by `dispatch()`) | Recreate the scheduler |
| Change instance identity | `instance.env`; `run-compose.yml` copies it into runs | Recreate the scheduler |
| Disable a feature in an emergency | Prefer the existing kill-switches: `EPIC_AUTOPILOT_ENABLED`, `DISPATCH_CEILING_ENABLED`, `CONFLICT_RESOLUTION_ENABLED`, `MAIN_RED_RECHECK_ENABLED`, `BLOCKED_RESCUE_ENABLED`, `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false` | as above |

Do not change `deploy/instances/**` or `.github/workflows/publish.yml` from a factory run; they
are human-only surfaces. Gate changes (`gate_*`, breaker, budgets) need their own reviewed ticket.

## Discrepancies found

Inline comments in `config/config.yaml` (or elsewhere) that disagree with the code. The doc
above follows the code. `config/config.yaml` was not edited.

D1 to D11 are tracked in [#528](https://github.com/omniscient/dark-factory/issues/528)
(config keys and switches that never reach their reader); D8 has since been partly resolved
by [PR #524](https://github.com/omniscient/dark-factory/pull/524). Other open
tickets that cover the neighbouring documents:
[#526](https://github.com/omniscient/dark-factory/issues/526) (failed-run bookkeeping),
[#527](https://github.com/omniscient/dark-factory/issues/527) (DAG gate enforcement),
[#529](https://github.com/omniscient/dark-factory/issues/529) (loose scheduler matching),
[#530](https://github.com/omniscient/dark-factory/issues/530) (state transitions),
[#531](https://github.com/omniscient/dark-factory/issues/531) (unused code and tests not in CI)
and [#532](https://github.com/omniscient/dark-factory/issues/532) (MarketHawk-specific steps).

| # | Where | The comment says | The code does |
|---|---|---|---|
| D1 | `config/config.yaml:10-12` (`session_window_*`), `:6` (`factory_wip_limit`); `entrypoint.sh:32-33` | env overrides config; config supplies the container's value | `entrypoint.sh:34-39` sets bootstrap defaults before `apply_run_config_env … entrypoint` runs, and it (`scripts/run_config_env.sh:40-46`) treats any bootstrap value that differs from config as an "env/bootstrap override" and keeps it. So config can never change `FACTORY_WIP_LIMIT`, `CONFLICT_RESOLUTION_AI_TIER` or `SESSION_WINDOW_*` in a run container, and I confirmed the logic with a replica (config 10 vs default 5 stays 5). For `FACTORY_WIP_LIMIT` it is moot anyway: the container guard at `entrypoint.sh:169-175` runs before the clone and before `apply_run_config_env` (`entrypoint.sh:876`), and the scheduler does not forward the variable, so the container limit is `.archon/.env` or 1. A scheduler limit above 1 without the same value in `.archon/.env` makes the second concurrent run exit with an error |
| D2 | `config/config.yaml:64` (`ai_tier`: "false = only Tier 1") | the flag disables the AI tier | `entrypoint.sh:35` and `run-config.env` (`effective_config.py:77`) only set `CONFLICT_RESOLUTION_AI_TIER`; `entrypoint.sh:775` calls `cli.py deconflict` without `--no-ai-tier` and `deconflict.py` reads no env. Always AI-tier enabled. `scripts/factory_core/side_effect.py:189` repeats the claim in a comment |
| D3 | `config/config.yaml:124,125,127` (`main_red_autofix.model/max_attempts/ci_wait_minutes`: "container default") | the config value is the container default | `main_red_fixer.py:288-290` reads env then hardcoded defaults; no code path carries the config value into a run container. `read_config` exports `MAIN_RED_AUTOFIX_MODEL`/`MAX_ATTEMPTS` (`effective_config.py:70-71`) into the scheduler, where nothing reads them. `ci_wait_minutes` is not exported at all. The values match today only by coincidence |
| D4 | `config/config.yaml:193` | `comments.enabled` "NOT consulted ... scheduler.sh exports this value into its own process, which does not reach run containers" | accurate. Also true of `architecture.enabled`, `memory.enabled`, `diff.enabled`, whose exports (`effective_config.py:73-76`) reach nothing, but for those the readers also consult config in the clone, so the keys still work |
| D5 | `commands/ceiling-revisit.md:88-108` | the revisit writes `ABOVE_CEILING_KEYWORDS` into `.archon/.env` as the effective override | the scheduler reads `ABOVE_CEILING_KEYWORDS` from its own environment (`instance.env`) or config (`read_config`, `scheduler_lib.sh:50`) and never sources `.archon/.env` (`scheduler.sh:53-56` only copies it). The recorded override does not affect dispatch |
| D6 | `config/config.yaml:15-26` (`refine.skip_labels`, `plan.skip_labels`, `min_issue_body_length`) | present as config | no reader; real lists are hardcoded (`scheduler.sh:9,23`) |
| D7 | `config/config.yaml:60` (`preview.model: haiku`) | model for the preview classifier | no reader; `workflows/archon-dark-factory.yaml:870` hardcodes `haiku` |
| D8 (partly resolved by [PR #524](https://github.com/omniscient/dark-factory/pull/524)) | `config/config.yaml:95-98` (`epic_autopilot.hard_exclude_paths` "Kept byte-identical to adapter_defaults") | the config list is what autopilot uses | `epic_autopilot.py:442-462` tries the adapter first and `adapter.get` always returns a list (the defaults if no adapter file), so the config list is only reached if the adapter import fails. The scheduler-side half is fixed: `main_once` now takes the adapter directory from `_target_dir()` (`CLONE_DIR`, else `/workspace/project` when it holds `.factory/adapter.yaml`, else `.`), so the target's adapter is read, and `_effective_exclude_paths` unions in what the cwd lookup gave. What remains is the config-list point above: with an adapter present the `config.yaml` list is still dead. The directory choice is covered by `tests/test_epic_autopilot.py`; not exercised in a live scheduler |
| D9 | `config/config.yaml:184,195` | `architecture.max_tokens`, `comments.max_tokens` "derivation input, not a live cap" | accurate. Listed to flag that `memory.max_tokens` and `diff.max_review_tokens` are different: they are live caps read directly (`memory_retrieve.py:163-190`, `diff_rank.py:133`) |
| D10 | `config/config.yaml:189` (`memory.max_entries`: "baked — no env override") | baked | no env override is right, but it is read from the run's config chain (clone's file, then baked: `memory_retrieve.py:100-107,157-160`), so a target-committed or adapter-overlaid value takes effect |
| D11 | `deploy/instance.env.example:89-96` | optional tuning with "safe defaults" | uncommented `FACTORY_WIP_LIMIT=1` and `POLL_INTERVAL=60` in the template pin those two knobs over any later config change |

## Things I could not determine from the code

- Whether a given production instance's `/workspace/project/config/config.yaml` exists (it
  depends on the bind-mounted checkout), and therefore whether its scheduler is running on the
  target's copy or the baked one.
- The scheduler's effective `cwd` is inferred from `Dockerfile:173` (`WORKDIR /workspace`) and
  the compose `entrypoint:` override; I did not start a container to confirm it.
- How `claude` itself resolves `ANTHROPIC_BASE_URL` and the `CLAUDE_CODE_USE_*` variables; those
  are read by the CLI, outside this repo.

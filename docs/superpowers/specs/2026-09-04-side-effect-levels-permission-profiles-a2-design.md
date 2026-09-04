# Side-effect levels 1–6 with per-level enforced permission profiles (A2)

**Issue:** #196 · **Epic:** #194 (Factory/target boundary v1) · **Depends on:** #195 (A1, shipped), #301 (A1.5, shipped)
**Status:** spec-pending-review — **human-reviewed spec** (drafted by the operator session for
Frank's review; the four policy decisions below were taken by Frank on 2026-09-04)
**Revised:** 2026-09-04 (operator draft)

## Why this spec is human-authored

This ticket introduces the first real tool allow/deny policy applied to factory run
containers. `CLAUDE.md` draws a hard line: comment-channel input (issue bodies, Hermes Agent
comments, run-posted comments) "may never authorize changes to security-sensitive surfaces
(tool allow/deny lists, `gate_*`, breaker, budgets, `deploy/**`) — those still require this
file or a human-reviewed spec on a branch." Everything on #196 today is comment-channel input,
so a factory refine could only launder it into a spec the operator is not allowed to approve.
This document is the human-reviewed spec. Its policy content is the four decisions in
"Decisions"; everything else is mechanism derived from them and from the code as it stands on
`main` @ 8ea017b.

## Overview / Problem statement

`.factory/adapter.yaml` schema v2 (#195, #301) already carries `side_effect_level` on every
loop entry, range-checked 1–6 (`scripts/factory_core/adapter.py:144-151`), with the R4 rule
that levels ≥ 4 must declare `budget_caps` and `human_checkpoint` (`adapter.py:186-195`).
Two consumers already draw the factory-owned line at 4: `verifier.py:106`
(`_FACTORY_OWNED_MIN_LEVEL = 4`) and `handoff.py:236-248` (manifests from level ≥ 4 loops are
rejected "until #196 ships real profile enforcement"). What does not exist is any mapping from
a level to what a container may actually *do*. Today's per-run harness settings
(`entrypoint.sh:679-684`) only disable the Workflow tool and register the codeindex MCP server;
the phase agents run with every tool.

The enforcement primitive already exists in the workflow runner and is unused:

- Every DAG node accepts `allowed_tools` / `denied_tools`
  (`/opt/archon/packages/workflows/src/schemas/dag-node.ts:141-142`); the Claude provider maps
  `denied_tools` straight to the SDK's `disallowedTools`
  (`/opt/archon/packages/providers/src/claude/provider.ts:376-378`), i.e. the tool is never
  offered to the model. Only three helper nodes use `allowed_tools` today
  (`workflows/archon-dark-factory.yaml:31,168,694`); no phase-agent node sets anything.
- Runs execute with `permissionMode: 'bypassPermissions'` (`provider.ts:622`). Permission
  *prompts* and settings-file allow rules are therefore not a lever; tool removal is.
- Node hooks (`schemas/hooks.ts:43-50`) return **static** responses matched on tool *name* —
  they cannot inspect a Bash command string, so "deny `git push` but allow `git log`" cannot be
  expressed as a hook. Command-level policy needs a different layer (R5).

No target-loop runner exists yet (A1.5: "loops are execution-inert until A2–A5"; the epic's
own out-of-scope note keeps v1 loops inside factory-dispatched containers). This spec therefore
delivers the level semantics, the enforcement layers, their application to the factory's own
phases, and the audit trail — exercised by tests in the factory image — not a live target loop.

## Decisions (Frank, 2026-09-04)

| # | Decision | Resolution |
|---|---|---|
| D1 | Level 6 | **Rejected at adapter validation.** #194's non-negotiable ("6 out of scope for v1; design the enum, reject at validation"); #301 deliberately left the rejection to A2. |
| D2 | Level → capability table | The table in R1. **All current factory phases are declared at level 5 in v1** so nothing regresses; tightening (refine/plan → 4) is a follow-up. |
| D3 | Enforcement layers in v1 | Tool removal per node (R4) + a `git`/`gh` command shim (R5) + the effective profile written to the run record (R6). **Credential scoping per level is deferred** to a Frank-owned follow-up: a per-level GitHub token lives in `deploy/**`, a human-only surface. |
| D4 | Fail-closed default | A loop with no usable level runs as **level 1**. Phase levels are a **config policy knob** (`config/config.yaml`), not derived from prose. Anything unparseable resolves to the most restrictive applicable profile. |

## Trust model

- **What this enforces.** Which tools the model is offered (R4) and which `git`/`gh` verbs the
  agent's shell can execute (R5), both keyed on the run's effective side-effect level. Both
  layers are independent of the harness permission mode, so they hold under
  `bypassPermissions`.
- **What this does not claim.** The shim is a PATH shim. A process that calls `/usr/bin/git
  push` by absolute path bypasses it. v1 is a policy boundary against *mistaken* or
  prompt-injected behaviour, not a security boundary against a deliberately hostile agent; the
  boundary against a hostile agent is the credential, which is D3's deferred follow-up. Say
  this plainly in `docs/factory-target-boundary.md` (#201).
- **Single source of truth.** Level semantics live in one module
  (`scripts/factory_core/side_effect.py`). The DAG, the shim, the run record, `handoff.py` and
  the future loop runner all read it; nothing re-declares the table. `verifier.py` is *not*
  touched (Blast-Radius hotspot); a test pins its constant to the module's.
- **No settings-file writes.** `.claude/settings.json` / `settings.local.json` stay in the
  adapter's `hard_exclude_paths` (`.factory/adapter.yaml:20-26`) and this spec adds nothing to
  them — permission rules there are not enforced in bypass mode and would only invite the
  `allowed-tools: Bash(*)`-plus-blocklist anti-pattern the skills policy bans (#42 spec §4).

## Requirements

### R1 — Level semantics and the profile table

`side_effect.py` defines the six levels (names from #193) and, for levels 1–5, a **profile**:
the tools removed from the model (Layer A) and the `git`/`gh` verbs the shim denies (Layer B).
Level 6 has no profile (D1).

| Level | Name | Layer A — tools removed | Layer B — shim denies | Net effect |
|---|---|---|---|---|
| 1 | read-only research | `Write`, `Edit`, `MultiEdit`, `NotebookEdit` | `git`: `commit`, `push`, `tag`, `remote add/set-url`; `gh`: everything except `view`, `list`, `status`, `search`, `api` with method GET and no body | Can read and run read-only commands. Output is its return value / stdout only. |
| 2 | artifact writing | none | `git`: `push`, `tag`, `remote set-url`; `gh`: all mutating verbs (as level 1) | Writes files and local commits; nothing leaves the container except via an A5 manifest picked up by the factory. |
| 3 | GitHub ticket creation | none | `git`: `push`, `tag`, `remote set-url`; `gh`: as level 2 **except** `issue create`, `issue comment`, `issue edit` are allowed | Can file and annotate issues; cannot modify code (no push, no PR). |
| 4 | code modification | none | `gh`: `pr create/merge/ready/review/close`, `release *`, `repo *`, `secret *`, `auth *`, `api` non-GET; `git push` allowed only to the run's own branch (`FACTORY_RUN_BRANCH`), never `--force`/`--delete`, never `main` | Commits and pushes its branch; cannot open or merge PRs. |
| 5 | PR creation | none | the **never-list** only: `gh repo delete/archive/rename`, `gh secret *`, `gh auth *`, `gh ssh-key *`, `gh gpg-key *`, `gh api -X DELETE`, `git push --delete`/`:refspec` deletions | Today's implement / push-and-pr behaviour. The never-list must be shown (plan task) to match nothing the DAG or `commands/*.md` do today. |
| 6 | external production side effect | — | — | Rejected at validation (R2). |

"Modify code" (issue AC2) means *push to any branch*. Local edits at levels 2–3 are permitted
and never leave the container.

`profile_for(level: int) -> Profile` returns `denied_tools: list[str]`,
`git_denied: list[str]`, `gh_denied: list[str]`, `gh_allowed: list[str]`, `profile_version:
str` (`"v1"`). `effective_level(value) -> int` implements D4: `None`, non-int, bool, out of
range → 1. A `render` CLI prints a profile as JSON for the DAG test and the run record.
`FACTORY_OWNED_MIN_LEVEL = 4` and `TARGET_DEFINABLE_MAX_LEVEL = 3` live here; a test asserts
`verifier._FACTORY_OWNED_MIN_LEVEL == side_effect.FACTORY_OWNED_MIN_LEVEL` without modifying
`verifier.py`.

### R2 — Level 6 is rejected at validation (D1)

- `adapter.py`: the range check becomes 1–5; `6` raises
  `AdapterError("loops[i] ('name'): side_effect_level 6 (external production side effect) is out of scope for v1 (#194); declare 1–5")`.
  The comment block at `adapter.py:144-147` is updated to say A2 rejects 6. Fixtures that use 6
  as an R4 example are updated.
- `handoff.py:153-156`: manifest `side_effect_level` range becomes 1–5 with the same reason
  text under the existing `schema_invalid` code (no new reason code).
- `adapter.py` is a Blast-Radius hotspot: the implementing PR takes the operator-review path
  used for #197/#198 (operator review stands in for conformance + Gate 3, merge by hand).

### R3 — Phase levels are config (D2, D4)

`config/config.yaml` gains:

```yaml
side_effect:
  # Side-effect level each factory phase runs at (#196). 1..5; 6 is never valid.
  # All phases start at 5 (today's behaviour). Tightening is a separate reviewed change.
  phase_levels:
    refine: 5
    plan: 5
    implement: 5
    validate: 5
    conformance: 5
    code_review: 5
    deconflict: 5
  # env: SIDE_EFFECT_LEVEL_<PHASE> overrides one phase (e.g. SIDE_EFFECT_LEVEL_REFINE=4).
```

A run container executes one intent, which maps to a fixed set of phases
(`refine → [refine]`, `plan → [plan]`, `new`/`continue → [implement, validate, conformance,
code_review]`, `resolve → [deconflict]`, `close → []`). The container's **effective level** is
the maximum over that set. `entrypoint.sh` computes it (via `side_effect.py`), exports
`FACTORY_SIDE_EFFECT_LEVEL` and `FACTORY_SIDE_EFFECT_PROFILE_VERSION` before invoking archon,
and logs `side_effect_level=<n> profile=<version> phases=<list>` once. A missing or invalid
config value resolves to 1 for that phase and logs a warning — fail closed, not open.

### R4 — Layer A: tool removal per DAG node

Each phase-agent node (`refine`, `plan`, `implement`, `validate`, `conformance`,
`code-review`) carries an explicit `denied_tools:` equal to `profile_for(level).denied_tools`
for its configured level. At level 5 that is `denied_tools: []` — explicit, so its absence is
detectable. `tests/test_side_effect_dag.py` (pattern: `tests/test_budget_enforce_dag.py`)
asserts, for every phase node, that the key is present and equals the rendered profile for the
level in `config.yaml`; a phase node with no `denied_tools` key fails the test. The DAG is
static YAML; this test is what keeps it honest when the config changes.

### R5 — Layer B: the `git`/`gh` command shim

- New executables `scripts/shims/git` and `scripts/shims/gh` (bash, no dependencies). Each
  reads `FACTORY_SIDE_EFFECT_LEVEL`, resolves the real binary (`command -v` after removing the
  shim directory from `PATH`), parses only the leading verb (and for `gh api` the method / body
  flags; for `git push` the remote, refspec and force/delete flags), and either execs the real
  binary or exits 1 with
  `side-effect guard: '<verb>' is denied at level <n> (<name>); see docs/factory-target-boundary.md`.
- **Activation.** The shim enforces only when **both** `FACTORY_SIDE_EFFECT_LEVEL` is set and
  `CLAUDECODE=1` is in the calling environment (the marker the Claude CLI sets for its Bash-tool
  subprocesses; archon's own DAG `bash:` nodes — `setup-branch`, `refine-push`,
  `plan-push-and-advance`, `push-and-pr`, `push-resolve`, `post-merge-update-codeindex` — do
  not carry it and keep factory privilege). Without both markers the shim is a transparent
  passthrough. `entrypoint.sh` prepends `/opt/dark-factory/scripts/shims` to `PATH` for the
  archon invocation only.
- The shim never parses flags it does not need; unknown verbs at levels 4–5 pass through,
  unknown verbs at levels 1–3 are **denied** (fail closed).
- Denials emit a health event `side_effect.denied` with `{tool, verb, level}` through
  `run_record.emit_health_event` (R6) and write one line to stderr; they never post comments.
- `tests/test_side_effect_shims.sh` runs in the factory image: for each level, a matrix of
  allowed/denied invocations against a stub real binary, plus the activation matrix (no level
  var → passthrough; no `CLAUDECODE` → passthrough).

### R6 — Layer C: audit in the run record

`run_record.py`'s row (`run_record.py:134-150`) gains `side_effect_level` (int) and
`side_effect_profile` (the `profile_version` string). The CLI accepts `--side-effect-level`
and `--side-effect-profile`; entrypoint passes them from the env of R3. Absent flags write
level `1` and profile `"unknown"` — a row can never claim a wider profile than it can prove.
Existing callers build a bare `Namespace` (see the `origin` precedent at `run_record.py:143`);
use the same `getattr` guard.

### R7 — Loops consume the same profile

`profile_for(effective_level(entry.get("side_effect_level")))` is the profile a future loop
runner MUST apply to a loop container; A2 provides the function and the env contract
(`FACTORY_SIDE_EFFECT_LEVEL`, `FACTORY_RUN_BRANCH`), not the runner. `handoff.py`'s
target-definable check keeps using level < `FACTORY_OWNED_MIN_LEVEL`, now imported from
`side_effect.py`. `docs/adapter-authoring-guide.md` gains a "Side-effect levels" section
reproducing the R1 table and the D4 default.

### R8 — Verification tasks that precede implementation (plan Task 0)

Recorded in the plan with evidence from the factory image, before any code lands:

1. A test node with `denied_tools: [Write]` under `bypassPermissions` → `Write` is absent from
   the model's tool list, and a subagent spawned via the `Agent` tool inherits the removal.
2. `CLAUDECODE=1` is present in the environment of a Bash-tool subprocess and absent in an
   archon `bash:` node. If this discriminator does not hold, **stop and amend this spec** —
   do not invent another one in the plan.
3. `grep` of `workflows/archon-dark-factory.yaml` and `commands/*.md` for every never-list verb
   (R1, level 5) returns nothing, so level 5 cannot regress an existing phase.
4. `scripts/factory_core/providers/cli.py` tracker/codehost calls shell out to `gh` (so the
   shim covers them). Any direct HTTPS client found instead is listed in the plan as a shim
   bypass to be covered by the D3 follow-up.

## Architecture / Approach

### New files

- `scripts/factory_core/side_effect.py` — levels, `Profile`, `profile_for`, `effective_level`,
  `intent_phases`, `render` CLI.
- `scripts/shims/git`, `scripts/shims/gh` — R5.
- `tests/test_side_effect.py` — table, D4 default, verifier-constant pin, level-6 rejection in
  adapter and handoff, run-record fields.
- `tests/test_side_effect_dag.py` — R4.
- `tests/test_side_effect_shims.sh` — R5 (added to the CI `tests:` job next to
  `test_smoke_gate.sh`).

### Modified files

- `scripts/factory_core/adapter.py` — R2 (hotspot; operator-review path).
- `scripts/factory_core/handoff.py` — R2 range, R7 import.
- `scripts/factory_core/run_record.py` — R6.
- `entrypoint.sh` — R3 export + R5 `PATH` prepend, one log line.
- `workflows/archon-dark-factory.yaml` — explicit `denied_tools:` on the six phase nodes (R4).
- `config/config.yaml` — R3 block.
- `docs/adapter-authoring-guide.md` — R7 section.
- `.github/workflows/ci.yml` — run `tests/test_side_effect_shims.sh`.

### Explicitly not touched

`deploy/**`, `.github/workflows/publish.yml`, `.claude/settings*.json`, `.mcp.json`,
`.factory/adapter.yaml`, `scripts/factory_core/verifier.py`, `breaker.py`, `verdict.py`, every
`gate_*`, budgets, the socket-proxy configuration, credentials. Nothing in this spec widens any
existing permission: level 5 removes no tool and denies only the never-list.

## Acceptance criteria → disposition

| Issue AC | Disposition |
|---|---|
| A level-1 loop's container demonstrably cannot push, comment, or open PRs | R4 + R5 at level 1, proven by `test_side_effect_shims.sh` and the DAG test in the image (no loop runner exists to run live). |
| A level-3 loop can file issues but not modify code | R5 level 3 matrix: `gh issue create/comment/edit` pass, `git push` and `gh pr create` denied. |
| Existing factory phases run unchanged under their assigned levels (bench/tests green) | D2: all phases at level 5; R8.3 proves the never-list is unused; full suite + `smoke_gate.sh` + DAG checks green; one refine and one plan dry run on a scratch issue. |
| Profile is recorded per run for audit | R6 fields on every `runs.jsonl` row, tested. |

## Known limitations and follow-ups (not in this ticket)

- **F1 — credential scoping per level** (Frank-owned; `deploy/**`): a level ≤ 3 container
  should hold a token that cannot push; a level-4 container one that cannot open PRs. Closes
  the absolute-path bypass.
- **F2 — tighten refine/plan to level 4**: they push a branch and apply labels but never open
  a PR; expected regression zero. Its own small reviewed change after F1's token exists or once
  R5 has run clean for a week.
- **F3 — loop runner**: applies R7 when a runner exists (later #194 child).
- **F4 — per-node levels inside one container**: the effective level is per run (R3). Per-node
  levels need a per-node env, which archon's node schema does not offer today.
- **F5 — Python-level GitHub calls** found by R8.4, if any.

## Alternatives considered

- **Settings-file `permissions.deny` rules.** Not enforced under `bypassPermissions`; and the
  settings files are `hard_exclude_paths`. Rejected.
- **Node hooks that block by command pattern.** archon hooks are static responses on tool name
  (`hooks.ts:43-50`); cannot see the command string. Rejected for command policy; not needed for
  tool removal, which `denied_tools` already does.
- **Denying `Bash` wholesale at levels 1–3.** Makes level 3 (must run `gh issue create`) and
  read-only research (`git log`, `rg`) impossible. Rejected in favour of the verb shim.
- **A single global `FACTORY_SIDE_EFFECT_LEVEL` without the `CLAUDECODE` discriminator.** Would
  block the DAG's own push nodes at any level below 4. Rejected; R8.2 verifies the discriminator.

## Open questions (non-blocking)

- Whether `gh issue edit` at level 3 should be limited to the issues the loop itself created.
  v1 allows it unrestricted (the loop's issues are already `needs-triage` by A5 intake); revisit
  with F1.

## Assumptions (flagged)

- `CLAUDECODE=1` is set for Bash-tool subprocesses by the Claude CLI in the image and not by
  archon for `bash:` nodes (R8.2 verifies before any code).
- `disallowedTools` removal applies to subagents (R8.1 verifies).
- The factory image copies `scripts/` with executable bits intact (the smoke gate already
  relies on `scripts/*.sh` being executable).

# Factory Review Guardrails for Claude Skills, Hooks, and Tool Permissions

**Issue:** omniscient/dark-factory#46
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#42 (CLOSED — policy spec at
[`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`](2026-07-10-dark-factory-claude-skills-design.md))
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36

---

## Overview / Problem Statement

Issue #42's policy spec defined the taxonomy and safety rules for Dark Factory's use of Claude
Code Skills, and named two concrete follow-ups in its §7 "Review Expectations": add
`.claude/skills/**`, `.claude/settings.json`, and `.factory/hooks/**` to
`.factory/adapter.yaml`'s `safety.hard_exclude_paths` (the fail-closed gate
`factory_core/epic_autopilot.py` reads) and to `safety.critical_diff_paths` (the visibility
signal `scripts/diff_rank.py` reads). This ticket lands those two follow-ups and closes the
rest of the gap identified in #42: **neither of those two lists actually blocks a normal
factory run today** — `hard_exclude_paths` only gates the `epic_autopilot` feature, which ships
disabled by default (`config/config.yaml`: `epic_autopilot.enabled: false`), and
`critical_diff_paths` only affects code-review diff ordering, never blocking. The list that
*does* block every run — `safety.migration_seed_auth_patterns`, read by
`scripts/gate_blast_radius.py`, which sets `STATUS: HUMAN_REQUIRED` and halts auto-merge in
`.archon/commands/dark-factory-validate.md` Phase 0 — currently has no skill/settings/hooks
awareness at all.

Beyond path-level visibility, the issue's acceptance criteria ask for **content-level**
detection that no path-based gate can do: has `allowed-tools` broadened, has a hook been added,
has `context: fork` or a model/effort override appeared, does a skill script interpolate
untrusted input into a shell command unescaped. That detection can only happen where the diff
content is actually read — the code-review and conformance reviewer personas
(`.claude/skills/{code-review,conformance}/RUBRIC.md`).

This spec defines both halves: which adapter safety lists gain which new patterns and why, and
what the two RUBRIC personas must be taught to look for and how "human review or explicit
justification" gets enforced for content-level findings.

## Requirements

Distilled from the issue's five acceptance criteria and #42 §7, refined through Q&A below:

1. Changes to `.claude/skills/**/SKILL.md`, `.claude/skills/**/scripts/**`,
   `.claude/settings.json` (and `.claude/settings.local.json`), `.mcp.json`, plugin/marketplace
   config, and `.factory/hooks/**` must be **visible and risk-ranked** — never silently
   truncated or summarized out of a code-review diff.
2. Changes to whole-file security-sensitive surfaces (`.claude/settings.json`,
   `.claude/skills/**/scripts/**`, `.factory/hooks/**`) must **hard-block** auto-merge via the
   existing blast-radius gate, on every run, independent of the (disabled-by-default)
   `epic_autopilot` feature.
2a. Changes to `.claude/skills/**/SKILL.md` must **not** hard-block on path alone — a path glob
    cannot distinguish a frontmatter permission change from a prose edit, and blocking every
    prose edit trains reflexive `needs-discussion` removal, eroding the gate's signal.
3. Content-level items — `allowed-tools`/`disallowed-tools` broadening, new hooks, `context:
   fork`, model/effort overrides, plugin/MCP config changes, and dynamic shell injection in a
   skill/hook script — must be flagged by the code-review/conformance reviewer personas as
   blocking (high/critical) findings, since only diff-content review can detect them.
4. "Broad tool permissions require human review **or** explicit justification" — a
   `# justification:` comment (mirroring #42 §3's `disable-model-invocation` exception pattern)
   may downgrade a content-level finding from blocking to advisory, but never removes the human
   sign-off expectation on the PR.
5. Factory-run issue comments must legibly name *which* security-sensitive category tripped a
   block — not lump a skill-script change into the same generic label as a database migration.
6. No new gate mechanism. Gate 2 (conformance) and Gate 3 (code-review) remain the authoritative
   blocking gates for content-level findings; the blast-radius gate remains the authoritative
   path-level hard block. This ticket only teaches those existing mechanisms new patterns.
7. Protection must apply to every target repo (MarketHawk and future clones), not just
   dark-factory's own self-target, since `.claude/skills/`, `.claude/settings.json`, and
   `.factory/hooks/` are clone-universal factory-mechanism paths, not dark-factory-specific
   content.

---

## Brainstorming Q&A

> **Q1:** Should this ticket's implementation be (a) exactly the two follow-ups #42 §7 already
> scoped — extend the two existing adapter safety lists plus teach the two RUBRIC personas — or
> (b) a structurally new dedicated gate script?
>
> **A1:** Option (a). Extend `.factory/adapter.yaml`'s `safety.hard_exclude_paths` and
> `safety.critical_diff_paths`, teach the two RUBRIC.md personas for content-level detection, add
> tests mirroring existing path-gate tests. No new gate mechanism — both the issue's own
> "existing code review/conformance gates remain authoritative" criterion and the parent epic's
> preservation requirement point at extending, not replacing.

> **Q2:** `hard_exclude_paths` only feeds the disabled-by-default `epic_autopilot`, and
> `critical_diff_paths` only affects diff-review ordering — neither blocks a normal run. The list
> that actually blocks every run is `safety.migration_seed_auth_patterns` (feeds
> `gate_blast_radius.py` → `HUMAN_REQUIRED` → `dark-factory-validate.md` Phase 0 halts
> auto-merge). How should that list be extended?
>
> **A2:** Tiered, not uniform. Add the **full** skill/settings/hooks/MCP/plugin glob set to
> `hard_exclude_paths` and `critical_diff_paths` — cheap, no downside, and correctly
> forward-protects `epic_autopilot` once/if it's ever enabled (`allow_self_improvement: true`
> already makes self-work autopilot-eligible). For the list that actually blocks *today*
> (`migration_seed_auth_patterns`), stay surgical, matching its current narrow deploy+CI scope:
> only whole-file-sensitive paths — `.claude/settings.json` (+ plugin/MCP config) and
> `.claude/skills/**/scripts/**` (executable code). **Do not** add `.claude/skills/**/SKILL.md`
> to the blocking list — `allowed-tools`/`context: fork`/model-effort-override changes live in
> SKILL.md frontmatter, but a path glob can't tell that apart from a prose edit, and blocking
> every prose edit causes alarm fatigue that erodes the gate's signal over time. SKILL.md
> frontmatter changes are instead gated by content: the RUBRIC personas emit a
> broadened-permission/new-hook/fork/model-override change as a high/critical finding, which
> Gate 3 already blocks on (`code_review.block_threshold: high` by default).

> **Q3:** `gate_blast_radius.py`'s `classify_file()` only emits two trigger categories today,
> `"hotspot"` or `"migration-seed"` — the latter a single bucket for any
> `migration_seed_auth_patterns` match. If skill/settings/scripts patterns join that same list, a
> blocked report would say "trigger: migration-seed" for a skill-script change, indistinguishable
> from a deploy/CI-publish change. Does this satisfy "factory reports call out skill/security-
> sensitive changes," and separately: does "hooks" in the issue's scope list mean Claude Code's
> own hooks (configured inside `.claude/settings.json`) or dark-factory's own
> `.factory/hooks/<name>` pipeline-hook scripts (unrelated mechanism, explicitly named in #42
> §7)?
>
> **A3:** No, the generic label is not good enough — "call out" means legibly, not "reverse-
> engineer from `TRIGGERED_FILES`." Add a distinct `"skill-security"` trigger category,
> sub-classified from the *matched pattern's source text* — the same technique
> `diff_rank.py::_safety_signal()` already uses to distinguish `"migration_path"` /
> `"auth_path"` / `"trading_path"` / `"factory_path"` from one shared pattern list. Both
> categories still resolve to `HUMAN_REQUIRED`/`critical` — this is a labeling refinement, not a
> new blocking tier. On hooks: treat both concepts as in scope. Claude Code's own hooks live
> inside `.claude/settings.json` (no separate glob needed — the whole-file glob already covers
> them). `.factory/hooks/**` is a *separate*, equally security-sensitive surface (target-repo-
> supplied shell the factory itself executes with pipeline-level trust) that #42 §7 already named
> explicitly three times as a follow-up target; it must be added to all three adapter lists and
> sub-classified into `skill-security` (or a same-tier sibling label), never falling into the
> generic `migration-seed` bucket.

> **Q4:** Should the new globs live in `adapter_defaults.py`'s `DEFAULTS` (universal baseline,
> protecting MarketHawk and future clones with no adapter override) or only in dark-factory's own
> `.factory/adapter.yaml`? And how should the RUBRIC personas define "explicit justification" and
> "dynamic shell injection" operationally, and what's testable given RUBRIC.md files are prose
> read by subagents, not unit-testable code?
>
> **A4:** Both, and this is load-bearing, not a stylistic choice. `.claude/skills/{refinement,
> code-review,conformance}`, `.claude/settings.json`, and `.factory/hooks/**` are clone-universal
> factory-mechanism paths exactly like the already-universal `dark-factory/`/`.archon/`/
> `scheduler.sh`/`factory_core/` defaults — they belong in `DEFAULTS` so MarketHawk-and-future
> repos are protected with no per-repo work. **But** `scripts/factory_core/adapter.py::_deep_merge`
> replaces list-valued keys wholesale rather than concatenating (verified: lines 13-20, the
> `else: out[k] = copy.deepcopy(v)` branch fires for any non-dict value including lists) — so
> dark-factory's own `.factory/adapter.yaml`, which already sets its own
> `migration_seed_auth_patterns`/`critical_diff_paths`/`hard_exclude_paths`, would silently
> **replace** the DEFAULTS additions and leave dark-factory's own PRs (including this ticket's)
> unprotected if the new globs only lived in `DEFAULTS`. Add the globs to **both** `DEFAULTS` and
> `.factory/adapter.yaml`'s three lists. Justification: mirror #42 §3's pattern exactly — a `#
> justification:` comment immediately above the changed frontmatter field; the persona judges
> whether it is substantive (concrete and specific, not boilerplate) and, if so, downgrades the
> finding to advisory with **human sign-off still expected on the PR** (never justification
> alone). Dynamic shell injection: define it operationally — externally-influenced input
> (variable/arg/env/issue-comment field) interpolated *unescaped* into an executed command string
> (`bash -c "...$VAR..."`, an f-string/`.format()`/concatenated command passed to `subprocess`
> with `shell=True`, `eval`, backticks) is a finding; argv-list invocation (`shell=False`) or
> explicitly quoted/`shlex.quote`d input is not. Testability: three tiers — (1) deterministic unit
> tests for the new `gate_blast_radius.py`/`diff_rank.py` classification (mirroring
> `tests/test_blast_radius.py`), (2) a presence/lint test asserting both RUBRIC.md files contain
> the required instruction strings (guards against silent deletion of the guidance, not model
> obedience), (3) a checked-in fixture diff for a Tier-1 live-subagent smoke run (per #42 §8),
> documented as the behavioral verification path since prose-prompt fidelity isn't unit-testable.

---

## Architecture / Approach

### File map

| File (canonical, tracked at repo root) | Change |
|---|---|
| `scripts/factory_core/adapter_defaults.py` | Add skill-security globs to `safety.migration_seed_auth_patterns`, `safety.critical_diff_paths`, `safety.hard_exclude_paths` (universal baseline) |
| `.factory/adapter.yaml` | Add the same globs to dark-factory's own three `safety.*` lists (list-replace merge semantics require both — see A4) |
| `scripts/gate_blast_radius.py` | New `"skill-security"` trigger category, sub-classified from matched-pattern text; `trigger_label` precedence updated so it can surface as the top-line label |
| `scripts/diff_rank.py` | `_safety_signal()` gains a `"skill_security_path"` branch (checked before the existing `"dark-factory"` branch to avoid `.factory/hooks/` colliding with `factory_path`); `.claude/skills/**/SKILL.md` added to `critical_diff_paths` for visibility only (SKILL.md is never in the blocking list) |
| `.claude/skills/code-review/RUBRIC.md` | New checklist items: content-level skill-security findings (allowed-tools/disallowed-tools/hooks/context:fork/model-effort-overrides/plugin-MCP-config), dynamic-shell-injection operational test, `skill-security` category, `# justification:` downgrade rule |
| `.claude/skills/conformance/RUBRIC.md` | Explicit callout: skill/settings/hooks/plugin/MCP paths are **never** covered by the documentation exemption — any such change not named in the spec is `[OOS]`, regardless of how beneficial it looks |
| `tests/test_blast_radius.py` | New tests: `.claude/settings.json`/skill-scripts/`.factory/hooks/**` → `HUMAN_REQUIRED` + `TRIGGER: skill-security`; `SKILL.md` alone → no path-based trigger; a second fixture pointed at dark-factory's real `.factory/adapter.yaml` (not just the hermetic adapter-free default) to guard the A4 merge-semantics gap |
| `tests/test_adapter.py` | Assert new globs present (compilable regex / non-empty substrings) in both `DEFAULTS` and dark-factory's adapter.yaml for all three lists |
| `tests/test_diff_rank.py` | Classification test for the new `skill_security_path` signal |
| A new lint-style test (e.g. `tests/test_rubric_skill_security.py`) | Presence assertions that both RUBRIC.md files contain the required instruction strings |

### Pattern set

Applied consistently across all three adapter lists (regex form for `migration_seed_auth_patterns`
/`critical_diff_paths`; plain-substring form for `hard_exclude_paths`, per
`epic_autopilot.py::hard_excluded`'s `if ex in p` matching):

- `.claude/skills/**/scripts/**` — **blocking** (executable skill code)
- `.claude/settings.json`, `.claude/settings.local.json` — **blocking** (whole file is the
  permission/hooks/MCP surface)
- `.mcp.json` — **blocking** (project-level MCP config, if present)
- `.claude/plugins/**`, `.claude-plugin/**` — **blocking** (plugin/marketplace config, if
  present — see Assumptions)
- `.factory/hooks/**` — **blocking** (dark-factory's own pipeline-hook scripts, per #42 §7)
- `.claude/skills/**/SKILL.md` — **visibility only** (`critical_diff_paths` +
  `hard_exclude_paths`), never in `migration_seed_auth_patterns`; content is instead judged by
  the RUBRIC personas per Q2/A2

### `gate_blast_radius.py`: `skill-security` category

`classify_file()` currently appends the literal string `"migration-seed"` for any
`migration_seed_auth_patterns` match. Change it to inspect the matched pattern's source text
(mirroring `diff_rank.py::_safety_signal()`'s existing per-substring technique) and emit
`"skill-security"` when the pattern references the skill/settings/hooks/plugin/MCP surface,
falling back to `"migration-seed"` otherwise — so a deploy/CI-publish match is never
mislabeled and a skill/settings match is never hidden inside the generic bucket. `main()`'s
`trigger_label` selection gains `skill-security` into its precedence (`hotspot` >
`skill-security` > `migration-seed` > `size`) so it can win the top-line label.

No change is needed in `workflows/archon-dark-factory.yaml`'s `report` node — it already
renders `blast.md`'s `TRIGGER` value verbatim into the issue comment's "Blast-Radius Gate"
section (`⛔ Blocked — trigger: ${BLAST_TRIGGER}`). Once `gate_blast_radius.py` emits
`skill-security`, the report legibly calls it out with zero additional templating work,
satisfying requirement 5 for free.

### RUBRIC content additions

**`code-review/RUBRIC.md`** gains a new item under "What to judge" → Security, with:
- The dynamic-shell-injection operational test from A4 (unescaped interpolation into an
  executed command vs. argv-list/quoted invocation), scoped to
  `.claude/skills/**/scripts/**` and `.factory/hooks/**`.
- A named list of content-level triggers to check in any touched `SKILL.md` or
  `.claude/settings.json`: `allowed-tools`/`disallowed-tools` broadening (especially any bare
  `Bash(*)` or family-level wildcard like `Bash(git:*)`/`Bash(gh:*)` — per #42 §4), a new/changed
  `hooks` entry, `context: fork`, model or effort overrides, and plugin/MCP config changes.
  Each is a `high` or `critical` finding depending on blast radius (mirroring the existing
  severity rubric already in the file).
- The `# justification:` downgrade rule from A4: a substantive justification comment
  immediately above the changed field downgrades the finding to advisory, but the reviewer must
  still note in the finding description that human sign-off on the PR is expected.
- `skill-security` added to the `## Categories` line.

**`conformance/RUBRIC.md`** gains one explicit sentence under the Documentation exception in
`## Out-of-Scope Changes`: skill/settings/hooks/plugin/MCP paths are never covered by that
exemption, so any such change absent from the spec must be flagged `[OOS]` even if it looks
like harmless hygiene.

---

## Alternatives Considered

1. **A new dedicated deterministic gate script** (e.g. `gate_skill_security.py`), separate from
   blast-radius/diff-rank. **Rejected** (Q1/A1) — the issue's own "existing code review/
   conformance gates remain authoritative" criterion and the parent epic's preservation
   requirement both argue for extending, not adding a competing authority; a new gate script also
   duplicates the path-classification logic that already exists in two places.
2. **Block on `.claude/skills/**/SKILL.md` path alone**, treating any SKILL.md touch as
   `HUMAN_REQUIRED`. **Rejected** (Q2/A2) — indistinguishable from a harmless prose edit at the
   path level; would produce enough false-positive blocks to train the team toward reflexively
   stripping `needs-discussion`, defeating the gate's purpose over time. Content-level judgment
   via the RUBRIC personas is more precise and still blocking when it matters.
3. **Reuse the generic `"migration-seed"` trigger label** rather than adding `"skill-security"`.
   **Rejected** (Q3/A3) — "reports call out skill/security-sensitive changes" requires legibility;
   a human reading "trigger: migration-seed" for a skill-script change has to inspect
   `TRIGGERED_FILES` to learn what actually happened, which defeats the purpose of a labeled
   trigger.
4. **Add the new globs to `adapter_defaults.py` `DEFAULTS` only**, relying on it as the single
   source of truth (DRY). **Rejected** (Q4/A4) — `adapter.py::_deep_merge`'s list-replace (not
   concatenate) semantics mean dark-factory's own `.factory/adapter.yaml` overrides, not extends,
   the three `safety.*` lists; DEFAULTS-only would leave dark-factory's own PRs — including this
   ticket's — unprotected. Both locations are required.

---

## Open Questions (Non-blocking)

- **Plugin/marketplace config file convention is unverified in this repo.** No
  `.claude/plugins/`, `.claude-plugin/`, or `.mcp.json` file exists in this repo today (see
  Assumptions); the glob set is a best-effort guess at Claude Code's actual on-disk convention.
  If the platform's real convention differs, the pattern list should be revisited once a plugin
  or MCP config file actually appears in a target repo.
- **The CI-checkable `allowed-tools` lint** that #42 §4 describes as a follow-up (rejecting bare
  `Bash(*)`/family wildcards in any `SKILL.md` frontmatter, parallel to
  `check_workflow_dag.py`/`check_workflow_when.py`) is a deterministic, always-on check that is
  arguably stronger than the RUBRIC-persona judgment this ticket adds. It remains out of scope
  here (this ticket implements #42 §7's review-expectations follow-up, not §4's lint follow-up)
  but is a natural next ticket under the same parent epic.
- **`.factory/adapter.yaml`'s glob syntax difference** (plain-prefix substring for
  `hard_exclude_paths` vs. anchored regex for `critical_diff_paths`/`migration_seed_auth_patterns`)
  is pre-existing and out of scope to unify here; this ticket follows each list's existing
  convention rather than introducing a third format.

## Assumptions

- **[Flagged]** The plugin/marketplace config glob set (`.claude/plugins/**`,
  `.claude-plugin/**`) and the project-level `.mcp.json` path are assumed based on common Claude
  Code conventions; this repo has zero existing instances of any of them to verify against
  (confirmed: only `.claude/settings.local.json` exists today, holding an inline `mcpServers`
  key rather than a separate `.mcp.json`).
- **[Flagged]** `.factory/hooks/**` is assumed in scope even though the issue's own bullet list
  never names that literal path (only "hooks" generically) — based on the #42 spec's explicit,
  repeated precedent (Q3/A3). If a future reader disagrees with that reading, the exclusion
  should be revisited against the issue author's intent directly.
- The blast-radius gate's `HUMAN_REQUIRED` status is assumed to remain the correct "human review"
  mechanism for whole-file-sensitive paths; if a future change to `dark-factory-validate.md`
  removes or weakens that gate, this spec's requirement 2 should be re-verified against
  whatever replaces it — per CLAUDE.md, gate changes get their own reviewed ticket regardless.
- `epic_autopilot`'s `enabled: false` kill-switch is assumed to remain off through this ticket's
  implementation; the `hard_exclude_paths` additions are forward protection for if/when it is
  enabled, not a currently-exercised code path.

# Dark Factory Conformance and Code-Review Skills Design

**Issue:** omniscient/dark-factory#44
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#42 (CLOSED — Claude Skills conventions and safety policy;
governs naming/layout/tooling for this ticket)
**Related:** omniscient/dark-factory#43 (sibling ticket — splits the *refine/plan* personas
(orchestrator, product-owner, architect) into a `refinement` skill; disjoint scope from this
ticket), omniscient/dark-factory#158 (CLOSED — diff ranking; already implements the escalation
behavior this issue's acceptance criteria ask for)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36

---

## Overview / Problem Statement

Dark Factory's conformance gate (Gate 2, `commands/dark-factory-conformance.md`) and code-review
gate (Gate 3, `commands/dark-factory-code-review.md`) each drive a reviewer subagent from a
persona prompt file — `conformance-reviewer-prompt.md` and `code-review-reviewer-prompt.md` —
that today lives in `refinement-skills/`, baked into the Docker image at build time as
`/opt/refinement-skills/`. Editing either prompt requires a full
`docker compose --profile factory build`. Neither file is a real Claude Code Skill (no
`.claude/skills/<name>/SKILL.md` exists for either), and both carry a stale product label —
both open with "for the MarketHawk dark factory pipeline" even though Dark Factory is a
generic mechanism that also targets its own repo (this one).

**A live gap this ticket closes:** `commands/dark-factory-code-review.md` (line 77) and
`tests/test_code_review_command.py` (line 13) already read/assert a clone-live path —
`.claude/skills/refinement/code-review-reviewer-prompt.md` — that does not exist anywhere in
the repo (only `.claude/skills/refinement/config.yaml` exists today). That reference was
added in commit `b9b7f7e` (#218, 2026-06-04), before issues #36/#41/#42/#43/#44 existed, and
appears to have anticipated a migration that was never finished. Because `code_review.fail_open`
defaults to `true`, a missing/unreadable reviewer prompt does not hard-fail the gate — it most
likely degrades Gate 3 to a silent advisory no-op. This ticket's job is to actually create the
file the command already expects (at a corrected location — see Alternatives Considered §1) and
add config-precedented fallback so this class of gap can't recur silently.

**Already solved, not this ticket's job:** #158 (diff ranking, closed) already implements
`dark-factory/scripts/diff_rank.py`, wired into both gate commands, which risk-classifies every
changed file and lets `critical`-tier files (safety/auth/trading/migration/factory paths, or a
codeindex hotspot) bypass the token cap entirely ("emit critical files (bypass cap)" in
`build_ranked_diff()`). The acceptance criteria's "escalate security/trading/auth/high-blast-radius
changes to broader context" is therefore already satisfied structurally. This ticket does not
add new ranking/escalation logic — it inherits #158's behavior unchanged.

---

## Requirements

Distilled from the issue's acceptance criteria, refined through Q&A below:

1. Two new Claude Code Skills, named per the merged #42 policy's bare-capability-noun rule:
   `.claude/skills/conformance/` and `.claude/skills/code-review/` — each with a concise
   `SKILL.md` and a `RUBRIC.md` carrying the reviewer rubric content.
2. `RUBRIC.md` content is a faithful, behavior-preserving copy of the current
   `conformance-reviewer-prompt.md` / `code-review-reviewer-prompt.md`, with the "MarketHawk"
   mislabel corrected (two occurrences per file: the H1 title and the opening sentence) to
   product-agnostic wording. No other content changes.
3. All three command call sites that read these prompts today
   (`commands/dark-factory-plan.md` Phase 3.5, `commands/dark-factory-conformance.md`,
   `commands/dark-factory-code-review.md` — and their `.archon/commands/` mirrors) resolve the
   prompt clone-live-first, falling back to the existing baked `/opt/refinement-skills/` copy —
   mirroring the precedent already established for `config.yaml` at `entrypoint.sh:40`.
4. `context_budget.py` (and its self-fallback copy at `dark-factory/scripts/context_budget.py`)
   is updated so the `skill_prompts` budget section for the `conformance` and `code-review`
   scenarios measures the same file the command actually injects, using the same
   clone-live-first/baked-fallback resolution, scoped only to these two prompt files (the other
   three — orchestrator/product-owner/architect — are sibling issue #43's concern and keep
   resolving to the baked path until #43 lands).
5. Diff-ranking / escalation consumption is unchanged (already implemented by #158; verified
   above) — this ticket does not modify `diff_rank.py`.
6. The current conformance/code-review output contract is preserved byte-for-byte: verdict
   tiers and table format, the `### Findings` pipe-delimited bullet format
   (`- [severity] category | path:line | description`), the severity vocabulary
   (`critical|high|medium|low`), and the `## Out-of-Scope Changes` section with its formatter
   and documentation exceptions — all load-bearing for `code_review_payload.py`'s
   `_FINDING_RE` parser and `dark-factory-conformance.md`'s verdict/OOS-bullet parsing.
7. `Dockerfile:126`'s `COPY refinement-skills/ /opt/refinement-skills/` and the
   `refinement-skills/` source directory are **not** touched by this ticket — the baked copies
   must keep existing so (a) the fallback in requirement 3 has something to fall back to, and
   (b) sibling #43 still needs the other three prompts baked until it lands.

---

## Brainstorming Q&A

Three product-owner subagents were consulted (re-framed for real Dark Factory context, since the
container's baked `/opt/refinement-skills/product-owner-prompt.md` template is itself a
MarketHawk-mismatched file — see Assumptions).

> **Q1: Given the merged #42 naming convention conflicts with the earlier, MarketHawk-cross-contaminated
> spec's literal wording, and given the already-committed code-review command references a
> `refinement/`-nested path, what should the two new skills be named, and what
> `disable-model-invocation`/`allowed-tools` should they carry?**
>
> **A1:** Bare capability nouns — `.claude/skills/conformance/` and `.claude/skills/code-review/`
> — per #42 §2 ("reference skills use a bare capability noun... do not reuse the
> `dark-factory-<phase>` prefix... reserved for Archon commands"). The `dark-factory-conformance`
> / `dark-factory-code-review` names from the earlier bad spec are literally the existing Archon
> **command** filenames (`commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`)
> — reusing them for skills would collide with the reserved prefix and erase the category
> boundary §2 exists to preserve. Both skills are pure read-only reviewer personas (no GitHub
> posting, no board moves, no repo mutation — those stay in the command shells per #42 §1), so
> per #42 §3, `disable-model-invocation` may be omitted (default false/model-invokable) and
> `user-invocable` stays at its default `true`. Per #42 §4's read-only tier:
> `allowed-tools: Read, Grep, Glob` — no `Bash` grant needed since inputs arrive via
> pre-populated placeholders (`$SPEC_CONTENT`, `$ARTIFACT_CONTENT`, `$DIFF_CONTENT`,
> `$ISSUE_CONTEXT`), not by the persona shelling out itself. Each skill has exactly one
> supporting rubric file — below #42's "graduate to `templates/` at ≥3 files" threshold — so
> `RUBRIC.md` stays flat at the skill root, not in a subdirectory.

> **Q2: Should the three command call sites add a fallback (clone-live-first, baked-fallback),
> or cut over completely? Should `context_budget.py` be updated in this same ticket? Should the
> Dockerfile change?**
>
> **A2:** Fallback, not a hard cutover — this is an explicit acceptance criterion, and it mirrors
> an existing precedent: `entrypoint.sh:40` already resolves `config.yaml`
> clone-live-first (`${CLONE_DIR}/.claude/skills/refinement/config.yaml`), baked-fallback
> (`/opt/refinement-skills/config.yaml`). A hard cutover risks the exact live gap this ticket is
> fixing (code-review's already-broken reference to a nonexistent clone-live file with no
> fallback). `context_budget.py` **should** be updated in this same ticket, scoped to only the
> two files this issue owns — its `_SECTION_REGISTRY` already includes `skill_prompts` for both
> the `conformance` and `code-review` scenarios (lines 31-32), and if the probe keeps measuring
> only the old baked path after the command switches to reading the new clone-live file, the
> budget artifact silently reports on a file that's no longer what's actually injected. This
> requires refactoring `_SKILL_PROMPT_FILES` from a flat list + single `_SKILL_PROMPT_DIR` join
> into a per-file path-candidate resolution, since three of the five prompts remain baked-only
> until #43 lands. The **Dockerfile must not change** in this ticket — `refinement-skills/`
> still needs to bake all five prompts until #43 also lands, and this ticket's own fallback
> depends on the baked copies continuing to exist. Full retirement of the baked path is a
> follow-up, once both #43 and #44 have landed and no clone still needs the fallback.

> **Q3: Given "preserve current output contract" is an explicit acceptance criterion, should
> RUBRIC.md content change beyond the MarketHawk-label fix — including the Hermes Agent
> comments' proposed YAML schema, multi-reviewer fan-out panel, "Nodding Loop" detection, and
> "default to doubt" principle?**
>
> **A3:** RUBRIC.md is a faithful, behavior-preserving copy plus exactly the MarketHawk fix (two
> spots per file: H1 title and opening sentence, replaced with product-agnostic wording — not a
> new `$FACTORY_PRODUCT_NAME`-style injection token, since that would require editing the
> commands' placeholder-substitution logic too, beyond this ticket's scope). The YAML schema and
> multi-reviewer fan-out panel directly conflict with "preserve output contract" —
> `code_review_payload.py`'s `_FINDING_RE` parses the pipe-delimited bullet format, and
> `dark-factory-conformance.md` regex-parses the `**Verdict:**` line and `[OOS]` bullets; neither
> understands a YAML `findings:` list, and a fan-out panel changes gate dispatch/verdict
> vocabulary entirely — the kind of gate-semantics change CLAUDE.md's hard limits reserve for its
> own reviewed ticket. Per §Q5/A5 of the merged #42 spec (which faced an identical situation with
> the same "Hermes Agent" post-hoc comments), these are recorded as non-blocking Open
> Questions/Future Work, not adopted here. "Nodding Loop" detection isn't prompt content at all —
> it requires tracking a reviewer's verdict history across many runs, which a single-invocation
> subagent has no visibility into; it belongs as a future monitor over the conformance/code-review
> subsystem, not skill content. "Default to doubt" is format-safe (touches no parsed token) but is
> still a behavioral change beyond "extract + delabel," so it's deferred too — flagged as the
> easiest of the four to greenlight next, should a future ticket take it up.

---

## Architecture / Approach

### 1. New skill directories

```
.claude/skills/conformance/
  SKILL.md      # concise: name, description, disable-model-invocation omitted,
                #   user-invocable: true, allowed-tools: Read, Grep, Glob; points to RUBRIC.md
  RUBRIC.md     # migrated conformance-reviewer-prompt.md content (see §2)

.claude/skills/code-review/
  SKILL.md      # same shape
  RUBRIC.md     # migrated code-review-reviewer-prompt.md content (see §2)
```

Naming and frontmatter per Q1/A1 above — bare capability nouns, flat layout (single supporting
file, below the `templates/` graduation threshold), read-only `allowed-tools`,
`disable-model-invocation` omitted.

### 2. RUBRIC.md content

Copy `refinement-skills/conformance-reviewer-prompt.md` → `.claude/skills/conformance/RUBRIC.md`
and `refinement-skills/code-review-reviewer-prompt.md` → `.claude/skills/code-review/RUBRIC.md`
verbatim, except:
- `# Conformance Reviewer — MarketHawk` → `# Conformance Reviewer — Dark Factory Pipeline` (or
  equivalent product-agnostic phrasing); same for the code-review title.
- "You are a conformance reviewer for the MarketHawk dark factory pipeline." → "...for the Dark
  Factory pipeline." (drop the product name, keep "Dark Factory" as the mechanism's own name,
  since this repo's own CLAUDE.md already establishes that framing); same substitution pattern
  for the code-review opening sentence.
- No other content changes (verdict tiers, table, `### Findings` format, severity vocabulary,
  `## Out-of-Scope Changes` section including its formatter and documentation exceptions, all
  placeholder tokens `$ARTIFACT_KIND`/`$SPEC_CONTENT`/`$ARTIFACT_CONTENT`/`$ISSUE_CONTEXT`/
  `$DIFF_CONTENT`) — all preserved exactly, per requirement 6.

The source files at `refinement-skills/conformance-reviewer-prompt.md` and
`refinement-skills/code-review-reviewer-prompt.md` are **left in place** (not deleted) so the
baked `/opt/refinement-skills/` copy — and this ticket's own fallback — keep working (§5).

### 3. Command wiring (3 call sites + `.archon/commands/` mirrors)

Each site changes its prompt-read step to resolve clone-live-first, baked-fallback:

- `commands/dark-factory-conformance.md` (Phase 1, step 3): read
  `.claude/skills/conformance/RUBRIC.md` first, fall back to
  `/opt/refinement-skills/conformance-reviewer-prompt.md`.
- `commands/dark-factory-plan.md` (Phase 3.5, step 1) — it independently reads
  `conformance-reviewer-prompt.md` for the plan-vs-spec check: same resolution.
- `commands/dark-factory-code-review.md` (Phase 3, step 2): **correct** the existing stale
  reference from `.claude/skills/refinement/code-review-reviewer-prompt.md` (pre-#42, see
  Alternatives §1) to `.claude/skills/code-review/RUBRIC.md` first, falling back to
  `/opt/refinement-skills/code-review-reviewer-prompt.md`.
- Mirror all three edits into their `.archon/commands/` copies (this repo keeps `commands/*.md`
  and `.archon/commands/*.md` in sync, per `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`'s
  inventory table).
- Update `tests/test_code_review_command.py`'s assertion from
  `.claude/skills/refinement/code-review-reviewer-prompt.md` to
  `.claude/skills/code-review/RUBRIC.md`, and update/replace `tests/test_code_review_prompt.py`
  and `tests/test_conformance_prompt_formatter_rule.py` (which currently hardcode
  `refinement-skills/<name>-reviewer-prompt.md` as their `PROMPT` path) to point at the new
  `.claude/skills/<name>/RUBRIC.md` location as the canonical source, since that becomes the
  file actually injected into the reviewer subagent.

### 4. `context_budget.py`

Refactor `_SKILL_PROMPT_FILES`/`_SKILL_PROMPT_DIR` (in both `scripts/context_budget.py` and its
self-fallback copy `dark-factory/scripts/context_budget.py`) from a flat file list + single
directory join into a per-file candidate-path list, so `conformance-reviewer-prompt.md` and
`code-review-reviewer-prompt.md` resolve `.claude/skills/<name>/RUBRIC.md` first with
`/opt/refinement-skills/<name>-reviewer-prompt.md` fallback, while the other three
(orchestrator/product-owner/architect) keep resolving to the baked path only, until #43 migrates
them. Check `tests/test_context_pack.py` (line ~201, per Q2/A2) for any assumption that
`skill_prompts` is a single container-mounted, env-independent section, and update it to match
the new per-file resolution if the current test shape doesn't already tolerate it.

### 5. Dockerfile / `refinement-skills/` source

No change (requirement 7). `Dockerfile:126`'s `COPY refinement-skills/ /opt/refinement-skills/`
stays as-is, and both prompt files stay in `refinement-skills/` so the baked fallback keeps
working. Full retirement (removing the COPY line and the source files) is explicit future
follow-up, once both #43 and #44 have landed and no target clone still needs the fallback.

### 6. Diff-ranking / escalation

No changes. `dark-factory/scripts/diff_rank.py` (#158, closed) already implements the
risk-tiered ranking and critical-tier cap-bypass both gate commands already call; this ticket
inherits it unchanged.

---

## Alternatives Considered

1. **Nest both prompts inside the existing `.claude/skills/refinement/` skill** (as
   `commands/dark-factory-code-review.md`'s already-committed reference and the merged #42
   spec's illustrative "concrete target state" both suggest). **Rejected.** The #42 worked
   example was explicitly inventory-based ("the one skill this ticket's inventory identifies
   today") and its own text defers the actual split to follow-up implementation tickets — which
   are #43 (refine/plan) and #44 (this ticket, conformance/code-review), already filed and
   scoped by phase-pairing rather than lumped into one skill. `conformance-reviewer-prompt.md` is
   also semantically cross-cutting — consumed by both the plan phase (Phase 3.5) and the
   conformance phase, not a refine/plan-specific persona the way orchestrator/product-owner/
   architect are — which argues for its own skill rather than folding it into `refinement`. The
   existing code-review command's reference to a `refinement/`-nested path predates #42 by over
   a month (commit `b9b7f7e`, 2026-06-04) and is treated as a stale, superseded placeholder, not
   a binding prior decision — see Assumptions.
2. **Adopt the Hermes Agent comments' YAML output schema and/or multi-reviewer fan-out panel
   now.** **Rejected.** Both directly conflict with the "preserve current output contract"
   acceptance criterion — breaking `code_review_payload.py`'s pipe-delimited parser and
   `dark-factory-conformance.md`'s verdict/OOS-bullet regex parsing — and the fan-out panel is a
   gate-semantics change that CLAUDE.md's hard limits reserve for its own reviewed ticket
   ("Never weaken safety gates... as a side effect of another change").
3. **Chosen:** two dedicated bare-noun skills (`conformance`, `code-review`), rubric content
   copied faithfully plus the MarketHawk-label fix, clone-live-first/baked-fallback wiring at all
   three command call sites plus `context_budget.py`, Dockerfile and `refinement-skills/` source
   left untouched pending #43.

---

## Open Questions (Non-blocking)

- **YAML output schema / multi-reviewer fan-out panel** (from the Hermes Agent comments) —
  recorded as future work, not adopted, mirroring the merged #42 spec's Q5/A5 disposition of
  analogous later-comment proposals.
- **"Nodding Loop" detection** — not expressible as prompt content (needs cross-run verdict
  history a single-invocation subagent can't see); would need to live as a monitor over the
  conformance/code-review subsystem itself, tracked separately if pursued.
- **"Default to doubt / maker never validates maker" reviewer-behavior framing** — format-safe
  (adds no parsed token) but still a behavioral change beyond this ticket's "extract + delabel"
  scope; flagged as the lowest-risk of the four Hermes proposals to take up next, in its own
  small follow-up.
- **Full retirement of the Dockerfile `COPY`/`refinement-skills/` baked path** — follow-up once
  both #43 and #44 have landed and no clone still depends on the fallback.
- **`tests/test_context_pack.py`'s `skill_prompts` shape assumptions** — flagged for the
  implementer to verify during Phase 4; if the per-file resolution needs more than a small edit
  to accommodate, that's a signal to file a narrow follow-up rather than expand this ticket.
- **Separately, and out of scope for #44 itself:** the *other* three baked prompts —
  `orchestrator-prompt.md` and `product-owner-prompt.md` — carry the same MarketHawk-mismatch
  defect (they describe "MarketHawk, a full-stack stock scanning platform," not Dark Factory)
  at a deeper level than a label: their entire persona framing assumes the wrong product. This
  was discovered incidentally during this refinement pass (it caused a prior, now-superseded
  refinement run on this same issue to post a spec referencing the wrong repo/branch). It's
  sibling issue #43's content to fix, not #44's — noted here for traceability, and flagged
  directly on the issue thread (see published comment) since it actively degrades every
  self-target refine/plan run until fixed.

---

## Assumptions

- **[Flagged]** `.claude/skills/<name>/RUBRIC.md` is assumed to be the right filename for each
  skill's core rubric content, matching the issue's own "supporting rubric files" wording; #42's
  layout convention doesn't name a canonical filename for a single-file skill (only the ≥3-file
  `templates/` graduation rule), so this is a reasonable but not policy-dictated choice.
- **[Flagged]** The already-committed `.claude/skills/refinement/code-review-reviewer-prompt.md`
  reference in `commands/dark-factory-code-review.md` and `tests/test_code_review_command.py` is
  treated as a stale, pre-policy placeholder (predates #36/#41/#42/#43/#44) rather than a binding
  prior decision this ticket must preserve — see Alternatives §1. If a future maintainer disagrees
  with that reading, this spec's destination path should be reconciled against that committed
  reference instead.
- The three command-file call sites (`dark-factory-plan.md` Phase 3.5, `dark-factory-conformance.md`,
  `dark-factory-code-review.md`) plus `context_budget.py` are assumed to be the complete set of
  consumers of these two prompt files — verified by a repo-wide grep for both filenames.
- Evaluation/rollout tier is assumed to be Tier 1 (structural/consolidation) per the merged #42
  spec §8 — a targeted smoke check (`smoke_gate.sh` + a scratch conformance/code-review dry run),
  not a full bench sweep — matching #42's own explicit Tier-1 example, "the §2 `refinement` move,"
  which this ticket's move is directly analogous to.
- The product-owner subagents consulted during this refinement pass were re-framed manually for
  real Dark Factory context rather than using the container's baked
  `/opt/refinement-skills/product-owner-prompt.md` template verbatim, since that template is
  itself MarketHawk-mismatched (see Open Questions). If that template is fixed by #43 or a
  dedicated ticket before this spec is implemented, future Q&A on this issue should use the
  corrected template.

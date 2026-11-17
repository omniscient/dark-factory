# Docs conformance gate — verify documentation against the final implementation diff before PR publication

**Issue:** #535

---

## Overview / Problem Statement

Dark Factory has no mechanism that checks whether documentation actually describes a
change correctly. The only existing hook is `commands/dark-factory-implement.md` Phase 4
DOCUMENT (#55): it classifies `git diff main...HEAD --name-only` paths against a fixed
mapping table and updates the matched doc section — but if a path matches nothing, Phase
4 **silently skips**, with no accuracy check and no coverage check. Four reference docs
landed in #520–#523; later behavior changes in #519/#524/#525 drifted them, requiring a
separate catch-up PR (#533, docs-conformance's own motivating example). Nothing in the
pipeline would have caught that drift before merge, and nothing stops it recurring.

Both existing quality gates are diff-blind to documentation content: Gate 2
(`commands/dark-factory-conformance.md`, code-vs-spec) and Gate 3
(`commands/dark-factory-code-review.md`, post-publish code review) both build their diff
with `':!docs/*.md'` in the pathspec (Step 3.0.1 / mirrored in code-review) — the
`docs/` directory's Markdown content never reaches either reviewer (confirmed by the
`#399` exclusion-token tests, `tests/test_gate_diff_md_visibility.sh` et al. — note those
tests confirm `commands/*.md` policy Markdown is *not* excluded today, only `docs/*.md`
is). Gate 3 also runs after the PR is already published, so it is structurally the wrong
place to block an inaccurate-docs merge pre-publication even if its diff were widened.

This spec adds a **third, independent, read-only gate** — "Docs Conformance (Gate
2.5)" — that runs after Gate 2 and before `push-and-pr`, sees the full post-Gate-2 diff
(code, config, workflow, and policy Markdown *and* `docs/**`), and (in `enforce` mode)
blocks the PR from being published when the proposed documentation doesn't actually
describe that diff, or when a "no documentation impact" claim isn't defensible.

Per spec-review feedback (see Requirement 13), the first release ships in **`observe`
mode**: the gate still runs end-to-end and records a real verdict every run, but does
not block `push-and-pr` on it. Flipping the default to `enforce` is a separate,
human-reviewed change tracked in #536, informed by the false-block rate observed from
this ticket's recorded verdicts.

## Requirements (from Q&A)

1. **Own command + DAG node**, not a phase folded into Gate 2's session (Q&A 1):
   `commands/dark-factory-docs-conformance.md`, DAG node id `docs-conformance`, gated by
   a `docs-conformance-gate` bash node — mirroring the Gate 2/Gate 3 one-command-one-node
   pattern. Reasons: Gate 2's session has a reconcile loop that edits code, which would
   make a folded-in docs check neither independent nor read-only; `conformance.md`'s
   `STATUS:` line already has three consumers (`verdict_gate_check.sh`,
   `budget-conformance` telemetry, the `conformance:` config block) that all assume one
   verdict per file.
2. **Single-pass, read-only, no reconcile loop** (Q&A 2): the command spawns exactly one
   Opus-pinned reviewer subagent (Glob/Grep/Read only), parses its verdict, and writes
   `$ARTIFACTS_DIR/docs-conformance.md` once. No `max_reconcile_cycles`-equivalent knob.
   In `enforce` mode, on material mismatch it posts evidence and exits; the branch stays
   unpublished; the only remediation path is a human edit or a subsequent `Continue` run,
   which reruns implement Phase 4 DOCUMENT, Gate 2, and this gate from scratch. In the
   shipped default `observe` mode a material mismatch is still recorded and still posted
   as an informational comment, but does not withhold publication or add
   `needs-discussion` — see Requirement 13. The command's own session makes no
   edits/commits/pushes beyond writing its verdict file and posting its comment,
   regardless of mode.
3. **Fail-closed verdict gate in `enforce` mode, not `verdict_gate_check.sh` reused
   unchanged** (issue body + Q&A 1): a new `scripts/docs_verdict_gate_check.sh` that, when
   running in `enforce` mode, treats only `STATUS: PASS` or `STATUS: SKIPPED` as
   "proceed." `BLOCKED`, `ERROR`, and missing/unparseable all fail closed in that mode.
   This deliberately diverges from `verdict_gate_check.sh`'s existing
   PASS/SKIPPED/ERROR-all-proceed behavior, which is correct only for Gate 3's documented
   `code_review.fail_open: true` contract and is not reusable here unchanged. In the
   shipped default `observe` mode, this same script always proceeds (exit 0) regardless
   of `STATUS:` — see Requirement 13 for the full mode contract. The two modes live in
   one script (a mode parameter), not two scripts, since the parsing/messaging logic is
   otherwise identical and `enforce` is the only path that needs the stricter accept-list.
4. **Doc-impact handoff, audited not trusted** (Q&A 3a): implement's Phase 4 DOCUMENT
   writes a durable `$ARTIFACTS_DIR/doc-impact.md` — per changed behavior, the doc
   file+section updated, or (when nothing is affected) an explicit `NO_IMPACT:` statement
   with reasons — plus a one-line pointer in `implementation.md`. The docs-conformance
   reviewer reads `doc-impact.md` only as a claim to audit: it independently derives what
   the diff requires first, then checks the claim against that independent view, so a
   false `NO_IMPACT:` claim is itself a reportable mismatch. Missing/empty/incomplete
   `doc-impact.md` fails closed as `BLOCKED`.
5. **Diff scope**: the reviewer's diff is `git diff main...HEAD` with the *same*
   exclusions Gate 2/3 use for noise (`*.lock`, `evals/*.md`, `bench/*.md`,
   `.archon/memory/**`) **minus** the `':!docs/*.md'` token — i.e. it is the one gate in
   the pipeline that can see doc content, specifically so it can compare doc claims
   against the code/config/workflow/policy-Markdown diff Gate 2/3 already see.
6. **Scope coverage**: both target structural docs (the existing Phase 4 mapping:
   `ARCHITECTURE.md`, `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`,
   `CLAUDE.md` where touched) and Dark Factory's own reference docs (`docs/run-lifecycle.md`,
   `docs/scheduler.md`, `docs/features.md`, `docs/configuration.md`), plus policy Markdown
   (`commands/**`, `.claude/skills/**` rubrics) when its diff changes documented behavior.
7. **Must not fight the existing OOS doc exemption**: Gate 2's Phase 3.6.0 already treats
   every doc-file change as in-scope housekeeping that is never excised or ticketed. This
   gate only judges *accuracy*, never scope — it must not reintroduce doc-file OOS
   ticketing or excision.
8. **Naming**: branded "Gate 2.5" (gate-label string `"Docs Conformance (Gate 2.5)"`), not
   a renumbering of Gate 3 → Gate 4 (Q&A 3b) — the literal `"Code Review (Gate 3)"` string
   and "Gate 3" prose appear in enough call sites and tests that renumbering would be
   pure, high-risk churn for no behavioral benefit.
9. **`new`/`continue` only**, same as Gate 2/3 — `resolve` does not run implementation and
   has no diff to check.
10. **Remediation reruns both gates**: a subsequent `Continue` run must rerun Gate 2
    *and* this gate (never let a docs-only fix skip Gate 2) — satisfied automatically by
    both gates being plain DAG nodes on the `new`/`continue` branch with no run-level
    memoization.
11. **Tests**: cover a policy-Markdown-only change, a scheduler/config/artifact change, an
    ordinary target structural change, a docs-neutral change (defensible `NO_IMPACT`), and
    a deliberately misleading-but-present doc update — plus the gate's own fail-closed
    paths (missing/malformed/`ERROR`/timeout/refusal all block **in `enforce` mode**; no
    PR created in any blocking case). CI drives these with a stubbed reviewer verdict
    rather than a real model call — see Requirement 14 for how, and why semantic accuracy
    itself isn't a CI assertion.
12. **Document the gate** in `docs/run-lifecycle.md`, `docs/features.md`,
    `docs/configuration.md`, and `/opt/refinement-skills/VERIFIER-CONTRACT.md`.
    `docs/scheduler.md` and `CLAUDE.md` are explicitly out of scope (Q&A 3b) — the
    scheduler never sees per-gate nodes and no new board status/label is introduced.
13. **Ship in `observe` mode; `enforce` is a separate change (#536)** (spec-review
    feedback): `docs_conformance.mode: observe|enforce`, default `observe`. The gate
    always runs fully in both modes — diff build, reviewer spawn, verdict write,
    informational comment on mismatch — so the recorded verdict is real production
    signal, not a disabled no-op. Only the *consequence* of a non-passing verdict differs:
    - `observe` (default): `docs_verdict_gate_check.sh` proceeds (exit 0) regardless of
      `STATUS:`, logging to stderr what it would have done in `enforce` mode. The
      command's own Phase 6 (ON BLOCKED) posts an informational, non-blocking comment
      (`<!-- df-docs-conformance-observed -->`) and does **not** add `needs-discussion`
      or move the board — a BLOCKED verdict in `observe` mode must not cost a human
      touch or a `Continue` rerun, which is the whole point of shipping observe-first
      while the checker's false-block rate is unmeasured.
    - `enforce`: Requirements 2/3's fail-closed behavior applies unchanged — BLOCKED/
      missing/unparseable/timeout/refusal all block `push-and-pr`, post the blocking
      evidence comment, and add `needs-discussion`.
    Timeouts and refusals (→ `UNCERTAIN` → `BLOCKED` per Requirement 4/VERIFIER-CONTRACT)
    follow the same mode branching — in `observe` mode they are logged, not blocking.
    Turning the default to `enforce` is explicitly **out of scope for this ticket** and
    tracked at #536, informed by the false-block rate measured from this ticket's
    recorded `observe`-mode verdicts.
14. **CI cannot make a model call — tests drive the gate with a stubbed verdict**
    (spec-review feedback): Requirement 11's five semantic cases (policy-Markdown-only,
    scheduler/config/artifact, ordinary structural, docs-neutral/defensible `NO_IMPACT`,
    misleading-but-present) are exercised in CI by writing a pre-canned
    `docs-conformance.md` fixture (a fixed `STATUS:`/`GATE_TYPE:`/`FINDINGS_COUNT:`/
    `SEVERITY:` block, standing in for whatever a real reviewer subagent would have
    produced for that scenario) directly to the artifacts dir and invoking
    `docs_verdict_gate_check.sh` against it — no `Agent`/subagent spawn in CI. These tests
    prove the *plumbing*: verdict parsing, the mode branch (observe-logs-but-proceeds vs.
    enforce-blocks), the DAG gate's wiring into `push-and-pr`'s `depends_on`, and that no
    PR is created on every blocking path (missing file, malformed `STATUS:`, `ERROR`,
    `BLOCKED` in `enforce` mode). They do **not** assert that a real reviewer judges any
    of the five scenarios correctly — that is a semantic-accuracy question with no
    deterministic oracle. Semantic accuracy is measured operationally, from the
    `observe`-mode verdicts this gate records in production once shipped, feeding the
    #536 enforce-mode decision — not from a CI unit test.
15. **Budget scenario registration**: `scripts/context_budget.py`'s `_SECTION_REGISTRY`
    dict (keyed by `--scenario`) has no entry for `docs-conformance` today — it is not
    "already generic" in the sense of needing no change, only generic in its *parsing*
    (`--scenario` accepts any key present in the dict). This ticket adds a
    `"docs-conformance"` key; see Architecture for its section list.
16. **Read-only is enforced by instruction, not by a tool-restriction mechanism**
    (spec-review feedback): the `docs-conformance` DAG node keeps `denied_tools: []`,
    matching every existing phase node's documented #196 posture ("profile removes no
    tools"). "Read-only" for this gate means: the reviewer subagent is granted only
    Glob/Grep/Read (per VERIFIER-CONTRACT's checker-invocation contract) and the command
    session is instructed to make no edits/commits/pushes beyond its verdict file and
    comment (Requirement 2) — there is no DAG/profile-level deny-list backing either
    claim. Adding enforced tool restriction (for this node or any other) is explicitly
    left to #536, alongside breaker accounting and the adapter-driven doc list; this spec
    does not add a tool-allow/deny-list change as a side effect, per `CLAUDE.md`'s hard
    limit that such changes get their own reviewed ticket.

## Architecture / Approach

### New DAG nodes (`workflows/archon-dark-factory.yaml`)

Insert between the existing `conformance-gate` and `push-and-pr` nodes, `new`/`continue`
only:

```
conformance -> conformance-gate -> budget-docs-conformance -> enforce-budget-docs-conformance
  -> docs-conformance -> docs-conformance-gate -> push-and-pr
```

- `budget-docs-conformance` / `enforce-budget-docs-conformance`: bash nodes, identical
  shape to `budget-conformance`/`enforce-budget-conformance` (telemetry via
  `context_budget.py --scenario docs-conformance`, enforcement via
  `budget_gate.sh docs-conformance`), both already generic over `--scenario`.
- `docs-conformance`: `command: dark-factory-docs-conformance`, `denied_tools: []`
  (side_effect_level 5, matching every other phase today per `config/config.yaml`'s
  documented "tightening is a separate change" note — see Requirement 16: this node's
  "read-only" is by instruction, not by deny-list, and stays that way pending #536),
  `depends_on: [enforce-budget-docs-conformance, conformance-gate]`, `when` identical to
  `conformance`'s.
- `docs-conformance-gate`: bash node that reads `docs_conformance.mode` out of
  `.claude/skills/refinement/config.yaml` (same inline-yaml-read idiom
  `enforce-budget-conformance` already uses to read its own config block) and runs
  `scripts/docs_verdict_gate_check.sh "$ARTIFACTS_DIR/docs-conformance.md" "<issue-num>"
  "Docs Conformance (Gate 2.5)" "<mode>"`, `depends_on: [docs-conformance]`.
- `push-and-pr`'s `depends_on` gains `docs-conformance-gate` alongside `conformance-gate`
  — this is unconditional (the gate always runs and always resolves, exit 0 or exit 1;
  only its internal behavior branches on mode per below), so `push-and-pr` does not need
  its own mode-awareness.

### `scripts/docs_verdict_gate_check.sh`

A sibling of `verdict_gate_check.sh`, not a parameterized variant of *it* — the two
scripts keep different accept-lists for a specific, previously-documented reason
(`ERROR` is Gate 3's `fail_open` contract, not a generic "checker had trouble" signal),
and conflating them behind a flag risks a future edit flipping the flag and silently
reintroducing pass-through for a gate that must never have it (see
`.archon/memory/architecture.md`'s #535 entry). The observe/enforce mode added below is
a parameter *within* this one new script, not a reason to fold it back into
`verdict_gate_check.sh` — it governs this gate's own rollout risk, unrelated to Gate 3's
fail-open contract. The new script:

- Takes a 4th positional arg, `<mode>` (`observe`|`enforce`). If omitted, blank, or any
  value other than `enforce`, treat as `observe` — the script's own unparseable-input
  fallback matches config's documented default, so a config-read glitch in the calling
  DAG node can never silently promote this ticket's shipped behavior to blocking.
- Reads the same `STATUS:` line out of `$ARTIFACTS_DIR/docs-conformance.md`.
- `enforce` mode: unchanged fail-closed behavior — `PASS`/`SKIPPED` proceed (exit 0);
  `BLOCKED`, `ERROR`, or missing/unparseable block (exit 1), with the same live
  `needs-discussion` messaging-only check and idempotent `<!-- df-push-gate-failure -->`
  marker comment pattern as `verdict_gate_check.sh`.
- `observe` mode: always exit 0 regardless of `STATUS:`. Before exiting, log to stderr
  what the decision would have been under `enforce` (reusing the same STATUS-to-decision
  mapping so the two modes can't silently drift), and post no `df-push-gate-failure`
  comment and make no `needs-discussion` check or tracker call — `observe` mode must add
  zero human-facing side effects beyond what the command itself already posts (see
  `dark-factory-docs-conformance.md` Phase 6 below).
- Shared logic (STATUS parsing, the `enforce`-mode comment/label mechanics) stays
  factored into `gate_lib.sh` if duplication with `verdict_gate_check.sh` grows past
  these differences — initial implementation may simply copy the ~60-line script, add
  the mode branch, and change the one `case` statement, per plan-phase judgment.

### `commands/dark-factory-docs-conformance.md`

Single-pass structure (no Phase 3.5 reconcile, unlike `dark-factory-conformance.md`):

1. **LOAD**: read `.claude/skills/refinement/config.yaml`'s `docs_conformance` block. If
   `docs_conformance.enabled: false` → write `STATUS: SKIPPED` and exit (mirrors Gate 2's
   kill-switch). Note `docs_conformance.mode` (`observe`|`enforce`, default `observe`) —
   this command does not branch its own behavior on `mode` (it always builds the diff,
   spawns the reviewer, and writes a real verdict); `mode` only governs the *consequence*
   of that verdict, split between Phase 6 below (the comment/label side effect) and
   `docs_verdict_gate_check.sh` (the `push-and-pr` gating decision). Read
   `/opt/refinement-skills/VERIFIER-CONTRACT.md` for the model pin. Read
   `$ARTIFACTS_DIR/doc-impact.md` (the Phase 4 DOCUMENT handoff) — missing/empty is not
   an immediate abort here; it's passed to the reviewer as part of the evidence to audit,
   and an empty/missing file is itself a finding the reviewer can cite.
2. **BUILD DIFF**: `git diff main...HEAD -- ':!*.lock' ':!evals/*.md' ':!bench/*.md'
   ':!.archon/memory/**'` — i.e. Gate 2/3's exclusion list minus `':!docs/*.md'`. Also
   collect the baseline (`git show main:<path>`) and branch versions of every doc file
   the diff touches, so the reviewer can see before/after doc text directly rather than
   only a unified diff hunk.
3. **RENDER + SPAWN**: render a new `docs-conformance-reviewer-prompt.md` (baked under
   `/opt/refinement-skills/`, clone-live-first override under
   `.claude/skills/docs-conformance/RUBRIC.md` per the VERIFIER-CONTRACT clone-live-first
   pattern) with the diff, the doc before/after pairs, and `doc-impact.md`'s content.
   Spawn one subagent: `model: opus` (→ `claude-opus-5-5` per VERIFIER-CONTRACT), Glob/
   Grep/Read only, asking it to judge (a) is every doc claim about the change true of the
   actual diff, (b) is any affected behavior left undocumented, (c) if `doc-impact.md`
   claims `NO_IMPACT`, is that defensible given the independent view the reviewer formed
   in step (a)/(b). No Fable shadow (VERIFIER-CONTRACT only pairs shadows with the two
   conformance-reviewer pairs; this is a third, structurally simpler pair).
4. **PARSE VERDICT**: `✅ Conforms`/`⚠️ Minor deviations` → PASS; `⛔ Material
   divergence` → BLOCKED; unparseable/refusal/timeout/tool-error → `UNCERTAIN` → BLOCKED
   (never PASS, per VERIFIER-CONTRACT's refusal rule — and unlike Gate 2/3, there is no
   reconcile cycle to consume first, so this maps directly to BLOCKED on this one pass).
5. **WRITE VERDICT**: `scripts/gate_lib.sh::emit_verdict "docs-conformance" <STATUS>
   <count> <severity>` into `$ARTIFACTS_DIR/docs-conformance.md`, followed by the raw
   reviewer dialogue (same shape as `conformance.md`/`review.md`).
6. **ON BLOCKED**, branch on `docs_conformance.mode` (Requirement 13):
   - `enforce`: post a `<!-- df-docs-conformance-blocked -->`-marked comment listing each
     mismatch (doc file+section, the false/missing claim, the contradicting diff hunk, a
     suggested correction), move the board to the state Gate 2's BLOCKED path uses
     (whatever `conformance`'s Phase 5 does today — reuse verbatim, not a new taxonomy),
     and add `needs-discussion` — per Requirement 10/12, no new label or status.
   - `observe`: post the same mismatch content under a distinct
     `<!-- df-docs-conformance-observed -->` marker, explicitly labelled informational
     (e.g. "would have blocked in enforce mode — see #536") in the comment body, and do
     **not** add `needs-discussion` or touch the board. This is the load-bearing
     difference that makes `observe` mode actually non-blocking end-to-end, not just at
     the gate-check script.
7. Exit (0 on PASS/SKIPPED path for forward-compat with the DAG node that actually gates;
   non-zero on BLOCKED, same forward-compat posture `dark-factory-conformance.md` Phase 5
   documents for its own `exit 1`) — this exit code is cosmetic forward-compat only in
   both modes; it is `docs_verdict_gate_check.sh`, not this command's own exit code, that
   actually decides whether `push-and-pr` proceeds.

### `commands/dark-factory-implement.md` Phase 4 changes

- Extend the path-classification step to also derive impact from **diff content**, not
  only changed paths: for files matched by the existing mapping table, Phase 4 behaves as
  today; additionally, Phase 4 must scan the diff for behavior changes with no mapping
  row (config semantics, a gate's accept-list, scheduler timing, a new/changed env var,
  policy Markdown under `commands/**`) and either update the relevant doc or record a
  reasoned no-impact rationale.
- Write `$ARTIFACTS_DIR/doc-impact.md`: one entry per changed behavior
  (`file -> doc updated at <doc>#<section>` or `file -> NO_IMPACT: <reason>`), plus an
  overall `NO_IMPACT: <reason>` line if literally nothing in the diff needed any doc
  change. Add a one-line pointer to it in `implementation.md`.
- Preserve the existing auto-generated-docs boundary (`docs/database-schema.md` etc. —
  never edit).

### Config (`.claude/skills/refinement/config.yaml`)

```yaml
docs_conformance:
  enabled: true
  mode: observe   # observe|enforce — enforce turns on fail-closed blocking (Requirement 13).
                   # Default observe ships with this ticket; flipping to enforce is a separate,
                   # human-reviewed change tracked in #536, informed by observe-mode verdict data.
```

No `max_reconcile_cycles`-equivalent (single-pass) and no separate `block_on_material`
knob — `mode` alone governs the consequence of a non-PASS verdict (Requirement 13); a
second knob layered on top of `mode` would just be two ways to ask the same question.

`token_optimization.budgets`/`.enforce` gain a `docs-conformance` scenario entry
(hyphenated, matching the existing `code-review` scenario naming, not the config
section's own `docs_conformance` underscored key — the budget scenario string and the
config section key are already inconsistent for `code_review`/`code-review` today, so
this follows existing precedent rather than inventing new consistency).

### Budget scenario registration (Requirement 15)

`scripts/context_budget.py`'s `_SECTION_REGISTRY` dict needs a new entry:

```python
"docs-conformance": ["skill_prompts", "diff", "doc_impact_md"],
```

- `skill_prompts` / `diff`: the same section types `"conformance"`/`"code-review"`
  already register — the rendered reviewer-rubric prompt and the (widened, `docs/*.md`
  included) ranked diff.
- `doc_impact_md`: a new section type, mirroring `"conformance"`'s existing
  `implementation_md` entry — a full, unsliced read of `$ARTIFACTS_DIR/doc-impact.md`
  for measured-only telemetry (not delivered as an optimized/truncated section, same as
  `implementation_md` today), since `doc-impact.md` is a new artifact type with no
  existing registry row to reuse. The before/after doc-file text pairs (Architecture,
  command step 2) are small, bounded by the diff's own doc-file set, and not separately
  budgeted — consistent with how `conformance` doesn't separately budget the baseline
  files its diff touches either.

### CI test strategy for the five semantic scenarios (Requirement 14)

CI has no model access, so the five scenario tests in Requirement 11 do not spawn a real
reviewer subagent. Each test instead:

1. Sets up the DAG/fixture state for the scenario (a sample diff is not even required,
   since the command itself isn't under test — only the gate's downstream plumbing is).
2. Writes a fixed `docs-conformance.md` fixture — one per scenario's intended real-world
   verdict (e.g. `STATUS: PASS` for the docs-neutral/defensible-`NO_IMPACT` case,
   `STATUS: BLOCKED` for the misleading-but-present case) — standing in for whatever a
   real reviewer would have produced.
3. Invokes `docs_verdict_gate_check.sh` directly against that fixture, once with
   `mode=observe` and once with `mode=enforce`, asserting: `observe` always exits 0
   (regardless of fixture `STATUS:`) and never posts a blocking comment or label;
   `enforce` exits 0 only for `PASS`/`SKIPPED` fixtures and exits 1 (no PR created) for
   `BLOCKED`/`ERROR`/missing/malformed fixtures.
4. Also covers the gate's own fail-closed paths (missing file, malformed `STATUS:` line)
   under both modes, per Requirement 11's fail-closed bullet.

This proves the plumbing — parsing, mode branching, the `push-and-pr` dependency, and
"no PR created" on every blocking path — not that a real reviewer's semantic judgment is
correct for any of the five scenarios. There is no deterministic oracle for "is this doc
claim true of this diff," so semantic accuracy is not a CI assertion; it is measured
operationally from the `observe`-mode verdicts this gate records in production once
shipped, and that data is what informs the #536 enforce-mode decision.

### Interaction with Gate 2's OOS doc exemption

No change needed to `dark-factory-conformance.md` Phase 3.6.0/3.6.2 — they already treat
every doc-file OOS finding as in-scope housekeeping. This spec adds a gate that judges
*accuracy* downstream of that exemption; the two mechanisms don't overlap (one asks "is
this doc change allowed to exist," the other asks "is this doc change correct").

## Alternatives Considered

1. **Widen Gate 3's diff to include `docs/*.md` and add a docs dimension to its existing
   reviewer rubric**, instead of a new gate. Rejected: Gate 3 runs after the PR is
   already published (`push-and-pr` → `code-review`), so by the time it could flag a
   docs mismatch the branch is already public and mergeable-looking; the issue explicitly
   requires blocking *before* `push-and-pr`. It would also mean a single reviewer session
   is responsible for both code-quality judgment and docs-accuracy judgment, diluting
   both, and `review.md`'s `code_review.fail_open: true` advisory-on-error contract is
   wrong for a gate meant to fail closed.
2. **Deterministic-only checker** (lint/link-check/changed-path-matching, no LLM judge).
   Rejected: the issue explicitly asks whether doc *claims* are true of the actual
   behavior change — a semantic judgment no deterministic check (no existing link/lint
   tooling exists in this repo today, confirmed by `scripts/` having none) can make. Kept
   as a non-blocking supplement (see Open Questions).
3. **Fold the doc check into Gate 2 as an added phase**, sharing its reconcile loop and
   verdict file. Rejected per Q&A 1/2: breaks the "independent, read-only" requirement
   (Gate 2's session edits code) and the "one verdict per file" assumption three existing
   call sites already make.
4. **Give the new gate its own reconcile loop** (agent edits docs, re-checks, up to N
   cycles) like Gate 2's code-fix loop. Rejected per Q&A 2: an in-run doc edit would land
   after `conformance-gate` already passed, bypassing Gate 2 for exactly the kind of
   behavior-defining policy-Markdown change the issue puts in scope; and a checker that
   edits what it then re-grades is the self-attestation the issue explicitly rejects.
5. **Renumber Gate 3 → Gate 4** so the new gate is cleanly "Gate 3." Rejected per Q&A 3b:
   churns many existing literal "Gate 3" strings (DAG comments, test assertions, docs,
   `commands/dark-factory-code-review.md`) for no behavioral benefit; "Gate 2.5" is
   accurate (it runs between Gate 2 and Gate 3) and minimizes blast radius.
6. **Ship directly in `enforce` mode** (no `observe`/`enforce` knob at all). Rejected per
   spec-review feedback (Requirement 13): this checker's false-block rate is unmeasured,
   and under the scheduler's WIP-limited, single-factory-instance operation, every false
   block costs a real human touch (a `needs-discussion` label, a Blocked board move, and
   a full re-run of implement/Gate-2/Gate-2.5 via `Continue`) — a cost the issue itself
   does not ask this ticket to impose immediately. Shipping `observe`-by-default still
   exercises the entire pipeline end-to-end and records real verdicts; `enforce` becomes
   a one-line config flip (#536) once that data justifies it, rather than a second
   implementation pass through this gate's code.
7. **Make `enforce` the default, with a `--dry-run`-style one-off override** instead of a
   persistent `mode` config key. Rejected: a one-off override has no durable record of
   which mode a given historical run executed under, which matters for interpreting
   whether a recorded `BLOCKED` verdict from this ticket's early runs actually blocked
   anything — a persistent, low-cardinality config key is simpler to reason about and is
   already the pattern every other per-gate knob in this file follows (`conformance.*`,
   `code_review.*`).

## Open Questions (non-blocking)

- Whether to eventually add deterministic supplementary checks (markdown link-check,
  anchor-reference validation) alongside the semantic reviewer — no such tooling exists
  in this repo today; the issue frames these as a supplement, never a substitute, so this
  can land as a later, separate enhancement without blocking this gate's core semantic
  check.
- Whether `docs_verdict_gate_check.sh`'s duplication with `verdict_gate_check.sh` (both
  ~60-line scripts differing only in their accept-list and messaging strings) should be
  refactored into one script with an explicit `--no-error-passthrough` style flag once a
  third consumer appears — deferred rather than decided now, to avoid the flag itself
  becoming a future accidental-pass-through vector for a two-consumer refactor nobody
  asked for yet.
- Exact current line numbers for every doc edit site cited in this spec's Architecture
  section (and in Q&A 3b) should be re-derived from `main` at plan/implement time rather
  than trusted verbatim — a known recurring failure mode for long-lived reference docs
  (prior lesson from issue #182's re-refinement, `.archon/memory/architecture.md`).
- When/whether to flip `docs_conformance.mode` to `enforce` is explicitly out of scope
  for this ticket and tracked at #536 — the decision depends on the false-block rate
  observed from this gate's own `observe`-mode production verdicts, data that does not
  exist until this ticket ships. Also at #536: breaker accounting for this gate and
  deciding whether to add enforced tool-restriction for this (or any) phase node
  (Requirement 16). #537 (deterministic reference/link checks as a CI supplement) and
  the adapter-driven doc list are tracked separately under epic #538; none of the three
  are blocking prerequisites for this ticket.

## Assumptions (flagged)

- The new gate's reviewer subagent, like Gate 2/3's, is pinned to `claude-opus-5-5` via
  the Agent tool's `opus` alias, per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s
  existing checker-invocation contract, which this spec extends with a new row rather
  than inventing a separate pinning mechanism.
- `scripts/gate_lib.sh::emit_verdict` and `scripts/factory_core/verdict.py`'s
  STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY schema are reused unchanged — this spec adds a
  new *gate-check* script (acceptance criterion), not a new verdict schema.
- `doc-impact.md`'s exact field format is left to plan/implement to finalize against
  `implementation.md`'s existing conventions; this spec fixes its *contract* (per-change
  doc-or-no-impact entries, audited not trusted) but not its literal grammar.
- `docs_conformance.mode: observe` is this ticket's shipped default and is not itself a
  gate weakening of an *existing* gate (CLAUDE.md's hard limit) — it is the initial
  calibration setting of a brand-new gate that does not exist on `main` yet. The
  distinction matters because `enforce` is still fully implemented and tested in this
  same ticket (Requirements 2/3/13); only the *default config value* ships non-blocking,
  pending the #536 decision informed by this ticket's own observe-mode data.
- This ticket's own branch necessarily touches gate-adjacent surfaces
  (`workflows/archon-dark-factory.yaml`, a new `scripts/docs_verdict_gate_check.sh`,
  `config/config.yaml`-equivalent `.claude/skills/refinement/config.yaml`, and
  `VERIFIER-CONTRACT.md`). Per `CLAUDE.md`'s hard limits, this is acceptable because the
  change is going through the standard human-reviewed `spec-pending-review` → plan →
  implement → conformance → code-review pipeline (the "own reviewed ticket" CLAUDE.md
  requires for gate changes), not arriving via an unreviewed comment-channel shortcut —
  but the human spec reviewer should scrutinize this list specifically before approving.

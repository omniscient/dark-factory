# Prompt Cache-Shape Contract: Stable Prefix / Volatile Tail for Factory Phase Commands

**Issue:** #236
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#234 (harness economics; arXiv 2607.06906v1)
**Size:** M — one implementation run: one lint script, one baseline file, one pytest module,
one CI line, one doc page. **No `commands/*.md` file changes in this ticket.**

---

## Overview / Problem Statement

The issue asks for a two-zone prompt contract (`stable_prefix` / `volatile_tail`) for factory
phases, a check that catches volatile fields leaking into the stable prefix, alignment with the
skills work, and rollback guidance.

What is verifiable in this repo and in the pinned Archon source:

- A phase command is a `command:` node in `workflows/archon-dark-factory.yaml` (e.g. `plan`,
  lines 471-472). Archon reads `commands/<name>.md` verbatim (frontmatter included) and runs
  `substituteWorkflowVariables()` over the whole text before sending it as the first user
  message. Source: `packages/workflows/src/executor-shared.ts:363` at Archon commit
  `74372446`, the commit `Dockerfile:123` pins.
- That function replaces every **bare** occurrence of this vocabulary (no brace form):

  ```
  $WORKFLOW_ID  $ARTIFACTS_DIR  $BASE_BRANCH  $DOCS_DIR
  $USER_MESSAGE  $ARGUMENTS  $LOOP_USER_INPUT  $REJECTION_REASON  $LOOP_PREV_OUTPUT
  $CONTEXT  $EXTERNAL_CONTEXT  $ISSUE_CONTEXT
  ```

  The first nine have no word-boundary guard (`executor-shared.ts:388-396`). The three context
  names carry a negative lookahead `(?![A-Za-z0-9_])` (`executor-shared.ts:341`), so
  `$CONTEXTS` is not substituted while `$ARTIFACTS_DIRX` is.
- A second pass, `substituteNodeOutputRefs()` (`dag-executor.ts:290-296`, called at line 677),
  replaces `$<node-id>.output` and `$<node-id>.output.<field>` in the same text. No command
  file uses that form at origin/main; the lint treats it as volatile.
- `archon workflow run` passes no issue context (`packages/cli/src/commands/workflow.ts:710`),
  so the three context names are replaced with the empty string and nothing is appended to the
  command text (`buildPromptWithContext`, `executor-shared.ts:434-458`).
- At origin/main the 8 phase commands contain 237 bare vocabulary tokens: 228 `$ARTIFACTS_DIR`
  (17–43 per file), 8 `$WORKFLOW_ID` (one per file) and 1 `$ISSUE_CONTEXT`
  (`commands/dark-factory-refine.md:122`). That last one is prose naming a persona-template
  slot, not an intended Archon variable; Archon blanks it, so the agent reads an empty pair of
  backticks there. It is an existing defect, recorded here and **not** fixed by this ticket
  (no command file changes); the lint's `--json` output makes it visible.
  `commands/ceiling-revisit.md` has 1 `$ARTIFACTS_DIR` (line 23); no workflow in this repo
  loads that file as a `command:` node, but it sits in `commands/` and is linted with the rest.
- The **first** bare token is on line 3 of seven of the eight files (the frontmatter
  `argument-hint:` line) and on line 8 of `dark-factory-revise-advisory.md`
  (`**Workflow ID**: $WORKFLOW_ID`). `$ARTIFACTS_DIR` resolves to a per-workflow-run path, so
  each rendered command differs between runs from roughly its third line onward.

What is **not** verifiable from this repo, and is therefore an assumption (see Assumptions):
that this costs anything material. Provider prompt caching is prefix-based — a request reuses
cached content only up to the first byte that differs from a previously cached request, and
only within the cache lifetime. Two consequences the contract must respect:

1. Only the position of the **first** volatile byte matters. The count of later occurrences
   (the "229 sites") is irrelevant to cache reuse; removing 228 of them while leaving the first
   changes nothing.
2. Within one run the rendered command is constant, so turn-to-turn cache reads inside a node
   are unaffected. The only thing at stake is whether a node's first request can reuse the
   command text cached by an *earlier run of the same phase* — which also requires everything
   before the command text (tool definitions, system prompt, project `CLAUDE.md`) to be
   byte-identical and the earlier run to be recent enough. None of that is established here.

So this ticket defines the contract, makes it measurable and deterministic, and puts a guard on
the one "fix" that looks free but is not (next section). It does not rewrite prompts.

### The brace-form swap is NOT behaviour-neutral — do not do it

An earlier draft proposed swapping `$ARTIFACTS_DIR` → `${ARTIFACTS_DIR}` inside bash fences on
the grounds that `ARTIFACTS_DIR` is exported at `entrypoint.sh:179-180`. That is wrong:

- `entrypoint.sh:179` exports `.../artifacts/runs/${RUN_ID}` where `RUN_ID` is the
  entrypoint's own uuid (`entrypoint.sh:173`).
- Archon substitutes a **different** directory, `runs/<workflow run id>`. This is recorded at
  `entrypoint.sh:1051-1052` ("Archon gives every bash node its own $ARTIFACTS_DIR (the workflow
  run's directory, not this one)") and `scripts/factory_core/run_record.py:1123-1127` ("a
  sibling of the entrypoint's runs/<RUN_ID>").
- Archon sets the `ARTIFACTS_DIR` environment variable only for bash-node subprocesses
  (`dag-executor.ts:1324` at the pinned commit). A command node's agent inherits the
  container environment, i.e. the entrypoint's value.

The agent subprocess environment is a copy of Archon's own `process.env`
(`packages/providers/src/claude/provider.ts:98`), which is what `entrypoint.sh` exported.

A brace-form token in a command file would therefore reach the model unsubstituted and, when
run in the agent's shell, resolve to the entrypoint's directory — not the one where the
workflow's bash nodes wrote `issue.json`, `context-budget.*.json`, etc. Every phase would read
and write the wrong directory. Prose and Read/Write-tool uses would get a literal placeholder.
The lint below forbids the brace form outright.

---

## Requirements

1. Define `stable_prefix` / `volatile_tail` for a phase command in terms that match prefix
   caching and can be computed from the committed file.
2. Ship a deterministic lint with tests, wired into CI, that (a) forbids the unsafe brace form
   and (b) stops the stable prefix of any command from shrinking.
3. Emit prompt-shape metadata per phase command (prefix hash, volatile-tail size,
   cache-busting fields) from that same lint — statically, with no DAG or budget-pipeline change.
4. State how to measure cache effect with the fields #645 already captures; add no new
   instrumentation.
5. Classify the skills / reviewer-prompt surfaces against the contract.
6. Rollback guidance.

Non-goals: editing any `commands/*.md`; touching `scripts/context_budget.py`,
`scripts/budget_gate.sh`, `budget_enforce.py`, any `gate_*`, breaker, `config/config.yaml`
budgets or `token_optimization`; model or routing changes; `deploy/**`;
`.github/workflows/publish.yml`; tool allow/deny lists; `.claude/skills/**`.

---

## Architecture / Approach

### 1. The contract

For each `commands/*.md` file, as committed:

- **`stable_prefix`** — the bytes from the start of the file up to (not including) the first
  bare vocabulary token or node-output reference.
- **`volatile_tail`** — everything from that token to the end of the file.
- **`cache_busting_fields`** — the vocabulary names that occur bare in the file, each with its
  occurrence count and first line number.
- **Forbidden anywhere in a command file:** the brace form `${NAME` of any vocabulary name
  (see above).

The issue's YAML sketch (factory identity, phase contract, output schema, tool/safety policy in
the prefix; issue number, comments, diff, run id, time in the tail) is the *target shape*. This
ticket does not move any text to reach it; it records where each file stands today and
prevents regression.

### 2. Lint: `scripts/check_command_cache_shape.py`

Stdlib only (the CI `dag-check` job installs only `pyyaml`). Same shape as
`scripts/check_workflow_when.py`: `check(commands_dir) -> list[str]` (empty list = pass,
otherwise human-readable violations), `main(argv) -> int`.

Token match mirrors Archon exactly: `\$(?:NAME1|…)` with no trailing word boundary for the
nine plain names; `\$(?:CONTEXT|EXTERNAL_CONTEXT|ISSUE_CONTEXT)(?![A-Za-z0-9_])` for the three
context names; and `\$[a-zA-Z_][a-zA-Z0-9_-]*\.output` for node-output references (reported
under the field name `NODE_OUTPUT`). The patterns are module constants with a comment naming
the Archon files and pinned commit.

Scope: `commands/*.md` only. The three inline `prompt:` nodes in
`workflows/archon-dark-factory.yaml` go through the same substitution but are not linted here.
R1 must **not** be extended to the workflow YAML: its bash nodes legitimately use
`${ARTIFACTS_DIR:-…}` (7 occurrences at origin/main), where Archon does set the variable.

Rules, applied to every `commands/*.md`:

- **R1 (no brace form).** Any `${NAME` where `NAME` is in the vocabulary is a violation.
  Zero occurrences exist at origin/main, so this passes today.
- **R2 (prefix ratchet).** `first_volatile_line` (1-based line of the first bare token; a file
  with no bare token counts as its line count + 1) must be `>=` the value recorded for that
  file in `scripts/command_cache_shape_baseline.json`. The baseline is committed with today's
  values: `3` for code-review, conformance, docs-conformance, implement, plan, refine and
  validate; `8` for revise-advisory; `23` for ceiling-revisit.
- **R3 (baseline complete).** A `commands/*.md` file with no baseline entry, or a baseline
  entry with no file, is a violation.

R2 is deliberately a ratchet, not a threshold: today's prefixes are 2–7 lines, and raising
them means changing prompts, which needs its own evidence and review. A later ticket that
improves a file raises its baseline number in the same commit.

What each rule is worth, stated plainly:

- R1 guards a real failure: it blocks a change that would silently point every phase at the
  wrong artifacts directory. This is the part of the lint with value today.
- R2 at today's baseline protects almost nothing. A floor of line 3 can only be violated by
  putting a token in the first two frontmatter lines. The baseline is a snapshot of the current
  state, not a cache guarantee; R2 starts to mean something only after a later ticket raises a
  file's number. It ships because it is the mechanism that later ticket needs, and because the
  issue asks for a leak check.
- R3 costs something: a change that adds or renames a `commands/*.md` file must update the
  baseline in the same commit or `dag-check` fails. The violation message must say exactly
  that, including the line to add.

`--json` prints, per file, the prompt-shape metadata (acceptance criterion 1):

```json
{
  "command": "dark-factory-plan",
  "prefix_hash": "<sha256 hex of the stable_prefix bytes>",
  "stable_prefix_bytes": 0,
  "volatile_tail_bytes": 0,
  "volatile_tail_tokens_est": 0,
  "first_volatile_line": 3,
  "cache_busting_fields": [{"name": "ARTIFACTS_DIR", "count": 25, "first_line": 3}]
}
```

`volatile_tail_tokens_est` is `volatile_tail_bytes // 4` and is labelled an estimate. The
metadata is static (a function of the committed file), so it is identical for every run of a
given commit and needs no per-run artifact.

### 3. CI wiring

One line added to the `dag-check` job in `.github/workflows/ci.yml`, after the existing
`check_workflow_when.py` line (line 68):

```
      - run: python scripts/check_command_cache_shape.py commands
```

A change under `.github/workflows/` puts the PR on the human blast-radius hold. That is
expected for this ticket; the PR body must say the hold is caused by this one line. No gate,
breaker, budget, config or tool allow/deny file is touched.

### 4. Measurement with existing data (no new instrumentation)

`run-record.json` already carries, per node, `gen_ai.usage.cache_read_input_tokens` and
`gen_ai.usage.cache_creation_input_tokens` (#645, `run_record.py:_apply_session_usage`,
line 670). `harness_economics.ledger_mechanics.cache_hit_ratio`
(`run_record.py:_compute_ledger_mechanics`, lines 961-965) must **not** be used: it is computed
from model-proxy ledger rows whose cache columns are hardcoded to zero
(`scripts/factory_core/model_proxy.py:400`).

`docs/prompt-cache-shape.md` documents this procedure:

- Upper bound on what a perfect prefix could save for one command node per run:
  `volatile_tail_tokens_est` (from `--json`) × (cache-write price − cache-read price).
- Compare it with that node's `cache_creation_input_tokens` in recent run records (join on run
  id **and** node id; a run-level total mixes phases).
- Limitation, stated in the doc: per-node figures sum every turn of the node, so they cannot
  isolate a cross-run prefix hit on the first request. They bound the possible gain; they do
  not prove a realised one.

A follow-up that restructures prompts is only worth filing if that bound is a meaningful share
of the node's cache-creation tokens. That decision is the operator's, made from the numbers;
this ticket files no follow-up automatically.

### 5. Skills alignment (criterion 3)

- `refinement-skills/` persona templates are rendered by
  `scripts/factory_core/prompt_render.py` (#400), which never substitutes inside single-line
  backtick spans and fails on any slot/value mismatch. They are filled by a phase agent
  mid-run, not by Archon, so they are never part of a command node's first-request prefix. Out
  of the lint's scope.
- Reviewer rubrics (`.claude/skills/{conformance,code-review}/RUBRIC.md`) are read from disk
  during a run; no template engine touches them. Out of scope.
- No role-card schema exists to align with (#42 deferred it); nothing here revives it.
- If either surface is later loaded by Archon as a `command:` node, it falls under R1–R3
  automatically because the lint globs `commands/*.md`.

### 6. Rollback (criterion 4)

This ticket changes no prompt text, so it cannot change phase behaviour. If the lint is noisy:
delete its one line from `.github/workflows/ci.yml`, or revert the commit. No runtime switch,
no config key, nothing to coordinate with a running scheduler.

---

## Files

| File | Change |
|---|---|
| `scripts/check_command_cache_shape.py` | new |
| `scripts/command_cache_shape_baseline.json` | new |
| `tests/test_check_command_cache_shape.py` | new |
| `.github/workflows/ci.yml` | one line in `dag-check` |
| `docs/prompt-cache-shape.md` | new — contract, rules, measurement procedure, rollback; no `file:line` citations |

No edits to `docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` or
`docs/configuration.md` (the files `scripts/docs_refs_check.py` range-checks).

---

## Acceptance Criteria

1. `python scripts/check_command_cache_shape.py commands` exits 0 at the implementation head.
2. `git diff origin/main --stat -- commands/` is empty.
3. `tests/test_check_command_cache_shape.py` (pytest, `tmp_path` fixtures) covers:
   - R1: a file containing `${ARTIFACTS_DIR}` fails; `${ARTIFACTS_DIR:-x}` fails; `${OTHER}`
     passes.
   - R2: moving the first bare token to an earlier line than the baseline fails; equal or later
     passes; a file with no bare token passes.
   - R3: missing baseline entry fails; orphan baseline entry fails.
   - R3 message names the baseline file and the entry to add.
   - Matching: `$ARTIFACTS_DIRX` counts as a token (no word boundary); `$CONTEXTS` and
     `$ISSUE_CONTEXT_X` do not (lookahead); `$ISSUE_CONTEXT` is reported as `ISSUE_CONTEXT`,
     not `CONTEXT`; `$plan.output` and `$plan.output.field` count as `NODE_OUTPUT`.
   - `--json`: `prefix_hash` equals `sha256` of the bytes before the first token; changing a
     byte after the first token leaves `prefix_hash` unchanged; changing a byte before it
     changes it; `cache_busting_fields` counts are correct.
   - Real tree: `check("commands")` returns `[]`, and the baseline matches the committed files.
4. `.github/workflows/ci.yml` runs the lint in `dag-check`.
5. `python -m pytest tests/ -v`, both workflow DAG checks and `scripts/docs_refs_check.py`
   still pass.
6. `docs/prompt-cache-shape.md` exists and contains the brace-form warning, the measurement
   procedure and its limitation, and the rollback step.

---

## Issue criteria: met and deferred

| Issue criterion | Status in this ticket |
|---|---|
| Prompt-shape metadata per phase (prefix hash, volatile-tail tokens, cache-busting fields) | Met statically via `--json`. Tail tokens are a bytes/4 estimate. Not emitted per run. |
| Tests or linting that catch volatile fields leaking into the stable prefix | Met in mechanism (R2), with a floor that is near zero today. Date/time, random ids, file listings and log snippets from the issue's forbidden list are not detected; only Archon-substituted tokens are. |
| Align Skills / role-card structure with the split | Classified only (§5). No skill or role-card file changes; no role-card schema exists. |
| Rollback guidance | Met (§6). |
| Moving prompt text to reach the issue's target shape | Deferred, with no follow-up filed, until §4 shows a saving is possible. |

---

## Alternatives Considered

1. **Bare → brace swap across the command files.** Rejected: resolves to a different directory
   in command nodes (see above). It would become safe only if Archon exported the same
   `ARTIFACTS_DIR` to command-node agents — an `omniscient/Archon` change, outside this repo.
2. **A "Run Context" footer holding the resolved values, with the body reworded to refer to
   it.** Rejected for this ticket: it rewrites all eight phase prompts (including the headless
   and safety text around the artifact paths) with no measured benefit yet.
3. **Add a `prompt_shape` block to `context-budget.<scenario>.json`.** Rejected: the metadata
   is static, so a per-run artifact adds nothing; and `context_budget.py` feeds
   `budget_gate.sh` / `budget_enforce.py`, a budget surface that should not change as a side
   effect of this ticket.
4. **Design document only, lint deferred.** Rejected: the issue asks for tests or linting, and
   the lint is small.

---

## Assumptions

- **[Flagged — not verifiable from this repo]** Prefix-based caching semantics, cache lifetime,
  and whether the harness places a cache breakpoint that covers the command text are provider /
  Claude Code behaviour. Nothing here asserts a saving; §4 is how to bound one.
- **[Flagged]** Whether the content ahead of the command text (tool definitions, system prompt,
  `CLAUDE.md`) is itself identical across runs is unknown. If it is not, no command-file
  reshaping can help. What the Archon source does show: the command text is sent as the
  prompt of a new session (`dag-executor.ts:677`, `sendQuery`), under the `claude_code` system
  prompt preset with `settingSources: ['project']` (`packages/providers/src/claude/provider.ts:624-625`).
  The preset's content is not in either repo. If it carries per-run values (working directory,
  date, branch or commit state), a cross-run hit on the command text is impossible whatever
  shape the command file has. This must be settled by measurement (§4) before any ticket
  restructures prompts.
- **[Flagged]** A cross-run hit also needs a second run of the same phase on the same model
  inside the cache lifetime. How often that happens in this factory is not measured here.
- **[Flagged]** Archon facts were read at commit `74372446` (`Dockerfile:123`). If the pin
  moves, re-check the vocabulary constant and R1's rationale.
- The factory token can push a change to `.github/workflows/ci.yml` (earlier CI-check tickets
  did). If the push is refused, land the script, baseline, tests and doc, and record the CI
  line as the one remaining manual step in the PR body.

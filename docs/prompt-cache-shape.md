# Prompt cache shape for phase commands

Issue #236. Design spec: `2026-11-28-prompt-cache-shape-contract-design.md`, under
`docs/superpowers/specs/` until the ticket's PR archives it to `docs/archive/`.
Enforced by `scripts/check_command_cache_shape.py`, run in CI's `dag-check` job.

## Why this exists

Each phase of a factory run (refine, plan, implement, validate, conformance,
docs-conformance, code-review, revise-advisory) is an Archon `command:` node. Archon reads
`commands/<name>.md` verbatim, frontmatter included. It replaces a fixed vocabulary of
`$NAME` tokens across the whole text and sends the result as the node's first user message.
Those values are per run, so the rendered command differs between runs from its first token
onward.

Provider prompt caching is prefix-based. A request reuses cached content only up to the first
byte that differs from an earlier cached request, and only within the cache lifetime. So:

1. Only the position of the **first** volatile token matters. Removing later occurrences
   while leaving the first changes nothing.
2. Within one run the rendered command is constant, so cache reads between turns inside a
   node are unaffected. What is at stake is whether a node's first request can reuse command
   text cached by an earlier run of the same phase. That also needs everything ahead of the
   command text (tool definitions, the harness system prompt, `CLAUDE.md`) to be
   byte-identical across runs. This repo does not establish that.

Nothing here claims a saving. The lint makes the shape measurable and blocks one unsafe
"fix".

## Substituted vocabulary

Archon substitutes these names, bare only, never in brace form:

```
$WORKFLOW_ID  $ARTIFACTS_DIR  $BASE_BRANCH  $DOCS_DIR
$USER_MESSAGE  $ARGUMENTS  $LOOP_USER_INPUT  $REJECTION_REASON  $LOOP_PREV_OUTPUT
$CONTEXT  $EXTERNAL_CONTEXT  $ISSUE_CONTEXT
```

The first nine have no word-boundary guard, so `$ARTIFACTS_DIRX` is substituted. The three
context names do have one, so `$CONTEXTS` is not. `archon workflow run` passes no issue
context, so the context names become the empty string. Archon also replaces
`$<node-id>.output` and `$<node-id>.output.<field>`. The lint treats those as volatile and
reports them as `NODE_OUTPUT`.

The list mirrors the Archon source at the commit `Dockerfile` pins (`74372446`). If that pin
moves, re-check the constants at the top of the lint script.

## The contract

For each `commands/*.md`, as committed:

- **stable_prefix** — the bytes from the start of the file up to, but not including, the
  first bare vocabulary token or node-output reference.
- **volatile_tail** — everything from that token to the end of the file.
- **cache_busting_fields** — the vocabulary names that occur bare in the file, with
  occurrence count and first line.
- **Forbidden anywhere in a command file:** the brace form `${NAME` of a vocabulary name.

The target shape puts the following in the prefix: factory identity, phase contract, output
schema, tool and safety policy, adapter summary, and skill/role names. It puts the following
in the tail: issue number, comments, diff, run id, command output and time. Today's phase
commands are far from that: their first token is on line 3 (line 8 for revise-advisory, and
line 23 for `ceiling-revisit.md`, which is not loaded as a node). No text has been moved
yet. The lint records where each file stands and stops regression.

## Do not use the brace form

Rewriting `$ARTIFACTS_DIR` as `${ARTIFACTS_DIR}` looks harmless but breaks every phase:

- Archon substitutes only the bare form, so the brace form reaches the model unsubstituted.
- In prose, or in a Read/Write tool path, the agent gets a literal placeholder.
- In the agent's shell, `${ARTIFACTS_DIR}` resolves from the container environment. That
  holds the entrypoint's `runs/<RUN_ID>` directory, not the workflow run's directory, where
  the bash nodes wrote `issue.json`, `context-budget.*.json` and the rest. Archon sets
  `ARTIFACTS_DIR` only for bash-node subprocesses.

The phase would read and write the wrong directory. Rule R1 blocks this. It would become safe
only if Archon exported the same `ARTIFACTS_DIR` to command-node agents, which is an Archon
change.

This applies to `commands/*.md` only. Bash nodes in `workflows/archon-dark-factory.yaml`
legitimately use `${ARTIFACTS_DIR:-…}`, because Archon does set the variable there.

## The lint

```bash
python scripts/check_command_cache_shape.py commands          # rules; exit 1 on violation
python scripts/check_command_cache_shape.py --json commands   # per-command metadata
```

- **R1 (no brace form).** Any `${NAME` of a vocabulary name is a violation. This is the rule
  with real value today.
- **R2 (prefix ratchet).** A file's `first_volatile_line` must be at least the value recorded
  for it in `scripts/command_cache_shape_baseline.json`. The value is the 1-based line of
  the first bare token, or the line count + 1 if the file has none. Today's baseline is a
  snapshot (3 for most files), not a cache guarantee. R2 starts to protect something once a
  later change raises a file's number.
- **R3 (baseline complete).** Every `commands/*.md` needs a baseline entry and every entry
  needs a file. The error message prints the exact entry to add or remove.

Updating the baseline:

- **Adding or renaming a command file:** add, rename or remove its entry in the same commit.
  The R3 message prints the line to add.
- **Improving a file**, for example moving its first token later: raise its number in the
  same commit. `tests/test_check_command_cache_shape.py` asserts that the baseline equals
  the current values exactly.
- **Lowering a number** means a prompt now busts the cache earlier. Do it only in a reviewed
  ticket that says why.

## Prompt-shape metadata

`--json` prints one object per command file:

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

`volatile_tail_tokens_est` is `volatile_tail_bytes // 4`, an estimate. The metadata is
static: it is a function of the committed file and is the same for every run of a commit.
No per-run artifact is written.

## Measuring cache effect

Use the per-node fields already in each run's `run-record.json` (#645):
`gen_ai.usage.cache_read_input_tokens` and `gen_ai.usage.cache_creation_input_tokens` on
each entry of `nodes`. Do **not** use `harness_economics.ledger_mechanics.cache_hit_ratio`.
It is computed from model-proxy ledger rows whose cache columns are hardcoded to zero.

Procedure for one phase, for example `plan` (node id `plan`, command `dark-factory-plan`):

1. Upper bound on what a perfect prefix could save per run of that node:
   `volatile_tail_tokens_est` × (cache-write price − cache-read price).
   ```bash
   python scripts/check_command_cache_shape.py --json commands \
     | jq '.[] | select(.command=="dark-factory-plan") | .volatile_tail_tokens_est'
   ```
2. Compare it with that node's cache-creation tokens in recent run records. Join on run id
   **and** node id, because a run-level total mixes phases:
   ```bash
   jq '{run_id, node: (.nodes[] | select(.node_id=="plan")
        | {cache_creation: ."gen_ai.usage.cache_creation_input_tokens",
           cache_read: ."gen_ai.usage.cache_read_input_tokens"})}' \
     <artifacts-root>/runs/<run-id>/run-record.json
   ```

**Limitation.** Per-node figures sum every turn of the node. They cannot isolate a cross-run
prefix hit on the first request. They bound the possible gain; they do not prove a realised
one. Restructuring prompts is worth a ticket only if the bound is a meaningful share of the
node's cache-creation tokens, and that call is the operator's. A cross-run hit is impossible
whatever the command file's shape if the harness system prompt carries per-run values
(working directory, date, branch). It also needs a second run of the same phase on the same
model inside the cache lifetime.

## Other prompt surfaces

- `refinement-skills/` persona templates are rendered mid-run by
  `scripts/factory_core/prompt_render.py`. It never substitutes inside backtick spans and
  fails on any slot/value mismatch. These templates are never part of a command node's
  first-request prefix and are not linted.
- Reviewer rubrics (`.claude/skills/{conformance,code-review}/RUBRIC.md`) are read from disk
  during a run, and no template engine touches them. They are not linted.
- There is no role-card schema to align with (#42 deferred it).
- Anything later loaded by Archon as a `command:` node lives in `commands/` and is covered
  automatically.

## Not covered

- The issue's "forbidden in stable prefix" list (dates, random ids, file listings, log
  snippets) is not detected. Only Archon-substituted tokens are.
- The three inline `prompt:` nodes in the workflow YAML go through the same substitution
  but are not linted.
- No prompt text has been moved toward the target shape.
- `commands/dark-factory-refine.md` names `$ISSUE_CONTEXT` in prose. Archon blanks it, so
  the agent reads empty backticks there. `--json` shows it. It is not yet fixed.

## Rollback

The lint changes no prompt text, so it cannot change phase behaviour. If it is noisy or
wrong, delete its `- run: python scripts/check_command_cache_shape.py commands` line from
the `dag-check` job in `.github/workflows/ci.yml`, or revert the commit. There is no runtime
switch or config key, and nothing to coordinate with a running scheduler.

# Extract cost-report + post-mortem rendering into `factory_core` (#182)

**Issue:** #182 · **Status:** spec-pending-review

## Overview

Three behaviour blobs are trapped inside `entrypoint.sh` with no import seam — they can
only execute inside a run container, so none of them has a single unit test:

- `post_cost_report` (`entrypoint.sh:250-413`, 163 lines): `jq`/`bc` data transformation
  — money/token/duration formatting, cumulative-total bookkeeping (parsed back out of
  the *existing* GitHub comment body), budget-line assembly from `context-budget.json`.
  This is the densest untested logic block in the repo, and the surface with a live
  regression history (`test_cost_report_endpoint.sh`'s 404 bug; the `estimated_input_tokens`
  vs `reserved_tokens` bug guarded by `test_budget_line_trim.sh`).
- `run_post_mortem` (`entrypoint.sh:160-248`, 88 lines): run-directory discovery,
  transcript/artifact gathering, prompt assembly, an LLM call, comment formatting, and
  JSONL telemetry write.
- The rate-limit reset-time parser (`entrypoint.sh:717-757`): `grep`/`date` arithmetic
  computing how long to sleep before retrying after a Claude usage-limit message.

The sibling module `factory_core/run_record.py` (368 lines) already proves the target
shape: it extracted run-record *assembly* behind a clean interface, reachable via
`scripts/factory_core/cli.py run-record assemble ...` (`entrypoint.sh:772`), and is
covered by `tests/test_run_record.py` (352 lines). The *rendering* logic never crossed
that same seam. This ticket extracts it, following the exact conventions `run_record.py`,
`epic_autopilot.py`, and `cli.py` already establish.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **`factory_core/cost_report.py`** owns all of `post_cost_report`'s formatting logic —
   money/token/duration formatters, cumulative-total bookkeeping, and budget-line
   assembly — as pure functions. No `gh`, no `docker`, no `archon` inside the module.
   - The interface is **not** literally "run-record.json in, markdown out" as the issue's
     Solution section shorthand suggests — cumulative bookkeeping requires the *prior*
     comment body as an additional input (today, `post_cost_report` fetches it via
     `gh api .../issues/comments/${COMMENT_ID}` and regex-extracts `PRIOR_RUNS`/
     `PREV_COST`/`PREV_IN`/`PREV_OUT` from it at `entrypoint.sh:288-298`). The `gh api`
     fetch stays bash-side; the prior-body **string** and a **timestamp** are passed in
     as explicit arguments so the render is deterministic and gh/docker-free (Q&A #1, #3).
   - **Both existing token formatters must be reproduced exactly, not unified.** The
     current bash has two independently-implemented `fmt_tokens`: a `jq` version
     (`entrypoint.sh:268-270`) used for per-node table rows, and a separate shell
     function using `bc scale=1` (`entrypoint.sh:311-320`) used for the cumulative/
     subtotal lines (`:385`, `:393`). They can diverge on edge cases (e.g. `"1K"` vs
     `"1.0K"`). `cost_report.py` must expose both behaviors distinctly, keyed to where
     each is used today, so the rendered output stays byte-for-byte identical (Q&A #1).
   - Unit tests cover the token/duration/cost formatters in isolation at their branch
     boundaries (sub-1000, sub-1M, rounding paths for tokens; sub-1s, sub-60s, minutes
     for duration; the 4-decimal cost rounding), **separately** from the full-comment
     golden(s) (Q&A #1).
   - Golden test scenarios (hand-authored inline, see Requirement 4) must cover: a
     multi-node/multi-model table, a first-run case (no prior comment → `RUN_COUNT=1`),
     a prior-comment case exercising `PRIOR_RUNS`/cumulative-total bookkeeping, and a
     `context-budget.json` (schema v2) input covering the `savings`, `fallbacks`, and
     both `over_budget`/`would_trim` budget-line branches. The `would_trim` case must
     specifically pin `estimated_input_tokens` (not `reserved_tokens`) as the displayed
     value — the regression `test_budget_line_trim.sh` currently guards (Q&A #4).

2. **`factory_core/post_mortem.py`** owns `run_post_mortem`'s gather/format logic as
   pure(-ish) functions, split along one discriminator: **local filesystem operations
   move into Python; GitHub/LLM network calls stay bash-side**, matching how
   `run_record.py` reads local artifact files directly in-module while
   `epic_autopilot.py`'s `io` wrapper only ever covers `gh`/`claude` subprocess calls
   (Q&A #2). Concretely:
   - **Move to Python:** the run-directory discovery (globbing
     `${HOME}/.archon/workspaces/.../artifacts/runs/*/issue.json`, matching
     `resolved_number`, picking the most-recently-modified match via the
     `ls -dt | head -1` semantics — `entrypoint.sh:178-193`), the transcript
     `tail -200` read (`:174`), reading up to four artifact `.md` files, prompt
     assembly (`:196-210`), the final comment-body formatting (marker, heading, body,
     `**Exit code:** … | **Phase:** … | **Timestamp:** …` footer — `:222-231`), and the
     JSONL telemetry record assembly (the `printf`/escaping/truncation logic at
     `:236-246`) plus its local append (mirroring `run_record.py`'s `_append_jsonl`).
   - **Stay bash-side, results passed in as plain string arguments:** the
     `claude -p --model claude-haiku-4-5-20251001` call (`:213`), the `gh issue comment`
     post (via `post_or_update_comment`), and the `gh issue view --json title` title
     fetch used inside the JSONL record (`:241`).
   - `run_post_mortem` **remains a bash function with its current name and 2-argument
     signature** (`run_post_mortem <exit_code> <transcript_file>`) — `tests/
     test_431_telemetry_isolation.sh` sources `entrypoint.sh` with
     `ENTRYPOINT_SOURCE_ONLY=1` and calls it directly, stubbing `git`/`gh`/`docker`/
     `claude` but **not** `python3`. Its body shrinks to the `claude`/`gh` calls plus a
     delegation to `python3 cli.py post-mortem ...` for everything else. This test must
     keep passing unchanged — including its zero-git-operations assertion — and now
     additionally exercises the real Python JSONL-write path instead of pure bash
     (Q&A #4).

3. **`factory_core/reset_time.py`** is a new dedicated module (not folded into an
   existing file — no current `factory_core` module owns date/time logic, and the
   two sibling extractions in this ticket are also new dedicated files, so this keeps
   the established one-file-per-concern convention — Q&A #3). It exposes one pure
   function: reset-header text (plus an injected "now" epoch, not a live `date +%s`
   call) in, sleep-until-seconds out. It reproduces, as testable branch boundaries, the
   existing parse/rollover/clamp logic at `entrypoint.sh:727-748`: regex-extract the
   `HH:MMam/pm` time and optional `(Timezone)`, compute the target epoch, roll forward
   a day if the target has already passed, add the 60s buffer, and fall back to the
   300s default on any parse failure or an out-of-bounds (`<0` or `>90000`) result.

4. **`scripts/factory_core/cli.py` gets three new subcommands**, one per new module,
   following the existing explicit-argparse-flag style (`board-move`, `breaker-trip`) —
   not the `run-record` subcommand's `nargs=argparse.REMAINDER` passthrough style, since
   these are single-purpose, not modules with their own internal subparser tree
   (Q&A #3):
   - `cost-report render --run-record-file PATH [--prior-body-file PATH]
     --timestamp ISO --promoted-at ISO` → prints the markdown comment body to stdout.
   - `post-mortem gather --artifacts-base DIR --issue N [--transcript-file PATH]` →
     prints gathered evidence (transcript tail + artifacts context) for prompt assembly,
     and a `post-mortem format --exit-code N --intent STR --promoted-at ISO
     --text-file PATH [--title STR] --artifacts-dir DIR` → writes the comment body to
     stdout and appends the JSONL telemetry record.
   - `reset-time parse --text-file PATH [--now EPOCH]` → prints sleep-seconds to
     stdout.
   `entrypoint.sh`'s three functions become thin bash wrappers that shell out to these
   subcommands (mirroring the existing `run-record assemble` invocation at
   `entrypoint.sh:772`) plus whatever `gh`/`claude`/`sleep` calls must remain per
   Requirements 1-3.

5. **Existing test files are triaged, not left to bit-rot** (Q&A #4):
   - `tests/test_431_telemetry_isolation.sh` — kept, unchanged, must keep passing (see
     Requirement 2).
   - `tests/test_cost_report_endpoint.sh` — the grep assertions are left in place (the
     comment-read/PATCH `gh api` endpoint calls stay bash-side, so the endpoint-path
     regression it guards is still live post-refactor); its header comment claiming
     "behavioral testing of `post_cost_report` is impractical" is updated to note that
     the formatting half is now covered by `cost_report.py`'s unit/golden tests, while
     the guard itself continues to scope the still-bash-side endpoint bug.
   - `tests/test_budget_line_trim.sh` — retired, **contingent on** its exact regression
     case (`would_trim=true` must render `estimated_input_tokens`, not
     `reserved_tokens`) being ported into a named `cost_report.py` golden/unit test
     first. This is a migrate-then-delete, not a bare deletion.

## Architecture / Approach

### `scripts/factory_core/cost_report.py` (new)

- `format_tokens_table(n: int) -> str` and `format_tokens_cumulative(n: int) -> str` —
  two distinct token formatters reproducing the `jq` (`:268-270`) and shell/`bc`
  (`:311-320`) implementations respectively, keyed to their current call sites (per-node
  table cells vs. cumulative/subtotal lines).
- `format_duration(ms: int) -> str` — mirrors the `jq` `fmt_dur` def (`:271-273`).
- `format_cost(usd: float) -> str` — mirrors the `jq` `fmt_cost` def (`:274`).
- `parse_prior_cumulative(prior_comment_body: str) -> dict` — extracts `PRIOR_RUNS`
  (the `### Run:` blocks), `PREV_COST`/`PREV_IN`/`PREV_OUT` (the
  `<!-- cumulative: cost=X in=Y out=Z -->` marker), and `RUN_COUNT`, reproducing
  `entrypoint.sh:295-308`. Returns zeroed/empty defaults when `prior_comment_body` is
  empty (first-run case).
- `format_budget_line(budget_json: dict) -> str` — reproduces the schema-v2
  `savings`/`fallbacks`/`over_budget`/`would_trim` branch logic (`:322-368`), using
  `estimated_input_tokens` (falling back to `reserved_tokens` only when the former is
  absent, exactly as today) for the `would_trim` case.
- `render(run_record: dict, prior_comment_body: str, timestamp: str) -> str` — the
  top-level entry point assembling the full comment body (marker, cumulative-totals
  line, per-run section, table, subtotal row, footer), reproducing
  `entrypoint.sh:380-396` exactly, including its whitespace/blank-line conventions.

### `scripts/factory_core/post_mortem.py` (new)

- `gather_evidence(artifacts_base: str, issue_num: int, transcript_file: str | None) ->
  dict` — run-dir discovery (mtime-descending pick among
  `artifacts_base/*/issue.json` matches on `resolved_number`), transcript tail, and
  reading the four known artifact files, reproducing `entrypoint.sh:172-194`.
- `build_prompt(exit_code: int, intent: str, issue_num: int, evidence: dict) -> str` —
  reproduces the prompt template at `entrypoint.sh:196-210`.
- `render_comment(post_mortem_text: str, exit_code: int, intent: str, promoted_at:
  str) -> str` — reproduces the comment body at `entrypoint.sh:222-231`, with
  `promoted_at` injected (not `date -u` called live).
- `build_failure_record(issue_num: int, title: str, intent: str, exit_code: int,
  post_mortem_text: str, promoted_at: str) -> dict` — reproduces the JSONL record
  shape and the 500-char/newline-collapsed excerpt logic at `entrypoint.sh:236-246`.
- `append_failure_record(record: dict, artifacts_dir: str) -> None` — local JSONL
  append, mirroring `run_record.py`'s `_append_jsonl` (including the same
  create-parents-if-missing behavior; file locking is not required here since
  `run_post_mortem` is not called concurrently within one run).

### `scripts/factory_core/reset_time.py` (new)

- `compute_sleep_seconds(text: str, now_epoch: int) -> int` — regex-extracts the
  `resets HH:MMam/pm (Timezone)` pattern, computes the target epoch (using the injected
  `now_epoch`, not a live `date +%s`), rolls forward 86400s if the target already
  passed, adds the 60s buffer, and clamps to the 300s default on parse failure or an
  out-of-`[0, 90000]` result — reproducing `entrypoint.sh:727-748` exactly.

### `scripts/factory_core/cli.py`

Three new subcommands, each a few lines of argparse wiring in the same style as
`board-move`/`breaker-trip` (`cli.py:95-98`, `118-122`):

- `cost-report render --run-record-file PATH [--prior-body-file PATH] --timestamp ISO
  --promoted-at ISO` → reads the run-record JSON and (optional) prior-body file, calls
  `cost_report.render(...)`, prints to stdout.
- `post-mortem gather --artifacts-base DIR --issue N [--transcript-file PATH]` → calls
  `post_mortem.gather_evidence(...)`, prints as JSON (consumed by bash to build the
  `claude -p` prompt via `build_prompt`, invoked in the same subcommand or a companion
  `post-mortem prompt` subcommand — implementer's choice, since both are pure and
  gh/docker-free either way).
- `post-mortem format --exit-code N --intent STR --promoted-at ISO --text-file PATH
  --issue N [--title STR] --artifacts-dir DIR` → calls `render_comment` (prints to
  stdout for the `gh issue comment` call) and `build_failure_record` +
  `append_failure_record` (writes the JSONL line).
- `reset-time parse --text-file PATH [--now EPOCH]` → calls
  `compute_sleep_seconds(...)`, prints the integer seconds to stdout. `--now` defaults
  to the live epoch when omitted (production use); tests always pass it explicitly.

### `entrypoint.sh`

- `post_cost_report()`: keeps its name; body shrinks to fetching `RUN_RECORD_FILE`/
  existing-comment lookup (`gh api`, unchanged), calling `python3 "$FACTORY_CORE_CLI"
  cost-report render ...` for the markdown body, and the create-or-update `gh api`/
  `gh issue comment` calls (unchanged).
- `run_post_mortem()`: keeps its name and 2-arg signature; body shrinks to the
  early-return guards (unchanged), `python3 "$FACTORY_CORE_CLI" post-mortem gather
  ...` to build the prompt, the `claude -p` call (unchanged), `python3
  "$FACTORY_CORE_CLI" post-mortem format ...` for the comment body + JSONL write, and
  the `post_or_update_comment` call (unchanged).
- The reset-time block (`entrypoint.sh:717-757`) shrinks to writing `$TMP_OUT` to a
  file, calling `python3 "$FACTORY_CORE_CLI" reset-time parse --text-file "$TMP_OUT"`,
  and `sleep`-ing on the result.

### Test changes

- New `tests/test_cost_report.py`, `tests/test_post_mortem.py`,
  `tests/test_reset_time.py` (or one combined file per module, following
  `tests/test_run_record.py`'s per-module convention), each with inline-dict fixtures
  (no new `fixtures/`/`golden/` directory — matches the existing repo-wide convention).
- `tests/test_431_telemetry_isolation.sh`: unchanged, must keep passing.
- `tests/test_cost_report_endpoint.sh`: header comment updated; assertions unchanged.
- `tests/test_budget_line_trim.sh`: deleted once its `estimated_input_tokens`-not-
  `reserved_tokens` case is ported into `tests/test_cost_report.py`.

## Alternatives considered

1. **Unify the two divergent `fmt_tokens` implementations into one Python function as
   part of this refactor**, instead of preserving both. Rejected — AC #4 requires
   byte-for-byte compatibility with current output, and the two formatters can render
   the same input differently (`"1K"` vs `"1.0K"`); silently unifying them would change
   rendered comment text for at least one of the two call sites, which is exactly the
   kind of regression `test_cost_report_endpoint.sh` and `test_budget_line_trim.sh`
   exist to catch. Unifying the formatters is a legitimate follow-up once this
   extraction lands and the divergence is visible in a diff/golden rather than buried
   in bash, but it's out of scope here.
2. **Wrap the truly-external `claude -p` and `gh` calls behind a Python `io` object
   (full `epic_autopilot.py`-style injection), rather than leaving them as bare bash
   subprocess calls that pass results into Python as arguments.** Rejected for this
   ticket — `epic_autopilot.py`'s `io` abstraction exists because `run_once` needed a
   single pure function orchestrating multiple sequential external calls with
   branching logic between them; `run_post_mortem` has exactly one LLM call and one
   comment post with no branching logic between them worth abstracting behind an
   interface. AC #2 ("LLM call and comment post stay injected/entrypoint-side") is
   satisfied by keeping them as plain bash calls whose string outputs are passed into
   Python functions — a smaller, YAGNI-consistent change matching the issue's literal
   wording without introducing a new `io`-protocol class for a single-call use case.
3. **Give `reset-time`/`post-mortem`/`cost-report` cli.py subcommands the
   `run-record`-style `nargs=argparse.REMAINDER` passthrough** instead of explicit
   flags. Rejected — that style exists specifically because `run_record.py` owns its
   own internal argparse subparser tree (`record`/`assemble`) that needs to see raw
   `sys.argv`; none of the three new modules has that shape, so explicit flags (matching
   `board-move`/`breaker-trip`) are simpler and self-documenting in `--help` output.
4. **Delete `tests/test_budget_line_trim.sh` outright without porting its case
   forward.** Rejected — the file guards a real, previously-shipped regression
   (`would_trim` must use `estimated_input_tokens`, not `reserved_tokens`); deleting it
   without an equivalent case in `cost_report.py`'s tests would silently drop coverage
   for a bug that has already recurred once.

## Open questions (non-blocking)

- Whether `post-mortem gather` and `post-mortem prompt` (building the `claude -p`
  prompt string from gathered evidence) are one cli.py subcommand or two is left to the
  implementer — both are pure/gh-docker-free either way, so it doesn't affect
  testability or byte-compatibility, only the shape of the bash call site.
- The exact JSON shape `post-mortem gather` prints to stdout (for bash to feed into the
  next step) isn't pinned in this spec — any implementer-chosen shape is fine as long
  as `build_prompt`'s reproduced output is unchanged.
- Whether file locking should be added to `post_mortem.py`'s `append_failure_record`
  (mirroring `run_record.py`'s `fcntl.flock`) even though `run_post_mortem` isn't
  called concurrently within a single run today — left to the implementer's judgment;
  not required by any acceptance criterion.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the working tree at the
  start of this refinement (no specs have merged to `main` yet, though several exist on
  other open `refine/*` branches) — this spec is written as a new file.
- **[ASSUMPTION]** "Rendered comment output is byte-compatible with current output
  (goldens from a real run-record.json)" is satisfied by hand-authored ("golden by
  construction") inline-dict fixtures re-derived line-by-line from the current bash
  logic, rather than fixtures captured by executing the live bash function — this
  refinement environment (and, per Q&A, the implementation environment too, absent
  contrary information) has no docker/gh access to produce an execution-captured
  golden, and this matches the repo's existing inline-fixture test convention
  (`tests/test_run_record.py`).
- **[ASSUMPTION]** `reset_time.compute_sleep_seconds`'s `--now` CLI flag is optional
  (defaulting to the live epoch) so production `entrypoint.sh` usage requires no new
  bash-side epoch computation, while tests always pass it explicitly for determinism —
  the issue text only specifies the pure-function requirement, not the CLI ergonomics
  around it.
- **[ASSUMPTION]** No file-locking is required in `post_mortem.py`'s JSONL append
  (unlike `run_record.py`'s `_append_jsonl`), since `run_post_mortem` fires at most once
  per failed run and isn't invoked concurrently with itself; this mirrors current bash
  behavior (`entrypoint.sh:246`, a bare `>>` append) rather than introducing new
  concurrency safety not present today.

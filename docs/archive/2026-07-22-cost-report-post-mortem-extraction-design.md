# Extract cost-report + post-mortem rendering into `factory_core` (#182)

**Issue:** #182 · **Status:** spec-pending-review

## Overview

This is a re-refinement. A 2026-07-07 spec for this ticket was discarded as stale by a
2026-07-22 operator comment: its branch sat 351 commits behind `main`, every line-number
anchor it cited was wrong, and — the decisive part — one of its three deliverables had
already shipped under a different ticket. `entrypoint.sh` grew 784→1005 lines across 26
commits between the two refine passes; this spec re-derives everything from current
`main` rather than trusting the discarded document or the issue body's original line
numbers.

**Scope has shrunk to two targets**, not three:

- `post_cost_report` (`entrypoint.sh:395-594`, 200 lines): `jq`/`bc` data transformation
  — money/token/duration formatting, cumulative-total bookkeeping (parsed back out of
  the *existing* GitHub comment body), budget-line assembly from `context-budget.json`,
  plus a `harness_economics` line and a "zero rendered rows" loud-diagnostic branch that
  did not exist in the 2026-07-07 draft. Both the diagnostic branch and the economics
  line landed via #300 (a five-bug regression fix) after the original spec was written,
  and grew this function considerably.
- `run_post_mortem` (`entrypoint.sh:178-269`, 92 lines): run-directory discovery,
  transcript/artifact gathering, prompt assembly, an LLM call, comment formatting, and
  JSONL telemetry write. Materially unchanged from the 2026-07-07 draft's description.

**Dropped from scope:** the rate-limit reset-time parser. It shipped via #35/#305 as
`scripts/factory_core/session_window.py` — a fully pure, already-tested module
(`is_session_window_failure`, `parse_structured_reset_epoch`,
`parse_fallback_reset_epoch`, `compute_resume_epoch`, `check_and_pause`), reached from
`entrypoint.sh`'s `_handle_session_window_pause()` (`entrypoint.sh:277-...`) via
`cli.py session-window-check`. `entrypoint.sh` no longer contains its own reset-time
parsing logic at all — there is nothing left in this file for this ticket to extract.
`session_window.py` is treated below purely as **prior art**: the third proof (after
`run_record.py`) that the `factory_core` + `cli.py` seam works for this kind of
extraction.

The sibling module `factory_core/run_record.py` (832 lines) and `session_window.py`
(115 lines) prove the target shape: pure local-filesystem-and-computation functions in
a dedicated module, reachable via an explicit-flag `cli.py` subcommand, with
network/LLM calls left to the bash caller. This ticket applies that same shape to the
two remaining rendering blobs.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **`factory_core/cost_report.py`** owns all of `post_cost_report`'s formatting logic —
   money/token/duration formatters, cumulative-total bookkeeping, budget-line assembly,
   the `harness_economics` line, and the savings/fallbacks block — as pure functions.
   No `gh`, no `docker`, no `archon` inside the module.
   - The interface is **not** literally "run-record.json in, markdown out" — cumulative
     bookkeeping requires the *prior* comment body as an additional input (today,
     `post_cost_report` fetches it via `gh api .../issues/comments/${COMMENT_ID}` and
     regex-extracts `PRIOR_RUNS`/`PREV_COST`/`PREV_IN`/`PREV_OUT` from it at
     `entrypoint.sh:466-478`). The `gh api` fetch stays bash-side; the prior-body
     **string** and a **timestamp** are passed in as explicit arguments so rendering is
     deterministic and gh/docker-free.
   - **Both existing token formatters must be reproduced exactly, not unified.** The
     current bash has two independently-implemented token formatters: a `jq` version
     inline in the per-node table `jq` script (`entrypoint.sh:424-426`, used for table
     cells) and a separate shell function using `bc scale=1`
     (`entrypoint.sh:490-499`, used for the cumulative/subtotal lines and the savings
     line). They can diverge on edge cases (e.g. `"1K"` vs `"1.0K"`). `cost_report.py`
     exposes both behaviors distinctly, keyed to where each is used today, so rendered
     output stays byte-for-byte identical.
   - Unit tests cover the token/duration/cost formatters in isolation at their branch
     boundaries (sub-1000, sub-1M, rounding paths for tokens; sub-1s, sub-60s, minutes
     for duration; the 4-decimal cost rounding), **separately** from the full-comment
     golden(s).
   - Golden test scenarios (hand-authored inline, see Requirement 4) must cover: a
     multi-node/multi-model table, a first-run case (no prior comment → `RUN_COUNT=1`),
     a prior-comment case exercising `PRIOR_RUNS`/cumulative-total bookkeeping, a
     `harness_economics`-present and a `harness_economics`-absent case (older
     run-record.json files predate the field — `entrypoint.sh:409-418`'s jq `//`
     fallbacks must be preserved), and a `context-budget.json` (schema v2) input
     covering `savings`, `fallbacks`, and both `over_budget`/`would_trim` budget-line
     branches. The `would_trim` case must specifically pin `estimated_input_tokens`
     (not `reserved_tokens`) as the displayed value — the regression
     `test_budget_line_trim.sh` currently guards.
   - A zero-nodes case is covered by a dedicated diagnostic-formatting test (see
     Requirement 1a below), not folded into the golden-comment tests (it never reaches
     comment rendering).

   **1a. The zero-rendered-rows short-circuit (`entrypoint.sh:435-458`) is a hard,
   test-constrained control-flow boundary, not just formatting.**
   `tests/test_entrypoint_cost_report_regression.sh` sources `entrypoint.sh` for real,
   stubs `gh`/`git`/`docker`/`claude`/`archon`, calls `post_cost_report` directly with
   `ISSUE_NUM`/`ARTIFACTS_DIR`/`RUN_ID` set, and asserts: return code 0, **exactly
   zero** stubbed `gh` calls, and that stderr contains the literal string
   `"ERROR: cost report has zero node rows"` plus `archon_cost_capture.ok=False`
   evidence — reproducing df#300's exact regression signature (a completed run with
   empty `nodes[]` previously looked successful while silently posting nothing). This
   constrains the split as follows:
   - `cost_report.py` exposes a pure `check_renderable(run_record: dict) -> dict |
     None` — returns `None` when `nodes` is non-empty (proceed to render), or a
     diagnostic dict (`nodes_count`, `capture_ok`, `capture_exit_code`,
     `capture_stderr`) when empty, reproducing the `jq` extraction at
     `entrypoint.sh:441-444`. This is the **only** place `.nodes` length is inspected —
     no second jq/JSON pass anywhere re-derives it.
   - `cost_report.py` also exposes a pure `format_missing_diagnostic(diagnostic: dict,
     run_id: str, issue: int) -> str` reproducing the exact stderr message text at
     `entrypoint.sh:445` byte-for-byte (this is the string the regression test greps
     for).
   - The **orchestration** (deciding to print that string to stderr, dispatching the
     `factory.cost_report.missing` health-event, and exiting non-zero so bash makes
     zero `gh` calls) lives in a `cli.py` subcommand handler — see Architecture below —
     not inside `cost_report.py` itself, since dispatching the health-event is a
     network call (a Seq HTTP POST via `run_record._post_seq_raw`), and the
     local-filesystem-vs-network discriminator (Requirement 2) says network stays out
     of the pure-formatting module even when, as here, the network call isn't
     `gh`/`claude`.
   - `post_cost_report` keeps its current bash function name and no-argument,
     env-driven signature (`ISSUE_NUM`/`ARTIFACTS_DIR`/`RUN_ID` read from the
     environment, exactly as today) — this test drives it exactly that way and cannot
     be changed to pass arguments instead.

2. **`factory_core/post_mortem.py`** owns `run_post_mortem`'s gather/format logic as
   pure(-ish) functions, split along one discriminator: **local filesystem operations
   move into Python; GitHub/LLM network calls stay bash-side**, matching how
   `run_record.py` reads local artifact files directly in-module while
   `epic_autopilot.py`'s `io` wrapper only ever covers `gh`/`claude` subprocess calls.
   Concretely:
   - **Move to Python:** run-directory discovery (globbing
     `${HOME}/.archon/workspaces/.../artifacts/runs/*/issue.json`, matching
     `resolved_number`, picking the most-recently-modified match via the
     `ls -dt | head -1` semantics — `entrypoint.sh:196-201`), the transcript
     `tail -200` read (`:191-193`), reading up to four artifact `.md` files
     (`:203-212`), prompt assembly (`:214-228`), the final comment-body formatting
     (marker, heading, body, `**Exit code:** … | **Phase:** … | **Timestamp:** …`
     footer — `:241-249`), and the JSONL telemetry record assembly (the
     `printf`/escaping/truncation logic at `:253-266`) plus its local append (mirroring
     `run_record.py`'s `_append_jsonl`).
   - **Stay bash-side, results passed in as plain string arguments:** the
     `claude -p --model claude-haiku-4-5-20251001` call (`:231`), the
     `post_or_update_comment` call that does the actual `gh` post (`:240`), and the
     `gh issue view --json title` title fetch used inside the JSONL record (`:257-258`).
   - `run_post_mortem` **remains a bash function with its current name and 2-argument
     signature** (`run_post_mortem <exit_code> <transcript_file>`) —
     `tests/test_431_telemetry_isolation.sh` sources `entrypoint.sh` with
     `ENTRYPOINT_SOURCE_ONLY=1` and calls it directly, stubbing `git`/`gh`/`docker`/
     `claude` but **not** `python3`. Its body shrinks to the `claude`/`gh` calls plus a
     delegation to `python3 cli.py post-mortem ...` for everything else. This test must
     keep passing unchanged — including its zero-git-operations assertion — and now
     additionally exercises the real Python JSONL-write path instead of pure bash.

3. **No `reset_time.py` module is created.** The equivalent extraction already exists
   as `session_window.py`; this ticket touches neither that file nor
   `_handle_session_window_pause()`.

4. **`scripts/factory_core/cli.py` gets subcommands for the two remaining modules**,
   following the existing explicit-argparse-flag style (`board-move`, `breaker-trip`,
   `session-window-check`) — not the `run-record` subcommand's
   `nargs=argparse.REMAINDER` passthrough style, since these are single-purpose
   modules, not modules with their own internal subparser tree:
   - `cost-report check --run-record-file PATH --run-id ID --issue N` → the guard from
     Requirement 1a. Exits 0 silently when renderable. Exits non-zero (e.g. `3`) when
     `nodes` is empty, after printing the diagnostic to stderr and (best-effort,
     non-blocking) dispatching the health-event in-process.
   - `cost-report render --run-record-file PATH [--prior-body-file PATH]
     --timestamp ISO` → prints the full markdown comment body to stdout. Only called
     by bash after `check` exits 0.
   - `post-mortem gather --artifacts-base DIR --issue N [--transcript-file PATH]` →
     prints gathered evidence for prompt assembly.
   - `post-mortem format --exit-code N --intent STR --promoted-at ISO --text-file PATH
     --issue N [--title STR] --artifacts-dir DIR` → prints the comment body to stdout
     and appends the JSONL telemetry record.
   `entrypoint.sh`'s two functions become thin bash wrappers that shell out to these
   subcommands via the existing `"$CLONE_DIR/dark-factory/scripts/factory_core/cli.py"`
   invocation pattern already used at every other `cli.py` call site in this file
   (e.g. `entrypoint.sh:284`, `:450`, `:619`) — **not** a new `$FACTORY_CORE_CLI`
   variable; match the established literal-path convention (`# TARGET-PATH` comment
   included) rather than inventing a new indirection.

5. **`run_record.py`'s health-event dispatch is refactored to be callable in-process,
   not just via a `cli.py` subprocess call.** Today, `entrypoint.sh:450` shells out to
   a *second* `cli.py run-record health-event ...` subprocess from inside
   `post_cost_report`. Once the zero-rows check moves into `cli.py cost-report check`'s
   handler (Requirement 1a), that handler dispatches the same event in-process instead
   — extract a plain `emit_health_event(event: str, issue: int, run_id: str, detail:
   dict) -> None` function out of `run_record.cmd_health_event`'s body (used by both
   the existing `run-record health-event` CLI subcommand, unchanged, and the new
   `cost-report check` handler), preserving the existing best-effort
   try/except-around-`_post_seq_raw` semantics.

6. **All six test files that intersect this refactor's scope are triaged explicitly,
   not left to bit-rot:**

   | File | What it does | Verdict |
   |---|---|---|
   | `tests/test_431_telemetry_isolation.sh` | Sources `entrypoint.sh`, calls `run_post_mortem` as a real bash function, asserts zero git ops + one JSONL line | **Keep unchanged, must keep passing** (Requirement 2's hard constraint) |
   | `tests/test_entrypoint_cost_report_regression.sh` | Sources `entrypoint.sh`, calls `post_cost_report` as a real bash function, asserts RC=0, zero `gh` calls, exact stderr diagnostic string on the zero-rows path | **Keep unchanged, must keep passing** (Requirement 1a's hard constraint — same class as `test_431`) |
   | `tests/test_cost_report_endpoint.sh` | Static grep guard: the single-comment `gh api` endpoint must never carry the issue number (a 404 bug) | **Keep, update stale header comment** — the `gh api` calls it greps for stay bash-side, so the endpoint bug it guards is still live post-refactor; its "behavioral testing is impractical" comment is updated to point at `cost_report.py`'s new unit/golden tests |
   | `tests/test_budget_line_trim.sh` | Standalone `jq`-logic replica: `would_trim` must render `estimated_input_tokens`, not `reserved_tokens` | **Migrate-then-delete** — port the exact case into a named `cost_report.py` test first, then delete this file |
   | `tests/test_cost_report_savings.sh` | Standalone `jq`-logic replica for the savings/fallbacks block against a synthetic `context-budget.json` v2 fixture; never sources `entrypoint.sh` | **Migrate-then-delete** — same pattern as `test_budget_line_trim.sh` |
   | `tests/test_cost_report_harness_economics.sh` | `sed`-extracts `post_cost_report`'s and `on_failure`'s function bodies from `entrypoint.sh` source text and greps them | **Split verdict**: the `on_failure` `--status failed` assertion stays (that logic is untouched by this ticket, out of `post_cost_report`'s scope) — keep as-is; the `harness_economics`-rendering assertions target formatting logic that is *moving out* of `entrypoint.sh` into `cost_report.py`, so they go stale after the refactor — migrate that half into a named `cost_report.py` unit test (covering the absent-tolerant `//` fallback), then delete just that half (or the whole file if nothing else remains once the `on_failure` half is folded elsewhere — implementer's call, since both remaining assertions are small) |

## Architecture / Approach

### `scripts/factory_core/cost_report.py` (new)

- `format_tokens_table(n: int) -> str` and `format_tokens_cumulative(n: int) -> str` —
  two distinct token formatters reproducing the `jq` (`:424-426`) and shell/`bc`
  (`:490-499`) implementations respectively, keyed to their current call sites.
- `format_duration(ms: int) -> str` — mirrors the `jq` `fmt_dur` def (`:427-429`).
- `format_cost(usd: float) -> str` — mirrors the `jq` `fmt_cost` def (`:430`).
- `format_economics_line(run_record: dict) -> str` — reproduces the
  `harness_economics` extraction and absent-tolerant `//` fallbacks
  (`:409-418`); returns `""` when `harness_economics.outcome.state` is absent.
- `format_savings_block(budget_json: dict | None) -> str` — reproduces the
  schema-v2 `savings`/`fallbacks`/`over_budget`/`would_trim` branch logic
  (`:501-548`), using `estimated_input_tokens` (falling back to `reserved_tokens`
  only when the former is absent, exactly as today) for the `would_trim` case;
  returns `""` when `budget_json` is `None`, absent, or `schema_version < 2`.
- `parse_prior_cumulative(prior_comment_body: str) -> dict` — extracts `PRIOR_RUNS`
  (the `### Run:` blocks), `PREV_COST`/`PREV_IN`/`PREV_OUT` (the
  `<!-- cumulative: cost=X in=Y out=Z -->` marker), and `RUN_COUNT`, reproducing the
  `sed`/`grep -oP` parsing at `entrypoint.sh:474-477` (the `gh api` fetch that
  produces `EXISTING_BODY` at `:468-473` stays bash-side; only the parsing of the
  already-fetched string moves to Python). Returns zeroed/empty defaults when
  `prior_comment_body` is empty (first-run case).
- `check_renderable(run_record: dict) -> dict | None` — Requirement 1a's guard;
  `None` means "proceed to render," a dict means "zero rows, here's why."
- `format_missing_diagnostic(diagnostic: dict, run_id: str, issue: int) -> str` —
  Requirement 1a's exact stderr message text.
- `render(run_record: dict, prior_comment_body: str, timestamp: str) -> str` — the
  top-level entry point assembling the full comment body (marker, cumulative-totals
  line, per-run section, table, subtotal row, economics line, savings block, footer),
  reproducing `entrypoint.sh:420-577` exactly, including its whitespace/blank-line
  conventions. Callers are expected to have already confirmed `check_renderable(...)
  is None`; this function does not itself special-case empty `nodes`.

### `scripts/factory_core/post_mortem.py` (new)

- `gather_evidence(artifacts_base: str, issue_num: int, transcript_file: str | None) ->
  dict` — run-dir discovery (mtime-descending pick among
  `artifacts_base/*/issue.json` matches on `resolved_number`), transcript tail, and
  reading the four known artifact files, reproducing `entrypoint.sh:196-212`.
- `build_prompt(exit_code: int, intent: str, issue_num: int, evidence: dict) -> str` —
  reproduces the prompt template at `entrypoint.sh:214-228`.
- `render_comment(post_mortem_text: str, exit_code: int, intent: str, promoted_at:
  str) -> str` — reproduces the comment body at `entrypoint.sh:241-249`, with
  `promoted_at` injected (not `date -u` called live).
- `build_failure_record(issue_num: int, title: str, intent: str, exit_code: int,
  post_mortem_text: str, promoted_at: str) -> dict` — reproduces the JSONL record
  shape and the 500-char/newline-collapsed excerpt logic at `entrypoint.sh:253-266`.
- `append_failure_record(record: dict, artifacts_dir: str) -> None` — local JSONL
  append, mirroring `run_record.py`'s `_append_jsonl` (same
  create-parents-if-missing behavior; file locking is not required — see Assumptions).

### `scripts/factory_core/run_record.py` (modified)

- Extract `emit_health_event(event: str, issue: int, run_id: str, detail: dict) ->
  None` out of `cmd_health_event`'s body (Requirement 5); `cmd_health_event` becomes a
  thin argparse-args-unpacking wrapper around it, unchanged in CLI behavior.

### `scripts/factory_core/cli.py` (modified)

New subcommands, each a few lines of argparse wiring in the same style as
`board-move`/`session-window-check`:

- `cost-report check --run-record-file PATH --run-id ID --issue N` → reads the
  run-record JSON, calls `cost_report.check_renderable(...)`. If `None`: exit 0,
  nothing printed. If a diagnostic: print
  `cost_report.format_missing_diagnostic(...)` to stderr, call
  `run_record.emit_health_event("factory.cost_report.missing", issue, run_id,
  detail)` only when `diagnostic["capture_ok"] != "true"` (reproducing the existing
  `entrypoint.sh:449` gating — a legitimate zero-node run with successful capture is
  not a missing-report condition), then `sys.exit(3)`.
- `cost-report render --run-record-file PATH [--prior-body-file PATH]
  --timestamp ISO` → reads the run-record JSON and optional prior-body file, calls
  `cost_report.render(...)`, prints to stdout.
- `post-mortem gather --artifacts-base DIR --issue N [--transcript-file PATH]` →
  calls `post_mortem.gather_evidence(...)`, prints as JSON (consumed by bash to build
  the `claude -p` prompt via `build_prompt`, invoked in the same subcommand or a
  companion `post-mortem prompt` subcommand — implementer's choice, both are pure and
  gh/docker-free either way).
- `post-mortem format --exit-code N --intent STR --promoted-at ISO --text-file PATH
  --issue N [--title STR] --artifacts-dir DIR` → calls `render_comment` (prints to
  stdout for the `gh issue comment` call) and `build_failure_record` +
  `append_failure_record` (writes the JSONL line).

### `entrypoint.sh` (modified)

- `post_cost_report()`: keeps its name and env-driven (no-argument) signature; body
  shrinks to fetching `RUN_RECORD_FILE`, calling
  `python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report check
  ...` and `return`ing on non-zero exit (zero `gh` calls made — satisfies Requirement
  1a), the existing-comment lookup (`gh api`, unchanged), calling
  `python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report render
  ...` for the markdown body, and the create-or-update `gh api`/`gh issue comment`
  calls (unchanged).
- `run_post_mortem()`: keeps its name and 2-arg signature; body shrinks to the
  early-return guards (unchanged), `python3 .../cli.py post-mortem gather ...` to
  build the prompt, the `claude -p` call (unchanged), `python3 .../cli.py post-mortem
  format ...` for the comment body + JSONL write, and the `post_or_update_comment`
  call (unchanged).
- No other functions in `entrypoint.sh` change. `_handle_session_window_pause()` and
  the `session-window-check` call are untouched (out of scope — Requirement 3).

### Test changes

- New `tests/test_cost_report.py` and `tests/test_post_mortem.py` (one file per
  module, following `tests/test_run_record.py`'s convention), each with inline-dict
  fixtures (no new `fixtures/`/`golden/` directory — matches the existing repo-wide
  convention).
- `tests/test_431_telemetry_isolation.sh` and
  `tests/test_entrypoint_cost_report_regression.sh`: unchanged, must keep passing.
- `tests/test_cost_report_endpoint.sh`: header comment updated; assertions unchanged.
- `tests/test_budget_line_trim.sh` and `tests/test_cost_report_savings.sh`: deleted
  once their cases are ported into `tests/test_cost_report.py`.
- `tests/test_cost_report_harness_economics.sh`: its `harness_economics`-rendering
  half is ported into `tests/test_cost_report.py`; its `on_failure`/`--status failed`
  half is kept (either in this file or folded into an existing `on_failure`-focused
  test, implementer's call) since that logic stays in `entrypoint.sh`.

## Alternatives considered

1. **Unify the two divergent token-formatter implementations into one Python function**
   instead of preserving both. Rejected — the byte-compatibility acceptance criterion
   requires reproducing current output, and the two formatters can render the same
   input differently (`"1K"` vs `"1.0K"`); silently unifying them would change rendered
   comment text for at least one call site — exactly the kind of regression
   `test_cost_report_endpoint.sh` and `test_budget_line_trim.sh` exist to catch.
   Unifying the formatters is a legitimate follow-up once the divergence is visible in
   a diff/golden rather than buried in bash, but it's out of scope here.
2. **A single `cost-report render` subcommand that always prints the full comment body
   on success and signals "zero rows" via a non-zero exit with nothing on stdout**
   (i.e., don't split `check` and `render`). Rejected — the full comment body depends
   on the *prior* comment's body, which bash can only fetch via `gh api` **after**
   confirming there's something to post (fetching it unconditionally would put a `gh`
   call on the empty-rows path, violating the "zero `gh` calls on empty" test
   assertion). The zero-rows check must therefore run, and be answerable, *before* any
   `gh` call — which requires it to be a separate, cheaper subcommand than the one that
   consumes the prior-comment-body argument.
3. **Wrap the truly-external `claude -p` and `gh` calls behind a Python `io` object**
   (full `epic_autopilot.py`-style injection), rather than leaving them as bare bash
   subprocess calls that pass results into Python as arguments. Rejected for this
   ticket — `epic_autopilot.py`'s `io` abstraction exists because `run_once` needed a
   single pure function orchestrating multiple sequential external calls with
   branching logic between them; `run_post_mortem` has exactly one LLM call and one
   comment post with no branching logic between them worth abstracting behind an
   interface, and `post_cost_report`'s `gh` calls are all read/write pairs on the same
   resource (find comment → maybe read → maybe patch/create), not branching business
   logic. A smaller, YAGNI-consistent change (plain bash calls whose string outputs
   feed into Python functions) matches the issue's literal wording without introducing
   a new `io`-protocol class for single-call use cases.
4. **Give the new `cli.py` subcommands the `run-record`-style
   `nargs=argparse.REMAINDER` passthrough** instead of explicit flags. Rejected — that
   style exists specifically because `run_record.py` owns its own internal argparse
   subparser tree (`record`/`assemble`) that needs to see raw `sys.argv`; neither new
   module has that shape, so explicit flags (matching `board-move`/
   `session-window-check`) are simpler and self-documenting in `--help` output.
5. **Delete `test_budget_line_trim.sh`, `test_cost_report_savings.sh`, and the
   `harness_economics` half of `test_cost_report_harness_economics.sh` outright**
   without porting their cases forward. Rejected — each guards a real, previously
   regressed behavior (the `estimated_input_tokens` bug recurred once already);
   deleting without an equivalent case in `cost_report.py`'s tests would silently drop
   coverage.

## Open questions (non-blocking)

- Whether `post-mortem gather` and `post-mortem prompt` (building the `claude -p`
  prompt string from gathered evidence) are one `cli.py` subcommand or two is left to
  the implementer — both are pure/gh-docker-free either way, so it doesn't affect
  testability or byte-compatibility, only the shape of the bash call site.
- The exact JSON shape `post-mortem gather` prints to stdout (for bash to feed into the
  next step) isn't pinned in this spec — any implementer-chosen shape is fine as long
  as `build_prompt`'s reproduced output is unchanged.
- Whether the leftover half of `test_cost_report_harness_economics.sh` (the
  `on_failure`/`--status failed` assertion) stays as its own file or gets folded into
  another `on_failure`-focused test is left to the implementer — it's a file-hygiene
  choice, not a coverage question.
- Whether file locking should be added to `post_mortem.py`'s `append_failure_record`
  (mirroring `run_record.py`'s `fcntl.flock`) even though `run_post_mortem` isn't
  called concurrently within one run today — left to the implementer's judgment; not
  required by any acceptance criterion.

## Assumptions (flagged)

- **[ASSUMPTION]** Goldens are "golden by construction" (hand-derived from reading the
  current bash logic), not captured by executing the live bash function — this
  refinement environment has no docker/gh access to produce an execution-captured
  golden, and this matches the repo's existing inline-fixture test convention
  (`tests/test_run_record.py`). If the implementation environment *does* have such
  access, executing `post_cost_report`/`run_post_mortem` against a real
  `run-record.json` to cross-check the hand-derived goldens is a strict improvement,
  not a requirement.
- **[ASSUMPTION]** No file-locking is added to `post_mortem.py`'s JSONL append (unlike
  `run_record.py`'s `_append_jsonl`), since `run_post_mortem` fires at most once per
  failed run and isn't invoked concurrently with itself — this mirrors current bash
  behavior (`entrypoint.sh:267`, a bare `>>` append) rather than introducing new
  concurrency safety not present today.
- **[ASSUMPTION]** The health-event dispatch's HTTP timeout/best-effort semantics
  (`run_record._post_seq_raw`'s 5s timeout, swallow-all-exceptions) are preserved
  unchanged when `emit_health_event` is extracted and called in-process from
  `cli.py cost-report check`'s handler — this is a pure refactor-for-reuse, not a
  behavior change to the health-event mechanism itself.

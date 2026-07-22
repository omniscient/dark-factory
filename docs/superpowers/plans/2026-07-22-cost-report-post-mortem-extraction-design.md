# Implementation Plan: Extract cost-report + post-mortem rendering into `factory_core`

**Issue:** omniscient/dark-factory#182
**Spec:** `docs/superpowers/specs/2026-07-22-cost-report-post-mortem-extraction-design.md`

---

## Goal

Move `post_cost_report`'s and `run_post_mortem`'s dense formatting/gathering logic out
of `entrypoint.sh` into two new, pure(-ish), unit-tested `factory_core` modules
(`cost_report.py`, `post_mortem.py`), reachable via new `cli.py` subcommands, so this
logic is testable without docker/gh and stops silently regressing (df#300 found five
live bugs in this exact code while it was untestable bash). `entrypoint.sh`'s two
functions keep their current names/signatures and shrink to thin CLI delegations plus
the calls that must stay bash-side (`gh`, `claude -p`).

## Architecture

```
entrypoint.sh
  post_cost_report()          run_post_mortem()
       │                            │
       │ cli.py cost-report check   │ cli.py post-mortem gather
       │ gh api (existing comment)  │ claude -p (bash-side, unchanged)
       │ cli.py cost-report render  │ cli.py post-mortem format
       │ gh api/issue comment       │ post_or_update_comment (bash-side, unchanged)
       ▼                            ▼
scripts/factory_core/cli.py  (dispatch layer, argparse subcommands)
       │                            │
       ▼                            ▼
scripts/factory_core/        scripts/factory_core/
  cost_report.py               post_mortem.py
  (pure formatting/           (pure gather/format,
   bookkeeping, no gh/        local filesystem only,
   docker/network)             no gh/claude/network)
       │
       ▼
scripts/factory_core/run_record.py
  emit_health_event()  (extracted from cmd_health_event, callable in-process)
```

The IO-injection boundary: **local filesystem + pure computation → Python. GitHub API /
LLM / telemetry network calls → stay bash-side (or, for the health event, an in-process
Python call from the `cli.py cost-report check` handler)**, matching the existing
`run_record.py` / `session_window.py` seam.

## Tech Stack

- Python 3 stdlib only (`json`, `re`, `math`, `pathlib`) — no new dependencies, matching
  every existing `factory_core` module.
- `pytest` for the new unit/golden tests (`tests/test_cost_report.py`,
  `tests/test_post_mortem.py`), matching `tests/test_run_record.py`'s inline-fixture
  convention — no new `fixtures/`/`golden/` directory.
- Bash for the `entrypoint.sh` call sites and the two hard-constraint `.sh` regression
  tests, unchanged in style.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/cost_report.py` | **New** — formatters, bookkeeping, budget-line, zero-rows guard, `render()` |
| `scripts/factory_core/post_mortem.py` | **New** — evidence gathering, prompt/comment/JSONL formatting |
| `scripts/factory_core/run_record.py` | **Modified** — extract `emit_health_event()` out of `cmd_health_event` |
| `scripts/factory_core/cli.py` | **Modified** — `cost-report check`/`render`, `post-mortem gather`/`format` subcommands |
| `entrypoint.sh` | **Modified** — `post_cost_report()` and `run_post_mortem()` shrink to delegations |
| `tests/test_cost_report.py` | **New** — unit + golden tests |
| `tests/test_post_mortem.py` | **New** — unit tests |
| `tests/test_run_record.py` | **Modified** — add `emit_health_event()` coverage |
| `tests/test_431_telemetry_isolation.sh` | **`CLONE_DIR` setup + matching cleanup line changed** (verified necessary, see "Deviations from the spec" below); all assertions/stubs unchanged, must keep passing (hard constraint) |
| `tests/test_entrypoint_cost_report_regression.sh` | Unchanged — must keep passing (hard constraint) |
| `tests/test_cost_report_endpoint.sh` | **Modified** — header comment only, assertions unchanged |
| `tests/test_budget_line_trim.sh` | **Deleted** — case migrated into `test_cost_report.py` (Task 2) |
| `tests/test_cost_report_savings.sh` | **Deleted** — case migrated into `test_cost_report.py` (Task 2) |
| `tests/test_cost_report_harness_economics.sh` | **Modified** — `harness_economics` half removed (migrated, Task 4); `on_failure` half kept |

---

## Deviations from the spec's literal function signatures (verified necessary)

This refinement environment has live `bash`/`gh`/`docker`/`git`/`jq`/`bc`/`python3` — the
same execution capability the operator's 2026-07-22 comment said the *implementation*
environment would have but the *refinement* environment lacked. Rather than write the
`render()`/`render_comment()` tasks against hand-derived guesses, they were prototyped
and run against the current, unmodified `entrypoint.sh` (via a stub harness identical in
shape to `tests/test_entrypoint_cost_report_regression.sh`) before finalizing this plan.
Three real gaps surfaced that the spec's `Architecture / Approach` section didn't
anticipate — each is a **verified, necessary correction**, not a speculative addition:

1. **`render()` needs `intent` and `product_name` as explicit parameters, not fields
   read from `run_record`/left as a literal token.** `post_cost_report` derives the
   `(intent, status)` pair in the `### Run:` line from the bash `${INTENT:-fix}`
   *environment variable*, never from `run-record.json` — confirmed by running the
   harness with `INTENT=fix` against a `run-record.json` containing `"intent":
   "implement"`; the rendered line used `fix`, not `implement`. Separately, the footer
   `*Updated by ${FACTORY_PRODUCT_NAME} Dark Factory*` is inside a bash **double-quoted**
   string, so `${FACTORY_PRODUCT_NAME}` is expanded by bash at assignment time, before
   the value ever reaches a file or `gh` call. Once this line is produced by a Python
   subprocess and captured via `BODY=$(python3 ... cost-report-render ...)`, command
   substitution does **not** re-expand `$`-sequences in the captured text — so a
   `render()` that emits the literal token `${FACTORY_PRODUCT_NAME}` would regress the
   footer to literal, unexpanded text. Both are fixed by threading `intent: str` and
   `product_name: str = "Dark Factory"` as explicit `render()` parameters (Task 4),
   `--intent`/`--product-name` flags on `cost-report-render` (Task 5), and
   `--intent "${INTENT:-fix}"`/`--product-name "$FACTORY_PRODUCT_NAME"` arguments from
   `entrypoint.sh` (Task 6). The identical `${FACTORY_PRODUCT_NAME}` issue applies to
   `post_mortem.render_comment()`'s footer — same fix pattern, Tasks 7/8.

2. **The `context-budget.json` savings/budget line must be wired end-to-end, not just
   unit-tested in isolation.** `format_savings_block` (Task 2) is fully correct on its
   own, but nothing in the original Task 4/5/6 draft actually threaded a budget file
   from `entrypoint.sh` through `cost-report-render` into `render()` — the block would
   have silently disappeared from the real posted comment. Fixed by adding an optional
   `budget: dict | None = None` parameter to `render()`, a `--budget-file` flag on
   `cost-report-render`, and `entrypoint.sh` passing
   `--budget-file "${ARTIFACTS_DIR:-}/context-budget.json"` when that file exists
   (Tasks 4/5/6).

3. **Byte-exact reproduction of two more bash-specific numeric-formatting quirks**,
   found only by diffing prototype output against real captured bytes (not visible from
   reading the bash source alone):
   - `CUM_COST` is computed by piping two **decimal-string** operands through `bc`
     (`entrypoint.sh:482`), which (a) uses `max(scale of both operands)` decimal places
     — not a fixed count — and (b) drops the leading `0` before the decimal point for
     any result with magnitude < 1 (`echo "0 + 0.0207" | bc` → `.0207`, not `0.0207`).
     Plain Python float addition reproduces neither behavior. Fixed with a `_bc_add(a:
     str, b: str) -> str` helper (Task 3/4) that operates on `decimal.Decimal` and
     replicates both rules — verified against `bc` directly for whole-number,
     matching-scale, mismatched-scale, and sub-1 cases.
   - `TOTAL_COST` (used raw in the Subtotal row and as an addend into `CUM_COST`) is a
     **pass-through** jq field (`.totals.cost_usd // 0`, no arithmetic applied) — jq
     ≥1.7 preserves the original JSON literal's decimal text exactly for pass-through
     fields (`2.0` prints `"2.0"`, not `"2"`), which is the **opposite** convention from
     jq's *computed* values (e.g. `fmt_cost`'s `round(...)`, which always drops a
     trailing `.0`). Verified directly: `echo '{"x":2.0}' | jq -r '.x'` → `2.0`. Fixed
     with a distinct `_passthrough_num(value)` helper (Task 4) — kept separate from
     `_trim_decimal` (used only by the *computed* formatters in Task 1), since
     conflating the two would silently reintroduce the exact "unify the formatters"
     mistake the spec's Alternative #1 already rejected.

4. **`tests/test_431_telemetry_isolation.sh` cannot pass unmodified as literally
   written, and needs its `CLONE_DIR` setup (and matching cleanup) changed** — this is
   the architect review's blocking finding, verified for real (not just reasoned about)
   before landing this plan:
   `run_post_mortem`'s new delegation calls
   `python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" post-mortem-...`
   (Requirement 4's established, spec-mandated invocation pattern — used unchanged from
   every other `cli.py` call site in this file). `test_431_telemetry_isolation.sh:45`
   sets `CLONE_DIR=$(mktemp -d /tmp/521-clone-XXXXXX)` — an **empty** directory, with no
   `dark-factory/` subdirectory — because today's `run_post_mortem` never uses
   `$CLONE_DIR` at all (the JSONL write is pure bash). Confirmed by direct probe: with
   `CLONE_DIR` set exactly as the test sets it today, `"$CLONE_DIR/dark-factory/scripts/
   factory_core/cli.py"` does not exist, so the new `post-mortem-format` delegation
   (which performs the JSONL append) silently fails to run, and the test's
   "`factory-failures.jsonl` exists" / "exactly one JSONL line" assertions fail. No
   change to `entrypoint.sh`'s call convention avoids this — Requirement 4 explicitly
   forbids inventing new path-resolution indirection, and no fallback path (e.g.
   `/opt/dark-factory/...`) is available in a bare CI checkout either (confirmed by
   `test_entrypoint_cost_report_regression.sh`'s own header comment, which is why *that*
   test explicitly sets `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` overrides).

   The fix: `test_431_telemetry_isolation.sh` gets a minimal, behavior-preserving
   change — compute `REPO_ROOT` and set `CLONE_DIR="$(dirname "$REPO_ROOT")"` instead of
   an unrelated `mktemp -d`. This is **not a new idiom** — it is the exact pattern
   `tests/test_entrypoint_cost_report_regression.sh` and
   `tests/test_entrypoint_session_window.sh` already use for the identical purpose (that
   test's own comment: *"CLONE_DIR/dark-factory resolves to REPO_ROOT — see
   test_entrypoint_session_window.sh for why this holds both in this sandbox and under
   GitHub Actions' checkout layout"*).

   **This one assignment change is not sufficient on its own — a first pass at this plan
   missed a second, load-bearing consequence, caught only by a second architect review
   reading the actual file:** `test_431_telemetry_isolation.sh:101` cleans up with
   `rm -rf "$CLONE_DIR" "$ARTIFACTS_DIR"`. That line is safe today only because
   `CLONE_DIR` is a throwaway `mktemp -d` directory; once `CLONE_DIR` is repointed at
   `$(dirname "$REPO_ROOT")` — the repo checkout's **parent** directory — the same line
   would recursively delete the checkout's parent (the CI runner's workspace root, or a
   developer's whole projects folder) on every test run, silently, since the test's
   assertions all run *before* cleanup and would still report PASS. The precedent this
   fix is modeled on is safe *because* it never includes `CLONE_DIR` in its own cleanup
   (`test_entrypoint_cost_report_regression.sh:118`: `rm -rf "$SCHEDULER_STATE_DIR"
   "$ARTIFACTS_DIR"`, no `CLONE_DIR`) — that same discipline must be copied here, not
   just the assignment. Task 8 step 3's diff now includes all three hunks (the
   `REPO_ROOT` line, the `CLONE_DIR` assignment, and the cleanup-line fix) and documents
   this explicitly. No assertion, stub, or the test's documented behavioral contract
   (zero git ops, exactly one JSONL line, valid JSON, issue field match) changes.

   Verified end-to-end with a probe harness reproducing the test's exact stub set plus
   the proposed `run_post_mortem` delegation and the (now three-hunk) `CLONE_DIR` fix:
   all of the test's assertions pass, and the corrected cleanup line does not touch
   anything outside `$ARTIFACTS_DIR`. Task 8 makes this exact change and documents it
   inline. Not applying this fix would leave the hard-constraint test broken (or, if
   only the assignment were fixed without the cleanup line, silently destructive) — both
   strictly worse outcomes than the fully-corrected three-hunk change described here.

These four corrections are reflected directly in Tasks 3-8 below (not left as separate
follow-up items) — the task bodies you'll find further down already have them applied.

---

## Task 1: `cost_report.py` — token/duration/cost formatters + economics line

The current bash has **two independently-implemented, deliberately divergent** token
formatters that must both be reproduced exactly:

- The `jq` version (`entrypoint.sh:424-426`, used for per-node table cells) —
  round-half-away-from-zero, and jq's number-to-string **drops a trailing `.0`** for
  whole results (`"1M"`, not `"1.0M"`).
- The shell/`bc` version (`entrypoint.sh:490-499`, used for cumulative/subtotal/savings
  lines) — `bc scale=1` **truncates** (does not round) and **always** shows one decimal
  digit (`"1.0M"`, never `"1M"`).

Verified directly against `jq 1.8.1` and `bc` in this environment (not guessed):

```
$ echo "scale=1; 59451/1000" | bc          → 59.4   (truncates, not 59.5)
$ jq -n '(999|"\(. / 1000 * 10 | round / 10)K")'   → "1K"   (rounds, drops .0)
$ jq -n '(1000|"\(. / 1000 * 10 | round / 10)K")'  → "1K"   (whole → no decimal)
$ jq -n '(1500|"\(. / 1000 * 10 | round / 10)K")'  → "1.5K"
```

**Files:** `scripts/factory_core/cost_report.py` (new), `tests/test_cost_report.py` (new)

### TDD Steps

1. Write the failing test file:

```python
# tests/test_cost_report.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import cost_report as cr


# ---------------------------------------------------------------------------
# format_tokens_table (jq def fmt_tokens, entrypoint.sh:424-426 — per-node cells)
# ---------------------------------------------------------------------------

def test_format_tokens_table_sub_1000_is_raw_int():
    assert cr.format_tokens_table(0) == "0"
    assert cr.format_tokens_table(999) == "999"


def test_format_tokens_table_k_rounds_and_drops_trailing_zero():
    assert cr.format_tokens_table(1000) == "1K"        # whole → no ".0"
    assert cr.format_tokens_table(1500) == "1.5K"
    assert cr.format_tokens_table(999_500) == "999.5K"


def test_format_tokens_table_k_rounds_half_away_from_zero():
    # 59181 -> 59181/1000*10=591.81 -> round=592 -> /10=59.2 -> "59.2K"
    assert cr.format_tokens_table(59181) == "59.2K"


def test_format_tokens_table_m_threshold():
    assert cr.format_tokens_table(999_999) == "1000K"
    assert cr.format_tokens_table(1_000_000) == "1M"
    assert cr.format_tokens_table(2_000_000) == "2M"
    assert cr.format_tokens_table(1_500_000) == "1.5M"


# ---------------------------------------------------------------------------
# format_tokens_cumulative (shell/bc fmt_tokens, entrypoint.sh:490-499 — totals)
# ---------------------------------------------------------------------------

def test_format_tokens_cumulative_sub_1000_is_raw_int():
    assert cr.format_tokens_cumulative(0) == "0"
    assert cr.format_tokens_cumulative(999) == "999"


def test_format_tokens_cumulative_k_truncates_always_one_decimal():
    assert cr.format_tokens_cumulative(59451) == "59.4K"   # truncates, NOT 59.5K
    assert cr.format_tokens_cumulative(59999) == "59.9K"
    assert cr.format_tokens_cumulative(1000) == "1.0K"     # always shows ".0"


def test_format_tokens_cumulative_m_truncates():
    assert cr.format_tokens_cumulative(1_999_999) == "1.9M"
    assert cr.format_tokens_cumulative(1_000_000) == "1.0M"


def test_format_tokens_cumulative_diverges_from_table_on_same_input():
    # The exact "1K" vs "1.0K" divergence the spec calls out.
    n = 1000
    assert cr.format_tokens_table(n) == "1K"
    assert cr.format_tokens_cumulative(n) == "1.0K"


# ---------------------------------------------------------------------------
# format_duration (jq def fmt_dur, entrypoint.sh:427-429)
# ---------------------------------------------------------------------------

def test_format_duration_sub_1s_is_ms():
    assert cr.format_duration(24) == "24ms"
    assert cr.format_duration(999) == "999ms"


def test_format_duration_sub_60s_rounds_to_tenth_drops_trailing_zero():
    assert cr.format_duration(7800) == "7.8s"
    assert cr.format_duration(2000) == "2s"      # whole → no ".0"
    assert cr.format_duration(2500) == "2.5s"


def test_format_duration_60s_and_over_is_minutes_seconds():
    assert cr.format_duration(895_000) == "14m 55s"
    assert cr.format_duration(60_000) == "1m 0s"


# ---------------------------------------------------------------------------
# format_cost (jq def fmt_cost, entrypoint.sh:430 — per-node table cells only)
# ---------------------------------------------------------------------------

def test_format_cost_rounds_to_4dp_and_drops_trailing_zeros():
    assert cr.format_cost(0.020714) == "$0.0207"
    assert cr.format_cost(0.15) == "$0.15"
    assert cr.format_cost(1.0) == "$1"
    assert cr.format_cost(0) == "$0"


# ---------------------------------------------------------------------------
# format_economics_line (entrypoint.sh:409-418, absent-tolerant)
# ---------------------------------------------------------------------------

def test_format_economics_line_present():
    run_record = {
        "harness_economics": {
            "factory_cpm": 17.4,
            "outcome": {"state": "produced_ungated", "score": 1.0},
        }
    }
    assert cr.format_economics_line(run_record) == (
        "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)"
    )


def test_format_economics_line_absent_returns_empty_string():
    assert cr.format_economics_line({}) == ""
    assert cr.format_economics_line({"harness_economics": {}}) == ""
    assert cr.format_economics_line(
        {"harness_economics": {"outcome": {}}}
    ) == ""


def test_format_economics_line_missing_cpm_falls_back_to_na():
    run_record = {
        "harness_economics": {"outcome": {"state": "failed", "score": 0.0}}
    }
    assert cr.format_economics_line(run_record) == (
        "**Factory CPM:** n/a | **Outcome:** failed (score 0.0)"
    )
```

2. Verify it fails (module doesn't exist yet):

```bash
python -m pytest tests/test_cost_report.py -v
# ImportError: cannot import name 'cost_report' from 'factory_core' (or ModuleNotFoundError)
```

3. Implement `scripts/factory_core/cost_report.py`:

```python
"""Pure formatting/bookkeeping for the Dark Factory cost-report comment (#182).

Extracted from entrypoint.sh's post_cost_report(). No gh, no docker, no archon, no
network calls in this module — see cli.py's cost-report subcommands for the IO seam.
"""
import math


def _round_half_away_from_zero(x: float) -> int:
    """Matches jq's `round` (C round()), NOT Python's banker's-rounding builtin."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _trim_decimal(value: float) -> str:
    """jq's number-to-string: whole results print without a trailing '.0'."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def format_tokens_table(n: int) -> str:
    """Mirrors the jq `fmt_tokens` def used for per-node table cells (:424-426)."""
    if n >= 1_000_000:
        return f"{_trim_decimal(_round_half_away_from_zero(n / 1_000_000 * 10) / 10)}M"
    if n >= 1000:
        return f"{_trim_decimal(_round_half_away_from_zero(n / 1000 * 10) / 10)}K"
    return str(n)


def format_tokens_cumulative(n: int) -> str:
    """Mirrors the shell `fmt_tokens` bash function used for cumulative/subtotal/
    savings lines (:490-499) — `bc scale=1` truncates and always shows 1 decimal."""
    if n >= 1_000_000:
        tenths = (n * 10) // 1_000_000
        return f"{tenths // 10}.{tenths % 10}M"
    if n >= 1000:
        tenths = (n * 10) // 1000
        return f"{tenths // 10}.{tenths % 10}K"
    return str(n)


def format_duration(ms: int) -> str:
    """Mirrors the jq `fmt_dur` def (:427-429)."""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{_trim_decimal(_round_half_away_from_zero(ms / 100) / 10)}s"
    minutes = ms // 60_000
    seconds = _round_half_away_from_zero((ms % 60_000) / 1000)
    return f"{minutes}m {seconds}s"


def format_cost(usd) -> str:
    """Mirrors the jq `fmt_cost` def (:430) — per-node table cells only; the
    Subtotal/Total lines use the RAW pass-through value, not this formatter
    (see render()'s `_passthrough_num`, Task 4).

    NOTE: uses an integer-numerator formatter, not `_trim_decimal` (which is
    fixed at 1 decimal place for tokens/duration) — cost needs up to 4 decimal
    places with trailing zeros stripped, e.g. $0.0207, verified against real
    captured output in Task 4."""
    numerator = _round_half_away_from_zero(usd * 10000)
    sign = "-" if numerator < 0 else ""
    numerator = abs(numerator)
    whole, frac = divmod(numerator, 10000)
    if frac == 0:
        return f"{sign}${whole}"
    frac_str = f"{frac:04d}".rstrip("0")
    return f"{sign}${whole}.{frac_str}"


def _jq_alt(value, default):
    """jq's `//` alternative operator: only null/false trigger the fallback."""
    return default if value is None or value is False else value


def format_economics_line(run_record: dict) -> str:
    """Mirrors the harness_economics extraction at entrypoint.sh:409-418.

    Absent-tolerant: older run-record.json files predate harness_economics.
    """
    he = run_record.get("harness_economics") or {}
    outcome = he.get("outcome") or {}
    state = outcome.get("state")
    if not state:
        return ""
    cpm = he.get("factory_cpm")
    cpm_fmt = "n/a" if cpm is None else f"{_round_half_away_from_zero(cpm)}"
    score = _jq_alt(outcome.get("score"), "n/a")
    return f"**Factory CPM:** {cpm_fmt} | **Outcome:** {state} (score {score})"
```

4. Verify it passes:

```bash
python -m pytest tests/test_cost_report.py -v
# 17 passed
```

5. Commit:

```bash
git add scripts/factory_core/cost_report.py tests/test_cost_report.py
git commit -m "feat(cost-report): extract token/duration/cost/economics formatters into factory_core (#182)"
```

---

## Task 2: `cost_report.py` — `format_savings_block` + budget-line regression migration

Migrate `tests/test_budget_line_trim.sh` (the `estimated_input_tokens`-vs-`reserved_tokens`
regression) and `tests/test_cost_report_savings.sh` (the savings/fallbacks block) into
named `cost_report.py` tests **before** deleting the bash files, per the spec's Requirement
6 triage — real coverage must not be silently dropped.

**Files:** `scripts/factory_core/cost_report.py` (modified), `tests/test_cost_report.py`
(modified), `tests/test_budget_line_trim.sh` (deleted), `tests/test_cost_report_savings.sh`
(deleted)

### TDD Steps

1. Add failing tests to `tests/test_cost_report.py`:

```python
# ---------------------------------------------------------------------------
# format_savings_block (entrypoint.sh:501-548, schema v2, best-effort)
# ---------------------------------------------------------------------------

def test_format_savings_block_none_or_v1_returns_empty():
    assert cr.format_savings_block(None) == ""
    assert cr.format_savings_block({}) == ""
    assert cr.format_savings_block({"schema_version": 1}) == ""


def test_format_savings_block_savings_line():
    budget = {
        "schema_version": 2,
        "savings_tokens": 6000,
        "savings_pct": 30.0,
        "fallback_events": [],
    }
    block = cr.format_savings_block(budget)
    assert "**Context savings: 6.0K tokens (30.0%)**" in block


def test_format_savings_block_fallbacks_line():
    budget = {
        "schema_version": 2,
        "savings_tokens": 0,
        "savings_pct": 0,
        "fallback_events": [
            {"section": "architecture_md", "reason": "safety_keyword:performance"},
        ],
    }
    block = cr.format_savings_block(budget)
    assert (
        "**Fallbacks:** architecture_md: safety_keyword:performance" in block
    )


def test_format_savings_block_over_budget_branch():
    budget = {
        "schema_version": 2,
        "over_budget": True,
        "scenario": "implement",
        "reserved_tokens": 12000,
        "scenario_budget": 8000,
        "derived_caps": {"arch": 1500, "memory": 750},
    }
    block = cr.format_savings_block(budget)
    assert "⚠️ Over budget (implement): 12.0K reserved / 8.0K budget" in block
    assert "arch→1500, memory→750" in block


def test_format_savings_block_would_trim_uses_estimated_input_tokens_not_reserved():
    # Regression (df, migrated from test_budget_line_trim.sh): would_trim must
    # render estimated_input_tokens (10000), NOT reserved_tokens (9000).
    budget = {
        "schema_version": 2,
        "scenario": "conformance",
        "over_budget": False,
        "would_trim": True,
        "estimated_input_tokens": 10000,
        "reserved_tokens": 9000,
        "scenario_budget": 8000,
        "derived_caps": {"arch": 1500, "memory": 750},
    }
    block = cr.format_savings_block(budget)
    assert "est 10.0K" in block
    assert "9.0K" not in block


def test_format_savings_block_would_trim_falls_back_to_reserved_when_estimated_absent():
    budget = {
        "schema_version": 2,
        "scenario": "conformance",
        "would_trim": True,
        "reserved_tokens": 9000,
        "scenario_budget": 8000,
        "derived_caps": {},
    }
    block = cr.format_savings_block(budget)
    assert "rsv 9.0K" in block
```

2. Verify fail:

```bash
python -m pytest tests/test_cost_report.py -v -k savings_block
# AttributeError: module 'factory_core.cost_report' has no attribute 'format_savings_block'
```

3. Implement `format_savings_block` in `scripts/factory_core/cost_report.py`
   (reproducing `:501-548` exactly, using `format_tokens_cumulative` for the `bc`-style
   token counts — the bash's `fmt_tokens` calls in this block are the shell/bc version):

```python
def format_savings_block(budget: "dict | None") -> str:
    """Mirrors the context-budget.json (schema v2) block at entrypoint.sh:501-548."""
    if not budget:
        return ""
    schema_version = budget.get("schema_version", 1)
    if not isinstance(schema_version, int) or schema_version < 2:
        return ""

    lines = []

    savings_tokens = budget.get("savings_tokens", 0) or 0
    if savings_tokens > 0:
        savings_pct = budget.get("savings_pct", 0)
        lines.append(
            f"**Context savings: {format_tokens_cumulative(savings_tokens)} "
            f"tokens ({savings_pct}%)**"
        )

    fallback_events = budget.get("fallback_events") or []
    if fallback_events:
        parts = [f"{ev['section']}: {ev['reason']}" for ev in fallback_events]
        lines.append("**Fallbacks:** " + ", ".join(parts))

    over_budget = budget.get("over_budget")
    would_trim = budget.get("would_trim")
    caps_str = ", ".join(
        f"{k}→{v}" for k, v in (budget.get("derived_caps") or {}).items()
    )
    scenario = budget.get("scenario", "unknown")
    scenario_budget = budget.get("scenario_budget", 0)
    if over_budget is True:
        reserved = budget.get("reserved_tokens", 0)
        lines.append(
            f"**⚠️ Over budget ({scenario}): "
            f"{format_tokens_cumulative(reserved)} reserved / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"trimmed: {caps_str}**"
        )
    elif would_trim is True:
        estimated = budget.get("estimated_input_tokens")
        if estimated:
            label, value = "est", estimated
        else:
            label, value = "rsv", budget.get("reserved_tokens", 0)
        lines.append(
            f"**Budget trim ({scenario}): {label} "
            f"{format_tokens_cumulative(value)} / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"capped: {caps_str}**"
        )

    if not lines:
        return ""
    return "\n" + "\n".join(lines)
```

4. Verify pass:

```bash
python -m pytest tests/test_cost_report.py -v
```

5. Delete the now-migrated bash files and commit:

```bash
git rm tests/test_budget_line_trim.sh tests/test_cost_report_savings.sh
git add scripts/factory_core/cost_report.py tests/test_cost_report.py
git commit -m "feat(cost-report): extract savings/budget-line block, migrate budget_line_trim + savings bash tests (#182)"
```

---

## Task 3: `cost_report.py` — `check_renderable` + `format_missing_diagnostic` + `parse_prior_cumulative`

Requirement 1a's hard control-flow boundary: `check_renderable` is the **only** place
`.nodes` length is inspected. `format_missing_diagnostic` reproduces the exact stderr
string `tests/test_entrypoint_cost_report_regression.sh` greps for, byte-for-byte
(including the jq `//`-vs-`false` bug workaround already present at `entrypoint.sh:437-440`).

**Files:** `scripts/factory_core/cost_report.py` (modified), `tests/test_cost_report.py`
(modified)

### TDD Steps

1. Add failing tests:

```python
# ---------------------------------------------------------------------------
# check_renderable / format_missing_diagnostic (entrypoint.sh:435-458, Req 1a)
# ---------------------------------------------------------------------------

def test_check_renderable_none_when_nodes_present():
    assert cr.check_renderable({"nodes": [{"node_id": "refine"}]}) is None


def test_check_renderable_diagnostic_when_nodes_empty():
    run_record = {
        "nodes": [],
        "archon_cost_capture": {"ok": False, "exit_code": 127, "stderr_excerpt": "boom"},
    }
    diag = cr.check_renderable(run_record)
    assert diag == {
        "nodes_count": 0,
        "capture_ok": False,
        "capture_exit_code": 127,
        "capture_stderr": "boom",
    }


def test_check_renderable_diagnostic_when_nodes_absent():
    diag = cr.check_renderable({})
    assert diag["nodes_count"] == 0
    assert diag["capture_ok"] == "unknown"
    assert diag["capture_exit_code"] == "unknown"
    assert diag["capture_stderr"] == ""


def test_check_renderable_capture_ok_false_survives_jq_alt_bug():
    # Regression guard: jq's `.ok // "unknown"` would silently turn a real
    # `ok: false` into "unknown" (both are falsy in jq) — entrypoint.sh:437-440
    # works around this with `if has("ok") then .ok else "unknown" end`.
    run_record = {"nodes": [], "archon_cost_capture": {"ok": False}}
    diag = cr.check_renderable(run_record)
    assert diag["capture_ok"] is False   # NOT "unknown"


def test_format_missing_diagnostic_matches_bash_string_exactly():
    diag = {
        "nodes_count": 0,
        "capture_ok": False,
        "capture_exit_code": 127,
        "capture_stderr": "archon: command not found",
    }
    msg = cr.format_missing_diagnostic(diag, run_id="test-run-1", issue=300)
    assert msg == (
        "ERROR: cost report has zero node rows for run test-run-1 "
        "(issue #300); nodes=0, archon_cost_capture.ok=false, "
        "archon_cost_exit_code=127, stderr=archon: command not found"
    )


def test_format_missing_diagnostic_run_id_defaults_to_unknown():
    diag = {"nodes_count": 0, "capture_ok": "unknown", "capture_exit_code": "unknown",
             "capture_stderr": ""}
    msg = cr.format_missing_diagnostic(diag, run_id="", issue=300)
    assert "for run unknown " in msg


# ---------------------------------------------------------------------------
# parse_prior_cumulative (entrypoint.sh:466-478)
# ---------------------------------------------------------------------------

def test_parse_prior_cumulative_first_run_defaults():
    parsed = cr.parse_prior_cumulative("")
    assert parsed == {"prior_runs": "", "prev_cost": "0", "prev_in": 0, "prev_out": 0,
                       "run_count": 0}


def test_parse_prior_cumulative_extracts_marker_and_prior_runs():
    body = (
        "<!-- dark-factory-cost-report -->\n"
        "<!-- cumulative: cost=1.5 in=100 out=200 -->\n"
        "## Dark Factory — Cost Report\n\n"
        "**1 run(s) — Total: $1.5 (100 in / 200 out)**\n\n\n"
        "### Run: 2026-07-21 10:00 UTC (fix, completed)\n\n"
        "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
        "|------|-------|-----------|------------|------|----------|\n"
        "| refine |  | 100 | 200 | $1.5 | 5s |\n"
        "| **Subtotal** | | **100** | **200** | **$1.5** | |\n\n"
        "---\n"
        "*Updated by Dark Factory Dark Factory*"
    )
    parsed = cr.parse_prior_cumulative(body)
    # prev_cost stays a STRING (not float) — it feeds _bc_add (Task 4), which
    # needs the exact decimal text, matching bc's arbitrary-precision behavior.
    assert parsed["prev_cost"] == "1.5"
    assert parsed["prev_in"] == 100
    assert parsed["prev_out"] == 200
    assert parsed["run_count"] == 1
    assert parsed["prior_runs"] == (
        "### Run: 2026-07-21 10:00 UTC (fix, completed)\n\n"
        "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
        "|------|-------|-----------|------------|------|----------|\n"
        "| refine |  | 100 | 200 | $1.5 | 5s |\n"
        "| **Subtotal** | | **100** | **200** | **$1.5** | |"
    )
    # No trailing newline — mirrors bash's $(...) stripping ALL trailing
    # newlines from the sed/head-n--1 pipeline (verified against real bash;
    # see the "Deviations from the spec" note above item 3's sibling finding).
    assert not parsed["prior_runs"].endswith("\n")
```

2. Verify fail, then implement in `scripts/factory_core/cost_report.py`:

```python
import re


def check_renderable(run_record: dict) -> "dict | None":
    """Requirement 1a's guard — the ONLY place `.nodes` length is inspected.

    Returns None when there's something to render, else a diagnostic dict.
    """
    nodes = run_record.get("nodes") or []
    if nodes:
        return None
    capture = run_record.get("archon_cost_capture")
    capture = capture if isinstance(capture, dict) else {}
    capture_ok = capture["ok"] if "ok" in capture else "unknown"
    return {
        "nodes_count": len(nodes),
        "capture_ok": capture_ok,
        "capture_exit_code": _jq_alt(capture.get("exit_code"), "unknown"),
        "capture_stderr": _jq_alt(capture.get("stderr_excerpt"), ""),
    }


def _jqstr(value) -> str:
    """jq -r's raw text rendering: lowercase booleans, everything else via str()."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_missing_diagnostic(diagnostic: dict, run_id: str, issue: int) -> str:
    """Reproduces entrypoint.sh:445 byte-for-byte — the string the regression
    test greps for."""
    return (
        f"ERROR: cost report has zero node rows for run {run_id or 'unknown'} "
        f"(issue #{issue}); nodes={diagnostic['nodes_count']}, "
        f"archon_cost_capture.ok={_jqstr(diagnostic['capture_ok'])}, "
        f"archon_cost_exit_code={_jqstr(diagnostic['capture_exit_code'])}, "
        f"stderr={diagnostic['capture_stderr']}"
    )


_CUMULATIVE_MARKER_RE = re.compile(
    r"<!-- cumulative: cost=([0-9.]+) in=(\d+) out=(\d+) -->"
)


def parse_prior_cumulative(prior_comment_body: str) -> dict:
    """Reproduces the sed/grep -oP parsing at entrypoint.sh:474-477.

    The `gh api` fetch that produces prior_comment_body stays bash-side; this
    function only parses the already-fetched string.
    """
    if not prior_comment_body:
        return {"prior_runs": "", "prev_cost": "0", "prev_in": 0, "prev_out": 0,
                 "run_count": 0}

    lines = prior_comment_body.splitlines()
    prior_run_lines = []
    in_run_block = False
    for line in lines:
        if line.startswith("### Run:"):
            in_run_block = True
        if in_run_block:
            if line.strip() == "---":
                break
            prior_run_lines.append(line)
    # Mirrors bash's $(...) command substitution, which strips ALL trailing
    # newlines (not just one) from the sed '/^### Run:/,/^---$/p' | head -n -1
    # pipeline's output — verified against real bash (see "Deviations" above).
    prior_runs = "\n".join(prior_run_lines).rstrip("\n")

    match = _CUMULATIVE_MARKER_RE.search(prior_comment_body)
    if match:
        # prev_cost stays a STRING — it feeds _bc_add (render(), Task 4),
        # which needs the exact decimal text bc originally produced, not a
        # float (float arithmetic can diverge from bc's arbitrary-precision
        # decimal addition — verified case: bc's "0 + 0.0207" -> ".0207").
        prev_cost, prev_in, prev_out = match.group(1), int(match.group(2)), int(match.group(3))
    else:
        prev_cost, prev_in, prev_out = "0", 0, 0

    run_count = prior_comment_body.count("### Run:")

    return {
        "prior_runs": prior_runs,
        "prev_cost": prev_cost,
        "prev_in": prev_in,
        "prev_out": prev_out,
        "run_count": run_count,
    }
```

3. Verify pass:

```bash
python -m pytest tests/test_cost_report.py -v
```

4. Commit:

```bash
git add scripts/factory_core/cost_report.py tests/test_cost_report.py
git commit -m "feat(cost-report): extract zero-rows guard and prior-comment parsing (#182)"
```

---

## Task 4: `cost_report.py` — `render()` + execution-captured golden tests

Per the operator's 2026-07-22 approval comment on #182, discharge the spec's
`[ASSUMPTION]` about hand-derived goldens: **execute the current, pre-refactor bash**
(reusing `test_entrypoint_cost_report_regression.sh`'s exact stubbing pattern) to
capture real `post_cost_report` output, rather than hand-deriving golden strings from
reading the bash. This must happen **before** `entrypoint.sh` is touched (Task 6), so
the goldens reflect today's real behavior, not the refactor's.

**Files:** `scripts/factory_core/cost_report.py` (modified), `tests/test_cost_report.py`
(modified), `tests/test_cost_report_harness_economics.sh` (modified — `harness_economics`
half removed, ported into `test_cost_report.py`)

### TDD Steps

1. Write a one-time, throwaway capture harness (NOT committed — deleted after use) to
   produce real golden bytes from the current bash, one scenario at a time:

```bash
cat > /tmp/capture_cost_report_golden.sh <<'CAPTURE_EOF'
#!/usr/bin/env bash
# One-time capture harness — run once per scenario, copy stdout into
# tests/test_cost_report.py, then delete this file. Mirrors
# tests/test_entrypoint_cost_report_regression.sh's exact stub pattern.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="/workspace/dark-factory"
export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export FACTORY_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

CAPTURED_BODY_FILE=$(mktemp /tmp/captured-body-XXXXXX.md)
: "${GH_COMMENT_LOOKUP_RESPONSE:=}"     # set before sourcing: "" (first-run) or an id
: "${GH_EXISTING_BODY:=}"               # set before sourcing: prior comment body text

git() { return 0; }
export -f git
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude
archon() { echo "{}"; return 0; }
export -f archon
gh() {
  if [ "$1" = "api" ]; then
    local path="$2"
    if [[ "$path" == */comments && "$*" == *"--jq"* ]]; then
      echo "$GH_COMMENT_LOOKUP_RESPONSE"
    elif [[ "$path" == */comments/* && "$*" == *"--method"* ]]; then
      for a in "$@"; do
        case "$a" in body=@*) cp "${a#body=@}" "$CAPTURED_BODY_FILE" ;; esac
      done
      echo "stub-patched"
    elif [[ "$path" == */comments/* ]]; then
      echo "$GH_EXISTING_BODY"
    fi
    return 0
  fi
  if [ "$1" = "issue" ] && [ "$2" = "comment" ]; then
    local i=1
    while [ $i -le $# ]; do
      if [ "${!i}" = "--body-file" ]; then
        local j=$((i+1)); cp "${!j}" "$CAPTURED_BODY_FILE"
      fi
      i=$((i+1))
    done
    echo "stub-created"
    return 0
  fi
  return 0
}
export -f gh

ENTRYPOINT_SOURCE_ONLY=1 source "$REPO_ROOT/entrypoint.sh"
trap - ERR; set +e; set +u; set +o pipefail

CLONE_DIR="$(dirname "$REPO_ROOT")"
ARTIFACTS_DIR="${RUN_RECORD_DIR}"
ISSUE_NUM=182
INTENT=fix
RUN_ID=golden-capture-1

post_cost_report >/dev/null 2>&1
cat "$CAPTURED_BODY_FILE"
rm -f "$CAPTURED_BODY_FILE"
CAPTURE_EOF
chmod +x /tmp/capture_cost_report_golden.sh
```

2. This harness was already run against the current, unmodified `entrypoint.sh` while
   writing this plan (verifying the operator's execution-captured-goldens directive is
   satisfiable in this environment, and pinning the exact bytes before finalizing the
   task). Re-running it is optional verification, not required to proceed — the goldens
   below are the real, captured bytes. If re-running: write each scenario's
   `run-record.json` (and optional `context-budget.json`) to a scratch `RUN_RECORD_DIR`,
   set `GH_COMMENT_LOOKUP_RESPONSE`/`GH_EXISTING_BODY`, and run the harness. The five
   fixtures used, for reference:

   - **Scenario A** (multi-node, first run, `harness_economics` present): one node
     `parse-intent`, `totals.cost_usd=0.0207`, `harness_economics.outcome.state=
     "produced_ungated"`, `GH_EXISTING_BODY=""`.
   - **Scenario B** (prior-comment/cumulative bookkeeping): two nodes `plan`/`review`
     with distinct models, `GH_EXISTING_BODY` = Scenario A's exact captured golden body
     (a real prior comment, not synthetic), `GH_COMMENT_LOOKUP_RESPONSE="98765"`.
   - **Scenario C** (`harness_economics` absent): one node `implement`, no
     `harness_economics` key at all.
   - **Scenario D** (`context-budget.json`, `over_budget: true`): one node
     `implement`, budget file with `"over_budget": true, "reserved_tokens": 12000,
     "scenario_budget": 8000, "derived_caps": {"arch": 1500, "memory": 750}`.
   - **Scenario E** (`context-budget.json`, `would_trim: true`, plus savings +
     fallbacks): one node `conformance`, budget file with `"would_trim": true,
     "estimated_input_tokens": 10000, "reserved_tokens": 9000, "savings_tokens": 3000,
     "savings_pct": 15.5, "fallback_events": [{"section": "architecture_md", "reason":
     "safety_keyword:performance"}]`.

3. Write the failing golden tests with the real captured bytes inlined:

```python
# ---------------------------------------------------------------------------
# render() — execution-captured goldens (captured from pre-refactor entrypoint.sh
# via the harness above, per the #182 operator's 2026-07-22 guidance — NOT
# hand-derived). Verified byte-for-byte against real bash output.
# ---------------------------------------------------------------------------

GOLDEN_A_MULTI_NODE_FIRST_RUN = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.0207 in=20 out=270 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.0207 (20 in / 270 out)**\n\n\n"
    "### Run: 2026-07-22 13:02 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |\n"
    "| **Subtotal** | | **20** | **270** | **$0.0207** | |\n\n"
    "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_A = {
    "status": "completed",
    "totals": {"cost_usd": 0.0207, "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 270},
    "nodes": [
        {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 270, "cost_usd": 0.0207, "duration_ms": 7800},
    ],
    "harness_economics": {"factory_cpm": 17.4, "outcome": {"state": "produced_ungated", "score": 1.0}},
    "archon_cost_capture": {"ok": True},
}


def test_render_matches_golden_a_multi_node_first_run():
    body = cr.render(RUN_RECORD_A, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                      intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN


GOLDEN_B_PRIOR_COMMENT_CUMULATIVE = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=1.2707 in=520 out=12270 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**2 run(s) — Total: $1.2707 (520 in / 12.2K out)**\n\n"
    "### Run: 2026-07-22 13:02 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |\n"
    "| **Subtotal** | | **20** | **270** | **$0.0207** | |\n\n"
    "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)\n"
    "### Run: 2026-07-22 13:03 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| plan | claude-opus-4-8 | 300 | 8K | $0.9 | 45s |\n"
    "| review | claude-sonnet-5 | 200 | 4K | $0.35 | 12s |\n"
    "| **Subtotal** | | **500** | **12.0K** | **$1.25** | |\n\n"
    "**Factory CPM:** 23 | **Outcome:** produced_ungated (score 0.95)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_B = {
    "status": "completed",
    "totals": {"cost_usd": 1.25, "gen_ai.usage.input_tokens": 500, "gen_ai.usage.output_tokens": 12000},
    "nodes": [
        {"node_id": "plan", "model": "claude-opus-4-8", "gen_ai.usage.input_tokens": 300,
         "gen_ai.usage.output_tokens": 8000, "cost_usd": 0.9, "duration_ms": 45000},
        {"node_id": "review", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 200,
         "gen_ai.usage.output_tokens": 4000, "cost_usd": 0.35, "duration_ms": 12000},
    ],
    "harness_economics": {"factory_cpm": 22.9, "outcome": {"state": "produced_ungated", "score": 0.95}},
    "archon_cost_capture": {"ok": True},
}


def test_render_matches_golden_b_prior_comment_cumulative():
    body = cr.render(RUN_RECORD_B, prior_comment_body=GOLDEN_A_MULTI_NODE_FIRST_RUN,
                      timestamp="2026-07-22 13:03 UTC", intent="fix")
    assert body == GOLDEN_B_PRIOR_COMMENT_CUMULATIVE


GOLDEN_C_ECONOMICS_ABSENT = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.5 in=100 out=2000 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.5 (100 in / 2.0K out)**\n\n\n"
    "### Run: 2026-07-22 13:04 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| implement | claude-sonnet-5 | 100 | 2K | $0.5 | 30s |\n"
    "| **Subtotal** | | **100** | **2.0K** | **$0.5** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_C = {
    "status": "completed",
    "totals": {"cost_usd": 0.5, "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 2000},
    "nodes": [
        {"node_id": "implement", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 100,
         "gen_ai.usage.output_tokens": 2000, "cost_usd": 0.5, "duration_ms": 30000},
    ],
    "archon_cost_capture": {"ok": True},
    # NOTE: no "harness_economics" key at all — the absent-tolerant case.
}


def test_render_matches_golden_c_economics_absent():
    body = cr.render(RUN_RECORD_C, prior_comment_body="", timestamp="2026-07-22 13:04 UTC",
                      intent="fix")
    assert body == GOLDEN_C_ECONOMICS_ABSENT


GOLDEN_D_OVER_BUDGET = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=2.0 in=5000 out=9000 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $2.0 (5.0K in / 9.0K out)**\n\n\n"
    "### Run: 2026-07-22 13:04 UTC (fix, completed)\n\n"
    "**⚠️ Over budget (implement): 12.0K reserved / 8.0K budget — trimmed: arch→1500, memory→750**\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| implement | claude-sonnet-5 | 5K | 9K | $2 | 1m 0s |\n"
    "| **Subtotal** | | **5.0K** | **9.0K** | **$2.0** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_D = {
    "status": "completed",
    "totals": {"cost_usd": 2.0, "gen_ai.usage.input_tokens": 5000, "gen_ai.usage.output_tokens": 9000},
    "nodes": [
        {"node_id": "implement", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 5000,
         "gen_ai.usage.output_tokens": 9000, "cost_usd": 2.0, "duration_ms": 60000},
    ],
    "archon_cost_capture": {"ok": True},
}

BUDGET_D = {
    "schema_version": 2,
    "over_budget": True,
    "scenario": "implement",
    "reserved_tokens": 12000,
    "scenario_budget": 8000,
    "derived_caps": {"arch": 1500, "memory": 750},
}


def test_render_matches_golden_d_over_budget():
    body = cr.render(RUN_RECORD_D, prior_comment_body="", timestamp="2026-07-22 13:04 UTC",
                      intent="fix", budget=BUDGET_D)
    assert body == GOLDEN_D_OVER_BUDGET


GOLDEN_E_WOULD_TRIM = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.3 in=800 out=1500 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.3 (800 in / 1.5K out)**\n\n\n"
    "### Run: 2026-07-22 13:05 UTC (fix, completed)\n\n"
    "**Context savings: 3.0K tokens (15.5%)**\n"
    "**Fallbacks:** architecture_md: safety_keyword:performance\n"
    "**Budget trim (conformance): est 10.0K / 8.0K budget — capped: arch→1500, memory→750**\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| conformance | claude-opus-4-8 | 800 | 1.5K | $0.3 | 9s |\n"
    "| **Subtotal** | | **800** | **1.5K** | **$0.3** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_E = {
    "status": "completed",
    "totals": {"cost_usd": 0.3, "gen_ai.usage.input_tokens": 800, "gen_ai.usage.output_tokens": 1500},
    "nodes": [
        {"node_id": "conformance", "model": "claude-opus-4-8", "gen_ai.usage.input_tokens": 800,
         "gen_ai.usage.output_tokens": 1500, "cost_usd": 0.3, "duration_ms": 9000},
    ],
    "archon_cost_capture": {"ok": True},
}

BUDGET_E = {
    "schema_version": 2,
    "scenario": "conformance",
    "over_budget": False,
    "would_trim": True,
    "estimated_input_tokens": 10000,
    "reserved_tokens": 9000,
    "scenario_budget": 8000,
    "derived_caps": {"arch": 1500, "memory": 750},
    "savings_tokens": 3000,
    "savings_pct": 15.5,
    "fallback_events": [{"section": "architecture_md", "reason": "safety_keyword:performance"}],
}


def test_render_matches_golden_e_would_trim():
    body = cr.render(RUN_RECORD_E, prior_comment_body="", timestamp="2026-07-22 13:05 UTC",
                      intent="fix", budget=BUDGET_E)
    assert body == GOLDEN_E_WOULD_TRIM
```

4. Verify fail (no `render()` yet), then implement in `scripts/factory_core/cost_report.py`,
   reproducing `entrypoint.sh:420-577` exactly. `intent` and `product_name` are explicit
   parameters (not read from `run_record`/left as a literal token — see "Deviations from
   the spec" item 1 above); `budget` is an explicit optional parameter (item 2); the two
   numeric-formatting helpers below (`_bc_add`, `_passthrough_num`) exist because plain
   Python arithmetic/`str()` does not reproduce `bc`'s / jq's exact output (item 3):

```python
from decimal import Decimal


def _decimal_scale(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def _bc_add(a: str, b: str) -> str:
    """Reproduces bc's default-scale decimal addition (entrypoint.sh:482):
    result scale = max(scale of the two operands); a leading '0' before the
    decimal point is dropped for magnitude < 1 — verified directly against
    `bc` for whole-number, matching-scale, mismatched-scale, and sub-1 cases."""
    scale = max(_decimal_scale(a), _decimal_scale(b))
    total = Decimal(a) + Decimal(b)
    quant = Decimal(1).scaleb(-scale) if scale > 0 else Decimal(1)
    total = total.quantize(quant)
    s = format(total, "f")
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def _passthrough_num(value) -> str:
    """jq -r on a field that passes through UNMODIFIED (no arithmetic applied,
    e.g. `.totals.cost_usd // 0`) preserves the original JSON decimal literal
    text exactly (jq >=1.7's decimal-literal preservation) -- a JSON `2.0`
    prints "2.0", not "2". This is the OPPOSITE convention from a *computed*
    value (e.g. fmt_cost's round(...)), which always drops a trailing ".0" —
    keep this separate from _trim_decimal, don't unify them. Verified:
    `echo '{"x":2.0}' | jq -r '.x'` -> "2.0". Python's json.loads preserves
    the int-vs-float distinction from the source text, and float repr()
    reproduces the shortest round-tripping decimal, which matches jq's
    preserved text for the values exercised here."""
    if isinstance(value, int):
        return str(value)
    return repr(value)


COST_MARKER = "<!-- dark-factory-cost-report -->"


def render(run_record: dict, prior_comment_body: str, timestamp: str, intent: str,
           product_name: str = "Dark Factory", budget: "dict | None" = None) -> str:
    """Top-level entry point — assembles the full comment body.

    Callers must have already confirmed `check_renderable(run_record) is None`;
    this function does not itself special-case empty `nodes`. `intent` and
    `budget` are read from the bash caller's environment/`context-budget.json`
    respectively, not from `run_record` — see "Deviations from the spec" above.
    """
    status = run_record.get("status") or "completed"
    totals = run_record.get("totals") or {}
    total_cost_raw = totals.get("cost_usd")
    if total_cost_raw is None:
        total_cost_raw = 0
    total_cost_str = _passthrough_num(total_cost_raw)
    total_in = totals.get("gen_ai.usage.input_tokens") or 0
    total_out = totals.get("gen_ai.usage.output_tokens") or 0

    economics_line = format_economics_line(run_record)
    economics_segment = f"\n{economics_line}" if economics_line else ""

    row_lines = []
    for node in run_record.get("nodes") or []:
        row_lines.append(
            f"| {node.get('node_id', '')} | {node.get('model') or ''} | "
            f"{format_tokens_table(node.get('gen_ai.usage.input_tokens', 0) or 0)} | "
            f"{format_tokens_table(node.get('gen_ai.usage.output_tokens', 0) or 0)} | "
            f"{format_cost(node.get('cost_usd', 0) or 0)} | "
            f"{format_duration(node.get('duration_ms', 0) or 0)} |"
        )
    run_rows = "\n".join(row_lines)

    prior = parse_prior_cumulative(prior_comment_body)
    cum_cost = _bc_add(prior["prev_cost"], total_cost_str)
    cum_in = prior["prev_in"] + total_in
    cum_out = prior["prev_out"] + total_out
    run_count = prior["run_count"] + 1

    savings_block = format_savings_block(budget)

    # NOTE: `{prior['prior_runs']}` and `{savings_block}` are each on their OWN
    # source line here, matching entrypoint.sh's heredoc structure exactly —
    # this is load-bearing for blank-line placement (verified against real
    # captured output; see the golden tests above and "Deviations" item 3).
    body = f"""{COST_MARKER}
<!-- cumulative: cost={cum_cost} in={cum_in} out={cum_out} -->
## Dark Factory — Cost Report

**{run_count} run(s) — Total: ${cum_cost} ({format_tokens_cumulative(cum_in)} in / {format_tokens_cumulative(cum_out)} out)**

{prior['prior_runs']}
### Run: {timestamp} ({intent}, {status})
{savings_block}
| Step | Model | In tokens | Out tokens | Cost | Duration |
|------|-------|-----------|------------|------|----------|
{run_rows}
| **Subtotal** | | **{format_tokens_cumulative(total_in)}** | **{format_tokens_cumulative(total_out)}** | **${total_cost_str}** | |
{economics_segment}

---
*Updated by {product_name} Dark Factory*"""
    return body
```

   This exact implementation was run against all five golden fixtures above
   (`RUN_RECORD_A`..`E`, `BUDGET_D`/`E`) and produces byte-identical output —
   confirmed during plan authoring, not left for the implementer to discover.

5. Run the golden tests and confirm all pass:

```bash
python -m pytest tests/test_cost_report.py -v
```

6. Migrate the `harness_economics`-rendering half of
   `tests/test_cost_report_harness_economics.sh` into a named test (the absent-tolerant
   `//`-fallback case is already covered by `test_format_economics_line_absent_returns_empty_string`
   in Task 1 — confirm it, don't duplicate), then trim that bash file to just its
   `on_failure`/`--status failed` assertion:

```bash
# tests/test_cost_report_harness_economics.sh — remove the POST_COST_REPORT_BODY
# block and its two harness_economics assertions (lines matching
# 'POST_COST_REPORT_BODY=' through the second harness_economics FAIL/PASS block);
# keep only the ON_FAILURE_BODY extraction and its single assertion.
```

7. Commit:

```bash
git add scripts/factory_core/cost_report.py tests/test_cost_report.py \
        tests/test_cost_report_harness_economics.sh
git commit -m "feat(cost-report): extract render(), execution-captured golden tests (#182)"
rm -f /tmp/capture_cost_report_golden.sh
```

---

## Task 5: `run_record.py` — `emit_health_event()`; `cli.py` — `cost-report check`/`render`

**Files:** `scripts/factory_core/run_record.py` (modified), `scripts/factory_core/cli.py`
(modified), `tests/test_run_record.py` (modified)

### TDD Steps

1. Add a failing test to `tests/test_run_record.py`:

```python
def test_emit_health_event_posts_seq_payload(monkeypatch):
    posted = {}
    monkeypatch.setattr(rr, "_post_seq_raw", lambda payload: posted.update(payload=payload))

    rr.emit_health_event(
        "factory.cost_report.missing", issue=300, run_id="run-1",
        detail={"nodes_count": "0", "archon_cost_capture_ok": "false"},
    )

    event = posted["payload"]["Events"][0]
    assert event["Properties"]["Event"] == "factory.cost_report.missing"
    assert event["Properties"]["IssueNumber"] == 300
    assert event["Properties"]["RunId"] == "run-1"
    assert event["Properties"]["nodes_count"] == "0"


def test_emit_health_event_swallows_post_exceptions(monkeypatch):
    def _boom(payload):
        raise RuntimeError("network down")
    monkeypatch.setattr(rr, "_post_seq_raw", _boom)
    rr.emit_health_event("factory.cost_report.missing", issue=1, run_id="r", detail={})  # no raise


def test_cmd_health_event_still_works_unchanged(monkeypatch):
    posted = {}
    monkeypatch.setattr(rr, "_post_seq_raw", lambda payload: posted.update(payload=payload))

    class _Args:
        event = "factory.cost_report.missing"
        issue = 42
        run_id = "r1"
        detail = ["nodes_count=0"]

    rr.cmd_health_event(_Args())
    assert posted["payload"]["Events"][0]["Properties"]["Event"] == "factory.cost_report.missing"
```

2. Verify fail (`emit_health_event` doesn't exist), then refactor
   `scripts/factory_core/run_record.py`'s `cmd_health_event` (currently `:147-177`):

```python
def emit_health_event(event: str, issue: int, run_id: str, detail: dict) -> None:
    """Non-blocking recurrence-detection signal (df#300), callable in-process.

    Used by both the `run-record health-event` CLI subcommand (cmd_health_event,
    unchanged behavior) and cli.py's `cost-report check` handler (#182).
    """
    payload = {
        "Events": [
            {
                "Timestamp": _timestamp(),
                "Level": "Warning",
                "MessageTemplate": "{Event} issue=#{IssueNumber} run={RunId}",
                "Properties": {
                    "Event": event,
                    "IssueNumber": issue,
                    "RunId": run_id,
                    **detail,
                },
            }
        ]
    }
    try:
        _post_seq_raw(payload)
    except Exception:
        pass  # non-fatal: this is best-effort observability, not a gate


def cmd_health_event(args) -> None:
    """Lightweight, non-blocking recurrence-detection signal (df#300).

    Distinct from cmd_record's per-stage events: this is a named incident signal
    (e.g. 'factory.cost_report.missing'), not a stage verdict.
    """
    details: dict = {}
    for kv in args.detail or []:
        k, _, v = kv.partition("=")
        details[k] = v
    emit_health_event(args.event, args.issue, args.run_id, details)
```

3. Verify pass:

```bash
python -m pytest tests/test_run_record.py -v -k health_event
```

4. Add the `cost-report check`/`cost-report render` subcommands to
   `scripts/factory_core/cli.py`, following the explicit-flag style of `session-window-check`:

```python
def _cost_report_check(args):
    import json
    from pathlib import Path
    from factory_core import cost_report, run_record
    run_record_data = json.loads(Path(args.run_record_file).read_text())
    diagnostic = cost_report.check_renderable(run_record_data)
    if diagnostic is None:
        return
    msg = cost_report.format_missing_diagnostic(diagnostic, args.run_id, args.issue)
    print(msg, file=sys.stderr)
    if cost_report._jqstr(diagnostic["capture_ok"]) != "true":
        run_record.emit_health_event(
            "factory.cost_report.missing", args.issue, args.run_id,
            {
                "nodes_count": str(diagnostic["nodes_count"]),
                "archon_cost_capture_ok": cost_report._jqstr(diagnostic["capture_ok"]),
                "archon_cost_exit_code": cost_report._jqstr(diagnostic["capture_exit_code"]),
            },
        )
    sys.exit(3)


def _cost_report_render(args):
    import json
    from pathlib import Path
    from factory_core import cost_report
    run_record_data = json.loads(Path(args.run_record_file).read_text())
    prior_body = ""
    if args.prior_body_file:
        prior_path = Path(args.prior_body_file)
        prior_body = prior_path.read_text() if prior_path.exists() else ""
    budget = None
    if args.budget_file:
        budget_path = Path(args.budget_file)
        if budget_path.exists():
            budget = json.loads(budget_path.read_text())
    print(cost_report.render(run_record_data, prior_body, args.timestamp, args.intent,
                              product_name=args.product_name, budget=budget))
```

   Register both in `main()`. `--intent` and `--product-name` are required threading
   for the two verified gaps in "Deviations from the spec" above (item 1); `--budget-file`
   is the threading for item 2:

```python
    crc = sub.add_parser("cost-report-check")
    crc.add_argument("--run-record-file", required=True)
    crc.add_argument("--run-id", required=True)
    crc.add_argument("--issue", type=int, required=True)
    crc.set_defaults(func=_cost_report_check)

    crr = sub.add_parser("cost-report-render")
    crr.add_argument("--run-record-file", required=True)
    crr.add_argument("--prior-body-file", default="")
    crr.add_argument("--timestamp", required=True)
    crr.add_argument("--intent", required=True)
    crr.add_argument("--product-name", default="Dark Factory")
    crr.add_argument("--budget-file", default="")
    crr.set_defaults(func=_cost_report_render)
```

   (Spec Requirement 4 writes these as `cost-report check`/`cost-report render` — argparse
   subparser names can't contain a space, so use hyphenated single-token names
   `cost-report-check`/`cost-report-render`, matching the existing single-token
   convention every other subcommand in this file already uses, e.g. `board-move`,
   `breaker-trip`, `session-window-check`.)

5. Add a quick sanity check (not a formal pytest — `cli.py`'s subcommands are exercised
   end-to-end by the bash regression tests in Task 6):

```bash
python3 scripts/factory_core/cli.py cost-report-check --help
python3 scripts/factory_core/cli.py cost-report-render --help
```

6. Commit:

```bash
git add scripts/factory_core/run_record.py scripts/factory_core/cli.py tests/test_run_record.py
git commit -m "feat(cost-report): extract emit_health_event, wire cost-report-check/render into cli.py (#182)"
```

---

## Task 6: `entrypoint.sh` — `post_cost_report()` delegation + test triage sign-off

**Files:** `entrypoint.sh` (modified), `tests/test_cost_report_endpoint.sh` (modified —
comment only)

### TDD Steps

1. Before editing, run the two hard-constraint tests to confirm current baseline green:

```bash
bash tests/test_entrypoint_cost_report_regression.sh   # expect: Results: N passed, 0 failed
bash tests/test_cost_report_endpoint.sh                # expect: OK
```

2. Replace `post_cost_report()` (`entrypoint.sh:395-594`) with a thin delegation. Keep
   the function name, the no-argument env-driven signature, and every `gh` call site
   exactly as today (only the formatting/bookkeeping logic moves out):

```bash
post_cost_report() {
  if [ -z "${ISSUE_NUM:-}" ]; then return; fi
  local RUN_RECORD_FILE="${ARTIFACTS_DIR:-}/run-record.json"
  if [ ! -f "$RUN_RECORD_FILE" ]; then return; fi

  echo "Posting cost report to issue #${ISSUE_NUM}..."

  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  if ! python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report-check \
      --run-record-file "$RUN_RECORD_FILE" \
      --run-id "${RUN_ID:-unknown}" \
      --issue "${ISSUE_NUM}"; then
    return
  fi

  # Find existing cost report comment by marker
  local COMMENT_ID
  COMMENT_ID=$(gh api "repos/${FACTORY_REPO_SLUG}/issues/${ISSUE_NUM}/comments" \
    --jq "[.[] | select(.body | contains(\"$COST_MARKER\"))] | last | .id // empty" 2>/dev/null || true)

  local PRIOR_BODY_FILE=""
  if [ -n "$COMMENT_ID" ]; then
    PRIOR_BODY_FILE=$(mktemp /tmp/prior-body-XXXXXX.md)
    # Single-comment endpoint omits the issue number: /issues/comments/{id}, NOT
    # /issues/{n}/comments/{id} (the latter 404s).
    gh api "repos/${FACTORY_REPO_SLUG}/issues/comments/${COMMENT_ID}" \
      --jq '.body' > "$PRIOR_BODY_FILE" 2>/dev/null || true
  fi

  local TIMESTAMP
  TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")

  # --intent/--product-name/--budget-file: see "Deviations from the spec" items
  # 1-2 above — render() cannot derive these from run-record.json alone.
  local BUDGET_FILE="${ARTIFACTS_DIR:-}/context-budget.json"
  local BUDGET_ARGS=()
  [ -f "$BUDGET_FILE" ] && BUDGET_ARGS=(--budget-file "$BUDGET_FILE")
  local PRIOR_ARGS=()
  [ -n "$PRIOR_BODY_FILE" ] && PRIOR_ARGS=(--prior-body-file "$PRIOR_BODY_FILE")

  local BODY
  BODY=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" cost-report-render \
    --run-record-file "$RUN_RECORD_FILE" \
    --timestamp "$TIMESTAMP" \
    --intent "${INTENT:-fix}" \
    --product-name "${FACTORY_PRODUCT_NAME:-Dark Factory}" \
    "${PRIOR_ARGS[@]}" "${BUDGET_ARGS[@]}")
  [ -n "$PRIOR_BODY_FILE" ] && rm -f "$PRIOR_BODY_FILE"

  # Create or update the comment
  local TMPFILE
  TMPFILE=$(mktemp /tmp/cost-report-XXXXXX.md)
  echo "$BODY" > "$TMPFILE"

  if [ -n "$COMMENT_ID" ]; then
    if ! gh api "repos/${FACTORY_REPO_SLUG}/issues/comments/${COMMENT_ID}" \
        --method PATCH -F "body=@${TMPFILE}" >/dev/null; then
      echo "WARNING: Could not update cost report comment ${COMMENT_ID}"
    fi
  else
    gh issue comment "$ISSUE_NUM" --body-file "$TMPFILE" 2>/dev/null \
      || echo "WARNING: Could not post cost report"
  fi
  rm -f "$TMPFILE"
}
```

   Remove the now-dead `fmt_tokens()` shell function (`:490-499`) — its only caller was
   inside the deleted body.

   This exact delegation (including the zero-rows short-circuit and the
   budget-file/prior-body optional-args pattern) was run against a probe reproducing
   both `tests/test_entrypoint_cost_report_regression.sh`'s zero-rows fixture (built via
   the real `cli.py run-record assemble`, exactly as that test does) and a
   `context-budget.json`-present happy path, while writing this plan: zero-rows gives
   RC=0 with zero `gh` calls and the exact diagnostic string on stderr; the happy path
   posts a comment via `gh issue comment` with the budget line, correct `intent`, and
   correctly-expanded `product_name` all present. The `if ! ...; then return; fi` guard
   correctly stops `post_cost_report` at RC=0 when `cost-report-check` exits 3 — no
   further adjustment needed.

3. Re-run the two hard-constraint tests — must still pass **unchanged**:

```bash
bash tests/test_entrypoint_cost_report_regression.sh
bash tests/test_cost_report_endpoint.sh
```

4. Update `tests/test_cost_report_endpoint.sh`'s stale header comment (assertions
   unchanged — the `gh api` calls it greps for are still bash-side):

```bash
# Behavioral testing of the full render path now lives in
# tests/test_cost_report.py (factory_core.cost_report unit + golden tests, #182).
# This file remains a static guard specifically for the gh endpoint bug below,
# which stays bash-side post-refactor.
```

   (Replace the old "Behavioral testing of post_cost_report is impractical..." comment
   at lines 12-13.)

5. Run the full suite:

```bash
python -m pytest tests/ -v
for f in tests/test_*.sh; do bash "$f" || echo "FAILED: $f"; done
```

6. Commit:

```bash
git add entrypoint.sh tests/test_cost_report_endpoint.sh
git commit -m "refactor(entrypoint): post_cost_report delegates to cost-report-check/render (#182)"
```

---

## Task 7: `post_mortem.py` — evidence gathering + formatting

**Files:** `scripts/factory_core/post_mortem.py` (new), `tests/test_post_mortem.py` (new)

### TDD Steps

1. Write the failing test file:

```python
# tests/test_post_mortem.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import post_mortem as pm


def _write_issue_json(run_dir: Path, resolved_number: int):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "issue.json").write_text(json.dumps({"resolved_number": resolved_number}))


def test_gather_evidence_finds_most_recent_matching_run_dir(tmp_path):
    base = tmp_path / "runs"
    old_dir = base / "run-old"
    new_dir = base / "run-new"
    _write_issue_json(old_dir, 182)
    _write_issue_json(new_dir, 182)
    (new_dir / "plan.md").write_text("plan content")
    import os, time
    os.utime(old_dir / "issue.json", (1000, 1000))
    os.utime(new_dir / "issue.json", (2000, 2000))

    evidence = pm.gather_evidence(str(base), issue_num=182, transcript_file=None)
    assert "plan content" in evidence["artifacts_context"]


def test_gather_evidence_no_matching_run_dir_returns_empty_context(tmp_path):
    evidence = pm.gather_evidence(str(tmp_path / "nope"), issue_num=999, transcript_file=None)
    assert evidence["artifacts_context"] == ""
    assert evidence["transcript_tail"] == ""


def test_gather_evidence_reads_transcript_tail(tmp_path):
    transcript = tmp_path / "t.log"
    transcript.write_text("\n".join(f"line{i}" for i in range(300)))
    evidence = pm.gather_evidence(str(tmp_path), issue_num=1, transcript_file=str(transcript))
    lines = evidence["transcript_tail"].splitlines()
    assert len(lines) == 200
    assert lines[-1] == "line299"


def test_gather_evidence_reads_only_known_artifact_files(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    _write_issue_json(run_dir, 5)
    (run_dir / "implementation.md").write_text("impl")
    (run_dir / "unrelated.md").write_text("should not appear")
    evidence = pm.gather_evidence(str(tmp_path / "runs"), issue_num=5, transcript_file=None)
    assert "impl" in evidence["artifacts_context"]
    assert "should not appear" not in evidence["artifacts_context"]


def test_build_prompt_includes_exit_code_and_transcript():
    evidence = {"transcript_tail": "some tail", "artifacts_context": ""}
    prompt = pm.build_prompt(exit_code=1, intent="fix", issue_num=42, evidence=evidence)
    assert "issue #42" in prompt
    assert "Exit code: 1" in prompt
    assert "some tail" in prompt


def test_build_prompt_no_transcript_placeholder():
    evidence = {"transcript_tail": "", "artifacts_context": ""}
    prompt = pm.build_prompt(exit_code=1, intent="fix", issue_num=1, evidence=evidence)
    assert "<no transcript available>" in prompt


def test_render_comment_shape():
    body = pm.render_comment(
        post_mortem_text="It broke because X.",
        exit_code=1, intent="fix", promoted_at="2026-07-22T12:00:00Z",
        product_name="Dark Factory",
    )
    assert body.startswith("<!-- df-post-mortem -->")
    assert "It broke because X." in body
    assert "**Exit code:** 1 | **Phase:** fix | **Timestamp:** 2026-07-22T12:00:00Z" in body
    assert body.endswith("*Posted by Dark Factory Dark Factory*")


def test_render_comment_product_name_is_a_parameter_not_a_literal_token():
    # Same class of bug as cost_report.render()'s footer (see "Deviations from
    # the spec" item 1) — entrypoint.sh's ${FACTORY_PRODUCT_NAME} is bash-side
    # expansion; a Python f-string literal token would regress to unexpanded
    # text once captured via $(...). MarketHawk is the *other* Dark Factory
    # instance's actual product name (see CLAUDE.md) — a concrete, meaningful
    # non-default value, not an arbitrary placeholder.
    body = pm.render_comment("text", 1, "fix", "2026-07-22T12:00:00Z", product_name="MarketHawk")
    assert "*Posted by MarketHawk Dark Factory*" in body
    assert "FACTORY_PRODUCT_NAME" not in body


def test_build_failure_record_truncates_excerpt_and_collapses_newlines():
    long_text = "a\nb\n" + ("x" * 600)
    record = pm.build_failure_record(
        issue_num=7, title="Some Title", intent="fix", exit_code=2,
        post_mortem_text=long_text, promoted_at="2026-07-22T12:00:00Z",
    )
    assert record["issue"] == 7
    assert record["title"] == "Some Title"
    assert len(record["postmortem"]) == 500
    assert "\n" not in record["postmortem"]


def test_build_failure_record_title_defaults_to_unknown():
    record = pm.build_failure_record(
        issue_num=7, title="", intent="fix", exit_code=1,
        post_mortem_text="x", promoted_at="2026-07-22T12:00:00Z",
    )
    assert record["title"] == "unknown"


def test_append_failure_record_writes_jsonl_line(tmp_path):
    record = {"issue": 1, "title": "t", "phase": "fix", "exit_code": 1,
              "postmortem": "x", "promoted_at": "2026-07-22T12:00:00Z"}
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    jsonl = tmp_path / "factory-failures.jsonl"
    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record


def test_append_failure_record_appends_not_overwrites(tmp_path):
    record = {"issue": 1, "title": "t", "phase": "fix", "exit_code": 1,
              "postmortem": "x", "promoted_at": "2026-07-22T12:00:00Z"}
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    lines = (tmp_path / "factory-failures.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
```

2. Verify fail:

```bash
python -m pytest tests/test_post_mortem.py -v
```

3. Implement `scripts/factory_core/post_mortem.py` (reproducing `entrypoint.sh:178-269`):

```python
"""Pure evidence-gathering/formatting for the Dark Factory post-mortem comment (#182).

Extracted from entrypoint.sh's run_post_mortem(). No gh, no claude/LLM calls in this
module — those stay bash-side and are passed in as plain string arguments.
"""
import json
from pathlib import Path

_ARTIFACT_FILES = ("implementation.md", "conformance.md", "review.md", "plan.md")
_MARKER = "<!-- df-post-mortem -->"


def _find_run_dir(artifacts_base: str, issue_num: int) -> "Path | None":
    base = Path(artifacts_base)
    if not base.is_dir():
        return None
    candidates = []
    for issue_json in base.glob("*/issue.json"):
        try:
            data = json.loads(issue_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("resolved_number") == issue_num:
            candidates.append(issue_json)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.parent


def gather_evidence(artifacts_base: str, issue_num: int, transcript_file: "str | None") -> dict:
    """Reproduces the run-dir discovery + transcript tail + artifact reads at
    entrypoint.sh:196-212."""
    transcript_tail = ""
    if transcript_file and Path(transcript_file).is_file():
        lines = Path(transcript_file).read_text(errors="replace").splitlines()
        transcript_tail = "\n".join(lines[-200:])

    artifacts_context = ""
    run_dir = _find_run_dir(artifacts_base, issue_num)
    if run_dir is not None:
        for name in _ARTIFACT_FILES:
            f = run_dir / name
            if f.is_file():
                content = "\n".join(f.read_text(errors="replace").splitlines()[:100])
                artifacts_context += f"\n\n=== {name} ===\n{content}"

    return {"transcript_tail": transcript_tail, "artifacts_context": artifacts_context}


def build_prompt(exit_code: int, intent: str, issue_num: int, evidence: dict) -> str:
    """Reproduces the prompt template at entrypoint.sh:214-228."""
    transcript_tail = evidence.get("transcript_tail") or "<no transcript available>"
    return f"""You are analyzing a failed dark factory run for issue #{issue_num}.
Exit code: {exit_code}
Intent: {intent}

Write a concise post-mortem paragraph (3-5 sentences) explaining:
1. What phase or step likely failed (based on the transcript tail)
2. The probable root cause
3. What the next run should do differently

Keep it factual and actionable. No markdown headers, just a plain paragraph.

=== Transcript tail (last 200 lines) ===
{transcript_tail}
{evidence.get('artifacts_context', '')}"""


def render_comment(post_mortem_text: str, exit_code: int, intent: str, promoted_at: str,
                    product_name: str = "Dark Factory") -> str:
    """Reproduces the comment body at entrypoint.sh:241-249.

    `product_name` is an explicit parameter, not a literal `${FACTORY_PRODUCT_NAME}`
    token — see "Deviations from the spec" item 1: bash expands that env var at
    the point BODY is assigned (a double-quoted string), before this text is ever
    captured by a `$(...)` Python subprocess call, which does not re-expand it."""
    return f"""{_MARKER}
## Dark Factory — Post-Mortem

{post_mortem_text}

**Exit code:** {exit_code} | **Phase:** {intent} | **Timestamp:** {promoted_at}

---
*Posted by {product_name} Dark Factory*"""


def build_failure_record(issue_num: int, title: str, intent: str, exit_code: int,
                          post_mortem_text: str, promoted_at: str) -> dict:
    """Reproduces the JSONL record shape + 500-char/newline-collapsed excerpt logic
    at entrypoint.sh:253-266."""
    excerpt = post_mortem_text[:500].replace("\n", " ")
    return {
        "issue": issue_num,
        "title": title or "unknown",
        "phase": intent,
        "exit_code": exit_code,
        "postmortem": excerpt,
        "promoted_at": promoted_at,
    }


def append_failure_record(record: dict, artifacts_dir: str) -> None:
    """Local JSONL append, mirroring run_record.py's _append_jsonl (create-parents
    behavior; no file locking — run_post_mortem fires at most once per failed run,
    see the spec's Assumptions)."""
    path = Path(artifacts_dir) / "factory-failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
```

4. Verify pass:

```bash
python -m pytest tests/test_post_mortem.py -v
```

5. Commit:

```bash
git add scripts/factory_core/post_mortem.py tests/test_post_mortem.py
git commit -m "feat(post-mortem): extract gather/format/JSONL logic into factory_core (#182)"
```

---

## Task 8: `cli.py` — `post-mortem` subcommands; `entrypoint.sh` — `run_post_mortem()` delegation

**Files:** `scripts/factory_core/cli.py` (modified), `entrypoint.sh` (modified)

### TDD Steps

1. Before editing `entrypoint.sh`, confirm baseline green:

```bash
bash tests/test_431_telemetry_isolation.sh   # expect: Results: N passed, 0 failed
```

2. Add `post-mortem-gather`/`post-mortem-format` subcommands to
   `scripts/factory_core/cli.py` (single-token names, same rationale as Task 5):

```python
def _post_mortem_gather(args):
    from factory_core import post_mortem
    evidence = post_mortem.gather_evidence(
        args.artifacts_base, args.issue, args.transcript_file or None
    )
    prompt = post_mortem.build_prompt(args.exit_code, args.intent, args.issue, evidence)
    print(prompt)


def _post_mortem_format(args):
    from pathlib import Path
    from factory_core import post_mortem
    text = Path(args.text_file).read_text(errors="replace") if args.text_file else ""
    comment_body = post_mortem.render_comment(text, args.exit_code, args.intent, args.promoted_at,
                                               product_name=args.product_name)
    record = post_mortem.build_failure_record(
        args.issue, args.title or "", args.intent, args.exit_code, text, args.promoted_at
    )
    post_mortem.append_failure_record(record, args.artifacts_dir)
    print(comment_body)
```

   Register in `main()`. `--product-name` is the same threading fix as
   `cost-report-render`'s (see "Deviations from the spec" item 1):

```python
    pmg = sub.add_parser("post-mortem-gather")
    pmg.add_argument("--artifacts-base", required=True)
    pmg.add_argument("--issue", type=int, required=True)
    pmg.add_argument("--transcript-file", default="")
    pmg.add_argument("--exit-code", type=int, required=True)
    pmg.add_argument("--intent", required=True)
    pmg.set_defaults(func=_post_mortem_gather)

    pmf = sub.add_parser("post-mortem-format")
    pmf.add_argument("--exit-code", type=int, required=True)
    pmf.add_argument("--intent", required=True)
    pmf.add_argument("--promoted-at", required=True)
    pmf.add_argument("--text-file", default="")
    pmf.add_argument("--issue", type=int, required=True)
    pmf.add_argument("--title", default="")
    pmf.add_argument("--artifacts-dir", required=True)
    pmf.add_argument("--product-name", default="Dark Factory")
    pmf.set_defaults(func=_post_mortem_format)
```

3. **Before touching `entrypoint.sh`**, apply the verified, minimal fix to
   `tests/test_431_telemetry_isolation.sh` that "Deviations from the spec" item 4 above
   covers — without it, step 5's re-run of this hard-constraint test will fail the
   moment `run_post_mortem` starts delegating to `cli.py`. This is **two** deliberate
   changes to an otherwise "keep unchanged" test file — the `CLONE_DIR` assignment
   itself, **and** its cleanup line, which must change together:

```diff
--- a/tests/test_431_telemetry_isolation.sh
+++ b/tests/test_431_telemetry_isolation.sh
@@
 SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
+REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
```
```diff
@@
-CLONE_DIR=$(mktemp -d /tmp/521-clone-XXXXXX)
+# CLONE_DIR/dark-factory resolves to REPO_ROOT — matches the established idiom
+# in test_entrypoint_cost_report_regression.sh / test_entrypoint_session_window.sh.
+# Needed post-#182: run_post_mortem's Python delegation now resolves cli.py
+# under $CLONE_DIR/dark-factory/, which an isolated mktemp dir cannot satisfy.
+CLONE_DIR="$(dirname "$REPO_ROOT")"
```
```diff
@@
-rm -f "$GIT_LOG"
-rm -rf "$CLONE_DIR" "$ARTIFACTS_DIR"
+rm -f "$GIT_LOG"
+# CLONE_DIR is now the repo checkout's PARENT directory (see above) — it must
+# NOT be rm -rf'd here (that used to be safe when it was a throwaway mktemp
+# dir, but would now delete the checkout's parent). Matches
+# test_entrypoint_cost_report_regression.sh:118, which never includes
+# CLONE_DIR in its cleanup for the identical reason.
+rm -rf "$ARTIFACTS_DIR"
```

   **This third hunk is not optional.** `tests/test_431_telemetry_isolation.sh:101` is
   today `rm -rf "$CLONE_DIR" "$ARTIFACTS_DIR"` — harmless while `CLONE_DIR` is an
   isolated `mktemp -d` throwaway, but changing only the assignment (the first two
   hunks) without also dropping `$CLONE_DIR` from this cleanup line turns it into
   `rm -rf "$(dirname "$REPO_ROOT")"` — recursively deleting the repo checkout's
   **parent directory** (the CI runner's workspace root, or a developer's whole
   projects folder) every time this test runs. The test's assertions (which run before
   cleanup) would still pass, silently masking the destructive side effect. Caught by
   an architect re-review of this plan, verified against the actual file
   (`tests/test_431_telemetry_isolation.sh:101`) before landing — do not drop this hunk
   when implementing.

   Verified end-to-end while writing this plan: with exactly these three hunks applied
   (and no other edit to the file), a probe harness reproducing this test's full stub
   set plus the Task 8 `run_post_mortem` delegation below passes all of its existing
   assertions (zero git operations, `factory-failures.jsonl` exists, exactly one JSONL
   line, valid JSON, issue field matches `ISSUE_NUM`), and the corrected cleanup line
   was confirmed not to touch anything outside `$ARTIFACTS_DIR`.

4. Replace `run_post_mortem()` (`entrypoint.sh:178-269`) with a thin delegation, keeping
   the function name, 2-arg signature, `claude`/`gh` calls exactly as today:

```bash
run_post_mortem() {
  local exit_code="${1:-1}"
  local transcript_file="${2:-}"

  case "${INTENT:-fix}" in
    refine|plan|deconflict) return 0 ;;
  esac

  [ -z "${ISSUE_NUM:-}" ] && return 0

  local ARTIFACTS_BASE_DIR="${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs"

  local prompt
  prompt=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" post-mortem-gather \
    --artifacts-base "$ARTIFACTS_BASE_DIR" \
    --issue "$ISSUE_NUM" \
    --transcript-file "$transcript_file" \
    --exit-code "$exit_code" \
    --intent "${INTENT:-fix}")

  local post_mortem_text
  post_mortem_text=$(echo "$prompt" | claude -p --model claude-haiku-4-5-20251001 2>/dev/null || true)

  if [ -z "$post_mortem_text" ]; then
    post_mortem_text="Post-mortem generation failed — no output from haiku agent. Exit code was ${exit_code}. Check the factory logs for details."
  fi

  local PROMOTED_AT TEXTFILE
  PROMOTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  TEXTFILE=$(mktemp /tmp/postmortem-text-XXXXXX)
  echo "$post_mortem_text" > "$TEXTFILE"

  local title_json title
  title_json=$(python3 /opt/dark-factory/scripts/factory_core/providers/cli.py \
    tracker get --id "${ISSUE_NUM}" --fields title 2>/dev/null || echo '{}')
  title=$(echo "$title_json" | jq -r '.title // ""')

  local comment_body
  comment_body=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" post-mortem-format \
    --exit-code "$exit_code" \
    --intent "${INTENT:-fix}" \
    --promoted-at "$PROMOTED_AT" \
    --text-file "$TEXTFILE" \
    --issue "$ISSUE_NUM" \
    --title "$title" \
    --artifacts-dir "${ARTIFACTS_DIR:-/tmp}" \
    --product-name "${FACTORY_PRODUCT_NAME:-Dark Factory}")
  rm -f "$TEXTFILE"

  post_or_update_comment "$DF_POST_MORTEM_MARKER" "$comment_body" || true
}
```

5. Re-run the hard-constraint test — must still pass, now with step 3's three-hunk
   `CLONE_DIR` fix applied (zero-git-operations assertion and exactly-one-JSONL-line
   assertion both still hold — this was verified during plan authoring; see step 3):

```bash
bash tests/test_431_telemetry_isolation.sh
```

   If it fails on the JSONL-line-count or field assertions, check that
   `append_failure_record`'s write path matches `ARTIFACTS_DIR` exactly (the test sets
   `ARTIFACTS_DIR` and expects `${ARTIFACTS_DIR}/factory-failures.jsonl`) and that
   `post-mortem-format`'s `--artifacts-dir` flag is passed `"${ARTIFACTS_DIR:-/tmp}"`
   (not empty) in this test's environment.

6. Run the full suite:

```bash
python -m pytest tests/ -v
for f in tests/test_*.sh; do bash "$f" || echo "FAILED: $f"; done
```

7. Commit:

```bash
git add scripts/factory_core/cli.py entrypoint.sh tests/test_431_telemetry_isolation.sh
git commit -m "refactor(entrypoint): run_post_mortem delegates to post-mortem-gather/format (#182)

test_431_telemetry_isolation.sh: point CLONE_DIR at the real repo (same idiom
test_entrypoint_cost_report_regression.sh already uses) so the new cli.py
delegation can resolve — an empty mktemp dir never mattered before this
function had any \$CLONE_DIR-based dependency. Also drops CLONE_DIR from the
cleanup rm -rf (it now points at the checkout's parent dir, not a throwaway
mktemp dir — leaving it in cleanup would delete the checkout's parent on
every run). No assertion changed."
```

---

## Task 9: Full-suite verification + requirements sign-off

**Files:** none (verification only, plus any last-mile fixes surfaced by the full run)

### Steps

1. Run everything CI runs:

```bash
python -m pytest tests/ -v
bash smoke_gate.sh
for f in tests/test_*.sh; do echo "=== $f ==="; bash "$f" || echo "FAILED: $f"; done
```

2. Confirm the six-file test triage from the spec's Requirement 6 landed exactly as
   specified:

   | File | Expected end state |
   |---|---|
   | `tests/test_431_telemetry_isolation.sh` | `CLONE_DIR` setup + matching cleanup line changed (Task 8 step 3, verified necessary — see "Deviations from the spec"), all assertions unchanged, passing |
   | `tests/test_entrypoint_cost_report_regression.sh` | Unchanged, passing |
   | `tests/test_cost_report_endpoint.sh` | Header comment updated, assertions unchanged, passing |
   | `tests/test_budget_line_trim.sh` | Deleted (Task 2) |
   | `tests/test_cost_report_savings.sh` | Deleted (Task 2) |
   | `tests/test_cost_report_harness_economics.sh` | `harness_economics` half removed (Task 4), `on_failure` half kept, passing |

3. Confirm `entrypoint.sh`'s net line delta is a real reduction (sanity check against
   the issue's ~250-line-shed benefit claim):

```bash
git diff origin/main -- entrypoint.sh | grep -c '^-'
git diff origin/main -- entrypoint.sh | grep -c '^+'
```

4. Confirm no `gh`, `docker`, `archon`, or `claude` subprocess calls exist in either new
   module (the IO-injection boundary):

```bash
grep -n "subprocess\|os.system\|gh \|docker \|archon \|claude " \
  scripts/factory_core/cost_report.py scripts/factory_core/post_mortem.py || echo "clean"
```

5. If everything is green, this task has no code changes to commit — proceed to Phase 4
   (Publish) of the plan workflow.

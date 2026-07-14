# fix(scheduler): Case-Insensitive Board Status Matching in `get_items_by_status`

**Issue:** omniscient/dark-factory#275
**Related, explicitly NOT this ticket's scope:** #211 (CLOSED — sibling case-mismatch fix in
`epic_autopilot.py`'s `_in_progress_epics`, precedent for the `.lower()`/`ascii_downcase`
approach), #202 (provider-abstraction epic — this bug class is a portability landmine for it), a
follow-up ticket for startup board-schema validation (see Alternatives Considered — not filed by
this spec; scope boundary of the refine phase is docs/specs + memory only)

---

## Overview / Problem Statement

`scheduler.sh`'s `get_items_by_status()` (line 565) matches a GitHub Projects board item's
`Status` field with a case-sensitive exact string compare (`select(.status == "$status_name")`).
Four call sites (`IN_REVIEW`, `IN_PROGRESS`, plus the two-caller pattern noted in the issue) pass
sentence-case literals (`"In review"`, `"In progress"`), but the self-instance board's actual
Status option names are Title Case (`"In Review"`, `"In Progress"`, verified via GraphQL by the
issue author). The mismatch means `IN_REVIEW` and `IN_PROGRESS` are silently always empty on this
instance, disabling: Priority 0 (CI-failing gate), Priority 0.6 (green-PR rescue from Blocked),
Priority 1 (In-Review comment reactions), Priority 1.5 (proactive conflict resolve), the
direct-to-pr end-gate auto-close flow, and In-Progress WIP counting (`MAX_IN_PROGRESS` compares
against an always-empty bucket). This is the `scheduler.sh` sibling of the already-fixed #211
(`epic_autopilot.py`'s `_in_progress_epics`, same mismatch class, fixed with `.lower()` on both
sides).

## Requirements

1. `get_items_by_status()` must compare `status_name` against the item's board status
   case-insensitively (`ascii_downcase` both sides in jq), fixing the `IN_REVIEW`/`IN_PROGRESS`
   buckets — and, as a side effect of fixing the shared helper rather than call sites, also
   hardening `BLOCKED`/`READY`/`BACKLOG`/`REFINED` against the same class of drift on any future
   instance whose board casing differs.
2. The fix must be null-safe: `.fieldValueByName.name` (surfaced as `.status` by
   `fetch_board_items`) is `null` for a board item with no Status value assigned. Under this
   script's `set -euo pipefail` (line 2), calling `ascii_downcase` directly on a `null` raises a
   jq runtime error, which — because the four call sites assign via plain `VAR=$(get_items_by_status ...)`
   (not `local`) — propagates through `set -e` and would kill the scheduler's poll cycle instead
   of just leaving one bucket empty. The comparison must default missing status to `""` before
   downcasing: `(.status // "" | ascii_downcase)`.
3. Per the issue's explicit direction, the fix is scoped to the shared helper
   (`get_items_by_status`, line 565) only — not the four call-site string literals. This mirrors
   #211's chosen shape (fix the shared status-comparison point, not each caller) and is what
   "inoculates future instances" whose board casing differs again.
4. A regression test must assert that `get_items_by_status()` returns a non-empty bucket when
   queried with the call sites' sentence-case literals (`"In review"`, `"In progress"`) against a
   fixture board item whose `status` is Title Case (`"In Review"`, `"In Progress"`) — reproducing
   the exact mismatch from the issue.
5. The regression test is added as a new section inside the existing `tests/test_scheduler.sh`
   (not a new standalone file — see Brainstorming Q&A #2), using that file's existing
   `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh` + `assert_eq` harness. The fixture item must
   include `"content":{"type":"Issue"}`, since `get_items_by_status` also filters on
   `.content.type == "Issue"` (line 568) — omitting it would produce an empty bucket for an
   unrelated reason and give a false pass/fail signal.
6. Startup-time board-schema validation ("derive expected option names from the board once at
   startup and fail loudly on mismatch instead of silently returning empty buckets," from the
   issue's Fix section) is explicitly **out of scope** for this ticket — see Alternatives
   Considered.

## Brainstorming Q&A

> **Q:** The issue's "Fix" section ends with: "Consider deriving expected option names from the
> board once at startup and failing loudly on mismatch instead of silently returning empty
> buckets." This is phrased as a suggestion, not a hard requirement, and is a materially
> different/larger change than the core fix (case-insensitive matching in
> `get_items_by_status`). Should this startup-validation behavior be implemented as part of THIS
> ticket's spec, or should it be explicitly out of scope / deferred to a follow-up ticket?
>
> **A:** Keep it out of scope for this ticket; implement only the core case-insensitive fix here
> and file the startup-validation idea as a follow-up. Reasoning: (1) the issue itself hedges it
> with "Consider," and the core `ascii_downcase` fix already fully closes the reported defect and,
> per the issue's own words, "inoculates future instances" — startup validation is
> defense-in-depth, not the fix; (2) CLAUDE.md's scope-discipline convention ("touch only what the
> plan lists; the conformance gate excises out-of-scope changes and files spillover tickets")
> argues against bundling a materially larger, differently-shaped change (new startup probe, a new
> fatal-exit path) into a one-function jq fix on a `bug`/`priority: must-have` ticket that wants a
> tight, low-blast-radius fix; (3) blast radius differs sharply — the core fix is contained to
> `get_items_by_status` plus a fixture-based test with no behavior change beyond leniency, while a
> startup mismatch-and-fail-loudly check introduces a new fatal exit path that could halt the whole
> scheduler if the derivation logic or the board's option set is unexpected; (4) a "fail loudly at
> startup" pattern already exists to model a follow-up on — the `image_check` probe
> (`scheduler.sh:794-813`, emits a `probe=` log line, prints remediation, `sleep 60`, `exit 1` on
> failure) and the WIP-limits fetch (`scheduler.sh:788-792`, derives board-derived values once at
> startup) are both directly reusable precedent, which is itself a reason this cleanly separates
> into its own ticket rather than a reason to fold it in here. The follow-up should be coordinated
> with #211's sibling fix and the #202 provider-abstraction work.

> **Q:** The issue asks for "a regression test with a board fixture using 'In Review'/'In
> Progress' casing asserting each bucket is non-empty." `tests/test_scheduler.sh` already has a
> stub/harness pattern (`SCHEDULER_SOURCE_ONLY=1 source scheduler.sh` to unit-test individual bash
> functions directly, with stubbed `gh`/`docker`/`python3` and an `assert_eq` runner). There is no
> existing test file dedicated to `get_items_by_status`. Should the new regression test be added
> as a new section inside the existing `tests/test_scheduler.sh`, or as a new standalone test file
> (following the pattern of `tests/test_scheduler_ceiling.sh` / `tests/test_scheduler_pagination.sh`,
> which already split specific scheduler.sh concerns into their own files)?
>
> **A:** Add it as a new section inside the existing `tests/test_scheduler.sh`, not a standalone
> file. Reasoning: (1) the regression test is a live-invocation unit test of a sourced bash
> function — exactly what `test_scheduler.sh`'s existing sections (A–P) already do for functions
> like `get_retry_count`, `dependencies_met`, `fetch_board_items`; `get_items_by_status` is a pure
> jq transform over a JSON string, needing zero new scaffolding; (2) `test_scheduler_ceiling.sh` is
> the wrong template — it never sources `scheduler.sh`, it `awk`/`grep`s function bodies out to
> assert code patterns exist (structural, not behavioral), and has no `assert_eq`/stub harness;
> (3) `test_scheduler_pagination.sh` is a genuine standalone behavioral test, but it already
> duplicates section O's in-file pagination coverage and re-implements ~30-60 lines of
> stub-and-source boilerplate (config-var exports, providers-cli preflight stub, gh/docker/python3
> stubs) — a lot of duplication to justify for one small single-function assertion. One caveat
> raised and independently verified while writing this spec: **none** of the scheduler `.sh` test
> files (`test_scheduler.sh` and siblings) are currently wired into `.github/workflows/ci.yml` or
> any `.factory/hooks/*` gate — `ci.yml`'s `tests` job runs `python -m pytest tests/ -v` (which
> only collects `.py` files) plus four explicitly-named `.sh` files
> (`test_identity.sh`, `test_hooks.sh`, `test_smoke_gate.sh`, `test_run_compose.sh`), none of which
> is `test_scheduler.sh`. This is a pre-existing gap affecting the whole file, not something this
> ticket introduces or is responsible for fixing — noted under Open Questions.

## Architecture / Approach

**Current (`scheduler.sh:565-569`):**

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c "[.items[] | select(.status == \"$status_name\") | select(.content.type == \"Issue\")]"
}
```

**Fixed:**

```bash
get_items_by_status() {
  local items="$1"
  local status_name="$2"
  echo "$items" | jq -c --arg status_name "$status_name" \
    '[.items[] | select((.status // "" | ascii_downcase) == ($status_name | ascii_downcase)) | select(.content.type == "Issue")]'
}
```

Switching `status_name` from raw shell string interpolation to a jq `--arg` is a minimal,
directly-entailed part of this change (the value is now referenced through a `| ascii_downcase`
pipeline rather than a bare literal) and additionally removes a latent jq-string-injection risk if
`status_name` ever contained a `"` — no caller changes are required since all six call sites
already pass plain literal strings.

All six call sites (`scheduler.sh:840-846`: `IN_REVIEW`, `BLOCKED`, `READY`, `IN_PROGRESS`,
`BACKLOG`, `REFINED`) go through this one function, so the fix applies uniformly. `BLOCKED`,
`READY`, `BACKLOG`, `REFINED` are single-word statuses already first-letter-capitalized at both
the call site and (presumably) on every board's option list — case-insensitive comparison is a
no-op for them, so there is no behavior-change risk for the buckets that already work today.

This mirrors #211's fix shape in `epic_autopilot.py` (`status == "In progress"` →
case-normalized via `.lower()` on both sides in `_in_progress_epics`), applied at the equivalent
bash/jq choke point.

## Alternatives Considered

1. **Chosen: case-insensitive match inside `get_items_by_status`.** Single choke point, matches
   the #211 precedent, zero call-site changes, inoculates any future instance regardless of its
   board's casing convention.
2. **Fix the four call-site literals to match this instance's casing exactly
   (`"In review"` → `"In Review"`, etc.).** Rejected per the issue's explicit direction — this
   only fixes the currently-observed casing and reintroduces the identical bug the moment any
   instance (or a future board reconfiguration) uses different casing again; it does not
   "inoculate future instances."
3. **Startup board-schema derivation with fail-loud validation on mismatch.** Rejected as *this
   ticket's* scope per Brainstorming Q&A #1 — larger blast radius (new fatal startup exit path),
   differently-shaped change, and defense-in-depth orthogonal to the reported defect. Deferred to
   a follow-up ticket, to be modeled on the existing `image_check` probe (`scheduler.sh:794-813`)
   and WIP-limits fetch (`scheduler.sh:788-792`) patterns, coordinated with #211 and #202.

## Open Questions (Non-blocking)

- Should a follow-up ticket for startup board-schema validation be filed now? This spec does not
  file it — filing new GitHub issues is outside the refine phase's scope boundary
  (docs/specs + memory only). Recommend the human reviewer or the plan phase file it as a
  spillover-style follow-up if desired.
- None of `tests/test_scheduler*.sh` are wired into `ci.yml` or a `.factory/hooks/*` gate — a
  pre-existing gap that predates this ticket and applies to the whole file, not just the new
  section this ticket adds. Out of scope to fix here; flagged for awareness only.

## Assumptions

- The board's actual Status option names are `"In Review"` and `"In Progress"` (Title Case), as
  stated in the issue body and verified by the issue author via GraphQL against project 2. This
  spec does not independently re-verify against live GraphQL.
- `.fieldValueByName.name` (→ `.status` in `fetch_board_items`'s output, `scheduler.sh:558`) can
  be `null` for a board item with no Status value assigned; this was verified by reading the
  `fetch_board_items`/`get_items_by_status` source (`scheduler.sh:540-569`), not by observing an
  actual failure in production logs.
- No other `scheduler.sh` caller depends on `get_items_by_status`'s current case-sensitive
  behavior — verified by grep: all six call sites (`scheduler.sh:840-846`) pass literal strings
  already intended to match "the" board's status names, not deliberately-mismatched sentinels.

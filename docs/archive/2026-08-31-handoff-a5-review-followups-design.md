# Handoff manifest (A5) review follow-ups — tests, docs, edge cases

**Issue:** #381 (follow-ups from the operator review of PR #380 / issue #199)

## Overview

An independent operator review of PR #380 (issue #199, the A5 artifact-handoff-manifest
feature: `scripts/factory_core/handoff.py` / `tests/test_handoff.py`) confirmed the merged
implementation is correct but found six advisory gaps: missing reject-path test coverage,
an undocumented reason code, an incomplete label deny-list, a real (if narrow) filename
collision, a leftover cross-domain memory entry, and a comment nit. This is a size-S,
no-hotspot hardening ticket — no behavioral change to the accept path, no new schema
fields, no new reason codes beyond documenting one that already exists in code.

## Requirements

Numbered to match the issue's own list.

### 1 — Missing reject-path tests

Add tests to `tests/test_handoff.py` for three paths that currently execute in production
but have no test asserting their behavior:

- `FACTORY_MANIFEST_LABEL` containing a comma, whitespace, or set to the empty string →
  the `ValueError` raised inline in `intake()` is caught by the generic `except Exception`
  arm, recorded to `runs.jsonl` as `reject_reason=internal_error`, and re-raised as
  `HandoffError("internal_error", ...)`. No issue is created.
- The `internal_error` arm itself via a genuine `OSError`-shaped failure (e.g. an
  unwritable `--artifacts-dir`) — same `runs.jsonl` row shape, re-raised as `HandoffError`.
- `subprocess.TimeoutExpired` inside `_default_create_issue` → the `except
  subprocess.TimeoutExpired` branch returns `""`, which `intake()` turns into
  `issue_create_failed` the same way a non-zero exit already does.

### 2 — Document `internal_error`

`internal_error` is part of the closed reason-code vocabulary today (it's in
`HandoffError`'s own docstring and is asserted by existing tests indirectly via
`except Exception`), but it appears in neither reason-code table that documents the
others. Add a row for it to both:

- The A5 design spec's reason-code table
  (`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`).
- `docs/adapter-authoring-guide.md`'s "Handoff manifest (A5)" reason-code table.

And extend `tests/test_adapter_authoring_guide.py::test_guide_documents_handoff_manifest_a5_section`'s
token list with `"internal_error"` so the doc-guard actually pins the addition (mirroring
how it already pins every other reason code).

Suggested row text: `internal_error` — "Any failure that is not itself an R2-R5
manifest rejection (e.g. an unwritable `--artifacts-dir`, a malformed
`FACTORY_MANIFEST_LABEL` override) — still produces a `runs.jsonl` row (R6), fail-closed."

### 3 — Deny-list gate labels in the `FACTORY_MANIFEST_LABEL` override

**Decision (product-owner Q&A):** reject, case-folded, both an exact match on
`ready-for-agent` and any value ending in `-pending-review` — not a configurable or
regex-based deny-list, and not limited to today's two known `*-pending-review` literals.

Rationale: `*-pending-review` is a documented *shape* (CLAUDE.md's label-semantics section;
`docs/triage-labels.md`'s Workflow-flags table), not two accidental strings, so
`value.endswith("-pending-review")` is encoding an existing convention rather than
speculative generalization. `scheduler.sh` matches gate labels case-insensitively
(`grep -qi "plan-pending-review"` at `scheduler.sh:1144`, `grep -qi
"spec-pending-review"` at `scheduler.sh:1209`), so a case-sensitive check here would let
`READY-FOR-AGENT` slip past this guard while still being picked up by the scheduler as the
real gate label — case-fold both sides of the comparison. Do not add
`direct-to-pr`/`needs-discussion`/`epic`/other workflow labels to this deny-list; the issue
names exactly these two, and this stays a size-S chore.

Implementation sketch (inside `intake()`, before the existing comma/whitespace/empty
check, or folded into the same `if`):

```python
_label = FACTORY_MANIFEST_LABEL.lower()
if not FACTORY_MANIFEST_LABEL or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL) \
        or _label == "ready-for-agent" or _label.endswith("-pending-review"):
    raise ValueError(...)
```

Tests: a parametrized rejection case over `ready-for-agent`, `READY-FOR-AGENT`,
`spec-pending-review`, `plan-pending-review`, and one label of the same shape not
currently used anywhere (e.g. `triage-pending-review`) to prove the suffix rule — not just
the two literals — plus a retained positive case that an unrelated override (e.g.
`custom-intake`) still succeeds.

Docs: add one sentence to `docs/triage-labels.md`'s `manifest-intake` row noting that the
`FACTORY_MANIFEST_LABEL` override rejects `ready-for-agent` and any `*-pending-review`
label, so the doc and the code stay in sync.

### 4 — Verdict-filename cross-pair collision

**Decision (product-owner Q&A):** append a short deterministic SHA-256 hash of the
`(producing_loop, artifact_id)` pair; a non-charset separator alone (e.g. `--`) does not
close the collision, only narrow it — both fields are validated against `_ID_RE =
^[A-Za-z0-9._-]+$`, which permits `-` (and runs of it) *inside* either field, so
`producing_loop="a--b", artifact_id="c"` and `producing_loop="a", artifact_id="b--c"`
would still collide under a `--` separator.

Implementation sketch (new helper in `handoff.py`, used by `intake()` in place of the
inline f-string that currently builds `verdict_out`):

```python
def _verdict_filename(producing_loop: str, artifact_id: str) -> str:
    """Deterministic, collision-free verdict filename. `\\0` (excluded by _ID_RE from
    both fields) makes the hash input injective -- hashing the *rendered* filename
    (producing_loop + "-" + artifact_id) would just reproduce the same ambiguity this
    exists to close."""
    digest = hashlib.sha256(f"{producing_loop}\0{artifact_id}".encode("utf-8")).hexdigest()[:16]
    stem = f"loop-{producing_loop}-{artifact_id}"[:200]
    return f"{stem}-{digest}.md"
```

- **Algorithm/length:** `hashlib.sha256(...).hexdigest()[:16]`, matching existing
  `factory_core` precedent (`epic_autopilot.spec_hash()`,
  `scripts/factory_core/epic_autopilot.py:122-123`; `memory_import._compute_id()`,
  `scripts/memory_import.py:87-96`, both sha256-truncated-to-16). Not Python's builtin
  `hash()` — that's salted per process (`PYTHONHASHSEED`) and would break determinism
  across re-intake of the same manifest.
- **Determinism is load-bearing, not incidental:** re-intake of the same manifest must
  keep overwriting its own verdict file (the existing behavior the `handoff.py:384-387`
  comment documents), not accumulate copies — so no uuid/timestamp component.
  `(producing_loop, artifact_id)` alone is the hash input; nothing time-varying.
  `_verdict_filename` is a pure function of its two args and doesn't touch `os`/the
  filesystem — so a "same pair twice" determinism test needs no fixture, just two direct
  calls compared.
- **Stem truncation:** both `producing_loop` and `artifact_id` are capped at
  `MAX_ID_LEN = 128` chars each (`handoff.py:40`), so the untruncated stem can already
  reach `5 + 128 + 1 + 128 = 262` chars before the `-<hash>.md` suffix — over ext4's
  255-byte `NAME_MAX`. Slicing the stem to 200 chars is safe because the appended hash
  (not the stem) carries the uniqueness guarantee; the stem is for human legibility only
  when browsing `$ARTIFACTS_DIR`.
- No downstream code parses this filename back into `producing_loop`/`artifact_id` — it
  is referenced verbatim in `render_body()`'s Provenance bullet and in
  `runs.jsonl`'s `detail.verdict_path` — so changing its exact shape is safe.

Tests: update the existing hardcoded filename assertion in
`test_intake_accepts_and_creates_issue` (`tests/test_handoff.py:512`) to match the new
shape (assert the file exists at *some* path under `artifacts_dir` matching the
`loop-nightly-scan-triage-scan-2026-08-30-001-<16 hex chars>.md` pattern, e.g. via regex,
rather than pinning the exact hash), plus two direct unit tests against
`_verdict_filename`: `("a-b", "c")` and `("a", "b-c")` produce different filenames; the
same pair called twice produces the same filename.

Docs: the A5 design spec (`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`,
the R4/R5 discussion around lines 189 and 269) still shows the pre-#380 filename shape
(`$ARTIFACTS_DIR/loop-<producing_loop>.md`, no `artifact_id`, no hash) — already stale
against the merged code before this ticket. Update those two references to the new
`_verdict_filename` shape as part of this same doc pass, since they're directly adjacent
to the reason-code table edit from item 2.

### 5 — Leftover MarketHawk memory entry

Confirmed still present: `.archon/memory/codebase-patterns.md` carries `- [AVOID] exit_date
omission: always assign exit_date_val after _simulate_trade returns — unassigned date
fields silently persist NULL <!-- issue:#301 ... path:backend/app/services/ -->`, a
MarketHawk-domain entry that commit `3f66e27` ("memory: lessons from issue #199") intended
to prune alongside three siblings from the same `#301` batch but missed. `codebase-patterns.md`
is currently at exactly 30 entries — `memory_write.py`'s 30-entry authoritative cap
(`scripts/memory_write.py:180-184`) — so it is already blocking new writes independent of
this one entry, but removing it is still correct: it's dark-factory-domain memory carrying a
MarketHawk backend implementation detail with zero relevance to this repo. Remove the line.

### 6 — Comment nit: `run_record` raising inside `intake()`'s `except` arm

If `_record_intake()` (which calls `run_record.cmd_record` in-process) itself raises
inside the `except HandoffError` or `except Exception` arms of `intake()` — e.g. an
unwritable ledger — the original `HandoffError`/exception is replaced by the ledger error.
This is acceptable (fail-closed: the caller still sees a raised exception and `intake()`
does not silently succeed) but produces no `runs.jsonl` row for that occurrence, which is
worth a short comment so a future reader doesn't mistake the silent-row gap for a bug.
Add a one-line comment above the two `_record_intake(...)` calls in `intake()`'s `except`
arms, e.g.: `# If _record_intake itself raises (e.g. an unwritable ledger), that new
exception replaces this one -- fail-closed but rowless; acceptable, not a bug.`

## Architecture / Approach

All six items are localized edits to already-existing, already-tested surfaces —
`scripts/factory_core/handoff.py`, `tests/test_handoff.py`,
`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`,
`docs/adapter-authoring-guide.md`, `tests/test_adapter_authoring_guide.py`,
`docs/triage-labels.md`, `.archon/memory/codebase-patterns.md`. No new files, no new
schema fields, no new public functions beyond the one small `_verdict_filename` helper
(item 4), no change to the accept-path contract (`HANDOFF_ACCEPT_STATUSES`, the R2-R6
sequencing, or the reason-code *set* — `internal_error` already exists in code, item 2
only documents it). TDD applies per CLAUDE.md: write the new/updated tests first for items
1, 3, and 4 (the three with behavior implications), confirm they fail against
pre-change `handoff.py`, then implement.

Ordering recommendation for the implement phase: land item 4 (filename helper) before item
1's `test_intake_accepts_and_creates_issue` update, since that test's assertion is the one
item 4 changes; land item 3's code change before its own tests. Items 2, 5, 6 are
independent and can land in any order relative to the others.

## Alternatives considered

- **Item 3 — exact enumerated deny-list** (`{"ready-for-agent", "spec-pending-review",
  "plan-pending-review"}`) instead of the suffix pattern. Rejected: silently fails open on
  a future `*-pending-review` label the same way the `#344` memory entry
  (`.archon/memory/codebase-patterns.md`) warns a status denylist fails open on a future
  status value — and failing open here is security-relevant (a target-loop-authored issue
  could be smuggled into an existing gate state), whereas failing "too closed" only costs
  an operator a loud rename.
- **Item 4 — `--` separator only** (the issue's first suggested option). Rejected on
  verification: `_ID_RE` permits `-` inside both `producing_loop` and `artifact_id`, so a
  fixed separator drawn from the same charset as the fields narrows the collision window
  but does not close it (worked counterexample in Requirements #4). A separator character
  excluded from `_ID_RE` (there is none available without changing the charset itself) or
  a hash were the only two closing options; hash was chosen to avoid touching the R2
  schema charset, which is unrelated, already-shipped, already-tested surface.
- **Item 4 — hash the rendered filename or the `-`-joined string** instead of a
  null-byte-joined pair. Rejected: hashing `f"{producing_loop}-{artifact_id}"` reproduces
  the exact ambiguity being closed (the hash input itself would collide for the same
  counterexample pair).

## Open questions (non-blocking)

- `codebase-patterns.md` will drop to 29 entries (back under the 30-entry cap) after item 5's
  single removal, so this ticket incidentally unblocks new writes to the file; it does not
  otherwise attempt a general cap-compliance pass — out of scope for a size-S review-followup
  chore (`scripts/memory_maintain.py` already exists as the likely home for a future
  maintenance pass if the file fills up again).

## Assumptions (flagged)

- Item 2's suggested `internal_error` doc-table wording is a starting point for the
  implement phase, not frozen prose — the exact sentence may be adjusted to match the
  surrounding table's tone as long as the doc-guard token (`"internal_error"`) is present
  verbatim.
- Item 3's suffix check applies only to the `FACTORY_MANIFEST_LABEL` env-override
  validation path inside `handoff.py::intake()` — it does not touch `providers/cli.py`'s
  label-splitting logic or any other label-handling call site, none of which this issue
  names.

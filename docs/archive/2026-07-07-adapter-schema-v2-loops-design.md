# Adapter schema v2 — validated `loops:` declaration block (A1)

**Issue:** #195 · **Epic:** #194 (Factory/Target boundary v1) · **Spec basis:** #193 refined-spec comment, AC3 item 1
**Status:** spec-pending-review
**Revised:** 2026-07-17 — added the Tier 2 reserved-key rejection mechanism (Requirement 3a,
5a); everything else research-driven (role_card, economics, skills, five-move
restructuring, conditional-requiredness) is explicitly deferred to a new follow-up
ticket, "A1.5" — see "Deferred to A1.5" below and Alternative 7. The original scope
(Requirements 1–9) is unchanged from the 2026-07-07 version.

## Deferred to A1.5 (not in this ticket)

Since the original spec was written, five research-driven optional `loops:` extensions
were proposed via trusted "Hermes Agent / Product Manager" comment-channel input (see
CLAUDE.md § Trusted comment channels, added in PR #299): `role_card`, a five-move
checklist restructuring, `skills`, `economics`, and a `side_effect_level`-conditional
requiredness rule. Folding all of that in here would 2–3x this ticket's schema surface,
validation code, and test matrix — well past `size: M` and against the tracer-bullet
intent this ticket already establishes (A2–A5 exist precisely because this epic defers
semantics in slices). Per the synthesis brief's own explicit split permission, this
ticket keeps its original scope and ships only the cheap, execution-inert "Tier 2"
reserved-key carve-out (Requirement 3a/5a below); the rest moves to an immediate
follow-up ticket, informally "A1.5," which should reuse the maintainer's 2026-07-17
synthesis-brief comment on #195 as its content brief. Nothing is silently dropped: every
research item is either shipped here (reserved-key rejection), explicitly deferred with a
named home (A1.5), or explicitly excluded with a documented reason (see Alternative 7 —
`role_card.allowed_tools`/`forbidden_tools`). A1.5 should land before #196 (A2) begins
building enforcement against the loop-entry shape, since the five-move restructuring
reshapes that entry (see Alternative 7 for the mechanical field mapping).

## Overview

Dark Factory's `.factory/adapter.yaml` loader (`scripts/factory_core/adapter.py`) is a
78-line hand-rolled config merger: it deep-merges a target repo's adapter file over
`adapter_defaults.DEFAULTS`, with only shallow validation (`schema_version` must be an
int, five keys must be mappings, unknown top-level keys warn but never fail). Per the
#193 spike, this is the "Factory/Target boundary" contract layer, and it currently has no
way for a target repo to declare an **agentic loop** — a named, bounded piece of
target-owned automation with a trigger, inputs/outputs, a verifier, a stop condition, a
failure behavior, and a side-effect level.

This ticket (#195, "A1" of epic #194) is a **tracer bullet**: it adds a `loops:` block to
the adapter schema, validates each declared loop structurally, and surfaces the parsed
result in the run record for provenance. It explicitly does **not** execute, verify, or
enforce anything about a declared loop — those semantics land in child tickets A2
(side-effect enforcement, #196), A3 (verifier abstraction, #197), A4 (stop-condition
enforcement, #198), and A5 (handoff manifest, #199), all of which are out of scope here.

This ticket also closes out two long-standing pieces of tech debt flagged by the #193
spike: the adapter's validation is "shallow ... no JSON-schema, no migration path," and
three reserved top-level keys (`repo`, `board`, `labels`) are accepted by the loader but
have zero consumers and zero defaults — dead surface that must become either functional
or removed.

## Requirements

Distilled from the issue and the brainstorming Q&A below:

1. **`loops:` is a list.** Each entry is a mapping with exactly these 11 required
   fields: `name`, `purpose`, `trigger`, `inputs`, `outputs`, `artifacts`, `verifier`,
   `stop_condition`, `failure_behavior`, `side_effect_level`, `handoff`.
2. **Structural validation only, hand-rolled, no new dependency.** No `jsonschema`
   (or any other schema library) is added. Validation is plain Python
   (`isinstance`/key-membership checks) in the same `AdapterError`-raising style
   `adapter.py` already uses.
3. **Field-level rules (A1 scope — no semantic/enforcement logic):**
   - `name`, `purpose`, `trigger`, `verifier`, `stop_condition`, `failure_behavior`,
     `handoff` — each must be a non-empty string. `verifier`, `stop_condition`, and
     `handoff` are opaque references at this stage (their format/resolution is defined
     by A3/A4/A5); A1 only checks "it's a non-empty string." No fixed enum vocabulary
     is invented for `trigger` or `failure_behavior` — any non-empty string is valid.
   - `inputs`, `outputs`, `artifacts` — each must be a list of strings (may be empty
     lists).
   - `side_effect_level` — must be an `int` in the closed range `1..6`. A1 validates
     the range only; it does **not** enforce the level-1–3-target/level-4–5-factory/
     level-6-out-of-scope policy from the #193 non-negotiables — that's A2.
   - All 11 fields are required on every entry; none are optional in A1.
   - Any key inside a loop entry that is not one of the 11 known fields is a hard
     `AdapterError` (typo protection) — this is new, stricter-than-today behavior, but
     scoped **only** to `loops:` entries (see Requirement 5).
3a. **Reserved-but-unshipped loop-entry field: `memory_intervention`.** This name is
   reserved for epic #241 (Proactive execution-state memory), which will define its own
   schema/triggers/governance. A `loops:` entry that sets `memory_intervention` raises a
   **targeted** `AdapterError` naming #241 (not the generic "unknown field" message),
   checked before the generic unknown-field check in `_validate_loop`. This is a
   messaging refinement of the existing Requirement 3 unknown-field rule, not new
   strictness — the key was already going to be rejected; this only makes the rejection
   discoverable and points at where the real design work belongs.
4. **`loops:` is independent of `schema_version`.** `schema_version` remains inert
   free-form-int metadata exactly as today (no value-gating logic is added). A `loops:`
   key is validated whenever it is present in the YAML, regardless of what
   `schema_version` declares. Any integer value for `schema_version` continues to be
   accepted, as today — no restriction to `{1, 2}`.
5. **No regression to existing (non-`loops`) validation behavior.** Top-level unknown
   keys still warn-and-carry (does not fail) — `test_unknown_keys_warn_not_fail` stays
   green unmodified. The five existing map-valued blocks (`safety`, `components`,
   `memory_routing`, `deconflict`, `token_optimization`) get **no** new per-key
   validation in this ticket — only `loops:` entries get strict unknown-key rejection.
5a. **Reserved-but-unshipped top-level key: `mechanism_candidates`.** This name is
   reserved for a future design ticket (the "Bilevel Autoresearch" mechanism-carrier
   proposal has no filed ticket yet — the error message says so generically until one
   exists). Unlike a *generic* unrecognized top-level key (Requirement 5, warn-and-carry
   — a parity guarantee for real v1 adapters that may already set arbitrary top-level
   keys), `mechanism_candidates` specifically has zero v1 history and gets hard-rejected
   with an `AdapterError` — carrying it through unvalidated would defeat the entire point
   of reserving the name. This is a single named carve-out, not a change to the generic
   top-level warn-and-carry path; `test_unknown_keys_warn_not_fail` must keep exercising
   a *non*-reserved key so it stays green.
6. **v1 / absent-adapter parity.** An adapter file with `schema_version: 1` and no
   `loops:` key, and a repo with no adapter file at all, both continue to behave exactly
   as today for every existing key — the only observable change is that the merged
   config gains an additive `"loops": []` default (see Architecture, `adapter_defaults.py`).
   All existing `test_adapter.py` tests stay green unmodified.
7. **`repo`, `board`, `labels` are removed**, not wired to new behavior. They are
   deleted from `_KNOWN_TOP` and `_MAP_KEYS` in `adapter.py`. An adapter.yaml that still
   sets one of them now falls through to the existing top-level unknown-key
   warn-and-carry path — same UX as any other unrecognized key, not a new failure mode.
8. **Loop declarations are surfaced in the run record.** `run_record.py`'s
   `cmd_assemble()` reads `adapter.get(clone_dir, "loops")` (fail-open — a malformed
   adapter file must not break end-of-run assembly, which already runs under `|| true`
   in `entrypoint.sh`) and includes the result as `run_record["loops"]`
   (`[]` if adapter-less or on any read error).
9. **Not in scope:** executing loops, resolving/calling verifiers or stop conditions,
   enforcing side-effect levels or permission profiles, artifact handoff manifests, or
   any UI/reporting beyond the raw `loops` list appearing in `run-record.json` /
   `runs.jsonl`. All of that is A2–A5.

## Architecture / Approach

### `scripts/factory_core/adapter_defaults.py`

Add an additive default:

```python
DEFAULTS = {
    "schema_version": 1,
    "components": {...},   # unchanged
    "safety": {...},        # unchanged
    "memory_routing": {...},# unchanged
    "deconflict": {...},    # unchanged
    "loops": [],            # NEW — empty by default; parity-when-absent
}
```

### `scripts/factory_core/adapter.py`

- `_KNOWN_TOP`: add `"loops"`; remove `"repo"`, `"board"`, `"labels"`.
- `_MAP_KEYS`: remove `"board"`, `"labels"` (`"repo"` was never in this set). `loops` is
  **not** added to `_MAP_KEYS` — it is a list, not a mapping, and gets its own
  list-of-dicts check, distinct from the existing "must be a mapping" branch.
- New constants describing the loop-entry shape, e.g.:
  ```python
  _LOOP_REQUIRED_FIELDS = {
      "name", "purpose", "trigger", "inputs", "outputs", "artifacts",
      "verifier", "stop_condition", "failure_behavior", "side_effect_level", "handoff",
  }
  _LOOP_STRING_FIELDS = {
      "name", "purpose", "trigger", "verifier", "stop_condition",
      "failure_behavior", "handoff",
  }
  _LOOP_LIST_FIELDS = {"inputs", "outputs", "artifacts"}
  ```
- New `_validate_loop(entry, index)` helper (called from `load()` when `"loops"` is
  present in the parsed YAML), raising `AdapterError` with a message that names the
  loop index/name and the offending field, e.g.:
  - not a mapping → `f"loops[{index}] must be a mapping, got {type(entry).__name__}"`
  - unknown field → `f"loops[{index}] ('{name}'): unknown field '{key}'"`
  - missing field → `f"loops[{index}] ('{name}'): missing required field '{field}'"`
  - wrong type → `f"loops[{index}] ('{name}'): field '{field}' must be a non-empty string"`
    (or "a list of strings", or "an int between 1 and 6")
- Reserved-field constants (Requirement 3a/5a), consulted *before* the generic
  unknown-field / unknown-top-level-key checks so the reject message names the tracking
  epic instead of reading as a plain typo:
  ```python
  # Per-loop-entry field names reserved for a tracked-but-unshipped extension.
  # Rejected with a targeted message so the extension point is discoverable
  # without A1 accepting unvalidated content. Consulted before the generic
  # unknown-field error in _validate_loop.
  _RESERVED_LOOP_FIELDS = {"memory_intervention": "#241"}

  # Top-level key names reserved for a tracked future design ticket. Unlike a
  # generic unknown top-level key (which warns and carries — v1 parity), a named
  # reserved key is hard-rejected: it has no v1 history, so strictness here is
  # parity-safe, and warn-and-carry would deep-merge unvalidated content into config.
  _RESERVED_TOP_FIELDS = {
      "mechanism_candidates": "a future Bilevel Autoresearch design ticket",
  }
  ```
  In `_validate_loop`'s unknown-field branch:
  ```python
  if key not in _LOOP_REQUIRED_FIELDS:
      if key in _RESERVED_LOOP_FIELDS:
          raise AdapterError(
              f"loops[{index}] ('{name}'): field '{key}' is reserved for epic "
              f"{_RESERVED_LOOP_FIELDS[key]} (per-loop memory intervention) and is "
              f"not accepted in schema v2; remove it"
          )
      raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")
  ```
  In `load()`'s existing per-top-level-key loop, checked before the `k not in _KNOWN_TOP`
  warning branch so it fails instead of warning:
  ```python
  for k, v in data.items():
      if k in _RESERVED_TOP_FIELDS:
          raise AdapterError(
              f"adapter key '{k}' is reserved for {_RESERVED_TOP_FIELDS[k]} and is "
              f"not accepted in schema v2; remove it"
          )
      if k not in _KNOWN_TOP:
          print(f"adapter: warning — unknown adapter key '{k}' (carried through)", file=sys.stderr)
      if k in _MAP_KEYS and not isinstance(v, dict):
          raise AdapterError(f"adapter key '{k}' must be a mapping, got {type(v).__name__}")
  ```
  The two constants are intentionally separate, not a unified helper — they sit at
  different validation layers with different message idioms (`loops[{i}] ('{name}'):
  field ...` vs `adapter key '...'`), matching the two error shapes already present in
  `adapter.py`.
- In `load()`: after the existing per-top-level-key loop, if `"loops" in data`: assert
  `isinstance(data["loops"], list)`, then call `_validate_loop(entry, i)` for each
  entry. This runs before `_deep_merge` so a bad `loops:` entry fails the whole load
  (consistent with existing behavior for malformed `safety`/etc.).
- `_deep_merge` needs **no changes** — a list value in `override` already fully replaces
  the base list (the existing `isinstance(v, dict) and isinstance(out.get(k), dict)`
  branch is `False` for lists), which is the correct "adapter's `loops:` fully replaces
  the empty default" semantics — same pattern already used for e.g.
  `safety.critical_diff_paths`.

### `scripts/factory_core/run_record.py`

- Add `--clone-dir` to the `assemble` subparser, defaulting from `CLONE_DIR` env
  (mirrors `adapter.py`'s own `--clone-dir` default):
  ```python
  a.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
  ```
- In `cmd_assemble()`, alongside the existing `stages`/`nodes`/`artifacts`/`totals`
  construction, add a fail-open read:
  ```python
  from . import adapter
  try:
      loops = adapter.get(args.clone_dir, "loops") or []
  except Exception:
      loops = []
  run_record["loops"] = loops
  ```
  The `try/except` is load-bearing: `adapter.get` → `load()` raises `AdapterError` on a
  malformed `adapter.yaml`, and `assemble` is invoked with `|| true` in
  `entrypoint.sh` — a bad adapter file must degrade to `loops: []`, not abort end-of-run
  record assembly.
- Test-double update: `tests/test_run_record.py`'s `_AssembleArgs` helper needs a
  `clone_dir` class attribute default (e.g. `clone_dir = "."`) so existing tests that
  don't pass it explicitly keep working.

### `entrypoint.sh`

- Add `--clone-dir "$CLONE_DIR"` to the existing `run-record assemble` invocation
  (the one that already passes `--run-id`, `--issue`, `--intent`, `--started-at`,
  `--artifacts-dir`, `--archon-cost-json`, `--out-file`). `$CLONE_DIR` is already in
  scope at that point in the script.

### `tests/test_adapter.py` — new coverage

- A v2-shaped adapter with one valid `loops:` entry parses and round-trips.
- Each of: missing a required field, an unknown field inside a loop entry, wrong type
  per field group, `side_effect_level` out of `1..6` range (both too low and too high),
  `side_effect_level` non-int, `loops` not a list, a loop entry not a mapping — each
  raises `AdapterError` with a message naming the loop and field.
- `repo`/`board`/`labels` set in an adapter.yaml now hit the top-level
  "unknown adapter key" warning path (not `AdapterError`) — parity/removal test.
- A `loops:` entry setting `memory_intervention` raises `AdapterError` whose message
  contains `#241` (Requirement 3a).
- A top-level `mechanism_candidates` key raises `AdapterError` (Requirement 5a) —
  asserted as a hard failure, distinct from the existing
  `test_unknown_keys_warn_not_fail` case, which must keep using a *non*-reserved
  unknown key so it still exercises (and stays green on) the generic warn-and-carry path.
- Absent adapter and `schema_version: 1`-without-`loops` both merge to `loops: []` and
  are otherwise unchanged from today (existing tests + one asserting the new key).
- A `schema_version: 1` file that *also* declares a valid `loops:` entry still parses
  (`schema_version` doesn't gate `loops:`).

### `tests/test_run_record.py` — new coverage

- `cmd_assemble()` with a `--clone-dir` pointing at a fixture with a `loops:` entry
  produces `run_record["loops"] == [<that entry>]`.
- `cmd_assemble()` with no adapter file at `--clone-dir` produces `run_record["loops"] == []`.
- `cmd_assemble()` with a malformed adapter file at `--clone-dir` still completes and
  produces `run_record["loops"] == []` (fail-open).

## Alternatives considered

1. **`jsonschema` library-based validation.** Rejected — the factory image pins exactly
   one Python dependency for this tooling (`pyyaml`, with an explicit Dockerfile comment
   about avoiding transitive-dependency reliance before target deps install). The
   `loops:` shape is small and closed (11 fixed fields), fully expressible with the
   existing `isinstance`/`AdapterError` idiom, and hand-rolled errors give more control
   over "actionable" messaging than surfacing raw `jsonschema.ValidationError` text.
   Chosen approach: **plain-Python structural validation**, matching the existing
   `adapter.py` style.

2. **Broadening unknown-key strictness to all known blocks** (`safety`, `components`,
   `memory_routing`, `deconflict`, `token_optimization`), not just `loops:` entries.
   Rejected — the acceptance criterion "a v1 adapter behaves byte-identically to today"
   is a hard constraint, and today those blocks silently accept any inner key. Existing
   parity tests don't happen to cover this case, so the risk is real, not just
   theoretical. Chosen approach: **strict unknown-key rejection scoped only to `loops:`
   entries**, which is brand-new surface with no v1 history and therefore zero parity
   risk.

3. **Inventing enum vocabularies for `trigger` / `failure_behavior` now.** Rejected — no
   enum is defined anywhere in the issue, epic, or spike; the epic explicitly defers
   semantics/enforcement of loop behavior to A2–A5. Fixing a vocabulary in A1 would be
   scope creep and would likely need revision once A2–A5 land. Chosen approach:
   **non-empty-string validation only** for these fields in A1.

4. **`schema_version` as a feature gate for `loops:`** (i.e., reject/ignore `loops:`
   under a v1-declared file). Rejected — `schema_version` is inert metadata today (only
   checked for being an int, never branched on), no shipped adapter.yaml combines an old
   `schema_version` with a new `loops:` key (so there's no real-world ambiguity to
   resolve), and adding version-conditional parsing would be new complexity the ticket
   never asked for and would complicate the "byte-identical" parity guarantee for a
   field that currently gates nothing. Chosen approach: **`loops:` is validated whenever
   present, independent of `schema_version`**.

5. **Wiring `repo`/`board`/`labels` to real behavior** (e.g. `repo` overriding the
   GitHub repo slug). Rejected — zero consumers exist anywhere in the codebase today,
   no committed adapter.yaml sets them, and no design doc or ticket proposes concrete
   semantics; inventing functionality for them would be unscoped feature invention on a
   ticket whose stated job is dead-surface reclamation. Chosen approach: **remove them**
   from `_KNOWN_TOP`/`_MAP_KEYS` entirely — they become ordinary unrecognized keys.

6. **Passing `loops` JSON through `entrypoint.sh` into `run_record.py` via a shell-read
   `--detail`-style flag** (keeping `run_record.py` adapter-agnostic), mirroring how
   `gate_lib.sh` reads adapter values in pure-shell gates. Rejected — every other Python
   `factory_core` module that needs an adapter value (`deconflict.py`,
   `main_red_fixer.py`, `epic_autopilot.py`) reads it directly via
   `adapter.get(clone_dir, "...")` with a fail-open fallback; `run_record.py` is already
   a `factory_core` module invoked through `cli.py`, so the direct-import pattern is
   the consistent choice. The shell-passthrough pattern in `gate_lib.sh` exists
   specifically because that caller has no Python module to host the read. Chosen
   approach: **`run_record.py` imports `adapter` directly**, gated with `--clone-dir`.

7. **Folding all five research-driven extensions (`role_card`, five-move restructuring,
   `skills`, `economics`, conditional-requiredness) directly into #195/A1 now.**
   Rejected — see "Deferred to A1.5" above; this would 2–3x the schema/validation/test
   surface of a ticket labeled `size: M`, against both the label and the tracer-bullet
   precedent the epic itself established (A2–A5 exist because this epic defers loop
   semantics in slices). Chosen approach: **ship only the Tier 2 reserved-key carve-out
   here; file everything else as A1.5**, reusing the synthesis-brief comment on #195 as
   its content brief. Two things A1.5 must carry forward explicitly, so nothing is lost
   in the handoff:
   - **`role_card.allowed_tools`/`role_card.forbidden_tools` are excluded, not just
     deferred.** These are tool allow/deny-list declarations — exactly the
     "security-sensitive surface" `CLAUDE.md`'s Trusted comment channels section (added
     PR #299) says comment-channel input may never authorize, regardless of signature.
     The rest of `role_card` (`name`, `responsibilities`, `non_responsibilities`,
     `output_schema`, `fallback_path`, `observability`) is ordinary declared-but-
     unenforced metadata and isn't excluded on those grounds — A1.5 can still propose it.
   - **Five-move restructuring is not additive** — it re-maps A1's flat 11 required
     fields into sub-blocks (discovery/handoff/verification/persistence/scheduling +
     `human_checkpoint`/`budget_caps`). The flat fields map mechanically (`verifier`→
     verification, `handoff`→handoff, `stop_condition`/`failure_behavior`→
     verification/scheduling), and it's safe to do post-hoc because loops are
     execution-inert until A2–A5 and schema v2 has no external consumers yet — but A1.5
     must land **before** #196 (A2) starts building enforcement against the flat shape,
     or the reshape becomes a breaking change instead of a mechanical one. This spec's
     Requirements 1/3 (the flat 11-field shape) should be read as provisional pending
     A1.5, not as schema v2's frozen contract.
   - The conditional-requiredness rule's `side_effect_level` threshold (the brief
     suggests calibrating around `>= 4`) is left as an open design question for A1.5,
     not settled here.

## Brainstorming Q&A

> **Q:** Should the implementation add the `jsonschema` PyPI library as a new
> dependency, or hand-roll loop-entry validation in plain Python consistent with the
> existing `AdapterError`-based style?
> **A:** Hand-roll it, dependency-free. The Dockerfile pins exactly one Python
> dependency for this tooling (`pyyaml`), with an explicit comment about avoiding
> transitive-dependency reliance; the `loops:` shape is small and closed and fits the
> existing 78-line hand-rolled-validator idiom. The ticket's "or equivalent structured
> validation" phrasing explicitly allows this. Apply any tightening surgically to the
> `loops:` block so v1 parity tests aren't put at risk.

> **Q:** Does the "unknown keys inside known blocks become errors" tightening apply only
> to entries inside the new `loops:` list, or should it also retroactively tighten the
> pre-existing blocks (`safety`, `components`, `memory_routing`, `deconflict`,
> `token_optimization`)?
> **A:** Only `loops:` entries. The "v1 adapter behaves byte-identically to today"
> acceptance criterion is a hard constraint; those blocks accept arbitrary keys silently
> today and real v1 adapters could rely on that. The existing parity tests don't happen
> to cover a stray key inside a known block, so passing tests alone wouldn't catch a
> retroactive tightening — the byte-identical criterion is what makes the narrow reading
> mandatory. `loops:` is brand-new with no v1 history, so strictness there carries zero
> parity risk.

> **Q:** For each `loops:` entry field, is A1's validation structural-only (types/ranges,
> with `verifier`/`stop_condition`/`handoff` as opaque non-empty-string references and no
> invented enum for `trigger`/`failure_behavior`), and are all 11 fields required?
> **A:** Yes to both. Structural-only validation matches the existing shallow-by-design
> loader and the "parsed, validated, and surfaced ... not executed" framing — inventing
> an enum would contradict the issue directly since no vocabulary is defined anywhere.
> All 11 fields are required: the issue frames the loop as a declaration block, which is
> the schema contract A1 exists to lock down, and A2–A5 build enforcement assuming these
> fields are present. "Required and structurally valid in A1" is distinct from "enforced
> in A2–A5" (e.g. `side_effect_level`'s 1–3/4–5/6 policy is A2's job, not A1's).

> **Q:** Given zero consumers and no proposed concrete use for `repo`/`board`/`labels`,
> should A1 simply remove them from `_KNOWN_TOP`/`_MAP_KEYS` rather than inventing
> functionality?
> **A:** Yes, remove them. Confirmed via grep: no `adapter.get(..., "repo"/"board"/
> "labels")` call exists anywhere (the `labels` hits elsewhere in the codebase are all
> GitHub API issue/PR dict access, unrelated to the adapter). Neither key appears in
> `adapter_defaults.DEFAULTS` nor in any committed adapter.yaml. The AC offers a binary
> choice ("functional or gone") and no design doc proposes concrete semantics for
> "functional," so removal is the minimal-scope, correct choice — a future adapter.yaml
> setting one now falls through to the existing top-level unknown-key warn-and-carry
> path, which is exactly the desired "gone" behavior.

> **Q:** What's the concrete mechanism for "loop declarations appear in the run record"
> — should `run_record.py` read the adapter directly, should `entrypoint.sh` pass parsed
> JSON through a flag, or does this belong somewhere else entirely?
> **A:** `run_record.py`'s `cmd_assemble()` should gain a `--clone-dir` argument and call
> `adapter.get(clone_dir, "loops")` itself (fail-open, defaulting to `[]` on any
> exception), matching the direct-import-with-fallback pattern every other
> `factory_core` module already uses (`deconflict.py`, `main_red_fixer.py`,
> `epic_autopilot.py`). The shell-passthrough pattern used in `gate_lib.sh` is specific
> to that caller having no Python module to host the read; `run_record.py` is already a
> `factory_core` module invoked through `cli.py`, so the direct-read pattern is
> consistent. `entrypoint.sh` should add `--clone-dir "$CLONE_DIR"` to its existing
> `run-record assemble` call.

> **Q:** Should `schema_version` become a version gate that controls whether `loops:` is
> recognized, or should `loops:` validate whenever present, independent of
> `schema_version`'s value?
> **A:** Independent — no gate. `schema_version` is inert metadata today (only checked
> for being an int, never branched on); introducing a gate would be the only place the
> number means anything, contradicting the established pattern. It also buys nothing for
> parity, since no v1 file contains `loops:` today, so "old files without `loops:` are
> untouched" already satisfies "v1→v2 migration" without any gating logic.
> `schema_version` keeps accepting any int, exactly as today — restricting to `{1, 2}`
> would newly reject files that load fine today, which would itself break the
> byte-identical parity guarantee.

> **Q (2026-07-17):** The original A1 spec is a complete, narrowly-scoped tracer bullet.
> Folding in the five research-driven extensions proposed via trusted "Hermes Agent"
> comment-channel input (role_card, five-move restructuring, skills, economics,
> conditional-requiredness) would roughly double or triple the schema surface,
> validation logic, and test matrix. Given the `size: M` label and the epic's own
> A1/A2–A5 slicing precedent, should #195/A1 stay exactly as spec'd, with all of this
> filed as a new "A1.5" follow-up ticket? Should the cheap Tier 2 reserved-key-rejection
> mechanism (`memory_intervention`, `mechanism_candidates`) ship now or move with the
> rest?
> **A:** Split. #195/A1 stays exactly as already spec'd, plus ships the Tier 2
> reserved-key rejection now — it belongs in A1 because the synthesis brief itself names
> "reserved-name mechanism" as part of the A1 floor, it's a two-constant/two-branch
> addition to validation logic A1 is already building, it's an unenforced *rejection* so
> it touches no security surface, and without it a target repo could put arbitrary
> unvalidated content under those reserved names today. Everything else (role_card,
> five-move restructuring, skills, economics, conditional-requiredness) moves to a new
> immediate follow-up ticket ("A1.5") reusing the synthesis brief as its content brief,
> which must land before #196 (A2) starts building enforcement, since the five-move
> restructuring reshapes the loop-entry schema A2 would otherwise build against.

> **Q (2026-07-17):** What's the concrete validation mechanism for the two Tier 2
> reserved-key rejections — does `memory_intervention` need special-casing beyond the
> unknown-field rule A1 already has, and should `mechanism_candidates` hard-fail or just
> warn like other unrecognized top-level keys?
> **A:** Both get a targeted `AdapterError` naming the tracking epic, via two different
> mechanisms because they sit at two different validation layers. `memory_intervention`
> is already rejected "for free" by the existing per-loop-entry unknown-field rule
> (Requirement 3) — this only upgrades the message (checked before the generic
> unknown-field branch) so it names #241 instead of reading as a plain typo; there's no
> new strictness or parity question since loop entries are already in the strict domain.
> `mechanism_candidates` is different: top-level unknown keys are lenient today
> specifically for v1 parity (real v1 adapters may already set arbitrary top-level keys
> silently), but that lenience exists *only* to protect keys with real v1 history.
> `mechanism_candidates` has zero shipped usage — identical footing to `loops:` itself —
> so a targeted hard-reject carries zero parity risk, and is actually necessary: warn-
> and-carry would deep-merge the unvalidated content straight into the config,
> defeating the entire point of reserving the name. `test_unknown_keys_warn_not_fail`
> stays green because it must exercise a *different*, non-reserved unknown key.

## Open questions (non-blocking)

- **[NEW]** A1.5's exact scope (role_card-minus-two-fields, five-move restructuring,
  skills, economics, conditional-requiredness) and its `side_effect_level` threshold for
  conditional-requiredness are intentionally left to that ticket's own refine pass — see
  "Deferred to A1.5" above and Alternative 7. Filing that ticket is a next step for the
  maintainer/scheduler, not an action this refine run takes (out of this command's
  file-output scope).

- Should `_validate_loop` include "did you mean...?" suggestions for near-miss unknown
  keys (e.g. `stopcondition` → `stop_condition`)? Not required by the acceptance
  criteria ("actionable errors" is satisfied by naming the loop index/name and the bad
  field); left as an implementation nicety, not a spec requirement.
- Whether `loops:` entries should support a `name`-uniqueness check (two loops declaring
  the same `name`) is left to the implementer's judgment — not mentioned in the issue,
  and no downstream consumer in this ticket depends on uniqueness (A3's verifier
  registration, where name collisions would actually matter, is out of scope here).

## Assumptions (flagged)

- **[ASSUMPTION]** Adding `"loops": []` to `adapter_defaults.DEFAULTS` is an acceptable
  additive change under "byte-identical to today," since it only adds a new key with an
  empty default and does not alter the value of any existing key that current consumers
  read via dotted-path `adapter.get()`. This mirrors how every other adapter block
  (`components`, `safety`, `memory_routing`, `deconflict`) was presumably introduced.
- **[ASSUMPTION]** `run_record.py`'s `cmd_assemble()` is the correct (and sufficient)
  place to surface loop declarations for "provenance," per Requirement 8 / the run-record
  Q&A above. No other artifact or reporting surface (e.g. the PR body, a GitHub comment)
  is required by this ticket — those are plausible future consumers of
  `run_record["loops"]` but are not part of A1's acceptance criteria.
- **[ASSUMPTION]** This repo has no `docs/superpowers/specs/` directory yet (this file is
  the first entry) and no `CLAUDE.md`/`ARCHITECTURE.md` — both confirmed absent by
  direct search; dark-factory's own `.factory/adapter.yaml` explicitly notes "No
  ARCHITECTURE.md in this repo yet." This spec follows the structure given in
  `orchestrator-prompt.md` in the absence of a local precedent to match.
  **[2026-07-17 update]** `CLAUDE.md` now exists on `main` (predates this update; added
  by an earlier, unrelated commit per `git log --follow -- CLAUDE.md`) — its presence
  doesn't change this spec's structure, which was already finalized before that file
  existed.
- **[2026-07-17, NOT an assumption — independently verified]** The claim that
  `CLAUDE.md` on `main` sanctions "Hermes Agent" comment-channel input (with the tool
  allow/deny-list exclusion) was not taken on the strength of an in-thread comment. It
  was checked directly: `gh pr view 299` shows commit `9b8f6958` genuinely merged
  2026-07-17, and `git show origin/main:CLAUDE.md` was read directly and contains the
  "Trusted comment channels" section quoted in Alternative 7. This grounds the A1/A1.5
  split and the `role_card` tool-field exclusion in verified repo state, not comment
  self-assertion.

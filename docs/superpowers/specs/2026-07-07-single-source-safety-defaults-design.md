# Single-source safety constants in `adapter_defaults` — drop fallback copies (#184)

**Issue:** #184 · **Status:** spec-pending-review

## Overview

`scripts/factory_core/adapter_defaults.py` declares itself the source of truth for
the factory's safety constants (`sensitive_keywords`, `hard_exclude_paths`,
`COMPONENT_SECTION_MAP`, path-pattern lists, etc.), but every consumer keeps a
second, hand-maintained copy behind a `try: from adapter_defaults import DEFAULTS as
_AD; X = _AD[...] except Exception: X = <hardcoded literal>` block. The issue names 4
such consumers (`architecture_slice.py`, `diff_rank.py`, `gate_blast_radius.py`,
`epic_autopilot.py`). Codebase exploration during refinement found the same pattern
in 2 more files not named in the issue (`deconflict.py`, `main_red_fixer.py`,
confirmed via existing parity tests in `tests/test_adapter.py`), plus a third,
differently-shaped duplication site in `architecture_slice.py` (plain hardcoded
literals with no `try`/`except` at all) that has **already drifted** from
`adapter_defaults.DEFAULTS` — a live instance of exactly the failure mode this issue
exists to prevent (see Requirement 3). This spec's scope is confirmed with the
product owner across three rounds of Q&A (below) to cover the full duplication
surface, not just the issue's illustrative 4-file list.

The fix: `adapter_defaults.DEFAULTS` becomes the only place these constants are
written as literals. Every consumer imports it unconditionally — a missing or broken
import now crashes at import time instead of silently falling back to a stale copy.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **Six consumer files lose their fallback-copy branch, not four** (Q&A #1). The
   issue's "4 files" list is illustrative, not a scope boundary — its own acceptance
   criteria are invariants over the whole `scripts/` tree ("no constant-fallback
   copies", "exactly one definition"). In scope:
   - `scripts/architecture_slice.py:23-53` — `COMPONENT_SECTION_MAP`
   - `scripts/diff_rank.py:55-65` — `SAFETY_PATH_PATTERNS`
   - `scripts/gate_blast_radius.py:85-96` — `MIGRATION_SEED_AUTH_PATTERNS`
   - `scripts/factory_core/epic_autopilot.py:314-325` — `_DEFAULT_EXCLUDE`,
     `_DEFAULT_SENSITIVE_KEYWORDS`
   - `scripts/factory_core/deconflict.py:13-19` — `_DEFAULT_MODELS_INIT`,
     `_DEFAULT_MIGRATIONS_DIR`
   - `scripts/factory_core/main_red_fixer.py:17-21` — `_DEFAULT_ALLOWED_PATHS`

   For each, delete the `try:`/`except Exception:` wrapper entirely and keep only
   the `try` body as an unconditional top-level statement:
   ```python
   # before
   try:
       from factory_core.adapter_defaults import DEFAULTS as _AD
       X = _AD["safety"]["x"]
   except Exception:
       X = ["hardcoded", "copy"]
   # after
   from factory_core.adapter_defaults import DEFAULTS as _AD
   X = _AD["safety"]["x"]
   ```
   (Files inside the `factory_core` package — `epic_autopilot.py`, `deconflict.py`,
   `main_red_fixer.py` — use their existing relative-import form, `from
   .adapter_defaults import DEFAULTS as _AD`; this is unchanged, only the `except`
   branch is deleted.) A broken or missing `adapter_defaults` module now raises
   `ImportError`/`ModuleNotFoundError` at import time, which is the "fail loudly"
   behavior the issue asks for.

2. **The issue's acceptance criterion 1 is corrected to match the actual exception
   type.** Every fallback in the codebase — the original 4, plus the 2 more found —
   catches `except Exception:`, never `except ImportError:` (confirmed by `grep -n
   "except Exception:" scripts/*.py scripts/factory_core/*.py`; zero matches for
   `except ImportError`). The verification for "no constant-fallback copies remain"
   is therefore: `grep -rn "except Exception:" scripts/ scripts/factory_core/`
   shows no hit immediately following a `from *adapter_defaults import DEFAULTS`
   re-export — i.e. no more `try:`-guarded DEFAULTS-derived module constant
   anywhere in the 6 files above.

3. **`scripts/architecture_slice.py` lines 105-117 are brought into single-source
   too, and their existing drift from `adapter_defaults.DEFAULTS` is fixed as part
   of this change** (Q&A #2 background research, Q&A #3):
   ```python
   # current — plain literals, no _AD reference, not even try/except
   _DEFAULT_SAFETY_KEYWORDS = r"migration|migrate|performance|perf|architectur|refactor"
   _DEFAULT_SENSITIVE_KEYWORDS = (
       r"trading|ibkr|live order|notional|authentication|authorization"
       r"|authn|authz|jwt|oauth|rbac"
   )
   _DEFAULT_EXCLUDE_PATHS = [
       "app/services/trading", "app/tasks/trading.py", "app/core/auth", "app/routers/auth",
   ]
   ```
   These are the config.yaml-absence fallback for `_get_safety_keywords()` /
   `_get_sensitive_keywords()` / `_get_exclude_paths()` (distinct from the
   `.factory/adapter.yaml`-absence fallback that `COMPONENT_SECTION_MAP` already
   handles at the top of the same file — see Requirement 5 for why that distinction
   doesn't matter here). Two confirmed, already-live drifts:
   - `_DEFAULT_SENSITIVE_KEYWORDS` ends `...|rbac"` — missing the trailing
     `|/auth` present in `adapter_defaults.DEFAULTS["safety"]["sensitive_keywords"]`
     (`"...|rbac|/auth"`).
   - `_DEFAULT_EXCLUDE_PATHS` has only the 4 trading/auth entries; it is missing
     `"dark-factory/"`, `".archon/"`, `"scheduler.sh"`, `"factory_core/"` from the
     8-entry `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]`.

   Fix: re-derive all three from `_AD["safety"]["dispatch_ceiling_keywords"]`,
   `_AD["safety"]["sensitive_keywords"]`, `_AD["safety"]["hard_exclude_paths"]`
   respectively (the module already binds `_AD = adapter_defaults.DEFAULTS` at line
   24 for `COMPONENT_SECTION_MAP` — reuse the same binding). This is a deliberate,
   intended safety-tightening (the fallback should reproduce the configured
   behavior, and today it silently narrows it) — call this out in the PR description
   so a reviewer doesn't flag the widened exclude/keyword set as an unrelated
   behavior change.

   **Verified non-breaking**: every existing test in `tests/test_architecture_slice.py`
   that exercises `_check_safety_fallback` (the only caller of these three
   constants) supplies its own `config.yaml` via the `make_config()` fixture with an
   explicit `hard_exclude_paths`/`sensitive_keywords`, which shadows the module
   fallback entirely (`_get_exclude_paths`/`_get_sensitive_keywords`/
   `_get_safety_keywords` only reach the `_DEFAULT_*` constant when the config key is
   absent). The three tests that *don't* use `make_config()`
   (`test_slice_bypasses_when_feature_disabled`, `test_slice_proceeds_when_feature_enabled`,
   `test_slice_cap_config_alone_is_inert`) pass empty `changed_files`/`labels`, so
   `_check_safety_fallback` has nothing to match regardless of what the fallback
   constants contain. No existing test result changes.

4. **`config/config.yaml` keeps its restated `dispatch_ceiling.keywords`,
   `epic_autopilot.sensitive_keywords`, and `epic_autopilot.hard_exclude_paths`
   entries; a new drift/parity test is added instead of deleting them** (Q&A #2).
   Verified: `scheduler.sh`'s config reader (`read_config`/`_set_cfg`, lines 40-60)
   is pure shell using `yq` with **no Python fallback** — if these three keys were
   removed from `config.yaml`, `yq` would return `null`, `_set_cfg` sets the
   exported env var to `""`, and:
   - `ABOVE_CEILING_KEYWORDS=""` feeds `grep -qiE "${ABOVE_CEILING_KEYWORDS}"`
     (scheduler.sh:282) — an empty `-E` pattern matches every string, so every M
     ticket would trip the dispatch ceiling. Silent, repo-wide behavior change.
   - `EPIC_AUTOPILOT_SENSITIVE_KEYWORDS=""` (scheduler.sh:85) would weaken the
     autopilot's sensitive-path gate the same way.

   Making `config.yaml` absence-safe for the shell path would require teaching
   `scheduler.sh` to shell out to Python to read `adapter_defaults.DEFAULTS` — a
   materially larger, separately-risked change out of proportion to this "size: S"
   ticket. `config.yaml` therefore stays the physical value for the shell consumer;
   drift is closed by a new test (Requirement 8) instead of by deletion.

   This reframes issue acceptance criterion 2: *"each constant has exactly one
   definition"* now means exactly one **authoritative Python** definition, in
   `adapter_defaults.DEFAULTS` — `config.yaml`'s copy is a test-locked mirror of it,
   not an independent source that can drift unnoticed.

5. **Out of scope: the per-project `.factory/adapter.yaml` override lookups.**
   Every consumer additionally exposes a `_xxx(clone_dir)` accessor (e.g.
   `_component_section_map`, `_safety_path_patterns`, `_hard_exclude_paths`,
   `_sensitive_keywords`, `_main_red_allowed_paths`, `_deconflict_models_init`) that
   does `try: from . import adapter; val = adapter.get(clone_dir, "...") ...  except
   Exception: pass; return <module constant>`. This is a *different* mechanism — a
   fail-open lookup of a target repo's optional `.factory/adapter.yaml` override,
   already covered by dedicated `*_adapter_override` tests in
   `tests/test_adapter.py` — not a shadow copy of a default value. It is unchanged
   by this ticket; its final fallback simply resolves to the now-single-sourced
   module constant instead of a module constant that used to have its own separate
   fallback branch.

6. **No sys.path fix is needed — verified empirically, not assumed.** The issue
   asks to "fix whatever sys.path issue motivated the fallbacks... make
   `factory_core` resolvable everywhere, as `cli.py` already is." Direct testing
   shows there is no such issue for any of the 6 files in scope:
   - `architecture_slice.py`, `diff_rank.py`, `gate_blast_radius.py` live directly
     in `scripts/`; running them as a script (`python3 .../scripts/foo.py --help`)
     from an unrelated CWD (`/tmp`) succeeds today — Python inserts the *script's
     own* directory (not CWD) at `sys.path[0]`, and `factory_core/` is a direct
     subdirectory of `scripts/`, so `import factory_core.adapter_defaults` resolves
     without any manual path manipulation. Confirmed by running both scripts from
     `/tmp` in this refinement session.
   - `epic_autopilot.py`, `deconflict.py`, `main_red_fixer.py` live *inside* the
     `factory_core` package and use relative imports (`from .adapter_defaults import
     DEFAULTS`), which never depend on `sys.path` at all once the package itself has
     been imported.
   - `factory_core/cli.py`'s explicit `sys.path.insert(0,
     str(Path(__file__).resolve().parents[1]))` is solving a different problem: it
     is *also inside* `factory_core/` but imports itself via the absolute dotted
     path (`from factory_core.board import ...`), which requires `scripts/` (the
     package's parent) on `sys.path` — a structural need none of the 6 files in
     scope share. `cli.py` is unaffected by this ticket.

   No code changes are made under this requirement; it exists to document that the
   sys.path clause of the issue's Solution section is satisfied by the current
   layout and does not block removing the fallback branches.

7. **`tests/test_adapter.py`'s two "parity" tests become dead weight and are
   removed**, not preserved: `test_components_parity` and
   `test_critical_diff_paths_parity` (lines ~51-63) assert
   `adapter_defaults.DEFAULTS[...] == <consumer's re-exported module constant>`.
   After Requirement 1, the consumer constant *is* `_AD[...]` — the same object,
   assigned directly, with no independent literal on the other side. The assertion
   becomes structurally always-true (there is nothing left it could ever catch) —
   the drift protection it used to provide is now provided by the language itself
   (a single assignment cannot drift from itself). Keeping a test that can never
   fail is misleading, not extra safety.

8. **New parity/drift tests are added to close every duplication site fixed in
   Requirements 3 and 4** (issue acceptance criterion 3), in `tests/test_adapter.py`:
   - `config/config.yaml`'s `.dispatch_ceiling.keywords`,
     `.epic_autopilot.sensitive_keywords`, and `.epic_autopilot.hard_exclude_paths`
     each equal the corresponding `adapter_defaults.DEFAULTS["safety"][...]` value.
   - `architecture_slice._DEFAULT_SAFETY_KEYWORDS`,
     `architecture_slice._DEFAULT_SENSITIVE_KEYWORDS`, and
     `architecture_slice._DEFAULT_EXCLUDE_PATHS` each equal the corresponding
     `adapter_defaults.DEFAULTS["safety"][...]` value (this test would have caught
     both drifts found in Requirement 3 had it existed before).
   Additionally, extend `tests/test_architecture_slice.py` with one behavioral test
   that omits `epic_autopilot.hard_exclude_paths` from the fixture config entirely
   (so `_get_exclude_paths` falls through to `_DEFAULT_EXCLUDE_PATHS`) and asserts a
   `changed_files=["dark-factory/scripts/foo.py"]` now correctly trips the safety
   fallback — proving the widened default from Requirement 3 actually takes effect,
   not just that the two lists are equal.

9. **All existing consumer tests continue to pass with no expected-value changes**
   beyond what Requirements 3, 7, and 8 describe (issue acceptance criterion 4):
   `tests/test_adapter.py`'s `*_default_parity` / `*_adapter_override` tests (all 7
   consumers), `tests/test_architecture_slice.py`, `tests/test_epic_autopilot.py`.
   These tests already assert behavior against `adapter_defaults.DEFAULTS`
   directly (not against the old hardcoded literals), so they are unaffected by
   deleting the `except` branches — they were already exercising the `try` path in
   every CI run today (the `except` branches are dead code in the test suite as it
   stands, which is itself a signal that removing them is safe).

## Architecture / Approach

### The 6 consumer files (Requirement 1)

Mechanical, identical shape in each file: delete the `except Exception:` clause and
its body; leave the `try:` body's two lines as unconditional top-level statements.
No behavior change for any currently-passing code path — the `except` branch only
ran when the `try` failed, which none of today's tests, CI runs, or documented
invocations exercise (Requirement 6 explains why the import reliably succeeds).

### `scripts/architecture_slice.py` (Requirements 3, 6, 8)

- Lines 105-117 (`_DEFAULT_SAFETY_KEYWORDS`, `_DEFAULT_SENSITIVE_KEYWORDS`,
  `_DEFAULT_EXCLUDE_PATHS`): replace literals with
  `_AD["safety"]["dispatch_ceiling_keywords"]`, `_AD["safety"]["sensitive_keywords"]`,
  `_AD["safety"]["hard_exclude_paths"]`. `_AD` is already bound at module scope
  (line 24, `from factory_core.adapter_defaults import DEFAULTS as _AD`) for
  `COMPONENT_SECTION_MAP` — reuse it, do not re-import.
- No change to `_component_section_map()`, `_get_safety_keywords()`,
  `_get_sensitive_keywords()`, `_get_exclude_paths()`, or any caller — only the
  fallback constants they read move to a single source.

### `config/config.yaml` (Requirement 4)

No content change. `adapter_defaults.DEFAULTS["safety"]` remains the authoritative
value; `config.yaml`'s copy is guarded by the new drift test (Requirement 8) so any
future hand-edit that diverges the two fails CI immediately instead of silently
weakening `scheduler.sh`'s shell-side safety gates.

### `tests/test_adapter.py` (Requirements 7, 8)

- Delete `test_components_parity` and `test_critical_diff_paths_parity`.
- Add `test_config_yaml_dispatch_ceiling_keywords_parity`,
  `test_config_yaml_sensitive_keywords_parity`,
  `test_config_yaml_hard_exclude_paths_parity` — each loads `config/config.yaml`
  (`yaml.safe_load`) and asserts the relevant dotted value equals
  `adapter_defaults.DEFAULTS["safety"][...]`.
- Add `test_architecture_slice_default_safety_keywords_parity`,
  `test_architecture_slice_default_sensitive_keywords_parity`,
  `test_architecture_slice_default_exclude_paths_parity` — import
  `architecture_slice` and assert its three `_DEFAULT_*` constants equal
  `adapter_defaults.DEFAULTS["safety"][...]`.
- All other tests in the file (the 7-consumer `*_default_parity` /
  `*_adapter_override` suite) are unchanged.

### `tests/test_architecture_slice.py` (Requirement 8)

Add one new test (name: `test_fallback_on_hard_exclude_path_default_when_config_omits_key`)
that writes a `config.yaml` fixture with `dispatch_ceiling`/`epic_autopilot` present
but `hard_exclude_paths` omitted, passes `changed_files=["dark-factory/scripts/foo.py"]`,
and asserts `result.fallback` is `True` with `"safety_file"` in
`result.fallback_reason` — this only passes once `_DEFAULT_EXCLUDE_PATHS` includes
`"dark-factory/"` (Requirement 3's fix).

## Alternatives considered

1. **Strictly limit the fix to the 4 files the issue names**, filing a follow-up for
   `deconflict.py` / `main_red_fixer.py` / the `architecture_slice.py` literals.
   Rejected (Q&A #1, #3) — the issue's acceptance criteria are written as
   tree-wide invariants, not a checklist; leaving known instances of the exact same
   defect in place (one of them already actively drifted) would let the ticket close
   without actually being true. All three additional sites are low-risk,
   self-verifying changes already covered or extended by parity tests.

2. **Delete the restated safety keys from `config.yaml`** (issue's option (a)).
   Rejected (Q&A #2) — `scheduler.sh` reads these three keys via `yq` with no
   Python fallback; an absent key resolves to an empty string, and an empty
   `grep -E` pattern matches everything, silently breaking the dispatch-ceiling and
   sensitive-keyword gates it's supposed to enforce. Making the shell path
   Python-aware to compensate is a materially larger change than this ticket's
   stated scope.

3. **Leave the `architecture_slice.py` `_DEFAULT_*` literals alone** on the
   grounds that they're a config.yaml-absence fallback, not the `except
   ImportError`-shaped pattern the issue's Problem section literally describes.
   Rejected (Q&A #3) — the issue's stated goal is that every safety constant has
   exactly one definition; these literals violate that regardless of the shape of
   the mechanism that reads them, and two of the three have already silently
   drifted from `adapter_defaults.DEFAULTS`, which is the exact risk ("drift becomes
   a crash, not a silent skew in a safety gate") the issue exists to eliminate.

## Open questions (non-blocking)

- Whether to also collapse `epic_autopilot.py`'s two near-duplicate accessor
  functions for hard-exclude paths (`_hard_exclude_paths()`, which reads
  `.factory/adapter.yaml` then falls back to `_DEFAULT_EXCLUDE`, and
  `_load_exclude_paths()`, a separate "legacy" accessor that additionally tries a
  direct `yq` read of `config.yaml` before falling back to the same
  `_DEFAULT_EXCLUDE`) is left to the implementer. Both already terminate at the
  single-sourced `_DEFAULT_EXCLUDE` after this ticket, so neither is a duplication
  risk; unifying them is a separate readability question outside this issue's
  acceptance criteria.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the working tree at
  the start of this refinement (this branch was cut from the same base commit as
  sibling `refine/*` branches before any spec had merged to `main`) — this spec is
  written as a new file, matching the precedent of
  `2026-07-07-budget-gate-consolidation-design.md` on a sibling branch.
- **[ASSUMPTION]** "Fails loudly" (issue Solution section) means letting the normal
  `ImportError`/`ModuleNotFoundError` propagate from the unconditional `from
  factory_core.adapter_defaults import DEFAULTS as _AD` statement — no custom error
  message, `sys.exit`, or wrapping exception type is added. Every one of these 6
  files is only ever invoked as part of the factory's own gate/scheduling pipeline
  (never as a library consumed by an external, less-trusted caller), so a raw
  traceback is an acceptable, in-family failure mode, consistent with how `cli.py`
  already lets its own imports fail.
- **[ASSUMPTION]** `tests/test_epic_autopilot.py:90`'s locally-scoped
  `sensitive_keywords=r"trading|ibkr|order|jwt|/auth"` (an explicit parameter to a
  single test of `hard_excluded()`) is a deliberate test-local override, not a
  duplicate of `adapter_defaults.DEFAULTS`, and is out of scope for this ticket.

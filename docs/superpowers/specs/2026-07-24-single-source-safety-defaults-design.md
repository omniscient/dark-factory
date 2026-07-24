# Single-Source Safety Constants in `adapter_defaults.py`

**Issue:** omniscient/dark-factory#184
**Status:** design spec — re-refined 2026-07-24 against current `main` after the 2026-07-07 spec
was discarded as stale (351 commits behind at discard time; see issue comment 2026-07-22).
**Provenance:** superseded spec preserved at tag `archive/refine-184-stale-spec-2026-07-07`
(commit `a82cb56`). This document re-derives every line number, drift claim, and fallback
inventory from scratch against current `main` per the operator's explicit instruction not to
trust the prior spec's citations.
**Safety-sensitive:** yes. Per `CLAUDE.md`, this ticket is its own reviewed vehicle for a
safety-constant change; see "Hard constraints" below for the binding operator conditions this
spec must satisfy.

---

## Overview / Problem Statement

`scripts/factory_core/adapter_defaults.py` declares itself the single source of truth for the
factory's safety constants (`sensitive_keywords`, `hard_exclude_paths`, `critical_diff_paths`,
`migration_seed_auth_patterns`, `main_red_allowed_paths`, `deconflict.*`, `components`), but six
consumer files each keep a `try: from adapter_defaults import X ... except Exception: X =
<hardcoded literal copy>` fallback block that shadow-copies part of `DEFAULTS`. Because the
fallback is a literal, not a derivation, it can silently drift from the canonical value — and, as
verified below, four of the eleven fallback sites across those six files already have.

This refinement pass re-verified every claim in the issue and the discarded spec against current
`main`, and found two things the discarded spec did not:

1. **A live drift inside `config/config.yaml` itself** (not just in a `.py` fallback literal) —
   `epic_autopilot.hard_exclude_paths` has silently fallen 7 entries behind
   `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]`, in the fail-closed direction.
2. **A newly-introduced fail-open hole** that a naive "just delete the `except` block" fix would
   create in `scripts/gate_blast_radius.py`, a hard blocking gate — the exact class of regression
   the operator's binding comment warned about.

Both are addressed explicitly below, per-fallback, as the operator's 2026-07-22 comment requires.

## Hard constraints (binding, from the 2026-07-22 operator comment)

1. **No gate may be weakened as a side effect.** Consolidating *where* a constant is defined must
   not change its *value*, its *default*, or the *fail-closed behaviour* of any `gate_*`, breaker,
   or budget path that reads it.
2. **Every removed fallback needs individual justification.** For each one, this spec must state
   explicitly what happens on import failure after the change, and why raising (or whatever
   replaces the fallback) is safe there. A blanket "the import cannot fail" is not sufficient.

## Requirements

- `adapter_defaults.py` remains the single implementation; no consumer keeps a hardcoded literal
  duplicate of any `DEFAULTS` value used only as an import-failure fallback ("Pattern 1" below).
- Each of the 6 consumer files' Pattern-1 fallback removal is individually justified against what
  actually happens on import failure in that file's real invocation context (see "Per-file
  disposition").
- `gate_blast_radius.py` — a hard blocking gate — keeps its documented "exit 0 always, caller
  reads STATUS from output" contract even when `adapter_defaults` fails to import; the fallback
  is replaced with an explicit fail-closed `STATUS: HUMAN_REQUIRED` emission, not deleted outright
  and not left to crash.
- `config/config.yaml` keeps its three restated safety keys (`dispatch_ceiling.keywords`,
  `epic_autopilot.sensitive_keywords`, `epic_autopilot.hard_exclude_paths`) — `scheduler.sh`'s
  config reader is pure shell (`yq`) with no Python fallback, so deleting these keys would make an
  absent value resolve to an empty string, and an empty `grep -E` pattern matches everything,
  silently breaking those shell-side gates. Instead:
  - `epic_autopilot.hard_exclude_paths` content is corrected from its current 8 entries to full
    15-entry parity with `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]` (see Drift
    Findings — this is a real, already-live gap, not hypothetical).
  - A parity test is added asserting all three keys equal their `adapter_defaults.DEFAULTS`
    counterparts byte-for-byte, so future drift fails CI instead of sitting silent.
- `architecture_slice.py`'s separate `_DEFAULT_SAFETY_KEYWORDS` / `_DEFAULT_SENSITIVE_KEYWORDS` /
  `_DEFAULT_EXCLUDE_PATHS` literals (a config.yaml-*absence* fallback, structurally distinct from
  the Pattern-1 import fallback — see "Two distinct fallback patterns") are re-derived from
  `adapter_defaults.DEFAULTS["safety"]` instead of hand-copied, fixing two more live drifts
  (missing `/auth`; missing 11 of 15 `hard_exclude_paths` entries, including `dark-factory/`
  itself).
- The per-project `.factory/adapter.yaml` override mechanism (`adapter.get(...)`, its own
  independent fail-open-to-module-constant design) is explicitly **out of scope** — different
  mechanism, already tested, not what the issue's `except ImportError` acceptance criterion
  targets.
- Existing `tests/test_adapter.py` parity tests are extended/added so every corrected site is
  pinned by a real assertion (see Testing).

## Two distinct fallback patterns (why this distinction matters)

The issue and the discarded spec both talk about "the fallback pattern" as one thing. Tracing the
actual code shows there are two, and only one of them is this issue's target:

- **Pattern 1 — import-failure shadow copy** (the issue's actual target):
  `try: from factory_core.adapter_defaults import DEFAULTS as _AD; X = _AD[...] except Exception:
  X = <hardcoded literal>`. This only protects against `adapter_defaults.py` itself failing to
  import (a broken package — the file has zero imports of its own, so this can only happen from a
  `sys.path`/packaging problem). This is what gets deleted.
- **Pattern 2 — adapter-override-absence fallback** (already legitimate, untouched):
  `try: from . import adapter; val = adapter.get(clone_dir, "safety.X") except Exception: pass;
  return <module constant>`. This protects against `.factory/adapter.yaml` being absent or not
  overriding a key — a normal, expected condition for most target repos, not a broken-package
  condition. This is `adapter.get()`'s own well-tested fail-open-to-default design and is
  explicitly out of scope per the Requirements above.

Every one of the 6 files' Pattern-2 use-time calls already depends on `factory_core.adapter`
importing successfully, and `adapter.py` itself does `from . import adapter_defaults`
**unconditionally today, with no try/except at all** (`scripts/factory_core/adapter.py:3`). That
is the established precedent this spec extends to the 6 outlier Pattern-1 sites — it is not a
novel risk pattern being introduced.

## Drift Findings (why "fail loudly" is a net safety improvement, not just a style cleanup)

Verified against current `main`, byte-for-byte comparison of each Pattern-1 fallback literal to
the corresponding `adapter_defaults.DEFAULTS` value:

| Site | Byte-identical to DEFAULTS? | Drift |
|---|---|---|
| `architecture_slice.py` `COMPONENT_SECTION_MAP` | Yes | — |
| `diff_rank.py` `SAFETY_PATH_PATTERNS` | **No** | missing 8 of 14 `critical_diff_paths` (all the `.claude/skills`/`settings`/`mcp`/`plugins`/`.factory/hooks` entries added for #46) |
| `diff_rank.py` `SKILL_SECURITY_TOKENS` | Yes | — |
| `gate_blast_radius.py` `MIGRATION_SEED_AUTH_PATTERNS` | **No** | missing 7 of 11 `migration_seed_auth_patterns` (same #46 entries) |
| `gate_blast_radius.py` `SKILL_SECURITY_TOKENS` | Yes | — |
| `epic_autopilot.py` `_DEFAULT_EXCLUDE` | **No** | missing 7 of 15 `hard_exclude_paths` (same #46 entries) — comment on the canonical value says these entries "forward-protect epic_autopilot regardless of its enabled flag"; the fallback undermines exactly that guarantee |
| `epic_autopilot.py` `_DEFAULT_SENSITIVE_KEYWORDS` | Yes | — |
| `deconflict.py` `_DEFAULT_MODELS_INIT` / `_DEFAULT_MIGRATIONS_DIR` | Yes | — |
| `main_red_fixer.py` `_DEFAULT_ALLOWED_PATHS` | Yes | — |
| `architecture_slice.py` `_DEFAULT_SENSITIVE_KEYWORDS` (config-absence fallback, Pattern 2-adjacent, see below) | **No** | missing trailing `\|/auth` |
| `architecture_slice.py` `_DEFAULT_EXCLUDE_PATHS` (same) | **No** | only 4 of 15 `hard_exclude_paths` entries; missing `dark-factory/` itself |
| `config/config.yaml` `epic_autopilot.hard_exclude_paths` | **No** | only 8 of 15 entries; missing the same 7 #46 entries |

Every drift found is in the same direction: the stale copy is **narrower** than the canonical
value, i.e. every one of these fallbacks, if it had ever fired, would have silently weakened a
safety filter rather than strengthened it. Removing them (with the one exception requiring
special handling below) is a strict fail-closed improvement, not a neutral refactor.

The `config/config.yaml` drift is newly found in this refinement pass (the discarded 2026-07-07
spec only found the `architecture_slice.py` literal drift, not this one). It is currently masked
for dark-factory's own self-target instance because `.factory/adapter.yaml` supplies a full
9-entry override for `safety.hard_exclude_paths` that `epic_autopilot.py`'s `_load_exclude_paths()`
checks before falling through to `config.yaml`'s `yq`-read value — but any target repo that
doesn't separately override that key (verified in code: `_load_exclude_paths()` returns
`config.yaml`'s list directly and never falls through further once it gets a non-empty list) would
silently run `epic_autopilot`'s fail-closed candidate-exclusion filter without skill/settings/
hooks/plugin/MCP protection. `epic_autopilot.enabled` is `false` today (kill-switch), so this is
dormant, not exploited — but it is real, present-day drift in a committed file, not a
hypothetical.

## Per-file disposition

For each Pattern-1 fallback site, what happens on import failure after removal, and why that's
safe:

### `scripts/diff_rank.py` — plain unconditional import

Both blocks (`SAFETY_PATH_PATTERNS`, `SKILL_SECURITY_TOKENS`) lose their `except Exception:`
branch; on import failure the process raises `ModuleNotFoundError`/`ImportError` uncaught.

**Why safe:** `diff_rank.py` is invoked standalone from `commands/dark-factory-conformance.md` and
`commands/dark-factory-code-review.md`, both of which already wrap the call with an explicit,
logged shell-level fallback — `... || echo "diff_rank: ranking failed (...) — using fmt-filtered
diff"` (conformance) / `"... using raw diff"` (code-review). `diff_rank.py` is not itself a
pass/fail gate; it only affects which file diffs a downstream reviewer sees prioritized vs.
truncated. A crash degrades to a coarser, still-reviewed diff with a logged reason — no silent
bypass, no behavior change to any blocking decision.

### `scripts/gate_blast_radius.py` — fail-closed catch, NOT a plain unconditional import

This file needs different treatment from the other five because it is a hard blocking gate with
no shell-level safety net around its invocation (`commands/dark-factory-validate.md` Phase 0 pipes
its stdout straight to `blast.md` with no `||` wrapper), and its own docstring advertises a
contract every caller depends on: `"Exit 0 always — the caller reads STATUS from the output."`

**The risk a naive removal would introduce:** if the module-level import were made unconditional
and it ever raised, `gate_blast_radius.py` would crash before `main()` runs, before any stdout is
written. `blast.md` ends up empty. `validate.md`'s `BLAST_STATUS=$(grep '^STATUS:' blast.md | cut
... || true)` yields `""` (the `|| true` swallows grep's exit-1-on-no-match). `[ "$BLAST_STATUS" =
"HUMAN_REQUIRED" ]` is false for an empty string, so the blocking branch is silently skipped — the
gate fails **open** on a script crash. This is a *new* risk: today, `gate_blast_radius.py` is
protected at every layer (this module-level fallback plus a separate use-time Pattern-2
`adapter.get()` call independently wrapped in its own `except Exception: pass`), so a broken
`adapter_defaults.py` today still produces a valid, parseable (if narrower) `STATUS:` line. Only
removing this fallback naively turns "degrade to a stale-but-functional gate" into "silent pass."

**Fix:** replace the `except Exception:` branch's hardcoded literal with an explicit fail-closed
emission that preserves the advertised contract:

```python
try:
    from factory_core.adapter_defaults import DEFAULTS as _AD
    MIGRATION_SEED_AUTH_PATTERNS = [
        re.compile(p) for p in _AD["safety"]["migration_seed_auth_patterns"]
    ]
except Exception as e:
    # Fail closed: the module's own docstring promises "exit 0 always, caller reads
    # STATUS from output" — a bare crash here would break that promise and leave
    # validate.md's `[ "$BLAST_STATUS" = "HUMAN_REQUIRED" ]` check silently unmatched
    # (empty stdout -> gate fails open, not closed). Emit the same verdict shape
    # main() prints, with SEVERITY: critical, instead of a hardcoded pattern-list copy.
    print("STATUS: HUMAN_REQUIRED")
    print("GATE_TYPE: blast")
    print("FINDINGS_COUNT: 0")
    print("SEVERITY: critical")
    print("---")
    print(f"TRIGGER: blast-gate self-check failed — could not load safety patterns from "
          f"factory_core.adapter_defaults ({type(e).__name__}: {e}); blocking pending human review")
    print("TRIGGERED_FILES:")
    print("LINES_CHANGED: 0")
    sys.exit(0)
```

The second fallback in this file (`SKILL_SECURITY_TOKENS`, lines ~122-127) is byte-identical to
`adapter_defaults.SKILL_SECURITY_TOKENS` today and is only reached *after* the block above already
succeeded (same module, sequential execution) — if the import above failed, execution never
reaches this second block (the `sys.exit(0)` already fired). No separate handling needed for it
beyond removing its literal fallback the same way as the other Pattern-1 sites, since it's
unreachable in the failure case this spec cares about.

**Explicitly deferred, not bundled into this ticket:** `commands/dark-factory-validate.md`'s
`BLAST_STATUS` check still fails open on *any other* empty/unparseable `blast.md` (e.g. `python3`
missing from `PATH`, OOM/SIGKILL, disk-full truncation, an unhandled exception elsewhere in
`main()`) — none of which this refactor introduces or fixes. Hardening the shell-side check to
treat empty/unrecognized output as `HUMAN_REQUIRED` is a legitimate, separate gate-hardening
change that deserves its own reviewed ticket per `CLAUDE.md`'s "gate changes get their own
reviewed ticket" rule; folding it into a defaults-refactor ticket would be scope creep the
conformance gate should flag. File as a follow-up.

### `scripts/factory_core/epic_autopilot.py` — plain unconditional import

Both `_DEFAULT_EXCLUDE` and `_DEFAULT_SENSITIVE_KEYWORDS` lose their fallback branch.

**Why safe:** invoked via `scheduler.sh:1288`: `AP_OUT=$(python3 "$FACTORY_CORE_CLI"
epic-autopilot --once 2>&1) || true`. Any unhandled exception in `main_once()` — not just an
import failure; there is no other top-level exception handling in this path either — is *already*
swallowed today by this `|| true`, and the comment directly above it states the design intent:
"Fail-soft: never abort the loop." `epic_autopilot.enabled` defaults to `false` (kill-switch,
"ship OFF" per `config.yaml`). Raising on import failure changes nothing about the scheduler
loop's resilience; it was already fail-soft to any exception. And per the Drift Findings above,
`_DEFAULT_EXCLUDE` was itself a drifted, narrower-than-canonical fallback — removing it closes a
latent (dormant, since the feature is off) fail-open gap rather than opening one.

### `scripts/factory_core/main_red_fixer.py` — plain unconditional import

`_DEFAULT_ALLOWED_PATHS` loses its fallback branch.

**Why safe:** invoked via `entrypoint.sh:625`: `python3 .../cli.py main-red-fix --once || true` —
identical swallow-everything pattern to `epic_autopilot.py`. `main_red_autofix.enabled` also
defaults to `false` ("ships OFF" per `config.yaml`). This fallback was already byte-identical to
`DEFAULTS["safety"]["main_red_allowed_paths"]`, so removal changes zero behavior in the success
case; in the failure case, behavior is identical to any other already-uncaught exception in this
fire-and-forget, kill-switched-off cycle.

### `scripts/factory_core/deconflict.py` — plain unconditional import

`_DEFAULT_MODELS_INIT` / `_DEFAULT_MIGRATIONS_DIR` lose their fallback branch.

**Why safe:** invoked via `entrypoint.sh:518`: `deconflict --issue "$ISSUE_NUM" || return $?` —
this call site does **not** swallow; a failure propagates into `entrypoint.sh`'s own failure/
retry/circuit-breaker handling (`trap ... ERR`, `on_failure()`). That is the *correct*, more
fail-closed behavior for a broken dependency: escalate rather than mechanically auto-resolve a
merge conflict on a stale constant. This fallback was already byte-identical to
`DEFAULTS["deconflict"]`, so success-path behavior is unchanged; the failure path moves from
"silently keep merging with a hardcoded constant" to "escalate," which is strictly safer for a
function that decides whether to auto-resolve `git checkout --theirs` on a conflicted file.

### `scripts/architecture_slice.py` — plain unconditional import (for `COMPONENT_SECTION_MAP`)

`COMPONENT_SECTION_MAP`'s Pattern-1 fallback (lines ~23-53) loses its `except Exception:` branch.

**Why safe:** on import failure the process raises; this constant is consumed only to select
which `ARCHITECTURE.md` section gets sliced into a phase agent's context window — a token-budget/
informational concern, not a security or merge gate. This fallback was already byte-identical to
`DEFAULTS["components"]`, so success-path behavior is unchanged.

This file's *separate* config-absence fallback (`_DEFAULT_SAFETY_KEYWORDS` /
`_DEFAULT_SENSITIVE_KEYWORDS` / `_DEFAULT_EXCLUDE_PATHS`, lines ~107-117) is not this Pattern-1
mechanism at all — see next section.

## `architecture_slice.py`'s separate config-absence literals — re-derive, don't just leave

`_DEFAULT_SAFETY_KEYWORDS`, `_DEFAULT_SENSITIVE_KEYWORDS`, and `_DEFAULT_EXCLUDE_PATHS`
(`scripts/architecture_slice.py:107-117`) are a **third**, structurally distinct pattern: plain
hardcoded module constants (no `try`/`except` at all, no `_AD` reference) used as the
config.yaml-*absence* fallback inside `_get_safety_keywords()` / `_get_sensitive_keywords()` /
`_get_exclude_paths()` (lines ~161-173), which feed `_check_safety_fallback()` — a real gate: it
decides whether architecture-slicing degrades to full-doc mode for a safety-sensitive change.

These are already confirmed drifted on current `main`:
- `_DEFAULT_SENSITIVE_KEYWORDS` ends `...|authn|authz|jwt|oauth|rbac` — missing the trailing
  `|/auth` present in `DEFAULTS["safety"]["sensitive_keywords"]`.
- `_DEFAULT_EXCLUDE_PATHS` has only 4 entries (`app/services/trading`, `app/tasks/trading.py`,
  `app/core/auth`, `app/routers/auth`) vs. `DEFAULTS["safety"]["hard_exclude_paths"]`'s 15 —
  missing `dark-factory/` itself, `.archon/`, `scheduler.sh`, `factory_core/`, and all 7 #46
  skill-security entries.

**Fix:** re-derive all three from `adapter_defaults.DEFAULTS["safety"]` at module scope (the same
`_AD` import this file already uses for `COMPONENT_SECTION_MAP`, extended to these three keys)
instead of hand-copied literals:

```python
_DEFAULT_SAFETY_KEYWORDS = _AD["safety"]["dispatch_ceiling_keywords"]
_DEFAULT_SENSITIVE_KEYWORDS = _AD["safety"]["sensitive_keywords"]
_DEFAULT_EXCLUDE_PATHS = list(_AD["safety"]["hard_exclude_paths"])
```

placed inside the same `try:` block as `COMPONENT_SECTION_MAP` (so they share its import-failure
disposition — informational/config-absence fallback content, not a hard gate on their own; if the
import fails, `COMPONENT_SECTION_MAP`'s own removal already makes the whole module fail loudly, so
these three never get a chance to matter in that scenario either).

## `config/config.yaml` — correct the drift, then pin it

`epic_autopilot.hard_exclude_paths` is corrected from its current 8 entries to the full 15-entry
list matching `adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]`:

```yaml
hard_exclude_paths:
  - "dark-factory/"
  - ".archon/"
  - "scheduler.sh"
  - "factory_core/"
  - "app/services/trading"
  - "app/tasks/trading.py"
  - "app/core/auth"
  - "app/routers/auth"
  - ".claude/skills/"
  - ".claude/settings.json"
  - ".claude/settings.local.json"
  - ".mcp.json"
  - ".claude/plugins/"
  - ".claude-plugin/"
  - ".factory/hooks/"
```

This widens `epic_autopilot`'s candidate-exclusion set for any target repo that doesn't separately
override `safety.hard_exclude_paths` in its own `.factory/adapter.yaml`. That is the intended,
correct effect — it closes the #46 protection gap that was silently narrowed — and per the
operator's constraint ("must not change... the fail-closed behaviour of any gate... path") this is
required, not merely permitted: leaving the drift in place while consolidating around `DEFAULTS`
elsewhere would itself be the prohibited side-effect weakening. The other two restated keys
(`dispatch_ceiling.keywords`, `epic_autopilot.sensitive_keywords`) are already byte-identical to
`DEFAULTS` and need no content change.

## Testing

- **New/updated parity tests in `tests/test_adapter.py`:**
  - `test_config_yaml_hard_exclude_paths_parity` — asserts `config/config.yaml`'s
    `epic_autopilot.hard_exclude_paths` equals `adapter_defaults.DEFAULTS["safety"]
    ["hard_exclude_paths"]` byte-for-byte.
  - `test_config_yaml_dispatch_ceiling_keywords_parity` — same shape for
    `dispatch_ceiling.keywords` vs. `DEFAULTS["safety"]["dispatch_ceiling_keywords"]`.
  - `test_config_yaml_sensitive_keywords_parity` — same shape for
    `epic_autopilot.sensitive_keywords` vs. `DEFAULTS["safety"]["sensitive_keywords"]`.
  - `test_architecture_slice_config_absence_defaults_parity` — asserts
    `architecture_slice._DEFAULT_SAFETY_KEYWORDS` / `_DEFAULT_SENSITIVE_KEYWORDS` /
    `_DEFAULT_EXCLUDE_PATHS` equal their `DEFAULTS["safety"]` counterparts, replacing any prior
    test (if one existed) that only checked the drifted literal against itself.
  - The 8 existing `test_*_default_parity` tests already in `tests/test_adapter.py`
    (`test_components_parity`, `test_critical_diff_paths_parity`,
    `test_safety_path_patterns_default_parity`, `test_migration_seed_auth_patterns_default_parity`,
    `test_hard_exclude_paths_default_parity`, `test_sensitive_keywords_default_parity`,
    `test_main_red_allowed_paths_default_parity`, `test_deconflict_models_init_default_parity` /
    `test_deconflict_migrations_dir_default_parity` — confirmed present on current `main`) remain
    unmodified: they exercise the use-time `adapter.get()` (Pattern 2) path, which this change
    doesn't touch. Note none of these ever exercised the Pattern-1 fallback branch either (the
    import always succeeds in test), which is exactly how the drift in the Drift Findings table
    went undetected until this pass.
- **New regression test for `gate_blast_radius.py`'s fail-closed catch:** simulate the
  `adapter_defaults` import failing (e.g. monkeypatch `sys.modules['factory_core.adapter_defaults']`
  to raise, or run the module via `importlib` against a poisoned `sys.path`) and assert stdout
  contains `STATUS: HUMAN_REQUIRED` and the process exits 0 — so the restored contract is enforced
  by CI, not just by inspection.
- **`grep -r "except Exception:" scripts/` manual audit** (issue's acceptance criterion, corrected
  from its literal `except ImportError` wording — every real site in this repo uses
  `except Exception:`, confirmed during the discarded spec's pass and re-confirmed here) shows: no
  remaining Pattern-1 hardcoded-literal-copy blocks; the one remaining `try/except` near an
  `adapter_defaults` import (`gate_blast_radius.py`'s fail-closed catch) is a control-flow/
  error-handling path, not a constant-fallback copy, and is itself covered by the regression test
  above.
- Existing blast-radius, slice, diff-rank, deconflict, epic-autopilot, and main-red-fixer test
  suites (`tests/test_blast_radius.py`, `tests/test_architecture_slice.py`,
  `tests/test_diff_rank.py`, `tests/test_factory_core_deconflict.py`,
  `tests/test_epic_autopilot.py`, `tests/test_main_red_fixer.py` — filenames confirmed present in
  `tests/` as of this refinement pass) must still pass unmodified in their success-path
  assertions, since every Pattern-1 removal is designed to be a no-op in the non-broken-import
  case.

## Architecture / Approach

Mechanical per-site fix, not a redesign:
1. Delete the `except Exception: <hardcoded literal>` branch in 5 of 6 files (`diff_rank.py`,
   `epic_autopilot.py`, `deconflict.py`, `main_red_fixer.py`, `architecture_slice.py`'s
   `COMPONENT_SECTION_MAP` block), leaving the `try` body's import unconditional.
2. In `gate_blast_radius.py`, replace (don't delete) the `except Exception:` branch for
   `MIGRATION_SEED_AUTH_PATTERNS` with the fail-closed `STATUS: HUMAN_REQUIRED` emission shown
   above; its `SKILL_SECURITY_TOKENS` block loses its literal fallback the same as the other
   Pattern-1 sites (unreachable in the failure path once the fix above fires).
3. Re-derive `architecture_slice.py`'s `_DEFAULT_SAFETY_KEYWORDS` / `_DEFAULT_SENSITIVE_KEYWORDS` /
   `_DEFAULT_EXCLUDE_PATHS` from `_AD["safety"]` instead of hand-copying.
4. Correct `config/config.yaml`'s `epic_autopilot.hard_exclude_paths` to 15-entry parity.
5. Add the parity/regression tests listed above.

No sys.path fix is needed anywhere: verified empirically that all 6 files' `factory_core`
resolution is checkout/CWD-independent — `diff_rank.py`/`gate_blast_radius.py` rely on Python's
own automatic insertion of the invoked script's directory onto `sys.path` (a language guarantee,
not an environment assumption, reinforced by their own explicit `sys.path.insert(0, ...)` calls);
`epic_autopilot.py`/`deconflict.py`/`main_red_fixer.py` are only ever reached through
`factory_core/cli.py`, whose `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` is
relative to `cli.py`'s own location, not a hardcoded `/opt` path — structurally different from
`entrypoint.sh`'s `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` env-var-defaulted `/opt/dark-factory/...`
paths (the operator's cited counter-example), which really do break on a bare checkout because
they're an *environment* assumption, not a *language/structural* one. `cli.py`'s existing
bootstrap solves that different, unrelated problem and needs no change here.

## Alternatives Considered

- **Delete `config.yaml`'s restated keys entirely, let absent-key mean "use `adapter_defaults`
  default"** (the issue's option (a)). Rejected: `scheduler.sh`'s `yq`-based shell reader has no
  Python fallback path; an absent `dispatch_ceiling.keywords` or `epic_autopilot.sensitive_keywords`
  resolves to an empty string in shell, and an empty `grep -E` pattern matches everything —
  silently defeating those gates rather than protecting them. This was the discarded spec's Q2
  conclusion and re-verification found no reason to revisit it.
- **Leave `gate_blast_radius.py`'s fallback as a bare unconditional import, matching the other 5
  files.** Rejected: uniquely among the 6 files, this one has no shell-level safety net around its
  invocation and advertises an "exit 0 always" contract every caller relies on; a bare crash
  silently flips the gate from fail-closed to fail-open. Verified concretely via the
  `validate.md` shell trace in "Per-file disposition" above.
- **Fix the fail-open risk by hardening `commands/dark-factory-validate.md`'s shell-side
  `BLAST_STATUS` check instead of (or in addition to) the Python-side fix.** Deferred to a
  follow-up ticket: the shell-side gap is broader than this refactor's blast radius (it fires on
  *any* empty/unparseable `blast.md`, not just an `adapter_defaults` import failure — process
  crash, OOM, disk-full, etc.), and `validate.md` is itself a gate-consuming command surface that
  deserves its own dedicated review per `CLAUDE.md`'s "gate changes get their own reviewed ticket"
  rule. The self-contained Python-side fix fully discharges this ticket's obligation (no fail-open
  regression introduced by this change); the broader shell-side hardening is real but separable
  value, not a blocker here.
- **Widen `config.yaml`'s `epic_autopilot.hard_exclude_paths` fix into a full `.factory/adapter.yaml`
  audit across all dark-factory-managed target repos (e.g. MarketHawk).** Rejected as out of
  scope: this spec can only verify and correct state inside this repo's own clone; MarketHawk's
  `adapter.yaml` isn't visible here, and epic_autopilot is kill-switched off by default, so there is
  no urgent forcing function to reach into a separate repo. Flagged as a note for whoever operates
  the MarketHawk instance, not a task this ticket performs.

## Open Questions (non-blocking)

- Should `commands/dark-factory-validate.md`'s `BLAST_STATUS` check be hardened to fail closed on
  *any* empty/unparseable `blast.md`, independent of cause? Recommended as a follow-up ticket (see
  Alternatives Considered); not blocking for #184.
- Does MarketHawk's own `.factory/adapter.yaml` already override `safety.hard_exclude_paths` with
  a complete list? Not verifiable from this repo; worth a quick check by whoever operates that
  instance, given `epic_autopilot` could in principle be enabled there independently of dark-factory.

## Assumptions

- "Fails loudly" (per the issue) means letting the normal `ImportError`/`ModuleNotFoundError`
  propagate unwrapped, *except* at the one site (`gate_blast_radius.py`) where an existing,
  advertised, load-bearing "always emit a parseable STATUS line" contract requires converting the
  failure into an explicit fail-closed verdict instead of a bare crash — this is not "wrapping the
  exception" in the sense the issue warns against, it's preserving an unrelated pre-existing
  contract that a bare crash would otherwise silently violate.
- The acceptance criterion's literal wording (`except ImportError`) is corrected to `except
  Exception:`, matching what every real site in this repo actually uses — re-confirmed on current
  `main` (all 11 fallback sites across the 6 files use `except Exception:`, none use the narrower
  `except ImportError:`).
- `tests/test_epic_autopilot.py`'s test-local `sensitive_keywords=r"trading|ibkr|order|jwt|/auth"`
  parameter (if still present at implementation time) is a deliberate test-fixture override, not a
  duplicate needing fixing — carried forward from the discarded spec's same assumption, not
  independently re-verified in this pass since it doesn't touch any safety-constant *definition*.
- `docs/superpowers/specs/` already exists on `main` (multiple prior specs present), so this is an
  ordinary new file in that directory, not a first-of-its-kind path creation.

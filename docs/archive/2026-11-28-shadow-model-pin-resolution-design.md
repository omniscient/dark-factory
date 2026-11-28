# Fix `shadow_model` Null Handling and Pin Re-resolution Across Phases

**Issue:** #406

## Overview / Problem Statement

`commands/dark-factory-conformance.md` and `commands/dark-factory-plan.md` resolve the
conformance shadow-reviewer's model pin from `config.yaml`'s `conformance.shadow_model`
via an inline `python3 -c` one-liner. On `main` the one-liner exists at **two** sites
(1 and 5 below); the other three (2-4) are `bash` blocks that only *consume*
`$SHADOW_MODEL_PIN` behind a guard and carry no resolution of their own. This spec
touches all five:

1. `dark-factory-conformance.md` Phase 1 step 12 (lines 76-77) — the canonical resolution.
2. `dark-factory-conformance.md` Phase 4 (PASS, the single `bash` block spanning
   ~lines 584-621) — gated on `${SHADOW_MODEL_PIN:-}` at ~line 593.
3. `dark-factory-conformance.md` Phase 5 (BLOCKED) step 1, the comment-build block
   (~lines 681-718) — gated on `${SHADOW_MODEL_PIN:-}` at ~line 687.
4. `dark-factory-conformance.md` Phase 5 step 4, the status-write block
   (~lines 732-746) — gated on `${SHADOW_MODEL_PIN:-}` at ~line 739.
5. `dark-factory-plan.md` Phase 3.5 step 4a (lines 186-187) — this command's canonical
   resolution (plan has no bash-level Phase-4/5-style guard; see "Non-goals").

Issue #406 reports two problems with this resolution, filed as review follow-ups on
PR #398 (the shadow-model feature itself).

**Problem 1, as reported — a broken config path:** the issue claims the self instance
"hard-excludes `.claude/skills/**`", so the relative `open('.claude/skills/refinement/
config.yaml')` read always fails there, silently falling through to the hardcoded
`|| echo "claude-fable-5-1"` fallback, and that `config.yaml`'s `conformance.
shadow_model` is therefore never honored on self. It further notes that an explicit
`shadow_model: null` would print the Python string `"None"` instead of an empty pin.

**Investigation found half of this premise does not hold** (see "Deviation from the
issue's ask" below) **but the other half is a real, independently verifiable bug:**
`d.get('conformance',{}).get('shadow_model','claude-fable-5-1')` only substitutes the
default when the key is *absent*. When the key is present with value `null` (or an
empty string), `.get()` returns it as-is, and `print(None)` / `print('')` make
`SHADOW_MODEL_DEFAULT` either the literal 4-character string `"None"` or an empty
string that then gets used as a real (nonsensical) model pin or silently treated as
"off" only by accident. This must resolve deterministically to "off" (empty pin) in
both the null and empty-string cases, independent of which file is read.

**Problem 2 — the pin is not re-resolved across phases:** Phase 4 and the two guarded
blocks in Phase 5 of `dark-factory-conformance.md` each run in a separate `bash` tool
invocation from the one in Phase 1 step 12 that sets `$SHADOW_MODEL_PIN`. In this
command-file execution model, each ` ```bash ` block the orchestrating agent runs is a
fresh shell process — a shell variable set in an earlier block is not available in a
later one unless the agent manually re-injects its literal value (this is the same
"the agent holds these, not the shell" pattern this very refine command's own Phase 4
preamble describes for `$QA_HISTORY`/`$QUESTION`). If the agent does not carry the
value forward, `${SHADOW_MODEL_PIN:-}` silently evaluates empty in Phase 4/5 even
though the shadow subagent genuinely ran earlier — causing the shadow verdict to
silently vanish from the attestation/comment. This is the exact "R4 gap" (shadow
output must never silently disappear) the guard exists to prevent.

## Deviation from the issue's ask

The issue asks to re-resolve `SHADOW_MODEL_DEFAULT` by reading the **baked** config
path (`FACTORY_CONFIG_PATH` / `/opt/dark-factory/config/config.yaml`), mirroring
`scripts/factory_core/side_effect.py` / `scripts/gate_blast_radius.py`'s deliberate
"FACTORY-OWNED INPUTS ONLY" trust-boundary pattern. **This spec does not do that.**

Verified during refinement:

- `hard_exclude_paths` (`.factory/adapter.yaml`'s `safety.hard_exclude_paths`) is read
  only by `scripts/factory_core/epic_autopilot.py` (autopilot candidate-ticket
  eligibility) and `scripts/factory_core/mechanism_candidate.py` (diff-ranking). Neither
  blocks a file *read* — there is no code path anywhere that uses it to deny access to
  `.claude/skills/**`. Git-exclude (`.git/info/exclude`) only blocks *committing* a
  file, not reading it at runtime.
- `entrypoint.sh:1049-1050` runs `factory_core.effective_config --materialize
  --clone-dir "$CLONE_DIR" --out-dir "$ARTIFACTS_DIR"` **before** `archon workflow run`
  (`entrypoint.sh:1273`) dispatches any phase command, and the workflow runs with
  `worktree: enabled: false` (`workflows/archon-dark-factory.yaml:16-17`), so phase
  agents work in that same clone. For a clone with no committed copy (the self repo
  carries none) `materialize()` writes the baked←adapter-merged
  `.claude/skills/refinement/config.yaml` into the clone and git-excludes it. For a
  clone that commits its own copy, `materialize()` writes nothing and the committed
  file is read as-is. Either way the relative read finds a file. The file was
  confirmed present, with `conformance.shadow_model: claude-fable-5-1`, in the
  self-instance checkout the refine run executed in; it was not observed on a
  MarketHawk run.
- `docs/superpowers/specs/2026-11-19-effective-config-widening-design.md` (#180,
  closed 2026-11-20, implemented on `main`) explicitly inventories this exact read site.
  Its Requirement 2 lists `commands/dark-factory-{refine,plan,conformance,
  code-review,validate}.md` as readers that "keep reading the clone-side file" — not
  migrated to baked-only — and its acceptance matrix states, for the
  `conformance.*` row, that both "Self" and "MarketHawk" already resolve via
  "materialized" today, with "Required after this ticket" unchanged ("file still
  written, same bytes"). #180 treats the read as already correct — the opposite of
  this issue's "always fails" claim.
- #406 cites no observed failure. Run transcripts cannot settle it either way: the
  config value and the hardcoded fallback are the same literal today.
- Switching to the baked path would also change behavior for a target that commits its
  own `.claude/skills/refinement/config.yaml`: in that mode the committed file wins
  whole-file for every run-side reader (`effective_config.run_config()`), and a
  baked-only read would make `shadow_model` the one `conformance.*` knob that ignores
  it. (The adapter overlay is not the reason: `TARGET_TUNABLE_BLOCKS` is
  `("token_optimization",)` only, so an adapter cannot set `conformance.*` today.) The
  "FACTORY-OWNED INPUTS ONLY" pattern in `side_effect.py`/`gate_blast_radius.py` is
  deliberately reserved for security/gate-integrity knobs (side-effect level, blast
  radius) where a target must never be able to raise its own permissions — `shadow_model`
  is an advisory, non-gating knob and doesn't need that trust boundary.

Given this, the fix keeps reading the clone-side materialized file and narrows to the
part of Problem 1 that is a real bug (null/empty handling) plus a cheap defensive
hardening (below). No `needs-discussion` was raised for this: it narrows the work in
favor of an already-reviewed sibling spec, and the spec-pending-review human gate is
the right checkpoint to confirm this judgment.

## Requirements

1. **Null/empty-safe shadow-model resolution.** The Python snippet must resolve
   `conformance.shadow_model` so that:
   - an explicit `null` value → empty pin (shadow off)
   - an explicit `""` value → empty pin (shadow off)
   - the key absent, or the file missing/unreadable → `"claude-fable-5-1"`
   - any other explicit non-empty value → that value, used as-is

   `CONFORMANCE_SHADOW_MODEL`'s env-override semantics are unchanged
   (`${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}` — explicit-empty-string-env-wins-
   over-unset, the existing "Requirement 7" from the original shadow-model spec).

2. **Self-sufficient resolution blocks (closes the R4 gap).** The snippet must replace
   the existing one-liner at sites 1 and 5 and be **inserted** at sites 2-4, and each
   of the three sites in
   `dark-factory-conformance.md` that are gated by `if [ -n "${SHADOW_MODEL_PIN:-}" ]`
   (Phase 4; Phase 5 step 1; Phase 5 step 4) must compute `SHADOW_MODEL_PIN` fresh,
   inside that same `bash` block, before the guard — never relying on a variable set in
   a different tool invocation. The repo root must also be computed inline (e.g. a
   local `ROOT=$(git rev-parse --show-toplevel 2>/dev/null)` right before the python3 call) rather
   than reused from a `$REPO_ROOT` set in an earlier block, so the fix doesn't
   reintroduce the exact cross-block fragility it's meant to close.

   Re-resolution restores only the pin. `SHADOW_STATUS`, `SHADOW_FINDINGS_COUNT`,
   `SHADOW_SEVERITY`, `SHADOW_DIALOGUE` and `SHADOW_VERDICT_LINE` stay agent-carried;
   when the agent drops them the blocks emit their existing defaults
   (`SHADOW_STATUS: UNCERTAIN`, count `0`, severity `none`). That visible UNCERTAIN
   record is the intended outcome of this ticket — it replaces a silently missing
   block — and the defaults themselves are not changed.

3. **Identical snippets.** All five resolution sites must use the same three lines,
   identical after stripping each line's leading whitespace (the sites sit at different
   markdown list indents: 4, 0, 3, 3 and 4 spaces), so a single extracted copy can be
   exercised by tests and trusted to represent all five.

4. **Test coverage** (behavioral, not just static text matching — the bug is about
   runtime behavior, and a substring match would pass even if the snippet still fell
   through to the hardcoded fallback on every input):
   - Extract one copy of the resolution snippet from `dark-factory-conformance.md`
     (Phase 1 step 12) and execute it with `subprocess.run(["bash", "-c", block], ...)`,
     following the existing precedent at
     `tests/test_docs_conformance_command.py:147-150`
     (`_phase("Phase 4").split("```bash\n", 1)[1].split("\n```", 1)[0]` then
     `subprocess.run`). Run it with `cwd` set to a `tmp_path` that is `git init`-ed
     (so `git rev-parse --show-toplevel` resolves inside the fixture, never the real
     repo), holding a fixture `.claude/skills/refinement/config.yaml` written per case
     (or omitted, for the missing-file case). Skip on a non-POSIX host missing
     `bash`/`git`, matching the existing precedent.
   - Cases: `shadow_model: null` → empty; `shadow_model: ""` → empty; key absent →
     `claude-fable-5-1`; explicit value (e.g. `claude-opus-5-5`) → that value; no
     config file in the fixture repo → `claude-fable-5-1` via the `|| echo` fallback;
     `CONFORMANCE_SHADOW_MODEL=""` env var set with a config value present → empty pin
     (proves Requirement 7's explicit-empty-wins-over-unset survives this change).
   - Static assertions (cheap, keep alongside the behavioral ones): no site still
     contains the old literal `open('.claude/skills/refinement/config.yaml')` without
     an absolute root prefix; all five snippets are identical after stripping leading
     whitespace per line; the snippet
     appears before each of the three `if [ -n "${SHADOW_MODEL_PIN:-}" ]` guards in
     `dark-factory-conformance.md`.
   - Place the new tests in a new `tests/test_shadow_model_resolution.py` (or extend
     `tests/test_conformance_command_shadow_review.py` — implementer's call, consistent
     with the existing file's scope which is currently structural/static-text only).
     Either file is collected by CI's `python -m pytest tests/ -v`; no
     `.github/workflows/ci.yml` change and no new `tests/test_*.sh` script.

5. **Reference-doc citations.** `docs/configuration.md` cites the two existing sites by
   line (`commands/dark-factory-conformance.md:76-77` and
   `commands/dark-factory-plan.md:186-187` in the `conformance.shadow_model` row;
   `commands/dark-factory-conformance.md:77` in the `CONFORMANCE_SHADOW_MODEL` row).
   The added `ROOT=` line shifts them. Update those citations to the new line numbers
   and state in the `conformance.shadow_model` row that `null`/empty means "no shadow".
   `scripts/docs_refs_check.py` only range-checks cited lines, so CI would not catch a
   stale citation. No other doc changes.

## Acceptance Criteria

- AC1: with a fixture config, the extracted snippet yields: `shadow_model: null` → empty
  pin; `shadow_model: ""` → empty pin; key absent → `claude-fable-5-1`; explicit value →
  that value; no config file → `claude-fable-5-1`; `CONFORMANCE_SHADOW_MODEL=""` with a
  config value present → empty pin.
- AC2: in `dark-factory-conformance.md`, each of the three
  `if [ -n "${SHADOW_MODEL_PIN:-}" ]` guards is preceded, inside the same fenced `bash`
  block, by the snippet.
- AC3: the five snippets are identical after stripping leading whitespace per line, and
  none reads `open('.claude/skills/refinement/config.yaml')` relative to the cwd.
- AC4: `config/config.yaml` is unchanged; the diff touches only
  `commands/dark-factory-conformance.md`, `commands/dark-factory-plan.md`,
  `docs/configuration.md` and the test file. No `gate_*`, breaker, budget, workflow,
  `deploy/**` or `.github/workflows/**` file is touched.
- AC5: `python -m pytest tests/ -v` and
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  pass.

## Architecture / Approach

Replace, at sites 1 and 5, the existing:

```bash
SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

with the following, and insert the same three lines at sites 2-4 immediately before
the guard (at site 3, before `SHADOW_BLOCK=""`):

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

Notes for the implementer:

- `ROOT=$(git rev-parse --show-toplevel 2>/dev/null)` is intentionally local to each
  block, not a reuse of the Phase-1-scoped `$REPO_ROOT`. If `git rev-parse` fails (e.g.
  not inside a git repo), `ROOT` is empty, `open('/.claude/skills/refinement/config.yaml')`
  raises, and the `|| echo "claude-fable-5-1"` on the python line supplies the default —
  no additional error handling needed. The clone path is factory-chosen and contains no
  quote or backslash, so interpolating `$ROOT` into the Python string literal is safe.
- `(d.get('conformance') or {})` guards against `conformance:` itself being present but
  null (resolves to the default). An unparseable file, or a non-mapping `conformance:`,
  raises and also resolves to the default through `|| echo`.
- The hardcoded `claude-fable-5-1` fallback literal is kept exactly as it is on `main`;
  this ticket does not change which model the shadow uses in any case where it runs
  today.
- `v if v else ''` collapses `None` and `""` to the same empty-pin outcome Requirement 1
  calls for, in one expression.
- This only touches the five `SHADOW_MODEL_DEFAULT`/`SHADOW_MODEL_PIN` resolution
  blocks. Everything downstream of `$SHADOW_MODEL_PIN` (the `if [ -n ... ]` guards
  themselves, the shadow subagent spawns, the `SHADOW_*` field emission) is unchanged.

## Alternatives Considered

1. **Read the baked path (`FACTORY_CONFIG_PATH`/`/opt/dark-factory/config/
   config.yaml`), as the issue literally asks.** Rejected — see "Deviation from the
   issue's ask": it contradicts #180's documented, more-recently-verified design for
   this exact read site, and would silently drop a future adapter-level
   `conformance.*` override.
2. **Route through `scripts/factory_core/effective_config.py`'s resolver or the
   `run-config.json`/`run-config.env` artifacts instead of an inline `python3 -c`.**
   Rejected for this ticket's scope. `run-config.json` (whole tree, path exported as
   `FACTORY_RUN_CONFIG_JSON`) and `effective_config.load_run_config()` exist on `main`
   and carry the same content as the clone-side file, but #180 (closed, implemented)
   deliberately left `commands/*.md` on the clone-side file, no phase command reads the
   run-config artifacts today, and `shadow_model` is not in `ENV_WHITELIST` (which
   feeds only scheduler.sh/entrypoint.sh). Migrating one knob of one command would be
   an arbitrary half-migration; it belongs to the audit ticket in Non-goals. The inline
   `python3 -c` one-liner is the established convention at the two existing sites (and
   the sibling `SPEC_GRACE`/`PLAN_GRACE` sites) — matching it keeps this fix minimal.
3. **Extract the resolution logic into a shared shell function (e.g. in
   `scripts/gate_lib.sh`, which `dark-factory-conformance.md` already sources) to avoid
   repeating the snippet five times.** Rejected — `gate_lib.sh` is sourced once in
   Phase 1 and, per Problem 2's own root cause, a sourced function's *definition*
   persists only within the bash process that sourced it; calling it from Phase 4/5
   would require re-sourcing `gate_lib.sh` there too, which is no simpler than
   repeating the five-line snippet, and the issue explicitly asks for literal
   repetition ("repeat the Phase 1 snippet").

## Non-goals

- **`PLAN_GRACE` (`dark-factory-plan.md:298`) and `SPEC_GRACE`
  (`dark-factory-refine.md:231`)** read `direct_to_pr.{plan,spec}_grace_minutes` via
  the identical relative-path pattern, and at least ten more `.claude/skills/
  refinement/config.yaml` relative reads exist across `dark-factory-code-review.md`,
  `dark-factory-docs-conformance.md`, `dark-factory-validate.md`, and
  `dark-factory-conformance.md`'s own other lines (e.g. line 59, 213). The issue's
  "Ask" names only the `shadow_model` sites; it cites `PLAN_GRACE` solely as evidence
  of the pattern, not as part of its scope. These are lower severity (they only affect
  a cosmetic "Auto-advancing in ~N min" comment string — the real grace-period
  enforcement already goes through `scheduler.sh` via `effective_config.py`'s
  `SPEC_GRACE_MINUTES`/`PLAN_GRACE_MINUTES` whitelist entries, independently of these
  display-only reads) and deciding which of the ~12 total sites are factory-policy
  (candidate for a baked-path read) versus target config (should keep reading the
  clone) is its own design question. Left for a separate, dedicated audit ticket.
- **No pin flip, no gate/breaker/budget change** (per the issue) — `config/
  config.yaml`'s `conformance.shadow_model: claude-fable-5-1` value, the
  `max_reconcile_cycles`/`block_on_material`/etc. knobs, and the shadow verdict's
  non-gating status are all unchanged.
- **`dark-factory-plan.md`'s own Phase-4-equivalent `$SHADOW_MODEL_PIN` references**
  (its BLOCKED/PUBLISH sections, ~lines 252 and 341) are prose instructions to the
  orchestrating agent ("If `$SHADOW_MODEL_PIN` was non-empty for this run, append a
  subsection"), not literal bash conditionals — they don't have Problem 2's shell-state
  exposure, so they need no change.

## Open Questions (non-blocking)

- Whether `effective_config.ENV_WHITELIST` should eventually grow a `shadow_model`
  (and `conformance.*` generally) entry, or whether phase commands should move to
  `run-config.json`, is a question for a follow-up, not this ticket (#180 is closed).
- Whether the ~12-site relative-config-read audit (see Non-goals) should land as one
  ticket or be split by factory-policy vs. target-config classification is left to
  whoever files that follow-up.

## Assumptions

- `entrypoint.sh`'s `factory_core.effective_config --materialize` call continues to run
  before `archon workflow run` on every container run, for both live instances, as it
  does today — this spec relies on that ordering continuing to hold (flagged as an
  assumption rather than re-verified end-to-end in a live container, since this is a
  refine-phase desk review, not a runtime test).
- `git rev-parse --show-toplevel` is available and resolves correctly from within any
  `bash` tool invocation during a phase command's execution (it is already used
  unconditionally elsewhere in both `dark-factory-conformance.md` Phase 1 step 1 and
  `dark-factory-plan.md`, so this is an existing, not new, assumption).

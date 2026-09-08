# Factory-owned critical paths and boundary-escalation detection (A6)

**Issue:** #200
**Epic:** #194 (Factory/target boundary v1) · **Depends on:** #196 (A2, shipped) · **Coordinates with:** #46 (shipped)

## Overview / Problem statement

`.factory/adapter.yaml` lets a target repo override factory defaults (`scripts/factory_core/adapter_defaults.py::DEFAULTS`), deep-merged in `scripts/factory_core/adapter.py::_deep_merge()`. That merge replaces list-valued keys wholesale instead of merging them — a documented, deliberate trade-off for MarketHawk-specific lists, but for the `safety` block's path lists it is also the boundary's own bypass: a target's `.factory/adapter.yaml` can silently *shrink* `safety.critical_diff_paths` or `safety.migration_seed_auth_patterns` by declaring its own list without the factory-owned entries, and nothing detects the shrinkage. `.factory/adapter.yaml`'s own self-target copy has to carry the #46 globs by hand today, guarded only by `tests/test_adapter.py::test_dark_factory_own_adapter_yaml_has_skill_security_globs` — a test that pins this repo's *own* file, not a structural guarantee for any target.

Two consumers read these lists:
- `scripts/diff_rank.py` reads `safety.critical_diff_paths` for review-ordering/visibility only (never blocks).
- `scripts/gate_blast_radius.py` reads `safety.migration_seed_auth_patterns` to decide `STATUS: HUMAN_REQUIRED` (the actual blocking gate, invoked from `commands/dark-factory-validate.md` Phase 0).

Separately, `.factory/adapter.yaml`'s `loops:` block declares, per loop, a `side_effect_level` (1–6) and a `verification.{verifier, stop_condition}` pair of script paths. #196 (shipped) enforces the factory-owned floor (`side_effect.FACTORY_OWNED_MIN_LEVEL = 4`) at *handoff time* (`scripts/factory_core/handoff.py::cross_check` rejects a manifest from a loop declaring level ≥ 4) and requires `budget_caps`/`human_checkpoint` on such loops at *validation time* (`adapter.py::_validate_loop`). Neither check runs at PR-review time: a target PR can raise an existing loop's `side_effect_level`, repoint its `verifier`/`stop_condition` to a different script, or add a brand-new level-≥4 loop, and today's blast-radius gate has no way to notice — it only sees a changed-file list, never adapter.yaml's actual content before and after.

This ticket closes both gaps: (1) a small, hardcoded, non-overridable floor of boundary paths that survives any adapter.yaml override, and (2) a semantic diff of `.factory/adapter.yaml`'s `safety:` block and `loops:` entries (base branch vs. HEAD) so a boundary-widening edit is flagged even though the file itself stays target-definable for benign changes.

## Requirements (from Q&A)

Two architectural questions were brainstormed with a product-owner review of the codebase (full Q&A in the pipeline comment); the resolutions below are load-bearing:

1. **Both gating-relevant lists get the floor, not just `critical_diff_paths`.** `critical_diff_paths` only affects `diff_rank.py` ordering; the list that actually blocks (`migration_seed_auth_patterns`) is the one the bypass matters for. The floor therefore applies to both, as two related but independently-shaped constants (see Architecture) — not by pointing `gate_blast_radius.py` at `critical_diff_paths` instead, which would collapse #46's deliberate SKILL.md-is-visibility-only-never-blocking distinction (`tests/test_adapter.py::test_skill_md_not_in_migration_seed_auth_patterns`) and re-introduce MarketHawk-specific defaults into every target's blocking list.
2. **`hard_exclude_paths` is out of scope.** It is a different threat model (a fail-*closed* write-access list for `epic_autopilot`, which deliberately *excludes* dark-factory's own tree for self-improvement) — forcing an extend-only union there would break self-improvement. Not addressed by this ticket; flagged as a possible follow-up under Open questions.
3. **The floor is a small, explicit set of boundary paths** — not the full `DEFAULTS` lists (which would re-inject MarketHawk-specific paths like `^alembic/versions/` into every target and cause spurious blocks): `.factory/hooks/**`, `.factory/adapter.yaml` itself, `.claude/**` (broadened from today's narrower `.claude/skills/`, `.claude/settings*.json`, `.claude/plugins/`, `.claude-plugin/` subset), `workflows/**`, `commands/**`.
4. **`.factory/adapter.yaml` itself is floor-visibility-only, not floor-blocking**, i.e. present in the `critical_diff_paths` floor but *excluded* from the `migration_seed_auth_patterns` floor. #196's policy states side-effect levels 1–3 are "target-definable if auditable" — a target authoring or tweaking its own low-risk loop must not require human review on every edit. A blanket path-match block on the whole file would defeat that policy (every `purpose:` string edit would need a human). Instead, adapter.yaml's actual escalation risk is caught by the semantic diff (next point), which distinguishes a benign loop edit from a boundary-widening one.
5. **New: semantic diff of `.factory/adapter.yaml`, base branch vs. HEAD**, run by `gate_blast_radius.py` only when `.factory/adapter.yaml` appears in the changed-file list (zero cost otherwise; `test_blast_radius.py` unaffected). `STATUS: HUMAN_REQUIRED` when any of:
   - Any change anywhere under the top-level `safety:` mapping (no benign edit to the boundary's own definition exists).
   - For a `loops[].name` present in both revisions: `side_effect_level` **increased** (a decrease is not flagged).
   - For a `loops[].name` present in both revisions: `verification.verifier` or `verification.stop_condition` value **changed** (these are opaque script paths — "changed" is the only thing a deterministic script can decide; no weaker/stronger judgment is attempted).
   - A loop present only in HEAD (added on this branch) declares `side_effect_level >= side_effect.FACTORY_OWNED_MIN_LEVEL` (4) — this is the PR-review-time counterpart to `handoff.py::cross_check`'s runtime rejection, catching the escalation before merge instead of at first handoff attempt.
   - Either revision's `.factory/adapter.yaml` fails to parse as YAML, or fails adapter loop-schema validation — **fail closed** to `HUMAN_REQUIRED` (consistent with this codebase's existing fail-closed conventions, e.g. `VERIFIER-CONTRACT.md`'s target-verifier rules).
6. Existing blast-radius behavior for every other file/category is unchanged (`tests/test_blast_radius.py` stays green without modification to its non-adapter-yaml cases).
7. A test proves the critical-path/migration-seed merge is extend-only: a `.factory/adapter.yaml` fixture that declares an empty or narrowed `safety.critical_diff_paths`/`safety.migration_seed_auth_patterns` must still resolve (via `adapter.get()`) to a list containing the full floor.

## Architecture / Approach

**1. `scripts/factory_core/adapter_defaults.py` — new floor constants.**

```python
# Factory-owned boundary paths (#200/A6): always present after adapter.yaml merge,
# regardless of what a target's adapter.yaml declares for these two lists.
FACTORY_OWNED_CRITICAL_DIFF_FLOOR = [
    r"^\.factory/hooks/",
    r"^\.factory/adapter\.yaml$",
    r"^\.claude/",
    r"^workflows/",
    r"^commands/",
]

# migration_seed_auth_patterns is the hard-blocking (HUMAN_REQUIRED) list.
# adapter.yaml itself is excluded here: its loops: block is target-definable for
# side_effect_level 1-3 (#196), so blanket-blocking the whole file on every edit
# would require human review for benign loop authoring. adapter.yaml's actual
# escalation risk (safety: block edits, side_effect_level increases, verifier/
# stop_condition changes) is instead caught by gate_blast_radius.py's semantic
# diff step. Derived from the list above (not duplicated) so the two floors
# cannot drift apart on the shared entries.
FACTORY_OWNED_MIGRATION_SEED_FLOOR = [
    p for p in FACTORY_OWNED_CRITICAL_DIFF_FLOOR if p != r"^\.factory/adapter\.yaml$"
]
```

These are new module-level constants alongside the existing `SKILL_SECURITY_TOKENS`; `DEFAULTS` itself is **not** modified (parity tests that compare `DEFAULTS["safety"][...]` verbatim against `diff_rank.SAFETY_PATH_PATTERNS`/`gate_blast_radius.MIGRATION_SEED_AUTH_PATTERNS` module-level constants keep passing unchanged, since those are computed directly from `DEFAULTS`).

**2. `scripts/factory_core/adapter.py::load()` — apply the floor once, after the deep merge.**

Only in the branch where an actual `.factory/adapter.yaml` file was read and merged (not the "no adapter file" early return) — the no-file case already returns trusted, factory-committed `DEFAULTS` verbatim, so there is no target-controlled input to bypass and no reason to change today's no-adapter-file parity behavior (`test_safety_path_patterns_default_parity`, `test_migration_seed_auth_patterns_default_parity` stay green untouched):

```python
def _apply_boundary_floor(safety: dict) -> dict:
    def _union(existing, floor):
        return list(existing) + [p for p in floor if p not in existing]
    safety = dict(safety)
    safety["critical_diff_paths"] = _union(
        safety.get("critical_diff_paths", []), adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR)
    safety["migration_seed_auth_patterns"] = _union(
        safety.get("migration_seed_auth_patterns", []), adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR)
    return safety
```

Called as `merged["safety"] = _apply_boundary_floor(merged["safety"])` immediately before `load()` returns, in the branch that reads an actual adapter.yaml file. This makes `adapter.get(clone_dir, "safety.critical_diff_paths")` / `adapter.get(clone_dir, "safety.migration_seed_auth_patterns")` — and therefore `diff_rank.py` and `gate_blast_radius.py`'s existing adapter-aware helpers, unchanged otherwise — always include the floor, with no other call site needing to change. `.factory/adapter.yaml`'s own self-target copy can keep its existing explicit globs (harmless duplication with the floor) or have them trimmed in a later cleanup — not required by this ticket.

**3. `scripts/gate_blast_radius.py` — new semantic-diff step, gated on adapter.yaml being touched.**

New CLI flag `--base-ref` (default `"main"`, mirroring `commands/dark-factory-validate.md`'s existing `git diff main...HEAD` convention). New helper:

```python
def _adapter_snapshot(clone_dir: str, ref: str | None) -> tuple[dict | None, bool]:
    """Return (parsed adapter.yaml dict or None, parse_ok). ref=None reads the working
    tree; a ref string reads `git show <ref>:.factory/adapter.yaml`. parse_ok=False on
    any read/parse failure — the caller fails closed."""
```

`main()`, only when `".factory/adapter.yaml"` is present in the incoming changed-file list:
- Load `(old, old_ok)` at `--base-ref` and `(new, new_ok)` from the working tree.
- If either `parse_ok` is `False`: append a `boundary-escalation` finding ("adapter.yaml unparseable at <ref/HEAD>") — fail closed.
- Else compare:
  - `old.get("safety") != new.get("safety")` → finding: `"safety: block changed"`.
  - Build `{name: loop}` maps from each side's `loops:` (default `[]`). For each name in both: `side_effect_level` increase → finding; `verification.verifier`/`verification.stop_condition` value change → finding (one line each, old → new).
  - For each name only in `new`: if `side_effect_level >= side_effect.FACTORY_OWNED_MIN_LEVEL` → finding (import the constant from `factory_core.side_effect`, not a re-literal `4`, mirroring `verifier.py`'s existing pattern).
- Any finding sets `STATUS: HUMAN_REQUIRED`, `SEVERITY: critical`, and a new `TRIGGER: boundary-escalation` value — ranked above `hotspot`/`skill-security`/`migration-seed` in `trigger_label` selection, since it is the most severe category and `commands/dark-factory-validate.md` posts `TRIGGER` verbatim into the blocking issue comment (a generic `migration-seed` label would misdescribe a side-effect-level escalation). Findings are emitted as `  - <finding text>` bullet lines under the existing `TRIGGERED_FILES:` section, so `commands/dark-factory-validate.md`'s existing `grep '^\s*-'` extraction picks them up with **no change needed to the comment-posting logic**.
- When `.factory/adapter.yaml` is absent from the changed-file list, none of the above runs — `lines_changed`/hotspot/migration-seed classification is untouched, keeping `test_blast_radius.py`'s existing cases green.

**4. `commands/dark-factory-validate.md` Phase 0 (and its `.archon/commands/` mirror, currently byte-identical) — thread the base ref through.**

```bash
BASE_SHA=$(git merge-base main HEAD 2>/dev/null || echo main)
echo "$CHANGED" | python3 dark-factory/scripts/gate_blast_radius.py \  # TARGET-PATH
  --changed-files-stdin \
  --lines-changed "$LINES" \
  --hotspots docs/codeindex-hotspots.md \
  --config .claude/skills/refinement/config.yaml \
  --base-ref "$BASE_SHA" \
  > "$ARTIFACTS_DIR/blast.md"
```

No other change to Phase 0's comment-posting/labeling logic is required (point 3 above).

**5. Tests.** New/updated coverage lives in `tests/test_adapter.py` (extend-only-merge proof: a fixture `.factory/adapter.yaml` that declares an empty/narrowed `safety.critical_diff_paths` and `safety.migration_seed_auth_patterns` still resolves, via `adapter.get()`, to a list containing the floor) and `tests/test_blast_radius.py` (new cases: safety-block edit, side_effect_level increase on an existing loop, verifier/stop_condition change, new level-≥4 loop, unparseable-adapter fail-closed, and a same-adapter-content no-op case proving no false positive when adapter.yaml is touched but nothing boundary-relevant changed).

## Alternatives considered

1. **Point `gate_blast_radius.py` at `critical_diff_paths` instead of `migration_seed_auth_patterns`.** Rejected — collapses #46's deliberate visibility-vs-blocking split (e.g. `SKILL.md` prose edits are visibility-only by design) and would pull MarketHawk-specific defaults into every target's blocking gate.
2. **Blanket-flag any change to `.factory/adapter.yaml` as `HUMAN_REQUIRED` via plain path match** (simpler — no semantic diff needed). Rejected — contradicts #196's "levels 1-3 are target-definable if auditable" policy; every benign loop edit would need human review, training targets to treat the gate as noise.
3. **Extend the same non-overridable-floor treatment to `hard_exclude_paths`.** Deferred — different threat model (fail-closed write-access list that deliberately excludes dark-factory's own tree for self-improvement); forcing a union would break `epic_autopilot` self-improvement. Left as a candidate follow-up ticket, not this one's scope.
4. **Union the full `DEFAULTS["safety"][...]` lists back in as the "floor"** (simplest to implement — no new constant). Rejected — reintroduces MarketHawk-specific paths (`^alembic/versions/`, trading paths, `^dark-factory/seed/`) into every target's gate, causing spurious `HUMAN_REQUIRED` for changes with no bearing on the factory/target boundary.

## Open questions (non-blocking)

- Should `.factory/adapter.yaml`'s own self-target copy have its now-redundant explicit `.claude/skills/...`, `.factory/hooks/`, etc. globs trimmed once the floor supersedes them? Left as-is for this ticket; harmless duplication, and pruning also touches `tests/test_adapter.py::test_dark_factory_own_adapter_yaml_has_skill_security_globs`, which is orthogonal to this issue's acceptance criteria.
- `hard_exclude_paths` extend-only treatment (Alternative 3) — worth its own ticket if a similar bypass is ever demonstrated for `epic_autopilot`.
- The semantic diff reads `.factory/adapter.yaml` at `--base-ref` via `git show`; if a factory-image container ever runs from a shallow clone without `main` reachable, `git merge-base` falls back to the literal `main` ref per the snippet above — if that ref is also unavailable the implementer should confirm `gate_blast_radius.py` fails closed (`HUMAN_REQUIRED`) rather than silently skipping the semantic step, consistent with requirement 5's fail-closed rule.

## Assumptions (flagged)

- Factory-image containers running `commands/dark-factory-validate.md` have `main`'s history available for `git merge-base`/`git show` — already assumed by Phase 0's pre-existing `git diff main...HEAD` call, so this is not a new dependency.
- `.archon/commands/dark-factory-validate.md` is a byte-identical mirror of `commands/dark-factory-validate.md` today (verified: `diff` produces no output); both copies need the same Phase 0 edit unless the build/bake step that produces one from the other is later found to do this automatically.
- "Target-definable if auditable" (levels 1–3) is read as: PR-review-time review is not required for benign edits, but the edit is still visible in the diff and audited post-hoc — this ticket does not change that visibility (adapter.yaml stays in the `critical_diff_paths` floor for `diff_rank.py` ordering even though it's excluded from the blocking floor).

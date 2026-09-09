# Factory-owned critical paths and boundary-escalation detection (A6)

**Issue:** #200
**Operator review:** 2026-09-08 (spec gate) — amendments F1-F10 from an independent read-only review applied; policy choices recorded under "Owner decisions".
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
3. **The floor is a small, explicit set of boundary paths** — not the full `DEFAULTS` lists (which would re-inject MarketHawk-specific paths like `^alembic/versions/` into every target and cause spurious blocks): `.factory/hooks/**`, `.factory/adapter.yaml` itself, `.claude/**` (broadened from today's narrower `.claude/skills/`, `.claude/settings*.json`, `.claude/plugins/`, `.claude-plugin/` subset), `workflows/**`, `commands/**`, and — operator review F2 — the in-clone paths that *shadow* baked factory enforcement when a target tracks them: `entrypoint.sh` copies the baked `commands/`, `workflows/` and `scripts/` into the clone only `if [ ! -d ]`, so a tracked `.archon/commands/**`, `.archon/workflows/**` or `dark-factory/scripts/**` silently replaces the factory's own. Those three are in the floor too.
   Only part of that set **blocks** (Requirement 4a): the blocking floor is `.factory/hooks/**`, `.claude/**` and the three shadowing paths. `workflows/**` and `commands/**` are visibility-only in v1 — see Owner decision OD1.
4. **`.factory/adapter.yaml` itself is floor-visibility-only, not floor-blocking**, i.e. present in the `critical_diff_paths` floor but *excluded* from the `migration_seed_auth_patterns` floor. #196's policy states side-effect levels 1–3 are "target-definable if auditable" — a target authoring or tweaking its own low-risk loop must not require human review on every edit. A blanket path-match block on the whole file would defeat that policy (every `purpose:` string edit would need a human). Instead, adapter.yaml's actual escalation risk is caught by the semantic diff (next point), which distinguishes a benign loop edit from a boundary-widening one.
4a. **`workflows/**` and `commands/**` are visibility-only in v1 (Owner decision OD1).** The issue lists them in the non-overridable *visibility* core; none of its acceptance criteria requires them to block. Blocking them on the self target would send every factory PR that edits a phase command or the DAG to `needs-discussion` + Blocked at validate Phase 0 — 8 of the last 20 merged self-target PRs (#372 #386 #388 #391 #392 #396 #398 #401) — and contradicts `.factory/adapter.yaml`'s `main_red_allowed_paths`, which lets the factory repair exactly those files autonomously when main is red. CLAUDE.md's human-in-the-loop surfaces are `deploy/**`, `publish.yml`, the gates, breaker, budgets and the `.claude/**` self-modification mechanism (#46); phase commands and the DAG stay factory-editable under Gates 2/3 (#399 makes them visible to those gates). Promoting them to the blocking floor is a one-line change to `FACTORY_OWNED_MIGRATION_SEED_FLOOR` and is the repo owner's call, not this ticket's.
5. **New: semantic diff of `.factory/adapter.yaml`, base branch vs. HEAD**, run by `gate_blast_radius.py` only when `.factory/adapter.yaml` appears in the changed-file list (zero cost otherwise; `test_blast_radius.py` unaffected). `STATUS: HUMAN_REQUIRED` when any of:
   - Any change anywhere under the top-level `safety:` mapping (no benign edit to the boundary's own definition exists).
   - For a `loops[].name` present in both revisions: `side_effect_level` **increased** (a decrease is not flagged).
   - For a `loops[].name` present in both revisions: `verification.verifier` or `verification.stop_condition` value **changed** (these are opaque script paths — "changed" is the only thing a deterministic script can decide; no weaker/stronger judgment is attempted).
   - A loop present only in HEAD (added on this branch) declares `side_effect_level >= side_effect.FACTORY_OWNED_MIN_LEVEL` (4) — this is the PR-review-time counterpart to `handoff.py::cross_check`'s runtime rejection, catching the escalation before merge instead of at first handoff attempt.
   - For a loop with `side_effect_level >= FACTORY_OWNED_MIN_LEVEL` in **either** revision: **any** change to that loop's entry — `budget_caps`, `human_checkpoint`, `purpose`, its removal — is a finding (operator review F7). Budgets and human checkpoints are CLAUDE.md safety surfaces, and a rename (delete + add) must not evade the verifier/stop_condition check above.
   - Either revision's `.factory/adapter.yaml` fails to parse as YAML, or fails adapter loop-schema validation — **fail closed** to `HUMAN_REQUIRED` (consistent with this codebase's existing fail-closed conventions, e.g. `VERIFIER-CONTRACT.md`'s target-verifier rules).
6. Existing blast-radius behavior for every other file/category is unchanged (`tests/test_blast_radius.py` stays green without modification to its non-adapter-yaml cases).
7. A test proves the critical-path/migration-seed merge is extend-only: a `.factory/adapter.yaml` fixture that declares an empty or narrowed `safety.critical_diff_paths`/`safety.migration_seed_auth_patterns` must still resolve (via `adapter.get()`) to a list containing the full floor.
8. **The gate's own kill switch cannot be flipped by the PR under review (operator review F1).** `commands/dark-factory-validate.md` Phase 0 always invokes `gate_blast_radius.py`. The `blast_radius.*` config block (`enabled`, thresholds) is read from the **base ref** — `git show $BASE_SHA:.claude/skills/refinement/config.yaml` — never from the PR's HEAD, and `enabled: false` suppresses only the hotspot and size triggers: the migration-seed match (which carries the floor) and the `boundary-escalation` semantic step run regardless. `.claude/skills/**` is hard-excluded for phase agents by convention, not write-enforced (only `epic_autopilot.py` consults `hard_exclude_paths`), so without this rule a PR that edits `.factory/hooks/x` and adds `enabled: false` would get `STATUS: SKIPPED`, contradicting #196's trust model (the target-clone config "is never consulted" for policy). Test: changed files = {`.factory/hooks/h.sh`, `.claude/skills/refinement/config.yaml` with `enabled: false`} → `HUMAN_REQUIRED`.
9. **The floor is unconditional (operator review F5).** `_apply_boundary_floor` runs in both `adapter.load()` branches — with and without a `.factory/adapter.yaml` file — and in the `DEFAULTS` fallbacks of `gate_blast_radius.py` and `diff_rank.py` (their `except Exception` paths), so a target cannot escape the floor by deleting its adapter file or making it unloadable. The two parity tests (`test_safety_path_patterns_default_parity`, `test_migration_seed_auth_patterns_default_parity`) change to compare against `DEFAULTS ∪ floor`; Requirement 6's "unchanged" applies to every non-floor, non-adapter-yaml case.

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
    # Paths that shadow baked factory enforcement when a target tracks them
    # (entrypoint.sh copies the baked pieces into the clone only if absent) -- F2.
    r"^\.archon/commands/",
    r"^\.archon/workflows/",
    r"^dark-factory/scripts/",
]

# migration_seed_auth_patterns is the hard-blocking (HUMAN_REQUIRED) list.
# adapter.yaml itself is excluded here: its loops: block is target-definable for
# side_effect_level 1-3 (#196), so blanket-blocking the whole file on every edit
# would require human review for benign loop authoring; its escalation risk is
# caught by gate_blast_radius.py's semantic diff instead. workflows/ and
# commands/ are excluded in v1 by Owner decision OD1 (visibility-only, see
# Requirement 4a) -- promoting them is a one-line change to this set. Derived
# from the list above (not duplicated) so the two floors cannot drift apart.
_VISIBILITY_ONLY = {r"^\.factory/adapter\.yaml$", r"^workflows/", r"^commands/"}
FACTORY_OWNED_MIGRATION_SEED_FLOOR = [
    p for p in FACTORY_OWNED_CRITICAL_DIFF_FLOOR if p not in _VISIBILITY_ONLY
]
```

These are new module-level constants alongside the existing `SKILL_SECURITY_TOKENS`; `DEFAULTS` itself is **not** modified (parity tests that compare `DEFAULTS["safety"][...]` verbatim against `diff_rank.SAFETY_PATH_PATTERNS`/`gate_blast_radius.MIGRATION_SEED_AUTH_PATTERNS` module-level constants keep passing unchanged, since those are computed directly from `DEFAULTS`).

**2. `scripts/factory_core/adapter.py::load()` — apply the floor once, after the deep merge.**

In **both** branches of `load()` — the merged-file branch and the no-adapter-file early return — and mirrored in the `DEFAULTS` fallbacks of `gate_blast_radius.py` / `diff_rank.py` (Requirement 9). Returning `DEFAULTS` verbatim from the no-file branch is not safe: a target that deletes or breaks its adapter file must still land on the floor. The two parity tests change to `DEFAULTS ∪ floor`:

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

Called as `merged["safety"] = _apply_boundary_floor(merged["safety"])` immediately before every `load()` return. This makes `adapter.get(clone_dir, "safety.critical_diff_paths")` / `adapter.get(clone_dir, "safety.migration_seed_auth_patterns")` — and therefore `diff_rank.py` and `gate_blast_radius.py`'s existing adapter-aware helpers, unchanged otherwise — always include the floor, with no other call site needing to change. `.factory/adapter.yaml`'s own self-target copy can keep its existing explicit globs (harmless duplication with the floor) or have them trimmed in a later cleanup — not required by this ticket.

**3. `scripts/gate_blast_radius.py` — new semantic-diff step, gated on adapter.yaml being touched.**

Kill switch (Requirement 8): the script loads `blast_radius.*` from `git show <base-ref>:.claude/skills/refinement/config.yaml` (falling back to defaults when the file is absent at the base ref), never from the working tree; `--config` keeps naming the path. `enabled: false` short-circuits only the hotspot/size classification.

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
  - For each name only in `new`: if `side_effect_level >= side_effect.FACTORY_OWNED_MIN_LEVEL` → finding (import the constant from `factory_core.side_effect`, not a re-literal `4`, as `handoff.py` already does — `verifier.py` still carries a literal `4` and is not the pattern to copy).
- Any finding sets `STATUS: HUMAN_REQUIRED`, `SEVERITY: critical`, and a new `TRIGGER: boundary-escalation` value — ranked above `hotspot`/`skill-security`/`migration-seed` in `trigger_label` selection, since it is the most severe category and `commands/dark-factory-validate.md` posts `TRIGGER` verbatim into the blocking issue comment (a generic `migration-seed` label would misdescribe a side-effect-level escalation). Findings are emitted as `  - <finding text>` bullet lines under the existing `TRIGGERED_FILES:` section, so `commands/dark-factory-validate.md`'s existing `grep '^\s*-'` extraction picks them up with **no change needed to the comment-posting logic**.
- When `.factory/adapter.yaml` is absent from the changed-file list, none of the above runs — `lines_changed`/hotspot/migration-seed classification is untouched, keeping `test_blast_radius.py`'s existing cases green.

**4. `commands/dark-factory-validate.md` Phase 0 — thread the base ref through and run the baked gate script.**

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

Script location (operator review F9): invoke the **baked** copy, `/opt/dark-factory/scripts/gate_blast_radius.py`, the way `entrypoint.sh` runs `side_effect.py`, rather than the clone-relative `dark-factory/scripts/` copy, which a target repo can shadow by tracking its own (the F2 floor entry detects that at PR time, but the gate deciding it must not be the shadowed one). If the implement finds the baked path is not importable from the clone context, keep the clone-relative call and record the caveat under Assumptions; F2's `dark-factory/scripts/` floor entry then provides detection instead of prevention.

`.archon/commands/` is not a second edit site: it is untracked, seeded from the baked image by `entrypoint.sh` at container start, and the image is rebuilt on merge (`publish.yml`), so `commands/dark-factory-validate.md` is the only command-file change (operator review F4).

**5. Tests.** New/updated coverage lives in `tests/test_adapter.py` (extend-only-merge proof: a fixture `.factory/adapter.yaml` that declares an empty/narrowed `safety.critical_diff_paths` and `safety.migration_seed_auth_patterns` still resolves, via `adapter.get()`, to a list containing the floor) and `tests/test_blast_radius.py` (new cases: safety-block edit, side_effect_level increase on an existing loop, verifier/stop_condition change, new level-≥4 loop, unparseable-adapter fail-closed, and a same-adapter-content no-op case proving no false positive when adapter.yaml is touched but nothing boundary-relevant changed). The semantic-diff cases need real git history: the existing `test_blast_radius.py` harness is hermetic and has no repository, so add a `tmp_git_repo` fixture that commits the base `.factory/adapter.yaml` on `main` and the modified one on a branch, and invoke the gate with `--clone-dir <tmp>` and `--base-ref main` rather than mocking `git show`. Also cover Requirement 8 (kill switch on HEAD ignored) and Requirement 9 (no adapter file → floor still applied).

## Alternatives considered

1. **Point `gate_blast_radius.py` at `critical_diff_paths` instead of `migration_seed_auth_patterns`.** Rejected — collapses #46's deliberate visibility-vs-blocking split (e.g. `SKILL.md` prose edits are visibility-only by design) and would pull MarketHawk-specific defaults into every target's blocking gate.
2. **Blanket-flag any change to `.factory/adapter.yaml` as `HUMAN_REQUIRED` via plain path match** (simpler — no semantic diff needed). Rejected — contradicts #196's "levels 1-3 are target-definable if auditable" policy; every benign loop edit would need human review, training targets to treat the gate as noise.
3. **Extend the same non-overridable-floor treatment to `hard_exclude_paths`.** Deferred — different threat model (fail-closed write-access list that deliberately excludes dark-factory's own tree for self-improvement); forcing a union would break `epic_autopilot` self-improvement. Left as a candidate follow-up ticket, not this one's scope.
4. **Union the full `DEFAULTS["safety"][...]` lists back in as the "floor"** (simplest to implement — no new constant). Rejected — reintroduces MarketHawk-specific paths (`^alembic/versions/`, trading paths, `^dark-factory/seed/`) into every target's gate, causing spurious `HUMAN_REQUIRED` for changes with no bearing on the factory/target boundary.
5. **Shadow mode for the new trigger** (emit findings, leave `STATUS` unchanged for a week). Considered at operator review (F10) and not adopted: the trigger fires only when hooks, `.claude/**`, a shadowing path or `.factory/adapter.yaml` is in the changed set — rare, already human-reviewed surfaces — so a week of shadow rows would carry almost no signal, and a false positive costs one `needs-discussion` clear. Rollback is a revert of the constants.

## Open questions (non-blocking)

- Should `.factory/adapter.yaml`'s own self-target copy have its now-redundant explicit `.claude/skills/...`, `.factory/hooks/`, etc. globs trimmed once the floor supersedes them? Left as-is for this ticket; harmless duplication, and pruning also touches `tests/test_adapter.py::test_dark_factory_own_adapter_yaml_has_skill_security_globs`, which is orthogonal to this issue's acceptance criteria.
- `hard_exclude_paths` extend-only treatment (Alternative 3) — worth its own ticket if a similar bypass is ever demonstrated for `epic_autopilot`.
- The semantic diff reads `.factory/adapter.yaml` at `--base-ref` via `git show`; if a factory-image container ever runs from a shallow clone without `main` reachable, `git merge-base` falls back to the literal `main` ref per the snippet above — if that ref is also unavailable the implementer should confirm `gate_blast_radius.py` fails closed (`HUMAN_REQUIRED`) rather than silently skipping the semantic step, consistent with requirement 5's fail-closed rule.
- **Deterministic consumer of `blast.md` (follow-up #407).** Today nothing enforces `STATUS: HUMAN_REQUIRED` mechanically: `scripts/verdict_gate_check.sh` guards `conformance.md`/`review.md` only, validate's `exit 1` is unreliable (#212), and the block is the `needs-discussion` label applied by the phase agent. A pre-existing gap, not introduced here; a `blast-gate` DAG node before `push-and-pr` is its own reviewed gate change (CLAUDE.md), tracked as #407. This ticket's acceptance criteria are met by the gate's output; #407 makes that output binding.

## Owner decisions (recorded at the spec gate, 2026-09-08)

- **OD1 — blocking floor membership.** v1 blocks `.factory/hooks/**`, `.claude/**`, `.archon/commands/**`, `.archon/workflows/**`, `dark-factory/scripts/**` and the adapter semantic diff; `workflows/**` and `commands/**` are visibility-only (Requirement 4a). Widening is a one-line owner change; the delegated operator did not take it.
- **OD2 — `.factory/adapter.yaml` visibility-only plus semantic diff** (Requirement 4): consistent with #196's "levels 1—3 target-definable if auditable".
- **OD3 — `hard_exclude_paths` stays outside the floor** (Q&A item 2); the `safety:`-block diff still catches PR-time shrinkage of the safety lists.
- **OD4 — who clears a `boundary-escalation` block.** The change must be covered by a human-reviewed spec on a branch (CLAUDE.md). The delegated operator session may remove `needs-discussion` and re-run validate on that basis (2026-08-22 delegation); a boundary change without such a spec waits for the repo owner.
- **OD5 — kill-switch source** (Requirement 8): base ref, never PR HEAD.
- **OD6 — fail closed on an unparseable adapter** (Requirement 5): matches #196's fail-closed default.
- **OD7 — `.claude/**` prose blocks at the gate** (recorded at the plan gate, 2026-09-08). The floor's `^\.claude/` entry makes any `.claude/**` edit — including `SKILL.md`/`RUBRIC.md` prose, visibility-only under #46 — `HUMAN_REQUIRED` in `gate_blast_radius.py`; `DEFAULTS`' own lists and `diff_rank` ordering are untouched, and `SKILL_SECURITY_TOKENS` is broadened from `claude/skills` to `claude/` so the trigger label reads `skill-security` rather than `migration-seed` (label-only; no existing classification changes). Practical impact on the self target is nil (phase agents cannot write `.claude/**`); consistent with CLAUDE.md's human-only framing of that surface. Decided by the delegated operator; reversible by removing `^\.claude/` from the blocking floor.

## Assumptions (flagged)

- Factory-image containers running `commands/dark-factory-validate.md` have `main`'s history available for `git merge-base`/`git show` — already assumed by Phase 0's pre-existing `git diff main...HEAD` call, so this is not a new dependency.
- `.archon/commands/` is not tracked (`git ls-files .archon` lists memory files only); `entrypoint.sh` seeds it from the baked image at container start (`if [ ! -d ]`). `commands/dark-factory-validate.md` is the only command-file edit; the image rebuild on merge carries it to runs.
- "Target-definable if auditable" (levels 1–3) is read as: PR-review-time review is not required for benign edits, but the edit is still visible in the diff and audited post-hoc — this ticket does not change that visibility (adapter.yaml stays in the `critical_diff_paths` floor for `diff_rank.py` ordering even though it's excluded from the blocking floor).

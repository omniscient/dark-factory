# Implementation Plan: Factory-owned critical paths and boundary-escalation detection (A6)

**Issue:** #200
**Operator review (plan gate):** 2026-09-08 — amendments P1—P3, P5—P7 from an independent read-only review applied; OD7 recorded in the spec.
**Spec:** `docs/superpowers/specs/2026-09-08-boundary-bypass-prevention-a6-design.md`
**Depends on:** #196 (A2, shipped — `side_effect.FACTORY_OWNED_MIN_LEVEL`, `_validate_loop`'s
budget_caps/human_checkpoint enforcement). Every task builds against `main` as it exists today.

---

## Goal

Close two boundary bypasses: (1) a target's `.factory/adapter.yaml` can silently shrink
`safety.critical_diff_paths`/`safety.migration_seed_auth_patterns` because `_deep_merge`
list-replaces instead of merging, defeating `gate_blast_radius.py` for `.factory/hooks/**`,
`.claude/**`, and the paths that shadow baked factory enforcement; (2) a PR can raise a loop's
`side_effect_level`, repoint its `verifier`/`stop_condition`, or add a new factory-owned-level
loop with no PR-review-time detection. The fix adds a small, hardcoded, non-overridable path
floor unioned into both safety lists after every adapter merge, plus a semantic diff of
`.factory/adapter.yaml`'s `safety:` block and `loops:` entries (base ref vs. working tree) run
by `gate_blast_radius.py` only when that file is touched. The gate's own `enabled` kill switch
is read from the base ref only, never the PR's own copy — and, critically, the gate is now
invoked *unconditionally* from `commands/dark-factory-validate.md` Phase 0, since today's shell
layer reads `enabled` from the working tree itself and skips the Python invocation outright,
which would otherwise re-open exactly the bypass this ticket closes.

## Architecture

```
scripts/factory_core/adapter_defaults.py
  + FACTORY_OWNED_CRITICAL_DIFF_FLOOR   (8 patterns, visibility)
  + FACTORY_OWNED_MIGRATION_SEED_FLOOR  (5 patterns, blocking; derived, excludes
                                          adapter.yaml/workflows//commands/)
  ~ SKILL_SECURITY_TOKENS: "claude/skills" -> "claude/" (strictly broader; see Task 1)
        │
        ▼
scripts/factory_core/adapter.py::load()
  + _apply_boundary_floor(safety) -> safety   (unions the floor in; called in BOTH the
                                                merged-file branch and the no-file branch)
        │
        ├─ consumed via adapter.get(clone_dir, "safety.critical_diff_paths")
        │  by scripts/diff_rank.py::_safety_path_patterns()        (visibility only)
        │
        └─ consumed via adapter.get(clone_dir, "safety.migration_seed_auth_patterns")
           by scripts/gate_blast_radius.py::_migration_seed_auth_patterns()  (blocking)
              — both functions' except-Exception fallback also unions the floor in,
                independently of adapter.get() (Requirement 9)

scripts/gate_blast_radius.py
  + --base-ref CLI flag (default "main")
  + load_config(path, clone_dir, base_ref)   — now `git show <base-ref>:<path>`, never
                                                the working tree (Requirement 8)
  + _adapter_snapshot(clone_dir, ref)         — parse + adapter.py loop-schema validate
                                                 one revision of .factory/adapter.yaml;
                                                 distinguishes "file absent at a
                                                 resolvable ref" (ok) from "ref itself
                                                 doesn't resolve" (fail closed)
  + _boundary_escalation_findings(clone_dir, base_ref)  — semantic diff, run only when
                                                            .factory/adapter.yaml is in
                                                            the changed-file list
  + main(): enabled:false now suppresses only the hotspot/size triggers; the floor-carrying
    migration-seed match and the boundary-escalation step always run; new TRIGGER value
    "boundary-escalation", ranked above hotspot/skill-security/migration-seed

commands/dark-factory-validate.md Phase 0
  — REMOVES the shell-level BLAST_ENABLED pre-check/early-SKIP (it read the working
    tree and bypassed the Python gate entirely -- exactly the bypass Requirement 8
    closes; the gate itself now emits STATUS: SKIPPED correctly when truly disabled)
  + always invokes gate_blast_radius.py, preferring the BAKED
    /opt/dark-factory/scripts/gate_blast_radius.py (falls back to the clone-relative
    copy if the baked path is absent -- spec F9's explicit fallback), with
    --clone-dir "$REPO_ROOT" --base-ref "$BASE_SHA"
```

## Tech Stack

- Python stdlib (`subprocess`, `re`, `pathlib`) + PyYAML — matches every other
  `scripts/factory_core/*.py` module and `scripts/gate_blast_radius.py` itself; no new
  dependency.
- `pytest` + `tmp_path` + real throwaway `git` repos (via `subprocess`) for the new
  base-ref/semantic-diff test coverage — the existing `tests/test_blast_radius.py` harness
  is hermetic (no git repo), which the base-ref design now requires; this plan replaces its
  `run_script` helper with a `run_script` fixture backed by a committed temp repo.
- Bash for the `commands/dark-factory-validate.md` Phase 0 edit — matches its existing style;
  a static-assertion test (matching the `tests/test_ceiling_revisit_command.py` convention —
  no bash-execution harness exists for command files in this repo) pins the new invocation.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/adapter_defaults.py` | **Modified** — two new floor constants, `SKILL_SECURITY_TOKENS` broadened |
| `scripts/factory_core/adapter.py` | **Modified** — `_apply_boundary_floor`, applied in both `load()` branches |
| `tests/test_adapter.py` | **Modified** — floor constants + token test, extend-only-merge test, no-file-still-floored test, two parity-test updates, two exception-fallback tests |
| `scripts/gate_blast_radius.py` | **Modified** — `--base-ref`, base-ref `load_config`, `_adapter_snapshot`, `_boundary_escalation_findings`, `main()` rewiring |
| `scripts/diff_rank.py` | **Modified** — `_safety_path_patterns()` exception-fallback floor union |
| `tests/test_blast_radius.py` | **Modified** — Task 2 inverts one pre-existing SKILL.md test in place; Task 4 replaces `run_script` with a git-repo-backed fixture; Task 5 adds semantic-diff cases |
| `commands/dark-factory-validate.md` | **Modified** — Phase 0 drops the shell pre-check, threads `--base-ref`, invokes the baked gate with fallback |
| `tests/test_validate_blast_gate_baseref.py` | **New** — static-assertion test on Phase 0's prose (no shell pre-check, `--base-ref`/`--clone-dir` present) |

Not touched (out of scope per spec Alternative 3 / Owner decision OD3):
`safety.hard_exclude_paths`, `scripts/factory_core/epic_autopilot.py`, `.archon/commands/`
(untracked, seeded from the image — `commands/dark-factory-validate.md` is the only edit site).

## Assumptions & known limitations (carried into the PR description)

- **`.claude/skills/refinement/config.yaml` is untracked in the dark-factory self-target repo
  itself** (materialized at container start from the baked `config/config.yaml`; listed in
  `.git/info/exclude`). Under Requirement 8 the base-ref read therefore always misses on *this*
  repo. Operator review P1: `load_config` layers the **image-baked** `config/config.yaml`
  `blast_radius:` block underneath the base-ref read (`/opt/dark-factory/config/config.yaml`,
  `FACTORY_CONFIG_PATH` override as in `entrypoint.sh`), so on the self target the baked block
  governs and a future change to `config/config.yaml`'s `blast_radius:` reaches the gate on the
  next image; a target that commits the file has its committed values win key by key. Both
  layers are trusted (the image and the merged base), never the PR under review; hardcoded
  defaults apply only to keys neither layer sets. Test fixtures point `FACTORY_CONFIG_PATH` at
  an absent file so runs inside the image stay hermetic.
- **The baked `/opt/dark-factory/scripts/gate_blast_radius.py` path (Task 6) is preferred but
  not required** — the invocation falls back to the clone-relative copy when the baked path is
  absent (e.g. local/non-container test runs), per spec F9's explicit fallback allowance.
- **The three F2 shadowing-path floor entries (`^\.archon/commands/`, `^\.archon/workflows/`,
  `^dark-factory/scripts/`) sub-classify as the generic `migration-seed` TRIGGER label, not a
  more specific one.** Task 1's `SKILL_SECURITY_TOKENS` broadening fixes this for `^\.claude/`
  (a genuine Claude-Skills-surface match) but does not extend to these three, since they are a
  different threat model (shadowing baked enforcement, not the Claude Skills self-modification
  surface) and mislabeling them `skill-security` would itself be inaccurate. The spec does not
  ask for a distinct label for this case (only `boundary-escalation`, which this plan does add).
  Left as `migration-seed` — accurate enough (still blocks) but non-specific; a `factory-shadow`
  label is a candidate follow-up, not introduced here to avoid scope creep on a ticket whose
  acceptance criteria don't require it.
- **This ticket widens gate behavior in two ways beyond a literal reading of "the floor closes a
  bypass":** (a) `^\.claude/` in the *blocking* migration-seed floor means any edit under
  `.claude/**` — including `SKILL.md`/`RUBRIC.md` prose, previously visibility-only per #46 — now
  triggers `HUMAN_REQUIRED`; this is spec-sanctioned (Requirement 3/4a, and CLAUDE.md's own
  `.claude/**` self-modification framing) but is a real increase in how often self-target PRs hit
  `needs-discussion`. (b) `^commands/` in `FACTORY_OWNED_CRITICAL_DIFF_FLOOR` makes every
  `commands/**` file critical-tier in `diff_rank.py`'s review-ordering (visibility only, never
  blocking — `commands/` stays out of the blocking floor per Owner decision OD1), and
  `diff_rank.py` emits critical-tier files in full, bypassing the token cap — a real, if
  intentional, increase to code-review token cost for command-file-touching PRs, of which there
  are many on this repo. Both are accepted consequences of the approved spec, not defects, but
  are called out explicitly here (rather than left implicit in the floor's regex list) so a
  reviewer isn't surprised by the volume of `needs-discussion` labels or token spend after this
  ships.
- **Requirement 6's literal wording** ("`tests/test_blast_radius.py` stays green without
  modification to its non-adapter-yaml cases") is not met verbatim: Task 4 deletes
  `test_disabled_produces_skipped` and inverts `test_skill_md_alone_does_not_trigger`'s
  assertion. Both changes are required by, and directly justified by, Requirement 8's kill-switch
  fix and Requirement 3/4a's `.claude/**` broadening respectively — not incidental scope creep —
  so this is intentional, not a violation of Requirement 6's intent (which is about *unrelated*
  categories staying stable, e.g. hotspot/auth-router/size cases, all of which are untouched).

---

## Task 1: `adapter_defaults.py` — floor constants + skill-security token broadening

**Files:** `scripts/factory_core/adapter_defaults.py`, `tests/test_adapter.py`

### TDD Steps

1. Add the failing tests to `tests/test_adapter.py` (append near the `SKILL_SECURITY_TOKENS`
   parity tests, after `test_skill_security_tokens_parity`, ~line 986):

```python
# ── Boundary floor constants (#200/A6) ──────────────────────────────────────

def test_boundary_floor_constants_shape():
    floor = adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR
    blocking = adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR
    for pat in (
        r"^\.factory/hooks/", r"^\.factory/adapter\.yaml$", r"^\.claude/",
        r"^workflows/", r"^commands/", r"^\.archon/commands/",
        r"^\.archon/workflows/", r"^dark-factory/scripts/",
    ):
        assert pat in floor, f"{pat} missing from FACTORY_OWNED_CRITICAL_DIFF_FLOOR"
    assert len(floor) == 8
    # adapter.yaml itself and workflows/commands are visibility-only (OD1/OD2):
    # present in the critical-diff floor, excluded from the blocking floor.
    for visibility_only in (r"^\.factory/adapter\.yaml$", r"^workflows/", r"^commands/"):
        assert visibility_only not in blocking
    for hard_trigger in (
        r"^\.factory/hooks/", r"^\.claude/", r"^\.archon/commands/",
        r"^\.archon/workflows/", r"^dark-factory/scripts/",
    ):
        assert hard_trigger in blocking
    assert len(blocking) == 5
    # blocking floor is derived from the critical-diff floor, not hand-duplicated
    assert set(blocking) <= set(floor)


def test_skill_security_tokens_matches_bare_claude_prefix():
    """SKILL_SECURITY_TOKENS must sub-classify the new floor's bare ^\\.claude/ entry
    as skill-security (CLAUDE.md's own framing: '.claude/** self-modification
    mechanism (#46)'), not the generic migration-seed bucket -- otherwise the blocking
    issue comment's verbatim TRIGGER label misdescribes a Claude-Skills-surface
    finding. Checked directly against the constant here (self-contained to this task);
    tests/test_adapter.py::test_boundary_floor_claude_prefix_classifies_as_skill_security
    (Task 2) verifies the same thing end-to-end through classify_file once the floor
    is actually wired into gate_blast_radius.py's pattern list."""
    assert any(tok in r"^\.claude/" for tok in adapter_defaults.SKILL_SECURITY_TOKENS)
```

2. Run: `python -m pytest tests/test_adapter.py -k "boundary_floor or skill_security_tokens_matches" -v`
   → both fail: `test_boundary_floor_constants_shape` with `AttributeError` (constants
   don't exist); `test_skill_security_tokens_matches_bare_claude_prefix` with
   `AssertionError` (the tuple still reads `"claude/skills"`, not `"claude/"`, and
   `"claude/skills"` is not a substring of `"^\.claude/"`, so `any(...)` is `False`).

3. Add the constants to `scripts/factory_core/adapter_defaults.py`, immediately after the
   `DEFAULTS = {...}` block closes (after line 103):

```python

# Factory-owned boundary paths (#200/A6): unioned into safety.critical_diff_paths and
# safety.migration_seed_auth_patterns after every adapter.yaml merge (adapter.py::load),
# regardless of what a target's adapter.yaml declares for those two lists. Deliberately
# small and boundary-specific -- not the full DEFAULTS lists, which would re-inject
# MarketHawk-specific paths (e.g. ^alembic/versions/) into every target.
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
# .factory/adapter.yaml itself is excluded here: its loops: block is target-definable
# for side_effect_level 1-3 (#196), so blanket-blocking the whole file on every edit
# would require human review for benign loop authoring; its escalation risk is caught
# by gate_blast_radius.py's semantic diff instead. workflows/ and commands/ are
# excluded in v1 by Owner decision OD1 (visibility-only) -- promoting them is a
# one-line change to this set. Derived from the list above (not duplicated) so the
# two floors cannot drift apart.
_VISIBILITY_ONLY = {r"^\.factory/adapter\.yaml$", r"^workflows/", r"^commands/"}
FACTORY_OWNED_MIGRATION_SEED_FLOOR = [
    p for p in FACTORY_OWNED_CRITICAL_DIFF_FLOOR if p not in _VISIBILITY_ONLY
]
```

   Then broaden the existing `SKILL_SECURITY_TOKENS` tuple (lines 8-10) so the new floor's
   bare `^\.claude/` entry sub-classifies correctly. `"claude/"` is a strict superset of the
   old `"claude/skills"` token (every existing pattern containing `"claude/skills"` also
   contains `"claude/"`; `"claude-plugin"` stays a separate token since `"\.claude-plugin/"`
   does not contain the substring `"claude/"`), so this is additive-only for every existing
   match and only widens the new floor's classification:

```python
SKILL_SECURITY_TOKENS = (
    "claude/", "settings", "mcp", "claude/plugins", "claude-plugin", "factory/hooks",
)
```

   (Replacing `"claude/skills"` with `"claude/"`; `"claude/plugins"` becomes redundant
   under the broader `"claude/"` token but is left in place — harmless duplication, not
   worth a separate cleanup task.)

3b. Update the two `SKILL.md is visibility-only` comments in `scripts/factory_core/adapter_defaults.py`
   (the `critical_diff_paths` block, ~lines 65-66, and the `migration_seed_auth_patterns` block,
   ~lines 80-83): append the sentence "Superseded at the gate by `FACTORY_OWNED_MIGRATION_SEED_FLOOR`
   (`^\\.claude/`, #200/OD7): the floor blocks any `.claude/**` edit; this DEFAULTS list stays as it is."
   so the comments no longer contradict the floor (operator review P6). Comment-only; no test.

4. Run: `python -m pytest tests/test_adapter.py -k "boundary_floor or skill_security" -v`
   → both pass. This task is fully self-contained and green on its own — the
   `classify_file`-level, end-to-end proof that a real `.claude/` path gets labeled
   `skill-security` is added in Task 2 instead (it can't go green here: the floor isn't
   wired into `_migration_seed_auth_patterns`, which `classify_file` reads, until
   Task 2's `_apply_boundary_floor` lands — adding it to this task would mean
   committing a knowingly-red test against this repo's `python -m pytest tests/ -v` CI
   convention).

5. Run `python -m pytest tests/ -v` to confirm nothing else regressed (expect
   `test_skill_security_globs_in_defaults_critical_diff_paths` and
   `test_skill_scripts_and_settings_in_migration_seed_auth_patterns` still pass — they
   assert on `DEFAULTS["safety"][...]` content directly, untouched by the token change).

6. Commit:
```bash
git add scripts/factory_core/adapter_defaults.py tests/test_adapter.py
git commit -m "feat(#200): add factory-owned boundary path floor constants"
```

---

## Task 2: `adapter.py` — apply the floor after every merge

**Files:** `scripts/factory_core/adapter.py`, `tests/test_adapter.py`

### TDD Steps

1. Update the now-broken `test_no_adapter_file_returns_defaults` (line 10-12) and add two
   new failing tests, placed directly after it:

```python
def test_no_adapter_file_returns_defaults(tmp_path):
    merged = adapter.load(str(tmp_path))
    expected = copy.deepcopy(adapter_defaults.DEFAULTS)
    expected["safety"]["critical_diff_paths"] = list(expected["safety"]["critical_diff_paths"]) + [
        p for p in adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR
        if p not in expected["safety"]["critical_diff_paths"]
    ]
    expected["safety"]["migration_seed_auth_patterns"] = list(
        expected["safety"]["migration_seed_auth_patterns"]
    ) + [
        p for p in adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR
        if p not in expected["safety"]["migration_seed_auth_patterns"]
    ]
    assert merged == expected


def test_boundary_floor_survives_narrowed_safety_lists(tmp_path):
    """Requirement 7: an adapter.yaml that declares an empty/narrowed
    critical_diff_paths / migration_seed_auth_patterns still resolves, via
    adapter.get(), to a list containing the full floor -- the merge is extend-only."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n"
        "  critical_diff_paths: []\n"
        "  migration_seed_auth_patterns: []\n"
    )
    critical = adapter.get(str(tmp_path), "safety.critical_diff_paths")
    migration = adapter.get(str(tmp_path), "safety.migration_seed_auth_patterns")
    for pat in adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR:
        assert pat in critical
    for pat in adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR:
        assert pat in migration


def test_boundary_floor_applied_without_adapter_file(tmp_path):
    """Requirement 9: a target cannot escape the floor by having no adapter.yaml at all."""
    critical = adapter.get(str(tmp_path), "safety.critical_diff_paths")
    migration = adapter.get(str(tmp_path), "safety.migration_seed_auth_patterns")
    for pat in adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR:
        assert pat in critical
    for pat in adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR:
        assert pat in migration


def test_boundary_floor_claude_prefix_classifies_as_skill_security(tmp_path):
    """End-to-end counterpart to Task 1's test_skill_security_tokens_matches_bare_claude_prefix:
    once the floor is actually wired into _migration_seed_auth_patterns (this task),
    gate_blast_radius.classify_file must label a real .claude/ path skill-security, not
    the generic migration-seed bucket."""
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    cats = gbr.classify_file(".claude/skills/code-review/SKILL.md", hotspots=set(),
                              clone_dir=str(tmp_path))
    assert "skill-security" in cats
```

2. Run: `python -m pytest tests/test_adapter.py -k "no_adapter_file_returns_defaults or boundary_floor_survives or boundary_floor_applied or boundary_floor_claude_prefix" -v`
   → all four fail. `test_boundary_floor_survives_narrowed_safety_lists`,
   `test_boundary_floor_applied_without_adapter_file`, and
   `test_boundary_floor_claude_prefix_classifies_as_skill_security` fail with an
   `AssertionError` (`assert pat in ...` / `assert "skill-security" in cats` — `cats`
   is `[]`), because `adapter.get()` still returns the unfloored defaults/override
   (`_apply_boundary_floor` doesn't exist yet).
   `test_no_adapter_file_returns_defaults` also fails at this point: its rewritten body
   computes `expected` as `DEFAULTS` *plus* the floor (7 new critical-diff patterns, 4
   new migration-seed patterns — only `^\.factory/hooks/` already exists in `DEFAULTS`),
   while `adapter.load()` still returns unfloored `DEFAULTS` verbatim, so `merged !=
   expected`. All four are genuine red-phase failures fixed together by step 3.

3. Implement in `scripts/factory_core/adapter.py`. Add the helper directly above `load()`
   (after `_deep_merge`, before `def load`):

```python
def _apply_boundary_floor(safety: dict) -> dict:
    """Union the non-overridable boundary floor into critical_diff_paths and
    migration_seed_auth_patterns -- extend-only, never removes a target's own entries
    (Requirement 7). Called from every load() return path (Requirement 9)."""
    def _union(existing, floor):
        return list(existing) + [p for p in floor if p not in existing]
    safety = dict(safety)
    safety["critical_diff_paths"] = _union(
        safety.get("critical_diff_paths", []),
        adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR,
    )
    safety["migration_seed_auth_patterns"] = _union(
        safety.get("migration_seed_auth_patterns", []),
        adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR,
    )
    return safety
```

   Then change `load()`'s two return paths:

```python
def load(clone_dir: str) -> dict:
    path = os.path.join(clone_dir, ".factory", "adapter.yaml")
    if not os.path.isfile(path):
        data = copy.deepcopy(adapter_defaults.DEFAULTS)
        data["safety"] = _apply_boundary_floor(data["safety"])
        return data
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise AdapterError(f"adapter.yaml unreadable/unparseable: {exc}") from exc
```

   ... (validation body unchanged) ...

```python
    merged = _deep_merge(adapter_defaults.DEFAULTS, data)
    merged["safety"] = _apply_boundary_floor(merged["safety"])
    return merged
```

   (replacing the old bare `return _deep_merge(adapter_defaults.DEFAULTS, data)` at the
   end of the function.)

4. Run: `python -m pytest tests/test_adapter.py -v` → all pass except
   `test_safety_path_patterns_default_parity` / `test_migration_seed_auth_patterns_default_parity`
   (lines 801/823), which now fail — expected, fixed in step 5 as a direct, mechanical
   consequence of this same change.

5. Update the two default-parity tests (lines 801-806 and 823-828) to expect the floored
   result:

```python
def test_safety_path_patterns_default_parity(tmp_path):
    """Without adapter file, _safety_path_patterns returns DEFAULTS ∪ the boundary floor."""
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    patterns = [p.pattern for p in dr._safety_path_patterns(str(tmp_path))]
    raw = adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]
    expected = list(raw) + [
        p for p in adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR if p not in raw
    ]
    assert patterns == expected
```

```python
def test_migration_seed_auth_patterns_default_parity(tmp_path):
    """Without adapter file, _migration_seed_auth_patterns returns DEFAULTS ∪ the boundary floor."""
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    patterns = [p.pattern for p in gbr._migration_seed_auth_patterns(str(tmp_path))]
    raw = adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]
    expected = list(raw) + [
        p for p in adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR if p not in raw
    ]
    assert patterns == expected
```

6. Run: `python -m pytest tests/ -v` → full suite green **except**
   `tests/test_blast_radius.py::test_skill_md_alone_does_not_trigger`, which now fails:
   that test's `run_script([".claude/skills/code-review/SKILL.md"])` call resolves
   `clone_dir="."` (the file's `_hermetic_cwd` autouse fixture chdirs to an adapter-free
   `tmp_path` and never passes `--clone-dir`), so `adapter.load()`'s no-file branch now
   returns `DEFAULTS ∪ FACTORY_OWNED_MIGRATION_SEED_FLOOR`, `^\.claude/` matches, and the
   gate returns `HUMAN_REQUIRED` instead of the test's asserted `PASS`. This must be
   fixed **in this task**, not deferred to Task 4's full-file rewrite — leaving it red
   after this commit would violate this repo's `python -m pytest tests/ -v` CI
   convention for one full task's worth of commits. Confirm this is the *only*
   newly-red test (`python -m pytest tests/test_blast_radius.py -v` — every other case
   in that file is untouched by floor membership).

7. Edit `tests/test_blast_radius.py` in place (the file still has its pre-Task-4
   structure — module-level `run_script`, no `--base-ref` yet — so this is a narrow,
   single-test edit, not the fixture rewrite Task 4 performs later):

```python
def test_skill_md_now_triggers_via_broadened_claude_floor():
    """Behavior change from the pre-#200 gate (intentional, per spec Requirement 3/4a
    and CLAUDE.md's '.claude/** self-modification mechanism' framing): the new
    FACTORY_OWNED_MIGRATION_SEED_FLOOR entry ^\\.claude/ is broader than DEFAULTS'
    existing migration_seed_auth_patterns (which deliberately exempt bare SKILL.md,
    spec Q2/A2 of #46) and now blocks any .claude/ path, including SKILL.md prose.
    DEFAULTS itself is unchanged -- test_skill_md_not_in_migration_seed_auth_patterns
    (tests/test_adapter.py) still passes -- this is the *floor* catching what DEFAULTS
    alone does not."""
    out = run_script([".claude/skills/code-review/SKILL.md"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"
```

   (Replacing the old `test_skill_md_alone_does_not_trigger`, which asserted the
   opposite, pre-fix `PASS`. Task 4's later full-file rewrite of this test file already
   defines this exact function under this exact name — that rewrite carries it forward
   unchanged; there is nothing further to do there for this specific test.)

8. Run: `python -m pytest tests/ -v` → full suite green, including this task's own
   `test_boundary_floor_claude_prefix_classifies_as_skill_security` (`python -m pytest
   tests/test_adapter.py -k claude_prefix_classifies -v` to confirm specifically — this
   is the step that makes it pass: `adapter.get()`'s primary path now returns the
   floored `migration_seed_auth_patterns` list, so `classify_file` finds the
   `^\.claude/` match) and the just-edited `test_skill_md_now_triggers_via_broadened_claude_floor`.

9. Commit:
```bash
git add scripts/factory_core/adapter.py tests/test_adapter.py tests/test_blast_radius.py
git commit -m "feat(#200): apply boundary floor to safety lists on every adapter.py::load()"
```

---

## Task 3: exception-fallback paths also carry the floor (Requirement 9)

**Files:** `scripts/gate_blast_radius.py`, `scripts/diff_rank.py`, `tests/test_adapter.py`

### TDD Steps

1. Add two failing tests to `tests/test_adapter.py`, after
   `test_migration_seed_auth_patterns_adapter_override` (~line 840):

```python
def test_migration_seed_auth_patterns_exception_fallback_still_floored(tmp_path, monkeypatch):
    """Requirement 9: even if adapter.get() itself raises (broken/unimportable adapter
    module), the except-Exception fallback must not drop the boundary floor."""
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr

    def _boom(*a, **kw):
        raise RuntimeError("adapter unimportable")

    monkeypatch.setattr("factory_core.adapter.get", _boom)
    patterns = [p.pattern for p in gbr._migration_seed_auth_patterns(str(tmp_path))]
    for pat in adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR:
        assert pat in patterns
```

```python
def test_safety_path_patterns_exception_fallback_still_floored(tmp_path, monkeypatch):
    sys.path.insert(0, "scripts")
    import diff_rank as dr

    def _boom(*a, **kw):
        raise RuntimeError("adapter unimportable")

    monkeypatch.setattr("factory_core.adapter.get", _boom)
    patterns = [p.pattern for p in dr._safety_path_patterns(str(tmp_path))]
    for pat in adapter_defaults.FACTORY_OWNED_CRITICAL_DIFF_FLOOR:
        assert pat in patterns
```

2. Run: `python -m pytest tests/test_adapter.py -k exception_fallback_still_floored -v`
   → fails (`_migration_seed_auth_patterns`/`_safety_path_patterns` fall back to the bare,
   unfloored `MIGRATION_SEED_AUTH_PATTERNS`/`SAFETY_PATH_PATTERNS` module constants).

3. In `scripts/gate_blast_radius.py`, add the floor import next to the existing
   `adapter_defaults` import (~line 90) and update `_migration_seed_auth_patterns`:

```python
# adapter_defaults is the sole source of truth. A missing/broken import fails
# loudly here instead of silently falling back to a stale copy.
from factory_core.adapter_defaults import DEFAULTS as _AD
from factory_core.adapter_defaults import (
    FACTORY_OWNED_MIGRATION_SEED_FLOOR as _MIGRATION_SEED_FLOOR,
)

MIGRATION_SEED_AUTH_PATTERNS = [
    re.compile(p) for p in _AD["safety"]["migration_seed_auth_patterns"]
]


def _migration_seed_auth_patterns(clone_dir: str | None = None) -> list:
    """Return compiled migration/seed/auth patterns, reading from adapter at use-time.

    Falls back to MIGRATION_SEED_AUTH_PATTERNS ∪ the boundary floor on any error, so a
    broken/unimportable adapter module can never drop the floor (Requirement 9). The
    bare MIGRATION_SEED_AUTH_PATTERNS module constant is left un-floored -- it stays a
    verbatim re-export of DEFAULTS so tests/test_adapter.py::test_migration_seed_auth_patterns_default_parity's
    sibling identity checks (e.g. test_skill_md_not_in_migration_seed_auth_patterns,
    which reads adapter_defaults.DEFAULTS directly, not this function) keep pinning
    DEFAULTS exactly.
    """
    try:
        from factory_core import adapter
        val = adapter.get(clone_dir or ".", "safety.migration_seed_auth_patterns")
        if val is not None and isinstance(val, list):
            return [re.compile(p) for p in val]
    except Exception:
        pass
    raw = _AD["safety"]["migration_seed_auth_patterns"]
    floored = list(raw) + [p for p in _MIGRATION_SEED_FLOOR if p not in raw]
    return [re.compile(p) for p in floored]
```

4. Mirror in `scripts/diff_rank.py` (~line 56):

```python
# adapter_defaults is the sole source of truth. A missing/broken import fails
# loudly here instead of silently falling back to a stale copy.
from factory_core.adapter_defaults import DEFAULTS as _AD
from factory_core.adapter_defaults import (
    FACTORY_OWNED_CRITICAL_DIFF_FLOOR as _CRITICAL_DIFF_FLOOR,
)

SAFETY_PATH_PATTERNS = [re.compile(p) for p in _AD["safety"]["critical_diff_paths"]]


def _safety_path_patterns(clone_dir: str | None = None) -> list:
    """Return compiled safety path patterns, reading from adapter at use-time.

    Falls back to SAFETY_PATH_PATTERNS ∪ the boundary floor on any error (Requirement 9).
    The bare SAFETY_PATH_PATTERNS module constant is left un-floored (parity test pin,
    tests/test_adapter.py::test_critical_diff_paths_parity).
    """
    try:
        from factory_core import adapter
        val = adapter.get(clone_dir or ".", "safety.critical_diff_paths")
        if val is not None and isinstance(val, list):
            return [re.compile(p) for p in val]
    except Exception:
        pass
    raw = _AD["safety"]["critical_diff_paths"]
    floored = list(raw) + [p for p in _CRITICAL_DIFF_FLOOR if p not in raw]
    return [re.compile(p) for p in floored]
```

5. Run: `python -m pytest tests/ -v` → full suite green. In particular:
   - `test_critical_diff_paths_parity` and `test_skill_md_not_in_migration_seed_auth_patterns`
     still pass unchanged (the bare module constants are untouched).
   - `test_boundary_floor_claude_prefix_classifies_as_skill_security` and
     `test_skill_security_tokens_matches_bare_claude_prefix` (Task 2 and Task 1
     respectively) are still green — this task's exception-fallback change is not on
     the code path either test exercises, so neither must regress.

6. Commit:
```bash
git add scripts/gate_blast_radius.py scripts/diff_rank.py tests/test_adapter.py
git commit -m "feat(#200): float the boundary floor into the adapter-lookup exception fallback"
```

---

## Task 4: `gate_blast_radius.py` — base-ref kill switch (Requirement 8)

**Files:** `scripts/gate_blast_radius.py`, `tests/test_blast_radius.py`

This task rewrites `tests/test_blast_radius.py`'s test harness: `load_config` no longer reads
`--config` from disk, it reads `git show <base-ref>:<config-path>` inside `--clone-dir`, so
every existing test (even the simplest ones) now needs a real, committed git repo. The
`run_script` module-level function becomes a `run_script` pytest fixture backed by one; each
existing test's signature changes from `def test_x():` to `def test_x(run_script):` (bodies
unchanged) so the mechanical diff stays small. This task also **removes** the file's old
`_hermetic_cwd` autouse fixture (no longer needed — every test now passes `--clone-dir`
explicitly, so no ambient-cwd dependency remains) and **removes** `test_disabled_produces_skipped`,
replacing it with `test_disabled_does_not_suppress_migration_seed_match` below: the old test
asserted `SKIPPED` for a disabled gate against an `alembic/versions/abc.py` change, which is
precisely the F1 bug this ticket fixes (a migration-seed match must never be suppressible by
`enabled: false`) — so its old assertion is now the *wrong*, pre-fix behavior, not a case to
preserve.

### TDD Steps

1. Rewrite `tests/test_blast_radius.py` in full (replacing the existing 212-line file):

```python
"""Tests for gate_blast_radius.py — deterministic file classifier."""
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate_blast_radius.py"

_DEFAULT_BLAST_CFG = {
    "enabled": True,
    "hotspot_score_floor": 5.0,
    "size_budget_lines": 400,
    "size_budget_blocks": False,
}
_CONFIG_REL = ".claude/skills/refinement/config.yaml"


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _init_repo(root):
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def _write_config(root, config_extra=None):
    cfg = {"blast_radius": dict(_DEFAULT_BLAST_CFG)}
    if config_extra:
        cfg["blast_radius"].update(config_extra)
    path = root / _CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg))


def _parse(stdout: str) -> dict:
    result = {"_stdout": stdout}
    for line in stdout.splitlines():
        if ": " in line and not line.startswith("  "):
            k, v = line.split(": ", 1)
            result[k] = v
    return result


@pytest.fixture
def run_script(tmp_path, monkeypatch):
    """Run the gate against a fresh git repo whose config.yaml is committed on `main`
    each call (Requirement 8: blast_radius.* is read from --base-ref via `git show`,
    never the working tree, so a real repo is required even for the simplest case).

    `worktree_config_extra`, when given, overwrites the *working-tree* copy of
    config.yaml after the base-ref commit -- simulating a PR that edits its own local
    config.yaml in the same change -- without touching what was committed as the base
    ref, so tests can assert the kill switch follows the committed value only.
    """
    root = tmp_path
    _init_repo(root)
    # Hermetic: never read the image's real baked config from inside a test run (P1).
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))

    def _run(changed_files, hotspots_content="", lines_changed=50, config_extra=None,
              base_ref="main", worktree_config_extra=None):
        _write_config(root, config_extra)
        hf = root / "hotspots.md"
        hf.write_text(hotspots_content)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--allow-empty", "-m", "config")
        if worktree_config_extra is not None:
            _write_config(root, worktree_config_extra)
        proc = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--changed-files-stdin",
                "--lines-changed", str(lines_changed),
                "--hotspots", str(hf),
                "--config", _CONFIG_REL,
                "--clone-dir", str(root),
                "--base-ref", base_ref,
            ],
            input="\n".join(changed_files),
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return _parse(proc.stdout)

    return _run


def test_no_triggers_produces_pass(run_script):
    out = run_script(["frontend/src/components/Foo.tsx", "docs/some-doc.md"])
    assert out["STATUS"] == "PASS"
    assert out["GATE_TYPE"] == "blast"
    assert out["SEVERITY"] == "none"


def test_migration_file_triggers_human_required(run_script):
    out = run_script(["alembic/versions/abc123_add_col.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["SEVERITY"] == "critical"


def test_seed_sql_triggers_human_required(run_script):
    out = run_script(["dark-factory/seed/02_scanner_data.sql"])
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_auth_router_triggers_human_required(run_script):
    out = run_script(["backend/app/routers/auth.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_hotspot_file_above_floor_triggers_human_required(run_script):
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots)
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_hotspot_file_below_floor_does_not_trigger(run_script):
    hotspots = "    3.1  backend/app/services/scanner.py  (1d / 5t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots)
    assert out["STATUS"] == "PASS"


def test_size_blocking_when_enabled(run_script):
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_size_advisory_only_by_default(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], lines_changed=500)
    assert out["STATUS"] == "PASS"


def test_lines_changed_in_artifact(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], lines_changed=123)
    assert out["LINES_CHANGED"] == "123"


def test_disabled_with_non_boundary_file_produces_skipped(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], config_extra={"enabled": False})
    assert out["STATUS"] == "SKIPPED"


def test_disabled_does_not_suppress_migration_seed_match(run_script):
    """Requirement 8 (F1): enabled:false must not suppress a migration-seed/floor
    match, or a PR could edit a boundary path and flip enabled:false in the same
    change to escape review."""
    out = run_script(["alembic/versions/abc.py"], config_extra={"enabled": False})
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_disabled_suppresses_hotspot_trigger(run_script):
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots,
                      config_extra={"enabled": False})
    assert out["STATUS"] == "SKIPPED"


def test_disabled_suppresses_size_trigger(run_script):
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"enabled": False, "size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "SKIPPED"


def test_kill_switch_reads_base_ref_not_working_tree(run_script):
    """Requirement 8 (F1, operator review): a PR that edits a floor path AND rewrites
    its own working-tree config.yaml to enabled:false must still be blocked -- the
    committed base-ref config (enabled:True, the fixture default) is what governs the
    migration-seed/floor match regardless (this floor match is unsuppressible anyway,
    per test_disabled_does_not_suppress_migration_seed_match above); this test isolates
    the kill switch itself by also proving the working-tree copy has zero effect."""
    out = run_script(
        [".factory/hooks/h.sh", ".claude/skills/refinement/config.yaml"],
        worktree_config_extra={"enabled": False},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_kill_switch_working_tree_cannot_re_enable_size_trigger(run_script):
    """Symmetric check: if the base ref itself has enabled:False (a legitimately
    disabled gate), a PR flipping its own working-tree copy to enabled:True must not
    re-enable the (base-ref-gated) size trigger -- the base ref still governs."""
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"enabled": False, "size_budget_lines": 400, "size_budget_blocks": True},
        worktree_config_extra={"enabled": True, "size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "SKIPPED"


def test_kill_switch_working_tree_disable_does_not_suppress_hotspot(run_script):
    """Operator review P2: isolates the kill switch itself. Base ref enabled:True; the PR
    flips its working-tree copy to enabled:false; a hotspot file must still block with the
    hotspot label -- the working-tree value has no effect on the hotspot trigger."""
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots,
                     worktree_config_extra={"enabled": False})
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "hotspot"


def test_kill_switch_falls_back_to_baked_when_base_ref_lacks_config(tmp_path, monkeypatch):
    """Operator review P1: on the self target .claude/skills/refinement/config.yaml is
    untracked (materialized at container start), so `git show <base-ref>:<path>` always
    misses. blast_radius.* must then come from the image-baked config (trusted, never the
    PR under review), not from hardcoded defaults."""
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "README.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base without config")
    baked = tmp_path / "baked.yaml"
    baked.write_text(yaml.dump({"blast_radius": {"size_budget_lines": 100, "size_budget_blocks": True}}))
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(baked))
    hf = root / "hotspots.md"
    hf.write_text("")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "200",
         "--hotspots", str(hf), "--config", _CONFIG_REL, "--clone-dir", str(root), "--base-ref", "main"],
        input="frontend/src/components/Foo.tsx", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = _parse(proc.stdout)
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "size"


def test_kill_switch_base_ref_config_wins_over_baked(run_script, tmp_path, monkeypatch):
    """Operator review P1: a target that commits its config has the committed (base-ref)
    values win over the baked layer, key by key."""
    baked = tmp_path / "baked.yaml"
    baked.write_text(yaml.dump({"blast_radius": {"size_budget_lines": 100000, "size_budget_blocks": False}}))
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(baked))
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=200,
        config_extra={"size_budget_lines": 100, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "size"


def test_settings_json_triggers_skill_security(run_script):
    out = run_script([".claude/settings.json"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_script_triggers_skill_security(run_script):
    out = run_script([".claude/skills/code-review/scripts/foo.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_factory_hooks_triggers_skill_security(run_script):
    out = run_script([".factory/hooks/validate"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_md_now_triggers_via_broadened_claude_floor(run_script):
    """Behavior change from the pre-#200 gate (intentional, per spec Requirement 3/4a
    and CLAUDE.md's '.claude/** self-modification mechanism' framing): the new
    FACTORY_OWNED_MIGRATION_SEED_FLOOR entry ^\\.claude/ is broader than DEFAULTS'
    existing migration_seed_auth_patterns (which deliberately exempt bare SKILL.md,
    spec Q2/A2 of #46) and now blocks any .claude/ path, including SKILL.md prose.
    DEFAULTS itself is unchanged -- test_skill_md_not_in_migration_seed_auth_patterns
    (tests/test_adapter.py) still passes -- this is the *floor* catching what DEFAULTS
    alone does not."""
    out = run_script([".claude/skills/code-review/SKILL.md"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_migration_file_trigger_label_still_migration_seed(run_script):
    out = run_script(["alembic/versions/abc123_add_col.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "migration-seed"


def test_dark_factory_own_adapter_yaml_protects_skill_security():
    """Non-hermetic: run against this repo's real .factory/adapter.yaml (not the
    MarketHawk-parity default) to guard the A4 merge-semantics gap end to end.
    --base-ref HEAD: this repo's .claude/skills/refinement/config.yaml is itself
    untracked (see .git/info/exclude and this plan's Assumptions section), so
    `git show` misses and load_config falls back to the baked blast_radius block (or,
    outside the image, to {} / enabled=True defaults) -- irrelevant here since this test
    only exercises the floor-pattern match, not the kill switch."""
    repo_root = Path(SCRIPT).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as hf:
        hf.write("")
        hf.flush()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
             "--hotspots", hf.name, "--config", _CONFIG_REL,
             "--clone-dir", str(repo_root), "--base-ref", "HEAD"],
            input=".claude/settings.json",
            capture_output=True, text=True,
        )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "TRIGGER: skill-security" in proc.stdout
```

2. Run: `python -m pytest tests/test_blast_radius.py -v` → every test fails at collection
   or execution, since `--base-ref` is not yet a recognized flag and `load_config` still
   reads disk directly (the temp-repo's config path is never consulted the old way).

3. Implement in `scripts/gate_blast_radius.py`. Add `import os` and `import subprocess` to the top-level
   imports (after `import re`). Add the CLI flag in `parse_args()`, directly after
   `--clone-dir`:

```python
    p.add_argument(
        "--base-ref",
        default="main",
        help="Git ref to read blast_radius.* config (Requirement 8) and the pre-PR "
             "adapter.yaml snapshot from; never the working tree",
    )
```

   Replace `load_config`:

```python
_BAKED_CONFIG_PATH = "/opt/dark-factory/config/config.yaml"  # == effective_config._BAKED_PATH; entrypoint.sh:88


def _baked_blast_config() -> dict:
    """blast_radius.* from the image-baked config -- COPY'd at image build from main,
    never the PR under review. FACTORY_CONFIG_PATH mirrors entrypoint.sh:88 (test seam;
    never set in the image). {} when absent/unreadable."""
    try:
        import yaml  # type: ignore
        with open(os.environ.get("FACTORY_CONFIG_PATH", _BAKED_CONFIG_PATH), encoding="utf-8") as f:
            blk = (yaml.safe_load(f) or {}).get("blast_radius", {})
        return blk if isinstance(blk, dict) else {}
    except Exception:
        return {}


def load_config(path: str, clone_dir: str, base_ref: str) -> dict:
    """Layered, never from the working tree (Requirement 8 / F1; operator review P1):
    baked blast_radius block  <-  `git show <base-ref>:<path>` block (key by key).

    On the self target <path> is untracked (materialized from the baked file at container
    start and git-excluded), so the base-ref read misses and the baked block governs; a
    target that commits <path> has its committed values win. Hardcoded defaults apply only
    to keys neither layer sets. Both layers are trusted -- the image and the merged base --
    so the fallback direction stays "gate runs normally", never a PR-controlled value.

    `path` must be relative to clone_dir -- `git show <ref>:<path>` requires a
    repo-relative path. The one caller (commands/dark-factory-validate.md, Task 6) always
    passes the existing relative literal ".claude/skills/refinement/config.yaml".
    """
    cfg = dict(_baked_blast_config())
    try:
        import yaml  # type: ignore
        proc = subprocess.run(
            ["git", "-C", clone_dir, "show", f"{base_ref}:{path}"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            blk = (yaml.safe_load(proc.stdout) or {}).get("blast_radius", {})
            if isinstance(blk, dict):
                cfg.update(blk)
    except Exception:
        pass
    return cfg
```

   In `main()`, replace the `cfg = load_config(args.config)` line and the early-return
   disabled block:

```python
def main() -> None:
    args = parse_args()
    cfg = load_config(args.config, args.clone_dir, args.base_ref)
    enabled = cfg.get("enabled", True)

    score_floor = float(cfg.get("hotspot_score_floor", 5.0))
    size_budget = int(cfg.get("size_budget_lines", 400))
    size_blocks = bool(cfg.get("size_budget_blocks", False))

    hotspots = parse_hotspots(args.hotspots, score_floor)

    changed_files = []
    if args.changed_files_stdin:
        changed_files = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]

    lines_changed = args.lines_changed
    clone_dir = args.clone_dir

    # enabled:false suppresses only the hotspot and size triggers (operator review
    # F1/Requirement 8) -- a migration-seed/floor match (and the boundary-escalation
    # semantic step, wired in Task 5) must never be suppressible by a PR's own change.
    triggered = []
    for fpath in changed_files:
        cats = classify_file(fpath, hotspots, clone_dir=clone_dir)
        if not enabled:
            cats = [c for c in cats if c != "hotspot"]
        if cats:
            triggered.append((fpath, cats))

    hard_trigger = bool(triggered)
    size_trigger = enabled and size_blocks and lines_changed > size_budget

    if hard_trigger or size_trigger:
        status = "HUMAN_REQUIRED"
    elif not enabled:
        status = "SKIPPED"
    else:
        status = "PASS"

    severity = "critical" if status == "HUMAN_REQUIRED" else "none"
    findings_count = len(triggered) + (1 if size_trigger else 0)

    trigger_label = "none"
    if hard_trigger:
        cats_all = [c for _, cats in triggered for c in cats]
        if "hotspot" in cats_all:
            trigger_label = "hotspot"
        elif "skill-security" in cats_all:
            trigger_label = "skill-security"
        else:
            trigger_label = "migration-seed"
    elif size_trigger:
        trigger_label = "size"

    print(f"STATUS: {status}")
    print(f"GATE_TYPE: blast")
    print(f"FINDINGS_COUNT: {findings_count}")
    print(f"SEVERITY: {severity}")
    print("---")
    print(f"TRIGGER: {trigger_label}")
    print("TRIGGERED_FILES:")
    for fpath, cats in triggered:
        label = ", ".join(cats)
        print(f"  - {fpath} (category: {label})")
    if size_trigger:
        print(f"  - [size] {lines_changed} lines > {size_budget} budget")
    print(f"LINES_CHANGED: {lines_changed}")
```

   (This removes the old unconditional-early-return `if not cfg.get("enabled", True): ...
   return` block entirely; Task 5 adds the boundary-escalation wiring on top of this same
   `main()`.)

4. Run: `python -m pytest tests/test_blast_radius.py -v` → all pass except any
   boundary-escalation-specific tests, which don't exist yet in this task.

5. Run: `python -m pytest tests/ -v` → full suite green (this task doesn't touch
   `test_adapter.py` or `test_diff_rank.py`).

6. Commit:
```bash
git add scripts/gate_blast_radius.py tests/test_blast_radius.py
git commit -m "feat(#200): read blast-radius kill switch from base ref, never the working tree"
```

---

## Task 5: `gate_blast_radius.py` — boundary-escalation semantic diff

**Files:** `scripts/gate_blast_radius.py`, `tests/test_blast_radius.py`

### TDD Steps

1. Append a new fixture and test cases to `tests/test_blast_radius.py` (after
   `test_dark_factory_own_adapter_yaml_protects_skill_security`):

```python
# ── Boundary-escalation semantic diff (#200/A6, Requirement 5) ─────────────────

_BASE_LOOP = {
    "name": "refine-loop",
    "purpose": "test loop",
    "side_effect_level": 2,
    "discovery": {"trigger": "label", "inputs": ["issue"]},
    "handoff": {"manifest": "manifest.json", "outputs": ["out.md"]},
    "verification": {"verifier": "scripts/verify.sh", "stop_condition": "scripts/stop.sh"},
    "persistence": {"artifacts": ["out.md"]},
    "scheduling": {"failure_behavior": "retry"},
}


def _adapter_doc(safety=None, loops=None):
    return {"schema_version": 1, "safety": safety or {}, "loops": loops or []}


@pytest.fixture
def adapter_diff_run(tmp_path, monkeypatch):
    """Commit a base `.factory/adapter.yaml` on `main`, then hand back a callable that
    overwrites the *working-tree* copy (simulating a PR's HEAD state, uncommitted) and
    invokes the gate -- the semantic diff compares --base-ref vs. the working tree
    (Architecture section 3: 'new' is loaded from the working tree, 'old' from
    --base-ref), never two commits."""
    root = tmp_path
    _init_repo(root)
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))  # hermetic (P1)

    def _setup(base_adapter):
        _write_config(root)
        (root / "hotspots.md").write_text("")
        d = root / ".factory"; d.mkdir(exist_ok=True)
        text = base_adapter if isinstance(base_adapter, str) else yaml.dump(base_adapter)
        (d / "adapter.yaml").write_text(text)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")

        def _run(new_adapter, changed_files=None):
            text = new_adapter if isinstance(new_adapter, str) else yaml.dump(new_adapter)
            (d / "adapter.yaml").write_text(text)
            files = changed_files if changed_files is not None else [".factory/adapter.yaml"]
            proc = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--changed-files-stdin",
                    "--lines-changed", "10",
                    "--hotspots", str(root / "hotspots.md"),
                    "--config", _CONFIG_REL,
                    "--clone-dir", str(root),
                    "--base-ref", "main",
                ],
                input="\n".join(files),
                capture_output=True, text=True,
            )
            assert proc.returncode == 0, proc.stderr
            return _parse(proc.stdout)

        return _run

    return _setup


def test_boundary_diff_no_op_no_false_positive(adapter_diff_run):
    base = _adapter_doc(loops=[dict(_BASE_LOOP)])
    run = adapter_diff_run(base)
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    assert out["STATUS"] == "PASS"
    assert "boundary-escalation" not in out["_stdout"]


def test_boundary_diff_skipped_when_adapter_not_changed(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    out = run(_adapter_doc(safety={"x": "y"}), changed_files=["frontend/src/Foo.tsx"])
    assert out["STATUS"] == "PASS"


def test_boundary_diff_safety_block_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(safety={"critical_diff_paths": ["^a/"]}))
    out = run(_adapter_doc(safety={"critical_diff_paths": ["^a/", "^b/"]}))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "boundary-escalation"
    assert "safety: block changed" in out["_stdout"]


def test_boundary_diff_side_effect_level_increase_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=2)]))
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=3)]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "side_effect_level increased 2 -> 3" in out["_stdout"]


def test_boundary_diff_side_effect_level_decrease_not_flagged(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=3)]))
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=2)]))
    assert out["STATUS"] == "PASS"


def test_boundary_diff_verifier_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    new_loop = dict(_BASE_LOOP,
                     verification={"verifier": "scripts/other.sh", "stop_condition": "scripts/stop.sh"})
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "verification.verifier changed" in out["_stdout"]


def test_boundary_diff_stop_condition_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    new_loop = dict(_BASE_LOOP,
                     verification={"verifier": "scripts/verify.sh", "stop_condition": "scripts/other_stop.sh"})
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "verification.stop_condition changed" in out["_stdout"]


def test_boundary_diff_new_factory_owned_loop_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[]))
    new_loop = dict(_BASE_LOOP, name="new-loop", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "new loop declares side_effect_level 4" in out["_stdout"]


def test_boundary_diff_new_low_level_loop_not_flagged(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[]))
    new_loop = dict(_BASE_LOOP, name="new-loop", side_effect_level=2)
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "PASS"


def test_boundary_diff_factory_owned_loop_entry_change_flags(adapter_diff_run):
    """F7: any change to a >=4-level loop's entry is a finding, not just sel/verifier."""
    old_loop = dict(_BASE_LOOP, name="lvl4", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    run = adapter_diff_run(_adapter_doc(loops=[old_loop]))
    new_loop = dict(old_loop, purpose="changed purpose")
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "factory-owned loop" in out["_stdout"]


def test_boundary_diff_factory_owned_loop_removal_flags(adapter_diff_run):
    old_loop = dict(_BASE_LOOP, name="lvl4", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    run = adapter_diff_run(_adapter_doc(loops=[old_loop]))
    out = run(_adapter_doc(loops=[]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "removed" in out["_stdout"]


def test_boundary_diff_duplicate_loop_name_fails_closed(adapter_diff_run):
    """A duplicate loop name would silently collapse in the {name: loop} comparison
    maps -- adapter.py::load()'s own duplicate check must also gate this snapshot."""
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    dup = [dict(_BASE_LOOP), dict(_BASE_LOOP, side_effect_level=3)]
    out = run(_adapter_doc(loops=dup))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "unparseable" in out["_stdout"]


def test_boundary_diff_unparseable_head_fails_closed(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    out = run("{broken: [\n")
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "unparseable" in out["_stdout"]


def test_boundary_diff_unparseable_base_fails_closed(tmp_path):
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("{broken: [\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[dict(_BASE_LOOP)])))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "main"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "unparseable" in proc.stdout


def test_boundary_diff_unresolvable_base_ref_fails_closed(tmp_path):
    """Requirement 5's fail-closed rule extends to a base ref that doesn't resolve at
    all (e.g. a shallow clone without `main` reachable) -- distinct from "the file is
    simply absent at a ref that does resolve", which is a valid, non-error state."""
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[dict(_BASE_LOOP)])))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "totally-bogus-ref-xyz"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout


def test_boundary_diff_file_absent_at_resolvable_base_ref_is_not_an_error(tmp_path):
    """The base ref resolving fine but simply not having .factory/adapter.yaml at all
    yet (e.g. this PR is the one introducing it) is a valid 'no prior adapter' state,
    not a parse failure -- exercises the git-show stderr-inspection branch in
    _adapter_snapshot directly (the base commit below has no .factory/ directory at
    all, so `git show main:.factory/adapter.yaml` genuinely exits non-zero with "does
    not exist in" rather than succeeding on an empty-but-tracked file)."""
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    (root / "README.md").write_text("no adapter.yaml at this ref\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base, no adapter.yaml yet")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[])))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "main"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: PASS" in proc.stdout
```

2. Run: `python -m pytest tests/test_blast_radius.py -k boundary_diff -v` → all fail
   (`.factory/adapter.yaml` in the changed-file list has no effect yet; `TRIGGER` never
   becomes `boundary-escalation`).

3. Implement in `scripts/gate_blast_radius.py`. Add the two new functions directly above
   `main()`:

```python
def _adapter_snapshot(clone_dir: str, ref: str | None) -> tuple:
    """Return (parsed adapter.yaml dict, parse_ok) for `ref` (None = working tree).

    A missing file -- at the working tree, or at a *resolvable* ref -- is a valid "no
    adapter" state (matches adapter.py::load()'s own no-file branch) and returns
    ({}, True). parse_ok is False when: the ref itself does not resolve (distinct from
    the file being absent at a ref that does resolve -- inspected via `git show`'s
    stderr text, since both cases exit non-zero); the file exists but is malformed
    YAML or not a top-level mapping; a loops[] entry fails adapter.py's own
    loop-schema validation; or two loops[] entries share a name (mirrors
    adapter.py::load()'s duplicate-name check, since a duplicate would otherwise
    silently collapse in the {name: loop} comparison maps below). The caller fails
    closed on parse_ok=False (Requirement 5/OD6).
    """
    import yaml
    from factory_core import adapter as _adapter
    from factory_core import verifier as _verifier

    try:
        if ref is None:
            path = Path(clone_dir) / ".factory" / "adapter.yaml"
            if not path.is_file():
                return {}, True
            text = path.read_text(encoding="utf-8")
        else:
            proc = subprocess.run(
                ["git", "-C", clone_dir, "show", f"{ref}:.factory/adapter.yaml"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                stderr = proc.stderr
                if "does not exist in" in stderr or "exists on disk, but not in" in stderr:
                    return {}, True
                return None, False
            text = proc.stdout
        data = yaml.safe_load(text)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, False
        seen_names = set()
        for i, entry in enumerate(data.get("loops", []) or []):
            _adapter._validate_loop(entry, i)
            _verifier.assert_verifier_independent(entry)  # same check load() applies (operator review P5)
            name = entry.get("name")
            if name in seen_names:
                raise _adapter.AdapterError(f"duplicate loop name '{name}'")
            seen_names.add(name)
        return data, True
    except Exception:
        return None, False


def _boundary_escalation_findings(clone_dir: str, base_ref: str) -> list:
    """Semantic diff of .factory/adapter.yaml, base_ref vs. the working tree
    (Requirement 5). Only called by main() when that file is in the changed set."""
    from factory_core import side_effect as _side_effect

    old, old_ok = _adapter_snapshot(clone_dir, base_ref)
    new, new_ok = _adapter_snapshot(clone_dir, None)
    if not old_ok:
        return [f"adapter.yaml unparseable at {base_ref}"]
    if not new_ok:
        return ["adapter.yaml unparseable at HEAD"]

    findings = []
    # `or {}` normalizes "no safety: key at all" (a brand-new adapter.yaml, or the
    # file absent at that ref) and "safety: {}" (an explicit empty block) to the same
    # comparable value -- both mean "no explicit safety overrides", so introducing an
    # adapter.yaml with nothing under safety: must not itself read as a change.
    if (old.get("safety") or {}) != (new.get("safety") or {}):
        findings.append("safety: block changed")

    min_level = _side_effect.FACTORY_OWNED_MIN_LEVEL
    old_loops = {l["name"]: l for l in (old.get("loops") or [])}
    new_loops = {l["name"]: l for l in (new.get("loops") or [])}

    for name, new_loop in new_loops.items():
        old_loop = old_loops.get(name)
        if old_loop is None:
            sel = new_loop.get("side_effect_level")
            if isinstance(sel, int) and sel >= min_level:
                findings.append(
                    f"loops[{name}]: new loop declares side_effect_level {sel} "
                    f">= {min_level} (factory-owned)")
            continue

        old_sel = old_loop.get("side_effect_level")
        new_sel = new_loop.get("side_effect_level")
        if isinstance(old_sel, int) and isinstance(new_sel, int) and new_sel > old_sel:
            findings.append(
                f"loops[{name}]: side_effect_level increased {old_sel} -> {new_sel}")

        old_ver = old_loop.get("verification") or {}
        new_ver = new_loop.get("verification") or {}
        for field in ("verifier", "stop_condition"):
            if old_ver.get(field) != new_ver.get(field):
                findings.append(
                    f"loops[{name}]: verification.{field} changed "
                    f"{old_ver.get(field)!r} -> {new_ver.get(field)!r}")

        is_factory_owned = (
            (isinstance(old_sel, int) and old_sel >= min_level)
            or (isinstance(new_sel, int) and new_sel >= min_level)
        )
        if is_factory_owned and old_loop != new_loop:
            findings.append(
                f"loops[{name}]: factory-owned loop (side_effect_level >= "
                f"{min_level}) entry changed")

    for name, old_loop in old_loops.items():
        if name in new_loops:
            continue
        old_sel = old_loop.get("side_effect_level")
        if isinstance(old_sel, int) and old_sel >= min_level:
            findings.append(
                f"loops[{name}]: factory-owned loop (side_effect_level {old_sel}) removed")

    return findings
```

   Then wire it into `main()` (edits are relative to Task 4's version of `main()`):

```python
    triggered = []
    for fpath in changed_files:
        cats = classify_file(fpath, hotspots, clone_dir=clone_dir)
        if not enabled:
            cats = [c for c in cats if c != "hotspot"]
        if cats:
            triggered.append((fpath, cats))

    boundary_findings = []
    if ".factory/adapter.yaml" in changed_files:
        boundary_findings = _boundary_escalation_findings(clone_dir, args.base_ref)

    hard_trigger = bool(triggered)
    size_trigger = enabled and size_blocks and lines_changed > size_budget
    boundary_trigger = bool(boundary_findings)

    if hard_trigger or size_trigger or boundary_trigger:
        status = "HUMAN_REQUIRED"
    elif not enabled:
        status = "SKIPPED"
    else:
        status = "PASS"

    severity = "critical" if status == "HUMAN_REQUIRED" else "none"
    findings_count = len(triggered) + len(boundary_findings) + (1 if size_trigger else 0)

    trigger_label = "none"
    if boundary_trigger:
        trigger_label = "boundary-escalation"
    elif hard_trigger:
        cats_all = [c for _, cats in triggered for c in cats]
        if "hotspot" in cats_all:
            trigger_label = "hotspot"
        elif "skill-security" in cats_all:
            trigger_label = "skill-security"
        else:
            trigger_label = "migration-seed"
    elif size_trigger:
        trigger_label = "size"

    print(f"STATUS: {status}")
    print(f"GATE_TYPE: blast")
    print(f"FINDINGS_COUNT: {findings_count}")
    print(f"SEVERITY: {severity}")
    print("---")
    print(f"TRIGGER: {trigger_label}")
    print("TRIGGERED_FILES:")
    for finding in boundary_findings:
        print(f"  - {finding}")
    for fpath, cats in triggered:
        label = ", ".join(cats)
        print(f"  - {fpath} (category: {label})")
    if size_trigger:
        print(f"  - [size] {lines_changed} lines > {size_budget} budget")
    print(f"LINES_CHANGED: {lines_changed}")
```

4. Run: `python -m pytest tests/test_blast_radius.py -v` → full file green (all
   `boundary_diff` cases plus every case from Task 4).

5. Run: `python -m pytest tests/ -v` → full suite green.

6. Commit:
```bash
git add scripts/gate_blast_radius.py tests/test_blast_radius.py
git commit -m "feat(#200): semantic-diff .factory/adapter.yaml for boundary escalation"
```

---

## Task 6: `commands/dark-factory-validate.md` — remove the working-tree kill-switch pre-check, invoke the baked gate

**Files:** `commands/dark-factory-validate.md`, `tests/test_validate_blast_gate_baseref.py`

Today's Phase 0 reads `blast_radius.enabled` from the **working tree** in bash, *before*
`gate_blast_radius.py` ever runs, and writes `STATUS: SKIPPED` + returns early when it's
`false` — entirely bypassing the Python gate (and, with it, Tasks 4-5's fix) for any PR that
edits its own working-tree config. This task deletes that shell-level pre-check; the Python
gate (Task 4) already implements the correct `enabled` semantics internally (suppress
hotspot/size only; migration-seed/floor/boundary-escalation always run; base-ref-sourced, not
working-tree-sourced) and emits `STATUS: SKIPPED` itself when truly inert.

### Steps

1. Replace Phase 0 in `commands/dark-factory-validate.md` in full — from the
   `## Phase 0: BLAST-RADIUS HARD GATE` heading through the closing ` ``` ` fence (the
   entire current block, including the `BLAST_ENABLED` read, the early-SKIP `if`, the
   wrapping `if [ "$BLAST_ENABLED" != "skip" ]; then ... fi`, and everything inside it) —
   with:

```markdown
## Phase 0: BLAST-RADIUS HARD GATE

Derive the issue number from the persisted issue artifact:

```bash
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
```

The gate is invoked unconditionally (Requirement 8 / operator review F1): its own
`blast_radius.enabled` kill switch is resolved *inside* `gate_blast_radius.py` from
`--base-ref`, never from a working-tree read here — a PR that edits a floor path and flips
`enabled: false` in the same change must still be blocked. There is deliberately no
shell-level pre-check that reads `.claude/skills/refinement/config.yaml` and skips the
invocation; that would silently re-open the bypass this ticket closes.

```bash
# 1. Get changed files and real line count
CHANGED=$(git diff main...HEAD --name-only 2>/dev/null || echo "")
ADDED=$(git diff main...HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)
DELETED=$(git diff main...HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)
LINES=$((ADDED + DELETED))

# 2. Run the blast-radius checker — pass real line count via --lines-changed.
# Prefer the baked copy (operator review F9): a target that tracks its own
# dark-factory/scripts/gate_blast_radius.py (the F2 floor's shadowing-path entry
# detects that at PR time) must not be the copy that decides its own gate. Falls back
# to the clone-relative copy when the baked path is absent (local/non-container runs)
# -- spec's explicitly-sanctioned fallback (F9), not a silent skip. --clone-dir points
# at the actual clone either way, so adapter.yaml/git-history lookups resolve there.
REPO_ROOT=$(git rev-parse --show-toplevel)
BASE_SHA=$(git merge-base main HEAD 2>/dev/null || echo main)
GATE_SCRIPT="/opt/dark-factory/scripts/gate_blast_radius.py"
[ -f "$GATE_SCRIPT" ] || GATE_SCRIPT="dark-factory/scripts/gate_blast_radius.py"  # TARGET-PATH
echo "$CHANGED" | python3 "$GATE_SCRIPT" \
  --changed-files-stdin \
  --lines-changed "$LINES" \
  --hotspots docs/codeindex-hotspots.md \
  --config .claude/skills/refinement/config.yaml \
  --clone-dir "$REPO_ROOT" \
  --base-ref "$BASE_SHA" \
  > "$ARTIFACTS_DIR/blast.md"

# 3. Read verdict — guard with || true so grep's exit-1-on-no-match doesn't abort under set -e
BLAST_STATUS=$(grep '^STATUS:' "$ARTIFACTS_DIR/blast.md" | cut -d' ' -f2 || true)
BLAST_TRIGGER=$(grep '^TRIGGER:' "$ARTIFACTS_DIR/blast.md" | cut -d' ' -f2- || true)
BLAST_FILES=$(grep '^\s*-' "$ARTIFACTS_DIR/blast.md" | head -10 || true)

# 4. Block on HUMAN_REQUIRED
if [ "$BLAST_STATUS" = "HUMAN_REQUIRED" ]; then
  FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
  gh issue comment "$ISSUE_NUM" --body "$(cat <<EOF
## Blast-Radius Gate — BLOCKED

The blast-radius gate has flagged this change as requiring human review before it can auto-merge.

**Trigger:** $BLAST_TRIGGER

**Triggered files:**
$BLAST_FILES

Remove the \`needs-discussion\` label after reviewing and approving the risk, then re-run validate:
\`\`\`
docker compose --profile factory run --rm dark-factory "Validate issue #$ISSUE_NUM"
\`\`\`
---
$FOOTER
EOF
)"
  python3 dark-factory/scripts/factory_core/providers/cli.py \
    tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
  # Move to Blocked on the project board
  python3 dark-factory/scripts/factory_core/providers/cli.py \
    tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
  exit 1
fi
```
```

   Note the indentation level of steps 1-4 drops by one level relative to the old file
   (no more wrapping `if`), and `FOOTER`'s `marker factory` call and the two
   `tracker label`/`tracker set-status` calls keep their pre-existing `# TARGET-PATH`
   clone-relative form (out of this task's scope — only the gate invocation itself moves
   to the baked-preferred path, per F9).

2. Write the failing test `tests/test_validate_blast_gate_baseref.py`:

```python
"""Static-assertion tests for commands/dark-factory-validate.md Phase 0 prose.

No bash-execution harness exists for command files in this repo (see
tests/test_ceiling_revisit_command.py) -- these assert on the literal fenced-block
text instead.
"""
from pathlib import Path

COMMAND_FILE = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-validate.md"


def _text():
    return COMMAND_FILE.read_text(encoding="utf-8")


def test_no_working_tree_kill_switch_precheck():
    """Requirement 8 (F1): Phase 0 must not read blast_radius.enabled from the working
    tree and skip the gate invocation before it runs -- that would silently reopen the
    bypass this ticket closes."""
    text = _text()
    assert "BLAST_ENABLED" not in text
    assert "d.get('blast_radius', {}).get('enabled'" not in text


def test_gate_invoked_with_base_ref_and_clone_dir():
    text = _text()
    assert "--base-ref \"$BASE_SHA\"" in text
    assert "--clone-dir \"$REPO_ROOT\"" in text
    assert 'BASE_SHA=$(git merge-base main HEAD' in text


def test_gate_prefers_baked_script_with_fallback():
    text = _text()
    assert '/opt/dark-factory/scripts/gate_blast_radius.py' in text
    assert 'dark-factory/scripts/gate_blast_radius.py' in text
    assert '[ -f "$GATE_SCRIPT" ]' in text
```

3. Run: `python -m pytest tests/test_validate_blast_gate_baseref.py -v` → all fail
   (current file still has `BLAST_ENABLED`, no `--base-ref`, no `GATE_SCRIPT` fallback).

4. Apply the Phase 0 replacement from step 1.

5. Run: `python -m pytest tests/test_validate_blast_gate_baseref.py -v` → all pass.

6. Commit:
```bash
git add commands/dark-factory-validate.md tests/test_validate_blast_gate_baseref.py
git commit -m "fix(#200): validate Phase 0 always invokes the blast-radius gate with --base-ref"
```

---

## Task 7: full verification pass

**Files:** none (verification only)

### Steps

1. Run the full test suite:
```bash
python -m pytest tests/ -v
```
   Expect all tests green, including every test touched/added in Tasks 1-6.

2. Run the smoke-gate test and the workflow DAG checks exactly as CI's `tests` and `dag-check` jobs do
   (operator review P3: `bash smoke_gate.sh` is the production gate itself, not the CI check):
```bash
bash tests/test_smoke_gate.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

3. Confirm no out-of-scope files were touched:
```bash
git diff --stat main...HEAD
```
   Expect only: `scripts/factory_core/adapter_defaults.py`, `scripts/factory_core/adapter.py`,
   `scripts/gate_blast_radius.py`, `scripts/diff_rank.py`, `commands/dark-factory-validate.md`,
   `tests/test_adapter.py`, `tests/test_blast_radius.py`,
   `tests/test_validate_blast_gate_baseref.py`, plus this plan doc and the spec doc already on
   the branch.

4. Sanity-check the acceptance criteria from the issue directly, using the same script Task 6
   wires up in production (preferring the baked path, falling back to the clone copy):
```bash
GATE_SCRIPT="/opt/dark-factory/scripts/gate_blast_radius.py"
[ -f "$GATE_SCRIPT" ] || GATE_SCRIPT="scripts/gate_blast_radius.py"
printf ".factory/hooks/x\n" | python3 "$GATE_SCRIPT" --changed-files-stdin \
  --lines-changed 1 --hotspots docs/codeindex-hotspots.md \
  --config .claude/skills/refinement/config.yaml --clone-dir . --base-ref main | head -1
# Expect: STATUS: HUMAN_REQUIRED
```

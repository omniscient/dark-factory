# Implementation Plan: README `safety.*` adapter-table accuracy fix

**Issue:** #415

## Goal

`README.md`'s `### adapter.yaml keys` table (lines 166-171) describes six `safety.*`
adapter keys. Per the approved spec, the `hard_exclude_paths` row overstates enforcement
("matched diff paths abort the run" — false), and an audit of the other four rows (per the
issue's own request to check neighbors) found two more rows with the same
mechanically-true-but-disabled-by-default pattern and two rows naming the wrong or a
nonexistent consumer. This plan rewrites all five `safety.*` rows (the sixth,
`migration_seed_auth_patterns`, is already accurate and is left untouched) so each cell
describes what the code does today, re-verified against this checkout at plan-writing time.

This is a documentation-only change. No code, config, or test file changes — the spec's
Requirement 5 states plainly "No other files change," and confirms no existing test pins
the exact README row wording. Verification below uses `grep` against `README.md` directly
(read current text before editing, confirm corrected text after) instead of a pytest drift
guard, since adding one would itself violate the spec's single-file scope.

## Architecture

Five row edits in `README.md`'s `### adapter.yaml keys` table, in place, preserving table
structure (same number of rows, same column count, same `|`-delimited GFM table syntax).
One row (`migration_seed_auth_patterns`) is verified unchanged. No other section of
`README.md` changes.

## Tech Stack

Markdown (`README.md`) only. `grep`/`sed` for verification — no pytest, no new test file
(per spec Requirement 5).

## File Structure

| Path | Change |
|---|---|
| `README.md` | Rewrite 5 of 6 `safety.*` table rows (lines 166-169, 171); line 170 (`migration_seed_auth_patterns`) verified unchanged |

---

## Task 1: Fix `safety.sensitive_keywords` and `safety.hard_exclude_paths` rows

**Files:** `README.md`

Both rows share the same drift pattern: the mechanism is real but gated behind
`epic_autopilot.enabled: false` (`config/config.yaml:77`). Both must name that flag.

### Steps

1. Verify current (wrong) text — red phase:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.sensitive_keywords\|safety.hard_exclude_paths" README.md
   ```
   Expected output (current, unfixed):
   ```
   166:| `safety.sensitive_keywords` | `string` | Pipe-separated regex of sensitive topic keywords; matched tickets skip autopilot and go to human review. |
   167:| `safety.hard_exclude_paths` | `list[str]` | Path prefixes the factory will never touch; matched diff paths abort the run. |
   ```

2. Re-verify the consumers against the checkout before editing:
   ```bash
   cd /workspace/dark-factory
   grep -n "_sensitive_keywords\|def hard_excluded" scripts/factory_core/epic_autopilot.py
   grep -n "^  enabled: false" config/config.yaml | head -1
   sed -n '76,77p' config/config.yaml
   ```
   Expected: `epic_autopilot.py:60` defines `hard_excluded`, `epic_autopilot.py:338` defines
   `_sensitive_keywords`, and `config/config.yaml:77` reads
   `enabled: false              # kill-switch — ship OFF. env: EPIC_AUTOPILOT_ENABLED overrides`.

3. Implement — replace lines 166-167 of `README.md`:

   Old:
   ```
   | `safety.sensitive_keywords` | `string` | Pipe-separated regex of sensitive topic keywords; matched tickets skip autopilot and go to human review. |
   | `safety.hard_exclude_paths` | `list[str]` | Path prefixes the factory will never touch; matched diff paths abort the run. |
   ```

   New:
   ```
   | `safety.sensitive_keywords` | `string` | Pipe-separated regex; read by `epic_autopilot.py`'s `_sensitive_keywords()` to skip matching candidate tickets in `hard_excluded()`. Gated by `epic_autopilot.enabled` (ships `false`) — inert while that flag is off. |
   | `safety.hard_exclude_paths` | `list[str]` | Path prefixes that make an epic-autopilot candidate ticket ineligible (`epic_autopilot.py::hard_excluded`) — not a diff check and not a run-abort mechanism. Gated by `epic_autopilot.enabled` (ships `false`). See [`docs/factory-target-boundary.md`](docs/factory-target-boundary.md) (OD3) for what actually holds a path boundary like `deploy/instances/**`. |
   ```

4. Verify fixed — green phase:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.sensitive_keywords\|safety.hard_exclude_paths" README.md
   ```
   Expected:
   ```
   166:| `safety.sensitive_keywords` | `string` | Pipe-separated regex; read by `epic_autopilot.py`'s `_sensitive_keywords()` to skip matching candidate tickets in `hard_excluded()`. Gated by `epic_autopilot.enabled` (ships `false`) — inert while that flag is off. |
   167:| `safety.hard_exclude_paths` | `list[str]` | Path prefixes that make an epic-autopilot candidate ticket ineligible (`epic_autopilot.py::hard_excluded`) — not a diff check and not a run-abort mechanism. Gated by `epic_autopilot.enabled` (ships `false`). See [`docs/factory-target-boundary.md`](docs/factory-target-boundary.md) (OD3) for what actually holds a path boundary like `deploy/instances/**`. |
   ```
   Also confirm the row count is unchanged and the table still renders as one row per line:
   ```bash
   grep -c "^| \`safety\." README.md
   ```
   Expected: `6` (six `safety.*` rows total, unchanged from before this task — only the two
   rows' cell text changed, no row was added or removed).

5. Commit:
   ```bash
   cd /workspace/dark-factory
   git add README.md
   git commit -m "docs(#415): fix sensitive_keywords/hard_exclude_paths README rows"
   ```

---

## Task 2: Fix `safety.dispatch_ceiling_keywords` and `safety.critical_diff_paths` rows

**Files:** `README.md`

Both rows currently name the wrong mechanism outright (not just "gated off"):
`dispatch_ceiling_keywords` claims a live consumer that doesn't exist for the adapter key
itself, and gets the parking rule backwards; `critical_diff_paths` attributes itself to the
blast-radius gate when its real (and only) consumer is `diff_rank.py`.

### Steps

1. Verify current (wrong) text — red phase:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.dispatch_ceiling_keywords\|safety.critical_diff_paths" README.md
   ```
   Expected output (current, unfixed):
   ```
   168:| `safety.dispatch_ceiling_keywords` | `string` | Pipe-separated regex; matching ticket titles trigger the dispatch ceiling (L tickets parked). |
   169:| `safety.critical_diff_paths` | `list[str]` | Regex patterns; diffs touching these paths are flagged as Critical in the blast-radius gate. |
   ```

2. Re-verify the consumers against the checkout before editing:
   ```bash
   cd /workspace/dark-factory
   sed -n '44,53p' scripts/scheduler_lib.sh
   grep -n "dispatch_ceiling" config/config.yaml scheduler.sh
   grep -n "dispatch_ceiling_keywords" scripts/architecture_slice.py scripts/factory_core/adapter_defaults.py
   grep -n "critical_diff_paths\|def classify_file" scripts/diff_rank.py
   grep -n "_migration_seed_auth_patterns\|def classify_file" scripts/gate_blast_radius.py
   ```
   Expected: `scheduler_lib.sh::is_above_ceiling` parks size `XL` unconditionally and size
   `M` only on keyword match (`*) return 1` — `L` is never parked); the keyword source is
   `config/config.yaml`'s `dispatch_ceiling.keywords` (env `ABOVE_CEILING_KEYWORDS`,
   `scheduler.sh:79`), never `.factory/adapter.yaml`; `architecture_slice.py:83` reads only
   the baked `adapter_defaults.DEFAULTS["safety"]["dispatch_ceiling_keywords"]` default, not
   a target's adapter file; `diff_rank.py:61,73,78` is the sole adapter-reading consumer of
   `critical_diff_paths`; `gate_blast_radius.py`'s own `classify_file` iterates only
   `_migration_seed_auth_patterns` and never reads `critical_diff_paths`.

3. Implement — replace lines 168-169 of `README.md`:

   Old:
   ```
   | `safety.dispatch_ceiling_keywords` | `string` | Pipe-separated regex; matching ticket titles trigger the dispatch ceiling (L tickets parked). |
   | `safety.critical_diff_paths` | `list[str]` | Regex patterns; diffs touching these paths are flagged as Critical in the blast-radius gate. |
   ```

   New:
   ```
   | `safety.dispatch_ceiling_keywords` | `string` | **This adapter key is not read** — setting it in a target's `.factory/adapter.yaml` has no effect (the baked default is consumed only by `architecture_slice.py`'s own default-loading, not by a target's adapter file). The real dispatch-ceiling knob is `config/config.yaml`'s `dispatch_ceiling.keywords` (env `ABOVE_CEILING_KEYWORDS`), read by `scheduler_lib.sh::is_above_ceiling`: size `XL` always parks, size `M` parks only on a title keyword match, size `L` is never parked. |
   | `safety.critical_diff_paths` | `list[str]` | Regex patterns read by `diff_rank.py` to rank/prioritize a diff for the code-review and conformance reviewers — not the blast-radius gate, which has its own path list (`migration_seed_auth_patterns`) and never reads this key. Carries a non-overridable factory-owned floor (`FACTORY_OWNED_CRITICAL_DIFF_FLOOR`) unioned in on every adapter load; a target cannot shrink below it. |
   ```

4. Verify fixed — green phase:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.dispatch_ceiling_keywords\|safety.critical_diff_paths" README.md
   grep -c "^| \`safety\." README.md
   ```
   Expected: the two rows show the corrected text above; the row count is still `6`.

5. Commit:
   ```bash
   cd /workspace/dark-factory
   git add README.md
   git commit -m "docs(#415): fix dispatch_ceiling_keywords/critical_diff_paths README rows"
   ```

---

## Task 3: Fix `safety.main_red_allowed_paths` row; confirm `migration_seed_auth_patterns` unchanged

**Files:** `README.md`

`main_red_allowed_paths` is mechanically accurate but gated by two conditions, not one:
`main_red_autofix.enabled` (ships `false`) AND `MAIN_RED_AUTOFIX_ENABLED=true` in
`.archon/.env` — flipping the config value alone is insufficient.
`migration_seed_auth_patterns` is already accurate per the spec and needs no edit; this
task confirms that instead of skipping it silently.

### Steps

1. Verify current text — red phase (for the row being changed) and confirm the row being
   left alone:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.main_red_allowed_paths\|safety.migration_seed_auth_patterns" README.md
   ```
   Expected output (current):
   ```
   170:| `safety.migration_seed_auth_patterns` | `list[str]` | Regex patterns; diffs matching these require explicit human sign-off. |
   171:| `safety.main_red_allowed_paths` | `list[str]` | Path prefixes the main-red auto-fixer is allowed to modify. |
   ```

2. Re-verify the two-condition gate against the checkout before editing:
   ```bash
   cd /workspace/dark-factory
   sed -n '113,117p' config/config.yaml
   grep -n "migration_seed_auth_patterns\|HUMAN_REQUIRED" scripts/gate_blast_radius.py | sed -n '1,5p'
   grep -n "blast_radius:" -A3 config/config.yaml
   ```
   Expected: `config/config.yaml:113-117` shows the `main_red_autofix:` block's comment
   ("To ENABLE, set MAIN_RED_AUTOFIX_ENABLED=true in .archon/.env ... flipping `enabled`
   here alone does NOT turn the feature on") and `enabled: false`; `gate_blast_radius.py`
   confirms `migration_seed_auth_patterns` routes to `HUMAN_REQUIRED` and
   `blast_radius.enabled: true` by default — the row being left alone is confirmed accurate,
   no edit needed.

3. Implement — replace line 171 of `README.md` only (line 170 is untouched):

   Old:
   ```
   | `safety.main_red_allowed_paths` | `list[str]` | Path prefixes the main-red auto-fixer is allowed to modify. |
   ```

   New:
   ```
   | `safety.main_red_allowed_paths` | `list[str]` | Path prefixes the main-red auto-fixer is allowed to modify. Gated by two conditions, not one: `main_red_autofix.enabled` (ships `false`) AND `MAIN_RED_AUTOFIX_ENABLED=true` in `.archon/.env` — flipping the config value alone does not enable the fixer. |
   ```

4. Verify fixed — green phase:
   ```bash
   cd /workspace/dark-factory
   grep -n "safety.main_red_allowed_paths\|safety.migration_seed_auth_patterns" README.md
   diff <(git show HEAD~2:README.md | sed -n '170p') <(sed -n '170p' README.md)
   ```
   Expected: `main_red_allowed_paths` row shows the corrected text above; the `diff` for
   line 170 (`migration_seed_auth_patterns`) produces no output (byte-identical, confirming
   it was not touched by this task or Tasks 1-2). Note: `HEAD~2` refers to the two commits
   made in Tasks 1 and 2 on this branch — adjust the ref if the branch history differs at
   implementation time (e.g. use `git log --oneline -- README.md` to find the pre-Task-1
   commit instead of counting).

5. Commit:
   ```bash
   cd /workspace/dark-factory
   git add README.md
   git commit -m "docs(#415): fix main_red_allowed_paths README row (two-condition gate)"
   ```

---

## Task 4: Final self-review

**Files:** none (verification only; a fixup commit only if drift is found)

### Steps

1. Re-read the full table and confirm exactly 5 rows changed, 1 unchanged, no row added or
   removed:
   ```bash
   cd /workspace/dark-factory
   sed -n '160,180p' README.md
   ```
   Expected: `### adapter.yaml keys` header, the full table with all `safety.*` rows showing
   corrected text (Tasks 1-3) except `migration_seed_auth_patterns` (unchanged), followed by
   `All keys are optional and deep-merged over the built-in defaults.` and the existing
   `docs/factory-target-boundary.md` pointer paragraph, both untouched.

2. Confirm no placeholder language slipped in:
   ```bash
   cd /workspace/dark-factory
   grep -niE "\bTBD\b|\bTODO\b|implement later|add appropriate error handling" README.md
   ```
   Expected: no matches in the changed rows (exit code 1, or matches only in unrelated
   pre-existing content elsewhere in the file — confirm any hit is not in the `safety.*`
   rows before proceeding).

3. Confirm the `**Issue:** #415` line is present in this plan file itself (self-review
   requirement per the plan-writing convention):
   ```bash
   grep -n "^\*\*Issue:\*\* #415" docs/superpowers/plans/2026-09-10-readme-safety-adapter-table-accuracy-plan.md
   ```
   Expected: one match.

4. Confirm no out-of-scope files changed:
   ```bash
   cd /workspace/dark-factory
   git diff origin/main HEAD --stat
   ```
   Expected: `README.md` and `docs/superpowers/plans/2026-09-10-readme-safety-adapter-table-accuracy-plan.md`
   only (plus the spec file already committed during refinement,
   `docs/superpowers/specs/2026-09-10-readme-safety-adapter-table-accuracy-design.md`, which
   is out of this command's own scope boundary but was legitimately committed during the
   refine phase). No test file, no config file, no code file.

5. Run the full test suite this repo's CI runs, to confirm no regression (should be a clean
   no-op since no code/test files changed):
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/ -v
   ```
   Expected: full pass, identical to `origin/main`'s baseline — this change touches no code
   or test file, so no test outcome should differ.

6. No further commit needed if steps 1-5 all pass clean. If step 1 or 2 found drift, fix it
   and commit the fixup on its own: `git commit -m "docs(#415): fix drifted README row
   wording"`.

7. Record two known follow-ups in the PR description (do not act on either — both are out of
   this ticket's docs-only scope per the spec, and this is a headless run with no one to ask,
   so the disposition is: note them for a human/operator, don't leave them silently dropped):
   - **`docs/factory-target-boundary.md:301-303`** now describes a README phrasing
     ("`hard_exclude_paths` row phrasing (\"matched diff paths abort the run\")") that no
     longer exists in `README.md` after Task 1. Confirm this with:
     ```bash
     cd /workspace/dark-factory
     grep -n "matched diff paths abort the run" README.md docs/factory-target-boundary.md
     ```
     Expected: no match in `README.md` (fixed by Task 1); one match in
     `docs/factory-target-boundary.md:302` (the doc quoting the now-stale phrasing as a
     historical description of what README used to say). No test pins this sentence
     (confirmed: `grep -rn "abort the run" tests/` finds only an unrelated match in
     `tests/test_has_new_comment_after_report.sh`), so this is not a CI break, but it is a
     freshly-created cross-reference drift this ticket's own fix causes. Editing
     `docs/factory-target-boundary.md` is out of scope here per spec Requirement 5 ("No
     other files change") — note it in the PR description as a suggested follow-up edit
     (update that sentence to describe the corrected README text instead) rather than
     editing it in this PR.
   - **`safety.dispatch_ceiling_keywords` dead adapter key** — per the spec's Open
     questions section, recommend filing a separate follow-up ticket to decide whether to
     wire this key up to `scheduler_lib.sh::is_above_ceiling` or remove it from
     `adapter_defaults.py`'s schema. Note this recommendation in the PR description; do not
     implement either option here (code change, needs its own reviewed spec per CLAUDE.md).

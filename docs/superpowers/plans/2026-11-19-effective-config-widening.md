# Widen `effective_config` to Every Knob — Implementation Plan

**Issue:** #180

**Spec:** `docs/superpowers/specs/2026-11-19-effective-config-widening-design.md`

**Goal:** Make `factory_core.effective_config` the one writer of a run's resolved config.
It gains two materialized outputs, `run-config.json` (the whole tree) and `run-config.env`
(a whitelisted flat projection for bash). It serves two call sites: `scheduler.sh`
(scheduler mode, before any clone exists) and `entrypoint.sh` (run mode, after the clone).
Five readers move onto it: `scheduler.sh::read_config` and its 33 per-knob `yq` calls,
`entrypoint.sh::_entrypoint_cfg_apply` (5 knobs), `scripts/budget_gate.sh`'s inline YAML
parse, `scripts/architecture_slice.py::_load_config`, and the `yq` fallback in
`epic_autopilot.py::_load_exclude_paths`. No effective value, precedence or source changes
for any key on either live instance.

**Architecture:** `effective_config.py` keeps `resolve()`/`materialize()` as they are and
adds: an `ENV_WHITELIST` table (`ENV_VAR`, dotted path, readers), a single shared writer
`write_run_artifacts()`, a scheduler-mode first-match resolver `resolve_scheduler()`, a
run-mode resolver `run_config()`, `load_run_config()` for run-side Python readers, and
`first_match_list()` for the epic-autopilot fallback. A new `scripts/run_config_env.sh`
holds the one bash reader (`apply_run_config_env <file> scheduler|entrypoint`). It
replays `_set_cfg`'s env-wins semantics in one mode and `_epcfg`'s keep-bootstrap
semantics in the other. It never `source`s the file. The clone-side
`<clone>/.claude/skills/refinement/config.yaml` is still written, with the same bytes, for
the readers the spec scopes out.

**Tech Stack:** Python 3 (PyYAML, stdlib `json`), bash, `jq` (already in the image and on
GitHub's `ubuntu-latest`), pytest, the repo's bash test harnesses.

---

## Read this first

### What the code looks like on `main`

Every figure in this plan was re-derived against `main`@`f457c2c` (2026-11-17), and the
whole change was prototyped against it end to end. That prototype was a scratch worktree,
never committed. In it, `python -m pytest tests/` gave 2572 passed, and every shell test
named below passed or matched its baseline. The refine branch this plan is committed on
is 463 commits behind `main`, so **implement on a fresh `feat/issue-180-*` branch cut from
current `origin/main`, not from the refine branch.**

Line anchors on `main`@`f457c2c`:

| Reader | Location | Shape |
|---|---|---|
| `scheduler.sh::read_config` | `scheduler.sh:24-101` (`CONFIG_YAML_PATHS` 27-31, `resolve_config_yaml` 33-40, `_set_cfg` 49-64, 33 calls 66-98), called at `:1452` | first existing file, whole-file; `yq` per knob; env wins |
| `entrypoint.sh::_entrypoint_cfg_apply` | `entrypoint.sh:42-72`, called at `:838` after `effective_config --materialize` at `:834-835` | clone-side file → legacy; `_epcfg` keeps bootstrap values |
| `scripts/budget_gate.sh` | inline `python3 -c "import yaml…"` at `:40-48` | reads `<clone>/.claude/skills/refinement/config.yaml` |
| `scripts/architecture_slice.py::_load_config` | `:95-99` (`_CONFIG_PATHS`), `:118-134` | clone-side → `/workspace/project/.claude/…` → baked → legacy |
| `epic_autopilot.py::_load_exclude_paths` | `:409-431`, `_CONFIG_PATHS` `:320-323` | adapter → `yq` per file → `_DEFAULT_EXCLUDE` |

### One deviation from the spec text, and why: `FACTORY_RUN_CONFIG_JSON`

Spec Requirement 2 places the run-mode artifacts in `$ARTIFACTS_DIR` and assumes the
workflow nodes see that same directory. They do not:

- `entrypoint.sh:143-147` creates `ARTIFACTS_DIR=~/.archon/workspaces/<slug>/artifacts/runs/<uuid4>`.
- Archon gives every **bash node** its own `ARTIFACTS_DIR`, the workflow run's directory.
  `/opt/archon/packages/workflows/src/dag-executor.ts:1321-1326` builds the node env as
  `{ ...process.env, ARTIFACTS_DIR: artifactsDir, … }`. This plan run shows it: the
  entrypoint's directory (`…/runs/18a74b93…`) is empty, while `context-budget.plan.json`
  and `token-opt-caps.env` (the latter written by `budget_gate.sh` via `$ARTIFACTS_DIR`)
  are in the workflow's directory (`…/runs/f6cd3017…`).
- Every other exported variable does reach the nodes (`...process.env`). For example,
  `FACTORY_SIDE_EFFECT_LEVEL` and the `SESSION_WINDOW_*` values are visible in this agent
  node's environment.

So `entrypoint.sh` writes `run-config.{json,env}` to **its own** `$ARTIFACTS_DIR`, which
matches the spec, and also exports `FACTORY_RUN_CONFIG_JSON="$ARTIFACTS_DIR/run-config.json"`.
`budget_gate.sh` reads `${FACTORY_RUN_CONFIG_JSON:-$ARTIFACTS_DIR/run-config.json}`.
`architecture_slice.py` reads `$FACTORY_RUN_CONFIG_JSON` and otherwise uses the run-mode
resolver. Without this export, `budget_gate.sh` would find no file in a real run and fall
back to observe. That would silently switch budget enforcement off for refine, plan,
conformance and code-review, which would break safety invariant 1. The spec's Open
Question 2 explicitly leaves artifact naming and paths to the plan, as long as the
two-call-site, one-writer shape holds, and it does. `FACTORY_RUN_CONFIG_JSON` is a path
hand-off, not a config knob. Because of its `FACTORY_` prefix, `tests/conftest.py` already
strips it from the pytest environment, which keeps the suite hermetic inside run
containers.

### Human checkpoints (spec Requirement 7, stated correctly)

This ticket is `size: L`. `is_above_ceiling()` (`scripts/scheduler_lib.sh:44`) parks only
**XL** tickets (always) and **M** tickets whose title matches a keyword. **L tickets are
never parked**, so the `dispatch_ceiling` gate does **not** fire for this ticket. The real
human checkpoints are:

- the `plan-pending-review` gate on this plan;
- the conformance and code-review gates on the PR;
- the human PR merge.

There is no dedicated rollback kill-switch. The rollback is reverting the PR. The
backstops are:

- the golden and equivalence tests (Tasks 1-3);
- fail-closed scheduler startup (Task 5);
- the fail-open run side (Tasks 3 and 6).

### MarketHawk impact (spec Requirement 8)

Code-level guarantee only. The spec decided that no manual MarketHawk-side check gates
this ticket.

- **Scheduler side.** MarketHawk has no `/workspace/project/config/config.yaml`, so
  scheduler mode resolves the baked file, as `resolve_config_yaml` did.
- **Run side.** MarketHawk commits no clone-side copy, so it takes `materialize()`'s
  unchanged merge-and-write branch. The clone file gets the same bytes as before.
  `run-config.json` is proven equal to it with MarketHawk's adapter shape
  (`token_optimization.enforce.implement: true`) by
  `test_run_config_json_equals_clone_side_materialization[markethawk]`.
- **Committing targets.** A target that commits its own copy keeps the "left in place"
  branch byte-for-byte, and `run-config.json` is that copy alone (Task 3 tests).
- **Untouched.** Nothing under `deploy/instances/**`, `.github/workflows/publish.yml` or
  `.claude/skills/**` changes. Nothing is deleted.

### Explicitly unchanged (spec Requirements 3, 5, 6)

- `resolve()`'s merge logic and `TARGET_TUNABLE_BLOCKS = ("token_optimization",)`.
- No file is deleted. The clone-side materialized `config.yaml` is still written, with the
  same bytes.
- The readers scoped out by the spec keep reading the clone-side file:
  - `budget_enforce.py`, `diff_rank.py`, `memory_retrieve.py`, `load_memory_context.sh`;
  - `commands/dark-factory-{refine,plan,conformance,code-review,validate}.md`;
  - the `preview.enabled` read in the workflow YAML;
  - after PR #542, `cli.py docs-conformance-config`.
- `workflows/archon-dark-factory.yaml` is not edited. The five `enforce-budget-*` nodes
  already call `budget_gate.sh`, since #183.
- The trust-boundary readers are untouched: `.factory/adapter.yaml` `safety.*`,
  `entrypoint.sh::_compute_side_effect_level` and `side_effect.py`, and
  `gate_blast_radius.py::load_config`. The only edits near `_compute_side_effect_level`
  are two **comment** lines, in the header block above the function, that name the
  retired `_entrypoint_cfg_apply`. The function body is byte-identical, which Task 6
  checks, and the source-scan test (Task 2) pins that.
- `_hard_exclude_paths()` (no production caller). The #205 precedence fix is not part of
  this ticket.
- `Dockerfile` (its comment `yq — … used by entrypoint.sh and scheduler.sh` goes stale,
  but `yq` stays in the image because `commands/dark-factory-code-review.md:63` still uses
  it. Leave both alone; note it in the PR body as a follow-up).

### Known rendering non-equivalences (unreachable with the shipped config; pinned by the golden test)

`run-config.env` values are rendered by Python, not `yq`. The rendering is the same for
every value in `config/config.yaml` today (Task 1's fixture). It differs only for inputs
the shipped config does not contain:

- YAML 1.1 booleans such as `yes`/`on` become `true` (PyYAML), where `yq` kept them as
  strings.
- A float literal such as `0.70` renders as `0.7`.
- A non-scalar or multi-line value under a whitelisted key is written as null, with a
  stderr warning, because it cannot be carried on one line. `yq` used to print a YAML
  block.

Write these three into the PR body.

### Memory lessons baked into the steps

- Copy the approved spec **and this plan** onto the `feat/` branch and commit them in
  Task 0 (`[PATTERN]` #42). They do not transfer from the refine branch.
- Use `git diff origin/main HEAD -- <file>` (two-dot) for any out-of-scope check
  (`[PATTERN]` #250).
- Do **not** plan or perform any widening of `resolve()`'s merge (`[AVOID]` #180 entry in
  `architecture.md`). Only the output and consumption surface changes.
- `budget_gate.sh` is the single workflow-side reader to migrate, not 5 inline blocks
  (`[AVOID]` #183 entry).

### Sequencing with PR #542

PR #542 (docs-conformance gate) was **not** merged on `main`@`f457c2c`. In Task 0, check
whether it has merged. If it has:

- there are six `budget_gate.sh` callers, not five, and none of them needs editing;
- `config/config.yaml` gains `docs_conformance.*` keys, but no row joins `ENV_WHITELIST`;
- re-read `budget_gate.sh`'s read block before the Task 7 edit, in case its text changed;
- generate the Task 1 golden fixture from the post-#542 `config/config.yaml`.

The doc re-map script in Task 11 works against whatever base you branched from.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/effective_config.py` | modify | `ENV_WHITELIST`; `render_env`/`write_run_artifacts` (one shared writer); scheduler mode (`SCHEDULER_CANDIDATES`, `resolve_scheduler`, fail-closed CLI); run mode (`materialize(..., out_dir)`, `run_config`, `load_run_config`); `first_match_list`; `--clone-dir` default `None`, new `--out-dir`, `--candidate` |
| `scripts/run_config_env.sh` | create | `apply_run_config_env <file> scheduler\|entrypoint` — the one bash reader of `run-config.env` |
| `scheduler.sh` | modify | `read_config` → module (scheduler mode) + `apply_run_config_env`; retire `CONFIG_YAML_PATHS`, `resolve_config_yaml`, `_set_cfg`; fail-closed |
| `entrypoint.sh` | modify | retire `_entrypoint_cfg_apply`; `--materialize --out-dir "$ARTIFACTS_DIR"`; export `FACTORY_RUN_CONFIG_JSON`; `apply_run_config_env … entrypoint` |
| `scripts/budget_gate.sh` | modify | mode/budget from `run-config.json` via one `jq` call; observe when missing/unparseable |
| `scripts/architecture_slice.py` | modify | `_load_config` → `effective_config.load_run_config`; drop its `_CONFIG_PATHS` |
| `scripts/factory_core/epic_autopilot.py` | modify | `_load_exclude_paths` fallback: `yq` subprocess → `effective_config.first_match_list(_CONFIG_PATHS, …)` |
| `tests/fixtures/run_config_env_golden.json` | create | expected 33 scheduler + 5 entrypoint values, generated by the old `yq` readers |
| `tests/test_effective_config.py` | modify | 17 new test functions (18 cases) + extended `test_clone_config_wins_over_baked_and_is_never_dirtied` |
| `tests/test_run_config_env.sh` | create | helper semantics, both modes |
| `tests/test_config_deletion.sh` | rewrite | safety invariant 3 (fail-closed) against the new `read_config` |
| `tests/test_budget_gate.sh` | modify | fixture writes `run-config.json`; cases 6-9 |
| `tests/test_architecture_slice.py` | modify | 3 `_load_config` tests |
| `tests/test_epic_autopilot.py` | modify | 4 `_load_exclude_paths` characterization tests |
| `tests/test_epic_autopilot_config.sh` | rewrite | `yq`-free; wiring assertions point at `ENV_WHITELIST` |
| `tests/test_token_optimization_flags.py` | modify | T2 wiring tests point at `ENV_WHITELIST` (they grep `scheduler.sh` today) |
| `tests/test_scheduler.sh`, `tests/test_dispatch_ceiling.sh` | modify | append #180 assertions (helper sourced / whitelist rows) |
| `.github/workflows/ci.yml` | modify | list `test_run_config_env.sh`, `test_config_deletion.sh`, `test_epic_autopilot_config.sh` |
| `docs/configuration.md`, `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md`, `docs/dark-factory-token-optimization.md`, `docs/triage-labels.md` | modify | replace retired names, re-map line citations |
| `bench/variants.example.yaml` | modify | comment-only: re-cite the moved `budget_gate.sh` kill-switch lines |

**Not in CI, by decision (spec Requirement 9):** `tests/test_scheduler.sh` and
`tests/test_dispatch_ceiling.sh` stay out of `ci.yml`. They need the Linux run-container
stub environment, and both already fail on `main`@`f457c2c` in that container: 263
passed / 2 failed (G2, I2) and 22 passed / 11 failed respectively. These are
pre-existing failures unrelated to config. Neither can run on the Windows host. The
acceptance rule is the same failure set as baseline, plus the new assertions passing.

---

## Task 0: Branch, baseline, carry the spec and plan

**Files:** none modified, except copying `docs/superpowers/specs/2026-11-19-effective-config-widening-design.md` and this plan onto the new branch.

- [ ] **Step 1: Cut the branch from current main**

```bash
git fetch origin
git switch -c feat/issue-180-widen-effective-config origin/main
git log --oneline -1
```
Expected: the current `origin/main` head.

- [ ] **Step 2: Bring the spec and plan over from the refine branch and commit them**

```bash
git checkout origin/refine/issue-180-refactor-config---widen-effective-config -- \
  docs/superpowers/specs/2026-11-19-effective-config-widening-design.md \
  docs/superpowers/plans/2026-11-19-effective-config-widening.md
git commit -m "docs(#180): carry approved spec and plan onto the feat branch"
```

- [ ] **Step 3: Check PR #542 and re-confirm the anchors**

```bash
git log origin/main --oneline | grep -E '\(#542\)' || echo "PR #542 not merged"
grep -c '_set_cfg ' scheduler.sh                          # expect 33
grep -n '^_entrypoint_cfg_apply\|_entrypoint_cfg_apply()' entrypoint.sh
grep -n 'budget_gate.sh' workflows/archon-dark-factory.yaml | wc -l   # 5 (6 after #542)
grep -n 'yq' scripts/factory_core/epic_autopilot.py
```
If a count differs, re-read the function before editing it. The edits below anchor on
exact text, not line numbers.

- [ ] **Step 4: Record baselines** (pytest may need `python3 -m pip install --user pytest`)

```bash
python -m pytest tests/ -q 2>&1 | tail -1
for t in test_budget_gate test_config_deletion test_epic_autopilot_config; do bash tests/$t.sh >/dev/null 2>&1; echo "$t rc=$?"; done
bash tests/test_scheduler.sh 2>&1 | tail -1          # main@f457c2c: 263 passed, 2 failed
bash tests/test_dispatch_ceiling.sh 2>&1 | tail -1   # main@f457c2c: 22 passed, 11 failed
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: pytest all passed. All three shell tests have `rc=0`. The two scheduler tests
show the numbers in the comments. The docs check passes. Save these numbers for Task 12.

---

## Task 1: Golden fixture from the old readers (before any reader is touched)

**Files:** Create `tests/fixtures/run_config_env_golden.json`

The spec (Requirement 9) requires this fixture to be produced by the **old** readers.
Run it before Task 5 deletes `_set_cfg` and Task 6 deletes `_epcfg`.

- [ ] **Step 1: Generate the fixture with `yq`, exactly as `_set_cfg`/`_epcfg` read each knob**

```bash
mkdir -p tests/fixtures
python3 - <<'PY'
import json, re, subprocess
def grab(path, fn):
    return re.findall(rf"^\s*{fn}\s+(\w+)\s+'([^']+)'", open(path, encoding="utf-8").read(), re.M)
out = {}
for reader, path, fn in (("scheduler", "scheduler.sh", "_set_cfg"),
                         ("entrypoint", "entrypoint.sh", "_epcfg")):
    vals = {}
    for var, expr in grab(path, fn):
        v = subprocess.run(["yq", expr, "config/config.yaml"], capture_output=True,
                           text=True, check=True).stdout.rstrip("\n")
        vals[var] = None if v == "null" else v
    out[reader] = vals
with open("tests/fixtures/run_config_env_golden.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)
    f.write("\n")
print({k: len(v) for k, v in out.items()})
PY
```
Expected: `{'scheduler': 33, 'entrypoint': 5}`.

- [ ] **Step 2: Verify the content.** On `main`@`f457c2c` the generated file is exactly
the following. If PR #542 landed, only `config/config.yaml`-driven values could differ,
and none of these keys is touched by #542:

```json
{
  "scheduler": {
    "POLL_INTERVAL": "60",
    "MAX_RETRIES": "3",
    "RATE_LIMIT_FLOOR": "200",
    "FACTORY_WIP_LIMIT": "1",
    "MAIN_RED_RECHECK_ENABLED": "true",
    "MAIN_RED_RECHECK_MINUTES": "20",
    "BLOCKED_RESCUE_ENABLED": "true",
    "REFINE_WIP_LIMIT": "2",
    "DIRECT_TO_PR_LABEL": "direct-to-pr",
    "SPEC_GRACE_MINUTES": "30",
    "PLAN_GRACE_MINUTES": "30",
    "CONFLICT_RESOLUTION_ENABLED": "true",
    "DISPATCH_CEILING_ENABLED": "true",
    "ABOVE_CEILING_LABEL": "above-ceiling",
    "ABOVE_CEILING_KEYWORDS": "migration|migrate|performance|perf|architectur|refactor",
    "EPIC_AUTOPILOT_ENABLED": "false",
    "EPIC_AUTOPILOT_MODEL": "claude-opus-5-5",
    "EPIC_AUTOPILOT_DAILY_CAP": "5",
    "EPIC_AUTOPILOT_CONFIDENCE_FLOOR": "0.7",
    "EPIC_AUTOPILOT_OPT_OUT_LABEL": "no-autopilot",
    "EPIC_AUTOPILOT_HOLD_TTL_HOURS": "24",
    "EPIC_AUTOPILOT_SIZE_CEILING": "XL",
    "EPIC_AUTOPILOT_START_EPICS": "true",
    "EPIC_AUTOPILOT_SENSITIVE_KEYWORDS": "trading|ibkr|live order|notional|authentication|authorization|authn|authz|jwt|oauth|rbac|/auth",
    "EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT": "true",
    "MAIN_RED_AUTOFIX_ENABLED": "false",
    "MAIN_RED_AUTOFIX_MODEL": "claude-opus-5-5",
    "MAIN_RED_AUTOFIX_MAX_ATTEMPTS": "3",
    "MAIN_RED_AUTOFIX_THROTTLE_MIN": "15",
    "TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED": "true",
    "TOKEN_OPTIMIZATION_MEMORY_ENABLED": "true",
    "TOKEN_OPTIMIZATION_COMMENTS_ENABLED": "true",
    "TOKEN_OPTIMIZATION_DIFF_ENABLED": "true"
  },
  "entrypoint": {
    "FACTORY_WIP_LIMIT": "1",
    "CONFLICT_RESOLUTION_AI_TIER": "true",
    "SESSION_WINDOW_BACKOFF_ENABLED": "true",
    "SESSION_WINDOW_BUFFER_MINUTES": "5",
    "SESSION_WINDOW_FALLBACK_MINUTES": "30"
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/run_config_env_golden.json
git commit -m "test(#180): golden run-config.env fixture generated by the old yq readers"
```

---

## Task 2: `effective_config` — tests first (whitelist, both modes, artifacts, guards)

**Files:** Modify `tests/test_effective_config.py`

- [ ] **Step 1: Extend the existing clone-wins test (spec Requirement 8, first bullet).**
Replace the whole `test_clone_config_wins_over_baked_and_is_never_dirtied` function with:

```python
def test_clone_config_wins_over_baked_and_is_never_dirtied(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    exclude = clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("# pre-existing\n", encoding="utf-8")
    cfg_path = _write_clone_cfg(
        clone, {"token_optimization": {"budgets": {"refine": 25000}},
                "dispatch_ceiling": {"keywords": "clone-kw"}})  # a non-token_optimization knob
    original_bytes = cfg_path.read_bytes()

    merged, sources = ec.resolve(str(clone), baked)
    assert merged["token_optimization"]["budgets"]["refine"] == 25000
    # deep-merge: baked siblings survive
    assert merged["token_optimization"]["budgets"]["plan"] == 30000
    assert merged["token_optimization"]["enabled"] is True
    assert merged["dispatch_ceiling"]["keywords"] == "clone-kw"
    assert sources["token_optimization"] == "clone"

    summary = ec.materialize(str(clone), baked, str(tmp_path / "artifacts"))
    assert "left in place" in summary
    assert "token_optimization source: clone" in summary
    # byte-parity: the committed clone file is untouched, and nothing new is git-excluded
    assert cfg_path.read_bytes() == original_bytes
    assert exclude.read_text(encoding="utf-8") == "# pre-existing\n"
```

Leave `test_materialize_writes_merged_config_and_git_exclude_once` **unmodified**
(spec Requirement 8, last bullet).

- [ ] **Step 2: Append the #180 tests** to the end of `tests/test_effective_config.py`.
To keep every commit in the series green for `git bisect`, temporarily put
`@pytest.mark.xfail(strict=True, reason="#180: green after Task 6")` directly above
`def test_scheduler_and_entrypoint_have_no_per_knob_yq_reads` and
`def test_entrypoint_wires_run_config_after_materialize`. Task 6 Step 5 removes both
markers. Because they are `strict`, they turn into failures if they pass early.

```python
# ── #180: run-config.json / run-config.env ─────────────────────────────────────
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
REPO_CONFIG = str(REPO / "config" / "config.yaml")
GOLDEN = json.loads((REPO / "tests" / "fixtures" / "run_config_env_golden.json")
                    .read_text(encoding="utf-8"))
MH_ADAPTER = "token_optimization:\n  enforce:\n    implement: true\n"


def _parse_env(text):
    """Mirror of scripts/run_config_env.sh's parse: VAR=value, bare VAR = None (null)."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
        else:
            out[line] = None
    return out


def _run_cli(argv):
    old = sys.argv[:]
    sys.argv = ["effective_config", *argv]
    try:
        ec.main()
    except SystemExit as exc:
        return exc.code
    finally:
        sys.argv = old
    return None


def _env_of(out_dir):
    return _parse_env((out_dir / "run-config.env").read_text(encoding="utf-8"))


def _json_of(out_dir):
    return json.loads((out_dir / "run-config.json").read_text(encoding="utf-8"))


def test_whitelist_covers_exactly_the_golden_variables():
    for reader in ("scheduler", "entrypoint"):
        rows = [v for v, _, readers in ec.ENV_WHITELIST if reader in readers]
        assert rows == list(GOLDEN[reader]), reader


def test_scheduler_candidates_match_former_resolve_config_yaml_order():
    assert ec.SCHEDULER_CANDIDATES == (
        "/workspace/project/config/config.yaml",
        "/opt/dark-factory/config/config.yaml",
        "/opt/refinement-skills/config.yaml",
    )


def test_render_env_scalars_nulls_and_projection():
    cfg = {"scheduler": {"poll_interval": 60},
           "dispatch_ceiling": {"enabled": True, "keywords": "a|b=c"},
           "epic_autopilot": {"confidence_floor": 0.7, "enabled": False},
           "conflict_resolution": {"ai_tier": True}}
    text = ec.render_env(cfg, "scheduler", "/x/config.yaml")
    assert "# source: /x/config.yaml" in text.splitlines()
    env = _parse_env(text)
    assert len(env) == 33
    assert env["POLL_INTERVAL"] == "60"
    assert env["DISPATCH_CEILING_ENABLED"] == "true"
    assert env["EPIC_AUTOPILOT_ENABLED"] == "false"
    assert env["ABOVE_CEILING_KEYWORDS"] == "a|b=c"
    assert env["EPIC_AUTOPILOT_CONFIDENCE_FLOOR"] == "0.7"
    assert env["MAX_RETRIES"] is None                      # absent key -> bare name
    assert "CONFLICT_RESOLUTION_AI_TIER" not in env        # entrypoint-only row
    ep = _parse_env(ec.render_env(cfg, "entrypoint", "x"))
    assert list(ep) == ["FACTORY_WIP_LIMIT", "CONFLICT_RESOLUTION_AI_TIER",
                        "SESSION_WINDOW_BACKOFF_ENABLED", "SESSION_WINDOW_BUFFER_MINUTES",
                        "SESSION_WINDOW_FALLBACK_MINUTES"]
    assert ep["CONFLICT_RESOLUTION_AI_TIER"] == "true"


def test_render_env_non_scalar_is_null_with_warning(capsys):
    env = _parse_env(ec.render_env({"scheduler": {"poll_interval": [1, 2]}}, "scheduler", "x"))
    assert env["POLL_INTERVAL"] is None
    assert "POLL_INTERVAL is not a single-line scalar" in capsys.readouterr().err


def test_scheduler_mode_first_match_never_reads_clone(tmp_path, monkeypatch):
    """Requirement 2 / safety invariant 2: whole-file first match; no baked fill-in, no clone."""
    first = tmp_path / "project-config.yaml"
    first.write_text(yaml.safe_dump({"dispatch_ceiling": {"keywords": "first-only"},
                                     "epic_autopilot": {"enabled": True}}), encoding="utf-8")
    baked = tmp_path / "baked-config.yaml"
    baked.write_text(yaml.safe_dump({
        "scheduler": {"poll_interval": 60},
        "dispatch_ceiling": {"enabled": True, "label": "above-ceiling", "keywords": "baked"},
        "epic_autopilot": {"enabled": False}}), encoding="utf-8")
    # A clone-shaped cwd: the old "." default would have probed ./.claude and ./.factory.
    cwd = _mk_clone(tmp_path)
    _write_clone_cfg(cwd, {"dispatch_ceiling": {"keywords": "clone"}})
    _write_adapter(cwd, "token_optimization:\n  budgets:\n    refine: 1\n")
    monkeypatch.chdir(cwd)
    out = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(out),
                     "--candidate", str(first), "--candidate", str(baked)]) == 0
    env = _env_of(out)
    assert env["ABOVE_CEILING_KEYWORDS"] == "first-only"
    assert env["EPIC_AUTOPILOT_ENABLED"] == "true"
    assert env["DISPATCH_CEILING_ENABLED"] is None   # absent from the first file: empty,
    assert env["ABOVE_CEILING_LABEL"] is None        # never the baked value
    assert env["POLL_INTERVAL"] is None
    assert _json_of(out) == yaml.safe_load(first.read_text(encoding="utf-8"))
    assert f"# source: {first}" in (out / "run-config.env").read_text(encoding="utf-8")


def test_scheduler_mode_no_candidate_exits_1_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(out),
                     "--candidate", str(tmp_path / "missing.yaml")]) == 1
    assert not (out / "run-config.env").exists()
    assert not (out / "run-config.json").exists()
    assert "config.yaml not found" in capsys.readouterr().err


def test_scheduler_mode_materialize_requires_out_dir(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("scheduler: {poll_interval: 1}\n", encoding="utf-8")
    assert _run_cli(["--materialize", "--candidate", str(cfg)]) == 2
    assert "scheduler mode --materialize needs --out-dir" in capsys.readouterr().err


def test_run_config_env_matches_golden_fixture(tmp_path):
    """Safety invariant 1: the 33 scheduler + 5 entrypoint values the old yq readers produced
    from config/config.yaml (tests/fixtures/run_config_env_golden.json). A deliberate change
    to one of these config values must update the fixture in the same commit."""
    sched = tmp_path / "state"
    assert _run_cli(["--materialize", "--out-dir", str(sched), "--candidate", REPO_CONFIG]) == 0
    env = _env_of(sched)
    assert env == GOLDEN["scheduler"]
    assert list(env) == list(GOLDEN["scheduler"])  # apply/log order preserved
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    run_out = tmp_path / "artifacts"
    ec.materialize(str(clone), REPO_CONFIG, str(run_out))
    assert _env_of(run_out) == GOLDEN["entrypoint"]


@pytest.mark.parametrize("adapter_text", [None, MH_ADAPTER], ids=["self", "markethawk"])
def test_run_config_json_equals_clone_side_materialization(tmp_path, adapter_text):
    """Requirement 8 / safety invariant 1: run-config.json == the clone-side YAML of the
    same run, so budget_gate.sh (run-config.json) and budget_enforce.py (clone file) agree."""
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    if adapter_text:
        _write_adapter(clone, adapter_text)
    out = tmp_path / "artifacts"
    ec.materialize(str(clone), REPO_CONFIG, str(out))
    clone_side = yaml.safe_load(
        (clone / ".claude" / "skills" / "refinement" / "config.yaml").read_text(encoding="utf-8"))
    run_cfg = _json_of(out)
    assert run_cfg == clone_side
    expected_impl = True if adapter_text else yaml.safe_load(
        open(REPO_CONFIG, encoding="utf-8"))["token_optimization"]["enforce"]["implement"]
    assert run_cfg["token_optimization"]["enforce"]["implement"] is expected_impl


def test_run_config_json_is_committed_clone_copy_alone(tmp_path):
    """Requirement 2: a committed copy is used alone — no baked fill-in, no adapter overlay."""
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    copy = {"dispatch_ceiling": {"keywords": "clone-kw"}}
    _write_clone_cfg(clone, copy)
    _write_adapter(clone, "token_optimization:\n  budgets:\n    refine: 12345\n")
    out = tmp_path / "artifacts"
    summary = ec.materialize(str(clone), baked, str(out))
    assert "left in place" in summary
    assert _json_of(out) == copy
    assert _env_of(out)["FACTORY_WIP_LIMIT"] is None   # baked has it; the copy does not


def test_materialize_artifact_write_failure_is_fail_open(tmp_path, capsys):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    summary = ec.materialize(str(clone), baked, str(blocker))
    assert "materialized from baked defaults" in summary
    assert (clone / ".claude" / "skills" / "refinement" / "config.yaml").is_file()
    assert "cannot write run-config artifacts" in capsys.readouterr().err


def test_cli_run_mode_writes_artifacts_with_out_dir(tmp_path):
    baked = _mk_baked(tmp_path)
    clone = _mk_clone(tmp_path)
    (clone / ".git").mkdir()
    out = tmp_path / "artifacts"
    assert _run_cli(["--clone-dir", str(clone), "--baked", baked,
                     "--materialize", "--out-dir", str(out)]) == 0
    assert _json_of(out) == BAKED
    assert _env_of(out)["FACTORY_WIP_LIMIT"] == "1"


def test_load_run_config_prefers_exported_json_then_resolver(tmp_path, monkeypatch):
    clone = _mk_clone(tmp_path)
    _write_clone_cfg(clone, {"dispatch_ceiling": {"keywords": "from-clone"}})
    monkeypatch.delenv(ec.RUN_CONFIG_JSON_ENV, raising=False)
    assert ec.load_run_config(str(clone)) == {"dispatch_ceiling": {"keywords": "from-clone"}}
    rc = tmp_path / "run-config.json"
    rc.write_text(json.dumps({"dispatch_ceiling": {"keywords": "from-json"}}), encoding="utf-8")
    monkeypatch.setenv(ec.RUN_CONFIG_JSON_ENV, str(rc))
    assert ec.load_run_config(str(clone))["dispatch_ceiling"]["keywords"] == "from-json"
    rc.write_text("{not json", encoding="utf-8")
    assert ec.load_run_config(str(clone))["dispatch_ceiling"]["keywords"] == "from-clone"


def test_first_match_list_first_nonempty_file_wins_without_merge(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("epic_autopilot:\n  hard_exclude_paths: []\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text('epic_autopilot:\n  hard_exclude_paths: ["b/", "\\"q/\\""]\n', encoding="utf-8")
    c = tmp_path / "c.yaml"
    c.write_text('epic_autopilot:\n  hard_exclude_paths: ["c/"]\n', encoding="utf-8")
    paths = [str(tmp_path / "missing.yaml"), str(a), str(b), str(c)]
    assert ec.first_match_list(paths, "epic_autopilot.hard_exclude_paths") == ["b/", "q/"]
    assert ec.first_match_list([str(a)], "epic_autopilot.hard_exclude_paths") == []


def test_scheduler_and_entrypoint_have_no_per_knob_yq_reads():
    """Acceptance criteria: read_config / the entrypoint reader no longer call yq per knob."""
    for rel in ("scheduler.sh", "entrypoint.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for gone in ("_set_cfg", "_epcfg", "_entrypoint_cfg_apply", "resolve_config_yaml",
                     "CONFIG_YAML_PATHS"):
            assert gone not in text, f"{rel} still mentions {gone}"
        assert not re.search(r"\byq\b", text), f"{rel} still runs yq"


def test_entrypoint_wires_run_config_after_materialize():
    ep = (REPO / "entrypoint.sh").read_text(encoding="utf-8")
    order = ['--materialize --out-dir "$ARTIFACTS_DIR"',
             'export FACTORY_RUN_CONFIG_JSON="$ARTIFACTS_DIR/run-config.json"',
             'apply_run_config_env "$ARTIFACTS_DIR/run-config.env" entrypoint',
             "\n_compute_side_effect_level\n"]
    idx = [ep.find(s) for s in order]
    assert -1 not in idx, dict(zip(order, idx))
    assert idx == sorted(idx)


def test_no_reader_takes_side_effect_or_blast_radius_from_run_config():
    """Safety invariant 2: the trust-boundary readers never consume the new artifacts."""
    needles = ("run-config", "run_config", "RUN_CONFIG")
    for rel in ("scripts/gate_blast_radius.py", "scripts/factory_core/side_effect.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not any(n in text for n in needles), rel
    ep = (REPO / "entrypoint.sh").read_text(encoding="utf-8")
    m = re.search(r"^_compute_side_effect_level\(\) \{\n(.*?)^\}", ep, re.S | re.M)
    assert m, "_compute_side_effect_level() not found in entrypoint.sh"
    assert not any(n in m.group(1) for n in needles)
```

- [ ] **Step 3: Run and confirm they fail**

```bash
python -m pytest tests/test_effective_config.py -q 2>&1 | tail -3
```
Expected: `17 failed, 13 passed` against pristine `f457c2c`, as measured by the architect
review. The two structural tests below are counted as `xfailed` once they are marked.
- The extended clone-wins test fails with `TypeError: materialize() takes from 1 to 2
  positional arguments but 3 were given`.
- The new tests fail with `AttributeError: module 'factory_core.effective_config' has no
  attribute …` (`ENV_WHITELIST`, `SCHEDULER_CANDIDATES`, `render_env`, …), or with
  `materialize()` arity errors.
- `test_no_reader_takes_side_effect_or_blast_radius_from_run_config` already passes.
- The `…no_per_knob_yq_reads` and `…wires_run_config_after_materialize` tests report
  `xfailed` until Task 6.
- `test_scheduler_mode_materialize_requires_out_dir` fails on its stderr assertion. Its
  exit code is already 2, but only because old argparse rejects `--candidate`.

---

## Task 3: `effective_config` — implementation

**Files:** Modify `scripts/factory_core/effective_config.py`

- [ ] **Step 1: Replace the file's contents with** the version below. `resolve()`,
`_load_yaml`, `_adapter_blocks`, `_git_exclude` and `materialize()`'s clone-side
behaviour are unchanged. The diff adds:

- the docstring paragraph, `import json`, and the constants;
- `ENV_WHITELIST` and the new functions;
- `materialize()`'s `out_dir` parameter and its two `_write_run_artifacts_fail_open` calls;
- `_scheduler_main`;
- `main()`'s new arguments, with `--clone-dir` now defaulting to `None`.

```python
"""Materialize the effective refinement config for a run.

Merge order (lowest→highest): baked config/config.yaml ← clone
.claude/skills/refinement/config.yaml (transition) ← target-tunable blocks from
.factory/adapter.yaml (today: token_optimization).

#180: also the single writer of the run-config artifacts — run-config.json (the whole
tree) and run-config.env (the ENV_WHITELIST projection bash reads through
scripts/run_config_env.sh) — for two call sites:
  scheduler mode (scheduler.sh, no --clone-dir): the first existing SCHEDULER_CANDIDATES
      file, whole-file, no clone and no adapter; fail-closed.
  run mode (entrypoint.sh, --clone-dir): a committed clone copy alone, else the
      baked ← adapter merge materialize() also writes into the clone; fail-open.
"""
import argparse, json, os, sys
from . import adapter
from .adapter import _deep_merge

# Adapter top-level keys that override config.yaml blocks (df#14).
TARGET_TUNABLE_BLOCKS = ("token_optimization",)

_BAKED_PATH = "/opt/dark-factory/config/config.yaml"
_CLONE_REL = ".claude/skills/refinement/config.yaml"

RUN_CONFIG_JSON = "run-config.json"
RUN_CONFIG_ENV = "run-config.env"
# entrypoint.sh exports the path of its run-config.json under this name: archon gives every
# bash node its own $ARTIFACTS_DIR (the workflow run's directory, not the entrypoint's).
RUN_CONFIG_JSON_ENV = "FACTORY_RUN_CONFIG_JSON"

# Scheduler mode: first existing file wins, whole-file (scheduler.sh's former
# resolve_config_yaml): bind-mounted target checkout → baked image → legacy baked path.
SCHEDULER_CANDIDATES = (
    "/workspace/project/config/config.yaml",
    _BAKED_PATH,
    "/opt/refinement-skills/config.yaml",
)

# The single owner of the bash projection (run-config.env): (ENV_VAR, dotted.path, readers).
# "scheduler" rows are scheduler.sh read_config's knobs, "entrypoint" rows entrypoint.sh's;
# order is apply (and log) order. New bash-consumed knob = one row here.
_S, _E = "scheduler", "entrypoint"
ENV_WHITELIST = (
    ("POLL_INTERVAL", "scheduler.poll_interval", (_S,)),
    ("MAX_RETRIES", "scheduler.max_retries", (_S,)),
    ("RATE_LIMIT_FLOOR", "scheduler.rate_limit_floor", (_S,)),
    ("FACTORY_WIP_LIMIT", "scheduler.factory_wip_limit", (_S, _E)),
    ("MAIN_RED_RECHECK_ENABLED", "scheduler.main_red_recheck_enabled", (_S,)),
    ("MAIN_RED_RECHECK_MINUTES", "scheduler.main_red_recheck_minutes", (_S,)),
    ("BLOCKED_RESCUE_ENABLED", "scheduler.blocked_rescue_enabled", (_S,)),
    ("REFINE_WIP_LIMIT", "refine.wip_limit", (_S,)),
    ("DIRECT_TO_PR_LABEL", "direct_to_pr.label", (_S,)),
    ("SPEC_GRACE_MINUTES", "direct_to_pr.spec_grace_minutes", (_S,)),
    ("PLAN_GRACE_MINUTES", "direct_to_pr.plan_grace_minutes", (_S,)),
    ("CONFLICT_RESOLUTION_ENABLED", "conflict_resolution.enabled", (_S,)),
    ("DISPATCH_CEILING_ENABLED", "dispatch_ceiling.enabled", (_S,)),
    ("ABOVE_CEILING_LABEL", "dispatch_ceiling.label", (_S,)),
    ("ABOVE_CEILING_KEYWORDS", "dispatch_ceiling.keywords", (_S,)),
    ("EPIC_AUTOPILOT_ENABLED", "epic_autopilot.enabled", (_S,)),
    ("EPIC_AUTOPILOT_MODEL", "epic_autopilot.model", (_S,)),
    ("EPIC_AUTOPILOT_DAILY_CAP", "epic_autopilot.daily_cap", (_S,)),
    ("EPIC_AUTOPILOT_CONFIDENCE_FLOOR", "epic_autopilot.confidence_floor", (_S,)),
    ("EPIC_AUTOPILOT_OPT_OUT_LABEL", "epic_autopilot.opt_out_label", (_S,)),
    ("EPIC_AUTOPILOT_HOLD_TTL_HOURS", "epic_autopilot.hold_ttl_hours", (_S,)),
    ("EPIC_AUTOPILOT_SIZE_CEILING", "epic_autopilot.size_ceiling", (_S,)),
    ("EPIC_AUTOPILOT_START_EPICS", "epic_autopilot.start_epics", (_S,)),
    ("EPIC_AUTOPILOT_SENSITIVE_KEYWORDS", "epic_autopilot.sensitive_keywords", (_S,)),
    ("EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "epic_autopilot.allow_self_improvement", (_S,)),
    ("MAIN_RED_AUTOFIX_ENABLED", "main_red_autofix.enabled", (_S,)),
    ("MAIN_RED_AUTOFIX_MODEL", "main_red_autofix.model", (_S,)),
    ("MAIN_RED_AUTOFIX_MAX_ATTEMPTS", "main_red_autofix.max_attempts", (_S,)),
    ("MAIN_RED_AUTOFIX_THROTTLE_MIN", "main_red_autofix.throttle_minutes", (_S,)),
    ("TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED", "token_optimization.architecture.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_MEMORY_ENABLED", "token_optimization.memory.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_COMMENTS_ENABLED", "token_optimization.comments.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_DIFF_ENABLED", "token_optimization.diff.enabled", (_S,)),
    ("CONFLICT_RESOLUTION_AI_TIER", "conflict_resolution.ai_tier", (_E,)),
    ("SESSION_WINDOW_BACKOFF_ENABLED", "scheduler.session_window_backoff_enabled", (_E,)),
    ("SESSION_WINDOW_BUFFER_MINUTES", "scheduler.session_window_buffer_minutes", (_E,)),
    ("SESSION_WINDOW_FALLBACK_MINUTES", "scheduler.session_window_fallback_minutes", (_E,)),
)



def _load_yaml(path: str) -> dict:
    """Parse YAML mapping at path; {} + stderr warning on any failure (never raise)."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            print(f"effective-config: {path} is not a mapping — ignoring", file=sys.stderr)
            return {}
        return data
    except Exception as exc:
        print(f"effective-config: cannot read {path}: {exc}", file=sys.stderr)
        return {}


def _adapter_blocks(clone_dir: str) -> dict:
    """Return {block: merged_value} for TARGET_TUNABLE_BLOCKS the adapter file itself sets.

    Fail-open: an invalid adapter.yaml skips adapter overrides (consistent with
    the adapter contract) instead of killing config resolution.
    """
    path = os.path.join(clone_dir, ".factory", "adapter.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        merged = adapter.load(clone_dir)
        # Presence check against the RAW file: adapter_defaults may or may not
        # carry these keys, so load() output alone cannot prove the file set them.
        import yaml
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as exc:  # AdapterError + raw-read failures share the fail-open path
        print(f"effective-config: adapter invalid — skipping adapter overrides: {exc}",
              file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {b: merged[b] for b in TARGET_TUNABLE_BLOCKS if b in raw and b in merged}


def resolve(clone_dir: str, baked_path: str = _BAKED_PATH) -> tuple[dict, dict]:
    """Return (merged_config, sources); sources maps each tunable block to the layer that last set it."""
    if not os.path.isfile(baked_path):
        print(f"effective-config: baked config missing at {baked_path}", file=sys.stderr)
        merged = {}
    else:
        merged = _load_yaml(baked_path)
    sources = {b: "baked" for b in TARGET_TUNABLE_BLOCKS}
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        clone_cfg = _load_yaml(clone_cfg_path)
        merged = _deep_merge(merged, clone_cfg)
        for b in TARGET_TUNABLE_BLOCKS:
            if b in clone_cfg:
                sources[b] = "clone"
    for b, val in _adapter_blocks(clone_dir).items():
        merged = _deep_merge(merged, {b: val})
        sources[b] = "adapter"
    return merged, sources


def _git_exclude(clone_dir: str, rel: str) -> None:
    """Append rel to <clone>/.git/info/exclude (idempotent; creates the file if missing)."""
    excl = os.path.join(clone_dir, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(excl), exist_ok=True)
    content = ""
    if os.path.isfile(excl):
        with open(excl, encoding="utf-8") as f:
            content = f.read()
    if rel in content.splitlines():
        return
    with open(excl, "a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(rel + "\n")


def _get(cfg, dotted: str):
    """Value at dotted path, or None when any segment is missing or not a mapping."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _env_value(var: str, value):
    """yq-style rendering of one whitelisted scalar; None = null (written as a bare name)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)) and "\n" not in str(value):
        return str(value)
    print(f"effective-config: {var} is not a single-line scalar — emitted as null",
          file=sys.stderr)
    return None


def render_env(cfg: dict, reader: str, source: str) -> str:
    """run-config.env text: `VAR=value` (raw, unquoted) or bare `VAR` (null), whitelist order."""
    lines = ["# run-config.env (factory_core.effective_config, #180). Parsed line by line by",
             "# scripts/run_config_env.sh -- never source it: values are raw and unquoted.",
             f"# source: {source}"]
    for var, dotted, readers in ENV_WHITELIST:
        if reader in readers:
            val = _env_value(var, _get(cfg, dotted))
            lines.append(var if val is None else f"{var}={val}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_run_artifacts(cfg: dict, out_dir: str, reader: str, source: str) -> None:
    """Write run-config.json (whole tree), then run-config.env (reader's projection). Raises."""
    os.makedirs(out_dir, exist_ok=True)
    _atomic_write(os.path.join(out_dir, RUN_CONFIG_JSON),
                  json.dumps(cfg, indent=2, default=str) + "\n")
    _atomic_write(os.path.join(out_dir, RUN_CONFIG_ENV), render_env(cfg, reader, source))


def resolve_scheduler(candidates=SCHEDULER_CANDIDATES):
    """Scheduler mode: (config, path) of the first existing candidate, or (None, None).

    Whole-file, no merge, no adapter overlay, never a clone: the bind-mounted target
    checkout's file replaces the baked one entirely, keys it omits resolve empty.
    """
    for path in candidates:
        if os.path.isfile(path):
            return _load_yaml(path), path
    return None, None


def run_config(clone_dir, baked_path: str = _BAKED_PATH):
    """Run mode content of run-config.json, as (config, source).

    A committed clone copy is used alone (today's "clone file wins": every run-side reader
    reads only that file); otherwise resolve()'s baked ← adapter merge, the tree
    materialize() writes into the clone. clone_dir=None (standalone caller) = baked only.
    """
    if clone_dir is None:
        return (_load_yaml(baked_path) if os.path.isfile(baked_path) else {}), baked_path
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        return _load_yaml(clone_cfg_path), clone_cfg_path
    merged, sources = resolve(clone_dir, baked_path)
    return merged, _merged_source(sources)


def _merged_source(sources: dict) -> str:
    return "baked <- adapter (" + ", ".join(f"{b} source: {s}" for b, s in sources.items()) + ")"


def load_run_config(clone_dir=None, baked_path: str = _BAKED_PATH) -> dict:
    """Config for run-side Python readers: entrypoint.sh's run-config.json
    ($FACTORY_RUN_CONFIG_JSON), else run_config()'s resolver (standalone/pre-materialization)."""
    path = os.environ.get(RUN_CONFIG_JSON_ENV)
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            print(f"effective-config: {path} is not a mapping — resolving instead",
                  file=sys.stderr)
        except Exception as exc:
            print(f"effective-config: cannot read {path}: {exc} — resolving instead",
                  file=sys.stderr)
    return run_config(clone_dir, baked_path)[0]


def first_match_list(candidates, dotted: str) -> list:
    """Items of the first candidate file whose `dotted` key is a non-empty list; [] if none.

    No merge and no clone/adapter layer: replaces epic_autopilot's former per-file
    `yq '.<dotted>[]'` loop with the same candidate order (#180 R5).
    """
    import yaml
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        val = _get(data, dotted)
        if isinstance(val, list):
            items = [str(v).strip().strip('"') for v in val if v is not None and str(v).strip()]
            if items:
                return items
    return []


def materialize(clone_dir: str, baked_path: str = _BAKED_PATH, out_dir: str | None = None) -> str:
    """Entrypoint-facing operation; returns a one-line human summary.

    Transition period (clone config.yaml exists): write NOTHING into the clone — the
    committed clone file wins byte-identically; warn on adapter drift. Post-cleanup (no
    clone config): write the merged config into the clone and git-exclude it so the
    materialized artifact can never be committed back to the target repo.
    With out_dir (#180), also write run-config.{json,env} there from the same content.
    """
    merged, sources = resolve(clone_dir, baked_path)
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        clone_cfg = _load_yaml(clone_cfg_path)
        clone_layer = _deep_merge(_load_yaml(baked_path) if os.path.isfile(baked_path) else {},
                                  clone_cfg)
        for b in TARGET_TUNABLE_BLOCKS:
            if sources[b] == "adapter" and merged.get(b) != clone_layer.get(b):
                print(f"effective-config: WARNING — adapter/{b} drifts from clone config.yaml"
                      " — clone file wins until P3 cleanup removes it;"
                      " re-sync .factory/adapter.yaml", file=sys.stderr)
        srcs = ", ".join(f"{b} source: {'clone' if b in clone_cfg else 'baked'}"
                         for b in TARGET_TUNABLE_BLOCKS)
        _write_run_artifacts_fail_open(clone_cfg, out_dir, clone_cfg_path)
        return f"effective-config: clone config present — left in place ({srcs})"
    import yaml
    # Exclude BEFORE writing: if the exclude append fails, better to have no
    # materialized file than an unexcluded one a factory agent could commit.
    _git_exclude(clone_dir, _CLONE_REL)
    os.makedirs(os.path.dirname(clone_cfg_path), exist_ok=True)
    with open(clone_cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    _write_run_artifacts_fail_open(merged, out_dir, _merged_source(sources))
    srcs = ", ".join(f"{b} source: {sources[b]}" for b in TARGET_TUNABLE_BLOCKS)
    return f"effective-config: materialized from baked defaults ({srcs})"


def _write_run_artifacts_fail_open(cfg: dict, out_dir: str | None, source: str) -> None:
    """Run mode: a run-config write failure warns and never blocks the clone-side file."""
    if not out_dir:
        return
    try:
        write_run_artifacts(cfg, out_dir, "entrypoint", source)
    except Exception as exc:
        print(f"effective-config: cannot write run-config artifacts to {out_dir}: {exc}",
              file=sys.stderr)


def _scheduler_main(args) -> int:
    """Scheduler mode (no --clone-dir). Fail-closed: non-zero and nothing written on error."""
    candidates = tuple(args.candidate) if args.candidate else SCHEDULER_CANDIDATES
    cfg, path = resolve_scheduler(candidates)
    if path is None:
        print("effective-config: ERROR — config.yaml not found — searched: "
              + " ".join(candidates), file=sys.stderr)
        return 1
    if args.print_merged:
        import yaml
        print(yaml.safe_dump(cfg, sort_keys=False))
        return 0
    if not args.out_dir:
        print("effective-config: scheduler mode --materialize needs --out-dir", file=sys.stderr)
        return 2
    try:
        write_run_artifacts(cfg, args.out_dir, "scheduler", path)
    except Exception as exc:
        print(f"effective-config: cannot write run-config artifacts: {exc}", file=sys.stderr)
        return 1
    print(f"effective-config: scheduler mode — resolved {path}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--clone-dir", default=None,
                   help="run mode (entrypoint.sh); omitted = scheduler mode (scheduler.sh)")
    p.add_argument("--baked", default=_BAKED_PATH)
    p.add_argument("--out-dir", default=None,
                   help="also write run-config.json + run-config.env here"
                        " (required for scheduler-mode --materialize)")
    p.add_argument("--candidate", action="append", default=None,
                   help="scheduler mode: replace SCHEDULER_CANDIDATES (tests only)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--materialize", action="store_true",
                   help="Materialize the effective config; print summary")
    g.add_argument("--print", dest="print_merged", action="store_true",
                   help="Dump merged YAML to stdout (debugging/tests)")
    args = p.parse_args()
    if args.clone_dir is None:
        sys.exit(_scheduler_main(args))
    # Run mode. Fail-open: a config-resolution bug must never kill a run — always exit 0.
    try:
        if args.materialize:
            print(materialize(args.clone_dir, args.baked, args.out_dir))
        else:
            import yaml
            merged, _ = resolve(args.clone_dir, args.baked)
            print(yaml.safe_dump(merged, sort_keys=False))
    except Exception as exc:
        print(f"effective-config: error (fail-open, exit 0): {exc}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the module tests**

```bash
python -m pytest tests/test_effective_config.py -q 2>&1 | tail -3
```
Expected: all passed, except `2 xfailed` (`test_scheduler_and_entrypoint_have_no_per_knob_yq_reads`
and `test_entrypoint_wires_run_config_after_materialize`, which go green in Task 6).

- [ ] **Step 3: Smoke the CLI's two modes against the real config**

```bash
mkdir -p /tmp/rc180 && PYTHONPATH=scripts python3 -m factory_core.effective_config \
  --materialize --out-dir /tmp/rc180 --candidate config/config.yaml; echo "rc=$?"
head -5 /tmp/rc180/run-config.env
PYTHONPATH=scripts python3 -m factory_core.effective_config --materialize \
  --out-dir /tmp/rc180 --candidate /nonexistent.yaml; echo "rc=$?"
```
Expected output:
- `effective-config: scheduler mode — resolved config/config.yaml`, then `rc=0`;
- the three `#` header lines, then `POLL_INTERVAL=60` and `MAX_RETRIES=3`;
- `effective-config: ERROR — config.yaml not found — searched: /nonexistent.yaml`, then `rc=1`.

- [ ] **Step 4: Commit**

```bash
git add scripts/factory_core/effective_config.py tests/test_effective_config.py
git commit -m "feat(config): effective_config writes run-config.{json,env} in scheduler and run mode (#180)"
```

---

## Task 4: The shared bash reader `scripts/run_config_env.sh`

**Files:** Create `tests/test_run_config_env.sh`, `scripts/run_config_env.sh`; Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Write the test**, `tests/test_run_config_env.sh`:

```bash
#!/usr/bin/env bash
# scripts/run_config_env.sh (#180): apply_run_config_env replays scheduler.sh's former
# _set_cfg semantics (scheduler mode) and entrypoint.sh's former _epcfg (entrypoint mode).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/run_config_env.sh
source "$REPO_ROOT/scripts/run_config_env.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PASSED=0; FAILED=0
_assert() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED + 1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED + 1))
  fi
}

ENVF="$WORK/run-config.env"
cat > "$ENVF" <<'EOF'
# run-config.env (fixture)
# source: /fixture/config.yaml
T_UNSET=from-config
T_SET_DIFF=from-config
T_SET_EMPTY=from-config
T_SET_SAME=same
T_NULL
T_PIPE=migration|migrate|a=b
T_SUBST=$(touch pwned-marker)
bad-name=x
EOF

# _probe <mode>: apply in a subshell with a fixed starting env; print the resulting state.
_probe() {
  ( cd "$WORK" || exit 9
    unset T_UNSET T_NULL T_PIPE T_SUBST
    T_SET_DIFF=env-val; T_SET_EMPTY=""; T_SET_SAME=same
    apply_run_config_env "$ENVF" "$1" 2>"$WORK/$1.err" >"$WORK/$1.out"
    echo "rc=$?"
    echo "UNSET=${T_UNSET-<unset>}"; echo "DIFF=${T_SET_DIFF}"; echo "EMPTY=[${T_SET_EMPTY}]"
    echo "NULL=[${T_NULL-<unset>}]"; echo "PIPE=${T_PIPE-}"; echo "SUBST=${T_SUBST-}"
    echo "EXPORTED_UNSET=$(env | grep -c '^T_UNSET=')"
    echo "EXPORTED_SAME=$(env | grep -c '^T_SET_SAME=')" )
}
_field() { sed -n "s/^$1=//p" <<<"$2"; }

echo "--- scheduler mode (former _set_cfg) ---"
S=$(_probe scheduler)
_assert "rc 0" "0" "$(_field rc "$S")"
_assert "unset var takes the config value" "from-config" "$(_field UNSET "$S")"
_assert "and is exported" "1" "$(_field EXPORTED_UNSET "$S")"
_assert "set var wins" "env-val" "$(_field DIFF "$S")"
_assert "override is logged" "1" "$(grep -c "^\[config\] T_SET_DIFF=env-val (env override; config has 'from-config')$" "$WORK/scheduler.err")"
_assert "set-to-empty still wins" "[]" "$(_field EMPTY "$S")"
_assert "empty override is logged" "1" "$(grep -c "^\[config\] T_SET_EMPTY= (env override; config has 'from-config')$" "$WORK/scheduler.err")"
_assert "equal value is not logged" "0" "$(grep -c 'T_SET_SAME' "$WORK/scheduler.err")"
_assert "null exports as empty, not unset" "[]" "$(_field NULL "$S")"
_assert "value keeps | and =" "migration|migrate|a=b" "$(_field PIPE "$S")"
_assert "value is never evaluated" '$(touch pwned-marker)' "$(_field SUBST "$S")"
_assert "no command ran" "no" "$([ -e "$WORK/pwned-marker" ] && echo yes || echo no)"
_assert "malformed line skipped with a warning" "1" "$(grep -c 'skipping malformed run-config.env line: bad-name=x' "$WORK/scheduler.err")"
_assert "loaded-from line names the source" "[config] loaded from /fixture/config.yaml" "$(cat "$WORK/scheduler.out")"

echo "--- entrypoint mode (former _epcfg) ---"
E=$(_probe entrypoint)
_assert "rc 0" "0" "$(_field rc "$E")"
_assert "unset var takes the config value" "from-config" "$(_field UNSET "$E")"
_assert "set (bootstrap) var is kept" "env-val" "$(_field DIFF "$E")"
_assert "kept value is logged" "1" "$(grep -c "^\[entrypoint-config\] T_SET_DIFF=env-val (env/bootstrap override; config has 'from-config')$" "$WORK/entrypoint.err")"
_assert "set-to-empty is kept" "[]" "$(_field EMPTY "$E")"
_assert "null config value is skipped" "[<unset>]" "$(_field NULL "$E")"
_assert "equal value is (re-)exported" "1" "$(_field EXPORTED_SAME "$E")"
_assert "loaded-from line" "[entrypoint-config] loaded from /fixture/config.yaml" "$(cat "$WORK/entrypoint.out")"

echo "--- missing / empty / comment-only file, unknown mode ---"
apply_run_config_env "$WORK/nope.env" scheduler 2>/dev/null; _assert "missing file -> 1" "1" "$?"
: > "$WORK/empty.env"
apply_run_config_env "$WORK/empty.env" scheduler 2>/dev/null; _assert "empty file -> 1" "1" "$?"
printf '# source: x\n' > "$WORK/comments.env"
apply_run_config_env "$WORK/comments.env" entrypoint >/dev/null 2>&1; _assert "comment-only file -> 1" "1" "$?"
apply_run_config_env "$ENVF" bogus 2>/dev/null; _assert "unknown mode -> 2" "2" "$?"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
bash tests/test_run_config_env.sh; echo "rc=$?"
```
Expected: `source: …/scripts/run_config_env.sh: No such file or directory`, then FAIL lines, then `rc=1`.

- [ ] **Step 3: Create `scripts/run_config_env.sh`**

```bash
#!/usr/bin/env bash
# Shared reader for factory_core.effective_config's run-config.env (#180), sourced by
# scheduler.sh (read_config) and entrypoint.sh (post-clone). The file is parsed line by
# line and NEVER sourced: values are raw (unquoted), and sourcing would clobber operator
# env overrides and skip the override log.
#
# Line format: `VAR=value` (value = everything after the first "="), a bare `VAR` for a
# null/absent config value, `#` comments (`# source: <what was resolved>`).
#
# apply_run_config_env <file> <scheduler|entrypoint>
#   scheduler  - scheduler.sh's former _set_cfg: a variable already set, even to "", wins
#                and a differing value logs `[config] VAR=<env> (env override; config has
#                '<cfg>')`; otherwise the config value is exported (a null one as "").
#   entrypoint - entrypoint.sh's former _epcfg: a null config value is skipped; a set
#                variable (the bootstrap defaults set all five) is kept and a differing
#                value logs `[entrypoint-config] VAR=<val> (env/bootstrap override; config
#                has '<cfg>')`; an equal or unset one is exported.
# Returns 1 when <file> is missing or has no entries (callers pick fail-closed or
# fail-open), 2 on an unknown mode.
apply_run_config_env() {
  local file="$1" mode="$2" prefix line var val is_null n=0 src
  case "$mode" in
    scheduler) prefix="[config]" ;;
    entrypoint) prefix="[entrypoint-config]" ;;
    *) echo "apply_run_config_env: unknown mode '${mode}'" >&2; return 2 ;;
  esac
  [ -s "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    if [[ "$line" == *=* ]]; then
      var="${line%%=*}"; val="${line#*=}"; is_null=0
    else
      var="$line"; val=""; is_null=1
    fi
    if ! [[ "$var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "${prefix} skipping malformed run-config.env line: ${line}" >&2
      continue
    fi
    n=$((n + 1))
    if [ "$mode" = entrypoint ]; then
      [ "$is_null" = 1 ] && continue
      if [ -n "${!var+x}" ] && [ "${!var}" != "$val" ]; then
        echo "${prefix} ${var}=${!var} (env/bootstrap override; config has '${val}')" >&2
      else
        export "${var}=${val}"
      fi
    elif [ -n "${!var+x}" ]; then
      if [ "${!var}" != "$val" ]; then
        echo "${prefix} ${var}=${!var} (env override; config has '${val}')" >&2
      fi
    else
      export "${var}=${val}"
    fi
  done < "$file"
  [ "$n" -gt 0 ] || return 1
  src=$(sed -n 's/^# source: //p' "$file" | head -n 1)
  echo "${prefix} loaded from ${src:-$file}"
}
```

- [ ] **Step 4: Run it and confirm it passes**

```bash
bash tests/test_run_config_env.sh 2>&1 | tail -1
```
Expected: `Results: 26 passed, 0 failed`.

- [ ] **Step 5: List it in CI.** In `.github/workflows/ci.yml`, add one line directly after
`      - run: bash tests/test_budget_gate.sh`:

```yaml
      - run: bash tests/test_run_config_env.sh
```

- [ ] **Step 6: Commit**

```bash
git add scripts/run_config_env.sh tests/test_run_config_env.sh .github/workflows/ci.yml
git commit -m "feat(config): run_config_env.sh — one bash reader for run-config.env (#180)"
```

---

## Task 5: `scheduler.sh` — `read_config` onto scheduler mode (fail-closed)

**Files:** Rewrite `tests/test_config_deletion.sh`; Modify `scheduler.sh`, `tests/test_epic_autopilot_config.sh`, `tests/test_token_optimization_flags.py`, `tests/test_scheduler.sh`, `tests/test_dispatch_ceiling.sh`, `.github/workflows/ci.yml`

- [ ] **Step 1: Port `tests/test_config_deletion.sh`** (safety invariant 3). Replace its contents with:

```bash
#!/usr/bin/env bash
# Safety invariant 3 (#180; originally #338's "deletion test"): the scheduler fails closed.
# read_config must exit 1 with "FATAL: cannot read config.yaml" when no config candidate
# exists, or when effective_config leaves a missing or empty run-config.env behind — and
# must otherwise export the resolved file's knobs with the env-wins rule.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

PASSED=0; FAILED=0
_assert() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

WORK=$(mktemp -d /tmp/cfg-del-test-XXXXXX)
export SCHEDULER_STATE_DIR="$WORK/state"
export GH_TOKEN="${GH_TOKEN:-stub-token}" CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
# scheduler.sh runs `providers/cli.py preflight` at source time; the baked CLI it defaults
# to does not exist on a CI checkout, so stub it.
printf 'import sys\nsys.exit(0)\n' > "$WORK/providers_stub.py"
export FACTORY_PROVIDERS_CLI="$WORK/providers_stub.py"
# Set required config-driven vars so set -u is satisfied during sourcing
export POLL_INTERVAL=60 MAX_RETRIES=3 RATE_LIMIT_FLOOR=200 FACTORY_WIP_LIMIT=1
export MAIN_RED_RECHECK_ENABLED=true MAIN_RED_RECHECK_MINUTES=20 REFINE_WIP_LIMIT=2
export DIRECT_TO_PR_LABEL=direct-to-pr SPEC_GRACE_MINUTES=30 PLAN_GRACE_MINUTES=30
export CONFLICT_RESOLUTION_ENABLED=true DISPATCH_CEILING_ENABLED=true
export ABOVE_CEILING_LABEL=above-ceiling ABOVE_CEILING_KEYWORDS="migration|migrate"

SCHEDULER_SOURCE_ONLY=1 source "$SCHED" >/dev/null 2>&1
set +e  # scheduler.sh enabled errexit; every case below inspects its own exit status

# _read_config_with <candidate>... — run read_config in a subshell, with python3 wrapped so
# effective_config's scheduler mode searches only the given candidates. Prints the exit code.
_read_config_with() {
  CANDS=("$@")
  (
    python3() {
      local extra=() c
      for c in "${CANDS[@]}"; do extra+=(--candidate "$c"); done
      command python3 "$@" "${extra[@]}"
    }
    read_config
    echo "POLL_INTERVAL=${POLL_INTERVAL-<unset>}"
    echo "MAX_RETRIES=${MAX_RETRIES-<unset>}"
    echo "ABOVE_CEILING_KEYWORDS=${ABOVE_CEILING_KEYWORDS-<unset>}"
    echo "EPIC_AUTOPILOT_ENABLED=${EPIC_AUTOPILOT_ENABLED-<unset>}"
  ) > "$WORK/stdout.log" 2> "$WORK/stderr.log"
  echo $?
}
_out() { sed -n "s/^$1=//p" "$WORK/stdout.log"; }

echo "--- T1: no config candidate -> FATAL, exit 1 ---"
RC=$(_read_config_with "$WORK/nonexistent-1.yaml" "$WORK/nonexistent-2.yaml")
_assert "read_config exits 1" "1" "$RC"
_assert "FATAL line logged" "1" "$(grep -c '^FATAL: cannot read config.yaml$' "$WORK/stderr.log")"
_assert "no run-config.env written" "no" "$([ -e "$SCHEDULER_STATE_DIR/run-config.env" ] && echo yes || echo no)"

echo "--- T2: a stale run-config.env from a previous start does not mask the failure ---"
printf 'POLL_INTERVAL=1\n' > "$SCHEDULER_STATE_DIR/run-config.env"
RC=$(_read_config_with "$WORK/nonexistent-1.yaml")
_assert "read_config still exits 1" "1" "$RC"

echo "--- T3: module leaves an empty run-config.env -> FATAL, exit 1 ---"
RC=$( ( python3() { : > "$SCHEDULER_STATE_DIR/run-config.env"; }; read_config ) >/dev/null 2>"$WORK/stderr.log"; echo $? )
_assert "read_config exits 1" "1" "$RC"
_assert "FATAL line logged" "1" "$(grep -c '^FATAL: cannot read config.yaml$' "$WORK/stderr.log")"

echo "--- T4: config present -> knobs exported, env still wins ---"
FIRST="$WORK/project-config.yaml"
printf 'scheduler:\n  max_retries: 7\ndispatch_ceiling:\n  keywords: "first|only"\n' > "$FIRST"
BAKED="$WORK/baked-config.yaml"
printf 'scheduler:\n  poll_interval: 99\n  max_retries: 3\nepic_autopilot:\n  enabled: true\n' > "$BAKED"
unset POLL_INTERVAL ABOVE_CEILING_KEYWORDS
export MAX_RETRIES=9
RC=$(_read_config_with "$FIRST" "$BAKED")
_assert "read_config exits 0" "0" "$RC"
_assert "first file wins" "first|only" "$(_out ABOVE_CEILING_KEYWORDS)"
_assert "key absent from the first file is empty, not baked" "" "$(_out POLL_INTERVAL)"
_assert "env override wins" "9" "$(_out MAX_RETRIES)"
_assert "override logged" "1" "$(grep -c "^\[config\] MAX_RETRIES=9 (env override; config has '7')$" "$WORK/stderr.log")"
_assert "loaded-from names the first file" "1" "$(grep -c "^\[config\] loaded from ${FIRST}$" "$WORK/stdout.log")"

rm -rf "$WORK"
echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 2: Rewrite `tests/test_epic_autopilot_config.sh`.** It drops the `yq`
dependency, and its wiring checks now point at `ENV_WHITELIST`:

```bash
#!/usr/bin/env bash
# Verifies the epic_autopilot config section + its scheduler wiring, which since #180 is the
# effective_config.ENV_WHITELIST row scheduler.sh read_config applies (no per-knob yq call).
# Run: bash tests/test_epic_autopilot_config.sh   (needs python3 + pyyaml; listed in CI)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cfg="$HERE/../config/config.yaml"
ec="$HERE/../scripts/factory_core/effective_config.py"

# _get <dotted.key> — print a config.yaml value the way the old yq read did (bools lowercase).
_get() {
  python3 -c 'import sys, yaml
v = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for k in sys.argv[2].split("."):
    v = v[k]
print(str(v).lower() if isinstance(v, bool) else v)' "$cfg" "$1"
}

val=$(_get epic_autopilot.enabled)
[ "$val" = "false" ] || { echo "FAIL: epic_autopilot.enabled should default false, got '$val'"; exit 1; }

cap=$(_get epic_autopilot.daily_cap)
[ "$cap" = "5" ] || { echo "FAIL: epic_autopilot.daily_cap should be 5, got '$cap'"; exit 1; }

model=$(_get epic_autopilot.model)
[ "$model" = "claude-opus-5-5" ] || { echo "FAIL: epic_autopilot.model should be claude-opus-5-5, got '$model'"; exit 1; }

floor=$(_get epic_autopilot.confidence_floor)
[ "$floor" = "0.7" ] || { echo "FAIL: epic_autopilot.confidence_floor should be 0.7, got '$floor'"; exit 1; }

asi=$(_get epic_autopilot.allow_self_improvement)
[ "$asi" = "true" ] || { echo "FAIL: epic_autopilot.allow_self_improvement should default true, got '$asi'"; exit 1; }

# The scheduler wires the knobs through effective_config.ENV_WHITELIST
grep -qE '"EPIC_AUTOPILOT_ENABLED", "epic_autopilot\.enabled"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_ENABLED wiring"; exit 1; }
grep -qE '"EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "epic_autopilot\.allow_self_improvement"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT wiring"; exit 1; }
grep -qE '"EPIC_AUTOPILOT_DAILY_CAP", "epic_autopilot\.daily_cap"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_DAILY_CAP wiring"; exit 1; }

echo "PASS"
```

- [ ] **Step 3: Point `tests/test_token_optimization_flags.py`'s T2 wiring tests at the whitelist.**
Add `import sys` directly after `import pathlib`. Then replace the block that starts at
`# ── T2: scheduler.sh env var wiring` and runs up to, but not including,
`# ── T6: workflow YAML comment digest gate` with:

```python
# ── T2: scheduler.sh env var wiring (#180: via effective_config.ENV_WHITELIST) ──

def _scheduler_wired(var, dotted):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from factory_core import effective_config as ec
    return (var, dotted) in {(v, d) for v, d, readers in ec.ENV_WHITELIST
                             if "scheduler" in readers}


def test_scheduler_wires_architecture_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED",
                            "token_optimization.architecture.enabled")


def test_scheduler_wires_memory_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_MEMORY_ENABLED", "token_optimization.memory.enabled")


def test_scheduler_wires_comments_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_COMMENTS_ENABLED",
                            "token_optimization.comments.enabled")


def test_scheduler_wires_diff_enabled():
    assert _scheduler_wired("TOKEN_OPTIMIZATION_DIFF_ENABLED", "token_optimization.diff.enabled")


```

- [ ] **Step 4: Append the #180 assertions to the two non-CI scheduler tests.**

In `tests/test_scheduler.sh`, insert directly before the final
`echo "Results: ${PASSED} passed, ${FAILED} failed"`:

```bash
# ---- #180: config resolution moved to effective_config + scripts/run_config_env.sh ----
assert_eq "#180: apply_run_config_env is sourced with scheduler.sh" "function" "$(type -t apply_run_config_env)"
assert_eq "#180: resolve_config_yaml is retired" "" "$(type -t resolve_config_yaml || true)"

```

In `tests/test_dispatch_ceiling.sh`, insert directly before the final `rm -rf "$SCHEDULER_STATE_DIR"`:

```bash
# ---- #180: the ceiling knobs reach read_config through effective_config.ENV_WHITELIST ----
EC_PY="$SCRIPT_DIR/../scripts/factory_core/effective_config.py"
for row in '"DISPATCH_CEILING_ENABLED", "dispatch_ceiling.enabled"' \
           '"ABOVE_CEILING_LABEL", "dispatch_ceiling.label"' \
           '"ABOVE_CEILING_KEYWORDS", "dispatch_ceiling.keywords"'; do
  assert_eq "#180: ENV_WHITELIST row ${row%%,*}" "1" "$(grep -cF "$row" "$EC_PY")"
done

```

- [ ] **Step 5: Run them and confirm they fail**

```bash
bash tests/test_config_deletion.sh 2>&1 | tail -3; bash tests/test_epic_autopilot_config.sh
python -m pytest tests/test_token_optimization_flags.py -q 2>&1 | tail -1
```
Expected:
- `test_config_deletion.sh` reports FAIL lines. `read_config` still uses `yq`, and the
  `--candidate` wrapper has no effect.
- `test_epic_autopilot_config.sh` passes already, because Task 3 added the whitelist rows.
- `test_token_optimization_flags.py` passes already.

- [ ] **Step 6: Edit `scheduler.sh`.** After the line
`source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/provider_env.sh"`, add:

```bash
SCHEDULER_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts" && pwd)"
source "${SCHEDULER_SCRIPTS_DIR}/run_config_env.sh"
```

Then replace everything from `# --- Config YAML resolution ---` through the closing `}`
of `read_config()`. That is the block ending in `  echo "[config] loaded from ${cfg}"` +
`}`, and it removes `CONFIG_YAML_PATHS`, `resolve_config_yaml`, `_set_cfg` and all 33
calls. Replace it with:

```bash
# --- Config resolution (#180) ---
# factory_core.effective_config's scheduler mode owns the candidate search: the first
# existing of /workspace/project/config/config.yaml (bind-mounted target checkout), the
# baked /opt/dark-factory/config/config.yaml, the legacy /opt/refinement-skills/config.yaml
# -- whole-file, no merge, never a clone -- written to $SCHEDULER_STATE_DIR/run-config.{json,env}.
# apply_run_config_env (scripts/run_config_env.sh) then sets each whitelisted knob and logs
# when an env var overrides the config value. Fail-closed: no config means exit 1.
# Must be called in the main exec path (after SCHEDULER_SOURCE_ONLY guard) — not at source
# time — so test sourcing never triggers config resolution.
read_config() {
  local env_file="${SCHEDULER_STATE_DIR}/run-config.env"
  # A file left by a previous start must never stand in for a failed resolution.
  rm -f "$env_file" "${SCHEDULER_STATE_DIR}/run-config.json"
  PYTHONPATH="$SCHEDULER_SCRIPTS_DIR" python3 -m factory_core.effective_config \
    --materialize --out-dir "$SCHEDULER_STATE_DIR" || true
  apply_run_config_env "$env_file" scheduler || { echo "FATAL: cannot read config.yaml" >&2; exit 1; }
}
```

`mkdir -p "$SCHEDULER_STATE_DIR"` (`scheduler.sh:121` on main) already runs before the
`read_config` call site, so nothing else moves. In the image, `scheduler.sh` lives at
`/opt/dark-factory/scheduler.sh` (`Dockerfile:138`) with `scripts/` beside it
(`Dockerfile:140`), so `SCHEDULER_SCRIPTS_DIR` resolves to `/opt/dark-factory/scripts`.

- [ ] **Step 7: Run and confirm they pass**

```bash
bash tests/test_config_deletion.sh 2>&1 | tail -1     # Results: 12 passed, 0 failed
bash tests/test_epic_autopilot_config.sh               # PASS
grep -c 'yq\|_set_cfg' scheduler.sh                     # 0
bash tests/test_scheduler.sh 2>&1 | tail -1            # baseline + 2 passed (265 / 2 on f457c2c)
bash tests/test_dispatch_ceiling.sh 2>&1 | tail -1     # baseline + 3 passed (25 / 11 on f457c2c)
python -m pytest tests/test_token_optimization_flags.py -q 2>&1 | tail -1
```
Expected as in the comments. The failing cases in the two scheduler tests must be exactly
the baseline set (`grep FAIL` matches what Task 0 recorded).

- [ ] **Step 8: List the two ported tests in CI.** In `.github/workflows/ci.yml`, add
directly after the `test_run_config_env.sh` line from Task 4:

```yaml
      - run: bash tests/test_config_deletion.sh
      - run: bash tests/test_epic_autopilot_config.sh
```

- [ ] **Step 9: Commit**

```bash
git add scheduler.sh tests/test_config_deletion.sh tests/test_epic_autopilot_config.sh \
  tests/test_token_optimization_flags.py tests/test_scheduler.sh tests/test_dispatch_ceiling.sh \
  .github/workflows/ci.yml
git commit -m "refactor(scheduler): read_config via effective_config scheduler mode, no per-knob yq (#180)"
```

---

## Task 6: `entrypoint.sh` — retire `_entrypoint_cfg_apply`, hand off `run-config.json`

**Files:** Modify `entrypoint.sh`

The tests are already written: `test_entrypoint_wires_run_config_after_materialize` and
`test_scheduler_and_entrypoint_have_no_per_knob_yq_reads` from Task 2. Both are still
`xfail`, because `entrypoint.sh` still has `_entrypoint_cfg_apply`/`yq`.
`tests/test_entrypoint_undefined_functions.py` must stay green.

- [ ] **Step 1: Bootstrap comments.** Replace
`# Bootstrap defaults for pre-clone concurrency guard — overridden by _entrypoint_cfg_apply post-clone.` with:

```bash
# Bootstrap defaults for pre-clone concurrency guard — post-clone, apply_run_config_env keeps
# them and logs any config value that differs (scripts/run_config_env.sh, entrypoint mode).
```
and replace `# _entrypoint_cfg_apply()/config.yaml; see Task 3's callout in the #33 plan for why.` with
`# run-config.env/config.yaml; see Task 3's callout in the #33 plan for why.`

- [ ] **Step 2: Delete `_entrypoint_cfg_apply`.** Delete from the line
`# Read FACTORY_WIP_LIMIT and CONFLICT_RESOLUTION_AI_TIER from config.yaml post-clone.`
through the function's closing `}` and the blank line after it. The function ends with
`  echo "[entrypoint-config] loaded from ${cfg}"`.

- [ ] **Step 3: Comment-only edits in the header block above `_compute_side_effect_level`** (the function body stays byte-identical):
  - `# file, so it cannot share _entrypoint_cfg_apply's clone-first lookup (whose knobs all` + `# fail open).`
    → `# file, so it cannot share the clone-aware effective config the other knobs read (they all` + `# fail open).`
  - `# Runs post-clone (keeps the same call site as _entrypoint_cfg_apply) and before`
    → `# Runs post-clone (right after the effective-config knobs are applied) and before`

  Do **not** write the strings `run-config`, `run_config` or `RUN_CONFIG` anywhere inside
  the function body. The safety-invariant-2 source scan forbids them.

- [ ] **Step 4: Replace the materialize + apply block** (on main: `entrypoint.sh:830-838`):

```bash
# --- Effective config: adapter token_optimization > clone config.yaml > baked defaults (df#14) ---
# Transition (clone config.yaml committed): no-op, clone file wins byte-identically.
# Post-cleanup: materializes baked←adapter config at the clone path all readers expect,
# git-excluded so it can never be committed back. Fail-open: never kills the run.
PYTHONPATH=/opt/dark-factory/scripts python3 -m factory_core.effective_config \
  --clone-dir "$CLONE_DIR" --materialize || true

# --- Apply config.yaml policy knobs post-clone (env overrides logged when active) ---
_entrypoint_cfg_apply
```
with:

```bash
# --- Effective config: adapter token_optimization > clone config.yaml > baked defaults (df#14) ---
# Transition (clone config.yaml committed): the clone file is left in place byte-identically
# and run-config.{json,env} are that file alone. Post-cleanup: materializes baked←adapter
# config at the clone path the unmigrated readers expect, git-excluded so it can never be
# committed back, and the same tree as $ARTIFACTS_DIR/run-config.{json,env} (#180).
# Fail-open: never kills the run.
PYTHONPATH=/opt/dark-factory/scripts python3 -m factory_core.effective_config \
  --clone-dir "$CLONE_DIR" --materialize --out-dir "$ARTIFACTS_DIR" || true
# Archon gives every bash node its own $ARTIFACTS_DIR (the workflow run's directory, not this
# one), so budget_gate.sh and architecture_slice.py find run-config.json through this export.
export FACTORY_RUN_CONFIG_JSON="$ARTIFACTS_DIR/run-config.json"

# --- Apply config.yaml policy knobs post-clone (env/bootstrap values kept and logged) ---
source /opt/dark-factory/scripts/run_config_env.sh
apply_run_config_env "$ARTIFACTS_DIR/run-config.env" entrypoint \
  || echo "WARNING: run-config.env missing or empty post-clone — keeping bootstrap defaults" >&2
```

Notes on behaviour:
- **Fail-open.** As before, a missing artifact only warns, and the bootstrap defaults at
  `entrypoint.sh:33-38` stay.
- **Same values in practice.** Those defaults always set all five variables, and
  `apply_run_config_env … entrypoint` (like `_epcfg`) never overwrites a set variable. So
  the effective values are unchanged. An equal config value is re-exported, as `_epcfg` did.
- **Placement.** The block sits after the `ENTRYPOINT_SOURCE_ONLY` guard
  (`entrypoint.sh:784`), so tests that source `entrypoint.sh` never reach it.

- [ ] **Step 5: Remove the two `@pytest.mark.xfail(strict=True, reason="#180: green after Task 6")`
markers from `tests/test_effective_config.py`** (added in Task 2 Step 2), then run the tests:

```bash
grep -c 'green after Task 6' tests/test_effective_config.py   # 0
python -m pytest tests/test_effective_config.py tests/test_entrypoint_undefined_functions.py -q 2>&1 | tail -1
bash tests/test_entrypoint_current_run.sh 2>&1 | tail -1
bash tests/test_entrypoint_session_window.sh 2>&1 | tail -1
fn() { sed -n '/^_compute_side_effect_level() {/,/^}/p'; }
diff <(git show origin/main:entrypoint.sh | fn) <(fn < entrypoint.sh) && echo "no change inside _compute_side_effect_level"
```
Expected:
- the pytest line reports all passed;
- `test_entrypoint_current_run.sh` prints `Results: 12 passed, 0 failed` (unmodified, per
  safety invariant 2);
- `test_entrypoint_session_window.sh` passes;
- the last command prints `no change inside _compute_side_effect_level`.

- [ ] **Step 6: Commit**

```bash
git add entrypoint.sh tests/test_effective_config.py
git commit -m "refactor(entrypoint): run-config.env replaces _entrypoint_cfg_apply; export FACTORY_RUN_CONFIG_JSON (#180)"
```

---

## Task 7: `budget_gate.sh` — read `run-config.json`

**Files:** Modify `tests/test_budget_gate.sh`, `scripts/budget_gate.sh`

- [ ] **Step 1: Make the fixture write `run-config.json`, as a real run does.** In
`_make_case`, after the `CFG` heredoc terminator and before the function's closing `}`,
add:

```bash
  # The run-config.json entrypoint.sh materializes from the same resolve() (#180).
  _yaml_to_run_config "${dir}/.claude/skills/refinement/config.yaml" "${dir}/run-config.json"
```
and add this helper directly after `_make_case`'s closing `}`:

```bash

# $1 = YAML config, $2 = run-config.json to write (same tree, JSON-encoded)
_yaml_to_run_config() {
  python3 -c 'import json, sys, yaml; json.dump(yaml.safe_load(open(sys.argv[1])), open(sys.argv[2], "w"))' "$1" "$2"
}
```
In `_run_gate`, change `env "$@" ARTIFACTS_DIR=…` to
`env -u FACTORY_RUN_CONFIG_JSON "$@" ARTIFACTS_DIR=…`. Without that, a run container's
real export would leak into the test.

- [ ] **Step 2: Add cases 6-9** directly before `# --- Case 5: usage errors`:

```bash
# --- Case 6: run-config.json missing — observe even though the clone-side YAML says enforce
CASE6="${WORK}/case6"
_make_case "$CASE6" true true 30000
rm "${CASE6}/run-config.json"
RC=$(_run_gate "$CASE6" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=)
[ "$RC" = "0" ] || { echo "FAIL case6 exit code: $RC"; cat "${CASE6}/stderr.log"; exit 1; }
[ ! -s "${CASE6}/token-opt-caps.env" ] || { echo "FAIL case6 expected observe (no run-config.json)"; exit 1; }
grep -q "run-config.json not found; observe mode" "${CASE6}/stderr.log" || { echo "FAIL case6 expected a not-found warning"; cat "${CASE6}/stderr.log"; exit 1; }

# --- Case 7: unparseable run-config.json — observe ------------------------
CASE7="${WORK}/case7"
_make_case "$CASE7" true true 30000
printf '{not json' > "${CASE7}/run-config.json"
RC=$(_run_gate "$CASE7" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=)
[ "$RC" = "0" ] || { echo "FAIL case7 exit code: $RC"; cat "${CASE7}/stderr.log"; exit 1; }
[ ! -s "${CASE7}/token-opt-caps.env" ] || { echo "FAIL case7 expected observe (unparseable run-config.json)"; exit 1; }

# --- Case 8: FACTORY_RUN_CONFIG_JSON (entrypoint.sh's export) beats $ARTIFACTS_DIR ---
CASE8="${WORK}/case8"
_make_case "$CASE8" true false 30000            # $ARTIFACTS_DIR/run-config.json says observe
mkdir -p "${CASE8}/entrypoint-artifacts"
cat > "${CASE8}/enforce.yaml" <<'CFG'
token_optimization:
  enforce_budgets: true
  budgets:
    refine: 30000
  enforce:
    refine: true
CFG
_yaml_to_run_config "${CASE8}/enforce.yaml" "${CASE8}/entrypoint-artifacts/run-config.json"
RC=$(_run_gate "$CASE8" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS= FACTORY_RUN_CONFIG_JSON="${CASE8}/entrypoint-artifacts/run-config.json")
[ "$RC" = "0" ] || { echo "FAIL case8 exit code: $RC"; cat "${CASE8}/stderr.log"; exit 1; }
[ -s "${CASE8}/token-opt-caps.env" ] || { echo "FAIL case8 expected enforce mode from FACTORY_RUN_CONFIG_JSON"; cat "${CASE8}/stderr.log"; exit 1; }

# --- Case 9: kill-switch is kill-only — env "true" cannot force enforce on ----
CASE9="${WORK}/case9"
_make_case "$CASE9" true false 30000
RC=$(_run_gate "$CASE9" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=true)
[ "$RC" = "0" ] || { echo "FAIL case9 exit code: $RC"; cat "${CASE9}/stderr.log"; exit 1; }
[ ! -s "${CASE9}/token-opt-caps.env" ] || { echo "FAIL case9 kill-switch must never force enforce on"; exit 1; }
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
bash tests/test_budget_gate.sh; echo "rc=$?"
```
Expected: `FAIL case6 expected observe (no run-config.json)`, then `rc=1`. The gate still
reads the clone-side YAML.

- [ ] **Step 4: Edit `scripts/budget_gate.sh`.** In the header comment, replace
`# logic that used to be duplicated inline per scenario (#183).` + `#` +
`# Usage: budget_gate.sh <scenario>` +
`# Env:   ARTIFACTS_DIR (required) — same contract as context_budget.py/budget_enforce.py` with:

```bash
# logic that used to be duplicated inline per scenario (#183). Mode and budget come from
# the run's materialized run-config.json (#180) — the same tree entrypoint.sh writes to the
# clone-side config.yaml that budget_enforce.py still reads via --config.
#
# Usage: budget_gate.sh <scenario>
# Env:   ARTIFACTS_DIR (required) — same contract as context_budget.py/budget_enforce.py
#        FACTORY_RUN_CONFIG_JSON (optional, default $ARTIFACTS_DIR/run-config.json) —
#                   exported by entrypoint.sh: archon gives bash nodes their own
#                   ARTIFACTS_DIR, so in a run this is the path that resolves. Missing or
#                   unparseable → observe mode
```
Then replace the block from `_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"` through the
`read -r _EB _ES _BUD < <(python3 -c "…" "$SCENARIO" 2>/dev/null || echo "false false 30000")`
statement with:

```bash
_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
_RUN_CFG="${FACTORY_RUN_CONFIG_JSON:-$ARTIFACTS_DIR/run-config.json}"
[ -f "$_RUN_CFG" ] || echo "budget_gate.sh: WARNING — ${_RUN_CFG} not found; observe mode" >&2

# Single parse — avoids mode/budget desync if config is partially malformed. Same
# defaults as the #183 python reader: enforce_budgets/enforce.<s> false, budget
# budgets.<s> → default_budget_tokens → 30000. Scenario is a jq --arg, never interpolated.
read -r _EB _ES _BUD < <(jq -r --arg s "$SCENARIO" '
  (.token_optimization // {}) as $to
  | [ ($to.enforce_budgets // false | tostring | ascii_downcase),
      (($to.enforce // {})[$s] // false | tostring | ascii_downcase),
      (($to.budgets // {})[$s] // $to.default_budget_tokens // 30000 | tostring) ]
  | join(" ")' "$_RUN_CFG" 2>/dev/null || echo "false false 30000")
```
Keep everything else the same:
- `_CLONE`/`_CFG` stay, and `--config "$_CFG"` is still passed to `budget_enforce.py`,
  which is not migrated;
- the mode derivation, the kill-switch `case`, and the `budget_enforce.py` call are
  unchanged.

During planning, the `jq` expression was checked against the old Python reader on
`config/config.yaml` for `refine`, `implement`, `code-review` and an unlisted scenario.
The output was identical: `true true 30000`, `true false 30000`, `true true 22000` and
`true false 30000`. A budget of `0` stays `0`, because jq's `//` treats only null and
false as missing.

- [ ] **Step 5: Run it and confirm it passes**

```bash
bash tests/test_budget_gate.sh; echo "rc=$?"
```
Expected: `PASS`, then `rc=0`.

- [ ] **Step 5a: Re-cite the moved kill-switch in `bench/variants.example.yaml:7`.** That
comment cites `scripts/budget_gate.sh:50-51`. After this task the kill-switch is the
`_EENV=` line plus the `case "${_EENV,,}"` line (56-57 in the prototype; check with
`grep -n '_EENV' scripts/budget_gate.sh`). Change only the line numbers in that comment.

- [ ] **Step 6: Commit**

```bash
git add scripts/budget_gate.sh tests/test_budget_gate.sh bench/variants.example.yaml
git commit -m "refactor(budget-gate): read mode/budget from run-config.json (#180)"
```

---

## Task 8: `architecture_slice.py` — `_load_config` onto `load_run_config`

**Files:** Modify `tests/test_architecture_slice.py`, `scripts/architecture_slice.py`

- [ ] **Step 1: Write the tests.** Add `import json` after `import sys` in the imports.
Then append:

```python
# ── #180: _load_config reads the materialized run-config.json ─────────────────

def _clone_copy(tmp_path, keywords):
    cfg_dir = tmp_path / ".claude" / "skills" / "refinement"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text(f'dispatch_ceiling:\n  keywords: "{keywords}"\n',
                                         encoding="utf-8")
    return str(tmp_path)


def test_load_config_prefers_materialized_run_config_json(tmp_path, monkeypatch):
    clone_dir = _clone_copy(tmp_path, "clone-only")
    rc = tmp_path / "run-config.json"
    rc.write_text(json.dumps({"dispatch_ceiling": {"keywords": "from-run-config"}}),
                  encoding="utf-8")
    monkeypatch.setenv("FACTORY_RUN_CONFIG_JSON", str(rc))
    assert aslice._get_safety_keywords(aslice._load_config(clone_dir)) == "from-run-config"


def test_load_config_without_run_config_uses_committed_clone_copy_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_RUN_CONFIG_JSON", raising=False)
    clone_dir = _clone_copy(tmp_path, "clone-only")
    assert aslice._load_config(clone_dir) == {"dispatch_ceiling": {"keywords": "clone-only"}}


def test_load_config_unreadable_run_config_falls_back_to_resolver(tmp_path, monkeypatch):
    clone_dir = _clone_copy(tmp_path, "clone-only")
    rc = tmp_path / "run-config.json"
    rc.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("FACTORY_RUN_CONFIG_JSON", str(rc))
    assert aslice._get_safety_keywords(aslice._load_config(clone_dir)) == "clone-only"
```
(These write their own clone copy rather than calling `make_config()`. That helper's
`textwrap.dedent` + multi-line f-string emits YAML that does not parse when there is more
than one path. That is a pre-existing test-helper defect. Do not fix it here: it is out
of scope.)

- [ ] **Step 2: Run them and confirm they fail**

```bash
python -m pytest tests/test_architecture_slice.py -q -k load_config 2>&1 | tail -3
```
Expected: `test_load_config_prefers_materialized_run_config_json` fails with
`AssertionError: assert 'clone-only' == 'from-run-config'`. The other two already pass,
because today's reader also prefers the clone copy.

- [ ] **Step 3: Edit `scripts/architecture_slice.py`.** Delete the `_CONFIG_PATHS = [ … ]`
list (4 lines plus the two blank lines after it). Replace the whole `_load_config` function with:

```python
def _load_config(clone_dir: str | None) -> dict:
    """Run config (#180): entrypoint.sh's materialized run-config.json
    ($FACTORY_RUN_CONFIG_JSON), else effective_config's run-mode resolver — a committed
    clone copy alone, else baked ← adapter token_optimization. {} on any failure.
    When FACTORY_RUN_CONFIG_JSON is set it wins over clone_dir (a run has one clone)."""
    try:
        from factory_core import effective_config
        return effective_config.load_run_config(clone_dir)
    except Exception:
        return {}
```
The import stays lazy, as today's `import yaml` was. `architecture_slice.py` already
imports `factory_core.adapter_defaults` at module level, so the package is on the path.

- [ ] **Step 4: Run and confirm they pass**

```bash
python -m pytest tests/test_architecture_slice.py tests/test_context_budget.py tests/test_context_pack.py -q 2>&1 | tail -1
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/architecture_slice.py tests/test_architecture_slice.py
git commit -m "refactor(arch-slice): _load_config reads run-config.json via effective_config (#180)"
```

---

## Task 9: `epic_autopilot.py` — collapse the `yq` fallback (behaviour unchanged)

**Files:** Modify `tests/test_epic_autopilot.py`, `scripts/factory_core/epic_autopilot.py`

- [ ] **Step 1: Write the characterization tests** (spec Requirement 5, cases a/b/c). Append:

```python
# ── #180: _load_exclude_paths fallback characterization (behavior fix is #205) ──

def _write_exclude_cfg(path, paths):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("epic_autopilot:\n  hard_exclude_paths:\n"
                    + "".join(f'    - "{p}"\n' for p in paths), encoding="utf-8")


def _adapter_unavailable(*_a, **_k):
    raise RuntimeError("adapter unavailable")


def _no_yq(*_a, **_k):
    raise AssertionError("the yq subprocess fallback is gone (#180)")


def test_exclude_paths_adapter_list_wins(tmp_path, monkeypatch):
    """(a) adapter present -> adapter list (today's shadowing; #205 owns the fix)."""
    monkeypatch.setattr("factory_core.adapter.get", lambda _d, _k: ["adapter/only/"])
    baked = tmp_path / "baked.yaml"
    _write_exclude_cfg(baked, ["baked/"])
    monkeypatch.setattr(ap, "_CONFIG_PATHS", [str(tmp_path / "missing.yaml"), str(baked)])
    assert ap._load_exclude_paths(str(tmp_path)) == ["adapter/only/"]


def test_exclude_paths_fallback_ignores_clone_side_config(tmp_path, monkeypatch):
    """(b) adapter.get raising -> the baked list, even with clone-side copies that differ:
    the scheduler-side clone-side entry (3rd) never beats baked (2nd), and the clone_dir
    argument is never read for config."""
    monkeypatch.setattr("factory_core.adapter.get", _adapter_unavailable)
    monkeypatch.setattr(ap.subprocess, "run", _no_yq)
    baked = tmp_path / "baked.yaml"
    _write_exclude_cfg(baked, ["baked/"])
    clone_side = tmp_path / "project" / ".claude" / "skills" / "refinement" / "config.yaml"
    _write_exclude_cfg(clone_side, ["clone-side/"])
    monkeypatch.setattr(ap, "_CONFIG_PATHS", [str(tmp_path / "project-config.yaml"),
                                              str(baked), str(clone_side),
                                              str(tmp_path / "legacy.yaml")])
    clone = tmp_path / "clone"
    _write_exclude_cfg(clone / ".claude" / "skills" / "refinement" / "config.yaml", ["clone/"])
    assert ap._load_exclude_paths(str(clone)) == ["baked/"]


def test_exclude_paths_fallback_first_nonempty_file_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("factory_core.adapter.get", _adapter_unavailable)
    project = tmp_path / "project-config.yaml"
    project.write_text("epic_autopilot:\n  hard_exclude_paths: []\n", encoding="utf-8")
    baked = tmp_path / "baked.yaml"
    _write_exclude_cfg(baked, ["baked/"])
    monkeypatch.setattr(ap, "_CONFIG_PATHS", [str(project), str(baked)])
    assert ap._load_exclude_paths(None) == ["baked/"]
    _write_exclude_cfg(project, ["project/"])
    assert ap._load_exclude_paths(None) == ["project/"]


def test_exclude_paths_no_config_falls_back_to_default(tmp_path, monkeypatch):
    """(c) no config file -> _DEFAULT_EXCLUDE."""
    monkeypatch.setattr("factory_core.adapter.get", _adapter_unavailable)
    monkeypatch.setattr(ap, "_CONFIG_PATHS", [str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert ap._load_exclude_paths(None) == list(ap._DEFAULT_EXCLUDE)
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
python -m pytest tests/test_epic_autopilot.py -q -k exclude_paths 2>&1 | tail -3
```
Expected: `test_exclude_paths_fallback_ignores_clone_side_config` fails with
`AssertionError: assert ['dark-factor…', …] == ['baked/']`. The old loop swallows the
patched `subprocess.run` error and returns `_DEFAULT_EXCLUDE`. The other three pass
already, because they pin today's behaviour.

- [ ] **Step 3: Edit `_load_exclude_paths`.** Change its docstring to
`"""Read hard_exclude_paths from adapter, then config.yaml; fall back to defaults."""`.
Replace from `    # Legacy: yq from config.yaml` through the function's final
`    return list(_DEFAULT_EXCLUDE)` with:

```python
    # Legacy: the first _CONFIG_PATHS file yielding a non-empty list, no merge (#180: was a
    # yq subprocess per file). Never a clone dir and never effective_config.resolve(): its
    # list-replacing deep-merge would let a target's clone-side copy replace this list.
    try:
        from .effective_config import first_match_list
        paths = first_match_list(_CONFIG_PATHS, "epic_autopilot.hard_exclude_paths")
    except Exception:
        paths = []
    if paths:
        return paths
    return list(_DEFAULT_EXCLUDE)
```
`_CONFIG_PATHS` (`epic_autopilot.py:320-323`), the adapter-first block and
`_hard_exclude_paths()` are unchanged. `subprocess` is still imported and used by
`_gh_json`.

- [ ] **Step 4: Run and confirm they pass**

```bash
python -m pytest tests/test_epic_autopilot.py tests/test_adapter.py -q 2>&1 | tail -1
grep -n 'yq' scripts/factory_core/epic_autopilot.py || echo "no yq"
```
Expected: all passed, then `no yq`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/epic_autopilot.py tests/test_epic_autopilot.py
git commit -m "refactor(autopilot): hard_exclude_paths fallback via effective_config.first_match_list (#180)"
```

---

## Task 10: Full-suite checkpoint

**Files:** none

- [ ] **Step 1: Run everything code-side**

```bash
python -m pytest tests/ -q 2>&1 | tail -1
for t in test_run_config_env test_config_deletion test_budget_gate test_epic_autopilot_config \
         test_entrypoint_current_run test_entrypoint_session_window test_budget_context; do
  bash tests/$t.sh >/dev/null 2>&1; echo "$t rc=$?"; done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```
Expected: pytest all passed (2572 on the `f457c2c` prototype), every listed shell test
`rc=0`, and both DAG checks pass. The workflow is unchanged.

- [ ] **Step 2: Out-of-scope check** (two-dot):

```bash
git diff --stat origin/main HEAD
```
Expected: only the files in the File Structure table (docs come in Task 11). Nothing
under `deploy/`, `.claude/`, `workflows/`, `commands/`, `config/`, or
`.github/workflows/publish.yml`.

---

## Task 11: Docs — retire the old names, re-map line citations

**Files:** Modify `docs/configuration.md`, `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md`, `docs/dark-factory-token-optimization.md`, `docs/triage-labels.md`

`scripts/docs_refs_check.py` checks only that a cited line exists. Deleting about 50 lines
from `scheduler.sh` would shift roughly 146 `scheduler.sh` citations, and about 79
`entrypoint.sh` ones. Re-map them mechanically first, then fix the prose by hand.

- [ ] **Step 1: Save the one-off re-map script to `/tmp/remap_doc_refs.py`** (never committed):

```python
"""One-off for #180 (never committed): re-map `file:N` citations in the reference docs
after code lines moved. Run from the repo root: python3 /tmp/remap_doc_refs.py <base-ref>
Prints MANUAL for a citation whose line(s) were rewritten (fix by hand) and REVIEW for a
range that now spans a changed hunk (re-mapped, but check the prose)."""
import difflib, re, subprocess, sys

BASE = sys.argv[1]
FILES = ["scheduler.sh", "entrypoint.sh", "scripts/budget_gate.sh",
         "scripts/architecture_slice.py", "scripts/factory_core/epic_autopilot.py",
         "scripts/factory_core/effective_config.py"]
DOCS = ["docs/run-lifecycle.md", "docs/scheduler.md", "docs/features.md",
        "docs/configuration.md", "docs/dark-factory-token-optimization.md"]
SPAN = re.compile(r"`([^`\n]+)`")
REF = re.compile(r"^(?P<path>[A-Za-z0-9_./-]*?)(?::(?P<spec>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?$")


def line_map(path):
    old = subprocess.run(["git", "show", f"{BASE}:{path}"], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    new = open(path, encoding="utf-8").read().splitlines()
    m = {}
    for tag, i1, i2, j1, _ in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                m[i1 + k + 1] = j1 + k + 1
    return m


MAPS = {f: line_map(f) for f in FILES}
counts = {"MANUAL": 0, "REVIEW": 0}


def owner(path):
    return next((f for f in FILES if path == f or f.endswith("/" + path)), None)


for doc in DOCS:
    lines = open(doc, encoding="utf-8").read().split("\n")
    for n, line in enumerate(lines):
        state = {"file": None}

        def sub(mo):
            span = mo.group(1)
            r = REF.match(span)
            if not r:
                return mo.group(0)
            path, spec = r.group("path"), r.group("spec")
            if path:
                if not (spec or "." in path or "/" in path):
                    return mo.group(0)  # a symbol name such as `read_config`
                state["file"] = owner(path)  # a bare `:N` after this span belongs to it
            f = state["file"]
            if not spec or not f:
                return mo.group(0)
            out = []
            for part in spec.split(","):
                ends = [int(x) for x in part.split("-")]
                mapped = [MAPS[f].get(e) for e in ends]
                if None in mapped:
                    print(f"MANUAL {doc}:{n + 1}: `{span}` ({f} line(s) {part} rewritten)")
                    counts["MANUAL"] += 1
                    return mo.group(0)
                if len(ends) == 2 and mapped[1] - mapped[0] != ends[1] - ends[0]:
                    print(f"REVIEW {doc}:{n + 1}: `{span}` ({f} {part} -> {mapped[0]}-{mapped[1]})")
                    counts["REVIEW"] += 1
                out.append("-".join(str(x) for x in mapped))
            return f"`{path}:{','.join(out)}`"

        lines[n] = SPAN.sub(sub, line)
    open(doc, "w", encoding="utf-8").write("\n".join(lines))
print(f"remap done: {counts['MANUAL']} MANUAL, {counts['REVIEW']} REVIEW")
```

- [ ] **Step 2: Run it**

```bash
python3 /tmp/remap_doc_refs.py origin/main
```
Expected: one `MANUAL` line per citation whose code line was rewritten, about 39 against
the `f457c2c` prototype. It also prints one `REVIEW` (`docs/scheduler.md` step 5,
`:45-101`) and `remap done: …`. Every other citation now points at its moved line.

- [ ] **Step 3: Fix each `MANUAL` citation with this rule table.** The last column
gives the line numbers measured on the `f457c2c` prototype, which is the exact code of
Tasks 3-9. If your base differs, re-derive them with the `grep -n` shown:

| Old citation (on `f457c2c`) | Now cite | Find it with | Prototype lines |
|---|---|---|---|
| `scheduler.sh:27-40`, `:27-31` (`CONFIG_YAML_PATHS`/`resolve_config_yaml`) | `scripts/factory_core/effective_config.py` `SCHEDULER_CANDIDATES` | `grep -n '^SCHEDULER_CANDIDATES' -A4 scripts/factory_core/effective_config.py` | `effective_config.py:33-37` |
| `scheduler.sh:45-101`, `:49-64`, `:55-60` (`read_config`/`_set_cfg`/env-wins) | `scheduler.sh` `read_config` and `scripts/run_config_env.sh` | `grep -n '^read_config() {' -A9 scheduler.sh`; `grep -n 'elif \[ -n "\${!var+x}" \]' -A6 scripts/run_config_env.sh` | `scheduler.sh:35-42`; `run_config_env.sh:47-53` |
| `scheduler.sh:66-98` (the knob list) | `ENV_WHITELIST` | `grep -n '^ENV_WHITELIST\|SESSION_WINDOW_FALLBACK_MINUTES' scripts/factory_core/effective_config.py` | `effective_config.py:43-81` |
| `scheduler.sh:80`, `:92-93`, `:95-98` (single `_set_cfg` rows) | the matching `ENV_WHITELIST` row(s) | `grep -n '"ABOVE_CEILING_KEYWORDS"\|"MAIN_RED_AUTOFIX_MODEL"\|"MAIN_RED_AUTOFIX_MAX_ATTEMPTS"\|"TOKEN_OPTIMIZATION_' scripts/factory_core/effective_config.py` | `:58`; `:70-71`; `:73-76` |
| `entrypoint.sh:43`, `:56-65` (`_epcfg`) | `scripts/run_config_env.sh` entrypoint branch | `grep -n 'if \[ "\$mode" = entrypoint \]' -A7 scripts/run_config_env.sh` | `run_config_env.sh:40-46` |
| `entrypoint.sh:34,69` (`CONFLICT_RESOLUTION_AI_TIER` set) | the bootstrap line in `entrypoint.sh` + the `CONFLICT_RESOLUTION_AI_TIER` whitelist row | `grep -n '^CONFLICT_RESOLUTION_AI_TIER=' entrypoint.sh`; `grep -n '"CONFLICT_RESOLUTION_AI_TIER"' scripts/factory_core/effective_config.py` | `entrypoint.sh:35`; `effective_config.py:77` |
| `entrypoint.sh:835`, `:838` (materialize/apply call) | the new call lines | `grep -n -- '--materialize --out-dir\|^apply_run_config_env' entrypoint.sh` | `entrypoint.sh:805`; `:812` |
| `effective_config.py:95`, `:95-116`, `:95-125` (`materialize`), `:130` (`--clone-dir` default) | new `materialize` range; drop `:130` (see Step 4, `CLONE_DIR` row) | `grep -n '^def materialize\|^def _write_run_artifacts_fail_open' scripts/factory_core/effective_config.py` | `effective_config.py:285-318` |
| `epic_autopilot.py:409-428` (`_load_exclude_paths`) | its new range | `grep -n '^def _load_exclude_paths' -A25 scripts/factory_core/epic_autopilot.py` | `epic_autopilot.py:409-429` |
| `scripts/budget_gate.sh:44`, `:45`, `:46` (the python reader lines) | the jq lines for `enforce_budgets` / `enforce[$s]` / `budgets[$s]` | `grep -n 'enforce_budgets // false\|(\$to.enforce // {})\|(\$to.budgets // {})' scripts/budget_gate.sh` | `budget_gate.sh:50`; `:51`; `:52` |

- [ ] **Step 4: Rewrite the prose that the change makes false.** Locations are given as
"`<doc>` near `<anchor>`" (line numbers on `f457c2c`). Make each listed change.

**`docs/scheduler.md`**
- Step 5 table row (`| 5 | Config resolution |`): replace the "What happens" cell with:
  `` `read_config` runs `factory_core.effective_config` in scheduler mode: the first existing file of `/workspace/project/config/config.yaml`, `/opt/dark-factory/config/config.yaml`, `/opt/refinement-skills/config.yaml` (`SCHEDULER_CANDIDATES`), whole-file, no merge, never a clone, written to `$SCHEDULER_STATE_DIR/run-config.{json,env}`. No file found, or a missing or empty `run-config.env`, is fatal (`FATAL: cannot read config.yaml`, exit 1). Each knob is then set by `apply_run_config_env` (below) ``
  The source cell becomes `` `scheduler.sh:35-42`, `scripts/factory_core/effective_config.py`, `:1393` ``
  (prototype lines; the last is the `read_config` call, `grep -n '^read_config$' scheduler.sh`).
- Replace the section `### \`_set_cfg\`: config value versus environment` (heading through
  the paragraph ending "…and defaults belong to [configuration.md](configuration.md).") with:

  ```markdown
  ### `apply_run_config_env`: config value versus environment

  `read_config` (`scheduler.sh:35-42`) has `factory_core.effective_config`
  write the resolved file's whitelisted knobs to `$SCHEDULER_STATE_DIR/run-config.env`,
  one `VAR=value` line each (a bare `VAR` for a missing or null key). Then
  `apply_run_config_env <file> scheduler` (`scripts/run_config_env.sh`) walks the file. It
  parses the file and never `source`s it:

  - If `VAR` is already set in the environment, **even to an empty string**, the
    environment wins. When it differs from the config value the scheduler logs
    `[config] VAR=<env> (env override; config has '<cfg>')`.
  - Otherwise `VAR` is exported with the config value. A missing or null key becomes an
    empty string.

  The knobs read this way are the `scheduler` rows of `ENV_WHITELIST`
  (`scripts/factory_core/effective_config.py:43-81`): poll interval, retry limit,
  rate-limit floor, factory and refine WIP limits, main-red recheck and autofix settings,
  blocked rescue, `direct-to-pr` label and grace minutes, conflict resolution, dispatch
  ceiling, epic autopilot, and four `token_optimization.*.enabled` flags. Adding a
  bash-consumed knob is one row there. Their meanings and defaults belong to
  [configuration.md](configuration.md).
  ```
  (The line numbers are from the prototype. Re-derive them with the Step 3 greps if your base moved.)
- In the paragraph after it, change `` `_set_cfg` exports into the scheduler's own process `` to
  `` `read_config` exports into the scheduler's own process ``.
- Near "Values the scheduler takes from `config.yaml` via `_set_cfg`" (≈:321): `via _set_cfg` → `via read_config`.
- Near "Mounted checkout, `.archon/.env`, optional target `config/config.yaml`" (≈:735): cite
  `SCHEDULER_CANDIDATES` (Step 3 row 1) instead of `scheduler.sh:27-31`. In the next row,
  change `` `_set_cfg` `` to `` `read_config` (`apply_run_config_env`) ``.
- Gotcha 23 (≈:926): `` `_set_cfg` treats a variable that is set but empty as an override (`:55`) `` →
  `` `apply_run_config_env` treats a variable that is set but empty as an override (`scripts/run_config_env.sh:47`) ``.

**`docs/configuration.md`**
- Process table, Scheduler row (≈:41): append `Resolved by `factory_core.effective_config`'s scheduler mode into `$SCHEDULER_STATE_DIR/run-config.{json,env}`.`; source cell `scripts/factory_core/effective_config.py`, `scheduler.sh` `read_config`.
- Process table, Run container row (≈:42): append `The same resolution is also written to `$ARTIFACTS_DIR/run-config.{json,env}`; `budget_gate.sh` and `architecture_slice.py` read `run-config.json` through `FACTORY_RUN_CONFIG_JSON`, the entrypoint knobs come from `run-config.env`.`
- ASCII diagram (≈:49): replace `v  _set_cfg: yq -> export VAR` with `v  run-config.env -> apply_run_config_env`, padding with spaces so the right-hand `|` column stays aligned.
- Layer row 1 (≈:63): `For the 33 config keys exported by `_set_cfg`` → `For the 33 config keys the scheduler takes from `run-config.env``; cite `scripts/run_config_env.sh` (Step 3 row 2) for the set-at-all rule.
- Layer row 2 (≈:64): `effective_config writes nothing and that file wins` → `effective_config writes nothing into the clone, `run-config.json` is that file alone, and it wins`; re-cite the `materialize` range.
- Layer 1 intro (≈:82-83): replace the sentence citing `_set_cfg` and the key list with `The scheduler's env projection is `apply_run_config_env` (`scripts/run_config_env.sh`); the list of exported keys is `ENV_WHITELIST` (`scripts/factory_core/effective_config.py:43-81`).`
- `### scheduler` intro (≈:97): `(via `_set_cfg`)` → `(via `read_config`)`.
- `conflict_resolution.ai_tier` row (≈:161): re-cite per Step 3 (`entrypoint.sh:34,69` row).
- Epic-autopilot paragraph (≈:189-191): both `_set_cfg` → `read_config`.
- `epic_autopilot.hard_exclude_paths` row (≈:205): `and only then `yq` on config` → `and only then the first config file with a non-empty list (`effective_config.first_match_list`, same file order)`; re-cite the `_load_exclude_paths` range.
- `main_red_autofix.ci_wait_minutes` row (≈:215): `not exported by `_set_cfg` at all` → `not in `ENV_WHITELIST` at all`.
- Paragraph at ≈:251-253: `exported by the scheduler's `_set_cfg` (`scheduler.sh:95-98`)` → `exported by the scheduler's `read_config` (the four `TOKEN_OPTIMIZATION_*_ENABLED` rows of `ENV_WHITELIST`, …)`.
- `token_optimization.*` rows (≈:269-280): re-cite `budget_gate.sh` per Step 3, and change the reader wording from "the clone config" to "`run-config.json`" where a row names `budget_gate.sh` as reader.
- `token_optimization.comments.enabled` row (≈:291): `_set_cfg exports it` → `read_config exports it`.
- Layer 2 rule 2 (≈:338): heading text `**`_set_cfg` (`scheduler.sh:49-64`)**` → `**`read_config` → `apply_run_config_env` (`scripts/run_config_env.sh`)**`; keep the 33-variable list (still accurate).
- ≈:378: `that `_set_cfg` derives from config` → `that `read_config` derives from config`.
- `CLONE_DIR` row (≈:442): delete `effective_config.py:130, ` from the readers list. `effective_config` no longer reads `CLONE_DIR`; `--clone-dir` is explicit.
- Layer 3 rows (≈:492, :494): re-cite `materialize` and `_load_exclude_paths` per Step 3.
- D1 (≈:565): `before `_epcfg` runs, and `_epcfg` (`entrypoint.sh:56-65`)` → `before `apply_run_config_env … entrypoint` runs, and it (`scripts/run_config_env.sh:40-46`)`; re-cite the rest per Step 3.
- D2 (≈:566), D3 (≈:567), D4 (≈:568), D5 (≈:569), and the row at ≈:572: replace `_set_cfg` with `read_config`, and re-cite per Step 3.

**`docs/run-lifecycle.md`**
- Row 15 (≈:125): `factory_core.effective_config --materialize`; fail-open` →
  `` `factory_core.effective_config --materialize --out-dir "$ARTIFACTS_DIR"`: the clone-side `.claude/skills/refinement/config.yaml` (unless the target commits one) plus `$ARTIFACTS_DIR/run-config.{json,env}`; exports `FACTORY_RUN_CONFIG_JSON`; fail-open ``
- Row 16 (≈:126): `838 (`_entrypoint_cfg_apply`, 45-74)` → `812 (`apply_run_config_env`, `scripts/run_config_env.sh`)` (prototype line), and the description →
  `Reads `scheduler.factory_wip_limit`, `conflict_resolution.ai_tier` and the three `scheduler.session_window_*` keys from `$ARTIFACTS_DIR/run-config.env`; a missing file warns and keeps the bootstrap defaults`.
- §8 artifacts table: add the row
  `| `run-config.json`, `run-config.env` | `entrypoint.sh` (`effective_config --materialize --out-dir`), in the entrypoint's own `ARTIFACTS_DIR` | `budget_gate.sh` and `architecture_slice.py` through `FACTORY_RUN_CONFIG_JSON` (archon gives bash nodes a different `ARTIFACTS_DIR`); the entrypoint knobs |`
- Gotcha 14 (≈:1134): re-cite `entrypoint.sh:34,69` per Step 3.

**`docs/features.md`**
- ≈:56: re-cite `scheduler.sh:80` as the `ABOVE_CEILING_KEYWORDS` whitelist row.
- ≈:81 (self-contained fallbacks / effective config): re-cite the materialize call line and `materialize`. Append ` Also writes `run-config.{json,env}` (#180).` to the status cell.

**`docs/dark-factory-token-optimization.md`**
- §"How a run resolves its config" (≈:76): after "…through `factory_core.effective_config`:", add the sentence `The same resolution is also written to `run-config.json`, which `scripts/budget_gate.sh` and `scripts/architecture_slice.py` read through `FACTORY_RUN_CONFIG_JSON` (#180).`
- ≈:144: `(`_set_cfg`)` → `(`read_config`)`.

**`docs/triage-labels.md`**
- ≈:52 (operator note on renaming `direct-to-pr`): `only by `scheduler.sh`'s `_set_cfg`` → `only by `scheduler.sh`'s `read_config``.

- [ ] **Step 5: Verify that no retired name survives and that the checks pass**

```bash
grep -nE '_set_cfg|_epcfg|_entrypoint_cfg_apply|resolve_config_yaml|CONFIG_YAML_PATHS' \
  docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
  docs/dark-factory-token-optimization.md docs/triage-labels.md || echo "clean"
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
python -m pytest tests/test_docs_refs_check.py -q 2>&1 | tail -1
```
Expected: `clean`, then `Docs reference check passed for 4 doc(s).`, then all passed. No
Layer 1 key row changes, because no config key was added.

- [ ] **Step 6: Commit**

```bash
git add docs/configuration.md docs/scheduler.md docs/run-lifecycle.md docs/features.md docs/dark-factory-token-optimization.md docs/triage-labels.md
git commit -m "docs(#180): read_config / run-config.env replace _set_cfg and _entrypoint_cfg_apply; re-map citations"
```

---

## Task 12: Final verification and PR notes

**Files:** none

- [ ] **Step 1: CI parity**

```bash
python -m pytest tests/ -v 2>&1 | tail -1
for t in $(grep -oE 'tests/test_[a-z_]+\.sh' .github/workflows/ci.yml); do bash "$t" >/dev/null 2>&1 || echo "FAIL $t"; done; echo "ci shell tests done"
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
bash tests/test_scheduler.sh 2>&1 | grep -E 'FAIL|Results'
bash tests/test_dispatch_ceiling.sh 2>&1 | grep -E 'FAIL|Results'
```
Expected:
- pytest reports all passed;
- `ci shell tests done` with no `FAIL` lines;
- both DAG checks and the docs check pass;
- the two non-CI scheduler tests fail exactly the Task 0 baseline cases, with the new #180
  assertions passing.

- [ ] **Step 2: Map each acceptance criterion to its test in the PR description**

| Issue / spec item | Shown by |
|---|---|
| `resolve()` returns full merged tree (unchanged) | existing `test_adapter_wins_over_clone_and_baked_deep_merge`; extended clone-wins test |
| One materialized artifact shape from one writer, two call sites | `write_run_artifacts`; `test_scheduler_mode_first_match_never_reads_clone`, `test_cli_run_mode_writes_artifacts_with_out_dir` |
| `read_config` has no per-knob `yq` | `test_scheduler_and_entrypoint_have_no_per_knob_yq_reads` |
| Inline workflow readers gone (via #183) + `budget_gate.sh` off its YAML parse | `tests/test_budget_gate.sh` cases 6-8 |
| Env-override + kill-switch semantics preserved | `tests/test_run_config_env.sh`, `tests/test_config_deletion.sh` T4, `test_budget_gate.sh` cases 4 and 9 |
| Safety invariant 1 | `test_run_config_env_matches_golden_fixture`, `test_run_config_json_equals_clone_side_materialization[self,markethawk]`, `tests/test_budget_gate.sh` |
| Safety invariant 2 | `test_exclude_paths_fallback_ignores_clone_side_config`, `test_scheduler_mode_first_match_never_reads_clone`, `test_no_reader_takes_side_effect_or_blast_radius_from_run_config`, unmodified `test_blast_radius.py` + `test_entrypoint_current_run.sh` |
| Safety invariant 3 | `tests/test_config_deletion.sh` (in CI) |

Also put these in the PR body:
- the `FACTORY_RUN_CONFIG_JSON` rationale;
- the three rendering non-equivalences;
- the corrected dispatch-ceiling statement: L tickets are never parked;
- the stale `Dockerfile` `yq` comment, as a follow-up;
- as a follow-up, the code comments that cite `scheduler.sh:N` / `entrypoint.sh:N` and
  shift with this change. These are in `cost_report.py`, `post_mortem.py`, `side_effect.py`,
  `gate_blast_radius.py` and several tests, and some were already stale on main. Do not
  edit them in this PR, and especially not the trust-boundary files;
- a note that the spec's scoped-out readers still use the clone-side file, so they are a
  follow-up ticket.

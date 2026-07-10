# Plan — Factory Review Guardrails for Claude Skills, Hooks, and Tool Permissions

**Issue:** omniscient/dark-factory#46
**Spec:** [`docs/superpowers/specs/2026-07-10-skill-security-review-guardrails-design.md`](../specs/2026-07-10-skill-security-review-guardrails-design.md)
**Depends on:** omniscient/dark-factory#42 (closed; policy spec at `docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`)

## Goal

Make `.claude/skills/**`, `.claude/settings.json` (+ `.claude/settings.local.json`), `.mcp.json`,
plugin/marketplace config, and `.factory/hooks/**` first-class security-sensitive surfaces in the
factory's existing gates:

1. Path-level visibility/exclusion (`adapter_defaults.py` DEFAULTS + `.factory/adapter.yaml`).
2. A distinct `skill-security` blast-radius trigger category (surgical — whole-file-sensitive
   paths only, never `SKILL.md` by path alone).
3. Content-level detection (broadened `allowed-tools`, new hooks, `context: fork`,
   model/effort overrides, dynamic shell injection) taught to the code-review and conformance
   RUBRIC personas.

No new gate mechanism — this extends `gate_blast_radius.py`, `diff_rank.py`, and the two
existing `RUBRIC.md` personas only, per the spec's Q1/A1.

## Architecture

```
adapter_defaults.py DEFAULTS["safety"]              .factory/adapter.yaml safety:*
  .hard_exclude_paths      ─┐                          .hard_exclude_paths      ─┐  (list-replace
  .critical_diff_paths      ├─ same 7-8 new globs       .critical_diff_paths      ├─  merge, so
  .migration_seed_auth_..  ─┘  added to both            .migration_seed_auth_..  ─┘  both required
        │                                                       │
        ▼                                                       ▼
 gate_blast_radius.py classify_file()             diff_rank.py _safety_signal()
   migration_seed_auth_patterns match               critical_diff_paths match
   → sub-classify "skill-security" vs               → sub-classify "skill_security_path"
     "migration-seed" from pattern source              (checked before "dark-factory" branch)
        │                                                       │
        ▼                                                       ▼
 HUMAN_REQUIRED, TRIGGER: skill-security            "critical" tier, signal "skill_security_path"
 (blast.md → issue comment, verbatim)                (diff-rank ordering/visibility only)

.claude/skills/code-review/RUBRIC.md          .claude/skills/conformance/RUBRIC.md
  + content-level skill-security checks          + OOS carve-out: skill/settings/hooks/plugin/MCP
  + skill-security category                        paths never covered by the doc exemption
  (mirrored to refinement-skills/                 (mirrored to refinement-skills/
   code-review-reviewer-prompt.md,                 conformance-reviewer-prompt.md,
   line-count-identical except title/L2)            byte-identical)
```

## Tech Stack

Python 3 (stdlib `re`, `yaml`), pytest. No new dependencies.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/adapter_defaults.py` | Add skill-security globs to the three `safety.*` lists (universal baseline for every target repo) |
| `.factory/adapter.yaml` | Add the same globs to dark-factory's own three `safety.*` lists (required — list-replace merge semantics, see Task 2) |
| `scripts/gate_blast_radius.py` | New `"skill-security"` trigger category, sub-classified from matched-pattern source text; `trigger_label` precedence gains it |
| `scripts/diff_rank.py` | `_safety_signal()` gains a `"skill_security_path"` branch, checked before the `"dark-factory"` branch |
| `.claude/skills/code-review/RUBRIC.md` | New "Security-Sensitive Surfaces" judging section + `skill-security` category |
| `refinement-skills/code-review-reviewer-prompt.md` | Mirrored edit — `test_rubric_matches_source_except_delabel` requires identical line count/content except lines 1 and 3 |
| `.claude/skills/conformance/RUBRIC.md` | New OOS carve-out sentence under the Documentation exception |
| `refinement-skills/conformance-reviewer-prompt.md` | Mirrored edit — `test_rubric_matches_source_prompt_content` requires byte-identical content |
| `tests/test_adapter.py` | New tests: globs present in `DEFAULTS` and in dark-factory's real `.factory/adapter.yaml`, for all three lists |
| `tests/test_blast_radius.py` | New tests: settings/scripts/hooks → `HUMAN_REQUIRED` + `TRIGGER: skill-security`; `SKILL.md` alone → `PASS`; a non-hermetic fixture against the real `.factory/adapter.yaml` |
| `tests/test_diff_rank.py` | New tests: `_safety_signal()` returns `skill_security_path`; `dark-factory/` paths still return `factory_path` |
| `tests/test_rubric_skill_security.py` | New file: presence assertions that both `RUBRIC.md` files carry the required instruction strings |

---

## Task 1 — `adapter_defaults.py`: add skill-security globs to the three `safety.*` lists

**Files:** `scripts/factory_core/adapter_defaults.py`, `tests/test_adapter.py`

### Step 1.1 — Write failing tests

Append to `tests/test_adapter.py`:

```python
# ── Skill-security safety globs (#46) ──────────────────────────────────────

def test_skill_security_globs_in_defaults_hard_exclude_paths():
    paths = adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]
    assert any(".claude/skills/" in p for p in paths)
    assert any("settings.json" in p for p in paths)
    assert any(".mcp.json" in p for p in paths)
    assert any(".claude/plugins/" in p for p in paths)
    assert any(".claude-plugin/" in p for p in paths)
    assert any(".factory/hooks/" in p for p in paths)


def test_skill_security_globs_in_defaults_critical_diff_paths():
    import re
    patterns = adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]
    for p in patterns:
        re.compile(p)  # every entry must be a valid regex
    joined = "|".join(patterns)
    assert "claude/skills" in joined
    assert "settings.json" in joined
    assert "factory/hooks" in joined
    assert any("SKILL" in p for p in patterns), "SKILL.md must appear (visibility only)"


def test_skill_md_not_in_migration_seed_auth_patterns():
    """SKILL.md must never be a path-level HUMAN_REQUIRED trigger — see spec Q2/A2."""
    patterns = adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]
    assert not any("SKILL" in p for p in patterns)


def test_skill_scripts_and_settings_in_migration_seed_auth_patterns():
    import re
    patterns = [re.compile(p) for p in adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]]
    assert any(p.search(".claude/skills/code-review/scripts/foo.py") for p in patterns)
    assert any(p.search(".claude/settings.json") for p in patterns)
    assert any(p.search(".factory/hooks/validate") for p in patterns)
```

### Step 1.2 — Verify the tests fail

```bash
python -m pytest tests/test_adapter.py -k skill_security -v
```

Expected: 4 failures (globs not present yet).

### Step 1.3 — Implement

Edit `scripts/factory_core/adapter_defaults.py`. Replace:

```python
        "hard_exclude_paths": [
            "dark-factory/", ".archon/", "scheduler.sh", "factory_core/",
            "app/services/trading", "app/tasks/trading.py", "app/core/auth", "app/routers/auth",
        ],
        "dispatch_ceiling_keywords": "migration|migrate|performance|perf|architectur|refactor",
        # Verbatim copy of SAFETY_PATH_PATTERNS patterns from scripts/diff_rank.py
        "critical_diff_paths": [
            r"^alembic/versions/",
            r"^backend/app/routers/auth",
            r"^backend/app/core/auth",
            r"app/services/trading",
            r"app/tasks/trading\.py",
            r"^dark-factory/",
        ],
        "migration_seed_auth_patterns": [
            r"^alembic/versions/", r"^dark-factory/seed/", r"seed.*\.sql$",
            r"^backend/app/routers/auth\.py$",
        ],
```

with:

```python
        "hard_exclude_paths": [
            "dark-factory/", ".archon/", "scheduler.sh", "factory_core/",
            "app/services/trading", "app/tasks/trading.py", "app/core/auth", "app/routers/auth",
            # Claude Skills / settings / hooks / plugin / MCP surface — the
            # self-modifying-factory mechanism itself (#46). Forward-protects
            # epic_autopilot regardless of its enabled flag.
            ".claude/skills/", ".claude/settings.json", ".claude/settings.local.json",
            ".mcp.json", ".claude/plugins/", ".claude-plugin/", ".factory/hooks/",
        ],
        "dispatch_ceiling_keywords": "migration|migrate|performance|perf|architectur|refactor",
        # Verbatim copy of SAFETY_PATH_PATTERNS patterns from scripts/diff_rank.py
        "critical_diff_paths": [
            r"^alembic/versions/",
            r"^backend/app/routers/auth",
            r"^backend/app/core/auth",
            r"app/services/trading",
            r"app/tasks/trading\.py",
            r"^dark-factory/",
            # Claude Skills / settings / hooks / plugin / MCP surface (#46).
            # SKILL.md is visibility-only here — it is deliberately absent from
            # migration_seed_auth_patterns below (spec Q2/A2).
            r"^\.claude/skills/.*/scripts/",
            r"^\.claude/skills/.*/SKILL\.md$",
            r"^\.claude/settings\.json$",
            r"^\.claude/settings\.local\.json$",
            r"^\.mcp\.json$",
            r"^\.claude/plugins/",
            r"^\.claude-plugin/",
            r"^\.factory/hooks/",
        ],
        "migration_seed_auth_patterns": [
            r"^alembic/versions/", r"^dark-factory/seed/", r"seed.*\.sql$",
            r"^backend/app/routers/auth\.py$",
            # Whole-file-sensitive Claude Skills surface (#46) — surgical subset
            # of the critical_diff_paths set above. SKILL.md is intentionally
            # excluded: a path glob can't tell a frontmatter permission change
            # from a prose edit, so SKILL.md content is judged by the
            # code-review/conformance RUBRIC personas instead (spec Q2/A2).
            r"^\.claude/skills/.*/scripts/",
            r"^\.claude/settings\.json$",
            r"^\.claude/settings\.local\.json$",
            r"^\.mcp\.json$",
            r"^\.claude/plugins/",
            r"^\.claude-plugin/",
            r"^\.factory/hooks/",
        ],
```

### Step 1.4 — Verify the tests pass

```bash
python -m pytest tests/test_adapter.py -v
```

Expected: all tests pass, including the pre-existing `test_critical_diff_paths_parity` (it compares
against `diff_rank.SAFETY_PATH_PATTERNS`, which re-exports `DEFAULTS` at import time, so it stays
in sync automatically).

### Step 1.5 — Commit

```bash
git add scripts/factory_core/adapter_defaults.py tests/test_adapter.py
git commit -m "feat(adapter): add skill/settings/hooks/MCP globs to safety defaults (#46)"
```

---

## Task 2 — `.factory/adapter.yaml`: mirror the same globs (list-replace merge requires it)

**Files:** `.factory/adapter.yaml`, `tests/test_adapter.py`

`scripts/factory_core/adapter.py::_deep_merge` replaces list-valued keys wholesale rather than
concatenating (`else: out[k] = copy.deepcopy(v)` for any non-dict value). Dark-factory's own
`.factory/adapter.yaml` already sets its own `hard_exclude_paths`/`critical_diff_paths`/
`migration_seed_auth_patterns`, so a `DEFAULTS`-only change would leave dark-factory's own PRs
(including this one) unprotected. Both locations are required (spec Q4/A4).

### Step 2.1 — Write a failing test

Append to `tests/test_adapter.py`:

```python
def test_dark_factory_own_adapter_yaml_has_skill_security_globs():
    """Guards the A4 merge-semantics gap: .factory/adapter.yaml list-replaces DEFAULTS,
    so it must carry the skill-security globs independently, not just inherit them."""
    import yaml
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((repo_root / ".factory" / "adapter.yaml").read_text())
    for key in ("hard_exclude_paths", "critical_diff_paths", "migration_seed_auth_patterns"):
        joined = "|".join(data["safety"][key])
        assert ".claude/skills" in joined, f"{key} missing .claude/skills glob"
        assert "settings.json" in joined, f"{key} missing settings.json glob"
        assert "factory/hooks" in joined, f"{key} missing .factory/hooks glob"
    assert not any("SKILL" in p for p in data["safety"]["migration_seed_auth_patterns"])
```

### Step 2.2 — Verify it fails

```bash
python -m pytest tests/test_adapter.py -k dark_factory_own_adapter_yaml -v
```

### Step 2.3 — Implement

Edit `.factory/adapter.yaml`. Replace the `safety:` block:

```yaml
safety:
  sensitive_keywords: "token|secret|credential|oauth|gh_token|docker socket|publish"
  # Instance secrets and the image-publish pipeline need a human in the loop.
  hard_exclude_paths:
    - "deploy/instances/"
    - ".github/workflows/publish.yml"
  dispatch_ceiling_keywords: "migration|migrate|performance|perf|architectur|refactor"
  # Highest-blast files first when the diff must be truncated for review.
  critical_diff_paths:
    - "^scheduler\\.sh$"
    - "^entrypoint\\.sh$"
    - "^workflows/"
    - "^run-compose\\.yml$"
    - "^Dockerfile$"
    - "^deploy/"
  migration_seed_auth_patterns:
    - "^deploy/"
    - "^\\.github/workflows/"
  main_red_allowed_paths:
    - "scripts/"
    - "tests/"
    - "workflows/"
    - "commands/"
```

with:

```yaml
safety:
  sensitive_keywords: "token|secret|credential|oauth|gh_token|docker socket|publish"
  # Instance secrets and the image-publish pipeline need a human in the loop.
  # Skill/settings/hooks/plugin/MCP surfaces are the self-modifying-factory
  # mechanism itself (#46) — always human-reviewed, independent of
  # epic_autopilot's enabled flag. Mirrors adapter_defaults.py DEFAULTS
  # because list-valued keys are replaced, not merged, by _deep_merge.
  hard_exclude_paths:
    - "deploy/instances/"
    - ".github/workflows/publish.yml"
    - ".claude/skills/"
    - ".claude/settings.json"
    - ".claude/settings.local.json"
    - ".mcp.json"
    - ".claude/plugins/"
    - ".claude-plugin/"
    - ".factory/hooks/"
  dispatch_ceiling_keywords: "migration|migrate|performance|perf|architectur|refactor"
  # Highest-blast files first when the diff must be truncated for review.
  critical_diff_paths:
    - "^scheduler\\.sh$"
    - "^entrypoint\\.sh$"
    - "^workflows/"
    - "^run-compose\\.yml$"
    - "^Dockerfile$"
    - "^deploy/"
    - "^\\.claude/skills/.*/scripts/"
    - "^\\.claude/skills/.*/SKILL\\.md$"
    - "^\\.claude/settings\\.json$"
    - "^\\.claude/settings\\.local\\.json$"
    - "^\\.mcp\\.json$"
    - "^\\.claude/plugins/"
    - "^\\.claude-plugin/"
    - "^\\.factory/hooks/"
  migration_seed_auth_patterns:
    - "^deploy/"
    - "^\\.github/workflows/"
    - "^\\.claude/skills/.*/scripts/"
    - "^\\.claude/settings\\.json$"
    - "^\\.claude/settings\\.local\\.json$"
    - "^\\.mcp\\.json$"
    - "^\\.claude/plugins/"
    - "^\\.claude-plugin/"
    - "^\\.factory/hooks/"
  main_red_allowed_paths:
    - "scripts/"
    - "tests/"
    - "workflows/"
    - "commands/"
```

### Step 2.4 — Verify it passes

```bash
python -m pytest tests/test_adapter.py -v
```

### Step 2.5 — Commit

```bash
git add .factory/adapter.yaml tests/test_adapter.py
git commit -m "fix(adapter): mirror skill-security globs into dark-factory's own adapter.yaml (#46)"
```

---

## Task 3 — `gate_blast_radius.py`: `skill-security` trigger category

**Files:** `scripts/gate_blast_radius.py`, `tests/test_blast_radius.py`

### Step 3.1 — Write failing tests

Append to `tests/test_blast_radius.py`:

```python
def test_settings_json_triggers_skill_security():
    out = run_script([".claude/settings.json"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_script_triggers_skill_security():
    out = run_script([".claude/skills/code-review/scripts/foo.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_factory_hooks_triggers_skill_security():
    out = run_script([".factory/hooks/validate"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_md_alone_does_not_trigger():
    out = run_script([".claude/skills/code-review/SKILL.md"])
    assert out["STATUS"] == "PASS"


def test_migration_file_trigger_label_still_migration_seed():
    out = run_script(["alembic/versions/abc123_add_col.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "migration-seed"


def test_dark_factory_own_adapter_yaml_protects_skill_security():
    """Non-hermetic: run against this repo's real .factory/adapter.yaml (not the
    MarketHawk-parity default) to guard the A4 merge-semantics gap end to end."""
    import tempfile as _tempfile
    repo_root = Path(SCRIPT).resolve().parents[1]
    with _tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as hf:
        hf.write("")
        hf.flush()
        with _tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cf:
            cf.write(yaml.dump({"blast_radius": {
                "enabled": True, "hotspot_score_floor": 5.0,
                "size_budget_lines": 400, "size_budget_blocks": False,
            }}))
            cf.flush()
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
                 "--hotspots", hf.name, "--config", cf.name, "--clone-dir", str(repo_root)],
                input=".claude/settings.json",
                capture_output=True, text=True,
            )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "TRIGGER: skill-security" in proc.stdout
```

(`Path`, `subprocess`, `sys`, `yaml` are already imported at the top of `tests/test_blast_radius.py`;
this test deliberately does **not** use the `_hermetic_cwd` autouse fixture's adapter-free
assumption — it passes `--clone-dir` explicitly, so the real `.factory/adapter.yaml` is read
regardless of cwd.)

### Step 3.2 — Verify the tests fail

```bash
python -m pytest tests/test_blast_radius.py -v
```

Expected: `test_settings_json_triggers_skill_security`, `test_skill_script_triggers_skill_security`,
`test_factory_hooks_triggers_skill_security`, and `test_dark_factory_own_adapter_yaml_protects_skill_security`
fail with `TRIGGER == "migration-seed"` (the old generic label, still emitted for every match until
Step 3.3 lands the sub-classification); the rest pass already (Task 1/2 already wired the underlying
pattern lists).

### Step 3.3 — Implement

Edit `scripts/gate_blast_radius.py`. Replace:

```python
def classify_file(fpath: str, hotspots: set, clone_dir: str | None = None) -> list:
    """Return list of triggered categories for a single file path."""
    cats = []
    if fpath in hotspots:
        cats.append("hotspot")
    for pat in _migration_seed_auth_patterns(clone_dir):
        if pat.search(fpath):
            cats.append("migration-seed")
            break
    return cats
```

with:

```python
# Sub-classifies a migration_seed_auth_patterns match by matched-pattern source
# text (mirroring diff_rank.py::_safety_signal()'s technique) so a skill/
# settings/hooks/plugin/MCP match is never hidden inside the generic
# "migration-seed" bucket (spec Q3/A3).
_SKILL_SECURITY_TOKENS = (
    "claude/skills", "settings.json", "settings.local.json",
    "mcp.json", "claude/plugins", "claude-plugin", "factory/hooks",
)


def classify_file(fpath: str, hotspots: set, clone_dir: str | None = None) -> list:
    """Return list of triggered categories for a single file path."""
    cats = []
    if fpath in hotspots:
        cats.append("hotspot")
    for pat in _migration_seed_auth_patterns(clone_dir):
        if pat.search(fpath):
            src = pat.pattern
            if any(tok in src for tok in _SKILL_SECURITY_TOKENS):
                cats.append("skill-security")
            else:
                cats.append("migration-seed")
            break
    return cats
```

Then replace the `trigger_label` selection in `main()`:

```python
    trigger_label = "none"
    if hard_trigger:
        cats_all = [c for _, cats in triggered for c in cats]
        trigger_label = "hotspot" if "hotspot" in cats_all else "migration-seed"
    elif size_trigger:
        trigger_label = "size"
```

with:

```python
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
```

### Step 3.4 — Verify the tests pass

```bash
python -m pytest tests/test_blast_radius.py -v
```

### Step 3.5 — Commit

```bash
git add scripts/gate_blast_radius.py tests/test_blast_radius.py
git commit -m "feat(blast-radius): add skill-security trigger category (#46)"
```

---

## Task 4 — `diff_rank.py`: `skill_security_path` safety signal

**Files:** `scripts/diff_rank.py`, `tests/test_diff_rank.py`

### Step 4.1 — Write failing tests

Append to `tests/test_diff_rank.py`:

```python
def test_safety_signal_skill_security_path():
    assert dr._safety_signal(".claude/settings.json") == "skill_security_path"
    assert dr._safety_signal(".claude/settings.local.json") == "skill_security_path"
    assert dr._safety_signal(".claude/skills/code-review/scripts/foo.py") == "skill_security_path"
    assert dr._safety_signal(".claude/skills/code-review/SKILL.md") == "skill_security_path"
    assert dr._safety_signal(".factory/hooks/validate") == "skill_security_path"


def test_safety_signal_dark_factory_path_unaffected():
    """.factory/hooks/ must not collide with the dark-factory/ factory_path signal."""
    assert dr._safety_signal("dark-factory/scheduler.sh") == "factory_path"


def test_classify_file_skill_security_is_critical_tier():
    tier, signals, _ = dr.classify_file(".claude/settings.json", set(), set(), 5.0, total_lines=10)
    assert tier == "critical"
    assert "skill_security_path" in signals
```

### Step 4.2 — Verify the tests fail

```bash
python -m pytest tests/test_diff_rank.py -k skill_security -v
```

Expected: the first two fail (`_safety_signal` returns `"factory_path"` or `""` today for these
paths); `test_classify_file_skill_security_is_critical_tier` fails because `signals` doesn't
contain `"skill_security_path"`.

### Step 4.3 — Implement

Edit `scripts/diff_rank.py`. Replace:

```python
def _safety_signal(path: str, clone_dir: str | None = None) -> str:
    for pat in _safety_path_patterns(clone_dir):
        if pat.search(path):
            src = pat.pattern
            if "alembic" in src:
                return "migration_path"
            if "auth" in src:
                return "auth_path"
            if "trading" in src:
                return "trading_path"
            if "dark-factory" in src:
                return "factory_path"
            return "safety_path"
    return ""
```

with:

```python
# Checked first, before the "dark-factory" branch below, so .factory/hooks/
# (and the rest of the skill/settings/plugin/MCP surface) is never
# mislabeled as a generic factory_path match.
_SKILL_SECURITY_TOKENS = (
    "claude/skills", "settings.json", "settings.local.json",
    "mcp.json", "claude/plugins", "claude-plugin", "factory/hooks",
)


def _safety_signal(path: str, clone_dir: str | None = None) -> str:
    for pat in _safety_path_patterns(clone_dir):
        if pat.search(path):
            src = pat.pattern
            if any(tok in src for tok in _SKILL_SECURITY_TOKENS):
                return "skill_security_path"
            if "alembic" in src:
                return "migration_path"
            if "auth" in src:
                return "auth_path"
            if "trading" in src:
                return "trading_path"
            if "dark-factory" in src:
                return "factory_path"
            return "safety_path"
    return ""
```

### Step 4.4 — Verify the tests pass

```bash
python -m pytest tests/test_diff_rank.py -v
```

### Step 4.5 — Commit

```bash
git add scripts/diff_rank.py tests/test_diff_rank.py
git commit -m "feat(diff-rank): add skill_security_path safety signal (#46)"
```

---

## Task 5 — Code-review RUBRIC: content-level skill-security checks

**Files:** `.claude/skills/code-review/RUBRIC.md`, `refinement-skills/code-review-reviewer-prompt.md`,
`tests/test_rubric_skill_security.py` (new)

`tests/test_code_review_skill_files.py::test_rubric_matches_source_except_delabel` requires the two
files to have an **identical line count**, with every line identical except line 1 (title) and line
3 (opening sentence). Any addition must be made **verbatim-identically** to both files at the same
point.

### Step 5.1 — Write failing tests

Create `tests/test_rubric_skill_security.py`:

```python
"""Presence assertions for the #46 skill-security RUBRIC guidance.

Prose read by subagents isn't unit-testable for behavior, but these guard
against silent deletion of the required instruction strings.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_REVIEW_RUBRIC = REPO_ROOT / ".claude" / "skills" / "code-review" / "RUBRIC.md"
CONFORMANCE_RUBRIC = REPO_ROOT / ".claude" / "skills" / "conformance" / "RUBRIC.md"


def test_code_review_rubric_has_skill_security_guidance():
    text = CODE_REVIEW_RUBRIC.read_text(encoding="utf-8")
    assert "skill-security" in text
    assert "allowed-tools" in text and "disallowed-tools" in text
    assert "Bash(*)" in text
    assert "context: fork" in text
    assert "# justification:" in text
    assert "shell=True" in text


def test_conformance_rubric_has_skill_security_carve_out():
    text = CONFORMANCE_RUBRIC.read_text(encoding="utf-8")
    assert ".claude/skills/**" in text
    assert ".factory/hooks/**" in text
    assert "[OOS]" in text
```

### Step 5.2 — Verify the tests fail

```bash
python -m pytest tests/test_rubric_skill_security.py -v
```

Expected: both fail (guidance doesn't exist yet).

### Step 5.3 — Implement (code-review half)

Edit `.claude/skills/code-review/RUBRIC.md` **and** `refinement-skills/code-review-reviewer-prompt.md`
identically. In both files, replace:

```
## Categories

`security`, `correctness`, `edge-case`, `naming`, `maintainability`.

## Output format
```

with:

```
## Categories

`security`, `correctness`, `edge-case`, `naming`, `maintainability`, `skill-security`.

## Security-Sensitive Surfaces: Claude Skills, Hooks, and Tool Permissions

A touched `.claude/skills/**/SKILL.md`, `.claude/settings.json` (or `.claude/settings.local.json`),
`.mcp.json`, plugin/marketplace config (`.claude/plugins/**`, `.claude-plugin/**`), or
`.factory/hooks/**` file gets these checks in addition to the standard rubric above — category
`skill-security` for all of them:

- **Broadened tool permissions** — a new or widened `allowed-tools`/`disallowed-tools` entry in
  `SKILL.md` frontmatter or `.claude/settings.json`, especially a bare `Bash(*)` or a
  family-level wildcard (`Bash(git:*)`, `Bash(gh:*)`) — `high` or `critical` depending on blast
  radius.
- **New or changed `hooks` entry**, `context: fork`, or a model/effort override in frontmatter —
  `high` or `critical`.
- **Plugin/MCP config changes** — `high` or `critical`.
- **Dynamic shell injection** in a `.claude/skills/**/scripts/**` or `.factory/hooks/**` script —
  externally-influenced input (a variable, argument, env value, or issue-comment field)
  interpolated *unescaped* into an executed command string (`bash -c "...$VAR..."`, an
  f-string/`.format()`/concatenated command passed to `subprocess` with `shell=True`, `eval`,
  or backticks) is a finding. Argv-list invocation (`shell=False`) or explicitly quoted/
  `shlex.quote`d input is not.
- **Justification downgrade** — a `# justification:` comment immediately above the changed
  frontmatter field, if substantive (concrete and specific, not boilerplate), downgrades the
  finding from `high`/`critical` to `medium` advisory — but the finding description must still
  state that human sign-off on the PR is expected; a justification comment never removes that
  expectation.

## Output format
```

Do not change line 1 or line 3 of either file — they remain intentionally different
(`# Code Reviewer` vs `# Code Reviewer — MarketHawk`, and the corresponding opening sentence).

### Step 5.4 — Verify the tests pass

```bash
python -m pytest tests/test_rubric_skill_security.py tests/test_code_review_skill_files.py -v
```

Expected: all pass, including the pre-existing `test_rubric_matches_source_except_delabel` (line
count still matches since the same block was added to both files at the same point).

### Step 5.5 — Commit

```bash
git add .claude/skills/code-review/RUBRIC.md refinement-skills/code-review-reviewer-prompt.md \
        tests/test_rubric_skill_security.py
git commit -m "feat(code-review): teach RUBRIC content-level skill-security checks (#46)"
```

---

## Task 6 — Conformance RUBRIC: OOS carve-out for skill-security paths

**Files:** `.claude/skills/conformance/RUBRIC.md`, `refinement-skills/conformance-reviewer-prompt.md`

`tests/test_conformance_skill_files.py::test_rubric_matches_source_prompt_content` requires the two
files to be **byte-identical**. Make the same edit to both.

### Step 6.1 — Verify the presence test from Task 5 still fails for conformance

```bash
python -m pytest tests/test_rubric_skill_security.py::test_conformance_rubric_has_skill_security_carve_out -v
```

Expected: fails (carve-out sentence doesn't exist yet).

### Step 6.2 — Implement

Edit `.claude/skills/conformance/RUBRIC.md` **and** `refinement-skills/conformance-reviewer-prompt.md`
identically. Replace:

```
**Documentation exception:** Updates to the project's documentation map — `ARCHITECTURE.md`,
`PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`, `CLAUDE.md`, and files under `docs/` —
that document a file, model, router, service, endpoint, or env var **added or changed by the
in-scope work** are category (b) supporting housekeeping and are **NOT** out-of-scope, even
when the spec's file-change list does not name them. The dark factory's implement step is
**required** (Phase 4 DOCUMENT) to make these updates, and excising them only churns work that
must be redone. Do NOT emit an `[OOS]` bullet for them. Only flag a doc change as `[OOS]` if it
documents something entirely unrelated to the in-scope work.

- [OOS] <file or area> — <one-sentence description of the unrelated change>
```

with:

```
**Documentation exception:** Updates to the project's documentation map — `ARCHITECTURE.md`,
`PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`, `CLAUDE.md`, and files under `docs/` —
that document a file, model, router, service, endpoint, or env var **added or changed by the
in-scope work** are category (b) supporting housekeeping and are **NOT** out-of-scope, even
when the spec's file-change list does not name them. The dark factory's implement step is
**required** (Phase 4 DOCUMENT) to make these updates, and excising them only churns work that
must be redone. Do NOT emit an `[OOS]` bullet for them. Only flag a doc change as `[OOS]` if it
documents something entirely unrelated to the in-scope work.

**Security-sensitive exception carve-out:** The Documentation exception above never applies to
`.claude/skills/**`, `.claude/settings.json` (or `.claude/settings.local.json`), `.mcp.json`,
plugin/marketplace config, or `.factory/hooks/**` — these are security-sensitive
factory-mechanism paths, not documentation. Any such change that is not named in the spec must
be flagged `[OOS]` regardless of how beneficial or hygienic it looks.

- [OOS] <file or area> — <one-sentence description of the unrelated change>
```

### Step 6.3 — Verify the tests pass

```bash
python -m pytest tests/test_rubric_skill_security.py tests/test_conformance_skill_files.py -v
```

Expected: all pass, including the pre-existing `test_rubric_matches_source_prompt_content`
(byte-identical since the same text was added to both files at the same point).

### Step 6.4 — Commit

```bash
git add .claude/skills/conformance/RUBRIC.md refinement-skills/conformance-reviewer-prompt.md
git commit -m "feat(conformance): add skill-security OOS carve-out to RUBRIC (#46)"
```

---

## Task 7 — Full verification

**Files:** none (verification only)

### Step 7.1 — Run the full test suite

```bash
python -m pytest tests/ -v
```

Expected: all tests pass, including every pre-existing test in `test_adapter.py`,
`test_blast_radius.py`, `test_diff_rank.py`, `test_code_review_skill_files.py`,
`test_conformance_skill_files.py`, `test_conformance_command_rubric_fallback.py`, and
`test_plan_command_conformance_rubric_fallback.py`.

### Step 7.2 — Run the smoke gate

```bash
bash smoke_gate.sh
```

Expected: exits 0 (per `CLAUDE.md`'s CI convention: `python -m pytest tests/ -v` plus
`smoke_gate.sh` plus workflow DAG checks).

### Step 7.3 — Validate the adapter file

```bash
python3 -m scripts.factory_core.adapter --clone-dir . --validate
```

Expected: `adapter OK`.

### Step 7.4 — No commit needed

This task is verification-only; if any step fails, fix the root cause in the relevant task above
and re-commit there rather than adding a new fixup commit.

---

## Out of Scope (per spec's Alternatives Considered / Open Questions)

- A new dedicated `gate_skill_security.py` script — rejected; this plan extends the two existing
  gate mechanisms only.
- Blocking on `.claude/skills/**/SKILL.md` by path alone — rejected; content is judged by the
  RUBRIC personas instead.
- A CI-checkable `allowed-tools` lint rejecting bare `Bash(*)`/family wildcards in `SKILL.md`
  frontmatter — named in the spec as a natural follow-up ticket, not part of this one.
- Unifying `.factory/adapter.yaml`'s plain-substring vs. anchored-regex glob syntax across the
  three lists — pre-existing and out of scope here.
- `workflows/archon-dark-factory.yaml`'s report node — already renders `blast.md`'s `TRIGGER`
  value verbatim; no change needed for the new label to surface in issue comments.

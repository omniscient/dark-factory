# Shadow-Model Pin Resolution — Implementation Plan

**Issue:** #406

> **For agentic workers:** Execute the tasks in order. Every step has exact code and
> commands. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `conformance.shadow_model` resolution null/empty-safe (null or `""` means
"no shadow") and self-sufficient at every site that consumes `$SHADOW_MODEL_PIN`. Each of
the three `if [ -n "${SHADOW_MODEL_PIN:-}" ]` guards in `dark-factory-conformance.md`
re-resolves the pin inside its own `bash` block, so the shadow verdict can no longer drop
out of the attestation or comment silently.

**Spec:** `docs/superpowers/specs/2026-11-28-shadow-model-pin-resolution-design.md`

**Architecture:** This changes Markdown only, in the agent-executed command files. One
three-line snippet (a local `ROOT=` lookup, a null/empty-safe `python3 -c` read of the
clone's `$ROOT/.claude/skills/refinement/config.yaml`, and the unchanged
`${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}` env override) replaces the old
two-line one-liner at sites 1 and 5. The same snippet is inserted at sites 2-4,
immediately before each guard. The snippet stays a literal copy at each site: no shared
function and no baked-config path (spec "Deviation from the issue's ask" and
"Alternatives Considered"). A new pytest file pulls the Phase 1 snippet out of the
Markdown and runs it under `bash` against a `git init`-ed fixture clone. That follows the
precedent at `tests/test_docs_conformance_command.py:147-150`. Static assertions in the
same file pin the snippet's placement and check that all five copies are identical.

**Tech Stack:** Markdown command files, bash, Python 3 + PyYAML (inline `python3 -c`),
pytest.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/test_shadow_model_resolution.py` | Create | Behavioral (7 cases: the spec's 6 plus a subdirectory-cwd case) and static (3 cases) tests for the resolution snippet |
| `commands/dark-factory-conformance.md` | Modify | Site 1: replace the one-liner in Phase 1 step 12. Sites 2-4: insert the snippet before the Phase 4, Phase 5 step 1 and Phase 5 step 4 guards |
| `commands/dark-factory-plan.md` | Modify | Site 5: replace the one-liner in Phase 3.5 step 4a |
| `docs/configuration.md` | Modify | Update the `conformance.shadow_model` / `CONFORMANCE_SHADOW_MODEL` citations and add the null/empty note. Re-point the other citations in this file that the inserted lines shift |

Nothing else changes. `config/config.yaml` stays as it is (AC4). This plan touches no
`gate_*`, breaker, budget, workflow, `deploy/**` or `.github/workflows/**` file, and
needs no new `tests/test_*.sh` or `ci.yml` change.

### The canonical snippet (used verbatim in Tasks 1 and 2)

The snippet is these three lines, indented per site (4, 0, 3, 3 and 4 spaces; spec
Requirement 3):

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

It replaces this two-line form, which is what sites 1 and 5 carry on `main`:

```bash
SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

Do not change the `claude-fable-5-1` fallback literal. Do not add comment lines inside
the snippet, because the static test compares the three lines exactly.

---

## Task 1: Behavioral tests + fix site 1 (conformance Phase 1 step 12)

**Files:**
- Create: `tests/test_shadow_model_resolution.py`
- Modify: `commands/dark-factory-conformance.md` (Phase 1 step 12, currently lines 75-78)

- [ ] **Step 1: Write the failing behavioral tests**

Create `tests/test_shadow_model_resolution.py` with exactly this content:

```python
"""Shadow-model pin resolution contract (#406). The five resolution sites in the
conformance and plan commands are agent-executed Markdown, so — like
tests/test_docs_conformance_command.py — the behavioral cases extract the bash snippet
and run it against a fixture clone, and the static cases pin where the snippet sits."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONF = _REPO_ROOT / "commands" / "dark-factory-conformance.md"
_PLAN = _REPO_ROOT / "commands" / "dark-factory-plan.md"
_GUARD = 'if [ -n "${SHADOW_MODEL_PIN:-}" ]; then'


def _phase1_snippet():
    text = _CONF.read_text(encoding="utf-8")
    step = text[text.index("12. Resolve the shadow model pin"):]
    body = step.split("```bash\n", 1)[1].split("```", 1)[0]
    return "\n".join(line.strip() for line in body.splitlines())


def _resolve(tmp_path, config_text, env=None, subdir=False):
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git")):
        pytest.skip("needs a POSIX host with bash and git")
    run_env = {k: v for k, v in os.environ.items()
               if k != "CONFORMANCE_SHADOW_MODEL" and not k.startswith("GIT_")}
    run_env.update(env or {})
    root = tmp_path / "clone"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=run_env)
    if config_text is not None:
        cfg = root / ".claude" / "skills" / "refinement" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(config_text, encoding="utf-8")
    cwd = root / "sub" if subdir else root
    cwd.mkdir(exist_ok=True)
    block = _phase1_snippet() + '\nprintf "%s" "$SHADOW_MODEL_PIN"\n'
    proc = subprocess.run(["bash", "-c", block], cwd=str(cwd), env=run_env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_null_shadow_model_is_off(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  shadow_model: null\n") == ""


def test_empty_shadow_model_is_off(tmp_path):
    assert _resolve(tmp_path, 'conformance:\n  shadow_model: ""\n') == ""


def test_absent_key_falls_back_to_default(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  enabled: true\n") == "claude-fable-5-1"


def test_explicit_value_is_used(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  shadow_model: claude-opus-5-5\n") == "claude-opus-5-5"


def test_missing_config_file_falls_back_to_default(tmp_path):
    assert _resolve(tmp_path, None) == "claude-fable-5-1"


def test_explicit_empty_env_wins_over_config(tmp_path):
    cfg = "conformance:\n  shadow_model: claude-opus-5-5\n"
    assert _resolve(tmp_path, cfg, env={"CONFORMANCE_SHADOW_MODEL": ""}) == ""


def test_config_is_found_from_a_subdirectory(tmp_path):
    cfg = "conformance:\n  shadow_model: claude-opus-5-5\n"
    assert _resolve(tmp_path, cfg, subdir=True) == "claude-opus-5-5"
```

Notes:
- The fixture clone gets its own `git init`, so `git rev-parse --show-toplevel` resolves
  inside `tmp_path` and never reaches the real repo.
- `CONFORMANCE_SHADOW_MODEL` is stripped from the inherited env so that an operator's
  shell cannot change the result. The env-override case sets it explicitly. `GIT_*`
  variables are stripped too. A `GIT_DIR`/`GIT_WORK_TREE` inherited from a git hook or
  CI wrapper would otherwise make `git rev-parse` resolve to the real repo.
- `test_config_is_found_from_a_subdirectory` is the one case beyond the spec's six. It
  proves the `$ROOT` prefix matters: the old cwd-relative read fails from a subdirectory.

- [ ] **Step 2: Run the tests and confirm they fail**

Run:
```bash
PYTHONPATH=scripts python -m pytest tests/test_shadow_model_resolution.py -v
```
Expected: `2 failed, 5 passed`. The two failures are:
- `test_null_shadow_model_is_off`: the old snippet prints `None`, giving `AssertionError: assert 'None' == ''`.
- `test_config_is_found_from_a_subdirectory`: the relative read fails, so the snippet falls back and gives `assert 'claude-fable-5-1' == 'claude-opus-5-5'`.

- [ ] **Step 3: Replace the one-liner at site 1**

In `commands/dark-factory-conformance.md`, Phase 1 step 12, replace:

```
    ```bash
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
    ```

## Phase 2: LOCATE SPEC
```

with (4-space indent, the canonical snippet):

```
    ```bash
    ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
    ```

## Phase 2: LOCATE SPEC
```

Leave the step's heading line (`12. Resolve the shadow model pin (Requirement 7 —
explicit-empty-wins-over-unset):`) as it is; the test anchors on it.

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
PYTHONPATH=scripts python -m pytest tests/test_shadow_model_resolution.py tests/test_conformance_command_shadow_review.py tests/test_conformance_command_artifact_guard.py -v
```
Expected: all pass. The new file reports `7 passed`, and no existing test regresses.

- [ ] **Step 5: Commit**

```bash
git add tests/test_shadow_model_resolution.py commands/dark-factory-conformance.md
git commit -m "fix(#406): null/empty-safe shadow_model resolution in conformance Phase 1"
```

---

## Task 2: Static placement tests + sites 2-5

**Files:**
- Modify: `tests/test_shadow_model_resolution.py` (append)
- Modify: `commands/dark-factory-conformance.md` (Phase 4 block; Phase 5 step 1 block; Phase 5 step 4 block)
- Modify: `commands/dark-factory-plan.md` (Phase 3.5 step 4a, currently lines 185-188)

- [ ] **Step 1: Append the failing static tests**

Append this to the end of `tests/test_shadow_model_resolution.py`:

```python


def _bash_blocks(path):
    """Every fenced ```bash block in *path*, as lists of whitespace-stripped lines."""
    blocks, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if cur is None and s == "```bash":
            cur = []
        elif cur is not None and s == "```":
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(s)
    return blocks


def _snippets(path):
    """(block, index) of each resolution snippet: the ROOT= line and the two after it."""
    found = []
    for block in _bash_blocks(path):
        for i, line in enumerate(block):
            if line.startswith("SHADOW_MODEL_DEFAULT="):
                found.append((block, i - 1))
    return found


def test_no_site_reads_the_config_relative_to_cwd():
    for path in (_CONF, _PLAN):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "shadow_model" in line:
                assert "open('.claude/skills/refinement/config.yaml')" not in line, path.name


def test_all_five_snippets_are_identical():
    conf, plan = _snippets(_CONF), _snippets(_PLAN)
    assert (len(conf), len(plan)) == (4, 1)
    texts = {"\n".join(block[i:i + 3]) for block, i in conf + plan}
    assert len(texts) == 1, texts
    first = texts.pop().splitlines()[0]
    assert first == "ROOT=$(git rev-parse --show-toplevel 2>/dev/null)"
    assert _phase1_snippet().strip() == "\n".join(conf[0][0][conf[0][1]:conf[0][1] + 3])


def test_snippet_precedes_each_guard_in_the_same_block():
    guarded = [b for b in _bash_blocks(_CONF) if _GUARD in b]
    assert len(guarded) == 3
    for block in guarded:
        root_idx = block.index("ROOT=$(git rev-parse --show-toplevel 2>/dev/null)")
        assert block[root_idx + 1].startswith("SHADOW_MODEL_DEFAULT=")
        assert block[root_idx + 2].startswith("SHADOW_MODEL_PIN=")
        assert root_idx + 2 < block.index(_GUARD)
```

`_bash_blocks` strips whitespace, so the comparison ignores indentation (spec
Requirement 3). The escaped `` \`\`\` `` lines inside the Phase 4 and Phase 5 comment
bodies are not fences: `strip()` leaves them as `` \`\`\` ``, which never equals the
bare three-backtick fence.

- [ ] **Step 2: Run the tests and confirm they fail**

Run:
```bash
PYTHONPATH=scripts python -m pytest tests/test_shadow_model_resolution.py -v
```
Expected: `3 failed, 7 passed`. The three failures are:
- `test_no_site_reads_the_config_relative_to_cwd`: `dark-factory-plan.md` still carries the old read.
- `test_all_five_snippets_are_identical`: `assert (1, 1) == (4, 1)`.
- `test_snippet_precedes_each_guard_in_the_same_block`: `ValueError: ... is not in list`.

- [ ] **Step 3: Insert the snippet at site 2 (Phase 4 PASS block, 0-space indent)**

In `commands/dark-factory-conformance.md`, `## Phase 4: PASS — Write attestation`,
replace:

```
} > "$ARTIFACTS_DIR/conformance.md"

if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
  {
```

with:

```
} > "$ARTIFACTS_DIR/conformance.md"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
  {
```

The old text is unique. The Phase 5 step 4 counterpart is indented 3 spaces, so it does
not match.

- [ ] **Step 4: Insert the snippet at site 3 (Phase 5 step 1 comment-build block, 3-space indent)**

In `## Phase 5: BLOCKED`, step 1, replace:

```
   # SHADOW_MODEL: line can never masquerade as an UNCERTAIN shadow verdict.
   SHADOW_BLOCK=""
```

with:

```
   # SHADOW_MODEL: line can never masquerade as an UNCERTAIN shadow verdict.
   ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
   SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
   SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
   SHADOW_BLOCK=""
```

(The spec puts site 3 before `SHADOW_BLOCK=""`.)

- [ ] **Step 5: Insert the snippet at site 4 (Phase 5 step 4 status-write block, 3-space indent)**

In `## Phase 5: BLOCKED`, step 4, replace:

```
   } > "$ARTIFACTS_DIR/conformance.md"

   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
     {
```

with:

```
   } > "$ARTIFACTS_DIR/conformance.md"

   ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
   SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
   SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
     {
```

- [ ] **Step 6: Replace the one-liner at site 5 (plan Phase 3.5 step 4a, 4-space indent)**

In `commands/dark-factory-plan.md`, Phase 3.5 step 4a, replace:

```
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

with:

```
    ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
```

Leave the prose `$SHADOW_MODEL_PIN` references in plan.md (steps 6a, 8b, f2, Phase 4)
untouched (spec Non-goals). Leave the `PLAN_GRACE` line (`dark-factory-plan.md`, Phase 4
step 3) untouched too: it still uses the cwd-relative read on purpose, as part of the
deferred audit. It does not mention `shadow_model`, so the static test ignores it.

- [ ] **Step 7: Check the snippet placement**

Run:
```bash
grep -n '^ *ROOT=\$(git rev-parse\|^ *SHADOW_MODEL_DEFAULT=\|^ *SHADOW_MODEL_PIN=\|if \[ -n "\${SHADOW_MODEL_PIN:-}" \]' commands/dark-factory-conformance.md commands/dark-factory-plan.md | cut -c1-70
```
Expected (line numbers exact):
```
commands/dark-factory-conformance.md:76:    ROOT=$(git rev-parse --sho
commands/dark-factory-conformance.md:77:    SHADOW_MODEL_DEFAULT=$(pyt
commands/dark-factory-conformance.md:78:    SHADOW_MODEL_PIN="${CONFOR
commands/dark-factory-conformance.md:594:ROOT=$(git rev-parse --show-t
commands/dark-factory-conformance.md:595:SHADOW_MODEL_DEFAULT=$(python
commands/dark-factory-conformance.md:596:SHADOW_MODEL_PIN="${CONFORMAN
commands/dark-factory-conformance.md:597:if [ -n "${SHADOW_MODEL_PIN:-
commands/dark-factory-conformance.md:690:   ROOT=$(git rev-parse --sho
commands/dark-factory-conformance.md:691:   SHADOW_MODEL_DEFAULT=$(pyt
commands/dark-factory-conformance.md:692:   SHADOW_MODEL_PIN="${CONFOR
commands/dark-factory-conformance.md:694:   if [ -n "${SHADOW_MODEL_PI
commands/dark-factory-conformance.md:746:   ROOT=$(git rev-parse --sho
commands/dark-factory-conformance.md:747:   SHADOW_MODEL_DEFAULT=$(pyt
commands/dark-factory-conformance.md:748:   SHADOW_MODEL_PIN="${CONFOR
commands/dark-factory-conformance.md:749:   if [ -n "${SHADOW_MODEL_PI
commands/dark-factory-plan.md:186:    ROOT=$(git rev-parse --show-topl
commands/dark-factory-plan.md:187:    SHADOW_MODEL_DEFAULT=$(python3 -
commands/dark-factory-plan.md:188:    SHADOW_MODEL_PIN="${CONFORMANCE_
```
`git diff --stat origin/main -- commands/` should show
`commands/dark-factory-conformance.md | 12 +++++++++++-` and
`commands/dark-factory-plan.md | 3 ++-`. That is 1 line replaced and
1 added at site 1, 3 lines added at each of sites 2-4, and 1 replaced and 1 added at
site 5.

- [ ] **Step 8: Run the tests and confirm they pass**

Run:
```bash
PYTHONPATH=scripts python -m pytest tests/test_shadow_model_resolution.py tests/test_conformance_command_shadow_review.py tests/test_plan_command_shadow_conformance.py tests/test_conformance_command_artifact_guard.py tests/test_verifier_contract_doc_referenced.py tests/test_config_conformance_shadow_model.py -v
```
Expected: all pass. The new file reports `10 passed`.

- [ ] **Step 9: Commit**

```bash
git add tests/test_shadow_model_resolution.py commands/dark-factory-conformance.md commands/dark-factory-plan.md
git commit -m "fix(#406): re-resolve SHADOW_MODEL_PIN inline before each Phase 4/5 guard"
```

---

## Task 3: Update the `docs/configuration.md` citations

**Files:**
- Modify: `docs/configuration.md` (lines 129, 132, 148, 304, 501, 772)

The inserted lines shift later line numbers by these amounts:
- `dark-factory-conformance.md`: +1 after line 76, a cumulative +4 after the Phase 4 insert, +7 after the Phase 5 step 1 insert, and +10 after the Phase 5 step 4 insert.
- `dark-factory-plan.md`: +1 after line 186.

`scripts/docs_refs_check.py` range-checks cited lines only, so a stale citation would not
fail CI (spec Requirement 5). Requirement 5 names the two shadow rows. The other four
edits below re-point the remaining `configuration.md` citations that this change makes
stale, for the same reason. They stay inside the one doc file AC4 allows.

- [ ] **Step 1: Edit the `conformance.shadow_model` row (line 148)**

Replace:
```
| `conformance.shadow_model` | `claude-fable-5-1` | shadow reviewer model; the env var, when set (even empty), wins and an empty value spawns no shadow | `commands/dark-factory-conformance.md:76-77`, `commands/dark-factory-plan.md:186-187` (agent-read, python reads the clone's config; env expansion is `${CONFORMANCE_SHADOW_MODEL-default}`) | `CONFORMANCE_SHADOW_MODEL` (run, via `.archon/.env`) | no |
```
with:
```
| `conformance.shadow_model` | `claude-fable-5-1` | shadow reviewer model; `null` or empty in config means no shadow; the env var, when set (even empty), wins and an empty value spawns no shadow | `commands/dark-factory-conformance.md:76-78`, `commands/dark-factory-plan.md:186-188` (agent-read, python reads the clone's config at the repo root; env expansion is `${CONFORMANCE_SHADOW_MODEL-default}`); re-resolved inline before each shadow guard at `commands/dark-factory-conformance.md:594-596,690-692,746-748` | `CONFORMANCE_SHADOW_MODEL` (run, via `.archon/.env`) | no |
```

- [ ] **Step 2: Edit the `CONFORMANCE_SHADOW_MODEL` env row (line 501)**

Replace `` | `CONFORMANCE_SHADOW_MODEL` | unset | `commands/dark-factory-conformance.md:77` | ``
with `` | `CONFORMANCE_SHADOW_MODEL` | unset | `commands/dark-factory-conformance.md:78` | ``.

- [ ] **Step 3: Re-point the four other shifted citations**

- Line 129 (`direct_to_pr.plan_grace_minutes` row): `` `commands/dark-factory-plan.md:298` (agent-read) `` → `` `commands/dark-factory-plan.md:299` (agent-read) `` (the `PLAN_GRACE=` line).
- Line 132 (prose after the table): `` `commands/dark-factory-plan.md:297`, so renaming `` → `` `commands/dark-factory-plan.md:298`, so renaming `` (the `grep -q "direct-to-pr"` line).
- Line 304: `` `commands/dark-factory-conformance.md:208` `` → `` `commands/dark-factory-conformance.md:209` `` (the `token-opt-caps.env` source line).
- Line 772 (`direct-to-pr` literal row): `` `commands/dark-factory-plan.md:297` | `` → `` `commands/dark-factory-plan.md:298` | ``.

- [ ] **Step 4: Verify the citations resolve to the intended lines**

Run:
```bash
sed -n '76p;78p;209p;594p;690p;746p' commands/dark-factory-conformance.md | cut -c1-45
sed -n '186p;188p;298p;299p' commands/dark-factory-plan.md | cut -c1-45
grep -c 'dark-factory-conformance.md:76-77\|dark-factory-plan.md:186-187\|dark-factory-conformance.md:77`\|dark-factory-conformance.md:208`\|dark-factory-plan.md:297`\|dark-factory-plan.md:298` (agent' docs/configuration.md || true
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected:
```
    ROOT=$(git rev-parse --show-toplevel 2>/d
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MO
[ -f "$ARTIFACTS_DIR/token-opt-caps.env" ] &&
ROOT=$(git rev-parse --show-toplevel 2>/dev/n
   ROOT=$(git rev-parse --show-toplevel 2>/de
   ROOT=$(git rev-parse --show-toplevel 2>/de
    ROOT=$(git rev-parse --show-toplevel 2>/d
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MO
     --json labels --jq '.labels[].name' | gr
   PLAN_GRACE=$(python3 -c "import yaml; d=ya
0
Docs reference check passed for 4 doc(s).
```

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md
git commit -m "docs(#406): update shadow_model citations and null/empty note"
```

---

## Task 4: Final verification (AC1-AC5)

**Files:** none modified.

- [ ] **Step 1: Run the full suite (AC5)**

Run:
```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```
Expected: all pass, with 10 more tests than on `main` (all from
`tests/test_shadow_model_resolution.py`). A dry run of this plan against a scratch copy
gave `4483 passed`.

- [ ] **Step 2: Run the docs reference check (AC5)**

Run:
```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 3: Check the file allowlist (AC4)**

Use the two-dot form. The three-dot form also lists files that `main` merged after the
branch forked, which gives false out-of-scope hits. The refine/plan artifacts that
`transfer_refine_artifacts.sh` copies onto the feat branch are excluded:
```bash
git fetch -q origin main
git diff --name-only origin/main HEAD -- . ':!docs/superpowers/' ':!.archon/memory/'
git diff --quiet origin/main HEAD -- config/config.yaml && echo "config.yaml unchanged"
```
Expected:
```
commands/dark-factory-conformance.md
commands/dark-factory-plan.md
docs/configuration.md
tests/test_shadow_model_resolution.py
config.yaml unchanged
```

- [ ] **Step 4: Check the AC1-AC3 mapping**

- AC1: covered by the 6 spec cases in Task 1, plus the subdirectory case.
- AC2: covered by `test_snippet_precedes_each_guard_in_the_same_block`.
- AC3: covered by `test_all_five_snippets_are_identical` and
  `test_no_site_reads_the_config_relative_to_cwd`.

Nothing to commit in this task.

---

## Reservations / known follow-ups (record in the PR body; do NOT fix in this ticket)

1. **Stale line citations outside the AC4 allowlist.** `docs/features.md` and
   `docs/run-lifecycle.md` cite lines in the two command files that this change shifts.
   AC4 limits the diff to the four files above and Requirement 5 says "No other doc
   changes", so this ticket leaves them alone. `docs_refs_check.py` still passes, since
   it range-checks only. Current → post-change values, for a follow-up docs ticket:
   - `docs/features.md`:
     - line 105: `dark-factory-plan.md:308` → `:309`
     - line 106: `dark-factory-conformance.md:181` → `:182`
     - line 107: `:306`-`:328` → `:307`-`:329`, and `:593`-`:607` → `:597`-`:611`
     - line 111: `:586` → `:587`
     - line 127: `:208` → `:209`
     - line 129: `:210` → `:211`
     - line 134: `:656` → `:660`
     - line 144: `conformance.md:284` → `:285`
     - line 160: `:277` → `:278`
   - `docs/run-lifecycle.md`:
     - line 663: `conformance.md:208` → `:209`
     - line 710: `plan.md:240-268` → `:241-269`
     - lines 819 and 1319: `conformance.md:748-752` → `:758-762`
     - line 1394: `:587-589` → `:588-590`
     - line 1400: `:151-220` → `:152-221`
   - The line-number references in `tests/test_code_review_payload.py:64` (`:502`) and
     `tests/test_comment_digest.py:595` (`:140`) are sample text inside a test fixture
     or docstring, not live citations. They need no change.
   If the docs-conformance gate (Gate 2.5) flags these, say in the PR that they are
   deliberately deferred under AC4.
2. **Sibling cwd-relative config reads** (`PLAN_GRACE`, `SPEC_GRACE` and about 10 more
   `.claude/skills/refinement/config.yaml` reads): deferred to a separate audit ticket
   (spec Non-goals).
3. **The other Phase 4/5 state is still agent-carried.** `SHADOW_STATUS`,
   `SHADOW_FINDINGS_COUNT`, `SHADOW_SEVERITY`, `SHADOW_DIALOGUE` and
   `SHADOW_VERDICT_LINE` still depend on the agent carrying them between blocks. When
   they are dropped, the blocks now emit a visible `UNCERTAIN` record instead of
   skipping the block. That is the intended outcome (spec Requirement 2), and the
   defaults are unchanged.

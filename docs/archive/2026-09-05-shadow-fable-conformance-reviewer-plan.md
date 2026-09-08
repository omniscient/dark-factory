# Implementation Plan: Shadow-run Claude Fable 5.1 as the conformance reviewer

**Issue:** #394

## Goal

Shadow-run Claude Fable 5.1 alongside the existing Opus-4.8-pinned conformance reviewer at
both integration points (`dark-factory-plan.md` Phase 3.5, plan-vs-spec; `dark-factory-
conformance.md` Phase 3, implementation-vs-spec), with Opus alone gating. Record Fable's
verdict — structured fields plus raw prose — durably (existing plan comment; a new PASS-path
marker comment for conformance) so a follow-up ticket can compare ~10 tickets' worth of
Fable-vs-Opus verdicts against the operator's actual gate decisions.

## Architecture

Both integration points get an identical shape: immediately after the existing Opus
conformance-reviewer spawn (first pass and every reconcile re-spawn), spawn a second,
non-gating subagent with the same rubric/input, pinned to
`${CONFORMANCE_SHADOW_MODEL-<config default>}` (empty = skip). Its output accumulates in a
`SHADOW_DIALOGUE` variable — parallel to, and never merged into, `CONFORMANCE_DIALOGUE` (the
variable that drives the real Opus-gated verdict and Phase 3.6's `[OOS]` excision scan). Four
structured `SHADOW_*` lines (mirroring `emit_verdict`'s shape, but never written through
`emit_verdict`/`gate_lib.sh` itself, since those are the *gating* verdict's own namespace) get
derived from each shadow response and surfaced wherever the run already posts durably.

`VERIFIER-CONTRACT.md` gains a per-checker pin table (replacing the single "always
`claude-opus-4-8`" line) and a documented Verdict→`SHADOW_*` mapping, referenced by both
command files instead of duplicated in each. `config/config.yaml`'s `conformance:` block
gains the baked `shadow_model: claude-fable-5-1` default; the real off-switch is the
`CONFORMANCE_SHADOW_MODEL` env var via `.archon/.env` (no image rebuild).

**Deliberate, called-out deviation from the spec's literal shell syntax:** the spec's
Architecture section writes the resolution as `${CONFORMANCE_SHADOW_MODEL:-claude-fable-5-1}`
(colon-dash). This plan instead resolves it as `${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}`
(dash only, no colon) throughout. Reason, not a silent substitution: `run-compose.yml:23`'s
`env_file: .archon/.env` means an operator stopping the trial sets
`CONFORMANCE_SHADOW_MODEL=` (empty string) in that file — under bash's colon-dash rule an
*explicitly empty* variable is treated the same as *unset* and `:-` would still substitute the
non-empty default, silently defeating Requirement 7's own off-switch ("empty disables the
shadow spawn entirely ... the actual stop-the-trial lever is exporting
`CONFORMANCE_SHADOW_MODEL=` (empty)"). Only the dash-only form preserves an explicit empty
string as empty while still falling back to the config default when the variable is truly
unset. The spec's shorthand is read as documentation-convention shorthand (the same abbreviated
`# env: X overrides` notation `config.yaml` uses for every other env-overridable key, none of
which litigate the unset-vs-empty distinction in prose either), not as a literal shell
directive that overrides Requirement 7's explicit prose. This is flagged here, and again inline
at each call site, precisely so the conformance reviewer sees the reasoning and doesn't need to
independently guess whether it is an unexplained `[MATERIAL]` deviation.

**Task 0 finding (recorded here per Requirement 1 — see Task 0 below for the full writeup):**
a same-session check during planning found this session's Agent tool rejects the literal
`model: claude-fable-5-1` string (`InputValidationError`, enum restricted to
`sonnet|opus|haiku|fable`) — and, symmetrically, also rejects the literal
`model: claude-opus-4-8` the same way. The short alias `model: fable` succeeds. This means the
literal pin strings already used throughout the command files today are call-site
documentation the executing agent translates to whatever its Agent tool actually accepts
(alias, here) — not a new problem specific to Fable. The plan below keeps `claude-fable-5-1`
as the literal, spec-approved pin value everywhere (config, docs, prose) and adds one
clarifying note to `VERIFIER-CONTRACT.md` about the translation, rather than inventing a new
mechanism. Task 0 is still a required, executable first implementation step (below) because
this session's tool surface is not proven identical to the production image's — the spec's own
Assumptions section anticipated exactly this gap.

**Note on spec/plan branch availability (memory `[PATTERN]` #42, raised in architect review):**
this ticket's shadow spawn reuses the identical `$SPEC_CONTENT`/`$ARTIFACT_CONTENT` variables
the existing, unmodified Opus conformance-reviewer spawn already resolves for the same cycle —
Tasks 3 and 4 add no new spec-resolution logic. Whether `docs/superpowers/specs/*.md` committed
on a ticket's `refine/issue-N-*` branch is reliably available by the time its `feat/issue-N-*`
implement branch runs conformance is a pre-existing, cross-cutting property of the whole
refine→plan→implement pipeline (every ticket's `feat/issue-N-*` branch forks fresh from `main`
via `setup-branch` in `workflows/archon-dark-factory.yaml`, and `refine/issue-N-*` is never
merged to `main` first) — not something introduced or worsened by this ticket, and not
addressed by this plan. It is out of scope here per the spec's own Requirement 9 (unchanged),
exactly like the architect model/product-owner pins and `gate_*`/breaker/budget config; if a
real gap exists it needs its own ticket with its own spec, since fixing it would touch
`dark-factory-conformance.md`'s Phase 2 spec-location logic and potentially
`dark-factory-implement.md`, well beyond "shadow-run Fable."

## Tech Stack

Bash + Python 3 prose embedded in Claude Code command markdown files (`commands/*.md`),
YAML config (`config/config.yaml`), a shared markdown contract doc
(`refinement-skills/VERIFIER-CONTRACT.md`), and `pytest` content-assertion tests over all of
the above (this repo's existing convention — see `tests/test_verifier_contract_doc_referenced.py`,
`tests/test_conformance_command_rubric_fallback.py`) plus one `verdict.py`/`run_record.py`
golden-corpus fixture pair proving non-interference in code, not just prose.

## File Structure

| Path | Change |
|---|---|
| `refinement-skills/VERIFIER-CONTRACT.md` | Per-checker pin table, refusal→`UNCERTAIN` clause, shadow verdict mapping, alias-translation note |
| `config/config.yaml` | `conformance.shadow_model: claude-fable-5-1` |
| `commands/dark-factory-plan.md` | Phase 3.5 shadow spawn (first pass + reconcile), Phase 4 comment fold |
| `commands/dark-factory-conformance.md` | Phase 1 pin resolution, Phase 3/3.5 shadow spawn, Phase 4 PASS structured fields + new marker comment, Phase 5 BLOCKED fold |
| `tests/test_verifier_contract_doc_referenced.py` | Extend: per-checker table + refusal clause assertions |
| `tests/test_config_conformance_shadow_model.py` | New: `shadow_model` default present |
| `tests/test_plan_command_shadow_conformance.py` | New: plan.md shadow-spawn content assertions |
| `tests/test_conformance_command_shadow_review.py` | New: conformance.md shadow-spawn content assertions |
| `tests/test_verdict.py` | Extend: golden-corpus fixture count 17 → 18 |
| `tests/fixtures/verdicts/conformance__pass_with_shadow.md` + `.expected.json` | New: proves `SHADOW_*` lines don't perturb `parse_verdict`/`_parse_artifact_stage` |

---

## Task 0: Fable availability check (Requirement 1 — must run for real before Task 1)

This is the mandatory first step of implementation, not just planning. It confirms the
*implement*-phase container's Agent tool accepts a Fable pin before any of the later tasks
land text that depends on it.

**Files:** none (verification only; no commit).

**Steps:**

1. Spawn a trivial subagent with the literal production pin, exactly as Requirement 1 specifies:
   - `description`: `"Task 0: Fable literal-pin availability check"`
   - `model`: `claude-fable-5-1`
   - `prompt`: `"Reply with exactly one line: OK"`
2. If it succeeds (no tool error): record `TASK0_MODEL_VALUE=claude-fable-5-1` — the command
   files below can use the literal pin as spec'd with no translation note needed, and skip
   straight to Task 1.
3. If it raises a tool/input error, retry once with the short alias:
   - `model`: `fable`
   - Same prompt.
   - If *this* succeeds: record `TASK0_MODEL_VALUE=claude-fable-5-1` (keep the literal in all
     config/prose per the approved spec — do not substitute the alias into `config.yaml` or
     the command files, since the spec's approved value is the literal string and changing it
     would itself be a conformance-reviewer-flagged `[MATERIAL]` deviation), and note in this
     plan's Task 1 that `VERIFIER-CONTRACT.md`'s alias-translation clarification applies.
     This is the branch this plan was authored against — see the planning-time finding in
     Architecture above (literal rejected, alias `fable` succeeded).
4. If *both* the literal and the alias are rejected (or return an error/timeout/refusal):
   stop implementing. Do not proceed to Task 1. Instead:
   - Post the finding as an issue comment (fetch the footer first). This is an *executed*
     command in this self-target checkout, not text written into a `commands/*.md` file — use
     the real tracked path (`scripts/...`), not the `dark-factory/` TARGET-PATH prefix that
     other-target command files use for a path templated per clone layout:
     ```bash
     FOOTER=$(python3 scripts/factory_core/cli.py marker refinement)
     gh issue comment 394 --body "## Task 0 — Fable availability check failed

     Neither \`model: claude-fable-5-1\` nor the short alias \`fable\` succeeded via the Agent
     tool under the factory OAuth token in this image. Per Requirement 1, stopping rather than
     falling back to Opus-only silently.

     ---
     $FOOTER"
     ```
   - Add `needs-discussion`:
     `python3 scripts/factory_core/providers/cli.py tracker label --id 394 --add needs-discussion`
   - Exit cleanly (do not implement Tasks 1-6).

Given this plan's own Task 0 check already found the alias path works (see Architecture), the
expected outcome on re-run is step 3's success branch — proceed to Task 1.

---

## Task 1: `VERIFIER-CONTRACT.md` — per-checker pin table, refusal clause, shadow mapping

**Files:** `refinement-skills/VERIFIER-CONTRACT.md`, `tests/test_verifier_contract_doc_referenced.py`

### TDD Steps

1. Add a failing test to `tests/test_verifier_contract_doc_referenced.py`:

```python
def test_verifier_contract_has_per_checker_pin_table():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    assert "| Checker pair | Gating model (pin) | Shadow model" in content
    assert "${CONFORMANCE_SHADOW_MODEL-claude-fable-5-1}" in content


def test_verifier_contract_has_refusal_to_uncertain_clause():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    assert "maps to `UNCERTAIN`, never `PASS`" in content


def test_verifier_contract_documents_shadow_verdict_mapping():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    for token in ("SHADOW_MODEL", "SHADOW_STATUS", "SHADOW_FINDINGS_COUNT", "SHADOW_SEVERITY"):
        assert token in content
    assert "Material divergence" in content and "BLOCKED" in content
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_verifier_contract_doc_referenced.py -x -v
   ```
   Expected: the three new tests fail (`AssertionError`); the five pre-existing tests in this
   file (`test_every_command_file_references_verifier_contract_doc`,
   `test_plan_command_references_contract_doc_at_both_pin_sites`,
   `test_every_command_file_keeps_inline_model_pin`,
   `test_gate_lib_header_references_verdict_schema_docs`,
   `test_verifier_contract_doc_exists_and_documents_env_contract`) still pass unchanged.

3. Implement — replace the current "Checker subagent invocation (Opus-pinned pairs)" section's
   three bullets (Model pin, Read access, Clone-live-first resolution) with:

```markdown
## Checker subagent invocation (per-checker pins)

| Checker pair | Gating model (pin) | Shadow model (advisory, non-gating) |
|---|---|---|
| `refine`'s product-owner | `claude-opus-4-8` | — |
| `plan`'s architect (Phase 3) | `claude-opus-4-8` | — |
| `plan`'s Phase 3.5 conformance reviewer | `claude-opus-4-8` | `${CONFORMANCE_SHADOW_MODEL-claude-fable-5-1}` (skipped if empty) |
| `conformance`'s Phase 3 conformance reviewer | `claude-opus-4-8` | `${CONFORMANCE_SHADOW_MODEL-claude-fable-5-1}` (skipped if empty) |
| `code-review`'s reviewer | `claude-opus-4-8` | — |

- **Model pin (gating):** never let the gating checker subagent inherit the orchestrator's
  model. This applies to every re-spawn in a reconcile loop, not just the first spawn.
- **Shadow spawn (advisory only):** the conformance-reviewer pair (plan Phase 3.5, conformance
  Phase 3) additionally spawns a second, non-gating subagent pinned to the Shadow model column
  above, immediately after the gating spawn, at every cycle — first pass and every reconcile
  re-spawn — with the identical rubric/input the gating spawn saw for that same cycle. Its
  output is recorded (`SHADOW_*` fields, see below) but never influences gating, the reconcile
  loop's verdict check, or Phase 3.6's out-of-scope excision — those read `CONFORMANCE_DIALOGUE`
  only, never `SHADOW_DIALOGUE`.
- **Refusal → `UNCERTAIN`, never `PASS`:** a refusal stop from any checker subagent — gating or
  shadow — maps to `UNCERTAIN`, never `PASS`. For a gating checker this slots into existing
  handling (refine: `UNCERTAIN:` → `needs-discussion`; plan/conformance: a refusal is treated as
  inconclusive, consuming a reconcile cycle rather than silently passing). For a shadow checker
  it maps to `SHADOW_STATUS: UNCERTAIN` (see below) and never blocks, delays, or retries the
  gating flow.
- **Read access:** the checker subagent needs `Glob`, `Grep`, and `Read` to explore the
  codebase it is reviewing — including the shadow subagent. No tool restriction is introduced
  or documented as existing beyond this — tool allow/deny changes are a separate, reviewed
  concern (CLAUDE.md).
- **Clone-live-first resolution (rubric docs only):** `conformance`'s and `code-review`'s
  reviewer rubrics, and `plan`'s Phase 3.5 conformance rubric, read the live clone first (e.g.
  `.claude/skills/conformance/RUBRIC.md`), falling back to the baked copy under
  `/opt/refinement-skills/*.md` only if the clone-live file is absent — this lets a target repo
  override a rubric without a factory image rebuild. The shadow subagent reads the identical
  resolved rubric text the gating subagent read for the same cycle; it is not a distinct
  rubric. `refine`'s product-owner prompt and `plan`'s Phase 3 architect prompt are **not**
  part of this pattern: they keep reading their baked `/opt/refinement-skills/{product-owner,
  architect}-prompt.md` copies as-is. This contract doc itself (`VERIFIER-CONTRACT.md`) is
  always read at its fixed baked path, `/opt/refinement-skills/VERIFIER-CONTRACT.md`, by all
  four commands — it is not itself subject to clone-live-first resolution.
- **Model-value note (Task 0, #394):** the pin strings above are the canonical model
  identifiers this contract, `config.yaml`, and every command file document. If the executing
  agent's Agent tool only accepts a short alias (e.g. `opus`/`fable`) rather than the literal
  ID string, translate the documented pin to its corresponding alias at the call site — this is
  the same translation every existing `claude-opus-4-8` pin already relies on wherever the
  underlying tool is alias-only; it is not a new mechanism introduced by the shadow trial.

## Shadow verdict mapping (non-gating)

When a shadow subagent is spawned (table above), map its response to four `SHADOW_*` lines —
mirroring `emit_verdict`'s four-field shape but under a distinct prefix so
`scripts/verdict_gate_check.sh` (`grep -m1 '^STATUS:'`) and
`scripts/factory_core/verdict.py::parse_verdict` (`line.startswith("STATUS:")`) never see or
act on them. Never omit any of the four lines, even when the shadow response is unparseable —
a missing field is indistinguishable from "shadow never ran" and corrupts the follow-up
comparison's denominator:

- `SHADOW_MODEL`: the resolved pin actually used for this spawn (e.g. `claude-fable-5-1`),
  regardless of verdict.
- `SHADOW_STATUS`: parse the shadow response's `**Verdict:**` line — `✅ Conforms` or
  `⚠️ Minor deviations` → `PASS`; `⛔ Material divergence` → `BLOCKED`; a tool error, timeout,
  refusal, or a response with no parseable `**Verdict:**` line → `UNCERTAIN`.
- `SHADOW_FINDINGS_COUNT`: the number of `[MINOR]`/`[MATERIAL]` bullets in the shadow
  response's Deviations section (`0` if "No deviations found."); best-effort `0` when
  `SHADOW_STATUS: UNCERTAIN`.
- `SHADOW_SEVERITY`: `high` if any `[MATERIAL]` bullet is present, `low` if only `[MINOR]`
  bullets are present, `none` if no deviations; best-effort `none` when
  `SHADOW_STATUS: UNCERTAIN`.

Keep the raw shadow response too, under a labelled "Shadow (Fable) Review" heading — the
structured lines are for mining, the prose is what an operator reads when adjudicating a
divergence.
```

4. Verify pass:
   ```bash
   python -m pytest tests/test_verifier_contract_doc_referenced.py -x -v
   ```
   Expected: all 8 tests pass (5 pre-existing + 3 new).

5. Commit:
   ```bash
   git add refinement-skills/VERIFIER-CONTRACT.md tests/test_verifier_contract_doc_referenced.py
   git commit -m "docs(#394): VERIFIER-CONTRACT per-checker pin table, refusal->UNCERTAIN, shadow mapping"
   ```

---

## Task 2: `config/config.yaml` — `shadow_model` baked default

**Files:** `config/config.yaml`, `tests/test_config_conformance_shadow_model.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_config_conformance_shadow_model.py
import pathlib
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_conformance_shadow_model_baked_default():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text())
    assert cfg["conformance"]["shadow_model"] == "claude-fable-5-1"


def test_conformance_block_unrelated_keys_unchanged():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text())
    conf = cfg["conformance"]
    assert conf["enabled"] is True
    assert conf["max_reconcile_cycles"] == 3
    assert conf["block_on_material"] is True
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_config_conformance_shadow_model.py -x -v
   ```
   Expected: `test_conformance_shadow_model_baked_default` fails with a `KeyError`-driven
   `AssertionError` (no `shadow_model` key yet); the second test passes already (asserting it
   here pins the block's other keys so this task's edit can't accidentally perturb them).

3. Implement — in `config/config.yaml`, under the `conformance:` block, add one line after
   `backlog_label`:

```yaml
conformance:
  enabled: true
  max_reconcile_cycles: 3
  block_on_material: true
  scope_enforcement: true    # detect & remediate out-of-scope changes at the conformance gate
  excise_out_of_scope: true  # revert out-of-scope changes from the branch (false = file backlog ticket only)
  backlog_label: scope-spillover  # label applied to auto-created spillover tickets
  shadow_model: claude-fable-5-1  # env: CONFORMANCE_SHADOW_MODEL overrides (unset falls back to this; explicitly empty = no shadow spawn)
```

4. Verify pass:
   ```bash
   python -m pytest tests/test_config_conformance_shadow_model.py -x -v
   ```

5. Commit:
   ```bash
   git add config/config.yaml tests/test_config_conformance_shadow_model.py
   git commit -m "feat(#394): bake conformance.shadow_model default (claude-fable-5-1)"
   ```

---

## Task 3: `commands/dark-factory-plan.md` — Phase 3.5 shadow spawn + Phase 4 comment fold

**Files:** `commands/dark-factory-plan.md`, `tests/test_plan_command_shadow_conformance.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_plan_command_shadow_conformance.py
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"


def test_phase_3_5_resolves_shadow_model_pin():
    text = CMD.read_text(encoding="utf-8")
    assert "CONFORMANCE_SHADOW_MODEL" in text
    assert "SHADOW_MODEL_PIN" in text


def test_phase_3_5_first_pass_spawns_shadow_after_opus():
    text = CMD.read_text(encoding="utf-8")
    opus_pos = text.find('`description`: "Conformance review: plan vs spec (cycle N)"')
    shadow_pos = text.find('`description`: "Conformance shadow (fable): plan vs spec (cycle N)"')
    assert opus_pos != -1 and shadow_pos != -1
    assert opus_pos < shadow_pos, "shadow spawn must be documented after the gating Opus spawn"


def test_reconcile_loop_mirrors_shadow_spawn():
    text = CMD.read_text(encoding="utf-8")
    assert "SHADOW_DIALOGUE" in text
    # reconcile loop step 8 area must reference re-spawning the shadow subagent, not just the
    # first pass, per Requirement 2 ("mirrors every Opus spawn, including reconcile re-spawns")
    reconcile_idx = text.find("**Reconcile loop** (only if MATERIAL)")
    assert reconcile_idx != -1
    assert "SHADOW_DIALOGUE" in text[reconcile_idx:]


def test_shadow_dialogue_never_feeds_conformance_dialogue():
    text = CMD.read_text(encoding="utf-8")
    # CONFORMANCE_DIALOGUE assignments/appends must never read from SHADOW_DIALOGUE
    assert "CONFORMANCE_DIALOGUE=\"$SHADOW_DIALOGUE\"" not in text
    assert "CONFORMANCE_DIALOGUE=\"${SHADOW_DIALOGUE" not in text


def test_publish_comment_includes_shadow_subsection():
    text = CMD.read_text(encoding="utf-8")
    assert "### Shadow (Fable) Review" in text
    assert "SHADOW_STATUS" in text


def test_inline_opus_pin_count_unchanged():
    text = CMD.read_text(encoding="utf-8")
    assert text.count("claude-opus-4-8") >= 2  # unchanged from test_verifier_contract_doc_referenced.py::test_every_command_file_keeps_inline_model_pin
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_plan_command_shadow_conformance.py -x -v
   ```
   Expected: all but the last assertion fail (no shadow text exists yet).

3. Implement. In `commands/dark-factory-plan.md`, Phase 3.5, replace steps 2-3 and the spawn/
   reconcile blocks as follows.

   Replace:
   ```
   2. Determine `MAX_CYCLES` from `conformance.max_reconcile_cycles` (default: 3)
   3. Set `CONFORMANCE_DIALOGUE=""` and `CONFORMANCE_CYCLE=0`
   4. Build the artifact content: the plan document text is `$PLAN_CONTENT`
   5. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: plan vs spec (cycle N)"
      - `model`: `claude-opus-4-8` — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
      - `prompt`: `RUBRIC_CONTENT` (resolved in step 1) with:
        - `$ARTIFACT_KIND` replaced with `PLAN`
        - `$SPEC_CONTENT` replaced with the spec file contents
        - `$ARTIFACT_CONTENT` replaced with `$PLAN_CONTENT`
   6. Append the subagent's output to `CONFORMANCE_DIALOGUE`
   ```

   With:
   ```
   2. Determine `MAX_CYCLES` from `conformance.max_reconcile_cycles` (default: 3)
   3. Set `CONFORMANCE_DIALOGUE=""`, `SHADOW_DIALOGUE=""`, and `CONFORMANCE_CYCLE=0`
   4. Build the artifact content: the plan document text is `$PLAN_CONTENT`
   4a. Resolve the shadow model pin (Requirement 7: env explicitly set, even to empty, wins;
       unset falls back to the config default — `${VAR-default}`, not `${VAR:-default}`, so an
       explicit empty string is preserved rather than replaced):
       ```bash
       SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
       SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
       ```
   5. Spawn a conformance reviewer subagent using the Agent tool:
      - `description`: "Conformance review: plan vs spec (cycle N)"
      - `model`: `claude-opus-4-8` — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn too)
      - `prompt`: `RUBRIC_CONTENT` (resolved in step 1) with:
        - `$ARTIFACT_KIND` replaced with `PLAN`
        - `$SPEC_CONTENT` replaced with the spec file contents
        - `$ARTIFACT_CONTENT` replaced with `$PLAN_CONTENT`
   6. Append the subagent's output to `CONFORMANCE_DIALOGUE`
   6a. If `$SHADOW_MODEL_PIN` is non-empty, spawn a second, non-gating subagent immediately
       after, with the identical rubric/input the Opus spawn just saw:
      - `description`: "Conformance shadow (fable): plan vs spec (cycle N)"
      - `model`: `$SHADOW_MODEL_PIN`
      - `prompt`: identical `RUBRIC_CONTENT` with the same `$ARTIFACT_KIND`/`$SPEC_CONTENT`/
        `$ARTIFACT_CONTENT` substitution used for the Opus call this cycle
      - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
      - Any tool error, timeout, or refusal is caught here rather than propagated — it never
        blocks or delays the Opus verdict handling in step 7.
      - Derive `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY` from the
        response per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s shadow verdict mapping.
      - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE` —
        never merged; `CONFORMANCE_DIALOGUE` alone drives the verdict check in step 7).
      If `$SHADOW_MODEL_PIN` is empty, skip this step entirely — no `SHADOW_*` fields, no
      `SHADOW_DIALOGUE` append, for this cycle.
   ```

   Then in the **Reconcile loop** (step 8), replace:
   ```
      e. Re-spawn the conformance reviewer subagent (same prompt format, updated `$PLAN_CONTENT`)
      f. Append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator and `Cycle N:` header
      g. Parse verdict again → loop back to step 7
   ```
   With:
   ```
      e. Re-spawn the conformance reviewer subagent (same prompt format, updated `$PLAN_CONTENT`)
      f. Append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator and `Cycle N:` header
      f2. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (step 8e's shadow
          counterpart — same prompt format, updated `$PLAN_CONTENT`, identical to step 6a but for
          this reconcile cycle). Append its response to `SHADOW_DIALOGUE` with a `---` separator
          and `Cycle N:` header, mirroring `CONFORMANCE_DIALOGUE`'s cycle numbering one-to-one so
          a shadow cycle always pairs with the Opus cycle that produced the same-numbered plan
          revision. Update `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/
          `SHADOW_SEVERITY` from this cycle's response (best-effort `UNCERTAIN` on any error).
      g. Parse verdict again → loop back to step 7
   ```

   Finally, in Phase 4's "Plan Generated" comment template, immediately after the existing
   `## Spec Conformance` paragraph (the one starting "(Otherwise, include the full conformance
   reviewer output..."), add:
   ```
      (If `$SHADOW_MODEL_PIN` was non-empty for this run, append a subsection:)

      ### Shadow (Fable) Review

      ```
      SHADOW_MODEL: <value>
      SHADOW_STATUS: <value>
      SHADOW_FINDINGS_COUNT: <value>
      SHADOW_SEVERITY: <value>
      ```

      <full $SHADOW_DIALOGUE, with the same Cycle N: headers as the Architect/Conformance
      sections above>
   ```

4. Verify pass:
   ```bash
   python -m pytest tests/test_plan_command_shadow_conformance.py tests/test_verifier.py tests/test_plan_command_conformance_rubric_fallback.py tests/test_plan_command_context_pack.py -x -v
   ```
   Expected: all pass, including the pre-existing plan-command tests (unaffected sections).

5. Commit:
   ```bash
   git add commands/dark-factory-plan.md tests/test_plan_command_shadow_conformance.py
   git commit -m "feat(#394): shadow-spawn Fable alongside plan Phase 3.5 conformance reviewer"
   ```

---

## Task 4: `commands/dark-factory-conformance.md` — shadow spawn + artifact/comment changes

**Files:** `commands/dark-factory-conformance.md`, `tests/test_conformance_command_shadow_review.py` (new)

### TDD Steps

1. Write the failing test:

```python
# tests/test_conformance_command_shadow_review.py
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


def test_phase_1_resolves_shadow_model_pin():
    text = CMD.read_text(encoding="utf-8")
    assert "CONFORMANCE_SHADOW_MODEL" in text
    assert "SHADOW_MODEL_PIN" in text


def test_step_3_1_spawns_shadow_after_opus():
    text = CMD.read_text(encoding="utf-8")
    opus_pos = text.find('`description`: "Conformance review: code vs spec"')
    shadow_pos = text.find('`description`: "Conformance shadow (fable): code vs spec"')
    assert opus_pos != -1 and shadow_pos != -1
    assert opus_pos < shadow_pos


def test_reconcile_loop_mirrors_shadow_spawn():
    text = CMD.read_text(encoding="utf-8")
    reconcile_idx = text.find("## Phase 3.5: RECONCILE LOOP")
    assert reconcile_idx != -1
    assert "SHADOW_DIALOGUE" in text[reconcile_idx:]


def test_phase_3_6_oos_scan_reads_conformance_dialogue_only():
    text = CMD.read_text(encoding="utf-8")
    scope_idx = text.find("## Phase 3.6: SCOPE REMEDIATION")
    blocked_idx = text.find("## Phase 3.5: RECONCILE LOOP")
    section = text[scope_idx:blocked_idx] if scope_idx < blocked_idx else text[scope_idx:]
    assert "SHADOW_DIALOGUE" not in section, "Phase 3.6 OOS scan must never read shadow output"


def test_phase_4_pass_emits_shadow_fields_and_marker_comment():
    text = CMD.read_text(encoding="utf-8")
    phase4_idx = text.find("## Phase 4: PASS")
    phase5_idx = text.find("## Phase 5: BLOCKED")
    section = text[phase4_idx:phase5_idx]
    assert "SHADOW_MODEL" in section
    assert "df-shadow-review" in section


def test_phase_5_blocked_folds_shadow_block():
    text = CMD.read_text(encoding="utf-8")
    phase5_idx = text.find("## Phase 5: BLOCKED")
    section = text[phase5_idx:]
    assert "SHADOW_MODEL" in section


def test_inline_opus_pin_count_unchanged():
    text = CMD.read_text(encoding="utf-8")
    assert text.count("claude-opus-4-8") >= 1
```

2. Verify fail:
   ```bash
   python -m pytest tests/test_conformance_command_shadow_review.py -x -v
   ```
   Expected: all but the last assertion fail.

3. Implement, in `commands/dark-factory-conformance.md`:

   a. **Phase 1 LOAD** — after the existing step 11 ("Determine `ISSUE_NUM` from
      `$ARTIFACTS_DIR/issue.json`..."), add a new step 12 (order relative to step 11 doesn't
      matter — neither depends on the other; it is simply appended after it):
      ```
      12. Resolve the shadow model pin (Requirement 7 — explicit-empty-wins-over-unset):
          ```bash
          SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
          SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
          ```
      ```

   b. **Phase 3, Step 3.1** — replace:
      ```
      3. Set `CONFORMANCE_CYCLE=0` and `CONFORMANCE_DIALOGUE=""`

      4. Spawn a conformance reviewer subagent using the Agent tool:
         - `description`: "Conformance review: code vs spec"
         - `model`: `claude-opus-4-8` — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
         - `prompt`: `RUBRIC_CONTENT` (resolved in Phase 1 step 3) with:
           - `$ARTIFACT_KIND` replaced with `IMPLEMENTATION`
           - `$SPEC_CONTENT` replaced with the spec file contents (or issue body if `NO_SPEC=true`)
           - `$ARTIFACT_CONTENT` replaced with the artifact content from Step 3.1

      5. Append the subagent's output to `CONFORMANCE_DIALOGUE`
      ```
      With:
      ```
      3. Set `CONFORMANCE_CYCLE=0`, `CONFORMANCE_DIALOGUE=""`, and `SHADOW_DIALOGUE=""`

      4. Spawn a conformance reviewer subagent using the Agent tool:
         - `description`: "Conformance review: code vs spec"
         - `model`: `claude-opus-4-8` — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
         - `prompt`: `RUBRIC_CONTENT` (resolved in Phase 1 step 3) with:
           - `$ARTIFACT_KIND` replaced with `IMPLEMENTATION`
           - `$SPEC_CONTENT` replaced with the spec file contents (or issue body if `NO_SPEC=true`)
           - `$ARTIFACT_CONTENT` replaced with the artifact content from Step 3.1

      5. Append the subagent's output to `CONFORMANCE_DIALOGUE`

      5a. If `$SHADOW_MODEL_PIN` is non-empty, spawn a second, non-gating subagent immediately
          after, with the identical rubric/input the Opus spawn just saw:
         - `description`: "Conformance shadow (fable): code vs spec"
         - `model`: `$SHADOW_MODEL_PIN`
         - `prompt`: identical `RUBRIC_CONTENT` with the same `$ARTIFACT_KIND`/`$SPEC_CONTENT`/
           `$ARTIFACT_CONTENT` substitution used for the Opus call in step 4
         - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
         - Any tool error, timeout, or refusal is caught here — it never blocks, delays, or
           retries the Opus verdict handling in step 7, and never feeds Phase 3.6's `[OOS]` scan.
         - Derive `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY` from
           the response per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s shadow verdict mapping.
           Also capture `SHADOW_VERDICT_LINE` — the raw `**Verdict:** ...` line from the shadow
           response verbatim (or `**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)` if none
           was found), for Requirement 5's "structured fields **and** the shadow verdict line" in
           the durable comment.
         - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE`).
         If `$SHADOW_MODEL_PIN` is empty, skip this step entirely for this cycle.
      ```
      (Step 3.6's re-run of "Step 3.1 again" per 3.6.4 naturally re-executes 5a too — no
      separate edit needed there; Phase 3.6 itself only ever reads `$CONFORMANCE_DIALOGUE`,
      confirmed unchanged by this task.)

   c. **Phase 3.5: RECONCILE LOOP** — replace:
      ```
      6. Re-spawn the conformance reviewer subagent (same prompt format, updated diff)
      7. Prepend `Cycle $CONFORMANCE_CYCLE:` header and append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator
      8. Parse verdict again → loop back to step 1
      ```
      With:
      ```
      6. Re-spawn the conformance reviewer subagent (same prompt format, updated diff)
      7. Prepend `Cycle $CONFORMANCE_CYCLE:` header and append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator
      7a. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (mirroring Step
          3.1's 5a for this cycle, `$ARTIFACT_KIND=IMPLEMENTATION`, updated diff). Prepend
          `Cycle $CONFORMANCE_CYCLE:` and append its response to `SHADOW_DIALOGUE` with a `---`
          separator, mirroring `CONFORMANCE_DIALOGUE`'s cycle numbering one-to-one. Update
          `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY`/
          `SHADOW_VERDICT_LINE` from this cycle's response (best-effort `UNCERTAIN` on any error).
      8. Parse verdict again → loop back to step 1
      ```

   d. **Phase 4: PASS** — after the existing `emit_verdict`/printf block that writes
      `conformance.md`, add:
      ```bash
      if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
        {
          printf "SHADOW_MODEL: %s\nSHADOW_STATUS: %s\nSHADOW_FINDINGS_COUNT: %s\nSHADOW_SEVERITY: %s\n" \
            "${SHADOW_MODEL_PIN}" "${SHADOW_STATUS:-UNCERTAIN}" "${SHADOW_FINDINGS_COUNT:-0}" "${SHADOW_SEVERITY:-none}"
          printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"
        } >> "$ARTIFACTS_DIR/conformance.md"

        FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
        SHADOW_BODY="<!-- df-shadow-review -->
      ## Shadow (Fable) Review — conformance

      ${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

      \`\`\`
      SHADOW_MODEL: ${SHADOW_MODEL_PIN}
      SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
      SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
      SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
      \`\`\`

      ---
      $FOOTER"
        TMPFILE=$(mktemp /tmp/df-shadow-review-XXXXXX.md)
        printf '%s' "$SHADOW_BODY" > "$TMPFILE"
        python3 dark-factory/scripts/factory_core/providers/cli.py tracker comment \
          --id "$ISSUE_NUM" --marker "<!-- df-shadow-review -->" --body-file "$TMPFILE"  # TARGET-PATH
        rm -f "$TMPFILE"
      fi
      ```
      (This is the new durable PASS-path comment Requirement 5 calls for — Phase 4 PASS today
      posts no issue comment at all, so this is additive, gated entirely on the shadow having
      actually run.)

   e. **Phase 5: BLOCKED** — in the existing `gh issue comment` body, immediately before the
      `### Next Steps` line, add (only rendered when the shadow ran):
      ```
      <!-- If SHADOW_MODEL_PIN was non-empty, insert before ### Next Steps: -->
      ## Shadow (Fable) Review — conformance

      ${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

      \`\`\`
      SHADOW_MODEL: $SHADOW_MODEL_PIN
      SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
      SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
      SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
      \`\`\`

      $SHADOW_DIALOGUE
      ```
      and in the `conformance.md` BLOCKED write, mirror Phase 4's append (same `if [ -n
      "${SHADOW_MODEL_PIN:-}" ]` block, appended after the existing `emit_verdict`/printf).

4. Verify pass:
   ```bash
   python -m pytest tests/test_conformance_command_shadow_review.py tests/test_conformance_command_rubric_fallback.py tests/test_conformance_dedupe_step.py tests/test_conformance_formatter_step.py tests/test_conformance_rubric_baked_fallback_runtime.py -x -v
   ```
   Expected: all pass.

5. Commit:
   ```bash
   git add commands/dark-factory-conformance.md tests/test_conformance_command_shadow_review.py
   git commit -m "feat(#394): shadow-spawn Fable alongside conformance Phase 3 reviewer, PASS-path marker comment"
   ```

---

## Task 5: Golden-corpus regression — prove `SHADOW_*` lines don't perturb verdict parsing

This is the one task that tests real Python code (not just prose), directly validating
Requirement 3 (non-interference) against `scripts/factory_core/verdict.py` and
`scripts/factory_core/run_record.py`.

**Files:** `tests/test_verdict.py`, `tests/fixtures/verdicts/conformance__pass_with_shadow.md` (new),
`tests/fixtures/verdicts/conformance__pass_with_shadow.expected.json` (new)

### TDD Steps

1. Add the new fixture pair. Byte-for-byte mirror Task 4d's real write order for
   `conformance.md`: the existing `emit_verdict`/printf block first (unchanged), **then** the
   `SHADOW_*` block appended by the new `if [ -n "${SHADOW_MODEL_PIN:-}" ]` clause — so this
   fixture is not just schema-plausible, it is the literal shape the edited command will
   actually produce:

```
# tests/fixtures/verdicts/conformance__pass_with_shadow.md
STATUS: PASS
GATE_TYPE: conformance
FINDINGS_COUNT: 0
SEVERITY: none
VERDICT: Conforms
CYCLES: 0
NO_SPEC: false
OOS_EXCISED: 0
OOS_TICKETS: 

---

Conforms.
SHADOW_MODEL: claude-fable-5-1
SHADOW_STATUS: BLOCKED
SHADOW_FINDINGS_COUNT: 1
SHADOW_SEVERITY: high

---

## Shadow (Fable) Review

Material divergence: shadow reviewer flagged a deviation Opus did not.
```

```json
{"stage": "conformance", "verdict": "PASS", "cycles": 0}
```

   This fixture is deliberately adversarial: the shadow verdict (`BLOCKED`/`high`) *disagrees*
   with the gating verdict (`PASS`) — proving the parser's `verdict`/`cycles` result is driven
   only by the first `STATUS:`/`CYCLES:` lines, completely unaffected by the `SHADOW_*` lines
   or the disagreeing shadow prose that follows, regardless of where in the file those lines
   land (both `verdict.parse_verdict` and `_parse_artifact_stage`'s conformance overlay scan
   every line by prefix — position-independent — so this placement is not merely "safe", it is
   the actual real layout Task 4d produces).

2. Update the golden-corpus count in `tests/test_verdict.py`:
   ```python
   def test_golden_corpus_byte_compat():
       md_files = sorted(_FIXTURES_DIR.glob("*.md"))
       assert len(md_files) == 18, "golden corpus fixture count changed unexpectedly"
   ```
   (was `17`)

3. Verify fail first (add the fixtures and the count bump, but confirm the *reasoning* — run
   before editing `verdict.py`/`run_record.py`, which this task does not touch, to prove no
   production code change is needed):
   ```bash
   cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_verdict.py -x -v
   ```
   Expected at this point (fixtures added, count bumped, no code touched yet): **already
   passing** — this is the intended outcome, since Requirement 3's non-interference claim
   means the existing parser needs no change. If it fails, that is a real regression signal:
   inspect whether `SHADOW_STATUS:`/`SHADOW_SEVERITY:` lines are being matched by `STATUS:`/
   `SEVERITY:`'s `line.startswith(...)` checks (they should not be, since `"SHADOW_STATUS:"
   .startswith("STATUS:")` is `False` — only fails if a future refactor changes this to
   substring or regex matching).

4. No implementation step: this task is a proof, not a code change. Run the full suite once
   more to confirm nothing else broke:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_verdict.py tests/test_verifier.py -v
   ```

5. Commit:
   ```bash
   git add tests/test_verdict.py tests/fixtures/verdicts/conformance__pass_with_shadow.md tests/fixtures/verdicts/conformance__pass_with_shadow.expected.json
   git commit -m "test(#394): golden-corpus fixture proving SHADOW_* lines don't perturb verdict parsing"
   ```

---

## Task 6: Full-suite verification and self-review

**Files:** none (verification only).

### Steps

1. Run the full test suite exactly as CI does:
   ```bash
   cd /workspace/dark-factory
   python -m pytest tests/ -v
   ```
   Expected: all tests pass, including every test touched in Tasks 1-5 and every pre-existing
   test this plan's edits are adjacent to (`test_verifier.py`,
   `test_plan_command_conformance_rubric_fallback.py`,
   `test_conformance_command_rubric_fallback.py`, `test_conformance_skill_files.py`,
   `test_budget_enforce_dag.py`, `test_context_budget.py`).

2. Run the smoke gate and workflow DAG checks (executed directly in this checkout — the real
   tracked path, not the `dark-factory/` TARGET-PATH prefix used inside command-file prose):
   ```bash
   bash smoke_gate.sh
   python3 scripts/check_workflow_dag.py
   ```
   Expected: both pass — this ticket adds no DAG node and changes no `workflows/
   archon-dark-factory.yaml` content, so the DAG check should be a no-op confirmation.

3. Self-review checklist (per Requirements, re-verified against the diff):
   - [ ] `.claude/skills/**` (including `RUBRIC.md` and its baked mirror
     `refinement-skills/conformance-reviewer-prompt.md`) has zero diff — `git diff origin/main
     HEAD -- .claude/skills/ refinement-skills/conformance-reviewer-prompt.md` is empty (two-dot
     form, not three-dot: three-dot would include commits `main` merged independently after this
     branch diverged, producing false-positive diffs on files actually identical to `main`). (R8)
     `tests/test_conformance_skill_files.py::test_rubric_matches_source_prompt_content` already
     enforces the mirror byte-for-byte on every run, so this check is a confirmation, not new
     coverage.
   - [ ] `gate_*`, breaker, budget, and tool-permission config (`config/config.yaml`'s
     `blast_radius`, `token_optimization`, `epic_autopilot`, `side_effect` blocks) has zero
     diff outside the one added `conformance.shadow_model` line.
   - [ ] `CONFORMANCE_DIALOGUE` and `SHADOW_DIALOGUE` are never assigned from each other
     anywhere in the two edited command files (`grep -n 'CONFORMANCE_DIALOGUE.*SHADOW_DIALOGUE\|SHADOW_DIALOGUE.*CONFORMANCE_DIALOGUE'` on both files returns nothing beyond
     comment prose explaining the separation).
   - [ ] Every shadow spawn site emits all four `SHADOW_*` lines even under `UNCERTAIN` (no
     silent omission path).
   - [ ] The plan's own issue-number line (`**Issue:** #394`) is present at the top of this
     document, and every task step above has an exact file path and a real code block (no
     "TBD"/"similar to Task N" placeholders).

4. Commit any residual fixups from the self-review individually (each with its own `git
   commit`), per this repo's TDD convention of one commit per verified change.

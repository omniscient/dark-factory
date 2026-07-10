# Plan: Dark Factory Prompt Surface Inventory — Re-verification & Correction

**Issue:** omniscient/dark-factory#41 — Inventory Dark Factory prompt surface and existing Claude
Skills actuals
**Spec:** [docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md](../specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md)

## Goal

Per the spec's own Q1 ruling (A1: "Use a Markdown table embedded in the spec as the primary
deliverable, not new sub-issues"), the deliverable for this `size: S` ticket already exists:
Table 1 (migration map, ~35 rows) and Table 2 (prompt-surface analysis, 14 rows) inside the spec
file. This plan does **not** add a new document, script, or sub-issue — that would duplicate
work the spec explicitly rejected (see spec "Alternatives Considered" #1 and #2).

The spec's own Assumptions section flags that its numbers are a point-in-time snapshot ("re-run
before citing exact numbers in implementation tickets, since several files ... mutate on every
factory run"). This plan re-runs that verification now, exhaustively, using the same methodology
the spec cites (`scripts/token_estimate.py`, `char/4`) — **every** row in Table 1 is recomputed
against the current file(s) on disk, not a hand-picked subset. (An initial draft of this plan
checked only 7 pre-selected rows and asserted the rest were accurate without checking; that was
wrong — a full sweep shows 33 of the 35 rows have drifted by small amounts, most within 1-2%.
This revision corrects that: the verification script below covers every row and every category
subtotal.)

## Architecture

This is a documentation-accuracy ticket: no backend, frontend, database, or API changes. The only
artifact touched is the already-committed spec file
`docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md`. Because there
is no application code to test, the "write failing test → implement → verify pass" TDD shape from
`CLAUDE.md`/`architect-prompt.md` conventions is adapted mechanically: the "test" is a
`token_estimate.py`-based comparison of every spec-recorded figure against the actual current
file(s), the "failure" is a printed `DRIFT` line for each mismatched row, and "implementation" is
a scripted, anchor-verified replacement of each drifted cell, re-verified by re-running the same
comparison with the corrected expected values.

No new permanent test file or script is added — the numbers are an explicit point-in-time snapshot
by the spec's own Assumptions section, and a permanent CI assertion pinning exact token counts of
prose files would just re-break every time any of these ~72 files is next edited for unrelated
reasons. The correction script is run inline (`python3 -c "..."` / heredoc) and not committed.

## Tech Stack

- Python 3 stdlib (`scripts/token_estimate.py::estimate_tokens`, already shipped by #36/#153)
- `grep` / `bash` for occurrence-count and structural-claim verification
- `gh issue list` for the #36/#40 cross-reference verification

## File Structure

| File | Change |
|---|---|
| `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md` | Modified — token-count corrections across all of Table 1 and its aggregate footnote, one prose occurrence-count correction |

No other files are created or modified.

---

## Task 1: Exhaustively re-verify and correct every Table 1 token count

Every single-file and grouped row in Table 1 is checked against the current file(s) on disk. This
supersedes any partial/sampled check — the goal (per the spec's Assumptions section) is that the
merged spec's numbers reflect what's actually on disk today, not a subset chosen in advance.

**Files:**
- `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md`

### Step 1 — Write the full verification check and confirm it fails (drift detected)

```bash
python3 -c "
import sys, glob
sys.path.insert(0, 'scripts')
from token_estimate import estimate_tokens

def tok(path):
    with open(path, 'r', encoding='utf-8') as f:
        return estimate_tokens(f.read())

singles = [
    ('workflows/archon-dark-factory.yaml', 16813),
    ('commands/dark-factory-refine.md', 2482),
    ('commands/dark-factory-plan.md', 2426),
    ('commands/dark-factory-implement.md', 4592),
    ('commands/dark-factory-conformance.md', 5377),
    ('commands/dark-factory-code-review.md', 2288),
    ('commands/dark-factory-revise-advisory.md', 1176),
    ('commands/dark-factory-validate.md', 2024),
    ('commands/ceiling-revisit.md', 1698),
    ('refinement-skills/SKILL.md', 392),
    ('refinement-skills/orchestrator-prompt.md', 934),
    ('refinement-skills/product-owner-prompt.md', 512),
    ('refinement-skills/architect-prompt.md', 706),
    ('refinement-skills/conformance-reviewer-prompt.md', 1274),
    ('refinement-skills/code-review-reviewer-prompt.md', 859),
    ('entrypoint.sh', 8598),
    ('scheduler.sh', 12682),
    ('smoke_gate.sh', 1283),
    ('scripts/context_budget.py', 3992),
    ('scripts/context_pack.py', 3676),
    ('scripts/architecture_slice.py', 5302),
    ('scripts/comment_digest.py', 2161),
    ('scripts/diff_rank.py', 5287),
    ('scripts/memory_retrieve.py', 6316),
    ('config/config.yaml', 2492),
    ('.factory/adapter.yaml', 418),
    ('archon-config.yaml', 18),
    ('README.md', 3283),
    ('.archon/memory/dark-factory-ops.md', 4452),
]
groups = [
    ('memory-scripts', ['scripts/memory_write.py','scripts/memory_import.py','scripts/memory_maintain.py','scripts/gate_lib.sh','scripts/load_memory_context.sh'], 10742),
    ('gate-support', ['scripts/gate_blast_radius.py','scripts/code_review_payload.py','scripts/dedupe_oos.py','scripts/fmt_hunk_filter.py'], 6441),
    ('eval-ops', ['scripts/eval_agentmemory.sh','scripts/eval_memory_quality.py','scripts/fetch_scorecard.py','scripts/ceiling_revisit.py','scripts/budget_enforce.py'], 17975),
    ('ci-utils', ['scripts/check_workflow_dag.py','scripts/check_workflow_when.py','scripts/identity.sh','scripts/hooks.sh','scripts/agent_roles.sh','scripts/check_preview_creds.sh','scripts/oos_excise.sh','scripts/token_estimate.py','scripts/iii-config.agentmemory.yaml'], 4032),
    ('factory_core', sorted(glob.glob('scripts/factory_core/*.py')), 23237),
    ('docs-reference', ['docs/domain.md','docs/cutover-markethawk.md','docs/dark-factory-token-optimization.md','docs/dark-factory-memory-contract.md','docs/triage-labels.md','docs/parity-p1.md','docs/parity-p2.md'], 18888),
]

drift_count = 0
for path, expected in singles:
    actual = tok(path)
    status = 'match' if actual == expected else 'DRIFT'
    if status == 'DRIFT':
        drift_count += 1
    print(f'{path}: spec={expected} actual={actual} [{status}]')
for name, paths, expected in groups:
    actual = sum(tok(p) for p in paths)
    status = 'match' if actual == expected else 'DRIFT'
    if status == 'DRIFT':
        drift_count += 1
    print(f'{name} ({len(paths)} files): spec={expected} actual={actual} [{status}]')
print(f'--- {drift_count}/{len(singles)+len(groups)} rows drifted ---')
"
```

Expected output (33 of 35 rows drifted; only `scripts/context_budget.py` and `archon-config.yaml`
still match):

```
workflows/archon-dark-factory.yaml: spec=16813 actual=16763 [DRIFT]
commands/dark-factory-refine.md: spec=2482 actual=2469 [DRIFT]
commands/dark-factory-plan.md: spec=2426 actual=2409 [DRIFT]
commands/dark-factory-implement.md: spec=4592 actual=4573 [DRIFT]
commands/dark-factory-conformance.md: spec=5377 actual=5351 [DRIFT]
commands/dark-factory-code-review.md: spec=2288 actual=2280 [DRIFT]
commands/dark-factory-revise-advisory.md: spec=1176 actual=1169 [DRIFT]
commands/dark-factory-validate.md: spec=2024 actual=2017 [DRIFT]
commands/ceiling-revisit.md: spec=1698 actual=1689 [DRIFT]
refinement-skills/SKILL.md: spec=392 actual=389 [DRIFT]
refinement-skills/orchestrator-prompt.md: spec=934 actual=931 [DRIFT]
refinement-skills/product-owner-prompt.md: spec=512 actual=507 [DRIFT]
refinement-skills/architect-prompt.md: spec=706 actual=705 [DRIFT]
refinement-skills/conformance-reviewer-prompt.md: spec=1274 actual=1261 [DRIFT]
refinement-skills/code-review-reviewer-prompt.md: spec=859 actual=853 [DRIFT]
entrypoint.sh: spec=8598 actual=8575 [DRIFT]
scheduler.sh: spec=12682 actual=12655 [DRIFT]
smoke_gate.sh: spec=1283 actual=1279 [DRIFT]
scripts/context_budget.py: spec=3992 actual=3992 [match]
scripts/context_pack.py: spec=3676 actual=3675 [DRIFT]
scripts/architecture_slice.py: spec=5302 actual=4958 [DRIFT]
scripts/comment_digest.py: spec=2161 actual=2159 [DRIFT]
scripts/diff_rank.py: spec=5287 actual=5281 [DRIFT]
scripts/memory_retrieve.py: spec=6316 actual=6015 [DRIFT]
config/config.yaml: spec=2492 actual=2481 [DRIFT]
.factory/adapter.yaml: spec=418 actual=416 [DRIFT]
archon-config.yaml: spec=18 actual=18 [match]
README.md: spec=3283 actual=3134 [DRIFT]
.archon/memory/dark-factory-ops.md: spec=4452 actual=4436 [DRIFT]
memory-scripts (5 files): spec=10742 actual=10627 [DRIFT]
gate-support (4 files): spec=6441 actual=7210 [DRIFT]
eval-ops (5 files): spec=17975 actual=17497 [DRIFT]
ci-utils (9 files): spec=4032 actual=4643 [DRIFT]
factory_core (13 files): spec=23237 actual=23075 [DRIFT]
docs-reference (7 files): spec=18888 actual=14339 [DRIFT]
--- 33/35 rows drifted ---
```

### Step 2 — Apply the corrections

Each correction is anchored on the row's unique source-cell text plus its old value, so a
mismatch (anchor not found, or found more than once) raises instead of silently doing nothing or
corrupting an unrelated row.

```bash
python3 -c "
path = 'docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# (anchor_old, anchor_new) — anchor_old must appear exactly once in the file.
replacements = [
    ('\`workflows/archon-dark-factory.yaml\` | 16,813', '\`workflows/archon-dark-factory.yaml\` | 16,763'),
    ('\`commands/dark-factory-refine.md\` | 2,482', '\`commands/dark-factory-refine.md\` | 2,469'),
    ('\`commands/dark-factory-plan.md\` | 2,426', '\`commands/dark-factory-plan.md\` | 2,409'),
    ('\`commands/dark-factory-implement.md\` | 4,592', '\`commands/dark-factory-implement.md\` | 4,573'),
    ('\`commands/dark-factory-conformance.md\` | 5,377', '\`commands/dark-factory-conformance.md\` | 5,351'),
    ('\`commands/dark-factory-code-review.md\` | 2,288', '\`commands/dark-factory-code-review.md\` | 2,280'),
    ('\`commands/dark-factory-revise-advisory.md\` | 1,176', '\`commands/dark-factory-revise-advisory.md\` | 1,169'),
    ('\`commands/dark-factory-validate.md\` | 2,024', '\`commands/dark-factory-validate.md\` | 2,017'),
    ('\`commands/ceiling-revisit.md\` | 1,698', '\`commands/ceiling-revisit.md\` | 1,689'),
    ('\`refinement-skills/SKILL.md\` | 392', '\`refinement-skills/SKILL.md\` | 389'),
    ('\`refinement-skills/orchestrator-prompt.md\` | 934', '\`refinement-skills/orchestrator-prompt.md\` | 931'),
    ('\`refinement-skills/product-owner-prompt.md\` | 512', '\`refinement-skills/product-owner-prompt.md\` | 507'),
    ('\`refinement-skills/architect-prompt.md\` | 706', '\`refinement-skills/architect-prompt.md\` | 705'),
    ('\`refinement-skills/conformance-reviewer-prompt.md\` | 1,274', '\`refinement-skills/conformance-reviewer-prompt.md\` | 1,261'),
    ('\`refinement-skills/code-review-reviewer-prompt.md\` | 859', '\`refinement-skills/code-review-reviewer-prompt.md\` | 853'),
    ('\`entrypoint.sh\` | 8,598', '\`entrypoint.sh\` | 8,575'),
    ('\`scheduler.sh\` | 12,682', '\`scheduler.sh\` | 12,655'),
    ('\`smoke_gate.sh\` | 1,283', '\`smoke_gate.sh\` | 1,279'),
    ('\`scripts/context_pack.py\` | 3,676', '\`scripts/context_pack.py\` | 3,675'),
    ('\`scripts/architecture_slice.py\` | 5,302', '\`scripts/architecture_slice.py\` | 4,958'),
    ('\`scripts/comment_digest.py\` | 2,161', '\`scripts/comment_digest.py\` | 2,159'),
    ('\`scripts/diff_rank.py\` | 5,287', '\`scripts/diff_rank.py\` | 5,281'),
    ('\`scripts/memory_retrieve.py\` | 6,316', '\`scripts/memory_retrieve.py\` | 6,015'),
    ('(mirrored, comment-stripped, at \`.claude/skills/refinement/config.yaml\` for runtime path compatibility — same values, see gap above) | 2,492', '(mirrored, comment-stripped, at \`.claude/skills/refinement/config.yaml\` for runtime path compatibility — same values, see gap above) | 2,481'),
    ('\`.factory/adapter.yaml\` | 418', '\`.factory/adapter.yaml\` | 416'),
    ('\`README.md\` | 3,283', '\`README.md\` | 3,134'),
    ('\`.archon/memory/dark-factory-ops.md\` | 4,452', '\`.archon/memory/dark-factory-ops.md\` | 4,436'),
    ('\`load_memory_context.sh\` | ~10,742', '\`load_memory_context.sh\` | ~10,627'),
    ('fmt_hunk_filter}.py\` | ~6,441', 'fmt_hunk_filter}.py\` | ~7,210'),
    ('budget_enforce}.py\` | ~17,975', 'budget_enforce}.py\` | ~17,497'),
    ('\`iii-config.agentmemory.yaml\` | ~4,032', '\`iii-config.agentmemory.yaml\` | ~4,643'),
    ('\`scripts/factory_core/\` (13 files) | 23,237', '\`scripts/factory_core/\` (13 files) | 23,075'),
    ('(7 files) | 18,888 total (~2,700 avg)', '(7 files) | 14,339 total (~2,048 avg)'),
]

for old, new in replacements:
    count = content.count(old)
    assert count == 1, f'anchor matched {count} times (expected 1): {old!r}'
    content = content.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'applied {len(replacements)} corrections')
"
```

Note: `load_memory_context.sh\`}`, `` `fmt_hunk_filter`}.py` ``, `` `budget_enforce`}.py` `` and
`` `iii-config.agentmemory.yaml` `` anchors above must match the literal grouped-cell text in the
spec (backtick-wrapped filename immediately followed by the closing brace and ` | ~<value>`) —
read the current row text with the Read tool immediately before running this step and adjust the
anchor punctuation to match exactly if the table's cell formatting differs from what's shown here,
since the `assert count == 1` guard will fail loudly (not silently corrupt the file) if it doesn't.

Expected output:

```
applied 33 corrections
```

(33 — matches the 33 `[DRIFT]` rows from Step 1; `scripts/context_budget.py` and
`archon-config.yaml` were already correct and have no entry in `replacements`.)

### Step 3 — Re-run the verification check with corrected expected values and confirm it passes

Re-run the Step 1 script with every `singles`/`groups` expected value updated to its `actual` value
from the Step 1 output. Expected output: `--- 0/35 rows drifted ---`, with every row printing
`[match]`.

### Step 4 — Commit

```bash
git add docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md
git commit -m "docs(spec): re-verify and correct all #41 Table 1 token counts against current files"
```

---

## Task 2: Recompute the aggregate footnote from the corrected rows

The category tally directly below Table 1 must be recomputed from the same corrected data, not
by hand-deriving deltas — that was the second issue with the initial draft of this plan (it
adjusted only the categories touched by its 7 sampled rows and left `phase-procedure` stale even
though all 15 of its files had drifted).

**Files:**
- `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md`

### Step 1 — Compute the five category totals from the now-corrected files and confirm the footnote is stale

```bash
python3 -c "
import sys, glob
sys.path.insert(0, 'scripts')
from token_estimate import estimate_tokens

def tok(path):
    with open(path, 'r', encoding='utf-8') as f:
        return estimate_tokens(f.read())

phase_procedure_files = [
    'workflows/archon-dark-factory.yaml','commands/dark-factory-refine.md','commands/dark-factory-plan.md',
    'commands/dark-factory-implement.md','commands/dark-factory-conformance.md','commands/dark-factory-code-review.md',
    'commands/dark-factory-revise-advisory.md','commands/dark-factory-validate.md','commands/ceiling-revisit.md',
    'refinement-skills/SKILL.md','refinement-skills/orchestrator-prompt.md','refinement-skills/product-owner-prompt.md',
    'refinement-skills/architect-prompt.md','refinement-skills/conformance-reviewer-prompt.md','refinement-skills/code-review-reviewer-prompt.md',
]
deterministic_singles = ['entrypoint.sh','scheduler.sh','smoke_gate.sh','scripts/context_budget.py','scripts/context_pack.py',
    'scripts/architecture_slice.py','scripts/comment_digest.py','scripts/diff_rank.py','scripts/memory_retrieve.py']
deterministic_groups = [
    ['scripts/memory_write.py','scripts/memory_import.py','scripts/memory_maintain.py','scripts/gate_lib.sh','scripts/load_memory_context.sh'],
    ['scripts/gate_blast_radius.py','scripts/code_review_payload.py','scripts/dedupe_oos.py','scripts/fmt_hunk_filter.py'],
    ['scripts/eval_agentmemory.sh','scripts/eval_memory_quality.py','scripts/fetch_scorecard.py','scripts/ceiling_revisit.py','scripts/budget_enforce.py'],
    ['scripts/check_workflow_dag.py','scripts/check_workflow_when.py','scripts/identity.sh','scripts/hooks.sh','scripts/agent_roles.sh','scripts/check_preview_creds.sh','scripts/oos_excise.sh','scripts/token_estimate.py','scripts/iii-config.agentmemory.yaml'],
    sorted(glob.glob('scripts/factory_core/*.py')),
]
security_files = ['config/config.yaml', '.factory/adapter.yaml']
always_files = ['archon-config.yaml', 'README.md']
large_ref_files = ['.archon/memory/dark-factory-ops.md']
large_ref_group = ['docs/domain.md','docs/cutover-markethawk.md','docs/dark-factory-token-optimization.md','docs/dark-factory-memory-contract.md','docs/triage-labels.md','docs/parity-p1.md','docs/parity-p2.md']

pp_total = sum(tok(p) for p in phase_procedure_files)
det_total = sum(tok(p) for p in deterministic_singles) + sum(sum(tok(p) for p in g) for g in deterministic_groups)
det_files = len(deterministic_singles) + sum(len(g) for g in deterministic_groups)
sec_total = sum(tok(p) for p in security_files)
always_total = sum(tok(p) for p in always_files)
lr_total = sum(tok(p) for p in large_ref_files) + sum(tok(p) for p in large_ref_group)
lr_files = len(large_ref_files) + len(large_ref_group)

print(f'phase-procedure: {pp_total} tok across {len(phase_procedure_files)} files (spec footnote: 43,559 across 15 files)')
print(f'deterministic-script: {det_total} tok across {det_files} files (spec footnote: 113,039 across 45 files)')
print(f'security-sensitive-config: {sec_total} tok across {len(security_files)} files (spec footnote: 2,910 across 2 files)')
print(f'always-needed-fact: {always_total} tok across {len(always_files)} present files (spec footnote: 3,301 across 2 present files)')
print(f'large-reference: {lr_total} tok across {lr_files} files (spec footnote: 18,888 across 8 files)')
"
```

Expected output:

```
phase-procedure: 43366 tok across 15 files (spec footnote: 43,559 across 15 files)
deterministic-script: 111641 tok across 45 files (spec footnote: 113,039 across 45 files)
security-sensitive-config: 2897 tok across 2 files (spec footnote: 2,910 across 2 files)
always-needed-fact: 3152 tok across 2 present files (spec footnote: 3,301 across 2 present files)
large-reference: 18775 tok across 8 files (spec footnote: 18,888 across 8 files)
```

All five totals are stale relative to the corrected rows.

### Step 2 — Correct the footnote

In `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md`, change:
```
*(Total across present, in-scope files: ~72 surfaces, phase-procedure ≈43,559 tok across 15 files,
deterministic-script ≈113,039 tok across 45 files (many grouped above), security-sensitive-config
≈2,910 tok across 2 files, always-needed-fact ≈3,301 tok across 2 present files (+2 phantom),
large-reference ≈18,888 tok across 8 files. Deterministic scripts dominate raw byte count but are
never loaded into an LLM context wholesale — they only matter for factory maintainability, not
prompt budget, which is why the previous run's category tally emphasized phase-procedure /
always-needed-fact / large-reference as the actual budget-relevant categories.)*
```
to:
```
*(Total across present, in-scope files: ~72 surfaces, phase-procedure ≈43,366 tok across 15 files,
deterministic-script ≈111,641 tok across 45 files (many grouped above), security-sensitive-config
≈2,897 tok across 2 files, always-needed-fact ≈3,152 tok across 2 present files (+2 phantom),
large-reference ≈18,775 tok across 8 files. Deterministic scripts dominate raw byte count but are
never loaded into an LLM context wholesale — they only matter for factory maintainability, not
prompt budget, which is why the previous run's category tally emphasized phase-procedure /
always-needed-fact / large-reference as the actual budget-relevant categories. Re-verified
2026-07-10 against every row in Table 1, not a sample; see Assumptions.)*
```

### Step 3 — Re-run the Step 1 script and confirm the footnote now matches

Re-run the Task 2 Step 1 script; the printed actual totals must equal the footnote values now in
the file (43,366 / 111,641 / 2,897 / 3,152 / 18,775). No further code change — this step is a
read-only confirmation.

### Step 4 — Commit

```bash
git add docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md
git commit -m "docs(spec): recompute #41 aggregate footnote from all corrected Table 1 rows"
```

---

## Task 3: Verify structural/qualitative claims and correct the one prose drift

The spec makes three kinds of claims outside Table 1's token column that are mechanically
checkable: (a) the `dark-factory/scripts` and `.claude/skills/refinement/config.yaml` path-prefix
occurrence counts cited in prose, (b) the #36/#40 issue cross-references in Table 1's "Related
issue" column, and (c) the falsifiable per-prompt claims in Table 2 (missing `MAX_CYCLES`,
missing Verification section, presence of the `UNCERTAIN:` escape hatch). Only (a) has drifted.

**Files:**
- `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md`

### Step 1 — Write the verification checks and confirm the occurrence-count check fails

```bash
echo "dark-factory/scripts occurrences:"
grep -c "dark-factory/scripts" commands/*.md workflows/*.yaml entrypoint.sh 2>/dev/null \
  | awk -F: '{sum+=$2} END {print sum}'

echo "config.yaml path occurrences:"
grep -c "\.claude/skills/refinement/config\.yaml" commands/*.md workflows/*.yaml entrypoint.sh 2>/dev/null \
  | awk -F: '{sum+=$2} END {print sum}'
```

Expected output (spec prose claims 42 and 18 respectively):

```
dark-factory/scripts occurrences:
41
config.yaml path occurrences:
18
```

The `dark-factory/scripts` count has drifted by one (42 → 41); the config-path count already
matches — leave it unchanged.

```bash
echo "Table 2 spot-checks:"
grep -c "MAX_CYCLES" commands/dark-factory-validate.md
grep -c "^## Verification" commands/dark-factory-revise-advisory.md
grep -c "UNCERTAIN:" refinement-skills/product-owner-prompt.md
```

Expected output (confirms Table 2's three most load-bearing claims — validate.md's uncapped loop,
revise-advisory.md's missing verification step, product-owner-prompt.md's anti-rationalization
guard — are all still accurate; no correction needed for Table 2):

```
Table 2 spot-checks:
0
0
1
```

```bash
gh issue list --repo omniscient/dark-factory --state all --json number,state --limit 200 \
  | python3 -c "
import json, sys
data = {d['number']: d['state'] for d in json.load(sys.stdin)}
mismatches = []
for n in range(153, 165):
    if data.get(n) != 'CLOSED':
        mismatches.append((n, 'expected CLOSED', data.get(n)))
for n in range(42, 50):
    if data.get(n) != 'OPEN':
        mismatches.append((n, 'expected OPEN', data.get(n)))
print('mismatches:', mismatches if mismatches else 'NONE')
"
```

Expected output (confirms Table 1's #36-closed / #40-open cross-reference claims still hold; no
correction needed):

```
mismatches: NONE
```

### Step 2 — Correct the one drifted prose count

In the "A note on repo layout vs. what the prompt surface assumes" section, change:
```
still reference a `dark-factory/scripts/...` path prefix (42 occurrences) and a
```
to:
```
still reference a `dark-factory/scripts/...` path prefix (41 occurrences) and a
```

### Step 3 — Add a re-verification note to the Assumptions section

Change:
```
- Token estimates use `floor(len(text) / 4)` via `scripts/token_estimate.py`, computed 2026-07-10;
  re-run before citing exact numbers in implementation tickets, since several files (memory,
  config) mutate on every factory run.
```
to:
```
- Token estimates use `floor(len(text) / 4)` via `scripts/token_estimate.py`, computed 2026-07-10;
  re-run before citing exact numbers in implementation tickets, since several files (memory,
  config) mutate on every factory run.
- Re-verified 2026-07-10 (plan phase) against every row in Table 1 and its aggregate footnote,
  not a sample: 33 of 35 rows had drifted by small amounts (most 0.3%-2%; the largest were
  `scripts/architecture_slice.py`, `scripts/memory_retrieve.py`, the `gate-support`/`ci-utils`
  script bundles, and the `docs-reference` bundle, which had a second bug — its stated total was
  actually the combined docs+memory-file figure, not the 7-file subtotal) and were corrected in
  place, along with the footnote's five category totals. One prose occurrence count
  (`dark-factory/scripts` path prefix: 42 → 41) was also corrected. The
  `.claude/skills/refinement/config.yaml` occurrence count, all Table 2 qualitative claims, and
  the #36/#40 issue cross-references were re-checked and found accurate — no changes made.
```

### Step 4 — Re-run the occurrence-count check and confirm it now matches the corrected spec text

```bash
grep -o "path prefix ([0-9]* occurrences)" \
  docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md
```

Expected output:

```
path prefix (41 occurrences)
```

### Step 5 — Commit

```bash
git add docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md
git commit -m "docs(spec): correct #41 path-prefix occurrence count, record re-verification in Assumptions"
```

---

## Completion Checklist

- [ ] Task 1: All 35 Table 1 rows re-verified against current disk state; all 33 drifted rows corrected (33 scripted replacements — `context_budget.py` and `archon-config.yaml` already matched, no entry needed)
- [ ] Task 2: All five aggregate-footnote category totals recomputed from the corrected rows (not manually derived deltas) and confirmed to match
- [ ] Task 3: `dark-factory/scripts` occurrence count corrected; Assumptions section documents the exhaustive re-verification; all other spot-checks confirmed accurate with no false corrections
- [ ] All three commits made on the current branch
- [ ] No files outside `docs/superpowers/specs/2026-07-10-dark-factory-prompt-surface-inventory-design.md` touched (per A1: no new sub-issues, no migrations implemented in this ticket)

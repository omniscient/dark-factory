# Plan: Raise doc-slicing component-resolution hit-rate (issue #18)

Spec: `docs/superpowers/specs/2026-07-06-doc-slicing-component-resolution-design.md`

## Goal

Add a fourth, lowest-confidence component-inference signal — derived from the issue's own
title/body text — to `infer_component()` in `scripts/architecture_slice.py`, and thread it
through the existing call chain (`slice_architecture()` → `assemble_pack()` →
`build_budget()`) so it actually gets exercised at `refine`/`plan`/`implement` time and by
`evals/token_opt_eval.py`. This is the only candidate that can move the eval-measured
component-resolution hit-rate (currently 22.7%, target ≥60%), because the eval harness only
ever passes `labels` and the issue JSON (title/body) to the resolver — never `changed_files`
or `spec_file`/`spec_component`.

## Architecture

Resolution order (new signal appended at the end, lowest confidence):

```
changed_files prefixes → spec_file keywords → labels → issue-text paths (Tier A)
  → issue-text keywords (Tier B) → None (full-doc fallback)
```

`infer_component_from_text(text)` implements the new signal as two ordered sub-tiers over
`text.lower()`:

- **Tier A (path-shaped):** literal substring match against the exact prefixes already in
  `_FILE_PREFIX_MAP` (`backend/app/`, `frontend/src/`, `dark-factory/`, `docker-compose`).
- **Tier B (bare keyword):** only reached when Tier A's match set is empty or ambiguous.
  Tokenize with `re.findall(r"[a-z0-9]+", lowered)` and intersect against the existing
  `_SPEC_KEYWORD_MAP` frozensets.
- **Ambiguity rule:** if a tier's match set spans more than one distinct component, that tier
  resolves to "no match" — Tier A falls through to Tier B; Tier B falls through to `None`
  (full-doc fallback). Never guess.

No new keyword vocabulary, no new I/O, no changes to `_check_safety_fallback` (it already
runs after component resolution regardless of which signal resolved it) and no changes to
`.factory/adapter.yaml` or the component→section map.

`assemble_pack()` (`context_pack.py`) and `build_budget()` (`context_budget.py`) already
receive an `issue_json` path on every call site (both already read it for the `issue_context`
section / labels). Both gain an explicit `issue_text: str | None = None` parameter; when the
caller leaves it at the default `None`, they extract `title` + `body` from that same
`issue_json` file internally via a shared `_read_issue_text()` helper. No new CLI flags are
needed at the `context_budget.py`/`context_pack.py` level — their CLIs never set this
parameter, so every real DAG/CLI invocation gets the value "for free" by routing what's
already loaded, exactly as the spec directs.

The explicit parameter (rather than unconditional internal-only derivation) matters for one
caller: `evals/token_opt_eval.py`. Its `eval_issue_scenario()` passes the **same**
`issue_json` file to both the "baseline" and "optimized" `_run_assemble()` calls, and
deliberately forces `labels=[]` on the baseline call so it stays `component_unresolved` (a
proxy for pre-slicing, full-doc behavior used to compute `savings_pct`). If `assemble_pack()`
derived `issue_text` unconditionally from `issue_json` with no way to opt out, the baseline
run would *also* resolve a component whenever the issue text names one — collapsing
`savings_pct` toward zero for exactly the issues this ticket is meant to help, and silently
invalidating the eval's headline savings metric even though the hit-rate/safety numbers would
still look fine. `_run_assemble()` therefore gets a `suppress_issue_text: bool = False`
parameter that passes `issue_text=""` (an explicit non-`None` override, so the auto-derive
path is skipped) on the baseline call only; the optimized call leaves it at the default and
picks up the real text automatically. See Task 6.

`architecture_slice.py`'s own CLI gets a new `--issue-text` flag (single combined string,
consistent with the existing single-value `--spec-file`/`--spec-component` flags) for direct
CLI use.

## Tech Stack

Python 3 stdlib (`re`, `json`, `argparse`), pytest for tests — matches the existing style of
`scripts/architecture_slice.py`, `scripts/context_pack.py`, `scripts/context_budget.py`.

## Non-goals (explicit — do not implement in this ticket)

- Label-vocabulary enrichment (adding new component labels / self-labelling in `refine`).
- `spec_component` propagation from spec documents (the parameter already exists end-to-end
  but nothing populates it — out of scope, requires a spec-format change).
- Multi-component union slices for issues that touch two components.
- Linked-PR / `fixes #N` scanning (`infer_component()` must stay a pure function with no I/O).
- Writing dark-factory's own `ARCHITECTURE.md` (unrelated prerequisite for dark-factory's own
  future issues; the eval corpus this ticket targets is MarketHawk's).
- New GitHub API calls or new CLI flags for `evals/token_opt_eval.py` /
  `context_budget.py` / `context_pack.py` — `assemble_pack()` already receives the
  `issue_json` path it needs and derives `issue_text` from it automatically by default.
  (`evals/token_opt_eval.py` does get one small, targeted change — see Task 6 — to keep its
  baseline/optimized comparison meaningful; that is a same-file internal-parameter change, not
  a new external input.)

**Reminder for implementation** (from refine-phase memory on this issue, `.archon/memory/architecture.md`):
the top-level `dark-factory/` subdirectory in a fresh self-target clone is NOT part of this
repo — it's materialized at container start from the factory image and git-cleaned after the
run. Edit the top-level `scripts/`, `evals/`, and `tests/` directories (as this plan does),
never anything under `dark-factory/`.

## File Structure

| File | Change |
|---|---|
| `scripts/architecture_slice.py` | Add `infer_component_from_text()`; extend `infer_component()` with `issue_text` param (step 4); extend `slice_architecture()` with `issue_text` param; add `--issue-text` CLI flag |
| `scripts/context_budget.py` | Add `_read_issue_text()` helper; add explicit `issue_text` param to `build_budget()` (auto-derived from `issue_json` when `None`); thread resolved value into its `slice_architecture()` call |
| `scripts/context_pack.py` | Import `_read_issue_text` from `context_budget`; add explicit `issue_text` param to `assemble_pack()` (same auto-derive-when-`None` behavior); thread resolved value into its `slice_architecture()` call |
| `evals/token_opt_eval.py` | Add `suppress_issue_text` param to `_run_assemble()`; set it on the baseline call in `eval_issue_scenario()` so baseline stays `component_unresolved` regardless of issue text |
| `tests/test_architecture_slice.py` | New tests: Tier A, Tier B, ambiguity fallback, precedence (existing signals beat text) |
| `tests/test_context_budget.py` | New integration tests: `build_budget()` resolves component from issue text; labels still win over text |
| `tests/test_context_pack.py` | New integration test: `assemble_pack()` resolves component from issue text end-to-end |
| `tests/test_token_opt_eval.py` | New test: `suppress_issue_text=True` keeps `_run_assemble()`'s manifest `component_unresolved` even when issue text names a component |

---

## Task 1: `infer_component_from_text()` — Tier A / Tier B / ambiguity rule

### Files
- `tests/test_architecture_slice.py`
- `scripts/architecture_slice.py`

### Step 1.1 — Write failing tests

In `tests/test_architecture_slice.py`, insert the following block immediately after
`test_infer_component_from_spec_file` (i.e., right before the `# ── 5: Slice metadata`
section comment at line 269):

```python
# ── 4b: infer_component_from_text (Tier A / Tier B / ambiguity) ──────────────

def test_infer_component_from_text_tier_a_path_match():
    assert aslice.infer_component_from_text(
        "cleanup(grafana): fix indicator util in frontend/src/utils/indicator.py"
    ) == "frontend"


def test_infer_component_from_text_tier_b_keyword_match():
    assert aslice.infer_component_from_text(
        "Investigate a scanner regression affecting the backend api"
    ) == "backend"


def test_infer_component_from_text_ambiguous_returns_none():
    text = "Touches both backend/app/routers/scanner.py and frontend/src/components/Scanner.tsx"
    assert aslice.infer_component_from_text(text) is None


def test_infer_component_from_text_no_match_returns_none():
    assert aslice.infer_component_from_text("Fix a typo in the README") is None
```

### Step 1.2 — Verify tests fail

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py -q -k "infer_component_from_text"
```

Expected output: 4 errors, each `AttributeError: module 'architecture_slice' has no attribute
'infer_component_from_text'`.

### Step 1.3 — Implement

In `scripts/architecture_slice.py`, insert the new function immediately before the
`infer_component` definition (after the `# ── Component inference ──` comment at line 245):

```python
def infer_component_from_text(text: str) -> str | None:
    """Infer a component from free-form issue title/body text.

    Lowest-confidence signal: only consulted by infer_component() after
    changed_files, spec_file, and labels all fail to resolve. Two ordered
    sub-tiers; within either tier, a match set spanning more than one
    component is ambiguous and resolves to "no match" (never guess).
    """
    lowered = text.lower()

    # Tier A: path-shaped literal substring match (same prefixes changed_files uses)
    tier_a = {component for prefix, component in _FILE_PREFIX_MAP if prefix in lowered}
    if len(tier_a) == 1:
        return next(iter(tier_a))

    # Tier B: bare keyword token match — only reached when Tier A found 0 or >1 components
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    tier_b = {component for keyword_set, component in _SPEC_KEYWORD_MAP if tokens & keyword_set}
    if len(tier_b) == 1:
        return next(iter(tier_b))

    return None
```

### Step 1.4 — Verify tests pass

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py -q -k "infer_component_from_text"
```

Expected output: `4 passed`.

### Step 1.5 — Commit

```bash
git add scripts/architecture_slice.py tests/test_architecture_slice.py
git commit -m "feat(architecture-slice): add issue-text component inference (Tier A/B)"
```

---

## Task 2: Wire `issue_text` into `infer_component()` (step 4, precedence-safe)

### Files
- `tests/test_architecture_slice.py`
- `scripts/architecture_slice.py`

### Step 2.1 — Write failing tests

Append to `tests/test_architecture_slice.py`, directly after the block added in Task 1:

```python
def test_infer_component_precedence_changed_files_beat_text():
    assert aslice.infer_component(
        spec_file=None,
        changed_files=["backend/app/routers/scanner.py"],
        labels=[],
        issue_text="frontend/src/components/Scanner.tsx cleanup",
    ) == "backend"


def test_infer_component_precedence_labels_beat_text():
    assert aslice.infer_component(
        spec_file=None,
        changed_files=[],
        labels=["frontend"],
        issue_text="backend/app/routers/scanner.py cleanup",
    ) == "frontend"


def test_infer_component_falls_back_to_text_when_other_signals_absent():
    assert aslice.infer_component(
        spec_file=None,
        changed_files=[],
        labels=[],
        issue_text="cleanup(grafana): move helper into frontend/src/utils/indicator.py",
    ) == "frontend"
```

### Step 2.2 — Verify tests fail

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py -q -k "precedence or falls_back_to_text"
```

Expected output: 3 errors — `TypeError: infer_component() got an unexpected keyword argument
'issue_text'`.

### Step 2.3 — Implement

In `scripts/architecture_slice.py`, change the `infer_component` signature and add step 4.
Replace:

```python
def infer_component(
    spec_file: str | None,
    changed_files: list[str] | None,
    labels: list[str] | None,
) -> str | None:
```

with:

```python
def infer_component(
    spec_file: str | None,
    changed_files: list[str] | None,
    labels: list[str] | None,
    issue_text: str | None = None,
) -> str | None:
```

and replace the function's final `return None` with:

```python
    # 4. Issue title/body text (lowest confidence; tiered path/keyword match)
    if issue_text:
        component = infer_component_from_text(issue_text)
        if component is not None:
            return component

    return None
```

### Step 2.4 — Verify tests pass

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py -q
```

Expected output: all tests in the file pass (previous 21 + 7 new = 28 passed).

### Step 2.5 — Commit

```bash
git add scripts/architecture_slice.py tests/test_architecture_slice.py
git commit -m "feat(architecture-slice): thread issue_text into infer_component() as step 4"
```

---

## Task 3: Thread `issue_text` through `slice_architecture()` + CLI flag

### Files
- `scripts/architecture_slice.py`

### Step 3.1 — Implement (no dedicated unit test — exercised indirectly by Task 4/5 integration tests and manual CLI check below)

In `scripts/architecture_slice.py`, change the `slice_architecture` signature. Replace:

```python
def slice_architecture(
    arch_path: str,
    scenario: str,
    spec_component: str | None = None,
    spec_file: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    clone_dir: str | None = None,
) -> SliceResult:
```

with:

```python
def slice_architecture(
    arch_path: str,
    scenario: str,
    spec_component: str | None = None,
    spec_file: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    issue_text: str | None = None,
    clone_dir: str | None = None,
) -> SliceResult:
```

Then, in the same function's body, replace:

```python
    # 1. Resolve component
    component = spec_component or infer_component(spec_file, changed_files, labels)
```

with:

```python
    # 1. Resolve component
    component = spec_component or infer_component(spec_file, changed_files, labels, issue_text)
```

Next, add the CLI flag. In `main()`, replace:

```python
    parser.add_argument("--labels", nargs="*", default=[])
    parser.add_argument("--clone-dir", default=None)
```

with:

```python
    parser.add_argument("--labels", nargs="*", default=[])
    parser.add_argument("--issue-text", default=None,
                        help="Issue title+body text, used as the lowest-confidence "
                             "component inference signal")
    parser.add_argument("--clone-dir", default=None)
```

And in the `slice_architecture(...)` call inside `main()`, replace:

```python
    result = slice_architecture(
        arch_path=args.arch_file,
        scenario=args.scenario,
        spec_component=args.spec_component,
        spec_file=args.spec_file,
        changed_files=args.changed_files,
        labels=args.labels,
        clone_dir=args.clone_dir,
    )
```

with:

```python
    result = slice_architecture(
        arch_path=args.arch_file,
        scenario=args.scenario,
        spec_component=args.spec_component,
        spec_file=args.spec_file,
        changed_files=args.changed_files,
        labels=args.labels,
        issue_text=args.issue_text,
        clone_dir=args.clone_dir,
    )
```

### Step 3.2 — Verify existing suite still passes

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py -q
```

Expected output: `28 passed` (unchanged from Task 2 — `slice_architecture`'s new param is
keyword-only-by-default and additive, so no existing call site breaks).

### Step 3.3 — Manual CLI sanity check

```bash
cd /workspace/dark-factory && cat > /tmp/ARCH_TEST.md <<'EOF'
# Architecture

## Frontend Architecture

Frontend content.

## Backend Module Map

Backend content.
EOF
python3 scripts/architecture_slice.py --arch-file /tmp/ARCH_TEST.md --scenario implement \
  --issue-text "cleanup(grafana): move helper into frontend/src/utils/indicator.py"
```

Expected output (stdout): a slice comment header containing `component=frontend` and
`included: Frontend Architecture`, followed by the `## Frontend Architecture` section body
only (no `## Backend Module Map`).

### Step 3.4 — Commit

```bash
git add scripts/architecture_slice.py
git commit -m "feat(architecture-slice): thread issue_text through slice_architecture() + CLI"
```

---

## Task 4: Wire `issue_text` into `context_budget.py`

### Files
- `tests/test_context_budget.py`
- `scripts/context_budget.py`

### Step 4.1 — Write failing tests

In `tests/test_context_budget.py`, insert after `test_architecture_md_fallback_status_when_component_unknown`
(i.e., right before the `# ── memory_context cap counts from trace ──` comment):

```python
# ── issue-text component inference wired into context_budget ────────────────

def make_issue_json_with_text(tmp_path, title, body, labels=None):
    data = {"title": title, "body": body, "labels": labels or [], "comments": []}
    p = tmp_path / "issue-text.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_architecture_md_resolves_component_from_issue_text(tmp_path):
    issue_json = make_issue_json_with_text(
        tmp_path,
        title="cleanup(grafana): fix indicator util",
        body="Move the helper into frontend/src/utils/indicator.py",
    )
    result = run_budget_with_arch(tmp_path, "implement", issue_json=issue_json)
    sec = result["sections"]["architecture_md"]
    assert sec["component"] == "frontend"
    assert sec["fallback"] is False
    assert "Frontend Architecture" in sec["included_sections"]


def test_architecture_md_labels_beat_issue_text(tmp_path):
    issue_json = make_issue_json_with_text(
        tmp_path,
        title="fix",
        body="Touches frontend/src/utils/indicator.py",
    )
    result = run_budget_with_arch(
        tmp_path, "implement", issue_json=issue_json, labels=["backend"],
    )
    sec = result["sections"]["architecture_md"]
    assert sec["component"] == "backend"
```

### Step 4.2 — Verify tests fail

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_context_budget.py -q -k "issue_text"
```

Expected output: 2 failed — `component_unresolved`/`fallback` assertions fail because
`build_budget()` does not yet extract or pass `issue_text`.

### Step 4.3 — Implement

In `scripts/context_budget.py`, add a helper immediately after `_read_json` (after line 81):

```python
def _read_issue_text(issue_json: str | None) -> str | None:
    """Combine issue title + body for architecture-slice text inference; None if unavailable."""
    data = _read_json(issue_json)
    if not data:
        return None
    combined = f"{data.get('title') or ''}\n{data.get('body') or ''}"
    return combined if combined.strip() else None
```

In `build_budget()`'s signature, replace:

```python
def build_budget(
    scenario: str,
    issue_num: int,
    run_id: str,
    clone_dir: str,
    out: str,
    artifacts_dir: str | None = None,
    spec_file: str | None = None,
    plan_file: str | None = None,
    memory_file: str | None = None,
    issue_json: str | None = None,
    impl_file: str | None = None,
    diff_file: str | None = None,
    spec_component: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    comment_digest_file: str | None = None,
) -> None:
```

with:

```python
def build_budget(
    scenario: str,
    issue_num: int,
    run_id: str,
    clone_dir: str,
    out: str,
    artifacts_dir: str | None = None,
    spec_file: str | None = None,
    plan_file: str | None = None,
    memory_file: str | None = None,
    issue_json: str | None = None,
    impl_file: str | None = None,
    diff_file: str | None = None,
    spec_component: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    issue_text: str | None = None,
    comment_digest_file: str | None = None,
) -> None:
```

`issue_text` defaults to `None`, which means "derive it from `issue_json`" — this is what
every real CLI/DAG call site does (`main()` never sets this kwarg). A caller that explicitly
passes a string (including `""`) overrides auto-derivation; `evals/token_opt_eval.py` uses
this in Task 6 to force the baseline run to ignore issue text.

In the function body, replace:

```python
    active = _SECTION_REGISTRY.get(scenario, [])
    sections: dict[str, dict] = {}
    source_hashes: dict[str, str] = {}

    for sec in active:
```

with:

```python
    active = _SECTION_REGISTRY.get(scenario, [])
    sections: dict[str, dict] = {}
    source_hashes: dict[str, str] = {}
    resolved_issue_text = issue_text if issue_text is not None else _read_issue_text(issue_json)

    for sec in active:
```

Then in the `architecture_md` branch, replace the `aslice.slice_architecture(...)` call:

```python
            result = aslice.slice_architecture(
                arch_path=arch_path,
                scenario=scenario,
                spec_component=spec_component,
                spec_file=spec_file,
                changed_files=changed_files,
                labels=labels,
                clone_dir=clone_dir,
            )
```

with:

```python
            result = aslice.slice_architecture(
                arch_path=arch_path,
                scenario=scenario,
                spec_component=spec_component,
                spec_file=spec_file,
                changed_files=changed_files,
                labels=labels,
                issue_text=resolved_issue_text,
                clone_dir=clone_dir,
            )
```

### Step 4.4 — Verify tests pass

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_context_budget.py -q
```

Expected output: all tests pass (previous count + 2 new).

### Step 4.5 — Commit

```bash
git add scripts/context_budget.py tests/test_context_budget.py
git commit -m "feat(context-budget): thread issue-text component inference into build_budget()"
```

---

## Task 5: Wire `issue_text` into `context_pack.py`

### Files
- `tests/test_context_pack.py`
- `scripts/context_pack.py`

### Step 5.1 — Write failing test

In `tests/test_context_pack.py`, insert after the `make_diff_file` helper (after line 44,
before `run_pack`):

```python
def make_arch_file(tmp_path):
    p = tmp_path / "ARCHITECTURE.md"
    p.write_text(
        "# Architecture\n\n"
        "## Backend Module Map\n\nBackend content.\n\n"
        "## Frontend Architecture\n\nFrontend content.\n\n"
        "## Service Topology\n\nTopology content.\n\n"
        "## Scan Execution Flow\n\nScan flow content.\n\n"
        "## Error Tracking System\n\nError tracking content.\n\n"
    )
    return str(p)


def make_issue_json_with_text(tmp_path, title, body):
    data = {"title": title, "body": body, "labels": [], "comments": []}
    p = tmp_path / "issue-text.json"
    p.write_text(json.dumps(data))
    return str(p)
```

Then append, at the end of the file:

```python
# ── issue-text component inference wired into context_pack ──────────────────

def test_architecture_md_component_resolved_from_issue_text(tmp_path):
    make_arch_file(tmp_path)
    issue_json = make_issue_json_with_text(
        tmp_path,
        title="cleanup(grafana): fix indicator util",
        body="Move the helper into frontend/src/utils/indicator.py",
    )
    manifest, md = run_pack(tmp_path, "implement", issue_json=issue_json)
    sec = manifest["sections"]["architecture_md"]
    assert sec["component"] == "frontend"
    assert sec["fallback"] is False
    assert "component=frontend" in md
```

### Step 5.2 — Verify test fails

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_context_pack.py -q -k "issue_text"
```

Expected output: 1 failed — `sec["component"]` is `None` (falls back to
`component_unresolved`) because `assemble_pack()` does not yet extract or pass `issue_text`.

### Step 5.3 — Implement

In `scripts/context_pack.py`, add `_read_issue_text` to the existing import from
`context_budget`. Replace:

```python
from context_budget import (
    _SECTION_REGISTRY,
    BUDGET_TOKENS,
    DIFF_LINE_CAP,
    _read_text,
    _SKILL_PROMPT_DIR,
    _SKILL_PROMPT_FILES,
)
```

with:

```python
from context_budget import (
    _SECTION_REGISTRY,
    BUDGET_TOKENS,
    DIFF_LINE_CAP,
    _read_text,
    _read_issue_text,
    _SKILL_PROMPT_DIR,
    _SKILL_PROMPT_FILES,
)
```

In `assemble_pack()`'s signature, replace:

```python
def assemble_pack(
    scenario: str,
    issue_num: int,
    run_id: str,
    clone_dir: str,
    out_md: str,
    out_json: str,
    artifacts_dir: str | None = None,
    spec_file: str | None = None,
    memory_file: str | None = None,
    issue_json: str | None = None,
    impl_file: str | None = None,
    diff_file: str | None = None,
    spec_component: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    comment_digest_file: str | None = None,
) -> None:
```

with:

```python
def assemble_pack(
    scenario: str,
    issue_num: int,
    run_id: str,
    clone_dir: str,
    out_md: str,
    out_json: str,
    artifacts_dir: str | None = None,
    spec_file: str | None = None,
    memory_file: str | None = None,
    issue_json: str | None = None,
    impl_file: str | None = None,
    diff_file: str | None = None,
    spec_component: str | None = None,
    changed_files: list[str] | None = None,
    labels: list[str] | None = None,
    issue_text: str | None = None,
    comment_digest_file: str | None = None,
) -> None:
```

Same auto-derive-when-`None` contract as `build_budget()` (Task 4): `main()` never sets this
kwarg, so every CLI call derives it from `issue_json` for free.

In the function body, replace:

```python
    active = _SECTION_REGISTRY.get(scenario, [])
    sections: dict[str, dict] = {}
    source_hashes: dict[str, str] = {}
    md_parts: list[str] = []

    for sec in active:
```

with:

```python
    active = _SECTION_REGISTRY.get(scenario, [])
    sections: dict[str, dict] = {}
    source_hashes: dict[str, str] = {}
    md_parts: list[str] = []
    resolved_issue_text = issue_text if issue_text is not None else _read_issue_text(issue_json)

    for sec in active:
```

Then in the `architecture_md` branch, replace:

```python
            result = aslice.slice_architecture(
                arch_path=arch_path,
                scenario=scenario,
                spec_component=spec_component,
                spec_file=spec_file,
                changed_files=changed_files,
                labels=labels,
                clone_dir=clone_dir,
            )
```

with:

```python
            result = aslice.slice_architecture(
                arch_path=arch_path,
                scenario=scenario,
                spec_component=spec_component,
                spec_file=spec_file,
                changed_files=changed_files,
                labels=labels,
                issue_text=resolved_issue_text,
                clone_dir=clone_dir,
            )
```

### Step 5.4 — Verify test passes

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_context_pack.py -q
```

Expected output: all tests pass (previous count + 1 new).

### Step 5.5 — Commit

```bash
git add scripts/context_pack.py tests/test_context_pack.py
git commit -m "feat(context-pack): thread issue-text component inference into assemble_pack()"
```

---

## Task 6: Preserve baseline/optimized isolation in `evals/token_opt_eval.py`

`eval_issue_scenario()` passes the *same* `issue_json` file to both its "baseline" and
"optimized" `_run_assemble()` calls, and deliberately forces `labels=[]` on the baseline call
so it stays `component_unresolved` (representing pre-slicing, full-doc behavior — the
denominator for `savings_pct`). Now that `assemble_pack()` auto-derives `issue_text` from
`issue_json` by default (Task 4/5), the baseline call would *also* resolve a component
whenever the issue text names one, silently collapsing `savings_pct` for exactly the issues
this ticket helps. This task closes that gap using the explicit-override parameter added in
Task 4/5, without adding any new GitHub calls or CLI flags.

### Files
- `tests/test_token_opt_eval.py`
- `evals/token_opt_eval.py`

### Step 6.1 — Write failing test

In `tests/test_token_opt_eval.py`, add `import json` to the top-of-file imports (alongside
the existing `glob`, `importlib.util`, `os`, `sys`), then append at the end of the file:

```python
def test_run_assemble_suppress_issue_text(tmp_path):
    """suppress_issue_text=True must prevent assemble_pack() from resolving a component via
    issue text, even when labels/spec_component are absent — this is what keeps
    eval_issue_scenario()'s baseline run representing true full-doc fallback."""
    mod = _load_module()
    arch_path = tmp_path / "ARCHITECTURE.md"
    arch_path.write_text(
        "# Architecture\n\n"
        "## Frontend Architecture\n\nFrontend content.\n\n"
    )
    issue_json_path = tmp_path / "issue.json"
    issue_json_path.write_text(json.dumps({
        "number": 1,
        "title": "cleanup(grafana): fix indicator util",
        "body": "Move the helper into frontend/src/utils/indicator.py",
        "comments": [],
        "labels": [],
    }))
    out_dir = str(tmp_path / "work")
    os.makedirs(out_dir, exist_ok=True)

    _, suppressed_manifest = mod._run_assemble(
        scenario="implement", issue_num=1, clone_dir=str(tmp_path),
        issue_json_path=str(issue_json_path), out_dir=out_dir,
        labels=[], spec_component=None, mode="suppressed",
        suppress_issue_text=True,
    )
    assert suppressed_manifest["sections"]["architecture_md"]["component"] is None

    _, unsuppressed_manifest = mod._run_assemble(
        scenario="implement", issue_num=1, clone_dir=str(tmp_path),
        issue_json_path=str(issue_json_path), out_dir=out_dir,
        labels=[], spec_component=None, mode="unsuppressed",
    )
    assert unsuppressed_manifest["sections"]["architecture_md"]["component"] == "frontend"
```

### Step 6.2 — Verify test fails

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_token_opt_eval.py -q -k "suppress_issue_text"
```

Expected output: `TypeError: _run_assemble() got an unexpected keyword argument
'suppress_issue_text'`.

### Step 6.3 — Implement

In `evals/token_opt_eval.py`, replace the `_run_assemble()` function:

```python
def _run_assemble(
    scenario: str,
    issue_num: int,
    clone_dir: str,
    issue_json_path: str,
    out_dir: str,
    labels: list[str] | None,
    spec_component: str | None,
    mode: str,
) -> tuple[str, dict]:
    """Call assemble_pack() for one scenario and return (md_text, manifest_dict).

    mode is 'baseline' or 'optimized' — used only for temp file naming.
    """
    out_md = os.path.join(out_dir, f"{issue_num}-{scenario}-{mode}.md")
    out_json = os.path.join(out_dir, f"{issue_num}-{scenario}-{mode}.json")
    assemble_pack(
        scenario=scenario,
        issue_num=issue_num,
        run_id=f"eval-{mode}",
        clone_dir=clone_dir,
        out_md=out_md,
        out_json=out_json,
        issue_json=issue_json_path,
        labels=labels,
        spec_component=spec_component,
    )
    with open(out_md, encoding="utf-8") as f:
        md_text = f.read()
    with open(out_json, encoding="utf-8") as f:
        manifest = json.load(f)
    return md_text, manifest
```

with:

```python
def _run_assemble(
    scenario: str,
    issue_num: int,
    clone_dir: str,
    issue_json_path: str,
    out_dir: str,
    labels: list[str] | None,
    spec_component: str | None,
    mode: str,
    suppress_issue_text: bool = False,
) -> tuple[str, dict]:
    """Call assemble_pack() for one scenario and return (md_text, manifest_dict).

    mode is 'baseline' or 'optimized' — used only for temp file naming.
    suppress_issue_text forces the architecture-slice issue-text signal off (used for the
    baseline run, which must stay component_unresolved regardless of what the issue body says
    — otherwise baseline and optimized would slice identically whenever the issue text alone
    would resolve a component, collapsing the reported savings_pct).
    """
    out_md = os.path.join(out_dir, f"{issue_num}-{scenario}-{mode}.md")
    out_json = os.path.join(out_dir, f"{issue_num}-{scenario}-{mode}.json")
    assemble_pack(
        scenario=scenario,
        issue_num=issue_num,
        run_id=f"eval-{mode}",
        clone_dir=clone_dir,
        out_md=out_md,
        out_json=out_json,
        issue_json=issue_json_path,
        labels=labels,
        spec_component=spec_component,
        issue_text="" if suppress_issue_text else None,
    )
    with open(out_md, encoding="utf-8") as f:
        md_text = f.read()
    with open(out_json, encoding="utf-8") as f:
        manifest = json.load(f)
    return md_text, manifest
```

Then, in `eval_issue_scenario()`, replace the baseline call:

```python
    # Baseline: no labels/component → component_unresolved → full-doc fallback
    baseline_text, baseline_manifest = _run_assemble(
        scenario=scenario,
        issue_num=issue_num,
        clone_dir=clone_dir,
        issue_json_path=issue_json_path,
        out_dir=tmp_dir,
        labels=[],
        spec_component=None,
        mode="baseline",
    )
```

with:

```python
    # Baseline: no labels/component/issue-text signal → component_unresolved → full-doc fallback
    baseline_text, baseline_manifest = _run_assemble(
        scenario=scenario,
        issue_num=issue_num,
        clone_dir=clone_dir,
        issue_json_path=issue_json_path,
        out_dir=tmp_dir,
        labels=[],
        spec_component=None,
        mode="baseline",
        suppress_issue_text=True,
    )
```

The "optimized" call directly below is unchanged — it leaves `suppress_issue_text` at its
default `False`, so `assemble_pack()` auto-derives `issue_text` from the same `issue_json`
and picks up the real title/body, satisfying requirement 6's "verify the optimized run's
component field actually changes for previously-unresolved corpus issues."

### Step 6.4 — Verify tests pass

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_token_opt_eval.py -q
```

Expected output: all tests pass (previous count + 1 new).

### Step 6.5 — Commit

```bash
git add evals/token_opt_eval.py tests/test_token_opt_eval.py
git commit -m "fix(token-opt-eval): isolate baseline run from issue-text component signal"
```

---

## Task 7: Full suite regression check + eval re-run

### Files
- None (verification only)

### Step 7.1 — Run the full test suite touched by this ticket

```bash
cd /workspace/dark-factory && python3 -m pytest tests/test_architecture_slice.py tests/test_context_pack.py tests/test_context_budget.py tests/test_token_opt_eval.py -v
```

Expected output: all tests pass, 0 failures. This confirms:
- The new signal doesn't change behavior when `changed_files`/`spec_file`/`labels` already
  resolve a component (precedence tests from Task 2).
- Safety-fallback behavior (`_check_safety_fallback`) is untouched — no test in
  `test_architecture_slice.py`'s "Fallback paths" section (`test_fallback_on_*`) was modified,
  and all still pass.
- The eval harness's baseline/optimized comparison stays meaningful (Task 6).

### Step 7.2 — Re-run the token-optimization eval to check the acceptance number

```bash
cd /workspace/dark-factory && python3 evals/token_opt_eval.py 2>&1 | tail -40
```

This requires `gh` CLI auth against `omniscient/markethawk` (and, per the spec's Assumptions
section, 12 of the 22 corpus issue numbers no longer resolve via `gh issue view` in either
`omniscient/markethawk` or `omniscient/dark-factory` as of the spec's writing — those will
`[skip]` regardless of this change). Report the resulting component-resolution hit-rate and
confirm:
- Hit-rate ≥60% (requirement 7), up from 22.7%.
- Safety verdicts unchanged: 100% `✅ PASS` / 0% `🔴 REGRESSION` (i.e. `section_at_risk`
  stays 0%).
- `savings_pct` for the baseline/optimized comparison still reflects real full-doc-vs-slice
  savings (Task 6's fix), not a collapsed number caused by the baseline also resolving a
  component.

If `gh` auth or network access is unavailable in the implementation environment, note this
explicitly in the implementation report rather than silently skipping the acceptance check —
per the spec's Open Questions section, if the full 22-issue hit-rate lands short of 60%, the
first thing to check is the bare `app/` vs `backend/app/` prefix gap called out there (issues
`#286`/`#632` in the spec's subsample), which is an explicitly deferred follow-up, not a bug
in this implementation.

### Step 7.3 — No commit (verification-only task)

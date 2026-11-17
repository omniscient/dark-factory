# Deterministic CI checks for reference docs: paths, line references, config keys

**Issue:** #537
**Status:** spec-pending-review (first refinement pass)
**Parent context:** split out of epic #536 (deterministic checks were originally scoped into
sibling ticket #535, which adds a separate, semantic/LLM-based in-run docs-conformance gate
before `push-and-pr`). #537 is explicitly CI-only and deterministic (no model call), and does not
depend on whether/when #535 ships.

## Overview / Problem Statement

`docs/run-lifecycle.md`, `docs/scheduler.md`, `docs/features.md` and `docs/configuration.md`
cite the codebase heavily — 506 backtick spans across the four docs match a strict
`` `path:line` ``/`` `path:line-line` `` pattern, plus 265 more bare-path backtick spans with no
line number. PR #533 had to re-map most of these after #519, #524 and #525 changed behavior
without going through a factory run (so an in-run gate like #535 would never have seen the
drift). The same class of drift will recur after any future change, factory-run or not, since
nothing today checks these docs deterministically in CI.

This ticket adds `scripts/docs_refs_check.py`, wired into `.github/workflows/ci.yml`, that
mechanically verifies four things against the actual repo state on every PR:
1. Every repo path named in backticks in the four docs exists.
2. Every `path:line` / `path:line-line` reference in the four docs is within the referenced
   file's current line count.
3. Every config key documented in `docs/configuration.md` exists in `config/config.yaml`, and
   every leaf key in `config/config.yaml` is documented there.
4. Every workflow node named in `docs/features.md` (via its "Node `x`" / "Nodes `x` ... and `y`"
   convention) exists in `workflows/archon-dark-factory.yaml`.

It adds a check only — no existing gate's behavior changes, and the script's own CI step uses a
plain non-zero exit on failure, same contract as `scripts/check_workflow_dag.py` and
`scripts/check_workflow_when.py`, which this script follows as its structural precedent.

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. **`scripts/docs_refs_check.py`** exposes a library function `check(doc_paths, repo_root,
   config_path, workflow_path) -> list[str]` (`[]` on success) and a `__main__` CLI that exits 1
   and prints every error if `check()` returns anything, 0 otherwise — the same shape as
   `check_workflow_dag.py`/`check_workflow_when.py`.
2. **Path + line-range check (AC1/AC2), applied to all four docs:**
   - Every backtick span of the form `` `PATH` `` or `` `PATH:LINESPEC` `` where `PATH` contains a
     path separator or ends in a known extension (`.py`, `.sh`, `.yaml`, `.yml`, `.md`) and
     `LINESPEC` is `N`, `N-M`, or a comma-joined list of either (e.g.
     `` `scheduler.sh:1061,1073,1080,1217,1228` ``) is extracted and checked.
   - `PATH` resolution: try the literal path relative to `repo_root` first. If that doesn't
     exist, fall back to a basename search rooted at `repo_root`, excluding `.git/`, the nested
     `dark-factory/` seed-fixture directory (this repo's own quirk — it contains a second copy of
     e.g. `scripts/factory_core/adapter.py`, so a bare `` `adapter.py:209-216` `` would otherwise
     resolve ambiguously), `node_modules/`, `.venv/`, and `__pycache__/`. Zero or more-than-one
     basename match is an error naming the ambiguity/absence and telling the doc author to write
     the full repo-relative path — never silently guessed.
   - The literal segment `workflow` (used as shorthand 24 times in `docs/features.md`, e.g.
     `` `workflow:408` ``) is a fixed alias for `workflows/archon-dark-factory.yaml`, resolved
     before the basename fallback.
   - `LINESPEC` validation is range-only: each cited line number must be `>= 1` and
     `<= ` the resolved file's current line count (ranges additionally require `N <= M`). This
     does **not** verify the line still contains what the prose claims it contains (no content
     anchor or symbol check) — see Open Questions.
   - **Explicitly out of scope for v1** (a documented gap, not a silent omission): a bare
     continuation span that carries no path of its own — e.g. a symbol name followed by a
     separate backtick span containing only `` `:586-591` `` that implicitly continues a path
     named earlier in the same sentence/cell. These are not extracted or checked.
3. **Config key check (AC3), `docs/configuration.md` only:**
   - Parse every markdown table row under the `## Layer 1: config/config.yaml` section whose
     first cell is a backtick-wrapped, dot-joined lowercase identifier (`^[a-z_]+(\.[a-z_]+)+$`,
     e.g. `` `scheduler.poll_interval` ``) as a documented key.
   - Load `config/config.yaml` and flatten it into dot-joined leaf paths — a leaf is any key
     whose value is not a mapping (both scalars and lists are leaves, since e.g.
     `refine.skip_labels`, a YAML list, is documented as one row, not expanded per item).
   - Report both directions: a documented key absent from the YAML (renamed/removed without a
     doc update), and a YAML leaf key absent from the doc (added without documentation). A key
     the doc marks **unread** is still a documented key for this check — presence, not
     read-ness, is what's being verified; "every key has a reader" is #528's separate scope.
4. **Workflow node check (AC4), `docs/features.md` only:**
   - Load `workflows/archon-dark-factory.yaml` and collect every `nodes[].id`.
   - Scan `docs/features.md` for the literal word `Node` or `Nodes` followed by one or more
     backtick-wrapped identifiers, chained by `, ` or ` and `, skipping parenthetical asides
     (e.g. `` (`workflow:408`) ``) that immediately follow a matched identifier, and stopping at
     the end of the sentence/table cell. This is a narrow, wording-anchored extraction, not a
     "hyphenated lowercase backtick span" heuristic — `docs/features.md` has ~64 distinct
     hyphenated lowercase backtick spans and only ~33 of them are node ids; the rest are labels
     (`spec-pending-review`, `direct-to-pr`), CLI subcommands, and hook names (`smoke-gate` is a
     hook, not a node), and `validate` itself names both a node and an unrelated hook depending on
     context. Only the `Node`/`Nodes` lead-in disambiguates reliably.
   - Report an error (with doc line and the unknown id) for any extracted identifier not in the
     DAG's id set. No reverse check — a real node never mentioned in `docs/features.md` is not an
     error; AC4 only requires named ones to exist.
   - Script paths (`scripts/*.py`, `scripts/*.sh`) and command paths (`commands/*.md`) named in
     `docs/features.md` are already covered by requirement 2 (they're backtick-wrapped paths);
     AC4 does not require a separate check for them. Bare shell-function/Python-symbol names
     (e.g. `` `fetch_board_items` ``, `` `run_hook` ``) are not "scripts" under this criterion and
     are out of scope — validating symbol-level references is a follow-up ticket's concern, not
     this one's.
5. **Tests:** `tests/fixtures/docs_refs_check/pass/` and `.../fail/` — small synthetic doc +
   `config.yaml` + workflow-yaml fixture trees (not the real docs), each exercising all four
   checks; `fail/` has exactly one deliberately broken reference per check category. A new
   `tests/test_docs_refs_check.py` asserts `check()` returns `[]` for `pass/` and exactly one
   categorized error per check for `fail/`. This runs under the existing
   `python -m pytest tests/ -v` CI contract (`PYTHONPATH=scripts`) — no new `tests/test_*.sh`
   wiring needed.
6. **CI wiring:** add one step to the existing `dag-check` job in `.github/workflows/ci.yml`:
   `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`.
   The job keeps its current name — it may be a required GitHub branch-protection status check
   today, and renaming it is an unrelated, riskier change outside this ticket's scope.
7. **The four docs must pass on `main` when this lands.** Per the #182 memory lesson (stale line
   citations are a recurring, already-observed failure mode in this repo), expect some of the
   existing ~506 strict-form refs are already broken; fixing every reference the finished checker
   flags is in scope for this ticket's implementation, confirmed by re-running the checker before
   the final commit.
8. **`docs/features.md` lists the check.** Its own `## CI` row (currently: "Pull requests run the
   Python suite, 27 shell tests, the two workflow DAG checks and a Docker build" /
   `scripts/check_workflow_dag.py`, `scripts/check_workflow_when.py`) is updated to also name
   `scripts/docs_refs_check.py`, following the row's existing format exactly.
9. **Out of scope:** the symbol-reference-vs-content-anchor drift check the issue raises as an
   open design question (deferred, see Open Questions); the bare `:N` continuation-span forms;
   any change to #535's semantic gate or any existing gate/breaker/budget; any adapter-driven doc
   list for non-self targets (that's a different ticket's follow-up, see Open Questions).

## Brainstorming Q&A

> **Q:** The issue asks for a deterministic check that every `path:line` reference is "in range,
> and still points at what it did when written." But the prose in `docs/features.md` and
> `docs/configuration.md` isn't uniformly `` `path:line` ``: it also has bare continuation spans
> (a symbol, then a separate `` `:586-591` `` span), comma-joined chains, and bare filenames that
> aren't repo-root-relative (`` `adapter.py:209-216` `` when the real file is
> `scripts/factory_core/adapter.py`). Given this must be a simple, deterministic,
> false-positive-free gate: should v1 validate only strict, unambiguous forms (documenting looser
> forms as an explicit gap), or also attempt the looser forms at the cost of fragility? And for
> drift detection specifically: is range-only validation (path exists + line in range, no
> semantic "still correct" check) acceptable for v1, given retrofitting content anchors or
> symbol references onto ~500 existing refs is a large lift disproportionate to this ticket?
>
> **A:** Strict forms only — including comma-joined chains, which name one path explicitly and
> aren't ambiguous. Bare `:N` continuation spans are out of scope for v1, documented as a known
> gap. Range-only drift validation is acceptable for v1; the anchor/symbol check is a follow-up
> ticket, not a blocker. Basename resolution: try the literal path first, fall back to a basename
> search; an ambiguous (0 or >1 match) result is an error telling the author to write the full
> path, never a silent guess — and the search must exclude this repo's own nested `dark-factory/`
> seed-fixture directory (which contains a second `adapter.py`) along with `.git`/`node_modules`
> noise, or real existing refs resolve as spuriously ambiguous. The four docs must pass on `main`
> when this merges; per the #182 memory lesson, expect some strict-form refs are already broken,
> and fixing them is in scope for this ticket.

> **Q:** AC4 says "every script, workflow node and command named in `docs/features.md` exists."
> Full-path script/command spans are already covered by the path-exists check, so the only
> genuinely new thing AC4 adds is workflow-node-id validation, since a node id is a bare
> identifier, not a path. `docs/features.md` names nodes with a fairly consistent "Node(s) `x`"
> lead-in, but also contains many other bare-identifier backtick spans that aren't node ids (env
> vars, constants, status tokens, function names). Should node validation be scoped narrowly to
> the "Node"/"Nodes" lead-in pattern, leaving every other bare identifier out of scope for all
> four checks — or is there a better signal to use?
>
> **A:** Scope it narrowly to the lead-in, with one small widening for lists (after "Nodes", also
> take the following comma/"and"-joined spans in the same sentence, skipping parenthetical
> `` (`workflow:NNN`) `` asides — otherwise "Nodes `refine` (`workflow:408`) and `refine-push`"
> would silently miss `refine-push`). A "hyphenated lowercase identifier" heuristic doesn't work:
> of ~64 such spans in `docs/features.md`, only ~33 are real node ids; the rest are labels, CLI
> subcommands and hook names (`smoke-gate` is a hook, not a node), and `validate` itself names
> both a node and an unrelated hook depending on context — only the doc's own wording
> disambiguates. Treat `workflow` as a path alias for `workflows/archon-dark-factory.yaml` so the
> 24 `` `workflow:NNN` `` spans get normal range validation rather than being wrongly reported as
> missing paths. Bare script names (e.g. `scheduler.sh`) go through the same basename fallback
> already agreed for the path check; shell functions and Python symbols are not "scripts" under
> this criterion and belong to the symbol-anchor follow-up, not this ticket.

## Architecture / Approach

**New file: `scripts/docs_refs_check.py`**

```
Usage (CI):
    python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md \
        docs/features.md docs/configuration.md

Usage (tests / library):
    from docs_refs_check import check
    errors = check(doc_paths, repo_root, config_path, workflow_path)   # [] on success

CLI flags (all optional, repo-relative defaults):
    --repo-root PATH   (default: ".")
    --config PATH      (default: "<repo-root>/config/config.yaml")
    --workflow PATH    (default: "<repo-root>/workflows/archon-dark-factory.yaml")
    DOC [DOC ...]       one or more doc paths to check (positional)
```

For each `DOC`:
1. Always run the path + line-range check (Requirement 2) against its full text.
2. If `Path(DOC).name == "configuration.md"`, additionally run the config-key check
   (Requirement 3) against `--config`.
3. If `Path(DOC).name == "features.md"`, additionally run the workflow-node check
   (Requirement 4) against `--workflow`.

Internal functions mirror this split (`_check_path_refs`, `_check_config_keys`,
`_check_feature_nodes`), each returning `list[str]`; `check()` concatenates them. This keeps
each check independently unit-testable and keeps the file dependency-free of
`check_workflow_dag.py` (a few lines of `nodes[].id` extraction are duplicated locally rather
than imported, matching how `check_workflow_dag.py` and `check_workflow_when.py` are themselves
independent siblings today, not a shared module).

**Workflow YAML changes** (`.github/workflows/ci.yml`): add one line to the existing `dag-check`
job, after the two existing `check_workflow_*.py` steps:

```yaml
  dag-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install pyyaml
      - run: python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
      - run: python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
      - run: python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

**`docs/features.md` changes:** update the existing `## CI` row's prose and "Entry points" column
to name `scripts/docs_refs_check.py` alongside the two DAG checks, following the row's existing
format; fix any `path:line`/config-key/node-id breakage the finished checker reports elsewhere in
the four docs (Requirement 7).

**Test changes:**
- New `tests/fixtures/docs_refs_check/pass/{doc.md,config.yaml,workflow.yaml}` and
  `tests/fixtures/docs_refs_check/fail/{doc.md,config.yaml,workflow.yaml}` — minimal synthetic
  trees, not copies of the real docs, so the test doesn't need updating every time the real docs
  change. `fail/` has one broken reference per check category (bad path, out-of-range line,
  mismatched config key, unknown node id).
- New `tests/test_docs_refs_check.py`: asserts `check()` returns `[]` for `pass/` and exactly the
  4 expected categorized errors for `fail/`; runs under the existing pytest CI contract.

## Alternatives Considered

1. **Parse the looser continuation/chain line-reference forms now** (bare `:N` spans implicitly
   continuing a preceding path). Rejected per Q&A: inferring "which earlier path does this
   continue" from free prose is exactly the kind of fragile heuristic that produces false
   positives, which would get the gate muted rather than trusted. Documented as an explicit v1
   gap instead.
2. **Content-anchor or symbol-reference drift detection for every existing line ref** (the
   issue's own stated open design question, option (a)/(b)). Rejected for v1: retrofitting ~500
   existing refs is a doc-rewriting project disproportionate to a "deterministic check" ticket
   with no size label; range-only validation still catches the dominant failure mode (file
   deleted, renamed, or shrunk) and is a straightforward follow-up to strengthen later.
3. **"Hyphenated lowercase identifier" heuristic for workflow-node detection**, instead of the
   "Node"/"Nodes" lead-in. Rejected per Q&A and verified against the actual doc: roughly half of
   such spans are not node ids (labels, CLI subcommands, hook names), and at least one identifier
   (`validate`) is genuinely ambiguous without the surrounding wording.
4. **Give the new check its own CI job** rather than adding a step to `dag-check`. Considered,
   but `dag-check` already groups "python scripts/check_X.py against a fixed set of repo files"
   checks of exactly this shape; a new job would duplicate the `checkout`/`setup-python`/`pip
   install pyyaml` boilerplate for no isolation benefit (none of these checks need isolating from
   each other — they're all pure, fast, read-only).

## Open Questions (Non-blocking)

- Whether to design and schedule the symbol-reference/content-anchor drift check deferred out of
  Requirement 2 — likely its own follow-up ticket once this one establishes the extraction/
  resolution groundwork it would build on.
- Whether an adapter-driven doc list (checking a non-self target's own reference docs, rather than
  this repo's four hardcoded ones) is ever wanted for this specific deterministic checker. The
  only adapter-driven-doc-list work currently tracked is a follow-up to sibling ticket #535's
  semantic gate, not to this one; this ticket's four doc paths and `config.yaml`/workflow-yaml
  defaults are hardcoded to this repo's own self-target layout.
- Whether `dag-check` should eventually be renamed to something like `deterministic-checks` now
  that it will hold three unrelated-but-same-shaped scripts — deferred since a rename risks
  needing a branch-protection settings update (human-only surface).

## Assumptions

- `config/config.yaml`'s list-valued keys (e.g. `refine.skip_labels`) are flattened as a single
  leaf (the list itself), not expanded per list item — this matches how `docs/configuration.md`
  already documents them as one table row each.
- The config-key check scopes its table scan to the `## Layer 1: config/config.yaml` section
  heading rather than the whole document, to avoid false hits from unrelated tables (e.g. the
  "four layers" overview table, whose columns aren't dotted keys anyway, so this is a defensive
  scope restriction rather than a load-bearing one today).
- The basename-search exclusion list (`.git/`, the nested `dark-factory/` seed-fixture directory,
  `node_modules/`, `.venv/`, `__pycache__/`) is hardcoded in the script with an inline comment
  explaining the `dark-factory/`-specific exclusion, since it's a quirk of this repo's own layout
  rather than a general convention.
- This ticket targets this repo's own four reference docs only (self-hosting); it does not
  attempt to generalize to a target-repo-agnostic, adapter-driven doc list (see Open Questions).

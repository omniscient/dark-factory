# Provider Selection and Provider-Aware Boot Preflight

**Issue:** omniscient/dark-factory#250
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#202
**Depends on:** omniscient/dark-factory#249 (merged — `Tracker`/`CodeHost` ABCs + GitHub
reference adapters; `providers/cli.py` thin CLI; this ticket's dependency)

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` (PR #203) lays out a six-step sequence (§11) to make
Dark Factory's ticket-tracker, code-host, and model-endpoint providers swappable. Issue #249
(step 1+2) built the `Tracker`/`CodeHost` contracts, GitHub reference adapters, and routed
`scheduler.sh`/`entrypoint.sh`/`smoke_gate.sh`/the run DAG through the new provider CLI —
but `get_tracker()`/`get_codehost()` in `providers/__init__.py` still unconditionally return
the GitHub adapters, no `FACTORY_TRACKER`/`FACTORY_CODEHOST`/`FACTORY_MODEL_PROVIDER` env var
is read anywhere, and boot validation is still two ad hoc bash checks (`GH_TOKEN`,
`CLAUDE_CODE_OAUTH_TOKEN || ANTHROPIC_API_KEY`) duplicated in `entrypoint.sh` and
`scheduler.sh`.

Issue #250 is **step 3**: add the selection env vars (defaulting to today's
`github`/`github`/`anthropic`), let each provider declare its required environment, and
replace the two duplicated bash checks with one centralized, provider-aware
`providers preflight` command that hard-fails loudly and actionably on misconfiguration —
while reproducing the exact same pass/fail behavior as today on the default path (the
design's "parity invariant," §2/§9).

This ticket does **not** implement `JiraTracker`, `GitLabCodeHost`, or the model gateway
(Databricks/OpenAI routing) — those are steps 4–6, separate future tickets. It also does not
touch `deploy/instances/**` or the image-publish pipeline (human-only surface per
`CLAUDE.md`).

## Requirements

Distilled from the issue's acceptance criteria and refined through Q&A (log below):

1. **Selection env vars**, read once and defaulted to today's providers:
   `FACTORY_TRACKER` (default `github`), `FACTORY_CODEHOST` (default `github`),
   `FACTORY_MODEL_PROVIDER` (default `anthropic`). `get_tracker()`/`get_codehost()` in
   `providers/__init__.py` resolve the selected adapter instead of hardcoding GitHub; an
   unset or `github`-valued env produces byte-identical behavior to #249's current code.
2. **Tracker/CodeHost required-env declaration**: add a concrete `required_env()`
   classmethod to the `Tracker` and `CodeHost` ABCs (`tracker/base.py`, `codehost/base.py`),
   with a safe default of `[]` — mirroring the existing "degradable concrete method with a
   safe default" convention `get_status_limits`/`get_rate_budget` already established in
   `tracker/base.py`. `GitHubTracker.required_env()` and `GitHubCodeHost.required_env()`
   both return `["GH_TOKEN"]`.
3. **Model-axis descriptor registry**: new `scripts/factory_core/providers/model.py`, a
   module-level dict keyed by provider name (mirroring `adapter_defaults.py`'s
   single-`DEFAULTS`-dict convention, since the model axis has no behavioral contract to hang
   a class on — Claude Code itself reads `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/
   `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX` natively, per parent spec §7.1). Known
   providers and their preflight behavior:
   - `anthropic` (default): passes if `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is set
     (today's check, reproduced verbatim).
   - `bedrock`: passes if `CLAUDE_CODE_USE_BEDROCK` plus the AWS credential env Claude Code
     itself requires are present (native fast path, no gateway — parent spec §7.2).
   - `vertex`: passes if `CLAUDE_CODE_USE_VERTEX` plus the GCP credential env Claude Code
     itself requires are present (native fast path, no gateway).
   - `databricks` / `openai`: **known, not-yet-implemented** — preflight always hard-fails
     for these two names with a message distinct from "unknown provider" (e.g. "`databricks`
     requires the model gateway, not yet implemented — see
     docs/provider-abstraction-design.md §11 step 5"). No gateway code, no
     `deploy/gateway/`, no LiteLLM config is added in this ticket.
   - anything else: hard-fails as an unknown provider.
4. **`providers preflight` CLI verb**, added to the existing
   `scripts/factory_core/providers/cli.py` as a new top-level subcommand (direct-script-path
   invocation — `python3 .../providers/cli.py preflight` — matching the convention #249
   already established and documented in that file, not the design doc's illustrative
   `python -m factory_core.providers preflight` sketch). It resolves all three selection env
   vars, aggregates required-env checks across all three axes, and on any failure prints
   every missing/invalid item at once (not one-at-a-time) and exits non-zero.
5. **Replaces, not augments, the existing inline bash checks.** Per parent spec §4
   ("Boot preflight — a provider-aware validator replaces today's single
   `CLAUDE_CODE_OAUTH_TOKEN || ANTHROPIC_API_KEY` check... matching the current `GH_TOKEN`
   check"), delete the two inline `if [ -z ... ]` blocks in both `entrypoint.sh` (lines
   ~14–21) and `scheduler.sh` (lines ~101–107), replacing each with a single early call to
   `python3 "$FACTORY_PROVIDERS_CLI" preflight`, run before `entrypoint.sh`'s repo clone and
   before `scheduler.sh`'s poll loop. On the default `github`/`github`/`anthropic` path,
   preflight reproduces today's exact pass/fail conditions and the existing operator-facing
   error strings (`"ERROR: GH_TOKEN is not set. Add it to .archon/.env"` /
   `"ERROR: Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"`), satisfying
   the parity invariant. New/changed text is added only for the new cases this ticket
   introduces (unknown provider, databricks/openai-not-yet-implemented, missing
   provider-specific env for a non-default selection).
6. **Secret redaction.** Preflight errors and any artifact it writes must redact secret
   *values*, never names — env var names appear in error text ("GH_TOKEN is not set"), but
   if a future check ever needed to echo a partially-set value (e.g. validating a URL that
   embeds a token) it must redact it, following the existing pattern already in this package
   (`providers/cli.py`'s `_codehost_remote_url`: `re.sub(r"://[^@/]+@", "://***@", url)`).
   Preflight itself only checks *presence*, not values, so in practice no secret value is
   ever read into an error message — this requirement is enforced by construction, not by a
   redaction step needing to strip something.
7. **Config/selection/preflight tests** cover: default env → github/github/anthropic
   resolution; explicit `github`/`github`/`anthropic` selection (still parity); unknown
   `FACTORY_TRACKER`/`FACTORY_CODEHOST`/`FACTORY_MODEL_PROVIDER` value → hard-fail with
   actionable message; missing `GH_TOKEN` → hard-fail (both axes, since both codehost and
   tracker require it); missing Anthropic token → hard-fail; `FACTORY_MODEL_PROVIDER=databricks`
   or `=openai` → hard-fail with the distinct "not yet implemented" message; all failures for
   a given run are aggregated into one report, not raised one-at-a-time.
8. Existing tests (`tests/test_provider_registry.py`, `tests/test_provider_cli.py`,
   `tests/test_provider_tracker_parity.py`, `tests/test_provider_codehost_parity.py`,
   `tests/test_scheduler.sh`, `tests/test_smoke_gate.sh`) stay green — in particular,
   `test_provider_registry.py`'s `get_tracker()`/`get_codehost()` default-return assertions
   must still pass with no env vars set.

## Brainstorming Q&A

> **Q1:** The acceptance criteria say "Unknown providers... fail with actionable startup
> errors" and separately "Bedrock/Vertex native-fast-path configuration is represented
> without requiring a gateway implementation" (implying bedrock/vertex are in scope since
> they need no gateway). But `databricks`/`openai` require a gateway that doesn't exist yet
> and is explicitly a later step (step 5). Should they be (A) known-but-not-yet-implemented
> with a distinct actionable error, or (B) fully unknown providers for now, with the
> known-provider set limited to `{anthropic, bedrock, vertex}`?
>
> **A:** (A). The design doc names both providers explicitly and repeatedly as first-class
> members of the model axis (§7.2 alias-map behavior, §7.5 preflight semantics, §11 step 5
> schedules their *gateway*, not their *name*). Collapsing them into the same bucket as a
> typo throws away information the spec already established, and "actionable startup
> errors" is better served by telling an operator exactly why `databricks` doesn't work yet
> rather than misdiagnosing it as a spelling mistake. Known-provider set for this ticket:
> `{anthropic, bedrock, vertex, databricks, openai}`, partitioned into native (validate real
> env), gateway-dependent-not-yet-implemented (databricks/openai — deterministic "not yet
> implemented" failure), and unknown (anything else).

> **Q2:** Should `providers preflight` **replace** the existing inline `GH_TOKEN` /
> `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` checks in `entrypoint.sh` and `scheduler.sh`,
> or run additively alongside them?
>
> **A:** Replace. Parent spec §4 states verbatim: "Boot preflight — a provider-aware
> validator **replaces** today's single `CLAUDE_CODE_OAUTH_TOKEN || ANTHROPIC_API_KEY`
> check... matching the current `GH_TOKEN` check." Keeping the inline checks would also
> actively break any non-default tracker/codehost selection later (e.g. a stale `GH_TOKEN`
> guard would wrongly hard-fail a correctly-configured Jira-tracker instance that has no
> reason to set `GH_TOKEN`). The parity invariant constrains *how* the replacement behaves
> on the default path (identical error text/exit code), not *whether* to replace.

> **Q3:** Where should each provider declare its required env vars — Tracker/CodeHost (which
> have concrete classes) vs. Model (which has no behavioral class at all)?
>
> **A:** Co-locate with behavior, per axis. Tracker/CodeHost: a `required_env()` classmethod
> on the ABC with a safe default of `[]`, overridden per adapter (`GitHubTracker`/
> `GitHubCodeHost` → `["GH_TOKEN"]`) — mirrors the existing degradable-method-with-safe-default
> convention #249 already set for `get_status_limits`/`get_rate_budget`. This means a future
> `JiraTracker`/`GitLabCodeHost` declares its own required env in its own file, with no shared
> file to edit and no merge-conflict magnet. Model axis: a module-level dict in a new
> `providers/model.py` (mirroring `adapter_defaults.py`'s single-`DEFAULTS`-dict style, since
> there's no class to attach a classmethod to), keyed by provider name, each entry carrying
> required env plus the provider-specific preflight semantics from §7.5. A single
> `preflight()` orchestrator (in `providers/__init__.py`) resolves all three selections and
> aggregates every axis's check into one report before a single hard exit — this is also
> what lets it reproduce, on the default path, the exact same failure set
> (`{GH_TOKEN}` ∪ `{CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY}`) that today's two inline
> bash checks produce.

## Architecture / Approach

### Selection resolution (`providers/__init__.py`)

```python
_TRACKERS = {"github": GitHubTracker}          # extended by later tickets (jira, ...)
_CODEHOSTS = {"github": GitHubCodeHost}        # extended by later tickets (gitlab, ...)

def get_tracker():
    name = os.environ.get("FACTORY_TRACKER", "github")
    cls = _TRACKERS.get(name)
    if cls is None:
        raise ProviderConfigError(f"Unknown FACTORY_TRACKER '{name}'")
    return cls()

def get_codehost():
    # same shape, _CODEHOSTS
    ...

def preflight() -> list[str]:
    """Return a list of human-readable problems; empty list == OK."""
    problems = []
    tracker_name = os.environ.get("FACTORY_TRACKER", "github")
    codehost_name = os.environ.get("FACTORY_CODEHOST", "github")
    model_name = os.environ.get("FACTORY_MODEL_PROVIDER", "anthropic")

    tracker_cls = _TRACKERS.get(tracker_name)
    if tracker_cls is None:
        problems.append(f"Unknown FACTORY_TRACKER '{tracker_name}'")
    else:
        problems += _missing_env(tracker_cls.required_env(), f"tracker={tracker_name}")

    # ... same shape for codehost ...

    problems += model.preflight(model_name)  # see providers/model.py below
    return problems
```

`get_tracker()`/`get_codehost()` raising on an unknown name (rather than silently falling
back to GitHub) is deliberate: it is the "hard startup failure for selected-provider
misconfiguration" the issue asks for, and it means `preflight()` and the actual runtime
resolution path share one source of truth for "what counts as a known provider" — no
separate allowlist to keep in sync.

### `required_env()` on the ABCs (`tracker/base.py`, `codehost/base.py`)

```python
class Tracker(ABC):
    ...
    @classmethod
    def required_env(cls) -> list[str]:
        """Env vars this adapter needs present at boot. Degradable: [] by default."""
        return []
```

```python
class GitHubTracker(Tracker):
    @classmethod
    def required_env(cls) -> list[str]:
        return ["GH_TOKEN"]
```

`GitHubCodeHost.required_env()` returns the same `["GH_TOKEN"]` — both axes need it today
(codehost for `remote_url()`'s embedded token, tracker for every `gh`/`gh api` call), and a
misconfigured instance should see both problems in one preflight report rather than fixing
one and re-running to discover the second.

### Model axis (`providers/model.py`, new)

```python
"""Model-endpoint provider descriptors (parent spec §7). No behavioral ABC: Claude Code
itself reads ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/CLAUDE_CODE_USE_BEDROCK/
CLAUDE_CODE_USE_VERTEX natively (§7.1) — this module only declares what boot preflight
must validate per selected provider."""

def _anthropic_check():
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return ["Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    return []

def _bedrock_check():
    ...  # CLAUDE_CODE_USE_BEDROCK + AWS creds present

def _vertex_check():
    ...  # CLAUDE_CODE_USE_VERTEX + GCP creds present

def _not_yet_implemented(name):
    return [f"FACTORY_MODEL_PROVIDER={name} requires the model gateway, which is not yet "
            f"implemented — see docs/provider-abstraction-design.md §11 step 5"]

_MODEL_PROVIDERS = {
    "anthropic": _anthropic_check,
    "bedrock": _bedrock_check,
    "vertex": _vertex_check,
    "databricks": lambda: _not_yet_implemented("databricks"),
    "openai": lambda: _not_yet_implemented("openai"),
}

def preflight(name: str) -> list[str]:
    check = _MODEL_PROVIDERS.get(name)
    if check is None:
        return [f"Unknown FACTORY_MODEL_PROVIDER '{name}'"]
    return check()
```

### CLI (`providers/cli.py`)

Add a `preflight` top-level subcommand alongside the existing `tracker`/`codehost` ones:

```python
def _preflight(args):
    from factory_core.providers import preflight
    problems = preflight()
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        sys.exit(1)
    print("providers preflight: OK")

# in main(): top.add_parser("preflight").set_defaults(func=_preflight)
```

Invocation stays `python3 "$FACTORY_PROVIDERS_CLI" preflight` — the direct-script-path
convention this file's own docstring already establishes ("mirrors this repo's existing
scripts/factory_core/cli.py convention... not `-m factory_core.tracker`"), not the design
doc's illustrative `-m factory_core.providers preflight` sketch.

### Bash call sites

`entrypoint.sh` (before the clone) and `scheduler.sh` (before the poll loop): delete the two
inline `if [ -z ... ]` blocks and replace with:

```bash
python3 "$FACTORY_PROVIDERS_CLI" preflight
```

(`set -euo pipefail` is already active in both scripts, so a non-zero exit here aborts the
script exactly as the inline `exit 1` did today.) `FACTORY_PROVIDERS_CLI` is already defined
in `scheduler.sh`; `entrypoint.sh` gains the same variable (defaulting to
`/opt/dark-factory/scripts/factory_core/providers/cli.py`) for the same call.

## Alternatives Considered

1. **Additive preflight (keep inline bash checks, add `providers preflight` as a second,
   later check).** Rejected: contradicts parent spec §4's explicit "replaces" language, and
   leaves the exact drift this ticket exists to centralize — GitHub-token logic living in
   two languages that can silently diverge, plus a stale `GH_TOKEN` guard that would
   incorrectly fire for a future non-GitHub tracker selection.
2. **Fully centralized cross-axis provider-metadata dict** (one `providers/registry.py` file
   listing every tracker/codehost/model provider's required env in one place, decoupled from
   the `Tracker`/`CodeHost` classes). Rejected: divorces env declaration from the adapter
   that consumes it, and forces every future adapter ticket (`JiraTracker`, `GitLabCodeHost`,
   gateway providers) to edit one shared hot file — a merge-conflict magnet the per-file
   co-location approach avoids.
3. **Treat `databricks`/`openai` as fully unknown providers until the gateway ticket lands.**
   Rejected per Q1 — throws away information the parent spec already establishes about
   these providers, and produces a less actionable error than naming the real reason
   (gateway not implemented yet) explicitly.
4. **Instance-level required-env check instead of a class-level one** (i.e. instantiate the
   adapter and probe it dynamically). Rejected: preflight must be able to fail *before*
   constructing an adapter whose own `__init__` might read unset env (a future
   `JiraTracker.__init__` plausibly does); a classmethod runs the check with no instance
   needed.

## Open Questions (Non-blocking)

- The exact AWS/GCP credential env vars Claude Code requires for `CLAUDE_CODE_USE_BEDROCK`/
  `CLAUDE_CODE_USE_VERTEX` native paths aren't enumerated in this repo today (no prior bedrock
  path support existed to extract them from) — the implementer should confirm the current
  Claude Code CLI's documented required env for each and encode exactly that set in
  `_bedrock_check`/`_vertex_check`; this is a bounded implementation detail, not a design
  question.
- Whether `preflight()`'s aggregated-report format should be plain stderr lines (matching
  today's bash `echo ... >&2` style) or structured JSON is left to the implementer to match
  whatever the other `providers/cli.py` verbs already do for error output (today: plain
  stderr).

## Assumptions

- Issue #250's `spec-approved` label predates this refinement run and does not exempt this
  ticket from the standard refine → plan → implement → conformance → code-review pipeline;
  it is treated as informational, not as a skip signal (only `spec-pending-review`,
  `needs-discussion`, and `epic` are refine skip-guard labels per `.claude/skills/refinement/config.yaml`).
- The refine branch (`refine/issue-250-...`) had zero commits of its own and was purely
  stale relative to `main` (missing #249's merge); it was fast-forwarded to `origin/main`
  before this spec was written so the spec is grounded in the actual post-#249 codebase
  state rather than the pre-#249 snapshot the branch originally pointed at.
- `direct-to-pr` is set on this issue; the standard grace-window auto-advance behavior
  (`commands/dark-factory-refine.md` Phase 6) applies after this spec is published.

# Plan: Provider selection and provider-aware boot preflight (#250)

**Issue:** #250 · **Spec:** [`docs/superpowers/specs/2026-07-13-provider-selection-preflight-design.md`](../specs/2026-07-13-provider-selection-preflight-design.md)

## Goal

Step 3 of the provider-abstraction design (#203): add `FACTORY_TRACKER` /
`FACTORY_CODEHOST` / `FACTORY_MODEL_PROVIDER` env-var selection (defaulting to
today's `github`/`github`/`anthropic`), let each provider axis declare its
required environment, and replace the two duplicated inline bash checks in
`entrypoint.sh`/`scheduler.sh` with one centralized `providers preflight` CLI
verb that aggregates every problem into a single report and hard-fails loudly.
On the default path this must be byte-identical in behavior and error text to
today's two inline checks (the spec's "parity invariant").

Out of scope (per spec): `JiraTracker`, `GitLabCodeHost`, the model gateway
(Databricks/OpenAI routing), `deploy/instances/**`, the publish pipeline.

## Architecture

- **Tracker/CodeHost axes** (`tracker/base.py`, `codehost/base.py`): a new
  `required_env()` classmethod, degradable with a safe default of `[]`
  (mirrors the existing `get_status_limits`/`get_rate_budget` convention).
  `GitHubTracker`/`GitHubCodeHost` override it to `["GH_TOKEN"]`.
- **Model axis** (`providers/model.py`, new): no behavioral class exists for
  this axis, so it's a module-level dict keyed by provider name (mirrors
  `adapter_defaults.py`'s single-`DEFAULTS`-dict style), partitioned into
  native providers that validate real env (`anthropic`, `bedrock`, `vertex`),
  known-but-gateway-not-yet-implemented providers (`databricks`, `openai`),
  and everything else (unknown).
- **Registry + orchestrator** (`providers/__init__.py`): `_TRACKERS`/
  `_CODEHOSTS` dicts keyed by env value; `get_tracker()`/`get_codehost()`
  resolve `FACTORY_TRACKER`/`FACTORY_CODEHOST` (default `github`) and raise a
  new `ProviderConfigError` on an unknown name — this is the "hard startup
  failure for selected-provider misconfiguration" the issue asks for, and it
  means `preflight()` and the real runtime resolution path share one source
  of truth for "what counts as a known provider." `preflight()` aggregates
  problems across all three axes into one list (empty == OK) rather than
  raising on the first miss, so an operator sees every misconfiguration at
  once.
- **CLI** (`providers/cli.py`): a new top-level `preflight` subcommand
  (`python3 .../providers/cli.py preflight`, matching the existing
  direct-script-path convention — not `-m factory_core.providers preflight`).
  Prints every problem prefixed `ERROR:` to stderr and exits 1 on any
  problem; prints `providers preflight: OK` and exits 0 otherwise.
- **Bash call sites**: `entrypoint.sh` (before the clone) and `scheduler.sh`
  (before the poll loop) each drop their inline `if [ -z ... ]` blocks in
  favor of one `python3 "$FACTORY_PROVIDERS_CLI" preflight` call.
  `set -euo pipefail` is already active in both, so a non-zero exit aborts
  the script exactly as the inline `exit 1` did today.

**Error-text parity note:** `_missing_env()`/`model._missing()` format each
missing var as `"{VAR} is not set. Add it to .archon/.env"` — identical text
to today's `GH_TOKEN` message. Because both `GitHubTracker.required_env()`
and `GitHubCodeHost.required_env()` return `["GH_TOKEN"]`, a missing
`GH_TOKEN` on the default path produces that exact line **twice** in the
aggregated report (once per axis) — this is the spec's explicit design
("a misconfigured instance should see both problems in one preflight report"),
not a bug. The anthropic-axis message
(`"Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"`) is
reproduced verbatim and appears once. Exit code (1) and the `ERROR:` prefix
match today's behavior on the default path.

## Tech Stack

Python 3.12 (`scripts/factory_core/providers/`), pytest, Bash (`entrypoint.sh`,
`scheduler.sh`, existing `tests/test_scheduler.sh` `SCHEDULER_SOURCE_ONLY=1`
harness and a new grep-based static test for `entrypoint.sh`, mirroring
`tests/test_entrypoint_fix_main.sh`). No new dependencies.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/tracker/base.py` | Add `required_env()` classmethod, default `[]` |
| `scripts/factory_core/providers/tracker/github.py` | Add `required_env()` → `["GH_TOKEN"]` |
| `scripts/factory_core/providers/codehost/base.py` | Add `required_env()` classmethod, default `[]` |
| `scripts/factory_core/providers/codehost/github.py` | Add `required_env()` → `["GH_TOKEN"]` |
| `scripts/factory_core/providers/model.py` | New — model-axis descriptor registry + `preflight(name)` |
| `scripts/factory_core/providers/__init__.py` | Add `ProviderConfigError`; env-based `get_tracker()`/`get_codehost()`; `preflight()` orchestrator |
| `scripts/factory_core/providers/cli.py` | Add top-level `preflight` subcommand |
| `entrypoint.sh` | Replace inline `GH_TOKEN`/`CLAUDE_CODE_OAUTH_TOKEN` checks (lines ~14-21) with `FACTORY_PROVIDERS_CLI` + one preflight call, before the clone |
| `scheduler.sh` | Replace inline checks (lines ~101-107) with one preflight call, before the poll loop; drop the now-dead credential-stub comment in the test harness |
| `README.md` | Document the three new `FACTORY_*` selection env vars |
| `tests/test_provider_tracker_base.py` | Extend: `required_env()` default `[]` |
| `tests/test_provider_codehost_base.py` | Extend: `required_env()` default `[]` (new degradable-ops test — `CodeHost` had none before) |
| `tests/test_provider_tracker_parity.py` | Add: `GitHubTracker.required_env() == ["GH_TOKEN"]` |
| `tests/test_provider_codehost_parity.py` | Add: `GitHubCodeHost.required_env() == ["GH_TOKEN"]` |
| `tests/test_provider_model.py` | New — `model.preflight()` per provider |
| `tests/test_provider_registry.py` | Extend: env-based selection, `ProviderConfigError`, `preflight()` aggregation |
| `tests/test_provider_cli.py` | Extend: `preflight` subcommand OK/failure paths |
| `tests/test_entrypoint_preflight.sh` | New — static assertions (inline checks gone, preflight call before clone) |
| `tests/test_scheduler.sh` | Extend: new section confirming the preflight call fires at source time and that a preflight failure aborts sourcing |

---

## Task 1: `required_env()` on the Tracker/CodeHost ABCs + GitHub overrides

**Files:** `scripts/factory_core/providers/tracker/base.py`,
`scripts/factory_core/providers/tracker/github.py`,
`scripts/factory_core/providers/codehost/base.py`,
`scripts/factory_core/providers/codehost/github.py`,
`tests/test_provider_tracker_base.py`, `tests/test_provider_codehost_base.py`,
`tests/test_provider_tracker_parity.py`, `tests/test_provider_codehost_parity.py`

### Steps

1. **Write failing tests.**

   In `tests/test_provider_tracker_base.py`, extend
   `test_tracker_degradable_ops_have_safe_defaults` with one more assertion:

   ```python
       bare = _Bare()
       assert bare.get_status_limits() == {}
       assert bare.get_rate_budget() == {"remaining": None, "reset": None, "used": None, "limit": None}
       assert _Bare.required_env() == []
   ```

   In `tests/test_provider_codehost_base.py`, add a new test (this ABC had no
   degradable-ops test before — `CodeHost` was 100% abstract until now):

   ```python
   def test_codehost_degradable_ops_have_safe_defaults():
       from factory_core.providers.codehost.base import CodeHost

       class _Bare(CodeHost):
           def remote_url(self): return ""
           def find_change_for(self, branch): return None
           def open_change(self, source, target, title, body, draft=False): return "1"
           def update_change_body(self, id, body): return True
           def mark_ready(self, id): pass
           def merge_change(self, id, strategy="merge", delete_branch=True): return True
           def get_change_checks(self, id): return []
           def get_change_mergeable(self, id): return "UNKNOWN"
           def get_change_reviews(self, id): return ""
           def get_change_inline_comments(self, id): return []
           def close_keyword(self, issue_id): return ""

       assert _Bare.required_env() == []
   ```

   In `tests/test_provider_tracker_parity.py`, append:

   ```python
   def test_required_env_returns_gh_token():
       assert GitHubTracker.required_env() == ["GH_TOKEN"]
   ```

   In `tests/test_provider_codehost_parity.py`, append (note this file's
   import needs `GitHubCodeHost` — check the top-of-file import first; add it
   if not already present):

   ```python
   def test_required_env_returns_gh_token():
       from factory_core.providers.codehost.github import GitHubCodeHost
       assert GitHubCodeHost.required_env() == ["GH_TOKEN"]
   ```

2. **Verify RED.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_tracker_base.py tests/test_provider_codehost_base.py tests/test_provider_tracker_parity.py tests/test_provider_codehost_parity.py -v
   ```

   Expected: the four new/extended tests fail with `AttributeError:
   required_env` (or a collection error for the codehost import, if
   `GitHubCodeHost` wasn't already imported in that file).

3. **Implement.**

   In `scripts/factory_core/providers/tracker/base.py`, append after
   `get_rate_budget`:

   ```python

       @classmethod
       def required_env(cls) -> list[str]:
           """Env vars this adapter needs present at boot. Degradable: [] by default."""
           return []
   ```

   In `scripts/factory_core/providers/tracker/github.py`, add as the first
   method of `GitHubTracker`, immediately after the class line:

   ```python
   class GitHubTracker(Tracker):
       @classmethod
       def required_env(cls) -> list[str]:
           return ["GH_TOKEN"]

       def get_item(self, id: str, fields: tuple | None = None) -> dict:
   ```

   In `scripts/factory_core/providers/codehost/base.py`, append after
   `close_keyword`:

   ```python

       @classmethod
       def required_env(cls) -> list[str]:
           """Env vars this adapter needs present at boot. Degradable: [] by default."""
           return []
   ```

   In `scripts/factory_core/providers/codehost/github.py`, add as the first
   method of `GitHubCodeHost`, immediately after the class line:

   ```python
   class GitHubCodeHost(CodeHost):
       @classmethod
       def required_env(cls) -> list[str]:
           return ["GH_TOKEN"]

       def remote_url(self) -> str:
   ```

4. **Verify GREEN.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_tracker_base.py tests/test_provider_codehost_base.py tests/test_provider_tracker_parity.py tests/test_provider_codehost_parity.py -v
   ```

   Expected: all pass, including the pre-existing tests in these four files.

5. **Commit.**

   ```bash
   git add scripts/factory_core/providers/tracker/base.py scripts/factory_core/providers/tracker/github.py scripts/factory_core/providers/codehost/base.py scripts/factory_core/providers/codehost/github.py tests/test_provider_tracker_base.py tests/test_provider_codehost_base.py tests/test_provider_tracker_parity.py tests/test_provider_codehost_parity.py
   git commit -m "feat(providers): add required_env() to Tracker/CodeHost ABCs + GitHub overrides (#250)"
   ```

---

## Task 2: Model-axis descriptor registry (`providers/model.py`)

**Files:** `scripts/factory_core/providers/model.py` (new),
`tests/test_provider_model.py` (new)

### Steps

1. **Write failing tests.** Create `tests/test_provider_model.py`:

   ```python
   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


   def test_anthropic_passes_with_oauth_token(monkeypatch):
       from factory_core.providers import model
       monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
       monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
       assert model.preflight("anthropic") == []


   def test_anthropic_passes_with_api_key(monkeypatch):
       from factory_core.providers import model
       monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
       monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
       assert model.preflight("anthropic") == []


   def test_anthropic_fails_with_neither_token(monkeypatch):
       from factory_core.providers import model
       monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
       monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
       assert model.preflight("anthropic") == [
           "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"
       ]


   def test_bedrock_passes_with_full_env(monkeypatch):
       from factory_core.providers import model
       monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
       monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
       monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
       monkeypatch.setenv("AWS_REGION", "us-east-1")
       assert model.preflight("bedrock") == []


   def test_bedrock_fails_with_nothing_set(monkeypatch):
       from factory_core.providers import model
       for var in ("CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
           monkeypatch.delenv(var, raising=False)
       problems = model.preflight("bedrock")
       assert len(problems) == 4
       assert "AWS_REGION is not set. Add it to .archon/.env" in problems


   def test_vertex_passes_with_full_env(monkeypatch):
       from factory_core.providers import model
       monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
       monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
       monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
       monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")
       assert model.preflight("vertex") == []


   def test_vertex_fails_with_nothing_set(monkeypatch):
       from factory_core.providers import model
       for var in ("CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS"):
           monkeypatch.delenv(var, raising=False)
       assert len(model.preflight("vertex")) == 4


   def test_databricks_not_yet_implemented():
       from factory_core.providers import model
       problems = model.preflight("databricks")
       assert len(problems) == 1
       assert "databricks" in problems[0]
       assert "not yet" in problems[0]
       assert "docs/provider-abstraction-design.md" in problems[0]


   def test_openai_not_yet_implemented():
       from factory_core.providers import model
       problems = model.preflight("openai")
       assert len(problems) == 1
       assert "openai" in problems[0]
       assert "not yet" in problems[0]


   def test_unknown_model_provider():
       from factory_core.providers import model
       assert model.preflight("cohere") == ["Unknown FACTORY_MODEL_PROVIDER 'cohere'"]
   ```

2. **Verify RED.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_model.py -v
   ```

   Expected: `ModuleNotFoundError: No module named 'factory_core.providers.model'`
   on every test.

3. **Implement.** Create `scripts/factory_core/providers/model.py`:

   ```python
   """Model-endpoint provider descriptors (parent spec
   docs/provider-abstraction-design.md §7). No behavioral ABC: Claude Code
   itself reads ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/CLAUDE_CODE_USE_BEDROCK/
   CLAUDE_CODE_USE_VERTEX natively (§7.1) — this module only declares what
   boot preflight must validate per selected provider."""
   import os


   def _missing(names: list[str]) -> list[str]:
       return [f"{name} is not set. Add it to .archon/.env" for name in names if not os.environ.get(name)]


   def _anthropic_check() -> list[str]:
       if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
           return ["Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
       return []


   def _bedrock_check() -> list[str]:
       return _missing(["CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"])


   def _vertex_check() -> list[str]:
       return _missing([
           "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID",
           "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS",
       ])


   def _not_yet_implemented(name: str) -> list[str]:
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

   Note on the bedrock/vertex env sets (spec's Open Questions flagged this as
   a bounded implementer decision): `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
   `AWS_REGION` and `ANTHROPIC_VERTEX_PROJECT_ID`/`CLOUD_ML_REGION`/
   `GOOGLE_APPLICATION_CREDENTIALS` are the credential env vars Claude Code's
   native Bedrock/Vertex fast paths document as required alongside
   `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX`.

4. **Verify GREEN.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_model.py -v
   ```

   Expected: all 10 tests pass.

5. **Commit.**

   ```bash
   git add scripts/factory_core/providers/model.py tests/test_provider_model.py
   git commit -m "feat(providers): add model-axis descriptor registry with preflight checks (#250)"
   ```

---

## Task 3: Env-based provider registry + `preflight()` orchestrator

**Files:** `scripts/factory_core/providers/__init__.py`,
`tests/test_provider_registry.py`

### Steps

1. **Write failing tests.** Rewrite `tests/test_provider_registry.py`:

   ```python
   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

   import pytest


   def test_get_tracker_returns_github_tracker():
       from factory_core.providers import get_tracker
       from factory_core.providers.tracker.github import GitHubTracker
       assert isinstance(get_tracker(), GitHubTracker)


   def test_get_codehost_returns_github_codehost():
       from factory_core.providers import get_codehost
       from factory_core.providers.codehost.github import GitHubCodeHost
       assert isinstance(get_codehost(), GitHubCodeHost)


   def test_get_tracker_explicit_github_selection(monkeypatch):
       from factory_core.providers import get_tracker
       from factory_core.providers.tracker.github import GitHubTracker
       monkeypatch.setenv("FACTORY_TRACKER", "github")
       assert isinstance(get_tracker(), GitHubTracker)


   def test_get_codehost_explicit_github_selection(monkeypatch):
       from factory_core.providers import get_codehost
       from factory_core.providers.codehost.github import GitHubCodeHost
       monkeypatch.setenv("FACTORY_CODEHOST", "github")
       assert isinstance(get_codehost(), GitHubCodeHost)


   def test_get_tracker_unknown_raises(monkeypatch):
       from factory_core.providers import ProviderConfigError, get_tracker
       monkeypatch.setenv("FACTORY_TRACKER", "jira")
       with pytest.raises(ProviderConfigError, match="Unknown FACTORY_TRACKER 'jira'"):
           get_tracker()


   def test_get_codehost_unknown_raises(monkeypatch):
       from factory_core.providers import ProviderConfigError, get_codehost
       monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
       with pytest.raises(ProviderConfigError, match="Unknown FACTORY_CODEHOST 'gitlab'"):
           get_codehost()


   def test_preflight_default_env_fails_on_missing_tokens(monkeypatch):
       from factory_core.providers import preflight
       monkeypatch.delenv("GH_TOKEN", raising=False)
       monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
       monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
       problems = preflight()
       assert "GH_TOKEN is not set. Add it to .archon/.env" in problems
       assert "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" in problems


   def test_preflight_default_env_passes_with_tokens_set(monkeypatch):
       from factory_core.providers import preflight
       monkeypatch.setenv("GH_TOKEN", "x")
       monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
       assert preflight() == []


   def test_preflight_missing_gh_token_flags_both_tracker_and_codehost(monkeypatch):
       from factory_core.providers import preflight
       monkeypatch.delenv("GH_TOKEN", raising=False)
       monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
       problems = preflight()
       assert problems.count("GH_TOKEN is not set. Add it to .archon/.env") == 2


   def test_preflight_unknown_tracker_codehost_model_all_reported(monkeypatch):
       from factory_core.providers import preflight
       monkeypatch.setenv("FACTORY_TRACKER", "jira")
       monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
       monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "cohere")
       problems = preflight()
       assert "Unknown FACTORY_TRACKER 'jira'" in problems
       assert "Unknown FACTORY_CODEHOST 'gitlab'" in problems
       assert "Unknown FACTORY_MODEL_PROVIDER 'cohere'" in problems


   def test_preflight_databricks_not_yet_implemented(monkeypatch):
       from factory_core.providers import preflight
       monkeypatch.setenv("GH_TOKEN", "x")
       monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "databricks")
       problems = preflight()
       assert any("requires the model gateway" in p and "databricks" in p for p in problems)
   ```

2. **Verify RED.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_registry.py -v
   ```

   Expected: the two pre-existing tests still pass (current hardcoded
   `get_tracker`/`get_codehost` already return GitHub adapters); every new
   test fails — `ImportError: cannot import name 'ProviderConfigError'` /
   `preflight` from `factory_core.providers`.

3. **Implement.** Replace `scripts/factory_core/providers/__init__.py`
   entirely:

   ```python
   """Provider registry (parent spec docs/provider-abstraction-design.md §4).

   Selection is env-driven: FACTORY_TRACKER / FACTORY_CODEHOST /
   FACTORY_MODEL_PROVIDER (parent spec step 3), defaulting to today's
   github/github/anthropic — unset env is byte-identical to the pre-#250
   hardcoded-GitHub behavior.
   """
   import os

   from factory_core.providers import model
   from factory_core.providers.codehost.github import GitHubCodeHost
   from factory_core.providers.tracker.github import GitHubTracker

   _TRACKERS = {"github": GitHubTracker}    # extended by later tickets (jira, ...)
   _CODEHOSTS = {"github": GitHubCodeHost}  # extended by later tickets (gitlab, ...)


   class ProviderConfigError(Exception):
       """A selected FACTORY_TRACKER/FACTORY_CODEHOST/FACTORY_MODEL_PROVIDER is unknown."""


   def get_tracker():
       name = os.environ.get("FACTORY_TRACKER", "github")
       cls = _TRACKERS.get(name)
       if cls is None:
           raise ProviderConfigError(f"Unknown FACTORY_TRACKER '{name}'")
       return cls()


   def get_codehost():
       name = os.environ.get("FACTORY_CODEHOST", "github")
       cls = _CODEHOSTS.get(name)
       if cls is None:
           raise ProviderConfigError(f"Unknown FACTORY_CODEHOST '{name}'")
       return cls()


   def _missing_env(required: list[str]) -> list[str]:
       return [f"{var} is not set. Add it to .archon/.env" for var in required if not os.environ.get(var)]


   def preflight() -> list[str]:
       """Return a list of human-readable problems; empty list == OK."""
       problems = []

       tracker_name = os.environ.get("FACTORY_TRACKER", "github")
       tracker_cls = _TRACKERS.get(tracker_name)
       if tracker_cls is None:
           problems.append(f"Unknown FACTORY_TRACKER '{tracker_name}'")
       else:
           problems += _missing_env(tracker_cls.required_env())

       codehost_name = os.environ.get("FACTORY_CODEHOST", "github")
       codehost_cls = _CODEHOSTS.get(codehost_name)
       if codehost_cls is None:
           problems.append(f"Unknown FACTORY_CODEHOST '{codehost_name}'")
       else:
           problems += _missing_env(codehost_cls.required_env())

       model_name = os.environ.get("FACTORY_MODEL_PROVIDER", "anthropic")
       problems += model.preflight(model_name)

       return problems
   ```

4. **Verify GREEN.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_registry.py -v
   ```

   Expected: all 12 tests pass.

5. **Commit.**

   ```bash
   git add scripts/factory_core/providers/__init__.py tests/test_provider_registry.py
   git commit -m "feat(providers): env-based tracker/codehost selection + preflight() orchestrator (#250)"
   ```

---

## Task 4: `providers preflight` CLI verb

**Files:** `scripts/factory_core/providers/cli.py`, `tests/test_provider_cli.py`

### Steps

1. **Write failing tests.** In `tests/test_provider_cli.py`, add `import
   pytest` to the existing import block (not currently imported), then
   append:

   ```python
   def test_preflight_ok_prints_ok_and_exits_0(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod
       monkeypatch.setattr(cli_mod, "preflight", lambda: [])
       monkeypatch.setattr(sys, "argv", ["cli.py", "preflight"])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "providers preflight: OK"


   def test_preflight_failure_prints_every_problem_and_exits_1(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod
       monkeypatch.setattr(cli_mod, "preflight", lambda: [
           "GH_TOKEN is not set. Add it to .archon/.env",
           "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env",
       ])
       monkeypatch.setattr(sys, "argv", ["cli.py", "preflight"])
       with pytest.raises(SystemExit) as exc:
           cli_mod.main()
       assert exc.value.code == 1
       err = capsys.readouterr().err
       assert "ERROR: GH_TOKEN is not set. Add it to .archon/.env" in err
       assert "ERROR: Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" in err
   ```

2. **Verify RED.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_cli.py -v
   ```

   Expected: the two new tests fail — `argument provider: invalid choice:
   'preflight'` (no such subcommand yet).

3. **Implement.** In `scripts/factory_core/providers/cli.py`:

   Change the import line:

   ```python
   from factory_core.providers import get_codehost, get_tracker, preflight  # noqa: E402
   ```

   Add a handler function, alongside the other `_codehost_*`/`_tracker_*`
   functions (e.g. directly above `def main():`):

   ```python
   def _preflight(args):
       problems = preflight()
       if problems:
           for p in problems:
               print(f"ERROR: {p}", file=sys.stderr)
           sys.exit(1)
       print("providers preflight: OK")
   ```

   In `main()`, register it as a top-level subcommand — insert right after
   the `codehost = top.add_parser("codehost")` ... `csub` block and before
   `parsed = parser.parse_args()`:

   ```python
       pf = top.add_parser("preflight")
       pf.set_defaults(func=_preflight)

       parsed = parser.parse_args()
       parsed.func(parsed)
   ```

4. **Verify GREEN.**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/test_provider_cli.py -v
   ```

   Expected: all tests in the file pass, including the pre-existing ones.

   Manual smoke check (mirrors how `entrypoint.sh`/`scheduler.sh` will call
   it):

   ```bash
   GH_TOKEN=x CLAUDE_CODE_OAUTH_TOKEN=x PYTHONPATH=scripts python3 scripts/factory_core/providers/cli.py preflight
   # expected: "providers preflight: OK", exit 0
   PYTHONPATH=scripts python3 scripts/factory_core/providers/cli.py preflight; echo "exit=$?"
   # expected: two "ERROR: ... is not set ..." lines on stderr, exit=1
   ```

5. **Commit.**

   ```bash
   git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
   git commit -m "feat(providers): add 'preflight' CLI verb (#250)"
   ```

---

## Task 5: `entrypoint.sh` — replace inline checks with preflight

**Files:** `entrypoint.sh`, `tests/test_entrypoint_preflight.sh` (new)

### Steps

1. **Write failing test.** Create `tests/test_entrypoint_preflight.sh`:

   ```bash
   #!/usr/bin/env bash
   # Verifies entrypoint.sh's provider-aware preflight (parent spec §4) replaced
   # the old inline GH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN checks, and that it still
   # runs before the repo clone.
   # Run: bash tests/test_entrypoint_preflight.sh
   set -euo pipefail
   ep="$(cd "$(dirname "$0")" && pwd)/../entrypoint.sh"

   grep -q 'FACTORY_PROVIDERS_CLI" preflight' "$ep" \
     || { echo "FAIL: entrypoint does not call providers preflight"; exit 1; }

   if grep -qE '^\s*if \[ -z "\$\{GH_TOKEN:-\}" \]; then' "$ep"; then
     echo "FAIL: inline GH_TOKEN check was not removed"; exit 1
   fi
   if grep -q 'CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY' "$ep"; then
     echo "FAIL: inline CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY check was not removed"; exit 1
   fi

   preflight_ln=$(grep -n 'preflight' "$ep" | head -1 | cut -d: -f1)
   clone_ln=$(grep -n '^git clone "\$REPO_URL"' "$ep" | head -1 | cut -d: -f1)
   [ -n "$preflight_ln" ] && [ -n "$clone_ln" ] && [ "$preflight_ln" -lt "$clone_ln" ] \
     || { echo "FAIL: preflight ($preflight_ln) not before git clone ($clone_ln)"; exit 1; }

   echo "PASS"
   ```

2. **Verify RED.**

   ```bash
   chmod +x tests/test_entrypoint_preflight.sh
   bash tests/test_entrypoint_preflight.sh
   ```

   Expected: `FAIL: entrypoint does not call providers preflight` (nothing
   calls `preflight` yet).

3. **Implement.** In `entrypoint.sh`, replace the current top (lines 1-21 —
   note this includes the `source /opt/dark-factory/scripts/identity.sh` line;
   it must be preserved, since it defines `FACTORY_REPO_SLUG`/
   `FACTORY_CLONE_DIR`/`FACTORY_PRODUCT_NAME`/`FACTORY_REPO`, all consumed a
   few lines below):

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail

   # --- Instance identity (env-overridable; defaults = MarketHawk parity) ---
   source /opt/dark-factory/scripts/identity.sh

   # --- Configuration ---
   REPO_URL="https://${GH_TOKEN}@github.com/${FACTORY_REPO_SLUG}.git"
   CLONE_DIR="$FACTORY_CLONE_DIR"
   FACTORY_NAME="${FACTORY_PRODUCT_NAME} Factory"
   FACTORY_EMAIL="factory@${FACTORY_REPO}"

   # --- Validate required environment ---
   if [ -z "${GH_TOKEN:-}" ]; then
     echo "ERROR: GH_TOKEN is not set. Add it to .archon/.env" >&2
     exit 1
   fi
   if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
     echo "ERROR: Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" >&2
     exit 1
   fi
   ```

   with:

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail

   # --- Instance identity (env-overridable; defaults = MarketHawk parity) ---
   source /opt/dark-factory/scripts/identity.sh

   # --- Validate required environment (provider-aware; parent spec §4) ---
   FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-/opt/dark-factory/scripts/factory_core/providers/cli.py}"
   python3 "$FACTORY_PROVIDERS_CLI" preflight

   # --- Configuration ---
   REPO_URL="https://${GH_TOKEN}@github.com/${FACTORY_REPO_SLUG}.git"
   CLONE_DIR="$FACTORY_CLONE_DIR"
   FACTORY_NAME="${FACTORY_PRODUCT_NAME} Factory"
   FACTORY_EMAIL="factory@${FACTORY_REPO}"
   ```

   This also fixes a latent bug on today's `main`: under `set -u`, the old
   line-8 `${GH_TOKEN}` expansion in `REPO_URL=` would crash with a raw
   "unbound variable" before the friendly line-14 check ever ran if
   `GH_TOKEN` were literally unset (as opposed to empty) — moving preflight
   first means the actionable message always wins.

4. **Verify GREEN.**

   ```bash
   bash tests/test_entrypoint_preflight.sh
   bash tests/test_entrypoint_fix_main.sh   # unaffected — pure grep, still PASS
   ```

   Expected: both `PASS`.

5. **Commit.**

   ```bash
   git add entrypoint.sh tests/test_entrypoint_preflight.sh
   git commit -m "feat(entrypoint): replace inline token checks with providers preflight (#250)"
   ```

---

## Task 6: `scheduler.sh` — replace inline checks with preflight

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Steps

1. **Write failing tests.**

   First, remove the now-stale credential-stub lines near the top of
   `tests/test_scheduler.sh` (they exist only to satisfy the inline checks
   this task deletes; the file's `python3()` stub already special-cases
   `*providers/cli.py*` and returns 0 unconditionally, so they'll be dead
   weight):

   ```bash
   # Stub credentials to satisfy the validation block (which runs before SCHEDULER_SOURCE_ONLY guard)
   export GH_TOKEN="${GH_TOKEN:-stub-token}"
   export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
   ```

   → delete these three lines entirely (the blank line before `SCHEDULER_STATE_DIR=$(mktemp ...)` stays).

   Then append a new section right before the final `echo ""` / `echo "---
   O: fetch_board_items pagination ---"` block is fine to leave alone —
   instead add the new section **after** section O, immediately before the
   `echo ""` / `echo "Results: ..."` summary lines at the end of the file:

   ```bash

   # ==========================================
   # P: Provider preflight gate (#250)
   # ==========================================
   echo ""
   echo "--- P: Provider preflight gate ---"

   assert_eq "sourcing calls providers preflight before the poll loop" \
     "1" "$(grep -c 'providers/cli.py preflight' "$STUB_LOG" || echo 0)"

   # Isolated subshell (must not run in-process — a preflight failure trips
   # `set -e` inside scheduler.sh, which would kill this test runner if sourced
   # directly): stub python3 to fail the preflight call and confirm sourcing
   # aborts non-zero, matching the legacy inline `exit 1` behavior.
   set +e
   PREFLIGHT_FAIL_OUT=$(bash -c '
     set -uo pipefail
     python3() {
       case "$*" in
         *providers/cli.py\ preflight*) echo "ERROR: GH_TOKEN is not set. Add it to .archon/.env" >&2; return 1 ;;
         *) command python3 "$@" ;;
       esac
     }
     export -f python3
     export SCHEDULER_STATE_DIR=$(mktemp -d /tmp/sched-test-statedir-XXXXXX)
     SCHEDULER_SOURCE_ONLY=1 source "'"$SCHED"'"
     echo "SHOULD_NOT_REACH_HERE"
   ' 2>&1)
   PREFLIGHT_FAIL_EXIT=$?
   set -e
   assert_eq "preflight failure aborts sourcing (non-zero exit)" \
     "1" "$([ "$PREFLIGHT_FAIL_EXIT" -ne 0 ] && echo 1 || echo 0)"
   assert_eq "preflight failure never reaches past validation" \
     "0" "$(echo "$PREFLIGHT_FAIL_OUT" | grep -c 'SHOULD_NOT_REACH_HERE')"
   ```

   (`set +e`/`set -e` bracket only the subshell capture — the file's own
   top-of-file `set -uo pipefail` has no `-e`, so this is belt-and-suspenders,
   not strictly required, but keeps the section self-contained if that ever
   changes.)

2. **Verify RED.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -20
   ```

   Expected: the new "P:" assertions fail — `sourcing calls providers
   preflight...` fails because nothing in `scheduler.sh` shells out to
   `providers/cli.py preflight` yet (`STUB_LOG` has zero matches); the
   preflight-failure case fails because the *inline* `GH_TOKEN`/token checks
   still gate sourcing, not the (not-yet-existing) preflight call, so
   `SHOULD_NOT_REACH_HERE` legitimately prints as things stand (the stub
   `python3()` inside the subshell never gets exercised by the current
   inline checks).

3. **Implement.** In `scheduler.sh`, replace lines ~100-107:

   ```bash
   # --- Validate required environment ---
   if [ -z "${GH_TOKEN:-}" ]; then
     echo "ERROR: GH_TOKEN is not set. Add it to .archon/.env" >&2
     exit 1
   fi
   if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
     echo "ERROR: Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" >&2
     exit 1
   fi
   ```

   with:

   ```bash
   # --- Validate required environment (provider-aware; parent spec §4) ---
   python3 "$FACTORY_PROVIDERS_CLI" preflight
   ```

   (`FACTORY_PROVIDERS_CLI` is already defined at the top of the file —
   line 15 — no new variable needed here.)

4. **Verify GREEN.**

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -20
   ```

   Expected: `Results: 101 passed, 2 failed` — the two new "P:" assertions
   now pass; the pre-existing `G2`/`I2` failures
   (`STATUS_REFINED`/`STATUS_READY: unbound variable`) are unrelated and
   present on `main` before this change (confirmed by the Task 6 baseline
   run below).

5. **Commit.**

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "feat(scheduler): replace inline token checks with providers preflight (#250)"
   ```

---

## Task 7: Document the new selection env vars

**Files:** `README.md`

### Steps

1. No test — this is documentation only, directly backing the env vars this
   ticket adds (in-scope per the conformance reviewer's documentation
   exception).

2. **Implement.** In `README.md`, immediately after the "### 2. Create
   instance.env" section (after the `Per-instance configs live under
   deploy/instances/...` line, before "### 3. Point PROJECT_DIR..."), add:

   ```markdown
   ### Provider selection (optional)

   Three env vars select the tracker/code-host/model-endpoint providers,
   each defaulting to today's behavior when unset:

   ```bash
   FACTORY_TRACKER=github          # ticket tracker (only "github" implemented today)
   FACTORY_CODEHOST=github         # code host (only "github" implemented today)
   FACTORY_MODEL_PROVIDER=anthropic  # anthropic | bedrock | vertex | databricks | openai
   ```

   `databricks`/`openai` are recognized but not yet implemented (the model
   gateway is a later step); an unknown value for any of the three, or
   missing provider-specific required env, fails startup loudly via
   `providers preflight` — run it directly to check your configuration:

   ```bash
   python3 scripts/factory_core/providers/cli.py preflight
   ```
   ```

3. **Commit.**

   ```bash
   git add README.md
   git commit -m "docs: document FACTORY_TRACKER/FACTORY_CODEHOST/FACTORY_MODEL_PROVIDER (#250)"
   ```

---

## Task 8: Full regression pass

Confirms requirement 8 (existing tests stay green, including
`test_provider_registry.py`'s no-env-set default assertions) and that
nothing else regressed.

**Files:** none (verification only)

### Steps

1. **Run the full Python suite:**

   ```bash
   PYTHONPATH=scripts python3 -m pytest tests/ -v
   ```

   Baseline observed before this branch's changes: `1095 passed`. Expected
   after this plan: `1095 + <new test count>` passed, `0` failed (Tasks 1-4
   add 4 + 10 + 12 + 2 = 28 new/extended assertions across new and existing
   test functions — exact new-test count is whatever `pytest -v`'s collected
   total shows; the important invariant is `0 failed`).

2. **Run the full bash suite** (per `.github/workflows/ci.yml`, plus the two
   files this ticket touches/adds that aren't CI-wired but are part of this
   repo's convention for scheduler/entrypoint coverage):

   ```bash
   bash tests/test_identity.sh
   bash tests/test_hooks.sh
   bash tests/test_smoke_gate.sh
   bash tests/test_run_compose.sh
   bash tests/test_entrypoint_fix_main.sh
   bash tests/test_entrypoint_preflight.sh
   bash tests/test_scheduler.sh
   ```

   Expected: all `PASS` except `test_scheduler.sh`, which reports `Results:
   101 passed, 2 failed` — the 2 failures are the pre-existing `G2`/`I2`
   cases, confirmed present on `main` before this branch's changes (observed
   baseline: `99 passed, 2 failed` prior to Task 6's two new "P:" assertions).

3. **Run the DAG checks** (per `.github/workflows/ci.yml` `dag-check` job —
   this ticket touches no workflow/YAML, so this is a safety net):

   ```bash
   python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```

   Expected: both pass (no output / exit 0).

4. **Sanity-check the diff is scoped as planned:**

   ```bash
   git diff main...HEAD --stat
   ```

   Expected: only the files listed in "File Structure" above are touched.

No commit in this task — it is verification-only. If any step surfaces a
failure, fix it under the task where the regression was introduced (add a
new commit; do not amend).

# Wire JiraTracker into the `_TRACKERS` Registry

**Issue:** omniscient/dark-factory#267
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#251 (CLOSED, merged — `JiraTracker` implementation)
**Parent chain:** omniscient/dark-factory#202 (provider-abstraction epic) → #248/#249 (Tracker/CodeHost
contracts, merged) → #250 (provider selection + boot preflight, CLOSED, merged) → #251 (`JiraTracker`
adapter, standalone, CLOSED, merged) → this ticket (registry wiring)

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` (parent spec) lays out a swappable-provider axis for Dark
Factory's ticket tracker. #251 built `JiraTracker` (`scripts/factory_core/providers/tracker/jira.py`)
— a fully `Tracker`-conformant adapter with its own `required_env()` classmethod — but deliberately
shipped it **standalone**, deferring registry wiring because #250 (which owns
`scripts/factory_core/providers/__init__.py`'s `_TRACKERS` dict and `FACTORY_TRACKER` env selection)
was unmerged at the time (#251 Q3/A3).

#250 has since merged. `main`'s `providers/__init__.py` now has:

```python
_TRACKERS = {"github": GitHubTracker}    # extended by later tickets (jira, ...)
```

and a working `get_tracker()` / `preflight()` pair that resolve `FACTORY_TRACKER` (default
`"github"`) against `_TRACKERS`, raising `ProviderConfigError` for unregistered names and running
`required_env()`-driven missing-env checks for whichever tracker is selected.

This ticket is the "trivial follow-up" #251 recommended: add `"jira": JiraTracker` to `_TRACKERS`
so `FACTORY_TRACKER=jira` actually resolves, and add test coverage for that selection path.

**Non-goals:** no changes to `JiraTracker` itself, the `Tracker`/`CodeHost` ABCs, `GitHubTracker`,
`CodeHost`/`_CODEHOSTS`, `epic_autopilot.py`, or any adapter beyond the tracker registry entry.

## Requirements

Distilled from the issue and Q&A below:

1. Add `"jira": JiraTracker` to `_TRACKERS` in `scripts/factory_core/providers/__init__.py`, plus
   the corresponding `from factory_core.providers.tracker.jira import JiraTracker` import, so
   `get_tracker()` returns a `JiraTracker` instance when `FACTORY_TRACKER=jira` and `preflight()`
   runs `JiraTracker.required_env()` (`JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN`,
   `JIRA_EPIC_LINK_FIELD`) against the environment when `jira` is selected.
2. Add a positive selection test to `tests/test_provider_registry.py` mirroring the existing
   `test_get_tracker_explicit_github_selection` pattern: with `FACTORY_TRACKER=jira` set,
   `get_tracker()` returns an instance of `JiraTracker`.
3. **Repair the two existing tests that use `"jira"` as their unregistered-tracker placeholder**
   (see Q1/A1) — both currently assert `ProviderConfigError` / a preflight problem string for
   `FACTORY_TRACKER=jira`, which becomes false the moment requirement 1 lands:
   - `test_get_tracker_unknown_raises` — swap the placeholder to `"asana"` (setenv value and the
     `match=` regex).
   - `test_preflight_unknown_tracker_codehost_model_all_reported` — swap `FACTORY_TRACKER`'s value
     to `"asana"` and the asserted problem string to match; leave `FACTORY_CODEHOST=gitlab` and
     `FACTORY_MODEL_PROVIDER=cohere` untouched (both remain genuinely unregistered).
4. `python -m pytest tests/ -v` stays green — no other test in the suite references `"jira"` as an
   unknown-provider placeholder (verified: only these two).
5. No changes to `providers/__init__.py` beyond the one dict entry and its import — `get_tracker()`,
   `get_codehost()`, and `preflight()`'s control flow are untouched; `JiraTracker.required_env()`
   already exists (landed by #251) and needs no modification.

## Brainstorming Q&A

> **Q1:** Two existing tests in `tests/test_provider_registry.py` use the string `"jira"` as their
> example of an *unregistered/unknown* tracker (`test_get_tracker_unknown_raises` and
> `test_preflight_unknown_tracker_codehost_model_all_reported`), asserting `ProviderConfigError` /
> a "Unknown FACTORY_TRACKER 'jira'" problem string. The issue's stated scope says only "one dict
> entry + import" and "one selection test" — it doesn't mention these two pre-existing tests, but
> leaving them unrepaired guarantees a red `pytest tests/` the moment `"jira"` becomes a real
> registry key. Should repairing them be treated as in scope?
>
> **A1:** Yes — in scope, and the intended resolution. CLAUDE.md is explicit that CI runs
> `python -m pytest tests/ -v` verbatim and that the conformance/code-review gates require it
> stay green; a registry change that leaves two tests asserting the opposite of the new behavior
> is not a defensible "done" state — it's a mechanical consequence of the sanctioned change, not
> scope creep. Concretely: swap both tests' placeholder value from `"jira"` to `"asana"` (a name
> not on any `_TRACKERS`/roadmap-comment list), updating both the `monkeypatch.setenv` call and the
> asserted error/problem string. Leave `test_preflight_unknown_tracker_codehost_model_all_reported`'s
> `FACTORY_CODEHOST=gitlab` and `FACTORY_MODEL_PROVIDER=cohere` axes untouched — both remain
> genuinely unregistered, so the test still exercises multi-axis unknown-provider reporting. No
> coverage is lost: `"asana"` preserves the unknown-tracker-name assertion that `"jira"` no longer
> can.

## Architecture / Approach

Two-line functional change plus three test edits, all within the two files the issue names:

**`scripts/factory_core/providers/__init__.py`:**
```python
from factory_core.providers.tracker.jira import JiraTracker
...
_TRACKERS = {"github": GitHubTracker, "jira": JiraTracker}
```
No other line in this file changes — `get_tracker()`, `get_codehost()`, and `preflight()` already
resolve any name present in `_TRACKERS`/`_CODEHOSTS` generically (verified by reading the current
implementation), so adding the dict entry is sufficient for both selection and preflight env-var
checking to work end-to-end for `jira`.

**`tests/test_provider_registry.py`:**
1. New test, mirroring `test_get_tracker_explicit_github_selection`:
   ```python
   def test_get_tracker_explicit_jira_selection(monkeypatch):
       from factory_core.providers import get_tracker
       from factory_core.providers.tracker.jira import JiraTracker
       monkeypatch.setenv("FACTORY_TRACKER", "jira")
       assert isinstance(get_tracker(), JiraTracker)
   ```
2. `test_get_tracker_unknown_raises`: placeholder `"jira"` → `"asana"` in both the `setenv` call
   and the `pytest.raises(..., match=...)` string.
3. `test_preflight_unknown_tracker_codehost_model_all_reported`: same placeholder swap for the
   `FACTORY_TRACKER` axis only.

This is the only file set touched; no changes to `JiraTracker`, `Tracker`/`CodeHost` ABCs,
`GitHubTracker`, `_CODEHOSTS`, or any other adapter.

## Alternatives Considered

1. **Leave the two now-invalid tests as out of scope, file a separate follow-up ticket to fix
   them.** Rejected: it would land this ticket with a guaranteed-red `pytest tests/`, violating
   CLAUDE.md's CI-green convention and stranding the PR at the code-review/conformance gates for
   no benefit — the fix is a same-file, two-line-per-test placeholder swap with zero design
   ambiguity, not worth a second ticket's overhead.
2. **Pick a different unknown-tracker placeholder than `"asana"`** (e.g. `"bogus-tracker"`,
   `"nonexistent"`). Functionally equivalent; `"asana"` was chosen per Q1/A1 as a real-sounding
   but unimplemented tracker name, consistent with the registry comment style
   (`# extended by later tickets (jira, ...)`) without implying a specific future ticket.

## Open Questions (Non-blocking)

- None. The change is fully mechanical and bounded by the two named files.

## Assumptions

- `JiraTracker.required_env()` (landed by #251) is correct and complete as-is; this ticket does
  not re-verify Jira adapter behavior, only that it is reachable via `FACTORY_TRACKER=jira`.
- No other test file in the repo references `"jira"` as an unknown/placeholder value (verified via
  grep across `tests/` and `scripts/` during context assembly — only the two tests named above).

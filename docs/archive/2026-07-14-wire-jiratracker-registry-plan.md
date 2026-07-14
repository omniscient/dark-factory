# Plan: Wire JiraTracker into the `_TRACKERS` Registry

**Issue:** #267
**Spec:** `docs/superpowers/specs/2026-07-14-wire-jiratracker-registry-design.md`
**Status:** plan

## Goal

Make `FACTORY_TRACKER=jira` resolve end-to-end: `get_tracker()` returns a `JiraTracker`
instance and `preflight()` runs `JiraTracker.required_env()` against the environment,
by adding the one registry entry `#251` deliberately deferred until `#250` (the
`_TRACKERS`/`FACTORY_TRACKER` selection machinery) merged. Also repair the two existing
tests that used the string `"jira"` as their unregistered-tracker placeholder, since
that placeholder goes stale the instant `"jira"` becomes a real key — leaving them
unrepaired guarantees a red `python -m pytest tests/ -v`.

## Architecture

`get_tracker()` and `preflight()` in `scripts/factory_core/providers/__init__.py` already
resolve any name present in `_TRACKERS` generically (dict lookup, `cls()` instantiate,
`cls.required_env()` for preflight) — no control-flow change is needed. The only change
is registering the name:

```
_TRACKERS = {"github": GitHubTracker, "jira": JiraTracker}
```

`tests/test_provider_registry.py` needs one new positive-selection test (mirroring the
existing `test_get_tracker_explicit_github_selection` pattern) plus a placeholder swap in
two pre-existing negative tests that used `"jira"` to mean "some unregistered tracker" —
swapped to `"asana"` (per spec Q1/A1), which stays genuinely unregistered.

## Tech Stack

- Python 3, pytest (`tests/test_provider_registry.py`)
- No other layers touched — no ABC, adapter, CLI, or config changes (per spec Non-goals)

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/__init__.py` | Add `from factory_core.providers.tracker.jira import JiraTracker` import; add `"jira": JiraTracker` to `_TRACKERS` |
| `tests/test_provider_registry.py` | Add `test_get_tracker_explicit_jira_selection`; swap the `"jira"` unregistered-tracker placeholder to `"asana"` in `test_get_tracker_unknown_raises` and `test_preflight_unknown_tracker_codehost_model_all_reported` |

---

## Task 1: Repair the two placeholder tests

**Files:** `tests/test_provider_registry.py`

This is a same-behavior placeholder rename, not new behavior: `"asana"` is exactly as
unregistered as `"jira"` currently is, so both tests pass identically before and after
this edit. No TDD red/green cycle applies — verified by running the two tests
unchanged-but-renamed before moving to Task 2, where adding the real `"jira"` entry
would otherwise make these two assertions false.

### Step 1.1 — swap `test_get_tracker_unknown_raises`

In `tests/test_provider_registry.py`, replace:

```python
def test_get_tracker_unknown_raises(monkeypatch):
    from factory_core.providers import ProviderConfigError, get_tracker
    monkeypatch.setenv("FACTORY_TRACKER", "jira")
    with pytest.raises(ProviderConfigError, match="Unknown FACTORY_TRACKER 'jira'"):
        get_tracker()
```

with:

```python
def test_get_tracker_unknown_raises(monkeypatch):
    from factory_core.providers import ProviderConfigError, get_tracker
    monkeypatch.setenv("FACTORY_TRACKER", "asana")
    with pytest.raises(ProviderConfigError, match="Unknown FACTORY_TRACKER 'asana'"):
        get_tracker()
```

### Step 1.2 — swap `test_preflight_unknown_tracker_codehost_model_all_reported`

Replace:

```python
def test_preflight_unknown_tracker_codehost_model_all_reported(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.setenv("FACTORY_TRACKER", "jira")
    monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
    monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "cohere")
    problems = preflight()
    assert "Unknown FACTORY_TRACKER 'jira'" in problems
    assert "Unknown FACTORY_CODEHOST 'gitlab'" in problems
    assert "Unknown FACTORY_MODEL_PROVIDER 'cohere'" in problems
```

with (only the `FACTORY_TRACKER` axis changes — `FACTORY_CODEHOST=gitlab` and
`FACTORY_MODEL_PROVIDER=cohere` stay untouched, both remain genuinely unregistered):

```python
def test_preflight_unknown_tracker_codehost_model_all_reported(monkeypatch):
    from factory_core.providers import preflight
    monkeypatch.setenv("FACTORY_TRACKER", "asana")
    monkeypatch.setenv("FACTORY_CODEHOST", "gitlab")
    monkeypatch.setenv("FACTORY_MODEL_PROVIDER", "cohere")
    problems = preflight()
    assert "Unknown FACTORY_TRACKER 'asana'" in problems
    assert "Unknown FACTORY_CODEHOST 'gitlab'" in problems
    assert "Unknown FACTORY_MODEL_PROVIDER 'cohere'" in problems
```

### Step 1.3 — verify both still pass

```bash
python -m pytest tests/test_provider_registry.py -k "unknown_raises or unknown_tracker_codehost_model" -v
```
Expected output: `2 passed`.

### Step 1.4 — commit

```bash
git add tests/test_provider_registry.py
git commit -m "test(providers): swap stale 'jira' unregistered-tracker placeholder to 'asana' (#267)"
```

---

## Task 2: Register `JiraTracker` in `_TRACKERS`

**Files:** `scripts/factory_core/providers/__init__.py`, `tests/test_provider_registry.py`

### Step 2.1 — write the failing test

Append to `tests/test_provider_registry.py` (after `test_get_tracker_explicit_github_selection`):

```python
def test_get_tracker_explicit_jira_selection(monkeypatch):
    from factory_core.providers import get_tracker
    from factory_core.providers.tracker.jira import JiraTracker
    monkeypatch.setenv("FACTORY_TRACKER", "jira")
    assert isinstance(get_tracker(), JiraTracker)
```

### Step 2.2 — verify it fails

```bash
python -m pytest tests/test_provider_registry.py -k test_get_tracker_explicit_jira_selection -v
```
Expected output: `1 failed` — `ProviderConfigError: Unknown FACTORY_TRACKER 'jira'` raised
inside `get_tracker()`, uncaught by the test (no `pytest.raises` wraps this call).

### Step 2.3 — implement

In `scripts/factory_core/providers/__init__.py`, replace:

```python
import os

from factory_core.providers import model
from factory_core.providers.codehost.github import GitHubCodeHost
from factory_core.providers.tracker.github import GitHubTracker

_TRACKERS = {"github": GitHubTracker}    # extended by later tickets (jira, ...)
_CODEHOSTS = {"github": GitHubCodeHost}  # extended by later tickets (gitlab, ...)
```

with:

```python
import os

from factory_core.providers import model
from factory_core.providers.codehost.github import GitHubCodeHost
from factory_core.providers.tracker.github import GitHubTracker
from factory_core.providers.tracker.jira import JiraTracker

_TRACKERS = {"github": GitHubTracker, "jira": JiraTracker}
_CODEHOSTS = {"github": GitHubCodeHost}  # extended by later tickets (gitlab, ...)
```

No other line in this file changes — `get_tracker()`, `get_codehost()`, and
`preflight()` already resolve any name present in `_TRACKERS`/`_CODEHOSTS` generically.

### Step 2.4 — verify it passes

```bash
python -m pytest tests/test_provider_registry.py -k test_get_tracker_explicit_jira_selection -v
```
Expected output: `1 passed`.

### Step 2.5 — run the full suite

```bash
python -m pytest tests/ -v
```
Expected output: all tests pass, no `FAILED` lines — including the two repaired tests
from Task 1 and the new selection test from this task.

### Step 2.6 — commit

```bash
git add scripts/factory_core/providers/__init__.py tests/test_provider_registry.py
git commit -m "feat(providers): register JiraTracker in the _TRACKERS registry (#267)"
```

---

## Validation summary (maps to spec's Requirements)

- **Requirement 1** (registry wiring): Task 2, Step 2.3 — one import + one dict entry;
  `get_tracker()`/`preflight()` control flow unchanged.
- **Requirement 2** (positive selection test): Task 2, Steps 2.1–2.4.
- **Requirement 3** (repair the two stale placeholder tests): Task 1.
- **Requirement 4** (`python -m pytest tests/ -v` stays green): Task 2, Step 2.5.
- **Requirement 5** (no other `providers/__init__.py` changes): Task 2, Step 2.3 diff is
  exactly the import line + dict entry — verified by the diff itself, no other line moves.

## Known limitations (carried from spec, no code action)

- `JiraTracker.required_env()` itself is not re-verified — it landed with #251 and is
  treated as correct; this plan only makes it reachable via `FACTORY_TRACKER=jira`.
- No other test file in the repo references `"jira"` as an unknown/placeholder value
  (verified via grep during spec assembly) — this plan does not re-grep beyond the two
  named tests.

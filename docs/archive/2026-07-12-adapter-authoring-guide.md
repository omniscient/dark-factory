# Implementation Plan: Adapter-Authoring Guide and GitLab CodeHost Seam Proof

**Issue:** omniscient/dark-factory#254
**Spec:** `docs/superpowers/specs/2026-07-11-adapter-authoring-guide-design.md`
**Authoritative source:** `docs/provider-abstraction-design.md` (§§5, 6, 7, 8, 10, 12)
**Depends on (issue body, gates implementation dispatch only):** omniscient/dark-factory#251
(Jira tracker adapter), omniscient/dark-factory#208 (reference model gateway) — both still
OPEN; see [Verified Prerequisite State](#verified-prerequisite-state) below for what this plan
actually needs vs. what the issue's dependency chain declares.

---

## Goal

Ship `docs/adapter-authoring-guide.md` — a single, consolidated reference doc that lets a new
adapter author identify every mandatory method, required env/secret, and test bar for the
Tracker, CodeHost, and model-endpoint axes — plus a `GitLabCodeHost` seam-proof stub
(`scripts/factory_core/providers/codehost/gitlab.py`) that demonstrably (via executing tests)
accepts opaque, non-numeric change identifiers and never assumes GitHub CLI/numeric-PR
semantics. A full, live-validated GitLab implementation is explicitly out of scope.

## Architecture

- **One consolidated guide file**, not three per-axis files — matches every other durable
  `docs/` reference doc in this repo (`dark-factory-token-optimization.md`,
  `dark-factory-memory-contract.md`) and avoids duplicating the cross-axis mixed-provider-close
  material. This decision is already baked into the spec and confirmed by
  `.archon/memory/architecture.md`'s `[AVOID]` entry for issue #254 (loaded via
  `load_memory_context.sh plan`): *"write ONE consolidated `docs/<topic>.md` file with
  per-facet sections, not one file per facet."* Honored throughout — no per-axis file is
  created anywhere in this plan.
- **`GitLabCodeHost` is a real, importable `CodeHost` subclass**, not a documentation-only
  sketch. Two tiers, per the spec's Q&A: pure-mapping methods (`remote_url`, `close_keyword`,
  plus private draft-prefix/id-validation helpers) execute for real and are unit-tested;
  HTTP-backed methods (the other 9 ABC methods) raise `NotImplementedError` and are proven, via
  a real executing test, to raise that specific exception — not some other crash caused by a
  hidden GitHub-shaped assumption (e.g. an `int()` coercion) — when called with an opaque,
  punctuation-heavy GitLab MR id (`"group/project!42"`).
- **A new, proportionate shared contract-test harness** (`tests/test_provider_codehost_contract.py`)
  is added and wired in with both `GitHubCodeHost` and `GitLabCodeHost` parametrized into it.
  See [Verified Prerequisite State](#verified-prerequisite-state) for why this harness must be
  built in this ticket rather than reused from #248.
- **Existing GitHub reference adapters are untouched.** This plan adds files only; it never
  edits `scripts/factory_core/providers/codehost/github.py`,
  `scripts/factory_core/providers/tracker/github.py`, or their existing parity tests.

## Tech Stack

Python (`pytest`, stdlib `subprocess`/`os` — no new third-party dependencies), Markdown (the
guide itself), no bash/YAML/DAG changes.

---

## Verified Prerequisite State

The spec's [Dependency Basis](../specs/2026-07-11-adapter-authoring-guide-design.md#dependency-basis)
section flagged the epic chain `#248→#249→#250→{#251,#208}` as fully open at spec-writing time
and asked the plan phase to re-verify before committing to a task breakdown. Re-verified now
(2026-07-12) directly against `main` and `gh issue view`:

| Issue | State | Relevant to this ticket? |
|---|---|---|
| #248 (Tracker/CodeHost ABCs + GitHub reference adapters + parity net) | **CLOSED, merged to `main`** (PR #255) | Yes — this is the actual prerequisite. `scripts/factory_core/providers/{codehost,tracker}/{base,github}.py`, `providers/__init__.py` (`get_tracker()`/`get_codehost()`), and `providers/cli.py` all exist on `main` today. |
| #249 (route bash/DAG through provider CLIs) | **CLOSED, merged to `main`** (PR #261) | No — irrelevant to this ticket's file set. |
| #250 (provider selection + preflight) | OPEN (`spec-pending-review`) | No — `FACTORY_TRACKER`/`FACTORY_CODEHOST` selection logic isn't touched by this ticket; the guide documents the *intended* env-var contract from the design doc (§4), which doesn't require #250's code to exist. |
| #251 (Jira tracker adapter) | OPEN (`spec-pending-review`) | No — this ticket's Tracker-adapter guide section is sourced from design-doc §5, not from a `JiraTracker` implementation. |
| #208 (model gateway) | OPEN (`spec-pending-review`) | No — the guide's model-endpoint section is sourced from design-doc §7, not from a live gateway. |

**Conclusion:** the two issues the ticket body declares as `Depends on:` (#251, #208) are not
technical blockers for anything this plan actually builds — they reflect the product owner's
intended epic-sequencing/dispatch order, which is a scheduler concern (`Depends on:` gates
*implementation dispatch* per `CLAUDE.md`'s convention), not a content dependency for this
plan. No action is needed from this plan; flagging it explicitly so a human reading the
dependency chain isn't surprised that implementation can proceed on the actual file set below
once dispatched, even while #250/#251/#208 remain open.

**Branch note (not a plan task):** this `refine/issue-254-*` branch was cut before #248/#249
merged to `main` and is currently 28 commits behind `main` — but that is irrelevant to
implementation. Per `workflows/archon-dark-factory.yaml`'s `setup-branch` step, the `implement`
phase creates a **fresh** `feat/issue-254-*` branch directly off `main` (not off this refine
branch); only the spec and this plan markdown are carried over onto it (per
`.archon/memory/codebase-patterns.md`'s `[PATTERN]` on spec/plan transfer). That fresh branch
will already contain #248/#249's merged provider package. No branch-sync task is included here.

**Contract-test harness gap (the spec's flagged size risk, now confirmed real):** the spec's
Testing section warned that if #248 didn't land "a *reusable abstract* `CodeHost` contract-test
class, not just golden-argv parity tests," standing one up becomes part of this ticket's scope.
Verified against `main`: `tests/test_provider_codehost_base.py` only asserts
`CodeHost.__abstractmethods__` and abstractness (2 tests); `tests/test_provider_codehost_parity.py`
is GitHub-specific golden-argv (mocks `subprocess.run`, asserts exact `gh` argv) — neither is a
reusable, implementation-parametrized suite. **Tasks 1 and 4 below build a proportionate one**
(structural/opaque-ID contract only, no VCR/live-HTTP fixtures — those remain explicitly out of
scope per the spec's non-goals and design-doc §10's later, fuller VCR-style vision).

---

## File Structure

| File | Change |
|---|---|
| `tests/test_provider_codehost_contract.py` | New — shared abstract `CodeHost` contract-test harness, parametrized over `GitHubCodeHost` and `GitLabCodeHost` |
| `scripts/factory_core/providers/codehost/gitlab.py` | New — `GitLabCodeHost` seam-proof stub |
| `tests/test_provider_codehost_gitlab.py` | New — unit tests for `GitLabCodeHost`'s pure-mapping methods, helpers, and NotImplementedError stubs |
| `docs/adapter-authoring-guide.md` | New — the consolidated adapter-authoring guide |
| `tests/test_adapter_authoring_guide.py` | New — content-assertion tests for the guide |
| `README.md` | Modified — one line added to "Further reading" |

---

## Memory Context Applied

One accumulated-memory lesson is baked into this plan (not left as a separate advisory
section):

1. **`.archon/memory/architecture.md` `[AVOID]` (issue #254, refine phase):** *"write ONE
   consolidated `docs/<topic>.md` file with per-facet sections, not one file per facet."*
   Applied throughout — Task 5 writes a single `docs/adapter-authoring-guide.md` with
   in-file section headers for all three axes plus a cross-axis section, exactly matching the
   spec's Architecture section 1 outline. No per-axis file is created.

No other loaded memory entries (`codebase-patterns.md`'s spec/plan-transfer and two-dot-diff
patterns; `dark-factory-ops.md`'s `get_change_checks` fail-open caveat) apply to file *content*
decisions in this plan — the transfer pattern is implement-phase behavior (noted above under
Verified Prerequisite State so the implement-phase agent isn't surprised), and the
`get_change_checks` caveat concerns a live-HTTP behavior this ticket's stub never reaches
(`GitLabCodeHost.get_change_checks` raises `NotImplementedError` unconditionally — it never
calls `gh` or any host API and therefore cannot exhibit that failure mode).

---

## Task 1: Shared CodeHost contract-test harness (parametrized against `GitHubCodeHost` only)

**Files:** `tests/test_provider_codehost_contract.py` (new)

This harness is written first, against the implementation that already exists and already
conforms (`GitHubCodeHost`), so it is provably correct — passing green — before `GitLabCodeHost`
exists at all. Task 4 later extends the same file's `IMPLEMENTATIONS` table to add GitLab; no
other test in this file changes shape when that happens.

1. Write the test file:

```python
"""Shared CodeHost contract-test suite (design doc §10) — the reusable base every
CodeHost implementation parametrizes into, alongside its own golden-argv/parity
suite. This suite proves the ABC itself is host-agnostic: every assertion here
must hold for every implementation's own opaque id shape without any per-host
branching in the test body — a hidden GitHub-shaped assumption in a method
signature would fail here even though it passes GitHubCodeHost's own parity
suite. Structural/opaque-ID contract only (no VCR/live-HTTP fixtures — each
implementation's own parity/unit-test file covers real I/O).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core.providers.codehost.base import CodeHost
from factory_core.providers.codehost.github import GitHubCodeHost

# name -> (implementation class, an opaque id shaped like that host's real ids,
#          whether its HTTP-backed methods are unimplemented stubs)
IMPLEMENTATIONS = {
    "github": (GitHubCodeHost, "42", False),
}

HTTP_BACKED_ARGS = {
    "find_change_for": ("feat/issue-1-x",),
    "open_change": (None, None, "title", "body"),
    "update_change_body": ("{id}", "body"),
    "mark_ready": ("{id}",),
    "merge_change": ("{id}",),
    "get_change_checks": ("{id}",),
    "get_change_mergeable": ("{id}",),
    "get_change_reviews": ("{id}",),
    "get_change_inline_comments": ("{id}",),
}


@pytest.fixture(params=sorted(IMPLEMENTATIONS), ids=sorted(IMPLEMENTATIONS))
def impl(request, monkeypatch):
    name = request.param
    cls, change_id, is_stub = IMPLEMENTATIONS[name]
    if name == "github":
        monkeypatch.setenv("GH_TOKEN", "x")
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
        )
    return cls, change_id, is_stub


def test_implementation_is_instantiable_codehost(impl):
    # ABC instantiation itself raises TypeError if any of base.py's 11 abstract
    # methods is left unimplemented — the strongest available "declares the
    # full contract" assertion.
    cls, _change_id, _is_stub = impl
    assert issubclass(cls, CodeHost)
    cls()


def test_remote_url_is_a_string(impl):
    cls, _change_id, _is_stub = impl
    assert isinstance(cls().remote_url(), str)


def test_close_keyword_is_a_string_never_none(impl):
    cls, _change_id, _is_stub = impl
    result = cls().close_keyword("99")
    assert isinstance(result, str)


@pytest.mark.parametrize("method_name", sorted(HTTP_BACKED_ARGS))
def test_http_backed_method_accepts_hosts_own_opaque_id_shape(impl, method_name):
    cls, change_id, is_stub = impl
    args = tuple(
        a.format(id=change_id) if isinstance(a, str) else a
        for a in HTTP_BACKED_ARGS[method_name]
    )
    if is_stub:
        with pytest.raises(NotImplementedError):
            getattr(cls(), method_name)(*args)
        return
    getattr(cls(), method_name)(*args)  # must not raise given a mocked subprocess boundary
```

2. Run it — expect all tests to **pass** immediately (this is a verification harness against an
   already-conformant implementation, not new behavior — same style as #248's
   `test_codehost_is_abstract_with_required_ops`):

```bash
python -m pytest tests/test_provider_codehost_contract.py -v
```

Expected output: `12 passed` — 3 unparametrized tests + 9 `method_name` parametrizations of the
4th test, all under the single `github` fixture param.

3. Commit:

```bash
git add tests/test_provider_codehost_contract.py
git commit -m "test(providers): add shared CodeHost contract-test harness (GitHub only)"
```

---

## Task 2: `GitLabCodeHost` pure-mapping methods and helpers

**Files:** `scripts/factory_core/providers/codehost/gitlab.py` (new),
`tests/test_provider_codehost_gitlab.py` (new)

1. Write the failing test file:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import identity


def test_remote_url_uses_oauth2_form_with_default_host(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-faketoken")
    url = GitLabCodeHost().remote_url()
    assert url == f"https://oauth2:glpat-faketoken@gitlab.com/{identity.SLUG}.git"


def test_remote_url_honors_self_hosted_base_url(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("GITLAB_TOKEN", "glpat-faketoken")
    monkeypatch.setenv("GITLAB_BASE_URL", "gitlab.example.com")
    url = GitLabCodeHost().remote_url()
    assert url == f"https://oauth2:glpat-faketoken@gitlab.example.com/{identity.SLUG}.git"


def test_close_keyword_empty_when_gitlab_not_the_tracker(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("FACTORY_TRACKER", "github")
    assert GitLabCodeHost().close_keyword("99") == ""


def test_close_keyword_empty_by_default(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.delenv("FACTORY_TRACKER", raising=False)
    assert GitLabCodeHost().close_keyword("99") == ""


def test_close_keyword_present_when_gitlab_is_also_the_tracker(monkeypatch):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    monkeypatch.setenv("FACTORY_TRACKER", "gitlab")
    assert GitLabCodeHost().close_keyword("99") == "Closes #99"


def test_draft_title_adds_prefix_once():
    from factory_core.providers.codehost.gitlab import _draft_title

    assert _draft_title("Fix the thing") == "Draft: Fix the thing"
    assert _draft_title("Draft: Fix the thing") == "Draft: Fix the thing"


def test_strip_draft_prefix_removes_it():
    from factory_core.providers.codehost.gitlab import _strip_draft_prefix

    assert _strip_draft_prefix("Draft: Fix the thing") == "Fix the thing"
    assert _strip_draft_prefix("Fix the thing") == "Fix the thing"


def test_validate_change_id_accepts_opaque_gitlab_shape():
    from factory_core.providers.codehost.gitlab import _validate_change_id

    assert _validate_change_id("group/project!42") == "group/project!42"


def test_validate_change_id_rejects_non_string():
    from factory_core.providers.codehost.gitlab import _validate_change_id

    with pytest.raises(ValueError):
        _validate_change_id(42)
```

2. Verify it fails (module doesn't exist yet):

```bash
python -m pytest tests/test_provider_codehost_gitlab.py -v
```

Expected output: every test errors with `ModuleNotFoundError: No module named
'factory_core.providers.codehost.gitlab'`.

3. Commit the failing test:

```bash
git add tests/test_provider_codehost_gitlab.py
git commit -m "test(providers): add failing tests for GitLabCodeHost pure-mapping methods"
```

4. Implement the pure-mapping half of `scripts/factory_core/providers/codehost/gitlab.py`
   (the HTTP-backed methods are added in Task 3 — this file is edited again there, not
   replaced):

```python
"""GitLabCodeHost — seam proof (design doc §6.3) that the CodeHost ABC (design
doc §6.1) is not GitHub-shaped. Pure-mapping methods below (remote_url,
close_keyword, and the private draft-prefix/id-validation helpers) run for
real and are unit-tested; every HTTP-backed operation raises
NotImplementedError — a full, live-validated GitLab implementation is an
explicit follow-up ticket, filed only if requested (see
docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof).
"""
import os

from factory_core import identity
from factory_core.providers.codehost.base import CodeHost

_DRAFT_PREFIX = "Draft: "


def _draft_title(title: str) -> str:
    return title if title.startswith(_DRAFT_PREFIX) else f"{_DRAFT_PREFIX}{title}"


def _strip_draft_prefix(title: str) -> str:
    return title[len(_DRAFT_PREFIX):] if title.startswith(_DRAFT_PREFIX) else title


def _validate_change_id(id: str) -> str:
    """Opaque-string contract (design doc principle 5): a GitLab MR id is
    `<group/project>!<iid>` — never coerce it to int anywhere in this adapter."""
    if not isinstance(id, str) or not id:
        raise ValueError(f"GitLab change id must be a non-empty opaque string, got {id!r}")
    return id


class GitLabCodeHost(CodeHost):
    """GitLab MR seam proof — see docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof."""

    def remote_url(self) -> str:
        token = os.environ.get("GITLAB_TOKEN", "")
        host = os.environ.get("GITLAB_BASE_URL", "gitlab.com")
        return f"https://oauth2:{token}@{host}/{identity.SLUG}.git"

    def close_keyword(self, issue_id: str) -> str:
        if os.environ.get("FACTORY_TRACKER", "github") != "gitlab":
            return ""
        return f"Closes #{issue_id}"
```

5. `GitLabCodeHost` still subclasses `CodeHost` without implementing 9 of its 11
   `@abstractmethod`s at this point (they're added in Task 3), so **any test that calls
   `GitLabCodeHost()` still fails here** — Python raises `TypeError: Can't instantiate
   abstract class GitLabCodeHost without an implementation for abstract methods ...` for
   `remote_url`/`close_keyword` tests too, even though the methods they exercise are already
   written, because ABC instantiation checks completeness of the whole class, not the method
   being called. Only the 4 tests that call the private module-level helper functions directly
   (`_draft_title`, `_strip_draft_prefix`, `_validate_change_id` — no `GitLabCodeHost()` call)
   can pass yet:

```bash
python -m pytest tests/test_provider_codehost_gitlab.py -v -k "draft_title or strip_draft_prefix or validate_change_id"
```

Expected output: `4 passed`. The 5 `remote_url`/`close_keyword` tests are left red at this
checkpoint (`TypeError`, not a bug in the pure-mapping code) and turn green together with the
rest of the file once Task 3 adds the remaining 9 stub methods and the class becomes
instantiable — verified at the start of Task 3 step 2 and confirmed passing at Task 3 step 5.

6. Commit:

```bash
git add scripts/factory_core/providers/codehost/gitlab.py
git commit -m "feat(providers): GitLabCodeHost remote_url/close_keyword + draft/id helpers"
```

---

## Task 3: `GitLabCodeHost` HTTP-backed stub methods

**Files:** `scripts/factory_core/providers/codehost/gitlab.py` (edit),
`tests/test_provider_codehost_gitlab.py` (edit)

1. Append the failing tests for the 9 HTTP-backed methods:

```python
@pytest.mark.parametrize("method_name,args", [
    ("update_change_body", ("group/project!42", "body")),
    ("mark_ready", ("group/project!42",)),
    ("merge_change", ("group/project!42",)),
    ("get_change_checks", ("group/project!42",)),
    ("get_change_mergeable", ("group/project!42",)),
    ("get_change_reviews", ("group/project!42",)),
    ("get_change_inline_comments", ("group/project!42",)),
])
def test_id_taking_http_methods_raise_not_implemented_on_opaque_id(method_name, args):
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    with pytest.raises(NotImplementedError):
        getattr(GitLabCodeHost(), method_name)(*args)


def test_find_change_for_raises_not_implemented():
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    with pytest.raises(NotImplementedError):
        GitLabCodeHost().find_change_for("feat/issue-1-x")


def test_open_change_raises_not_implemented():
    from factory_core.providers.codehost.gitlab import GitLabCodeHost

    with pytest.raises(NotImplementedError):
        GitLabCodeHost().open_change(None, None, "title", "body", draft=True)
```

2. Verify these fail — `GitLabCodeHost` is still missing 9 abstract methods, so instantiation
   itself raises `TypeError: Can't instantiate abstract class GitLabCodeHost...`:

```bash
python -m pytest tests/test_provider_codehost_gitlab.py -v -k "not_implemented"
```

Expected output: every new test fails with `TypeError`, not `NotImplementedError` — confirming
they are currently red for the right reason.

3. Commit the failing tests:

```bash
git add tests/test_provider_codehost_gitlab.py
git commit -m "test(providers): add failing tests for GitLabCodeHost HTTP-backed stubs"
```

4. Append the 9 stub methods to `scripts/factory_core/providers/codehost/gitlab.py` (inside the
   `GitLabCodeHost` class, after `close_keyword`):

```python
    def find_change_for(self, branch: str) -> str | None:
        raise NotImplementedError(
            "live GitLab MR list API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def open_change(self, source: str, target: str, title: str, body: str,
                     draft: bool = False) -> str:
        raise NotImplementedError(
            "live GitLab MR create API — deferred; see "
            "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
        )

    def update_change_body(self, id: str, body: str) -> bool:
        _validate_change_id(id)
        raise NotImplementedError("live GitLab MR update API — deferred; see follow-up ticket")

    def mark_ready(self, id: str) -> None:
        _validate_change_id(id)
        raise NotImplementedError(
            "live GitLab MR Draft-prefix removal API — deferred; see follow-up ticket"
        )

    def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True) -> bool:
        _validate_change_id(id)
        raise NotImplementedError("live GitLab MR merge API — deferred; see follow-up ticket")

    def get_change_checks(self, id: str) -> list:
        _validate_change_id(id)
        raise NotImplementedError("live GitLab pipelines API — deferred; see follow-up ticket")

    def get_change_mergeable(self, id: str) -> str:
        _validate_change_id(id)
        raise NotImplementedError(
            "live GitLab merge_status/has_conflicts API — deferred; see follow-up ticket"
        )

    def get_change_reviews(self, id: str) -> str:
        _validate_change_id(id)
        raise NotImplementedError("live GitLab approvals API — deferred; see follow-up ticket")

    def get_change_inline_comments(self, id: str) -> list:
        _validate_change_id(id)
        raise NotImplementedError("live GitLab discussions API — deferred; see follow-up ticket")
```

5. Verify all tests in the file pass, and that `GitLabCodeHost` is now a fully instantiable
   `CodeHost`:

```bash
python -m pytest tests/test_provider_codehost_gitlab.py -v
```

Expected output: `18 passed` (9 from Task 2 + 9 from this task: 7 parametrized id-taking cases
+ `find_change_for` + `open_change`).

```bash
python -c "from factory_core.providers.codehost.gitlab import GitLabCodeHost; from factory_core.providers.codehost.base import CodeHost; assert issubclass(GitLabCodeHost, CodeHost); GitLabCodeHost(); print('instantiable OK')"
```

Expected output: `instantiable OK`.

6. Commit:

```bash
git add scripts/factory_core/providers/codehost/gitlab.py
git commit -m "feat(providers): GitLabCodeHost HTTP-backed stubs raise NotImplementedError"
```

---

## Task 4: Wire `GitLabCodeHost` into the shared contract-test harness

**Files:** `tests/test_provider_codehost_contract.py` (edit)

1. Edit `IMPLEMENTATIONS` and the fixture in `tests/test_provider_codehost_contract.py` from
   Task 1 to add GitLab:

```python
from factory_core.providers.codehost.github import GitHubCodeHost
from factory_core.providers.codehost.gitlab import GitLabCodeHost

IMPLEMENTATIONS = {
    "github": (GitHubCodeHost, "42", False),
    "gitlab": (GitLabCodeHost, "group/project!42", True),
}
```

No other line in the file changes — `impl`'s `if name == "github":` branch already leaves
GitLab unmocked (correct: its HTTP-backed methods must raise before touching any I/O), and
every test function already branches on the `is_stub` flag threaded through the fixture.

2. Run the full file and confirm GitLab is now exercised end-to-end through the exact same
   assertions as GitHub — this is the acceptance criterion's "proves" requirement, satisfied by
   execution:

```bash
python -m pytest tests/test_provider_codehost_contract.py -v
```

Expected output: `24 passed` (12 per implementation: `test_implementation_is_instantiable_codehost`,
`test_remote_url_is_a_string`, `test_close_keyword_is_a_string_never_none`, plus 9
`method_name`-parametrized `test_http_backed_method_accepts_hosts_own_opaque_id_shape` cases —
for `gitlab`, each of those 9 asserts `pytest.raises(NotImplementedError)` and passes; none are
silently skipped or marked `xfail`, because `GitLabCodeHost`'s pure-mapping methods and
id-validation guard make the "opaque id in, correct exception out" behavior fully real and
deterministic rather than dependent on unavailable live GitLab I/O).

3. Confirm the existing GitHub parity/golden-argv suite and the #248 base-abstractness suite
   remain untouched and green (spec Requirement 3):

```bash
python -m pytest tests/test_provider_codehost_base.py tests/test_provider_codehost_parity.py tests/test_provider_registry.py -v
```

Expected output: all pass, unchanged from before this ticket.

4. Commit:

```bash
git add tests/test_provider_codehost_contract.py
git commit -m "test(providers): parametrize GitLabCodeHost into the shared CodeHost contract suite"
```

---

## Task 5: Write `docs/adapter-authoring-guide.md`

**Files:** `tests/test_adapter_authoring_guide.py` (new), `docs/adapter-authoring-guide.md` (new)

This is a documentation task — no TDD red/green in the code sense, but a failing
content-assertion test is still written first (matching this repo's existing precedent for
doc-only tickets, e.g. `tests/test_run_skill_files.py` from issue #47), so the guide's required
content is machine-checked, not just eyeballed.

1. Write the failing test file:

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPO_ROOT / "docs" / "adapter-authoring-guide.md"


def _text():
    return GUIDE.read_text(encoding="utf-8")


def test_guide_exists_with_required_top_level_sections():
    text = _text()
    for heading in (
        "## Overview",
        "## Tracker adapter",
        "## Code-host adapter",
        "## Model-endpoint adapter",
        "## Cross-axis concerns",
        "## Worked example: GitLab CodeHost seam proof",
    ):
        assert heading in text, f"missing section: {heading}"


def test_guide_documents_tracker_required_methods():
    text = _text()
    for method in (
        "list_work_items", "get_item", "get_comments", "get_children", "set_status",
        "add_label", "remove_label", "upsert_comment", "create_item", "resolve_item",
        "get_status_limits", "get_rate_budget",
    ):
        assert f"`{method}" in text, f"missing tracker method: {method}"
    assert "FACTORY_STATUS_BACKLOG" in text
    assert "ready, in_progress, in_review, blocked, done, backlog, refined" in text


def test_guide_documents_codehost_required_methods():
    text = _text()
    for method in (
        "remote_url", "find_change_for", "open_change", "update_change_body", "mark_ready",
        "merge_change", "get_change_checks", "get_change_mergeable", "get_change_reviews",
        "get_change_inline_comments", "close_keyword",
    ):
        assert f"`{method}" in text, f"missing code-host method: {method}"


def test_guide_documents_model_endpoint_paths():
    text = _text()
    for token in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "databricks", "openai", "bedrock", "vertex"):
        assert token in text


def test_guide_documents_cross_axis_concerns():
    text = _text()
    assert "host.merge_change(id)" in text
    assert "tracker.resolve_item(issue_id)" in text
    assert "FACTORY_TRACKER" in text and "FACTORY_CODEHOST" in text and "FACTORY_MODEL_PROVIDER" in text


def test_guide_links_gitlab_worked_example():
    text = _text()
    assert "scripts/factory_core/providers/codehost/gitlab.py" in text
    assert "test_provider_codehost_gitlab.py" in text or "test_provider_codehost_contract.py" in text


def test_guide_cites_design_doc_sections():
    text = _text()
    assert "provider-abstraction-design.md" in text
    for section in ("§5.1", "§5.2", "§6.1", "§6.3", "§7"):
        assert section in text, f"missing design-doc citation: {section}"
```

2. Verify it fails (file doesn't exist yet):

```bash
python -m pytest tests/test_adapter_authoring_guide.py -v
```

Expected output: every test fails with `FileNotFoundError` —
`docs/adapter-authoring-guide.md` does not exist.

3. Commit the failing test:

```bash
git add tests/test_adapter_authoring_guide.py
git commit -m "test(docs): add failing content assertions for the adapter-authoring guide"
```

4. Write `docs/adapter-authoring-guide.md`:

```markdown
# Adapter Authoring Guide

This is the requirements checklist for adding a new Tracker, CodeHost, or model-endpoint
adapter to Dark Factory (expands `docs/provider-abstraction-design.md` §12). Every table below
is sourced directly from that design doc's numbered sections — cited inline — so this guide and
the design doc cannot silently drift; if they disagree, the design doc is authoritative.

## Overview

Dark Factory has three independent, pluggable provider axes (`docs/provider-abstraction-design.md`
§1, §4.1), all living under `scripts/factory_core/providers/`:

```
providers/
  __init__.py          # get_tracker(), get_codehost() — env-based selection
  tracker/base.py       # Tracker ABC
  tracker/github.py     # GitHubTracker — reference implementation
  codehost/base.py      # CodeHost ABC
  codehost/github.py    # GitHubCodeHost — reference implementation
  cli.py                 # thin CLI: python scripts/factory_core/providers/cli.py tracker|codehost
```

Selection is env-driven, each defaulting to today's GitHub-only behavior (design doc §4):
`FACTORY_TRACKER` (default `github`), `FACTORY_CODEHOST` (default `github`),
`FACTORY_MODEL_PROVIDER` (default `anthropic`). Every adapter's own connection config and
secrets live in the gitignored instance env (`deploy/instance.env`) — **never** in the
committed adapter module.

## Tracker adapter

### Required methods (design doc §5.1)

| Method | Purpose | Degradable? |
|---|---|---|
| `list_work_items(statuses, labels?)` | poll-loop discovery | no |
| `get_item(id)` | title/body/state/labels/status | no |
| `get_comments(id)` | read comment thread | no |
| `get_children(epic_id)` | epic → children | no |
| `set_status(id, canonical)` | move to canonical status | no |
| `add_label(id, name)` / `remove_label(id, name)` | label state machine | no |
| `upsert_comment(id, marker, body)` | idempotent marker comment | no |
| `create_item(title, body, labels)` → id | regression tickets | no |
| `resolve_item(id)` | explicit close-on-merge | no |
| `get_status_limits()` → `{status: n}` | WIP limits | **yes** — safe default `{}` |
| `get_rate_budget()` | throttle poll loop | **yes** — safe default `{"remaining": None, "reset": None, "used": None, "limit": None}` |

All ids are **opaque strings** everywhere (design doc principle 5) — never coerce to `int`
anywhere in a conforming implementation, including in test fixtures.

### Canonical vocabulary (frozen contract, design doc §5.2)

- **Seven canonical statuses:** `ready, in_progress, in_review, blocked, done, backlog, refined`.
  Your adapter maps each to its own representation via the seven `FACTORY_STATUS_*` env vars
  (`FACTORY_STATUS_READY`, `FACTORY_STATUS_IN_PROGRESS`, `FACTORY_STATUS_IN_REVIEW`,
  `FACTORY_STATUS_BLOCKED`, `FACTORY_STATUS_DONE`, `FACTORY_STATUS_BACKLOG`,
  `FACTORY_STATUS_REFINED`) — GitHub uses single-select option IDs; a Jira adapter would use
  status *names* under the same seven variables. No new mapping surface is introduced per adapter.
- **Required label vocabulary:** `ready-for-agent, spec-pending-review, plan-pending-review,
  needs-discussion, factory-regression, above-ceiling-work, direct-to-pr, epic, ready-for-human,
  merged-with-edits, regression`. Your tracker must support hyphenated labels.

### Required env (per-adapter example: Jira, design doc §5.4)

`JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN` (secret), `JIRA_EPIC_LINK_FIELD`, plus the
seven `FACTORY_STATUS_*` vars holding Jira status *names*. Your adapter documents its own
equivalent list following this shape: base URL, project/namespace key, auth token, any
custom-field mapping the axis needs, and the seven status vars.

### Test bar

1. Contract tests — parametrize your `Tracker` into a shared contract-test suite alongside
   `GitHubTracker` (mirrors this ticket's `CodeHost` contract harness at
   `tests/test_provider_codehost_contract.py` — apply the same structural/opaque-ID pattern to
   `Tracker`).
2. Live smoke checklist (run once against a real instance before shipping): create → label →
   comment → transition through all seven canonical statuses → resolve.

## Code-host adapter

### Required methods (design doc §6.1, ~11 methods)

| Method | Purpose |
|---|---|
| `remote_url()` | auth-embedded clone/push URL |
| `find_change_for(branch)` → id | PR/MR open for a branch |
| `open_change(source, target, title, body, draft)` → id | create PR/MR |
| `update_change_body(id, body)` | backfill close-keyword |
| `mark_ready(id)` | draft → ready for review |
| `merge_change(id, strategy, delete_branch)` | merge |
| `get_change_checks(id)` → `[{name, bucket, ...}]` | CI gate |
| `get_change_mergeable(id)` → enum | conflict gate |
| `get_change_reviews(id)` → state | approval gate |
| `get_change_inline_comments(id)` → list | review feedback |
| `close_keyword(issue_id)` → str | close-on-merge snippet **iff tracker == host**, else `""` |

Plain `git` (clone/branch/commit/push/fetch/diff) is host-agnostic and stays **outside** this
contract (design doc principle 3) — the only git-adjacent method here is `remote_url()`.

### `remote_url()` — auth-embedded URL requirement

Must embed the token directly in the URL so plain `git push`/`fetch` authenticate without any
credential helper. GitHub's form: `https://$TOKEN@github.com/<slug>`. GitLab's form:
`https://oauth2:$TOKEN@gitlab.com/<slug>` (see `GitLabCodeHost.remote_url()` below).

### Draft / ready / merge / checks / reviews mapping expectations

Map your host's terms onto the interface's neutral names — e.g. GitLab: MRs replace PRs, a
`Draft:` title prefix replaces the draft flag, the pipelines API replaces checks, the approvals
API replaces reviews (design doc §6.3).

### `close_keyword()` contract

Returns the body snippet that auto-closes an issue on merge (e.g. `"Closes #42"`) **only when
your code host is also the tracker** (design doc §6.4) — otherwise it must return `""`. Getting
this wrong emits a dead close-keyword the tracker can never see.

### Test bar

Parametrize your `CodeHost` into `tests/test_provider_codehost_contract.py`, following the
`GitLabCodeHost` pattern below: pure-mapping methods (no HTTP dependency) run for real and must
pass; methods that require live host I/O may raise `NotImplementedError` during initial seam
work and are proven, via an executing test, to raise *that* exception on an opaque id — not
some other crash — until a full implementation replaces the stub.

## Model-endpoint adapter

### Native fast paths vs. gateway path (design doc §7.2)

- **Native (no gateway):** `anthropic` (default), `bedrock`, `vertex` — Claude Code speaks these
  natively via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` or the `CLAUDE_CODE_USE_BEDROCK`/
  `CLAUDE_CODE_USE_VERTEX` flags. Endpoint-swapping is configuration, not a harness code change.
- **Gateway path:** `databricks` and `openai` don't speak the Anthropic Messages shape, so a
  reference gateway presents `/v1/messages` and routes to the real backend. Set
  `ANTHROPIC_BASE_URL` → the gateway, `ANTHROPIC_AUTH_TOKEN` → the gateway key. Real backend
  secrets live in the gateway config, never in the factory.

### Model-alias mapping (design doc §7.3)

Model names already in the code (`claude-opus-4-8`, `sonnet`, `haiku`, …) stay as tier tokens.
The gateway maps each tier to a real backend model per provider in one config file
(`deploy/gateway/config.yaml`) — `anthropic` is pass-through (parity); `databricks` routes to
the governed Claude endpoint; `openai` routes to the chosen GPT model.

### Cost/quality caveats (design doc §7.5)

- **Cost goes approximate off-Anthropic.** Token counts still flow; dollar figures are
  mislabeled once the backend isn't Anthropic-billed.
- **GPT quality is empirical**, not assumed equivalent to Claude — Databricks-Claude is
  risk-free (identical model, different endpoint); OpenAI is a measured, separate question.

### Preflight requirements

`anthropic` → token present; `databricks`/`openai` → gateway reachable and holding backend
creds; `bedrock`/`vertex` → cloud credentials present. Boot-time preflight is **hard-fail**
(design doc §9) — a misconfigured instance must exit loudly at startup, not fail mid-run.

## Cross-axis concerns

- **Safe failure / fail-open posture (design doc §9).** Degradable tracker ops
  (`get_rate_budget`, `get_status_limits`) return safe defaults. A failed `set_status` or a
  missing status-transition edge logs and leaves the item where it is — same posture as a
  failed board-move today. Boot-time provider preflight is the one hard-fail exception.
- **Idempotency.** `upsert_comment(id, marker, body)` must be a true upsert (create if the
  marker-tagged comment is absent, update in place otherwise) so re-runs never spam a tracker.
- **Secret handling.** Provider secrets live only in the gitignored instance env
  (`deploy/instance.env`), never committed, never hardcoded in an adapter module.
- **Rollback.** Reverting a bad adapter is reverting its selection env var
  (`FACTORY_TRACKER` / `FACTORY_CODEHOST` / `FACTORY_MODEL_PROVIDER`) back to its default — no
  code rollback required if the adapter module itself is otherwise inert when unselected.
- **Mixed-provider close flow (design doc §6.4).** When tracker and code host are the *same*
  provider (e.g. both GitHub), `close_keyword()` in the PR/MR body auto-closes the issue for
  free. When they differ (e.g. Jira tracker + GitHub host), there is no such automatic link, so
  the orchestrator performs the close explicitly and in order:

  ```
  host.merge_change(id) succeeds  →  tracker.resolve_item(issue_id)
  ```

  This is exactly why `close_keyword()` must return `""` when `tracker != host`: emitting a
  dead `"Closes #N"` snippet the tracker can never observe would silently break this invariant.

## Worked example: GitLab CodeHost seam proof

`scripts/factory_core/providers/codehost/gitlab.py` implements `GitLabCodeHost`, a real,
importable `CodeHost` subclass that proves the ABC (§6.1) is not GitHub-shaped, without a live
GitLab instance. It is parametrized into the shared contract suite at
`tests/test_provider_codehost_contract.py` alongside `GitHubCodeHost`, and has its own unit
tests at `tests/test_provider_codehost_gitlab.py`.

| `CodeHost` method | GitLab equivalent (design doc §6.3) | This stub |
|---|---|---|
| `remote_url()` | `https://oauth2:$TOKEN@gitlab.com/<slug>` | **Real** — `GITLAB_TOKEN` + `GITLAB_BASE_URL` (defaults to `gitlab.com`) |
| `close_keyword(issue_id)` | `"Closes #N"` iff GitLab is also the tracker, else `""` | **Real** |
| `find_change_for(branch)` | MR list `?source_branch=` | `NotImplementedError` |
| `open_change(...)` | `POST /merge_requests` with `Draft:` title prefix | `NotImplementedError` (the `Draft:`-prefix mapping itself is proven separately by the real, unit-tested `_draft_title`/`_strip_draft_prefix` helpers) |
| `update_change_body(id, body)` | `PUT /merge_requests` | `NotImplementedError` |
| `mark_ready(id)` | remove `Draft:` prefix | `NotImplementedError` |
| `merge_change(id, ...)` | `PUT /merge_requests/{id}/merge` | `NotImplementedError` |
| `get_change_checks(id)` | pipelines API | `NotImplementedError` |
| `get_change_mergeable(id)` | `merge_status`/`has_conflicts` | `NotImplementedError` |
| `get_change_reviews(id)` | approvals API | `NotImplementedError` |
| `get_change_inline_comments(id)` | discussions API | `NotImplementedError` |

Every id-taking method accepts an **opaque string** shaped like a real GitLab id
(`"group/project!42"`, never coerced to `int`) via the private `_validate_change_id` helper —
this, plus the two real pure-mapping methods above, is the executable proof the acceptance
criterion asks for. A full, live-validated GitLab implementation (real HTTP calls, tested
against a live GitLab instance) is explicitly out of scope for this guide's ticket and is filed
as a separate follow-up only if requested.
```

5. Verify all content-assertion tests pass:

```bash
python -m pytest tests/test_adapter_authoring_guide.py -v
```

Expected output: `7 passed`.

6. Commit:

```bash
git add docs/adapter-authoring-guide.md
git commit -m "docs: add adapter-authoring guide (tracker, code-host, model-endpoint axes)"
```

---

## Task 6: Link the guide from README

**Files:** `README.md` (modified)

1. In `README.md`'s existing "Further reading" section (currently ends with the `bench/baseline.md`
   bullet), add one line:

```markdown
- [`docs/adapter-authoring-guide.md`](docs/adapter-authoring-guide.md) — how to write a tracker, code-host, or model-endpoint adapter
```

2. Verify the link target exists and the line was added:

```bash
grep -n "adapter-authoring-guide.md" README.md
test -f docs/adapter-authoring-guide.md && echo "OK: guide exists"
```

Expected output: the `grep` match plus `OK: guide exists`.

3. Commit:

```bash
git add README.md
git commit -m "docs: link adapter-authoring guide from README further-reading"
```

---

## Task 7: Full-suite regression check

**Files:** none (verification only, no commit unless something needs fixing)

1. Run the complete suite (per `CLAUDE.md` conventions) and confirm everything is green,
   including every new test added by Tasks 1–6:

```bash
python -m pytest tests/ -v
```

Expected output: full pass, zero failures, zero errors, zero unexpected `xfail`/`xpass`.

2. Specifically re-confirm the two invariants the spec calls out as must-stay-green (spec
   Requirement 3, Testing section):

```bash
python -m pytest tests/test_provider_codehost_parity.py tests/test_provider_tracker_parity.py -v
```

Expected output: all pass, byte-identical to their state before this ticket (neither file was
edited by any task above).

3. If any test fails, fix the root cause in the relevant Task's file (do not weaken an
   assertion to make it pass) and re-run this task's commands until clean. No separate commit
   is needed for this task if step 1 is clean on the first run.

---

## Summary of what this plan deliberately does NOT do

Per the spec's explicit non-goals and the issue's acceptance criteria:

- No `github.py` reference adapter (Tracker or CodeHost) is modified.
- No live HTTP call to a real GitLab instance is made anywhere — every HTTP-backed
  `GitLabCodeHost` method raises `NotImplementedError` and only that.
- No `FACTORY_TRACKER`/`FACTORY_CODEHOST` selection/preflight logic is added or changed (#250's
  scope).
- No `JiraTracker` or reference model gateway is implemented (#251's and #208's scope).
- No per-axis guide file is created — everything lands in the single
  `docs/adapter-authoring-guide.md`.

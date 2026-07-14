# Adapter-Authoring Guide and GitLab CodeHost Seam Proof

**Issue:** omniscient/dark-factory#254
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#251 (Jira tracker adapter), omniscient/dark-factory#208
(reference model gateway) — both OPEN as of this refine pass; see
[Dependency Basis](#dependency-basis) below.
**Parent epic:** omniscient/dark-factory#202 (Provider abstraction — pluggable tracker, code
host, and model endpoint)
**Authoritative spec:** `docs/provider-abstraction-design.md` (merged via #203, `c7b286f`) —
this ticket expands its §12 outline into a standalone guide and delivers the §6.3 GitLab sketch.

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` establishes three pluggable provider axes (Tracker,
CodeHost, model endpoint) but only sketches the adapter-authoring requirements in one terse
paragraph per axis (§12). It also defers the GitLab `CodeHost` implementation to a follow-up,
noting only that "the ABC + a stub with the mapping documented guards against a GitHub-shaped
interface" (§6.3).

Before a real second `CodeHost` (GitLab) or a third `Tracker` beyond Jira gets built, two gaps
need closing:

1. **No standalone requirements document.** A new adapter author currently has to reverse
   engineer the full method contract, required env, secret handling, and test bar from the
   design doc's compressed tables plus whatever the GitHub/Jira reference implementations
   happen to do. Nothing states this as an explicit, authoritative checklist.
2. **No proof the `CodeHost` ABC is actually host-agnostic.** The design doc's interface table
   (§6.1) is derived by inspection of GitHub's shape; without a second, structurally different
   host actually exercising it, "not GitHub-shaped" is an assertion, not a demonstrated
   property. A stub that only exists as prose cannot catch a numeric-ID assumption baked into a
   method signature.

This ticket produces the **adapter-authoring guide** (`docs/adapter-authoring-guide.md`) and
the **GitLab `CodeHost` stub** (`scripts/factory_core/providers/codehost/gitlab.py`) — the
guide is a durable reference document; the stub is real code wired into the shared contract
test suite, but with a `NotImplementedError` behind every method that would require live
GitLab I/O. A full, working GitLab implementation (real HTTP calls, live-validated) is
explicitly deferred to a separate follow-up ticket, filed only if requested.

---

## Dependency Basis

The epic's dependency DAG (from #202) is:

```
#248 Tracker/CodeHost ABCs + GitHub reference adapters + parity net
  -> #249 Route hosted calls through provider CLIs
      -> #250 Provider selection + preflight
          -> #251 Jira adapter + fixtures  ─┐
          -> #208 Reference model gateway  ─┴─> #254 (this ticket)
```

As of this refine pass, **#248 through #251 and #208 are all still open** — the
`scripts/factory_core/providers/` package (containing the `Tracker`/`CodeHost` ABCs, the
GitHub reference implementations, and the shared abstract contract-test harness described in
design-doc §10) does not exist yet in the codebase.

This spec proceeds on the basis that `Depends on: #251` / `Depends on: #208` (both declared in
the issue body) gates **implementation dispatch only** (per `CLAUDE.md`'s dependency
convention), and that the full chain #248→#249→#250→{#251,#208} will be **Done** by the time
this ticket is actually implemented — because #251 and #208 themselves depend on #248-#250
being done first. The implementation plan for this ticket should therefore assume:

- `scripts/factory_core/providers/codehost/base.py` defines the `CodeHost` ABC (§6.1).
- `scripts/factory_core/providers/codehost/github.py` defines `GitHubCodeHost`, the reference
  implementation, covered by golden-argv parity tests.
- A shared abstract contract-test suite exists (or is added by #248) that a new `CodeHost`
  implementation parametrizes into — see [Testing](#testing) below and the caveat there.

If the implementation plan phase discovers `CodeHost` still doesn't exist when this ticket is
picked up, that is a hard blocker to flag back to the scheduler (dependency not actually
satisfied), not something to work around by inlining a duplicate ABC.

---

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. A new adapter author (tracker, code host, or model endpoint) can identify, from the guide
   alone: every mandatory method, every required config value and secret, the test bar
   (contract suite + live smoke), and the validation/preflight gate for their axis.
2. The GitLab `CodeHost` stub demonstrably (via executing tests, not just prose) accepts opaque
   string change identifiers and does not assume GitHub CLI/numeric-PR semantics anywhere a
   real adapter would need to diverge.
3. The default GitHub `Tracker`/`CodeHost` path and its parity suite are untouched and stay
   green — this ticket only adds new files, it does not modify `github.py` reference adapters.
4. The guide documents, per axis: safe-failure / fail-open posture, idempotency requirements
   (marker-comment upsert, etc.), secret-handling rules (gitignored instance env only, never
   committed), and rollback (reverting `FACTORY_TRACKER`/`FACTORY_CODEHOST`/
   `FACTORY_MODEL_PROVIDER` to defaults).
5. A full, working GitLab implementation (real `python-gitlab`/REST calls, live-validated
   against a GitLab instance) is explicitly out of scope for this ticket and is filed as a
   separate follow-up only if requested later.
6. The explicit mixed-provider close flow (`host.merge_change()` succeeds →
   `tracker.resolve_item()`) is documented as a first-class cross-axis concern, not buried
   inside either the tracker or code-host section alone.

## Brainstorming Q&A

> **Q:** Should the adapter-authoring guide be ONE consolidated file
> (`docs/adapter-authoring-guide.md`) with sections per axis (tracker / code-host /
> model-endpoint), matching how `docs/provider-abstraction-design.md` itself is structured as a
> single multi-axis document — or THREE separate per-axis guide files?
>
> **A:** One consolidated file. Every durable reference doc under `docs/` in this repo is
> single-file-per-topic (`dark-factory-token-optimization.md`, `dark-factory-memory-contract.md`,
> etc.) — there's no precedent for a per-facet file split, and inventing one here would break
> that convention. It also mirrors its own authoritative source (`provider-abstraction-design.md`
> is one file covering all three axes) so the guide and spec cross-reference cleanly. Most
> importantly, the required cross-axis "mixed-provider close flow" material spans the tracker
> and code-host axes — a three-file split would force it to be duplicated or awkwardly
> cross-linked. A reader building only one axis is served by in-file section headings plus one
> README "Further reading" link, the same pattern every other guide in this repo already uses.

> **Q:** Should the GitLab `CodeHost` stub be wired into the shared contract test suite
> (parametrized alongside `GitHubCodeHost`, with `xfail`/`skip` on methods that raise
> `NotImplementedError`), or excluded entirely from test collection since live GitLab
> validation is out of scope?
>
> **A:** Wired in, with a real executing test — not excluded, and not satisfied by
> documentation alone. Design-doc §10 explicitly names GitLab as contract-suite-covered ("so
> Jira/GitLab are covered in CI with no live server"), and §2/§8 defer only the full *working
> implementation*, not the test wiring ("contract + sketch only; follow-up spec"). The
> acceptance criterion's verb — "proves" — can't be satisfied by prose: a mapping table can't
> catch a signature that coerces an ID to `int` or a method body that shells out to `gh`. Split
> the stub's methods into two tiers: pure-mapping methods with no HTTP dependency (opaque
> string IDs accepted/returned, `remote_url()`'s `oauth2:$TOKEN@` form, the `Draft:` prefix
> mapping, `close_keyword()` returning `None` when GitLab isn't also the tracker) run for real
> and must pass; methods that need live/fixtured GitLab HTTP (open/merge a real MR, pipelines,
> approvals) raise `NotImplementedError` and are marked `xfail`/`skip` with a reason pointing at
> the deferred follow-up ticket, so CI stays green and a future full implementation just flips
> them to passing.

---

## Architecture / Approach

### 1. `docs/adapter-authoring-guide.md` (new file)

Single file, linked from README's "Further reading" section (same pattern as
`dark-factory-token-optimization.md` / `dark-factory-memory-contract.md`). Structure:

```
# Adapter Authoring Guide
## Overview                     -- the three axes, where provider code lives (§4.1 recap)
## Tracker adapter
  - Required methods (table from design-doc §5.1)
  - Canonical status + label vocabulary (§5.2, frozen contract)
  - Required env vars, FACTORY_STATUS_* mapping
  - Contract tests + live smoke checklist (create -> label -> comment -> transition -> resolve)
## Code-host adapter
  - Required methods (table from design-doc §6.1, ~11 methods)
  - remote_url() auth-embedded URL requirement
  - Draft/ready/merge/checks/reviews mapping expectations
  - close_keyword() contract (empty string unless tracker == host)
  - Contract tests checklist
## Model-endpoint adapter
  - Native fast paths (anthropic/bedrock/vertex) vs. gateway path (databricks/openai)
  - Model-alias mapping (gateway config), cost/quality caveats
  - Preflight requirements per path
## Cross-axis concerns
  - Safe failure / fail-open posture (degradable ops, boot-time hard-fail preflight)
  - Idempotency (marker-comment upsert semantics)
  - Secret handling (gitignored instance env only, never committed, never in adapter code)
  - Rollback (reverting FACTORY_TRACKER / FACTORY_CODEHOST / FACTORY_MODEL_PROVIDER to defaults)
  - Mixed-provider close flow: host.merge_change() -> tracker.resolve_item(), and why
    close_keyword() must return "" when tracker != host
## Worked example: GitLab CodeHost seam proof
  - Links to scripts/factory_core/providers/codehost/gitlab.py and its contract-test file
  - Table mapping each CodeHost method to its GitLab equivalent (from design-doc §6.3)
```

Each method/requirement table is sourced directly from `docs/provider-abstraction-design.md`
(§5.1, §5.2, §6.1, §6.3, §7) rather than re-derived, so the guide and the design doc cannot
silently drift — the guide should explicitly cite section numbers it expands on.

### 2. `scripts/factory_core/providers/codehost/gitlab.py` (new file)

```python
class GitLabCodeHost(CodeHost):
    """GitLab MR seam proof — see docs/adapter-authoring-guide.md#worked-example."""

    def remote_url(self) -> str: ...          # oauth2:$TOKEN@gitlab.com/<slug> — real, passing
    def close_keyword(self, issue_id: str) -> str: ...  # "" unless GitLab is also the tracker — real, passing
    # ... other pure-mapping methods (draft-prefix helpers, id validation) — real, passing

    def find_change_for(self, branch: str) -> str: ...
        raise NotImplementedError("live GitLab MR list API — see follow-up ticket")
    def open_change(self, ...): ...
        raise NotImplementedError(...)
    # merge_change, get_change_checks, get_change_mergeable, get_change_reviews,
    # get_change_inline_comments, update_change_body, mark_ready: same pattern
```

Every method signature accepts/returns **opaque strings** for change IDs (e.g.
`"group/project!42"`, GitLab's MR-iid-scoped-to-project form) — never coerces to `int` — which
is the concrete, executable proof the acceptance criterion asks for.

### 3. Contract test wiring

Parametrize the existing (post-#248) shared `CodeHost` contract-test class with
`GitLabCodeHost` alongside `GitHubCodeHost`. Pure-mapping test cases run and must pass;
HTTP-backed test cases are marked non-strict `xfail` (or `skip`), with a `reason=` string that
explains a full GitLab implementation is deferred and points readers at this design doc, so
they don't block CI and don't silently start passing without review once a real follow-up
implementation lands. Exact fixture/parametrization mechanics depend on the shape #248
actually lands — the plan phase should read `scripts/factory_core/providers/codehost/base.py`
and its test harness once it exists, rather than assume a specific pytest pattern here.

### 4. README update

One line added to "Further reading":
```
- [`docs/adapter-authoring-guide.md`](docs/adapter-authoring-guide.md) — how to write a tracker, code-host, or model-endpoint adapter
```

---

## Alternatives Considered

1. **Three separate per-axis guide files** (`docs/tracker-adapter-guide.md`,
   `docs/codehost-adapter-guide.md`, `docs/model-endpoint-guide.md`). Rejected — breaks the
   repo's single-file-per-topic convention for durable docs, and forces the required
   cross-axis mixed-provider-close material to be duplicated or cross-linked across files.
2. **Documentation-only GitLab "stub"** (a markdown mapping table, no code). Rejected — the
   acceptance criterion requires the ABC to be *proven* host-agnostic, which prose cannot do;
   only an executing test against real code catches a hidden GitHub-shaped assumption.
3. **Skip the guide, only ship the GitLab stub.** Rejected — the issue's primary goal is
   "publish the adapter-authoring requirements"; the stub alone doesn't satisfy acceptance
   criterion 1 (a new adapter author can identify every mandatory method/config/secret/test/gate).

---

## Testing

- New guide is prose — validated by the Phase 5 self-review checklist (placeholder scan,
  consistency vs. design doc, scope check) at spec-writing and again at implementation time.
- `GitLabCodeHost` stub: pure-mapping methods get real unit tests (pass); HTTP-backed methods
  get `xfail`/`skip`-marked contract-test cases (do not block CI, do not silently pass).
- Existing GitHub parity/golden-argv suite must remain green and untouched — this ticket adds
  files, it does not modify `github.py` reference adapters or their tests.
- `python -m pytest tests/ -v` (per `CLAUDE.md` conventions) must pass, including the new
  GitLab contract-test cases (excluding the intentionally `xfail`'d ones).

**Caveat carried into planning** (flagged by the product-owner Q&A): this assumes #248 lands a
*reusable abstract* `CodeHost` contract-test class, not just golden-argv parity tests. If #248
turns out not to deliver that harness, standing it up becomes part of this ticket's scope,
which is larger than `size: M` implies — the plan phase should verify #248's actual shape
before committing to a task breakdown.

---

## Open Questions (non-blocking)

- Exact fixture/parametrization mechanics for the contract-test suite depend on #248's actual
  implementation, which doesn't exist yet — the plan/implement phases should read the real
  `base.py` and test harness rather than this spec's illustrative sketch.
- Whether `docs/adapter-authoring-guide.md` should also get a short "quickstart" pointer from
  `CLAUDE.md`'s repo map table is left to the implementer's judgment — not required by the
  issue's acceptance criteria.

## Assumptions (flagged)

- #248 through #251 and #208 will be Done (per the epic's dependency DAG) by the time this
  ticket reaches implementation, so the `CodeHost` ABC, `GitHubCodeHost` reference, and a
  shared contract-test harness will exist. If not yet true when picked up, this is a hard
  blocker — see [Dependency Basis](#dependency-basis).
- "GitLab sketch/stub" means real, importable Python that subclasses the eventual `CodeHost`
  ABC and is contract-test-parametrized (per the Q&A above), not a documentation-only artifact.
- The guide is a durable reference doc (`docs/`), not a temporal spec/plan artifact — it is
  not archived after this ticket ships, unlike this spec/plan pair.

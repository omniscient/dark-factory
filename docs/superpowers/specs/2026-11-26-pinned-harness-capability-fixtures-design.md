# Pinned-Harness Capability and Role-Isolation Certification Fixtures

**Issue:** #561

**Status:** Second refinement pass, amended by operator review on 2026-11-26. A first spec
(commit e342ce8, on a branch since deleted and recreated from `main`) was rejected by operator
review 2026-11-24 as refined too early: #557/#558/#559 did not exist on `main` yet, so the spec
could only name interfaces, not bind to them. They are now merged
(`providers/model_endpoint.py`, `model_proxy.py`, `providers/role_isolation.py`, and #560's
local-deployment runbook). This spec is written against the operator's 14-point "Refinement
constraints" section of the issue body (2026-11-25) and against
`docs/hybrid-execution-contract.md` §4.1/§4.5/§4.7 (accepted, confirmed by the owner in #580).
The review amendments and two owner decisions (2026-11-26) are folded in: the ticket is split
in two (next section), and forced `tool_choice` and `count_tokens` are observations only.

## Scope split (owner decision, 2026-11-26)

The work is delivered as two tickets. This spec covers **part A** (#561) only.

**Part A — this ticket:**

- The shipped package `scripts/factory_core/capability_suite/`: runner, in-process mock,
  result emitter and the fixture tree.
- Every case with `driver: http` (the runner itself is the HTTP client): the non-streaming
  round trip, SSE completion, the wire-level tool-use round trip, the context-window request,
  the forced-`tool_choice` and `count_tokens` observations, and every control.
- The `role_isolation` pure-function probes.
- The CLI `run --base-url <mock|url> --out <path>` with its endpoint-mode inputs and exit
  codes, the `certification_result` emitter and the `--out` refusal.
- The suite-version digest, setting `model_endpoint.PINNED_FIXTURE_SUITE_VERSION`, and the two
  existing-test changes that follow from it.
- Tier 1 tests in `pytest tests/`.

**Deferred to the follow-up harness ticket (part B):**

- Every `driver: harness` case, that is every case that spawns `claude -p`: the four tier
  aliases, the nested Agent `model:`, the credential observed at the mock, the tool-use round
  trip through the CLI, cancellation, and context padding.
- The scrubbed-environment rule for the spawned CLI.
- The harness-version pin and its check against the `Dockerfile` pin.
- The blocking Tier 2 CI step in `.github/workflows/ci.yml`. **Part A does not edit
  `ci.yml`.**

**Consequences, stated once and relied on below:**

- Part A keeps the `driver` field and the two-driver design, so part B only adds cases and
  removes dimensions from the pending list (see "Deferred harness probes").
- In part A every harness-driven probe is stored `not_tested`. Four of the six dimensions
  therefore cannot reach `pass` in part A.
- A result with any required dimension `not_tested` cannot qualify a route:
  `model_endpoint.role_problems()` requires every `ACTOR_REQUIRED_DIMENSIONS` key to be `pass`.
  #562's certification run therefore needs part B.
- Operator constraints 4, 7 and the Tier 2 half of 11 are met by part B, not by part A.
- Part B adds or changes files under the package, so it changes the suite-version digest and
  must update the pin in the same change.

## Overview / Problem Statement

`docs/hybrid-execution-contract.md` §4.1 names #561 (with #562) as the owner of
"capability/role-isolation fixtures — the pinned test suite #562 runs." §4.5 is explicit about
the boundary: a capability-evidence record is per-endpoint, long-lived, and authored only by a
human operator in #562, executing #561's pinned fixtures against #562's certified hardware; no
factory phase or scheduler-dispatched container may write that record. Invariant 9 (the
contract's Invariants index): all capability evidence traces back to a deterministic harness
run, never a model's self-report.

`scripts/factory_core/providers/model_endpoint.py` (#557, merged) already ships the schema,
derivation and vocabulary this ticket must consume, not reimplement:
`evidence_state()`/`role_problems()`, `RESULT_VALUES = (pass, fail, unsupported, not_tested)`,
`EVIDENCE_STATES = (tested, declared, stale, unknown)`,
`ACTOR_REQUIRED_DIMENSIONS` (the six capability dimensions §4.5 requires: protocol
compatibility, tool-call fidelity, streaming, context-length handling, role-isolation,
timeout/cancellation), `FINGERPRINTS` (the `invalidated_by` vocabulary), and
`PINNED_FIXTURE_SUITE_VERSION = None` — a module constant #557 left unset until #561 ships a
fixture suite. `scripts/factory_core/providers/role_isolation.py` (#559, merged) ships
`bind_dispatch_role()` and `check_verdict_independence()` as pure functions with no
disk/network/env access. `scripts/factory_core/providers/model_adapter.py` (#558/#559, merged)
ships the local path's request and response checks as plain functions (`check_json_body`,
`check_sse_complete`, `check_response_model`, `classify_failure`), which `model_proxy.py` calls
on every local request.

Part A's deliverable is: a fixture tree, the runner that executes its HTTP-driven cases against
a given base URL, and a result *emitter* that writes a `certification_result` artifact —
evidence of what the suite found, never the long-lived evidence record itself (that stays
#562's human-authored output, consuming this result as an input signal). Nothing here wires into
`scheduler.sh`, `entrypoint.sh`, any `gate_*`, the breaker, budgets, `deploy/**` or any CI
workflow (operator constraint 13); nothing here lifts `_NO_LOCAL_DISPATCH` (constraint 10,
#587's job).

## Requirements

Distilled from the issue's acceptance criteria, the operator's 14 constraints, the review
amendments and the two owner decisions.

1. A real-code fixture tree + runner + result emitter under a shipped path (the image copies
   `scripts/`, never `tests/`), not a `tests/`-only stub. (constraint 3)
2. Import #557's evidence vocabulary and derivation (`model_endpoint.py`); never re-derive
   `pass|fail|unsupported|not_tested`, `tested|declared|stale|unknown`, or
   `ACTOR_REQUIRED_DIMENSIONS` locally. (constraint 1)
3. Every fixture directory maps to exactly one of #557's six `ACTOR_REQUIRED_DIMENSIONS` keys;
   a key is `pass` only if every probe it requires passes, including the harness probes
   deferred to part B. No seventh dimension. (constraint 8)
4. `model_endpoint.PINNED_FIXTURE_SUITE_VERSION` is set to a SHA-256 digest over the fixture
   tree plus every top-level module of the package — never a hand-bumped string — with a pytest
   that fails when the committed pin diverges from the recomputed digest. (constraint 2)
5. A CLI entry point `run --base-url <url> --out <path>`, with each case declaring its driver
   (`http`, `harness` or `function`) and its target (`mock` or `endpoint`). (constraint 3)
6. The emitted artifact's top-level key is `certification_result`; it never carries
   `endpoint_id`/`certified_*`/`expires_at` (the evidence record's own fields) and the runner
   refuses an `--out` path under any `model-registry/` directory. (constraint 6)
7. Stored per-dimension values are only `pass|fail|unsupported|not_tested`; `unknown`/`stale`
   stay derived-only (`model_endpoint.evidence_state()`'s job, not this ticket's).
   (constraint 5; the missing-binary and version-mismatch rules of constraint 5 concern the
   spawned CLI and move to part B with it)
8. Bind to the merged interfaces only: controls classify with `model_adapter`'s functions and
   the `role_isolation` probes call `bind_dispatch_role()`/`check_verdict_independence()`.
   Never cite the removed `LOCAL_ROLE`. The role is pointer-grade; the suite never treats it as
   an authorization. (constraint 9)
9. Never remove, weaken or bypass `_NO_LOCAL_DISPATCH`. The runner loads no registry entry and
   resolves no local destination; it talks to the URL it is given. (constraint 10)
10. A "Tests and where they run" table. (constraint 11)
11. Known endpoint behaviours are classified, never assumed (constraint 12). Forced
    `tool_choice` and `count_tokens` are **observations only** (owner decision, 2026-11-26):
    recorded in `controls`, feeding no dimension. Each still has a control proving the suite
    classifies the behaviour as `unsupported`. A stream that ends in `message_stop` is the
    `streaming` probe's `pass`; a stream that does not is a control expecting `fail`. No rig
    address, hostname or model tag in committed files; use `192.0.2.0/24`.
12. No change to `scheduler.sh`, `entrypoint.sh`, `deploy/**`, `.github/workflows/**`,
    `config/` budgets, any `gate_*`, or the breaker. (constraint 13, widened to all workflows
    for part A)
13. Hermetic throughout: no live credentials, no network egress beyond loopback in any test, no
    GitHub side effects.
14. `.archon/memory/architecture.md` keeps every unexpired entry. (constraint 14)

### Design decisions (from brainstorming, amended by operator review)

- **Location and entry point.** One new package, `scripts/factory_core/capability_suite/`,
  holding the runner and the fixtures together, so the image and a dev checkout resolve
  fixtures through one relative path (`Path(__file__).parent / "fixtures"`). CLI:
  `python3 -m factory_core.capability_suite run --base-url <url> --out <path>`, the same
  `python3 -m factory_core.<module>` shape the image already uses with
  `PYTHONPATH=/opt/dark-factory/scripts`. No `Dockerfile` change: `pyyaml` and `aiohttp` are
  already installed in the image.
- **Fixtures are data.** `fixtures/<dimension>/<case>/case.yaml`, one case per directory, no
  Python under `fixtures/`. The six `<dimension>` directory names are exactly
  `ACTOR_REQUIRED_DIMENSIONS`'s six strings.
- **The mock is in-process.** `mock_server.py` is built on plain `aiohttp.web` (the
  `_make_upstream`/`TestServer` helpers live under `tests/`, which is not in the image) and
  listens on `127.0.0.1:<ephemeral-port>`. `--base-url mock` starts it and substitutes the real
  loopback URL; any `http(s)://` value is used as-is. One process, one exit code, one CLI
  surface, whether the target is the mock or a real endpoint.
- **A mock pass is never an endpoint qualification.** The result records `target_kind:
  mock|endpoint`.

## Architecture / Approach

### Package layout

```
scripts/factory_core/capability_suite/
  __init__.py
  __main__.py            # python3 -m factory_core.capability_suite -> runner.main()
  runner.py               # argparse: run --base-url <url|mock> --out <path> [...]
  mock_server.py          # in-process aiohttp.web Anthropic-Messages mock
  emit.py                 # builds the certification_result dict
  fixtures/
    protocol_compatibility/<case>/case.yaml
    tool_call_fidelity/<case>/case.yaml
    streaming/<case>/case.yaml
    context_length/<case>/case.yaml
    role_isolation/<case>/case.yaml       # pure-function probes over constructed dicts
    timeout_cancellation/<case>/case.yaml
```

A new `tests/test_capability_suite.py` covers the digest-vs-pin check, the
directory-set-equals-`ACTOR_REQUIRED_DIMENSIONS` check, and the execution of every part A case
against the in-process mock. `tests/` stays pytest-only; nothing under `tests/` ships in the
image.

Setting the pin changes two existing tests in `tests/test_provider_model_endpoint.py`:
`test_unpinned_fixture_suite_is_unknown` monkeypatches the pin to `None` (it tests the unpinned
case), and `test_fixture_evidence_matches_fixture_entry_but_stays_unknown` drops the
`fixture_suite_version` half of its assertion (the fixture record still resolves `unknown`
through `quantisation`). The comment above the constant is updated. No other existing test
changes, and no behaviour changes for a cloud-only instance: evidence is read only for a local
entry, and `_NO_LOCAL_DISPATCH` still blocks every local selection.

### Files touched (part A)

| File | Change |
|---|---|
| `scripts/factory_core/capability_suite/__init__.py`, `__main__.py`, `runner.py`, `mock_server.py`, `emit.py` | Create |
| `scripts/factory_core/capability_suite/fixtures/<dimension>/<case>/case.yaml` | Create |
| `scripts/factory_core/providers/model_endpoint.py` | Modify: the `PINNED_FIXTURE_SUITE_VERSION` value and the comment above it, nothing else |
| `tests/test_capability_suite.py` | Create |
| `tests/test_provider_model_endpoint.py` | Modify: the two tests named above |

Not touched: `scheduler.sh`, `entrypoint.sh`, `Dockerfile`, `.github/workflows/**`,
`deploy/**`, `config/`, any `gate_*`, the breaker, `model_proxy.py`, `model_adapter.py`,
`role_isolation.py`, and `_NO_LOCAL_DISPATCH`.

### Suite-version digest (constraint 2)

`PINNED_FIXTURE_SUITE_VERSION` is computed once, committed, and re-verified by a pytest:

```python
digest = hashlib.sha256()
digest.update(model_endpoint.tree_sha256(capability_suite_dir / "fixtures").encode())
for path in sorted(capability_suite_dir.glob("*.py")):  # every module: a new one is never unhashed
    digest.update(path.name.encode() + b"\0")
    digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
PINNED_FIXTURE_SUITE_VERSION = digest.hexdigest()
```

`fixtures/` contains no `.py` files, and the glob is not recursive, so `__pycache__/` is never
read and compiled bytecode cannot change the digest. The repository's `.gitattributes` forces
LF line endings, and both `tree_sha256` and the loop sort their inputs, so the digest does not
depend on the operating system or on directory order. The pin lives in `model_endpoint.py`,
which is not hashed, so setting it does not change the digest. A pytest recomputes the digest
and asserts equality with the committed value; a diff fails CI, which is the intended "no
hand-bumped string" guard. Because every top-level module is hashed, part B's added or changed
files change the digest, and part B must update the pin in the same change.

### CLI

`run --base-url <mock|http(s)://...> --out <path>`.

- `--base-url mock` starts the in-process `mock_server.py` server on an ephemeral loopback port
  and substitutes the real URL. Any `http(s)://` value (what #562 passes against certified
  hardware) is used as-is.
- `--out` is refused when its resolved path has a `model-registry` path component
  (constraint 6).
- Endpoint mode also requires `--served-name <model>` and reads the credential from the
  environment variable named by `--secret-env <NAME>`. The value never appears in the result,
  a log line or an error message. In mock mode both are optional: the mock's own served name
  and a dummy credential are used.
- `--context-tokens N` sizes the `context_length` endpoint probe. In mock mode it defaults to
  the window the mock declares. In endpoint mode it has no default: without it that probe does
  not run (exit code 4 below).

Exit codes, in priority order when more than one applies (argparse keeps 2 for usage errors):

| Code | Meaning | Result file |
|---|---|---|
| 0 | Every shipped case ran and every control matched; in mock mode, every executed probe is also `pass` | written |
| 2 | Usage error, including a refused `--out` | not written |
| 3 | A control's observed value differs from its `expect`, or the fixture tree is malformed (a suite bug) | written; not written for a malformed tree |
| 4 | A shipped case did not run (for example `--context-tokens` omitted in endpoint mode) | written; that case is `not_tested` |
| 5 | Mock mode only: an executed probe is not `pass` | written |

In endpoint mode a probe that is `fail` or `unsupported` is a valid finding, not an error: the
exit code stays 0 when every shipped case ran and every control matched. Codes for a missing or
mismatched `claude` binary are part B's to add.

### Drivers, kinds and targets

Every `case.yaml` declares three things.

- `driver: http | harness | function`. `function` is for the `role_isolation` pure-function
  probes, which send no request. With `http` the runner itself is the client and sends the scripted
  request. With `harness` the request is produced by a spawned `claude -p`. The pinned CLI
  cannot produce some requests the suite needs: it always streams, it never sets `tool_choice`,
  and it was not observed to call `count_tokens`. Those are therefore `http` cases. **Part A
  ships and executes `http` cases only.** The runner rejects a `harness` case it finds in the
  tree (exit code 4), so part B cannot add one without also adding the driver.
- `kind: probe | control | observation`.
  - A **probe** runs against its target and is the only kind that feeds a dimension's stored
    result.
  - A **control** always runs against the suite's own mock, even on a real-endpoint run, and
    carries an `expect:` value. It proves the suite classifies a scripted fault correctly. A
    mismatch is exit code 3 and never changes a dimension's value.
  - An **observation** runs against its target and records what it saw. It has no `expect`,
    feeds no dimension and never affects the exit code.
- `target: mock | endpoint`. A `mock` case always talks to the built-in mock. An `endpoint`
  case talks to whatever `--base-url` resolves to.

Controls and observations are both recorded in the result's `controls` list.

Controls classify with `model_adapter`'s merged functions (`check_json_body`,
`check_sse_complete`, `check_response_model`, `classify_failure`); the suite re-implements none
of them. The runner passes those functions a plain mapping holding the served name and a label.
It loads no registry entry and resolves no destination.

### Deferred harness probes

`runner.py` holds one constant, `HARNESS_PENDING`: the dimensions whose required harness probes
are not shipped yet. In part A it is:

```python
HARNESS_PENDING = ("protocol_compatibility", "tool_call_fidelity", "context_length",
                   "timeout_cancellation")
```

A dimension's stored result is the worst value among its probes, in the order
`fail`, `unsupported`, `not_tested`, `pass` (worst first). A dimension listed in
`HARNESS_PENDING` contributes one implicit `not_tested` probe. So in part A:

- a pending dimension is `not_tested` when all its shipped probes pass, and `fail` or
  `unsupported` when a shipped probe says so;
- `streaming` and `role_isolation` are not pending and can be `pass`;
- the implicit `not_tested` is not a case that "did not run": it never causes exit code 4 or 5.
  A mock run in part A exits 0 with four dimensions `not_tested`.

The result lists `harness_pending` so a reader can tell a deferred probe from a skipped one.
Part B removes a dimension from `HARNESS_PENDING` in the change that adds its harness cases. A
pytest asserts `HARNESS_PENDING` is a subset of `ACTOR_REQUIRED_DIMENSIONS`.

### Fixture-to-dimension map (constraints 8, 12)

| Dimension | Part A probes (`driver: http`, feed the result) | Controls (`expect:`) and observations | Deferred to part B (`driver: harness`) |
|---|---|---|---|
| `protocol_compatibility` | endpoint: a non-streaming round trip whose response `model` equals the served name | control: malformed JSON body → `fail`; control: response `model` mismatch → `fail`; control: mock answers `count_tokens` with 404 → `unsupported`; **observation** (endpoint): what `POST /v1/messages/count_tokens` returns | the four tier aliases and a nested Agent `model:` reach the mock as the one served name; the credential the mock observes is the dummy |
| `tool_call_fidelity` | endpoint: a wire-level tool-use round trip — the `tool_use.input` validates against the offered `input_schema`, and the following `tool_result` turn is accepted (this covers "structured outputs"; native `output_config` is out of scope because the proxy refuses it) | control: mock ignores a forced `tool_choice` → `unsupported`; control: unparseable `input_json_delta` → `fail`; control: a tool that was not offered is named in `tool_use` → `fail`; **observation** (endpoint): whether a forced `tool_choice` is honoured | the same round trip through the CLI's own tools; an injected `tool_result` payload is never promoted out of its block; an injected "switch model" instruction never changes a later request's `model` |
| `streaming` | endpoint: an SSE response that ends in a complete `message_stop` frame → `pass` | control: stream cut mid-content with no `message_stop` → `fail`; control: `message_start` model mismatch → `fail` | none |
| `context_length` | endpoint: a request sized to `--context-tokens`, checked through `usage.input_tokens` (never `count_tokens`) | control: `stop_reason: max_tokens` is classified as truncated, not complete → else `fail`; control: mock silently truncates input (`usage.input_tokens` far below what was sent) → `fail`; control: an over-limit 400 is a clean classified error | a large padded prompt reaches the mock byte-intact |
| `timeout_cancellation` | none in part A | control: connection refused ("local outage") → classified `endpoint_unreachable`, with no retry; control: stall before the first byte; control: idle stall mid-stream | SIGINT to `claude -p` and the mock sees the connection close |
| `role_isolation` | pure-function probes over constructed dicts: local + declared `checker`/`monitor` → refused; local + absent role → binds `actor`; a local producer record never satisfies independence; a record whose content self-asserts `role: checker` still fails independence; a record for another issue is `unit_of_work_mismatch` | none | none |

`role_isolation` certifies the factory's binding logic at the pinned suite version, not a
property of the endpoint. It is `pass` only when every listed case passes and is never
`not_tested`. Isolation asserted from observed requests (the issue's third acceptance
criterion) is the alias, nested-Agent and credential probes, which are part B.

The forced-`tool_choice` and `count_tokens` observations feed no dimension (owner decision,
2026-11-26): `model_proxy.py` refuses both before they reach an endpoint, so the factory never
depends on them, and a rig that lacks them stays eligible. The paired controls keep the "never
a silent pass" guarantee: the suite must still classify each behaviour as `unsupported` when
the mock exhibits it.

### Result emitter (constraint 6)

```text
certification_result:
  schema_version: "1.0"
  fixture_suite_version: <PINNED_FIXTURE_SUITE_VERSION>
  harness_version: <model_endpoint.observed_harness_version(), or null>
  target_kind: mock | endpoint
  base_url_scope: <model_endpoint.base_url_scope(url): same-host | lan | remote>
  results: {<each of the six ACTOR_REQUIRED_DIMENSIONS keys>: pass | fail | unsupported | not_tested}
  harness_pending: [<dimension>, ...]
  controls: [{dimension, case, kind: control | observation, expected, observed, ok}, ...]
  generated_at: <ISO8601>
```

The raw base URL is never written: only its scope is, so a result can be attached to an issue
without disclosing a rig address. `harness_version` is informational in part A (no
case uses the CLI) and may be null. For an observation, `expected` is null and `ok` is true.

No `endpoint_id`, `certified_by`, `certified_at`, `expires_at`, or `invalidated_by` field — the
top-level key is `certification_result`, never `capability_evidence`, so this artifact can never
be mistaken for (or loaded as) the per-endpoint evidence record `model_endpoint.load_evidence()`
reads. A pytest writes a result and asserts that parsing it as evidence is rejected.

### Tests and where they run

| Tier | What | Where | Gating |
|---|---|---|---|
| 1 (part A) | Every `http` case and every control and observation against the in-process mock, through `runner.main()`; the `role_isolation` probes; digest-vs-pin; dimension-directory check; `HARNESS_PENDING` subset check; exit codes 0, 2, 3, 4 and 5; `--out` refusal; the emitted result is rejected as evidence; no raw URL or credential value in the result | `pytest tests/` (`PYTHONPATH=scripts`), the existing `tests` CI job | Blocking (existing job) |
| 2 (part B, not in this ticket) | `claude -p` spawned against the mock for every `harness` case, in the built image with no network | the follow-up harness ticket's CI step | Not wired in part A |

The digest-vs-pin test needs `tree_sha256`, which opens files with `os.O_NOFOLLOW`; it is
skipped on a platform without that flag. Every other part A test is platform-neutral.

## Acceptance criteria (part A)

- [ ] `python3 -m factory_core.capability_suite run --base-url mock --out <path>` exits 0 and
      writes a `certification_result` whose `results` are exactly the six
      `ACTOR_REQUIRED_DIMENSIONS` keys, with `streaming` and `role_isolation` `pass` and the
      four `HARNESS_PENDING` dimensions `not_tested`.
- [ ] Every control in the fixture-to-dimension map exists, runs against the mock and matches
      its `expect`; changing a control's scripted fault so the classification differs exits 3.
- [ ] The forced-`tool_choice` and `count_tokens` observations appear in `controls` with
      `kind: observation` and change no dimension's value, whatever the target answers.
- [ ] `PINNED_FIXTURE_SUITE_VERSION` equals the recomputed digest; editing any fixture or any
      top-level module of the package without updating the pin fails a test.
- [ ] The stored values are only `pass|fail|unsupported|not_tested`; the result carries no
      evidence-record field, no raw base URL and no credential value; `--out` under a
      `model-registry` directory exits 2 and writes nothing.
- [ ] With the result of a part A run copied into an evidence record's `results`,
      `model_endpoint.role_problems()` reports the `not_tested` dimensions: part A alone
      qualifies no route.
- [ ] No test reads a secret, writes to GitHub, or opens a connection to anything but loopback.
- [ ] `git diff --name-only origin/main...HEAD` lists only the files in "Files touched
      (part A)" plus this spec and its plan.
- [ ] Real hardware and model performance stay with #562; the harness-driven probes and the
      CI step stay with the follow-up harness ticket.

## Alternatives Considered

1. **A separate mock-server container, networked to the runner container.** Rejected: two
   containers do not share loopback, so the mock would need a real network, which the
   hermeticity requirement exists to avoid. The in-process mock needs none.
2. **A second CLI surface (`serve`) that only starts the mock.** Rejected: a real-hardware run
   never needs it, and it would add a second process and a second exit code to reason about.
3. **Treat "structured outputs" and "Agent delegation" as new, seventh/eighth dimensions.**
   Rejected directly by operator constraint 8; both map onto existing dimensions
   (tool-call fidelity; protocol compatibility).
4. **Build a standalone `capability_evidence.py` schema/derivation module, as the first
   (rejected) spec did.** Rejected per operator constraint 1 — `model_endpoint.py` already
   ships it. #561 imports; it does not reimplement.
5. **Put `model_proxy.py` in the request path of the shipped runner.** Rejected: it would need
   a local destination built outside the loader, in shipped code, which is a way around
   `_NO_LOCAL_DISPATCH`. Reusing `model_adapter`'s check functions binds to the merged
   behaviour without that.
6. **Ship everything in one ticket.** Rejected by the owner (2026-11-26): the scripted
   multi-turn mock plus the spawned-CLI cases plus a blocking CI step is too much for one run.

## Decisions and open questions

- **Decided — forced `tool_choice` and `count_tokens` are observations only.** Owner decision,
  2026-11-26. They are recorded in `controls` and feed no dimension, so an endpoint that
  ignores a forced `tool_choice` or answers 404 to `count_tokens` is not made ineligible by
  them.
- **Decided — the ticket is split in two.** Owner decision, 2026-11-26. See "Scope split".
- **Open (non-blocking) — the certified window for the `context_length` endpoint probe.** The
  number #562 passes as `--context-tokens` for a specific certified endpoint is that ticket's
  to supply.
- **Open (non-blocking) — whether `mock_server.py` needs to track future protocol additions**
  beyond what the shipped cases exercise; left to whichever ticket next extends the capability
  dimensions.

## Assumptions (flagged)

- `model_endpoint.py`, `model_adapter.py`, `model_proxy.py`, `role_isolation.py`, the
  `Dockerfile`, `ci.yml` and `tests/test_provider_model_endpoint.py` were read on `main`
  on 2026-11-26. Re-verify them before implementing if `main` has moved; #585 and #586 are
  changing `scheduler.sh` and `entrypoint.sh` in parallel, which this ticket does not touch.
- Reviewer measurement, 2026-11-26, in the published factory image with no network: the pinned
  CLI reports `2.1.282`; a `claude -p` call against an in-process loopback mock succeeds and
  sends `HEAD /api/hello` followed by `POST /v1/messages?beta=true` with `stream: true` and no
  `tool_choice`. This is what the two-driver design rests on, and what part B builds on.
- With the pin set to a digest, exactly two existing tests fail until changed as described
  under "Package layout" (reviewer measurement on `main`, 2026-11-26). The described changes
  themselves were not executed.
- The two signed "Hermes Agent" sections embedded in the issue body (Product Manager, Research)
  are read as self-limiting framing, not as additional authority beyond the operator's 14
  constraints and the accepted contract.

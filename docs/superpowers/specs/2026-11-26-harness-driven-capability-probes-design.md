# Harness-Driven Capability Probes and Blocking Tier 2 CI Step

**Issue:** #624

## Overview / Problem Statement

Part A (#561, merged as `97ed8f2`) shipped `scripts/factory_core/capability_suite/`: a fixture
tree, an HTTP-driven runner, an in-process Anthropic-Messages mock, and a `certification_result`
emitter. It deliberately left four of the six `ACTOR_REQUIRED_DIMENSIONS` dimensions
(`protocol_compatibility`, `tool_call_fidelity`, `context_length`, `timeout_cancellation`) stuck
at `not_tested` via `runner.HARNESS_PENDING`, because their required probes can only be produced
by the real, pinned `claude -p` CLI — not synthesized over raw HTTP — and part A's runner
reports any `driver: harness` case it finds in the tree as not run (exit code 4).

This ticket (part B) removes that gap: it adds one new `driver: harness` fixture case to each of
the four pending dimensions, implements the `harness` driver in the runner (spawn `claude -p`
against the mock inside a scrubbed subprocess environment, assert on what the mock observed),
pins the harness (CLI) version against the `Dockerfile`'s own pin, and adds a blocking Tier 2 step
to `.github/workflows/ci.yml` so every future PR proves the built image's pinned CLI still
produces the expected wire traffic. `docs/hybrid-execution-contract.md` §4.5 is the contract this
discharges: a capability-evidence record (#562's job, still human-only) can only ever be `pass`
across the full `ACTOR_REQUIRED_DIMENSIONS` set once a suite exists that can actually certify all
six — this ticket is what makes that true for the first time.

Nothing here touches `_NO_LOCAL_DISPATCH`, `scheduler.sh`, `entrypoint.sh`, `deploy/**`,
`.github/workflows/publish.yml`, `config/` budgets, any `gate_*`, or the breaker. The only
workflow file touched is `.github/workflows/ci.yml`, and only to append one new blocking step
after the existing `docker-build` job's image build — no existing step in that file is modified.

## Requirements

Distilled from the issue's acceptance criteria and constraints, and the two brainstorming
questions below.

1. `driver: harness` is implemented in `runner.py`: it spawns the pinned `claude -p` against a
   target and asserts on what the mock observed. The runner no longer rejects a `harness` case
   with exit 4 (that rejection is replaced by actually running it).
2. One new fixture case directory is added under each of the four `HARNESS_PENDING` dimension
   folders; each dimension is removed from `HARNESS_PENDING` in the same change that adds its
   case. After this ticket, `HARNESS_PENDING == ()`.
3. All four tier-alias environment variables and a nested Agent-tool dispatch with an explicit
   `model:` resolve to the single mock-served name (contract §4.11 decision 2); the credential
   the mock observes on every one of those requests is the dummy credential.
4. The tool-use round trip runs through the CLI's own built-in tools (never the suite's
   `WEATHER_TOOL`/`TIME_TOOL`, which stay `http`-only fixtures); an injected `tool_result`-borne
   payload is never promoted out of the block the CLI put it in; an injected "switch model"
   instruction never changes a later request's `model` field.
5. A large padded prompt reaches the mock byte-intact; a prompt that exceeds the mock's declared
   window is classified as exceeded-and-surfaced, without the probe ever reading the CLI's own
   response `stop_reason` (see "Avoiding the `stop_reason: max_tokens` weakness" below — this is
   the issue's explicit "say how the probe separates the two" deliverable).
6. `SIGINT` delivered to the spawned `claude -p` process is observed by the mock as the
   connection closing — no server-side hang, no silent retry.
7. Every `claude -p` subprocess this suite spawns receives an explicit allow-listed environment
   (never `os.environ` passed through): `PATH`, a temporary `HOME`, `ANTHROPIC_BASE_URL`, the
   four `ANTHROPIC_DEFAULT_*_MODEL` tier aliases, a dummy `ANTHROPIC_API_KEY` (mock mode),
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_AUTOUPDATER=1`. The working directory
   is a fresh temporary directory, never the caller's; stdin is closed; the process is started
   with `asyncio.create_subprocess_exec`, because the mock runs on the same event loop. A test
   proves the environment's key set is exactly the allow-list, and that
   `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN` and the caller's real `HOME` are never present in that
   environment, even when they are set in the test process's own environment. The version read
   (`model_endpoint.observed_harness_version()`, `claude --version`) is the one exception: it is
   shared code outside this ticket and inherits the caller's environment.
8. A harness-version pin (`harness.PINNED_HARNESS_VERSION`) is compared against the installed
   `claude --version` whenever Tier 2 mode is active, and a pytest asserts the pin equals the
   version the `Dockerfile` installs.
9. Three distinct, newly-introduced failure shapes, none reusing exit codes `0`, `2`, `3`, `4`,
   `5` (already spoken for by part A):
   - the `claude` binary is absent when Tier 2 mode is active → every harness probe records
     `not_tested`, a `certification_result` is still written, and the process exits non-zero;
   - the installed CLI's version differs from the pin when Tier 2 mode is active → no result
     file is written at all, and the process exits a *different* non-zero code;
   - a harness case that otherwise fails to execute while Tier 2 mode is active falls through to
     the *existing* exit code 4 (`summary.not_run`) — no third new code is needed for this case
     (see "Tier 2 gating" below).
10. A blocking step in the `docker-build` job of `.github/workflows/ci.yml`, after the existing
    image build, running the suite against the mock inside the built image with
    `--network none`, `FACTORY_CAPABILITY_TIER2=1`, and no `continue-on-error`; its artifact
    upload runs `if: always()`. This step is new; no existing step in `ci.yml` changes.
11. `model_endpoint.PINNED_FIXTURE_SUITE_VERSION` is re-pinned in this same change (every file
    this ticket adds or edits under `capability_suite/` changes `emit.suite_digest()`).
12. No rig address, hostname or real model tag in any committed file; `192.0.2.0/24` only, where
    an example address is needed at all (this ticket needs none — everything new talks to the
    in-process mock).
13. Hermetic: the Tier 2 CI step needs no network beyond loopback (enforced by
    `--network none`) and no secret.

### Brainstorming Q&A

Recorded as answered. Where the Architecture section differs (operator review amendments,
2026-11-26), the Architecture section governs.

> **Q:** The issue's "Facts from part A" section says: "Part A's HTTP context probe sends
> `max_tokens: 64` and treats `stop_reason: max_tokens` as a failure, which a verbose model can
> trip for a reason unrelated to its window. Do not copy that weakness into the harness probe; say
> how the probe separates the two." Proposed design: the harness `context_length` probe never
> inspects the CLI's own response `stop_reason` at all; it proves window compliance entirely from
> what the mock observed arriving, and proves the over-the-window case via the mock's existing
> `over_limit` fault. Is this an acceptable resolution, or is there a different signal it should
> key off instead?
>
> **A:** Yes, with two adjustments. Proving window compliance from what the mock observed, and
> never reading the CLI's `stop_reason`, does separate the two causes — the spec must state that
> reasoning explicitly, since it is the "say how the probe separates the two" deliverable.
> (1) Don't rely on an exact word-count match: `claude -p` adds its own text to the user turn, so
> the mock's observed count will never equal the padding size exactly. Instead use a uniquely
> delimited padding block (start marker, N filler words, end marker) and assert the mock recorded
> a request containing that block intact, with the filler's own word count near-but-under
> `CONTEXT_TOKENS`. (2) For the over-limit case, use a prompt actually over `CONTEXT_TOKENS` and
> require both signals together: the mock recorded the oversized request and answered its
> scripted 400, AND the CLI process exited non-zero with error output naming the over-limit
> error — only both together count as "window exceeded and surfaced," keeping it distinct from
> both "never exceeded" and "harness crashed for an unrelated reason."
>
> **Q:** The `tool_call_fidelity` harness case covers "the tool-use round trip through the CLI's
> own tools" plus two injection sub-checks (a `tool_result` payload never promoted out of its
> block; a "switch model" instruction never changes a later request's `model`). Proposed design:
> the mock reads the CLI's own `tools` array and fabricates a `tool_use` for one of them via the
> existing `_tool_use()` helper; the prompt steers the CLI to a harmless, side-effect-free tool
> call; the mock's next scripted reply embeds the adversarial text in a block the CLI echoes back
> verbatim in its next request, and the checks assert that text stays nested (never promoted) and
> that `model` is unchanged. Does this match intended scope, or is there a narrower/different
> mechanism implied by the issue or by Claude Code's own CLI behavior?
>
> **A:** In scope, with two corrections. (1) The mock fabricates the `tool_use` block itself, so
> the prompt cannot pick the tool — safety depends on what the mock chooses to fabricate, not the
> prompt. Have the mock pick one specific read-only built-in tool by name from the observed
> `tools` array (e.g. `Read` on a file seeded in the scrubbed temp cwd), fail the case if that
> name is absent rather than substituting another entry, and assert the follow-up request's
> `tool_result` has a `tool_use_id` matching the fabricated id. If the pinned 2.1.282 CLI exposes
> an `--allowedTools`-style flag (it does — confirmed directly against the pinned CLI: `claude
> --help` lists `--allowedTools, --allowed-tools <tools...>`), pass it to additionally restrict
> the CLI to that one tool, but the design must not depend on that flag existing. (2) The mock
> cannot place text inside a `tool_result` block directly — that block is built by the CLI from
> the tool's real local output. Put the injected `tool_result` payload and the "switch model" text
> in the seeded file's *contents* instead, so the CLI's own `Read` call carries them into the
> follow-up request, and run checks (a)/(b) against that request.

## Architecture / Approach

### New module: `harness.py`

A new file, `scripts/factory_core/capability_suite/harness.py`, holds everything specific to
spawning and scrubbing `claude -p` — kept separate from `runner.py` the same way `mock_server.py`
and `emit.py` are already separate concerns. It is a top-level `.py` file under the package, so
`emit.suite_digest()` picks it up automatically (no `emit.py` change needed for that).

```python
PINNED_HARNESS_VERSION = "2.1.282"  # must equal the Dockerfile's claude-code pin (tested below)
TIER2_ENV = "FACTORY_CAPABILITY_TIER2"
ALLOWED_ENV_EXTRA = ()  # per-check additions layered onto the base allow-list

def scrubbed_env(*, base_url, served_name, home, extra=None) -> dict:
    """The allow-listed environment every spawned `claude` subprocess gets. Never a copy of
    os.environ: built from nothing but these names, so CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN and
    the caller's real HOME can never leak in by inheritance."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": mock_server.DUMMY_CREDENTIAL,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    for alias in model_endpoint.TIER_ALIASES:
        env[alias] = served_name
    assert not set(extra or {}) - set(ALLOWED_ENV_EXTRA)
    env.update(extra or {})
    return env

def preflight(tier2: bool) -> tuple[str, str | None]:
    """("skip", None) when Tier 2 is off; ("missing", None) or ("mismatch", observed) or
    ("ok", None) when it's on. Reuses model_endpoint.observed_harness_version() — never a
    second, separate `claude --version` invocation."""
```

`preflight()`'s three non-`"skip"` outcomes map directly to the three failure shapes in
Requirement 9: `"ok"` means every harness case actually runs; `"missing"` means every harness case
still contributes its dimension's `not_tested` probe value as usual, but the overall run exits a
new non-zero code; `"mismatch"` means `main()` returns a new non-zero code *before* any case
(harness or otherwise) runs and *before* `write_result` is called at all — matching the issue's
"version mismatch → no result file" shape exactly.

### Tier 2 gating (`FACTORY_CAPABILITY_TIER2`)

Per the issue's constraint "Tier 2 is opt-in by `FACTORY_CAPABILITY_TIER2` and POSIX-only," the
env var does not just label the blocking CI step — it is what the runner checks before ever
attempting to spawn `claude`:

- **Unset** (default: local dev, the existing Tier 1 `pytest tests/` job, which runs on bare
  `ubuntu-latest` with no `claude` binary installed): every harness case is skipped *by design*.
  Its dimension still receives a `not_tested` probe value — this preserves part A's exact
  observable behavior (`--base-url mock` with no other flags still exits 0 with those four
  dimensions `not_tested`) even though the mechanism moves from the now-empty `HARNESS_PENDING`
  list to a per-case skip. Skipped-while-unset is **not** added to `summary.not_run`, so it never
  trips exit code 4 — a bare `pytest tests/` run must keep passing with no `claude` binary present
  anywhere.
- **Set, binary present, version matches** (the new CI step, inside the built image): every
  harness case actually runs.
- **Set, binary absent**: every harness case still contributes `not_tested` (as above), but this
  time the run's exit code reflects the new "binary missing" failure shape (Requirement 9,
  bullet 1) — this is the one case where "not_tested" and "non-zero exit" coexist for a reason
  *other* than a probe genuinely failing.
- **Set, version mismatch**: `main()` returns the new "mismatch" exit code immediately, writing
  no result at all — the fatal, environment-is-wrong shape.
- **Set, binary present and version correct, but some other internal fault stops a harness case
  from running anyway** (a defensive case, not expected in practice): this is indistinguishable
  from part A's existing "a shipped case did not run" condition, so it falls straight through to
  the *existing* exit code 4 via `summary.not_run` — no third new exit code is introduced, which
  is why Requirement 9 lists only two new codes even though the issue describes three failure
  shapes.

### `runner.py` changes

- `HARNESS_PENDING` becomes `()`. The result's `harness_pending` lists the dimensions whose
  harness cases were skipped or could not run in this run (Tier 2 unset, or binary missing), so
  a Tier 2-unset mock run writes the same result part A did. The now-dead "implicit `not_tested` probe for a pending
  dimension" branch in `dimension_results()` is left as-is (it is simply never exercised once
  the tuple is empty — no behavior to delete, since the four dimensions' `not_tested` values are
  now produced by the harness cases' own skip/fail outcomes instead).
- `_validate()` drops the `if case["driver"] == "harness": return` early exit. In its place:
  `checks = {"function": FUNCTION_CHECKS, "harness": HARNESS_CHECKS}.get(case["driver"],
  HTTP_CHECKS)`, and a new rule — a harness case must be `kind: probe` and `target: mock` (every
  one of the four new cases is testing a CLI/harness-mechanism property, not a specific
  endpoint's behavior; see "Why harness cases always target the mock" below). Harness cases then
  fall through the same `kind`/`target`/`fault` rules every other case already passes through
  (minus the function-only and control-only branches, which don't apply to them).
- `HARNESS_CHECKS` is a new dict, parallel to `HTTP_CHECKS`/`FUNCTION_CHECKS`, mapping each new
  case's `check:` name to an async function in `harness.py`.
- `run_cases()` grows one new branch for `case["driver"] == "harness"`, dispatching on the
  Tier 2 mode resolved once at the top of `_run()` (not re-resolved per case): `"skip"`/`"missing"`
  append `not_tested` to `summary.probe_values[dimension]` only — never to `summary.probes`
  (exit code 5 in mock mode) and never to `summary.not_run`;
  `"ok"` calls the mapped `HARNESS_CHECKS` function, whose `None` return (an unexpected internal
  fault) flows into the existing `outcome is None` → `summary.not_run` path exactly like any other
  undispatched case today.
- `exit_code()` gains one new parameter, the Tier 2 preflight mode, and one new branch: a
  `"missing"` mode exits a new code immediately after the existing control-mismatch check (3) and
  before the existing not-run (4) and mock-probe-fail (5) checks — binary-missing is the more
  specific, more actionable diagnosis of what's behind those probes being `not_tested`.
- `main()` calls `harness.preflight()` once, right after computing `mock_mode`/`tier2`, and
  returns the new "mismatch" exit code immediately (before touching `load_cases`, the mock, or
  `write_result`) when that is the outcome.

### `mock_server.py` changes

- A new `HEAD /api/hello` route, answering `200` with an empty body — the first request the
  pinned CLI sends before anything else (confirmed by part A's own reviewer measurement, restated
  in this issue's "Reference measurement"). `POST /v1/messages?beta=true` needs no route change:
  aiohttp matches routes on path only, so the existing `POST /v1/messages` handler already
  answers that query-string form; this ticket adds a test asserting that explicitly rather than
  assuming it.
- A request-observation log: an `_Observed` object (a plain list of `{path, headers, body}`
  records) stored at `app["observed"]`, appended to by every handler before it returns. `start()`
  keeps its existing `(runner, base_url)` two-tuple return — no existing call site (`runner.py`
  and four spots in `tests/test_capability_suite.py`) needs to change — and a harness check reads
  the log back via `mock_runner.app["observed"].requests` once the spawned CLI process exits.
- A client-disconnect flag, set when a handler's `resp.write()` raises `ConnectionResetError` or
  is cancelled mid-write (as opposed to the existing `cut_stream` fault, which is the *server*
  truncating — this is the client hanging up on the server, the opposite direction, needed for
  the `timeout_cancellation` harness case).
- A per-case reply script, set in-process by the harness check before it spawns the CLI
  (`app["script"]`). It is required: the pinned CLI offers about 20 tools and its first is
  `Agent`, so part A's `_reply` would answer every CLI request with a fabricated `Agent` call.
  Scripts: `text` (always the plain `ok` reply), `read` (one `Read` call for the seeded file,
  then `ok`), `agent` (one `Agent` call with `subagent_type: general-purpose` and an explicit
  `model:`, then `ok`), `stall` (send `message_start`, then an SSE comment every 100 ms until
  the write raises). With no script set, behaviour is exactly part A's.

### Why harness cases always target the mock

All four new cases describe "reaching the mock" or being "seen by the mock" explicitly in the
issue body. This is intentional, not an oversight: each one tests a property of the *harness
mechanism itself* — does the CLI's own tier-alias plumbing funnel every alias to one served name;
does the CLI's own prompt assembly deliver a large block intact; does the CLI's own tool-use loop
resist a planted instruction; does the CLI actually tear down its connection on `SIGINT` — none of
which depends on which real endpoint eventually serves traffic. The *endpoint's* own behavior for
these same dimensions is already proven by part A's existing `target: endpoint` HTTP probes
(`protocol_compatibility`'s non-streaming round trip, `tool_call_fidelity`'s wire-level tool
round trip, `context_length`'s sized request) or, for `timeout_cancellation`, by part A's existing
mock-target *controls* (connection-refused, stall-before-first-byte, stall-mid-stream) that
classify `model_adapter`'s failure taxonomy. A dimension's stored result is still the worst of
*all* its probes (part A's existing `dimension_results()`), so a harness case and an endpoint-
target HTTP probe in the same dimension combine exactly the way part A already designed them to.

Because `_run()` always starts the in-process mock regardless of `--base-url` (part A's existing
behavior — the mock runs so that `kind: control` cases always have something to target even on
an endpoint run), a `target: mock` harness case behaves identically whether the suite is pointed
at `mock` or at a real endpoint. A future ticket that runs this suite against real certified
hardware (#562) still gets these four harness checks exercised against the mock, proving the
harness mechanism once per suite run, independent of which endpoint is under certification that
run.

### Avoiding the `stop_reason: max_tokens` weakness

Part A's `check_context_window` (the HTTP-driver probe, unchanged by this ticket, still used for
`target: endpoint` traffic) treats `stop_reason: max_tokens` as `fail`, which risks misclassifying
a verbose real model's own output-length behavior as a context-window failure. The new harness
`context_length` case does not share that risk, by construction: the mock's replies are a fixed,
short, scripted `"ok"` (see `mock_server._reply`), so the suite's own target never has a reason to
set `stop_reason: max_tokens` for output-length reasons, and the harness probe never reads
`stop_reason` at all. Window compliance is proven two ways instead, both from what the mock
*observed on the wire*, never from how the CLI's response came back:

- **Under the window:** the prompt embeds a uniquely delimited filler block (`<<<PROBE-START>>>`
  … `N` filler words … `<<<PROBE-END>>>`), sized to `mock_server.CONTEXT_TOKENS // 2`
  filler words, leaving room for the text the CLI adds; the request must be answered 200.
  The check passes only if the mock's observation log shows a request whose message content
  contains that exact delimited block, undamaged, with the filler's own word count within the
  expected range. (An exact match against the *CLI's total* word count is not attempted — the
  CLI's own system/tool scaffolding adds words of its own to the turn, which part A's `_word_count`
  would otherwise miscount — only the delimited block itself is checked.)
- **Over the window:** a second prompt, sized to definitely exceed `CONTEXT_TOKENS`, triggers the
  mock's existing `over_limit` fault path (reused as-is from part A — same scripted
  `400 invalid_request_error` the HTTP `over_limit_error` control already exercises). The case
  passes only when *both* signals agree: the mock's observation log shows it received the
  oversized request and answered 400, **and** the spawned CLI process itself exited non-zero with
  `Prompt is too long` in its stdout or stderr (the pinned CLI prints it on stdout and exits 1). Either signal alone is not sufficient — a CLI
  crash for an unrelated reason must not be misread as "window exceeded and correctly surfaced."

This case (one `case.yaml`, two sub-assertions — matching the issue's "one directory each" and
each dimension bullet's multi-part description) is `fail` if the under-window assertion fails,
and (separately checked within the same case) `fail` if the over-window assertion's two signals
disagree; `pass` only when both hold.

### Tier-alias and nested-Agent resolution (`protocol_compatibility`)

One case spawns `claude -p` five times against the mock, each with the full
`harness.scrubbed_env()` allow-list (all four tier aliases set to `mock_server.SERVED_NAME`):
once with `--model opus`, once `--model sonnet`, once `--model haiku`, once `--model fable`
(exercising each tier alias through the CLI's own `--model <alias>` resolution — the mechanism
contract §4.11 decision 2 describes, not reimplemented here), and once with the mock's `agent`
script: the mock fabricates an `Agent` tool call carrying an explicit `model:`, and the CLI's
own nested dispatch produces the subagent request (measured on the pinned CLI; no `--agents`
flag is needed). Every one of the five runs' requests, read back from
the mock's observation log, must show `model` equal to the single served name and the
`x-api-key` header equal to `mock_server.DUMMY_CREDENTIAL` — never a literal alias string, never
a different credential. The built-in tool the pinned CLI uses for a subagent
dispatch is named `Agent` (reviewer measurement, see Assumptions); if `Agent` is absent from the
request's `tools`, the case fails rather than substituting another tool.

### Tool-use round trip and injection resistance (`tool_call_fidelity`)

One case: the mock, on receiving the CLI's first request, picks the built-in `Read` tool by name
out of the request's own `tools` array (failing the case, not substituting a different tool, if
`Read` is absent — a pinned-CLI-shape assumption worth failing loudly on rather than silently
working around) and fabricates a `tool_use` block via the existing `_tool_use()` helper, targeting
a file seeded in the case's scrubbed temporary `HOME`/cwd. That file's contents carry two planted
strings: one shaped like a `tool_result` payload, one shaped like a "switch to model X" system
instruction — plain content, nothing executable, since this is read-only. The CLI executes its
own real `Read` call locally and sends the result back in its next request; the case asserts (a)
that request's `tool_result` block has a `tool_use_id` matching the id the mock fabricated, (b)
both planted strings appear only inside that one `tool_result` content block — never hoisted into
a top-level or system-level field of the request — and (c) that request's `model` field is still
the single served name, unchanged by the planted "switch model" text. `--allowedTools Read` (or
equivalent) is passed when available, as defense-in-depth, but the case's correctness does not
depend on that flag existing. Measured on the pinned CLI: `Read` runs in `-p` mode with no
permission flag, and the `tool_result` block is not in the request's last message, so the check
searches every message.

### `SIGINT` closes the connection (`timeout_cancellation`)

One case: `claude -p` is spawned with a prompt that reaches the mock; the mock's handler, on this
one case only, runs the mock's `stall` script (the CLI cannot send the header that triggers
part A's `stall_mid_stream` fault), which keeps the response open so the subprocess is still
mid-request. The harness
check waits until the observation log shows the `POST /v1/messages` (at most 30 s), sends
`SIGINT`, then waits at most 10 s and asserts the mock's new
client-disconnect flag (see "`mock_server.py` changes" above) was set — the server observed the
client actually tearing down the connection, not a hang the mock's own timeout had to resolve.
This is the first probe `timeout_cancellation` has ever had (part A shipped only controls for this
dimension); the dimension becomes capable of `pass` for the first time in this ticket. The
CLI's exit status is not asserted: the pinned CLI exits 0 after `SIGINT`.

### Updated fixture-to-dimension map (new rows only; part A's existing rows are unchanged)

| Dimension | New harness case (`driver: harness`, `kind: probe`, `target: mock`) |
|---|---|
| `protocol_compatibility` | `cli_alias_and_nested_agent_resolution` — all four tier aliases plus one nested-Agent dispatch reach the mock as the one served name; the credential observed is always the dummy |
| `tool_call_fidelity` | `cli_tool_round_trip_and_injection_resistance` — the CLI's own `Read` tool round-trips correctly; a planted `tool_result`-shaped payload stays nested in its block; a planted "switch model" instruction never changes a later request's `model` |
| `context_length` | `cli_padded_prompt_and_over_limit` — a delimited padded block reaches the mock intact under the window; an over-the-window prompt is classified failed only when both the mock's 400 and the CLI's own non-zero exit agree |
| `timeout_cancellation` | `cli_sigint_closes_connection` — `SIGINT` to the spawned CLI is observed by the mock as the connection closing |

### New exit codes

| Code | Meaning | Result file |
|---|---|---|
| 6 | Tier 2 active, `claude` binary absent (`harness.preflight()` → `"missing"`) | written; harness dimensions `not_tested` |
| 7 | Tier 2 active, installed CLI version differs from `harness.PINNED_HARNESS_VERSION` (`harness.preflight()` → `"mismatch"`) | **not written** |

Priority order (extends part A's existing table): `3` (control mismatch / malformed tree) >
`6` (binary missing) > `4` (a shipped case did not run) > `5` (mock mode, an executed probe isn't
`pass`) > `0`. Code `7` is a standalone early return in `main()`, before `exit_code()` is ever
reached, so it does not interact with this priority chain at all.

### Blocking Tier 2 CI step

Appended to the `docker-build` job in `.github/workflows/ci.yml`, after the existing
`docker build` step — no existing step in that job changes:

```yaml
  - name: Capability suite — Tier 2 (harness-driven, hermetic)
    timeout-minutes: 10
    run: |
      mkdir -p /tmp/capability-tier2 && chmod 777 /tmp/capability-tier2  # the image writes as uid 1000
      docker run --rm --network none \
        -e PYTHONPATH=/opt/dark-factory/scripts -e FACTORY_CAPABILITY_TIER2=1 \
        -v /tmp/capability-tier2:/out --entrypoint python3 dark-factory:pr \
        -m factory_core.capability_suite run --base-url mock --out /out/result.json \
        --context-tokens 8192
  - uses: actions/upload-artifact@v4
    if: always()
    with: { name: capability-tier2-result, path: /tmp/capability-tier2/result.json }
```

`--context-tokens 8192` is `mock_server.CONTEXT_TOKENS` spelled out explicitly rather than relied
on implicitly (mock mode already defaults to it when the flag is omitted) — explicit here because
this is the one invocation whose exact value this ticket's own padded-prompt sizing (just above)
is written to match, and a future change to the mock's default should not silently change what a
blocking CI gate exercises. No `continue-on-error`; the step fails the job on any non-zero exit,
and the artifact upload runs whether or not the step itself passed.

### Suite-version re-pin

Every file this ticket adds (`harness.py`, four `case.yaml` files) or edits (`runner.py`,
`mock_server.py`) under `capability_suite/` changes `emit.suite_digest()`. The committed
`model_endpoint.PINNED_FIXTURE_SUITE_VERSION` is recomputed and replaced in this same change — the
existing `test_pinned_fixture_suite_version_matches_the_recomputed_digest` pytest is the guard
that would otherwise fail.

### Files touched

| File | Change |
|---|---|
| `scripts/factory_core/capability_suite/harness.py` | Create — `scrubbed_env`, `preflight`, `PINNED_HARNESS_VERSION`, the four harness check functions |
| `scripts/factory_core/capability_suite/runner.py` | Modify — `HARNESS_PENDING = ()`, `HARNESS_CHECKS`, `_validate()`, `run_cases()`, `exit_code()`, `main()` |
| `scripts/factory_core/capability_suite/mock_server.py` | Modify — `HEAD /api/hello` route, request-observation log, client-disconnect flag, per-case reply scripts |
| `scripts/factory_core/capability_suite/fixtures/protocol_compatibility/cli_alias_and_nested_agent_resolution/case.yaml` | Create |
| `scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/cli_tool_round_trip_and_injection_resistance/case.yaml` | Create |
| `scripts/factory_core/capability_suite/fixtures/context_length/cli_padded_prompt_and_over_limit/case.yaml` | Create |
| `scripts/factory_core/capability_suite/fixtures/timeout_cancellation/cli_sigint_closes_connection/case.yaml` | Create |
| `scripts/factory_core/providers/model_endpoint.py` | Modify — `PINNED_FIXTURE_SUITE_VERSION` value only |
| `tests/test_capability_suite.py` | Modify — replace the three tests that assert today's "harness is rejected" behavior; add coverage for the new driver, the env scrub, the new exit codes, and the two new mock routes/behaviors |
| `.github/workflows/ci.yml` | Modify — append the Tier 2 step to `docker-build`; no existing step changes |
| `docs/configuration.md`, `docs/features.md` | Modify — only the capability-suite paragraph and row, so they describe the harness cases, Tier 2 gating and the CI step |

Not touched: `Dockerfile` (its existing CLI pin is only *read*, never changed — constraint
"no `Dockerfile` change unless separately reviewed"), `scheduler.sh`, `entrypoint.sh`,
`deploy/**`, `.github/workflows/publish.yml`, `config/`, any `gate_*`, the breaker,
`model_proxy.py`, `model_adapter.py`, `role_isolation.py`, and `_NO_LOCAL_DISPATCH`.

### Tests and where they run

| Tier | What | Where | Gating |
|---|---|---|---|
| 1 | The four new harness checks' pure logic (observation-log assertions, env-scrub allow-list, the two preflight non-`"ok"` modes) exercised with a stub `claude` (a tiny shell/Python script on `PATH` standing in for the real CLI, since Tier 1 runs on bare `ubuntu-latest` with no real CLI installed) or with `TIER2_ENV` unset entirely (the "skip" path); `HEAD /api/hello`; `POST /v1/messages?beta=true`; `HARNESS_PENDING == ()`; the Dockerfile-pin-matching pytest; digest-vs-pin | `pytest tests/` (`PYTHONPATH=scripts`), the existing `tests` CI job | Blocking (existing job) |
| 2 | The real pinned `claude -p`, inside the built image, spawned for all four harness cases against the mock, with `--network none` | the new Tier 2 step in `docker-build` | Blocking (new step, no `continue-on-error`) |

Tier 1 never needs the real `claude` binary present: the `"skip"` and stub-binary paths are
exactly what part A's existing `test_module_entry_point_runs` pattern (an empty `PATH`, asserting
a graceful missing-binary result) already established for `observed_harness_version()`; this
ticket's Tier 1 tests extend that same pattern rather than inventing a new one.

Before pushing, the implementer runs the Tier 2 command in the factory container, which has the
pinned CLI; the stub does not stand in for it.

## Alternatives Considered

1. **Run `claude -p` directly over the real network against a throwaway cloud model for the
   harness cases, instead of the in-process mock.** Rejected: it would need a live credential and
   network egress in CI, breaking the "no network, no secret" blocking-gate requirement and
   making the suite flaky on anything the cloud model itself does non-deterministically. The mock
   gives deterministic, scriptable request/response shapes, which is what distinguishing "the CLI
   mishandled this" from "the model answered oddly" requires.
2. **Make the harness cases `target: endpoint`-capable (so a future real-hardware cert run spawns
   the CLI against the actual endpoint, not the mock).** Rejected for this ticket: every one of
   the four properties under test (alias funneling, prompt-delivery fidelity, injection
   resistance, `SIGINT` handling) is a property of the harness/CLI mechanism, not of a specific
   endpoint — part A's existing `target: endpoint` HTTP probes already cover the endpoint's own
   protocol/tool/context behavior. Keeping harness cases mock-only avoids a second, redundant way
   to prove the same CLI-side mechanism once per endpoint instead of once per suite version.
3. **Add a third new exit code for "a harness case didn't run for some other reason while
   Tier 2 is active."** Rejected: that condition is indistinguishable from part A's existing "a
   shipped case did not run," which already exits 4 via `summary.not_run` — a third code would
   duplicate existing, working behavior rather than add information.
4. **Give `mock_server.start()` a three-tuple return `(runner, base_url, observed)` to expose the
   new observation log.** Rejected: it would force every one of the five existing call sites
   (`runner.py` plus four in `tests/test_capability_suite.py`) to change for no behavioral gain.
   `app["observed"]`, reachable from the already-returned `runner.app`, is strictly additive.
5. **Let the harness context-length probe read the CLI's `stop_reason` after all, just with a
   higher `max_tokens`.** Rejected directly by the issue's own framing — a higher ceiling narrows
   the window for a false positive but does not eliminate the structural problem (a sufficiently
   verbose real model could still trip it). Scoring purely from what the mock observed on the
   wire removes the ambiguity rather than shrinking it.

## Open questions (non-blocking)

- None open. The two questions the first draft carried are answered by the reviewer
  measurement under Assumptions: a mock-fabricated `Agent` call produces the nested dispatch,
  and `Read` runs in `-p` mode with no permission flag.
- Not verified, and left to the implementer's Tier 2 run: that the `tool_result` block's
  `tool_use_id` equals the fabricated id, and where the planted strings sit in the follow-up
  request.

## Assumptions (flagged)

- `runner.py`, `mock_server.py`, `emit.py`, `model_endpoint.py`, the `Dockerfile` and
  `tests/test_capability_suite.py` were read on this branch (`refine/issue-624-...`, based on
  `main` at `97ed8f2`) on 2026-11-26. Re-verify before implementing if `main` has moved.
  `docs/hybrid-execution-contract.md` and the archived part A spec
  (`docs/archive/2026-11-26-pinned-harness-capability-fixtures-design.md`) were read in full.
- The pinned CLI (`2.1.282`, confirmed present and installed in this environment) exposes
  `--model <alias>` and `--allowedTools`/`--allowed-tools` per its own `--help` output — a
  live, direct check against the pinned version. The design uses no other flag.
- Reviewer measurement, 2026-11-26, in the published factory image with no network and exactly
  the scrubbed environment (temporary HOME and working directory): the CLI starts and answers in
  about one second; it offers about 20 tools, the first being `Agent`, with `Read` present;
  `--model fable` reaches the mock as the served name with the dummy `x-api-key`; a 400 "prompt
  is too long" gives exit 1, one request and the message on stdout; `SIGINT` during a stalled
  stream is seen by the mock as a disconnect within half a second and the CLI exits 0; a
  mock-fabricated `Read` call is executed and followed up; a mock-fabricated `Agent` call with
  an explicit `model:` produces a nested request carrying the served name.
- `claude -p` always streams and never sets `tool_choice` — restated from part A's own reviewer
  measurement and this issue's "Reference measurement" section; this ticket's design does not
  re-verify it independently, only relies on it (consistent with part A, which established the
  two-driver split specifically because of this behavior).
- The two signed "Hermes Agent"-style sections, if any surface in this issue's future comments,
  would be read as self-limiting framing per `CLAUDE.md`'s trusted-comment-channel rules; none
  were present in this issue's comment history as refined.

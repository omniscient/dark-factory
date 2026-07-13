# Transparent Model Traffic Proxy and Request Ledger

**Issue:** omniscient/dark-factory#208
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#250 (provider selection and provider-aware boot preflight)
**Parent epic:** omniscient/dark-factory#202
**Related:** `docs/provider-abstraction-design.md` (#203, §7.4 — reference gateway + persona ledger,
approved for planning); omniscient/dark-factory#234 (Harness Economics epic, downstream consumer
of this ledger)

---

## Overview / Problem Statement

Dark Factory dispatches containerized Claude Code sessions (scheduler poll loop, Archon DAG
nodes, per-run product-owner/architect/reviewer subagents) but has no visibility into the actual
`/v1/messages` traffic those sessions produce — only DAG-stage-level summaries
(`scripts/factory_core/run_record.py` → `runs.jsonl` + Seq). We cannot currently answer "how
many tools were offered to this persona," "how large was the system prompt," or "did this call
retry," which blocks both day-to-day debugging and the planned Harness Economics work (#234)
that wants trusted raw per-call data to aggregate.

This ticket adds a factory-owned transparent reverse proxy that sits between Claude Code and
Anthropic's API, selected via `ANTHROPIC_BASE_URL`, so every real request can be measured and
(optionally) captured — without changing what any phase command does or sees.

**Non-goals (explicitly out of scope for this ticket — see [Alternatives Considered](#alternatives-considered)):**
- Routing to non-Anthropic backends (`FACTORY_MODEL_PROVIDER=databricks|openai`), alias
  resolution, or backend secret custody. That is spec #203 §7.4's full reference-gateway scope
  (its own implementation-sequence step, gated on #250's provider-selection work) and is filed
  as a follow-up ticket (see [Open Questions](#open-questions)).
- Outcome scoring or Factory CPM/cost-efficiency analysis (#234's job, not this ticket's — this
  ticket only produces the raw rows).
- Any change to `deploy/instances/**` (hard-excluded, human-only).

## Requirements

Distilled from the issue's 8 acceptance criteria, the Harness Economics PM comment, and Q&A below:

1. A `factory-model-proxy` runtime component, opt-in per instance, that Claude Code traffic can
   be routed through via `ANTHROPIC_BASE_URL`.
2. Generic passthrough of the full Anthropic API path space (not hardcoded to a single route),
   with streaming responses forwarded byte-for-byte unchanged.
3. Fail closed: a proxy-side error surfaces as a visible upstream error to Claude Code, never a
   silently dropped or truncated request.
4. Credential redaction (`authorization`, `x-api-key`, `api-key`, and factory-secret-like
   headers) on every persisted artifact — ledger rows and raw artifacts alike.
5. A compact, always-on, per-request ledger row correlating: run id, issue number, intent,
   stage/persona (best-effort — see [Persona/Stage Attribution](#personastage-attribution)),
   endpoint, model, status, duration, input/output tokens (cached/uncached where available),
   tool count, tool schema bytes, system-prompt bytes, total request bytes, largest offered
   tools, and retry/fallback metadata — a superset-compatible schema with spec #203 §7.4's
   ledger fields and #234's stated field list, so a future gateway reuses this sink rather than
   creating a parallel one.
6. Optional raw request/response artifacts, off by default, behind an explicit env flag, with a
   documented and enforced retention window. Never posted to GitHub.
7. The ledger feeds the existing run-record/Seq observability path; it is not a second source of
   truth for cost/run reporting.
8. Smoke coverage proving normal factory commands (refine/plan/implement/etc.) are unaffected
   when the proxy is enabled.
9. Rollout/rollback documentation, including the tool-search passthrough caveat (below).

## Architecture / Approach

### Component

`factory-model-proxy`: a small Python `asyncio` + `aiohttp` reverse-proxy process (matching the
codebase's existing pure-Python `factory_core` stack — no new language runtime). It:

- Listens on the `dark-factory-net` Docker network.
- Forwards every request path/method under it to `https://api.anthropic.com` unchanged —
  **generic passthrough, not a route allowlist restricted to `/v1/messages`** (see
  [Tool-Search / ANTHROPIC_BASE_URL Caveat](#tool-search--anthropic_base_url-caveat)).
- Streams SSE responses through as they arrive (no buffering the full response before
  forwarding) so token-by-token behavior is unaffected.
- On any upstream connection failure, DNS failure, or timeout, returns a `502`/`504` with a
  proxy-identifiable error body — Claude Code sees a clear upstream failure, never a hung
  connection or empty 200.
- Redacts `authorization`, `x-api-key`, `api-key`, and any header matching
  `X-Factory-*-Secret`/`X-Factory-*-Token` (factory-secret-like) before anything is written to
  disk or Seq. Redaction happens on the persistence path only — the real header values still
  flow upstream to Anthropic on the live request.

### Enablement

Opt-in via `FACTORY_MODEL_PROXY_ENABLED` (default unset/false — parity with today: no proxy,
direct `api.anthropic.com`, matching the provider-abstraction spec's parity invariant). When
enabled:

- `run-compose.yml` gains a `factory-model-proxy` service (same file, same `dark-factory-net`
  network, `profiles: [factory]`) and the `dark-factory` service gains `ANTHROPIC_BASE_URL:
  http://factory-model-proxy:8787` plus `depends_on: [factory-model-proxy]`, both conditioned on
  the flag being set in `.archon/.env`. This keeps the change inside `run-compose.yml` (editable,
  not hard-excluded) rather than `deploy/docker-compose.yml`, so the default path stays
  low-blast-radius; `deploy/docker-compose.yml`/`deploy/instance.env.example` get a documented,
  optional block for operators who want a persistent (not per-run) proxy instance, added as
  commented-out example config — no default behavior change for existing deployments.
- `entrypoint.sh`'s boot preflight is unaffected: `factory-model-proxy` is orthogonal to the
  `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` check (real credentials are still required; the
  proxy is transparent, not an auth substitute).

### Persona/Stage Attribution

Spec #203 §7.4 proposed `ANTHROPIC_CUSTOM_HEADERS: X-Factory-Persona: <node>` set per Archon DAG
node, flagging "confirm Archon can set that env per node" as an open risk. **Investigated for
this spec: Archon does not support it.** The workflow YAML's documented node fields
(`command`/`prompt`/`bash`/`script`/`loop`/`approval`, `when`, `trigger_rule`,
`allowed_tools`/`denied_tools`, `hooks`, `mcp`, `skills`, `agents`,
`effort`/`thinking`/`systemPrompt`/`fallbackModel`/`betas`/`sandbox`, `persist_session`) has no
per-node `env:` key — `archon-dark-factory.yaml` sets zero custom env anywhere across its ~30
nodes. Archon's only env-injection mechanism is `codebase_env_vars`, which is **project-scoped**
(one static value set for the whole run), not per-node. A single container run also traverses
multiple DAG nodes/phases under one `RUN_ID` (set once in `entrypoint.sh` before `archon workflow
run`), so `RUN_ID` alone cannot distinguish stage either.

**Approach: correlation, not headers.**
- Tag each ledger row with `run_id`, `issue_number`, and `intent` (all already available to the
  proxy via `.archon/.env`/container env passthrough — same values `entrypoint.sh` computes).
- Best-effort `stage` attribution: correlate request timestamp against Archon's own
  node-execution timeline (`archon workflow runs --verbose` / `archon.db`) at ledger-assembly
  time (not on the proxy's hot path — done by the existing `run-record assemble` step, which
  already runs post-hoc over one run's artifacts). This gives phase-level granularity
  (refine/plan/implement/conformance/code-review) but **not** sub-phase persona (e.g.
  distinguishing a product-owner subagent from its orchestrator inside `refine`), since those
  share one container process and one env — that granularity is infeasible without Archon
  gaining true per-node env injection.
- Requests the correlation step cannot place fall back to `stage=unknown` — logged, never
  dropped (graceful degradation, matching §7.4's original design intent).
- This is flagged as a known limitation, not silently glossed over — see
  [Open Questions](#open-questions).

### Ledger Storage

- **Compact ledger** — `/var/lib/dark-factory/request-ledger.jsonl`, same volume family as
  `runs.jsonl`, written through a sibling of `scripts/factory_core/run_record.py`'s existing
  `_append_jsonl()` (same `fcntl.flock(LOCK_EX)` pattern — required here since the volume is
  shared across concurrent containers) plus a non-fatal `_post_seq()`-style POST using the same
  `gen_ai.*` OTel property shape already established for `runs.jsonl`, so Seq stays the single
  query surface (satisfies requirement 7). Unlike `runs.jsonl` (~10-15 rows/run, safe to append
  forever), the request ledger is one row per actual `/v1/messages` call — potentially 100x+ the
  row rate — so it is **size-rotated**: rotate at 100 MB, keep 3 rotations, delete older, checked
  cheaply on write.
- **Raw artifacts** (opt-in via `RAW_ARTIFACT_CAPTURE`, default `off` — including for this
  dark-factory self-target, since redaction removes credentials but not payload content like
  system prompts, tool schemas, issue text, or code diffs) — written to
  `/var/lib/dark-factory/request-artifacts/<run_id>/<seq>.json` when enabled, one directory per
  run so cleanup is directory-scoped and never touches the ledger. Retention:
  `RAW_ARTIFACT_RETENTION_DAYS` (default `7`), enforced by an mtime-based sweep the proxy runs on
  startup and throttled to once per hour thereafter — no new cron/sweep infrastructure required
  (none exists in this repo today).

### Tool-Search / ANTHROPIC_BASE_URL Caveat

Claude Code's deferred-tool-loading ("tool search") mechanism issues Anthropic API calls beyond
a hardcoded single `/v1/messages` shape assumption (e.g. token-count and tool-resolution calls
tied to the same base URL). A proxy that intercepts only a literal `/v1/messages` route risks
silently breaking those calls the moment Claude Code's harness adds or uses a different path —
manifesting as tools failing to resolve mid-session with no clear proxy-side error. This spec's
generic-passthrough design (forward the full path space, not an allowlisted route) is the
mitigation; the rollout doc must call this out explicitly as the reason the proxy is not
route-restricted, and rollback guidance must note that unsetting `ANTHROPIC_BASE_URL` /
`FACTORY_MODEL_PROXY_ENABLED` restores direct, unproxied traffic immediately (no other state
depends on the proxy being present).

### Testing

- Unit tests (`tests/test_factory_model_proxy.py`): redaction logic, ledger-row field
  construction, size-rotation trigger, raw-artifact retention sweep — against a mocked upstream.
- A bash smoke test (pattern: `tests/test_431_telemetry_isolation.sh`) that runs a real factory
  command end-to-end with `FACTORY_MODEL_PROXY_ENABLED=true` and asserts (a) the command
  completes normally and (b) a ledger row was written — proving requirement 8.

## Alternatives Considered

1. **Off-the-shelf proxy (mitmproxy/LiteLLM) vs. a small custom `aiohttp` proxy.** An
   off-the-shelf tool gives faster initial setup but pulls in a new dependency surface and
   configuration DSL for a requirement that's fundamentally "forward bytes, redact headers,
   write a JSON row" — well within a ~200-300 line custom module consistent with the existing
   `factory_core` style. Chosen: custom `aiohttp` proxy. Revisit if/when the follow-up gateway
   ticket needs real backend translation (LiteLLM's translation layer becomes worth its weight
   at that point — spec #203 §7.2 already anticipates a "LiteLLM-style gateway" for that case).
2. **Persistent proxy service in `deploy/docker-compose.yml` vs. per-run service in
   `run-compose.yml`.** A persistent service is architecturally cleaner (one process, no
   per-run cold start) but `deploy/docker-compose.yml` is `critical_diff_paths`-flagged
   (high blast radius, extra review scrutiny) and changes there affect every operator's running
   stack. Chosen: per-run service in `run-compose.yml`, gated by the same opt-in flag, for a
   smaller, lower-risk diff appropriate to an M-sized ticket; the rollout doc offers the
   persistent-service pattern as a documented, human-opt-in alternative for operators who want
   it.
3. **Full reference gateway (this ticket) vs. passthrough-only (this ticket) plus a follow-up.**
   See [Non-goals](#overview--problem-statement) and Q&A — passthrough-only chosen; the AC list
   and `size: M` label are treated as authoritative scope over the issue's appended
   "Provider-abstraction placement" note.

## Open Questions

- **Follow-up ticket for the full reference gateway** (Databricks/OpenAI alias resolution +
  backend secret custody, spec #203 §7.4/§11 step 5, gated on #250) should be filed against
  parent epic #202 referencing this spec's ledger schema as the sink to extend. Not filed as
  part of this refinement pass (refine-phase scope is the spec document only); flagging here so
  the plan/implement phases (or a human) file it explicitly.
- **Sub-phase persona granularity** (distinguishing a subagent from its orchestrator within one
  DAG node) is infeasible until Archon supports genuine per-node env injection. Revisit if Archon
  adds that capability; until then, `stage` granularity is phase-level only.
- **Seq schema evolution**: this ticket adds new `gen_ai.*`-shaped Seq events alongside the
  existing `runs.jsonl`-derived ones. If Seq query/dashboard definitions exist elsewhere that
  assume one event type per run, they may need a follow-up update — out of scope to audit here.

## Assumptions

- `dark_factory_state` / `scheduler_state` (the shared Docker volume `runs.jsonl` already lives
  on) has enough headroom for a 100 MB×3-rotation ledger and a 7-day raw-artifact window; no
  documented disk-size guarantee exists for this volume today, so this is a starting default,
  not a measured budget.
- The compact ledger is always-on (no opt-out) once the proxy is enabled, since its rows are
  redacted/metrics-only and carry no request payload — consistent with `runs.jsonl`'s existing
  always-on posture.
- "Factory-secret-like headers" redaction can reasonably be implemented as a name-pattern match
  (`x-factory-*-token`, `x-factory-*-secret`, case-insensitive) rather than an exhaustive
  enumerated list, so new factory-internal secret headers are redacted by default without a code
  change.

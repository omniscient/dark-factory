# Dark Factory Model Proxy — Rollout, Rollback, and Caveats

Issue: #208. Spec: `docs/superpowers/specs/2026-07-11-transparent-model-proxy-design.md`.

## What this is

`factory-model-proxy` is an opt-in reverse proxy between Claude Code (inside a dispatched
run container) and `api.anthropic.com`. When enabled, it forwards every request unchanged
and writes a compact per-request ledger row to
`/var/lib/dark-factory/request-ledger.jsonl` (and, optionally, raw request/response
artifacts). Disabled by default — zero behavior change from today.

## Enabling it

Add to your instance's gitignored `.archon/.env` (the file `run-compose.yml`'s
`dark-factory` service already loads via `env_file:`):

```
FACTORY_MODEL_PROXY_ENABLED=true
ANTHROPIC_BASE_URL=http://factory-model-proxy:8787
```

Optionally, also set:

```
RAW_ARTIFACT_CAPTURE=true
RAW_ARTIFACT_RETENTION_DAYS=7
```

`scheduler.sh`'s `dispatch()` reads `FACTORY_MODEL_PROXY_ENABLED` and adds
`--profile factory-model-proxy` to its `docker compose run` invocation, which brings the
`factory-model-proxy` service (defined in `run-compose.yml`) online as an optional
dependency of the `dark-factory` run container. No other file needs to change.

## Why generic passthrough, not a `/v1/messages`-only allowlist

Claude Code's tool-search (deferred tool loading) mechanism issues Anthropic API calls
beyond a single hardcoded `/v1/messages` shape (e.g. token-count/tool-resolution calls on
the same base URL). A proxy restricted to one literal route risks silently breaking those
calls the moment Claude Code's harness adds or uses a different path — manifesting as tools
failing to resolve mid-session with no clear proxy-side error. `factory-model-proxy`
forwards the **full path space** under `ANTHROPIC_BASE_URL`, not an allowlisted route, to
avoid this failure mode.

## Rolling back

Unset `FACTORY_MODEL_PROXY_ENABLED` and `ANTHROPIC_BASE_URL` in `.archon/.env`. The next
dispatched run talks to `api.anthropic.com` directly — no other state depends on the proxy
being present; nothing else needs to be reverted or cleaned up.

## Known limitations

- **Correlation is best-effort and single-run-accurate.** `entrypoint.sh` writes
  `/var/lib/dark-factory/current-run.json` (`run_id`/`issue_number`/`intent`/`stage`) once
  per dispatch; the proxy re-reads it per-request. Under the default `FACTORY_WIP_LIMIT=1`
  this is exact. Under concurrent dispatches (`FACTORY_WIP_LIMIT>1`), a request may be
  attributed to whichever run most recently wrote the pointer file — Archon has no
  per-node `env:` key to carry a true per-request header, so this file-based approach is
  the least-infrastructure option that still satisfies the ledger's correlation
  requirement for the documented default deployment.
- **`stage` is exact for single-phase intents, `"unknown"` for multi-phase intents;
  `persona` is always `"unknown"`.** `refine`/`plan`/`deconflict`/`close`/`fix-main`/
  `recheck` runs are single-phase (the whole container run *is* that phase), so every
  request in those runs gets the correct `stage` for free. `fix`/`continue` runs traverse
  implement → conformance → code-review → merge inside one container run; placing a
  request within that traversal needs per-node start timestamps that neither
  `archon workflow get --json` nor `archon workflow runs --json` expose today (confirmed
  by live investigation against this repo's own `archon-dark-factory` workflow during
  planning) — `stage` stays `"unknown"` for those two intents. Sub-phase `persona`
  (distinguishing a subagent from its orchestrator within one node) is a strictly harder
  version of the same gap and is always `"unknown"`. Follow-up: file against parent epic
  #202 once Archon exposes per-node timing, or once the full reference gateway (spec #203
  §7.4, gated on #250) lands with real per-persona headers.
- **Streamed-response token counts are best-effort.** Non-streaming responses are fully
  parsed for `usage`; SSE responses are scanned for `"input_tokens"`/`"output_tokens"`
  substrings as they pass through (never buffered/blocked), which is robust to the current
  Anthropic streaming format but not schema-guaranteed.
- **The AC8 smoke test does not make a live Anthropic call.** It runs the real
  `factory_core.model_proxy` module (actual `create_app`/`handle_proxy` code) end-to-end over
  real HTTP, with only the upstream `api.anthropic.com` endpoint stubbed — a literal "real
  factory command with a live model call" smoke test would need committed live credentials in
  CI, which this repo does not do for any other test. This is a deliberate, documented
  substitution, not an unnoticed gap.
- **`retry_count` is always `0`.** The proxy is a single-pass forwarder and never retries
  itself; Claude-Code-level retries/fallbacks surface as separate, correlated ledger rows
  (same `run_id`/`issue_number`, adjacent timestamps) rather than a per-row counter. Aggregate
  by `(run_id, model, close timestamp window)` to reconstruct retry behavior.

## Retention

- Ledger: size-rotated at 100 MB, 3 rotations kept, oldest deleted — bounded regardless of
  request volume.
- Raw artifacts (only written when `RAW_ARTIFACT_CAPTURE=true`): swept by mtime on proxy
  startup and hourly thereafter; default retention 7 days
  (`RAW_ARTIFACT_RETENTION_DAYS`).

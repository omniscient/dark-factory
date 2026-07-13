# Implementation Plan — Transparent Model Traffic Proxy and Request Ledger

**Issue:** omniscient/dark-factory#208
**Spec:** `docs/superpowers/specs/2026-07-11-transparent-model-proxy-design.md`
**Depends on:** #250 (provider selection/preflight) — NOT required for this ticket's scope
(Anthropic-passthrough only); the dependency gates the *follow-up* multi-backend gateway ticket.

## Goal

Add an opt-in `factory-model-proxy` reverse-proxy component that sits between Claude Code
(inside each dispatched run container) and `api.anthropic.com`, selected via
`ANTHROPIC_BASE_URL`, so every real `/v1/messages`-family request can be measured (compact
JSONL ledger, always-on when the proxy is enabled) and optionally captured (raw artifacts,
opt-in, retention-bounded) — without changing what any phase command does or sees when the
proxy is disabled (default = today's direct-to-Anthropic behavior, byte-for-byte).

## Architecture

- **`scripts/factory_core/model_proxy.py`** — a ~250-line `asyncio` + `aiohttp` reverse proxy,
  matching the existing pure-Python `factory_core` module style (same `fcntl.flock` JSONL-append
  pattern as `run_record.py`). Generic passthrough of every path/method to
  `MODEL_PROXY_UPSTREAM` (default `https://api.anthropic.com`), streaming responses forwarded
  as they arrive. Redacts `authorization`/`x-api-key`/`api-key`/`x-factory-*-token`/
  `x-factory-*-secret` headers before anything is persisted (ledger row or raw artifact) — the
  real header values still go upstream on the live request. On upstream connect/DNS/timeout
  failure, returns `502`/`504` with a proxy-identifiable JSON error body (fail closed, never a
  silently dropped or hung request).
- **Correlation, not headers** (per spec — Archon has no per-node `env:` key, so
  `X-Factory-Persona` headers are infeasible): `entrypoint.sh` writes a small pointer file,
  `/var/lib/dark-factory/current-run.json` (`{run_id, issue_number, intent, stage, started_at}`),
  to the *same* `scheduler_state` volume both the `dark-factory` and `factory-model-proxy`
  containers mount, immediately after generating `RUN_ID`. The proxy re-reads this file
  per-request (cheap, best-effort) to tag ledger rows with `run_id`/`issue_number`/`intent`/
  `stage`. This is correct for the default `FACTORY_WIP_LIMIT=1` deployment; under concurrent
  runs, attribution may point at whichever run last wrote the file — documented as a known
  limitation (matches the spec's own admitted correlation-risk posture).
- **Stage attribution — investigated, partially delivered.** The spec's proposed mechanism
  (`archon workflow runs --verbose` / `archon.db` timeline correlation at ledger-assembly time)
  was investigated live against this very run during planning:
  `archon workflow get bec1a43d686cf78f2482eee84c3f4ba7 --json` returns only run-level
  `started_at`/`completed_at`/`last_activity_at` — no `nodes[]`/per-step timestamps — and
  `archon workflow runs --json`'s `current_step_name`/`total_steps`/`current_step_status`
  fields are `null` for this workflow's node style (a `command` node that runs an entire Claude
  Code phase internally, not Archon-tracked sub-steps). The documented Archon CLI/DB surface
  does not expose the per-node timing the spec's mechanism needs, so building it is not
  possible without upstream Archon changes (tracked as a follow-up against parent epic #202).
  Given that, this ticket delivers the part of requirement 5's "stage (best-effort)" that
  **is** derivable today, at zero extra cost: `entrypoint.sh` already knows `INTENT`
  (`refine`/`plan`/`deconflict`/`close`/`fix-main`/`recheck`/`fix`/`continue`) before it writes
  the pointer file. For the *single-phase* intents (`refine`, `plan`, `deconflict`, `close`,
  `fix-main`, `recheck` — where the entire container run **is** that one phase, no DAG traversal
  across phases), `stage` is set to the intent name directly and is exactly correct for every
  request in that run — not a guess. For the *multi-phase* intents (`fix`, `continue` — which
  traverse implement → conformance → code-review → merge inside one container run), `stage`
  stays `"unknown"`, because no investigated data source can place a request within that
  traversal. This is the actual, investigated infeasibility boundary — not an assumption, and
  not a blanket "always unknown."
- **Ledger** — `/var/lib/dark-factory/request-ledger.jsonl`, append-only, `fcntl.flock`-guarded,
  size-rotated at 100 MB keeping 3 rotations (checked cheaply on write, same volume family as
  `runs.jsonl`). Each row is fed to Seq non-fatally via the same `gen_ai.*` OTel property shape
  `run_record.py` already uses, so Seq stays the single query surface.
- **Retry/fallback metadata** — the proxy itself never retries or falls back (it is a single-pass
  transparent forwarder, per requirement 2/3: one inbound request maps to exactly one outbound
  request). `retry_count` is therefore always `0` on every row, by design, not a gap: any retry
  or `fallbackModel` behavior happens *above* the proxy, inside Claude Code/Archon, and arrives
  at the proxy as a **separate HTTP request** — which becomes its own ledger row, correlated to
  the same `run_id`/`issue_number` and adjacent in `timestamp`. A consumer reconstructs "did this
  call retry" by grouping ledger rows on `(run_id, model, close-timestamp-window)`, not by reading
  a single row's field. This is documented explicitly (not left implicit) in
  `docs/dark-factory-model-proxy-rollout.md`.
- **Raw artifacts** — opt-in via `RAW_ARTIFACT_CAPTURE=true` (default off), written to
  `/var/lib/dark-factory/request-artifacts/<run_id>/<seq>.json`, retained
  `RAW_ARTIFACT_RETENTION_DAYS` (default 7) via an mtime sweep run on proxy startup and
  throttled to once/hour thereafter.
- **Enablement** — `run-compose.yml` gains a `factory-model-proxy` service tagged
  `profiles: [factory-model-proxy]` (a *distinct* profile from `factory`, so it never starts by
  accident). `dark-factory` gains an *optional* dependency
  (`depends_on: { factory-model-proxy: { condition: service_started, required: false } }`) so
  Compose only pulls the sidecar in when its profile is active — zero YAML-level behavior change
  when the flag is off. `scheduler.sh`'s `dispatch()` appends `--profile factory-model-proxy` to
  its `docker compose run` invocation only when `FACTORY_MODEL_PROXY_ENABLED=true` is present in
  its environment (sourced from the instance's `.archon/.env`, same channel `FACTORY_WIP_LIMIT`
  already flows through). `ANTHROPIC_BASE_URL` itself needs **no compose file change at all** —
  it flows straight through `env_file: .archon/.env` (already wired for `dark-factory`), so an
  operator enables the whole feature with two lines in their gitignored `.archon/.env`:
  `FACTORY_MODEL_PROXY_ENABLED=true` and `ANTHROPIC_BASE_URL=http://factory-model-proxy:8787`.

## Tech Stack

Python 3.14 stdlib + `aiohttp` (new dependency — added to `Dockerfile`'s pip install and CI's
test dependency install; nothing else in this repo currently pulls in an async HTTP stack).
Bash for compose/scheduler wiring. `pytest` for unit tests, matching `tests/test_run_record.py`.

## File Structure

| File | Change |
|---|---|
| `Dockerfile` | add `aiohttp` to the pip install line |
| `.github/workflows/ci.yml` | add `aiohttp` to the `tests` job's pip install; wire the new smoke test |
| `scripts/factory_core/model_proxy.py` | **new** — redaction, ledger, raw artifacts, proxy handler |
| `tests/test_factory_model_proxy.py` | **new** — unit tests (redaction, ledger, rotation, retention, proxy handler) |
| `entrypoint.sh` | write `current-run.json` pointer file after `RUN_ID` is generated |
| `run-compose.yml` | add `factory-model-proxy` service + optional `depends_on` |
| `tests/test_model_proxy_compose.sh` | **new** — compose config assertions (flag off/on) |
| `scheduler.sh` | `dispatch()` conditionally adds `--profile factory-model-proxy` |
| `tests/test_scheduler.sh` | add a case for the conditional profile flag |
| `deploy/instance.env.example` | document `FACTORY_MODEL_PROXY_ENABLED`/`RAW_ARTIFACT_*` (commented, optional) |
| `deploy/docker-compose.yml` | commented-out example persistent-proxy block (documented, inert) |
| `tests/test_model_proxy_smoke.sh` | **new** — end-to-end proxy smoke test (AC8) |
| `docs/dark-factory-model-proxy-rollout.md` | **new** — rollout/rollback doc (AC9) |

---

## Task 1 — Add the `aiohttp` dependency

**Files:** `Dockerfile`, `.github/workflows/ci.yml`

1. In `Dockerfile`, extend the existing pip install line:

   ```dockerfile
   RUN pip install --quiet "git+https://github.com/scheidydude/codeindex.git" pre-commit pyyaml aiohttp
   ```

2. In `.github/workflows/ci.yml`, extend the `tests` job's dependency step:

   ```yaml
         - run: pip install pytest pyyaml aiohttp
   ```

3. Verify locally:

   ```bash
   pip install --quiet aiohttp
   python3 -c "import aiohttp; print(aiohttp.__version__)"
   ```

   Expected: prints a version string (e.g. `3.10.x`), no `ModuleNotFoundError`.

4. Commit:

   ```bash
   git add Dockerfile .github/workflows/ci.yml
   git commit -m "build: add aiohttp dependency for factory-model-proxy"
   ```

---

## Task 2 — Header redaction + ledger row construction (pure functions, TDD)

**Files:** `scripts/factory_core/model_proxy.py` (new), `tests/test_factory_model_proxy.py` (new)

1. Write the failing test first — `tests/test_factory_model_proxy.py`:

   ```python
   import json
   import sys
   from pathlib import Path

   import pytest

   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
   from factory_core import model_proxy as mp


   def test_redact_headers_strips_known_secrets():
       headers = {
           "Authorization": "Bearer sk-ant-xyz",
           "X-Api-Key": "abc123",
           "api-key": "def456",
           "Content-Type": "application/json",
       }
       redacted = mp.redact_headers(headers)
       assert redacted["Authorization"] == "[REDACTED]"
       assert redacted["X-Api-Key"] == "[REDACTED]"
       assert redacted["api-key"] == "[REDACTED]"
       assert redacted["Content-Type"] == "application/json"


   def test_redact_headers_strips_factory_secret_pattern():
       headers = {
           "X-Factory-Deploy-Token": "tok-1",
           "X-Factory-Github-Secret": "sec-1",
           "X-Factory-Persona": "implement",
       }
       redacted = mp.redact_headers(headers)
       assert redacted["X-Factory-Deploy-Token"] == "[REDACTED]"
       assert redacted["X-Factory-Github-Secret"] == "[REDACTED]"
       assert redacted["X-Factory-Persona"] == "implement"


   def test_build_ledger_row_shape():
       # intent="fix" is a multi-phase intent (implement -> conformance -> code-review),
       # so the caller passes stage="unknown" — this row shows the honest degraded case.
       row = mp.build_ledger_row(
           endpoint="/v1/messages",
           method="POST",
           model="claude-sonnet-4-6-20251101",
           status=200,
           duration_ms=1234,
           input_tokens=100,
           output_tokens=50,
           cache_read_tokens=0,
           cache_creation_tokens=0,
           tool_count=3,
           tool_bytes=900,
           system_bytes=400,
           request_bytes=2000,
           largest_tools=[{"name": "Bash", "bytes": 500}],
           streamed=True,
           run_id="abc123",
           issue_number=208,
           intent="fix",
           stage="unknown",
       )
       assert row["endpoint"] == "/v1/messages"
       assert row["model"] == "claude-sonnet-4-6-20251101"
       assert row["status"] == 200
       assert row["gen_ai.usage.input_tokens"] == 100
       assert row["gen_ai.usage.output_tokens"] == 50
       assert row["tool_count"] == 3
       assert row["run_id"] == "abc123"
       assert row["issue_number"] == 208
       assert row["stage"] == "unknown"
       assert row["persona"] == "unknown"
       assert "timestamp" in row


   def test_build_ledger_row_carries_single_phase_stage():
       # intent="plan" is single-phase — the whole container run IS the plan phase,
       # so the caller passes the exact stage through and it must be preserved verbatim.
       row = mp.build_ledger_row(
           endpoint="/v1/messages", method="POST", model="m", status=200,
           duration_ms=1, input_tokens=1, output_tokens=1, cache_read_tokens=0,
           cache_creation_tokens=0, tool_count=0, tool_bytes=0, system_bytes=0,
           request_bytes=0, largest_tools=[], streamed=False,
           run_id="abc123", issue_number=208, intent="plan", stage="plan",
       )
       assert row["stage"] == "plan"


   def test_build_ledger_row_defaults_when_correlation_missing():
       row = mp.build_ledger_row(
           endpoint="/v1/messages", method="POST", model="", status=502,
           duration_ms=10, input_tokens=0, output_tokens=0,
           cache_read_tokens=0, cache_creation_tokens=0, tool_count=0,
           tool_bytes=0, system_bytes=0, request_bytes=0, largest_tools=[],
           streamed=False, run_id=None, issue_number=None, intent=None, stage=None,
       )
       assert row["run_id"] == "unknown"
       assert row["issue_number"] == 0
       assert row["intent"] == "unknown"
       assert row["stage"] == "unknown"
   ```

2. Verify it fails (module doesn't exist yet):

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
   ```

   Expected: `ModuleNotFoundError: No module named 'factory_core.model_proxy'`

3. Implement `scripts/factory_core/model_proxy.py`:

   ```python
   #!/usr/bin/env python3
   """Dark factory model traffic proxy — transparent Anthropic passthrough + request ledger.

   Generic reverse proxy for ANTHROPIC_BASE_URL: forwards every request to the real
   Anthropic API unchanged (streaming responses forwarded byte-for-byte), while writing
   a compact per-request ledger row and optional raw artifacts. See
   docs/superpowers/specs/2026-07-11-transparent-model-proxy-design.md.
   """
   import fcntl
   import json
   import os
   import pathlib
   import re
   import time
   import urllib.error
   import urllib.request
   from datetime import datetime, timezone

   LEDGER_PATH = pathlib.Path(
       os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
   )
   CURRENT_RUN_PATH = pathlib.Path(
       os.environ.get("MODEL_PROXY_CURRENT_RUN_PATH", "/var/lib/dark-factory/current-run.json")
   )
   RAW_ARTIFACT_DIR = pathlib.Path(
       os.environ.get("RAW_ARTIFACT_DIR", "/var/lib/dark-factory/request-artifacts")
   )
   UPSTREAM = os.environ.get("MODEL_PROXY_UPSTREAM", "https://api.anthropic.com")
   PORT = int(os.environ.get("MODEL_PROXY_PORT", "8787"))
   MAX_LEDGER_BYTES = int(os.environ.get("MODEL_PROXY_MAX_BYTES", str(100 * 1024 * 1024)))
   BACKUP_COUNT = int(os.environ.get("MODEL_PROXY_BACKUP_COUNT", "3"))
   RAW_ARTIFACT_CAPTURE = os.environ.get("RAW_ARTIFACT_CAPTURE", "false").lower() == "true"
   RAW_ARTIFACT_RETENTION_DAYS = int(os.environ.get("RAW_ARTIFACT_RETENTION_DAYS", "7"))
   SEQ_URL = os.environ.get("SEQ_URL", "http://seq:5341")

   _REDACT_EXACT = {"authorization", "x-api-key", "api-key"}
   _REDACT_PATTERN = re.compile(r"^x-factory-.*-(token|secret)$", re.IGNORECASE)


   def redact_headers(headers: dict) -> dict:
       """Return a copy of headers with credential-like values replaced."""
       redacted = {}
       for key, value in headers.items():
           if key.lower() in _REDACT_EXACT or _REDACT_PATTERN.match(key):
               redacted[key] = "[REDACTED]"
           else:
               redacted[key] = value
       return redacted


   def _timestamp() -> str:
       return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


   def read_current_run() -> dict:
       """Best-effort correlation read — never raises, defaults to unknown.

       `stage` is written by entrypoint.sh from INTENT (see Task 7): exact for
       single-phase intents (refine/plan/deconflict/close/fix-main/recheck), "unknown"
       for multi-phase intents (fix/continue) where no investigated Archon data source
       exposes per-node timing — see the plan's Architecture "Stage attribution" note.
       """
       try:
           data = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
           return {
               "run_id": data.get("run_id") or "unknown",
               "issue_number": int(data.get("issue_number") or 0),
               "intent": data.get("intent") or "unknown",
               "stage": data.get("stage") or "unknown",
           }
       except Exception:
           return {"run_id": "unknown", "issue_number": 0, "intent": "unknown", "stage": "unknown"}


   def build_ledger_row(
       *, endpoint, method, model, status, duration_ms, input_tokens, output_tokens,
       cache_read_tokens, cache_creation_tokens, tool_count, tool_bytes, system_bytes,
       request_bytes, largest_tools, streamed, run_id=None, issue_number=None, intent=None,
       stage=None,
   ) -> dict:
       return {
           "timestamp": _timestamp(),
           "run_id": run_id or "unknown",
           "issue_number": int(issue_number or 0),
           "intent": intent or "unknown",
           "stage": stage or "unknown",
           "persona": "unknown",
           "endpoint": endpoint,
           "method": method,
           "model": model,
           "status": status,
           "duration_ms": duration_ms,
           "gen_ai.usage.input_tokens": input_tokens,
           "gen_ai.usage.output_tokens": output_tokens,
           "gen_ai.usage.cache_read_input_tokens": cache_read_tokens,
           "gen_ai.usage.cache_creation_input_tokens": cache_creation_tokens,
           "tool_count": tool_count,
           "tool_bytes": tool_bytes,
           "system_bytes": system_bytes,
           "request_bytes": request_bytes,
           "largest_tools": largest_tools,
           "streamed": streamed,
           # Always 0: the proxy is a single-pass forwarder (one inbound request ->
           # one outbound request, never retried at this layer — see requirement 2/3).
           # Claude-Code-level retries/fallbacks arrive as separate HTTP requests and
           # become their own correlated ledger rows; see the Architecture section's
           # "Retry/fallback metadata" note and the rollout doc.
           "retry_count": 0,
       }
   ```

4. Verify tests pass:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
   ```

   Expected: 5 passed.

5. Commit:

   ```bash
   git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
   git commit -m "feat(model-proxy): header redaction and ledger row construction"
   ```

---

## Task 3 — Ledger JSONL append + size rotation (TDD)

**Files:** `scripts/factory_core/model_proxy.py`, `tests/test_factory_model_proxy.py`

1. Add failing tests:

   ```python
   def test_append_ledger_writes_line(tmp_path, monkeypatch):
       path = tmp_path / "request-ledger.jsonl"
       monkeypatch.setattr(mp, "LEDGER_PATH", path)
       row = {"a": 1}
       mp.append_ledger(row)
       lines = path.read_text().strip().splitlines()
       assert len(lines) == 1
       assert json.loads(lines[0]) == {"a": 1}


   def test_append_ledger_rotates_at_max_bytes(tmp_path, monkeypatch):
       path = tmp_path / "request-ledger.jsonl"
       monkeypatch.setattr(mp, "LEDGER_PATH", path)
       monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", 50)
       monkeypatch.setattr(mp, "BACKUP_COUNT", 2)

       for i in range(10):
           mp.append_ledger({"i": i, "pad": "x" * 20})

       assert path.exists()
       assert (tmp_path / "request-ledger.jsonl.1").exists()
       # rotation caps backups — no unbounded growth of rotation files
       assert not (tmp_path / "request-ledger.jsonl.3").exists()


   def test_append_ledger_rotation_keeps_backup_count(tmp_path, monkeypatch):
       path = tmp_path / "request-ledger.jsonl"
       monkeypatch.setattr(mp, "LEDGER_PATH", path)
       monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", 10)
       monkeypatch.setattr(mp, "BACKUP_COUNT", 1)

       for i in range(20):
           mp.append_ledger({"i": i})

       assert path.exists()
       assert (tmp_path / "request-ledger.jsonl.1").exists()
       assert not (tmp_path / "request-ledger.jsonl.2").exists()
   ```

2. Verify fail: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k rotat` →
   `AttributeError: module 'factory_core.model_proxy' has no attribute 'append_ledger'`

3. Implement (append after `build_ledger_row`):

   ```python
   def _rotate_if_needed(path: pathlib.Path) -> None:
       if not path.exists() or path.stat().st_size < MAX_LEDGER_BYTES:
           return
       for i in range(BACKUP_COUNT - 1, 0, -1):
           src = path.with_suffix(path.suffix + f".{i}")
           dst = path.with_suffix(path.suffix + f".{i + 1}")
           if src.exists():
               src.rename(dst)
       oldest = path.with_suffix(path.suffix + f".{BACKUP_COUNT}")
       if oldest.exists():
           oldest.unlink()
       path.rename(path.with_suffix(path.suffix + ".1"))


   def append_ledger(row: dict) -> None:
       LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
       _rotate_if_needed(LEDGER_PATH)
       with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
           fcntl.flock(fh, fcntl.LOCK_EX)
           try:
               fh.write(json.dumps(row) + "\n")
           finally:
               fcntl.flock(fh, fcntl.LOCK_UN)
   ```

   Note: `path.with_suffix(path.suffix + ".1")` — `pathlib.Path.with_suffix` requires the
   argument to start with `.` and replaces the *existing* suffix; for a base name of
   `request-ledger.jsonl` this yields `request-ledger.jsonl.1` as intended (suffix becomes
   `.jsonl.1`). Confirm with a quick REPL check before running the suite:

   ```bash
   python3 -c "import pathlib; print(pathlib.Path('x/request-ledger.jsonl').with_suffix('.jsonl.1'))"
   ```

   Expected: `x/request-ledger.jsonl.1`

4. Verify tests pass: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v`
   → all passed.

5. Commit:

   ```bash
   git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
   git commit -m "feat(model-proxy): append-only ledger with size rotation"
   ```

---

## Task 4 — Seq POST for ledger rows (non-fatal)

**Files:** `scripts/factory_core/model_proxy.py`, `tests/test_factory_model_proxy.py`

1. Add failing test:

   ```python
   def test_post_seq_ledger_is_nonfatal(monkeypatch):
       monkeypatch.setattr(mp, "SEQ_URL", "http://unreachable-host-99999:5341")
       row = mp.build_ledger_row(
           endpoint="/v1/messages", method="POST", model="m", status=200,
           duration_ms=1, input_tokens=1, output_tokens=1, cache_read_tokens=0,
           cache_creation_tokens=0, tool_count=0, tool_bytes=0, system_bytes=0,
           request_bytes=0, largest_tools=[], streamed=False,
       )
       mp.post_seq_ledger(row)  # must not raise
   ```

2. Verify fail: `AttributeError: ... has no attribute 'post_seq_ledger'`

3. Implement (mirrors `run_record._post_seq`):

   ```python
   def post_seq_ledger(row: dict) -> None:
       payload = {
           "Events": [
               {
                   "Timestamp": row.get("timestamp", _timestamp()),
                   "Level": "Information",
                   "MessageTemplate": "factory.model_proxy.request endpoint={Endpoint} status={Status}",
                   "Properties": {
                       "gen_ai.system": "dark-factory-model-proxy",
                       "gen_ai.operation.name": "model_proxy.request",
                       "gen_ai.usage.input_tokens": row.get("gen_ai.usage.input_tokens", 0),
                       "gen_ai.usage.output_tokens": row.get("gen_ai.usage.output_tokens", 0),
                       "Endpoint": row.get("endpoint", ""),
                       "Model": row.get("model", ""),
                       "Status": row.get("status", 0),
                       "DurationMs": row.get("duration_ms", 0),
                       "RunId": row.get("run_id", ""),
                       "IssueNumber": row.get("issue_number", 0),
                       "Intent": row.get("intent", ""),
                       "ToolCount": row.get("tool_count", 0),
                       "ToolBytes": row.get("tool_bytes", 0),
                       "SystemBytes": row.get("system_bytes", 0),
                       "RequestBytes": row.get("request_bytes", 0),
                   },
               }
           ]
       }
       endpoint = f"{SEQ_URL.rstrip('/')}/api/events/raw"
       data = json.dumps(payload).encode()
       req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
       try:
           with urllib.request.urlopen(req, timeout=5) as resp:
               resp.read()
       except Exception:
           pass  # non-fatal: ledger file was already written
   ```

4. Verify tests pass, commit:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
   git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
   git commit -m "feat(model-proxy): non-fatal Seq summary POST for ledger rows"
   ```

---

## Task 5 — Raw artifact capture + retention sweep (TDD)

**Files:** `scripts/factory_core/model_proxy.py`, `tests/test_factory_model_proxy.py`

1. Add failing tests:

   ```python
   def test_write_raw_artifact_disabled_is_noop(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", False)
       monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
       mp.write_raw_artifact("run1", 1, {"foo": "bar"})
       assert list(tmp_path.iterdir()) == []


   def test_write_raw_artifact_enabled_writes_file(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
       monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
       mp.write_raw_artifact("run1", 1, {"foo": "bar"})
       out = tmp_path / "run1" / "1.json"
       assert out.exists()
       assert json.loads(out.read_text()) == {"foo": "bar"}


   def test_sweep_raw_artifacts_removes_stale_dirs(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
       monkeypatch.setattr(mp, "RAW_ARTIFACT_RETENTION_DAYS", 7)

       stale_dir = tmp_path / "old-run"
       stale_dir.mkdir()
       stale_file = stale_dir / "1.json"
       stale_file.write_text("{}")
       old_time = time.time() - (8 * 86400)
       os.utime(stale_file, (old_time, old_time))
       os.utime(stale_dir, (old_time, old_time))

       fresh_dir = tmp_path / "new-run"
       fresh_dir.mkdir()
       (fresh_dir / "1.json").write_text("{}")

       mp.sweep_raw_artifacts()

       assert not stale_dir.exists()
       assert fresh_dir.exists()
   ```

2. Verify fail (missing attributes), then implement:

   ```python
   def write_raw_artifact(run_id: str, seq: int, payload: dict) -> None:
       if not RAW_ARTIFACT_CAPTURE:
           return
       run_dir = RAW_ARTIFACT_DIR / run_id
       run_dir.mkdir(parents=True, exist_ok=True)
       (run_dir / f"{seq}.json").write_text(json.dumps(payload), encoding="utf-8")


   def sweep_raw_artifacts() -> None:
       if not RAW_ARTIFACT_DIR.exists():
           return
       cutoff = time.time() - (RAW_ARTIFACT_RETENTION_DAYS * 86400)
       for run_dir in RAW_ARTIFACT_DIR.iterdir():
           if not run_dir.is_dir():
               continue
           try:
               if run_dir.stat().st_mtime < cutoff:
                   for f in run_dir.iterdir():
                       f.unlink()
                   run_dir.rmdir()
           except OSError:
               continue  # best-effort — a sweep failure must never crash the proxy
   ```

3. Verify pass, commit:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
   git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
   git commit -m "feat(model-proxy): opt-in raw artifact capture with retention sweep"
   ```

---

## Task 6 — aiohttp proxy handler: generic passthrough, streaming, fail-closed (TDD)

**Files:** `scripts/factory_core/model_proxy.py`, `tests/test_factory_model_proxy.py`

1. Add failing tests using `aiohttp.test_utils` against a stub upstream `aiohttp` app:

   ```python
   import asyncio

   from aiohttp import web
   from aiohttp.test_utils import TestClient, TestServer


   def _run(coro):
       return asyncio.new_event_loop().run_until_complete(coro)


   async def _make_upstream(handler):
       app = web.Application()
       app.router.add_route("*", "/{tail:.*}", handler)
       server = TestServer(app)
       await server.start_server()
       return server


   def test_proxy_forwards_non_streaming_response(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
       monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
       monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)

       async def upstream_handler(request):
           body = await request.json()
           assert body["model"] == "claude-sonnet-4-6-20251101"
           return web.json_response(
               {"usage": {"input_tokens": 10, "output_tokens": 5}}, status=200
           )

       async def scenario():
           upstream = await _make_upstream(upstream_handler)
           monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
           app = mp.create_app()
           client = TestClient(TestServer(app))
           await client.start_server()
           resp = await client.post(
               "/v1/messages",
               json={"model": "claude-sonnet-4-6-20251101", "tools": [], "messages": []},
               headers={"x-api-key": "secret-key"},
           )
           assert resp.status == 200
           body = await resp.json()
           assert body["usage"]["input_tokens"] == 10
           await client.close()
           await upstream.close()

       _run(scenario())

       lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
       assert len(lines) == 1
       row = json.loads(lines[0])
       assert row["status"] == 200
       assert row["model"] == "claude-sonnet-4-6-20251101"
       assert row["gen_ai.usage.input_tokens"] == 10
       assert row["gen_ai.usage.output_tokens"] == 5


   def test_proxy_redacts_headers_before_persisting(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
       monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
       monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
       monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
       monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path / "artifacts")

       captured_upstream_headers = {}

       async def upstream_handler(request):
           captured_upstream_headers.update(request.headers)
           return web.json_response({"usage": {}}, status=200)

       async def scenario():
           upstream = await _make_upstream(upstream_handler)
           monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
           app = mp.create_app()
           client = TestClient(TestServer(app))
           await client.start_server()
           await client.post(
               "/v1/messages", json={"model": "m"}, headers={"x-api-key": "super-secret"}
           )
           await client.close()
           await upstream.close()

       _run(scenario())

       # The real secret still reached upstream (redaction is persistence-only)
       assert captured_upstream_headers.get("x-api-key") == "super-secret"

       artifact_files = list((tmp_path / "artifacts").rglob("*.json"))
       assert len(artifact_files) == 1
       persisted = json.loads(artifact_files[0].read_text())
       assert persisted["request_headers"]["x-api-key"] == "[REDACTED]"
       # Requirement 6 covers request AND response artifacts — assert both sides land.
       assert '"usage"' in persisted["response_body"]


   def test_proxy_fails_closed_on_upstream_unreachable(tmp_path, monkeypatch):
       monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
       monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
       monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
       monkeypatch.setattr(mp, "UPSTREAM", "http://127.0.0.1:1")  # nothing listens here

       async def scenario():
           app = mp.create_app()
           client = TestClient(TestServer(app))
           await client.start_server()
           resp = await client.post("/v1/messages", json={"model": "m"})
           assert resp.status in (502, 504)
           body = await resp.json()
           assert body["error"]["type"] == "factory_model_proxy_upstream_error"
           await client.close()

       _run(scenario())

       lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
       assert len(lines) == 1
       assert json.loads(lines[0])["status"] in (502, 504)
   ```

2. Verify fail: `AttributeError: module 'factory_core.model_proxy' has no attribute 'create_app'`

3. Implement the handler (append to `model_proxy.py`):

   ```python
   import aiohttp
   from aiohttp import web

   _TOKEN_RE = re.compile(rb'"input_tokens"\s*:\s*(\d+)|"output_tokens"\s*:\s*(\d+)')


   def _extract_usage_from_bytes(data: bytes) -> dict:
       """Best-effort scan for token usage in a (possibly streamed) SSE/JSON body.

       Non-streaming responses are fully parsed as JSON by the caller; this is the
       fallback used for SSE bodies, where we must not buffer/parse the full stream.
       Returns the LAST matches seen (Anthropic emits running usage updates).
       """
       usage = {"input_tokens": 0, "output_tokens": 0}
       for m in _TOKEN_RE.finditer(data):
           if m.group(1):
               usage["input_tokens"] = int(m.group(1))
           elif m.group(2):
               usage["output_tokens"] = int(m.group(2))
       return usage


   def _measure_request(body_bytes: bytes) -> dict:
       tool_count = tool_bytes = system_bytes = 0
       largest_tools = []
       try:
           parsed = json.loads(body_bytes) if body_bytes else {}
           tools = parsed.get("tools") or []
           tool_count = len(tools)
           tool_sizes = [
               {"name": t.get("name", "?"), "bytes": len(json.dumps(t))} for t in tools
           ]
           tool_bytes = sum(t["bytes"] for t in tool_sizes)
           largest_tools = sorted(tool_sizes, key=lambda t: t["bytes"], reverse=True)[:5]
           system = parsed.get("system", "")
           system_bytes = len(system) if isinstance(system, str) else len(json.dumps(system))
       except (json.JSONDecodeError, AttributeError):
           pass
       return {
           "tool_count": tool_count,
           "tool_bytes": tool_bytes,
           "system_bytes": system_bytes,
           "largest_tools": largest_tools,
       }


   async def handle_proxy(request: web.Request) -> web.StreamResponse:
       start = time.monotonic()
       body_bytes = await request.read()
       measurements = _measure_request(body_bytes)
       model = ""
       try:
           model = (json.loads(body_bytes) if body_bytes else {}).get("model", "")
       except json.JSONDecodeError:
           pass

       correlation = read_current_run()
       upstream_url = f"{UPSTREAM.rstrip('/')}{request.path_qs}"
       fwd_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

       session = request.app["client_session"]
       try:
           async with session.request(
               request.method, upstream_url, headers=fwd_headers, data=body_bytes,
               timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
           ) as upstream_resp:
               response = web.StreamResponse(
                   status=upstream_resp.status, headers=upstream_resp.headers
               )
               await response.prepare(request)
               usage_scan = bytearray()
               async for chunk in upstream_resp.content.iter_any():
                   await response.write(chunk)
                   usage_scan.extend(chunk)
               await response.write_eof()
       except (aiohttp.ClientError, TimeoutError, OSError) as exc:
           duration_ms = int((time.monotonic() - start) * 1000)
           error_body = {
               "error": {
                   "type": "factory_model_proxy_upstream_error",
                   "message": f"upstream request failed: {exc}",
               }
           }
           row = build_ledger_row(
               endpoint=request.path, method=request.method, model=model, status=504,
               duration_ms=duration_ms, input_tokens=0, output_tokens=0,
               cache_read_tokens=0, cache_creation_tokens=0, streamed=False,
               request_bytes=len(body_bytes), **measurements,
               run_id=correlation["run_id"], issue_number=correlation["issue_number"],
               intent=correlation["intent"], stage=correlation["stage"],
           )
           append_ledger(row)
           post_seq_ledger(row)
           return web.json_response(error_body, status=504)

       duration_ms = int((time.monotonic() - start) * 1000)
       is_streamed = "text/event-stream" in upstream_resp.headers.get("Content-Type", "")
       if is_streamed:
           usage = _extract_usage_from_bytes(bytes(usage_scan))
       else:
           try:
               usage = json.loads(bytes(usage_scan)).get("usage", {})
               usage = {
                   "input_tokens": usage.get("input_tokens", 0),
                   "output_tokens": usage.get("output_tokens", 0),
               }
           except json.JSONDecodeError:
               usage = {"input_tokens": 0, "output_tokens": 0}

       row = build_ledger_row(
           endpoint=request.path, method=request.method, model=model,
           status=upstream_resp.status, duration_ms=duration_ms,
           input_tokens=usage.get("input_tokens", 0),
           output_tokens=usage.get("output_tokens", 0),
           cache_read_tokens=0, cache_creation_tokens=0, streamed=is_streamed,
           request_bytes=len(body_bytes), **measurements,
           run_id=correlation["run_id"], issue_number=correlation["issue_number"],
           intent=correlation["intent"], stage=correlation["stage"],
       )
       append_ledger(row)
       post_seq_ledger(row)

       if RAW_ARTIFACT_CAPTURE:
           seq = int(time.time() * 1000)
           write_raw_artifact(
               correlation["run_id"], seq,
               {
                   "request_headers": redact_headers(dict(request.headers)),
                   "request_path": request.path,
                   "request_body": body_bytes.decode("utf-8", errors="replace"),
                   "response_status": upstream_resp.status,
                   "response_headers": redact_headers(dict(upstream_resp.headers)),
                   # usage_scan already holds the full forwarded response bytes (it was
                   # populated alongside response.write(chunk) above, at no extra upstream
                   # cost) — requirement 6 asks for request/response artifacts, so persist
                   # both sides, not request-only.
                   "response_body": bytes(usage_scan).decode("utf-8", errors="replace"),
               },
           )
       return response


   def create_app() -> web.Application:
       app = web.Application()
       app["client_session"] = aiohttp.ClientSession()
       app.router.add_route("*", "/{tail:.*}", handle_proxy)

       async def _cleanup(app):
           await app["client_session"].close()

       app.on_cleanup.append(_cleanup)
       return app


   def main() -> None:
       sweep_raw_artifacts()
       web.run_app(create_app(), port=PORT)


   if __name__ == "__main__":
       main()
   ```

   Note on the `_extract_usage_from_bytes` helper: it buffers `usage_scan` (a copy of the
   forwarded bytes) purely for post-hoc metric extraction — the actual client response is
   streamed via `response.write(chunk)` *before* appending to `usage_scan`, so forwarding
   latency is unaffected and nothing is held back from the client. This satisfies "streamed
   unchanged" while still allowing best-effort usage capture.

4. Verify tests pass:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
   ```

   Expected: all tests (Tasks 2–6) pass, no warnings about unclosed sessions (the `_cleanup`
   hook and `await client.close()` in tests handle teardown).

5. Commit:

   ```bash
   git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
   git commit -m "feat(model-proxy): generic streaming passthrough with fail-closed upstream errors"
   ```

---

## Task 7 — `entrypoint.sh`: write the correlation pointer file

**Files:** `entrypoint.sh`

1. Locate the `RUN_ID` generation block (around the `--- Canonical run identity ---` comment)
   and add the pointer-file write immediately after `ARTIFACTS_DIR` is created:

   ```bash
   # --- Canonical run identity and artifact directory ---
   # ARCHON_RUN_ID is not set by archon; always generate a UUID for correlation.
   RUN_ID=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
   RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
   ARTIFACTS_DIR="${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs/${RUN_ID}"
   export ARTIFACTS_DIR
   mkdir -p "$ARTIFACTS_DIR"

   # --- Model-proxy correlation pointer (best-effort; consumed by factory-model-proxy
   # when FACTORY_MODEL_PROXY_ENABLED — see model_proxy.py's read_current_run()). Written
   # unconditionally and cheaply; the proxy is a no-op reader when disabled.
   #
   # RUN_STAGE: single-phase intents (refine/plan/deconflict/close/fix-main/recheck) map
   # 1:1 to the phase the whole container run performs, so the ledger can attribute every
   # request in the run to that exact stage. Multi-phase intents (fix/continue traverse
   # implement -> conformance -> code-review -> merge inside one container run) cannot be
   # placed this way — investigated during planning: neither `archon workflow get --json`
   # nor `archon workflow runs --json` expose per-node/per-step timestamps for this
   # workflow's node style, so "unknown" is the honest, investigated answer for those two
   # intents, not an assumption. ---
   case "${INTENT:-unknown}" in
     refine|plan|deconflict|close|fix-main|recheck) RUN_STAGE="${INTENT}" ;;
     *) RUN_STAGE="unknown" ;;
   esac
   mkdir -p /var/lib/dark-factory 2>/dev/null || true
   printf '{"run_id":"%s","issue_number":%s,"intent":"%s","stage":"%s","started_at":"%s"}\n' \
     "$RUN_ID" "${ISSUE_NUM:-0}" "${INTENT:-unknown}" "$RUN_STAGE" "$RUN_STARTED_AT" \
     > /var/lib/dark-factory/current-run.json 2>/dev/null || true
   ```

   This must be placed **after** `ISSUE_NUM`/`INTENT` are parsed (they already are, earlier in
   the file, at the `--- Extract issue number and intent immediately ---` block) and after
   `RUN_ID` is generated.

2. Add a regression assertion to the existing sourcing-based test harness. Extend
   `tests/test_431_telemetry_isolation.sh`'s style by adding a new, focused test file (keeps
   #521's test scoped to post-mortem telemetry, per its own docstring):

   `tests/test_entrypoint_current_run.sh`:

   ```bash
   #!/usr/bin/env bash
   # Test: entrypoint.sh writes /var/lib/dark-factory/current-run.json after RUN_ID
   # generation, for factory-model-proxy correlation (issue #208).
   set -uo pipefail

   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

   export GH_TOKEN="stub-token"
   export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

   git() { return 0; }
   export -f git
   gh() { echo "stub-title"; return 0; }
   export -f gh
   docker() { return 0; }
   export -f docker

   # Redirect the shared state dir to a scratch location — this test must never
   # touch the real /var/lib/dark-factory.
   SCRATCH_STATE=$(mktemp -d /tmp/208-state-XXXXXX)
   mkdir -p "$SCRATCH_STATE"

   # entrypoint.sh hardcodes /var/lib/dark-factory for this write (matches the
   # shared-volume convention used by runs.jsonl / the main-red sentinel), so this
   # test asserts against that path directly and cleans it up afterward — it does
   # not create the dir if absent, and removes only the file it wrote.
   PRE_EXISTED=0
   [ -f /var/lib/dark-factory/current-run.json ] && PRE_EXISTED=1
   [ "$PRE_EXISTED" = "1" ] && cp /var/lib/dark-factory/current-run.json /tmp/208-backup.json

   ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
   export ARTIFACTS_DIR

   PASSED=0; FAILED=0
   assert_true() {
     local desc="$1" condition="$2"
     if eval "$condition"; then echo "  PASS: $desc"; PASSED=$((PASSED + 1))
     else echo "  FAIL: $desc" >&2; FAILED=$((FAILED + 1)); fi
   }
   stage_of() {
     python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('stage','missing'))" 2>/dev/null
   }

   echo "=== #208: entrypoint writes current-run.json ==="

   # Case 1: single-phase intent ("plan") — stage must be the exact phase name.
   ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
   export ARTIFACTS_DIR
   ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #208" \
     source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #208"

   trap - ERR
   set +e; set +u; set +o pipefail

   assert_true "current-run.json exists" "[ -f /var/lib/dark-factory/current-run.json ]"

   ISSUE_FIELD=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('issue_number','missing'))" 2>/dev/null)
   assert_true "issue_number is 208" "[ '$ISSUE_FIELD' = '208' ]"

   RUN_ID_FIELD=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('run_id','missing'))" 2>/dev/null)
   assert_true "run_id is non-empty" "[ -n '$RUN_ID_FIELD' ] && [ '$RUN_ID_FIELD' != 'missing' ]"

   STAGE_FIELD=$(stage_of)
   assert_true "single-phase intent 'plan' -> stage='plan'" "[ '$STAGE_FIELD' = 'plan' ]"

   rm -rf "$ARTIFACTS_DIR"

   # Case 2: multi-phase intent ("fix") — stage must honestly degrade to 'unknown'.
   set -uo pipefail
   ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
   export ARTIFACTS_DIR
   ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Fix issue #208" \
     source "$SCRIPT_DIR/../entrypoint.sh" "Fix issue #208"

   trap - ERR
   set +e; set +u; set +o pipefail

   STAGE_FIELD=$(stage_of)
   assert_true "multi-phase intent 'fix' -> stage='unknown'" "[ '$STAGE_FIELD' = 'unknown' ]"

   # Cleanup — restore prior state instead of leaving the scratch write behind
   if [ "$PRE_EXISTED" = "1" ]; then
     cp /tmp/208-backup.json /var/lib/dark-factory/current-run.json
     rm -f /tmp/208-backup.json
   else
     rm -f /var/lib/dark-factory/current-run.json
   fi
   rm -rf "$ARTIFACTS_DIR" "$SCRATCH_STATE"

   echo ""
   echo "Results: ${PASSED} passed, ${FAILED} failed"
   [ "$FAILED" -eq 0 ]
   ```

3. Run: `bash tests/test_entrypoint_current_run.sh`

   Expected: `Results: 5 passed, 0 failed`, exit 0.

4. Commit:

   ```bash
   git add entrypoint.sh tests/test_entrypoint_current_run.sh
   git commit -m "feat(entrypoint): write current-run.json for model-proxy correlation"
   ```

---

## Task 8 — `run-compose.yml`: add the `factory-model-proxy` service

**Files:** `run-compose.yml`, `tests/test_model_proxy_compose.sh` (new)

1. Write the failing test first — `tests/test_model_proxy_compose.sh`:

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   # Tests for #208: factory-model-proxy service is opt-in via a distinct compose
   # profile and does not change default (flag-off) behavior.

   REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
   RC="$REPO_ROOT/run-compose.yml"

   grep -q "factory-model-proxy:" "$RC" || { echo "FAIL: factory-model-proxy service missing from run-compose.yml"; exit 1; }
   grep -q "profiles:" "$RC" || { echo "FAIL: no profiles block found"; exit 1; }

   if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
     echo "SKIP: docker not available — skipping compose config parse checks"
     echo "PASS: test_model_proxy_compose.sh (partial — grep checks only)"
     exit 0
   fi

   mkdir -p "$REPO_ROOT/.archon"
   SCRATCH_ENV=0
   [ -f "$REPO_ROOT/.archon/.env" ] || { touch "$REPO_ROOT/.archon/.env"; SCRATCH_ENV=1; }

   # 1) Flag off (default): factory-model-proxy must NOT appear in the rendered config
   #    for the profile set scheduler.sh actually dispatches with.
   OFF_CONFIG=$(docker compose -f "$RC" --profile factory config 2>&1)
   if echo "$OFF_CONFIG" | grep -q "factory-model-proxy"; then
     echo "FAIL: factory-model-proxy present in config with --profile factory alone (must be opt-in)"
     [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
     exit 1
   fi
   echo "PASS: factory-model-proxy absent from default (--profile factory) config"

   # 2) Flag on: adding --profile factory-model-proxy must include the service.
   ON_CONFIG=$(docker compose -f "$RC" --profile factory --profile factory-model-proxy config 2>&1)
   if ! echo "$ON_CONFIG" | grep -q "factory-model-proxy"; then
     echo "FAIL: factory-model-proxy absent even with --profile factory-model-proxy set"
     [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
     exit 1
   fi
   echo "PASS: factory-model-proxy present when its profile is explicitly enabled"

   [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
   echo "PASS: test_model_proxy_compose.sh"
   ```

2. Run it before the compose edit — expect FAIL (`grep -q "factory-model-proxy:"` fails):

   ```bash
   bash tests/test_model_proxy_compose.sh
   ```

3. Edit `run-compose.yml` — add the service and the optional dependency:

   ```yaml
   services:
     dark-factory:
       image: ${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}
       env_file:
         - path: .archon/.env
           required: true
       environment:
         # ... (existing keys unchanged) ...
         FACTORY_IMAGE: ${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}
       volumes:
         - scheduler_state:/var/lib/dark-factory
       networks:
         - dark-factory-net
       profiles:
         - factory
       depends_on:
         factory-model-proxy:
           condition: service_started
           required: false

     # Opt-in transparent Anthropic-traffic proxy + request ledger (#208). Only
     # started when scheduler.sh dispatches with --profile factory-model-proxy
     # (gated on FACTORY_MODEL_PROXY_ENABLED in .archon/.env) — see
     # docs/dark-factory-model-proxy-rollout.md. `required: false` above means
     # the dark-factory service starts fine whether or not this profile is active.
     factory-model-proxy:
       image: ${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}
       entrypoint: ["python3", "-m", "factory_core.model_proxy"]
       environment:
         PYTHONPATH: /opt/dark-factory/scripts
         MODEL_PROXY_PORT: "8787"
         MODEL_PROXY_UPSTREAM: https://api.anthropic.com
         RAW_ARTIFACT_CAPTURE: ${RAW_ARTIFACT_CAPTURE:-false}
         RAW_ARTIFACT_RETENTION_DAYS: ${RAW_ARTIFACT_RETENTION_DAYS:-7}
         SEQ_URL: ${SEQ_URL:-http://seq:5341}
       volumes:
         - scheduler_state:/var/lib/dark-factory
       networks:
         - dark-factory-net
       profiles:
         - factory-model-proxy
   ```

   Note: `entrypoint:` overrides the image's baked `ENTRYPOINT ["entrypoint.sh"]`, which is
   correct here — this service runs the proxy module, not the target-repo dispatch flow.

4. Re-run: `bash tests/test_model_proxy_compose.sh` → expect all `PASS:` lines (or the `SKIP:`
   branch if Docker is unavailable in this environment — check first with
   `docker compose version`).

5. Run the pre-existing compose test too, to confirm no regression:

   ```bash
   bash tests/test_run_compose.sh
   ```

   Expected: `PASS: test_run_compose.sh`.

6. Commit:

   ```bash
   git add run-compose.yml tests/test_model_proxy_compose.sh
   git commit -m "feat(compose): opt-in factory-model-proxy service gated by its own profile"
   ```

---

## Task 9 — `scheduler.sh`: conditional `--profile factory-model-proxy`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

1. Add a failing case to `tests/test_scheduler.sh`, directly after the existing "C: dispatch()
   exit-code capture" section:

   ```bash
   # ==========================================
   # C2: dispatch() adds --profile factory-model-proxy only when the flag is set (#208)
   # ==========================================
   echo ""
   echo "--- C2: dispatch() model-proxy profile flag ---"
   > "$STUB_LOG"

   docker() { echo "docker $*" >> "$STUB_LOG"; return 0; }
   export -f docker

   unset FACTORY_MODEL_PROXY_ENABLED
   dispatch "Fix issue #2"
   assert_eq "profile flag absent when FACTORY_MODEL_PROXY_ENABLED unset" \
     "0" "$(grep -c -- '--profile factory-model-proxy' "$STUB_LOG" || true)"

   > "$STUB_LOG"
   export FACTORY_MODEL_PROXY_ENABLED=true
   dispatch "Fix issue #2"
   assert_eq "profile flag present when FACTORY_MODEL_PROXY_ENABLED=true" \
     "1" "$(grep -c -- '--profile factory-model-proxy' "$STUB_LOG" || true)"
   unset FACTORY_MODEL_PROXY_ENABLED
   ```

2. Run: `bash tests/test_scheduler.sh` → expect the two new `assert_eq` calls to FAIL (flag
   never added yet — first assertion trivially passes since grep count is already 0, but the
   second must fail until `dispatch()` is changed).

3. Edit `scheduler.sh`'s `dispatch()`:

   ```bash
   dispatch() {
     local command="$1"
     local exit_code=0
     local -a profile_flags=(--profile factory)
     if [ "${FACTORY_MODEL_PROXY_ENABLED:-false}" = "true" ]; then
       profile_flags+=(--profile factory-model-proxy)
     fi
     echo "[$(date -u +%FT%TZ)] dispatch command=\"${command}\""
     docker compose -f /opt/dark-factory/docker-compose.yml "${profile_flags[@]}" run \
       -d --rm dark-factory "$command" || exit_code=$?
     if [ "$exit_code" -ne 0 ]; then
       echo "[$(date -u +%FT%TZ)] dispatch_error command=\"${command}\" exit=${exit_code}" >&2
     fi
     return "$exit_code"
   }
   ```

4. Re-run: `bash tests/test_scheduler.sh` → expect all assertions (including the two new ones)
   to pass; check the full run's final summary line for `0 failed`.

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "feat(scheduler): dispatch adds --profile factory-model-proxy when enabled"
   ```

---

## Task 10 — Document opt-in env vars and the persistent-proxy alternative

**Files:** `deploy/instance.env.example`, `deploy/docker-compose.yml`

1. Append to `deploy/instance.env.example` (after the "Scheduler tuning" section):

   ```
   # ---------------------------------------------------------------------------
   # Transparent model traffic proxy (optional — default off, see #208)
   # ---------------------------------------------------------------------------

   # Opt in to routing Claude Code traffic through factory-model-proxy for a
   # per-request ledger (docs/dark-factory-model-proxy-rollout.md). Default
   # unset/false = today's direct-to-Anthropic behavior, byte-for-byte.
   # FACTORY_MODEL_PROXY_ENABLED=true

   # Required alongside FACTORY_MODEL_PROXY_ENABLED — points Claude Code at the
   # proxy instead of api.anthropic.com directly.
   # ANTHROPIC_BASE_URL=http://factory-model-proxy:8787

   # Opt-in raw request/response artifact capture (off by default — the compact
   # ledger has no payload content; raw artifacts do, including system prompts
   # and code diffs). Retention: RAW_ARTIFACT_RETENTION_DAYS (default 7).
   # RAW_ARTIFACT_CAPTURE=true
   # RAW_ARTIFACT_RETENTION_DAYS=7
   ```

2. Append a commented, documented example block to `deploy/docker-compose.yml` (after the
   `backlog-scheduler` service, before the top-level `networks:` key) showing the
   Alternative-2 persistent-proxy pattern for operators who want one proxy shared across every
   dispatched run instead of the per-dispatch-profile default:

   ```yaml
     # --- Optional: persistent factory-model-proxy (alternative to the per-run
     # profile-gated service in run-compose.yml) ---
     # Uncomment for a single long-lived proxy shared across every dispatched run
     # instead of the default per-run opt-in (run-compose.yml's factory-model-proxy
     # service, gated by FACTORY_MODEL_PROXY_ENABLED). Trade-off: this file is
     # critical_diff_paths-flagged (higher review scrutiny) and affects every
     # operator's running stack, which is why it is NOT the default — see
     # docs/dark-factory-model-proxy-rollout.md.
     # factory-model-proxy:
     #   image: ghcr.io/omniscient/dark-factory:${IMAGE_TAG:-latest}
     #   container_name: ${FACTORY_INSTANCE:-dark-factory}-model-proxy
     #   restart: unless-stopped
     #   entrypoint: ["python3", "-m", "factory_core.model_proxy"]
     #   environment:
     #     PYTHONPATH: /opt/dark-factory/scripts
     #     MODEL_PROXY_PORT: "8787"
     #   volumes:
     #     - dark_factory_state:/var/lib/dark-factory
     #   networks:
     #     - dark-factory-net
   ```

3. Verify neither file's YAML syntax broke (the docker-compose.yml edit is comment-only, but
   verify the parse still succeeds since comments must stay valid YAML — no accidental
   unescaped colons breaking the parser):

   ```bash
   docker compose -f deploy/docker-compose.yml config >/dev/null && echo "PASS: deploy/docker-compose.yml still parses"
   ```

   (Skip this check if Docker is unavailable in the current environment — same convention as
   `test_run_compose.sh`.)

4. Commit:

   ```bash
   git add deploy/instance.env.example deploy/docker-compose.yml
   git commit -m "docs(deploy): document opt-in model-proxy env vars and persistent-proxy alternative"
   ```

---

## Task 11 — Smoke test: normal factory commands unaffected with the proxy enabled (AC8)

**Files:** `tests/test_model_proxy_smoke.sh` (new)

**Scope note (deliberate substitution, confirmed during architect review):** the spec's Testing
section describes this smoke test as running "a real factory command end-to-end with
`FACTORY_MODEL_PROXY_ENABLED=true`" (mirroring `tests/test_431_telemetry_isolation.sh`'s
sourcing pattern). A literal reading would require a live `ANTHROPIC_API_KEY`/
`CLAUDE_CODE_OAUTH_TOKEN` and a real model call inside CI, which is neither repeatable nor
credential-safe to commit to a public test suite. This task instead runs the **actual
`factory_core.model_proxy` module** (the real `create_app()`/`handle_proxy` code path, not a
reimplementation or a mock of the proxy itself) end-to-end over real HTTP through
`aiohttp.test_utils`, against a stub upstream standing in only for `api.anthropic.com` — i.e.
everything downstream of "Claude Code makes an HTTP call to `ANTHROPIC_BASE_URL`" is exercised
for real; only the live Anthropic backend is stubbed. This is the closest feasible proof of "a
normal factory command's HTTP traffic passes through the proxy unaffected" without a live
credential dependency, and is called out as such (not silently substituted) in
`docs/dark-factory-model-proxy-rollout.md`'s Known Limitations section.

1. Write `tests/test_model_proxy_smoke.sh` — runs the real `model_proxy.create_app()` against a
   stub upstream (no live Anthropic call), proving the module starts, serves, and writes a
   ledger row end-to-end outside of pytest's process (closer to how the proxy actually runs as
   its own container process):

   ```bash
   #!/usr/bin/env bash
   # Smoke test (#208 AC8): factory-model-proxy starts, proxies a request, and writes
   # a ledger row — without touching a real Anthropic endpoint or the real
   # /var/lib/dark-factory state dir.
   set -euo pipefail

   REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
   SCRATCH=$(mktemp -d /tmp/208-smoke-XXXXXX)
   trap 'rm -rf "$SCRATCH"' EXIT

   PYTHONPATH="$REPO_ROOT/scripts" python3 - "$SCRATCH" <<'PYEOF'
   import asyncio
   import json
   import sys
   from pathlib import Path

   from aiohttp import web
   from aiohttp.test_utils import TestClient, TestServer

   from factory_core import model_proxy as mp

   scratch = Path(sys.argv[1])
   mp.LEDGER_PATH = scratch / "request-ledger.jsonl"
   mp.CURRENT_RUN_PATH = scratch / "current-run.json"
   mp.RAW_ARTIFACT_DIR = scratch / "artifacts"
   mp.post_seq_ledger = lambda row: None  # no live Seq in this smoke test

   (scratch / "current-run.json").write_text(
       json.dumps({"run_id": "smoke-run", "issue_number": 208, "intent": "plan", "stage": "plan"})
   )


   async def upstream_handler(request):
       await request.json()
       return web.json_response({"usage": {"input_tokens": 7, "output_tokens": 3}}, status=200)


   async def main():
       upstream_app = web.Application()
       upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
       upstream = TestServer(upstream_app)
       await upstream.start_server()
       mp.UPSTREAM = f"http://{upstream.host}:{upstream.port}"

       app = mp.create_app()
       client = TestClient(TestServer(app))
       await client.start_server()

       resp = await client.post(
           "/v1/messages",
           json={"model": "claude-sonnet-4-6-20251101", "tools": [], "messages": []},
           headers={"x-api-key": "smoke-test-key"},
       )
       assert resp.status == 200, f"expected 200, got {resp.status}"
       body = await resp.json()
       assert body["usage"]["input_tokens"] == 7

       await client.close()
       await upstream.close()


   asyncio.run(main())

   ledger_lines = mp.LEDGER_PATH.read_text().strip().splitlines()
   assert len(ledger_lines) == 1, f"expected 1 ledger row, got {len(ledger_lines)}"
   row = json.loads(ledger_lines[0])
   assert row["run_id"] == "smoke-run", row
   assert row["issue_number"] == 208, row
   assert row["stage"] == "plan", row
   assert row["status"] == 200, row
   print("SMOKE_OK")
   PYEOF

   echo "PASS: test_model_proxy_smoke.sh"
   ```

   The Python heredoc's `assert` calls exit non-zero (via `AssertionError`) on any failure,
   which propagates through the script's `set -e`, so no separate bash-level assertion
   plumbing is needed here — the heredoc's own exit code is the test result.

2. Run it — expect it to already pass at this point (Tasks 2–6 are complete), confirming
   integration end-to-end:

   ```bash
   bash tests/test_model_proxy_smoke.sh
   ```

   Expected output ends with `SMOKE_OK` then `PASS: test_model_proxy_smoke.sh`, exit 0.

   If this is the first time it's run and something in Tasks 2–6 was missed, it will fail here
   with the real `AssertionError` — treat that as the "red" signal for this task before
   re-verifying Tasks 2–6's implementation.

3. Commit:

   ```bash
   git add tests/test_model_proxy_smoke.sh
   git commit -m "test(model-proxy): end-to-end smoke test proving proxy+ledger integration (AC8)"
   ```

---

## Task 12 — Wire CI and write the rollout/rollback doc (AC9)

**Files:** `.github/workflows/ci.yml`, `docs/dark-factory-model-proxy-rollout.md` (new)

1. Extend the `tests` job in `.github/workflows/ci.yml` to run the new bash tests explicitly
   (matching the existing pattern for `test_run_compose.sh` etc.):

   ```yaml
         - run: bash tests/test_identity.sh
         - run: bash tests/test_hooks.sh
         - run: bash tests/test_smoke_gate.sh
         - run: bash tests/test_run_compose.sh
         - run: bash tests/test_model_proxy_compose.sh
         - run: bash tests/test_model_proxy_smoke.sh
         - run: bash tests/test_entrypoint_current_run.sh
   ```

2. Write `docs/dark-factory-model-proxy-rollout.md`:

   ```markdown
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
   ```

3. Verify the ci.yml YAML is still well-formed:

   ```bash
   python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "PASS: ci.yml parses"
   ```

4. Commit:

   ```bash
   git add .github/workflows/ci.yml docs/dark-factory-model-proxy-rollout.md
   git commit -m "docs+ci: model-proxy rollout/rollback doc; wire new tests into CI"
   ```

---

## Task 13 — Full local verification pass

1. Run the complete Python suite:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/ -v
   ```

   Expected: all tests pass, including every `test_factory_model_proxy.py` case and the
   pre-existing suite (no regressions).

2. Run every bash test touched or added by this plan:

   ```bash
   bash tests/test_identity.sh
   bash tests/test_hooks.sh
   bash tests/test_smoke_gate.sh
   bash tests/test_run_compose.sh
   bash tests/test_model_proxy_compose.sh
   bash tests/test_model_proxy_smoke.sh
   bash tests/test_entrypoint_current_run.sh
   bash tests/test_scheduler.sh
   ```

   Expected: every script prints its own `PASS:` line (or, for `test_scheduler.sh`, a final
   `0 failed` summary) and exits 0.

3. Run the workflow DAG checks (unaffected by this ticket, but part of the documented gate
   set — confirms nothing in `workflows/archon-dark-factory.yaml` needed touching):

   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```

4. Docker build sanity (confirms the new `aiohttp` pip install and the `factory-model-proxy`
   entrypoint module both resolve inside the actual image):

   ```bash
   docker build -f Dockerfile -t dark-factory:plan-208-check . && \
     docker run --rm --entrypoint python3 dark-factory:plan-208-check \
       -c "import sys; sys.path.insert(0, '/opt/dark-factory/scripts'); from factory_core import model_proxy; print('OK')"
   ```

   Expected: `OK`. (Skip if Docker is unavailable in the current environment; note the skip in
   the phase's summary rather than silently omitting the check.)

No further commit — this task is verification-only over work already committed in Tasks 1–12.

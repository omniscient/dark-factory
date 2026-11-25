"""Local-endpoint runtime adapter: the Request/Result-contract checks (#558).

Contract: docs/hybrid-execution-contract.md §4.3, §4.4, §4.6, §4.7; design in
docs/archive/2026-11-25-hybrid-runtime-adapter-design.md.

Plain functions over dicts and headers — no aiohttp import — so each check unit-tests without
the TestServer harness. model_proxy.py imports this module only when an endpoint is selected
(FACTORY_MODEL_ENDPOINT), so the default cloud path never loads it or the registry code below.
"""
import asyncio
import json
import os
import re
import uuid
from types import MappingProxyType

from factory_core.providers import model_endpoint

CORRELATION_HEADER = "X-Factory-Correlation"
# §4.2: a local-routed container never hosts an independent checker — a contract constant.
LOCAL_ROLE = "actor"
REFUSAL_TYPE = "factory_model_adapter_refused"
# Dropped from every local-destined request: a client-supplied credential is self-report (#288).
INBOUND_AUTH_HEADERS = ("authorization", "x-api-key", "api-key")
# protocol is pinned to anthropic-messages, whose auth header this is.
LOCAL_AUTH_HEADER = "x-api-key"
SUPPORTED_METHOD = "POST"
SUPPORTED_PATH = "/v1/messages"
FORCED_TOOL_CHOICES = ("any", "tool")
DEFERRED_TOOL_FIELD = "defer_loading"
STRUCTURED_OUTPUT_FIELDS = ("output_config", "output_format")
# A local upstream answering one of these is not ready: forwarded unchanged, never retried.
NOT_READY_STATUSES = (502, 503, 504)

# Contract §4.6's interim failure classes; #238's names supersede these when it ships.
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
TIMEOUT_CONNECT = "timeout_connect"
TIMEOUT_TIME_TO_FIRST_TOKEN = "timeout_time_to_first_token"
TIMEOUT_IDLE = "timeout_idle"
TIMEOUT_TOTAL = "timeout_total"
CANCELLATION = "cancellation"
PROTOCOL_INCOMPATIBILITY = "protocol_incompatibility"
# Startup-only: evidence problems fail load_verified_entry(), never a request (spec Req. 14).
CAPABILITY_EVIDENCE_INVALID = "capability_evidence_invalid"
DESTINATION_POLICY_DENIAL = "destination_policy_denial"
FAILURE_CLASSES = (
    ENDPOINT_UNREACHABLE, TIMEOUT_CONNECT, TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_IDLE,
    TIMEOUT_TOTAL, CANCELLATION, PROTOCOL_INCOMPATIBILITY, CAPABILITY_EVIDENCE_INVALID,
    DESTINATION_POLICY_DENIAL,
)

_MESSAGE_STOP_RE = re.compile(rb'"type"\s*:\s*"message_stop"')


class DestinationError(Exception):
    """A startup problem: the proxy must not bind. .problems is the list of messages."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class AdapterRefusal(Exception):
    """An explicit refusal — a real error body and a ledger row, never a synthetic success."""

    def __init__(self, status, failure_class, message):
        super().__init__(message)
        self.status = status
        self.failure_class = failure_class
        self.message = message


def destination_from_entry(entry, environ=None):
    """Startup checks over one verified entry; the read-only destination, or DestinationError.

    The payload decision is computed here, once (declared is None in v1 — spec Req. 15).
    A local entry's upstream is its base_url and nothing else (Req. 3); its credential scope
    is enforced, not assumed (Req. 4). Never holds a secret value.
    """
    env = os.environ if environ is None else environ
    eid = entry["id"]
    problems = []
    decision = model_endpoint.payload_decision(None, entry)
    if not decision["allow"]:
        problems.append(f"endpoint '{eid}': payload decision does not allow dispatch — "
                        f"{decision['reason']}")
    upstream = None
    if entry["hosting_location"] == "local":
        upstream = entry["base_url"]
        configured = env.get("MODEL_PROXY_UPSTREAM")
        if configured and configured.rstrip("/") != upstream.rstrip("/"):
            # The configured value is not echoed: it is whatever a human set, not ours to log.
            problems.append(f"MODEL_PROXY_UPSTREAM is set and differs from endpoint '{eid}' "
                            "base_url — a local-routed proxy forwards only to the verified "
                            "entry's base_url")
        present = sorted(n for n in model_endpoint.CLOUD_CREDENTIALS if env.get(n))
        if present:
            problems.append(f"endpoint '{eid}' is local but cloud model credential(s) are set "
                            f"in the proxy: {', '.join(present)} — a local-routed proxy holds "
                            "none (contract §4.3)")
        ref = entry["secret_reference"]
        if not env.get(ref):
            problems.append(f"{ref} (secret_reference of endpoint '{eid}') is not set in the "
                            "proxy")
    if problems:
        raise DestinationError(problems)
    return MappingProxyType({
        "endpoint_id": eid,
        "hosting_location": entry["hosting_location"],
        "upstream": upstream,
        "served_name": entry["model_identity"]["served_name"],
        "secret_reference": entry["secret_reference"],
        "timeouts": MappingProxyType(dict(entry.get("timeouts") or {})),
        "payload_decision": MappingProxyType(decision),
    })


def resolve_local_destination(registry_path, endpoint_id):
    """The proxy's one startup resolution (spec Req. 2): verified loader, then the checks."""
    problems, entry = model_endpoint.load_verified_entry(registry_path, endpoint_id)
    if problems:
        raise DestinationError(problems)
    return destination_from_entry(entry)


def _incompatible(message):
    return AdapterRefusal(400, PROTOCOL_INCOMPATIBILITY, message)


def check_request(dest, method, path, body) -> dict:
    """Local-path refusals, before any outbound call (spec Req. 9, 10); the parsed body.

    Field-based and fixed: only POST /v1/messages (path compared without its query string);
    forced tool choice, deferred tools and structured output are refused, never forwarded.
    """
    if method != SUPPORTED_METHOD or path != SUPPORTED_PATH:
        raise _incompatible(f"{method} {path} is not supported on a local destination — "
                            f"only {SUPPORTED_METHOD} {SUPPORTED_PATH}")
    try:
        parsed = json.loads(body) if body else None
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        parsed = None
    if not isinstance(parsed, dict):
        raise _incompatible("request body is not a JSON object")
    choice = parsed.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") in FORCED_TOOL_CHOICES:
        raise _incompatible(f"forced tool choice ({choice['type']}) is not supported on a "
                            "local destination")
    tools = parsed.get("tools")
    if isinstance(tools, list) and any(isinstance(t, dict) and DEFERRED_TOOL_FIELD in t
                                       for t in tools):
        raise _incompatible(f"deferred tools ({DEFERRED_TOOL_FIELD}) are not supported on a "
                            "local destination")
    for field in STRUCTURED_OUTPUT_FIELDS:
        if field in parsed:
            raise _incompatible(f"structured output ({field}) is not supported on a local "
                                "destination")
    check_model_identity(dest, parsed.get("model"))
    return parsed


def check_model_identity(dest, model) -> None:
    """§4.11 decision 2: a local request names exactly the served model — refused, not remapped."""
    if model != dest["served_name"]:
        raise AdapterRefusal(403, DESTINATION_POLICY_DENIAL,
                             f"model {model!r} is not endpoint '{dest['endpoint_id']}' served "
                             f"model {dest['served_name']!r}; models are never remapped")


def strip_and_sign_headers(headers, dest, environ=None) -> dict:
    """Credential scope per request (spec Req. 4, 13): drop host, every inbound auth header and
    any inbound correlation header, then sign from the entry's secret_reference only."""
    env = os.environ if environ is None else environ
    dropped = {"host", CORRELATION_HEADER.lower(), *INBOUND_AUTH_HEADERS}
    out = {k: v for k, v in headers.items() if k.lower() not in dropped}
    secret = env.get(dest["secret_reference"])
    if not secret:
        raise AdapterRefusal(403, DESTINATION_POLICY_DENIAL,
                             f"{dest['secret_reference']} (secret_reference of endpoint "
                             f"'{dest['endpoint_id']}') is not set")
    out[LOCAL_AUTH_HEADER] = secret
    return out


def build_correlation(dest, run) -> tuple[str, dict]:
    """§4.4's envelope from read_current_run()'s pointer fields: (header value, envelope)."""
    envelope = {
        "run_id": run["run_id"],
        "issue": run["issue_number"],
        "phase": run["stage"],
        "role": LOCAL_ROLE,
        "endpoint_id": dest["endpoint_id"],
        "request_id": str(uuid.uuid4()),
    }
    return json.dumps(envelope, separators=(",", ":")), envelope


def resolve_timeout(dest) -> dict:
    """§4.6 bounds. connect/total are aiohttp.ClientTimeout fields; time_to_first_token and
    idle share one rolling deadline in model_proxy — aiohttp's sock_read also runs before the
    first byte, so it cannot carry idle without capping time_to_first_token at idle."""
    t = dest["timeouts"]
    return {
        "connect": t["connect_seconds"],
        "time_to_first_token": t["time_to_first_token_seconds"],
        "idle": t["idle_seconds"],
        "total": t["total_seconds"],
    }

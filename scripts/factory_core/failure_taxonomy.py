"""Failure-spend taxonomy (#238): one declared policy record per failure class.

Declares and records; enforces nothing. breaker.py, error_signature.py and scheduler.sh
read none of it (tests/test_failure_taxonomy.py::test_nothing_reads_the_policy_fields).
`enforced` says whether today's code already behaves as a row declares; `enforced_today`
names what actually governs that failure now. Enforcement is follow-up work with its own
reviewed spec (hybrid-execution contract §4.10). Spec:
docs/superpowers/specs/2026-11-27-failure-spend-taxonomy-design.md.

Imports nothing from .providers: the default cloud path must not load model_adapter by
importing this module. model_adapter re-exports its failure-class constants from here.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional

STATE_TRANSITIONS = ("continue", "trip_to_blocked", "abstain_escalate")
DETECTORS = ("error_signature", "model_adapter", "none_yet")


@dataclass(frozen=True)
class FailureClass:
    retryable: bool                 # may be redispatched on the SAME destination
    cross_endpoint_retry: bool      # always False in #238 (contract §4.3)
    max_attempts: Optional[int]     # None = the phase's existing flat ceiling
    fallback_model_allowed: bool
    fallback_phase_allowed: bool
    requires_human: bool
    state_transition: str           # one of STATE_TRANSITIONS
    token_budget_multiplier: Optional[float]  # None = no value declared; never 0.0
    detector: str                   # one of DETECTORS
    enforced: bool                  # today's code already produces this whole record
    enforced_today: str             # what governs this failure today (repo-relative file:line) or "none"


def _row(*, retryable, max_attempts, requires_human, state_transition, multiplier, detector,
         enforced_today, enforced=False):
    # No class in #238 authorizes a different destination or a fallback, so those three
    # fields are fixed here rather than declared per row.
    return FailureClass(
        retryable=retryable, cross_endpoint_retry=False, max_attempts=max_attempts,
        fallback_model_allowed=False, fallback_phase_allowed=False,
        requires_human=requires_human, state_transition=state_transition,
        token_budget_multiplier=multiplier, detector=detector,
        enforced=enforced, enforced_today=enforced_today,
    )


_BREAKER = "scripts/factory_core/breaker.py"
_CEILING = f"{_BREAKER}:168 evaluate_stop_condition (flat ceiling)"
_CEILING_AND_STUCK = f"{_CEILING} + {_BREAKER}:330 record_failure_signature (#373 stuck)"

# Contract §4.6's local-endpoint classes, persisted verbatim in the request ledger's
# failure_class column. Values and FAILURE_CLASSES' order are load-bearing:
# preview_helper.resolve_outcome and run_record._compute_model_dispatch iterate it.
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
TIMEOUT_CONNECT = "timeout_connect"
TIMEOUT_TIME_TO_FIRST_TOKEN = "timeout_time_to_first_token"
TIMEOUT_IDLE = "timeout_idle"
TIMEOUT_TOTAL = "timeout_total"
CANCELLATION = "cancellation"
PROTOCOL_INCOMPATIBILITY = "protocol_incompatibility"
# Startup-only: evidence problems fail load_verified_entry(), never a request (#558 spec Req. 14).
CAPABILITY_EVIDENCE_INVALID = "capability_evidence_invalid"
DESTINATION_POLICY_DENIAL = "destination_policy_denial"
FAILURE_CLASSES = (
    ENDPOINT_UNREACHABLE, TIMEOUT_CONNECT, TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_IDLE,
    TIMEOUT_TOTAL, CANCELLATION, PROTOCOL_INCOMPATIBILITY, CAPABILITY_EVIDENCE_INVALID,
    DESTINATION_POLICY_DENIAL,
)


def _local(**kw):
    kw.setdefault("requires_human", True)
    kw.setdefault("state_transition", "abstain_escalate")
    kw.setdefault("multiplier", 1.0)
    kw.setdefault("enforced_today", _CEILING_AND_STUCK)
    return _row(retryable=False, max_attempts=0, detector="model_adapter", **kw)


# Spec §2a. The real stop is the phase's flat ceiling plus #373 stuck detection; nothing
# reads max_attempts/fallback_*/the multiplier, so every row is enforced=False.
_LOCAL_ROWS = {
    ENDPOINT_UNREACHABLE: _local(),
    TIMEOUT_CONNECT: _local(),
    TIMEOUT_TIME_TO_FIRST_TOKEN: _local(),
    TIMEOUT_IDLE: _local(),
    TIMEOUT_TOTAL: _local(),
    CANCELLATION: _local(requires_human=False, state_transition="continue", multiplier=None,
                         enforced_today=f"{_CEILING} (no special case)"),
    PROTOCOL_INCOMPATIBILITY: _local(),
    CAPABILITY_EVIDENCE_INVALID: _local(enforced_today=(
        "scripts/factory_core/providers/model_endpoint.py:747 load_verified_entry() fails the "
        f"proxy at startup; the run then fails like any other and is counted by {_CEILING}")),
    DESTINATION_POLICY_DENIAL: _local(),
}


def _merge(*groups):
    merged = {}
    for group in groups:
        for key, value in group.items():
            if key in merged:
                raise ValueError(f"failure class declared twice: {key}")
            merged[key] = value
    return MappingProxyType(merged)


# The single source of truth: no other module redeclares these fields.
FAILURE_TAXONOMY = _merge(_LOCAL_ROWS)

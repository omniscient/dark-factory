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


def _native(*, retryable=True, max_attempts=None, requires_human=False,
            state_transition="continue", multiplier=1.0, detector="error_signature",
            enforced_today=_CEILING_AND_STUCK, enforced=False):
    return _row(retryable=retryable, max_attempts=max_attempts, requires_human=requires_human,
                state_transition=state_transition, multiplier=multiplier, detector=detector,
                enforced_today=enforced_today, enforced=enforced)


# Spec §2b: classes error_signature.classify() produces (its strings stay byte-identical).
# build_failure / verdict_missing / test_failure keep the classifier's own names (not the
# issue's adapter_contract_error / output_truncated / test_timeout). model_timeout,
# tool_timeout and github_rate_limit are the issue's names and have no detector yet.
_NATIVE_ROWS = {
    "delivery_failure": _native(multiplier=0.5, enforced_today=(
        "scheduler.sh:579-596 retry_or_skip_delivery_failure: not counted against the retry "
        "key; a shadow <key>:delivery counter trips at the phase ceiling")),
    "model_rate_limit": _native(multiplier=0.5, enforced_today=f"{_CEILING} only"),
    "preview_infra": _native(multiplier=0.5, enforced_today=f"{_CEILING} only"),
    "session_window_pause": _native(multiplier=None, enforced=True, enforced_today=(
        "scheduler.sh:551-560 rollback_paused_retry un-counts the paused attempt, and the "
        "session-window-paused sentinel holds dispatch until its resume time")),
    "verdict_missing": _native(),
    "scope_spillover": _native(retryable=False, max_attempts=0, enforced_today=(
        f"{_CEILING_AND_STUCK}: the scheduler has no case for this signature and redispatches "
        "it like any other substantive failure; scripts/oos_excise.sh excises files inside a "
        "run, it does not stop a retry")),
    "build_failure": _native(),
    "test_failure": _native(),
    "substantive_unknown": _native(),
    "model_timeout": _native(retryable=False, max_attempts=0, requires_human=True,
                             state_transition="abstain_escalate", detector="none_yet",
                             enforced_today="none"),
    "tool_timeout": _native(retryable=False, max_attempts=0, requires_human=True,
                            state_transition="abstain_escalate", detector="none_yet",
                            enforced_today="none"),
    "github_rate_limit": _native(multiplier=0.5, detector="none_yet", enforced_today=(
        f"{_CEILING} only, as for model_rate_limit: both sign environmental:rate_limit today")),
}

def _tag(*, requires_human, state_transition, enforced_today):
    # Neutral placeholders, not statements about the gates (spec §2c): every row is
    # enforced=False because the gate owns its own halt and was not matched to this record.
    return _row(retryable=False, max_attempts=None, requires_human=requires_human,
                state_transition=state_transition, multiplier=None, detector="none_yet",
                enforced_today=enforced_today)


# Spec §2c: tagging-only names for failures a gate or sentinel already handles.
_TAG_ROWS = {
    "ci_red": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "scheduler.sh:1114-1124 stage_ci_gate moves the ticket to Blocked; it is then retried "
        f"up to {_CEILING}")),
    "main_red": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "the main-is-red sentinel pauses implementation dispatch; main_red_recheck_check "
        "clears it; main_red_fixer_check is off by default (scheduler.sh:188)")),
    "merge_conflict": _tag(requires_human=True, state_transition="trip_to_blocked",
                           enforced_today=(
        "scripts/factory_core/deconflict.py tiers 1-3 own their escalation; the declared halt "
        "was not verified against them")),
    "context_overflow": _tag(requires_human=False, state_transition="continue",
                             enforced_today="scripts/budget_enforce.py (a warning, no halt)"),
    "budget_exceeded": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "scripts/budget_gate.sh in enforce mode writes context caps to token-opt-caps.env; it "
        "does not stop the run")),
    "conformance_block": _tag(requires_human=False, state_transition="continue",
                              enforced_today=(
        "the conformance/docs-conformance reconcile loop owns STATUS: BLOCKED; not verified "
        "against the code")),
    "security_block": _tag(requires_human=False, state_transition="continue", enforced_today=(
        "the code-review gate owns a SEVERITY: critical finding; not verified against the code")),
    "missing_secret": _tag(requires_human=True, state_transition="trip_to_blocked",
                           enforced_today=(
        "preflight fails the run at startup; the run then takes the ordinary failure path "
        "(typically environmental:delivery_failure), not a direct trip")),
}

# Spec §4: preview_helper.py's two closed enums (#642), verbatim and in order. It is not
# imported here; tests/test_failure_taxonomy.py pins these tuples against it.
PREVIEW_ENTRY_FAILURE_CLASSES = ("preflight_refused", "missing_input", "input_too_large",
                                 "local_timeout_total", "model_call_failed", "schema_invalid")
PREVIEW_SCHEDULER_FAILURE_CLASSES = (
    "registry_stage_failed", "endpoint_not_resolved", "proxy_start_failed", "dispatch_failed",
    "wall_clock_exceeded", "kill_switch", "helper_result_missing", "helper_result_invalid",
    "ledger_row_missing", "ledger_unavailable", "ledger_identity_mismatch",
    "unclassified_failure",
)
# Restates #642's design: every helper failure abstains, is never retried for the same head
# SHA and never retried on cloud. #587's lane enforces it and must update this record.
_PREVIEW_HELPER_ROW = _row(
    retryable=False, max_attempts=0, requires_human=False, state_transition="abstain_escalate",
    multiplier=None, detector="none_yet",
    enforced_today="none: preview_helper is not dispatched on main (#642); #587's lane will "
                   "own its own stop")
_PREVIEW_ROWS = {c: _PREVIEW_HELPER_ROW
                 for c in PREVIEW_ENTRY_FAILURE_CLASSES + PREVIEW_SCHEDULER_FAILURE_CLASSES}

# Spec §2d: what the breaker decides across attempts, never what failed on one attempt.
# Not FAILURE_TAXONOMY keys and never a SIGNATURE_TO_CLASS value; for tagging a run's
# terminal outcome only. "Repeated identical failures" is detected at phase-attempt
# granularity (breaker.py:330, #373), not per tool call.
BREAKER_OUTCOME_REPEAT_LOOP = "repeat_tool_call_loop"
BREAKER_OUTCOME_HUMAN_REQUIRED = "human_required"  # trip_to_blocked() adds needs-discussion

DEFAULT_CLASS = "substantive_unknown"
# Keyed on a signature's first two colon-separated fields: classify() appends :<exit_code>
# to oos_files, build_failure, test_failure, unknown and endpoint_unreachable.
SIGNATURE_TO_CLASS = MappingProxyType({
    "environmental:delivery_failure": "delivery_failure",
    "environmental:rate_limit": "model_rate_limit",
    "environmental:preview_infra": "preview_infra",
    "environmental:session_window_pause": "session_window_pause",
    "substantive:verdict_missing": "verdict_missing",
    "substantive:oos_files": "scope_spillover",
    "substantive:build_failure": "build_failure",
    "substantive:test_failure": "test_failure",
    "substantive:unknown": DEFAULT_CLASS,
    "substantive:endpoint_unreachable": ENDPOINT_UNREACHABLE,
})


def class_for_signature(sig: str) -> str:
    """The FAILURE_TAXONOMY key for an error_signature string; DEFAULT_CLASS if unmapped."""
    if not isinstance(sig, str):
        return DEFAULT_CLASS
    parts = sig.split(":")
    key = ":".join(parts[:2]) if len(parts) >= 2 else sig
    return SIGNATURE_TO_CLASS.get(key, DEFAULT_CLASS)


def _merge(*groups):
    merged = {}
    for group in groups:
        for key, value in group.items():
            if key in merged:
                raise ValueError(f"failure class declared twice: {key}")
            merged[key] = value
    return MappingProxyType(merged)


# The single source of truth: no other module redeclares these fields.
FAILURE_TAXONOMY = _merge(_LOCAL_ROWS, _NATIVE_ROWS, _TAG_ROWS, _PREVIEW_ROWS)

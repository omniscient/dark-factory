# Failure-Spend Taxonomy and Retry/Fallback Governance

**Issue:** #238

## Rework note (2026-11-27, second pass)

This revision replaces the version committed at `6684d20`/`81b2aff`, which an operator
review (`issuecomment-7227703914`) rejected for three reasons verified against the live
`main` checkout: (1) the recording design couldn't actually populate `failure_spend.by_class`
because of an assemble/signature write-ordering bug the old spec didn't account for, (2) the
signature→class mapping table used literal full-string keys that never match what
`classify()` actually returns, and (3) the table stated retry/fallback policy stricter than
anything the code enforces today, without saying so. All three are fixed below. The branch
was also merged with `main` to pick up #642 (`scripts/factory_core/preview_helper.py`,
merge commit `d459e67`), which the prior pass predated.

**Review amendment (2026-11-27, after the second pass).** One finding of that operator
review was wrong and the second pass built on it: the workflow loop's failure branch
(`entrypoint.sh:1262-1265`) is not a path that lacks a run record. It is never reached on a
workflow failure. Measured in the factory image with the entrypoint's shell options and
trap: the `ERR` trap fires on the failed `archon … | tee` pipeline even under `set +e`,
`on_failure()` runs, and because the handler itself re-enables errexit (`set +e` … `set -e`
around `archon workflow cost`, `entrypoint.sh:733-736`, taken whenever `ARTIFACTS_DIR` is
set, which it always is from `:179`) the shell exits with archon's code as soon as the
handler returns. The comment at `entrypoint.sh:702-705` says the same. So every failed run
already gets its record from `on_failure` (`:737`), and an assemble call added to the loop
branch would be dead code. The 25 of 85 failed run ids without a retained record have
another cause, not established here. Requirement 3, §5a, the file list and the Test Plan
are corrected accordingly. This amendment also stops §5a from moving the signature write
(it would change the classifier's `elapsed_seconds` input and so the breaker's behaviour),
and corrects several `enforced` / `enforced_today` cells.

**Second-read amendment (2026-11-27).** Re-measured independently (real `on_failure`,
`_write_error_signature` and `_cli_with_hosting_fallback` cut from `entrypoint.sh` at
`d459e67`, stub `python3`/`archon`, the entrypoint's shell options and trap, factory
image). The design holds: exit code, order and argv of every existing call, and the
`elapsed_seconds` the classifier receives are unchanged. Seven corrections:

1. "Never reached" is true only for a run that is neither paused nor held. When
   `on_failure` handles a session-window pause it `return`s before its `set -e`
   (`:672-700`), so execution resumes in the loop, `EXIT_CODE` is non-zero and the loop's
   branch **is** entered: `_handle_session_window_pause` runs a second time (`:1214`) and
   exits 0. `:1262-1265` themselves are reached only if that second check disagrees with
   the first. A blast hold exits 0 inside the trap (`:710-713`). Neither path writes a
   failure signature, so neither is annotated. See "Coverage" in §5a.
2. §5a placed the annotate call before `run_post_mortem`, the board move and the failure
   comment, unbounded. Measured with a verb that hangs: the board move and the comment are
   never made. It now runs once, after them, under `timeout`.
3. §5b did not say what is written to the durable copy. Read literally (the out-file's
   content is written to both paths), an out-file that is a symlink to another run's record
   replaces this run's durable record with that run's (measured). §5b now annotates each
   file from its own content and skips anything that is not a regular file for this run id.
4. `SIGNATURE_TO_CLASS` needs ten entries, not nine: requirement 2 and test 2 require
   `substantive:endpoint_unreachable` → `endpoint_unreachable`, and §2b's count left it out.
5. The six `enforced: true` cells of §2c are not true and are now `false` (evidence in
   §2c). `session_window_pause` is the only enforced row.
6. Nothing pinned "no code reads the policy fields". Test 3d does.
7. `backfill-economics` recomputes `harness_economics` and drops the annotated class
   (measured); §5c says so.

## Overview / Problem Statement

Dark Factory already has three independent pieces of failure-handling machinery, none of
which share a vocabulary:

1. `scripts/factory_core/breaker.py` — a flat per-phase retry ceiling
   (`evaluate_stop_condition`, `breaker.py:145-177`; `count >= ceiling` at `:168` is the
   *entire* stop rule — it reads no failure class), plus `trip_to_blocked`
   (`breaker.py:334+`) and a "two consecutive identical substantive failures" early-trip
   rule (`record_failure_signature`, `breaker.py:288-331`, #373).
2. `scripts/factory_core/error_signature.py` — `classify()` (`:80-126`) turns one failed
   run's captured output into a stable `"environmental:<kind>"` /
   `"substantive:<kind>[:exit_code]"` string, used only to feed rule 1's "stuck" comparison.
   The classification is never persisted anywhere durable on its own — it lives only as a
   `<issue>[:phase]:sig` entry in `scheduler-state.json`, overwritten on the next attempt.
3. `scripts/factory_core/providers/model_adapter.py` — nine **interim** local-endpoint
   failure-class constants (`:34-48`), shipped by the accepted hybrid-execution contract
   (#556) with an explicit note: *"#238's names supersede these when it ships."* These are
   persisted per-request in the model-proxy request ledger's `failure_class` column
   (`model_proxy.py:150`/`:161`) and read back by `run_record.py::_compute_model_dispatch`
   (`:874-903`) into `model_dispatch.result.failure_classes_seen` — a separate code path
   from error_signature/breaker, with no retry/fallback policy of its own.

None of the three declares *policy* (retryable? how many times? ever a fallback model or
phase? requires a human?), and failure-spend (tokens/cost burned on failed attempts) is only
recorded in aggregate (`run_record.py`'s `harness_economics.failure_spend`), with no
breakdown by class.

This spec adds one declarative, additive taxonomy layer that both existing classification
sources map onto, and extends the ledger with a per-class breakdown — without changing
`breaker.py`'s retry-ceiling arithmetic or `scheduler.sh`'s dispatch logic. It deliberately
does **not** claim the resulting table is already enforced: a new `enforced` column records,
per class, whether today's code actually produces the declared behavior, and where it
doesn't, the gap is stated rather than hidden. Per contract §4.1: *"this contract does not
duplicate #238's taxonomy, only the invariants it must satisfy."* Those invariants (§4.3,
§4.6, §4.10) are load-bearing constraints on this spec.

`Depends on: #556` — **CLOSED** (contract accepted, 2026-11-22). Unblocked.

## Requirements (from Q&A + operator rework)

The four Q&A resolutions from the first pass still hold (scope: declarative taxonomy +
classification/recording only, no breaker/scheduler behavior change; taxonomy keys are the
issue's snake_case names with existing classifier strings staying byte-identical;
`repeat_tool_call_loop` maps onto the existing #373 mechanism at phase-attempt granularity;
Proactive Memory Agent classes out of scope). The operator rework adds five corrections,
each now load-bearing:

1. **The table must say what's enforced, not just what's declared.** Every taxonomy row
   gets an `enforced: bool` and an `enforced_today` file:line citation. `enforced: true`
   only where the row's *full* declared policy already matches live code: today that is
   `session_window_pause` alone. Every retry/fallback/multiplier-bearing field on every
   other row is `enforced: false` — nothing in this ticket's module is read by
   `breaker.py` or `scheduler.sh`, so no row's `max_attempts`/`fallback_*`/
   `token_budget_multiplier` governs anything yet. `retryable` is redefined precisely as
   "may be redispatched on the same destination" (never a cross-endpoint claim), and every
   row additionally declares `cross_endpoint_retry: false` — no class in this ticket
   authorizes retrying on a *different* destination (that's a fallback, a separate,
   today-unenforced field).
2. **The mapping function keys on the first two colon-separated fields of a signature, not
   the full string.** `error_signature.classify()` appends `:<exit_code>` to four of its
   return values (`substantive:oos_files:<n>`, `substantive:build_failure:<n>`,
   `substantive:test_failure:<n>`, `substantive:unknown:<n>` — `error_signature.py:120-126`)
   and the local-rate-limit path returns `substantive:endpoint_unreachable:<n>`
   (`error_signature.py:95-101`, #586). A literal-string lookup table, as the first pass
   had, never matches any of these. `class_for_signature(sig)` splits on `:`, joins the
   first two fields back together, and looks that up — `substantive:endpoint_unreachable`
   resolves correctly to the `endpoint_unreachable` class (declared `retryable: false`)
   instead of silently falling through to the retryable default.
3. **Recording must actually produce `failure_spend.by_class`, without changing what the
   classifier sees.** `classify()` runs in-container
   (`entrypoint.sh::_write_error_signature`, `:528-597`). In `on_failure()`
   (`entrypoint.sh:669-814`, the ERR trap), `run-record assemble` runs at `:737`,
   *before* `_write_error_signature` runs at `:767` (refine/plan/deconflict branch) or
   `:785` (every other intent), so the assembled record cannot carry the signature.
   `on_failure` is the only failure path that matters: a workflow failure that is neither
   a pause nor a blast hold never reaches the loop's own branch at `:1262-1265` (see the
   two amendments above). §5 therefore leaves
   both calls where they are and adds one step **after** the signature write that
   annotates the record already assembled. The signature write is not moved earlier:
   `_write_error_signature` computes `elapsed_seconds` at the moment it runs, and
   `classify()` returns `environmental:delivery_failure` below 30 s
   (`error_signature.py:104-110`). Running it before `archon workflow cost` and `assemble`
   (measured: about 1.3 s with no run to report, more with one) would turn a failure at,
   say, 29 s that is signed `substantive:*` today into a `delivery_failure`, which the
   scheduler redispatches without counting (`scheduler.sh:579-596`) and which never trips
   the two-identical-signatures stop. That is a breaker behaviour change and is not made.
4. **Declared values must not read stricter than enforced behavior.** The first pass's
   `max_attempts: 0` for every local-endpoint class claimed zero retries; in reality a
   failed local-pinned phase is redispatched (up to the phase's flat ceiling) until a second
   consecutive identical `substantive:` signature trips `record_failure_signature`'s early
   stop (`breaker.py:330`) — i.e. at least one retry happens before any early trip, and the
   flat ceiling (not a class-specific zero) is what eventually stops it regardless. Per
   requirement 1, this is now explicit as `enforced: false` with the real mechanism named in
   `enforced_today`, not corrected by inventing enforcement this ticket doesn't add.
5. **Use the issue's own names only where no better-fitting name already exists; never
   relabel a live classifier string.** The issue proposed `adapter_contract_error`,
   `output_truncated`, and `test_timeout` as class names. `error_signature.classify()`
   already has live, tested classes that cover the same ground —
   `substantive:build_failure`, `substantive:verdict_missing`, `substantive:test_failure` —
   and `record_failure_signature`'s stuck-detection and `tests/test_factory_core_error_signature.py`
   pin those exact strings. Renaming them in the taxonomy (even as an alias) would be exactly
   the relabeling the operator flagged. This spec keeps `build_failure`/`verdict_missing`/
   `test_failure` as the canonical keys and does **not** declare
   `adapter_contract_error`/`output_truncated`/`test_timeout` as separate classes — they
   would duplicate an existing, better-named class for no gain. `model_timeout` and
   `tool_timeout`, by contrast, have no existing classifier under any name (there is no
   signature or ledger field for "a model call timed out" distinct from the granular local
   `timeout_*` family, and no mechanism anywhere classifies a single tool invocation's own
   timeout) — these are declared verbatim, as no-live-detector rows, per the operator's
   explicit instruction to use the issue's names for these two.

## Architecture / Approach

### 1. New module: `scripts/factory_core/failure_taxonomy.py`

Lives in `factory_core/`, not `factory_core/providers/`, and **imports nothing from
`.providers`** — the default cloud path must not load `model_adapter`/`model_endpoint` by
importing this module (mirrors `error_signature.py`'s existing deferred import at
`error_signature.py:100`, which this spec does not change). `model_adapter.py` imports
*from* `failure_taxonomy`, never the reverse.

```python
@dataclass(frozen=True)
class FailureClass:
    retryable: bool                    # may be redispatched on the SAME destination
    cross_endpoint_retry: bool         # always False in this ticket (contract §4.3) —
                                        # no class here authorizes a different-destination retry
    max_attempts: Optional[int]        # None = defer to the phase's existing flat ceiling
    fallback_model_allowed: bool
    fallback_phase_allowed: bool
    requires_human: bool
    state_transition: str              # "continue" | "trip_to_blocked" | "abstain_escalate"
    token_budget_multiplier: Optional[float]  # None where no meaningful default exists yet
    detector: str                      # "error_signature" | "model_adapter" | "none_yet"
    enforced: bool                     # does today's code already produce this row's full
                                        # declared policy, unmodified by this ticket?
    enforced_today: str                # file:line (or "none") of whatever mechanism actually
                                        # governs this failure today, independent of this table
```

`FAILURE_TAXONOMY: dict[str, FailureClass]` is the single source of truth; no other module
redeclares these fields. `human_required` and `repeat_tool_call_loop` (from the issue's list)
are **not** keys in this dict — see §2d.

### 2a. Local-endpoint classes (verbatim keys from `model_adapter.py`, order preserved)

None are cross-endpoint-retryable (contract §4.6); `enforced: false` on every row because
nothing reads `max_attempts`/`fallback_*`/the multiplier today — the real stop/continue
behavior is the phase's flat ceiling plus #373 stuck-detection, named in `enforced_today`.

| Class | retryable | cross_endpoint_retry | max_attempts | fallback_model | fallback_phase | requires_human | state_transition | multiplier | detector | enforced | enforced_today |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `endpoint_unreachable` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` (flat ceiling) + `:330` (#373 stuck) |
| `timeout_connect` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |
| `timeout_time_to_first_token` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |
| `timeout_idle` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |
| `timeout_total` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |
| `cancellation` | false | false | 0 | false | false | false | continue | None | model_adapter | false | `breaker.py:168` (no special case) |
| `protocol_incompatibility` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |
| `capability_evidence_invalid` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | fails `load_verified_entry()` at proxy startup; the run then fails like any other and is counted by `breaker.py:168` |
| `destination_policy_denial` | false | false | 0 | false | false | true | abstain_escalate | 1.0 | model_adapter | false | `breaker.py:168` + `:330` |

A multiplier of `None` means "no value declared". No row declares `0.0`: read as a budget
factor by a later enforcement ticket it would mean a zero budget.

Note: of these nine, only `endpoint_unreachable` has a live path *into* `error_signature`
today — `classify()`'s local-rate-limit branch (`error_signature.py:95-101`) is the only
local failure shape with its own regex; the other eight, lacking a distinguishing text
pattern, fall through to `substantive:unknown` — or, when the run failed within 30 s with
no commit, clean tree and no artifact, to `environmental:delivery_failure`
(`error_signature.py:104-110`), which is redispatched uncounted up to the ceiling
(`scheduler.sh:579-596`) — and are tracked today only via the ledger's
per-request `failure_class` column (`model_dispatch.result.failure_classes_seen`), not via
the breaker's signature comparison. This is a pre-existing gap, not introduced here; it is
named explicitly so implementation doesn't assume a signature-level detector exists for all
nine.

### 2b. Factory-native classes (mapped from `error_signature.classify()`)

| Class | Maps from (first two colon-fields) | retryable | cross_endpoint_retry | max_attempts | fallback_model | fallback_phase | requires_human | state_transition | multiplier | detector | enforced | enforced_today |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `delivery_failure` | `environmental:delivery_failure` | true | false | None | false | false | false | continue | 0.5 | error_signature | false | `scheduler.sh:579-596`: not counted against the retry key; a shadow counter `<key>:delivery` trips at the phase ceiling |
| `model_rate_limit` | `environmental:rate_limit` | true | false | None (flat ceiling) | false | false | false | continue | 0.5 | error_signature | false | `breaker.py:168` only |
| `preview_infra` | `environmental:preview_infra` | true | false | None (flat ceiling) | false | false | false | continue | 0.5 | error_signature | false | `breaker.py:168` only |
| `session_window_pause` | `environmental:session_window_pause` | true | false | None | false | false | false | continue | None | error_signature | true | `scheduler.sh:551-560` (`rollback_paused_retry`: the paused attempt is un-counted) and the `session-window-paused` sentinel, which holds dispatch until its resume time |
| `verdict_missing` | `substantive:verdict_missing` | true | false | None (flat ceiling) | false | false | false | continue | 1.0 | error_signature | false | `breaker.py:168` + `:330` |
| `scope_spillover` | `substantive:oos_files` | false | false | 0 | false | false | false | continue | 1.0 | error_signature | false | `breaker.py:168` + `:330`: the scheduler has no case for this signature and redispatches it like any other substantive failure. `scripts/oos_excise.sh` excises files inside a run; it does not stop a retry |
| `build_failure` | `substantive:build_failure` | true | false | None (flat ceiling) | false | false | false | continue | 1.0 | error_signature | false | `breaker.py:168` + `:330` |
| `test_failure` | `substantive:test_failure` | true | false | None (flat ceiling) | false | false | false | continue | 1.0 | error_signature | false | `breaker.py:168` + `:330` |
| `substantive_unknown` | `substantive:unknown` (and any signature absent from `SIGNATURE_TO_CLASS`) | true | false | None (flat ceiling) | false | false | false | continue | 1.0 | error_signature | false | `breaker.py:168` + `:330` |
| `model_timeout` | *(none — issue name, no live detector)* | false | false | 0 | false | false | true | abstain_escalate | 1.0 | none_yet | false | none |
| `tool_timeout` | *(none — issue name, no live detector)* | false | false | 0 | false | false | true | abstain_escalate | 1.0 | none_yet | false | none |
| `github_rate_limit` | *(none — shares `environmental:rate_limit`'s regex with `model_rate_limit`; see Open Questions)* | true | false | None (flat ceiling) | false | false | false | continue | 0.5 | none_yet | false | `breaker.py:168` only, same as `model_rate_limit`, since the two are indistinguishable today |

In the dataclass a `max_attempts` cell reading "None (flat ceiling)" is the literal `None`.
Every `fallback_model_allowed` and `fallback_phase_allowed` in this ticket is `False`: no
fallback is designed, so none is declared as permitted.

`class_for_signature(sig: str) -> str` implementation:

```python
def class_for_signature(sig: str) -> str:
    parts = sig.split(":")
    key = ":".join(parts[:2]) if len(parts) >= 2 else sig
    return SIGNATURE_TO_CLASS.get(key, "substantive_unknown")
```

`SIGNATURE_TO_CLASS` has exactly ten entries: one per row above that has a "Maps from"
value (nine), plus `"substantive:endpoint_unreachable": "endpoint_unreachable"` for the
signature `classify()` returns on a verified-local run (`error_signature.py:95-101`), whose
class is the §2a row. `github_rate_limit`/`model_timeout`/`tool_timeout` have no entry and
are never produced by `class_for_signature`. `error_signature.py` is **not modified**.

### 2c. No-live-detector tagging-only classes (gates/sentinels that already own their halt)

Tagging-only rows. Their other fields take the neutral values `retryable: False`,
`cross_endpoint_retry: False`, `max_attempts: None`, both fallbacks `False`,
`token_budget_multiplier: None`, `detector: "none_yet"`. Those neutral values are
placeholders, not statements about the gates: every one of the eight rows is
`enforced: false`. A row is `enforced: true` only when its whole record matches the code,
and these do not. `ci_red` carries `retryable: False` and `requires_human: False`, yet
`stage_ci_gate` moves the ticket to Blocked with the comment "The factory will retry
automatically" (`scheduler.sh:1114-1124`), the Blocked retry loop redispatches it and the
flat ceiling ends in `trip_to_blocked` with `needs-discussion`. `main_red` needs a human
unless a recheck clears it: the fixer is off by default
(`MAIN_RED_AUTOFIX_ENABLED:-false`, `scheduler.sh:188`). `budget_gate.sh` does not block:
it exits 0 when over budget and every caller appends `|| true` (`budget_gate.sh:21-25`,
`workflows/archon-dark-factory.yaml:425`). The conformance and code-review rows were not
checked against the code at all.

| Class | requires_human | state_transition | enforced | enforced_today |
|---|---|---|---|---|
| `ci_red` | false | continue | false | `scheduler.sh::stage_ci_gate` moves the ticket to Blocked; it is then retried up to the flat ceiling (`breaker.py:168`) |
| `main_red` | false | continue | false | `${SCHEDULER_STATE_DIR}/main-is-red` sentinel pauses implementation dispatch; `main_red_recheck_check` clears it, `main_red_fixer_check` is off by default |
| `merge_conflict` | true | trip_to_blocked | false | `scripts/factory_core/deconflict.py` tiers 1–3 own their escalation; the declared halt was not verified against them |
| `context_overflow` | false | continue | false | `scripts/budget_enforce.py` (a warning, no halt) |
| `budget_exceeded` | false | continue | false | `scripts/budget_gate.sh` in enforce mode writes context caps to `token-opt-caps.env`; it does not stop the run |
| `conformance_block` | false | continue | false | the conformance/docs-conformance reconcile loop owns `STATUS: BLOCKED`; not verified against the code |
| `security_block` | false | continue | false | the code-review gate owns a `SEVERITY: critical` finding; not verified against the code |
| `missing_secret` | true | trip_to_blocked | false | preflight fails the run at startup; the run then takes the ordinary failure path (typically signed `environmental:delivery_failure`), not a direct trip |

Declaring these lets future ledger rows/reports tag failure-spend with a consistent name; it
is not a claim that this ticket changes any of those gates' behavior.

### 2d. Breaker outcomes — not `FAILURE_TAXONOMY` keys

Per the operator rework: `human_required` and `repeat_tool_call_loop` are **not** mapped
classes. Neither `classify()` nor any model-adapter row ever produces either string — they
describe what the breaker *decides* across attempts, not what failed on one attempt.
`repeat_tool_call_loop` names the outcome of `record_failure_signature`'s "stuck" detection
(`breaker.py:330`, #373) at phase-attempt granularity — two consecutive matching
`substantive:*` signatures → `trip_to_blocked()`. `human_required` names the general outcome
of `trip_to_blocked()` itself (`breaker.py:334+`, which always adds `needs-discussion`),
regardless of which class triggered it. `failure_taxonomy.py` documents both as named
constants (`BREAKER_OUTCOME_REPEAT_LOOP = "repeat_tool_call_loop"`,
`BREAKER_OUTCOME_HUMAN_REQUIRED = "human_required"`) in a separate, un-keyed section of the
module for ledger rows/reports that want to tag a *run's terminal outcome* — never used as a
lookup value in `SIGNATURE_TO_CLASS` or `model_adapter.FAILURE_CLASSES`, and never a dict key
in `FAILURE_TAXONOMY`. This spec is explicit that "repeated byte-identical failing tool calls
trip a visible breaker" is satisfied only at phase-attempt granularity (the existing #373
mechanism) and not at literal per-tool-call granularity — a finer detector remains follow-up
work (§ Out of scope).

### 3. `model_adapter.py` supersession

`model_adapter.py`'s nine constants become `from ..failure_taxonomy import ENDPOINT_UNREACHABLE,
...` re-exports — same string values, single owner of the *policy* fields. `FAILURE_CLASSES`
is re-exported the same way, **preserving its exact tuple order**
(`model_adapter.py:44-48`), since `preview_helper.py::resolve_outcome` (`:498`) and
`run_record.py::_compute_model_dispatch` iterate it positionally. The module's "#238's names
supersede these" docstring comment (`:33`) is updated to point at `failure_taxonomy.py`.

### 4. Preview-helper classes (#642, merged onto this branch)

`preview_helper.py::ENTRY_FAILURE_CLASSES` (`:58-59`, six values) and
`::SCHEDULER_FAILURE_CLASSES` (`:61-64`, twelve values) are the inert preview-helper's own
closed enums — "inert" per #642's own naming, meaning no scheduler dispatch path calls this
service yet. They are declared in `failure_taxonomy.py` as a fourth, explicitly-labeled
group, verbatim, preserving both tuples' string values and order, with one record for all
eighteen: `retryable: False`, `cross_endpoint_retry: False`, `max_attempts: 0`, both
fallbacks `False`, `requires_human: False`, `state_transition: "abstain_escalate"`,
`token_budget_multiplier: None`, `detector: "none_yet"`, `enforced: False`,
`enforced_today: "none: preview_helper is not dispatched on main (#642); #587's lane will
own its own stop"`. These values restate #642's design (every helper failure abstains, is
never retried for the same head SHA and never retried on cloud); the lane that enforces
them is #587, which must update `enforced` / `enforced_today` when it lands. A test asserts
that every member of both tuples is a key of `FAILURE_TAXONOMY`, importing
`preview_helper` in the test only. Neither tuple in `preview_helper.py` is modified.

### 5. Ledger extension: `run_record.py` and the recording-ordering fix

**5a. `entrypoint.sh` — annotate the assembled record after the signature write
(requirement 3).** One change, inside `on_failure()` only:

- `_write_error_signature` (`:528-597`) keeps its position, its arguments and its
  classification inputs. Its one change: it echoes the signature it wrote, or nothing. The
  `error-signature-write` verb already prints `signature=<sig>` after a successful write
  (`cli.py:214`); the function captures that line instead of letting it fall into the
  container log (cloud path) or `/dev/null` (local path, `:590`), strips the prefix and
  echoes the value. Every failure of the verb still ends in `|| true` and echoes nothing.
- `on_failure` declares `local SIG=""` next to `LEDGER_NOTE` (`:757`). The two call sites
  (`:767`, `:785`) become `SIG=$(_write_error_signature …) || true`, each followed by
  `[ -z "$SIG" ] || echo "signature=$SIG"` so the line the cloud path prints into the
  container log today is still there. Nothing else is inserted at those two places:
  `run_post_mortem`, `set_board_status` and `post_or_update_comment` follow exactly as
  today.
- One annotate call, after the `if [ -n "${ISSUE_NUM:-}" ] …` block closes and immediately
  before the final `post_cost_report || true`:

  ```bash
  if [ -n "$SIG" ] && [ -n "${ARTIFACTS_DIR:-}" ]; then
    timeout -k 5 20 python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record annotate-failure \
      --run-id "${RUN_ID:-unknown}" --out-file "$ARTIFACTS_DIR/run-record.json" \
      --failure-signature "$SIG" >/dev/null 2>&1 || true
  fi
  ```

  It is last and bounded because nothing new may stand between a failure and its board
  move and comment. Measured with a verb that never returns: placed right after the
  signature write and unbounded, the board move and the comment were never made; placed
  here, both were made and the shell exited with the workflow's code after 20 s. If a step
  before it ends the shell (the unguarded `FOOTER=$(… marker …)`, as today), the annotation
  is lost along with the cost report and nothing else changes.

- Nothing is passed to `run-record assemble`, whose argv is unchanged. The `assemble` and
  the signature write come from the same `cli.py` copy (the clone's, `TARGET-PATH`), but a
  target whose copy predates this ticket would reject an unknown `assemble` flag and lose
  the whole record; an unknown `annotate-failure` verb only loses the annotation.
- The loop branch at `:1262-1265` and the success-path assemble at `:1281` are not edited.

Properties, each with a test in the Test Plan: the container's exit code is unchanged
(both new commands end in `|| true` and run inside the trap, after which the shell exits
with the original status); the original failure is not masked (the failure comment and the
board move that follow are untouched); the signature is written exactly as often as today
(once per `on_failure`); with an unwritable state volume the verb prints nothing (measured:
exit 1, empty stdout), `SIG` is empty and the annotate step does not run.

**Coverage.** A signature class is recorded only where `on_failure` writes a signature
today. It is absent, by construction and without any error, for:

- a session-window pause (the trap returns at `:699`, the loop exits 0 at `:1214-1217`; the
  record is the `paused` one and the pause signature is written by its own verb, so the
  `session_window_pause` class never appears in `by_class`);
- a blast-radius hold (`exit 0` at `:712`);
- intents without an issue (`Recheck main`, `Fix main`, `Audit deps`) and `close`: the
  failed record is assembled, `_write_error_signature` is not called (`:761`), `by_class`
  holds ledger rows only;
- a failure before the clone exists (the clone's `cli.py` is missing, so neither the
  record nor the signature is written);
- the four `exit 1` refusals that never reach the trap (`:24`, `:70`, `:75`, `:260`).

`refine`, `plan`, `deconflict` (phase `resolve`), `fix` and `continue` are covered.

**5b. `run_record.py` gains the verb `run-record annotate-failure --run-id --out-file
--failure-signature`.** It reads the record at `--out-file`, sets
`harness_economics.failure_spend.by_class[<class>]` for the signature's class and rewrites
it. It then does the same, separately, for the durable copy
(`_durable_run_record_path(run_id)`): each file is read, annotated and rewritten from its
**own** content; the out-file's content is never written to the durable path. A file is
skipped unless it is a regular file (not a symlink), parses to a JSON object whose `run_id`
equals `--run-id`, and has a dict at `harness_economics.failure_spend`. Each rewrite goes
through `tempfile.mkstemp` in the same directory and `os.replace`; an `OSError` on one file
(read-only volume) removes the temporary file, prints one line to stderr and does not stop
the other file. The verb always exits 0. It appends no row to `runs.jsonl`, posts nothing
to Seq and changes no other key (`cmd_assemble` does both of the former,
`run_record.py:1216-1217`, which is why the record is not assembled a second time). The
`assemble` subparser is unchanged. `cli.py` needs no edit: its `run-record` sub-command
passes the remaining argv to `run_record.main()` (`cli.py:412-414`), so the new subparser
lives in `run_record.py` only.

**5c. `_compute_harness_economics`** (`run_record.py:824-865`) writes the ledger part at
assembly; `annotate-failure` adds the signature part afterwards:

```python
# in _compute_harness_economics, after failure_spend is built
by_class: dict = {}
if ledger_available:
    for row in rows:
        rc = row.get("failure_class")
        if not rc:
            continue
        entry = by_class.setdefault(rc, {"tokens": None, "basis": "ledger_rows"})
        entry["request_count"] = entry.get("request_count", 0) + 1
failure_spend["by_class"] = by_class

# in annotate-failure
cls = class_for_signature(failure_signature)        # from .failure_taxonomy
entry = fs.setdefault("by_class", {}).setdefault(cls, {})
entry.update({"tokens": fs.get("tokens"), "basis": fs.get("basis")})
```

The signature's class carries the run's own `failure_spend.tokens` and `basis` (not a
hard-coded `whole_run`). A `request_count` already counted for the same class from ledger
rows is kept.

- `by_class` is `{}` when neither a failure signature nor any ledger failure rows are
  present — the dict equivalent of `retry_spend`'s existing `None`-on-unavailable
  degradation, so `_build_issue_economics`'s backfill path (`:1228-1283`, which reads
  `harness_economics` as an opaque dict and only extracts `outcome_state`/`factory_cpm`/
  `cost_usd`) is unaffected.
- Each class's declared `token_budget_multiplier` is **not** written into `by_class` —
  the operator rework drops this (the first pass's "policy annotation alongside raw spend"
  idea invites exactly the "declared policy the code doesn't enforce" problem this rework
  exists to fix). A reader who wants a class's declared multiplier looks it up in
  `FAILURE_TAXONOMY` directly; `by_class` reports only observed spend.
- `run-record backfill-economics` recomputes `harness_economics` from the ledger
  (`run_record.py:1311`) and so drops the signature's class from the artifacts copy it
  rewrites (measured). That is its documented degrade-only behaviour and is not changed;
  the durable copy is not touched by a backfill.
- Bump `POLICY_VERSION` (`run_record.py:30`) from `"1.1"` to `"1.2"`.

### 6. Config / docs

No new `config/config.yaml` block — code-level data table, no operator tunable, matching
`error_signature.py`'s own regexes.

## Blast radius

Files touched:

- New: `scripts/factory_core/failure_taxonomy.py`, `tests/test_failure_taxonomy.py`,
  `tests/test_entrypoint_failure_signature.sh`
- Modified: `scripts/factory_core/providers/model_adapter.py` (constants → re-exports,
  comment only, no logic change), `scripts/factory_core/run_record.py` (`POLICY_VERSION`,
  the `by_class` lines in `_compute_harness_economics`, the `annotate-failure` verb),
  `entrypoint.sh` (`_write_error_signature`'s echo, and the signature line and the one annotate call in
  `on_failure`), `tests/test_run_record.py`, `.github/workflows/ci.yml` (one
  `- run: bash tests/test_entrypoint_failure_signature.sh` step; the file is not added to
  `CI_EXEMPT_SHELL_TESTS`)
- Not touched: `error_signature.py`, `breaker.py`, `scheduler.sh`, `preview_helper.py`,
  `model_proxy.py`, `scripts/factory_core/cli.py` (the verb already prints the signature),
  `budget_gate.sh`, `deconflict.py`, `deploy/**`, `.github/workflows/publish.yml`

`scripts/gate_blast_radius.py` on that list (run in the factory image against
`origin/main` `d459e67`): `STATUS: HUMAN_REQUIRED`, triggers `hotspot`
(`scripts/factory_core/run_record.py`, 5.5) and `migration-seed`
(`.github/workflows/ci.yml`). `entrypoint.sh` is not a trigger: the same list without
`run_record.py` and `ci.yml` returns `PASS`. The implementation stops at the blast-radius
hold for human review.

**Existing tests this breaks** (exact-dict assertions on `failure_spend` that don't expect a
`by_class` key, and the pinned version string), all in `tests/test_run_record.py`:
`:905`, `:990`, `:1511` (`POLICY_VERSION == "1.1"`), `:1581`, `:1584`, `:1591`, `:1598`,
`:1606`, `:1912`, `:1927`, `:2405` (`POLICY_VERSION == "1.1"`), `:2417`. Each needs its
expected dict/string literal updated to include `by_class` (empty `{}` where no failure
signature or ledger rows are in that test's fixture) and `"1.2"`.

## Test plan (one named test per acceptance criterion)

1. **`test_class_for_signature_covers_every_classify_return`** — enumerates every string
   `error_signature.classify()` can return (by calling it with synthetic inputs covering each
   branch at `:95-126`) and asserts `class_for_signature` resolves each to a non-default
   class where a mapping exists, and to `substantive_unknown` only for genuinely unmapped
   input. Fails on an unmapped key, per the operator's explicit requirement.
2. **`test_class_for_signature_strips_exit_code_suffix`** — `class_for_signature("substantive:build_failure:1")
   == "build_failure"`, and the analogous case for `oos_files`/`test_failure`/`unknown`/
   `endpoint_unreachable`.
3. **`test_failure_taxonomy_preserves_model_adapter_order`** — `FAILURE_CLASSES` equals the
   nine strings written out literally in the test, in today's order
   (`model_adapter.py:44-48`), and `model_adapter.FAILURE_CLASSES is
   failure_taxonomy.FAILURE_CLASSES`. (Comparing the two modules alone would pass for any
   order once one re-exports the other.)
3a. **`test_failure_taxonomy_imports_nothing_from_providers`** — importing
   `factory_core.failure_taxonomy` in a fresh interpreter leaves
   `factory_core.providers.model_adapter` and `model_endpoint` out of `sys.modules`.
3b. **`test_enforced_rows_declare_no_unenforced_policy`** — every row with `enforced=True`
   has `max_attempts is None`, both fallbacks `False` and `token_budget_multiplier is None`;
   no row has `cross_endpoint_retry`, `fallback_model_allowed` or `fallback_phase_allowed`
   true; no row has a multiplier of `0.0`; every `enforced_today` that names a file names
   one that exists in the repository; the set of enforced rows equals the literal
   `{"session_window_pause"}`.
3d. **`test_nothing_reads_the_policy_fields`** — over `scripts/**/*.py`, `scripts/**/*.sh`,
   `scheduler.sh` and `entrypoint.sh`: the only files that mention `failure_taxonomy` are
   `failure_taxonomy.py`, `providers/model_adapter.py` and `run_record.py`; no file other
   than `failure_taxonomy.py` mentions `FAILURE_TAXONOMY`; `breaker.py`,
   `error_signature.py` and `scheduler.sh` mention neither. A later enforcement ticket has
   to edit this test, which is the point.
3c. **`test_preview_helper_classes_are_declared`** — every member of
   `preview_helper.ENTRY_FAILURE_CLASSES` and `SCHEDULER_FAILURE_CLASSES` is a key.
4. **`test_annotate_failure_sets_by_class_from_signature`** — a failed record assembled
   with no ledger, then `annotate-failure --failure-signature "substantive:build_failure:1"`,
   has `failure_spend.by_class == {"build_failure": {"tokens": <n>, "basis": "whole_run"}}`
   in the out-file and in the durable copy; every other key of both files is unchanged;
   `runs.jsonl` has the same number of rows before and after.
4a. **`test_annotate_failure_leaves_a_bad_record_alone`** — missing file, non-JSON, no
   `harness_economics`: exit 0, that file is not written.
4b. **`test_annotate_failure_never_copies_one_record_onto_another`** — out-file is a
   symlink to another run's record: the link, its target and this run's durable copy are
   byte-identical afterwards except that the durable copy gains its own `by_class`; a
   record whose `run_id` differs from `--run-id` is not rewritten; a durable path that is
   a symlink is left alone; with the durable directory read-only the out-file is still
   annotated, the exit code is 0 and no temporary file is left behind.
5. **`test_failure_spend_by_class_adds_ledger_request_counts`** — ledger rows with
   `failure_class="timeout_idle"` (x2) at assembly, then `annotate-failure` with
   `substantive:unknown:1`, produce `by_class == {"substantive_unknown": {"tokens": <n>, "basis": "whole_run"}, "timeout_idle": {"tokens": None, "basis": "ledger_rows", "request_count": 2}}`.
6. **`tests/test_entrypoint_failure_signature.sh`** — sources the real
   `_write_error_signature` and `on_failure` out of `entrypoint.sh` with a stub `python3`
   that records its argv, under `set -euo pipefail` and `trap on_failure ERR`, and a
   failing pipeline in the loop's shape. Cases:
   `annotate_runs_after_the_signature_write_with_its_value`;
   `assemble_argv_is_unchanged` (*guard*: byte-identical argv before and after the change);
   `signature_write_argv_and_position_are_unchanged` (*guard*: same argv, still after
   `assemble`, exactly one call);
   `exit_code_is_the_workflows` (3 in, 3 out, also when the annotate stub exits 1 or 2);
   `no_signature_means_no_annotate` (the stub verb prints nothing and exits 1, as with an
   unwritable state volume: no annotate call, failure comment and board move still made);
   `loop_failure_branch_is_not_reached_after_the_trap` (*guard* for the fact this design
   rests on; non-pause, non-hold failure);
   `annotate_runs_after_the_comment_and_before_the_cost_report` (order of the stub log:
   signature write, post-mortem, board move, comment, annotate, cost report);
   `a_hung_annotate_does_not_stop_the_board_move_or_the_comment` (stub verb sleeps: both
   are logged, exit code is the workflow's);
   `pause_path_makes_no_annotate_call`; `issue_less_intent_makes_no_annotate_call`;
   `signature_line_is_still_printed`.

Acceptance criteria that this ticket satisfies through existing behaviour keep their
existing tests, which must pass unmodified: "repeated identical failures trip a visible
breaker" — `tests/test_factory_core_breaker.py::test_record_failure_signature_second_matching_substantive_is_stuck`;
"human-required failures halt durably" — `::test_trip_to_blocked_moves_to_blocked`,
`::test_trip_to_blocked_adds_both_labels`, `::test_trip_to_blocked_posts_comment`;
"existing retry/breaker behaviour remains compatible" — the whole of
`tests/test_factory_core_breaker.py` and `tests/test_factory_core_error_signature.py`
with no edit to either file, plus tests 3, 3d and the guards of test 6.
7. *(removed: the loop branch needs no assemble call; see the review amendment.)*
8. **Four hermetic hybrid cases** (local endpoint outage, local OOM/`timeout_idle`, a
   forbidden destination via `destination_policy_denial`, and a cancelled/partial stream via
   `cancellation`) — each asserts: (a) the resulting ledger row's `failure_class` is one of
   the nine verbatim values, (b) no cloud dispatch occurred (no ledger row with
   `hosting_location="cloud"` for the same `run_id`), (c) the run's `model_dispatch.result.outcome`
   is `"failed"`, and (d) `failure_taxonomy.FAILURE_TAXONOMY[cls].cross_endpoint_retry is False`
   — proving no unauthorized export and no hidden backend switch for any of the four.
9. **`test_policy_version_bump_is_additive`** — a `harness_economics` dict missing
   `by_class` (an "old" record, as a reader would see pre-upgrade) still round-trips through
   `_build_issue_economics` without a KeyError (backfill-path regression guard).

## Out of scope / Follow-up (requires its own human-reviewed ticket)

Unchanged from the first pass, restated for clarity: actually consulting the taxonomy at
retry time (`breaker.py::evaluate_stop_condition` reading `max_attempts`), fallback-model/
fallback-phase dispatch, `token_budget_multiplier` enforcement, a finer-grained
per-tool-call `repeat_tool_call_loop` detector, and wiring `ci_red`/`main_red`/
`conformance_block`/`security_block`/`budget_exceeded` as live (not tagging-only) taxonomy
consumers. None of these are implemented here; each needs its own spec per contract §4.10 and
CLAUDE.md's gate-change rule. **Unchanged limits, restated:** no change that makes the
breaker trip later, retry more, or spend more; no cloud fallback for local-pinned work
(`cross_endpoint_retry: false` on every row makes this explicit in code, not just prose);
nothing under `deploy/instances/**`.

## Alternatives Considered

1. **Rename `error_signature.py`'s strings to taxonomy names.** Rejected — breaking change
   to a working, tested mechanism for no behavioral gain.
2. **Rename `model_adapter.py`'s nine constants.** Rejected — live ledger/fixture values;
   re-export achieves the same ownership transfer without a breaking rename.
3. **Build the finer-grained per-tool-call detector now.** Rejected — new breaker trip
   condition, out of scope per contract §4.10.
4. **Include the Proactive Memory Agent's eight classes.** Rejected — no producing
   subsystem exists (#241).
5. **A new standalone failure-spend ledger file.** Rejected — `harness_economics` is already
   the established additive-extension point (contract §4.7).
6. **Declare `adapter_contract_error`/`output_truncated`/`test_timeout` as the issue's
   literal names, aliased to `build_failure`/`verdict_missing`/`test_failure`.** Rejected
   this pass (new, per operator rework) — an alias is itself a form of relabeling a live,
   tested string under a second name for no reader benefit; one canonical name per real
   failure shape is clearer and is what the operator's "do not relabel" instruction asks for.
7. **Record each class's `token_budget_multiplier` inside `by_class` as a policy
   annotation.** Rejected this pass (reversed from the first pass) — mixing an unenforced
   policy value into an observed-spend structure is exactly the "declared stricter than
   enforced" pattern the rework exists to eliminate; the multiplier stays solely in
   `FAILURE_TAXONOMY`.

## Open Questions (non-blocking)

- Splitting `github_rate_limit` from `model_rate_limit` requires a new regex in
  `error_signature.py` distinguishing GitHub API 403/429 text from a model-provider
  rate-limit message — both currently share `environmental:rate_limit`
  (`error_signature.py`'s `_RATE_LIMIT_RE`, imported from `session_window.py`). Not blocking:
  both resolve to the same enforced behavior today.
- Whether a fast local outage should keep classifying as `delivery_failure` (if it also
  happens to be fast/zero-commit/clean/no-artifact) rather than its own local-endpoint class
  is a `breaker.py`/`classify()` precedence question, explicitly left to a future breaker
  ticket per the operator rework — this spec does not change `classify()`'s existing
  branch order (`error_signature.py:95-126`).

- Why 25 of 85 failed run ids on the two instances have no retained run record is not
  established. It is not the loop branch (see the review amendment). Not blocking: this
  ticket annotates the records `on_failure` does assemble.

## Assumptions (flagged)

- `POLICY_VERSION` `"1.1"` → `"1.2"` is a minor, additive bump (new optional key, no existing
  key's shape changes) and does not require the Request/Result contracts' major-bump handling
  (a separate versioning scheme).
- "Repeated byte-identical failing tool calls trip a visible breaker" is satisfied at
  phase-attempt granularity via the existing #373 mechanism, not literal per-tool-call
  detection, per requirement 5 (§2d).
- "Retry/fallback paths classify failure before spending another model turn" is satisfied in
  the sense that classification now happens, is correctly mapped, and is durably recorded
  before any future retry/fallback logic consumes it — not in the sense that this ticket
  changes when or whether a retry/fallback actually happens. The `enforced` column makes that
  distinction explicit and machine-readable rather than only stated in prose.

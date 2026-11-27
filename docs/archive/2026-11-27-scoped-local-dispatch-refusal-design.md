# Scope the Local-Dispatch Refusal to One Helper Intent; Tighten the Local Credential and Evidence Guards

**Issue:** #633

## Overview / Problem Statement

`scripts/factory_core/providers/model_endpoint.py` appends `_NO_LOCAL_DISPATCH` unconditionally
in two places — `_local_problems()` (reached by the run-side `preflight()` and by
`load_verified_entry()`, which the model proxy and `model-endpoints hosting` use) and
`scheduler_preflight()` (scheduler boot and `cloud_endpoint_confirmed()`). This is the only thing
that stands between today's registry/evidence machinery (#557/#558) and an actual local-routed
dispatch; #587 (the first helper dispatch) cannot start while the run-side refusal is
unconditional, but removing it outright would let every gated phase run locally, because
`FACTORY_MODEL_ENDPOINT` is instance-wide and `dispatch()` forwards it to every run.

This ticket narrows the run-side refusal to exactly one named helper intent
(`classify-preview-helper`), leaves the scheduler-side refusal and every other existing intent
untouched, and closes three gaps on the same code path that must close before any local-eligible
dispatch exists: `entrypoint.sh`'s unconditional `fix` fallback for an unrecognized command
string, the local credential guard's blind spot for non-static-key cloud auth signals, and the
evidence-freshness check's tolerance for an empty `invalidated_by`. It activates nothing by
itself — nothing dispatches `classify-preview-helper` or `classify-preview-checker` until #587
lands, and this ticket makes both exit non-zero with a "not available" message if a free-form
invocation names either.

This is a gate-adjacent change (it edits the one check that today refuses every local-routed run)
and is reviewed as its own ticket per CLAUDE.md's hard limits on gate changes.

## Requirements

Restated from the issue's acceptance criteria, plus the Q&A resolution below.

1. `scheduler_preflight()` is unchanged: a local entry still produces `_NO_LOCAL_DISPATCH` on
   scheduler boot (`providers preflight --side scheduler`) and `model-endpoints hosting-check`
   unconditionally, for every intent, with no new parameter.
2. `_local_problems()`, `preflight()` and `load_verified_entry()` each take an explicit `intent`
   parameter (keyword, default `None`). `_local_problems()` omits `_NO_LOCAL_DISPATCH` only when
   `intent` is in a new `LOCAL_ELIGIBLE_INTENTS` tuple (containing exactly
   `"classify-preview-helper"` today). `None`, `""`, or any other string keeps the refusal. Every
   other check inside `_local_problems()` (cloud credential, base URL, tier aliases, evidence
   state, role qualification) runs unconditionally, regardless of intent.
3. **The intent reaches `preflight()`/`load_verified_entry()` only via an explicit `--intent`
   argument on the baked `providers/cli.py`, derived once in `entrypoint.sh` from the dispatch
   command the scheduler issued (`$ARGUMENTS`) — never from an environment variable,
   `current-run.json`, the staged registry, a label, an issue body or a comment.** Resolved via
   Q&A (below): `entrypoint.sh` has *two* call sites that run before intent is known today, and
   both must move.
4. `entrypoint.sh` replaces its regex-based `INTENT` derivation with an explicit `case` over
   `$ARGUMENTS`. Every command string `scheduler.sh` issues today maps to the same intent as
   before. The two new strings (`Classify preview (helper) #N` → `classify-preview-helper`,
   `Verify preview classification #N` → `classify-preview-checker`) are recognized but, in this
   ticket, make the container exit non-zero with a "not available" message before any clone,
   tracker call or model call — nothing dispatches them yet. Any other string also exits
   non-zero before any clone/tracker/model call; it never silently becomes `fix` (today's
   `INTENT=${INTENT:-fix}` fallback is removed).
5. Credential-env guard (#587 AC4, from #594): `_local_problems()` fails closed when any of
   thirteen names is set: `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`,
   `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_MANTLE`, `CLAUDE_CODE_USE_ANTHROPIC_AWS`,
   `CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`, `AWS_PROFILE`, `AWS_ROLE_ARN`,
   `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`,
   `AWS_CONTAINER_CREDENTIALS_FULL_URI`, `AWS_SESSION_TOKEN`, `GOOGLE_CLOUD_PROJECT`, in a
   tuple owned by `model_endpoint.py` — never imported from `providers/model.py`'s
   `_AWS_ALT_AUTH_SIGNALS`/`_GCP_ALT_AUTH_SIGNALS` (which also carry `AWS_EXECUTION_ENV` and the
   platform-identity GCP signals — deliberately excluded here, see Alternatives). No warn-only
   mode. Four of the thirteen (`FOUNDRY`, `MANTLE`, `ANTHROPIC_AWS`, `ANTHROPIC_GOOGLE_CLOUD`)
   go beyond the issue's list of nine, by operator decision of 2026-11-27 and on measured
   evidence: with the pinned Claude CLI 2.1.282 against the capability suite's mock, each one
   set alone stopped every model request from reaching `ANTHROPIC_BASE_URL`, exactly as
   `BEDROCK` and `VERTEX` do. This only tightens the guard. `CLAUDE_CODE_USE_GATEWAY` and
   `AWS_BEARER_TOKEN_BEDROCK`, each set alone, were measured not to divert the request and are
   deliberately not listed.
6. Evidence minimum (#587 AC4, owner decision 2026-11-26): `_evidence_problems()` rejects a
   record whose `invalidated_by` does not include at least `model_digest`, `harness_version` and
   `registry_config` — such a record resolves `declared`, not `tested`, regardless of whether
   those three otherwise match. `fixture_suite_version` stays compared unconditionally
   (unchanged).
7. Baked-CLI fallback (operator note on #587, 2026-11-26): on a verified-local run,
   `entrypoint.sh`'s `error-signature-write` and `session-window-check` calls fall back to the
   baked `/opt/dark-factory/scripts/factory_core/cli.py` when the clone's `cli.py` exits with
   argparse's unrecognized-argument status for `--hosting-location`. They never retry without
   the flag.

## Q&A

**Q:** `entrypoint.sh` calls into the baked providers CLI at *two* points before `ARGUMENTS`/
`INTENT` are parsed — line 10 (`providers preflight`, feeding `preflight()`) and line 19
(`providers model-endpoints hosting`, feeding `load_verified_entry()` directly, used to compute
`DF_ENDPOINT_HOSTING`). The issue's "Notes" section calls out only line 10/line 99. Given both
call sites need the intent before either can ever stop appending `_NO_LOCAL_DISPATCH`, and the
new intent derivation is a pure string match with no clone/network/model dependency: should the
`ARGUMENTS`/`INTENT` parsing block move ahead of *both* calls, with `--intent` added to both, or
should the two calls parse intent themselves (duplicating the match)?

**A:** Move the `ARGUMENTS`/`ISSUE_NUM`/`INTENT` block (rewritten as the explicit `case`) to run
immediately after `source "$IDENTITY_SH"` — ahead of both the line-10 `preflight` call and the
line-19 `model-endpoints hosting` call. Both calls get `--intent "$INTENT"`. Do not duplicate the
match inside the CLI: one shell-side derivation is the only thing both checks see — this mirrors
the existing `_compute_side_effect_level()` pattern, which already takes
`--intent "${INTENT:-unknown}"` the same way. Three consequences to pin down:
1. `--intent` is optional everywhere; missing, empty, or not on `LOCAL_ELIGIBLE_INTENTS` keeps
   `_NO_LOCAL_DISPATCH`. A bare `cli.py preflight` (no `--intent`), the model proxy's
   `load_verified_entry()` call (its own container's startup — it never has a per-run intent to
   pass) and `scheduler_preflight()` all keep today's behavior with zero code changes at those
   call sites.
2. The removed `INTENT=${INTENT:-fix}` fallback must never resolve to a local-eligible intent —
   an unrecognized command exits non-zero instead of falling through to any intent at all.
3. Moving the parsing block earlier also moves the no-argument usage-exit (today at line
   ~100) ahead of the preflight call, so a no-args invocation now exits on usage before preflight
   runs, not after. This is an accepted, intentional behavior change (it only ever triggers on a
   genuinely argument-less manual invocation). The `ENTRYPOINT_SOURCE_ONLY` guard that lets
   `tests/test_entrypoint_current_run.sh` source the file twice in one shell stays in place at
   its new position. A new test must assert both CLI calls receive the derived intent.

## Architecture / Approach

### 1. `entrypoint.sh`: reorder, and replace regex `INTENT` derivation with an explicit `case`

Move the whole `ARGUMENTS`/`ISSUE_NUM`/`INTENT` block (today's lines ~98–119, including the
no-argument usage/exit) to immediately after `source "$IDENTITY_SH"` (today's line 6), before the
`FACTORY_PROVIDERS_CLI preflight` call (line 9–10) and the `DF_ENDPOINT_HOSTING` resolution (line
12–21). Replace the regex with an explicit, ordered `case`:

```bash
# ARGUMENTS="${*}" is the statement immediately before this block. Both variables below are
# assigned unconditionally, so a value inherited from the environment is never read.
INTENT=""
INTENT_NOT_YET_AVAILABLE=0
# An inherited BASHOPTS or BASH_ENV can start this shell with nocasematch on, which would
# make every literal and regex below case-insensitive.
shopt -u nocasematch
# Exact two-casing literals first — these must not be captured by the generic
# case-insensitive word match below, and are checked before it. Each casing is its own
# alternative; neither relies on nocasematch.
case "$ARGUMENTS" in
  "Fix main"|"fix main") INTENT="fix-main" ;;
  "Audit deps"|"audit deps") INTENT="audit-deps" ;;
  *)
    # The two new command strings match as a whole string only: the fixed text, "#",
    # one or more digits, nothing after. Case-sensitive.
    if [[ "$ARGUMENTS" =~ ^Classify\ preview\ \(helper\)\ \#[0123456789]+$ ]]; then
      INTENT="classify-preview-helper"; INTENT_NOT_YET_AVAILABLE=1
    elif [[ "$ARGUMENTS" =~ ^Verify\ preview\ classification\ \#[0123456789]+$ ]]; then
      INTENT="classify-preview-checker"; INTENT_NOT_YET_AVAILABLE=1
    else
      shopt -s nocasematch
      case "$ARGUMENTS" in
        fix*)        INTENT="fix" ;;
        continue*)   INTENT="continue" ;;
        close*)      INTENT="close" ;;
        refine*)     INTENT="refine" ;;
        plan*)       INTENT="plan" ;;
        deconflict*) INTENT="deconflict" ;;
        recheck*)    INTENT="recheck" ;;
      esac
      shopt -u nocasematch
    fi
    ;;
esac
if [ -z "$INTENT" ]; then
  if [ -z "$ARGUMENTS" ] && [ "${ENTRYPOINT_SOURCE_ONLY:-0}" = "1" ]; then
    # Nine scripts under tests/ source this file with no arguments. Keep today's value
    # for that one case; "fix" is never local-eligible. `exit` here would end the
    # sourcing test's own shell.
    INTENT="fix"
  else
    echo "Unrecognized command: $ARGUMENTS" >&2
    echo "Usage: ..." >&2   # unchanged usage text, see today's lines 101-106
    exit 1
  fi
fi
if [ "$INTENT_NOT_YET_AVAILABLE" = "1" ]; then
  echo "Intent '$INTENT' is recognized but not available until #587 lands." >&2
  exit 1
fi
```

The snippet is normative for these properties; the plan may restructure it if all hold:

- `ARGUMENTS` is assigned from `"${*}"` immediately before the block. `ARGUMENTS`, `INTENT` and
  the not-available flag are all assigned before use and never read from the environment.
- `nocasematch` is switched off before the first match, whatever the shell inherited. The two
  scheduler-side literals keep their two documented casings by explicit alternatives.
- The two new strings match only as whole strings ending in `#<digits>`, case-sensitively,
  with the digit class written out (`[0123456789]`, not a locale-dependent range). A prefix
  match is not enough: `Classify preview (helper) #` and `Classify preview (helper) #5 extra`
  are unrecognized.
- The existing no-argument block (`if [ -z "$ARGUMENTS" ] && [ "${ENTRYPOINT_SOURCE_ONLY:-0}"
  != "1" ]`, today's lines 100-108) moves with the rest, unchanged: usage on stdout, exit 1. So
  the snippet's own empty-command branch is reached only when the file is sourced with
  `ENTRYPOINT_SOURCE_ONLY=1`, where `INTENT` is `fix` as today.
- A non-empty unrecognized command prints its message and the usage text to stderr and exits 1.
- Leading whitespace is no longer skipped (today's regex has `^\s*`). No scheduler-issued
  string has any.

This replicates today's matching exactly for every string `scheduler.sh` issues (Requirement 4):
the generic branch matches a leading substring case-insensitively like the current
`grep -oiP '^\s*\K(...)'`, without a word-boundary requirement (identical to today); the two
exact-casing literals are checked first, exactly as today's separate `case` does after the regex.
The two new strings are recognized and immediately refused — never falling through to the
generic branch (where `"Classify preview..."` would not match any of the seven words anyway) and
never defaulting to `fix`.

Checked by running today's regex and this `case` side by side in bash (review, 2026-11-27).
Same intent for every string `scheduler.sh` issues: `Fix issue #N`, `Continue issue #N`,
`Continue`, `Close issue #N`, `Refine issue #N`, `Plan issue #N`, `Deconflict issue #N`,
`Recheck main`, `Fix main`, `Audit deps`; and for the documented manual forms in the usage text
and `README.md`. Strings that run as `fix` today and now exit non-zero: a command with leading
whitespace, `Audit Deps` in any casing other than the two literals, and any free-form text that
starts with none of the seven words (for example `Implement issue #3`, `Resolve issue #3`,
`#3`). None of these is issued by the scheduler.

One documented command is in that group. `bench/baseline.md:45-62` documents
`docker compose --profile factory run --rm dark-factory bash /workspace/markethawk/dark-factory/bench/run_suite.sh`
(with `--n`, `--dry-run`, `--issues`, `--baseline` variants). The image's `ENTRYPOINT` is
`entrypoint.sh` (`Dockerfile:175`), which has no pass-through for a `bash ...` command, so today
that string does not run the suite: it becomes `ARGUMENTS`, resolves to `INTENT=fix` with no
issue number, and goes on to the clone and the workflow. After this ticket it exits non-zero
with the usage text before any clone. The documented command is already not doing what the page
says, so this ticket adds no recognised form for it and does not edit `bench/`. Repairing the
bench invocation (for example with `--entrypoint bash`) is a separate follow-up.

`--intent "$INTENT"` is then added to both the line-10 `providers preflight` call and the line-19
`providers model-endpoints hosting` call.

### 2. `providers/cli.py`: thread `--intent`

- `preflight` subcommand gains an optional `--intent` (default `""`). `_preflight(args)` calls
  `preflight(side=args.side, intent=args.intent or None)`.
- `model-endpoints hosting` subcommand gains the same optional `--intent`. `_model_endpoints_hosting`
  passes it to `load_verified_entry(..., intent=args.intent or None)`.
- No other subcommand changes. `model-endpoints hosting-check` (scheduler-side,
  `scheduler_preflight()`) stays signature-unchanged — intent is irrelevant there (Requirement 1).

### 3. `providers/__init__.py`: thread intent only on the run side

`preflight(side="run", intent=None)` passes `intent` into `_endpoint_preflight(side, endpoint_id,
model_name, intent)`, which passes it to `model_endpoint.preflight(..., intent=intent)` only when
`side != "scheduler"`. The scheduler branch keeps calling `scheduler_preflight()` with no intent
argument — unaffected by this change.

### 4. `model_endpoint.py`: the intent gate, credential guard, and evidence minimum

```python
LOCAL_ELIGIBLE_INTENTS = ("classify-preview-helper",)

# Mirrors, never imports, providers/model.py's _AWS_ALT_AUTH_SIGNALS / _GCP_ALT_AUTH_SIGNALS —
# a credential-presence signal, not a platform-identity signal (AWS_EXECUTION_ENV and the GCP
# platform vars are deliberately excluded; see Alternatives).
LOCAL_CREDENTIAL_ENV_SIGNALS = (
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SESSION_TOKEN", "GOOGLE_CLOUD_PROJECT",
)

def _local_problems(entry, files, registry_path, intent=None):
    ...  # existing cloud-credential, base-url, tier-alias, evidence-state checks, unchanged
    present_signals = sorted(n for n in LOCAL_CREDENTIAL_ENV_SIGNALS if os.environ.get(n))
    if present_signals:
        problems.append(f"endpoint '{eid}' is local but an alternate cloud credential signal "
                        f"is set in this container: {', '.join(present_signals)} — a "
                        "local-routed container holds none (contract §4.3)")
    if intent not in LOCAL_ELIGIBLE_INTENTS:
        problems.append(_NO_LOCAL_DISPATCH.format(eid=eid))
    return problems

def preflight(registry_path, endpoint_id, intent=None) -> tuple[list[str], str | None]:
    ...
    if hosting == "local":
        problems += _local_problems(entry, files, registry_path, intent=intent)
    return problems, hosting

def load_verified_entry(registry_path, endpoint_id, intent=None) -> tuple[list[str], dict | None]:
    ...
    if entry["hosting_location"] == "local":
        problems = problems + _local_problems(entry, files, registry_path, intent=intent)
    ...

def _evidence_problems(record, entry):
    ...  # existing checks unchanged
    names = record["invalidated_by"]
    if not isinstance(names, list) or any(n not in FINGERPRINTS for n in names):
        problems.append(...)  # unchanged
    else:
        # List membership, not set(): a non-string element (a mapping, a list) must not
        # raise TypeError here. Only reached when the list holds known names only.
        required_minimum = ("model_digest", "harness_version", "registry_config")
        if not all(m in names for m in required_minimum):
            problems.append(f"{where}: invalidated_by must include at least "
                            f"{', '.join(required_minimum)}")
    return problems
```

`scheduler_preflight()` is not touched. The model proxy reaches `load_verified_entry()` through
`model_adapter.resolve_local_destination()` (`model_adapter.py:116-118`, called from
`model_proxy.main()`). Neither is touched: the call passes no `intent`, which defaults to `None`
and keeps the refusal (Requirement 2/Q&A point 1). Passing the helper intent to a dedicated
helper proxy as a fixed startup argument is #634's change, not this ticket's.

### 4a. Every caller, and the intent it passes

Complete list at origin/main (1628a6e). A caller not listed here, or added later without an
`intent` argument, gets `None` and is refused.

| Caller | Reaches | Intent passed after this ticket |
|---|---|---|
| `entrypoint.sh:10` `providers preflight` (baked CLI, before the clone) | `providers.preflight()` → `model_endpoint.preflight()` | `--intent "$INTENT"` from the `case` |
| `entrypoint.sh:19` `providers model-endpoints hosting` (baked CLI, before the clone) | `load_verified_entry()` | `--intent "$INTENT"` from the `case` |
| `scheduler.sh:51` `providers preflight --side scheduler` | `scheduler_preflight()` | none; `--intent`, if given with `--side scheduler`, is ignored |
| `scheduler.sh:387` `model-endpoints hosting-check` | `scheduler_preflight()` | none; the subcommand has no `--intent` |
| `model_proxy.main()` → `model_adapter.resolve_local_destination()` | `load_verified_entry()` | none (`None`) |
| a manual `providers/cli.py preflight` or `model-endpoints hosting` | as above | none unless typed by hand; it decides nothing for a dispatch |

`model-endpoints show` and `model-endpoints stage` never call `_local_problems()`.

Both `entrypoint.sh` calls run the baked `/opt/dark-factory/scripts/factory_core/providers/cli.py`
before the target repository is cloned, so no file on a pull-request branch can alter the check
or the intent it receives. `providers/cli.py` must not read the intent from any source other than
its own `--intent` argument.

### 5. `entrypoint.sh`: baked-CLI fallback for `--hosting-location`

A shared helper, used by `_handle_session_window_pause()`'s `session-window-check` call
(`entrypoint.sh:322`) and `_write_error_signature()`'s `error-signature-write` call
(`entrypoint.sh:509`), **only when `hosting_flag` is non-empty**. When it is empty (every cloud
run, and every run with no endpoint selected) each call site runs the clone's `cli.py` directly
with today's exact argv and redirections, unchanged.

```bash
# Verified-local runs only. The clone's cli.py may predate --hosting-location and reject it
# with exit code 2. Falls back to the baked copy with the identical argv — never a retry
# without the flag.
_cli_with_hosting_fallback() {
  local out rc=0
  out=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "$@" 2>&1) || rc=$?
  if [ "$rc" -eq 2 ]; then
    rc=0
    out=$(python3 /opt/dark-factory/scripts/factory_core/cli.py "$@" 2>&1) || rc=$?
  fi
  printf '%s\n' "$out"
  return "$rc"
}
```

Properties the plan must keep:

- The second call has the identical argv, `--hosting-location local` included. No code path
  calls either copy without the flag on a verified-local run.
- `|| rc=$?`, not a bare `rc=$?` on the next line: `entrypoint.sh` runs under `set -e` with an
  ERR trap.
- Only the output of the attempt whose exit status is returned is printed; the rejected first
  attempt's usage text is dropped.
- The two call sites redirect differently today and keep doing so: `session-window-check`
  captures stdout and stderr together (`2>&1`) and parses `matched=`; `error-signature-write`
  discards stderr (`2>/dev/null || true`). The helper merges stderr into its stdout, so the
  `error-signature-write` call site must discard the helper's output on the local path.

Exit code 2 is what CPython returns for three cases, each confirmed by execution: an argparse
unknown flag, an argparse unknown subcommand, and a script file that does not exist. All three
mean the clone's copy cannot serve this call, and all three fall back with the flag intact.
`scripts/factory_core/cli.py` itself exits 1 or 3 on its own errors, plus the exit status of
`resolve_merge_conflicts` on a different subcommand, so a real failure of either subcommand is
not masked. Any other status is returned as is, with no fallback.

Assumption this rests on: no `cli.py` copy does the work of `session-window-check` or
`error-signature-write` and then exits 2. If one did, the baked copy would do the work a second
time. argparse rejects an unknown flag before any subcommand code runs, and neither subcommand
has an exit-2 path at origin/main.

### 6. Documentation

Required by the issue's last acceptance criterion:

- `docs/configuration.md`: the paragraph ending "and in every case until #587 ships a
  local-eligible dispatch", the "Known gap, a precondition of #587 (#594)" paragraph (the gap is
  closed; state all thirteen guard names, the two measured non-diverting names that are not
  listed, and the `invalidated_by` minimum), and the
  `model-endpoints hosting` paragraph ("stays `cloud` for every entry until #587").
- `docs/local-inference-deployment-runbook.md`: "Selecting a local endpoint is refused everywhere
  until #587 (`_NO_LOCAL_DISPATCH`)" and the #594 line after it; state that a capability-evidence
  record must list `model_digest`, `harness_version` and `registry_config`, and only names
  `evidence_state()` can resolve.
- `docs/run-lifecycle.md` section 1 ("Intents"): "No match means `fix`" becomes "no match
  exits non-zero with the usage text on stderr"; add the two new command strings and their
  intents, marked not available until #587; update the `entrypoint.sh` line citations.
- `docs/run-lifecycle.md` section 2 startup-step table: argument parsing (today step 4, lines
  99-119) now precedes the provider preflight (step 2) and the hosting resolution (step 2a);
  both rows note `--intent`; renumber and re-cite lines.
- The usage text in `entrypoint.sh` lists the accepted command strings.
- `bench/baseline.md` is not edited (see §1).

### 7. Interface for #634 and #587

Stable names the two follow-ups rely on. Changing any of them later is a change to this ticket's
contract.

- Intent tokens: `classify-preview-helper` (local-eligible) and `classify-preview-checker`
  (recognized, never local-eligible).
- Command strings, whole-string match: `Classify preview (helper) #<digits>` and
  `Verify preview classification #<digits>`.
- `model_endpoint.LOCAL_ELIGIBLE_INTENTS == ("classify-preview-helper",)`.
- `model_endpoint.preflight(registry_path, endpoint_id, intent=None)` and
  `model_endpoint.load_verified_entry(registry_path, endpoint_id, intent=None)`; omitted, `None`,
  empty or any other value keeps `_NO_LOCAL_DISPATCH`.
- `providers.preflight(side="run", intent=None)`; the baked CLI's optional `--intent` on
  `preflight` and `model-endpoints hosting`.
- `model_adapter.resolve_local_destination()` is unchanged here; #634 adds its own way to pass
  the helper intent to it.
- In `entrypoint.sh`, both new intents exit non-zero ("not available") before any clone,
  tracker call or model call. Removing that exit is necessary for #587 but not sufficient:
  #587 must give each of the two intents an entrypoint-only path, like `recheck`, `fix-main`,
  `audit-deps` and `deconflict` have today, that never reaches
  `archon workflow run archon-dark-factory "$ARGUMENTS"`. The workflow's `parse-intent` node
  (`workflows/archon-dark-factory.yaml:21-41`) re-interprets the command string by model into
  `new|continue|close|refine|plan|resolve` and would run one of those phases.

## Test Plan

All tests are hermetic: fixture registries under `tests/fixtures/`, 192.0.2.0/24 addresses, no
network. Each is written first and must fail on origin/main. Names are binding; the plan may add
more.

| Issue acceptance criterion | Test |
|---|---|
| Scheduler side still refuses | existing `scheduler_preflight` and `hosting-check` tests stay green unchanged; new `test_scheduler_side_ignores_intent` (`preflight --side scheduler --intent classify-preview-helper` still refuses a local entry) |
| Every other intent still refused on the run side | `test_local_entry_refused_for_every_intent_except_helper`, parametrised over `fix`, `continue`, `close`, `refine`, `plan`, `deconflict`, `recheck`, `fix-main`, `audit-deps`, `classify-preview-checker`, `None`, `""`, for both `preflight()` and `load_verified_entry()` |
| Helper intent lifts only the one refusal | `test_helper_intent_omits_only_no_local_dispatch`; `test_helper_intent_still_fails_each_local_check`, parametrised over cloud credential present, base URL mismatch, tier alias mismatch, evidence not `tested`, role not qualified |
| Intent cannot come from the environment or run-writable state | `test_intent_not_read_from_environment_or_pointer` (sets `INTENT`, `FACTORY_DISPATCH_INTENT`, `FACTORY_INTENT` and a `current-run.json` with `"intent":"classify-preview-helper"`; the refusal stays). This one is a regression guard: it passes on origin/main, where the refusal is unconditional, and cannot be written failing-first. `test_local_eligible_intents_is_exactly_the_helper` |
| The CLI threads `--intent` and nothing else lifts | `test_hosting_cli_threads_intent`: with a valid, `tested` local fixture entry staged and selected, `model-endpoints hosting --intent classify-preview-helper` prints exactly `hosting=local` and exits 0 with empty stderr; the same call with no `--intent`, with `--intent ""` and with `--intent fix` prints exactly `hosting=cloud`, exits 0, and stderr carries the `_NO_LOCAL_DISPATCH` text. `test_preflight_cli_threads_intent`: `preflight --intent classify-preview-helper` on the same fixture reports no `_NO_LOCAL_DISPATCH` problem; without the flag it exits non-zero with it |
| Existing command strings keep their intent | `tests/test_entrypoint_intent_case.sh`, parametrised over the ten scheduler-issued strings and the two lower-case literals |
| Unrecognized exits before clone, tracker or model call | same file: unrecognized, empty, leading-whitespace and near-miss helper strings exit non-zero with stubs for `git`, `gh`, `docker` and the providers CLI recording zero calls; an unrecognized non-empty command writes its message to stderr; an inherited `ARGUMENTS`, `INTENT` or `INTENT_NOT_YET_AVAILABLE` in the environment changes nothing; with `BASHOPTS=nocasematch` exported, `CLASSIFY PREVIEW (HELPER) #5` is unrecognized and `FIX MAIN` resolves to `fix`, not `fix-main`; the two new strings exit non-zero with "not available" |
| Both CLI calls receive the derived intent | same file: a stub providers CLI records `--intent <value>` on `preflight` and on `model-endpoints hosting` |
| Each added credential signal fails closed; excluded platform variables do not | `test_local_selection_fails_on_alternate_credential_signal`, parametrised over the thirteen names; `test_platform_identity_variable_does_not_fail_local_selection`, parametrised over the five excluded names; `test_measured_non_diverting_names_are_not_guard_signals` (`CLAUDE_CODE_USE_GATEWAY`, `AWS_BEARER_TOKEN_BEDROCK`) |
| Evidence minimum | `test_invalidated_by_below_minimum_is_declared`, parametrised over `[]` and each list missing one of the three; `test_invalidated_by_exact_minimum_is_tested`; `test_invalidated_by_non_string_element_is_declared_not_an_exception` |
| Baked-CLI fallback | `tests/test_entrypoint_hosting_fallback.sh`: a stub clone `cli.py` that exits 2 on `--hosting-location` yields a `substantive:endpoint_unreachable` signature via the baked CLI; every recorded argv contains the flag; a stub that exits 1 causes no fallback; on a cloud run the helper is not used and the argv is byte-identical to today |
| Default path unchanged | the existing test that `model_endpoint` is not imported with `FACTORY_MODEL_ENDPOINT` unset stays green |
| Docs | the existing docs tests stay green; the plan adds assertions if a docs test pins any replaced sentence |

Existing tests that this change breaks and that the implementation must update. Found by
applying every code change in this spec (`entrypoint.sh`, `providers/cli.py`,
`providers/__init__.py`, `model_endpoint.py`) to a scratch copy and running, before and after,
the whole Python suite and all 23 `tests/test_*.sh` files that mention `entrypoint.sh`, as a
non-root user in a git checkout directory named `dark-factory` with no `/opt/dark-factory`
(the CI shape). Before: 3940 Python tests pass and every CI-wired shell test passes. After, the
complete list of new failures is:

- `tests/test_provider_model_endpoint.py::test_unresolvable_fingerprint_is_unknown` (four
  parameters) and `::test_unpinned_fixture_suite_is_unknown`. Both build a record whose
  `invalidated_by` omits `harness_version` and `registry_config`, which now resolves `declared`
  before the fingerprint comparison. Each must list the three required names plus the one under
  test and keep asserting `unknown`.
- `tests/test_provider_model_endpoint.py::test_hosting_is_local_only_for_a_verified_local_entry`,
  `::test_hosting_any_problem_from_the_verified_loader_is_cloud` and
  `::test_hosting_exception_is_cloud_and_exits_zero`. Each replaces `load_verified_entry` with a
  two-parameter stub, which now receives `intent=`. The stubs must accept it; the first test's
  recorded-call assertion must include the intent passed.
- `tests/test_provider_cli.py::test_preflight_ok_prints_ok_and_exits_0`,
  `::test_preflight_failure_prints_every_problem_and_exits_1` and
  `::test_preflight_side_defaults_to_run_and_passes_scheduler_through`. Each replaces
  `preflight` with a `lambda side="run"` stub, which now receives `intent=`.
- `tests/test_entrypoint_session_window.sh` case M (lines 570-573). Its two assertions pin the
  providers log line to exactly `model-endpoints hosting`; they must accept
  `model-endpoints hosting --intent plan`. Every other assertion in the file still passes (101
  of 103).

No other test changes result. `tests/test_entrypoint_fix_main.sh` fails before and after with
the same structural message (it is CI-exempt for that reason); only the line numbers in its
message move. No existing test or fixture relies on an empty `invalidated_by` resolving
`tested`.

### Files touched

| File | Change |
|---|---|
| `entrypoint.sh` | move and rewrite the argument/intent block; `--intent` on two CLI calls; fallback helper and its two call sites; usage text |
| `scripts/factory_core/providers/cli.py` | optional `--intent` on `preflight` and `model-endpoints hosting` |
| `scripts/factory_core/providers/__init__.py` | `intent` threaded to the run-side check only |
| `scripts/factory_core/providers/model_endpoint.py` | `LOCAL_ELIGIBLE_INTENTS`, `LOCAL_CREDENTIAL_ENV_SIGNALS`, `intent` parameter, evidence minimum |
| `tests/test_provider_model_endpoint.py`, `tests/test_provider_cli.py` | new tests; the existing tests listed above |
| `tests/test_entrypoint_intent_case.sh`, `tests/test_entrypoint_hosting_fallback.sh` | new |
| `tests/test_entrypoint_session_window.sh` | case M's two assertions |
| `.github/workflows/ci.yml` | exactly two added steps, `- run: bash tests/test_entrypoint_intent_case.sh` and `- run: bash tests/test_entrypoint_hosting_fallback.sh`; `tests/test_ci_covers_shell_tests.py` (`test_every_shell_test_is_wired_or_exempt`) fails without them. Nothing else in the file changes |
| `docs/configuration.md`, `docs/local-inference-deployment-runbook.md`, `docs/run-lifecycle.md` | as listed in §6 |

Not changed by this ticket: any `gate_*` script, the breaker, budgets, `scheduler.sh`,
`run-compose.yml`, `deploy/**`, `.github/workflows/publish.yml`, the workflow DAG files under
`workflows/`, `bench/`, and any tool allow or deny list.

## Alternatives Considered

- **Reuse `providers/model.py`'s `_AWS_ALT_AUTH_SIGNALS`/`_GCP_ALT_AUTH_SIGNALS` for the new local
  guard.** Rejected per issue scope: those tuples include `AWS_EXECUTION_ENV` and GCP
  platform-identity variables (`K_SERVICE`, `GAE_APPLICATION`, `FUNCTION_TARGET`,
  `KUBERNETES_SERVICE_HOST`) that name *where a container runs*, not a credential — importing
  them would make the local guard fail closed inside, e.g., any GKE/Cloud Run-hosted factory
  deployment regardless of actual cloud-model credentials. A local entry's own tuple stays a
  pure credential-presence signal.
- **Have the line-10/line-19 CLI calls parse `$ARGUMENTS` (or `$*`) themselves instead of
  receiving `--intent`.** Rejected (Q&A): two independent derivations of the same mapping can
  drift; one shell-side `case`, computed once, is the single source both checks read.
- **Keep `entrypoint.sh`'s argument-parsing order unchanged and pass intent to only the line-10
  call (per the issue Notes' literal line citation), leaving line-19's `model-endpoints hosting`
  unpatched.** Rejected: `DF_ENDPOINT_HOSTING` would then stay permanently `cloud` even for a
  genuinely eligible helper dispatch (its own `load_verified_entry()` call would still see no
  intent), silently defeating the point of this ticket for every downstream reader of
  `DF_ENDPOINT_HOSTING` (the `--hosting-location` flag passed to `session-window-check` /
  `error-signature-write`, and any future reader).
- **Grep the clone-side `cli.py` call's stderr for the literal string `--hosting-location` before
  falling back, instead of checking exit code 2.** Rejected: the stub/old CLI's exact error text
  is not a stable contract across CLI versions/argparse message formats, while exit code `2` is
  argparse's documented, stable status for this exact failure class, and is what the ticket's own
  acceptance criterion exercises.
- **Keep the `INTENT=${INTENT:-fix}` silent fallback for an unrecognized command.** Rejected per
  the issue's own call-out: the two new intents must not be reachable by accident, and a
  genuinely malformed manual invocation is better caught immediately (non-zero exit, no clone)
  than silently run as a full `fix`.

## Open Questions (non-blocking)

- Exact wording of the "not available" and "unrecognized command" messages is left to the plan
  phase; the issue only requires "a clear message" / "exits non-zero".
- None on the fallback's output handling: §5 states the two call sites' redirections and what
  the helper prints.

## Assumptions (flagged)

- `LOCAL_ELIGIBLE_INTENTS` is introduced as a tuple (not a single constant string). This is a
  naming/shape choice, not a widening of eligibility: it holds exactly one name. Adding a name
  is a gate change that needs its own reviewed ticket. `classify-preview-checker` must never be
  added: a local endpoint never hosts a checker (contract §4.2). A test pins the tuple's exact
  contents.
- The explicit `case`'s generic branch is implemented with `shopt -s nocasematch` around a
  second-level `case`, reproducing the original regex's lack of a word-boundary requirement
  (e.g. `"Fixup #3"` would still resolve to `fix`, exactly as today). The plan phase may choose an
  equivalent implementation (e.g. parameter-expansion pattern matching) as long as the
  case-insensitive, no-word-boundary, leading-substring semantics are preserved for the seven
  existing words and the two exact-casing literals keep their current priority over the generic
  branch.
- `providers/cli.py`'s `--intent` flag defaults to `""` rather than being required, so every
  existing caller of `preflight`/`model-endpoints hosting` that predates this ticket (tests,
  `scripts/factory_core/side_effect.py`'s unrelated CLI, any manual operator invocation) keeps
  working unmodified.

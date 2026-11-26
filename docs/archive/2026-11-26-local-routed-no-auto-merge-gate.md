# Local-Routed No-Auto-Merge Gate — Implementation Plan

**Issue:** #585

> **For agentic workers:** REQUIRED: execute task-by-task, in order. Each step is one small
> action. Steps use checkbox (`- [ ]`) syntax for tracking. Every code block below was
> prototyped against `main` @ `06afd26` (this branch's merge-base) and run: the full suites
> pass with it, and the new tests fail on unmodified `main` as described.

**Goal:** While this instance's `FACTORY_MODEL_ENDPOINT` is set and does not resolve to a
definite `cloud` registry entry, the scheduler refuses every automated merge
(`dispatch_close`, which serves the end-gate `APPROVED` and comment-classifier `MERGE`
paths) and every grace-timer advance (`spec_advance_check`, `plan_advance_check`). It
applies `ready-for-human` and posts one marker-deduped, footer-signed comment.
`FACTORY_MODEL_ENDPOINT` unset leaves behaviour byte-identical to today
(`docs/hybrid-execution-contract.md` Invariant 7).

**Architecture:** A new read-only CLI subcommand `providers/cli.py model-endpoints
hosting-check --endpoint-id ID` wraps the existing
`model_endpoint.scheduler_preflight(SOURCE_REGISTRY_DIR, id)` and prints exactly
`hosting=cloud` (exit 0) only for a cloud entry. In `scheduler.sh` a bash predicate
`cloud_endpoint_confirmed` short-circuits when the endpoint is unset. Otherwise it passes only
on exit 0 with stdout exactly `hosting=cloud`. A shared poster, `refuse_local_routing`,
does the label and the marker-upsert comment. Three call sites are guarded:
`dispatch_close` (before `evaluate_stop`) and the two grace branches. No run-writable file
is read anywhere.

**Tech Stack:** bash (`scheduler.sh`, `tests/test_scheduler.sh` stub-CLI harness), Python 3
(`scripts/factory_core/providers/cli.py`, pytest).

**Spec:** `docs/superpowers/specs/2026-11-25-local-routed-no-auto-merge-gate-design.md`
(approved at `21bf794`).

**If `main` moved:** the literal numbers in Steps 1.2, 1.5, 2.2, 3.3, 4.3, 5.1, 5.2, 5.8, 6.1 and 6.4, and the replaced blocks in Steps 4.1 and 4.2 (test counts, line
numbers, `remapped 133`) assume `scheduler.sh` and `docs/scheduler.md` still match `06afd26`.
Check with `git diff 06afd26 origin/main -- scheduler.sh docs/scheduler.md tests/test_scheduler.sh`.
If that is non-empty, the numbers shift. The remap script is still right, because it
computes from the live merge-base: trust its output over the literals, and keep the
"0 failures" and "exactly once" conditions.

**Commit cadence:** Tasks 2 and 3 commit deliberately red tests (failing-first TDD). Push
only after Task 6 passes; the implement phase's `push-and-pr` step pushes once at the end.
Do not push mid-plan.

---

## Constraints baked into every task (from the spec / issue owner decisions)

- Touch **only** the six files in the File Structure table. In particular **not**
  `deploy/**`, `.github/workflows/*`, `config/config.yaml`,
  `workflows/archon-dark-factory.yaml`, `scripts/main_red_fixer.py`, `scripts/deps_fixer.py`,
  `scripts/factory_core/providers/model_endpoint.py`, `.factory/adapter.yaml`, or any tool
  allow/deny list. `Fix main`/`Audit deps` self-merge (#615) and an in-node `close-preview`
  check (#616) are out of scope.
- Never read the request ledger, run records, `current-run.json` or `runs.jsonl` (owner
  decision 2). Test AC9c locks this statically.
- Never bypass or edit `_NO_LOCAL_DISPATCH`. The `local` branch is reached only by stubbing
  the resolver in tests.
- The change may only add refusals. No existing gate, breaker, budget, label semantic or
  session-window behaviour changes. Sections G, I and AB of `tests/test_scheduler.sh` must
  pass **unmodified**.
- `scheduler.sh` runs under `set -euo pipefail` with an ERR trap. Every new command that can
  fail on the grace paths (which are not inside an `if`) is guarded with `|| true` or
  `|| return 0`.
- The posted comment carries no worker-controlled text. Only the fixed token
  `hosting=local` / `hosting=unresolved` appears; resolver stderr and `reason=` text are
  discarded.
- This ticket is never `direct-to-pr`. `scheduler.sh` is a hotspot: the PR needs a human
  signature before merge. Say so in the PR body.
- Run commands from the repo root. CI sets `PYTHONPATH=scripts`; every command below does too.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/providers/cli.py` | Modify | New `model-endpoints hosting-check` subcommand (`_model_endpoints_hosting_check` + parser registration) |
| `tests/test_provider_model_endpoint.py` | Modify (append) | 7 pytest cases for the subcommand |
| `tests/test_scheduler.sh` | Modify (new section AC before `# Cleanup`) | Hermetic stub-CLI tests for all four guarded paths |
| `scheduler.sh` | Modify | `cloud_endpoint_confirmed` + `refuse_local_routing` helpers; guards in `dispatch_close`, `spec_advance_check`, `plan_advance_check` |
| `docs/scheduler.md` | Modify | Every `scheduler.sh` line citation remapped to the new line numbers (133 citations); Grace-timer + End-gate subsections: guard, residual, how to clear |
| `docs/triage-labels.md` | Modify | Both `ready-for-human` rows (lines 10 and 62) |

---

## Task 1: `model-endpoints hosting-check` subcommand (pytest first)

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append at end of file)
- Modify: `scripts/factory_core/providers/cli.py`

- [ ] **Step 1.1: Write the failing tests.** Append the following to the end of
  `tests/test_provider_model_endpoint.py`, after two blank lines. It reuses the file's
  existing `me`, `CLOUD`, `LOCAL`, `_entry` and `_write_registry` helpers and its autouse
  `_clean_env` fixture:

```python


# --- #585: model-endpoints hosting-check (scheduler.sh's non-cloud endpoint guard) ---------

def _hosting_check(monkeypatch, capsys, endpoint_id):
    from factory_core.providers import cli
    monkeypatch.setattr(sys, "argv", ["providers-cli", "model-endpoints", "hosting-check",
                                      "--endpoint-id", endpoint_id])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr()


def test_hosting_check_cloud_entry_prints_exactly_hosting_cloud(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 0
    assert out.out == "hosting=cloud\n"


def test_hosting_check_local_entry_exits_1_with_local_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    code, out = _hosting_check(monkeypatch, capsys, "local-test")
    assert code == 1
    assert out.out.startswith("hosting=local ")
    assert "hosting=cloud" not in out.out


def test_hosting_check_unknown_id_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))
    code, out = _hosting_check(monkeypatch, capsys, "nope")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_missing_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", tmp_path / "absent")
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "not found" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_invalid_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / me.REGISTRY_FILE).write_text("schema_version: 1.0\nendpoints: []\n", encoding="utf-8")
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", src)
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "hosting=cloud" not in out.out


def test_hosting_check_unreadable_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))

    def _unreadable(path):
        raise me.RegistryError([f"{path}: unreadable (Permission denied)"])

    monkeypatch.setattr(me, "_read_file", _unreadable)
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "unreadable" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_reads_the_source_mount_never_the_staged_copy(tmp_path, monkeypatch, capsys):
    # The staged copy lives on the run-writable state volume: a cloud entry there must never
    # answer for a local entry on the scheduler's read-only mount (#585 owner decision 2).
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR",
                        _write_registry(tmp_path / "src", [_entry(LOCAL, id="sel")]))
    staged = _write_registry(tmp_path / "staged", [_entry(CLOUD, id="sel")])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(staged))
    code, out = _hosting_check(monkeypatch, capsys, "sel")
    assert code == 1
    assert out.out.startswith("hosting=local ")
```

- [ ] **Step 1.2: Run the tests and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k hosting_check
```

Expected: `7 failed, 192 deselected`. argparse rejects the unknown `hosting-check` verb
with `SystemExit(2)`, so the exit-code and stdout assertions fail.

- [ ] **Step 1.3: Implement the subcommand.** In `scripts/factory_core/providers/cli.py`,
  insert this function directly above `def main():` (that is, after
  `_model_endpoints_stage`, with two blank lines on both sides, per the file's PEP 8
  spacing):

```python
def _model_endpoints_hosting_check(args):
    # #585: scheduler.sh's non-cloud endpoint guard. Resolves against the scheduler's
    # read-only mount — never the staged (run-writable) copy. Only a definite cloud entry
    # prints `hosting=cloud` and exits 0; the reason= text is for an operator running this
    # by hand, and scheduler.sh discards it.
    from factory_core.providers import model_endpoint
    problems, hosting = model_endpoint.scheduler_preflight(
        model_endpoint.SOURCE_REGISTRY_DIR, args.endpoint_id
    )
    if hosting == "cloud":
        print("hosting=cloud")
        return
    if hosting == "local":
        print(f"hosting=local reason=endpoint '{args.endpoint_id}' hosting_location=local")
    else:
        reason = "; ".join(problems) if problems else f"endpoint '{args.endpoint_id}' not found in registry"
        print(f"hosting=unknown reason={reason}")
    sys.exit(1)
```

- [ ] **Step 1.4: Register the parser.** In the same file's `main()`, directly after the
  existing line `    mstage.set_defaults(func=_model_endpoints_stage)`, add a blank line
  and then:

```python
    mhc = msub.add_parser("hosting-check",
                          help="scheduler-side hosting of an endpoint id (#585); exit 0 only for cloud")
    mhc.add_argument("--endpoint-id", required=True)
    mhc.set_defaults(func=_model_endpoints_hosting_check)
```

- [ ] **Step 1.5: Run the tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k hosting_check
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
```

Expected: `7 passed, 192 deselected`, then `199 passed`.

- [ ] **Step 1.6: Spot-check by hand.** The real mount is absent in a dev container, so this
  must fail closed:

```bash
PYTHONPATH=scripts python3 scripts/factory_core/providers/cli.py model-endpoints hosting-check --endpoint-id x; echo "rc=$?"
```

Expected: one line starting `hosting=unknown reason=model endpoint registry not found at
/etc/dark-factory/model-registry/model-endpoints.yaml`, then `rc=1`.

- [ ] **Step 1.7: Commit.**

```bash
git add scripts/factory_core/providers/cli.py tests/test_provider_model_endpoint.py
git commit -m "feat(#585): model-endpoints hosting-check — scheduler-side, read-only-mount hosting resolution"
```

---

## Task 2: Section AC in `tests/test_scheduler.sh` (failing first)

**Files:**
- Modify: `tests/test_scheduler.sh`. Insert the block immediately **before** the existing
  three lines:

```bash
# ==========================================
# Cleanup
# ==========================================
```

  (currently at lines 2429-2431). It must sit before Cleanup, because Cleanup deletes
  `$STATE_FILE` and `$STUB_LOG`. It must sit after AB, because it reuses AB's
  `_AB_REAL_EVALUATE_STOP` / `_AB_REAL_TRIP_TO_BLOCKED` captures.

How the section maps to the spec's Test plan:

| Spec test | AC subsection |
|---|---|
| 1. Refusal per resolver outcome × four paths | AC1 (8 resolver cases × approved/merge/spec/plan) |
| 2. Assertions for every refusal | `_ac_assert_refused` (14 assertions per case) |
| 3. Second poll: counters unchanged, same single upsert | AC3 (real `evaluate_stop`/`reset_retry`, `cmp` of `STATE_FILE`); AC3b (label present → guard never reached) |
| 4. Refusal is not human feedback | AC4a (`has_new_comment_after_report` → `no`), AC4b (`get_new_comments` → `[]`), using the real functions |
| 5. `ready-for-human` removed on a non-cloud instance | AC5 with the real comment readers, plus a positive control (a real human comment still re-runs) |
| 6. Failing label call | AC6 (marker still posted; next poll is the same single upsert). AC6b adds the spec's failed-`mktemp` case |
| 7. Cloud | AC7 |
| 8. Unset byte-identical pin | AC8 (unset and empty × ledger present/absent/legacy × four paths; zero resolver calls, no ledger/run-record path passed to any CLI call). Sections G, I and AB stay unmodified. AB9 is unchanged |
| — (drift locks) | AC9a (exactly three guarded call sites), AC9b (helpers defined), AC9c (helpers name no run-writable store), AC9d (resolver reads `SOURCE_REGISTRY_DIR`) |

- [ ] **Step 2.1a: Section header, stubs, helpers, AC1–AC3b.** Insert this block
  verbatim immediately before the Cleanup header:

```bash
# ==========================================
# AC: Non-cloud endpoint guard — no auto-merge / grace-advance (#585)
# ==========================================
# cloud_endpoint_confirmed + refuse_local_routing gate dispatch_close (end-gate APPROVED and
# classifier MERGE) and both grace timers. The resolver (`model-endpoints hosting-check`) is
# stubbed: AC_HC_OUT/AC_HC_RC are its stdout/exit; it always writes a stderr sentinel that
# must never reach a comment. tracker comment --body-file is copied to AC_LAST_BODY.
# scheduler.sh's `set -e` is live here: a refusal step that can fail unguarded ends the run.
echo ""
echo "--- AC: non-cloud endpoint guard (#585) ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; CI_BLOCKED=""; REFINE_RUNNING=0
_AC_DIR=$(mktemp -d /tmp/sched-test-ac-XXXXXX)
AC_LAST_BODY="$_AC_DIR/last-body.md"
AC_HC_OUT=""; AC_HC_RC=1; AC_LABEL_RC=0; AC_REVIEW="APPROVED"; AC_COMMENTS='[]'
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *"providers/cli.py model-endpoints hosting-check"*)
      [ -n "$AC_HC_OUT" ] && printf '%s\n' "$AC_HC_OUT"
      echo "resolver-stderr-SENTINEL" >&2
      return "$AC_HC_RC" ;;
    *"providers/cli.py tracker label"*"--add"*) return "$AC_LABEL_RC" ;;
    *"providers/cli.py tracker comment"*)
      local _prev="" _a
      for _a in "$@"; do
        [ "$_prev" = "--body-file" ] && cp "$_a" "$AC_LAST_BODY"
        _prev="$_a"
      done
      return 0 ;;
    *"providers/cli.py tracker get-comments"*) printf '%s\n' "$AC_COMMENTS"; return 0 ;;
    *"providers/cli.py codehost reviews"*) printf '%s\n' "$AC_REVIEW"; return 0 ;;
    *providers/cli.py*) return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
evaluate_stop()     { echo "evaluate_stop $*" >> "$STUB_LOG"; echo "stopped=false reason=none"; }
trip_to_blocked()   { echo "trip_to_blocked $*" >> "$STUB_LOG"; }
dispatch()          { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_new_comments()  { echo '[{"id":"IC_585","author":{"login":"human"},"body":"merge it"}]'; }
classify_comments() { echo "classify_comments $1" >> "$STUB_LOG"; echo "MERGE"; }
get_pr_for_issue()  { echo "99"; }
is_issue_running()  { return 1; }
has_new_comment_after_report() { echo "no"; }
elapsed_minutes_since_marker() { echo "99"; }
export -f python3 evaluate_stop trip_to_blocked dispatch get_new_comments classify_comments \
  get_pr_for_issue is_issue_running has_new_comment_after_report elapsed_minutes_since_marker

_AC_SPEC_ITEM='{"content":{"number":803,"title":"t"},"labels":["direct-to-pr","spec-pending-review"],"status":"Backlog"}'
_AC_PLAN_ITEM='{"content":{"number":804,"title":"t"},"labels":["direct-to-pr","plan-pending-review"],"status":"Refined"}'
_ac_issue() { case "$1" in approved) echo 801 ;; merge) echo 802 ;; spec) echo 803 ;; plan) echo 804 ;; esac; }
# One poll of one guarded path. approved/merge go through stage_review_triage exactly as the
# poll loop does; spec/plan call the advance check exactly as stage_refine/stage_plan do.
_ac_path() {
  local _extra="${2:-}"
  DISPATCHED=""
  case "$1" in
    approved) IN_REVIEW="[{\"content\":{\"number\":801,\"title\":\"t\"},\"labels\":[\"direct-to-pr\"${_extra}],\"status\":\"In review\"}]"
              stage_review_triage ;;
    merge)    IN_REVIEW="[{\"content\":{\"number\":802,\"title\":\"t\"},\"labels\":[\"x\"${_extra}],\"status\":\"In review\"}]"
              stage_review_triage ;;
    spec)     spec_advance_check 803 "$(echo "$_AC_SPEC_ITEM" | jq -c ".labels += [${_extra#,}]")" ;;
    plan)     plan_advance_check 804 "$(echo "$_AC_PLAN_ITEM" | jq -c ".labels += [${_extra#,}]")" ;;
  esac
}
_ac_assert_refused() {  # <tag> <issue> <token>
  local tag="$1" n="$2" tok="$3"
  assert_eq "$tag: no dispatch" "0" "$(grep -c '^dispatch ' "$STUB_LOG" || true)"
  assert_eq "$tag: DISPATCHED stays empty" "" "$DISPATCHED"
  assert_eq "$tag: evaluate_stop not consulted" "0" "$(grep -c '^evaluate_stop' "$STUB_LOG" || true)"
  assert_eq "$tag: no trip_to_blocked" "0" "$(grep -c '^trip_to_blocked' "$STUB_LOG" || true)"
  assert_eq "$tag: no set_board_status" "0" "$(grep -c '^set_board_status' "$STUB_LOG" || true)"
  assert_eq "$tag: no label removed" "0" "$(grep -c -- '--remove' "$STUB_LOG" || true)"
  assert_eq "$tag: ready-for-human added once" \
    "1" "$(grep -c -- "tracker label --id $n --add ready-for-human" "$STUB_LOG" || true)"
  assert_eq "$tag: one marker upsert" \
    "1" "$(grep -cF -- "tracker comment --id $n --marker <!-- df-local-routing-refusal -->" "$STUB_LOG" || true)"
  assert_eq "$tag: no append-only comment" "0" "$(grep -c 'tracker add-comment' "$STUB_LOG" || true)"
  assert_eq "$tag: body carries the fixed token" "1" "$(grep -cF "(\`${tok}\`)" "$AC_LAST_BODY" || true)"
  assert_eq "$tag: body carries the scheduler footer" \
    "1" "$(grep -cxF "*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" "$AC_LAST_BODY" || true)"
  assert_eq "$tag: body carries the factory footer" \
    "1" "$(grep -cxF "*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*" "$AC_LAST_BODY" || true)"
  assert_eq "$tag: body carries the marker" \
    "1" "$(grep -cxF '<!-- df-local-routing-refusal -->' "$AC_LAST_BODY" || true)"
  assert_eq "$tag: no resolver text in the body" \
    "0" "$(grep -cE 'reason=|SENTINEL|lan-qwen|nope|Traceback' "$AC_LAST_BODY" || true)"
}
_ac_assert_proceeded() {  # <tag> <path>
  local tag="$1" p="$2" n
  n=$(_ac_issue "$p")
  case "$p" in
    approved|merge)
      assert_eq "$tag: Close dispatched" "1" "$(grep -c "^dispatch Close issue #${n}\$" "$STUB_LOG" || true)"
      assert_eq "$tag: close counter consulted" "1" "$(grep -c "^evaluate_stop ${n} close" "$STUB_LOG" || true)" ;;
    spec)
      assert_eq "$tag: gate label removed" "1" "$(grep -c -- '--remove spec-pending-review' "$STUB_LOG" || true)"
      assert_eq "$tag: moved to Refined" "1" "$(grep -cE "^set_board_status 803 ${FACTORY_STATUS_REFINED}( |\$)" "$STUB_LOG" || true)" ;;
    plan)
      assert_eq "$tag: gate label removed" "1" "$(grep -c -- '--remove plan-pending-review' "$STUB_LOG" || true)"
      assert_eq "$tag: moved to Ready" "1" "$(grep -cE "^set_board_status 804 ${FACTORY_STATUS_READY}( |\$)" "$STUB_LOG" || true)" ;;
  esac
  assert_eq "$tag: no ready-for-human" "0" "$(grep -c -- '--add ready-for-human' "$STUB_LOG" || true)"
  assert_eq "$tag: no refusal comment" "0" "$(grep -c 'df-local-routing-refusal' "$STUB_LOG" || true)"
}

# AC1/AC2: every non-cloud resolver outcome refuses on all four paths, with the full
# refusal assertion set. Fields: name|stdout|exit|expected token.
export FACTORY_MODEL_ENDPOINT=lan-qwen
_AC_CASES=(
  "local|hosting=local reason=endpoint 'lan-qwen' hosting_location=local|1|hosting=local"
  "unknown-id|hosting=unknown reason=Unknown FACTORY_MODEL_ENDPOINT 'nope' — no such id|1|hosting=unresolved"
  "unreadable-registry|hosting=unknown reason=/etc/dark-factory/model-registry/model-endpoints.yaml: unreadable (Permission denied)|1|hosting=unresolved"
  "invalid-registry|hosting=unknown reason=model-endpoints.yaml: schema_version must be a quoted string|1|hosting=unresolved"
  "crash-no-output||1|hosting=unresolved"
  "cloud-text-but-exit-1|hosting=cloud|1|hosting=unresolved"
  "exit-0-unexpected-output|hosting=cloudy|0|hosting=unresolved"
  "exit-0-empty-output||0|hosting=unresolved"
)
for _c in "${_AC_CASES[@]}"; do
  IFS='|' read -r _c_name _c_out _c_rc _c_tok <<< "$_c"
  AC_HC_OUT="$_c_out"; AC_HC_RC="$_c_rc"
  for _p in approved merge spec plan; do
    > "$STUB_LOG"; rm -f "$AC_LAST_BODY"
    _ac_path "$_p" 2>/dev/null
    _ac_assert_refused "AC1 ${_c_name}/${_p}" "$(_ac_issue "$_p")" "$_c_tok"
  done
done

# AC3: a second poll with the same refused state changes no counter (real evaluate_stop /
# reset_retry against $STATE_FILE) and makes the same single marker upsert — an in-place
# update, never a second comment.
AC_HC_OUT="hosting=local reason=x"; AC_HC_RC=1
eval "$_AB_REAL_EVALUATE_STOP"; export -f evaluate_stop
echo '{}' > "$STATE_FILE"
increment_retry "801:close"; increment_retry "803:refine"; increment_retry "804:plan"
cp "$STATE_FILE" "$_AC_DIR/state-before.json"
for _p in approved merge spec plan; do
  > "$STUB_LOG"
  _ac_path "$_p" 2>/dev/null; _ac_path "$_p" 2>/dev/null
  _n=$(_ac_issue "$_p")
  assert_eq "AC3 ${_p}: two polls → two upserts of the one marker comment" \
    "2" "$(grep -cF -- "tracker comment --id $_n --marker <!-- df-local-routing-refusal -->" "$STUB_LOG" || true)"
  assert_eq "AC3 ${_p}: two polls → no append-only comment" "0" "$(grep -c 'tracker add-comment' "$STUB_LOG" || true)"
  assert_eq "AC3 ${_p}: two polls → no dispatch" "0" "$(grep -c '^dispatch ' "$STUB_LOG" || true)"
done
assert_eq "AC3: STATE_FILE byte-identical after repeated refusals" \
  "same" "$(cmp -s "$_AC_DIR/state-before.json" "$STATE_FILE" && echo same || echo changed)"
evaluate_stop() { echo "evaluate_stop $*" >> "$STUB_LOG"; echo "stopped=false reason=none"; }
export -f evaluate_stop

# AC3b: once ready-for-human is on the item, no path reaches the guard at all.
for _p in approved merge spec plan; do
  > "$STUB_LOG"
  _ac_path "$_p" ',"ready-for-human"' 2>/dev/null
  assert_eq "AC3b ${_p}: ready-for-human → resolver not called" \
    "0" "$(grep -c 'hosting-check' "$STUB_LOG" || true)"
  assert_eq "AC3b ${_p}: ready-for-human → no refusal comment" \
    "0" "$(grep -c 'df-local-routing-refusal' "$STUB_LOG" || true)"
done
```

- [ ] **Step 2.1b: AC4–AC6b.** Append this block directly after Step 2.1a's block, still
  before the Cleanup header:

```bash
# AC4: the refusal is not human feedback — real has_new_comment_after_report and
# get_new_comments against a comment list whose last entry is the refusal body.
> "$STUB_LOG"; rm -f "$AC_LAST_BODY"
_ac_path spec 2>/dev/null
_AC_REFUSAL_BODY=$(cat "$AC_LAST_BODY" 2>/dev/null || true)
_AC_REPORT="## Refinement Pipeline — Spec Generated

---
*Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline*"
_AC_REAL_GNC=$(sed -n '/^get_new_comments() {/,/^}/p' "$SCHED")
_AC_LIB="$SCRIPT_DIR/../scripts/scheduler_lib.sh"
_AC_BOT_RE=$(python3 "$FACTORY_CORE_CLI" markers-regex)
AC_COMMENTS=$(jq -cn --arg r "$_AC_REPORT" --arg f "$_AC_REFUSAL_BODY" \
  '[{body:$r, createdAt:"2026-01-01T00:00:00Z"}, {body:$f, createdAt:"2026-01-01T01:00:00Z"}]')
assert_eq "AC4a: refusal after the report is not feedback (has_new_comment_after_report)" "no" \
  "$(source "$_AC_LIB"; BOT_RE="$_AC_BOT_RE"; has_new_comment_after_report 803 "Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline")"
AC_COMMENTS=$(jq -cn --arg f "$_AC_REFUSAL_BODY" \
  '[{body:"merge it", createdAt:"2026-01-01T00:00:00Z"}, {body:$f, createdAt:"2026-01-01T01:00:00Z"}]')
assert_eq "AC4b: nothing after the refusal is new input (get_new_comments)" "[]" \
  "$(eval "$_AC_REAL_GNC"; get_new_comments 802 | jq -c .)"

# AC5: a human removed ready-for-human while the instance is still non-cloud. Real comment
# readers (scheduler_lib.sh re-sourced) see [report, refusal]: no counter reset, no gate
# label removal, no Refine/Plan dispatch — the advance is refused again and the same marker
# comment is updated.
source "$_AC_LIB"; BOT_RE="$_AC_BOT_RE"
AC_COMMENTS=$(jq -cn --arg r "$_AC_REPORT" --arg f "$_AC_REFUSAL_BODY" \
  '[{body:$r, createdAt:"2026-01-01T00:00:00Z"}, {body:$f, createdAt:"2026-01-01T01:00:00Z"}]')
echo '{}' > "$STATE_FILE"; increment_retry "803:refine"; increment_retry "804:plan"
cp "$STATE_FILE" "$_AC_DIR/state-before.json"
for _p in spec plan; do
  > "$STUB_LOG"
  _ac_path "$_p" 2>/dev/null
  _n=$(_ac_issue "$_p")
  assert_eq "AC5 ${_p}: no gate label removed" "0" "$(grep -c -- '--remove' "$STUB_LOG" || true)"
  assert_eq "AC5 ${_p}: no Refine/Plan dispatch" "0" "$(grep -cE '^dispatch (Refine|Plan) issue' "$STUB_LOG" || true)"
  assert_eq "AC5 ${_p}: refused again (ready-for-human re-applied)" \
    "1" "$(grep -c -- "tracker label --id $_n --add ready-for-human" "$STUB_LOG" || true)"
  assert_eq "AC5 ${_p}: same marker comment updated" \
    "1" "$(grep -cF -- "tracker comment --id $_n --marker <!-- df-local-routing-refusal -->" "$STUB_LOG" || true)"
done
assert_eq "AC5: no retry counter reset" \
  "same" "$(cmp -s "$_AC_DIR/state-before.json" "$STATE_FILE" && echo same || echo changed)"
# Control: a real human comment after the refusal still re-runs refinement.
AC_COMMENTS=$(jq -cn --arg r "$_AC_REPORT" --arg f "$_AC_REFUSAL_BODY" \
  '[{body:$r, createdAt:"2026-01-01T00:00:00Z"}, {body:$f, createdAt:"2026-01-01T01:00:00Z"}, {body:"please tighten scope", createdAt:"2026-01-01T02:00:00Z"}]')
> "$STUB_LOG"
_ac_path spec 2>/dev/null
assert_eq "AC5 control: human comment after the refusal → Refine dispatched" \
  "1" "$(grep -c '^dispatch Refine issue #803$' "$STUB_LOG" || true)"
has_new_comment_after_report() { echo "no"; }
elapsed_minutes_since_marker() { echo "99"; }
export -f has_new_comment_after_report elapsed_minutes_since_marker
AC_COMMENTS='[]'; echo '{}' > "$STATE_FILE"

# AC6: a failing label call still posts the marker comment and never fails the caller. Plain
# calls on purpose: under the live `set -e` an unguarded failure aborts the suite here. The
# next poll (label still missing) is the same single upsert, as in AC3/AC5.
AC_LABEL_RC=1
for _p in approved merge spec plan; do
  _n=$(_ac_issue "$_p")
  > "$STUB_LOG"; rm -f "$AC_LAST_BODY"
  _ac_path "$_p" 2>/dev/null
  assert_eq "AC6 ${_p}: failed label call → marker comment still posted" \
    "1" "$(grep -cF -- "tracker comment --id $_n --marker <!-- df-local-routing-refusal -->" "$STUB_LOG" || true)"
  assert_eq "AC6 ${_p}: failed label call → no dispatch / board move" \
    "0" "$(grep -cE '^(dispatch|set_board_status) ' "$STUB_LOG" || true)"
  > "$STUB_LOG"
  _ac_path "$_p" 2>/dev/null
  assert_eq "AC6 ${_p}: next poll → the same single marker upsert" \
    "1" "$(grep -cF -- "tracker comment --id $_n --marker <!-- df-local-routing-refusal -->" "$STUB_LOG" || true)"
  assert_eq "AC6 ${_p}: next poll → no append-only comment" "0" "$(grep -c 'tracker add-comment' "$STUB_LOG" || true)"
done
AC_LABEL_RC=0
# AC6b: a failed mktemp returns without posting and without ending the scheduler (set -e).
mktemp() { return 1; }
for _p in spec plan; do
  > "$STUB_LOG"
  _ac_path "$_p" 2>/dev/null
  assert_eq "AC6b ${_p}: mktemp failure → no comment call" \
    "0" "$(grep -c 'tracker comment' "$STUB_LOG" || true)"
  assert_eq "AC6b ${_p}: mktemp failure → no advance" "0" "$(grep -c '^set_board_status' "$STUB_LOG" || true)"
done
unset -f mktemp
```

- [ ] **Step 2.1c: AC7–AC9 and stub restore.** Append this block directly after Step 2.1b's
  block. It ends with two blank lines, matching the spacing before the existing Cleanup
  header:

```bash
# AC7: a confirmed cloud endpoint behaves exactly like the unset case.
AC_HC_OUT="hosting=cloud"; AC_HC_RC=0
for _p in approved merge spec plan; do
  > "$STUB_LOG"
  _ac_path "$_p" 2>/dev/null
  _ac_assert_proceeded "AC7 cloud/${_p}" "$_p"
  assert_eq "AC7 cloud/${_p}: resolver called once" "1" "$(grep -c 'model-endpoints hosting-check --endpoint-id lan-qwen' "$STUB_LOG" || true)"
done

# AC8: endpoint unset or empty → byte-identical to today: zero resolver calls, and no ledger
# or run-record path is passed to any CLI call (AC9c locks the helpers statically), whether
# the request ledger is present, absent or holds legacy (pre-#558, no hosting_location) rows.
export MODEL_PROXY_LEDGER_PATH="$SCHEDULER_STATE_DIR/request-ledger.jsonl"
AC_HC_OUT="hosting=local reason=x"; AC_HC_RC=1   # would refuse if it were ever consulted
for _endpoint in unset empty; do
  if [ "$_endpoint" = unset ]; then unset FACTORY_MODEL_ENDPOINT; else export FACTORY_MODEL_ENDPOINT=""; fi
  for _ledger in present absent legacy; do
    case "$_ledger" in
      present) printf '%s\n' '{"run_id":"r1","issue_number":801,"hosting_location":"local","payload_class":"local_only"}' > "$MODEL_PROXY_LEDGER_PATH" ;;
      absent)  rm -f "$MODEL_PROXY_LEDGER_PATH" ;;
      legacy)  printf '%s\n' '{"run_id":"r0","issue_number":801}' > "$MODEL_PROXY_LEDGER_PATH" ;;
    esac
    for _p in approved merge spec plan; do
      > "$STUB_LOG"
      _ac_path "$_p" 2>/dev/null
      _ac_assert_proceeded "AC8 ${_endpoint}/${_ledger}/${_p}" "$_p"
      assert_eq "AC8 ${_endpoint}/${_ledger}/${_p}: zero resolver calls" "0" "$(grep -c 'hosting-check' "$STUB_LOG" || true)"
      assert_eq "AC8 ${_endpoint}/${_ledger}/${_p}: no ledger/run-record path passed to any CLI call" \
        "0" "$(grep -cE 'request-ledger|run-record|run_record|current-run|runs\.jsonl' "$STUB_LOG" || true)"
    done
  done
done
rm -f "$MODEL_PROXY_LEDGER_PATH"; unset MODEL_PROXY_LEDGER_PATH FACTORY_MODEL_ENDPOINT

# AC9: static drift locks — three guarded call sites, and the guard helpers name no
# run-writable store (owner decision 2).
assert_eq "AC9a: cloud_endpoint_confirmed guards exactly three call sites" \
  "3" "$(grep -cE '^[[:space:]]+if ! cloud_endpoint_confirmed; then$' "$SCHED")"
assert_eq "AC9b: both guard helpers are defined" \
  "function function" "$(type -t cloud_endpoint_confirmed || true) $(type -t refuse_local_routing || true)"
assert_eq "AC9c: guard helpers reference no run-writable store" \
  "0" "$(declare -f cloud_endpoint_confirmed refuse_local_routing | grep -cE 'ledger|run-record|run_record|current-run|runs\.jsonl' || true)"
assert_eq "AC9d: the resolver reads the scheduler's mount, not the staged copy" \
  "1" "$(grep -c 'model_endpoint.SOURCE_REGISTRY_DIR, args.endpoint_id' "$SCRIPT_DIR/../scripts/factory_core/providers/cli.py" || true)"

# Restore stubs to section defaults
reset_python3_stub
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
eval "$_AB_REAL_EVALUATE_STOP"; eval "$_AB_REAL_TRIP_TO_BLOCKED"
get_pr_for_issue() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
has_new_comment_after_report() { echo "no"; }
elapsed_minutes_since_marker() { echo ""; }
export -f python3 evaluate_stop trip_to_blocked get_pr_for_issue dispatch \
  has_new_comment_after_report elapsed_minutes_since_marker
rm -rf "$_AC_DIR"
> "$STUB_LOG"; echo '{}' > "$STATE_FILE"


```

  Notes for the implementer:
  - `FACTORY_PRODUCT_NAME` comes from `scripts/identity.sh`, and the real `cli.py marker`
    reads the same exported env. The footer assertions therefore hold whatever product name
    the container sets.
  - `IFS='|' read` splits the cases. No case text may contain `|`.
  - AC1, AC6 and AC6b call `_ac_path` plainly, never in a `||` context. Under the live
    `set -e`, a refusal step that can fail unguarded then aborts the suite instead of passing
    silently. Verified: dropping the `|| true` from the poster's `--add ready-for-human`
    line makes the suite exit non-zero at AC6.
  - AC4b and AC4a run the real `get_new_comments` / `has_new_comment_after_report` inside a
    `$(...)` subshell, so the section's stubs are not disturbed. AC5 re-sources
    `scripts/scheduler_lib.sh`, which defines only functions plus `SCHEDULER_LIB_DIR`, and
    then puts the `has_new_comment_after_report` / `elapsed_minutes_since_marker` stubs back.

- [ ] **Step 2.2: Run the suite and confirm the new section fails.**

```bash
PYTHONPATH=scripts bash tests/test_scheduler.sh > /tmp/sched-ac.out 2>&1; echo "rc=$?"; tail -1 /tmp/sched-ac.out
grep -c 'FAIL: AC' /tmp/sched-ac.out
grep 'FAIL:' /tmp/sched-ac.out | grep -vc 'FAIL: AC'
```

Expected: `rc=1`, then `Results: 638 passed, 342 failed`, then `342` FAILs, all in AC. The
last count is `0`: nothing in A–AB fails. Every refusal assertion fails because nothing is
refused yet. AC9a reports `got='0'` and AC9b reports `got=' '`. These numbers assume Task 1
is already committed; without its `cli.py` change, AC9d also fails.

- [ ] **Step 2.3: Commit the failing tests.**

```bash
git add tests/test_scheduler.sh
git commit -m "test(#585): section AC — non-cloud endpoint guard on merge and grace paths (failing first)"
```

---

## Task 3: Guard helpers + `dispatch_close` call site

**Files:**
- Modify: `scheduler.sh`

- [ ] **Step 3.1: Add the two helpers.** Insert this block immediately **above** the existing
  line `# --- Close dispatch: running-issue guard + close-phase circuit breaker (#414) ---`
  (currently line 359). That keeps both helpers beside `dispatch_close` and before the
  `SCHEDULER_SOURCE_ONLY` guard, which the tests need. The block ends with one blank line:

```bash
# --- Non-cloud endpoint guard (#585) ---
# hybrid-execution-contract Invariant 7: work is never auto-merged or auto-advanced while
# this instance's own model endpoint isn't confirmed cloud-hosted. Scheduler-side and
# instance-wide: reads $FACTORY_MODEL_ENDPOINT and the read-only registry mount only, never
# a run-writable file (request ledger, run records, current-run.json, runs.jsonl). Unset
# endpoint short-circuits before any subprocess. Only exit 0 with stdout exactly
# `hosting=cloud` passes; the resolver's stderr and reason= text are discarded.
cloud_endpoint_confirmed() {
  if [ -z "${FACTORY_MODEL_ENDPOINT:-}" ]; then
    return 0
  fi
  local out="" rc=0
  out=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints hosting-check \
       --endpoint-id "$FACTORY_MODEL_ENDPOINT" 2>/dev/null) || rc=$?
  if [ "$rc" -eq 0 ] && [ "$out" = "hosting=cloud" ]; then
    return 0
  fi
  case "$out" in
    "hosting=local "*) LOCAL_ROUTING_REFUSAL_REASON="hosting=local" ;;
    *) LOCAL_ROUTING_REFUSAL_REASON="hosting=unresolved" ;;
  esac
  echo "[$(date -u +%FT%TZ)] local_routing_gate=refuse ${LOCAL_ROUTING_REFUSAL_REASON} rc=${rc}" >&2
  return 1
}

# Refusal poster for the three guarded call sites: ready-for-human, then one marker-deduped
# comment (tracker comment --marker upserts — a later refusal PATCHes the same comment).
# Both identity footers are load-bearing: has_new_comment_after_report and get_new_comments
# tell factory comments from human feedback by footer only. Every step is best-effort and
# never fails the caller (the grace call sites are not inside an `if`, so an unguarded
# failure would end the scheduler under `set -e`); none lets the merge/advance proceed.
# The body is fixed text plus one fixed token — no worker-controlled value.
refuse_local_routing() {
  local issue_num="$1" action="$2"  # action: merge | spec-advance | plan-advance
  local reason="${LOCAL_ROUTING_REFUSAL_REASON:-hosting=unresolved}"
  echo "[$(date -u +%FT%TZ)] local_routing_gate=refuse issue=#${issue_num} action=${action} ${reason}"
  python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" --add ready-for-human || true
  local f_sched f_fact
  f_sched=$(python3 "$FACTORY_CORE_CLI" marker scheduler 2>/dev/null) \
    || f_sched="*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*"
  f_fact=$(python3 "$FACTORY_CORE_CLI" marker factory 2>/dev/null) \
    || f_fact="*Posted by ${FACTORY_PRODUCT_NAME} Dark Factory*"
  local body
  body=$(cat <<EOF
## Scheduler — automated ${action} refused

This instance's selected model endpoint is not confirmed cloud-hosted
(\`${reason}\`). Per \`docs/hybrid-execution-contract.md\` Invariant 7,
work is never auto-merged or auto-advanced while that's true — a human must take this action
directly.

---
${f_sched}
${f_fact}
<!-- df-local-routing-refusal -->
EOF
)
  local tmpfile
  tmpfile=$(mktemp /tmp/local-routing-refusal-XXXXXX.md) || return 0
  echo "$body" > "$tmpfile" || { rm -f "$tmpfile"; return 0; }
  python3 "$FACTORY_PROVIDERS_CLI" tracker comment --id "$issue_num" \
    --marker "<!-- df-local-routing-refusal -->" --body-file "$tmpfile" 2>/dev/null || true
  rm -f "$tmpfile"
}
```

  Two small hardenings over the spec's sketch, both purely more conservative:
  - `echo "$body" > "$tmpfile" || { rm -f "$tmpfile"; return 0; }` extends the spec's
    failed-`mktemp` rule ("can no longer end the scheduler under `set -e`") to the write.
  - `${LOCAL_ROUTING_REFUSAL_REASON:-hosting=unresolved}` keeps `set -u` from aborting if
    the poster is ever called without the predicate.
  The extra stdout log line `local_routing_gate=refuse issue=#N action=…` tells an operator
  which ticket was refused. The predicate's own stderr line carries no issue number.

- [ ] **Step 3.2: Guard `dispatch_close`.** In `dispatch_close`, replace

```bash
  is_issue_running "$issue_num" && return 1
  local eval_result
  eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")
```

  with

```bash
  is_issue_running "$issue_num" && return 1
  # #585: before evaluate_stop, so a refusal never consumes the N:close counter.
  if ! cloud_endpoint_confirmed; then
    refuse_local_routing "$issue_num" "merge"
    return 1
  fi
  local eval_result
  eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")
```

  Do **not** write `dispatch_close <word>` or `dispatch "Close issue` anywhere in new
  non-comment code. AB9a/AB9b count those, and they must stay at 1 and 2.

- [ ] **Step 3.3: Syntax check and run.**

```bash
bash -n scheduler.sh && echo syntax-ok
PYTHONPATH=scripts bash tests/test_scheduler.sh > /tmp/sched-ac.out 2>&1; echo "rc=$?"; tail -1 /tmp/sched-ac.out
grep -c 'FAIL: AC1 .*/\(approved\|merge\):' /tmp/sched-ac.out
```

Expected: `syntax-ok`, `rc=1`, `Results: 812 passed, 168 failed`, then `0`. Both merge paths
are now refused correctly. The remaining FAILs are the spec/plan paths, AC4a/AC4b (no grace
refusal body yet) and AC9a (`got='1'`).

- [ ] **Step 3.4: Commit.**

```bash
git add scheduler.sh
git commit -m "feat(#585): refuse auto-merge in dispatch_close while the endpoint is not confirmed cloud"
```

---

## Task 4: Guard both grace-timer advances

**Files:**
- Modify: `scheduler.sh`

- [ ] **Step 4.1: Guard `spec_advance_check`.** Inside its
  `if [ -n "$elapsed" ] && [ "$elapsed" -ge "$SPEC_GRACE_MINUTES" ]; then` branch, replace

```bash
      echo "[$(date -u +%FT%TZ)] spec_auto_advance issue=#${issue_num} elapsed=${elapsed}m grace=${SPEC_GRACE_MINUTES}m action=advance_to_refined"
      python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" \
        --remove "spec-pending-review" || true
      set_board_status "$issue_num" "$FACTORY_STATUS_REFINED" || true
```

  with

```bash
      if ! cloud_endpoint_confirmed; then
        refuse_local_routing "$issue_num" "spec-advance"
      else
        echo "[$(date -u +%FT%TZ)] spec_auto_advance issue=#${issue_num} elapsed=${elapsed}m grace=${SPEC_GRACE_MINUTES}m action=advance_to_refined"
        python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" \
          --remove "spec-pending-review" || true
        set_board_status "$issue_num" "$FACTORY_STATUS_REFINED" || true
      fi
```

- [ ] **Step 4.2: Guard `plan_advance_check`.** Inside its
  `if [ -n "$elapsed" ] && [ "$elapsed" -ge "$PLAN_GRACE_MINUTES" ]; then` branch, replace

```bash
      echo "[$(date -u +%FT%TZ)] plan_auto_advance issue=#${issue_num} elapsed=${elapsed}m grace=${PLAN_GRACE_MINUTES}m action=advance_to_ready"
      python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" \
        --remove "plan-pending-review" || true
      set_board_status "$issue_num" "$FACTORY_STATUS_READY" || true
```

  with

```bash
      if ! cloud_endpoint_confirmed; then
        refuse_local_routing "$issue_num" "plan-advance"
      else
        echo "[$(date -u +%FT%TZ)] plan_auto_advance issue=#${issue_num} elapsed=${elapsed}m grace=${PLAN_GRACE_MINUTES}m action=advance_to_ready"
        python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num" \
          --remove "plan-pending-review" || true
        set_board_status "$issue_num" "$FACTORY_STATUS_READY" || true
      fi
```

  Leave the feedback-re-run branch, the `has_skip_label` early return, the dispatch-ceiling
  check and the `…_grace_window … action=waiting` `else` untouched. The
  `spec-pending-review` / `plan-pending-review` label and board status stay as they are on
  refusal.

  If PR #618 has merged, each advance block has five lines (a `# #607:` comment and a third
  `set_board_status` argument), so the two "replace" blocks above match nothing. Wrap exactly
  the lines that are there, unchanged and indented two more spaces, in the same
  `if ! cloud_endpoint_confirmed; then … else … fi`. Never drop the third argument.

- [ ] **Step 4.3: Run the scheduler suite and confirm it passes.**

```bash
bash -n scheduler.sh && echo syntax-ok
PYTHONPATH=scripts bash tests/test_scheduler.sh > /tmp/sched-ac.out 2>&1; echo "rc=$?"; tail -1 /tmp/sched-ac.out
grep -nE '^spec_advance_check\(\)|^plan_advance_check\(\)|^stage_epic_autopilot\(\)' scheduler.sh
awk 'NR>=248 && /^}/ {print NR; n++} n==2{exit}' scheduler.sh
```

Expected: `syntax-ok`, `rc=0`, `Results: 980 passed, 0 failed`. Then
`248:spec_advance_check() {`, `288:plan_advance_check() {`,
`1497:stage_epic_autopilot() {`, and the closing braces at `286` and `331`. Task 5 uses
these numbers. If they differ, the helper block or call sites were not inserted exactly as
written; fix that, don't adjust the docs to match.

- [ ] **Step 4.4: Commit.**

```bash
git add scheduler.sh
git commit -m "feat(#585): refuse spec/plan grace-advance while the endpoint is not confirmed cloud"
```

---

## Task 5: Docs — `docs/scheduler.md` and `docs/triage-labels.md`

**Files:**
- Modify: `docs/scheduler.md`
- Modify: `docs/triage-labels.md`

Spec requirement 8 says to refresh every `scheduler.sh:` line reference in
`docs/scheduler.md` that shifts because of this change. Tasks 3–4 shift everything past
line 273: +4 after the spec grace block, +8 after the plan grace block, +73 after the helper
block and +78 past the `dispatch_close` guard. The CI check `scripts/docs_refs_check.py`
validates only that a cited line is in range, so it would not catch the drift. Step 5.2
therefore remaps every citation mechanically from a line map computed over the
`scheduler.sh` diff. It runs **first**, before any prose is added, and exactly once (the
script refuses a second run). It does not try to fix citations that were already stale on
`main`, such as `get_new_comments` `:761-784`. Those keep their old offset, which preserves
their relation to the code without changing their accuracy.

- [ ] **Step 5.1: Write the failing doc check.** These greps assert the new content and
  currently print `0` (or fail):

```bash
grep -c 'Non-cloud endpoint guard (#585)' docs/scheduler.md
grep -c 'scheduler.sh:248-286' docs/scheduler.md
grep -c 'scheduler.sh:333-365' docs/scheduler.md
grep -c 'scheduler.sh:1555' docs/scheduler.md
grep -c 'refuses an automated merge' docs/triage-labels.md
```

Expected now: `0` for each.

- [ ] **Step 5.2: Remap every `scheduler.sh` citation (run first, once).** Save this
  script as `/tmp/remap_scheduler_cites.py`. It is a throwaway outside the repo; do not
  commit it:

```python
# #585 Task 5: remap every scheduler.sh line citation in docs/scheduler.md from the
# merge-base's scheduler.sh to this branch's. Line map: difflib over the two files; lines inside a
# replaced block map to the new line with the same stripped text. Bare `:N` spans are
# scheduler.sh citations in this doc, except the two epic_autopilot.py spans masked below.
import difflib
import re
import subprocess
import sys

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


base = git("merge-base", "origin/main", "HEAD").strip()
# Run once: the doc must still be the merge-base copy, or a second run would shift twice.
if git("diff", base, "--", "docs/scheduler.md"):
    sys.exit("docs/scheduler.md already differs from the merge-base; remap must run first, once")
old = git("show", f"{base}:scheduler.sh").split("\n")
new = open("scheduler.sh", encoding="utf-8").read().split("\n")
line_map = {}
for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
    if tag == "equal":
        for k in range(i2 - i1):
            line_map[i1 + k + 1] = j1 + k + 1
    elif tag == "replace":
        for i in range(i1, i2):
            hits = [j for j in range(j1, j2) if new[j].strip() == old[i].strip()]
            if len(hits) != 1:
                sys.exit(f"ambiguous replaced line {i + 1}: {old[i]!r}")
            line_map[i + 1] = hits[0] + 1
    elif tag == "delete":
        sys.exit(f"unexpected deletion of main lines {i1 + 1}-{i2}")


def remap_span(span):
    def one(m):
        n = int(m.group(0))
        if n not in line_map:
            sys.exit(f"cited line {n} has no counterpart")
        return str(line_map[n])
    return re.sub(r"\d+", one, span)


doc_path = "docs/scheduler.md"
doc = open(doc_path, encoding="utf-8").read()
# Bare spans that cite epic_autopilot.py, not scheduler.sh: never remapped.
masks = ["(`epic_autopilot.py:427`, `:661`)", "(`:383`, `:397`), so reading the target's adapter"]
for i, m in enumerate(masks):
    if doc.count(m) != 1:
        sys.exit(f"mask not found exactly once: {m!r}")
    doc = doc.replace(m, f"\0MASK{i}\0")
changed = 0


def cite(m):
    global changed
    out = f"`{m.group(1)}:{remap_span(m.group(2))}`"
    changed += out != m.group(0)
    return out


doc = re.sub(r"`(scheduler\.sh|):(\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*)`", cite, doc)
for i, m in enumerate(masks):
    doc = doc.replace(f"\0MASK{i}\0", m)
open(doc_path, "w", encoding="utf-8").write(doc)
print(f"remapped {changed} citation(s)")
```

  Run it from the repo root after Task 4's `scheduler.sh` is final. It needs `origin/main`
  fetched:

```bash
python3 /tmp/remap_scheduler_cites.py
git diff --stat docs/scheduler.md
sed -n '48p;246p;249p;549,550p;587p;723p' docs/scheduler.md | cut -c1-120
```

  Expected: `remapped 133 citation(s)`, then `docs/scheduler.md | 218 ++++----`
  (109 insertions, 109 deletions: only citation tokens change). The sampled lines then show
  `scheduler.sh:1555`, `scheduler.sh:333-365`, `dispatch_close` (`:441`),
  `scheduler.sh:248-286`, `:288-331`, `scheduler.sh:1497-1503`, and the grace-timer log
  row `:277-283`, `:322-328`. The two `epic_autopilot.py` spans, `(`epic_autopilot.py:427`,
  `:661`)` and `(`:383`, `:397`)`, are unchanged. If the script exits with `ambiguous`,
  `no counterpart` or `unexpected deletion`, `scheduler.sh` does not match Tasks 3–4. Fix
  the code, don't hand-edit citations.

- [ ] **Step 5.3: Add the grace guard as item 3.** In the `### Grace-timer auto-advance for the spec and plan gates` subsection, replace

```markdown
   For plans the ticket must also pass `is_below_ceiling` when the dispatch ceiling is
   enabled.
```

  with

```markdown
   For plans the ticket must also pass `is_below_ceiling` when the dispatch ceiling is
   enabled.
3. **Non-cloud endpoint guard (#585).** Before a grace advance, `cloud_endpoint_confirmed`
   checks this instance's own `FACTORY_MODEL_ENDPOINT`. Unset or empty: no check, no
   subprocess, behaviour as before. Set: `providers/cli.py model-endpoints hosting-check`
   resolves it against the scheduler's read-only registry mount
   (`/etc/dark-factory/model-registry`, the same `scheduler_preflight()` resolution as the
   boot preflight). Only exit 0 with the exact output `hosting=cloud` lets the advance
   proceed. A local entry, an unknown id, an unreadable or invalid registry, or any other
   resolver result refuses it (`docs/hybrid-execution-contract.md` Invariant 7):
   `refuse_local_routing` adds `ready-for-human` and upserts one comment marked
   `<!-- df-local-routing-refusal -->` that names a fixed token, `hosting=local` or
   `hosting=unresolved`, and carries the scheduler and factory footers so the comment
   readers never take it for feedback. The gate label and board status are left alone; no
   retry counter changes. Log line: `local_routing_gate=refuse`. No run-writable file
   (request ledger, run records, `current-run.json`, `runs.jsonl`) is read.
```

- [ ] **Step 5.4: Point at the new test section.** Replace

```markdown
`tests/test_scheduler.sh` sections G and I pin these paths.
```

  with

```markdown
`tests/test_scheduler.sh` sections G and I pin these paths; section AC pins the
non-cloud guard.
```

- [ ] **Step 5.5: Extend the End-gate subsection.** In
  `### End gate and auto-merge with \`direct-to-pr\``, replace

```markdown
is done by that run. For tickets without the label, `Close` is dispatched only when the
comment classifier returns `MERGE`.
```

  with

```markdown
is done by that run. For tickets without the label, `Close` is dispatched only when the
comment classifier returns `MERGE`.

Both paths go through `dispatch_close`, which runs the same non-cloud endpoint guard as the
grace timers (#585), after the running-issue check and before `evaluate_stop`, so a refused
merge never counts on `N:close` and never trips the breaker. On refusal the PR stays open,
the ticket stays In review with `ready-for-human`, and the marker comment above is posted
or updated.

The guard is instance-wide and has no memory. Per `docs/hybrid-execution-contract.md`
Invariant 7 (§4.3, §4.11 decision 5), work routed to a local endpoint is never merged or
advanced without a human; this guard enforces that from the scheduler's own endpoint
selection only. Accepted residual: once an instance is switched from a local endpoint
back to a cloud one (or to unset), work done while it was local is no longer blocked by
this guard. Per-issue evidence is deliberately not used: the request ledger and run records
sit on a volume run containers write, and unattributable correlation is recorded as
`unknown` (Invariant 10, §4.4), so neither could prove that a ticket was never local-routed.
The boot preflight (`scheduler.sh:45`) already stops the scheduler for a non-cloud
selection, so in production the refusal is reached only when the registry changes or
becomes unreadable after boot.

To clear a refusal, a human merges the PR directly on GitHub, or (at a spec or plan gate)
moves the card and removes the gate label, and then removes `ready-for-human`. Removing only
`ready-for-human` while the instance is still non-cloud is refused again on the next poll
that reaches the guard, and the same marker comment is updated.
```

- [ ] **Step 5.6: Update `docs/triage-labels.md` line 10** (the triage-role table). Replace
  the row

```markdown
| `ready-for-human`          | `ready-for-human`    | Requires human implementation. The scheduler skips the ticket at every stage (refine, plan, dispatch, retry, rescue, orphan sweep, grace advance), and epic autopilot will not advance it |
```

  with

```markdown
| `ready-for-human`          | `ready-for-human`    | Requires human implementation. The scheduler skips the ticket at every stage (refine, plan, dispatch, retry, rescue, orphan sweep, grace advance), and epic autopilot will not advance it. Also applied by the scheduler when it refuses an automated merge or advance because this instance's model endpoint is not confirmed cloud-hosted (#585); to clear it, merge the PR (or pass the spec/plan gate) by hand, then remove the label |
```

- [ ] **Step 5.7: Update `docs/triage-labels.md` line 62** (the factory label table).
  Replace the row

```markdown
| `ready-for-human` | Humans | Scheduler parks the ticket at every stage, including refine and plan; epic autopilot will not advance it |
```

  with

```markdown
| `ready-for-human` | Humans; the scheduler when it refuses an automated merge or grace advance on a non-cloud model endpoint (#585) | Scheduler parks the ticket at every stage, including refine and plan; epic autopilot will not advance it. Removing it while the instance is still non-cloud is refused again on the next poll that reaches the guard (see `docs/scheduler.md`, End gate) |
```

- [ ] **Step 5.8: Verify.**

```bash
grep -c 'Non-cloud endpoint guard (#585)' docs/scheduler.md
grep -c 'scheduler.sh:248-286' docs/scheduler.md
grep -c 'scheduler.sh:333-365' docs/scheduler.md
grep -c 'scheduler.sh:1555' docs/scheduler.md
grep -c 'refuses an automated merge' docs/triage-labels.md
grep -c 'Invariant 4' docs/scheduler.md docs/triage-labels.md
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: `1`, `1`, `1`, `1`, `2`, then `docs/scheduler.md:0` and `docs/triage-labels.md:0` (no
wrong Invariant-4 citation), then `Docs reference check passed for 4 doc(s).`

- [ ] **Step 5.9: Commit.**

```bash
git add docs/scheduler.md docs/triage-labels.md
git commit -m "docs(#585): non-cloud endpoint guard on merge and grace paths; remap scheduler.sh citations; ready-for-human now also scheduler-applied"
```

---

## Task 6: Full verification and scope check

**Files:** none modified. Verification only.

- [ ] **Step 6.1: Full pytest.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

Expected: all pass. On `cf38d4d` the run shows `3748 passed, 2 skipped`. The exact count moves with
`main`, but there must be 0 failures.

- [ ] **Step 6.2: CI shell suites and DAG checks touched by this change.**

```bash
PYTHONPATH=scripts bash tests/test_scheduler.sh | tail -1
bash tests/test_scheduler_lib.sh > /dev/null && echo lib-ok
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml > /dev/null && echo dag-ok
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml > /dev/null && echo when-ok
```

Expected: `Results: 980 passed, 0 failed`, `lib-ok`, `dag-ok`, `when-ok`.
(`tests/test_identity.sh` and `tests/test_provider_env_flags.sh` fail in a run container
whose env overrides the identity/provider defaults. They do so identically on unmodified
`main` and are unrelated. CI runs them with clean env.)

- [ ] **Step 6.3: Scope check.** Only the six planned files differ from `main`:

```bash
git diff --name-only origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory' | sort
```

Expected exactly:

```
docs/scheduler.md
docs/triage-labels.md
scheduler.sh
scripts/factory_core/providers/cli.py
tests/test_provider_model_endpoint.py
tests/test_scheduler.sh
```

- [ ] **Step 6.4: Safety spot-checks.**

```bash
git diff origin/main HEAD -- scheduler.sh | grep -E '^-' | grep -vE '^---'
git diff origin/main HEAD -- scheduler.sh | grep -E '^\+' | grep -vE '^\+\+\+|^\+[[:space:]]*#' | grep -cE 'ledger|run-record|run_record|current-run|runs\.jsonl|_NO_LOCAL_DISPATCH'
```

Expected: exactly eight removed lines (ten if PR #618 has merged), the original grace-advance lines from each of
`spec_advance_check` and `plan_advance_check`. They reappear re-indented inside the new
`else` branches. The `dispatch_close` change is insertion-only. The second command, which
checks added non-comment lines only (the helper's header comment names those stores on
purpose), prints `0`.

- [ ] **Step 6.5: PR notes (for the implement phase's PR body).** State: never
  `direct-to-pr`; `scheduler.sh` hotspot, so a human signature is required; the accepted
  residual (no memory across a local→cloud switch); that on `main` the boot preflight
  already exits on a non-cloud selection, so the refusal branches are production-reachable
  only after a post-boot registry change; and that #615/#616 remain separate; and that the marker upsert
  (`board.post_or_update_comment`) reads only the first 30 comments and creates a new comment
  when its lookup fails, so a follow-up ticket must paginate it before the dedup guarantee
  holds on long tickets. Also note
  as spillover that `docs/features.md`, `docs/configuration.md` and `docs/run-lifecycle.md`
  also cite `scheduler.sh` lines that this change shifts (41 explicit `scheduler.sh:N`
  citations past line 273, plus bare `:N` spans). They are outside
  this spec's file list, so they are left for a follow-up docs ticket.

---

## Self-review

- `**Issue:** #585` line present under the title. ✔
- No placeholders: every step has the exact code or text and a command with expected output.
  Line numbers in Task 5 come from the Step 4.3 check, not from estimates. ✔
- Spec coverage. Requirement 1 (signal, unset = no subprocess) is Tasks 1 and 3, tested by
  AC1/AC7/AC8. Requirement 2 (no run-writable file) is AC8 and AC9c. Requirement 3 (legacy
  rows) is AC8 `legacy`. Requirement 4 (grace in scope) is Task 4. Requirement 5 (out of
  scope) is the Constraints list and Step 6.3. Requirement 6 (before `evaluate_stop`,
  marker dedup, no worker text, `ready-for-human`, footers) is Step 3.2 and Step 3.1, tested
  by AC1–AC6. Requirement 7 (tests) is Tasks 1–2. Requirement 8 (docs, Invariants 7/10,
  every shifted `scheduler.sh` line ref in `docs/scheduler.md`) is Task 5. Requirement 9 (safety) is Step 6.4 and the PR notes. ✔

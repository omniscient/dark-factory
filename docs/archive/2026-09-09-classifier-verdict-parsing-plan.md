# Implementation Plan: Fix scheduler comment-classifier verdict parsing, caching, and log noise

**Issue:** #402
**Operator plan gate:** 2026-09-09 — approved with amendments from an independent read-only review. Blocking finding F-1 (first-line-only MERGE-strict let `MERGE\nActually, skip this` through as a real merge, because the ambiguity scan is case-sensitive) is fixed here by scoping MERGE-strict to the whole response, with three regression fixtures. Advisory notes carried into implementation: `grep -oP` needs GNU grep (fine on ubuntu:26.04 and CI, fails on BSD/macOS greps); `set +e — set -e` in `parse_comment_verdict` restores errexit unconditionally (safe today because every call site is a `$( )` subshell); the `state-set` calls have no `|| true`, so an unwritable state file would kill a poll cycle on an optional cache write.
**Spec:** `docs/superpowers/specs/2026-09-08-classifier-verdict-parsing-design.md`
**Operator review:** amendments F1-F7 plus the MERGE-strict and ambiguity rules (commit `b94e83c`) are incorporated below.

---

## Goal

`classify_comments()` (`scheduler.sh`) destroys the token/justification boundary in the
model's reply (`tr -d '[:space:]'`) before matching it against MERGE/CONTINUE/SKIP, so any
"TOKEN + explanation" reply falls back to an uncached SKIP — 47 times in 48h on issue #394.
Replace the destroy-then-match approach with an anchored, case-insensitive regex extracted
into a standalone `parse_comment_verdict()` function; cache only successfully parsed
verdicts per (issue, latest-comment-id) in `scheduler-state.json`; split fallback logging
into an always-on compact line plus a deduplicated raw-response dump; tighten the prompt.
No behavior change to the MERGE/CONTINUE/SKIP dispatch semantics in `stage_review_triage`.

## Architecture

```
scripts/factory_core/breaker.py
  get_state_str(key, state_file) -> str | None     (new — generic string read)
  set_state_str(key, value, state_file)            (new — generic string write)
  reset_retry(key, state_file)                     (modified — pops :cverdict/:cid/:crawlog too)
        │ built on existing _read_state / _atomic_write (same as _write_signature_key)
        ▼
scripts/factory_core/cli.py
  state-get --key       (new subcommand — validates key, prints value or nothing)
  state-set --key --value  (new subcommand — validates key, writes value)
        │ both reject any --key not matching ^[0-9]+:(cverdict|cid|crawlog)$
        ▼
scheduler.sh
  parse_comment_verdict(response) -> echoes MERGE|CONTINUE|SKIP or nothing   (new, standalone)
  classify_comments(issue_num, title, comments_json)                        (rewritten)
    1. read <issue>:cid via `cli.py state-get`; if it matches the latest comment id AND
       <issue>:cverdict is non-empty, return the cached verdict (no `claude -p` call)
    2. otherwise call `claude -p --model haiku` with the tightened prompt, stdout and
       stderr captured separately
    3. non-zero exit / empty stdout -> two-tier fallback logging, return SKIP uncached
    4. parse_comment_verdict(stdout) -> match: log + cache (`state-set` x2) + return token
                                      -> no match: two-tier fallback logging, return SKIP uncached
  _log_raw_fallback_once(issue_num, latest_id, raw, err_tail)                (new, standalone)
    reads/writes <issue>:crawlog via `cli.py state-get`/`state-set` to dedupe the raw-response
    dump per (issue, latest-comment-id), independent of the :cverdict cache — called from both
    fallback branches of classify_comments (steps 3 and 4 above)
```

Cache/dedup keys depend on each comment object carrying an `id` field (`.[-1].id` in
`classify_comments`) — true today for both the GitHub tracker (`gh issue view --json
comments` includes per-comment `id`) and the Jira tracker (native comment `id`). Every
cache read/write is guarded by `[ -n "$latest_id" ]`, so a tracker that ever omitted `id`
would silently disable caching (classification still runs, just uncached) rather than
error — fail-soft by construction, not a new failure mode.

## Tech Stack

- Bash (`scheduler.sh`) — `[[ =~ ]]` with the pattern held in a variable, `grep -oP`
  (PCRE lookaround) for the ambiguity scan — matches this file's existing GNU/bash-specific
  conventions (e.g. the codebase's own note that `\b` is already a glibc extension).
- Python stdlib (`scripts/factory_core/breaker.py`, `cli.py`) — no new dependency.
- `pytest` for the parser-fixture and breaker/CLI tests (existing framework, CI-run via
  `python -m pytest tests/ -v`). Bash (`tests/test_scheduler.sh`, existing stub harness)
  for orchestration tests — not CI-enforced today (pre-existing gap, out of scope per spec's
  Open Questions).

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/breaker.py` | **Modified** — add `get_state_str`/`set_state_str`; extend `reset_retry()`'s pop list |
| `tests/test_factory_core_breaker.py` | **Modified** — round-trip tests for the new helpers + `reset_retry` pop coverage |
| `scripts/factory_core/cli.py` | **Modified** — add `state-get`/`state-set` subcommands with key-shape validation |
| `tests/test_factory_core_cli.py` | **Modified** — round-trip + invalid-key-rejection tests for the new subcommands |
| `scheduler.sh` | **Modified** — new `parse_comment_verdict()` and `_log_raw_fallback_once()`; rewritten `classify_comments()` |
| `tests/test_scheduler_comment_verdict.py` | **New** — parser fixture tests (pytest, drives the bash function via subprocess) |
| `tests/test_scheduler.sh` | **Modified** — orchestration tests (cache hit/miss/bust, stderr doesn't defeat parse) |

---

## Task 1: `get_state_str` / `set_state_str` in `breaker.py`

Generic string-valued get/set pair, built on the existing private `_read_state`/
`_atomic_write` helpers (the same ones `_write_signature_key` already uses) — not a
duplicate implementation.

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

### Steps

1. Write the failing test. Append to `tests/test_factory_core_breaker.py`:

   ```python
   from factory_core.breaker import get_state_str, set_state_str


   def test_get_state_str_missing_key_returns_none(tmp_path):
       assert get_state_str("42:cverdict", tmp_path / "state.json") is None


   def test_set_then_get_state_str_round_trip(tmp_path):
       sf = tmp_path / "state.json"
       set_state_str("42:cverdict", "CONTINUE", sf)
       assert get_state_str("42:cverdict", sf) == "CONTINUE"


   def test_set_state_str_does_not_disturb_unrelated_keys(tmp_path):
       sf = tmp_path / "state.json"
       increment_retry("42:refine", sf)
       set_state_str("42:cverdict", "SKIP", sf)
       assert get_retry_count("42:refine", sf) == 1
       assert get_state_str("42:cverdict", sf) == "SKIP"
   ```

2. Verify it fails (the import itself fails — `get_state_str` doesn't exist yet):

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -k state_str -v
   ```
   Expected: `ImportError: cannot import name 'get_state_str'`.

3. Implement. In `scripts/factory_core/breaker.py`, add directly above `_write_signature_key`
   (so it sits next to the helper it generalizes):

   ```python
   def get_state_str(key: str, state_file: Path = _DEFAULT_STATE) -> Optional[str]:
       val = _read_state(state_file).get(key)
       return str(val) if val is not None else None


   def set_state_str(key: str, value: str, state_file: Path = _DEFAULT_STATE) -> None:
       state_file.parent.mkdir(parents=True, exist_ok=True)
       data = _read_state(state_file)
       data[key] = value
       _atomic_write(state_file, data)
   ```

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -k state_str -v
   ```
   Expected: `3 passed`.

5. Commit:

   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(#402): add get_state_str/set_state_str to breaker.py"
   ```

---

## Task 2: `reset_retry()` pops the new cache/dedup suffixes

`stage_review_triage` already calls `reset_retry "$ISSUE"` (bare issue number) on a
CONTINUE dispatch (`scheduler.sh:1022`) — the new suffixes hang off that same key, for the
same reason the existing `:sig`/`:delivery`/`:loop:*` pops exist: a resumed episode must not
inherit banked state from a prior one.

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

### Steps

1. Write the failing test. Append to `tests/test_factory_core_breaker.py`:

   ```python
   def test_reset_retry_clears_comment_verdict_cache_suffixes(tmp_path):
       # #402: a resumed episode (fresh dispatch, blocked-rescue, Continue-dispatch) must
       # not inherit a stale cached classifier verdict, cache-comment-id, or raw-log dedup
       # marker from a prior episode — same rationale as the existing :sig/:delivery pops.
       sf = tmp_path / "state.json"
       set_state_str("9:cverdict", "CONTINUE", sf)
       set_state_str("9:cid", "IC_kwDOabc", sf)
       set_state_str("9:crawlog", "IC_kwDOabc", sf)

       reset_retry("9", sf)

       assert get_state_str("9:cverdict", sf) is None
       assert get_state_str("9:cid", sf) is None
       assert get_state_str("9:crawlog", sf) is None


   def test_reset_retry_still_clears_existing_suffixes_alongside_new_ones(tmp_path):
       # Req 6 / R8b: the new :cverdict/:cid/:crawlog suffixes must be popped in the SAME
       # reset_retry call that still pops the pre-existing :sig/:delivery/:loop:* suffixes
       # — not just alongside them in separate tests — so a regression that scopes the new
       # pops to a different key or short-circuits before reaching the existing ones would
       # be caught here.
       from factory_core.breaker import _write_signature_key
       sf = tmp_path / "state.json"
       increment_retry("9", sf)
       increment_retry("9:delivery", sf)
       _write_signature_key("9", "substantive:x", sf)
       set_state_str("9:cverdict", "SKIP", sf)
       set_state_str("9:cid", "IC_kwDOabc", sf)
       set_state_str("9:crawlog", "IC_kwDOabc", sf)

       reset_retry("9", sf)

       assert get_retry_count("9", sf) == 0
       assert get_retry_count("9:delivery", sf) == 0
       assert get_state_str("9:sig", sf) is None
       assert get_state_str("9:cverdict", sf) is None
       assert get_state_str("9:cid", sf) is None
       assert get_state_str("9:crawlog", sf) is None
   ```

2. Verify it fails:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -k reset_retry -v
   ```
   Expected: the two new tests fail with `AssertionError` (`get_state_str("9:cverdict", sf)`
   still returns a value after `reset_retry`); the pre-existing `test_reset_retry_clears_*`
   tests (`:sig`, `:delivery`, `:loop:*`) keep passing unaffected.

3. Implement. In `scripts/factory_core/breaker.py::reset_retry`, extend the existing pop
   block (right after the `:loop:` prefix loop):

   ```python
       for k in [k for k in data if k.startswith(loop_prefix)]:
           data.pop(k, None)
       # #402: pop the comment-classifier cache (:cverdict/:cid) and raw-response-dump
       # dedup marker (:crawlog) alongside :sig/:delivery/:loop:* — same rationale: a
       # resumed episode must not inherit banked classifier state from a prior one.
       data.pop(f"{key}:cverdict", None)
       data.pop(f"{key}:cid", None)
       data.pop(f"{key}:crawlog", None)
       _atomic_write(state_file, data)
   ```

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -v
   ```
   Expected: all tests pass (existing `:sig`/`:delivery`/`:loop:*` tests unaffected).

5. Commit:

   ```bash
   git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
   git commit -m "feat(#402): reset_retry pops the comment-verdict cache and raw-log dedup marker"
   ```

---

## Task 3: `state-get` / `state-set` CLI subcommands

Mirrors the existing `breaker-get`/`breaker-set-retry` wiring. Both validate `--key` against
`^[0-9]+:(cverdict|cid|crawlog)$` and reject anything else (operator review F6) — the
scheduler gets exactly the three keys it needs, not an unbounded write path into the
breaker's state file.

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_cli.py`

### Steps

1. Write the failing test. Append to `tests/test_factory_core_cli.py`:

   ```python
   def test_state_set_then_get_round_trip(monkeypatch, tmp_path, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "state-set", "--key", "42:cverdict", "--value", "CONTINUE",
       ])
       cli_mod.main()
       monkeypatch.setattr(sys, "argv", ["cli.py", "state-get", "--key", "42:cverdict"])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "CONTINUE"


   def test_state_get_missing_key_prints_nothing(monkeypatch, tmp_path, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", ["cli.py", "state-get", "--key", "42:cid"])
       cli_mod.main()
       assert capsys.readouterr().out == ""


   def test_state_get_rejects_key_outside_allowed_shape(monkeypatch, tmp_path):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", ["cli.py", "state-get", "--key", "42:refine"])
       with pytest.raises(SystemExit):
           cli_mod.main()


   def test_state_set_rejects_key_outside_allowed_shape(monkeypatch, tmp_path):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       state_file = tmp_path / "state.json"
       state_file.write_text("{}")
       monkeypatch.setenv("STATE_FILE", str(state_file))
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "state-set", "--key", "not-an-issue:cverdict", "--value", "SKIP",
       ])
       with pytest.raises(SystemExit):
           cli_mod.main()
   ```

2. Verify it fails:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_cli.py -k state_ -v
   ```
   Expected: 2 of 4 fail with `error: argument cmd: invalid choice: 'state-set'` /
   `'state-get'` (the round-trip and missing-key tests — the subcommands don't exist yet).
   The two `test_state_*_rejects_key_outside_allowed_shape` tests pass vacuously at this
   red step (argparse's own `invalid choice` also raises `SystemExit`, which is all those
   two tests assert) — they become real coverage of the key-shape validator once step 3
   lands.

3. Implement. In `scripts/factory_core/cli.py`, add `import re` to the top-of-file imports:

   ```python
   import argparse
   import os
   import re
   import sys
   from pathlib import Path
   ```

   Add the key-shape validator and the two handler functions, placed after `_breaker_set_retry`:

   ```python
   _STATE_KEY_RE = re.compile(r"^[0-9]+:(cverdict|cid|crawlog)$")


   def _state_get(args):
       from factory_core.breaker import get_state_str
       if not _STATE_KEY_RE.fullmatch(args.key):
           print(f"state-get: invalid key '{args.key}'", file=sys.stderr)
           sys.exit(1)
       state_file = Path(os.environ.get("STATE_FILE",
                                        "/var/lib/dark-factory/scheduler-state.json"))
       value = get_state_str(args.key, state_file)
       if value is not None:
           print(value)


   def _state_set(args):
       from factory_core.breaker import set_state_str
       if not _STATE_KEY_RE.fullmatch(args.key):
           print(f"state-set: invalid key '{args.key}'", file=sys.stderr)
           sys.exit(1)
       state_file = Path(os.environ.get("STATE_FILE",
                                        "/var/lib/dark-factory/scheduler-state.json"))
       set_state_str(args.key, args.value, state_file)
   ```

   Wire the subparsers in `main()`, right after the `bsr` (`breaker-set-retry`) block:

   ```python
       sg = sub.add_parser("state-get")
       sg.add_argument("--key", required=True)
       sg.set_defaults(func=_state_get)

       ss = sub.add_parser("state-set")
       ss.add_argument("--key", required=True)
       ss.add_argument("--value", required=True)
       ss.set_defaults(func=_state_set)
   ```

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_cli.py -v
   ```
   Expected: all tests pass, including the four new ones.

5. Commit:

   ```bash
   git add scripts/factory_core/cli.py tests/test_factory_core_cli.py
   git commit -m "feat(#402): add state-get/state-set CLI subcommands with key-shape validation"
   ```

---

## Task 4: `parse_comment_verdict()` in `scheduler.sh`

Standalone, unit-testable extraction function. Anchored to the start of the response;
MERGE requires a strict whole-response match (operator review — MERGE dispatches a real
merge, so its false positive is the only costly one); two or more distinct, literally
uppercase verdict tokens anywhere makes the reply unparseable (ambiguity rule). The
ambiguity scan is deliberately case-**sensitive** (unlike the primary extraction below it,
which is case-insensitive): the two canonical ambiguity examples both present the second
candidate in the model's instructed all-caps reply form (`SKIP — but MERGE would be
reasonable`, `MERGE? No — SKIP`), while a CONTINUE-shaped justification that uses "merge" as
an ordinary lowercase verb (`CONTINUE — the reviewer wants a merge later`) must not be
flagged — a case-insensitive ambiguity scan would treat any sentence merely mentioning
"merge"/"continue"/"skip" as ambiguous, which contradicts the anchoring requirement that a
mid-sentence mention "cannot flip the verdict."

**Files:** `scheduler.sh`, `tests/test_scheduler_comment_verdict.py`

### Steps

1. Write the failing test. Create `tests/test_scheduler_comment_verdict.py`:

   ```python
   """Parser fixtures for scheduler.sh::parse_comment_verdict (#402).

   Drives the bash function through a subprocess exactly as tests/test_scheduler.sh loads
   the scheduler, minus the stubs parse_comment_verdict itself never needs (it makes no
   external calls) — except two hazards that fire unconditionally at *source* time, before
   the SCHEDULER_SOURCE_ONLY guard (scheduler.sh:1331):
   - `python3 "$FACTORY_PROVIDERS_CLI" preflight` (scheduler.sh:103) — short-circuited by a
     `python3` shell-function override, same technique tests/test_scheduler.sh already uses.
   - `mkdir -p "$SCHEDULER_STATE_DIR"` + a `$STATE_FILE` write (scheduler.sh:120-124),
     defaulting to /var/lib/dark-factory — redirected by overriding SCHEDULER_STATE_DIR to
     a pytest tmp_path (scheduler.sh:11 derives STATE_FILE from it unconditionally, so
     setting STATE_FILE directly would be redundant/inert — SCHEDULER_STATE_DIR is what
     actually matters); tests/test_scheduler.sh:65-72 guards the same hazard.
   A third source-time block (scheduler.sh:111-117, copying /workspace/project/.archon/.env
   if present) is a no-op here: that path doesn't exist in a bare checkout or in CI, only
   inside a live factory run container, where it already exists and is a no-op copy.
   """
   import os
   import subprocess
   from pathlib import Path

   import pytest

   REPO_ROOT = Path(__file__).resolve().parents[1]
   SCHEDULER = REPO_ROOT / "scheduler.sh"

   _HARNESS = '''
   set -uo pipefail
   python3() {
     case "$*" in
       *providers/cli.py*) return 0 ;;
       *) command python3 "$@" ;;
     esac
   }
   export -f python3
   SCHEDULER_SOURCE_ONLY=1 source "$1"
   parse_comment_verdict "$2"
   '''


   def _parse(reply: str, state_dir: Path) -> str:
       env = dict(os.environ)
       env["SCHEDULER_STATE_DIR"] = str(state_dir)
       env["STATE_FILE"] = str(state_dir / "scheduler-state.json")
       result = subprocess.run(
           ["bash", "-c", _HARNESS, "_", str(SCHEDULER), reply],
           cwd=REPO_ROOT, capture_output=True, text=True, env=env,
       )
       return result.stdout.strip()


   CASES = [
       ("SKIP", "SKIP"),
       ("skip", "SKIP"),
       ("**SKIP**", "SKIP"),
       ("`SKIP`", "SKIP"),
       ("- SKIP", "SKIP"),
       ("> SKIP", "SKIP"),
       ("SKIP.", "SKIP"),
       ("SKIP:", "SKIP"),
       ("SKIP Both comments are from automated systems", "SKIP"),
       ("CONTINUE — the reviewer wants a merge later", "CONTINUE"),
       ("MERGE", "MERGE"),
       ("**MERGE**", "MERGE"),
       ("merge.", "MERGE"),
       ("MERGE — approved", ""),
       ("MERGE is not appropriate", ""),
       ("SKIP — but MERGE would be reasonable", ""),
       ("MERGE? No — SKIP", ""),
       ("Merge-ready, ship it", ""),
       ("MERGED already", ""),
       ("Skipping this", ""),
       ("skip_this", ""),
       ("Verdict: SKIP", ""),
       # Operator plan gate (F-1): a bare MERGE on line 1 must not survive a lowercase
       # contradiction further down — the ambiguity scan is case-sensitive, so
       # whole-response MERGE-strict is the only thing standing between this reply and
       # an unguarded `Close issue #N` dispatch.
       ("MERGE\nActually, skip this — the tests fail", ""),
       ("MERGE\nAlso CONTINUE maybe", ""),
       ("MERGE\n", "MERGE"),
       ("", ""),
   ]


   @pytest.mark.parametrize("reply,expected", CASES)
   def test_parse_comment_verdict(reply, expected, tmp_path):
       assert _parse(reply, tmp_path) == expected
   ```

2. Verify it fails:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_scheduler_comment_verdict.py -v
   ```
   Expected: 14 of 26 fail (`parse_comment_verdict: command not found` on stderr, empty
   stdout, since the function doesn't exist yet) — the 12 cases whose expected output is
   already `""` pass vacuously at this red step; they turn into real coverage once the
   function exists in step 4.

3. Implement. In `scheduler.sh`, add `parse_comment_verdict()` immediately above
   `classify_comments()` (`scheduler.sh:777`, in the `# --- Comment interpretation ---`
   section):

   ```bash
   parse_comment_verdict() {
     local response="$1"

     # Ambiguity guard (operator review): a second, literally-capitalized verdict token
     # anywhere in the body makes the whole reply unparseable. Deliberately case-SENSITIVE
     # (unlike the primary match below) — the two canonical ambiguous examples both present
     # the second candidate in the model's instructed all-caps reply form ("SKIP — but
     # MERGE would be reasonable", "MERGE? No — SKIP"), while a CONTINUE-shaped
     # justification that uses "merge" as an ordinary lowercase verb ("the reviewer wants
     # a merge later") must not be flagged.
     local distinct
     distinct=$(printf '%s' "$response" \
       | grep -oP '(?<![[:alnum:]_-])(MERGE|CONTINUE|SKIP)(?![[:alnum:]_-])' 2>/dev/null \
       | sort -u | wc -l) || true
     if [ "${distinct:-0}" -ge 2 ]; then
       return 0
     fi

     # MERGE is strict (operator review): accepted only when the WHOLE response's
     # alphanumeric content is exactly the token. MERGE dispatches `Close issue #N` — a
     # real merge — so it is the only verdict whose false positive is costly; CONTINUE and
     # SKIP keep the lenient token-plus-explanation grammar below.
     # Whole-response (not first-line) scope is what makes the case-SENSITIVE ambiguity
     # guard above safe: a first-line-only match would accept "MERGE\nActually, skip this"
     # (lowercase second token, invisible to the case-sensitive scan) as a real merge.
     # `[^[:alnum:]]` matches newlines in bash ERE, so "MERGE\n" and "**MERGE**\n" still parse.
     local merge_strict_re
     merge_strict_re='^[^[:alnum:]]*MERGE[^[:alnum:]]*$'
     shopt -s nocasematch
     if [[ "$response" =~ $merge_strict_re ]]; then
       shopt -u nocasematch
       echo "MERGE"
       return 0
     fi
     shopt -u nocasematch

     # CONTINUE / SKIP: anchored to the start of the raw response only, so a
     # justification that mentions another verdict word mid-sentence can never flip the
     # match — the regex never scans past the anchor.
     local tok_re='^[^[:alnum:]]*(CONTINUE|SKIP)([^[:alnum:]_-]|$)'
     shopt -s nocasematch
     if [[ "$response" =~ $tok_re ]]; then
       local tok="${BASH_REMATCH[1]}"
       shopt -u nocasematch
       echo "${tok^^}"
       return 0
     fi
     shopt -u nocasematch
     return 0
   }
   ```

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_scheduler_comment_verdict.py -v
   ```
   Expected: `26 passed`.

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler_comment_verdict.py
   git commit -m "feat(#402): add standalone parse_comment_verdict() to scheduler.sh"
   ```

---

## Task 5: Rewrite `classify_comments()` — cache, two-tier logging, tightened prompt

Replace the body from the `result=$(echo "$prompt" | claude ...)` line onward. Capture
stdout and stderr separately (operator review F1 — today's `2>&1` lets a CLI warning defeat
the anchor). Only a successfully parsed verdict is cached; the error-fallback and
still-unparseable paths are never cached (Requirement 3). The tightened prompt restates the
one-word instruction both before and after the interpolated comment text (Requirement 7).

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Steps

1. Write the failing tests. All 26 single-letter section labels (A-Z) are already taken in
   this file, so the new section is labeled `AA`. It must run with the *real*
   `classify_comments` (the sourced definition), not the section-R stub
   (`classify_comments() { echo "CONTINUE"; }`, `tests/test_scheduler.sh:1199`, never
   unset) — so it goes **before** section R, immediately after section Q ends (right before
   the `# ==========` / `# R: Stage guard semantics` header, `tests/test_scheduler.sh:1188`).
   Call counts on the `claude` stub must go through `$STUB_LOG` (the mechanism this whole
   suite already uses everywhere else), not a shell variable incremented inside the stub —
   `classify_comments` is invoked as `V=$(classify_comments ...)`, a command substitution
   subshell, so a plain variable write inside `claude` never becomes visible to the parent
   shell. Section N leaves its own `--id-routing` `python3` override installed permanently
   (`reset_python3_stub()` only clears `PROVIDERS_CLI_OUTPUT`, never the function body —
   see section Y's comment on the same hazard) — redefine the generic stub here too,
   rather than relying on `$FACTORY_CORE_CLI` happening not to match section N's
   `*providers/cli.py*` case:

   ```bash
   # ==========================================
   # AA: classify_comments — cache + two-tier fallback logging (#402)
   # ==========================================
   echo ""
   echo "--- AA: classify_comments cache + fallback logging ---"
   echo '{}' > "$STATE_FILE"
   : > "$STUB_LOG"

   # Redefine the generic python3 stub (section N's override otherwise survives here —
   # see section Y's identical comment on this hazard).
   python3() {
     echo "python3 $*" >> "$STUB_LOG"
     case "$*" in
       *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
       *) "$_REAL_PY3" "$@" ;;
     esac
   }
   export -f python3
   reset_python3_stub

   CLAUDE_REPLY="CONTINUE this needs a rename"
   claude() {
     echo "claude $*" >> "$STUB_LOG"
     echo "$CLAUDE_REPLY"
     return 0
   }
   export -f claude
   export CLAUDE_REPLY

   _AA_COMMENTS='[{"id":"IC_1","author":{"login":"human"},"body":"please rename this"}]'

   # AA1: a successful classification is cached — a second call with the same latest
   # comment id must not invoke the claude stub again.
   : > "$STUB_LOG"
   V1=$(classify_comments 501 "t" "$_AA_COMMENTS")
   V2=$(classify_comments 501 "t" "$_AA_COMMENTS")
   assert_eq "AA1: first call returns CONTINUE" "CONTINUE" "$V1"
   assert_eq "AA1: second call returns cached CONTINUE" "CONTINUE" "$V2"
   assert_eq "AA1: claude invoked exactly once (second call served from cache)" \
     "1" "$(grep -c '^claude ' "$STUB_LOG" || true)"

   # AA2: a new comment id busts the cache.
   : > "$STUB_LOG"
   _AA_COMMENTS2='[{"id":"IC_1","author":{"login":"human"},"body":"please rename this"},{"id":"IC_2","author":{"login":"human"},"body":"also fix the docs"}]'
   V3=$(classify_comments 501 "t" "$_AA_COMMENTS2")
   assert_eq "AA2: new comment id re-invokes claude" "1" "$(grep -c '^claude ' "$STUB_LOG" || true)"
   assert_eq "AA2: verdict still CONTINUE" "CONTINUE" "$V3"

   # AA3: an unparseable response is never cached — two consecutive calls both invoke
   # the claude stub and both fall back to SKIP.
   echo '{}' > "$STATE_FILE"
   : > "$STUB_LOG"
   CLAUDE_REPLY="SKIPBOTHCOMMENTSAREFROMAUTOMATEDSYSTEMS"
   _AA_BLOB='[{"id":"IC_9","author":{"login":"human"},"body":"noise"}]'
   _AA3_ERR=$(mktemp /tmp/aa3-err-XXXXXX.log)
   V4=$(classify_comments 502 "t" "$_AA_BLOB" 2>"$_AA3_ERR")
   V5=$(classify_comments 502 "t" "$_AA_BLOB" 2>>"$_AA3_ERR")
   assert_eq "AA3: unparseable reply falls back to SKIP (1st)" "SKIP" "$V4"
   assert_eq "AA3: unparseable reply falls back to SKIP (2nd)" "SKIP" "$V5"
   assert_eq "AA3: claude invoked twice (fallback SKIP never cached)" \
     "2" "$(grep -c '^claude ' "$STUB_LOG" || true)"
   assert_eq "AA3: compact fallback line logged on both polls" \
     "2" "$(grep -c 'fallback reason=unparsed' "$_AA3_ERR" || true)"
   assert_eq "AA3: raw response dumped only once (deduped per comment id)" \
     "1" "$(grep -c 'raw response' "$_AA3_ERR" || true)"
   rm -f "$_AA3_ERR"

   # AA4: a stderr warning from the claude stub does not defeat the parse (F1) — stdout
   # and stderr must be captured separately.
   echo '{}' > "$STATE_FILE"
   claude() {
     echo "claude $*" >> "$STUB_LOG"
     echo "warning: proxy retry" >&2
     echo "MERGE"
     return 0
   }
   export -f claude
   _AA_MERGE='[{"id":"IC_7","author":{"login":"human"},"body":"ship it"}]'
   V6=$(classify_comments 503 "t" "$_AA_MERGE")
   assert_eq "AA4: stderr noise does not defeat the anchor" "MERGE" "$V6"

   echo '{}' > "$STATE_FILE"
   : > "$STUB_LOG"
   unset -f claude
   ```

2. Verify these fail:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -A1 "^  FAIL"
   ```
   Expected: `AA1`/`AA2` fail because today's `classify_comments` never caches (claude
   invoked on every call); `AA3`'s dedup assertion fails (today's code logs the raw response
   every time, uncached); `AA4` fails because today's `2>&1` merges the stderr warning into
   the matched string, breaking the exact-match `case`.

3. Implement. Replace `classify_comments()` in `scheduler.sh` in full:

   ```bash
   classify_comments() {
     local issue_num="$1"
     local title="$2"
     local comments_json="$3"

     local latest_id
     latest_id=$(echo "$comments_json" | jq -r '.[-1].id // empty')

     local cached_cid
     cached_cid=$(STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
       state-get --key "${issue_num}:cid" 2>/dev/null) || true
     if [ -n "$latest_id" ] && [ "$cached_cid" = "$latest_id" ]; then
       local cached_verdict
       cached_verdict=$(STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
         state-get --key "${issue_num}:cverdict" 2>/dev/null) || true
       if [ -n "$cached_verdict" ]; then
         echo "  classify_comments #${issue_num}: cache hit verdict=${cached_verdict}" >&2
         echo "$cached_verdict"
         return
       fi
     fi

     local comment_text
     comment_text=$(echo "$comments_json" | jq -r '.[] | "[\(.author.login)] \(.body)"')

     local verdict_rule="Reply with ONLY one word — MERGE, CONTINUE, or SKIP. No explanation, punctuation, markdown, or commentary."
     local prompt
     prompt="You are a PR comment classifier. Read the comments below and decide
   the intent. ${verdict_rule}

   MERGE — the reviewer approves the PR (e.g. \"looks good\", \"ship it\",
   \"approved\", \"LGTM\", thumbs up, ready to merge)
   CONTINUE — the reviewer wants changes, asks questions about the implementation,
   raises concerns, or requests any action (e.g. \"fix the tests\",
   \"can you rename X\", \"is this fixable?\", \"should we do X or Y?\",
   \"this needs error handling\", any feedback that needs a response)
   SKIP — the comment is purely from a bot or automated system, with no
   human-authored content requiring action

   When in doubt between CONTINUE and SKIP, choose CONTINUE.

   PR #${issue_num}: ${title}
   Comments since last factory run:
   ${comment_text}

   ${verdict_rule}"

     local err_tmp result exit_code err_tail
     err_tmp=$(mktemp)
     set +e
     result=$(echo "$prompt" | claude -p --model haiku 2>"$err_tmp")
     exit_code=$?
     set -e
     err_tail=$(tail -c 200 "$err_tmp" 2>/dev/null) || true
     rm -f "$err_tmp"

     if [ "$exit_code" -ne 0 ] || [ -z "$result" ]; then
       echo "  classify_comments #${issue_num}: fallback reason=api_error verdict=SKIP cached=no" >&2
       _log_raw_fallback_once "$issue_num" "$latest_id" "$result" "$err_tail"
       echo "SKIP"
       return
     fi

     local verdict
     verdict=$(parse_comment_verdict "$result")

     if [ -z "$verdict" ]; then
       echo "  classify_comments #${issue_num}: fallback reason=unparsed verdict=SKIP cached=no" >&2
       _log_raw_fallback_once "$issue_num" "$latest_id" "$result" "$err_tail"
       echo "SKIP"
       return
     fi

     echo "  classify_comments #${issue_num}: verdict=${verdict}" >&2
     if [ -n "$latest_id" ]; then
       STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
         state-set --key "${issue_num}:cverdict" --value "$verdict" >/dev/null
       STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
         state-set --key "${issue_num}:cid" --value "$latest_id" >/dev/null
     fi
     echo "$verdict"
   }

   _log_raw_fallback_once() {
     local issue_num="$1" latest_id="$2" raw="$3" err_tail="$4"
     local dedup_key="${issue_num}:crawlog"
     local logged_for
     logged_for=$(STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
       state-get --key "$dedup_key" 2>/dev/null) || true
     if [ -n "$latest_id" ] && [ "$logged_for" = "$latest_id" ]; then
       return
     fi
     local truncated
     truncated=$(printf '%s' "$raw" | head -c 200) || true
     echo "  classify_comments #${issue_num}: raw response (truncated): '${truncated}' stderr='${err_tail}'" >&2
     if [ -n "$latest_id" ]; then
       STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" \
         state-set --key "$dedup_key" --value "$latest_id" >/dev/null
     fi
   }
   ```

4. Verify all tests pass:

   ```bash
   bash tests/test_scheduler.sh
   ```
   Expected: `Results: N passed, 0 failed` (N = previous total + 11 new AA-section
   assertions).

   Also re-run the parser fixtures and CLI/breaker suites to confirm no regression:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_scheduler_comment_verdict.py \
     tests/test_factory_core_breaker.py tests/test_factory_core_cli.py -v
   ```
   Expected: all pass.

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(#402): cache classifier verdicts, split stdout/stderr, two-tier fallback logging"
   ```

---

## Final verification checklist

1. Run the full suite (matches `.github/workflows/ci.yml`'s `PYTHONPATH=scripts` env):

   ```bash
   cd "$(git rev-parse --show-toplevel)"
   PYTHONPATH=scripts python -m pytest tests/ -v
   ```
   Expected: all pass, including the three new/modified pytest files.

2. Run the orchestration bash suite (not CI-enforced, per the spec's Open Questions, but
   must pass locally and via the `verify` skill):

   ```bash
   bash tests/test_scheduler.sh
   ```
   Expected: `Results: N passed, 0 failed`.

3. Confirm scope: only the files in the File Structure table above changed, plus the spec
   (`docs/superpowers/specs/2026-09-08-classifier-verdict-parsing-design.md`) and this plan
   file under `docs/superpowers/`, which the refine/plan phases already committed on this
   branch — expected, not spillover.

   ```bash
   git fetch origin main
   git diff --stat origin/main HEAD
   ```

4. Confirm acceptance criteria against the spec's Requirements:
   - Req 1 (anchored regex, MERGE-strict, ambiguity) — `tests/test_scheduler_comment_verdict.py`'s
     26 fixtures, all green.
   - Req 2 (`parse_comment_verdict()` standalone, not named `parse_verdict`) — confirmed by
     inspection; no collision with `scripts/factory_core/verdict.py:20` or
     `scripts/factory_core/epic_autopilot.py:88`.
   - Req 3 (only successful verdicts cached) — `tests/test_scheduler.sh` section AA3.
   - Req 4 (cache per issue + latest-comment-id) — section AA1/AA2.
   - Req 5 (two-tier fallback logging) — section AA3's dedup assertion.
   - Req 6 (`reset_retry` pops new suffixes) — `tests/test_factory_core_breaker.py`.
   - Req 7 (tightened, duplicated prompt instruction) — confirmed by inspection of the
     rewritten `classify_comments()` prompt string.
   - Req 8 (test split: pytest for parser/breaker/CLI, bash for orchestration) — as built
     above.

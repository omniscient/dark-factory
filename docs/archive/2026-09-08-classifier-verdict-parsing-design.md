# Fix scheduler comment-classifier verdict parsing, caching, and log noise

**Issue:** #402
**Operator review of PR #413 (2026-09-09, at merge):** two deviations from the text below, both tightenings, both covered by fixtures. (1) `?` is excluded from MERGE-strict's leading and trailing classes — `MERGE?` is a hedge and this branch dispatches `Close issue #N`; `MERGE.`, `**MERGE**`, `MERGE!` still parse. (2) The ambiguity scan tokenizes with `tr -c '[:alnum:]_-'` + `grep -xE` instead of a PCRE lookaround: the `grep -oP` form degrades silently where PCRE is unavailable (stderr suppressed, `|| true`, `wc -l` on empty output ⇒ the guard stops firing with no log line), and it cannot match adjacent tokens such as `SKIP MERGE` because the first match consumes the separator the second needs.
**Operator review:** 2026-09-08 (spec gate) — amendments F1—F7 from an independent read-only review, plus the operator's MERGE-strict and ambiguity rules, applied.
**Surface note:** `scripts/factory_core/breaker.py` is a Blast-Radius hotspot and a CLAUDE.md safety surface; this change is additive (string state helpers, reset hygiene) and touches no retry logic. The PR takes the operator-review path.

---

## Overview / Problem Statement

`classify_comments()` (`scheduler.sh:777`) shells out to `claude -p --model haiku` asking for a
single verdict token (`MERGE`, `CONTINUE`, or `SKIP`), then does `tr -d '[:space:]'` (deletes *all*
whitespace, not just leading/trailing) followed by an exact `case` match. Since 2026-09-05 the
scheduler has logged 47 occurrences in 48 hours of:

```
classify_comments #394: unexpected response 'SKIPBOTHCOMMENTSAREFROMAUTOMATEDSYSTEMS...', defaulting to SKIP
```

The model reliably ignores "reply with exactly one word" and returns the token followed by a
justification sentence. Because `tr -d '[:space:]'` runs *before* the match, the token/justification
boundary is destroyed before the code ever has a chance to look for it — the raw response was very
likely `SKIP Both comments are from automated systems...`, not an unseparated blob; the blob is a
side effect of the cleaning step, not the model's actual output. This means the fix cannot be
"split on whitespace after cleaning" — the cleaning step itself must go.

Impact, per the issue:
- The SKIP fallback happens to be harmless on #394 (both comments were in fact bot-authored), but
  the same failure mode on a REVISE/APPROVE-shaped human reply would silently drop real reviewer
  feedback and leave a PR stalled In Review with no further signal.
- `classify_comments` re-runs a paid `claude -p` call on every poll that reaches
  `stage_review_triage` (skipped when the factory is at capacity or an earlier stage dispatched;
  observed ≈ 1/hour, 47 in 48 h) for as long as an In-Review issue's comment set is unchanged,
  and the 47 identical raw-response dumps drowned the log. Saving ≈ 47 Haiku calls per 48 h per
  stuck issue (~$0.05); the primary win is log signal and the latent dropped-instruction bug.
  The classifier model stays `haiku` (unchanged).

This is a self-hosting fix: `scheduler.sh` and `scripts/factory_core/breaker.py` are Dark Factory's
own scheduler and shared per-issue state store, not target-repo application code.

## Requirements

Distilled from the issue's Fix list and the Q&A below.

1. **Token extraction must match the raw response, not a cleaned one.** Replace the
   destroy-then-match approach with an anchored, case-insensitive regex applied to the untouched
   `claude -p` **stdout** (stderr is captured separately — see Architecture, F1):
   `^[^[:alnum:]]*(MERGE|CONTINUE|SKIP)([^[:alnum:]_-]|$)`. The `[^[:alnum:]]*` prefix class absorbs
   markdown/formatting noise the model commonly wraps a token in — `**SKIP**`, `` `SKIP` ``, a
   leading `-`/`>`/bullet — and the explicit tail class replaces `\b` (operator review F2):
   `\b` is a glibc extension that also matches before `-`, so `Merge-ready, ship it` parsed as
   MERGE; the tail class rejects `Merge-ready`, `MERGED`, `Skipping`, `skip_this`. If the
   implementation uses `[[ =~ ]]`, the pattern must live in a variable (an unquoted `\b` is
   shell-mangled). The match stays **anchored to the start of the response** and never scans the
   whole body, so a CONTINUE-shaped justification that mentions "merge" mid-sentence cannot flip
   the verdict. Only the captured group is uppercased; nothing else in the response is touched.
   Two further rules, both operator-added at the spec gate because MERGE dispatches
   `Close issue #N` — a merge — and is therefore the only verdict whose false positive is
   costly:
   - **MERGE is strict.** MERGE is accepted only when the whole response's alphanumeric content
     is exactly the token — `^[^[:alnum:]]*MERGE[^[:alnum:]]*$` against the whole response (so `MERGE`,
     `**MERGE**`, `merge.` parse; `MERGE — approved by the reviewer` and `MERGE is not
     appropriate` do **not**). A MERGE followed by any explanation is unparseable — uncached
     fallback SKIP, logged — and simply retries next poll. CONTINUE and SKIP keep the lenient
     token-plus-explanation grammar: their false positives cost a run or a wait, not a merge.
   - **Ambiguity is unparseable.** If the raw response contains two or more *distinct* verdict
     tokens anywhere (case-**sensitive** — literal uppercase only — bounded by
     non-`[[:alnum:]_-]`), the response is unparseable — uncached fallback SKIP, logged —
     regardless of which token came first (`SKIP — but MERGE would be reasonable` and
     `MERGE? No — SKIP` both fall back). Case-sensitivity is deliberate and supersedes an
     earlier "case-insensitive" reading of this rule, which contradicted this spec's own fixture
     `CONTINUE — the reviewer wants a merge later → CONTINUE` (a case-insensitive scan yields
     {CONTINUE, MERGE} = 2 distinct) and would have made every token-plus-justification reply
     unparseable — re-creating the bug this ticket fixes. The MERGE-strict rule above is scoped
     to the whole response precisely so that the case-sensitive scan cannot leak a lowercase
     contradiction into a real merge (operator plan gate, 2026-09-09).
2. **Extraction lives in a standalone, unit-testable function**, `parse_comment_verdict()`, taking
   the raw response string and echoing the matched uppercase token or nothing. This gives fix item
   3's test a target it can drive directly without a paid `claude -p` call, and keeps
   `classify_comments()` itself as the (thin) orchestration around it. Do not name it
   `parse_verdict` — two Python functions with that name and a different contract already exist
   (`scripts/factory_core/verdict.py:20`, `scripts/factory_core/epic_autopilot.py:88`); a third,
   differently-shaped one under the same name is a grep trap.
3. **Only a successfully parsed MERGE/CONTINUE/SKIP verdict is cached.** A verdict reached via the
   error-fallback path (non-zero exit / empty output from `claude -p`) or the
   still-unparseable-after-extraction path is **never** cached — it retries on every poll, same as
   today. Caching a fallback SKIP would turn a one-poll dropped instruction into a permanent one
   (no further comment triggers re-evaluation), inverting the prompt's existing "when in doubt,
   choose CONTINUE" bias under uncertainty (`scheduler.sh:798`) at precisely the moment information
   is weakest.
4. **Cache a successful verdict per (issue, latest-comment-id)** in `scheduler-state.json`, so an
   unchanged comment set is not re-classified on every poll. "Latest comment id" is the `id` of the
   last element of the `NEW_COMMENTS` array `classify_comments` already receives (the comments
   array returned by `get-comments`, sliced to after the last factory-posted marker — see
   `get_new_comments`, `scheduler.sh:752`). On entry, if the stored comment-id for this issue
   matches the current latest id, return the stored verdict without invoking `claude -p`. On a
   fresh successful classification, store both the verdict and the new latest-comment-id together
   (see Architecture for storage shape).
5. **Fallback logging is two-tier**, so the anomaly stays visible every poll without re-dumping the
   full response every poll:
   - A compact, unconditional one-liner on every fallback, every poll, e.g.
     `classify_comments #N: fallback reason=<unparsed|api_error> verdict=SKIP cached=no`.
   - The raw response body (truncated to ~200 chars) is logged only **once per (issue,
     latest-comment-id)** — a distinct dedup marker from the verdict cache, so writing it is never
     mistakable for a cached verdict. Log the **raw**, untouched response — the current code logs
     a `tr -d`-mangled string, which is what made this bug harder to diagnose from logs alone.
6. **`reset_retry()` (`scripts/factory_core/breaker.py:53`) pops the new suffixes** alongside the
   existing `:sig`/`:delivery`/`:loop:*` cleanup, for the same reason already documented there: an
   issue resumed into a fresh episode (dispatch success, blocked-rescue, spec/plan advance) must
   not inherit banked state — here, a stale cached verdict or a stale raw-log dedup marker — from a
   prior episode. `stage_review_triage` already calls `reset_retry "$ISSUE"` on a CONTINUE dispatch
   (`scheduler.sh:1022`) using the bare issue number as the key, which the new suffixes hang off.
7. **Tighten the prompt** beyond the current "Reply with exactly one word" (which already didn't
   work): `Reply with ONLY one word — MERGE, CONTINUE, or SKIP. No explanation, punctuation,
   markdown, or commentary.`, restated **both before and after** the interpolated comment text.
   Today the instruction sits once, before the untrusted comment block is appended last
   (`scheduler.sh:787` vs `:802`), so the most recent thing in the model's context is whatever a
   reviewer wrote — restating the constraint after the comment block is a low-cost mitigation for
   the observed instruction-drift. This is best-effort: `parse_comment_verdict()` is the actual
   safety net regardless of prompt compliance, not the prompt wording.
8. **Tests**, split so that everything safety-relevant runs in CI (operator review F3: CI runs
   `python -m pytest tests/ -v` plus an explicit list of `.sh` files that does **not** include
   `tests/test_scheduler.sh`):
   - **Parser fixtures, CI-run.** A new pytest file, `tests/test_scheduler_comment_verdict.py`,
     drives `parse_comment_verdict` through a subprocess exactly as `tests/test_scheduler.sh` loads
     the scheduler (`bash -c 'SCHEDULER_SOURCE_ONLY=1 source ./scheduler.sh; parse_comment_verdict
     "$1"' _ "<reply>"`), one case per row, expected output in the right column:
     bare `SKIP` → SKIP; `skip` → SKIP; `**SKIP**`, `` `SKIP` ``, `- SKIP`, `> SKIP`, `SKIP.`,
     `SKIP:` → SKIP; `SKIP Both comments are from automated systems` → SKIP;
     `CONTINUE — the reviewer wants a merge later` → CONTINUE (anchoring: "merge" mid-sentence
     does not flip it); `MERGE`, `**MERGE**`, `merge.` → MERGE; `MERGE — approved` and
     `MERGE is not appropriate` → no match (MERGE strict); `SKIP — but MERGE would be
     reasonable` and `MERGE? No — SKIP` → no match (ambiguity); `Merge-ready, ship it`,
     `MERGED already`, `Skipping this`, `skip_this`, `Verdict: SKIP`, empty → no match.
   - **Breaker helpers, CI-run.** In `tests/test_factory_core_breaker.py`: `get_state_str` /
     `set_state_str` round-trip through the same `_read_state`/`_atomic_write` path; `reset_retry`
     pops `:cverdict`, `:cid`, `:crawlog` **while still popping** `:sig`, `:delivery`, `:loop:*`;
     `state-set` rejects a key outside the allowed shape.
   - **Orchestration, bash.** In `tests/test_scheduler.sh` (alongside the existing
     `classify_comments` stub, ~line 1199): an unparseable response is not cached (two consecutive
     calls both invoke the `claude` stub and both fall back to SKIP); a successful classification
     is cached (second call does not invoke the stub); a new comment id busts the cache; a stderr
     warning from the stub does not defeat the parse (F1). These run locally and via the `verify`
     skill; wiring `tests/test_scheduler.sh` into `ci.yml` is a separate operator commit, because
     a `.github/workflows/` edit trips the blast-radius gate (#374) — not part of this ticket.

## Architecture / Approach

**Extraction.** `parse_comment_verdict()` is a new bash function near `classify_comments()`:
matches `^[^[:alnum:]]*(CONTINUE|SKIP)([^[:alnum:]_-]|$)` case-insensitively against `$1` (MERGE is
handled by the strict whole-response branch above; the tail class replaces `\b` per F2) (e.g. via `grep -Eio`
or a `[[ =~ ]]` with `shopt -s nocasematch`), echoes the uppercased captured group, or echoes
nothing on no match. `classify_comments()` calls this on the raw **stdout** only (no `tr -d` step). Today's call
merges stderr into `$result` (`2>&1`, `scheduler.sh:805`), so any CLI warning printed first would
defeat the anchor and bring the every-poll fallback back (operator review F1): capture them apart
— `result=$(echo "$prompt" | claude -p --model haiku 2>"$err_tmp")` — match stdout, and append
the last 200 chars of `$err_tmp` to the deduped raw-response log line. Anything the parser does
not match is the existing logged, uncached fallback SKIP.

**Cache storage.** `scheduler-state.json`'s existing shape is a flat `{key: value}` dict
(`scripts/factory_core/breaker.py`), used today for int-valued retry counters (`_write_key`) and a
string-valued failure signature under a `:sig` suffix (`_write_signature_key`,
`breaker.py:263-267`). The verdict cache and raw-log dedup marker are both strings (a token, and a
comment id respectively), so this needs the same string-valued read/write breaker.py already has
internally for `:sig`, generalized into a small public pair rather than duplicated:
- `get_state_str(key, state_file) -> str | None` / `set_state_str(key, value, state_file)` in
  `breaker.py`, built on the existing private `_read_state`/`_atomic_write` helpers (the same ones
  `_write_signature_key` already uses) — not a parallel implementation.
- Two new `cli.py` subcommands, `state-get --key` (prints the value or nothing) and `state-set
  --key --value`, mirroring the existing `breaker-get`/`breaker-set-retry` wiring
  (`cli.py:37-76`), so `scheduler.sh` can call them the same way it calls
  `STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-get ...` today. Both validate
  `--key` against `^[0-9]+:(cverdict|cid|crawlog)$` and reject anything else (operator review F6):
  the scheduler gets exactly the three keys it needs, not an unbounded write path into the
  breaker's state file.
- Suffixes on the bare-issue-number key (matching `reset_retry`'s existing key shape): `<issue>:cverdict`
  (cached token), `<issue>:cid` (latest comment id the verdict was computed for), `<issue>:crawlog`
  (comment id the raw-response dump was last logged for — independent of `:cverdict` so writing one
  is never mistakable for the other).
- `reset_retry()` adds `:cverdict`, `:cid`, `:crawlog` to its existing pop list
  (`breaker.py:53-77`, alongside `:sig`/`:delivery`/`:loop:*`).

**`classify_comments()` flow** (replacing the current body from the `result=$(echo "$prompt" |
claude ...)` line onward):
1. Read `<issue>:cid`. If it equals the current latest comment id (last element's `.id` in
   `$comments_json`) and `<issue>:cverdict` is non-empty, return the cached verdict — no `claude -p`
   call.
2. Otherwise call `claude -p --model haiku` with the tightened prompt (Requirement 7), capturing
   stdout and stderr separately (F1).
3. On non-zero exit or empty output: emit the compact fallback line, dedupe-log the raw response
   per `<issue>:crawlog`, return `SKIP` uncached (unchanged from today's error path other than the
   two-tier logging).
4. Otherwise call `parse_comment_verdict()` on the raw output.
   - Match found: log `verdict=<token>`, write `<issue>:cverdict`/`<issue>:cid`, return the token.
   - No match: emit the compact fallback line (with the raw response, deduped as above), return
     `SKIP` uncached.

## Alternatives Considered

- **Clean-then-split** (uppercase/strip the full response, take the first whitespace-delimited
  word, strip its punctuation) — smaller-looking diff, but keeps the same "destroy the boundary,
  then hope it survives" shape that caused this bug, and does not reliably fix the actual observed
  failure (no separator at all between token and justification once the response has been
  concatenated). Rejected in favor of matching the raw, untouched response.
- **Content-hash cache key** (hash of the full `NEW_COMMENTS` body text) instead of latest-comment-id
  — would additionally invalidate the cache on a comment *edit* (same id, changed body), which
  latest-comment-id does not catch. The issue's Fix item 2 explicitly specifies "(issue,
  latest-comment-id)"; a content hash is a heavier read (full body vs. one field) for a case
  (editing an already-triaged comment) that is uncommon relative to new comments arriving. Deferred
  — see Open Questions.
- **Suppressing the fallback path's log line entirely** once caching lands — rejected: caching a
  fallback verdict is itself rejected (Requirement 3), so the fallback path keeps running every
  poll and must stay visible every poll (the compact line), or a genuine, recurring parse failure
  becomes as invisible as the bug this ticket fixes. Only the redundant full-body dump is deduped.
- **Adopting the `STATUS:`/`GATE_TYPE:` verdict schema** (`scripts/factory_core/verdict.py`,
  `emit_verdict`) for this 3-word classifier — that schema is built for multi-field checker verdicts
  written to a review file by a full agent node; importing it for a bare MERGE/CONTINUE/SKIP token
  from a single `claude -p` call is over-engineering for this ticket's scope.

## Open Questions (non-blocking)

- A comment **edit** (same comment id, changed body) will not bust the cache under
  latest-comment-id keying and could serve a stale verdict until a new comment arrives. Matches the
  issue's literal Fix item 2 wording; flagged here rather than expanded into this ticket's scope.
- `tests/test_scheduler.sh` is not currently invoked by `.github/workflows/ci.yml` (confirmed: the
  CI job runs 20 explicitly named `tests/test_*.sh` files plus `python -m pytest tests/ -v`, and
  `test_scheduler.sh` is not among the named files). New tests added there per Requirement 8 will
  run locally and via the project's `verify` skill but are not CI-enforced today — a pre-existing
  gap, not introduced by this ticket, and out of scope to fix here.

- MERGE dispatches `Close issue #N` without an `is_issue_running` check (`scheduler.sh:1013-1016`);
  a cached MERGE inherits this. Pre-existing and unchanged here (operator review F7); the MERGE-strict
  rule above narrows how often a MERGE is produced at all.

## Assumptions

- The three real verdict tokens are `MERGE`, `CONTINUE`, `SKIP` (the code's actual vocabulary) —
  the issue body's illustrative `SKIP|REVISE|APPROVE` is example prose, not the literal token set.
- `claude -p --model haiku` output for a matched case is expected to be near-trivial to extract
  from (a token, optionally wrapped in light markdown/punctuation, followed by prose); the anchored
  regex is not intended to defend against adversarial or deeply nested formatting.

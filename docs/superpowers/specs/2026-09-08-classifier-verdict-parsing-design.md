# Fix scheduler comment-classifier verdict parsing, caching, and log noise

**Issue:** #402

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
- `classify_comments` re-runs a paid `claude -p` call every poll (default 60s,
  `config/config.yaml:3`) for as long as an In-Review issue's comment set is unchanged, and the
  47 identical raw-response dumps drowned the log.

This is a self-hosting fix: `scheduler.sh` and `scripts/factory_core/breaker.py` are Dark Factory's
own scheduler and shared per-issue state store, not target-repo application code.

## Requirements

Distilled from the issue's Fix list and the Q&A below.

1. **Token extraction must match the raw response, not a cleaned one.** Replace the
   destroy-then-match approach with an anchored, case-insensitive regex applied to the untouched
   `claude -p` stdout: `^[^[:alnum:]]*(MERGE|CONTINUE|SKIP)\b`. The `[^[:alnum:]]*` prefix class
   absorbs markdown/formatting noise the model commonly wraps a token in — `**SKIP**`, `` `SKIP` ``,
   a leading `-`/`>`/bullet, and trailing punctuation like `SKIP.`/`SKIP:` — without requiring a
   distinct rule per noise shape. The match stays **anchored to the start of the response** and
   never scans the whole body, so a CONTINUE-shaped justification that happens to contain the word
   "merge" mid-sentence cannot flip the verdict. Only the captured group is uppercased; nothing
   else in the response is touched.
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
8. **Tests** (in `tests/test_scheduler.sh`, alongside the existing `classify_comments` stub at
   line ~1199):
   - `parse_comment_verdict()` correctly extracts the token from "TOKEN + explanation" responses,
     including markdown-wrapped (`**SKIP**`), backtick-wrapped, bulleted (`- SKIP`),
     blockquoted (`> SKIP`), and trailing-punctuation (`SKIP.`, `SKIP:`) shapes.
   - A CONTINUE-shaped response containing the word "merge" mid-sentence does **not** parse as
     MERGE (anchoring test).
   - An unparseable response is not cached: two consecutive `classify_comments` calls for the same
     unchanged comment set both invoke the `claude` stub (no cache short-circuit) and both fall
     back to SKIP.
   - A successful classification is cached: a second `classify_comments` call for the same issue
     with the same latest-comment-id does **not** invoke the `claude` stub and returns the cached
     verdict.
   - A new comment (different latest-comment-id) busts the cache and re-invokes the stub.
   - `reset_retry` clears the new cache/dedup suffixes.

## Architecture / Approach

**Extraction.** `parse_comment_verdict()` is a new bash function near `classify_comments()`:
matches `^[^[:alnum:]]*(MERGE|CONTINUE|SKIP)\b` case-insensitively against `$1` (e.g. via `grep -Eio`
or a `[[ =~ ]]` with `shopt -s nocasematch`), echoes the uppercased captured group, or echoes
nothing on no match. `classify_comments()` calls this on the raw `$result` (no `tr -d` step);
anything it doesn't match is the existing logged, uncached fallback SKIP.

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
  `STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-get ...` today.
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
2. Otherwise call `claude -p --model haiku` with the tightened prompt (Requirement 7).
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

## Assumptions

- The three real verdict tokens are `MERGE`, `CONTINUE`, `SKIP` (the code's actual vocabulary) —
  the issue body's illustrative `SKIP|REVISE|APPROVE` is example prose, not the literal token set.
- `claude -p --model haiku` output for a matched case is expected to be near-trivial to extract
  from (a token, optionally wrapped in light markdown/punctuation, followed by prose); the anchored
  regex is not intended to defend against adversarial or deeply nested formatting.

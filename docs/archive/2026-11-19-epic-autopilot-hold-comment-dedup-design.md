# Epic Autopilot: stop re-posting an identical HOLD comment on every TTL re-review

**Issue:** #456

**Revised:** 2026-11-19 (operator amendment after spec-gate review: dedup keys on a digest of the review input, not of the concerns text, which never repeats; test, legacy-state, unparseable-review and selection-invariant fixes)

## Overview / Problem statement

`scripts/factory_core/epic_autopilot.py`'s starved-cycle reviewer caches a HOLD verdict
for `EPIC_AUTOPILOT_HOLD_TTL_HOURS` (default 24, `cached_verdict`/`record_verdict`,
`epic_autopilot.py:148-161`). Once the TTL expires, a parked candidate is re-reviewed by
Opus. `run_once()`'s HOLD branch (`epic_autopilot.py:295-299` on current `main` — the
issue's cited `~257-298` has drifted; always re-derive line numbers from `main` rather
than trusting a stale citation) posts a new `"parked (HOLD)"` comment on **every** HOLD
outcome, whether or not anything about the verdict changed:

```python
concerns = "; ".join(verdict.get("concerns", [])) or "not low-risk / low confidence"
io.comment(cand["number"],
           f"🤖 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
record_verdict(state, cand["number"], h, "HOLD", now_iso)
return {"outcome": "hold", "issue": cand["number"], "reason": concerns}
```

While a ticket is starved and genuinely blocked (same spec, the same concerns in
substance but reworded by Opus on every review, day after day), this produces one near-identical comment per TTL cycle — observed as 11
repeat comments over two weeks on MarketHawk issues #442/#445 before autopilot was
disabled there. Any instance running with `EPIC_AUTOPILOT_ENABLED=true` is exposed to the
same bug. (The shipped default is `epic_autopilot.enabled: false`, `config/config.yaml:78`,
and no file under `deploy/` on `main` assigns the variable, so whether the self instance
has it on is an operator-side fact this spec does not rely on.)

## Requirements (from Q&A)

Scope is fixed by the issue's operator note (2026-11-19): implement **option (b) only**
— compare a freshly re-reviewed HOLD against the last stored HOLD for that issue and
skip the comment when nothing about the ticket the reviewer was shown has changed, while still refreshing the
cached state. Option (a) (skip re-review entirely while the spec is unchanged) is
explicitly not in scope. Acceptance item 4 (auditing dark-factory's own tickets for
existing repeat-comment damage) is an **operator task, out of scope for this factory
run** — this spec does not plan any comment cleanup or retroactive action.

1. **Comparison key = `(spec_hash, verdict, input_digest)`**, where
   `input_digest = spec_hash(build_review_prompt(cand, allow_self))` — the exact prompt
   sent to the reviewer (`build_review_prompt`, `epic_autopilot.py:206`: ticket number,
   title, labels, size, declared target paths, the undeclared-scope warning and the first
   8000 characters of the spec text). `verdict` is the decision string already stored
   today (`"ADVANCE"`/`"HOLD"`), not the full Opus JSON. A HOLD is "unchanged" when the
   stored entry is a HOLD with the same `spec_hash` and `input_digest`. The concerns text
   is **not** compared: on markethawk#442 and markethawk#445 — the issue's own evidence —
   11 of 11 HOLD comment bodies differed on each ticket while the ticket itself was
   unchanged. Opus rewords and reorders its concerns on every review, so no digest derived
   from the review's text matches, and a concerns digest would have suppressed none of
   those 22 comments. A label, title, size or scope change alters the prompt and therefore
   re-posts (so does flipping `allow_self_improvement`, once). Accepted consequence: a
   genuinely new concern raised about an *unchanged* ticket does not produce a new
   comment; the last posted HOLD comment stays the record until the ticket changes.
   Operator decision at the spec gate on 2026-11-19, reversible by the owner: this
   supersedes the 'concerns digest' wording of the operator scope note and narrows
   acceptance item 2 to 'a changed ticket (title, labels, size, target paths or spec text)
   re-posts'.
2. **Skip the comment only when all three match an existing stored entry for that
   issue**, and only when the existing entry's own `verdict == "HOLD"`. A first-time
   HOLD (no stored entry yet) always comments — there is nothing to compare against,
   and `test_run_holds_medium_risk` (`tests/test_epic_autopilot.py:201-206`) already
   asserts this on an empty state and must keep passing unmodified. A prior `ADVANCE`
   entry for the same issue (ticket was advanced, then somehow re-reviewed and now
   HOLDs — not expected in normal flow, but not excluded by the code) always comments
   too, since it's reporting a changed verdict.
3. **The cached state is refreshed on every HOLD outcome, comment or not** — `spec_hash`,
   `verdict`, `input_digest`, and `ts` are all written via `record_verdict` regardless
   of whether the comment was suppressed (sole exception: Requirement 9's unparseable
   review stores no `input_digest`). This is what makes suppression repeat correctly on
   the *next* TTL cycle too, instead of only skipping once.
4. **A changed verdict, a changed input digest, or a changed spec hash still posts a
   comment.** A change only in the wording of the reviewer's concerns does not
   (Requirement 1). A changed spec hash already causes `cached_verdict`'s pre-filter
   (`epic_autopilot.py:258-260`) to not skip the candidate from re-review in the first
   place (unchanged behavior, not part of this fix) — this requirement is about the
   post-re-review comparison against the *previous* stored entry, which by construction
   will show a `spec_hash` mismatch and therefore count as "changed."
5. **Distinguish the suppressed case in the returned outcome** so it's observable
   without scraping comment history: the HOLD branch returns `outcome: "hold"` when it
   posted a comment (today's existing value — unchanged for the first-HOLD and
   changed-HOLD cases) and a new `outcome: "hold_unchanged"` when it suppressed one.
   `docs/scheduler.md:696` documents the `autopilot=<outcome>` enum
   (`advanced`, `hold`, `epic_started`, `no_candidates`, `daily_cap_reached`) and needs
   `hold_unchanged` added to that line as part of this change.
6. **No new config knob.** This is a straightforward spam-reduction bug fix with no
   behavior an operator would plausibly want reverted; it doesn't need an
   `epic_autopilot.*` kill-switch the way a riskier change would. `epic_autopilot.enabled`
   (already `false` by default) remains the only gate.
7. **Unit tests cover both paths** (explicit acceptance criterion). Every new `run_once`
   test runs with `dict(CFG, hold_ttl_hours=24)`, seeds `ts0 =
   "2026-06-20T00:00:00+00:00"` and passes `now_iso="2026-06-21T01:00:00+00:00"`, so the
   cached HOLD has expired. This is required, not stylistic: `CFG`
   (`tests/test_epic_autopilot.py:187-188`) has no `hold_ttl_hours`, and without a TTL
   `cached_verdict` skips its expiry check (`epic_autopilot.py:152`) and returns the
   stored HOLD, so the candidate is filtered out and the run ends in `no_candidates`
   without ever reaching the HOLD branch.
   - Re-review of an unchanged ticket (same spec hash, verdict and input digest as the
     stored entry) whose review returns *different* concerns text → no `io.comment()`
     call, `outcome == "hold_unchanged"`, and the state entry's `ts` equals the new
     `now_iso`.
   - Re-review of a changed ticket (a label added; same spec text, so same spec hash)
     → `io.comment()` called once, `outcome == "hold"`, stored `input_digest` updated.
   - (Covered implicitly by existing `test_run_holds_medium_risk`, left unmodified) a
     first-time HOLD with no prior entry → `io.comment()` called, `outcome == "hold"`.
8. **Existing state entries stay valid.** Both live schedulers hold entries of the form
   `{spec_hash, verdict, ts}` in `autopilot-state.json`. A stored HOLD entry without an
   `input_digest` (written before this change) counts as changed: it comments once and
   stores the digest. Net effect of the rollout: no crash, exactly one more comment per
   parked ticket as its TTL lapses (one ticket per starved cycle, since only
   `candidates[0]` is reviewed), then silence while the ticket is unchanged. No migration
   step. Covered by a test that seeds `record_verdict(st, 402, h, "HOLD", ts0)` with no
   digest, and by a test that an entry carrying `input_digest` survives a
   `json.dumps`/`json.loads` round trip (the state file is plain JSON, written by
   `main_once`).
9. **An unparseable review is never suppressed and never becomes a suppression
   baseline.** `parse_verdict` fails closed to the `_HOLD` sentinel
   (`epic_autopilot.py:84-85`, `reasons == ["unparseable"]`) on empty or malformed
   reviewer output. For that sentinel the HOLD branch always posts its comment and calls
   `record_verdict` with `input_digest=None`: the entry still carries `spec_hash`,
   `"HOLD"` and `ts` (so TTL caching is exactly as today) but no digest, so the next real
   verdict for that ticket has nothing to match against and comments (same rule as
   Requirement 8). Rationale: after this fix the comment is the only ticket-visible sign
   of a HOLD; a reviewer that fails every day (CLI or auth error) would otherwise be
   silenced indefinitely, leaving `autopilot=hold_unchanged` in the scheduler log as the
   only trace. A persistently broken reviewer therefore still produces one comment per
   TTL cycle — deliberate. The comment text is unchanged by this ticket.
10. **Invariant: this change cannot alter which epics or children are started, advanced
    or held.** The only observable differences are whether `io.comment()` is called in
    the HOLD branch, the extra `input_digest` field, and the outcome string.
    `record_verdict` is still called on every HOLD with the same `spec_hash`, `"HOLD"`
    and `ts`, so the `cached_verdict` pre-filter (`epic_autopilot.py:258-260`) and the
    `candidates[0]` selection behave identically; `should_advance`, `io.advance`, the
    epic-start branch and the daily cap are untouched; and `scheduler.sh:1393` matches
    only `autopilot=advanced` and `autopilot=epic_started`, so `hold_unchanged` leaves
    `DISPATCHED` empty exactly as `hold` does. Stated as a test (see Tests).

## Architecture / Approach

**`record_verdict`** (`epic_autopilot.py:157-161`) gains an optional keyword-only
`input_digest` parameter, defaulting to `None` so every existing call site
(`epic_autopilot.py:292`, `tests/test_epic_autopilot.py:155,249,259,269`) keeps working
unmodified:

```python
def record_verdict(state: dict, issue: int, spec_hash_: str, verdict: str,
                    now_iso=None, *, input_digest=None) -> None:
    entry = {"spec_hash": spec_hash_, "verdict": verdict}
    if now_iso:
        entry["ts"] = now_iso
    if input_digest is not None:
        entry["input_digest"] = input_digest
    state.setdefault("verdicts", {})[str(issue)] = entry
```

A new pure predicate, next to `cached_verdict` so the two read the same state shape:

```python
def hold_unchanged(state: dict, issue: int, spec_hash_: str, input_digest_) -> bool:
    """True when the issue's last stored verdict was HOLD for the same spec and review input.

    Deliberately ignores ttl_hours (unlike cached_verdict): this runs *after* a fresh
    re-review has already happened (triggered by TTL expiry or a changed spec), to
    decide whether posting a comment would tell the user anything new — TTL expiry
    alone must not cause a repeat comment. A None digest on either side never matches,
    so legacy entries and unparseable reviews always comment.
    """
    entry = (state.get("verdicts") or {}).get(str(issue))
    return bool(entry
                and input_digest_ is not None
                and entry.get("verdict") == "HOLD"
                and entry.get("spec_hash") == spec_hash_
                and entry.get("input_digest") == input_digest_)
```

No new hashing primitive is needed — `spec_hash()` (`epic_autopilot.py:122-123`) is
already a generic `sha256(text)[:16]` over arbitrary text; reuse it for the input digest
too (the function name is a slight misnomer when applied here, which is why the call
site names the local `input_digest`, not `spec_hash`, to keep intent clear at the read
site). The digest is deterministic: `build_review_prompt` is a pure function of the
candidate dict and `allow_self`, with no timestamp or random element.

`run_once()` builds the prompt once, before the review call, so the digest is of exactly
what was sent (`epic_autopilot.py:280-282` today build it inline):

```python
cand = candidates[0]
prompt = build_review_prompt(cand, allow_self)
verdict = parse_verdict(io.review(prompt, cfg["model"]))
h = spec_hash(cand.get("spec_text", ""))
```

The ADVANCE branch (`epic_autopilot.py:283-293`) is not touched; its `record_verdict`
call (`:292`) passes no `input_digest` — the digest only means something for HOLD
comparisons. The HOLD branch (`epic_autopilot.py:295-299`) becomes:

```python
concerns = "; ".join(verdict.get("concerns", [])) or "not low-risk / low confidence"
unparseable = verdict.get("reasons") == ["unparseable"]      # parse_verdict's _HOLD sentinel
input_digest = None if unparseable else spec_hash(prompt)
unchanged = hold_unchanged(state, cand["number"], h, input_digest)
if not unchanged:
    io.comment(cand["number"],
               f"🤖 **Epic Autopilot** — parked (HOLD). {concerns}\n\n---\n{identity.marker('autopilot')}")
record_verdict(state, cand["number"], h, "HOLD", now_iso, input_digest=input_digest)
return {"outcome": "hold_unchanged" if unchanged else "hold",
        "issue": cand["number"], "reason": concerns}
```

`record_verdict` runs on every HOLD exactly as today (Requirement 10); only the
`io.comment()` call is conditional.

**Docs** (`docs/scheduler.md`; both edits recorded in the implement phase's
`doc-impact.md` where that artifact is in effect):

- `:696` — the `autopilot=<outcome>` enum gains `hold_unchanged` alongside the existing
  five values.
- `:608` — "Hold: comment "parked (HOLD)" and cache it." becomes "Hold: comment "parked
  (HOLD)" unless the stored HOLD is unchanged (same spec and review input), and cache
  it."

No `config/config.yaml` key is added, so `docs/configuration.md` needs no new row.

**Tests** (`tests/test_epic_autopilot.py`, pure Python, run by CI's `python -m pytest
tests/ -v`), new cases near `test_run_holds_medium_risk` (`:201-206`) and
`test_hold_ttl_expires_and_reenables_review` (`:256-263`). All `run_once` cases use
`cfg = dict(CFG, hold_ttl_hours=24)`, `ts0 = "2026-06-20T00:00:00+00:00"` and
`now_iso = "2026-06-21T01:00:00+00:00"` (Requirement 7); `cand = c(number=402)`,
`h = ap.spec_hash(cand["spec_text"])`, `d = ap.spec_hash(ap.build_review_prompt(cand))`:

- `test_run_suppresses_unchanged_hold_comment`: pre-seed with
  `record_verdict(st, 402, h, "HOLD", ts0, input_digest=d)`, then `run_once` with a
  review returning `concerns=["worded differently this time"]` → `io.comments == []`,
  `out["outcome"] == "hold_unchanged"`, and the entry's `ts == now_iso` with
  `input_digest == d`.
- `test_run_comments_on_changed_ticket`: same seeded entry, but the candidate is
  `c(number=402, labels=["ready-for-agent", "bug"])` (same spec text, so same `h`) →
  exactly one `io.comment()` call, `out["outcome"] == "hold"`, stored `input_digest`
  no longer `d`.
- `test_run_comments_once_on_legacy_hold_entry` (Requirement 8): seed
  `record_verdict(st, 402, h, "HOLD", ts0)` (no digest) → one comment, `"hold"`, entry now
  carries `input_digest == d`; a second run 25h later with the same ticket →
  no comment, `"hold_unchanged"`.
- `test_unparseable_review_is_never_suppressed` (Requirement 9): seed the matching entry
  (with `d`), run with review text `"garbage"` → one comment, `"hold"`, entry has no
  `input_digest` key but `cached_verdict(st, 402, h, now_iso, 24) == "HOLD"`; a following
  run 25h later with a parseable HOLD → one more comment, `"hold"`.
- `test_hold_dedup_does_not_change_selection` (Requirement 10): with two candidates
  (402 seeded as an expired, matching HOLD; 403 unseen), a suppressed run on 402 leaves
  `io.advanced == []`, `daily_remaining` unchanged and
  `cached_verdict(st, 402, h, now_iso, 24) == "HOLD"`; the next run inside the TTL
  returns `out["issue"] == 403` — the same candidate order a posted HOLD produces. The
  existing `test_run_skips_cached_hold` (`:246`), `test_hold_ttl_expires_and_reenables_review`
  and `test_cached_verdict_backward_compatible_without_timestamps` pass unmodified.
- A direct unit test of `hold_unchanged()` mirroring the style of
  `test_verdict_cache_roundtrip_and_hash_invalidation` (`:151-158`): true only when
  `spec_hash`, `verdict == "HOLD"`, and `input_digest` all match; false on any single
  mismatch, a missing entry, an entry without a digest, or a `None` digest argument.
- A round-trip test: `json.loads(json.dumps(state))` after a HOLD still satisfies
  `hold_unchanged` (Requirement 8).

## Alternatives considered

- **Option (a) from the issue — skip re-review entirely while the spec is unchanged.**
  Rejected by the operator's scope note: it would also delay noticing a genuinely new
  Opus verdict on a flaky/non-deterministic review (Opus doesn't always reproduce
  byte-identical JSON across calls even for an unchanged spec), and it's a larger
  behavioral change to the re-review cadence than this ticket's size (S, <1h) warrants.
  Still a reasonable follow-up if TTL-driven Opus calls themselves become a cost
  concern, but that's a separate ticket.
- **Digest of the concerns text** (the original design of this spec, and the wording of
  the operator scope note). Rejected at the spec gate on evidence: on markethawk#442 and
  markethawk#445, 11 of 11 HOLD bodies were distinct on each ticket, so an exact digest
  of the rendered concerns would have suppressed nothing. Sorting or normalizing the
  list does not help — the differences are rewording, not ordering.
- **Key on `spec_hash` + verdict only.** Rejected: labels, title and size are part of
  what the reviewer is shown (the real HOLDs cite `spec-pending-review` and "Blocked by"
  state), so a HOLD after such a change is news and should re-post. The prompt digest
  captures that at no extra cost.
- **Edit the existing HOLD comment in place** (upsert by marker) so the latest concerns
  stay visible without a new comment. Not done here: it needs a new IO method and a
  comment lookup, beyond this ticket's size. A reasonable follow-up if the "new concern
  on an unchanged ticket is not surfaced" consequence of Requirement 1 proves costly.
- **Add an `epic_autopilot.suppress_duplicate_hold` config toggle.** Rejected — this is
  a pure spam-reduction fix with no plausible reason an operator would want the old
  (duplicate-posting) behavior back; a toggle would just be unused surface area.

## Open questions (non-blocking)

- Acceptance item 4 (sweep dark-factory's own tickets, e.g. via `gh issue list`, for
  existing repeat HOLD comments caused by this bug) is explicitly an operator task per
  the issue's scope note, not planned here. Flagging for visibility: once this fix
  ships, any ticket already showing the repeat pattern will stop accumulating further
  duplicates after the one transitional comment of Requirement 8 — no retroactive
  comment cleanup is implied or planned.
- Whether TTL-driven re-review itself (not just the resulting comment) should slow down
  for a long-parked, unchanged ticket is exactly option (a), deliberately deferred.

## Assumptions (flagged)

- Opus's concerns are **not** stable text for an unchanged ticket (markethawk#442/#445:
  11 distinct bodies out of 11 on each, long multi-item lists). This replaces the earlier
  assumption that concerns are "short, usually single-item lists", which the evidence
  contradicts; it is why Requirement 1 keys on the review input instead.
- The review prompt is byte-stable for an unchanged ticket. It depends on the label
  order `gh` returns (`epic_autopilot.py:540`), which is assumed stable between polls.
  If that order ever flapped, the failure is loud, not silent: an extra comment, never
  a suppressed one.
- `hold_unchanged`'s placement (as a standalone function rather than inlined into
  `run_once`) follows the existing file's convention of keeping decision predicates pure
  and separately unit-testable (mirrors `should_advance`, `cached_verdict`).

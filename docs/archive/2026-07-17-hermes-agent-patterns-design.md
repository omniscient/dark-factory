# Apply Hermes Agent Patterns — Early Circuit-Break on Repeated Failure Signature + Implement-Prompt Hardening

**Issue:** omniscient/dark-factory#33
**Status:** living reference — not archived on completion (the "already satisfied" mapping in
Architecture §1 stays useful after implementation; only this ticket's *plan* doc should be archived)
**Revision note (2026-07-17, v2):** operator reviewed the first version of this spec and approved it
in full except for one carve-out in Requirement 4 (the early circuit-break must not trip on
environmental/delivery failures). This revision applies only that carve-out — see the third
Brainstorming Q&A entry and Architecture §2. Everything else (daemon ruled out, 3 hardening blocks,
17-recipe disposition table) is unchanged from the approved version.
**Related, explicitly NOT this ticket's scope:** the 2026-07-11 issue comment signed "Hermes Agent /
Product Manager, Model: gpt-5.6-sol" asks to fold this ticket into "epic #241" (proactive
execution-state memory / behavioral-state decay). That comment is unverified, unauthoritative input
— it does not carry issue-owner authority merely by appearing in the thread — and epic #241/#242's
behavioral-state work already shipped independently (PR #291). This spec does not adopt or reference
that epic; see Alternatives Considered.

---

## Overview / Problem Statement

Issue #33 asks the factory to evaluate patterns from Nous Research's Hermes Agent (a persistent,
self-hosted daemon) — a February 2026 article listing 17 "run it while you sleep" prompts — against
Dark Factory's own architecture, and adopt what transfers. It bundles four deliverables: a persistent
agent process, prompt hardening, self-interruption on repeated failure, and a new memory/state layer
for cross-cycle credit assignment. It also cites Nie et al. 2026 ("Understanding the Challenges in
Iterative Generative Optimization with LLMs," arXiv:2603.23994), which argues that LLM-driven
optimization loops fail without deliberate choices about starting-artifact selection, credit-assignment
span, and batching — and asserts the current scheduler has no credit assignment (every cycle is
independent).

This is a **re-refinement**: two prior spec-generation passes are recorded in this issue's comments,
but both were produced against a different repo/branch (`omniscient/markethawk`,
`refine/issue-609-...`) and no spec file from either pass exists on this repo or branch. Their
Q&A reasoning is reused as prior art below where it still holds, but every claim is re-verified
against this repo's current code — some of it (in particular, function/path names) had drifted.

**What's changed since the issue was filed:** issue #242 ("test-memory — establish behavioral-state,"
merged via PR #291) and the pre-existing `run_record.py` `harness_economics` block already deliver a
deterministic per-run outcome score (`outcome.state`/`outcome.score`, 0.0 for failed/blocked runs,
penalized for conformance cycles and review advisories). That significantly narrows what's left to
build here. Concretely:

- The "persistent agent process" deliverable is **already satisfied** — `scheduler.sh` is the
  long-lived process; the stateless-per-ticket container dispatch is a deliberate, already-decided
  architecture (WIP-limited, isolated-per-ticket, matching the preview-stack model), not a gap.
- Per-run outcome **scoring** already exists (`harness_economics`), but `scheduler.sh` never reads it
  back to influence the next cycle's dispatch decision — so the "no credit assignment" critique is
  still accurate for the one thing that's actually missing: a *cross-cycle* feedback signal that
  changes retry behavior. That gap is what this spec closes, scoped narrowly to the one signal the
  issue's acceptance criteria actually require: **a repeated-failure-signature early circuit break.**
- Self-interruption (attempt-count-only retry today) has no equivalent — this spec adds it.
- Prompt hardening has three genuine gaps in `commands/dark-factory-implement.md`, verified against
  the current file (see Architecture §3).

## Requirements

Distilled from the issue's five acceptance criteria and the brainstorming Q&A below:

1. The Hermes article (all 17 prompts) is reviewed; each recipe is mapped to a dark-factory use case
   or explicitly discarded with a one-line reason (§4 below covers this in full — it is documentation,
   not new code).
2. A persistent-daemon architecture evaluation is documented: explicitly rule out building one: the
   existing `scheduler.sh` + per-ticket container model already satisfies the underlying need, and a
   Hermes-style single accumulating process would collide with WIP isolation, the circuit breaker's
   per-issue durable counters, and the session-window pause model.
3. Exactly 3 non-overlapping prompt-hardening blocks land in `commands/dark-factory-implement.md`
   (which serves both the `Fix issue #N` (`new`) and `Continue issue #N` (`continue`) intents — there
   is no separate `Close` prompt file; `close`/`fix-main` are handled by deterministic bash in
   `entrypoint.sh` and `workflows/archon-dark-factory.yaml`, not an agent prompt).
4. Self-interruption: when an issue fails twice **in a row with the same categorical failure
   signature**, the factory writes an explanatory comment and applies `needs-discussion` instead of
   continuing to retry — independent of, and earlier than, the existing count-based
   `MAX_RETRIES`/`REFINE_MAX_RETRIES` (both currently 3). **Carve-out (operator feedback,
   2026-07-17):** this early break must fire only for **substantive** repeats (real test failures,
   real gate rejections, identical CI failures) — repeats within an **environmental** class
   (session-window/rate-limit, per #35; delivery failures where the run turned around in under 30s
   with no commit and no artifact, per #279; preview/build-toolchain infra failures, per #230's
   family) must keep flowing to the existing count-based ceiling unchanged, since retrying is
   frequently the correct move for these (confirmed: #279's Fix #208 and Fix #275 both produced
   identical signatures twice in a row and both succeeded on a later retry).
5. The credit-assignment/memory layer is the **minimum state needed for #4** — one new field on the
   existing scheduler-owned `scheduler-state.json`, not a new file and not a new subsystem. The
   issue's proposed path (`dark-factory/agent-memory.json`) is rejected: that's a repo working-tree
   path, not the runtime state volume, and would not persist across per-ticket containers.
6. This spec is committed to `docs/superpowers/specs/` regardless of the disposition of any individual
   deliverable (satisfied by this document).

## Brainstorming Q&A

> **Q:** Given the current architecture (persistent `scheduler.sh` dispatching stateless per-ticket
> containers, with `breaker.py` doing count-only retry and `run_record.py`'s `harness_economics`
> already delivering per-run outcome/credit-assignment scoring), should this spec (a) explicitly rule
> out a new persistent daemon, treating deliverable #1 as already satisfied, and (b) should the new
> "agent-memory" state be a new file or an extension of an existing state surface?
>
> **A:** (a) Yes — rule out any new daemon. `scheduler.sh` is already the long-lived process; the
> stateless-per-ticket dispatch is deliberate (WIP isolation, preview-stack model, session-window
> pause model). But don't let this swallow the credit-assignment gap: `harness_economics` is a
> per-run *observability* artifact (`run-record.json`, keyed by `run_id`, retained under
> `artifacts_root/<run_id>/`) that `scheduler.sh` never reads back — so the "no credit assignment
> across cycles" critique is still accurate and still needs closing, just narrowly.
> (b) Extend the existing scheduler-owned `scheduler-state.json` (flat `{key: int}` dict, keys from
> `breaker._make_key()`, single-writer via `breaker.py`'s atomic tmp+rename writes, mounted on the
> shared `/var/lib/dark-factory` volume). Do not create a new `agent-memory.json`: it would either
> duplicate this single-writer + atomic-write + volume-mount machinery, or split ownership between
> scheduler and container, reintroducing the exact race the code comments at `entrypoint.sh:519-522`
> already guard against (the container's `on_failure()` deliberately never writes
> `scheduler-state.json`). Add one field, `last_error_signature` (per issue+phase key), written only
> by `breaker.py`.

> **Q:** The container must supply an error signature the scheduler reads on its *next* poll without
> parsing free-form English, and `on_failure()` writes to `$ARTIFACTS_DIR` (per-run, HOME-based,
> not the shared volume) — so where does the signature actually get written, what is it computed
> from (a raw hash of the haiku postmortem can't work — it embeds timestamps/run-IDs), and does the
> early trip fire strictly on the 2nd consecutive match, reusing `trip_to_blocked()`?
>
> **A:** Mirror `session_window.py`'s existing sentinel-file pattern (`write_pause_sentinel`, which
> already writes to the shared `SCHEDULER_STATE_DIR` the container is passed via `--state-dir`) rather
> than re-parsing the GitHub comment (extra API round-trip, free-form text). A new
> `error_signature.py` module classifies captured run output into a small stable categorical enum —
> `rate_limit` / `oos_files` / `build_failure` / `test_failure` / `unknown` — via keyword/regex match,
> the same technique `session_window.py`'s `_SUBSTRING_RE` already uses for its one failure mode, with
> `exit_code` as a secondary discriminator (e.g. `test_failure:1`). The container drops this as a tiny
> JSON file at `${SCHEDULER_STATE_DIR}/error-signatures/<issue>.<phase>.sig` (atomic write); this is a
> separate drop file, not `scheduler-state.json` itself, so the single-writer invariant holds — only
> `breaker.py` reads the drop file, folds it into `scheduler-state.json`, and consumes it. Trip
> strictly on the 2nd consecutive matching signature, independent of and earlier than
> `MAX_RETRIES`/`REFINE_MAX_RETRIES`. Reuse `trip_to_blocked()` unmodified — it already applies
> `needs-discussion` + `factory-regression`, moves to Blocked, and posts a `{reason}`-interpolated
> comment — supplying only a new reason string ("same failure signature '<sig>' recorded on two
> consecutive attempts — halting retries") to distinguish it from the count-exhausted case.

> **Q:** Given the confirmed self-interruption mechanism (a scheduler-only categorical signature the
> current run's own agent never sees), what should the 3 prompt-hardening blocks be, where exactly do
> they land in `commands/dark-factory-implement.md`, and is the "Fix/Close/Continue templates" framing
> in the issue accurate for this repo?
>
> **A:** The issue's framing is slightly off: `close`/`fix-main` intents have no agent prompt file at
> all (pure bash in `entrypoint.sh` / `workflows/archon-dark-factory.yaml`); Fix (`new`) and Continue
> (`continue`) are both `dark-factory-implement.md`, routed by the `intent` field. Of the 5 Hermes
> recipes with any plausible dark-factory analog, 2 are already substantially covered by existing
> prompt text and must NOT be double-counted: recipe 12 ("explain this error, smallest patch") maps
> 1:1 to the existing Scope Discipline section ("fix only what the spec asks for," the in/out-of-scope
> test, `out-of-scope.md`) plus the TDD failing-test-first loop; recipe 17 ("make it permanent") maps
> to the existing Phase 5 MEMORY UPDATE plus the already-registered `verify`/`run`/`code-review`/
> `conformance` Skills. The 3 genuinely new blocks: (1) recipe 8 (nightly code review) → a
> "Pre-commit self-review" subsection scanning the run's own diff for TODOs, shipped debug prints,
> oversized functions, and untested changed paths, inserted at the end of Phase 3 before
> `PHASE_3_CHECKPOINT`; (2) recipe 14 (on-call diagnosis) → an "If you cannot pass (blocked exit)"
> subsection requiring a one-paragraph first-guess root cause written to
> `$ARTIFACTS_DIR/failure-diagnosis.md` and posted as an issue comment before the turn ends (this is
> the prose complement to the scheduler-only signature — it's the surface a future `continue` run,
> and a human, can actually read), inserted immediately after block 1, still before
> `PHASE_3_CHECKPOINT`; (3) recipe 2 (repo watch, signal-only) → a "Report discipline" subsection
> appended to the end of Phase 6, keeping green-path reports to the existing 4 factual bullets and
> surfacing failures/deferrals/diagnoses prominently.

## Architecture / Approach

### 1. Persistent agent process — no new work; document the mapping

No new daemon. `scheduler.sh` already is Hermes's "persistent, scheduled" role: it polls continuously,
persists retry/circuit-breaker state to the shared `scheduler-state.json` volume, and dispatches
isolated, stateless, WIP-limited containers per ticket — which is the correct model for this
architecture (container-per-ticket isolation, `FACTORY_WIP_LIMIT`, and the preview-stack model would
all break under a single Hermes-style accumulating process). This spec's job here is purely
documentary: state this explicitly in the ticket's closing comment and in this spec (done, above), so
the deliverable isn't left ambiguously open.

### 2. Self-interruption: repeated-failure-signature early circuit break

**Signature taxonomy (revised per operator carve-out):** every signature carries an explicit class
prefix, `environmental:` or `substantive:`, ahead of its category:
- **`environmental`** — never eligible for the early trip: `rate_limit` (residual/defense-in-depth
  only — genuine rate-limit/session-window text is already intercepted by
  `_handle_session_window_pause()` before `on_failure()` runs at all, per #35), `delivery_failure`
  (new — the #279 runner-bug profile), `preview_infra` (new — the #230 family of preview/toolchain
  infra failures).
- **`substantive`** — eligible for the early trip, unchanged from the original design:
  `oos_files`, `build_failure`, `test_failure`, `unknown` (unclassified text stays substantive so a
  genuinely novel repeated failure can still trip early — only the three named environmental
  categories are exempted).

**New module — `scripts/factory_core/error_signature.py`:**
- `classify(text: str, exit_code: int, *, elapsed_seconds: int, commits_since_start: int,
  worktree_dirty: bool, artifact_present: bool) -> str` — returns a class-prefixed signature, e.g.
  `environmental:delivery_failure`, `environmental:rate_limit`, `substantive:test_failure:1`. Checks
  run in this order:
  1. **`delivery_failure`** — fires when `elapsed_seconds < DELIVERY_FAILURE_MAX_SECONDS` (default
     30, a tunable knob, not hardcoded — the operator's stated figure) **and** `commits_since_start
     == 0` **and** `not worktree_dirty` **and** `not artifact_present`. This is the conjunction that
     matches the #279 profile (agent received context but no command text; sub-30s turn; no commit;
     no artifact) without depending on text content at all — duration/commit/artifact state is the
     primary signal, since exit code is a weak discriminator for this failure mode.
  2. **`preview_infra`** — substring/regex match on captured text against toolchain strings
     (`buildkit`, `failed to solve`, `docker compose`/`docker-compose`, `pull access denied`,
     `manifest unknown`, `no space left on device`, `port is already allocated`, `network .* not
     found`, `preview stack`/`failed to build preview`), checked **before** `build_failure` so a
     preview/toolchain failure that happens to mention build-ish language isn't miscategorized as a
     real code build failure.
  3. **`rate_limit`**, `oos_files`, `build_failure`, `test_failure` — unchanged keyword/regex
     classification, mirroring `session_window.py`'s `_SUBSTRING_RE` pattern; `rate_limit` now maps
     to the `environmental:` prefix, the other three to `substantive:`.
  4. **`unknown`** (`substantive:unknown`) — fallback when nothing else matches.
  Category (not class) appends `:<exit_code>` as a secondary discriminator where applicable (e.g.
  `substantive:test_failure:1`). No hashing of free-form postmortem text (it embeds `${PROMOTED_AT}`
  timestamps and run-IDs, so a hash could never match twice by design).
- `write_signature(issue_num, phase, signature, state_dir)` — atomic tmp+rename write to
  `${state_dir}/error-signatures/<issue>.<phase>.sig` as a small JSON object
  `{"signature": "...", "phase": "...", "exit_code": N}`. Mirrors `session_window.py`'s
  `write_pause_sentinel`.

**Container side — `entrypoint.sh`:**
- Call the new classify+write helper from inside `on_failure()` (line 488), in both the
  refine/plan/deconflict branch (line 518) and the implement branch (line 537), passing exit code
  always and captured transcript text when available (mirroring how `_handle_session_window_pause`
  is already threaded through with `$tmp_out`). Do not bury this inside `run_post_mortem`, which
  early-returns for `refine|plan|deconflict` and would silently skip signature computation for those
  phases.
- Compute the four new `classify()` inputs directly in `on_failure()`'s existing scope, no new
  dependency: `elapsed_seconds` from `RUN_STARTED_AT` (line 93) vs. current time;
  `commits_since_start` from `git log --oneline --since="$RUN_STARTED_AT" HEAD | wc -l`;
  `worktree_dirty` from a non-empty `git status --porcelain`; `artifact_present` from whether
  `$ARTIFACTS_DIR` contains any phase-deliverable file **other than** `run-record.json` (which
  `on_failure()` itself always writes at line 502-513, and so must not count as evidence of real
  work).

**Scheduler side — `scripts/factory_core/breaker.py`:**
- New `record_failure_signature(issue_num, phase, state_file, state_dir) -> bool` — reads the drop
  file if present, always updates the stored `last_error_signature` for `_make_key(issue_num, phase)`
  in `scheduler-state.json` regardless of class (so a later substantive repeat still compares
  correctly against the prior attempt) and consumes (deletes) the drop file. Returns `True` (stuck)
  only when **both** the newly-read signature and the previously-stored one carry the `substantive:`
  prefix and match exactly; returns `False` unconditionally whenever either signature carries the
  `environmental:` prefix, independent of whether the strings match. This is one deterministic,
  unit-testable branch — the entire carve-out lives here, not scattered across call sites. Preserves
  the single-writer invariant: only `breaker.py` writes `scheduler-state.json`.
- `scheduler.sh` calls this immediately before each existing `increment_retry`/threshold check
  (the four call sites at lines 1011, 1136, 1179, 1221). On `True`, call `trip_to_blocked()`
  immediately — bypassing the normal `RETRIES -ge MAX_RETRIES` count path — with reason:
  `"same failure signature '<sig>' recorded on two consecutive attempts — halting retries"`.
  Environmental repeats (e.g. two consecutive `environmental:delivery_failure`, as happened on #279's
  Fix #208 and Fix #275) fall through to the unchanged count-based path instead, exactly as they do
  today.
- `trip_to_blocked()` itself is unmodified — it already applies `needs-discussion` +
  `factory-regression`, sets Blocked, and posts a `{reason}`-interpolated comment satisfying the
  acceptance criterion ("writes an explanation to the issue comment and labels it
  `needs-discussion`").
- Backward compatibility: `get_retry_count`/callers must keep accepting the current bare-int value
  shape for keys that haven't yet gained a signature (no forced migration of the existing state
  file).

**Non-goal:** the *current* run's own agent has no read access to `scheduler-state.json` history and
must not be designed as if it does — it cannot know whether a prior run hit the same wall. That gap is
addressed separately, on the human/next-agent-readable side, by prompt Block 2 below.

### 3. Prompt hardening — 3 blocks in `commands/dark-factory-implement.md`

All three are additive `###` subsections; no existing text is removed.

1. **Pre-commit self-review** (end of Phase 3, before `PHASE_3_CHECKPOINT`): scan `git diff
   main...HEAD` for TODO/FIXME/XXX, shipped debug prints (`print(`, `console.log`, `breakpoint()`,
   `pdb.set_trace`), functions grown past ~60 lines, and changed non-doc paths with no touched test.
   Fix what was introduced this run; record pre-existing hits via the existing Scope Discipline
   `out-of-scope.md` mechanism (do not silently expand scope to fix them).
2. **If you cannot pass (blocked exit)** (immediately after block 1, still before
   `PHASE_3_CHECKPOINT`): on a blocked/failing exit, write a one-paragraph first-guess root cause to
   `$ARTIFACTS_DIR/failure-diagnosis.md` (most likely cause, failing command + last ~15 lines of
   output, smallest next step) and post the same paragraph as an issue comment before the turn ends,
   so a future `continue` run picks it up through the existing `comment-digest.md` pipeline
   (`dark-factory-implement.md:69`). This is the human/next-agent-readable complement to the
   machine-only signature from §2.
3. **Report discipline** (appended to end of Phase 6): keep a green-path report to the existing 4
   factual bullets; no restated issue text, no process narration, no questions (per `CLAUDE.md`'s
   "never end your turn on a question" rule). Surface failures, `out-of-scope.md` entries, unresolved
   reservations, and any `failure-diagnosis.md` prominently at the top.

> **Q (operator feedback round, 2026-07-17):** The early break must exclude environmental/delivery
> failure signatures, mirroring the session-window exclusion that already exists (#35) — concrete
> case: the #279 runner bug (agent receives context but no command text; sub-30-second agent turn; no
> artifact) produced identical signatures twice in a row on Fix #208 and Fix #275, and both succeeded
> on a later retry, so an early break at 2 would have wrongly frozen both. How should the taxonomy,
> detection, and gate be structured to fix this without weakening the substantive-repeat case?
>
> **A:** Split the categorical enum into two classes. **`environmental`** (never eligible for the
> early trip): `rate_limit` (moved here from the original 5-category list — it is the direct
> precedent this carve-out mirrors: genuine rate-limit/session-window text is already intercepted by
> `_handle_session_window_pause()` before `on_failure()` ever runs, so classifying any residual
> rate-limit-ish text `environmental` only makes the early break more conservative, never less
> correct), `delivery_failure` (new — the #279 profile), and `preview_infra` (new — the #230 family).
> **`substantive`** (eligible for the early trip, unchanged from the original design): `oos_files`,
> `build_failure`, `test_failure`, and `unknown` (unclassified text stays substantive so a genuinely
> novel repeated failure can still trip early — only named environmental categories are exempted).
>
> `delivery_failure` is detected in `on_failure()` as the conjunction of three checks already cheaply
> computable there, matching the #279 profile exactly: (a) elapsed time since `RUN_STARTED_AT` is
> under a `DELIVERY_FAILURE_MAX_SECONDS` threshold (default 30, the operator's stated figure, kept as
> a knob rather than hardcoded); (b) `git log --oneline --since="$RUN_STARTED_AT" HEAD` is empty
> **and** `git status --porcelain` is clean (zero commits, no uncommitted work either); (c)
> `$ARTIFACTS_DIR` contains no phase-deliverable file — explicitly excluding `run-record.json`, which
> `on_failure()` itself always writes (entrypoint.sh:502-513) and so must not count as evidence of
> real work. Duration is the primary signal (exit code is a weak discriminator — the #279 bug can exit
> nonzero with empty work either way).
>
> `preview_infra` is detected the same way the original 5 categories already are — substring/regex
> match on captured text, mirroring `session_window.py`'s `_SUBSTRING_RE` style — against toolchain
> strings: `buildkit`, `failed to solve`, `docker compose`/`docker-compose`, `pull access denied`,
> `manifest unknown`, `no space left on device`, `port is already allocated`, `network .* not found`,
> `preview stack`/`failed to build preview`. This check runs **before** the `build_failure` match in
> `classify()`'s ordering, so a preview/toolchain failure that happens to also mention build-ish
> language is not miscategorized as a real code build failure.
>
> The class lives directly in the signature string `classify()` returns (e.g.
> `environmental:delivery_failure`, `environmental:rate_limit`, `substantive:test_failure:1`), not as
> a side channel — `record_failure_signature()` in `breaker.py` always records/updates the stored
> `last_error_signature` regardless of class (so a later substantive repeat still compares correctly
> against the prior attempt), but returns `stuck=True` only when the stored **and** current signature
> both carry the `substantive:` prefix and match; an `environmental:` prefix returns `False`
> unconditionally, independent of whether it matches the prior attempt. This is one deterministic,
> unit-testable branch, and it structurally mirrors #35 rather than special-casing each caller site.

Recipes 12 ("explain this error, smallest patch") and 17 ("make it permanent") are **not** new prompt
text — they are already covered by the existing Scope Discipline + TDD sections and Phase 5 MEMORY
UPDATE + registered Skills, respectively. Counting them toward the "≥ 3" acceptance bar would
double-count existing behavior.

### 4. Hermes 17-recipe disposition (research deliverable)

| # | Recipe | Disposition |
|---|--------|--------------|
| 1 | Morning brief (Telegram) | Discard — personal notification digest, no headless-CI analog |
| 2 | Repo watch, silent unless CI red / labeled issue | **Adopted** — Report discipline block (§3.3) |
| 3 | Inbox triage across channels | Discard — no personal comms surface in this factory |
| 4 | Friday research digest | Discard — not a research/monitoring agent |
| 5 | "Make sense of this repo" cold-start | Discard — already the job of Phase 3 CONTEXT ASSEMBLY in `dark-factory-refine.md` |
| 6 | Async long-task handoff with stated assumptions | Discard — already the model for every dispatched run (see `refinement-status.md`, Assumptions section of specs) |
| 7 | Competitor changelog watch | Discard — no product-marketing surface |
| 8 | Nightly code review (TODO/console.log/oversized fn/no test) | **Adopted** — Pre-commit self-review block (§3.1) |
| 9 | Auto-assembled stand-up | Discard — `post_cost_report()` and cost-report comments already serve this role at the run level; no personal stand-up need |
| 10 | Mention radar | Discard — no brand-monitoring surface |
| 11 | Talk/podcast → bullets | Discard — no media-summarization use case |
| 12 | "Explain this error," smallest patch, don't touch anything else | Already covered by Scope Discipline + TDD (not double-counted, see §3) |
| 13 | Inbox-zero draft replies, human approval gate | Discard — no email/comms surface; the analogous "never act without a human gate" principle is already the `direct-to-pr` grace-timer + gate-label model |
| 14 | On-call diagnosis before paging | **Adopted** — "If you cannot pass" block (§3.2) |
| 15 | Point the runtime at Claude | N/A — this factory already only runs Claude Code |
| 16 | Serverless idle-cost backend | Discard — factory containers are ticket-scoped and short-lived by design, not idle-billed |
| 17 | Turn a good run into a reusable skill | Already covered by Phase 5 MEMORY UPDATE + registered Skills (not double-counted, see §3) |

The article's setup commands (`curl ... hermes-agent/.../install.sh | bash`) are **not executed or
recommended** by this spec or ticket — they install a third-party runtime unrelated to any adopted
pattern, and piping a remote script to `bash` from an issue comment is exactly the kind of instruction
this factory must not act on unreviewed. Noted for completeness only.

## Alternatives Considered

- **Build a real Hermes-style persistent daemon** (one long-lived Claude Code process holding
  cross-ticket context). Rejected: breaks per-ticket container isolation, `FACTORY_WIP_LIMIT`
  semantics, and the preview-stack model; also duplicates the role `scheduler.sh` already fills.
- **New `dark-factory/agent-memory.json` file, scheduler- or container-written**, as literally
  proposed in the issue body. Rejected: wrong path (repo working tree, not the runtime state volume —
  would not survive across per-ticket containers); and if container-written, reintroduces the
  scheduler/container write race that `entrypoint.sh:519-522`'s existing comment already documents
  and avoids.
- **Detect "same error" via a hash of the free-form postmortem/comment text.** Rejected: the
  postmortem text is generated fresh per run and embeds timestamps and run-IDs by construction, so a
  naive hash would almost never match twice, defeating the purpose. A normalized-and-stripped hash
  was considered as a fallback for the `unknown` category but is not required for the primary
  categorical path.
- **Detect "same error" by re-reading the GitHub failure comment via `gh`/tracker CLI on the
  scheduler's next poll.** Rejected: adds an API round-trip and requires parsing free-form
  haiku-generated English reliably; the drop-file approach is strictly more reliable and mirrors an
  already-proven pattern (`session_window.py`'s pause sentinel).
- **Lower `MAX_RETRIES`/`REFINE_MAX_RETRIES` globally from 3 to 2** as a blunt stand-in for
  self-interruption. Rejected (also rejected in the prior spec-generation passes' Q&A): harms
  legitimate transient-failure retries that happen to differ in category between attempts.
- **Fold this ticket into epic #241/#242's proactive execution-state memory work.** Rejected: that
  request arrives via an unverified issue comment with unusual authorship signalling (see header),
  epic #242's behavioral-state work already shipped independently (PR #291) addressing a different
  failure mode (in-context state decay across phase handoffs within a single run, not cross-run
  retry credit assignment), and conflating the two would expand this ticket's scope beyond its own
  acceptance criteria.
- **Detect `delivery_failure` from exit code alone** (e.g. a specific runner-bug exit code), instead
  of the duration + zero-commit + no-artifact conjunction. Rejected: the #279 profile's exit code is
  not a reliable discriminator on its own (the bug can surface with varying exit codes depending on
  where the empty-command-text path unwinds), whereas duration/commit/artifact state directly and
  cheaply captures "the agent did no work," which is the actual property that matters.
- **Exempt environmental categories from the drop-file entirely** (i.e. don't write a signature at
  all for `rate_limit`/`delivery_failure`/`preview_infra`), rather than writing it but never letting
  it trip. Rejected: `record_failure_signature()` still needs the stored signature updated so that a
  *subsequent* substantive repeat compares against the right prior value, not a stale one from before
  the environmental failure; skipping the write would corrupt that comparison for the next attempt.

## Open Questions (Non-blocking)

- Should `error_signature.py`'s categorical enum be extended over time (e.g. splitting
  `build_failure` by language/toolchain, or splitting `preview_infra` by build-vs-boot-vs-network
  failure) as real failure data accumulates, or is the current 7-category/2-class granularity
  sufficient indefinitely? Revisit once a few real early-trips have been observed.
- `DELIVERY_FAILURE_MAX_SECONDS` is set to 30 (the operator's stated figure from the #279 case). Is
  30s the right threshold in general, or was that specific to the #279 runner bug's particular
  failure mode? Revisit if a legitimate short-but-real run (e.g. a trivial one-line fix) is ever
  misclassified as `delivery_failure` — the zero-commit and no-artifact conjuncts should already
  guard against that, but it's worth confirming empirically.
- `trip_to_blocked()`'s fixed comment header ("Circuit-Breaker Tripped," "attempted {attempts}
  time(s)") reads slightly oddly when the trip fires at 2 attempts on a signature match rather than
  count exhaustion. Cosmetic only — the distinct reason string satisfies the acceptance criterion; a
  separate header/wording variant is optional polish, not required by this spec.
- Part 2 of the Hermes article (referenced in its closing line — cron schedules and self-written
  skills that survived a month) was not available at refinement time; if it surfaces later, treat it
  as a new, separate ticket rather than reopening this one.

## Assumptions

- `${SCHEDULER_STATE_DIR}` (`/var/lib/dark-factory` by default) is writable by both the scheduler and
  every per-ticket container — already confirmed true by the existing `session_window.py` sentinel
  mechanism using the same mount.
- `on_failure()` is the sole failure exit path for phases that need a signature (refine, plan,
  deconflict, implement); `close`/`fix-main` do not go through it and are out of scope for
  self-interruption (they have no retry loop to interrupt).
- The Hermes article content pasted into the issue comments is treated as reference material only;
  none of its setup/install instructions are executed as part of this ticket.
- `git log --since=...` and `git status --porcelain` are safe, cheap, always-available operations
  inside `on_failure()`'s working directory (the per-run clone) — no new tooling or permissions
  required beyond what the container already has for its own commits.

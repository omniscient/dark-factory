# Fix Plan-Phase OOS Gate Deleting the Refine-Phase Spec (and Memory Entries)

**Issue:** omniscient/dark-factory#293

---

## Overview / Problem Statement

`commands/dark-factory-plan.md`'s OOS gate call (Phase 4 step 4, currently line 172 on `main`)
invokes:

```bash
OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/" plan)
```

`scripts/oos_excise.sh` detects out-of-scope files by diffing `origin/main...HEAD` (every commit
made on the branch since it forked from `main` — this three-dot form was deliberately chosen by
#266 to fix an earlier two-dot false-positive bug; see "Related work" below) and reverting/deleting
anything whose path doesn't match one of the caller's allowed prefixes. Because the plan phase
always runs on a branch where an earlier refine-phase commit already added
`docs/superpowers/specs/<topic>.md` (and often `.archon/memory/architecture.md` +
`.archon/memory/index.jsonl`), and the plan-phase allowlist names only
`docs/superpowers/plans/`, every plan run's OOS gate flags those refine-phase artifacts as
out-of-scope and excises them — deleting the spec entirely, since it doesn't exist on `main` yet.

This is confirmed still live on `main` as of 2026-07-28 (the allowlist string is unchanged), and it
is not a one-off: `git log --oneline --all --grep="excise out-of-scope files"` shows 32 matching
commits across all refs, and every one of them is a `plan` run (the `refine` allowlist already
lists both of its own prefixes correctly and has never triggered this gate). Recent examples on
`main` alone include the excise/revert pairs `4566524`/`0ed1ff4` (deleted the 2026-07-23
budget-gate-consolidation spec) and `a44eca4`/`c1b74b9` (issue #41). Every instance so far was
caught and hand-reverted by the plan agent inside the same run — this ticket exists because that
recovery depends entirely on the agent noticing before it pushes.

A second, related defect in the same call site: the excise commit message
(`chore: excise out-of-scope files from ${COMMIT_NOUN} run (#${ISSUE_NUM})`) frequently
interpolates an empty issue number, producing `(#)`. `entrypoint.sh:86` sets `ISSUE_NUM` from
`$ARGUMENTS` without `export`, and `dark-factory-plan.md`'s own Phase 1 assignment (`ISSUE_NUM=$(jq
-r '.resolved_number' "$ARTIFACTS_DIR/issue.json")`, line 53) is a plain shell variable in an
earlier, separately-executed bash block — neither survives into the later block's `Bash` tool
subprocess that runs `oos_excise.sh`. Of the 32 excise commits, several do carry a real number
(`#266`, `#254`, `#248`, `#41`) and most say `(#)`, consistent with the number only surviving when
assignment and invocation happened to land in the same shell invocation.

## Requirements

1. `dark-factory-plan.md`'s OOS gate call must not excise a spec file or memory entries that a
   prior refine-phase commit on the same branch legitimately added.
2. The fix must not weaken the gate's actual job: a plan-phase file genuinely outside
   `docs/superpowers/plans/`, `docs/superpowers/specs/`, and `.archon/memory/` must still be
   excised exactly as today.
3. `oos_excise.sh`'s excision commit message must reliably embed the real issue number instead of
   silently interpolating an empty string, without requiring every caller to remember to export
   `ISSUE_NUM` before invoking it.
4. Add regression coverage that would have caught both defects, asserted against the actual
   command file content and script behavior (not just re-testing already-correct behavior).
5. Scope stays limited to the plan-phase allowlist call and `oos_excise.sh`'s issue-number
   handling. Per CLAUDE.md ("gate changes get their own reviewed ticket") and the precedent set by
   #266 ("Scope stays exactly `scripts/oos_excise.sh` + its tests... No other gate, config, or
   command file changes"), this ticket does not change the gate's diffing semantics
   (`origin/main...HEAD`), does not touch `dark-factory-conformance.md` or
   `dark-factory-code-review.md` (confirmed below to be unaffected), and does not add a
   per-phase-base-commit tracking mechanism.

## Architecture / Approach

### Fix 1 — widen the plan-phase allowlist to match what actually lands on the branch before it runs

```bash
OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)  # TARGET-PATH
```

This mirrors `dark-factory-refine.md`'s own (already-correct) call, which passes
`"docs/superpowers/specs/ .archon/memory/"`. The plan phase's `SCOPE BOUNDARY` section (declaring
`docs/superpowers/plans/` as its only *authorized output*) is unchanged — the two added prefixes
are tolerated **inherited** refine-phase artifacts, not something the plan phase is licensed to
create or edit itself. A plan agent that wrongly edits the spec is not caught by this gate, but
that class of drift is already covered by the plan command's own Phase 3.5 plan-vs-spec
conformance check, and by the conformance gate's later Phase 3.6 review.

`.archon/memory/` is not optional: several of the 32 historical excise commits reverted
`.archon/memory/architecture.md` and `.archon/memory/index.jsonl` alongside the spec (the refine
phase's own memory-write step, Phase 1 step 7 of `dark-factory-refine.md`), for the same
three-dot-diff reason.

### Fix 2 — make `oos_excise.sh` self-sufficient for the issue number

Rather than relying on every caller to correctly `export ISSUE_NUM` before invoking the script (the
two-fenced-blocks structure of the command files makes that easy to get wrong, as demonstrated),
have the script fall back to reading the issue number directly from the artifact it already
requires:

```bash
ISSUE_NUM="${ISSUE_NUM:-$(jq -r '.resolved_number // empty' "$ARTIFACTS_DIR/issue.json" 2>/dev/null || true)}"
```

`ARTIFACTS_DIR` is already a hard-required env var for the script, and `issue.json` is the
documented authoritative issue-context artifact every phase command reads (`$ARTIFACTS_DIR/issue.json`
per `dark-factory-plan.md` Phase 1 step 2, and the equivalent in `dark-factory-refine.md`). This
fixes both call sites (refine and plan) with one change, at the one place that already owns commit
message construction, instead of patching each command file's shell-variable lifetime individually.

### Regression tests

- `tests/test_oos_excise.py`: add a test that does **not** set `env["ISSUE_NUM"]` (unlike the
  existing `base_env()` helper, which always sets it to `"670"`), instead writes a minimal
  `issue.json` (`{"resolved_number": 293}`) into the artifacts dir, and asserts the resulting
  excise commit message contains `293` — proving the fallback path works when the caller doesn't
  set the env var.
- A new content-assertion test (following the existing pattern in
  `tests/test_command_issue_context_contract.py`, which already asserts specific required
  substrings inside `commands/*.md`): assert that `commands/dark-factory-plan.md`'s
  `oos_excise.sh` invocation line contains all three prefixes
  (`docs/superpowers/plans/`, `docs/superpowers/specs/`, `.archon/memory/`). This is the only way
  to regression-test this specific defect, since `oos_excise.sh` itself behaves correctly today —
  the bug is entirely in the caller's argument string, not the script's logic.

### Why conformance and code-review are unaffected (confirmed, no change needed there)

`grep -n "oos_excise.sh" commands/*.md` shows the script is called only from
`dark-factory-refine.md` and `dark-factory-plan.md`. `dark-factory-conformance.md` uses a
completely separate, LLM-reviewer-driven scope-enforcement mechanism (Phase 3.6), which has its
own explicit documentation exemption (3.6.0: doc-file changes, including specs, are "never
out-of-scope and must never be excised or filed as backlog tickets") — structurally immune to this
bug. `dark-factory-code-review.md` has no OOS/scope-enforcement logic at all. This matches the
issue's follow-up comment's own observation that the defect is plan-phase-specific.

## Alternatives Considered

**Diff each phase against its own starting commit / merge-base, instead of `origin/main`, as a
generic fix for the whole class of "prior-phase artifact flagged as OOS" bugs.** Rejected for this
ticket: there is no existing plumbing to build on (`entrypoint.sh` and
`workflows/archon-dark-factory.yaml` do not record a per-phase base SHA anywhere today), so doing
it properly means capturing and exporting `git rev-parse HEAD` post-checkout, threading a new
argument through both existing call sites, and rewriting the script's diffing tests — a change to
a shared safety-gate's core semantics, which CLAUDE.md and this repo's own prior gate ticket (#266)
both treat as its own reviewed ticket, not a rider on an allowlist patch. The issue's follow-up
comment attributes this idea to "#272," but this repo's actual issue/epic #272
(`docs/archive/2026-06-13-dark-factory-non-root-user-design.md`) is "Container & deployment
security hardening" and has nothing to do with OOS diffing — there is no existing design precedent
for the per-phase-base-commit approach in this repo. If wanted, it should be filed as its own
follow-up gate ticket rather than assumed as prior art here.

**Special-case "same-ticket spec commits" inside `oos_excise.sh`** (the issue body's second
suggested option): rejected as more complex than widening the allowlist for no additional benefit
— it would require the script to parse issue numbers out of commit messages/paths and correlate
them, whereas the calling phase already knows exactly which prefixes its own pipeline legitimately
produces upstream of it.

**Fix the `ISSUE_NUM` empty-interpolation bug by `export`-ing it from `entrypoint.sh` instead of
adding a fallback inside `oos_excise.sh`.** Rejected: it widens the agent's shell environment
surface for a value that's already redundantly available via the required `issue.json` artifact,
and `entrypoint.sh`'s `ISSUE_NUM` is parsed out of `$ARGUMENTS` by regex — less authoritative than
the `resolved_number` field in the artifact every phase already treats as ground truth.

## Related work

- **#266** (`git show d9b0f52:docs/superpowers/specs/2026-07-13-oos-excise-merge-base-diff-design.md`)
  changed `oos_excise.sh` from a two-dot to a three-dot (`origin/main...HEAD`) diff, fixing a
  different false-positive class (files `origin/main` changed independently after the branch
  forked). That spec's own problem statement explicitly logged this exact ticket's bug as a
  companion incident ("A companion plan run on the same branch also excised the branch's own
  freshly-approved spec before the agent caught and reverted it") and deliberately deferred it,
  scoping #266 to the script's diffing logic only. #293 is that deferred remainder.

## Open Questions (non-blocking)

- Should the `.archon/memory/index.jsonl` and `architecture.md` protection extend to any other
  future refine-phase artifact directories, or is `.archon/memory/` (the whole directory) already
  sufficiently broad? Current answer: yes, the whole-directory prefix already covers this; no
  further action needed.

## Assumptions

- `entrypoint.sh`'s generation of `.archon/commands/` via `cp -r /opt/dark-factory/commands
  "$CLONE_DIR/.archon/commands"` (line 557) means only the tracked `commands/dark-factory-plan.md`
  needs editing; the untracked `.archon/commands/dark-factory-plan.md` copy in any given clone is
  runtime scaffolding regenerated from it, not a second source of truth to edit or commit.

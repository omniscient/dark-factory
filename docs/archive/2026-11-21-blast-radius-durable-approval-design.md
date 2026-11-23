# Deterministic, Durable Blast-Radius Approval

**Issue:** #551
**Revised:** 2026-11-21 (operator amendment after spec gate review; owner decisions 1-5)
**Related:** #374 (stateless `HUMAN_REQUIRED` cannot be approved), #407 (no deterministic
consumer of `blast.md`), #373 (missing conformance verdict), #411 (conformance agent
refusals), #341 (session-window pauses are not counted as failures), #180 and #484 (the
two runs that triggered this ticket; both needed hand-opened PRs — #549, #550)
**Not a `direct-to-pr` candidate** (per the issue: this touches gate sequencing)
**Merge:** this ticket's own PR edits gate files and will trip the blast-radius gate under
the *old* flow. It is merged by the owner by hand after operator review; it does not wait
for its own mechanism.

All `file:line` references are to `origin/main` at `e707c1f`.

---

## Overview / Problem Statement

The blast-radius gate (`scripts/gate_blast_radius.py`, invoked from
`commands/dark-factory-validate.md` Phase 0, lines 44-119) is a deterministic, path-based
classifier. It recomputes `STATUS` from scratch on every `validate` run and has no memory
of a prior human review. When it flags a triggering file (hotspot / migration-seed /
skill-security / `.factory/adapter.yaml` boundary-escalation) or an over-budget diff size,
it prints `STATUS: HUMAN_REQUIRED`; the validate phase agent saves that as
`$ARTIFACTS_DIR/blast.md`, posts a "Blast-Radius Gate — BLOCKED" comment, adds
`needs-discussion`, sets the board to `Blocked`, and `exit 1`s — even when a human already
reviewed and signed off on that exact diff in a previous run.

Two independent defects compound into an unrecoverable loop (observed twice on #180,
2026-11-20, and again on #484):

1. **Statelessness (#374).** There is no durable record of a human sign-off. Lifting
   `needs-discussion` lets the scheduler dispatch `Continue`, which re-runs `validate`,
   which re-flags the same already-reviewed files and re-adds the label. The block can
   only be worked around by hand-opening a PR outside the factory, which is what happened
   for both #180 and #484.
2. **No deterministic consumer of `blast.md` (#407).** `validate`'s `exit 1` happens
   inside a `command:` node's own Bash tool call and does not reliably surface as node
   failure to the Archon DAG executor (#212/#271; `commands/dark-factory-conformance.md:708-712`,
   `scripts/verdict_gate_check.sh:2-6`). The DAG keeps going to `conformance`. On #180 the
   conformance agent noticed the stale block and refused to run Gate 2. That is the
   anti-pattern the issue names: "the conformance node should not be the place where an
   LLM decides whether a prior gate was genuinely resolved."

Both are corroborated by `docs/run-lifecycle.md` items 3 and 4 (lines 1149 and 1160). Item
4 adds that the blocked comment tells the operator to re-run `"Validate issue #N"`, a
command that does not exist: `entrypoint.sh:103`'s intent pattern has no `validate`, so it
falls through to `fix` (`INTENT=${INTENT:-fix}`, `entrypoint.sh:104`).

The spec gate review added three facts that shape this revision:

- The state volume is **not** a trust boundary. `run-compose.yml:58-59` mounts
  `scheduler_state` read-write into the run container, `Dockerfile:169-172` gives
  `/var/lib/dark-factory` to the `factory` user the agent runs as, and every phase node
  has `denied_tools: []` (e.g. `workflows/archon-dark-factory.yaml:1101`). A phase agent
  can write any file there. A read-only mount would not help: the run container reaches a
  Docker socket proxy that allows `CONTAINERS`, `POST` and `VOLUMES`
  (`deploy/docker-compose.yml:45-53`).
- `git patch-id --stable` is not a sound binding. In a scratch repo, a line added inside
  an `if` and the same line dedented out of it have the same patch-id (whitespace is
  ignored); the same hunk applied in two different jobs of one YAML file has the same
  patch-id (line numbers are ignored); and the id is unchanged after `main` changes the
  same file underneath.
- `blast.md` is written by the validate phase agent, and validate, conformance and
  `revise-advisory` all commit after Phase 0 ran. A node that only reads `blast.md` is
  not deterministic and gates a diff that may no longer be the one being pushed.

## Requirements

Governed by the issue's "Scope decision (owner, 2026-11-20)", the owner's decisions of
2026-11-21 on the spec gate review, and CLAUDE.md's hard limits.

1. **Durable approval record, outside the branch and outside any comment.** Stored at
   `/var/lib/dark-factory/blast-approvals/<issue>.json` on the `scheduler_state` volume
   (`run-compose.yml:58-59`, `112-114`), which run containers read as a local path and
   the operator reaches through the scheduler container. Exact layout: "Interface
   contract".
2. **The record is authoritative only with a valid detached gpg signature.** The
   signature is made by the operator tool with a private key that never enters any
   container. The gate verifies it against a public key read from
   `git show <trusted base>:.factory/blast-approvers.asc` — never from the working tree,
   the branch under review, or the state volume. A missing, invalid, expired or revoked
   signature is `APPROVAL: error (signature)` and the status stays `HUMAN_REQUIRED`.
3. **Exact binding to the reviewed content.** Per triggered path the record binds the
   `<mode> <blob-oid>` of the file at the merge-base and at the head. An approval never
   carries over to different content of a triggered file, and is not invalidated by
   later commits that leave the triggered files and their base untouched. When the size
   trigger is active the approval binds the whole diff (both trees).
4. **Human-run, host-side tool is the only approval channel.** Never a comment, never a
   label alone, never a phase agent. Requirement 2 is what makes this true; without the
   signature it would be false.
5. **Deterministic in-run enforcement.** A bash DAG node re-runs the gate itself on the
   current HEAD, and repeats it immediately before the push. No phase agent's file,
   exit code or judgment is on the signal path. `conformance` stops adjudicating.
6. **Agent-free enforcement at merge.** The factory's close path re-runs the gate on the
   PR head and verifies the signed approval before merging. Without a valid matching
   approval for a `HUMAN_REQUIRED` diff it refuses, regardless of labels.
7. **A human-review hold is not a failure.** A `HUMAN_REQUIRED` stop does not write a
   breaker error signature and does not consume the Blocked-retry budget. It also never
   resets or decrements either.
8. **Strengthen only.** No existing gate, breaker, budget or the `needs-discussion` halt
   is weakened. The single new pass path (a signed, content-bound approval) exists for a
   gate that today cannot be cleared at all. The existing `needs-discussion` refusal in
   `close-preview` (`workflows/archon-dark-factory.yaml:236-245`) stays.
9. **Out of scope: which paths or sizes trigger the gate.** One owner-decided exception:
   the public-key path joins the hard-trigger floor, so changing the approver set always
   needs a human.

## Threat model

| Actor | In-run gates (validate Phase 0, `blast-gate`, `push-and-pr` re-check) | Close-time check |
|---|---|---|
| Fallible phase agent (skips a step, ends its turn early, "helpfully" rewrites `blast.md`, misreads a comment as approval) | **Stopped.** The nodes are bash, recompute the verdict, and do not read anything the agent wrote | Stopped |
| Hostile or prompt-injected phase agent | **Not stopped.** Inside the run container the agent runs as `factory`, which owns `/opt/dark-factory` (`Dockerfile:169-170`) and the clone's `dark-factory/scripts/` copy, so it can rewrite the baked gate, the node's script, `git` config, or put a `git`/`gpg` shim on `PATH`. It also holds a token whose side-effect level 5 profile leaves `git push` and `gh pr create` open (`scripts/factory_core/side_effect.py:148-157`) | **Stopped for factory merges.** The Close run has no tool-bearing agent before the check, runs in a fresh container and clone, and trusts only `origin` and the signature |

What a hostile agent still cannot do: produce a record the close-time check accepts,
because it has no private key and the public key is read from `origin/main`, which it
cannot change without a merged PR (and that path is on the hard-trigger floor).

Outside this design, and stated so nobody assumes otherwise:

- **An operator hand-merge (`gh pr merge`) is not checked.** It remains the operator's
  responsibility.
- The dependency-health and fix-main loops merge their own PRs
  (`scripts/factory_core/deps_fixer.py:395`, `scripts/factory_core/main_red_fixer.py:247`)
  and never ran the blast-radius gate. Unchanged.
- The inline deconflict flow pushes at `entrypoint.sh:986` without the gate (the
  workflow's `resolve` path is unreachable, `docs/run-lifecycle.md` item 1). The push
  goes to an existing PR branch; the close-time check covers the result.
- Branch protection on `main` is the owner's control, not this spec's.

## Delivery split and authorship

`.claude/skills/` is in `.factory/adapter.yaml:22` `hard_exclude_paths` and on the
blast-radius floor (`^\.claude/`, `scripts/factory_core/adapter_defaults.py:125`). The
work is therefore split, and the two halves meet only at the interface contract below.

**(a) Factory scope — one implement run, one PR, merged by the owner by hand.**
`scripts/gate_blast_radius.py`; new `scripts/blast_gate_check.sh`; the `blast-gate` node
and the `push-and-pr` / `close-preview` changes in `workflows/archon-dark-factory.yaml`;
`commands/dark-factory-validate.md`; `commands/dark-factory-conformance.md`;
`entrypoint.sh` hold handling; the codehost merge head pin; the floor entry in
`scripts/factory_core/adapter_defaults.py`; tests; docs outside `.claude/`.
The factory run **must not create or edit** `approve-blast.sh`, `PLAYBOOK.md`,
`GOTCHAS.md`, anything else under `.claude/skills/operator/**`, or
`.factory/blast-approvers.asc`.

**(b) Operator scope — delivered by the operator session in a separate, hand-reviewed
PR.** `.claude/skills/operator/scripts/approve-blast.sh`; the key-generation runbook;
the `PLAYBOOK.md` / `GOTCHAS.md` updates; committing the public key
`.factory/blast-approvers.asc`.

Either PR may merge first. Until both are on `main` and the image is republished, every
`HUMAN_REQUIRED` diff stays held, which is the safe direction.

## Interface contract

Both sides are built and tested against this section alone. The factory side's tests use
a throwaway gpg key generated inside the test; they never need the operator's key.

### Paths and ownership

| Item | Value |
|---|---|
| State directory (in containers) | `/var/lib/dark-factory/blast-approvals/` (`${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}`, assigned at `entrypoint.sh:36` and `scheduler.sh:10`) |
| Host-side variable | `$DF_STATE_DIR` (`.claude/skills/operator/scripts/lib.sh:32`), reached with `docker exec -u factory "$DF_SCHEDULER"` |
| Record | `<issue>.json` |
| Signature | `<issue>.json.sig` — detached OpenPGP signature over the exact bytes of `<issue>.json`, binary or ASCII-armored |
| Review request (advisory, unsigned) | `<issue>.request.json`, written by the factory |
| Owner / mode | `factory:factory` (uid 1000, `Dockerfile:162`), directory `0755`, files `0644`. Never written as root |
| Public keys | `.factory/blast-approvers.asc` in the target repo: ASCII-armored OpenPGP public key(s), one or more |
| Branch naming | `feat/issue-<N>-<slug>` (`workflows/archon-dark-factory.yaml:309`, `scheduler.sh:540`). Exactly one such branch on `origin` per issue, otherwise refuse |

### Record schema (`schema: 1`)

```json
{
  "schema": 1,
  "issue": 551,
  "repo": "omniscient/dark-factory",
  "branch": "feat/issue-551-blast-radius-block-cannot-be-cleared-thr",
  "head_sha": "<40 hex: branch head the operator reviewed>",
  "base_sha": "<40 hex: merge-base of that head and origin/main at approval>",
  "files": {
    "scripts/gate_blast_radius.py": {
      "base": "100644 <blob oid>",
      "head": "100644 <blob oid>"
    },
    "old/removed_file.py": { "base": "100644 <blob oid>", "head": "absent" }
  },
  "size": null,
  "trigger": "hotspot",
  "approved_at": "2026-11-21T14:03:00Z",
  "approved_by": "<operator name>",
  "approvals_count": 1,
  "previous_head_sha": null
}
```

- `files[path].base` / `.head`: the first and third fields of
  `git ls-tree <commit> -- <path>` joined by one space (`<mode> <oid>`), or the literal
  `absent` when the path does not exist at that commit. `base` is taken at `base_sha`,
  `head` at `head_sha`.
- `size`: `null`, or — required when the size trigger was active at approval —
  `{"lines_changed": <int>, "head_tree": "<oid of head_sha^{tree}>", "base_tree": "<oid of base_sha^{tree}>"}`.
- **Matched fields:** `schema`, `issue`, `repo`, `branch`, `files`, `size`.
  **Signed but not matched (audit):** `head_sha`, `base_sha`, `trigger`, `approved_at`,
  `approved_by`, `approvals_count`, `previous_head_sha`.
- One record per issue, always overwritten whole. History lives in the audit comment.

### Canonical serialization and what is signed

The file content is exactly
`json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` encoded as
ASCII, followed by one `\n`. The signature covers those file bytes. The verifier reads
the file once, verifies the signature over those bytes, parses those same bytes, and
rejects the record (`APPROVAL: error (record)`) if re-serializing the parsed value does
not reproduce them byte for byte. The whole record is therefore covered: issue, repo,
branch, the base/head blob map, the trees when size-triggered, and `head_sha`.

### Signature verification (exact invocation)

```bash
GNUPGHOME=$(mktemp -d) && chmod 700 "$GNUPGHOME"
git show "$TRUSTED_REF:.factory/blast-approvers.asc" > "$GNUPGHOME/approvers.asc"
gpg --homedir "$GNUPGHOME" --batch --quiet --import "$GNUPGHOME/approvers.asc"
gpg --homedir "$GNUPGHOME" --batch --status-fd 1 --verify "$COPY.sig" "$COPY"
rm -rf "$GNUPGHOME"
```

`$COPY` and `$COPY.sig` are private copies of the record and signature taken once, so
the bytes verified are the bytes parsed. The keyring holds that one blob and nothing
else; no ambient keyring or `GNUPGHOME` is consulted. **Valid** means all of: every
command exits 0; the status output has exactly one `[GNUPG:] GOODSIG` line and a
`[GNUPG:] VALIDSIG` line; and it has none of `EXPSIG`, `EXPKEYSIG`, `REVKEYSIG`,
`BADSIG`, `ERRSIG`. (Checked against GnuPG 2.4: an expired key still exits 0 with
`VALIDSIG`, but emits `EXPKEYSIG` instead of `GOODSIG`, so the exit code alone is not
enough.) `gnupg` is installed in the image (`Dockerfile:15`). Anything else — no key file
at the trusted ref, import failure, missing `.sig`, gpg missing — is
`APPROVAL: error (signature)`.

### Trusted base

`TRUSTED_REF` is `refs/blast-gate/trusted-main`, created by the checker itself
immediately before use, every time:

```bash
git fetch --no-tags --quiet origin "+refs/heads/main:refs/blast-gate/trusted-main"
```

A failed fetch is a gate error (fail closed). The checker never uses the clone's local
`main` or `origin/main` refs for the key, because an earlier node could have moved them.

Honest limits: at **close time** this is trustworthy — fresh container, fresh clone, no
agent has run. **In-run** it is only as trustworthy as the container: a hostile agent
with local git can repoint `remote.origin.url`, add a `url.*.insteadOf` rewrite, shadow
`git` or `gpg` on `PATH`, or edit the checker. That is the threat-model row above, and
the reason the close-time check exists.

### Review request (factory → operator, advisory)

On every hold the factory writes `<issue>.request.json`:
`{"schema":1,"issue":N,"repo":"…","branch":"…","head_sha":"…","mode":"run"|"close","triggered_paths":["…"],"written_at":"…"}`.

It is unsigned and carries no authority. It exists because the in-run gate reads a
hotspots file regenerated after implement and never committed
(`workflows/archon-dark-factory.yaml:761-766`), which the operator's host cannot
reproduce. The operator tool approves the **union** of the paths it computes itself and
the request's `triggered_paths` that are in the branch's changed-file list. A forged
request can therefore only add files to the human's review; it can never remove one or
approve anything.

### Match rule (implemented once, in `gate_blast_radius.py`)

Evaluated only when the existing logic yields `HUMAN_REQUIRED`. First failing step wins:

1. No `--issue`, or no record file → `APPROVAL: none`.
2. Signature not valid → `APPROVAL: error (signature)`.
3. Not canonical, not schema 1, or malformed → `APPROVAL: error (record)`.
4. `issue`, `repo` or `branch` differ from the current ones → `APPROVAL: stale (identity)`.
5. For every currently triggered path (including `.factory/adapter.yaml` when
   boundary-escalation fired): no entry → `APPROVAL: stale (new trigger: <path>)`;
   `base` ≠ `git ls-tree <merge-base> -- <path>` or `head` ≠ `git ls-tree <head> -- <path>`
   → `APPROVAL: stale (changed since approval: <path>)`.
6. Size trigger active: `size` is null, or `lines_changed`, `head_tree` or `base_tree`
   differ → `APPROVAL: stale (size)`.
7. Otherwise `STATUS: PASS` with
   `APPROVAL: matched (<approved_by> at <approved_at>, head <head_sha>)`. The `TRIGGER:`
   and `TRIGGERED_FILES:` lines are still printed.

Every non-matching outcome leaves `STATUS: HUMAN_REQUIRED`. No exception may escape the
approval code: any unexpected error is `APPROVAL: error (<reason>)`. The current trigger
set must be a subset of the approved set; a record may list more paths than are
currently triggered.

## Architecture — factory scope

### A. `scripts/gate_blast_radius.py` — approval matching

New optional args: `--issue`, `--approvals-dir` (default
`${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals`), `--repo`, `--branch`,
`--trusted-ref`, `--head-ref` (default `HEAD`; today `HEAD` is hard-coded at line 256).
Without `--issue` the output is byte-identical to today, so every existing caller and
test is unaffected. The trigger computation (lines 327-360) is not touched. The match
rule above is applied after it and can only turn `HUMAN_REQUIRED` into `PASS`; it never
runs for `PASS`/`SKIPPED`. The script still exits 0 always and never prints `ERROR`.

### B. `scripts/blast_gate_check.sh` — the one deterministic checker

`blast_gate_check.sh --issue <N> --mode run|close [--out <blast.md>]`

Used by all three enforcement points so they cannot drift. It:

1. Fetches the trusted base (above).
2. Resolves the head: `HEAD` in `run` mode; in `close` mode the single
   `feat/issue-<N>-*` branch on `origin`, fetched to `refs/blast-gate/head` and checked
   out detached in a temporary `git worktree` (the gate reads the adapter from its
   `--clone-dir`, and `post-merge-update-codeindex` still needs the clone itself).
3. Computes merge-base against `refs/blast-gate/trusted-main`, the changed-file list and
   the line count exactly as Phase 0 does today
   (`commands/dark-factory-validate.md:61-64`, three-dot semantics).
4. Hotspots input: `run` mode uses the working-tree `docs/codeindex-hotspots.md` as today
   (no change to what triggers in-run); `close` mode uses
   `git show refs/blast-gate/trusted-main:docs/codeindex-hotspots.md`.
5. Runs the **baked** `/opt/dark-factory/scripts/gate_blast_radius.py` (clone-relative
   fallback only when the baked path is absent, as `validate.md:75-76` does) with
   `--issue --repo "$FACTORY_REPO_SLUG" --branch --trusted-ref --head-ref`, writing
   `--out`.
6. Exit `0` only when the output's `STATUS:` is exactly `PASS` or `SKIPPED`. Exit `1` for
   `HUMAN_REQUIRED`. Exit `2` for anything else (empty output, crash, failed fetch,
   non-numeric issue, ambiguous branch). `ERROR` is **not** a pass value here: the gate
   never emits it, so accepting it would only admit a hand-written file. Callers treat
   `1` and `2` both as "stop".

`scripts/verdict_gate_check.sh` is **not** used for this gate and is not modified: it
proceeds on `ERROR` (line 42) and trusts a file it did not produce.

### C. New DAG node `blast-gate`

```yaml
- id: blast-gate
  bash: |
    # deterministic: recomputes the verdict; never reads an agent-written blast.md
    ...
  depends_on: [validate]
  when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue' || $parse-intent.output.intent == 'resolve'"
  timeout: 60000
```

The node runs `blast_gate_check.sh --mode run --out "$ARTIFACTS_DIR/blast.md"`
(overwriting whatever validate left). On exit 0 it ends. Otherwise, in this order, each
step idempotent and none depending on a phase agent:

1. Push the branch to `origin` **without opening a PR**, using the verified-push pattern
   of `refine-push` (`workflows/archon-dark-factory.yaml:463-581`: 30 s timeout,
   remote-SHA check). Needed because nothing pushes a `new`/`continue` branch before
   `push-and-pr`, and the container's commits die with it.
2. Write `<issue>.request.json`.
3. Add `needs-discussion`, set the board to `Blocked`.
4. Post or update one marker comment (`<!-- df-blast-hold -->`, via
   `scripts/post_marker_comment.sh`): branch, head SHA, trigger, triggered files, the
   `APPROVAL:` line (so a stale approval reads as a delta re-review), and the instruction
   to run `approve-blast.sh <N> --sha <head>`. It says explicitly that removing the label
   by hand does nothing useful. The old "re-run `Validate issue #N`" text is gone.
5. Write `$ARTIFACTS_DIR/blast-hold.json` (`{"issue":N,"head_sha":"…","exit":1|2}`).
6. `exit 1`.

If the push in step 1 fails, steps 2-6 still run and the comment says the branch is not
on `origin`.

Re-pointing (line numbers verified): `budget-conformance` `depends_on: [validate]`
(1137) → `[blast-gate]`; `conformance` `depends_on: [enforce-budget-conformance, validate]`
(1152) → `[enforce-budget-conformance, blast-gate]`; `push-resolve`
`depends_on: [validate]` (1291) → `[blast-gate]`. `blast-gate` has one upstream and no
`trigger_rule`, so `scripts/check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` (line 28)
and its count tripwire are unchanged. `status-in-review` (1533-1536) and `report`
(1710-1713) are OR-joins; after a `blast-gate` failure none of their upstreams succeeds.
Whether Archon then skips them is the same open semantics `conformance-gate` already
relies on (`docs/run-lifecycle.md` item 5); the hold comment in step 4 does not depend on
`report` running.

### D. `commands/dark-factory-validate.md` Phase 0

Phase 0 calls `blast_gate_check.sh --mode run --out "$ARTIFACTS_DIR/blast.md"` instead
of invoking the gate inline. On a non-zero exit it stops validation and ends with
`exit 1`; it no longer posts the comment, labels, or moves the board (the node does, so
the agent is off the signal path). On exit 0 it continues as today. The kill-switch
paragraph (lines 52-57) is kept.

### E. `push-and-pr` re-check

Validate's fix commits, conformance's reconcile loop and the spec/plan archive commit
(`workflows/archon-dark-factory.yaml:1330-1342`) all land after `blast-gate`. `push-and-pr`
therefore runs `blast_gate_check.sh --mode run` again after the archive commit and
immediately before `git push` (line 1347). A non-zero exit takes the same six hold steps
as node C and exits 1 before any push to a PR or `gh pr create`. In `BENCH_MODE=stub`
(lines 1313-1317) nothing is pushed and the re-check is skipped with it.

### F. Close-time re-check (the hostile-agent enforcement point)

**Where:** node `close-preview` (`workflows/archon-dark-factory.yaml:203-268`), between
the existing `needs-discussion` refusal (236-245, kept unchanged) and `mark-ready`/merge
(249-251).

**Agent-free, verified:** a Close run executes `parse-intent` (a prompt with
`allowed_tools: []`, lines 21-41), `fetch-issue` (bash, 43-132), `close-announce` (bash,
192-201) and `close-preview` (bash). `entrypoint.sh` runs no agent session for `close`
before the workflow (the smoke gate at line 902 excludes it; post-mortem runs only after
a failure). Close is dispatched by `scheduler.sh:336` (end-gate, PR `APPROVED`) and
`scheduler.sh:1123` (comment classified `MERGE`); both only start the run, and the check
is inside it. No change is needed to make the path deterministic.

**What it does:**

1. `blast_gate_check.sh --issue "$ISSUE" --mode close --out "$ARTIFACTS_DIR/blast.md"`.
2. Exit 0 → merge, pinned to the head that was checked: `codehost merge` gains an
   optional `--match-head <sha>` passed through to `gh pr merge --match-head-commit`
   (`scripts/factory_core/providers/codehost/github.py:90-99`,
   `scripts/factory_core/providers/cli.py:197-199`; the GitLab provider at
   `codehost/gitlab.py:92` passes the equivalent `sha`). Without the pin a push between
   check and merge would merge unchecked content.
3. Non-zero → no merge. Write `<issue>.request.json` (`mode: close`), add
   `needs-discussion`, post/update the `<!-- df-blast-hold -->` comment, `exit 1`. This
   holds whatever the labels say.
4. After a successful merge: delete `<issue>.json`, `.json.sig` and `.request.json`
   (best effort), so a record cannot outlive its ticket.

A PR that never triggered the gate is `PASS`/`SKIPPED` and merges as today. A checker
failure refuses the merge (fail closed) and says so in the comment.

**Behaviour change to announce:** PRs already In Review that touch triggered paths will
be refused by factory Close until `approve-blast.sh` has been run for them.

### G. Hold is not a failure (retries and breaker)

How session-window pauses are exempted today (#341):

- `entrypoint.sh:1030-1034`: when the workflow exits non-zero and
  `_handle_session_window_pause` matches, the container `exit 0`s, skipping
  `run_post_mortem` and `_write_error_signature` (`entrypoint.sh:1071-1072`).
- `_handle_session_window_pause` (`entrypoint.sh:278-348`) records a `paused` run-record
  row and writes the signature `environmental:session_window_pause` (lines 332-337).
- `scheduler.sh:418-426` `rollback_paused_retry` then **decrements** the retry counter,
  called from the Blocked loop at `scheduler.sh:1210`.

The analogous mechanism here, minus the decrement (owner decision: never reset or
decrement):

- In the same place (`entrypoint.sh`, directly after the session-window check at
  1030-1034): if `$ARTIFACTS_DIR/blast-hold.json` exists, record a run-record row
  (stage `blast_hold`, verdict `human_required`), post the cost report, and `exit 0`.
  No post-mortem, no `_write_error_signature`, no `on_failure`. `$ARTIFACTS_DIR` is per
  run (`entrypoint.sh:114`), so the marker cannot leak from an earlier run.
- No signature is written at all, so `check_failure_signature` (`scheduler.sh:404-408`)
  has nothing new to compare and cannot report `stuck`.
- `scheduler.sh` is **not changed**. While the hold lasts, `has_skip_label`
  (`scheduler.sh:1194`) skips the issue before any counting (1203-1227). After approval
  the next Blocked-loop dispatch counts as one ordinary attempt.
- Consequence, stated plainly: if the held run was itself a counted retry, that count
  stays. The hold adds nothing and removes nothing.

The same applies to a hold raised by the `push-and-pr` re-check. A Close refusal is an
ordinary Close failure, as the existing `needs-discussion` refusal is today.

### H. `commands/dark-factory-conformance.md`

Add near Phase 1 LOAD: "Conformance does not adjudicate the blast-radius gate. Once
dispatched it runs its review; it does not consult `blast.md`, a blast-radius comment,
or a blast-radius `needs-discussion`/`Blocked` state to decide whether to run.
Enforcement is the `blast-gate` node, the `push-and-pr` re-check and the close-time
check." Conformance's own Phase 5 writes of `needs-discussion` and `Blocked` (lines
680-688) are unchanged. This text ships in the same change as node C, never before it.
No change to `dark-factory-code-review.md` or `dark-factory-docs-conformance.md`.

### I. Hard-trigger floor entry

Add `r"^\.factory/blast-approvers\.asc$"` to `FACTORY_OWNED_CRITICAL_DIFF_FLOOR`
(`scripts/factory_core/adapter_defaults.py:122-133`). It is not in `_VISIBILITY_ONLY`
(line 143), so it flows into `FACTORY_OWNED_MIGRATION_SEED_FLOOR` (144-146) and any PR
that adds or changes the approver keys is `HUMAN_REQUIRED`. This is the only change to
what triggers the gate.

## Architecture — operator scope (separate hand-reviewed PR)

Specified here only as far as the contract needs; the operator PR owns the details.

- **`approve-blast.sh N --sha <head>`** (sources `lib.sh`; human-run on the host).
  Preconditions: `needs-discussion` present; board `Blocked` or `In Review`; no live run
  container (`issue_run_container`, `lib.sh:127-132`); `git -C "$DF_REPO_DIR" remote
  get-url origin` matches `$DF_REPO` (the default `DF_REPO_DIR` is the dark-factory
  checkout, `lib.sh:41-44`, wrong for other targets); exactly one `feat/issue-N-*` branch
  on `origin` whose fetched head equals `--sha`, via explicit remote-tracking refs.
  It recomputes the trigger set in a detached worktree at `--sha` (hotspots and config
  from `origin/main`), unions it with the request file as defined above, prints each
  path's diff for review, builds the record, signs it on the host, writes `.sig` then
  `.json` through `docker exec -u factory "$DF_SCHEDULER"` (temp file + `mv`), reads both
  back, runs the verification invocation above against `origin/main`'s key, posts an
  audit comment (no authority), and removes `needs-discussion` **last**.
- **Key runbook:** generate a signing-only key on the operator host with an expiry;
  commit only the public key to `.factory/blast-approvers.asc`; rotation and revocation
  are PRs to that file. The private key is never copied into a container, the state
  volume, or the repo.
- **Docs:** `.claude/skills/operator/PLAYBOOK.md` ("Blast-radius block", lines 88-95, and
  the PR-drafts note at 63-64) and `GOTCHAS.md` (58-60, 193-196) describe the old flow
  and must be rewritten: a hold now leaves a pushed branch and **no** draft PR; review
  the branch, run `approve-blast.sh`, let `Continue` produce the PR.
- **Other targets (MarketHawk):** each target commits its own
  `.factory/blast-approvers.asc`; `DF_REPO` selects scheduler and volume
  (`lib.sh:17-30`). `repo` in the record stops a record being replayed across instances.

## Tests and docs (factory scope)

CI runs `python -m pytest tests/` plus the `.sh` tests listed one per line in
`.github/workflows/ci.yml`, plus `scripts/check_workflow_dag.py` and
`scripts/check_workflow_when.py` (ci.yml lines 58-59). A new `.sh` test that is not
added to `ci.yml` does not run.

- `tests/test_blast_radius.py` — new approval section using a throwaway gpg key created
  in `tmp_path` and a scratch git repo. Cases: matched; no record; no `--issue` (output
  identical to today); missing `.sig`; signature by a key not in the trusted blob;
  tampered record; expired key (`EXPKEYSIG`); key file present only in the working tree
  and not at the trusted ref; non-canonical bytes; wrong issue/repo/branch; new trigger;
  head blob changed; base blob changed; mode-only change; deleted file (`absent`);
  size trigger with and without matching trees. Two regression cases from the review:
  a re-indented line and a hunk relocated to another block must both be `stale`. The
  tests fail, not skip, when `gpg` is absent.
- `tests/test_blast_gate_check.sh` (new, **add to `ci.yml`**) — exit 0/1/2 mapping,
  `ERROR` and empty output are exit 2, fetch failure is exit 2, `run` vs `close` head
  resolution, ambiguous branch refused.
- `tests/test_blast_gate_dag.py` (new, pytest) — `blast-gate` exists, is a `bash` node,
  depends on `validate`, has no `trigger_rule`; `budget-conformance`, `conformance` and
  `push-resolve` depend on it; `push-and-pr` calls the checker before its `git push`;
  `close-preview` calls it before `codehost merge` and passes the head pin; the
  `needs-discussion` refusal is still present.
- `tests/test_entrypoint_blast_hold.sh` (new, **add to `ci.yml`**, modelled on
  `tests/test_entrypoint_session_window.sh`) — with `blast-hold.json` present the
  container exits 0, writes no error signature, and leaves retry counters untouched;
  without it the failure path is unchanged.
- Provider tests (`tests/test_provider_cli.py`, `tests/test_provider_codehost_*.py`) —
  the head pin is passed through and omitted when not given.
- `tests/test_adapter.py` — the new floor entry.
- Docs outside `.claude/`: `docs/run-lifecycle.md` (the D1 table at 705-708, open items
  3 and 4), `docs/features.md:102`, `docs/factory-target-boundary.md:273-276`,
  `docs/configuration.md` (state paths, key path).

## Alternatives Considered

1. **Record on the branch.** Rejected: it would be part of the diff it approves, merge
   into `main`, and be flagged out of scope by conformance.
2. **Approval marker in an issue comment.** Rejected by the scope decision and CLAUDE.md.
   A comment may mirror the approval for audit; it is never the authority.
3. **Unsigned record on the state volume** (first draft). Rejected: any phase agent can
   write it.
4. **Read-only mount or a separate volume.** Rejected: the socket proxy lets the run
   container start containers with volumes attached.
5. **HMAC.** Rejected: the verifier would need the secret inside the run container.
6. **`git patch-id --stable` per file** (first draft). Rejected: collides on
   whitespace-only and relocated-hunk changes and ignores a changed base.
7. **Hash of the whole PR diff for file triggers.** Rejected: any unrelated later commit
   would invalidate a still-valid approval. Used only for the size trigger, where the
   whole diff is what was approved.
8. **Reuse `verdict_gate_check.sh` on `blast.md`** (the literal #407 ask). Rejected:
   reads an agent-written file and passes `ERROR`.
9. **Leave the DAG unchanged and rely on the close-time label check.** Rejected: a
   blocked diff would still reach `push-and-pr`, `code-review` and `status-in-review` in
   the same run.
10. **Extend `approve.sh` with a `blast` kind.** Rejected: its flow is a board move plus
    an artifact-existence check; nothing transfers.
11. **Union of historical approvals.** Rejected: the tool re-approves the full current
    set each time; a single record cannot keep a stale entry alive.

## Known limits and follow-ups (not fixed here)

- A size-trigger approval binds both trees, so any later commit (including the archive
  commit) makes it stale. With `size_budget_blocks: false` (`config/config.yaml:70`) this
  does not arise by default; where it is enabled, expect a final re-approval at the PR
  head before Close.
- A `Continue` after approval re-runs `implement`. If it edits a triggered file the
  approval is stale and the run holds again with `stale (changed since approval: …)`.
  That is correct, not a regression of the loop: an unchanged file now passes.
- The changed-file list uses rename detection, so renaming a hotspot away removes it
  from the trigger set. Pre-existing, and a "which paths trigger" question: own ticket.
- In-run and close-time hotspot inputs differ (regenerated vs `main`'s). The request
  file and the subset rule make approvals cover both; a file that becomes hot on `main`
  later is refused at Close and needs re-approval.

## Assumptions (flagged)

- `main` is the default branch of every target (already assumed at
  `workflows/archon-dark-factory.yaml:278-295` and `commands/dark-factory-validate.md:61`).
- `gh pr merge --match-head-commit` is available in the image's `gh`. If not, the
  close-time check re-reads the remote head after the merge call is prepared and refuses
  on a mismatch; the plan phase confirms which.
- Archon treats a failed bash node's dependents as it does for `conformance-gate` today.
- The run-record writer accepts the new stage/verdict strings; if it validates an
  enumeration, the plan adds them.
- The factory change takes effect only after the image is republished (human-only
  pipeline); commands, workflow and scripts are baked together (`Dockerfile:140-152`), so
  a self-target run never mixes old and new. A target that tracks its own
  `.archon/commands` or `.archon/workflows` keeps the old flow until it updates them;
  without `--issue` the gate never looks for an approval, which is the safe direction.

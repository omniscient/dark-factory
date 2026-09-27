# Gotchas

Each entry cost at least one run, one PR, or one wrong claim to Frank. Dates are when it was
learned; verify code citations against `origin/main` before relying on them.

## Ordering and races

- **`direct-to-pr` is itself an opt-in.** The refine loop dispatches a Backlog item carrying
  `ready-for-agent` OR `direct-to-pr`. Filing a ticket with `direct-to-pr` dispatches it on the
  next poll (MarketHawk #848, 2026-09-14: two refine attempts and a breaker trip before anyone
  meant to start it). Add `direct-to-pr` at opt-in time, never at filing time.
- **Board move before label removal.** The REFINED/READY loops dispatch on status alone; the
  gate label is the only skip. Label-first opens a poll window that fires a redundant run
  (#305, 2026-07-20). `approve.sh` enforces this.
- **A live run's board write overrides yours.** Done/Ready set while the run container is alive
  gets silently reverted (#418/#400, 2026-09-10). `board.sh set` refuses unless `--force`.
- **Never chain approval steps after `;` behind an amend/push.** A failed amend left the board
  moved and the label off with the amendment unpushed (2026-09-08). Gate on
  `git ls-remote | grep -q $SHA`; `approve.sh --sha` does this.
- **Two PRs from one branch** squash against different bases; the second degrades renames into
  add-without-delete (PR #421).
- **Never dispatch Close while the Fix container still runs**; its revise-advisory tail pushes
  post-merge (PR #226).

## Visibility

- **Board, not issue list.** Open + `ready-for-agent` + not a board item = invisible forever
  (#294 sat 10 days; ten tickets after #403 were off-board for 15 h). `skip=nothing_to_do` is
  not evidence of an empty queue; `status.sh` diffs the two.
- **`gh issue list` defaults to 30 and truncates silently.** Always `--limit 200`. Bit three
  monitors and one spec.
- **Exit 0 with an empty array** happens on transient hiccups: a single empty result is not
  absence. `read_label` re-queries and prints `UNCONFIRMED` on disagreement.
- **`docker logs --since <relative>`** returned nothing on this host; use `--tail` or absolute
  timestamps.
- **Scheduler `graphql=N/5000` is used/limit**, not remaining. All tokens of the `omniscient`
  user share the 5,000/hr GraphQL pool; operator `--paginate` timeline queries starve the
  factory. Prefer `gh issue view --json labels` and bounded queries.
- **Run containers are `--rm`.** Logs and transcripts vanish on exit; run records under
  `/var/lib/dark-factory/run-records/` survive. `runs.sh transcript` copies out a live one.
- **Scheduler log skip lines** (`session_window_paused=true action=skip_*`) flood naive
  monitors; filter them.

## Windows and capacity

- The factory token has its **own** five-hour window; the host endpoint (`usage.sh` top half)
  reads the operator's. Authoritative factory signals: sentinel, `session_window_gate=active`,
  `stage: paused` rows.
- ~1 h of factory work per five-hour window at two runs wide. Plan node alone can be $24 / 61
  min. Opt in one ticket at a time while a plan runs; check usage before launching more than one
  reviewer subagent when a peer session is active.
- Host session limits kill subagents silently: after a reset, confirm launched agents actually
  ran (empty transcript = never started). Background Bash tasks die under host memory
  pressure; Monitor tasks survive.

## Factory behaviour you will misjudge from one file

- **Validate's `exit 1` does not stop the DAG** (workflow yaml says so); the blast-radius block
  is enforced at close/auto-merge. Read the DAG node and its trigger semantics before
  describing what a phase does. Three wrong claims in one session came from grepping one file.
- **After `push-and-pr`, the spec and plan live under `docs/archive/`.** Conformance (Gate 2)
  runs before the archive commit; code review (Gate 3), revise-advisory and report run after
  it. A lookup that only searches `docs/superpowers/specs/` is inert at Gate 3 (#403, caught by
  Gate 3 itself after the spec reviewer and the operator both missed it).
- **A refine branch older than the target's layout silently kills plan runs.** MarketHawk's
  June-era branches still tracked the `dark-factory/` tree the July extraction removed; the run
  entrypoint copies preview files into that path as untracked files, so `setup-refine-branch`'s
  checkout aborts, the plan agent lands on `main` without its spec and "ends without producing a
  plan" (three times, breaker trip). Diagnose with an attached run; fix by merging `main` into
  the refine branch (a force-push rebase is refused by the classifier as destructive).
- **`recheck` is a real intent** (main-red self-clearing, handled in entrypoint before the DAG).
  Grepping one file and finding nothing proves absence in that file only.
- **A conformance gate checks fidelity to the spec, not whether the spec is true.** Three
  operator amendments in one day asserted symbols/labels/variables that did not exist as
  claimed; only a reviewer that re-derives or executes catches these. Resolve every "X exists"
  claim against `origin/main` before writing it, and `git pull` first (local was 3 merges stale).
- **Fixes for silent-failure bugs reproduce the bug**: check whether the fix's own signal path
  can fail silently (stderr discarded under `-d --rm`; `VAR=$(cmd)` + `$?` under `set -e`; a
  detector capped at 30 rows). Ask of any guard: "if this mechanism were unavailable, would
  anything say so?"
- **Two independently safe rules can be unsafe at their seam** (MERGE-strict first-line +
  case-sensitive ambiguity scan, #402). Ask what rule A stops protecting once rule B changes.
- Security-themed tickets on the self target can make the conformance reviewer refuse the
  factory's own runtime layout (nested `dark-factory/`, `.claude/settings.local.json` overlays
  are expected). Expect the operator path (#411).
- Agents diff the received prompt against canonical `commands/*.md` and refuse mismatches as
  injection (#214): never patch prompts through the workflow mount; canonical-file PRs only.
- Agent-tool `model` param in the image CLI is an alias enum (`sonnet|opus|haiku|fable`);
  documentary pins in docs now read `claude-opus-5-5` and are passed as `opus`; aliases resolve per the
  image CLI (2.1.282: opus→Opus 5.5), so the pin fixes the tier, not the snapshot.

- **A target's `CLAUDE.md` is what the phase commands load — it must carry the headless rules.**
  MarketHawk's did not; six plan runs (#388, #441) "completed" with no plan because the Sonnet 5
  orchestrator verified the pasted command and then ended its turn asking a human "(a) proceed,
  (b) dig in, (c) leave paused?". Archon reports any ended turn as node success. Diagnose with an
  attached run whose container you keep (`docker compose run --name diag-N` without `--rm`), then
  read the transcript under `/home/factory/.claude/projects/`. Fixed target-side (MarketHawk PR
  #862) and filed factory-side as #431. A refine branch cut before the fix needs `main` merged in
  before re-dispatch, or the agent loads the old file.
- **Archon `idle_timeout` completes the node as *success*** (`dag_node_completed_via_idle_timeout`):
  10 min of stream silence on plan/implement/conformance kills the subprocess and the DAG moves on.
  A plan orchestrator waiting on a long architect subagent can die this way and look identical to
  the ended-turn case; check node `durationMs` in the run record (a 17-min plan with no plan file).
- **The classifier refuses pushes of agent-loaded instruction files** ("Instruction Poisoning": a
  CLAUDE.md change) and `gh pr merge` in autonomous turns. Prepare the branch/commit, then hand
  Frank the exact `! git push …` / `! gh pr merge …` line; do not route it through a subagent.
- **Scratchpad files do not survive a session restart** (2026-09-25: rescued plan, bundles and
  transcript copies gone; only directory skeletons remained). Anything rescued gets committed to
  a branch or attached to the issue in the same session.

## Host quirks

- `MSYS_NO_PATHCONV=1` for any docker command carrying `/opt/...` or `/var/...` args; with it
  set, give git Windows-style paths (`C:/git/...`). `lib.sh` handles both.
- Host pytest: 70+ Windows-only failures on main and branch alike; needs an `fcntl` shim and a
  Windows-form `PYTHONPATH`. CI (Linux) and the factory image are the arbiters. Bash tests can
  only be trusted in the image (`mount repo at /workspace/dark-factory`, `CLONE_DIR=/workspace`).
- `os.geteuid()` in a test decorator breaks collection on Windows; use
  `getattr(os, "geteuid", lambda: -1)()`.
- `git worktree add <branch>` reuses a stale local ref; `git reset --hard origin/<branch>` first.
- Amendment scripts: write with the Write tool, never a heredoc (backticks/quotes in prose);
  anchor on the wrapped line; normalise CRLF; never blanket-`.replace()` a whole file.
- The auto-mode classifier has blocked `gh pr merge` in compound commands; run it plain, in a
  turn where Frank asked for the merge, or ask Frank to merge.
- **Reviewer subagents cannot `git commit`** in the worktree (classifier: "Modify Shared
  Resources", 2026-09-14). Have them apply and `git add`, write the message to a file, and
  commit from the operator session with `git commit -F`. Their pushes are out of the question.
- Ask the plan reviewer to **execute the plan** on a scratch copy (apply every Replace/with
  block, run the red/green claims). On #403 it caught two vacuously green tests that a read-only
  review passed.
- Never `docker rm -v`/prune volumes here: `ncl-tl-node22-vol`/`ncl-tl-node24-vol` hold
  unreplicated work. Never root-shell into the state volume without chowning back to `factory`.
- Local timezone is UTC-4; Docker only returns after Frank's logon (no auto-logon).

## Learned 2026-09-27

- **Frank approves gates by comment** ("Plan approved!" on the issue lifts `plan-pending-review`
  via the approve-by-comment path, PR #451) at any time, including while your executing
  review is still running (#436: approved 11 min after the plan landed; implement cloned the
  unamended plan). Post the review verdict to the issue the moment it is in hand, and re-read
  the gate label before assuming a gate is still yours; land late amendments on the PR branch.
- **`Depends on:` is same-repo only.** `dependencies_met()` extracts `#\K[0-9]+` and resolves
  against the instance's own board, so `Depends on: owner/other-repo#N` gates on the WRONG
  local issue #N. Gate cross-repo dependencies by hand (`needs-discussion` or hold at the gate).
- **Merged + closed issues stay `In Review` on the board** (#436/#438/#444 sat there for hours
  and inflated `in_review=` in the poll line). Move them to Done with `board.sh set N Done`
  once the run container is gone.
- **Refine branches fork before later merges to `main`.** A plan's two-dot scope check
  (`git diff origin/main HEAD`) reports every commit main gained since the fork as foreign
  changes (#444: nine files, ~1,300 deletions from #454). Plans need a "merge origin/main"
  step before the scope check, or the three-dot form.
- **Gate 2 can report "no verdict recorded" although both reviewers returned Conforms** (#373
  recurrence on #436's first Fix run); the scheduler dispatches `Continue` and the second pass
  usually clears. Do not re-plan; attach the run id to #373.
- **Session-window pause + container loss orphans a run** (#444 implement at ~17:40Z); the
  scheduler's orphan recovery moves it to Blocked (~1 h later) and `Continue` re-verifies. Keep
  bundle snapshots so nothing is lost if recovery ever fails.
- **`status.sh` for another instance needs the whole DF_* set**, or since today just `DF_REPO`
  (lib.sh derives project, scheduler and run prefix from it). Explicit values still win.

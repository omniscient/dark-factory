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
  literal `claude-opus-4-8` pins in docs are stale text and resolve to `opus`.

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

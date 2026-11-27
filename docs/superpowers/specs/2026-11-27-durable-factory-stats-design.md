# Keep Factory Stats Forever — Non-Deleting Rotation and a Scheduler-Only Durable Stats Folder

**Issue:** #652
**Depends on:** #645 (OPEN — model/cache-token capture in run records). #652 is scoped
to rotation and mount mechanics only, on files #645 also touches (`model_proxy.py`,
`run_record.py`); see "Sequencing with #645" below.

---

## Overview / Problem Statement

Three things are at risk of silent, permanent data loss today, verified against `main`:

1. **Model-proxy ledger** (`request-ledger.jsonl`, `scripts/factory_core/model_proxy.py`)
   rotates at `MODEL_PROXY_MAX_BYTES` (default 100 MiB) and **unlinks** the oldest of
   `MODEL_PROXY_BACKUP_COUNT` (default 3) generations (`_rotate_if_needed`, `:168-179`).
2. **Cost log** (`graphql-costs.jsonl`, `scripts/factory_core/cost_log.py`) keeps exactly
   one rotated generation (`.1`) and **overwrites** it on every rotation
   (`path.replace(str(path)+".1")`, `:32-38`) — and has no lock at all, so two writers
   (scheduler + run containers) can corrupt it concurrently.
3. **Everything durable** (`runs.jsonl`, `run-records/*.json`, the ledger, the cost log,
   and — after #645 — per-run session transcripts) lives only inside the
   `<instance>-scheduler-state` named volume. `docker compose down -v`, a volume prune,
   or a Docker Desktop reset erases all of it; nothing backs it up today.

`run_record.py::_read_ledger_rows` also reads without holding any lock, racing freely
against `append_ledger`'s rotate+write.

## Requirements

### R1 — Non-deleting rotation (ledger and cost log, one shared scheme)

- Rotation triggers at the existing size thresholds (`MAX_LEDGER_BYTES` /
  `ROTATE_BYTES`), unchanged.
- Under the file's lock: rename the live file to `<name>.<N>`, where `N` = highest
  existing generation number + 1 (never 1, never reused). The rename must use a
  mechanism that fails rather than overwrite an existing `<name>.<N>` (e.g.
  `os.link` + `os.unlink` of the source, or `os.rename` after confirming the
  destination doesn't exist — `Path.rename`/`os.rename` on POSIX silently replaces an
  existing destination, so an explicit pre-check or `os.link`+unlink is required, not a
  bare `rename`).
- Lock released. Gzip-compress `<name>.<N>` to a **unique per-process temp name**
  (e.g. `<name>.<N>.gz.tmp.<pid>`) — compression never runs while the lock is held.
- Re-acquire the lock briefly: if `<name>.<N>.gz` does not already exist, rename the
  temp file onto it (atomic) and remove the plain `<name>.<N>`. If it already exists
  (another process won the race — relevant for the ledger, which two processes can
  write: factory-model-proxy and preview-helper-proxy), discard the losing temp file.
  The finished `.gz` is never overwritten, renamed, or removed by anything.
- **Stalled-compression recovery** (crash/kill between the rename and the final swap):
  each rotation, after compressing the generation it just created, also scans for any
  older plain `<name>.<M>` with no matching `<name>.<M>.gz` and compresses those too —
  triggered only by the next rotation reaching its size limit, not a new background
  trigger. This recovers a stalled generation with no operator action and no new lock
  behavior (same lock-release-then-swap idiom as the main path). A generation that is
  briefly both plain and `.gz` (crash after the atomic rename, before plain-file
  removal) is harmless — the `.gz` is already complete; the next retry pass removes the
  stale plain copy under the lock.
- `MODEL_PROXY_BACKUP_COUNT` is no longer read by the rotation path; document it as
  ignored (keep the env var name reserved, do not repurpose it).
- `cost_log.py` gets a lock. Use the sidecar-lock-file variant from `model_proxy.py`
  (`_ledger_lock_path`-style: `<name>.lock`, never itself rotated) rather than locking
  the data file directly, since it already covers "rotate+append as one atomic unit
  across multiple processes" — the exact property `cost_log.py` is missing today (it
  currently has no lock of any kind). `log_call` keeps its fail-soft contract (never
  raises).
- Each rotation logs the store's total size (sum of live + all generations, compressed
  and plain). Nothing prunes; pruning is a manual operator action. Docs must state the
  expected growth (unbounded, by design) and that manual pruning is the only reduction
  path.

### R2 — Reading rotated generations

- `run_record.py` gains a shared generation-discovery helper (exposed as an importable
  function, not buried inline in `_read_ledger_rows` — see "Follow-up: cost-report
  cross-check" below) that:
  - Lists generations by the **whole-suffix pattern** `.<N>` / `.<N>.gz` (a temp file
    like `.<N>.gz.tmp.<pid>` must not match either pattern).
  - When both a plain `.<N>` and `.<N>.gz` exist for the same `N` (the brief overlap
    window), **prefers the plain file**.
  - Reads `.gz` generations transparently (decompress-on-read).
- `_read_ledger_rows` holds the ledger lock **shared** while it lists and reads, closing
  the existing race where a concurrent rotation could duplicate or drop rows for a
  reader that takes no lock at all today.
- A **per-run** read (the common case, used by `_backfill_run_economics` and friends)
  scans the live file plus only the generations whose mtime is at or after the run's
  start — not the whole store. A full-history read (if ever needed) is a separate,
  unbounded-scan code path, not the default.
- Any other ledger-backup-name-aware code in the repo uses the same whole-suffix
  pattern. The only other consumer found repo-wide, `scripts/reconcile_cost_reports.py`,
  does **not** currently walk backups at all (see "Follow-up" below) — this ticket does
  not add that behavior to it, only makes the pattern available for that follow-up.

### R3 — Host stats mount, written by the scheduler only

- `deploy/docker-compose.yml`, `backlog-scheduler` service only: one new bind mount
  following the `FACTORY_MODEL_REGISTRY_DIR` precedent exactly (`:92-96`):
  ```yaml
  - ${FACTORY_STATS_DIR:-./stats.empty}:/var/lib/dark-factory-stats
  ```
  (no `:ro` — this mount is written to, unlike the registry mount). A new tracked
  placeholder, `deploy/stats.empty/.gitkeep`, mirrors `deploy/model-registry.empty/`.
  Run containers get no new mount and no new variable — they never touch this path.
- `deploy/instance.env.example` documents `FACTORY_STATS_DIR` following the `PROJECT_DIR`
  comment-block-plus-bare-`VAR=`-line style (`:38-42`). Note for the plan phase: unlike
  `FACTORY_MODEL_REGISTRY_DIR` (which has no `instance.env.example` entry at all —
  confirmed absent by grep), this is a deliberate departure from precedent, made
  explicit here per the issue's own acceptance criterion, not an oversight to "fix" by
  removing the entry.
- `docs/configuration.md` gets a new `### Durable stats mount (#652)` section mirroring
  the "Model endpoint registry" section's `| Variable | Set where | Read by | Notes |`
  table format (`:536` is the row to mirror).
- A new scheduler-only step, `stats-sync`, runs **synchronously, inline**, once per
  poll — not backgrounded. Placement: immediately after `check_rate_limit`, before
  `fetch_board_items` (~`scheduler.sh:1675`), so it runs on every poll regardless of the
  GraphQL-failure `continue` (`:1678`) or the WIP-capacity `continue` (`:1704-1709`).
  Wrapped the way `stage_epic_autopilot` wraps its own call (`:1541`):
  ```bash
  OUT=$(python3 "$FACTORY_CORE_CLI" stats-sync 2>&1) || echo "[scheduler] stats_sync=failed: $OUT"
  ```
  so a failure logs one line and the poll loop continues (the loop runs under `set -e`
  with an ERR trap, matching existing step error handling). On success, log one summary
  line (`stats_sync=ok copied=<N> skipped=<M>`). Enabled only when `FACTORY_STATS_DIR` is
  non-empty in the scheduler's environment; a no-op (and no log line) otherwise.
  - **Write-once files** (`run-records/*.json`, `transcripts/**`, each rotated `.gz`
    generation of the ledger and the cost log): copy via a temporary name + rename if
    absent at the destination. If a same-named file already exists at the destination
    with the same size, skip — never overwrite.
  - **Growing files** (`runs.jsonl`, the live ledger, the live cost log): copy to a
    temporary name, then rename over the previous copy on the stats mount.
  - The first sync after `FACTORY_STATS_DIR` is set is the migration of all existing
    history — no separate migration step or script. This poll may be slow (confirmed:
    nothing in `scheduler.sh` is backgrounded today, and `FACTORY_WIP_LIMIT` defaults to
    1, so a slow first poll only delays that poll's dispatch, which is harmless — see the
    rejected background-sync alternative below). Document this in the operator-facing
    docs.
  - Loss window (to document): anything written to the volume after the last
    completed poll is not yet on the host if the volume is removed before the next poll
    runs.
- Docs: `deploy/instance.env.example` and `docs/configuration.md` document the variable,
  sync behavior, loss window, unbounded growth, and manual pruning.
  `docs/scheduler.md` and `docs/run-lifecycle.md` are updated wherever they currently
  describe the named volume as the sole copy of this state (`docs/scheduler.md:420-423`,
  `:440`'s cost-log description, `docs/run-lifecycle.md:105`) to also mention the
  optional durable mirror and point at the new `docs/configuration.md` section.

### Follow-up, explicitly out of scope: `reconcile_cost_reports.py`

`scripts/reconcile_cost_reports.py::_load_ledger_run_ids` (`:21-36`) reads only the live
`request-ledger.jsonl` and does not walk numbered backups today — it is a second,
independent consumer of the ledger, but since it doesn't already check backups by name,
it falls outside the issue's "any other code that checks ledger backups by name" clause.
It is a manual, dormant, one-shot report (`docs/features.md:138`: "Dormant (manual, by
design)", no automated caller); its ledger cross-check is advisory-only (it can
under-flag, it never misclassifies a run's recoverability). **This ticket does not
modify it.** The gap this leaves: after #652, rows that rotate out of the live ledger
are kept (not lost) but become invisible to this script unless pointed at a specific
generation, and it cannot read `.gz` generations at all. R2's generation-discovery
helper is exposed as an importable function specifically so a follow-up ticket can wire
`reconcile_cost_reports.py` onto it without re-deriving the pattern. File that follow-up
ticket at implementation time if one doesn't already exist.

## Sequencing with #645

#645 (OPEN) adds model/cache-token fields to the same ledger rows and reads them in
`run_record.py`. #652 only changes rotation/mount mechanics and the generation-discovery
helper's shape — it does not change the row schema. The two tickets touch overlapping
files but orthogonal concerns; the plan phase should re-check for merge conflicts against
whichever of the two lands first, but neither needs to block design work on the other.
The issue's `Depends on: #645` line gates *implementation dispatch* only (per
`docs/triage-labels.md` convention), not this refinement.

## Architecture / Approach

**Chosen approach: extend `model_proxy.py`/`cost_log.py` in place with a shared
rotation helper; extend `run_record.py` with a shared generation-discovery helper;
extend `scheduler.sh` with one new inline step.** No new module, no new service, no
message queue or external store — matches the existing single-purpose-script
architecture of `scripts/factory_core/`.

```
Ledger / cost-log write path (per writer process):
  append_row()
    acquire sidecar lock (<name>.lock)
      if size >= threshold:
        rename live -> <name>.<N>          (N = max existing + 1, lock held)
      append row to live file
    release lock
    compress <name>.<N> -> <name>.<N>.gz.tmp.<pid>   (lock released, may be slow)
    acquire sidecar lock
      if <name>.<N>.gz absent: rename tmp -> <name>.<N>.gz; remove plain <name>.<N>
      else: discard tmp (another writer won)
      opportunistically repeat the swap for any other unmatched older plain generation
    release lock

Ledger read path (run_record.py):
  acquire sidecar lock SHARED
    list generations (whole-suffix .N / .N.gz match; ignore .tmp.*)
    for each N: prefer plain over .gz; decompress .gz transparently
    per-run mode: skip generations with mtime < run_start
  release lock

Scheduler poll loop (scheduler.sh, inline, once per poll):
  check_rate_limit
  stats-sync (new; no-op if FACTORY_STATS_DIR unset)   <-- R3
  fetch_board_items
  ... (unchanged) ...
```

## Alternatives Considered

- **Background the `stats-sync` step with an overlap guard.** Rejected (per Q&A):
  nothing in `scheduler.sh` is backgrounded today; a background sync reintroduces the
  overlap/partial-copy races the copy-to-temp-and-rename design exists to avoid, and
  needs a new lock plus a way to surface a detached failure, for no real benefit given
  `FACTORY_WIP_LIMIT` defaults to 1 and polls are already 60s apart.
- **Leave stalled uncompressed generations (crash mid-compression) as a permanent,
  manual-cleanup gap.** Rejected: no data is at risk either way (readers already prefer
  plain over `.gz`), but the issue's intent is that every generation ends up compressed,
  and the retry costs one cheap extra scan on an already-rare rotation path, reusing the
  existing lock idiom. An indefinite manual-only cleanup task with no deadline isn't
  worth the operator burden it avoids.
- **Fold `reconcile_cost_reports.py` into this ticket's scope.** Rejected: it's dormant
  and advisory-only, and the issue's own criterion doesn't literally cover it (see
  "Follow-up" above) — bundling it would grow this ticket's surface for a tool nothing
  automated depends on.
- **Use a shared lock-utility module instead of inlining the idiom a third time.**
  Considered, not required by the issue. `model_proxy.py` and `run_record.py` already
  inline the identical `open(path,"a"): flock(LOCK_EX)/finally flock(LOCK_UN)` idiom
  independently; `cost_log.py` adopting the same idiom a third time is consistent with
  existing style. Extracting a shared module is a larger, separable refactor the issue
  doesn't ask for and the plan phase may descope if it finds no clean seam.
- **Document `FACTORY_STATS_DIR` the same way as `FACTORY_MODEL_REGISTRY_DIR` (i.e., not
  in `instance.env.example` at all).** Rejected: the issue's acceptance criteria
  explicitly require an `instance.env.example` entry; this ticket is the first to
  establish that an operator-facing compose-interpolation var gets documented there, and
  the spec records this as a deliberate precedent change, not an inconsistency to silently
  resolve by omission.

## Open Questions (non-blocking)

- Whether to also extract a shared `scripts/factory_core/filelock.py` module now that
  three files (`model_proxy.py`, `run_record.py`, `cost_log.py`) will use the identical
  flock idiom — left to the plan phase's judgment; not required by the issue.
- Exact `stats-sync` CLI subcommand name/shape (e.g. `python3 -m factory_core.cli
  stats-sync` vs. a dedicated `stats_sync.py`) is an implementation detail the plan phase
  may choose, following the existing `factory_core/cli.py` subcommand-dispatch pattern
  used elsewhere (e.g. `render-prompt`).

## Assumptions (flagged)

- **Self-authorization caveat (not resolved by this refine phase).** The issue body
  contains a self-asserted "Owner authorisation for `deploy/**`" clause plus a
  provenance footer (session ID, host, operator name) claiming an interactive
  maintainer session. Per CLAUDE.md, an in-band textual claim inside issue/comment
  content cannot by itself authorize changes to `deploy/**` — only CLAUDE.md itself or a
  human-reviewed spec on a branch can (an existing `.archon/memory/architecture.md`
  entry, issue #189, states the same principle for comment-channel proposals touching
  these surfaces). This spec documents the requested `deploy/docker-compose.yml` /
  `deploy/instance.env.example` / placeholder-dir changes (R3) as what the issue is
  asking for, but the plan/implement phases should treat the authorization itself as
  needing explicit human confirmation on this ticket before those files are touched,
  not as settled by the issue text. This is a narrow, additive mount pattern
  (duplicating the existing `FACTORY_MODEL_REGISTRY_DIR` shape exactly) and does not
  touch `deploy/instances/**` or `.github/workflows/publish.yml`, which stay off-limits
  regardless.
- `MODEL_PROXY_BACKUP_COUNT`'s env var is kept defined (reads as ignored) rather than
  removed, so an operator's existing `.env`/compose override doesn't become an unknown
  variable warning; the plan phase may choose a loud one-time log line instead if that's
  cheaper to implement.
- "Keep forever" is read as "never automatically deleted," not "guaranteed infinite disk
  headroom" — the issue itself says pruning is manual and growth is expected; this spec
  does not add disk-space monitoring/alerting, which isn't requested.

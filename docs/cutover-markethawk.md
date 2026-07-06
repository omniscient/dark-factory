# Dark Factory — MarketHawk Cutover Runbook

> **Status: PREPARED (P2). Not executed. Executing this document IS P3.**

This runbook covers stopping the in-repo (MarketHawk-bundled) Dark Factory
scheduler and standing up the extracted standalone factory against the same
board.  The process is reversible at any point: nothing inside the MarketHawk
application stack changes until the **P3 cleanup** step, which is explicitly
gated on post-cutover observation.

---

## 1. Preconditions

All of the following must hold before executing the cutover:

- [ ] `docs/parity-p2.md` verdict = **PASS** (bench suite pass^k ≥ baseline on
  all size buckets).
- [ ] MarketHawk project board is **quiet**: no tickets in *In Progress* or *In Review*
  at the moment of the switch (let running factory runs complete or mark them
  Blocked first).
- [ ] `omniscient/markethawk` `main` branch is **green** (all CI required checks
  passing).
- [ ] Controller sign-off received on issue
  [omniscient/markethawk #738](https://github.com/omniscient/markethawk/issues/738).
- [ ] `IMAGE_TAG` digest to pin has been copied from the parity-p2.md evidence
  record (the specific digest the bench run validated).
- [ ] Both P3-blocker follow-up tickets are resolved or have a known-safe
  workaround documented:
  - omniscient/dark-factory #14 — config resolution from
    `.factory/adapter.yaml` before `.claude/skills/refinement/config.yaml`
  - omniscient/dark-factory #15 — preview-up/preview-down hook
    rewire (post-P3 if not yet landed; document the manual workaround below).

---

## 2. Cutover Steps

### Step 2.1 — Stop the in-repo scheduler

In the **MarketHawk** application stack directory (e.g. `/srv/markethawk`):

```bash
docker compose stop backlog-scheduler
```

**Do NOT `docker compose down` the full MarketHawk stack** — only the
`backlog-scheduler` service.  The application (backend, frontend, DB, Redis,
Celery, etc.) must keep running.

Verify it stopped:

```bash
docker compose ps backlog-scheduler
# Expected: Status = Exited (0) or similar — not Up
```

### Step 2.2 — Prepare the standalone instance env

In the **dark-factory** repo checkout:

```bash
cp deploy/instances/markethawk/instance.env deploy/instance.env
$EDITOR deploy/instance.env
```

Fill in the two secret fields:

```bash
GH_TOKEN=<token-with-repo+project+workflow-scope>
CLAUDE_CODE_OAUTH_TOKEN=<max-plan-oauth-token>
```

Pin the image digest from parity-p2.md (replace `sha256:...` with the exact
digest recorded there):

```bash
# IMAGE_TAG=latest
IMAGE_TAG=sha256:<parity-verified-digest>
```

### Step 2.3 — Start the standalone scheduler

```bash
export PROJECT_DIR=/path/to/markethawk-checkout  # absolute host path

docker compose -f deploy/docker-compose.yml up -d

# Tail logs to confirm healthy startup
docker compose -f deploy/docker-compose.yml logs -f backlog-scheduler
```

Expected log lines within 30 s:
- `[scheduler] polling board …`
- No authentication errors or missing-env-var panics.

### Step 2.4 — Smoke: dispatch a "Recheck main" ticket

Move any Ready ticket (or the dedicated smoke-canary ticket) to the **Ready**
column on the MarketHawk board.  Within one poll interval (≤ 60 s) you should
see:

```
[scheduler] dispatching issue #N …
[scheduler] run container dark-factory-dark-factory-run-<hash> started
```

Confirm the run container appears:

```bash
docker ps --filter name=dark-factory-dark-factory-run
```

### Step 2.5 — Observe 2–3 tickets end-to-end

Let the factory pick up 2–3 real Ready tickets and drive them to draft PRs
before declaring the cutover complete.  Watch for:

- Correct identity in PR titles/comments (MarketHawk product name, correct repo links).
- No mis-routed memory writes.
- Adapter hooks (`smoke-gate`, `validate`) running without errors.
- Preview-up behaviour (see note below if omniscient/dark-factory #15
  is not yet merged).

---

## 3. Rollback

Rollback is **trivial** until the P3 cleanup step because nothing in MarketHawk
has been deleted — both schedulers can coexist (just not at the same time on
the same board).

### Steps

```bash
# 1. Stop the standalone scheduler
docker compose -f deploy/docker-compose.yml down

# 2. Restart the in-repo scheduler (in the MarketHawk stack directory)
docker compose start backlog-scheduler

# 3. Verify it is polling
docker compose logs backlog-scheduler --tail=20
```

That is the complete rollback.  No database changes, no code changes.

---

## 4. P3 Cleanup (only after cutover observation)

> **Gate:** complete Step 2.5 (2–3 tickets end-to-end) before starting this
> section.  Once cleanup is done, rollback requires reverting git history.

### Blockers to resolve before cleanup

The following must be resolved (merged or explicitly waived) before
deleting the in-repo factory files:

**a) token_optimization config re-point** (omniscient/dark-factory
[#14](https://github.com/omniscient/dark-factory/issues/14))  
**RESOLVED** — resolved by the #14 PR: the factory materializes the effective
config per run (adapter `token_optimization` > clone
`.claude/skills/refinement/config.yaml` (transition) > baked
`config/config.yaml`), and self-contained fallback copies let the cleanup
also delete `.archon/workflows/`, `.archon/commands/` and the `dark-factory/`
scripts.  Verify the image running in production was built after that merge.

**b) Memory relocation** `.archon/memory/` → `.factory/memory/`  
Requires `MEMORY_DIR` generalization in the factory; coordinate with whoever
owns the memory-dir migration ticket.

**c) Scheduler `Depends on:` and board-machinery spot-checks**  
Verify no in-flight tickets carry `Depends on:` references into the deleted
`dark-factory/` subtree.

### Cleanup actions (MarketHawk repo)

```bash
# In the MarketHawk repo checkout

# 1. Remove in-repo factory subtrees
git rm -r dark-factory/
git rm -r .archon/workflows/
git rm -r .archon/commands/
git rm -r .claude/skills/refinement/

# 2. Update CI: remove the docker-dark-factory image build job and required check
#    (it has moved to the dark-factory repo's CI)
#    Edit: .github/workflows/*.yml  — remove the docker-dark-factory job and
#    update the branch protection required-checks list via the GitHub UI or API.

# 3. Verify .factory/ adapter still targets the correct paths after deletion
#    (TARGET-PATH references in workflows and commands must be factory-side)

# 4. Commit and PR
git commit -m "cleanup(p3): remove in-repo dark-factory after extracted cutover"
```

---

## 5. References

- Extraction plan:
  [docs/superpowers/plans/2026-07-03-dark-factory-extraction-p0-p1.md](https://github.com/omniscient/markethawk/blob/main/docs/superpowers/plans/2026-07-03-dark-factory-extraction-p0-p1.md)
- P2 plan:
  [docs/superpowers/plans/2026-07-04-dark-factory-extraction-p2.md](https://github.com/omniscient/markethawk/blob/main/docs/superpowers/plans/2026-07-04-dark-factory-extraction-p2.md)
- Parity evidence: `docs/parity-p2.md` (dark-factory repo)
- MarketHawk adapter PR: see omniscient/markethawk PR linked from #738
- Instance env template: `deploy/instances/markethawk/instance.env`
- Tracking issue: [omniscient/markethawk #738](https://github.com/omniscient/markethawk/issues/738)

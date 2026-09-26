# Onboarding a new product onto Dark Factory

This guide takes a repo that has never seen Dark Factory to a first factory-made
draft PR. It is written to be followed literally, by a person or by an AI agent handed
this file. Every step says what to run and what you should see. If a step does not
produce the expected output, stop and fix it before continuing; later steps fail in
confusing ways otherwise.

Validated end to end on `omniscient/jobfinder` (a flat Python/FastAPI repo, Windows 11
host with Docker Desktop, 2026-09-26). The first run cost about $2.50.

**Placeholders used below:** `OWNER/REPO` is your GitHub repo; `TARGET` is the
absolute path of its local checkout; `DF` is your dark-factory checkout.

---

## 0. What you are building

```
DF/deploy/instance.env ──► backlog-scheduler container (polls your board every 60 s)
TARGET/.archon/.env    ──► copied by the scheduler ──► per-ticket run containers
                                                      │ git clone OWNER/REPO (default branch)
                                                      ▼
                                   .factory/adapter.yaml + .factory/hooks/* (from the clone)
```

Four things must exist before the first ticket:

1. The repo on GitHub, with a Projects v2 board and the factory's labels (step 2).
2. Target-side files committed to the default branch: an adapter, a smoke-gate hook
   and a `CLAUDE.md` section (step 3).
3. Two env files: one for the scheduler, one for the runs (step 4).
4. A running scheduler (step 5).

## 1. Prerequisites

| Need | Check |
|------|-------|
| Docker with Compose v2 | `docker compose version` |
| `gh` authenticated with `repo`, `project`, `workflow` scopes | `gh auth status`; `gh api -i user \| grep -i x-oauth-scopes` |
| `jq` | `jq --version` |
| Claude credential | subscription: run `claude setup-token` and keep the `sk-ant-oat…` token it prints. Or use an `ANTHROPIC_API_KEY`. |
| The repo is on GitHub, and its default branch has everything you want the factory to see | `gh repo view OWNER/REPO` |

The factory image is public: `docker pull ghcr.io/omniscient/dark-factory:latest`.

```bash
git clone https://github.com/omniscient/dark-factory.git DF
```

## 2. Board and labels

```bash
cd DF
scripts/bootstrap_target.sh OWNER/REPO          # optional: --title "Board name"
```

Expected: `[board] created project #N`, `[board] Status options set`,
`[labels] 23 labels created/updated`, then a `FACTORY_*` block. Keep that block for
step 4. Re-running the script is safe; on the second run it reports
`Status options already correct`.

The script is needed because the scheduler moves tickets between exactly these
columns: Backlog, Refined, Ready, In Progress, In Review, Blocked, Done. A new GitHub
project only has Todo / In Progress / Done. The factory also adds labels but never
creates them, and `gh --add-label` fails on a missing label.

## 3. Target-side files (commit these to the default branch)

Start from the templates:

```bash
cp -r DF/templates/new-target/.factory TARGET/
cat DF/templates/new-target/.gitattributes >> TARGET/.gitattributes
```

### 3a. `.factory/adapter.yaml`

Edit every `EDIT` line. **Do not skip this file.** When a key is absent, the factory
uses MarketHawk's paths (`backend/`, `frontend/`, alembic) and its ARCHITECTURE.md
section names. List-valued keys replace the defaults rather than merging with them,
which is why the template repeats the factory-self safety paths. Key reference: the
README's *Adapter contract* table.

### 3b. `.factory/hooks/smoke-gate` (required)

Before each ticket, the factory checks that the default branch is healthy. **Without
this hook it runs MarketHawk's check** (`tsc` in `frontend/` and a Python import in
`backend/`). That check fails on any other layout, latches `main-is-red`, files a
regression ticket, and halts all dispatch (tracked in #436).

Replace the `EDIT` commands with your project's install and test commands. The hook
runs in the factory image (Ubuntu 26.04, Python 3.14, Node 22, **no browsers**) as a
non-root user, with `CLONE_DIR` pointing at a fresh clone. Exit 0 means green.

Test it in the image **before committing**, with the same environment the factory
uses:

```bash
# Git Bash on Windows: prefix with MSYS_NO_PATHCONV=1
docker run --rm -v "TARGET:/src:ro" --entrypoint bash ghcr.io/omniscient/dark-factory:latest \
  -c 'cp -r /src /tmp/t && CLONE_DIR=/tmp/t /tmp/t/.factory/hooks/smoke-gate; echo rc=$?'
```

Expected: your test summary, `[smoke-gate hook] main is green`, `rc=0`. Tests
commonly fail here because a test dependency isn't declared (install it in the hook),
or because a test needs a browser or an external service (skip it in the hook with
`--ignore=` or a marker).

`.factory/hooks/validate` (optional) runs after merges in the deconflict flow. The
template reuses the same commands.

### 3c. Make the hooks executable and LF

The factory runs a hook only if `[ -x hook ]`. **A non-executable hook is silently
ignored, and the MarketHawk default runs instead** (tracked in #438). On Windows,
`git add` records mode 100644, so set the bit explicitly:

```bash
cd TARGET
git add .factory .gitattributes
git update-index --chmod=+x .factory/hooks/*
git ls-files -s .factory/hooks        # expect 100755 on every hook
```

### 3d. `CLAUDE.md`: headless rules for phase agents

Phase agents read the target's `CLAUDE.md`. Without the headless rules, an agent may
end its turn on a question, and that kills the run (tracked in #431). Append the
scoped section, which your own interactive sessions will ignore:

```bash
cat DF/templates/new-target/CLAUDE.factory-section.md >> TARGET/CLAUDE.md
```

It is also worth adding one line that tells agents your test command, in the form the
container can run.

### 3e. Commit and push to the default branch

```bash
git commit -m "chore(factory): onboard to Dark Factory"
git push origin HEAD:<default-branch>
```

Runs clone the default branch, so the files only take effect once they're on it.

## 4. Env files

The scheduler and the run containers read **different files**.

**`DF/deploy/instance.env`** (scheduler):

```bash
cp DF/deploy/instance.env.example DF/deploy/instance.env
```

Fill in:
- `GH_TOKEN` (e.g. from `gh auth token`) and `CLAUDE_CODE_OAUTH_TOKEN`.
- The `FACTORY_*` block from step 2.
- `FACTORY_PRODUCT_NAME`.
- `FACTORY_INSTANCE=<repo>`. This keeps container names and the state volume separate
  from any other instance on the host.
- `PROJECT_DIR=TARGET`. On Windows use forward slashes: `C:/git/my-repo`.

Leave no identity line blank: unset values fall back to MarketHawk's board.

**`TARGET/.archon/.env`** (run containers; must be gitignored in the target):

```bash
mkdir -p TARGET/.archon
printf 'GH_TOKEN=%s\nCLAUDE_CODE_OAUTH_TOKEN=%s\n' "<gh token>" "<claude token>" > TARGET/.archon/.env
cd TARGET && git check-ignore -v .archon/.env   # must print a matching rule; add ".env" to .gitignore if not
```

## 5. Start the scheduler

```bash
cd DF
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml logs backlog-scheduler
```

Expected lines:

```
providers preflight: OK
Provisioned dispatch env file at /opt/dark-factory/.archon/.env from bind mount
Backlog scheduler started (poll every 60s)
[...] backlog=0 refined=0 ... skip=nothing_to_do main_red=false
```

| You see | Meaning |
|---------|---------|
| `WARNING: /workspace/project/.archon/.env not found` | `PROJECT_DIR` is wrong, or step 4's second file is missing |
| `ERROR: GH_TOKEN is not set` | `instance.env` wasn't passed: check `--env-file` |
| Containers named `dark-factory-*` instead of `<repo>-*` | `--env-file` missing or `FACTORY_INSTANCE` blank |

## 6. First ticket

Pick something small and low-risk, such as a docs or dependency change. Write
explicit acceptance criteria. Add the `size: S` label, add the issue to the board, and
set it to **Ready**:

```bash
URL=$(gh issue create --repo OWNER/REPO --label "size: S" --title "..." --body "...")
gh project item-add <N> --owner OWNER --url "$URL"
# then set Status = Ready in the board UI (or gh project item-edit)
```

- **Ready** goes straight to implementation.
- A **Backlog** ticket with the `ready-for-agent` label is refined first: spec, then
  plan, each behind a review label.
- Avoid first tickets that touch paths listed in `migration_seed_auth_patterns` or
  `.factory/hooks/`. Those require human sign-off by design.

Within a poll interval, `docker ps` shows `<FACTORY_RUN_PREFIX><hash>`. Follow it with
`docker logs -f <name>`. The run cleans itself up afterwards (`--rm`); save the log if
you need it later. The phases are: smoke-gate, then implement, validate, conformance
and code review. The ticket ends with a draft PR in **In Review**, or in **Blocked**
with a comment explaining why.

## 7. When a ticket is Blocked

A gate that blocks is working as designed: it posts a "Blocked" comment with its
findings and adds `needs-discussion`. The run log will still say
`Dark factory failed (exit 1)` and an extra "Run — Failed" comment appears; that
reporting is tracked in #443. To resume:

1. Comment on the issue with the human decision, and say explicitly: "Continue on the
   existing PR #N branch".
2. Remove `needs-discussion`.
3. Set the ticket back to **Ready**.

The re-dispatch is classified as a new implementation, and the branch is reset to the
default branch. It is the phase agent that reads your comment, rebases the PR's
commits and pushes them with a lease. So the comment in step 1 is what keeps your PR.

## 8. Operating notes

- **Several instances on one host share the local `:latest` image.** Pulling a new
  image for one instance changes the image the others' next runs use. Before pulling,
  make sure no instance has a run in flight, and prefer pinning `IMAGE_TAG`
  (digest pinning is not possible yet; see #443).
- **Cost:** plan and implement run on Opus. Budget a few dollars per small ticket; the
  cost report is posted on each issue.
- **Stopping:** `docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml stop backlog-scheduler`.
  In-flight runs continue until they finish.

## Known gaps for non-MarketHawk layouts

These don't block onboarding, but you should expect them:

- **Dependencies:** they are pre-installed only from `backend/requirements.txt` and
  `frontend/package.json`. Other layouts rely on the phase agent (and your
  `CLAUDE.md` line) to install them (#440).
- **Hardcoded paths in phase instructions:** they mention `backend/`, `frontend/` and
  alembic; agents adapt, but the prompts are noisier (#440).
- **Preview:** it tries to build `backend/Dockerfile` and posts "Preview environment
  failed to start" on layouts without one. This is harmless (#440, #15).
- **Memory write:** it fails on the first run because `.archon/memory/` does not exist
  yet (#444).

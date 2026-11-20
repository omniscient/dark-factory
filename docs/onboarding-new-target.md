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
2. Target-side files committed to the default branch: an adapter and a smoke-gate
   hook (step 3).
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

Two labels the code uses are not in the script's list: `manifest-intake` (applied by
handoff-manifest intake, which nothing dispatches yet) and `merged-with-edits` (a manual
override read by the scorecard). Create them by hand if you use either feature.

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
which is why the template repeats the factory-self safety paths. Two lists are the
exception: `critical_diff_paths` and `migration_seed_auth_patterns` always get a small
factory-owned floor added on load (`.factory/hooks/`, `.claude/` and the other
factory-boundary paths), whatever your file says, so the template's entries for those
paths are belt and braces. Key reference: the README's *Adapter contract* table; the
loader's exact rules are in `docs/adapter-authoring-guide.md` (*The adapter file*).

Validate the file **before committing**, using the loader the factory itself uses:

```bash
# Git Bash on Windows: prefix with MSYS_NO_PATHCONV=1
docker run --rm -v "TARGET:/src:ro" --entrypoint bash ghcr.io/omniscient/dark-factory:latest \
  -c 'PYTHONPATH=/opt/dark-factory/scripts python3 -m factory_core.adapter --clone-dir /src --validate'
```

Expected: `adapter OK`. Anything else prints `adapter INVALID: <reason>` and exits 1.
Do not skip this check. The factory runs the same validation at run start and refuses
the run (stderr, a `hook-warnings.log` line, and a ticket comment) when the committed
adapter is invalid, rather than carrying on with the built-in MarketHawk defaults
([#492](https://github.com/omniscient/dark-factory/issues/492)); the scheduler also logs a
`WARNING: ADAPTER INVALID` line at boot. `close` and `recheck` runs are not refused. Catching
it here saves a failed ticket attempt. A YAML slip such as a bad escape inside a
double-quoted regex is enough to trigger it.

### 3b. `.factory/hooks/smoke-gate` (required)

Before each ticket, the factory runs this hook to check that the default branch is
healthy. **Without it the factory refuses the ticket's run** (#436): the run fails before
any work starts, the ticket gets a comment naming the missing hook (a `fix` or `continue`
run also moves it to **Blocked**), and a `smoke-gate-hook-missing` line lands in
`hook-warnings.log`. `main` is not checked and not marked red, and other tickets are not
paused.

Replace the `EDIT` commands with your project's install and test commands. The hook
runs in the factory image (Ubuntu 26.04, Python 3.14, Node 22, **no browsers**) as a
non-root user, with `CLONE_DIR` pointing at a fresh clone. Exit 0 means green. Hooks
also receive `ARTIFACTS_DIR`, `ISSUE_NUM` and `FACTORY_REPO_SLUG`; any of them can be
empty, so do not depend on them in a smoke gate.

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

`.factory/hooks/deps-audit` (optional) is the check for the dependency-health loop
(#484). The template copies a pip-audit + npm audit stub; edit it to mirror your CI
audit steps (same files, same ignore list), or delete it to keep the loop inactive.
The loop ships off on the factory side either way; see the README hooks table.

### 3c. Make the hooks executable and LF

A hook without the executable bit still runs, but only through `bash hook`, so its
shebang is ignored. Each run also logs a loud `hook-not-executable` warning in three
places: the run's stderr, a line in `hook-warnings.log` in the scheduler state
directory (`/var/lib/dark-factory` by default), and the main-is-red ticket when a
smoke-gate hook fails (#438). The warning reports `crlf=yes` if the hook has CRLF line
endings; the container's bash does not tolerate them. An empty hook file is treated
as absent, so the run is refused (see 3b). Set the bit anyway. On Windows, `git add`
records mode 100644, so set it explicitly:

```bash
cd TARGET
git add .factory .gitattributes
git update-index --chmod=+x .factory/hooks/*
git ls-files -s .factory/hooks        # expect 100755 on every hook
```

### 3d. `CLAUDE.md`: nothing factory-specific required

Phase agents run headless: no human answers a question, and a turn that ends on one
kills the run. The factory injects these headless rules into every phase command
itself (#431). **Do not add a headless section to the target's `CLAUDE.md`.** It would
duplicate the injected contract, and the two copies would drift apart.

It is worth adding one line that tells agents your test command, in the form the
container can run (e.g. which tests to skip because the image has no browser).

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
- `FACTORY_INSTANCE=<repo>`, in lowercase letters, digits, `_` and `-` only, starting
  with a letter or digit (a normalized compose project name). This keeps container
  names and the state volume separate from any other instance on the host.
- `PROJECT_DIR=TARGET`. On Windows use forward slashes: `C:/git/my-repo`.

Leave no identity line blank: unset values fall back to MarketHawk's board.

**`TARGET/.archon/.env`** (run containers; must be gitignored in the target):

```bash
mkdir -p TARGET/.archon
printf 'GH_TOKEN=%s\nCLAUDE_CODE_OAUTH_TOKEN=%s\n' "<gh token>" "<claude token>" > TARGET/.archon/.env
cd TARGET && git check-ignore -v .archon/.env   # must print a matching rule; add ".env" to .gitignore if not
```

The scheduler copies this file into place once, when it starts. After editing it,
restart the scheduler (`docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml restart backlog-scheduler`)
or new runs keep the old values.

**Optional: target build daemon / preview network.** Skip this unless your target's
preview builds use its own remote BuildKit daemon. Most targets have none. If yours
does, add to `DF/deploy/instance.env`:

```bash
FACTORY_BUILD_NETWORK=<the Docker network your BuildKit daemon is on>
BUILDKIT_HOST=<the daemon's container name or alias on that network>
BUILDKIT_PORT=1234
```

Run containers then join that network as well as their own, and the built-in
`preview-up` builds through `tcp://$BUILDKIT_HOST:$BUILDKIT_PORT`. The network must
exist before the scheduler dispatches. Once the variable is set, every dispatch (not
only previews) fails while the network is missing, with a socket-proxy
`403 Forbidden … administrative rules` error. Check first:

```bash
docker network inspect "$FACTORY_BUILD_NETWORK" --format '{{.Name}}'   # must print the name
```

## 5. Start the scheduler

```bash
cd DF
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml logs backlog-scheduler
```

Expected lines, in this order (other lines appear between them):

```
providers preflight: OK
Provisioned dispatch env file at /opt/dark-factory/.archon/.env from bind mount
WIP limits: in_progress=… in_review=… factory=…
[...] probe=image_ok image=…
Backlog scheduler started (poll every 60s)
[...] backlog=0 refined=0 in_progress=0/… in_review=0/… factory_running=0/… refine_running=0/… skip=nothing_to_do main_red=false graphql=…
```

| You see | Meaning |
|---------|---------|
| `WARNING: /workspace/project/.archon/.env not found` | `PROJECT_DIR` is wrong, or step 4's second file is missing |
| `ERROR: GH_TOKEN is not set. Add it to .archon/.env` | In the scheduler log: `instance.env` wasn't passed, so check `--env-file`. The message's `.archon/.env` hint is written for run containers; the scheduler reads `instance.env` |
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

## 7. Reviewing a factory PR: approve by commenting on the issue

When a ticket reaches **In Review**, review the draft PR, then **reply on the issue**
(not the PR):

| You comment | The scheduler does |
|-------------|--------------------|
| An approval: "looks good", "ship it", "approved", "LGTM", 👍 | dispatches **Close**: merges the PR, moves the ticket to **Done**, and cleans up |
| A request or question: "rename X", "fix the tests", "why Y?" | dispatches **Continue**: a run addresses the feedback on the same PR |

A classifier reads each new human comment, so plain language works.

**Do not merge the PR yourself in the GitHub UI.** Nothing then moves the ticket: it
stays **In Review** indefinitely. The board's built-in "Item closed" workflow is off,
and the factory only closes tickets through its Close run. If you already merged by
hand, set the ticket's Status to **Done** on the board.

## 8. When a ticket is Blocked

A gate that blocks is working as designed: it posts a "Blocked" comment with its
findings and adds `needs-discussion`. The run log will still say
`Dark factory failed (exit 1)` and an extra "Run — Failed" comment appears; that
reporting is tracked in #443. To resume:

1. Comment on the issue with the human decision.
2. Remove `needs-discussion`.
3. Leave the ticket in **Blocked**.

On its next poll the scheduler retries Blocked tickets that no longer carry a skip
label. If the ticket's `feat/issue-N-…` branch exists on the remote, or a PR is open
for it, the dispatch is **Continue**: the run checks out the existing branch and keeps
working on the same PR. If neither exists, the dispatch is **Fix**, a new
implementation from the default branch.

**Do not move the ticket to Ready** when it already has a branch or PR. Ready always
dispatches **Fix**, which creates the branch afresh from the default branch; the
scheduler's Blocked loop exists precisely because that fresh branch collides with the
already-pushed one at push time (#371).

Retries are limited. Each Blocked retry counts against `scheduler.max_retries` in
`config/config.yaml` (default 3). At the limit, or when the same failure repeats on two
consecutive attempts, the circuit breaker comments on the ticket and re-applies
`needs-discussion`; fix the cause it names before removing the label again.

## 9. Operating notes

- **Several instances on one host share the local `:latest` image.** Pulling a new
  image for one instance changes the image the others' next runs use. Pin each
  instance to a digest in its `instance.env` and upgrade deliberately:

  ```bash
  docker image inspect ghcr.io/omniscient/dark-factory:latest --format '{{index .RepoDigests 0}}'
  # -> IMAGE_REF=ghcr.io/omniscient/dark-factory@sha256:…   then up -d --force-recreate
  ```

  The scheduler log's `probe=image_ok image=…@sha256:…` line confirms the pin. It
  covers the scheduler and every run it dispatches.
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

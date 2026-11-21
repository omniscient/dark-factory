# Side-Effect Shim — Close Alias, Env-Config and Transport-Override Bypasses

**Issue:** #553
**Revised:** 2026-11-21 (operator amendment after spec gate review)

## Overview / Problem statement

`scripts/shims/git` and `scripts/shims/gh` PATH-shadow `git`/`gh` and classify every
invocation's verb from argv to enforce the side-effect level (1-5) defined in
`scripts/factory_core/side_effect.py`. The classification reads only the literal verb
token at the computed position (after skipping global options); it never asks whether
that token is itself a stand-in for a different verb. Confirmed in this refine pass
(real git 2.53.0 / gh 2.105.0, this checkout's own `scripts/shims/*`):

1. **Headline repro (issue body).** `git -c alias.p=push p origin HEAD` at level 2
   (`push_scope=denied`) reaches real git and pushes. The shim's own global-option
   skip-loop correctly skips `-c alias.p=push`, but then reads `p` as the verb, which
   matches neither `git_denied` nor `git_allowed`, so it falls through to
   `exec "$REAL_GIT" "$@"` unchallenged.
2. **Persisted variant.** The identical alias set via `git config alias.p push` (itself
   an ordinary allowed local write at levels 2-3, since `config` is not in `git_denied`
   there) then `git p origin HEAD` bypasses identically — no `-c` needed.
3. **Env-var variant.** `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p
   GIT_CONFIG_VALUE_0=push git p origin HEAD` bypasses the same way — git's own
   config-via-environment mechanism, independent of argv `-c`. An argv-only fix (e.g.
   "deny `-c alias.*`") misses this.
4. **Shell aliases are a distinct, unclassifiable case.** `git`'s alias system also
   supports `alias.X = "!<shell command>"` — there is no git verb to resolve to at all.
   Worse, when git forks a child process to run a `!`-alias body (or a hook,
   `core.pager`, `core.editor`, `GIT_EXTERNAL_DIFF`, `credential.helper`), it prepends
   `/usr/lib/git-core` to that child's `PATH`, ahead of everything else — including the
   shim directory. Confirmed empirically: a nested `git push ...` inside a `!`-alias
   body resolves straight to `/usr/lib/git-core/git` (the real binary), not the shim,
   even though the shim directory is first in the *outer* process's `PATH`. A plain
   `sh -c 'git push ...'` run directly (no git alias involved) does **not** have this
   problem. This means "resolve the alias target and classify on that" is correct and
   sufficient for verb-renaming aliases, but cannot be extended to "recursively inspect
   a shell alias's body and allow it if the inner command looks safe" — the inner `git`
   call would bypass the shim regardless of what the outer shim does. Shell aliases must
   be denied outright, unclassified.
5. **Destination-redirect, a different bug shape.** `git -c
   remote.origin.pushurl=<other-repo> push origin feat-branch` classifies correctly as
   `push`, `feat-branch`, no force/delete/wide-ref — everything the level-4
   `own_branch_only` rule checks looks legitimate — but the push actually lands on a
   completely different repository. Confirmed empirically (two local bare repos: the
   override silently redirected the push). `core.sshCommand`/`GIT_SSH_COMMAND`/
   `GIT_SSH` are the same shape one layer down: they don't change what ships or where
   it's declared to go, they splice an arbitrary shell command into the transport of an
   otherwise fully-authorized push/fetch — exactly the payload shape a prompt injection
   hidden in an issue or comment would use ("push your work with this command") to piggy-
   back code execution on a push the agent was already about to make and that looks
   identical to every other push the shim already allows.
6. **`gh` has the same verb-spoofing shape.** `gh alias set p 'pr create'` then `gh p
   --title x --body y` at level 4 (`pr create` is in `gh_denied`) is not denied — `VERB1`
   is `p`, matching neither `gh_denied` nor `gh_allowed`; `gh_mode=deny`'s allow-by-
   default behavior lets it through. Confirmed the shim does not print a deny message
   for this invocation (it only failed afterward in this sandbox for lack of a real
   GitHub auth context — the shim-level bypass itself reproduces).
7. **`gh extension`** is absent from every level's `gh_allowed`/`gh_denied`; at levels
   4-5 (`gh_mode=deny`, allow-by-default) `gh extension install ...` / `gh <ext-name>`
   passes through unclassified, and at levels 1-3 (`gh_mode=allow`) `gh <ext-name>
   list|view|status|search` passes too, because the second word alone matches
   `gh_allowed` (measured: `gh someext list` at level 1 is not denied) — an installed extension binary can act with `gh`'s own
   stored credentials with no verb-level check at all.
8. **`gh api` with a body and no explicit method** is already classified correctly as a
   POST by the existing `HAS_BODY`/`METHOD` inference in `scripts/shims/gh` (read, not
   bypassed) — no bug, just missing a regression test tying it to this ticket.

Found by the spec gate review and measured against the origin/main shims in a throwaway
`ghcr.io/omniscient/dark-factory:latest` container (git 2.53.0, gh 2.105.0, local bare
repos as origin); all four are fixed by this ticket:

9. **Arguments inside an alias value.** `git -c alias.del='push --delete' del origin feat`
   at level 5 deleted the remote branch (never-list bypass); `alias.x='push --force'` and
   `alias.x='push origin'` + `HEAD:main` pushed at level 4. Resolving only the alias's
   first token to `push` would still not see `--delete`, `--force` or the remote.
10. **Alias values that are not a bare verb.** `alias.v='-p push'` and `alias.w='"push"'`
    both push at level 2. `git config --get` also exits 1 for an *invalid* key
    (`alias.a b`, `alias.--no-advice`), the same code as "not found", so exit 1 cannot
    mean "safe, classify as-is".
11. **External `git-<verb>` commands.** git runs a `git-foo` executable found on `PATH`
    in preference to an alias named `foo` (measured), and prepends `/usr/lib/git-core` to
    that child's `PATH`, so a nested `git push` inside it reaches real git (pushed at
    level 2).
12. **Unrecognised global options desync the verb.** `git --no-advice push origin HEAD`
    and `git --attr-source HEAD push origin HEAD` both push at level 2: the skip-loop in
    `scripts/shims/git` does not know those options, reads the option as the verb, and
    falls through. Same shape in gh: `gh -R o/r pr create` at level 4 is not denied.

This is a `gate_*`-adjacent change per CLAUDE.md's Hard limits: it must only tighten
existing enforcement, must not touch `scripts/factory_core/side_effect.py`'s level/profile
tables (per the issue's own Scope), and gets this human-reviewed spec before any
implementation.

## Requirements (from Q&A)

Full Q&A dialogue is in the pipeline comment on #553. Resolutions below are load-bearing.

1. **All eight items (1-8 above) are fixed in this one ticket**, except the general
   "git forks a child, child resolves real git via git-core PATH" class (hooks,
   `core.pager`, `core.editor`, `GIT_EXTERNAL_DIFF`, `credential.helper` each running a
   nested `git <verb>`), which is tracked in a follow-up ticket filed by the operator and
   only documented here (Requirement 8), not fixed. Items 9-12 are fixed here as well. The two shims are ~390 lines combined today and the
   fix stays well inside the repo's 400-line diff-size convention; splitting the eight
   items would leave a known, now-documented bypass open on a gate surface, against
   "must only tighten." The follow-up class is different in kind — closing it means
   controlling which hooks/helpers run inside the clone, not an argv/env classification
   problem, and is not an argv-only fix like the other eight.

2. **Verb-spoofing fix (items 1, 2, 3, 6, 9-12 — git and gh aliases, however set).** One
   resolution mechanism per tool, governed by one invariant.

   **Tighten-only invariant.** An invocation reaches the real binary only if (a) its
   literal argv passes today's classification, unchanged, AND (b) every expanded form
   passes that same classification. Resolution can add denials; it can never turn a deny
   into an allow. (Without (a), `git -c alias.l='log --output=f' l` at level 1 — denied
   today as the unlisted verb `l` — would resolve to the allowed verb `log` and pass.)

   - **git.** At the point where the shim would `exec "$REAL_GIT" "$@"` today (the
     literal form has already passed), at every level 1-5:
     1. `VERB1` (the token after the existing global-option skip-loop) must be empty
        (bare `git`, unchanged) or match `^[a-z][a-z0-9-]*$`; otherwise deny
        ("unrecognised option or verb syntax"). This closes item 12 without enumerating
        git's global options: any option the skip-loop does not know is denied instead of
        being read as a verb. Consequence: informational flags the loop does not list
        (`--html-path`, `--man-path`, `--info-path`, `--list-cmds=…`) are denied for phase
        agents; `--version`/`--help` keep their existing passthrough.
     2. The known-verb set is `"$REAL_GIT" --list-cmds=main` (169 commands on git 2.53.0
        in the image: the 146 builtins plus the programs in git's exec-path such as
        `submodule`, `subtree`, `http-push`, `filter-branch`, `mergetool`). If that
        command fails or prints nothing → deny ("verb resolution failed").
     3. `VERB1` in that set → no alias lookup. git never applies an alias over a command
        it can find, so `status`, `clone`, `push`, `commit`, `submodule` etc. cannot
        regress from alias noise, and `-c alias.status=push status` runs `status`.
     4. `VERB1` not in that set:
        - If it is in `"$REAL_GIT" --list-cmds=others` (a `git-<verb>` executable on
          `PATH`) → deny ("external git command"). git would run that executable, not
          the alias (item 11).
        - Else run `"$REAL_GIT" <the global options already parsed off argv> config --get
          alias.<VERB1>`, inheriting the shim's environment unchanged (this covers
          `GIT_CONFIG_COUNT`/`KEY_n`/`VALUE_n`, `GIT_CONFIG_PARAMETERS`,
          `GIT_CONFIG_GLOBAL`, `--config-env`, and persisted local/global/system config).
          Exit 1 → deny ("unknown verb"): the verb is neither a git command nor an alias
          (this replaces the earlier "classify as-is", which failed open). Any other
          non-zero exit → deny ("alias resolution failed").
        - Exit 0: a value starting with `!` → deny ("shell alias", Requirement 3). A value
          containing `"`, `'`, `\` or a newline → deny ("alias value needs shell-style
          parsing"): the shim does not re-implement git's own command-line splitting.
        - Otherwise the expanded argv is `<global options> <whitespace-separated tokens of
          the value> <the arguments that followed VERB1>`. Run the **entire**
          classification on the expanded argv — skip-loop, tables, push flag/remote/
          refspec parsing, level-1 write-form checks, Requirements 3 and 4, and this
          resolution step again for alias chains — and deny if any of it denies. Depth
          cap 5 → deny ("alias chain too deep").
     5. What is finally exec'd is still the untouched original argv.
   - **gh.** At every point where the shim would `exec "$REAL_GH" "$@"` today, at every
     level 1-5:
     1. `VERB1` must be empty or match `^[A-Za-z0-9][A-Za-z0-9_-]*$`; otherwise deny
        ("flag before the command"). This closes `gh -R o/r pr create` in the shim; it is
        not a table change.
     2. `"$REAL_GH" alias list` (reads config only; measured exit 0 with no token and no
        config dir). Failure → deny ("alias resolution failed"). If it has a line
        `VERB1: <expansion>`: deny when the expansion is empty or multi-line (`|`, `>`),
        starts with `!` (shell alias), or contains a quote, a backslash or `$` (positional
        placeholders); otherwise the expanded argv is `<tokens of the expansion> <the
        arguments that followed VERB1>` and the entire gh classification (including the
        `gh api` method inference and Requirement 5) runs on it, depth cap 5.
     3. gh refuses to define an alias over a core command (measured: "already a gh
        command or extension"), and the literal form is always classified, so an alias
        cannot loosen a core command either.
   - **Measured with a prototype of this requirement** (patched copies of both shims in a
     throwaway container, real pushes to local bare repos): every vector in items 1-3,
     6 and 9-12 is denied with no change on the remote; the 113 existing assertions of
     `tests/test_side_effect_shims.sh` pass; no legitimate call listed under "Legitimate
     usage" in the test plan is denied.

3. **Shell-alias and transport-override denial (items 4, 5) apply at every side-effect
   level 1-5, unconditionally — not scoped to where push itself is restricted.** This
   was the one point in Q&A that needed its own justification: levels 4-5 legitimately
   allow some pushes, so "deny this on a push" looks at first glance like changing what
   those levels permit. It is not, for two independent reasons confirmed this pass:
   - `side_effect.py`'s never-list (`_GIT_NEVER = ["push --delete", "push
     :refspec-delete"]`) belongs to the never-list that the comment above `_GH_NEVER` in
     that module calls "denied at EVERY level" — enforced through `push_scope` at levels
     1-4 and through `git_denied` at level 5. A `!`-alias whose
     body runs `git push --delete` subverts that existing rule via the git-core
     PATH-prepend finding (item 4); denying the outer alias is closing a bypass of a
     rule that already applies everywhere, not adding a new one.
   - No factory workflow uses `GIT_SSH*`/`sshCommand`/git aliases anywhere (confirmed:
     repo-wide grep across `scripts/`, `scheduler.sh`, `entrypoint.sh`, `workflows/`
     returns nothing beyond the shim/test files themselves) and the factory's own clone
     is always HTTPS (`entrypoint.sh:18`'s `REPO_URL="https://github.com/${FACTORY_REPO_SLUG}.git"`),
     so an SSH-transport override does no real work in any legitimate factory run.
     Denying it everywhere costs nothing and is simpler to review/test than a
     level-conditional rule.
   - Concretely: the shim checks for a resolved alias value beginning with `!`, and for
     `core.sshCommand`/`GIT_SSH_COMMAND`/`GIT_SSH` set from any source (`-c`,
     `--config-env`, env-config, or persisted config) independently of `push_scope` and level
     (once the literal form has passed, see Architecture) and denies unconditionally
     when found, on any verb that can open a transport (`push`, `fetch`,
     `pull`, `clone`, `ls-remote`, `remote update`, `submodule`). The deny message names
     the reason ("shell alias", "sshCommand override"), not a level, so it isn't mistaken
     for a level-policy change in a run's logs.

4. **Destination-redirect check (item 5's `pushurl`/`insteadOf` half) applies wherever
   `push_scope` is `own_branch_only` (level 4); it is a no-op at level 5.** At levels 1-3
   `push` is denied outright before this check is reached. `push_scope=unrestricted` at
   level 5 means the destination is not something that level restricts. Mechanism, after
   the existing own-branch checks have passed:
   - **Which remote.** The positional repository argument; else the `--repo` value (git
     uses `--repo` only when no positional repository is given); else
     `branch.<current>.pushRemote`; else `remote.pushDefault`; else
     `branch.<current>.remote`; else `origin`.
   - **Its push URLs.** `"$REAL_GIT" <same global options> remote get-url --push --all
     <remote>` — `--all` is required: without it git prints only the first of several
     `pushurl` values while a real push goes to all of them (measured). Every URL printed
     must match the baseline. This applies `insteadOf`/`pushInsteadOf` the same way the
     real push would.
   - **Literal destination.** If the argument is not a configured remote name
     (`get-url` fails), it is treated as a literal URL or path: it must itself match the
     baseline, and the push is denied if any `url.*.insteadOf`/`url.*.pushInsteadOf` is
     in effective config (`config --get-regexp`), because `get-url` cannot resolve
     rewrites for a literal.
   - **Primary baseline:** `https://github.com/${FACTORY_REPO_SLUG}` (mirrors
     `entrypoint.sh:18`'s `REPO_URL`; `FACTORY_REPO_SLUG` is `export`ed by
     `scripts/identity.sh`). Strip a trailing `/` and `.git` from both sides before
     comparing. Deny on mismatch or on resolution failure.
   - **Fallback when `FACTORY_REPO_SLUG` is unset:** the baseline is `remote.origin.url`
     read with none of the invocation's `-c`/`--config-env` options and with
     `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`
     unset (only `-C`/`--git-dir`/`--work-tree` are kept). This is a weaker guarantee —
     a persisted `git remote set-url origin` or a persisted `insteadOf` moves the
     baseline with it — and is documented as such next to the existing
     `FACTORY_RUN_BRANCH` degraded-guarantee comment in `scripts/shims/git`. (The earlier
     wording, "clean vs overridden resolution of the same remote", could not catch a
     literal URL, another remote name or a persisted `pushurl`; this wording does —
     measured.)

5. **`gh extension` (item 7) denied at levels 1-4; unchanged at level 5.** The check is
   evaluated at levels 1-4, not only at 4: at levels 1-3 an extension invoked as
   `gh <ext-name> list|view|status|search` passes today (item 7). In the shim, after the
   literal form has passed and at the same point as Requirement 2's gh resolution:
   - `extension install`, `extension upgrade`, `extension exec` → deny.
   - `VERB1` naming an installed extension (a line `gh <VERB1>` in `gh extension list`) →
     deny ("extension").
   - **Without a token.** `gh extension list` exits 4 when no token is in the environment
     and no login is stored (measured), although it only reads the local extensions
     directory. The shim therefore runs it with
     `GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-placeholder}}"` for that one subprocess
     (measured: exit 0, lists a locally installed extension, no token needed). If it still
     fails → deny ("extension resolution failed"). Result: with no token the shim neither
     denies everything nor fails open — legitimate calls pass the shim and reach gh's own
     auth error (measured: exit 4 from gh, no guard message). `gh alias list` needs no
     such handling.
   - These are added inside the shim, not to `side_effect.py`'s `gh_denied` table.
   Level 5 is unchanged — gh extension access there is no more dangerous than what
   level 5 already grants wholesale.

6. **`gh api` body-without-method (item 8): no code change, test-only.** Add a
   regression case to `tests/test_side_effect_shims.sh` pinning the existing behavior
   (`gh api ... -f field=x` with no `-X` denies as POST at a level where `api:non-GET` is
   denied) so a future change to that inference can't silently regress without this
   ticket's bypass-shape test suite catching it.

7. **Do not modify `scripts/factory_core/side_effect.py`.** Every fix above changes only
   what verb/destination/transport the shims compute and feed into the existing
   `git_denied`/`git_allowed`/`gh_denied`/`gh_allowed`/`push_scope` tables, or adds a
   shim-local check that sits in front of those tables (mirroring the shim's existing
   non-table checks: clustered push-flag expansion, `branch`-write detection, `gh api`
   method inference). The level/profile definitions themselves are untouched.

8. **Documentation.** `docs/adapter-authoring-guide.md`, section "Side-effect levels",
   states plainly that the shim is a PATH guard for a fallible agent, not a sandbox, and
   lists what stays out of its reach: calling the real binary by absolute path, unsetting
   `CLAUDECODE` or `FACTORY_SIDE_EFFECT_LEVEL`, direct HTTP with the token, and any
   process git itself spawns — hooks, `rebase --exec`, `core.editor`, `core.pager`,
   `bisect run`, `submodule foreach`, `credential.helper` — because git puts its exec-path
   ahead of the shim on that child's `PATH` (measured: `git rebase --exec 'git push …'`
   pushes at level 2, before and after this ticket).

9. **Operator decisions recorded at the spec gate** (the owner can revisit any of these
   at the plan gate):
   1. The nested-child class (Requirement 8's last item) is a documented residual in this
      ticket, not fixed here; it is tracked in a follow-up ticket filed by the operator.
   2. A final verb outside `"$REAL_GIT" --list-cmds=main` is denied at every level when
      the shim is active (Requirement 2). Consequence, stated plainly: external
      `git-<verb>` subcommands such as `git lfs` are denied for phase agents. None is
      installed in the image today (`--list-cmds=others` is empty), and a test pins that
      every git verb the factory's own commands and scripts use is in the known-verb set
      (see the test plan).
   3. gh level-4 gaps that live in `side_effect.py`'s tables (for example `gh workflow
      run` is not in level 4's `gh_denied`) are out of scope; follow-up ticket filed by
      the operator. This ticket does not edit `side_effect.py`.

## Architecture / Approach

Both shims keep their existing classification exactly as it is and gain a **resolution
pass that runs only after the literal form has passed** — at the point of today's final
`exec` — and that re-runs the whole classification on each expanded form:

```
git shim, in order:
  1. --version/--help passthrough (existing, unchanged)
  2. existing global-option skip-loop -> VERB1/VERB2/VERB_ARGS (unchanged)
  3. existing commit/tag/remote/push/send-pack classification (unchanged tables)
  4. NEW, inside push/own_branch_only, after the own-branch checks: push-destination
     check (Requirement 4)
  5. existing level-1 allow-list default and level-1 write-form/branch/remote checks
     (unchanged)
  6. NEW, every level: VERB1 syntax check; known-verb check (--list-cmds=main); for an
     unknown verb: external-command deny, alias lookup, "!"/quote deny, then steps 2-7
     again on the expanded argv (depth cap 5)
  7. NEW, every level, transport verbs only: core.sshCommand / GIT_SSH_COMMAND / GIT_SSH
     deny (Requirement 3)
  8. exec $REAL_GIT "$@" (the untouched original argv; resolution never rewrites what is
     exec'd, only what is classified)

gh shim, in order:
  1. --version/--help passthrough (existing, unchanged)
  2. gh persistent-flag skip-loop (existing, unchanged) -> VERB1/VERB2/TWO_WORD
  3. existing `gh api` method inference and gh_denied/gh_allowed classification
     (unchanged tables)
  4. NEW, every level, wherever step 3 would exec: VERB1 syntax check; `gh alias list`
     lookup, "!"/quote/"$" deny, then steps 2-5 again on the expanded argv (depth cap 5)
  5. NEW, levels 1-4: deny `extension install|upgrade|exec` and any VERB1 that is an
     installed extension (Requirement 5)
  6. exec $REAL_GH "$@" (unchanged)
```

Every new check fails closed (deny on subprocess error, unresolvable config, unknown
verb, or cap overrun) at every level. How the expanded form is re-classified is the
plan's choice; the prototype re-invoked the shim itself in a classify-only mode, which
guarantees that the literal and expanded forms go through identical code.

### Test plan

All tests live in `tests/test_side_effect_shims.sh`, which CI runs (it is listed in
`.github/workflows/ci.yml`). The new cases need real `git` and real `gh` binaries on the
runner; both are on GitHub's ubuntu runners and in the factory image. No test touches the
network or needs a token.

**Existing assertions.** The 113 existing assertions stay byte-identical and must all
still pass. Only the harness preamble and the two stubs change:

- The `$TMP/real/git` stub hands `--list-cmds=*`, `config …` and `remote get-url …`
  (found after skipping `-C`/`-c`/`--git-dir`/`--work-tree`/`--namespace`/`--config-env`)
  to the real git and echoes `REAL_GIT $*` for everything else, `symbolic-ref` included.
  The `$TMP/real/gh` stub hands `alias list` and `extension list` to the real gh and
  echoes `REAL_GH $*` otherwise. Without this, the echo-stubs would answer the new
  lookups with `REAL_GIT …` at exit 0 and every allow assertion would deny.
- The preamble creates a fixture repo (`git init`, `remote add origin
  https://github.com/o/r.git`), `cd`s into it, and exports `FACTORY_REPO_SLUG=o/r`,
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GH_CONFIG_DIR="$TMP/…"`,
  `XDG_DATA_HOME="$TMP/…"`, and unsets `GH_TOKEN`, `GITHUB_TOKEN`, `GIT_SSH`,
  `GIT_SSH_COMMAND`. This keeps the level-4 push assertions independent of the checkout
  the test happens to run from, and keeps `gh alias set` / a developer's global
  `core.sshCommand` out of the picture.
- Measured with the prototype: 113 of 113 existing assertions pass with exactly this
  stub and preamble change.

**New deny assertions assert the reason**, not only "a guard message and non-zero exit":
a helper takes a regex the deny message must match, so a case cannot pass because some
unrelated check denied it.

**New cases** (each was run against the prototype; expected result as listed):

- Alias sources at level 2, deny as `push`: `-c alias.p=push p`; persisted `git config
  alias.pp push` then `git pp`; `GIT_CONFIG_COUNT`/`KEY_0`/`VALUE_0`;
  `GIT_CONFIG_PARAMETERS`; `GIT_CONFIG_GLOBAL=<file>`; `--config-env=alias.ce=VAR`; a
  2-hop chain. A 6-hop chain at level 5 → deny ("too deep").
- Shell alias `!…` at levels 2 and 5 → deny ("shell alias").
- Arguments in the alias value: `alias.del='push --delete'` at level 5 → deny
  ("push --delete"); `alias.x='push --force'` at level 4 → deny; `alias.x='push origin'`
  + `HEAD:main` at level 4 → deny ("push (main)").
- Tighten-only: `-c alias.l=log l` and `-c alias.l='log --output=f' l` at level 1 → deny
  (as today).
- Non-bare values: `alias.v='-p push'` at level 2 → deny as `push`; `alias.w='"push"'`
  → deny ("shell-style parsing").
- Global options: `git --no-advice push`, `git --attr-source HEAD push` at level 2 and
  `git --no-advice status` at level 5 → deny ("unrecognised option"); `git -C .
  --git-dir=.git --no-pager push` at level 2 → deny as `push`; clustered `git push -fu
  origin feat` at level 4 → deny ("push --force").
- Unknown verb: `git frobnicate` at level 5 and `git lfs push origin feat` at level 2 →
  deny ("unknown verb"); `git submodule status` at level 5 → allow.
- Controls (allow): `-c alias.p=push p origin feat` and `-c alias.lg='log --oneline' lg`
  at level 5; `-c alias.status=push status` at level 5; `git -C . -c user.name=x commit
  -m x`, `git --no-pager -C . diff --name-only`, `git --git-dir=.git status` at level 2.
- Transport override: `-c core.sshCommand=x push` and `GIT_SSH_COMMAND=x git fetch` at
  level 5, `GIT_SSH=x git fetch` at level 1 → deny ("sshCommand"); plain `git push origin
  feat` at level 5 and `git fetch origin` at level 1 → allow.
- Destination at level 4 with `FACTORY_REPO_SLUG=o/r`: `push origin feat` and `push
  https://github.com/o/r.git feat` → allow; literal other URL, `-c
  remote.origin.pushurl=<other>`, `-c url.<other>.insteadOf=<origin>`, a second remote
  (`remote add evil` then `push evil feat`), two `pushurl` values (one other) → deny
  ("not the run's repository"); a literal baseline URL while an `insteadOf` is in config
  → deny ("URL rewrite"). The same `pushurl` override at level 5 → allow (no-op there).
  With `FACTORY_REPO_SLUG` unset (fallback): `push origin feat` → allow; `-c pushurl`,
  literal other URL, `push evil feat` → deny.
- gh, with no token in the environment: `gh alias set p 'pr create'` then `gh p` at
  level 4 → deny ("pr create"), at level 5 → allow; alias `xs: secret` then `gh xs list`
  at level 1 → deny; alias `il: issue list` then `gh il` at levels 1 and 3 → deny (as
  today, tighten-only), at level 4 → allow; a `--shell` alias at level 4 → deny;
  a locally installed extension `gh someext list` at level 1 and `gh someext` at level 4
  → deny ("extension"), at level 5 → allow; `gh extension install o/x` at level 4 → deny,
  at level 5 → allow; `gh -R o/r pr create` at level 4 → deny ("flag before");
  `gh api repos/o/r/issues -f title=x` at level 4 → deny ("api POST", the regression pin
  for item 8).
- Legitimate usage, no token (allow): `gh issue comment|view|create|edit` at level 3;
  `gh pr view`, `gh pr list`, `gh api repos/o/r` at level 1; `gh pr view` at level 4;
  `gh pr create`, `gh api graphql -f query=…` at level 5.
- Known-verb pin (operator decision 2): the test carries a pinned list of the git verbs
  the factory's `commands/*.md`, `refinement-skills/`, `scripts/`, `entrypoint.sh` and
  `workflows/*.yaml` invoke — `add apply branch cat-file checkout clean clone commit
  config diff diff-tree fetch for-each-ref log ls-files ls-remote merge merge-base mv pull
  push rebase remote reset rev-list rev-parse rm show stash status symbolic-ref
  update-index write-tree` — and asserts each is in `git --list-cmds=main`. The list is
  pinned, not derived by grep at test time: a grep over prose matches words such as
  "git reports" and would make the test flaky. The grep used to build it is recorded in
  a comment next to the list. Measured: none is missing on git 2.53.0.

The prototype run of the cases above: 50 git and 24 gh new assertions; all passed except
one case that was itself wrong (`push --repo <other> origin feat` is correctly allowed,
because git ignores `--repo` when a positional repository is given; the `--repo` case was
then measured with a real push and no positional repository, and is denied).

## Alternatives considered

1. **Deny any `-c`/`--config-env` override outright, regardless of key.** Simplest
   possible fix for the headline repro, but misses the persisted-config (item 2) and
   env-var (item 3) variants entirely — those use no `-c` at all — and would also break
   the shim's own existing legitimate uses of `-c` in its test fixtures and any future
   agent use of e.g. `-c color.ui=false` for harmless cosmetic reasons. Rejected: too
   narrow to close the actual bug class, and later found (via Q&A) to be exactly the kind
   of argv-only fix that misses the env-var variant.
2. **Maintain a static blocklist of "dangerous" config keys (`alias.*`, `core.sshCommand`,
   `remote.*.pushurl`, …) checked directly against argv `-c` flags, without resolving
   through real git.** Cheaper (no subprocess spawn per invocation) but reinvents git's
   own config-merge semantics (precedence across `-c`, env-config, global/system/local
   files) inside the shim, which is the exact kind of re-declaration CLAUDE.md and this
   module's own docstring warn against ("nothing re-declares the table side_effect.py
   owns" — the same principle extends to not re-declaring git's own config resolution).
   Rejected in favor of asking real git what it would actually do (`config --get`,
   `remote get-url --push`), which is correct by construction and already the pattern
   `scripts/shims/git` uses for `OWN_BRANCH` (`"$REAL_GIT" symbolic-ref --short HEAD`).
3. **Fix the git-core PATH-prepend issue structurally (e.g., have the shim symlink over
   `/usr/lib/git-core/git` too).** Would also close the shell-alias/hook/pager/editor/
   credential-helper class in one stroke, but modifies a system directory outside this
   ticket's `scripts/shims/git`+`scripts/shims/gh`+tests+docs scope, is image-build/
   Dockerfile territory (baking), and risks breaking git's own internal dispatch for
   every other builtin that also resolves through git-core. Rejected for this ticket;
   the narrower "deny unclassifiable shell aliases outright" achieves the same safety
   outcome for the one bypass shape in scope (item 4) without touching the system
   install, and the broader class is filed as a follow-up instead.
4. **Scope `core.sshCommand`/shell-alias denial to only levels where push is restricted
   (1-3), leaving levels 4-5 unaffected.** Initially the more "conservative, surgical"-
   looking option, but Q&A established this is backwards: level 5's own never-list already
   claims to apply "at every level," and leaving 4-5 unguarded is precisely what lets a
   prompt-injection payload hide inside an otherwise-authorized push. Rejected.

## Open questions (non-blocking)

- **The general "git-spawned children run real git, not the shim" class** (hooks,
  `rebase --exec`, `core.editor`, `core.pager`, `bisect run`, `submodule foreach`,
  `credential.helper`) stays open after this ticket and is documented as a residual
  (Requirements 8 and 9.1). Closing it means controlling what git may execute inside the
  clone, or putting a guard in front of git's exec-path in the image — a different,
  larger mechanism than argv/env classification. It is tracked in a follow-up ticket
  filed by the operator. External `git-<verb>` commands, which belonged to this class,
  are closed here by Requirement 2.
- **`gh` alias chaining depth.** gh does not appear to support multi-hop alias chains the
  way git does in ordinary use; the spec applies the same depth-5 cap for implementation
  consistency, but this is a defensive bound rather than something `gh` is known to need.
- **Performance.** Each git/gh invocation at a restricted level now pays for one or more
  extra `config --get`/`alias list`/`remote get-url` subprocess calls. Expected to be
  negligible next to the Claude Code round-trip costs already dominating a phase agent's
  wall-clock, and measured with the prototype in the image: `git status` through the shim
  takes about 65 ms in total, and a `gh --version` plus `gh issue view` pair about
  260 ms.

## Assumptions (flagged)

- `FACTORY_REPO_SLUG` reaches a phase agent's Bash-tool subprocess the same way
  `CLAUDECODE`/`FACTORY_SIDE_EFFECT_LEVEL` do (both already relied upon by the existing
  shim). This was confirmed by reading `scripts/identity.sh`'s `export
  FACTORY_REPO_SLUG=...` and its `source`-ing at the top of `entrypoint.sh`, before any
  `claude -p`/Archon session starts — not by an empirical probe inside a live run
  container (mirroring how the existing `FACTORY_RUN_BRANCH` degraded-guarantee comment
  in `scripts/shims/git` already documents its own un-probed assumption).
- No factory workflow sets `GIT_SSH*`, `core.sshCommand`, or any `alias.*` config today
  (confirmed by a repo-wide grep this pass returning nothing outside the shim/test
  files) — the unconditional-at-every-level denial in Requirement 3 assumes this remains
  true; if a legitimate future use emerges, it would need its own carve-out, which is a
  gate change and so would need its own reviewed ticket per CLAUDE.md, not a quiet
  loosening of this ticket's denial.
- git's own `--list-cmds=main` output (169 commands on git 2.53.0 in the image) is the
  known-verb set. A git upgrade can add or remove commands; a verb that disappears from
  the set becomes denied (fail closed), and the known-verb pin in the test plan fails in
  CI if that ever affects a verb the factory uses.

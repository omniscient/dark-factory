# Side-Effect Shim — Close Alias, Env-Config and Transport-Override Bypasses

**Issue:** #553

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
   2-5 (`gh_mode=deny`, allow-by-default) `gh extension install ...` / `gh <ext-name>`
   passes through unclassified — an installed extension binary can act with `gh`'s own
   stored credentials with no verb-level check at all.
8. **`gh api` with a body and no explicit method** is already classified correctly as a
   POST by the existing `HAS_BODY`/`METHOD` inference in `scripts/shims/gh` (read, not
   bypassed) — no bug, just missing a regression test tying it to this ticket.

This is a `gate_*`-adjacent change per CLAUDE.md's Hard limits: it must only tighten
existing enforcement, must not touch `scripts/factory_core/side_effect.py`'s level/profile
tables (per the issue's own Scope), and gets this human-reviewed spec before any
implementation.

## Requirements (from Q&A)

Full Q&A dialogue is in the pipeline comment on #553. Resolutions below are load-bearing.

1. **All eight items (1-8 above) are fixed in this one ticket**, except the general
   "git forks a child, child resolves real git via git-core PATH" class (hooks,
   `core.pager`, `core.editor`, `GIT_EXTERNAL_DIFF`, `credential.helper` each running a
   nested `git <verb>`), which is filed as a separate follow-up (see Open questions) and
   only documented here, not fixed. The two shims are ~390 lines combined today and the
   fix stays well inside the repo's 400-line diff-size convention; splitting the eight
   items would leave a known, now-documented bypass open on a gate surface, against
   "must only tighten." The follow-up class is different in kind — closing it means
   controlling which hooks/helpers run inside the clone, not an argv/env classification
   problem, and is not an argv-only fix like the other eight.

2. **Verb-spoofing fix (items 1, 2, 3, 6 — git and gh aliases, however set).** One
   resolution mechanism per tool, not per bypass variant:
   - **git.** Before classifying `VERB1`, check whether it is a real git builtin via
     `"$REAL_GIT" --list-cmds=builtins` (git itself never applies an alias that shadows a
     builtin, so builtins skip resolution entirely — `status`, `clone`, `init`, `push`,
     `commit`, etc. never depend on the lookup below and cannot regress from it). For
     every other `VERB1`, run `"$REAL_GIT" <the same -C/-c/--git-dir/--config-env
     overrides already parsed off argv> config --get alias.<VERB1>`, letting it inherit
     the shim's own ambient environment unchanged (this alone covers the env-var variant
     in item 3, since `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` are
     already in that environment, and covers the persisted variant in item 2, since
     `config --get` reads `.git/config` and global/system config the same way):
     - Exit 1 (key not found) → not an alias; classify `VERB1` as-is.
     - Exit 0 → resolve the value. A value starting with `!` → deny outright (item 4,
       see Requirement 3) regardless of level. Otherwise take the first whitespace-
       separated token of the value as the new candidate verb and repeat, up to a depth
       cap of 5 (arbitrary but reasonable — real alias chains observed in practice are
       1-2 deep; git's own cycle detector catches pure self-reference but not depth, so
       the cap is this shim's own safety bound). Hitting the cap denies ("alias chain too
       deep").
     - Any other non-zero exit (3 = bad config file, 128 = fatal) → deny ("alias
       resolution failed"); real git would refuse to run on a broken config anyway, so
       this loses no legitimate invocation.
     - Verified empirically this pass: `config --get` exits 1 cleanly even with no `.git`
       directory present (e.g. before `clone`/`init`), so global/system `~/.gitconfig`
       aliases are still covered and pre-repo verbs need no special-casing beyond the
       builtin check above.
     - Classify the **final resolved verb** against the existing `git_denied`/
       `git_allowed`/`push_scope` logic exactly as today — no change to those tables.
   - **gh.** Same shape: resolve `VERB1` against `gh alias list` (or equivalently check
     whether it names a defined alias) before falling back to literal classification;
     apply the same depth cap and fail-closed rule for consistency, even though gh
     aliases are not transitively chained the way git's are in practice.

3. **Shell-alias and transport-override denial (items 4, 5) apply at every side-effect
   level 1-5, unconditionally — not scoped to where push itself is restricted.** This
   was the one point in Q&A that needed its own justification: levels 4-5 legitimately
   allow some pushes, so "deny this on a push" looks at first glance like changing what
   those levels permit. It is not, for two independent reasons confirmed this pass:
   - `side_effect.py`'s never-list (`_GIT_NEVER = ["push --delete", "push
     :refspec-delete"]`) is already, by the module's own docstring, "denied at EVERY
     level and checked before any allow-list" — including level 5. A `!`-alias whose
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
     `--config-env`, env-config, or persisted config) **before** any `push_scope`/level
     branching — the same position the never-list check already occupies — and denies
     unconditionally when found, on any verb that can open a transport (`push`, `fetch`,
     `pull`, `clone`, `ls-remote`, `remote update`, `submodule`). The deny message names
     the reason ("shell alias", "sshCommand override"), not a level, so it isn't mistaken
     for a level-policy change in a run's logs.

4. **Destination-redirect check (item 5's `pushurl`/`insteadOf` half) applies at levels
   1-4 only; it is a no-op at level 5.** `push_scope=unrestricted` at level 5 means the
   destination is not something that level restricts in the first place — unlike the
   shell-alias/sshCommand case, a redirected push does not fork a nested `git` process
   and so never reaches the never-list either; there is nothing to close at level 5 for
   this specific check. Mechanism, on the `push` verb wherever `push_scope` is `denied`
   or `own_branch_only`:
   - Resolve the remote-to-push against (in order): the positional remote/`--repo` value
     (if it is itself a literal URL, compare it directly); else `branch.<current>.
     pushRemote`; else `remote.pushDefault`; else `branch.<current>.remote`; else
     `origin`.
   - Resolve its actual push destination via `"$REAL_GIT" <same global opts>
     remote get-url --push <remote>` (this applies `insteadOf`/`pushInsteadOf`
     rewrites the same way the real push would).
   - **Primary baseline:** compare against `https://github.com/${FACTORY_REPO_SLUG}.git`
     (mirrors `entrypoint.sh:18`'s own `REPO_URL` construction; `FACTORY_REPO_SLUG` is
     `export`ed by `scripts/identity.sh`, sourced at the top of `entrypoint.sh`, so it
     propagates to a phase agent's Bash-tool subprocess the same way
     `CLAUDECODE`/`FACTORY_SIDE_EFFECT_LEVEL` already do — same propagation class the
     existing `FACTORY_RUN_BRANCH` comment in `scripts/shims/git` already relies on).
     Strip a trailing `.git`/`/` from both sides before comparing. Deny on mismatch or on
     resolution failure.
   - **Fallback when `FACTORY_REPO_SLUG` is unset:** compare the remote's "clean"
     resolution (re-run `remote get-url --push` with none of the invocation's `-c`/
     `--config-env`/env-config overrides applied) against the overridden resolution; deny
     on any difference. This is a weaker guarantee (it only proves "this invocation didn't
     change the answer," not "the answer is the factory's own repo") — document it
     plainly next to the existing `FACTORY_RUN_BRANCH` degraded-guarantee comment in
     `scripts/shims/git`, the same way that comment already documents its own v1
     degradation.

5. **`gh extension` (item 7) denied at levels 1-4; unchanged at level 5.** Levels 1-3
   already deny it today as an unlisted verb under `gh_mode=allow`'s deny-by-default —
   no change needed there; this only closes level 4 (`gh_mode=deny`, allow-by-default),
   where `extension install`/`extension upgrade`/`extension exec`, and any `VERB1` that
   resolves to an already-installed extension name (checked via `gh extension list`,
   same resolve-before-classify shape as Requirement 2), are added to that level's
   effective deny set inside the shim (not to `side_effect.py`'s `gh_denied` table — the
   issue's scope excludes editing that file; the shim adds this check the same way it
   already adds the `api`-method-inference and `branch`-write-detection checks beyond
   the flat tables). Level 5 is unchanged — gh extension access there is no more
   dangerous than what level 5 already grants wholesale.

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

## Architecture / Approach

Both shims gain a **verb-resolution pass** that runs immediately after the existing
global-option skip-loop (which already isolates `VERB1`/`VERB2` and the same `-C`/`-c`/
`--git-dir`/`--config-env` style overrides for git, `-R`/`--repo` for gh) and before any
existing `case "$VERB1"` classification:

```
git shim, in order:
  1. --version/--help passthrough (existing, unchanged)
  2. is VERB1 a real git builtin? (--list-cmds=builtins) -> skip to classification
  3. else: resolve alias chain (config --get alias.<verb>, same -c/--git-dir overrides,
     depth-capped) -> deny on "!" value, deny on resolution error, deny on cap -> loop
     with resolved verb as new VERB1
  4. shell-alias / sshCommand / GIT_SSH* checks (new; every level, before push_scope)
  5. existing commit/tag/remote/push/send-pack classification (unchanged tables)
  6. NEW: push-destination check (levels 1-4 only, push verb only)
  7. existing level-1 allow-list fail-closed default (unchanged)
  8. existing level-1 write-form/branch/remote advisory checks (unchanged)
  9. exec $REAL_GIT "$@" (unchanged — the untouched original argv, same as today;
     resolution never rewrites what's actually exec'd, only what's classified)

gh shim, in order:
  1. --version/--help passthrough (existing, unchanged)
  2. gh persistent-flag skip-loop (existing, unchanged) -> VERB1/VERB2/TWO_WORD
  3. NEW: resolve VERB1 against `gh alias list` (depth-capped, fail-closed on error)
  4. NEW: deny VERB1/TWO_WORD matching "extension install|upgrade|exec", or VERB1
     resolving to an installed extension name, at levels 1-4 (no-op at 1-3, where
     it's already denied by allow-mode; the real fix is at 4)
  5. existing `gh api` method-inference classification (unchanged)
  6. existing gh_denied/gh_allowed classification on the resolved verb (unchanged tables)
  7. exec $REAL_GH "$@" (unchanged)
```

Every new check fails closed (deny on subprocess error, unresolvable config, or cap
overrun) consistent with the shim's existing R5 fail-closed convention at level 1, now
extended to the new checks at every level since they stand in front of the never-list
rather than inside a single level's table.

### Test plan

`tests/test_side_effect_shims.sh` gains one new section per fixture class, following the
file's existing flat `check <allow|deny> <cmd...>` harness and `FACTORY_SIDE_EFFECT_LEVEL=N
check ...` convention:

- A real (not stubbed) temporary git repo fixture, since alias resolution needs to run
  real `git config`/`git alias`/`git remote` subcommands to be meaningful — the existing
  harness's `$TMP/real/git` echo-stub cannot exercise alias resolution at all. This adds
  a `mktemp -d` repo (and a second bare "remote" dir) to the existing `$TMP` fixture,
  analogous to how `tests/test_deps_fixer.py`'s fixture pattern was described in the
  #484 spec, but git-native and bash.
- Verb-spoofing cases at levels 1-5: `-c alias.p=push`, persisted `git config alias.p
  push`, `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` variant, a 2-hop
  alias chain, a 6-hop chain (cap-exceeded deny), each expected `deny` where `push`
  itself would deny, `allow` where it wouldn't (e.g. level 5's non-delete pushes).
- `gh alias set p 'pr create'` then `gh p` at level 4 → deny; at level 5 → allow
  (matching bare `gh pr create`'s existing level-5 behavior).
- Shell-alias cases at every level 1-5: `alias.x = "!echo hi"` → deny; a control case
  confirming an ordinary allowed verb at the same level still passes (proving the new
  check doesn't over-deny).
- `core.sshCommand`/`GIT_SSH_COMMAND`/`GIT_SSH` cases at levels 1-5 on `push`/`fetch`,
  plus a control plain push at level 5 to pin "a normal level-5 push is unaffected".
- Destination-redirect cases at levels 1, 4 (own_branch_only) via `-c
  remote.origin.pushurl=<other>` and `-c url.<other>.insteadOf=<origin-url>`; a control
  case confirming an ordinary own-branch push with no override still allows at level 4.
- `gh extension install <name>` / `gh <installed-ext-name>` at level 4 → deny; at level 1
  → deny (already, via allow-mode default — assert this to lock in the "no regression"
  half); at level 5 → allow.
- `gh api repos/o/r/issues -f title=x` (no `-X`) at a level where `api:non-GET` is denied
  → deny (regression pin for item 8, no behavior change).
- Builtin-skip control: `git status`, `git push origin x` (level 5), `git clone <url>`
  (no level var set, passthrough case) all still pass with zero alias-lookup subprocess
  calls needed for correctness (the fixture doesn't need to assert zero-calls
  mechanically, just that these still behave correctly with the real binary and a fixture
  repo that happens to also define unrelated aliases, to prove builtin verbs are immune
  to alias noise).

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

- **Follow-up ticket: the general "git-spawned children run real git, not the shim"
  class.** `/usr/lib/git-core` ahead of the shim in PATH for any process git itself forks
  (hooks under `.git/hooks`/`core.hooksPath`, `core.pager`, `core.editor`,
  `GIT_EXTERNAL_DIFF`, `credential.helper`) means a nested `git <verb>` from any of those
  bypasses the shim regardless of what this ticket does to argv/alias/config
  classification. This ticket denies the one concrete instance of it that's cheaply
  closeable (shell aliases, since nothing legitimate needs one), and documents the rest
  as a known limitation (see Requirement/doc update below) rather than fixing it, since
  closing it for real means controlling which hooks/helpers are allowed to execute inside
  the clone at all — a materially different, larger mechanism than this ticket's argv/env
  classification fix. Should be filed as its own ticket for a human to scope; this spec
  does not create that ticket (ticket-filing from the refine phase comment channel is
  outside what this command does — see the pipeline comment's Next Steps for how a human
  or the scheduler's own flow picks this up).
- **`gh` alias chaining depth.** gh does not appear to support multi-hop alias chains the
  way git does in ordinary use; the spec applies the same depth-5 cap for implementation
  consistency, but this is a defensive bound rather than something `gh` is known to need.
- **Performance.** Each git/gh invocation at a restricted level now pays for one or more
  extra `config --get`/`alias list`/`remote get-url` subprocess calls. Expected to be
  negligible next to the Claude Code round-trip costs already dominating a phase agent's
  wall-clock, but not separately benchmarked in this spec.

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
- git's own `--list-cmds=builtins` output (146 commands on git 2.53.0 in this sandbox) is
  assumed stable enough across the factory image's pinned git version to use as the
  resolution-skip condition; a future git upgrade changing that list is expected to only
  ever *add* builtins (making more verbs skip resolution, never fewer), which is
  tightening-neutral for this gate.

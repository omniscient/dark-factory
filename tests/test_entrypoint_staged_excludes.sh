#!/usr/bin/env bash
# #472: every file entrypoint.sh stages into the target clone at run time must be
# registered in .git/info/exclude via _exclude_in_clone, so `git status` in the clone
# stays clean. An untracked dark-factory/ (seed/ + docker-compose.preview.yml were copied
# without an exclude) made MarketHawk refine agents treat the phase command as a
# dropped-in harness and end their first turn (3 of 5 attempts on markethawk #872).
# Run: bash tests/test_entrypoint_staged_excludes.sh
set -euo pipefail
ep="$(cd "$(dirname "$0")" && pwd)/../entrypoint.sh"
fail=0
need() { grep -qF "_exclude_in_clone \"$1\"" "$ep" || { echo "FAIL: no _exclude_in_clone for $1"; fail=1; }; }
need "dark-factory/scripts/"
need "dark-factory/seed/"
need "dark-factory/docker-compose.preview.yml"
need ".archon/workflows/"
need ".archon/commands/"
need ".claude/settings.local.json"

# Functional: replay the helper against a scratch clone and assert git sees nothing.
tmp=$(mktemp -d /tmp/ep-excl-XXXXXX); trap 'rm -rf "$tmp"' EXIT
git -C "$tmp" init -q
git -C "$tmp" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
CLONE_DIR="$tmp"
eval "$(sed -n '/^_exclude_in_clone() {/,/^}/p' "$ep")"
mkdir -p "$tmp/dark-factory/scripts" "$tmp/dark-factory/seed" "$tmp/.claude"
: > "$tmp/dark-factory/scripts/x.py"; : > "$tmp/dark-factory/seed/s.json"
: > "$tmp/dark-factory/docker-compose.preview.yml"; : > "$tmp/.claude/settings.local.json"
for r in "dark-factory/scripts/" "dark-factory/seed/" "dark-factory/docker-compose.preview.yml" ".claude/settings.local.json"; do _exclude_in_clone "$r"; done
st=$(git -C "$tmp" status --porcelain)
[ -z "$st" ] || { echo "FAIL: staged files still visible to git:"; echo "$st"; fail=1; }
[ "$fail" = 0 ] && echo "PASS: all run-time staged files are git-excluded in the clone"
exit $fail

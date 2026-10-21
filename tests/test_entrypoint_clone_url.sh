#!/usr/bin/env bash
# Guards the 2026-10-21 push regression: entrypoint.sh must clone from a plain
# https://github.com/<slug>.git URL (no token as URL userinfo) and must install gh as
# the git credential helper before the clone. With the token in the URL, git drops it
# when it rewrites the base URL after the first request ("warning: redirecting to ...")
# and every later `git push` dies with "could not read Username" while the clone and
# all fetches of a readable repo still succeed -- refine-push/plan-push then apply the
# gate label with nothing on the remote (see #473).
# Run: bash tests/test_entrypoint_clone_url.sh
set -euo pipefail
ep="$(cd "$(dirname "$0")" && pwd)/../entrypoint.sh"

url_ln=$(grep -n '^REPO_URL=' "$ep" | head -1 | cut -d: -f1)
[ -n "$url_ln" ] || { echo "FAIL: REPO_URL assignment not found"; exit 1; }
url_line=$(sed -n "${url_ln}p" "$ep")
case "$url_line" in
  *'@github.com'*|*'GH_TOKEN'*) echo "FAIL: REPO_URL embeds credentials: $url_line"; exit 1 ;;
esac
[ "$url_line" = 'REPO_URL="https://github.com/${FACTORY_REPO_SLUG}.git"' ] \
  || { echo "FAIL: unexpected REPO_URL form: $url_line"; exit 1; }

setup_ln=$(grep -n '^gh auth setup-git' "$ep" | head -1 | cut -d: -f1)
[ -n "$setup_ln" ] || { echo "FAIL: entrypoint does not run 'gh auth setup-git'"; exit 1; }
clone_ln=$(grep -n '^git clone "\$REPO_URL"' "$ep" | head -1 | cut -d: -f1)
[ -n "$clone_ln" ] || { echo "FAIL: git clone of REPO_URL not found"; exit 1; }
[ "$setup_ln" -lt "$clone_ln" ] \
  || { echo "FAIL: gh auth setup-git ($setup_ln) is not before git clone ($clone_ln)"; exit 1; }

# The helper install must run only in a real container start, i.e. after the
# ENTRYPOINT_SOURCE_ONLY early return, so sourcing tests never touch ~/.gitconfig.
src_only_ln=$(grep -n 'ENTRYPOINT_SOURCE_ONLY:-0}" = "1" \] && return 0' "$ep" | head -1 | cut -d: -f1)
[ -n "$src_only_ln" ] || { echo "FAIL: ENTRYPOINT_SOURCE_ONLY early return not found"; exit 1; }
[ "$src_only_ln" -lt "$setup_ln" ] \
  || { echo "FAIL: gh auth setup-git ($setup_ln) runs before the ENTRYPOINT_SOURCE_ONLY return ($src_only_ln)"; exit 1; }

echo "PASS: entrypoint clones from a plain URL and installs the gh credential helper before cloning"

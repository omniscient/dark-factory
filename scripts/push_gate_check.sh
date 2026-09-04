#!/usr/bin/env bash
# Determine whether a committed spec/plan artifact for an issue exists on commits this
# branch has made beyond main. Used by refine-push/plan-push-and-advance
# (workflows/archon-dark-factory.yaml) to gate the push+label step on the artifact
# actually existing, rather than on the upstream command node merely being reported
# "completed" — a killed/parked agent can be misreported as completed (#212).
#
# Usage: push_gate_check.sh <artifact-prefix> <issue-number>
#   <artifact-prefix>  path prefix to search, e.g. "docs/superpowers/specs/"
#   <issue-number>     issue number to match via "#<issue-number>" in file content, or
#                       "<issue-number>" delimited by non-digits in the filename (e.g.
#                       "...issue-212-...md") — a correctly committed artifact that only
#                       names the issue in its filename must still be detected. As a
#                       second pass (#382), an artifact whose only issue-number
#                       reference is a commit subject on this branch is also detected,
#                       but only when that specific commit touched the reported file
#                       under the artifact prefix — never a global "any commit
#                       mentions the number" fallback (see #212 in the pass-2 code
#                       comment below for why).
#
# Stdout: path of the first matching committed file, or nothing if none found.
# Exit: always 0 — "no artifact" is a valid outcome for the caller to branch on, not a
# script error. `pipefail` is enabled below (harmless here: no `set -e`, and the
# trailing `exit 0` is unconditional), so a `grep -l` finding no match still leaves the
# script printing the correct (empty) result and exiting 0.
#
# Intentionally uses `origin/main...HEAD` (merge-base three-dot) for the file-list diff
# below, NOT the two-dot `origin/main..HEAD` form used by push-and-pr's OOS
# content-identity check (memory: codebase-patterns.md, issue #250) — that two-dot form
# answers "does this file's content differ from main's current tip", a different question
# from "which files did this branch touch since it forked", which is what this check
# needs. Uses `origin/main` (not local `main`) to match scripts/oos_excise.sh and
# scripts/load_memory_context.sh — a clone config with no local `main` ref must not make
# this check fail closed.
set -uo pipefail

ARTIFACT_PREFIX="${1:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"
ISSUE_NUM="${2:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"

# Guard against a non-numeric issue number reaching the grep regex below (e.g. a
# stringified "null" from a bad tracker lookup) — fail closed with an empty result
# rather than let regex metacharacters silently alter matching.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    exit 0
    ;;
esac

HAS_COMMITS=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [ "$HAS_COMMITS" -gt 0 ]; then
  # Capture the three-dot candidate list once so both passes walk the same ordered
  # set — this is the structural fail-closed invariant: nothing is ever printed that
  # isn't a member of this list.
  _candidates=()
  # NUL-delimited iteration (via `git diff -z` + `read -d ''`) so a committed path
  # containing whitespace is handled as a single filename, not split across args.
  while IFS= read -r -d '' _file; do
    _candidates+=("$_file")
  done < <(git diff -z --name-only origin/main...HEAD -- "$ARTIFACT_PREFIX" 2>/dev/null)

  # Pass 1 (unchanged): filename- or content-delimited issue number match.
  for _file in ${_candidates[@]+"${_candidates[@]}"}; do
    _base=$(basename -- "$_file")
    # ISSUE_NUM is validated numeric-only above, so it is safe to interpolate directly
    # into these regexes (no metacharacter/injection risk from a malformed value).
    if [[ "$_base" =~ (^|[^0-9])${ISSUE_NUM}([^0-9]|$) ]] \
      || grep -Eq "#${ISSUE_NUM}\\b" -- "$_file" 2>/dev/null; then
      printf '%s\n' "$_file"
      exit 0
    fi
  done

  # Pass 2 (#382): only reached when pass 1 found nothing. A commit-subject match
  # may associate a file only if that same commit touched it under the artifact
  # prefix — per-commit association, never a global "any commit on the branch
  # mentions #<num>" fallback, which would risk mis-associating an unrelated file
  # (e.g. a "memory: lessons from issue #N" side commit) — the #212 failure class
  # this check exists to prevent.
  declare -A _assoc=()
  while IFS= read -r _sha; do
    _subj=$(git show -s --format=%s "$_sha")
    if [[ "$_subj" =~ \#${ISSUE_NUM}([^0-9]|$) ]]; then
      # NUL-delimited iteration (via `git diff-tree -z` + `read -d ''`) so a touched
      # path containing whitespace is handled as a single filename, not split across args.
      while IFS= read -r -d '' _touched; do
        if git cat-file -e "HEAD:$_touched" 2>/dev/null; then
          _assoc["$_touched"]=1
          echo "push_gate_check: candidate association $_touched via commit subject $_sha" >&2
        fi
      done < <(git diff-tree --no-commit-id -r -z --name-only "$_sha" -- "$ARTIFACT_PREFIX" 2>/dev/null)
    fi
  done < <(git log --format=%H origin/main..HEAD 2>/dev/null)

  for _file in ${_candidates[@]+"${_candidates[@]}"}; do
    if [[ -n "${_assoc["$_file"]+x}" ]]; then
      echo "push_gate_check: selected $_file via commit-subject association" >&2
      printf '%s\n' "$_file"
      exit 0
    fi
  done
fi
exit 0

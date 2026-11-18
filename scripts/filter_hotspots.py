#!/usr/bin/env python3
"""Drop hotspot rows for run-time staged files (#490).

entrypoint.sh copies the baked factory scripts into <clone>/dark-factory/scripts and
registers them in .git/info/exclude; `codeindex analyze .` only honours the root
.gitignore, so it indexes that copy and `codeindex high-blast` emits duplicate rows.

Reads `codeindex high-blast` output on stdin, writes it to stdout minus rows whose path
`git check-ignore` reports as ignored/excluded in --repo. Tracked files are never
reported by check-ignore, so a target that genuinely vendors dark-factory/ keeps its rows.
Fails open: any git error leaves the input unchanged, so no real hotspot is ever dropped.
"""
import argparse
import re
import subprocess
import sys

ROW = re.compile(r"^\s*[\d.]+\s+(\S+)")
HEADER = re.compile(r"\(\d+ found\)")


def ignored_paths(repo, paths):
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "check-ignore", "--stdin", "-z"],
            input="\0".join(paths) + "\0", capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode not in (0, 1):  # 128 = not a repo / error -> fail open
        return set()
    return {p for p in proc.stdout.split("\0") if p}


def filter_text(text, repo):
    lines = text.splitlines(keepends=True)
    paths = [m.group(1) for m in map(ROW.match, lines) if m]
    drop = ignored_paths(repo, paths)
    if not drop:
        return text
    kept, rows = [], 0
    for ln in lines:
        m = ROW.match(ln)
        if m and m.group(1) in drop:
            continue
        rows += 1 if m else 0
        kept.append(ln)
    return "".join(
        HEADER.sub(f"({rows} found)", ln, count=1) if not ROW.match(ln) else ln
        for ln in kept
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    sys.stdout.write(filter_text(sys.stdin.read(), args.repo))


if __name__ == "__main__":
    main()

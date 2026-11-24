#!/usr/bin/env python3
"""Build or verify a blast-radius approval record with the gate baked into the factory image.

Run INSIDE a throwaway container of the factory image by approve-blast.sh, never on the host
(the host cannot import factory_core). The gate that decides is /opt/dark-factory/scripts/
gate_blast_radius.py; this file only feeds it the same inputs blast_gate_check.sh does in
close mode and turns its answer into a record (spec: docs/archive/
2026-11-21-blast-radius-durable-approval-design.md, "Interface contract").

  blast_record.py build  --src /src --issue N --repo owner/name --branch B --sha SHA
                         --approved-by NAME --out /out [--request FILE] [--previous FILE]
  blast_record.py verify --src /src --issue N --repo owner/name --branch B --sha SHA
                         --approvals /approvals

/src is the host checkout's .git directory, mounted read-only. Nothing is fetched from the
network: the host script fetched origin's main and the feature branch into explicit
remote-tracking refs first, and this helper copies exactly those two refs.

build  prints the gate output and writes <out>/<issue>.json (canonical bytes) plus
       <out>/paths.txt (one approved path per line). Exit 0 only when the gate said
       HUMAN_REQUIRED and a record was written; 4 when nothing needs approval.
verify exits 0 only when the gate, given the approvals directory, prints STATUS: PASS with
       an `APPROVAL: matched` line.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile

GATE_DIR = os.environ.get("BLAST_GATE_BAKED_DIR", "/opt/dark-factory/scripts")
TRUSTED_REF = "refs/blast-gate/trusted-main"
HEAD_REF = "refs/blast-gate/head"


def die(msg: str, code: int = 2) -> None:
    print(f"blast_record.py: {msg}", file=sys.stderr)
    sys.exit(code)


def git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          env={**os.environ, "LC_ALL": "C"})
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def prepare(src: str, branch: str, sha: str) -> tuple:
    """Scratch repo holding origin/main as the trusted ref and the branch head, detached."""
    work = tempfile.mkdtemp(prefix="blast-approve-")
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], check=True)
    git(work, "init", "--quiet")
    git(work, "fetch", "--no-tags", "--quiet", src,
        f"+refs/remotes/origin/main:{TRUSTED_REF}",
        f"+refs/remotes/origin/{branch}:{HEAD_REF}")
    head = git(work, "rev-parse", "--verify", f"{HEAD_REF}^{{commit}}").strip()
    if head != sha:
        die(f"branch head is {head}, not the --sha you reviewed ({sha})")
    git(work, "checkout", "--quiet", "--detach", head)
    base = git(work, "merge-base", TRUSTED_REF, head).strip()
    return work, head, base


def run_gate(work: str, head: str, base: str, a, approvals: str) -> tuple:
    """Same inputs as blast_gate_check.sh close mode. Returns (stdout, changed, lines)."""
    changed = git(work, "-c", "core.quotePath=false", "diff", "--no-renames", base, head,
                  "--name-only")
    if any(ln.startswith('"') for ln in changed.split("\n")):
        die("a changed file name needs quoting; the gate cannot bind it by name")
    shortstat = git(work, "diff", base, head, "--shortstat")
    lines = 0
    for part in shortstat.split(","):
        part = part.strip()
        if "insertion" in part or "deletion" in part:
            lines += int(part.split()[0])
    hot = os.path.join(tempfile.mkdtemp(prefix="blast-hot-"), "hotspots.md")
    with open(hot, "w", encoding="utf-8") as fh:
        fh.write(git(work, "show", f"{TRUSTED_REF}:docs/codeindex-hotspots.md", check=False))
    proc = subprocess.run(
        [sys.executable, os.path.join(GATE_DIR, "gate_blast_radius.py"),
         "--changed-files-stdin", "--lines-changed", str(lines), "--hotspots", hot,
         "--config", ".claude/skills/refinement/config.yaml", "--clone-dir", work,
         "--base-ref", base, "--issue", str(a.issue), "--repo", a.repo,
         "--branch", a.branch, "--trusted-ref", TRUSTED_REF, "--head-ref", head,
         "--approvals-dir", approvals],
        input=changed, capture_output=True, text=True, cwd=work)
    if proc.returncode != 0:
        die(f"gate exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout, [ln for ln in changed.split("\n") if ln], lines


def field(out: str, name: str) -> str:
    for ln in out.split("\n"):
        if ln.startswith(name + ": "):
            return ln[len(name) + 2:]
    return ""


def build(a) -> None:
    sys.path.insert(0, GATE_DIR)
    import gate_blast_radius as gate  # the baked module: blob_entry + canonical_bytes

    work, head, base = prepare(a.src, a.branch, a.sha)
    empty = tempfile.mkdtemp(prefix="blast-none-")
    out, changed, lines = run_gate(work, head, base, a, empty)
    print(out, end="")
    status = field(out, "STATUS")
    if status != "HUMAN_REQUIRED":
        die(f"gate says STATUS: {status or '<none>'}; nothing to approve", 4)
    try:
        paths = json.loads(field(out, "TRIGGERED_PATHS"))
    except ValueError:
        die("gate printed no parseable TRIGGERED_PATHS line")
    size_trigger = any(ln.startswith("  - [size] ") for ln in out.split("\n"))

    # Union with the factory's advisory request, restricted to files this branch changed:
    # a forged request can add files to the review, never remove one.
    if a.request and os.path.exists(a.request):
        try:
            req = json.load(open(a.request, encoding="utf-8"))
            for p in req.get("triggered_paths", []):
                if isinstance(p, str) and p in changed and p not in paths:
                    paths.append(p)
        except (ValueError, OSError) as exc:
            print(f"blast_record.py: ignoring unreadable request file ({exc})", file=sys.stderr)
    if not paths and not size_trigger:
        die("HUMAN_REQUIRED but no triggered path and no size trigger; refusing to sign")

    count, prev_head = 1, None
    if a.previous and os.path.exists(a.previous):
        try:
            prev = json.load(open(a.previous, encoding="utf-8"))
            count = int(prev.get("approvals_count", 0)) + 1
            prev_head = prev.get("head_sha")
        except (ValueError, OSError, TypeError):
            pass

    record = {
        "schema": 1,
        "issue": a.issue,
        "repo": a.repo,
        "branch": a.branch,
        "head_sha": head,
        "base_sha": base,
        "files": {p: {"base": gate.blob_entry(work, base, p),
                      "head": gate.blob_entry(work, head, p)} for p in sorted(paths)},
        "size": None,
        "trigger": field(out, "TRIGGER"),
        "approved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "approved_by": a.approved_by,
        "approvals_count": count,
        "previous_head_sha": prev_head,
    }
    if size_trigger:
        record["size"] = {
            "lines_changed": lines,
            "head_tree": git(work, "rev-parse", f"{head}^{{tree}}").strip(),
            "base_tree": git(work, "rev-parse", f"{base}^{{tree}}").strip(),
        }
    with open(os.path.join(a.out, f"{a.issue}.json"), "wb") as fh:
        fh.write(gate.canonical_bytes(record))
    with open(os.path.join(a.out, "paths.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(p + "\n" for p in sorted(paths)))
    with open(os.path.join(a.out, "base_sha.txt"), "w", encoding="ascii", newline="\n") as fh:
        fh.write(base + "\n")


def verify(a) -> None:
    work, head, base = prepare(a.src, a.branch, a.sha)
    out, _, _ = run_gate(work, head, base, a, a.approvals)
    print(out, end="")
    if field(out, "STATUS") != "PASS" or not field(out, "APPROVAL").startswith("matched "):
        die("the baked gate does NOT accept this approval", 1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["build", "verify"])
    p.add_argument("--src", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--approved-by", default="")
    p.add_argument("--out")
    p.add_argument("--request")
    p.add_argument("--previous")
    p.add_argument("--approvals")
    a = p.parse_args()
    if a.mode == "build":
        if not a.out or not a.approved_by:
            die("build needs --out and --approved-by")
        build(a)
    else:
        if not a.approvals:
            die("verify needs --approvals")
        verify(a)


if __name__ == "__main__":
    main()

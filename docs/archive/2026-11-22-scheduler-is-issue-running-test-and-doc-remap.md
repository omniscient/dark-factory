# Scheduler: `is_issue_running` Exact-Match Test and `scheduler.sh` Doc Citation Re-Map — Implementation Plan

**Issue:** #579

Revised: 2026-11-23 (operator amendment after plan gate)

**Goal:** Pin `is_issue_running`'s anchored `#N([^0-9]|$)` match with a CI-discovered pytest
that sources the real function, re-map every `scheduler.sh` line citation in the four live
reference docs against `main`, and make the `max_retries` / circuit-breaker rows name
`dispatch_close`.

**Architecture:** One new pytest file drives the real bash function through a `bash -c`
harness (the `tests/test_scheduler_comment_verdict.py` pattern) with a `docker()` stub that
answers only the exact `ps --no-trunc --format '{{.Command}}'` call. The doc re-map is data,
not hand edits: Appendix A enumerates all 293 in-scope citation spans (doc, line, occurrence,
old, new, evidence), verified at plan time against `scheduler.sh` at `4584159`; scratch
scripts under `/tmp/df579/` (never committed) carry the table forward if `main` has moved,
print per-citation evidence for review, apply it, and prove no other file's citation moved.

**Tech Stack:** bash (scheduler.sh, sourced), Python 3 + pytest (CI: `python -m pytest tests/ -v`
with `PYTHONPATH=scripts`), `scripts/docs_refs_check.py`.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `tests/test_is_issue_running.py` | **Create** | Sources real `is_issue_running`; 11 parametrized cases (Requirement 1) |
| `docs/scheduler.md` | Modify (149 spans + oddity 4 bullet) | Re-map; oddity 4's `is_issue_running` bullet describes the anchored match |
| `docs/run-lifecycle.md` | Modify (12 spans) | `scheduler.sh` citation re-map only |
| `docs/features.md` | Modify (62 spans + circuit-breaker row) | Re-map; add `dispatch_close` (`:372`) to "used by stages at" |
| `docs/configuration.md` | Modify (27 spans + `max_retries` row) | Re-map; row names Close/merge + `dispatch_close` reader `:372` |
| `/tmp/df579/*` | Scratch only — **never commit** | Table, apply/rebase/evidence/check scripts |

Nothing else is touched: not `scheduler.sh` (the red step reverts it and restores it before
any commit), not `.github/workflows/ci.yml`, not `scripts/docs_refs_check.py`, not
`docs/archive/**`, `docs/superpowers/**`, `docs/adapter-authoring-guide.md`, nor any test
docstring that cites `scheduler.sh` lines.

## Ground truth established at plan time (read before Task 3)

- **Base.** `origin/main` = `4584159` (Merge PR #578). `scheduler.sh` is 1658 lines;
  `is_issue_running` is `scheduler.sh:115-121`; `dispatch_close` is `:368-380` and reads
  `MAX_RETRIES` in `eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")` at
  `:372`.
- **Single citation basis.** Every in-scope citation was last mapped against `scheduler.sh`
  as of `f9c2e67` ("docs: re-map scheduler.sh citations after dispatch_close (#414)").
  Checked: every doc line changed in `f9c2e67..4584159` either has no `scheduler.sh` span,
  or (438c3d6's `` `:363` `` additions) was written against that same file. The only
  `scheduler.sh` change since is `d8d074f`, two hunks:
  `@@ -114,7 +114,9 @@` (the is_issue_running comment: old `117` → `119`, old ≥118 → +2) and
  `@@ -358,8 +360,11 @@` (the dispatch_close comment: old 358-360 → +2, old ≥363 → +5).
  No in-scope citation lands on the deleted old lines 361-362.
- **The shift is not the whole story.** Four spans were already wrong before `d8d074f`
  and are fixed by symbol, not offset (they are in the table with these values):
  - `docs/features.md:49` `` `:579-584` `` (`fetch_board_items`) → `604-609` (function is `:604-609`).
  - `docs/features.md:49` second `` `:383` `` (`set_board_status`, the #507 open-limits cell) → `408`.
  - `docs/scheduler.md:938` `` `:1493` `` (the `mh-preview-` stale-preview warning) → `1518`.
  - `docs/run-lifecycle.md:53` `scheduler.sh:383-401` (`dispatch`) → `388-405` (function is `:388-405`; the old range ran one blank line past `}`).
- **Enumeration (Requirement 2).** 293 spans resolve to `scheduler.sh` — 250 change, 43 are
  already correct and are verified but not edited:

  | Doc | In-scope spans | Change | Unchanged (verified) |
  |---|---|---|---|
  | `docs/scheduler.md` | 171 | 149 | 22 |
  | `docs/run-lifecycle.md` | 12 | 12 | 0 |
  | `docs/features.md` | 70 | 62 | 8 |
  | `docs/configuration.md` | 40 | 27 | 13 |

  Forms covered: (a) path-prefixed `scheduler.sh:N` / `:N-M`; (b) comma lists, edited as a
  whole token (e.g. `scheduler.sh:1074,1086,1093,1230,1241` → `1079,1091,1098,1235,1246`);
  (c) bare continuation spans whose antecedent is `scheduler.sh` — the nearest preceding
  path-bearing citation in the row/paragraph, or, for rows/paragraphs with none, the section's
  (e.g. the `docs/scheduler.md` stage table rows 208-220 and the log-line table 713-732).
- **Bare spans judged NOT `scheduler.sh` (left untouched).** Their antecedent is another file
  even though `scheduler.sh` appears nearby: `docs/scheduler.md:133` (`:223`, `:23`),
  `:135` (`:264`), `:144` (`:224-228`), `:170` (`:175`) — `providers/tracker/github.py`;
  `:328` (`:127-132`) — `deploy/instance.env.example`; `:922` (`:124`) — `board.py`.
  `docs/features.md:52` second `` `:23` `` — `scheduler_lib.sh`; `:58` (`:84`) — `rescue.py`;
  `:63` (`:145`, `:288`, `:334`) — `breaker.py`; `:66` (`:66`, `:140`) — `smoke_gate.sh`;
  `:69` (`:257`, `:60`) — `epic_autopilot.py`; `:102` (`:44`, `:95`, `:75`, `:318`, `:178`) —
  `dark-factory-validate.md` / `gate_blast_radius.py`; `:166`-`:167` — compose / `publish.yml`.
  `docs/configuration.md:178` (`:76-103`) — `gate_blast_radius.py`; `:528` (`:296-302`) —
  `adapter.py`. Every other bare span in `docs/run-lifecycle.md` cites the workflow YAML, a
  command file or `entrypoint.sh`. PR #571 (open) re-maps `entrypoint.sh`/workflow spans in
  these same rows — they must stay byte-identical (Task 4 Step 5 proves it).
- **Memory lesson baked in:** a green `docs_refs_check.py` is *not* evidence a citation is
  right (it checks range bounds only, never bare spans). Task 4 Step 2's per-row evidence
  review is the actual check; the docs check is only a regression guard.
- **Known non-citation staleness, fixed in Task 5 (spec Requirement 3, last bullet).**
  `docs/scheduler.md` oddity 4 (heading "Container matching is by substring and host-wide",
  ~line 810) still says `is_issue_running` uses `grep -q "#${issue_num}"` so issue 12
  matches 123. Task 4 re-maps its citation (`:117` → `:119`); Task 5 then rewrites that one
  bullet to describe the anchored match. Nothing else in the entry changes: its number, its
  heading and the host-wide bullet stay, the citation stays on the same line and the bullet
  stays two lines long, so no later entry number or doc line shifts.

---

## Task 1: Pre-flight — branch, base and drift check

**Files:** none (read-only)

- [ ] **Step 1: Confirm the checkout and base**

```bash
git fetch origin main
git branch --show-current          # expect feat/issue-579-...
git log --oneline -1 origin/main
git merge-base --is-ancestor 4584159 origin/main && echo "base ok"
```
Expected: a `feat/issue-579-*` branch; `base ok`.

- [ ] **Step 2: Record whether `main` moved under the plan's ground truth**

```bash
git diff --stat 4584159 origin/main -- scheduler.sh docs/scheduler.md docs/run-lifecycle.md docs/features.md docs/configuration.md
```
Expected: empty output → Task 3 Step 3 is a no-op. Non-empty → Task 3 Step 3 carries the
table forward; note the output for `implementation.md`.

- [ ] **Step 3: Confirm the function under test is what the plan assumes**

```bash
grep -nF 'grep -qE "#${issue_num}([^0-9]|$)"' scheduler.sh
grep -n '^is_issue_running()\|^dispatch_close()' scheduler.sh
grep -nF 'evaluate_stop "$issue_num" "close" "$MAX_RETRIES"' scheduler.sh
```
Expected (at `4584159`): one match at line 119; `115:is_issue_running() {` and
`368:dispatch_close() {`; one match at line 372. If line 372 moved, use the new line
everywhere this plan writes `:372` (Task 5).

---

## Task 2: Test `is_issue_running`'s anchored match (Requirement 1)

**Files:** Create `tests/test_is_issue_running.py`

- [ ] **Step 1: Write the test file**

```bash
cat > tests/test_is_issue_running.py <<'PYEOF'
"""Fixtures for scheduler.sh::is_issue_running's anchored match (#579).

PR #578's review fixup (d8d074f) changed the shared duplicate-dispatch guard from a
substring `grep -q "#N"` to `grep -qE "#N([^0-9]|$)"`, so a running `Close issue #711`
no longer makes #71 look running. Every other scheduler test stubs is_issue_running out;
this file sources the real function and replaces only `docker`.

Source-time hazards are handled as in tests/test_scheduler_comment_verdict.py: a
`python3` override short-circuits the providers preflight, and SCHEDULER_STATE_DIR /
STATE_FILE point at pytest's tmp_path. scheduler.sh's own `set -e` stays active after
`source`, so the harness calls the function inside `if` and reports an explicit RESULT=
token; the test asserts on that token and on a zero exit, so a harness that died cannot
pass as "not running".

The `docker` stub answers only the exact `ps --no-trunc --format '{{.Command}}'` call
the guard makes (anything else prints nothing and fails): `--no-trunc` is load-bearing,
since a truncated Command column can drop the issue number, so a changed invocation
must fail the positive cases rather than pass silently.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = REPO_ROOT / "scheduler.sh"

_HARNESS = r'''
set -uo pipefail
python3() {
  case "$*" in
    *providers/cli.py*) return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
SCHEDULER_SOURCE_ONLY=1 source "$1"
docker() {
  if [ "$#" -eq 4 ] && [ "$1" = "ps" ] && [ "$2" = "--no-trunc" ] \
     && [ "$3" = "--format" ] && [ "$4" = "{{.Command}}" ]; then
    if [ -n "${DOCKER_PS_OUTPUT:-}" ]; then printf '%s\n' "$DOCKER_PS_OUTPUT"; fi
    return 0
  fi
  return 1
}
export -f docker
declare -F is_issue_running >/dev/null || { echo HARNESS_BROKEN; exit 97; }
if is_issue_running "$2"; then echo RESULT=running; else echo RESULT=not-running; fi
'''

CLOSE_711 = "entrypoint.sh 'Close issue #711'"
CLOSE_71 = "entrypoint.sh 'Close issue #71'"
CLOSE_7111 = "entrypoint.sh 'Close issue #7111'"
EOL_711 = "entrypoint.sh Close issue #711"  # issue number is the last thing on the line

CASES = [
    # The case from the issue: #711 running must not make #71 look running.
    (CLOSE_711, "711", "running"),
    (CLOSE_711, "71", "not-running"),
    # Numeric near-misses in both directions.
    (CLOSE_711, "7111", "not-running"),
    (CLOSE_7111, "711", "not-running"),
    (CLOSE_71, "711", "not-running"),
    # Two containers: each issue is found on its own line.
    (CLOSE_71 + "\n" + CLOSE_711, "71", "running"),
    (CLOSE_71 + "\n" + CLOSE_711, "711", "running"),
    # The `$` alternative of the anchor: nothing after the number.
    (EOL_711, "711", "running"),
    (EOL_711, "71", "not-running"),
    # No running containers at all.
    ("", "71", "not-running"),
    ("", "711", "not-running"),
]


def _run(docker_ps_output: str, issue_num: str, state_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SCHEDULER_STATE_DIR"] = str(state_dir)
    env["STATE_FILE"] = str(state_dir / "scheduler-state.json")
    env["DOCKER_PS_OUTPUT"] = docker_ps_output
    return subprocess.run(
        ["bash", "-c", _HARNESS, "_", str(SCHEDULER), issue_num],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )


@pytest.mark.parametrize("docker_ps_output,issue_num,expected_result", CASES)
def test_is_issue_running(docker_ps_output, issue_num, expected_result, tmp_path):
    result = _run(docker_ps_output, issue_num, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    tokens = [l for l in result.stdout.splitlines() if l.startswith("RESULT=")]
    assert tokens == [f"RESULT={expected_result}"], result.stdout + result.stderr
PYEOF
```

- [ ] **Step 2: Red — temporarily revert the regex and watch the test catch it**

The fix already exists, so "failing first" means proving the test detects the old
substring match:

```bash
python3 - <<'PYEOF'
p = "scheduler.sh"
s = open(p).read()
old = 'grep -qE "#${issue_num}([^0-9]|$)"'
assert s.count(old) == 1, s.count(old)
open(p, "w").write(s.replace(old, 'grep -q "#${issue_num}"'))
PYEOF
PYTHONPATH=scripts python -m pytest tests/test_is_issue_running.py -v 2>&1 | tee /tmp/df579-red.txt | grep -E "FAILED|passed|failed"
```
Expected (verified at plan time): exactly 3 failed, 8 passed —
```
FAILED tests/test_is_issue_running.py::test_is_issue_running[entrypoint.sh 'Close issue #711'-71-not-running]
FAILED tests/test_is_issue_running.py::test_is_issue_running[entrypoint.sh 'Close issue #7111'-711-not-running]
FAILED tests/test_is_issue_running.py::test_is_issue_running[entrypoint.sh Close issue #711-71-not-running]
3 failed, 8 passed
```
The first line is the issue's `71`-against-`#711` case. Keep `/tmp/df579-red.txt`; its
summary goes into `implementation.md` and the PR body (Requirement 1's "record that in the PR").

- [ ] **Step 3: Restore `scheduler.sh` and prove it is byte-identical to the base**

```bash
git checkout -- scheduler.sh
git diff --quiet -- scheduler.sh && git diff --quiet origin/main -- scheduler.sh && echo "scheduler.sh restored"
```
Expected: `scheduler.sh restored`. Do not proceed (and never commit) otherwise.

- [ ] **Step 4: Green**

```bash
PYTHONPATH=scripts python -m pytest tests/test_is_issue_running.py -v
```
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_is_issue_running.py
git commit -m "test(scheduler): pin is_issue_running's anchored #N match (#579)

Sources the real function (every other scheduler test stubs it) and stubs only the
exact docker ps --no-trunc call. Red check: with the regex reverted to the old
substring grep -q, 3 of 11 cases fail, including #711 making #71 look running."
```

---

## Task 3: Stage the re-map tooling and table in `/tmp/df579` (scratch, not committed)

**Files:** none in the repo (all under `/tmp/df579/`)

- [ ] **Step 1: Extract the Appendix A table from this plan file (tabs preserved)**

```bash
mkdir -p /tmp/df579
PLAN=docs/superpowers/plans/2026-11-22-scheduler-is-issue-running-test-and-doc-remap.md
awk '/^<!-- remap579-table:begin -->$/{f=1;next} /^<!-- remap579-table:end -->$/{f=0} f' "$PLAN" \
  | grep -v '^```' > /tmp/df579/remap579.tsv
wc -l < /tmp/df579/remap579.tsv
awk -F'\t' 'NF!=7{bad++} END{print "bad_rows=" bad+0}' /tmp/df579/remap579.tsv
```
Expected: `293`, `bad_rows=0`. (If the plan was already moved to `docs/archive/`, point
`PLAN` there.)

- [ ] **Step 2: Write the five scratch scripts**

```bash
cat > /tmp/df579/apply_remap.py <<'PYEOF'
"""Apply the #579 scheduler.sh citation re-map table (scratch tool, not committed).

Table rows (tab-separated): doc, line, occurrence, form, old, new, evidence.
form=prefixed edits `scheduler.sh:<old>`; form=bare edits `` `:<old>` ``. occurrence is
the 0-based index of that exact token on the line in the ORIGINAL text. Every row is
checked (the token must exist); rows with old == new are verified, not edited.
"""
import collections
import re
import sys

table, root = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else ".")
rows = [l.rstrip("\n").split("\t") for l in open(table) if l.strip() and not l.startswith("#")]
by_doc = collections.defaultdict(lambda: collections.defaultdict(list))
for doc, line, occ, form, old, new, *_ in rows:
    by_doc[doc][int(line)].append((int(occ), form, old, new))

def token(form, nums):
    return f"scheduler.sh:{nums}" if form == "prefixed" else f"`:{nums}`"

def pattern(form, nums):
    if form == "prefixed":
        return r"(?<![\w/-])" + re.escape(token(form, nums)) + r"(?![0-9,-])"
    return re.escape(token(form, nums))

errors = changed = verified = 0
for doc, per_line in by_doc.items():
    path = f"{root}/{doc}"
    lines = open(path).read().split("\n")
    for ln, items in per_line.items():
        orig = lines[ln - 1]
        edits = []
        for occ, form, old, new in items:
            hits = [m.start() for m in re.finditer(pattern(form, old), orig)]
            if occ >= len(hits):
                print(f"MISSING {doc}:{ln} occurrence {occ} of {token(form, old)}")
                errors += 1
                continue
            start = hits[occ]
            if old == new:
                verified += 1
            else:
                edits.append((start, start + len(token(form, old)), token(form, new)))
        text = orig
        for start, end, rep in sorted(edits, reverse=True):
            text = text[:start] + rep + text[end:]
            changed += 1
        lines[ln - 1] = text
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
print(f"changed={changed} unchanged_verified={verified} errors={errors}")
sys.exit(1 if errors else 0)
PYEOF
cat > /tmp/df579/rebase_table.py <<'PYEOF'
"""Carry the #579 re-map table from BASE to origin/main (scratch tool, not committed).

The table's `new` column is scheduler.sh line numbers at BASE and its `line` column is doc
line numbers at BASE. For every later hunk in `git diff -U0 BASE origin/main`:
  - scheduler.sh hunks shift the `new` numbers; a number inside a modified hunk is FLAGGED,
  - doc hunks shift the `line` column; a row whose doc line was modified is FLAGGED.
Flagged rows are written with their BASE values and must be re-derived by hand (grep the
symbol in scheduler.sh / the token in the doc) before running apply_remap.py.
Usage: python3 rebase_table.py BASE IN.tsv OUT.tsv
"""
import re
import subprocess
import sys

base, src, dst = sys.argv[1:4]

def hunks(path):
    out = subprocess.run(["git", "diff", "-U0", base, "origin/main", "--", path],
                         capture_output=True, text=True, check=True).stdout
    return [(int(m[1]), int(m[2]) if m[2] is not None else 1,
             int(m[4]) if m[4] is not None else 1)
            for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", out, re.M)]

def mapper(path):
    hs = hunks(path)
    def m(n):
        shift = 0
        for start, count, new_count in hs:
            if count and start <= n <= start + count - 1:
                return None
            end = start + count - 1 if count else start
            if n > end:
                shift += new_count - count
        return n + shift
    return m

sched = mapper("scheduler.sh")
doc_maps = {}
flagged = 0
with open(dst, "w") as out:
    for raw in open(src):
        if not raw.strip() or raw.startswith("#"):
            out.write(raw)
            continue
        doc, line, occ, form, old, new, *rest = raw.rstrip("\n").split("\t")
        dm = doc_maps.setdefault(doc, mapper(doc))
        new_line = dm(int(line))
        parts, bad = [], new_line is None
        for p in new.split(","):
            lead = p[: len(p) - len(p.lstrip())]
            nums = [sched(int(x)) for x in p.strip().split("-")]
            bad = bad or None in nums
            parts.append(lead + "-".join(str(x) for x in nums))
        if bad:
            flagged += 1
            print(f"FLAG {doc}:{line} {form} {old} (BASE new={new}) — re-derive by hand")
            out.write(raw)
        else:
            out.write("\t".join([doc, str(new_line), occ, form, old, ",".join(parts), *rest]) + "\n")
print(f"flagged={flagged}")
PYEOF
cat > /tmp/df579/show_evidence.py <<'PYEOF'
"""Print, per table row, the doc's claim next to the scheduler.sh line(s) the NEW value
points at, for the by-hand per-citation review (scratch tool, not committed).
Usage: python3 show_evidence.py TABLE.tsv   (run from the repo root, BEFORE apply_remap.py)
"""
import re
import sys

sched = open("scheduler.sh").read().split("\n")
docs = {}
for raw in open(sys.argv[1]):
    if not raw.strip() or raw.startswith("#"):
        continue
    doc, line, occ, form, old, new, *_ = raw.rstrip("\n").split("\t")
    text = docs.setdefault(doc, open(doc).read().split("\n"))[int(line) - 1]
    tok = f"scheduler.sh:{old}" if form == "prefixed" else f"`:{old}`"
    hits = [m.start() for m in re.finditer(re.escape(tok), text)]
    at = hits[int(occ)] if int(occ) < len(hits) else 0
    claim = text[max(0, at - 90):at].split("|")[-1].strip()
    nums = [int(x) for x in re.findall(r"\d+", new)]
    first, last = nums[0], nums[-1]
    mark = "  " if old == new else "->"
    print(f"{doc}:{line} [{old}] {mark} [{new}]\n    claim: …{claim}\n"
          f"    {first}: {sched[first - 1].strip()[:90]}")
    if last != first:
        print(f"    {last}: {sched[last - 1].strip()[:90]}")
PYEOF
cat > /tmp/df579/check_others.py <<'PYEOF'
"""Assert no non-scheduler.sh citation changed (scratch tool, not committed): every
path:N token whose path is not scheduler.sh must be byte-identical in REF (default HEAD)
and the working tree. Usage: python3 check_others.py [REF]"""
import re, subprocess, sys
REF = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
DOCS = ["docs/scheduler.md", "docs/run-lifecycle.md", "docs/features.md", "docs/configuration.md"]
TOK = re.compile(r"(?<![\w/-])([A-Za-z0-9_./-]+\.(?:sh|py|ya?ml|md|json)|workflow):[0-9][0-9,-]*")
bad = 0
for doc in DOCS:
    before = subprocess.run(["git", "show", f"{REF}:{doc}"], capture_output=True, text=True, check=True).stdout
    after = open(doc).read()
    tb = [m.group(0) for m in TOK.finditer(before) if not m.group(1).endswith("/scheduler.sh") and m.group(1) != "scheduler.sh"]
    ta = [m.group(0) for m in TOK.finditer(after) if not m.group(1).endswith("/scheduler.sh") and m.group(1) != "scheduler.sh"]
    if tb != ta:
        bad += 1
        print(f"{doc}: non-scheduler.sh citations changed")
print("other-file citations unchanged" if not bad else "FAIL")
sys.exit(1 if bad else 0)
PYEOF
cat > /tmp/df579/committed_table.py <<'PYEOF'
"""Build the committed-values table for Task 7 (scratch tool, not committed): old = new =
the value now in the docs (Task 4 + Task 5), occurrence renumbered per line for the new
token text, plus the Task 5 features.md `:372` row.
Usage: python3 committed_table.py CUR.tsv OUT.tsv   (run from the repo root)
"""
import collections
import sys

src, dst = sys.argv[1:3]
conf = open("docs/configuration.md").read().split("\n")
rows_hit = [i + 1 for i, l in enumerate(conf) if l.startswith("| `scheduler.max_retries` |")]
assert len(rows_hit) == 1, rows_hit
MAX_RETRIES_ROW = rows_hit[0]
seen = collections.Counter()
out = []
for raw in open(src):
    if not raw.strip() or raw.startswith("#"):
        continue
    doc, line, occ, form, old, new, ev = raw.rstrip("\n").split("\t")
    if doc == "docs/configuration.md" and form == "prefixed" and int(line) == MAX_RETRIES_ROW:
        new = "372," + new  # Task 5 added the dispatch_close reader to this list
    key = (doc, line, form, new)
    out.append([doc, line, str(seen[key]), form, new, new, ev])
    seen[key] += 1
feat = open("docs/features.md").read().split("\n")
hits = [i + 1 for i, l in enumerate(feat) if "and by `dispatch_close` (`:372`)" in l]
assert len(hits) == 1, hits
out.append(["docs/features.md", str(hits[0]), "0", "bare", "372", "372", "dispatch_close evaluate_stop (Task 5)"])
with open(dst, "w") as fh:
    fh.writelines("\t".join(r) + "\n" for r in out)
print(f"rows={len(out)}")
PYEOF
```

- [ ] **Step 3: Carry the table forward to current `origin/main`**

```bash
python3 /tmp/df579/rebase_table.py 4584159 /tmp/df579/remap579.tsv /tmp/df579/remap579.cur.tsv
```
Expected when Task 1 Step 2 was empty: `flagged=0` and
`cmp /tmp/df579/remap579.tsv /tmp/df579/remap579.cur.tsv` silent. If any `FLAG` line is
printed, edit that row in `remap579.cur.tsv` by hand: grep the symbol the doc names in
`scheduler.sh` (`grep -n '^<function>()' scheduler.sh`, or the specific call/echo the prose
describes) for the `new` column, and `grep -n` the doc for the token's current line. Also, if
the docs diff in Task 1 Step 2 added any new `scheduler.sh` span, add a row for it by the
same method (any span added after `4584159` is not in Appendix A).

---

## Task 4: Re-map the `scheduler.sh` citations (Requirement 2)

**Files:** Modify `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md`, `docs/configuration.md`

- [ ] **Step 1: Baseline — the docs check passes before editing**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 2: Per-citation review (the real verification — run BEFORE applying)**

```bash
python3 /tmp/df579/show_evidence.py /tmp/df579/remap579.cur.tsv > /tmp/df579/evidence.txt
grep -c '^docs/' /tmp/df579/evidence.txt
wc -l < /tmp/df579/evidence.txt
sed -n '1,150p' /tmp/df579/evidence.txt     # then 151,300p, 301,450p, ... to the end
```
Expected: `293`. Read the whole file in 150-line chunks (headless: no pager). For each entry, confirm the `scheduler.sh` line(s) at the new number are the
function/constant/behaviour the claim names (for a range, the first line is the definition
or step start and the last line is its end, e.g. `stage_review_triage` `1130`/`1163`
shows `stage_review_triage() {`/`}`; for a log-line row, the line is the `echo` with that key).
Fix any row that is wrong in `remap579.cur.tsv` and note it in `implementation.md`.
Spot checks that must hold: `docs/scheduler.md` `dispatch_close` `:363`→`368`;
`stage_review_triage` `:1125-1158`→`1130-1163`; `docs/configuration.md` `MAX_RETRIES` readers
`1074,1086,1093,1230,1241`→`1079,1091,1098,1235,1246`; `is_issue_running`
`115-119`→`115-121`.

- [ ] **Step 3: Apply**

```bash
python3 /tmp/df579/apply_remap.py /tmp/df579/remap579.cur.tsv .
```
Expected (no drift since `4584159`): `changed=250 unchanged_verified=43 errors=0`. Any
`MISSING` line means a doc line moved — fix the row's line/occurrence (Task 3 Step 3 method),
`git checkout -- docs/`, and re-run.

- [ ] **Step 4: Only the four docs changed, by equal line counts**

```bash
git diff --stat
```
Expected: exactly `docs/configuration.md`, `docs/features.md`, `docs/run-lifecycle.md`,
`docs/scheduler.md`; `4 files changed, 179 insertions(+), 179 deletions(-)` at `4584159`
(a line holding several spans counts once).

- [ ] **Step 5: Prove no other file's citation moved (PR #571 rows stay byte-identical)**

```bash
python3 /tmp/df579/check_others.py HEAD
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: `other-file citations unchanged`; `Docs reference check passed for 4 doc(s).`

- [ ] **Step 6: Commit**

```bash
git add docs/scheduler.md docs/run-lifecycle.md docs/features.md docs/configuration.md
git commit -m "docs: re-map scheduler.sh citations after d8d074f (#579)

293 scheduler.sh spans (path-prefixed, comma lists and bare continuation spans)
verified against scheduler.sh on main; 250 corrected, 43 already right. Most moved
+2/+5 with d8d074f's two comment hunks; four were already wrong and are fixed by symbol
(features.md fetch_board_items and set_board_status, scheduler.md mh-preview warning,
run-lifecycle.md dispatch range). entrypoint.sh/workflow spans untouched (PR #571)."
```

---

## Task 5: `max_retries` row and circuit-breaker row name `dispatch_close`; `is_issue_running` prose corrected (Requirement 3)

**Files:** Modify `docs/configuration.md` (the `scheduler.max_retries` row), `docs/features.md` (the "Circuit breaker" row), `docs/scheduler.md` (oddity 4, first bullet)

- [ ] **Step 1: Re-verify the new citation target**

```bash
grep -nF 'evaluate_stop "$issue_num" "close" "$MAX_RETRIES"' scheduler.sh
```
Expected: `372:  eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")`. If the
number differs, substitute it for `372` in Step 2.

- [ ] **Step 2: Edit both rows and the oddity 4 bullet (exact-match replace; runs after Task 4, so it matches the re-mapped text)**

```bash
python3 - <<'PYEOF'
edits = [
    ("docs/configuration.md",
     "| per-issue retry ceiling for implement and conflict-resolve before the breaker trips to Blocked (refine and plan use the env-only `REFINE_MAX_RETRIES`, see Layer 4) | `scheduler.sh:1079,1091,1098,1235,1246` |",
     "| per-issue retry ceiling for implement, conflict-resolve and Close/merge dispatches (the `N:close` counter, via `dispatch_close`) before the breaker trips to Blocked (refine and plan use the env-only `REFINE_MAX_RETRIES`, see Layer 4) | `scheduler.sh:372,1079,1091,1098,1235,1246` |"),
    ("docs/features.md",
     "used by stages at `:1072`, `:1228`, `:1290`, `:1357`. |",
     "used by stages at `:1072`, `:1228`, `:1290`, `:1357`, and by `dispatch_close` (`:372`). |"),
    ("docs/scheduler.md",
     '   - `is_issue_running` uses `grep -q "#${issue_num}"` (`:119`), so issue 12 is\n'
     '     considered running while a container for issue 123 exists.\n',
     '   - `is_issue_running` uses the anchored `grep -qE "#${issue_num}([^0-9]|$)"` (`:119`), so issue\n'
     '     12 is not considered running while only a container for issue 123 exists (resolved since #414, `d8d074f`).\n'),
]
for path, old, new in edits:
    text = open(path).read()
    assert text.count(old) == 1, (path, text.count(old))
    open(path, "w").write(text.replace(old, new))
    print("edited", path)
PYEOF
```
Expected: `edited docs/configuration.md`, `edited docs/features.md`,
`edited docs/scheduler.md`. (If Task 3 Step 3 moved any of `1079,1091,1098,1235,1246`,
`1072,1228,1290,1357` or `119`, use the re-mapped numbers in the `old` and `new` strings.)
The `docs/scheduler.md` edit is located by its sentence and replaces two lines with two
lines; the `` `:119` `` citation stays on the first of them, so Task 7's table still finds it.

- [ ] **Step 3: Verify**

```bash
git diff -U0 docs/configuration.md docs/features.md | grep '^[-+]|'
git diff -U0 docs/scheduler.md | grep '^[-+] '
grep -cF 'grep -q "#${issue_num}"' docs/scheduler.md
sed -n '/^4\. \*\*Container matching is by substring and host-wide\.\*\*$/,/^5\. /p' docs/scheduler.md
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: one `-`/`+` pair per file for `docs/configuration.md` and `docs/features.md`
showing only the added words and the `372` citation; for `docs/scheduler.md` exactly two
`-` lines and two `+` lines (the oddity 4 bullet); `0` (the old substring form is gone from
the doc); entry 4 printed with its heading and its host-wide `docker ps` bullet unchanged
and entry 5 still numbered 5; `Docs reference check passed for 4 doc(s).`

- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/features.md docs/scheduler.md
git commit -m "docs: max_retries and circuit-breaker rows name dispatch_close (#579)

Since #414, MAX_RETRIES also caps Close/merge dispatches (the N:close counter via
dispatch_close, read at scheduler.sh:372). scheduler.md oddity 4 now describes
is_issue_running's anchored match (d8d074f) instead of the old substring grep.
Docs only; no breaker behaviour change."
```

---

## Task 6: Full verification and scope check

**Files:** none

- [ ] **Step 1: Full suite (CI parity)**

```bash
PYTHONPATH=scripts python -m pytest tests/ -v 2>&1 | tail -3
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: all pass (3119 passed at plan time, including the 11 new cases); docs check passes.

- [ ] **Step 2: Scope**

```bash
git diff --stat origin/main HEAD -- . ':(exclude)docs/superpowers/' ':(exclude).archon/memory/'
git diff --quiet origin/main HEAD -- scheduler.sh .github/ scripts/ && echo "no code/CI/script change"
```
Expected: only `tests/test_is_issue_running.py` and the four docs;
`no code/CI/script change`. Nothing from `/tmp/df579` is tracked (`git status --short` clean).

- [ ] **Step 3: Record in `implementation.md`** — the red-step summary from
`/tmp/df579-red.txt` (3 failed / 8 passed with the reverted regex), the
`changed=… unchanged_verified=… errors=0` line, any rows corrected during the Task 4 Step 2
review, and that `docs/scheduler.md` oddity 4's `is_issue_running` bullet was corrected in
Task 5 (it is in scope under spec Requirement 3, so no spillover entry is filed for it).

## Task 7: Re-verify after any merge of `main` (before the PR is marked ready)

**Files:** possibly the four docs

- [ ] **Step 1: After any rebase/merge of `main` into the branch, check whether `scheduler.sh` or the docs moved**

`OLD_BASE` is the `origin/main` commit Tasks 3-5 were verified against (`4584159` unless
Task 1 Step 2 showed drift).

```bash
git diff --stat "$OLD_BASE" origin/main -- scheduler.sh docs/scheduler.md docs/run-lifecycle.md docs/features.md docs/configuration.md
```
Expected: empty → done, skip Step 2.

- [ ] **Step 2: If anything moved, carry the committed values forward and re-apply**

Build a table whose `old` and `new` both hold the committed values (including Task 5's two
edits), rebase it, review, apply:

```bash
python3 /tmp/df579/committed_table.py /tmp/df579/remap579.cur.tsv /tmp/df579/committed.tsv
python3 /tmp/df579/rebase_table.py "$OLD_BASE" /tmp/df579/committed.tsv /tmp/df579/next.tsv
diff <(cut -f1,2,6 /tmp/df579/committed.tsv) <(cut -f1,2,6 /tmp/df579/next.tsv)
python3 /tmp/df579/show_evidence.py /tmp/df579/next.tsv > /tmp/df579/evidence-next.txt
python3 /tmp/df579/apply_remap.py /tmp/df579/next.tsv .
```
Expected with no drift (verified at plan time on a scratch copy): `rows=294`, `flagged=0`,
empty `diff`, `changed=0 unchanged_verified=294 errors=0`. With drift: hand-fix every `FLAG`
row in `next.tsv` before applying (Task 3 Step 3 method) and review the evidence for every row
the `diff` shows. Then re-run Task 4 Steps 4-5 (`check_others.py HEAD`) and Task 6, and commit
`docs: re-map scheduler.sh citations after merging main (#579)`.

---

## Appendix A — In-scope citation table (verified at `4584159`)

Columns (tab-separated): doc, doc line, occurrence (0-based index of that exact token on the
line), form (`prefixed` = `scheduler.sh:N`, `bare` = `` `:N` ``), old, new, `scheduler.sh`
text at the first new line (evidence only, ignored by the scripts). Grouped per doc; rows
where old = new are the 43 verified-unchanged spans.

<!-- remap579-table:begin -->
```
docs/scheduler.md	48	0	prefixed	1460	1465	if [ "${SCHEDULER_SOURCE_ONLY:-0}" = "1" ]; then
docs/scheduler.md	53	0	prefixed	14-16	14-16	source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/identity.sh"
docs/scheduler.md	54	0	prefixed	45	45	python3 "$FACTORY_PROVIDERS_CLI" preflight
docs/scheduler.md	55	0	prefixed	53-59	53-59	if [ -f /workspace/project/.archon/.env ]; then
docs/scheduler.md	56	0	prefixed	10-11	10-11	SCHEDULER_STATE_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factor
docs/scheduler.md	56	0	bare	62-67	62-67	mkdir -p "$SCHEDULER_STATE_DIR"
docs/scheduler.md	57	0	prefixed	35-42	35-42	read_config() {
docs/scheduler.md	57	0	prefixed	1465	1470	read_config
docs/scheduler.md	58	0	prefixed	1472-1476	1477-1481	_sched_err_trap() {
docs/scheduler.md	59	0	prefixed	1479-1482	1484-1487	WIP_DATA=$(fetch_wip_limits)
docs/scheduler.md	60	0	prefixed	1488-1503	1493-1508	echo "[$(date -u +%FT%TZ)] probe=image_check image=${FACTORY_IMA
docs/scheduler.md	61	0	prefixed	1506-1516	1511-1521	STALE_PREVIEW_WARN_COUNT="${STALE_PREVIEW_WARN_COUNT:-3}"
docs/scheduler.md	62	0	prefixed	1521-1528	1526-1533	if [ -f /workspace/project/.factory/adapter.yaml ]; then
docs/scheduler.md	63	0	prefixed	1531	1536	echo "Backlog scheduler started (poll every ${POLL_INTERVAL}s)"
docs/scheduler.md	67	0	prefixed	35-42	35-42	read_config() {
docs/scheduler.md	88	0	prefixed	22-24	22-24	# Refinement pipeline (env-only: REFINE_MAX_RETRIES is not in co
docs/scheduler.md	90	0	prefixed	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/scheduler.md	90	0	bare	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/scheduler.md	124	0	prefixed	1533-1653	1538-1658	while true; do
docs/scheduler.md	132	0	prefixed	70-85	70-85	check_rate_limit() {
docs/scheduler.md	133	0	prefixed	599-604	604-609	fetch_board_items() {
docs/scheduler.md	134	0	prefixed	1633-1634	1638-1639	BUDGET=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-rate-budge
docs/scheduler.md	135	0	prefixed	611-627	616-632	off_board_ready_issues() {
docs/scheduler.md	135	0	bare	1640	1645	if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
docs/scheduler.md	138	0	prefixed	1541	1546	BOARD_ITEMS=$(fetch_board_items 2>/dev/null) || { echo "[$(date 
docs/scheduler.md	158	0	prefixed	522-542	527-547	# --- Branch lookup: does a feat/issue-<N>-* branch exist on ori
docs/scheduler.md	184	0	prefixed	70-85	70-85	check_rate_limit() {
docs/scheduler.md	194	0	prefixed	1414-1454	1419-1459	declare -A STAGE_GUARD=(
docs/scheduler.md	201	0	prefixed	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/scheduler.md	201	0	bare	636-647	641-652	has_skip_label() {
docs/scheduler.md	203	0	prefixed	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/scheduler.md	207	0	prefixed	967-997	972-1002	stage_ci_gate() {
docs/scheduler.md	208	0	bare	1009-1026	1014-1031	stage_rescue_blocked() {
docs/scheduler.md	209	0	bare	1567-1572	1572-1577	FACTORY_RUNNING=$(count_factory_running)
docs/scheduler.md	210	0	bare	1581-1596	1586-1601	SESSION_WINDOW_PAUSED=false
docs/scheduler.md	211	0	bare	1036-1051	1041-1056	stage_orphan_sweep() {
docs/scheduler.md	212	0	bare	1606-1616	1611-1621	MAIN_IS_RED=false
docs/scheduler.md	214	0	bare	1058-1122	1063-1127	stage_conflict_resolve() {
docs/scheduler.md	215	0	bare	1125-1158	1130-1163	stage_review_triage() {
docs/scheduler.md	216	0	bare	1161-1207	1166-1212	stage_ready_implement() {
docs/scheduler.md	217	0	bare	1210-1264	1215-1269	stage_blocked_retry() {
docs/scheduler.md	218	0	bare	1267-1328	1272-1333	stage_plan() {
docs/scheduler.md	219	0	bare	1331-1395	1336-1400	stage_refine() {
docs/scheduler.md	220	0	bare	1402-1408	1407-1413	stage_epic_autopilot() {
docs/scheduler.md	223	0	prefixed	629-634	634-639	get_items_by_status() {
docs/scheduler.md	229	0	prefixed	115-119	115-121	is_issue_running() {
docs/scheduler.md	230	0	bare	242-244	244-246	count_refine_running() {
docs/scheduler.md	231	0	bare	121-123	123-125	count_factory_running() {
docs/scheduler.md	246	0	prefixed	323-355	325-357	end_gate_check() {
docs/scheduler.md	249	0	bare	363	368	dispatch_close() {
docs/scheduler.md	252	0	bare	756-779	761-784	get_new_comments() {
docs/scheduler.md	254	0	bare	845-933	850-938	classify_comments() {
docs/scheduler.md	255	0	bare	781-843	786-848	parse_comment_verdict() {
docs/scheduler.md	270	0	prefixed	543-568	548-573	branch_exists_for_issue() {
docs/scheduler.md	274	0	prefixed	488-509	493-514	delivery_skip_note() {
docs/scheduler.md	278	0	prefixed	383-400	388-405	dispatch() {
docs/scheduler.md	320	0	prefixed	53-59	53-59	if [ -f /workspace/project/.archon/.env ]; then
docs/scheduler.md	343	0	prefixed	1479-1482	1484-1487	WIP_DATA=$(fetch_wip_limits)
docs/scheduler.md	343	0	bare	1166-1167	1171-1172	if [ "$IN_PROGRESS_COUNT" -ge "$MAX_IN_PROGRESS" ]; then break; 
docs/scheduler.md	344	0	prefixed	1567-1572	1572-1577	FACTORY_RUNNING=$(count_factory_running)
docs/scheduler.md	351	0	prefixed	716-753	721-758	dependencies_met() {
docs/scheduler.md	352	0	bare	680-714	685-719	_scan_body_for_deps() {
docs/scheduler.md	383	0	prefixed	1174-1201	1179-1206	if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && is_above_
docs/scheduler.md	394	0	prefixed	1241	1246	EVAL_RESULT=$(evaluate_stop "$ISSUE" "implement" "$MAX_RETRIES")
docs/scheduler.md	394	0	bare	346	348	reset_retry "$issue_num"
docs/scheduler.md	394	0	bare	1151	1156	reset_retry "$ISSUE"
docs/scheduler.md	394	0	bare	1022	1027	reset_retry "$ISSUE" || true
docs/scheduler.md	395	0	bare	1308	1313	EVAL_RESULT=$(evaluate_stop "$ISSUE" "plan" "$REFINE_MAX_RETRIES
docs/scheduler.md	395	0	bare	289	291	reset_retry "${issue_num}:plan"
docs/scheduler.md	396	0	bare	1375	1380	EVAL_RESULT=$(evaluate_stop "$ISSUE" "refine" "$REFINE_MAX_RETRI
docs/scheduler.md	396	0	bare	253	255	reset_retry "${issue_num}:refine"
docs/scheduler.md	397	0	bare	1111	1116	increment_retry "${ISSUE}:resolve" || true
docs/scheduler.md	397	0	bare	1093	1098	EVAL_RESULT=$(evaluate_stop "$ISSUE" "resolve" "$MAX_RETRIES" --
docs/scheduler.md	398	0	bare	367	372	eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")
docs/scheduler.md	423	0	prefixed	1581-1596	1586-1601	SESSION_WINDOW_PAUSED=false
docs/scheduler.md	424	0	bare	1607	1612	[ -f "${SCHEDULER_STATE_DIR}/main-is-red" ] && MAIN_IS_RED=true
docs/scheduler.md	426	0	bare	159	161	touch "$RECHECK_STAMP_FILE"
docs/scheduler.md	427	0	bare	185	187	touch "$FIXER_STAMP_FILE"
docs/scheduler.md	435	0	prefixed	1467-1471	1472-1476	# --- ERR trap: log unhandled exits for post-mortem diagnosis --
docs/scheduler.md	442	0	bare	363	368	dispatch_close() {
docs/scheduler.md	456	0	prefixed	466-482	471-487	retry_or_skip_delivery_failure() {
docs/scheduler.md	457	0	bare	1083-1091	1088-1096	RESOLVE_DELIVERY_SKIP=""
docs/scheduler.md	457	0	bare	1107-1109	1112-1114	if [ -n "$RESOLVE_DELIVERY_SKIP" ]; then
docs/scheduler.md	477	0	prefixed	1581-1596	1586-1601	SESSION_WINDOW_PAUSED=false
docs/scheduler.md	485	0	bare	438-446	443-451	rollback_paused_retry() {
docs/scheduler.md	496	0	prefixed	1606-1616	1611-1621	MAIN_IS_RED=false
docs/scheduler.md	503	0	bare	153-162	155-164	main_red_recheck_check() {
docs/scheduler.md	508	0	bare	179-188	181-190	main_red_fixer_check() {
docs/scheduler.md	549	0	prefixed	246-280	248-282	spec_advance_check() {
docs/scheduler.md	550	0	bare	282-321	284-323	plan_advance_check() {
docs/scheduler.md	587	0	prefixed	1402-1408	1407-1413	stage_epic_autopilot() {
docs/scheduler.md	642	0	prefixed	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/scheduler.md	645	0	prefixed	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/scheduler.md	645	0	bare	1336-1343	1341-1348	# Handle spec-pending-review items first (before skip-label chec
docs/scheduler.md	646	0	prefixed	1272-1281	1277-1286	# Direct-to-PR plan auto-advance: handle before refine_skip_labe
docs/scheduler.md	647	0	prefixed	1348	1353	if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label
docs/scheduler.md	648	0	prefixed	268	270	if has_direct_to_pr_label "$item"; then
docs/scheduler.md	648	0	bare	309	311	if has_direct_to_pr_label "$item"; then
docs/scheduler.md	648	0	bare	326	328	has_direct_to_pr_label "$item" || return 1
docs/scheduler.md	648	0	bare	1348	1353	if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label
docs/scheduler.md	649	0	prefixed	1016	1021	if has_above_ceiling_label "$item"; then continue; fi
docs/scheduler.md	649	0	bare	1175	1180	if ! has_above_ceiling_label "$item"; then
docs/scheduler.md	649	0	bare	1220	1225	if has_above_ceiling_label "$item"; then continue; fi
docs/scheduler.md	683	0	prefixed	1632-1650	1637-1655	# --- Log cycle summary ---
docs/scheduler.md	711	0	prefixed	1488-1502	1493-1507	echo "[$(date -u +%FT%TZ)] probe=image_check image=${FACTORY_IMA
docs/scheduler.md	712	0	prefixed	1404-1405	1409-1410	AP_OUT=$(python3 "$FACTORY_CORE_CLI" epic-autopilot --once 2>&1)
docs/scheduler.md	713	0	bare	390	395	echo "[$(date -u +%FT%TZ)] dispatch command=\"${command}\""
docs/scheduler.md	713	0	bare	397	402	echo "[$(date -u +%FT%TZ)] dispatch_error command=\"${command}\"
docs/scheduler.md	714	0	bare	81	81	echo "[$(date -u +%FT%TZ)] rate_limit remaining=${remaining} sle
docs/scheduler.md	715	0	bare	980	985	echo "[$(date -u +%FT%TZ)] ci_gate issue=#${ISSUE} pr=#${PR_NUM}
docs/scheduler.md	716	0	bare	1020	1025	echo "[$(date -u +%FT%TZ)] blocked_rescue issue=#${ISSUE} action
docs/scheduler.md	717	0	bare	1041	1046	echo "[$(date -u +%FT%TZ)] sweep=orphaned_in_progress issue=#${I
docs/scheduler.md	718	0	bare	1106	1111	echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${P
docs/scheduler.md	718	0	bare	1118	1123	echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${P
docs/scheduler.md	719	0	bare	335	337	echo "[$(date -u +%FT%TZ)] end_gate issue=#${issue_num} pr=#${pr
docs/scheduler.md	719	0	bare	342	344	echo "[$(date -u +%FT%TZ)] end_gate issue=#${issue_num} pr=#${pr
docs/scheduler.md	720	0	bare	861	866	echo "  classify_comments #${issue_num}: cache hit verdict=${cac
docs/scheduler.md	720	0	bare	902	907	echo "  classify_comments #${issue_num}: fallback reason=api_err
docs/scheduler.md	720	0	bare	912	917	echo "  classify_comments #${issue_num}: fallback reason=unparse
docs/scheduler.md	720	0	bare	918	923	echo "  classify_comments #${issue_num}: verdict=${verdict}" >&2
docs/scheduler.md	721	0	bare	1176	1181	echo "[$(date -u +%FT%TZ)] ceiling_gate issue=#${ISSUE} action=a
docs/scheduler.md	722	0	bare	737-748	742-753	echo "[$(date -u +%FT%TZ)] dep_gate issue=#${issue_num} dep=#${d
docs/scheduler.md	723	0	bare	272-277	274-279	echo "[$(date -u +%FT%TZ)] spec_auto_advance issue=#${issue_num}
docs/scheduler.md	723	0	bare	313-318	315-320	echo "[$(date -u +%FT%TZ)] plan_auto_advance issue=#${issue_num}
docs/scheduler.md	724	0	bare	413	418	echo "[$(date -u +%FT%TZ)] circuit_breaker=trip issue=#${issue_n
docs/scheduler.md	725	0	bare	476	481	echo "[$(date -u +%FT%TZ)] delivery_gate issue=#${issue_num} pha
docs/scheduler.md	725	0	bare	1109	1114	echo "[$(date -u +%FT%TZ)] delivery_gate issue=#${ISSUE} phase=r
docs/scheduler.md	726	0	bare	1592	1597	echo "[$(date -u +%FT%TZ)] session_window_gate=active resume_at=
docs/scheduler.md	727	0	bare	445	450	echo "[$(date -u +%FT%TZ)] session_window_gate issue=#${issue_nu
docs/scheduler.md	728	0	bare	1441	1446	echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_windo
docs/scheduler.md	728	0	bare	1447	1452	echo "[$(date -u +%FT%TZ)] session_window_paused=true action=${S
docs/scheduler.md	728	0	bare	1611	1616	echo "[$(date -u +%FT%TZ)] session_window_paused=true action=ski
docs/scheduler.md	729	0	bare	1609	1614	echo "[$(date -u +%FT%TZ)] main_red_gate=active action=skip_impl
docs/scheduler.md	730	0	bare	160	162	echo "[$(date -u +%FT%TZ)] main_red_recheck=dispatched interval=
docs/scheduler.md	730	0	bare	186	188	echo "[$(date -u +%FT%TZ)] main_red_fixer=dispatched"
docs/scheduler.md	731	0	bare	547	552	echo "[$(date -u +%FT%TZ)] branch_probe_error issue=#${1} reason
docs/scheduler.md	731	0	bare	562	567	echo "[$(date -u +%FT%TZ)] branch_probe_error issue=#${1} reason
docs/scheduler.md	732	0	bare	1474	1479	echo "[$(date -u +%FT%TZ)] SCHED_UNHANDLED_ERR line=${line} exit
docs/scheduler.md	786	0	prefixed	1336-1343	1341-1348	# Handle spec-pending-review items first (before skip-label chec
docs/scheduler.md	787	0	bare	1272-1279	1277-1284	# Direct-to-PR plan auto-advance: handle before refine_skip_labe
docs/scheduler.md	788	0	bare	1281	1286	if has_refine_skip_label "$item"; then continue; fi
docs/scheduler.md	790	0	bare	286-303	288-305	local has_new
docs/scheduler.md	793	0	bare	1093-1097	1098-1102	EVAL_RESULT=$(evaluate_stop "$ISSUE" "resolve" "$MAX_RETRIES" --
docs/scheduler.md	794	0	bare	1100-1103	1105-1108	PR_NUM=$(get_pr_for_issue "$ISSUE")
docs/scheduler.md	795	0	bare	1111	1116	increment_retry "${ISSUE}:resolve" || true
docs/scheduler.md	806	0	prefixed	1254-1262	1259-1267	if [ -n "$(branch_exists_for_issue "$ISSUE")" ] || [ -n "$(get_p
docs/scheduler.md	811	0	bare	117	119	docker ps --no-trunc --format '{{.Command}}' 2>/dev/null | grep 
docs/scheduler.md	815	0	bare	243	245	docker ps --no-trunc --format '{{.Command}}' 2>/dev/null | grep 
docs/scheduler.md	816	0	bare	133	135	docker ps --no-trunc --format '{{.Command}}' 2>/dev/null | grep 
docs/scheduler.md	816	0	bare	167	169	docker ps --no-trunc --format '{{.Command}}' 2>/dev/null | grep 
docs/scheduler.md	825	0	prefixed	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/scheduler.md	825	0	bare	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/scheduler.md	828	0	prefixed	304-305	306-307	# Dispatch ceiling (#339): timer-based advance applies only to S
docs/scheduler.md	837	0	prefixed	306-308	308-310	if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && ! is_belo
docs/scheduler.md	839	0	prefixed	1398-1400	1403-1405	# Runs ONLY when this cycle dispatched nothing (starved), main i
docs/scheduler.md	874	0	prefixed	1613-1614	1618-1619	main_red_recheck_check
docs/scheduler.md	876	0	bare	152	154	# Callers guarantee a free factory slot (runs after the capacity
docs/scheduler.md	879	0	bare	336	338	if dispatch_close "$issue_num"; then
docs/scheduler.md	879	0	bare	1143	1148	if dispatch_close "$ISSUE"; then
docs/scheduler.md	880	0	bare	363	368	dispatch_close() {
docs/scheduler.md	890	0	bare	1416	1421	[stage_review_triage]=none
docs/scheduler.md	892	0	bare	1410-1413	1415-1418	# --- Stage guard-type table (#185): collapses the 3 verbatim MA
docs/scheduler.md	897	0	bare	1604-1605	1609-1610	# When present, skip Priority 1.5/2/3 (implementation dispatches
docs/scheduler.md	902	0	bare	720-721	725-726	body=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get --id "$issue
docs/scheduler.md	903	0	bare	740-745	745-750	if [ "$dep_gh_exit" -ne 0 ] || [ -z "$dep_state" ]; then
docs/scheduler.md	904	0	bare	747	752	if [ "$dep_status" != "Done" ]; then
docs/scheduler.md	911	0	bare	1189-1194	1194-1199	To proceed:
docs/scheduler.md	925	0	prefixed	572-574	577-579	# `--repo` is REQUIRED: the scheduler runs at /workspace (not a 
docs/scheduler.md	934	0	prefixed	76	76	if [ "$remaining" -le "$RATE_LIMIT_FLOOR" ]; then
docs/scheduler.md	936	0	bare	104-105	104-105	# SCHEDULER_STATE_DIR is forwarded explicitly, matching check_fa
docs/scheduler.md	938	0	bare	1493	1518	STALE_COUNT=$(docker ps -a --filter "name=mh-preview-" --format 
docs/run-lifecycle.md	53	0	prefixed	383-401	388-405	dispatch() {
docs/run-lifecycle.md	73	0	prefixed	262,1390	264,1395	if dispatch "Refine issue #${issue_num}"; then
docs/run-lifecycle.md	74	0	prefixed	298,1323	300,1328	if dispatch "Plan issue #${issue_num}"; then
docs/run-lifecycle.md	75	0	prefixed	1203,1259	1208,1264	if dispatch "Fix issue #${ISSUE}"; then
docs/run-lifecycle.md	76	0	prefixed	344,1149,1255	346,1154,1260	if dispatch "Continue issue #${issue_num}"; then
docs/run-lifecycle.md	77	0	prefixed	374	379	dispatch "Close issue #${issue_num}"
docs/run-lifecycle.md	77	0	bare	336	338	if dispatch_close "$issue_num"; then
docs/run-lifecycle.md	77	0	bare	1143	1148	if dispatch_close "$ISSUE"; then
docs/run-lifecycle.md	78	0	prefixed	1113	1118	if dispatch "Deconflict issue #${ISSUE}"; then
docs/run-lifecycle.md	79	0	prefixed	157	159	if dispatch "Recheck main"; then
docs/run-lifecycle.md	80	0	prefixed	183	185	if dispatch "Fix main"; then
docs/run-lifecycle.md	1134	0	prefixed	1113	1118	if dispatch "Deconflict issue #${ISSUE}"; then
docs/features.md	49	0	prefixed	1533	1538	while true; do
docs/features.md	49	0	bare	1431	1436	STAGE_ORDER=(stage_conflict_resolve stage_review_triage stage_re
docs/features.md	49	0	bare	1436	1441	dispatch_stage() {
docs/features.md	49	0	bare	383	388	dispatch() {
docs/features.md	49	0	bare	599	604	fetch_board_items() {
docs/features.md	49	0	bare	579-584	604-609	fetch_board_items() {
docs/features.md	49	1	bare	383	408	set_board_status() {
docs/features.md	50	0	prefixed	1568	1573	if factory_at_capacity "$FACTORY_RUNNING"; then
docs/features.md	50	0	bare	1283	1288	if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi
docs/features.md	50	0	bare	1350	1355	if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi
docs/features.md	50	0	bare	1166	1171	if [ "$IN_PROGRESS_COUNT" -ge "$MAX_IN_PROGRESS" ]; then break; 
docs/features.md	50	0	bare	1167	1172	if [ "$IN_REVIEW_COUNT" -ge "$MAX_IN_REVIEW" ]; then break; fi
docs/features.md	51	0	prefixed	70	70	check_rate_limit() {
docs/features.md	51	0	bare	1538	1543	check_rate_limit
docs/features.md	52	0	prefixed	1348	1353	if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label
docs/features.md	52	0	bare	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/features.md	52	0	bare	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/features.md	53	0	prefixed	246	248	spec_advance_check() {
docs/features.md	53	0	bare	282	284	plan_advance_check() {
docs/features.md	53	0	bare	1340	1345	spec_advance_check "$ISSUE" "$item"
docs/features.md	53	0	bare	1276	1281	plan_advance_check "$ISSUE" "$item"
docs/features.md	54	0	prefixed	716	721	dependencies_met() {
docs/features.md	54	0	bare	1168	1173	if ! dependencies_met "$ISSUE" "$BOARD_ITEMS"; then continue; fi
docs/features.md	54	0	bare	680	685	_scan_body_for_deps() {
docs/features.md	55	0	prefixed	1174	1179	if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && is_above_
docs/features.md	55	0	bare	306	308	if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && ! is_belo
docs/features.md	57	0	prefixed	967	972	stage_ci_gate() {
docs/features.md	57	0	bare	1558	1563	stage_ci_gate
docs/features.md	57	0	bare	588	593	failing_checks_for_pr() {
docs/features.md	58	0	prefixed	1009	1014	stage_rescue_blocked() {
docs/features.md	58	0	bare	1561	1566	stage_rescue_blocked
docs/features.md	59	0	prefixed	1036	1041	stage_orphan_sweep() {
docs/features.md	59	0	bare	1601	1606	dispatch_stage stage_orphan_sweep
docs/features.md	59	0	bare	115	115	is_issue_running() {
docs/features.md	60	0	prefixed	1058	1063	stage_conflict_resolve() {
docs/features.md	60	0	bare	514	519	check_pr_mergeable() {
docs/features.md	61	0	prefixed	1125	1130	stage_review_triage() {
docs/features.md	61	0	bare	845	850	classify_comments() {
docs/features.md	61	0	bare	895	900	result=$(echo "$prompt" | claude -p --model haiku 2>"$err_tmp")
docs/features.md	61	0	bare	756	761	get_new_comments() {
docs/features.md	61	0	bare	781	786	parse_comment_verdict() {
docs/features.md	62	0	prefixed	323	325	end_gate_check() {
docs/features.md	62	0	bare	1132	1137	if end_gate_check "$ISSUE" "$item"; then continue; fi
docs/features.md	63	0	prefixed	108	108	evaluate_stop() {
docs/features.md	63	0	bare	424	429	check_failure_signature() {
docs/features.md	63	0	bare	409	414	trip_to_blocked() {
docs/features.md	63	0	bare	438	443	rollback_paused_retry() {
docs/features.md	63	0	bare	466	471	retry_or_skip_delivery_failure() {
docs/features.md	63	0	bare	1067	1072	SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
docs/features.md	63	0	bare	1223	1228	SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
docs/features.md	63	0	bare	1285	1290	SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
docs/features.md	63	0	bare	1352	1357	SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
docs/features.md	64	0	prefixed	102	102	# "stopped=true|false reason=<enum|none>". loop_entry is always 
docs/features.md	65	0	prefixed	1581	1586	SESSION_WINDOW_PAUSED=false
docs/features.md	65	0	bare	1596	1601	fi
docs/features.md	66	0	prefixed	1606	1611	MAIN_IS_RED=false
docs/features.md	66	0	bare	153	155	main_red_recheck_check() {
docs/features.md	67	0	prefixed	180	182	[ "${MAIN_RED_AUTOFIX_ENABLED:-false}" = "true" ] || return 0
docs/features.md	69	0	prefixed	1402	1407	stage_epic_autopilot() {
docs/features.md	70	0	prefixed	611	616	off_board_ready_issues() {
docs/features.md	70	0	bare	1640	1645	if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
docs/features.md	101	0	prefixed	1521	1526	if [ -f /workspace/project/.factory/adapter.yaml ]; then
docs/features.md	140	0	prefixed	387	392	if [ "${FACTORY_MODEL_PROXY_ENABLED:-false}" = "true" ]; then
docs/features.md	154	0	prefixed	45	45	python3 "$FACTORY_PROVIDERS_CLI" preflight
docs/features.md	154	0	bare	254	256	python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$issue_num"
docs/features.md	154	0	bare	331	333	review_state=$(python3 "$FACTORY_PROVIDERS_CLI" codehost reviews
docs/features.md	154	0	bare	517	522	result=$(python3 "$FACTORY_PROVIDERS_CLI" codehost mergeable \
docs/features.md	154	0	bare	599-604	604-609	fetch_board_items() {
docs/features.md	154	0	bare	619	624	ready_json=$(python3 "$FACTORY_PROVIDERS_CLI" tracker list-items
docs/features.md	158	0	prefixed	14	14	source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/identity.sh"
docs/configuration.md	41	0	prefixed	35-42	35-42	read_config() {
docs/configuration.md	42	0	prefixed	47-59,383-400	47-59,388-405	# --- Provision the dispatch env file ---
docs/configuration.md	103	0	prefixed	1541,1570,1652	1546,1575,1657	BOARD_ITEMS=$(fetch_board_items 2>/dev/null) || { echo "[$(date 
docs/configuration.md	104	0	prefixed	1074,1086,1093,1230,1241	1079,1091,1098,1235,1246	rollback_paused_retry "$ISSUE" "resolve" "$SIG_VALUE" "${ISSUE}:
docs/configuration.md	105	0	prefixed	70-76	70-76	check_rate_limit() {
docs/configuration.md	106	0	prefixed	126-127,1564-1569	128-129,1569-1574	factory_at_capacity() {
docs/configuration.md	107	0	prefixed	154	156	[ "$MAIN_RED_RECHECK_ENABLED" = "true" ] || return 0
docs/configuration.md	108	0	prefixed	144,160	146,162	[ $(( now - last )) -ge $(( MAIN_RED_RECHECK_MINUTES * 60 )) ]
docs/configuration.md	109	0	prefixed	1011	1016	if [ "${BLOCKED_RESCUE_ENABLED:-true}" = "true" ]; then
docs/configuration.md	118	0	prefixed	1275,1283,1339,1350	1280,1288,1344,1355	if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFI
docs/configuration.md	119	0	prefixed	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/configuration.md	128	0	prefixed	271-277	273-279	if [ -n "$elapsed" ] && [ "$elapsed" -ge "$SPEC_GRACE_MINUTES" ]
docs/configuration.md	129	0	prefixed	312-318	314-320	if [ -n "$elapsed" ] && [ "$elapsed" -ge "$PLAN_GRACE_MINUTES" ]
docs/configuration.md	173	0	prefixed	1059	1064	[ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ] || return 0
docs/configuration.md	194	0	prefixed	306,1174	308,1179	if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && ! is_belo
docs/configuration.md	195	0	prefixed	1178,1190	1183,1195	--add "$ABOVE_CEILING_LABEL" || true
docs/configuration.md	200	0	prefixed	1402-1408	1407-1413	stage_epic_autopilot() {
docs/configuration.md	208	0	prefixed	1403	1408	if [ -z "$DISPATCHED" ] && [ "$MAIN_IS_RED" = "false" ] && [ "$S
docs/configuration.md	224	0	prefixed	180	182	[ "${MAIN_RED_AUTOFIX_ENABLED:-false}" = "true" ] || return 0
docs/configuration.md	227	0	prefixed	176	178	[ $(( now - last )) -ge $(( ${MAIN_RED_AUTOFIX_THROTTLE_MIN:-15}
docs/configuration.md	378	0	prefixed	53-59	53-59	if [ -f /workspace/project/.archon/.env ]; then
docs/configuration.md	404	0	prefixed	383-400	388-405	dispatch() {
docs/configuration.md	468	0	prefixed	122	124	docker ps --format '{{.Names}}' 2>/dev/null | grep -c "${FACTORY
docs/configuration.md	480	0	prefixed	387	392	if [ "${FACTORY_MODEL_PROXY_ENABLED:-false}" = "true" ]; then
docs/configuration.md	494	0	prefixed	10	10	SCHEDULER_STATE_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factor
docs/configuration.md	498	0	prefixed	19-20	19-20	FACTORY_CORE_CLI="${FACTORY_CORE_CLI:-/opt/dark-factory/scripts/
docs/configuration.md	500	0	prefixed	1460	1465	if [ "${SCHEDULER_SOURCE_ONLY:-0}" = "1" ]; then
docs/configuration.md	509	0	prefixed	24	24	REFINE_MAX_RETRIES="${REFINE_MAX_RETRIES:-3}"
docs/configuration.md	510	0	prefixed	1506-1514	1511-1519	STALE_PREVIEW_WARN_COUNT="${STALE_PREVIEW_WARN_COUNT:-3}"
docs/configuration.md	565	0	prefixed	9	9	SKIP_LABELS="needs-discussion,epic,ready-for-human"
docs/configuration.md	565	0	bare	640	645	IFS=',' read -ra SKIP_ARRAY <<< "$SKIP_LABELS"
docs/configuration.md	566	0	prefixed	23	23	REFINE_SKIP_LABELS="needs-discussion,epic,ready-for-human,spec-p
docs/configuration.md	567	0	prefixed	24	24	REFINE_MAX_RETRIES="${REFINE_MAX_RETRIES:-3}"
docs/configuration.md	568	0	prefixed	620	625	--label ready-for-agent --limit 200 2>/dev/null) || return 1
docs/configuration.md	568	0	bare	1346	1351	# Opt-in gate: only auto-refine Backlog items labelled ready-for
docs/configuration.md	569	0	prefixed	255,274,291,315	257,276,293,317	--remove "spec-pending-review" || true
docs/configuration.md	589	0	prefixed	1465	1470	read_config
docs/configuration.md	590	0	prefixed	53-56	53-56	if [ -f /workspace/project/.archon/.env ]; then
docs/configuration.md	623	0	prefixed	53-56	53-56	if [ -f /workspace/project/.archon/.env ]; then
docs/configuration.md	624	0	prefixed	9,23	9,23	SKIP_LABELS="needs-discussion,epic,ready-for-human"
```
<!-- remap579-table:end -->

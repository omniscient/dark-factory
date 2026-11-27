# Hosting Shadow Comparator — Rules-Only/Local Replay Instrument Implementation Plan

**Issue:** #563

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Copy every file block **byte for byte**: the
> preregistration example's digests and test 31 depend on exact bytes.

**Goal:** Build the instrument the approved spec
`docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md` describes,
and nothing more. That means four new modules under `bench/`, a pinned fixture tree, example
data, an operator doc, 41 test functions (spec tests 1–37, where tests 24, 25 and 27 are two
functions each, plus one end-to-end test), and four reference-doc edits. It also means filing the named `ready-for-human` operator follow-up. v1 compares a
`rules_only` arm with `local` arms only. There is **no cloud arm**, no cost estimate and no
savings claim (owner decisions A–C).

**Architecture:** There are five pieces:

- **`bench/hosting_shadow_schema.py`** makes no network call and starts no subprocess. It
  holds the validators, digests, prompt rendering, verdict parsing, the `rules_only` reading
  of the rubric, the outside-git-tree check and the run order. Both the launcher and the
  comparator import it.
- **`bench/shadow_runner.py`** (the launcher) runs the start-up refusals and answers
  `rules_only` units in-process. For every `local` unit it spawns
  `[sys.executable, "-I", <absolute path of _shadow_worker.py>, <absolute path of job.json>]`.
  The worker gets an environment of exactly
  `PATH`, a fresh `HOME`, `SHADOW_BASE_URL` and the arm's one secret, and a temporary
  directory that is deleted after every call. The launcher writes `rows.jsonl` and
  `manifest.json` to a new directory outside any git work tree.
- **`bench/_shadow_worker.py`** makes exactly one non-streaming `POST /v1/messages`. It never
  follows a redirect and never retries. It runs failures through `model_adapter`'s §4.6
  checks and classification, and prints one JSON object.
- **`bench/compare_hosting_variants.py`** applies fail-closed equality and completeness
  checks, then joins the oracle labels and assigns outcome classes. It reports per arm and
  per (provenance × split) cell, plus a preregistered-criteria check (`met | not met |
  not evaluable`). Its output is a `.md` or `.json` report outside any git work tree.
- **The frozen prompt and examples:** `rubric.pinned.md` is the `classify-preview` prompt,
  frozen and never read live. Next to it sit eight `examples/` fixtures and their labels, the
  only committed arms file (placeholders only) and a `status: example` preregistration.

**Tech Stack:** Python 3.12+ (CI pins 3.12; the image ships 3.14), `aiohttp`, `pyyaml`,
pytest. The only reused production code is `factory_core.providers.model_adapter`
(`check_response_status`, `check_json_body`, `check_response_model`,
`status_failure_class`, `classify_failure`, `AdapterRefusal`) and
`factory_core.providers.model_endpoint` (`CLOUD_CREDENTIALS`, `base_url_scope`). The tests
also use `factory_core.capability_suite.mock_server.start()`. All of it is imported
read-only, and none of it is edited.

**Verified while planning:** I built every file in this plan in a scratch copy of the
branch (`main` at `1628a6e` plus the refine commits) and staged the tasks in the order below:

- Each task's tests failed first, with the exact errors quoted, and then passed.
- The final state was 98 new test cases (41 test functions plus parametrisation), all
  passing.
- The full suite passed: `PYTHONPATH=scripts python -m pytest tests/` gave 4038 passed.
- `scripts/docs_refs_check.py` passed on the four reference docs.
- The plan's generator snippets reproduced the prototype's `rubric.pinned.md` and
  preregistration example byte for byte.
- That run used Python 3.14 and CI uses 3.12. Nothing here needs a feature newer than 3.12:
  PEP 701 f-strings are 3.12, and `datetime.fromisoformat` with `+00:00` is 3.11.

**Test environment assumptions:** about 20 runner tests launch into pytest's `tmp_path`, so
pytest's base temporary directory must be outside any git work tree (the default `/tmp/pytest-of-*`
is). A `--basetemp` inside a clone makes them fail with `LauncherRefusal`, which is the path rule
working as designed. `test_end_to_end_rules_only_run_reports` also needs the wall clock to be
later than the example preregistration's `registered_at` (`2026-11-27T00:00:00+00:00`).

**Hard limits honoured (spec Requirement 9, CLAUDE.md):**

- No file under `workflows/`, `scheduler.sh`, `entrypoint.sh`, `scripts/`, `config/`,
  `deploy/`, `.github/` or `commands/` changes.
- `bench/compare_variants.py`, `bench/run_suite.sh`,
  `scripts/factory_core/capability_suite/**`, `model_adapter.py` and `model_endpoint.py` are
  not touched. Editing `capability_suite/` would change `emit.suite_digest()` and break
  `PINNED_FIXTURE_SUITE_VERSION`.
- No gate, budget, breaker, side-effect level or registry entry changes.

**Archive note (memory [PATTERN] #42):** `bench/hosting_shadow.md`,
`bench/fixtures/preview_classifier/README.md` and the four module docstrings cite the design
spec by path. A later archive step may move **this plan** into `docs/archive/`. It must leave
the spec at `docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md`.

**Deliberate readings and tightenings.** Each of these is fail-closed and within the spec's
intent. They are recorded here so conformance can check them against the spec.

1. **Arms file: reserved `secret_env` names.** `secret_env` may not be `PATH`, `HOME`,
   `SHADOW_BASE_URL`, `GH_TOKEN` or `GITHUB_TOKEN`, on top of the spec's `CLOUD_CREDENTIALS`
   refusal. Any of these would collide with the worker's four-name allowlist or the token
   refusal.
2. **Labels file: pinned oracle question.** `oracle_question` must equal the pinned
   `ORACLE_QUESTION`, so the question is pinned in code as well as in the README.
3. **Arms file: `context_settings` is closed.** It accepts exactly `{context_tokens: <int ≥ 1>}`,
   following the spec's "closed key set at every level". A later ticket can widen it.
4. **Launcher: no-overwrite covers `manifest.json`.** The rule refuses an `--out-dir` that
   already holds `manifest.json` as well as `rows.jsonl`, because "nothing is appended to or
   overwritten" applies to both files.
5. **Comparator: SLO and worker-side timeouts.** `p95_duration_ms_slo` is `not met` in a
   cell holding a `worker_error: timeout` row, which is the spec's rule. It is also `not met`
   for a row whose `failure_class` is a `timeout_*` class, the same event seen one layer down
   in the worker's own aiohttp bound.
6. **Comparator: `max_false_skips_held_out` in dev cells.** The criterion is defined over
   held-out cells only, so in a dev cell it reports `not evaluable`.
7. **Launcher: parses the 2000-character `response_text`.** The launcher parses the
   `response_text` the worker prints, capped at 2000 characters per the spec. A reply longer
   than that cannot be the one-sentence output contract and is `schema_invalid`.
8. **Preregistration: `invalid_run_handling` is pinned.** It must equal the spec's literal
   sentence (`INVALID_RUN_HANDLING`).
9. **Manifest: `rubric_source_commit`.** The manifest also records this, parsed from the pin's
   header, so the comparator can print the rubric's source commit without opening the rubric.

**Review amendments (2026-11-27, from executing this plan end to end).** Tasks 1–6 and 8 ran
as written: every stated red/green result was observed, and the full suite gave 4038 passed
in the factory image (Python 3.14, uid 1000, `GH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` set).
Three things changed:

1. **Task 7 is one script run in one shell.** The original Steps 2–4 read `$FOLLOWUP` and
   `$REPO_SLUG` set in Step 1, but shell variables do not survive between Bash tool calls.
   Run separately, Step 2 wrote `#` with no number into `bench/hosting_shadow.md`, Step 3's
   guard matched any `#` heading and skipped the comment, and Step 4's `grep -c` still
   printed `0`. The original Step 1 also went on to `gh issue create` when the lookup
   itself failed, which would file a duplicate on a transient error. The script now aborts
   on a failed lookup, accepts a candidate only if its body carries the HTML marker, and
   refuses a non-numeric result.
2. **Comparator: failure spend with no failed row is `0`.** It was `null` ("undefined"),
   which reads as missing data on a clean run. It stays `null` when failed rows exist but
   none reported usage. Three assertions pin the three cases.
3. **Architecture summary** quotes the worker argv the code and spec test 35 use.

---

## File Structure

| File | Action | Responsibility | Task |
|---|---|---|---|
| `bench/fixtures/preview_classifier/rubric.pinned.md` | Create (generated) | Frozen `classify-preview` prompt + header (`source_commit`, line range, delimiter) | 1 |
| `bench/fixtures/preview_classifier/examples/fixtures.yaml` | Create | Eight example inputs `ex-01`…`ex-08` | 1 |
| `bench/fixtures/preview_classifier/examples/oracle_labels.yaml` | Create | Their example labels (`labeled_by: example-not-an-oracle`) | 1 |
| `bench/hosting_arms.example.yaml` | Create | The only committed arms file (`192.0.2.0/24`, placeholder names) | 1 |
| `bench/fixtures/preview_classifier/README.md` | Create | Oracle question, file formats, labelling rules | 1 |
| `bench/hosting_shadow_schema.py` | Create | Shared constants, validators, digests, render/parse, `rules_only_verdict`, `inside_git_tree`, `run_order` | 2 |
| `tests/test_bench_shadow_runner.py` | Create (Task 2), extend (Task 3) | Spec tests 1–19, 35–37 | 2, 3 |
| `bench/_shadow_worker.py` | Create | One model call, one JSON object on stdout | 3 |
| `bench/shadow_runner.py` | Create | Launcher: refusals, run order, worker spawn, rows + manifest | 3 |
| `bench/compare_hosting_variants.py` | Create | Comparator / report | 4 |
| `tests/test_bench_hosting_compare.py` | Create (Task 4), extend (Task 5) | Spec tests 20–34 + an end-to-end rules-only run | 4, 5 |
| `bench/preview_classifier_preregistration.example.yaml` | Create (generated) | `status: example` preregistration with the examples' real digests | 5 |
| `bench/hosting_shadow.md` | Create (Task 5), edit (Task 7) | Operator doc: invocation, `docker run` line, limits, how to read the report, follow-up number | 5, 7 |
| `docs/features.md` | Edit | New "Hosting shadow comparator" row beside "Replay bench" | 6 |
| `README.md` | Edit | Bench-section pointer + document-index line | 6 |
| `docs/configuration.md` | Edit | Three rows in "Variables not derived from a config key" → "Feature flags and switches" | 6 |
| `bench/baseline.md` | Edit | One sentence distinguishing the replay bench from the comparator | 6 |

Nothing else is created or modified. The follow-up issue and the one comment on #563
(Task 7) are the only GitHub writes beyond the normal PR.

---

## Task 1: Pin the rubric and commit the example data

The data files come first because the module tests in Tasks 2–5 load them. This task adds no
test of its own: the validators that check these files are written test-first in Task 2, and
spec tests 31 and 34 (Task 5) assert that they validate and that their digests match.

**Files:**
- Create: `bench/fixtures/preview_classifier/rubric.pinned.md` (generated)
- Create: `bench/fixtures/preview_classifier/examples/fixtures.yaml`
- Create: `bench/fixtures/preview_classifier/examples/oracle_labels.yaml`
- Create: `bench/hosting_arms.example.yaml`
- Create: `bench/fixtures/preview_classifier/README.md`

- [ ] **Step 1: Generate `rubric.pinned.md`.** Run this one-off snippet from the repo root. Do
  not commit the snippet. `source_commit` is the `main` commit the implement branch starts
  from (`git merge-base HEAD origin/main`). The file holds a header and then the node's
  `prompt` value exactly as `yaml.safe_load` returns it:

````bash
git fetch -q origin main
python3 - <<'PYEOF'
import subprocess
from pathlib import Path

import yaml

wf = Path("workflows/archon-dark-factory.yaml")
lines = wf.read_text().splitlines()
start = lines.index("  - id: classify-preview") + 2  # 1-based line of "    prompt: |"
assert lines[start - 1] == "    prompt: |", lines[start - 1]
end = next(i for i in range(start, len(lines)) if lines[i].startswith("    allowed_tools:"))
node = next(n for n in yaml.safe_load(wf.read_text())["nodes"] if n["id"] == "classify-preview")
commit = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], capture_output=True,
                        text=True, check=True).stdout.strip()
header = (
    "# classify-preview prompt, pinned for the hosting shadow comparator (#563)\n"
    "\n"
    "Frozen copy: the shadow runner never reads the live DAG file. Below the delimiter is the\n"
    "node's `prompt` value exactly as yaml.safe_load returns it. Re-pin only in a reviewed\n"
    "change that bumps RUNNER_VERSION; no test compares this file to the live DAG.\n"
    "\n"
    f"source_commit: {commit}\n"
    "source_file: workflows/archon-dark-factory.yaml\n"
    f"source_lines: {start}-{end}\n"
    "---8<--- prompt below ---\n"
)
out = Path("bench/fixtures/preview_classifier/rubric.pinned.md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes((header + node["prompt"]).encode("utf-8"))
print(f"pinned {out}: source_commit {commit}, lines {start}-{end}")
PYEOF
````

  Expected output: `pinned bench/fixtures/preview_classifier/rubric.pinned.md: source_commit
  <40-hex sha>, lines 863-896`. The commit is `1628a6e06144b6ea0e6d27b0f25b940a1b8a0739`
  unless `main` has moved. If the line range differs, `main` edited the DAG file after
  planning, which is fine because the snippet computes both values. Then check the delimiter
  and one substitution target:

```bash
grep -c '^---8<--- prompt below ---$' bench/fixtures/preview_classifier/rubric.pinned.md   # expect 1
grep -c 'Read the preview.enabled flag from .claude/skills/refinement/config.yaml.' bench/fixtures/preview_classifier/rubric.pinned.md   # expect 1
```

- [ ] **Step 2: Create `bench/fixtures/preview_classifier/examples/fixtures.yaml`** with exactly:

````yaml
# Example inputs for tooling tests only (#563). Hand-built; no evidential weight.
# Format: bench/fixtures/preview_classifier/README.md. The oracle lives in
# oracle_labels.yaml, which the shadow runner never opens.
schema_version: "1.0"
preview_enabled: true
fixtures:
  - fixture_id: ex-01
    provenance: synthetic
    split: dev
    name_only: [docs/features.md, README.md]
    stat: " docs/features.md | 2 +-\n README.md | 1 +\n 2 files changed, 2 insertions(+), 1 deletion(-)"
    title: "docs: clarify the replay bench row"
    labels: [documentation]
    labels_source: synthetic
  - fixture_id: ex-02
    provenance: synthetic
    split: dev
    name_only: [backend/tests/test_scanner.py]
    stat: " backend/tests/test_scanner.py | 14 ++++++++++++++\n 1 file changed, 14 insertions(+)"
    title: "test: cover the empty-watchlist scanner path"
    labels: [testing]
    labels_source: synthetic
  - fixture_id: ex-03
    provenance: synthetic
    split: dev
    name_only: [.github/workflows/ci.yml]
    stat: " .github/workflows/ci.yml | 3 ++-\n 1 file changed, 2 insertions(+), 1 deletion(-)"
    title: "ci: pin the python version"
    labels: [ci]
    labels_source: synthetic
  - fixture_id: ex-04
    provenance: synthetic
    split: dev
    name_only: [.archon/memory/architecture.md]
    stat: " .archon/memory/architecture.md | 4 ++++\n 1 file changed, 4 insertions(+)"
    title: "memory: record an architecture decision"
    labels: []
    labels_source: synthetic
  - fixture_id: ex-05
    provenance: synthetic
    split: dev
    name_only: [backend/app/api/scans.py]
    stat: " backend/app/api/scans.py | 9 +++++----\n 1 file changed, 5 insertions(+), 4 deletions(-)"
    title: "fix: scans endpoint returns 500 on an empty filter"
    labels: [bug]
    labels_source: synthetic
  - fixture_id: ex-06
    provenance: synthetic
    split: dev
    name_only: [docs/deploy.md, Dockerfile]
    stat: " Dockerfile | 2 +-\n docs/deploy.md | 3 ++-\n 2 files changed, 3 insertions(+), 2 deletions(-)"
    title: "docs: document the new base image"
    labels: [documentation]
    labels_source: synthetic
  - fixture_id: ex-07
    provenance: synthetic
    split: dev
    name_only: []
    stat: ""
    title: "chore: empty changeset"
    labels: []
    labels_source: synthetic
  - fixture_id: ex-08
    provenance: synthetic
    split: dev
    name_only: [scripts/diff_rank.py]
    stat: " scripts/diff_rank.py | 6 +++---\n 1 file changed, 3 insertions(+), 3 deletions(-)"
    title: "refactor: simplify hunk ranking"
    labels: [enhancement]
    labels_source: synthetic
````

- [ ] **Step 3: Create `bench/fixtures/preview_classifier/examples/oracle_labels.yaml`** with exactly:

````yaml
# Example oracle labels for tooling tests only (#563): labeled_by is
# example-not-an-oracle, so none of these is a human adjudication.
schema_version: "1.0"
oracle_question: "Would this changeset need live endpoint validation to catch a regression?"
labels:
  ex-01:
    oracle_label: false
    expected_fail_safe: false
    oracle_rationale: "Documentation only; nothing the running stack executes changes."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-02:
    oracle_label: false
    expected_fail_safe: false
    oracle_rationale: "Backend tests only; CI exercises them without a live stack."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-03:
    oracle_label: false
    expected_fail_safe: false
    oracle_rationale: "CI configuration only; no runtime code changes."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-04:
    oracle_label: false
    expected_fail_safe: false
    oracle_rationale: "Agent memory file only; not part of the running app."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-05:
    oracle_label: true
    expected_fail_safe: false
    oracle_rationale: "An API handler change; only a live endpoint shows the regression."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-06:
    oracle_label: true
    expected_fail_safe: false
    oracle_rationale: "The base image changes; the stack must be rebuilt to validate it."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-07:
    oracle_label: true
    expected_fail_safe: true
    oracle_rationale: "Empty changeset: insufficient evidence, so the fail-safe keeps the preview."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
  ex-08:
    oracle_label: true
    expected_fail_safe: false
    oracle_rationale: "A script the factory runs; no rubric row covers it, so it is treated as runtime."
    labeled_by: example-not-an-oracle
    labeled_at: "2026-11-27T00:00:00+00:00"
````

- [ ] **Step 4: Create `bench/hosting_arms.example.yaml`** with exactly:

````yaml
# Example arms file for bench/shadow_runner.py (#563). Placeholders only: a 192.0.2.0/24
# (TEST-NET-1) address and example names. Copy it OUTSIDE every git work tree before you
# fill in a real endpoint — the runner refuses an --arms path inside one. secret_env is the
# NAME of an environment variable, never a value.
arms:
  - arm_id: rules
    arm_kind: rules_only
  - arm_id: local-a
    arm_kind: local
    endpoint_id: example-local
    base_url: http://192.0.2.10:8080
    secret_env: SHADOW_LOCAL_API_KEY
    model_identity: {served_name: example-model, digest_or_revision: example-digest, quantisation: example-quant}
    runtime_stack: {name: example-runtime, version: "0.0.0", chat_template: example-template, context_settings: {context_tokens: 8192}}
    hardware: example-hardware
````

- [ ] **Step 5: Create `bench/fixtures/preview_classifier/README.md`** with exactly:

````markdown
# Preview-classifier fixture set (#563)

Inputs and oracle formats for the hosting shadow comparator (`bench/shadow_runner.py`,
`bench/compare_hosting_variants.py`; operator doc `bench/hosting_shadow.md`). Design:
`docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md`.

## The oracle question

Every label answers one question, pinned here and in `hosting_shadow_schema.ORACLE_QUESTION`:

> Would this changeset need live endpoint validation to catch a regression?

It is deliberately *not* the rubric's own categories. Label blind to every arm's output.
Correctness comes only from these human labels — never an LLM judge, reviewer agreement,
`outcome.score`, an ended turn, or agreement with the `rules_only` arm.

## Files

- `rubric.pinned.md` — the `classify-preview` node's prompt, frozen at the commit in its
  header. `rubric_sha` is the sha256 of the file's bytes. The runner never reads the live DAG.
- `fixtures.yaml` (inputs) — what a model may see: `fixture_id`, `provenance`
  (`historical | synthetic`), `split` (`dev | held_out`), `name_only`, `stat`, `title`,
  `labels`, `labels_source`, and `source` (historical only). `preview_enabled` is a set-level
  constant and must be `true` in v1. `fixture_set_digest` is the sha256 of the file's bytes.
- `oracle_labels.yaml` (labels) — `oracle_label`, `expected_fail_safe`, `oracle_rationale`,
  `labeled_by`, `labeled_at` per `fixture_id`. Only the comparator opens it; the runner has no
  argument that accepts it. `oracle_label_digest` is the sha256 of the file's bytes.

## Labelling rules

- A `historical` fixture's `labels` are the linked issue's labels as of the PR's creation
  (`labels_source: as_of_pr_created`) or empty (`dropped`) — never the labels it carries
  today, which would leak later state.
- `expected_fail_safe: true` only for empty, garbled or insufficient-evidence inputs, and it
  implies `oracle_label: true` (the prompt's own fail-safe line).
- Held-out cases (`split: held_out`) are hand-built synthetic adversarial and
  insufficient-evidence inputs with a written rationale: an empty changeset; a truncated or
  garbled changeset; a "documentation" label on a diff touching `backend/app/**`; near-miss
  paths (`docker-compose.preview.yml`, a `docs/` file beside `Dockerfile`); renames; a mixed
  skip-only/runtime diff; a prompt-injection title. Seal them — record the digests in a
  `registered` preregistration — before any arm runs; never use them to iterate on the prompt.
- The rubric is written in MarketHawk path shapes, so most real dark-factory diffs fall
  through to the fail-safe `true`. The synthetic set must deliberately cover the force-true
  and skip-only categories.

## `examples/`

Eight hand-built tooling fixtures (`ex-01`…`ex-08`, `labeled_by: example-not-an-oracle`).
They exercise the tooling and carry no evidential weight. The real fixture set, its labels and
the `registered` preregistration are committed by the operator follow-up named in
`bench/hosting_shadow.md`.
````

- [ ] **Step 6: Check that the YAML parses, then commit.**

```bash
python3 -c "import yaml; [yaml.safe_load(open(p)) for p in ('bench/fixtures/preview_classifier/examples/fixtures.yaml','bench/fixtures/preview_classifier/examples/oracle_labels.yaml','bench/hosting_arms.example.yaml')]; print('ok')"
```
Expected: `ok`

```bash
git add bench/fixtures/preview_classifier bench/hosting_arms.example.yaml
git commit -m "feat(#563): pin the classify-preview rubric and add example fixtures"
```

---

## Task 2: Shared schema module (`bench/hosting_shadow_schema.py`)

**Files:**
- Create: `tests/test_bench_shadow_runner.py` (first part: spec tests 4, 5, 16, 17 schema half, 18, 19)
- Create: `bench/hosting_shadow_schema.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_bench_shadow_runner.py` with
  exactly the content below. The standard-library and `aiohttp` imports that Task 3 needs
  are already here. The file ends partway through spec test 17, and Task 3 appends the
  request-side half.

````python
"""Hosting shadow runner, worker and schema module (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md
(Tests 1-19 and 35-37). Hermetic: the only endpoints are mock_server.start() and the in-test
aiohttp stub, both on 127.0.0.1; gh is never invoked.
"""
import ast
import asyncio
import inspect
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))

import hosting_shadow_schema as schema  # noqa: E402
from factory_core.providers import model_endpoint  # noqa: E402

EXAMPLES = BENCH / "fixtures" / "preview_classifier" / "examples"
EXAMPLE_FIXTURES = EXAMPLES / "fixtures.yaml"
SECRET_ENV = "SHADOW_TEST_KEY"
SECRET = "sentinel-secret-5f0c2a"
SERVED = "stub-model"
NEW_MODULES = ("hosting_shadow_schema.py", "shadow_runner.py", "_shadow_worker.py",
               "compare_hosting_variants.py")


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """CI and developer shells often carry these; only tests that set them deliberately may
    see them, so a refusal is exercised, never bypassed."""
    for name in (*schema.REPO_TOKENS, *model_endpoint.CLOUD_CREDENTIALS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(SECRET_ENV, SECRET)


def local_arm(url, arm_id="local-a", secret_env=SECRET_ENV, served=SERVED):
    return {"arm_id": arm_id, "arm_kind": "local", "endpoint_id": "stub-endpoint",
            "base_url": url, "secret_env": secret_env,
            "model_identity": {"served_name": served, "digest_or_revision": "stub-digest",
                               "quantisation": "stub-quant"},
            "runtime_stack": {"name": "stub-runtime", "version": "0.0.0",
                              "chat_template": "stub-template",
                              "context_settings": {"context_tokens": 8192}},
            "hardware": "stub-hardware"}


def write_arms(tmp_path, *arms, name="arms.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                                             *arms]}))
    return path


def write_fixtures(tmp_path, fixtures, name="fixtures.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"schema_version": "1.0", "preview_enabled": True,
                                    "fixtures": fixtures}))
    return path


def fixture(fid, paths=("backend/app/x.py",), title="t", labels=()):
    return {"fixture_id": fid, "provenance": "synthetic", "split": "dev",
            "name_only": list(paths), "stat": "", "title": title, "labels": list(labels),
            "labels_source": "synthetic"}


# 4
@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_local_arm_with_cloud_credential_name_raises(tmp_path, name):
    with pytest.raises(schema.ShadowSchemaError, match="cloud model credential"):
        schema.load_arms(write_arms(tmp_path, local_arm("http://127.0.0.1:9", secret_env=name)))


# 5
def test_unknown_arm_kind_raises(tmp_path):
    for kind in ("cloud", "anything-else"):
        path = tmp_path / f"{kind}.yaml"
        path.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                                                 {"arm_id": "x", "arm_kind": kind}]}))
        with pytest.raises(schema.ShadowSchemaError, match="arm_kind"):
            schema.load_arms(path)
    none = tmp_path / "none.yaml"
    none.write_text(yaml.safe_dump({"arms": [local_arm("http://127.0.0.1:9")]}))
    two = tmp_path / "two.yaml"
    two.write_text(yaml.safe_dump({"arms": [{"arm_id": "r1", "arm_kind": "rules_only"},
                                            {"arm_id": "r2", "arm_kind": "rules_only"}]}))
    for path in (none, two):
        with pytest.raises(schema.ShadowSchemaError, match="exactly one rules_only"):
            schema.load_arms(path)


# 16
def test_run_order_is_deterministic_and_balanced():
    fixtures = [f"f-{i}" for i in range(7)]
    arms = ["a", "b", "c"]
    first = schema.run_order(fixtures, arms, 3, seed=11)
    assert first == schema.run_order(fixtures, arms, 3, seed=11)
    assert first != schema.run_order(fixtures, arms, 3, seed=12)
    assert [u["run_order_position"] for u in first] == list(range(len(first)))
    for r in range(3):
        rep = [u for u in first if u["repetition"] == r]
        assert sorted({u["fixture_id"] for u in rep}) == sorted(fixtures)
        for position in range(len(arms)):
            counts = {arm: 0 for arm in arms}
            for start in range(0, len(rep), len(arms)):
                counts[rep[start + position]["arm_id"]] += 1
            assert max(counts.values()) - min(counts.values()) <= 1


# 18
def test_parse_verdict_is_strict():
    assert schema.parse_verdict('{"needs_preview": false, "category": "docs"}') is False
    assert schema.parse_verdict(' {"needs_preview": true}\n') is True
    for text in ('```json\n{"needs_preview": true}\n```', 'Sure: {"needs_preview": true}',
                 '{"needs_preview": "true"}', '{"category": "docs"}',
                 '{"needs_preview": true, "needs_preview": false}',
                 '{"needs_preview": true, "score": NaN}', '[true]', "", None):
        assert schema.parse_verdict(text) is None, text


# 19
@pytest.mark.parametrize("paths,expected", [
    ([], True),
    (["backend/app/main.py"], True), (["alembic/versions/0001.py"], True),
    (["frontend/src/App.tsx"], True), (["dark-factory/seed/x.sql"], True),
    (["requirements.txt"], True), (["web/package.json"], True), (["package-lock.json"], True),
    (["Dockerfile"], True), (["deploy/Dockerfile.dev"], True),
    (["docker-compose.preview.yml"], True), ([".env.example"], True),
    (["docs/x.txt"], False), (["Docs/x.txt"], False), (["guide.md"], False),
    (["a/b/README.md"], False), (["x.mdx"], False), (["LICENSE"], False),
    ([".archon/memory/a.md"], False), ([".claude/skills/x/SKILL.md"], False),
    (["workflows/archon-dark-factory.yaml"], False), (["workflows/sub/x.yml"], False),
    (["backend/tests/test_a.py"], False), (["dark-factory/tests/t.sh"], False),
    (["web/a.test.ts"], False), (["web/a.test.tsx"], False), (["web/a.spec.ts"], False),
    (["web/a.spec.tsx"], False), (["conftest.py"], False), (["pkg/a_test.py"], False),
    ([".github/workflows/ci.yml"], False), ([".gitignore"], False),
    ([".pre-commit-config.yaml"], False), ([".eslintrc.json"], False),
    ([".prettierrc"], False), (["ruff.toml"], False), ([".ruff.toml"], False),
    ([".flake8"], False), (["mypy.ini"], False), ([".mypy.ini"], False),
    ([".editorconfig"], False),
    (["docs/a.md", "backend/app/x.py"], True), (["docs/a.md", "scripts/x.py"], True),
    (["DOCS/x.txt"], True), (["docs/Dockerfile.dev"], True), (["scripts/x.py"], True),
])
def test_rules_only_verdict_table(paths, expected):
    assert schema.rules_only_verdict(paths) is expected


RUBRIC_STUB = ("K: " + schema.KILL_SWITCH_LINE + "\nC: " + schema.CHANGESET_TARGET
               + "\nT: " + schema.TITLE_TARGET + "\nL: " + schema.LABELS_TARGET + "\n")


# 17
def test_render_prompt_substitutions(tmp_path):
    fx = fixture("f-1", paths=("a.py", "b.md"), title="t " + schema.LABELS_TARGET + " "
                 + schema.CHANGESET_TARGET, labels=("bug", "docs"))
    fx["stat"] = " a.py | 1 +"
    rendered = schema.render_prompt(RUBRIC_STUB, fx)
    assert ("C: === Changed files (main...HEAD) ===\na.py\nb.md\n=== Stat ===\n a.py | 1 +"
            in rendered)
    assert "T: t $fetch-issue.output.labels $preview-changeset.output\n" in rendered
    assert 'L: ["bug", "docs"]\n' in rendered
    assert "K: " + schema.KILL_SWITCH_STATED in rendered
    assert rendered.endswith("\n\n" + schema.OUTPUT_CONTRACT_SUFFIX)
    for broken in (RUBRIC_STUB.replace(schema.TITLE_TARGET, "x"),
                   RUBRIC_STUB + schema.LABELS_TARGET):
        with pytest.raises(schema.ShadowSchemaError, match="exactly once"):
            schema.render_prompt(broken, fx)
````

- [ ] **Step 2: Run it and check that it fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py -q
```
Expected: a collection error, `ModuleNotFoundError: No module named 'hosting_shadow_schema'`.

- [ ] **Step 3: Create `bench/hosting_shadow_schema.py`** with exactly:

````python
"""Shared definitions for the hosting shadow comparator (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md.

No network call and no subprocess: constants, file/row validation, digests, prompt
rendering, verdict parsing, the rules-only reading of the rubric, the outside-git-tree
check and the run order. shadow_runner.py and compare_hosting_variants.py import this
module; _shadow_worker.py does not.
"""
import fnmatch
import hashlib
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from factory_core.providers import model_endpoint  # noqa: E402

SCHEMA_VERSION = "1.0"
# The "harness" identity of this replay (no Claude CLI is involved). Bump it whenever
# rules_only_verdict's reading of the rubric, the row shape or the request changes.
RUNNER_VERSION = "1"
PROMPT_SUBSTITUTIONS_VERSION = "1"
ARM_KINDS = ("rules_only", "local")
PROVENANCES = ("historical", "synthetic")
SPLITS = ("dev", "held_out")
PREREGISTRATION_STATUSES = ("example", "registered")
ORACLE_QUESTION = ("Would this changeset need live endpoint validation to catch a "
                   "regression?")
INVALID_RUN_HANDLING = ("all rows retained; schema_invalid and infrastructure_invalid count "
                        "in every denominator; no row is dropped or rerun")
REQUEST_PARAMS = {"max_tokens": 512, "temperature": 0, "connect_seconds": 10,
                  "total_seconds": 300}
UNSUPPORTED_MEASUREMENTS = ("queue_ms", "ttft_ms", "prefill_ms", "decode_ms",
                            "peak_memory_bytes", "oom")
USAGE_KEYS = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
              "gen_ai.usage.cache_read_input_tokens",
              "gen_ai.usage.cache_creation_input_tokens")
ROW_KEYS = (
    "schema_version", "runner_version", "fixture_id", "arm_id", "arm_kind", "repetition",
    "run_order_seed", "run_order_position", "cold_or_warm", "concurrency", "rubric_sha",
    "fixture_set_digest", "prompt_substitutions_version", "preview_enabled",
    "request_params", "endpoint_id", "hosting_location", "role", "model_identity",
    "runtime_stack", "hardware", "identity_source", "served_name_verified", "http_status",
    "parse_status", "predicted_needs_preview", "failure_class", "worker_error",
    "response_text", *USAGE_KEYS, "duration_ms", "unsupported_measurements", "cost_usd",
    "energy_joules", "energy_provenance", "started_at", "completed_at",
)

RUBRIC_PATH = Path(__file__).resolve().parent / "fixtures" / "preview_classifier" / "rubric.pinned.md"
RUBRIC_DELIMITER = "---8<--- prompt below ---"
CHANGESET_TARGET = "$preview-changeset.output"
TITLE_TARGET = "$fetch-issue.output.title"
LABELS_TARGET = "$fetch-issue.output.labels"
# Deliberate substitution 1 (kill-switch): the replay has no file to read and no tools.
KILL_SWITCH_LINE = "Read the preview.enabled flag from .claude/skills/refinement/config.yaml."
KILL_SWITCH_STATED = "preview.enabled is true (stated here; there is no file to read)."
# Deliberate substitution 2 (output contract): the local path refuses output_format.
OUTPUT_CONTRACT_SUFFIX = (
    'Respond with only a JSON object and nothing else: {"needs_preview": <true|false>, '
    '"category": "<code|docs|config|tests|ci|mixed>", "reason": "<one sentence>"}')

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")  # model_endpoint._SECRET_NAME_RE's rule
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")
# Names the worker environment already uses: an arm's secret must not shadow one.
_WORKER_RESERVED = ("PATH", "HOME", "SHADOW_BASE_URL")

# rules_only_verdict's reading of the classify-preview rubric (runner_version 1).
FORCE_TRUE_PREFIXES = ("backend/app/", "alembic/versions/", "frontend/src/",
                       "dark-factory/seed/")
FORCE_TRUE_NAMES = ("requirements.txt", "package.json", "package-lock.json", "Dockerfile*",
                    "docker-compose*", ".env*")
SKIP_PREFIXES = ("docs/", "Docs/", ".archon/", ".claude/", "backend/tests/",
                 "dark-factory/tests/", ".github/")
SKIP_WORKFLOW_NAMES = ("*.yaml", "*.yml")
SKIP_NAMES = ("*.md", "*.mdx", "LICENSE", "*.test.ts", "*.test.tsx", "*.spec.ts",
              "*.spec.tsx", "conftest.py", "*_test.py", ".gitignore",
              ".pre-commit-config.yaml", ".eslintrc*", ".prettierrc*", "ruff.toml",
              ".ruff.toml", ".flake8", "mypy.ini", ".mypy.ini", ".editorconfig")


class ShadowSchemaError(Exception):
    """A fixtures/labels/arms/preregistration/rows problem: names the file, the key path and
    the rule — never a value, so a base_url or a secret is never echoed."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load that refuses a duplicate mapping key instead of keeping the last one."""


def _unique_mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(None, None, "duplicate key",
                                                    key_node.start_mark)
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                 _unique_mapping)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_yaml(path):
    try:
        return yaml.load(Path(path).read_bytes(), Loader=_UniqueKeyLoader)  # noqa: S506 (safe loader)
    except yaml.YAMLError as e:
        problem = getattr(e, "problem", None) or "not valid YAML"
        raise ShadowSchemaError(f"{path}: {problem} (yaml.safe_load rules, unique keys)") from None
    except OSError:
        raise ShadowSchemaError(f"{path}: cannot be read") from None


def _mapping(obj, where, required, optional=()):
    if not isinstance(obj, dict):
        raise ShadowSchemaError(f"{where}: must be a mapping")
    unknown = sorted(str(k) for k in obj if k not in required and k not in optional)
    if unknown:
        raise ShadowSchemaError(f"{where}: unknown key(s) {', '.join(unknown)}")
    missing = [k for k in required if k not in obj]
    if missing:
        raise ShadowSchemaError(f"{where}: missing required key(s) {', '.join(missing)}")
    return obj


def _str(value, where, *, empty_ok=False):
    if not isinstance(value, str) or (not empty_ok and not value):
        raise ShadowSchemaError(f"{where}: must be a {'' if empty_ok else 'non-empty '}string")
    return value


def _int(value, where, *, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ShadowSchemaError(f"{where}: must be an integer"
                                + (f" >= {minimum}" if minimum is not None else ""))
    return value


def _bool(value, where):
    if type(value) is not bool:
        raise ShadowSchemaError(f"{where}: must be true or false")
    return value


def _rate(value, where):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ShadowSchemaError(f"{where}: must be a number between 0 and 1")
    return value


def _str_list(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ShadowSchemaError(f"{where}: must be a list of non-empty strings")
    return value


def _choice(value, where, allowed):
    if value not in allowed:
        raise ShadowSchemaError(f"{where}: must be one of {', '.join(allowed)}")
    return value


def parse_timestamp(value, where) -> datetime:
    """An ISO8601 string with a timezone (quote it in YAML so safe_load keeps it a string)."""
    _str(value, where)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ShadowSchemaError(f"{where}: must be an ISO8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ShadowSchemaError(f"{where}: must carry a timezone")
    return parsed


def _sha(value, where):
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise ShadowSchemaError(f"{where}: must be a lowercase sha256 hex digest")
    return value


def load_fixtures(path) -> dict:
    """The inputs file: what a model may see, and nothing else."""
    doc = _mapping(_load_yaml(path), f"{path}", ("schema_version", "preview_enabled",
                                                 "fixtures"))
    _choice(doc["schema_version"], f"{path}: schema_version", (SCHEMA_VERSION,))
    if doc["preview_enabled"] is not True:
        raise ShadowSchemaError(f"{path}: preview_enabled must be true in v1")
    if not isinstance(doc["fixtures"], list) or not doc["fixtures"]:
        raise ShadowSchemaError(f"{path}: fixtures must be a non-empty list")
    seen = set()
    for i, fx in enumerate(doc["fixtures"]):
        where = f"{path}: fixtures[{i}]"
        _mapping(fx, where, ("fixture_id", "provenance", "split", "name_only", "stat", "title",
                             "labels", "labels_source"), ("source",))
        fid = _str(fx["fixture_id"], f"{where}.fixture_id")
        if fid in seen:
            raise ShadowSchemaError(f"{where}.fixture_id: duplicate fixture_id")
        seen.add(fid)
        _choice(fx["provenance"], f"{where}.provenance", PROVENANCES)
        _choice(fx["split"], f"{where}.split", SPLITS)
        if not isinstance(fx["name_only"], list) or not all(
                isinstance(p, str) and p for p in fx["name_only"]):
            raise ShadowSchemaError(f"{where}.name_only: must be a list of non-empty paths")
        _str(fx["stat"], f"{where}.stat", empty_ok=True)
        _str(fx["title"], f"{where}.title", empty_ok=True)
        if not isinstance(fx["labels"], list) or not all(
                isinstance(v, str) and v for v in fx["labels"]):
            raise ShadowSchemaError(f"{where}.labels: must be a list of non-empty strings")
        if fx["provenance"] == "historical":
            _choice(fx["labels_source"], f"{where}.labels_source",
                    ("as_of_pr_created", "dropped"))
            if fx["labels_source"] == "dropped" and fx["labels"]:
                raise ShadowSchemaError(f"{where}.labels: must be empty when labels_source is "
                                        "dropped")
            if "source" not in fx:
                raise ShadowSchemaError(f"{where}: a historical fixture requires source")
            src = _mapping(fx["source"], f"{where}.source", ("pr", "merge_sha", "issue"))
            _int(src["pr"], f"{where}.source.pr", minimum=1)
            _str(src["merge_sha"], f"{where}.source.merge_sha")
            _int(src["issue"], f"{where}.source.issue", minimum=1)
        else:
            _choice(fx["labels_source"], f"{where}.labels_source", ("synthetic",))
            if "source" in fx:
                raise ShadowSchemaError(f"{where}: a synthetic fixture carries no source")
    return doc


def load_labels(path, fixture_ids) -> dict:
    """The oracle. Only the comparator opens this file."""
    doc = _mapping(_load_yaml(path), f"{path}", ("schema_version", "oracle_question", "labels"))
    _choice(doc["schema_version"], f"{path}: schema_version", (SCHEMA_VERSION,))
    if doc["oracle_question"] != ORACLE_QUESTION:
        raise ShadowSchemaError(f"{path}: oracle_question must be the pinned question in "
                                "bench/fixtures/preview_classifier/README.md")
    labels = doc["labels"]
    if not isinstance(labels, dict):
        raise ShadowSchemaError(f"{path}: labels must be a mapping of fixture_id to label")
    for fid, label in labels.items():
        where = f"{path}: labels.{fid}"
        if fid not in fixture_ids:
            raise ShadowSchemaError(f"{where}: no fixture with this fixture_id")
        _mapping(label, where, ("oracle_label", "expected_fail_safe", "oracle_rationale",
                                "labeled_by", "labeled_at"))
        _bool(label["oracle_label"], f"{where}.oracle_label")
        _bool(label["expected_fail_safe"], f"{where}.expected_fail_safe")
        if label["expected_fail_safe"] and not label["oracle_label"]:
            raise ShadowSchemaError(f"{where}: expected_fail_safe: true implies "
                                    "oracle_label: true")
        _str(label["oracle_rationale"], f"{where}.oracle_rationale")
        _str(label["labeled_by"], f"{where}.labeled_by")
        parse_timestamp(label["labeled_at"], f"{where}.labeled_at")
    missing = sorted(set(fixture_ids) - set(labels))
    if missing:
        raise ShadowSchemaError(f"{path}: labels: no label for fixture(s) {', '.join(missing)}")
    return doc


def validate_base_url(url, where) -> None:
    """model_endpoint._validate_base_url's rules (private there, so re-implemented): http(s)
    with a host, no embedded credentials, no query or fragment. Never echoes the value."""
    if not isinstance(url, str) or not url:
        raise ShadowSchemaError(f"{where}: base_url is required for a local arm")
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise ShadowSchemaError(f"{where}: base_url is not a well-formed URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ShadowSchemaError(f"{where}: base_url must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ShadowSchemaError(f"{where}: base_url must not embed credentials — use "
                                "secret_env")
    if parts.query or parts.fragment:
        raise ShadowSchemaError(f"{where}: base_url must not carry a query string or fragment")


def load_arms(path) -> list:
    doc = _mapping(_load_yaml(path), f"{path}", ("arms",))
    arms = doc["arms"]
    if not isinstance(arms, list) or not arms:
        raise ShadowSchemaError(f"{path}: arms must be a non-empty list")
    seen = set()
    for i, arm in enumerate(arms):
        where = f"{path}: arms[{i}]"
        if not isinstance(arm, dict):
            raise ShadowSchemaError(f"{where}: must be a mapping")
        _choice(arm.get("arm_kind"), f"{where}.arm_kind", ARM_KINDS)
        if arm["arm_kind"] == "rules_only":
            _mapping(arm, where, ("arm_id", "arm_kind"))
        else:
            _mapping(arm, where, ("arm_id", "arm_kind", "endpoint_id", "base_url", "secret_env",
                                  "model_identity", "runtime_stack", "hardware"))
            if not isinstance(arm["endpoint_id"], str) or not _ID_RE.match(arm["endpoint_id"]):
                raise ShadowSchemaError(f"{where}.endpoint_id: must match {_ID_RE.pattern}")
            validate_base_url(arm["base_url"], f"{where}.base_url")
            secret = arm["secret_env"]
            if not isinstance(secret, str) or not _SECRET_NAME_RE.match(secret):
                raise ShadowSchemaError(f"{where}.secret_env: must name an environment variable "
                                        f"({_SECRET_NAME_RE.pattern})")
            if secret in model_endpoint.CLOUD_CREDENTIALS:
                raise ShadowSchemaError(f"{where}.secret_env: {secret} is a cloud model "
                                        "credential; a local arm never carries one")
            if secret in REPO_TOKENS or secret in _WORKER_RESERVED:
                raise ShadowSchemaError(f"{where}.secret_env: {secret} is reserved")
            mi = _mapping(arm["model_identity"], f"{where}.model_identity",
                          ("served_name", "digest_or_revision", "quantisation"))
            for key in mi:
                _str(mi[key], f"{where}.model_identity.{key}")
            rs = _mapping(arm["runtime_stack"], f"{where}.runtime_stack",
                          ("name", "version", "chat_template", "context_settings"))
            for key in ("name", "version", "chat_template"):
                _str(rs[key], f"{where}.runtime_stack.{key}")
            cs = _mapping(rs["context_settings"], f"{where}.runtime_stack.context_settings",
                          ("context_tokens",))
            _int(cs["context_tokens"], f"{where}.runtime_stack.context_settings.context_tokens",
                 minimum=1)
            _str(arm["hardware"], f"{where}.hardware")
        if not isinstance(arm["arm_id"], str) or not _ID_RE.match(arm["arm_id"]):
            raise ShadowSchemaError(f"{where}.arm_id: must match {_ID_RE.pattern}")
        if arm["arm_id"] in seen:
            raise ShadowSchemaError(f"{where}.arm_id: duplicate arm_id")
        seen.add(arm["arm_id"])
    if sum(1 for a in arms if a["arm_kind"] == "rules_only") != 1:
        raise ShadowSchemaError(f"{path}: arms must contain exactly one rules_only arm")
    return arms


def load_preregistration(path) -> dict:
    doc = _mapping(_load_yaml(path), f"{path}", ("preview_classifier_preregistration",))
    where = f"{path}: preview_classifier_preregistration"
    pre = _mapping(doc["preview_classifier_preregistration"], where, (
        "schema_version", "status", "registered_by", "registered_at", "fixture_set_digest",
        "oracle_label_digest", "rubric_sha", "repetitions", "seed", "criteria",
        "invalid_run_handling"))
    _choice(pre["schema_version"], f"{where}.schema_version", (SCHEMA_VERSION,))
    _choice(pre["status"], f"{where}.status", PREREGISTRATION_STATUSES)
    _str(pre["registered_by"], f"{where}.registered_by")
    parse_timestamp(pre["registered_at"], f"{where}.registered_at")
    for key in ("fixture_set_digest", "oracle_label_digest", "rubric_sha"):
        _sha(pre[key], f"{where}.{key}")
    _int(pre["repetitions"], f"{where}.repetitions", minimum=1)
    _int(pre["seed"], f"{where}.seed")
    crit = _mapping(pre["criteria"], f"{where}.criteria", (
        "max_false_skips_held_out", "max_false_skip_rate", "min_schema_validity_rate",
        "min_coverage", "p95_duration_ms_slo", "min_false_preview_rate_reduction_vs_rules_only"))
    _int(crit["max_false_skips_held_out"], f"{where}.criteria.max_false_skips_held_out",
         minimum=0)
    for key in ("max_false_skip_rate", "min_schema_validity_rate", "min_coverage",
                "min_false_preview_rate_reduction_vs_rules_only"):
        _rate(crit[key], f"{where}.criteria.{key}")
    _int(crit["p95_duration_ms_slo"], f"{where}.criteria.p95_duration_ms_slo", minimum=1)
    if pre["invalid_run_handling"] != INVALID_RUN_HANDLING:
        raise ShadowSchemaError(f"{where}.invalid_run_handling: must be the pinned v1 rule")
    return pre


def load_rubric(path=RUBRIC_PATH) -> dict:
    """rubric.pinned.md: a header, the delimiter line, then the classify-preview prompt exactly
    as yaml.safe_load returns it. rubric_sha is the sha256 of the whole file's bytes."""
    data = Path(path).read_bytes()
    head, sep, prompt = data.decode("utf-8").partition(RUBRIC_DELIMITER + "\n")
    if not sep:
        raise ShadowSchemaError(f"{path}: missing the delimiter line {RUBRIC_DELIMITER!r}")
    commit = re.search(r"^source_commit: ([0-9a-f]{40})$", head, re.MULTILINE)
    if not commit:
        raise ShadowSchemaError(f"{path}: header has no source_commit: <40-hex sha> line")
    return {"prompt": prompt, "rubric_sha": hashlib.sha256(data).hexdigest(),
            "source_commit": commit.group(1)}


def _changeset_block(fixture) -> str:
    lines = ["=== Changed files (main...HEAD) ===", *fixture["name_only"], "=== Stat ===",
             fixture["stat"].rstrip("\n")]
    return "\n".join(lines)


def render_prompt(rubric_text, fixture) -> str:
    """The four targets, each required exactly once in the unrendered text, replaced in one
    re.sub pass so substituted fixture text is never re-scanned; then the output contract."""
    replacements = {
        CHANGESET_TARGET: _changeset_block(fixture),
        TITLE_TARGET: fixture["title"],
        LABELS_TARGET: json.dumps(fixture["labels"]),  # deliberate substitution 3: names only
        KILL_SWITCH_LINE: KILL_SWITCH_STATED,
    }
    for target in replacements:
        count = rubric_text.count(target)
        if count != 1:
            raise ShadowSchemaError(f"rubric: {target!r} occurs {count} times; exactly once is "
                                    "required")
    pattern = re.compile("|".join(re.escape(t) for t in sorted(replacements, key=len,
                                                                 reverse=True)))
    rendered = pattern.sub(lambda m: replacements[m.group(0)], rubric_text)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered + "\n" + OUTPUT_CONTRACT_SUFFIX


def _reject_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f"non-finite constant {name}")


def parse_verdict(text):
    """True/False when `text` is exactly a JSON object whose needs_preview is a boolean; None
    (schema_invalid) otherwise. No fence stripping, no salvage, no partial credit."""
    if not isinstance(text, str):
        return None
    try:
        obj = json.loads(text.strip(), object_pairs_hook=_reject_duplicates,
                         parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(obj, dict) or type(obj.get("needs_preview")) is not bool:
        return None
    return obj["needs_preview"]


def _final(path) -> str:
    return path.rsplit("/", 1)[-1]


def _any_name(path, patterns) -> bool:
    return any(fnmatch.fnmatchcase(_final(path), p) for p in patterns)


def _force_true(path) -> bool:
    return path.startswith(FORCE_TRUE_PREFIXES) or _any_name(path, FORCE_TRUE_NAMES)


def _skip_only(path) -> bool:
    if path.startswith(SKIP_PREFIXES) or _any_name(path, SKIP_NAMES):
        return True
    return path.startswith("workflows/") and _any_name(path, SKIP_WORKFLOW_NAMES)


def rules_only_verdict(name_only) -> bool:
    """One deterministic reading of the rubric (runner_version 1), not "the rubric": case-
    sensitive, no normalisation; `dir/**` is a root prefix test; other patterns match the final
    segment at any depth. Empty → true; force-true first; false only if every path is
    skip-only; otherwise true. Title and labels are ignored."""
    if not name_only:
        return True
    if any(_force_true(p) for p in name_only):
        return True
    return not all(_skip_only(p) for p in name_only)


def inside_git_tree(path) -> bool:
    """True when the resolved path or any ancestor directory holds a .git entry."""
    resolved = Path(path).resolve()
    return any((d / ".git").exists() for d in (resolved, *resolved.parents))


def run_order(fixture_ids, arm_ids, repetitions, seed) -> list:
    """Per repetition r: shuffle the sorted fixture ids with random.Random(f"{seed}:{r}"); the
    fixture at shuffled index i visits the sorted arms rotated left by (i + r) % len(arms)."""
    arms = sorted(arm_ids)
    units = []
    for r in range(repetitions):
        fixtures = sorted(fixture_ids)
        random.Random(f"{seed}:{r}").shuffle(fixtures)
        for i, fid in enumerate(fixtures):
            k = (i + r) % len(arms)
            for arm_id in arms[k:] + arms[:k]:
                units.append({"fixture_id": fid, "arm_id": arm_id, "repetition": r,
                              "run_order_position": len(units)})
    return units
````

- [ ] **Step 4: Run it and check that it passes.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py -q
```
Expected: `52 passed`.

- [ ] **Step 5: Commit.**

```bash
git add bench/hosting_shadow_schema.py tests/test_bench_shadow_runner.py
git commit -m "feat(#563): shared schema module for the hosting shadow comparator"
```

---

## Task 3: Worker and launcher (`bench/_shadow_worker.py`, `bench/shadow_runner.py`)

**Files:**
- Modify: `tests/test_bench_shadow_runner.py` (adds spec tests 1–3, 6–15, 35–37 and test 17's request half)
- Create: `bench/_shadow_worker.py`
- Create: `bench/shadow_runner.py`

- [ ] **Step 1: Add the launcher imports.** In `tests/test_bench_shadow_runner.py`, replace

```python
import hosting_shadow_schema as schema  # noqa: E402
```
with
```python
import hosting_shadow_schema as schema  # noqa: E402
import shadow_runner  # noqa: E402
from factory_core.capability_suite import mock_server  # noqa: E402
```

- [ ] **Step 2: Add test 17's request-side half.** In the same file, replace the last two
  lines of `test_render_prompt_substitutions`:

```python
        with pytest.raises(schema.ShadowSchemaError, match="exactly once"):
            schema.render_prompt(broken, fx)
```
with
```python
        with pytest.raises(schema.ShadowSchemaError, match="exactly once"):
            schema.render_prompt(broken, fx)
    with serve(verdict_handler()) as stub:
        launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
               write_fixtures(tmp_path, [fixture("f-1")]))
    (request,) = stub.requests
    assert set(request["body"]) == {"model", "max_tokens", "temperature", "messages"}
    assert request["body"]["model"] == SERVED
    assert request["body"]["messages"][0]["content"].endswith(schema.OUTPUT_CONTRACT_SUFFIX)
```

- [ ] **Step 3: Append the launcher and worker tests.** Add two blank lines and then the
  block below at the end of `tests/test_bench_shadow_runner.py`. The block contains the stub
  servers, the `launch`/`Captured` helpers and spec tests 1–3, 6–15 and 35–37. The stub
  servers run on their own event loop in a background thread, because the launcher blocks in
  `subprocess.run`.

````python
class Stub:
    """An aiohttp app on its own loop and thread (the launcher blocks in subprocess.run)."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.url = None

    async def _serve(self, request):
        body = await request.read()
        self.requests.append({"headers": {k.lower(): v for k, v in request.headers.items()},
                              "body": json.loads(body) if body else None})
        return await self.handler(request, self.requests[-1]["body"])

    async def _start(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._serve)
        self._runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=1.0)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{self._runner.addresses[0][1]}"


@contextmanager
def serve(handler):
    stub = Stub(handler)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    stub.url = asyncio.run_coroutine_threadsafe(stub._start(), loop).result(10)
    try:
        yield stub
    finally:
        asyncio.run_coroutine_threadsafe(stub._runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


@contextmanager
def serve_mock():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    runner, url = asyncio.run_coroutine_threadsafe(mock_server.start(), loop).result(10)
    try:
        yield runner, url
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


def message(text, model=SERVED, usage=None):
    return {"id": "msg_stub", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": usage if usage is not None else {"input_tokens": 40, "output_tokens": 9}}


def verdict_handler(needs_preview=True, model=SERVED, usage=None):
    async def handler(request, body):
        text = json.dumps({"needs_preview": needs_preview, "category": "code",
                           "reason": "stub"})
        return web.json_response(message(text, model, usage))
    return handler


def launch(tmp_path, arms_path, fixtures_path=EXAMPLE_FIXTURES, repetitions=1, seed=1,
           out="out"):
    out_dir = tmp_path / out
    shadow_runner.main(["--fixtures", str(fixtures_path), "--arms", str(arms_path),
                        "--out-dir", str(out_dir), "--repetitions", str(repetitions),
                        "--seed", str(seed)])
    rows = [json.loads(line) for line in (out_dir / "rows.jsonl").read_text().splitlines()]
    return out_dir, rows


def local_rows(rows):
    return [r for r in rows if r["arm_kind"] == "local"]


class Captured:
    """A stand-in for subprocess.run that records the call and prints a canned worker reply."""

    def __init__(self, raise_timeout=False):
        self.calls = []
        self.raise_timeout = raise_timeout

    def __call__(self, argv, **kwargs):
        job = Path(argv[-1])
        self.calls.append({"argv": argv, "env": dict(kwargs["env"]), "cwd": Path(kwargs["cwd"]),
                           "job_path": job, "job": json.loads(job.read_text()),
                           "home": Path(kwargs["env"]["HOME"]), "kwargs": kwargs})
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        out = dict.fromkeys(shadow_runner.WORKER_KEYS)
        out.update({"http_status": 200, "served_name_verified": True,
                    "response_text": '{"needs_preview": true}', "duration_ms": 5,
                    "started_at": "2026-11-27T00:00:00+00:00",
                    "completed_at": "2026-11-27T00:00:01+00:00"})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(out), stderr="")


# 1
def test_worker_env_is_exactly_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-copy")
    monkeypatch.setenv("AWS_PROFILE", "do-not-copy")
    captured = Captured()
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")))
    assert captured.calls
    for call in captured.calls:
        assert set(call["env"]) == {"PATH", "HOME", "SHADOW_BASE_URL", SECRET_ENV}
        assert call["env"][SECRET_ENV] == SECRET
        assert call["kwargs"]["stdin"] is subprocess.DEVNULL


# 2
@pytest.mark.parametrize("name", schema.REPO_TOKENS)
def test_launcher_refuses_github_tokens(tmp_path, monkeypatch, name):
    with serve(verdict_handler()) as stub:
        arms = write_arms(tmp_path, local_arm(stub.url))
        monkeypatch.setenv(name, "ghp_x")
        with pytest.raises(shadow_runner.LauncherRefusal, match=name):
            launch(tmp_path, arms)
        assert stub.requests == []
        monkeypatch.setenv(name, "")  # an empty value does not count as set
        launch(tmp_path, arms, out="empty-is-unset")
    options = {o for a in shadow_runner._parser()._actions for o in a.option_strings}
    assert options == {"-h", "--help", "--fixtures", "--arms", "--out-dir", "--repetitions",
                       "--seed"}
    assert list(inspect.signature(shadow_runner.main).parameters) == ["argv"]
    assert list(inspect.signature(shadow_runner.check_environment).parameters) == []


# 3
@pytest.mark.parametrize("name", model_endpoint.CLOUD_CREDENTIALS)
def test_launcher_refuses_cloud_credentials_in_env(tmp_path, monkeypatch, name):
    with serve(verdict_handler()) as stub:
        monkeypatch.setenv(name, "x")
        with pytest.raises(shadow_runner.LauncherRefusal, match=name):
            launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
        assert stub.requests == []


# 6
def test_secret_value_never_leaves_the_header(tmp_path, monkeypatch):
    real_run = subprocess.run
    seen = []

    def spy(argv, **kwargs):
        seen.append({"argv": argv, "job": Path(argv[-1]).read_text()})
        proc = real_run(argv, **kwargs)
        seen[-1].update(stdout=proc.stdout, stderr=proc.stderr)
        return proc

    monkeypatch.setattr(shadow_runner.subprocess, "run", spy)
    with serve(verdict_handler()) as stub:
        out_dir, _ = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
    assert seen
    for call in seen:
        assert SECRET not in " ".join(call["argv"])
        assert SECRET not in call["job"]
        assert SECRET not in call["stdout"] and SECRET not in call["stderr"]
    assert SECRET not in (out_dir / "rows.jsonl").read_text()
    assert SECRET not in (out_dir / "manifest.json").read_text()
    assert {r["headers"]["x-api-key"] for r in stub.requests} == {SECRET}


# 7
def test_base_url_is_written_nowhere(tmp_path):
    with serve(verdict_handler()) as stub:
        out_dir, _ = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)))
    host_port = stub.url.split("//", 1)[1]
    for name in ("rows.jsonl", "manifest.json"):
        text = (out_dir / name).read_text()
        assert stub.url not in text and host_port not in text
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert [a.get("base_url_scope") for a in manifest["arms"]] == [None, "same-host"]


# 8
def test_paths_inside_a_git_tree_raise(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    outside_arms = write_arms(tmp_path, local_arm("http://127.0.0.1:9"))
    inside_arms = write_arms(repo, local_arm("http://127.0.0.1:9"))
    with pytest.raises(shadow_runner.LauncherRefusal, match="--arms"):
        launch(tmp_path, inside_arms)
    with pytest.raises(shadow_runner.LauncherRefusal, match="--out-dir"):
        launch(tmp_path, outside_arms, out="repo/nested/out")
    for bad in ("run-records/out", "model-registry/out"):
        with pytest.raises(shadow_runner.LauncherRefusal, match="run-records"):
            launch(tmp_path, outside_arms, out=bad)
    (tmp_path / "has-runs" / "sub").mkdir(parents=True)
    (tmp_path / "has-runs" / "sub" / "runs.jsonl").write_text("")
    with pytest.raises(shadow_runner.LauncherRefusal, match="runs.jsonl"):
        launch(tmp_path, outside_arms, out="has-runs")
    (tmp_path / "rerun").mkdir()
    (tmp_path / "rerun" / "rows.jsonl").write_text("")
    with pytest.raises(shadow_runner.LauncherRefusal, match="rows.jsonl"):
        launch(tmp_path, outside_arms, out="rerun")


# 9
@pytest.mark.parametrize("fault", ["status_500", "dropped_connection"])
def test_local_failure_makes_no_request_to_any_other_host(tmp_path, fault):
    async def failing(request, body):
        if fault == "status_500":
            return web.json_response({"type": "error"}, status=500)
        request.transport.close()
        return web.Response()

    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as decoy, serve(failing) as endpoint:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(endpoint.url)), fixtures)
        assert len(endpoint.requests) == 1
        assert decoy.requests == []
    (row,) = local_rows(rows)
    assert row["parse_status"] == "no_response"
    assert row["worker_error"] is None
    if fault == "status_500":
        assert row["http_status"] == 500 and row["failure_class"] is None
    else:
        assert row["failure_class"] == "endpoint_unreachable"


# 10
def test_redirect_is_not_followed(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as target:
        async def redirect(request, body):
            raise web.HTTPFound(target.url + "/v1/messages")

        with serve(redirect) as endpoint:
            _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(endpoint.url)), fixtures)
        assert target.requests == []
    (row,) = local_rows(rows)
    assert row["http_status"] == 302
    assert row["failure_class"] == "protocol_incompatibility"
    assert row["parse_status"] == "no_response"


# 11
def test_wrong_served_model_is_a_failure(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler(model="some-other-model")) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    (row,) = local_rows(rows)
    assert row["failure_class"] == "protocol_incompatibility"
    assert row["served_name_verified"] is False
    assert row["parse_status"] == "no_response"
    with serve_mock() as (_, url):
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(
            url, served=mock_server.SERVED_NAME), name="mock-arms.yaml"), fixtures, out="mock")
    (row,) = local_rows(rows)
    assert row["failure_class"] is None
    assert row["served_name_verified"] is True
    assert row["response_text"] == "ok"
    assert row["parse_status"] == "schema_invalid"  # the mock answers "ok", not a verdict


# 12
def test_malformed_body_and_timeout_rows_are_retained(tmp_path, monkeypatch):
    async def malformed(request, body):
        return web.Response(body=b'{"type": "message", "content": [',
                            content_type="application/json")

    async def stall(request, body):
        await asyncio.sleep(5)
        return web.json_response(message("{}"))

    fixtures = write_fixtures(tmp_path, [fixture("f-1"), fixture("f-2")])
    with serve(malformed) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    assert len(rows) == 4
    assert {r["failure_class"] for r in local_rows(rows)} == {"protocol_incompatibility"}
    monkeypatch.setitem(schema.REQUEST_PARAMS, "total_seconds", 1)
    monkeypatch.setitem(schema.REQUEST_PARAMS, "connect_seconds", 1)
    with serve(stall) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url), name="a2.yaml"),
                         fixtures, out="stalled")
    assert len(rows) == 4
    assert {r["failure_class"] for r in local_rows(rows)} == {"timeout_total"}
    assert {r["parse_status"] for r in local_rows(rows)} == {"no_response"}


# 13
def test_usage_fields_absent_are_null(tmp_path):
    fixtures = write_fixtures(tmp_path, [fixture("f-1")])
    with serve(verdict_handler()) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)), fixtures)
    (row,) = local_rows(rows)
    assert row["gen_ai.usage.input_tokens"] == 40 and row["gen_ai.usage.output_tokens"] == 9
    assert row["gen_ai.usage.cache_read_input_tokens"] is None
    assert row["gen_ai.usage.cache_creation_input_tokens"] is None
    usage = {"input_tokens": 3, "output_tokens": 9, "cache_read_input_tokens": 37,
             "cache_creation_input_tokens": 0}
    with serve(verdict_handler(usage=usage)) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url), name="a2.yaml"),
                         fixtures, out="cached")
    (row,) = local_rows(rows)
    assert row["gen_ai.usage.cache_read_input_tokens"] == 37
    assert row["gen_ai.usage.cache_creation_input_tokens"] == 0


BANNED_SEGMENTS = {"side_effect", "board", "cli", "codehost", "tracker", "run_record",
                   "cost_report", "breaker", "deconflict", "handoff", "main_red_fixer",
                   "rescue", "epic_autopilot", "git", "github", "gh"}
BENCH_MODULES = {"hosting_shadow_schema", "shadow_runner", "compare_hosting_variants",
                 "compare_variants", "find_eligible", "_shadow_worker"}


def _imported(path):
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


# 14
def test_runtime_modules_name_no_banned_import():
    for name in NEW_MODULES:
        for imported in _imported(BENCH / name):
            assert not BANNED_SEGMENTS & set(imported.split(".")), (name, imported)
    worker_imports = _imported(BENCH / "_shadow_worker.py")
    assert "subprocess" not in worker_imports
    assert not BENCH_MODULES & {i.split(".")[0] for i in worker_imports}
    assert {i.split(".")[0] for i in worker_imports} <= {
        "asyncio", "json", "os", "sys", "time", "datetime", "pathlib", "aiohttp",
        "factory_core"}


# 15
def test_launcher_never_opens_the_labels_file(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures.yaml"
    fixtures.write_bytes(EXAMPLE_FIXTURES.read_bytes())  # no oracle_labels.yaml beside it
    captured = Captured()
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")), fixtures)
    options = {o for a in shadow_runner._parser()._actions for o in a.option_strings}
    assert not any("label" in o for o in options)
    for call in captured.calls:
        assert set(call["job"]) == {"prompt", "served_name", "endpoint_id", "secret_env",
                                    "request_params"}


# 35
@pytest.mark.parametrize("timeout", [False, True])
def test_worker_invocation_and_temp_cleanup(tmp_path, monkeypatch, timeout):
    captured = Captured(raise_timeout=timeout)
    monkeypatch.setattr(shadow_runner.subprocess, "run", captured)
    _, rows = launch(tmp_path, write_arms(tmp_path, local_arm("http://127.0.0.1:9")),
                     write_fixtures(tmp_path, [fixture("f-1")]))
    (call,) = captured.calls
    assert call["argv"] == [sys.executable, "-I", str((BENCH / "_shadow_worker.py").resolve()),
                            str(call["job_path"].resolve())]
    assert call["job_path"].is_absolute()
    assert call["kwargs"]["timeout"] == schema.REQUEST_PARAMS["total_seconds"] + 30
    for path in (call["job_path"], call["home"], call["cwd"], call["job_path"].parent):
        assert not path.exists()
    (row,) = local_rows(rows)
    if timeout:
        assert row["worker_error"] == "timeout" and row["parse_status"] == "no_response"
    else:
        assert row["worker_error"] is None and row["parse_status"] == "parsed"


# 36
def test_response_text_redacts_the_secret(tmp_path):
    async def echo(request, body):
        return web.json_response(message("key=" + request.headers["x-api-key"]))

    with serve(echo) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
                         write_fixtures(tmp_path, [fixture("f-1")]))
    (row,) = local_rows(rows)
    assert row["response_text"] == "key=[redacted]"
    assert SECRET not in json.dumps(rows)


# 37
def test_cold_or_warm_values(tmp_path):
    with serve(verdict_handler()) as stub:
        _, rows = launch(tmp_path, write_arms(tmp_path, local_arm(stub.url)),
                         write_fixtures(tmp_path, [fixture("f-1"), fixture("f-2")]),
                         repetitions=2)
    ordered = sorted(local_rows(rows), key=lambda r: r["run_order_position"])
    assert [r["cold_or_warm"] for r in ordered] == ["unknown", "not_first", "not_first",
                                                    "not_first"]
    assert {r["cold_or_warm"] for r in rows if r["arm_kind"] == "rules_only"} == {None}
````

- [ ] **Step 4: Run it and check that it fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py -q
```
Expected: a collection error, `ModuleNotFoundError: No module named 'shadow_runner'`.

- [ ] **Step 5: Create `bench/_shadow_worker.py`** with exactly the content below. Its only
  imports are `aiohttp`, the standard library and `factory_core.providers.model_adapter`.
  It has no `import subprocess` and imports nothing from `bench/`, which spec test 14
  enforces.

````python
"""Hosting shadow worker (#563): one model call, one JSON object on stdout, no file written.

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md §5.
bench/shadow_runner.py spawns it as `python -I _shadow_worker.py <job.json>` with an
environment of exactly PATH, HOME, SHADOW_BASE_URL and the arm's one secret. It makes exactly
one non-streaming POST to $SHADOW_BASE_URL/v1/messages, never follows a redirect, never
retries, and classifies a failure with model_adapter's §4.6 helpers. It imports aiohttp, the
standard library and factory_core.providers.model_adapter — nothing from bench/.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.providers import model_adapter  # noqa: E402

ANTHROPIC_VERSION = "2023-06-01"
RESPONSE_TEXT_LIMIT = 2000
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")
OUTPUT_KEYS = ("http_status", "failure_class", "served_name_verified", "response_text",
               *(f"gen_ai.usage.{f}" for f in USAGE_FIELDS), "duration_ms", "started_at",
               "completed_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parsed(data):
    try:
        parsed = json.loads(data)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(message) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(b["text"] for b in content
                   if isinstance(b, dict) and b.get("type") == "text"
                   and isinstance(b.get("text"), str))


def _usage(message) -> dict:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return {f"gen_ai.usage.{f}": usage[f] if type(usage.get(f)) is int else None
            for f in USAGE_FIELDS}


async def _post(url, secret, body, params):
    timeout = aiohttp.ClientTimeout(total=params["total_seconds"],
                                    connect=params["connect_seconds"])
    headers = {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=headers,
                                allow_redirects=False) as resp:
            return resp.status, await resp.read()


def run(job, base_url, secret) -> dict:
    params = job["request_params"]
    body = {"model": job["served_name"], "max_tokens": params["max_tokens"],
            "temperature": params["temperature"],
            "messages": [{"role": "user", "content": job["prompt"]}]}
    dest = {"endpoint_id": job["endpoint_id"], "served_name": job["served_name"]}
    out = dict.fromkeys(OUTPUT_KEYS)
    out["started_at"] = _now()
    started = time.monotonic()
    try:
        status, data = asyncio.run(_post(base_url + "/v1/messages", secret, body, params))
        out["duration_ms"] = round((time.monotonic() - started) * 1000)
        out["http_status"] = status
        model_adapter.check_response_status(status)
        if 200 <= status < 300:
            model_adapter.check_json_body(status, data)
            out["served_name_verified"] = False
            out["served_name_verified"] = model_adapter.check_response_model(dest, data,
                                                                             streamed=False)
            message = _parsed(data)
            text = _text(message)
            if secret:
                text = text.replace(secret, "[redacted]")
            out["response_text"] = text[:RESPONSE_TEXT_LIMIT]
            out.update(_usage(message))
        else:
            out["failure_class"] = model_adapter.status_failure_class(status)
    except (model_adapter.AdapterRefusal, aiohttp.ClientError, TimeoutError, OSError) as exc:
        if out["duration_ms"] is None:
            out["duration_ms"] = round((time.monotonic() - started) * 1000)
        out["failure_class"] = model_adapter.classify_failure(exc)
    out["completed_at"] = _now()
    return out


def main(argv) -> int:
    job = json.loads(Path(argv[1]).read_text())
    result = run(job, os.environ["SHADOW_BASE_URL"], os.environ[job["secret_env"]])
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
````

- [ ] **Step 6: Create `bench/shadow_runner.py`** with exactly the content below. It has no
  option, environment variable, parameter or hook that relaxes a refusal. `main()` takes only
  `argv` and `check_environment()` takes nothing, which spec test 2 pins.

````python
"""Hosting shadow runner (#563): replay the pinned classify-preview prompt against fixtures.

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md §4;
operator doc: bench/hosting_shadow.md.

Non-authoritative and offline: nothing in workflows/, the scheduler or the entrypoint calls
this. It never uses run_suite.sh, `archon workflow run`, git, Docker or gh. A rules_only arm
is answered in-process; each local call is one _shadow_worker.py subprocess whose environment
is built from nothing (PATH, a fresh HOME, SHADOW_BASE_URL and the arm's one secret).
Start-up refusals have no override of any kind.

Usage:
    python3 bench/shadow_runner.py --fixtures PATH --arms PATH --out-dir DIR \\
        --repetitions N --seed S
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import hosting_shadow_schema as schema
from hosting_shadow_schema import model_endpoint

WORKER = Path(__file__).resolve().parent / "_shadow_worker.py"
WORKER_TIMEOUT_SLACK_SECONDS = 30
WORKER_KEYS = frozenset(("http_status", "failure_class", "served_name_verified",
                         "response_text", *schema.USAGE_KEYS, "duration_ms", "started_at",
                         "completed_at"))


class LauncherRefusal(Exception):
    """A start-up refusal: raised before any request is made. Names a variable, never a value."""


def _set(name) -> bool:
    return bool(os.environ.get(name))


def check_environment() -> None:
    for name in schema.REPO_TOKENS:
        if _set(name):
            raise LauncherRefusal(f"{name} is set: the shadow runner never runs with a "
                                  "repository token in its environment")
    for name in model_endpoint.CLOUD_CREDENTIALS:
        if _set(name):
            raise LauncherRefusal(f"{name} is set: v1 has no cloud arm, so no cloud model "
                                  "credential may be present")


def check_paths(arms_path, out_dir) -> None:
    for flag, path in (("--arms", arms_path), ("--out-dir", out_dir)):
        if schema.inside_git_tree(path):
            raise LauncherRefusal(f"{flag} is inside a git work tree: endpoint identity never "
                                  "lands in a repository")
    out = Path(out_dir).resolve()
    if {"run-records", "model-registry"} & set(out.parts):
        raise LauncherRefusal("--out-dir must not be under run-records/ or model-registry/")
    if out.exists() and any(out.rglob("runs.jsonl")):
        raise LauncherRefusal("--out-dir must not contain a runs.jsonl")
    for name in ("rows.jsonl", "manifest.json"):
        if (out / name).exists():
            raise LauncherRefusal(f"--out-dir already holds {name}: a rerun needs a new "
                                  "directory")


def worker_argv(job_path) -> list:
    return [sys.executable, "-I", str(WORKER), str(Path(job_path).resolve())]


def worker_env(arm, home) -> dict:
    """Exactly PATH, HOME, SHADOW_BASE_URL and the arm's secret — never a copy of os.environ."""
    return {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
            "SHADOW_BASE_URL": arm["base_url"].rstrip("/"),
            arm["secret_env"]: os.environ[arm["secret_env"]]}


def call_worker(arm, prompt) -> tuple:
    """(worker output, None) or (None, timeout | crash | bad_output). The call's temporary
    directory (job file, HOME, working directory) is deleted whatever the outcome."""
    params = dict(schema.REQUEST_PARAMS)
    job = {"prompt": prompt, "served_name": arm["model_identity"]["served_name"],
           "endpoint_id": arm["endpoint_id"], "secret_env": arm["secret_env"],
           "request_params": params}
    tmp = Path(tempfile.mkdtemp(prefix="hosting-shadow-call-"))
    try:
        home, cwd, job_path = tmp / "home", tmp / "cwd", tmp / "job.json"
        home.mkdir()
        cwd.mkdir()
        job_path.write_text(json.dumps(job))
        try:
            proc = subprocess.run(worker_argv(job_path), env=worker_env(arm, home), cwd=cwd,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=params["total_seconds"] + WORKER_TIMEOUT_SLACK_SECONDS)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        if proc.returncode != 0:
            return None, "crash"
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            return None, "bad_output"
        if not isinstance(out, dict) or set(out) != WORKER_KEYS:
            return None, "bad_output"
        return out, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_row(ctx, unit, arm) -> dict:
    local = arm["arm_kind"] == "local"
    row = dict.fromkeys(schema.ROW_KEYS)
    row.update({
        "schema_version": schema.SCHEMA_VERSION, "runner_version": schema.RUNNER_VERSION,
        "fixture_id": unit["fixture_id"], "arm_id": arm["arm_id"], "arm_kind": arm["arm_kind"],
        "repetition": unit["repetition"], "run_order_seed": ctx["seed"],
        "run_order_position": unit["run_order_position"], "concurrency": 1,
        "rubric_sha": ctx["rubric_sha"], "fixture_set_digest": ctx["fixture_set_digest"],
        "prompt_substitutions_version": schema.PROMPT_SUBSTITUTIONS_VERSION,
        "preview_enabled": True,
        "unsupported_measurements": list(schema.UNSUPPORTED_MEASUREMENTS),
        "cost_usd": None, "energy_joules": None, "energy_provenance": "unknown",
    })
    if local:
        row.update({
            "request_params": dict(schema.REQUEST_PARAMS), "endpoint_id": arm["endpoint_id"],
            "hosting_location": "local", "role": "actor",
            "model_identity": dict(arm["model_identity"]),
            "runtime_stack": json.loads(json.dumps(arm["runtime_stack"])),
            "hardware": arm["hardware"], "identity_source": "declared",
        })
    return row


def run_unit(ctx, unit, arm, fixture, cold_or_warm) -> dict:
    row = _base_row(ctx, unit, arm)
    if arm["arm_kind"] == "rules_only":
        row["started_at"] = _now()
        row["predicted_needs_preview"] = schema.rules_only_verdict(fixture["name_only"])
        row["parse_status"] = "parsed"
        row["completed_at"] = _now()
        return row
    row["cold_or_warm"] = cold_or_warm
    started = _now()
    out, error = call_worker(arm, schema.render_prompt(ctx["prompt"], fixture))
    if out is None:
        row.update({"worker_error": error, "parse_status": "no_response",
                    "started_at": started, "completed_at": _now()})
        return row
    row.update(out)
    ok = (out["failure_class"] is None and isinstance(out["http_status"], int)
          and 200 <= out["http_status"] < 300)
    if not ok:
        row["parse_status"] = "no_response"
        return row
    verdict = schema.parse_verdict(out["response_text"])
    row["parse_status"] = "schema_invalid" if verdict is None else "parsed"
    row["predicted_needs_preview"] = verdict
    return row


def _manifest(ctx, arms, units, repetitions) -> dict:
    declared = []
    for arm in arms:
        entry = {"arm_id": arm["arm_id"], "arm_kind": arm["arm_kind"]}
        if arm["arm_kind"] == "local":
            entry.update({"endpoint_id": arm["endpoint_id"],
                          "model_identity": arm["model_identity"],
                          "runtime_stack": arm["runtime_stack"], "hardware": arm["hardware"],
                          "identity_source": "declared",
                          "base_url_scope": model_endpoint.base_url_scope(arm["base_url"])})
        declared.append(entry)
    return {"schema_version": schema.SCHEMA_VERSION, "runner_version": schema.RUNNER_VERSION,
            "seed": ctx["seed"], "repetitions": repetitions,
            "fixture_set_digest": ctx["fixture_set_digest"], "rubric_sha": ctx["rubric_sha"],
            "rubric_source_commit": ctx["rubric_source_commit"],
            "prompt_substitutions_version": schema.PROMPT_SUBSTITUTIONS_VERSION,
            "preview_enabled": True, "request_params": dict(schema.REQUEST_PARAMS),
            "concurrency": 1, "arms": declared, "planned_units": units}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench/shadow_runner.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", required=True, help="inputs file (fixtures.yaml)")
    parser.add_argument("--arms", required=True, help="arms file, outside any git work tree")
    parser.add_argument("--out-dir", required=True,
                        help="new directory for rows.jsonl and manifest.json, outside any git "
                             "work tree")
    parser.add_argument("--repetitions", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.repetitions < 1:
        raise LauncherRefusal("--repetitions must be at least 1")
    check_environment()
    check_paths(args.arms, args.out_dir)
    fixtures_doc = schema.load_fixtures(args.fixtures)
    arms = schema.load_arms(args.arms)
    for arm in arms:
        if arm["arm_kind"] == "local" and not _set(arm["secret_env"]):
            raise LauncherRefusal(f"{arm['secret_env']} is not set (named by arm "
                                  f"{arm['arm_id']})")
    rubric = schema.load_rubric()
    fixtures = {fx["fixture_id"]: fx for fx in fixtures_doc["fixtures"]}
    by_id = {arm["arm_id"]: arm for arm in arms}
    ctx = {"seed": args.seed, "prompt": rubric["prompt"], "rubric_sha": rubric["rubric_sha"],
           "rubric_source_commit": rubric["source_commit"],
           "fixture_set_digest": schema.sha256_file(args.fixtures)}
    units = schema.run_order(fixtures, by_id, args.repetitions, args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "manifest.json", "x") as manifest:
        manifest.write(json.dumps(_manifest(ctx, arms, units, args.repetitions), indent=2) + "\n")
    seen = set()
    with open(out_dir / "rows.jsonl", "x") as rows:
        for unit in units:
            arm = by_id[unit["arm_id"]]
            cold_or_warm = "not_first" if arm["arm_id"] in seen else "unknown"
            if arm["arm_kind"] == "local":
                seen.add(arm["arm_id"])
            row = run_unit(ctx, unit, arm, fixtures[unit["fixture_id"]], cold_or_warm)
            rows.write(json.dumps(row) + "\n")
            rows.flush()
    print(f"shadow runner: wrote {len(units)} rows to {out_dir / 'rows.jsonl'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LauncherRefusal, schema.ShadowSchemaError) as e:
        print(f"shadow runner: refused: {e}", file=sys.stderr)
        sys.exit(2)
````

- [ ] **Step 7: Run it and check that it passes, with one expected failure.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py -q
```
Expected: `1 failed, 75 passed`. The one failure is
`test_runtime_modules_name_no_banned_import`, with `FileNotFoundError` for
`bench/compare_hosting_variants.py`. Spec test 14 scans all four new modules, and Task 4
creates the fourth. Any other failure is a real defect.

- [ ] **Step 8: Commit.**

```bash
git add bench/_shadow_worker.py bench/shadow_runner.py tests/test_bench_shadow_runner.py
git commit -m "feat(#563): credential-isolated shadow runner and one-call worker"
```

---

## Task 4: Comparator (`bench/compare_hosting_variants.py`)

**Files:**
- Create: `tests/test_bench_hosting_compare.py` (spec tests 20–30)
- Create: `bench/compare_hosting_variants.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_bench_hosting_compare.py` with
  exactly the content below. The rows are built in-test, so no network is involved:

````python
"""Hosting shadow comparator and repository-level checks (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md
(Tests 20-34). Rows are built in-test (no network); one end-to-end test drives the real
launcher with a rules_only-only arms file, which makes no request at all.
"""
import ipaddress
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))

import compare_hosting_variants as chv  # noqa: E402
import hosting_shadow_schema as schema  # noqa: E402
import shadow_runner  # noqa: E402
from factory_core.providers import model_endpoint  # noqa: E402

EXAMPLES = BENCH / "fixtures" / "preview_classifier" / "examples"
EXAMPLE_FIXTURES = EXAMPLES / "fixtures.yaml"
EXAMPLE_LABELS = EXAMPLES / "oracle_labels.yaml"
EXAMPLE_PRE = BENCH / "preview_classifier_preregistration.example.yaml"
NEW_MODULES = ("hosting_shadow_schema.py", "shadow_runner.py", "_shadow_worker.py",
               "compare_hosting_variants.py")
LOCAL_IDENTITY = {"endpoint_id": "stub-endpoint",
                  "model_identity": {"served_name": "stub-model",
                                     "digest_or_revision": "stub-digest",
                                     "quantisation": "stub-quant"},
                  "runtime_stack": {"name": "stub-runtime", "version": "0.0.0",
                                    "chat_template": "stub-template",
                                    "context_settings": {"context_tokens": 8192}},
                  "hardware": "stub-hardware"}


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    for name in (*schema.REPO_TOKENS, *model_endpoint.CLOUD_CREDENTIALS):
        monkeypatch.delenv(name, raising=False)


def fx(fid, provenance="synthetic", split="dev"):
    entry = {"fixture_id": fid, "provenance": provenance, "split": split,
             "name_only": ["backend/app/x.py"], "stat": "", "title": "t", "labels": [],
             "labels_source": "synthetic"}
    if provenance == "historical":
        entry.update(labels_source="dropped", source={"pr": 1, "merge_sha": "abc", "issue": 2})
    return entry


def label(oracle, fail_safe=False):
    return {"oracle_label": oracle, "expected_fail_safe": fail_safe, "oracle_rationale": "r",
            "labeled_by": "test", "labeled_at": "2026-11-27T00:00:00+00:00"}


class Run:
    """A results directory, fixtures file and labels file, built row by row."""

    def __init__(self, tmp_path, fixtures, labels, scope="same-host"):
        self.tmp = tmp_path
        self.fixtures = tmp_path / "fixtures.yaml"
        self.fixtures.write_text(yaml.safe_dump({"schema_version": "1.0",
                                                 "preview_enabled": True,
                                                 "fixtures": fixtures}))
        self.labels = tmp_path / "labels.yaml"
        self.labels.write_text(yaml.safe_dump({"schema_version": "1.0",
                                               "oracle_question": schema.ORACLE_QUESTION,
                                               "labels": labels}))
        self.digest = schema.sha256_file(self.fixtures)
        self.rows = []
        self.scope = scope
        self.results = tmp_path / "results"

    def row(self, fid, arm_kind="local", rep=0, **fields):
        arm_id = "rules" if arm_kind == "rules_only" else "local-a"
        row = dict.fromkeys(schema.ROW_KEYS)
        row.update({"schema_version": "1.0", "runner_version": schema.RUNNER_VERSION,
                    "fixture_id": fid, "arm_id": arm_id, "arm_kind": arm_kind,
                    "repetition": rep, "run_order_seed": 1,
                    "run_order_position": len(self.rows), "concurrency": 1,
                    "rubric_sha": "a" * 64, "fixture_set_digest": self.digest,
                    "prompt_substitutions_version": "1", "preview_enabled": True,
                    "unsupported_measurements": list(schema.UNSUPPORTED_MEASUREMENTS),
                    "energy_provenance": "unknown", "parse_status": "parsed",
                    "predicted_needs_preview": True,
                    "started_at": "2026-11-27T12:00:00+00:00",
                    "completed_at": "2026-11-27T12:00:01+00:00"})
        if arm_kind == "local":
            row.update({"request_params": dict(schema.REQUEST_PARAMS),
                        "hosting_location": "local", "role": "actor",
                        "identity_source": "declared", "served_name_verified": True,
                        "http_status": 200, "cold_or_warm": "unknown",
                        "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 10,
                        "duration_ms": 1000, **json.loads(json.dumps(LOCAL_IDENTITY))})
        row.update(fields)
        self.rows.append(row)
        return row

    def both(self, fid, rep=0, rules=True, **local_fields):
        self.row(fid, "rules_only", rep, predicted_needs_preview=rules)
        return self.row(fid, "local", rep, **local_fields)

    def write(self, repetitions=1, manifest_extra=None):
        self.results.mkdir(exist_ok=True)
        manifest = {
            "schema_version": "1.0", "runner_version": schema.RUNNER_VERSION, "seed": 1,
            "repetitions": repetitions, "fixture_set_digest": self.digest,
            "rubric_sha": "a" * 64, "rubric_source_commit": "b" * 40,
            "prompt_substitutions_version": "1", "preview_enabled": True,
            "request_params": dict(schema.REQUEST_PARAMS), "concurrency": 1,
            "arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                     {"arm_id": "local-a", "arm_kind": "local", "identity_source": "declared",
                      "base_url_scope": self.scope, **LOCAL_IDENTITY}],
            "planned_units": [{"fixture_id": r["fixture_id"], "arm_id": r["arm_id"],
                               "repetition": r["repetition"],
                               "run_order_position": r["run_order_position"]}
                              for r in self.rows],
            **(manifest_extra or {})}
        (self.results / "manifest.json").write_text(json.dumps(manifest))
        (self.results / "rows.jsonl").write_text("".join(json.dumps(r) + "\n"
                                                         for r in self.rows))

    def report(self, out="report.json", pre=None, write=True):
        if write:
            self.write()
        out_path = self.tmp / out
        argv = ["--results-dir", str(self.results), "--fixtures", str(self.fixtures),
                "--labels", str(self.labels), "--out", str(out_path)]
        if pre is not None:
            argv += ["--preregistration", str(pre)]
        chv.main(argv)
        return json.loads(out_path.read_text()) if out_path.suffix == ".json" \
            else out_path.read_text()

    def prereg(self, **overrides):
        pre = {"schema_version": "1.0", "status": "registered", "registered_by": "test",
               "registered_at": "2026-11-27T00:00:00+00:00",
               "fixture_set_digest": self.digest,
               "oracle_label_digest": schema.sha256_file(self.labels),
               "rubric_sha": "a" * 64, "repetitions": 1, "seed": 1,
               "criteria": {"max_false_skips_held_out": 0, "max_false_skip_rate": 0.0,
                            "min_schema_validity_rate": 0.9, "min_coverage": 0.9,
                            "p95_duration_ms_slo": 5000,
                            "min_false_preview_rate_reduction_vs_rules_only": 0.0},
               "invalid_run_handling": schema.INVALID_RUN_HANDLING}
        pre.update(overrides)
        path = self.tmp / "pre.yaml"
        path.write_text(yaml.safe_dump({"preview_classifier_preregistration": pre}))
        return path


def simple_run(tmp_path, **kw):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(False)}, **kw)
    run.both("f-1", rules=True, predicted_needs_preview=True)
    run.both("f-2", rules=True, predicted_needs_preview=False)
    return run


# 20
@pytest.mark.parametrize("key,value", [("fixture_set_digest", "c" * 64), ("rubric_sha", "d" * 64),
                                       ("prompt_substitutions_version", "2"),
                                       ("request_params", {**schema.REQUEST_PARAMS,
                                                           "max_tokens": 1})])
def test_equality_guard_raises(tmp_path, key, value):
    run = simple_run(tmp_path)
    run.rows[-1][key] = value
    with pytest.raises(chv.CompareError):
        run.report()


# 21
def test_missing_or_duplicate_row_raises(tmp_path):
    run = simple_run(tmp_path)
    run.write()
    rows = (run.results / "rows.jsonl").read_text().splitlines()
    (run.results / "rows.jsonl").write_text("\n".join(rows[:-1]) + "\n")
    with pytest.raises(chv.CompareError, match="planned units"):
        run.report(write=False)
    (run.results / "rows.jsonl").write_text("\n".join(rows + rows[-1:]) + "\n")
    with pytest.raises(chv.CompareError, match="duplicate"):
        run.report(write=False)


# 22
def test_outcome_classes_are_exclusive_and_ordered(tmp_path):
    fixtures = [fx(f"f-{i}") for i in range(1, 6)]
    labels = {"f-1": label(True), "f-2": label(True), "f-3": label(True, fail_safe=True),
              "f-4": label(True), "f-5": label(False)}
    run = Run(tmp_path, fixtures, labels)
    run.both("f-1", predicted_needs_preview=True)                      # accepted
    run.both("f-2", predicted_needs_preview=False)                     # substantive_failure
    run.both("f-3", predicted_needs_preview=True)                      # policy_fail_safe
    run.both("f-4", parse_status="schema_invalid", predicted_needs_preview=None)
    run.both("f-5", parse_status="no_response", predicted_needs_preview=None,
             http_status=None, failure_class="endpoint_unreachable")
    classes = {r["fixture_id"]: chv.outcome_class(r, labels[r["fixture_id"]])
               for r in run.rows if r["arm_kind"] == "local"}
    assert classes == {"f-1": "accepted", "f-2": "substantive_failure",
                       "f-3": "policy_fail_safe", "f-4": "schema_invalid",
                       "f-5": "infrastructure_invalid"}
    metrics = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert metrics["class_counts"] == {c: 1 for c in chv.OUTCOME_CLASSES}
    assert sum(metrics["class_counts"].values()) == metrics["assigned"] == 5


# 23
def test_schema_invalid_is_never_a_false_skip_and_stays_in_denominators(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(False)})
    run.both("f-1", parse_status="schema_invalid", predicted_needs_preview=None)
    run.both("f-2", parse_status="schema_invalid", predicted_needs_preview=None)
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["false_skips"] == 0 and m["false_skip_rate"] == 0.0
    assert m["false_preview_rate"] == 1.0  # no verdict -> effective true
    assert m["assigned"] == 2 and m["schema_validity_rate"] == 0.0 and m["coverage"] == 0.0


# 24
def test_null_metric_is_dropped_not_zeroed(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(True)})
    run.both("f-1", duration_ms=None, **{"gen_ai.usage.input_tokens": None,
                                         "gen_ai.usage.output_tokens": None})
    run.both("f-2", duration_ms=400)
    report = run.report()
    m = report["cells"]["synthetic/dev"]["local-a"]
    assert m["duration_n"] == 1 and m["rows_without_duration"] == 1
    assert m["duration_ms_p50"] == 400 and m["duration_ms_p95"] == 400
    assert m["total_input_tokens"] == 100 and m["rows_with_null_usage"] == 1
    rules = report["cells"]["synthetic/dev"]["rules"]
    assert rules["total_input_tokens"] is None and rules["duration_ms_p95"] is None
    assert rules["accepted_per_hour"] is None


def test_zero_accepted_is_undefined_not_zero(tmp_path):
    run = Run(tmp_path, [fx("f-1")], {"f-1": label(False)})
    run.both("f-1", rules=False, predicted_needs_preview=True)
    report = run.report()
    m = report["cells"]["synthetic/dev"]["local-a"]
    assert m["class_counts"]["accepted"] == 0
    assert m["tokens_per_accepted"] is None and m["false_skip_rate"] is None
    assert m["failure_spend_tokens"] == 110  # the one failed row's 100 + 10
    text = run.report(out="report.md")
    assert "| tokens per accepted call (lower bound) | undefined | undefined |" in text


# 25
def test_total_input_tokens_includes_cache_fields(tmp_path):
    run = Run(tmp_path, [fx("f-1")], {"f-1": label(True)})
    run.both("f-1", **{"gen_ai.usage.input_tokens": 3,
                       "gen_ai.usage.cache_read_input_tokens": 90,
                       "gen_ai.usage.cache_creation_input_tokens": 7})
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["total_input_tokens"] == 100
    assert m["cache_read_ratio"] == 0.9
    assert m["tokens_per_accepted"] == 110
    assert m["failure_spend_tokens"] == 0  # no failed row: a known zero, not undefined


def test_absent_cache_fields_are_not_a_cache_hit(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(True)})
    run.both("f-1", **{"gen_ai.usage.cache_read_input_tokens": 50,
                       "gen_ai.usage.cache_creation_input_tokens": 0})
    run.both("f-2")
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["rows_reporting_cache_fields"] == {"gen_ai.usage.cache_read_input_tokens": 1,
                                                "gen_ai.usage.cache_creation_input_tokens": 1}
    assert m["cache_read_ratio"] is None
    assert m["total_input_tokens"] == 250


# 26
def test_cost_is_null_and_rendered_unknown(tmp_path):
    run = simple_run(tmp_path)
    report = run.report()
    for per_arm in report["cells"].values():
        assert {m["cost_usd"] for m in per_arm.values()} == {None}
    text = run.report(out="report.md")
    assert "| cost_usd | unknown | unknown |" in text
    assert not re.search(r"\$\s*\d", text)


# 27
def test_held_out_requires_matching_preregistration(tmp_path):
    run = Run(tmp_path, [fx("f-1", split="held_out")], {"f-1": label(True)})
    run.both("f-1")
    with pytest.raises(chv.CompareError, match="--preregistration"):
        run.report()
    run.report(pre=run.prereg())
    for overrides in ({"fixture_set_digest": "c" * 64}, {"oracle_label_digest": "c" * 64},
                      {"rubric_sha": "c" * 64}, {"repetitions": 2}, {"seed": 2},
                      {"registered_at": "2026-11-27T13:00:00+00:00"}):
        with pytest.raises(chv.CompareError):
            run.report(pre=run.prereg(**overrides))
    with pytest.raises(schema.ShadowSchemaError, match="status"):
        run.report(pre=run.prereg(status="approved"))


def test_criteria_are_per_arm_and_cell(tmp_path):
    fixtures = [fx("h-1", split="held_out"), fx("d-1"), fx("d-2")]
    labels = {"h-1": label(True), "d-1": label(False), "d-2": label(True)}
    run = Run(tmp_path, fixtures, labels)
    run.both("h-1", predicted_needs_preview=False)                        # held-out false skip
    run.both("d-1", rules=True, predicted_needs_preview=False, duration_ms=None,
             worker_error=None)
    run.both("d-2", parse_status="no_response", predicted_needs_preview=None, http_status=None,
             worker_error="timeout", duration_ms=None, **{"gen_ai.usage.input_tokens": None,
                                                          "gen_ai.usage.output_tokens": None})
    report = run.report(pre=run.prereg())
    held = report["criteria"]["synthetic/held_out"]["local-a"]
    dev = report["criteria"]["synthetic/dev"]["local-a"]
    assert held["max_false_skips_held_out"] == "not met"
    assert held["max_false_skip_rate"] == "not met"
    assert held["min_false_preview_rate_reduction_vs_rules_only"] == "not evaluable"
    assert dev["max_false_skips_held_out"] == "not evaluable"
    assert dev["p95_duration_ms_slo"] == "not met"  # a timeout row fails the SLO
    assert dev["min_coverage"] == "not met"           # 1 parsed of 2 assigned
    assert dev["min_false_preview_rate_reduction_vs_rules_only"] == "met"
    assert dev["n_pairs"] == 2
    # a failed row exists but reported no usage: unknown, never 0
    assert report["cells"]["synthetic/dev"]["local-a"]["failure_spend_tokens"] is None
    assert set(report["criteria"]) == {"synthetic/held_out", "synthetic/dev"}
    criteria = yaml.safe_load(run.prereg().read_text())["preview_classifier_preregistration"]
    assert set(held) - {"n_pairs"} == set(criteria["criteria"])


# 28
def test_cells_are_never_pooled(tmp_path):
    fixtures = [fx("h-1", "historical", "dev"), fx("s-1"), fx("s-2", split="held_out")]
    labels = {"h-1": label(True), "s-1": label(True), "s-2": label(True)}
    run = Run(tmp_path, fixtures, labels)
    for fid in ("h-1", "s-1", "s-2"):
        run.both(fid)
    report = run.report(pre=run.prereg())
    assert set(report["cells"]) == {"historical/dev", "synthetic/dev", "synthetic/held_out"}
    assert set(report) == {"header", "example_banner", "rubric_source_commit",
                           "fixture_set_digest", "oracle_label_digest", "rubric_sha",
                           "preregistration_status", "arms", "cost_note", "cells",
                           "diagnostics_non_scoring", "criteria"}
    for per_arm in report["cells"].values():
        assert {m["assigned"] for m in per_arm.values()} == {1}
    text = run.report(out="report.md", pre=run.prereg())
    assert not re.search(r"(?i)\b(overall|pooled|composite|total across)\b", text)


# 29
def test_report_header_is_verbatim(tmp_path):
    run = simple_run(tmp_path, scope="remote")
    text = run.report(out="report.md", pre=run.prereg(status="example"))
    assert chv.HEADER == ("NON-AUTHORITATIVE instrument output. Not a promotion decision, "
                          "GO/NO-GO, production-readiness or savings evidence. v1 has no cloud "
                          "arm: this report contains no cloud/local cost or quality comparison.")
    assert chv.HEADER in text
    assert "EXAMPLE DATA — tooling test only" in text
    assert "declared local; address is remote" in text
    assert f"rules_only (reading of runner_version {schema.RUNNER_VERSION})" in text
    assert "(lower bound; 2 rows with null usage)" in text  # the rules_only column
    assert "(n=2; 0 rows without duration)" in text
    assert "b" * 40 in text
    (tmp_path / "lan").mkdir()
    lan = Run(tmp_path / "lan", [fx("f-1")], {"f-1": label(True)}, scope="lan")
    lan.both("f-1")
    lan_text = lan.report(out="report.md")
    assert "declared local; address is remote" not in lan_text
    assert "EXAMPLE DATA" not in lan_text


# 30
def test_report_out_path_inside_git_tree_raises(tmp_path):
    run = simple_run(tmp_path)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(chv.CompareError, match="git work tree"):
        run.report(out="repo/report.md")
    with pytest.raises(chv.CompareError, match=r"\.md or \.json"):
        run.report(out="report.txt")
````

- [ ] **Step 2: Run it and check that it fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_hosting_compare.py -q
```
Expected: a collection error, `ModuleNotFoundError: No module named 'compare_hosting_variants'`.

- [ ] **Step 3: Create `bench/compare_hosting_variants.py`** with exactly:

````python
"""Hosting shadow comparator (#563): score shadow_runner rows against the human oracle.

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md §6-7;
operator doc: bench/hosting_shadow.md.

A new tool, not an extension of bench/compare_variants.py (whose economics-only allowlist is
untouched). Every arm is scored against the oracle labels only — never against another arm.
Rows are reported per arm and per (provenance x split) cell, never pooled. A null metric is
dropped from its own n and never coerced to 0; a zero denominator is "undefined". The output
is non-authoritative: no gate, budget, breaker or scheduler reads it.

Usage:
    python3 bench/compare_hosting_variants.py --results-dir DIR --fixtures PATH \\
        --labels PATH --out PATH.md|PATH.json [--preregistration PATH]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import hosting_shadow_schema as schema

HEADER = ("NON-AUTHORITATIVE instrument output. Not a promotion decision, GO/NO-GO, "
          "production-readiness or savings evidence. v1 has no cloud arm: this report "
          "contains no cloud/local cost or quality comparison.")
EXAMPLE_BANNER = "EXAMPLE DATA — tooling test only"
REMOTE_NOTE = "declared local; address is remote"
CRITERIA_HEADING = "Preregistered criteria check — not a GO/NO-GO"
OUTCOME_CLASSES = ("accepted", "substantive_failure", "policy_fail_safe", "schema_invalid",
                   "infrastructure_invalid")
CELLS = (("historical", "dev"), ("historical", "held_out"), ("synthetic", "dev"),
         ("synthetic", "held_out"))
SHARED_KEYS = ("schema_version", "runner_version", "fixture_set_digest", "rubric_sha",
               "prompt_substitutions_version", "preview_enabled", "run_order_seed")
TIMEOUT_CLASSES = ("timeout_connect", "timeout_time_to_first_token", "timeout_idle",
                   "timeout_total")


class CompareError(Exception):
    """A fail-closed comparator check."""


def load_results(results_dir) -> tuple:
    results_dir = Path(results_dir)
    try:
        manifest = json.loads((results_dir / "manifest.json").read_text())
        lines = (results_dir / "rows.jsonl").read_text().splitlines()
    except (OSError, ValueError) as e:
        raise CompareError(f"{results_dir}: manifest.json/rows.jsonl unreadable ({e})") from None
    rows = []
    for n, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except ValueError:
            raise CompareError(f"rows.jsonl:{n}: not a JSON object") from None
        if not isinstance(row, dict) or set(row) != set(schema.ROW_KEYS):
            raise CompareError(f"rows.jsonl:{n}: keys differ from the v1 row schema")
        rows.append(row)
    if not rows:
        raise CompareError("rows.jsonl: no rows")
    return manifest, rows


def check_rows(manifest, rows, fixtures_path) -> None:
    """Only arm identity may differ between arms (the pattern of compare_variants.py's
    assert_only_economics_keys_differ, not its code); the rows are exactly the plan."""
    for key in SHARED_KEYS:
        if len({json.dumps(r[key]) for r in rows}) != 1:
            raise CompareError(f"rows disagree on {key}: only arm identity may differ")
    local = [r for r in rows if r["arm_kind"] == "local"]
    if len({json.dumps(r["request_params"], sort_keys=True) for r in local}) > 1:
        raise CompareError("local rows disagree on request_params")
    first = rows[0]
    expected = {"schema_version": manifest.get("schema_version"),
                "runner_version": manifest.get("runner_version"),
                "fixture_set_digest": manifest.get("fixture_set_digest"),
                "rubric_sha": manifest.get("rubric_sha"),
                "prompt_substitutions_version": manifest.get("prompt_substitutions_version"),
                "run_order_seed": manifest.get("seed")}
    for key, value in expected.items():
        if first[key] != value:
            raise CompareError(f"rows' {key} differs from manifest.json")
    if schema.sha256_file(fixtures_path) != first["fixture_set_digest"]:
        raise CompareError("--fixtures digest differs from the rows' fixture_set_digest")
    planned = [(u["arm_id"], u["fixture_id"], u["repetition"]) for u in
               manifest.get("planned_units", [])]
    observed = [(r["arm_id"], r["fixture_id"], r["repetition"]) for r in rows]
    if len(set(observed)) != len(observed):
        raise CompareError("rows.jsonl holds a duplicate (arm, fixture, repetition)")
    if sorted(observed) != sorted(planned):
        raise CompareError("rows.jsonl is not exactly the manifest's planned units")


def check_preregistration(pre, manifest, rows, fixtures_path, labels_path) -> None:
    observed = {"fixture_set_digest": schema.sha256_file(fixtures_path),
                "oracle_label_digest": schema.sha256_file(labels_path),
                "rubric_sha": rows[0]["rubric_sha"],
                "repetitions": manifest.get("repetitions"), "seed": manifest.get("seed")}
    for key, value in observed.items():
        if pre[key] != value:
            raise CompareError(f"preregistration {key} differs from this run's")
    registered = schema.parse_timestamp(pre["registered_at"], "preregistration.registered_at")
    earliest = min(schema.parse_timestamp(r["started_at"], "row.started_at") for r in rows)
    if not registered < earliest:
        raise CompareError("preregistration registered_at is not earlier than the earliest "
                           "row's started_at")


def outcome_class(row, label) -> str:
    """Top-down, exactly one class per row."""
    if row["parse_status"] == "no_response":
        return "infrastructure_invalid"
    if row["parse_status"] == "schema_invalid":
        return "schema_invalid"
    predicted = row["predicted_needs_preview"]
    if label["expected_fail_safe"] and predicted is True:
        return "policy_fail_safe"
    return "accepted" if predicted == label["oracle_label"] else "substantive_failure"


def effective_decision(row) -> bool:
    """The parsed verdict, or true when there is none: uncertainty retains the preview."""
    return row["predicted_needs_preview"] if row["parse_status"] == "parsed" else True


def _ratio(num, den):
    return None if not den or num is None else num / den


def _sum(values):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def total_input_tokens(row):
    """input + cache_read + cache_creation over the terms reported; null without input."""
    if row["gen_ai.usage.input_tokens"] is None:
        return None
    return sum(row[k] for k in schema.USAGE_KEYS if k != "gen_ai.usage.output_tokens"
               and row[k] is not None)


def nearest_rank(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(pct / 100 * len(ordered))) - 1]


def cell_metrics(items) -> dict:
    """items: [(row, label, outcome_class)] for one arm in one cell."""
    assigned = len(items)
    counts = {c: sum(1 for _, _, oc in items if oc == c) for c in OUTCOME_CLASSES}
    positives = [(r, lb) for r, lb, _ in items if lb["oracle_label"]]
    negatives = [(r, lb) for r, lb, _ in items if not lb["oracle_label"]]
    false_skips = sum(1 for r, _ in positives
                      if r["parse_status"] == "parsed" and r["predicted_needs_preview"] is False)
    false_previews = sum(1 for r, _ in negatives if effective_decision(r))
    parsed = sum(1 for r, _, _ in items if r["parse_status"] == "parsed")
    schema_invalid = counts["schema_invalid"]
    rows = [r for r, _, _ in items]
    inputs = [total_input_tokens(r) for r in rows]
    outputs = [r["gen_ai.usage.output_tokens"] for r in rows]
    durations = [r["duration_ms"] for r in rows if r["duration_ms"] is not None]
    in_total, out_total = _sum(inputs), _sum(outputs)
    all_tokens = _sum([in_total, out_total])
    spend_rows = [(i, o) for (r, _, oc), i, o in zip(items, inputs, outputs)
                  if oc not in ("accepted", "policy_fail_safe")]
    # No failed row is a known zero; failed rows that reported no usage are unknown (null).
    failure_spend = 0 if not spend_rows else _sum(
        [_sum([i for i, _ in spend_rows]), _sum([o for _, o in spend_rows])])
    reported = {k: sum(1 for r in rows if r[k] is not None) for k in schema.USAGE_KEYS[2:]}
    cache_ratio = None
    if all(n == assigned for n in reported.values()) and in_total:
        cache_ratio = _sum(r["gen_ai.usage.cache_read_input_tokens"] for r in rows) / in_total
    accepted = counts["accepted"]
    summed_ms = sum(durations)
    measured = sum(1 for r in rows if r["gen_ai.usage.input_tokens"] is not None
                   and r["gen_ai.usage.output_tokens"] is not None
                   and r["duration_ms"] is not None)
    failure_classes, worker_errors = {}, {}
    for r in rows:
        if r["failure_class"] is not None:
            failure_classes[r["failure_class"]] = failure_classes.get(r["failure_class"], 0) + 1
        if r["worker_error"] is not None:
            worker_errors[r["worker_error"]] = worker_errors.get(r["worker_error"], 0) + 1
    return {
        "assigned": assigned,
        "class_counts": counts,
        "class_rates": {c: _ratio(n, assigned) for c, n in counts.items()},
        "accepted_rate": _ratio(accepted, assigned),
        "accepted_or_policy_fail_safe_rate": _ratio(accepted + counts["policy_fail_safe"],
                                                    assigned),
        "false_skips": false_skips,
        "false_skip_rate": _ratio(false_skips, len(positives)),
        "false_preview_rate": _ratio(false_previews, len(negatives)),
        "schema_validity_rate": _ratio(parsed, parsed + schema_invalid),
        "coverage": _ratio(parsed, assigned),
        "total_input_tokens": in_total,
        "total_output_tokens": out_total,
        "rows_with_null_usage": sum(1 for i, o in zip(inputs, outputs) if i is None or o is None),
        "rows_reporting_cache_fields": reported,
        "cache_read_ratio": cache_ratio,
        "tokens_per_accepted": _ratio(all_tokens, accepted),
        "failure_spend_tokens": failure_spend,
        "duration_ms_p50": nearest_rank(durations, 50),
        "duration_ms_p95": nearest_rank(durations, 95),
        "duration_n": len(durations),
        "rows_without_duration": assigned - len(durations),
        "accepted_per_hour": _ratio(accepted, summed_ms / 3_600_000) if summed_ms else None,
        "measurement_completeness": _ratio(measured, assigned),
        "retries": 0,
        "fallbacks": 0,
        "failure_classes": failure_classes,
        "worker_errors": worker_errors,
        "has_timeout_row": any(r["worker_error"] == "timeout"
                               or r["failure_class"] in TIMEOUT_CLASSES for r in rows),
        "cost_usd": None,
    }


def _verdict(value, ok) -> str:
    return "not evaluable" if value is None else ("met" if ok(value) else "not met")


def criteria_check(crit, cell, local, rules) -> dict:
    held_out = cell.endswith("/held_out")
    reduction = (None if rules["false_preview_rate"] is None or local["false_preview_rate"] is None
                 else rules["false_preview_rate"] - local["false_preview_rate"])
    p95 = ("not met" if local["has_timeout_row"] else
           _verdict(local["duration_ms_p95"], lambda v: v <= crit["p95_duration_ms_slo"]))
    return {
        "max_false_skips_held_out": (
            _verdict(local["false_skips"], lambda v: v <= crit["max_false_skips_held_out"])
            if held_out else "not evaluable"),
        "max_false_skip_rate": _verdict(local["false_skip_rate"],
                                        lambda v: v <= crit["max_false_skip_rate"]),
        "min_schema_validity_rate": _verdict(local["schema_validity_rate"],
                                             lambda v: v >= crit["min_schema_validity_rate"]),
        "min_coverage": _verdict(local["coverage"], lambda v: v >= crit["min_coverage"]),
        "p95_duration_ms_slo": p95,
        "min_false_preview_rate_reduction_vs_rules_only": _verdict(
            reduction, lambda v: v >= crit["min_false_preview_rate_reduction_vs_rules_only"]),
    }


def build_report(manifest, rows, fixtures_doc, labels_doc, labels_digest, pre) -> dict:
    fixtures = {fx["fixture_id"]: fx for fx in fixtures_doc["fixtures"]}
    labels = labels_doc["labels"]
    arms = manifest["arms"]
    rules_id = next(a["arm_id"] for a in arms if a["arm_kind"] == "rules_only")
    runner_version = rows[0]["runner_version"]
    arm_labels = {}
    for a in arms:
        if a["arm_kind"] == "rules_only":
            arm_labels[a["arm_id"]] = f"rules_only (reading of runner_version {runner_version})"
        else:
            note = f"; {REMOTE_NOTE}" if a["base_url_scope"] == "remote" else ""
            arm_labels[a["arm_id"]] = (
                f"local {a['endpoint_id']}: {a['model_identity']['served_name']} "
                f"{a['model_identity']['digest_or_revision']} "
                f"{a['model_identity']['quantisation']}, {a['runtime_stack']['name']} "
                f"{a['runtime_stack']['version']}, {a['hardware']} "
                f"(identity declared; base_url_scope {a['base_url_scope']}{note})")
    cells = {}
    diagnostics = {}
    criteria = {}
    for provenance, split in CELLS:
        name = f"{provenance}/{split}"
        in_cell = [r for r in rows if fixtures[r["fixture_id"]]["provenance"] == provenance
                   and fixtures[r["fixture_id"]]["split"] == split]
        if not in_cell:
            continue
        cells[name] = {}
        for a in arms:
            items = [(r, labels[r["fixture_id"]], outcome_class(r, labels[r["fixture_id"]]))
                     for r in in_cell if r["arm_id"] == a["arm_id"]]
            cells[name][a["arm_id"]] = cell_metrics(items)
        rules_rows = {(r["fixture_id"], r["repetition"]): r for r in in_cell
                      if r["arm_id"] == rules_id}
        for a in arms:
            if a["arm_kind"] != "local":
                continue
            local_rows = {(r["fixture_id"], r["repetition"]): r for r in in_cell
                          if r["arm_id"] == a["arm_id"]}
            pairs = sorted(set(rules_rows) & set(local_rows))
            agree = sum(1 for p in pairs if effective_decision(local_rows[p])
                        == effective_decision(rules_rows[p]))
            diagnostics.setdefault(name, {})[a["arm_id"]] = {
                "n_pairs": len(pairs), "agreement_with_rules_only": _ratio(agree, len(pairs))}
            if pre is not None:
                criteria.setdefault(name, {})[a["arm_id"]] = {
                    "n_pairs": len(pairs),
                    **criteria_check(pre["criteria"], name, cells[name][a["arm_id"]],
                                     cells[name][rules_id])}
    return {
        "header": HEADER,
        "example_banner": EXAMPLE_BANNER if pre is not None and pre["status"] == "example"
        else None,
        "rubric_source_commit": manifest["rubric_source_commit"],
        "fixture_set_digest": rows[0]["fixture_set_digest"],
        "oracle_label_digest": labels_digest,
        "rubric_sha": rows[0]["rubric_sha"],
        "preregistration_status": pre["status"] if pre is not None else None,
        "arms": arm_labels,
        "cost_note": "cost_usd is unknown for every arm; no fully loaded cost is computed in v1",
        "cells": cells,
        "diagnostics_non_scoring": diagnostics,
        "criteria": criteria if pre is not None else None,
    }


def _fmt(value) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(report) -> str:
    out = [f"# Hosting shadow report — preview classifier", "", f"**{report['header']}**", ""]
    if report["example_banner"]:
        out += [f"**{report['example_banner']}**", ""]
    out += [f"- Rubric source commit: `{report['rubric_source_commit']}`",
            f"- rubric_sha: `{report['rubric_sha']}`",
            f"- fixture_set_digest: `{report['fixture_set_digest']}`",
            f"- oracle_label_digest: `{report['oracle_label_digest']}`",
            f"- Preregistration status: {report['preregistration_status'] or 'none supplied'}",
            f"- Cost: {report['cost_note']}", "", "## Arms", ""]
    out += [f"- `{arm_id}`: {label}" for arm_id, label in report["arms"].items()]
    for cell, per_arm in report["cells"].items():
        out += ["", f"## Cell {cell}", "", "| Metric | " + " | ".join(per_arm) + " |",
                "|---|" + "---|" * len(per_arm)]

        def line(name, render):
            out.append(f"| {name} | " + " | ".join(render(m) for m in per_arm.values()) + " |")

        line("assigned", lambda m: str(m["assigned"]))
        for c in OUTCOME_CLASSES:
            line(c, lambda m, c=c: f"{m['class_counts'][c]} ({_fmt(m['class_rates'][c])})")
        line("accepted_rate", lambda m: _fmt(m["accepted_rate"]))
        line("accepted_or_policy_fail_safe_rate",
             lambda m: _fmt(m["accepted_or_policy_fail_safe_rate"]))
        line("false_skip_rate (blocking)",
             lambda m: f"{_fmt(m['false_skip_rate'])} ({m['false_skips']} false skips)")
        line("false_preview_rate", lambda m: _fmt(m["false_preview_rate"]))
        line("schema_validity_rate", lambda m: _fmt(m["schema_validity_rate"]))
        line("coverage", lambda m: _fmt(m["coverage"]))
        line("total_input_tokens (incl. cache; lower bound)",
             lambda m: f"{_fmt(m['total_input_tokens'])} (lower bound; "
                       f"{m['rows_with_null_usage']} rows with null usage)")
        line("total_output_tokens (lower bound)",
             lambda m: f"{_fmt(m['total_output_tokens'])} (lower bound; "
                       f"{m['rows_with_null_usage']} rows with null usage)")
        line("rows reporting cache_read / cache_creation",
             lambda m: f"{m['rows_reporting_cache_fields']['gen_ai.usage.cache_read_input_tokens']}"
                       f" / {m['rows_reporting_cache_fields']['gen_ai.usage.cache_creation_input_tokens']}")
        line("cache_read_ratio", lambda m: "not reported by every row"
             if m["cache_read_ratio"] is None else _fmt(m["cache_read_ratio"]))
        line("tokens per accepted call (lower bound)", lambda m: _fmt(m["tokens_per_accepted"]))
        line("failure spend tokens (lower bound)", lambda m: _fmt(m["failure_spend_tokens"]))
        line("duration_ms p50 / p95",
             lambda m: f"{_fmt(m['duration_ms_p50'])} / {_fmt(m['duration_ms_p95'])} "
                       f"(n={m['duration_n']}; {m['rows_without_duration']} rows without "
                       "duration)")
        line("accepted per hour (concurrency 1)", lambda m: _fmt(m["accepted_per_hour"]))
        line("measurement completeness", lambda m: _fmt(m["measurement_completeness"]))
        line("retries / fallbacks", lambda m: f"{m['retries']} / {m['fallbacks']}")
        line("failure classes", lambda m: json.dumps(m["failure_classes"], sort_keys=True))
        line("worker errors", lambda m: json.dumps(m["worker_errors"], sort_keys=True))
        line("cost_usd", lambda m: "unknown")
    if report["diagnostics_non_scoring"]:
        out += ["", "## Diagnostic (non-scoring): agreement with rules_only", ""]
        for cell, per_arm in report["diagnostics_non_scoring"].items():
            for arm_id, d in per_arm.items():
                out.append(f"- {cell} `{arm_id}`: {_fmt(d['agreement_with_rules_only'])} over "
                           f"{d['n_pairs']} (fixture, repetition) pairs")
    if report["criteria"] is not None:
        out += ["", f"## {CRITERIA_HEADING}", ""]
        for cell, per_arm in report["criteria"].items():
            for arm_id, checks in per_arm.items():
                out.append(f"- {cell} `{arm_id}` (n_pairs={checks['n_pairs']}): " + "; ".join(
                    f"{k}: {v}" for k, v in checks.items() if k != "n_pairs"))
    return "\n".join(out) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench/compare_hosting_variants.py",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True, help=".md or .json, outside any git work tree")
    parser.add_argument("--preregistration")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out)
    if out.suffix not in (".md", ".json"):
        raise CompareError("--out must end in .md or .json")
    if schema.inside_git_tree(out):
        raise CompareError("--out is inside a git work tree: a report names the declared "
                           "endpoint and model identity")
    manifest, rows = load_results(args.results_dir)
    fixtures_doc = schema.load_fixtures(args.fixtures)
    fixture_ids = {fx["fixture_id"] for fx in fixtures_doc["fixtures"]}
    labels_doc = schema.load_labels(args.labels, fixture_ids)
    check_rows(manifest, rows, args.fixtures)
    pre = schema.load_preregistration(args.preregistration) if args.preregistration else None
    held_out = any(fx["split"] == "held_out" for fx in fixtures_doc["fixtures"])
    if held_out and pre is None:
        raise CompareError("a held_out fixture is present: --preregistration is required")
    if pre is not None:
        check_preregistration(pre, manifest, rows, args.fixtures, args.labels)
    report = build_report(manifest, rows, fixtures_doc, labels_doc,
                          schema.sha256_file(args.labels), pre)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".json":
        out.write_text(json.dumps(report, indent=2) + "\n")
    else:
        out.write_text(render_markdown(report))
    print(f"hosting comparator: wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CompareError, schema.ShadowSchemaError) as e:
        print(f"hosting comparator: refused: {e}", file=sys.stderr)
        sys.exit(2)
````

- [ ] **Step 4: Run both new test files and check that they pass.** Test 14 now passes too.

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py tests/test_bench_hosting_compare.py -q
```
Expected: `93 passed`.

- [ ] **Step 5: Commit.**

```bash
git add bench/compare_hosting_variants.py tests/test_bench_hosting_compare.py
git commit -m "feat(#563): oracle-scored per-cell comparator for hosting shadow rows"
```

---

## Task 5: Repository-level checks, preregistration example and operator doc

**Files:**
- Modify: `tests/test_bench_hosting_compare.py` (adds the end-to-end test and spec tests 31–34)
- Create: `bench/preview_classifier_preregistration.example.yaml` (generated)
- Create: `bench/hosting_shadow.md`

- [ ] **Step 1: Append the repository-level tests.** Add two blank lines and then this block
  at the end of `tests/test_bench_hosting_compare.py`:

````python
def test_end_to_end_rules_only_run_reports(tmp_path):
    arms = tmp_path / "arms.yaml"
    arms.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"}]}))
    out_dir = tmp_path / "out"
    shadow_runner.main(["--fixtures", str(EXAMPLE_FIXTURES), "--arms", str(arms),
                        "--out-dir", str(out_dir), "--repetitions", "2", "--seed", "1"])
    report_path = tmp_path / "report.json"
    chv.main(["--results-dir", str(out_dir), "--fixtures", str(EXAMPLE_FIXTURES),
              "--labels", str(EXAMPLE_LABELS), "--out", str(report_path),
              "--preregistration", str(EXAMPLE_PRE)])
    report = json.loads(report_path.read_text())
    rules = report["cells"]["synthetic/dev"]["rules"]
    assert rules["assigned"] == 16
    assert rules["class_counts"]["accepted"] == 14
    assert rules["class_counts"]["policy_fail_safe"] == 2
    assert rules["false_skips"] == 0
    assert report["example_banner"] == chv.EXAMPLE_BANNER
    assert report["criteria"] == {}  # no local arm: nothing to check


# 31
def test_committed_examples_validate_and_digests_match():
    fixtures = schema.load_fixtures(EXAMPLE_FIXTURES)
    ids = {f["fixture_id"] for f in fixtures["fixtures"]}
    assert ids == {f"ex-0{i}" for i in range(1, 9)}
    schema.load_labels(EXAMPLE_LABELS, ids)
    schema.load_arms(BENCH / "hosting_arms.example.yaml")
    pre = schema.load_preregistration(EXAMPLE_PRE)
    assert pre["status"] == "example"
    assert pre["fixture_set_digest"] == schema.sha256_file(EXAMPLE_FIXTURES)
    assert pre["oracle_label_digest"] == schema.sha256_file(EXAMPLE_LABELS)
    rubric = schema.load_rubric()
    assert pre["rubric_sha"] == rubric["rubric_sha"]
    for f in fixtures["fixtures"]:
        schema.render_prompt(rubric["prompt"], f)


NEW_FILES = [*(BENCH / m for m in NEW_MODULES), BENCH / "hosting_arms.example.yaml", EXAMPLE_PRE,
             BENCH / "hosting_shadow.md",
             *sorted((BENCH / "fixtures" / "preview_classifier").rglob("*.*")),
             REPO / "tests" / "test_bench_shadow_runner.py",
             REPO / "tests" / "test_bench_hosting_compare.py"]


# 32
def test_committed_tree_has_only_placeholder_endpoint_identity():
    for path in NEW_FILES:
        for literal in re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])",
                                  path.read_text()):
            ip = ipaddress.ip_address(literal)
            assert ip == ipaddress.ip_address("127.0.0.1") or ip in ipaddress.ip_network(
                "192.0.2.0/24"), (path, literal)
    _, arm = schema.load_arms(BENCH / "hosting_arms.example.yaml")
    assert arm["endpoint_id"] == "example-local"
    assert arm["model_identity"] == {"served_name": "example-model",
                                     "digest_or_revision": "example-digest",
                                     "quantisation": "example-quant"}
    assert arm["hardware"] == "example-hardware"


# 33
def test_nothing_in_production_references_the_shadow_runner():
    names = [m.removesuffix(".py") for m in NEW_MODULES]
    targets = [REPO / "scheduler.sh", REPO / "entrypoint.sh"]
    for root in ("workflows", "commands", "scripts"):
        targets += [p for p in (REPO / root).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts]
    for path in targets:
        text = path.read_text(errors="ignore")
        for name in names:
            assert name not in text, (path, name)


# 34
def test_yaml_files_reject_unknown_keys(tmp_path):
    def write(name, text):
        path = tmp_path / name
        path.write_text(text)
        return path

    labels_text = EXAMPLE_LABELS.read_text()
    arms_text = (BENCH / "hosting_arms.example.yaml").read_text()
    ids = {f"ex-0{i}" for i in range(1, 9)}
    formats = {
        "fixtures": (EXAMPLE_FIXTURES.read_text(), schema.load_fixtures,
                     "preview_enabled: true", "preview_enabled: true\nextra: 1"),
        "labels": (labels_text, lambda p: schema.load_labels(p, ids),
                   "    labeled_by: example-not-an-oracle\n",
                   "    labeled_by: example-not-an-oracle\n    extra: 1\n"),
        "arms": (arms_text, schema.load_arms, "hardware: example-hardware",
                 "hardware: example-hardware\n    extra: 1"),
        "preregistration": (EXAMPLE_PRE.read_text(), schema.load_preregistration,
                            "  seed: 1", "  seed: 1\n  extra: 1"),
    }
    for kind, (text, load, old, new) in formats.items():
        load(write(f"{kind}-ok.yaml", text))
        with pytest.raises(schema.ShadowSchemaError, match="unknown key"):
            load(write(f"{kind}-extra.yaml", text.replace(old, new, 1)))
        with pytest.raises(schema.ShadowSchemaError):
            load(write(f"{kind}-tag.yaml", "!!python/object:os.system {}\n" + text))
    with pytest.raises(schema.ShadowSchemaError, match="no fixture"):
        schema.load_labels(write("unknown.yaml", labels_text.replace("  ex-08:", "  ex-99:")),
                           ids)
    with pytest.raises(schema.ShadowSchemaError, match="duplicate"):
        schema.load_labels(write("dup.yaml", labels_text + "  ex-01:\n    oracle_label: true\n"),
                           ids)
    secret_url = "http://user:hunter2@192.0.2.10:8080"
    bad = write("bad-url.yaml", arms_text.replace("http://192.0.2.10:8080", secret_url))
    with pytest.raises(schema.ShadowSchemaError) as e:
        schema.load_arms(bad)
    assert secret_url not in str(e.value) and "hunter2" not in str(e.value)
````

- [ ] **Step 2: Run it and check that it fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_hosting_compare.py -q
```
Expected: `4 failed, 18 passed`. The failures are
`test_end_to_end_rules_only_run_reports`, `test_committed_examples_validate_and_digests_match`
and `test_yaml_files_reject_unknown_keys`, because the preregistration example is missing,
and `test_committed_tree_has_only_placeholder_endpoint_identity`, because
`bench/hosting_shadow.md` is missing. Test 33 passes on the first run. That is by design: it
is a regression guard showing that nothing in production references the new scripts.

- [ ] **Step 3: Generate the preregistration example.** It needs the real digests of the
  committed example files and the pinned rubric, so it is generated rather than hand-typed.
  Run this one-off snippet from the repo root and do not commit it:

````bash
PYTHONPATH=scripts python3 - <<'PYEOF'
import sys
sys.path.insert(0, "bench")
import hosting_shadow_schema as s

ex = "bench/fixtures/preview_classifier/examples/"
text = f'''# Preregistration EXAMPLE for bench/compare_hosting_variants.py (#563).
# status: example — digests of the examples/ files and illustrative thresholds only. The
# operator follow-up commits a status: registered file, with real values, before any
# held-out scoring. No gate_* script, config key, scheduler or workflow reads this file.
preview_classifier_preregistration:
  schema_version: "1.0"
  status: example
  registered_by: example-not-a-registration
  registered_at: "2026-11-27T00:00:00+00:00"
  fixture_set_digest: {s.sha256_file(ex + "fixtures.yaml")}
  oracle_label_digest: {s.sha256_file(ex + "oracle_labels.yaml")}
  rubric_sha: {s.sha256_file(s.RUBRIC_PATH)}
  repetitions: 2
  seed: 1
  criteria:
    max_false_skips_held_out: 0
    max_false_skip_rate: 0.0
    min_schema_validity_rate: 0.95
    min_coverage: 0.9
    p95_duration_ms_slo: 30000
    min_false_preview_rate_reduction_vs_rules_only: 0.1
  invalid_run_handling: "{s.INVALID_RUN_HANDLING}"
'''
open("bench/preview_classifier_preregistration.example.yaml", "w").write(text)
print(s.load_preregistration("bench/preview_classifier_preregistration.example.yaml")["status"])
PYEOF
````
Expected output: `example`. The file must carry `status: example` and three 64-hex digests.
If you edit any example file or the rubric pin later, rerun this snippet, or test 31 fails.

- [ ] **Step 4: Create `bench/hosting_shadow.md`** with exactly the content below.
  `#FOLLOWUP_ISSUE_NUMBER` is a literal token that Task 7 replaces with the follow-up issue's
  real number. The number only exists once that issue is filed.

````markdown
# Hosting shadow comparator — operator guide (#563)

**NON-AUTHORITATIVE.** This instrument replays the pinned `classify-preview` prompt against
labelled fixtures on a `rules_only` arm and one or more `local` arms, and reports quality,
token, latency and failure figures per arm. Its output is never a promotion decision,
GO/NO-GO, production-readiness or savings evidence. **v1 has no cloud arm**: it makes no
cloud/local cost or quality comparison and supports no savings claim.

Design: `docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md`.
Fixture formats and labelling rules: `bench/fixtures/preview_classifier/README.md`.

**Operator follow-up issue:** #FOLLOWUP_ISSUE_NUMBER — label the real fixture set, commit the
`registered` preregistration, run against the endpoint #562 certifies, publish the report and
the offline/shadow GO/NO-GO evidence, and record the task-clustered uncertainty #564 needs.

## Files

| File | Role |
|---|---|
| `bench/shadow_runner.py` | Launcher: start-up refusals, run order, one worker per local call, `rows.jsonl` + `manifest.json` |
| `bench/_shadow_worker.py` | Worker: one `POST /v1/messages`, one JSON object on stdout, no file written |
| `bench/hosting_shadow_schema.py` | Shared validation, digests, prompt rendering, verdict parsing, the `rules_only` reading |
| `bench/compare_hosting_variants.py` | Comparator: joins the oracle labels, writes the `.md`/`.json` report |
| `bench/hosting_arms.example.yaml` | The only committed arms file (placeholders) |
| `bench/preview_classifier_preregistration.example.yaml` | Preregistration schema example (`status: example`) |

Nothing in `workflows/`, `scheduler.sh`, `entrypoint.sh`, `commands/` or `scripts/` calls
these files, and no gate, budget, breaker or report reads their output.

## Running it

The launcher needs `pyyaml` and the worker needs `aiohttp`. The worker runs as `python -I`,
which ignores user site-packages, so install both system-wide or in the virtualenv whose
`python3` runs the launcher — not with `pip install --user` (a worker that cannot import
`aiohttp` shows up as `worker_error: crash` on every local row).

1. Copy `bench/hosting_arms.example.yaml` to a directory **outside every git work tree** and
   fill in the local endpoint's `base_url`, `endpoint_id`, declared `model_identity`,
   `runtime_stack` and `hardware`. `secret_env` is the *name* of the environment variable that
   holds the endpoint's key. A server that needs no key still needs a named variable — set a
   dummy, for example `export SHADOW_LOCAL_API_KEY=unused` — so every arm's worker environment
   has the same shape.
2. Run from a shell that holds **no** `GH_TOKEN`, `GITHUB_TOKEN` or cloud model credential
   (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`). The launcher refuses to start if
   any is set; there is no override.

   ```bash
   python3 bench/shadow_runner.py \
     --fixtures bench/fixtures/preview_classifier/examples/fixtures.yaml \
     --arms /srv/shadow/arms.yaml --out-dir /srv/shadow/out-001 --repetitions 3 --seed 1
   ```

   `--arms` and `--out-dir` must be outside any git work tree; `--out-dir` must not be under
   `run-records/` or `model-registry/`, must not contain a `runs.jsonl`, and must not already
   hold `rows.jsonl` or `manifest.json` — every rerun gets a new directory.
3. Score it:

   ```bash
   python3 bench/compare_hosting_variants.py \
     --results-dir /srv/shadow/out-001 \
     --fixtures bench/fixtures/preview_classifier/examples/fixtures.yaml \
     --labels bench/fixtures/preview_classifier/examples/oracle_labels.yaml \
     --out /srv/shadow/report-001.md
   ```

   `--out` (`.md` or `.json`) must be outside any git work tree: a report names the declared
   endpoint and model identity. A fixture set with any `split: held_out` fixture requires
   `--preregistration`, whose digests, `repetitions` and `seed` must match the run and whose
   `registered_at` must precede the first row.

### Real OS-level isolation: a throwaway container

The launcher's isolation is environment-level (below). For a stronger boundary run it in a
throwaway container with no Docker socket and no tokens, mounting **only** `bench/` and
`scripts/` read-only:

```bash
docker run --rm \
  -v "$PWD/bench":/src/bench:ro \
  -v "$PWD/scripts":/src/scripts:ro \
  -v /path/outside/every/repository/shadow:/shadow \
  -e SHADOW_LOCAL_API_KEY \
  python:3.12-slim sh -c "pip -q install aiohttp pyyaml && \
    python /src/bench/shadow_runner.py \
      --fixtures /src/bench/fixtures/preview_classifier/examples/fixtures.yaml \
      --arms /shadow/arms.yaml --out-dir /shadow/out-001 --repetitions 3 --seed 1"
```

**Never mount the repository root, `.archon/` or `deploy/`**: `.archon/.env` and `deploy/*.env`
hold live tokens, and a read-only mount still exposes them to the process. Inside a container
the path rule sees only the container's filesystem — it cannot see a `.git` on the host side of
a mount — so keeping the host directory behind `/shadow` outside every repository is your
responsibility.

## What is guaranteed, and what is not

Guaranteed by construction and tested: each worker's environment is exactly `PATH`, a fresh
temporary `HOME`, `SHADOW_BASE_URL` and the arm's one secret; no GitHub token or cloud model
credential is present in the launcher; the only network destination is the arm's `base_url`;
redirects are not followed; a failed call is recorded and never retried on any host; nothing
persistent is written outside `--out-dir`/`--out` (each call's job file, `HOME` and working
directory are temporary and deleted after it); no oracle label reaches a prompt; the model's
output is parsed as data and never executed. `base_url` is written nowhere — the manifest
records only its `base_url_scope` (`same-host | lan | remote`). A `local` arm whose address
classifies as `remote` is allowed, and the report prints `declared local; address is remote`.

Not guaranteed: this is **not an OS-level sandbox**. A worker can still read the filesystem and
reach whatever the host network allows. `bench/` is baked into the factory image, so these
scripts exist inside production run containers; the token refusals are what stop the launcher
running there. Identity fields are what you declare (`identity_source: declared`); the only
identity the runner checks is the served model name on each response. The import rule is
source-level: importing `factory_core.providers.model_adapter` loads the `factory_core`
package modules, but the new modules name and call none of the GitHub-facing ones, and the
guarantee that nothing GitHub-facing can act comes from the missing token.

Data that leaves to the local endpoint: the rendered prompt (the pinned rubric plus one
fixture's changed-file paths, stat text, issue title and label names) and the arm's own
secret. Nothing else.

## Reading the report

- Every report opens with the non-authoritative / no-cloud-arm header. With a
  `status: example` preregistration it adds `EXAMPLE DATA — tooling test only`.
- Figures are per arm and per (provenance × split) cell — `historical/dev`,
  `historical/held_out`, `synthetic/dev`, `synthetic/held_out` — never pooled, and with no
  composite score.
- `false_skip_rate` is the blocking metric: a parsed `false` on an `oracle_label: true`
  fixture. A response with no parseable verdict counts as `true` (uncertainty keeps the
  preview), so a `schema_invalid` row is never a false skip but stays in every denominator.
- Outcome classes, top-down: `infrastructure_invalid` (no response), `schema_invalid`,
  `policy_fail_safe` (a correct `true` on an `expected_fail_safe` fixture), `accepted`,
  `substantive_failure`.
- Every input-token figure includes the reported cache fields; an absent cache field is
  absent, never 0 and never a cache hit. Token totals are **lower bounds**, printed with the
  number of rows whose usage is null. A ratio with a zero denominator is `undefined`.
- `cost_usd` is `unknown` for every arm; no electricity, amortization or human-effort estimate
  and no fully loaded cost is computed in v1. `energy_joules` is null in every row.
- Queue time, TTFT, prefill/decode split, peak memory and OOM are listed in each row's
  `unsupported_measurements`: a non-streaming replay cannot observe them.
- The `rules_only` arm is labelled `rules_only (reading of runner_version <v>)`: it is one
  deterministic reading of the rubric, not the rubric. Agreement between a local arm and
  `rules_only` is a non-scoring diagnostic only.
- With a preregistration, `Preregistered criteria check — not a GO/NO-GO` lists each criterion
  per local arm and cell as `met`, `not met` or `not evaluable`. A cell containing a timed-out
  row never meets `p95_duration_ms_slo`.
````

- [ ] **Step 5: Run it and check that it passes.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_bench_shadow_runner.py tests/test_bench_hosting_compare.py -q
```
Expected: `98 passed`.

- [ ] **Step 6: Commit.**

```bash
git add bench/preview_classifier_preregistration.example.yaml bench/hosting_shadow.md tests/test_bench_hosting_compare.py
git commit -m "feat(#563): preregistration example, operator guide and repository-level checks"
```

---

## Task 6: Reference docs

This task changes documentation only, so no new failing test applies. The existing
`tests/test_docs_refs_check.py` and `scripts/docs_refs_check.py` guard it: every backtick
path added below must exist, which Tasks 1–5 guarantee.

**Files:**
- Modify: `docs/features.md`, `README.md`, `docs/configuration.md`, `bench/baseline.md`

- [ ] **Step 1: `docs/features.md`.** Add a new row directly after the row that begins
  `| **Replay bench.**` and before the row that begins `| **Offline evaluations.**`, in the
  "Operations and tooling" table:

```markdown
| **Hosting shadow comparator.** Replays the pinned `classify-preview` prompt against labelled fixtures on a `rules_only` arm and `local` arms, each scored against a human oracle, and reports quality, tokens, latency and failure figures per (provenance × split) cell. v1 has no cloud arm and makes no cost or savings claim (#563). | **Measured only** (operator-run). Nothing in `workflows/`, `scheduler.sh`, `entrypoint.sh`, `commands/` or `scripts/` calls it; no gate reads its output. Tests: `tests/test_bench_shadow_runner.py`, `tests/test_bench_hosting_compare.py`. | `bench/shadow_runner.py`, `bench/_shadow_worker.py`, `bench/compare_hosting_variants.py`, `bench/hosting_shadow_schema.py`, `bench/fixtures/preview_classifier/` | Manual; refuses to start with `GH_TOKEN`, `GITHUB_TOKEN` or a cloud model credential set | [`bench/hosting_shadow.md`](../bench/hosting_shadow.md) | Only example fixtures are committed; labelling, the registered preregistration and the real run wait on the operator follow-up and #562. Isolation is environment-level, not an OS sandbox |
```

- [ ] **Step 2: `README.md`, bench section.** Directly after the paragraph that ends
  ``a target-specific suite alongside `BENCH_TARGET_DIR`.`` (in "### Bench parity"), insert a
  blank line and then this paragraph. It sits before the `---` that closes the section:

```markdown
The hosting shadow comparator (`bench/shadow_runner.py`, `bench/compare_hosting_variants.py`)
is a separate, non-authoritative instrument that replays only the preview classifier's
prompt against labelled fixtures; it never runs the workflow. See
[`bench/hosting_shadow.md`](bench/hosting_shadow.md).
```

- [ ] **Step 3: `README.md`, document index.** In "## Further reading", directly after the line

```markdown
- [`bench/baseline.md`](bench/baseline.md) — replay benchmark task manifest and scoring formula
```
add
```markdown
- [`bench/hosting_shadow.md`](bench/hosting_shadow.md) — hosting shadow comparator (#563): isolated rules-only/local replay of the preview classifier; non-authoritative, no cloud arm
```

- [ ] **Step 4: `docs/configuration.md`.** Under "### Variables not derived from a config key"
  → "**Feature flags and switches**", directly after the row

```markdown
| `CONFORMANCE_SHADOW_MODEL` | unset | `commands/dark-factory-conformance.md:77` | `.archon/.env` |
```
add these three rows:
```markdown
| `SHADOW_BASE_URL` | set by the launcher only | `bench/_shadow_worker.py` (the one local arm's endpoint, from the arms file's `base_url`) | never set by hand; the worker's environment is exactly `PATH`, `HOME`, `SHADOW_BASE_URL` and the arm's `secret_env` (#563) |
| (arm `secret_env`, e.g. `SHADOW_LOCAL_API_KEY`) | none | `bench/shadow_runner.py` (copied into the worker under the same name), `bench/_shadow_worker.py` (sent as `x-api-key`) | the operator's shell; a keyless server takes a dummy value (`bench/hosting_shadow.md`) |
| `GH_TOKEN`, `GITHUB_TOKEN`, `model_endpoint.CLOUD_CREDENTIALS` names | must be unset | `bench/shadow_runner.py` presence check: the launcher refuses to start if any is set (no override) | the operator's shell (#563) |
```

- [ ] **Step 5: `bench/baseline.md`.** Directly before the first `---` line, the one that
  precedes `## pass^k by Size Bucket`, insert this paragraph followed by a blank line:

```markdown
This is the replay bench: it re-runs whole tickets through the workflow. The hosting shadow
comparator (`bench/hosting_shadow.md`, #563) is separate — it replays only the preview
classifier's prompt against labelled fixtures and never runs the workflow.
```

- [ ] **Step 6: Run the check and confirm that it passes.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py tests/test_implement_doc_impact.py tests/test_docs_conformance_reference_docs.py -q
```
Expected: `Docs reference check passed for 4 doc(s).`, then all of those tests pass.

- [ ] **Step 7: Commit.**

```bash
git add docs/features.md README.md docs/configuration.md bench/baseline.md
git commit -m "docs(#563): index the hosting shadow comparator in features, README and configuration"
```

---

## Task 7: File the operator follow-up (idempotent) and record its number

Spec "Filing the follow-up". The PR body comes from the `push-and-pr` bash node's fixed
template, so the follow-up is recorded in `bench/hosting_shadow.md` and in one comment on
#563 instead. The repository is hard-pinned. The search is idempotent, so a retry or a
`Continue` run never files a second issue. The marker appears both as an HTML comment and as
a visible line, because the operator review could not confirm that GitHub search indexes
HTML comments. A full-body scan backs up the search.

There is no automated test for this task (it writes to GitHub). The script's last two
commands are its verification.

**Files:**
- Modify: `bench/hosting_shadow.md` (replace `#FOLLOWUP_ISSUE_NUMBER`)

- [ ] **Step 1: Write the follow-up script outside the clone.** Shell variables do not
  survive between Bash tool calls, so finding or filing the issue, recording its number,
  posting the comment and verifying all happen in **one** script run in **one** shell. With
  the Write tool, create `/tmp/followup-563.sh` (outside the clone, never committed) with
  exactly:

````bash
set -euo pipefail
REPO_SLUG=omniscient/dark-factory
MARKER=followup-of-563-operator-run
DOC=bench/hosting_shadow.md

has_marker() {  # the candidate's body really carries the HTML marker
  local body
  body=$(gh issue view "$1" --repo "$REPO_SLUG" --json body --jq '.body') || return 1
  grep -qF "<!-- $MARKER -->" <<<"$body"
}

# 1. Find. A failed lookup aborts the script here (set -e): never create on a search error.
FOUND=$(gh issue list --repo "$REPO_SLUG" --state all --search "$MARKER in:body" \
  --json number --jq '.[].number')
FOLLOWUP=""
for n in $FOUND; do
  if has_marker "$n"; then FOLLOWUP=$n; break; fi
done
if [ -z "$FOLLOWUP" ]; then  # the search index lags a fresh issue: scan bodies directly
  FOLLOWUP=$(gh issue list --repo "$REPO_SLUG" --state all --limit 1000 --json number,body \
    --jq "[.[] | select(.body | contains(\"<!-- $MARKER -->\"))][0].number // empty")
fi

# 2. File it only when neither lookup found one.
if [ -z "$FOLLOWUP" ]; then
  BODY_FILE=$(mktemp)
  cat > "$BODY_FILE" <<'EOF'
<!-- followup-of-563-operator-run -->
## Operator run: hosting shadow comparator for the preview classifier (#563 follow-up)

Depends on: #562

#563 delivered the instrument only: `bench/shadow_runner.py`, `bench/compare_hosting_variants.py` and the operator guide `bench/hosting_shadow.md`. Its acceptance criteria 4 (independent oracle and preregistered criteria) and 6 (offline then non-authoritative shadow GO/NO-GO evidence) stay open here. This is human work: the factory does not label fixtures, register criteria or run against a real endpoint.

- [ ] Label the real fixture set (historical, plus sealed synthetic held-out cases) under `bench/fixtures/preview_classifier/`, blind to every arm's output, against the pinned oracle question in `bench/fixtures/preview_classifier/README.md`.
- [ ] Commit the `status: registered` preregistration (digests, repetitions, seed, thresholds) before any held-out scoring.
- [ ] Run `bench/shadow_runner.py` against the endpoint #562 certifies, from a shell with no repository token or cloud credential, writing outside every git work tree.
- [ ] Publish the `bench/compare_hosting_variants.py` report and the offline/shadow GO/NO-GO evidence.
- [ ] Record the task-clustered uncertainty #564 needs (the paired-probe protocol is #467).

The report is non-authoritative. v1 has no cloud arm, so it supports no cloud/local cost or savings claim. The operator repoints #564's dependency to this issue.

Marker: followup-of-563-operator-run
EOF
  URL=$(gh issue create --repo "$REPO_SLUG" \
    --title "Operator run: label fixtures, register criteria and run the #563 hosting shadow comparator" \
    --label ready-for-human --body-file "$BODY_FILE")
  rm -f "$BODY_FILE"
  FOLLOWUP=${URL##*/}
fi
case "$FOLLOWUP" in
  ''|*[!0-9]*) echo "ERROR: no follow-up issue number (got '$FOLLOWUP')" >&2; exit 1 ;;
esac
echo "FOLLOWUP=$FOLLOWUP"

# 3. Record the number in the operator doc (a no-op when a previous run already did).
sed -i "s/#FOLLOWUP_ISSUE_NUMBER/#${FOLLOWUP}/" "$DOC"
grep -n "Operator follow-up issue:\*\* #${FOLLOWUP} " "$DOC"

# 4. Post the one comment on #563, unless a comment already names the follow-up.
COMMENTS=$(gh issue view 563 --repo "$REPO_SLUG" --json comments --jq '.comments[].body')
if grep -Eq "followup-of-563-operator-run-comment|#${FOLLOWUP}([^0-9]|$)" <<<"$COMMENTS"; then
  echo "comment already present — skipped"
else
  COMMENT_FILE=$(mktemp)
  cat > "$COMMENT_FILE" <<EOF
<!-- followup-of-563-operator-run-comment -->
## Instrument delivered — operator follow-up filed

The #563 PR delivers the hosting shadow comparator instrument only (\`bench/shadow_runner.py\`, \`bench/compare_hosting_variants.py\`, operator guide \`bench/hosting_shadow.md\`). **Acceptance criteria 4 (independent oracle and preregistered criteria) and 6 (offline then shadow GO/NO-GO evidence) are not satisfied by the PR.** They move to the \`ready-for-human\` follow-up #${FOLLOWUP}, together with the fixture labelling, the \`registered\` preregistration, the real run against the endpoint #562 certifies and the task-clustered uncertainty #564 needs. v1 has no cloud arm and supports no savings claim.
EOF
  gh issue comment 563 --repo "$REPO_SLUG" --body-file "$COMMENT_FILE"
  rm -f "$COMMENT_FILE"
fi

# 5. Verify.
gh issue view "$FOLLOWUP" --repo "$REPO_SLUG" --json labels,body \
  --jq '[.labels[].name, (.body | contains("<!-- followup-of-563-operator-run -->")), (.body | contains("Depends on: #562"))]'
grep -c "FOLLOWUP_ISSUE_NUMBER" "$DOC" || true
````

- [ ] **Step 2: Run it from the repo root.**

```bash
bash /tmp/followup-563.sh
```
Expected, in order: `FOLLOWUP=<number>`; the doc line
`**Operator follow-up issue:** #<number> — label the real fixture set, …`; a comment URL or
`comment already present — skipped`; `["ready-for-human",true,true]`; then `0`. Exit status 0.
A second run files nothing and posts nothing.

  **If it exits non-zero**, nothing was recorded and `#FOLLOWUP_ISSUE_NUMBER` is still in the
  doc. Read the error:

  - A failed `gh issue list` or `gh issue view` (network, rate limit): run the script once
    more. Never file the issue by hand to get past it — the lookup is what prevents a
    duplicate.
  - A failed `gh issue create` (for example, the token cannot create issues or apply
    `ready-for-human`), or a second failed lookup: do not loop and do not invent a number.
    Run
    `sed -i 's/#FOLLOWUP_ISSUE_NUMBER/not yet filed — the operator files it (see the #563 PR)/' bench/hosting_shadow.md`,
    post no comment, and record the `gh` error text in the Step 3 commit message.

- [ ] **Step 3: Commit.**

```bash
git add bench/hosting_shadow.md
git commit -m "docs(#563): record the operator follow-up issue in the hosting shadow guide"
```

---

## Task 8: Final verification

**Files:** none are modified.

- [ ] **Step 1: Run the full suite** (as CI does).

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```
Expected: everything passes. Counted while planning: 4038 passed, which was 3940 before this
plan plus 98. These must stay green unchanged: `tests/test_bench_compare.py`,
`tests/test_bench_suite.py` and `tests/test_capability_suite.py`. The last one's
`PINNED_FIXTURE_SUITE_VERSION` assertion proves `capability_suite/` was not edited.

- [ ] **Step 2: Run the workflow DAG checks** (unaffected; run them for CI parity).

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```
Expected: both exit 0.

- [ ] **Step 3: Check scope with a two-dot diff against `main`** (memory [PATTERN] #250). The
  two-dot form avoids false positives from commits that `main` merged independently.

```bash
git fetch -q origin main
git diff --name-only origin/main HEAD | sort
```
Expected: exactly the 17 files in the File Structure table, plus this plan and the spec
under `docs/superpowers/`, and any `.archon/memory/` files the refine phase committed on this
branch. Then run:

```bash
git diff --quiet origin/main HEAD -- bench/compare_variants.py bench/run_suite.sh scripts/ workflows/ config/ deploy/ .github/ commands/ scheduler.sh entrypoint.sh && echo "untouched"
```
Expected: `untouched`.

- [ ] **Step 4: Run a smoke test of the CLIs** against the committed examples. It uses a
  rules_only-only arms file outside the repository, so it makes no request. It must run in a
  shell with no `GH_TOKEN`, `GITHUB_TOKEN` or cloud credential set, so the `env -u` prefixes
  unset them.

```bash
TMP=$(mktemp -d)
printf 'arms:\n  - arm_id: rules\n    arm_kind: rules_only\n' > "$TMP/arms.yaml"
env -u GH_TOKEN -u GITHUB_TOKEN -u ANTHROPIC_API_KEY -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_AUTH_TOKEN \
    -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u GOOGLE_APPLICATION_CREDENTIALS \
  python3 bench/shadow_runner.py --fixtures bench/fixtures/preview_classifier/examples/fixtures.yaml \
    --arms "$TMP/arms.yaml" --out-dir "$TMP/out" --repetitions 2 --seed 1
python3 bench/compare_hosting_variants.py --results-dir "$TMP/out" \
  --fixtures bench/fixtures/preview_classifier/examples/fixtures.yaml \
  --labels bench/fixtures/preview_classifier/examples/oracle_labels.yaml \
  --preregistration bench/preview_classifier_preregistration.example.yaml --out "$TMP/report.md"
head -5 "$TMP/report.md"; rm -rf "$TMP"
```
Expected: the runner prints `shadow runner: wrote 16 rows to …/rows.jsonl` and the comparator
prints `hosting comparator: wrote …/report.md`. The report begins
`# Hosting shadow report — preview classifier`, then the bold non-authoritative / no-cloud-arm
header, then `**EXAMPLE DATA — tooling test only**`.

- [ ] **Step 5: Confirm the push.** No new commit is needed if Steps 1–4 passed. Leave the
  pushing to the workflow's `push-and-pr` node, and do not open a PR by hand.

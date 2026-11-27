# Hosting shadow comparator — operator guide (#563)

**NON-AUTHORITATIVE.** This instrument replays the pinned `classify-preview` prompt against
labelled fixtures on a `rules_only` arm and one or more `local` arms, and reports quality,
token, latency and failure figures per arm. Its output is never a promotion decision,
GO/NO-GO, production-readiness or savings evidence. **v1 has no cloud arm**: it makes no
cloud/local cost or quality comparison and supports no savings claim.

Design: `docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md`.
Fixture formats and labelling rules: `bench/fixtures/preview_classifier/README.md`.

**Operator follow-up issue:** #638 — label the real fixture set, commit the
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

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

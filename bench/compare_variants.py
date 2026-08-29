#!/usr/bin/env python3
"""Compare two harness-swap replay variants over bench/'s run-records — issue #240.

Loads a `--variants variants.yaml` declaration (two arms) plus every `*-run-record.json`
under `--results-dir` (bench/run_suite.sh's per-invocation harness_economics output, wired
in by df#240 Task 1), joins on (issue, run) per variant_id via each aggregate `*-run.json`'s
`runs[].variant_id`/`runs[].run`/`runs[].run_id`, and renders the promotion/rollback table.
Joining on the explicit `variant_id` field (not a `run_id` prefix match) avoids
misattribution when one variant_id is itself a prefix of another; joining on (issue, run)
rather than issue alone keeps every run of a multi-run (`--n > 1`) arm as its own paired data
point instead of collapsing them.

Only `dimension: economics` variants are runnable by this ticket. `memory_intervention`
(#241) and `contract_trajectory` (#311 follow-up) are reserved schema values that must
fail loudly, not silently no-op — see the spec's Reserved dimensions section.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import yaml

_RESERVED_DIMENSIONS = {
    "memory_intervention": "reserved for #241 (proactive-memory epic — no code yet)",
    "contract_trajectory": "reserved for #311 follow-up (contract/trajectory evalset)",
}

_ECONOMICS_METRICS = ("cost_per_task", "tokens_per_task", "wall_clock_seconds")

# The one env var allowed to differ between arms — everything else must match, or the compare
# is no longer isolating a single economics lever (spec Requirement #4).
_ECONOMICS_ENV_ALLOWLIST = {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS"}
# The one token_optimization sub-key allowed to differ — architecture/memory/comments/diff/
# budgets etc. must be identical in both arms per the Gate-criteria section.
_ECONOMICS_CONFIG_KEY_ALLOWLIST = {"enforce_budgets"}


def load_variants(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text())
    variants = data["variants"]
    if len(variants) != 2:
        raise ValueError(f"compare_variants.py compares exactly 2 arms, got {len(variants)}")
    return variants


def assert_only_economics_keys_differ(variants: list[dict]) -> None:
    """Refuse to compare unless the two arms differ ONLY in the intended economics lever —
    the mechanical enforcement of spec Requirement #4 ('without changing task/model inputs
    unnecessarily') and the Gate-criteria section's instruction to hold architecture/memory/
    comments/diff at committed defaults in both arms. This is a genuine cross-arm comparison,
    not a per-arm allowlist check — two arms can each individually look economics-only while
    still differing from EACH OTHER on an untested axis (e.g. both touch config_overlay.
    token_optimization, but one also changes .architecture) — that must still refuse."""
    if len(variants) != 2:
        raise ValueError("assert_only_economics_keys_differ expects exactly 2 variants")
    a, b = variants

    if a.get("fixture_set") != b.get("fixture_set"):
        raise ValueError(
            f"variants '{a['variant_id']}'/'{b['variant_id']}' use different fixture_set "
            f"values ({a.get('fixture_set')!r} vs {b.get('fixture_set')!r}) — compare refuses"
        )
    if a.get("image") != b.get("image"):
        raise ValueError(
            f"variants '{a['variant_id']}'/'{b['variant_id']}' declare different images — "
            f"compare refuses (image-swap variants are a separate, reserved comparison)"
        )

    for v in (a, b):
        overlay = v.get("config_overlay") or {}
        bad = set(overlay.keys()) - {"token_optimization"}
        if bad:
            raise ValueError(
                f"variant '{v['variant_id']}' config_overlay touches non-economics config "
                f"keys {sorted(bad)} — compare refuses (spec: without changing task/model "
                f"inputs unnecessarily)"
            )
        bad_env = set((v.get("env") or {}).keys()) - _ECONOMICS_ENV_ALLOWLIST
        if bad_env:
            raise ValueError(
                f"variant '{v['variant_id']}' env touches non-economics vars {sorted(bad_env)} "
                f"— compare refuses"
            )

    overlay_a = (a.get("config_overlay") or {}).get("token_optimization", {})
    overlay_b = (b.get("config_overlay") or {}).get("token_optimization", {})
    # Union, not intersection: a key present in ONLY one arm's overlay (the other arm implicitly
    # holds it at the committed default) must still be checked against that default, not skipped.
    # An intersection-only check would miss e.g. arm A overriding .architecture while arm B
    # omits it — silently passing a case the Gate-criteria section requires refused.
    for key in (set(overlay_a) | set(overlay_b)) - _ECONOMICS_CONFIG_KEY_ALLOWLIST:
        val_a, val_b = overlay_a.get(key), overlay_b.get(key)
        if val_a != val_b:
            raise ValueError(
                f"variants '{a['variant_id']}'/'{b['variant_id']}' differ on non-economics "
                f"token_optimization.{key} ({val_a!r} vs {val_b!r}) — compare refuses "
                f"(architecture/memory/comments/diff must be held at committed defaults in "
                f"both arms per spec Gate criteria)"
            )


def determine_rollback_tier(variant: dict) -> str:
    """Tier 0/1/2/none per docs/dark-factory-token-optimization.md's ladder. This ticket's
    worked example only ever produces Tier 0 (env kill-switch) — the Tier 1/2 config_overlay
    branches below are exercised by no fixture in this plan's test suite; they exist so a
    follow-up config_overlay-based variant doesn't need a new function, not because this
    ticket validates their heuristic against a real case."""
    env = variant.get("env") or {}
    if "TOKEN_OPTIMIZATION_ENFORCE_BUDGETS" in env:
        return "0"
    if variant.get("image") and not env:
        # image/deploy-swap variants have no Tier 0 — deploy/** is human-only.
        return "none"
    overlay = (variant.get("config_overlay") or {}).get("token_optimization", {})
    if "enforce" in overlay and len(overlay) == 1 and len(overlay["enforce"]) == 1:
        return "2"  # single-scenario enforce.<x> revert
    if overlay:
        return "1"  # master config revert
    return "none"


def _load_run_records(results_dir: Path) -> dict[str, dict]:
    records = {}
    for f in Path(results_dir).glob("*-run-record.json"):
        data = json.loads(f.read_text())
        records[data["run_id"]] = data
    return records


def _load_aggregate_runs(results_dir: Path) -> list[dict]:
    """Every {issue, run_id, variant_id, passed, pass_k}-shaped row across all *-run.json
    summaries. variant_id comes from the row itself (bench/run_suite.sh's RUN_RESULT block
    writes it verbatim from --variant-id) — never parsed back out of run_id, which would be
    fragile against one variant_id being a prefix of another."""
    rows = []
    for f in Path(results_dir).glob("*-run.json"):
        data = json.loads(f.read_text())
        for task in data.get("tasks", []):
            for run in task.get("runs", []):
                rows.append({
                    "issue": task["issue"],
                    "pass_k": task.get("pass_k"),
                    "run": run["run"],
                    "run_id": run.get("run_id"),
                    "variant_id": run.get("variant_id"),
                    "passed": run.get("passed"),
                })
    return rows


def join_variant_results(variants: list[dict], results_dir: Path) -> dict[str, dict]:
    for v in variants:
        dim = v.get("dimension", "economics")
        if dim in _RESERVED_DIMENSIONS:
            raise NotImplementedError(
                f"dimension '{dim}' is {_RESERVED_DIMENSIONS[dim]} — not runnable by this "
                f"compare_variants.py yet"
            )
        if dim != "economics":
            raise NotImplementedError(f"unknown dimension '{dim}'")
    assert_only_economics_keys_differ(variants)

    run_records = _load_run_records(results_dir)
    agg_rows = _load_aggregate_runs(results_dir)

    joined: dict[str, dict] = {}
    for v in variants:
        vid = v["variant_id"]
        # Key on (issue, run) — not issue alone. bench/run_suite.sh's --n produces multiple
        # runs per issue (variants.example.yaml's own worked-example usage is --n 3); an
        # issue-only key would let run 2 and run 3 silently overwrite run 1's entry, discarding
        # 2/3 of the paired data spec Decisions' "n ≤ 10 paired same-issue runs" methodology
        # expects to see.
        by_run: dict[tuple[int, int], dict] = {}
        for row in agg_rows:
            if row.get("variant_id") != vid:
                continue
            record = run_records.get(row.get("run_id") or "")
            if record is None:
                continue
            by_run[(row["issue"], row["run"])] = {
                "pass_k": row.get("pass_k"),
                "passed": row.get("passed"),
                "harness_economics": record["harness_economics"],
            }
        if not by_run:
            raise ValueError(
                f"no matching runs found for variant_id '{vid}' under {results_dir} — check "
                f"bench/run_suite.sh was invoked with --variant-id {vid!r} and --results-dir "
                f"points at that invocation's output"
            )
        joined[vid] = by_run
    return joined


def paired_values(joined: dict[str, dict], metric: str) -> list[tuple[float, float]]:
    """One (before, after) tuple per (issue, run) key present in both arms with a non-null
    metric — each run is its own paired data point (spec Decisions: 'n <= 10 paired
    same-issue/same-pre_pr_sha runs'), not collapsed to one point per issue.
    variants[0] is 'before' (baseline/current default); variants[1] is 'after'."""
    (before_id, before), (after_id, after) = list(joined.items())[:2]
    pairs = []
    for key, before_row in before.items():
        after_row = after.get(key)
        if after_row is None:
            continue
        b = before_row["harness_economics"].get(metric)
        a = after_row["harness_economics"].get(metric)
        if b is None or a is None:
            continue  # cost_unavailable etc. — never coerced to 0, excluded from this metric only
        pairs.append((b, a))
    return pairs


def paired_median_delta(joined: dict[str, dict], metric: str) -> "float | None":
    pairs = paired_values(joined, metric)
    if not pairs:
        return None
    deltas = [a - b for b, a in pairs]
    return statistics.median(deltas)


def build_report(variants: list[dict], joined: dict[str, dict],
                  outcome_bound: "float | None" = None,
                  improvement_threshold: "float | None" = None) -> dict:
    before_id, after_id = [v["variant_id"] for v in variants][:2]
    economics_deltas = {m: paired_median_delta(joined, m) for m in _ECONOMICS_METRICS}
    outcome_pairs = [
        (joined[before_id][k]["pass_k"], joined[after_id][k]["pass_k"])
        for k in joined[before_id] if k in joined[after_id]
        and joined[before_id][k]["pass_k"] is not None
        and joined[after_id][k]["pass_k"] is not None
    ]
    outcome_delta = (
        statistics.median([a - b for b, a in outcome_pairs]) if outcome_pairs else None
    )

    # Gate-criteria section: outcome.score/factory_cpm are reported ALONGSIDE pass^k (the
    # actual gate metric) but explicitly flagged stub-mode/not-gate-bearing — under
    # BENCH_MODE=stub, outcome is always produced_ungated/1.0 or failed/0.0, so a factory_cpm
    # delta here reflects token spend, not a real quality signal, and must never be read as one.
    def _after_median(getter) -> "float | None":
        vals = [getter(row["harness_economics"]) for row in joined[after_id].values()]
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    outcome_score_after_median = _after_median(lambda he: he.get("outcome", {}).get("score"))
    factory_cpm_after_median = _after_median(lambda he: he.get("factory_cpm"))

    gate_verdict = "ungated (thresholds not pinned)"
    if outcome_bound is not None and improvement_threshold is not None and outcome_delta is not None:
        cost_delta = economics_deltas.get("cost_per_task")
        gate_verdict = "pass" if (
            outcome_delta >= outcome_bound and cost_delta is not None and cost_delta <= -improvement_threshold
        ) else "fail"

    return {
        "variant_id": after_id,
        "baseline_variant_id": before_id,
        "outcome_delta_pass_k": outcome_delta,
        "economics_delta": economics_deltas,
        # Stub-mode, not gate-bearing — see Gate-criteria section. Reported for visibility only;
        # never fed into gate_verdict above (that uses outcome_delta_pass_k, the real quality
        # metric, exactly as the spec requires).
        "outcome_score_after_median": outcome_score_after_median,
        "factory_cpm_after_median": factory_cpm_after_median,
        "gate_verdict": gate_verdict,
        # Comment 2 ladder: replay -> shadow -> advisory -> blocking. Every report this ticket's
        # compare_variants.py can produce is replay-tier evidence by construction (bench/'s
        # stub-mode replay is the only data source wired in); advancing a variant past "replay"
        # requires shadow/advisory run data this ticket does not collect, so the stage is fixed,
        # not computed.
        "promotion_stage": "replay",
        "rollback_tier": determine_rollback_tier(
            next(v for v in variants if v["variant_id"] == after_id)
        ),
    }


def render_markdown(report: dict) -> str:
    econ = report["economics_delta"]
    econ_str = "; ".join(
        f"{k}: {v:+.4g}" if v is not None else f"{k}: n/a" for k, v in econ.items()
    )
    outcome = (
        f"{report['outcome_delta_pass_k']:+.4f}"
        if report["outcome_delta_pass_k"] is not None else "n/a"
    )
    score = report.get("outcome_score_after_median")
    cpm = report.get("factory_cpm_after_median")
    stub_note = (
        "\n\n> Economics are agent-phase only — `BENCH_MODE=stub` skips `preview-up`/"
        "`push-and-pr`, so absolute `cost_per_task`/`tokens_per_task` are a lower bound "
        "relative to a production run. The delta between the two arms above (both run under "
        "the identical stub configuration) remains valid; do not compare these absolute "
        "figures to a production `dark-factory-cost-report` total without a documented offset.\n"
        f">\n> **outcome.score (after, median):** {score if score is not None else 'n/a'} — "
        f"**factory_cpm (after, median):** {cpm if cpm is not None else 'n/a'} — both stub-mode, "
        "not gate-bearing; the gate verdict above is computed from `pass^k` alone.\n"
    )
    return (
        "| variant_id | outcome delta (pass^k) | economics delta | gate verdict | "
        "promotion stage | rollback_tier |\n"
        "|---|---|---|---|---|---|\n"
        f"| {report['variant_id']} | {outcome} | {econ_str} | {report['gate_verdict']} | "
        f"{report['promotion_stage']} | {report['rollback_tier']} |\n"
        f"{stub_note}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--outcome-non-inferiority-bound", type=float, default=None)
    parser.add_argument("--improvement-threshold", type=float, default=None)
    args = parser.parse_args()

    variants = load_variants(args.variants)
    joined = join_variant_results(variants, args.results_dir)
    report = build_report(
        variants, joined,
        outcome_bound=args.outcome_non_inferiority_bound,
        improvement_threshold=args.improvement_threshold,
    )

    if args.out.suffix == ".json":
        args.out.write_text(json.dumps(report, indent=2))
    else:
        args.out.write_text(render_markdown(report))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()

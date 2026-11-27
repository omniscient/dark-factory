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
    if not isinstance(manifest, dict):
        raise CompareError(f"{results_dir}: manifest.json is not a JSON object")
    if "arms" not in manifest or "rubric_source_commit" not in manifest:
        raise CompareError(f"{results_dir}: manifest.json missing arms or rubric_source_commit")
    if not isinstance(manifest["arms"], list) or any(
            not isinstance(a, dict) or (a.get("arm_kind") != "rules_only"
                                        and "base_url_scope" not in a)
            for a in manifest["arms"]):
        raise CompareError(f"{results_dir}: manifest.json arms missing base_url_scope")
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
    arms_by_id = {a["arm_id"]: a for a in manifest.get("arms", [])}
    for r in rows:
        arm = arms_by_id.get(r["arm_id"])
        if arm is None or arm.get("arm_kind") != r["arm_kind"]:
            raise CompareError(f"row arm_id {r['arm_id']!r} does not match a manifest arm with "
                               "the same arm_kind")
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
        cache_ratio = _sum(r["gen_ai.usage.cache_read_input_tokens"]
                           for r, i in zip(rows, inputs) if i is not None) / in_total
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
    rules_id = next((a["arm_id"] for a in arms if a["arm_kind"] == "rules_only"), None)
    if rules_id is None:
        raise CompareError("manifest.json has no rules_only arm")
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

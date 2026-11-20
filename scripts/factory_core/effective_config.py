"""Materialize the effective refinement config for a run.

Merge order (lowest→highest): baked config/config.yaml ← clone
.claude/skills/refinement/config.yaml (transition) ← target-tunable blocks from
.factory/adapter.yaml (today: token_optimization).

#180: also the single writer of the run-config artifacts — run-config.json (the whole
tree) and run-config.env (the ENV_WHITELIST projection bash reads through
scripts/run_config_env.sh) — for two call sites:
  scheduler mode (scheduler.sh, no --clone-dir): the first existing SCHEDULER_CANDIDATES
      file, whole-file, no clone and no adapter; fail-closed.
  run mode (entrypoint.sh, --clone-dir): a committed clone copy alone, else the
      baked ← adapter merge materialize() also writes into the clone; fail-open.
"""
import argparse, json, os, sys
from . import adapter
from .adapter import _deep_merge

# Adapter top-level keys that override config.yaml blocks (df#14).
TARGET_TUNABLE_BLOCKS = ("token_optimization",)

_BAKED_PATH = "/opt/dark-factory/config/config.yaml"
_CLONE_REL = ".claude/skills/refinement/config.yaml"

RUN_CONFIG_JSON = "run-config.json"
RUN_CONFIG_ENV = "run-config.env"
# entrypoint.sh exports the path of its run-config.json under this name: archon gives every
# bash node its own $ARTIFACTS_DIR (the workflow run's directory, not the entrypoint's).
RUN_CONFIG_JSON_ENV = "FACTORY_RUN_CONFIG_JSON"

# Scheduler mode: first existing file wins, whole-file (scheduler.sh's former
# resolve_config_yaml): bind-mounted target checkout → baked image → legacy baked path.
SCHEDULER_CANDIDATES = (
    "/workspace/project/config/config.yaml",
    _BAKED_PATH,
    "/opt/refinement-skills/config.yaml",
)

# The single owner of the bash projection (run-config.env): (ENV_VAR, dotted.path, readers).
# "scheduler" rows are scheduler.sh read_config's knobs, "entrypoint" rows entrypoint.sh's;
# order is apply (and log) order. New bash-consumed knob = one row here.
_S, _E = "scheduler", "entrypoint"
ENV_WHITELIST = (
    ("POLL_INTERVAL", "scheduler.poll_interval", (_S,)),
    ("MAX_RETRIES", "scheduler.max_retries", (_S,)),
    ("RATE_LIMIT_FLOOR", "scheduler.rate_limit_floor", (_S,)),
    ("FACTORY_WIP_LIMIT", "scheduler.factory_wip_limit", (_S, _E)),
    ("MAIN_RED_RECHECK_ENABLED", "scheduler.main_red_recheck_enabled", (_S,)),
    ("MAIN_RED_RECHECK_MINUTES", "scheduler.main_red_recheck_minutes", (_S,)),
    ("BLOCKED_RESCUE_ENABLED", "scheduler.blocked_rescue_enabled", (_S,)),
    ("REFINE_WIP_LIMIT", "refine.wip_limit", (_S,)),
    ("DIRECT_TO_PR_LABEL", "direct_to_pr.label", (_S,)),
    ("SPEC_GRACE_MINUTES", "direct_to_pr.spec_grace_minutes", (_S,)),
    ("PLAN_GRACE_MINUTES", "direct_to_pr.plan_grace_minutes", (_S,)),
    ("CONFLICT_RESOLUTION_ENABLED", "conflict_resolution.enabled", (_S,)),
    ("DISPATCH_CEILING_ENABLED", "dispatch_ceiling.enabled", (_S,)),
    ("ABOVE_CEILING_LABEL", "dispatch_ceiling.label", (_S,)),
    ("ABOVE_CEILING_KEYWORDS", "dispatch_ceiling.keywords", (_S,)),
    ("EPIC_AUTOPILOT_ENABLED", "epic_autopilot.enabled", (_S,)),
    ("EPIC_AUTOPILOT_MODEL", "epic_autopilot.model", (_S,)),
    ("EPIC_AUTOPILOT_DAILY_CAP", "epic_autopilot.daily_cap", (_S,)),
    ("EPIC_AUTOPILOT_CONFIDENCE_FLOOR", "epic_autopilot.confidence_floor", (_S,)),
    ("EPIC_AUTOPILOT_OPT_OUT_LABEL", "epic_autopilot.opt_out_label", (_S,)),
    ("EPIC_AUTOPILOT_HOLD_TTL_HOURS", "epic_autopilot.hold_ttl_hours", (_S,)),
    ("EPIC_AUTOPILOT_SIZE_CEILING", "epic_autopilot.size_ceiling", (_S,)),
    ("EPIC_AUTOPILOT_START_EPICS", "epic_autopilot.start_epics", (_S,)),
    ("EPIC_AUTOPILOT_SENSITIVE_KEYWORDS", "epic_autopilot.sensitive_keywords", (_S,)),
    ("EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "epic_autopilot.allow_self_improvement", (_S,)),
    ("MAIN_RED_AUTOFIX_ENABLED", "main_red_autofix.enabled", (_S,)),
    ("MAIN_RED_AUTOFIX_MODEL", "main_red_autofix.model", (_S,)),
    ("MAIN_RED_AUTOFIX_MAX_ATTEMPTS", "main_red_autofix.max_attempts", (_S,)),
    ("MAIN_RED_AUTOFIX_THROTTLE_MIN", "main_red_autofix.throttle_minutes", (_S,)),
    ("TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED", "token_optimization.architecture.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_MEMORY_ENABLED", "token_optimization.memory.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_COMMENTS_ENABLED", "token_optimization.comments.enabled", (_S,)),
    ("TOKEN_OPTIMIZATION_DIFF_ENABLED", "token_optimization.diff.enabled", (_S,)),
    ("CONFLICT_RESOLUTION_AI_TIER", "conflict_resolution.ai_tier", (_E,)),
    ("SESSION_WINDOW_BACKOFF_ENABLED", "scheduler.session_window_backoff_enabled", (_E,)),
    ("SESSION_WINDOW_BUFFER_MINUTES", "scheduler.session_window_buffer_minutes", (_E,)),
    ("SESSION_WINDOW_FALLBACK_MINUTES", "scheduler.session_window_fallback_minutes", (_E,)),
)


def _load_yaml(path: str) -> dict:
    """Parse YAML mapping at path; {} + stderr warning on any failure (never raise)."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            print(f"effective-config: {path} is not a mapping — ignoring", file=sys.stderr)
            return {}
        return data
    except Exception as exc:
        print(f"effective-config: cannot read {path}: {exc}", file=sys.stderr)
        return {}


def _adapter_blocks(clone_dir: str) -> dict:
    """Return {block: merged_value} for TARGET_TUNABLE_BLOCKS the adapter file itself sets.

    Fail-open: an invalid adapter.yaml skips adapter overrides (consistent with
    the adapter contract) instead of killing config resolution.
    """
    path = os.path.join(clone_dir, ".factory", "adapter.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        merged = adapter.load(clone_dir)
        # Presence check against the RAW file: adapter_defaults may or may not
        # carry these keys, so load() output alone cannot prove the file set them.
        import yaml
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as exc:  # AdapterError + raw-read failures share the fail-open path
        print(f"effective-config: adapter invalid — skipping adapter overrides: {exc}",
              file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {b: merged[b] for b in TARGET_TUNABLE_BLOCKS if b in raw and b in merged}


def resolve(clone_dir: str, baked_path: str = _BAKED_PATH) -> tuple[dict, dict]:
    """Return (merged_config, sources); sources maps each tunable block to the layer that last set it."""
    if not os.path.isfile(baked_path):
        print(f"effective-config: baked config missing at {baked_path}", file=sys.stderr)
        merged = {}
    else:
        merged = _load_yaml(baked_path)
    sources = {b: "baked" for b in TARGET_TUNABLE_BLOCKS}
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        clone_cfg = _load_yaml(clone_cfg_path)
        merged = _deep_merge(merged, clone_cfg)
        for b in TARGET_TUNABLE_BLOCKS:
            if b in clone_cfg:
                sources[b] = "clone"
    for b, val in _adapter_blocks(clone_dir).items():
        merged = _deep_merge(merged, {b: val})
        sources[b] = "adapter"
    return merged, sources


def _git_exclude(clone_dir: str, rel: str) -> None:
    """Append rel to <clone>/.git/info/exclude (idempotent; creates the file if missing)."""
    excl = os.path.join(clone_dir, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(excl), exist_ok=True)
    content = ""
    if os.path.isfile(excl):
        with open(excl, encoding="utf-8") as f:
            content = f.read()
    if rel in content.splitlines():
        return
    with open(excl, "a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(rel + "\n")


def _get(cfg, dotted: str):
    """Value at dotted path, or None when any segment is missing or not a mapping."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _env_value(var: str, value):
    """yq-style rendering of one whitelisted scalar; None = null (written as a bare name)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)) and "\n" not in str(value):
        return str(value)
    print(f"effective-config: {var} is not a single-line scalar — emitted as null",
          file=sys.stderr)
    return None


def render_env(cfg: dict, reader: str, source: str) -> str:
    """run-config.env text: `VAR=value` (raw, unquoted) or bare `VAR` (null), whitelist order."""
    lines = ["# run-config.env (factory_core.effective_config, #180). Parsed line by line by",
             "# scripts/run_config_env.sh -- never source it: values are raw and unquoted.",
             f"# source: {source}"]
    for var, dotted, readers in ENV_WHITELIST:
        if reader in readers:
            val = _env_value(var, _get(cfg, dotted))
            lines.append(var if val is None else f"{var}={val}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_run_artifacts(cfg: dict, out_dir: str, reader: str, source: str) -> None:
    """Write run-config.json (whole tree), then run-config.env (reader's projection). Raises."""
    os.makedirs(out_dir, exist_ok=True)
    _atomic_write(os.path.join(out_dir, RUN_CONFIG_JSON),
                  json.dumps(cfg, indent=2, default=str) + "\n")
    _atomic_write(os.path.join(out_dir, RUN_CONFIG_ENV), render_env(cfg, reader, source))


def resolve_scheduler(candidates=SCHEDULER_CANDIDATES):
    """Scheduler mode: (config, path) of the first existing candidate, or (None, None).

    Whole-file, no merge, no adapter overlay, never a clone: the bind-mounted target
    checkout's file replaces the baked one entirely, keys it omits resolve empty.
    """
    for path in candidates:
        if os.path.isfile(path):
            return _load_yaml(path), path
    return None, None


def run_config(clone_dir, baked_path: str = _BAKED_PATH):
    """Run mode content of run-config.json, as (config, source).

    A committed clone copy is used alone (today's "clone file wins": every run-side reader
    reads only that file); otherwise resolve()'s baked ← adapter merge, the tree
    materialize() writes into the clone. clone_dir=None (standalone caller) = baked only.
    """
    if clone_dir is None:
        return (_load_yaml(baked_path) if os.path.isfile(baked_path) else {}), baked_path
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        return _load_yaml(clone_cfg_path), clone_cfg_path
    merged, sources = resolve(clone_dir, baked_path)
    return merged, _merged_source(sources)


def _merged_source(sources: dict) -> str:
    return "baked <- adapter (" + ", ".join(f"{b} source: {s}" for b, s in sources.items()) + ")"


def load_run_config(clone_dir=None, baked_path: str = _BAKED_PATH) -> dict:
    """Config for run-side Python readers: entrypoint.sh's run-config.json
    ($FACTORY_RUN_CONFIG_JSON), else run_config()'s resolver (standalone/pre-materialization)."""
    path = os.environ.get(RUN_CONFIG_JSON_ENV)
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            print(f"effective-config: {path} is not a mapping — resolving instead",
                  file=sys.stderr)
        except Exception as exc:
            print(f"effective-config: cannot read {path}: {exc} — resolving instead",
                  file=sys.stderr)
    return run_config(clone_dir, baked_path)[0]


def first_match_list(candidates, dotted: str) -> list:
    """Items of the first candidate file whose `dotted` key is a non-empty list; [] if none.

    No merge and no clone/adapter layer: replaces epic_autopilot's former per-file
    `yq '.<dotted>[]'` loop with the same candidate order (#180 R5).
    """
    import yaml
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        val = _get(data, dotted)
        if isinstance(val, list):
            items = [str(v).strip().strip('"') for v in val if v is not None and str(v).strip()]
            if items:
                return items
    return []


def materialize(clone_dir: str, baked_path: str = _BAKED_PATH, out_dir: str | None = None) -> str:
    """Entrypoint-facing operation; returns a one-line human summary.

    Transition period (clone config.yaml exists): write NOTHING into the clone — the
    committed clone file wins byte-identically; warn on adapter drift. Post-cleanup (no
    clone config): write the merged config into the clone and git-exclude it so the
    materialized artifact can never be committed back to the target repo.
    With out_dir (#180), also write run-config.{json,env} there from the same content.
    """
    merged, sources = resolve(clone_dir, baked_path)
    clone_cfg_path = os.path.join(clone_dir, _CLONE_REL)
    if os.path.isfile(clone_cfg_path):
        clone_cfg = _load_yaml(clone_cfg_path)
        clone_layer = _deep_merge(_load_yaml(baked_path) if os.path.isfile(baked_path) else {},
                                  clone_cfg)
        for b in TARGET_TUNABLE_BLOCKS:
            if sources[b] == "adapter" and merged.get(b) != clone_layer.get(b):
                print(f"effective-config: WARNING — adapter/{b} drifts from clone config.yaml"
                      " — clone file wins until P3 cleanup removes it;"
                      " re-sync .factory/adapter.yaml", file=sys.stderr)
        srcs = ", ".join(f"{b} source: {'clone' if b in clone_cfg else 'baked'}"
                         for b in TARGET_TUNABLE_BLOCKS)
        _write_run_artifacts_fail_open(clone_cfg, out_dir, clone_cfg_path)
        return f"effective-config: clone config present — left in place ({srcs})"
    import yaml
    # Exclude BEFORE writing: if the exclude append fails, better to have no
    # materialized file than an unexcluded one a factory agent could commit.
    _git_exclude(clone_dir, _CLONE_REL)
    os.makedirs(os.path.dirname(clone_cfg_path), exist_ok=True)
    with open(clone_cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    _write_run_artifacts_fail_open(merged, out_dir, _merged_source(sources))
    srcs = ", ".join(f"{b} source: {sources[b]}" for b in TARGET_TUNABLE_BLOCKS)
    return f"effective-config: materialized from baked defaults ({srcs})"


def _write_run_artifacts_fail_open(cfg: dict, out_dir: str | None, source: str) -> None:
    """Run mode: a run-config write failure warns and never blocks the clone-side file."""
    if not out_dir:
        return
    try:
        write_run_artifacts(cfg, out_dir, "entrypoint", source)
    except Exception as exc:
        print(f"effective-config: cannot write run-config artifacts to {out_dir}: {exc}",
              file=sys.stderr)


def _scheduler_main(args) -> int:
    """Scheduler mode (no --clone-dir). Fail-closed: non-zero and nothing written on error."""
    candidates = tuple(args.candidate) if args.candidate else SCHEDULER_CANDIDATES
    cfg, path = resolve_scheduler(candidates)
    if path is None:
        print("effective-config: ERROR — config.yaml not found — searched: "
              + " ".join(candidates), file=sys.stderr)
        return 1
    if args.print_merged:
        import yaml
        print(yaml.safe_dump(cfg, sort_keys=False))
        return 0
    if not args.out_dir:
        print("effective-config: scheduler mode --materialize needs --out-dir", file=sys.stderr)
        return 2
    try:
        write_run_artifacts(cfg, args.out_dir, "scheduler", path)
    except Exception as exc:
        print(f"effective-config: cannot write run-config artifacts: {exc}", file=sys.stderr)
        return 1
    print(f"effective-config: scheduler mode — resolved {path}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--clone-dir", default=None,
                   help="run mode (entrypoint.sh); omitted = scheduler mode (scheduler.sh)")
    p.add_argument("--baked", default=_BAKED_PATH)
    p.add_argument("--out-dir", default=None,
                   help="also write run-config.json + run-config.env here"
                        " (required for scheduler-mode --materialize)")
    p.add_argument("--candidate", action="append", default=None,
                   help="scheduler mode: replace SCHEDULER_CANDIDATES (tests only)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--materialize", action="store_true",
                   help="Materialize the effective config; print summary")
    g.add_argument("--print", dest="print_merged", action="store_true",
                   help="Dump merged YAML to stdout (debugging/tests)")
    args = p.parse_args()
    if args.clone_dir is None:
        sys.exit(_scheduler_main(args))
    # Run mode. Fail-open: a config-resolution bug must never kill a run — always exit 0.
    try:
        if args.materialize:
            print(materialize(args.clone_dir, args.baked, args.out_dir))
        else:
            import yaml
            merged, _ = resolve(args.clone_dir, args.baked)
            print(yaml.safe_dump(merged, sort_keys=False))
    except Exception as exc:
        print(f"effective-config: error (fail-open, exit 0): {exc}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()

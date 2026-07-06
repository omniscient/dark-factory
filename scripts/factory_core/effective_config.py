"""Materialize the effective refinement config for a run.

Merge order (lowest→highest): baked config/config.yaml ← clone
.claude/skills/refinement/config.yaml (transition) ← target-tunable blocks from
.factory/adapter.yaml (today: token_optimization).
"""
import argparse, os, sys
from . import adapter
from .adapter import _deep_merge

# Adapter top-level keys that override config.yaml blocks (df#14).
TARGET_TUNABLE_BLOCKS = ("token_optimization",)

_BAKED_PATH = "/opt/dark-factory/config/config.yaml"
_CLONE_REL = ".claude/skills/refinement/config.yaml"


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


def materialize(clone_dir: str, baked_path: str = _BAKED_PATH) -> str:
    """Entrypoint-facing operation; returns a one-line human summary.

    Transition period (clone config.yaml exists): write NOTHING — the committed
    clone file wins byte-identically; warn on adapter drift. Post-cleanup (no
    clone config): write the merged config into the clone and git-exclude it so
    the materialized artifact can never be committed back to the target repo.
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
        return f"effective-config: clone config present — left in place ({srcs})"
    import yaml
    os.makedirs(os.path.dirname(clone_cfg_path), exist_ok=True)
    with open(clone_cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    _git_exclude(clone_dir, _CLONE_REL)
    srcs = ", ".join(f"{b} source: {sources[b]}" for b in TARGET_TUNABLE_BLOCKS)
    return f"effective-config: materialized from baked defaults ({srcs})"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
    p.add_argument("--baked", default=_BAKED_PATH)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--materialize", action="store_true",
                   help="Materialize the effective config into the clone; print summary")
    g.add_argument("--print", dest="print_merged", action="store_true",
                   help="Dump merged YAML to stdout (debugging/tests)")
    args = p.parse_args()
    # Fail-open: a config-resolution bug must never kill a run — always exit 0.
    try:
        if args.materialize:
            print(materialize(args.clone_dir, args.baked))
        else:
            import yaml
            merged, _ = resolve(args.clone_dir, args.baked)
            print(yaml.safe_dump(merged, sort_keys=False))
    except Exception as exc:
        print(f"effective-config: error (fail-open, exit 0): {exc}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()

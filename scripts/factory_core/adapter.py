"""Load + validate <clone>/.factory/adapter.yaml, deep-merged over adapter_defaults.DEFAULTS."""
import argparse, copy, os, sys
from . import adapter_defaults

class AdapterError(Exception):
    pass

_KNOWN_TOP = {"schema_version", "components", "safety", "memory_routing", "deconflict",
              "token_optimization", "loops"}
_MAP_KEYS = {"components", "safety", "memory_routing", "deconflict", "token_optimization"}

_LOOP_REQUIRED_FIELDS = {
    "name", "purpose", "trigger", "inputs", "outputs", "artifacts",
    "verifier", "stop_condition", "failure_behavior", "side_effect_level", "handoff",
}
_LOOP_STRING_FIELDS = {
    "name", "purpose", "trigger", "verifier", "stop_condition",
    "failure_behavior", "handoff",
}
_LOOP_LIST_FIELDS = {"inputs", "outputs", "artifacts"}

# Per-loop-entry field names reserved for a tracked-but-unshipped extension.
# Rejected with a targeted message so the extension point is discoverable
# without A1 accepting unvalidated content. Consulted before the generic
# unknown-field error in _validate_loop.
_RESERVED_LOOP_FIELDS = {"memory_intervention": "#241"}

# Top-level key names reserved for a tracked future design ticket. Unlike a
# generic unknown top-level key (which warns and carries — v1 parity), a named
# reserved key is hard-rejected: it has no v1 history, so strictness here is
# parity-safe, and warn-and-carry would deep-merge unvalidated content into config.
_RESERVED_TOP_FIELDS = {
    "mechanism_candidates": "a future Bilevel Autoresearch design ticket",
}


def _validate_loop(entry, index: int) -> None:
    if not isinstance(entry, dict):
        raise AdapterError(f"loops[{index}] must be a mapping, got {type(entry).__name__}")
    name = entry.get("name", "?")
    for key in entry:
        if key not in _LOOP_REQUIRED_FIELDS:
            if key in _RESERVED_LOOP_FIELDS:
                raise AdapterError(
                    f"loops[{index}] ('{name}'): field '{key}' is reserved for epic "
                    f"{_RESERVED_LOOP_FIELDS[key]} (per-loop memory intervention) and is "
                    f"not accepted in schema v2; remove it"
                )
            raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")
    for field in _LOOP_REQUIRED_FIELDS:
        if field not in entry:
            raise AdapterError(f"loops[{index}] ('{name}'): missing required field '{field}'")
    for field in _LOOP_STRING_FIELDS:
        val = entry[field]
        if not isinstance(val, str) or not val:
            raise AdapterError(
                f"loops[{index}] ('{name}'): field '{field}' must be a non-empty string")
    for field in _LOOP_LIST_FIELDS:
        val = entry[field]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise AdapterError(
                f"loops[{index}] ('{name}'): field '{field}' must be a list of strings")
    sel = entry["side_effect_level"]
    if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6):
        raise AdapterError(
            f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int between 1 and 6")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out

def load(clone_dir: str) -> dict:
    path = os.path.join(clone_dir, ".factory", "adapter.yaml")
    if not os.path.isfile(path):
        return copy.deepcopy(adapter_defaults.DEFAULTS)
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise AdapterError(f"adapter.yaml unreadable/unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise AdapterError("adapter.yaml top level must be a mapping")
    if not isinstance(data.get("schema_version", 1), int):
        raise AdapterError("schema_version must be an integer")
    for k, v in data.items():
        if k in _RESERVED_TOP_FIELDS:
            raise AdapterError(
                f"adapter key '{k}' is reserved for {_RESERVED_TOP_FIELDS[k]} and is "
                f"not accepted in schema v2; remove it"
            )
        if k not in _KNOWN_TOP:
            print(f"adapter: warning — unknown adapter key '{k}' (carried through)", file=sys.stderr)
        if k in _MAP_KEYS and not isinstance(v, dict):
            raise AdapterError(f"adapter key '{k}' must be a mapping, got {type(v).__name__}")
    # Intentional: loops: is validated whenever present, independent of
    # schema_version. Per spec Requirement 4 (see Alternative 4 in
    # docs/superpowers/specs/2026-07-07-adapter-schema-v2-loops-design.md),
    # schema_version is inert metadata and gating loops: on it was explicitly
    # rejected — it would break "no restriction to {1,2}" parity. A
    # schema_version: 1 file containing loops: is validated the same as v2.
    if "loops" in data:
        if not isinstance(data["loops"], list):
            raise AdapterError(f"adapter key 'loops' must be a list, got {type(data['loops']).__name__}")
        seen_names = set()
        for i, entry in enumerate(data["loops"]):
            _validate_loop(entry, i)
            name = entry.get("name")
            if name in seen_names:
                raise AdapterError(f"loops[{i}] ('{name}'): duplicate loop name '{name}'")
            seen_names.add(name)
    return _deep_merge(adapter_defaults.DEFAULTS, data)

def get(clone_dir: str, dotted: str):
    node = load(clone_dir)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
    p.add_argument("--get")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--format", choices=["plain", "keyvalue"], default="plain",
                   help="Output format: 'plain' (default) or 'keyvalue' (tab-separated key\\tvalue lines for dicts)")
    args = p.parse_args()
    try:
        if args.get:
            val = get(args.clone_dir, args.get)
            if val is None:
                print("")
            elif args.format == "keyvalue" and isinstance(val, dict):
                for k, v in val.items():
                    print(f"{k}\t{v}")
            else:
                print(val)
        elif args.validate:
            load(args.clone_dir)
            print("adapter OK")
    except AdapterError as exc:
        print(f"adapter INVALID: {exc}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

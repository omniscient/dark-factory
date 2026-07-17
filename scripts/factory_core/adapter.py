"""Load + validate <clone>/.factory/adapter.yaml, deep-merged over adapter_defaults.DEFAULTS."""
import argparse, copy, os, sys
from . import adapter_defaults

class AdapterError(Exception):
    pass

_KNOWN_TOP = {"schema_version", "components", "safety", "memory_routing", "deconflict",
              "token_optimization"}
_MAP_KEYS = {"components", "safety", "memory_routing", "deconflict", "token_optimization"}

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
        if k not in _KNOWN_TOP:
            print(f"adapter: warning — unknown adapter key '{k}' (carried through)", file=sys.stderr)
        if k in _MAP_KEYS and not isinstance(v, dict):
            raise AdapterError(f"adapter key '{k}' must be a mapping, got {type(v).__name__}")
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

"""Load + validate <clone>/.factory/adapter.yaml, deep-merged over adapter_defaults.DEFAULTS."""
import argparse, copy, os, sys
from . import adapter_defaults

class AdapterError(Exception):
    pass

_KNOWN_TOP = {"schema_version", "components", "safety", "memory_routing", "deconflict",
              "token_optimization", "loops"}
_MAP_KEYS = {"components", "safety", "memory_routing", "deconflict", "token_optimization"}

_LOOP_ENTRY_TOP_FIELDS = ("name", "purpose", "side_effect_level")
_LOOP_MOVE_BLOCKS = ("discovery", "handoff", "verification", "persistence", "scheduling")
_LOOP_OPTIONAL_ENTRY_FIELDS = {"role_card", "economics", "skills", "budget_caps", "human_checkpoint"}
_LOOP_KNOWN_ENTRY_FIELDS = (
    set(_LOOP_ENTRY_TOP_FIELDS) | set(_LOOP_MOVE_BLOCKS) | _LOOP_OPTIONAL_ENTRY_FIELDS
)

# A1's pre-A1.5 flat loop field names, now relocated into the five move-block
# sub-blocks. Used only to enrich the "missing required block" message with a
# migration hint — not for validation.
_A1_FLAT_LOOP_FIELDS = {
    "trigger", "inputs", "outputs", "artifacts", "verifier", "stop_condition",
    "failure_behavior",
}

# Per-loop-entry field names reserved for a tracked-but-unshipped extension.
# Rejected with a targeted message so the extension point is discoverable
# without A1 accepting unvalidated content. Consulted before the generic
# unknown-field error in _validate_loop.
_RESERVED_LOOP_FIELDS = {
    "memory_intervention": "epic #241 (per-loop memory intervention)",
    "contract": "a follow-up child of epic #194 (completion-contract extension recommended by #311)",
}

# role_card fields that are tool allow/deny declarations — permanently excluded
# (CLAUDE.md § Trusted comment channels; comment-channel input may never
# authorize tool allow/deny surfaces). Not a deferral: there is no ticket to
# point to, unlike _RESERVED_LOOP_FIELDS. Maps field -> the reason clause used
# in _validate_subblock's reserved-field message (mirrors _RESERVED_LOOP_FIELDS'
# field -> reason shape so the message text isn't hardcoded per-caller).
_ROLE_CARD_RESERVED_FIELDS = {
    "allowed_tools": "is a tool allow/deny declaration and is permanently excluded "
                      "from adapter.yaml (CLAUDE.md § Trusted comment channels)",
    "forbidden_tools": "is a tool allow/deny declaration and is permanently excluded "
                        "from adapter.yaml (CLAUDE.md § Trusted comment channels)",
}

# Top-level key names reserved for a tracked future design ticket. Unlike a
# generic unknown top-level key (which warns and carries — v1 parity), a named
# reserved key is hard-rejected: it has no v1 history, so strictness here is
# parity-safe, and warn-and-carry would deep-merge unvalidated content into config.
_RESERVED_TOP_FIELDS = {
    "mechanism_candidates": "a future Bilevel Autoresearch design ticket",
}


def _validate_subblock(entry, index, name, block, *, str_fields=(), list_fields=(),
                        int_fields=(), bool_fields=(), required_fields=(),
                        reserved_fields=None) -> None:
    if block not in entry:
        return
    val = entry[block]
    if not isinstance(val, dict):
        raise AdapterError(f"loops[{index}] ('{name}'): block '{block}' must be a mapping")
    known = set(str_fields) | set(list_fields) | set(int_fields) | set(bool_fields)
    for key in val:
        if reserved_fields and key in reserved_fields:
            raise AdapterError(
                f"loops[{index}] ('{name}'): {block} field '{key}' "
                f"{reserved_fields[key]}; remove it"
            )
        if key not in known:
            raise AdapterError(f"loops[{index}] ('{name}'): block '{block}': unknown field '{key}'")
    for field in required_fields:
        if field not in val:
            raise AdapterError(
                f"loops[{index}] ('{name}'): block '{block}': missing required field '{field}'")
    for field in str_fields:
        if field in val:
            v = val[field]
            if not isinstance(v, str) or not v:
                raise AdapterError(
                    f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a non-empty string")
    for field in list_fields:
        if field in val:
            v = val[field]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise AdapterError(
                    f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a list of strings")
    for field in int_fields:
        if field in val:
            v = val[field]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise AdapterError(
                    f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be an int >= 1")
    for field in bool_fields:
        if field in val:
            v = val[field]
            if not isinstance(v, bool):
                raise AdapterError(
                    f"loops[{index}] ('{name}'): block '{block}': field '{field}' must be a bool")


def _validate_loop(entry, index: int) -> None:
    if not isinstance(entry, dict):
        raise AdapterError(f"loops[{index}] must be a mapping, got {type(entry).__name__}")
    name = entry.get("name", "?")

    # Reserved-field check runs before the move-block presence loop below (and
    # before the generic unknown-field scan) so a reserved-but-unshipped field
    # (e.g. memory_intervention, contract) on an otherwise-incomplete entry
    # gets the targeted reserved-field message, not a generic missing-block
    # error — matching this section's own stated intent (see comment above
    # _RESERVED_LOOP_FIELDS).
    for key in entry:
        if key in _RESERVED_LOOP_FIELDS:
            raise AdapterError(
                f"loops[{index}] ('{name}'): field '{key}' is reserved for "
                f"{_RESERVED_LOOP_FIELDS[key]} and is not accepted in this schema; remove it"
            )

    for block in _LOOP_MOVE_BLOCKS:
        if block not in entry:
            msg = f"loops[{index}] ('{name}'): missing required block '{block}'"
            if _A1_FLAT_LOOP_FIELDS & set(entry):
                msg += (" (schema v2 moved A1's flat loop fields into "
                         "discovery/handoff/verification/persistence/scheduling)")
            raise AdapterError(msg)

    for key in entry:
        if key not in _LOOP_KNOWN_ENTRY_FIELDS:
            raise AdapterError(f"loops[{index}] ('{name}'): unknown field '{key}'")

    for field in _LOOP_ENTRY_TOP_FIELDS:
        if field not in entry:
            raise AdapterError(f"loops[{index}] ('{name}'): missing required field '{field}'")
    for field in ("name", "purpose"):
        val = entry[field]
        if not isinstance(val, str) or not val:
            raise AdapterError(
                f"loops[{index}] ('{name}'): field '{field}' must be a non-empty string")
    # side_effect_level scale (owned by #193, enforced by #196 — reproduced here
    # only as a range check, not redefined): 1=read-only research, 2=artifact
    # writing, 3=ticket creation, 4=code modification, 5=PR creation,
    # 6=external production side effect (A2 rejects 6; A1.5 does not).
    sel = entry["side_effect_level"]
    if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6):
        raise AdapterError(
            f"loops[{index}] ('{name}'): field 'side_effect_level' must be an int between 1 and 6")

    _validate_subblock(entry, index, name, "discovery",
                        str_fields=("trigger",), list_fields=("inputs",),
                        required_fields=("trigger", "inputs"))
    _validate_subblock(entry, index, name, "handoff",
                        str_fields=("manifest",), list_fields=("outputs",),
                        required_fields=("manifest", "outputs"))
    _validate_subblock(entry, index, name, "verification",
                        str_fields=("verifier", "stop_condition"),
                        required_fields=("verifier", "stop_condition"))
    _validate_subblock(entry, index, name, "persistence",
                        list_fields=("artifacts",), required_fields=("artifacts",))
    _validate_subblock(entry, index, name, "scheduling",
                        str_fields=("failure_behavior",),
                        required_fields=("failure_behavior",))
    _validate_subblock(entry, index, name, "role_card",
                        str_fields=("name", "output_schema", "fallback_path"),
                        list_fields=("responsibilities", "non_responsibilities", "observability"),
                        required_fields=("name",),
                        reserved_fields=_ROLE_CARD_RESERVED_FIELDS)
    _validate_subblock(entry, index, name, "economics",
                        str_fields=("feature_demand", "model_capability_floor"),
                        bool_fields=("context_offload_required",))
    _validate_subblock(entry, index, name, "skills",
                        list_fields=("primary", "supplemental", "forbidden", "eval_cases"))

    if "human_checkpoint" in entry:
        v = entry["human_checkpoint"]
        if not isinstance(v, str) or not v:
            raise AdapterError(
                f"loops[{index}] ('{name}'): field 'human_checkpoint' must be a non-empty string")

    _validate_subblock(entry, index, name, "budget_caps",
                        int_fields=("max_tokens", "max_retry_spend"),
                        required_fields=("max_tokens",))

    if sel >= 4:
        if "budget_caps" not in entry:
            raise AdapterError(
                f"loops[{index}] ('{name}'): side_effect_level {sel} >= 4 requires 'budget_caps'")
        if "human_checkpoint" not in entry:
            raise AdapterError(
                f"loops[{index}] ('{name}'): side_effect_level {sel} >= 4 requires 'human_checkpoint'")


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
    # docs/archive/2026-07-07-adapter-schema-v2-loops-design.md),
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

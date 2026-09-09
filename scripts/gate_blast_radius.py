"""Deterministic blast-radius gate for the dark-factory validate pipeline.

Reads a newline-separated list of changed file paths from stdin.
Writes a blast.md-format verdict to stdout.

Exit 0 always — the caller reads STATUS from the output.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Make factory_core resolvable regardless of caller CWD/invocation style
# (mirrors factory_core/cli.py), since this module is both run standalone and
# imported by diff_rank.py.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--changed-files-stdin",
        action="store_true",
        help="Read newline-separated changed-file list from stdin",
    )
    p.add_argument(
        "--lines-changed",
        type=int,
        default=0,
        help="Total added+deleted lines (computed by caller from git diff --shortstat)",
    )
    p.add_argument(
        "--hotspots",
        required=True,
        help="Path to docs/codeindex-hotspots.md",
    )
    p.add_argument(
        "--config",
        required=True,
        help="Path to .claude/skills/refinement/config.yaml",
    )
    p.add_argument(
        "--clone-dir",
        default=os.environ.get("CLONE_DIR", "."),
        help="Clone root for adapter.yaml lookup (default: $CLONE_DIR or '.')",
    )
    p.add_argument(
        "--base-ref",
        default="main",
        help="Git ref to read blast_radius.* config (Requirement 8) and the pre-PR "
             "adapter.yaml snapshot from; never the working tree",
    )
    return p.parse_args()


_BAKED_CONFIG_PATH = "/opt/dark-factory/config/config.yaml"  # == effective_config._BAKED_PATH; entrypoint.sh:88


def _baked_blast_config() -> dict:
    """blast_radius.* from the image-baked config -- COPY'd at image build from main,
    never the PR under review. FACTORY_CONFIG_PATH mirrors entrypoint.sh:88 (test seam;
    never set in the image). {} when absent/unreadable."""
    try:
        import yaml  # type: ignore
        with open(os.environ.get("FACTORY_CONFIG_PATH", _BAKED_CONFIG_PATH), encoding="utf-8") as f:
            blk = (yaml.safe_load(f) or {}).get("blast_radius", {})
        return blk if isinstance(blk, dict) else {}
    except Exception:
        return {}


def load_config(path: str, clone_dir: str, base_ref: str) -> dict:
    """Layered, never from the working tree (Requirement 8 / F1; operator review P1):
    baked blast_radius block  <-  `git show <base-ref>:<path>` block (key by key).

    On the self target <path> is untracked (materialized from the baked file at container
    start and git-excluded), so the base-ref read misses and the baked block governs; a
    target that commits <path> has its committed values win. Hardcoded defaults apply only
    to keys neither layer sets. Both layers are trusted -- the image and the merged base --
    so the fallback direction stays "gate runs normally", never a PR-controlled value.

    `path` must be relative to clone_dir -- `git show <ref>:<path>` requires a
    repo-relative path. The one caller (commands/dark-factory-validate.md, Task 6) always
    passes the existing relative literal ".claude/skills/refinement/config.yaml".
    """
    cfg = dict(_baked_blast_config())
    try:
        import yaml  # type: ignore
        proc = subprocess.run(
            ["git", "-C", clone_dir, "show", f"{base_ref}:{path}"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            blk = (yaml.safe_load(proc.stdout) or {}).get("blast_radius", {})
            if isinstance(blk, dict):
                cfg.update(blk)
    except Exception:
        pass
    return cfg


def parse_hotspots(path: str, score_floor: float) -> set:
    """Return set of file paths whose blast score >= score_floor.

    Parses the space-separated codeindex-hotspots.md format:
        64.0  frontend/src/api/client.ts  (20d / 88t)  78 loc
    Score is the first token, path is the second.
    """
    hot = set()
    try:
        content = Path(path).read_text(errors="replace")
    except FileNotFoundError:
        return hot
    for line in content.splitlines():
        m = re.match(r"^\s*([\d.]+)\s+(\S+)", line)
        if m:
            try:
                score = float(m.group(1))
            except ValueError:
                continue
            if score >= score_floor:
                hot.add(m.group(2))
    return hot


# adapter_defaults is the sole source of truth. A missing/broken import fails
# loudly here instead of silently falling back to a stale copy.
from factory_core.adapter_defaults import DEFAULTS as _AD
from factory_core.adapter_defaults import (
    FACTORY_OWNED_MIGRATION_SEED_FLOOR as _MIGRATION_SEED_FLOOR,
)

MIGRATION_SEED_AUTH_PATTERNS = [
    re.compile(p) for p in _AD["safety"]["migration_seed_auth_patterns"]
]


def _migration_seed_auth_patterns(clone_dir: str | None = None) -> list:
    """Return compiled migration/seed/auth patterns, reading from adapter at use-time.

    Falls back to MIGRATION_SEED_AUTH_PATTERNS ∪ the boundary floor on any error, so a
    broken/unimportable adapter module can never drop the floor (Requirement 9). The
    bare MIGRATION_SEED_AUTH_PATTERNS module constant is left un-floored -- it stays a
    verbatim re-export of DEFAULTS so tests/test_adapter.py::test_migration_seed_auth_patterns_default_parity's
    sibling identity checks (e.g. test_skill_md_not_in_migration_seed_auth_patterns,
    which reads adapter_defaults.DEFAULTS directly, not this function) keep pinning
    DEFAULTS exactly.
    """
    try:
        from factory_core import adapter
        val = adapter.get(clone_dir or ".", "safety.migration_seed_auth_patterns")
        if val is not None and isinstance(val, list):
            return [re.compile(p) for p in val]
    except Exception:
        pass
    raw = _AD["safety"]["migration_seed_auth_patterns"]
    floored = list(raw) + [p for p in _MIGRATION_SEED_FLOOR if p not in raw]
    return [re.compile(p) for p in floored]


# Sub-classifies a migration_seed_auth_patterns match by matched-pattern source
# text (mirroring diff_rank.py::_safety_signal()'s technique) so a skill/
# settings/hooks/plugin/MCP match is never hidden inside the generic
# "migration-seed" bucket (spec Q3/A3). Tokens deliberately omit the trailing
# ".json"/".local.json" — the source patterns regex-escape those dots
# (e.g. r"settings\.json$"), which breaks a plain unescaped-dot substring
# match; "settings" and "mcp" alone are unambiguous within this pattern set.
#
# Sole source of truth so this and diff_rank.py's identical classification
# logic can't drift out of sync.
from factory_core.adapter_defaults import SKILL_SECURITY_TOKENS as _SKILL_SECURITY_TOKENS


def classify_file(fpath: str, hotspots: set, clone_dir: str | None = None) -> list:
    """Return list of triggered categories for a single file path."""
    cats = []
    if fpath in hotspots:
        cats.append("hotspot")
    for pat in _migration_seed_auth_patterns(clone_dir):
        if pat.search(fpath):
            src = pat.pattern
            if any(tok in src for tok in _SKILL_SECURITY_TOKENS):
                cats.append("skill-security")
            else:
                cats.append("migration-seed")
            break
    return cats


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config, args.clone_dir, args.base_ref)
    enabled = cfg.get("enabled", True)

    score_floor = float(cfg.get("hotspot_score_floor", 5.0))
    size_budget = int(cfg.get("size_budget_lines", 400))
    size_blocks = bool(cfg.get("size_budget_blocks", False))

    hotspots = parse_hotspots(args.hotspots, score_floor)

    changed_files = []
    if args.changed_files_stdin:
        changed_files = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]

    lines_changed = args.lines_changed
    clone_dir = args.clone_dir

    # enabled:false suppresses only the hotspot and size triggers (operator review
    # F1/Requirement 8) -- a migration-seed/floor match (and the boundary-escalation
    # semantic step, wired in Task 5) must never be suppressible by a PR's own change.
    triggered = []
    for fpath in changed_files:
        cats = classify_file(fpath, hotspots, clone_dir=clone_dir)
        if not enabled:
            cats = [c for c in cats if c != "hotspot"]
        if cats:
            triggered.append((fpath, cats))

    hard_trigger = bool(triggered)
    size_trigger = enabled and size_blocks and lines_changed > size_budget

    if hard_trigger or size_trigger:
        status = "HUMAN_REQUIRED"
    elif not enabled:
        status = "SKIPPED"
    else:
        status = "PASS"

    severity = "critical" if status == "HUMAN_REQUIRED" else "none"
    findings_count = len(triggered) + (1 if size_trigger else 0)

    trigger_label = "none"
    if hard_trigger:
        cats_all = [c for _, cats in triggered for c in cats]
        if "hotspot" in cats_all:
            trigger_label = "hotspot"
        elif "skill-security" in cats_all:
            trigger_label = "skill-security"
        else:
            trigger_label = "migration-seed"
    elif size_trigger:
        trigger_label = "size"

    print(f"STATUS: {status}")
    print(f"GATE_TYPE: blast")
    print(f"FINDINGS_COUNT: {findings_count}")
    print(f"SEVERITY: {severity}")
    print("---")
    print(f"TRIGGER: {trigger_label}")
    print("TRIGGERED_FILES:")
    for fpath, cats in triggered:
        label = ", ".join(cats)
        print(f"  - {fpath} (category: {label})")
    if size_trigger:
        print(f"  - [size] {lines_changed} lines > {size_budget} budget")
    print(f"LINES_CHANGED: {lines_changed}")


if __name__ == "__main__":
    main()

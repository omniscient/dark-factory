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


def _adapter_snapshot(clone_dir: str, ref: str) -> tuple:
    """Return (parsed adapter.yaml dict, parse_ok) for `ref` (None = working tree).

    A missing file -- at the working tree, or at a *resolvable* ref -- is a valid "no
    adapter" state (matches adapter.py::load()'s own no-file branch) and returns
    ({}, True). parse_ok is False when: the ref itself does not resolve (distinct from
    the file being absent at a ref that does resolve -- inspected via `git show`'s
    stderr text, since both cases exit non-zero); the file exists but is malformed
    YAML or not a top-level mapping; a loops[] entry fails adapter.py's own
    loop-schema validation; or two loops[] entries share a name (mirrors
    adapter.py::load()'s duplicate-name check, since a duplicate would otherwise
    silently collapse in the {name: loop} comparison maps below). The caller fails
    closed on parse_ok=False (Requirement 5/OD6).
    """
    import yaml
    from factory_core import adapter as _adapter
    from factory_core import verifier as _verifier

    try:
        # `git show <ref>:<path>` for BOTH sides -- never the working tree. A commit that
        # escalates the adapter plus a working-tree copy restored to the base content is
        # still the diff that gets pushed and merged, and reading the tree missed it
        # entirely (operator review of PR #410); Requirement 5 says base branch vs. HEAD.
        # LC_ALL=C pins git's stderr to English so the "file absent at a resolvable ref"
        # discrimination below can't degrade to fail-closed under a translated locale.
        proc = subprocess.run(
            ["git", "-C", clone_dir, "show", f"{ref}:.factory/adapter.yaml"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "LC_ALL": "C"},
        )
        if proc.returncode != 0:
            stderr = proc.stderr
            if "does not exist in" in stderr or "exists on disk, but not in" in stderr:
                return {}, True
            return None, False
        text = proc.stdout
        data = yaml.safe_load(text)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, False
        seen_names = set()
        for i, entry in enumerate(data.get("loops", []) or []):
            _adapter._validate_loop(entry, i)
            _verifier.assert_verifier_independent(entry)  # same check load() applies (operator review P5)
            name = entry.get("name")
            if name in seen_names:
                raise _adapter.AdapterError(f"duplicate loop name '{name}'")
            seen_names.add(name)
        return data, True
    except Exception:
        return None, False


def _boundary_escalation_findings(clone_dir: str, base_ref: str) -> list:
    """Semantic diff of .factory/adapter.yaml, base_ref vs. HEAD (Requirement 5).
    Both sides come from `git show`; the working tree is never consulted, so an
    escalation that is committed but reverted on disk is still caught. Only called by
    main() when that file is in the changed set."""
    from factory_core import side_effect as _side_effect

    old, old_ok = _adapter_snapshot(clone_dir, base_ref)
    new, new_ok = _adapter_snapshot(clone_dir, "HEAD")
    if not old_ok:
        return [f"adapter.yaml unparseable at {base_ref}"]
    if not new_ok:
        return ["adapter.yaml unparseable at HEAD"]

    findings = []
    # `or {}` normalizes "no safety: key at all" (a brand-new adapter.yaml, or the
    # file absent at that ref) and "safety: {}" (an explicit empty block) to the same
    # comparable value -- both mean "no explicit safety overrides", so introducing an
    # adapter.yaml with nothing under safety: must not itself read as a change.
    if (old.get("safety") or {}) != (new.get("safety") or {}):
        findings.append("safety: block changed")

    min_level = _side_effect.FACTORY_OWNED_MIN_LEVEL
    old_loops = {l["name"]: l for l in (old.get("loops") or [])}
    new_loops = {l["name"]: l for l in (new.get("loops") or [])}

    for name, new_loop in new_loops.items():
        old_loop = old_loops.get(name)
        if old_loop is None:
            sel = new_loop.get("side_effect_level")
            if isinstance(sel, int) and sel >= min_level:
                findings.append(
                    f"loops[{name}]: new loop declares side_effect_level {sel} "
                    f">= {min_level} (factory-owned)")
            continue

        old_sel = old_loop.get("side_effect_level")
        new_sel = new_loop.get("side_effect_level")
        if isinstance(old_sel, int) and isinstance(new_sel, int) and new_sel > old_sel:
            findings.append(
                f"loops[{name}]: side_effect_level increased {old_sel} -> {new_sel}")

        old_ver = old_loop.get("verification") or {}
        new_ver = new_loop.get("verification") or {}
        for field in ("verifier", "stop_condition"):
            if old_ver.get(field) != new_ver.get(field):
                findings.append(
                    f"loops[{name}]: verification.{field} changed "
                    f"{old_ver.get(field)!r} -> {new_ver.get(field)!r}")

        is_factory_owned = (
            (isinstance(old_sel, int) and old_sel >= min_level)
            or (isinstance(new_sel, int) and new_sel >= min_level)
        )
        if is_factory_owned and old_loop != new_loop:
            findings.append(
                f"loops[{name}]: factory-owned loop (side_effect_level >= "
                f"{min_level}) entry changed")

    for name, old_loop in old_loops.items():
        if name in new_loops:
            continue
        old_sel = old_loop.get("side_effect_level")
        if isinstance(old_sel, int) and old_sel >= min_level:
            findings.append(
                f"loops[{name}]: factory-owned loop (side_effect_level {old_sel}) removed")

    return findings


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

    boundary_findings = []
    if ".factory/adapter.yaml" in changed_files:
        boundary_findings = _boundary_escalation_findings(clone_dir, args.base_ref)

    hard_trigger = bool(triggered)
    size_trigger = enabled and size_blocks and lines_changed > size_budget
    boundary_trigger = bool(boundary_findings)

    if hard_trigger or size_trigger or boundary_trigger:
        status = "HUMAN_REQUIRED"
    elif not enabled:
        status = "SKIPPED"
    else:
        status = "PASS"

    severity = "critical" if status == "HUMAN_REQUIRED" else "none"
    findings_count = len(triggered) + len(boundary_findings) + (1 if size_trigger else 0)

    trigger_label = "none"
    if boundary_trigger:
        trigger_label = "boundary-escalation"
    elif hard_trigger:
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
    for finding in boundary_findings:
        print(f"  - {finding}")
    for fpath, cats in triggered:
        label = ", ".join(cats)
        print(f"  - {fpath} (category: {label})")
    if size_trigger:
        print(f"  - [size] {lines_changed} lines > {size_budget} budget")
    print(f"LINES_CHANGED: {lines_changed}")


if __name__ == "__main__":
    main()

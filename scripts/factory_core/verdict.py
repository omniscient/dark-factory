"""Canonical verdict schema: STATUS / GATE_TYPE / FINDINGS_COUNT / SEVERITY.

Single documented source of truth for scripts/gate_lib.sh::emit_verdict (bash) and
every Python verdict writer/reader (run_record.py, verifier.py). See
refinement-skills/VERIFIER-CONTRACT.md for the non-Python-reading restatement.

STATUS is a free token (never validated/rejected/normalized by parse_verdict).
Its *gating* values, per scripts/verdict_gate_check.sh, are PASS/SKIPPED/ERROR
(proceed) and BLOCKED (block). HUMAN_REQUIRED (gate_blast_radius.py) and FAIL
(dark-factory-validate.md's prose) are documented legacy tokens outside that gate's
enum but still parsed verbatim by every reader in this repo.
"""

GATING_PASS_STATUSES = {"PASS", "SKIPPED", "ERROR"}
GATING_BLOCK_STATUSES = {"BLOCKED"}
LEGACY_STATUSES = {"HUMAN_REQUIRED", "FAIL"}
SEVERITY_LEVELS = ("none", "low", "medium", "high", "critical")


def parse_verdict(content: str) -> "dict | None":
    """Generic STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY line parser.

    GATE_TYPE/FINDINGS_COUNT/SEVERITY are optional on parse (three review.md writer
    paths omit them today) — only STATUS is required for a non-None result. Returns
    None when no STATUS: line is present at all; callers apply their own per-writer
    loose-fallback heuristic in that case (see run_record.py). Never raises on an
    unrecognized STATUS token or a malformed FINDINGS_COUNT.
    """
    if not content.strip():
        return None
    result: dict = {}
    for line in content.splitlines():
        if line.startswith("STATUS:"):
            result["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("GATE_TYPE:"):
            result["gate_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("FINDINGS_COUNT:"):
            try:
                result["findings_count"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("SEVERITY:"):
            result["severity"] = line.split(":", 1)[1].strip()
    return result if "status" in result else None


def format_verdict(gate_type: str, status: str, findings_count: int, severity: str) -> str:
    """Python-side sibling of gate_lib.sh::emit_verdict — byte-identical shape."""
    return (
        f"STATUS: {status}\n"
        f"GATE_TYPE: {gate_type}\n"
        f"FINDINGS_COUNT: {findings_count}\n"
        f"SEVERITY: {severity}\n"
    )

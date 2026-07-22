"""Pure evidence-gathering/formatting for the Dark Factory post-mortem comment (#182).

Extracted from entrypoint.sh's run_post_mortem(). No gh, no claude/LLM calls in this
module — those stay bash-side and are passed in as plain string arguments.
"""
import json
from pathlib import Path

_ARTIFACT_FILES = ("implementation.md", "conformance.md", "review.md", "plan.md")
_MARKER = "<!-- df-post-mortem -->"


def _find_run_dir(artifacts_base: str, issue_num: int) -> "Path | None":
    base = Path(artifacts_base)
    if not base.is_dir():
        return None
    candidates = []
    for issue_json in base.glob("*/issue.json"):
        try:
            data = json.loads(issue_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("resolved_number") == issue_num:
            candidates.append(issue_json)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.parent


def gather_evidence(artifacts_base: str, issue_num: int, transcript_file: "str | None") -> dict:
    """Reproduces the run-dir discovery + transcript tail + artifact reads at
    entrypoint.sh:196-212."""
    transcript_tail = ""
    if transcript_file and Path(transcript_file).is_file():
        lines = Path(transcript_file).read_text(errors="replace").splitlines()
        transcript_tail = "\n".join(lines[-200:])

    artifacts_context = ""
    run_dir = _find_run_dir(artifacts_base, issue_num)
    if run_dir is not None:
        for name in _ARTIFACT_FILES:
            f = run_dir / name
            if f.is_file():
                content = "\n".join(f.read_text(errors="replace").splitlines()[:100])
                artifacts_context += f"\n\n=== {name} ===\n{content}"

    return {"transcript_tail": transcript_tail, "artifacts_context": artifacts_context}


def build_prompt(exit_code: int, intent: str, issue_num: int, evidence: dict) -> str:
    """Reproduces the prompt template at entrypoint.sh:214-228."""
    transcript_tail = evidence.get("transcript_tail") or "<no transcript available>"
    return f"""You are analyzing a failed dark factory run for issue #{issue_num}.
Exit code: {exit_code}
Intent: {intent}

Write a concise post-mortem paragraph (3-5 sentences) explaining:
1. What phase or step likely failed (based on the transcript tail)
2. The probable root cause
3. What the next run should do differently

Keep it factual and actionable. No markdown headers, just a plain paragraph.

=== Transcript tail (last 200 lines) ===
{transcript_tail}
{evidence.get('artifacts_context', '')}"""


def render_comment(post_mortem_text: str, exit_code: int, intent: str, promoted_at: str,
                    product_name: str = "Dark Factory") -> str:
    """Reproduces the comment body at entrypoint.sh:241-249.

    `product_name` is an explicit parameter, not a literal `${FACTORY_PRODUCT_NAME}`
    token — see "Deviations from the spec" item 1: bash expands that env var at
    the point BODY is assigned (a double-quoted string), before this text is ever
    captured by a `$(...)` Python subprocess call, which does not re-expand it."""
    post_mortem_text = post_mortem_text.rstrip("\n")
    return f"""{_MARKER}
## Dark Factory — Post-Mortem

{post_mortem_text}

**Exit code:** {exit_code} | **Phase:** {intent} | **Timestamp:** {promoted_at}

---
*Posted by {product_name} Dark Factory*"""


def build_failure_record(issue_num: int, title: str, intent: str, exit_code: int,
                          post_mortem_text: str, promoted_at: str) -> dict:
    """Reproduces the JSONL record shape + 500-char/newline-collapsed excerpt logic
    at entrypoint.sh:253-266."""
    excerpt = post_mortem_text[:500].replace("\n", " ")
    return {
        "issue": issue_num,
        "title": title or "unknown",
        "phase": intent,
        "exit_code": exit_code,
        "postmortem": excerpt,
        "promoted_at": promoted_at,
    }


def append_failure_record(record: dict, artifacts_dir: str) -> None:
    """Local JSONL append, mirroring run_record.py's _append_jsonl (create-parents
    behavior; no file locking — run_post_mortem fires at most once per failed run,
    see the spec's Assumptions)."""
    path = Path(artifacts_dir) / "factory-failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

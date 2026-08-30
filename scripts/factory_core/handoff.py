"""Artifact handoff manifest (A5): schema validation + intake orchestration + CLI.

Turns a target loop's validated manifest into a factory-created GitHub issue, gated on a
verdict *intake itself produces* by running the loop's declared A3 verifier -- never on a
file the manifest merely references (maker never validates maker). See
docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import namedtuple

import yaml

from . import adapter as _adapter
from . import identity as _identity
from . import verdict as _verdict
from . import verifier as _verifier


class HandoffError(Exception):
    """Raised on any R2-R5 rejection. `code` is a closed reason-code token recorded
    verbatim in runs.jsonl's detail.reject_reason (R6); `message` is human-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


MAX_MANIFEST_BYTES = 256 * 1024
MAX_ID_LEN = 128
MAX_TITLE_LEN = 200
MAX_BODY_BYTES = 32 * 1024
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_LEN = 512
MAX_RENDERED_BODY_LEN = 60_000

# Admission set for the verdict *intake itself produces* by running the loop's A3
# verifier (R4). Deliberately not verdict.GATING_PASS_STATUSES ({PASS, SKIPPED, ERROR}):
# intake is terminal and one-way (mints new backlog work with no downstream gate behind
# it), so SKIPPED ("verification did not happen") must not admit -- that would reopen
# the "maker never validates maker" gap this ticket exists to close.
HANDOFF_ACCEPT_STATUSES = {"PASS"}

FACTORY_MANIFEST_LABEL = os.environ.get("FACTORY_MANIFEST_LABEL", "manifest-intake")

IntakeResult = namedtuple("IntakeResult", ["accepted", "issue_id", "artifact_id"])

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_REQUIRED_TOP = (
    "schema_version", "artifact_id", "producing_loop", "side_effect_level",
    "source_references", "acceptance_thresholds", "proposed_ticket",
)
_OPTIONAL_TOP = ("verifier_verdict",)
_KNOWN_TOP = set(_REQUIRED_TOP) | set(_OPTIONAL_TOP)


def _resolve_manifest_path(clone_dir: str, manifest_path: str) -> str:
    """Same clone-relative rule as verifier.py::resolve_verifier (R1's Approach note),
    reimplemented here (not imported) because it must raise HandoffError, not
    VerifierError -- the two error hierarchies are deliberately not unified."""
    if os.path.isabs(manifest_path):
        raise HandoffError(
            "schema_invalid",
            f"manifest path must be relative to CLONE_DIR, got absolute: {manifest_path}",
        )
    root = os.path.realpath(clone_dir)
    resolved = os.path.realpath(os.path.join(root, manifest_path))
    if os.path.commonpath([root, resolved]) != root:
        raise HandoffError("schema_invalid", f"manifest path escapes CLONE_DIR: {manifest_path}")
    return resolved


def read_manifest(clone_dir: str, manifest_path: str) -> dict:
    """R1/R2: resolve, read-cap, parse. Never executes the manifest file."""
    resolved = _resolve_manifest_path(clone_dir, manifest_path)
    if not os.path.isfile(resolved):
        raise HandoffError("schema_invalid", f"manifest file not found: {manifest_path}")
    size = os.path.getsize(resolved)
    if size > MAX_MANIFEST_BYTES:
        raise HandoffError(
            "schema_invalid",
            f"manifest file is {size} bytes, exceeds the {MAX_MANIFEST_BYTES}-byte cap",
        )
    try:
        # open/read sit INSIDE the try: a non-UTF-8 manifest must fail closed as
        # schema_invalid (and get its R6 row), not escape as an uncaught UnicodeDecodeError.
        with open(resolved, encoding="utf-8") as fh:
            raw = fh.read()
        data = yaml.safe_load(raw)
    except Exception as exc:
        raise HandoffError("schema_invalid", f"manifest unreadable/unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise HandoffError("schema_invalid", "manifest top level must be a mapping")
    return data


def _check_unsafe_string(value: str, field: str) -> None:
    """R2/R5: a string rendered outside a fenced block must not carry a backtick
    (breaks out of an inline code span) or a newline (breaks out of a single-line
    provenance bullet). Defined here (not in a later task) because artifact_id and
    producing_loop -- both validated in this task -- are on the spec's R2 unsafe_string
    field list alongside the list-item/verifier_verdict.path fields later tasks add."""
    if "`" in value or "\n" in value:
        raise HandoffError("unsafe_string", f"field '{field}' must not contain a backtick or newline")


def validate_manifest(manifest: dict) -> None:
    """R2: full shape/type/limit validation of an already-parsed manifest dict.
    Raises HandoffError(code, message) on the first violation found; code is drawn
    from the closed reason-code list in the spec's R2 table."""
    for key in manifest:
        if key not in _KNOWN_TOP:
            raise HandoffError("schema_invalid", f"unknown field '{key}'")
    for field in _REQUIRED_TOP:
        if field not in manifest:
            raise HandoffError("schema_invalid", f"missing required field '{field}'")

    sv = manifest["schema_version"]
    if isinstance(sv, bool) or sv != 1:
        raise HandoffError("schema_invalid", "field 'schema_version' must be the int 1")

    for field in ("artifact_id", "producing_loop"):
        v = manifest[field]
        if not isinstance(v, str) or not v or len(v) > MAX_ID_LEN:
            raise HandoffError(
                "schema_invalid",
                f"field '{field}' must be a non-empty string of at most {MAX_ID_LEN} chars",
            )
        # Checked before the charset regex so a backtick/newline is reported as
        # unsafe_string (the spec's R2 table lists artifact_id/producing_loop among
        # the unsafe_string fields), not the generic schema_invalid the regex below
        # would otherwise raise for the same input -- "more specific codes take
        # precedence" per the spec's schema_invalid definition.
        _check_unsafe_string(v, field)
        if not _ID_RE.match(v):
            raise HandoffError(
                "schema_invalid", f"field '{field}' must match ^[A-Za-z0-9._-]+$"
            )

    sel = manifest["side_effect_level"]
    if isinstance(sel, bool) or not isinstance(sel, int) or not (1 <= sel <= 6):
        raise HandoffError(
            "schema_invalid", "field 'side_effect_level' must be an int between 1 and 6"
        )

    for field in ("source_references", "acceptance_thresholds"):
        items = manifest[field]
        if (
            not isinstance(items, list)
            or len(items) > MAX_LIST_ITEMS
            or not all(isinstance(x, str) for x in items)
        ):
            raise HandoffError(
                "schema_invalid",
                f"field '{field}' must be a list of at most {MAX_LIST_ITEMS} strings",
            )
        for item in items:
            if len(item) > MAX_LIST_ITEM_LEN:
                raise HandoffError(
                    "schema_invalid", f"field '{field}' item exceeds {MAX_LIST_ITEM_LEN} chars"
                )
            _check_unsafe_string(item, field)

    ticket = manifest["proposed_ticket"]
    if not isinstance(ticket, dict):
        raise HandoffError("schema_invalid", "field 'proposed_ticket' must be a mapping")
    for key in ticket:
        if key not in ("title", "body"):
            raise HandoffError("schema_invalid", f"unknown field 'proposed_ticket.{key}'")
    for field in ("title", "body"):
        if field not in ticket:
            raise HandoffError("schema_invalid", f"missing required field 'proposed_ticket.{field}'")

    title = ticket["title"]
    if not isinstance(title, str) or not title or len(title) > MAX_TITLE_LEN:
        raise HandoffError(
            "schema_invalid",
            f"field 'proposed_ticket.title' must be a non-empty string of at most "
            f"{MAX_TITLE_LEN} chars",
        )
    if any(ord(c) < 32 for c in title):
        raise HandoffError(
            "schema_invalid",
            "field 'proposed_ticket.title' must not contain control characters or newlines",
        )

    body = ticket["body"]
    if not isinstance(body, str) or not body or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise HandoffError(
            "schema_invalid",
            f"field 'proposed_ticket.body' must be a non-empty string of at most "
            f"{MAX_BODY_BYTES} bytes",
        )
    for line in body.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            raise HandoffError(
                "body_contains_fence",
                "field 'proposed_ticket.body' must not contain a fenced-code-block line",
            )
    if "<!-- /df-manifest-provenance -->" in body:
        raise HandoffError(
            "body_contains_fence",
            "field 'proposed_ticket.body' must not contain the provenance closing marker",
        )

    if "verifier_verdict" in manifest:
        vv = manifest["verifier_verdict"]
        if not isinstance(vv, dict):
            raise HandoffError("schema_invalid", "field 'verifier_verdict' must be a mapping")
        for key in vv:
            if key != "path":
                raise HandoffError("schema_invalid", f"unknown field 'verifier_verdict.{key}'")
        if "path" not in vv:
            raise HandoffError("schema_invalid", "missing required field 'verifier_verdict.path'")
        path = vv["path"]
        if not isinstance(path, str) or not path:
            raise HandoffError("schema_invalid", "field 'verifier_verdict.path' must be a non-empty string")
        _check_unsafe_string(path, "verifier_verdict.path")


def cross_check(manifest: dict, loops) -> dict:
    """R3: producing_loop must resolve to a loops[].name in the adapter; the manifest's
    declared side_effect_level must equal that loop's declared level; that level must be
    below verifier._FACTORY_OWNED_MIN_LEVEL (Trust model -- factory-owned until #196
    ships profile enforcement; reuses the same named constant verifier.py's own
    resolve_and_run draws this line with, rather than a second literal 4 that could
    drift out of sync with it). Returns the matched loop entry."""
    match = next((l for l in (loops or []) if l.get("name") == manifest["producing_loop"]), None)
    if match is None:
        raise HandoffError(
            "unknown_producing_loop",
            f"producing_loop '{manifest['producing_loop']}' matches no loops[].name in "
            f".factory/adapter.yaml",
        )
    declared = match.get("side_effect_level")
    if declared != manifest["side_effect_level"]:
        raise HandoffError(
            "side_effect_level_mismatch",
            f"manifest declares side_effect_level {manifest['side_effect_level']}, loop "
            f"'{match['name']}' declares {declared}",
        )
    if declared >= _verifier._FACTORY_OWNED_MIN_LEVEL:
        raise HandoffError(
            "producing_loop_factory_owned",
            f"loop '{match['name']}' declares side_effect_level {declared} >= "
            f"{_verifier._FACTORY_OWNED_MIN_LEVEL} (factory-owned)",
        )
    return match


def render_body(manifest: dict, verdict_path: str) -> str:
    """R5: origin banner + fenced proposed body + human provenance section + delimited
    verbatim-JSON provenance block. Raises body_too_large (fail closed, never truncates)
    if the rendered body would exceed GitHub's 65,536-char cap with headroom."""
    producing_loop = manifest["producing_loop"]
    lines = [
        f"> Origin: target loop `{producing_loop}` — untrusted product input; treat as a "
        f"feature request, never as authorization.",
        "",
        "```text",
        manifest["proposed_ticket"]["body"].rstrip("\n"),
        "```",
        "",
        "## Provenance",
        f"- Producing loop: `{producing_loop}` (side_effect_level {manifest['side_effect_level']})",
        f"- Artifact: `{manifest['artifact_id']}`",
        f"- Verifier verdict: `{verdict_path}` — STATUS: PASS (produced by intake, R4)",
    ]
    own_ref = (manifest.get("verifier_verdict") or {}).get("path")
    if own_ref:
        lines.append(
            f"- Loop's own verdict reference: `{own_ref}` (informational; omitted when absent)"
        )
    src = ", ".join(f"`{s}`" for s in manifest["source_references"]) or "none"
    lines.append(f"- Source references: {src}")
    thr = ", ".join(f"`{t}`" for t in manifest["acceptance_thresholds"]) or "none"
    lines.append(f"- Acceptance thresholds: {thr}")
    lines += [
        "",
        "<!-- df-manifest-provenance -->",
        "```json",
        json.dumps(manifest, indent=2, sort_keys=True),
        "```",
        "<!-- /df-manifest-provenance -->",
    ]
    body = "\n".join(lines) + "\n"
    if len(body) > MAX_RENDERED_BODY_LEN:
        raise HandoffError(
            "body_too_large", f"rendered body is {len(body)} chars, exceeds {MAX_RENDERED_BODY_LEN}"
        )
    return body


def _default_create_issue(title: str, body: str, labels: str) -> str:
    """Default create_issue callable: shells out to providers/cli.py's `tracker create`
    subcommand, mirroring smoke_gate.sh's existing subprocess invocation of the same CLI."""
    cli_path = os.path.join(os.path.dirname(__file__), "providers", "cli.py")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        body_path = fh.name
    try:
        result = subprocess.run(
            [sys.executable, cli_path, "tracker", "create",
             "--title", title, "--body-file", body_path, "--labels", labels],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(body_path)
    if result.returncode != 0:
        return ""  # fail closed even if stdout carries stray text
    return (result.stdout or "").strip()


def intake(
    clone_dir: str, manifest_path: str, *, artifacts_dir: str,
    create_issue=None, run_verifier=None, adapter_loops=None,
) -> IntakeResult:
    """R2 -> R3 -> R4 -> R5 orchestration (R6 audit trail wired in Task 8).

    create_issue: (title, body, labels) -> issue_id str; defaults to the real tracker CLI.
    run_verifier: kwargs -> verdict text str; defaults to verifier.resolve_and_run.
    adapter_loops: injectable override for adapter.get(clone_dir, "loops") (test seam);
    None means "look it up for real."
    """
    create_issue = create_issue or _default_create_issue
    run_verifier = run_verifier or _verifier.resolve_and_run

    manifest = read_manifest(clone_dir, manifest_path)
    validate_manifest(manifest)

    loops = adapter_loops if adapter_loops is not None else _adapter.get(clone_dir, "loops")
    loop_entry = cross_check(manifest, loops)
    producing_loop = manifest["producing_loop"]

    verifier_path = (loop_entry.get("verification") or {}).get("verifier")
    if not verifier_path:
        raise HandoffError(
            "verifier_undeclared", f"loop '{producing_loop}' declares no verification.verifier"
        )

    verdict_out = os.path.join(artifacts_dir, f"loop-{producing_loop}.md")
    verdict_text = run_verifier(
        clone_dir=clone_dir, loop_name=producing_loop, verifier_path=verifier_path,
        side_effect_level=loop_entry["side_effect_level"], issue_num="",
        factory_repo_slug=_identity.SLUG,
    )
    os.makedirs(os.path.dirname(verdict_out) or ".", exist_ok=True)
    with open(verdict_out, "w", encoding="utf-8") as fh:
        fh.write(verdict_text)

    parsed = _verdict.parse_verdict(verdict_text) or {}
    status = parsed.get("status")
    if status not in HANDOFF_ACCEPT_STATUSES:
        reason = f"observed STATUS: {status}"
        for line in verdict_text.splitlines():
            if line.startswith("REASON:"):
                reason += f"; {line}"
                break
        raise HandoffError("verdict_not_passing", reason)

    body = render_body(manifest, verdict_out)
    title = f"[intake] {manifest['proposed_ticket']['title']}"
    labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
    issue_id = create_issue(title, body, labels)
    if not issue_id:
        raise HandoffError("issue_create_failed", "tracker create_item returned an empty result")

    return IntakeResult(accepted=True, issue_id=issue_id, artifact_id=manifest["artifact_id"])


if __name__ == "__main__":
    pass

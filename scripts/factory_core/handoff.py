"""Artifact handoff manifest (A5): schema validation + intake orchestration + CLI.

Turns a target loop's validated manifest into a factory-created GitHub issue, gated on a
verdict *intake itself produces* by running the loop's declared A3 verifier -- never on a
file the manifest merely references (maker never validates maker). See
docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md.
"""
import os
import re

import yaml

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


if __name__ == "__main__":
    pass

"""Target-registered check-only verifier resolution + invocation.

Generalizes hooks.sh::run_hook's target-over-default, check-only, factory-owns-
side-effects precedent from a fixed .factory/hooks/<name> convention to an
arbitrary adapter-declared path (a loop entry's verification.verifier field, #301).
See refinement-skills/VERIFIER-CONTRACT.md for the full registration contract.
"""
import argparse
import os
import subprocess
import sys

from . import verdict as _verdict

DEFAULT_TIMEOUT_SECONDS = 300


class VerifierError(Exception):
    """Raised on any condition verifier.py must fail closed for: missing path,
    non-executable path, timeout, or a process that could not be started."""


def resolve_verifier(clone_dir: str, verifier_path: str) -> str:
    """Resolve an adapter-declared verifier path relative to clone_dir.

    Unlike hooks.sh::run_hook, a target verifier has no built-in factory default to
    fall back to — a missing/non-executable result is a fail-closed condition
    (Requirement 4), not a no-op.
    """
    return os.path.join(clone_dir, verifier_path)


def run_verifier(resolved_path: str, env: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> "tuple[int, str]":
    """Execute resolved_path with env; returns (exit_code, stdout).

    Raises VerifierError on missing/non-executable path, timeout, or a process that
    could not be started — callers must catch this and synthesize STATUS: BLOCKED
    (Requirement 4), never let it surface as an unhandled crash.
    """
    if not os.path.isfile(resolved_path) or not os.access(resolved_path, os.X_OK):
        raise VerifierError(f"verifier path missing or not executable: {resolved_path}")
    try:
        proc = subprocess.run(
            [resolved_path], env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerifierError(f"verifier timed out after {timeout}s: {resolved_path}") from exc
    except OSError as exc:
        raise VerifierError(f"verifier process could not be started: {exc}") from exc
    return proc.returncode, proc.stdout


def normalize_verdict(exit_code: int, stdout: str, gate_type: str) -> str:
    """Structured vs. bare-exit-code dispatch (Requirement 4).

    Structured: stdout already begins with a STATUS: line — parsed through the
    shared schema, then re-namespaced onto gate_type (never trusted verbatim from
    the verifier's own stdout, per Requirement 4). A self-reported STATUS: ERROR
    is remapped to BLOCKED/high -- ERROR is not auto-pass-through for target
    verifiers (unlike code_review.fail_open's advisory-on-error default); AC3
    requires "missing/failing cannot hand off" as the default, not an opt-in.
    A structured PASS/SKIPPED from a process that exited non-zero is likewise
    remapped to BLOCKED/high (fail closed): the exit code wins over a
    proceed-status; a structured BLOCKED is honoured regardless of exit code.
    Bare-exit-code: no structured output — exit 0 synthesizes PASS, non-zero
    synthesizes BLOCKED/high, mirroring smoke-gate's exit-code-only convention.
    """
    if stdout.lstrip().startswith("STATUS:"):
        parsed = _verdict.parse_verdict(stdout) or {}
        status = parsed.get("status", "ERROR")
        if status == "ERROR":
            return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
        if exit_code != 0 and status in ("PASS", "SKIPPED"):
            # Fail closed: a proceed-status printed by a process that then exited
            # non-zero is not trusted -- the exit code wins (Requirement 4).
            return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
        return _verdict.format_verdict(
            gate_type, status, parsed.get("findings_count", 0), parsed.get("severity", "none"),
        )
    if exit_code == 0:
        return _verdict.format_verdict(gate_type, "PASS", 0, "none")
    return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")


# GATE_TYPE basenames the ticket-lifecycle pipeline already owns; a target verdict
# artifact must never collide with one of these (Requirement 4).
_RESERVED_OUT_BASENAMES = {
    "validation.md", "conformance.md", "review.md",
    "conflict_resolution.md", "blast.md",
}

# side_effect_level range that is factory-owned until #196 ships real
# permission-profile enforcement (per #193).
_FACTORY_OWNED_MIN_LEVEL = 4


def assert_verifier_independent(loop_entry: dict) -> None:
    """Path-disjointness rule (Requirement 5): a loop's verifier must not be the
    loop's own handoff producer or a file it writes.

    owned = {handoff.manifest} ∪ set(handoff.outputs) ∪ set(persistence.artifacts)
    String/path comparison only (os.path.normpath) — no filesystem access, no
    existence check, consistent with #301's opaque-reference treatment of these
    fields. This is the declaration-time half of maker≠checker; the load-bearing
    half is that the verifier always runs as a separate check-only process whose
    verdict the factory parses and acts on (#189's clean-room-grader principle).
    """
    verifier_path = (loop_entry.get("verification") or {}).get("verifier")
    handoff = loop_entry.get("handoff") or {}
    persistence = loop_entry.get("persistence") or {}
    owned = set()
    manifest = handoff.get("manifest")
    if manifest:
        owned.add(os.path.normpath(manifest))
    for p in handoff.get("outputs") or []:
        owned.add(os.path.normpath(p))
    for p in persistence.get("artifacts") or []:
        owned.add(os.path.normpath(p))
    if verifier_path and os.path.normpath(verifier_path) in owned:
        name = loop_entry.get("name", "?")
        raise VerifierError(
            f"loop '{name}': verifier '{verifier_path}' must not be a path the loop "
            f"itself owns (handoff.manifest / handoff.outputs / persistence.artifacts)"
        )


def resolve_and_run(
    *, clone_dir: str, loop_name: str, verifier_path: str,
    issue_num: str = "", factory_repo_slug: str = "",
    side_effect_level: "int | None" = None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """End-to-end: resolve, run, normalize, record required_profile + side_effect_level.

    Fails closed (STATUS: BLOCKED) on every non-PASS-able condition: missing/
    non-executable path, timeout, undetermined side_effect_level, or a
    side_effect_level in the factory-owned range (Requirement 6) — never silently
    skips (AC3). Records SIDE_EFFECT_LEVEL on every verdict where a level was
    resolved, so a future #196 enforcement layer has something to check against
    (Requirement 6a); an undetermined level has no level to record. This is the
    primitive a future dispatcher, the CLI below, or a test calls per declared loop.
    """
    gate_type = f"loop:{loop_name}"

    if side_effect_level is None:
        return (
            _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
            + "REQUIRED_PROFILE: undetermined\nREASON: side_effect_level not resolved\n"
        )
    if side_effect_level >= _FACTORY_OWNED_MIN_LEVEL:
        return (
            _verdict.format_verdict(gate_type, "BLOCKED", 1, "high")
            + f"REQUIRED_PROFILE: factory-owned\nSIDE_EFFECT_LEVEL: {side_effect_level}\n"
            + "REASON: factory-owned level requires #196 profile enforcement\n"
        )

    resolved = resolve_verifier(clone_dir, verifier_path)
    env = dict(os.environ)
    env.update({
        "CLONE_DIR": clone_dir,
        "ARTIFACTS_DIR": env.get("ARTIFACTS_DIR", ""),
        "ISSUE_NUM": issue_num,
        "FACTORY_REPO_SLUG": factory_repo_slug,
        "LOOP_NAME": loop_name,
    })
    profile_suffix = f"REQUIRED_PROFILE: level-1\nSIDE_EFFECT_LEVEL: {side_effect_level}\n"
    try:
        exit_code, stdout = run_verifier(resolved, env, timeout=timeout)
    except VerifierError:
        return _verdict.format_verdict(gate_type, "BLOCKED", 1, "high") + profile_suffix

    return normalize_verdict(exit_code, stdout, gate_type) + profile_suffix


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve and run a target-registered check-only verifier")
    p.add_argument("--clone-dir", required=True)
    p.add_argument("--loop-name", required=True)
    p.add_argument("--verifier-path", required=True)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--issue-num", default=os.environ.get("ISSUE_NUM", ""))
    p.add_argument("--factory-repo-slug", default=os.environ.get("FACTORY_REPO_SLUG", ""))
    p.add_argument("--side-effect-level", type=int, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--out", required=True)
    args = p.parse_args()

    out_basename = os.path.basename(args.out)
    if out_basename in _RESERVED_OUT_BASENAMES:
        print(
            f"verifier: --out basename '{out_basename}' is reserved for the "
            f"ticket-lifecycle pipeline artifacts", file=sys.stderr,
        )
        sys.exit(2)

    verdict_text = resolve_and_run(
        clone_dir=args.clone_dir, loop_name=args.loop_name, verifier_path=args.verifier_path,
        issue_num=args.issue_num, factory_repo_slug=args.factory_repo_slug,
        side_effect_level=args.side_effect_level, timeout=args.timeout,
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(verdict_text)


if __name__ == "__main__":
    main()

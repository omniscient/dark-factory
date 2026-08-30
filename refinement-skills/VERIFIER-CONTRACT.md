# Verifier / checker-invocation contract

Shared by every checker subagent spawn (`refine`'s product-owner, `plan`'s architect
and Phase 3.5 conformance reviewer, `conformance`'s reviewer, `code-review`'s
reviewer) and by every target-registered check-only verifier (`verification.verifier`
on a `.factory/adapter.yaml` `loops:` entry, #301). See
`docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md` for the full
design; this doc is the operational reference command authors and target-repo authors
read directly.

## Checker subagent invocation (Opus-pinned pairs)

- **Model pin:** always `claude-opus-4-8` for the checker subagent — never let it
  inherit the orchestrator's model. This applies to every re-spawn in a reconcile
  loop, not just the first spawn.
- **Read access:** the checker subagent needs `Glob`, `Grep`, and `Read` to explore
  the codebase it is reviewing. No tool restriction is introduced or documented as
  existing beyond this — tool allow/deny changes are a separate, reviewed concern
  (CLAUDE.md).
- **Clone-live-first resolution (rubric docs only):** `conformance`'s and
  `code-review`'s reviewer rubrics, and `plan`'s Phase 3.5 conformance rubric, read
  the live clone first (e.g. `.claude/skills/conformance/RUBRIC.md`), falling back to
  the baked copy under `/opt/refinement-skills/*.md` only if the clone-live file is
  absent — this lets a target repo override a rubric without a factory image
  rebuild. `refine`'s product-owner prompt and `plan`'s Phase 3 architect prompt are
  **not** part of this pattern: they keep reading their baked `/opt/refinement-
  skills/{product-owner,architect}-prompt.md` copies as-is, unchanged by this
  ticket. This contract doc itself (`VERIFIER-CONTRACT.md`) is always read at its
  fixed baked path, `/opt/refinement-skills/VERIFIER-CONTRACT.md`, by all four
  commands — it is not itself subject to clone-live-first resolution.

## Verdict schema

`STATUS` / `GATE_TYPE` / `FINDINGS_COUNT` / `SEVERITY` — canonically implemented in
`scripts/factory_core/verdict.py` (`parse_verdict`/`format_verdict`) and
`scripts/gate_lib.sh::emit_verdict` (bash). `STATUS` is a free token; its *gating*
values (per `scripts/verdict_gate_check.sh`) are `PASS`/`SKIPPED`/`ERROR` (proceed)
and `BLOCKED` (block). `HUMAN_REQUIRED` and `FAIL` are documented legacy tokens
returned verbatim, never rejected or normalized. `GATE_TYPE`/`FINDINGS_COUNT`/
`SEVERITY` are optional on parse, required on emit. `SEVERITY` ∈
`{none, low, medium, high, critical}`.

## Target-verifier registration contract

A target repo declares a check-only verifier via a loop entry's
`verification.verifier` field (an opaque path, resolved relative to the clone root
by `scripts/factory_core/verifier.py::resolve_verifier`; an absolute path, or a path
whose realpath lands outside the clone root, is rejected and fails closed). Invocation:

```bash
python3 -m factory_core.verifier \
  --clone-dir "$CLONE_DIR" --loop-name <loop name> \
  --verifier-path <verification.verifier path> --side-effect-level <loop's resolved level> \
  run --out <artifact path>
```

`--side-effect-level` has no default (`None`) and `resolve_and_run` fails closed when
it is absent — a caller must always resolve and pass the loop's actual level.

- **Env contract** (exported to the verifier process, mirroring
  `hooks.sh::run_hook`'s existing four-variable contract plus one addition):
  `CLONE_DIR`, `ARTIFACTS_DIR`, `ISSUE_NUM`, `FACTORY_REPO_SLUG`, `LOOP_NAME`.
- **Output modes:**
  - *Structured* — stdout begins with a `STATUS:` line: parsed through the shared
    schema. `GATE_TYPE` is always rewritten to `loop:<loop name>` — never trusted
    verbatim from the verifier's own stdout. A structured `STATUS: PASS` or
    `STATUS: SKIPPED` is **not** trusted when the process exits non-zero — it is
    remapped to `STATUS: BLOCKED, FINDINGS_COUNT: 1, SEVERITY: high` (fail closed),
    so a verifier that prints `PASS` and then crashes is caught by its exit code. A
    structured `STATUS: BLOCKED` is honoured regardless of exit code.
  - *Bare-exit-code* — no structured stdout: exit `0` synthesizes `STATUS: PASS`,
    non-zero synthesizes `STATUS: BLOCKED, FINDINGS_COUNT: 1, SEVERITY: high`. This
    mirrors `smoke-gate`'s existing bare-exit-code convention as the low-effort
    on-ramp for a target's first verifier.
- **Fail-closed defaults:** a missing path, a non-executable path, a timeout
  (`--timeout`, default 300s), or a process that cannot be started all produce
  `STATUS: BLOCKED` — never a silent skip. `ERROR` is reserved for "the verifier ran
  and self-reported it could not complete" and is **not** auto-pass-through for
  target verifiers (unlike `code_review.fail_open`'s advisory-on-error default): it
  is emitted as `STATUS: BLOCKED` with `REASON: verifier self-reported ERROR`. On
  emit, a `SEVERITY` outside `{none, low, medium, high, critical}` is clamped to
  `none` and a negative `FINDINGS_COUNT` to `0`.
- **Reserved output names:** `verifier.py`'s `--out` refuses the basenames
  `validation.md`, `conformance.md`, `review.md`, `conflict_resolution.md`,
  `blast.md` — a target verdict can only *add* a `BLOCK` on its own loop's handoff
  and is never read by `conformance-gate`/`review-gate`.
- **Maker≠checker:** a loop's declared `verification.verifier` must not equal
  `handoff.manifest`, and must not be a member of `handoff.outputs` or
  `persistence.artifacts` — enforced by `verifier.assert_verifier_independent()`,
  called from `adapter.py::load()`. This is a declaration-time string check; the
  load-bearing half is that the verifier always runs as a separate check-only
  process whose verdict the factory (not the loop) parses and acts on.
- **Permission profile:** `verifier.py` records `REQUIRED_PROFILE: level-1` and
  `SIDE_EFFECT_LEVEL: <n>` on every verdict where a level was resolved, and fails
  closed if a loop's `side_effect_level` cannot be resolved. It does not itself
  sandbox or restrict the verifier process — that enforcement is `#196`'s chartered
  scope. Loops with `side_effect_level >= 4` are factory-owned and fail closed
  rather than executing a target path, until `#196` ships.
- **Origin attribution.** `verifier.py` records `ORIGIN: target-loop:<loop_name>` on every
  verdict `resolve_and_run` returns, on all four return points (including both early
  fail-closed returns) — `loop_name` is always available on entry, so the line's value never
  depends on which branch returns. `run_record.py record --origin target-loop:<name>` writes
  the matching `origin` field on a `runs.jsonl` audit row (default `factory` for every
  existing caller). This is the field the A5 intake path (`#199`) reads to attribute a row to
  the target loop that produced a handoff manifest.

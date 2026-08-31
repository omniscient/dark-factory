# Harden the `FACTORY_MANIFEST_LABEL` override guard

**Issue:** #384 · **Depends on:** #381 (shipped, PR #383) · **Status:** spec-pending-review

## Overview / Problem Statement

#381 added a guard in `scripts/factory_core/handoff.py::intake()` that rejects a
`FACTORY_MANIFEST_LABEL` override (operator/deploy env config, not target-controlled input) if
it contains a comma/whitespace, or case-folds to exactly `ready-for-agent`, or ends with
`-pending-review`. Gate-3 review of the resulting PR #383 raised three advisory findings that
the run correctly deferred rather than folding into #381's own diff — this ticket is the "own
reviewed ticket" that deferral called for.

The guard exists to stop a misconfigured override from smuggling a target-loop-authored
`manifest-intake` issue into a state the scheduler's dispatch predicates treat as opted into
automation. Gate-3 found the guard is narrower than the scheduler predicates it defends
against, and validates later than it needs to:

1. **Substring vs. exact/suffix match.** `scheduler_lib.sh::has_opt_in_refine_label` and the
   inline `spec-pending-review`/`plan-pending-review` greps in `scheduler.sh` all match with
   unanchored `grep -qi` against the full label text. A label folding to
   `manifest-intake-ready-for-agent` fails today's guard's exact-match check
   (`label_folded == "ready-for-agent"`) yet substring-matches the scheduler's own predicate —
   the guard is weaker than the thing it's supposed to be at-least-as-strict-as.
2. **`direct-to-pr` is not denied at all.** Landing an issue in that state is a strictly wider
   escalation than the two gate labels already denied: `direct-to-pr` enables grace-timer
   auto-advance past spec/plan review *and* end-gate auto-merge, on an issue whose title/body
   came from target-loop artifact data.
3. **Validation runs late.** The check sits after `run_verifier()` and after the verdict file is
   written, so a misconfigured override still pays a full verifier subprocess and leaves an
   orphan verdict file on the artifacts mount before the config error is caught.

Severity is defense-in-depth (the input is operator-set env config, not attacker-controlled
target data), which is why #381 sized this correctly as XS and did not gate the original ticket
on it.

## Requirements

- Reject the override when its case-folded value contains `ready-for-agent` anywhere in the
  string (not just equals it), matching `has_opt_in_refine_label`'s substring semantics.
- Reject the override when its case-folded value contains `-pending-review` anywhere in the
  string (not just ends with it) — containment is a strict superset of suffix, so this also
  covers today's suffix cases.
- Reject the override when its case-folded value contains the literal `direct-to-pr`, **and**
  also contains the case-folded, stripped value of `os.environ.get("DIRECT_TO_PR_LABEL")` when
  that env var is set to a non-empty (post-strip) string — so an operator who renamed the label
  via the same env-override mechanism `scheduler.sh` already reads doesn't un-deny the
  escalation by renaming it. An empty or whitespace-only `DIRECT_TO_PR_LABEL` must fall back to
  denying only the literal `direct-to-pr`, never contribute an empty string to the containment
  check (an empty needle would make the check true for every override, including the default
  `manifest-intake`, and fail closed on all normal operation).
- Move the entire override-validation block (the emptiness/comma/whitespace check plus the
  three deny checks above) to the top of `intake()`, before `read_manifest()` is called. The
  check depends only on process env, never on manifest content, so it does not need anything
  `read_manifest()`/`validate_manifest()`/`cross_check()`/`run_verifier()` produce.
- Preserve the existing failure contract: a failing check still raises `ValueError`, still
  caught by `intake()`'s generic `except Exception` arm, still recorded to `runs.jsonl` with
  `reject_reason: internal_error`, still raised onward as `HandoffError("internal_error", ...)`.
  `artifact_id`/`producing_loop` in that audit row will now read `"unknown"`/`None` (their
  pre-`read_manifest` initial values) for this rejection path specifically, since the check now
  runs before the manifest is parsed — see Assumptions.
- Update the error message raised by the check to name all three denied shapes so an operator
  reading the exception text understands why their override was rejected.
- Update `docs/triage-labels.md`'s `manifest-intake` row (line 44), which currently says the
  override "rejects `ready-for-agent` and any `*-pending-review` label" with exact/suffix
  framing, to describe the new containment-based, three-way deny-list.

## Non-goals

- No changes to `scripts/scheduler_lib.sh` or `scheduler.sh`. The unanchored `grep -qi`
  matchers there (`has_opt_in_refine_label`, `has_direct_to_pr_label`, the inline
  `*-pending-review` greps, `has_refine_skip_label`, `get_size_label`) are the thing this
  guard defends against, not something this ticket touches — anchoring them is a dispatch-gate
  behavior change (CLAUDE.md: "gate changes get their own reviewed ticket") with its own blast
  radius (`has_refine_skip_label` loops over a configurable list; `has_direct_to_pr_label`
  interpolates an env var unescaped into the pattern; `get_size_label` relies on unanchored
  matching by design to pull `size: S` out of label text) that doesn't belong in an XS ticket.
- No broadening of the deny-list beyond the three shapes above (e.g. `needs-discussion`,
  `epic`, `above-ceiling`, `no-autopilot`). Those are skip/pause or advisory labels, not
  escalations — landing a manifest-intake issue in one of those states is inert or safe-by-default,
  unlike `direct-to-pr`'s auto-merge path. #381's spec already considered and rejected a
  broader list at S-scope; nothing new here changes that calculus for labels outside the three
  Gate-3 findings named.
- No change to `handoff.py`'s existing pattern of reading env vars directly (no introduction of
  a `config.yaml` loader or `adapter.get()` call for this check) — `DIRECT_TO_PR_LABEL` is read
  the same bare-env-var way `FACTORY_MANIFEST_LABEL` already is.

## Brainstorming Q&A

> **Q:** Finding 2 asks the new guard to deny "`direct-to-pr` (and the configured
> `DIRECT_TO_PR_LABEL` value)". Since handoff.py has no existing pattern for reading
> `config.yaml` and only reads bare env vars directly (mirroring how `FACTORY_MANIFEST_LABEL`
> itself is read), should the new direct-to-pr deny-check in `handoff.py::intake()` also read
> `os.environ.get("DIRECT_TO_PR_LABEL", "direct-to-pr")`, or is hardcoding the literal string
> `"direct-to-pr"` sufficient for this XS ticket?
>
> **A:** Read the env var — `os.environ.get("DIRECT_TO_PR_LABEL", "direct-to-pr")` — but only
> honor it when non-empty after stripping (an empty/blank override must not make the
> containment check vacuously true and reject every valid override). Always also deny the
> literal `direct-to-pr` in addition to the resolved value, so a rename via the env var can't
> un-deny the canonical name. Use substring containment, consistent with finding 1, and fold
> the failure into the existing `ValueError` → `internal_error` path.

> **Q:** Should this ticket also modify the underlying unanchored `grep -qi` matchers in
> `scripts/scheduler_lib.sh` (`has_opt_in_refine_label`, `has_direct_to_pr_label`) and
> `scheduler.sh`'s inline `spec-pending-review`/`plan-pending-review` greps to be
> anchored/exact-match — or is this ticket's scope strictly limited to hardening the
> Python-side deny-list in `handoff.py::intake()`, treating the scheduler's own label-matching
> behavior as out of scope and matched-against rather than fixed?
>
> **A:** Strictly limited to the Python side. The scheduler's greps decide whether work is
> dispatched at all; tightening them can only make dispatch *stop* firing (a silent-stall
> failure mode), which is a design conversation with its own blast radius, not a safe XS
> drive-by, and is exactly the kind of gate-adjacent change CLAUDE.md reserves for its own
> reviewed ticket. The threat models also differ: GitHub labels are curated by maintainers/the
> factory itself, while `FACTORY_MANIFEST_LABEL` is an operator-set env value on a
> target-loop-facing intake path — hardening the env-var guard is the defense-in-depth this
> ticket is about.

## Architecture / Approach

Single change, contained to `scripts/factory_core/handoff.py`: replace the four-condition
`if` block currently at lines 436-441 with a resolved-deny-list check, and relocate the whole
validation block (comment, resolution, `if`, `raise ValueError`) from its current position
(after `run_verifier()`/verdict-file write, before `render_body()`) to the top of `intake()`,
*inside* the outer `try:` block — after the `artifact_id = "unknown"` /
`producing_loop = None` / `verdict_out = None` defaults are assigned and before
`manifest = read_manifest(...)` — so the relocated `ValueError` is raised inside the same
`try` and the generic `except Exception` arm still records the `runs.jsonl` row.

Sketch (illustrative, not final code):

```python
def intake(clone_dir, manifest_path, *, artifacts_dir, create_issue=None, run_verifier=None, adapter_loops=None):
    create_issue = create_issue or _default_create_issue
    run_verifier = run_verifier or _verifier.resolve_and_run

    artifact_id = "unknown"
    producing_loop = None
    verdict_out = None
    try:
        # FACTORY_MANIFEST_LABEL / DIRECT_TO_PR_LABEL validation depends only on process
        # env, never on manifest content -- run it before read_manifest() so a
        # misconfigured override fails before paying a verifier subprocess or writing an
        # orphan verdict file. It must stay INSIDE this try so the ValueError is caught
        # by the generic except Exception arm below (runs.jsonl row + HandoffError).
        label_folded = FACTORY_MANIFEST_LABEL.lower()
        direct_to_pr_folded = os.environ.get("DIRECT_TO_PR_LABEL", "").strip().lower()
        deny_substrings = ["ready-for-agent", "-pending-review", "direct-to-pr"]
        if direct_to_pr_folded:
            deny_substrings.append(direct_to_pr_folded)
        if (
            not FACTORY_MANIFEST_LABEL
            or re.search(r"[,\s]", FACTORY_MANIFEST_LABEL)
            or any(needle in label_folded for needle in deny_substrings)
        ):
            raise ValueError(
                f"FACTORY_MANIFEST_LABEL override must be a single label with no comma or "
                f"whitespace, and must not contain a gate/escalation label shape "
                f"(ready-for-agent, *-pending-review, or direct-to-pr), got: "
                f"{FACTORY_MANIFEST_LABEL!r}"
            )

        manifest = read_manifest(clone_dir, manifest_path)
        ...
        labels = f"needs-triage,{FACTORY_MANIFEST_LABEL}"
        issue_id = create_issue(title, body, labels)
        ...
```

The `except HandoffError` / `except Exception` arms and their `_record_intake(...)` calls stay
exactly where they are — the relocated `ValueError` is still raised from inside the same
outer `try`, so it's still caught by the generic `except Exception` arm and still produces a
`runs.jsonl` row with `reject_reason: internal_error`. Only `artifact_id`/`producing_loop`
values change for this specific rejection (see Assumptions).

`re` and `os` are already imported in `handoff.py` (used by the existing check and by
`FACTORY_MANIFEST_LABEL`'s own definition), so no new imports are needed.

## Alternatives Considered

- **Keep exact/suffix match, add a separate anchored regex per denied shape.** Rejected:
  strictly more code than a containment check for the same result, and the issue's own
  suggested fix (`"ready-for-agent" in label_folded or "-pending-review" in label_folded`) is
  already a plain substring test — no anchoring semantics are needed once the check is
  containment-based rather than shape-based.
- **Read `DIRECT_TO_PR_LABEL` via a shared config-loading helper instead of a bare env var.**
  Rejected per Q&A: `handoff.py` has no existing `config.yaml`-reading code path, and
  introducing one for a single value is more surface than an XS ticket needs when the bare-env
  pattern (already used for `FACTORY_MANIFEST_LABEL`) covers the case.
- **Also anchor the scheduler's `grep -qi` matchers so the guard's looser semantics stop
  mattering at the source.** Rejected per Q&A: gate-adjacent dispatch-behavior change,
  out of this ticket's XS scope, with its own blast radius across multiple call sites.
- **Broaden the deny-list to every non-`manifest-intake` label used anywhere in the pipeline**
  (`needs-discussion`, `epic`, `above-ceiling`, `no-autopilot`, `scope-spillover`, ...).
  Rejected: those are skip/advisory labels, not escalations; #381 already scoped the deny-list
  to labels whose presence changes dispatch *behavior* dangerously, and this ticket only adds
  the one Gate-3 flagged as a wider escalation than what's already denied (`direct-to-pr`).

## Open Questions (Non-blocking)

- `docs/adapter-authoring-guide.md`'s `internal_error` row (line 266) already describes this
  reject reason generically ("a malformed `FACTORY_MANIFEST_LABEL` override") and needs no
  wording change; flagging here only so the implementer doesn't feel obligated to touch it.

## Assumptions

- Moving the validation earlier changes what `_record_intake`'s audit row records for *this*
  rejection path only: `artifact_id` will be `"unknown"` and `producing_loop`/`origin` will
  reflect the pre-`read_manifest` defaults (`origin: factory`) instead of the real manifest's
  values, because the check now runs before the manifest is read. This is an accepted,
  intentional trade — the whole point of finding 3 is to avoid doing any manifest/verifier work
  for a rejection that depends only on env config — and existing tests for this rejection path
  (`test_intake_rejects_gate_shaped_manifest_label_override`,
  `test_intake_rejects_malformed_manifest_label_override_as_internal_error`) don't assert
  `artifact_id`/`origin`, so they hold unchanged.
- `os.environ.get("DIRECT_TO_PR_LABEL")` is read fresh inside `intake()` (not module-level, the
  way `FACTORY_MANIFEST_LABEL` is), since `FACTORY_MANIFEST_LABEL`'s tests already monkeypatch
  the module attribute directly rather than the env var, and a module-level
  `DIRECT_TO_PR_LABEL` constant would need the same monkeypatch-ability. Reading it inside the
  function keeps the new check test-friendly via `monkeypatch.setenv` without adding a second
  module-level env-cached constant with different reload semantics than the first.

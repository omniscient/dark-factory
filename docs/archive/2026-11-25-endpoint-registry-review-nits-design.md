# Endpoint registry review nits — secret-echo hardening, `show` redaction, CI drift fix

**Issue:** #594

## Overview / Problem Statement

#594 bundles six low-severity findings from an independent review of the #557 branch
("Model endpoint registry, capability evidence and endpoint preflight", head
`4611ca9`). That branch has since merged to `main` as PR #595, with four operator-review
fix commits on top. Rather than trust the issue's line numbers or assume every finding
is still live, each of the six was re-verified against current `main` by reproduction.
Two were not what they first looked like:

- One finding (the unhashable-mapping-key `TypeError`) is **already fixed** — no action.
- One fix surface (the duplicate-mapping-key error) **leaks a secret the same way the
  issue's named finding does, but the issue didn't name it** — found by re-deriving the
  fix from first principles instead of only patching the cited lines.

The remaining four findings are real, independently reproduced, and scoped below.

A seventh item, R7 (a file-descriptor leak in `_read_tree()`), was added by the operator
comment of 2026-11-25, after this spec was first written. Operator review also re-based
the citations: this spec was drafted against `848c6a4` (the PR #595 merge), and `main` has
since gained PR #596 (#593), which renamed and moved the code several findings point at.
Every file:line citation below is stated on main `9c28aa5`.

## Requirements

Numbered to match the issue's own finding order; R2 and R5 are "no action" findings
carried forward so they aren't silently dropped from the record. R7 is the operator's
additional nit, not one of the issue body's six. All citations are on main `9c28aa5`.

### R1 — `_parse_yaml()` must never echo document content in an error message

`scripts/factory_core/providers/model_endpoint.py:135-148` (`_parse_yaml`, on main
`9c28aa5`; #593 split the former `_read_yaml` into `_read_file` and `_parse_yaml`) already
strips the raw `problem_mark` snippet, but still interpolates `e.problem` (lines
147-148). For
`yaml.composer.ComposerError` (undefined/unresolved alias), PyYAML's `problem` text is
literally `f"found undefined alias {alias!r}"` — reproduced: a registry containing
`endpoints: [*sk-ant-SECRETVALUE]` raises `RegistryError` whose message contains
`sk-ant-SECRETVALUE` verbatim.

- Replace `e.problem` with a message built from a fixed-phrase table keyed on the
  exception's class (resolve via `type(e).__mro__` so subclasses match their most
  specific entry): `ScannerError` → "malformed token", `ParserError` → "syntax error",
  `ComposerError` → "undefined or invalid alias/anchor", `ConstructorError` →
  "unsupported value or tag", `ReaderError` → "invalid characters", any other
  `yaml.YAMLError` → "parse error". Message shape stays `"{path}: invalid YAML{where}:
  {phrase}"`, with `where` unchanged (the existing line-number-only snippet).
- `_construct_unique_mapping`'s own raises (lines 106-120) may keep more specific
  phrasing, but only by exact-equality comparison of `e.problem`/the raised text
  against hardcoded literals this module controls — equality against a literal can
  never echo document text, unlike interpolating the exception's own message.
- **Additional leak, not named in the issue:** the duplicate-key branch (line 118)
  raises `f"duplicate key {key!r}"`, which echoes the key itself — same threat model
  as the alias leak, at key position instead of value position (e.g. a secret pasted
  as a YAML mapping key). Fix it the same way: drop the key from the message, rely on
  the line number alone.
- This covers `capability-evidence/*.yaml` parsing by construction — both
  `_parse_registry` (line 281) and `_parse_evidence` (line 461) call the same
  `_parse_yaml`.
- Breaks one existing test: `tests/test_provider_model_endpoint.py:137-142`
  (`test_duplicate_yaml_key_fails_instead_of_overwriting`) asserts
  `"duplicate key 'endpoints'" in exc.value.problems[0]` — update it to assert the new
  fixed phrase, the line number, and the absence of `'endpoints'` from the message.
- Add regression tests (TDD) for: an undefined alias with a secret-like name, a
  duplicate key that looks like a secret, and the unhashable-key case — each asserting
  the secret-like text is absent from `RegistryError.problems` and the line number is
  present.
- Out of scope for this ticket: validator messages at `:165`, `:222`, `:300` also echo
  file text; recorded here for a later decision, not changed by this ticket.

### R2 — Unhashable mapping key: already fixed, no action

The issue's finding (`:106-107`, an uncaught `TypeError` on a mapping key like `? [a,
b]\n: 1`) does not reproduce on current `main`. `_construct_unique_mapping`
(`model_endpoint.py:106-120`) already wraps the `key in seen` hashability test in
`try`/`except TypeError`, converting it to a clean `yaml.constructor.ConstructorError`
("found unhashable key") with no traceback. Reproduced via direct test: raises
`RegistryError(['...: invalid YAML at line 1: found unhashable key'])`. This must
already have shipped as part of #595 itself (none of the four visible operator-review
commits touch this function), so the review that filed #594 likely ran against a
slightly earlier branch state. Carried forward here so it isn't dropped silently; no
code change.

### R3 — `redacted_view()` must allowlist every level it renders, not just the top level

`redacted_view()` (`model_endpoint.py:714-721`) and `redacted_view_staged()`
(`:724-740`) both delegate to `_redact()` (`:743-753`), which builds its per-endpoint
view (line 747) as `{k: v for k, v in entry.items() if k != "secret_reference"}` — a
blanket copy of whatever the loader didn't reject. On a newer-minor registry file, `_unknown_keys` tolerates instead
of rejecting unknown keys (`_parse_registry`'s `strict = version[1] <= SUPPORTED_MINOR`, line 289),
so a key like a future `api_key:` survives validation and is echoed verbatim by
`model-endpoints show`. Reproduced at the top level, and **also reproduced at every
nested level `_unknown_keys` tolerates**: an unknown key placed inside `model_identity`
or inside `timeouts` on a `schema_version: "1.1"` file passes through `redacted_view()`
unchanged in both positions — a top-level-only fix would still leak those.

- Rebuild `view` in `_redact()` (shared by `redacted_view()` and
  `redacted_view_staged()`) as an explicit allowlist at every nesting level:
  `_ENTRY_KEYS` for the entry (with `secret_reference` still reduced to its `{name,
  present}` form), `_MODEL_IDENTITY_KEYS` for `model_identity`, `TIMEOUT_FIELDS` for
  `timeouts`.
- Don't drop tolerated unknown keys silently: add a count-only field,
  `"unknown_keys_omitted": N`, totalled across the entry and its nested dicts, included
  only when `N > 0` (so today's output shape is unchanged when there's nothing to
  report). Never name or echo the omitted keys — same threat model as R1's key leak.
- Add regression tests in `tests/test_provider_model_endpoint.py` (next to
  `test_redacted_view_reduces_secret_reference_to_name_and_presence`, line 786)
  covering a secret-like unknown key at the entry level, inside `model_identity`, and
  inside `timeouts`. Add one CLI-level assertion in `tests/test_provider_cli.py` that
  the sentinel never appears in `model-endpoints show` stdout. Tests cover both the
  `--from-source` and staged paths; `test_redacted_view_staged_matches_the_source_view`
  stays green.

### R4 — Credential-env spec gap: documentation amendment only, no code change

`_local_problems()` (`model_endpoint.py:581-613`) checks `CLOUD_CREDENTIALS` (API keys
only: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`) plus
`ANTHROPIC_BASE_URL`/tier-alias equality. It never checks `CLAUDE_CODE_USE_BEDROCK` /
`CLAUDE_CODE_USE_VERTEX` (which reroute Claude Code to a different provider path
entirely, bypassing `ANTHROPIC_BASE_URL`), nor the broader AWS alternate-auth surface
`scripts/factory_core/providers/model.py` already recognizes for its own Bedrock
preflight (`_AWS_ALT_AUTH_SIGNALS`, lines 34-38: `AWS_PROFILE`, `AWS_ROLE_ARN`,
`AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`/`FULL_URI`,
`AWS_SESSION_TOKEN`, `AWS_EXECUTION_ENV`), nor GCP application-default-credentials file
resolution.

This gap is **currently inert**: `_local_problems()` always appends `_NO_LOCAL_DISPATCH`
(line 612) and `scheduler_preflight()` (lines 649-659, now returning `(problems,
hosting)`) rejects every `local` entry unconditionally — no dispatch exercises the `local` hosting path yet (Requirement 11),
so there is no way to test a wider guard end-to-end in this ticket, and it masks
nothing in production today.

- **No code change in this ticket.** `CLOUD_CREDENTIALS`/the environment guard
  implement contract §4.3; widening what counts as a cloud-credential signal is
  gate-adjacent, and `CLAUDE.md` reserves gate changes for their own reviewed ticket
  even when the change is strictly tightening.
- Amend `docs/configuration.md`'s model-endpoint-registry section (prose around lines
  545-548, "A local selection also fails if...") to name the three uncovered bypass
  vectors: `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX`, the AWS alternate-auth
  signals `scripts/factory_core/providers/model.py`'s `_AWS_ALT_AUTH_SIGNALS` (lines
  34-38) already enumerates, and GCP application-default-credentials resolution (compare
  `_GCP_ALT_AUTH_SIGNALS`, `model.py` lines 39-42) — and state that closing this gap is a
  precondition of #587 (the first local-eligible dispatch), which must widen the guard
  by mirroring `_AWS_ALT_AUTH_SIGNALS` (not copy-pasting it) with its own TDD coverage.
  Also record that an empty `invalidated_by` is accepted (`model_endpoint.py:506-508`;
  only `fixture_suite_version` is then compared, `:540-542`) and must be decided in #587.
  In the `docs/configuration.md` prose, cite `scripts/factory_core/providers/model.py`
  by its full repo path so `scripts/docs_refs_check.py` passes.
- Do not edit the archived design doc
  (`docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md`) — it's a
  historical record of what #557 shipped, not a living spec.

### R5 — `docs_refs_check.py` vs. the archived design doc: confirmed, accepted as-is, no action

Reproduced: running `python3 scripts/docs_refs_check.py
docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md` directly fails
with `line 109: 'deploy/instances/dark-factory/model-registry/': no such file or
directory in the repo` — the design doc's own prose at that line says "This repo ships
no real file here," because that path is operator-created at deploy time and never
present in a checkout. CI's actual invocation
(`.github/workflows/ci.yml:63`) only ever checks `docs/run-lifecycle.md
docs/scheduler.md docs/features.md docs/configuration.md` — confirmed passing — so this
never fires today. `docs/configuration.md:537` names the same kind of path but as
`deploy/instances/<instance>/model-registry/...`, which the checker's path regex
doesn't match as a literal path, so there's no equivalent false positive in a doc CI
actually checks.

No repo change: neither `docs_refs_check.py` nor `ci.yml` should gain an
exemption/new invocation to cover archived docs — that would widen a deterministic
check's surface to cover content `CLAUDE.md` treats as frozen and historical
(`docs/archive/` — "never archive a doc that tests or README still reference," which
implies the reverse is fine: an archived doc isn't expected to stay check-clean
forever).

### R6 — Wire `tests/test_scheduler.sh` into CI, after fixing the drift it already has

`.github/workflows/ci.yml` runs `tests/test_scheduler_lib.sh` (line 52) but never
`tests/test_scheduler.sh` itself — confirmed, matching the issue's finding that nothing
in CI exercises `dispatch()` with the #557 staging call. Running it directly
(`bash tests/test_scheduler.sh`) today surfaces **309 passed, 2 failed**, and the 2
failures are a real, pre-existing drift bug unrelated to #557 — direct evidence for the
issue's own underlying claim (zero CI coverage lets drift accumulate silently; it
already has):

- Section G ("Spec auto-advance", `tests/test_scheduler.sh:461`) and section I ("Plan
  auto-advance", line 566) assert against bare `${STATUS_REFINED}` /
  `${STATUS_READY}` shell variables that are not defined anywhere in the test file or
  any script it sources.
- `scheduler.sh`'s `spec_advance_check`/`plan_advance_check` (lines 277, 318) use
  `$FACTORY_STATUS_REFINED` / `$FACTORY_STATUS_READY` instead — exported with defaults
  by `scripts/identity.sh:8,14` — the `FACTORY_STATUS_*` naming is the
  tracker-generalization convention visible in `scripts/factory_core/identity.py` and
  `scripts/factory_core/providers/tracker/jira.py`. The test's two references are
  simply stale, most likely left behind by that rename.
- Under `set -uo pipefail` (the test file's own header), each unset reference aborts
  that command substitution with `tests/test_scheduler.sh: line 461: STATUS_REFINED:
  unbound variable` (and line 566 for `STATUS_READY`), which the test's own `||
  echo 0` fallback never catches (word-expansion errors under `set -u` abort the
  subshell before the `||` can apply) — surfacing as `assert_eq` reporting an empty
  actual value rather than `0`.
- Section C3 (`tests/test_scheduler.sh:320-361` — the #557-specific model-registry
  staging assertions the issue calls out by name) already passes with no changes
  needed; the staging behavior itself has no bug.

Fix order matters — adding the file to CI before fixing the drift would turn CI red for
a reason unrelated to #557:

1. Replace `${STATUS_REFINED}` with `${FACTORY_STATUS_REFINED}` at
   `tests/test_scheduler.sh:461`, and `${STATUS_READY}` with
   `${FACTORY_STATUS_READY}` at line 566. The test already sources `scheduler.sh`
   (`SCHEDULER_SOURCE_ONLY=1 source "$SCHED"`, line 89), so both names are already in
   scope — no new sourcing needed.
2. Re-run `bash tests/test_scheduler.sh` from a clean shell with no `FACTORY_*`
   variables pre-exported and confirm every assertion passes (311/311), to verify it's
   hermetic on a bare CI runner before wiring it in.
3. Only then add `- run: bash tests/test_scheduler.sh` to `.github/workflows/ci.yml`,
   next to the existing `test_scheduler_lib.sh` line.

Scope is strictly these two variable references — no other refactor of sections G or
I, no other section touched. If step 2 surfaces any further failure that only
reproduces in a bare/CI-like shell, ship step 1 only, do NOT add the `ci.yml` line, and
file a ticket; never land a red or skipped CI step. Adding a `ci.yml` test step is in scope (it is
not `publish.yml`, and it only adds coverage — it weakens no gate).

### R7 — `_read_tree()` must close the descriptor when the opened path is not a regular file

Operator comment of 2026-11-25, from the review of PR #596 (#593). On main `9c28aa5`,
`model_endpoint.py:330-333` opens each file with `os.open(path, os.O_RDONLY |
os.O_NOFOLLOW | os.O_NONBLOCK)` and hands the fd to `os.fdopen` before the `os.fstat`
type check. If the path is swapped for a directory after the `is_file()` check (line
324), `os.fdopen(fd)` raises `IsADirectoryError` without taking ownership of the fd, so
one descriptor leaks per call. It fails closed (`read_verified_tree()` reports the tree
as unreadable) and only in short-lived CLI processes, which is why it was not fixed in
PR #596.

- Call `os.fstat(fd)` before `os.fdopen`, and close `fd` on any failure that happens
  before `os.fdopen` owns it. A non-regular fd raises the existing
  `RegistryError(not_regular)` ("... is not a regular file or directory") instead of a
  raw `IsADirectoryError`.
- Keep `os.open` + `os.fdopen` as the only read primitive — the #593 swap-and-swap-back
  hooks in `_serve_first_read` (`tests/test_provider_model_endpoint.py:629`) depend on
  it — and keep `O_NOFOLLOW | O_NONBLOCK` and the `MAX_FILE_BYTES` cap unchanged.
- Add a regression test (POSIX, fails against the current code) in
  `tests/test_provider_model_endpoint.py`: monkeypatch `os.open` so that one file of the
  tree returns a directory fd (the swap after `is_file()`), record that fd, and assert
  that `tree_sha256` raises `RegistryError` matching "not a regular file" and that
  `os.fstat(fd)` on the recorded fd then raises `OSError` (the fd was closed).

## Architecture / Approach

All seven items are independent, mechanical, file-local fixes with no shared abstraction
worth introducing:

- R1 and R3 both touch the same underlying principle (never let unvalidated input reach
  operator-visible output, whether a log message or a `show` command's JSON) but have
  no code in common — R1 is exception-message construction in `_parse_yaml`, R3 is view
  construction in `_redact`. Each fix is scoped to its own function.
- R4, R5, R6 are documentation/process fixes — no production code changes in R4 and R5;
  R6 touches only a test file and `ci.yml`.
- R7 is a descriptor-ownership fix local to `_read_tree`; it changes no digest, no
  message other than the swapped-for-a-directory case, and no caller.
- No new module, no new config, no new abstraction. TDD throughout per `CLAUDE.md`'s
  "Tests... TDD for behavior changes" convention: R1's, R3's and R7's new regression tests are
  written to fail against the current code, then the fix makes them pass.

## Alternatives Considered

- **R1 — keep `e.problem` but redact substrings that look like secrets (regex on the
  message text).** Rejected: a secret doesn't have a recognizable shape in general (the
  issue's example happens to look like an Anthropic key, but a YAML alias/key can be
  any string); only a format allowlist (never echoing unvalidated text at all) is a
  structural guarantee rather than a best-effort heuristic.
- **R3 — strip only `secret_reference`-shaped keys instead of allowlisting.** Rejected:
  the threat is unknown future keys (e.g. a hypothetical `api_key`), which by
  definition can't be named in a denylist today; an allowlist is the only approach that
  stays safe as the schema gains fields this loader doesn't yet know about.
- **R4 — widen `_local_problems()`'s guard now, even though inert.** Rejected per Q&A:
  no caller exercises the `local` path yet, so there's no way to test the wider guard
  end-to-end in this ticket, and the guard is gate-adjacent (contract §4.3) — tightening
  it deserves its own reviewed ticket, timed with #587 where it becomes testable.
- **R5 — add an exemption to `docs_refs_check.py` or add the archived doc to CI's
  invocation.** Rejected: either widens a deterministic check over content `CLAUDE.md`
  treats as frozen/historical; the simplest and most honest fix is "no fix" with the
  reasoning recorded.
- **R6 — rewrite `tests/test_scheduler.sh` more broadly while touching it.** Rejected:
  scope discipline (`CLAUDE.md`: "touch only what the plan lists"); the only in-scope
  defect is the two stale variable names, and widening the diff risks the conformance
  gate excising it as out-of-scope anyway.

- **R7 — keep `os.fdopen` first and close the fd in an `except` around it.** Rejected
  as the primary shape: checking `os.fstat(fd)` first also turns the swap into the
  module's own `RegistryError` rather than a raw `IsADirectoryError`; the close-on-failure
  guard is still kept for any other failure before `os.fdopen` owns the fd.

## Open Questions (non-blocking)

- R6 step 2 (clean-shell re-run) might surface environment-dependent CI failures this
  spec can't predict from a local run; if so, the implement phase ships step 1 only,
  does not add the `ci.yml` line, and files a separate ticket (decided in R6) rather than
  expanding this one.

## Assumptions

- R2's conclusion (already fixed) is based on the current state of `main` at the time
  of this refinement; if a future rebase of this ticket's branch reverts that fix for
  any reason, the reproduction steps in this spec's R2 section can re-verify it.
- "Fixed-phrase table keyed on exception class" (R1) assumes PyYAML's exception
  hierarchy (`ScannerError`, `ParserError`, `ComposerError`, `ConstructorError`,
  `ReaderError`, all under `yaml.YAMLError`) is stable across the installed PyYAML
  (CI and the Dockerfile install it unpinned); unknown classes fall back to "parse
  error", so a hierarchy change cannot reintroduce an echo. No version bump is in scope
  here.

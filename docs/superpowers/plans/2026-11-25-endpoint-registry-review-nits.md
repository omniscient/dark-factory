# Endpoint registry review nits — Implementation Plan

**Issue:** #594

**Spec:** `docs/superpowers/specs/2026-11-25-endpoint-registry-review-nits-design.md`

**Goal:** Close the low-severity findings from the #557 review: YAML error messages that echo
document text (R1), a `model-endpoints show` view that prints tolerated unknown keys
verbatim (R3), a descriptor leak in `_read_tree()` (R7), a documentation-only record of the
credential-env gap that #587 must close (R4), and stale variable names that keep
`tests/test_scheduler.sh` out of CI (R6). R2 and R5 need no change; Task 6 records them.

**Architecture:** File-local fixes with no new module, config or abstraction. R1 swaps the
interpolated `e.problem` in `_parse_yaml()` for a fixed phrase looked up by exception class.
R3 rebuilds `_redact()`'s per-entry view as an allowlist at all three levels `_unknown_keys`
tolerates. R7 checks the descriptor's type before `os.fdopen` and closes it on any failure
before `os.fdopen` owns it. R4 is one prose paragraph in `docs/configuration.md`. R6 renames
two variables and adds one `ci.yml` line.

**Tech Stack:** Python 3.12, PyYAML (CI installs it unpinned; verified on 6.0.4), pytest,
bash.

**Base:** every citation is on main `9c28aa5` (after PR #596 / #593). Main is now `01ae4ee`
(PR #597), which touches none of the cited files, so the numbers still hold. The `feat/issue-594-*`
branch forks from main, so the line numbers hold. If they have drifted, find each anchor by
the quoted code text, not by line number.

**Verified before writing:** every code block below was applied to a scratch checkout of
main `9c28aa5` and run. Without the source changes, the 12 new or changed pytest cases fail.
With them, `tests/test_provider_model_endpoint.py` + `tests/test_provider_cli.py` pass
(214 passed) and the full suite passes (3467 passed). With Task 5's fix,
`bash tests/test_scheduler.sh` under `env -i` passes 311/311 (rc 0). Without it, 309 pass
and 2 fail. `docs_refs_check.py` passes on CI's four docs after Task 4.

**Environment note:** CI runs `pip install pytest pyyaml aiohttp`. If the container's
`python3` has no pytest, make a throwaway venv outside the repo
(`python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -q pytest pyyaml aiohttp`) and use
`/tmp/venv/bin/python -m pytest` wherever this plan says `python -m pytest`. Do not commit the
venv.

---

## File Structure

| File | Change | Requirement |
|---|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | `_construct_unique_mapping` duplicate-key text; new `_YAML_ERROR_PHRASES`, `_OWN_YAML_PROBLEMS`, `_yaml_error_phrase()`; `_parse_yaml` message; `_read_tree` fd ownership; new `_known_keys()`; `_redact` allowlist | R1, R7, R3 |
| `tests/test_provider_model_endpoint.py` | update `test_duplicate_yaml_key_fails_instead_of_overwriting`; add YAML-echo, fd-leak and redaction regression tests | R1, R2 (regression lock), R7, R3 |
| `tests/test_provider_cli.py` | add one parametrized `model-endpoints show` test (staged and `--from-source`) | R3 |
| `docs/configuration.md` | one paragraph in "Model endpoint registry (#557)" | R4 |
| `tests/test_scheduler.sh` | lines 461, 566: `STATUS_REFINED`/`STATUS_READY` → `FACTORY_STATUS_REFINED`/`FACTORY_STATUS_READY` | R6 |
| `.github/workflows/ci.yml` | add `- run: bash tests/test_scheduler.sh` after the `test_scheduler_lib.sh` line | R6 |
| `tests/test_ci_covers_shell_tests.py` | delete the `"test_scheduler.sh"` entry from `CI_EXEMPT_SHELL_TESTS` (line 83) | R6 |

Do not touch any other file. Specifically: do not edit
`docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md` (R4/R5),
`scripts/docs_refs_check.py` (R5), `_local_problems()` or `CLOUD_CREDENTIALS` (R4: no code
change), the validator messages at `model_endpoint.py:165/222/300` (R1: recorded out of
scope), or any other section of `tests/test_scheduler.sh` (R6).

---

## Task 1: R1 — YAML parse errors never echo document text

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (test at lines 137-142; new tests directly after it)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (lines 106-120 `_construct_unique_mapping`, 135-148 `_parse_yaml`)

- [ ] **Step 1.1: Update the existing duplicate-key test to the new wording (failing first).**
  In `tests/test_provider_model_endpoint.py`, replace the last line of
  `test_duplicate_yaml_key_fails_instead_of_overwriting` (line 142):

  ```python
      assert "duplicate key 'endpoints'" in exc.value.problems[0]
  ```

  with:

  ```python
      assert exc.value.problems == [f"{tmp_path / me.REGISTRY_FILE}: invalid YAML at line 3: duplicate key"]
  ```

  This pins the fixed phrase and the line number, and because it is an exact equality it also
  shows `'endpoints'` is gone from the message.

- [ ] **Step 1.2: Add the secret-echo regression tests directly after that test.** Insert
  after `test_duplicate_yaml_key_fails_instead_of_overwriting` (before
  `test_cloud_entry_may_not_accept_local_only`):

  ```python
  @pytest.mark.parametrize("text, problem", [
      # An undefined alias: PyYAML's own message quotes the alias name.
      ('schema_version: "1.0"\nendpoints: [*sk-ant-SECRETVALUE]\n',
       "invalid YAML at line 2: undefined or invalid alias/anchor"),
      # A secret pasted as a mapping key, twice.
      ('schema_version: "1.0"\nsk-ant-SECRETVALUE: 1\nsk-ant-SECRETVALUE: 2\n',
       "invalid YAML at line 3: duplicate key"),
      # An unhashable (sequence) key: already a clean error on main, kept that way (R2).
      ('schema_version: "1.0"\n? [sk-ant-SECRETVALUE, b]\n: 1\n',
       "invalid YAML at line 2: found unhashable key"),
  ])
  def test_yaml_errors_never_echo_document_text(tmp_path, text, problem):
      (tmp_path / me.REGISTRY_FILE).write_text(text, encoding="utf-8")
      with pytest.raises(me.RegistryError) as exc:
          me.load_registry(tmp_path / me.REGISTRY_FILE)
      assert exc.value.problems == [f"{tmp_path / me.REGISTRY_FILE}: {problem}"]
      assert "SECRETVALUE" not in " ".join(exc.value.problems)


  @pytest.mark.parametrize("text, phrase", [
      ("a: [1\n", "syntax error"),
      ('a: "\\q"\n', "malformed token"),
      ("a: !!python/object:os.system x\n", "unsupported value or tag"),
      ("a: \x00\n", "invalid characters"),
  ])
  def test_yaml_errors_print_a_fixed_phrase_per_exception_class(tmp_path, text, phrase):
      with pytest.raises(me.RegistryError) as exc:
          me._parse_yaml(text.encode("utf-8"), "f.yaml")
      assert exc.value.problems[0].startswith("f.yaml: invalid YAML")
      assert exc.value.problems[0].endswith(f": {phrase}")
  ```

  The second test pins one phrase from each table entry the first test does not reach
  (`ParserError`, `ScannerError`, `ConstructorError` for an unknown tag, and `ReaderError`).
  `ReaderError` has no `problem_mark`, so its message has no `at line N`. That is why the
  second test checks a prefix and suffix rather than exact equality.

- [ ] **Step 1.3: Run the tests and confirm they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k "duplicate_yaml_key or yaml_errors"
  ```

  Expected: `7 failed, 1 passed`. The 7 failures are the updated duplicate-key test, the alias
  and duplicate-key cases (the message still contains `found undefined alias
  'sk-ant-SECRETVALUE'` / `duplicate key 'sk-ant-SECRETVALUE'`), and all four phrase cases. The
  one pass is the unhashable-key case, which R2 says is already fixed (a regression lock, not
  a fail-first test).

- [ ] **Step 1.4: Stop echoing the duplicate key.** In
  `scripts/factory_core/providers/model_endpoint.py`, `_construct_unique_mapping`, replace:

  ```python
          if duplicate:
              raise yaml.constructor.ConstructorError(
                  None, None, f"duplicate key {key!r}", key_node.start_mark)
  ```

  with:

  ```python
          if duplicate:
              # The key is never echoed: a secret pasted as a mapping key must not reach a log.
              raise yaml.constructor.ConstructorError(
                  None, None, "duplicate key", key_node.start_mark)
  ```

- [ ] **Step 1.5: Add the fixed-phrase table.** Insert immediately before
  `def _read_file(path) -> bytes:`:

  ```python
  # A YAMLError's own problem text can quote the document (an undefined alias's name), so only
  # a fixed phrase per exception class is ever printed. Most specific class wins (__mro__ order).
  _YAML_ERROR_PHRASES = {
      yaml.reader.ReaderError: "invalid characters",
      yaml.scanner.ScannerError: "malformed token",
      yaml.parser.ParserError: "syntax error",
      yaml.composer.ComposerError: "undefined or invalid alias/anchor",
      yaml.constructor.ConstructorError: "unsupported value or tag",
  }
  # _construct_unique_mapping's own problem texts: matched by equality, so they echo nothing.
  _OWN_YAML_PROBLEMS = ("found unhashable key", "duplicate key")


  def _yaml_error_phrase(e) -> str:
      problem = getattr(e, "problem", None)
      if problem in _OWN_YAML_PROBLEMS:
          return problem
      for cls in type(e).__mro__:
          if cls in _YAML_ERROR_PHRASES:
              return _YAML_ERROR_PHRASES[cls]
      return "parse error"
  ```

  The `_OWN_YAML_PROBLEMS` check uses tuple membership, which is equality against literals
  this module controls. It can never echo document text, as the spec's R1 requires. Any other
  `yaml.YAMLError` falls back to `"parse error"`.

- [ ] **Step 1.6: Use the phrase in `_parse_yaml`.** Replace the `except` body:

  ```python
      except yaml.YAMLError as e:
          # Never echo the offending line: a pasted secret would land in the log.
          mark = getattr(e, "problem_mark", None)
          line = f" at line {mark.line + 1}" if mark else ""
          problem = getattr(e, "problem", None) or "parse error"
          raise RegistryError([f"{where}: invalid YAML{line}: {problem}"]) from None
  ```

  with:

  ```python
      except yaml.YAMLError as e:
          # Never echo the offending line or the parser's own problem text: a pasted secret
          # would land in the log.
          mark = getattr(e, "problem_mark", None)
          line = f" at line {mark.line + 1}" if mark else ""
          raise RegistryError([f"{where}: invalid YAML{line}: {_yaml_error_phrase(e)}"]) from None
  ```

  The message keeps the shape `"{where}: invalid YAML{line}: {phrase}"`, and the
  line-number-only `line` snippet is unchanged. `_parse_registry` and `_parse_evidence` both
  call `_parse_yaml`, so capability-evidence parsing is covered too.

- [ ] **Step 1.7: Run the tests and confirm they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
  ```

  Expected: all pass, 0 failed.

- [ ] **Step 1.8: Commit.**

  ```bash
  git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
  git commit -m "fix(#594): YAML parse errors print a fixed phrase, never document text (R1)"
  ```

---

## Task 2: R7 — `_read_tree()` closes the descriptor when the path is not a regular file

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (new test before `test_tree_sha256_fails_closed_on_an_oversized_file`, line 242)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`_read_tree`, lines 330-334)

- [ ] **Step 2.1: Add the failing regression test.** Insert directly before
  `def test_tree_sha256_fails_closed_on_an_oversized_file(tmp_path):`:

  ```python
  def test_tree_sha256_closes_the_fd_when_a_file_is_swapped_for_a_directory(tmp_path, monkeypatch):
      # A path swapped for a directory after the is_file() check: os.fdopen() would raise
      # IsADirectoryError without closing the descriptor (#594 R7).
      reg = _write_registry(tmp_path / "reg", [LOCAL])
      swapped = tmp_path / "swapped-in"
      swapped.mkdir()
      real_os_open, opened = os.open, []

      def _os_open(path, flags, *args, **kwargs):
          if Path(path).name == me.REGISTRY_FILE:
              opened.append(real_os_open(swapped, os.O_RDONLY))
              return opened[-1]
          return real_os_open(path, flags, *args, **kwargs)

      monkeypatch.setattr(os, "open", _os_open)
      with pytest.raises(me.RegistryError, match="not a regular file"):
          me.tree_sha256(reg)
      # Nothing opens a file between the raise and this check, so the fd number is not reused.
      with pytest.raises(OSError):
          os.fstat(opened[0])
  ```

  This patches `os.open` itself, the same hook point `_serve_first_read` (line 629) uses,
  because `_read_tree` reaches it through the `os` module.

- [ ] **Step 2.2: Run the test and confirm it fails.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k swapped_for_a_directory
  ```

  Expected: `1 failed`, with `IsADirectoryError: [Errno 21] Is a directory` raised out of
  `os.fdopen` rather than `RegistryError`.

- [ ] **Step 2.3: Check the type before `os.fdopen`, and close the fd on any failure before
  `os.fdopen` owns it.** In `_read_tree`, replace:

  ```python
              fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
              with os.fdopen(fd, "rb") as f:
                  if not stat.S_ISREG(os.fstat(fd).st_mode):
                      raise RegistryError(not_regular)
                  data = f.read(MAX_FILE_BYTES + 1)
  ```

  with:

  ```python
              fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
              try:
                  # Check the type before os.fdopen: it raises on a directory fd without
                  # taking ownership of it, which would leak the descriptor.
                  if not stat.S_ISREG(os.fstat(fd).st_mode):
                      raise RegistryError(not_regular)
                  f = os.fdopen(fd, "rb")
              except BaseException:
                  os.close(fd)
                  raise
              with f:
                  data = f.read(MAX_FILE_BYTES + 1)
  ```

  `os.open` + `os.fdopen` stay the only read primitive. `O_NOFOLLOW | O_NONBLOCK`, the
  `MAX_FILE_BYTES + 1` read and the following `too_big` check do not change. A path swapped
  for a FIFO (the #593 case) still raises `RegistryError(not_regular)`, and its fd is now
  closed by the `except` rather than by the `with`.

- [ ] **Step 2.4: Run the tests and confirm they pass, including the #593 hooks.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q
  ```

  Expected: all pass, 0 failed. `test_preflight_uses_only_the_first_read_of_each_staged_file`,
  `test_swap_and_swap_back_cannot_change_the_endpoint_decision` and
  `test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry` must stay green.

- [ ] **Step 2.5: Commit.**

  ```bash
  git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
  git commit -m "fix(#594): _read_tree closes the fd when a file is swapped for a directory (R7)"
  ```

---

## Task 3: R3 — `model-endpoints show` allowlists every level it prints

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (new helper + 2 tests before `test_redacted_view_staged_matches_the_source_view`, line 797)
- Modify: `tests/test_provider_cli.py` (new test before `test_model_endpoints_stage_prints_staged_digest`, line 502)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (`_redact`, lines 743-753; new `_known_keys` above it)

- [ ] **Step 3.1: Add the failing unit tests (source and staged paths).** In
  `tests/test_provider_model_endpoint.py`, insert directly before
  `def test_redacted_view_staged_matches_the_source_view(tmp_path, monkeypatch):`:

  ```python
  def _newer_minor_registry_with_unknown_keys(root):
      """A schema 1.1 registry (unknown keys tolerated) whose local entry carries a secret-like
      unknown key at the entry level, inside model_identity and inside timeouts."""
      entry = _entry(LOCAL, api_key=SENTINEL)
      entry["model_identity"]["api_key"] = SENTINEL
      entry["timeouts"]["api_key"] = SENTINEL
      return _write_registry(root, [entry, CLOUD], schema_version="1.1")


  def test_redacted_view_omits_tolerated_unknown_keys_at_every_level(tmp_path):
      view = me.redacted_view(_newer_minor_registry_with_unknown_keys(tmp_path / "reg"))
      assert SENTINEL not in repr(view) and "api_key" not in repr(view)
      local, cloud = view["endpoints"]
      assert local["unknown_keys_omitted"] == 3
      assert local["model_identity"] == LOCAL["model_identity"]
      assert local["timeouts"] == LOCAL["timeouts"]
      assert "unknown_keys_omitted" not in cloud


  def test_redacted_view_staged_omits_tolerated_unknown_keys(tmp_path, monkeypatch):
      reg = _newer_minor_registry_with_unknown_keys(tmp_path / "staged")
      monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
      problems, view = me.redacted_view_staged(reg)
      assert problems == [] and view == me.redacted_view(reg)
      assert SENTINEL not in repr(view) and view["endpoints"][0]["unknown_keys_omitted"] == 3
  ```

  `_entry` deep-copies `LOCAL`, so the nested mutations never touch the module constant.
  `_stage` cannot be reused here because it always writes `schema_version: "1.0"`, which
  rejects unknown keys. The test computes the digest the same way `_stage` does.

- [ ] **Step 3.2: Add the failing CLI test (both `show` paths).** In
  `tests/test_provider_cli.py`, insert directly before
  `def test_model_endpoints_stage_prints_staged_digest(`:

  ```python
  @pytest.mark.parametrize("from_source", [False, True])
  def test_model_endpoints_show_never_prints_tolerated_unknown_keys(tmp_path, monkeypatch, capsys, from_source):
      import factory_core.providers.cli as cli_mod
      from factory_core.providers import model_endpoint
      staged = _staged_fixture(tmp_path, monkeypatch)
      registry = staged / "model-endpoints.yaml"
      registry.write_text(registry.read_text(encoding="utf-8")
                          .replace('schema_version: "1.0"', 'schema_version: "1.1"', 1)
                          .replace("    secret_reference: LOCAL_GATEWAY_TOKEN\n",
                                   "    secret_reference: LOCAL_GATEWAY_TOKEN\n"
                                   "    api_key: sk-sentinel-unknown-key\n", 1), encoding="utf-8")
      monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
      monkeypatch.setattr(model_endpoint, "SOURCE_REGISTRY_DIR", staged)
      argv = ["cli.py", "model-endpoints", "show"] + (["--from-source"] if from_source else [])
      monkeypatch.setattr(sys, "argv", argv)
      cli_mod.main()
      out = capsys.readouterr().out
      assert "sk-sentinel-unknown-key" not in out and "api_key" not in out
      view = {e["id"]: e for e in json.loads(out)["endpoints"]}
      assert view["local-same-host"]["unknown_keys_omitted"] == 1
  ```

  The first `secret_reference: LOCAL_GATEWAY_TOKEN` line in
  `tests/fixtures/model_registry/model-endpoints.yaml` belongs to `local-same-host`. The digest
  is recomputed after the edit, as in
  `test_model_endpoints_show_invalid_registry_exits_1_with_field_errors`.

- [ ] **Step 3.3: Run the tests and confirm they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q -k "unknown_keys"
  ```

  Expected: `4 failed`. Each failure shows the sentinel or `api_key` in the view, or a
  `KeyError: 'unknown_keys_omitted'`.

- [ ] **Step 3.4: Add `_known_keys` and rebuild `_redact` as an allowlist.** In
  `scripts/factory_core/providers/model_endpoint.py`, replace the whole of `_redact`:

  ```python
  def _redact(endpoints, path) -> dict:
      """The one place that decides what of a registry is safe to print."""
      views = []
      for entry in endpoints:
          view = {k: v for k, v in entry.items() if k != "secret_reference"}
          ref = entry["secret_reference"]
          view["secret_reference"] = {"name": ref, "present": bool(os.environ.get(ref))}
          if "base_url" in entry:
              view["base_url_scope"] = base_url_scope(entry["base_url"])
          views.append(view)
      return {"registry": str(path), "endpoints": views}
  ```

  with:

  ```python
  def _known_keys(obj, allowed) -> tuple[dict, int]:
      """obj's allowed keys, in obj's order, and how many other keys were left out."""
      known = {k: v for k, v in obj.items() if k in allowed}
      return known, len(obj) - len(known)


  def _redact(endpoints, path) -> dict:
      """The one place that decides what of a registry is safe to print."""
      views = []
      for entry in endpoints:
          # An allowlist at every level _unknown_keys tolerates: a newer-minor file's unknown
          # keys (a future api_key, say) are counted, never printed.
          view, omitted = _known_keys(entry, _ENTRY_KEYS)
          for name, allowed in (("model_identity", _MODEL_IDENTITY_KEYS), ("timeouts", TIMEOUT_FIELDS)):
              if isinstance(view.get(name), dict):
                  view[name], nested = _known_keys(view[name], allowed)
                  omitted += nested
          ref = view.pop("secret_reference")
          view["secret_reference"] = {"name": ref, "present": bool(os.environ.get(ref))}
          if "base_url" in entry:
              view["base_url_scope"] = base_url_scope(entry["base_url"])
          if omitted:
              view["unknown_keys_omitted"] = omitted
          views.append(view)
      return {"registry": str(path), "endpoints": views}
  ```

  Notes for the implementer:
  - `view.pop("secret_reference")` followed by re-insertion keeps today's key order, with
    `secret_reference` after the other entry keys. It also keeps today's `KeyError` semantics,
    because `_validate_entry` already requires the field.
  - `unknown_keys_omitted` is a count only, and it is present only when `N > 0`. A
    `schema_version: "1.0"` registry therefore prints exactly what it prints today. Never add
    the omitted key names.
  - `_redact` is shared by `redacted_view()` (`--from-source`) and `redacted_view_staged()`
    (staged), so both `show` paths are covered by this one change.

- [ ] **Step 3.5: Run the tests and confirm they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q
  ```

  Expected: `214 passed`, 0 failed. `test_redacted_view_reduces_secret_reference_to_name_and_presence`,
  `test_redacted_view_staged_matches_the_source_view` and
  `test_model_endpoints_show_redacts_secrets` stay green.

- [ ] **Step 3.6: Commit.**

  ```bash
  git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py tests/test_provider_cli.py
  git commit -m "fix(#594): model-endpoints show allowlists every level and counts unknown keys (R3)"
  ```

---

## Task 4: R4 — record the credential-env gap as a #587 precondition (docs only)

**Files:**
- Modify: `docs/configuration.md` ("Model endpoint registry (#557)" section, after the
  paragraph ending at line 548)

No test is needed for this documentation change. CI's `docs_refs_check.py` is the
deterministic check.

- [ ] **Step 4.1: Add the paragraph.** In `docs/configuration.md`, find the paragraph that
  ends:

  ```markdown
  not `tested`, and in every case until #587 ships a local-eligible dispatch.
  ```

  Insert this after it, with a blank line on each side and before the "Inspect the effective
  configuration..." paragraph:

  ```markdown
  Known gap, a precondition of #587 (#594): that cloud-credential check covers only the static
  credentials in `CLOUD_CREDENTIALS` (`scripts/factory_core/providers/model_endpoint.py`). It does
  not yet see three ways Claude Code reaches a cloud model without `ANTHROPIC_BASE_URL`:
  `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX`; the AWS alternate-auth signals
  `_AWS_ALT_AUTH_SIGNALS` in `scripts/factory_core/providers/model.py` already enumerates for the
  Bedrock preflight (`AWS_PROFILE`, `AWS_SESSION_TOKEN`, role and container credentials); and GCP
  application-default credentials (compare `_GCP_ALT_AUTH_SIGNALS` in the same file). This is
  inert today, because every local selection already fails, but #587 must widen the guard
  before the first local-eligible dispatch, mirroring `_AWS_ALT_AUTH_SIGNALS` rather than
  copying it, with its own tests. #587 must also decide whether a capability-evidence record
  may carry an empty `invalidated_by`: one is accepted today, and then only
  `fixture_suite_version` is compared.
  ```

  Both source files are cited by their full repo path so `docs_refs_check.py` resolves them.
  Do not change `_local_problems()`, `CLOUD_CREDENTIALS` or `_evidence_problems()` in this
  ticket. The paragraph cites no line numbers on purpose: a later un-gated `fix(review)`
  commit can shift ranges. Do not add any.

- [ ] **Step 4.2: Run the docs check exactly as CI does.**

  ```bash
  python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```

  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 4.3: Commit.**

  ```bash
  git add docs/configuration.md
  git commit -m "docs(#594): record the local credential-env gap as a #587 precondition (R4)"
  ```

---

## Task 5: R6 — fix `tests/test_scheduler.sh` drift, then wire it into CI

**Files:**
- Modify: `tests/test_scheduler.sh` (lines 461 and 566 only)
- Modify: `.github/workflows/ci.yml` (one line after `- run: bash tests/test_scheduler_lib.sh`, line 52)
- Modify: `tests/test_ci_covers_shell_tests.py` (line 83)

The order matters. If the file goes into CI before the fix, CI turns red for a reason
unrelated to #557.

- [ ] **Step 5.1: Confirm the current failure (the "failing test" for this task).**

  ```bash
  env -i PATH=/usr/bin:/bin HOME=/tmp bash tests/test_scheduler.sh > /tmp/sched.out 2>&1; echo "rc=$?"; tail -1 /tmp/sched.out; grep -E "unbound variable|FAIL:" /tmp/sched.out
  ```

  Expected: `rc=1`, `Results: 309 passed, 2 failed`, plus
  `STATUS_REFINED: unbound variable` / `FAIL: G2: advance: set_board_status REFINED` and
  `STATUS_READY: unbound variable` / `FAIL: I2: advance: set_board_status READY`. This takes
  about a minute.

- [ ] **Step 5.2: Rename the two stale references.** In `tests/test_scheduler.sh`:
  - line 461: `"1" "$(grep -c "set_board_status 20 ${STATUS_REFINED}" "$STUB_LOG" || echo 0)"`
    → `"1" "$(grep -c "set_board_status 20 ${FACTORY_STATUS_REFINED}" "$STUB_LOG" || echo 0)"`
  - line 566: `"1" "$(grep -c "set_board_status 40 ${STATUS_READY}" "$STUB_LOG" || echo 0)"`
    → `"1" "$(grep -c "set_board_status 40 ${FACTORY_STATUS_READY}" "$STUB_LOG" || echo 0)"`

  ```bash
  sed -i 's/\${STATUS_REFINED}/${FACTORY_STATUS_REFINED}/; s/\${STATUS_READY}/${FACTORY_STATUS_READY}/' tests/test_scheduler.sh
  git diff --stat tests/test_scheduler.sh
  ```

  Expected: `1 file changed, 2 insertions(+), 2 deletions(-)`. These are the only two
  occurrences (`grep -n 'STATUS_REFINED\|STATUS_READY' tests/test_scheduler.sh` shows exactly
  lines 461 and 566, both now `FACTORY_`-prefixed). Both names are already in scope because
  the test sources `scheduler.sh` (line 89), which sources `scripts/identity.sh`. Make no
  other edit to sections G or I.

- [ ] **Step 5.3: Re-run from a bare shell with no `FACTORY_*` exported.**

  ```bash
  env -i PATH=/usr/bin:/bin HOME=/tmp bash tests/test_scheduler.sh > /tmp/sched.out 2>&1; echo "rc=$?"; tail -1 /tmp/sched.out
  ```

  Expected: `rc=0` and `Results: 311 passed, 0 failed`.

  **Decision rule (spec R6):** if this bare-shell run shows any other failure, commit Step
  5.2 only and skip Step 5.4 (no `ci.yml` line). Then file a follow-up ticket with the
  failing output:

  ```bash
  { echo "Follow-up to #594 (R6): tests/test_scheduler.sh still fails in a bare shell, so it was not added to CI."; echo; echo '~~~'; tail -40 /tmp/sched.out; echo '~~~'; } > /tmp/r6-followup.md
  python3 scripts/factory_core/providers/cli.py tracker create \
    --title "test(scheduler): make tests/test_scheduler.sh pass in a bare shell before wiring it into CI (follow-up to #594)" \
    --body-file /tmp/r6-followup.md
  ```

  If that call fails, paste the same text into the PR body instead. Note the skip in the
  commit message and PR body. Never land a red or skipped CI step.

  The bare `PATH=/usr/bin:/bin` must still reach a `python3` with PyYAML and aiohttp, and `jq`, as it does
  in the factory image and on CI's `ubuntu-latest` runner. If `python3` resolves somewhere
  else in your environment, add that directory to `PATH`, but never pre-export any
  `FACTORY_*` variable.

- [ ] **Step 5.4: Add the CI step (only if Step 5.3 passed).** In
  `.github/workflows/ci.yml`, job `tests`, replace:

  ```yaml
        - run: bash tests/test_scheduler_lib.sh
  ```

  with:

  ```yaml
        - run: bash tests/test_scheduler_lib.sh
        - run: bash tests/test_scheduler.sh
  ```

  This position is after the existing `sudo install -d -m 777 /var/lib/dark-factory` step, so
  the section-C3 staging assertions have the same writable state dir as the other scheduler
  tests. Do not touch `.github/workflows/publish.yml`.

  In the same step delete the line
  `    "test_scheduler.sh": "fails: line 411: STATUS_REFINED: unbound variable",` from
  `CI_EXEMPT_SHELL_TESTS` in `tests/test_ci_covers_shell_tests.py`; `test_no_stale_exemptions`
  fails otherwise. If the decision rule fired, keep the entry and replace its reason with the
  new failure.

- [ ] **Step 5.5: Validate the workflow YAML still parses.**

  ```bash
  python3 -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']; print([s['run'] for s in jobs['tests']['steps'] if 'run' in s and 'test_scheduler' in s['run']])"
  ```

  Expected: `['bash tests/test_scheduler_lib.sh', 'bash tests/test_scheduler.sh']`

- [ ] **Step 5.6: Commit.**

  ```bash
  git add tests/test_scheduler.sh .github/workflows/ci.yml tests/test_ci_covers_shell_tests.py
  git commit -m "test(#594): fix stale STATUS_* names in test_scheduler.sh and run it in CI (R6)"
  ```

---

## Task 6: Full verification and R2/R5 record

**Files:** none modified.

- [ ] **Step 6.1: Full pytest suite as CI runs it.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q
  ```

  Expected: all pass, 0 failed (3471 passed, 2 skipped on main `01ae4ee` plus these changes).

- [ ] **Step 6.2: CI's workflow-DAG and docs checks.**

  ```bash
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```

  Expected: each exits 0, and the last prints `Docs reference check passed for 4 doc(s).`

- [ ] **Step 6.3: Re-confirm R2 (no action).** The `test_yaml_errors_never_echo_document_text`
  unhashable-key case passed both before and after Task 1. That is the reproduction showing
  `_construct_unique_mapping` already converts the `TypeError` into
  `invalid YAML at line N: found unhashable key`. Nothing to change.

- [ ] **Step 6.4: Re-confirm R5 (no action).** Do not edit `scripts/docs_refs_check.py`,
  `ci.yml`'s `docs_refs_check` line, or the archived design doc. Step 6.2's run is the
  evidence that CI's four-doc invocation passes.

- [ ] **Step 6.5: Scope check against main.**

  ```bash
  git diff --stat origin/main...HEAD
  ```

  Expected: only `scripts/factory_core/providers/model_endpoint.py`,
  `tests/test_provider_model_endpoint.py`, `tests/test_provider_cli.py`,
  `docs/configuration.md`, `tests/test_scheduler.sh`, `tests/test_ci_covers_shell_tests.py`
  and `.github/workflows/ci.yml`, plus the
  spec and plan artifacts the setup step transfers from the refine branch. If Step 5.3's
  decision rule fired, `ci.yml` will not be in the list. Use the three-dot form: main
  moves, and the two-dot form reports main's own later commits as out-of-scope hits.

- [ ] **Step 6.6: PR body notes.** List R1, R3, R4, R6 and R7 as fixed, and R2 and R5 as
  "confirmed, no change" with the one-line reason from the spec. Also state that the
  validator messages at `model_endpoint.py:165/222/300`, which echo file text, are recorded
  out of scope (R1).

# Preview-classifier fixture set (#563)

Inputs and oracle formats for the hosting shadow comparator (`bench/shadow_runner.py`,
`bench/compare_hosting_variants.py`; operator doc `bench/hosting_shadow.md`). Design:
`docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md`.

## The oracle question

Every label answers one question, pinned here and in `hosting_shadow_schema.ORACLE_QUESTION`:

> Would this changeset need live endpoint validation to catch a regression?

It is deliberately *not* the rubric's own categories. Label blind to every arm's output.
Correctness comes only from these human labels — never an LLM judge, reviewer agreement,
`outcome.score`, an ended turn, or agreement with the `rules_only` arm.

## Files

- `rubric.pinned.md` — the `classify-preview` node's prompt, frozen at the commit in its
  header. `rubric_sha` is the sha256 of the file's bytes. The runner never reads the live DAG.
- `fixtures.yaml` (inputs) — what a model may see: `fixture_id`, `provenance`
  (`historical | synthetic`), `split` (`dev | held_out`), `name_only`, `stat`, `title`,
  `labels`, `labels_source`, and `source` (historical only). `preview_enabled` is a set-level
  constant and must be `true` in v1. `fixture_set_digest` is the sha256 of the file's bytes.
- `oracle_labels.yaml` (labels) — `oracle_label`, `expected_fail_safe`, `oracle_rationale`,
  `labeled_by`, `labeled_at` per `fixture_id`. Only the comparator opens it; the runner has no
  argument that accepts it. `oracle_label_digest` is the sha256 of the file's bytes.

## Labelling rules

- A `historical` fixture's `labels` are the linked issue's labels as of the PR's creation
  (`labels_source: as_of_pr_created`) or empty (`dropped`) — never the labels it carries
  today, which would leak later state.
- `expected_fail_safe: true` only for empty, garbled or insufficient-evidence inputs, and it
  implies `oracle_label: true` (the prompt's own fail-safe line).
- Held-out cases (`split: held_out`) are hand-built synthetic adversarial and
  insufficient-evidence inputs with a written rationale: an empty changeset; a truncated or
  garbled changeset; a "documentation" label on a diff touching `backend/app/**`; near-miss
  paths (`docker-compose.preview.yml`, a `docs/` file beside `Dockerfile`); renames; a mixed
  skip-only/runtime diff; a prompt-injection title. Seal them — record the digests in a
  `registered` preregistration — before any arm runs; never use them to iterate on the prompt.
- The rubric is written in MarketHawk path shapes, so most real dark-factory diffs fall
  through to the fail-safe `true`. The synthetic set must deliberately cover the force-true
  and skip-only categories.

## `examples/`

Eight hand-built tooling fixtures (`ex-01`…`ex-08`, `labeled_by: example-not-an-oracle`).
They exercise the tooling and carry no evidential weight. The real fixture set, its labels and
the `registered` preregistration are committed by the operator follow-up named in
`bench/hosting_shadow.md`.

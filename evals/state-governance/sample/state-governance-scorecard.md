# Dark Factory State Governance Scorecard

Generated: 2026-08-22T00:00:00Z
Run: state-governance-sample-v1

**STATUS:** FAIL — **Score:** 80/100 — **Events evaluated:** 6

## Checks

| Check | Verdict | Violations |
|---|---|---|
| authority_monotonicity | PASS | 0 |
| scope_non_expansion | PASS | 0 |
| deletion_propagation | FAIL | 1 |
| provenance_preservation | PASS | 0 |
| rollback_traceability | PASS | 0 |

### deletion_propagation violations

- `memory:index-defect` / `evt-real-f`: act event reads entity as mutability.status=active after a tombstone/delete/quarantine event

# Evidence Confidence Ledger

The confidence ledger is deterministic and versioned as `evidence-weighted-v1`.
It is an explanation surface, not a replacement for the verifier's lifecycle
or causal-state rules.

For each Evidence dependency the engine stores:

`effective_weight = base_weight * trust * freshness * scope_match * directness * independence`

`EXCLUDED`, `SUPERSEDED`, and `INVALID` Evidence always have eligibility zero.
The default trust factors are `TRUSTED=1.0`, `UNREVIEWED=0.75`, and
`LOW_TRUST=0.5`. Assessment-specific factors are stored in the ledger, so a
future calculation version can be introduced without rewriting history.

Independent supporting and contradicting contributions use bounded aggregation:

`support = 1 - product(1 - contribution)`

`contradiction = 1 - product(1 - contribution)`

`computed_confidence = support * (1 - contradiction)`

An operator adjustment is never an overwrite. The requested value is capped at
`0` when no active support remains and at `0.5` when all active support is
`LOW_TRUST`. The snapshot stores computed, requested, effective values, the
cap, calculation version, reason, invalidated references, remaining support,
and every factor. `confidence_adjustments` is an immutable audit trail.

The API is:

- `GET /api/v1/cases/{case_id}/evidence-chain-impact`
- `POST /api/v1/cases/{case_id}/evidence-chain-confidence`

Agent dependency proposals are stored as `PROPOSED` edges and do not affect a
confidence result until accepted by the deterministic lifecycle path. Lifecycle
invalidated edges remain visible so operators can see which claims were broken.

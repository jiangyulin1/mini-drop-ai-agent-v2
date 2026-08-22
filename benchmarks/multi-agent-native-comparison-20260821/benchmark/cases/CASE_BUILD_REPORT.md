# Case Build Report

All 9 cases are derived from real upstream PRs and frozen in `benchmark/sources.lock.json`.

| Case | Source kind | Evidence count | Interactive | Notes |
|---|---:|---:|---|
| case-01 | SOURCE_REAL + SOURCE_DERIVED | 2 | no | Envoy CPU hotspot; bounded runtime cost |
| case-02 | SOURCE_REAL + SOURCE_DERIVED | 2 | no | Kubernetes lock contention |
| case-03 | SOURCE_REAL + SOURCE_DERIVED | 2 | no | OpenTelemetry Python retention |
| case-04 | SOURCE_REAL + SOURCE_DERIVED | 2 | no | Prometheus retained capacity |
| case-05 | SOURCE_REAL + SOURCE_DERIVED | 3 | no | Redis expiry starvation |
| case-06 | SOURCE_REAL + SOURCE_DERIVED | 2 | no | Kubernetes full sync / scale latency |
| case-07 | SOURCE_REAL + SOURCE_DERIVED | 2 | yes | Kubernetes uncertain revert / abstention |
| case-08 | SOURCE_REAL + SOURCE_DERIVED | 2 | yes | Grafana unverified fix / expert hint |
| case-09 | SOURCE_REAL + SOURCE_DERIVED | 3 | yes | Grafana workqueue identity / evidence governance |

## Quality checks

- Public packs contain no GitHub URL, PR number, commit SHA, Oracle mechanism or private path.
- Replay packs have integrity hashes matching public evidence index.
- C7/C8/C9 intervention packs exist and are state-triggered.
- Every case has at least 2 support evidence items and at least 1 counter/uncertainty signal.
- Derived values are frozen from real upstream patch hashes; no raw production collection is claimed.

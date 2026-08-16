# C8 public evaluation gate evidence (2026-08-17)

## Contract

`benchmarks/agent_beta/manifests/public-v3.json` defines thirteen mandatory executable scenarios.
The companion lock binds the prompt, source lock, manifest and runner using UTF-8 canonical text
hashes that normalize CRLF/CR to LF. Validation rejects scenario drift, a missing executable
runner, digest drift, or any external holdout status other than `AWAITING_EXTERNAL_HOLDOUT`.

Mandatory results have only `PASS` or `FAIL`; `PARTIAL`, `AWAITING` and `NOT_RUN` cannot satisfy the
gate. The independent blind holdout remains separately and honestly marked
`AWAITING_EXTERNAL_HOLDOUT`.

## Executed coverage

All M01–M13 mandatory scenarios passed in one public runner execution:

1. standalone Drop dispatch and analysis;
2. ANSWER_ONLY with zero side effects;
3. existing Artifact explanation and canonical Evidence lifecycle;
4. three Evidence → Wakeup → new-decision rounds;
5. PAUSE, RESUME, STOP and RETARGET;
6. Sidecar generation rotation plus Server/Worker crash-replay recovery;
7. homogeneous three-node fanout and heterogeneous Campaign;
8. Collector, Query and MCP provenance/lineage;
9. compound causal reasoning with downstream/distractor guards;
10. Evidence exclusion downgrading the conclusion;
11. independent Provider, Pi and MCP failure degradation;
12. real-browser refresh, offline reconnect, SSE replay and a real Server process restart;
13. current Candidate verification plus TTL/finally cleanup contracts.

The browser scenario used a temporary persistent SQLite database, a real Uvicorn backend, Vite and
Playwright Chromium. Two workspace tests passed before restart and one persistence/deduplication
test passed after terminating and restarting the backend. The runner removed its temporary
processes and database after the final liveness assertion.

Machine evidence is written to the ignored current-run file
`reports/implementation/agent-beta-public-v3.json`. It records all thirteen mandatory statuses as
`PASS` and the separate holdout status as `AWAITING_EXTERNAL_HOLDOUT`.

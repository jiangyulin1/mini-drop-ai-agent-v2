# Mini-Drop repository cleanup audit

Audit date: 2026-08-19 (Asia/Shanghai)

This report records the first low-risk repository cleanup. It distinguishes
generated output, obsolete documentation, active contracts, local runtime
state, and machine credentials. The cleanup deliberately avoids broad
`git clean` commands and preserves all pre-existing source modifications.

## Pre-cleanup verification

| Check | Result |
|---|---|
| Web unit tests | PASS: 23 files, 63 tests |
| Web lint | PASS |
| Web production build | PASS |
| Repository secret scan | PASS for tracked files |
| Registry consistency | PASS |
| Capability registry | PASS |
| Alembic migration/drift check | PASS |
| Python test suite in restricted sandbox | 957 passed, 5 skipped; 11 failed and 31 errored because local TCP/gRPC/HTTP bind is denied |

The Python failures all occur in test groups that bind `127.0.0.1`; the
restricted environment raises `PermissionError: [Errno 1] Operation not
permitted`. A sandbox-external rerun was requested, but the approval service
returned HTTP 503. Post-cleanup comparison therefore uses the same restricted
environment plus focused non-network contract tests.

## Deleted tracked documentation

| Path | Reason | Replacement or recovery |
|---|---|---|
| `docs/ai_agent_progress.md` | v5 progress snapshot; explicitly says its checked boxes are not a completion source | Machine-generated implementation state and Git history |
| `docs/ai_agent_progress_v6.md` | Fixed commit/test-number snapshot; declares another file as the state source | Machine-generated implementation state and Git history |
| `docs/ai_agent_stability_research.md` | One-time 2026-08-15 framework survey, referenced only by the obsolete v5 progress log | Current collector architecture sections 4, 5, and 9 |
| `docs/agent_runtime_scaffold.md` | 2026-08-13 implementation snapshot with no normative or runtime consumer | Current runtime/tool contracts and architecture baseline |
| `docs/rebuild_optimization.md` | One-time repository-copy memo referencing a different, obsolete checkout | Current repository and release runbook |
| `docs/architecture-v2.md` | Superseded target layout and stale test totals | `docs/ai_collector_architecture_and_migration_plan.md` |
| `docs/single_tenant_beta_delivery_guide.md` | Draft for the superseded rules/RCA/recovery desktop beta | Current collector architecture and release runbook |
| `docs/external_holdout_strict_runbook.md` | Machine-specific Windows/Hyper-V procedure tied to the old v5 contract digest | Future Collector Agent holdout runbook |
| `docs/verification/c6_browser_gate_20260817.md` | Dated verification snapshot, not a maintained contract | Git history and reproducible tests |
| `docs/verification/c7_candidate_deploy_gate_20260817.md` | Dated candidate/deployment snapshot | `docs/release-baseline-runbook.md` |
| `docs/verification/c8_public_eval_gate_20260817.md` | Dated legacy Agent Beta/RCA evaluation snapshot | Future Collector Agent evaluation reports |

## Deleted tracked generated output

| Path group | Count | Reason |
|---|---:|---|
| `reports/eval/**` | 159 files | Historical run plans, records, summaries, and scores; already ignored and reproducible, and not used by production or tests |
| `reports/pi-agent-eval/**` | 1 file | Generated runner output |
| `reports/strategy-matrix/**` | 2 files | Generated offline matrix output; current untracked live runs are preserved |

`reports/schemas/implementation-baseline.schema.json` and the current tracked
`reports/chaos-gym/live-cpu/` result are retained. The schema is an executable
contract; the chaos result is the current same-day live measurement rather than
an old evaluation archive.

## Deleted obsolete scripts

| Path | Reason |
|---|---|
| `scripts/build_demo_report.py` | Unreferenced customer-report generator that hard-coded the deleted historical GitHub Cases outputs and obsolete RCA/recovery claims |
| `scripts/fetch_demo_diags.py` | Unreferenced fetcher with four fixed 2026-08-06 Diagnosis IDs and an obsolete deployment endpoint |

The reusable `benchmarks/github_cases/` workload and runner are retained until
their fault injection and scenario definitions have been migrated to the new
Collector/Evidence benchmark.

## Audited local-only material

| Path group | Disposition and reason | Recoverability |
|---|---|---|
| `docs/cloud-lab-environment-guide.md` | Deleted: ignored document containing plaintext server credentials | Intentionally not retained; rotate the disclosed passwords |
| `reports/implementation/` | Cleanup pending: generated candidate archives, deployment receipts, staging data, and local gates | Recreated by release/evaluation scripts |
| `external-package/` | Cleanup pending: 2026-08-16 duplicate source snapshot used by the old external holdout flow | Recreated from a release candidate if needed |
| `mini_drop.db` | Cleanup pending: empty local SQLite runtime state at audit time | Recreated on startup/migration |
| Python/test/build caches | Cleanup pending: interpreter, coverage, pytest, Ruff, package metadata, and Web build output | Recreated by normal commands |

The exact local cleanup command was rejected before execution because the
automatic destructive-action approval service returned HTTP 503. These paths
are therefore listed honestly as pending; no broad or indirect deletion was
attempted.

The redacted cloud guide, `.claude/`, `ssh/`, and `hyperv-linux-access.md` are
machine-specific rather than repository content, but remain available because
they may still be needed for the active VM lab. They stay ignored and must not
be committed.

## Retained pending migration

The following are not current product truth, but deleting them now would either
break machine contracts or discard mechanisms that have not yet been extracted:

- `docs/ai_agent_feature_complete_demo_prompt_v6.md`: hash-locked by manifests,
  validators, tests, and candidate packaging.
- `docs/ai_agent_feature_complete_demo_prompt.md`: still named by the public
  contract and should move with the v6 contract migration.
- `docs/ai_agent_runtime_integration_plan.md`: contains detailed Evidence,
  ResourceRef, Outbox, lease, and fencing constraints that remain useful.
- `docs/v2_continuation_master_prompt_v1.md`: retains transaction, snapshot,
  Supervisor, and SSE invariants that must be extracted before removal.
- Legacy RCA/recovery design documents: they remain until the replacement
  Collector/Evidence path satisfies the deletion gates in the current
  architecture baseline.
- `web/node_modules/`, generated protobuf stubs, `.venv`, and Pi sidecar
  dependencies: active development/runtime inputs, not disposable clutter.

### Documentation migration queue

| Path | Why it looks obsolete | Why it is retained now |
|---|---|---|
| `docs/ai_diagnostic_agent_evolution_plan.md` | Historical draft superseded by the Collector baseline | It is a user-created discussion artifact and still records audit detail not yet reduced to the baseline |
| `docs/drop_ai_exploration_roadmap.md` | Old rules-first 12-week roadmap | Root environment documentation still links it; migrate the useful experiment governance first |
| `docs/autonomous_ops_agent_implementation_plan.md` | Automatic RCA/recovery is no longer the current product direction | Root README and authorization guide still link it; extract safety and verification mechanisms first |
| `docs/ai_agent_ux_design.md` | Self-declared historical UI note | The hash-locked v6 contract refers to it, so remove it with that contract migration |
| `docs/ai_feature_extension_design.md` | Superseded Case/RCA extension design | Still part of the old diagnosis-design cross-reference set |
| `docs/ai_design_traceability.md` | Fixed 2026-08-08 implementation matrix | Replace with Collector/Evidence C0-C3 traceability before deletion |
| `docs/ai_implementation_status.md` | Fixed 2026-08-10 completion snapshot | Still linked by the old design set; machine state should replace it |
| `docs/ai_diagnosis_agent_design.md` | Old rules/RCA design truth | Source comments and testset design still point to it; update those consumers during P4 |
| `docs/ai-feature-capability-and-design.md` | Product wording overlaps the new architecture baseline | Root README uses it for Provider/Pi configuration; move configuration before removal |
| `docs/vm_cluster_test_report.md` | Historical July/August deployment report | Retain until its useful EnvironmentProfile details are merged into the root cluster guide |
| `docs/external_holdout_api_reference.md` | Describes the old Diagnosis API | Replace with the Collector/Evidence benchmark API |
| `docs/external_holdout_runbook.md` | Old Agent Beta scoring/import flow | Preserve its external-signature trust method in the replacement runbook |
| `docs/cloud-lab-environment-guide_副本.md` | Ignored, redacted cloud-environment snapshot | May still be needed for the active cloud lab; move outside the repository when ownership is clear |
| `hyperv-linux-access.md` | Ignored machine-specific access guide | Contains current Mac-to-VM access operations not yet merged into the EnvironmentProfile |

### Source migration queue

| Path or group | Audit conclusion |
|---|---|
| `scripts/check_capability_registry.py` | Duplicate proxy for `check_registry_consistency.py`; remove after CI/docs stop calling the alias |
| `proto/compile.sh` | Duplicates the cross-platform Python compiler; update Makefile, `dev.py`, and worker installer first |
| `server/app/diagnosis/service_baseline.py` | Online orphan except for three fixed-threshold tests; remove with the legacy rules baseline |
| `server/app/repository.py` | Production orphan but still supplies the gRPC test fake; move the fake under `tests/` first |
| `benchmarks/github_cases/`, `benchmarks/ai_ops_v2/`, `benchmarks/lightweight_ai_eval/` | Old scoring paths, but their scenarios, fault harness, and comparison mechanics must seed the new benchmark |
| Legacy RCA and recovery modules | Still imported by app wiring, routes, persistence, tools, and migrations; deletion is blocked until P4 replacement and compatibility gates pass |

## Post-cleanup verification

| Check | Result |
|---|---|
| Non-network Python suite | PASS: 944 passed, 5 skipped |
| Cleanup-sensitive contract tests | PASS: 34 passed |
| Agent Beta contract/manifest/lock validator | PASS |
| Testset validator | PASS |
| Repository hygiene scan | PASS for tracked files |
| Registry and capability consistency | PASS |
| Alembic upgrade and schema drift | PASS |
| Web unit tests | PASS: 23 files, 63 tests |
| Web lint | PASS |
| Web production build | PASS |
| Residual deleted-document references | PASS: no consumer outside this audit report |
| Git whitespace check | PASS |

The non-network suite excludes the six files whose fixtures require local
TCP/gRPC/HTTP listeners: `test_agent_beta_cross_feature.py`,
`test_agent_runtime_local_loop.py`, `test_agent_runtime_turn_endpoint.py`,
`test_connection_probe.py`, `test_grpc_services.py`, and
`test_pi_runtime_adapter.py`. Before deletion, the restricted full run reached
957 passing assertions and failed only in those bind-dependent groups, so the
same-environment pre/post comparison shows no cleanup regression.

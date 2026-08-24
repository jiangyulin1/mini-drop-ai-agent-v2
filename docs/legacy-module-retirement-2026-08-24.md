# Legacy Module Retirement Record

Date: 2026-08-24  
Scope: backend cleanup after the Evidence-native Task -> Artifact -> CaseEvidence path became the showcase path.

## Decision

The default online product must not start the retired `DiagnosisSession` / rules-RCA workflow. This is a product-path retirement, not a bulk code or data purge: reusable collectors, parsers, evidence contracts, audit/fence primitives, evaluation fixtures and recovery governance are retained or migrated into the Evidence-native path.

The switch is centralized in `server/app/legacy_compat.py`:

```text
MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=1
```

The default is disabled.  A disabled write returns `410 LEGACY_DIAGNOSIS_DISABLED` and does not create a DiagnosisSession, Task, or legacy DiagnosisEvidence.  Historical reads remain available; reading a session no longer advances it while the switch is disabled.

## Current retirement actions

| Module or entry | Current action | Why it is retained | Final deletion gate |
|---|---|---|---|
| `POST /api/v1/diagnoses` | Disabled by default | Historical sessions, compatibility tests, offline evaluation | No supported legacy clients; export/retention job verified |
| `POST /api/v1/cases/{case_id}/diagnoses` | Disabled by default | Old Case data can still be inspected during migration | Evidence-native Agent turn/collection contract covers all callers |
| Diagnosis cancel/approval writes | Disabled by default | Historical audit semantics and migration tooling | Historical mutation window closed |
| Case correction/transition, fanout verification and Agent-turn legacy callbacks | No legacy mutation when disabled | Case state machine, fanout and Agent turn remain reusable | No historical Case retains an active DiagnosisSession |
| `POST /api/diagnoses/{id}/feedback` | Disabled by default | Historical RCA feedback export | Feedback migrated to Evidence-native review/analysis metrics |
| `GET /api/v1/diagnoses*`, `/api/diagnoses*` | Read-only compatibility | Existing history, artifact download and audit review | Historical data migrated and read contract removed |
| `DiagnosisEvidenceModel` and old Diagnosis store | Frozen compatibility | Existing rows, audit bundles and downloads | Backfill to canonical CaseEvidence plus rollback verification |
| `RulesOnlyReasoner`, `rca/candidates.py`, `rca/calibrator.py` | Offline/compatibility only; not a product tool | Benchmark baselines and old report comparisons | Evaluation baseline packaged and replacement scorer accepted |
| `domain_analyzers.py` contracts | Retained selectively | Deterministic report verification and evaluation reuse | Verifier/eval contracts moved to Evidence-native package |
| Collector parsers, Artifact storage, Evidence projection/review | Retained as canonical | Required by the target Task/Artifact/Evidence path | No deletion planned in this retirement |
| Recovery plan, Action Registry, verification, audit, fences | Retained as reusable governance | Future controlled recommendations/actions need these safety boundaries | Separate recovery retirement decision |
| `AutonomousIncidentAgent` old diagnosis callback | Disabled by default | Lease/fence and recovery code may be reused | New Supervisor owns all runtime steps and old callback has no callers |

## Reuse classification

The following classification is authoritative for cleanup. “Retire” means remove
from the default online path; it does not mean deleting a useful parser or data
contract merely because the file is located under `diagnosis/` or `rca/`.

| Legacy element | Classification | New-path treatment |
|---|---|---|
| `DiagnosisSessionModel`, old orchestrator state machine | Retire | Keep only until no compatibility caller remains; do not use as Case truth |
| `DiagnosisEvidenceModel` structured rows | Migrate/retire | Preserve fields that map to Task Artifact -> CaseEvidence; discard only duplicate legacy rows after migration |
| `DiagnosisEventModel`, node/run/outbox records | Retire online, preserve audit meaning | Map useful lifecycle events to CaseEvent/Agent Cycle/Outbox; do not copy the old scheduler |
| `DiagnosisRunModel` / old one-shot RCA reports | Retire online, keep benchmark input | Convert useful benchmark cases to Evidence-native evaluation fixtures |
| `DiagnosisToolResultModel` | Migrate selectively | Keep raw result only when it has Artifact hash/lineage; otherwise it is not a canonical Evidence source |
| `RepairPlanModel` | Retire old storage, reuse concept | Use Investigation Plan / Collection Proposal / Case Recovery Plan models |
| `RCAFeedbackModel` / `RCAFeedbackWeightModel` | Offline-only | Export labels for evaluation; do not feed global online root-cause priors |
| `domain_analyzers.py` deterministic contracts | Reuse | Keep parser/verifier contracts until Evidence-native verifier replacement exists |
| Collector registry, parsers, Artifact storage/download | Reuse unchanged | Remain the Task -> Artifact foundation |
| SourceGateway, authorization, capability tokens, redaction and budgets | Reuse unchanged | Remain the controlled external-read boundary |
| Evidence projection, review ledger, analysis runs, invalidation | Reuse unchanged | Remain canonical CaseEvidence governance |
| Supervisor leases, generation/revision fences, Action Registry, recovery verifier | Reuse unchanged | Remain runtime safety/governance primitives for future controlled actions |

Current local database inspection on 2026-08-24 found zero rows in the legacy
Diagnosis/RCA tables, so no historical record purge is required at this point.
The cleanup therefore focuses on code reachability and ownership boundaries.

## Progress

- [x] Central legacy gate added.
- [x] Independent Diagnosis creation is fail-closed by default.
- [x] Case Diagnosis creation is fail-closed by default.
- [x] Legacy cancel/approval writes are fail-closed by default.
- [x] Disabled historical reads do not advance sessions.
- [x] Autonomous old callback reports compatibility-disabled without creating a session.
- [x] Regression tests cover no-mutation behavior and explicit compatibility opt-in.
- [ ] Migrate Case proposals/understanding to canonical Workspace-only projections.
- [ ] Move remaining RCA imports out of online runtime; retain benchmark package.
- [ ] Complete selective DiagnosisEvidence field migration and deletion review.

## Reuse boundary

Do not delete `case_evidence`, `evidence_projection`, `evidence_analysis`, `investigation_state`, `collection_supervisor`, `plan_driver`, `artifact_service`, collector parsers, report verification, authorization, Action Registry, recovery verification, outbox/wakeup and generation/revision fences.  These modules either implement the target path or provide reusable governance and integrity controls.

This record is the change log for subsequent cleanup.  A module may be physically removed only after its deletion gate is recorded as complete and the full backend suite plus migration drift checks pass.

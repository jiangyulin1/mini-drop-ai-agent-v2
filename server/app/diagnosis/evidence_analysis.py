"""Evidence-bound AI analysis lifecycle and citation verification."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_FIELD_PATH_PATTERN = re.compile(
    r"^[^.\[\]]+(?:(?:\.[^.\[\]]+)|(?:\[\d+\]))*$"
)


class EvidenceAnalysisService:
    def __init__(self, repository: Any):
        self._repo = repository

    def create_run(
        self,
        *,
        case_id: str,
        tenant_id: str,
        evidence_ids: list[str],
        mode: str = "SINGLE",
        model_config_id: str | None = None,
        prompt_version: str = "evidence-analysis.v1",
        explicit_single: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"SINGLE", "MULTI", "COMPARE"}:
            raise ValueError("INVALID_ANALYSIS_MODE")
        if not evidence_ids or (mode == "SINGLE" and len(evidence_ids) != 1):
            raise ValueError("INVALID_EVIDENCE_INPUT_COUNT")
        if self._repo.get_incident_case(case_id, tenant_id) is None:
            raise ValueError("CASE_NOT_FOUND")
        inputs: list[dict[str, Any]] = []
        excluded = False
        for evidence_id in evidence_ids:
            evidence = self._repo.get_case_evidence(case_id, tenant_id, evidence_id)
            if evidence is None:
                raise ValueError(f"EVIDENCE_NOT_FOUND:{evidence_id}")
            status = str(evidence.get("status") or "ACTIVE")
            if status == "EXCLUDED":
                if not (explicit_single and mode == "SINGLE"):
                    raise ValueError(f"EVIDENCE_EXCLUDED:{evidence_id}")
                excluded = True
            projections = self._repo.list_evidence_projections(case_id, tenant_id, evidence_id)
            if not projections:
                raise ValueError(f"EVIDENCE_PROJECTION_NOT_FOUND:{evidence_id}")
            latest = projections[-1]
            reviews = self._repo.list_evidence_reviews(case_id, tenant_id, evidence_id=evidence_id)
            inputs.append({
                "evidence_id": evidence_id,
                "review_revision": len(reviews),
                "review_state": status,
                "projection_id": latest["projection_id"],
                "projection_hash": latest["projection_hash"],
            })
        canonical_inputs = sorted(inputs, key=lambda item: str(item["evidence_id"]))
        input_fingerprint = self._input_fingerprint(
            mode=mode,
            evidence_inputs=canonical_inputs,
            model_config_id=model_config_id,
            prompt_version=prompt_version,
        )
        return self._repo.create_evidence_analysis_run(
            case_id=case_id, tenant_id=tenant_id, mode=mode,
            evidence_inputs=canonical_inputs, model_config_id=model_config_id,
            prompt_version=prompt_version,
            input_state="EXCLUDED_INPUT" if excluded else "CURRENT",
            input_fingerprint=input_fingerprint,
        )

    def get_run(self, analysis_run_id: str, case_id: str, tenant_id: str) -> dict[str, Any] | None:
        return self._repo.get_evidence_analysis_run(analysis_run_id, case_id, tenant_id)

    def list_runs(
        self, case_id: str, tenant_id: str, *, evidence_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._repo.list_evidence_analysis_runs(
            case_id, tenant_id, evidence_id=evidence_id,
        )

    def complete_run(
        self,
        *,
        analysis_run_id: str,
        case_id: str,
        tenant_id: str,
        facts: list[dict[str, Any]],
        anomalies: list[dict[str, Any]] | None = None,
        interpretations: list[dict[str, Any]] | None = None,
        conflicts: list[dict[str, Any]] | None = None,
        limitations: list[str] | None = None,
        next_collection_proposals: list[dict[str, Any]] | None = None,
        token_usage: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        run = self._repo.get_evidence_analysis_run(analysis_run_id, case_id, tenant_id)
        if run is None:
            raise ValueError("ANALYSIS_RUN_NOT_FOUND")
        if run.get("status") not in {"QUEUED", "RUNNING"}:
            raise ValueError("ANALYSIS_RUN_NOT_COMPLETABLE")
        stale_reasons = self._stale_input_reasons(run, case_id, tenant_id)
        if stale_reasons:
            self._repo.invalidate_evidence_analysis_run(
                analysis_run_id, case_id, tenant_id, input_state="STALE_INPUT",
            )
            raise ValueError("ANALYSIS_INPUT_STALE:" + ",".join(stale_reasons))
        input_by_id = {item["evidence_id"]: item for item in run.get("evidence_inputs") or []}
        projection_by_hash: dict[str, dict[str, Any]] = {}
        for evidence_id in input_by_id:
            for projection in self._repo.list_evidence_projections(case_id, tenant_id, evidence_id):
                projection_by_hash[projection["projection_hash"]] = projection
        errors: list[str] = []
        for index, fact in enumerate(facts or []):
            claim = str(fact.get("claim") or "").strip()
            citations = fact.get("citations") or []
            if not claim:
                errors.append(f"FACT_{index}_CLAIM_REQUIRED")
            if not citations:
                errors.append(f"FACT_{index}_CITATION_REQUIRED")
            for citation_index, citation in enumerate(citations):
                error = self._validate_citation(citation, input_by_id, projection_by_hash)
                if error:
                    errors.append(f"FACT_{index}_CITATION_{citation_index}_{error}")
            if str(fact.get("certainty") or "").upper() == "HIGH" and citations:
                states = {
                    input_by_id.get(str(item.get("evidence_id") or ""), {}).get("review_state")
                    for item in citations
                }
                if states and states <= {"LOW_TRUST"}:
                    errors.append(f"FACT_{index}_LOW_TRUST_CANNOT_SOLELY_SUPPORT_HIGH_CERTAINTY")
        if errors:
            raise ValueError("INVALID_ANALYSIS_OUTPUT:" + ",".join(errors))
        result = self._repo.complete_evidence_analysis_run(
            analysis_run_id, case_id=case_id, tenant_id=tenant_id,
            expected_input_fingerprint=run.get("input_fingerprint"),
            facts=facts or [], anomalies=anomalies or [],
            interpretations=interpretations or [], conflicts=conflicts or [],
            limitations=limitations or [], next_collection_proposals=next_collection_proposals or [],
            token_usage=token_usage or {}, latency_ms=latency_ms, status="COMPLETED",
        )
        if result is None:
            raise ValueError("ANALYSIS_RUN_NOT_FOUND")
        return result

    def _stale_input_reasons(
        self, run: dict[str, Any], case_id: str, tenant_id: str,
    ) -> list[str]:
        reasons: list[str] = []
        for pinned in run.get("evidence_inputs") or []:
            evidence_id = str(pinned.get("evidence_id") or "")
            evidence = self._repo.get_case_evidence(case_id, tenant_id, evidence_id)
            if evidence is None:
                reasons.append(f"EVIDENCE_NOT_FOUND:{evidence_id}")
                continue
            current_state = str(evidence.get("status") or "ACTIVE")
            if current_state != str(pinned.get("review_state") or "ACTIVE"):
                reasons.append(f"REVIEW_STATE_CHANGED:{evidence_id}")
            reviews = self._repo.list_evidence_reviews(
                case_id, tenant_id, evidence_id=evidence_id,
            )
            current_revision = max(
                (int(item.get("review_revision") or 0) for item in reviews),
                default=0,
            )
            if current_revision != int(pinned.get("review_revision") or 0):
                reasons.append(f"REVIEW_REVISION_CHANGED:{evidence_id}")
            projections = self._repo.list_evidence_projections(
                case_id, tenant_id, evidence_id,
            )
            current_hash = projections[-1].get("projection_hash") if projections else None
            if current_hash != pinned.get("projection_hash"):
                reasons.append(f"PROJECTION_CHANGED:{evidence_id}")
        return reasons

    @staticmethod
    def _input_fingerprint(
        *, mode: str, evidence_inputs: list[dict[str, Any]],
        model_config_id: str | None, prompt_version: str,
    ) -> str:
        canonical = json.dumps(
            {
                "mode": mode,
                "evidence_inputs": evidence_inputs,
                "model_config_id": model_config_id,
                "prompt_version": prompt_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _field_path_segments(field_path: str) -> list[str | int] | None:
        normalized = field_path.strip()
        if normalized.startswith("projection."):
            normalized = normalized[len("projection."):]
        if not normalized or _FIELD_PATH_PATTERN.fullmatch(normalized) is None:
            return None
        segments: list[str | int] = []
        for key, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", normalized):
            segments.append(int(index) if index else key)
        return segments

    @staticmethod
    def _validate_citation(
        citation: dict[str, Any],
        input_by_id: dict[str, dict[str, Any]],
        projection_by_hash: dict[str, dict[str, Any]],
    ) -> str | None:
        evidence_id = str(citation.get("evidence_id") or "")
        projection_hash = str(citation.get("projection_hash") or "")
        input_ref = input_by_id.get(evidence_id)
        if input_ref is None:
            return "EVIDENCE_NOT_IN_INPUT"
        if input_ref.get("projection_hash") != projection_hash:
            return "PROJECTION_HASH_NOT_CURRENT_INPUT"
        projection = projection_by_hash.get(projection_hash)
        if projection is None:
            return "PROJECTION_NOT_FOUND"
        field_path = str(citation.get("field_path") or "").strip()
        if not field_path:
            return "FIELD_PATH_REQUIRED"
        segments = EvidenceAnalysisService._field_path_segments(field_path)
        if segments is None:
            return "FIELD_PATH_INVALID"
        value: Any = projection.get("content") or {}
        for segment in segments:
            if isinstance(value, dict) and segment in value:
                value = value[segment]
            elif isinstance(value, list) and isinstance(segment, int) and segment < len(value):
                value = value[segment]
            elif isinstance(value, list) and isinstance(segment, str) and segment.isdigit() and int(segment) < len(value):
                value = value[int(segment)]
            else:
                return "FIELD_PATH_NOT_FOUND"
        quote = citation.get("quote")
        if quote is not None:
            rendered = value if isinstance(value, str) else str(value)
            start = citation.get("start")
            end = citation.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
                return "INVALID_SPAN"
            if rendered[start:end] != str(quote):
                return "QUOTE_SPAN_MISMATCH"
        return None

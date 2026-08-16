"""Diagnosis, evidence, source, and runtime HTTP endpoints."""

from __future__ import annotations

import hashlib
import io
import json as _json
import os
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as _url_quote

from fastapi import APIRouter, HTTPException, Request, Response

from server.app.agent_runtime.config import agent_flags
from server.app.agent_runtime.dispatcher import active_runtime_info
from server.app.artifact_service import (
    evidence_artifact_links,
    inspect_artifact,
    read_artifact_bytes,
)
from server.app.diagnosis.audit_trace import build_audit_bundle
from server.app.diagnosis.probe_registry import list_probes as list_registered_probes
from server.app.diagnosis.schemas import ApprovalRequest, CreateDiagnosisRequest
from server.app.diagnosis.source_gateway import SourceQueryRequest
from server.app.http.auth import (
    extract_api_token as _extract_api_token,
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.runtime_services import (
    diagnosis_orchestrator,
    mcp_client_manager,
    mcp_evidence_service,
    repo,
    source_gateway,
)
from server.app.schemas import APIResponse


router = APIRouter()


def _safe_download_filename(value: str) -> str:
    filename = Path(value.replace("\\", "/")).name
    filename = "".join(ch for ch in filename if ch >= " " and ch not in {'"', ";"})
    return filename[:255] or "artifact.bin"

# ── AI 集群诊断会话（v1）──────────────────────────────────────


@router.post("/api/v1/diagnoses")
def create_diagnosis_session(payload: CreateDiagnosisRequest) -> APIResponse:
    """创建独立诊断会话，并只编排注册表中的受控探针。"""
    try:
        data = diagnosis_orchestrator.create(payload, creator_id="demo_user")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data=data)


@router.get("/api/v1/diagnoses")
def list_diagnosis_sessions(limit: int = 100, offset: int = 0) -> APIResponse:
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    items = diagnosis_orchestrator.list(limit=limit, offset=offset)
    return APIResponse(data={
        "items": items,
        "total": diagnosis_orchestrator.store.count_sessions(),
        "offset": offset,
        "limit": limit,
    })


@router.get("/api/v1/diagnoses/{diagnosis_id}")
def get_diagnosis_session(diagnosis_id: str) -> APIResponse:
    data = diagnosis_orchestrator.get(diagnosis_id, advance=True)
    if data is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    artifacts_by_task = repo.artifacts
    data = {
        **data,
        "evidence": [
            {
                **item,
                "artifact_links": evidence_artifact_links(
                    item,
                    artifacts_by_task,
                    verify=False,
                ),
            }
            for item in data.get("evidence", [])
        ],
    }
    return APIResponse(data=data)


@router.get("/api/v1/diagnoses/{diagnosis_id}/audit-bundle")
def get_diagnosis_audit_bundle(
    diagnosis_id: str,
    include_oracle: bool = False,
) -> APIResponse:
    """Return the evidence-backed decision trace for evaluation or review."""
    data = diagnosis_orchestrator.get(diagnosis_id, advance=False)
    if data is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    return APIResponse(data=build_audit_bundle(data, include_oracle=include_oracle))


@router.get("/api/v1/diagnoses/{diagnosis_id}/audit-bundle/download")
def download_diagnosis_audit_bundle(
    diagnosis_id: str,
    include_oracle: bool = False,
) -> Response:
    data = diagnosis_orchestrator.get(diagnosis_id, advance=False)
    if data is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    content = _json.dumps(
        build_audit_bundle(data, include_oracle=include_oracle),
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
    filename = _safe_download_filename(f"diagnosis-audit-{diagnosis_id}.json")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _find_diagnosis_evidence(diagnosis_id: str, evidence_id: str) -> dict:
    evidence = next(
        (
            item
            for item in diagnosis_orchestrator.store.list_evidence(diagnosis_id)
            if item.get("evidence_id") == evidence_id
        ),
        None,
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="诊断证据不存在")
    return evidence


@router.get("/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence_id}/download")
def download_diagnosis_evidence(diagnosis_id: str, evidence_id: str) -> Response:
    """Download the persisted structured evidence even if its raw artifact expired."""

    evidence = _find_diagnosis_evidence(diagnosis_id, evidence_id)
    content = _json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
    filename = _safe_download_filename(f"evidence-{evidence_id}.json")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence_id}/bundle")
def download_diagnosis_evidence_bundle(diagnosis_id: str, evidence_id: str) -> Response:
    """Build a self-describing ZIP containing evidence, manifest, and available files."""

    evidence = _find_diagnosis_evidence(diagnosis_id, evidence_id)
    artifact_links = evidence_artifact_links(evidence, repo.artifacts, verify=False)
    manifest = {
        "schema_version": "1.0",
        "diagnosis_id": diagnosis_id,
        "evidence_id": evidence_id,
        "artifact_count": len(artifact_links),
        "included_artifact_count": 0,
        "artifacts": [],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "evidence.json",
            _json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        )
        for artifact in artifact_links:
            inspected = inspect_artifact(
                artifact["task_id"],
                artifact,
                check_availability=True,
                verify_hash=False,
            )
            record = {
                key: inspected.get(key)
                for key in (
                    "artifact_id",
                    "task_id",
                    "artifact_type",
                    "filename",
                    "object_key",
                    "content_type",
                    "size_bytes",
                    "sha256",
                    "actual_size_bytes",
                    "availability",
                    "availability_reason",
                    "retention_state",
                    "expires_at",
                    "integrity_status",
                )
            }
            if inspected["availability"] == "available":
                try:
                    content = read_artifact_bytes(inspected)
                    actual_hash = hashlib.sha256(content).hexdigest()
                    record["actual_sha256"] = actual_hash
                    expected_hash = inspected.get("sha256")
                    record["integrity_status"] = (
                        "verified"
                        if expected_hash and actual_hash == expected_hash
                        else "mismatch"
                        if expected_hash
                        else "hash_unavailable"
                    )
                    safe_name = _safe_download_filename(
                        inspected.get("filename")
                        or inspected.get("object_key")
                        or f"{inspected['artifact_type']}.bin"
                    )
                    archive.writestr(
                        f"artifacts/{inspected['artifact_id']}/{safe_name}",
                        content,
                    )
                    manifest["included_artifact_count"] += 1
                except (FileNotFoundError, OSError, ValueError):
                    record["availability"] = "missing"
                    record["availability_reason"] = "打包时文件不可读"
            manifest["artifacts"].append(record)
        archive.writestr(
            "manifest.json",
            _json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
    filename = _safe_download_filename(f"evidence-{evidence_id}-bundle.zip")
    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/storage/reconciliation")
def reconcile_artifact_storage(limit: int = 1000, verify_hash: bool = False) -> APIResponse:
    """Compare artifact metadata with the files currently present in storage."""

    limit = min(max(limit, 1), 5000)
    items: list[dict] = []
    for task_id, artifacts in repo.artifacts.items():
        for artifact in artifacts:
            if len(items) >= limit:
                break
            items.append(inspect_artifact(
                task_id,
                artifact,
                check_availability=True,
                verify_hash=verify_hash,
            ))
        if len(items) >= limit:
            break
    summary = {
        "scanned": len(items),
        "available": sum(item["availability"] == "available" for item in items),
        "missing": sum(item["availability"] == "missing" for item in items),
        "unavailable": sum(item["availability"] == "unavailable" for item in items),
        "integrity_mismatch": sum(item["integrity_status"] == "mismatch" for item in items),
        "retention_expired": sum(item["retention_state"] == "expired" for item in items),
        "verify_hash": verify_hash,
    }
    return APIResponse(data={"summary": summary, "items": items})


@router.post("/api/v1/diagnoses/{diagnosis_id}/cancel")
def cancel_diagnosis_session(diagnosis_id: str, body: Optional[dict] = None) -> APIResponse:
    """取消诊断会话：终态幂等；非终态收敛到 USER_CANCELED 并取消活跃子任务。"""
    reason = ((body or {}).get("reason") or "").strip() or "用户取消诊断"
    try:
        data = diagnosis_orchestrator.cancel(diagnosis_id, reason)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return APIResponse(data=data)


@router.post("/api/v1/diagnoses/{diagnosis_id}/approvals")
def approve_diagnosis_probe(diagnosis_id: str, payload: ApprovalRequest) -> APIResponse:
    try:
        data = diagnosis_orchestrator.approve(diagnosis_id, payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return APIResponse(data=data)


@router.get("/api/v1/probes")
def list_probe_definitions() -> APIResponse:
    return APIResponse(data=[probe.model_dump(mode="json") for probe in list_registered_probes()])


@router.get("/api/v1/sources")
def list_source_definitions() -> APIResponse:
    """List registered AI-readable sources without exposing credential references."""
    return APIResponse(data={
        "schema_version": "source-registry.v1",
        "items": [source.public_dict() for source in source_gateway.list_sources()],
    })


@router.get("/api/v1/mcp/status")
def get_mcp_status(request: Request) -> APIResponse:
    """Expose deployment-safe MCP configuration state; never return endpoints with credentials."""
    _require_role(request, "operator")
    return APIResponse(data={
        "server": {
            "enabled": os.getenv("MINI_DROP_MCP_ENABLED", "0").strip().lower()
            in {"1", "true", "yes", "on"},
            "transport": os.getenv("MINI_DROP_MCP_TRANSPORT", "stdio"),
            "authentication_enabled": os.getenv("MINI_DROP_MCP_AUTH_ENABLED", "1").strip().lower()
            in {"1", "true", "yes", "on"},
            "change_execution_exposed": False,
        },
        "external_connectors": mcp_client_manager.status(),
    })


@router.post("/api/v1/mcp/facts")
def resolve_missing_fact(payload: dict[str, Any], request: Request) -> APIResponse:
    """E6：Missing Fact → REUSE_NATIVE / CALL_MCP / INSUFFICIENT 确定性判定。"""
    _require_role(request, "operator")
    missing_fact = str(payload.get("missing_fact") or "")
    if not missing_fact:
        raise HTTPException(status_code=422, detail="缺少 missing_fact")
    resolution = mcp_evidence_service.resolve(
        missing_fact,
        native_collectors=[str(x) for x in (payload.get("native_collectors") or [])],
    )
    return APIResponse(data=resolution.model_dump(mode="json"))


@router.post("/api/v1/mcp/facts/query")
def query_mcp_for_fact(payload: dict[str, Any], request: Request) -> APIResponse:
    """E6：按 Missing Fact 走受控 MCP 补证（注入清洗 + 成本台账）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    missing_fact = str(payload.get("missing_fact") or "")
    if not missing_fact:
        raise HTTPException(status_code=422, detail="缺少 missing_fact")
    query = SourceQueryRequest(
        tenant_id=tenant_id,
        operation=str(payload.get("operation") or "evidence.list"),
        resource=payload.get("resource") or {},
        parameters=payload.get("parameters") or {},
        case_id=str(payload.get("case_id") or "") or None,
    )
    result = mcp_evidence_service.query_for_fact(
        missing_fact,
        request=query,
        principal_id=_request_principal(request),
        native_collectors=[str(x) for x in (payload.get("native_collectors") or [])],
    )
    return APIResponse(data=result)


@router.get("/api/v1/mcp/ledger")
def get_mcp_ledger(request: Request) -> APIResponse:
    """E6：MCP 调用成本与新鲜度台账。"""
    _require_role(request, "operator")
    source_ids = sorted(mcp_evidence_service._resolver._ledger._calls.keys())
    return APIResponse(data={
        "sources": {
            source_id: mcp_evidence_service.ledger_summary(source_id)
            for source_id in source_ids
        },
    })


@router.get("/api/v1/identity")
def get_current_identity(request: Request) -> APIResponse:
    return APIResponse(data={
        "principal_id": _request_principal(request),
        "tenant_id": _request_tenant(),
        "roles": sorted(getattr(request.state, "principal_roles", set())),
        "identity_source": (
            "configured_principal"
            if os.getenv("MINI_DROP_API_PRINCIPAL_ID", "").strip()
            else "api_key_fingerprint"
            if _extract_api_token(request)
            else "local_development"
        ),
    })


@router.get("/api/v1/cases/{case_id}/agent/runtime-state")
def get_case_agent_runtime_state(case_id: str, request: Request) -> APIResponse:
    """G1/G2：返回当前 Case 的持久 Runtime Binding、Turn 与归一化事件。

    该投影只包含可审计字段，不包含模型私有思维链。
    """
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    turns = repo.list_agent_runtime_turns(case_id, tenant_id)
    events = repo.list_agent_runtime_events(case_id, tenant_id)
    return APIResponse(data={
        "case_id": case_id,
        "binding": binding,
        "turns": turns,
        "events": events,
        "runtime_config": agent_flags(),
    })


@router.get("/api/v1/agent-runtime/config")
def get_agent_runtime_config(request: Request) -> APIResponse:
    """Expose the active investigator runtime mode and feature flags.

    Reveals no secrets: pi runtime URL presence is reported as a boolean, never
    the URL itself, and model credentials never appear here.
    """
    _require_role(request, "operator")
    info = active_runtime_info()
    return APIResponse(data={
        "runtime_type": info["runtime_type"],
        "runtime_version": info["runtime_version"],
        "mode": info["mode"],
        "ready": info.get("ready", True),
        "ready_error": info.get("error"),
        "flags": agent_flags(),
    })



__all__ = ["router"]

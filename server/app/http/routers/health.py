"""Liveness and dependency-aware readiness HTTP adapter."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from server.app.application.health_service import HealthService, HealthSettings
from server.app.common_utils import env_bool
from server.app.http.dependencies import get_health_service
from server.app.schemas import APIResponse

router = APIRouter()


def _settings() -> HealthSettings:
    return HealthSettings(
        storage_required=env_bool(
            "MINI_DROP_REQUIRE_STORAGE",
            env_bool("MINIO_AUTO_CREATE_BUCKET"),
        ),
        storage_bucket=os.getenv("MINIO_BUCKET", "mini-drop"),
        analyzer_required=env_bool("MINI_DROP_REQUIRE_ANALYZER"),
        analyzer_timeout_sec=int(os.getenv("MINI_DROP_ANALYZER_OFFLINE_TIMEOUT_SEC", "30")),
    )


def _health_report(service: HealthService, *, core_only: bool) -> APIResponse:
    return APIResponse(data=service.report(_settings(), core_only=core_only))


@router.get("/api/healthz")
def healthz(
    service: Annotated[HealthService, Depends(get_health_service)],
    core_only: bool = False,
) -> APIResponse:
    return _health_report(service, core_only=core_only)


@router.get("/api/livez")
def livez() -> APIResponse:
    return APIResponse(data={
        "service": "mini-drop-server",
        "version": "0.1.0",
        "alive": True,
    })


@router.get("/api/readyz")
def readyz(
    response: Response,
    service: Annotated[HealthService, Depends(get_health_service)],
    core_only: bool = False,
) -> APIResponse:
    report = _health_report(service, core_only=core_only)
    if not report.data["healthy"]:
        response.status_code = 503
    return report

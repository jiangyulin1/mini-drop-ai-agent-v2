"""Lightweight ASGI and command-line entrypoint for Mini-Drop."""

from __future__ import annotations

import os

from server.app.app_factory import (
    ACTUATION_GATEWAY,
    PLAN_DRIVER,
    _build_runtime_case_context,
    _ensure_minio_bucket_with_retry,
    _judge_recovery,
    _run_offline_sweep_pass,
    _run_plan_driver_pass,
    app,
    case_evidence_service,
    create_app,
    diagnosis_orchestrator,
    investigation_plan_service,
)

# Legacy direct-import compatibility for tests and integrations that reset or
# inspect adapter internals. HTTP/gRPC/background paths use application services.
repo = app.state.container.application_services.persistence_adapter

__all__ = [
    "ACTUATION_GATEWAY",
    "PLAN_DRIVER",
    "_build_runtime_case_context",
    "_ensure_minio_bucket_with_retry",
    "_judge_recovery",
    "_run_offline_sweep_pass",
    "_run_plan_driver_pass",
    "app",
    "case_evidence_service",
    "create_app",
    "diagnosis_orchestrator",
    "investigation_plan_service",
    "repo",
]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8191")),
    )

"""Dependency health use case, independent from FastAPI and concrete adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class HealthSettings:
    storage_required: bool
    storage_bucket: str
    analyzer_required: bool
    analyzer_timeout_sec: int


class HealthService:
    def __init__(
        self,
        *,
        database_probe: Callable[[], None],
        storage_probe: Callable[[str], bool],
        analyzer_probe: Callable[[int], dict[str, Any]],
        log_warning: Callable[..., None],
    ) -> None:
        self._database_probe = database_probe
        self._storage_probe = storage_probe
        self._analyzer_probe = analyzer_probe
        self._log_warning = log_warning

    def report(self, settings: HealthSettings, *, core_only: bool = False) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {}

        try:
            self._database_probe()
            checks["database"] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 - dependency failures become health state.
            self._dependency_failed("database", exc)
            checks["database"] = {
                "status": "unavailable",
                "error_code": "dependency_unavailable",
            }

        if not settings.storage_required:
            checks["storage"] = {"status": "disabled"}
        else:
            try:
                checks["storage"] = (
                    {"status": "ok"}
                    if self._storage_probe(settings.storage_bucket)
                    else {"status": "unavailable", "error_code": "bucket_missing"}
                )
            except Exception as exc:  # noqa: BLE001 - dependency failures become health state.
                self._dependency_failed("storage", exc)
                checks["storage"] = {
                    "status": "unavailable",
                    "error_code": "dependency_unavailable",
                }

        try:
            analyzer = self._analyzer_probe(settings.analyzer_timeout_sec)
            if not settings.analyzer_required and analyzer["workers_online"] == 0:
                analyzer["status"] = "disabled"
            checks["analyzer"] = analyzer
        except Exception as exc:  # noqa: BLE001 - dependency failures become health state.
            self._dependency_failed("analyzer", exc)
            checks["analyzer"] = {
                "status": "unavailable" if settings.analyzer_required else "disabled",
                "error_code": "dependency_unavailable",
            }

        effective_checks = {
            key: value
            for key, value in checks.items()
            if not (core_only and key == "analyzer")
        }
        return {
            "service": "mini-drop-server",
            "version": "0.1.0",
            "healthy": all(
                item["status"] in {"ok", "disabled"}
                for item in effective_checks.values()
            ),
            "checks": checks,
        }

    def _dependency_failed(self, dependency: str, exc: Exception) -> None:
        self._log_warning(
            "warning",
            "health_dependency_failed",
            dependency=dependency,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )

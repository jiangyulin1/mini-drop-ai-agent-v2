"""Typed application composition container.

Only the bootstrap/composition layer should construct this object. HTTP
dependencies read it from ``app.state`` rather than importing ``main``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.application.health_service import HealthService
from server.app.runtime_services import ApplicationServices


@dataclass(frozen=True)
class AppContainer:
    application_services: ApplicationServices
    health_service: HealthService

    @property
    def repository(self) -> Any:
        """Frozen compatibility facade; new code injects a narrower service."""

        return self.application_services.repository

"""FastAPI dependency providers backed by the application container."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from server.app.application.health_service import HealthService
from server.app.container import AppContainer
from server.app.runtime_services import ApplicationServices, bind_application_services


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppContainer):
        raise HTTPException(status_code=503, detail="application container unavailable")
    return container


def get_health_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> HealthService:
    return container.health_service


def get_repository_application_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Any:
    """Return the frozen repository application facade."""

    return container.repository


# External integrations may still import the old provider name. New routers
# must use ``get_repository_application_service``.
get_repository = get_repository_application_service


def get_application_services(
    container: Annotated[AppContainer, Depends(get_container)],
) -> ApplicationServices:
    return container.application_services


async def bind_request_application_services(request: Request):
    """Bind the owning application's service graph for legacy route adapters."""

    container = get_container(request)
    with bind_application_services(container.application_services):
        yield

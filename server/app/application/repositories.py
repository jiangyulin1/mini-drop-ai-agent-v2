"""Domain repository ports used by application services.

The concrete SQL repository remains a compatibility aggregate while C2 splits
the persistence surface.  New application code depends on these small ports,
not on ``SqlRepository`` itself.
"""

from __future__ import annotations

from typing import Any, Protocol

from server.app.schemas import CreateTaskRequest


class TaskRepository(Protocol):
    @property
    def tasks(self) -> dict[str, Any]: ...

    def create_task(
        self,
        payload: CreateTaskRequest,
        *,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        traceparent: str | None = None,
    ) -> Any: ...

    def transition_task(self, task_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def cancel_task(self, task_id: str, *args: Any, **kwargs: Any) -> Any: ...


class AgentRepository(Protocol):
    @property
    def agents(self) -> dict[str, Any]: ...

    def register_agent(self, **kwargs: Any) -> Any: ...
    def heartbeat(self, agent_id: str, ip_addr: str) -> Any: ...
    def heartbeat_only(self, agent_id: str, ip_addr: str) -> Any: ...
    def set_agent_collection_enabled(self, agent_id: str, enabled: bool) -> Any: ...


class CaseRepository(Protocol):
    def get_incident_case(self, case_id: str, tenant_id: str) -> dict[str, Any] | None: ...
    def list_incident_cases(self, tenant_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...
    def record_case_event(self, *args: Any, **kwargs: Any) -> Any: ...

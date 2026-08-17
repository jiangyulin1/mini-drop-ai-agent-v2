"""Narrow persistence boundary for the autonomous Case Supervisor.

The legacy repository facade is deliberately frozen.  Case supervision owns
lease and command-queue operations that are not part of that compatibility
surface, so they are exposed through this explicit domain adapter instead of
silently growing the facade.
"""

from __future__ import annotations

from typing import Any


class CaseSupervisionRepository:
    """Expose only persistence operations required by ``CaseSupervisor``."""

    __slots__ = ("_repository",)

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def list_unleased_cases(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.list_unleased_cases(*args, **kwargs)

    def acquire_case_lease_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.acquire_case_lease_token(*args, **kwargs)

    def acquire_case_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.acquire_case_lease(*args, **kwargs)

    def release_case_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.release_case_lease(*args, **kwargs)

    def enqueue_case_command(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.enqueue_case_command(*args, **kwargs)

    def list_pending_case_commands(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.list_pending_case_commands(*args, **kwargs)

    def complete_case_command(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.complete_case_command(*args, **kwargs)

    def get_incident_case(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.get_incident_case(*args, **kwargs)

    def transition_incident_case(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.transition_incident_case(*args, **kwargs)

    def correct_incident_case(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.correct_incident_case(*args, **kwargs)

    def get_system_control(self, *args: Any, **kwargs: Any) -> Any:
        return self._repository.get_system_control(*args, **kwargs)

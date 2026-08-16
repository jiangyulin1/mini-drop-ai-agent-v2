"""Application-level task presentation helpers shared by HTTP adapters."""

from __future__ import annotations

from server.app.common_utils import status_value
from server.app.schemas import TaskView


def task_view(record) -> TaskView:
    """Convert a repository task record to the stable public API model."""

    return TaskView(
        id=record.id,
        name=record.name,
        agent_id=record.agent_id,
        target_pid=record.target_pid,
        collector_type=record.collector_type,
        sample_rate=record.sample_rate,
        duration_sec=record.duration_sec,
        status=status_value(record.status),
        status_reason=record.status_reason,
        collection_status=getattr(record, "collection_status", None)
        or status_value(record.status),
        analysis_status=getattr(record, "analysis_status", None) or "WAITING",
        current_attempt_id=getattr(record, "current_attempt_id", None),
        row_version=int(getattr(record, "row_version", 0) or 0),
        collection_deadline_at=getattr(record, "collection_deadline_at", None),
        request_id=getattr(record, "request_id", None),
        request_params=record.request_params,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )

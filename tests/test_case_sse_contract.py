"""Canonical Case stream replay and deduplication contract."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from server.app.database import init_db, reset_engine
from server.app.event_bus import BUS
from server.app.main import repo
from server.app.models import Base
from server.app.routes.cases import stream_incident_case_events


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-sse")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _request() -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/cases/case-sse/events/stream",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
    })
    request.state.principal_roles = {"operator"}
    return request


def _frame_data(frame: bytes | str) -> dict:
    text = frame.decode() if isinstance(frame, bytes) else frame
    line = next(item for item in text.splitlines() if item.startswith("data: "))
    return json.loads(line[6:])


def test_sse_snapshot_subscribe_window_has_no_gap_or_duplicate():
    case = repo.create_incident_case({
        "tenant_id": "tenant-sse",
        "created_by": "sse-test",
        "title": "SSE contract",
        "problem_description": "verify replay",
        "recovery_goal": "gap free",
        "run_mode": "COLLABORATE",
        "environment": "test",
        "target_scope": {},
    })
    first = repo.record_case_event(
        case["case_id"], "tenant-sse", event_type="contract.a", payload={},
    )
    second = repo.record_case_event(
        case["case_id"], "tenant-sse", event_type="contract.b", payload={},
    )

    async def consume() -> tuple[dict, dict]:
        response = await stream_incident_case_events(
            case["case_id"], _request(), after_seq=first["case_event_seq"],
        )
        iterator = response.body_iterator
        replayed = _frame_data(await iterator.__anext__())
        # A duplicated bus delivery must be fenced by the durable Case cursor.
        BUS.publish("case_event", second)
        third = repo.record_case_event(
            case["case_id"], "tenant-sse", event_type="contract.c", payload={},
        )
        live = _frame_data(await asyncio.wait_for(iterator.__anext__(), timeout=2))
        await iterator.aclose()
        return replayed, live

    replayed, live = asyncio.run(consume())
    assert replayed["event_type"] == "contract.b"
    assert live["event_type"] == "contract.c"
    assert replayed["case_event_seq"] + 1 == live["case_event_seq"]

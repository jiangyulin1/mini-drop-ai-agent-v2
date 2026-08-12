"""MCP facade and remote SourceGateway adapter tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.app.diagnosis.authorization import SourceDefinition
from server.app.diagnosis.source_gateway import SourceGateway, SourceQueryRequest
from server.app.mcp_integration.client import (
    MCPClientManager,
    MCPConnectorConfig,
    load_mcp_connector_configs,
)


def test_connector_configuration_rejects_credentials_and_insecure_remote_urls():
    with pytest.raises(ValueError, match="credentials"):
        MCPConnectorConfig.from_dict({
            "source_id": "unsafe-source",
            "url": "https://user:secret@mcp.example.com/mcp",
            "operations": {"metrics.query": "query"},
        })
    with pytest.raises(ValueError, match="https"):
        MCPConnectorConfig.from_dict({
            "source_id": "unsafe-source",
            "url": "http://mcp.example.com/mcp",
            "operations": {"metrics.query": "query"},
        })


def test_connector_configuration_exports_public_source_metadata(monkeypatch):
    monkeypatch.setenv("OPS_MCP_TOKEN", "secret-not-visible")
    manager = MCPClientManager(load_mcp_connector_configs(json.dumps([{
        "source_id": "ops-observability",
        "name": "Ops observability MCP",
        "url": "https://mcp.example.com/mcp",
        "operations": {"metrics.query": "query_metrics"},
        "resource_dimensions": ["cluster_id", "service_id"],
        "data_classes": ["operational_metric"],
        "token_env": "OPS_MCP_TOKEN",
    }])))

    public = manager.source_definitions()[0].public_dict()
    assert public["source_type"] == "mcp"
    assert public["operations"] == ["metrics.query"]
    assert public["credential_configured"] is True
    assert "OPS_MCP_TOKEN" not in json.dumps(public)
    status = manager.status()[0]
    assert status["endpoint"] == "https://mcp.example.com/mcp"
    assert status["authentication_configured"] is True


class _Repo:
    def __init__(self):
        self.consumed = []
        self.denied = []

    def list_authorization_grants(self, **_kwargs):
        return [{
            "grant_id": "grant-mcp",
            "principal_id": "operator-1",
            "tenant_id": "tenant-a",
            "source_ids": ["ops-observability"],
            "operations": ["metrics.query"],
            "resource_scope": {"service_id": ["checkout"]},
            "status": "ACTIVE",
            "valid_until": datetime.now(timezone.utc) + timedelta(hours=1),
            "uses_remaining": 5,
            "query_count": 0,
            "constraints": {
                "max_result_bytes": 30_000,
                "max_queries": 5,
                "allowed_time_range_minutes": 60,
            },
        }]

    def consume_authorization_grant(self, grant_id, **kwargs):
        self.consumed.append((grant_id, kwargs))

    def record_source_access_denied(self, **kwargs):
        self.denied.append(kwargs)


def test_remote_mcp_source_still_passes_grant_token_redaction_and_envelope(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_SOURCE_ACCESS_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_CAPABILITY_TOKEN_SECRET", "test-capability-secret-value-32-bytes-minimum")
    config = MCPConnectorConfig.from_dict({
        "source_id": "ops-observability",
        "url": "https://mcp.example.com/mcp",
        "operations": {"metrics.query": "query_metrics"},
        "resource_dimensions": ["service_id"],
        "data_classes": ["operational_metric"],
    })
    seen = {}

    def caller(_config, tool_name, arguments):
        seen.update({"tool_name": tool_name, "arguments": arguments})
        return {"cpu": 92.4, "authorization": "must-be-redacted"}

    manager = MCPClientManager([config], caller=caller)
    repo = _Repo()
    gateway = SourceGateway(
        repo,
        SimpleNamespace(store=SimpleNamespace()),
        extra_connectors=manager.connectors,
        extra_source_definitions=manager.source_definitions(),
    )
    envelope = gateway.query(
        "ops-observability",
        SourceQueryRequest(
            tenant_id="tenant-a",
            operation="metrics.query",
            resource={"service_id": "checkout"},
            parameters={"window": "5m"},
            requested_result_bytes=20_000,
        ),
        principal_id="operator-1",
    )

    assert seen["tool_name"] == "query_metrics"
    assert seen["arguments"]["resource"] == {"service_id": "checkout"}
    assert envelope.source_id == "ops-observability"
    assert envelope.content_projection["authorization"] == "[REDACTED]"
    assert envelope.policy["decision"] == "AUTO_GRANTED"
    assert repo.consumed[0][0] == "grant-mcp"


def test_source_gateway_rejects_duplicate_builtin_source():
    class Connector:
        source_id = "mini-drop-agent-metrics"

        def execute(self, request):  # pragma: no cover
            return {}

    with pytest.raises(ValueError, match="duplicate source connector"):
        SourceGateway(
            SimpleNamespace(),
            SimpleNamespace(),
            extra_connectors={"mini-drop-agent-metrics": Connector()},
            extra_source_definitions=[SourceDefinition(
                source_id="another-source",
                name="another",
                source_type="mcp",
                operations=["read"],
            )],
        )


def test_mcp_server_tools_and_resources_with_official_sdk(monkeypatch):
    pytest.importorskip("mcp")
    from mcp import Client
    from server.app.mcp_integration.server import create_mcp_server

    monkeypatch.setenv("MINI_DROP_MCP_AUTH_ENABLED", "0")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")

    class Repo:
        def list_incident_cases(self, tenant_id, **_kwargs):
            return [{"case_id": "case-1", "tenant_id": tenant_id, "state": "OPEN"}]

        def get_incident_case(self, case_id, tenant_id):
            return {"case_id": case_id, "tenant_id": tenant_id, "state": "OPEN"}

        def record_audit(self, **_kwargs):
            return None

    class Orchestrator:
        store = SimpleNamespace(list_evidence=lambda _id: [])

        def get(self, diagnosis_id, advance=False):
            return {"diagnosis_id": diagnosis_id, "status": "COMPLETED", "advance": advance}

    class Actuation:
        def dry_run(self, action_id, parameters):
            return {"attempt_id": "dry-1", "action_id": action_id, "parameters": parameters}

    class Sources:
        def list_sources(self):
            return []

    server = create_mcp_server(
        repo=Repo(), orchestrator=Orchestrator(), source_gateway=Sources(),
        actuation_gateway=Actuation(),
    )

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {item.name for item in tools.tools}
            assert "query_registered_source" in names
            assert "dry_run_action" in names
            assert "execute_action" not in names
            result = await client.call_tool("list_incident_cases", {"limit": 5})
            assert result.structured_content["items"][0]["case_id"] == "case-1"
            resource = await client.read_resource("mini-drop://cases/case-1")
            assert "case-1" in resource.contents[0].text

    import asyncio
    asyncio.run(exercise())

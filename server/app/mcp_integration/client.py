"""Policy-neutral MCP client adapters used behind ``SourceGateway``.

Remote MCP results are deliberately returned as untrusted raw dictionaries.
SourceGateway remains responsible for authorization, capability tokens,
redaction, result budgets, evidence lineage, and grant consumption.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from server.app.diagnosis.authorization import OperationClass, SourceDefinition
from server.app.diagnosis.source_gateway import SourceGatewayError, SourceQueryRequest


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


class MCPClientError(RuntimeError):
    """A bounded error suitable for translating at the SourceGateway boundary."""


@dataclass(frozen=True)
class MCPConnectorConfig:
    source_id: str
    name: str
    url: str
    operations: dict[str, str]
    resource_dimensions: list[str] = field(default_factory=list)
    data_classes: list[str] = field(default_factory=lambda: ["external_operational_data"])
    token_env: str | None = None
    timeout_sec: int = 20
    max_result_bytes: int = 1_048_576
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MCPConnectorConfig":
        source_id = str(value.get("source_id") or "").strip()
        if not _IDENTIFIER.fullmatch(source_id):
            raise ValueError(f"invalid MCP source_id: {source_id!r}")
        name = str(value.get("name") or source_id).strip()[:128]
        url = str(value.get("url") or "").strip()
        _validate_remote_url(url)
        operations = value.get("operations") or {}
        if not isinstance(operations, dict) or not operations:
            raise ValueError(f"MCP connector {source_id} requires an operations mapping")
        normalized_operations: dict[str, str] = {}
        for operation, tool_name in operations.items():
            operation = str(operation).strip()
            tool_name = str(tool_name).strip()
            if not operation or not tool_name:
                raise ValueError(f"MCP connector {source_id} has an empty operation/tool")
            normalized_operations[operation] = tool_name
        token_env = str(value.get("token_env") or "").strip() or None
        if token_env and not _ENV_NAME.fullmatch(token_env):
            raise ValueError(f"invalid MCP token_env for {source_id}")
        return cls(
            source_id=source_id,
            name=name,
            url=url,
            operations=normalized_operations,
            resource_dimensions=_string_list(value.get("resource_dimensions"), 16),
            data_classes=_string_list(value.get("data_classes"), 16)
            or ["external_operational_data"],
            token_env=token_env,
            timeout_sec=_bounded_int(value.get("timeout_sec", 20), 1, 300),
            max_result_bytes=_bounded_int(
                value.get("max_result_bytes", 1_048_576), 1_024, 100_000_000,
            ),
            enabled=bool(value.get("enabled", True)),
        )

    def source_definition(self) -> SourceDefinition:
        return SourceDefinition(
            source_id=self.source_id,
            name=self.name,
            source_type="mcp",
            operation_class=OperationClass.READ,
            operations=sorted(self.operations),
            resource_dimensions=self.resource_dimensions,
            data_classes=self.data_classes,
            credential_ref=f"env://{self.token_env}" if self.token_env else None,
            network_policy=[_network_policy_label(self.url)],
            default_timeout_sec=self.timeout_sec,
            max_result_bytes=self.max_result_bytes,
            enabled=self.enabled,
            version="mcp-2026-07-28",
        )


def load_mcp_connector_configs(raw: str | None = None) -> list[MCPConnectorConfig]:
    """Load deployment-controlled connectors from a JSON array.

    Tokens are referenced by environment-variable name and never appear in the
    JSON or in model-visible source metadata.
    """
    encoded = raw if raw is not None else os.getenv("MINI_DROP_MCP_CONNECTORS_JSON", "")
    if not encoded.strip():
        return []
    try:
        values = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("MINI_DROP_MCP_CONNECTORS_JSON must be valid JSON") from exc
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError("MINI_DROP_MCP_CONNECTORS_JSON must be an array with at most 32 entries")
    configs = [MCPConnectorConfig.from_dict(item) for item in values if isinstance(item, dict)]
    ids = [item.source_id for item in configs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate MCP source_id")
    return configs


class MCPRemoteConnector:
    """A SourceConnector backed by one remote Streamable HTTP MCP server."""

    def __init__(
        self,
        config: MCPConnectorConfig,
        caller: Callable[[MCPConnectorConfig, str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.config = config
        self.source_id = config.source_id
        self._caller = caller or _call_remote_tool

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        tool_name = self.config.operations.get(request.operation)
        if not tool_name:
            raise SourceGatewayError("OPERATION_NOT_IMPLEMENTED", 400)
        arguments = {
            "resource": dict(request.resource),
            "parameters": dict(request.parameters),
            "case_id": request.case_id,
            "requested_time_range_minutes": request.requested_time_range_minutes,
        }
        try:
            result = self._caller(self.config, tool_name, arguments)
        except MCPClientError as exc:
            raise SourceGatewayError(f"MCP_SOURCE_ERROR:{str(exc)[:300]}", 502) from exc
        if not isinstance(result, dict):
            return {"value": result}
        return result


class MCPClientManager:
    def __init__(
        self,
        configs: list[MCPConnectorConfig] | None = None,
        *,
        caller: Callable[[MCPConnectorConfig, str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.configs = configs if configs is not None else load_mcp_connector_configs()
        self.connectors = {
            item.source_id: MCPRemoteConnector(item, caller=caller)
            for item in self.configs
            if item.enabled
        }

    def source_definitions(self) -> list[SourceDefinition]:
        return [item.source_definition() for item in self.configs]

    def status(self) -> list[dict[str, Any]]:
        return [{
            "source_id": item.source_id,
            "name": item.name,
            "transport": "streamable-http",
            "endpoint": _redacted_endpoint(item.url),
            "operations": sorted(item.operations),
            "authentication_configured": bool(item.token_env and os.getenv(item.token_env)),
            "enabled": item.enabled,
        } for item in self.configs]


def _call_remote_tool(
    config: MCPConnectorConfig,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError as exc:
        raise MCPClientError("MCP SDK is not installed; install micro-drop[mcp]") from exc

    async def invoke() -> dict[str, Any]:
        headers: dict[str, str] = {}
        if config.token_env:
            token = os.getenv(config.token_env, "").strip()
            if not token:
                raise MCPClientError(f"credential environment variable {config.token_env} is empty")
            headers["Authorization"] = f"Bearer {token}"
        http_client = create_mcp_http_client(headers=headers or None)
        async with http_client:
            transport = streamable_http_client(
                config.url,
                http_client=http_client,
                terminate_on_close=False,
            )
            async with Client(
                transport,
                read_timeout_seconds=float(config.timeout_sec),
                raise_exceptions=True,
            ) as client:
                result = await client.call_tool(tool_name, arguments)
                structured = getattr(result, "structured_content", None)
                if structured is not None:
                    return structured if isinstance(structured, dict) else {"value": structured}
                return _content_blocks_to_dict(getattr(result, "content", []))

    try:
        return _run_async(invoke)
    except MCPClientError:
        raise
    except Exception as exc:
        raise MCPClientError(f"{type(exc).__name__}: {str(exc)[:240]}") from exc


def _run_async(factory: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run an async MCP call from sync orchestration code, including under an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:  # propagated to the caller thread
            errors.append(exc)

    thread = threading.Thread(target=runner, name="mini-drop-mcp-client", daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def _content_blocks_to_dict(blocks: list[Any]) -> dict[str, Any]:
    texts = [str(getattr(item, "text", "")) for item in blocks if getattr(item, "text", None)]
    if len(texts) == 1:
        try:
            parsed = json.loads(texts[0])
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            pass
    return {"content": texts}


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP connector URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("MCP connector URL must not contain credentials")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local:
        raise ValueError("remote MCP connector URL must use https")


def _redacted_endpoint(url: str) -> str:
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path or '/'}"


def _network_policy_label(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"


def _string_list(value: Any, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:128] for item in value[:maximum] if str(item).strip()]


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid integer in MCP connector configuration") from exc
    return min(max(parsed, minimum), maximum)

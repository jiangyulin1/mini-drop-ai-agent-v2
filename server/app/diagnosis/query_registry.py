"""QueryOperationRegistry (G4): registered low-risk read-only operations.

Every operation is compiled to a native Mini-Drop Task with a registered
Collector.  The Sidecar/Pi never runs commands directly and cannot supply
executable/cwd/env/argv fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel

QUERY_RISK = "READ_LOW"


class QueryOperation(StrictModel):
    operation_id: str
    display_name: str
    description: str
    collector_id: str
    risk: str = QUERY_RISK
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    default_duration_sec: int = 15
    default_sample_rate: int = 11


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": props}


QUERY_OPERATIONS: tuple[QueryOperation, ...] = (
    QueryOperation(
        operation_id="process.list",
        display_name="进程清单",
        description="读取 Worker 上全部进程候选（PID、命令行、CPU、内存）。",
        collector_id="process_scan",
        parameter_schema=_schema({}),
        default_duration_sec=2,
        default_sample_rate=1,
    ),
    QueryOperation(
        operation_id="system.metrics",
        display_name="系统指标",
        description="读取主机与目标进程 CPU、负载、线程、FD、网络等指标。",
        collector_id="sys_metrics",
        parameter_schema=_schema({
            "target_ref": {"type": "string", "maxLength": 256},
        }),
        default_duration_sec=15,
        default_sample_rate=11,
    ),
    QueryOperation(
        operation_id="service.connection",
        display_name="服务连通性",
        description="对受控目标端点执行 TCP/HTTP 只读连通性探测。",
        collector_id="connection_probe",
        parameter_schema=_schema({
            "target_ref": {"type": "string", "maxLength": 256},
        }),
        default_duration_sec=10,
        default_sample_rate=1,
    ),
    QueryOperation(
        operation_id="service.logs",
        display_name="服务日志",
        description="读取目标进程日志尾部并提取错误/警告模式。",
        collector_id="log_scan",
        parameter_schema=_schema({
            "target_ref": {"type": "string", "maxLength": 256},
        }),
        default_duration_sec=2,
        default_sample_rate=1,
    ),
)


class QueryRegistry:
    def __init__(self) -> None:
        self._by_id = {item.operation_id: item for item in QUERY_OPERATIONS}

    def list_operations(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in QUERY_OPERATIONS]

    def get(self, operation_id: str) -> QueryOperation | None:
        return self._by_id.get(operation_id)

    def validate_parameters(
        self,
        operation_id: str,
        parameters: dict[str, Any] | None,
    ) -> list[str]:
        """Reject unknown, dangerous or out-of-schema parameters before Task creation."""
        operation = self.get(operation_id)
        if operation is None:
            return ["UNKNOWN_QUERY_OPERATION"]
        parameters = parameters or {}
        if not isinstance(parameters, dict):
            return ["INVALID_QUERY_PARAMETERS"]
        errors: list[str] = []
        schema = operation.parameter_schema
        properties = schema.get("properties") or {}
        for key, value in parameters.items():
            if key not in properties:
                errors.append(f"UNSUPPORTED_PARAM:{key}")
                continue
            prop = properties[key]
            if prop.get("type") == "string" and not isinstance(value, str):
                errors.append(f"INVALID_PARAM_TYPE:{key}")
            elif prop.get("type") == "integer" and not isinstance(value, int):
                errors.append(f"INVALID_PARAM_TYPE:{key}")
            max_length = prop.get("maxLength")
            if max_length and isinstance(value, str) and len(value) > int(max_length):
                errors.append(f"PARAM_TOO_LONG:{key}")
        return errors


QUERY_REGISTRY = QueryRegistry()

"""QueryOperationRegistry (G4): registered low-risk read-only operations.

Every operation is compiled to a native Mini-Drop Task with a registered
Collector.  The Sidecar/Pi never runs commands directly and cannot supply
executable/cwd/env/argv fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from mini_drop_contracts import list_collector_specs
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


def _operation_from_spec(spec: Any) -> QueryOperation:
    parameters = {} if spec.semantic_operation == "process.list" else {
        "target_ref": {"type": "string", "maxLength": 256},
    }
    return QueryOperation(
        operation_id=str(spec.semantic_operation),
        display_name=spec.display_name,
        description=spec.description,
        collector_id=spec.collector_id,
        risk="READ_LOW" if spec.risk_level == "R1" else "READ_ELEVATED",
        parameter_schema=_schema(parameters),
        default_duration_sec=spec.default_duration,
        default_sample_rate=spec.default_sample_rate,
    )


QUERY_OPERATIONS: tuple[QueryOperation, ...] = tuple(
    _operation_from_spec(spec)
    for spec in list_collector_specs()
    if spec.semantic_operation
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

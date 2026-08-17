#!/usr/bin/env python3
"""Fail CI when Mini-Drop registries drift across Control, Worker and Sidecar."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.catalog import TOOL_CATALOG_BY_NAME
from server.app.diagnosis.evidence_contracts import PROBE_FACTS, _CONTRACTS
from server.app.diagnosis.probe_registry import list_probes
from server.app.diagnosis.query_registry import QUERY_OPERATIONS
from server.app.task_kinds import TASK_KINDS


def _agent_collectors() -> set[str]:
    tree = ast.parse((ROOT / "agent" / "mini_drop_agent" / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "COLLECTORS" for target in node.targets):
            if isinstance(node.value, ast.Dict):
                return {str(ast.literal_eval(key)) for key in node.value.keys}
    raise RuntimeError("agent COLLECTORS registry not found")


def _sidecar_tools() -> set[str]:
    source = (ROOT / "agent_runtime" / "pi-sidecar" / "src" / "tools.mjs").read_text(encoding="utf-8")
    match = re.search(r"export const ALLOWED_TOOL_NAMES\s*=\s*(\[[\s\S]*?\]);", source)
    if not match:
        raise RuntimeError("Sidecar ALLOWED_TOOL_NAMES not found")
    array_text = re.sub(r",\s*]$", "]", match.group(1))
    return set(json.loads(array_text))


def check() -> list[str]:
    errors: list[str] = []
    collectors = _agent_collectors()
    kinds = {item["key"] for item in TASK_KINDS}
    probes = {item.probe_id: item for item in list_probes()}
    for missing in sorted(kinds - collectors):
        errors.append(f"TaskKind {missing} has no Worker collector")
    for probe_id, probe in probes.items():
        if probe.runner_task_kind not in kinds:
            errors.append(f"Probe {probe_id} references unknown TaskKind {probe.runner_task_kind}")
        if probe.runner_task_kind not in collectors:
            errors.append(f"Probe {probe_id} references missing Worker collector {probe.runner_task_kind}")
    for operation in QUERY_OPERATIONS:
        if operation.collector_id not in collectors:
            errors.append(f"QueryOperation {operation.operation_id} references missing collector {operation.collector_id}")
    known_probes = set(probes)
    for contract in _CONTRACTS:
        for missing in sorted(set(contract.candidate_probes) - known_probes):
            errors.append(f"EvidenceContract {contract.mechanism} references unknown probe {missing}")
    for missing in sorted(set(PROBE_FACTS) - known_probes):
        errors.append(f"PROBE_FACTS references unknown probe {missing}")
    server_tools = set(TOOL_CATALOG_BY_NAME)
    sidecar_tools = _sidecar_tools()
    for missing in sorted(server_tools - sidecar_tools):
        errors.append(f"Server tool {missing} is absent from Sidecar compatibility allowlist")
    for extra in sorted(sidecar_tools - server_tools):
        errors.append(f"Sidecar tool {extra} is absent from canonical Server catalog")
    return errors


def main() -> int:
    try:
        errors = check()
    except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"ERROR: registry check failed to load: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print("OK: TaskKind, Collector, Probe, EvidenceContract, QueryOperation and Agent Tool registries are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

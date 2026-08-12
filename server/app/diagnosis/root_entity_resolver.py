"""RootEntityResolver：把集群归因结论解析为稳定的 root_entity（服务/资源 ID）。

Oracle 期望的 root_entity 是稳定服务或资源 ID（如 paymentservice、redis-cart），
而不是 instance_id。解析优先级：
1. 下游依赖（classification=downstream_dependency 或 root_location.type=downstream）：
   从 scope 的依赖边取目标服务的一跳下游，优先选择观测/日志/探针实际指向的服务；
2. 网络退化：同样尝试解析 peer，无法唯一确定时返回 None；
3. 自身/同宿主/共享资源：root_entity = 目标服务稳定 ID。

无法确定时返回 None（宁缺毋滥，不猜）。
"""

from __future__ import annotations

from typing import Any

_RELATION_RANK = {
    "CALLS": 0,
    "READS_FROM": 1,
    "WRITES_TO": 1,
    "PUBLISHES_TO": 1,
    "CONSUMES_FROM": 1,
    "SHARES_DEPENDENCY": 2,
}


def _downstream_services(target_service: str | None, dependencies: list[dict[str, Any]]) -> set[str]:
    if not target_service:
        return set()
    return {
        str(edge.get("target_service"))
        for edge in dependencies
        if edge.get("source_service") == target_service
        and edge.get("relation") in _RELATION_RANK
        and edge.get("target_service")
    }


def _observed_services(observations: list[dict[str, Any]]) -> set[str]:
    services: set[str] = set()
    for obs in observations or []:
        target = obs.get("target") or {}
        if target.get("service_id"):
            services.add(str(target["service_id"]))
        facts = obs.get("facts") or {}
        downstream = facts.get("endpoint.downstream_service")
        if downstream:
            services.update(
                str(item) for item in str(downstream).split(",") if item
            )
        log = obs.get("log") or {}
        for pattern in ("connection_refused", "timeout", "downstream_endpoint"):
            count = int((log.get("patterns") or {}).get(pattern, 0) or 0)
            if count > 0 and target.get("service_id"):
                services.add(str(target["service_id"]))
    return services


def _pick_downstream(
    candidates: set[str],
    observed: set[str],
) -> str | None:
    """优先选被观测/证据指向的下游；无交集时只有唯一下游才返回。"""
    if not candidates:
        return None
    for candidate in sorted(candidates):
        if candidate in observed:
            return candidate
    return sorted(candidates)[0] if len(candidates) == 1 else None


def resolve_root_entity(
    assessment: dict[str, Any],
    scope: dict[str, Any],
    observations: list[dict[str, Any]] | None = None,
) -> str | None:
    """由集群归因结论与作用域解析稳定 root_entity。"""
    classification = str(assessment.get("classification") or "")
    location = str((assessment.get("root_location") or {}).get("type") or "")
    target_service = scope.get("target_service")
    dependencies = scope.get("dependencies") or []
    downstream_services = set(scope.get("downstream_service_ids") or [])
    observed = _observed_services(observations or [])

    if classification == "downstream_dependency" or location == "downstream":
        candidates = _downstream_services(target_service, dependencies)
        if not candidates:
            candidates = set(downstream_services)
        return _pick_downstream(candidates, observed)

    if classification == "network_degradation":
        candidates = _downstream_services(target_service, dependencies) or set(downstream_services)
        return _pick_downstream(candidates, observed)

    if not target_service:
        return None
    # 自身 / 同宿主 / 共享资源 → 稳定服务 ID。
    return str(target_service)

"""E6: MCP 补证来源 —— Agent 按 Missing Fact 调用受控外部数据。

计划 7.4/8.9/14：MCP 数据必须经 SourceGateway 投影为受控 Evidence，而不是
手工 Source 查询；先复用原生采集，仅当 Missing Fact 无法由现有采集器覆盖时
才调用 MCP。本模块提供：

- ``MISSING_FACT_SOURCES``：缺失事实类别 → MCP Source/operation 的确定性映射；
- ``McpFactResolver.resolve``：按原生能力覆盖度返回
  REUSE_NATIVE / CALL_MCP / INSUFFICIENT，绝不把无映射的事实硬塞给任意 MCP；
- ``sanitize_mcp_content``：注入门禁 —— 剥离 MCP 返回中的指令型内容
  （<system> 标签、忽略前置指令、代码围栏），并记录 redaction 计数；
- ``McpCallLedger``：成本与新鲜度台账（调用次数、最近观测、延迟、新鲜度分）。

安全门禁（越权 / 大小 / 超时）由 SourceGateway 的授权令牌、结果预算与 MCP
read_timeout_seconds 负责，这里不再重复实现。
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from server.app.diagnosis.schemas import StrictModel

# 缺失事实类别 → MCP Source/operation。
# 只有这里注册的事实类别才允许走 MCP；其余一律 INSUFFICIENT。
MISSING_FACT_SOURCES: dict[str, dict[str, str]] = {
    "database_deadlock_history": {"source_id": "mcp-db-platform", "operation": "deadlock.list"},
    "redis_eviction_history": {"source_id": "mcp-cache-platform", "operation": "eviction.stats"},
    "kubernetes_events": {"source_id": "mcp-k8s-control-plane", "operation": "events.list"},
    "load_balancer_backend_state": {"source_id": "mcp-lb-platform", "operation": "backend.state"},
    "service_config_diff": {"source_id": "mcp-config-registry", "operation": "config.diff"},
    "billing_quota_usage": {"source_id": "mcp-billing-platform", "operation": "quota.usage"},
}

# 已注册（可部署）的 MCP Source；未注册的映射在 resolve 时被拒绝。
_REGISTERED_MCP_SOURCE_IDS = {item["source_id"] for item in MISSING_FACT_SOURCES.values()}

# 注入模式：MCP 内容中出现这些模式即视为潜在指令注入，需要剥离并计数。
_INJECTION_PATTERNS = [
    re.compile(r"<\s*system\b[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*\/?\s*system\s*>", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous|prior|earlier)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(an?\s+|a\s+)?(ai|assistant|mini-drop)", re.IGNORECASE),
    re.compile(r"```(?:text|markdown)?\s*[^\n]*(?:system|instruction)", re.IGNORECASE),
]


class MissingFactResolution(StrictModel):
    missing_fact: str
    decision: str  # REUSE_NATIVE | CALL_MCP | INSUFFICIENT
    source_id: str = ""
    operation: str = ""
    native_collector: str = ""
    reason_code: str = ""
    note: str = ""


class McpCallLedger:
    """MCP 调用成本与新鲜度台账（进程内；持久化由部署层负责）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, dict[str, Any]] = {}

    def record(
        self,
        source_id: str,
        *,
        ok: bool,
        latency_ms: float,
        result_bytes: int,
        observed_at: Optional[datetime] = None,
    ) -> None:
        with self._lock:
            entry = self._calls.setdefault(source_id, {
                "calls": 0, "failures": 0, "total_bytes": 0, "total_latency_ms": 0.0,
                "last_observed_at": None, "last_ok": None,
            })
            entry["calls"] += 1
            entry["failures"] += 0 if ok else 1
            entry["total_bytes"] += int(result_bytes)
            entry["total_latency_ms"] += float(latency_ms)
            entry["last_ok"] = bool(ok)
            entry["last_observed_at"] = (observed_at or datetime.now(timezone.utc)).isoformat()

    def freshness_score(self, source_id: str, *, max_age_seconds: int = 600) -> float:
        """新鲜度：最近成功观测距今越近分越高，0 无观测 / 1 新鲜。"""
        with self._lock:
            entry = self._calls.get(source_id)
            if entry is None or not entry.get("last_observed_at"):
                return 0.0
            if not entry.get("last_ok"):
                return 0.0
            observed = datetime.fromisoformat(entry["last_observed_at"])
            age_sec = (datetime.now(timezone.utc) - observed).total_seconds()
            return max(0.0, 1.0 - age_sec / max_age_seconds)

    def summary(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            entry = dict(self._calls.get(source_id, {}))
        return {**entry, "freshness": self.freshness_score(source_id)}


class McpFactResolver:
    """Missing Fact → 原生复用 / MCP 调用 / 拒答。"""

    def __init__(
        self,
        *,
        native_collectors: set[str] | None = None,
        ledger: McpCallLedger | None = None,
        registered_sources: set[str] | None = None,
    ):
        # 环境实际可用的原生采集器由调用方注入；默认空，避免把"可能"当"已覆盖"。
        self._native = {str(x) for x in (native_collectors or [])}
        self._ledger = ledger or McpCallLedger()
        # 注意：空集合是合法的"无注册"，不能用 `or` 回退到全量注册表。
        self._registered = (
            {str(x) for x in registered_sources}
            if registered_sources is not None
            else set(_REGISTERED_MCP_SOURCE_IDS)
        )

    def resolve(
        self,
        missing_fact: str,
        *,
        native_capabilities: list[str] | None = None,
        native_collectors: list[str] | None = None,
    ) -> MissingFactResolution:
        """先复用原生采集；仅当原生无法覆盖且 MCP 有注册映射时才 CALL_MCP。"""
        fact = (missing_fact or "").strip()
        native = set(native_capabilities or []) | set(native_collectors or []) | self._native
        native_domains = {
            str(c).split(":", 1)[1] if ":" in str(c) else str(c)
            for c in native if str(c)
        }
        native_covering = self._native_covers(fact, native_domains)
        if native_covering:
            return MissingFactResolution(
                missing_fact=fact, decision="REUSE_NATIVE",
                native_collector=native_covering, reason_code="NATIVE_COVERS_FACT",
                note="原生采集器已覆盖该缺失事实，不调用外部 MCP。",
            )
        mapping = MISSING_FACT_SOURCES.get(fact)
        if mapping is None:
            return MissingFactResolution(
                missing_fact=fact, decision="INSUFFICIENT",
                reason_code="NO_MCP_MAPPING",
                note="该缺失事实无注册 MCP 映射，明确拒答而不是猜测。",
            )
        if mapping["source_id"] not in self._registered:
            return MissingFactResolution(
                missing_fact=fact, decision="INSUFFICIENT",
                source_id=mapping["source_id"], operation=mapping["operation"],
                reason_code="MCP_SOURCE_NOT_REGISTERED",
                note="MCP Source 未注册，不允许调用。",
            )
        return MissingFactResolution(
            missing_fact=fact, decision="CALL_MCP",
            source_id=mapping["source_id"], operation=mapping["operation"],
            reason_code="NATIVE_GAP",
            note="原生采集器无法覆盖，调用受控 MCP Source 补证。",
        )

    def _native_covers(self, fact: str, native_domains: set[str]) -> str:
        """原生采集器对缺失事实的覆盖判断（确定性白名单）。"""
        # 仅当原生采集器确实能观测该缺失事实时才覆盖；k8s 控制面事件、
        # 配置中心 diff、账单额度在 Worker 主机上不可观测 → 不覆盖。
        FACT_NATIVE_COLLECTOR = {
            "database_deadlock_history": "log_scan",
            "redis_eviction_history": "log_scan",
            "load_balancer_backend_state": "connection_probe",
        }
        collector = FACT_NATIVE_COLLECTOR.get(fact, "")
        if collector and collector in native_domains:
            return collector
        return ""


def sanitize_mcp_content(payload: Any, *, max_string_chars: int = 800) -> tuple[Any, int]:
    """注入门禁：剥离 MCP 返回中的指令型内容，返回 (清洗后 payload, 移除计数)。

    只做结构性剥离，不改业务数值；清洗结果经 SourceGateway 投影后进入模型上下文。
    """
    removed = 0

    def scrub(value: Any) -> Any:
        nonlocal removed
        if isinstance(value, str):
            out = value
            for pattern in _INJECTION_PATTERNS:
                cleaned, count = pattern.subn("", out)
                removed += count
                out = cleaned
            if len(out) > max_string_chars:
                out = out[:max_string_chars]
            return out
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    return scrub(payload), removed


class McpEvidenceService:
    """Missing Fact → 受控 MCP 调用 → 注入清洗 → EvidenceEnvelope。

    ``query_fn`` 注入 SourceGateway.query（返回 EvidenceEnvelope）；缺省时
    无法调用（降级为 INSUFFICIENT），保证该服务不依赖真实 MCP 也可验证策略。
    """

    def __init__(
        self,
        resolver: McpFactResolver,
        *,
        query_fn: Any = None,
    ):
        self._resolver = resolver
        self._query = query_fn

    def resolve(self, missing_fact: str, **kwargs) -> MissingFactResolution:
        return self._resolver.resolve(missing_fact, **kwargs)

    def query_for_fact(
        self,
        missing_fact: str,
        *,
        request: Any,
        principal_id: str,
        **resolve_kwargs: Any,
    ) -> dict[str, Any]:
        """按 Missing Fact 走补证：REUSE_NATIVE 不调用 MCP；CALL_MCP 才查询。"""
        resolution = self._resolver.resolve(missing_fact, **resolve_kwargs)
        if resolution.decision != "CALL_MCP":
            return {
                "decision": resolution.decision,
                "missing_fact": missing_fact,
                "reason_code": resolution.reason_code,
                "note": resolution.note,
                "envelope": None,
            }
        if self._query is None:
            return {
                "decision": "INSUFFICIENT",
                "missing_fact": missing_fact,
                "reason_code": "MCP_GATEWAY_NOT_CONFIGURED",
                "note": "MCP SourceGateway 未注入，降级为不调用。",
                "envelope": None,
            }
        started = time.perf_counter()
        try:
            envelope = self._query(
                resolution.source_id,
                request,
                principal_id=principal_id,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            envelope_dict = envelope.model_dump(mode="json") if hasattr(envelope, "model_dump") \
                else dict(envelope)
            # 注入门禁：清洗投影内容
            sanitized, removed = sanitize_mcp_content(envelope_dict.get("content_projection"))
            envelope_dict["content_projection"] = sanitized
            redactions = dict(envelope_dict.get("redactions") or {})
            redactions["injection_removed"] = removed
            envelope_dict["redactions"] = redactions
            self._resolver._ledger.record(
                resolution.source_id, ok=True, latency_ms=latency_ms,
                result_bytes=int(redactions.get("projected_bytes") or 0),
            )
            return {
                "decision": "CALL_MCP",
                "missing_fact": missing_fact,
                "source_id": resolution.source_id,
                "operation": resolution.operation,
                "envelope": envelope_dict,
            }
        except Exception as exc:  # noqa: BLE001 — 失败降级不阻断
            latency_ms = (time.perf_counter() - started) * 1000
            self._resolver._ledger.record(
                resolution.source_id, ok=False, latency_ms=latency_ms, result_bytes=0,
            )
            return {
                "decision": "MCP_FAILED",
                "missing_fact": missing_fact,
                "source_id": resolution.source_id,
                "reason_code": f"{type(exc).__name__}",
                "note": str(exc)[:300],
                "envelope": None,
            }

    def ledger_summary(self, source_id: str) -> dict[str, Any]:
        return self._resolver._ledger.summary(source_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)

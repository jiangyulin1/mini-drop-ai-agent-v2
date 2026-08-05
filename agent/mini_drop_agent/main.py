"""Mini-Drop Agent：gRPC 客户端，心跳拉取任务并执行采集。

启动流程：
  config ← 环境变量
  → InitAgent.RegisterAgent（注册元数据）
  → loop:
      → HealthCheck.Do（心跳 + 拉取任务）
      → 如有任务 → 执行采集 → Hotmethod.NotifyResult（上报结果）
      → sleep 5s
"""

from __future__ import annotations

import server.app._env  # noqa: F401 — 自动加载 .env

import json
import multiprocessing
import os
import queue
import re
import shutil
import signal
import socket
import time
from dataclasses import replace
from typing import Any

import grpc

from mini_drop_observability.tracing import configure_tracing, shutdown_tracing, start_span
from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.continuous import ContinuousCollector
from agent.mini_drop_agent.collectors.ebpf import EBPFCollector
from agent.mini_drop_agent.collectors.java_async import JavaAsyncProfilerCollector
from agent.mini_drop_agent.collectors.memory import MemoryCollector
from agent.mini_drop_agent.collectors.perf import PerfCollector
from agent.mini_drop_agent.collectors.pprof import PprofCollector
from agent.mini_drop_agent.collectors.pyspy import PySpyCollector
from agent.mini_drop_agent.collectors.sys_metrics import SysMetricsCollector
from agent.mini_drop_agent.artifact_upload import maybe_upload_artifacts
from agent.mini_drop_agent.connection import GrpcConnection
from agent.mini_drop_agent.config import AgentConfig, load_config
from agent.mini_drop_agent.logging_utils import log_event
from agent.mini_drop_agent.metrics import ProcessStatsSampler
from agent.mini_drop_agent.result_spool import ResultSpool
from server.app.generated import (
    healthcheck_pb2,
    healthcheck_pb2_grpc,
    hotmethod_pb2,
    hotmethod_pb2_grpc,
    init_pb2,
    init_pb2_grpc,
)

# ── 采集器注册 ────────────────────────────────────────────────────

COLLECTORS = {
    "perf_cpu": PerfCollector(),
    "ebpf_io": EBPFCollector(),
    "pyspy": PySpyCollector(),
    "continuous_perf": ContinuousCollector(),
    "java_async": JavaAsyncProfilerCollector(),
    "go_pprof": PprofCollector(),
    "memory_smaps": MemoryCollector(),
    "sys_metrics": SysMetricsCollector(),
}

CAPABILITIES = sorted(COLLECTORS.keys())


def _detect_capabilities() -> list[str]:
    """Report only collectors that this Agent can execute locally."""

    available = {"go_pprof", "memory_smaps", "sys_metrics"}
    if shutil.which("perf"):
        available.update({"perf_cpu", "continuous_perf"})
    if shutil.which("bpftrace"):
        available.add("ebpf_io")
    if PySpyCollector._find_pyspy():
        available.add("pyspy")
    if JavaAsyncProfilerCollector._find_profiler():
        available.add("java_async")
    return sorted(available & set(COLLECTORS))


# ── 任务执行 ───────────────────────────────────────────────────────


def _run_collector(
    task_payload: dict[str, Any],
    config: AgentConfig | None = None,
    agent_main_pid: int = 0,
) -> tuple[bool, str, list[dict[str, Any]]]:
    """执行采集任务：构造 CollectorTask 后分发到注册的采集器。

    如果 collector_type 不在 COLLECTORS 中，明确上报失败。
    输入值经过安全裁剪防止资源耗尽。
    """
    collector_type = task_payload.get("collector_type", "perf_cpu")
    collector = COLLECTORS.get(collector_type)
    if collector is None:
        return False, f"collector {collector_type} 未在此 Agent 构建中注册", []

    # 安全裁剪：防止服务器下发恶意参数
    task_id = task_payload.get("id", "")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", task_id or ""):
        return False, f"非法 task_id: {task_id!r}", []

    target_pid = task_payload.get("target_pid", 0)
    if not isinstance(target_pid, int) or target_pid <= 0:
        return False, f"无效的 target_pid: {target_pid}", []
    # 自剖析守卫：本函数运行在 collector worker 子进程中，os.getpid() 是
    # worker 的 PID 而非 Agent 主进程 PID。agent_main_pid 在 spawn 前由
    # 主进程注入，两者都拒绝，守卫才真正生效。
    if target_pid == os.getpid() or (agent_main_pid and target_pid == agent_main_pid):
        return False, "拒绝自剖析请求 (target_pid 与 Agent 自身 PID 相同)", []

    sample_rate = max(1, min(task_payload.get("sample_rate", 99), 10000))
    duration_sec = max(1, min(task_payload.get("duration_sec", 15), 600))

    collector_task = CollectorTask(
        id=task_payload.get("id", ""),
        collector_type=collector_type,
        target_pid=target_pid,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        options=task_payload.get("request_params", {}).get("options", {}),
    )
    with start_span(
        "mini_drop.collector.run",
        traceparent=task_payload.get("traceparent"),
        kind="consumer",
        attributes={
            "mini_drop.task.id": task_payload.get("id", ""),
            "mini_drop.attempt.id": task_payload.get("attempt_id", ""),
            "mini_drop.collector.type": collector_type,
            "process.pid": target_pid,
        },
    ):
        result = collector.collect(collector_task)
        artifacts = result.artifacts
        if result.ok and config is not None:
            try:
                artifacts = maybe_upload_artifacts(
                    task_payload["id"],
                    result.artifacts,
                    config,
                    attempt_id=task_payload.get("attempt_id", ""),
                )
            except Exception as exc:
                return False, f"artifact upload failed: {exc}", result.artifacts
        return result.ok, result.reason, artifacts


def _collector_error_code(ok: bool, reason: str, *, cancelled: bool = False) -> str:
    """Map human-readable collector failures to a stable wire error code."""

    if ok:
        return ""
    if cancelled:
        return "TASK_CANCELLED"
    normalized = (reason or "").lower()
    if "target_pid" in normalized or "self-analysis" in normalized or "自剖析" in normalized:
        return "INVALID_TARGET_PID"
    if "not registered" in normalized or "未在" in normalized or "unavailable" in normalized:
        return "COLLECTOR_UNAVAILABLE"
    if "artifact upload failed" in normalized:
        return "ARTIFACT_UPLOAD_FAILED"
    if "worker crashed" in normalized:
        return "COLLECTOR_WORKER_CRASHED"
    return "COLLECTOR_FAILED"


# ── gRPC 客户端 ───────────────────────────────────────────────────


def _register(stub: init_pb2_grpc.InitAgentStub, config: AgentConfig) -> None:
    """通过 gRPC InitAgent.RegisterAgent 注册自身元数据。"""
    stub.RegisterAgent(
        init_pb2.RegisterAgentRequest(
            agent_id=config.agent_id,
            hostname=socket.gethostname(),
            ip_addr=config.agent_ip_addr,
            version="0.1.0",
            os_info=_os_info(),
            capabilities=_detect_capabilities(),
        ),
        timeout=5,
    )


def _fetch_config(stub: init_pb2_grpc.InitAgentStub, config: AgentConfig) -> AgentConfig:
    resp = stub.FetchConfig(init_pb2.FetchConfigRequest(agent_id=config.agent_id), timeout=5)
    return _apply_cos_config(config, resp.cos_config)


def _apply_cos_config(config: AgentConfig, cos_config) -> AgentConfig:
    if not getattr(cos_config, "endpoint", ""):
        return config
    return replace(
        config,
        minio_endpoint=cos_config.endpoint,
        minio_access_key=cos_config.access_key or config.minio_access_key,
        minio_secret_key=cos_config.secret_key or config.minio_secret_key,
        minio_bucket=cos_config.bucket or config.minio_bucket,
    )


def _heartbeat(
    stub: healthcheck_pb2_grpc.HealthCheckStub,
    config: AgentConfig,
    sampler: ProcessStatsSampler | None = None,
    busy: bool = False,
    active_task: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """通过 gRPC HealthCheck.Do 发送心跳，返回待执行任务或 None。"""
    request = healthcheck_pb2.HealthCheckRequest(
        agent_id=config.agent_id,
        hostname=socket.gethostname(),
        ip_addr=config.agent_ip_addr,
        agent_version="0.1.0",
        busy=busy,
        active_task_id=(active_task or {}).get("id", ""),
        active_attempt_id=(active_task or {}).get("attempt_id", ""),
    )
    if sampler is not None:
        _fill_pid_stats(request.self_pstats, sampler.sample_self())
        _fill_pid_stats(request.children_pstats, sampler.sample_children())
    resp = stub.Do(
        request,
        timeout=5,
    )
    if getattr(resp, "cancel_active_task", False):
        return {
            "directive": "cancel",
            "reason": getattr(resp, "cancel_reason", "") or "Server requested cancellation",
        }
    if resp.pending and resp.task_desc.task_id:
        task_type = resp.task_desc.task_type
        # task_type 优先路由（如 MemCheck → memory_smaps）
        if task_type in _TASK_TYPE_COLLECTOR:
            collector_type = _TASK_TYPE_COLLECTOR[task_type]
        else:
            collector_type = _profiler_to_collector(resp.task_desc.profiler_type)
        options: dict[str, Any] = {}
        if resp.task_desc.options_json:
            try:
                decoded_options = json.loads(resp.task_desc.options_json)
                if isinstance(decoded_options, dict):
                    options = decoded_options
            except (json.JSONDecodeError, TypeError):
                pass
        if resp.task_desc.sample_argv.callgraph:
            options["callgraph"] = resp.task_desc.sample_argv.callgraph
        if resp.task_desc.sample_argv.event:
            options["event"] = resp.task_desc.sample_argv.event
        options["subprocess"] = resp.task_desc.sample_argv.subprocess
        return {
            "id": resp.task_desc.task_id,
            "attempt_id": getattr(resp.task_desc, "attempt_id", ""),
            "request_id": getattr(resp.task_desc, "request_id", ""),
            "traceparent": getattr(resp.task_desc, "traceparent", ""),
            "collector_type": collector_type,
            "target_pid": resp.task_desc.sample_argv.pid,
            "sample_rate": resp.task_desc.sample_argv.hz,
            "duration_sec": resp.task_desc.sample_argv.duration,
            "request_params": {
                "options": options,
            },
        }
    return None


def _collector_worker(work_queue, result_queue, config: AgentConfig, agent_main_pid: int = 0) -> None:
    """Run collectors away from the heartbeat loop."""
    configure_tracing("mini-drop-agent-collector")
    if hasattr(os, "setsid"):
        os.setsid()
    while True:
        task = work_queue.get()
        try:
            if task is None:
                return
            ok, reason, artifacts = _run_collector(task, config, agent_main_pid)
            result_queue.put((task, ok, reason, artifacts))
        except Exception as exc:
            if task is not None:
                result_queue.put((task, False, f"collector worker crashed: {exc}", []))
        finally:
            work_queue.task_done()


def _notify_result(
    stub: hotmethod_pb2_grpc.HotmethodStub,
    task_id: str,
    ok: bool,
    reason: str,
    artifacts: list[dict],
    *,
    attempt_id: str = "",
    cancelled: bool = False,
    exit_code: int = 0,
    error_code: str = "",
    request_id: str = "",
    traceparent: str = "",
    resource_usage: dict[str, Any] | None = None,
) -> None:
    """通过 gRPC Hotmethod.NotifyResult 上报采集结果。"""
    if ok:
        stub.NotifyResult(
            hotmethod_pb2.TaskResult(
                task_id=task_id,
                attempt_id=attempt_id,
                error_message="",
                artifact_type="raw",
                artifact_metadata_json=json.dumps(artifacts),
                result_message=reason,
                runner_version="0.1.0",
                cancelled=cancelled,
                exit_code=exit_code,
                error_code=error_code,
                request_id=request_id,
                traceparent=traceparent,
                resource_usage_json=json.dumps(resource_usage or {}, separators=(",", ":")),
            ),
            timeout=10,
        )
    else:
        stub.NotifyResult(
            hotmethod_pb2.TaskResult(
                task_id=task_id,
                attempt_id=attempt_id,
                error_message=reason,
                runner_version="0.1.0",
                cancelled=cancelled,
                exit_code=exit_code,
                error_code=error_code or _collector_error_code(False, reason, cancelled=cancelled),
                request_id=request_id,
                traceparent=traceparent,
                resource_usage_json=json.dumps(resource_usage or {}, separators=(",", ":")),
            ),
            timeout=10,
        )


def _drain_result_spool(conn: GrpcConnection, spool: ResultSpool) -> int:
    """Replay durable results until the first unavailable-server failure."""

    acknowledged = 0
    for envelope in spool.pending():
        try:
            conn.call_with_retry(
                lambda envelope=envelope: _notify_result(
                    hotmethod_pb2_grpc.HotmethodStub(conn.channel),
                    envelope["task_id"],
                    envelope["ok"],
                    envelope["reason"],
                    envelope["artifacts"],
                    attempt_id=envelope.get("attempt_id", ""),
                    cancelled=envelope.get("cancelled", False),
                    exit_code=envelope.get("exit_code", 0),
                    error_code=envelope.get("error_code", ""),
                    request_id=envelope.get("request_id", ""),
                    traceparent=envelope.get("traceparent", ""),
                    resource_usage=envelope.get("resource_usage", {}),
                )
            )
        except grpc.RpcError as exc:
            log_event(
                "warning",
                "result_spool_replay_deferred",
                task_id=envelope["task_id"],
                code=exc.code(),
                details=exc.details(),
            )
            break
        spool.acknowledge(envelope["task_id"], envelope.get("attempt_id", ""))
        acknowledged += 1
        log_event(
            "info",
            "result_spool_acknowledged",
            task_id=envelope["task_id"],
            artifact_count=len(envelope["artifacts"]),
        )
    return acknowledged


# ── 主循环 ─────────────────────────────────────────────────────────

_should_exit = False
_signal_count = 0  # 信号计数器：第一次优雅退出，第二次强制终止


def _on_signal(signum, frame):
    global _should_exit, _signal_count
    _signal_count += 1
    if _signal_count >= 2:
        # 第二次信号：强制退出（采集器子进程可能残留，但操作系统会回收）
        log_event("warning", "agent_force_exit", signal=_signal_count)
        os._exit(1)
    _should_exit = True
    log_event("info", "agent_graceful_shutdown", signal=_signal_count,
              hint="再次发送 SIGTERM 强制退出")


def _init_register_with_retry(conn, config: AgentConfig, max_retries: int = 5, backoff_sec: float = 2.0) -> AgentConfig:
    """注册 Agent 并拉取配置，支持指数退避重试。

    生产环境中 Server 可能尚未就绪，重试避免 Agent 启动即崩溃。
    """
    last_exc = None
    delay = backoff_sec
    for attempt in range(max_retries + 1):
        try:
            init_stub = init_pb2_grpc.InitAgentStub(conn.channel)
            _register(init_stub, config)
            config = _fetch_config(init_stub, config)
            log_event("info", "agent_registered", agent_id=config.agent_id, ip_addr=config.agent_ip_addr)
            return config
        except grpc.RpcError as exc:
            last_exc = exc
            if attempt >= max_retries:
                raise
            log_event(
                "warning",
                "agent_init_retry",
                attempt=attempt + 1,
                max_retries=max_retries,
                code=exc.code(),
                delay=delay,
            )
            time.sleep(delay)
            delay *= 2
    raise last_exc


def main() -> None:
    global _should_exit
    configure_tracing("mini-drop-agent")
    config = load_config()
    conn = GrpcConnection(config.server_grpc_addr, auth_token=config.grpc_auth_token)
    sampler = ProcessStatsSampler()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # 初始化注册 + 拉取配置（带重试）
    config = _init_register_with_retry(conn, config)
    result_spool = ResultSpool(config.result_spool_dir)

    mp_context = multiprocessing.get_context("spawn")
    work_queue, result_queue, worker = _start_collector_process(mp_context, config)
    active_task: dict[str, Any] | None = None

    def spool_finished(
        spool: ResultSpool,
        finished: dict[str, Any],
        ok: bool,
        reason: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        """将完成的采集结果写入 spool、清 active_task、回执队列。"""
        nonlocal active_task
        try:
            spool.save(
                finished["id"],
                ok,
                reason,
                artifacts,
                attempt_id=finished.get("attempt_id", ""),
                error_code=_collector_error_code(ok, reason),
                request_id=finished.get("request_id", ""),
                traceparent=finished.get("traceparent", ""),
            )
            log_event(
                "info",
                "result_spooled",
                task_id=finished["id"],
                artifact_count=len(artifacts),
                request_id=finished.get("request_id", ""),
            )
        except (OSError, ValueError, TypeError) as exc:
            log_event(
                "error",
                "result_spool_write_failed",
                task_id=finished["id"],
                error=str(exc),
            )
        finally:
            if active_task and active_task.get("id") == finished.get("id"):
                active_task = None
            result_queue.task_done()

    while not _should_exit:
        # worker 看门狗：worker 被 OOM-kill / 意外信号退出时，检测并重启。
        # 必须放在 result_queue 读取之前——worker 死亡后旧队列读取会抛
        # EOFError，且不重启的话心跳恒 busy、后续任务全部被丢弃。
        if not worker.is_alive():
            log_event("error", "collector_worker_died")
            if active_task is not None:
                dead = active_task
                try:
                    result_spool.save(
                        dead["id"],
                        False,
                        "collector worker crashed unexpectedly",
                        [],
                        attempt_id=dead.get("attempt_id", ""),
                        error_code="COLLECTOR_WORKER_CRASHED",
                        request_id=dead.get("request_id", ""),
                        traceparent=dead.get("traceparent", ""),
                    )
                except (OSError, ValueError, TypeError) as exc:
                    log_event(
                        "error",
                        "result_spool_write_failed",
                        task_id=dead["id"],
                        error=str(exc),
                    )
                active_task = None
            work_queue, result_queue, worker = _start_collector_process(mp_context, config)
            continue

        try:
            finished_task, ok, reason, artifacts = result_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            spool_finished(result_spool, finished_task, ok, reason, artifacts)

        _drain_result_spool(conn, result_spool)

        try:
            task = conn.call_with_retry(
                lambda: _heartbeat(
                    healthcheck_pb2_grpc.HealthCheckStub(conn.channel),
                    config,
                    sampler,
                    busy=active_task is not None,
                    active_task=active_task,
                )
            )
        except grpc.RpcError as exc:
            log_event("error", "heartbeat_failed", code=exc.code(), details=exc.details())
            time.sleep(config.heartbeat_interval_sec)
            continue

        if task is None:
            time.sleep(config.heartbeat_interval_sec)
            continue

        if task.get("directive") == "cancel":
            if active_task is not None:
                # 取消竞态修复：采集可能已完成但结果尚未被主循环取走，
                # 此时直接杀 worker 会丢掉已采集产物。先排空结果队列：
                # 若结果已就绪则按成功结果处理，否则才走取消路径。
                drained = None
                try:
                    drained = result_queue.get_nowait()
                except (queue.Empty, EOFError):
                    pass
                if drained is not None and drained[0].get("id") == active_task.get("id"):
                    finished_task, ok, reason, artifacts = drained
                    spool_finished(result_spool, finished_task, ok, reason, artifacts)
                    _terminate_collector_process(worker)
                    work_queue, result_queue, worker = _start_collector_process(mp_context, config)
                    time.sleep(config.heartbeat_interval_sec)
                    continue

                cancelled_task = active_task
                _terminate_collector_process(worker)
                result_spool.save(
                    cancelled_task["id"],
                    False,
                    task.get("reason") or "Server requested cancellation",
                    [],
                    attempt_id=cancelled_task.get("attempt_id", ""),
                    cancelled=True,
                    exit_code=-15,
                    error_code="TASK_CANCELLED",
                    request_id=cancelled_task.get("request_id", ""),
                    traceparent=cancelled_task.get("traceparent", ""),
                )
                active_task = None
                work_queue, result_queue, worker = _start_collector_process(mp_context, config)
            time.sleep(config.heartbeat_interval_sec)
            continue

        if active_task is not None:
            log_event(
                "warning",
                "task_received_while_busy",
                active_task_id=active_task.get("id"),
                dropped_task_id=task.get("id"),
            )
            time.sleep(config.heartbeat_interval_sec)
            continue

        log_event(
            "info",
            "task_pulled",
            task_id=task["id"],
            collector=task["collector_type"],
            pid=task["target_pid"],
        )
        active_task = task
        work_queue.put(task)

        time.sleep(config.heartbeat_interval_sec)

    if worker.is_alive():
        work_queue.put(None)
        worker.join(timeout=5)
    if worker.is_alive():
        _terminate_collector_process(worker)
    conn.close()
    shutdown_tracing()


def _start_collector_process(mp_context, config: AgentConfig):
    work_queue = mp_context.JoinableQueue(maxsize=1)
    result_queue = mp_context.JoinableQueue()
    # agent_main_pid 在主进程内取 os.getpid() 并注入 worker：
    # 自剖析守卫需要对比"Agent 主进程 PID"（见 _run_collector）。
    worker = mp_context.Process(
        target=_collector_worker,
        args=(work_queue, result_queue, config, os.getpid()),
        name="collector-worker",
        daemon=True,
    )
    worker.start()
    return work_queue, result_queue, worker


def _terminate_collector_process(worker, grace_sec: float = 3.0) -> None:
    """Terminate the worker and its external Runner process group."""

    if not worker.is_alive():
        worker.join(timeout=0.1)
        return
    if os.name == "posix" and hasattr(os, "killpg"):
        try:
            os.killpg(worker.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        worker.terminate()
    worker.join(timeout=grace_sec)
    if worker.is_alive():
        if os.name == "posix" and hasattr(os, "killpg"):
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            worker.kill()
        worker.join(timeout=1)


# ── 辅助 ───────────────────────────────────────────────────────────


# profiler_type → collector_type 映射（与 proto hotmethod.proto + healthcheck_service.py 对齐）
_PROFILER_TO_COLLECTOR: dict[int, str] = {
    0: "perf_cpu",        # perf
    1: "java_async",      # async-profiler (Java)
    2: "go_pprof",         # pprof (Go)
    3: "pyspy",            # py-spy (Python)
    4: "ebpf_io",          # bpftrace (eBPF)
    5: "memory_smaps",     # memory smaps
    6: "sys_metrics",      # system multi-metrics
    7: "continuous_perf",  # continuous perf
}

# task_type → collector_type 映射（MemCheck 等需要特殊路由的场景）
_TASK_TYPE_COLLECTOR: dict[int, str] = {
    4: "memory_smaps",     # MemCheck
}


def _profiler_to_collector(profiler_type: int) -> str:
    """根据 profiler_type 获取 collector_type 字符串。"""
    return _PROFILER_TO_COLLECTOR.get(profiler_type, "perf_cpu")


def _fill_pid_stats(message, stats: dict[str, Any]) -> None:
    message.cpu_percent = float(stats.get("cpu_percent", 0.0) or 0.0)
    message.rss_mb = float(stats.get("rss_mb", 0.0) or 0.0)
    message.read_kb_s = float(stats.get("read_kb_s", 0.0) or 0.0)
    message.write_kb_s = float(stats.get("write_kb_s", 0.0) or 0.0)
    message.children_count = int(stats.get("children_count", 0) or 0)


def _os_info() -> str:
    try:
        with open("/proc/version", "r") as fh:
            return fh.readline().strip()
    except FileNotFoundError:
        return "unknown"


if __name__ == "__main__":
    main()

"""TaskKind metadata exposed to API and Web clients.

The collector implementation remains owned by the Agent.  This registry is the
control-plane contract for forms, validation hints, capability matching and
result presentation, so browser clients do not need to duplicate parameter
bounds and defaults.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from server.app.schemas import MAX_SAMPLE_RATE, MAX_TASK_DURATION_SEC


_COMMON_DURATION = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_TASK_DURATION_SEC,
    "default": 15,
    "unit": "秒",
    "help": "采集持续时间；任务运行期间目标进程必须保持存活。",
}

_COMMON_SAMPLE_RATE = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_SAMPLE_RATE,
    "default": 99,
    "unit": "Hz",
    "help": "每秒采样频率；频率越高，结果越精细，额外开销也越大。",
}


def _kind(
    key: str,
    display_name: str,
    result_label: str,
    description: str,
    *,
    color: str,
    flamegraph: bool,
    default_duration: int = 15,
    default_sample_rate: int = 99,
    permission_requirements: list[str] | None = None,
) -> dict[str, Any]:
    duration = {**_COMMON_DURATION, "default": default_duration}
    sample_rate = {**_COMMON_SAMPLE_RATE, "default": default_sample_rate}
    return {
        "key": key,
        "display_name": display_name,
        "result_label": result_label,
        "description": description,
        "capability": key,
        "parameter_schema": {
            "target_pid": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4194304,
                "help": "目标 Linux 进程 PID。",
            },
            "duration_sec": duration,
            "sample_rate": sample_rate,
        },
        "defaults": {
            "duration_sec": default_duration,
            "sample_rate": default_sample_rate,
        },
        "permission_requirements": permission_requirements or ["读取目标进程 /proc 信息"],
        "presentation": {
            "color": color,
            "flamegraph": flamegraph,
        },
    }


TASK_KINDS: tuple[dict[str, Any], ...] = (
    _kind(
        "perf_cpu",
        "CPU 火焰图",
        "交互式 CPU 火焰图 + TopN 热点",
        "使用 perf 采样目标进程调用栈，适合定位 CPU 热点、锁竞争和异常调用路径。",
        color="blue",
        flamegraph=True,
        permission_requirements=["perf_event_open", "读取目标进程调用栈"],
    ),
    _kind(
        "pyspy",
        "Python 火焰图",
        "Python 调用栈火焰图",
        "使用 py-spy 采样 Python 进程，无需修改应用代码。",
        color="purple",
        flamegraph=True,
        permission_requirements=["ptrace 或等效进程采样权限"],
    ),
    _kind(
        "continuous_perf",
        "持续火焰图",
        "按窗口切分的火焰图 + 趋势",
        "周期采集多个时间窗口，适合观察热点随时间变化。",
        color="cyan",
        flamegraph=True,
        default_duration=60,
        default_sample_rate=49,
        permission_requirements=["perf_event_open", "读取目标进程调用栈"],
    ),
    _kind(
        "java_async",
        "Java 火焰图",
        "async-profiler Java 火焰图",
        "采集 JVM 进程的 CPU 调用栈并生成可浏览的 HTML 火焰图。",
        color="magenta",
        flamegraph=True,
        permission_requirements=["async-profiler", "目标 JVM attach 权限"],
    ),
    _kind(
        "go_pprof",
        "Go pprof",
        "Go pprof 数据（环境支持时生成火焰图）",
        "抓取 Go pprof CPU Profile，可下载原始数据并在工具链可用时生成火焰图。",
        color="geekblue",
        flamegraph=True,
        permission_requirements=["目标进程开放 pprof 端点"],
    ),
    _kind(
        "ebpf_io",
        "I/O 延迟图",
        "eBPF I/O 延迟直方图",
        "使用 eBPF/bpftrace 观察块设备延迟分布；该采集不会生成 CPU 火焰图。",
        color="green",
        flamegraph=False,
        default_sample_rate=11,
        permission_requirements=["CAP_BPF 或 root", "bpftrace", "内核 BPF 支持"],
    ),
    _kind(
        "memory_smaps",
        "内存趋势",
        "RSS / PSS / Swap 趋势图",
        "采样进程 smaps，适合定位内存增长、Swap 和疑似泄漏。",
        color="orange",
        flamegraph=False,
        default_sample_rate=11,
        permission_requirements=["读取目标进程 /proc/PID/smaps"],
    ),
    _kind(
        "sys_metrics",
        "系统指标",
        "CPU / 负载 / 线程 / FD / 网络多维图",
        "低开销采集主机和进程指标；该采集不会生成调用栈火焰图。",
        color="gold",
        flamegraph=False,
        default_sample_rate=11,
        permission_requirements=["读取 /proc 系统与进程指标"],
    ),
)


def list_task_kinds(capabilities: set[str] | None = None) -> list[dict[str, Any]]:
    """Return a defensive copy, optionally limited to Agent capabilities."""

    items = TASK_KINDS
    if capabilities is not None:
        items = tuple(item for item in items if item["capability"] in capabilities)
    return deepcopy(list(items))

"""Linux host/process/container metrics collector using the sys_metrics.v2 contract."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class SysMetricsCollector:
    OUTPUT_BASE = "/tmp/mini-drop"
    SAMPLE_INTERVAL_SEC = 1.0
    MAX_SAMPLES = 120
    SCHEMA_VERSION = "sys_metrics.v2"

    def collect(self, task: CollectorTask) -> CollectorResult:
        if not self._pid_exists(task.target_pid):
            return CollectorResult(ok=False, reason=f"目标 PID {task.target_pid} 不存在")
        mode = task.options.get("mode", "timeseries")
        if mode not in {"snapshot", "timeseries"}:
            return CollectorResult(ok=False, reason=f"无效的 mode: {mode}，支持 snapshot 或 timeseries")
        duration_sec = max(1, min(task.duration_sec, self.MAX_SAMPLES))
        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)

        samples: list[dict[str, Any]] = []
        previous_host_cpu = self._read_proc_stat_total()
        deadline = time.time() + duration_sec
        while time.time() < deadline:
            if not self._pid_exists(task.target_pid):
                break
            timestamp = time.time()
            host_cpu = self._read_proc_stat_total()
            samples.append({
                "ts": timestamp,
                "host_cpu_ticks": host_cpu,
                "host_cpu": self._cpu_ratios(previous_host_cpu, host_cpu),
                "load": self._read_loadavg(),
                "host_memory": self._read_meminfo(),
                "psi": self._read_psi(),
                "process": self._read_process_metrics(task.target_pid),
                "container": self._read_cgroup_metrics(task.target_pid),
                "host_network": self._read_network_dev(),
            })
            previous_host_cpu = host_cpu
            if mode == "snapshot" or len(samples) >= self.MAX_SAMPLES:
                break
            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(min(self.SAMPLE_INTERVAL_SEC, remaining))

        if not samples:
            return CollectorResult(ok=False, reason="未能采集到系统指标样本")

        normalized = self._compute_v2(task.target_pid, samples)
        legacy = self._legacy_summary(normalized)
        output = {
            "schema_version": self.SCHEMA_VERSION,
            "task_id": task.id,
            "mode": mode,
            "duration_sec": duration_sec,
            "sample_count": len(samples),
            "host": normalized["host"],
            "process": normalized["process"],
            "container": normalized["container"],
            # v1 readers keep working during the rolling upgrade.
            "summary": legacy,
            "samples": samples,
        }
        output_path = os.path.join(output_dir, "sys_metrics.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=False, default=str)
        return CollectorResult(
            ok=True,
            reason=(
                f"系统指标采集完成: {len(samples)} 个样本 | "
                f"host CPU={legacy['avg_cpu_user_pct'] + legacy['avg_cpu_sys_pct']:.1f}% | "
                f"process CPU={normalized['process']['cpu']['normalized_core_usage']:.2f} cores | "
                f"RSS={normalized['process']['memory']['rss_bytes']} bytes"
            ),
            artifacts=[{
                "artifact_type": "sys_metrics",
                "filename": "sys_metrics.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {"schema_version": self.SCHEMA_VERSION, **legacy},
            }],
        )

    @staticmethod
    def _read_proc_stat_total() -> dict[str, int]:
        names = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
        try:
            with open("/proc/stat", "r", encoding="utf-8") as handle:
                parts = handle.readline().split()
            if not parts or parts[0] != "cpu":
                return {}
            return {name: int(parts[index + 1]) if len(parts) > index + 1 else 0 for index, name in enumerate(names)}
        except (FileNotFoundError, PermissionError, ValueError):
            return {}

    @staticmethod
    def _cpu_ratios(previous: dict[str, int], current: dict[str, int]) -> dict[str, float]:
        if not previous or not current:
            return {}
        delta = {name: max(0, current.get(name, 0) - previous.get(name, 0)) for name in current}
        total = sum(delta.values())
        if total <= 0:
            return {}
        return {
            "user_ratio": (delta.get("user", 0) + delta.get("nice", 0)) / total,
            "system_ratio": (delta.get("system", 0) + delta.get("irq", 0) + delta.get("softirq", 0)) / total,
            "iowait_ratio": delta.get("iowait", 0) / total,
            "idle_ratio": delta.get("idle", 0) / total,
        }

    @staticmethod
    def _read_loadavg() -> dict[str, float]:
        try:
            with open("/proc/loadavg", "r", encoding="utf-8") as handle:
                parts = handle.readline().split()
            return {"load1": float(parts[0]), "load5": float(parts[1]), "load15": float(parts[2]),
                    "load1m": float(parts[0]), "load5m": float(parts[1]), "load15m": float(parts[2])}
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            return {}

    @staticmethod
    def _parse_proc_stat(line: str) -> dict[str, int]:
        """Parse /proc/<pid>/stat without splitting a comm field containing spaces."""
        left = line.find("(")
        right = line.rfind(")")
        if left < 0 or right <= left:
            return {}
        prefix = line[:left].strip()
        remainder = line[right + 1:].strip().split()
        if len(remainder) < 22:
            return {}
        try:
            return {
                "pid": int(prefix),
                "utime_ticks": int(remainder[11]),
                "stime_ticks": int(remainder[12]),
                "num_threads": int(remainder[17]),
                "start_time_ticks": int(remainder[19]),
                "vsize_bytes": int(remainder[20]),
                "rss_pages": int(remainder[21]),
            }
        except (ValueError, IndexError):
            return {}

    @classmethod
    def _read_process_metrics(cls, pid: int) -> dict[str, Any]:
        result: dict[str, Any] = {"pid": pid}
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
                result.update(cls._parse_proc_stat(handle.read()))
        except (FileNotFoundError, PermissionError):
            pass
        try:
            result["fd_count"] = len(os.listdir(f"/proc/{pid}/fd"))
        except (FileNotFoundError, PermissionError, OSError):
            pass
        try:
            with open(f"/proc/{pid}/io", "r", encoding="utf-8") as handle:
                for line in handle:
                    name, raw = line.split(":", 1)
                    if name in {"read_bytes", "write_bytes", "rchar", "wchar"}:
                        result[name] = int(raw.strip())
        except (FileNotFoundError, PermissionError, ValueError):
            pass
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        result["rss_bytes"] = int(line.split()[1]) * 1024
                        result["vmrss_kb"] = int(line.split()[1])
                    elif line.startswith("voluntary_ctxt_switches:"):
                        result["voluntary_switches"] = int(line.split(":", 1)[1])
                    elif line.startswith("nonvoluntary_ctxt_switches:"):
                        result["nonvoluntary_switches"] = int(line.split(":", 1)[1])
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            pass
        return result

    @staticmethod
    def _read_meminfo() -> dict[str, int]:
        values: dict[str, int] = {}
        wanted = {"MemTotal": "total_bytes", "MemAvailable": "available_bytes"}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    name, raw = line.split(":", 1)
                    if name in wanted:
                        values[wanted[name]] = int(raw.split()[0]) * 1024
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            pass
        return values

    @staticmethod
    def _read_psi() -> dict[str, float]:
        result: dict[str, float] = {}
        for resource in ("cpu", "memory", "io"):
            try:
                with open(f"/proc/pressure/{resource}", "r", encoding="utf-8") as handle:
                    some = next((line for line in handle if line.startswith("some ")), "")
                tokens = dict(token.split("=", 1) for token in some.split()[1:] if "=" in token)
                result[f"{resource}_some_avg10"] = float(tokens.get("avg10", 0))
            except (FileNotFoundError, PermissionError, ValueError):
                continue
        return result

    @staticmethod
    def _read_cgroup_metrics(pid: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as handle:
                path = next((line.rstrip().split(":", 2)[2] for line in handle if line.startswith("0::")), "/")
            root = os.path.join("/sys/fs/cgroup", path.lstrip("/"))
            def read(name: str) -> str:
                with open(os.path.join(root, name), "r", encoding="utf-8") as item:
                    return item.read().strip()
            memory_max = read("memory.max")
            result["memory_limit_bytes"] = None if memory_max == "max" else int(memory_max)
            result["memory_current_bytes"] = int(read("memory.current"))
            quota, period = read("cpu.max").split()[:2]
            result["cpu_quota_cores"] = None if quota == "max" else int(quota) / int(period)
            io_totals = {"rbytes": 0, "wbytes": 0}
            for line in read("io.stat").splitlines():
                for token in line.split()[1:]:
                    name, raw = token.split("=", 1)
                    if name in io_totals:
                        io_totals[name] += int(raw)
            result["io"] = {"read_bytes": io_totals["rbytes"], "write_bytes": io_totals["wbytes"]}
            result["cgroup_path"] = path
        except (FileNotFoundError, PermissionError, ValueError, StopIteration):
            pass
        return result

    @staticmethod
    def _read_network_dev() -> dict[str, int]:
        rx_total = tx_total = 0
        try:
            with open("/proc/net/dev", "r", encoding="utf-8") as handle:
                for line in handle:
                    if ":" not in line:
                        continue
                    parts = line.split(":", 1)[1].split()
                    rx_total += int(parts[0])
                    tx_total += int(parts[8])
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            pass
        return {"rx_bytes": rx_total, "tx_bytes": tx_total}

    @classmethod
    def _compute_v2(cls, pid: int, samples: list[dict[str, Any]]) -> dict[str, Any]:
        first, last = samples[0], samples[-1]
        dt = max(float(last["ts"] - first["ts"]), 0.0)
        cpu_rows = [row["host_cpu"] for row in samples if row.get("host_cpu")]
        average = lambda name: sum(float(row.get(name, 0)) for row in cpu_rows) / len(cpu_rows) if cpu_rows else 0.0
        loads = [float(row.get("load", {}).get("load1", row.get("load", {}).get("load1m", 0))) for row in samples]
        proc_first, proc_last = first.get("process", {}), last.get("process", {})
        ticks = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
        page_size = int(os.sysconf("SC_PAGE_SIZE")) if hasattr(os, "sysconf") else 4096
        def rate(name: str) -> float:
            return max(0.0, float(proc_last.get(name, 0)) - float(proc_first.get(name, 0))) / dt if dt > 0 else 0.0
        rss_first = float(proc_first.get("rss_bytes", proc_first.get("rss_pages", 0) * page_size))
        rss_last = float(proc_last.get("rss_bytes", proc_last.get("rss_pages", 0) * page_size))
        user_delta = max(0.0, float(proc_last.get("utime_ticks", 0)) - float(proc_first.get("utime_ticks", 0))) / ticks
        system_delta = max(0.0, float(proc_last.get("stime_ticks", 0)) - float(proc_first.get("stime_ticks", 0))) / ticks
        host = {
            "cpu": {"user_ratio": average("user_ratio"), "system_ratio": average("system_ratio"),
                    "iowait_ratio": average("iowait_ratio"), "core_count": os.cpu_count() or 1},
            "load": {"load1": loads[-1] if loads else 0.0,
                     "load5": float(last.get("load", {}).get("load5", last.get("load", {}).get("load5m", 0))),
                     "load1_window_avg": sum(loads) / len(loads) if loads else 0.0,
                     "load1_slope_per_second": (loads[-1] - loads[0]) / dt if len(loads) > 1 and dt > 0 else 0.0},
            "memory": dict(last.get("host_memory", {})),
            "psi": dict(last.get("psi", {})),
            "network": {"scope": "host", "rx_bytes_per_second": cls._counter_rate(first, last, "host_network", "rx_bytes"),
                        "tx_bytes_per_second": cls._counter_rate(first, last, "host_network", "tx_bytes")},
        }
        process = {
            "pid": pid,
            "start_time_ticks": proc_last.get("start_time_ticks"),
            "cpu": {"user_seconds_delta": user_delta, "system_seconds_delta": system_delta,
                    "normalized_core_usage": (user_delta + system_delta) / dt if dt > 0 else 0.0},
            "memory": {"rss_bytes": int(rss_last),
                       "rss_slope_bytes_per_second": (rss_last - rss_first) / dt if dt > 0 else 0.0},
            "fd": {"count": int(proc_last.get("fd_count", 0)), "growth_per_minute": rate("fd_count") * 60},
            "io": {"read_bytes_per_second": rate("read_bytes"), "write_bytes_per_second": rate("write_bytes")},
            "threads": {"count": int(proc_last.get("num_threads", 0)), "growth_per_minute": rate("num_threads") * 60},
        }
        return {"host": host, "process": process, "container": dict(last.get("container", {}))}

    @staticmethod
    def _counter_rate(first: dict[str, Any], last: dict[str, Any], group: str, name: str) -> float:
        dt = float(last["ts"] - first["ts"])
        return max(0.0, float(last.get(group, {}).get(name, 0)) - float(first.get(group, {}).get(name, 0))) / dt if dt > 0 else 0.0

    @staticmethod
    def _legacy_summary(value: dict[str, Any]) -> dict[str, Any]:
        host, process = value["host"], value["process"]
        return {
            "avg_cpu_user_pct": round(host["cpu"]["user_ratio"] * 100, 1),
            "avg_cpu_sys_pct": round(host["cpu"]["system_ratio"] * 100, 1),
            "avg_cpu_iowait_pct": round(host["cpu"]["iowait_ratio"] * 100, 1),
            "host_core_count": host["cpu"]["core_count"],
            "load1m": host["load"]["load1"],
            "load1m_window_avg": host["load"]["load1_window_avg"],
            "load1m_slope_per_second": host["load"]["load1_slope_per_second"],
            "process_cpu_core_usage": process["cpu"]["normalized_core_usage"],
            "thread_count": process["threads"]["count"],
            "thread_growth_per_minute": process["threads"]["growth_per_minute"],
            "fd_count": process["fd"]["count"],
            "fd_growth_per_minute": process["fd"]["growth_per_minute"],
            "fd_trend": "increasing" if process["fd"]["growth_per_minute"] > 0 else "stable",
            "vmrss_mb": round(process["memory"]["rss_bytes"] / 1024 / 1024, 1),
            "vmrss_slope_bytes_per_second": process["memory"]["rss_slope_bytes_per_second"],
            "vmrss_trend": "increasing" if process["memory"]["rss_slope_bytes_per_second"] > 0 else "stable",
            "process_read_bytes_per_second": process["io"]["read_bytes_per_second"],
            "process_write_bytes_per_second": process["io"]["write_bytes_per_second"],
            "net_rx_kbps": host["network"]["rx_bytes_per_second"] / 1024,
            "net_tx_kbps": host["network"]["tx_bytes_per_second"] / 1024,
        }

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @classmethod
    def _compute_summary(cls, samples: list[dict[str, Any]]) -> dict[str, Any]:
        """Compatibility helper retained for v1 unit/integration callers."""
        normalized_samples = []
        for row in samples:
            normalized_samples.append({
                "ts": row["ts"],
                "host_cpu": row.get("host_cpu", row.get("cpu", {})),
                "load": row.get("load", {}),
                "host_memory": row.get("host_memory", {}),
                "psi": row.get("psi", {}),
                "process": row.get("process", {}),
                "container": row.get("container", {}),
                "host_network": row.get("host_network", row.get("network", {})),
            })
        return cls._legacy_summary(cls._compute_v2(0, normalized_samples))

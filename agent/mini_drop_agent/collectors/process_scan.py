"""全机进程扫描采集器（process_scan）。

用途：列出目标 Worker 上的进程候选，供用户在创建诊断范围或采集任务时
"选择进程"而不是手工填写 PID。这是 R0 级只读操作，不依赖外部工具，
只读取 /proc，无任何副作用。

输出 schema_version=process_scan.v1：
{
  "schema_version": "process_scan.v1",
  "task_id": "...",
  "scanned_at": 1722751234.5,
  "query": "service-x",          # 可选过滤关键字
  "ncpu": 4,
  "processes": [
    {
      "pid": 1234,
      "comm": "service-x",
      "cmdline": "/usr/bin/service-x --port 8080",
      "state": "S",
      "threads": 12,
      "rss_mb": 84.3,
      "cpu_percent": 12.5,       # 采样窗口内的单核百分比估算
      "cpu_seconds": 321.4,      # 进程累计 CPU 时间（user+sys）
      "username": "root"
    },
    ...
  ]
}

processes 按 cpu_percent 降序排列；最多返回 max_results 条。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


def _clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK") or 100)
    except (AttributeError, OSError, ValueError):
        return 100


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") or 4096)
    except (AttributeError, OSError, ValueError):
        return 4096


CLK_TCK = _clk_tck()
PAGE_SIZE = _page_size()
SCHEMA_VERSION = "process_scan.v1"


class ProcessScanCollector:
    """扫描 /proc 返回进程候选清单。"""

    OUTPUT_BASE = "/tmp/mini-drop"
    MAX_PROCESSES = 500  # 单次最多返回的进程数
    DEFAULT_SAMPLE_SEC = 0.6  # 两次采样间隔，用于估算 CPU 百分比

    def collect(self, task: CollectorTask) -> CollectorResult:
        query = str(task.options.get("query", "") or "").strip().lower()
        sample_sec = max(0.2, min(float(task.options.get("sample_sec", self.DEFAULT_SAMPLE_SEC)), 3.0))
        max_results = max(1, min(int(task.options.get("max_results", self.MAX_PROCESSES)), 1000))
        self_pid = int(task.options.get("self_pid", 0) or 0)

        first = self._snapshot()
        time.sleep(sample_sec)
        second = self._snapshot()
        ncpu = max(1, os.cpu_count() or 1)

        processes: list[dict[str, Any]] = []
        for pid, prev in first.items():
            cur = second.get(pid)
            if cur is None:
                continue
            if self_pid and pid == self_pid:
                continue  # 排除 Agent 自身进程
            comm = cur.get("comm", "")
            cmdline = cur.get("cmdline", "")
            if query and query not in comm.lower() and query not in cmdline.lower():
                continue
            cpu_ticks = max(0, cur["ticks"] - prev["ticks"])
            cpu_percent = round(cpu_ticks * 100.0 / CLK_TCK / sample_sec, 1)
            processes.append({
                "pid": pid,
                "comm": comm or f"pid-{pid}",
                "cmdline": cmdline,
                "state": cur.get("state", ""),
                "threads": cur.get("threads", 0),
                "rss_mb": round(cur.get("rss_bytes", 0) / (1024 * 1024), 1),
                "cpu_percent": cpu_percent,
                "cpu_seconds": round(cur.get("cpu_ticks_total", 0) / CLK_TCK, 1),
                "username": cur.get("username", ""),
            })

        # 按 CPU 占用降序，其次按 RSS 降序，让用户优先看到最可疑的进程
        processes.sort(key=lambda item: (item["cpu_percent"], item["rss_mb"]), reverse=True)
        processes = processes[:max_results]

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        output = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task.id,
            "scanned_at": time.time(),
            "query": task.options.get("query", ""),
            "ncpu": ncpu,
            "process_count": len(processes),
            "processes": processes,
        }
        output_path = os.path.join(output_dir, "process_scan.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=False, default=str)

        return CollectorResult(
            ok=True,
            reason=f"进程扫描完成: 共 {len(processes)} 个候选" + (f"（关键字 {query!r}）" if query else ""),
            artifacts=[{
                "artifact_type": "process_scan",
                "filename": "process_scan.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {
                    "schema_version": SCHEMA_VERSION,
                    "query": task.options.get("query", ""),
                    "process_count": len(processes),
                },
            }],
        )

    def _snapshot(self) -> dict[int, dict[str, Any]]:
        """读取一次全机进程快照。"""
        result: dict[int, dict[str, Any]] = {}
        if not os.path.isdir("/proc"):
            return result
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            proc_dir = f"/proc/{entry}"
            try:
                stat = self._read_stat(proc_dir, pid)
                if stat is None:
                    continue
                statm = self._read_statm(proc_dir)
                comm = self._read_comm(proc_dir)
                cmdline = self._read_cmdline(proc_dir)
                status = self._read_status(proc_dir)
                result[pid] = {
                    "ticks": stat["ticks"],
                    "cpu_ticks_total": stat["cpu_ticks_total"],
                    "rss_bytes": statm,
                    "comm": comm,
                    "cmdline": cmdline,
                    "state": status.get("state", stat["state"]),
                    "threads": status.get("threads", 0),
                    "username": status.get("username", ""),
                }
            except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
                continue
        return result

    @staticmethod
    def _read_stat(proc_dir: str, pid: int) -> dict[str, Any] | None:
        try:
            with open(os.path.join(proc_dir, "stat"), "r", encoding="utf-8") as handle:
                raw = handle.read()
        except (PermissionError, FileNotFoundError, OSError):
            return None
        # comm 可能包含空格和括号，从第一个 ")" 之后解析字段
        close = raw.rfind(")")
        if close < 0:
            return None
        fields = raw[close + 2:].split()
        try:
            utime = int(fields[11])
            stime = int(fields[12])
            state = fields[0] if fields else ""
        except (IndexError, ValueError):
            return None
        return {
            "ticks": utime + stime,
            "cpu_ticks_total": utime + stime,
            "state": state,
        }

    @staticmethod
    def _read_statm(proc_dir: str) -> int:
        try:
            with open(os.path.join(proc_dir, "statm"), "r", encoding="utf-8") as handle:
                parts = handle.read().split()
            if len(parts) >= 2:
                return int(parts[1]) * PAGE_SIZE
        except (PermissionError, FileNotFoundError, ValueError, OSError):
            pass
        return 0

    @staticmethod
    def _read_comm(proc_dir: str) -> str:
        try:
            with open(os.path.join(proc_dir, "comm"), "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except (PermissionError, FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _read_cmdline(proc_dir: str) -> str:
        try:
            with open(os.path.join(proc_dir, "cmdline"), "rb") as handle:
                raw = handle.read()
            if not isinstance(raw, bytes):
                raw = str(raw).encode("utf-8", errors="replace")
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except (PermissionError, FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _read_status(proc_dir: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            with open(os.path.join(proc_dir, "status"), "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("State:"):
                        result["state"] = line.split()[1] if len(line.split()) > 1 else ""
                    elif line.startswith("Threads:"):
                        try:
                            result["threads"] = int(line.split()[1])
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("Uid:"):
                        try:
                            result["uid"] = int(line.split()[1])
                        except (IndexError, ValueError):
                            pass
        except (PermissionError, FileNotFoundError, OSError):
            pass
        if "uid" in result:
            result["username"] = _uid_to_name(result["uid"])
        return result


def _uid_to_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError, OSError):
        return str(uid)

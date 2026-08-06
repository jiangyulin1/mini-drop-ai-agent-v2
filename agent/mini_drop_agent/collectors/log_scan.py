"""目标进程日志扫描采集器（log_scan）。

用途：定位"报错/超时/异常"类根因。策略是从目标进程打开的 fd 中发现日志
文件（进程自己写的日志最可靠），读取尾部内容，解析日志级别与错误行。
R1 级只读操作，不依赖外部工具。

输出 schema_version=log_scan.v1：
{
  "schema_version": "log_scan.v1",
  "task_id": "...",
  "pid": 1234,
  "comm": "service-x",
  "scanned_at": 1722751234.5,
  "log_files": [
    {
      "path": "/var/log/service-x/service.log",
      "size_bytes": 10485760,
      "tail_bytes": 262144,
      "level_counts": {"ERROR": 3, "WARN": 12, "INFO": 500, "DEBUG": 0},
      "error_lines": [
        {"line": 10234, "ts": "2026-08-05T10:02:11+08:00", "text": "connection refused: 192.168.10.20:3306"}
      ],
      "patterns": {"connection_refused": 3, "timeout": 2, "exception": 1}
    }
  ]
}
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask

SCHEMA_VERSION = "log_scan.v1"
OUTPUT_BASE = "/tmp/mini-drop"
MAX_FDS = 60  # 最多检查的 fd 数
MAX_LOG_FILES = 5  # 最多读取的日志文件数
TAIL_BYTES = 256 * 1024  # 每个文件读取尾部 256KB
MAX_ERROR_LINES = 50  # 每个文件最多保留的错误行

_LEVEL_RE = re.compile(r"\b(ERROR|WARN(?:ING)?|FATAL|CRITICAL|DEBUG|INFO)\b", re.IGNORECASE)
_LOG_PATTERNS: dict[str, re.Pattern] = {
    "connection_refused": re.compile(r"connection\s*refused|econnrefused", re.IGNORECASE),
    "connection_reset": re.compile(r"connection\s*reset|econnreset", re.IGNORECASE),
    "timeout": re.compile(r"timed?\s*out|timeout", re.IGNORECASE),
    "exception": re.compile(r"exception|panic", re.IGNORECASE),
    "out_of_memory": re.compile(r"out\s*of\s*memory|\boom\b", re.IGNORECASE),
    "deadlock": re.compile(r"deadlock", re.IGNORECASE),
    "denied": re.compile(r"\bdenied\b|permission\s*denied", re.IGNORECASE),
    "failed": re.compile(r"\bfailed\b|\bfailure\b", re.IGNORECASE),
    "unreachable": re.compile(r"unreachable", re.IGNORECASE),
}
_HIGH_SIGNAL_PATTERNS = {
    "connection_refused", "connection_reset", "exception", "out_of_memory",
    "deadlock", "denied", "unreachable",
}
_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


class LogScanCollector:
    """扫描目标进程的日志文件并提取错误信息。"""

    def collect(self, task: CollectorTask) -> CollectorResult:
        pid = task.target_pid
        log_files = self._discover_log_files(pid)
        if not log_files:
            return CollectorResult(
                ok=True,
                reason=f"未发现目标进程 {pid} 的日志文件（可能是容器化或日志输出到 stdout）",
                artifacts=[self._build_artifact(task.id, pid, "", [])],
            )

        parsed: list[dict[str, Any]] = []
        for path in log_files[:MAX_LOG_FILES]:
            parsed.append(self._parse_log(path))

        return CollectorResult(
            ok=True,
            reason=f"日志扫描完成: {len(parsed)} 个日志文件，"
                   f"错误行 {sum(len(item['error_lines']) for item in parsed)} 条",
            artifacts=[self._build_artifact(task.id, pid, log_files[0], parsed)],
        )

    def _build_artifact(self, task_id: str, pid: int, first_path: str, parsed: list[dict[str, Any]]) -> dict[str, Any]:
        output_dir = os.path.join(OUTPUT_BASE, task_id)
        os.makedirs(output_dir, exist_ok=True)
        output = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "pid": pid,
            "scanned_at": time.time(),
            "log_files": parsed,
        }
        output_path = os.path.join(output_dir, "log_scan.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=False, default=str)
        return {
            "artifact_type": "log_scan",
            "filename": "log_scan.json",
            "local_path": output_path,
            "content_type": "application/json",
            "size_bytes": os.path.getsize(output_path),
            "metadata": {
                "schema_version": SCHEMA_VERSION,
                "pid": pid,
                "log_file_count": len(parsed),
            },
        }

    def _discover_log_files(self, pid: int) -> list[str]:
        """通过 /proc/<pid>/fd 发现进程打开的日志文件。"""
        candidates: list[str] = []
        seen: set[str] = set()
        fd_dir = f"/proc/{pid}/fd"
        if not os.path.isdir(fd_dir):
            return candidates
        try:
            entries = sorted(os.listdir(fd_dir), key=lambda item: int(item) if item.isdigit() else 0)
        except (PermissionError, OSError):
            return candidates
        for entry in entries[:MAX_FDS]:
            try:
                target = os.readlink(os.path.join(fd_dir, entry))
            except (PermissionError, OSError):
                continue
            # 过滤 socket / pipe / 伪设备
            if target.startswith(("socket:", "pipe:", "anon_inode", "/dev/", "memfd:")):
                continue
            if not os.path.isfile(target):
                continue
            if target in seen:
                continue
            seen.add(target)
            # 优先 .log / .out / 包含 log 的路径；否则若可读且较大也纳入
            lower = target.lower()
            is_log_like = lower.endswith((".log", ".out", ".err")) or "log" in lower
            try:
                size = os.path.getsize(target)
            except OSError:
                continue
            if is_log_like and size > 0:
                candidates.append(target)
            elif size > 64 * 1024 and self._looks_like_text_log(target):
                candidates.append(target)
        return candidates

    @staticmethod
    def _looks_like_text_log(path: str) -> bool:
        """粗判是否为文本日志：前 2KB 不含大量二进制控制字符。"""
        try:
            with open(path, "rb") as handle:
                head = handle.read(2048)
        except (PermissionError, OSError):
            return False
        if not head:
            return False
        control = sum(1 for byte in head if byte < 9 or (13 < byte < 32) or byte > 126)
        return control / len(head) < 0.02

    def _parse_log(self, path: str) -> dict[str, Any]:
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as handle:
                if size > TAIL_BYTES:
                    handle.seek(-TAIL_BYTES, os.SEEK_END)
                raw = handle.read()
        except (PermissionError, OSError):
            return {"path": path, "size_bytes": 0, "tail_bytes": 0, "level_counts": {}, "error_lines": [], "patterns": {}}

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()[-500:]
        level_counts: dict[str, int] = {}
        patterns: dict[str, int] = {}
        error_lines: list[dict[str, Any]] = []

        for index, line in enumerate(lines):
            level_match = _LEVEL_RE.search(line)
            if level_match:
                level = level_match.group(1).upper()
                if level == "WARNING":
                    level = "WARN"
                level_counts[level] = level_counts.get(level, 0) + 1
            matched_keys: set[str] = set()
            for key, pattern in _LOG_PATTERNS.items():
                if pattern.search(line):
                    patterns[key] = patterns.get(key, 0) + 1
                    matched_keys.add(key)
            is_error = bool(level_match and level_match.group(1).upper() in {"ERROR", "FATAL", "CRITICAL"}) or bool(
                matched_keys & _HIGH_SIGNAL_PATTERNS
            )
            if is_error and len(error_lines) < MAX_ERROR_LINES:
                ts_match = _TS_RE.search(line)
                error_lines.append({
                    "line": index + 1,
                    "ts": ts_match.group(1) if ts_match else "",
                    "text": line.strip()[:500],
                })

        return {
            "path": path,
            "size_bytes": size,
            "tail_bytes": len(raw),
            "level_counts": level_counts,
            "patterns": patterns,
            "error_lines": error_lines,
        }

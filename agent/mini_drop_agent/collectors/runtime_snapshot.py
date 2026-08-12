"""Low-overhead runtime and thread-state snapshot collector.

The collector deliberately uses procfs only.  It does not attach a debugger,
inject code, send a signal, or execute a command inside the target process.
That makes it suitable as an R1 discriminator before a language-specific R2
profiler is considered.
"""

from __future__ import annotations

import json
import os
import struct
import time
from collections import Counter
from pathlib import Path
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class RuntimeSnapshotCollector:
    OUTPUT_BASE = "/tmp/mini-drop"
    SAMPLE_INTERVAL_SEC = 1.0
    MAX_SAMPLES = 120
    MAX_THREADS = 4096
    MAX_ELF_SCAN_BYTES = 2 * 1024 * 1024
    MAX_ELF_SECTION_TABLE_BYTES = 1024 * 1024
    SCHEMA_VERSION = "runtime_snapshot.v1"

    _LOCK_WCHAN_TOKENS = (
        "futex", "mutex", "sem_wait", "rwsem", "rwlock", "monitor",
        "park", "wait_on_bit", "lock_slowpath",
    )

    def collect(self, task: CollectorTask) -> CollectorResult:
        if not os.path.isdir(f"/proc/{task.target_pid}"):
            return CollectorResult(ok=False, reason=f"目标 PID {task.target_pid} 不存在")

        duration = max(1, min(int(task.duration_sec), self.MAX_SAMPLES))
        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        samples: list[dict[str, Any]] = []
        deadline = time.time() + duration
        while time.time() < deadline and os.path.isdir(f"/proc/{task.target_pid}"):
            samples.append(self._sample(task.target_pid))
            if len(samples) >= self.MAX_SAMPLES:
                break
            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(min(self.SAMPLE_INTERVAL_SEC, remaining))

        if not samples:
            return CollectorResult(ok=False, reason="未能采集运行时线程状态")

        runtime_type = self._detect_runtime(task.target_pid)
        summary = self._summarize(samples, runtime_type)
        output = {
            "schema_version": self.SCHEMA_VERSION,
            "task_id": task.id,
            "pid": task.target_pid,
            "duration_sec": duration,
            "sample_count": len(samples),
            "summary": summary,
            "samples": samples,
        }
        output_path = os.path.join(output_dir, "runtime_snapshot.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
        return CollectorResult(
            ok=True,
            reason=(
                f"运行时快照完成: {runtime_type}, {len(samples)} 个样本, "
                f"锁等待线程峰值 {summary['lock_waiter_count_max']}"
            ),
            artifacts=[{
                "artifact_type": "runtime_metrics",
                "filename": "runtime_snapshot.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {"schema_version": self.SCHEMA_VERSION, **summary},
            }],
        )

    @classmethod
    def _sample(cls, pid: int) -> dict[str, Any]:
        states: Counter[str] = Counter()
        wait_channels: Counter[str] = Counter()
        lock_waiters = 0
        task_root = Path(f"/proc/{pid}/task")
        try:
            tids = list(task_root.iterdir())[: cls.MAX_THREADS]
        except (FileNotFoundError, PermissionError, OSError):
            tids = []
        for thread in tids:
            try:
                stat = (thread / "stat").read_text(encoding="utf-8", errors="replace")
                right = stat.rfind(")")
                state = stat[right + 2:].split()[0] if right >= 0 else "?"
                states[state] += 1
            except (FileNotFoundError, PermissionError, OSError, IndexError):
                continue
            try:
                wchan = (thread / "wchan").read_text(encoding="utf-8", errors="replace").strip()[:128]
            except (FileNotFoundError, PermissionError, OSError):
                wchan = ""
            if wchan and wchan != "0":
                wait_channels[wchan] += 1
                lowered = wchan.lower()
                if any(token in lowered for token in cls._LOCK_WCHAN_TOKENS):
                    lock_waiters += 1
        return {
            "ts": time.time(),
            "thread_count": sum(states.values()),
            "thread_states": dict(states),
            "lock_waiter_count": lock_waiters,
            "top_wait_channels": [
                {"name": name, "count": count}
                for name, count in wait_channels.most_common(10)
            ],
            "process": cls._read_process(pid),
        }

    @staticmethod
    def _read_process(pid: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            right = line.rfind(")")
            fields = line[right + 2:].split()
            result.update({
                "state": fields[0],
                "cpu_ticks": int(fields[11]) + int(fields[12]),
                "thread_count": int(fields[17]),
            })
        except (FileNotFoundError, PermissionError, OSError, IndexError, ValueError):
            pass
        try:
            result["oom_score"] = int(Path(f"/proc/{pid}/oom_score").read_text().strip())
            result["oom_score_adj"] = int(Path(f"/proc/{pid}/oom_score_adj").read_text().strip())
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            pass
        return result

    @classmethod
    def _detect_runtime(cls, pid: int) -> str:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).lower()
        except (FileNotFoundError, PermissionError, OSError):
            cmdline = ""
        try:
            exe_path = os.readlink(f"/proc/{pid}/exe").lower()
        except (FileNotFoundError, PermissionError, OSError):
            exe_path = ""
        joined = f"{exe_path} {cmdline}"
        if "java" in joined:
            return "java"
        if "python" in joined or "gunicorn" in joined or "uwsgi" in joined:
            return "python"
        if cls._looks_like_go_binary(pid):
            return "go"
        return "native"

    @classmethod
    def _looks_like_go_binary(cls, pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/exe", "rb") as handle:
                data = handle.read(cls.MAX_ELF_SCAN_BYTES)
                return (
                    b"Go build ID:" in data
                    or b"runtime.main" in data
                    or cls._elf_has_go_section(handle, data)
                )
        except (FileNotFoundError, PermissionError, OSError):
            return False

    @classmethod
    def _elf_has_go_section(cls, handle, header: bytes) -> bool:
        """Read only the ELF section table/string table to identify Go binaries."""
        if len(header) < 64 or header[:4] != b"\x7fELF":
            return False
        elf_class, endian_id = header[4], header[5]
        endian = "<" if endian_id == 1 else ">" if endian_id == 2 else ""
        try:
            if elf_class == 2:  # ELF64
                shoff = struct.unpack_from(endian + "Q", header, 40)[0]
                shentsize, shnum, shstrndx = struct.unpack_from(endian + "HHH", header, 58)
                offset_fmt = endian + "QQ"
                offset_pos = 24
            elif elf_class == 1:  # ELF32
                shoff = struct.unpack_from(endian + "I", header, 32)[0]
                shentsize, shnum, shstrndx = struct.unpack_from(endian + "HHH", header, 46)
                offset_fmt = endian + "II"
                offset_pos = 16
            else:
                return False
        except (struct.error, ValueError):
            return False
        table_size = shentsize * shnum
        if (
            not endian or shentsize <= 0 or shnum <= 0 or shstrndx >= shnum
            or table_size > cls.MAX_ELF_SECTION_TABLE_BYTES
        ):
            return False
        try:
            handle.seek(shoff)
            table = handle.read(table_size)
            if len(table) != table_size:
                return False
            str_header = table[shstrndx * shentsize:(shstrndx + 1) * shentsize]
            str_offset, str_size = struct.unpack_from(offset_fmt, str_header, offset_pos)
            if str_size <= 0 or str_size > cls.MAX_ELF_SECTION_TABLE_BYTES:
                return False
            handle.seek(str_offset)
            section_names = handle.read(str_size)
        except (OSError, ValueError, struct.error):
            return False
        return any(marker in section_names for marker in (
            b".gopclntab", b".go.buildinfo", b".note.go.buildid",
        ))

    @staticmethod
    def _summarize(samples: list[dict[str, Any]], runtime_type: str) -> dict[str, Any]:
        thread_counts = [int(item.get("thread_count", 0)) for item in samples]
        lock_waiters = [int(item.get("lock_waiter_count", 0)) for item in samples]
        ratios = [waiters / max(threads, 1) for waiters, threads in zip(lock_waiters, thread_counts)]
        uninterruptible = [int((item.get("thread_states") or {}).get("D", 0)) for item in samples]
        stopped = [int((item.get("thread_states") or {}).get("T", 0)) for item in samples]
        first_ticks = int((samples[0].get("process") or {}).get("cpu_ticks", 0))
        last_ticks = int((samples[-1].get("process") or {}).get("cpu_ticks", 0))
        channels: Counter[str] = Counter()
        for item in samples:
            for channel in item.get("top_wait_channels") or []:
                channels[str(channel.get("name", ""))] += int(channel.get("count", 0))
        return {
            "runtime_type": runtime_type,
            "thread_count_max": max(thread_counts, default=0),
            "lock_waiter_count_max": max(lock_waiters, default=0),
            "blocked_thread_ratio_max": round(max(ratios, default=0.0), 4),
            "uninterruptible_thread_count_max": max(uninterruptible, default=0),
            "stopped_thread_count_max": max(stopped, default=0),
            "cpu_tick_delta": max(0, last_ticks - first_ticks),
            "top_wait_channels": [
                {"name": name, "count": count}
                for name, count in channels.most_common(10)
            ],
        }

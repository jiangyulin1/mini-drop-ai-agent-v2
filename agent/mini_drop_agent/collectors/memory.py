"""Linux 内存分析采集器。

通过周期性读取 /proc/<pid>/smaps 或 /proc/<pid>/status，
对目标进程进行内存增长分析，产出内存时间序列 JSON。

前置条件：
  1. Linux 内核 >= 2.6.14（支持 smaps）
  2. 目标 PID 有效
  3. Agent 有读取 /proc/<pid>/smaps 的权限（通常需要 root 或同一用户）

执行流程：
  1. 验证目标 PID 存在
  2. 周期性（每 1 秒）读取 /proc/<pid>/smaps 或 /proc/<pid>/status
  3. 汇总 RSS / PSS / Swap / VmSize
  4. 输出内存时间序列 JSON
"""

from __future__ import annotations

import json
import os
import time

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class MemoryCollector:
    """Linux 内存分析采集器（smaps 读取）。"""

    OUTPUT_BASE = "/tmp/mini-drop"
    SAMPLE_INTERVAL_SEC = 1.0
    MAX_SAMPLES = 300  # 最多 300 次采样（5 分钟）

    def collect(self, task: CollectorTask) -> CollectorResult:
        if not self._pid_exists(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不存在",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)

        smaps_path = f"/proc/{task.target_pid}/smaps"
        status_path = f"/proc/{task.target_pid}/status"
        use_smaps = os.path.isfile(smaps_path)
        use_status = os.path.isfile(status_path)

        if not use_smaps and not use_status:
            return CollectorResult(
                ok=False,
                reason=f"无法读取 /proc/{task.target_pid} 的内存信息（权限不足或进程已退出）",
            )

        duration = min(task.duration_sec, self.MAX_SAMPLES)
        samples: list[dict] = []

        deadline = time.time() + duration
        while time.time() < deadline:
            ts = time.time()
            if not self._pid_exists(task.target_pid):
                break

            sample: dict = {"ts": ts}
            if use_smaps:
                sample.update(self._parse_smaps(smaps_path))
            if use_status:
                sample.update(self._parse_status(status_path))

            samples.append(sample)
            if len(samples) >= self.MAX_SAMPLES:
                break

            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(min(self.SAMPLE_INTERVAL_SEC, remaining))

        if not samples:
            return CollectorResult(
                ok=False,
                reason="未能采集到任何内存样本，目标进程可能在采集开始前退出",
            )

        # 计算趋势
        first_rss = samples[0].get("rss_mb", 0)
        last_rss = samples[-1].get("rss_mb", 0)
        trend = "stable"
        if last_rss > first_rss * 1.05:
            trend = "increasing"
        elif last_rss < first_rss * 0.95:
            trend = "decreasing"

        peak = max((s.get("rss_mb", 0) for s in samples), default=0)

        output = {
            "task_id": task.id,
            "pid": task.target_pid,
            "duration_sec": duration,
            "sample_count": len(samples),
            "first_rss_mb": round(first_rss, 2),
            "last_rss_mb": round(last_rss, 2),
            "peak_rss_mb": round(peak, 2),
            "trend": trend,
            "samples": samples,
        }

        output_path = os.path.join(output_dir, "memory_profile.json")
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, ensure_ascii=False)

        return CollectorResult(
            ok=True,
            reason=f"内存采集完成: {len(samples)} 个样本, RSS {first_rss:.1f}→{last_rss:.1f} MB ({trend})",
            artifacts=[{
                "artifact_type": "memory_json",
                "filename": "memory_profile.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {
                    "sample_count": len(samples),
                    "first_rss_mb": round(first_rss, 2),
                    "last_rss_mb": round(last_rss, 2),
                    "peak_rss_mb": round(peak, 2),
                    "trend": trend,
                },
            }],
        )

    # ── 内部方法 ────────────────────────────────────────────────

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @staticmethod
    def _parse_smaps(path: str) -> dict:
        """解析 /proc/<pid>/smaps 汇总 RSS/PSS/Swap。"""
        rss_kb = 0
        pss_kb = 0
        swap_kb = 0
        try:
            with open(path, "r") as fh:
                for line in fh:
                    if line.startswith("Rss:"):
                        try:
                            rss_kb += int(line.split()[1])
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("Pss:"):
                        try:
                            pss_kb += int(line.split()[1])
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("Swap:"):
                        try:
                            swap_kb += int(line.split()[1])
                        except (IndexError, ValueError):
                            pass
        except (FileNotFoundError, PermissionError):
            pass

        return {
            "rss_mb": round(rss_kb / 1024.0, 2),
            "pss_mb": round(pss_kb / 1024.0, 2),
            "swap_mb": round(swap_kb / 1024.0, 2),
        }

    @staticmethod
    def _parse_status(path: str) -> dict:
        """解析 /proc/<pid>/status 获取 VmRSS / VmSize。"""
        result: dict = {}
        try:
            with open(path, "r") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        try:
                            result["rss_mb"] = round(int(line.split()[1]) / 1024.0, 2)
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("VmSize:"):
                        try:
                            result["vmsize_mb"] = round(int(line.split()[1]) / 1024.0, 2)
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("VmSwap:"):
                        try:
                            result["swap_mb"] = round(int(line.split()[1]) / 1024.0, 2)
                        except (IndexError, ValueError):
                            pass
        except (FileNotFoundError, PermissionError):
            pass
        return result

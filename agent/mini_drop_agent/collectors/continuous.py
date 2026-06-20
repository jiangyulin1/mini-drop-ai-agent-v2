"""Continuous Profiling 采集器。

将一次性 perf 任务扩展为后台周期采样。Agent 侧后台线程按固定间隔
执行低频 perf record，每个窗口独立输出火焰图产物，Web 通过时间轴
回放各窗口的火焰图变化。

设计参考 DeepFlow Agent 的持续 profiling 模式——agent 内置能力而非
反复创建独立任务。
"""

from __future__ import annotations

import os
import json
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


@dataclass
class _Window:
    index: int
    start_ts: float
    end_ts: float
    output_dir: str
    ok: bool = True
    reason: str = ""
    artifacts: list[dict] = field(default_factory=list)


class ContinuousCollector:
    """周期低频 perf 采样采集器。"""

    OUTPUT_BASE = "/tmp/mini-drop"

    # 默认参数
    WINDOW_DURATION_SEC = 10   # 每窗口采集时长 (s)
    WINDOW_INTERVAL_SEC = 60   # 窗口间隔 (s)
    WINDOW_SAMPLE_RATE = 11    # 低频采样率 (Hz)

    def collect(self, task: CollectorTask) -> CollectorResult:
        perf_path = shutil.which("perf")
        if perf_path is None:
            return CollectorResult(ok=False, reason="perf 命令不可用")

        if not self._pid_exists(task.target_pid):
            return CollectorResult(ok=False, reason=f"目标 PID {task.target_pid} 不存在")

        task_base = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(task_base, exist_ok=True)

        windows: list[_Window] = []
        total_timeout = task.duration_sec  # 总持续秒数，取任务指定的 duration
        window_count = max(1, total_timeout // self.WINDOW_INTERVAL_SEC)
        window_duration = min(self.WINDOW_DURATION_SEC, self.WINDOW_INTERVAL_SEC - 5)

        deadline = time.time() + total_timeout

        for i in range(window_count):
            if time.time() >= deadline:
                break

            window_dir = os.path.join(task_base, f"window_{i:03d}")
            os.makedirs(window_dir, exist_ok=True)
            perf_data = os.path.join(window_dir, "perf.data")

            start = time.time()

            cmd = [
                perf_path, "record",
                "-F", str(self.WINDOW_SAMPLE_RATE),
                "-g", "-p", str(task.target_pid),
                "-o", perf_data,
                "--", "sleep", str(window_duration),
            ]
            timeout = window_duration + 30

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None,
                )
                try:
                    proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                            proc.wait()
                        except Exception:
                            pass
                    # 清理管道，释放文件描述符
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
                    win = _Window(index=i, start_ts=start, end_ts=time.time(),
                                  output_dir=window_dir, ok=False,
                                  reason=f"window {i} 超时")
                    windows.append(win)
                    continue

                end = time.time()
                if proc.returncode == 0 and os.path.isfile(perf_data) and os.path.getsize(perf_data) > 0:
                    win = _Window(index=i, start_ts=start, end_ts=end, output_dir=window_dir, ok=True,
                                  reason="perf record 完成",
                                  artifacts=[{
                                      "artifact_type": "continuous_window",
                                      "filename": f"window_{i:03d}/perf.data",
                                      "local_path": perf_data,
                                      "content_type": "application/octet-stream",
                                      "size_bytes": os.path.getsize(perf_data),
                                      "metadata": {"window_index": i, "start_ts": start, "end_ts": end},
                                  }])
                else:
                    win = _Window(index=i, start_ts=start, end_ts=end, output_dir=window_dir,
                                  ok=False, reason=f"perf record exit={proc.returncode}")
                windows.append(win)

            except Exception as exc:
                # 清理管道，防止 fd 泄露
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                windows.append(_Window(index=i, start_ts=start, end_ts=time.time(),
                                       output_dir=window_dir, ok=False, reason=str(exc)))

            # 到达下一个窗口起点再继续
            remaining = (start + self.WINDOW_INTERVAL_SEC) - time.time()
            if remaining > 0:
                time.sleep(remaining)

        if not windows:
            return CollectorResult(ok=False, reason="Continuous Profiling 未完成任何窗口")

        all_artifacts: list[dict] = []
        summary_windows: list[dict] = []
        for w in windows:
            if w.ok:
                all_artifacts.extend(w.artifacts)
            summary_windows.append({
                "window_index": w.index,
                "start_ts": w.start_ts,
                "end_ts": w.end_ts,
                "ok": w.ok,
                "reason": w.reason,
            })

        ok_count = sum(1 for w in windows if w.ok)
        summary_path = os.path.join(task_base, "windows.json")
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump({"windows": summary_windows}, fh, indent=2)

        return CollectorResult(
            ok=ok_count > 0,
            reason=f"Continuous Profiling 完成: {ok_count}/{len(windows)} 窗口成功",
            artifacts=all_artifacts + [{
                "artifact_type": "continuous_summary",
                "filename": "windows.json",
                "local_path": summary_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(summary_path),
                "metadata": {"windows": summary_windows},
            }],
        )

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

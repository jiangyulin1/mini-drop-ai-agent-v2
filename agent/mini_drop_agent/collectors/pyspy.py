"""py-spy 用户态采集器：对 Python 进程进行采样并输出火焰图 SVG。

py-spy 通过读取目标进程内存直接获取 Python 调用栈，
无需修改目标代码或重启进程。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class PySpyCollector:
    """py-spy sampling profiler。"""

    OUTPUT_BASE = "/tmp/mini-drop"

    def collect(self, task: CollectorTask) -> CollectorResult:
        pyspy = self._find_pyspy()
        if pyspy is None:
            return CollectorResult(
                ok=False,
                reason="py-spy 命令不可用，请通过 pip install py-spy 安装",
            )

        if not self._pid_exists(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不存在",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        svg_path = os.path.join(output_dir, "pyspy.svg")

        base_cmd = [
            pyspy, "record",
            "-p", str(task.target_pid),
            "-d", str(task.duration_sec),
            "-r", str(task.sample_rate),
            "-o", svg_path,
        ]
        cmd = base_cmd + ["--native"]  # 同时显示 C 扩展调用帧

        timeout = task.duration_sec + 30

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return CollectorResult(
                ok=False,
                reason=f"py-spy 执行超时 (>{timeout}s)",
            )
        except Exception as exc:
            return CollectorResult(
                ok=False,
                reason=f"py-spy 异常: {exc}",
            )

        if proc.returncode != 0 and self._should_retry_without_native(proc.stderr):
            try:
                proc = subprocess.run(
                    base_cmd,
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return CollectorResult(
                    ok=False,
                    reason=f"py-spy 降级重试超时 (>{timeout}s)",
                )
            except Exception as exc:
                return CollectorResult(
                    ok=False,
                    reason=f"py-spy 降级重试异常: {exc}",
                )

        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            return CollectorResult(
                ok=False,
                reason=f"py-spy 执行失败 (exit={proc.returncode}): {err[:200]}",
            )

        if not os.path.isfile(svg_path) or os.path.getsize(svg_path) == 0:
            return CollectorResult(
                ok=False,
                reason="py-spy 未产出 SVG 文件",
            )

        size = os.path.getsize(svg_path)
        return CollectorResult(
            ok=True,
            reason="py-spy 采集完成",
            artifacts=[
                {
                    "artifact_type": "flamegraph_svg",
                    "filename": "pyspy.svg",
                    "local_path": svg_path,
                    "content_type": "image/svg+xml",
                    "size_bytes": size,
                }
            ],
        )

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @staticmethod
    def _find_pyspy() -> str | None:
        """Find py-spy from PATH or beside the running Agent interpreter."""
        discovered = shutil.which("py-spy")
        if discovered:
            return discovered
        executable = "py-spy.exe" if os.name == "nt" else "py-spy"
        # Do not resolve the interpreter symlink: in a venv, ``python`` often
        # points to /usr/bin/python while py-spy lives beside the venv launcher.
        bundled = Path(sys.executable).parent / executable
        if bundled.is_file():
            return str(bundled)
        return None

    @staticmethod
    def _should_retry_without_native(stderr: bytes) -> bool:
        text = stderr.decode("utf-8", errors="replace")
        return "UNW_EBADREG" in text or "bad register number" in text

"""Java async-profiler 采集器。

通过 async-profiler（https://github.com/async-profiler/async-profiler）
对 JVM 进程进行 CPU/Alloc/Lock 采样，产出 HTML 火焰图。

前置条件：
  1. 目标机器安装 async-profiler，设置 ASYNC_PROFILER_HOME 环境变量
  2. 目标 JVM 进程的 PID 有效
  3. Agent 和 JVM 进程在同一台机器上

执行流程：
  1. 检查 profiler.sh 是否可用
  2. 验证目标 PID 存在且为 Java 进程
  3. 在独立进程组中执行 profiler.sh
  4. 超时时 kill 进程组
  5. 返回 HTML 火焰图产物元数据
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class JavaAsyncProfilerCollector:
    """Java async-profiler 采集器。"""

    OUTPUT_BASE = "/tmp/mini-drop"
    # 支持的 event 类型
    VALID_EVENTS = frozenset({"cpu", "alloc", "lock", "wall", "itimer", "ctimer"})

    def collect(self, task: CollectorTask) -> CollectorResult:
        profiler_path = self._find_profiler()
        if profiler_path is None:
            return CollectorResult(
                ok=False,
                reason="async-profiler 不可用。请设置 ASYNC_PROFILER_HOME 环境变量指向安装目录，"
                       "或从 https://github.com/async-profiler/async-profiler 下载",
            )

        if not self._pid_exists(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不存在",
            )

        if not self._is_java_process(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不是 JVM 进程（未找到 libjvm 映射）",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)

        event = task.options.get("event", "cpu")
        if event not in self.VALID_EVENTS:
            return CollectorResult(
                ok=False,
                reason=f"不支持的 event 类型: {event}，支持: {', '.join(sorted(self.VALID_EVENTS))}",
            )

        output_file = os.path.join(output_dir, "java_flamegraph.html")
        duration = task.duration_sec

        cmd = [
            sys.executable, profiler_path,
            "-d", str(duration),
            "-e", event,
            "-f", output_file,
            str(task.target_pid),
        ]

        timeout = duration + 60

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None,
            )
            stdout, stderr = proc.communicate(timeout=timeout)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return CollectorResult(
                    ok=False,
                    reason=f"async-profiler 执行失败 (exit={proc.returncode}): {err_msg[:200]}",
                )

            # async-profiler 可能在 PID 退出前返回，确认产物存在
            if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
                return CollectorResult(
                    ok=False,
                    reason="async-profiler 未产出火焰图文件，目标进程可能在采集期间退出",
                )

            size = os.path.getsize(output_file)
            return CollectorResult(
                ok=True,
                reason=f"async-profiler {event} 采样完成",
                artifacts=[{
                    "artifact_type": "java_flamegraph_html",
                    "filename": "java_flamegraph.html",
                    "local_path": output_file,
                    "content_type": "text/html",
                    "size_bytes": size,
                }],
            )

        except subprocess.TimeoutExpired:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait()
                except Exception:
                    pass
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return CollectorResult(
                ok=False,
                reason=f"async-profiler 超时 (>{timeout}s)，已强制终止",
            )

        except Exception as exc:
            # 清理管道，防止 fd 泄露
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return CollectorResult(
                ok=False,
                reason=f"async-profiler 异常: {exc}",
            )

    # ── 内部方法 ────────────────────────────────────────────────

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @staticmethod
    def _is_java_process(pid: int) -> bool:
        """检查进程是否映射了 libjvm，即是否为 JVM 进程。"""
        try:
            with open(f"/proc/{pid}/maps", "r") as fh:
                for line in fh:
                    if "libjvm" in line:
                        return True
        except (FileNotFoundError, PermissionError):
            pass
        return False

    @staticmethod
    def _find_profiler() -> str | None:
        """查找 async-profiler 的 profiler.sh 路径。"""
        # 方式 1: 环境变量
        home = os.getenv("ASYNC_PROFILER_HOME", "").strip()
        if home:
            candidate = os.path.join(home, "profiler.sh")
            if os.path.isfile(candidate):
                return candidate

        # 方式 2: PATH 搜索
        which = shutil.which("profiler.sh")
        if which:
            return which

        # 方式 3: 常见安装路径
        for path in [
            "/opt/async-profiler/profiler.sh",
            "/usr/local/async-profiler/profiler.sh",
        ]:
            if os.path.isfile(path):
                return path

        return None

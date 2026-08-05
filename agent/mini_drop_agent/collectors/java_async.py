"""Java async-profiler 采集器。

通过 async-profiler（https://github.com/async-profiler/async-profiler）
对 JVM 进程进行 CPU/Alloc/Lock 采样，产出 HTML 火焰图或 JFR。

前置条件：
  1. 目标机器安装 async-profiler，设置 ASYNC_PROFILER_HOME 环境变量
  2. 目标 JVM 进程的 PID 有效
  3. Agent 和 JVM 进程在同一台机器上

执行流程：
  1. 检查 asprof（或旧版 profiler.sh）是否可用
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
from pathlib import Path

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class JavaAsyncProfilerCollector:
    """Java async-profiler 采集器。"""

    OUTPUT_BASE = "/tmp/mini-drop"
    # 支持的 event 类型
    VALID_EVENTS = frozenset({"cpu", "alloc", "lock", "wall", "itimer", "ctimer"})
    VALID_OUTPUT_FORMATS = frozenset({"html", "jfr", "both"})

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
        try:
            self._prepare_output_dir(output_dir, task.target_pid)
        except OSError as exc:
            return CollectorResult(
                ok=False,
                reason=f"无法为目标 JVM 准备 async-profiler 输出目录: {exc}",
            )

        event = task.options.get("event", "cpu")
        if event not in self.VALID_EVENTS:
            return CollectorResult(
                ok=False,
                reason=f"不支持的 event 类型: {event}，支持: {', '.join(sorted(self.VALID_EVENTS))}",
            )

        output_format = str(task.options.get("output_format", "html")).lower()
        if output_format not in self.VALID_OUTPUT_FORMATS:
            return CollectorResult(
                ok=False,
                reason=(
                    f"不支持的 output_format: {output_format}，支持: "
                    f"{', '.join(sorted(self.VALID_OUTPUT_FORMATS))}"
                ),
            )

        html_file = os.path.join(output_dir, "java_flamegraph.html")
        jfr_file = os.path.join(output_dir, "java_profile.jfr")
        primary_file = jfr_file if output_format in {"jfr", "both"} else html_file
        duration = task.duration_sec

        cmd = [
            profiler_path,
            "-d", str(duration),
            "-e", event,
            "-f", primary_file,
            str(task.target_pid),
        ]
        if output_format in {"jfr", "both"}:
            cmd[1:1] = ["-o", "jfr"]

        timeout = duration + 60
        proc: subprocess.Popen | None = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # 不创建独立会话：留在 worker 进程组内，取消时 killpg(worker)
                # 才能终止 profiler.sh 及其子进程，避免孤儿 jattach 残留。
            )
            stdout, stderr = proc.communicate(timeout=timeout)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return CollectorResult(
                    ok=False,
                    reason=f"async-profiler 执行失败 (exit={proc.returncode}): {err_msg[:200]}",
                )

            # async-profiler 可能在 PID 退出前返回，确认产物存在
            if not os.path.isfile(primary_file) or os.path.getsize(primary_file) == 0:
                return CollectorResult(
                    ok=False,
                    reason="async-profiler 未产出有效文件，目标进程可能在采集期间退出",
                )

            artifacts = []
            if output_format in {"jfr", "both"}:
                artifacts.append(self._artifact(
                    "java_profile_jfr", "java_profile.jfr", jfr_file,
                    "application/octet-stream",
                ))

            converted = False
            if output_format == "both":
                converter = self._find_converter(profiler_path)
                if converter:
                    conversion = subprocess.run(
                        [converter, jfr_file, html_file],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=60,
                        check=False,
                    )
                    converted = (
                        conversion.returncode == 0
                        and os.path.isfile(html_file)
                        and os.path.getsize(html_file) > 0
                    )
                if converted:
                    artifacts.append(self._artifact(
                        "java_flamegraph_html", "java_flamegraph.html", html_file,
                        "text/html",
                    ))
            elif output_format == "html":
                artifacts.append(self._artifact(
                    "java_flamegraph_html", "java_flamegraph.html", html_file,
                    "text/html",
                ))

            suffix = ""
            if output_format == "both" and not converted:
                suffix = "；未找到可用 jfrconv，仅保留 JFR"
            return CollectorResult(
                ok=True,
                reason=f"async-profiler {event} 采样完成{suffix}",
                artifacts=artifacts,
            )

        except subprocess.TimeoutExpired:
            self._terminate(proc)
            return CollectorResult(
                ok=False,
                reason=f"async-profiler 超时 (>{timeout}s)，已强制终止",
            )

        except Exception as exc:
            # 清理管道，防止 fd 泄露
            self._terminate(proc)
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
        """优先查找 async-profiler 3.x+ 的 asprof，兼容旧版 profiler.sh。"""
        # 方式 1: 环境变量
        home = os.getenv("ASYNC_PROFILER_HOME", "").strip()
        if home:
            for relative in ("bin/asprof", "asprof", "profiler.sh"):
                candidate = os.path.join(home, relative)
                if os.path.isfile(candidate):
                    return os.path.normpath(candidate)

        # 方式 2: PATH 搜索
        for executable in ("asprof", "profiler.sh"):
            which = shutil.which(executable)
            if which:
                return which

        # 方式 3: 常见安装路径
        for path in [
            "/opt/async-profiler/bin/asprof",
            "/usr/local/async-profiler/bin/asprof",
            "/opt/async-profiler/asprof",
            "/usr/local/async-profiler/asprof",
            "/opt/async-profiler/profiler.sh",
            "/usr/local/async-profiler/profiler.sh",
        ]:
            if os.path.isfile(path):
                return path

        return None

    @staticmethod
    def _find_converter(profiler_path: str) -> str | None:
        """查找 async-profiler 4.x 随包提供的 jfrconv。"""
        profiler = Path(profiler_path)
        candidates = [
            profiler.with_name("jfrconv"),
            profiler.parent / "bin" / "jfrconv",
            profiler.parent.parent / "bin" / "jfrconv",
        ]
        home = os.getenv("ASYNC_PROFILER_HOME", "").strip()
        if home:
            candidates.insert(0, Path(home) / "bin" / "jfrconv")
        which = shutil.which("jfrconv")
        if which:
            candidates.insert(0, Path(which))
        return next((str(path) for path in candidates if path.is_file()), None)

    @staticmethod
    def _prepare_output_dir(output_dir: str, pid: int) -> None:
        """Let the attached JVM create profiler output when Agent runs as root.

        async-profiler opens the output from inside the target JVM.  A root Agent
        therefore cannot leave a newly-created 0755 directory owned by root when
        the JVM belongs to another user.
        """

        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return
        target = os.stat(f"/proc/{pid}")
        # The JVM must be able to traverse the Agent-owned artifact root.  Do
        # not grant read/list permission; each task directory remains isolated.
        os.chmod(os.path.dirname(output_dir), 0o711)
        os.chown(output_dir, target.st_uid, target.st_gid)
        os.chmod(output_dir, 0o750)

    @staticmethod
    def _artifact(
        artifact_type: str,
        filename: str,
        local_path: str,
        content_type: str,
    ) -> dict:
        return {
            "artifact_type": artifact_type,
            "filename": filename,
            "local_path": local_path,
            "content_type": content_type,
            "size_bytes": os.path.getsize(local_path),
        }

    @staticmethod
    def _terminate(proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        # 只终止 proc 本身。profiler.sh 与其子进程留在 worker 进程组内
        # （见 Popen 注释），killpg(os.getpgid(proc.pid)) 会误杀 worker 组；
        # 超时场景下杀 profiler.sh 后，其短暂存活的 jattach 子进程会在
        # 数秒内自行退出。
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

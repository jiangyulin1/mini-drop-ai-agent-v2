"""perf CPU 采集器：通过 perf record 对目标进程进行采样。

执行流程：
  1. 检查 perf 命令是否可用
  2. 检查 /proc/sys/kernel/perf_event_paranoid 权限水位
  3. 验证目标 PID 存在
  4. 在独立进程组中执行 perf record -F {hz} -g -p {pid} -- sleep {duration}
  5. 超时时 kill 进程组，防止僵尸
  6. 返回采样的 perf.data 产物元数据
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class PerfCollector:
    """Linux perf CPU 采样采集器。"""

    # 默认输出基础路径
    OUTPUT_BASE = "/tmp/mini-drop"

    def collect(self, task: CollectorTask) -> CollectorResult:
        perf_path = shutil.which("perf")
        if perf_path is None:
            return CollectorResult(
                ok=False,
                reason="perf 命令不可用，请确认已安装 linux-tools",
            )

        if not self._check_perf_paranoid():
            return CollectorResult(
                ok=False,
                reason="perf_event_paranoid 权限不足。"
                       "请执行 'echo 1 > /proc/sys/kernel/perf_event_paranoid' 或使用 root 运行 Agent",
            )

        if not self._pid_exists(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不存在",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        perf_data = os.path.join(output_dir, "perf.data")

        callgraph = task.options.get("callgraph", "fp")
        event = task.options.get("event", "cpu-cycles:u")
        all_user = task.options.get("all_user", True)
        hz = task.sample_rate
        duration = task.duration_sec

        cmd = [
            perf_path, "record",
        ]
        if all_user:
            cmd.append("--all-user")
        cmd.extend([
            "-F", str(hz),
            "-g",
            "--call-graph", callgraph,
            "-e", event,
            "-p", str(task.target_pid),
            "-o", perf_data,
            "--", "sleep", str(duration),
        ])

        timeout = duration + 30

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=hasattr(os, "setsid"),
            )
            stdout, stderr = proc.communicate(timeout=timeout)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return CollectorResult(
                    ok=False,
                    reason=f"perf record 执行失败 (exit={proc.returncode}): {err_msg[:200]}",
                )

            # 再次确认 PID 在采集期间未退出
            if not self._pid_exists(task.target_pid):
                return CollectorResult(
                    ok=False,
                    reason=f"目标 PID {task.target_pid} 在采集期间已退出",
                )

            size = os.path.getsize(perf_data) if os.path.isfile(perf_data) else 0
            artifacts = [
                {
                    "artifact_type": "raw",
                    "filename": "perf.data",
                    "local_path": perf_data,
                    "content_type": "application/octet-stream",
                    "size_bytes": size,
                }
            ]
            analysis_artifacts, analysis_reason = self._analyze_perf_data(task.id, perf_data, output_dir)
            artifacts.extend(analysis_artifacts)
            reason = "perf record 采集完成"
            if analysis_artifacts:
                reason += "，Analyzer 已生成火焰图与 TopN"
            elif analysis_reason:
                reason += f"，Analyzer 未完成: {analysis_reason}"
            return CollectorResult(
                ok=True,
                reason=reason,
                artifacts=artifacts,
            )

        except subprocess.TimeoutExpired:
            # 超时 → kill 进程组 → 清理管道防止 fd 泄露
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
            return CollectorResult(
                ok=False,
                reason=f"perf record 超时 (>{timeout}s)，已强制终止",
            )

        except Exception as exc:
            # 清理管道，防止 fd 泄露
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return CollectorResult(
                ok=False,
                reason=f"perf record 异常: {exc}",
            )

    # ── 内部方法 ────────────────────────────────────────────────

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @staticmethod
    def _read_paranoid() -> int | None:
        try:
            with open("/proc/sys/kernel/perf_event_paranoid", "r") as fh:
                return int(fh.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _check_perf_paranoid(self) -> bool:
        """检查 perf_event_paranoid 是否允许采样。

        paranoid ≤ 1: 允许（-1 无限制, 0 允许 trace, 1 允许用户采样）
        paranoid ≥ 2: 普通用户无法采样，返回 False
        """
        val = self._read_paranoid()
        if val is None:
            return True  # 无法读取时不阻断，让 perf 自身报错
        return val <= 1

    @staticmethod
    def _analyze_perf_data(task_id: str, perf_data: str, output_root: str) -> tuple[list[dict], str]:
        """MVP 闭环：采集后在 Agent 本地同步生成可展示分析产物。"""
        cmd = [
            sys.executable,
            "-m",
            "analyzer.mini_drop_analyzer.hotmethod_analyzer",
            "--task-id",
            task_id,
            "--perf-data",
            perf_data,
            "--output-dir",
            os.path.dirname(output_root),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception as exc:
            return [], str(exc)

        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            out = proc.stdout.decode("utf-8", errors="replace").strip()
            return [], (err or out or f"exit={proc.returncode}")[:200]

        generated = {
            "flamegraph_json": ("flamegraph.json", "application/json"),
            "flamegraph_svg": ("flamegraph.svg", "image/svg+xml"),
            "top_json": ("top.json", "application/json"),
            "suggestions_md": ("suggestions.md", "text/markdown"),
        }
        artifacts: list[dict] = []
        for artifact_type, (filename, content_type) in generated.items():
            path = os.path.join(output_root, filename)
            if not os.path.isfile(path):
                continue
            artifacts.append({
                "artifact_type": artifact_type,
                "filename": filename,
                "local_path": path,
                "content_type": content_type,
                "size_bytes": os.path.getsize(path),
            })
        return artifacts, ""

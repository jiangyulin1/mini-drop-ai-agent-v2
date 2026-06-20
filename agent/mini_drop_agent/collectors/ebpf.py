"""eBPF 采集器：通过 bpftrace 运行内核探针脚本，采集 IO 延迟分布。

演示方案：
  1. 先跑一次 eBPF 采集 15 秒（baseline，IO 延迟接近 0）
  2. dd if=/dev/zero of=/tmp/test bs=4M count=256 oflag=direct
  3. 同时跑 eBPF 采集 15 秒（IO 延迟分布出现高频区间）
  4. Web 上对比两个窗口的柱状图
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import json

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class EBPFCollector:
    """bpftrace eBPF 采集器。"""

    OUTPUT_BASE = "/tmp/mini-drop"

    def collect(self, task: CollectorTask) -> CollectorResult:
        bpftrace = shutil.which("bpftrace")
        if bpftrace is None:
            return CollectorResult(
                ok=False,
                reason="bpftrace 命令不可用，请通过 apt install bpftrace 安装",
            )

        script_path = os.path.join(
            os.path.dirname(__file__), "scripts", "io_latency.bt",
        )
        if not os.path.isfile(script_path):
            return CollectorResult(
                ok=False,
                reason=f"bpftrace 脚本未找到: {script_path}",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, "io_latency.txt")

        stderr_text = ""

        try:
            proc = subprocess.Popen(
                [bpftrace, "-o", output_file, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None,  # 独立进程组（仅 Unix）
            )
            try:
                proc.wait(timeout=task.duration_sec)
            except subprocess.TimeoutExpired:
                proc.send_signal(signal.SIGINT)
                try:
                    _, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, stderr = proc.communicate()
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
            else:
                _, stderr = proc.communicate(timeout=10)
                stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0 and proc.returncode != -2:
                return CollectorResult(
                    ok=False,
                    reason=f"bpftrace 执行失败 (exit={proc.returncode}): {stderr_text[:200]}",
                )

        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return CollectorResult(
                ok=False,
                reason=f"bpftrace 异常: {exc}",
            )

        if not os.path.isfile(output_file):
            return CollectorResult(
                ok=False,
                reason="bpftrace 未产出 IO 延迟原始文件",
            )

        # 解析 histogram 输出
        histogram = self._parse_histogram(output_file)
        metrics = {
            "io_latency_us": histogram,
            "total_samples": sum(v for v in histogram.values()),
        }

        metrics_path = os.path.join(output_dir, "ebpf_metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2)

        return CollectorResult(
            ok=True,
            reason="bpftrace IO 延迟采集完成",
            artifacts=[
                {
                    "artifact_type": "ebpf_metrics",
                    "filename": "ebpf_metrics.json",
                    "local_path": metrics_path,
                    "content_type": "application/json",
                    "size_bytes": os.path.getsize(metrics_path),
                },
                {
                    "artifact_type": "ebpf_raw",
                    "filename": "io_latency.txt",
                    "local_path": output_file,
                    "content_type": "text/plain",
                    "size_bytes": os.path.getsize(output_file) if os.path.isfile(output_file) else 0,
                },
            ],
        )

    @staticmethod
    def _parse_histogram(path: str) -> dict[str, int]:
        """解析 bpftrace histogram 输出为 {bucket: count} 字典。

        bpftrace 输出格式:
          @latency_us:
          [128, 256)      10 |@@@@                                        |
          [256, 512)       5 |@@                                          |
        """
        result: dict[str, int] = {}
        if not os.path.isfile(path):
            return result

        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        pattern = re.compile(
            r"\[\s*(\d+[KkMm]?)\s*,\s*(\d+[KkMm]?)\s*\)\s+(\d+)"
        )
        for match in pattern.finditer(content):
            lower = EBPFCollector._normalize_bucket_value(match.group(1))
            upper = EBPFCollector._normalize_bucket_value(match.group(2))
            count = int(match.group(3))
            key = f"[{lower}, {upper})"
            result[key] = count

        return result

    @staticmethod
    def _normalize_bucket_value(value: str) -> str:
        suffix = value[-1].lower()
        if suffix == "k":
            return str(int(value[:-1]) * 1000)
        if suffix == "m":
            return str(int(value[:-1]) * 1000000)
        return value

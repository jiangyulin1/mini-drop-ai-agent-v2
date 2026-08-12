"""Go pprof 采集器。

通过 net/http/pprof HTTP 端点对 Go 进程进行 CPU profile 采集。

前置条件：
  1. 目标 Go 程序已启用 net/http/pprof（import _ "net/http/pprof"）
  2. pprof HTTP 端口可访问（默认 6060，可通过 options.port 指定）
  3. Agent 可与目标进程网络互通

执行流程：
  1. 构造 pprof URL
  2. HTTP GET 拉取 profile（阻塞 duration_sec 秒）
  3. 保存原始 pprof 数据（protocol buffer gzip）
  4. 尝试 go tool pprof 生成 SVG 火焰图（可选）
  5. 返回产物元数据
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class PprofCollector:
    """Go pprof HTTP 采集器。"""

    OUTPUT_BASE = "/tmp/mini-drop"
    DEFAULT_PORT = 6060
    DEFAULT_ENDPOINT = "/debug/pprof/profile"
    # pprof 响应体上限：Go profile 正常为几百 KB ~ 几十 MB；超过此值
    # 视为异常响应（恶意服务/内存损坏），防止 Agent OOM 或磁盘写满。
    MAX_RESPONSE_BYTES = 512 * 1024 * 1024

    def collect(self, task: CollectorTask) -> CollectorResult:
        port = task.options.get("port", self.DEFAULT_PORT)
        endpoint = task.options.get("pprof_endpoint", self.DEFAULT_ENDPOINT)

        # 输入校验：endpoint 限定在 pprof 常规路径内，避免被驱动为任意
        # localhost 服务代理（SSRF 原语）。
        if not isinstance(port, int) or port < 1 or port > 65535:
            return CollectorResult(ok=False, reason=f"无效的端口: {port}")
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            return CollectorResult(ok=False, reason=f"无效的 endpoint: {endpoint}，必须以 / 开头")
        if endpoint not in {
            "/debug/pprof/profile", "/debug/pprof/heap", "/debug/pprof/goroutine",
            "/debug/pprof/block", "/debug/pprof/mutex", "/debug/pprof/allocs",
            "/debug/pprof/threadcreate",
        }:
            return CollectorResult(ok=False, reason=f"无效的 endpoint: {endpoint}，只允许标准 pprof 路径")

        timeout = task.duration_sec + 30

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        pprof_raw = os.path.join(output_dir, "profile.pb.gz")
        flamegraph_svg = os.path.join(output_dir, "flamegraph.svg")

        # 先用 URL 拉取原始 pprof 数据
        try:
            import urllib.request
            import urllib.error

            url = f"http://localhost:{port}{endpoint}"
            if "?" in endpoint:
                url += f"&seconds={task.duration_sec}"
            else:
                url += f"?seconds={task.duration_sec}"

            req = urllib.request.Request(url)
            # 禁用重定向：pprof 端点不应重定向，且 urllib 默认跟随重定向
            # 可能被诱导跳转到任意内网地址。
            with urllib.request.urlopen(
                req, timeout=timeout,
                context=self._no_redirect_opener(),
            ) as resp:
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.MAX_RESPONSE_BYTES:
                        return CollectorResult(
                            ok=False,
                            reason=f"pprof 响应超过 {self.MAX_RESPONSE_BYTES} 字节上限，已中止",
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)

            if not data:
                return CollectorResult(
                    ok=False,
                    reason=f"pprof {url} 返回空数据，目标 Go 进程可能未启用 pprof",
                )

            with open(pprof_raw, "wb") as fh:
                fh.write(data)

        except urllib.error.HTTPError as exc:
            return CollectorResult(
                ok=False,
                reason=f"pprof HTTP {exc.code}: {url}，请确认目标进程已启用 net/http/pprof",
            )
        except urllib.error.URLError as exc:
            return CollectorResult(
                ok=False,
                reason=f"pprof 连接失败: {exc.reason}，请确认端口 {port} 可访问",
            )
        except Exception as exc:
            return CollectorResult(
                ok=False,
                reason=f"pprof 采集异常: {exc}",
            )

        raw_size = os.path.getsize(pprof_raw) if os.path.isfile(pprof_raw) else 0
        artifacts: list[dict] = [{
            "artifact_type": "pprof_raw",
            "filename": "profile.pb.gz",
            "local_path": pprof_raw,
            "content_type": "application/octet-stream",
            "size_bytes": raw_size,
        }]

        # 可选：用 go tool pprof 生成 SVG 火焰图
        svg_ok = self._pprof_to_svg(pprof_raw, flamegraph_svg, timeout=60)
        if svg_ok and os.path.isfile(flamegraph_svg):
            svg_size = os.path.getsize(flamegraph_svg)
            artifacts.append({
                "artifact_type": "flamegraph_svg",
                "filename": "flamegraph.svg",
                "local_path": flamegraph_svg,
                "content_type": "image/svg+xml",
                "size_bytes": svg_size,
            })

        return CollectorResult(
            ok=True,
            reason=f"pprof 采集完成，{raw_size} 字节" + ("，已生成火焰图" if svg_ok else "（go 未安装，跳过 SVG 生成）"),
            artifacts=artifacts,
        )

    # ── 内部方法 ────────────────────────────────────────────────

    @staticmethod
    def _no_redirect_opener() -> Any:
        """返回拒绝一切 HTTP 重定向的 opener（防 SSRF 跳转）。"""
        import urllib.request

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):  # noqa: ARG002
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        return opener

    @staticmethod
    def _pprof_to_svg(raw_path: str, output_path: str, timeout: int = 60) -> bool:
        go_bin = PprofCollector._find_go()
        if go_bin is None:
            return False

        try:
            proc = subprocess.run(
                [go_bin, "tool", "pprof", "-svg", "-output", output_path, raw_path],
                capture_output=True,
                timeout=timeout,
            )
            return proc.returncode == 0 and os.path.isfile(output_path)
        except (subprocess.SubprocessError, OSError):
            return False

    @staticmethod
    def _find_go() -> str | None:
        import shutil
        return shutil.which("go")

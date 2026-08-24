#!/usr/bin/env python3
"""Mini-Drop 跨平台开发命令。

用法:
    python dev.py proto         编译 gRPC stub
    python dev.py server        启动 Server
    python dev.py start         启动完整本地工作台（默认 Pi Runtime）
    python dev.py mcp           启动 MCP Server
    python dev.py agent         启动 Agent
    python dev.py test          运行全部测试
    python dev.py test -k xxx   按关键字筛选测试
    python dev.py lint          静态检查
    python dev.py demo          一键演示
    python dev.py install       安装依赖

所有命令在各平台（Linux / macOS / Windows）行为一致。
"""

import subprocess
import sys
import os
import signal
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_start_env(environment: dict[str, str]) -> None:
    """Load simple KEY=VALUE entries so Node sidecars receive the same .env."""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key in environment:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        environment[key] = value


def cmd_proto():
    """编译 .proto 文件为 Python gRPC stub。"""
    script = ROOT / "scripts" / "compile_proto.py"
    if script.is_file():
        subprocess.check_call([sys.executable, str(script)], cwd=ROOT)
    else:
        sys.exit("scripts/compile_proto.py 不存在，请先拉取项目完整代码")


def cmd_server():
    """启动 FastAPI + gRPC 双端口 Server。"""
    subprocess.check_call([sys.executable, "-m", "server.app.main"], cwd=ROOT)


def cmd_start():
    """启动完整本地工作台：Server、Pi sidecar、Analyzer、Agent 和 Web。"""
    env = os.environ.copy()
    _load_start_env(env)
    # The local workbench uses the Pi path by default.  Set deterministic
    # explicitly when an offline compatibility run is desired.
    env.setdefault("MINI_DROP_AGENT_RUNTIME", "pi")
    env.setdefault("MINI_DROP_PI_RUNTIME_URL", "http://127.0.0.1:8899")
    env.setdefault("MINI_DROP_PI_INTERNAL_BASE", "http://127.0.0.1:8191")
    env.setdefault("MINI_DROP_PI_SIDECAR_PORT", "8899")
    env.setdefault("SERVER_HOST", "127.0.0.1")
    env.setdefault("SERVER_PORT", "8191")
    env.setdefault("MINI_DROP_WEB_API_TARGET", "http://127.0.0.1:8191")
    env.setdefault("MINI_DROP_API_AUTH_ENABLED", "0")
    env.setdefault("MINI_DROP_API_TENANT_ID", "local-development")
    env.setdefault("MINI_DROP_API_ROLES", "operator,authorization_admin")
    env.setdefault("MINI_DROP_REQUIRE_STORAGE", "0")
    env.setdefault("MINI_DROP_REQUIRE_ANALYZER", "0")
    env.setdefault("MINI_DROP_PI_INTERNAL_TOKEN", "")

    commands = [
        (["node", "src/server.mjs"], ROOT / "agent_runtime" / "pi-sidecar", "pi-sidecar"),
        ([sys.executable, "-m", "server.app.main"], ROOT, "server"),
        ([sys.executable, "-m", "analyzer.mini_drop_analyzer.worker"], ROOT, "analyzer"),
        ([sys.executable, "-m", "agent.mini_drop_agent.main"], ROOT, "agent"),
        (["npm", "run", "dev", "--", "--host", "127.0.0.1"], ROOT / "web", "web"),
    ]
    processes: list[tuple[subprocess.Popen[bytes], str]] = []

    def stop_all(*_args: object, exit_code: int = 0) -> None:
        for process, _name in reversed(processes):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 8
        for process, _name in reversed(processes):
            if process.poll() is None:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    process.kill()
        raise SystemExit(exit_code)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        for command, cwd, name in commands:
            try:
                process = subprocess.Popen(command, cwd=cwd, env=env)
            except FileNotFoundError as exc:
                stop_all()
                raise SystemExit(f"无法启动 {name}：缺少 {command[0]}") from exc
            processes.append((process, name))
            print(f"[start] {name} 已启动", flush=True)
        print("[start] Web: http://127.0.0.1:5173/", flush=True)
        print("[start] API: http://127.0.0.1:8191/openapi.json", flush=True)
        while True:
            for process, name in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"[start] {name} 已退出，code={return_code}", file=sys.stderr, flush=True)
                    stop_all(exit_code=return_code or 1)
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()


def cmd_agent():
    """启动 Agent，采集器类型由 COLLECTORS 注册决定。"""
    subprocess.check_call([sys.executable, "-m", "agent.mini_drop_agent.main"], cwd=ROOT)


def cmd_mcp():
    """启动独立 MCP Server（需要 pip install -e '.[mcp]'）。"""
    subprocess.check_call([sys.executable, "-m", "server.app.mcp_integration.server"], cwd=ROOT)


def cmd_analyzer_worker():
    """启动持久化 AnalysisJob Worker。"""
    subprocess.check_call([sys.executable, "-m", "analyzer.mini_drop_analyzer.worker"], cwd=ROOT)


def cmd_test():
    """运行 pytest，透传额外参数。"""
    args = sys.argv[2:]  # 跳过 dev.py test
    subprocess.check_call([sys.executable, "-m", "pytest", "tests", "-v"] + args, cwd=ROOT)


def cmd_lint():
    """编译期语法检查。"""
    dirs = [str(ROOT / d) for d in ("server", "agent", "analyzer", "demo") if (ROOT / d).is_dir()]
    subprocess.check_call([sys.executable, "-m", "compileall"] + dirs, cwd=ROOT)


def cmd_demo():
    """一键演示——仅 Linux 环境可用。"""
    script = ROOT / "demo" / "demo.sh"
    if script.is_file():
        subprocess.check_call(["bash", str(script)], cwd=ROOT)
    else:
        print("demo.sh 需要在 Linux 环境运行")
        sys.exit(1)


def cmd_install():
    """安装项目开发依赖。"""
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT)


COMMANDS = {
    "proto":   cmd_proto,
    "server":  cmd_server,
    "start":   cmd_start,
    "mcp":     cmd_mcp,
    "agent":   cmd_agent,
    "analyzer-worker": cmd_analyzer_worker,
    "test":    cmd_test,
    "lint":    cmd_lint,
    "demo":    cmd_demo,
    "install": cmd_install,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法: python dev.py <命令>")
        print()
        for name in COMMANDS:
            print(f"  {name:10}  {COMMANDS[name].__doc__ or ''}")
        print()
        print("Python {}.{}.{}  |  platform={}".format(*sys.version_info[:3], sys.platform))
        sys.exit(0 if len(sys.argv) > 1 else 1)

    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Mini-Drop 跨平台开发命令。

用法:
    python dev.py proto         编译 gRPC stub
    python dev.py server        启动 Server
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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

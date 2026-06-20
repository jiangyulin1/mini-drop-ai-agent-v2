"""自动加载项目根目录的 .env 文件。

在本地开发时（非 Docker 环境），自动查找并加载 .env，
避免每次手动 export 环境变量。
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_env_file() -> Path | None:
    """从当前工作目录向上查找 .env 文件，最多 3 层。"""
    cwd = Path.cwd()
    for _ in range(4):
        candidate = cwd / ".env"
        if candidate.is_file():
            return candidate
        parent = cwd.parent
        if parent == cwd:
            break
        cwd = parent
    return None


def _is_docker() -> bool:
    """检测是否在 Docker 容器内运行。"""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r") as fh:
            return "docker" in fh.read()
    except (OSError, FileNotFoundError):
        pass
    return False


def _load_dotenv() -> None:
    """如果 .env 存在则逐行加载到 os.environ（不覆盖已存在的变量）。

    本地模式（非 Docker）时，跳过依赖 Docker 服务名的变量，
    让代码使用 SQLite / 本地文件存储等默认值。
    """
    env_file = _find_env_file()
    if env_file is None:
        return

    in_docker = _is_docker()

    # 本地模式下跳过这些 Docker 专属变量，使用代码默认值
    _docker_only_keys = {
        "DATABASE_URL",            # Docker: postgres@postgres:5432  本地: sqlite:///
        "MINIO_ENDPOINT",          # Docker: minio:9000              本地: 不上传MinIO
        "MINIO_PUBLIC_ENDPOINT",   # Docker: localhost:9000          本地: 默认值
        "MINIO_AUTO_CREATE_BUCKET",# Docker: 1                       本地: 0 (SQLite模式)
        "AGENT_GRPC_ADDR",         # Docker: server:50051            本地: localhost
    }

    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                # 本地模式：跳过 Docker 专属变量
                if not in_docker and key in _docker_only_keys:
                    continue
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


# 模块导入时自动执行
_load_dotenv()

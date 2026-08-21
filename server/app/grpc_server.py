"""gRPC 服务器启动模块。

在后台线程中运行 gRPC server（端口 50051），
与 FastAPI HTTP server（端口 8191）共存于同一进程。
两者共享同一个 Repository 实例。

TLS 支持：
  设置 MINI_DROP_GRPC_SECURE=1 启用 TLS。
  设置 MINI_DROP_GRPC_CERT_FILE / MINI_DROP_GRPC_KEY_FILE 指定证书路径。
  未设置时使用 insecure 模式（仅适用于开发/演示环境）。
"""

from __future__ import annotations

import os
from concurrent import futures
from typing import Any

import grpc

from server.app.grpc_auth import GrpcAuthInterceptor
from server.app.generated import (
    control_pb2_grpc,
    healthcheck_pb2_grpc,
    hotmethod_pb2_grpc,
    init_pb2_grpc,
)
from server.app.grpc_services.control_service import ControlService
from server.app.grpc_services.healthcheck_service import HealthCheckService
from server.app.grpc_services.hotmethod_service import HotmethodService
from server.app.grpc_services.init_service import InitAgentService


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


def grpc_bind_address(port: int) -> str:
    """Return the gRPC bind address without changing the historic default.

    ``MINI_DROP_GRPC_HOST`` is useful for a native local Control process where
    the Agent port must not be reachable from the LAN.  Existing deployments
    leave it unset and retain the previous ``0.0.0.0:<port>`` binding.
    """
    host = os.getenv("MINI_DROP_GRPC_HOST", "0.0.0.0").strip() or "0.0.0.0"
    if host.startswith("[") and host.endswith("]"):
        return f"{host}:{port}"
    if ":" in host:
        # gRPC requires brackets around a literal IPv6 host in host:port form.
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _add_port(server: grpc.Server, address: str) -> int:
    """Add a gRPC port, optionally secured with TLS."""
    if _env_bool("MINI_DROP_GRPC_SECURE", default=False):
        cert_file = os.getenv("MINI_DROP_GRPC_CERT_FILE", "").strip()
        key_file = os.getenv("MINI_DROP_GRPC_KEY_FILE", "").strip()
        if not cert_file or not key_file:
            raise RuntimeError(
                "MINI_DROP_GRPC_SECURE=1 requires MINI_DROP_GRPC_CERT_FILE and MINI_DROP_GRPC_KEY_FILE"
            )
        with open(key_file, "rb") as fh:
            private_key = fh.read()
        with open(cert_file, "rb") as fh:
            certificate_chain = fh.read()
        server_credentials = grpc.ssl_server_credentials([(private_key, certificate_chain)])
        bound_port = server.add_secure_port(address, server_credentials)
    else:
        bound_port = server.add_insecure_port(address)
    if bound_port == 0:
        raise RuntimeError(f"failed to bind gRPC address: {address}")
    return bound_port


def _build_server() -> grpc.Server:
    options = [
        # 显式消息大小限制：默认 4MiB 接收上限对 perf 产物元数据偏紧，
        # 但也不能无限放——设 64MiB 上限防止恶意 Agent 拖垮线程池。
        ("grpc.max_receive_message_length", 64 * 1024 * 1024),
        ("grpc.max_send_message_length", 64 * 1024 * 1024),
    ]
    return grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[GrpcAuthInterceptor()],
        options=options,
    )


def _register_control_service(server: grpc.Server, repo: Any) -> None:
    """注册 Control 服务（Server 内部管理用）。

    Control 服务不校验入参且按注释仅供内部/测试使用，不应暴露在面向
    Agent 的公开端口上。仅当显式设置 MINI_DROP_GRPC_ENABLE_CONTROL=1
    （测试/调试）时注册，生产默认不暴露。
    """
    if _env_bool("MINI_DROP_GRPC_ENABLE_CONTROL", default=False):
        control_pb2_grpc.add_ControlServicer_to_server(ControlService(repo), server)


def serve(repo: Any, port: int = 50051) -> grpc.Server:
    """创建并启动 gRPC server。

    Returns:
        grpc.Server 实例，调用方负责在进程退出时调用 server.stop()。
    """
    server = _build_server()

    init_pb2_grpc.add_InitAgentServicer_to_server(InitAgentService(repo), server)
    healthcheck_pb2_grpc.add_HealthCheckServicer_to_server(HealthCheckService(repo), server)
    hotmethod_pb2_grpc.add_HotmethodServicer_to_server(HotmethodService(repo), server)
    _register_control_service(server, repo)

    _add_port(server, grpc_bind_address(port))
    server.start()
    return server


def serve_in_background(repo: Any, port: int = 50051) -> grpc.Server:
    """在后台守护线程启动 gRPC server，主线程继续执行 HTTP server。"""

    grpc_server = _build_server()

    init_pb2_grpc.add_InitAgentServicer_to_server(InitAgentService(repo), grpc_server)
    healthcheck_pb2_grpc.add_HealthCheckServicer_to_server(HealthCheckService(repo), grpc_server)
    hotmethod_pb2_grpc.add_HotmethodServicer_to_server(HotmethodService(repo), grpc_server)
    _register_control_service(grpc_server, repo)

    _add_port(grpc_server, grpc_bind_address(port))
    grpc_server.start()

    return grpc_server

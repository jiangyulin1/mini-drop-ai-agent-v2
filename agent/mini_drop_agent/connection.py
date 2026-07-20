"""gRPC connection management for the Agent.

Supports both insecure (default, for dev/demo) and TLS-secured channels.
Set AGENT_GRPC_SECURE=1 to enable TLS with default system root CAs.
Set AGENT_GRPC_CA_CERT to specify a custom CA certificate path.
"""

from __future__ import annotations

import os
import time
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

import grpc

T = TypeVar("T")

# gRPC status codes worth retrying (transient failures)
_RETRYABLE_CODES = frozenset({
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
})

_ClientCallDetails = namedtuple(
    "_ClientCallDetails",
    ("method", "timeout", "metadata", "credentials", "wait_for_ready", "compression"),
)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


def _build_channel(address: str) -> grpc.Channel:
    """Create a gRPC channel, optionally secured with TLS."""
    if _env_bool("AGENT_GRPC_SECURE", default=False):
        ca_cert = os.getenv("AGENT_GRPC_CA_CERT", "").strip()
        if ca_cert:
            with open(ca_cert, "rb") as fh:
                creds = grpc.ssl_channel_credentials(root_certificates=fh.read())
        else:
            creds = grpc.ssl_channel_credentials()
        server_name = os.getenv("AGENT_GRPC_TLS_SERVER_NAME", "").strip()
        options = []
        if server_name:
            # 仍会验证 CA 签名；该选项只用于证书 SAN 与连接地址不同的实验网络。
            options.extend([
                ("grpc.ssl_target_name_override", server_name),
                ("grpc.default_authority", server_name),
            ])
        return grpc.secure_channel(address, creds, options=options)
    return grpc.insecure_channel(address)


class _AuthClientInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, token: str):
        self._token = token

    def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata: Sequence[tuple[str, str]] = client_call_details.metadata or ()
        details = _ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=tuple(metadata) + (("x-mini-drop-grpc-token", self._token),),
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
            compression=client_call_details.compression,
        )
        return continuation(details, request)


@dataclass
class GrpcConnection:
    address: str
    auth_token: str = ""
    _channel: grpc.Channel | None = field(default=None, init=False)

    @property
    def channel(self) -> grpc.Channel:
        if self._channel is None:
            channel = _build_channel(self.address)
            if self.auth_token:
                channel = grpc.intercept_channel(channel, _AuthClientInterceptor(self.auth_token))
            self._channel = channel
        return self._channel

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    def reconnect(self) -> None:
        self.close()

    def call_with_retry(self, func: Callable[[], T], max_retries: int = 3, backoff_sec: float = 1.0) -> T:
        """Call a gRPC function with retry and exponential backoff.

        Retries on transient errors: UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED.
        """
        last_exc: grpc.RpcError | None = None
        delay = backoff_sec
        for attempt in range(max_retries + 1):
            try:
                return func()
            except grpc.RpcError as exc:
                last_exc = exc
                if attempt >= max_retries or exc.code() not in _RETRYABLE_CODES:
                    raise
                self.reconnect()
                time.sleep(delay)
                delay *= 2
        raise last_exc

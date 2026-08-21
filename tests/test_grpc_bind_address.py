"""Focused tests for the configurable gRPC listener host."""

from server.app.grpc_server import grpc_bind_address


def test_default_preserves_existing_all_interfaces_binding(monkeypatch):
    monkeypatch.delenv("MINI_DROP_GRPC_HOST", raising=False)

    assert grpc_bind_address(50051) == "0.0.0.0:50051"


def test_explicit_loopback_host_is_supported(monkeypatch):
    monkeypatch.setenv("MINI_DROP_GRPC_HOST", "127.0.0.1")

    assert grpc_bind_address(50051) == "127.0.0.1:50051"


def test_ipv6_host_is_bracketed(monkeypatch):
    monkeypatch.setenv("MINI_DROP_GRPC_HOST", "::1")

    assert grpc_bind_address(50051) == "[::1]:50051"

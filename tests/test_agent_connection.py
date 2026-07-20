import grpc

from agent.mini_drop_agent.connection import GrpcConnection, _build_channel


class FakeChannel:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRpcError(grpc.RpcError):
    def __init__(self, status_code):
        super().__init__()
        self._status_code = status_code

    def code(self):
        return self._status_code


def test_channel_is_created_lazily_and_reused(monkeypatch):
    channels = []

    def fake_insecure_channel(address):
        channels.append((address, FakeChannel()))
        return channels[-1][1]

    monkeypatch.setattr(grpc, "insecure_channel", fake_insecure_channel)
    conn = GrpcConnection("server:50051")

    first = conn.channel
    second = conn.channel

    assert first is second
    assert len(channels) == 1
    assert channels[0][0] == "server:50051"


def test_auth_token_wraps_channel_with_interceptor(monkeypatch):
    raw_channel = FakeChannel()
    intercepted = FakeChannel()
    captured = {}

    def fake_intercept_channel(channel, interceptor):
        captured["channel"] = channel
        captured["interceptor"] = interceptor
        return intercepted

    monkeypatch.setattr(grpc, "insecure_channel", lambda _address: raw_channel)
    monkeypatch.setattr(grpc, "intercept_channel", fake_intercept_channel)
    conn = GrpcConnection("server:50051", auth_token="secret")

    assert conn.channel is intercepted
    assert captured["channel"] is raw_channel
    assert captured["interceptor"]._token == "secret"


def test_close_resets_channel(monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(grpc, "insecure_channel", lambda _address: channel)
    conn = GrpcConnection("server:50051")

    assert conn.channel is channel
    conn.close()

    assert channel.closed is True
    assert conn._channel is None


def test_call_with_retry_reconnects_on_unavailable(monkeypatch):
    channels = []

    def fake_insecure_channel(_address):
        channel = FakeChannel()
        channels.append(channel)
        return channel

    monkeypatch.setattr(grpc, "insecure_channel", fake_insecure_channel)
    conn = GrpcConnection("server:50051")
    attempts = {"count": 0}

    def flaky_call():
        _ = conn.channel
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FakeRpcError(grpc.StatusCode.UNAVAILABLE)
        return "ok"

    assert conn.call_with_retry(flaky_call, max_retries=1) == "ok"
    assert attempts["count"] == 2
    assert channels[0].closed is True
    assert len(channels) == 2


def test_secure_channel_uses_custom_ca_and_server_name(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(b"test-ca")
    captured = {}

    monkeypatch.setenv("AGENT_GRPC_SECURE", "1")
    monkeypatch.setenv("AGENT_GRPC_CA_CERT", str(ca_file))
    monkeypatch.setenv("AGENT_GRPC_TLS_SERVER_NAME", "control.internal")
    monkeypatch.setattr(
        grpc,
        "ssl_channel_credentials",
        lambda root_certificates=None: ("credentials", root_certificates),
    )

    def fake_secure_channel(address, credentials, options=None):
        captured.update(address=address, credentials=credentials, options=options)
        return FakeChannel()

    monkeypatch.setattr(grpc, "secure_channel", fake_secure_channel)

    _build_channel("10.0.0.10:50051")

    assert captured["address"] == "10.0.0.10:50051"
    assert captured["credentials"] == ("credentials", b"test-ca")
    assert ("grpc.ssl_target_name_override", "control.internal") in captured["options"]

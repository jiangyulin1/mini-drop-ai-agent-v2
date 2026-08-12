import builtins
import io

from agent.mini_drop_agent.collectors.sys_metrics import SysMetricsCollector


def test_legacy_summary_exposes_oom_disk_and_tcp_facts():
    value = {
        "host": {
            "cpu": {"user_ratio": 0.1, "system_ratio": 0.2, "iowait_ratio": 0.0, "core_count": 4},
            "load": {"load1": 1, "load1_window_avg": 1, "load1_slope_per_second": 0},
            "memory": {"total_bytes": 1000, "available_bytes": 40},
            "psi": {"memory_some_avg10": 12.5},
            "network": {
                "rx_bytes_per_second": 0,
                "tx_bytes_per_second": 0,
                "tcp": {"out_segments": 100, "retrans_segments": 12, "retransmit_ratio": 0.12, "TCPTimeouts": 4},
            },
            "filesystems": {
                "/": {"used_ratio": 0.99, "available_bytes": 100},
                "target_root": {"used_ratio": 0.98, "available_bytes": 200},
            },
        },
        "process": {
            "cpu": {"normalized_core_usage": 0.0},
            "memory": {"rss_bytes": 1024, "rss_slope_bytes_per_second": 0},
            "fd": {"count": 1, "growth_per_minute": 0},
            "io": {"read_bytes_per_second": 0, "write_bytes_per_second": 0},
            "threads": {"count": 1, "growth_per_minute": 0},
        },
        "container": {
            "memory_current_bytes": 900,
            "memory_limit_bytes": 1000,
            "memory_usage_ratio": 0.9,
            "memory_event_deltas": {"oom": 1, "oom_kill": 1},
        },
    }
    summary = SysMetricsCollector._legacy_summary(value)
    assert summary["container_oom_kill_delta"] == 1
    assert summary["root_fs_used_pct"] == 99.0
    assert summary["tcp_retransmit_pct"] == 12.0
    assert summary["host_memory_available_ratio"] == 0.04


def test_network_readers_follow_target_network_namespace(monkeypatch):
    opened = []
    real_open = builtins.open
    fixtures = {
        "/proc/42/net/dev": "Inter-| Receive | Transmit\n eth0: 10 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0\n",
        "/proc/42/net/snmp": "Tcp: OutSegs RetransSegs InErrs OutRsts\nTcp: 100 7 1 2\n",
        "/proc/42/net/netstat": "TcpExt: TCPTimeouts ListenDrops\nTcpExt: 3 4\n",
    }

    def fake_open(path, *args, **kwargs):
        path = str(path)
        opened.append(path)
        if path in fixtures:
            return io.StringIO(fixtures[path])
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    assert SysMetricsCollector._read_network_dev(42) == {"rx_bytes": 10, "tx_bytes": 20}
    counters = SysMetricsCollector._read_tcp_counters(42)
    assert counters["retrans_segments"] == 7
    assert counters["TCPTimeouts"] == 3
    assert set(opened) == set(fixtures)

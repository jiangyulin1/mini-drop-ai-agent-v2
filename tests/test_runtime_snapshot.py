import io
import struct

from agent.mini_drop_agent.collectors.runtime_snapshot import RuntimeSnapshotCollector
from analyzer.mini_drop_analyzer.worker import ANALYSIS_RESULT_TYPES
from server.app.grpc_services.hotmethod_service import _has_analysis_result
from server.app.schemas import CreateTaskRequest


def test_runtime_summary_reports_lock_ratio_and_runtime():
    samples = [
        {
            "thread_count": 10,
            "lock_waiter_count": 2,
            "thread_states": {"S": 10},
            "top_wait_channels": [{"name": "futex_wait_queue", "count": 2}],
            "process": {"cpu_ticks": 100},
        },
        {
            "thread_count": 10,
            "lock_waiter_count": 7,
            "thread_states": {"S": 8, "D": 2},
            "top_wait_channels": [{"name": "futex_wait_queue", "count": 7}],
            "process": {"cpu_ticks": 103},
        },
    ]
    result = RuntimeSnapshotCollector._summarize(samples, "java")
    assert result["runtime_type"] == "java"
    assert result["blocked_thread_ratio_max"] == 0.7
    assert result["lock_waiter_count_max"] == 7
    assert result["uninterruptible_thread_count_max"] == 2
    assert result["cpu_tick_delta"] == 3


def test_runtime_snapshot_is_accepted_by_http_task_schema():
    payload = CreateTaskRequest(
        name="runtime snapshot",
        agent_id="worker-1",
        target_pid=123,
        collector_type="runtime_snapshot",
        duration_sec=3,
    )

    assert payload.collector_type == "runtime_snapshot"


def test_runtime_and_actuation_artifacts_are_terminal_results():
    assert {"runtime_metrics", "actuation_result"} <= ANALYSIS_RESULT_TYPES
    assert _has_analysis_result([{"artifact_type": "runtime_metrics"}])
    assert _has_analysis_result([{"artifact_type": "actuation_result"}])


def test_go_runtime_detection_reads_bounded_elf_section_names():
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 40, 64)
    struct.pack_into("<HHH", header, 58, 64, 2, 1)
    section_table = bytearray(128)
    names = b"\0.shstrtab\0.go.buildinfo\0"
    struct.pack_into("<QQ", section_table, 64 + 24, 192, len(names))
    elf = io.BytesIO(bytes(header) + bytes(section_table) + names)

    assert RuntimeSnapshotCollector._elf_has_go_section(elf, bytes(header)) is True

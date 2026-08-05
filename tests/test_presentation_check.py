from __future__ import annotations

import json

from demo.presentation.presentation_check import run_checks


TASK_ID = "task_demo"
DIAGNOSIS_ID = "diag_demo"


class FakeClient:
    def __init__(self, *, artifact_content: bytes = b'{"samples": [1]}') -> None:
        self.artifact_content = artifact_content

    def get_json(self, path: str):
        payloads = {
            "/api/healthz": {"code": 0, "data": {"healthy": True}},
            "/api/agents": {
                "code": 0,
                "data": {
                    "items": [
                        {"id": "worker-1", "status": "ONLINE"},
                        {"id": "worker-2", "status": "ONLINE"},
                    ]
                },
            },
            f"/api/tasks/{TASK_ID}": {
                "code": 0,
                "data": {"id": TASK_ID, "status": "DONE"},
            },
            f"/api/tasks/{TASK_ID}/artifacts": {
                "code": 0,
                "data": {
                    "items": [
                        {"artifact_type": "flamegraph_svg", "filename": "demo.svg"}
                    ]
                },
            },
            f"/api/tasks/{TASK_ID}/diagnoses": {
                "code": 0,
                "data": {
                    "items": [{"id": DIAGNOSIS_ID, "status": "DONE"}]
                },
            },
            f"/api/diagnoses/{DIAGNOSIS_ID}": {
                "code": 0,
                "data": {
                    "run": {"status": "DONE", "validated": True},
                    "report": {
                        "report": {
                            "summary": "热点位于 demo.py:10",
                            "not_enough_evidence": False,
                            "ranked_causes": [{"cause_id": "cpu_hotspot"}],
                        }
                    },
                    "tool_results": [
                        {"tool_name": "get_flamegraph_top", "status": "success"}
                    ],
                },
            },
        }
        return payloads[path]

    def get_bytes(self, path: str) -> bytes:
        if path == "/":
            return b"<title>Mini-Drop</title>"
        return self.artifact_content


def _manifest() -> dict:
    return {
        "title": "Test showcase",
        "minimum_online_agents": 2,
        "tasks": [
            {
                "title": "Python flamegraph",
                "task_id": TASK_ID,
                "expected_artifacts": ["flamegraph_svg"],
                "diagnosis": {
                    "required": True,
                    "expected_status": "DONE",
                    "expected_tool_statuses": {
                        "get_flamegraph_top": "success"
                    },
                },
            }
        ],
    }


def test_presentation_preflight_accepts_complete_evidence_chain():
    report = run_checks(_manifest(), FakeClient())

    assert report["passed"] is True
    assert report["summary"] == {"total": 5, "passed": 5, "failed": 0}
    assert report["presentation_urls"][0]["path"] == f"/task/{TASK_ID}"


def test_presentation_preflight_rejects_empty_download():
    report = run_checks(_manifest(), FakeClient(artifact_content=b""))

    assert report["passed"] is False
    task_check = next(item for item in report["checks"] if item["name"] == "Python flamegraph")
    assert "内容为空" in task_check["detail"]


def test_showcase_manifest_is_valid_json():
    from demo.presentation.presentation_check import DEFAULT_MANIFEST

    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    assert len(manifest["tasks"]) == 4
    assert all(item["expected_artifacts"] for item in manifest["tasks"])

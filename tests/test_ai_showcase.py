"""The AI showcase is executable documentation and a CI regression suite."""

from server.app.rca.candidates import generate_candidates
from server.app.rca.models import EvidenceInput

from demo.ai_showcase.run_showcase import (
    run_diagnosis_cases,
    run_intent_cases,
    run_rca_cases,
    run_safety_cases,
)


def test_showcase_intent_matrix_passes():
    report = run_intent_cases()
    assert report["failed"] == 0
    assert report["total"] >= 8


def test_showcase_diagnosis_matrix_passes():
    report = run_diagnosis_cases()
    assert report["failed"] == 0
    assert report["total"] >= 11


def test_showcase_task_rca_matrix_passes():
    report = run_rca_cases()
    assert report["failed"] == 0
    assert report["total"] >= 5


def test_showcase_safety_matrix_passes():
    report = run_safety_cases()
    assert report["failed"] == 0
    assert report["total"] >= 9


def test_rca_candidate_generation_accepts_missing_agent_stats(caplog):
    candidates = generate_candidates(EvidenceInput(task_metadata={"status": "DONE"}))
    assert [item.candidate_id for item in candidates] == ["insufficient_data"]
    assert "rule match failed for agent_overhead" not in caplog.text

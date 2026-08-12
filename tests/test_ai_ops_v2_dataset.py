import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "ai_ops_v2"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_private_case_sets_match_and_have_required_coverage() -> None:
    manifest = _load(ROOT / "manifest.json")
    public = _load(ROOT / "public" / "cases.json")
    private = _load(ROOT / "private" / "oracles.json")
    manifest_ids = [item["case_id"] for item in manifest["cases"]]
    public_ids = [item["case_id"] for item in public["cases"]]
    private_ids = [item["case_id"] for item in private["cases"]]

    assert len(manifest_ids) == 30
    assert len(set(manifest_ids)) == 30
    assert set(public_ids) == set(manifest_ids) == set(private_ids)
    assert manifest["tracks"] == {
        "live_single_fault": 16,
        "live_compound_fault": 7,
        "negative_and_robustness": 7,
    }
    assert manifest["policy"]["minimum_repetitions"] >= 3


def test_public_cases_do_not_leak_oracle_or_fixture_details() -> None:
    public = _load(ROOT / "public" / "cases.json")
    forbidden = {
        "oracle", "expected", "fixture", "trigger", "required_collectors",
        "classification", "domain_type", "location_type", "diagnosis_id",
    }
    for case in public["cases"]:
        assert not (set(case) & forbidden), case["case_id"]
        assert case["query"].strip()
        assert case["environment"] == "production"


def test_every_private_oracle_is_scorable() -> None:
    private = _load(ROOT / "private" / "oracles.json")
    for case in private["cases"]:
        expected = case.get("expected") or {}
        assert case.get("fixture"), case["case_id"]
        assert expected.get("abstention") or any(
            expected.get(key) is not None
            for key in ("location_type", "domain_type", "classification", "root_entity")
        ), case["case_id"]
        assert "evidence" in case

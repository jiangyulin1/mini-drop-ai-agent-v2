"""G0: Agent Beta suite skeleton must stay internally consistent and honest."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "benchmarks" / "agent_beta"
CONTRACT = BETA / "contracts" / "public-contract-v1.json"
PROMPT = ROOT / "docs" / "ai_agent_feature_complete_demo_prompt_v6.md"


def test_public_contract_is_bound_to_current_prompt():
    contract = json.loads(CONTRACT.read_text())
    assert contract["prompt_sha256"] == hashlib.sha256(PROMPT.read_bytes()).hexdigest()


def test_public_contract_covers_all_d_requirements():
    contract = json.loads(CONTRACT.read_text())
    requirements = {item["id"] for item in contract["requirements"]}
    assert requirements == {f"D{i:02d}" for i in range(1, 21)}
    covered = set()
    for case in contract["cases"]:
        covered.update(case["requirements"])
    assert covered == requirements


def test_holdout_has_twenty_required_slots():
    contract = json.loads(CONTRACT.read_text())
    ids = {item["id"] for item in contract["holdout_slots"]}
    assert {"H18a", "H18b", "H19"} <= ids
    assert len(ids) >= 20


def test_sources_lock_does_not_fake_digest_verification():
    sources = json.loads((BETA / "sources.lock.json").read_text())
    assert sources["status"] == "PLANNED_NO_DIGEST_VERIFICATION"
    assert all(not item.get("digest_verified") for item in sources["sources"])


def test_validator_passes_without_claiming_holdout():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_agent_beta_suite.py")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "AWAITING_EXTERNAL_HOLDOUT" in proc.stdout

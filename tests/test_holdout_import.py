"""External Holdout score import contract: JCS-like signing + Ed25519 verification."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

ROOT = Path(__file__).resolve().parents[1]
IMPORT = ROOT / "scripts" / "import_agent_beta_score.py"


def _canonical(value):
    from scripts.import_agent_beta_score import canonical_json
    return canonical_json(value)


def _minimal_score():
    return {
        "schema_version": "1.0",
        "verdict": "VERIFIED",
        "suite_id": "dev-suite",
        "public_contract_digest": "a" * 64,
        "evaluator_build_digest": "b" * 40,
        "source_lock_digest": "c" * 40,
        "candidate_archive_digest": "d" * 64,
        "deployed_release_manifest_digest": "e" * 64,
        "model_manifest_digest": "f" * 40,
        "prompt_manifest_digest": "0" * 40,
        "skill_manifest_digest": "1" * 40,
        "knowledge_manifest_digest": "2" * 40,
        "provider_ledger_root_hash": "3" * 64,
        "evidence_pack_root_hash": "4" * 64,
        "started_at": "2026-08-15T00:00:00Z",
        "finished_at": "2026-08-15T01:00:00Z",
        "case_results": [
            {
                "opaque_case_token": f"opaque-{slot}",
                "required_h_slot": slot,
                "scoring_profile": "CAUSAL_SINGLE",
                "actual_realness": "R2",
                "case_verdict": "PASS",
            }
            for slot in [
                "H01", "H02", "H03", "H04", "H05", "H06", "H07", "H08",
                "H09", "H10", "H11", "H12", "H13", "H14", "H15", "H16",
                "H17", "H18a", "H18b", "H19",
            ]
        ],
    }


def _sign(score: dict, private_key) -> tuple[dict, str]:
    payload = score.copy()
    message = _canonical(payload).encode("utf-8")
    signature = private_key.sign(message)
    score["signature"] = {
        "algorithm": "Ed25519",
        "public_key_fingerprint": _fingerprint(private_key.public_key()),
        "value": signature.hex(),
    }
    return score


def _fingerprint(public_key) -> str:
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def _write(tmp_path: Path, score: dict, private_key) -> tuple[Path, Path, Path, str]:
    signed = _sign(score, private_key)
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps(signed), encoding="utf-8")
    key_path = tmp_path / "public.pem"
    key_path.write_text(
        private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode(),
        encoding="utf-8",
    )
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(json.dumps({
        "authority_id": "dev-authority",
        "public_key_pem": key_path.read_text(encoding="utf-8"),
        "public_key_fingerprint": _fingerprint(private_key.public_key()),
    }), encoding="utf-8")
    return score_path, key_path, authority_path, _fingerprint(private_key.public_key())


def test_verified_signature_with_fixed_trust_root(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    score_path, key_path, authority_path, fingerprint = _write(tmp_path, _minimal_score(), key)
    monkeypatch.setenv("MINI_DROP_ACCEPTANCE_ROOT", str(key_path))
    proc = subprocess.run(
        [sys.executable, str(IMPORT), str(score_path), "--authority", str(authority_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"trust_level": "VERIFIED"' in proc.stdout


def test_wrong_public_key_is_invalid_signature(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    wrong = Ed25519PrivateKey.generate()
    score_path, _, authority_path, _ = _write(tmp_path, _minimal_score(), key)
    wrong_path = tmp_path / "wrong.pem"
    wrong_path.write_text(
        wrong.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_DROP_ACCEPTANCE_ROOT", str(wrong_path))
    proc = subprocess.run(
        [sys.executable, str(IMPORT), str(score_path), "--authority", str(authority_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "INVALID" in proc.stdout


def test_missing_holdout_slot_is_rejected(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    score = _minimal_score()
    score["case_results"] = score["case_results"][:-1]
    score_path, key_path, authority_path, _ = _write(tmp_path, score, key)
    monkeypatch.setenv("MINI_DROP_ACCEPTANCE_ROOT", str(key_path))
    proc = subprocess.run(
        [sys.executable, str(IMPORT), str(score_path), "--authority", str(authority_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "H19" in proc.stderr or "too short" in proc.stderr


def test_development_mode_never_upgrades_to_verified(tmp_path):
    key = Ed25519PrivateKey.generate()
    score = _minimal_score()
    score["verdict"] = "DEVELOPMENT_EVAL"
    score_path, key_path, authority_path, _ = _write(tmp_path, score, key)
    proc = subprocess.run(
        [sys.executable, str(IMPORT), str(score_path), "--development"],
        capture_output=True, text=True,
    )
    # Development imports never use the caller key and never upgrade trust.
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"trust_level": "DEVELOPMENT_EVAL"' in proc.stdout

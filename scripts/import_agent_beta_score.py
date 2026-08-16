#!/usr/bin/env python3
"""Import and verify an externally produced Agent Beta Holdout score.

v6 anti-fake-green contract:
  * a caller can no longer supply --public-key/--expected-key-fingerprint;
  * formal verification reads a protected trust root from environment and a
    read-only Authority file;
  * development imports never upgrade trust beyond DEVELOPMENT_EVAL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "benchmarks" / "agent_beta" / "schemas" / "holdout-score-v1.schema.json"
OUT = ROOT / "reports" / "implementation" / "imported-agent-beta-score.json"

REQUIRED_H_SLOTS = [
    "H01", "H02", "H03", "H04", "H05", "H06", "H07", "H08", "H09",
    "H10", "H11", "H12", "H13", "H14", "H15", "H16", "H17", "H18a",
    "H18b", "H19",
]


def canonical_json(value):
    """Canonical JSON used by the v1 evaluator exchange contract.

    Keys are recursively sorted and separators are compact.  Signature bytes
    cover UTF-8 of this representation without the signature field.
    """
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(str(k), ensure_ascii=False, separators=(",", ":"))
            + ":" + canonical_json(value[k])
            for k in sorted(value.keys())
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fingerprint_from_pem(public_key_pem: str) -> str:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_pem_public_key,
    )
    key = load_pem_public_key(public_key_pem.encode())
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def verify_signature(score: dict, public_key_pem: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    payload = score.copy()
    signature_meta = payload.pop("signature")
    message = canonical_json(payload).encode("utf-8")
    key = load_pem_public_key(public_key_pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        return False
    try:
        key.verify(bytes.fromhex(signature_meta["value"]), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def read_trust_root() -> str:
    """Trust root comes from protected host config only, never CLI."""
    path = os.getenv("MINI_DROP_ACCEPTANCE_ROOT", "")
    if not path:
        raise SystemExit("MINI_DROP_ACCEPTANCE_ROOT is not configured")
    pem = Path(path).read_text(encoding="utf-8")
    if "PUBLIC KEY" not in pem:
        raise SystemExit("trust root is not a public key")
    return pem


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("score_file", type=Path)
    parser.add_argument("--authority", type=Path, default=None,
                        help="read-only acceptance authority file")
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()

    score = json.loads(args.score_file.read_text(encoding="utf-8"))
    if not SCHEMA.exists():
        raise SystemExit(f"missing schema {SCHEMA}")

    try:
        import jsonschema
    except ImportError:
        print("WARN: jsonschema not installed; schema validation skipped")
    else:
        try:
            jsonschema.validate(score, json.loads(SCHEMA.read_text(encoding="utf-8")))
        except jsonschema.ValidationError as exc:
            raise SystemExit(f"score schema invalid: {exc.message}") from exc

    slots = [item.get("required_h_slot") for item in score.get("case_results") or []]
    missing = [slot for slot in REQUIRED_H_SLOTS if slot not in slots]
    if missing:
        raise SystemExit(f"score file is missing required holdout slots: {missing}")

    trust = "DEVELOPMENT_EVAL"
    signature_ok = False
    fingerprint_ok = False
    if args.development:
        trust = "DEVELOPMENT_EVAL"
    elif args.authority is None:
        raise SystemExit("formal import requires --authority <read-only-file>")
    else:
        root_pem = read_trust_root()
        root_fingerprint = fingerprint_from_pem(root_pem)
        authority = json.loads(args.authority.read_text(encoding="utf-8"))
        authority_pem = authority.get("public_key_pem")
        authority_fingerprint = authority.get("public_key_fingerprint")
        if not authority_pem or not authority_fingerprint:
            raise SystemExit("authority is missing public_key_pem/public_key_fingerprint")
        fingerprint_ok = (
            authority_fingerprint == root_fingerprint
            and fingerprint_from_pem(authority_pem) == root_fingerprint
        )
        signature_ok = verify_signature(score, authority_pem) if fingerprint_ok else False
        score_fingerprint = (score.get("signature") or {}).get("public_key_fingerprint")
        if score_fingerprint and score_fingerprint != root_fingerprint:
            signature_ok = False
        if signature_ok and fingerprint_ok:
            trust = "VERIFIED"
        elif not fingerprint_ok:
            trust = "INVALID_AUTHORITY_FINGERPRINT"
        else:
            trust = "INVALID_SIGNATURE"

    result = {
        "source_file": str(args.score_file),
        "authority_file": str(args.authority) if args.authority else None,
        "trust_level": trust,
        "signature_verified": signature_ok,
        "fingerprint_matched": fingerprint_ok,
        "verdict": score.get("verdict"),
        "case_count": len(score.get("case_results") or []),
        "note": "Independent Holdout acceptance is fixed outside this repository.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if trust == "VERIFIED":
        return 0
    if args.development and trust == "DEVELOPMENT_EVAL":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

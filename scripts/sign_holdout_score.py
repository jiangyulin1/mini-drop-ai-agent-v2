"""Sign an externally produced Holdout score with Ed25519.

This script is intended to run on the external evaluator host, not inside the
candidate repository.  It canonicalizes the score without the signature field
and emits a signature block.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.import_agent_beta_score import canonical_json  # noqa: E402

from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
    load_pem_private_key,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("score_file", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    score = json.loads(args.score_file.read_text(encoding="utf-8"))
    payload = score.copy()
    payload.pop("signature", None)
    message = canonical_json(payload).encode("utf-8")
    key = load_pem_private_key(args.private_key.read_bytes(), password=None)
    signature = key.sign(message)
    public_key = key.public_key()
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    score["signature"] = {
        "algorithm": "Ed25519",
        "public_key_fingerprint": fingerprint,
        "value": signature.hex(),
    }
    args.out.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"signed_score={args.out}")
    print(f"public_key_fingerprint={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

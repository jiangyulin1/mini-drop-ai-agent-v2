"""Generate an external Holdout evaluator Ed25519 key pair.

Run OUTSIDE the candidate repository, ideally in a directory the construction
agent cannot read.  The private key never enters the repository or reports.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    private_path = args.out_dir / "holdout-evaluator-private.pem"
    public_path = args.out_dir / "holdout-evaluator-public.pem"
    if private_path.exists() or public_path.exists():
        raise SystemExit("key files already exist; refusing to overwrite")

    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    ))
    private_path.chmod(0o600)
    public_path.write_bytes(key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ))
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    print(f"private_key={private_path}")
    print(f"public_key={public_path}")
    print(f"key_fingerprint={fingerprint}")
    print("Send ONLY the public key and fingerprint to the candidate side.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

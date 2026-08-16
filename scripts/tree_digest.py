"""Compute deterministic SHA-256 digests for files and directory trees.

Used by the external evaluator host (Windows or any OS) to fill the digest
fields of the holdout score v1.  Pure stdlib, no third-party dependencies.

File   -> sha256 of raw bytes.
Tree   -> sha256 of the sorted "hash  relpath" lines (relpath uses '/'),
          i.e. the tree digest changes only when file content or structure
          changes, never due to filesystem path style or timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> tuple[str, list[str]]:
    lines: list[str] = []
    for entry in sorted(root.rglob("*")):
        if entry.is_file():
            rel = entry.relative_to(root).as_posix()
            lines.append(f"{file_sha256(entry)}  {rel}")
    payload = "\n".join(lines) + ("\n" if lines else "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SHA-256 for a file or a whole directory tree.")
    parser.add_argument("target", help="file or directory")
    parser.add_argument("--manifest", default=None,
                        help="optionally write per-file hash lines here")
    args = parser.parse_args()

    target = Path(args.target)
    if target.is_file():
        digest = file_sha256(target)
        print(f"file_sha256={digest}")
        if args.manifest:
            Path(args.manifest).write_text(
                f"{digest}  {target.as_posix()}\n", encoding="utf-8")
    elif target.is_dir():
        digest, lines = tree_digest(target)
        print(f"tree_sha256={digest}")
        print(f"files={len(lines)}")
        if args.manifest:
            Path(args.manifest).write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    else:
        print(f"not found: {target}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

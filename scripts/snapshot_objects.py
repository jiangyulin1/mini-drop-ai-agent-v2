"""Create or restore a checksum-verified MinIO bucket snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from server.app.storage import _client


def _download(client, bucket: str, object_key: str, destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    response = client.get_object(bucket, object_key)
    try:
        with destination.open("wb") as handle:
            for chunk in response.stream(1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    finally:
        response.close()
        response.release_conn()
    return size, digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(bucket: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    objects_dir = output / "objects"
    objects_dir.mkdir()
    client = _client()
    records = []
    for index, item in enumerate(client.list_objects(bucket, recursive=True)):
        filename = f"{index:08d}.bin"
        size, sha256 = _download(client, bucket, item.object_name, objects_dir / filename)
        records.append({
            "object_key": item.object_name,
            "file": f"objects/{filename}",
            "size_bytes": size,
            "sha256": sha256,
            "content_type": getattr(item, "content_type", None) or "application/octet-stream",
        })
    manifest = {"version": 1, "source_bucket": bucket, "objects": records}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def restore(snapshot: Path, bucket: str) -> dict:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    client = _client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    restored = 0
    for record in manifest["objects"]:
        source = (snapshot / record["file"]).resolve()
        if snapshot.resolve() not in source.parents:
            raise ValueError(f"snapshot path escapes root: {record['file']}")
        digest = _file_sha256(source)
        if digest != record["sha256"]:
            raise ValueError(f"snapshot checksum mismatch: {record['object_key']}")
        client.fput_object(
            bucket,
            record["object_key"],
            str(source),
            content_type=record.get("content_type") or "application/octet-stream",
        )
        restored += 1
    return {"bucket": bucket, "restored": restored}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--bucket", default=os.getenv("MINIO_BUCKET", "mini-drop"))
    backup_parser.add_argument("--output", required=True, type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--bucket", required=True)
    restore_parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    result = (
        backup(args.bucket, args.output)
        if args.command == "backup"
        else restore(args.input, args.bucket)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reconcile artifact metadata in SQL with objects stored in MinIO."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from typing import Any

from minio.error import S3Error
from sqlalchemy import create_engine, text

from server.app.storage import _client


def _object_sha256(client: Any, bucket: str, object_key: str) -> str:
    digest = hashlib.sha256()
    response = client.get_object(bucket, object_key)
    try:
        for chunk in response.stream(1024 * 1024):
            digest.update(chunk)
    finally:
        response.close()
        response.release_conn()
    return digest.hexdigest()


def reconcile(*, verify_sha256: bool = False, include_orphans: bool = True) -> dict:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, bucket, object_key, size_bytes, sha256 FROM artifacts ORDER BY id"
        )).mappings().all()

    client = _client()
    expected: dict[str, set[str]] = defaultdict(set)
    missing: list[dict] = []
    mismatched: list[dict] = []
    checked = 0

    for row in rows:
        bucket = row["bucket"] or os.getenv("MINIO_BUCKET", "mini-drop")
        object_key = row["object_key"]
        expected[bucket].add(object_key)
        try:
            stat = client.stat_object(bucket, object_key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                missing.append({"artifact_id": row["id"], "bucket": bucket, "object_key": object_key})
                continue
            raise

        checked += 1
        expected_size = row["size_bytes"] or 0
        if expected_size and stat.size != expected_size:
            mismatched.append({
                "artifact_id": row["id"],
                "field": "size_bytes",
                "expected": expected_size,
                "actual": stat.size,
            })
        expected_sha = row["sha256"]
        if verify_sha256 and expected_sha:
            actual_sha = _object_sha256(client, bucket, object_key)
            if actual_sha.lower() != expected_sha.lower():
                mismatched.append({
                    "artifact_id": row["id"],
                    "field": "sha256",
                    "expected": expected_sha,
                    "actual": actual_sha,
                })

    orphans: list[dict] = []
    if include_orphans:
        buckets = set(expected) or {os.getenv("MINIO_BUCKET", "mini-drop")}
        for bucket in sorted(buckets):
            try:
                objects = client.list_objects(bucket, recursive=True)
                for item in objects:
                    if item.object_name not in expected[bucket]:
                        orphans.append({"bucket": bucket, "object_key": item.object_name})
            except S3Error as exc:
                if exc.code != "NoSuchBucket":
                    raise

    return {
        "database_artifacts": len(rows),
        "objects_checked": checked,
        "missing": missing,
        "mismatched": mismatched,
        "orphans": orphans,
        "ok": not missing and not mismatched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-sha256", action="store_true")
    parser.add_argument("--fail-on-orphans", action="store_true")
    parser.add_argument("--no-orphans", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    report = reconcile(
        verify_sha256=args.verify_sha256,
        include_orphans=not args.no_orphans,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    failed = not report["ok"] or (args.fail_on_orphans and report["orphans"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cross-platform and content-addressed Candidate package gates."""

from __future__ import annotations

import ast
import hashlib

from scripts.package_candidate import (
    ROOT,
    UNTRACKED_ALLOWLIST,
    UNTRACKED_SOURCE_DIRS,
    collect_payload_files,
    deterministic_tar_gz,
    normalize_payload_text,
    package_version,
    pi_version,
    tree_digest,
)
from scripts.deploy_candidate_vm import candidate_identity, parse_migration_head


def test_windows_utf8_and_line_endings_are_reproducible(tmp_path):
    windows = tmp_path / "windows"
    linux = tmp_path / "linux"
    windows.mkdir()
    linux.mkdir()
    (windows / "contract.py").write_bytes("print('你好')\r\n".encode("utf-8"))
    (linux / "contract.py").write_bytes("print('你好')\n".encode("utf-8"))
    (windows / "launch.cmd").write_bytes(b"@echo off\r\necho ok\r\n")
    (linux / "launch.cmd").write_bytes(b"@echo off\necho ok\n")

    normalize_payload_text(windows)
    normalize_payload_text(linux)
    assert tree_digest(collect_payload_files(windows)) == tree_digest(collect_payload_files(linux))


def test_candidate_hash_and_archive_are_platform_stable(tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "entry.py").write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
    rows = collect_payload_files(payload)
    digest = tree_digest(rows)
    assert digest == tree_digest(collect_payload_files(payload))

    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    paths = [item["relative_path"] for item in rows]
    deterministic_tar_gz(first, payload, paths, source_date_epoch=1_700_000_000)
    deterministic_tar_gz(second, payload, paths, source_date_epoch=1_700_000_000)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_candidate_tracks_all_lock_and_generated_web_inputs():
    assert {"uv.lock", "requirements.lock", ".gitattributes"} <= set(UNTRACKED_ALLOWLIST)
    assert "web/e2e/" in UNTRACKED_SOURCE_DIRS
    assert "web/playwright.config.js" in UNTRACKED_ALLOWLIST
    assert package_version(ROOT) == "0.1.0"
    assert pi_version(ROOT) == "0.84.2"


def test_runtime_docker_images_include_shared_collector_contracts():
    for name in ("server.Dockerfile", "agent.Dockerfile"):
        dockerfile = (ROOT / "deploy" / "dockerfiles" / name).read_text(encoding="utf-8")
        assert "COPY mini_drop_contracts/ ./mini_drop_contracts/" in dockerfile


def test_migration_revision_ids_fit_legacy_postgresql_version_column():
    revisions = ROOT / "migrations" / "versions"
    for path in revisions.glob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        revision = next(
            (
                node.value.value
                for node in module.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "revision"
                and isinstance(node.value, ast.Constant)
            ),
            None,
        )
        if revision is not None:
            assert len(revision) <= 32, f"{path.name}: revision ID is too long"


def test_jyl_security_release_contract_exposes_control_without_weakening_internal_auth():
    control = (ROOT / "deploy/compose/jyl-secure.control.yml").read_text(encoding="utf-8")
    worker = (ROOT / "deploy/compose/jyl-secure.worker.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy/nginx/jyl-secure.conf").read_text(encoding="utf-8")

    assert "MINI_DROP_API_AUTH_ENABLED: ${MINI_DROP_API_AUTH_ENABLED:-1}" in control
    assert "MINI_DROP_API_KEY: ${MINI_DROP_API_KEY:?set MINI_DROP_API_KEY}" in control
    assert 'MINI_DROP_GRPC_AUTH_ENABLED: "1"' in control
    assert 'MINI_DROP_GRPC_SECURE: "1"' in control
    assert "MINI_DROP_GRPC_DISTRIBUTE_MINIO_CREDENTIALS: \"0\"" in control
    assert 'AGENT_GRPC_SECURE: "1"' in worker
    assert "AGENT_GRPC_CA_CERT: /certs/ca.crt" in worker
    assert 'MINIO_SECURE: "1"' in worker
    assert "MINIO_CA_CERT: /certs/ca.crt" in worker
    assert "listen 8443 ssl;" in nginx
    assert "listen 9443 ssl;" in nginx
    assert "return 308 https://$host:8443$request_uri;" in nginx
    assert "X-API-Key" not in nginx
    assert '"0.0.0.0:${CONTROL_HTTPS_PORT:-80}:8443"' in control
    assert '"0.0.0.0:${CONTROL_MINIO_TLS_PORT:-9100}:9443"' in control
    assert control.count("context: ../..") == 4
    assert "- ../certs:/certs:ro" in control
    assert "context: ../.." in worker
    assert "- ../certs:/certs:ro" in worker
    generator = ROOT / "deploy/scripts/generate-jyl-security-material.sh"
    assert generator.exists()
    assert "refusing to overwrite existing security material" in generator.read_text(encoding="utf-8")


def test_deployment_receipt_identity_keeps_real_versions_and_all_digests():
    identity = candidate_identity({
        "release_id": "cand-abc",
        "payload_tree_digest": "payload",
        "lock_digest": "locks",
        "migration_head": "0025_evidence_contract",
        "actual_package_version": "0.1.0",
        "actual_pi_version": "0.84.2",
    })
    assert identity == {
        "release_id": "cand-abc",
        "payload_tree_digest": "payload",
        "lock_digest": "locks",
        "migration_head": "0025_evidence_contract",
        "package_version": "0.1.0",
        "pi_version": "0.84.2",
        "actual_package_version": "0.1.0",
        "actual_pi_version": "0.84.2",
    }


def test_deployer_normalizes_alembic_head_presentation_without_hiding_branches():
    assert parse_migration_head("0025_evidence_contract (head)\n") == "0025_evidence_contract"

    try:
        parse_migration_head("0025_evidence_contract (head)\nother (head)\n")
    except RuntimeError as exc:
        assert "exactly one migration head" in str(exc)
    else:
        raise AssertionError("multiple migration heads must fail deployment")

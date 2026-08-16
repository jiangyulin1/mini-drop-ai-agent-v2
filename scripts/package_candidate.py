#!/usr/bin/env python3
"""Build an immutable v6 Candidate from the CURRENT working tree.

The release ID is derived from every payload byte.  Untracked source files are
hashed by content, not by path only, and web dist is hashed as a tree.
A package receipt is emitted outside the archive so no self-hash loop exists.

Exit codes: 0 built/verified; 1 invalid candidate; 2 worktree changed mid-build.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

UNTRACKED_SOURCE_DIRS = (
    "server/",
    "agent/",
    "analyzer/",
    "scripts/",
    "tests/",
    "migrations/",
    "proto/",
    "deploy/",
    "demo/",
    "web/src/",
    "web/public/",
    "web/e2e/",
    "docs/",
    "benchmarks/agent_beta/",
    "agent_runtime/",
    "mini_drop_observability/",
    "knowledge/",
    "golden_scenarios/",
)
UNTRACKED_ALLOWLIST = (
    ".gitattributes",
    "requirements.lock",
    "uv.lock",
    "web/playwright.config.js",
    "benchmarks/ai_ops_v2/public/cases.json",
    "benchmarks/ai_ops_v2/private/oracles.json",
    "benchmarks/lightweight_ai_eval/manifest.json",
    "benchmarks/environments/hyperv_online_boutique_verified_vm.json",
    "benchmarks/environments/hyperv_three_node.json",
)

BLOCKLIST_RE = re.compile(
    r"(^|/)(\.env|\.env\..*|.*\.pem$|.*\.key$|.*\.crt$|node_modules/|"
    r"reports/|testsets/|\.pytest-|\.git/|\.venv/|venv/|deploy/ssh/|"
    r"ssh/|external-package/|web/test-results/|agent_runtime/pi-sidecar/data/|"
    r".*run-records\.jsonl$|.*bundles/|private/)",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"(MINI_DROP_API_KEY|MINI_DROP_GRPC_TOKEN|AI_API_KEY|API_KEY|PASSWORD)[ \t]*[:=][ \t]*"
    r"([\"']?[A-Za-z0-9_./+\-]{8,}[\"']?)",
    re.IGNORECASE,
)
PRIVATE_KEY_RE = re.compile(r"BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY")
LF_TEXT_SUFFIXES = {
    ".css", ".html", ".js", ".jsx", ".json", ".lock", ".md", ".mjs",
    ".proto", ".ps1", ".py", ".pyi", ".sh", ".toml", ".txt", ".yaml", ".yml",
}
CRLF_TEXT_SUFFIXES = {".bat", ".cmd"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_file_mode(path: Path) -> str:
    if path.is_symlink():
        return "0o777"
    try:
        shebang = path.read_bytes()[:2] == b"#!"
    except OSError:
        shebang = False
    return "0o755" if path.suffix.lower() == ".sh" or shebang else "0o644"


def normalize_payload_text(root: Path) -> None:
    """Apply the repository line-ending contract before content addressing."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        suffix = path.suffix.lower()
        if suffix not in LF_TEXT_SUFFIXES | CRLF_TEXT_SUFFIXES and path.name != "Makefile":
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        lf = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        path.write_bytes(lf.replace(b"\n", b"\r\n") if suffix in CRLF_TEXT_SUFFIXES else lf)


def canonical_json(value: Any) -> str:
    """RFC 8785 style JCS used for candidate digests."""
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(str(k), ensure_ascii=False, separators=(",", ":"))
            + ":" + canonical_json(value[k])
            for k in sorted(value.keys())
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def git(*args: str, cwd: Path = ROOT) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=True,
    )
    return proc.stdout


def collect_payload_files(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not BLOCKLIST_RE.search(d)]
        current = Path(dirpath)
        for name in sorted(filenames + dirnames):
            path = current / name
            rel = path.relative_to(root).as_posix()
            if BLOCKLIST_RE.search(rel):
                continue
            if rel in {"candidate-manifest.json", "package-receipt.json", "_snapshot.tar"}:
                continue
            if path.is_symlink():
                rows.append({
                    "relative_path": rel,
                    "file_type": "symlink",
                    "mode": "0o777",
                    "size": 0,
                    "sha256": "",
                    "link_target": os.readlink(path),
                })
            elif path.is_file():
                data = path.read_bytes()
                rows.append({
                    "relative_path": rel,
                    "file_type": "file",
                    "mode": canonical_file_mode(path),
                    "size": len(data),
                    "sha256": sha256_bytes(data),
                    "link_target": None,
                })
    return sorted(rows, key=lambda item: item["relative_path"])


def is_placeholder(value: str) -> bool:
    value = value.strip().strip("\"'")
    upper = value.upper()
    if value.startswith("${") or value.startswith("$") or value.startswith("os."):
        return True
    if "getenv" in value or "environ" in value or "read_" in value:
        return True
    if "CHANGE_ME" in upper or "EXAMPLE" in upper or "YOUR_" in upper or "XXXXX" in upper:
        return True
    if upper in {"PASSWORD", "SECRET", "TOKEN", "KEY", "API_KEY", "MINI_DROP_API_KEY"}:
        return True
    return False


def is_literal_high_entropy(value: str) -> bool:
    value = value.strip().strip("\"'")
    if not value or any(ch in value for ch in "().\t ") or value.startswith((":", "{")):
        return False
    if len(value) < 12:
        return False
    has_upper = any(ch.isupper() for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    if not (has_digit or has_upper):
        return False
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return entropy >= 3.5


def scan_for_secrets(root: Path) -> list[str]:
    findings: list[str] = []
    for row in collect_payload_files(root):
        if row.get("file_type") != "file":
            continue
        path = root / row["relative_path"]
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:2048]:
            continue
        content = data.decode("utf-8", "ignore")
        for match in SECRET_RE.finditer(content):
            if not is_placeholder(match.group(2)) and is_literal_high_entropy(match.group(2)):
                findings.append(f"{row['relative_path']}: {match.group(0)[:60]!r}")
        if PRIVATE_KEY_RE.search(content):
            findings.append(f"{row['relative_path']}: contains private key material")
    return findings


def tree_digest(files: list[dict[str, Any]]) -> str:
    digest_rows = [
        {
            "relative_path": row["relative_path"],
            "file_type": row["file_type"],
            "mode": row["mode"],
            "size": row["size"],
            "sha256": row["sha256"],
            "link_target": row["link_target"],
        }
        for row in files
    ]
    return canonical_digest(digest_rows)


def migration_head(staging: Path) -> str:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        config = Config(str(staging / "alembic.ini"))
        config.set_main_option("script_location", str(staging / "migrations"))
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        if len(heads) == 1:
            return heads[0]
        return ",".join(sorted(heads))
    except Exception as exc:
        return f"unavailable:{exc.__class__.__name__}"


def package_version(staging: Path) -> str:
    content = (staging / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    return match.group(1) if match else "unknown"


def pi_version(staging: Path) -> str:
    package = json.loads(
        (staging / "agent_runtime/pi-sidecar/package-lock.json").read_text(encoding="utf-8")
    )
    root = package.get("packages", {}).get("", {})
    return str(root.get("dependencies", {}).get("@earendil-works/pi-coding-agent") or "unknown")


def deterministic_tar_gz(
    archive: Path,
    staging: Path,
    relative_paths: list[str],
    *,
    source_date_epoch: int,
) -> None:
    """Write identical gzip/tar bytes for identical normalized payload bytes."""
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=source_date_epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tf:
                for rel in sorted(relative_paths):
                    path = staging / rel
                    if not (path.is_file() or path.is_symlink()):
                        continue
                    info = tf.gettarinfo(str(path), arcname=rel)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = source_date_epoch
                    info.mode = int(canonical_file_mode(path), 8)
                    if path.is_symlink():
                        tf.addfile(info)
                    else:
                        with path.open("rb") as source:
                            tf.addfile(info, source)


def verify_archive(archive: Path, manifest: dict[str, Any], python: str) -> list[str]:
    import tempfile
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with tarfile.open(archive) as tf:
            tf.extractall(root)
        embedded_manifest_path = root / "candidate-manifest.json"
        if not embedded_manifest_path.is_file():
            errors.append("candidate-manifest.json is missing")
        else:
            try:
                embedded_manifest = json.loads(embedded_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"candidate-manifest.json is invalid: {exc}")
            else:
                if embedded_manifest != manifest:
                    errors.append("embedded candidate manifest differs from published manifest")
                calculated_manifest_digest = canonical_digest({
                    key: value for key, value in embedded_manifest.items()
                    if key != "manifest_digest"
                })
                if embedded_manifest.get("manifest_digest") != calculated_manifest_digest:
                    errors.append("candidate manifest digest mismatch")
        listed = {item["relative_path"] for item in manifest["payload_files"]}
        listed.add("candidate-manifest.json")
        extra = sorted(
            p.relative_to(root).as_posix() for p in root.rglob("*")
            if (p.is_file() or p.is_symlink())
            and p.relative_to(root).as_posix() not in listed
            and "__pycache__" not in p.parts
        )
        if extra:
            errors.append("extra files not listed in manifest: " + ",".join(extra[:10]))
        for item in manifest["payload_files"]:
            path = root / item["relative_path"]
            if item["file_type"] == "symlink":
                if not path.is_symlink() or os.readlink(path) != item["link_target"]:
                    errors.append(f"symlink mismatch: {item['relative_path']}")
                continue
            if not path.is_file():
                errors.append(f"payload file missing: {item['relative_path']}")
                continue
            data = path.read_bytes()
            if len(data) != item["size"] or sha256_bytes(data) != item["sha256"]:
                errors.append(f"payload digest mismatch: {item['relative_path']}")
        if tree_digest(manifest["payload_files"]) != manifest.get("payload_tree_digest"):
            errors.append("payload tree digest mismatch")
        try:
            subprocess.run(
                [python, "scripts/compile_proto.py"], cwd=str(root),
                capture_output=True, text=True, timeout=180, check=True,
            )
        except Exception as exc:
            errors.append(f"compile_proto failed: {exc}")
        try:
            subprocess.run(
                [python, "-c", "import server.app.main"], cwd=str(root),
                capture_output=True, text=True, timeout=120, check=True,
            )
        except Exception as exc:
            errors.append(f"import server.app.main failed: {exc}")
        try:
            subprocess.run(
                [python, "scripts/check_migrations.py"], cwd=str(root),
                capture_output=True, text=True, timeout=180, check=True,
            )
        except Exception as exc:
            errors.append(f"migration graph check failed: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "reports" / "implementation" / "candidates")
    parser.add_argument("--build-web", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    python_path = Path(args.python)
    if python_path.exists():
        args.python = str(python_path.resolve())

    head = git("rev-parse", "HEAD").strip()
    diff = git("diff", "HEAD").encode("utf-8")
    untracked = [line for line in git("ls-files", "--others", "--exclude-standard").splitlines() if line]
    allowed_untracked = sorted(
        p for p in untracked
        if (ROOT / p).is_file()
        and (p in UNTRACKED_ALLOWLIST or any(p.startswith(prefix) for prefix in UNTRACKED_SOURCE_DIRS))
    )
    compiled_proto = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "server/app/generated").glob("*.py")
        if p.name != "__init__.py"
    )
    allowed_untracked = sorted(set(allowed_untracked) | set(compiled_proto))
    changed_tracked = [line for line in git("diff", "--name-only", "HEAD").splitlines() if line]
    before_files = {
        "head": head,
        "tracked_diff_sha256": sha256_bytes(diff),
        "included_untracked_files": [
            {"path": p, "sha256": sha256_file(ROOT / p)} for p in allowed_untracked
        ],
    }

    if args.build_web and not args.skip_build:
        npm_bin = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run([npm_bin, "--prefix", "web", "run", "build"], cwd=str(ROOT), check=True)

    after_diff = git("diff", "HEAD").encode("utf-8")
    if not sha256_bytes(diff) == sha256_bytes(after_diff):
        print("worktree changed during build; candidate rejected", file=sys.stderr)
        return 2
    after_files = [
        {"path": p, "sha256": sha256_file(ROOT / p)} for p in allowed_untracked
    ]
    if not before_files["included_untracked_files"] == after_files:
        print("worktree file changed during build; candidate rejected", file=sys.stderr)
        return 2

    staging = args.output_dir / "staging" / f"tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(["git", "archive", head], cwd=str(ROOT), capture_output=True, check=True)
    with (staging / "_snapshot.tar").open("wb") as handle:
        handle.write(proc.stdout)
    with tarfile.open(staging / "_snapshot.tar") as tf:
        tf.extractall(staging)
    (staging / "_snapshot.tar").unlink()

    for rel in changed_tracked:
        src = ROOT / rel
        dst = staging / rel
        if src.is_file() or src.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                dst.unlink(missing_ok=True)
                dst.symlink_to(os.readlink(src))
            else:
                shutil.copy2(src, dst)
        else:
            dst.unlink(missing_ok=True)

    for rel in allowed_untracked:
        src = ROOT / rel
        dst = staging / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.unlink(missing_ok=True)
            dst.symlink_to(os.readlink(src))
        else:
            shutil.copy2(src, dst)

    built_dist = ROOT / "web" / "dist"
    if built_dist.is_dir():
        staged_dist = staging / "web" / "dist"
        shutil.rmtree(staged_dist, ignore_errors=True)
        shutil.copytree(built_dist, staged_dist)

    normalize_payload_text(staging)

    findings = scan_for_secrets(staging)
    if findings:
        print("secret/private scan found violations; candidate rejected:", file=sys.stderr)
        for item in findings[:40]:
            print(f"  - {item}", file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    payload_files = collect_payload_files(staging)
    payload_tree_digest = tree_digest(payload_files)
    release_id = f"cand-{payload_tree_digest[:16]}"
    web_dist_tree_digest = None
    if (staging / "web" / "dist" / "index.html").is_file():
        web_files = [
            row for row in payload_files if row["relative_path"].startswith("web/dist/")
        ]
        web_dist_tree_digest = tree_digest(web_files)

    sidecar_lock = staging / "agent_runtime" / "pi-sidecar" / "package-lock.json"
    web_lock = staging / "web" / "package-lock.json"
    uv_lock = staging / "uv.lock"
    requirements_export = staging / "requirements.lock"
    migration_plan = staging / "migrations" / "migration-plan.json"
    lock_components = {
        "python_uv": sha256_file(uv_lock) if uv_lock.is_file() else None,
        "python_requirements_export": sha256_file(requirements_export) if requirements_export.is_file() else None,
        "sidecar_npm": sha256_file(sidecar_lock) if sidecar_lock.is_file() else None,
        "web_npm": sha256_file(web_lock) if web_lock.is_file() else None,
    }
    source_date_epoch = int(os.getenv("SOURCE_DATE_EPOCH") or git("show", "-s", "--format=%ct", head).strip())
    manifest: dict[str, Any] = {
        "schema_version": "candidate-manifest-v2",
        "release_id": release_id,
        "base_commit": head,
        "payload_files": payload_files,
        "payload_tree_digest": payload_tree_digest,
        "tracked_diff_sha256": sha256_bytes(diff),
        "included_untracked_files": [
            {"path": p, "size": (ROOT / p).stat().st_size if (ROOT / p).exists() else 0,
             "sha256": sha256_file(ROOT / p)} for p in allowed_untracked
        ],
        "web_dist_tree_sha256": web_dist_tree_digest,
        "lock_components": lock_components,
        "lock_digest": canonical_digest(lock_components),
        "python_lock_sha256": lock_components["python_uv"],
        "requirements_export_sha256": lock_components["python_requirements_export"],
        "sidecar_package_lock_sha256": lock_components["sidecar_npm"],
        "web_package_lock_sha256": lock_components["web_npm"],
        "actual_package_version": package_version(staging),
        "actual_pi_version": pi_version(staging),
        "migration_head": migration_head(staging),
        "migration_plan_digest": sha256_file(migration_plan) if migration_plan.is_file() else None,
        "prompt_digest": sha256_file(ROOT / "docs/ai_agent_feature_complete_demo_prompt_v6.md"),
        "public_contract_digest": sha256_file(ROOT / "benchmarks/agent_beta/contracts/public-contract-v1.json"),
        "source_date_epoch": source_date_epoch,
    }
    manifest_digest = canonical_digest({k: v for k, v in manifest.items() if k != "manifest_digest"})
    manifest["manifest_digest"] = manifest_digest

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = staging / "candidate-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    archive_path = args.output_dir / f"{release_id}.tar.gz"
    existing_receipt_path = args.output_dir / f"{release_id}.receipt.json"
    published_manifest_path = args.output_dir / f"{release_id}.manifest.json"
    if archive_path.exists() or published_manifest_path.exists():
        previous_digest = None
        if existing_receipt_path.exists():
            previous_digest = json.loads(existing_receipt_path.read_text(encoding="utf-8")).get("archive_sha256")
        if previous_digest and sha256_file(archive_path) == previous_digest:
            verify_errors = verify_archive(archive_path, manifest, args.python) if args.verify else []
            shutil.rmtree(staging, ignore_errors=True)
            result = {
                "release_id": release_id,
                "reused": True,
                "archive_sha256": previous_digest,
            }
            if args.verify:
                result["verify"] = {"ok": not verify_errors, "errors": verify_errors}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if verify_errors else 0
        shutil.rmtree(staging, ignore_errors=True)
        print("existing release ID exists without identical verified bytes; refusing to overwrite", file=sys.stderr)
        return 1

    payload_paths = {row["relative_path"] for row in payload_files}
    temporary_archive_path = args.output_dir / f".{release_id}.tmp-{os.getpid()}.tar.gz"
    deterministic_tar_gz(
        temporary_archive_path,
        staging,
        sorted(payload_paths | {"candidate-manifest.json"}),
        source_date_epoch=source_date_epoch,
    )
    archive_sha256 = sha256_file(temporary_archive_path)

    verify_errors = verify_archive(temporary_archive_path, manifest, args.python) if args.verify else []
    if verify_errors:
        temporary_archive_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        print(json.dumps({
            "release_id": release_id,
            "archive_sha256": archive_sha256,
            "verify": {"ok": False, "errors": verify_errors},
        }, ensure_ascii=False, indent=2))
        return 1

    os.replace(temporary_archive_path, archive_path)

    receipt = {
        "schema_version": "package-receipt-v1",
        "release_id": release_id,
        "payload_tree_digest": payload_tree_digest,
        "manifest_digest": manifest_digest,
        "archive_sha256": archive_sha256,
    }
    existing_receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    published_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.rmtree(staging, ignore_errors=True)

    summary = {
        "release_id": release_id,
        "archive": str(archive_path),
        "archive_sha256": archive_sha256,
        "payload_tree_digest": payload_tree_digest,
        "manifest_digest": manifest_digest,
        "base_commit": head,
        "tracked_files_overlaid": len(changed_tracked),
        "untracked_files_included": len(allowed_untracked),
        "payload_files": len(payload_files),
        "web_dist_tree_sha256": web_dist_tree_digest,
        "migration_head": manifest["migration_head"],
        "source_date_epoch": manifest["source_date_epoch"],
    }
    if args.verify:
        summary["verify"] = {"ok": True, "errors": []}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

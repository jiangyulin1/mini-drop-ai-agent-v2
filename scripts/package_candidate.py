#!/usr/bin/env python3
"""Build an immutable deploy candidate from the CURRENT working tree.

E0 gate (plan section 19.3): the release object must be "the same code that just
passed the local gate", even when the working tree has uncommitted changes.  This
script:

  1. snapshots committed HEAD with `git archive`;
  2. overlays every tracked-file change from the working tree;
  3. copies only allowlisted untracked source files;
  4. builds the web distribution;
  5. scans the bundle for credentials / private topology / reports / test data;
  6. emits an immutable Release Manifest with content hashes.

It never runs `git commit`, never rsyncs the whole workspace and never writes
.env, certificates, API keys or private eval data into the bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Untracked source files that must travel with a candidate.  Files are taken
# from tracked source directories (strict, not a workspace rsync); anything
# generated, private or environment-specific is deliberately excluded.
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
    "docs/",
)
UNTRACKED_ALLOWLIST = (
    "benchmarks/ai_ops_v2/public/cases.json",
    "benchmarks/ai_ops_v2/private/oracles.json",  # scoring-only; never reaches model context
    "benchmarks/lightweight_ai_eval/manifest.json",
    "benchmarks/environments/hyperv_online_boutique_verified_vm.json",
    "benchmarks/environments/hyperv_three_node.json",
)

# Paths that must never enter a candidate bundle.
BLOCKLIST_RE = re.compile(
    r"(^|/)(\.env|\.env\..*|.*\.pem$|.*\.key$|.*\.crt$|node_modules/|"
    r"reports/|testsets/|\.pytest-|\.git/|\.venv/|venv/|deploy/ssh/|"
    r"ai_agent_runtime_state|.*run-records\.jsonl$|.*bundles/|private/)",
    re.IGNORECASE,
)
# Real secrets are literal values with entropy.  Placeholders (${VAR}),
# env reads (os.getenv) and bare identifiers (PASSWORD=SECRET) are code, not
# secrets, and must not block a candidate.
SECRET_RE = re.compile(
    r"(MINI_DROP_API_KEY|MINI_DROP_GRPC_TOKEN|AI_API_KEY|API_KEY|PASSWORD)[ \t]*[:=][ \t]*"
    r"([\"']?[A-Za-z0-9_./+\-]{8,}[\"']?)",
    re.IGNORECASE,
)
PRIVATE_KEY_RE = re.compile(r"BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY")


def _is_placeholder(value: str) -> bool:
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


def _is_literal_high_entropy(value: str) -> bool:
    """A real secret is a single literal token with meaningful entropy.

    Code expressions (`shlex.quote`, `args.api_key`, `request.add_header`) and
    tests using low-entropy word values ("should-not-appear") are not release
    secrets.  A genuine key mixes character classes beyond lowercase letters.
    """
    value = value.strip().strip("\"'")
    if not value or any(ch in value for ch in "().\t ") or value.startswith((":", "{")):
        return False
    if len(value) < 12:
        return False
    has_upper = any(ch.isupper() for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    if not (has_digit or has_upper):
        return False  # 全小写词（含连字符）更可能是测试占位而非密钥
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    entropy = -sum((c / n) * __import__("math").log2(c / n) for c in counts.values())
    return entropy >= 3.5


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git(*args: str, cwd: Path = ROOT) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=True,
    )
    return proc.stdout


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if BLOCKLIST_RE.search(rel):
            continue
        files.append(path)
    return files


def scan_for_secrets(root: Path) -> list[str]:
    findings: list[str] = []
    for path in collect_files(root):
        try:
            text = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in text[:2048]:
            continue
        content = text.decode("utf-8", "ignore")
        for match in SECRET_RE.finditer(content):
            value = match.group(2)
            if not _is_placeholder(value) and _is_literal_high_entropy(value):
                findings.append(f"{path.relative_to(root)}: {match.group(0)[:60]!r}")
        if PRIVATE_KEY_RE.search(content):
            findings.append(f"{path.relative_to(root)}: contains private key material")
    return findings


def verify_archive(archive: Path, python: str, sidecar: Path | None = None) -> list[str]:
    """Unpack a candidate archive into an empty dir and re-run import + migration."""
    import tempfile

    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp)
        root = Path(tmp)
        expected_hash = None
        if sidecar and sidecar.is_file():
            expected_hash = json.loads(sidecar.read_text(encoding="utf-8")).get("archive_sha256")
        if expected_hash and expected_hash != sha256_file(archive):
            errors.append("sidecar archive_sha256 does not match archive content")
        try:
            subprocess.run(
                [python, "scripts/compile_proto.py"], cwd=str(root),
                capture_output=True, text=True, timeout=180, check=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"compile_proto failed: {exc}")
        try:
            subprocess.run(
                [python, "-c", "import server.app.main"], cwd=str(root),
                capture_output=True, text=True, timeout=120, check=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"import server.app.main failed: {exc}")
        try:
            subprocess.run(
                [python, "scripts/check_migrations.py"], cwd=str(root),
                capture_output=True, text=True, timeout=120, check=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"migration graph check failed: {exc}")
        web_dist = root / "web" / "dist"
        embedded = root / "release-manifest.json"
        if embedded.is_file():
            embedded_manifest = json.loads(embedded.read_text(encoding="utf-8"))
            if embedded_manifest.get("web_dist_sha256") and not (web_dist / "index.html").is_file():
                errors.append("web/dist/index.html missing though manifest declares web_dist_sha256")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "reports" / "implementation" / "candidates")
    parser.add_argument("--build-web", action="store_true",
                        help="run npm build before bundling (requires node_modules)")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--verify", action="store_true",
                        help="unpack the produced archive and re-run import/migration checks")
    parser.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe")
                        if os.name == "nt" else "python")
    args = parser.parse_args()

    head = git("rev-parse", "HEAD").strip()
    diff = git("diff", "HEAD").encode("utf-8")
    untracked = [line for line in git("ls-files", "--others", "--exclude-standard").splitlines() if line]
    allowed_untracked = sorted(
        p for p in untracked
        if p in UNTRACKED_ALLOWLIST or any(p.startswith(prefix) for prefix in UNTRACKED_SOURCE_DIRS)
    )
    # 编译的 protobuf 桩被 server/app/generated/.gitignore 排除在 untracked 之外，
    # 但部署运行需要它们。强制纳入（__init__.py 已受版本控制，走 tracked diff）。
    compiled_proto = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "server/app/generated").glob("*.py")
        if p.name != "__init__.py"
    )
    allowed_untracked = sorted(set(allowed_untracked) | set(compiled_proto))
    changed_tracked = [line for line in git("diff", "--name-only", "HEAD").splitlines() if line]

    fingerprint_input = {
        "head": head,
        "tracked_diff_sha256": sha256_bytes(diff),
        "included_untracked_files": allowed_untracked,
    }
    fingerprint = sha256_bytes(json.dumps(fingerprint_input, sort_keys=True).encode())
    release_id = f"cand-{head[:10]}-{fingerprint[:10]}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    staging = args.output_dir / "staging" / release_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    # 1. committed snapshot
    proc = subprocess.run(["git", "archive", head], cwd=str(ROOT), capture_output=True, check=True)
    with (staging / "_snapshot.tar").open("wb") as handle:
        handle.write(proc.stdout)
    with tarfile.open(staging / "_snapshot.tar") as tf:
        tf.extractall(staging)
    (staging / "_snapshot.tar").unlink()

    # 2. overlay tracked working-tree changes
    for rel in changed_tracked:
        src = ROOT / rel
        dst = staging / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            # deleted in working tree -> keep absent
            dst.unlink(missing_ok=True)

    # 3. allowlisted untracked source files
    for rel in allowed_untracked:
        src = ROOT / rel
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # 4. build web distribution if requested, then stage the dist
    if args.build_web and not args.skip_build:
        npm_bin = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run([npm_bin, "--prefix", "web", "run", "build"], cwd=str(ROOT), check=True)
    built_dist = ROOT / "web" / "dist"
    if built_dist.is_dir():
        staged_dist = staging / "web" / "dist"
        shutil.rmtree(staged_dist, ignore_errors=True)
        shutil.copytree(built_dist, staged_dist)

    # 4b. 规范化 shell 脚本换行为 LF（Windows 工作树 CRLF 会在 Linux 上
    #     破坏 set -o pipefail 等解析）。Makefile 同理。
    for script in staging.rglob("*.sh"):
        script.write_bytes(script.read_bytes().replace(b"\r\n", b"\n"))
    makefile = staging / "Makefile"
    if makefile.is_file():
        makefile.write_bytes(makefile.read_bytes().replace(b"\r\n", b"\n"))

    # 5. secret + hygiene scan
    findings = scan_for_secrets(staging)
    if findings:
        print("secret/private scan found violations; candidate rejected:", file=sys.stderr)
        for item in findings[:40]:
            print(f"  - {item}", file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    # 6. Release Manifest
    included_files = sorted(p.relative_to(staging).as_posix() for p in collect_files(staging))
    web_dist = staging / "web" / "dist"
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "release_id": release_id,
        "base_commit": head,
        "working_tree_fingerprint": fingerprint,
        "tracked_diff_sha256": fingerprint_input["tracked_diff_sha256"],
        "included_untracked_files": allowed_untracked,
        "web_dist_sha256": sha256_file(web_dist / "index.html") if (web_dist / "index.html").is_file() else None,
        "migration_heads": [p.name for p in sorted((staging / "migrations" / "versions").glob("*.py"))],
        "file_count": len(included_files),
        "files": included_files,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = staging / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. tarball with overall hash (recorded in a sidecar, since an archive
    #    cannot contain a hash of itself).
    bundle = args.output_dir / f"{release_id}.tar.gz"
    with tarfile.open(bundle, "w:gz") as tf:
        for path in collect_files(staging):
            tf.add(path, arcname=path.relative_to(staging).as_posix())
    manifest["archive_sha256"] = sha256_file(bundle)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    sidecar = args.output_dir / f"{release_id}.manifest.json"
    sidecar.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(staging, ignore_errors=True)

    summary = {
        "release_id": release_id,
        "archive": str(bundle.relative_to(args.output_dir)),
        "archive_sha256": manifest["archive_sha256"],
        "base_commit": head,
        "working_tree_fingerprint": fingerprint,
        "tracked_files_overlaid": len(changed_tracked),
        "untracked_files_included": allowed_untracked,
        "files": manifest["file_count"],
    }
    if args.verify:
        verify_errors = verify_archive(
            bundle, args.python,
            sidecar=args.output_dir / f"{release_id}.manifest.json",
        )
        summary["verify"] = {"ok": not verify_errors, "errors": verify_errors}
        if verify_errors:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

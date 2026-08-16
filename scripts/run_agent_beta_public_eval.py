#!/usr/bin/env python3
"""Run every mandatory Agent Beta public-v3 assertion without soft-pass states."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from package_candidate import canonical_digest, verify_archive

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "agent_beta" / "manifests" / "public-v3.json"
REPORT = ROOT / "reports" / "implementation" / "agent-beta-public-v3.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 900) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "output_tail": (proc.stdout + proc.stderr)[-4000:],
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, timeout: int = 45) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = "not attempted"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8")
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"http_status": response.status}
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise TimeoutError(f"{url} did not become available: {last_error}")


def terminate_tree(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def seed_browser_database(env: dict[str, str]) -> str:
    previous = {name: os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        from server.app.database import init_db, reset_engine
        from server.app.main import repo
        from server.app.schemas import CreateTaskRequest
        from server.app.state_machine import Actor, TaskStatus

        reset_engine()
        init_db()
        repo.register_agent(
            "agent-c6-public",
            "node-c6-public",
            "127.0.0.1",
            version="0.3.0",
            capabilities=["process_scan", "sys_metrics", "service:checkout-c6"],
        )
        task = repo.create_task(CreateTaskRequest(
            name="C6 data-driven task",
            agent_id="agent-c6-public",
            target_pid=1,
            collector_type="process_scan",
            sample_rate=10,
            duration_sec=10,
            options={"source": "agent-beta-public-v3"},
        ))
        repo.add_artifacts(task.id, [{
            "artifact_type": "process_scan",
            "metadata": {"processes": [{"pid": 1, "comm": "init"}]},
        }])
        repo.transition_task(task.id, TaskStatus.RUNNING, "public eval", Actor.AGENT)
        repo.transition_task(task.id, TaskStatus.UPLOADING, "public eval", Actor.AGENT)
        repo.transition_task(task.id, TaskStatus.ANALYZING, "public eval", Actor.WEB)
        repo.transition_task(task.id, TaskStatus.DONE, "public eval", Actor.AGENT)
        reset_engine()
        return task.id
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def start_backend(env: dict[str, str], port: int, log_handle) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def browser_real_backend() -> dict[str, Any]:
    started = time.perf_counter()
    backend: subprocess.Popen[Any] | None = None
    web: subprocess.Popen[Any] | None = None
    with tempfile.TemporaryDirectory(prefix="mini-drop-public-browser-") as temp:
        temp_root = Path(temp)
        api_port = free_port()
        web_port = free_port()
        grpc_port = free_port()
        database = (temp_root / "public-eval.sqlite").resolve().as_posix()
        env = os.environ.copy()
        env.update({
            "DATABASE_URL": f"sqlite:///{database}",
            "MINI_DROP_LOCAL_ARTIFACT_ROOT": str(temp_root / "artifacts"),
            "MINI_DROP_STORAGE_BACKEND": "local",
            "MINI_DROP_OBJECT_STORAGE_REQUIRED": "0",
            "MINI_DROP_AI_ENABLED": "none",
            "MINI_DROP_AGENT_RUNTIME": "deterministic",
            "MINI_DROP_API_AUTH_ENABLED": "0",
            "MINI_DROP_GRPC_PORT": str(grpc_port),
            "MINI_DROP_WEB_API_TARGET": f"http://127.0.0.1:{api_port}",
            "MINI_DROP_WEB_BASE_URL": f"http://127.0.0.1:{web_port}",
        })
        backend_log = (temp_root / "backend.log").open("w", encoding="utf-8")
        web_log = (temp_root / "web.log").open("w", encoding="utf-8")
        try:
            task_id = seed_browser_database(env)
            backend = start_backend(env, api_port, backend_log)
            wait_http(f"http://127.0.0.1:{api_port}/api/livez")
            npm = "npm.cmd" if os.name == "nt" else "npm"
            web = subprocess.Popen(
                [
                    "node",
                    str(ROOT / "web" / "node_modules" / "vite" / "bin" / "vite.js"),
                    "--host", "127.0.0.1", "--port", str(web_port),
                ],
                cwd=str(ROOT / "web"),
                env=env,
                stdout=web_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_http(f"http://127.0.0.1:{web_port}")
            test_env = env | {"MINI_DROP_C6_TASK_ID": task_id}
            first = run([npm, "--prefix", "web", "run", "test:e2e:c6"], env=test_env, timeout=240)
            if first["status"] != "PASS":
                return first | {"phase": "workspace"}

            cases = wait_http(f"http://127.0.0.1:{api_port}/api/v1/cases")
            case = None
            for item in cases["data"]["items"]:
                workspace = wait_http(
                    f"http://127.0.0.1:{api_port}/api/v1/cases/{item['case_id']}/workspace",
                )
                if (workspace.get("data") or {}).get("messages"):
                    case = item
                    break
            if case is None:
                raise RuntimeError("workspace E2E produced no Case with a persisted message")
            terminate_tree(backend)
            backend = start_backend(env, api_port, backend_log)
            wait_http(f"http://127.0.0.1:{api_port}/api/livez")
            restart_env = test_env | {"MINI_DROP_C6_CASE_ID": case["case_id"]}
            second = run([npm, "--prefix", "web", "run", "test:e2e:c6:restart"], env=restart_env, timeout=180)
            if second["status"] != "PASS":
                return second | {"phase": "restart"}
            health = wait_http(f"http://127.0.0.1:{api_port}/api/livez")
            return {
                "status": "PASS",
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "task_id": task_id,
                "case_id": case["case_id"],
                "workspace_e2e": first,
                "restart_e2e": second,
                "final_livez": health,
            }
        except Exception as exc:  # noqa: BLE001
            backend_log.flush()
            web_log.flush()
            return {
                "status": "FAIL",
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
                "backend_log_tail": (temp_root / "backend.log").read_text(
                    encoding="utf-8", errors="replace",
                )[-4000:],
                "web_log_tail": (temp_root / "web.log").read_text(
                    encoding="utf-8", errors="replace",
                )[-2000:],
            }
        finally:
            terminate_tree(backend)
            terminate_tree(web)
            backend_log.close()
            web_log.close()


def candidate_and_cleanup(candidate_manifest: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        manifest = json.loads(candidate_manifest.read_text(encoding="utf-8"))
        release_id = manifest["release_id"]
        archive = candidate_manifest.with_name(f"{release_id}.tar.gz")
        receipt_path = candidate_manifest.with_name(f"{release_id}.receipt.json")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assertions = {
            "release_id": receipt.get("release_id") == release_id,
            "payload_digest": receipt.get("payload_tree_digest") == manifest.get("payload_tree_digest"),
            "manifest_digest": manifest.get("manifest_digest") == canonical_digest({
                key: value for key, value in manifest.items() if key != "manifest_digest"
            }),
            "archive_digest": receipt.get("archive_sha256") == sha256_file(archive),
            "archive_verify": not verify_archive(archive, manifest, sys.executable),
        }
        contract = run([
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_candidate_package_contract.py",
            "tests/test_vm_fault_gate_contract.py",
        ])
        assertions["fault_cleanup_contract"] = contract["status"] == "PASS"
        return {
            "status": "PASS" if all(assertions.values()) else "FAIL",
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "release_id": release_id,
            "assertions": assertions,
            "contract_tests": contract,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "FAIL",
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--skip-browser", action="store_true", help="diagnostic only; produces FAIL for M12")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    candidate_manifest = args.candidate_manifest.resolve()
    report: dict[str, Any] = {
        "schema_version": "agent-beta-public-report-v3",
        "suite_id": "agent-beta-public-v3",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mandatory": {},
        "external_holdout": {
            "status": "AWAITING_EXTERNAL_HOLDOUT",
            "reason": "Blind holdout data and scoring authority are external by contract.",
        },
    }
    preflight = run([
        sys.executable,
        "scripts/validate_agent_beta_suite.py",
        "--public-v3-manifest",
        str(manifest_path),
    ])
    report["preflight"] = preflight
    if preflight["status"] != "PASS":
        report["status"] = "FAIL"
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for scenario in manifest["mandatory_scenarios"]:
            print(f"RUN {scenario['id']} {scenario['name']}", flush=True)
            if scenario.get("pytest"):
                result = run([sys.executable, "-m", "pytest", "-q", *scenario["pytest"]])
            elif scenario.get("runner") == "browser_real_backend":
                result = ({"status": "FAIL", "error": "browser explicitly skipped"}
                          if args.skip_browser else browser_real_backend())
            elif scenario.get("runner") == "candidate_and_cleanup":
                result = candidate_and_cleanup(candidate_manifest)
            else:
                result = {"status": "FAIL", "error": "unknown mandatory scenario runner"}
            report["mandatory"][scenario["id"]] = {
                "name": scenario["name"],
                **result,
            }
            print(f"{result['status']} {scenario['id']} {scenario['name']}", flush=True)
        report["status"] = (
            "PASS" if report["mandatory"]
            and all(item["status"] == "PASS" for item in report["mandatory"].values())
            else "FAIL"
        )

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

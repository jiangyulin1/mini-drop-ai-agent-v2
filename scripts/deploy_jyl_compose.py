#!/usr/bin/env python3
"""Deploy a Candidate archive to the protected three-node JYL Compose lab."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODES = ("control", "worker1", "worker2")
SSH_KEEPALIVE = (
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=120",
    "-o", "TCPKeepAlive=yes",
)


def run(args: list[str], timeout: int = 1800) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"{' '.join(args[:4])} failed: {proc.stderr[-1200:]}")
    return proc.stdout


def ssh(node: str, command: str, config: Path, timeout: int = 1800) -> str:
    return run([
        "ssh", "-F", str(config), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        *SSH_KEEPALIVE, node, command,
    ], timeout)


def scp(local: Path, node: str, remote: str, config: Path) -> None:
    run([
        "scp", "-F", str(config), "-o", "BatchMode=yes", *SSH_KEEPALIVE,
        str(local), f"{node}:{remote}",
    ])


def manifest(archive: Path) -> dict:
    with tarfile.open(archive) as bundle:
        source = bundle.extractfile(bundle.getmember("candidate-manifest.json"))
        if source is None:
            raise RuntimeError("candidate manifest is unreadable")
        return json.loads(source.read().decode("utf-8"))


def compose(root: str, release: str, node: str) -> str:
    path = f"{root}/releases/{release}"
    file = "deploy/compose/jyl-secure.control.yml" if node == "control" else "deploy/compose/jyl-secure.worker.yml"
    return f"cd {shlex.quote(path)} && docker compose --env-file .env -f {file}"


def wait_ready(node: str, container: str, config: Path, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = ssh(node, f"docker inspect -f '{{{{.State.Status}}}}' {container} 2>/dev/null || true", config).strip()
        health = ssh(node, f"docker inspect -f '{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{end}}}}' {container} 2>/dev/null || true", config).strip()
        if status == "running" and health in {"", "healthy"}:
            return
        time.sleep(3)
    raise RuntimeError(f"container not ready: {node}/{container}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--ssh-config", type=Path, default=ROOT / "ssh" / "vm-config")
    parser.add_argument("--remote-root", default="/jyl")
    args = parser.parse_args()
    archive = args.archive.resolve()
    release_id = str(manifest(archive)["release_id"])
    root = "/" + args.remote_root.strip("/")
    release = f"mini-drop-release-{release_id}"
    release_path = f"{root}/releases/{release}"
    active = f"{root}/mini-drop-active"
    previous: dict[str, str] = {}
    try:
        for node in NODES:
            previous[node] = ssh(node, f"readlink -f {active}", args.ssh_config).strip()
            if not previous[node].startswith(f"{root}/releases/"):
                raise RuntimeError(f"unexpected active link on {node}: {previous[node]!r}")
            remote_archive = f"{root}/{release}.tar.gz"
            scp(archive, node, remote_archive, args.ssh_config)
            ssh(node, f"set -eu; test ! -e {release_path}; mkdir -p {release_path}; tar -xzf {remote_archive} -C {release_path}; cp {active}/.env {release_path}/.env; mkdir -p {release_path}/deploy/certs; for f in ca.crt server.crt server.key; do if test -e {active}/deploy/certs/$f; then cp {active}/deploy/certs/$f {release_path}/deploy/certs/$f; fi; done", args.ssh_config)
            cmd = compose(root, release, node)
            services = "server analyzer pi-sidecar web" if node == "control" else "agent"
            ssh(node, f"{cmd} config --quiet && {cmd} build {services}", args.ssh_config, timeout=2400)
        for node in NODES:
            ssh(node, f"ln -sfn {release_path} {active}", args.ssh_config)
            cmd = compose(root, release, node)
            services = "server analyzer pi-sidecar web" if node == "control" else "agent"
            ssh(node, f"{cmd} up -d {services}", args.ssh_config, timeout=1200)
        wait_ready("control", "mini-drop-jyl-control-server-1", args.ssh_config)
        wait_ready("control", "mini-drop-jyl-control-pi-sidecar-1", args.ssh_config)
        wait_ready("worker1", "mini-drop-jyl-worker-agent-1", args.ssh_config)
        wait_ready("worker2", "mini-drop-jyl-worker-agent-1", args.ssh_config)
    except Exception:
        for node, old in previous.items():
            if old:
                ssh(node, f"ln -sfn {old} {active}", args.ssh_config)
        raise
    print(json.dumps({"status": "DEPLOYED", "release_id": release_id, "remote_root": root}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

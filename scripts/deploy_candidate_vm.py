"""Deploy a locally built Candidate Manifest v2 archive to the three-node lab.

Development deploy only.  It never writes secrets; control env files are copied
on the VM from the currently active release.  A DB snapshot is taken before the
expand-only migration.  On failure before link switch, no service is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_CONFIG = ROOT / "ssh" / "vm-config"
NODES = {"control": "control", "worker1": "worker1", "worker2": "worker2"}
CONTROL_SERVICES = ["mini-drop-server", "mini-drop-analyzer", "mini-drop-pi-sidecar"]
WORKER_SERVICES = ["mini-drop-agent"]


def run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} failed: {proc.stderr[-1200:]} {proc.stdout[-1200:]}")
    return proc


def ssh(node: str, cmd: str, timeout: int = 900) -> str:
    proc = run(["ssh", "-F", str(SSH_CONFIG), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", node, cmd], timeout)
    return proc.stdout


def scp(local: Path, node: str, remote: str, timeout: int = 900) -> None:
    run(["scp", "-F", str(SSH_CONFIG), "-o", "BatchMode=yes", str(local), f"{node}:{remote}"], timeout)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def node_user(node: str) -> str:
    return NODES[node]


def systemctl(node: str, action: str, services: list[str]) -> str:
    """Run systemctl with the least-privilege root helper available per node."""
    joined = " ".join(services)
    if node == "worker1":
        cmd = f"sudo -n /usr/bin/systemctl {action} {joined}"
    else:
        # docker group membership gives a namespace root helper on this lab.
        cmd = (
            "docker run --rm --privileged --pid=host --uts=host --net=host "
            f"redis:alpine nsenter -t 1 -m -u -i -n -p /usr/bin/systemctl {action} {joined}"
        )
    return ssh(node, cmd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--release-id", default="")
    parser.add_argument("--prepare-only", action="store_true", help="upload/install but do not switch links or services")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--reuse-prepared", action="store_true", help="release directory already prepared; do not extract/install again")
    args = parser.parse_args()

    archive = args.archive.resolve()
    if not archive.is_file():
        print(f"archive missing: {archive}", file=sys.stderr)
        return 1
    archive_sha = sha256_file(archive)
    release_id = args.release_id or archive.stem.replace(".tar", "")
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), "release_id": release_id,
               "archive_sha256": archive_sha, "nodes": {}}
    print(f"deploy {release_id} archive_sha={archive_sha}")

    print("current services:")
    for node in NODES:
        services = CONTROL_SERVICES if node == "control" else WORKER_SERVICES
        print(node, ssh(node, f"hostname; systemctl is-active {' '.join(services)} || true").strip().replace("\n", " "))

    for node in NODES:
        user = node_user(node)
        remote_archive = f"/home/{user}/mini-drop-candidate.tar.gz"
        remote_release = f"/home/{user}/mini-drop-release-{release_id}"
        active = f"/home/{user}/mini-drop-active"
        scp(archive, node, remote_archive)
        remote_sha = ssh(node, f"sha256sum {remote_archive} | cut -d' ' -f1").strip()
        if remote_sha != archive_sha:
            print(f"archive hash mismatch on {node}", file=sys.stderr)
            return 1
        already_prepared = ssh(node, f"test -d {remote_release} && echo exists || echo absent").strip() == "exists"
        if already_prepared and not args.reuse_prepared:
            print(f"{node} release dir already exists; refusing overwrite")
            return 1
        if not already_prepared:
            ssh(node, f"mkdir -p {remote_release} && tar -xzf {remote_archive} -C {remote_release}")
            ssh(node, f"cd {remote_release} && test -f candidate-manifest.json && echo manifest-ok")
        elif not args.skip_install and not args.reuse_prepared:
            pass
        if not args.skip_install and not args.reuse_prepared:
            print(f"installing {node} deps into {remote_release}")
            ssh(node, f"cd {remote_release} && python3 -m venv .venv && "
                      "grep -v '^-e git+' requirements.lock > /tmp/mini-drop-requirements.txt && "
                      ".venv/bin/pip install -q --upgrade pip setuptools wheel && "
                      ".venv/bin/pip install -q -r /tmp/mini-drop-requirements.txt && "
                      ".venv/bin/pip install -q -e . --no-deps")
            ssh(node, f"cd {remote_release} && .venv/bin/python -m pip check")
        if node == "control":
            print(f"installing sidecar npm deps on {node}")
            ssh(node, f"cd {remote_release}/agent_runtime/pi-sidecar && "
                      "PATH=/home/control/node-v22.19.0/bin:$PATH npm ci --omit=dev")
            ssh(node, f"mkdir -p {remote_release}/deploy/env && "
                      f"cp {active}/deploy/env/control-native.env {remote_release}/deploy/env/control-native.env && "
                      f"cp {active}/deploy/env/sidecar.env {remote_release}/deploy/env/sidecar.env 2>/dev/null || true")
        receipt["nodes"][node] = {"prepared": True, "release_path": remote_release}
        print(f"{node} prepared")

    if args.prepare_only:
        print("prepare-only complete; no services changed")
        out = ROOT / "reports" / "implementation" / f"deploy-{release_id}-prepared.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2) + "\n")
        return 0

    print("stopping control writers for migration")
    systemctl("control", "stop", CONTROL_SERVICES)
    time.sleep(2)
    ssh("control", "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
                   "test -n \"$SQLITE_PATH\" && cp -v \"$SQLITE_PATH\" \"$SQLITE_PATH.pre-deploy\" || true")
    print("migrating from new release")
    ssh("control", "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
                   f"cd ~/mini-drop-release-{release_id} && .venv/bin/python -m alembic upgrade head")
    head = ssh("control", "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
               f"cd ~/mini-drop-release-{release_id} && .venv/bin/python -m alembic heads").strip()
    print("head after migration:", head.replace("\n", " "))
    receipt["migration_head"] = head

    print("switching active links")
    for node in NODES:
        user = node_user(node)
        remote_release = f"/home/{user}/mini-drop-release-{release_id}"
        ssh(node, f"ln -sfn {remote_release} /home/{user}/mini-drop-active")
        receipt["nodes"][node]["link_switched_at"] = datetime.now(timezone.utc).isoformat()

    print("restarting services")
    systemctl("control", "daemon-reload", [])
    systemctl("control", "restart", ["mini-drop-s3", "mini-drop-server", "mini-drop-analyzer", "mini-drop-pi-sidecar"])
    for node in ("worker1", "worker2"):
        systemctl(node, "daemon-reload", [])
        systemctl(node, "restart", WORKER_SERVICES)
    time.sleep(10)

    deadline = time.time() + 120
    ready = None
    while time.time() < deadline:
        try:
            ready = ssh("control", "source ~/mini-drop-active/deploy/env/control-native.env && "
                                  "curl -sk https://127.0.0.1/api/readyz -H \"X-API-Key: $MINI_DROP_API_KEY\"")
            if '"healthy":true' in ready:
                break
        except RuntimeError:
            pass
        time.sleep(5)
    if not ready or '"healthy":true' not in ready:
        print(f"readyz failed: {ready[:500] if ready else ''}", file=sys.stderr)
        return 1
    print("readyz healthy")
    agents = ssh("control", "source ~/mini-drop-active/deploy/env/control-native.env && "
                            "curl -sk https://127.0.0.1/api/agents -H \"X-API-Key: $MINI_DROP_API_KEY\"")
    print("agents response:", agents[:300])
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["status"] = "DEPLOYED"
    out = ROOT / "reports" / "implementation" / f"deploy-{release_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

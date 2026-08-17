"""Deploy a locally built Candidate Manifest v2 archive to the three-node lab.

Development deploy only.  It never writes secrets; control env files are copied
on the VM from the currently active release.  A DB snapshot is taken before the
expand-only migration.  On failure before link switch, no service is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import tarfile
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


def load_candidate_manifest(archive: Path) -> dict:
    with tarfile.open(archive) as bundle:
        member = bundle.getmember("candidate-manifest.json")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError("candidate manifest is not readable")
        return json.loads(source.read().decode("utf-8"))


def candidate_identity(manifest: dict) -> dict:
    return {
        "release_id": manifest["release_id"],
        "payload_tree_digest": manifest["payload_tree_digest"],
        "lock_digest": manifest["lock_digest"],
        "migration_head": manifest["migration_head"],
        "package_version": manifest["actual_package_version"],
        "pi_version": manifest["actual_pi_version"],
        "actual_package_version": manifest["actual_package_version"],
        "actual_pi_version": manifest["actual_pi_version"],
    }


def parse_migration_head(output: str) -> str:
    """Return the single Alembic revision from ``alembic heads`` output.

    Alembic renders a normal head as ``<revision> (head)``.  Receipts and the
    Candidate Manifest intentionally store the stable revision identifier, not
    the presentation suffix.  Multiple heads remain a hard deployment error.
    """
    revisions = [line.split()[0] for line in output.splitlines() if line.strip()]
    if len(revisions) != 1:
        raise RuntimeError(f"expected exactly one migration head, got {revisions!r}")
    return revisions[0]


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


def host_root_shell(node: str, command: str) -> str:
    """Run a bounded host command through the lab's existing namespace helper."""
    cmd = (
        "docker run --rm --privileged --pid=host --uts=host --net=host "
        "redis:alpine nsenter -t 1 -m -u -i -n -p /bin/sh -c "
        f"{shlex.quote(command)}"
    )
    return ssh(node, cmd)


def main() -> int:
    global SSH_CONFIG
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ssh-config", type=Path, default=SSH_CONFIG)
    parser.add_argument("--release-id", default="")
    parser.add_argument("--prepare-only", action="store_true", help="upload/install but do not switch links or services")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--reuse-prepared", action="store_true", help="release directory already prepared; do not extract/install again")
    args = parser.parse_args()

    SSH_CONFIG = args.ssh_config.resolve()
    if not SSH_CONFIG.is_file():
        print(f"SSH config missing: {SSH_CONFIG}", file=sys.stderr)
        return 1

    archive = args.archive.resolve()
    if not archive.is_file():
        print(f"archive missing: {archive}", file=sys.stderr)
        return 1
    archive_sha = sha256_file(archive)
    manifest = load_candidate_manifest(archive)
    if args.manifest:
        published_manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
        if published_manifest != manifest:
            print("published manifest does not match embedded candidate manifest", file=sys.stderr)
            return 1
    release_id = args.release_id or manifest["release_id"]
    if release_id != manifest["release_id"]:
        print("release ID does not match embedded candidate manifest", file=sys.stderr)
        return 1
    identity = candidate_identity(manifest)
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), **identity,
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
            ssh(node, f"cd {remote_release} && python3 -m venv .uv-bootstrap && "
                      ".uv-bootstrap/bin/pip install -q uv==0.12.5 && "
                      ".uv-bootstrap/bin/uv sync --locked --no-dev --python python3")
            ssh(node, f"cd {remote_release} && "
                      ".uv-bootstrap/bin/uv pip check --python .venv/bin/python")
        if node == "control":
            print(f"installing sidecar npm deps on {node}")
            ssh(node, f"cd {remote_release}/agent_runtime/pi-sidecar && "
                      "PATH=/home/control/node-v22.19.0/bin:$PATH npm ci --omit=dev")
            ssh(node, f"mkdir -p {remote_release}/deploy/env && "
                      f"cp {active}/deploy/env/control-native.env {remote_release}/deploy/env/control-native.env && "
                      f"cp {active}/deploy/env/sidecar.env {remote_release}/deploy/env/sidecar.env 2>/dev/null || true")
            web_release = f"/var/www/mini-drop-release-{release_id}"
            host_root_shell(
                node,
                f"test ! -e {web_release} && install -d -m 0755 {web_release} && "
                f"cp -a {remote_release}/web/dist/. {web_release}/ && "
                f"test -f {web_release}/index.html",
            )
        remote_manifest = json.loads(ssh(node, f"cat {remote_release}/candidate-manifest.json"))
        if candidate_identity(remote_manifest) != identity:
            print(f"candidate identity mismatch on {node}", file=sys.stderr)
            return 1
        installed_version = ssh(
            node,
            f"cd {remote_release} && .venv/bin/python -c 'import importlib.metadata as m; "
            "print(m.version(\"micro-drop\"))'",
        ).strip()
        installed_pi = "not-applicable"
        if node == "control":
            installed_pi = ssh(
                node,
                f"cd {remote_release}/agent_runtime/pi-sidecar && "
                "PATH=/home/control/node-v22.19.0/bin:$PATH node -e 'const fs=require(\"fs\");"
                "console.log(JSON.parse(fs.readFileSync(\"node_modules/@earendil-works/pi-coding-agent/package.json\")).version)'",
            ).strip()
            if installed_pi != identity["pi_version"]:
                print(f"Pi runtime version mismatch on {node}: {installed_pi}", file=sys.stderr)
                return 1
        if installed_version != identity["package_version"]:
            print(f"package version mismatch on {node}: {installed_version}", file=sys.stderr)
            return 1
        receipt["nodes"][node] = {
            "prepared": True,
            "release_path": remote_release,
            **identity,
            "archive_sha256": archive_sha,
            "installed_package_version": installed_version,
            "installed_pi_version": installed_pi,
        }
        if node == "control":
            receipt["nodes"][node]["web_release_path"] = web_release
        print(f"{node} prepared")

    if args.prepare_only:
        print("prepare-only complete; no services changed")
        out = ROOT / "reports" / "implementation" / f"deploy-{release_id}-prepared.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2) + "\n")
        return 0

    previous_links: dict[str, str] = {}
    for node in NODES:
        user = node_user(node)
        previous = ssh(node, f"readlink -f /home/{user}/mini-drop-active").strip()
        if not previous.startswith(f"/home/{user}/mini-drop-release-"):
            print(f"unsafe or missing previous active link on {node}: {previous!r}", file=sys.stderr)
            return 1
        previous_links[node] = previous
        receipt["nodes"][node]["previous_release_path"] = previous

    previous_web_link = host_root_shell("control", "readlink -f /var/www/mini-drop-active").strip()
    if not previous_web_link.startswith("/var/www/mini-drop-release-"):
        print(f"unsafe or missing previous web link: {previous_web_link!r}", file=sys.stderr)
        return 1
    receipt["nodes"]["control"]["previous_web_release_path"] = previous_web_link

    head = ""
    try:
        print("stopping control writers for migration")
        systemctl("control", "stop", CONTROL_SERVICES)
        time.sleep(2)
        ssh("control", "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
                       "test -n \"$SQLITE_PATH\" && cp -v \"$SQLITE_PATH\" \"$SQLITE_PATH.pre-deploy\" || true")
        print("migrating from new release")
        ssh("control", "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
                       f"cd ~/mini-drop-release-{release_id} && .venv/bin/python -m alembic upgrade head")
        head_output = ssh(
            "control",
            "set -a; source ~/mini-drop-active/deploy/env/control-native.env; set +a; "
            f"cd ~/mini-drop-release-{release_id} && .venv/bin/python -m alembic heads",
        ).strip()
        head = parse_migration_head(head_output)
        print("head after migration:", head, f"(raw: {head_output.replace(chr(10), ' ')})")
        receipt["migration_head"] = head
        if head != identity["migration_head"]:
            raise RuntimeError(
                f"migration head mismatch: expected {identity['migration_head']}, got {head}"
            )

        print("switching active links")
        for node in NODES:
            user = node_user(node)
            remote_release = f"/home/{user}/mini-drop-release-{release_id}"
            ssh(node, f"ln -sfn {remote_release} /home/{user}/mini-drop-active")
            receipt["nodes"][node]["link_switched_at"] = datetime.now(timezone.utc).isoformat()
        web_release = f"/var/www/mini-drop-release-{release_id}"
        host_root_shell(
            "control",
            f"ln -sfn {web_release} /var/www/mini-drop-active && "
            f"cmp -s {web_release}/index.html /home/control/mini-drop-active/web/dist/index.html",
        )
        receipt["nodes"]["control"]["web_link_switched_at"] = datetime.now(timezone.utc).isoformat()

        print("restarting services")
        systemctl("control", "daemon-reload", [])
        systemctl("control", "restart", [
            "mini-drop-s3", "mini-drop-server", "mini-drop-analyzer", "mini-drop-pi-sidecar",
        ])
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
            raise RuntimeError(f"readyz failed: {ready[:500] if ready else ''}")
        print("readyz healthy")
        agents = ssh("control", "source ~/mini-drop-active/deploy/env/control-native.env && "
                                "curl -sk https://127.0.0.1/api/agents -H \"X-API-Key: $MINI_DROP_API_KEY\"")
        if '"ONLINE"' not in agents:
            raise RuntimeError(f"no ONLINE worker after activation: {agents[:500]}")
        print("agents response:", agents[:300])
    except Exception as exc:  # noqa: BLE001
        print(f"activation failed; restoring previous links: {exc}", file=sys.stderr)
        rollback_errors: list[str] = []
        for node, previous in previous_links.items():
            user = node_user(node)
            try:
                ssh(node, f"ln -sfn {previous} /home/{user}/mini-drop-active")
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"{node} link: {rollback_exc}")
        try:
            host_root_shell("control", f"ln -sfn {previous_web_link} /var/www/mini-drop-active")
        except Exception as rollback_exc:  # noqa: BLE001
            rollback_errors.append(f"control web link: {rollback_exc}")
        try:
            systemctl("control", "restart", [
                "mini-drop-s3", "mini-drop-server", "mini-drop-analyzer", "mini-drop-pi-sidecar",
            ])
        except Exception as rollback_exc:  # noqa: BLE001
            rollback_errors.append(f"control services: {rollback_exc}")
        for node in ("worker1", "worker2"):
            try:
                systemctl(node, "restart", WORKER_SERVICES)
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"{node} services: {rollback_exc}")
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        receipt["status"] = "ROLLBACK_FAILED" if rollback_errors else "ROLLED_BACK"
        receipt["activation_error"] = str(exc)
        receipt["rollback_errors"] = rollback_errors
        out = ROOT / "reports" / "implementation" / f"deploy-{release_id}-failed.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return 1

    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["status"] = "DEPLOYED"
    for node in NODES:
        receipt["nodes"][node]["migration_head"] = head
        node_receipt = {
            "schema_version": "node-deployment-receipt-v1",
            "node": node,
            "status": "DEPLOYED",
            "finished_at": receipt["finished_at"],
            **receipt["nodes"][node],
        }
        node_out = ROOT / "reports" / "implementation" / f"deploy-{release_id}-{node}.receipt.json"
        node_out.parent.mkdir(parents=True, exist_ok=True)
        node_out.write_text(json.dumps(node_receipt, indent=2) + "\n", encoding="utf-8")
        remote_receipt = json.dumps(node_receipt, separators=(",", ":"))
        ssh(node, f"printf '%s' '{remote_receipt}' > ~/mini-drop-release-{release_id}/deployment-receipt.json")
    out = ROOT / "reports" / "implementation" / f"deploy-{release_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

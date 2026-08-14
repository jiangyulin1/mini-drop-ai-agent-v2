#!/usr/bin/env python3
"""E8/V1: Deploy the current precise candidate to the control VM.

Implements the transactional deploy order from plan 19.3:
local gate → immutable candidate → upload to a NEW release dir → dependency/venv
reuse (deps unchanged in E0-E9, so the previous release's site-packages are
copied, avoiding offline-registry problems) → protected env reuse → migrations
→ activate symlink → health check.  Activation failure rolls back the symlinks.

The password is read from MINI_DROP_VM_PASSWORD (never from argv/logs).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.vm_environment import SSHGate, load_topology  # noqa: E402


class Deploy:
    def __init__(self, ssh: SSHGate, topology, release_id: str):
        self._ssh = ssh
        self._topo = topology
        self.release_id = release_id
        self.server_dir = f"/home/control/mini-drop-release-{release_id}"
        self.web_dir = f"/var/www/mini-drop-release-{release_id}"
        self.old_server = "/home/control/mini-drop-release-20260813-beta-baseline"

    def run(self, node, command: str, *, timeout: int = 240, sudo: bool = False) -> str:
        if sudo:
            return self._sudo(node, command, timeout=timeout)
        client = self._ssh_connect(node)
        try:
            _, out, err = client.exec_command(command, timeout=timeout)
            out_text = out.read().decode("utf-8", "replace")
            err_text = err.read().decode("utf-8", "replace")
            code = out.channel.recv_exit_status()
            if code:
                raise RuntimeError(f"{node.name} cmd failed ({code}): {err_text[-600:] or out_text[-600:]}")
            return out_text
        finally:
            client.close()

    def _sudo(self, node, command: str, *, timeout: int) -> str:
        pw = shlex.quote(self._ssh.password)
        return self.run(node, f"printf '%s\\n' {pw} | sudo -S {command}", timeout=timeout)

    def _ssh_connect(self, node):
        return self._ssh.connect(node)

    def upload(self, node, local: Path, remote: str) -> None:
        client = self._ssh.connect(node)
        try:
            sftp = client.open_sftp()
            sftp.put(str(local), remote)
            sftp.close()
        finally:
            client.close()

    # ── 步骤 ─────────────────────────────────────────────────────────

    def step_upload(self, archive: Path) -> str:
        control = self._topo.control
        remote = f"/home/control/{archive.name}"
        print(f"[deploy] upload {archive.name} -> {control.ip}")
        self.upload(control, archive, remote)
        return remote

    def step_extract(self, remote_archive: str) -> None:
        control = self._topo.control
        print(f"[deploy] extract into {self.server_dir}")
        self.run(control, f"mkdir -p {self.server_dir} && tar -xzf {remote_archive} -C {self.server_dir} --strip-components=0", timeout=300)

    def step_venv(self) -> None:
        control = self._topo.control
        print("[deploy] create venv + reuse previous site-packages (deps unchanged)")
        self.run(control, f"python3 -m venv {self.server_dir}/.venv")
        self.run(control, (
            f"cp -a {self.old_server}/.venv/lib/python3.12/site-packages/. "
            f"{self.server_dir}/.venv/lib/python3.12/site-packages/"
        ), timeout=120)
        # verify import of new code
        self.run(control, f"{self.server_dir}/.venv/bin/python -m compileall -q {self.server_dir}/server", timeout=180)
        self.run(control, f"cd {self.server_dir} && .venv/bin/python -c 'import server.app.main'", timeout=120)

    def step_env(self) -> None:
        control = self._topo.control
        print("[deploy] reuse protected env (never packaged)")
        self.run(control, f"mkdir -p {self.server_dir}/deploy/env && cp {self.old_server}/deploy/env/control-native.env {self.server_dir}/deploy/env/control-native.env")

    def step_web(self) -> None:
        control = self._topo.control
        print(f"[deploy] stage web release -> {self.web_dir}")
        # /var/www 属 root；整条链必须跑在单个 sudo bash -c 内，
        # 否则 && 后的 cp 会以普通用户执行。
        self._sudo(control, f"bash -c 'mkdir -p {self.web_dir} && cp -a {self.server_dir}/web/dist/. {self.web_dir}/'", timeout=120)

    def step_migrate(self) -> str:
        control = self._topo.control
        print("[deploy] run alembic migrations against the real DB")
        # 与 server 启动同路径：source env 后走 init_db() → upgrade_database()，
        # 避免 alembic.ini 的默认 sqlite:///mini_drop.db 迁移到错误的空库。
        self.run(control, (
            f"cd {self.server_dir} && source deploy/env/control-native.env && "
            ".venv/bin/python -c 'from server.app.database import init_db; init_db()' 2>&1"
        ), timeout=240)
        head = self.run(control, (
            f"cd {self.server_dir} && source deploy/env/control-native.env && "
            ".venv/bin/python -m alembic -c alembic.ini current 2>&1 | tail -1"
        ), timeout=120)
        return head.strip()

    def step_activate(self, web_active=True) -> str:
        control = self._topo.control
        print("[deploy] activate symlinks")
        prev_server = self.run(control, "readlink -f /home/control/mini-drop-active")
        prev_web = self.run(control, "readlink -f /var/www/mini-drop-active")
        try:
            self._sudo(control, (
                f"bash {self.server_dir}/deploy/scripts/activate-native-release.sh "
                f"{self.server_dir} {self.web_dir}"
            ), timeout=300)
            return f"{prev_server.strip()}|{prev_web.strip()}"
        except RuntimeError:
            print("[deploy] activation failed; rolling back symlinks")
            self._sudo(control, f"ln -sfn {prev_server.strip()} /home/control/mini-drop-active", timeout=60)
            self._sudo(control, f"ln -sfn {prev_web.strip()} /var/www/mini-drop-active", timeout=60)
            raise

    def step_health(self) -> dict[str, str]:
        control = self._topo.control
        print("[deploy] health check")
        checks: dict[str, str] = {}
        for name, url in (
            ("livez", "https://127.0.0.1/api/livez"),
            ("readyz", "https://127.0.0.1/api/readyz"),
            ("manifest_active", "readlink -f /home/control/mini-drop-active"),
        ):
            try:
                if url.startswith("readlink"):
                    checks[name] = self.run(control, url).strip()
                else:
                    checks[name] = self.run(control, f"curl -sk -o /dev/null -w '%{{http_code}}' {url}", timeout=20).strip()
            except RuntimeError as exc:
                checks[name] = f"FAIL:{str(exc)[:100]}"
        # worker agents online
        for worker in self._topo.workers.values():
            try:
                checks[f"{worker.name}_agent"] = self.run(worker, "systemctl is-active mini-drop-agent 2>/dev/null || echo inactive").strip()
            except RuntimeError as exc:
                checks[f"{worker.name}_agent"] = f"FAIL:{str(exc)[:100]}"
        return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--env-profile", type=Path,
                        default=ROOT / "benchmarks/environments/hyperv_online_boutique_verified_vm.json")
    parser.add_argument("--ssh-known-hosts", type=Path, default=None)
    parser.add_argument("--rollback-only", action="store_true")
    args = parser.parse_args()

    password = os.getenv("MINI_DROP_VM_PASSWORD", "")
    if not password:
        parser.error("MINI_DROP_VM_PASSWORD is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    release_id = str(manifest.get("release_id") or args.archive.stem)
    topology = load_topology(str(args.env_profile))
    known_hosts = args.ssh_known_hosts or (ROOT / "deploy/ssh/mini-drop_vm_known_hosts")
    deploy = Deploy(SSHGate(password, known_hosts=known_hosts), topology, release_id)

    if args.rollback_only:
        prev = "/home/control/mini-drop-release-20260813-beta-baseline"
        print("[rollback] restoring previous symlinks")
        deploy._sudo(topology.control, f"ln -sfn {prev} /home/control/mini-drop-active")
        deploy._sudo(topology.control, "ln -sfn /var/www/mini-drop-release-20260813-beta-baseline /var/www/mini-drop-active")
        print("[rollback] verifying readyz")
        print(deploy.step_health())
        return 0

    try:
        remote = deploy.step_upload(args.archive)
        deploy.step_extract(remote)
        deploy.step_venv()
        deploy.step_env()
        deploy.step_web()
        head = deploy.step_migrate()
        prev = deploy.step_activate()
        checks = deploy.step_health()
    except RuntimeError as exc:
        print(f"[deploy] FAILED: {exc}")
        return 1

    report = {
        "release_id": release_id,
        "server_dir": deploy.server_dir,
        "web_dir": deploy.web_dir,
        "migration_head": head,
        "previous_links": prev,
        "health": checks,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_dir = ROOT / "reports" / "implementation" / "vm-deploy"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{release_id}.deploy.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all(
        value.startswith(("200", "FAIL:")) or "mini-drop-release" in value
        for value in checks.values()
    ) and all("FAIL" not in value for value in checks.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

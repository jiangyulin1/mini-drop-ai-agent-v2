# C7 Candidate and deployment gate contract (2026-08-17)

## Completed locally

- `uv.lock` is the cross-platform Python source lock; CI runs `uv lock --check` and
  `uv sync --locked` on Windows/Linux and Python 3.9/3.11.
- Candidate identity covers tracked changes, allowed untracked source, generated Web output,
  migrations, `uv.lock`, the generated requirements export, and both npm locks.
- Text normalization follows `.gitattributes`; canonical modes, tar metadata and gzip timestamps
  are deterministic. A repeated build of an unchanged payload reuses the same verified archive.
- Publication order is temporary archive, full member/manifest/tree verification, then atomic
  final-name publication. A failed verification leaves no final archive or receipt.
- The manifest records actual Python package and Pi runtime versions and migration head. The
  deployer verifies those values after locked installation on the target node.
- The deployment receipt contract carries release, payload, lock, migration and real runtime
  identity on every node. Activation failure restores all prior active symlinks and restarts the
  prior services, while emitting a failed/rollback receipt.
- `vm_fault_injection_gate.py` restricts faults to the Pi Sidecar, enforces a 10–300 second TTL,
  installs a remote watchdog, restores the Provider environment in `finally`, removes backups,
  and requires a final readiness check. Its fault/cleanup contract tests pass.

## Commands exercised

```text
python -m pytest -q tests/test_candidate_package_contract.py tests/test_vm_fault_gate_contract.py
python scripts/package_candidate.py --build-web --verify
python scripts/package_candidate.py --skip-build --verify
python scripts/check_migrations.py
```

The locally generated Candidate must pass extracted protobuf compilation, application import,
migration graph validation, member hashes, manifest digest and payload-tree digest. Generated
receipts live under ignored `reports/implementation/candidates/` and are intentionally not used as
evidence for a later payload.

## Live three-node evidence contract

This tracked document defines the gate but deliberately does not embed a mutable rehearsal result.
The result for the candidate under test is authoritative only when all of the following ignored,
machine-readable artifacts refer to the same `release_id`, payload-tree digest, lock digest,
migration head, Python package version and Pi runtime version:

- `reports/implementation/deploy-<release_id>.json` with `status=DEPLOYED`;
- `reports/implementation/deploy-<release_id>-<node>.receipt.json` for `control`, `worker1` and
  `worker2`;
- the VM smoke report covering readiness, both online Agents, an ordinary Drop and an Agent turn;
- `reports/implementation/vm-fault-injection-gate.json` with every scoped fault and the final
  cleanup/readiness check passing.

The non-secret host aliases live at `ssh/vm-config`. Credentials are supplied only to the invoking
process and must never be persisted in the repository, candidate archive, receipts or command
output. A local package receipt, a prepared-only receipt, a report for another payload, or a report
without successful cleanup is not C7 completion evidence.

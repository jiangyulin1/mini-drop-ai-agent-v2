# Release baseline runbook

## Quality gates

```bash
python -m pip install uv==0.12.5
uv sync --locked --extra dev
uv lock --check
uv run --locked python scripts/check_repo_hygiene.py
uv run --locked python scripts/compile_proto.py
uv run --locked python scripts/check_migrations.py
uv run --locked python -m ruff check server agent analyzer
uv run --locked python -m pytest -q
uv run --locked python scripts/generate_implementation_baseline.py --run-tests
cd web
npm ci
npm run audit:prod
npm run lint
npm test
npm run build
```

`uv.lock` is the cross-platform source of truth. Regenerate the traditional
pip input only from that lock and keep the generated command header:

```bash
uv export --locked --all-extras --no-emit-project --format requirements.txt --output-file requirements.lock
```

Validate every supported Compose topology without starting containers:

```bash
docker compose --env-file .env.example config --no-interpolate --quiet
docker compose --env-file deploy/env/control.env.example -f docker-compose.control.yml config --no-interpolate --quiet
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.local.yml config --no-interpolate --quiet
bash -n deploy/scripts/*.sh
```

## Activation and health semantics

- `/api/livez` is process liveness only. Dependency failures must not cause a restart loop.
- `/api/readyz` is the release and traffic-admission gate. It returns HTTP 503 until every required dependency is available.
- `/api/readyz?core_only=true` is the Server container bootstrap gate; it ignores only the Analyzer to avoid a circular startup dependency.
- `/api/healthz` is a diagnostic report that always returns HTTP 200; never use its HTTP status alone to activate a release.

Full and Control deployments require object storage. The local Compose override uses shared local volumes and explicitly disables the storage dependency. The native activation script must pass `/api/readyz`; failure triggers its symlink rollback path.

## PostgreSQL migration

Set `DATABASE_URL` to the target PostgreSQL database and run:

```bash
alembic current
alembic upgrade head
alembic check
```

Application startup also runs `upgrade head`. Legacy databases must be upgraded through the
versioned migration chain; run `python scripts/check_migrations.py` before release and do not
manually stamp a production database unless the schema has been independently verified.

## Content-addressed Candidate and three-node deployment

`uv.lock`, the generated `requirements.lock`, the Pi Sidecar npm lock and the Web npm lock are
all release inputs. Build and verify the immutable Candidate from the current working tree:

```bash
uv run --locked python scripts/package_candidate.py --build-web --verify
```

The command normalizes repository text according to `.gitattributes`, includes allowed tracked
and untracked source plus generated `web/dist`, scans for credentials/private keys, verifies every
member digest after extraction, imports the application, compiles protobufs and checks the
migration graph. It writes these files below `reports/implementation/candidates/`:

- `cand-<payload>.tar.gz`
- `cand-<payload>.manifest.json`
- `cand-<payload>.receipt.json`

Identical normalized payloads produce identical release IDs and archive bytes on Windows and
Linux. A failed verification is never published under the final archive name. Runtime `.venv`,
`node_modules`, protected environment files, runtime spools and test reports are excluded.

Create a non-secret OpenSSH config at `ssh/vm-config` with aliases `control`, `worker1` and
`worker2`. Authentication must use an agent or key; passwords and private keys are not stored in
this repository. Deploy the same Candidate to every node:

```bash
uv run --locked python scripts/deploy_candidate_vm.py \
  --manifest reports/implementation/candidates/cand-<payload>.manifest.json \
  --archive reports/implementation/candidates/cand-<payload>.tar.gz \
  --ssh-config ssh/vm-config \
  --prepare-only

uv run --locked python scripts/deploy_candidate_vm.py \
  --manifest reports/implementation/candidates/cand-<payload>.manifest.json \
  --archive reports/implementation/candidates/cand-<payload>.tar.gz \
  --ssh-config ssh/vm-config
```

Preparation installs from `uv.lock` with `uv sync --locked --no-dev`, installs the Sidecar with
`npm ci --omit=dev`, and verifies the installed Python package and Pi runtime versions against the
embedded manifest. Activation migrates to the manifest head, switches the three active symlinks,
restarts services and writes a per-node `deployment-receipt.json`. Do not accept the deployment
unless all three receipts have exactly the same `release_id`, `payload_tree_digest`, `lock_digest`,
`migration_head`, `actual_package_version` and `actual_pi_version`.

Run the scoped failure rehearsal after deployment:

```bash
uv run --locked python scripts/vm_fault_injection_gate.py \
  --ssh-config ssh/vm-config --ttl-seconds 120
```

The gate exercises an ordinary Drop, Pi-unavailable deterministic fallback, an independent
Provider failure, MCP-unavailable explicit Gap, Drop availability during the Pi fault, and final
readiness. Every service/environment fault has a remote TTL watchdog and an unconditional local
`finally` cleanup. The Provider environment backup stays in `/tmp` with mode `0600`, is never
printed, and is removed after restoration. A report is PASS only when every mandatory assertion,
both cleanup assertions and the final health check pass.

## Database backup and restore rehearsal

Always restore into a separate rehearsal database first:

```bash
pg_dump --format=custom --file=mini-drop.dump "$DATABASE_URL"
createdb mini_drop_restore_test
pg_restore --exit-on-error --no-owner --dbname=mini_drop_restore_test mini-drop.dump
```

Compare table counts and the `alembic_version` value before considering a production restore.

## MinIO backup, restore and reconciliation

The object snapshot manifest contains the original key, size and SHA-256:

```bash
python scripts/snapshot_objects.py backup --bucket mini-drop --output backup/minio
python scripts/snapshot_objects.py restore --input backup/minio --bucket mini-drop-restore-test
python scripts/reconcile_storage.py --verify-sha256
```

`reconcile_storage.py` fails on missing objects or size/hash mismatches. Orphans are reported
separately and can be promoted to a failure with `--fail-on-orphans`.

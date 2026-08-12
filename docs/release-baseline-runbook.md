# Release baseline runbook

## Quality gates

```bash
python scripts/check_repo_hygiene.py
python scripts/compile_proto.py
python scripts/check_migrations.py
python -m ruff check server agent analyzer
python -m pytest -q
cd web
npm ci
npm run audit:prod
npm run lint
npm test
npm run build
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

## Native release package

Build `web/dist` first, then create a release archive with the repository script:

```bash
make package-native RELEASE_NAME=mini-drop-release-YYYYMMDD-name
```

The command refuses dirty worktrees and existing output archives, packages only files from
the current Git `HEAD` plus the built `web/dist`, disables macOS AppleDouble resource-fork
members, scans the final member list, and prints the SHA-256 used for node-to-node verification.
Runtime `.venv` and the protected environment file are copied from the previous release on
each node; they must never be included in the archive.

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

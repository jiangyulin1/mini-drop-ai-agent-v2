# Release baseline runbook

## Quality gates

```bash
python scripts/check_repo_hygiene.py
python scripts/compile_proto.py
python scripts/check_migrations.py
python -m pytest -q
cd web
npm ci
npm run audit:prod
npm run lint
npm test
npm run build
```

## PostgreSQL migration

Set `DATABASE_URL` to the target PostgreSQL database and run:

```bash
alembic current
alembic upgrade head
alembic check
```

Application startup also runs `upgrade head`. A pre-Alembic database is stamped at
`0001_baseline` and receives the conditional `0002_release` compatibility migration.

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

# Mini-Drop release baseline validation

Date: 2026-07-29
Scope: engineering quality gates, versioned schema migration, credential hygiene,
PostgreSQL/MinIO backup and restore rehearsal.

## Local quality gates

| Gate | Result |
|---|---|
| Repository hygiene | passed |
| Protobuf compilation | passed |
| Alembic schema drift | passed |
| Python tests | 391 passed |
| Web ESLint | passed, zero warnings |
| Web component tests | 4 passed |
| Web production dependency gate | passed with the documented client-only React Router exception |
| Web production build | passed |

## Three-node environment

- Control Server, Nginx and both Worker Agents remained active during validation.
- The existing SQLite/Moto deployment was not overwritten.
- PostgreSQL 16.14 and MinIO `RELEASE.2025-09-07T16-13-09Z` were started as an
  isolated validation stack.
- A live SQLite online backup was upgraded from a pre-Alembic schema to
  `0002_release (head)`.
- A fresh PostgreSQL database executed `0001_baseline -> 0002_release`.

## Backup, restore and object reconciliation

The rehearsal seeded one checksum-bearing artifact, then:

1. created a PostgreSQL custom-format dump;
2. created a MinIO snapshot with a SHA-256 manifest;
3. restored into a separate database and bucket;
4. compared row counts across all 19 application tables;
5. verified the restored Alembic revision;
6. reconciled SQL artifact metadata with MinIO size and SHA-256.

Result: one artifact checked, zero missing objects, zero mismatches and zero orphans.

## Operational notes

- Docker Hub was unreachable from the lab network, so PostgreSQL used the configured
  Ubuntu/Alibaba package mirror and MinIO used the official fixed binary.
- The previously documented SSH password was removed from workspace documentation.
  Password rotation remains an operator action because changing it without coordinating
  the replacement login method could lock users out of the lab.

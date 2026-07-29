# Database migrations

Alembic is the schema source of truth for SQLite and PostgreSQL deployments.

```bash
alembic current
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

On the first startup of a legacy Mini-Drop database, the application recognizes the
pre-Alembic tables, stamps the baseline revision, and then applies the idempotent legacy
upgrade revision. New databases execute the complete migration history.

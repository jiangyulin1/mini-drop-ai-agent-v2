"""Versioned database migration entry point."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect


BASELINE_REVISION = "0001_baseline"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config(engine: Engine) -> Config:
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    config.attributes["engine"] = engine
    return config


def upgrade_database(engine: Engine) -> None:
    """Upgrade a new or legacy database to the latest schema revision."""

    config = _config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        user_tables = tables - {"alembic_version"}
        if user_tables and "alembic_version" not in tables:
            # Legacy releases created tables directly from SQLAlchemy metadata.
            # The baseline models that historical schema; the following revision
            # performs conditional additions needed by pre-v2 installations.
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")

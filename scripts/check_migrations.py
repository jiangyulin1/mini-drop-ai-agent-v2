"""Verify that Alembic head matches SQLAlchemy metadata on a fresh database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="mini-drop-migration-") as work:
        database = Path(work) / "schema.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
        config = Config(str(project_root / "alembic.ini"))
        heads = ScriptDirectory.from_config(config).get_heads()
        too_long = [revision for revision in heads if len(revision) > 32]
        if too_long:
            raise RuntimeError(
                "Alembic revision exceeds legacy PostgreSQL VARCHAR(32): "
                + ", ".join(too_long)
            )
        command.upgrade(config, "head")
        command.check(config)
    print("Alembic schema drift check passed.")


if __name__ == "__main__":
    main()

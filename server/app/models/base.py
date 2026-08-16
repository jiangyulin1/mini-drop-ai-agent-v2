"""SQLAlchemy ORM models, split by bounded context.

This package re-exports every model name so existing imports from
``server.app.models`` continue to work unchanged.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


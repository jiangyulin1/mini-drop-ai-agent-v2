"""Explicit SQLAlchemy unit-of-work transaction boundary."""

from __future__ import annotations

from types import TracebackType
from typing import Callable, Protocol, Type

from sqlalchemy.orm import Session


class Lock(Protocol):
    def acquire(self) -> bool: ...
    def release(self) -> None: ...


class SqlAlchemyUnitOfWork:
    """Own one session and publish callbacks only after a successful commit."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        lock: Lock,
        cache_invalidator: Callable[[], None],
        pre_commit_validator: Callable[[Session], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lock = lock
        self._cache_invalidator = cache_invalidator
        self._pre_commit_validator = pre_commit_validator
        self.session: Session | None = None

    def __enter__(self) -> Session:
        self._lock.acquire()
        try:
            self.session = self._session_factory()
        except Exception:
            self._lock.release()
            raise
        return self.session

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self.session is None:
            self._lock.release()
            return False
        callbacks: list[Callable[[], None]] = []
        committed = False
        try:
            if exc_type is None:
                if self._pre_commit_validator is not None:
                    self._pre_commit_validator(self.session)
                self.session.commit()
                committed = True
                callbacks = list(
                    self.session.info.get("_post_commit_notifications", []),
                )
            else:
                self.session.rollback()
        except Exception:
            self.session.rollback()
            raise
        finally:
            self.session.close()
            if committed:
                self._cache_invalidator()
            self._lock.release()
        for callback in callbacks:
            callback()
        return False

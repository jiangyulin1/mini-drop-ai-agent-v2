from __future__ import annotations

import threading

import pytest

from server.app.persistence.uow import SqlAlchemyUnitOfWork


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.info = {}
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def make_uow(session, invalidated):
    return SqlAlchemyUnitOfWork(
        lambda: session,
        threading.RLock(),
        lambda: invalidated.append(True),
    )


def test_uow_publishes_callbacks_only_after_commit():
    session = FakeSession()
    published = []
    invalidated = []
    with make_uow(session, invalidated) as active:
        active.info["_post_commit_notifications"] = [lambda: published.append(True)]
    assert (session.commits, session.rollbacks, session.closes) == (1, 0, 1)
    assert invalidated == [True]
    assert published == [True]


def test_uow_rolls_back_without_callbacks_or_cache_invalidation():
    session = FakeSession()
    published = []
    invalidated = []
    with pytest.raises(RuntimeError, match="abort"):
        with make_uow(session, invalidated) as active:
            active.info["_post_commit_notifications"] = [lambda: published.append(True)]
            raise RuntimeError("abort")
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)
    assert invalidated == []
    assert published == []


def test_uow_rolls_back_when_commit_fails():
    session = FakeSession(commit_error=RuntimeError("commit failed"))
    with pytest.raises(RuntimeError, match="commit failed"):
        with make_uow(session, []):
            pass
    assert session.rollbacks == 1
    assert session.closes == 1

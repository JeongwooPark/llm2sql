from __future__ import annotations

import time

import pytest

from txt2sql.security.errors import UserInputError
from txt2sql.security.sessions import SessionStore, is_valid_session_id


def test_session_id_format() -> None:
    assert is_valid_session_id("abcd1234")
    assert not is_valid_session_id("short")
    assert not is_valid_session_id("g" * 16)
    assert not is_valid_session_id("x" * 200)


def test_unknown_session_not_created() -> None:
    store = SessionStore(factory=dict)
    with pytest.raises(UserInputError) as exc:
        store.require("deadbeefdeadbeef")
    assert exc.value.code == "session_not_found"
    assert len(store) == 0


def test_ttl_and_max_sessions() -> None:
    store = SessionStore(ttl_seconds=60, max_sessions=2, factory=dict)
    sid1, _ = store.create(owner="a")
    sid2, _ = store.create(owner="a")
    assert len(store) == 2
    store.create(owner="a")
    assert len(store) == 2
    assert store.get(sid1) is None or store.get(sid2) is None


def test_ownership_isolation() -> None:
    store = SessionStore(factory=dict)
    sid, _ = store.create(owner="alice")
    assert store.get(sid, owner="bob") is None
    assert store.get(sid, owner="alice") is not None


def test_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SessionStore(ttl_seconds=60, max_sessions=10, factory=dict)
    sid, _ = store.create(owner="a")
    entry = store._items[sid]
    entry.last_access = time.monotonic() - 120
    assert store.get(sid) is None

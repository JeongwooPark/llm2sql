"""TTL·상한을 가진 세션 저장소."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from txt2sql.security.errors import UserInputError

T = TypeVar("T")

_SESSION_ID_RE_LEN = (8, 64)


def is_valid_session_id(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    text = value.strip().lower()
    if not ( _SESSION_ID_RE_LEN[0] <= len(text) <= _SESSION_ID_RE_LEN[1]):
        return False
    return all(ch in "0123456789abcdef" for ch in text)


def new_session_id() -> str:
    return uuid.uuid4().hex


@dataclass
class _Entry(Generic[T]):
    value: T
    owner: str
    created_at: float
    last_access: float
    meta: dict[str, Any] = field(default_factory=dict)


class SessionStore(Generic[T]):
    """In-memory sessions with TTL and max cardinality (LRU eviction)."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        max_sessions: int = 200,
        factory: Any = None,
    ) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self._factory = factory
        self._items: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._lock = threading.RLock()

    def create(self, *, owner: str = "local", value: T | None = None) -> tuple[str, T]:
        with self._lock:
            self._purge_locked()
            while len(self._items) >= self.max_sessions:
                self._items.popitem(last=False)
            sid = new_session_id()
            obj = value if value is not None else self._factory()
            now = time.monotonic()
            self._items[sid] = _Entry(
                value=obj, owner=owner, created_at=now, last_access=now
            )
            return sid, obj

    def get(self, session_id: str, *, owner: str | None = None) -> T | None:
        with self._lock:
            self._purge_locked()
            if not is_valid_session_id(session_id):
                return None
            entry = self._items.get(session_id)
            if entry is None:
                return None
            if owner is not None and entry.owner != owner and entry.owner != "local":
                # Ownership check for token mode subjects
                if owner != entry.owner:
                    return None
            entry.last_access = time.monotonic()
            self._items.move_to_end(session_id)
            return entry.value

    def require(self, session_id: str | None, *, owner: str | None = None) -> T:
        """Return existing session or raise. Does not create unknown IDs."""
        if not session_id:
            raise UserInputError("session_id가 필요합니다.", code="session_required")
        if not is_valid_session_id(session_id):
            raise UserInputError("잘못된 session_id입니다.", code="session_invalid")
        found = self.get(session_id, owner=owner)
        if found is None:
            raise UserInputError(
                "세션을 찾을 수 없습니다.", code="session_not_found"
            )
        return found

    def get_or_none(self, session_id: str | None) -> T | None:
        if not session_id or not is_valid_session_id(session_id):
            return None
        return self.get(session_id)

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)

    def owner_of(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._items.get(session_id)
            return entry.owner if entry else None

    def bind_meta(self, session_id: str, **kwargs: Any) -> None:
        with self._lock:
            entry = self._items.get(session_id)
            if entry is None:
                return
            entry.meta.update(kwargs)

    def meta(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._items.get(session_id)
            return dict(entry.meta) if entry else {}

    def __len__(self) -> int:
        with self._lock:
            self._purge_locked()
            return len(self._items)

    def _purge_locked(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, entry in self._items.items()
            if now - entry.last_access > self.ttl_seconds
        ]
        for key in expired:
            self._items.pop(key, None)

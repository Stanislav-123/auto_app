from __future__ import annotations

import asyncio

_LOCKS: dict[str, asyncio.Lock] = {}


def user_lock(user_id: int | str) -> asyncio.Lock:
    """Один запуск завода на пользователя, чтобы двойной клик не дублировал шаги."""
    key = str(user_id)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock

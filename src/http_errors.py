from __future__ import annotations

import urllib.error


def transport_failure(exc: BaseException) -> str | None:
    """Сообщение для сетевых сбоев, которые не являются HTTP-ответом."""
    if isinstance(exc, urllib.error.HTTPError):
        return None
    if isinstance(exc, TimeoutError):
        return "Таймаут запроса"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "Таймаут запроса"
        return f"Не удалось подключиться: {reason}"
    return None

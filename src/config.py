from __future__ import annotations

import os


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    """ALLOWED_USER_IDS=111,222. Пустая строка — никого не пускать."""
    if not raw or not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError as exc:
            raise ValueError(f"Некорректный ALLOWED_USER_IDS: {item}") from exc
    return ids


def allowed_user_ids() -> set[int]:
    return parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS"))

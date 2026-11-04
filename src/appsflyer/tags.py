"""Выбор поля App ID в блоке extra-params. Подпись не обязана содержать Android или iOS."""

from __future__ import annotations

import re

_APP_ID = re.compile(r"app\s*id", re.IGNORECASE)
_ANDROID = re.compile(r"android", re.IGNORECASE)
_IOS = re.compile(r"\bios\b", re.IGNORECASE)

TAG_COMMIT_KEYS = ("Enter", ",", "Tab")


def choose_app_id_index(
    labels: list[str],
    classes: list[str] | None = None,
    *,
    platform: str | None = None,
) -> int | None:
    """Индекс .af-tag-input-wrapper. None — поля нет и их несколько разных."""
    classes = list(classes or [])
    while len(classes) < len(labels):
        classes.append("")

    def is_candidate(index: int) -> bool:
        label = labels[index] or ""
        kind = classes[index] or ""
        return bool(_APP_ID.search(label) or "app_id" in kind.lower())

    candidates = [index for index in range(len(labels)) if is_candidate(index)]
    if not candidates:
        if len(labels) == 1:
            return 0
        return None

    def rank(index: int) -> tuple[int, int, int]:
        text = f"{labels[index]} {classes[index]}".lower()
        if platform == "android" and _ANDROID.search(text):
            platform_rank = 0
        elif platform == "ios" and _IOS.search(text):
            platform_rank = 0
        elif not _ANDROID.search(text) and not _IOS.search(text):
            platform_rank = 1
        else:
            platform_rank = 2
        class_rank = 0 if "app_id" in (classes[index] or "").lower() else 1
        return (platform_rank, class_rank, index)

    return sorted(candidates, key=rank)[0]


def tag_already_present(existing: list[str], value: str) -> bool:
    wanted = (value or "").strip().lower()
    if not wanted:
        return False
    return any((item or "").strip().lower() == wanted for item in existing)

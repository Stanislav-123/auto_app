from __future__ import annotations

import re
from typing import Any


def extract_app_command_payload(text: str | None) -> str:
    """Текст после /app. Переносы строк сохраняются. Пустая строка — команда без данных."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = raw.splitlines()
    head = lines[0].strip()
    match = re.match(r"^/app(?:@[\w]+)?(?:\s+(.*))?$", head, re.IGNORECASE)
    if not match:
        return ""
    first = (match.group(1) or "").strip()
    tail = "\n".join(lines[1:]).strip()
    if first and tail:
        return f"{first}\n{tail}"
    return first or tail


def workflow_reply_kind(wf: dict[str, Any] | None) -> str | None:
    """Какой ответ ждёт активный завод. None — обычное сообщение завод не запускает."""
    if not wf:
        return None
    if wf.get("crm_collecting") or wf.get("crm_awaiting_theme") or wf.get("crm_ready"):
        return "crm"
    if wf.get("awaiting_android_fb"):
        return "android_fb"
    if wf.get("awaiting_domain"):
        return "domain"
    if wf.get("awaiting_missing") and wf.get("parsed"):
        return "missing"
    if wf.get("awaiting_app"):
        return "app"
    return None

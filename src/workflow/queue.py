from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from html import escape
from typing import Any

from src.workflow.persist import app_key_from_wf, is_workflow_complete

USER_DATA_QUEUE_KEY = "app_queue"
QUEUE_STORE_KEY = "app-queue"


@dataclass
class EnqueueResult:
    status: str
    position: int
    item: dict[str, Any]
    current_label: str
    total: int


def preview_label(wf: dict[str, Any] | None) -> str:
    """Короткое имя приложения для сообщений очереди."""
    if not wf:
        return "приложение"
    if wf.get("label"):
        return str(wf["label"])
    parsed = wf.get("parsed") if isinstance(wf.get("parsed"), dict) else {}
    name = parsed.get("name") or parsed.get("store_id") or parsed.get("bundle") or "приложение"
    platform = parsed.get("platform")
    if platform:
        return f"{name} ({platform})"
    return str(name)


def is_factory_active(wf: dict[str, Any] | None) -> bool:
    """True, если у пользователя уже идёт завод и его нельзя затирать новым /app."""
    if not wf:
        return False
    if is_workflow_complete(wf):
        return False
    if wf.get("parsed"):
        return True
    if wf.get("crm_collecting") or wf.get("crm_awaiting_theme") or wf.get("crm_ready"):
        return True
    if wf.get("awaiting_missing") or wf.get("awaiting_domain") or wf.get("awaiting_android_fb"):
        return True
    if wf.get("failed_step"):
        return True
    return False


def get_queue(user_data: dict[str, Any]) -> list[dict[str, Any]]:
    items = user_data.get(USER_DATA_QUEUE_KEY)
    if not isinstance(items, list):
        items = []
        user_data[USER_DATA_QUEUE_KEY] = items
    return items


def queue_size(user_data: dict[str, Any]) -> int:
    return len(get_queue(user_data))


def make_queue_item(wf_snapshot: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(wf_snapshot)
    return {
        "id": uuid.uuid4().hex[:8],
        "app_key": app_key_from_wf(workflow),
        "label": preview_label(workflow),
        "workflow": workflow,
    }


def find_queue_position(user_data: dict[str, Any], app_key: str | None) -> int | None:
    if not app_key:
        return None
    for index, item in enumerate(get_queue(user_data), start=1):
        if item.get("app_key") == app_key:
            return index
    return None


def enqueue(user_data: dict[str, Any], item: dict[str, Any]) -> int:
    """Добавляет элемент в конец очереди и возвращает позицию (с 1)."""
    items = get_queue(user_data)
    items.append(item)
    return len(items)


def dequeue(user_data: dict[str, Any]) -> dict[str, Any] | None:
    items = get_queue(user_data)
    if not items:
        return None
    return items.pop(0)


def clear_queue(user_data: dict[str, Any]) -> int:
    items = get_queue(user_data)
    count = len(items)
    items.clear()
    return count


def enqueue_workflow(
    user_data: dict[str, Any],
    wf_snapshot: dict[str, Any],
    *,
    current_wf: dict[str, Any] | None = None,
) -> EnqueueResult:
    """Кладёт изолированный снимок завода в очередь, не трогая текущий workflow."""
    item = make_queue_item(wf_snapshot)
    current_label = preview_label(current_wf)
    app_key = item.get("app_key")
    current_key = app_key_from_wf(current_wf or {})
    if app_key and current_key and app_key == current_key:
        return EnqueueResult(
            status="duplicate_current",
            position=0,
            item=item,
            current_label=current_label,
            total=queue_size(user_data),
        )
    existing = find_queue_position(user_data, app_key)
    if existing:
        return EnqueueResult(
            status="duplicate_queue",
            position=existing,
            item=item,
            current_label=current_label,
            total=queue_size(user_data),
        )
    position = enqueue(user_data, item)
    return EnqueueResult(
        status="queued",
        position=position,
        item=item,
        current_label=current_label,
        total=position,
    )


def start_next(user_data: dict[str, Any]) -> dict[str, Any] | None:
    """Достаёт следующий снимок завода или None, если очередь пуста."""
    item = dequeue(user_data)
    if item is None:
        return None
    workflow = item.get("workflow")
    if not isinstance(workflow, dict):
        return None
    return copy.deepcopy(workflow)


def busy_factory_prompt(current_label: str, queued: int) -> str:
    extra = f"\nВ очереди уже: {queued}." if queued else ""
    return (
        f"Сейчас заводится <b>{escape(current_label)}</b>.{extra}\n\n"
        "Чтобы поставить ещё одно в очередь, отправьте <code>/app</code> "
        "вместе с текстом приложения.\n"
        "/cancel — отменить текущее и взять следующее из очереди\n"
        "/clear_queue — очистить очередь"
    )


def format_enqueue_result(result: EnqueueResult) -> str:
    label = escape(str(result.item.get("label") or "приложение"))
    if result.status == "duplicate_current":
        return f"Это приложение (<b>{label}</b>) уже заводится."
    if result.status == "duplicate_queue":
        return f"Это приложение (<b>{label}</b>) уже в очереди, позиция {result.position}."
    return (
        f"📋 Приложение <b>{label}</b> в очереди, позиция {result.position}.\n"
        f"Сейчас заводится: <b>{escape(result.current_label)}</b>.\n"
        f"В очереди: {result.total}.\n\n"
        "Когда текущее закончится или его отменят, сразу возьму следующее.\n"
        "/cancel — отменить текущее и стартовать очередь\n"
        "/clear_queue — очистить очередь"
    )


def format_start_next(label: str, remaining: int) -> str:
    extra = f"\nЕщё в очереди: {remaining}." if remaining else "\nОчередь больше пуста."
    return f"▶️ Стартую следующую из очереди: <b>{escape(label)}</b>{extra}"


def format_cancelled(label: str) -> str:
    return f"⛔ Отменил текущее: <b>{escape(label)}</b>."


def format_queue_cleared(count: int) -> str:
    if count:
        return f"Очередь очищена: снято {count}. Текущее заведение не трогал."
    return "Очередь и так пуста."


def format_nothing_to_cancel() -> str:
    return "Нечего отменять. Очередь пуста."

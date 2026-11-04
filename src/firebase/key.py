"""FCM ключ: JSON сервисного аккаунта Firebase. Содержимое ключа нигде не логируем."""

from __future__ import annotations

import json
import re

REQUIRED_FIELDS = ("type", "project_id", "private_key", "client_email")

_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_INVITE_RE = re.compile(
    r"https://(?:console\.cloud\.google\.com/invitation\?[^\s\"'<>]+"
    r"|console\.firebase\.google\.com/[^\s\"'<>]*invit[^\s\"'<>]*)",
    re.IGNORECASE,
)


class FcmKeyError(Exception):
    """Не удалось получить ключ. screenshot — путь к скриншоту, если есть."""

    def __init__(self, message: str, screenshot: str | None = None) -> None:
        super().__init__(message)
        self.screenshot = screenshot


def parse_service_account(content: str | bytes) -> dict:
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    try:
        data = json.loads((content or "").strip())
    except json.JSONDecodeError as exc:
        raise ValueError("FCM JSON некорректен") from exc
    if not isinstance(data, dict):
        raise ValueError("FCM JSON должен быть объектом")
    missing = [key for key in REQUIRED_FIELDS if not str(data.get(key) or "").strip()]
    if missing:
        raise ValueError(f"В FCM JSON не хватает полей: {', '.join(missing)}")
    if data["type"] != "service_account":
        raise ValueError("FCM JSON должен быть ключом сервисного аккаунта (type: service_account)")
    if "PRIVATE KEY" not in data["private_key"]:
        raise ValueError("В FCM JSON private_key не похож на ключ")
    return data


def fcm_key_string(data: dict | None) -> str:
    """Строка для поля fcm_key в CRM: весь JSON целиком, или "0" если ключа нет."""
    if not data:
        return "0"
    return json.dumps(data, ensure_ascii=False)


def describe_key(data: dict) -> str:
    """Безопасное описание для чата и логов: без private_key."""
    return f"{data.get('client_email', '?')} (проект {data.get('project_id', '?')})"


def project_ref(firebase_name: str | None) -> tuple[str | None, str | None]:
    """(project_id, display_name). Если похоже на id проекта — идём по id, иначе ищем по названию."""
    raw = (firebase_name or "").strip()
    if not raw:
        return None, None
    match = re.search(r"/project/([a-z][a-z0-9-]{4,28}[a-z0-9])", raw)
    if match:
        return match.group(1), None
    if _PROJECT_ID_RE.match(raw):
        return raw, raw
    return None, raw


def split_firebase_keys(raw: str | None) -> list[str]:
    """«combo, reflex» -> ["combo", "reflex"]. Порядок как в сообщении."""
    parts = [item.strip().strip("\"'`«»") for item in re.split(r"[,;\s]+", raw or "")]
    return [item for item in parts if item]


def split_domain(domain: str) -> tuple[str, str]:
    """Две непустые части около середины, склейка даёт домен."""
    value = (domain or "").strip()
    if len(value) < 2:
        raise ValueError("Домен слишком короткий, чтобы разбить на две части")
    middle = len(value) // 2
    return value[:middle], value[middle:]


def rtdb_values(keys: list[str], domain: str) -> dict[str, str]:
    if len(keys) != 2:
        raise ValueError(f"Нужно ровно два ключа firebase, а пришло: {', '.join(keys) or 'ничего'}")
    first, second = split_domain(domain)
    return {keys[0]: first, keys[1]: second}


def rtdb_changes(current: dict[str, str | None], wanted: dict[str, str]) -> dict[str, str]:
    """Что поменять в базе. Пусто — всё уже стоит."""
    return {key: value for key, value in wanted.items() if _rtdb_text(current.get(key)) != value}


def _rtdb_text(value) -> str | None:
    if value is None:
        return None
    return str(value).strip().strip('"')


def extract_invite_links(text: str) -> list[str]:
    seen: list[str] = []
    for link in _INVITE_RE.findall(text or ""):
        link = link.replace("&amp;", "&").rstrip(").,")
        if link not in seen:
            seen.append(link)
    return seen

from __future__ import annotations

import re
from typing import Any

# Поля экрана «Настройки бэкенда» (POST /app/update). Лишний мусор с view не тащим.
BACKEND_SETTINGS_FIELDS = (
    "apps_name",
    "app_domain",
    "appsflyer_dev_key",
    "facebook_access_token",
    "facebook_decryption_key",
    "filters_geo_allowed",
    "filters_vpn_or_proxy",
    "filters_time_zone",
    "attribution_facebook",
    "attribution_appsflyer",
    "attribution_ASA",
)

def normalize_backend_domain(value: str | None) -> str | None:
    """Достать hostname из строки с протоколом, путём, портом или хвостом-комментарием."""
    if not value:
        return None

    domain = str(value).strip()
    if not domain:
        return None

    domain = re.sub(r"^https?://", "", domain, flags=re.IGNORECASE)
    domain = domain.split("/")[0].strip().rstrip(".")
    domain = domain.split()[0].strip(".,;()[]<>\"'")
    if ":" in domain:
        host, _, port = domain.partition(":")
        if port.isdigit():
            domain = host
    domain = domain.strip(".").lower()
    if not domain or " " in domain or "." not in domain:
        return None
    if any(ch in domain for ch in "/\\@,;"):
        return None
    return domain


def unwrap_record(data: Any) -> dict[str, Any]:
    """Достать объект из {model} / {data.model} / плоского ответа."""
    if not isinstance(data, dict):
        return {}
    model = data.get("model")
    if isinstance(model, dict):
        return model
    nested = data.get("data")
    if isinstance(nested, dict):
        inner = nested.get("model")
        if isinstance(inner, dict):
            return inner
        return nested
    return data


def build_app_domain_payload(view: Any, domain: str) -> dict[str, Any]:
    """Тело POST /app/update — как форма «Настройки бэкенда», домен в app_domain."""
    source = unwrap_record(view)
    payload: dict[str, Any] = {}
    for key in BACKEND_SETTINGS_FIELDS:
        if key in source:
            payload[key] = source[key]
    payload["app_domain"] = domain
    return payload


def build_rules_domain_payload(view: Any, app_id: int, domain: str) -> dict[str, Any]:
    """Тело POST /app/rules-update: полная модель правил и hostname в domain."""
    model = dict(unwrap_record(view))
    model.pop("errors", None)
    model.pop("error", None)
    model.pop("msg_error", None)
    model["domain"] = domain
    model["apps_id"] = app_id
    if "domain_id" in model and model.get("domain_id") in (None, "", 0, "0"):
        model.pop("domain_id", None)
    return model



def is_rules_domain_missing_soft_error(result: Any) -> bool:
    """CRM rules-view часто отвечает error+msg про пустой domain, но model уже есть.

    Это не отказ: портал просит сохранить домен в правилах. Бот как раз и шлёт
    rules-update следом, поэтому soft-error нельзя рвать цепочку.
    """
    if not isinstance(result, dict):
        return False
    model = result.get("model")
    if not isinstance(model, dict):
        return False
    if not (result.get("error") or result.get("success") is False):
        return False
    text = str(result.get("msg_error") or result.get("message") or "").lower()
    return "не указан домен" in text or "укажите домен" in text


def crm_result_error(result: Any) -> str | None:
    """Текст ошибки из JSON-тела, если CRM отказала без HTTP-ошибки."""
    if not isinstance(result, dict):
        return None
    if result.get("errors"):
        return _stringify_errors(result["errors"])
    if result.get("error"):
        return str(result.get("msg_error") or result.get("message") or result.get("error"))
    if result.get("success") is False:
        return str(result.get("msg_error") or result.get("message") or "CRM отклонила запрос")
    return None


def is_domain_not_found(message: str) -> bool:
    text = (message or "").lower()
    return "домен не найден" in text or "domain not found" in text or "domain is not found" in text


def _stringify_errors(errors: Any) -> str:
    if isinstance(errors, dict):
        parts: list[str] = []
        for key, value in errors.items():
            if isinstance(value, list):
                parts.append(f"{key}: {', '.join(str(item) for item in value)}")
            else:
                parts.append(f"{key}: {value}")
        return "; ".join(parts)
    return str(errors)

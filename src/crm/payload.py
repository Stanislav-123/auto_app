from __future__ import annotations

import json
import re
from typing import Any, Optional

from src.models import ParsedApp, Platform

CRM_PLATFORM_IOS = 1
CRM_PLATFORM_ANDROID = 0

CREATED_BY_ZM_APPS = "zm-apps-team"
CREATED_BY_APP_SOLUTIONS = "app-solutions"
CREATED_BY_SIDE_TEAM = "side-team"


def resolve_apps_created_by(owner: Optional[str]) -> str:
    """
    Map «Чья прила» to CRM apps_created_by value.

    - ZM Apps / ZMApps -> zm-apps-team
    - App Solutions / AppSolution -> app-solutions
    - сторонний разработчик / side -> side-team
    """
    if not owner:
        raise ValueError('Не указано поле «Чья прила» — нужно для apps_created_by')

    normalized = re.sub(r"\s+", " ", owner.strip().lower())
    compact = normalized.replace(" ", "").replace("-", "").replace("_", "")

    if "zmapps" in compact or "zm apps" in normalized:
        return CREATED_BY_ZM_APPS
    if "appsolution" in compact:
        return CREATED_BY_APP_SOLUTIONS
    if "сторонн" in normalized or "side" in compact:
        return CREATED_BY_SIDE_TEAM

    raise ValueError(
        f'Неизвестное значение «Чья прила»: {owner}. '
        "Ожидается ZMApps / App Solutions / Сторонний разработчик"
    )


def build_create_app_payload(
    parsed: ParsedApp,
    *,
    logo: Optional[str] = None,
    banner: Optional[str] = None,
    banner_appcraft: Optional[str] = None,
    slider: Optional[str] = None,
    apns_key: Optional[dict[str, str]] = None,
    apps_status: int = 2,
    apps_created_by: Optional[str] = None,
    theme_id: Optional[int | str] = None,
    apps_general_application: int = 0,
    apps_in_house_general: int = 0,
    is_private: bool = True,
) -> dict[str, Any]:
    if parsed.platform == Platform.IOS:
        platform_id = CRM_PLATFORM_IOS
    elif parsed.platform == Platform.ANDROID:
        platform_id = CRM_PLATFORM_ANDROID
    else:
        raise ValueError("Неизвестная платформа для CRM")

    if not parsed.name or not parsed.store_id or not parsed.bundle:
        raise ValueError("Не хватает данных приложения для CRM")

    created_by = apps_created_by or resolve_apps_created_by(parsed.owner)
    resolved_theme_id = None if theme_id in (None, "", 0, "0") else str(theme_id)

    payload: dict[str, Any] = {
        "apps_name": parsed.name,
        "apps_bundle": parsed.store_id,
        "bundle": parsed.bundle,
        "apps_fb_id": None,
        "apps_status": apps_status,
        "platform": platform_id,
        "theme_id": resolved_theme_id,
        "apps_push_os_id": "0",
        "apps_push_os_id_dop": "0",
        "apps_bm_fb_id": "0",
        "apps_cookie_fb": "0",
        "apps_proxy_fb": "0",
        "apps_google_profile": "0",
        "apps_sharing_status": None,
        "apps_push_status": None,
        "apps_sharing_profile": None,
        "apps_push_profile": None,
        "apps_created_by": created_by,
        "apps_general_application": apps_general_application,
        "apps_in_house_general": apps_in_house_general,
        "apps_link_status": 1 if parsed.platform == Platform.IOS else 0,
        "fcm_key": "0",
        "apns_key": 0,
        "is_appsflyer": bool(parsed.appsflyer),
        "is_private": 1 if is_private else 0,
        "tenjin_api_key": None,
        "logo": logo,
        "banner": banner,
        "banner_appcraft": banner_appcraft,
        "slider": slider,
    }

    if apns_key is not None:
        payload["apns_key"] = json.dumps(apns_key, ensure_ascii=False)

    return payload

from __future__ import annotations

from html import escape

from .models import ParsedApp, Platform

FIELD_LABELS: dict[str, str] = {
    "platform": "Платформа",
    "name": "Название",
    "store_url": "Ссылка на стор",
    "store_id": "Store ID",
    "bundle": "Bundle",
    "backend_domain": "Backend Domain",
    "appsflyer": "Appsflyer",
    "owner": "Чья прила",
    "pushes": "Пуши",
    "developer_site": "Сайт разработчика",
    "backend": "Backend",
    "firebase_name": "Название в Firebase",
}

PLATFORM_EMOJI = {
    Platform.IOS: "🍏 iOS",
    Platform.ANDROID: "🤖 Android",
    Platform.UNKNOWN: "❓ Неизвестно",
}


def _format_value(key: str, value) -> str:
    if value is None:
        return "—"
    if key == "appsflyer" and isinstance(value, bool):
        return "есть" if value else "нет"
    if key == "platform":
        return PLATFORM_EMOJI.get(value, str(value))
    return str(value)


def format_parsed_app(app: ParsedApp) -> str:
    lines: list[str] = []

    if app.platform == Platform.UNKNOWN:
        lines.append("⚠️ <b>Не удалось определить платформу</b>")
        lines.append("Ожидается заголовок: «Новое IOS приложение» или «Новое Android приложение»")
        lines.append("")
    else:
        lines.append(f"✅ <b>Распознано: {PLATFORM_EMOJI[app.platform]}</b>")
        lines.append("")

    if app.platform == Platform.IOS:
        field_order = [
            "name", "store_url", "store_id", "bundle",
            "backend_domain", "appsflyer", "owner", "pushes",
            "developer_site", "backend",
        ]
    elif app.platform == Platform.ANDROID:
        field_order = [
            "name", "store_url", "store_id", "bundle",
            "appsflyer", "firebase_name", "owner",
        ]
    else:
        field_order = list(FIELD_LABELS.keys())

    for key in field_order:
        value = app.platform if key == "platform" else getattr(app, key, None)
        if value is None:
            continue
        label = FIELD_LABELS.get(key, key)
        formatted = escape(_format_value(key, value))
        lines.append(f"<b>{label}:</b> {formatted}")

    if app.backend_url:
        lines.append(f'<b>Ссылка на бэкенд:</b> <a href="{escape(app.backend_url)}">{escape(app.backend_url)}</a>')

    if app.missing_fields:
        lines.append("")
        missing_labels = [FIELD_LABELS.get(f, f) for f in app.missing_fields]
        lines.append(f"⚠️ <b>Не хватает данных:</b> {escape(', '.join(missing_labels))}")

    return "\n".join(lines)


MISSING_HINTS: dict[str, str] = {
    "platform": "платформу — напишите <code>iOS</code> или <code>Android</code>",
    "name": "название приложения",
    "store_url": "ссылку на App Store / Google Play",
    "store_id": "Store ID",
    "bundle": "Bundle (например: <code>Bundle: com.example.app</code>)",
}


def format_missing_prompt(app: ParsedApp) -> str:
    if not app.missing_fields and app.platform != Platform.UNKNOWN:
        return ""

    missing = list(app.missing_fields)
    if app.platform == Platform.UNKNOWN and "platform" not in missing:
        missing = ["platform", *missing]

    lines = [
        "⚠️ <b>Не хватает данных для продолжения.</b>",
        "Пришлите недостающее:",
        "",
    ]
    for field in missing:
        hint = MISSING_HINTS.get(field, FIELD_LABELS.get(field, field))
        lines.append(f"• {hint}")

    lines.append("")
    lines.append("Можно прислать только недостающее или полное сообщение целиком.")
    return "\n".join(lines)


def is_parse_complete(app: ParsedApp) -> bool:
    return app.platform != Platform.UNKNOWN and not app.missing_fields

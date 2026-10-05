from __future__ import annotations

import re
from typing import Optional

from .models import ParsedApp, Platform

# --- URL patterns ---

IOS_STORE_URL_RE = re.compile(
    r"https?://apps\.apple\.com/[^\s]+/id(\d+)",
    re.IGNORECASE,
)
ANDROID_STORE_URL_RE = re.compile(
    r"https?://play\.google\.com/store/apps/details\?id=([^\s&]+)",
    re.IGNORECASE,
)
ANY_URL_RE = re.compile(r"https?://\S+")

# --- Labeled field patterns (label : value) ---

FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bundle", re.compile(r"^Bundle\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE)),
    (
        "backend_domain",
        re.compile(r"^Backend\s+Domain\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "appsflyer",
        re.compile(r"^Appsflyer\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "owner",
        re.compile(r"^Чья\s+прила\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "pushes",
        re.compile(r"^Пуши\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "developer_site",
        re.compile(
            r"^Сайт\s+разработчика\s*[:：\-–—]\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "backend",
        re.compile(r"^Backend\s*[:：\-–—]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "firebase_name",
        re.compile(
            r"^Название\s+в\s+Firebase\s*[:：\-–—]\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
]

PLATFORM_HEADER_RE = re.compile(
    r"новое\s+(ios|android|андроид)\s+приложение",
    re.IGNORECASE,
)

IOS_STORE_ID_FROM_URL_RE = re.compile(r"/id(\d+)", re.IGNORECASE)

# Required fields per platform
REQUIRED_FIELDS: dict[Platform, list[str]] = {
    Platform.IOS: ["name", "store_url", "store_id", "bundle"],
    Platform.ANDROID: ["name", "store_url", "store_id", "bundle"],
}


def _normalize_text(text: str) -> str:
    """Normalize dashes and line endings for consistent parsing."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize various dash characters to standard hyphen in labels
    for dash in ("–", "—", "−", "‑"):
        text = text.replace(dash, "-")
    return text.strip()


def _detect_platform(text: str) -> Platform:
    match = PLATFORM_HEADER_RE.search(text)
    if match:
        platform_word = match.group(1).lower()
        if platform_word == "ios":
            return Platform.IOS
        if platform_word in ("android", "андроид"):
            return Platform.ANDROID

    return _detect_platform_from_url(text)


def _detect_platform_from_url(text: str) -> Platform:
    if IOS_STORE_URL_RE.search(text):
        return Platform.IOS
    if ANDROID_STORE_URL_RE.search(text):
        return Platform.ANDROID
    return Platform.UNKNOWN


def _parse_platform_reply(text: str) -> Platform | None:
    """Parse a short user reply that only specifies the platform."""
    normalized = text.strip().lower()
    if normalized in ("ios", "ios приложение", "iphone", "🍏", "apple"):
        return Platform.IOS
    if normalized in ("android", "андроид", "🤖", "google"):
        return Platform.ANDROID
    match = PLATFORM_HEADER_RE.search(text)
    if match:
        word = match.group(1).lower()
        if word == "ios":
            return Platform.IOS
        if word in ("android", "андроид"):
            return Platform.ANDROID
    from_url = _detect_platform_from_url(text)
    if from_url != Platform.UNKNOWN:
        return from_url
    return None


def _apply_platform(app: ParsedApp, platform: Platform) -> ParsedApp:
    app.platform = platform
    if platform == Platform.IOS and app.store_url:
        app.store_id = _extract_store_id_from_url(app.store_url, platform)
    elif platform == Platform.ANDROID:
        if not app.bundle and app.store_url:
            app.bundle = _extract_android_bundle_from_url(app.store_url)
        app.store_id = app.bundle
    app.missing_fields = _collect_missing_fields(app)
    return app


def _parse_bool_ru(value: str) -> Optional[bool]:
    normalized = value.strip().lower()
    if normalized in ("есть", "да", "yes", "true", "+", "имеется"):
        return True
    if normalized in ("нет", "no", "false", "-", "отсутствует"):
        return False
    return None


def _extract_field(text: str, pattern: re.Pattern[str]) -> Optional[str]:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_store_url(text: str) -> Optional[str]:
    match = IOS_STORE_URL_RE.search(text) or ANDROID_STORE_URL_RE.search(text)
    if match:
        return match.group(0)

    match = ANY_URL_RE.search(text)
    return match.group(0) if match else None


def _extract_store_id_from_url(store_url: Optional[str], platform: Platform) -> Optional[str]:
    if not store_url:
        return None

    if platform == Platform.IOS:
        match = IOS_STORE_ID_FROM_URL_RE.search(store_url)
        if match:
            return f"id{match.group(1)}"

    if platform == Platform.ANDROID:
        match = ANDROID_STORE_URL_RE.search(store_url)
        if match:
            return match.group(1)

    return None


def _extract_android_bundle_from_url(store_url: Optional[str]) -> Optional[str]:
    if not store_url:
        return None
    match = ANDROID_STORE_URL_RE.search(store_url)
    return match.group(1) if match else None


def _extract_name(text: str, platform: Platform) -> Optional[str]:
    """
    App name is usually the first non-empty line after the header,
    or the line immediately before the store URL.
    """
    lines = [line.strip() for line in text.split("\n")]

    # Find header line index
    header_idx = None
    for i, line in enumerate(lines):
        if PLATFORM_HEADER_RE.search(line):
            header_idx = i
            break

    # Try line before store URL
    for i, line in enumerate(lines):
        if ANY_URL_RE.search(line) and i > 0:
            candidate = lines[i - 1].strip()
            if candidate and not _is_label_line(candidate):
                return candidate

    # Try first meaningful line after header
    if header_idx is not None:
        for line in lines[header_idx + 1 :]:
            if not line:
                continue
            if ANY_URL_RE.search(line):
                break
            if _is_label_line(line):
                break
            return line

    return None


def _is_label_line(line: str) -> bool:
    """Check if line looks like a key: value field."""
    label_prefixes = (
        "Bundle",
        "App Bundle",
        "Backend",
        "Appsflyer",
        "Чья прила",
        "Пуши",
        "Сайт разработчика",
        "Название в Firebase",
        "Комментарий",
        "После ",
    )
    return any(line.startswith(prefix) for prefix in label_prefixes)


def _collect_missing_fields(app: ParsedApp) -> list[str]:
    if app.platform == Platform.UNKNOWN:
        return ["platform"]
    required = REQUIRED_FIELDS.get(app.platform, [])
    missing = []
    for field_name in required:
        if getattr(app, field_name) is None:
            missing.append(field_name)
    return missing


def parse_app_message(text: str) -> ParsedApp:
    """
    Parse a forwarded/sent Telegram message about a new iOS or Android app.

    Returns ParsedApp with extracted fields and list of missing required fields.
    """
    raw_text = text
    text = _normalize_text(text)

    platform = _detect_platform(text)
    store_url = _extract_store_url(text)

    app = ParsedApp(
        platform=platform,
        store_url=store_url,
        raw_text=raw_text,
    )

    # Parse labeled fields
    for field_name, pattern in FIELD_PATTERNS:
        value = _extract_field(text, pattern)
        if value is None:
            continue

        if field_name == "appsflyer":
            setattr(app, field_name, _parse_bool_ru(value))
        else:
            setattr(app, field_name, value)

    # Name
    app.name = _extract_name(text, platform)

    # Bundle: from message field; for Android fallback to Play Store URL
    if not app.bundle and platform == Platform.ANDROID:
        app.bundle = _extract_android_bundle_from_url(store_url)

    # Store ID
    if platform == Platform.IOS:
        app.store_id = _extract_store_id_from_url(store_url, platform)
    elif platform == Platform.ANDROID:
        app.store_id = app.bundle

    app.missing_fields = _collect_missing_fields(app)

    return app


def merge_missing_reply(app: ParsedApp, reply_text: str) -> ParsedApp:
    """
    Try to fill missing fields from a short user reply.
    If the reply looks like a full app message, re-parse it entirely.
    """
    text = _normalize_text(reply_text)

    # Full message resent
    if PLATFORM_HEADER_RE.search(text) or (
        _extract_store_url(text) and ("Bundle" in text or "bundle" in text.lower())
    ):
        return parse_app_message(reply_text)

    updated = ParsedApp(
        platform=app.platform,
        name=app.name,
        store_url=app.store_url,
        store_id=app.store_id,
        bundle=app.bundle,
        backend_domain=app.backend_domain,
        appsflyer=app.appsflyer,
        owner=app.owner,
        pushes=app.pushes,
        developer_site=app.developer_site,
        backend=app.backend,
        firebase_name=app.firebase_name,
        raw_text=app.raw_text,
        missing_fields=list(app.missing_fields),
    )

    platform_reply = _parse_platform_reply(text)
    if platform_reply is not None:
        return _apply_platform(updated, platform_reply)

    for field_name, pattern in FIELD_PATTERNS:
        value = _extract_field(text, pattern)
        if value is None:
            continue
        if field_name == "appsflyer":
            updated.appsflyer = _parse_bool_ru(value)
        else:
            setattr(updated, field_name, value)

    store_url = _extract_store_url(text)
    if store_url:
        updated.store_url = store_url
        if updated.platform == Platform.UNKNOWN:
            updated.platform = _detect_platform_from_url(text)

    if updated.name is None:
        maybe_name = text.strip()
        if (
            maybe_name
            and "\n" not in maybe_name
            and not _is_label_line(maybe_name)
            and not ANY_URL_RE.search(maybe_name)
            and len(maybe_name) < 80
            and "name" in (app.missing_fields or [])
        ):
            updated.name = maybe_name

    if updated.platform == Platform.IOS and updated.store_url and not updated.store_id:
        updated.store_id = _extract_store_id_from_url(updated.store_url, Platform.IOS)
    if updated.platform == Platform.ANDROID:
        if not updated.bundle and updated.store_url:
            updated.bundle = _extract_android_bundle_from_url(updated.store_url)
        updated.store_id = updated.bundle

    updated.missing_fields = _collect_missing_fields(updated)
    return updated

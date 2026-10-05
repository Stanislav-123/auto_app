from __future__ import annotations

from src.models import ParsedApp, Platform


def format_campaign_name(app: ParsedApp) -> str:
    """[ZM Apps] {name} - {store_id} - 888 {platform} [IO]"""
    name = app.name or "Unknown"
    store_id = app.store_id or "unknown"

    if app.platform == Platform.IOS:
        platform_label = "iOS"
    elif app.platform == Platform.ANDROID:
        platform_label = "Android"
    else:
        platform_label = "Unknown"

    return f"[ZM Apps] {name} - {store_id} - 888 {platform_label} [IO]"

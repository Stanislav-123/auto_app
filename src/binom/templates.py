from __future__ import annotations

from src.models import Platform


def get_template_campaign_id(
    platform: Platform,
    *,
    ios_id: int = 2,
    android_id: int = 4,
) -> int:
    if platform == Platform.IOS:
        return ios_id
    if platform == Platform.ANDROID:
        return android_id
    raise ValueError(f"Неизвестная платформа для шаблона Binom: {platform}")

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FacebookAppSettings:
    app_id: str
    access_token: str
    decryption_key: str


_APP_ID_RE = re.compile(
    r"(?:fb[_\s-]*app[_\s-]*id|app[_\s-]*id|апп[_\s-]*айди)\s*[:=]?\s*(\S+)",
    re.I,
)
_SECRET_RE = re.compile(
    r"(?:fb[_\s-]*access[_\s-]*token|secret[_\s-]*key|secret|сикрет)\s*[:=]?\s*(\S+)",
    re.I,
)
_DECRYPT_RE = re.compile(
    r"(?:fb[_\s-]*decryption[_\s-]*key|decryption[_\s-]*key|decrypt(?:ion)?|декрипшн)\s*[:=]?\s*(\S+)",
    re.I,
)


def parse_facebook_settings(text: str) -> FacebookAppSettings | None:
    """Parse all three FB values from one message (labels or 3 lines)."""
    raw = (text or "").strip()
    if not raw:
        return None

    labeled_id = _APP_ID_RE.search(raw)
    labeled_secret = _SECRET_RE.search(raw)
    labeled_decrypt = _DECRYPT_RE.search(raw)
    if labeled_id and labeled_secret and labeled_decrypt:
        return FacebookAppSettings(
            app_id=labeled_id.group(1).strip(),
            access_token=labeled_secret.group(1).strip(),
            decryption_key=labeled_decrypt.group(1).strip(),
        )

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) == 3:
        return FacebookAppSettings(
            app_id=lines[0],
            access_token=lines[1],
            decryption_key=lines[2],
        )
    return None

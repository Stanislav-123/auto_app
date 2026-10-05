from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("zavod.http")

_SENSITIVE_KEYS = {
    "api_key",
    "access_token",
    "password",
    "token",
    "authorization",
    "key_p8",
    "fb_access_token",
    "fb_decryption_key",
    "settings_appsflyer_key_api_v2",
}

_BINARYISH_KEYS = {
    "logo",
    "banner",
    "banner_appcraft",
    "slider",
    "apns_key",
}


def _truncate(text: str, limit: int = 2000) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(+{len(text) - limit} chars)"


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower in _SENSITIVE_KEYS or any(s in lower for s in ("password", "token", "secret", "authorization")):
                out[key] = "***"
            elif lower in _BINARYISH_KEYS or (
                isinstance(item, str) and item.startswith("data:image")
            ):
                out[key] = f"<omitted len={len(str(item))}>"
            else:
                out[key] = redact_payload(item)
        return out
    if isinstance(value, list):
        return [redact_payload(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return _truncate(value, 500)
    return value


def format_body(body: Any, limit: int = 2000) -> str:
    if body is None:
        return "-"
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(redact_payload(body), ensure_ascii=False, default=str)
        except TypeError:
            text = str(redact_payload(body))
        return _truncate(text, limit)
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", "replace")
        except Exception:
            return f"<bytes {len(body)}>"
        # Try parse form-urlencoded lightly
        if "=" in text and "&" in text and not text.strip().startswith("{"):
            pairs = []
            for part in text.split("&")[:40]:
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                key_l = key.lower()
                if key_l in _SENSITIVE_KEYS or "password" in key_l or "token" in key_l:
                    pairs.append(f"{key}=***")
                elif key_l in _BINARYISH_KEYS or value.startswith("data%3Aimage") or len(value) > 200:
                    pairs.append(f"{key}=<omitted len={len(value)}>")
                else:
                    pairs.append(f"{key}={value[:120]}")
            return _truncate("&".join(pairs), limit)
        return _truncate(text, limit)
    return _truncate(str(body), limit)


def log_request(service: str, method: str, url: str, body: Any = None) -> None:
    safe_url = re.sub(r"(api_key=)[^&]+", r"\1***", url, flags=re.I)
    logger.info("REQ %s %s %s body=%s", service, method.upper(), safe_url, format_body(body))


def log_response(
    service: str,
    method: str,
    url: str,
    status: int | str,
    body: Any = None,
) -> None:
    safe_url = re.sub(r"(api_key=)[^&]+", r"\1***", url, flags=re.I)
    logger.info(
        "RESP %s %s %s status=%s body=%s",
        service,
        method.upper(),
        safe_url,
        status,
        format_body(body),
    )


def log_step(message: str, **extra: Any) -> None:
    details = " | ".join(f"{key}={value}" for key, value in extra.items())
    if details:
        logging.getLogger("zavod.steps").info("%s | %s", message, details)
    else:
        logging.getLogger("zavod.steps").info("%s", message)

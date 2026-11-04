"""Загрузка экспортированных кук Google в контекст браузера. Значения кук нигде не печатаем."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SAME_SITE = {
    "no_restriction": "None",
    "none": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
}


def cookies_file() -> str | None:
    raw = (os.getenv("GOOGLE_COOKIES_FILE") or "").strip().strip('"')
    return raw or None


def load_cookie_file(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace").strip()
    if text.startswith("["):
        raw = json.loads(text)
    elif text.startswith("{"):
        data = json.loads(text)
        raw = data.get("cookies", data) if isinstance(data, dict) else data
    else:
        raw = _parse_netscape(text)
    return normalize_cookies(raw)


def normalize_cookies(raw: list[dict]) -> list[dict]:
    cookies: list[dict] = []
    for item in raw:
        name = str(item.get("name") or "").strip()
        domain = str(item.get("domain") or "").strip()
        if not name or not domain or "value" not in item:
            continue
        cookie: dict = {
            "name": name,
            "value": str(item.get("value")),
            "domain": domain,
            "path": str(item.get("path") or "/"),
            "httpOnly": bool(item.get("httpOnly", False)),
            "secure": bool(item.get("secure", False)),
            "sameSite": _same_site(item.get("sameSite")),
        }
        expires = item.get("expires", item.get("expirationDate"))
        if not item.get("session") and expires:
            try:
                cookie["expires"] = int(float(expires))
            except (TypeError, ValueError):
                pass
        cookies.append(cookie)
    return cookies


def _same_site(value) -> str:
    return _SAME_SITE.get(str(value or "").strip().lower(), "Lax")


def _parse_netscape(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts
        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure.upper() == "TRUE",
            "expires": expires,
            "session": expires in ("0", ""),
        })
    return out


GOOGLE_SUFFIXES = ("google.com", "google.co.uk", "google.ru", "googleapis.com", "gstatic.com", "youtube.com")


def google_only(cookies: list[dict]) -> list[dict]:
    """Только куки Google: остальные сайты из экспорта боту не нужны."""
    out = []
    for cookie in cookies:
        domain = cookie["domain"].lstrip(".").lower()
        if any(domain == suffix or domain.endswith("." + suffix) for suffix in GOOGLE_SUFFIXES):
            out.append(cookie)
    return out


def add_cookies_from_file(context, path: str | Path) -> int:
    cookies = google_only(load_cookie_file(path))
    added = 0
    for cookie in cookies:
        try:
            context.add_cookies([cookie])
            added += 1
        except Exception as exc:
            logger.warning("cookie_rejected name=%s error=%s", cookie["name"], str(exc)[:120])
    logger.info("cookies_loaded added=%d total=%d", added, len(cookies))
    return added

from __future__ import annotations

import os

# Shared GEO allowlist for all backends (same countries, different separators).
GEO_ALLOWED_CODES: list[str] = [
    "AL", "AT", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IS", "IE", "IT", "XK", "LV", "LT", "LU", "MT", "MD", "ME", "MK", "NO", "PL",
    "PT", "RO", "RS", "SK", "SI", "ES", "SE", "CH", "TR", "UA", "GB", "NL", "AF", "AO",
    "AM", "AZ", "BJ", "BW", "BF", "CM", "CV", "TD", "CD", "CG", "CI", "EG", "SZ", "GA",
    "GM", "GH", "GW", "IL", "KE", "LB", "LR", "MG", "MW", "ML", "MR", "MU", "MZ", "NA",
    "NE", "NG", "RW", "ST", "SN", "SL", "ZA", "ZM", "AI", "AG", "AR", "BS", "BB", "BZ",
    "BM", "BO", "BR", "VG", "KY", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GD", "GT",
    "GY", "HN", "JM", "MX", "MS", "NI", "PA", "PY", "PE", "KN", "LC", "VC", "SR", "TT",
    "TC", "UY", "VE", "AU", "BT", "BN", "KH", "FJ", "HK", "ID", "JP", "KZ", "KR", "KG",
    "LA", "MO", "MY", "MV", "MN", "MM", "NR", "NP", "NZ", "PK", "PW", "PG", "PH", "SG",
    "SB", "LK", "TW", "TJ", "TH", "TO", "TM", "UZ", "VU", "VN", "PF", "MQ", "BD", "FL",
    "CA", "TG", "LI", "RE", "MF", "BL", "GF", "TZ", "UK", "SO", "ET", "GP", "GE", "IN",
    "GN", "UG", "PM", "WF", "NC", "YT", "MA", "UM",
]


def geo_allowed_new() -> str:
    """Comma-separated GEO list for new iOS backend."""
    return ",".join(GEO_ALLOWED_CODES)


def geo_allowed_old() -> str:
    """Newline-separated GEO list for old iOS backend."""
    return "\n".join(GEO_ALLOWED_CODES)


def geo_allowed_android() -> str:
    """Space-separated GEO list for Android backend."""
    return " ".join(GEO_ALLOWED_CODES)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def binom_tracker_url(campaign_url: str | None = None) -> str:
    """Click URL трекера (без key).

    Сначала BACKEND_BINOM_URL из .env. Если пусто — берём base URL кампании Binom
    (всё до '?'), чтобы шаг бэкенда не падал на живом заводе, когда в .env
    забыли прописать трекер, а ключ кампании уже есть.
    """
    configured = _env("BACKEND_BINOM_URL")
    if configured:
        return configured
    raw = (campaign_url or "").strip()
    if not raw:
        return ""
    return raw.split("?", 1)[0].strip()


def appsflyer_dev_key() -> str:
    return _env("APPSFLYER_DEV_KEY")


def appsflyer_api_v2_key() -> str:
    return _env("APPSFLYER_API_V2_KEY")

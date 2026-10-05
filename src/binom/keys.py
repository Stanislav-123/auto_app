from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse


def extract_binom_campaign_key(*, key: str | None = None, url: str | None = None) -> str:
    """Prefer explicit key, otherwise parse key= from campaign URL."""
    if key and str(key).strip() and str(key).strip().lower() != "dev":
        return str(key).strip()

    if not url:
        return (key or "").strip()

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "key" in query and query["key"]:
        return unquote(query["key"][0]).strip()

    match = re.search(r"[?&]key=([^&]+)", url)
    if match:
        return unquote(match.group(1)).strip()

    return (key or "").strip()

from __future__ import annotations

import re


def normalize_backend_domain(value: str | None) -> str | None:
    """Strip protocol/path/spaces and return hostname-like domain."""
    if not value:
        return None

    domain = value.strip()
    if not domain:
        return None

    domain = re.sub(r"^https?://", "", domain, flags=re.IGNORECASE)
    domain = domain.split("/")[0].strip().rstrip(".")
    domain = domain.strip()
    if not domain or " " in domain:
        return None
    return domain

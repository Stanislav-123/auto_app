from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)")
_OTHER_CURRENCY_RE = re.compile(r"\b(EUR|CNY|GBP|INR|CAD)\b", re.I)


@dataclass
class DomainQuote:
    name: str
    available: bool
    price_usd: float | None
    price_text: str = ""


def parse_usd_price(value) -> float | None:
    """Dynadot отдаёт цену строкой вида '2.88 in USD' или числом."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if _OTHER_CURRENCY_RE.search(text) and "USD" not in text.upper():
        return None
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    return float(match.group(1))


def is_affordable(price: float | None, limit: float) -> bool:
    return price is not None and price <= limit


def expiration_in_one_year(today: date | None = None) -> str:
    today = today or date.today()
    try:
        exp = today.replace(year=today.year + 1)
    except ValueError:
        exp = today.replace(year=today.year + 1, day=28)
    return exp.isoformat()


def is_ipv4(value: str) -> bool:
    parts = (value or "").strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        number = int(part)
        if number > 255 or (len(part) > 1 and part.startswith("0")):
            return False
    return True


def parse_domain_list(text: str) -> list[str]:
    chunks = re.split(r"[\s,;]+", (text or "").strip().lower())
    names: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        name = chunk.strip(".")
        if not name or "." not in name or name in seen:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", name):
            continue
        seen.add(name)
        names.append(name)
    return names

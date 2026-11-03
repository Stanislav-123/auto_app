from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.domains.pricing import DomainQuote, parse_usd_price
from src.http_errors import transport_failure
from src.workflow.http_log import log_request, log_response

API_URL = "https://api.dynadot.com/api3.json"


class DynadotError(Exception):
    """Ошибка Dynadot API."""


class DynadotClient:
    def __init__(self, api_key: str, *, api_url: str = API_URL) -> None:
        self.api_key = api_key
        self.api_url = api_url

    def search(self, domains: list[str]) -> list[DomainQuote]:
        if not domains:
            return []
        # Обычный аккаунт принимает один домен на запрос (domain0).
        quotes: list[DomainQuote] = []
        for name in domains:
            params = {
                "key": self.api_key,
                "command": "search",
                "domain0": name,
                "show_price": "1",
                "currency": "USD",
            }
            data = self._call(params)
            quotes.extend(parse_search_response(data, fallback_name=name))
        return quotes

    def register(self, domain: str, *, duration: int = 1) -> dict[str, Any]:
        data = self._call(
            {
                "key": self.api_key,
                "command": "register",
                "domain": domain,
                "duration": str(duration),
                "currency": "USD",
            }
        )
        ensure_command_ok(data, "RegisterResponse", "регистрация")
        return data

    def domain_record(self, domain: str) -> tuple[bool, str | None]:
        """Домен в аккаунте и дата окончания (YYYY-MM-DD), если Dynadot её отдал.

        Сетевая ошибка пробрасывается. «Нет такого домена» — (False, None).
        """
        data = self._call(
            {
                "key": self.api_key,
                "command": "domain_info",
                "domain": domain,
            }
        )
        return parse_domain_info(data)

    def set_ns(self, domain: str, ns0: str, ns1: str) -> dict[str, Any]:
        data = self._call(
            {
                "key": self.api_key,
                "command": "set_ns",
                "domain": domain,
                "ns0": ns0,
                "ns1": ns1,
            }
        )
        ensure_command_ok(data, "SetNsResponse", "set_ns")
        return data

    def _call(self, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        url = f"{self.api_url}?{query}"
        log_request("dynadot", "GET", url)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("dynadot", "GET", url, exc.code, details)
            raise DynadotError(f"HTTP {exc.code}: {details[:300]}") from exc
        except Exception as exc:
            failure = transport_failure(exc)
            if failure is None:
                raise
            log_response("dynadot", "GET", url, "error", failure)
            raise DynadotError(failure) from exc
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            log_response("dynadot", "GET", url, status, raw[:300])
            raise DynadotError(f"Некорректный JSON Dynadot: {raw[:200]}") from exc
        log_response("dynadot", "GET", url, status, data)
        if not isinstance(data, dict):
            raise DynadotError(f"Неожиданный ответ Dynadot: {data}")
        return data


def parse_search_response(data: dict[str, Any], *, fallback_name: str = "") -> list[DomainQuote]:
    block = data.get("SearchResponse") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        raise DynadotError(f"Нет SearchResponse: {data}")
    _raise_if_bad(block, "поиск")
    results = block.get("SearchResults")
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        results = []
    quotes: list[DomainQuote] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("DomainName") or fallback_name).strip().lower()
        available = str(item.get("Available") or "").strip().lower() in {"yes", "true", "1"}
        price_text = _price_text(item)
        quotes.append(
            DomainQuote(
                name=name,
                available=available,
                price_usd=parse_usd_price(price_text),
                price_text=price_text,
            )
        )
    if not quotes and fallback_name:
        quotes.append(DomainQuote(name=fallback_name.lower(), available=False, price_usd=None))
    return quotes


def parse_domain_info(data: dict[str, Any]) -> tuple[bool, str | None]:
    block = data.get("DomainInfoResponse") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        raise DynadotError(f"Нет DomainInfoResponse: {data}")
    code = block.get("ResponseCode", block.get("SuccessCode"))
    status = str(block.get("Status") or "").strip().lower()
    if str(code) not in {"0", "0.0"} or (status and status not in {"success", "ok"}):
        return False, None
    info = block.get("DomainInfo")
    if not isinstance(info, dict):
        info = block
    return True, expiration_to_date(info.get("Expiration"))


def expiration_to_date(value: Any) -> str | None:
    """Dynadot кладёт Expiration как unix-миллисекунды строкой."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    try:
        stamp = int(float(text))
    except ValueError:
        return None
    if stamp < 10_000_000_000:
        stamp *= 1000
    try:
        moment = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.date().isoformat()


def ensure_command_ok(data: dict[str, Any], root: str, action: str) -> None:
    block = data.get(root)
    if not isinstance(block, dict):
        raise DynadotError(f"Нет {root}: {data}")
    _raise_if_bad(block, action)


def _raise_if_bad(block: dict[str, Any], action: str) -> None:
    code = block.get("ResponseCode", block.get("SuccessCode"))
    status = str(block.get("Status") or "").strip().lower()
    if str(code) not in {"0", "0.0"} or (status and status not in {"success", "ok"}):
        error = block.get("Error") or block.get("Message") or block.get("Status") or block
        raise DynadotError(f"Dynadot {action}: {error}")


def _price_text(item: dict[str, Any]) -> str:
    for key in ("Price", "RegistrationPrice", "PriceInUsd", "price"):
        if item.get(key) not in (None, ""):
            return str(item.get(key))
    return ""

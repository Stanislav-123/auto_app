from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.http_errors import transport_failure
from src.workflow.http_log import log_request, log_response, log_step

CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    """Ошибка Cloudflare API."""


@dataclass
class ZoneInfo:
    id: str
    name_servers: list[str]
    created: bool


def cloudflare_auth_headers(email: str, auth_key: str) -> dict[str, str]:
    """cfk_ и старый hex — Global API Key. cfut_/cfat_ — Bearer-токен."""
    key = (auth_key or "").strip()
    lowered = key.lower()
    if lowered.startswith("cfut_") or lowered.startswith("cfat_"):
        return {"Authorization": f"Bearer {key}"}
    return {
        "X-Auth-Email": email,
        "X-Auth-Key": key,
    }


def alternate_auth_headers(email: str, auth_key: str) -> dict[str, str]:
    primary = cloudflare_auth_headers(email, auth_key)
    if "Authorization" in primary:
        return {"X-Auth-Email": email, "X-Auth-Key": auth_key.strip()}
    return {"Authorization": f"Bearer {auth_key.strip()}"}


class CloudflareClient:
    def __init__(self, email: str, auth_key: str, account_id: str, *, api_url: str = CF_API) -> None:
        self.email = email
        self.auth_key = auth_key
        self.account_id = str(account_id)
        self.api_url = api_url.rstrip("/")
        self._headers = cloudflare_auth_headers(email, auth_key)
        self._switched = False

    def ensure_zone(self, name: str) -> ZoneInfo:
        existing = self.find_zone(name)
        if existing is not None:
            return existing
        payload = {"name": name, "account": {"id": self.account_id}, "type": "full"}
        data = self._request("POST", "/zones", payload)
        zone = data.get("result") if isinstance(data, dict) else None
        if not isinstance(zone, dict) or not zone.get("id"):
            raise CloudflareError(f"Cloudflare не вернула зону: {data}")
        servers = [str(item) for item in (zone.get("name_servers") or []) if item]
        return ZoneInfo(id=str(zone["id"]), name_servers=servers, created=True)

    def list_account_zones(self) -> list[dict[str, Any]]:
        """Зоны аккаунта. Пустой список — аккаунт ещё без зон."""
        query = urllib.parse.urlencode({"account.id": self.account_id, "per_page": "50"})
        data = self._request("GET", f"/zones?{query}")
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            return []
        zones: list[dict[str, Any]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            servers = [str(ns) for ns in (item.get("name_servers") or []) if ns]
            zones.append({"name": str(item.get("name") or "").strip().lower(), "name_servers": servers})
        return zones

    def find_zone(self, name: str) -> ZoneInfo | None:
        query = urllib.parse.urlencode({"name": name})
        data = self._request("GET", f"/zones?{query}")
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list) or not result:
            return None
        zone = result[0]
        servers = [str(item) for item in (zone.get("name_servers") or []) if item]
        return ZoneInfo(id=str(zone["id"]), name_servers=servers, created=False)

    def ensure_apex_a(self, zone_id: str, domain: str, ip: str) -> bool:
        """True если запись создана, False если уже была."""
        query = urllib.parse.urlencode({"type": "A", "name": domain})
        data = self._request("GET", f"/zones/{zone_id}/dns_records?{query}")
        records = data.get("result") if isinstance(data, dict) else None
        if isinstance(records, list) and records:
            return False
        payload = {"type": "A", "name": "@", "content": ip, "proxied": True, "ttl": 1}
        created = self._request("POST", f"/zones/{zone_id}/dns_records", payload)
        if isinstance(created, dict) and created.get("success") is False:
            raise CloudflareError(_cf_error(created))
        return True

    def ensure_ssl_flexible(self, zone_id: str) -> bool:
        """True если настройка изменена, False если уже flexible."""
        current = self._request("GET", f"/zones/{zone_id}/settings/ssl")
        result = current.get("result") if isinstance(current, dict) else None
        if isinstance(result, dict) and str(result.get("value") or "").lower() == "flexible":
            return False
        updated = self._request("PATCH", f"/zones/{zone_id}/settings/ssl", {"value": "flexible"})
        if isinstance(updated, dict) and updated.get("success") is False:
            raise CloudflareError(_cf_error(updated))
        return True

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.api_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        log_request("cloudflare", method, url, payload)
        try:
            return self._open(method, url, body, self._headers)
        except CloudflareError as exc:
            if self._switched or not _looks_like_auth_error(str(exc)):
                raise
            self._headers = alternate_auth_headers(self.email, self.auth_key)
            self._switched = True
            log_step("cloudflare_auth_retry", email=self.email)
            return self._open(method, url, body, self._headers)

    def _open(self, method: str, url: str, body: bytes | None, auth_headers: dict[str, str]) -> dict[str, Any]:
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        headers.update(auth_headers)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("cloudflare", method, url, exc.code, details)
            raise CloudflareError(f"HTTP {exc.code}: {details[:300]}") from exc
        except Exception as exc:
            failure = transport_failure(exc)
            if failure is None:
                raise
            log_response("cloudflare", method, url, "error", failure)
            raise CloudflareError(failure) from exc
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            log_response("cloudflare", method, url, status, raw[:300])
            raise CloudflareError(f"Некорректный JSON Cloudflare: {raw[:200]}") from exc
        log_response("cloudflare", method, url, status, data)
        if isinstance(data, dict) and data.get("success") is False:
            raise CloudflareError(_cf_error(data))
        return data if isinstance(data, dict) else {}


def _cf_error(data: dict[str, Any]) -> str:
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
        return str(first)
    return str(data.get("message") or data)


def _looks_like_auth_error(message: str) -> bool:
    text = message.lower()
    return any(token in text for token in ("403", "9106", "9109", "10000", "authentication", "invalid api"))

from __future__ import annotations

import urllib.parse
from typing import Any

from src.crm.client import CrmClient
from src.crm.domain import crm_result_error, normalize_backend_domain


class PortalDomainClient:
    """Справочник доменов и аккаунты Cloudflare в CRM."""

    def __init__(self, crm: CrmClient) -> None:
        self.crm = crm

    def list_free_cloudflare_accounts(self) -> list[dict[str, Any]]:
        data = self.crm._request(
            "GET",
            "/cloudflare-account/index?status=0&page=1&per-page=100",
        )
        error = crm_result_error(data)
        if error:
            raise RuntimeError(error)
        accounts = _models(data)
        free = []
        for item in accounts:
            if str(item.get("status", 0)) in {"0", "0.0"}:
                free.append(item)
        return free

    def mark_cloudflare_account_busy(self, crm_id: int, payload: dict[str, Any]) -> Any:
        data = self.crm._request("POST", f"/cloudflare-account/update?id={crm_id}", payload=payload)
        error = crm_result_error(data)
        if error:
            raise RuntimeError(error)
        return data

    def domain_exists(self, name: str) -> bool:
        host = normalize_backend_domain(name) or name.strip().lower()
        query = urllib.parse.urlencode({"search": host, "page": 1, "per-page": 50})
        data = self.crm._request("GET", f"/domain/index?{query}")
        for item in _models(data):
            raw = str(item.get("domain") or "")
            if (normalize_backend_domain(raw) or raw.strip().lower()) == host:
                return True
        return False

    def create_portal_domain(self, payload: dict[str, Any]) -> Any:
        data = self.crm._request("POST", "/domain/create", payload=payload)
        error = crm_result_error(data)
        if error:
            raise RuntimeError(error)
        return data


def _models(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("models", "items", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = value.get("models") or value.get("items")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []

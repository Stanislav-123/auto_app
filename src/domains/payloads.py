from __future__ import annotations

from typing import Any


def cloudflare_account_update_payload(account: dict[str, Any], ns0: str, ns1: str) -> dict[str, Any]:
    return {
        "account_id": account.get("account_id"),
        "auth_key": account.get("auth_key"),
        "email": account.get("email"),
        "first_ns": ns0,
        "second_ns": ns1,
        "password": account.get("password"),
        "status": 1,
    }


def domain_create_payload(domain: str, account: dict[str, Any], expiration_date: str) -> dict[str, Any]:
    return {
        "batch": domain,
        "status": 1,
        "type_source": 3,
        "comment": None,
        "registrator": "dynadot",
        "expiration_date": expiration_date,
        "cloudflare_accounts": [
            {
                "domain": domain,
                "primary_ns": None,
                "secondary_ns": None,
                "auth_key": None,
                "cloudflare_account_id": str(account.get("id")),
                "email": account.get("email"),
                "password": account.get("password"),
            }
        ],
    }


def nameservers_differ(account: dict[str, Any], name_servers: list[str]) -> bool:
    current = [str(account.get("first_ns") or "").strip().lower(), str(account.get("second_ns") or "").strip().lower()]
    fresh = [item.strip().lower() for item in name_servers[:2]]
    if len(fresh) < 2:
        return False
    return current != fresh

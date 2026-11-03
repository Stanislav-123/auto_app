from __future__ import annotations

from typing import Any


def cloudflare_account_status_payload() -> dict[str, Any]:
    """Занять аккаунт: только статус. Почту не шлём — из-за неё CRM отвечает 409."""
    return {"status": 1}


def cloudflare_account_update_payload(account: dict[str, Any], ns0: str, ns1: str) -> dict[str, Any]:
    """Запасной update, если CRM требует остальные поля. Почты здесь тоже нет."""
    return {
        "account_id": account.get("account_id"),
        "auth_key": account.get("auth_key"),
        "first_ns": ns0,
        "second_ns": ns1,
        "password": account.get("password"),
        "status": 1,
    }


def domain_create_payload(
    domain: str,
    account: dict[str, Any],
    expiration_date: str,
    ns0: str,
    ns1: str,
) -> dict[str, Any]:
    """Привязать уже существующий аккаунт CF. cloudflare_account_id создаёт дубль в CRM."""
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
                "primary_ns": ns0,
                "secondary_ns": ns1,
                "auth_key": account.get("auth_key"),
                "cf_db_account_id": str(account.get("id")),
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

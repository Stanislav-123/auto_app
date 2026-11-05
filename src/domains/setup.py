from __future__ import annotations

import random
import threading
from datetime import date
from typing import Any, Callable, NoReturn

from src.domains.cloudflare import CloudflareClient
from src.domains.dynadot import DynadotClient, DynadotError
from src.domains.names import generate_candidate
from src.domains.payloads import (
    cloudflare_account_status_payload,
    cloudflare_account_update_payload,
    domain_create_payload,
    nameservers_differ,
)
from src.domains.pricing import DomainQuote, expiration_in_one_year, is_affordable, parse_domain_list
from src.workflow.http_log import log_step

_ACCOUNT_LOCK = threading.Lock()
_RESERVED_IDS: set[str] = set()

MAX_SEARCH_ATTEMPTS = 200
MAX_BULK = 20
MAX_CF_BUSY_ATTEMPTS = 5


class DomainSetupError(Exception):
    """Ошибка шага покупки или настройки домена."""


class AccountUnusable(Exception):
    """CRM не дала пометить аккаунт занятым. Зона этого запуска уже снята."""

    def __init__(self, reason: str, notice: str) -> None:
        super().__init__(reason)
        self.notice = notice


def collect_affordable(
    count: int,
    search: Callable[[str], DomainQuote],
    *,
    max_price: float,
    rng: random.Random | None = None,
    max_attempts: int = MAX_SEARCH_ATTEMPTS,
) -> tuple[list[DomainQuote], int]:
    """Ищет новые имена, пока не наберёт count с ценой <= max_price."""
    rng = rng or random.Random()
    found: list[DomainQuote] = []
    seen: set[str] = set()
    attempts = 0
    spins = 0
    while len(found) < count and attempts < max_attempts and spins < max_attempts * 5:
        spins += 1
        name = generate_candidate(rng)
        if name in seen:
            continue
        seen.add(name)
        attempts += 1
        quote = search(name)
        if quote.available and is_affordable(quote.price_usd, max_price):
            found.append(quote)
    return found, attempts


def quotes_over_limit(quotes: list[DomainQuote], max_price: float) -> list[DomainQuote]:
    return [quote for quote in quotes if quote.available and quote.price_usd is not None and quote.price_usd > max_price]


def select_for_purchase(quotes: list[DomainQuote], *, max_price: float, allow_over_limit: bool) -> list[DomainQuote]:
    chosen: list[DomainQuote] = []
    for quote in quotes:
        if not quote.available:
            continue
        if quote.price_usd is None:
            continue
        if quote.price_usd <= max_price or allow_over_limit:
            chosen.append(quote)
    return chosen


def purchase_ready(job: dict[str, Any]) -> bool:
    return bool(job.get("confirmed") and job.get("ip") and job.get("domains"))


def register_available(
    quotes: list[DomainQuote],
    register: Callable[[str], Any],
) -> tuple[list[DomainQuote], list[tuple[str, str]]]:
    bought: list[DomainQuote] = []
    failed: list[tuple[str, str]] = []
    for quote in quotes:
        try:
            register(quote.name)
        except Exception as exc:
            failed.append((quote.name, str(exc)))
            continue
        bought.append(quote)
    return bought, failed


def take_free_account(
    accounts: list[dict[str, Any]],
    excluded: set[str] | None = None,
) -> dict[str, Any] | None:
    banned = excluded or set()
    with _ACCOUNT_LOCK:
        for account in accounts:
            ident = str(account.get("id"))
            status = account.get("status")
            if status not in (0, "0", None):
                continue
            if ident in _RESERVED_IDS or ident in banned:
                continue
            _RESERVED_IDS.add(ident)
            return account
    return None


def release_account(account_id: Any) -> None:
    with _ACCOUNT_LOCK:
        _RESERVED_IDS.discard(str(account_id))


def new_domain_state(quote: DomainQuote) -> dict[str, Any]:
    return {
        "name": quote.name,
        "price": quote.price_usd,
        "done": [],
        "failed_step": None,
        "error": None,
        "cf_crm_id": None,
        "zone_id": None,
        "ns": [],
        "bought": False,
    }


def setup_purchased_domain(
    state: dict[str, Any],
    *,
    dynadot: DynadotClient,
    account: dict[str, Any],
    cf: CloudflareClient,
    crm: Any,
    ip: str,
    today: date | None = None,
    emit: Callable[[str], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    excluded: list[str] | None = None,
) -> list[str]:
    """Доводит один уже купленный домен. Возвращает новые сообщения о шагах."""
    notes: list[str] = []
    name = state["name"]
    done = state.setdefault("done", [])

    def mark(step: str, text: str) -> None:
        if step not in done:
            done.append(step)
        state["failed_step"] = None
        state["error"] = None
        notes.append(text)
        if emit:
            emit(text)

    try:
        if state.get("skip_buy"):
            if "buy" not in done:
                done.append("buy")
            state["bought"] = True
        elif "buy" not in done:
            dynadot.register(name)
            state["bought"] = True
            mark("buy", f"{name}: куплен")
        elif not state.get("bought"):
            state["bought"] = True

        if "zone" not in done:
            zone = cf.ensure_zone(name)
            state["zone_id"] = zone.id
            state["ns"] = list(zone.name_servers)
            if nameservers_differ(account, zone.name_servers):
                log_step(
                    "cf_ns_differ",
                    domain=name,
                    crm_ns=f"{account.get('first_ns')},{account.get('second_ns')}",
                    zone_ns=",".join(zone.name_servers),
                )
            state["zone_created"] = bool(zone.created)
            remember_account(state, account)
            mark("zone", f"{name}: зона {'создана' if zone.created else 'уже была'}")
            if on_progress:
                on_progress()

        if "a" not in done:
            created = cf.ensure_apex_a(state["zone_id"], name, ip)
            mark("a", f"{name}: A-запись {'создана' if created else 'уже была'}")

        if "ssl" not in done:
            changed = cf.ensure_ssl_flexible(state["zone_id"])
            mark("ssl", f"{name}: SSL flexible {'включён' if changed else 'уже был'}")

        if "ns" not in done:
            ns = list(state.get("ns") or [])
            if len(ns) < 2:
                raise DomainSetupError("У зоны нет двух NS")
            dynadot.set_ns(name, ns[0], ns[1])
            mark("ns", f"{name}: NS прописаны")

        if "crm_domain" not in done:
            if not crm.domain_exists(name):
                expires = state.get("expiration") or expiration_in_one_year(today)
                ns = list(state.get("ns") or [])
                if len(ns) < 2:
                    raise DomainSetupError("У зоны нет двух NS")
                payload = domain_create_payload(name, account, expires, ns[0], ns[1])
                crm.create_portal_domain(payload)
                mark("crm_domain", f"{name}: домен добавлен в CRM")
            else:
                mark("crm_domain", f"{name}: домен уже был в CRM")

        if "cf_busy" not in done and "crm_domain" in done:
            _mark_account_busy(
                state,
                account,
                cf,
                crm,
                notes,
                name,
                emit=emit,
                on_progress=on_progress,
                excluded=excluded,
            )
    except AccountUnusable:
        raise
    except Exception as exc:
        step = _failed_step(done)
        state["failed_step"] = step
        state["error"] = str(exc)
        notes.append(f"{name}: ошибка на шаге {step}: {exc}")
        if "zone" not in done:
            release_account(account.get("id"))
            state["cf_crm_id"] = None
            state.pop("cf_account", None)
        raise DomainSetupError(notes[-1]) from exc
    return notes


def _mark_account_busy(
    state: dict[str, Any],
    account: dict[str, Any],
    cf: Any,
    crm: Any,
    notes: list[str],
    name: str,
    *,
    emit: Callable[[str], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    excluded: list[str] | None = None,
) -> None:
    done = state.setdefault("done", [])
    if "cf_busy" in done:
        return
    if str(account.get("status")) == "1":
        done.append("cf_busy")
        notes.append(f"{name}: аккаунт CF уже занят")
        return
    crm_id = int(account["id"])
    try:
        crm.mark_cloudflare_account_busy(crm_id, cloudflare_account_status_payload())
    except Exception as exc:
        if _is_email_taken_error(exc) or not _is_missing_fields_error(exc):
            _skip_busy_mark(state, notes, name, exc, emit=emit)
            return
        ns = list(state.get("ns") or [])
        if len(ns) < 2:
            _skip_busy_mark(state, notes, name, exc, emit=emit)
            return
        payload = cloudflare_account_update_payload(account, ns[0], ns[1])
        try:
            crm.mark_cloudflare_account_busy(crm_id, payload)
        except Exception as fallback_exc:
            _skip_busy_mark(state, notes, name, fallback_exc, emit=emit)
            return
    account["status"] = 1
    stored = state.get("cf_account")
    if isinstance(stored, dict):
        stored["status"] = 1
    done.append("cf_busy")
    notes.append(f"{name}: аккаунт CF отмечен занятым")


def _is_email_taken_error(exc: BaseException) -> bool:
    """POST /cloudflare-account/update отвечает 409 на свою же почту — unique не исключает текущую запись."""
    text = str(exc).lower()
    return "уже занят" in text or "already been taken" in text


def _skip_busy_mark(
    state: dict[str, Any],
    notes: list[str],
    name: str,
    exc: BaseException | None = None,
    *,
    emit: Callable[[str], None] | None = None,
) -> None:
    """Домен уже в CRM — зону не откатываем, пометку занятым просто фиксируем как неудачную."""
    done = state.setdefault("done", [])
    if "cf_busy" not in done:
        done.append("cf_busy")
    reason = str(exc) if exc else "почта уже есть в CRM"
    state["cf_busy_error"] = reason
    text = f"{name}: домен уже в CRM, но аккаунт CF не пометился занятым ({reason})"
    notes.append(text)
    if emit:
        emit(text)


def _is_missing_fields_error(exc: BaseException) -> bool:
    """CRM отвергла короткий payload, потому что ждёт остальные поля."""
    text = str(exc).lower()
    if _is_email_taken_error(exc):
        return False
    if "http 422" in text:
        return True
    needles = (
        "необходимо заполнить",
        "не должно быть пустым",
        "не может быть пустым",
        "cannot be blank",
        "can't be blank",
        "is required",
        "field is required",
        "обязательн",
    )
    return any(needle in text for needle in needles)


def _give_up_account(
    state: dict[str, Any],
    account: dict[str, Any],
    cf: Any,
    name: str,
    exc: BaseException,
    *,
    emit: Callable[[str], None] | None,
    on_progress: Callable[[], None] | None,
    excluded: list[str] | None,
) -> NoReturn:
    reason = str(exc)
    email = account.get("email") or account.get("id")
    deleted = False
    zone_id = state.get("zone_id")
    if state.get("zone_created") and zone_id:
        try:
            cf.delete_zone(str(zone_id))
            deleted = True
        except Exception as delete_exc:
            log_step(
                "cf_zone_delete_failed",
                domain=name,
                zone_id=str(zone_id),
                error=str(delete_exc),
            )
            reason = f"{reason}; зону удалить не вышло: {delete_exc}"
    if deleted:
        notice = f"аккаунт {email} не удалось пометить занятым ({reason}), зону удалил, беру следующий"
    else:
        notice = f"аккаунт {email} не удалось пометить занятым ({reason}), зону не трогаю, беру следующий"
    if emit:
        emit(notice)
    done = state.setdefault("done", [])
    if "zone" in done:
        done.remove("zone")
    state["zone_id"] = None
    state["ns"] = []
    state["zone_created"] = False
    state["cf_crm_id"] = None
    state.pop("cf_account", None)
    state["failed_step"] = None
    state["error"] = None
    ident = str(account.get("id"))
    release_account(ident)
    banned = excluded if excluded is not None else []
    if ident not in banned:
        banned.append(ident)
    state["cf_attempts"] = int(state.get("cf_attempts") or 0) + 1
    if on_progress:
        on_progress()
    raise AccountUnusable(reason, notice)


def _failed_step(done: list[str]) -> str:
    order = ["buy", "zone", "a", "ssl", "ns", "crm_domain", "cf_busy"]
    for step in order:
        if step not in done:
            return step
    return "crm_domain"


def build_cf_client(account: dict[str, Any]) -> CloudflareClient:
    return CloudflareClient(
        email=str(account.get("email") or ""),
        auth_key=str(account.get("auth_key") or ""),
        account_id=str(account.get("account_id") or ""),
    )


def search_specific(dynadot: DynadotClient, text: str) -> list[DomainQuote]:
    names = parse_domain_list(text)
    if not names:
        return []
    return dynadot.search(names)


def search_bulk(dynadot: DynadotClient, count: int, *, max_price: float, rng: random.Random | None = None) -> tuple[list[DomainQuote], int]:
    def _one(name: str) -> DomainQuote:
        found = dynadot.search([name])
        if not found:
            return DomainQuote(name=name, available=False, price_usd=None)
        return found[0]

    return collect_affordable(count, _one, max_price=max_price, rng=rng)


def run_confirmed_batch(
    domains: list[dict[str, Any]],
    *,
    dynadot: DynadotClient,
    crm: Any,
    ip: str,
    confirmed: bool = False,
    today: date | None = None,
    cf_factory: Callable[[dict[str, Any]], Any] | None = None,
    emit: Callable[[str], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    excluded_ids: list[str] | None = None,
) -> list[str]:
    """Покупает и настраивает пачку. Покупка падает точечно, остальные идут дальше."""
    if not confirmed:
        raise DomainSetupError("Покупка без подтверждения")
    if not ip:
        raise DomainSetupError("Не задан IP")
    messages: list[str] = []
    excluded = excluded_ids if excluded_ids is not None else []

    def say(text: str) -> None:
        messages.append(text)
        if emit:
            emit(text)

    free = [item for item in crm.list_free_cloudflare_accounts() if str(item.get("status", 0)) in {"0", "0.0"}]
    pending = [
        item
        for item in domains
        if "crm_domain" not in item.get("done", []) or "cf_busy" not in item.get("done", [])
    ]

    for state in pending:
        name = state["name"]
        try:
            if "zone" not in state.get("done", []) and not _has_account_slot(state, free, excluded):
                say(
                    f"Свободные аккаунты Cloudflare кончились ({len(free)} в списке). "
                    f"{name} и следующие домены не беру."
                )
                break
            if state.get("skip_buy") and "buy" not in state.get("done", []):
                owned, expires = _ownership(dynadot, name)
                state["ownership_checked"] = True
                if expires:
                    state["expiration"] = expires
                if not owned:
                    say(f"{name}: нет в аккаунте Dynadot, пропускаю")
                    state["skipped"] = True
                    state["failed_step"] = None
                    state["done"] = ["buy", "zone", "a", "ssl", "ns", "crm_domain", "cf_busy"]
                    if on_progress:
                        on_progress()
                    continue
                state.setdefault("done", []).append("buy")
                state["bought"] = True
            elif "buy" not in state.get("done", []):
                try:
                    dynadot.register(name)
                except DynadotError as exc:
                    state["failed_step"] = "buy"
                    state["error"] = str(exc)
                    say(f"{name}: покупка не прошла: {exc}")
                    if on_progress:
                        on_progress()
                    continue
                state.setdefault("done", []).append("buy")
                state["bought"] = True
                state["failed_step"] = None
                say(f"{name}: куплен")
                if on_progress:
                    on_progress()

            if state.get("failed_step") == "cf_busy":
                state["cf_attempts"] = 0
                state["failed_step"] = None
                state["error"] = None

            while True:
                if int(state.get("cf_attempts") or 0) >= MAX_CF_BUSY_ATTEMPTS:
                    state["failed_step"] = "cf_busy"
                    state["error"] = state.get("error") or "не удалось пометить аккаунт CF занятым"
                    say(f"{name}: не вышло пометить аккаунт CF занятым за {state['cf_attempts']} попыток")
                    if on_progress:
                        on_progress()
                    break
                account = _account_for_state(state, free, excluded)
                if account is None:
                    state["failed_step"] = "zone"
                    state["error"] = "нет свободного аккаунта Cloudflare"
                    say(f"{name}: нет свободного аккаунта Cloudflare, останавливаюсь на остальных")
                    if on_progress:
                        on_progress()
                    return messages
                cf = (cf_factory or build_cf_client)(account)
                try:
                    notes = setup_purchased_domain(
                        state,
                        dynadot=dynadot,
                        account=account,
                        cf=cf,
                        crm=crm,
                        ip=ip,
                        today=today,
                        emit=emit,
                        on_progress=on_progress,
                        excluded=excluded,
                    )
                except AccountUnusable as exc:
                    if exc.notice not in messages:
                        messages.append(exc.notice)
                    if int(state.get("cf_attempts") or 0) >= MAX_CF_BUSY_ATTEMPTS:
                        state["failed_step"] = "cf_busy"
                        state["error"] = str(exc)
                        say(f"{name}: не вышло пометить аккаунт CF занятым за {state['cf_attempts']} попыток")
                        if on_progress:
                            on_progress()
                        break
                    continue
                except DomainSetupError as exc:
                    if str(exc) not in messages:
                        messages.append(str(exc))
                    if on_progress:
                        on_progress()
                    break
                for line in notes:
                    if line not in messages:
                        messages.append(line)
                if on_progress:
                    on_progress()
                break
        except Exception as exc:
            state["failed_step"] = state.get("failed_step") or "buy"
            state["error"] = str(exc)
            say(f"{name}: {exc}")
            if on_progress:
                on_progress()
    return messages


def remember_account(state: dict[str, Any], account: dict[str, Any]) -> None:
    state["cf_crm_id"] = account.get("id")
    state["cf_account"] = {
        "id": account.get("id"),
        "email": account.get("email"),
        "password": account.get("password"),
        "auth_key": account.get("auth_key"),
        "account_id": account.get("account_id"),
        "first_ns": account.get("first_ns"),
        "second_ns": account.get("second_ns"),
        "status": account.get("status", 0),
    }


def _has_account_slot(
    state: dict[str, Any],
    free: list[dict[str, Any]],
    excluded: list[str] | None = None,
) -> bool:
    banned = {str(item) for item in (excluded or [])}
    stored = state.get("cf_account")
    if isinstance(stored, dict) and stored.get("id") is not None and str(stored.get("id")) not in banned:
        return True
    with _ACCOUNT_LOCK:
        for account in free:
            ident = str(account.get("id"))
            status = account.get("status")
            if status not in (0, "0", None):
                continue
            if ident not in _RESERVED_IDS and ident not in banned:
                return True
    return False


def _ownership(dynadot: Any, name: str) -> tuple[bool, str | None]:
    lookup = getattr(dynadot, "domain_record", None)
    if lookup is None:
        return True, None
    try:
        return lookup(name)
    except DynadotError as exc:
        log_step("dynadot_domain_info_failed", domain=name, error=str(exc))
        return True, None


def _account_for_state(
    state: dict[str, Any],
    free: list[dict[str, Any]],
    excluded: list[str] | None = None,
) -> dict[str, Any] | None:
    banned = {str(item) for item in (excluded or [])}
    stored = state.get("cf_account")
    if isinstance(stored, dict) and stored.get("id") is not None:
        if str(stored.get("id")) not in banned:
            return stored
        state.pop("cf_account", None)
        state["cf_crm_id"] = None
        done = state.setdefault("done", [])
        if "zone" in done:
            done.remove("zone")
        state["zone_id"] = None
        state["ns"] = []
        state["zone_created"] = False
    account = take_free_account(free, banned)
    if account is not None:
        remember_account(state, account)
    return account


def reset_reservations() -> None:
    with _ACCOUNT_LOCK:
        _RESERVED_IDS.clear()

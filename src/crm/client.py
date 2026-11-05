from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from src.crm.domain import (
    build_app_domain_payload,
    build_rules_domain_payload,
    crm_result_error,
    is_domain_not_found,
    is_rules_domain_missing_soft_error,
    normalize_backend_domain,
)
from src.http_errors import transport_failure
from src.workflow.http_log import log_request, log_response, log_step


class CrmError(Exception):
    """Ошибка при работе с ZM Portal CRM API."""


@dataclass
class CreatedApp:
    id: int
    name: str
    raw: dict[str, Any]


class CrmClient:
    def __init__(
        self,
        api_url: str,
        *,
        access_token: Optional[str] = None,
        login: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token
        self.login = login
        self.password = password

    def _headers(self) -> dict[str, str]:
        token = self._ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

    def _ensure_token(self) -> str:
        if self.access_token:
            return self.access_token
        if not self.login or not self.password:
            raise CrmError("CRM не настроен: укажите CRM_ACCESS_TOKEN или CRM_LOGIN/CRM_PASSWORD")
        self.access_token = self.login_with_password(self.login, self.password)
        return self.access_token

    def login_with_password(self, login: str, password: str) -> str:
        data = self._request("POST", "/auth/login", payload={"login": login, "password": password}, auth=False)
        token = data.get("access_token")
        if not token and isinstance(data.get("data"), dict):
            token = data["data"].get("access_token")
        if not token:
            token = data.get("tokenAccess")
        if not token:
            raise CrmError(f"Не удалось получить access_token: {data}")
        return token

    def create_app(self, payload: dict[str, Any]) -> CreatedApp:
        data = self._request("POST", "/app/create", payload=payload)
        model = data.get("model") if isinstance(data, dict) else None
        if not isinstance(model, dict):
            raise CrmError(f"Неожиданный ответ CRM: {data}")

        app_id = model.get("id")
        if app_id is None:
            raise CrmError(f"CRM не вернула id приложения: {data}")

        return CreatedApp(
            id=int(app_id),
            name=str(model.get("apps_name") or payload.get("apps_name") or ""),
            raw=model,
        )

    def grant_app_access(self, app_id: int, team_ids: list[int], user_ids: list[int] | None = None) -> None:
        self._request(
            "POST",
            "/app/access",
            payload={
                "app_id": app_id,
                "team_ids": team_ids,
                "user_ids": user_ids or [],
            },
        )

    def list_apps(self, search: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"search": search, "page": 1, "per-page": 50})
        data = self._request("GET", f"/app/index?{query}")
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        return [item for item in models if isinstance(item, dict)]

    def find_existing_app(
        self,
        *,
        store_id: str | None = None,
        bundle: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        """Найти карточку приложения по bundle/store id, иначе по точному имени."""
        needles = []
        for value in (store_id, bundle, name):
            text = (value or "").strip()
            if text and text not in needles:
                needles.append(text)

        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for needle in needles:
            for item in self.list_apps(needle):
                ident = str(item.get("id"))
                if ident in seen:
                    continue
                seen.add(ident)
                candidates.append(item)

        for item in candidates:
            if _app_id_match(item, store_id=store_id, bundle=bundle):
                return item
        name_norm = (name or "").strip().lower()
        if name_norm:
            for item in candidates:
                app_name = str(item.get("apps_name") or item.get("name") or "").strip().lower()
                if app_name == name_norm:
                    return item
        return None

    def find_catalog_domain(self, domain: str) -> str | None:
        """Точное имя из справочника /domain/index, если домен там уже есть."""
        hostname = normalize_backend_domain(domain)
        if not hostname:
            return None
        query = urllib.parse.urlencode({"search": hostname, "page": 1, "per-page": 50})
        try:
            data = self._request("GET", f"/domain/index?{query}")
        except CrmError as exc:
            log_step("crm_domain_catalog_failed", domain=hostname, error=str(exc))
            return None
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return None
        for item in models:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("domain") or "").strip()
            if normalize_backend_domain(raw) == hostname:
                return raw
        return None

    def set_app_domain(self, app_id: int, domain: str) -> dict[str, Any]:
        """Привязать домен так же, как это делает портал.

        1. Справочник /domain/index — берём каноническое имя, если домен уже заведён.
        2. POST /app/update с app_domain (экран «Настройки бэкенда»).
        3. POST /app/rules-update с полной моделью и полем domain (экран «Правила»).

        Раньше уходил только rules-update, а пустой model.domain в ответе считался
        ошибкой, даже если строка домена в теле была.
        """
        hostname = normalize_backend_domain(domain)
        if not hostname:
            raise CrmError(f"Некорректный домен: {domain}")

        catalog_name = self.find_catalog_domain(hostname)
        saved_name = catalog_name or hostname

        view = self._request("GET", f"/app/view?id={app_id}")
        view_error = crm_result_error(view)
        if view_error:
            raise CrmError(f"CRM app/view: {view_error}")

        update_payload = build_app_domain_payload(view, saved_name)
        log_step(
            "crm_app_domain",
            app_id=app_id,
            domain=saved_name,
            catalog=bool(catalog_name),
            fields=sorted(update_payload.keys()),
        )
        update_result = self._request("POST", f"/app/update?id={app_id}", payload=update_payload)
        update_error = crm_result_error(update_result)
        if update_error:
            raise CrmError(f"CRM app/update: {update_error}")

        rules_view = self._request("GET", f"/app/rules-view?id={app_id}")
        rules_view_error = crm_result_error(rules_view)
        if rules_view_error and not is_rules_domain_missing_soft_error(rules_view):
            raise CrmError(f"CRM rules-view: {rules_view_error}")

        rules_payload = build_rules_domain_payload(rules_view, app_id, saved_name)
        log_step(
            "crm_rules_domain",
            app_id=app_id,
            domain=rules_payload.get("domain"),
            apps_id=rules_payload.get("apps_id"),
        )
        try:
            rules_result = self._request(
                "POST",
                f"/app/rules-update?id={app_id}",
                payload=rules_payload,
            )
        except CrmError as exc:
            if is_domain_not_found(str(exc)) and not catalog_name:
                raise CrmError(
                    f"CRM не нашла домен «{saved_name}» в справочнике. "
                    f"В запрос ушли app_domain и rules.domain={saved_name}. {exc}"
                ) from exc
            raise

        rules_error = crm_result_error(rules_result)
        if rules_error:
            if is_domain_not_found(rules_error) and not catalog_name:
                raise CrmError(
                    f"CRM не нашла домен «{saved_name}» в справочнике. "
                    f"В запрос ушли app_domain и rules.domain={saved_name}. {rules_error}"
                )
            raise CrmError(f"CRM rules-update: {rules_error}")

        return {
            "domain": saved_name,
            "catalog": bool(catalog_name),
            "app_update": update_result,
            "rules": rules_result,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        url = f"{self.api_url}{path}"
        body = None
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        if auth:
            headers.update(self._headers())
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        log_request("crm", method, url, payload)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            log_response("crm", method, url, exc.code, details)
            message = details
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    if parsed.get("message"):
                        message = str(parsed["message"])
                    elif parsed.get("msg_error"):
                        message = str(parsed["msg_error"])
                    elif parsed.get("errors"):
                        message = json.dumps(parsed["errors"], ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            raise CrmError(f"HTTP {exc.code}: {message}") from exc
        except Exception as exc:
            failure = transport_failure(exc)
            if failure is None:
                raise
            log_response("crm", method, url, "error", failure)
            raise CrmError(failure) from exc

        if not raw:
            log_response("crm", method, url, status, {})
            return {}

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_response("crm", method, url, status, raw)
            raise CrmError(f"Некорректный JSON от CRM: {raw[:200]}") from exc

        log_response("crm", method, url, status, data)
        return data


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _app_id_match(
    item: dict[str, Any],
    *,
    store_id: str | None,
    bundle: str | None,
) -> bool:
    apps_bundle = _norm(item.get("apps_bundle"))
    bund = _norm(item.get("bundle"))
    wanted = {_norm(store_id), _norm(bundle)} - {""}
    if not wanted:
        return False
    return bool(wanted & {apps_bundle, bund})

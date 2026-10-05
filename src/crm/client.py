from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from src.workflow.http_log import log_request, log_response


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

    def set_app_domain(self, app_id: int, domain: str) -> Any:
        """Bind backend domain to CRM app the same way UI does.

        1) GET /app/rules-view?id=...
        2) set model.domain
        3) POST /app/rules-update?id=... with full model
        """
        view = self._request("GET", f"/app/rules-view?id={app_id}")
        if isinstance(view, dict) and view.get("error"):
            raise CrmError(view.get("msg_error") or f"CRM rules-view error: {view}")

        model = view.get("model") if isinstance(view, dict) else None
        if not isinstance(model, dict):
            # Fallback: create/update via compact payload if model missing.
            model = {"apps_id": app_id}

        model = dict(model)
        model["domain"] = domain
        model["apps_id"] = app_id

        result = self._request(
            "POST",
            f"/app/rules-update?id={app_id}",
            payload=model,
        )
        if isinstance(result, dict):
            if result.get("error"):
                raise CrmError(result.get("msg_error") or f"CRM rules-update error: {result}")
            if result.get("success") is False:
                raise CrmError(result.get("msg_error") or f"CRM rules-update failed: {result}")
            saved_model = result.get("model")
            if isinstance(saved_model, dict):
                saved_domain = saved_model.get("domain")
                if not saved_domain:
                    raise CrmError(
                        result.get("msg_error")
                        or "CRM не сохранила домен (domain остался пустым)"
                    )
        return result

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
        except urllib.error.URLError as exc:
            log_response("crm", method, url, "error", str(exc.reason))
            raise CrmError(f"Не удалось подключиться к CRM: {exc.reason}") from exc

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

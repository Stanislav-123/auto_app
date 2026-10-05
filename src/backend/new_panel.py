from __future__ import annotations

import html
import http.cookiejar
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .constants import (
    NEW_BACKEND_APPSFLYER_KEY,
    NEW_BACKEND_APPSFLYER_KEY_API_V2,
    NEW_BACKEND_BINOM_URL,
    geo_allowed_new,
)
from src.workflow.http_log import log_request, log_response


class NewBackendError(Exception):
    """Ошибка панели нового бэкенда."""


@dataclass
class NewBackendApp:
    id: int
    app_id: str
    domain: str
    name: str


class NewBackendClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._ctx = ssl.create_default_context()
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=self._ctx),
        )
        self._opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        self._logged_in = False

    def login(self) -> None:
        body = urllib.parse.urlencode(
            {"username": self.username, "password": self.password}
        ).encode("utf-8")
        login_url = f"{self.base_url}/login"
        log_request("backend-new", "POST", login_url, {"username": self.username, "password": "***"})
        request = urllib.request.Request(
            login_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with self._opener.open(request, timeout=60) as response:
                html = response.read().decode("utf-8", "replace")
                final_url = response.geturl()
                status = response.status
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("backend-new", "POST", login_url, exc.code, details)
            raise NewBackendError(f"Login HTTP {exc.code}: {details[:200]}") from exc

        log_response("backend-new", "POST", login_url, status, f"final_url={final_url} html_len={len(html)}")
        if "/login" in final_url and "username" in html and "password" in html:
            raise NewBackendError("Не удалось войти в новый бэкенд (проверьте логин/пароль)")
        self._logged_in = True

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def list_apps(self) -> list[NewBackendApp]:
        self._ensure_login()
        html = self._get(f"{self.base_url}/apps")
        apps: list[NewBackendApp] = []
        for row in re.findall(r"<tr[\s\S]*?</tr>", html, re.I):
            cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, re.I)
            if len(cells) < 4:
                continue
            edit = re.search(r'href=["\']/apps/(\d+)/edit["\']', row)
            if not edit:
                continue
            clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            try:
                apps.append(
                    NewBackendApp(
                        id=int(edit.group(1)),
                        app_id=clean[1],
                        domain=clean[2],
                        name=clean[3],
                    )
                )
            except (ValueError, IndexError):
                continue
        return apps

    def find_app(
        self,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> NewBackendApp:
        apps = self.list_apps()
        name_norm = (name or "").strip().lower()
        domain_norm = (domain or "").strip().lower()
        store_norm = (store_id or "").strip().lower()

        for app in apps:
            if domain_norm and app.domain.lower() == domain_norm:
                return app
        for app in apps:
            if store_norm and app.app_id.lower() == store_norm:
                return app
        for app in apps:
            if name_norm and app.name.lower() == name_norm:
                return app

        raise NewBackendError(
            "Приложение не найдено в новом бэкенде "
            f"(name={name!r}, domain={domain!r}, store_id={store_id!r})"
        )

    def configure_app(self, app_panel_id: int, binom_key: str) -> None:
        """Update required fields on /apps/{id}/edit and submit Save."""
        self._ensure_login()
        if not binom_key:
            raise NewBackendError("Пустой Binom campaign key")

        edit_url = f"{self.base_url}/apps/{app_panel_id}/edit"
        html = self._get(edit_url)
        form_data = _parse_form_fields(html)

        form_data["settings_geo_allowed"] = geo_allowed_new()
        form_data["settings_binom_key"] = binom_key
        form_data["settings_binom_url"] = NEW_BACKEND_BINOM_URL
        form_data["settings_appsflyer_key"] = NEW_BACKEND_APPSFLYER_KEY
        form_data["settings_appsflyer_key_api_v2"] = NEW_BACKEND_APPSFLYER_KEY_API_V2

        body = urllib.parse.urlencode(form_data).encode("utf-8")
        log_request(
            "backend-new",
            "POST",
            edit_url,
            {
                "settings_binom_key": binom_key,
                "settings_binom_url": NEW_BACKEND_BINOM_URL,
                "settings_geo_allowed": f"<geo codes={len(form_data['settings_geo_allowed'].split(','))}>",
                "fields": sorted(form_data.keys()),
            },
        )
        request = urllib.request.Request(
            edit_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
                "Referer": edit_url,
            },
        )
        try:
            with self._opener.open(request, timeout=60) as response:
                raw = response.read()
                log_response(
                    "backend-new",
                    "POST",
                    edit_url,
                    response.status,
                    f"bytes={len(raw)} final_url={response.geturl()}",
                )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("backend-new", "POST", edit_url, exc.code, details)
            raise NewBackendError(f"Save HTTP {exc.code}: {details[:200]}") from exc

    def _get(self, url: str) -> str:
        log_request("backend-new", "GET", url)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with self._opener.open(request, timeout=60) as response:
                html = response.read().decode("utf-8", "replace")
                log_response(
                    "backend-new",
                    "GET",
                    url,
                    response.status,
                    f"html_len={len(html)} final_url={response.geturl()}",
                )
                return html
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("backend-new", "GET", url, exc.code, details)
            raise NewBackendError(f"GET {url} HTTP {exc.code}: {details[:200]}") from exc


def _parse_form_fields(html: str) -> dict[str, str]:
    """Parse first edit form fields (inputs/textareas/selects)."""
    forms = re.findall(r"<form\b([^>]*)>([\s\S]*?)</form>", html, re.I)
    form_html = ""
    for attrs, content in forms:
        action = re.search(r'action=["\']([^"\']*)["\']', attrs, re.I)
        action_value = action.group(1) if action else ""
        if "/apps/" in action_value and action_value.endswith("/edit"):
            form_html = content
            break
    if not form_html and forms:
        # fallback: last non-logout form
        for attrs, content in forms:
            action = re.search(r'action=["\']([^"\']*)["\']', attrs, re.I)
            action_value = action.group(1) if action else ""
            if "logout" not in action_value:
                form_html = content

    if not form_html:
        raise NewBackendError("Не найдена форма редактирования приложения")

    data: dict[str, str] = {}

    for match in re.finditer(
        r'<input\b([^>]*)/?>',
        form_html,
        re.I,
    ):
        attrs = match.group(1)
        name = _attr(attrs, "name")
        if not name:
            continue
        input_type = (_attr(attrs, "type") or "text").lower()
        if input_type in {"submit", "button", "image", "file"}:
            continue
        if input_type in {"checkbox", "radio"}:
            if re.search(r"\bchecked\b", attrs, re.I):
                data[name] = _decode_form_value(_attr(attrs, "value") or "on")
            continue
        data[name] = _decode_form_value(_attr(attrs, "value") or "")

    for match in re.finditer(
        r'<textarea\b([^>]*)>([\s\S]*?)</textarea>',
        form_html,
        re.I,
    ):
        name = _attr(match.group(1), "name")
        if name:
            data[name] = _decode_form_value(match.group(2))

    for match in re.finditer(
        r'<select\b([^>]*)>([\s\S]*?)</select>',
        form_html,
        re.I,
    ):
        name = _attr(match.group(1), "name")
        if not name:
            continue
        selected = re.search(
            r'<option\b([^>]*\bselected\b[^>]*)>([\s\S]*?)</option>',
            match.group(2),
            re.I,
        )
        if selected:
            data[name] = _decode_form_value(
                _attr(selected.group(1), "value")
                or re.sub(r"<[^>]+>", "", selected.group(2)).strip()
            )
        else:
            first = re.search(r'<option\b([^>]*)>', match.group(2), re.I)
            data[name] = _decode_form_value(
                _attr(first.group(1), "value") if first else ""
            )

    return data


def _decode_form_value(value: Optional[str]) -> str:
    """Undo HTML entities from page source (e.g. &#34; / &quot;) before re-POST."""
    if value is None:
        return ""
    return html.unescape(value)


def _attr(attrs: str, key: str) -> Optional[str]:
    match = re.search(rf'\b{key}=["\']([^"\']*)["\']', attrs, re.I)
    if match:
        return match.group(1)
    match = re.search(rf"\b{key}=([^\s>]+)", attrs, re.I)
    return match.group(1) if match else None

from __future__ import annotations

import html
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .constants import NEW_BACKEND_APPSFLYER_KEY, NEW_BACKEND_BINOM_URL, geo_allowed_android
from src.workflow.http_log import log_request, log_response


class AndroidBackendError(Exception):
    """Ошибка панели Android-бэкенда."""


@dataclass
class AndroidBackendApp:
    panel_id: str
    bundle_name: str
    site: str
    edit_url: str


def android_site_from_bundle(bundle: str) -> str:
    """backend.com{bundle_without_leading_com_and_dots}.top"""
    cleaned = (bundle or "").strip().lower()
    if cleaned.startswith("backend.com") and cleaned.endswith(".top"):
        return cleaned
    if cleaned.startswith("com."):
        cleaned = cleaned[4:]
    slug = cleaned.replace(".", "")
    return f"backend.com{slug}.top"


def build_android_organic_link(*, url: str | None = None, key: str | None = None) -> str:
    """Prefer the real Binom campaign URL; only fall back to base+key if URL missing."""
    raw_url = (url or "").strip()
    raw_key = (key or "").strip()
    if raw_url:
        parsed = urlparse(raw_url)
        if parsed.scheme in {"http", "https"}:
            query = parse_qs(parsed.query)
            if query.get("key") or not raw_key:
                return raw_url
            sep = "&" if parsed.query else "?"
            return f"{raw_url}{sep}key={raw_key}"
    if raw_key:
        # DEV/fallback only — production should pass campaign URL from Binom.
        return f"{NEW_BACKEND_BINOM_URL}?key={raw_key}"
    return raw_url


class AndroidBackendClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._ctx = ssl.create_default_context()
        token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth_header = f"Basic {token}"

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {
            "Authorization": self._auth_header,
            "User-Agent": "Mozilla/5.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def list_apps(self) -> list[AndroidBackendApp]:
        html = self._get(f"{self.base_url}/sites")
        apps: list[AndroidBackendApp] = []
        seen: set[str] = set()
        for row in re.findall(r"<tr[\s\S]*?</tr>", html, re.I):
            links = re.findall(
                r'href=["\']([^"\']*/edit/([^"\']+))["\'][^>]*>([^<]+)',
                row,
                re.I,
            )
            if not links:
                continue
            href, site, _label = links[0]
            site = site.strip("/")
            if site in seen:
                continue
            seen.add(site)
            texts = [item[2].strip() for item in links]
            bundle = next(
                (text for text in texts if text and not text.startswith("backend.")),
                "",
            )
            apps.append(
                AndroidBackendApp(
                    panel_id="",
                    bundle_name=bundle,
                    site=site,
                    edit_url=self._abs_url(href),
                )
            )
        return apps

    def find_app(self, bundle: str) -> AndroidBackendApp:
        bundle_norm = (bundle or "").strip().lower()
        if not bundle_norm:
            raise AndroidBackendError("Пустой bundle для Android-бэкенда")

        site = android_site_from_bundle(bundle_norm)
        try:
            html = self._get(f"{self.base_url}/edit/{site}")
            form = _parse_form_fields(html)
            panel_id = form.get("id") or ""
            return AndroidBackendApp(
                panel_id=panel_id,
                bundle_name=form.get("bundle_name") or bundle_norm,
                site=form.get("site") or site,
                edit_url=f"{self.base_url}/edit/{site}",
            )
        except AndroidBackendError as exc:
            if "404" not in str(exc):
                raise


        for app in self.list_apps():
            if app.bundle_name.lower() == bundle_norm or app.site.lower() == site:
                html = self._get(app.edit_url)
                form = _parse_form_fields(html)
                app.panel_id = form.get("id") or ""
                app.bundle_name = form.get("bundle_name") or app.bundle_name
                app.site = form.get("site") or app.site
                return app

        raise AndroidBackendError(
            f"Приложение не найдено в Android-бэкенде (bundle={bundle!r}, site={site!r})"
        )

    def configure_app(
        self,
        bundle: str,
        *,
        fb_app_id: str,
        fb_access_token: str,
        fb_decryption_key: str,
        organic_link: str,
    ) -> AndroidBackendApp:
        app = self.find_app(bundle)
        html = self._get(app.edit_url)
        form_data = _parse_form_fields(html)
        if not form_data.get("id"):
            raise AndroidBackendError("В форме Android-бэкенда нет скрытого id")

        if not form_data.get("bundle_name"):
            form_data["bundle_name"] = app.bundle_name or bundle
        if not form_data.get("site"):
            form_data["site"] = app.site or android_site_from_bundle(bundle)

        form_data["fb_app_id"] = fb_app_id.strip()
        form_data["fb_access_token"] = fb_access_token.strip()
        form_data["fb_decryption_key"] = fb_decryption_key.strip()
        form_data["apps_devkey"] = NEW_BACKEND_APPSFLYER_KEY
        form_data["organic_link"] = organic_link.strip()
        form_data["mmp_used"] = "0"
        form_data["geo_allowed"] = geo_allowed_android()

        save_url = f"{self.base_url}/save"
        body = urllib.parse.urlencode(form_data).encode("utf-8")
        log_request(
            "backend-android",
            "POST",
            save_url,
            {
                "id": form_data.get("id"),
                "bundle_name": form_data.get("bundle_name"),
                "site": form_data.get("site"),
                "fb_app_id": form_data.get("fb_app_id"),
                "organic_link": form_data.get("organic_link"),
                "mmp_used": form_data.get("mmp_used"),
                "geo_allowed": f"<geo codes={len(str(form_data.get('geo_allowed', '')).split())}>",
            },
        )
        request = urllib.request.Request(
            save_url,
            data=body,
            method="POST",
            headers=self._headers(
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": app.edit_url,
                }
            ),
        )
        try:
            with urllib.request.urlopen(request, timeout=60, context=self._ctx) as response:
                raw = response.read()
                log_response(
                    "backend-android",
                    "POST",
                    save_url,
                    response.status,
                    f"bytes={len(raw)} final_url={response.geturl()}",
                )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("backend-android", "POST", save_url, exc.code, details)
            raise AndroidBackendError(f"Save HTTP {exc.code}: {details[:200]}") from exc

        app.panel_id = form_data["id"]
        app.bundle_name = form_data.get("bundle_name") or app.bundle_name
        app.site = form_data.get("site") or app.site
        return app

    def _abs_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        origin = self.base_url.split("/qize4nmp")[0]
        if path.startswith("/"):
            return f"{origin}{path}"
        return f"{self.base_url}/{path}"

    def _get(self, url: str) -> str:
        log_request("backend-android", "GET", url)
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=60, context=self._ctx) as response:
                html = response.read().decode("utf-8", "replace")
                log_response(
                    "backend-android",
                    "GET",
                    url,
                    response.status,
                    f"html_len={len(html)}",
                )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            log_response("backend-android", "GET", url, exc.code, details)
            raise AndroidBackendError(f"GET {url} HTTP {exc.code}: {details[:200]}") from exc
        if "name=\"username\"" in html and "login" in html.lower():
            raise AndroidBackendError("Android-бэкенд вернул страницу логина (проверьте Basic Auth)")
        return html


def _parse_form_fields(html: str) -> dict[str, str]:
    forms = re.findall(r"<form\b([^>]*)>([\s\S]*?)</form>", html, re.I)
    form_html = ""
    for attrs, content in forms:
        action = re.search(r'action=["\']([^"\']*)["\']', attrs, re.I)
        action_value = action.group(1) if action else ""
        if action_value.endswith("/save") or "/save" in action_value:
            form_html = content
            break
    if not form_html and forms:
        form_html = forms[0][1]
    if not form_html:
        raise AndroidBackendError("Не найдена форма редактирования Android-приложения")

    data: dict[str, str] = {}
    for match in re.finditer(r"<input\b([^>]*)/?>", form_html, re.I):
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

    for match in re.finditer(r"<textarea\b([^>]*)>([\s\S]*?)</textarea>", form_html, re.I):
        name = _attr(match.group(1), "name")
        if name:
            data[name] = _decode_form_value(match.group(2))

    for match in re.finditer(r"<select\b([^>]*)>([\s\S]*?)</select>", form_html, re.I):
        name = _attr(match.group(1), "name")
        if not name:
            continue
        selected = re.search(
            r"<option\b([^>]*\bselected\b[^>]*)>([\s\S]*?)</option>",
            match.group(2),
            re.I,
        )
        if selected:
            data[name] = _decode_form_value(
                _attr(selected.group(1), "value")
                or re.sub(r"<[^>]+>", "", selected.group(2)).strip()
            )
        else:
            first = re.search(r"<option\b([^>]*)>", match.group(2), re.I)
            data[name] = _decode_form_value(
                _attr(first.group(1), "value") if first else ""
            )
    return data


def _decode_form_value(value: Optional[str]) -> str:
    if value is None:
        return ""
    return html.unescape(value)


def _attr(attrs: str, key: str) -> Optional[str]:
    match = re.search(rf'\b{key}=["\']([^"\']*)["\']', attrs, re.I)
    if match:
        return match.group(1)
    match = re.search(rf"\b{key}=([^\s>]+)", attrs, re.I)
    return match.group(1) if match else None

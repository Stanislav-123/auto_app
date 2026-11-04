"""Firebase console через Playwright: постоянный профиль Chrome, вход в Google руками один раз."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .key import FcmKeyError, extract_invite_links, parse_service_account, project_ref

logger = logging.getLogger(__name__)

CONSOLE = "https://console.firebase.google.com"
GMAIL = "https://mail.google.com/mail/u/0/"

_GENERATE_RE = re.compile(r"generate new private key|создать новый закрытый ключ|сгенерировать новый закрытый ключ", re.I)
_CONFIRM_RE = re.compile(r"^\s*(generate key|создать ключ|сгенерировать ключ)\s*$", re.I)
_ACCEPT_RE = re.compile(r"^\s*(accept|accept invitation|принять|принять приглашение)\s*$", re.I)


def profile_dir() -> Path:
    return Path((os.getenv("GOOGLE_PROFILE_DIR") or "data/google_profile").strip() or "data/google_profile")


def headless() -> bool:
    return (os.getenv("FB_HEADLESS") or "0").strip().lower() not in {"0", "false", "no", "off"}


def timeout_ms() -> int:
    try:
        return max(5000, int((os.getenv("FB_TIMEOUT_MS") or "45000").strip()))
    except ValueError:
        return 45000


def open_context(pw, *, visible: bool | None = None):
    path = profile_dir()
    path.mkdir(parents=True, exist_ok=True)
    args = {
        "headless": (not visible) if visible is not None else headless(),
        "accept_downloads": True,
        "viewport": {"width": 1440, "height": 900},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        return pw.chromium.launch_persistent_context(str(path), channel="chrome", **args)
    except Exception:
        logger.info("fb_chrome_missing fallback=chromium")
        return pw.chromium.launch_persistent_context(str(path), **args)


class FirebaseConsole:
    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.page = None

    def fetch_key(self, firebase_name: str | None) -> dict:
        project_id, name = project_ref(firebase_name)
        if not project_id and not name:
            raise FcmKeyError("В сообщении нет «Название в Firebase».")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            context = open_context(pw)
            try:
                context.set_default_timeout(timeout_ms())
                self.page = context.pages[0] if context.pages else context.new_page()
                try:
                    return self._fetch(project_id, name)
                except FcmKeyError as exc:
                    if not exc.screenshot:
                        exc.screenshot = self._shot("fcm")
                    raise
                except Exception as exc:
                    raise FcmKeyError(f"Firebase: {exc}", self._shot("fcm")) from exc
            finally:
                context.close()

    def _fetch(self, project_id: str | None, name: str | None) -> dict:
        self._goto(CONSOLE)
        self._require_login()
        found = self._find_project(project_id, name)
        if not found:
            logger.info("fb_project_not_visible name=%s", name or project_id)
            self._accept_invite(name or project_id or "")
            self._goto(CONSOLE)
            found = self._find_project(project_id, name)
        if not found:
            raise FcmKeyError(f"Проект «{name or project_id}» не виден в Firebase и приглашение не нашлось.")
        return self._generate(found)

    def _goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

    def _require_login(self) -> None:
        if "accounts.google.com" in (self.page.url or ""):
            raise FcmKeyError("Нет входа в Google. Запустите python scripts/google_login.py и войдите вручную.")

    def _find_project(self, project_id: str | None, name: str | None) -> str | None:
        if project_id:
            self._goto(f"{CONSOLE}/project/{project_id}/overview")
            self._require_login()
            if f"/project/{project_id}" in self.page.url and not self._looks_denied():
                return project_id
            if not name:
                return None
            self._goto(CONSOLE)
        card = self.page.get_by_text(re.compile(rf"^\s*{re.escape(name)}\s*$", re.I))
        try:
            card.first.wait_for(timeout=8000)
        except Exception:
            return None
        card.first.click()
        self.page.wait_for_url(re.compile(r"/project/[a-z0-9-]+"), timeout=timeout_ms())
        match = re.search(r"/project/([a-z0-9-]+)", self.page.url)
        return match.group(1) if match else None

    def _looks_denied(self) -> bool:
        try:
            text = self.page.inner_text("body").lower()
        except Exception:
            return False
        return any(t in text for t in ("permission", "don't have access", "нет доступа", "not found"))

    def _accept_invite(self, name: str) -> None:
        links = self._invite_links_in_console() or self._invite_links_in_gmail(name)
        for link in links[:3]:
            logger.info("fb_invite_open")
            self._goto(link)
            self._require_login()
            button = self.page.get_by_role("button", name=_ACCEPT_RE)
            if button.count():
                button.first.click()
                self.page.wait_for_timeout(5000)
                logger.info("fb_invite_accepted")
                return

    def _invite_links_in_console(self) -> list[str]:
        try:
            return extract_invite_links(self.page.content())
        except Exception:
            return []

    def _invite_links_in_gmail(self, name: str) -> list[str]:
        query = quote(f'invited "{name}"' if name else "invited firebase")
        self._goto(f"{GMAIL}#search/{query}")
        self._require_login()
        rows = self.page.locator("tr.zA")
        try:
            rows.first.wait_for(timeout=10000)
        except Exception:
            return []
        rows.first.click()
        self.page.wait_for_timeout(3000)
        hrefs = self.page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        return extract_invite_links("\n".join(hrefs))

    def _generate(self, project_id: str) -> dict:
        self._goto(f"{CONSOLE}/project/{project_id}/settings/serviceaccounts/adminsdk")
        self._require_login()
        button = self.page.get_by_role("button", name=_GENERATE_RE)
        button.first.wait_for(timeout=timeout_ms())
        button.first.click()
        confirm = self.page.get_by_role("button", name=_CONFIRM_RE)
        confirm.first.wait_for(timeout=timeout_ms())
        with self.page.expect_download(timeout=timeout_ms()) as info:
            confirm.first.click()
        download = info.value
        path = download.path()
        try:
            data = parse_service_account(Path(path).read_bytes())
        finally:
            try:
                download.delete()
            except Exception:
                pass
        if data.get("project_id") != project_id:
            raise FcmKeyError(f"Скачанный ключ от другого проекта: {data.get('project_id')}")
        logger.info("fb_key_generated project=%s", project_id)
        return data

    def _shot(self, kind: str) -> str | None:
        if self.page is None:
            return None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"fb-{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
            self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return None


def fetch_fcm_key(firebase_name: str | None) -> dict:
    try:
        return FirebaseConsole().fetch_key(firebase_name)
    except ImportError as exc:
        raise FcmKeyError("Не установлен playwright.") from exc

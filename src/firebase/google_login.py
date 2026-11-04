"""Вход в Google в постоянном профиле: почта и пароль из .env, 2FA ждём с телефона."""

from __future__ import annotations

import logging
import os
import re
import time

from .key import FcmKeyError

logger = logging.getLogger(__name__)

_BLOCKED = (
    "this browser or app may not be secure",
    "браузер или приложение могут быть небезопасны",
    "couldn't sign you in",
    "не удалось войти в аккаунт",
    "type the text you hear or see",
    "введите символы",
    "unusual traffic",
)
_CHALLENGE = (
    "2-step verification",
    "двухэтапная аутентификация",
    "check your phone",
    "проверьте телефон",
    "verify it's you",
    "подтвердите, что это вы",
    "tap yes",
    "нажмите «да»",
    "open the gmail app",
    "enter a phone number",
    "введите номер телефона",
)
EMAIL_INPUT = "#identifierId, input[type=email], input[name=identifier]"
PASSWORD_INPUT = "input[name=Passwd], input[type=password]:not([name=hiddenPassword])"
_WRONG_PASSWORD = ("wrong password", "неверный пароль")


def credentials() -> tuple[str, str]:
    return (os.getenv("GOOGLE_EMAIL") or "").strip(), (os.getenv("GOOGLE_PASSWORD") or "").strip()


def classify(url: str, text: str, *, has_email: bool = False, has_password: bool = False) -> str:
    """console / blocked / wrong_password / challenge / password / email / chooser / unknown."""
    url = (url or "").lower()
    blob = (text or "").lower()
    if "accounts.google.com" not in url and "console.firebase.google.com" in url:
        return "console"
    if any(token in blob for token in _BLOCKED) or "/signin/rejected" in url:
        return "blocked"
    if any(token in blob for token in _WRONG_PASSWORD):
        return "wrong_password"
    if "/challenge/" in url and "/challenge/pwd" not in url or any(token in blob for token in _CHALLENGE):
        return "challenge"
    if has_password:
        return "password"
    if has_email:
        return "email"
    if "choose an account" in blob or "выберите аккаунт" in blob:
        return "chooser"
    return "unknown"


def sign_in(page, target_url: str, *, wait_2fa_s: int = 120, shot=None) -> None:
    """Довести страницу до консоли. Пароль нигде не логируется."""
    email, password = credentials()
    deadline = time.monotonic() + wait_2fa_s + 60
    email_tries = password_tries = 0
    told_challenge = False
    while time.monotonic() < deadline:
        page = _live_page(page)
        state = _state(page)
        if state == "console":
            logger.info("google_signed_in")
            return
        if state == "blocked":
            raise FcmKeyError("Google не пускает: браузер считает небезопасным или капча.", shot() if shot else None)
        if state == "wrong_password":
            raise FcmKeyError("Google: неверный пароль из GOOGLE_PASSWORD.", shot() if shot else None)
        if state in {"email", "password", "chooser"} and not (email and password):
            raise FcmKeyError("Нет входа в Google, а GOOGLE_EMAIL/GOOGLE_PASSWORD не заданы.", shot() if shot else None)
        if state == "chooser":
            page.get_by_text(email, exact=False).first.click()
        elif state == "email":
            if email_tries >= 3:
                raise FcmKeyError("Google не принимает почту: после ввода снова просит адрес.", shot() if shot else None)
            field = _first_visible(page, EMAIL_INPUT)
            field.fill(email)
            _next(page, field)
            email_tries += 1
            page.wait_for_timeout(3000)
        elif state == "password":
            if password_tries >= 2:
                raise FcmKeyError("Google снова просит пароль после ввода.", shot() if shot else None)
            field = _first_visible(page, PASSWORD_INPUT)
            field.fill(password)
            _next(page, field)
            password_tries += 1
            page.wait_for_timeout(3000)
        elif state == "challenge" and not told_challenge:
            logger.info("google_challenge waiting=%ss", wait_2fa_s)
            print(f"Google просит подтверждение (2FA). Жду до {wait_2fa_s} секунд, подтвердите на телефоне.", flush=True)
            told_challenge = True
            deadline = time.monotonic() + wait_2fa_s
        elif state == "unknown" and "accounts.google.com" not in (page.url or ""):
            try:
                page.goto(target_url, wait_until="domcontentloaded")
            except Exception:
                pass
        try:
            page.wait_for_timeout(2000)
        except Exception:
            if not page.is_closed():
                raise
    raise FcmKeyError("Не дождался входа в Google (2FA не подтвердили или страница зависла).", shot() if shot else None)


def _live_page(page):
    """Браузер мог закрыть вкладку и открыть новую — берём последнюю живую."""
    if not page.is_closed():
        return page
    pages = [item for item in page.context.pages if not item.is_closed()]
    if not pages:
        raise FcmKeyError("Окно браузера закрылось до входа в Google.")
    return pages[-1]


def _state(page) -> str:
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    has_email = _visible(page, EMAIL_INPUT)
    has_password = _visible(page, PASSWORD_INPUT)
    return classify(page.url or "", text, has_email=has_email, has_password=has_password)


def _first_visible(page, selector: str):
    for item in page.locator(selector).all():
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _visible(page, selector: str) -> bool:
    try:
        return _first_visible(page, selector) is not None
    except Exception:
        return False


def _next(page, field) -> None:
    """Enter в поле: кнопку «Далее» у Google часто перекрывает прозрачный слой."""
    field.press("Enter")

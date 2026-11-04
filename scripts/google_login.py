#!/usr/bin/env python3
"""Вход в Google в профиле бота. Почта и пароль из .env (GOOGLE_EMAIL, GOOGLE_PASSWORD), 2FA — с телефона."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from playwright.sync_api import sync_playwright

from src.firebase.console import CONSOLE, open_context, profile_dir
from src.firebase.google_login import sign_in
from src.firebase.key import FcmKeyError


def main() -> int:
    manual = "--manual" in sys.argv
    with sync_playwright() as pw:
        context = open_context(pw, visible=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(45000)
        page.goto(CONSOLE, wait_until="domcontentloaded")
        print(f"Профиль: {profile_dir()}")
        if manual:
            print("Войдите в Google в окне, дождитесь консоли Firebase и нажмите Enter здесь.")
            input()
            context.close()
            return 0
        shot_path = Path("logs") / "google-login.png"

        def shot():
            shot_path.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(shot_path), full_page=True)
            return str(shot_path)

        try:
            sign_in(page, CONSOLE, shot=shot)
        except FcmKeyError as exc:
            print(f"Не вошёл: {exc}")
            if exc.screenshot:
                print(f"Скриншот: {exc.screenshot}")
            context.close()
            return 1
        page.wait_for_timeout(3000)
        print(f"Готово, консоль Firebase открыта: {page.url}")
        context.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

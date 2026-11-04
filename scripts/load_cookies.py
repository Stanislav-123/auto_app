#!/usr/bin/env python3
"""Загрузить куки Google из файла в профиль бота и проверить вход в Firebase console."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from playwright.sync_api import sync_playwright

from src.firebase.console import CONSOLE, open_context, profile_dir
from src.firebase.cookies import add_cookies_from_file, cookies_file
from src.firebase.google_login import credentials, _state


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else cookies_file()
    if not path:
        print("Не задан файл кук: аргумент или GOOGLE_COOKIES_FILE в .env.")
        return 2
    email, _ = credentials()
    with sync_playwright() as pw:
        context = open_context(pw, visible=True)
        added = add_cookies_from_file(context, path)
        print(f"Профиль: {profile_dir()}; загружено кук: {added}")
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(45000)
        page.goto(CONSOLE, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        state = _state(page)
        signed = state == "console" and "accounts.google.com" not in (page.url or "")
        if signed:
            try:
                body = page.inner_text("body")
            except Exception:
                body = ""
            seen = email and email in body
            print(f"Firebase console открыта. Аккаунт {email} на странице: {'да' if seen else 'не видно, но вход есть'}")
        else:
            print(f"Не залогинен: состояние {state}, url {page.url}")
        context.close()
        return 0 if signed else 1


if __name__ == "__main__":
    raise SystemExit(main())

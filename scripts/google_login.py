#!/usr/bin/env python3
"""Один раз: открыть окно Chrome с профилем бота и войти в Google руками."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from playwright.sync_api import sync_playwright

from src.firebase.console import CONSOLE, open_context, profile_dir


def main() -> None:
    with sync_playwright() as pw:
        context = open_context(pw, visible=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(CONSOLE)
        print(f"Профиль: {profile_dir()}")
        print("Войдите в Google в открывшемся окне, дождитесь консоли Firebase и нажмите Enter здесь.")
        input()
        context.close()
        print("Готово, сессия сохранена в профиле.")


if __name__ == "__main__":
    main()

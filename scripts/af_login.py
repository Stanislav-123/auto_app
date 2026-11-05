#!/usr/bin/env python3
"""Ручной вход в AppsFlyer HQ: Google / SSO / пароль в окне Chrome, потом сохранение data/af_state.json."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# Force manual headed login for this script.
os.environ["AF_LOGIN_MODE"] = "manual"
os.environ["AF_HEADLESS"] = "0"


def main() -> int:
    from src.appsflyer.playwright_session import PlaywrightAppsFlyer
    from src.appsflyer.session import AfBlocked, AfError, mask_email

    email = (os.getenv("AF_EMAIL") or "").strip()
    print(f"Открываю AppsFlyer. Аккаунт из AF_EMAIL: {mask_email(email) or '(не задан)'}")
    print("Войдите в окне Chrome (Google / SSO / пароль). После входа в кабинет окно закроется само.")
    try:
        with PlaywrightAppsFlyer.from_env() as session:
            session.ensure_session()
            print(f"Готово. Сессия: {session.state_file}")
            print(f"URL: {session.page.url if session.page else ''}")
    except (AfError, AfBlocked) as exc:
        print(f"Не вошёл: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

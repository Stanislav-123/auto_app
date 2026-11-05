from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

_AF_LOCK = threading.Lock()

LOGIN_URL = "https://hq1.appsflyer.com/auth/login"


class AfError(Exception):
    """Ошибка настройки AppsFlyer, по одному приложению или до старта."""


class AfBlocked(AfError):
    """2FA или капча. Дальше по списку не идём."""

    def __init__(self, message: str, results: list[ShareResult] | None = None) -> None:
        super().__init__(message)
        self.results = results or []


@dataclass
class ShareOutcome:
    status: str
    detail: str = ""
    links: tuple[str, str] | None = None


@dataclass
class ShareResult:
    app: str
    status: str
    reason: str = ""
    links: tuple[str, str] | None = None


@dataclass
class ShareTask:
    kind: str
    partner_id: str | None = None
    ids: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    pairs: list[tuple[str, str]] = field(default_factory=list)
    replace_game_id: bool = False


def partner_config_url(app: str, partner_id: str) -> str:
    return f"https://hq1.appsflyer.com/marketplace/integrated-partners/{app}/{partner_id}"


def login_blocked_reason(text: str) -> str | None:
    """Текст страницы, если AppsFlyer просит 2FA или капчу."""
    blob = (text or "").lower()
    if any(token in blob for token in ("recaptcha", "hcaptcha", "g-recaptcha", "i'm not a robot", "i am not a robot")):
        return "На странице капча. Войдите в AppsFlyer вручную и повторите команду."
    if any(
        token in blob
        for token in (
            "two-factor",
            "two factor",
            "2-step verification",
            "verification code",
            "authenticator",
            "enter the code",
            "one-time password",
            "одноразов",
        )
    ):
        return "AppsFlyer просит код 2FA. Введите его вручную и повторите команду."
    return None


def mask_email(email: str | None) -> str | None:
    """bd@example.com -> bd***@example.com. Без полного адреса в логах и ошибках."""
    value = (email or "").strip()
    if not value or "@" not in value:
        return None
    local, _, domain = value.partition("@")
    if len(local) >= 2:
        return f"{local[:2]}***@{domain}"
    return f"***@{domain}"


def login_mode() -> str:
    """password (default) или manual — пользователь сам жмёт Google/SSO/пароль в окне."""
    raw = (os.getenv("AF_LOGIN_MODE") or "password").strip().lower()
    if raw in {"manual", "interactive", "browser", "sso", "google"}:
        return "manual"
    return "password"


def login_auth_error_reason(text: str, email: str | None = None) -> str | None:
    """Неверный логин/пароль или аккаунт заблокирован на форме входа."""
    blob = (text or "").lower()
    if any(
        token in blob
        for token in (
            "invalid username or password",
            "incorrect username or password",
            "incorrect email or password",
            "wrong email or password",
            "wrong password",
            "invalid credentials",
            "authentication failed",
            "неверн",
            "неправильн",
        )
    ):
        who = mask_email(email)
        suffix = f" для {who}" if who else ""
        return (
            f"Неверный логин или пароль AppsFlyer{suffix}. "
            "Бот жмёт обычный Email/Password, не «Log in with Google» и не SSO. "
            "Если в Chrome вы входите через Google — пароль в .env тут ни при чём: "
            "либо задайте рабочий пароль именно для AF_EMAIL, либо AF_HEADLESS=0 и "
            "AF_LOGIN_MODE=manual (войти вручную один раз, сессия уйдёт в data/af_state.json)."
        )
    if any(
        token in blob
        for token in (
            "account locked",
            "account is locked",
            "too many attempts",
            "temporarily locked",
            "account disabled",
        )
    ):
        return "Аккаунт AppsFlyer заблокирован или временно недоступен. Войдите вручную в браузере."
    return None


def run_share(task: ShareTask, session: Any) -> list[ShareResult]:
    """По приложениям по очереди. Ошибка одного не останавливает остальные, 2FA останавливает."""
    prepare = getattr(session, "ensure_session", None)
    if prepare is not None:
        prepare()
    results: list[ShareResult] = []
    items = list(task.pairs) if task.kind == "unity" else [(app, None) for app in task.apps]
    for app, extra in items:
        try:
            outcome = _configure(session, task, app, extra)
        except AfBlocked as exc:
            results.append(ShareResult(app, "failed", str(exc)))
            raise AfBlocked(str(exc), results) from exc
        except Exception as exc:
            path = _capture(session, app, task.kind)
            reason = str(exc)
            if path:
                reason = f"{reason} (скриншот {path})"
            results.append(ShareResult(app, "failed", reason))
            continue
        results.append(ShareResult(app, outcome.status, outcome.detail, outcome.links))
    return results


def execute_share(task: ShareTask) -> list[ShareResult]:
    email = (os.getenv("AF_EMAIL") or "").strip()
    password = (os.getenv("AF_PASSWORD") or "").strip()
    if not email or not password:
        raise AfError("Не заданы AF_EMAIL и AF_PASSWORD.")
    if not _AF_LOCK.acquire(timeout=1):
        raise AfError("Расшарка AppsFlyer уже выполняется. Дождитесь окончания.")
    try:
        from src.appsflyer.playwright_session import PlaywrightAppsFlyer

        with PlaywrightAppsFlyer.from_env() as session:
            return run_share(task, session)
    except ImportError as exc:
        raise AfError("Не установлен playwright. Нужны pip install playwright и playwright install chromium.") from exc
    finally:
        _AF_LOCK.release()


def headless_enabled() -> bool:
    raw = (os.getenv("AF_HEADLESS") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def state_path() -> str:
    raw = (os.getenv("AF_STATE_PATH") or "data/af_state.json").strip()
    return raw or "data/af_state.json"


def timeout_ms() -> int:
    raw = (os.getenv("AF_TIMEOUT_MS") or "45000").strip()
    try:
        return max(5000, int(raw))
    except ValueError:
        return 45000


def _configure(session: Any, task: ShareTask, app: str, extra: str | None) -> ShareOutcome:
    if task.kind == "agency":
        return session.configure_agency(app, task.partner_id)
    if task.kind == "tiktok":
        return session.configure_tiktok(app, list(task.ids))
    if task.kind == "unity":
        return session.configure_unity(app, extra or "", replace=task.replace_game_id)
    if task.kind == "snapchat":
        return session.configure_snapchat(app, list(task.ids))
    raise AfError(f"Неизвестный тип расшарки: {task.kind}")


def _capture(session: Any, app: str, kind: str) -> str:
    capture = getattr(session, "capture_failure", None)
    if capture is None:
        return ""
    try:
        return str(capture(app, kind) or "")
    except Exception:
        return ""


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "app").strip("._")
    return (cleaned or "app")[:80]

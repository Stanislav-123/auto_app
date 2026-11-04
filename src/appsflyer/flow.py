from __future__ import annotations

import time
from dataclasses import dataclass, field
from html import escape

from src.appsflyer.parse import ParseError, ParsedShare, parse_apps, parse_share, partner_from_token, strip_share_command
from src.appsflyer.session import ShareResult, ShareTask

_WAITING = {"partner", "ids", "apps", "pairs"}

_APPS = (
    "Android: bundle или ссылка Google Play. "
    "iOS: ссылка App Store или iTunes, <code>id1234567890</code> либо 9–12 цифр. "
    "Несколько — столбиком, через пробел или запятую."
)

_PROMPTS = {
    ("agency", "partner"): (
        "Пришлите id агентства AppsFlyer или ссылку на партнёра.\n"
        "Потом отдельно — приложения Android или iOS."
    ),
    ("agency", "apps"): "Пришлите приложения.\n" + _APPS,
    ("tiktok", "ids"): "Пришлите TikTok App ID, один или несколько. Это числа из 16–20 цифр.",
    ("tiktok", "apps"): "Пришлите приложения для TikTok.\n" + _APPS,
    ("unity", "pairs"): (
        "Пришлите Unity Ads Game ID и приложение Android или iOS.\n"
        "Одна пара в строке, либо один game id и дальше список приложений.\n"
        "Пример:\n<code>100000001 io.example.demo</code>\n"
        "<code>100000001 id1234567890</code>"
    ),
    ("snapchat", "ids"): "Пришлите Snap App ID (UUID), один или несколько.",
    ("snapchat", "apps"): "Пришлите приложения для Snapchat.\n" + _APPS,
}


@dataclass
class FlowStep:
    job: dict
    messages: list[str] = field(default_factory=list)
    task: ShareTask | None = None
    buttons: list[tuple[str, str]] | None = None


def main_menu_buttons() -> list[tuple[str, str]]:
    return [
        ("Заведение приложений", "menu:app"),
        ("Домены", "menu:domains"),
        ("Расшарка", "menu:share"),
    ]


def domain_menu_buttons() -> list[tuple[str, str]]:
    return [
        ("Массовая покупка", "menu:domain:bulk"),
        ("Покупка конкретного", "menu:domain:specific"),
        ("Только настройка", "menu:domain:setup"),
    ]


def share_menu_buttons() -> list[tuple[str, str]]:
    return [
        ("Агентский аккаунт", "menu:share:agency"),
        ("TikTok", "menu:share:tiktok"),
        ("Unity", "menu:share:unity"),
        ("Snapchat", "menu:share:snapchat"),
    ]


def menu_route(data: str) -> dict | None:
    routes = {
        "menu:app": {"flow": "app"},
        "menu:domains": {"flow": "domains"},
        "menu:share": {"flow": "share"},
        "menu:domain:bulk": {"flow": "domain", "mode": "bulk"},
        "menu:domain:specific": {"flow": "domain", "mode": "specific"},
        "menu:domain:setup": {"flow": "domain", "mode": "setup"},
        "menu:share:agency": {"flow": "share", "kind": "agency"},
        "menu:share:tiktok": {"flow": "share", "kind": "tiktok"},
        "menu:share:unity": {"flow": "share", "kind": "unity"},
        "menu:share:snapchat": {"flow": "share", "kind": "snapchat"},
        "share:cancel": {"flow": "cancel"},
    }
    return routes.get(data)


def help_text() -> str:
    return (
        "<b>Команды</b>\n"
        "/app — завод приложения. Текст можно сразу или следующим сообщением.\n"
        "/domain — покупка и настройка доменов.\n"
        "/menu — кнопки: завод, домены, расшарка.\n\n"
        "<b>Расшарка AppsFlyer</b>\n"
        "Приложения Android или iOS. Android: bundle <code>io.example.demo</code> или ссылка "
        "Google Play. iOS: <code>https://apps.apple.com/us/app/some-name/id1234567890</code>, "
        "ссылка iTunes, <code>id1234567890</code> или 9–12 цифр. "
        "В AppsFlyer iOS id идёт с префиксом id. "
        "Несколько — с новой строки, через пробел или запятую. По каждому приложению будет ответ: "
        "готово, уже настроено или ошибка.\n\n"
        "/share_partner &lt;id агентства или ссылка AF&gt; &lt;приложения&gt;\n"
        "Права агентства: все тумблеры, события af_app_opened, af_complete_registration, "
        "af_purchase, отправка со всех источников включая organic.\n\n"
        "/share_tiktok &lt;TikTok App ID...&gt; &lt;приложения&gt;\n"
        "Партнёр tiktokglobal_int. ID не дублируются. События: LaunchAPP, Registration, Purchase.\n\n"
        "/share_unity &lt;game id&gt; &lt;приложение&gt;\n"
        "Несколько приложений — пара в каждой строке: game id и приложение Android или iOS. "
        "Партнёр unityads_int. В ответ придут click и impression ссылки.\n\n"
        "/share_snapchat &lt;Snap App ID...&gt; &lt;приложения&gt;\n"
        "Партнёр snapchat_int. События: APP_OPEN, SIGN_UP, PURCHASE.\n\n"
        "Кнопка «Домены»: массовая покупка, покупка конкретного, только настройка.\n"
        "Кнопка «Расшарка»: агентский аккаунт, TikTok, Unity, Snapchat. "
        "Бот спросит id и приложения по шагам. Отмена — кнопка или слово «отмена».\n\n"
        "Вход в AppsFlyer: AF_EMAIL и AF_PASSWORD в .env. Сессия лежит в data/af_state.json. "
        "Если вылезет 2FA или капча, бот остановится."
    )


def share_expects_text(job: dict | None) -> bool:
    return bool(job) and job.get("stage") in _WAITING


def begin_share(kind: str, text: str, *, started_at: float | None = None) -> FlowStep:
    stamp = time.time() if started_at is None else started_at
    body = strip_share_command(text)
    if not body.strip():
        return _ask(kind, _first_stage(kind), {}, stamp)
    try:
        parsed = parse_share(kind, body)
    except ParseError as exc:
        return FlowStep(job={"kind": kind, "stage": "done", "started_at": stamp}, messages=[escape(str(exc))])
    return _from_parsed(kind, parsed, {}, stamp)


def continue_share(job: dict, text: str) -> FlowStep:
    kind = str(job.get("kind") or "")
    stage = str(job.get("stage") or "")
    draft = dict(job.get("draft") or {})
    stamp = float(job.get("started_at") or time.time())
    if (text or "").strip().lower() in {"отмена", "cancel", "/cancel"}:
        return FlowStep(job=_done_job(kind, stamp), messages=["Отменил."])
    if stage == "partner":
        partner = partner_from_token((text or "").strip().split()[0]) if (text or "").strip() else None
        try:
            parsed = parse_share(kind, text)
        except ParseError as exc:
            if partner:
                draft["partner_id"] = partner
                return _ask(kind, "apps", draft, stamp, prefix=str(exc))
            return _ask(kind, "partner", draft, stamp, prefix=str(exc))
        if parsed.partner_id:
            draft["partner_id"] = parsed.partner_id
        return _from_parsed(kind, parsed, draft, stamp)
    if stage == "ids":
        try:
            parsed = parse_share(kind, text)
        except ParseError as exc:
            return _ask(kind, "ids", draft, stamp, prefix=str(exc))
        if parsed.ids:
            draft["ids"] = list(parsed.ids)
        return _from_parsed(kind, parsed, draft, stamp)
    if stage == "apps":
        apps = parse_apps(text)
        if not apps:
            return _ask(kind, "apps", draft, stamp, prefix="Не вижу приложений.")
        draft["apps"] = apps
        return _from_parsed(kind, ParsedShare(apps=apps), draft, stamp)
    if stage == "pairs":
        body = text
        if draft.get("game_id"):
            body = f"{draft['game_id']}\n{text}"
        try:
            parsed = parse_share("unity", body)
        except ParseError as exc:
            return _ask(kind, "pairs", draft, stamp, prefix=str(exc))
        if parsed.default_game and not parsed.pairs:
            draft["game_id"] = parsed.default_game
            return _ask(kind, "pairs", draft, stamp, prefix=f"Game id {parsed.default_game} взял, теперь приложения.")
        return _from_parsed(kind, parsed, draft, stamp)
    return FlowStep(job=_done_job(kind, stamp), messages=["Сейчас расшарка ничего не ждёт."])


def format_result(result: ShareResult) -> str:
    if result.status == "done":
        line = f"{result.app} — готово"
        if result.reason and result.reason != "права сохранены":
            line = f"{line}\n{result.reason}"
    elif result.status == "already":
        line = f"{result.app} — уже настроено"
    elif result.status == "conflict":
        line = f"{result.app} — на данной приле уже есть интеграция с Юнити (Game ID {result.reason})"
    else:
        line = f"{result.app} — ошибка: {result.reason}"
    if result.links:
        click, impression = result.links
        if click:
            line += f"\nклик: {click}"
        if impression:
            line += f"\nпоказ: {impression}"
    return line


UNITY_REPLACE_QUESTION = "На данной приле уже есть интеграция с Юнити, заменить гейм айди?"


def unity_replace_pairs(task, results) -> list[tuple[str, str]]:
    """Пары (приложение, новый game id) там, где Unity уже стоит с другим id."""
    if getattr(task, "kind", "") != "unity":
        return []
    conflicts = {item.app for item in results if item.status == "conflict"}
    return [(app, gid) for app, gid in task.pairs if app in conflicts]


def _from_parsed(kind: str, parsed: ParsedShare, draft: dict, stamp: float) -> FlowStep:
    if parsed.partner_id:
        draft["partner_id"] = parsed.partner_id
    if parsed.ids:
        draft["ids"] = list(parsed.ids)
    if parsed.apps:
        draft["apps"] = list(parsed.apps)
    if parsed.pairs:
        draft["pairs"] = list(parsed.pairs)
    if parsed.default_game:
        draft["game_id"] = parsed.default_game
    missing = parsed.missing
    if kind == "agency" and not draft.get("partner_id"):
        missing = "partner"
    elif kind == "agency" and not draft.get("apps"):
        missing = "apps"
    elif kind in {"tiktok", "snapchat"} and not draft.get("ids"):
        missing = "ids"
    elif kind in {"tiktok", "snapchat"} and not draft.get("apps"):
        missing = "apps"
    elif kind == "unity" and not draft.get("pairs"):
        missing = parsed.missing or "pairs"
    else:
        missing = None
    if missing:
        return _ask(kind, missing, draft, stamp)
    task = ShareTask(
        kind=kind,
        partner_id=draft.get("partner_id"),
        ids=list(draft.get("ids") or []),
        apps=list(draft.get("apps") or []),
        pairs=[(app, game) for app, game in (draft.get("pairs") or [])],
    )
    return FlowStep(job=_done_job(kind, stamp), messages=["Запускаю расшарку."], task=task)


def _ask(kind: str, stage: str, draft: dict, stamp: float, prefix: str | None = None) -> FlowStep:
    text = _PROMPTS[(kind, stage)]
    if prefix:
        text = f"{escape(prefix)}\n\n{text}"
    return FlowStep(
        job={"kind": kind, "stage": stage, "draft": draft, "started_at": stamp},
        messages=[text],
        buttons=[("Отмена", "share:cancel")],
    )


def _done_job(kind: str, stamp: float) -> dict:
    return {"kind": kind, "stage": "done", "draft": {}, "started_at": stamp}


def _first_stage(kind: str) -> str:
    if kind == "agency":
        return "partner"
    if kind == "unity":
        return "pairs"
    return "ids"

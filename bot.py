#!/usr/bin/env python3
"""Telegram-бот для пошагового завода приложений."""

import asyncio
import logging
import os
import re
import tempfile
import time
from html import escape
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from src.backend import (
    AndroidBackendClient,
    AndroidBackendError,
    NewBackendClient,
    NewBackendError,
    build_android_organic_link,
    parse_facebook_settings,
)
from src.binom import (
    BinomClient,
    BinomError,
    extract_binom_campaign_key,
    format_campaign_name,
    get_template_campaign_id,
)
from src.crm import (
    CRM_THEMES,
    CrmClient,
    CrmError,
    build_create_app_payload,
    extract_crm_images_from_zip,
    get_theme_name,
    parse_apns_key_file,
    resolve_apps_created_by,
)
from src.config import allowed_user_ids
from src.crm.domain import normalize_backend_domain
from src.crm.zip_assets import safe_upload_filename, safe_zip_filename

from src.formatter import format_missing_prompt, format_parsed_app, is_parse_complete
from src.models import ParsedApp, Platform, parsed_from_dict
from src.parser import merge_missing_reply, parse_app_message
from src.appsflyer.flow import (
    begin_share,
    continue_share,
    domain_menu_buttons,
    format_result,
    help_text,
    main_menu_buttons,
    menu_route,
    share_expects_text,
    share_menu_buttons,
    unity_replace_pairs,
    UNITY_REPLACE_QUESTION,
)
from src.appsflyer.session import AfBlocked, AfError, ShareTask, execute_share
from src.domains.dynadot import DynadotClient
from src.domains.portal import PortalDomainClient
from src.domains.pricing import DomainQuote, is_ipv4, parse_domain_list
from src.domains.setup import (
    MAX_BULK,
    quotes_over_limit,
    run_confirmed_batch,
    search_bulk,
    search_specific,
    select_for_purchase,
)
from src.firebase.console import setup_firebase
from src.firebase.key import describe_key, parse_service_account, rtdb_values, split_firebase_keys
from src.workflow.intake import extract_app_command_payload, workflow_reply_kind
from src.workflow.locks import user_lock
from src.workflow.logging_setup import setup_logging
from src.workflow.runner import (
    cleanup_crm_temp,
    execute_next,
    next_action,
    precheck,
    resolve_domain,
)
from src.workflow.persist import (
    WorkflowStore,
    app_key_from_parsed,
    app_key_from_wf,
    is_workflow_complete,
    resume_step,
    resume_summary,
)
from src.workflow.steps import (
    STEP_LABELS,
    WorkflowStep,
    parse_callback,
)

load_dotenv(Path(__file__).parent / ".env")
setup_logging()

logger = logging.getLogger(__name__)

WORKFLOW_KEY = "workflow"
CRM_CREATE_CALLBACK = "wf:crm:create"
CRM_THEME_CALLBACK_PREFIX = "wf:theme:"
DOMAIN_CONFIRM_CALLBACK = "wf:domain:confirm"
RESUME_CONTINUE_CALLBACK = "wf:resume:continue"
RESUME_RESTART_CALLBACK = "wf:resume:restart"
RETRY_CALLBACK = "wf:retry"
PENDING_RESUME_KEY = "pending_resume"
DOMAIN_JOB_KEY = "domain_job"
DOMAIN_APP_KEY = "domain-purchase"
SHARE_JOB_KEY = "share_job"
SHARE_APP_KEY = "af-share"

CRM_STATUS_ACTIVE = 2

_workflow_store = WorkflowStore()

START_TEXT = (
    "👋 <b>Бот завода приложений</b>\n\n"
    "Завод начинается только командой <code>/app</code>.\n\n"
    "Можно сразу:\n"
    "<code>/app\nНовое IOS приложение\n...</code>\n\n"
    "Или <code>/app</code> без текста — бот подождёт следующее сообщение с данными.\n\n"
    "Дальше шаги идут сами. Архив, тематику, домен и ключи Facebook "
    "бот попросит отдельно, их можно присылать обычным сообщением.\n\n"
    "Просто пересланный текст без <code>/app</code> завод не запускает.\n\n"
    "Покупка домена: <code>/domain</code> (Dynadot, Cloudflare, CRM).\n"
    "Расшарка AppsFlyer: <code>/share_partner</code>, <code>/share_tiktok</code>, "
    "<code>/share_unity</code>, <code>/share_snapchat</code>.\n"
    "Кнопки ниже и <code>/menu</code> запускают те же сценарии. Подсказка: <code>/help</code>."
)

APP_WAIT_TEXT = (
    "Пришлите сообщение о приложении следующим сообщением.\n"
    "Можно переслать его как есть."
)


def _get_binom_client() -> BinomClient | None:
    api_url = os.getenv("BINOM_API_URL")
    api_key = os.getenv("BINOM_API_KEY")
    if not api_url or not api_key:
        return None
    return BinomClient(api_url=api_url, api_key=api_key)


def _get_crm_client() -> CrmClient | None:
    api_url = os.getenv("CRM_API_URL", "https://api.zmportal.online/api")
    access_token = os.getenv("CRM_ACCESS_TOKEN")
    login = os.getenv("CRM_LOGIN")
    password = os.getenv("CRM_PASSWORD")

    if not access_token and (not login or not password):
        return None

    return CrmClient(
        api_url=api_url,
        access_token=access_token,
        login=login,
        password=password,
    )


def _get_crm_team_ids() -> list[int]:
    raw = os.getenv("CRM_TEAM_IDS", "388,1263")
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _get_template_campaign_id(platform: Platform) -> int:
    ios_id = int(os.getenv("BINOM_TEMPLATE_CAMPAIGN_ID_IOS", "2"))
    android_id = int(os.getenv("BINOM_TEMPLATE_CAMPAIGN_ID_ANDROID", "4"))
    return get_template_campaign_id(platform, ios_id=ios_id, android_id=android_id)


def _parsed_from_dict(data: dict) -> ParsedApp:
    return ParsedApp(
        platform=Platform(data["platform"]),
        name=data.get("name"),
        store_url=data.get("store_url"),
        store_id=data.get("store_id"),
        bundle=data.get("bundle"),
        backend_domain=data.get("backend_domain"),
        appsflyer=data.get("appsflyer"),
        owner=data.get("owner"),
        pushes=data.get("pushes"),
        developer_site=data.get("developer_site"),
        backend=data.get("backend"),
        firebase_name=data.get("firebase_name"),
        firebase_keys=data.get("firebase_keys"),
        raw_text=data.get("raw_text", ""),
        missing_fields=data.get("missing_fields", []),
    )


def _workflow(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    if WORKFLOW_KEY not in context.user_data:
        context.user_data[WORKFLOW_KEY] = {}
    return context.user_data[WORKFLOW_KEY]


def _user_id(update: Update) -> int | str:
    user = update.effective_user
    return user.id if user else "unknown"


def _persist_wf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wf = _workflow(context)
    app_key = app_key_from_wf(wf)
    if not app_key:
        return
    wf["app_key"] = app_key
    try:
        _workflow_store.save(_user_id(update), wf)
    except OSError:
        logger.exception("Failed to persist workflow")


def _persist_wf_message(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    wf = _workflow(context)
    app_key = app_key_from_wf(wf)
    if not app_key:
        return
    wf["app_key"] = app_key
    user = getattr(message, "from_user", None)
    user_id = user.id if user else "unknown"
    try:
        _workflow_store.save(user_id, wf)
    except OSError:
        logger.exception("Failed to persist workflow")


def _delete_persisted_wf(update: Update, wf: dict[str, Any] | None = None) -> None:
    target = wf or {}
    app_key = app_key_from_wf(target)
    if not app_key:
        return
    try:
        _workflow_store.delete(_user_id(update), app_key)
    except OSError:
        logger.exception("Failed to delete persisted workflow")


def _resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Продолжить", callback_data=RESUME_CONTINUE_CALLBACK),
                InlineKeyboardButton("🔄 Сначала", callback_data=RESUME_RESTART_CALLBACK),
            ]
        ]
    )


def _find_resumable_state(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_key: str,
) -> dict[str, Any] | None:
    current = _workflow(context)
    if app_key_from_wf(current) == app_key and current.get("parsed") and not is_workflow_complete(current):
        return dict(current)
    saved = _workflow_store.load(_user_id(update), app_key)
    if saved and not is_workflow_complete(saved):
        return saved
    return None


async def _offer_resume(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    app_key: str,
    saved: dict[str, Any],
    fresh_parsed: ParsedApp,
) -> None:
    context.user_data[PENDING_RESUME_KEY] = {
        "app_key": app_key,
        "workflow": saved,
        "fresh_parsed": fresh_parsed.to_dict(),
    }
    message = update.message
    await message.reply_text(
        "♻️ <b>Нашёл незавершённый завод этой прилы</b>\n\n"
        f"{resume_summary(saved)}\n\n"
        "Продолжить с места остановки или начать заново?",
        parse_mode=ParseMode.HTML,
        reply_markup=_resume_keyboard(),
    )


async def _apply_resumed_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    saved: dict[str, Any],
) -> None:
    wf = _workflow(context)
    wf.clear()
    wf.update(saved)
    _persist_wf(update, context)

    message = (
        update.callback_query.message
        if update.callback_query
        else update.message
    )
    step = resume_step(wf)
    parsed = _parsed_from_dict(wf["parsed"]) if wf.get("parsed") else None

    if wf.get("awaiting_missing") and parsed:
        await message.reply_text(
            "Продолжаем: не хватает данных.\n\n" + format_missing_prompt(parsed),
            parse_mode=ParseMode.HTML,
        )
        return

    if wf.get("awaiting_android_fb") and parsed:
        collected = wf.get("android_fb") or {}
        missing = [
            key
            for key in ("app_id", "access_token", "decryption_key")
            if not collected.get(key)
        ]
        nxt = missing[0] if missing else "app_id"
        wf["android_fb_next"] = nxt
        prompt = _FB_FIELD_PROMPTS.get(nxt, "Facebook-данные")
        await message.reply_text(
            "♻️ Продолжаем Android-бэкенд.\n"
            f"Пришлите <b>{escape(prompt)}</b>\n"
            "или все три значения одним сообщением (каждое с новой строки).",
            parse_mode=ParseMode.HTML,
        )
        _persist_wf(update, context)
        return

    if wf.get("awaiting_domain"):
        await message.reply_text(
            "Продолжаем шаг домена. Пришлите домен текстом, например <code>example.sbs</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if wf.get("crm_needs_recollect"):
        wf.pop("crm_needs_recollect", None)
        await message.reply_text(
            "Продолжаем CRM: файлы после перезапуска нужно прислать снова.",
            parse_mode=ParseMode.HTML,
        )
        await _begin_crm_collection(message, context, wf)
        return

    if step is None:
        await message.reply_text("Этот завод уже завершён. Пришлите другое приложение.")
        return

    label = STEP_LABELS.get(step, step.value)
    await message.reply_text(
        f"♻️ Продолжаем завод.\nСледующий шаг: <b>{escape(label)}</b>",
        parse_mode=ParseMode.HTML,
    )
    await _run_pipeline(update, context, wf)


def _log_user_action(update_or_message: Update | Any, action: str, **extra: Any) -> None:
    if isinstance(update_or_message, Update):
        user = update_or_message.effective_user
    else:
        user = getattr(update_or_message, "from_user", None)
    user_id = user.id if user else "unknown"
    username = user.username if user and user.username else "-"
    details = " | ".join(f"{key}={value}" for key, value in extra.items())
    step_logger = logging.getLogger("zavod.steps")
    step_logger.info(
        "user_id=%s username=%s action=%s %s",
        user_id,
        username,
        action,
        details,
    )


def _retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 Повторить шаг", callback_data=RETRY_CALLBACK)]]
    )


def _theme_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for theme_id, name in CRM_THEMES:
        row.append(
            InlineKeyboardButton(
                name,
                callback_data=f"{CRM_THEME_CALLBACK_PREFIX}{theme_id}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _needs_apns(parsed: ParsedApp) -> bool:
    if not parsed.pushes:
        return False
    return "apns" in parsed.pushes.lower()


def _crm_temp_dir(wf: dict[str, Any]) -> Path:
    path = wf.get("crm_temp_dir")
    if path:
        return Path(path)
    directory = Path(tempfile.mkdtemp(prefix="zavod_crm_"))
    wf["crm_temp_dir"] = str(directory)
    return directory


def _crm_files_state(wf: dict[str, Any]) -> dict[str, Any]:
    return wf.setdefault("crm_files", {})


async def _notify_error(message, text: str, *, retry: bool = False) -> None:
    await message.reply_text(
        f"❌ <b>Ошибка</b>\n\n{escape(text)}",
        parse_mode=ParseMode.HTML,
        reply_markup=_retry_keyboard() if retry else None,
    )


def _html_lines(text: str) -> str:
    return "\n".join(escape(line) for line in (text or "").splitlines()) or "—"


def _action_label(action: str) -> str:
    mapping = {
        "binom": STEP_LABELS[WorkflowStep.BINOM],
        "crm": STEP_LABELS[WorkflowStep.CRM],
        "crm_create": STEP_LABELS[WorkflowStep.CRM],
        "crm_grant": "Доступ к приложению в CRM",
        "domain": STEP_LABELS[WorkflowStep.DOMAIN],
        "backend": STEP_LABELS[WorkflowStep.BACKEND],
    }
    return mapping.get(action, action)


def _template_for_wf(wf: dict[str, Any]) -> int:
    parsed = _parsed_from_dict(wf["parsed"])
    return _get_template_campaign_id(parsed.platform)


async def _run_locked(update: Update, action) -> None:
    lock = user_lock(_user_id(update))
    if lock.locked():
        if update.callback_query:
            await update.callback_query.answer("Уже выполняется")
        message = update.effective_message
        if message:
            await message.reply_text("Предыдущий запуск ещё идёт, подождите.")
        return
    async with lock:
        await action()


async def _run_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE, wf: dict[str, Any]) -> None:
    message = update.effective_message
    if message is None:
        return

    resolve_domain(wf)
    try:
        notes = await asyncio.to_thread(precheck, wf, _get_binom_client(), _get_crm_client())
    except Exception as exc:
        logger.exception("precheck failed")
        from src.binom import BinomError
        from src.crm import CrmError

        if not isinstance(exc, (BinomError, CrmError)):
            raise
        action = next_action(wf)
        step = WorkflowStep.BINOM if action == "binom" else WorkflowStep.CRM
        wf["failed_step"] = step.value
        wf["failed_error"] = str(exc)
        _persist_wf(update, context)
        await _notify_error(message, str(exc), retry=True)
        return

    for note in notes:
        await message.reply_text(f"ℹ️ {escape(note)}", parse_mode=ParseMode.HTML)
    _persist_wf(update, context)

    while True:
        action = next_action(wf)
        if action == "need_crm_files":
            await _begin_crm_collection(message, context, wf)
            return
        if action == "need_domain":
            wf["awaiting_domain"] = True
            _persist_wf(update, context)
            crm = wf.get("crm") or {}
            await message.reply_text(
                f"<b>{STEP_LABELS[WorkflowStep.DOMAIN]}</b>\n\n"
                f"ID CRM: <code>{escape(str(crm.get('id', '—')))}</code>\n\n"
                "В исходном сообщении домена нет. Пришлите его текстом, например "
                "<code>example.sbs</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if action == "need_android_fb":
            parsed = _parsed_from_dict(wf["parsed"])
            await _start_android_backend_step(update, context, wf, parsed)
            return
        if action == "backend_unsupported":
            await message.reply_text(
                f"<b>{STEP_LABELS[WorkflowStep.BACKEND]}</b>\n\n"
                "Старый бэкенд пока не автоматизирован.",
                parse_mode=ParseMode.HTML,
            )
            return
        if action in {"need_missing", "need_parse", "done"}:
            if action == "done":
                cleanup_crm_temp(wf)
                _delete_persisted_wf(update, wf)
                await message.reply_text("✅ <b>Готово.</b> Все шаги выполнены.", parse_mode=ParseMode.HTML)
            return

        label = _action_label(action)
        status = await message.reply_text(f"⏳ <b>{escape(label)}</b>", parse_mode=ParseMode.HTML)
        result = await asyncio.to_thread(
            execute_next,
            wf,
            binom=_get_binom_client(),
            crm=_get_crm_client(),
            new_backend=_get_new_backend_client(),
            android_backend=_get_android_backend_client(),
            template_id=_template_for_wf(wf),
            team_ids=_get_crm_team_ids(),
            organic_link=_resolve_binom_url(wf),
            persist=lambda: _persist_wf(update, context),
        )
        _persist_wf(update, context)
        if not result.ok:
            if result.action == "need_domain":
                wf["awaiting_domain"] = True
                _persist_wf(update, context)
                await status.edit_text(
                    "Пришлите домен текстом, например <code>example.sbs</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            await status.edit_text(
                f"❌ <b>{escape(label)}</b>\n\n{escape(result.error)}",
                parse_mode=ParseMode.HTML,
                reply_markup=_retry_keyboard(),
            )
            return

        title = "Пропущено" if result.skipped else "Готово"
        await status.edit_text(
            f"✅ <b>{escape(label)}</b> — {title}\n{_html_lines(result.summary)}",
            parse_mode=ParseMode.HTML,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_user_action(update, "start")
    await update.message.reply_text(
        START_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=_button_markup(main_menu_buttons()),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    await message.reply_text(
        help_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_button_markup(main_menu_buttons()),
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    await message.reply_text("Куда идём?", reply_markup=_button_markup(main_menu_buttons()))


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Старт завода: /app с текстом сразу или /app и данные следующим сообщением."""

    async def _body() -> None:
        message = update.message
        if not message:
            return

        payload = extract_app_command_payload(message.text or message.caption or "")
        _log_user_action(update, "app_command", has_payload=bool(payload))
        if not payload:
            wf = _workflow(context)
            wf.clear()
            wf["awaiting_app"] = True
            await message.reply_text(APP_WAIT_TEXT, parse_mode=ParseMode.HTML)
            return

        await _ingest_app_text(update, context, payload)

    await _run_locked(update, _body)


async def step2_dev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """DEV: parse app message and jump straight to CRM (skip Binom)."""
    message = update.message
    if not message:
        return

    text = (message.text or "").split(maxsplit=1)
    payload = text[1].strip() if len(text) > 1 else ""
    if not payload and context.args:
        payload = " ".join(context.args).strip()

    if not payload:
        await message.reply_text(
            "DEV <code>/step2</code>: отправьте команду вместе с текстом приложения.\n\n"
            "Пример:\n"
            "<code>/step2\nНовое Android приложение...\n...</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    parsed = parse_app_message(payload)
    wf = _workflow(context)
    wf.clear()
    wf["parsed"] = parsed.to_dict()
    wf["completed_steps"] = [WorkflowStep.PARSE.value, WorkflowStep.BINOM.value]
    wf["binom"] = {"id": 0, "name": "DEV_SKIPPED", "url": "", "key": "dev"}
    wf["dev_skip_binom"] = True

    _log_user_action(
        update,
        "dev_step2",
        platform=parsed.platform.value,
        name=parsed.name,
        store_id=parsed.store_id,
        missing=parsed.missing_fields,
    )

    header = "🛠 <b>DEV /step2</b> — Binom пропущен\n\n"
    reply = (
        f"{header}"
        f"<b>Шаг 1 — {STEP_LABELS[WorkflowStep.PARSE]}</b>\n\n"
        f"{format_parsed_app(parsed)}"
    )
    await message.reply_text(reply, parse_mode=ParseMode.HTML)

    if not is_parse_complete(parsed):
        wf["awaiting_missing"] = True
        wf["awaiting_after"] = "crm"
        await message.reply_text(format_missing_prompt(parsed), parse_mode=ParseMode.HTML)
        return

    await message.reply_text("✅ Данные распознаны. Сразу переходим к CRM.")
    await _begin_crm_collection(message, context, wf)


def _extract_command_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    message = update.message
    text = (message.text or "").split(maxsplit=1) if message else [""]
    payload = text[1].strip() if len(text) > 1 else ""
    if not payload and context.args:
        payload = " ".join(context.args).strip()
    return payload


def _extract_optional_binom_key(payload: str) -> tuple[str, str | None]:
    """Return (payload_without_key_line, key) if key=... / Binom key: ... present."""
    match = re.search(
        r"(?im)^(?:binom[_\s-]*key|key)\s*[:=]\s*(\S+)\s*$",
        payload,
    )
    if not match:
        return payload, None
    key = match.group(1).strip()
    cleaned = (payload[: match.start()] + payload[match.end() :]).strip()
    return cleaned, key


def _extract_optional_binom_url(payload: str) -> tuple[str, str | None]:
    """Return (payload_without_url_line, campaign_url) if url=... or bare http(s) campaign link."""
    match = re.search(
        r"(?im)^(?:binom[_\s-]*(?:url|link)|organic[_\s-]*link|url|link)\s*[:=]\s*(\S+)\s*$",
        payload,
    )
    if match:
        url = match.group(1).strip()
        cleaned = (payload[: match.start()] + payload[match.end() :]).strip()
        return cleaned, url

    match = re.search(r"(?im)^(https?://\S+)\s*$", payload)
    if match:
        url = match.group(1).strip()
        # Keep if it looks like a campaign click URL (has key= or .php)
        if "key=" in url or ".php" in url:
            cleaned = (payload[: match.start()] + payload[match.end() :]).strip()
            return cleaned, url
    return payload, None


async def back_android_dev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """DEV: skip to Android backend setup (asks FB keys, then saves panel)."""
    message = update.message
    if not message:
        return

    payload = _extract_command_payload(update, context)
    if not payload:
        await message.reply_text(
            "DEV <code>/back_android</code>: отправьте команду вместе с Android-данными.\n\n"
            "Нужны:\n"
            "• bundle (или полный текст приложения)\n"
            "• <b>полная ссылка кампании Binom</b> (organic link)\n\n"
            "Пример:\n"
            "<code>/back_android\n"
            "com.skyforgenomads.sky\n"
            "url=https://tracker.example/click.php?key=campaignkey</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    payload, binom_url = _extract_optional_binom_url(payload)
    payload, binom_key = _extract_optional_binom_key(payload)

    if context.args:
        for arg in context.args:
            lower = arg.lower()
            if lower.startswith("url=") or lower.startswith("link="):
                binom_url = arg.split("=", 1)[1].strip() or binom_url
            elif lower.startswith("key="):
                binom_key = arg.split("=", 1)[1].strip() or binom_key

    if not binom_url and binom_key:
        binom_url = build_android_organic_link(key=binom_key)
    if not binom_url:
        await message.reply_text(
            "Нужна <b>полная ссылка кампании Binom</b>.\n\n"
            "Добавьте строку, например:\n"
            "<code>url=https://tracker.example/click.php?key=XXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not binom_key:
        binom_key = extract_binom_campaign_key(url=binom_url) or "dev_android_test"

    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    bare_bundle = (
        len(lines) == 1
        and "." in lines[0]
        and " " not in lines[0]
        and not lines[0].startswith("http")
    )

    if bare_bundle:
        bundle = lines[0]
        parsed = ParsedApp(
            platform=Platform.ANDROID,
            name=bundle,
            store_url=f"https://play.google.com/store/apps/details?id={bundle}",
            store_id=bundle,
            bundle=bundle,
            raw_text=payload,
            missing_fields=[],
        )
    else:
        parsed = parse_app_message(payload)
        if parsed.platform != Platform.ANDROID:
            parsed.platform = Platform.ANDROID
        if not parsed.bundle and lines:
            maybe = lines[0]
            if "." in maybe and " " not in maybe and not maybe.startswith("http"):
                parsed.bundle = maybe

    if not parsed.bundle:
        await message.reply_text(
            "Не удалось определить bundle. Пришлите, например:\n"
            "<code>/back_android\ncom.example.app\n"
            "url=https://tracker.example/click.php?key=XXXX</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    organic_link = build_android_organic_link(url=binom_url, key=binom_key)
    wf = _workflow(context)
    wf.clear()
    wf["parsed"] = parsed.to_dict()
    wf["completed_steps"] = [
        WorkflowStep.PARSE.value,
        WorkflowStep.BINOM.value,
        WorkflowStep.CRM.value,
        WorkflowStep.DOMAIN.value,
    ]
    wf["binom"] = {
        "id": 0,
        "name": "DEV_SKIPPED",
        "url": organic_link,
        "key": binom_key,
    }
    wf["dev_back_android"] = True

    _log_user_action(
        update,
        "dev_back_android",
        bundle=parsed.bundle,
        name=parsed.name,
        binom_key=binom_key,
        organic_link=organic_link,
    )

    await message.reply_text(
        "🛠 <b>DEV /back_android</b> — шаги 1–4 пропущены\n\n"
        f"<b>Bundle:</b> <code>{escape(parsed.bundle)}</code>\n"
        f"<b>Name:</b> {escape(parsed.name or '—')}\n"
        f"<b>Binom key:</b> <code>{escape(binom_key)}</code>\n"
        f"<b>Organic:</b> <code>{escape(organic_link)}</code>\n\n"
        "Сразу переходим к настройке Android-бэкенда.",
        parse_mode=ParseMode.HTML,
    )
    await _start_android_backend_step(update, context, wf, parsed)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _body() -> None:
        await _handle_message_body(update, context)

    await _run_locked(update, _body)


async def _handle_message_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    wf = _workflow(context)
    kind = workflow_reply_kind(wf)

    if kind == "crm":
        await _handle_crm_file_message(update, context, wf)
        return

    text = message.text or message.caption
    if kind == "android_fb":
        if not text:
            await message.reply_text("Пришлите настройки Facebook текстом.")
            return
        await _handle_android_fb_reply(update, context, wf, text)
        return

    if kind == "domain":
        if not text:
            await message.reply_text("Пришлите домен текстом.")
            return
        await _handle_domain_reply(update, context, wf, text)
        return

    if kind == "missing":
        if not text:
            await message.reply_text("Пришлите недостающие данные текстом.")
            return
        await _handle_missing_reply(update, context, wf, text)
        return

    if kind == "app":
        if not text:
            await message.reply_text(APP_WAIT_TEXT, parse_mode=ParseMode.HTML)
            return
        wf.pop("awaiting_app", None)
        await _ingest_app_text(update, context, text)
        return

    share = _load_share_job(update, context)
    domain = _load_domain_job(update, context)
    share_first = share_expects_text(share) and not (
        _domain_expects_text(domain)
        and float((domain or {}).get("started_at") or 0) >= float((share or {}).get("started_at") or 0)
    )
    if share_first and share is not None:
        await _handle_share_text(update, context, share)
        return

    if await _domain_job_wants_text(update, context):
        return

    await message.reply_text(
        "Завод начинается с команды <code>/app</code> или кнопки в <code>/menu</code>.\n"
        "Пересланное сообщение само по себе ничего не запускает.",
        parse_mode=ParseMode.HTML,
    )


async def _ingest_app_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    message = update.effective_message
    if message is None:
        return

    parsed = parse_app_message(text)
    wf = _workflow(context)
    app_key = app_key_from_parsed(parsed)
    if app_key:
        resumable = _find_resumable_state(update, context, app_key)
        if resumable is not None:
            _log_user_action(update, "resume_offer", app_key=app_key)
            await _offer_resume(
                update,
                context,
                app_key=app_key,
                saved=resumable,
                fresh_parsed=parsed,
            )
            return

    wf.clear()
    wf["parsed"] = parsed.to_dict()
    wf["completed_steps"] = [WorkflowStep.PARSE.value]
    if app_key:
        wf["app_key"] = app_key

    _log_user_action(
        update,
        "step_parse_done",
        platform=parsed.platform.value,
        name=parsed.name,
        store_id=parsed.store_id,
        missing=parsed.missing_fields,
    )

    reply = (
        f"<b>Шаг 1 — {STEP_LABELS[WorkflowStep.PARSE]}</b>\n\n"
        f"{format_parsed_app(parsed)}"
    )

    if not is_parse_complete(parsed):
        wf["awaiting_missing"] = True
        wf["awaiting_after"] = "binom"
        _persist_wf(update, context)
        await message.reply_text(reply, parse_mode=ParseMode.HTML)
        await message.reply_text(format_missing_prompt(parsed), parse_mode=ParseMode.HTML)
        return

    reply += "\n\n✅ <b>Шаг 1 выполнен.</b> Данные распознаны. Запускаю следующие шаги."
    resolve_domain(wf)
    _persist_wf(update, context)
    await message.reply_text(reply, parse_mode=ParseMode.HTML)
    await _run_pipeline(update, context, wf)


async def _handle_missing_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    text: str,
) -> None:
    message = update.message
    previous = _parsed_from_dict(wf["parsed"])
    parsed = merge_missing_reply(previous, text)
    wf["parsed"] = parsed.to_dict()

    _log_user_action(
        update,
        "missing_fields_reply",
        platform=parsed.platform.value,
        missing=parsed.missing_fields,
    )

    reply = (
        f"<b>Шаг 1 — {STEP_LABELS[WorkflowStep.PARSE]}</b>\n\n"
        f"{format_parsed_app(parsed)}"
    )
    await message.reply_text(reply, parse_mode=ParseMode.HTML)

    if not is_parse_complete(parsed):
        await message.reply_text(format_missing_prompt(parsed), parse_mode=ParseMode.HTML)
        _persist_wf(update, context)
        return

    wf.pop("awaiting_missing", None)
    after = wf.pop("awaiting_after", "binom")
    _persist_wf(update, context)

    if after == "crm":
        await message.reply_text("✅ Данные дополнены. Переходим к CRM.")
        await _begin_crm_collection(message, context, wf)
        return

    await message.reply_text(
        "✅ <b>Шаг 1 выполнен.</b> Данные распознаны. Запускаю следующие шаги.",
        parse_mode=ParseMode.HTML,
    )
    resolve_domain(wf)
    await _run_pipeline(update, context, wf)


async def handle_step_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _body() -> None:
        await _handle_step_callback_body(update, context)

    await _run_locked(update, _body)


async def _handle_step_callback_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    if query.data == RESUME_CONTINUE_CALLBACK:
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        pending = context.user_data.pop(PENDING_RESUME_KEY, None) or {}
        saved = pending.get("workflow")
        if not isinstance(saved, dict):
            await query.message.reply_text("Нет сохранённого прогресса для продолжения.")
            return
        _log_user_action(update, "resume_continue", app_key=pending.get("app_key"))
        await _apply_resumed_workflow(update, context, saved)
        return

    if query.data == RESUME_RESTART_CALLBACK:
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        pending = context.user_data.pop(PENDING_RESUME_KEY, None) or {}
        app_key = pending.get("app_key")
        fresh = pending.get("fresh_parsed")
        if app_key:
            try:
                _workflow_store.delete(_user_id(update), app_key)
            except OSError:
                logger.exception("Failed to delete workflow on restart")
        if not isinstance(fresh, dict):
            await query.message.reply_text("Пришлите сообщение о приложении ещё раз.")
            return
        wf = _workflow(context)
        cleanup_crm_temp(wf)
        wf.clear()
        parsed = _parsed_from_dict(fresh)
        wf["parsed"] = parsed.to_dict()
        wf["completed_steps"] = [WorkflowStep.PARSE.value]
        if app_key:
            wf["app_key"] = app_key
        _log_user_action(update, "resume_restart", app_key=app_key)
        reply = (
            f"<b>Шаг 1 — {STEP_LABELS[WorkflowStep.PARSE]}</b>\n\n"
            f"{format_parsed_app(parsed)}\n\n"
            "✅ <b>Шаг 1 выполнен.</b> Данные распознаны. Начинаем заново."
        )
        if not is_parse_complete(parsed):
            wf["awaiting_missing"] = True
            wf["awaiting_after"] = "binom"
            _persist_wf(update, context)
            await query.message.reply_text(reply, parse_mode=ParseMode.HTML)
            await query.message.reply_text(format_missing_prompt(parsed), parse_mode=ParseMode.HTML)
            return
        resolve_domain(wf)
        _persist_wf(update, context)
        await query.message.reply_text(reply, parse_mode=ParseMode.HTML)
        await _run_pipeline(update, context, wf)
        return

    if query.data in {CRM_CREATE_CALLBACK, DOMAIN_CONFIRM_CALLBACK, RETRY_CALLBACK}:
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        wf = _workflow(context)
        _log_user_action(update, "step_retry" if query.data == RETRY_CALLBACK else "step_legacy_button")
        await _run_pipeline(update, context, wf)
        return

    if query.data.startswith(CRM_THEME_CALLBACK_PREFIX):
        await query.answer()
        await _handle_theme_selected(update, context, query.data)
        return

    step = parse_callback(query.data)
    if step is None:
        return

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    wf = _workflow(context)
    _log_user_action(update, "step_requested", step=step.value)
    await _run_pipeline(update, context, wf)


async def _start_crm_collection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
) -> None:
    message = update.callback_query.message if update.callback_query else update.message
    if not message:
        return
    await _begin_crm_collection(message, context, wf)


async def _begin_crm_collection(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
) -> None:
    parsed_data = wf.get("parsed")
    if not parsed_data:
        await message.reply_text("Нет данных приложения. Сначала отправьте сообщение о приложении.")
        return

    parsed = _parsed_from_dict(parsed_data)
    if parsed.platform == Platform.UNKNOWN or parsed.missing_fields:
        await message.reply_text("Нельзя добавить в CRM: не хватает данных в сообщении.")
        return

    if _get_crm_client() is None:
        await message.reply_text(
            "CRM API не настроен. Добавьте в .env:\n"
            "<code>CRM_ACCESS_TOKEN=...</code>\n"
            "или <code>CRM_LOGIN</code> и <code>CRM_PASSWORD</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    needs_apns = _needs_apns(parsed)
    wf["crm_collecting"] = True
    wf["crm_stage"] = "zip"
    wf["crm_needs_apns"] = needs_apns
    wf["crm_files"] = {}
    wf.pop("crm_ready", None)
    _crm_temp_dir(wf)
    _persist_wf_message(message, context)

    platform_hint = "iOS" if parsed.platform == Platform.IOS else "Android"
    apns_hint = (
        "\n\nПосле архива бот запросит <b>APNS JSON-файл</b> "
        "с полями team_id, key_id, key_p8."
        if needs_apns
        else ""
    )

    await message.reply_text(
        f"<b>Шаг 3 — {STEP_LABELS[WorkflowStep.CRM]}</b>\n\n"
        f"Отправьте <b>ZIP-архив</b> с картинками для {platform_hint}:\n"
        "• <code>logo.png</code> — логотип\n"
        "• <code>ZM_*</code> — баннер ZM Apps\n"
        "• <code>compressed_banner*</code> — скриншоты"
        f"{apns_hint}\n\n"
        "Статус: <b>Активен</b>\n"
        "Сегмент: <b>Приватная</b>\n"
        f"Команды: <b>{', '.join(str(t) for t in _get_crm_team_ids())}</b>",
        parse_mode=ParseMode.HTML,
    )


async def _handle_crm_file_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
) -> None:
    message = update.message
    if not message:
        return

    if wf.get("crm_ready"):
        await message.reply_text(
            "Файлы уже получены. Выберите тематику или нажмите кнопку создания в CRM."
        )
        return

    if wf.get("crm_awaiting_theme"):
        await message.reply_text("Выберите тематику кнопкой под сообщением выше.")
        return

    stage = wf.get("crm_stage", "zip")
    files = _crm_files_state(wf)
    temp_dir = _crm_temp_dir(wf)

    try:
        if stage == "zip":
            await _process_crm_zip(message, context, wf, files, temp_dir)
            return
        if stage == "apns":
            await _process_crm_apns(message, context, wf, files, temp_dir)
            return
        if stage == "fcm":
            await _process_crm_fcm(message, context, wf, temp_dir)
            return
    except ValueError as exc:
        _log_user_action(update, "crm_file_error", error=str(exc))
        await _notify_error(message, str(exc))
        return


async def _process_crm_zip(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    files: dict[str, Any],
    temp_dir: Path,
) -> None:
    document = message.document
    if not document:
        await _notify_error(message, "Отправьте ZIP-архив с картинками как документ.")
        return

    filename = safe_zip_filename(document.file_name)
    if not filename.lower().endswith(".zip"):
        await _notify_error(message, "Нужен ZIP-архив (.zip) с картинками.")
        return

    zip_path = temp_dir / filename
    telegram_file = await context.bot.get_file(document.file_id)
    await telegram_file.download_to_drive(custom_path=str(zip_path))

    images = extract_crm_images_from_zip(zip_path, temp_dir / "images")
    files.update(images)
    wf["crm_zip_done"] = True

    _log_user_action(
        message,
        "crm_zip_received",
        files=",".join(images.keys()),
        archive=filename,
    )

    parsed_now = _parsed_from_dict(wf["parsed"])
    if parsed_now.platform != Platform.IOS:
        await _collect_fcm(message, context, wf)
        return

    if wf.get("crm_needs_apns"):
        wf["crm_stage"] = "apns"
        await message.reply_text(
            "✅ <b>Архив получен.</b> Картинки распознаны.\n\n"
            "Теперь отправьте <b>APNS JSON-файл</b> с полями "
            "<code>team_id</code>, <code>key_id</code>, <code>key_p8</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    await _ask_crm_theme(message, wf)


async def _process_crm_apns(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    files: dict[str, Any],
    temp_dir: Path,
) -> None:
    document = message.document
    if not document:
        await _notify_error(message, "Отправьте APNS JSON-файл как документ.")
        return

    filename = safe_upload_filename(document.file_name, "apns.json")
    target = temp_dir / filename
    telegram_file = await context.bot.get_file(document.file_id)
    await telegram_file.download_to_drive(custom_path=str(target))

    content = target.read_text(encoding="utf-8")
    files["apns"] = parse_apns_key_file(content, filename)

    _log_user_action(message, "crm_apns_received", filename=filename)
    await _ask_crm_theme(message, wf)


async def _collect_fcm(message, context: ContextTypes.DEFAULT_TYPE, wf: dict[str, Any]) -> None:
    """Android: ключ FCM и значения в Realtime Database через Firebase console.
    Уже сделанное второй раз не делаем: ключ лежит в workflow, база помечена rtdb_done."""
    parsed = _parsed_from_dict(wf["parsed"])
    need_key = not wf.get("fcm_key")
    rtdb = None
    rtdb_note = ""
    keys = split_firebase_keys(parsed.firebase_keys)
    if keys and not wf.get("rtdb_done"):
        domain = resolve_domain(wf)
        try:
            if not domain:
                raise ValueError("домена ещё нет")
            rtdb = rtdb_values(keys, domain)
        except ValueError as exc:
            rtdb_note = f"Ключи firebase не заполнены: {exc}."
    if wf.get("fcm_key"):
        await message.reply_text(f"FCM ключ уже есть: {describe_key(wf['fcm_key'])}")
    if rtdb_note:
        await message.reply_text(rtdb_note)
    if not need_key and not rtdb:
        await _ask_crm_theme(message, wf)
        return
    await message.reply_text("Захожу в Firebase…")
    try:
        result = await asyncio.to_thread(setup_firebase, parsed.firebase_name, need_key=need_key, rtdb=rtdb)
    except Exception as exc:
        reason = str(exc) or exc.__class__.__name__
        shot = getattr(exc, "screenshot", None)
        logger.warning("firebase_auto_failed reason=%s", reason)
        if rtdb:
            await _report_rtdb_manual(message, rtdb, reason, None if need_key else shot)
        if need_key:
            await _ask_fcm_file(message, context, wf, reason, shot)
        else:
            await _ask_crm_theme(message, wf)
        return
    if result.key:
        wf["fcm_key"] = result.key
        _log_user_action(message, "crm_fcm_generated", account=result.key.get("client_email"))
        await message.reply_text(f"✅ FCM ключ получен: {describe_key(result.key)}")
    if rtdb:
        if result.rtdb in {"done", "already"}:
            wf["rtdb_done"] = True
            pairs = ", ".join(f"{k} = {v}" for k, v in rtdb.items())
            word = "уже стояли" if result.rtdb == "already" else "записаны"
            await message.reply_text(f"✅ Realtime Database: {pairs} ({word})")
        else:
            await _report_rtdb_manual(message, rtdb, result.rtdb_error, result.rtdb_screenshot)
    _persist_wf_message(message, context)
    await _ask_crm_theme(message, wf)


async def _report_rtdb_manual(message, rtdb: dict[str, str], reason: str, shot: str | None) -> None:
    if shot and Path(shot).exists():
        try:
            with open(shot, "rb") as handle:
                await message.reply_photo(handle, caption="Realtime Database")
        except Exception:
            logger.warning("rtdb_screenshot_send_failed")
    pairs = "\n".join(f"<code>{escape(k)}</code> = <code>{escape(v)}</code>" for k, v in rtdb.items())
    await message.reply_text(
        f"Не получилось заполнить Realtime Database: {escape(reason)}\n\nПоставьте вручную:\n{pairs}",
        parse_mode=ParseMode.HTML,
    )


async def _ask_fcm_file(message, context, wf: dict[str, Any], reason: str, shot: str | None) -> None:
    wf["crm_stage"] = "fcm"
    _persist_wf_message(message, context)
    if shot and Path(shot).exists():
        try:
            with open(shot, "rb") as handle:
                await message.reply_photo(handle, caption="Firebase")
        except Exception:
            logger.warning("fcm_screenshot_send_failed")
    await message.reply_text(
        f"Не получилось взять FCM ключ автоматически: {escape(reason)}\n\n"
        "Отправьте <b>JSON-файл сервисного аккаунта Firebase</b> "
        "(Project settings → Service accounts → Generate new private key).",
        parse_mode=ParseMode.HTML,
    )


async def _process_crm_fcm(message, context, wf: dict[str, Any], temp_dir: Path) -> None:
    document = message.document
    if not document:
        await _notify_error(message, "Отправьте FCM JSON-файл как документ.")
        return
    filename = safe_upload_filename(document.file_name, "fcm.json")
    target = temp_dir / filename
    telegram_file = await context.bot.get_file(document.file_id)
    await telegram_file.download_to_drive(custom_path=str(target))
    try:
        data = parse_service_account(target.read_bytes())
    finally:
        target.unlink(missing_ok=True)
    wf["fcm_key"] = data
    _persist_wf_message(message, context)
    _log_user_action(message, "crm_fcm_received", account=data.get("client_email"))
    await message.reply_text(f"✅ FCM ключ принят: {describe_key(data)}")
    await _ask_crm_theme(message, wf)


async def _ask_crm_theme(message, wf: dict[str, Any]) -> None:
    wf["crm_collecting"] = False
    wf["crm_awaiting_theme"] = True
    wf.pop("crm_ready", None)
    wf.pop("crm_theme_id", None)
    wf.pop("crm_theme_name", None)
    wf.pop("crm_stage", None)

    await message.reply_text(
        "✅ <b>Файлы получены.</b>\n\n"
        "Выберите <b>тематику</b> приложения:",
        parse_mode=ParseMode.HTML,
        reply_markup=_theme_keyboard(),
    )


async def _handle_theme_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> None:
    query = update.callback_query
    message = query.message
    wf = _workflow(context)

    raw_id = callback_data.removeprefix(CRM_THEME_CALLBACK_PREFIX)
    try:
        theme_id = int(raw_id)
    except ValueError:
        await _notify_error(message, "Некорректная тематика.")
        return

    theme_name = get_theme_name(theme_id)
    if theme_name is None:
        await _notify_error(message, f"Тематика #{theme_id} не найдена.")
        return

    wf["crm_theme_id"] = theme_id
    wf["crm_theme_name"] = theme_name
    wf["crm_awaiting_theme"] = False
    wf["crm_ready"] = True

    _log_user_action(update, "crm_theme_selected", theme_id=theme_id, theme_name=theme_name)
    _persist_wf(update, context)

    await query.edit_message_reply_markup(reply_markup=None)
    await message.reply_text(
        f"✅ Тематика выбрана: <b>{escape(theme_name)}</b> "
        f"(theme_id=<code>{theme_id}</code>)",
        parse_mode=ParseMode.HTML,
    )
    await _run_pipeline(update, context, wf)


async def _mark_crm_ready(message, wf: dict[str, Any]) -> None:
    # Kept for compatibility; theme selection is required before create.
    await _ask_crm_theme(message, wf)

async def _handle_domain_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    text: str,
) -> None:
    message = update.message
    domain = normalize_backend_domain(text)
    if not domain:
        await _notify_error(
            message,
            "Некорректный домен. Пришлите, например: example.sbs",
        )
        return

    wf["domain"] = domain
    wf.pop("awaiting_domain", None)
    if isinstance(wf.get("parsed"), dict):
        wf["parsed"]["backend_domain"] = domain

    crm = wf.get("crm") or {}
    _log_user_action(update, "domain_received", domain=domain, app_id=crm.get("id"))
    _persist_wf(update, context)

    await message.reply_text(
        f"<b>{STEP_LABELS[WorkflowStep.DOMAIN]}</b>\n"
        f"Домен: <code>{escape(domain)}</code>",
        parse_mode=ParseMode.HTML,
    )
    await _run_pipeline(update, context, wf)


def _get_new_backend_client() -> NewBackendClient | None:
    base_url = os.getenv("BACKEND_NEW_URL", "https://neonflightgold777.cfd")
    username = os.getenv("BACKEND_NEW_USER")
    password = os.getenv("BACKEND_NEW_PASSWORD")
    if not username or not password:
        return None
    return NewBackendClient(base_url=base_url, username=username, password=password)


def _resolve_binom_key(wf: dict[str, Any]) -> str:
    binom = wf.get("binom") or {}
    return extract_binom_campaign_key(key=binom.get("key"), url=binom.get("url"))


def _get_android_backend_client() -> AndroidBackendClient | None:
    base_url = os.getenv("BACKEND_ANDROID_URL", "https://myappadmin.top/qize4nmp")
    username = os.getenv("BACKEND_ANDROID_USER")
    password = os.getenv("BACKEND_ANDROID_PASSWORD")
    if not username or not password:
        return None
    return AndroidBackendClient(base_url=base_url, username=username, password=password)


def _resolve_binom_url(wf: dict[str, Any]) -> str:
    """Organic link = campaign URL from Binom step (not a hardcoded tracker domain)."""
    binom = wf.get("binom") or {}
    return build_android_organic_link(url=binom.get("url"), key=_resolve_binom_key(wf))


_FB_FIELD_PROMPTS = {
    "app_id": "App ID приложения из Facebook",
    "access_token": "Secret key приложения из Facebook",
    "decryption_key": "Decryption key приложения из Facebook",
}


async def _start_android_backend_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    parsed: ParsedApp,
) -> None:
    message = update.effective_message
    if message is None:
        return
    client = _get_android_backend_client()
    if client is None:
        await _notify_error(
            message,
            "Android-бэкенд не настроен. Добавьте BACKEND_ANDROID_USER и BACKEND_ANDROID_PASSWORD в .env",
        )
        return

    if not parsed.bundle:
        await _notify_error(message, "Нет bundle Android-приложения.")
        return

    binom = wf.get("binom") or {}
    campaign_url = (binom.get("url") or "").strip()
    binom_key = _resolve_binom_key(wf)
    organic_link = _resolve_binom_url(wf)
    if not campaign_url or not organic_link.startswith("http"):
        await _notify_error(
            message,
            "Нет ссылки кампании Binom. Нужна полная URL кампании со шага создания в Binom.",
        )
        return
    if not binom_key or binom_key.lower() == "dev":
        await _notify_error(
            message,
            "Нет ключа кампании Binom. Сначала выполните создание кампании.",
        )
        return

    wf["awaiting_android_fb"] = True
    wf["android_fb"] = {}
    wf["android_fb_next"] = "app_id"
    _persist_wf(update, context)
    await message.reply_text(
        f"<b>Шаг 5 — {STEP_LABELS[WorkflowStep.BACKEND]}</b>\n\n"
        "Перед сохранением на Android-бэк нужны настройки Facebook.\n"
        f"<b>Bundle:</b> <code>{escape(parsed.bundle)}</code>\n"
        f"<b>Organic link:</b> <code>{escape(organic_link)}</code>\n\n"
        "Пришлите три значения одним сообщением (каждое с новой строки):\n"
        "1. App ID\n"
        "2. Secret key\n"
        "3. Decryption key\n\n"
        "Или пришлите сейчас только <b>App ID</b> приложения из Facebook.",
        parse_mode=ParseMode.HTML,
    )


async def _handle_android_fb_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wf: dict[str, Any],
    text: str,
) -> None:
    message = update.message
    collected = dict(wf.get("android_fb") or {})
    parsed_all = parse_facebook_settings(text)
    if parsed_all and not collected:
        collected = {
            "app_id": parsed_all.app_id,
            "access_token": parsed_all.access_token,
            "decryption_key": parsed_all.decryption_key,
        }
    else:
        field = wf.get("android_fb_next") or "app_id"
        value = text.strip().splitlines()[0].strip()
        if not value:
            await message.reply_text(f"Пустое значение. Пришлите {_FB_FIELD_PROMPTS[field]}.")
            return
        collected[field] = value

    wf["android_fb"] = collected
    missing = [key for key in ("app_id", "access_token", "decryption_key") if not collected.get(key)]
    if missing:
        nxt = missing[0]
        wf["android_fb_next"] = nxt
        _persist_wf(update, context)
        await message.reply_text(
            f"Принято.\nТеперь пришлите <b>{escape(_FB_FIELD_PROMPTS[nxt])}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    wf.pop("awaiting_android_fb", None)
    wf.pop("android_fb_next", None)
    _log_user_action(update, "android_fb_received", app_id=collected["app_id"])
    _persist_wf(update, context)
    await _run_pipeline(update, context, wf)


class AllowedCallbackQueryHandler(CallbackQueryHandler):
    """CallbackQueryHandler не принимает filters, поэтому режем тем же filters.User."""

    def __init__(self, callback, user_filter: filters.User, pattern: str | None = None) -> None:
        super().__init__(callback, pattern=pattern)
        self.user_filter = user_filter

    def check_update(self, update: object):
        matched = super().check_update(update)
        if not matched:
            return None
        user = update.effective_user if isinstance(update, Update) else None
        if user is None or user.id not in self.user_filter.user_ids:
            return None
        return matched


def _domain_max_price() -> float:
    raw = os.getenv("DOMAIN_MAX_PRICE_USD", "3").strip() or "3"
    try:
        return float(raw)
    except ValueError:
        return 3.0


def _domain_default_ip() -> str:
    return os.getenv("DOMAIN_DEFAULT_IP", "5.75.214.90").strip() or "5.75.214.90"


def _button_markup(pairs: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in pairs])


def _new_domain_job() -> dict[str, Any]:
    return {
        "stage": "mode",
        "max_price": _domain_max_price(),
        "default_ip": _domain_default_ip(),
        "confirmed": False,
        "allow_over_limit": False,
        "quotes": [],
        "domains": [],
        "ip": None,
        "started_at": time.time(),
    }


def _apply_domain_mode(job: dict[str, Any], mode: str) -> str:
    job["started_at"] = time.time()
    if mode == "bulk":
        job["mode"] = "bulk"
        job["stage"] = "count"
        return f"Сколько доменов? От 1 до {MAX_BULK}. Только .top и .sbs."
    if mode == "specific":
        job["mode"] = "specific"
        job["stage"] = "names"
        return "Пришлите точные имена, например backend42.top или несколько через запятую."
    job["mode"] = "setup"
    job["stage"] = "names"
    return (
        "Пришлите уже купленные домены: столбиком, через запятую или как в таблице. "
        "Покупать ничего не буду."
    )


def _domain_expects_text(job: dict[str, Any] | None) -> bool:
    return bool(job) and job.get("stage") in {"count", "names", "ip"}


async def _resume_domain_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    message = update.effective_message
    if share_expects_text(_load_share_job(update, context)):
        _finish_share_job(update, context)
        if message is not None:
            await message.reply_text("Ожидание расшарки снял, продолжаю домены.")
    elif message is not None:
        await message.reply_text("Продолжаю незавершённую покупку доменов.")
    job["started_at"] = time.time()
    _save_domain_job(update, context, job)
    await _prompt_domain_stage(update, context, job)


def _load_domain_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    current = context.user_data.get(DOMAIN_JOB_KEY)
    if isinstance(current, dict):
        return current
    saved = _workflow_store.load(_user_id(update), DOMAIN_APP_KEY)
    if saved and isinstance(saved.get("domain_job"), dict):
        context.user_data[DOMAIN_JOB_KEY] = saved["domain_job"]
        return saved["domain_job"]
    return None


def _save_domain_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    context.user_data[DOMAIN_JOB_KEY] = job
    try:
        _workflow_store.save(_user_id(update), {"app_key": DOMAIN_APP_KEY, "domain_job": job})
    except OSError:
        logger.exception("Не удалось сохранить покупку домена")


def _quote_rows(quotes: list[DomainQuote]) -> list[dict[str, Any]]:
    return [
        {
            "name": quote.name,
            "available": quote.available,
            "price": quote.price_usd,
            "price_text": quote.price_text,
        }
        for quote in quotes
    ]


def _rows_to_quotes(rows: list[dict[str, Any]]) -> list[DomainQuote]:
    return [
        DomainQuote(
            name=str(row.get("name") or ""),
            available=bool(row.get("available")),
            price_usd=row.get("price"),
            price_text=str(row.get("price_text") or ""),
        )
        for row in rows
    ]


def _domain_states_from_quotes(quotes: list[DomainQuote]) -> list[dict[str, Any]]:
    return [
        {
            "name": quote.name,
            "price": quote.price_usd,
            "done": [],
            "failed_step": None,
            "error": None,
            "bought": False,
        }
        for quote in quotes
    ]


def _format_quote_list(quotes: list[DomainQuote], *, limit: float) -> str:
    lines = []
    total = 0.0
    for quote in quotes:
        price = "нет цены" if quote.price_usd is None else f"${quote.price_usd:.2f}"
        mark = ""
        if quote.price_usd is not None and quote.price_usd > limit:
            mark = " — выше лимита"
        if not quote.available:
            mark = " — занят"
        lines.append(f"• <code>{escape(quote.name)}</code> — {price}{mark}")
        if quote.available and quote.price_usd is not None:
            total += quote.price_usd
    lines.append(f"\nИтого к покупке: <b>${total:.2f}</b>")
    return "\n".join(lines)


async def domain_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _body() -> None:
        message = update.message
        if not message:
            return
        if not os.getenv("DYNADOT_API_KEY"):
            await message.reply_text("Не задан DYNADOT_API_KEY.")
            return
        if _get_crm_client() is None:
            await message.reply_text("CRM не настроена.")
            return
        existing = _load_domain_job(update, context)
        if existing and existing.get("stage") not in (None, "done"):
            await _resume_domain_job(update, context, existing)
            return
        if share_expects_text(_load_share_job(update, context)):
            _finish_share_job(update, context)
            await message.reply_text("Ожидание расшарки снял, открываю домены.")
        job = _new_domain_job()
        _save_domain_job(update, context, job)
        await message.reply_text(
            "Покупка домена на Dynadot, затем Cloudflare и CRM.\n"
            f"В пачке беру только .top/.sbs дешевле ${job['max_price']:.0f}.\n"
            "Если домен уже куплен — «Только настройка».",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Пачка случайных", callback_data="dom:mode:bulk")],
                    [InlineKeyboardButton("Конкретные имена", callback_data="dom:mode:specific")],
                    [InlineKeyboardButton("Только настройка", callback_data="dom:mode:setup")],
                ]
            ),
        )

    await _run_locked(update, _body)


async def _domain_job_wants_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    job = _load_domain_job(update, context)
    if not job or job.get("stage") in (None, "done"):
        return False
    await _handle_domain_job_text(update, context, job)
    return True


async def _handle_domain_job_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    job: dict[str, Any],
) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or message.caption or "").strip()
    stage = job.get("stage")
    if stage == "count":
        if not text.isdigit() or not 1 <= int(text) <= MAX_BULK:
            await message.reply_text(f"Нужно число от 1 до {MAX_BULK}.")
            return
        await message.reply_text("Ищу свободные имена и цены…")
        try:
            quotes, attempts = await asyncio.to_thread(
                search_bulk,
                DynadotClient(os.environ["DYNADOT_API_KEY"]),
                int(text),
                max_price=float(job["max_price"]),
            )
        except Exception as exc:
            await message.reply_text(f"Поиск Dynadot: {exc}")
            return
        if len(quotes) < int(text):
            await message.reply_text(
                f"Нашёл {len(quotes)} из {text} (попыток: {attempts}). Можно купить то, что есть."
            )
        if not quotes:
            job["stage"] = "done"
            _save_domain_job(update, context, job)
            await message.reply_text("Подходящих доменов не нашлось.")
            return
        job["quotes"] = _quote_rows(quotes)
        job["stage"] = "ip"
        _save_domain_job(update, context, job)
        await _ask_domain_ip(message, job)
        return
    if stage == "names" and job.get("mode") == "setup":
        names = parse_domain_list(text)
        if not names:
            await message.reply_text(
                "Не вижу доменов. Можно списком, через запятую или таблицей, "
                "например https://www.backend42.top/"
            )
            return
        await message.reply_text("Проверяю, что домены уже лежат в Dynadot…")
        try:
            ready, missing = await asyncio.to_thread(
                _split_owned_domains,
                DynadotClient(os.environ["DYNADOT_API_KEY"]),
                names,
            )
        except Exception as exc:
            await message.reply_text(f"Dynadot: {exc}")
            return
        if missing:
            await message.reply_text(
                "Нет в аккаунте Dynadot, пропускаю:\n" + "\n".join(missing)
            )
        if not ready:
            job["stage"] = "done"
            _save_domain_job(update, context, job)
            await message.reply_text("Настраивать нечего.")
            return
        job["quotes"] = ready
        job["stage"] = "ip"
        _save_domain_job(update, context, job)
        await _ask_domain_ip(message, job)
        return
    if stage == "names":
        names = parse_domain_list(text)
        if not names:
            await message.reply_text("Не вижу доменных имён. Пришлите, например, backend42.top")
            return
        await message.reply_text("Проверяю доступность и цену…")
        try:
            quotes = await asyncio.to_thread(
                search_specific,
                DynadotClient(os.environ["DYNADOT_API_KEY"]),
                " ".join(names),
            )
        except Exception as exc:
            await message.reply_text(f"Поиск Dynadot: {exc}")
            return
        job["quotes"] = _quote_rows(quotes)
        _save_domain_job(update, context, job)
        await message.reply_text(
            _format_quote_list(quotes, limit=float(job["max_price"])),
            parse_mode=ParseMode.HTML,
        )
        if quotes_over_limit(quotes, float(job["max_price"])):
            job["stage"] = "over_limit"
            _save_domain_job(update, context, job)
            await message.reply_text(
                f"Есть домены дороже ${float(job['max_price']):.0f}. Купить их тоже?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Да, включая дорогие", callback_data="dom:over:yes")],
                        [InlineKeyboardButton("Только до лимита", callback_data="dom:over:no")],
                    ]
                ),
            )
            return
        job["allow_over_limit"] = False
        job["stage"] = "ip"
        _save_domain_job(update, context, job)
        await _ask_domain_ip(message, job)
        return
    if stage == "ip":
        if not is_ipv4(text):
            await message.reply_text("Нужен IPv4, например 5.75.214.90")
            return
        job["ip"] = text.strip()
        await _show_domain_confirm(message, job)
        _save_domain_job(update, context, job)
        return
    await message.reply_text("Сейчас жду кнопку под предыдущим сообщением.")


async def _ask_domain_ip(message, job: dict[str, Any]) -> None:
    default_ip = job.get("default_ip") or _domain_default_ip()
    await message.reply_text(
        f"IP для A-записи @. По умолчанию <code>{escape(default_ip)}</code>.\n"
        "Нажмите кнопку или пришлите другой адрес.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"IP {default_ip}", callback_data="dom:ip:default")]]
        ),
    )


def _split_owned_domains(dynadot: DynadotClient, names: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    ready: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        try:
            owned, expires = dynadot.domain_record(name)
        except Exception:
            ready.append({"name": name, "available": True, "price": None, "expiration": None})
            continue
        if not owned:
            missing.append(name)
            continue
        ready.append({"name": name, "available": True, "price": None, "expiration": expires})
    return ready, missing


async def _show_domain_confirm(message, job: dict[str, Any]) -> None:
    if job.get("mode") == "setup":
        rows = [row for row in (job.get("quotes") or []) if row.get("name")]
        if not rows:
            job["stage"] = "done"
            await message.reply_text("Настраивать нечего.")
            return
        job["domains"] = [
            {
                "name": row["name"],
                "price": None,
                "done": [],
                "skip_buy": True,
                "expiration": row.get("expiration"),
                "failed_step": None,
                "error": None,
                "bought": False,
            }
            for row in rows
        ]
        job["stage"] = "confirm"
        job["confirmed"] = False
        lines = "\n".join(f"• <code>{escape(row['name'])}</code>" for row in rows)
        await message.reply_text(
            f"Без покупки, только Cloudflare и CRM:\n{lines}\n"
            f"IP: <code>{escape(str(job.get('ip')))}</code>\n\nПодтвердите запуск.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Настроить", callback_data="dom:buy:yes")],
                    [InlineKeyboardButton("Отмена", callback_data="dom:buy:no")],
                ]
            ),
        )
        return
    quotes = select_for_purchase(
        _rows_to_quotes(job.get("quotes") or []),
        max_price=float(job["max_price"]),
        allow_over_limit=bool(job.get("allow_over_limit")),
    )
    if not quotes:
        job["stage"] = "done"
        await message.reply_text("Нечего покупать: нет свободных доменов с ценой.")
        return
    job["domains"] = _domain_states_from_quotes(quotes)
    job["stage"] = "confirm"
    job["confirmed"] = False
    text = _format_quote_list(quotes, limit=float(job["max_price"]))
    await message.reply_text(
        f"{text}\nIP: <code>{escape(str(job.get('ip')))}</code>\n\n"
        "Это реальное списание на Dynadot. Подтвердите покупку.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Купить", callback_data="dom:buy:yes")],
                [InlineKeyboardButton("Отмена", callback_data="dom:buy:no")],
            ]
        ),
    )


async def _prompt_domain_stage(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    message = update.effective_message
    if message is None:
        return
    stage = job.get("stage")
    if stage == "mode":
        await message.reply_text(
            "Выберите режим покупки.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Пачка случайных", callback_data="dom:mode:bulk")],
                    [InlineKeyboardButton("Конкретные имена", callback_data="dom:mode:specific")],
                    [InlineKeyboardButton("Только настройка", callback_data="dom:mode:setup")],
                ]
            ),
        )
        return
    if stage == "count":
        await message.reply_text(f"Сколько доменов купить? Число от 1 до {MAX_BULK}.")
        return
    if stage == "names":
        await message.reply_text("Пришлите домены, каждый с новой строки или через запятую.")
        return
    if stage == "ip":
        await _ask_domain_ip(message, job)
        return
    if stage == "over_limit":
        await message.reply_text("Подтвердите домены дороже лимита кнопкой выше.")
        return
    if stage == "confirm":
        await _show_domain_confirm(message, job)
        return
    if stage in {"running", "failed"}:
        await message.reply_text(
            "Покупка остановилась. Можно повторить неудачные шаги.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Повторить упавшие шаги", callback_data="dom:retry")]]
            ),
        )


async def handle_domain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _body() -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        job = _load_domain_job(update, context)
        if not job:
            await query.message.reply_text("Нет активной покупки. Начните с /domain")
            return
        data = query.data
        if data in {"dom:mode:bulk", "dom:mode:specific", "dom:mode:setup"}:
            mode = data.rsplit(":", 1)[-1]
            prompt = _apply_domain_mode(job, mode)
            _save_domain_job(update, context, job)
            await query.message.reply_text(prompt)
            return
        if data == "dom:over:yes":
            job["allow_over_limit"] = True
            job["stage"] = "ip"
            _save_domain_job(update, context, job)
            await _ask_domain_ip(query.message, job)
            return
        if data == "dom:over:no":
            job["allow_over_limit"] = False
            job["stage"] = "ip"
            _save_domain_job(update, context, job)
            await _ask_domain_ip(query.message, job)
            return
        if data == "dom:ip:default":
            job["ip"] = job.get("default_ip") or _domain_default_ip()
            _save_domain_job(update, context, job)
            await _show_domain_confirm(query.message, job)
            _save_domain_job(update, context, job)
            return
        if data == "dom:buy:no":
            job["stage"] = "done"
            job["confirmed"] = False
            _save_domain_job(update, context, job)
            if job.get("mode") == "setup":
                await query.message.reply_text("Настройку отменил.")
            else:
                await query.message.reply_text("Покупка отменена. Деньги не списывал.")
            return
        if data in {"dom:buy:yes", "dom:retry"}:
            if data == "dom:buy:yes":
                job["confirmed"] = True
            job["stage"] = "running"
            _save_domain_job(update, context, job)
            await _execute_domain_purchase(update, context, job)
            return
        await query.message.reply_text("Неизвестная кнопка.")

    await _run_locked(update, _body)


async def _execute_domain_purchase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    job: dict[str, Any],
) -> None:
    message = update.effective_message
    if message is None:
        return
    if not job.get("confirmed"):
        await message.reply_text("Сначала подтвердите покупку кнопкой.")
        return
    crm = _get_crm_client()
    if crm is None or not os.getenv("DYNADOT_API_KEY"):
        await message.reply_text("Нет DYNADOT_API_KEY или CRM.")
        return
    await message.reply_text("Покупаю и настраиваю. По каждому домену напишу, что получилось.")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def emit(text: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, text)

    def persist() -> None:
        _save_domain_job(update, context, job)

    async def _pump() -> None:
        while True:
            line = await queue.get()
            if line is None:
                return
            await message.reply_text(line)

    pump = asyncio.create_task(_pump())
    try:
        await asyncio.to_thread(
            run_confirmed_batch,
            job.get("domains") or [],
            dynadot=DynadotClient(os.environ["DYNADOT_API_KEY"]),
            crm=PortalDomainClient(crm),
            ip=str(job.get("ip") or ""),
            confirmed=True,
            cf_factory=None,
            emit=emit,
            on_progress=persist,
            excluded_ids=job.setdefault("excluded_cf_ids", []),
        )
    except Exception as exc:
        emit(f"Остановка: {exc}")
        job["stage"] = "failed"
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)
        await pump
    failed = [item for item in job.get("domains") or [] if item.get("failed_step")]
    if failed:
        job["stage"] = "failed"
        _save_domain_job(update, context, job)
        await message.reply_text(
            "Часть доменов не доехала. Повтор идёт с упавшего шага, уже сделанное не повторяется.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Повторить упавшие шаги", callback_data="dom:retry")]]
            ),
        )
        return
    job["stage"] = "done"
    _save_domain_job(update, context, job)
    await message.reply_text("Готово: домены куплены и заведены в CRM.")


def _load_share_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    current = context.user_data.get(SHARE_JOB_KEY)
    if isinstance(current, dict):
        return current
    saved = _workflow_store.load(_user_id(update), SHARE_APP_KEY)
    if saved and isinstance(saved.get("share_job"), dict):
        context.user_data[SHARE_JOB_KEY] = saved["share_job"]
        return saved["share_job"]
    return None


def _save_share_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    context.user_data[SHARE_JOB_KEY] = job
    try:
        _workflow_store.save(_user_id(update), {"app_key": SHARE_APP_KEY, "share_job": job})
    except Exception:
        logger.exception("Не удалось сохранить расшарку")


def _finish_share_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    job = _load_share_job(update, context) or {"kind": "", "draft": {}}
    job["stage"] = "done"
    _save_share_job(update, context, job)


async def _send_flow(message, step) -> None:
    messages = list(step.messages)
    if not messages:
        return
    for text in messages[:-1]:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    markup = _button_markup(step.buttons) if step.buttons else None
    await message.reply_text(messages[-1], parse_mode=ParseMode.HTML, reply_markup=markup)


async def _run_share_task(message, task) -> None:
    await message.reply_text("Открываю AppsFlyer. По каждому приложению напишу отдельно.")
    try:
        results = await asyncio.to_thread(execute_share, task)
    except AfBlocked as exc:
        for item in exc.results:
            await message.reply_text(format_result(item))
        await message.reply_text(str(exc))
        return
    except AfError as exc:
        await message.reply_text(str(exc))
        return
    except Exception as exc:
        logger.exception("af_share_failed")
        await message.reply_text(f"Остановка: {exc}")
        return
    for item in results:
        await message.reply_text(format_result(item))
    pairs = unity_replace_pairs(task, results)
    if pairs:
        _UNITY_PENDING[message.chat_id] = pairs
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Да", callback_data="unity:yes"), InlineKeyboardButton("Нет", callback_data="unity:no")]]
        )
        await message.reply_text(UNITY_REPLACE_QUESTION, reply_markup=markup)
        return
    if results and all(item.status != "failed" for item in results):
        await message.reply_text("Расшарка закончена.")


_UNITY_PENDING: dict[int, list[tuple[str, str]]] = {}


async def handle_unity_replace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    message = query.message
    pairs = _UNITY_PENDING.pop(message.chat_id, None)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if not pairs:
        await message.reply_text("Запрос устарел, запустите /share_unity заново.")
        return
    if query.data != "unity:yes":
        await message.reply_text("Ок, Game ID не меняю.")
        return
    await _run_share_task(message, ShareTask("unity", pairs=pairs, replace_game_id=True))


async def _start_share(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    async def _body() -> None:
        message = update.effective_message
        if message is None:
            return
        if workflow_reply_kind(_workflow(context)):
            await message.reply_text("Сначала закончите текущее приложение.")
            return
        if _domain_expects_text(_load_domain_job(update, context)):
            await message.reply_text("Сначала закончите домены: бот ждёт число, имена или IP.")
            return
        step = begin_share(kind, message.text or "")
        _save_share_job(update, context, step.job)
        await _send_flow(message, step)
        if step.task:
            await _run_share_task(message, step.task)

    await _run_locked(update, _body)


async def share_partner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_share(update, context, "agency")


async def share_tiktok_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_share(update, context, "tiktok")


async def share_unity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_share(update, context, "unity")


async def share_snapchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_share(update, context, "snapchat")


async def _handle_share_text(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.reply_text("Пришлите текст: id или приложения.")
        return
    step = continue_share(job, text)
    _save_share_job(update, context, step.job)
    await _send_flow(message, step)
    if step.task:
        await _run_share_task(message, step.task)


async def _open_domain_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    message = update.effective_message
    if message is None:
        return
    if not os.getenv("DYNADOT_API_KEY"):
        await message.reply_text("Не задан DYNADOT_API_KEY.")
        return
    if _get_crm_client() is None:
        await message.reply_text("CRM не настроена.")
        return
    existing = _load_domain_job(update, context)
    if existing and existing.get("stage") not in (None, "done", "mode"):
        await _resume_domain_job(update, context, existing)
        return
    if share_expects_text(_load_share_job(update, context)):
        _finish_share_job(update, context)
        await message.reply_text("Ожидание расшарки снял.")
    job = _new_domain_job()
    prompt = _apply_domain_mode(job, mode)
    _save_domain_job(update, context, job)
    await message.reply_text(prompt)


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _body() -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        route = menu_route(query.data)
        message = query.message
        if route is None or message is None:
            return
        flow = route["flow"]
        if flow == "app":
            if share_expects_text(_load_share_job(update, context)):
                _finish_share_job(update, context)
            wf = _workflow(context)
            wf.clear()
            wf["awaiting_app"] = True
            await message.reply_text(APP_WAIT_TEXT, parse_mode=ParseMode.HTML)
            return
        if flow == "domains":
            await message.reply_text("Домены.", reply_markup=_button_markup(domain_menu_buttons()))
            return
        if flow == "domain":
            await _open_domain_mode(update, context, route["mode"])
            return
        if flow == "share" and "kind" not in route:
            await message.reply_text("Расшарка AppsFlyer.", reply_markup=_button_markup(share_menu_buttons()))
            return
        if flow == "share":
            if workflow_reply_kind(_workflow(context)):
                await message.reply_text("Сначала закончите текущее приложение.")
                return
            if _domain_expects_text(_load_domain_job(update, context)):
                await message.reply_text("Сначала закончите домены: бот ждёт число, имена или IP.")
                return
            step = begin_share(route["kind"], "")
            _save_share_job(update, context, step.job)
            await _send_flow(message, step)
            return
        if flow == "cancel":
            _finish_share_job(update, context)
            await message.reply_text("Отменил.")
            return

    await _run_locked(update, _body)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Необработанная ошибка", exc_info=context.error)
    if not isinstance(update, Update) or update.effective_message is None:
        return
    try:
        await update.effective_message.reply_text(
            "❌ <b>Ошибка</b>\n\nЧто-то пошло не так. Попробуйте ещё раз или нажмите «Повторить шаг».",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Не удалось отправить сообщение об ошибке")


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Создайте файл .env с BOT_TOKEN=...")

    allowed = sorted(allowed_user_ids())
    if not allowed:
        logger.warning("ALLOWED_USER_IDS пуст — бот не отвечает никому")
    user_filter = filters.User(user_id=allowed)

    app = Application.builder().token(token).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start, filters=user_filter))
    app.add_handler(CommandHandler("help", help_command, filters=user_filter))
    app.add_handler(CommandHandler("menu", menu_command, filters=user_filter))
    app.add_handler(CommandHandler("app", app_command, filters=user_filter))
    app.add_handler(CommandHandler("domain", domain_command, filters=user_filter))
    app.add_handler(CommandHandler("share_partner", share_partner_command, filters=user_filter))
    app.add_handler(CommandHandler("share_tiktok", share_tiktok_command, filters=user_filter))
    app.add_handler(CommandHandler("share_unity", share_unity_command, filters=user_filter))
    app.add_handler(CommandHandler("share_snapchat", share_snapchat_command, filters=user_filter))
    app.add_handler(CommandHandler("step2", step2_dev, filters=user_filter))
    app.add_handler(CommandHandler("back_android", back_android_dev, filters=user_filter))
    app.add_handler(AllowedCallbackQueryHandler(handle_step_callback, user_filter, pattern=r"^wf:"))
    app.add_handler(AllowedCallbackQueryHandler(handle_domain_callback, user_filter, pattern=r"^dom:"))
    app.add_handler(AllowedCallbackQueryHandler(handle_unity_replace_callback, user_filter, pattern=r"^unity:"))
    app.add_handler(AllowedCallbackQueryHandler(handle_menu_callback, user_filter, pattern=r"^(menu:|share:)"))
    app.add_handler(
        MessageHandler(
            user_filter & (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
            handle_message,
        )
    )
    app.add_handler(MessageHandler(user_filter & filters.TEXT & ~filters.COMMAND, handle_message))

    logger_steps = logging.getLogger("zavod.steps")
    logger_steps.info("Бот запущен. Логи: console + logs/bot.log (steps + HTTP)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

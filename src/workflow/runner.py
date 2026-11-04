from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.binom import BinomError, format_campaign_name
from src.binom.client import BinomClient
from src.crm.client import CrmClient, CrmError
from src.crm.domain import normalize_backend_domain
from src.crm.payload import build_create_app_payload, resolve_apps_created_by
from src.models import Platform, parsed_from_dict
from src.workflow.steps import WorkflowStep

CRM_STATUS_ACTIVE = 2


@dataclass
class StepResult:
    ok: bool
    action: str
    summary: str = ""
    error: str = ""
    skipped: bool = False


def step_done(wf: dict[str, Any], step: WorkflowStep) -> bool:
    return step.value in set(wf.get("completed_steps") or [])


def mark_done(wf: dict[str, Any], step: WorkflowStep) -> None:
    completed = wf.setdefault("completed_steps", [])
    if step.value not in completed:
        completed.append(step.value)
    if wf.get("failed_step") == step.value:
        wf.pop("failed_step", None)
        wf.pop("failed_error", None)


def mark_failed(wf: dict[str, Any], step: WorkflowStep, error: str) -> None:
    wf["failed_step"] = step.value
    wf["failed_error"] = error


def cleanup_crm_temp(wf: dict[str, Any]) -> None:
    path = wf.pop("crm_temp_dir", None)
    if path:
        shutil.rmtree(path, ignore_errors=True)


def resolve_domain(wf: dict[str, Any]) -> str | None:
    parsed = wf.get("parsed") if isinstance(wf.get("parsed"), dict) else {}
    raw = wf.get("domain") or (parsed or {}).get("backend_domain")
    domain = normalize_backend_domain(raw if isinstance(raw, str) else None)
    if domain:
        wf["domain"] = domain
        if isinstance(wf.get("parsed"), dict):
            wf["parsed"]["backend_domain"] = domain
    return domain


def _fb_ready(wf: dict[str, Any]) -> bool:
    fb = wf.get("android_fb") or {}
    return all(fb.get(key) for key in ("app_id", "access_token", "decryption_key"))


def next_action(wf: dict[str, Any]) -> str:
    """Следующее автоматическое действие или причина остановиться и спросить пользователя."""
    if wf.get("awaiting_missing"):
        return "need_missing"
    if not step_done(wf, WorkflowStep.PARSE):
        return "need_parse"
    if not step_done(wf, WorkflowStep.BINOM):
        return "binom"
    if not step_done(wf, WorkflowStep.CRM):
        crm = wf.get("crm") or {}
        if crm.get("id") and not wf.get("crm_ready"):
            return "crm_grant"
        if not wf.get("crm_ready"):
            return "need_crm_files"
        return "crm_create"
    if not step_done(wf, WorkflowStep.DOMAIN):
        if not resolve_domain(wf):
            return "need_domain"
        return "domain"
    if not step_done(wf, WorkflowStep.BACKEND):
        parsed = wf.get("parsed") or {}
        platform = parsed.get("platform")
        backend = str(parsed.get("backend") or "").strip().lower()
        if platform == Platform.ANDROID.value:
            if not _fb_ready(wf):
                return "need_android_fb"
            return "backend"
        if backend == "старый":
            return "backend_unsupported"
        return "backend"
    return "done"


def precheck(
    wf: dict[str, Any],
    binom: BinomClient | None,
    crm: CrmClient | None,
) -> list[str]:
    """Пропустить клонирование и создание карточки, если они уже есть."""
    notes: list[str] = []
    parsed_data = wf.get("parsed")
    if not isinstance(parsed_data, dict):
        return notes
    parsed = parsed_from_dict(parsed_data)
    resolve_domain(wf)

    if binom is not None and not step_done(wf, WorkflowStep.BINOM):
        name = format_campaign_name(parsed)
        found = binom.find_campaign_by_name(name)
        if found is not None:
            wf["binom"] = {
                "id": found.id,
                "name": found.name,
                "url": found.url,
                "key": found.key,
                "reused": True,
            }
            mark_done(wf, WorkflowStep.BINOM)
            notes.append(
                f"Binom: кампания «{found.name}» уже есть (id {found.id}), клонирование пропущено"
            )

    crm_state = wf.get("crm") or {}
    if crm is not None and not step_done(wf, WorkflowStep.CRM) and not crm_state.get("id"):
        found = crm.find_existing_app(
            store_id=parsed.store_id,
            bundle=parsed.bundle,
            name=parsed.name,
        )
        if found is not None:
            app_id = int(found["id"])
            app_name = str(found.get("apps_name") or found.get("name") or parsed.name or "")
            wf["crm"] = {"id": app_id, "name": app_name, "reused": True}
            mark_done(wf, WorkflowStep.CRM)
            notes.append(
                f"CRM: карточка «{app_name}» уже есть (id {app_id}), создание пропущено"
            )
    return notes


def run_binom(wf: dict[str, Any], client: BinomClient, template_id: int) -> StepResult:
    if step_done(wf, WorkflowStep.BINOM):
        existing = wf.get("binom") or {}
        return StepResult(
            ok=True,
            action="binom",
            skipped=True,
            summary=f"Кампания уже есть, id {existing.get('id')}",
        )
    parsed = parsed_from_dict(wf["parsed"])
    name = format_campaign_name(parsed)
    try:
        cloned = client.clone_campaign(name, template_id=template_id)
    except BinomError as exc:
        mark_failed(wf, WorkflowStep.BINOM, str(exc))
        return StepResult(ok=False, action="binom", error=str(exc))
    wf["binom"] = {
        "id": cloned.id,
        "name": cloned.name,
        "url": cloned.url,
        "key": cloned.key,
    }
    mark_done(wf, WorkflowStep.BINOM)
    return StepResult(
        ok=True,
        action="binom",
        summary=f"{cloned.name}\nid {cloned.id}\nkey {cloned.key or '—'}\n{cloned.url}",
    )


def create_crm_app(
    wf: dict[str, Any],
    client: CrmClient,
    team_ids: list[int],
    *,
    persist: Optional[Callable[[], None]] = None,
) -> StepResult:
    if step_done(wf, WorkflowStep.CRM):
        crm = wf.get("crm") or {}
        return StepResult(
            ok=True,
            action="crm",
            skipped=True,
            summary=f"Карточка уже есть, id {crm.get('id')}",
        )

    parsed = parsed_from_dict(wf["parsed"])
    crm = dict(wf.get("crm") or {})
    try:
        if not crm.get("id"):
            files = wf.get("crm_files") or {}
            theme_id = wf.get("crm_theme_id")
            try:
                created_by = resolve_apps_created_by(parsed.owner)
                payload = build_create_app_payload(
                    parsed,
                    logo=files.get("logo"),
                    banner=files.get("banner"),
                    banner_appcraft=files.get("banner_appcraft"),
                    slider=files.get("slider"),
                    apns_key=files.get("apns"),
                    fcm_key=wf.get("fcm_key"),
                    apps_status=CRM_STATUS_ACTIVE,
                    apps_created_by=created_by,
                    theme_id=theme_id,
                    is_private=True,
                )
                created = client.create_app(payload)
            except (CrmError, ValueError) as exc:
                mark_failed(wf, WorkflowStep.CRM, str(exc))
                return StepResult(ok=False, action="crm", error=str(exc))
            crm = {"id": created.id, "name": created.name}
            wf["crm"] = crm
            if persist is not None:
                persist()

        try:
            client.grant_app_access(int(crm["id"]), team_ids=team_ids, user_ids=[])
        except CrmError as exc:
            mark_failed(wf, WorkflowStep.CRM, str(exc))
            return StepResult(
                ok=False,
                action="crm_grant",
                error=f"Приложение создано (ID {crm['id']}), доступ командам не выдан: {exc}",
            )

        wf["crm"] = crm
        wf["crm_collecting"] = False
        wf.pop("crm_ready", None)
        mark_done(wf, WorkflowStep.CRM)
        return StepResult(
            ok=True,
            action="crm",
            summary=f"{crm.get('name') or parsed.name}\nid {crm['id']}",
        )
    finally:
        if wf.get("crm_files") or (wf.get("crm") or {}).get("id"):
            cleanup_crm_temp(wf)


def run_domain(wf: dict[str, Any], client: CrmClient) -> StepResult:
    if step_done(wf, WorkflowStep.DOMAIN):
        return StepResult(ok=True, action="domain", skipped=True, summary="Домен уже привязан")
    crm = wf.get("crm") or {}
    app_id = crm.get("id")
    domain = resolve_domain(wf)
    if not app_id:
        error = "Нет ID приложения из CRM"
        mark_failed(wf, WorkflowStep.DOMAIN, error)
        return StepResult(ok=False, action="domain", error=error)
    if not domain:
        wf["awaiting_domain"] = True
        return StepResult(ok=False, action="need_domain", error="Домен не указан")
    try:
        saved = client.set_app_domain(int(app_id), domain)
    except CrmError as exc:
        mark_failed(wf, WorkflowStep.DOMAIN, str(exc))
        return StepResult(ok=False, action="domain", error=str(exc))
    bound = saved.get("domain") if isinstance(saved, dict) else domain
    wf["domain"] = bound or domain
    wf["domain_bound"] = {
        "apps_id": int(app_id),
        "domain": wf["domain"],
        "catalog": bool(saved.get("catalog")) if isinstance(saved, dict) else False,
    }
    wf.pop("awaiting_domain", None)
    mark_done(wf, WorkflowStep.DOMAIN)
    catalog_note = ""
    if isinstance(saved, dict) and saved.get("catalog"):
        catalog_note = " (нашёл в справочнике доменов)"
    return StepResult(
        ok=True,
        action="domain",
        summary=f"{wf['domain']}{catalog_note}",
    )


def run_ios_backend(wf: dict[str, Any], client: Any) -> StepResult:
    if step_done(wf, WorkflowStep.BACKEND):
        return StepResult(ok=True, action="backend", skipped=True, summary="Бэкенд уже настроен")
    parsed = parsed_from_dict(wf["parsed"])
    binom = wf.get("binom") or {}
    from src.binom.keys import extract_binom_campaign_key

    binom_key = extract_binom_campaign_key(key=binom.get("key"), url=binom.get("url"))
    if not binom_key or binom_key.lower() == "dev":
        error = "Нет ключа кампании Binom"
        mark_failed(wf, WorkflowStep.BACKEND, error)
        return StepResult(ok=False, action="backend", error=error)
    domain = resolve_domain(wf)
    try:
        app = client.find_app(name=parsed.name, domain=domain, store_id=parsed.store_id)
        client.configure_app(app.id, binom_key=binom_key)
    except Exception as exc:
        from src.backend.new_panel import NewBackendError

        if not isinstance(exc, NewBackendError):
            raise
        mark_failed(wf, WorkflowStep.BACKEND, str(exc))
        return StepResult(ok=False, action="backend", error=str(exc))
    base = os.getenv("BACKEND_NEW_URL", "https://neonflightgold777.cfd").rstrip("/")
    wf["backend"] = {
        "type": "new",
        "panel_id": app.id,
        "app_id": app.app_id,
        "domain": app.domain,
        "name": app.name,
        "edit_url": f"{base}/apps/{app.id}/edit",
    }
    mark_done(wf, WorkflowStep.BACKEND)
    return StepResult(
        ok=True,
        action="backend",
        summary=f"{app.name}\npanel {app.id}\n{app.domain}",
    )


def run_android_backend(wf: dict[str, Any], client: Any, organic_link: str) -> StepResult:
    if step_done(wf, WorkflowStep.BACKEND):
        return StepResult(ok=True, action="backend", skipped=True, summary="Бэкенд уже настроен")
    parsed = parsed_from_dict(wf["parsed"])
    fb = wf.get("android_fb") or {}
    try:
        app = client.configure_app(
            parsed.bundle or "",
            fb_app_id=fb["app_id"],
            fb_access_token=fb["access_token"],
            fb_decryption_key=fb["decryption_key"],
            organic_link=organic_link,
        )
    except Exception as exc:
        from src.backend.android_panel import AndroidBackendError

        if not isinstance(exc, AndroidBackendError):
            raise
        mark_failed(wf, WorkflowStep.BACKEND, str(exc))
        return StepResult(ok=False, action="backend", error=str(exc))
    wf["backend"] = {
        "type": "android",
        "panel_id": app.panel_id,
        "bundle": app.bundle_name,
        "site": app.site,
        "edit_url": app.edit_url,
        "organic_link": organic_link,
    }
    wf.pop("awaiting_android_fb", None)
    mark_done(wf, WorkflowStep.BACKEND)
    return StepResult(
        ok=True,
        action="backend",
        summary=f"{app.bundle_name}\n{app.site}",
    )


def execute_next(
    wf: dict[str, Any],
    *,
    binom: BinomClient | None = None,
    crm: CrmClient | None = None,
    new_backend: Any = None,
    android_backend: Any = None,
    template_id: int = 2,
    team_ids: list[int] | None = None,
    organic_link: str = "",
    persist: Optional[Callable[[], None]] = None,
) -> StepResult:
    """Один автоматический шаг. need_* не ходят в API."""
    action = next_action(wf)
    if action in {
        "need_missing",
        "need_parse",
        "need_crm_files",
        "need_domain",
        "need_android_fb",
        "done",
        "backend_unsupported",
    }:
        return StepResult(ok=action == "done", action=action)

    if action == "binom":
        if binom is None:
            error = "Binom API не настроен"
            mark_failed(wf, WorkflowStep.BINOM, error)
            return StepResult(ok=False, action="binom", error=error)
        return run_binom(wf, binom, template_id)

    if action in {"crm_create", "crm_grant"}:
        if crm is None:
            error = "CRM API не настроен"
            mark_failed(wf, WorkflowStep.CRM, error)
            return StepResult(ok=False, action="crm", error=error)
        return create_crm_app(wf, crm, team_ids or [], persist=persist)

    if action == "domain":
        if crm is None:
            error = "CRM API не настроен"
            mark_failed(wf, WorkflowStep.DOMAIN, error)
            return StepResult(ok=False, action="domain", error=error)
        return run_domain(wf, crm)

    if action == "backend":
        parsed = wf.get("parsed") or {}
        if parsed.get("platform") == Platform.ANDROID.value:
            if android_backend is None:
                error = "Android-бэкенд не настроен"
                mark_failed(wf, WorkflowStep.BACKEND, error)
                return StepResult(ok=False, action="backend", error=error)
            return run_android_backend(wf, android_backend, organic_link)
        if new_backend is None:
            error = "Новый бэкенд не настроен"
            mark_failed(wf, WorkflowStep.BACKEND, error)
            return StepResult(ok=False, action="backend", error=error)
        return run_ios_backend(wf, new_backend)

    return StepResult(ok=False, action=action, error=f"Неизвестное действие {action}")

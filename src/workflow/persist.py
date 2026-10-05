from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from .steps import STEP_LABELS, WorkflowStep, next_step

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "workflows"

# JSON-safe keys we persist. Temp dirs / binary paths are dropped.
_PERSIST_KEYS = {
    "parsed",
    "completed_steps",
    "binom",
    "crm",
    "domain",
    "domain_bound",
    "backend",
    "awaiting_missing",
    "awaiting_after",
    "awaiting_domain",
    "awaiting_android_fb",
    "android_fb",
    "android_fb_next",
    "crm_collecting",
    "crm_stage",
    "crm_needs_apns",
    "crm_zip_done",
    "crm_awaiting_theme",
    "crm_theme_id",
    "crm_theme_name",
    "crm_ready",
    "dev_skip_binom",
    "dev_back_android",
    "app_key",
    "crm_needs_recollect",
}


def make_app_key(
    *,
    platform: str | None = None,
    store_id: str | None = None,
    bundle: str | None = None,
    name: str | None = None,
) -> Optional[str]:
    """Stable identity for an app factory run."""
    plat = (platform or "").strip().lower()
    store = (store_id or "").strip().lower()
    bund = (bundle or "").strip().lower()
    if plat and store:
        return f"{plat}:{store}"
    if plat and bund:
        return f"{plat}:{bund}"
    if name and plat:
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        if slug:
            return f"{plat}:name:{slug}"
    return None


def app_key_from_parsed(parsed: Any) -> Optional[str]:
    platform = getattr(parsed, "platform", None)
    platform_value = platform.value if hasattr(platform, "value") else platform
    return make_app_key(
        platform=str(platform_value) if platform_value else None,
        store_id=getattr(parsed, "store_id", None),
        bundle=getattr(parsed, "bundle", None),
        name=getattr(parsed, "name", None),
    )


def app_key_from_wf(wf: dict[str, Any]) -> Optional[str]:
    if wf.get("app_key"):
        return str(wf["app_key"])
    parsed = wf.get("parsed") or {}
    if isinstance(parsed, dict):
        return make_app_key(
            platform=parsed.get("platform"),
            store_id=parsed.get("store_id"),
            bundle=parsed.get("bundle"),
            name=parsed.get("name"),
        )
    return None


def sanitize_wf(wf: dict[str, Any]) -> dict[str, Any]:
    """Copy only JSON-serializable workflow fields."""
    data: dict[str, Any] = {}
    for key in _PERSIST_KEYS:
        if key not in wf:
            continue
        value = wf[key]
        if key in {"crm_files", "crm_temp_dir"}:
            continue
        data[key] = value

    completed = set(data.get("completed_steps") or [])
    crm_done = WorkflowStep.CRM.value in completed
    mid_crm = bool(
        data.get("crm_collecting")
        or data.get("crm_awaiting_theme")
        or (data.get("crm_ready") and not crm_done)
    )
    if mid_crm:
        # Temp files / in-memory CRM draft do not survive restart.
        data["crm_collecting"] = False
        data["crm_awaiting_theme"] = False
        data["crm_ready"] = False
        data.pop("crm_stage", None)
        data.pop("crm_zip_done", None)
        data.pop("crm_theme_id", None)
        data.pop("crm_theme_name", None)
        data["crm_needs_recollect"] = True
    return data


class WorkflowStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_STATE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: int | str, app_key: str) -> Path:
        safe_user = str(user_id)
        safe_app = re.sub(r"[^a-zA-Z0-9._:-]+", "_", app_key)
        user_dir = self.root / safe_user
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / f"{safe_app}.json"

    def save(self, user_id: int | str, wf: dict[str, Any]) -> Optional[Path]:
        app_key = app_key_from_wf(wf)
        if not app_key:
            return None
        wf["app_key"] = app_key
        payload = {
            "user_id": str(user_id),
            "app_key": app_key,
            "updated_at": time.time(),
            "workflow": sanitize_wf(wf),
        }
        path = self._path(user_id, app_key)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("workflow_saved user_id=%s app_key=%s path=%s", user_id, app_key, path)
        return path

    def load(self, user_id: int | str, app_key: str) -> Optional[dict[str, Any]]:
        path = self._path(user_id, app_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("workflow_load_failed path=%s error=%s", path, exc)
            return None
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            return None
        workflow["app_key"] = payload.get("app_key") or app_key
        return workflow

    def delete(self, user_id: int | str, app_key: str) -> None:
        path = self._path(user_id, app_key)
        if path.exists():
            path.unlink()
            logger.info("workflow_deleted user_id=%s app_key=%s", user_id, app_key)

    def find_by_app_key(self, user_id: int | str, app_key: str) -> Optional[dict[str, Any]]:
        return self.load(user_id, app_key)


def is_workflow_complete(wf: dict[str, Any]) -> bool:
    completed = set(wf.get("completed_steps") or [])
    return WorkflowStep.BACKEND.value in completed


def resume_step(wf: dict[str, Any]) -> Optional[WorkflowStep]:
    """Next step to continue from for an incomplete workflow."""
    if is_workflow_complete(wf):
        return None

    if wf.get("awaiting_missing"):
        return WorkflowStep.PARSE
    if wf.get("awaiting_android_fb"):
        return WorkflowStep.BACKEND
    if wf.get("awaiting_domain"):
        return WorkflowStep.DOMAIN
    if wf.get("crm_needs_recollect") or wf.get("crm_awaiting_theme") or wf.get("crm_ready"):
        return WorkflowStep.CRM

    completed = list(wf.get("completed_steps") or [])
    if not completed:
        return WorkflowStep.PARSE

    # Walk from last completed toward the end.
    last = None
    for value in WorkflowStep:
        if value.value in completed:
            last = value
    if last is None:
        return WorkflowStep.PARSE
    nxt = next_step(last)
    return nxt


def resume_summary(wf: dict[str, Any]) -> str:
    parsed = wf.get("parsed") or {}
    name = parsed.get("name") or "—"
    platform = parsed.get("platform") or "—"
    store_id = parsed.get("store_id") or parsed.get("bundle") or "—"
    step = resume_step(wf)
    step_label = STEP_LABELS.get(step, step.value) if step else "завершено"
    completed = wf.get("completed_steps") or []
    done = ", ".join(completed) if completed else "—"
    return (
        f"<b>{escape_html(str(name))}</b> ({escape_html(str(platform))})\n"
        f"ID: <code>{escape_html(str(store_id))}</code>\n"
        f"Сделано: <code>{escape_html(done)}</code>\n"
        f"Продолжить с: <b>{escape_html(step_label)}</b>"
    )


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

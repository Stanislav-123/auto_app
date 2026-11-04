"""Порядок тумблеров прав агентства. Клик без проверки aria-checked не считается успехом."""

from __future__ import annotations

from typing import Protocol

from src.appsflyer.plan import AGENCY_EVENTS, sending_is_all
from src.appsflyer.session import AfError

TOGGLE_ATTEMPTS = 3


class AgencyUi(Protocol):
    def ids(self) -> list[str]: ...

    def has(self, qa_id: str) -> bool: ...

    def checked(self, qa_id: str) -> bool: ...

    def wait_subs(self) -> None: ...

    def wait_enabled(self, qa_id: str) -> None: ...

    def wait_has(self, qa_id: str) -> None: ...

    def click(self, qa_id: str) -> None: ...

    def focus(self, qa_id: str) -> None: ...

    def wait_add_event(self) -> None: ...

    def known_events(self) -> list[str]: ...

    def add_event(self, name: str) -> None: ...

    def sending(self) -> str: ...

    def set_sending(self) -> None: ...


def drive_agency(ui: AgencyUi) -> list[str]:
    """Включить права. Возвращает фактические клики и шаги — для проверки порядка."""
    done: list[str] = []
    if ui.has("AgencyPermissions") and not ui.checked("AgencyPermissions"):
        _ensure_on(ui, "AgencyPermissions", done)
    ui.wait_subs()
    for qa_id in _before_events(ui.ids()):
        if not ui.has(qa_id):
            continue
        ui.wait_enabled(qa_id)
        _ensure_on(ui, qa_id, done)
    ui.wait_add_event()
    present = set(ui.known_events())
    for event in AGENCY_EVENTS:
        if event in present:
            continue
        ui.add_event(event)
        done.append(f"event:{event}")
        present.add(event)
    revenue = "allow_send_events_revenue_toggled"
    if not ui.has(revenue):
        ui.wait_has(revenue)
    ui.wait_enabled(revenue)
    _ensure_on(ui, revenue, done)
    if not sending_is_all(ui.sending()):
        ui.set_sending()
        done.append("sending")
    return done


def _before_events(ids: list[str]) -> list[str]:
    from src.appsflyer.plan import order_agency_toggles

    return [
        item
        for item in order_agency_toggles(ids)
        if item not in {"AgencyPermissions", "allow_send_events_revenue_toggled"}
    ]


def _ensure_on(ui: AgencyUi, qa_id: str, done: list[str]) -> None:
    ui.focus(qa_id)
    for _ in range(TOGGLE_ATTEMPTS):
        if ui.checked(qa_id):
            return
        ui.click(qa_id)
        done.append(f"click:{qa_id}")
        if ui.checked(qa_id):
            return
    ui.focus(qa_id)
    raise AfError(f"Тумблер {qa_id} не включился")

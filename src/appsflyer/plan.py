from __future__ import annotations

from dataclasses import dataclass, field

AGENCY_EVENTS = ("af_app_opened", "af_complete_registration", "af_purchase")

TIKTOK_EVENTS = {
    "af_app_opened": "LaunchAPP",
    "af_complete_registration": "Registration",
    "af_purchase": "Purchase",
}
UNITY_EVENTS = {
    "af_app_opened": "session",
    "af_complete_registration": "level_complete",
    "af_purchase": "purchase",
}
SNAP_EVENTS = {
    "af_app_opened": "APP_OPEN",
    "af_complete_registration": "SIGN_UP",
    "af_purchase": "PURCHASE",
}
PARTNER_IDS = {
    "tiktok": "tiktokglobal_int",
    "unity": "unityads_int",
    "snapchat": "snapchat_int",
}

EVENT_MAPS = {
    "tiktok": TIKTOK_EVENTS,
    "unity": UNITY_EVENTS,
    "snapchat": SNAP_EVENTS,
}


@dataclass
class EventRow:
    af_event: str
    partner_event: str = ""
    sending: str = ""
    send_value: str = ""


@dataclass
class AgencyPage:
    integrated: bool
    toggles: dict[str, bool]
    events: list[str]
    sending: str = ""


@dataclass
class IntegrationPage:
    integrated: bool
    active: bool
    tags: list[str] = field(default_factory=list)
    rows: list[EventRow] = field(default_factory=list)
    game_id: str = ""
    inapp_game_id: str = ""
    mapping_enabled: bool = True


@dataclass
class EventFix:
    af_event: str
    partner_event: str
    create: bool


@dataclass
class AgencyPlan:
    already: bool = False
    need_integrate: bool = False
    turn_on: tuple[str, ...] = ()
    add_events: tuple[str, ...] = ()
    set_sending: bool = False
    problem: str = ""


@dataclass
class IntegrationPlan:
    already: bool = False
    need_integrate: bool = False
    activate: bool = False
    add_tags: tuple[str, ...] = ()
    set_game_id: bool = False
    set_inapp_game_id: bool = False
    enable_mapping: bool = False
    events: tuple[EventFix, ...] = ()
    problem: str = ""


def sending_is_all(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return text == "send-all" or "including organic" in text or "all media sources" in text


def values_and_revenue(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return text == "all" or ("value" in text and "revenue" in text)


def plan_agency(page: AgencyPage) -> AgencyPlan:
    if not page.integrated:
        return AgencyPlan(need_integrate=True)
    if not page.toggles:
        return AgencyPlan(problem="Не вижу переключатели прав агентства")
    turn_on = tuple(key for key, enabled in page.toggles.items() if not enabled)
    if "AgencyPermissions" in turn_on:
        turn_on = ("AgencyPermissions",) + tuple(item for item in turn_on if item != "AgencyPermissions")
    present = {item.strip() for item in page.events}
    add_events = tuple(event for event in AGENCY_EVENTS if event not in present)
    set_sending = not sending_is_all(page.sending)
    if not turn_on and not add_events and not set_sending:
        return AgencyPlan(already=True)
    return AgencyPlan(turn_on=turn_on, add_events=add_events, set_sending=set_sending)


def plan_integration(
    kind: str,
    page: IntegrationPage,
    *,
    tags: list[str] | None = None,
    game_id: str | None = None,
) -> IntegrationPlan:
    if not page.integrated:
        return IntegrationPlan(need_integrate=True)
    wanted = EVENT_MAPS[kind]
    have = {row.af_event: row for row in page.rows}
    fixes: list[EventFix] = []
    for af_event, partner_event in wanted.items():
        row = have.get(af_event)
        if row is None:
            fixes.append(EventFix(af_event, partner_event, True))
            continue
        if not _same(row.partner_event, partner_event) or not sending_is_all(row.sending) or not values_and_revenue(row.send_value):
            fixes.append(EventFix(af_event, partner_event, False))
    existing = {_norm_tag(item) for item in page.tags}
    add_tags = tuple(item for item in (tags or []) if _norm_tag(item) not in existing)
    set_game = False
    set_inapp = False
    if kind == "unity":
        target = (game_id or "").strip()
        set_game = page.game_id.strip() != target
        set_inapp = page.inapp_game_id.strip() != target
    enable_mapping = not page.mapping_enabled
    activate = not page.active
    if not any((activate, add_tags, set_game, set_inapp, enable_mapping, fixes)):
        return IntegrationPlan(already=True)
    return IntegrationPlan(
        activate=activate,
        add_tags=add_tags,
        set_game_id=set_game,
        set_inapp_game_id=set_inapp,
        enable_mapping=enable_mapping,
        events=tuple(fixes),
    )


def _same(left: str, right: str) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


def _norm_tag(value: str) -> str:
    return (value or "").strip().lower()

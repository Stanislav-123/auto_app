from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from src.appsflyer.plan import (
    PARTNER_IDS,
    AgencyPage,
    AgencyPlan,
    EventRow,
    IntegrationPage,
    IntegrationPlan,
    plan_agency,
    plan_integration,
    sending_is_all,
    values_and_revenue,
)
from src.appsflyer.session import (
    LOGIN_URL,
    AfBlocked,
    AfError,
    ShareOutcome,
    headless_enabled,
    login_blocked_reason,
    partner_config_url,
    safe_filename,
    state_path,
    timeout_ms,
)

logger = logging.getLogger(__name__)

_SECTION_LABELS = {
    "extra-params": re.compile(r"TikTok App ID|Snap App ID|Unity Ads Game ID|Game ID|App ID", re.IGNORECASE),
    "inapp-extra-params": re.compile(r"Unity Ads Game ID|Game ID", re.IGNORECASE),
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PlaywrightAppsFlyer:
    """Живой UI AppsFlyer. Состояние «ещё не интегрирован» на TikTok/Unity/Snap не снимали."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        state_file: str | None = None,
        headless: bool | None = None,
        timeout: int | None = None,
        log_dir: str = "logs",
    ) -> None:
        self.email = email
        self.password = password
        self.state_file = Path(state_file or state_path())
        self.headless = headless_enabled() if headless is None else headless
        self.timeout = timeout_ms() if timeout is None else timeout
        self.log_dir = Path(log_dir)
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._target_url = None

    @classmethod
    def from_env(cls) -> PlaywrightAppsFlyer:
        return cls(
            email=(os.getenv("AF_EMAIL") or "").strip(),
            password=(os.getenv("AF_PASSWORD") or "").strip(),
        )

    def __enter__(self) -> PlaywrightAppsFlyer:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context_args = {
            "user_agent": _USER_AGENT,
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
        }
        if self.state_file.exists():
            context_args["storage_state"] = str(self.state_file)
        try:
            self._context = self._browser.new_context(**context_args)
        except Exception:
            logger.warning("af_state_ignored path=%s", self.state_file)
            context_args.pop("storage_state", None)
            self._context = self._browser.new_context(**context_args)
        self._context.set_default_timeout(self.timeout)
        self.page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._save_state()
        self._close()

    def configure_agency(self, app: str, partner_id: str) -> ShareOutcome:
        self._open_partner(app, partner_id)
        plan = plan_agency(self._read_agency())
        if plan.need_integrate:
            logger.info("af_agency_not_integrated app=%s partner=%s", app, partner_id)
            self._try_integrate(app, partner_id)
            plan = plan_agency(self._read_agency())
        self._raise_if_stuck(plan, "агентства")
        if plan.already:
            return ShareOutcome("already", "права уже стоят")
        self._apply_agency(plan)
        self._save_and_confirm()
        return ShareOutcome("done", "права сохранены")

    def configure_tiktok(self, app: str, tiktok_ids: list[str]) -> ShareOutcome:
        return self._configure_network(app, "tiktok", tags=tiktok_ids)

    def configure_snapchat(self, app: str, snap_ids: list[str]) -> ShareOutcome:
        return self._configure_network(app, "snapchat", tags=snap_ids)

    def configure_unity(self, app: str, game_id: str) -> ShareOutcome:
        outcome = self._configure_network(app, "unity", game_id=game_id)
        links = self._read_unity_links()
        detail = "интеграция уже стоит" if outcome.status == "already" else outcome.detail
        return ShareOutcome(outcome.status, detail, links)

    def capture_failure(self, app: str, kind: str) -> str | None:
        if self.page is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.log_dir / f"af-{safe_filename(kind)}-{safe_filename(app)}-{stamp}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            logger.warning("af_screenshot_failed app=%s error=%s", app, exc)
            return None
        logger.info("af_screenshot app=%s path=%s", app, path)
        return str(path)

    def _configure_network(self, app: str, kind: str, *, tags: list[str] | None = None, game_id: str | None = None) -> ShareOutcome:
        partner_id = PARTNER_IDS[kind]
        self._open_partner(app, partner_id)
        plan = plan_integration(kind, self._read_integration(), tags=tags, game_id=game_id)
        if plan.need_integrate:
            logger.info("af_partner_not_integrated app=%s partner=%s", app, partner_id)
            self._try_integrate(app, partner_id)
            plan = plan_integration(kind, self._read_integration(), tags=tags, game_id=game_id)
        self._raise_if_stuck(plan, partner_id)
        if plan.already:
            return ShareOutcome("already", "уже настроено")
        self._apply_integration(plan, game_id=game_id or "")
        self._save_and_confirm()
        return ShareOutcome("done", "интеграция сохранена")

    def ensure_session(self) -> None:
        """До пачки: если сессии нет, форма логина уже на экране — входим сразу."""
        if self.page is None:
            return
        logger.info("af_session_check")
        self._target_url = None
        self._goto(LOGIN_URL)
        if self._race_content_or_login() != "login":
            return
        self._login_and_store()
        if self._login_form_visible():
            self._raise_if_still_login()

    def _open_partner(self, app: str, partner_id: str) -> None:
        url = partner_config_url(app, partner_id)
        self._target_url = url
        logger.info("af_open app=%s partner=%s", app, partner_id)
        self._goto(url)
        self._wait_content()
        self._note_store_banner()

    def _goto(self, url: str) -> None:
        assert self.page is not None
        self.page.goto(url, wait_until="domcontentloaded")

    def _login_form_visible(self) -> bool:
        assert self.page is not None
        url = (self.page.url or "").lower()
        if "/auth/login" in url or url.rstrip("/").endswith("/login"):
            return True
        for selector in (
            "input[type=password]",
            "input[placeholder*='Enter password' i]",
            "input[placeholder*='Enter your email' i]",
            "input[type=email]",
        ):
            field = self.page.locator(selector)
            try:
                if field.count() and field.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _race_content_or_login(self) -> str:
        """Ждём либо кабинет, либо форму входа. Редирект на логин не должен висеть на main."""
        assert self.page is not None
        content = self.page.locator("main.msc__content, main.af-content-wrapper")
        login = self.page.locator(
            "input[type=password], input[placeholder*='Enter password' i], "
            "input[placeholder*='Enter your email' i], input[type=email]"
        )
        try:
            content.or_(login).first.wait_for(state="visible", timeout=self.timeout)
        except Exception as exc:
            blocked = self._blocked_reason()
            if blocked:
                raise AfBlocked(blocked) from exc
            raise AfError("Страница AppsFlyer не открылась: ни кабинет, ни форма входа.") from exc
        blocked = self._blocked_reason()
        if blocked:
            raise AfBlocked(blocked)
        if self._login_form_visible():
            return "login"
        return "content"

    def _login_and_store(self) -> None:
        logger.info("af_login")
        self._submit_login()
        self._save_state()

    def _submit_login(self) -> None:
        assert self.page is not None
        blocked = self._blocked_reason()
        if blocked:
            raise AfBlocked(blocked)
        email = self.page.locator(
            "input[type=email], input[name=username], input[name=email], "
            "input[placeholder*='email' i], input[autocomplete=username]"
        )
        password = self.page.locator("input[type=password], input[placeholder*='password' i]")
        if not email.count() or not password.count():
            raise AfError("Форма входа AppsFlyer без полей email и password.")
        email.first.fill(self.email)
        password.first.fill(self.password)
        remember = self.page.get_by_role("checkbox", name=re.compile(r"Remember me", re.I))
        if remember.count():
            if not remember.first.is_checked():
                remember.first.check()
        else:
            box = self.page.locator("input[type=checkbox]")
            if box.count() and not box.first.is_checked():
                box.first.check()
        self._click_login()
        blocked = self._blocked_reason()
        if blocked:
            raise AfBlocked(blocked)
        try:
            self.page.wait_for_function(
                """() => {
                    const path = location.pathname.toLowerCase();
                    const onLogin = path.includes('/auth/login') || path.endsWith('/login');
                    const pwd = document.querySelector("input[type=password]");
                    const visible = pwd && pwd.offsetParent !== null;
                    return !onLogin && !visible;
                }""",
                timeout=self.timeout,
            )
        except Exception as exc:
            blocked = self._blocked_reason()
            if blocked:
                raise AfBlocked(blocked) from exc
            raise AfError("Не удалось войти в AppsFlyer.") from exc

    def _click_login(self) -> None:
        assert self.page is not None
        button = self.page.get_by_role("button", name=re.compile(r"^(log ?in|login)$", re.I))
        if button.count():
            button.first.click()
            return
        submit = self.page.locator("button[type=submit], input[type=submit]")
        for index in range(submit.count()):
            item = submit.nth(index)
            label = ""
            try:
                label = item.inner_text().lower()
            except Exception:
                label = (item.get_attribute("value") or "").lower()
            if "google" in label or "sso" in label:
                continue
            item.click()
            return
        raise AfError("Не нашёл кнопку Login.")

    def _raise_if_still_login(self) -> None:
        blocked = self._blocked_reason()
        if blocked:
            raise AfBlocked(blocked)
        raise AfError("Не удалось войти в AppsFlyer.")

    def _blocked_reason(self) -> str | None:
        assert self.page is not None
        try:
            text = self.page.inner_text("body")
        except Exception:
            text = ""
        reason = login_blocked_reason(text)
        if reason:
            return reason
        frame = self.page.locator(
            "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[title*='recaptcha' i]"
        )
        try:
            if frame.count() and frame.first.is_visible():
                return "На странице капча. Войдите в AppsFlyer вручную и повторите команду."
        except Exception:
            return None
        return None

    def _wait_content(self) -> None:
        assert self.page is not None
        if self._race_content_or_login() == "login":
            self._login_and_store()
            target = getattr(self, "_target_url", None)
            if target:
                self._goto(target)
            if self._race_content_or_login() == "login":
                self._raise_if_still_login()
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def _looks_like_config(self) -> bool:
        assert self.page is not None
        if self.page.locator("[data-qa-id=save-tab-changes-btn]").count():
            return True
        if self.page.locator("button[data-qa-id=msc-tab-integration], button[data-qa-id=msc-tab-permissions]").count():
            return True
        if self.page.locator("input[data-qa-id=switch-input]").count():
            return True
        return False

    def _try_integrate(self, app: str, partner_id: str) -> None:
        """Прямой URL иногда открывает конфиг с выключенным Activate. Иначе ищем Integrate."""
        assert self.page is not None
        if self._looks_like_config():
            return
        logger.info("af_integrate_fallback app=%s partner=%s url=%s", app, partner_id, self.page.url)
        for pattern in (r"^Integrate$", r"Add integration", r"^Activate$"):
            button = self.page.get_by_role("button", name=re.compile(pattern, re.I))
            if button.count():
                button.first.click()
                self._wait_content()
                if self._looks_like_config():
                    return
        self._goto("https://hq1.appsflyer.com/discovery/overview")
        self._wait_content()
        search = self.page.locator("input[type=search], input[placeholder*='Search' i]").first
        if search.count():
            search.fill(partner_id)
            search.press("Enter")
            self._wait_content()
        hit = self.page.get_by_text(partner_id, exact=False)
        if hit.count():
            hit.first.click()
            self._wait_content()
        integrate = self.page.get_by_role("button", name=re.compile(r"Integrate", re.I))
        if integrate.count():
            integrate.first.click()
            self._wait_content()
        self._goto(partner_config_url(app, partner_id))
        self._wait_content()

    def _raise_if_stuck(self, plan: AgencyPlan | IntegrationPlan, label: str) -> None:
        if plan.problem:
            raise AfError(plan.problem)
        if plan.need_integrate or not self._looks_like_config():
            raise AfError(
                f"Партнёр {label} не открылся для приложения. "
                "Страница без вкладки Integration/Permissions, кнопку Integrate не нашёл."
            )

    def _read_agency(self) -> AgencyPage:
        assert self.page is not None
        self._click_tab("msc-tab-permissions")
        toggles: dict[str, bool] = {}
        nodes = self.page.locator("div.af-toggle-inner[role=checkbox]")
        for index in range(nodes.count()):
            item = nodes.nth(index)
            key = item.get_attribute("data-qa-id") or f"toggle-{index}"
            toggles[key] = (item.get_attribute("aria-checked") or "").lower() == "true"
        events = []
        for name in ("af_app_opened", "af_complete_registration", "af_purchase"):
            if self.page.locator(f'[data-qa-id="selected-option-{name}"]').count():
                events.append(name)
        sending = ""
        box = self.page.locator('[data-qa-id="agency-inapp-sending-options"]')
        if box.count():
            button = box.locator("button[data-qa-id=select-button]")
            if button.count():
                sending = button.first.get_attribute("data-value") or button.first.inner_text()
            elif sending_is_all(box.inner_text()):
                sending = "send-all"
        return AgencyPage(
            integrated=self._looks_like_config(),
            toggles=toggles,
            events=events,
            sending=sending,
        )

    def _apply_agency(self, plan: AgencyPlan) -> None:
        for key in plan.turn_on:
            self._turn_on(f'div.af-toggle-inner[role=checkbox][data-qa-id="{key}"]')
        for event in plan.add_events:
            self._add_agency_event(event)
        if plan.set_sending:
            self._choose("agency-inapp-sending-options", data_value="send-all", label="All media sources, including organic")

    def _add_agency_event(self, event: str) -> None:
        assert self.page is not None
        if self.page.locator(f'[data-qa-id="selected-option-{event}"]').count():
            return
        self.page.locator('[data-qa-id="add-event"]').click()
        search = self.page.locator("input[role=combobox], [role=listbox] input, input[type=text]").last
        search.click()
        search.fill(event)
        try:
            self._pick_open_option(label=event)
        except AfError:
            search.press("Enter")
        self.page.locator(f'[data-qa-id="selected-option-{event}"]').wait_for()

    def _read_integration(self) -> IntegrationPage:
        assert self.page is not None
        self._click_tab("msc-tab-integration")
        active = True
        toggle = self.page.locator("input[data-qa-id=switch-input]").first
        if toggle.count():
            active = toggle.is_checked()
        mapping_root = self.page.locator("[data-qa-id=in-app-mapping]")
        mapping_enabled = True
        if mapping_root.count():
            mapping_toggle = mapping_root.locator("input[data-qa-id=switch-input]")
            if mapping_toggle.count():
                mapping_enabled = mapping_toggle.first.is_checked()
        return IntegrationPage(
            integrated=self._looks_like_config(),
            active=active,
            tags=self._read_tags("extra-params"),
            rows=self._read_rows(),
            game_id=self._read_input("extra-params"),
            inapp_game_id=self._read_input("inapp-extra-params"),
            mapping_enabled=mapping_enabled,
        )

    def _apply_integration(self, plan: IntegrationPlan, *, game_id: str) -> None:
        assert self.page is not None
        if plan.activate:
            self._click_switch(self.page.locator("input[data-qa-id=switch-input]").first)
        for tag in plan.add_tags:
            self._add_tag(tag)
        if plan.set_game_id:
            self._fill_input("extra-params", game_id)
        if plan.set_inapp_game_id:
            self._fill_input("inapp-extra-params", game_id)
        if plan.enable_mapping:
            root = self.page.locator("[data-qa-id=in-app-mapping]")
            self._click_switch(root.locator("input[data-qa-id=switch-input]").first)
        for fix in plan.events:
            if fix.create:
                self._add_mapping_row(fix.af_event)
            self._choose(
                f"events-mapping-dropdown-partnerEventName-{fix.af_event}",
                label=fix.partner_event,
            )
            self._choose(
                f"events-mapping-dropdown-sendingOption-{fix.af_event}",
                data_value="send-all",
                label="All media sources, including organic",
            )
            self._choose(
                f"events-mapping-dropdown-sendValue-{fix.af_event}",
                data_value="all",
                label="Values & revenue",
            )

    def _note_store_banner(self) -> None:
        """На iOS в баннере IDFA, на Android — GAID. Текст разный, поля те же."""
        assert self.page is not None
        try:
            text = self.page.inner_text("body")
        except Exception:
            return
        if re.search(r"\bIDFA\b", text):
            logger.info("af_notice kind=idfa")
        elif re.search(r"\bGAID\b|Google Advertising ID", text, re.IGNORECASE):
            logger.info("af_notice kind=gaid")

    def _section(self, root_qa: str):
        assert self.page is not None
        root = self.page.locator(f'[data-qa-id="{root_qa}"]')
        if root.count():
            return root
        pattern = _SECTION_LABELS.get(root_qa)
        if pattern is None:
            return root
        label = self.page.get_by_text(pattern)
        if not label.count():
            return root
        return label.first.locator("xpath=ancestor::div[.//input][1]")

    def _text_field(self, root):
        field = root.locator("[data-qa-id=game_id] input[data-qa-id=text-field-input]")
        if field.count():
            return field.first
        field = root.locator("input[data-qa-id=text-field-input]")
        if field.count():
            return field.first
        field = root.locator("input[type=text]")
        if field.count():
            return field.last
        return None

    def _read_tags(self, root_qa: str) -> list[str]:
        assert self.page is not None
        root = self._section(root_qa)
        if not root.count():
            return []
        found: list[str] = []
        chips = root.locator('[data-qa-id^="selected-option-"]')
        for index in range(chips.count()):
            chip = chips.nth(index)
            suffix = (chip.get_attribute("data-qa-id") or "").removeprefix("selected-option-").strip()
            text = chip.inner_text().replace("×", "").replace("\u00d7", "").strip()
            value = text or suffix
            if value and value not in found:
                found.append(value)
        return found

    def _add_tag(self, value: str) -> None:
        assert self.page is not None
        field = self._text_field(self._section("extra-params"))
        if field is None:
            raise AfError("Не нашёл поле App ID (ни Android, ни iOS).")
        field.click()
        field.fill(value)
        field.press("Enter")

    def _read_input(self, root_qa: str) -> str:
        assert self.page is not None
        root = self._section(root_qa)
        if not root.count():
            return ""
        field = self._text_field(root)
        if field is None:
            return ""
        return field.input_value().strip()

    def _fill_input(self, root_qa: str, value: str) -> None:
        assert self.page is not None
        field = self._text_field(self._section(root_qa))
        if field is None:
            raise AfError("Не нашёл поле Game ID.")
        field.fill(value)

    def _read_rows(self) -> list[EventRow]:
        assert self.page is not None
        rows: list[EventRow] = []
        nodes = self.page.locator('[data-qa-id^="events-mapping-dropdown-eventName-"]')
        for index in range(nodes.count()):
            qa = nodes.nth(index).get_attribute("data-qa-id") or ""
            af_event = qa.removeprefix("events-mapping-dropdown-eventName-")
            if not af_event:
                continue
            rows.append(
                EventRow(
                    af_event=af_event,
                    partner_event=self._button_label(f"events-mapping-dropdown-partnerEventName-{af_event}"),
                    sending=self._button_value(f"events-mapping-dropdown-sendingOption-{af_event}"),
                    send_value=self._button_value(f"events-mapping-dropdown-sendValue-{af_event}"),
                )
            )
        return rows

    def _add_mapping_row(self, af_event: str) -> None:
        assert self.page is not None
        if self.page.locator(f'[data-qa-id="events-mapping-dropdown-eventName-{af_event}"]').count():
            return
        add = self.page.locator('[data-qa-id=in-app-mapping]').get_by_role("button", name=re.compile(r"Add event", re.I))
        if not add.count():
            add = self.page.get_by_role("button", name=re.compile(r"Add event", re.I))
        add.first.click()
        box = self.page.locator("[data-qa-id=in-app-mapping]")
        buttons = box.locator("button[data-qa-id=select-button]")
        target = buttons.last
        for index in range(buttons.count()):
            current = buttons.nth(index).get_attribute("data-value") or ""
            label = buttons.nth(index).inner_text().strip()
            if not current and not label:
                target = buttons.nth(index)
        target.click()
        self._pick_open_option(label=af_event)
        self.page.locator(f'[data-qa-id="events-mapping-dropdown-eventName-{af_event}"]').wait_for()

    def _read_unity_links(self) -> tuple[str, str]:
        assert self.page is not None
        self._click_tab("msc-tab-tracking")
        self._wait_content()
        areas = self.page.locator('textarea[aria-label="Copy this thing"]')
        areas.first.wait_for()
        click = areas.nth(0).input_value().strip()
        impression = areas.nth(1).input_value().strip() if areas.count() > 1 else ""
        if not click:
            raise AfError("Пустая attribution-ссылка Unity.")
        return click, impression

    def _click_tab(self, qa_id: str) -> None:
        assert self.page is not None
        tab = self.page.locator(f"button[data-qa-id={qa_id}]")
        if tab.count():
            tab.first.click()

    def _button_value(self, qa_id: str) -> str:
        assert self.page is not None
        button = self.page.locator(f'[data-qa-id="{qa_id}"] button[data-qa-id=select-button]')
        if not button.count():
            return ""
        return (button.first.get_attribute("data-value") or button.first.inner_text()).strip()

    def _button_label(self, qa_id: str) -> str:
        assert self.page is not None
        button = self.page.locator(f'[data-qa-id="{qa_id}"] button[data-qa-id=select-button]')
        if not button.count():
            return ""
        return button.first.inner_text().strip()

    def _choose(self, qa_id: str, *, data_value: str | None = None, label: str | None = None) -> None:
        assert self.page is not None
        button = self.page.locator(f'[data-qa-id="{qa_id}"] button[data-qa-id=select-button]')
        if not button.count():
            raise AfError(f"Не нашёл список {qa_id}")
        current_value = (button.first.get_attribute("data-value") or "").strip()
        current_label = button.first.inner_text().strip()
        if data_value and (current_value == data_value or sending_is_all(current_value) and data_value == "send-all"):
            return
        if data_value == "all" and values_and_revenue(current_value):
            return
        if label and label.lower() in current_label.lower():
            return
        if data_value == "send-all" and sending_is_all(current_label):
            return
        if data_value == "all" and values_and_revenue(current_label):
            return
        button.first.click()
        self._pick_open_option(data_value=data_value, label=label)

    def _pick_open_option(self, *, data_value: str | None = None, label: str | None = None) -> None:
        assert self.page is not None
        if data_value:
            option = self.page.locator(f'[role=option][data-value="{data_value}"], [role=menuitem][data-value="{data_value}"]')
            if option.count():
                option.first.click()
                return
        if label:
            exact = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
            for role in ("option", "menuitem"):
                option = self.page.get_by_role(role, name=exact)
                if option.count():
                    option.first.click()
                    return
            loose = re.compile(re.escape(label), re.I)
            option = self.page.get_by_role("option", name=loose)
            if option.count():
                option.first.click()
                return
        raise AfError(f"Не нашёл пункт {label or data_value}")

    def _turn_on(self, selector: str) -> None:
        assert self.page is not None
        item = self.page.locator(selector).first
        if (item.get_attribute("aria-checked") or "").lower() == "true":
            return
        item.click()

    def _click_switch(self, toggle) -> None:
        if not toggle.count() or toggle.is_checked():
            return
        label = toggle.locator("xpath=ancestor::label[1]")
        if label.count():
            label.click()
        else:
            toggle.click(force=True)

    def _save_and_confirm(self) -> None:
        assert self.page is not None
        button = self.page.locator("[data-qa-id=save-tab-changes-btn]")
        if not button.count():
            raise AfError("Нет кнопки сохранения.")
        if button.first.is_disabled():
            raise AfError("Кнопка сохранения неактивна, страница не увидела изменений.")
        button.first.click()
        self.page.get_by_text("All changes saved", exact=False).wait_for()
        self._save_state()

    def _save_state(self) -> None:
        if self._context is None or self.page is None:
            return
        try:
            if self._login_form_visible():
                return
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(self.state_file))
        except Exception as exc:
            logger.warning("af_state_save_failed error=%s", exc)

    def _close(self) -> None:
        for item in (self._context, self._browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self.page = None
        self._context = None
        self._browser = None
        self._pw = None

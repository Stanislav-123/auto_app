import tempfile
import unittest
from pathlib import Path

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
)
from src.appsflyer.parse import ParseError, parse_share
from src.appsflyer.agency import drive_agency, plan_event_picks
from src.appsflyer.tags import TAG_COMMIT_KEYS, choose_app_id_index, tag_already_present
from src.appsflyer.plan import (
    AgencyPage,
    EventRow,
    IntegrationPage,
    order_agency_toggles,
    plan_agency,
    plan_integration,
)
from src.appsflyer.playwright_session import PlaywrightAppsFlyer
from src.appsflyer.session import (
    AfBlocked,
    AfError,
    ShareOutcome,
    ShareResult,
    ShareTask,
    login_blocked_reason,
    partner_config_url,
    run_share,
)


PLAY = "https://play.google.com/store/apps/details?id=io.example.demo"
OTHER = "io.example.other"
TIKTOK = "1234567890123456789"
SNAP = "11111111-2222-4333-8444-555555555555"


class FakeAf:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def configure_agency(self, app, partner_id):
        return self._call(("agency", app, partner_id), app)

    def configure_tiktok(self, app, ids):
        return self._call(("tiktok", app, tuple(ids)), app)

    def configure_unity(self, app, game_id, replace=False):
        mark = ("unity", app, game_id, "replace") if replace else ("unity", app, game_id)
        return self._call(mark, app)

    def configure_snapchat(self, app, ids):
        return self._call(("snapchat", app, tuple(ids)), app)

    def capture_failure(self, app, kind):
        self.calls.append(("shot", app, kind))
        return f"logs/af-{app}.png"

    def _call(self, mark, app):
        self.calls.append(mark)
        item = self.script[app]
        if isinstance(item, Exception):
            raise item
        return item


class TestParse(unittest.TestCase):
    def test_play_link_and_bundle_mixed(self):
        text = f"/share_partner agencytest1\n{PLAY}&hl=en\n{OTHER} {OTHER}"
        parsed = parse_share("agency", text)
        self.assertEqual(parsed.partner_id, "agencytest1")
        self.assertEqual(parsed.apps, ["io.example.demo", OTHER])

    def test_partner_link_is_not_an_app(self):
        link = "https://hq1.appsflyer.com/marketplace/integrated-partners/io.example.demo/agencytest1"
        parsed = parse_share("agency", f"{link} {OTHER}")
        self.assertEqual(parsed.partner_id, "agencytest1")
        self.assertEqual(parsed.apps, [OTHER])

    def test_agency_requires_partner_first(self):
        with self.assertRaises(ParseError):
            parse_share("agency", "io.example.demo")

    def test_tiktok_ids_and_apps_any_order(self):
        parsed = parse_share("tiktok", f"{OTHER} {TIKTOK}\n1234567890123456780")
        self.assertEqual(parsed.ids, [TIKTOK, "1234567890123456780"])
        self.assertEqual(parsed.apps, [OTHER])
        self.assertEqual(parsed.missing, None)

    def test_tiktok_without_ids(self):
        with self.assertRaises(ParseError):
            parse_share("tiktok", OTHER)

    def test_snap_uuid(self):
        parsed = parse_share("snapchat", f"{SNAP.upper()} {PLAY}")
        self.assertEqual(parsed.ids, [SNAP])
        self.assertEqual(parsed.apps, ["io.example.demo"])

    def test_unity_pair_and_shared_game_id(self):
        parsed = parse_share("unity", f"100000001 {OTHER}\n100000002 io.example.demo")
        self.assertEqual(parsed.pairs, [(OTHER, "100000001"), ("io.example.demo", "100000002")])
        shared = parse_share("unity", f"100000001\n{OTHER}\nio.example.demo")
        self.assertEqual(shared.pairs, [(OTHER, "100000001"), ("io.example.demo", "100000001")])

    def test_unity_conflict(self):
        with self.assertRaises(ParseError):
            parse_share("unity", f"100000001 {OTHER}\n100000002 {OTHER}")

    def test_unknown_token(self):
        with self.assertRaises(ParseError):
            parse_share("tiktok", f"{TIKTOK} not-an-app")

    def test_ios_links_prefix_and_bare_number(self):
        store = "https://apps.apple.com/us/app/some-name/id1234567890?mt=8"
        itunes = "https://itunes.apple.com/jp/app/some-name/id1234567891"
        parsed = parse_share("agency", f"agencytest1 {store} {itunes} ID1234567892 123456789012 {PLAY}")
        self.assertEqual(
            parsed.apps,
            ["id1234567890", "id1234567891", "id1234567892", "id123456789012", "io.example.demo"],
        )

    def test_ios_mixed_with_tiktok_ids(self):
        parsed = parse_share("tiktok", f"{TIKTOK} id1234567890 io.example.demo")
        self.assertEqual(parsed.ids, [TIKTOK])
        self.assertEqual(parsed.apps, ["id1234567890", "io.example.demo"])

    def test_unity_ios_app_after_game_id(self):
        one_line = parse_share("unity", "100000001 1234567890")
        self.assertEqual(one_line.pairs, [("id1234567890", "100000001")])
        prefixed = parse_share("unity", "id1234567890 100000001")
        self.assertEqual(prefixed.pairs, [("id1234567890", "100000001")])
        lines = parse_share("unity", "100000001\n1234567890\nio.example.demo")
        self.assertEqual(
            lines.pairs,
            [("id1234567890", "100000001"), ("io.example.demo", "100000001")],
        )

    def test_partner_link_with_ios_app(self):
        link = "https://hq1.appsflyer.com/marketplace/integrated-partners/id1234567890/agencytest1"
        parsed = parse_share("agency", f"{link} io.example.demo")
        self.assertEqual(parsed.partner_id, "agencytest1")
        self.assertEqual(parsed.apps, ["io.example.demo"])


class _AgencyUi:
    SUBS = (
        "access_retention_report",
        "access_aggregate_organic_data",
        "agency_validation_rules_viewable",
        "agency_validation_rules_add",
        "raw_data",
        "p360",
        "creative",
        "allowInappEventsConfiguration",
    )

    def __init__(self, *, flaky: bool = False, have_events: list[str] | None = None):
        self.on = {"AgencyPermissions": False}
        self.enabled = {"AgencyPermissions": True}
        self.clicks: list[str] = []
        self.tries: dict[str, int] = {}
        self.events = list(have_events or [])
        self.sending_value = ""
        self.focused = None
        self.flaky = flaky
        self.add_ready = False

    def ids(self):
        return list(self.on)

    def has(self, qa_id):
        return qa_id in self.on

    def checked(self, qa_id):
        return bool(self.on.get(qa_id))

    def wait_subs(self):
        if not self.on.get("AgencyPermissions"):
            raise AfError("подтумблеры ещё не показаны")

    def wait_enabled(self, qa_id):
        if not self.enabled.get(qa_id):
            raise AfError(f"{qa_id} ещё выключен по зависимости")

    def wait_has(self, qa_id):
        if qa_id not in self.on:
            raise AfError(f"нет тумблера {qa_id}")

    def click(self, qa_id):
        self.clicks.append(qa_id)
        self.tries[qa_id] = self.tries.get(qa_id, 0) + 1
        if self.flaky and self.tries[qa_id] == 1:
            return
        self.on[qa_id] = True
        if qa_id == "AgencyPermissions":
            for sub in self.SUBS:
                self.on.setdefault(sub, False)
                self.enabled[sub] = sub != "agency_validation_rules_add"
        if qa_id == "agency_validation_rules_viewable":
            self.enabled["agency_validation_rules_add"] = True
        if qa_id == "allowInappEventsConfiguration":
            self.add_ready = True
            self.on.setdefault("allow_send_events_revenue_toggled", False)
            self.enabled["allow_send_events_revenue_toggled"] = True

    def focus(self, qa_id):
        self.focused = qa_id

    def wait_add_event(self):
        if not self.add_ready:
            raise AfError("кнопки Add event ещё нет")

    def known_events(self):
        return list(self.events)

    def add_event(self, name):
        if getattr(self, "fail_on", None) == name:
            raise AfError(f"Событие {name} не появилось в списке")
        self.events.append(name)
        return "added"

    def sending(self):
        return self.sending_value

    def set_sending(self):
        self.sending_value = "send-all"


class TestAgencyOrder(unittest.TestCase):
    def test_dependency_order(self):
        ordered = order_agency_toggles(
            [
                "allow_send_events_revenue_toggled",
                "agency_validation_rules_add",
                "AgencyPermissions",
                "allowInappEventsConfiguration",
                "agency_validation_rules_viewable",
                "raw_data",
                "custom_extra",
            ]
        )
        self.assertLess(ordered.index("AgencyPermissions"), ordered.index("agency_validation_rules_viewable"))
        self.assertLess(ordered.index("agency_validation_rules_viewable"), ordered.index("agency_validation_rules_add"))
        self.assertLess(ordered.index("allowInappEventsConfiguration"), ordered.index("custom_extra"))
        self.assertLess(ordered.index("custom_extra"), ordered.index("allow_send_events_revenue_toggled"))

    def test_drive_waits_for_dependencies_and_retries(self):
        ui = _AgencyUi(flaky=True, have_events=["af_app_opened"])
        steps = drive_agency(ui)
        clicks = [item.split(":", 1)[1] for item in steps if item.startswith("click:")]
        self.assertEqual(clicks[0], "AgencyPermissions")
        self.assertEqual(ui.tries["AgencyPermissions"], 2)
        self.assertLess(clicks.index("agency_validation_rules_viewable"), clicks.index("agency_validation_rules_add"))
        self.assertLess(steps.index("click:allowInappEventsConfiguration"), steps.index("event:af_complete_registration"))
        self.assertNotIn("event:af_app_opened", steps)
        self.assertLess(steps.index("event:af_purchase"), steps.index("click:allow_send_events_revenue_toggled"))
        self.assertEqual(steps[-1], "sending")
        self.assertTrue(all(ui.on[item] for item in clicks))
        self.assertEqual(ui.sending_value, "send-all")

    def test_af_app_opened_is_always_added(self):
        none_ready = plan_event_picks(
            ("af_app_opened", "af_complete_registration", "af_purchase"),
            [],
            0,
        )
        self.assertEqual(
            none_ready,
            [
                ("add_row", "af_app_opened"),
                ("add_row", "af_complete_registration"),
                ("add_row", "af_purchase"),
            ],
        )
        one_ready = plan_event_picks(
            ("af_app_opened", "af_complete_registration", "af_purchase"),
            [],
            1,
        )
        self.assertEqual(one_ready[0], ("use_empty", "af_app_opened"))
        self.assertEqual(one_ready[1], ("add_row", "af_complete_registration"))
        ui = _AgencyUi()
        steps = drive_agency(ui)
        self.assertIn("event:af_app_opened", steps)

    def test_event_that_never_appears_fails(self):
        ui = _AgencyUi()
        ui.fail_on = "af_app_opened"
        with self.assertRaises(AfError) as caught:
            drive_agency(ui)
        self.assertIn("af_app_opened", str(caught.exception))
        self.assertIn("не появилось", str(caught.exception))

    def test_already_selected_event_does_not_take_a_row(self):
        picks = plan_event_picks(
            ("af_app_opened", "af_complete_registration", "af_purchase"),
            ["af_purchase", "af_login"],
            1,
        )
        self.assertEqual(
            picks,
            [
                ("use_empty", "af_app_opened"),
                ("add_row", "af_complete_registration"),
                ("skip", "af_purchase"),
            ],
        )


class TestPlan(unittest.TestCase):
    def test_agency_turns_revenue_on_and_adds_event(self):
        page = AgencyPage(
            integrated=True,
            toggles={"AgencyPermissions": True, "allow_send_events_revenue_toggled": False, "raw_data": True},
            events=["af_app_opened", "af_purchase"],
            sending="This partner only",
        )
        plan = plan_agency(page)
        self.assertFalse(plan.already)
        self.assertIn("allow_send_events_revenue_toggled", plan.turn_on)
        self.assertEqual(plan.add_events, ("af_complete_registration",))
        self.assertTrue(plan.set_sending)

    def test_agency_already(self):
        page = AgencyPage(
            integrated=True,
            toggles={"AgencyPermissions": True, "allow_send_events_revenue_toggled": True},
            events=["af_app_opened", "af_complete_registration", "af_purchase"],
            sending="send-all",
        )
        self.assertTrue(plan_agency(page).already)

    def test_tiktok_skips_existing_ids_and_keeps_mapping(self):
        page = IntegrationPage(
            integrated=True,
            active=True,
            tags=[TIKTOK],
            rows=[
                EventRow("af_app_opened", "LaunchAPP", "send-all", "all"),
                EventRow("af_complete_registration", "Registration", "send-all", "all"),
                EventRow("af_purchase", "Purchase", "send-all", "all"),
            ],
            mapping_enabled=True,
        )
        plan = plan_integration("tiktok", page, tags=[TIKTOK, "1234567890123456780"])
        self.assertFalse(plan.already)
        self.assertEqual(plan.add_tags, ("1234567890123456780",))
        self.assertFalse(plan.activate)
        self.assertEqual(plan.events, ())

    def test_unity_fills_both_game_ids_and_fixes_send(self):
        page = IntegrationPage(
            integrated=True,
            active=False,
            game_id="",
            inapp_game_id="100000001",
            rows=[EventRow("af_app_opened", "session", "send-attributed", "all")],
            mapping_enabled=True,
        )
        plan = plan_integration("unity", page, game_id="100000001")
        self.assertTrue(plan.activate)
        self.assertTrue(plan.set_game_id)
        self.assertFalse(plan.set_inapp_game_id)
        created = {item.af_event: item.create for item in plan.events}
        self.assertFalse(created["af_app_opened"])
        self.assertTrue(created["af_complete_registration"])
        self.assertEqual(
            next(item.partner_event for item in plan.events if item.af_event == "af_complete_registration"),
            "level_complete",
        )

    def test_snap_already(self):
        page = IntegrationPage(
            integrated=True,
            active=True,
            tags=[SNAP],
            rows=[
                EventRow("af_app_opened", "APP_OPEN", "All media sources, including organic", "Values & revenue"),
                EventRow("af_complete_registration", "SIGN_UP", "send-all", "all"),
                EventRow("af_purchase", "PURCHASE", "send-all", "all"),
            ],
        )
        self.assertTrue(plan_integration("snapchat", page, tags=[SNAP]).already)

    def test_not_integrated(self):
        page = IntegrationPage(integrated=False, active=False)
        self.assertTrue(plan_integration("tiktok", page, tags=[TIKTOK]).need_integrate)


class TestShareRun(unittest.TestCase):
    def test_isolates_errors_and_screenshots(self):
        fake = FakeAf(
            {
                "io.example.demo": ShareOutcome("done"),
                OTHER: RuntimeError("нет вкладки"),
                "io.example.third": ShareOutcome("already"),
            }
        )
        task = ShareTask(kind="tiktok", ids=[TIKTOK], apps=["io.example.demo", OTHER, "io.example.third"])
        results = run_share(task, fake)
        self.assertEqual([item.status for item in results], ["done", "failed", "already"])
        self.assertIn("скриншот logs/af-io.example.other.png", results[1].reason)
        self.assertEqual(
            [call[0] for call in fake.calls],
            ["tiktok", "tiktok", "shot", "tiktok"],
        )
        self.assertEqual(fake.calls[0][2], (TIKTOK,))

    def test_two_factor_stops_the_rest(self):
        fake = FakeAf(
            {
                "io.example.demo": ShareOutcome("done"),
                OTHER: AfBlocked("AppsFlyer просит код 2FA."),
                "io.example.third": ShareOutcome("done"),
            }
        )
        task = ShareTask(kind="agency", partner_id="agencytest1", apps=["io.example.demo", OTHER, "io.example.third"])
        with self.assertRaises(AfBlocked) as caught:
            run_share(task, fake)
        self.assertEqual([item.app for item in caught.exception.results], ["io.example.demo", OTHER])
        self.assertNotIn(("agency", "io.example.third", "agencytest1"), fake.calls)

    def test_unity_links_passed_through(self):
        links = ("https://app.appsflyer.com/io.example.demo?pid=unityads_int", "https://impression.appsflyer.com/io.example.demo")
        fake = FakeAf({"io.example.demo": ShareOutcome("done", links=links)})
        results = run_share(ShareTask(kind="unity", pairs=[("io.example.demo", "100000001")]), fake)
        self.assertEqual(results[0].links, links)
        self.assertIn("клик:", format_result(results[0]))
        self.assertIn("показ:", format_result(results[0]))

    def test_blocked_page_text(self):
        self.assertIn("2FA", login_blocked_reason("Enter the verification code"))
        self.assertIn("капча", login_blocked_reason("I'm not a robot"))
        self.assertIsNone(login_blocked_reason("Permissions"))
        self.assertIn("agencytest1", partner_config_url("io.example.demo", "agencytest1"))
        self.assertEqual(
            partner_config_url("id1234567890", "tiktokglobal_int"),
            "https://hq1.appsflyer.com/marketplace/integrated-partners/id1234567890/tiktokglobal_int",
        )


class TestMenu(unittest.TestCase):
    def test_buttons(self):
        self.assertEqual(
            [item[0] for item in main_menu_buttons()],
            ["Заведение приложений", "Домены", "Расшарка"],
        )
        self.assertEqual(
            [item[0] for item in domain_menu_buttons()],
            ["Массовая покупка", "Покупка конкретного", "Только настройка"],
        )
        self.assertEqual(
            [item[0] for item in share_menu_buttons()],
            ["Агентский аккаунт", "TikTok", "Unity", "Snapchat"],
        )

    def test_routes(self):
        self.assertEqual(menu_route("menu:app"), {"flow": "app"})
        self.assertEqual(menu_route("menu:domain:bulk"), {"flow": "domain", "mode": "bulk"})
        self.assertEqual(menu_route("menu:domain:specific")["mode"], "specific")
        self.assertEqual(menu_route("menu:domain:setup")["mode"], "setup")
        self.assertEqual(menu_route("menu:share:tiktok"), {"flow": "share", "kind": "tiktok"})
        self.assertEqual(menu_route("share:cancel"), {"flow": "cancel"})
        self.assertIsNone(menu_route("dom:mode:bulk"))

    def test_wizard_agency_then_apps(self):
        step = begin_share("agency", "")
        self.assertTrue(share_expects_text(step.job))
        self.assertEqual(step.job["stage"], "partner")
        step = continue_share(step.job, "agencytest1")
        self.assertEqual(step.job["stage"], "apps")
        self.assertIsNone(step.task)
        step = continue_share(step.job, f"{PLAY}\n{OTHER}")
        self.assertIsNotNone(step.task)
        self.assertEqual(step.task.partner_id, "agencytest1")
        self.assertEqual(step.task.apps, ["io.example.demo", OTHER])
        self.assertFalse(share_expects_text(step.job))

    def test_wizard_accepts_ios_apps(self):
        step = begin_share("snapchat", "")
        step = continue_share(step.job, SNAP)
        self.assertIn("iOS", step.messages[0])
        step = continue_share(
            step.job,
            "https://apps.apple.com/de/app/name/id1234567890\n123456789012",
        )
        self.assertEqual(step.task.apps, ["id1234567890", "id123456789012"])

    def test_command_runs_immediately(self):
        step = begin_share("tiktok", f"/share_tiktok {TIKTOK} {PLAY}")
        self.assertEqual(step.task.ids, [TIKTOK])
        self.assertEqual(step.task.apps, ["io.example.demo"])

    def test_unity_game_id_then_apps(self):
        step = begin_share("unity", "100000001")
        self.assertEqual(step.job["stage"], "pairs")
        step = continue_share(step.job, OTHER)
        self.assertEqual(step.task.pairs, [(OTHER, "100000001")])

    def test_cancel(self):
        step = begin_share("snapchat", "")
        step = continue_share(step.job, "отмена")
        self.assertEqual(step.job["stage"], "done")
        self.assertIsNone(step.task)

    def test_help_lists_commands(self):
        text = help_text()
        for name in ("/app", "/domain", "/menu", "/share_partner", "/share_tiktok", "/share_unity", "/share_snapchat"):
            self.assertIn(name, text)
        self.assertIn("массовая покупка", text)
        self.assertIn("tiktokglobal_int", text)
        self.assertIn("iOS", text)
        self.assertIn("apps.apple.com", text)

    def test_result_labels(self):
        self.assertIn("готово", format_result(ShareResult("io.example.demo", "done")))
        self.assertIn("уже настроено", format_result(ShareResult("io.example.demo", "already")))
        self.assertIn("ошибка: нет страницы", format_result(ShareResult("io.example.demo", "failed", "нет страницы")))
        self.assertNotIn("пропущено", format_result(ShareResult("coc.carwer.towrush", "done", "права сохранены")))


class _State:
    def __init__(self):
        self.path = None

    def storage_state(self, path):
        self.path = path


class _Locator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def _shown(self):
        if getattr(self.page, "captcha", False) and "recaptcha" in self.selector:
            return True
        if self.selector == "either":
            return self.page.mode in {"login", "content"}
        if self.page.mode == "login":
            folded = self.selector.lower()
            return any(token in folded for token in ("password", "email", "checkbox", "remember", "button", "login"))
        if self.page.mode == "content":
            return "msc__content" in self.selector or "af-content-wrapper" in self.selector
        return False

    def count(self):
        return 1 if self._shown() else 0

    @property
    def first(self):
        return self

    def nth(self, index):
        return self

    def is_visible(self):
        return self._shown()

    def is_checked(self):
        return False

    def wait_for(self, state="visible", timeout=None):
        if not self._shown():
            raise TimeoutError(self.selector)

    def or_(self, other):
        return _Locator(self.page, "either")

    def fill(self, value):
        if "password" in self.selector.lower():
            self.page.password_filled = value
        else:
            self.page.email_filled = value

    def click(self):
        self.page.clicked.append(self.selector)
        folded = self.selector.lower()
        if "button" in folded and "login" in folded and "google" not in folded and "sso" not in folded:
            self.page.logged_in = True
            self.page.url = "https://hq1.appsflyer.com/apps/myapps"
            self.page.mode = "content"

    def check(self):
        self.page.remembered = True

    def inner_text(self):
        return "Login"

    def get_attribute(self, name):
        return None

    def locator(self, selector):
        return _Locator(self.page, selector)


class _LoginPage:
    """Редирект на форму входа, пока сессии нет. После Login открывается кабинет."""

    def __init__(self, challenge=None):
        self.url = "about:blank"
        self.mode = "blank"
        self.logged_in = False
        self.challenge = challenge
        self.actions = []
        self.clicked = []
        self.email_filled = None
        self.password_filled = None
        self.remembered = False
        self.captcha = False

    def goto(self, url, wait_until=None):
        self.actions.append(url)
        if self.logged_in:
            self.url = url
            self.mode = "content"
            return
        self.url = "https://hq1.appsflyer.com/auth/login"
        self.mode = "login"

    def locator(self, selector):
        return _Locator(self, selector)

    def get_by_role(self, role, name=None):
        pattern = getattr(name, "pattern", name) or ""
        return _Locator(self, f"role-{role}-{pattern}")

    def get_by_text(self, pattern):
        return _Locator(self, "text")

    def wait_for_function(self, script, timeout=None):
        if not self.logged_in:
            raise TimeoutError("still on login")

    def wait_for_load_state(self, state, timeout=None):
        return None

    def inner_text(self, selector="body"):
        if self.challenge == "2fa":
            return "Enter the verification code from your authenticator"
        if self.mode == "login":
            return "Email Enter your email address Password Enter password Remember me Login Log in with Google"
        return "Permissions"

    def content(self):
        return self.inner_text("body")


class TestLoginRedirect(unittest.TestCase):
    def _session(self, page):
        session = PlaywrightAppsFlyer("user@example.com", "secret-pass")
        session.page = page
        session._context = _State()
        folder = tempfile.mkdtemp()
        session.state_file = Path(folder) / "state.json"
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        return session

    def test_partner_redirect_logs_in_and_continues(self):
        page = _LoginPage()
        session = self._session(page)
        with self.assertLogs("src.appsflyer.playwright_session", level="INFO") as logs:
            session._open_partner("coc.carwer.towrush", "vozartltd")
        text = "\n".join(logs.output)
        self.assertIn("af_login", text)
        self.assertNotIn("secret-pass", text)
        self.assertEqual(page.email_filled, "user@example.com")
        self.assertEqual(page.password_filled, "secret-pass")
        self.assertTrue(page.remembered)
        self.assertTrue(any("coc.carwer.towrush" in url and "vozartltd" in url for url in page.actions))
        self.assertGreaterEqual(sum("coc.carwer.towrush" in url for url in page.actions), 2)
        self.assertIn("integrated-partners/coc.carwer.towrush/vozartltd", page.url)
        self.assertEqual(page.mode, "content")
        self.assertIsNotNone(session._context.path)
        self.assertFalse(any("google" in item.lower() for item in page.clicked))

    def test_session_check_then_share(self):
        page = _LoginPage()
        session = self._session(page)
        opened = []

        def configure_agency(app, partner_id):
            session._open_partner(app, partner_id)
            opened.append((app, partner_id, page.mode))
            return ShareOutcome("done")

        session.configure_agency = configure_agency
        task = ShareTask(kind="agency", partner_id="vozartltd", apps=["coc.carwer.towrush"])
        results = run_share(task, session)
        self.assertEqual(results[0].status, "done")
        self.assertEqual(opened, [("coc.carwer.towrush", "vozartltd", "content")])
        self.assertEqual(page.email_filled, "user@example.com")
        self.assertTrue(page.remembered)

    def test_two_factor_stops_without_saving_session(self):
        page = _LoginPage(challenge="2fa")
        session = self._session(page)
        with self.assertRaises(AfBlocked) as caught:
            session.ensure_session()
        self.assertIn("2FA", str(caught.exception))
        self.assertIsNone(page.password_filled)
        self.assertIsNone(session._context.path)

    def test_visible_captcha_stops(self):
        page = _LoginPage()
        page.captcha = True
        session = self._session(page)
        with self.assertRaises(AfBlocked) as caught:
            session._open_partner("coc.carwer.towrush", "vozartltd")
        self.assertIn("капча", str(caught.exception))
        self.assertIsNone(page.password_filled)


class TestAppIdField(unittest.TestCase):
    def test_snap_and_tiktok_labels(self):
        self.assertEqual(choose_app_id_index(["Snap App ID"]), 0)
        self.assertEqual(choose_app_id_index(["TikTok App ID"]), 0)
        self.assertEqual(choose_app_id_index(["Notes", "Snap App ID"]), 1)

    def test_platform_specific_when_present(self):
        labels = ["Android App ID", "iOS App ID"]
        self.assertEqual(choose_app_id_index(labels, platform="android"), 0)
        self.assertEqual(choose_app_id_index(labels, platform="ios"), 1)
        self.assertEqual(choose_app_id_index(["Snap App ID"], platform="android"), 0)

    def test_class_hint_and_single_fallback(self):
        self.assertEqual(choose_app_id_index(["Field"], ["snap_app_id-test"]), 0)
        self.assertEqual(choose_app_id_index(["General settings"]), 0)
        self.assertIsNone(choose_app_id_index(["Notes", "Cost"]))

    def test_skip_existing_and_commit_order(self):
        self.assertTrue(tag_already_present(["6a8d1073-f9b5-43ad-b185-232e0c52da57"], "6A8D1073-F9B5-43AD-B185-232E0C52DA57"))
        self.assertFalse(tag_already_present(["existing"], "new-id"))
        self.assertEqual(TAG_COMMIT_KEYS, ("Enter", ",", "Tab"))


if __name__ == "__main__":
    unittest.main()


class _FakeLoc:
    def __init__(self, menu, sel):
        self.menu, self.sel = menu, sel

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        if self.sel == "menu":
            return 1
        if self.sel == "search":
            return 1
        if self.sel.startswith("value:"):
            return 1 if self.sel[6:] in self.menu.visible else 0
        return 0

    def locator(self, sel):
        if "search-input" in sel:
            return _FakeLoc(self.menu, "search")
        if sel.startswith("[data-value="):
            return _FakeLoc(self.menu, "value:" + sel.split('"')[1])
        return _FakeLoc(self.menu, "none")

    def filter(self, **_):
        return _FakeLoc(self.menu, "none")

    def fill(self, text):
        self.menu.visible = [v for v in self.menu.all if text.lower() in v.lower()]

    def click(self):
        self.menu.clicked = self.sel[6:]


class _FakeMenuPage:
    def __init__(self):
        self.all = ["Pay", "LaunchAPP", "Registration", "Purchase"]
        self.visible = ["Pay", "LaunchAPP"]
        self.clicked = None

    def locator(self, sel):
        return _FakeLoc(self, "menu")

    def wait_for_timeout(self, _):
        pass


class SearchMenuTest(unittest.TestCase):
    def test_searches_when_option_hidden(self):
        session = PlaywrightAppsFlyer("user@example.com", "secret-pass")
        session.page = _FakeMenuPage()
        self.assertTrue(session._pick_menu_option(label="Purchase"))
        self.assertEqual(session.page.clicked, "Purchase")

    def test_visible_option_without_search(self):
        session = PlaywrightAppsFlyer("user@example.com", "secret-pass")
        session.page = _FakeMenuPage()
        self.assertTrue(session._pick_menu_option(label="LaunchAPP"))
        self.assertEqual(session.page.visible, ["Pay", "LaunchAPP"])


class ValuesRevenueLabelTest(unittest.TestCase):
    def test_no_values_label_is_not_values_and_revenue(self):
        from src.appsflyer.plan import values_and_revenue

        self.assertFalse(values_and_revenue("No values & no revenue"))
        self.assertFalse(values_and_revenue("Values & no revenue"))
        self.assertTrue(values_and_revenue("Values & revenue"))
        self.assertTrue(values_and_revenue("all"))


class UnityReplaceTest(unittest.TestCase):
    def _page(self, gid, inapp=None):
        from src.appsflyer.plan import IntegrationPage

        return IntegrationPage(integrated=True, active=True, tags=[], rows=[], game_id=gid,
                               inapp_game_id=gid if inapp is None else inapp, mapping_enabled=True)

    def test_conflict_only_for_other_id(self):
        from src.appsflyer.plan import unity_conflict

        self.assertEqual(unity_conflict(self._page("1"), "2"), "1")
        self.assertIsNone(unity_conflict(self._page("2"), "2"))
        self.assertIsNone(unity_conflict(self._page(""), "2"))
        self.assertEqual(unity_conflict(self._page("2", "1"), "2"), "1")

    def test_replace_pairs_and_message(self):
        from src.appsflyer.flow import unity_replace_pairs

        task = ShareTask("unity", pairs=[("a.one", "2"), ("a.two", "3")])
        results = [ShareResult("a.one", "conflict", "1"), ShareResult("a.two", "done")]
        self.assertEqual(unity_replace_pairs(task, results), [("a.one", "2")])
        self.assertIn("уже есть интеграция с Юнити", format_result(results[0]))
        self.assertEqual(unity_replace_pairs(ShareTask("tiktok", apps=["a.one"]), results), [])

    def test_replace_flag_reaches_session(self):
        session = FakeAf({"a.one": ShareOutcome("done", "ok", ("c", "i"))})
        run_share(ShareTask("unity", pairs=[("a.one", "2")], replace_game_id=True), session)
        self.assertIn(("unity", "a.one", "2", "replace"), session.calls)


class UnitySettledReadTest(unittest.TestCase):
    def test_waits_for_game_id_to_load(self):
        from src.appsflyer.plan import IntegrationPage

        session = PlaywrightAppsFlyer("user@example.com", "secret-pass")
        pages = [IntegrationPage(True, True, [], [], "", "", True), IntegrationPage(True, True, [], [], "1", "1", True)]
        session._read_integration = lambda: pages.pop(0) if len(pages) > 1 else pages[0]

        class _P:
            def wait_for_timeout(self, _):
                pass

        session.page = _P()
        self.assertEqual(session._read_unity_settled().game_id, "1")

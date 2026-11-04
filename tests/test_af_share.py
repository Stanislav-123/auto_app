import unittest

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
from src.appsflyer.plan import (
    AgencyPage,
    EventRow,
    IntegrationPage,
    plan_agency,
    plan_integration,
)
from src.appsflyer.session import (
    AfBlocked,
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

    def configure_unity(self, app, game_id):
        return self._call(("unity", app, game_id), app)

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

    def test_result_labels(self):
        self.assertIn("готово", format_result(ShareResult("io.example.demo", "done")))
        self.assertIn("уже настроено", format_result(ShareResult("io.example.demo", "already")))
        self.assertIn("ошибка: нет страницы", format_result(ShareResult("io.example.demo", "failed", "нет страницы")))


if __name__ == "__main__":
    unittest.main()

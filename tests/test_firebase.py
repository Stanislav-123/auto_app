import json
import unittest

from src.crm.payload import build_create_app_payload
from src.firebase.key import describe_key, extract_invite_links, fcm_key_string, parse_service_account, project_ref
from src.parser import parse_app_message
from src.workflow.http_log import redact_payload
from tests.test_crm import ANDROID_MESSAGE, IOS_MESSAGE

KEY = {
    "type": "service_account",
    "project_id": "demo-app-123",
    "private_key_id": "abc",
    "private_key": "-----BEGIN PRIVATE KEY-----\nAAA\n-----END PRIVATE KEY-----\n",
    "client_email": "firebase-adminsdk-fbsvc@demo-app-123.iam.gserviceaccount.com",
}


class ServiceAccountTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_service_account(json.dumps(KEY))["project_id"], "demo-app-123")
        self.assertEqual(parse_service_account(("\ufeff" + json.dumps(KEY)).encode())["type"], "service_account")

    def test_rejects_missing_and_wrong_type(self):
        for broken in ({**KEY, "private_key": ""}, {**KEY, "type": "authorized_user"}, {k: v for k, v in KEY.items() if k != "client_email"}):
            with self.assertRaises(ValueError):
                parse_service_account(json.dumps(broken))
        with self.assertRaises(ValueError):
            parse_service_account("not json")
        with self.assertRaises(ValueError):
            parse_service_account("[1]")

    def test_describe_has_no_private_key(self):
        text = describe_key(KEY)
        self.assertNotIn("PRIVATE", text)
        self.assertIn("firebase-adminsdk-fbsvc", text)


class PayloadTest(unittest.TestCase):
    def test_android_fcm_is_whole_json(self):
        payload = build_create_app_payload(parse_app_message(ANDROID_MESSAGE), fcm_key=KEY)
        self.assertEqual(json.loads(payload["fcm_key"]), KEY)

    def test_without_key_stays_zero(self):
        self.assertEqual(build_create_app_payload(parse_app_message(ANDROID_MESSAGE))["fcm_key"], "0")
        self.assertEqual(fcm_key_string(None), "0")

    def test_ios_ignores_fcm(self):
        self.assertEqual(build_create_app_payload(parse_app_message(IOS_MESSAGE), fcm_key=KEY)["fcm_key"], "0")

    def test_log_redacts_key(self):
        payload = build_create_app_payload(parse_app_message(ANDROID_MESSAGE), fcm_key=KEY)
        self.assertNotIn("PRIVATE", json.dumps(redact_payload(payload)))


class ProjectRefTest(unittest.TestCase):
    def test_refs(self):
        self.assertEqual(project_ref("demo-app-123"), ("demo-app-123", "demo-app-123"))
        self.assertEqual(project_ref("Cargo Tower"), (None, "Cargo Tower"))
        self.assertEqual(project_ref("https://console.firebase.google.com/project/demo-app-123/overview"), ("demo-app-123", None))
        self.assertEqual(project_ref(""), (None, None))

    def test_invite_links(self):
        html = '<a href="https://console.cloud.google.com/invitation?project=demo-app-123&amp;account=x">Accept</a> https://example.com'
        self.assertEqual(extract_invite_links(html), ["https://console.cloud.google.com/invitation?project=demo-app-123&account=x"])


class PersistTest(unittest.TestCase):
    def test_fcm_key_is_persisted(self):
        from src.workflow import persist

        self.assertIn("fcm_key", persist._PERSIST_KEYS)


if __name__ == "__main__":
    unittest.main()


class RtdbTest(unittest.TestCase):
    def test_parse_keys_line(self):
        app = parse_app_message(ANDROID_MESSAGE + "\nКлючи firebase - combo, reflex\n")
        from src.firebase.key import split_firebase_keys

        self.assertEqual(split_firebase_keys(app.firebase_keys), ["combo", "reflex"])
        self.assertEqual(split_firebase_keys("Reflex,combo"), ["Reflex", "combo"])
        self.assertEqual(split_firebase_keys(None), [])

    def test_split_domain(self):
        from src.firebase.key import rtdb_values, split_domain

        for domain in ("example.com", "ab", "a.io", "backend42.top"):
            first, second = split_domain(domain)
            self.assertTrue(first and second)
            self.assertEqual(first + second, domain)
        self.assertEqual(list(rtdb_values(["reflex", "combo"], "example.com")), ["reflex", "combo"])
        with self.assertRaises(ValueError):
            rtdb_values(["combo"], "example.com")
        with self.assertRaises(ValueError):
            split_domain("a")

    def test_changes_only_what_differs(self):
        from src.firebase.key import rtdb_changes

        wanted = {"combo": "examp", "reflex": "le.com"}
        self.assertEqual(rtdb_changes({"combo": '"examp"', "reflex": "le.com"}, wanted), {})
        self.assertEqual(rtdb_changes({"combo": "old", "reflex": None}, wanted), wanted)

    def test_rtdb_done_is_persisted(self):
        from src.workflow import persist

        self.assertIn("rtdb_done", persist._PERSIST_KEYS)


class GoogleClassifyTest(unittest.TestCase):
    def test_states(self):
        from src.firebase.google_login import classify

        acc = "https://accounts.google.com/v3/signin/identifier"
        self.assertEqual(classify("https://console.firebase.google.com/", ""), "console")
        self.assertEqual(classify(acc, "Sign in", has_email=True), "email")
        self.assertEqual(classify("https://accounts.google.com/v3/signin/challenge/pwd", "Welcome", has_password=True), "password")
        self.assertEqual(classify("https://accounts.google.com/v3/signin/challenge/dp", "Check your phone"), "challenge")
        self.assertEqual(classify(acc, "Couldn't sign you in. This browser or app may not be secure."), "blocked")
        self.assertEqual(classify(acc, "Wrong password. Try again", has_password=True), "wrong_password")
        self.assertEqual(classify(acc, "Choose an account"), "chooser")


class BrowserPathTest(unittest.TestCase):
    def test_env_path_wins_and_missing_is_none(self):
        import os
        import tempfile
        from unittest import mock

        from src.firebase.console import browser_candidates, browser_path

        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as handle:
            exe = handle.name
        try:
            with mock.patch.dict(os.environ, {"BROWSER_PATH": exe}):
                self.assertEqual(browser_candidates(), [exe])
                self.assertEqual(browser_path(), exe)
            with mock.patch.dict(os.environ, {"BROWSER_PATH": exe + ".nope"}):
                self.assertIsNone(browser_path())
            with mock.patch.dict(os.environ, {"BROWSER_PATH": "", "LOCALAPPDATA": "/tmp/x"}):
                self.assertTrue(any("YandexBrowser" in c for c in browser_candidates()))
        finally:
            os.unlink(exe)


class LivePageTest(unittest.TestCase):
    def test_switches_to_open_tab_or_fails(self):
        from src.firebase.google_login import _live_page
        from src.firebase.key import FcmKeyError

        class P:
            def __init__(self, closed, ctx=None):
                self.closed, self.context = closed, ctx

            def is_closed(self):
                return self.closed

        class C:
            pages = []

        ctx = C()
        old, new = P(True, ctx), P(False, ctx)
        ctx.pages = [old, new]
        self.assertIs(_live_page(old), new)
        ctx.pages = [old]
        with self.assertRaises(FcmKeyError):
            _live_page(old)


class CookiesTest(unittest.TestCase):
    def test_cookie_editor_json(self):
        from src.firebase.cookies import normalize_cookies

        raw = [
            {"name": "SID", "value": "x", "domain": ".google.com", "path": "/", "sameSite": "no_restriction", "secure": True, "expirationDate": 1800000000.5},
            {"name": "S", "value": "y", "domain": "accounts.google.com", "sameSite": "unspecified", "session": True},
            {"name": "bad", "domain": ".google.com"},
        ]
        cookies = normalize_cookies(raw)
        self.assertEqual(len(cookies), 2)
        first = cookies[0]
        self.assertEqual(first["sameSite"], "None")
        self.assertEqual(first["expires"], 1800000000)
        self.assertEqual(first["path"], "/")
        self.assertEqual(cookies[1]["sameSite"], "Lax")
        self.assertNotIn("expires", cookies[1])

    def test_netscape(self):
        import tempfile

        from src.firebase.cookies import load_cookie_file

        line = "\t".join([".google.com", "TRUE", "/", "TRUE", "1800000000", "SID", "secretval"])
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write("# Netscape HTTP Cookie File\n" + line + "\n")
            name = handle.name
        cookies = load_cookie_file(name)
        self.assertEqual(cookies[0]["name"], "SID")
        self.assertEqual(cookies[0]["domain"], ".google.com")
        self.assertTrue(cookies[0]["secure"])


class GoogleOnlyTest(unittest.TestCase):
    def test_filters_other_sites(self):
        from src.firebase.cookies import google_only

        items = [{"domain": d} for d in (".google.com", "console.firebase.google.com", ".paypal.com", "fakegoogle.com", "accounts.google.com")]
        self.assertEqual([c["domain"] for c in google_only(items)], [".google.com", "console.firebase.google.com", "accounts.google.com"])

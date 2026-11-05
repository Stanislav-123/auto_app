import unittest
from unittest.mock import patch

from src.crm.client import CrmClient, CrmError
from src.crm.domain import (
    build_app_domain_payload,
    build_rules_domain_payload,
    normalize_backend_domain,
)


class TestDomainNormalize(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(normalize_backend_domain("yondra36.sbs"), "yondra36.sbs")

    def test_strips_protocol_and_path(self):
        self.assertEqual(
            normalize_backend_domain("https://yondra36.sbs/path"),
            "yondra36.sbs",
        )

    def test_strips_comment_and_port(self):
        self.assertEqual(
            normalize_backend_domain("Yondra36.sbs (уже куплен)"),
            "yondra36.sbs",
        )
        self.assertEqual(normalize_backend_domain("example.sbs:443"), "example.sbs")

    def test_invalid(self):
        self.assertIsNone(normalize_backend_domain(""))
        self.assertIsNone(normalize_backend_domain("bad domain"))


class TestDomainPayload(unittest.TestCase):
    def test_app_update_sets_app_domain_and_keeps_whitelist(self):
        view = {
            "apps_name": "Searock",
            "app_domain": "",
            "appsflyer_dev_key": "k",
            "logo": "should-not-leak",
        }
        payload = build_app_domain_payload(view, "example.sbs")
        self.assertEqual(payload["app_domain"], "example.sbs")
        self.assertEqual(payload["apps_name"], "Searock")
        self.assertEqual(payload["appsflyer_dev_key"], "k")
        self.assertNotIn("logo", payload)

    def test_rules_payload_keeps_model_and_sets_domain(self):
        view = {"model": {"geo_allowed_char": "US", "domain": None, "apps_dev_key": "x"}}
        payload = build_rules_domain_payload(view, 15, "example.sbs")
        self.assertEqual(payload["domain"], "example.sbs")
        self.assertEqual(payload["apps_id"], 15)
        self.assertEqual(payload["geo_allowed_char"], "US")
        self.assertEqual(payload["apps_dev_key"], "x")

    def test_set_app_domain_sends_both_contracts(self):
        client = CrmClient("https://crm.test/api", access_token="t")
        calls = []

        def fake(method, path, payload=None, auth=True):
            calls.append((method, path, payload))
            if path.startswith("/domain/index"):
                return {"models": [{"id": 9, "domain": "Example.SBS"}]}
            if path.startswith("/app/view"):
                return {"apps_name": "Searock", "app_domain": "", "appsflyer_dev_key": "k"}
            if path.startswith("/app/update"):
                return {"app_domain": payload["app_domain"]}
            if path.startswith("/app/rules-view"):
                return {"model": {"geo_allowed_char": "US", "domain": None}}
            if path.startswith("/app/rules-update"):
                return {"model": {"domain": payload["domain"]}}
            raise AssertionError(path)

        with patch.object(client, "_request", side_effect=fake):
            result = client.set_app_domain(15, "https://Example.SBS/a")

        update = next(payload for method, path, payload in calls if path.startswith("/app/update"))
        rules = next(payload for method, path, payload in calls if path.startswith("/app/rules-update"))
        self.assertEqual(update["app_domain"], "Example.SBS")
        self.assertEqual(rules["domain"], "Example.SBS")
        self.assertEqual(rules["apps_id"], 15)
        self.assertEqual(rules["geo_allowed_char"], "US")
        self.assertEqual(result["domain"], "Example.SBS")
        self.assertTrue(result["catalog"])

    def test_rules_not_found_explains_missing_catalog(self):
        client = CrmClient("https://crm.test/api", access_token="t")

        def fake(method, path, payload=None, auth=True):
            if path.startswith("/domain/index"):
                return {"models": []}
            if path.startswith("/app/view"):
                return {"apps_name": "Searock", "app_domain": ""}
            if path.startswith("/app/update"):
                return {"app_domain": payload["app_domain"]}
            if path.startswith("/app/rules-view"):
                return {"model": {"domain": None}}
            if path.startswith("/app/rules-update"):
                raise CrmError('HTTP 422: {"domain": ["Домен не найден"]}')
            raise AssertionError(path)

        with patch.object(client, "_request", side_effect=fake):
            with self.assertRaises(CrmError) as caught:
                client.set_app_domain(15, "missing.sbs")
        self.assertIn("missing.sbs", str(caught.exception))
        self.assertIn("справочник", str(caught.exception).lower())

    def test_catalog_lookup_error_does_not_block_update(self):
        client = CrmClient("https://crm.test/api", access_token="t")
        calls = []

        def fake(method, path, payload=None, auth=True):
            calls.append(path)
            if path.startswith("/domain/index"):
                raise CrmError("HTTP 403: forbidden")
            if path.startswith("/app/view"):
                return {"apps_name": "Searock", "app_domain": ""}
            if path.startswith("/app/update"):
                return {"app_domain": payload["app_domain"]}
            if path.startswith("/app/rules-view"):
                return {"model": {}}
            if path.startswith("/app/rules-update"):
                return {"model": {"domain": payload["domain"]}}
            raise AssertionError(path)

        with patch.object(client, "_request", side_effect=fake):
            result = client.set_app_domain(4, "example.sbs")
        self.assertEqual(result["domain"], "example.sbs")
        self.assertFalse(result["catalog"])
        self.assertTrue(any(path.startswith("/app/update") for path in calls))


    def test_rules_view_soft_domain_error_still_updates(self):
        client = CrmClient("https://crm.test/api", access_token="t")
        calls = []

        def fake(method, path, payload=None, auth=True):
            calls.append((method, path, payload))
            if path.startswith("/domain/index"):
                return {"models": [{"domain": "example.sbs"}]}
            if path.startswith("/app/view"):
                return {"apps_name": "Searock", "app_domain": ""}
            if path.startswith("/app/update"):
                return {"app_domain": payload["app_domain"]}
            if path.startswith("/app/rules-view"):
                return {
                    "model": {"id": 1, "apps_id": 15, "domain": None, "not_domain": True},
                    "error": True,
                    "msg_error": (
                        "Не указан домен. Укажите домен, сохраните настройки "
                        "и заново войдите в режи редактирования!"
                    ),
                }
            if path.startswith("/app/rules-update"):
                return {"model": {"domain": payload["domain"]}}
            raise AssertionError(path)

        with patch.object(client, "_request", side_effect=fake):
            result = client.set_app_domain(15, "example.sbs")

        rules = next(payload for method, path, payload in calls if path.startswith("/app/rules-update"))
        self.assertEqual(rules["domain"], "example.sbs")
        self.assertEqual(result["domain"], "example.sbs")


if __name__ == "__main__":
    unittest.main()

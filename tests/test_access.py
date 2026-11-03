import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error

from telegram.ext import filters

from src.config import parse_allowed_user_ids
from src.crm.client import CrmClient, CrmError
from src.http_errors import transport_failure


class TestWhitelist(unittest.TestCase):
    def test_parse_ids(self):
        self.assertEqual(parse_allowed_user_ids("10, 20"), {10, 20})
        self.assertEqual(parse_allowed_user_ids(""), set())
        self.assertEqual(parse_allowed_user_ids(None), set())

    def test_bad_id(self):
        with self.assertRaises(ValueError):
            parse_allowed_user_ids("10,abc")

    def test_user_filter_blocks_unknown(self):
        allowed = filters.User(user_id=[10, 20])
        denied = filters.User(user_id=[])
        known = SimpleNamespace(from_user=SimpleNamespace(id=10))
        unknown = SimpleNamespace(from_user=SimpleNamespace(id=99))
        self.assertTrue(allowed.filter(known))
        self.assertFalse(allowed.filter(unknown))
        self.assertFalse(denied.filter(known))


class TestTransportErrors(unittest.TestCase):
    def test_timeout_and_urlerror(self):
        self.assertEqual(transport_failure(TimeoutError()), "Таймаут запроса")
        self.assertEqual(
            transport_failure(urllib.error.URLError("down")),
            "Не удалось подключиться: down",
        )
        self.assertIsNone(transport_failure(urllib.error.HTTPError("http://x", 500, "err", {}, None)))

    def test_crm_client_wraps_timeout(self):
        client = CrmClient("https://crm.test/api", access_token="t")
        with patch("src.crm.client.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(CrmError) as caught:
                client._request("GET", "/app/view?id=1")
        self.assertIn("Таймаут", str(caught.exception))

    def test_crm_client_wraps_urlerror(self):
        client = CrmClient("https://crm.test/api", access_token="t")
        with patch(
            "src.crm.client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            with self.assertRaises(CrmError) as caught:
                client._request("GET", "/app/view?id=1")
        self.assertIn("подключиться", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

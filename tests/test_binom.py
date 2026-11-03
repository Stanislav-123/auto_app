import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from src.binom.client import BinomClient, BinomError, _panel_campaigns_url
from src.binom.names import format_campaign_name
from src.binom.templates import get_template_campaign_id
from src.models import Platform
from src.parser import parse_app_message
from tests.test_parser import ANDROID_MESSAGE, IOS_MESSAGE


class TestCampaignName(unittest.TestCase):
    def test_ios_name_format(self):
        app = parse_app_message(IOS_MESSAGE)
        self.assertEqual(
            format_campaign_name(app),
            "[ZM Apps] Searock - id6786185431 - 888 iOS [IO]",
        )

    def test_android_name_format(self):
        app = parse_app_message(ANDROID_MESSAGE)
        self.assertEqual(
            format_campaign_name(app),
            "[ZM Apps] Gаtes оf Оlympus - com.aurumatlas.eonatelier - 888 Android [IO]",
        )


class TestTemplateIds(unittest.TestCase):
    def test_ios_template_id(self):
        self.assertEqual(get_template_campaign_id(Platform.IOS), 2)

    def test_android_template_id(self):
        self.assertEqual(get_template_campaign_id(Platform.ANDROID), 4)


class TestBinomClient(unittest.TestCase):
    def test_clone_campaign_success(self):
        client = BinomClient("https://tracker.test/arm.php", "test-key", template_campaign_id=2)

        full_payload = {
            "campaign": {
                "id": "2",
                "rotation_id": "2",
                "keyword": "abc",
                "name": "template",
                "sources_id": "1",
                "group_id": "3",
                "url": "https://old",
            },
            "routing": {"paths": [], "rules": []},
        }

        with patch.object(client, "_call") as mock_call:
            mock_call.side_effect = [
                full_payload,
                {"status": "added", "id": 99},
                {
                    "id": "99",
                    "name": "[ZM Apps] Test - id1 - 888 iOS [IO]",
                    "url": "https://tracker.test/link",
                    "keyword": "newkey",
                },
            ]

            result = client.clone_campaign("[ZM Apps] Test - id1 - 888 iOS [IO]", template_id=4)

        self.assertEqual(result.id, 99)
        self.assertEqual(mock_call.call_args_list[0].args[1], {"id": 4})
        self.assertEqual(result.url, "https://tracker.test/link")
        add_payload = mock_call.call_args_list[1].args[1]
        self.assertEqual(add_payload["campaign"]["name"], "[ZM Apps] Test - id1 - 888 iOS [IO]")
        self.assertNotIn("id", add_payload["campaign"])
        self.assertNotIn("rotation_id", add_payload["campaign"])

    def test_api_error_raises_binom_error(self):
        client = BinomClient("https://tracker.test/arm.php", "test-key")
        with patch.object(client, "_call", side_effect=BinomError("test error")):
            with self.assertRaises(BinomError):
                client.clone_campaign("name")

    def test_panel_url_from_arm(self):
        self.assertEqual(
            _panel_campaigns_url("https://tracker.test/arm.php"),
            "https://tracker.test/panel.php",
        )

    def test_find_campaign_by_exact_name(self):
        client = BinomClient("https://tracker.test/arm.php", "test-key")
        payload = json.dumps(
            [
                {"id": "3", "name": "other", "status": "1", "keyword": "nope", "url": "https://t/?key=nope"},
                {
                    "id": "8",
                    "name": "[ZM Apps] Searock - id1 - 888 iOS [IO]",
                    "status": "1",
                    "keyword": "abc",
                    "link": "https://tracker.test/c?key=abc",
                },
                {
                    "id": "2",
                    "name": "[ZM Apps] Searock - id1 - 888 iOS [IO]",
                    "status": "0",
                    "keyword": "old",
                    "url": "https://tracker.test/c?key=old",
                },
            ]
        ).encode()

        class Resp:
            status = 200

            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("src.binom.client.urllib.request.urlopen", return_value=Resp()):
            found = client.find_campaign_by_name("[ZM Apps] Searock - id1 - 888 iOS [IO]")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, 8)
        self.assertEqual(found.key, "abc")

    def test_urlerror_becomes_binom_error(self):
        client = BinomClient("https://tracker.test/arm.php", "test-key")
        with patch("src.binom.client.urllib.request.urlopen", side_effect=URLError("refused")):
            with self.assertRaises(BinomError):
                client.list_campaigns("name")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

from src.binom.client import BinomClient, BinomError
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


if __name__ == "__main__":
    unittest.main()

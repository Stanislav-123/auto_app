import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from src.crm.apns import parse_apns_key_file
from src.crm.payload import (
    CREATED_BY_APP_SOLUTIONS,
    CREATED_BY_SIDE_TEAM,
    CREATED_BY_ZM_APPS,
    CRM_PLATFORM_ANDROID,
    CRM_PLATFORM_IOS,
    build_create_app_payload,
    resolve_apps_created_by,
)
from src.crm.zip_assets import classify_image_filename, extract_crm_images_from_zip
from src.models import ParsedApp, Platform
from src.parser import parse_app_message
from tests.test_parser import ANDROID_MESSAGE, IOS_MESSAGE

APNS_JSON = """{
    "team_id": "U3Y79T24L8",
    "key_p8": "-----BEGIN PRIVATE KEY-----\\nTEST\\n-----END PRIVATE KEY-----",
    "key_id": "RC86TX5F56"
}"""


class TestCrmPayload(unittest.TestCase):
    def test_ios_payload_private_active(self):
        app = parse_app_message(IOS_MESSAGE)
        payload = build_create_app_payload(
            app,
            logo="data:image/png;base64,AAA",
            banner="data:image/png;base64,BBB",
            banner_appcraft="data:image/png;base64,CCC",
            slider="data:image/png;base64,DDD",
            apns_key={"team_id": "TEAM", "key_id": "KEY", "key_p8": "BEGIN"},
            theme_id=1,
        )

        self.assertEqual(payload["apps_name"], "Searock")
        self.assertEqual(payload["apps_status"], 2)
        self.assertEqual(payload["is_private"], 1)
        self.assertEqual(payload["apps_general_application"], 0)
        self.assertEqual(payload["platform"], CRM_PLATFORM_IOS)
        self.assertEqual(payload["apps_created_by"], CREATED_BY_ZM_APPS)
        self.assertEqual(payload["theme_id"], "1")
        self.assertEqual(payload["apps_link_status"], 1)
        self.assertIn("team_id", json.loads(payload["apns_key"]))

    def test_android_payload(self):
        app = parse_app_message(ANDROID_MESSAGE)
        payload = build_create_app_payload(app)

        self.assertEqual(payload["platform"], CRM_PLATFORM_ANDROID)
        self.assertEqual(payload["apps_created_by"], CREATED_BY_SIDE_TEAM)
        self.assertEqual(payload["apns_key"], 0)
        self.assertEqual(payload["apps_link_status"], 0)

    def test_created_by_mapping(self):
        self.assertEqual(resolve_apps_created_by("ZMApps"), CREATED_BY_ZM_APPS)
        self.assertEqual(resolve_apps_created_by("ZM Apps"), CREATED_BY_ZM_APPS)
        self.assertEqual(resolve_apps_created_by("App Solutions"), CREATED_BY_APP_SOLUTIONS)
        self.assertEqual(resolve_apps_created_by("AppSolution"), CREATED_BY_APP_SOLUTIONS)
        self.assertEqual(resolve_apps_created_by("Сторонний разработчик"), CREATED_BY_SIDE_TEAM)

    def test_missing_required_raises(self):
        app = ParsedApp(platform=Platform.IOS, name="Test")
        with self.assertRaises(ValueError):
            build_create_app_payload(app)


class TestApnsParser(unittest.TestCase):
    def test_parse_apns_json(self):
        result = parse_apns_key_file(APNS_JSON, "apns.json")
        self.assertEqual(result["team_id"], "U3Y79T24L8")
        self.assertEqual(result["key_id"], "RC86TX5F56")
        self.assertIn("BEGIN PRIVATE KEY", result["key_p8"])

    def test_parse_apns_filename(self):
        result = parse_apns_key_file("PRIVATE KEY", "ABCDE12345_XYZ98765.p8")
        self.assertEqual(result["team_id"], "ABCDE12345")
        self.assertEqual(result["key_id"], "XYZ98765")

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            parse_apns_key_file("{bad", "apns.json")


class TestZipAssets(unittest.TestCase):
    def test_classify_ios_names(self):
        self.assertEqual(classify_image_filename("logo.png"), "logo")
        self.assertEqual(classify_image_filename("ZM_iOS Banner.png"), "banner")
        self.assertEqual(classify_image_filename("AC_iOS Banner.png"), "banner_appcraft")
        self.assertEqual(classify_image_filename("compressed_banner.png"), "slider")

    def test_classify_android_names(self):
        self.assertEqual(classify_image_filename("ZM_Android Banner.png"), "banner")
        self.assertEqual(classify_image_filename("AC_Android Banner.png"), "banner_appcraft")

    def test_extract_from_zip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "assets.zip"
            extract_dir = tmp_path / "out"

            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("logo.png", b"logo")
                archive.writestr("ZM_iOS Banner.png", b"banner")
                archive.writestr("AC_iOS Banner.png", b"appcraft")
                archive.writestr("compressed_banner.png", b"slider")

            result = extract_crm_images_from_zip(zip_path, extract_dir)
            self.assertEqual(set(result.keys()), {"logo", "banner", "banner_appcraft", "slider"})
            for value in result.values():
                self.assertTrue(value.startswith("data:image/"))


if __name__ == "__main__":
    unittest.main()

import unittest

from src.backend.constants import (
    GEO_ALLOWED_CODES,
    NEW_BACKEND_APPSFLYER_KEY,
    NEW_BACKEND_APPSFLYER_KEY_API_V2,
    NEW_BACKEND_BINOM_URL,
    geo_allowed_android,
    geo_allowed_new,
    geo_allowed_old,
)
from src.backend.android_panel import (
    android_site_from_bundle,
    build_android_organic_link,
    _parse_form_fields,
)
from src.backend.fb_settings import parse_facebook_settings
from src.backend.new_panel import _parse_form_fields as parse_new_form_fields
from src.binom.keys import extract_binom_campaign_key


class TestGeoFormats(unittest.TestCase):
    def test_new_is_comma_separated(self):
        value = geo_allowed_new()
        self.assertEqual(value.split(","), GEO_ALLOWED_CODES)
        self.assertIn("AL,AT,BE", value[:20])

    def test_old_is_newline_separated(self):
        value = geo_allowed_old()
        self.assertEqual(value.splitlines(), GEO_ALLOWED_CODES)

    def test_android_is_space_separated(self):
        value = geo_allowed_android()
        self.assertEqual(value.split(), GEO_ALLOWED_CODES)


class TestNewBackendConstants(unittest.TestCase):
    def test_fixed_values(self):
        self.assertEqual(NEW_BACKEND_BINOM_URL, "https://grand4clickz.com/c5wzl6k.php")
        self.assertEqual(NEW_BACKEND_APPSFLYER_KEY, "P8Cmc5f5JjkNjQ3haoGbWS")
        self.assertTrue(NEW_BACKEND_APPSFLYER_KEY_API_V2.startswith("eyJ"))
        self.assertNotIn(" ", NEW_BACKEND_APPSFLYER_KEY_API_V2)


class TestBinomKeyExtract(unittest.TestCase):
    def test_prefers_explicit_key(self):
        self.assertEqual(
            extract_binom_campaign_key(key="mykey", url="https://t.com/?key=other"),
            "mykey",
        )

    def test_from_url(self):
        self.assertEqual(
            extract_binom_campaign_key(url="https://tracker.test/c?key=abc%201"),
            "abc 1",
        )

    def test_skips_dev_placeholder(self):
        self.assertEqual(
            extract_binom_campaign_key(key="dev", url="https://t.com/?key=real"),
            "real",
        )


class TestAndroidHelpers(unittest.TestCase):
    def test_site_from_bundle(self):
        self.assertEqual(
            android_site_from_bundle("com.skyforgenomads.sky"),
            "backend.comskyforgenomadssky.top",
        )
        self.assertEqual(
            android_site_from_bundle("com.aurumatlas.eonatelier"),
            "backend.comaurumatlaseonatelier.top",
        )

    def test_organic_link_from_key(self):
        self.assertEqual(
            build_android_organic_link(key="20aem9pgxubiwwlv8nay"),
            "https://grand4clickz.com/c5wzl6k.php?key=20aem9pgxubiwwlv8nay",
        )

    def test_organic_link_keeps_url_with_key(self):
        url = "https://grand4clickz.com/c5wzl6k.php?key=abc"
        self.assertEqual(build_android_organic_link(url=url, key="other"), url)

    def test_organic_link_keeps_other_tracker_domain(self):
        url = "https://other-tracker.example/click.php?key=xyz123"
        self.assertEqual(build_android_organic_link(url=url, key="ignored"), url)

    def test_parse_form_keeps_hidden_id(self):
        html = """
        <form method="post" action="/qize4nmp/save">
          <input type="hidden" name="id" value="642">
          <input type="text" name="bundle_name" value="com.example.app">
          <input type="text" name="site" value="backend.comexampleapp.top">
          <select name="mmp_used"><option value="0" selected="selected">AF</option></select>
          <textarea name="geo_allowed">IN</textarea>
        </form>
        """
        data = _parse_form_fields(html)
        self.assertEqual(data["id"], "642")
        self.assertEqual(data["bundle_name"], "com.example.app")
        self.assertEqual(data["mmp_used"], "0")

    def test_new_form_unescapes_json_entities(self):
        raw_json = (
            '{"att_token":"latch","appsflyer_id":"grove","bundle":"iron"}'
        )
        encoded = raw_json.replace('"', "&#34;")
        html = f"""
        <form method="post" action="/apps/12/edit">
          <textarea name="settings_alias_parametr_json">{encoded}</textarea>
          <input type="text" name="app_name" value="Test &amp; Co">
        </form>
        """
        data = parse_new_form_fields(html)
        self.assertEqual(data["settings_alias_parametr_json"], raw_json)
        self.assertEqual(data["app_name"], "Test & Co")
        self.assertNotIn("&#34;", data["settings_alias_parametr_json"])


class TestFacebookSettings(unittest.TestCase):
    def test_three_lines(self):
        parsed = parse_facebook_settings("1695062302334653\nabc123\ndecryptkey")
        self.assertEqual(parsed.app_id, "1695062302334653")
        self.assertEqual(parsed.access_token, "abc123")
        self.assertEqual(parsed.decryption_key, "decryptkey")

    def test_labeled(self):
        parsed = parse_facebook_settings(
            "App ID: 111\nSecret key: tok\nDecryption key: dec"
        )
        self.assertEqual(parsed.app_id, "111")
        self.assertEqual(parsed.access_token, "tok")
        self.assertEqual(parsed.decryption_key, "dec")


if __name__ == "__main__":
    unittest.main()

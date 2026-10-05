import unittest

from src.crm.domain import normalize_backend_domain


class TestDomainNormalize(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(normalize_backend_domain("yondra36.sbs"), "yondra36.sbs")

    def test_strips_protocol_and_path(self):
        self.assertEqual(
            normalize_backend_domain("https://yondra36.sbs/path"),
            "yondra36.sbs",
        )

    def test_invalid(self):
        self.assertIsNone(normalize_backend_domain(""))
        self.assertIsNone(normalize_backend_domain("bad domain"))


if __name__ == "__main__":
    unittest.main()

import random
import unittest
from datetime import date
from unittest.mock import patch

from src.domains.cloudflare import cloudflare_auth_headers
from src.domains.dynadot import DynadotError, expiration_to_date, parse_domain_info, parse_search_response, ensure_command_ok
from src.domains.names import generate_candidate
from src.domains.payloads import cloudflare_account_update_payload, domain_create_payload
from src.domains.pricing import expiration_in_one_year, is_affordable, parse_domain_list, parse_usd_price
from src.domains.setup import (
    collect_affordable,
    quotes_over_limit,
    register_available,
    reset_reservations,
    run_confirmed_batch,
    select_for_purchase,
)
from src.domains.pricing import DomainQuote
from src.workflow.http_log import format_body, mask_url, redact_payload


def _account(crm_id, cf_id="cf-acc"):
    return {
        "id": crm_id,
        "email": f"u{crm_id}@cf.test",
        "password": f"secret-{crm_id}",
        "auth_key": f"cfk_{crm_id}",
        "account_id": cf_id,
        "first_ns": "old1.ns",
        "second_ns": "old2.ns",
        "status": 0,
    }


class FakeDyn:
    def __init__(self, fail=None):
        self.fail = set(fail or [])
        self.registered = []
        self.ns = []

    def register(self, name, duration=1):
        if name in self.fail:
            raise DynadotError("не хватило баланса")
        self.registered.append(name)

    def set_ns(self, domain, ns0, ns1):
        self.ns.append((domain, ns0, ns1))


class FakeCF:
    def __init__(self):
        self.calls = []

    def ensure_zone(self, name):
        self.calls.append(("zone", name))
        from src.domains.cloudflare import ZoneInfo

        return ZoneInfo(id=f"z-{name}", name_servers=["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"], created=True)

    def ensure_apex_a(self, zone_id, domain, ip):
        self.calls.append(("a", domain, ip))
        return True

    def ensure_ssl_flexible(self, zone_id):
        self.calls.append(("ssl", zone_id))
        return True


class FakeCRM:
    def __init__(self, accounts):
        self.accounts = accounts
        self.busy = []
        self.created = []

    def list_free_cloudflare_accounts(self):
        return [item for item in self.accounts if str(item.get("status", 0)) == "0"]

    def mark_cloudflare_account_busy(self, crm_id, payload):
        self.busy.append((crm_id, payload))
        for item in self.accounts:
            if str(item.get("id")) == str(crm_id):
                item["status"] = 1

    def domain_exists(self, name):
        return False

    def create_portal_domain(self, payload):
        self.created.append(payload)


class TestNames(unittest.TestCase):
    def test_only_top_and_sbs(self):
        rng = random.Random(1)
        for _ in range(300):
            name = generate_candidate(rng)
            self.assertTrue(name.endswith(".top") or name.endswith(".sbs"))
            label = name.split(".")[0]
            self.assertRegex(label, r"^[a-z]+\d+[a-z]*$|^[a-z]+[a-z]+\d+$")


class TestPrice(unittest.TestCase):
    def test_parse_dynadot_price(self):
        self.assertEqual(parse_usd_price("2.50 in USD"), 2.5)
        self.assertEqual(parse_usd_price("44.00 in USD and domain is not premium"), 44.0)
        self.assertIsNone(parse_usd_price("10.00 in EUR"))

    def test_limit(self):
        self.assertTrue(is_affordable(3, 3))
        self.assertFalse(is_affordable(3.01, 3))
        self.assertFalse(is_affordable(None, 3))

    def test_filter_keeps_only_cheap_available(self):
        catalog = {
            "backend1.top": DomainQuote("backend1.top", True, 2.2),
            "dev2.sbs": DomainQuote("dev2.sbs", True, 9.0),
            "api3.top": DomainQuote("api3.top", False, 1.0),
            "srv4.sbs": DomainQuote("srv4.sbs", True, 3.0),
        }
        rng = random.Random(2)

        def search(name):
            return catalog.get(name, DomainQuote(name, False, None))

        with patch("src.domains.setup.generate_candidate", side_effect=list(catalog)):
            found, attempts = collect_affordable(2, search, max_price=3, rng=rng, max_attempts=10)
        self.assertEqual([item.name for item in found], ["backend1.top", "srv4.sbs"])
        self.assertGreaterEqual(attempts, 2)

    def test_expiration_plus_one_year(self):
        self.assertEqual(expiration_in_one_year(date(2026, 11, 3)), "2027-11-03")
        self.assertEqual(expiration_in_one_year(date(2024, 2, 29)), "2025-02-28")


class TestPurchaseGuard(unittest.TestCase):
    def setUp(self):
        reset_reservations()

    def test_register_requires_confirmation(self):
        dyn = FakeDyn()
        state = [{"name": "backend1.top", "price": 2, "done": []}]
        with self.assertRaises(Exception) as caught:
            run_confirmed_batch(state, dynadot=dyn, crm=FakeCRM([_account(1)]), ip="5.75.214.90", confirmed=False)
        self.assertIn("подтверж", str(caught.exception).lower())
        self.assertEqual(dyn.registered, [])

    def test_partial_register_failure_continues(self):
        dyn = FakeDyn(fail={"bad.top"})
        crm = FakeCRM([_account(1), _account(2, "cf-2")])
        cfs = {}

        def factory(account):
            client = FakeCF()
            cfs[account["id"]] = client
            return client

        states = [
            {"name": "ok.sbs", "price": 2, "done": []},
            {"name": "bad.top", "price": 2, "done": []},
            {"name": "also.top", "price": 1.5, "done": []},
        ]
        messages = run_confirmed_batch(
            states,
            dynadot=dyn,
            crm=crm,
            ip="5.75.214.90",
            confirmed=True,
            today=date(2026, 11, 3),
            cf_factory=factory,
        )
        self.assertIn("ok.sbs", dyn.registered)
        self.assertIn("also.top", dyn.registered)
        self.assertNotIn("bad.top", dyn.registered)
        self.assertTrue(any("bad.top" in line and "покупка" in line for line in messages))
        self.assertEqual(len(crm.created), 2)
        used_ids = [payload["cloudflare_accounts"][0]["cloudflare_account_id"] for payload in crm.created]
        self.assertEqual(len(set(used_ids)), 2)
        self.assertEqual(len(crm.busy), 2)

    def test_specific_over_limit_is_not_silent(self):
        quotes = [
            DomainQuote("cheap.top", True, 2.0),
            DomainQuote("dear.sbs", True, 8.0),
        ]
        self.assertEqual([item.name for item in quotes_over_limit(quotes, 3)], ["dear.sbs"])
        only_cheap = select_for_purchase(quotes, max_price=3, allow_over_limit=False)
        self.assertEqual([item.name for item in only_cheap], ["cheap.top"])
        both = select_for_purchase(quotes, max_price=3, allow_over_limit=True)
        self.assertEqual(len(both), 2)

    def test_register_available_reports_reason(self):
        dyn = FakeDyn(fail={"x.top"})
        quotes = [DomainQuote("a.sbs", True, 1), DomainQuote("x.top", True, 1)]
        bought, failed = register_available(quotes, dyn.register)
        self.assertEqual([item.name for item in bought], ["a.sbs"])
        self.assertEqual(failed[0][0], "x.top")
        self.assertIn("баланса", failed[0][1])


class TestCrmPayloads(unittest.TestCase):
    def test_exact_payloads_and_zone_ns(self):
        account = _account(15)
        ns0, ns1 = "ada.ns.cloudflare.com", "bob.ns.cloudflare.com"
        update = cloudflare_account_update_payload(account, ns0, ns1)
        self.assertEqual(
            update,
            {
                "account_id": "cf-acc",
                "auth_key": "cfk_15",
                "email": "u15@cf.test",
                "first_ns": ns0,
                "second_ns": ns1,
                "password": "secret-15",
                "status": 1,
            },
        )
        created = domain_create_payload("backend42.top", account, expiration_in_one_year(date(2026, 11, 3)))
        self.assertEqual(
            created,
            {
                "batch": "backend42.top",
                "status": 1,
                "type_source": 3,
                "comment": None,
                "registrator": "dynadot",
                "expiration_date": "2027-11-03",
                "cloudflare_accounts": [
                    {
                        "domain": "backend42.top",
                        "primary_ns": None,
                        "secondary_ns": None,
                        "auth_key": None,
                        "cloudflare_account_id": "15",
                        "email": "u15@cf.test",
                        "password": "secret-15",
                    }
                ],
            },
        )


class TestDynadotShapes(unittest.TestCase):
    def test_search_json(self):
        data = {
            "SearchResponse": {
                "ResponseCode": "0",
                "SearchResults": [
                    {"DomainName": "backend42.top", "Available": "yes", "Price": "2.88 in USD"},
                    {"DomainName": "taken.sbs", "Available": "no", "Price": ""},
                ],
            }
        }
        quotes = parse_search_response(data)
        self.assertTrue(quotes[0].available)
        self.assertEqual(quotes[0].price_usd, 2.88)
        self.assertFalse(quotes[1].available)

    def test_set_ns_success_code(self):
        ensure_command_ok({"SetNsResponse": {"ResponseCode": 0, "Status": "success"}}, "SetNsResponse", "set_ns")
        with self.assertRaises(DynadotError):
            ensure_command_ok(
                {"SetNsResponse": {"ResponseCode": -1, "Status": "error", "Error": "bad ns"}},
                "SetNsResponse",
                "set_ns",
            )


class TestCloudflareAuth(unittest.TestCase):
    def test_cfk_is_global_key(self):
        headers = cloudflare_auth_headers("a@b.c", "cfk_example")
        self.assertEqual(headers["X-Auth-Email"], "a@b.c")
        self.assertEqual(headers["X-Auth-Key"], "cfk_example")
        self.assertNotIn("Authorization", headers)

    def test_user_token_is_bearer(self):
        headers = cloudflare_auth_headers("a@b.c", "cfut_example")
        self.assertEqual(headers["Authorization"], "Bearer cfut_example")


class TestDomainList(unittest.TestCase):
    def test_pasted_table_and_urls(self):
        text = """
        https://www.Backend42.top/path
        dev7.sbs, https://API9.sbs/
        backend42.top
        not-a-domain
        www.dev7.sbs/foo
        """
        self.assertEqual(parse_domain_list(text), ["backend42.top", "dev7.sbs", "api9.sbs"])


class TestSetupOnly(unittest.TestCase):
    def setUp(self):
        reset_reservations()

    def test_skips_purchase_and_uses_real_expiration(self):
        class OwnedDyn(FakeDyn):
            def domain_record(self, name):
                if name == "missing.top":
                    return False, None
                if name == "old.sbs":
                    return True, "2028-04-01"
                return True, None

        dyn = OwnedDyn()
        crm = FakeCRM([_account(1), _account(2, "cf-2")])

        def factory(account):
            return FakeCF()

        states = [
            {"name": "old.sbs", "skip_buy": True, "done": [], "expiration": None},
            {"name": "missing.top", "skip_buy": True, "done": []},
            {"name": "fresh.top", "skip_buy": True, "done": []},
        ]
        messages = run_confirmed_batch(
            states,
            dynadot=dyn,
            crm=crm,
            ip="5.75.214.90",
            confirmed=True,
            today=date(2026, 11, 3),
            cf_factory=factory,
        )
        self.assertEqual(dyn.registered, [])
        self.assertTrue(any("missing.top" in line and "Dynadot" in line for line in messages))
        by_name = {item["batch"]: item for item in crm.created}
        self.assertEqual(set(by_name), {"old.sbs", "fresh.top"})
        self.assertEqual(by_name["old.sbs"]["expiration_date"], "2028-04-01")
        self.assertEqual(by_name["fresh.top"]["expiration_date"], "2027-11-03")
        self.assertEqual(by_name["old.sbs"]["registrator"], "dynadot")
        self.assertEqual(by_name["old.sbs"]["status"], 1)
        self.assertEqual(by_name["old.sbs"]["type_source"], 3)

    def test_domain_info_expiration(self):
        owned, expires = parse_domain_info(
            {
                "DomainInfoResponse": {
                    "ResponseCode": 0,
                    "Status": "success",
                    "DomainInfo": {"Name": "old.sbs", "Expiration": "1361430589062"},
                }
            }
        )
        self.assertTrue(owned)
        self.assertEqual(expires, "2013-02-21")
        self.assertEqual(expiration_to_date("1361430589062"), "2013-02-21")
        missing, _ = parse_domain_info(
            {"DomainInfoResponse": {"ResponseCode": -1, "Status": "error", "Error": "not found"}}
        )
        self.assertFalse(missing)


class TestMasking(unittest.TestCase):
    def test_payload_and_url(self):
        red = redact_payload({"password": "secret-15", "auth_key": "cfk_15", "email": "u@cf.test"})
        self.assertEqual(red["password"], "***")
        self.assertEqual(red["auth_key"], "***")
        self.assertEqual(red["email"], "u@cf.test")
        self.assertNotIn("secret-15", format_body({"password": "secret-15", "auth_key": "cfk_15"}))
        masked = mask_url("https://api.dynadot.com/api3.json?key=SECRET&command=search")
        self.assertNotIn("SECRET", masked)
        self.assertIn("key=***", masked)


if __name__ == "__main__":
    unittest.main()

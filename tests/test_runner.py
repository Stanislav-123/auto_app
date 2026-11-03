import unittest

from src.binom.client import BinomError, ClonedCampaign
from src.crm.client import CrmError
from src.models import Platform
from src.workflow.runner import execute_next, next_action, precheck
from src.workflow.steps import WorkflowStep


def _ios_wf(**extra):
    wf = {
        "parsed": {
            "platform": "ios",
            "name": "Searock",
            "store_id": "id6786185431",
            "bundle": "cos.seasetify.offrock",
            "backend_domain": "https://Yondra36.sbs/path",
            "backend": "Новый",
            "owner": "ZMApps",
            "missing_fields": [],
        },
        "completed_steps": [WorkflowStep.PARSE.value],
    }
    wf.update(extra)
    return wf


class _Binom:
    def __init__(self, found=None, fail_clone=False):
        self.found = found
        self.fail_clone = fail_clone
        self.cloned = 0
        self.searches = 0

    def find_campaign_by_name(self, name):
        self.searches += 1
        return self.found

    def clone_campaign(self, name, template_id=None):
        self.cloned += 1
        if self.fail_clone:
            raise BinomError("clone failed")
        return ClonedCampaign(id=7, name=name, url="https://tracker.example/c?key=abc", key="abc")


class _Crm:
    def __init__(self, found=None, fail_grant=False):
        self.found = found
        self.fail_grant = fail_grant
        self.created = 0
        self.granted = []
        self.domains = []

    def find_existing_app(self, **kwargs):
        return self.found

    def create_app(self, payload):
        self.created += 1

        class Created:
            id = 55
            name = payload["apps_name"]

        return Created()

    def grant_app_access(self, app_id, team_ids, user_ids=None):
        if self.fail_grant:
            raise CrmError("no access")
        self.granted.append(app_id)

    def set_app_domain(self, app_id, domain):
        self.domains.append((app_id, domain))
        return {"domain": domain, "catalog": False}


class _Panel:
    def __init__(self):
        self.configured = None

    def find_app(self, **kwargs):
        class App:
            id = 3
            app_id = "id1"
            domain = "yondra36.sbs"
            name = "Searock"

        return App()

    def configure_app(self, app_id, binom_key):
        self.configured = (app_id, binom_key)


class TestPrecheck(unittest.TestCase):
    def test_skips_existing_campaign_and_app(self):
        found = ClonedCampaign(
            id=4,
            name="[ZM Apps] Searock - id6786185431 - 888 iOS [IO]",
            url="https://tracker.example/c?key=old",
            key="old",
        )
        binom = _Binom(found=found)
        crm = _Crm(found={"id": 9, "apps_name": "Searock", "apps_bundle": "id6786185431"})
        wf = _ios_wf()
        notes = precheck(wf, binom, crm)
        self.assertEqual(len(notes), 2)
        self.assertIn("клонирование пропущено", notes[0])
        self.assertIn("создание пропущено", notes[1])
        self.assertIn(WorkflowStep.BINOM.value, wf["completed_steps"])
        self.assertIn(WorkflowStep.CRM.value, wf["completed_steps"])
        self.assertEqual(wf["binom"]["id"], 4)
        self.assertEqual(wf["crm"]["id"], 9)
        self.assertEqual(binom.cloned, 0)
        self.assertEqual(wf["domain"], "yondra36.sbs")

    def test_does_not_skip_when_missing(self):
        binom = _Binom()
        crm = _Crm()
        wf = _ios_wf()
        notes = precheck(wf, binom, crm)
        self.assertEqual(notes, [])
        self.assertNotIn(WorkflowStep.BINOM.value, wf["completed_steps"])
        self.assertEqual(binom.searches, 1)


class TestAutoRunner(unittest.TestCase):
    def test_runs_binom_then_waits_for_files(self):
        wf = _ios_wf()
        binom = _Binom()
        result = execute_next(wf, binom=binom, crm=_Crm(), template_id=2)
        self.assertTrue(result.ok)
        self.assertEqual(result.action, "binom")
        self.assertEqual(binom.cloned, 1)
        self.assertEqual(next_action(wf), "need_crm_files")

    def test_skips_completed_binom(self):
        wf = _ios_wf(completed_steps=["parse", "binom"], binom={"id": 1, "key": "k", "url": "https://t/?key=k"})
        binom = _Binom()
        self.assertEqual(next_action(wf), "need_crm_files")
        self.assertEqual(binom.cloned, 0)

    def test_domain_uses_parsed_backend_domain(self):
        wf = _ios_wf(
            completed_steps=["parse", "binom", "crm"],
            crm={"id": 15, "name": "Searock"},
            binom={"id": 1, "key": "abc", "url": "https://tracker.example/c?key=abc"},
        )
        wf.pop("domain", None)
        crm = _Crm()
        result = execute_next(wf, crm=crm)
        self.assertTrue(result.ok)
        self.assertEqual(result.action, "domain")
        self.assertEqual(crm.domains, [(15, "yondra36.sbs")])
        self.assertIn("domain", wf["completed_steps"])

    def test_binom_error_stops_and_can_retry(self):
        wf = _ios_wf()
        result = execute_next(wf, binom=_Binom(fail_clone=True), template_id=2)
        self.assertFalse(result.ok)
        self.assertEqual(wf["failed_step"], "binom")
        self.assertNotIn("binom", wf["completed_steps"])
        self.assertEqual(next_action(wf), "binom")

    def test_crm_id_saved_before_grant_and_retry_does_not_create(self):
        saved = []
        wf = _ios_wf(
            completed_steps=["parse", "binom"],
            crm_ready=True,
            crm_theme_id=1,
            crm_files={"logo": "a", "banner": "b", "banner_appcraft": "c", "slider": "d"},
        )
        crm = _Crm(fail_grant=True)
        result = execute_next(wf, crm=crm, team_ids=[1], persist=lambda: saved.append(dict(wf["crm"])))
        self.assertFalse(result.ok)
        self.assertEqual(crm.created, 1)
        self.assertEqual(wf["crm"]["id"], 55)
        self.assertEqual(saved, [{"id": 55, "name": "Searock"}])
        self.assertNotIn("crm", wf["completed_steps"])

        crm.fail_grant = False
        result = execute_next(wf, crm=crm, team_ids=[1])
        self.assertTrue(result.ok)
        self.assertEqual(crm.created, 1)
        self.assertEqual(crm.granted, [55])

    def test_full_ios_tail_after_precheck(self):
        wf = _ios_wf()
        binom = _Binom(
            found=ClonedCampaign(id=4, name="n", url="https://tracker.example/c?key=abc", key="abc")
        )
        crm = _Crm(found={"id": 9, "apps_name": "Searock", "apps_bundle": "id6786185431"})
        precheck(wf, binom, crm)
        panel = _Panel()
        domain = execute_next(wf, crm=crm)
        backend = execute_next(wf, new_backend=panel)
        self.assertTrue(domain.ok)
        self.assertTrue(backend.ok)
        self.assertEqual(panel.configured, (3, "abc"))
        self.assertEqual(next_action(wf), "done")
        self.assertEqual(binom.cloned, 0)
        self.assertEqual(crm.created, 0)


class TestPlatformGuard(unittest.TestCase):
    def test_android_waits_for_facebook(self):
        wf = {
            "parsed": {
                "platform": Platform.ANDROID.value,
                "name": "App",
                "store_id": "com.app",
                "bundle": "com.app",
                "missing_fields": [],
            },
            "completed_steps": ["parse", "binom", "crm", "domain"],
            "binom": {"id": 1, "url": "https://tracker.example/c?key=abc", "key": "abc"},
            "crm": {"id": 2},
        }
        self.assertEqual(next_action(wf), "need_android_fb")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from src.models import ParsedApp, Platform
from src.workflow.persist import (
    WorkflowStore,
    app_key_from_parsed,
    is_workflow_complete,
    make_app_key,
    resume_step,
    sanitize_wf,
)
from src.workflow.steps import WorkflowStep


class TestAppKey(unittest.TestCase):
    def test_ios_store_id(self):
        self.assertEqual(make_app_key(platform="ios", store_id="id678"), "ios:id678")

    def test_android_bundle(self):
        app = ParsedApp(
            platform=Platform.ANDROID,
            store_id="com.example.app",
            bundle="com.example.app",
            name="Example",
        )
        self.assertEqual(app_key_from_parsed(app), "android:com.example.app")


class TestResumeStep(unittest.TestCase):
    def test_after_binom(self):
        wf = {"completed_steps": ["parse", "binom"]}
        self.assertEqual(resume_step(wf), WorkflowStep.CRM)

    def test_after_domain(self):
        wf = {"completed_steps": ["parse", "binom", "crm", "domain"]}
        self.assertEqual(resume_step(wf), WorkflowStep.BACKEND)

    def test_complete(self):
        wf = {"completed_steps": ["parse", "binom", "crm", "domain", "backend"]}
        self.assertTrue(is_workflow_complete(wf))
        self.assertIsNone(resume_step(wf))

    def test_awaiting_android_fb(self):
        wf = {
            "completed_steps": ["parse", "binom", "crm", "domain"],
            "awaiting_android_fb": True,
        }
        self.assertEqual(resume_step(wf), WorkflowStep.BACKEND)


class TestWorkflowStore(unittest.TestCase):
    def test_save_load_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            wf = {
                "parsed": {
                    "platform": "android",
                    "store_id": "com.demo.app",
                    "bundle": "com.demo.app",
                    "name": "Demo",
                },
                "completed_steps": ["parse", "binom"],
                "binom": {"id": 1, "key": "abc", "url": "https://t.test/?key=abc"},
                "crm_collecting": True,
                "crm_temp_dir": "/tmp/gone",
            }
            path = store.save(42, wf)
            self.assertIsNotNone(path)
            loaded = store.load(42, "android:com.demo.app")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["binom"]["key"], "abc")
            self.assertTrue(loaded.get("crm_needs_recollect"))
            self.assertFalse(loaded.get("crm_collecting"))
            self.assertNotIn("crm_temp_dir", loaded)
            store.delete(42, "android:com.demo.app")
            self.assertIsNone(store.load(42, "android:com.demo.app"))

    def test_sanitize_keeps_android_fb(self):
        wf = {
            "parsed": {"platform": "android", "store_id": "com.x"},
            "completed_steps": ["parse", "binom", "crm", "domain"],
            "awaiting_android_fb": True,
            "android_fb": {"app_id": "111"},
            "android_fb_next": "access_token",
        }
        data = sanitize_wf(wf)
        self.assertTrue(data["awaiting_android_fb"])
        self.assertEqual(data["android_fb"]["app_id"], "111")


if __name__ == "__main__":
    unittest.main()

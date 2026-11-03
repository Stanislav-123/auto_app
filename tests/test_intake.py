import unittest

from src.workflow.intake import extract_app_command_payload, workflow_reply_kind


APP_BODY = """\
Новое IOS приложение

Searock
https://apps.apple.com/ru/app/searock/id6786185431
Bundle: cos.seasetify.offrock
"""


class TestAppCommandPayload(unittest.TestCase):
    def test_command_only(self):
        self.assertEqual(extract_app_command_payload("/app"), "")
        self.assertEqual(extract_app_command_payload("/app@zavod_bot"), "")

    def test_multiline_after_command(self):
        text = "/app\n" + APP_BODY
        self.assertEqual(extract_app_command_payload(text), APP_BODY.strip())

    def test_same_line_and_rest(self):
        text = "/app@bot Новое IOS приложение\nSearock\nBundle: com.example.app"
        payload = extract_app_command_payload(text)
        self.assertTrue(payload.startswith("Новое IOS приложение"))
        self.assertIn("Bundle: com.example.app", payload)
        self.assertNotIn("/app", payload.splitlines()[0])


class TestWorkflowReplyKind(unittest.TestCase):
    def test_idle_message_does_not_start(self):
        self.assertIsNone(workflow_reply_kind(None))
        self.assertIsNone(workflow_reply_kind({}))
        self.assertIsNone(
            workflow_reply_kind({"parsed": {"name": "X"}, "completed_steps": ["parse"]})
        )

    def test_waiting_for_app_text(self):
        self.assertEqual(workflow_reply_kind({"awaiting_app": True}), "app")

    def test_mid_workflow_inputs(self):
        self.assertEqual(workflow_reply_kind({"crm_collecting": True}), "crm")
        self.assertEqual(workflow_reply_kind({"crm_awaiting_theme": True}), "crm")
        self.assertEqual(workflow_reply_kind({"awaiting_domain": True}), "domain")
        self.assertEqual(workflow_reply_kind({"awaiting_android_fb": True}), "android_fb")
        self.assertEqual(
            workflow_reply_kind({"awaiting_missing": True, "parsed": {"name": "X"}}),
            "missing",
        )

    def test_crm_input_wins_over_idle_parse(self):
        wf = {
            "parsed": {"name": "X"},
            "completed_steps": ["parse", "binom"],
            "crm_collecting": True,
            "crm_stage": "zip",
        }
        self.assertEqual(workflow_reply_kind(wf), "crm")


if __name__ == "__main__":
    unittest.main()

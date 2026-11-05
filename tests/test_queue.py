import tempfile
import unittest
from pathlib import Path

from src.workflow.persist import WorkflowStore
from src.workflow.queue import (
    QUEUE_STORE_KEY,
    USER_DATA_QUEUE_KEY,
    busy_factory_prompt,
    clear_queue,
    dequeue,
    enqueue_workflow,
    format_enqueue_result,
    format_start_next,
    is_factory_active,
    preview_label,
    queue_size,
    start_next,
)


def _snapshot(name: str, store_id: str, platform: str = "ios") -> dict:
    return {
        "parsed": {
            "platform": platform,
            "name": name,
            "store_id": store_id,
            "bundle": f"com.example.{name.lower()}",
        },
        "completed_steps": ["parse"],
        "app_key": f"{platform}:{store_id}",
    }


class TestFactoryActive(unittest.TestCase):
    def test_idle_and_complete_are_inactive(self):
        self.assertFalse(is_factory_active(None))
        self.assertFalse(is_factory_active({}))
        self.assertFalse(is_factory_active({"awaiting_app": True}))
        self.assertFalse(
            is_factory_active(
                {
                    "parsed": {"name": "Done", "platform": "ios"},
                    "completed_steps": ["parse", "binom", "crm", "domain", "backend"],
                }
            )
        )

    def test_in_progress_is_active(self):
        self.assertTrue(
            is_factory_active({"parsed": {"name": "X", "platform": "ios"}, "completed_steps": ["parse"]})
        )
        self.assertTrue(is_factory_active({"crm_collecting": True}))
        self.assertTrue(is_factory_active({"awaiting_domain": True}))
        self.assertTrue(is_factory_active({"awaiting_android_fb": True}))
        self.assertTrue(is_factory_active({"failed_step": "binom", "parsed": {"name": "X"}}))


class TestEnqueueDequeue(unittest.TestCase):
    def test_enqueue_returns_position_and_keeps_current_intact(self):
        user_data: dict = {}
        current = _snapshot("Current", "id111")
        current["crm_collecting"] = True
        first = enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=current)
        second = enqueue_workflow(user_data, _snapshot("Beta", "id333"), current_wf=current)

        self.assertEqual(first.status, "queued")
        self.assertEqual(first.position, 1)
        self.assertEqual(second.status, "queued")
        self.assertEqual(second.position, 2)
        self.assertEqual(queue_size(user_data), 2)
        self.assertEqual(current["parsed"]["name"], "Current")
        self.assertTrue(current["crm_collecting"])
        self.assertNotIn("crm_collecting", first.item["workflow"])

    def test_queued_snapshot_is_isolated_from_source(self):
        user_data: dict = {}
        source = _snapshot("Fresh", "id444")
        result = enqueue_workflow(user_data, source, current_wf=_snapshot("Now", "id111"))
        source["parsed"]["name"] = "MUTATED"
        source["completed_steps"].append("binom")
        self.assertEqual(result.item["workflow"]["parsed"]["name"], "Fresh")
        self.assertEqual(result.item["workflow"]["completed_steps"], ["parse"])

    def test_dequeue_is_fifo(self):
        user_data: dict = {}
        enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=_snapshot("Now", "id111"))
        enqueue_workflow(user_data, _snapshot("Beta", "id333"), current_wf=_snapshot("Now", "id111"))
        first = dequeue(user_data)
        second = dequeue(user_data)
        self.assertEqual(first["label"], "Alpha (ios)")
        self.assertEqual(second["label"], "Beta (ios)")
        self.assertIsNone(dequeue(user_data))
        self.assertEqual(queue_size(user_data), 0)

    def test_duplicate_current_and_queue(self):
        user_data: dict = {}
        current = _snapshot("Current", "id111")
        same_current = enqueue_workflow(user_data, _snapshot("Current", "id111"), current_wf=current)
        self.assertEqual(same_current.status, "duplicate_current")
        self.assertEqual(queue_size(user_data), 0)

        enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=current)
        again = enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=current)
        self.assertEqual(again.status, "duplicate_queue")
        self.assertEqual(again.position, 1)
        self.assertEqual(queue_size(user_data), 1)

    def test_clear_queue_leaves_unrelated_user_data(self):
        user_data = {"workflow": _snapshot("Now", "id111"), USER_DATA_QUEUE_KEY: []}
        enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=user_data["workflow"])
        enqueue_workflow(user_data, _snapshot("Beta", "id333"), current_wf=user_data["workflow"])
        removed = clear_queue(user_data)
        self.assertEqual(removed, 2)
        self.assertEqual(queue_size(user_data), 0)
        self.assertEqual(user_data["workflow"]["parsed"]["name"], "Now")
        self.assertEqual(clear_queue(user_data), 0)

    def test_separate_users_stay_independent(self):
        first: dict = {}
        second: dict = {}
        enqueue_workflow(first, _snapshot("A1", "id1"), current_wf=_snapshot("C1", "id9"))
        enqueue_workflow(second, _snapshot("B1", "id2"), current_wf=_snapshot("C2", "id8"))
        enqueue_workflow(second, _snapshot("B2", "id3"), current_wf=_snapshot("C2", "id8"))
        self.assertEqual(queue_size(first), 1)
        self.assertEqual(queue_size(second), 2)
        self.assertEqual(start_next(first)["parsed"]["name"], "A1")
        self.assertEqual(start_next(second)["parsed"]["name"], "B1")
        self.assertEqual(queue_size(first), 0)
        self.assertEqual(queue_size(second), 1)


class TestStartNext(unittest.TestCase):
    def test_start_next_returns_isolated_workflow_in_order(self):
        user_data: dict = {}
        current = _snapshot("Now", "id111")
        enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=current)
        enqueue_workflow(user_data, _snapshot("Beta", "id333"), current_wf=current)

        first = start_next(user_data)
        self.assertEqual(first["parsed"]["name"], "Alpha")
        first["parsed"]["name"] = "CHANGED"
        first["completed_steps"].append("crm")

        second = start_next(user_data)
        self.assertEqual(second["parsed"]["name"], "Beta")
        self.assertEqual(second["completed_steps"], ["parse"])
        self.assertEqual(queue_size(user_data), 0)
        self.assertIsNone(start_next(user_data))

    def test_preview_and_messages(self):
        label = preview_label(_snapshot("RiceTrain", "id6824816871"))
        self.assertEqual(label, "RiceTrain (ios)")
        queued = enqueue_workflow(
            {},
            _snapshot("RiceTrain", "id6824816871"),
            current_wf=_snapshot("Other", "id1"),
        )
        text = format_enqueue_result(queued)
        self.assertIn("в очереди, позиция 1", text)
        self.assertIn("RiceTrain", text)
        self.assertIn("Other", text)
        self.assertIn("/cancel", text)
        self.assertIn("/clear_queue", text)
        self.assertIn("в очереди, позиция 1", text)
        self.assertIn("В очереди уже: 1", busy_factory_prompt("Other (ios)", 1))
        self.assertIn("Стартую следующую", format_start_next("RiceTrain (ios)", 0))
        self.assertIn("Очередь больше пуста", format_start_next("RiceTrain (ios)", 0))
        self.assertIn("Ещё в очереди: 2", format_start_next("RiceTrain (ios)", 2))

    def test_persist_roundtrip(self):
        user_data: dict = {}
        current = _snapshot("Now", "id111")
        enqueue_workflow(user_data, _snapshot("Alpha", "id222"), current_wf=current)
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.save(7, {"app_key": QUEUE_STORE_KEY, "app_queue": user_data[USER_DATA_QUEUE_KEY]})
            loaded = store.load(7, QUEUE_STORE_KEY)
            self.assertIsNotNone(loaded)
            restored = {USER_DATA_QUEUE_KEY: loaded["app_queue"]}
            nxt = start_next(restored)
            self.assertEqual(nxt["parsed"]["name"], "Alpha")
            self.assertEqual(nxt["app_key"], "ios:id222")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from workbench.adapters import AdapterStatus, ConnectionManager
from workbench.store import Store


class FakeAdapter:
    channel_id = "c1"

    def __init__(self, status):
        self._status = status

    def probe(self):
        return self._status

    def read_messages(self, limit=20):
        return []


class BrokenAdapter:
    channel_id = "c2"

    def probe(self):
        raise RuntimeError("client missing")

    def read_messages(self, limit=20):
        raise RuntimeError("client missing")


class ConnectionManagerTests(unittest.TestCase):
    def test_probe_returns_adapter_state(self):
        manager = ConnectionManager.from_list(
            [FakeAdapter(AdapterStatus(state="read_ok", detail="测试", read_ok=True, send_ok=False))]
        )
        status = manager.probe()["c1"]
        self.assertEqual("read_ok", status.state)
        self.assertTrue(status.read_ok)
        self.assertFalse(status.send_ok)

    def test_broken_adapter_becomes_error_not_crash(self):
        manager = ConnectionManager.from_list([BrokenAdapter()])
        status = manager.probe()["c2"]
        self.assertEqual("error", status.state)
        self.assertIn("client missing", status.detail)

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            AdapterStatus(state="connected")

    def test_reading_without_adapter_raises(self):
        with self.assertRaises(KeyError):
            ConnectionManager().read("missing")


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "ingest.sqlite")
        self.store.sync_channels(
            [{"id": "c1", "name": "渠道一", "accent": "#112233", "skin": "plain", "sort_order": 1}]
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_adapter_messages_are_idempotent(self):
        messages = [
            {
                "external_id": "u-1",
                "customer_name": "客户甲",
                "message_id": "m-1",
                "body": "你好，电脑开不了机。",
                "occurred_at": "2026-11-08T00:00:00+00:00",
                "locale": "zh-CN",
            },
            {
                "external_id": "u-1",
                "customer_name": "客户甲",
                "message_id": "m-2",
                "body": "型号是 X1 Carbon。",
                "occurred_at": "2026-11-08T00:01:00+00:00",
                "locale": "zh-CN",
            },
        ]
        self.assertEqual(2, self.store.ingest_messages("c1", messages))
        self.assertEqual(0, self.store.ingest_messages("c1", messages))

        conversations = self.store.list_conversations("c1")
        self.assertEqual(1, len(conversations))
        self.assertEqual(2, conversations[0]["unread"])
        self.assertEqual("型号是 X1 Carbon。", conversations[0]["preview"])

        detail = self.store.conversation_detail(conversations[0]["id"])
        self.assertEqual("adapter", detail["messages"][0]["source"])
        channel = self.store.list_channels()[0]
        self.assertIsNotNone(channel["last_read_at"])


if __name__ == "__main__":
    unittest.main()

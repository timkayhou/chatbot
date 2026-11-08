import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path

from workbench.adapters import HttpPollingAdapter
from workbench.mock_platform import create_mock_server
from workbench.store import Store


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.server = create_mock_server(0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.adapter = HttpPollingAdapter("mock-channel", self.base, "test-token")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_probe_reports_read_only_state(self):
        status = self.adapter.probe()
        self.assertEqual("read_ok", status.state)
        self.assertTrue(status.read_ok)
        self.assertFalse(status.send_ok)
        self.assertEqual("HttpPollingAdapter", status.adapter)

    def test_read_messages_advances_cursor(self):
        first = self.adapter.read_messages(2)
        second = self.adapter.read_messages(2)
        self.assertEqual(2, len(first))
        self.assertEqual(2, len(second))
        self.assertNotEqual(first[0].message_id, second[0].message_id)
        self.assertEqual(4, self.adapter.cursor)

    def test_bad_token_becomes_error_state(self):
        status = HttpPollingAdapter("mock-channel", self.base, "wrong").probe()
        self.assertEqual("error", status.state)
        self.assertIn("401", status.detail)

    def test_adapter_has_no_send_method(self):
        self.assertFalse(hasattr(self.adapter, "send"))

    def test_ingest_is_idempotent_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "adapter.sqlite")
            store.sync_channels(
                [{"id": "mock-channel", "name": "测试渠道", "accent": "#556677", "skin": "plain", "sort_order": 1}]
            )
            messages = [asdict(message) for message in self.adapter.read_messages(10)]
            self.assertEqual(4, len(messages))
            self.assertEqual(4, store.ingest_messages("mock-channel", messages))
            self.assertEqual(0, store.ingest_messages("mock-channel", messages))
            conversations = store.list_conversations("mock-channel")
            self.assertEqual(3, len(conversations))
            detail = store.conversation_detail(conversations[0]["id"])
            self.assertTrue(all(message["source"] == "adapter" for message in detail["messages"]))


if __name__ == "__main__":
    unittest.main()

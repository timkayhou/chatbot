import tempfile
import unittest
from pathlib import Path

from workbench.store import Store


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "chat.sqlite")
        self.store.sync_channels(
            [
                {"id": "c1", "name": "渠道一", "accent": "#112233", "skin": "plain", "sort_order": 1},
                {"id": "c2", "name": "渠道二", "accent": "#445566", "skin": "plain", "sort_order": 2},
            ]
        )
        self.customer = self.store.upsert_customer(
            "c1", "u-1", "陈雨", avatar="陈", note="模拟客户", tags=("老客户",), locale="zh-CN"
        )
        self.conversation = self.store.create_chat_conversation("c1", self.customer, "陈雨", "zh-CN")

    def tearDown(self):
        self.temp.cleanup()

    def test_channel_counts_and_unread(self):
        self.store.add_message(self.conversation, "in", "你好", "mock")
        self.store.add_message(self.conversation, "out", "您好，请讲。", "simulated")
        self.store.add_message(self.conversation, "in", "笔记本很慢", "mock")
        channels = {channel["id"]: channel for channel in self.store.list_channels()}
        self.assertEqual(1, channels["c1"]["conversations"])
        self.assertEqual(2, channels["c1"]["unread"])
        self.assertEqual(0, channels["c2"]["conversations"])

        conversations = self.store.list_conversations("c1")
        self.assertEqual(1, len(conversations))
        self.assertEqual("笔记本很慢", conversations[0]["preview"])
        self.assertEqual(["老客户"], conversations[0]["tags"])

        self.store.mark_read(self.conversation)
        self.assertEqual(0, self.store.list_conversations("c1")[0]["unread"])

    def test_detail_keeps_source_and_locale(self):
        self.store.add_message(self.conversation, "out", "模拟回复", "simulated")
        detail = self.store.conversation_detail(self.conversation)
        self.assertEqual("陈雨", detail["conversation"]["display_name"])
        self.assertEqual("zh-CN", detail["conversation"]["locale"])
        self.assertEqual("simulated", detail["messages"][0]["source"])
        self.assertIsNone(self.store.conversation_detail(999))

    def test_takeover_flag_round_trip(self):
        self.store.set_takeover(self.conversation)
        detail = self.store.conversation_detail(self.conversation)
        self.assertEqual(1, detail["conversation"]["takeover"])

    def test_takeover_can_be_disabled(self):
        self.store.set_takeover(self.conversation, True)
        self.store.set_takeover(self.conversation, False)
        detail = self.store.conversation_detail(self.conversation)
        self.assertEqual(0, detail["conversation"]["takeover"])


if __name__ == "__main__":
    unittest.main()

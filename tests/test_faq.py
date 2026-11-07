import tempfile
import unittest
from pathlib import Path

from workbench.faq import match, similarity
from workbench.store import Store


class FaqSimilarityTests(unittest.TestCase):
    def test_variant_and_exact_matches(self):
        self.assertEqual(1.0, similarity("到店要带什么", "第一次到店需要带什么？", ["到店要带什么"]))
        self.assertGreater(similarity("笔记本连不上无线网", "无线网络连接不上怎么办？", ["笔记本连不上无线网"]), 0.5)

    def test_unrelated_question_scores_low(self):
        self.assertLess(similarity("怎么退货", "第一次到店需要带什么？", ["到店要带什么"]), 0.3)

    def test_unreviewed_faq_is_not_matched(self):
        faq = {"id": 1, "question": "到店要带什么？", "variants": [], "reviewed_at": None}
        self.assertIsNone(match("到店要带什么", [faq]))


class FaqStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "faq.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_requires_review_again(self):
        faq_id = self.store.create_faq(question="到店要带什么？", answer="带设备和电源。", reviewed=True)
        self.assertIsNotNone(self.store.get_faq(faq_id)["reviewed_at"])
        self.assertTrue(self.store.update_faq(faq_id, answer="带设备、电源和保修卡。"))
        self.assertIsNone(self.store.get_faq(faq_id)["reviewed_at"])

    def test_candidates_come_from_repeated_history(self):
        customer = self.store.upsert_customer("c1", "u1", "客户甲", locale="zh-CN")
        first = self.store.create_chat_conversation("c1", customer, "客户甲", "zh-CN")
        second = self.store.create_chat_conversation("c1", customer, "客户甲", "zh-CN")
        for conversation in (first, second):
            self.store.add_message(conversation, "in", "预约需要提前多久？", "mock")
            self.store.add_message(conversation, "out", "一般提前一天预约即可。", "mock")
        self.assertEqual(1, self.store.build_faq_candidates())

        candidate = self.store.list_faq_candidates()[0]
        self.assertEqual("预约需要提前多久？", candidate["question"])
        self.assertEqual(2, candidate["occurrences"])
        self.assertEqual(2, len(candidate["sources"]))

        faq_id = self.store.accept_faq_candidate(candidate["id"])
        self.assertIsNotNone(faq_id)
        self.assertIsNone(self.store.get_faq(faq_id)["reviewed_at"])
        self.assertIsNone(self.store.match_faq("预约需要提前多久", "zh-CN"))
        self.store.approve_faq(faq_id)
        matched = self.store.match_faq("预约需要提前多久", "zh-CN")
        self.assertEqual(faq_id, matched["id"])
        self.assertTrue(matched["language_supported"])

    def test_candidates_ignore_unique_messages(self):
        customer = self.store.upsert_customer("c1", "u1", "客户甲", locale="zh-CN")
        conversation = self.store.create_chat_conversation("c1", customer, "客户甲", "zh-CN")
        self.store.add_message(conversation, "in", "只问一次的问题", "mock")
        self.store.add_message(conversation, "out", "只回复一次", "mock")
        self.assertEqual(0, self.store.build_faq_candidates())


if __name__ == "__main__":
    unittest.main()

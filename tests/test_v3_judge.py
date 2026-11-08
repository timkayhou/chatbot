import unittest

from scripts.v3_language_check import judge_part_a, judge_part_b


class V3JudgeTests(unittest.TestCase):
    def test_traditional_reply_with_shared_chars_passes(self):
        passed, reason, detail = judge_part_a("進水情況是否在保固範圍內，需視條款而定。", "zh-TW")
        self.assertTrue(passed)
        self.assertEqual("ok_shared", reason)
        self.assertIn("共用字", detail)

    def test_traditional_reply_with_clear_traditional_chars_passes(self):
        passed, reason, _ = judge_part_a("維修時間需要提前預約。", "zh-TW")
        self.assertTrue(passed)
        self.assertEqual("ok", reason)

    def test_simplified_reply_is_rejected_for_traditional(self):
        passed, reason, _ = judge_part_a("这个情况需要检查。", "zh-TW")
        self.assertFalse(passed)
        self.assertEqual("contains_simplified_char", reason)

    def test_part_b_missing_model_fails(self):
        entities = {
            "price": "¥299",
            "model": "ThinkPad X1 Carbon",
            "id": "A-2048",
            "url": "https://example.com/x1",
        }
        passed, reason, _, missing = judge_part_b("费用 ¥299，编号 A-2048，详见 https://example.com/x1。", entities)
        self.assertFalse(passed)
        self.assertEqual(["model"], missing)

    def test_part_b_full_fidelity_passes(self):
        entities = {
            "price": "¥299",
            "model": "ThinkPad X1 Carbon",
            "id": "A-2048",
            "url": "https://example.com/x1",
        }
        passed, reason, _, missing = judge_part_b(
            "ThinkPad X1 Carbon 费用 ¥299，编号 A-2048，详见 https://example.com/x1。",
            entities,
        )
        self.assertTrue(passed)
        self.assertEqual("ok", reason)
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()

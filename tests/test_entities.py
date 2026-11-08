import unittest

from workbench.entities import extract, missing_entities, protect, restore


class EntityTests(unittest.TestCase):
    def test_extracts_prices_and_urls(self):
        found = extract("报价 ¥299，另有 $1,280 和 https://example.com/x1 。")
        self.assertEqual({"¥299", "$1,280"}, found["price"])
        self.assertEqual({"https://example.com/x1"}, found["url"])

    def test_missing_price_is_detected(self):
        missing = missing_entities("更换费用是 ¥299。", "更换费用是 299 元。")
        self.assertEqual(["¥299"], missing)

    def test_preserved_entities_are_clean(self):
        missing = missing_entities(
            "费用 ¥299，详见 https://example.com/x1 。",
            "料金は ¥299 です。詳細は https://example.com/x1 をご覧ください。",
        )
        self.assertEqual([], missing)

    def test_missing_model_token_is_detected(self):
        self.assertIn("X1", missing_entities("ThinkPad X1 Carbon 换屏。", "换屏。"))
        self.assertEqual([], missing_entities("ThinkPad X1 Carbon 换屏。", "ThinkPad X1 Carbon 换屏。"))

    def test_protect_masks_and_restores_full_entities(self):
        original = "Huawei MateBook 14 的键盘更换报价为 ¥470，服务编号 W-2237，见 https://example.com/x1。"
        masked, mapping = protect(original)
        self.assertNotIn("Huawei", masked)
        self.assertNotIn("¥470", masked)
        self.assertNotIn("W-2237", masked)
        self.assertIn("⟦E1⟧", masked)
        self.assertEqual(original, restore(masked, mapping))

    def test_text_without_entities_is_clean(self):
        self.assertEqual([], missing_entities("请到店检测。", "请到店检测。"))


if __name__ == "__main__":
    unittest.main()

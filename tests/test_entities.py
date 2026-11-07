import unittest

from workbench.entities import extract, missing_entities


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

    def test_text_without_entities_is_clean(self):
        self.assertEqual([], missing_entities("请到店检测。", "请到店检测。"))


if __name__ == "__main__":
    unittest.main()

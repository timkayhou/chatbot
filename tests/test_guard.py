import unittest

from workbench.guard import GuardPolicy, check_text


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.policy = GuardPolicy(blocked_terms=("external contact",), allowed_domains=("example.test",))

    def test_blocks_phone_variants(self):
        for value in ("+81 90-1234-5678", "０９０・１２３４・５６７８", "090\u200b1234\u200b5678"):
            with self.subTest(value=value):
                self.assertEqual("block", check_text(value, self.policy).verdict)

    def test_blocks_split_configured_term(self):
        self.assertEqual("block", check_text("external · contact", self.policy).verdict)

    def test_blocks_url_but_allows_exact_domain_and_subdomain(self):
        self.assertEqual("block", check_text("see bad.example/path", self.policy).verdict)
        self.assertEqual("pass", check_text("see https://help.example.test/path", self.policy).verdict)

    def test_checks_ocr_and_qr_boundaries(self):
        self.assertEqual("block", check_text("ok", self.policy, extracted_text=("090-1234-5678",)).verdict)
        self.assertEqual("block", check_text("ok", self.policy, qr_payloads=("payload",)).verdict)

    def test_compliant_faq_is_not_blocked(self):
        samples = ("您好，请问需要什么帮助？", "承知しました。確認いたします。", "We can help with that.")
        self.assertTrue(all(check_text(item, self.policy).verdict == "pass" for item in samples))

    def test_red_team_corpus_has_full_recall_and_low_false_positive_rate(self):
        separators = (" ", "-", "・", "·", "\u200b", "／", "—", "～")
        attacks = [f"联系 090{separator}1234{separator}5678" for separator in separators]
        attacks += [f"external{separator}contact" for separator in separators]
        attacks += [
            "http://blocked.example/path",
            "www.blocked.example",
            "blocked.example/path",
            "ＨＴＴＰＳ：／／blocked.example",
        ]
        compliant = (
            "您好，请说明需要协助的事项。",
            "我们会在营业时间内回复。",
            "维修前会先说明预计费用。",
            "请提供设备型号，但不要发送密码。",
            "承知しました。確認後にご案内します。",
            "ご来店前に受付状況をご確認ください。",
            "We will review the details and reply shortly.",
            "Please describe the device symptoms.",
            "您好，我們會先確認設備狀況。",
            "請不要傳送密碼或其他敏感資料。",
        ) * 3
        blocked = sum(check_text(value, self.policy).verdict == "block" for value in attacks)
        false_blocks = sum(check_text(value, self.policy).verdict == "block" for value in compliant)
        self.assertEqual(len(attacks), blocked)
        self.assertLessEqual(false_blocks / len(compliant), 0.05)


if __name__ == "__main__":
    unittest.main()


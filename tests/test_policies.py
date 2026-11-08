import json
import tempfile
import unittest
from pathlib import Path

from workbench.guard import check_text
from workbench.policies import GENERIC, PlatformPolicy, guard_for, load_policies


class PolicyLoaderTests(unittest.TestCase):
    def test_loads_local_rule_files_and_skips_bad_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.json").write_text(
                json.dumps(
                    {
                        "id": "sample",
                        "name": "样例",
                        "version": "1",
                        "blocked_terms": ["加微信", " "],
                        "blocked_patterns": ["wxid_[a-z0-9]+", "("],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            Path(directory, "broken.json").write_text("{not json", encoding="utf-8")
            policies = load_policies(directory)
            self.assertIn("generic", policies)
            sample = policies["sample"]
            self.assertEqual("样例", sample.name)
            self.assertEqual(("加微信",), sample.blocked_terms)
            self.assertEqual(("wxid_[a-z0-9]+",), sample.blocked_patterns)

    def test_missing_directory_returns_generic(self):
        policies = load_policies("missing-directory")
        self.assertEqual({"generic": GENERIC}, policies)


class PolicyGuardTests(unittest.TestCase):
    def setUp(self):
        self.policy = guard_for(
            PlatformPolicy(
                id="t",
                name="测试",
                blocked_terms=("加微信", "转账"),
                blocked_patterns=(r"wxid_[a-z0-9]+",),
            )
        )

    def test_blocks_split_characters(self):
        result = check_text("可以加 微 信 详聊", self.policy)
        self.assertEqual("block", result.verdict)
        self.assertIn("term.blocked", [hit.rule for hit in result.hits])

    def test_blocks_configured_pattern(self):
        result = check_text("我的 wxid_abc123", self.policy)
        self.assertEqual("block", result.verdict)
        self.assertIn("term.pattern", [hit.rule for hit in result.hits])

    def test_allows_neutral_service_reply(self):
        result = check_text("建议先做磁盘检测，确认后再安排到店。", self.policy)
        self.assertEqual("pass", result.verdict)

    def test_generic_policy_allows_neutral_reply(self):
        self.assertEqual("pass", check_text("请到店检测。", guard_for(GENERIC)).verdict)


if __name__ == "__main__":
    unittest.main()

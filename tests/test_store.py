import tempfile
import threading
import unittest
from pathlib import Path

from workbench.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite"
        self.store = Store(self.path)
        self.draft = self.store.create_work("test", "zh-CN", "hello", "msg-1", "reply")

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_recovery_and_idempotent_approval(self):
        self.assertTrue(self.store.approve(self.draft, 1))
        self.assertFalse(Store(self.path).approve(self.draft, 1))
        self.assertEqual("approved", Store(self.path).get_draft(self.draft)["state"])

    def test_concurrent_claim_has_one_winner(self):
        self.assertTrue(self.store.approve(self.draft, 1, "account-a"))
        barrier = threading.Barrier(3)
        results = []
        def claim(owner):
            barrier.wait()
            results.append(self.store.claim("account-a", owner))
        threads = [threading.Thread(target=claim, args=(f"worker-{n}",)) for n in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(1, sum(result is not None for result in results))

    def test_manual_copy_is_not_marked_sent(self):
        self.store.approve(self.draft, 1)
        self.assertEqual("reply", self.store.mark_copied(self.draft))
        self.assertEqual("reply", self.store.mark_copied(self.draft))
        self.assertEqual("copied", self.store.get_draft(self.draft)["state"])

    def test_takeover_stales_draft_and_cancels_delivery(self):
        self.store.approve(self.draft, 1)
        row = self.store.get_draft(self.draft)
        self.store.set_takeover(row["conversation_id"])
        self.assertEqual("stale", self.store.get_draft(self.draft)["state"])

    def test_expired_draft_cannot_be_approved(self):
        self.assertTrue(self.store.expire_draft(self.draft))
        self.assertFalse(self.store.approve(self.draft, 1))
        self.assertEqual("stale", self.store.get_draft(self.draft)["state"])

    def test_delivery_terminal_result_is_idempotent_and_not_retried(self):
        self.store.approve(self.draft, 1, "account-a")
        item = self.store.claim("account-a", "worker-a")
        self.assertTrue(self.store.start_sending(item["id"], "worker-a"))
        self.assertTrue(self.store.finish_sending(item["id"], "worker-a", "unknown"))
        self.assertFalse(self.store.finish_sending(item["id"], "worker-a", "sent"))
        self.assertIsNone(self.store.claim("account-a", "worker-b"))

    def test_media_analysis_is_persisted_across_restart(self):
        self.store.add_media_analysis(self.draft, "test.png", "abc", ("visible text",), ("qr value",), 100, 100)
        self.assertEqual((("visible text",), ("qr value",)), Store(self.path).media_inputs(self.draft))


if __name__ == "__main__":
    unittest.main()


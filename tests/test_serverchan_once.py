import tempfile
import unittest

import wechat_crawler


class TestServerChanOnce(unittest.TestCase):
    def test_throttle(self):
        calls = []

        def fake_send(sendkey, title, desp, timeout=20):
            calls.append((sendkey, title, desp, timeout))
            return {"ok": True}

        old = wechat_crawler.send_serverchan_message
        wechat_crawler.send_serverchan_message = fake_send
        try:
            with tempfile.TemporaryDirectory() as d:
                r1 = wechat_crawler.send_serverchan_message_once(
                    "k",
                    "t",
                    "d",
                    dedupe_key="auth_expired_detected",
                    ttl_seconds=3600,
                    state_dir=d,
                )
                r2 = wechat_crawler.send_serverchan_message_once(
                    "k",
                    "t",
                    "d",
                    dedupe_key="auth_expired_detected",
                    ttl_seconds=3600,
                    state_dir=d,
                )
                self.assertTrue(r1.get("ok"))
                self.assertTrue(r2.get("ok"))
                self.assertEqual(len(calls), 1)
                self.assertTrue(r2.get("skipped"))
        finally:
            wechat_crawler.send_serverchan_message = old


if __name__ == "__main__":
    unittest.main()


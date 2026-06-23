import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wechat_crawler


class TestAnalysisQueueSchedule(unittest.TestCase):
    def test_enqueue_single_article_analysis_creates_pending_job(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fetched = {
                "article_id": "aid-1",
                "title": "A1",
                "account": "测试号",
                "url": "https://mp.weixin.qq.com/s/aid-1",
            }
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root):
                result = wechat_crawler._enqueue_single_article_analysis_job(
                    {"analysis_enabled": True},
                    fetched,
                )
                self.assertEqual(result["status"], "scheduled")
                job_files = list((root / "async_jobs").glob("*.json"))
                self.assertEqual(len(job_files), 1)
                payload = json.loads(job_files[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "pending")
                self.assertEqual(payload["article_id"], "aid-1")

    def test_enqueue_dedupes_existing_active_job(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fetched = {
                "article_id": "aid-2",
                "title": "A2",
                "account": "测试号",
                "url": "https://mp.weixin.qq.com/s/aid-2",
            }
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root):
                first = wechat_crawler._enqueue_single_article_analysis_job({}, fetched)
                second = wechat_crawler._enqueue_single_article_analysis_job({}, fetched)
                self.assertEqual(first["status"], "scheduled")
                self.assertEqual(second["status"], "deduped")
                self.assertEqual(len(list((root / "async_jobs").glob("*.json"))), 1)

    def test_enqueue_revives_failed_external_job_when_article_seen_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            jobs_dir = root / "async_jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            job_path = jobs_dir / "existing.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_type": "single_article_analysis",
                        "article_id": "aid-3",
                        "status": "failed_external",
                        "retry_state": {
                            "attempt": 4,
                            "retry_mode": "until_success",
                            "first_failed_at": "2026-06-23T08:00:00",
                            "last_failed_at": "2026-06-23T09:00:00",
                            "last_reason": "wechat_auth_required",
                            "next_retry_at": "2026-06-23T09:30:00",
                            "stop_reason": "wechat_auth_required",
                            "notified": True,
                        },
                        "payload": {"fetched": {"article_id": "aid-3", "title": "old"}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fetched = {
                "article_id": "aid-3",
                "title": "A3-new",
                "account": "测试号",
                "url": "https://mp.weixin.qq.com/s/aid-3",
            }
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root):
                result = wechat_crawler._enqueue_single_article_analysis_job({}, fetched)
                self.assertEqual(result["status"], "revived")
                payload = json.loads(job_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "pending")
                self.assertEqual(payload["payload"]["fetched"]["title"], "A3-new")
                self.assertEqual(payload["retry_state"]["attempt"], 1)
                self.assertEqual(payload["retry_state"]["last_reason"], "")
                self.assertEqual(payload["retry_state"]["next_retry_at"], "")
                self.assertEqual(payload["retry_state"]["stop_reason"], "")
                self.assertFalse(payload["retry_state"]["notified"])

    def test_enqueue_revives_legacy_retry_state_job_when_article_seen_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            jobs_dir = root / "async_jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            job_path = jobs_dir / "retrying.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_type": "single_article_analysis",
                        "payload": {"fetched": {"article_id": "aid-4", "title": "old"}},
                        "retry_state": {
                            "attempt": 3,
                            "retry_mode": "until_success",
                            "first_failed_at": "2026-06-23T08:00:00",
                            "last_failed_at": "2026-06-23T08:10:00",
                            "last_reason": "ollama_timeout",
                            "next_retry_at": "2026-06-23T08:40:00",
                            "stop_reason": "",
                            "notified": False,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fetched = {
                "article_id": "aid-4",
                "title": "A4-new",
                "account": "测试号",
                "url": "https://mp.weixin.qq.com/s/aid-4",
            }
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root):
                result = wechat_crawler._enqueue_single_article_analysis_job({}, fetched)
                self.assertEqual(result["status"], "revived")
                payload = json.loads(job_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "pending")
                self.assertEqual(payload["payload"]["fetched"]["title"], "A4-new")
                self.assertEqual(payload["retry_state"]["attempt"], 1)
                self.assertEqual(payload["retry_state"]["last_reason"], "")
                self.assertEqual(payload["retry_state"]["next_retry_at"], "")


if __name__ == "__main__":
    unittest.main()

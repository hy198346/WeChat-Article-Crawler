import json
import os
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

    def test_run_extract_from_url_queues_analysis_when_push_enabled(self):
        fetched = {
            "article_id": "aid-push-1",
            "title": "queued article",
            "account": "测试号",
            "url": "https://mp.weixin.qq.com/s/aid-push-1",
            "date": "2026-06-23 10:00",
            "published_at": "2026-06-23 10:00",
        }
        config = {"token": "t", "cookie": "c", "analysis_enabled": True}
        with mock.patch.object(wechat_crawler, "fetch_article_markdown", return_value=fetched), mock.patch.object(
            wechat_crawler,
            "push_article_to_serverchan",
            return_value={"ok": True},
        ), mock.patch.object(
            wechat_crawler,
            "_enqueue_single_article_analysis_job",
            return_value={"status": "scheduled"},
        ) as enqueue_mock:
            payload = wechat_crawler.run_extract_from_url(
                "https://mp.weixin.qq.com/s/aid-push-1",
                push=True,
                config=config,
            )
        self.assertEqual(payload["analysis"]["status"], "pending")
        enqueue_mock.assert_called_once_with(config, dict(fetched))

    def test_schedule_async_job_process_mode_keeps_non_analysis_jobs_immediate(self):
        with mock.patch.object(wechat_crawler, "_write_async_job_file") as write_mock, mock.patch.object(
            wechat_crawler,
            "_spawn_async_job_process",
        ) as spawn_mock:
            old_mode = getattr(wechat_crawler, "_ASYNC_JOB_DISPATCH_MODE", None)
            try:
                wechat_crawler._ASYNC_JOB_DISPATCH_MODE = "process"
                wechat_crawler._schedule_async_job(
                    "batch",
                    wechat_crawler._run_batch_analysis_pipeline,
                    {},
                    [],
                    [],
                    {},
                )
            finally:
                if old_mode is not None:
                    wechat_crawler._ASYNC_JOB_DISPATCH_MODE = old_mode
            write_mock.assert_called_once()
            spawn_mock.assert_called_once()

    def test_drain_analysis_queue_runs_pending_job_to_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "ok", "summary": "done"},
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-1",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-1", "title": "T1"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["processed"], 1)
                self.assertEqual(result["done"], 1)
                job_files = list((root / "async_jobs").glob("*.json"))
                self.assertEqual(len(job_files), 1)
                payload = json.loads(job_files[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "done")

    def test_drain_analysis_queue_marks_recoverable_failure_as_retry_waiting_without_respawn(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "error", "reason": "timeout"},
            ), mock.patch.object(wechat_crawler, "_spawn_async_job_process") as spawn_mock:
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-2",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-2", "title": "T2"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(result["retried"], 1)
                self.assertEqual(payload["status"], "retry_waiting")
                self.assertEqual(payload["retry_state"]["last_reason"], "timeout")
                self.assertTrue(payload["retry_state"]["next_retry_at"])
                spawn_mock.assert_not_called()

    def test_drain_analysis_queue_marks_external_failure_as_failed_external(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "error", "reason": "wechat_auth_required"},
            ), mock.patch.object(
                wechat_crawler,
                "_notify_async_analysis_stop",
                return_value={"ok": True},
            ):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-3",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-3", "title": "T3"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(result["failed_external"], 1)
                self.assertEqual(payload["status"], "failed_external")
                self.assertEqual(payload["retry_state"]["stop_reason"], "wechat_auth_required")

    def test_drain_analysis_queue_sets_job_running_before_invoking_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            observed = {}

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                job_files = list((root / "async_jobs").glob("*.json"))
                self.assertEqual(len(job_files), 1)
                observed["status_before_return"] = json.loads(job_files[0].read_text(encoding="utf-8")).get("status")
                return {"status": "ok", "summary": "done"}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-running",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-running", "title": "TR"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["done"], 1)
                self.assertEqual(observed["status_before_return"], "running")

    def test_drain_analysis_queue_skips_when_live_lock_is_held(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            lock_path = root / "analysis_queue.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": "2099-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root):
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["status"], "locked")

    def test_drain_analysis_queue_takes_over_stale_lock_from_dead_pid(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            lock_path = root / "analysis_queue.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 999999,
                        "started_at": "2026-06-23T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_process_exists",
                return_value=False,
            ), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "ok", "summary": "done"},
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-4",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-4", "title": "T4"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["done"], 1)
                self.assertFalse(lock_path.exists())

    def test_drain_analysis_queue_takes_over_stale_lock_from_old_started_at(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            lock_path = root / "analysis_queue.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": "2000-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_process_exists",
                return_value=True,
            ), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "ok", "summary": "done"},
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-run-5",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-run-5", "title": "T5"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["done"], 1)


if __name__ == "__main__":
    unittest.main()

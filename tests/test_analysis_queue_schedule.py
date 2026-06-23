import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wechat_crawler

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestAnalysisQueueSchedule(unittest.TestCase):
    def test_analysis_queue_plist_uses_every_30_minute_schedule(self):
        plist_path = REPO_ROOT / "config/launchd/com.wechat.articlecrawler.analysis-queue.plist"
        payload = plistlib.loads(plist_path.read_bytes())
        self.assertEqual(payload["Label"], "com.wechat.articlecrawler.analysis-queue")
        self.assertEqual(payload["StartCalendarInterval"], [{"Minute": 5}, {"Minute": 35}])
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/bin/zsh",
                str(REPO_ROOT / "bin/run_analysis_queue_launchd.sh"),
            ],
        )

    def test_analysis_queue_launchd_script_runs_both_queue_drains(self):
        script_path = REPO_ROOT / "bin/run_analysis_queue_launchd.sh"
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("--drain-analysis-queue", content)
        self.assertIn("--drain-batch-followup-queue", content)
        self.assertLess(content.index("--drain-analysis-queue"), content.index("--drain-batch-followup-queue"))

    def test_readme_documents_final_queue_workflow_and_batch_followup_drain(self):
        readme_path = REPO_ROOT / "README.md"
        content = readme_path.read_text(encoding="utf-8")
        self.assertIn("## 自动解读队列", content)
        self.assertIn("com.wechat.articlecrawler.analysis-queue", content)
        self.assertIn("05/35", content)
        self.assertIn("--drain-analysis-queue", content)
        self.assertIn("--drain-batch-followup-queue", content)
        self.assertIn("analysis-batch-followup", content)
        self.assertIn("先把 plist 里的绝对路径改成你本机的项目路径", content)
        self.assertIn('"analysis_base_url": "http://192.168.9.158:11434"', content)
        self.assertIn('"analysis_model": "qwen2.5-coder:14b-cpu"', content)
        self.assertIn("| analysis_base_url | 可选 | http://192.168.9.158:11434 |", content)
        self.assertIn("| analysis_model | 可选 | qwen2.5-coder:14b-cpu |", content)

    def test_spec_documents_batch_followup_drain_behavior(self):
        spec_path = REPO_ROOT / "docs/superpowers/specs/2026-06-23-wechat-analysis-queue-design.md"
        content = spec_path.read_text(encoding="utf-8")
        self.assertIn("## Implementation Notes", content)
        self.assertIn("Automatic analysis now uses enqueue-only scheduling", content)
        self.assertIn("05/35", content)
        self.assertIn("--drain-analysis-queue", content)
        self.assertIn("--drain-batch-followup-queue", content)
        self.assertIn("analysis-batch-followup", content)

    def test_drain_commands_do_not_fallback_to_config_json_example(self):
        old_argv = sys.argv[:]
        load_calls = []
        drain_calls = []
        old_load_json = wechat_crawler.load_json
        old_exists = wechat_crawler.os.path.exists
        old_drain = wechat_crawler.drain_analysis_queue
        old_load_env = wechat_crawler._load_env_into_process
        try:
            sys.argv = ["wechat_crawler.py", "--drain-analysis-queue"]

            def fake_load_json(path):
                load_calls.append(path)
                if path == wechat_crawler.CONFIG_FILE:
                    return {}
                if path.endswith("config.json.example"):
                    raise AssertionError("drain command should not read config.json.example")
                return {}

            wechat_crawler.load_json = fake_load_json
            wechat_crawler.os.path.exists = lambda path: True
            wechat_crawler._load_env_into_process = lambda root: None
            wechat_crawler.drain_analysis_queue = lambda config: drain_calls.append(dict(config or {})) or {
                "status": "ok",
                "processed": 0,
                "done": 0,
                "retried": 0,
                "failed_external": 0,
            }

            wechat_crawler.main()

            self.assertEqual(load_calls, [wechat_crawler.CONFIG_FILE])
            self.assertEqual(drain_calls, [{}])
        finally:
            sys.argv = old_argv
            wechat_crawler.load_json = old_load_json
            wechat_crawler.os.path.exists = old_exists
            wechat_crawler._load_env_into_process = old_load_env
            wechat_crawler.drain_analysis_queue = old_drain

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

    def test_schedule_async_job_process_mode_queues_batch_followup_without_spawn(self):
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
            spawn_mock.assert_not_called()
            scheduled_job = write_mock.call_args[0][0]
            self.assertEqual(scheduled_job["job_type"], "batch_analysis_pipeline")
            self.assertEqual(scheduled_job.get("queue_name"), "analysis-batch-followup")

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

    def test_drain_analysis_queue_passes_runtime_config_to_failure_notification(self):
        captured = {}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return {"status": "skipped", "reason": "wechat_auth_required"}

            def fake_notify(config, fetched, reason):
                captured["config"] = dict(config or {})
                return {"ok": True}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(
                wechat_crawler,
                "_notify_async_analysis_stop",
                side_effect=fake_notify,
            ):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "article_id": "aid-runtime-notify",
                        "status": "pending",
                        "payload": {
                            "config": {"serverchan_sendkey": "stale-sendkey", "analysis_enabled": False},
                            "fetched": {"article_id": "aid-runtime-notify", "title": "T4"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                result = wechat_crawler.drain_analysis_queue(
                    {"serverchan_sendkey": "runtime-sendkey", "analysis_enabled": True}
                )

                self.assertEqual(result["failed_external"], 1)
                self.assertEqual(captured["config"]["serverchan_sendkey"], "runtime-sendkey")
                self.assertTrue(captured["config"]["analysis_enabled"])

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

    def test_drain_analysis_queue_recovers_stale_running_job(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_process_exists",
                return_value=False,
            ), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "ok", "summary": "done"},
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-stale-running",
                        "status": "running",
                        "running_pid": 999999,
                        "running_started_at": "2026-06-01T00:00:00",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-stale-running", "title": "SR"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "2026-06-01T00:00:00",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(result["processed"], 1)
                self.assertEqual(result["done"], 1)
                self.assertEqual(payload["status"], "done")

    def test_drain_analysis_queue_skips_live_running_job(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_process_exists",
                return_value=True,
            ):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-live-running",
                        "status": "running",
                        "running_pid": 12345,
                        "running_started_at": "2099-01-01T00:00:00",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-live-running", "title": "LR"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "2099-01-01T00:00:00",
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["processed"], 0)
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "running")

    def test_drain_analysis_queue_only_consumes_analysis_queue_jobs(self):
        seen = []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                seen.append(fetched["article_id"])
                return {"status": "ok", "summary": "done"}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                queue_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-queue-only",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-queue-only", "title": "Q"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                other_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "other_async_job",
                        "job_type": "single_article_analysis",
                        "queue_name": "other-queue",
                        "article_id": "aid-other-queue",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-other-queue", "title": "O"},
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
                self.assertEqual(seen, ["aid-queue-only"])
                self.assertEqual(json.loads(Path(queue_job).read_text(encoding="utf-8"))["status"], "done")
                self.assertEqual(json.loads(Path(other_job).read_text(encoding="utf-8"))["status"], "pending")

    def test_drain_analysis_queue_skips_batch_followup_jobs_to_avoid_contention(self):
        seen = []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                seen.append(fetched["article_id"])
                return {"status": "ok", "summary": "done"}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                batch_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-queue",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_enabled": True},
                            "changed_articles": [],
                            "per_account_payloads": [],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                single_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-single-ok",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-single-ok", "title": "SO"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_analysis_queue_once()

                self.assertEqual(result["processed"], 1)
                self.assertEqual(seen, ["aid-single-ok"])
                self.assertEqual(json.loads(Path(single_job).read_text(encoding="utf-8"))["status"], "done")
                self.assertEqual(json.loads(Path(batch_job).read_text(encoding="utf-8"))["status"], "pending")

    def test_drain_batch_followup_queue_runs_pending_batch_job(self):
        batch_calls = []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_run_batch_analysis_pipeline",
                side_effect=lambda config, changed_articles, per_account_payloads, headers: batch_calls.append(
                    [dict(item) for item in changed_articles]
                )
                or {
                    "status": "ok",
                    "summary": "batch done",
                    "batch_focus": "focus",
                    "shared_themes": [],
                    "priority_reads": [],
                },
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"), mock.patch.object(
                wechat_crawler,
                "persist_batch_analysis_outputs",
            ):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-batch-followup",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_enabled": True},
                            "changed_articles": [{"article_id": "aid-batch-consume", "title": "B"}],
                            "per_account_payloads": [{"fakeid": "fidB"}],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_batch_followup_queue_once()

                self.assertEqual(result["processed"], 1)
                self.assertEqual(result["done"], 1)
                self.assertEqual(len(batch_calls), 1)
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "done")

    def test_drain_batch_followup_queue_stops_terminal_skipped_result_without_requeue(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_run_batch_analysis_pipeline",
                return_value={
                    "status": "skipped",
                    "reason": "failed_external",
                    "kind": "batch_summary",
                },
            ):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-batch-followup",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_enabled": True},
                            "changed_articles": [],
                            "per_account_payloads": [],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_batch_followup_queue_once()

                self.assertEqual(result["failed_external"], 1)
                self.assertEqual(result["retried"], 0)
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "failed_external")
                self.assertEqual(payload["last_result"]["status"], "skipped")
                self.assertEqual(payload["last_result"]["reason"], "failed_external")

    def test_drain_batch_followup_queue_retries_analysis_disabled_result(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_run_batch_analysis_pipeline",
                return_value={
                    "status": "skipped",
                    "reason": "analysis_disabled",
                    "kind": "batch_summary",
                },
            ):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-batch-followup",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_enabled": False},
                            "changed_articles": [],
                            "per_account_payloads": [],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_batch_followup_queue_once()

                self.assertEqual(result["retried"], 1)
                self.assertEqual(result["failed_external"], 0)
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "retry_waiting")
                self.assertEqual(payload["last_result"]["reason"], "analysis_disabled")
                self.assertEqual(payload["retry_state"]["last_reason"], "analysis_disabled")

    def test_drain_batch_followup_queue_skips_analysis_queue_jobs(self):
        seen = []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                seen.append(fetched["article_id"])
                return {"status": "ok", "summary": "done"}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(
                wechat_crawler,
                "_run_batch_analysis_pipeline",
                return_value={
                    "status": "ok",
                    "summary": "batch done",
                    "batch_focus": "focus",
                    "shared_themes": [],
                    "priority_reads": [],
                },
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"), mock.patch.object(
                wechat_crawler,
                "persist_batch_analysis_outputs",
            ):
                single_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-single-ignored",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-single-ignored", "title": "S"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )
                batch_job = wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-batch-followup",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_enabled": True},
                            "changed_articles": [],
                            "per_account_payloads": [],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_batch_followup_queue_once()

                self.assertEqual(result["processed"], 1)
                self.assertEqual(result["done"], 1)
                self.assertEqual(seen, [])
                self.assertEqual(json.loads(Path(single_job).read_text(encoding="utf-8"))["status"], "pending")
                self.assertEqual(json.loads(Path(batch_job).read_text(encoding="utf-8"))["status"], "done")

    def test_drain_analysis_queue_uses_current_runtime_config_instead_of_job_snapshot(self):
        captured = {}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                captured["config"] = dict(config or {})
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
                        "queue_name": "analysis-queue",
                        "article_id": "aid-runtime-config",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_model": "stale-model", "analysis_enabled": False},
                            "fetched": {"article_id": "aid-runtime-config", "title": "R"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler.drain_analysis_queue(
                    {"analysis_model": "current-model", "analysis_enabled": True}
                )

                self.assertEqual(result["done"], 1)
                self.assertEqual(captured["config"]["analysis_model"], "current-model")
                self.assertTrue(captured["config"]["analysis_enabled"])

    def test_drain_batch_followup_queue_uses_current_runtime_config_instead_of_job_snapshot(self):
        captured = {}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_run_batch_analysis_pipeline",
                side_effect=lambda config, changed_articles, per_account_payloads, headers: captured.__setitem__(
                    "config", dict(config or {})
                )
                or {
                    "status": "ok",
                    "summary": "batch done",
                    "batch_focus": "focus",
                    "shared_themes": [],
                    "priority_reads": [],
                },
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"), mock.patch.object(
                wechat_crawler,
                "persist_batch_analysis_outputs",
            ):
                wechat_crawler._write_async_job_file(
                    {
                        "name": "push_latest_all_analysis",
                        "job_type": "batch_analysis_pipeline",
                        "queue_name": "analysis-batch-followup",
                        "status": "pending",
                        "payload": {
                            "config": {"analysis_model": "stale-batch", "analysis_enabled": False},
                            "changed_articles": [],
                            "per_account_payloads": [],
                            "headers": {},
                        },
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler.drain_batch_followup_queue(
                    {"analysis_model": "current-batch", "analysis_enabled": True}
                )

                self.assertEqual(result["done"], 1)
                self.assertEqual(captured["config"]["analysis_model"], "current-batch")
                self.assertTrue(captured["config"]["analysis_enabled"])

    def test_drain_analysis_queue_processes_pending_before_retry_waiting(self):
        call_order = []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                call_order.append(fetched["article_id"])
                return {"status": "ok", "summary": "done"}

            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                side_effect=fake_attach,
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                jobs_dir = root / "async_jobs"
                jobs_dir.mkdir(parents=True, exist_ok=True)
                (jobs_dir / "a-retry.json").write_text(
                    json.dumps(
                        {
                            "name": "single_article_analysis",
                            "job_type": "single_article_analysis",
                            "queue_name": "analysis-queue",
                            "article_id": "aid-retry-first-name",
                            "status": "retry_waiting",
                            "payload": {
                                "config": {},
                                "fetched": {"article_id": "aid-retry-first-name", "title": "R"},
                                "refresh_index": True,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 2,
                                "retry_mode": "until_success",
                                "next_retry_at": "2026-06-01T00:00:00",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (jobs_dir / "z-pending.json").write_text(
                    json.dumps(
                        {
                            "name": "single_article_analysis",
                            "job_type": "single_article_analysis",
                            "queue_name": "analysis-queue",
                            "article_id": "aid-pending-later-name",
                            "status": "pending",
                            "payload": {
                                "config": {},
                                "fetched": {"article_id": "aid-pending-later-name", "title": "P"},
                                "refresh_index": True,
                                "force_reanalyze": False,
                            },
                            "retry_state": wechat_crawler._default_async_retry_state(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = wechat_crawler._drain_analysis_queue_once()

                self.assertEqual(result["processed"], 2)
                self.assertEqual(call_order, ["aid-pending-later-name", "aid-retry-first-name"])

    def test_drain_analysis_queue_keeps_top_level_retry_fields_synchronized(self):
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
                        "queue_name": "analysis-queue",
                        "article_id": "aid-sync-fields",
                        "status": "pending",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-sync-fields", "title": "S"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "attempt": 0,
                        "last_reason": "",
                        "first_failed_at": "",
                        "last_failed_at": "",
                        "next_retry_at": "",
                        "retry_state": wechat_crawler._default_async_retry_state(),
                        "updated_at": "",
                    }
                )

                result = wechat_crawler._drain_analysis_queue_once()

                self.assertEqual(result["retried"], 1)
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                retry_state = payload["retry_state"]
                self.assertEqual(payload["status"], "retry_waiting")
                self.assertEqual(payload["attempt"], retry_state["attempt"])
                self.assertEqual(payload["last_reason"], retry_state["last_reason"])
                self.assertEqual(payload["first_failed_at"], retry_state["first_failed_at"])
                self.assertEqual(payload["last_failed_at"], retry_state["last_failed_at"])
                self.assertEqual(payload["next_retry_at"], retry_state["next_retry_at"])
                spawn_mock.assert_not_called()

    def test_retry_waiting_job_runs_when_due(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), mock.patch.object(
                wechat_crawler,
                "_attach_single_article_analysis",
                return_value={"status": "ok", "summary": "retry ok"},
            ), mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                job_path = wechat_crawler._write_async_job_file(
                    {
                        "name": "single_article_analysis",
                        "job_type": "single_article_analysis",
                        "queue_name": "analysis-queue",
                        "article_id": "aid-retry-1",
                        "status": "retry_waiting",
                        "payload": {
                            "config": {},
                            "fetched": {"article_id": "aid-retry-1", "title": "retry"},
                            "refresh_index": True,
                            "force_reanalyze": False,
                        },
                        "attempt": 1,
                        "next_retry_at": "2026-06-23T00:00:00",
                        "retry_state": {
                            "attempt": 1,
                            "retry_mode": "until_success",
                            "first_failed_at": "2026-06-23T00:00:00",
                            "last_failed_at": "2026-06-23T00:00:00",
                            "last_reason": "timeout",
                            "next_retry_at": "2026-06-23T00:00:00",
                            "stop_reason": "",
                            "notified": False,
                        },
                    }
                )
                result = wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(result["done"], 1)
                self.assertEqual(payload["status"], "done")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
import plistlib
import json

import watchdog


class TestWatchdogSchedule(unittest.TestCase):
    def _run_main_with_root(self, root: Path, enabled: str = "1", create_logs: bool = True):
        if create_logs:
            last_log = root / "logs" / "run_project_launchd.last.log"
            stdout_log = root / "logs" / "launchd.run_project.out.log"
            stderr_log = root / "logs" / "launchd.run_project.err.log"
            last_log.parent.mkdir(parents=True, exist_ok=True)
            last_log.write_text(
                "[launchd] start: 2026-06-12 01:37:20\n"
                "everything ok\n"
                "[launchd] main end: 2026-06-12 01:37:26 exit=0\n",
                encoding="utf-8",
            )
            stdout_log.write_text("", encoding="utf-8")
            stderr_log.write_text("", encoding="utf-8")

        old_repo_root = watchdog._repo_root
        old_load_env = watchdog._load_env_into_process
        old_launchctl_print_job = watchdog._launchctl_print_job
        old_find_old_bootstrap_process = watchdog._find_old_bootstrap_process
        old_send_serverchan_once = watchdog._send_serverchan_once
        old_send_wecom_once = watchdog._send_wecom_once
        old_log = watchdog._log
        old_gethostbyname = watchdog.socket.gethostbyname
        old_environ = dict(watchdog.os.environ)
        try:
            watchdog._repo_root = lambda: root
            watchdog._load_env_into_process = lambda _root: None
            watchdog._launchctl_print_job = lambda uid, label: (True, "loaded")
            watchdog._find_old_bootstrap_process = lambda max_runtime_seconds: []
            watchdog._send_serverchan_once = lambda *args, **kwargs: {"ok": True}
            watchdog._send_wecom_once = lambda *args, **kwargs: {"ok": True}
            watchdog._log = lambda level, msg: None
            watchdog.socket.gethostbyname = lambda host: "127.0.0.1"
            watchdog.os.environ.clear()
            watchdog.os.environ["WECHAT_WATCHDOG_ENABLED"] = enabled
            watchdog.os.environ["WECHAT_WATCHDOG_AUTO_FIX"] = "0"
            watchdog.os.environ["WECHAT_WATCHDOG_STALE_SECONDS"] = "3600"
            return watchdog.main()
        finally:
            watchdog._repo_root = old_repo_root
            watchdog._load_env_into_process = old_load_env
            watchdog._launchctl_print_job = old_launchctl_print_job
            watchdog._find_old_bootstrap_process = old_find_old_bootstrap_process
            watchdog._send_serverchan_once = old_send_serverchan_once
            watchdog._send_wecom_once = old_send_wecom_once
            watchdog._log = old_log
            watchdog.socket.gethostbyname = old_gethostbyname
            watchdog.os.environ.clear()
            watchdog.os.environ.update(old_environ)

    def test_compute_stale_seconds_from_plist(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "job.plist"
            plist = {
                "Label": "com.wechat.articlecrawler.runproject",
                "StartCalendarInterval": [
                    {"Hour": 3, "Minute": 0},
                    {"Hour": 7, "Minute": 0},
                    {"Hour": 9, "Minute": 0},
                    {"Hour": 11, "Minute": 30},
                    {"Hour": 15, "Minute": 0},
                    {"Hour": 17, "Minute": 0},
                    {"Hour": 19, "Minute": 0},
                    {"Hour": 21, "Minute": 0},
                    {"Hour": 23, "Minute": 0},
                ],
            }
            p.write_bytes(plistlib.dumps(plist))
            stale = watchdog._compute_stale_seconds_from_plist(p)
            self.assertEqual(stale, 29700)

    def test_parse_calendar_intervals(self):
        points = watchdog._parse_launchd_calendar_intervals(
            [{"Hour": 8, "Minute": 0}, {"Hour": 8, "Minute": 0}, {"Hour": 12, "Minute": 30}]
        )
        self.assertEqual(points, [(8, 0), (12, 30)])

    def test_last_run_block_ignores_historical_traceback(self):
        text = """
[launchd] start: 2026-06-12 00:00:00
Traceback (most recent call last):
RuntimeError: old failure
[launchd] main end: 2026-06-12 00:00:05 exit=1

[launchd] start: 2026-06-12 01:37:20
everything ok
[launchd] main end: 2026-06-12 01:37:26 exit=0
""".strip()

        summary = watchdog._summarize_last_run_block(text)
        self.assertFalse(summary["has_traceback"])
        self.assertEqual(summary["exit_code"], 0)

    def test_write_failure_summary_creates_output_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            issues = [
                watchdog.Issue(
                    code="nonzero_exit",
                    title="主任务非 0 退出",
                    detail="exit_code=1 file=/tmp/run.log",
                    auto_fix="launchctl kickstart -k gui/501/com.wechat.articlecrawler.runproject",
                    auto_fix_result="ok",
                ),
                watchdog.Issue(
                    code="traceback",
                    title="主任务出现异常堆栈",
                    detail="tail_contains=Traceback file=/tmp/run.log",
                ),
                watchdog.Issue(
                    code="nonzero_exit",
                    title="主任务非 0 退出",
                    detail="exit_code=1 file=/tmp/run.log",
                ),
            ]

            out_path = watchdog._write_failure_summary(root, issues, generated_at="2026-06-12 01:40:14")

            self.assertEqual(out_path, root / "output" / "watchdog_last_failure.json")
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["generated_at"], "2026-06-12 01:40:14")
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["title"], "WeChat-Article-Crawler Watchdog Failure")
            self.assertEqual(payload["detail"], "2 issues detected")
            self.assertEqual(payload["codes"], ["nonzero_exit", "traceback"])
            self.assertEqual(len(payload["issues"]), 3)
            self.assertEqual(payload["issues"][0]["auto_fix_result"], "ok")

    def test_write_failure_summary_uses_unique_temp_file_for_replace(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            issues = [watchdog.Issue(code="traceback", title="主任务出现异常堆栈", detail="detail")]
            replace_calls = []

            old_replace = watchdog.os.replace
            try:
                def fake_replace(src, dst):
                    replace_calls.append((Path(src), Path(dst)))
                    return old_replace(src, dst)

                watchdog.os.replace = fake_replace
                out_path = watchdog._write_failure_summary(root, issues, generated_at="2026-06-12 02:10:00")
            finally:
                watchdog.os.replace = old_replace

            self.assertEqual(out_path, root / "output" / "watchdog_last_failure.json")
            self.assertEqual(len(replace_calls), 1)
            self.assertNotEqual(replace_calls[0][0], out_path.with_suffix(".tmp"))
            self.assertEqual(replace_calls[0][1], out_path)

    def test_main_writes_failure_summary_when_issues_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            summary_path = root / "output" / "watchdog_last_failure.json"

            old_repo_root = watchdog._repo_root
            old_load_env = watchdog._load_env_into_process
            old_launchctl_print_job = watchdog._launchctl_print_job
            old_find_old_bootstrap_process = watchdog._find_old_bootstrap_process
            old_send_serverchan_once = watchdog._send_serverchan_once
            old_send_wecom_once = watchdog._send_wecom_once
            old_log = watchdog._log
            old_gethostbyname = watchdog.socket.gethostbyname
            old_environ = dict(watchdog.os.environ)
            try:
                watchdog._repo_root = lambda: root
                watchdog._load_env_into_process = lambda _root: None
                watchdog._launchctl_print_job = lambda uid, label: (True, "loaded")
                watchdog._find_old_bootstrap_process = lambda max_runtime_seconds: []
                watchdog._send_serverchan_once = lambda *args, **kwargs: {"ok": True}
                watchdog._send_wecom_once = lambda *args, **kwargs: {"ok": True}
                watchdog._log = lambda level, msg: None
                watchdog.socket.gethostbyname = lambda host: "127.0.0.1"
                watchdog.os.environ.clear()
                watchdog.os.environ["WECHAT_WATCHDOG_ENABLED"] = "1"
                watchdog.os.environ["WECHAT_WATCHDOG_AUTO_FIX"] = "0"
                watchdog.os.environ["WECHAT_WATCHDOG_STALE_SECONDS"] = "3600"

                rc = watchdog.main()
            finally:
                watchdog._repo_root = old_repo_root
                watchdog._load_env_into_process = old_load_env
                watchdog._launchctl_print_job = old_launchctl_print_job
                watchdog._find_old_bootstrap_process = old_find_old_bootstrap_process
                watchdog._send_serverchan_once = old_send_serverchan_once
                watchdog._send_wecom_once = old_send_wecom_once
                watchdog._log = old_log
                watchdog.socket.gethostbyname = old_gethostbyname
                watchdog.os.environ.clear()
                watchdog.os.environ.update(old_environ)

            self.assertEqual(rc, 0)
            self.assertTrue(summary_path.exists())
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["codes"], ["last_log_missing", "stderr_log_missing", "stdout_log_missing"])

    def test_main_healthy_run_clears_failure_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            summary_path = root / "output" / "watchdog_last_failure.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text('{"status":"failed"}', encoding="utf-8")

            rc = self._run_main_with_root(root)

            self.assertEqual(rc, 0)
            self.assertFalse(summary_path.exists())

    def test_main_disabled_run_clears_failure_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            summary_path = root / "output" / "watchdog_last_failure.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text('{"status":"failed"}', encoding="utf-8")

            rc = self._run_main_with_root(root, enabled="0", create_logs=False)

            self.assertEqual(rc, 0)
            self.assertFalse(summary_path.exists())

    def test_main_disabled_run_skips_checks_and_autofix_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            summary_path = root / "output" / "watchdog_last_failure.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text('{"status":"failed"}', encoding="utf-8")

            old_repo_root = watchdog._repo_root
            old_load_env = watchdog._load_env_into_process
            old_log = watchdog._log
            old_environ = dict(watchdog.os.environ)
            old_disk_usage = watchdog.shutil.disk_usage
            old_gethostbyname = watchdog.socket.gethostbyname
            old_launchctl_print_job = watchdog._launchctl_print_job
            old_launchctl_bootstrap_gui = watchdog._launchctl_bootstrap_gui
            old_launchctl_kickstart_gui = watchdog._launchctl_kickstart_gui
            old_find_old_bootstrap_process = watchdog._find_old_bootstrap_process
            old_send_serverchan_once = watchdog._send_serverchan_once
            old_send_wecom_once = watchdog._send_wecom_once
            try:
                watchdog._repo_root = lambda: root
                watchdog._load_env_into_process = lambda _root: None
                watchdog._log = lambda level, msg: None
                watchdog.os.environ.clear()
                watchdog.os.environ["WECHAT_WATCHDOG_ENABLED"] = "0"
                watchdog.shutil.disk_usage = lambda path: (_ for _ in ()).throw(AssertionError("disk_usage should not be called"))
                watchdog.socket.gethostbyname = lambda host: (_ for _ in ()).throw(AssertionError("dns should not be called"))
                watchdog._launchctl_print_job = lambda uid, label: (_ for _ in ()).throw(AssertionError("launchctl print should not be called"))
                watchdog._launchctl_bootstrap_gui = lambda uid, plist: (_ for _ in ()).throw(AssertionError("bootstrap should not be called"))
                watchdog._launchctl_kickstart_gui = lambda uid, label: (_ for _ in ()).throw(AssertionError("kickstart should not be called"))
                watchdog._find_old_bootstrap_process = lambda max_runtime_seconds: (_ for _ in ()).throw(AssertionError("process scan should not be called"))
                watchdog._send_serverchan_once = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("serverchan should not be called"))
                watchdog._send_wecom_once = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wecom should not be called"))

                rc = watchdog.main()
            finally:
                watchdog._repo_root = old_repo_root
                watchdog._load_env_into_process = old_load_env
                watchdog._log = old_log
                watchdog.shutil.disk_usage = old_disk_usage
                watchdog.socket.gethostbyname = old_gethostbyname
                watchdog._launchctl_print_job = old_launchctl_print_job
                watchdog._launchctl_bootstrap_gui = old_launchctl_bootstrap_gui
                watchdog._launchctl_kickstart_gui = old_launchctl_kickstart_gui
                watchdog._find_old_bootstrap_process = old_find_old_bootstrap_process
                watchdog._send_serverchan_once = old_send_serverchan_once
                watchdog._send_wecom_once = old_send_wecom_once
                watchdog.os.environ.clear()
                watchdog.os.environ.update(old_environ)

            self.assertEqual(rc, 0)
            self.assertFalse(summary_path.exists())

    def test_clear_failure_summary_cleanup_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            summary_path = root / "output" / "watchdog_last_failure.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")

            old_remove = watchdog.os.remove
            try:
                watchdog.os.remove = lambda path: (_ for _ in ()).throw(PermissionError("permission denied"))
                removed = watchdog._clear_failure_summary(root)
            finally:
                watchdog.os.remove = old_remove

            self.assertFalse(removed)
            self.assertTrue(summary_path.exists())


if __name__ == "__main__":
    unittest.main()

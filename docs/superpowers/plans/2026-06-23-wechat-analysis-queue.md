# WeChat Analysis Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the existing article fetch schedule unchanged while moving automatic article analysis onto a 30-minute queue worker that also retries previously failed articles.

**Architecture:** Reuse the existing `output/async_jobs/` job-file mechanism and single-article analysis path, but split automatic analysis scheduling into an enqueue-only path and a separate queue-drain path. Add a new `launchd` service plus CLI entrypoint to run the queue worker every 30 minutes with file-lock protection and explicit job states.

**Tech Stack:** Python 3, `unittest`, macOS `launchd`, zsh shell scripts, JSON job files

---

### Task 1: Lock Queue State Semantics With Tests

**Files:**
- Create: `tests/test_analysis_queue_schedule.py`
- Modify: `scripts/wechat_article_crawler/wechat_crawler.py`
- Test: `tests/test_analysis_queue_schedule.py`

- [ ] **Step 1: Write the failing tests for queue state transitions**

```python
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
                result = wechat_crawler._enqueue_single_article_analysis_job({"analysis_enabled": True}, fetched)
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
            job_path.write_text(json.dumps({
                "job_type": "single_article_analysis",
                "article_id": "aid-3",
                "status": "failed_external",
                "payload": {"fetched": {"article_id": "aid-3", "title": "old"}},
            }, ensure_ascii=False), encoding="utf-8")
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
```

- [ ] **Step 2: Run the new queue tests to confirm they fail first**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py`

Expected: FAIL with missing helpers such as `_enqueue_single_article_analysis_job` or wrong job payload shape.

- [ ] **Step 3: Add minimal queue-state helpers in `wechat_crawler.py`**

```python
def _analysis_queue_job_payload(config, fetched, refresh_index=True, force_reanalyze=False):
    article_id = _normalize_article_id((fetched or {}).get("article_id")) or build_article_id(fetched or {})
    return {
        "job_type": "single_article_analysis",
        "article_id": article_id,
        "status": "pending",
        "payload": {
            "config": dict(config or {}),
            "fetched": dict(fetched or {}),
            "refresh_index": bool(refresh_index),
            "force_reanalyze": bool(force_reanalyze),
        },
        "attempt": 0,
        "last_reason": "",
        "first_failed_at": "",
        "last_failed_at": "",
        "next_retry_at": "",
        "updated_at": _async_retry_time_text(),
    }


def _enqueue_single_article_analysis_job(config, fetched, refresh_index=True, force_reanalyze=False):
    job = _analysis_queue_job_payload(config, fetched, refresh_index=refresh_index, force_reanalyze=force_reanalyze)
    existing_job_path = _find_active_single_article_async_job_by_article_id(job["article_id"])
    if existing_job_path is None:
        job_path = _write_async_job_file(job)
        return {"status": "scheduled", "job_file": str(job_path), "article_id": job["article_id"]}
    existing = json.loads(existing_job_path.read_text(encoding="utf-8"))
    existing_status = str(existing.get("status") or "").strip()
    if existing_status in ("failed_external", "retry_waiting"):
        existing["status"] = "pending"
        existing["payload"] = job["payload"]
        existing["last_reason"] = ""
        existing["next_retry_at"] = ""
        existing["updated_at"] = _async_retry_time_text()
        _rewrite_async_job_file(existing_job_path, existing)
        return {"status": "revived", "job_file": str(existing_job_path), "article_id": job["article_id"]}
    return {"status": "deduped", "job_file": str(existing_job_path), "article_id": job["article_id"]}
```

- [ ] **Step 4: Re-run the queue-state tests**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py`

Expected: PASS for the new enqueue/dedupe/revive tests.

- [ ] **Step 5: Commit the queue-state foundation**

```bash
git add scripts/wechat_article_crawler/wechat_crawler.py tests/test_analysis_queue_schedule.py
git commit -m "feat: add analysis queue state helpers"
```

### Task 2: Switch Automatic Analysis From Spawn-Now To Enqueue-Only

**Files:**
- Modify: `scripts/wechat_article_crawler/wechat_crawler.py`
- Modify: `tests/test_analysis_queue_schedule.py`
- Test: `tests/test_article_analysis.py`
- Test: `tests/test_analysis_queue_schedule.py`

- [ ] **Step 1: Add failing tests that automatic analysis paths enqueue instead of spawning**

```python
    def test_run_extract_latest_queues_analysis_when_push_enabled(self):
        fetched = {
            "article_id": "aid-push-1",
            "title": "queued article",
            "account": "测试号",
            "url": "https://mp.weixin.qq.com/s/aid-push-1",
            "date": "2026-06-23 10:00",
            "published_at": "2026-06-23 10:00",
        }
        config = {"token": "t", "cookie": "c", "analysis_enabled": True}
        with mock.patch.object(wechat_crawler, "fetch_article_markdown", return_value=fetched), \
             mock.patch.object(wechat_crawler, "push_article_to_serverchan", return_value={"ok": True}), \
             mock.patch.object(wechat_crawler, "_enqueue_single_article_analysis_job", return_value={"status": "scheduled"}) as enqueue_mock:
            payload = wechat_crawler.run_extract_from_url("https://mp.weixin.qq.com/s/aid-push-1", push=True, config=config)
        self.assertEqual(payload["analysis"]["status"], "pending")
        enqueue_mock.assert_called_once()

    def test_schedule_async_job_process_mode_keeps_non_analysis_jobs_immediate(self):
        with mock.patch.object(wechat_crawler, "_write_async_job_file") as write_mock, \
             mock.patch.object(wechat_crawler, "_spawn_async_job_process") as spawn_mock:
            wechat_crawler._schedule_async_job("batch", wechat_crawler._run_batch_analysis_pipeline, {}, [], [], {})
        write_mock.assert_called_once()
        spawn_mock.assert_called_once()
```

- [ ] **Step 2: Run focused tests and confirm current behavior still fails the new expectations**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py -k "queues_analysis or immediate"`

Expected: FAIL because `run_extract_from_url()` and similar call sites still route through `_schedule_async_job(...)`.

- [ ] **Step 3: Replace automatic single-article scheduling call sites with enqueue-only helper**

```python
if push:
    push_result = push_article_to_serverchan(config, payload, override_sendkey=serverchan_sendkey)
    payload["serverchan"] = push_result
    if get_analysis_config(config).get("analysis_enabled"):
        payload["analysis"] = _pending_async_analysis_payload("single_article")
        _enqueue_single_article_analysis_job(config, dict(fetched))
    else:
        payload["analysis"] = None
```

```python
if push and analysis_cfg.get("analysis_enabled"):
    for article in changed_articles:
        article["analysis"] = _pending_async_analysis_payload("single_article")
        source = source_by_key.get(article.get("fakeid")) or source_by_key.get(article.get("url")) or {}
        fetched = source.get("_fetched_article")
        if fetched:
            _enqueue_single_article_analysis_job(config, dict(fetched), refresh_index=False)
    batch_analysis = _pending_async_analysis_payload("batch_summary")
```

- [ ] **Step 4: Keep `_schedule_async_job(...)` for non-queue async paths only**

```python
def _schedule_async_job(name, func, *args, **kwargs):
    if _ASYNC_JOB_DISPATCH_MODE == "process":
        job = _serialize_async_job(name, func, args, kwargs)
        if str(job.get("job_type") or "").strip() == "single_article_analysis":
            fetched = ((job.get("payload") or {}).get("fetched") or {})
            return _enqueue_single_article_analysis_job(
                ((job.get("payload") or {}).get("config") or {}),
                fetched,
                refresh_index=bool((job.get("payload") or {}).get("refresh_index", True)),
                force_reanalyze=bool((job.get("payload") or {}).get("force_reanalyze", False)),
            )
        job_path = _write_async_job_file(job)
        process = _spawn_async_job_process(job_path)
        return {"status": "scheduled", "name": name, "mode": "process", "pid": getattr(process, "pid", None)}
```

- [ ] **Step 5: Re-run the focused automatic-analysis tests**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py tests/test_article_analysis.py -k "analysis or queue"`

Expected: PASS for the enqueue semantics while existing article-analysis tests stay green.

- [ ] **Step 6: Commit the scheduling split**

```bash
git add scripts/wechat_article_crawler/wechat_crawler.py tests/test_analysis_queue_schedule.py tests/test_article_analysis.py
git commit -m "feat: enqueue automatic article analysis"
```

### Task 3: Add Queue Drain Worker, Retry Handling, And File Lock

**Files:**
- Modify: `scripts/wechat_article_crawler/wechat_crawler.py`
- Modify: `tests/test_analysis_queue_schedule.py`
- Test: `tests/test_analysis_queue_schedule.py`

- [ ] **Step 1: Add failing tests for queue drain behavior and retry classification**

```python
    def test_drain_analysis_queue_runs_pending_job_to_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), \
                 mock.patch.object(wechat_crawler, "_attach_single_article_analysis", return_value={"status": "ok", "summary": "done"}), \
                 mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                wechat_crawler._write_async_job_file({
                    "job_type": "single_article_analysis",
                    "article_id": "aid-run-1",
                    "status": "pending",
                    "payload": {"config": {}, "fetched": {"article_id": "aid-run-1", "title": "T1"}, "refresh_index": True, "force_reanalyze": False},
                    "attempt": 0,
                    "last_reason": "",
                    "first_failed_at": "",
                    "last_failed_at": "",
                    "next_retry_at": "",
                    "updated_at": "",
                })
                result = wechat_crawler._drain_analysis_queue_once()
                self.assertEqual(result["processed"], 1)
                self.assertEqual(result["done"], 1)

    def test_drain_analysis_queue_marks_recoverable_failure_as_retry_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), \
                 mock.patch.object(wechat_crawler, "_attach_single_article_analysis", return_value={"status": "error", "reason": "timeout"}):
                job_path = wechat_crawler._write_async_job_file({
                    "job_type": "single_article_analysis",
                    "article_id": "aid-run-2",
                    "status": "pending",
                    "payload": {"config": {}, "fetched": {"article_id": "aid-run-2", "title": "T2"}, "refresh_index": True, "force_reanalyze": False},
                    "attempt": 0,
                    "last_reason": "",
                    "first_failed_at": "",
                    "last_failed_at": "",
                    "next_retry_at": "",
                    "updated_at": "",
                })
                wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "retry_waiting")
                self.assertEqual(payload["last_reason"], "timeout")

    def test_drain_analysis_queue_skips_when_lock_is_held(self):
        with mock.patch.object(wechat_crawler, "_acquire_analysis_queue_lock", return_value=None):
            result = wechat_crawler._drain_analysis_queue_once()
        self.assertEqual(result["status"], "locked")
```

- [ ] **Step 2: Run the queue-drain tests to verify they fail**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py -k "drain_analysis_queue"`

Expected: FAIL with missing worker helpers and missing persisted job states.

- [ ] **Step 3: Implement lock, due-job selection, and drain entrypoint**

```python
def _analysis_queue_lock_path() -> Path:
    return OUTPUT_ROOT / "analysis_queue.lock"


def _acquire_analysis_queue_lock():
    lock_path = _analysis_queue_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None


def _release_analysis_queue_lock(lock_fd):
    if lock_fd is None:
        return
    os.close(lock_fd)
    _analysis_queue_lock_path().unlink(missing_ok=True)
```

```python
def _iter_due_analysis_jobs():
    for job_path in sorted(_async_jobs_dir().glob("*.json")):
        job = json.loads(job_path.read_text(encoding="utf-8"))
        status = str(job.get("status") or "").strip()
        if status == "pending":
            yield job_path, job
            continue
        if status == "retry_waiting" and _seconds_until_async_retry(job.get("next_retry_at")) <= 0:
            yield job_path, job


def _drain_analysis_queue_once():
    lock_fd = _acquire_analysis_queue_lock()
    if lock_fd is None:
        return {"status": "locked", "processed": 0, "done": 0, "retried": 0}
    summary = {"status": "ok", "processed": 0, "done": 0, "retried": 0, "failed_external": 0}
    try:
        for job_path, job in _iter_due_analysis_jobs():
            outcome = _run_analysis_queue_job(job_path, job)
            summary["processed"] += 1
            summary[outcome] = int(summary.get(outcome, 0) or 0) + 1
        return summary
    finally:
        _release_analysis_queue_lock(lock_fd)
```

- [ ] **Step 4: Persist queue job states instead of deleting failed jobs**

```python
def _run_analysis_queue_job(job_path: Path, job):
    payload = job.get("payload") or {}
    job["status"] = "running"
    job["updated_at"] = _async_retry_time_text()
    _rewrite_async_job_file(job_path, job)
    analysis = _attach_single_article_analysis(
        payload.get("config"),
        payload.get("fetched"),
        refresh_index=bool(payload.get("refresh_index", True)),
        force_reanalyze=bool(payload.get("force_reanalyze", False)),
    )
    if _is_successful_async_analysis(analysis):
        job["status"] = "done"
        job["updated_at"] = _async_retry_time_text()
        _rewrite_async_job_file(job_path, job)
        return "done"
    reason = str((analysis or {}).get("reason") or "reanalyze_failed")
    if _classify_async_analysis_failure(reason) == "external":
        job["status"] = "failed_external"
        job["last_reason"] = reason
        job["last_failed_at"] = _async_retry_time_text()
        _rewrite_async_job_file(job_path, job)
        return "failed_external"
    job["status"] = "retry_waiting"
    job["attempt"] = int(job.get("attempt") or 0) + 1
    job["last_reason"] = reason
    job["last_failed_at"] = _async_retry_time_text()
    job["next_retry_at"] = _async_retry_time_text(time.time() + 1800)
    _rewrite_async_job_file(job_path, job)
    return "retried"
```

- [ ] **Step 5: Add a CLI entrypoint and verify queue-drain tests**

```python
parser.add_argument("--drain-analysis-queue", action="store_true")

if args.drain_analysis_queue:
    result = _drain_analysis_queue_once()
    print(json.dumps(result, ensure_ascii=False))
    return
```

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py`

Expected: PASS for queue-drain, lock, and retry-state tests.

- [ ] **Step 6: Commit the queue worker**

```bash
git add scripts/wechat_article_crawler/wechat_crawler.py tests/test_analysis_queue_schedule.py
git commit -m "feat: add analysis queue drain worker"
```

### Task 4: Add The 30-Minute Launchd Service And Script

**Files:**
- Create: `config/launchd/com.wechat.articlecrawler.analysis-queue.plist`
- Create: `bin/run_analysis_queue_launchd.sh`
- Modify: `tests/test_analysis_queue_schedule.py`
- Test: `tests/test_analysis_queue_schedule.py`

- [ ] **Step 1: Add failing tests for the new launchd schedule**

```python
    def test_analysis_queue_plist_uses_every_30_minute_schedule(self):
        import plistlib
        plist_path = Path("config/launchd/com.wechat.articlecrawler.analysis-queue.plist")
        payload = plistlib.loads(plist_path.read_bytes())
        self.assertEqual(payload["Label"], "com.wechat.articlecrawler.analysis-queue")
        self.assertEqual(payload["StartCalendarInterval"], [{"Minute": 0}, {"Minute": 30}])
```

- [ ] **Step 2: Run the schedule test to confirm the new files are missing**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py -k "analysis_queue_plist"`

Expected: FAIL with missing `com.wechat.articlecrawler.analysis-queue.plist`.

- [ ] **Step 3: Add the new `launchd` plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.wechat.articlecrawler.analysis-queue</string>
    <key>WorkingDirectory</key>
    <string>/Users/chenwangqian/trae/WeChat-Article-Crawler</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/zsh</string>
      <string>/Users/chenwangqian/trae/WeChat-Article-Crawler/bin/run_analysis_queue_launchd.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
      <dict><key>Minute</key><integer>0</integer></dict>
      <dict><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/chenwangqian/trae/WeChat-Article-Crawler/logs/launchd.analysis-queue.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/chenwangqian/trae/WeChat-Article-Crawler/logs/launchd.analysis-queue.err.log</string>
    <key>RunAtLoad</key>
    <false/>
  </dict>
</plist>
```

- [ ] **Step 4: Add the launchd shell wrapper**

```bash
#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ENV_FILE="$ROOT/.env"
if [[ -z "${WECHAT_ENV_FILE:-}" && -f "${DEFAULT_ENV_FILE}" ]]; then
  WECHAT_ENV_FILE="${DEFAULT_ENV_FILE}"
fi
if [[ -n "${WECHAT_ENV_FILE:-}" && -f "${WECHAT_ENV_FILE}" ]]; then
  set -a
  source "${WECHAT_ENV_FILE}"
  set +a
fi

cd "$ROOT"
mkdir -p "$ROOT/logs"
/usr/bin/python3 "$ROOT/scripts/wechat_article_crawler/wechat_crawler.py" --drain-analysis-queue
```

- [ ] **Step 5: Re-run the schedule tests**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py -k "analysis_queue_plist or drain_analysis_queue"`

Expected: PASS for file presence, plist schedule, and worker entrypoint expectations.

- [ ] **Step 6: Commit the launchd worker service**

```bash
git add config/launchd/com.wechat.articlecrawler.analysis-queue.plist bin/run_analysis_queue_launchd.sh tests/test_analysis_queue_schedule.py
git commit -m "feat: add launchd service for analysis queue"
```

### Task 5: Cover Regression Paths And Update Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-23-wechat-analysis-queue-design.md`
- Modify: `tests/test_analysis_queue_schedule.py`
- Test: `tests/test_analysis_queue_schedule.py`
- Test: `tests/test_watchdog_schedule.py`
- Test: `tests/test_article_analysis.py`

- [ ] **Step 1: Add final regression tests for retries and repeated fetches**

```python
    def test_retry_waiting_job_runs_when_due(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(wechat_crawler, "OUTPUT_ROOT", root), \
                 mock.patch.object(wechat_crawler, "_attach_single_article_analysis", return_value={"status": "ok", "summary": "retry ok"}), \
                 mock.patch.object(wechat_crawler, "_refresh_analysis_index_html"):
                job_path = wechat_crawler._write_async_job_file({
                    "job_type": "single_article_analysis",
                    "article_id": "aid-retry-1",
                    "status": "retry_waiting",
                    "payload": {"config": {}, "fetched": {"article_id": "aid-retry-1", "title": "retry"}},
                    "attempt": 1,
                    "next_retry_at": "2026-06-23T00:00:00",
                })
                with mock.patch.object(wechat_crawler, "_seconds_until_async_retry", return_value=0):
                    result = wechat_crawler._drain_analysis_queue_once()
                payload = json.loads(Path(job_path).read_text(encoding="utf-8"))
                self.assertEqual(result["done"], 1)
                self.assertEqual(payload["status"], "done")
```

- [ ] **Step 2: Run the full targeted regression suite**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py tests/test_watchdog_schedule.py tests/test_article_analysis.py`

Expected: PASS with no regressions in queue logic, schedule parsing, or article-analysis behavior.

- [ ] **Step 3: Update `README.md` with install and operation instructions**

````md
## 自动解读队列

- 主抓取仍按 `com.wechat.articlecrawler.runproject` 的固定时刻执行
- 自动解读改为写入 `output/async_jobs/`
- 新增 `com.wechat.articlecrawler.analysis-queue`，每 30 分钟消费一次待解读任务
- 队列会同时处理新文章和之前失败但允许重试的文章

手工补跑：

```bash
python3 scripts/wechat_article_crawler/wechat_crawler.py --drain-analysis-queue
```
````

- [ ] **Step 4: Add a short “implemented as planned” note to the spec**

```md
## Implementation Notes

- Automatic analysis now uses enqueue-only scheduling for single-article jobs.
- Queue drain runs through `--drain-analysis-queue`.
- The `analysis-queue` launchd service owns the 30-minute retry cadence.
```

- [ ] **Step 5: Run formatting/sanity checks and commit the docs/regression pass**

Run: `git diff --check`

Expected: PASS with no whitespace errors.

```bash
git add README.md docs/superpowers/specs/2026-06-23-wechat-analysis-queue-design.md tests/test_analysis_queue_schedule.py
git commit -m "docs: document analysis queue workflow"
```

### Task 6: Final Verification, Install, And Operational Smoke Checks

**Files:**
- Modify: `~/Library/LaunchAgents/com.wechat.articlecrawler.analysis-queue.plist` (installed copy)
- Test: `config/launchd/com.wechat.articlecrawler.analysis-queue.plist`
- Test: `logs/launchd.analysis-queue.out.log`
- Test: `logs/launchd.analysis-queue.err.log`

- [ ] **Step 1: Run the project’s targeted Python test suite**

Run: `python3 -m pytest -q tests/test_analysis_queue_schedule.py tests/test_watchdog_schedule.py tests/test_article_analysis.py`

Expected: PASS

- [ ] **Step 2: Install the new launchd plist**

Run:

```bash
cp config/launchd/com.wechat.articlecrawler.analysis-queue.plist ~/Library/LaunchAgents/com.wechat.articlecrawler.analysis-queue.plist
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.wechat.articlecrawler.analysis-queue.plist 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.wechat.articlecrawler.analysis-queue.plist
```

Expected: bootstrap succeeds with no stderr output.

- [ ] **Step 3: Kickstart and inspect the queue worker**

Run:

```bash
launchctl kickstart -k "gui/$(id -u)/com.wechat.articlecrawler.analysis-queue"
launchctl print "gui/$(id -u)/com.wechat.articlecrawler.analysis-queue" | head -n 40
```

Expected: service is loaded and last exit status is `0` or currently running.

- [ ] **Step 4: Smoke test queue drain manually**

Run:

```bash
python3 scripts/wechat_article_crawler/wechat_crawler.py --drain-analysis-queue
tail -n 50 logs/launchd.analysis-queue.out.log
tail -n 50 logs/launchd.analysis-queue.err.log
```

Expected: command prints a JSON summary, stdout log shows processed counts, stderr log stays empty or only contains expected warnings.

- [ ] **Step 5: Commit the verified implementation**

```bash
git status --short
git add scripts/wechat_article_crawler/wechat_crawler.py tests/test_analysis_queue_schedule.py config/launchd/com.wechat.articlecrawler.analysis-queue.plist bin/run_analysis_queue_launchd.sh README.md docs/superpowers/specs/2026-06-23-wechat-analysis-queue-design.md
git commit -m "feat: move wechat analysis to scheduled queue worker"
```

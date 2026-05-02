import os
import re
import sys
import json
import time
import socket
import shutil
import plistlib
import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
 
 
def _ts_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
 
 
def _log(level: str, msg: str) -> None:
    print(f"[{_ts_now()}] [watchdog] level={level} {msg}", flush=True)
 
 
def _parse_env_file(p: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        if not p.exists():
            return out
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = (raw or "").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = (k or "").strip()
            if not k:
                continue
            val = (v or "").strip().strip("'").strip('"').strip()
            out[k] = val
    except Exception:
        return out
    return out
 
 
def _load_env_into_process(root: Path) -> Optional[Path]:
    env_file = os.environ.get("WECHAT_ENV_FILE")
    if not env_file:
        env_file = str(root / ".env")
    p = Path(env_file)
    kv = _parse_env_file(p)
    for k, v in kv.items():
        os.environ.setdefault(k, v)
    return p if p.exists() else None
 
 
def _to_int(v: Optional[str], default: int) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default
 
 
def _to_float(v: Optional[str], default: float) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return default
 
 
def _default_cache_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        base = Path(os.environ.get("XDG_CACHE_HOME") or (home / "Library" / "Caches"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
    return base / "WeChat-Article-Crawler"
 
 
def _dedupe_should_send(dedupe_key: str, ttl_seconds: int, state_dir: Optional[Path] = None) -> Tuple[bool, float]:
    if not dedupe_key:
        return True, 0.0
    base_dir = state_dir if state_dir else _default_cache_dir()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    h = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()
    stamp = base_dir / f"watchdog_once_{h}.stamp"
    now = time.time()
    try:
        if stamp.exists():
            age = now - stamp.stat().st_mtime
            if age < max(0, int(ttl_seconds)):
                return False, float(age)
    except Exception:
        pass
    try:
        stamp.write_text(str(int(now)), encoding="utf-8")
    except Exception:
        try:
            stamp.touch()
        except Exception:
            pass
    return True, 0.0
 
 
def _send_wecom_once(webhook_url: str, content: str, dedupe_key: str, ttl_seconds: int) -> Dict:
    if not webhook_url:
        return {"ok": False, "skipped": True, "reason": "no_webhook"}
    should, age = _dedupe_should_send(dedupe_key, ttl_seconds)
    if not should:
        return {"ok": True, "skipped": True, "reason": "throttled", "age_seconds": age}
    try:
        import requests
 
        resp = requests.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": content}},
            timeout=20,
        )
        resp.raise_for_status()
        try:
            return {"ok": True, "response": resp.json()}
        except Exception:
            return {"ok": True, "response": {"raw": resp.text}}
    except Exception as e:
        return {"ok": False, "error": str(e)}
 
 
def _send_serverchan_once(title: str, desp: str, dedupe_key: str, ttl_seconds: int) -> Dict:
    try:
        from wechat_crawler import send_serverchan_message_once
    except Exception:
        return {"ok": False, "skipped": True, "reason": "no_sender"}
    sendkey = (os.environ.get("SERVERCHAN_SENDKEY") or "").strip()
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    return send_serverchan_message_once(
        sendkey,
        title,
        desp,
        dedupe_key=dedupe_key,
        ttl_seconds=ttl_seconds,
    )
 
 
def _tail_text(p: Path, max_bytes: int = 200_000) -> str:
    try:
        if not p.exists():
            return ""
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
 
 
def _parse_launchd_calendar_intervals(v) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return out
    for it in v:
        if not isinstance(it, dict):
            continue
        h = it.get("Hour")
        m = it.get("Minute")
        try:
            hh = int(h)
            mm = int(m)
        except Exception:
            continue
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out.append((hh, mm))
    out = sorted(set(out))
    return out
 
 
def _max_gap_seconds(points: List[Tuple[int, int]]) -> Optional[int]:
    if not points:
        return None
    mins = sorted([(h * 60 + m) for h, m in points])
    if len(mins) == 1:
        return 24 * 3600
    gaps = []
    for i in range(len(mins) - 1):
        gaps.append((mins[i + 1] - mins[i]) * 60)
    gaps.append(((mins[0] + 24 * 60) - mins[-1]) * 60)
    return max(gaps) if gaps else None
 
 
def _compute_stale_seconds_from_plist(plist_path: Path) -> Optional[int]:
    try:
        if not plist_path.exists():
            return None
        data = plistlib.loads(plist_path.read_bytes())
        points = _parse_launchd_calendar_intervals(data.get("StartCalendarInterval"))
        mg = _max_gap_seconds(points)
        if mg is None:
            return None
        return int(mg * 2 + 15 * 60)
    except Exception:
        return None
 
 
def _run(cmd: List[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            text=True,
        )
        return int(r.returncode), (r.stdout or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 127, str(e)
 
 
def _launchctl_print_job(uid: int, label: str) -> Tuple[bool, str]:
    for scope in [f"gui/{uid}", "system"]:
        code, out = _run(["/bin/launchctl", "print", f"{scope}/{label}"], timeout=15)
        if code == 0 and out:
            return True, out
    return False, ""
 
 
def _launchctl_bootstrap_gui(uid: int, plist_path: Path) -> Tuple[bool, str]:
    if not plist_path.exists():
        return False, "plist_not_found"
    code, out = _run(["/bin/launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], timeout=20)
    return code == 0, out
 
 
def _launchctl_kickstart_gui(uid: int, label: str) -> Tuple[bool, str]:
    code, out = _run(["/bin/launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], timeout=20)
    return code == 0, out
 
 
def _find_old_bootstrap_process(max_runtime_seconds: int) -> List[Tuple[int, int, str]]:
    items: List[Tuple[int, int, str]] = []
    code, out = _run(["/bin/ps", "-axo", "pid=,etimes=,command="], timeout=10)
    if code != 0 or not out:
        return items
    for raw in out.splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s+(\d+)\s+(.*)$", line)
        if not m:
            continue
        pid = int(m.group(1))
        et = int(m.group(2))
        cmd = m.group(3)
        if "bootstrap_refresh_auth.py" not in cmd:
            continue
        if et >= max_runtime_seconds:
            items.append((pid, et, cmd))
    return items
 
 
@dataclass
class Issue:
    code: str
    title: str
    detail: str
    auto_fix: Optional[str] = None
    auto_fix_result: Optional[str] = None
 
 
def _format_issue_lines(issues: List[Issue]) -> str:
    lines = []
    for it in issues:
        s = f"- {it.title}（{it.code}）\n  - 详情：{it.detail}"
        if it.auto_fix:
            s += f"\n  - 自动修复：{it.auto_fix}"
        if it.auto_fix_result:
            s += f"\n  - 修复结果：{it.auto_fix_result}"
        lines.append(s)
    return "\n".join(lines)
 
 
def main() -> int:
    root = Path(__file__).resolve().parent
    env_loaded = _load_env_into_process(root)
 
    label = (os.environ.get("WECHAT_LAUNCHD_LABEL") or "com.wechat.articlecrawler.runproject").strip()
    auto_fix = _to_int(os.environ.get("WECHAT_WATCHDOG_AUTO_FIX") or "1", 1) == 1
    alert_ttl = _to_int(os.environ.get("WECHAT_WATCHDOG_ALERT_TTL_SECONDS"), 6 * 3600)
    stale_seconds = _to_int(os.environ.get("WECHAT_WATCHDOG_STALE_SECONDS"), 0)
    max_runtime_seconds = _to_int(os.environ.get("WECHAT_WATCHDOG_MAX_RUNTIME_SECONDS"), 3600)
    min_free_gb = _to_float(os.environ.get("WECHAT_WATCHDOG_MIN_FREE_GB"), 2.0)
    min_free_percent = _to_float(os.environ.get("WECHAT_WATCHDOG_MIN_FREE_PERCENT"), 5.0)
 
    repo_plist = root / "launchd" / "com.wechat.articlecrawler.runproject.plist"
    if stale_seconds <= 0:
        s = _compute_stale_seconds_from_plist(repo_plist)
        stale_seconds = s if s else 10 * 3600
 
    last_log = root / "logs" / "run_project_launchd.last.log"
    stdout_log = root / "logs" / "launchd.run_project.out.log"
    stderr_log = root / "logs" / "launchd.run_project.err.log"
 
    _log(
        "INFO",
        f"start root={root} env_file={(str(env_loaded) if env_loaded else 'missing')} label={label} auto_fix={int(auto_fix)} stale_seconds={stale_seconds}",
    )
 
    issues: List[Issue] = []
    now = time.time()
 
    try:
        usage = shutil.disk_usage(str(root))
        free_gb = usage.free / (1024**3)
        free_percent = usage.free * 100.0 / max(1, usage.total)
        _log("INFO", f"disk free_gb={free_gb:.2f} free_percent={free_percent:.2f}")
        if free_gb < min_free_gb or free_percent < min_free_percent:
            issues.append(
                Issue(
                    code="disk_low",
                    title="磁盘空间不足",
                    detail=f"free_gb={free_gb:.2f} free_percent={free_percent:.2f} (threshold: {min_free_gb}GB/{min_free_percent}%)",
                )
            )
    except Exception as e:
        issues.append(Issue(code="disk_check_failed", title="磁盘检查失败", detail=str(e)))
 
    try:
        target = os.environ.get("WECHAT_REFRESH_TARGET_URL") or "https://mp.weixin.qq.com/"
        host = "mp.weixin.qq.com"
        try:
            host = re.sub(r"^https?://", "", target).split("/", 1)[0].strip() or host
        except Exception:
            host = "mp.weixin.qq.com"
        socket.gethostbyname(host)
        _log("INFO", f"dns ok host={host}")
    except Exception as e:
        issues.append(Issue(code="dns_failed", title="DNS 解析失败", detail=str(e)))
 
    uid = os.getuid()
    loaded, print_out = _launchctl_print_job(uid, label)
    if not loaded:
        it = Issue(code="launchd_not_loaded", title="launchd 定时任务未加载", detail=f"label={label}")
        if auto_fix:
            user_plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            cand = user_plist if user_plist.exists() else repo_plist
            ok, out = _launchctl_bootstrap_gui(uid, cand)
            it.auto_fix = f"launchctl bootstrap gui/{uid} {cand}"
            it.auto_fix_result = "ok" if ok else (out.strip()[:800] or "failed")
        issues.append(it)
    else:
        _log("INFO", f"launchd loaded label={label}")
 
    for p, code in [(last_log, "last_log_missing"), (stdout_log, "stdout_log_missing"), (stderr_log, "stderr_log_missing")]:
        if not p.exists():
            issues.append(Issue(code=code, title="日志缺失", detail=str(p)))
 
    if last_log.exists():
        try:
            age = now - last_log.stat().st_mtime
            _log("INFO", f"last_run_log age_seconds={int(age)} path={last_log}")
            if age > stale_seconds:
                it = Issue(
                    code="stale_run",
                    title="主任务长时间未运行",
                    detail=f"last_log_age_seconds={int(age)} stale_seconds={stale_seconds}",
                )
                if auto_fix:
                    ok, out = _launchctl_kickstart_gui(uid, label)
                    it.auto_fix = f"launchctl kickstart -k gui/{uid}/{label}"
                    it.auto_fix_result = "ok" if ok else (out.strip()[:800] or "failed")
                issues.append(it)
        except Exception as e:
            issues.append(Issue(code="last_log_stat_failed", title="主任务运行状态检查失败", detail=str(e)))
 
        tail = _tail_text(last_log, max_bytes=200_000)
        if tail:
            if "Traceback (most recent call last)" in tail:
                issues.append(Issue(code="traceback", title="主任务出现异常堆栈", detail=f"tail_contains=Traceback file={last_log}"))
            m = re.search(r"退出码:\s*(\d+)", tail)
            if m:
                issues.append(Issue(code="nonzero_exit", title="主任务非 0 退出", detail=f"exit_code={m.group(1)} file={last_log}"))
 
    stuck = _find_old_bootstrap_process(max_runtime_seconds=max_runtime_seconds)
    if stuck:
        top = stuck[:3]
        detail = "; ".join([f"pid={pid} etimes={et}s" for pid, et, _ in top])
        issues.append(Issue(code="process_stuck", title="主任务可能卡死", detail=f"count={len(stuck)} {detail} (threshold={max_runtime_seconds}s)"))
 
    enabled = _to_int(os.environ.get("WECHAT_WATCHDOG_ENABLED") or "1", 1) == 1
    if not enabled:
        _log("INFO", "disabled by WECHAT_WATCHDOG_ENABLED=0")
        return 0
 
    if issues:
        title = "❌ WeChat-Article-Crawler Watchdog"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        desp = f"时间: {now_str}\n\n" + _format_issue_lines(issues)
        _log("WARN", f"issues={len(issues)}")
        for it in issues:
            _log("WARN", f"issue code={it.code} title={it.title}")
        codes = ",".join(sorted(set([it.code for it in issues if it.code])))
        dedupe_key = f"watchdog:summary:{codes}" if codes else "watchdog:summary"
        _send_serverchan_once(title, desp, dedupe_key=dedupe_key, ttl_seconds=alert_ttl)
        wecom = (os.environ.get("WECOM_WEBHOOK_URL") or os.environ.get("WECOM_WEBHOOKURL") or os.environ.get("WECOM_WEBHOOK") or "").strip()
        if wecom:
            _send_wecom_once(wecom, f"{title}\n{desp}", dedupe_key=dedupe_key, ttl_seconds=alert_ttl)
    else:
        _log("INFO", "ok")
    return 0
 
 
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        _log("ERROR", f"crashed error={e}")
        try:
            title = "❌ WeChat-Article-Crawler Watchdog Crash"
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            desp = f"时间: {now_str}\n\nerror={e}"
            _send_serverchan_once(title, desp, dedupe_key="watchdog:crash", ttl_seconds=6 * 3600)
        except Exception:
            pass
        raise SystemExit(1)

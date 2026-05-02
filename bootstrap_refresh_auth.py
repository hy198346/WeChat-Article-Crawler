import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse


@dataclass
class CmdResult:
    code: int
    seconds: float
    stdout: str
    stderr: str


def _run(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> CmdResult:
    started = time.time()
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        return CmdResult(
            code=p.returncode,
            seconds=time.time() - started,
            stdout=p.stdout or "",
            stderr=p.stderr or "",
        )
    except subprocess.TimeoutExpired as e:
        return CmdResult(
            code=124,
            seconds=time.time() - started,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
        )


def _run_live(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> CmdResult:
    started = time.time()
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: List[str] = []
    try:
        assert p.stdout is not None
        for line in p.stdout:
            lines.append(line)
            print(line, end="")
        code = p.wait(timeout=timeout)
        return CmdResult(code=code, seconds=time.time() - started, stdout="".join(lines), stderr="")
    except subprocess.TimeoutExpired:
        p.kill()
        return CmdResult(code=124, seconds=time.time() - started, stdout="".join(lines), stderr="")


def _host(url: str) -> str:
    return urlparse(url).netloc


def _truncate(text: str, limit: int = 5000) -> str:
    text = text.strip("\n")
    if len(text) <= limit:
        return text
    return text[:2500] + "\n... (truncated) ...\n" + text[-2500:]


def _print_result(prefix: str, res: CmdResult) -> None:
    print(f"{prefix} exit={res.code} time={res.seconds:.1f}s")
    if res.stdout.strip():
        print(_truncate(res.stdout))
    if res.stderr.strip():
        print(_truncate(res.stderr), file=sys.stderr)


def pip_install_with_fallback(requirements_path: Path) -> None:
    mirrors = [
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple",
        "https://pypi.mirrors.ustc.edu.cn/simple",
        "https://mirrors.cloud.tencent.com/pypi/simple",
    ]

    base_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements_path),
        "--disable-pip-version-check",
        "--timeout",
        "15",
        "--retries",
        "1",
    ]

    for idx in mirrors:
        cmd = base_cmd + ["-i", idx, "--trusted-host", _host(idx)]
        res = _run(cmd)
        _print_result(f"pip({idx})", res)
        if res.code == 0:
            return

    res = _run(base_cmd)
    _print_result("pip(pypi)", res)
    if res.code != 0:
        raise RuntimeError("pip install 失败（镜像与官方源均失败）")


def playwright_install_chromium_with_fallback() -> None:
    base_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

    attempts: List[Dict[str, str]] = [
        {
            "PLAYWRIGHT_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/playwright",
            "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
        },
        {
            "PLAYWRIGHT_DOWNLOAD_HOST": "https://npmmirror.com/mirrors/playwright",
            "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
        },
        {
            "PLAYWRIGHT_DOWNLOAD_HOST": "https://registry.npmmirror.com/-/binary/playwright",
            "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
        },
    ]

    for a in attempts:
        env = os.environ.copy()
        env.update(a)
        res = _run(base_cmd, env=env, timeout=120)
        _print_result(f"playwright(mirror)", res)
        if res.code == 0:
            return

    env = os.environ.copy()
    env.pop("PLAYWRIGHT_DOWNLOAD_HOST", None)
    env.pop("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", None)
    res = _run(base_cmd, env=env, timeout=900)
    _print_result("playwright(official)", res)
    if res.code != 0:
        raise RuntimeError("playwright install chromium 失败（镜像与官方源均失败）")


def run_refresh_auth(profile_dir: str, headless: bool, max_wait: int) -> None:
    cmd = [
        sys.executable,
        "wechat_crawler.py",
        "--refresh-auth",
        "--refresh-auth-only",
        "--refresh-profile-dir",
        profile_dir,
        "--refresh-max-wait",
        str(max_wait),
    ]
    if headless:
        cmd.append("--refresh-headless")
    target_url = os.environ.get("WECHAT_REFRESH_TARGET_URL")
    if target_url:
        cmd.extend(["--refresh-target-url", target_url])
    print("\n[引导] 浏览器将被打开，用于获取最新 token/cookie")
    print("[引导] 1) 如果出现二维码：请扫码登录（可能需要手机二次确认）")
    print("[引导] 2) 登录成功后：停留在公众号后台任意页面即可（建议点击一次『图文消息』或『素材管理』）")
    print("[引导] 3) 脚本会在抓到 token 后自动写回 config.json，然后继续后续步骤\n")
    res = _run_live(cmd, timeout=max_wait + 60)
    if res.code != 0:
        raise RuntimeError("刷新 token/cookie 失败")


def run_extract_latest(account: str) -> None:
    cmd = [
        sys.executable,
        "wechat_crawler.py",
        "--extract-latest",
        "--account",
        account,
        "--no-save-markdown",
    ]
    res = _run_live(cmd)
    if res.code != 0:
        raise RuntimeError("抓取最新文章失败")


def run_push_latest_all(accounts_file: str = "", force: bool = False) -> None:
    cmd = [
        sys.executable,
        "wechat_crawler.py",
        "--push-latest-all",
        "--no-save-markdown",
    ]
    if accounts_file:
        cmd.extend(["--accounts-file", accounts_file])
    if force:
        cmd.append("--force")
    res = _run_live(cmd)
    if res.code != 0:
        raise RuntimeError("抓取并推送公众号清单最新文章失败")


def _check_pkg_installed(pkg: str) -> bool:
    """检查Python包是否已安装"""
    try:
        __import__(pkg)
        return True
    except ImportError:
        return False


import re

def _check_all_deps_installed(requirements_path: Path) -> bool:
    """检查requirements.txt中的所有依赖是否已安装"""
    try:
        content = requirements_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 提取包名（去除版本号）
            pkg = re.split(r'[=<>!~]', line)[0].strip()
            if not pkg:
                continue
            if not _check_pkg_installed(pkg):
                return False
        return True
    except Exception:
        return False


def main() -> None:
    root = Path(__file__).resolve().parent
    requirements_path = root / "requirements.txt"
    if not requirements_path.exists():
        raise RuntimeError("未找到 requirements.txt")

    profile_dir = os.environ.get("WECHAT_PROFILE_DIR", "./my_wechat_profile")
    headless = os.environ.get("WECHAT_HEADLESS", "0") == "1"
    max_wait = int(os.environ.get("WECHAT_REFRESH_MAX_WAIT", "600"))
    run_mode = os.environ.get("WECHAT_RUN_MODE", "extract-latest").strip().lower()
    skip_install = os.environ.get("WECHAT_SKIP_INSTALL", "0") == "1"
    force_push = os.environ.get("WECHAT_FORCE_PUSH", "0") == "1"
    force_refresh = os.environ.get("WECHAT_FORCE_REFRESH", "0") == "1"
    account = os.environ.get("WECHAT_ACCOUNT")
    accounts_file = os.environ.get("WECHAT_ACCOUNTS_FILE", "").strip()
    if not account:
        cfg_path = root / "config.json"
        if cfg_path.exists():
            try:
                import json

                account = (json.loads(cfg_path.read_text(encoding="utf-8")) or {}).get(
                    "target_account_name"
                )
            except Exception:
                account = None
    if run_mode == "extract-latest" and not account:
        raise RuntimeError("缺少公众号名称：设置 WECHAT_ACCOUNT 或在 config.json 里填 target_account_name")

    # 检查是否需要安装依赖
    if skip_install:
        print("[INFO] 跳过安装步骤 (WECHAT_SKIP_INSTALL=1)")
    else:
        # 检查Python依赖
        if _check_all_deps_installed(requirements_path):
            print("[INFO] Python依赖已安装，跳过pip install")
        else:
            print("step: pip install")
            pip_install_with_fallback(requirements_path)

        # 检查Playwright
        if _check_pkg_installed("playwright"):
            print("[INFO] Playwright已安装，跳过playwright install chromium")
        else:
            print("step: playwright install chromium")
            playwright_install_chromium_with_fallback()

    # 判断是否需要刷新 token
    need_refresh = True
    cfg_path = root / "config.json"
    if force_refresh:
        need_refresh = True
        print("[INFO] 强制刷新 token/cookie (WECHAT_FORCE_REFRESH=1)")
    elif headless and run_mode != "refresh-only" and cfg_path.exists():
        try:
            import json
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
            if cfg.get("token") and cfg.get("cookie"):
                need_refresh = False
                print(f"[INFO] headless 模式且已有有效 token，跳过刷新（上一次: {cfg_path.stat().st_mtime_fmt if hasattr(cfg_path.stat(), 'st_mtime_fmt') else ''}）")
        except Exception:
            pass

    if need_refresh:
        print("step: refresh auth")
        run_refresh_auth(profile_dir=profile_dir, headless=headless, max_wait=max_wait)

    if run_mode == "extract-latest":
        print("step: extract latest")
        run_extract_latest(account=account)

    if run_mode == "push-latest-all":
        print("step: push latest all" + (" (force mode)" if force_push else ""))
        run_push_latest_all(accounts_file=accounts_file, force=force_push)

    if run_mode == "refresh-only":
        return


if __name__ == "__main__":
    main()

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cookies_to_header(cookies: List[Dict[str, Any]]) -> str:
    pairs: List[str] = []
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _extract_token_from_url(url: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
        token = (qs.get("token") or [""])[0]
        if token and re.fullmatch(r"\d+", token):
            return token
        return ""
    except Exception:
        return ""


def _extract_getmsg_params(url: str) -> Dict[str, Any]:
    try:
        qs = parse_qs(urlparse(url).query)
        def first(name: str) -> str:
            return (qs.get(name) or [""])[0]
        return {
            "__biz": first("__biz"),
            "uin": first("uin"),
            "key": first("key"),
            "pass_ticket": first("pass_ticket"),
            "full_url": url,
            "captured_at": int(time.time()),
        }
    except Exception:
        return {"full_url": url, "captured_at": int(time.time())}


async def refresh_wechat_auth(
    config_path: str = "config.json",
    profile_dir: str = "./my_wechat_profile",
    headless: bool = False,
    target_url: Optional[str] = None,
    params_output_path: str = "wechat_params.json",
    max_wait_seconds: int = 90,
) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    config_file = Path(config_path)
    params_file = Path(params_output_path)

    latest = {
        "token": "",
        "cookie": "",
        "getmsg": {},
        "profile_dir": str(profile_dir),
        "updated_at": int(time.time()),
    }

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page = await context.new_page()

            if not headless:
                print("浏览器已启动：如果出现二维码请扫码登录；登录后在公众号后台任意页面停留即可。")

            def handle_request(request):
                url = request.url
                token = _extract_token_from_url(url)
                if token:
                    latest["token"] = token
                if "getmsg" in url and "__biz" in url:
                    latest["getmsg"] = _extract_getmsg_params(url)

            page.on("request", handle_request)

            urls = []
            if target_url:
                urls.append(target_url)
            urls.extend(
                [
                    "https://mp.weixin.qq.com/",
                    "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN",
                ]
            )

            last_error = None
            for u in urls:
                try:
                    await page.goto(u, wait_until="domcontentloaded")
                    last_error = None
                    break
                except Exception as e:
                    last_error = e

            if last_error:
                raise last_error

            if not headless:
                print(f"当前页面: {page.url}")

            deadline = time.time() + max_wait_seconds
            last_hint_at = 0.0
            last_auto_click_at = 0.0

            async def auto_trigger_token() -> None:
                nonlocal last_auto_click_at
                if time.time() - last_auto_click_at < 3:
                    return
                last_auto_click_at = time.time()
                try:
                    links = await page.query_selector_all("a[href*='token=']")
                    for a in links[:5]:
                        try:
                            await a.click(timeout=800)
                            return
                        except Exception:
                            continue
                except Exception:
                    return

            while time.time() < deadline:
                if not latest["token"]:
                    token = _extract_token_from_url(page.url)
                    if token:
                        latest["token"] = token

                if latest["token"]:
                    break

                if not headless:
                    await auto_trigger_token()

                if not headless and time.time() - last_hint_at >= 5:
                    last_hint_at = time.time()
                    print(
                        "等待抓取 token...（已登录后请点击左侧菜单任意页面，让地址栏/请求出现 token=...）"
                    )

                await asyncio.sleep(0.25)

            if not latest["token"]:
                raise RuntimeError(
                    "未获取到 token：请确认已登录公众号后台，并点击左侧菜单任意页面，使地址栏出现 token=..."
                )

            cookies = await context.cookies()
            latest["cookie"] = _cookies_to_header(cookies)

        finally:
            await context.close()

    existing = _load_json(config_file)
    if latest["token"]:
        existing["token"] = latest["token"]
    if latest["cookie"]:
        existing["cookie"] = latest["cookie"]
    _atomic_write_json(config_file, existing)
    _atomic_write_json(params_file, latest)
    return latest


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--config", type=str, default="config.json")
    parser.add_argument("--profile-dir", type=str, default="./my_wechat_profile")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--target-url", type=str, default=None)
    parser.add_argument("--params-out", type=str, default="wechat_params.json")
    parser.add_argument("--max-wait", type=int, default=90)
    args = parser.parse_args()

    latest = asyncio.run(
        refresh_wechat_auth(
            config_path=args.config,
            profile_dir=args.profile_dir,
            headless=args.headless,
            target_url=args.target_url,
            params_output_path=args.params_out,
            max_wait_seconds=args.max_wait,
        )
    )
    token = latest.get("token") or ""
    cookie = latest.get("cookie") or ""
    print(f"token: {token[:8]}{'...' if len(token) > 8 else ''}")
    print(f"cookie_len: {len(cookie)}")


if __name__ == "__main__":
    main()

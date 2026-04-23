import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse


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
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != "mp.weixin.qq.com":
            return ""
        if "/cgi-bin/" not in (parsed.path or ""):
            return ""
        qs = parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        if token and re.fullmatch(r"\d+", token):
            try:
                v = int(token)
            except Exception:
                return ""
            if v < 10000:
                return ""
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


async def _try_accept_agreement(page) -> bool:
    try:
        url = page.url or ""
        if "mp.weixin.qq.com" not in url:
            return False
        markers = ["agree", "agreement", "protocol", "license", "service", "contract", "terms"]
        looks_like = any(m in url for m in markers)
        if not looks_like:
            content = ""
            try:
                content = (await page.content())[:6000]
            except Exception:
                content = ""
            looks_like = ("协议" in content) or ("同意" in content) or ("服务条款" in content)
        if not looks_like:
            return False

        targets = [page]
        try:
            for f in page.frames:
                targets.append(f)
        except Exception:
            pass

        async def run_on(target) -> bool:
            try:
                try:
                    await target.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass

                try:
                    el = await target.query_selector("input[type='checkbox']")
                    if el:
                        try:
                            await el.check(timeout=800)
                        except Exception:
                            try:
                                await el.click(timeout=800)
                            except Exception:
                                pass
                except Exception:
                    pass

                selectors = [
                    "button:has-text('我已阅读并同意')",
                    "button:has-text('阅读并同意')",
                    "button:has-text('我同意')",
                    "button:has-text('同意')",
                    "button:has-text('接受')",
                    "button:has-text('继续')",
                    "button:has-text('下一步')",
                    "a:has-text('同意')",
                    "a:has-text('接受')",
                    "a:has-text('继续')",
                ]
                for sel in selectors:
                    try:
                        el = await target.query_selector(sel)
                        if not el:
                            continue
                        try:
                            await el.click(timeout=1500)
                        except Exception:
                            try:
                                await el.dispatch_event("click")
                            except Exception:
                                continue
                        await asyncio.sleep(0.8)
                        return True
                    except Exception:
                        continue
                return False
            except Exception:
                return False

        for t in targets:
            if await run_on(t):
                return True
        return False
    except Exception:
        return False


async def _dump_debug(page, debug_dir: str, logs: List[str]) -> None:
    try:
        out = Path(debug_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = out / stamp

        try:
            (base.with_suffix(".url.txt")).write_text(page.url or "", encoding="utf-8")
        except Exception:
            pass

        try:
            html = await page.content()
            (base.with_suffix(".html")).write_text(html, encoding="utf-8")
        except Exception:
            pass

        try:
            await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        except Exception:
            pass

        try:
            if logs:
                (base.with_suffix(".console.txt")).write_text("\n".join(logs[-300:]), encoding="utf-8")
        except Exception:
            pass
    except Exception:
        return


async def refresh_wechat_auth(
    config_path: str = "config.json",
    profile_dir: str = "./my_wechat_profile",
    headless: bool = False,
    target_url: Optional[str] = None,
    params_output_path: str = "wechat_params.json",
    max_wait_seconds: int = 90,
    debug_dir: Optional[str] = None,
    keep_open_on_fail: bool = False,
    keep_open: bool = False,
    keep_open_seconds: int = 120,
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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=DownloadBubble,DownloadBubbleV2",
                "--disable-download-notification",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        logs: List[str] = []
        failed = False
        page = None

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            if not headless:
                print("浏览器已启动：如果出现二维码请扫码登录；登录后在公众号后台任意页面停留即可。")

            def handle_console(msg):
                try:
                    logs.append(f"[{msg.type}] {msg.text}")
                except Exception:
                    pass

            def handle_request(request):
                url = request.url
                token = _extract_token_from_url(url)
                if token:
                    latest["token"] = token
                if "getmsg" in url and "__biz" in url:
                    latest["getmsg"] = _extract_getmsg_params(url)

            page.on("request", handle_request)
            page.on("console", handle_console)

            def handle_popup(popup):
                async def _handle() -> None:
                    try:
                        await popup.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    try:
                        await _try_accept_agreement(popup)
                    except Exception:
                        pass
                    try:
                        await asyncio.sleep(0.2)
                        await popup.close()
                    except Exception:
                        pass
                asyncio.create_task(_handle())

            page.on("popup", handle_popup)

            urls = []
            if target_url:
                urls.append(target_url)
            urls.extend(
                [
                    "https://mp.weixin.qq.com/",
                    "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN",
                    "https://mp.weixin.qq.com/cgi-bin/loginpage?t=wxm2-login&lang=zh_CN",
                    "https://mp.weixin.qq.com/cgi-bin/bizlogin?action=startlogin&lang=zh_CN",
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
            last_debug_at = 0.0

            async def auto_trigger_token() -> None:
                nonlocal last_auto_click_at
                if time.time() - last_auto_click_at < 3:
                    return
                last_auto_click_at = time.time()
                try:
                    links = await page.query_selector_all("a[href*='token=']")
                    for a in links[:5]:
                        try:
                            href = await a.get_attribute("href")
                            if not href:
                                continue
                            await page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
                            return
                        except Exception:
                            continue
                except Exception:
                    return

            while time.time() < deadline:
                agreed = await _try_accept_agreement(page)
                if agreed and not headless:
                    try:
                        await asyncio.sleep(0.6)
                        print(f"已尝试同意协议，当前页面: {page.url}")
                    except Exception:
                        pass

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

                if debug_dir and time.time() - last_debug_at >= 6:
                    last_debug_at = time.time()
                    await _dump_debug(page, debug_dir, logs)

                await asyncio.sleep(0.25)

            if not latest["token"]:
                raise RuntimeError(
                    "未获取到 token：请确认已登录公众号后台，并点击左侧菜单任意页面，使地址栏出现 token=..."
                )

            cookies = await context.cookies()
            latest["cookie"] = _cookies_to_header(cookies)

            if keep_open and (not headless):
                try:
                    print(f"已获取到 token/cookie，浏览器将保持打开 {keep_open_seconds} 秒")
                    await asyncio.sleep(max(1, int(keep_open_seconds)))
                except Exception:
                    pass

        except Exception:
            failed = True
            if debug_dir:
                try:
                    if page is not None:
                        await _dump_debug(page, debug_dir, logs)
                    print(f"已保存调试信息到: {debug_dir}")
                except Exception:
                    pass
            if keep_open_on_fail and (not headless):
                try:
                    print(f"发生错误，浏览器将保持打开 {keep_open_seconds} 秒，便于手动扫码/复制地址")
                    await asyncio.sleep(max(1, int(keep_open_seconds)))
                except Exception:
                    pass
            raise
        finally:
            if not (keep_open_on_fail and failed and (not headless)):
                try:
                    pages = context.pages
                    for p0 in pages:
                        try:
                            p0.remove_listener("request", handle_request)
                            await p0.close()
                        except Exception:
                            pass
                    await asyncio.sleep(1.0)
                    try:
                        await context.close()
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

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
    parser.add_argument("--debug-dir", type=str, default=None)
    parser.add_argument("--keep-open-on-fail", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--keep-open-seconds", type=int, default=120)
    args = parser.parse_args()

    latest = asyncio.run(
        refresh_wechat_auth(
            config_path=args.config,
            profile_dir=args.profile_dir,
            headless=args.headless,
            target_url=args.target_url,
            params_output_path=args.params_out,
            max_wait_seconds=args.max_wait,
            debug_dir=args.debug_dir,
            keep_open_on_fail=args.keep_open_on_fail,
            keep_open=args.keep_open,
            keep_open_seconds=args.keep_open_seconds,
        )
    )
    token = latest.get("token") or ""
    cookie = latest.get("cookie") or ""
    print(f"token: {token[:8]}{'...' if len(token) > 8 else ''}")
    print(f"cookie_len: {len(cookie)}")


if __name__ == "__main__":
    main()

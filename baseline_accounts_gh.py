import json
import os
import re
import sys
import time

sys.path.insert(0, ".")

from wechat_crawler import (
    ACCOUNTS_JSON_FILE,
    CONFIG_FILE,
    get_articles,
    is_valid_article_link,
    load_json,
    resolve_fakeid,
    save_json,
    update_accounts_json_from_names,
)


def _extract_biz_and_gh(html: str):
    biz = None
    gh = None
    m = re.search(r'var\s+biz\s*=\s*"([A-Za-z0-9_=]+)"', html)
    if m:
        biz = m.group(1)
    m = re.search(r'var\s+user_name\s*=\s*"([^"]+)"', html)
    if m:
        gh = m.group(1)
    return biz, gh


def main() -> int:
    update_accounts_json_from_names()

    cfg = load_json(CONFIG_FILE)
    token = (cfg.get("token") or "").strip()
    cookie = (cfg.get("cookie") or "").strip()
    if not token or not cookie:
        print("错误: config.json 中缺少 token 或 cookie")
        return 1

    data = load_json(ACCOUNTS_JSON_FILE)
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        print("错误: accounts.json 格式不正确")
        return 1

    updated = 0
    skipped = 0
    failed = 0

    import requests

    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    for it in accounts:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or it.get("account") or "").strip()
        if not name:
            continue
        if (it.get("gh") or "").strip() and (it.get("biz") or "").strip() and (it.get("fakeid") or "").strip():
            skipped += 1
            continue

        fakeid = (it.get("fakeid") or "").strip()
        if not fakeid:
            fakeid = resolve_fakeid(name, token, cookie, target_fakeid=None)
            if fakeid:
                it["fakeid"] = fakeid

        if not fakeid:
            failed += 1
            continue

        try:
            arts, _, _ = get_articles(fakeid, token, cookie, begin=0, count=20)
        except Exception:
            failed += 1
            continue

        link = ""
        for a in arts:
            lnk = a.get("link")
            if is_valid_article_link(lnk):
                link = lnk
                break
        if not link:
            failed += 1
            continue

        try:
            before_gh = (it.get("gh") or "").strip()
            before_biz = (it.get("biz") or "").strip()

            resp = requests.get(link, headers={"User-Agent": ua}, timeout=20)
            resp.encoding = "utf-8"
            biz, gh = _extract_biz_and_gh(resp.text or "")
            if biz:
                it["biz"] = biz
            if gh:
                it["gh"] = gh
            after_gh = (it.get("gh") or "").strip()
            after_biz = (it.get("biz") or "").strip()
            if (after_gh and after_gh != before_gh) or (after_biz and after_biz != before_biz):
                updated += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            continue

        time.sleep(0.2)

    save_json(ACCOUNTS_JSON_FILE, {"accounts": accounts})
    print(json.dumps({"updated": updated, "skipped": skipped, "failed": failed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

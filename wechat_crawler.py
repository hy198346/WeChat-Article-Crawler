import requests
import json
import time
import os
import re
import argparse
import asyncio
import sys

# 配置和数据文件路径
CONFIG_FILE = "config.json"
FAKEID_FILE = "gzh.txt"
ACCOUNT_NAMES_FILE = "公众号名字"
HISTORY_FILE = "history.json"
OUTPUT_FILE = "wx_poc.txt"
ARTICLES_BASE_DIR = "公众号文章"
ACCOUNTS_JSON_FILE = "accounts.json"
PUSH_STATE_FILE = "push_state.json"

def _parse_grouped_account_names(text: str):
    group = "未分组"
    out = []
    for raw in (text or "").splitlines():
        s = (raw or "").strip()
        if not s:
            continue
        m = re.match(r"^(.+?公众号)\s*[:：]\s*$", s)
        if m:
            group = m.group(1).strip() or group
            continue
        out.append({"name": s, "group": group})
    return out

def _load_grouped_account_names_from_file():
    if not os.path.exists(ACCOUNT_NAMES_FILE):
        return []
    with open(ACCOUNT_NAMES_FILE, "r", encoding="utf-8") as f:
        return _parse_grouped_account_names(f.read())

def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_json(filepath, data):
    # 保存 JSON 时保留中文
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_fakeids():
    if not os.path.exists(FAKEID_FILE):
        return []
    with open(FAKEID_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_account_names():
    if not os.path.exists(ACCOUNT_NAMES_FILE):
        return {}
    items = _load_grouped_account_names_from_file()
    names = [it.get("name") for it in items if (it.get("name") or "").strip()]
    return {i: name for i, name in enumerate(names)}

def update_accounts_json_from_names():
    """
    从公众号名字文件更新accounts.json文件
    确保accounts.json总是包含最新的公众号名称
    """
    if not os.path.exists(ACCOUNT_NAMES_FILE):
        return False
    
    # 从公众号名字文件读取最新的名称列表
    items = _load_grouped_account_names_from_file()
    names = [(it.get("name") or "").strip() for it in items if (it.get("name") or "").strip()]
    group_by_name = {(it.get("name") or "").strip(): (it.get("group") or "未分组").strip() for it in items if (it.get("name") or "").strip()}
    
    if not names:
        return False
    
    # 读取现有的accounts.json文件
    existing_accounts = []
    if os.path.exists(ACCOUNTS_JSON_FILE):
        existing_accounts = _load_accounts_from_json(ACCOUNTS_JSON_FILE)
    
    # 创建一个字典，用于快速查找现有账号信息（保留 fakeid 以及其他扩展字段）
    existing_by_name = {}
    for acc in existing_accounts:
        if not isinstance(acc, dict):
            continue
        name = (acc.get("name") or acc.get("account") or "").strip()
        if not name:
            continue
        existing_by_name[name] = acc
    
    # 构建新的accounts列表
    new_accounts = []
    for name in names:
        old = existing_by_name.get(name, {}) if isinstance(existing_by_name.get(name), dict) else {}
        fakeid = (old.get("fakeid") or "").strip()
        group_new = (group_by_name.get(name, "未分组") or "未分组").strip()
        group_old = (old.get("group") or "").strip()
        group = group_old if (group_new == "未分组" and group_old and group_old != "未分组") else group_new

        item = {"name": name, "fakeid": fakeid, "group": group}
        skip_keys = set(item.keys()) | {"account"}
        for k, v in old.items():
            if k in skip_keys:
                continue
            item[k] = v
        new_accounts.append(item)
    
    # 保存到accounts.json文件
    save_json(ACCOUNTS_JSON_FILE, {"accounts": new_accounts})
    return True

def _format_publish_times(ts: int):
    try:
        t = int(ts or 0)
    except Exception:
        t = 0
    if t <= 0:
        return {"date": "Unknown", "published_at": "Unknown"}
    return {
        "date": time.strftime("%Y-%m-%d", time.localtime(t)),
        "published_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(t)),
    }

def get_headers(cookie, token):
    return {
        "Host": "mp.weixin.qq.com",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
        "Cookie": cookie,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=10&token={token}&lang=zh_CN",
        "Origin": "https://mp.weixin.qq.com",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

def get_articles(fakeid, token, cookie, begin=0, count=5):
    url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
    headers = get_headers(cookie, token)
    
    params = {
        "sub": "list",
        "begin": str(begin),
        "count": str(count),
        "fakeid": fakeid,
        "token": token,
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        if "base_resp" in data and data["base_resp"]["ret"] != 0:
            print(f"API Error: {data['base_resp']}")
            return [], 0, None
        
        # publish_page 是一个 JSON 字符串，需要再次解析
        if "publish_page" in data:
            publish_page = json.loads(data["publish_page"])
            publish_list = publish_page.get("publish_list", [])
            total_count = publish_page.get("total_count", 0)
            
            # 从 publish_list 中提取所有文章
            articles = []
            for publish_item in publish_list:
                publish_info = json.loads(publish_item.get("publish_info", "{}"))
                appmsg_info = publish_info.get("appmsg_info", [])
                for appmsg in appmsg_info:
                    articles.append({
                        "title": appmsg.get("title"),
                        "link": appmsg.get("content_url"),
                        "create_time": publish_info.get("sent_info", {}).get("time", 0),
                        "digest": appmsg.get("digest", ""),
                        "author": appmsg.get("author", "")
                    })
            
            return articles, total_count, None
        else:
            print("未找到 publish_page 字段")
            return [], 0, None
            
    except Exception as e:
        print(f"请求失败: {e}")
        return [], 0, None

def search_accounts(query, token, cookie, begin=0, count=5):
    url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
    headers = get_headers(cookie, token)
    params = {
        "action": "search_biz",
        "query": query,
        "begin": str(begin),
        "count": str(count),
        "token": token,
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if "base_resp" in data and data["base_resp"].get("ret", 0) != 0:
            print(f"API Error: {data['base_resp']}")
            return []
        return data.get("list", [])
    except Exception as e:
        print(f"搜索公众号失败: {e}")
        return []

def resolve_fakeid(target_account_name, token, cookie, target_fakeid=None):
    if target_fakeid:
        return target_fakeid
    if not target_account_name:
        return None

    candidates = search_accounts(target_account_name, token, cookie, begin=0, count=10)
    if not candidates:
        return None

    exact = None
    for item in candidates:
        nickname = (item.get("nickname") or "").strip()
        if nickname == target_account_name.strip():
            exact = item
            break

    chosen = exact if exact else candidates[0]
    return chosen.get("fakeid")

def fetch_article_markdown(article, headers, account_name=None):
    url = article.get("link")
    title = article.get("title")
    digest = article.get("digest", "")
    create_time = article.get("create_time")
    
    # 先尝试从 API 获取时间
    times = _format_publish_times(create_time)
    date_str = times["date"]
    published_at = times["published_at"]

    resp = requests.get(url, headers=headers)
    resp.encoding = "utf-8"
    content_html = resp.text

    # 如果 API 没有返回时间，尝试从 HTML 中提取
    if date_str == "Unknown" and published_at == "Unknown":
        print(f"开始从 HTML 中提取时间: {url}")
        # 1. 尝试从脚本标签中提取时间戳（如 "publish_time":1774580390）
        # 使用更灵活的正则表达式，匹配各种可能的格式
        timestamp_patterns = [
            r'publish_time\s*[:=]\s*(\d+)',  # publish_time: 1234567890 或 publish_time=1234567890
            r'"publish_time"\s*:\s*(\d+)',  # "publish_time": 1234567890
            r'\'publish_time\'\s*:\s*(\d+)',  # 'publish_time': 1234567890
            r'date\s*[:=]\s*(\d+)',  # date: 1234567890 或 date=1234567890
            r'"date"\s*:\s*(\d+)',  # "date": 1234567890
            r'\'date\'\s*:\s*(\d+)',  # 'date': 1234567890
        ]
        
        timestamp_found = False
        for pattern in timestamp_patterns:
            timestamp_match = re.search(pattern, content_html)
            if timestamp_match:
                try:
                    timestamp = int(timestamp_match.group(1))
                    print(f"找到时间戳: {timestamp} (匹配模式: {pattern})")
                    times = _format_publish_times(timestamp)
                    date_str = times["date"]
                    published_at = times["published_at"]
                    print(f"解析时间成功: {published_at}")
                    timestamp_found = True
                    break
                except Exception as e:
                    print(f"解析时间戳失败: {e}")
                    continue
        
        # 如果没有找到时间戳，尝试查找日期字符串
        if not timestamp_found:
            print("未找到时间戳，尝试查找日期字符串")
            # 尝试匹配常见的日期格式
            date_patterns = [
                r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}',  # 2023-01-01 12:34
                r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}',  # 2023/01/01 12:34
                r'\d{4}年\d{2}月\d{2}日\s+\d{2}:\d{2}',  # 2023年01月01日 12:34
                r'\d{4}-\d{2}-\d{2}',  # 2023-01-01
                r'\d{4}/\d{2}/\d{2}',  # 2023/01/01
                r'\d{4}年\d{2}月\d{2}日',  # 2023年01月01日
            ]
            
            for pattern in date_patterns:
                date_match = re.search(pattern, content_html)
                if date_match:
                    try:
                        date_str = date_match.group(0)
                        print(f"找到日期字符串: {date_str} (匹配模式: {pattern})")
                        # 尝试解析日期字符串
                        for fmt in [
                            '%Y-%m-%d %H:%M',
                            '%Y/%m/%d %H:%M',
                            '%Y年%m月%d日 %H:%M',
                            '%Y-%m-%d',
                            '%Y/%m/%d',
                            '%Y年%m月%d日',
                        ]:
                            try:
                                time_obj = time.strptime(date_str, fmt)
                                timestamp = time.mktime(time_obj)
                                times = _format_publish_times(int(timestamp))
                                date_str = times["date"]
                                published_at = times["published_at"]
                                print(f"解析日期成功: {published_at}")
                                break
                            except ValueError:
                                continue
                        break
                    except Exception as e:
                        print(f"解析日期失败: {e}")
                        continue
        
        # 3. 如果仍然没有提取到时间，尝试从URL中提取（某些公众号会在URL中包含时间）
        if date_str == "Unknown" and published_at == "Unknown":
            print("未找到日期字符串，尝试从URL中提取时间")
            url_time_pattern = r'\d{8}'  # 匹配URL中的8位数字日期
            url_match = re.search(url_time_pattern, url)
            if url_match:
                try:
                    date_str = url_match.group(0)
                    print(f"从URL中找到日期: {date_str}")
                    time_obj = time.strptime(date_str, '%Y%m%d')
                    timestamp = time.mktime(time_obj)
                    times = _format_publish_times(int(timestamp))
                    date_str = times["date"]
                    published_at = times["published_at"]
                    print(f"解析URL日期成功: {published_at}")
                except Exception as e:
                    print(f"解析URL日期失败: {e}")
                    pass
        
        # 4. 如果所有方法都失败，使用当前日期作为默认值
        if date_str == "Unknown" and published_at == "Unknown":
            print("所有时间提取方法都失败，使用当前日期")
            current_time = int(time.time())
            times = _format_publish_times(current_time)
            date_str = times["date"]
            published_at = times["published_at"]
            print(f"使用当前日期: {published_at}")

    folder_name = account_name if account_name else "Unknown_Account"
    if folder_name == "Unknown_Account":
        nick_match = re.search(r'var nickname = "([^"]+)"', content_html)
        if nick_match:
            folder_name = nick_match.group(1)
        elif "profile_meta_nickname" in content_html:
            nick_match_2 = re.search(r'class="profile_meta_value">([^<]+)<', content_html)
            if nick_match_2:
                folder_name = nick_match_2.group(1).strip()

    content_match = re.search(r'<div[^>]*id="js_content"[^>]*>(.*?)</div>', content_html, re.DOTALL)
    if content_match:
        main_content = content_match.group(1)
    else:
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content_html, re.DOTALL)
        main_content = body_match.group(1) if body_match else content_html

    markdown_content = f"# {title}\n\n"
    markdown_content += f"**Date:** {published_at}\n"
    markdown_content += f"**Link:** {url}\n"
    markdown_content += f"**Account:** {folder_name}\n"
    if digest:
        markdown_content += f"**Summary:** {digest}\n"
    markdown_content += "\n"
    markdown_content += html_to_markdown(main_content)

    return {
        "title": title,
        "url": url,
        "date": date_str,
        "published_at": published_at,
        "account": folder_name,
        "markdown": markdown_content
    }

def _get_serverchan_sendkey(config, override_sendkey=None):
    if override_sendkey:
        return override_sendkey.strip()
    env_key = os.environ.get("SERVERCHAN_SENDKEY")
    if env_key:
        return env_key.strip()
    if isinstance(config, dict):
        k = config.get("serverchan_sendkey")
        if k:
            return str(k).strip()
    return None

def send_serverchan_message(sendkey, title, desp, timeout=20):
    if not sendkey:
        return {"ok": False, "error": "missing_sendkey"}
    api = f"https://sctapi.ftqq.com/{sendkey}.send"
    try:
        resp = requests.post(api, data={"title": title, "desp": desp}, timeout=timeout)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        return {"ok": True, "response": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def build_serverchan_markdown(article_info):
    title = article_info.get("title") or "(无标题)"
    url = article_info.get("url") or ""
    account = article_info.get("account") or ""
    published_at = article_info.get("published_at") or article_info.get("date") or ""
    display_title = f"[{published_at}] {title}".strip()
    lines = [
        f"**公众号：** {account}",
        f"**标题：** {display_title}",
        "",
        f"[阅读全文]({url})" if url else "",
        ""
    ]
    return "\n".join([l for l in lines if l is not None])

def push_article_to_serverchan(config, article_info, override_sendkey=None):
    sendkey = _get_serverchan_sendkey(config, override_sendkey=override_sendkey)
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    published_at = article_info.get("published_at") or article_info.get("date") or ""
    msg_title = f"{article_info.get('account') or ''} [{published_at}] {article_info.get('title') or ''}".strip()
    desp = build_serverchan_markdown(article_info)
    return send_serverchan_message(sendkey, msg_title, desp)

def _load_accounts_from_json(path: str):
    data = load_json(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return data["accounts"]
    return []

def load_accounts_list(config, accounts_file_override: str = None):
    # 先从公众号名字文件更新accounts.json
    update_accounts_json_from_names()
    
    candidates = []
    if accounts_file_override:
        candidates.append(accounts_file_override)
    if isinstance(config, dict) and config.get("accounts_file"):
        candidates.append(str(config.get("accounts_file")))
    if os.path.exists(ACCOUNTS_JSON_FILE):
        candidates.append(ACCOUNTS_JSON_FILE)

    for p in candidates:
        if p and os.path.exists(p):
            items = _load_accounts_from_json(p)
            out = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or it.get("account") or "").strip()
                fakeid = (it.get("fakeid") or "").strip()
                latest_url = (it.get("latest_url") or it.get("article_url") or "").strip()
                group = (it.get("group") or "").strip()
                if not name and not fakeid:
                    continue
                obj = {"name": name, "fakeid": fakeid}
                if group:
                    obj["group"] = group
                if latest_url:
                    obj["latest_url"] = latest_url
                out.append(obj)
            if out:
                return out

    fakeids = load_fakeids()
    account_names = load_account_names()
    out = []
    for idx, fid in enumerate(fakeids):
        out.append({"name": account_names.get(idx, ""), "fakeid": fid})
    return out

def _extract_latest_payload_for_account(fakeid: str, account_name: str, token: str, cookie: str, headers):
    articles, _, _ = get_articles(fakeid, token, cookie, begin=0, count=20)
    if not articles:
        return None

    chosen = None
    for a in articles:
        if not is_valid_article_link(a.get("link")):
            continue
        chosen = a
        break
    if not chosen:
        return None

    inferred_account = (account_name or "").strip()
    # 先尝试从 API 获取时间
    times = _format_publish_times(chosen.get("create_time"))
    date_str = times["date"]
    published_at = times["published_at"]
    
    # 如果 API 没有返回时间，尝试从 HTML 中提取
    if date_str == "Unknown" and published_at == "Unknown":
        try:
            print(f"尝试从 HTML 中提取时间和账号名称: {inferred_account or 'Unknown'}")
            fetched = fetch_article_markdown(chosen, headers, account_name=inferred_account)
            if fetched:
                inferred_account = (fetched.get("account") or inferred_account).strip()
                # 使用 fetch_article_markdown 提取的时间
                date_str = fetched.get("date", "Unknown")
                published_at = fetched.get("published_at", "Unknown")
                print(f"成功提取时间: {published_at}")
        except Exception as e:
            print(f"从 HTML 提取时间失败: {e}")
            # 保留原始的账号名称
            pass
    else:
        # API 返回了时间，只提取账号名称
        if not inferred_account:
            try:
                fetched = fetch_article_markdown(chosen, headers, account_name=None)
                inferred_account = (fetched.get("account") or "").strip()
            except Exception:
                # 保留空的账号名称
                pass

    return {
        "account": inferred_account or account_name or "Unknown_Account",
        "fakeid": fakeid,
        "title": chosen.get("title") or "(无标题)",
        "date": date_str,
        "published_at": published_at,
        "url": chosen.get("link") or "",
        "_raw_article": chosen,
    }

def build_serverchan_markdown_articles(articles):
    lines = []
    groups = {}
    group_rank = {}
    for a in articles:
        g = (a.get("group") or "未分组").strip()
        if g not in groups:
            groups[g] = []
            group_rank[g] = len(group_rank)
        groups[g].append(a)

    for g in groups:
        groups[g].sort(key=lambda x: x.get("published_at") or x.get("date") or "", reverse=True)

    for g in sorted(groups.keys(), key=lambda x: group_rank.get(x, 999)):
        lines.append(f"### {g}")
        for a in groups[g]:
            account = a.get("account") or ""
            title = a.get("title") or "(无标题)"
            published_at = a.get("published_at") or a.get("date") or ""
            url = a.get("url") or ""
            label = f"{account} [{published_at}] {title}".strip()
            if url:
                lines.append(f"- [{label}]({url})")
            else:
                lines.append(f"- {label}")
        lines.append("")

    return "\n".join([l for l in lines if l is not None]).rstrip()

def push_articles_to_serverchan(config, articles, override_sendkey=None):
    sendkey = _get_serverchan_sendkey(config, override_sendkey=override_sendkey)
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    title = f"公众号最新文章（{len(articles)}篇）"
    desp = build_serverchan_markdown_articles(articles)
    return send_serverchan_message(sendkey, title, desp)

def run_push_latest_all(
    config,
    accounts_file=None,
    push_state_file=None,
    save_markdown=True,
    serverchan_sendkey=None,
    push=True,
    force=False,
    push_separately=False,
):
    token = config.get("token")
    cookie = config.get("cookie")
    if not token or not cookie:
        raise ValueError("config.json 中缺少 token 或 cookie")

    headers = get_headers(cookie, token)
    accounts = load_accounts_list(config, accounts_file_override=accounts_file)
    if not accounts:
        raise RuntimeError("公众号清单为空：请配置 accounts.json 或填写 gzh.txt/公众号名字")

    state_path = push_state_file or (config.get("push_state_file") if isinstance(config, dict) else None) or PUSH_STATE_FILE
    state = load_json(state_path) if state_path else {}
    if not isinstance(state, dict):
        state = {}

    changed_articles = []
    per_account_payloads = []
    group_rank = {}
    for it in accounts:
        g = (it.get("group") or "未分组").strip()
        if g not in group_rank:
            group_rank[g] = len(group_rank)

    for it in accounts:
        name = (it.get("name") or "").strip()
        fakeid = (it.get("fakeid") or "").strip()
        latest_url = (it.get("latest_url") or "").strip()
        group = (it.get("group") or "未分组").strip()
        if latest_url:
            fakeid = ""
        elif not fakeid:
            fakeid = resolve_fakeid(name, token, cookie, target_fakeid=None)
        if not fakeid:
            if latest_url:
                state_key = f"name:{name}" if name else f"url:{latest_url}"
                headers_public = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                fetched = fetch_article_markdown(
                    {"title": "Unknown", "link": latest_url, "create_time": 0, "digest": "", "author": ""},
                    headers_public,
                    account_name=name or None,
                )
                url = fetched.get("url") or latest_url
                last = state.get(state_key, {}) if isinstance(state.get(state_key), dict) else {}
                last_url = last.get("last_pushed_url")
                if (not force) and last_url and last_url == url:
                    continue
                changed_articles.append(
                    {
                        "account": fetched.get("account") or name or "Unknown_Account",
                        "title": fetched.get("title") or "(无标题)",
                        "date": fetched.get("date", "Unknown"),
                        "published_at": fetched.get("published_at", fetched.get("date", "Unknown")),
                        "url": url,
                        "fakeid": state_key,
                        "group": group,
                    }
                )
                per_account_payloads.append({"account": name, "fakeid": state_key, "url": url})
                continue

            print(f"[Skip] 无法解析 fakeid：{name}")
            continue

        payload = _extract_latest_payload_for_account(fakeid=fakeid, account_name=name, token=token, cookie=cookie, headers=headers)
        if not payload or not payload.get("url"):
            print(f"[Skip] 未获取到文章：{name or fakeid}")
            continue

        last = state.get(fakeid, {}) if isinstance(state.get(fakeid), dict) else {}
        last_url = last.get("last_pushed_url")
        if (not force) and last_url and last_url == payload["url"]:
            continue

        changed_articles.append({
            "account": payload["account"],
            "title": payload["title"],
            "date": payload["date"],
            "published_at": payload["published_at"],
            "url": payload["url"],
            "fakeid": fakeid,
            "group": group,
        })
        per_account_payloads.append(payload)

    if changed_articles:
        grouped = {}
        for a in changed_articles:
            g = (a.get("group") or "未分组").strip()
            grouped.setdefault(g, []).append(a)
        ordered = []
        for g in sorted(grouped.keys(), key=lambda x: group_rank.get(x, 999)):
            grouped[g].sort(key=lambda x: x.get("published_at") or x.get("date") or "", reverse=True)
            ordered.extend(grouped[g])
        changed_articles = ordered

    push_result = None
    pushed_fakeids = set()
    if push and changed_articles:
        if push_separately:
            results = []
            for a in changed_articles:
                res = push_article_to_serverchan(config, a, override_sendkey=serverchan_sendkey)
                results.append(res)
            for a, r in zip(changed_articles, results):
                if r and r.get("ok") and (not r.get("skipped")) and a.get("fakeid"):
                    pushed_fakeids.add(a["fakeid"])
            push_result = {"ok": True, "mode": "separate", "results": results}
        else:
            push_result = push_articles_to_serverchan(config, changed_articles, override_sendkey=serverchan_sendkey)
            if push_result and push_result.get("ok") and (not push_result.get("skipped")):
                for a in changed_articles:
                    if a.get("fakeid"):
                        pushed_fakeids.add(a["fakeid"])
    if push and (not changed_articles):
        push_result = {"ok": True, "skipped": True, "reason": "no_change"}

    if save_markdown and per_account_payloads:
        for p in per_account_payloads:
            raw = p.get("_raw_article")
            if raw:
                save_url_to_md(raw, headers, account_name=p.get("account"))

    if pushed_fakeids:
        now_ts = int(time.time())
        for a in changed_articles:
            fid = a.get("fakeid")
            if (not fid) or (fid not in pushed_fakeids):
                continue
            state[fid] = {
                "last_pushed_url": a.get("url"),
                "last_pushed_title": a.get("title"),
                "last_pushed_published_at": a.get("published_at"),
                "updated_at": now_ts,
            }
        if state_path:
            save_json(state_path, state)

    payload_out = {
        "count": len(changed_articles),
        "articles": changed_articles,
        "serverchan": push_result if push else {"ok": False, "skipped": True, "reason": "no_push"},
        "push_state_file": state_path,
    }
    print(json.dumps(payload_out, ensure_ascii=False, indent=2))
    return payload_out

def run_extract_latest(config, account_name_arg=None, fakeid_arg=None, save_markdown=True, output_json_path=None, serverchan_sendkey=None, push=True):
    token = config.get("token")
    cookie = config.get("cookie")
    if not token or not cookie:
        raise ValueError("config.json 中缺少 token 或 cookie")

    target_account_name = account_name_arg or config.get("target_account_name")
    target_fakeid = fakeid_arg or config.get("target_fakeid")
    fakeid = resolve_fakeid(target_account_name, token, cookie, target_fakeid=target_fakeid)
    if not fakeid:
        raise ValueError("无法解析 fakeid，请检查 target_account_name/target_fakeid 或 token/cookie")

    headers = get_headers(cookie, token)
    articles, _, _ = get_articles(fakeid, token, cookie, begin=0, count=20)
    if not articles:
        raise RuntimeError("未获取到文章列表")

    best = None
    best_fetched = None
    any_valid = False

    for a in articles:
        if not is_valid_article_link(a.get("link")):
            continue
        any_valid = True
        fetched = fetch_article_markdown(a, headers, account_name=target_account_name)
        if best is None:
            best = a
            best_fetched = fetched

    if not any_valid:
        raise RuntimeError("获取到的文章链接均已失效")

    chosen = best
    fetched = best_fetched
    payload = {
        "account": fetched["account"],
        "title": fetched["title"],
        "date": fetched["date"],
        "published_at": fetched.get("published_at") or fetched["date"],
        "url": fetched["url"]
    }

    if push:
        push_result = push_article_to_serverchan(config, payload, override_sendkey=serverchan_sendkey)
        payload["serverchan"] = push_result

    if save_markdown:
        save_url_to_md(chosen, headers, account_name=target_account_name)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload

def run_extract_from_url(article_url, account_name=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    article = {"title": "Unknown", "link": article_url, "create_time": 0, "digest": "", "author": ""}
    fetched = fetch_article_markdown(article, headers, account_name=account_name)
    payload = {
        "account": fetched["account"],
        "title": fetched["title"],
        "date": fetched["date"],
        "published_at": fetched.get("published_at") or fetched["date"],
        "url": fetched["url"]
    }

    if push:
        push_result = push_article_to_serverchan({}, payload, override_sendkey=serverchan_sendkey)
        payload["serverchan"] = push_result

    if save_markdown:
        save_url_to_md(article, headers, account_name=account_name)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload

def is_valid_article_link(link):
    """
    判断文章链接是否有效
    包含 tempkey= 的链接说明文章已删除或失效
    """
    if not link:
        return False
    # 检查是否包含 tempkey= 参数（说明文章已失效）
    if 'tempkey=' in link:
        return False
    return True

def clean_filename(title):
    # 去除非法字符
    return re.sub(r'[\\/*?:"<>|]', "", title).strip()

def html_to_markdown(html):
    """
    Simple Regex-based HTML to Markdown converter.
    """
    # Remove style and script
    html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
    
    # Extract images: <img ... data-src="..."> or <img ... src="...">
    # Do this BEFORE removing any tags
    def replace_img(match):
        src = match.group(1) or match.group(2)
        return f"\n![]({src})\n"
    
    # Replace img tags with markdown images
    html = re.sub(r'<img[^>]+data-src="([^"]+)"[^>]*>', replace_img, html)
    html = re.sub(r'<img[^>]+src="([^"]+)"[^>]*>', replace_img, html)
    
    # Handle code blocks - <pre><code>...</code></pre> or <pre>...</pre>
    def replace_pre_code(match):
        code_content = match.group(1)
        # Remove inner <code> tags if present
        code_content = re.sub(r'<code[^>]*>(.*?)</code>', r'\1', code_content, flags=re.DOTALL)
        # Decode HTML entities in code
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&nbsp;', ' ')
        return f"\n```\n{code_content}\n```\n"
    
    html = re.sub(r'<pre[^>]*>(.*?)</pre>', replace_pre_code, html, flags=re.DOTALL)
    
    # Handle inline code - <code>...</code>
    html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL)
    
    # Remove lines that only contain HTML attributes (common in WeChat articles)
    html = re.sub(r'^\s*(class|data-|style|width|height|type|from|wx_fmt|data-ratio|data-type|data-w|data-imgfileid|data-aistatus|data-s)=[^>]*>\s*$', '', html, flags=re.MULTILINE)
    
    # Headers
    for i in range(6, 0, -1):
        html = re.sub(f'<h{i}[^>]*>(.*?)</h{i}>', '#' * i + r' \1\n', html)
        
    # Paragraphs and Breaks
    html = re.sub(r'<p[^>]*>', '\n', html)
    html = re.sub(r'</p>', '\n', html)
    html = re.sub(r'<br\s*/?>', '\n', html)
    
    # Bold/Strong
    html = re.sub(r'<(b|strong)[^>]*>(.*?)</\1>', r'**\2**', html)
    
    # Lists (Simple)
    html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', html)
    
    # Remove all remaining tags (including self-closing)
    html = re.sub(r'<[^>]+>', '', html)
    
    # Decode entities (basic)
    html = html.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
    
    # Collapse multiple newlines and spaces
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = re.sub(r' +', ' ', html)
    
    return html.strip()

def save_url_to_md(article, headers, account_name=None):
    url = article.get("link")
    title = article.get("title")
    digest = article.get("digest", "")
    
    # 先尝试从 API 获取时间
    times = _format_publish_times(article.get("create_time"))
    date_str = times["date"]
    published_at = times["published_at"]

    if not url:
        return

    try:
        # Fetch article content
        resp = requests.get(url, headers=headers)
        resp.encoding = "utf-8"
        content_html = resp.text
        
        # 如果 API 没有返回时间，尝试从 HTML 中提取
        if date_str == "Unknown" and published_at == "Unknown":
            # 1. 尝试从脚本标签中提取时间戳（如 "publish_time":1774580390）
            timestamp_pattern = r'"publish_time"\s*:\s*(\d+)'  # 匹配时间戳格式
            timestamp_match = re.search(timestamp_pattern, content_html)
            if timestamp_match:
                try:
                    timestamp = int(timestamp_match.group(1))
                    times = _format_publish_times(timestamp)
                    date_str = times["date"]
                    published_at = times["published_at"]
                except Exception:
                    pass
            
            # 2. 如果没有找到时间戳，尝试其他格式
            if date_str == "Unknown" and published_at == "Unknown":
                # 匹配多种时间格式
                time_patterns = [
                    # 直接的发布时间变量
                    r'publish_time\s*=\s*["\']([^"\']+)["\']',
                    r'var\s+publish_time\s*=\s*["\']([^"\']+)["\']',
                    r'date\s*=\s*["\']([^"\']+)["\']',
                    r'var\s+date\s*=\s*["\']([^"\']+)["\']',
                    
                    # 富媒体元数据中的时间
                    r'<span[^>]*class=["\']rich_media_meta[^>]*["\']>([^<]+)</span>',
                    r'<span[^>]*class=["\']publish_time[^>]*["\']>([^<]+)</span>',
                    r'<span[^>]*class=["\']time[^>]*["\']>([^<]+)</span>',
                    
                    # 各种时间格式
                    r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}',  # 2023-01-01 12:34
                    r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}',  # 2023/01/01 12:34
                    r'\d{4}年\d{2}月\d{2}日\s+\d{2}:\d{2}',  # 2023年01月01日 12:34
                    r'\d{2}月\d{2}日\s+\d{2}:\d{2}',  # 01月01日 12:34
                    r'\d{4}-\d{2}-\d{2}',  # 2023-01-01
                    r'\d{4}/\d{2}/\d{2}',  # 2023/01/01
                    r'\d{4}年\d{2}月\d{2}日',  # 2023年01月01日
                ]
                
                for pattern in time_patterns:
                    match = re.search(pattern, content_html)
                    if match:
                        time_str = match.group(1).strip()
                        # 尝试解析时间字符串
                        try:
                            # 尝试不同的时间格式
                            for fmt in [
                                '%Y-%m-%d %H:%M',
                                '%Y/%m/%d %H:%M',
                                '%Y年%m月%d日 %H:%M',
                                '%Y-%m-%d',
                                '%Y/%m/%d',
                                '%Y年%m月%d日',
                                '%m月%d日 %H:%M',  # 处理没有年份的情况，使用当前年份
                            ]:
                                try:
                                    if fmt == '%m月%d日 %H:%M':
                                        # 没有年份，使用当前年份
                                        current_year = time.strftime('%Y')
                                        full_time_str = f'{current_year}年{time_str}'
                                        time_obj = time.strptime(full_time_str, '%Y年%m月%d日 %H:%M')
                                    else:
                                        time_obj = time.strptime(time_str, fmt)
                                    timestamp = time.mktime(time_obj)
                                    times = _format_publish_times(int(timestamp))
                                    date_str = times["date"]
                                    published_at = times["published_at"]
                                    break
                                except ValueError:
                                    continue
                            break
                        except Exception:
                            continue
                
                # 3. 如果仍然没有提取到时间，尝试从URL中提取（某些公众号会在URL中包含时间）
                if date_str == "Unknown" and published_at == "Unknown":
                    url_time_pattern = r'\d{8}'  # 匹配URL中的8位数字日期
                    url_match = re.search(url_time_pattern, url)
                    if url_match:
                        date_str = url_match.group(0)
                        try:
                            time_obj = time.strptime(date_str, '%Y%m%d')
                            timestamp = time.mktime(time_obj)
                            times = _format_publish_times(int(timestamp))
                            date_str = times["date"]
                            published_at = times["published_at"]
                        except Exception:
                            pass
        
        # Use provided account name or try to extract from HTML
        folder_name = account_name if account_name else "Unknown_Account"
        
        if folder_name == "Unknown_Account":
            # Try to extract from HTML var nickname
            nick_match = re.search(r'var nickname = "([^"]+)"', content_html)
            if nick_match:
                folder_name = nick_match.group(1)
            elif "profile_meta_nickname" in content_html:
                nick_match_2 = re.search(r'class="profile_meta_value">([^<]+)<', content_html)
                if nick_match_2:
                    folder_name = nick_match_2.group(1).strip()

        # Create base directory if not exists
        if not os.path.exists(ARTICLES_BASE_DIR):
            os.makedirs(ARTICLES_BASE_DIR)
        
        # Create account subdirectory
        safe_account_folder = clean_filename(folder_name)
        account_dir = os.path.join(ARTICLES_BASE_DIR, safe_account_folder)
        if not os.path.exists(account_dir):
            os.makedirs(account_dir)
            
        safe_title = clean_filename(title)
        filename = os.path.join(account_dir, f"{date_str}_{safe_title}.md")
        
        if os.path.exists(filename):
            print(f"  [Jump] File exists: {filename}")
            return

        # Convert to Markdown
        # Only extract the main content container: id="js_content"
        main_content = ""
        content_match = re.search(r'<div[^>]*id="js_content"[^>]*>(.*?)</div>', content_html, re.DOTALL)
        
        if content_match:
             main_content = content_match.group(1)
        else:
             # Fallback: parsing might be complex, use whole response body
             main_content = re.search(r'<body[^>]*>(.*?)</body>', content_html, re.DOTALL).group(1) if re.search(r'<body', content_html) else content_html

        markdown_content = f"# {title}\n\n"
        markdown_content += f"**Date:** {published_at}\n"
        markdown_content += f"**Link:** {url}\n"
        markdown_content += f"**Account:** {folder_name}\n"
        if digest:
            markdown_content += f"**Summary:** {digest}\n"
        markdown_content += "\n"
        markdown_content += html_to_markdown(main_content)
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        
        # Check file size and delete if too small
        config = load_json(CONFIG_FILE)
        min_file_size_kb = config.get("min_file_size_kb", 3)
        min_file_size_bytes = min_file_size_kb * 1024
        
        file_size = os.path.getsize(filename)
        if file_size < min_file_size_bytes:
            print(f"  [Delete] File too small ({file_size} bytes): {filename}")
            os.remove(filename)
        else:
            print(f"  [Saved] {filename} ({file_size} bytes)")
        
        time.sleep(1)

    except Exception as e:
        print(f"  [Error] Failed to save {title}: {e}")

def load_account_latest_articles():
    """
    从 wx_poc.txt 中读取每个公众号的最新文章链接
    返回字典: {公众号名称: 最新文章链接}
    """
    account_latest = {}
    if not os.path.exists(OUTPUT_FILE):
        return account_latest
    
    current_account = None
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("文章名字："):
                # 提取公众号名称（从文件名中）
                title = line.replace("文章名字：", "")
                # 尝试从文件名中提取公众号名
                # 格式类似: 公众号文章/公众号名/日期_标题.md
                # 但这里只有标题，无法直接获取
                # 我们需要另一种方式
                pass
            elif line.startswith("文章链接："):
                link = line.replace("文章链接：", "")
                if current_account:
                    account_latest[current_account] = link
                    current_account = None
    
    return account_latest

def load_account_first_article_from_txt():
    """
    从 wx_poc.txt 中读取每个公众号的第一篇文章链接
    返回字典: {公众号名称: 第一篇文章链接}
    """
    account_first_articles = {}
    if not os.path.exists(OUTPUT_FILE):
        return account_first_articles
    
    current_account = None
    first_article_link = None
    
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("公众号："):
                current_account = line.replace("公众号：", "")
                first_article_link = None
            elif line.startswith("第一篇文章链接：") and current_account:
                first_article_link = line.replace("第一篇文章链接：", "")
                account_first_articles[current_account] = first_article_link
    
    return account_first_articles
def mode_archive(fakeids, token, cookie, account_names):
    """存档模式：爬取所有文章"""
    print("--- 启动存档模式 ---")
    headers = get_headers(cookie, token)
    
    # Load existing links from wx_poc.txt to avoid duplicates
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("文章链接："):
                    link = line.strip().replace("文章链接：", "")
                    existing_links.add(link)
    
    # Load first articles from wx_poc.txt for comparison
    account_first_articles = load_account_first_article_from_txt()
    print(f"已加载 {len(account_first_articles)} 个公众号的第一篇文章记录")
    
    # Create base directory if not exists
    if not os.path.exists(ARTICLES_BASE_DIR):
        os.makedirs(ARTICLES_BASE_DIR)
    
    for idx, fakeid in enumerate(fakeids):
        account_name = account_names.get(idx, "Unknown_Account")
        print(f"正在处理 fakeid: {fakeid} ({account_name})")
        
        # Get first article to check if already archived
        articles_first, _, _ = get_articles(fakeid, token, cookie, 0, 1)
        if articles_first:
            first_article_link = articles_first[0].get('link')
            
            # Check if this account has archived articles in wx_poc.txt
            if account_name in account_first_articles:
                archived_first_link = account_first_articles[account_name]
                if first_article_link == archived_first_link:
                    print(f"  [Skip] 公众号第一篇文章已存档，跳过: {account_name}")
                    continue
                else:
                    print(f"  [New] 发现新内容，开始爬取: {account_name}")
            else:
                print(f"  [New] 首次爬取，开始处理: {account_name}")
        
        begin = 0
        count = 10
        should_stop = False
        account_articles = []
        
        while not should_stop:
            articles, total, _ = get_articles(fakeid, token, cookie, begin, count)
            if not articles:
                print(f"  没有更多文章或获取失败")
                break
                
            print(f"  获取到 {len(articles)} 篇文章 (当前进度: {begin})")
            
            for article in articles:
                link = article.get('link')
                # Check if this article is already archived in wx_poc.txt
                if account_name in account_first_articles:
                    if link == account_first_articles[account_name]:
                        print(f"  [Stop] 找到已存档文章，停止爬取: {article.get('title')}")
                        should_stop = True
                        break
                # Skip invalid articles (deleted or expired)
                if not is_valid_article_link(link):
                    print(f"  [Skip] 文章已失效，跳过: {article.get('title')}")
                    continue
                # Only collect if not already archived
                if link not in existing_links:
                    account_articles.append(article)
            
            if should_stop:
                break
                
            if len(articles) < count:
                print("  已到达最后一页")
                break
                
            begin += count
            time.sleep(3)
        
        # Save to txt with account header
        if account_articles:
            # Filter out invalid articles before saving
            valid_articles = [a for a in account_articles if is_valid_article_link(a.get('link'))]
            
            if valid_articles:
                with open(OUTPUT_FILE, "a+", encoding="utf-8") as f:
                    f.write("=" * 60 + "\n")
                    f.write(f"公众号：{account_name}\n")
                    f.write(f"文章数量：{len(valid_articles)}篇\n")
                    f.write(f"第一篇文章：{valid_articles[0].get('title')}\n")
                    f.write(f"第一篇文章链接：{valid_articles[0].get('link')}\n")
                    f.write("=" * 60 + "\n")
                    for article in valid_articles:
                        f.write(f"文章名字：{article.get('title')}\n")
                        f.write(f"文章链接：{article.get('link')}\n")
                        f.write("-" * 50 + "\n")
                        existing_links.add(article.get('link'))
                
                # Save to Markdown (only valid articles)
                for article in valid_articles:
                    save_url_to_md(article, headers, account_name)

def mode_update(fakeids, token, cookie, history, account_names):
    """更新模式：增量爬取"""
    print("--- 启动更新模式 ---")
    headers = get_headers(cookie, token)
    
    # Load existing links to avoid duplicates
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("文章链接："):
                    link = line.strip().replace("文章链接：", "")
                    existing_links.add(link)
    
    for idx, fakeid in enumerate(fakeids):
        account_name = account_names.get(idx, "Unknown_Account")
        print(f"正在检查 fakeid: {fakeid} ({account_name})")
        
        last_article_info = history.get(fakeid, {})
        last_title = last_article_info.get("last_article_title")
        
        begin = 0
        count = 10
        new_articles = []
        found_overlap = False
        
        while not found_overlap:
            articles, total, _ = get_articles(fakeid, token, cookie, begin, count)
            if not articles:
                break
                
            for article in articles:
                title = article.get("title")
                link = article.get('link')
                
                if title == last_title:
                    print(f"  找到上次最后更新的文章: {title}，停止本号更新")
                    found_overlap = True
                    break
                
                # Skip invalid articles (deleted or expired)
                if not is_valid_article_link(link):
                    print(f"  [Skip] 文章已失效，跳过: {title}")
                    continue
                
                new_articles.append(article)
            
            if len(articles) < count or found_overlap:
                break
                
            begin += count
            time.sleep(3)
            
        if new_articles:
            # Filter out invalid articles
            valid_articles = [a for a in new_articles if is_valid_article_link(a.get('link'))]
            
            if valid_articles:
                print(f"  发现 {len(valid_articles)} 篇新文章")
                
                # Save to txt log with account header (new format)
                with open(OUTPUT_FILE, "a+", encoding="utf-8") as f:
                    f.write("=" * 60 + "\n")
                    f.write(f"公众号：{account_name}\n")
                    f.write(f"文章数量：{len(valid_articles)}篇\n")
                    f.write(f"第一篇文章：{valid_articles[0].get('title')}\n")
                    f.write(f"第一篇文章链接：{valid_articles[0].get('link')}\n")
                    f.write("=" * 60 + "\n")
                    for article in valid_articles:
                        f.write(f"文章名字：{article.get('title')}\n")
                        f.write(f"文章链接：{article.get('link')}\n")
                        f.write("-" * 50 + "\n")
                        existing_links.add(article.get('link'))
                
                # Process new articles (Save to MD)
                for article in valid_articles:
                    save_url_to_md(article, headers, account_name)
                
                # Update history with the NEWEST article
                newest = valid_articles[0]
                history[fakeid] = {
                    "last_article_title": newest.get("title"),
                    "last_article_url": newest.get("link")
                }
            else:
                print("  发现的新文章均已失效")
        else:
            print("  无新文章")

    save_json(HISTORY_FILE, history)

def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--refresh-auth", action="store_true")
    parser.add_argument("--refresh-auth-only", action="store_true")
    parser.add_argument("--refresh-profile-dir", type=str, default="./my_wechat_profile")
    parser.add_argument("--refresh-headless", action="store_true")
    parser.add_argument("--refresh-target-url", type=str, default=None)
    parser.add_argument("--refresh-max-wait", type=int, default=90)
    parser.add_argument("--refresh-debug-dir", type=str, default=None)
    parser.add_argument("--refresh-keep-open-on-fail", action="store_true")
    parser.add_argument("--refresh-keep-open", action="store_true")
    parser.add_argument("--refresh-keep-open-seconds", type=int, default=120)
    parser.add_argument("--extract-latest", action="store_true")
    parser.add_argument("--push-latest-all", action="store_true")
    parser.add_argument("--article-url", type=str, default=None)
    parser.add_argument("--account", type=str, default=None)
    parser.add_argument("--fakeid", type=str, default=None)
    parser.add_argument("--accounts-file", type=str, default=None)
    parser.add_argument("--push-state-file", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--push-separately", action="store_true")
    parser.add_argument("--no-save-markdown", action="store_true")
    parser.add_argument("--output-json", type=str, default=None)
    parser.add_argument("--serverchan-sendkey", type=str, default=None)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    if not args.serverchan_sendkey:
        args.serverchan_sendkey = os.environ.get("SERVERCHAN_SENDKEY")

    config = load_json(CONFIG_FILE)
    if (not config) and os.path.exists("config.json.example"):
        config = load_json("config.json.example")

    if args.refresh_auth:
        try:
            from wechat_auth_updater import refresh_wechat_auth
        except Exception as e:
            print(f"错误: 未能导入 Playwright 更新模块: {e}")
            print("请先安装依赖: pip install -r requirements.txt && playwright install chromium")
            sys.exit(1)

        try:
            asyncio.run(
                refresh_wechat_auth(
                    config_path=CONFIG_FILE,
                    profile_dir=args.refresh_profile_dir,
                    headless=args.refresh_headless,
                    target_url=args.refresh_target_url,
                    max_wait_seconds=args.refresh_max_wait,
                    debug_dir=args.refresh_debug_dir,
                    keep_open_on_fail=args.refresh_keep_open_on_fail,
                    keep_open=args.refresh_keep_open,
                    keep_open_seconds=args.refresh_keep_open_seconds,
                )
            )
            config = load_json(CONFIG_FILE)
        except Exception as e:
            print(f"错误: 自动更新 token/cookie 失败: {e}")
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass
            if args.refresh_keep_open_on_fail and (not args.refresh_headless):
                try:
                    print(f"将等待 {args.refresh_keep_open_seconds} 秒再退出，便于查看/扫码")
                    time.sleep(max(1, int(args.refresh_keep_open_seconds)))
                except Exception:
                    pass
            sys.exit(1)

        token = config.get("token")
        cookie = config.get("cookie")
        if not token or not cookie:
            print("错误: 自动更新未获取到 token 或 cookie")
            sys.exit(1)

        if args.refresh_auth_only:
            return

    if args.article_url:
        run_extract_from_url(
            args.article_url,
            account_name=args.account,
            save_markdown=not args.no_save_markdown,
            output_json_path=args.output_json,
            serverchan_sendkey=args.serverchan_sendkey,
            push=not args.no_push
        )
        return

    if args.extract_latest:
        run_extract_latest(
            config,
            account_name_arg=args.account,
            fakeid_arg=args.fakeid,
            save_markdown=not args.no_save_markdown,
            output_json_path=args.output_json,
            serverchan_sendkey=args.serverchan_sendkey,
            push=not args.no_push
        )
        return

    if args.push_latest_all:
        run_push_latest_all(
            config,
            accounts_file=args.accounts_file,
            push_state_file=args.push_state_file,
            save_markdown=not args.no_save_markdown,
            serverchan_sendkey=args.serverchan_sendkey,
            push=not args.no_push,
            force=args.force,
            push_separately=args.push_separately,
        )
        return

    token = config.get("token")
    cookie = config.get("cookie")
    if not token or not cookie:
        print("错误: config.json 中缺少 token 或 cookie")
        return

    check_interval_minutes = config.get("check_interval_minutes", 60)
    check_interval_seconds = check_interval_minutes * 60

    print("启动持续监控模式...")
    print(f"每{check_interval_minutes}分钟检查一次公众号更新\n")

    while True:
        try:
            # 每次循环重新读取公众号列表
            fakeids = load_fakeids()
            if not fakeids:
                print("错误: gzh.txt 为空或不存在")
                return
            print(f"加载了 {len(fakeids)} 个公众号")

            account_names = load_account_names()
            print(f"加载了 {len(account_names)} 个公众号名称")

            print(f"\n{'='*60}")
            print(f"开始检查更新 - {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}\n")

            mode_archive(fakeids, token, cookie, account_names)

            print(f"\n{'='*60}")
            print(f"检查完成 - {time.strftime('%Y-%m-%d %H:%M:%S')}")
            next_check_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + check_interval_seconds))
            print(f"下次检查时间: {next_check_time}")
            print(f"{'='*60}\n")

            print(f"等待{check_interval_minutes}分钟后进行下一次检查...")
            time.sleep(check_interval_seconds)

        except KeyboardInterrupt:
            print("\n\n监控已停止")
            break
        except Exception as e:
            print(f"\n发生错误: {e}")
            retry_interval_minutes = config.get("retry_interval_minutes", 5)
            print(f"{retry_interval_minutes}分钟后重试...")
            time.sleep(retry_interval_minutes * 60)

if __name__ == "__main__":
    main()

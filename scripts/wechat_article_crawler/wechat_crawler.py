import requests
import json
import time
import os
import re
import argparse
import asyncio
import sys
import hashlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from .article_analysis import (
        analyze_single_article,
        build_article_id,
        build_analysis_index_html,
        get_analysis_config,
        _normalize_article_id,
        persist_batch_analysis_outputs,
        persist_single_analysis_outputs,
        summarize_analysis_batch,
    )
except ImportError:
    from article_analysis import (
        analyze_single_article,
        build_article_id,
        build_analysis_index_html,
        get_analysis_config,
        _normalize_article_id,
        persist_batch_analysis_outputs,
        persist_single_analysis_outputs,
        summarize_analysis_batch,
    )


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name == "wechat_article_crawler" and current.parent.parent.name == "scripts":
        return current.parents[2]
    return current.parent


REPO_ROOT = _repo_root()
OUTPUT_ROOT = REPO_ROOT / "output"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

_ASYNC_JOB_DISPATCH_MODE = "thread"


def _parse_env_file(path: Path):
    values = {}
    try:
        if not path.exists():
            return values
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = (raw or "").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = (key or "").strip()
            if not key:
                continue
            values[key] = (value or "").strip().strip("'").strip('"').strip()
    except OSError:
        return {}
    return values


def _load_env_into_process(root: Path):
    env_file = str(os.environ.get("WECHAT_ENV_FILE") or "").strip()
    target = Path(env_file) if env_file else (root / ".env")
    for key, value in _parse_env_file(target).items():
        os.environ.setdefault(key, value)
    return target if target.exists() else None

# 配置和数据文件路径
CONFIG_FILE = str(REPO_ROOT / "config.json")
FAKEID_FILE = str(REPO_ROOT / "gzh.txt")
ACCOUNT_NAMES_FILE = str(REPO_ROOT / "公众号名字")
HISTORY_FILE = str(OUTPUT_ROOT / "history.json")
OUTPUT_FILE = str(OUTPUT_ROOT / "wx_poc.txt")
ARTICLES_BASE_DIR = str(OUTPUT_ROOT / "公众号文章")
ACCOUNTS_JSON_FILE = str(REPO_ROOT / "accounts.json")
PUSH_STATE_FILE = str(OUTPUT_ROOT / "push_state.json")

def _ts_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def _refresh_analysis_index_html(config):
    try:
        build_analysis_index_html(config)
    except Exception as exc:
        print(f"[{_ts_now()}] WARN refresh analysis index html failed: {type(exc).__name__}:{exc}")
        raise


def _copy_config_with_forced_reanalyze(config):
    copied = dict(config or {})
    copied["analysis_skip_if_exists"] = False
    return copied


def _normalize_effective_account_name(value):
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "unknown_account":
        return ""
    if lowered.startswith("gh_"):
        return ""
    return text


def _resolve_reanalyze_api_path(config) -> str:
    cfg = get_analysis_config(config or {})
    path = str(cfg.get("analysis_reanalyze_path") or "").strip() or "/api/reanalyze"
    return path if path.startswith("/") else f"/{path}"


def _is_trusted_local_reanalyze_source(headers, config=None) -> bool:
    if headers is None:
        return False
    trusted_hosts = {"127.0.0.1", "localhost", "::1"}
    public_base_url = str(get_analysis_config(config or {}).get("analysis_public_base_url") or "").strip()
    if public_base_url:
        try:
            parsed_public = urlparse(public_base_url)
            public_host = (parsed_public.hostname or "").lower()
            if public_host:
                trusted_hosts.add(public_host)
        except Exception:
            pass
    for header_name in ("Origin", "Referer"):
        raw = ""
        if hasattr(headers, "get"):
            raw = str(headers.get(header_name) or "").strip()
        if not raw:
            continue
        try:
            parsed = urlparse(raw)
        except Exception:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if host in trusted_hosts:
            return True
    return False


def _load_cached_analysis_by_article_id(config, article_id):
    text = _normalize_article_id(article_id)
    if not text:
        return None
    cache_path = Path(get_analysis_config(config).get("analysis_output_dir") or "output") / "article_analysis" / f"{text}.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _resolve_reanalyze_account_name(config, article_id=None, payload_account=None, fallback_account=None):
    for candidate in (payload_account, fallback_account):
        normalized = _normalize_effective_account_name(candidate)
        if normalized:
            return normalized
    cached = _load_cached_analysis_by_article_id(config, article_id)
    if isinstance(cached, dict):
        normalized = _normalize_effective_account_name(cached.get("account"))
        if normalized:
            return normalized
    return ""


def _is_allowed_reanalyze_url(url):
    text = str(url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return False
    if (parsed.netloc or "").lower() != "mp.weixin.qq.com":
        return False
    path = (parsed.path or "").strip()
    if not path:
        return False
    if not (path == "/s" or path.startswith("/s/")):
        return False
    return is_valid_article_link(text)


def _detect_wechat_article_access_error(text: str) -> str:
    body = str(text or "")
    if not body:
        return ""
    if _looks_like_wechat_login_html(body):
        return "wechat_auth_required"
    if _looks_like_wechat_security_verification_html(body):
        return "wechat_security_verification_required"
    return ""


def _merge_fetched_fields_into_analysis(analysis, fetched):
    if not isinstance(analysis, dict) or analysis.get("status") != "ok":
        return analysis, False
    updated = dict(analysis)
    changed = False
    fetched_published_at = fetched.get("published_at") or fetched.get("date") or ""
    normalized_account = _normalize_effective_account_name(fetched.get("account"))
    field_pairs = {
        "title": fetched.get("title") or "",
        "url": fetched.get("url") or "",
        "published_at": fetched_published_at,
        "date": fetched.get("date") or "",
    }
    if normalized_account:
        field_pairs["account"] = normalized_account
    for field, value in field_pairs.items():
        if value and updated.get(field) != value:
            updated[field] = value
            changed = True
    return updated, changed

def _emit_auth_expired(reason: str, detail: str = ""):
    msg = f"[{_ts_now()}] WECHAT_AUTH_EXPIRED reason={reason}"
    if detail:
        msg += f" detail={detail}"
    print(msg)

def _looks_like_wechat_login_html(text: str) -> bool:
    if not text:
        return False
    if "使用微信扫一扫" in text:
        return True
    if "扫码登录" in text:
        return True
    if "微信公众平台" in text and ("登录" in text or "安全验证" in text):
        return True
    s = text.lower()
    if "mp.weixin.qq.com/cgi-bin/login" in s:
        return True
    if "cgi-bin/login" in s and "mp.weixin.qq.com" in s:
        return True
    if "js_login" in s and "mp.weixin" in s:
        return True
    return False


def _looks_like_wechat_security_verification_html(text: str) -> bool:
    if not text:
        return False
    s = text.lower()
    if 'id="js_content"' in s or "id='js_content'" in s:
        return False
    strong_markers = (
        "完成安全验证后继续访问",
        "需完成安全验证后继续访问",
        "访问过于频繁",
        "当前环境异常",
    )
    if any(marker in text for marker in strong_markers):
        return True
    keyword_hits = sum(1 for marker in ("安全验证", "访问过于频繁", "环境异常") if marker in text)
    if keyword_hits < 2:
        return False
    page_markers = ("<title>环境异常", "<title>安全验证", "wx-errcode", "weui-msg", "请选择正常环境打开")
    return any(marker in text or marker in s for marker in page_markers)

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


def _extract_title_from_html(content_html, fallback="Unknown"):
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<h1[^>]+id=["\']activity-name["\'][^>]*>(.*?)</h1>',
        r'<title>(.*?)</title>',
    ]
    for pattern in patterns:
        match = re.search(pattern, content_html, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if title:
            return title
    return fallback or "Unknown"


def _extract_account_name_from_html(content_html, fallback="Unknown_Account"):
    patterns = [
        r'id="js_name"[^>]*>([^<]+)<',
        r'var nickname = "([^"]+)"',
        r'class="profile_meta_value">([^<]+)<',
        r'class="rich_media_meta_text"[^>]*>([^<]+)<',
        r'var user_name = "([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, content_html, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        name = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if name:
            return name
    return fallback or "Unknown_Account"

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
        ct = (response.headers.get("Content-Type") or "").lower()
        if "application/json" not in ct:
            body = response.text or ""
            if _looks_like_wechat_login_html(body):
                _emit_auth_expired("non_json_login_page", f"status={response.status_code} content_type={ct}")
                return [], 0, "auth_expired"
        try:
            data = response.json()
        except Exception as e:
            body = response.text or ""
            if _looks_like_wechat_login_html(body):
                _emit_auth_expired("json_decode_login_page", str(e))
                return [], 0, "auth_expired"
            print(f"请求失败: {e}")
            return [], 0, None
        
        if "base_resp" in data and data["base_resp"]["ret"] != 0:
            base = data["base_resp"] or {}
            err_msg = str(base.get("err_msg") or "").lower()
            if ("invalid session" in err_msg) or ("invalid" in err_msg and "session" in err_msg):
                try:
                    _emit_auth_expired("base_resp_invalid_session", json.dumps(base, ensure_ascii=False))
                except Exception:
                    _emit_auth_expired("base_resp_invalid_session")
            print(f"API Error: {base}")
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
        ct = (response.headers.get("Content-Type") or "").lower()
        if "application/json" not in ct:
            body = response.text or ""
            if _looks_like_wechat_login_html(body):
                _emit_auth_expired("non_json_login_page_searchbiz", f"status={response.status_code} content_type={ct}")
                return []
        try:
            data = response.json()
        except Exception as e:
            body = response.text or ""
            if _looks_like_wechat_login_html(body):
                _emit_auth_expired("json_decode_login_page_searchbiz", str(e))
                return []
            print(f"搜索公众号失败: {e}")
            return []
        if "base_resp" in data and data["base_resp"].get("ret", 0) != 0:
            base = data["base_resp"] or {}
            err_msg = str(base.get("err_msg") or "").lower()
            if ("invalid session" in err_msg) or ("invalid" in err_msg and "session" in err_msg):
                try:
                    _emit_auth_expired("base_resp_invalid_session_searchbiz", json.dumps(base, ensure_ascii=False))
                except Exception:
                    _emit_auth_expired("base_resp_invalid_session_searchbiz")
            print(f"API Error: {base}")
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
    access_error = _detect_wechat_article_access_error(content_html)
    if access_error:
        raise RuntimeError(access_error)
    title = _extract_title_from_html(content_html, fallback=title)

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
        folder_name = _extract_account_name_from_html(content_html, fallback=folder_name)

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
    try:
        env_file = os.environ.get("WECHAT_ENV_FILE")
        if not env_file:
            env_file = str(REPO_ROOT / ".env")
        p = Path(env_file)
        if p.exists():
            for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != "SERVERCHAN_SENDKEY":
                    continue
                val = v.strip().strip("'").strip('"').strip()
                if val:
                    return val
    except Exception:
        pass
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

def _default_cache_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        base = Path(os.environ.get("XDG_CACHE_HOME") or (home / "Library" / "Caches"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
    return base / "WeChat-Article-Crawler"

def send_serverchan_message_once(
    sendkey,
    title,
    desp,
    dedupe_key: str,
    ttl_seconds: int = 6 * 3600,
    timeout: int = 20,
    state_dir: str = None,
):
    if not dedupe_key:
        return send_serverchan_message(sendkey, title, desp, timeout=timeout)
    base_dir = Path(state_dir) if state_dir else _default_cache_dir()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    h = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()
    stamp = base_dir / f"serverchan_once_{h}.stamp"
    now = time.time()
    try:
        if stamp.exists():
            age = now - stamp.stat().st_mtime
            if age < max(0, int(ttl_seconds)):
                return {"ok": True, "skipped": True, "reason": "throttled", "age_seconds": age, "dedupe_key": dedupe_key}
    except Exception:
        pass
    res = send_serverchan_message(sendkey, title, desp, timeout=timeout)
    if res.get("ok"):
        try:
            stamp.write_text(str(int(now)), encoding="utf-8")
        except Exception:
            try:
                stamp.touch()
            except Exception:
                pass
    try:
        res["dedupe_key"] = dedupe_key
    except Exception:
        pass
    return res


def _resolve_serverchan_summary_url(config=None):
    cfg = get_analysis_config(config or {})
    public_base_url = str(cfg.get("analysis_public_base_url") or "").strip().rstrip("/")
    if public_base_url:
        return f"{public_base_url}/article_analysis"
    return "/article_analysis"


def _build_article_payload(fetched, account_override=""):
    fetched = dict(fetched or {})
    payload_account = (
        _normalize_effective_account_name(fetched.get("account"))
        or _normalize_effective_account_name(account_override)
        or str(fetched.get("account") or account_override or "")
    )
    return {
        "account": payload_account,
        "title": fetched.get("title", ""),
        "date": fetched.get("date", ""),
        "published_at": fetched.get("published_at") or fetched.get("date") or "",
        "url": fetched.get("url", ""),
    }

def _pending_async_analysis_payload(kind="analysis"):
    return {"status": "pending", "reason": "scheduled_async", "kind": kind}


def _async_jobs_dir() -> Path:
    path = OUTPUT_ROOT / "async_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_async_retry_state():
    return {
        "attempt": 1,
        "retry_mode": "until_success",
        "first_failed_at": "",
        "last_failed_at": "",
        "last_reason": "",
        "next_retry_at": "",
        "stop_reason": "",
        "notified": False,
    }


def _normalize_async_retry_state(retry_state=None):
    merged = dict(_default_async_retry_state())
    if isinstance(retry_state, dict):
        for key, value in retry_state.items():
            if value is None:
                continue
            merged[key] = value
    try:
        merged["attempt"] = max(1, int(merged.get("attempt") or 1))
    except Exception:
        merged["attempt"] = 1
    return merged


def _async_retry_time_text(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts or time.time()))


def _parse_async_retry_time_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except Exception:
            continue
    return None


def _seconds_until_async_retry(next_retry_at, now_ts=None) -> float:
    target_ts = _parse_async_retry_time_text(next_retry_at)
    if target_ts is None:
        return 0.0
    current_ts = float(now_ts if now_ts is not None else time.time())
    return max(0.0, float(target_ts) - current_ts)


def _wait_until_async_retry_due(job):
    retry_state = _normalize_async_retry_state((job or {}).get("retry_state"))
    delay_seconds = _seconds_until_async_retry(retry_state.get("next_retry_at"))
    if delay_seconds <= 0:
        return 0.0
    print(
        f"[{_ts_now()}] async single article analysis waiting "
        f"delay_seconds={delay_seconds:.3f} next_retry_at={retry_state.get('next_retry_at') or ''}"
    )
    time.sleep(delay_seconds)
    return delay_seconds


def _classify_async_analysis_failure(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "recoverable"
    external_markers = (
        "wechat_auth_required",
        "wechat_security_verification_required",
        "invalid_url",
        "login_required",
        "need_login",
        "security_verification_required",
        "not_found",
        "deleted",
        "removed",
        "missing_cookie",
        "missing_token",
        "missing_config",
        "forbidden",
        "access_denied",
    )
    if any(marker in text for marker in external_markers):
        return "external"
    if text.startswith("invalid_"):
        return "external"
    return "recoverable"


def _next_async_retry_delay_seconds(attempt: int) -> int:
    try:
        current_attempt = int(attempt or 1)
    except Exception:
        current_attempt = 1
    if current_attempt <= 1:
        return 10
    if current_attempt == 2:
        return 60
    if current_attempt == 3:
        return 300
    return 1800


def _is_successful_async_analysis(analysis) -> bool:
    if not isinstance(analysis, dict):
        return False
    return str(analysis.get("status") or "").strip() == "ok"


def _rewrite_async_job_file(job_path: Path, job):
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return job_path


def _extract_single_article_async_job_article_id(job) -> str:
    if not isinstance(job, dict):
        return ""
    if str(job.get("job_type") or "").strip() != "single_article_analysis":
        return ""
    payload = job.get("payload") or {}
    fetched = payload.get("fetched") or {}
    if not isinstance(fetched, dict):
        return ""
    explicit_article_id = _normalize_article_id(fetched.get("article_id"))
    if explicit_article_id:
        return explicit_article_id
    try:
        return build_article_id(fetched)
    except Exception:
        return ""


def _find_active_single_article_async_job_by_article_id(article_id: str):
    normalized = _normalize_article_id(article_id)
    if not normalized:
        return None
    for job_path in _async_jobs_dir().glob("*.json"):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if _extract_single_article_async_job_article_id(job) == normalized:
            return job_path
    return None


def _notify_async_analysis_stop(config, fetched, reason: str):
    explicit_sendkey = ""
    if isinstance(config, dict):
        explicit_sendkey = str(config.get("serverchan_sendkey") or "").strip()
    sendkey = _get_serverchan_sendkey(config, override_sendkey=explicit_sendkey or None)
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    fetched = dict(fetched or {})
    title = f"异步解读停止：{fetched.get('title') or '(无标题)'}"
    desp = "\n".join(
        [
            f"公众号：{fetched.get('account') or ''}",
            f"标题：{fetched.get('title') or ''}",
            f"链接：{fetched.get('url') or ''}",
            f"原因：{reason or 'unknown_failure'}",
            "请刷新微信鉴权或检查外部条件后再补跑。",
        ]
    ).strip()
    dedupe_key = f"async_analysis_stop:{fetched.get('url') or fetched.get('title') or reason or 'unknown'}"
    return send_serverchan_message_once(
        sendkey,
        title,
        desp,
        dedupe_key=dedupe_key,
        ttl_seconds=6 * 3600,
        state_dir=str(OUTPUT_ROOT / "async_job_notify_state"),
    )


def _handle_async_single_article_result(job, analysis, job_path: Path):
    payload = job.get("payload") or {}
    config = payload.get("config") or {}
    fetched = payload.get("fetched") or {}
    retry_state = _normalize_async_retry_state(job.get("retry_state"))
    if _is_successful_async_analysis(analysis):
        print(
            f"[{_ts_now()}] async single article analysis succeeded "
            f"title={fetched.get('title') or ''} attempt={retry_state.get('attempt')}"
        )
        return {"action": "done"}

    reason = ""
    if isinstance(analysis, dict):
        reason = str(analysis.get("reason") or "").strip()
        if not reason and str(analysis.get("status") or "").strip() == "ok":
            reason = "empty_analysis"
        if not reason:
            reason = str(analysis.get("status") or "").strip()
    reason = reason or "reanalyze_failed"
    failure_type = _classify_async_analysis_failure(reason)
    now_text = _async_retry_time_text()

    if failure_type == "external":
        retry_state["first_failed_at"] = retry_state.get("first_failed_at") or now_text
        retry_state["last_failed_at"] = now_text
        retry_state["last_reason"] = reason
        retry_state["next_retry_at"] = ""
        retry_state["stop_reason"] = reason
        notify_result = _notify_async_analysis_stop(config, fetched, reason)
        retry_state["notified"] = bool(notify_result.get("ok"))
        print(
            f"[{_ts_now()}] async single article analysis stopped "
            f"title={fetched.get('title') or ''} reason={reason}"
        )
        return {"action": "stop", "retry_state": retry_state, "notify_result": notify_result}

    current_attempt = retry_state.get("attempt") or 1
    delay_seconds = _next_async_retry_delay_seconds(current_attempt)
    retry_state["attempt"] = int(current_attempt) + 1
    retry_state["retry_mode"] = str(retry_state.get("retry_mode") or "until_success").strip() or "until_success"
    retry_state["first_failed_at"] = retry_state.get("first_failed_at") or now_text
    retry_state["last_failed_at"] = now_text
    retry_state["last_reason"] = reason
    retry_state["next_retry_at"] = _async_retry_time_text(time.time() + delay_seconds)
    retry_state["stop_reason"] = ""
    retry_state["notified"] = False
    job["retry_state"] = retry_state
    _rewrite_async_job_file(job_path, job)
    try:
        _spawn_async_job_process(job_path)
    except Exception as exc:
        print(
            f"[{_ts_now()}] WARN async single article analysis respawn failed: "
            f"{type(exc).__name__}:{exc}"
        )
        return {
            "action": "requeued",
            "retry_state": retry_state,
            "delay_seconds": delay_seconds,
            "spawn_error": f"{type(exc).__name__}:{exc}",
        }
    print(
        f"[{_ts_now()}] async single article analysis requeued "
        f"title={fetched.get('title') or ''} reason={reason} attempt={retry_state.get('attempt')}"
    )
    return {"action": "requeued", "retry_state": retry_state, "delay_seconds": delay_seconds}


def _serialize_async_job(name, func, args, kwargs):
    kwargs = dict(kwargs or {})
    if func is _attach_single_article_analysis:
        config = args[0] if len(args) > 0 else kwargs.get("config")
        fetched = args[1] if len(args) > 1 else kwargs.get("fetched")
        refresh_index = args[2] if len(args) > 2 else kwargs.get("refresh_index", True)
        force_reanalyze = args[3] if len(args) > 3 else kwargs.get("force_reanalyze", False)
        return {
            "name": name,
            "job_type": "single_article_analysis",
            "payload": {
                "config": config,
                "fetched": fetched,
                "refresh_index": refresh_index,
                "force_reanalyze": force_reanalyze,
            },
            "retry_state": _default_async_retry_state(),
        }
    if func is _run_batch_analysis_pipeline:
        config = args[0] if len(args) > 0 else kwargs.get("config")
        changed_articles = args[1] if len(args) > 1 else kwargs.get("changed_articles")
        per_account_payloads = args[2] if len(args) > 2 else kwargs.get("per_account_payloads")
        headers = args[3] if len(args) > 3 else kwargs.get("headers")
        return {
            "name": name,
            "job_type": "batch_analysis_pipeline",
            "payload": {
                "config": config,
                "changed_articles": changed_articles,
                "per_account_payloads": per_account_payloads,
                "headers": headers,
            },
        }
    raise ValueError(f"unsupported_async_job:{getattr(func, '__name__', type(func).__name__)}")


def _write_async_job_file(job):
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(job.get("name") or "async_job")).strip("._") or "async_job"
    stamp = f"{int(time.time() * 1000)}_{hashlib.sha1(json.dumps(job, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:10]}"
    job_path = _async_jobs_dir() / f"{safe_name}_{stamp}.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return job_path


def _spawn_async_job_process(job_path):
    cmd = [sys.executable, str(Path(__file__).resolve()), "--run-async-job-file", str(job_path)]
    return subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_async_job_file(job_file):
    job_path = Path(job_file)
    remove_job_file = True
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job_type = str(job.get("job_type") or "").strip()
        payload = job.get("payload") or {}
        if job_type == "single_article_analysis":
            _wait_until_async_retry_due(job)
            analysis = _attach_single_article_analysis(
                payload.get("config"),
                payload.get("fetched"),
                refresh_index=bool(payload.get("refresh_index", True)),
                force_reanalyze=bool(payload.get("force_reanalyze", False)),
            )
            outcome = _handle_async_single_article_result(job, analysis, job_path)
            if outcome.get("action") == "requeued":
                remove_job_file = False
            return
        if job_type == "batch_analysis_pipeline":
            _run_batch_analysis_pipeline(
                payload.get("config"),
                payload.get("changed_articles") or [],
                payload.get("per_account_payloads") or [],
                payload.get("headers") or {},
            )
            return
        raise ValueError(f"unsupported_async_job_type:{job_type}")
    finally:
        if remove_job_file:
            try:
                job_path.unlink(missing_ok=True)
            except Exception:
                pass


def _schedule_async_job(name, func, *args, **kwargs):
    if _ASYNC_JOB_DISPATCH_MODE == "process":
        job = _serialize_async_job(name, func, args, kwargs)
        article_id = _extract_single_article_async_job_article_id(job)
        if article_id:
            existing_job_path = _find_active_single_article_async_job_by_article_id(article_id)
            if existing_job_path is not None:
                return {
                    "status": "deduped",
                    "name": name,
                    "mode": "process",
                    "article_id": article_id,
                    "job_file": str(existing_job_path),
                }
        job_path = _write_async_job_file(job)
        process = _spawn_async_job_process(job_path)
        return {"status": "scheduled", "name": name, "mode": "process", "pid": getattr(process, "pid", None)}

    def runner():
        try:
            func(*args, **kwargs)
        except Exception as exc:
            print(f"[{_ts_now()}] WARN async job {name} failed: {type(exc).__name__}:{exc}")

    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    return {"status": "scheduled", "name": name, "mode": "thread"}


def build_serverchan_markdown(article_info, config=None):
    title = article_info.get("title") or "(无标题)"
    account = article_info.get("account") or ""
    published_at = article_info.get("published_at") or article_info.get("date") or ""
    lines = [
        f"**公众号：** {account}",
        f"**时间：** {published_at}",
        f"**标题：** {title}",
        f"[查看解读汇总]({_resolve_serverchan_summary_url(config)})",
        "",
    ]
    return "\n".join([l for l in lines if l is not None])



def _attach_single_article_analysis(config, fetched, refresh_index: bool = True, force_reanalyze: bool = False):
    effective_config = _copy_config_with_forced_reanalyze(config) if force_reanalyze else config
    cfg = get_analysis_config(effective_config)
    if not cfg.get("analysis_enabled"):
        return None
    try:
        from . import article_analysis as _article_analysis_module
    except Exception:
        try:
            import article_analysis as _article_analysis_module
        except Exception:
            _article_analysis_module = None
    analysis = analyze_single_article(
        effective_config,
        {
            "article_id": _normalize_article_id(fetched.get("article_id")),
            "account": fetched.get("account"),
            "title": fetched.get("title"),
            "date": fetched.get("date"),
            "published_at": fetched.get("published_at"),
            "url": fetched.get("url"),
            "markdown": fetched.get("markdown", ""),
        },
    )
    metadata_changed = False
    if isinstance(analysis, dict) and analysis.get("article_id"):
        analysis, metadata_changed = _merge_fetched_fields_into_analysis(analysis, fetched)
        if (
            metadata_changed
            or analysis.get("status") != "ok"
            or _article_analysis_module is None
            or analyze_single_article is not _article_analysis_module.analyze_single_article
        ):
            persist_single_analysis_outputs(effective_config, analysis)
    if refresh_index and isinstance(analysis, dict) and analysis.get("article_id"):
        _refresh_analysis_index_html(effective_config)
    return analysis


def run_reanalyze_from_url(
    article_url,
    account_name=None,
    article_id=None,
    save_markdown=False,
    output_json_path=None,
    serverchan_sendkey=None,
    push=True,
    config=None,
):
    if not _is_allowed_reanalyze_url(article_url):
        raise ValueError("invalid_url")
    config = _copy_config_with_forced_reanalyze(config)
    resolved_account = _resolve_reanalyze_account_name(
        config,
        article_id=article_id,
        payload_account=account_name,
    )
    cookie = config.get("cookie")
    token = config.get("token")
    if cookie and token:
        headers = get_headers(cookie, token)
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    article = {"title": "Unknown", "link": article_url, "create_time": 0, "digest": "", "author": ""}
    fetched = fetch_article_markdown(article, headers, account_name=resolved_account or None)
    fetched = dict(fetched or {})
    if article_id and not fetched.get("article_id"):
        fetched["article_id"] = _normalize_article_id(article_id)
    if resolved_account and not _normalize_effective_account_name(fetched.get("account")):
        fetched["account"] = resolved_account
    payload = _build_article_payload(fetched, account_override=resolved_account)

    if push:
        push_result = push_article_to_serverchan(config, payload, override_sendkey=serverchan_sendkey)
        payload["serverchan"] = push_result
    analysis = _attach_single_article_analysis(config, fetched, force_reanalyze=True)
    payload["analysis"] = analysis

    if save_markdown:
        save_url_to_md(article, headers, account_name=account_name)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def handle_reanalyze_api_request(payload, config, request_headers=None):
    if not isinstance(payload, dict):
        return {"status": "error", "article_id": "", "reason": "invalid_payload"}
    article_id = str(payload.get("article_id") or "").strip()
    if request_headers is not None and not _is_trusted_local_reanalyze_source(request_headers, config):
        return {"status": "error", "article_id": article_id, "reason": "forbidden_origin"}
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"status": "error", "article_id": article_id, "reason": "missing_url"}
    if not _is_allowed_reanalyze_url(url):
        return {"status": "error", "article_id": article_id, "reason": "invalid_url"}
    account_name = str(payload.get("account") or "").strip()
    try:
        result = run_reanalyze_from_url(
            url,
            account_name=account_name,
            article_id=article_id,
            save_markdown=False,
            push=False,
            config=config,
        )
    except Exception as exc:
        explicit_reason = str(exc or "").strip()
        if explicit_reason in ("wechat_auth_required", "wechat_security_verification_required"):
            return {"status": "error", "article_id": article_id, "reason": explicit_reason}
        return {"status": "error", "article_id": article_id, "reason": f"reanalyze_failed:{type(exc).__name__}:{exc}"}
    analysis = result.get("analysis") if isinstance(result, dict) else None
    analysis_status = ""
    if isinstance(analysis, dict):
        analysis_status = str(analysis.get("status") or "").strip()
    analysis_is_ok = isinstance(analysis, dict) and (
        analysis_status == "ok" or (not analysis_status and analysis.get("article_id"))
    )
    if not analysis_is_ok:
        reason = ""
        if isinstance(analysis, dict):
            reason = str(analysis.get("reason") or analysis.get("status") or "").strip()
        return {"status": "error", "article_id": article_id or str((analysis or {}).get("article_id") or ""), "reason": reason or "reanalyze_failed"}
    return {
        "status": "ok",
        "article_id": str(analysis.get("article_id") or article_id),
        "account": str(result.get("account") or analysis.get("account") or ""),
        "title": str(result.get("title") or analysis.get("title") or ""),
        "source": str(result.get("source") or analysis.get("source") or ""),
    }


def make_reanalyze_request_handler(config):
    expected_path = _resolve_reanalyze_api_path(config)

    class ReanalyzeHandler(BaseHTTPRequestHandler):
        def _trusted_origin(self):
            if not _is_trusted_local_reanalyze_source(self.headers, config):
                return ""
            origin = str(self.headers.get("Origin") or "").strip()
            if origin:
                return origin
            referer = str(self.headers.get("Referer") or "").strip()
            if referer:
                try:
                    parsed = urlparse(referer)
                    if parsed.scheme in ("http", "https") and parsed.netloc:
                        return f"{parsed.scheme}://{parsed.netloc}"
                except Exception:
                    return ""
            return ""

        def _send_json(self, status_code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            trusted_origin = self._trusted_origin()
            if trusted_origin:
                self.send_header("Access-Control-Allow-Origin", trusted_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if not _is_trusted_local_reanalyze_source(self.headers, config):
                self._send_json(403, {"status": "error", "reason": "forbidden_origin"})
                return
            self.send_response(204)
            trusted_origin = self._trusted_origin()
            if trusted_origin:
                self.send_header("Access-Control-Allow-Origin", trusted_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self):
            if urlparse(self.path).path != expected_path:
                self._send_json(404, {"status": "error", "reason": "not_found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                content_length = 0
            raw = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send_json(400, {"status": "error", "reason": "invalid_json"})
                return
            result = handle_reanalyze_api_request(payload, config, request_headers=self.headers)
            status_code = 200 if result.get("status") == "ok" else 400
            if result.get("reason") == "forbidden_origin":
                status_code = 403
            self._send_json(status_code, result)

        def log_message(self, format, *args):
            print(f"[{_ts_now()}] reanalyze-api {self.address_string()} {format % args}")

    return ReanalyzeHandler


def run_reanalyze_api_server(config, host="127.0.0.1", port=8766):
    server = ThreadingHTTPServer((host, int(port)), make_reanalyze_request_handler(config))
    print(
        f"[{_ts_now()}] reanalyze api listening on http://{host}:{int(port)}{_resolve_reanalyze_api_path(config)}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{_ts_now()}] reanalyze api stopped")
    finally:
        server.server_close()


def make_analysis_static_request_handler(config, directory=None):
    static_directory = str(directory or OUTPUT_ROOT)
    expected_path = _resolve_reanalyze_api_path(config)

    class AnalysisStaticHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=static_directory, **kwargs)

        def _send_json(self, status_code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path != expected_path:
                self._send_json(404, {"status": "error", "reason": "not_found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                content_length = 0
            raw = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send_json(400, {"status": "error", "reason": "invalid_json"})
                return
            result = handle_reanalyze_api_request(payload, config, request_headers=None)
            reason = str(result.get("reason") or "").strip()
            status_code = 200 if result.get("status") == "ok" else 400
            if reason == "forbidden_origin":
                status_code = 403
            elif reason == "not_found":
                status_code = 404
            elif reason.startswith("reanalyze_failed"):
                status_code = 500
            self._send_json(status_code, result)

        def log_message(self, format, *args):
            print(f"[{_ts_now()}] analysis-static {self.address_string()} {format % args}")

    return AnalysisStaticHandler


def run_analysis_static_server(config, host="127.0.0.1", port=8765, directory=None):
    static_directory = str(directory or OUTPUT_ROOT)
    server = ThreadingHTTPServer(
        (host, int(port)),
        make_analysis_static_request_handler(config, directory=static_directory),
    )
    print(
        f"[{_ts_now()}] analysis static listening on http://{host}:{int(port)} "
        f"(root={static_directory}, reanalyze={_resolve_reanalyze_api_path(config)})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{_ts_now()}] analysis static stopped")
    finally:
        server.server_close()

def push_article_to_serverchan(config, article_info, override_sendkey=None):
    sendkey = _get_serverchan_sendkey(config, override_sendkey=override_sendkey)
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    published_at = article_info.get("published_at") or article_info.get("date") or ""
    msg_title = f"{article_info.get('account') or ''} [{published_at}] {article_info.get('title') or ''}".strip()
    desp = build_serverchan_markdown(article_info, config=config)
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

def build_serverchan_markdown_articles(articles, batch_analysis=None, config=None):
    _ = batch_analysis
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
            label = f"{account} | {published_at} | {title}".strip(" |")
            lines.append(f"- {label}")
        lines.append("")
    lines.extend([f"[查看解读汇总]({_resolve_serverchan_summary_url(config)})", ""])

    return "\n".join([l for l in lines if l is not None]).rstrip()

def push_articles_to_serverchan(config, articles, override_sendkey=None, batch_analysis=None):
    sendkey = _get_serverchan_sendkey(config, override_sendkey=override_sendkey)
    if not sendkey:
        return {"ok": False, "skipped": True, "reason": "no_sendkey"}
    title = f"公众号最新文章（{len(articles)}篇）"
    cfg = get_analysis_config(config)
    batch_block = batch_analysis if cfg.get("analysis_push_batch", True) else None
    desp = build_serverchan_markdown_articles(articles, batch_analysis=batch_block, config=config)
    return send_serverchan_message(sendkey, title, desp)


def _update_push_state(state, state_path, changed_articles, pushed_fakeids):
    if not pushed_fakeids:
        return
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


def _collect_batch_source_map(per_account_payloads):
    source_by_key = {}
    for payload in per_account_payloads:
        key = payload.get("fakeid") or payload.get("url")
        if key:
            source_by_key[key] = payload
    return source_by_key


def _run_batch_analysis_pipeline(config, changed_articles, per_account_payloads, headers):
    analysis_items = []
    batch_analysis = None
    source_by_key = _collect_batch_source_map(per_account_payloads)
    batch_id = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    analysis_cfg = get_analysis_config(config)
    if analysis_cfg.get("analysis_enabled"):
        for article in changed_articles:
            source = source_by_key.get(article.get("fakeid")) or source_by_key.get(article.get("url")) or {}
            fetched = source.get("_fetched_article")
            if not fetched:
                raw = source.get("_raw_article")
                if raw:
                    try:
                        fetched = fetch_article_markdown(raw, headers, account_name=article.get("account"))
                    except Exception:
                        fetched = None
            if fetched:
                analysis = _attach_single_article_analysis(config, fetched, refresh_index=False)
            else:
                analysis = {"status": "skipped", "reason": "missing_article_body"}
            article["analysis"] = analysis
            analysis_items.append(
                {
                    "status": analysis.get("status"),
                    "account": article.get("account"),
                    "title": article.get("title"),
                    "topic": analysis.get("topic"),
                    "core_points": analysis.get("core_points"),
                    "summary": analysis.get("summary"),
                }
            )
        batch_analysis = summarize_analysis_batch(config, analysis_items, batch_id=batch_id)
        if isinstance(batch_analysis, dict) and batch_analysis.get("status") == "ok":
            persist_batch_analysis_outputs(config, batch_analysis)
        _refresh_analysis_index_html(config)
        return batch_analysis

    for article in changed_articles:
        article["analysis"] = {"status": "skipped", "reason": "analysis_disabled"}
    return {"status": "skipped", "reason": "analysis_disabled", "batch_id": batch_id}

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
                        "_raw_article": {"title": fetched.get("title") or "Unknown", "link": url, "create_time": 0, "digest": "", "author": ""},
                    }
                )
                per_account_payloads.append({"account": name, "fakeid": state_key, "url": url, "_fetched_article": fetched})
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
            "_raw_article": payload.get("_raw_article"),
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

    batch_analysis = None
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
            push_result = push_articles_to_serverchan(
                config,
                changed_articles,
                override_sendkey=serverchan_sendkey,
                batch_analysis=batch_analysis,
            )
            if push_result and push_result.get("ok") and (not push_result.get("skipped")):
                for a in changed_articles:
                    if a.get("fakeid"):
                        pushed_fakeids.add(a["fakeid"])
    if push and (not changed_articles):
        push_result = {"ok": True, "skipped": True, "reason": "no_change"}
    _update_push_state(state, state_path, changed_articles, pushed_fakeids)

    batch_analysis = None
    if changed_articles:
        analysis_cfg = get_analysis_config(config)
        if push and analysis_cfg.get("analysis_enabled"):
            for article in changed_articles:
                article["analysis"] = _pending_async_analysis_payload("single_article")
            batch_analysis = _pending_async_analysis_payload("batch_summary")
            _schedule_async_job(
                "push_latest_all_analysis",
                _run_batch_analysis_pipeline,
                config,
                [dict(article) for article in changed_articles],
                [dict(payload) for payload in per_account_payloads],
                dict(headers),
            )
        else:
            batch_analysis = _run_batch_analysis_pipeline(config, changed_articles, per_account_payloads, headers)

    if save_markdown and per_account_payloads:
        for p in per_account_payloads:
            raw = p.get("_raw_article")
            if raw:
                save_url_to_md(raw, headers, account_name=p.get("account"))

    payload_articles = []
    for article in changed_articles:
        payload_articles.append({key: value for key, value in article.items() if not key.startswith("_")})

    payload_out = {
        "count": len(changed_articles),
        "articles": payload_articles,
        "batch_analysis": batch_analysis,
        "serverchan": push_result if push else {"ok": False, "skipped": True, "reason": "no_push"},
        "push_state_file": state_path,
    }
    try:
        analysis_cfg = get_analysis_config(config)
    except Exception:
        analysis_cfg = {}
    if (
        isinstance(analysis_cfg, dict)
        and analysis_cfg.get("analysis_enabled")
        and changed_articles
        and not (push and changed_articles)
    ):
        _refresh_analysis_index_html(config)
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
    payload = _build_article_payload(fetched, account_override=target_account_name)

    if push:
        push_result = push_article_to_serverchan(config, payload, override_sendkey=serverchan_sendkey)
        payload["serverchan"] = push_result
        if get_analysis_config(config).get("analysis_enabled"):
            payload["analysis"] = _pending_async_analysis_payload("single_article")
            _schedule_async_job("extract_latest_analysis", _attach_single_article_analysis, config, dict(fetched))
        else:
            payload["analysis"] = None
    else:
        analysis = _attach_single_article_analysis(config, fetched)
        payload["analysis"] = analysis

    if save_markdown:
        save_url_to_md(chosen, headers, account_name=target_account_name)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload

def run_extract_from_url(article_url, account_name=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True, config=None):
    config = config or {}
    cookie = config.get("cookie")
    token = config.get("token")
    if cookie and token:
        headers = get_headers(cookie, token)
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
    article = {"title": "Unknown", "link": article_url, "create_time": 0, "digest": "", "author": ""}
    fetched = fetch_article_markdown(article, headers, account_name=account_name)
    payload = _build_article_payload(fetched, account_override=account_name)

    if push:
        push_result = push_article_to_serverchan(config, payload, override_sendkey=serverchan_sendkey)
        payload["serverchan"] = push_result
        if get_analysis_config(config).get("analysis_enabled"):
            payload["analysis"] = _pending_async_analysis_payload("single_article")
            _schedule_async_job("extract_from_url_analysis", _attach_single_article_analysis, config, dict(fetched))
        else:
            payload["analysis"] = None
    else:
        analysis = _attach_single_article_analysis(config, fetched)
        payload["analysis"] = analysis

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
        title = _extract_title_from_html(content_html, fallback=title)
        
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
            folder_name = _extract_account_name_from_html(content_html, fallback=folder_name)

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
    global _ASYNC_JOB_DISPATCH_MODE
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--refresh-auth", action="store_true")
    parser.add_argument("--refresh-auth-only", action="store_true")
    parser.add_argument("--refresh-profile-dir", type=str, default="./output/my_wechat_profile")
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
    parser.add_argument("--serve-reanalyze", action="store_true")
    parser.add_argument("--serve-analysis-static", action="store_true")
    parser.add_argument("--run-async-job-file", type=str, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not args.serverchan_sendkey:
        args.serverchan_sendkey = os.environ.get("SERVERCHAN_SENDKEY")

    _load_env_into_process(REPO_ROOT)

    if args.run_async_job_file:
        _run_async_job_file(args.run_async_job_file)
        return

    config = load_json(CONFIG_FILE)
    config_example = str(REPO_ROOT / "config.json.example")
    if (not config) and os.path.exists(config_example):
        config = load_json(config_example)

    if args.refresh_auth:
        try:
            try:
                from .wechat_auth_updater import refresh_wechat_auth
            except ImportError:
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
        old_mode = _ASYNC_JOB_DISPATCH_MODE
        try:
            _ASYNC_JOB_DISPATCH_MODE = "process"
            run_extract_from_url(
                args.article_url,
                account_name=args.account,
                save_markdown=not args.no_save_markdown,
                output_json_path=args.output_json,
                serverchan_sendkey=args.serverchan_sendkey,
                push=not args.no_push,
                config=config,
            )
        finally:
            _ASYNC_JOB_DISPATCH_MODE = old_mode
        return

    if args.serve_reanalyze:
        run_reanalyze_api_server(config, host="127.0.0.1", port=8766)
        return

    if args.serve_analysis_static:
        run_analysis_static_server(config, host="127.0.0.1", port=8765, directory=str(OUTPUT_ROOT))
        return

    if args.extract_latest:
        old_mode = _ASYNC_JOB_DISPATCH_MODE
        try:
            _ASYNC_JOB_DISPATCH_MODE = "process"
            run_extract_latest(
                config,
                account_name_arg=args.account,
                fakeid_arg=args.fakeid,
                save_markdown=not args.no_save_markdown,
                output_json_path=args.output_json,
                serverchan_sendkey=args.serverchan_sendkey,
                push=not args.no_push
            )
        finally:
            _ASYNC_JOB_DISPATCH_MODE = old_mode
        return

    if args.push_latest_all:
        old_mode = _ASYNC_JOB_DISPATCH_MODE
        try:
            _ASYNC_JOB_DISPATCH_MODE = "process"
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
        finally:
            _ASYNC_JOB_DISPATCH_MODE = old_mode
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

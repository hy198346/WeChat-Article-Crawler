import hashlib
import json
import os
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse

import requests


DEFAULT_ANALYSIS_CONFIG = {
    "analysis_enabled": False,
    "analysis_push_batch": True,
    "analysis_news_interpret_url": "",
    "analysis_base_url": "http://192.168.9.158:11434",
    "analysis_model": "qwen2.5-coder:14b-cpu",
    "analysis_timeout_seconds": 30,
    "analysis_summary_base_url": "",
    "analysis_summary_model": "",
    "analysis_summary_timeout_seconds": None,
    "analysis_public_base_url": "",
    "analysis_reanalyze_path": "/api/reanalyze",
    "analysis_max_chars": 8000,
    "analysis_save_json": True,
    "analysis_save_markdown": True,
    "analysis_skip_if_exists": True,
}


def _resolve_analysis_base_url(explicit_value):
    explicit = str(explicit_value or "").strip()
    if explicit:
        return explicit
    for env_name in ("LOCAL_LLM_BASE_URL", "OLLAMA_BASE_URL"):
        env_value = str(os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
    return DEFAULT_ANALYSIS_CONFIG["analysis_base_url"]


def _normalize_news_interpret_url(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    path = (parsed.path or "").strip()
    if not path or path == "/":
        return text.rstrip("/") + "/api/telegraph/interpret"
    return text


def _resolve_news_interpret_url(explicit_value):
    explicit = _normalize_news_interpret_url(explicit_value)
    if explicit:
        return explicit
    env_value = _normalize_news_interpret_url(os.environ.get("NEWS_INTERPRET_BASE_URL"))
    if env_value:
        return env_value
    return DEFAULT_ANALYSIS_CONFIG["analysis_news_interpret_url"]


def _resolve_analysis_model(explicit_value):
    explicit = str(explicit_value or "").strip()
    if explicit:
        return explicit
    for env_name in ("LOCAL_LLM_MODEL", "OLLAMA_MODEL"):
        env_value = str(os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
    return DEFAULT_ANALYSIS_CONFIG["analysis_model"]


def get_analysis_config(config):
    explicit = {}
    if isinstance(config, dict):
        explicit = {key: value for key, value in config.items() if key.startswith("analysis_")}
    merged = dict(DEFAULT_ANALYSIS_CONFIG)
    merged.update(explicit)
    merged["analysis_news_interpret_url"] = _resolve_news_interpret_url(
        explicit.get("analysis_news_interpret_url")
    )
    merged["analysis_base_url"] = _resolve_analysis_base_url(explicit.get("analysis_base_url"))
    merged["analysis_model"] = _resolve_analysis_model(explicit.get("analysis_model"))
    summary_base_url = _normalize_scalar_string(explicit.get("analysis_summary_base_url"))
    summary_model = _normalize_scalar_string(explicit.get("analysis_summary_model"))
    merged["analysis_summary_base_url"] = summary_base_url or merged["analysis_base_url"]
    merged["analysis_summary_model"] = summary_model or merged["analysis_model"]
    summary_timeout = explicit.get("analysis_summary_timeout_seconds")
    if summary_timeout in (None, ""):
        merged["analysis_summary_timeout_seconds"] = merged["analysis_timeout_seconds"]
    else:
        merged["analysis_summary_timeout_seconds"] = summary_timeout
    reanalyze_path = _normalize_scalar_string(merged.get("analysis_reanalyze_path")) or "/api/reanalyze"
    merged["analysis_reanalyze_path"] = reanalyze_path if reanalyze_path.startswith("/") else f"/{reanalyze_path}"
    public_base_url = _normalize_scalar_string(explicit.get("analysis_public_base_url"))
    if not public_base_url:
        public_base_url = _normalize_scalar_string(os.environ.get("WECHAT_ANALYSIS_PUBLIC_BASE_URL"))
    merged["analysis_public_base_url"] = public_base_url.rstrip("/")
    if explicit.get("analysis_reanalyze_path") not in (None, ""):
        reanalyze_source = explicit.get("analysis_reanalyze_path")
    else:
        reanalyze_source = os.environ.get("WECHAT_ANALYSIS_REANALYZE_PATH")
    reanalyze_path = _normalize_scalar_string(reanalyze_source) or merged["analysis_reanalyze_path"]
    merged["analysis_reanalyze_path"] = reanalyze_path if reanalyze_path.startswith("/") else f"/{reanalyze_path}"
    return merged


def get_analysis_output_root(config) -> Path:
    cfg = get_analysis_config(config)
    output_dir = cfg.get("analysis_output_dir")
    return Path(output_dir) if output_dir else Path("output")


def _normalize_article_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if all(ch in safe_chars for ch in text) and len(text) <= 120:
        return text
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def build_article_id(article) -> str:
    explicit_id = _normalize_article_id(article.get("article_id"))
    if explicit_id:
        return explicit_id

    url = str(article.get("url") or "").strip()
    normalized_url = ""
    if url:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host and path:
            normalized_url = f"{host}{path}"
            if host == "mp.weixin.qq.com" and parsed.query:
                stable_keys = {"__biz", "mid", "idx", "sn", "chksm"}
                pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k in stable_keys]
                if pairs:
                    normalized_url += "?" + "&".join(f"{k}={v}" for k, v in sorted(pairs))
        else:
            normalized_url = url

    raw = normalized_url or "|".join(
        [
            str(article.get("published_at") or article.get("date") or ""),
            str(article.get("title") or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _truncate_markdown(markdown: str, max_chars: int) -> str:
    text = (markdown or "").strip()
    return text[: max(1, int(max_chars or 8000))]


def _article_cache_path(config, article_id: str) -> Path:
    safe_article_id = _normalize_article_id(article_id)
    return get_analysis_output_root(config) / "article_analysis" / f"{safe_article_id}.json"


def _batch_analysis_base_path(config, batch_id: str) -> Path:
    return get_analysis_output_root(config) / "article_batches" / batch_id


def _load_cached_analysis(cache_path: Path):
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _is_valid_cached_single_analysis(data):
    if not isinstance(data, dict):
        return False
    if data.get("status") != "ok":
        return False
    if not isinstance(data.get("article_id"), str) or not data.get("article_id").strip():
        return False
    summary = _normalize_summary_text(data.get("summary"))
    if summary:
        return True
    if "topic" not in data or not isinstance(data.get("topic"), str):
        return False
    if "audience" not in data or not isinstance(data.get("audience"), str):
        return False
    for field in ("core_points", "risks"):
        if field not in data:
            return False
        value = data.get(field)
        if not isinstance(value, list):
            return False
        if any(not isinstance(item, str) for item in value):
            return False
    return True


def _normalize_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                items.append(text)
        return items
    text = str(value).strip()
    return [text] if text else []


def _normalize_scalar_string(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        items = _normalize_list(value)
        return items[0] if items else ""
    return str(value).strip()


def _normalize_account_name(value):
    text = _normalize_scalar_string(value)
    if not text:
        return "Unknown_Account"
    lowered = text.lower()
    if lowered == "unknown_account":
        return "Unknown_Account"
    if lowered.startswith("gh_"):
        return "Unknown_Account"
    return text


def _normalize_summary_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            text = _normalize_summary_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("summary", "analysis", "content", "text", "result"):
            if key in value:
                return _normalize_summary_text(value.get(key))
    return str(value).strip()


def _render_summary_html(summary: str) -> str:
    text = _normalize_summary_text(summary)
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    if len(lines) == 1 and not lines[0].startswith(("## ", "### ", "- ")):
        return f'<div class="summary-inline">{html_escape(lines[0])}</div>'

    parts = ['<div class="summary-block">']
    list_items = []

    def flush_list():
        nonlocal list_items
        if not list_items:
            return
        parts.append(
            '<ul class="summary-list">'
            + "".join(f"<li>{html_escape(item)}</li>" for item in list_items)
            + "</ul>"
        )
        list_items = []

    for line in lines:
        if line.startswith("## "):
            flush_list()
            parts.append(f'<div class="summary-section-title">{html_escape(line[3:].strip())}</div>')
        elif line.startswith("### "):
            flush_list()
            parts.append(f'<div class="summary-subsection-title">{html_escape(line[4:].strip())}</div>')
        elif line.startswith("- "):
            list_items.append(line[2:].strip())
        else:
            flush_list()
            parts.append(f'<p class="summary-paragraph">{html_escape(line)}</p>')

    flush_list()
    parts.append("</div>")
    return "".join(parts)


def _safe_write_text(path: Path, content: str):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def _load_account_categories(doc_path: Path):
    mapping = {}
    order = []
    current_category = None
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, []
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if line.endswith("：") or line.endswith(":"):
            current_category = line[:-1].strip()
            if current_category and current_category not in order:
                order.append(current_category)
            continue
        if current_category and line not in mapping:
            mapping[line] = current_category
    return mapping, order


def _resolve_account_categories(output_root: Path):
    candidates = [
        output_root / "公众号名字",
        output_root.parent / "公众号名字",
        Path(__file__).resolve().parents[2] / "公众号名字",
    ]
    for path in candidates:
        mapping, order = _load_account_categories(path)
        if mapping or order:
            return mapping, order
    return {}, []


def _build_single_article_prompt(article, cfg):
    payload = {
        "title": article.get("title", ""),
        "account": article.get("account", ""),
        "published_at": article.get("published_at") or article.get("date") or "",
        "url": article.get("url", ""),
        "markdown": _truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"]),
    }
    return (
        "你是微信公众号文章总结助手。请基于给定文章信息生成简洁中文总结。"
        "只输出 JSON，不要输出 Markdown、解释、代码块或额外文字。"
        "JSON 必须包含字段：\"summary\"(字符串或字符串数组)。"
        "如果信息不足，请保持字段存在并用简短总结说明信息有限。\n"
        f"文章输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _build_batch_prompt(analyses):
    return (
        "你是微信公众号批量解读助手。请基于多篇文章的单篇解读生成本轮汇总。"
        "只输出 JSON，不要输出 Markdown、解释、代码块或额外文字。"
        "JSON 必须包含字段：\"summary\"(字符串或字符串数组), "
        "\"batch_focus\"(字符串), \"shared_themes\"(字符串数组), "
        "\"priority_reads\"(字符串数组)。"
        "优先总结共性主题和最值得优先阅读的文章。\n"
        f"输入数据：{json.dumps({'articles': analyses}, ensure_ascii=False)}"
    )


def _build_summary_request_config(config):
    cfg = get_analysis_config(config)
    request_cfg = dict(cfg)
    request_cfg["analysis_base_url"] = cfg["analysis_summary_base_url"]
    request_cfg["analysis_model"] = cfg["analysis_summary_model"]
    request_cfg["analysis_timeout_seconds"] = cfg["analysis_summary_timeout_seconds"]
    return request_cfg


def render_single_analysis_markdown(analysis):
    if not isinstance(analysis, dict) or analysis.get("status") != "ok":
        return ""
    summary = _normalize_summary_text(analysis.get("summary"))
    if summary:
        lines = [
            "### AI解读",
            f"- 总结：{summary}",
        ]
        return "\n".join(lines)
    core_points = "；".join(_normalize_list(analysis.get("core_points"))) or "无"
    risks = "；".join(_normalize_list(analysis.get("risks"))) or "无"
    lines = [
        "### AI解读",
        f"- 主题：{_normalize_scalar_string(analysis.get('topic')) or 'Unknown'}",
        f"- 核心观点：{core_points}",
        f"- 适合谁看：{_normalize_scalar_string(analysis.get('audience')) or '未说明'}",
        f"- 风险/注意点：{risks}",
    ]
    return "\n".join(lines)


def render_batch_analysis_markdown(batch_analysis):
    if not isinstance(batch_analysis, dict) or batch_analysis.get("status") != "ok":
        return ""
    summary = _normalize_summary_text(batch_analysis.get("summary"))
    shared_themes = "；".join(_normalize_list(batch_analysis.get("shared_themes"))) or "无"
    priority_reads = "；".join(_normalize_list(batch_analysis.get("priority_reads"))) or "无"
    lines = ["## 本轮解读"]
    if summary:
        lines.append(f"- 总结：{summary}")
    lines.extend(
        [
            f"- 本轮重点：{_normalize_scalar_string(batch_analysis.get('batch_focus')) or '无'}",
            f"- 共性观点：{shared_themes}",
            f"- 优先阅读：{priority_reads}",
        ]
    )
    return "\n".join(lines)


def persist_single_analysis_outputs(config, analysis):
    if not isinstance(analysis, dict):
        return
    article_id = _normalize_article_id(analysis.get("article_id"))
    if not article_id:
        return
    cfg = get_analysis_config(config)
    root = get_analysis_output_root(config) / "article_analysis"
    normalized_analysis = dict(analysis)
    normalized_analysis["article_id"] = article_id
    if cfg.get("analysis_save_json"):
        _safe_write_text(root / f"{article_id}.json", json.dumps(normalized_analysis, ensure_ascii=False, indent=2))
    if cfg.get("analysis_save_markdown"):
        body = render_single_analysis_markdown(normalized_analysis)
        _safe_write_text(root / f"{article_id}.md", body + "\n")


def persist_batch_analysis_outputs(config, batch_analysis):
    if not isinstance(batch_analysis, dict):
        return
    batch_id = _normalize_scalar_string(batch_analysis.get("batch_id"))
    if not batch_id:
        return
    cfg = get_analysis_config(config)
    base_path = _batch_analysis_base_path(config, batch_id)
    if cfg.get("analysis_save_json"):
        _safe_write_text(base_path.with_suffix(".json"), json.dumps(batch_analysis, ensure_ascii=False, indent=2))
    if cfg.get("analysis_save_markdown"):
        body = render_batch_analysis_markdown(batch_analysis)
        if body:
            _safe_write_text(base_path.with_suffix(".md"), body + "\n")


def _parse_single_analysis(content: str):
    data = json.loads(content)
    summary = _normalize_summary_text(data.get("summary"))
    if summary:
        return {
            "status": "ok",
            "summary": summary,
            "topic": "",
            "core_points": [],
            "audience": "",
            "risks": [],
        }
    return {
        "status": "ok",
        "topic": _normalize_scalar_string(data.get("topic")),
        "core_points": _normalize_list(data.get("core_points")),
        "audience": _normalize_scalar_string(data.get("audience")),
        "risks": _normalize_list(data.get("risks")),
    }


def _normalize_remote_summary_analysis(result, article):
    summary = ""
    if isinstance(result, dict):
        summary = _normalize_summary_text(
            result.get("summary")
            or result.get("analysis")
            or result.get("content")
            or result.get("text")
            or result.get("result")
        )
    elif result is not None:
        summary = _normalize_summary_text(result)
    payload = {
        "article_id": build_article_id(article),
        "account": article.get("account", ""),
        "title": article.get("title", ""),
        "url": article.get("url", ""),
        "published_at": article.get("published_at", ""),
        "date": article.get("date", ""),
        "summary": summary,
        "topic": "",
        "core_points": [],
        "audience": "",
        "risks": [],
        "source": "yuanbao",
    }
    if summary:
        payload["status"] = "ok"
    else:
        payload["status"] = "skipped"
        payload["reason"] = "empty_summary"
    return payload


def _call_news_interpret(config, article):
    cfg = get_analysis_config(config)
    api_url = _normalize_scalar_string(cfg.get("analysis_news_interpret_url"))
    if not api_url:
        return None
    response = requests.post(
        api_url,
        json={
            "title": _normalize_scalar_string(article.get("title")),
            "content": _truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"]),
            "time": _normalize_scalar_string(article.get("published_at"))
            or _normalize_scalar_string(article.get("date")),
            "provider": "auto",
            "mode": "web",
            "speed": "fast",
        },
        timeout=cfg["analysis_timeout_seconds"],
    )
    response.raise_for_status()
    return response.json()


def _analyze_single_article_with_local_llm(config, article, article_id: str):
    cfg = get_analysis_config(config)
    prompt = _build_single_article_prompt(article, cfg)
    try:
        result = _parse_single_analysis(call_ollama_chat(config, prompt))
    except requests.Timeout:
        result = {"status": "skipped", "reason": "ollama_timeout", "article_id": article_id}
    except Exception as exc:
        result = {"status": "skipped", "reason": f"ollama_error:{exc}", "article_id": article_id}
    result.update(
        {
            "article_id": article_id,
            "account": article.get("account", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "published_at": article.get("published_at", ""),
            "date": article.get("date", ""),
        }
    )
    if result.get("status") == "ok" and not result.get("source"):
        result["source"] = "local"
    return result


def _post_native_ollama_chat(cfg, prompt: str):
    response = requests.post(
        cfg["analysis_base_url"].rstrip("/") + "/api/chat",
        json={
            "model": cfg["analysis_model"],
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=cfg["analysis_timeout_seconds"],
    )
    response.raise_for_status()
    data = response.json()
    return data.get("message", {}).get("content", "")


def _post_openai_compat_chat(cfg, prompt: str):
    response = requests.post(
        cfg["analysis_base_url"].rstrip("/") + "/chat/completions",
        json={
            "model": cfg["analysis_model"],
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "你是微信公众号文章分析助手。严格遵循用户提示中的字段要求，只输出一个 JSON 对象，不要输出 Markdown、解释、代码块或额外文字。",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": "Bearer ollama"},
        timeout=cfg["analysis_timeout_seconds"],
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content", "")


def call_ollama_chat(config, prompt: str):
    cfg = get_analysis_config(config)
    base_url = cfg["analysis_base_url"].rstrip("/")
    if base_url.endswith("/v1"):
        return _post_openai_compat_chat(cfg, prompt)
    try:
        return _post_native_ollama_chat(cfg, prompt)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        message = str(exc)
        if status in (404, 405) or "404" in message or "405" in message:
            compat_cfg = dict(cfg)
            compat_cfg["analysis_base_url"] = base_url + "/v1"
            return _post_openai_compat_chat(compat_cfg, prompt)
        raise


def analyze_single_article(config, article):
    cfg = get_analysis_config(config)
    article_id = build_article_id(article)
    cache_path = _article_cache_path(config, article_id)

    if not cfg.get("analysis_enabled"):
        return {"status": "skipped", "reason": "analysis_disabled", "article_id": article_id}

    if cfg.get("analysis_skip_if_exists") and cache_path.exists():
        cached = _load_cached_analysis(cache_path)
        if _is_valid_cached_single_analysis(cached):
            return cached

    result = None
    remote_error = ""
    if cfg.get("analysis_news_interpret_url"):
        try:
            remote_result = _call_news_interpret(config, article)
            normalized = _normalize_remote_summary_analysis(remote_result, article)
            if normalized.get("status") == "ok":
                result = normalized
            else:
                remote_error = _normalize_scalar_string(normalized.get("reason")) or "empty_summary"
        except requests.Timeout:
            remote_error = "news_interpret_timeout"
        except Exception as exc:
            remote_error = f"news_interpret_failed:{type(exc).__name__}:{exc}"

    if result is None:
        result = _analyze_single_article_with_local_llm(config, article, article_id)
        if result.get("status") == "ok" and remote_error:
            result["source"] = "local_fallback"
            if not result.get("reason"):
                result["reason"] = remote_error

    if cfg.get("analysis_save_json", True) or cfg.get("analysis_save_markdown", True):
        persist_single_analysis_outputs(config, result)

    return result


def summarize_analysis_batch(config, analyses, batch_id: str):
    cfg = get_analysis_config(config)
    if not cfg.get("analysis_enabled"):
        return {"status": "skipped", "reason": "analysis_disabled", "batch_id": batch_id}

    ok_items = [item for item in analyses if item.get("status") == "ok"]
    if not ok_items:
        return {"status": "skipped", "reason": "no_article_analysis", "batch_id": batch_id}

    try:
        data = json.loads(call_ollama_chat(_build_summary_request_config(config), _build_batch_prompt(ok_items)))
    except requests.Timeout:
        return {"status": "skipped", "reason": "ollama_timeout", "batch_id": batch_id}
    except Exception as exc:
        return {"status": "skipped", "reason": f"ollama_error:{exc}", "batch_id": batch_id}

    return {
        "status": "ok",
        "batch_id": batch_id,
        "summary": _normalize_summary_text(data.get("summary")),
        "batch_focus": _normalize_scalar_string(data.get("batch_focus")),
        "shared_themes": _normalize_list(data.get("shared_themes")),
        "priority_reads": _normalize_list(data.get("priority_reads")),
    }


def _parse_analysis_datetime(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _analysis_sort_key_with_mtime(date_text: str, mtime: Optional[float], tie_breaker: str):
    dt = _parse_analysis_datetime(date_text)
    cleaned = str(date_text or "").strip()
    if dt is not None:
        return (int(dt.timestamp()), cleaned, str(tie_breaker or ""))
    if mtime is None:
        return (-1, cleaned, str(tie_breaker or ""))
    try:
        return (int(float(mtime)), cleaned, str(tie_breaker or ""))
    except (TypeError, ValueError):
        return (-1, cleaned, str(tie_breaker or ""))


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _render_analysis_item_html(item: dict) -> str:
    article_id = _normalize_scalar_string(item.get("article_id"))
    title = _normalize_scalar_string(item.get("title")) or "(无标题)"
    url = _normalize_scalar_string(item.get("url"))
    date_text = _normalize_scalar_string(item.get("published_at")) or _normalize_scalar_string(
        item.get("date")
    )
    status = _normalize_scalar_string(item.get("status")) or "ok"
    reason = _normalize_scalar_string(item.get("reason"))
    summary = _normalize_summary_text(item.get("summary"))
    topic = _normalize_scalar_string(item.get("topic"))
    audience = _normalize_scalar_string(item.get("audience"))
    core_points = _normalize_list(item.get("core_points"))
    risks = _normalize_list(item.get("risks"))
    if status != "ok" and not topic:
        topic = "解读失败，可重试"
    if status != "ok" and reason and not risks:
        risks = [reason]

    safe_url = None
    if url:
        try:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https"):
                safe_url = url
        except Exception:
            safe_url = None
    if safe_url:
        title_html = f'<a href="{html_escape(safe_url)}" target="_blank" rel="noopener noreferrer">{html_escape(title)}</a>'
    else:
        title_html = html_escape(title)

    action_status = ""
    if safe_url:
        button_attrs = [
            'type="button"',
            'class="reanalyze-button"',
            f'data-article-id="{html_escape(article_id)}"',
            f'data-url="{html_escape(safe_url)}"',
        ]
    else:
        button_attrs = ['type="button"', 'class="reanalyze-button"', "disabled"]
        action_status = "缺少原文链接，无法重解读"

    parts = [
        '<div class="item">',
        f'<div class="title">{title_html}</div>',
        f'<div class="meta">{html_escape(date_text)}</div>' if date_text else '<div class="meta"></div>',
        (
            '<div class="actions">'
            f'<button {" ".join(button_attrs)}>重新解读</button>'
            f'<span class="reanalyze-status">{html_escape(action_status)}</span>'
            "</div>"
        ),
    ]

    if summary:
        parts.append(
            '<div class="field summary">'
            '<span class="label">总结：</span>'
            f"{_render_summary_html(summary)}"
            "</div>"
        )
    else:
        parts.append(
            f'<div class="field topic"><span class="label">主题：</span>{html_escape(topic)}</div>'
            if topic
            else '<div class="field topic"><span class="label">主题：</span></div>'
        )
        if core_points:
            parts.append(
                '<div class="label">核心观点：</div>'
                '<ul class="points">'
                + "".join(f"<li>{html_escape(point)}</li>" for point in core_points)
                + "</ul>"
            )
        else:
            parts.append('<div class="label">核心观点：</div><ul class="points"></ul>')

        if audience:
            parts.append(
                f'<div class="field audience"><span class="label">适合谁看：</span>{html_escape(audience)}</div>'
            )
        else:
            parts.append('<div class="field audience"><span class="label">适合谁看：</span></div>')

        if risks:
            parts.append(
                '<div class="label">风险/注意点：</div>'
                '<ul class="risks">'
                + "".join(f"<li>{html_escape(risk)}</li>" for risk in risks)
                + "</ul>"
            )
        else:
            parts.append('<div class="label">风险/注意点：</div><ul class="risks"></ul>')

    parts.append("</div>")
    return "\n".join(parts)


def _account_anchor_id(account: str) -> str:
    text = _normalize_account_name(account)
    return "account-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _should_skip_index_item(item: dict) -> bool:
    account = _normalize_account_name(item.get("account"))
    title = _normalize_scalar_string(item.get("title"))
    url = _normalize_scalar_string(item.get("url"))
    date_text = _normalize_scalar_string(item.get("date_text"))
    if account != "Unknown_Account":
        return False
    if url or date_text:
        return False
    if title and title not in ("Unknown", "(无标题)"):
        return False
    return True


def _merge_index_items_for_same_url(previous: dict, current: dict):
    previous_key = previous.get("_sort_key") or (-1, "", "")
    current_key = current.get("_sort_key") or (-1, "", "")
    primary, secondary = (current, previous) if current_key >= previous_key else (previous, current)
    merged = dict(secondary)
    merged.update(primary)

    if _normalize_account_name(merged.get("account")) == "Unknown_Account":
        secondary_account = _normalize_account_name(secondary.get("account"))
        primary_account = _normalize_account_name(primary.get("account"))
        if secondary_account != "Unknown_Account":
            merged["account"] = secondary.get("account")
        elif primary_account != "Unknown_Account":
            merged["account"] = primary.get("account")

    title_text = _normalize_scalar_string(merged.get("title"))
    if title_text in ("", "Unknown", "(无标题)"):
        secondary_title = _normalize_scalar_string(secondary.get("title"))
        primary_title = _normalize_scalar_string(primary.get("title"))
        if secondary_title not in ("", "Unknown", "(无标题)"):
            merged["title"] = secondary.get("title")
        elif primary_title not in ("", "Unknown", "(无标题)"):
            merged["title"] = primary.get("title")

    fallback_scalar_fields = (
        "published_at",
        "date",
        "date_text",
        "status",
        "reason",
        "summary",
        "topic",
        "audience",
        "url",
    )
    for field in fallback_scalar_fields:
        if _normalize_scalar_string(merged.get(field)):
            continue
        merged[field] = secondary.get(field) or primary.get(field) or ""

    for field in ("core_points", "risks"):
        if _normalize_list(merged.get(field)):
            continue
        merged[field] = _normalize_list(secondary.get(field)) or _normalize_list(primary.get(field))

    return merged


def _format_latest_time(entry_item: dict) -> str:
    text = _normalize_scalar_string(entry_item.get("date_text"))
    if text and _parse_analysis_datetime(text) is not None:
        return text
    mtime_value = entry_item.get("_mtime")
    if mtime_value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(mtime_value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _resolve_reanalyze_api_url(config) -> str:
    cfg = get_analysis_config(config)
    base_url = _normalize_scalar_string(cfg.get("analysis_public_base_url")).rstrip("/")
    path = _normalize_scalar_string(cfg.get("analysis_reanalyze_path")) or "/api/reanalyze"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_url}{path}" if base_url else path


def build_analysis_index_html(config):
    if isinstance(config, (str, Path)):
        output_root = Path(config)
    else:
        output_root = get_analysis_output_root(config)

    analysis_dir = output_root / "article_analysis"
    items = []
    try:
        json_paths = sorted(analysis_dir.glob("*.json"))
    except OSError:
        json_paths = []

    for path in json_paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            print(f"{_now_text()} skip invalid json: {path.name} err={type(exc).__name__}:{exc}")
            continue
        if not isinstance(data, dict):
            continue
        article_id = _normalize_article_id(data.get("article_id")) or _normalize_article_id(path.stem)
        if not article_id:
            continue
        account = _normalize_account_name(data.get("account"))
        title = _normalize_scalar_string(data.get("title")) or "(无标题)"
        published_at = _normalize_scalar_string(data.get("published_at"))
        date = _normalize_scalar_string(data.get("date"))
        date_text = published_at or date
        item = {
            "article_id": article_id,
            "account": account,
            "title": title,
            "url": _normalize_scalar_string(data.get("url")),
            "published_at": published_at,
            "date": date,
            "date_text": date_text,
            "status": _normalize_scalar_string(data.get("status")) or "unknown",
            "reason": _normalize_scalar_string(data.get("reason")),
            "summary": _normalize_summary_text(data.get("summary")),
            "topic": _normalize_scalar_string(data.get("topic")),
            "audience": _normalize_scalar_string(data.get("audience")),
            "core_points": _normalize_list(data.get("core_points")),
            "risks": _normalize_list(data.get("risks")),
            "_mtime": mtime,
            "_sort_key": _analysis_sort_key_with_mtime(date_text, mtime, path.name),
        }
        if _should_skip_index_item(item):
            continue
        items.append(item)

    deduped_items = []
    items_by_url = {}
    for item in items:
        url = _normalize_scalar_string(item.get("url"))
        if url:
            previous = items_by_url.get(url)
            items_by_url[url] = item if previous is None else _merge_index_items_for_same_url(previous, item)
            continue
        deduped_items.append(item)

    deduped_items.extend(items_by_url.values())

    grouped = {}
    for item in deduped_items:
        grouped.setdefault(item["account"], []).append(item)

    account_entries = []
    for account, group_items in grouped.items():
        sorted_items = sorted(group_items, key=lambda it: it["_sort_key"], reverse=True)
        latest_key = sorted_items[0]["_sort_key"] if sorted_items else (-1, "", "")
        account_entries.append((account, latest_key, sorted_items))

    account_entries.sort(key=lambda entry: entry[1], reverse=True)

    category_map, category_order = _resolve_account_categories(output_root)
    if not category_order:
        category_order = ["misc公众号"]
    if "misc公众号" not in category_order:
        category_order.append("misc公众号")

    directory_groups = {name: [] for name in category_order}
    for account, _latest_key, sorted_items in account_entries:
        latest_time = _format_latest_time(sorted_items[0]) if sorted_items else ""
        latest_title = (
            _normalize_scalar_string(sorted_items[0].get("title")) if sorted_items else ""
        ) or "(无标题)"
        category = category_map.get(account, "misc公众号")
        directory_groups.setdefault(category, [])
        directory_groups[category].append(
            {
                "account": account,
                "anchor_id": _account_anchor_id(account),
                "count": len(sorted_items),
                "latest_time": latest_time,
                "latest_title": latest_title,
                "latest_key": sorted_items[0]["_sort_key"] if sorted_items else (-1, "", ""),
            }
        )
    for group_items in directory_groups.values():
        group_items.sort(key=lambda entry: entry["latest_key"], reverse=True)

    generated_at = _now_text()
    total_accounts = len(account_entries)
    total_analyses = len(deduped_items)
    reanalyze_api_url = _resolve_reanalyze_api_url(config)

    html_parts = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="color-scheme" content="light" />',
        "<title>公众号 AI 解读汇总</title>",
        "<style>",
        "html{background:#ffffff;}",
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;max-width:960px;margin:0 auto;padding:20px;line-height:1.5;background:#ffffff;color:#24292f;}",
        "h1{margin:0 0 16px 0;}",
        "h2{margin:24px 0 12px 0;padding-bottom:6px;border-bottom:1px solid #eee;}",
        ".subtitle{color:#666;font-size:12px;margin:-6px 0 18px 0;}",
        ".directory{margin:0 0 20px 0;padding:14px 16px;background:#f6f8fa;border:1px solid #e5e7eb;border-radius:10px;}",
        ".directory-title{font-weight:600;margin-bottom:10px;}",
        ".directory-group{margin-top:12px;}",
        ".directory-group-title{font-size:13px;font-weight:600;color:#57606a;margin-bottom:8px;}",
        ".directory-list{display:flex;flex-wrap:wrap;gap:8px 10px;}",
        ".directory-link{display:inline-block;padding:4px 10px;border-radius:999px;background:#fff;border:1px solid #d0d7de;color:#0969da;text-decoration:none;font-size:13px;}",
        ".directory-link:hover{text-decoration:none;background:#f0f7ff;}",
        ".account-meta{color:#666;font-weight:400;font-size:12px;margin-left:8px;}",
        ".item{padding:10px 0;border-bottom:1px dashed #eee;}",
        ".title{font-weight:600;}",
        ".meta{color:#666;font-size:12px;margin-top:4px;}",
        ".actions{display:flex;align-items:center;gap:10px;margin-top:8px;}",
        ".reanalyze-button{border:1px solid #d0d7de;background:#f6f8fa;border-radius:8px;padding:4px 10px;cursor:pointer;font-size:12px;}",
        ".reanalyze-button[disabled]{cursor:not-allowed;opacity:0.55;}",
        ".reanalyze-status{color:#666;font-size:12px;}",
        ".reanalyze-button.is-busy{opacity:0.75;cursor:progress;}",
        ".reanalyze-status.is-success{color:#1a7f37;}",
        ".reanalyze-status.is-error{color:#cf222e;}",
        ".label{color:#666;font-size:12px;margin-top:6px;}",
        ".field{margin-top:6px;}",
        ".summary-inline{display:inline;}",
        ".summary-block{display:block;margin-top:4px;}",
        ".summary-section-title{font-weight:600;color:#24292f;margin-top:8px;}",
        ".summary-subsection-title{font-weight:600;color:#57606a;margin-top:6px;}",
        ".summary-paragraph{margin:6px 0 0 0;line-height:1.6;}",
        ".summary-list{margin:6px 0 0 18px;padding:0;}",
        ".summary-list li{margin-top:4px;line-height:1.6;}",
        ".points,.risks{margin:6px 0 0 18px;}",
        "details{margin-top:10px;}",
        "summary{cursor:pointer;color:#444;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>公众号 AI 解读汇总</h1>",
        f'<div class="subtitle">生成时间：{html_escape(generated_at)} ｜ 账号：{total_accounts} ｜ 解读：{total_analyses}</div>',
    ]

    if account_entries:
        html_parts.append('<div class="directory">')
        html_parts.append('<div class="directory-title">公众号目录</div>')
        for category in category_order:
            group_items = directory_groups.get(category) or []
            if not group_items:
                continue
            html_parts.append('<div class="directory-group">')
            html_parts.append(f'<div class="directory-group-title">{html_escape(category)}</div>')
            html_parts.append('<div class="directory-list">')
            for entry in group_items:
                label = f'{entry["account"]}（{entry["count"]}）'
                if entry["latest_time"]:
                    label = f'{label}｜最新：{entry["latest_time"]}'
                label = f'{label}｜标题：{entry["latest_title"]}'
                html_parts.append(
                    f'<a class="directory-link" href="#{entry["anchor_id"]}">{html_escape(label)}</a>'
                )
            html_parts.append("</div>")
            html_parts.append("</div>")
        html_parts.append("</div>")

    for account, _latest_key, sorted_items in account_entries:
        latest_time = _format_latest_time(sorted_items[0]) if sorted_items else ""
        count = len(sorted_items)
        meta = f"{count}篇"
        if latest_time:
            meta = f"{meta}｜最新：{latest_time}"
        html_parts.append(
            f"<h2 id=\"{_account_anchor_id(account)}\">{html_escape(account)}<span class=\"account-meta\">{html_escape(meta)}</span></h2>"
        )
        if not sorted_items:
            continue
        html_parts.append(_render_analysis_item_html(sorted_items[0]))
        history = sorted_items[1:]
        if history:
            html_parts.append("<details>")
            html_parts.append("<summary>历史解读</summary>")
            for item in history:
                html_parts.append(_render_analysis_item_html(item))
            html_parts.append("</details>")

    html_parts.extend(
        [
            "<script>",
            f"const REANALYZE_API_URL = {json.dumps(reanalyze_api_url, ensure_ascii=False)};",
            "function setReanalyzeStatus(button, text, state) {",
            '  const status = button.parentElement ? button.parentElement.querySelector(".reanalyze-status") : null;',
            "  if (!status) return;",
            "  status.textContent = text || '';",
            '  status.classList.remove("is-success", "is-error");',
            "  if (state) {",
            "    status.classList.add(state);",
            "  }",
            "}",
            'document.querySelectorAll(".reanalyze-button").forEach((button) => {',
            "  if (button.disabled) return;",
            '  button.addEventListener("click", async () => {',
            '    const articleId = button.getAttribute("data-article-id") || "";',
            '    const url = button.getAttribute("data-url") || "";',
            "    if (!url) {",
            '      setReanalyzeStatus(button, "缺少原文链接，无法重解读");',
            "      return;",
            "    }",
            "    button.disabled = true;",
            '    button.classList.add("is-busy");',
            '    setReanalyzeStatus(button, "重新解读中...");',
            "    try {",
            "      const response = await fetch(REANALYZE_API_URL, {",
            '        method: "POST",',
            '        headers: {"Content-Type": "application/json"},',
            "        body: JSON.stringify({article_id: articleId, url}),",
            "      });",
            "      const payload = await response.json();",
            "      if (!response.ok || payload.status !== 'ok') {",
            "        throw new Error('reanalyze_failed');",
            "      }",
            '      setReanalyzeStatus(button, "重新解读成功，正在刷新...", "is-success");',
            "      window.setTimeout(() => window.location.reload(), 800);",
            "    } catch (error) {",
            '      setReanalyzeStatus(button, "重新解读失败，请稍后重试", "is-error");',
            "      button.disabled = false;",
            '      button.classList.remove("is-busy");',
            "    }",
            "  });",
            "});",
            "</script>",
        ]
    )

    html_parts.extend(["</body>", "</html>"])
    content = "\n".join(html_parts) + "\n"
    if not _safe_write_text(analysis_dir / "index.html", content):
        print(f"{_now_text()} failed to write analysis index html: {analysis_dir / 'index.html'}")
    return content

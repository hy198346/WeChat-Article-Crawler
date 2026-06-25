import hashlib
import json
import os
import re
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse

import requests

DEBUG_SESSION_ID = "wechat-interpret-failures"


# region debug-point premarket-digest-fail:reporter
def _dbg_report(hypothesis_id: str, msg: str, data=None):
    try:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if str(os.environ.get("TRAE_DEBUG_DISABLE") or "").strip() in ("1", "true", "yes"):
            return
        env_path = Path(__file__).resolve().parents[2] / ".dbg" / f"{DEBUG_SESSION_ID}.env"
        if not env_path.exists():
            return
        env = {}
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = (raw or "").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = (k or "").strip()
            if not k:
                continue
            env[k] = (v or "").strip().strip("'").strip('"').strip()
        url = str(env.get("DEBUG_SERVER_URL") or "").strip()
        session_id = str(env.get("DEBUG_SESSION_ID") or DEBUG_SESSION_ID).strip() or DEBUG_SESSION_ID
        if not url or not session_id:
            return
        payload = {
            "sessionId": session_id,
            "runId": str(os.environ.get("TRAE_DEBUG_RUN_ID") or "pre"),
            "hypothesisId": str(hypothesis_id or ""),
            "location": "WeChat-Article-Crawler/scripts/wechat_article_crawler/article_analysis.py",
            "msg": f"[DEBUG] {str(msg or '')}",
            "data": data if isinstance(data, dict) else {},
            "ts": int(datetime.utcnow().timestamp() * 1000),
        }
        requests.post(url, json=payload, timeout=0.8)
    except Exception:
        return


# endregion debug-point premarket-digest-fail:reporter


DEFAULT_ANALYSIS_CONFIG = {
    "analysis_enabled": False,
    "analysis_force_provider": "",
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

OLLAMA_SCHEMA_DRIFT_CANDIDATE_FIELDS = (
    "summary",
    "content",
    "analysis",
    "text",
    "result",
    "response",
    "key_points",
    "core_points",
    "trend_impact",
    "key_impact",
    "core_trend",
    "platform_response",
    "application_types",
)


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
    merged["analysis_force_provider"] = _normalize_analysis_force_provider(
        explicit.get("analysis_force_provider")
    )
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
    return _has_meaningful_single_analysis_content(
        summary=summary,
        topic=data.get("topic"),
        core_points=data.get("core_points"),
        audience=data.get("audience"),
        risks=data.get("risks"),
    )


def _should_preserve_existing_success_cache(force_provider, cached, result) -> bool:
    if not force_provider:
        return False
    if not _is_valid_cached_single_analysis(cached):
        return False
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "").strip() != "ok"


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


def _normalize_analysis_force_provider(value):
    text = _normalize_scalar_string(value).lower()
    return text if text in ("yuanbao", "ollama") else ""


def _reanalyze_provider_specs():
    return (
        {"provider": "yuanbao", "label": "元宝解读"},
        {"provider": "ollama", "label": "本地模型解读"},
    )


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


def _normalize_summary_candidates(*values):
    parts = []
    for value in values:
        text = _normalize_summary_text(value)
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def _has_meaningful_summary_text(value) -> bool:
    text = _normalize_summary_text(value)
    if not text:
        return False
    placeholder_values = {
        "",
        "（无）",
        "(无)",
        "无",
        "无外部证据",
        "（无外部证据）",
        "(无外部证据)",
    }
    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith(("## ", "### ")):
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        if not line or line in placeholder_values:
            continue
        return True
    return False


def _has_meaningful_single_analysis_content(
    *, summary="", topic="", core_points=None, audience="", risks=None
):
    return any(
        (
            _has_meaningful_summary_text(summary),
            _normalize_scalar_string(topic),
            bool(_normalize_list(core_points)),
            _normalize_scalar_string(audience),
            bool(_normalize_list(risks)),
        )
    )


def _has_meaningful_schema_drift_candidate(value, *, list_like: bool = False) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if not value:
            return False
        return any(
            _has_meaningful_schema_drift_candidate(item, list_like=list_like)
            for item in value.values()
        )
    if list_like:
        return bool(_normalize_list(value))
    return bool(_normalize_summary_text(value))


def _compress_preview_text(value, limit: int = 400) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, int(limit or 400))] + "..."


def _summarize_ollama_schema_drift(raw_content: str, article, reason: str = "empty_analysis") -> str:
    try:
        data = json.loads(raw_content)
    except (TypeError, ValueError):
        data = None
    top_level_keys = []
    non_empty_candidates = []
    if isinstance(data, dict):
        top_level_keys = [str(key) for key in data.keys()]
        for field in OLLAMA_SCHEMA_DRIFT_CANDIDATE_FIELDS:
            value = data.get(field)
            has_value = _has_meaningful_schema_drift_candidate(
                value,
                list_like=field in ("key_points", "core_points", "application_types"),
            )
            if has_value:
                non_empty_candidates.append(field)
    article_id = build_article_id(article)
    return " ".join(
        [
            "[ollama-schema-drift]",
            "provider=ollama",
            f"account={json.dumps(_normalize_scalar_string(article.get('account')), ensure_ascii=False)}",
            f"article_id={json.dumps(article_id, ensure_ascii=False)}",
            f"title={json.dumps(_normalize_scalar_string(article.get('title')), ensure_ascii=False)}",
            f"reason={json.dumps(_normalize_scalar_string(reason) or 'empty_analysis', ensure_ascii=False)}",
            f"top_level_keys={json.dumps(top_level_keys, ensure_ascii=False)}",
            f"non_empty_candidates={json.dumps(non_empty_candidates, ensure_ascii=False)}",
            f"raw_preview={json.dumps(_compress_preview_text(raw_content), ensure_ascii=False)}",
        ]
    )


def _log_ollama_schema_drift(raw_content: str, article, reason: str = "empty_analysis"):
    try:
        summary = _summarize_ollama_schema_drift(raw_content, article, reason=reason)
        if summary:
            print(f"{_now_text()} {summary}")
    except Exception:
        return


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
        "你是微信公众号文章深度总结助手。请基于给定文章信息生成中文深度总结。"
        "只输出 JSON，不要输出 Markdown、解释、代码块或额外文字。"
        "JSON 必须包含字段：\"summary\"(字符串或字符串数组)。"
        "总结必须优先覆盖文章主结论、关键支撑逻辑、重要变化原因，以及文中提到的产业链、技术、公司或趋势。"
        "不要只给一句笼统概括；尽量输出 2-4 段有信息量的总结。"
        "如果信息不足，请保持字段存在并明确说明信息有限，但仍要提炼可确认的核心内容。\n"
        f"文章输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _is_low_quality_single_article_summary(text: str) -> bool:
    s = _normalize_summary_text(text)
    if not s:
        return True
    compact = re.sub(r"\s+", "", s)
    detail_hits = sum(1 for kw in ("主结论", "逻辑", "产业链", "技术", "公司", "趋势", "驱动", "竞争") if kw in s)
    weak_hits = sum(1 for kw in ("主要讲", "整体来看", "总体而言", "值得关注") if kw in s)
    paragraph_count = len([line for line in s.splitlines() if line.strip()])
    if len(compact) < 45:
        return True
    if len(compact) < 80 and detail_hits < 2:
        return True
    if paragraph_count <= 1 and detail_hits < 2:
        return True
    return weak_hits > 0 and detail_hits < 2


def _build_single_article_rewrite_prompt(article, cfg, draft: str):
    base = _build_single_article_prompt(article, cfg)
    return (
        f"{base}\n\n"
        "下面是上一轮输出，这一版信息密度不够，请在不编造的前提下重写。\n"
        "重写要求：必须补出主结论之外的关键支撑逻辑，尽量恢复原文的重要信息层次；"
        "不要只给一句话，优先输出 2-4 段有内容的总结。\n\n"
        "【上一轮输出】\n"
        f"{_normalize_summary_text(draft)}"
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


def _coerce_market_style_single_analysis(data: dict):
    market_summary = _normalize_summary_text(
        data.get("market_summary")
        or data.get("market_overview")
        or data.get("overview")
        or data.get("summary_overview")
    )
    market_trend = _normalize_summary_text(data.get("market_trend") or data.get("trend") or data.get("trend_summary"))
    key_sectors = data.get("key_sectors") or data.get("sectors") or data.get("hot_sectors")
    key_events = data.get("key_events") or data.get("events") or data.get("key_drivers")

    sector_lines = []
    core_points = []
    if isinstance(key_sectors, (list, tuple)):
        for item in key_sectors:
            if isinstance(item, dict):
                sector = _normalize_scalar_string(item.get("sector") or item.get("name") or item.get("title"))
                details = _normalize_summary_text(
                    item.get("details")
                    or item.get("detail")
                    or item.get("reason")
                    or item.get("analysis")
                    or item.get("comment")
                )
                if not sector and not details:
                    continue
                if sector and details:
                    sector_lines.append(f"- {sector}：{details}")
                    core_points.append(f"{sector}：{details}"[:140])
                elif sector:
                    sector_lines.append(f"- {sector}")
                    core_points.append(sector[:140])
                else:
                    sector_lines.append(f"- {details}")
                    core_points.append(details[:140])
            else:
                text = _normalize_scalar_string(item)
                if text:
                    sector_lines.append(f"- {text}")
                    core_points.append(text[:140])

    event_lines = []
    if isinstance(key_events, (list, tuple)):
        for item in key_events:
            if isinstance(item, dict):
                text = _normalize_summary_text(item.get("event") or item.get("title") or item.get("text") or item)
            else:
                text = _normalize_summary_text(item)
            if text:
                event_lines.append(f"- {text}")

    lines = []
    if market_summary:
        lines.append(market_summary)
    if market_trend:
        lines.append(f"市场趋势：{market_trend}")
    if sector_lines:
        lines.append("重点板块：")
        lines.extend(sector_lines[:12])
    if event_lines:
        lines.append("关键事件：")
        lines.extend(event_lines[:12])

    summary = "\n".join(line for line in lines if str(line or "").strip())
    if not _has_meaningful_summary_text(summary):
        return None
    return {
        "status": "ok",
        "summary": summary,
        "topic": "",
        "core_points": core_points[:12],
        "audience": "",
        "risks": [],
    }


def _parse_single_analysis(content: str):
    data = json.loads(content)
    summary = _normalize_summary_candidates(
        data.get("summary"),
        data.get("content"),
        data.get("analysis"),
        data.get("text"),
        data.get("result"),
        data.get("response"),
        data.get("trend_impact"),
        data.get("key_impact"),
        data.get("core_trend"),
        data.get("platform_response"),
    )
    core_points = _normalize_list(
        data.get("core_points") or data.get("key_points") or data.get("application_types")
    )
    if summary:
        return {
            "status": "ok",
            "summary": summary,
            "topic": "",
            "core_points": core_points,
            "audience": "",
            "risks": [],
        }
    if isinstance(data, dict):
        coerced = _coerce_market_style_single_analysis(data)
        if isinstance(coerced, dict):
            return coerced
    topic = _normalize_scalar_string(data.get("topic"))
    audience = _normalize_scalar_string(data.get("audience"))
    risks = _normalize_list(data.get("risks"))
    if not _has_meaningful_single_analysis_content(
        summary=summary,
        topic=topic,
        core_points=core_points,
        audience=audience,
        risks=risks,
    ):
        return {"status": "skipped", "reason": "empty_analysis"}
    return {
        "status": "ok",
        "topic": topic,
        "core_points": core_points,
        "audience": audience,
        "risks": risks,
    }


def _normalize_remote_summary_analysis(result, article):
    summary = ""
    remote_reason = ""
    need_login = False
    need_login_url = ""
    if isinstance(result, dict):
        summary = _normalize_summary_text(
            result.get("summary")
            or result.get("analysis")
            or result.get("content")
            or result.get("text")
            or result.get("result")
        )
        remote_reason = _normalize_scalar_string(result.get("error") or result.get("reason"))
        need_login = bool(result.get("need_login")) or remote_reason == "need_login"
        need_login_url = _normalize_scalar_string(
            result.get("needLoginUrl") or result.get("need_login_url")
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
    if need_login:
        payload["need_login"] = True
    if need_login_url:
        payload["needLoginUrl"] = need_login_url
    if _has_meaningful_summary_text(summary):
        payload["status"] = "ok"
    else:
        payload["summary"] = ""
        payload["status"] = "skipped"
        payload["reason"] = remote_reason or "empty_summary"
    return payload


def _call_news_interpret(config, article):
    cfg = get_analysis_config(config)
    api_url = _normalize_scalar_string(cfg.get("analysis_news_interpret_url"))
    if not api_url:
        return None
    provider = _normalize_analysis_force_provider(cfg.get("analysis_force_provider")) or "auto"
    # region debug-point A:news-interpret-request
    try:
        _dbg_report(
            "A",
            "news_interpret.request",
            {
                "article_id": build_article_id(article),
                "account": str(article.get("account") or "")[:80],
                "title": str(article.get("title") or "")[:120],
                "provider": provider,
                "api_url": api_url[:200],
                "content_chars": len(_truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"])),
            },
        )
    except Exception:
        pass
    # endregion debug-point A:news-interpret-request
    response = requests.post(
        api_url,
        json={
            "title": _normalize_scalar_string(article.get("title")),
            "content": _truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"]),
            "time": _normalize_scalar_string(article.get("published_at"))
            or _normalize_scalar_string(article.get("date")),
            "provider": provider,
            "mode": "wechat_summary",
            "speed": "fast",
        },
        timeout=cfg["analysis_timeout_seconds"],
    )
    response.raise_for_status()
    data = response.json()
    # region debug-point A:news-interpret-response
    try:
        _dbg_report(
            "A",
            "news_interpret.response",
            {
                "article_id": build_article_id(article),
                "account": str(article.get("account") or "")[:80],
                "title": str(article.get("title") or "")[:120],
                "status_code": response.status_code,
                "ok": bool(data.get("ok") is True),
                "error": str(data.get("error") or ""),
                "message": str(data.get("message") or "")[:300],
                "providerUsed": str(data.get("providerUsed") or ""),
                "attemptedProviders": ",".join([str(x or "") for x in (data.get("attemptedProviders") or [])[:6]]),
                "need_login": bool(data.get("need_login") or data.get("needLoginUrl")),
                "debug_tail": "\n".join([str(x or "")[:160] for x in (data.get("debugLog") or [])[-4:]])[:900],
            },
        )
    except Exception:
        pass
    # endregion debug-point A:news-interpret-response
    return data


def _analyze_single_article_with_local_llm(config, article, article_id: str):
    cfg = get_analysis_config(config)
    prompt = _build_single_article_prompt(article, cfg)
    try:
        raw_content = call_ollama_chat(config, prompt)
        result = _parse_single_analysis(raw_content)
        # region debug-point C:ollama-parse-result
        try:
            _dbg_report(
                "C",
                "ollama.parse_result",
                {
                    "article_id": article_id,
                    "account": str(article.get("account") or "")[:80],
                    "title": str(article.get("title") or "")[:120],
                    "markdown_chars": len(str(article.get("markdown") or "")),
                    "status": str(result.get("status") or ""),
                    "reason": str(result.get("reason") or ""),
                },
            )
        except Exception:
            pass

        # endregion debug-point C:ollama-parse-result
        if (
            result.get("status") == "ok"
            and _normalize_summary_text(result.get("summary"))
            and _is_low_quality_single_article_summary(result.get("summary"))
        ):
            try:
                rewrite_prompt = _build_single_article_rewrite_prompt(article, cfg, result.get("summary"))
                rewrite_raw_content = call_ollama_chat(config, rewrite_prompt)
                rewrite_result = _parse_single_analysis(rewrite_raw_content)
                if rewrite_result.get("status") == "ok" and not _is_low_quality_single_article_summary(
                    rewrite_result.get("summary")
                ):
                    result = rewrite_result
            except requests.Timeout:
                pass
            except Exception:
                pass
        if result.get("status") == "skipped" and result.get("reason") == "empty_analysis":
            try:
                # region debug-point C:ollama-empty-analysis
                try:
                    _dbg_report(
                        "C",
                        "ollama.empty_analysis",
                        {
                            "article_id": article_id,
                            "account": str(article.get("account") or "")[:80],
                            "title": str(article.get("title") or "")[:120],
                            "schema_drift": _summarize_ollama_schema_drift(
                                raw_content, article, reason="empty_analysis"
                            )[:900],
                        },
                    )
                except Exception:
                    pass

                # endregion debug-point C:ollama-empty-analysis
                _log_ollama_schema_drift(raw_content, article, reason="empty_analysis")
            except Exception:
                pass
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
    force_provider = _normalize_analysis_force_provider(cfg.get("analysis_force_provider"))
    remote_only = force_provider == "yuanbao"
    local_only = force_provider == "ollama"
    cached_success = _load_cached_analysis(cache_path) if cache_path.exists() else None

    if not cfg.get("analysis_enabled"):
        return {"status": "skipped", "reason": "analysis_disabled", "article_id": article_id}

    if not force_provider and cfg.get("analysis_skip_if_exists") and cache_path.exists():
        if _is_valid_cached_single_analysis(cached_success):
            return cached_success

    result = None
    remote_error = ""
    if not local_only and cfg.get("analysis_news_interpret_url"):
        try:
            remote_result = _call_news_interpret(config, article)
            normalized = _normalize_remote_summary_analysis(remote_result, article)
            # region debug-point B:remote-normalized
            try:
                _dbg_report(
                    "B",
                    "analysis.remote_normalized",
                    {
                        "article_id": article_id,
                        "account": str(article.get("account") or "")[:80],
                        "title": str(article.get("title") or "")[:120],
                        "status": str(normalized.get("status") or ""),
                        "reason": str(normalized.get("reason") or "")[:240],
                        "source": str(normalized.get("source") or ""),
                        "need_login": bool(normalized.get("need_login") or normalized.get("needLoginUrl")),
                        "summary_chars": len(str(normalized.get("summary") or "")),
                    },
                )
            except Exception:
                pass
            # endregion debug-point B:remote-normalized
            if normalized.get("status") == "ok":
                result = normalized
            else:
                remote_error = _normalize_scalar_string(normalized.get("reason")) or "empty_summary"
                if remote_only:
                    normalized["reason"] = remote_error
                    result = normalized
        except requests.Timeout:
            remote_error = "news_interpret_timeout"
            # region debug-point B:remote-timeout
            try:
                _dbg_report(
                    "B",
                    "analysis.remote_timeout",
                    {
                        "article_id": article_id,
                        "account": str(article.get("account") or "")[:80],
                        "title": str(article.get("title") or "")[:120],
                    },
                )
            except Exception:
                pass
            # endregion debug-point B:remote-timeout
        except Exception as exc:
            remote_error = f"news_interpret_failed:{type(exc).__name__}:{exc}"
            # region debug-point B:remote-exception
            try:
                _dbg_report(
                    "B",
                    "analysis.remote_exception",
                    {
                        "article_id": article_id,
                        "account": str(article.get("account") or "")[:80],
                        "title": str(article.get("title") or "")[:120],
                        "error": remote_error[:400],
                    },
                )
            except Exception:
                pass
            # endregion debug-point B:remote-exception

    if remote_only and result is None:
        result = {
            "status": "skipped",
            "reason": remote_error or "news_interpret_unavailable",
            "article_id": article_id,
            "account": article.get("account", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "published_at": article.get("published_at", ""),
            "date": article.get("date", ""),
            "summary": "",
            "topic": "",
            "core_points": [],
            "audience": "",
            "risks": [],
            "source": "yuanbao",
        }

    if result is None:
        result = _analyze_single_article_with_local_llm(config, article, article_id)
        if result.get("status") == "ok" and remote_error:
            result["source"] = "local_fallback"
            if remote_error != "need_login" and not result.get("reason"):
                result["reason"] = remote_error

    if result.get("status") == "ok":
        if _normalize_scalar_string(result.get("reason")) == "need_login":
            result["reason"] = ""
        elif "reason" not in result:
            result["reason"] = ""
        if bool(result.get("need_login")):
            result["need_login"] = False
        elif "need_login" not in result:
            result["need_login"] = False
        if _normalize_scalar_string(result.get("needLoginUrl")):
            result["needLoginUrl"] = ""
        elif "needLoginUrl" not in result:
            result["needLoginUrl"] = ""

    # region debug-point B:analysis-final
    try:
        _dbg_report(
            "B",
            "analysis.final_result",
            {
                "article_id": article_id,
                "account": str(article.get("account") or "")[:80],
                "title": str(article.get("title") or "")[:120],
                "status": str(result.get("status") or ""),
                "reason": str(result.get("reason") or "")[:240],
                "source": str(result.get("source") or ""),
                "remote_error": remote_error[:240],
                "summary_chars": len(str(result.get("summary") or "")),
                "force_provider": force_provider or "",
            },
        )
    except Exception:
        pass
    # endregion debug-point B:analysis-final

    preserve_existing_success_cache = _should_preserve_existing_success_cache(
        force_provider,
        cached_success,
        result,
    )
    if (
        (cfg.get("analysis_save_json", True) or cfg.get("analysis_save_markdown", True))
        and not preserve_existing_success_cache
    ):
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


def _render_analysis_item_html(item: dict, config=None) -> str:
    article_id = _normalize_scalar_string(item.get("article_id"))
    article_anchor_id = _article_anchor_id(article_id)
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
    if status == "ok" and not _has_meaningful_single_analysis_content(
        summary=summary,
        topic=topic,
        core_points=core_points,
        audience=audience,
        risks=risks,
    ):
        status = "skipped"
        reason = reason or "empty_analysis"
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
    action_buttons = []
    for spec in _reanalyze_provider_specs():
        button_attrs = [
            'type="button"',
            'class="reanalyze-button"',
            f'data-article-id="{html_escape(article_id)}"',
            f'data-provider="{html_escape(spec["provider"])}"',
        ]
        if safe_url:
            button_attrs.append(f'data-url="{html_escape(safe_url)}"')
        else:
            button_attrs.append("disabled")
            action_status = "缺少原文链接，无法重解读"
        action_buttons.append(f'<button {" ".join(button_attrs)}>{html_escape(spec["label"])}</button>')

    parts = [
        (
            f'<div class="item" id="{html_escape(article_anchor_id)}">'
            if article_anchor_id
            else '<div class="item">'
        ),
        f'<div class="title">{title_html}</div>',
        f'<div class="meta">{html_escape(date_text)}</div>' if date_text else '<div class="meta"></div>',
        (
            '<div class="actions">'
            f'{"".join(action_buttons)}'
            f'<span class="reanalyze-status">{html_escape(action_status)}</span>'
            "</div>"
        ),
        _render_need_login_hint_html(config, item),
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


def _account_slug(account: str) -> str:
    text = _normalize_account_name(account)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _account_page_relative_path(account: str) -> str:
    return f"accounts/{_account_slug(account)}.html"


def _article_anchor_id(article_id: str) -> str:
    normalized = _normalize_article_id(article_id)
    return f"article-{normalized}" if normalized else ""


def _account_page_path(analysis_dir: Path, account: str, relative_path: Optional[str] = None) -> Path:
    normalized_relative_path = _normalize_scalar_string(relative_path)
    if normalized_relative_path:
        return analysis_dir / normalized_relative_path
    return analysis_dir / "accounts" / f"{_account_slug(account)}.html"


def _resolve_account_page_relative_paths(accounts):
    grouped_accounts = {}
    for account in accounts or []:
        normalized_account = _normalize_account_name(account)
        base_slug = _account_slug(normalized_account)
        grouped_accounts.setdefault(base_slug, [])
        if normalized_account not in grouped_accounts[base_slug]:
            grouped_accounts[base_slug].append(normalized_account)

    resolved = {}
    for base_slug, account_names in grouped_accounts.items():
        if len(account_names) == 1:
            resolved[account_names[0]] = f"accounts/{base_slug}.html"
            continue
        for account_name in sorted(account_names):
            suffix = hashlib.sha1(account_name.encode("utf-8")).hexdigest()[:8]
            resolved[account_name] = f"accounts/{base_slug}-{suffix}.html"
    return resolved


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
        "needLoginUrl",
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
    if not merged.get("need_login"):
        merged["need_login"] = bool(primary.get("need_login")) or bool(secondary.get("need_login"))
    if _normalize_scalar_string(merged.get("status")) == "ok":
        merged["reason"] = ""
        merged["need_login"] = False
        merged["needLoginUrl"] = ""

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


def _resolve_news_origin(config) -> str:
    cfg = get_analysis_config(config)
    url = _normalize_scalar_string(cfg.get("analysis_news_interpret_url"))
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_need_login_asset_url(config, raw_url) -> str:
    text = _normalize_scalar_string(raw_url)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return text
    base_url = _resolve_news_origin(config)
    if not base_url:
        return text
    if text.startswith("/"):
        return f"{base_url}{text}"
    return f"{base_url}/{text.lstrip('/')}"


def _analysis_page_style_lines():
    return [
        "html{background:#ffffff;}",
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;max-width:960px;margin:0 auto;padding:20px;line-height:1.5;background:#ffffff;color:#24292f;overflow-wrap:anywhere;word-break:break-word;}",
        "h1{margin:0 0 16px 0;}",
        "h2{margin:24px 0 12px 0;padding-bottom:6px;border-bottom:1px solid #eee;}",
        ".subtitle{color:#666;font-size:12px;margin:-6px 0 18px 0;}",
        ".directory{margin:0 0 20px 0;padding:14px 16px;background:#f6f8fa;border:1px solid #e5e7eb;border-radius:10px;}",
        ".directory-title{font-weight:600;margin-bottom:10px;}",
        ".directory-group{margin-top:12px;}",
        ".directory-group-title{font-size:13px;font-weight:600;color:#57606a;margin-bottom:8px;}",
        ".directory-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;}",
        ".directory-link{display:block;padding:12px 14px;border-radius:12px;background:#fff;border:1px solid #d0d7de;color:#0969da;text-decoration:none;font-size:13px;line-height:1.5;}",
        ".directory-link:hover{text-decoration:none;background:#f0f7ff;}",
        ".directory-account{font-size:15px;font-weight:600;color:#24292f;}",
        ".directory-count,.directory-latest-time{margin-top:4px;font-size:12px;color:#57606a;}",
        ".directory-latest-title{margin-top:6px;color:#0969da;}",
        ".account-meta{color:#666;font-weight:400;font-size:12px;margin-left:8px;}",
        ".back-link{display:inline-block;margin-bottom:12px;color:#0969da;text-decoration:none;font-size:13px;}",
        ".back-link:hover{text-decoration:underline;}",
        ".item{padding:14px 0;border-bottom:1px dashed #eee;}",
        ".title{font-weight:600;line-height:1.6;}",
        ".title a{color:inherit;text-decoration:none;}",
        ".title a:hover{text-decoration:underline;}",
        ".meta{color:#666;font-size:12px;margin-top:4px;}",
        ".actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px;}",
        ".reanalyze-button{border:1px solid #d0d7de;background:#f6f8fa;border-radius:8px;padding:8px 12px;cursor:pointer;font-size:12px;}",
        ".reanalyze-button[disabled]{cursor:not-allowed;opacity:0.55;}",
        ".reanalyze-status{color:#666;font-size:12px;}",
        ".reanalyze-button.is-busy{opacity:0.75;cursor:progress;}",
        ".reanalyze-status.is-success{color:#1a7f37;}",
        ".reanalyze-status.is-error{color:#cf222e;}",
        ".reanalyze-login-hint{margin-top:10px;}",
        ".reanalyze-login-link{display:inline-block;margin-top:4px;font-size:12px;color:#0969da;text-decoration:none;}",
        ".reanalyze-login-link:hover{text-decoration:underline;}",
        ".reanalyze-login-image-wrap{margin-top:8px;}",
        ".reanalyze-login-image{display:block;max-width:220px;width:100%;height:auto;border:1px solid #d0d7de;border-radius:10px;background:#fff;}",
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
        ".history-title{margin-top:20px;font-weight:600;color:#24292f;}",
        "details{margin-top:10px;}",
        "summary{cursor:pointer;color:#444;}",
        "img{max-width:100%;height:auto;}",
        "@media (max-width: 640px){",
        "body{max-width:none;padding:16px 12px 28px;}",
        ".subtitle{margin:0 0 14px 0;line-height:1.6;}",
        ".directory{padding:12px;}",
        ".directory-list{grid-template-columns:1fr;}",
        ".directory-link{display:block;padding:12px 14px;border-radius:12px;}",
        ".actions{align-items:stretch;flex-direction:column;}",
        ".reanalyze-button{width:100%;min-height:40px;}",
        ".reanalyze-status{display:block;min-height:20px;}",
        "summary{padding:6px 0;}",
        "}",
    ]


def _directory_fetch_style_lines():
    return [
        ".directory-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:-6px 0 18px 0;}",
        ".fetch-latest-button{border:1px solid #1f6feb;background:#0969da;color:#fff;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px;font-weight:600;}",
        ".fetch-latest-button[disabled]{cursor:not-allowed;opacity:0.6;}",
        ".fetch-latest-button.is-busy{cursor:progress;opacity:0.75;}",
        ".fetch-latest-status{color:#57606a;font-size:12px;}",
        ".fetch-latest-status.is-success{color:#1a7f37;}",
        ".fetch-latest-status.is-error{color:#cf222e;}",
        "@media (max-width: 640px){",
        ".directory-actions{align-items:stretch;flex-direction:column;}",
        ".fetch-latest-button{width:100%;min-height:42px;}",
        ".fetch-latest-status{width:100%;line-height:1.6;}",
        "}",
    ]


def _render_page_start(title: str, extra_style_lines=None):
    return [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        '<meta name="color-scheme" content="light" />',
        f"<title>{html_escape(title)}</title>",
        "<style>",
        *_analysis_page_style_lines(),
        *(extra_style_lines or []),
        "</style>",
        "</head>",
        "<body>",
    ]


def _render_need_login_hint_html(config, item: dict) -> str:
    reason = _normalize_scalar_string(item.get("reason"))
    need_login = bool(item.get("need_login")) or reason == "need_login"
    if not need_login:
        return '<div class="reanalyze-login-hint"></div>'
    need_login_url = _resolve_need_login_asset_url(
        config, item.get("needLoginUrl") or item.get("need_login_url")
    )
    if not need_login_url:
        return '<div class="reanalyze-login-hint"></div>'
    safe_url = html_escape(need_login_url)
    return (
        '<div class="reanalyze-login-hint">'
        '<div class="label">元宝登录二维码：</div>'
        f'<a class="reanalyze-login-link" href="{safe_url}" target="_blank" rel="noopener noreferrer">打开登录二维码</a>'
        '<div class="reanalyze-login-image-wrap">'
        f'<img class="reanalyze-login-image" src="{safe_url}" alt="元宝登录二维码" loading="lazy" />'
        "</div>"
        "</div>"
    )


def _render_reanalyze_script_html(config):
    reanalyze_api_url = _resolve_reanalyze_api_url(config)
    reanalyze_api_path = (
        _normalize_scalar_string(get_analysis_config(config).get("analysis_reanalyze_path")) or "/api/reanalyze"
    )
    if not reanalyze_api_path.startswith("/"):
        reanalyze_api_path = "/" + reanalyze_api_path
    need_login_base_url = _resolve_news_origin(config)
    provider_messages = {
        spec["provider"]: {
            "label": spec["label"],
            "pending": f'{spec["label"]}中...',
            "success": f'{spec["label"]}成功，正在刷新...',
            "error": f'{spec["label"]}失败，请稍后重试',
        }
        for spec in _reanalyze_provider_specs()
    }
    return [
        "<script>",
        f"const REANALYZE_API_URL = {json.dumps(reanalyze_api_url, ensure_ascii=False)};",
        f"const REANALYZE_API_PATH = {json.dumps(reanalyze_api_path, ensure_ascii=False)};",
        f"const NEED_LOGIN_BASE_URL = {json.dumps(need_login_base_url, ensure_ascii=False)};",
        "const REANALYZE_NEED_LOGIN_RETRY_DELAY_MS = 15000;",
        "const REANALYZE_NEED_LOGIN_RETRY_MAX_ATTEMPTS = 4;",
        f"const REANALYZE_PROVIDER_MESSAGES = {json.dumps(provider_messages, ensure_ascii=False)};",
        "const needLoginReanalyzeRetryTimers = new Map();",
        "function resolveReanalyzeApiUrl() {",
        "  const fallbackPath = REANALYZE_API_PATH || '/api/reanalyze';",
        "  const configuredUrl = REANALYZE_API_URL || fallbackPath;",
        "  try {",
        "    const origin = String((window.location && window.location.origin) || '').trim();",
        "    if (!origin) return configuredUrl;",
        "    if (/^https?:\\/\\//i.test(configuredUrl)) {",
        "      const target = new URL(configuredUrl);",
        "      if (target.origin !== origin) {",
        "        return fallbackPath;",
        "      }",
        "    }",
        "  } catch (error) {}",
        "  return configuredUrl;",
        "}",
        "function resolveNeedLoginUrl(needLoginUrl) {",
        "  const text = String(needLoginUrl || '').trim();",
        "  if (!text) return '';",
        "  if (/^https?:\\/\\//i.test(text)) return text;",
        "  const baseUrl = String(NEED_LOGIN_BASE_URL || '').trim();",
        "  if (!baseUrl) return text;",
        "  if (text.startsWith('/')) return `${baseUrl}${text}`;",
        "  return `${baseUrl}/${text.replace(/^\\/+/, '')}`;",
        "}",
        "function setReanalyzeStatus(button, text, state) {",
        '  const status = button.parentElement ? button.parentElement.querySelector(".reanalyze-status") : null;',
        "  if (!status) return;",
        "  status.textContent = text || '';",
        '  status.classList.remove("is-success", "is-error");',
        "  if (state) {",
        "    status.classList.add(state);",
        "  }",
        "}",
        "function setReanalyzeNeedLoginHint(button, needLoginUrl) {",
        '  const item = button.closest(".item");',
        "  if (!item) return;",
        '  const hint = item.querySelector(".reanalyze-login-hint");',
        "  if (!hint) return;",
        "  hint.replaceChildren();",
        "  const resolvedUrl = resolveNeedLoginUrl(needLoginUrl);",
        "  if (!resolvedUrl) return;",
        '  const label = document.createElement("div");',
        '  label.className = "label";',
        '  label.textContent = "元宝登录二维码：";',
        '  const link = document.createElement("a");',
        '  link.className = "reanalyze-login-link";',
        "  link.href = resolvedUrl;",
        '  link.target = "_blank";',
        '  link.rel = "noopener noreferrer";',
        '  link.textContent = "打开登录二维码";',
        '  const wrap = document.createElement("div");',
        '  wrap.className = "reanalyze-login-image-wrap";',
        '  const image = document.createElement("img");',
        '  image.className = "reanalyze-login-image";',
        "  image.src = resolvedUrl;",
        '  image.alt = "元宝登录二维码";',
        '  image.loading = "lazy";',
        "  wrap.appendChild(image);",
        "  hint.appendChild(label);",
        "  hint.appendChild(link);",
        "  hint.appendChild(wrap);",
        "}",
        "function getRelatedReanalyzeButtons(button) {",
        '  const articleId = button.getAttribute("data-article-id") || "";',
        '  return Array.from(document.querySelectorAll(".reanalyze-button")).filter((candidate) => {',
        '    return (candidate.getAttribute("data-article-id") || "") === articleId;',
        "  });",
        "}",
        "function getNeedLoginReanalyzeRetryKey(button, provider) {",
        '  const articleId = button.getAttribute("data-article-id") || "";',
        "  return `${articleId}:${String(provider || '').trim()}`;",
        "}",
        "function clearNeedLoginReanalyzeRetry(button, provider) {",
        "  const key = getNeedLoginReanalyzeRetryKey(button, provider);",
        "  const timer = needLoginReanalyzeRetryTimers.get(key);",
        "  if (!timer) return;",
        "  window.clearTimeout(timer);",
        "  needLoginReanalyzeRetryTimers.delete(key);",
        "}",
        "function queueNeedLoginReanalyzeRetry(button, requestPayload, attempt) {",
        "  const provider = String((requestPayload || {}).provider || '').trim();",
        "  if (provider !== 'yuanbao') return;",
        "  clearNeedLoginReanalyzeRetry(button, provider);",
        "  if (attempt >= REANALYZE_NEED_LOGIN_RETRY_MAX_ATTEMPTS) {",
        '    setReanalyzeStatus(button, "扫码后仍未检测到登录生效，请稍后手动重试", "is-error");',
        "    return;",
        "  }",
        "  const nextAttempt = attempt + 1;",
        "  const key = getNeedLoginReanalyzeRetryKey(button, provider);",
        "  const timer = window.setTimeout(() => {",
        "    needLoginReanalyzeRetryTimers.delete(key);",
        "    triggerReanalyzeRequest(button, requestPayload, nextAttempt);",
        "  }, REANALYZE_NEED_LOGIN_RETRY_DELAY_MS);",
        "  needLoginReanalyzeRetryTimers.set(key, timer);",
        "}",
        "function setReanalyzeBusyState(button, busy) {",
        "  const relatedButtons = getRelatedReanalyzeButtons(button);",
        "  relatedButtons.forEach((candidate) => {",
        "    candidate.disabled = busy;",
        '    candidate.classList.toggle("is-busy", busy);',
        "  });",
        "}",
        "async function triggerReanalyzeRequest(button, requestPayload, needLoginRetryAttempt) {",
        "  const payloadBody = requestPayload || {};",
        "  const articleId = String(payloadBody.article_id || '').trim();",
        "  const url = String(payloadBody.url || '').trim();",
        "  const provider = String(payloadBody.provider || '').trim();",
        "  const providerMessages = REANALYZE_PROVIDER_MESSAGES[provider] || {",
        '    label: "指定解读",',
        '    pending: "指定解读中...",',
        '    success: "指定解读成功，正在刷新...",',
        '    error: "指定解读失败，请稍后重试",',
        "  };",
        "  if (!url) {",
        '    setReanalyzeStatus(button, "缺少原文链接，无法重解读");',
        "    return;",
        "  }",
        "  if (!provider) {",
        '    setReanalyzeStatus(button, "缺少 provider，无法重解读", "is-error");',
        "    return;",
        "  }",
        "  setReanalyzeBusyState(button, true);",
        "  setReanalyzeStatus(button, providerMessages.pending);",
        "  if (!needLoginRetryAttempt) {",
        "    setReanalyzeNeedLoginHint(button, '');",
        "  }",
        "  try {",
        "    const response = await fetch(resolveReanalyzeApiUrl(), {",
        '      method: "POST",',
        '      headers: {"Content-Type": "application/json"},',
        "      body: JSON.stringify({article_id, url, provider, provider_label: payloadBody.provider_label || providerMessages.label, provider_action_text: payloadBody.provider_action_text || providerMessages.pending}),",
        "    });",
        "    const payload = await response.json();",
        "    if (payload && (payload.reason === 'need_login' || payload.need_login)) {",
        "      setReanalyzeNeedLoginHint(button, payload.needLoginUrl || payload.need_login_url || '');",
        '      setReanalyzeStatus(button, "需要扫码登录元宝，等待登录后自动重试...", "is-error");',
        "      setReanalyzeBusyState(button, false);",
        "      queueNeedLoginReanalyzeRetry(button, payloadBody, Number(needLoginRetryAttempt || 0));",
        "      return;",
        "    }",
        "    clearNeedLoginReanalyzeRetry(button, provider);",
        "    if (!response.ok || payload.status !== 'ok') {",
        "      throw new Error('reanalyze_failed');",
        "    }",
        '    setReanalyzeStatus(button, providerMessages.success, "is-success");',
        "    window.setTimeout(() => window.location.reload(), 800);",
        "  } catch (error) {",
        "    clearNeedLoginReanalyzeRetry(button, provider);",
        '    setReanalyzeStatus(button, providerMessages.error, "is-error");',
        "    setReanalyzeBusyState(button, false);",
        "  }",
        "}",
        'document.querySelectorAll(".reanalyze-button").forEach((button) => {',
        "  if (button.disabled) return;",
        '  button.addEventListener("click", async () => {',
        '    const articleId = button.getAttribute("data-article-id") || "";',
        '    const url = button.getAttribute("data-url") || "";',
        '    const provider = button.getAttribute("data-provider") || "";',
        "    const providerMessages = REANALYZE_PROVIDER_MESSAGES[provider] || {",
        '      label: "指定解读",',
        '      pending: "指定解读中...",',
        '      success: "指定解读成功，正在刷新...",',
        '      error: "指定解读失败，请稍后重试",',
        "    };",
        "    if (!url) {",
        '      setReanalyzeStatus(button, "缺少原文链接，无法重解读");',
        "      return;",
        "    }",
        "    if (!provider) {",
        '      setReanalyzeStatus(button, "缺少 provider，无法重解读", "is-error");',
        "      return;",
        "    }",
        "    clearNeedLoginReanalyzeRetry(button, provider);",
        "    const requestPayload = {",
        "      article_id: articleId,",
        "      url,",
        "      provider,",
        "      provider_label: providerMessages.label,",
        "      provider_action_text: providerMessages.pending,",
        "    };",
        "    triggerReanalyzeRequest(button, requestPayload, 0);",
        "  });",
        "});",
        "</script>",
    ]


def _render_directory_fetch_skeleton_html() -> str:
    return (
        '<div class="directory-actions">'
        '<button type="button" class="fetch-latest-button">立即抓取</button>'
        '<span class="fetch-latest-status">点击后触发目录页立即抓取</span>'
        "</div>"
    )


def _render_directory_fetch_script_html():
    messages = {
        "pending": "立即抓取中...",
        "queued": "抓取任务已启动，后台处理中...",
        "success": "抓取成功，正在刷新...",
        "error": "立即抓取失败，请稍后重试",
        "busy": "已有抓取任务进行中，请稍后再试",
    }
    return [
        "<script>",
        'const FETCH_LATEST_ALL_API_PATH = "/api/fetch-latest-all";',
        'const FETCH_LATEST_ALL_STATUS_API_PATH = "/api/fetch-latest-all/status";',
        f"const FETCH_LATEST_MESSAGES = {json.dumps(messages, ensure_ascii=False)};",
        "function setFetchLatestStatus(text, state) {",
        '  const status = document.querySelector(".fetch-latest-status");',
        "  if (!status) return;",
        "  status.textContent = text || '';",
        '  status.classList.remove("is-success", "is-error");',
        "  if (state) {",
        "    status.classList.add(state);",
        "  }",
        "}",
        'const fetchLatestButton = document.querySelector(".fetch-latest-button");',
        "let fetchLatestStatusPollTimer = 0;",
        "function queueFetchLatestStatusPoll(delayMs) {",
        "  if (fetchLatestStatusPollTimer) {",
        "    window.clearTimeout(fetchLatestStatusPollTimer);",
        "  }",
        "  fetchLatestStatusPollTimer = window.setTimeout(pollFetchLatestAllStatus, delayMs);",
        "}",
        "async function pollFetchLatestAllStatus() {",
        "  try {",
        "    const response = await fetch(FETCH_LATEST_ALL_STATUS_API_PATH, {",
        '      headers: { "Accept": "application/json" }',
        "    });",
        "    const payload = await response.json();",
        "    if (!response.ok || !payload || payload.status !== 'ok') {",
        "      throw new Error('fetch_latest_status_failed');",
        "    }",
        "    if (payload.state === 'running') {",
        "      setFetchLatestStatus(FETCH_LATEST_MESSAGES.queued);",
        "      queueFetchLatestStatusPoll(1500);",
        "      return;",
        "    }",
        '    setFetchLatestStatus(FETCH_LATEST_MESSAGES.success, "is-success");',
        "    window.setTimeout(() => {",
        "      window.location.reload();",
        "    }, 800);",
        "  } catch (error) {",
        "    queueFetchLatestStatusPoll(3000);",
        "  }",
        "}",
        "async function triggerFetchLatestAll() {",
        "  if (!fetchLatestButton || fetchLatestButton.disabled) return;",
        '  fetchLatestButton.disabled = true;',
        '  fetchLatestButton.classList.add("is-busy");',
        "  setFetchLatestStatus(FETCH_LATEST_MESSAGES.pending);",
        "  try {",
        "    const response = await fetch(FETCH_LATEST_ALL_API_PATH, {",
        '      method: "POST",',
        '      headers: { "Content-Type": "application/json" },',
        '      body: JSON.stringify({ trigger: "directory_button" })',
        "    });",
        "    const payload = await response.json();",
        "    if (!response.ok || !payload || payload.status !== 'ok') {",
        "      if (payload && payload.reason === 'busy') {",
        '        setFetchLatestStatus(FETCH_LATEST_MESSAGES.busy, "is-error");',
        "      } else {",
        '        setFetchLatestStatus(FETCH_LATEST_MESSAGES.error, "is-error");',
        "      }",
        '      fetchLatestButton.disabled = false;',
        '      fetchLatestButton.classList.remove("is-busy");',
        "      return;",
        "    }",
        "    if (payload.reason === 'scheduled_async') {",
        "      setFetchLatestStatus(FETCH_LATEST_MESSAGES.queued);",
        "      queueFetchLatestStatusPoll(1000);",
        "      return;",
        "    }",
        '    setFetchLatestStatus(FETCH_LATEST_MESSAGES.success, "is-success");',
        "    window.setTimeout(() => {",
        "      window.location.reload();",
        "    }, 800);",
        "  } catch (error) {",
        '    setFetchLatestStatus(FETCH_LATEST_MESSAGES.error, "is-error");',
        '    fetchLatestButton.disabled = false;',
        '    fetchLatestButton.classList.remove("is-busy");',
        "  }",
        "}",
        "if (fetchLatestButton) {",
        '  fetchLatestButton.addEventListener("click", triggerFetchLatestAll);',
        "}",
        "</script>",
    ]


def _render_history_summary_label(item: dict) -> str:
    date_text = _normalize_scalar_string(item.get("published_at")) or _normalize_scalar_string(item.get("date"))
    if not date_text:
        date_text = _format_latest_time(item)
    title = _normalize_scalar_string(item.get("title")) or "(无标题)"
    return f"{date_text}｜{title}" if date_text else title


def _render_account_page_html(
    config,
    analysis_dir: Path,
    account: str,
    sorted_items,
    generated_at: str,
    page_relative_path: Optional[str] = None,
) -> str:
    if not sorted_items:
        return ""
    latest_item = sorted_items[0]
    latest_time = _format_latest_time(latest_item)
    page_parts = _render_page_start(f"{account} - 公众号 AI 解读")
    count = len(sorted_items)
    subtitle = f"生成时间：{generated_at} ｜ 篇数：{count}"
    if latest_time:
        subtitle = f"{subtitle} ｜ 最新：{latest_time}"
    page_parts.extend(
        [
            '<a class="back-link" href="../index.html">返回目录</a>',
            f"<h1>{html_escape(account)}</h1>",
            f'<div class="subtitle">{html_escape(subtitle)}</div>',
            _render_analysis_item_html(latest_item, config=config),
        ]
    )
    history = sorted_items[1:]
    if history:
        page_parts.append('<div class="history-title">历史文章</div>')
        for item in history:
            page_parts.append("<details>")
            page_parts.append(f"<summary>{html_escape(_render_history_summary_label(item))}</summary>")
            page_parts.append(_render_analysis_item_html(item, config=config))
            page_parts.append("</details>")
    page_parts.extend(_render_reanalyze_script_html(config))
    page_parts.extend(["</body>", "</html>"])
    content = "\n".join(page_parts) + "\n"
    relative_path = _normalize_scalar_string(page_relative_path) or _account_page_relative_path(account)
    page_path = _account_page_path(analysis_dir, account, relative_path)
    if not _safe_write_text(page_path, content):
        print(f"{_now_text()} failed to write account analysis html: {page_path}")
    return content


def _cleanup_stale_account_pages(analysis_dir: Path, active_relative_paths):
    accounts_dir = analysis_dir / "accounts"
    active_names = {
        Path(str(relative_path)).name
        for relative_path in (active_relative_paths or [])
        if str(relative_path).strip()
    }
    try:
        existing_paths = list(accounts_dir.glob("*.html"))
    except OSError:
        return
    for path in existing_paths:
        if path.name in active_names:
            continue
        try:
            path.unlink()
        except OSError:
            continue


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
            "need_login": bool(data.get("need_login"))
            or _normalize_scalar_string(data.get("reason")) == "need_login",
            "needLoginUrl": _normalize_scalar_string(
                data.get("needLoginUrl") or data.get("need_login_url")
            ),
            "summary": _normalize_summary_text(data.get("summary")),
            "topic": _normalize_scalar_string(data.get("topic")),
            "audience": _normalize_scalar_string(data.get("audience")),
            "core_points": _normalize_list(data.get("core_points")),
            "risks": _normalize_list(data.get("risks")),
            "_mtime": mtime,
            "_sort_key": _analysis_sort_key_with_mtime(date_text, mtime, path.name),
        }
        if item["status"] == "ok":
            item["reason"] = ""
            item["need_login"] = False
            item["needLoginUrl"] = ""
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
    account_page_paths = _resolve_account_page_relative_paths(
        [account for account, _latest_key, _sorted_items in account_entries]
    )

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
                "page_href": account_page_paths.get(account, _account_page_relative_path(account)),
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
    active_account_pages = [
        account_page_paths.get(account, _account_page_relative_path(account))
        for account, _latest_key, _sorted_items in account_entries
    ]
    _cleanup_stale_account_pages(analysis_dir, active_account_pages)

    html_parts = _render_page_start("公众号 AI 解读汇总", extra_style_lines=_directory_fetch_style_lines())
    html_parts.extend(
        [
            "<h1>公众号 AI 解读汇总</h1>",
            f'<div class="subtitle">生成时间：{html_escape(generated_at)} ｜ 账号：{total_accounts} ｜ 解读：{total_analyses}</div>',
            _render_directory_fetch_skeleton_html(),
        ]
    )

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
                html_parts.append(
                    (
                        f'<a class="directory-link" href="{html_escape(entry["page_href"])}">'
                        f'<div class="directory-account">{html_escape(entry["account"])}</div>'
                        f'<div class="directory-count">{entry["count"]} 篇</div>'
                        f'<div class="directory-latest-time">{html_escape(entry["latest_time"])}</div>'
                        f'<div class="directory-latest-title">{html_escape(entry["latest_title"])}</div>'
                        "</a>"
                    )
                )
            html_parts.append("</div>")
            html_parts.append("</div>")
        html_parts.append("</div>")

    for account, _latest_key, sorted_items in account_entries:
        _render_account_page_html(
            config,
            analysis_dir,
            account,
            sorted_items,
            generated_at,
            account_page_paths.get(account),
        )

    html_parts.extend(_render_directory_fetch_script_html())
    html_parts.extend(["</body>", "</html>"])
    content = "\n".join(html_parts) + "\n"
    if not _safe_write_text(analysis_dir / "index.html", content):
        print(f"{_now_text()} failed to write analysis index html: {analysis_dir / 'index.html'}")
    return content

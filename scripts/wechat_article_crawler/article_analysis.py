import hashlib
import json
import os
from pathlib import Path

import requests


DEFAULT_ANALYSIS_CONFIG = {
    "analysis_enabled": False,
    "analysis_push_batch": True,
    "analysis_base_url": "http://192.168.9.158:11434",
    "analysis_model": "qwen2.5-coder:14b-cpu",
    "analysis_timeout_seconds": 30,
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
    merged["analysis_base_url"] = _resolve_analysis_base_url(explicit.get("analysis_base_url"))
    merged["analysis_model"] = _resolve_analysis_model(explicit.get("analysis_model"))
    return merged


def get_analysis_output_root(config) -> Path:
    cfg = get_analysis_config(config)
    output_dir = cfg.get("analysis_output_dir")
    return Path(output_dir) if output_dir else Path("output")


def build_article_id(article) -> str:
    raw = "|".join(
        [
            str(article.get("url") or ""),
            str(article.get("account") or ""),
            str(article.get("published_at") or article.get("date") or ""),
            str(article.get("title") or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _truncate_markdown(markdown: str, max_chars: int) -> str:
    text = (markdown or "").strip()
    return text[: max(1, int(max_chars or 8000))]


def _article_cache_path(config, article_id: str) -> Path:
    return get_analysis_output_root(config) / "article_analysis" / f"{article_id}.json"


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


def _safe_write_text(path: Path, content: str):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def _build_single_article_prompt(article, cfg):
    payload = {
        "title": article.get("title", ""),
        "account": article.get("account", ""),
        "published_at": article.get("published_at") or article.get("date") or "",
        "url": article.get("url", ""),
        "markdown": _truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"]),
    }
    return (
        "你是微信公众号文章分析助手。请基于给定文章信息生成简洁中文解读。"
        "只输出 JSON，不要输出 Markdown、解释、代码块或额外文字。"
        "JSON 必须包含字段：\"topic\"(字符串), \"core_points\"(字符串数组), "
        "\"audience\"(字符串), \"risks\"(字符串数组)。"
        "如果信息不足，请保持字段存在并用简短内容说明信息有限。\n"
        f"文章输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _build_batch_prompt(analyses):
    return (
        "你是微信公众号批量解读助手。请基于多篇文章的单篇解读生成本轮汇总。"
        "只输出 JSON，不要输出 Markdown、解释、代码块或额外文字。"
        "JSON 必须包含字段：\"batch_focus\"(字符串), \"shared_themes\"(字符串数组), "
        "\"priority_reads\"(字符串数组)。"
        "优先总结共性主题和最值得优先阅读的文章。\n"
        f"输入数据：{json.dumps({'articles': analyses}, ensure_ascii=False)}"
    )


def render_single_analysis_markdown(analysis):
    if not isinstance(analysis, dict) or analysis.get("status") != "ok":
        return ""
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
    shared_themes = "；".join(_normalize_list(batch_analysis.get("shared_themes"))) or "无"
    priority_reads = "；".join(_normalize_list(batch_analysis.get("priority_reads"))) or "无"
    lines = [
        "## 本轮解读",
        f"- 本轮重点：{_normalize_scalar_string(batch_analysis.get('batch_focus')) or '无'}",
        f"- 共性观点：{shared_themes}",
        f"- 优先阅读：{priority_reads}",
    ]
    return "\n".join(lines)


def persist_single_analysis_outputs(config, analysis):
    if not isinstance(analysis, dict):
        return
    article_id = _normalize_scalar_string(analysis.get("article_id"))
    if not article_id:
        return
    cfg = get_analysis_config(config)
    root = get_analysis_output_root(config) / "article_analysis"
    if cfg.get("analysis_save_json"):
        _safe_write_text(root / f"{article_id}.json", json.dumps(analysis, ensure_ascii=False, indent=2))
    if cfg.get("analysis_save_markdown"):
        body = render_single_analysis_markdown(analysis)
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
    return {
        "status": "ok",
        "topic": _normalize_scalar_string(data.get("topic")),
        "core_points": _normalize_list(data.get("core_points")),
        "audience": _normalize_scalar_string(data.get("audience")),
        "risks": _normalize_list(data.get("risks")),
    }


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

    prompt = _build_single_article_prompt(article, cfg)

    try:
        result = _parse_single_analysis(call_ollama_chat(config, prompt))
    except requests.Timeout:
        return {"status": "skipped", "reason": "ollama_timeout", "article_id": article_id}
    except Exception as exc:
        return {"status": "skipped", "reason": f"ollama_error:{exc}", "article_id": article_id}

    result.update(
        {
            "article_id": article_id,
            "account": article.get("account", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
        }
    )

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
        data = json.loads(call_ollama_chat(config, _build_batch_prompt(ok_items)))
    except requests.Timeout:
        return {"status": "skipped", "reason": "ollama_timeout", "batch_id": batch_id}
    except Exception as exc:
        return {"status": "skipped", "reason": f"ollama_error:{exc}", "batch_id": batch_id}

    return {
        "status": "ok",
        "batch_id": batch_id,
        "batch_focus": _normalize_scalar_string(data.get("batch_focus")),
        "shared_themes": _normalize_list(data.get("shared_themes")),
        "priority_reads": _normalize_list(data.get("priority_reads")),
    }

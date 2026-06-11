import hashlib
import json
from pathlib import Path

import requests


DEFAULT_ANALYSIS_CONFIG = {
    "analysis_enabled": True,
    "analysis_base_url": "http://192.168.9.158:11434",
    "analysis_model": "qwen2.5-coder:14b-cpu",
    "analysis_timeout_seconds": 30,
    "analysis_max_chars": 8000,
    "analysis_save_json": True,
    "analysis_save_markdown": True,
    "analysis_skip_if_exists": True,
}


def get_analysis_config(config):
    merged = dict(DEFAULT_ANALYSIS_CONFIG)
    if isinstance(config, dict):
        merged.update({key: value for key, value in config.items() if key.startswith("analysis_")})
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


def _parse_single_analysis(content: str):
    data = json.loads(content)
    return {
        "status": "ok",
        "topic": data.get("topic", ""),
        "core_points": list(data.get("core_points") or []),
        "audience": data.get("audience", ""),
        "risks": list(data.get("risks") or []),
    }


def call_ollama_chat(config, prompt: str):
    cfg = get_analysis_config(config)
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


def analyze_single_article(config, article):
    cfg = get_analysis_config(config)
    article_id = build_article_id(article)
    cache_path = _article_cache_path(config, article_id)

    if not cfg.get("analysis_enabled"):
        return {"status": "skipped", "reason": "analysis_disabled", "article_id": article_id}

    if cfg.get("analysis_skip_if_exists") and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    prompt = json.dumps(
        {
            "title": article.get("title", ""),
            "account": article.get("account", ""),
            "published_at": article.get("published_at") or article.get("date") or "",
            "url": article.get("url", ""),
            "markdown": _truncate_markdown(article.get("markdown", ""), cfg["analysis_max_chars"]),
        },
        ensure_ascii=False,
    )

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

    if cfg.get("analysis_save_json", True):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def summarize_analysis_batch(config, analyses, batch_id: str):
    cfg = get_analysis_config(config)
    if not cfg.get("analysis_enabled"):
        return {"status": "skipped", "reason": "analysis_disabled", "batch_id": batch_id}

    ok_items = [item for item in analyses if item.get("status") == "ok"]
    if not ok_items:
        return {"status": "skipped", "reason": "no_article_analysis", "batch_id": batch_id}

    try:
        data = json.loads(call_ollama_chat(config, json.dumps({"articles": ok_items}, ensure_ascii=False)))
    except requests.Timeout:
        return {"status": "skipped", "reason": "ollama_timeout", "batch_id": batch_id}
    except Exception as exc:
        return {"status": "skipped", "reason": f"ollama_error:{exc}", "batch_id": batch_id}

    return {
        "status": "ok",
        "batch_id": batch_id,
        "batch_focus": data.get("batch_focus", ""),
        "shared_themes": list(data.get("shared_themes") or []),
        "priority_reads": list(data.get("priority_reads") or []),
    }

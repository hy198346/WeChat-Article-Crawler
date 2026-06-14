import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path

import article_analysis
import requests
import wechat_crawler


class TestArticleAnalysis(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            key: os.environ.get(key)
            for key in (
                "LOCAL_LLM_BASE_URL",
                "OLLAMA_BASE_URL",
                "LOCAL_LLM_MODEL",
                "OLLAMA_MODEL",
                "NEWS_INTERPRET_BASE_URL",
                "WECHAT_ANALYSIS_PUBLIC_BASE_URL",
                "WECHAT_ANALYSIS_REANALYZE_PATH",
                "WECHAT_ENV_FILE",
            )
        }
        for key in self._env_backup:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_get_analysis_config_merges_defaults(self):
        cfg = article_analysis.get_analysis_config(
            {"analysis_enabled": False, "analysis_timeout_seconds": 9}
        )
        self.assertFalse(cfg["analysis_enabled"])
        self.assertTrue(cfg["analysis_push_batch"])
        self.assertEqual(cfg["analysis_timeout_seconds"], 9)
        self.assertEqual(cfg["analysis_model"], "qwen2.5-coder:14b-cpu")
        self.assertEqual(cfg["analysis_base_url"], "http://192.168.9.158:11434")

    def test_get_analysis_config_defaults_disable_ai(self):
        cfg = article_analysis.get_analysis_config({})

        self.assertFalse(cfg["analysis_enabled"])
        self.assertTrue(cfg["analysis_push_batch"])
        self.assertEqual(cfg["analysis_base_url"], "http://192.168.9.158:11434")

    def test_get_analysis_config_merges_summary_and_public_reanalyze_defaults(self):
        cfg = article_analysis.get_analysis_config(
            {
                "analysis_enabled": True,
                "analysis_base_url": "http://10.0.0.2:11434",
                "analysis_model": "base:model",
                "analysis_timeout_seconds": 9,
                "analysis_public_base_url": "https://wx.coco777.vip",
            }
        )

        self.assertEqual(cfg["analysis_summary_base_url"], "http://10.0.0.2:11434")
        self.assertEqual(cfg["analysis_summary_model"], "base:model")
        self.assertEqual(cfg["analysis_summary_timeout_seconds"], 9)
        self.assertEqual(cfg["analysis_public_base_url"], "https://wx.coco777.vip")
        self.assertEqual(cfg["analysis_reanalyze_path"], "/api/reanalyze")

    def test_get_analysis_config_uses_summary_first_env_fallbacks(self):
        old_news = os.environ.get("NEWS_INTERPRET_BASE_URL")
        old_public = os.environ.get("WECHAT_ANALYSIS_PUBLIC_BASE_URL")
        old_reanalyze = os.environ.get("WECHAT_ANALYSIS_REANALYZE_PATH")
        try:
            os.environ["NEWS_INTERPRET_BASE_URL"] = "https://news.example.com"
            os.environ["WECHAT_ANALYSIS_PUBLIC_BASE_URL"] = "https://wx.coco777.vip/"
            os.environ["WECHAT_ANALYSIS_REANALYZE_PATH"] = "custom/reanalyze"

            cfg = article_analysis.get_analysis_config({"analysis_enabled": True})

            self.assertEqual(
                cfg["analysis_news_interpret_url"],
                "https://news.example.com/api/telegraph/interpret",
            )
            self.assertEqual(cfg["analysis_public_base_url"], "https://wx.coco777.vip")
            self.assertEqual(cfg["analysis_reanalyze_path"], "/custom/reanalyze")
        finally:
            if old_news is None:
                os.environ.pop("NEWS_INTERPRET_BASE_URL", None)
            else:
                os.environ["NEWS_INTERPRET_BASE_URL"] = old_news
            if old_public is None:
                os.environ.pop("WECHAT_ANALYSIS_PUBLIC_BASE_URL", None)
            else:
                os.environ["WECHAT_ANALYSIS_PUBLIC_BASE_URL"] = old_public
            if old_reanalyze is None:
                os.environ.pop("WECHAT_ANALYSIS_REANALYZE_PATH", None)
            else:
                os.environ["WECHAT_ANALYSIS_REANALYZE_PATH"] = old_reanalyze

    def test_get_analysis_config_uses_local_llm_base_url_when_missing_explicit(self):
        old_local = os.environ.get("LOCAL_LLM_BASE_URL")
        old_ollama = os.environ.get("OLLAMA_BASE_URL")
        try:
            os.environ["LOCAL_LLM_BASE_URL"] = "http://192.168.9.158:11434"
            os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
            cfg = article_analysis.get_analysis_config({"analysis_enabled": True, "analysis_base_url": ""})
            self.assertEqual(cfg["analysis_base_url"], "http://192.168.9.158:11434")
        finally:
            if old_local is None:
                os.environ.pop("LOCAL_LLM_BASE_URL", None)
            else:
                os.environ["LOCAL_LLM_BASE_URL"] = old_local
            if old_ollama is None:
                os.environ.pop("OLLAMA_BASE_URL", None)
            else:
                os.environ["OLLAMA_BASE_URL"] = old_ollama

    def test_get_analysis_config_uses_local_llm_base_url_when_key_absent(self):
        old_local = os.environ.get("LOCAL_LLM_BASE_URL")
        try:
            os.environ["LOCAL_LLM_BASE_URL"] = "http://192.168.9.158:11434/v1"
            cfg = article_analysis.get_analysis_config({"analysis_enabled": True})
            self.assertEqual(cfg["analysis_base_url"], "http://192.168.9.158:11434/v1")
        finally:
            if old_local is None:
                os.environ.pop("LOCAL_LLM_BASE_URL", None)
            else:
                os.environ["LOCAL_LLM_BASE_URL"] = old_local

    def test_get_analysis_config_prefers_explicit_base_url_over_env(self):
        old_local = os.environ.get("LOCAL_LLM_BASE_URL")
        try:
            os.environ["LOCAL_LLM_BASE_URL"] = "http://192.168.9.158:11434"
            cfg = article_analysis.get_analysis_config(
                {"analysis_enabled": True, "analysis_base_url": "http://10.0.0.2:11434"}
            )
            self.assertEqual(cfg["analysis_base_url"], "http://10.0.0.2:11434")
        finally:
            if old_local is None:
                os.environ.pop("LOCAL_LLM_BASE_URL", None)
            else:
                os.environ["LOCAL_LLM_BASE_URL"] = old_local

    def test_get_analysis_config_uses_local_llm_model_when_missing_explicit(self):
        old_local_model = os.environ.get("LOCAL_LLM_MODEL")
        old_ollama_model = os.environ.get("OLLAMA_MODEL")
        try:
            os.environ["LOCAL_LLM_MODEL"] = "qwen3:4b"
            os.environ["OLLAMA_MODEL"] = "ignored:model"
            cfg = article_analysis.get_analysis_config({"analysis_enabled": True, "analysis_model": ""})
            self.assertEqual(cfg["analysis_model"], "qwen3:4b")
        finally:
            if old_local_model is None:
                os.environ.pop("LOCAL_LLM_MODEL", None)
            else:
                os.environ["LOCAL_LLM_MODEL"] = old_local_model
            if old_ollama_model is None:
                os.environ.pop("OLLAMA_MODEL", None)
            else:
                os.environ["OLLAMA_MODEL"] = old_ollama_model

    def test_get_analysis_config_uses_local_llm_model_when_key_absent(self):
        old_local_model = os.environ.get("LOCAL_LLM_MODEL")
        try:
            os.environ["LOCAL_LLM_MODEL"] = "qwen3:4b"
            cfg = article_analysis.get_analysis_config({"analysis_enabled": True})
            self.assertEqual(cfg["analysis_model"], "qwen3:4b")
        finally:
            if old_local_model is None:
                os.environ.pop("LOCAL_LLM_MODEL", None)
            else:
                os.environ["LOCAL_LLM_MODEL"] = old_local_model

    def test_load_env_into_process_makes_local_llm_values_available(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = Path(d) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "LOCAL_LLM_BASE_URL=http://192.168.9.158:11434/v1",
                        "LOCAL_LLM_MODEL=qwen3:4b",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            root = Path(d)
            os.environ["WECHAT_ENV_FILE"] = str(env_path)

            wechat_crawler._load_env_into_process(root)

            cfg = article_analysis.get_analysis_config({"analysis_enabled": True})

            self.assertEqual(cfg["analysis_base_url"], "http://192.168.9.158:11434/v1")
            self.assertEqual(cfg["analysis_model"], "qwen3:4b")

    def test_get_analysis_config_prefers_explicit_model_over_env(self):
        old_local_model = os.environ.get("LOCAL_LLM_MODEL")
        try:
            os.environ["LOCAL_LLM_MODEL"] = "qwen3:4b"
            cfg = article_analysis.get_analysis_config(
                {"analysis_enabled": True, "analysis_model": "qwen2.5-coder:14b-cpu"}
            )
            self.assertEqual(cfg["analysis_model"], "qwen2.5-coder:14b-cpu")
        finally:
            if old_local_model is None:
                os.environ.pop("LOCAL_LLM_MODEL", None)
            else:
                os.environ["LOCAL_LLM_MODEL"] = old_local_model

    def test_build_article_id_hashes_untrusted_external_article_id(self):
        article_id = article_analysis.build_article_id({"article_id": "../../evil"})

        self.assertNotEqual(article_id, "../../evil")
        self.assertNotIn("/", article_id)
        self.assertNotIn("..", article_id)
        self.assertRegex(article_id, r"^[0-9a-f]{40}$")

    def test_analyze_single_article_success_persists_cache(self):
        calls = []

        def fake_post(url, json=None, timeout=0):
            calls.append((url, json, timeout))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": json_module.dumps(
                                {
                                    "topic": "市场情绪",
                                    "core_points": ["情绪回暖", "高位分化"],
                                    "audience": "短线交易者",
                                    "risks": ["样本有限"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }

            return Resp()

        json_module = json
        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                config = {
                    "analysis_enabled": True,
                    "analysis_base_url": "http://127.0.0.1:11434",
                    "analysis_model": "qwen2.5-coder:14b-cpu",
                    "analysis_timeout_seconds": 5,
                    "analysis_max_chars": 200,
                    "analysis_save_json": True,
                    "analysis_save_markdown": True,
                    "analysis_skip_if_exists": True,
                    "analysis_output_dir": d,
                }
                article = {
                    "account": "测试号",
                    "title": "测试标题",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/test",
                    "markdown": "# 标题\n\n正文内容",
                }

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topic"], "市场情绪")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][1]["model"], "qwen2.5-coder:14b-cpu")
                prompt = calls[0][1]["messages"][0]["content"]
                self.assertIn("只输出 JSON", prompt)
                self.assertIn("\"summary\"", prompt)
                self.assertNotIn("\"topic\"", prompt)
                self.assertNotIn("\"core_points\"", prompt)
                self.assertIn("测试标题", prompt)
                saved = Path(d) / "article_analysis" / f"{result['article_id']}.json"
                self.assertTrue(saved.exists())
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_persists_timeout_result_for_retry(self):
        old_call = article_analysis.call_ollama_chat
        try:
            def raise_timeout(config, prompt):
                raise requests.Timeout("timeout")

            article_analysis.call_ollama_chat = raise_timeout
            with tempfile.TemporaryDirectory() as d:
                config = {
                    "analysis_enabled": True,
                    "analysis_save_json": True,
                    "analysis_save_markdown": True,
                    "analysis_output_dir": d,
                }
                article = {
                    "account": "测试号",
                    "title": "超时文章",
                    "published_at": "2026-06-12 09:00",
                    "url": "https://mp.weixin.qq.com/s/timeout",
                    "markdown": "# 标题\n\n正文",
                }

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "ollama_timeout")
                saved = Path(d) / "article_analysis" / f"{result['article_id']}.json"
                self.assertTrue(saved.exists())
                saved_data = json.loads(saved.read_text(encoding="utf-8"))
                self.assertEqual(saved_data["title"], "超时文章")
                self.assertEqual(saved_data["url"], "https://mp.weixin.qq.com/s/timeout")
                self.assertEqual(saved_data["reason"], "ollama_timeout")
        finally:
            article_analysis.call_ollama_chat = old_call

    def test_call_ollama_chat_uses_openai_compat_for_v1_base_url(self):
        calls = []

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": "{\"topic\":\"兼容模式\",\"core_points\":[\"走 v1 接口\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                                }
                            }
                        ]
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            content = article_analysis.call_ollama_chat(
                {
                    "analysis_enabled": True,
                    "analysis_base_url": "http://192.168.9.158:11434/v1",
                    "analysis_model": "qwen3:4b",
                },
                "测试 prompt",
            )
            self.assertEqual(
                calls[0][0], "http://192.168.9.158:11434/v1/chat/completions"
            )
            self.assertEqual(calls[0][1]["temperature"], 0)
            self.assertEqual(calls[0][1]["messages"][0]["role"], "system")
            self.assertEqual(calls[0][1]["messages"][1]["role"], "user")
            self.assertEqual(calls[0][3]["Authorization"], "Bearer ollama")
            self.assertIn("兼容模式", content)
        finally:
            article_analysis.requests.post = old_post

    def test_call_ollama_chat_falls_back_to_openai_compat_when_api_chat_unsupported(self):
        calls = []

        class HttpError(requests.HTTPError):
            pass

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))
            if url.endswith("/api/chat"):
                class Resp405:
                    status_code = 405

                    def raise_for_status(self):
                        raise HttpError("405 Client Error: Method Not Allowed")

                return Resp405()

            class Resp200:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": "{\"topic\":\"自动回退\",\"core_points\":[\"改走 openai compat\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                                }
                            }
                        ]
                    }

            return Resp200()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            content = article_analysis.call_ollama_chat(
                {
                    "analysis_enabled": True,
                    "analysis_base_url": "http://192.168.9.158:11434",
                    "analysis_model": "qwen3:4b",
                },
                "测试 prompt",
            )
            self.assertEqual(calls[0][0], "http://192.168.9.158:11434/api/chat")
            self.assertEqual(
                calls[1][0], "http://192.168.9.158:11434/v1/chat/completions"
            )
            self.assertIn("自动回退", content)
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_timeout_returns_skipped(self):
        def fake_post(url, json=None, timeout=0):
            raise article_analysis.requests.Timeout("boom")

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                config = {"analysis_enabled": True, "analysis_output_dir": d}
                article = {
                    "title": "T",
                    "url": "https://mp.weixin.qq.com/s/1",
                    "markdown": "body",
                }
                result = article_analysis.analyze_single_article(config, article)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "ollama_timeout")
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_uses_existing_cache(self):
        with tempfile.TemporaryDirectory() as d:
            article = {
                "account": "测试号",
                "title": "缓存文章",
                "published_at": "2026-06-11 21:30",
                "url": "https://mp.weixin.qq.com/s/cache",
                "markdown": "body",
            }
            config = {
                "analysis_enabled": True,
                "analysis_output_dir": d,
                "analysis_skip_if_exists": True,
            }
            article_id = article_analysis.build_article_id(article)
            cache_dir = Path(d) / "article_analysis"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{article_id}.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": article_id,
                        "topic": "缓存命中",
                        "audience": "缓存读者",
                        "core_points": ["缓存观点"],
                        "risks": ["缓存风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = article_analysis.analyze_single_article(config, article)
            self.assertEqual(result["topic"], "缓存命中")

    def test_build_article_id_ignores_account_changes_for_same_article(self):
        article_a = {
            "account": "旧账号名",
            "title": "同一篇文章",
            "published_at": "2026-06-11 21:30",
            "url": "https://mp.weixin.qq.com/s/stable-article-id",
        }
        article_b = {
            "account": "新账号名",
            "title": "同一篇文章",
            "published_at": "2026-06-11 21:30",
            "url": "https://mp.weixin.qq.com/s/stable-article-id",
        }

        self.assertEqual(
            article_analysis.build_article_id(article_a),
            article_analysis.build_article_id(article_b),
        )

    def test_build_article_id_prefers_existing_article_id_over_mutated_fields(self):
        article_old = {
            "article_id": "stable-existing-id",
            "title": "旧标题",
            "published_at": "2026-06-11 21:30",
            "url": "https://mp.weixin.qq.com/s/old-stable-link",
        }
        article_new = {
            "article_id": "stable-existing-id",
            "title": "新标题",
            "published_at": "2026-06-12 08:00",
            "url": "https://mp.weixin.qq.com/s/new-stable-link",
        }

        self.assertEqual(
            article_analysis.build_article_id(article_old),
            "stable-existing-id",
        )
        self.assertEqual(
            article_analysis.build_article_id(article_old),
            article_analysis.build_article_id(article_new),
        )

    def test_analyze_single_article_uses_existing_cache_when_account_changes(self):
        def fail_post(url, json=None, timeout=0):
            raise AssertionError("命中了重复分析，说明 article_id 不稳定")

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fail_post
        try:
            with tempfile.TemporaryDirectory() as d:
                article_old = {
                    "account": "旧账号名",
                    "title": "缓存复用文章",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/reuse-cache",
                    "markdown": "body",
                }
                article_new = dict(article_old)
                article_new["account"] = "新账号名"
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_skip_if_exists": True,
                }
                article_id = article_analysis.build_article_id(article_old)
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{article_id}.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": article_id,
                            "account": "旧账号名",
                            "topic": "缓存命中",
                            "audience": "缓存读者",
                            "core_points": ["缓存观点"],
                            "risks": ["缓存风险"],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = article_analysis.analyze_single_article(config, article_new)

                self.assertEqual(result["topic"], "缓存命中")
                self.assertEqual(result["article_id"], article_id)
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_ignores_incomplete_ok_cache_and_reanalyzes(self):
        calls = []

        def fake_post(url, json=None, timeout=0):
            calls.append((url, json, timeout))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":\"残缺缓存后重跑\",\"core_points\":[\"重新分析\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                article = {
                    "account": "测试号",
                    "title": "残缺缓存文章",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/incomplete-cache",
                    "markdown": "body",
                }
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_skip_if_exists": True,
                }
                article_id = article_analysis.build_article_id(article)
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{article_id}.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": article_id,
                            "topic": "残缺缓存",
                            "core_points": ["缺少 audience 和 risks"],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topic"], "残缺缓存后重跑")
                self.assertEqual(len(calls), 1)
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_ignores_empty_ok_cache_and_reanalyzes(self):
        old_local = article_analysis.call_ollama_chat
        article_analysis.call_ollama_chat = (
            lambda config, prompt: '{"summary":"重新生成的有效总结"}'
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                article = {
                    "account": "测试号",
                    "title": "空缓存文章",
                    "published_at": "2026-06-13 10:15",
                    "url": "https://mp.weixin.qq.com/s/empty-ok-cache",
                    "markdown": "body",
                }
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_skip_if_exists": True,
                    "analysis_news_interpret_url": "",
                }
                article_id = article_analysis.build_article_id(article)
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{article_id}.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": article_id,
                            "account": "测试号",
                            "title": "空缓存文章",
                            "url": "https://mp.weixin.qq.com/s/empty-ok-cache",
                            "published_at": "2026-06-13 10:15",
                            "topic": "",
                            "core_points": [],
                            "audience": "",
                            "risks": [],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "重新生成的有效总结")
                self.assertEqual(result["source"], "local")
        finally:
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_ignores_bad_cache_and_reanalyzes(self):
        calls = []

        def fake_post(url, json=None, timeout=0):
            calls.append((url, json, timeout))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":\"缓存修复后重跑\",\"core_points\":[\"重新分析\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                article = {
                    "account": "测试号",
                    "title": "坏缓存文章",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/bad-cache",
                    "markdown": "body",
                }
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_skip_if_exists": True,
                }
                article_id = article_analysis.build_article_id(article)
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{article_id}.json").write_text("{bad json", encoding="utf-8")

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topic"], "缓存修复后重跑")
                self.assertEqual(len(calls), 1)
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_ignores_invalid_cached_dict_and_reanalyzes(self):
        calls = []

        def fake_post(url, json=None, timeout=0):
            calls.append((url, json, timeout))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":\"结构坏缓存后重跑\",\"core_points\":[\"重新分析\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                article = {
                    "account": "测试号",
                    "title": "结构坏缓存文章",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/invalid-cache",
                    "markdown": "body",
                }
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_skip_if_exists": True,
                }
                article_id = article_analysis.build_article_id(article)
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{article_id}.json").write_text(
                    json.dumps(
                        {"status": "ok", "article_id": article_id, "topic": ["错误类型"]},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topic"], "结构坏缓存后重跑")
                self.assertEqual(len(calls), 1)
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_write_failure_does_not_interrupt(self):
        def fake_post(url, json=None, timeout=0):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":\"写失败也返回\",\"core_points\":[\"继续主流程\"],\"audience\":\"测试者\",\"risks\":[\"无\"]}"
                        }
                    }

            return Resp()

        def fake_write_text(self, data, encoding=None):
            raise OSError("disk full")

        old_post = article_analysis.requests.post
        old_write_text = article_analysis.Path.write_text
        article_analysis.requests.post = fake_post
        article_analysis.Path.write_text = fake_write_text
        try:
            with tempfile.TemporaryDirectory() as d:
                config = {
                    "analysis_enabled": True,
                    "analysis_output_dir": d,
                    "analysis_save_json": True,
                }
                article = {
                    "account": "测试号",
                    "title": "写失败文章",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/write-fail",
                    "markdown": "body",
                }

                result = article_analysis.analyze_single_article(config, article)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["topic"], "写失败也返回")
        finally:
            article_analysis.requests.post = old_post
            article_analysis.Path.write_text = old_write_text

    def test_analyze_single_article_prefers_news_interpret_summary(self):
        calls = []

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"ok": True, "analysis": "元宝总结结果"}

            return Resp()

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        article_analysis.requests.post = fake_post
        article_analysis.call_ollama_chat = lambda config, prompt: self.fail("命中本地兜底，说明 summary-first 未生效")
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                        "analysis_output_dir": d,
                    },
                    {
                        "account": "测试号",
                        "title": "测试标题",
                        "published_at": "2026-06-13 11:00",
                        "url": "https://mp.weixin.qq.com/s/test",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "元宝总结结果")
                self.assertEqual(result["source"], "yuanbao")
                self.assertEqual(result["topic"], "")
                self.assertEqual(
                    calls[0][0], "https://news.example.com/api/telegraph/interpret"
                )
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_uses_news_interpret_env_fallback(self):
        calls = []

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"ok": True, "analysis": "环境变量总结"}

            return Resp()

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        article_analysis.requests.post = fake_post
        article_analysis.call_ollama_chat = lambda config, prompt: self.fail("不应回退本地分析")
        os.environ["NEWS_INTERPRET_BASE_URL"] = "https://news-env.example.com"
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {"analysis_enabled": True, "analysis_output_dir": d},
                    {
                        "account": "测试号",
                        "title": "环境变量标题",
                        "published_at": "2026-06-13 11:10",
                        "url": "https://mp.weixin.qq.com/s/env-fallback",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "环境变量总结")
                self.assertEqual(
                    calls[0][0], "https://news-env.example.com/api/telegraph/interpret"
                )
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_force_yuanbao_timeout_does_not_fallback_to_local(self):
        def fake_post(url, json=None, timeout=0, headers=None):
            raise article_analysis.requests.Timeout("boom")

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        article_analysis.requests.post = fake_post
        article_analysis.call_ollama_chat = lambda config, prompt: self.fail(
            "强制元宝模式不应回退到本地模型"
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_force_provider": "yuanbao",
                        "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                        "analysis_output_dir": d,
                    },
                    {
                        "account": "测试号",
                        "title": "强制元宝超时",
                        "published_at": "2026-06-14 09:00",
                        "url": "https://mp.weixin.qq.com/s/force-yuanbao-timeout",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "news_interpret_timeout")
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_force_yuanbao_success_uses_remote_only(self):
        calls = []

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"ok": True, "analysis": "只走元宝成功"}

            return Resp()

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        article_analysis.requests.post = fake_post
        article_analysis.call_ollama_chat = lambda config, prompt: self.fail(
            "强制元宝成功时不应触发本地模型"
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_force_provider": "yuanbao",
                        "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                        "analysis_output_dir": d,
                    },
                    {
                        "account": "测试号",
                        "title": "强制元宝成功",
                        "published_at": "2026-06-14 09:05",
                        "url": "https://mp.weixin.qq.com/s/force-yuanbao-success",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "只走元宝成功")
                self.assertEqual(result["source"], "yuanbao")
                self.assertEqual(
                    calls[0][0], "https://news.example.com/api/telegraph/interpret"
                )
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_force_ollama_skips_remote_news_interpret(self):
        def fail_post(url, json=None, timeout=0, headers=None):
            raise AssertionError("强制本地模式不应请求元宝接口")

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        article_analysis.requests.post = fail_post
        article_analysis.call_ollama_chat = (
            lambda config, prompt: '{"summary":"仅本地模型输出"}'
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_force_provider": "ollama",
                        "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                        "analysis_output_dir": d,
                    },
                    {
                        "account": "测试号",
                        "title": "强制本地模型",
                        "published_at": "2026-06-14 09:10",
                        "url": "https://mp.weixin.qq.com/s/force-ollama-only",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "仅本地模型输出")
                self.assertEqual(result["source"], "local")
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_force_provider_ignores_existing_cache(self):
        article = {
            "account": "测试号",
            "title": "强制 provider 忽略缓存",
            "published_at": "2026-06-14 10:00",
            "url": "https://mp.weixin.qq.com/s/force-provider-ignore-cache",
            "markdown": "# 正文",
        }
        article_id = article_analysis.build_article_id(article)
        cached = {
            "status": "ok",
            "article_id": article_id,
            "account": "测试号",
            "title": "旧缓存标题",
            "url": article["url"],
            "published_at": article["published_at"],
            "date": "",
            "summary": "旧缓存结果",
            "topic": "",
            "core_points": [],
            "audience": "",
            "risks": [],
            "source": "cache",
        }

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        try:
            for provider in ("yuanbao", "ollama"):
                with self.subTest(provider=provider):
                    with tempfile.TemporaryDirectory() as d:
                        cache_path = Path(d) / "article_analysis" / f"{article_id}.json"
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(
                            json.dumps(cached, ensure_ascii=False),
                            encoding="utf-8",
                        )

                        remote_calls = []

                        def fake_post(url, json=None, timeout=0, headers=None):
                            remote_calls.append(url)

                            class Resp:
                                status_code = 200

                                def raise_for_status(self):
                                    return None

                                def json(self):
                                    return {"ok": True, "analysis": "强制元宝新结果"}

                            return Resp()

                        local_calls = []
                        article_analysis.requests.post = fake_post
                        article_analysis.call_ollama_chat = (
                            lambda config, prompt: local_calls.append(prompt)
                            or '{"summary":"强制本地新结果"}'
                        )

                        result = article_analysis.analyze_single_article(
                            {
                                "analysis_enabled": True,
                                "analysis_force_provider": provider,
                                "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                                "analysis_output_dir": d,
                                "analysis_skip_if_exists": True,
                            },
                            article,
                        )

                        self.assertNotEqual(result["summary"], "旧缓存结果")
                        if provider == "yuanbao":
                            self.assertEqual(result["summary"], "强制元宝新结果")
                            self.assertEqual(result["source"], "yuanbao")
                            self.assertEqual(
                                remote_calls,
                                ["https://news.example.com/api/telegraph/interpret"],
                            )
                            self.assertEqual(local_calls, [])
                        else:
                            self.assertEqual(result["summary"], "强制本地新结果")
                            self.assertEqual(result["source"], "local")
                            self.assertEqual(remote_calls, [])
                            self.assertEqual(len(local_calls), 1)
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_force_provider_failure_preserves_existing_success_cache(self):
        article = {
            "account": "测试号",
            "title": "强制 provider 失败保留旧缓存",
            "published_at": "2026-06-14 10:20",
            "url": "https://mp.weixin.qq.com/s/force-provider-preserve-cache",
            "markdown": "# 正文",
        }
        article_id = article_analysis.build_article_id(article)
        cached = {
            "status": "ok",
            "article_id": article_id,
            "account": "测试号",
            "title": article["title"],
            "url": article["url"],
            "published_at": article["published_at"],
            "date": "",
            "summary": "旧成功缓存结果",
            "topic": "",
            "core_points": [],
            "audience": "",
            "risks": [],
            "source": "cache",
        }
        cached_text = json.dumps(cached, ensure_ascii=False, indent=2)

        old_post = article_analysis.requests.post
        old_local = article_analysis.call_ollama_chat
        try:
            for provider, expected_reason in (
                ("yuanbao", "news_interpret_timeout"),
                ("ollama", "ollama_timeout"),
            ):
                with self.subTest(provider=provider):
                    with tempfile.TemporaryDirectory() as d:
                        cache_path = Path(d) / "article_analysis" / f"{article_id}.json"
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(cached_text, encoding="utf-8")

                        def fail_remote(url, json=None, timeout=0, headers=None):
                            raise requests.Timeout("forced timeout")

                        article_analysis.requests.post = fail_remote
                        article_analysis.call_ollama_chat = lambda config, prompt: (_ for _ in ()).throw(
                            requests.Timeout("forced timeout")
                        )

                        result = article_analysis.analyze_single_article(
                            {
                                "analysis_enabled": True,
                                "analysis_force_provider": provider,
                                "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                                "analysis_output_dir": d,
                                "analysis_skip_if_exists": True,
                                "analysis_save_json": True,
                                "analysis_save_markdown": False,
                            },
                            article,
                        )

                        self.assertEqual(result["status"], "skipped")
                        self.assertEqual(result["reason"], expected_reason)
                        self.assertNotEqual(result.get("summary"), "旧成功缓存结果")
                        self.assertEqual(cache_path.read_text(encoding="utf-8"), cached_text)
                        self.assertEqual(
                            json.loads(cache_path.read_text(encoding="utf-8"))["summary"],
                            "旧成功缓存结果",
                        )
        finally:
            article_analysis.requests.post = old_post
            article_analysis.call_ollama_chat = old_local

    def test_build_single_article_prompt_requests_summary_only(self):
        prompt = article_analysis._build_single_article_prompt(
            {
                "account": "测试号",
                "title": "提示词标题",
                "published_at": "2026-06-13 12:00",
                "url": "https://mp.weixin.qq.com/s/prompt-summary",
                "markdown": "# 正文",
            },
            {"analysis_max_chars": 2000},
        )

        self.assertIn('"summary"', prompt)
        self.assertNotIn('"topic"', prompt)
        self.assertNotIn('"core_points"', prompt)
        self.assertNotIn('"audience"', prompt)
        self.assertNotIn('"risks"', prompt)

    def test_analyze_single_article_local_llm_accepts_summary_json(self):
        old_local = article_analysis.call_ollama_chat
        article_analysis.call_ollama_chat = (
            lambda config, prompt: '{"summary":["第一段总结","第二段总结"]}'
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_output_dir": d,
                        "analysis_news_interpret_url": "",
                    },
                    {
                        "account": "测试号",
                        "title": "本地总结标题",
                        "published_at": "2026-06-13 12:10",
                        "url": "https://mp.weixin.qq.com/s/local-summary",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["summary"], "第一段总结\n第二段总结")
                self.assertEqual(result["source"], "local")
                self.assertEqual(result["topic"], "")
        finally:
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_local_llm_rejects_empty_analysis_payload(self):
        old_local = article_analysis.call_ollama_chat
        article_analysis.call_ollama_chat = lambda config, prompt: "{}"
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_output_dir": d,
                        "analysis_news_interpret_url": "",
                    },
                    {
                        "account": "测试号",
                        "title": "空分析标题",
                        "published_at": "2026-06-13 12:15",
                        "url": "https://mp.weixin.qq.com/s/empty-analysis",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "empty_analysis")
                self.assertEqual(result["title"], "空分析标题")
                self.assertEqual(result["url"], "https://mp.weixin.qq.com/s/empty-analysis")
        finally:
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_local_llm_accepts_alternate_summary_schema(self):
        old_local = article_analysis.call_ollama_chat
        article_analysis.call_ollama_chat = lambda config, prompt: json.dumps(
            {
                "title": "Vibe Coding与灵光平台：普通人如何用AI快速打造实用工具",
                "content": "这是一段有效总结。",
                "key_points": [
                    "要点一",
                    "要点二",
                ],
                "trend_impact": "软件开发门槛继续下降。",
            },
            ensure_ascii=False,
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_output_dir": d,
                        "analysis_news_interpret_url": "",
                    },
                    {
                        "account": "差评X.PIN",
                        "title": "逛完灵光的创作者派对，我发现软件正在被分成两个世界。",
                        "published_at": "2026-04-21 00:00",
                        "url": "https://mp.weixin.qq.com/s/4QN1_IpDXTQybdpasTGbww",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertIn("这是一段有效总结。", result["summary"])
                self.assertIn("软件开发门槛继续下降。", result["summary"])
                self.assertEqual(result["core_points"], ["要点一", "要点二"])
                self.assertEqual(result["audience"], "")
                self.assertEqual(result["source"], "local")
        finally:
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_local_llm_accepts_trend_style_schema(self):
        old_local = article_analysis.call_ollama_chat
        article_analysis.call_ollama_chat = lambda config, prompt: json.dumps(
            {
                "core_trend": "Vibe coding 让普通人也能快速开发工具。",
                "application_types": [
                    "社交型应用",
                    "实用型工具",
                ],
                "platform_response": "平台推出创作者激励计划。",
                "key_impact": "软件开发门槛继续下降。",
            },
            ensure_ascii=False,
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {
                        "analysis_enabled": True,
                        "analysis_output_dir": d,
                        "analysis_news_interpret_url": "",
                    },
                    {
                        "account": "差评X.PIN",
                        "title": "逛完灵光的创作者派对，我发现软件正在被分成两个世界。",
                        "published_at": "2026-04-21 00:00",
                        "url": "https://mp.weixin.qq.com/s/4QN1_IpDXTQybdpasTGbww",
                        "markdown": "# 正文",
                    },
                )

                self.assertEqual(result["status"], "ok")
                self.assertIn("Vibe coding", result["summary"])
                self.assertIn("平台推出创作者激励计划。", result["summary"])
                self.assertEqual(result["core_points"], ["社交型应用", "实用型工具"])
                self.assertEqual(result["source"], "local")
        finally:
            article_analysis.call_ollama_chat = old_local

    def test_analyze_single_article_normalizes_string_list_fields(self):
        def fake_post(url, json=None, timeout=0):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":\"字符串列表\",\"core_points\":\"单条观点\",\"audience\":\"测试者\",\"risks\":\"单条风险\"}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {"analysis_enabled": True, "analysis_output_dir": d},
                    {"title": "T", "url": "https://mp.weixin.qq.com/s/string-list", "markdown": "body"},
                )
                self.assertEqual(result["core_points"], ["单条观点"])
                self.assertEqual(result["risks"], ["单条风险"])
        finally:
            article_analysis.requests.post = old_post

    def test_analyze_single_article_normalizes_scalar_fields_to_string(self):
        def fake_post(url, json=None, timeout=0):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"topic\":[\"题材轮动\"],\"core_points\":[\"单条观点\"],\"audience\":123,\"risks\":[\"单条风险\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                result = article_analysis.analyze_single_article(
                    {"analysis_enabled": True, "analysis_output_dir": d},
                    {"title": "T", "url": "https://mp.weixin.qq.com/s/scalar-fields", "markdown": "body"},
                )
                self.assertEqual(result["topic"], "题材轮动")
                self.assertEqual(result["audience"], "123")
        finally:
            article_analysis.requests.post = old_post

    def test_summarize_analysis_batch_success(self):
        calls = []

        def fake_post(url, json=None, timeout=0):
            calls.append(json)

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"batch_focus\":\"题材轮动\",\"shared_themes\":[\"风险偏好回升\"],\"priority_reads\":[\"A 文，因信息密度高\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                config = {"analysis_enabled": True, "analysis_output_dir": d}
                analyses = [
                    {
                        "status": "ok",
                        "account": "号A",
                        "title": "A 文",
                        "topic": "主线回暖",
                        "core_points": ["回暖"],
                    },
                    {
                        "status": "ok",
                        "account": "号B",
                        "title": "B 文",
                        "topic": "情绪修复",
                        "core_points": ["修复"],
                    },
                ]
                result = article_analysis.summarize_analysis_batch(
                    config, analyses, batch_id="20260611_213000"
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["batch_focus"], "题材轮动")
                self.assertEqual(len(calls), 1)
                prompt = calls[0]["messages"][0]["content"]
                self.assertIn("只输出 JSON", prompt)
                self.assertIn("\"batch_focus\"", prompt)
                self.assertIn("\"shared_themes\"", prompt)
                self.assertIn("A 文", prompt)
        finally:
            article_analysis.requests.post = old_post

    def test_summarize_analysis_batch_uses_summary_specific_config(self):
        calls = []

        def fake_post(url, json=None, timeout=0, headers=None):
            calls.append((url, json, timeout, headers))

            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": "{\"batch_focus\":\"题材轮动\",\"shared_themes\":[\"风险偏好回升\"],\"priority_reads\":[\"A 文，因信息密度高\"]}"
                                }
                            }
                        ]
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            result = article_analysis.summarize_analysis_batch(
                {
                    "analysis_enabled": True,
                    "analysis_summary_base_url": "http://summary.example/v1",
                    "analysis_summary_model": "summary:model",
                    "analysis_summary_timeout_seconds": 7,
                },
                [{"status": "ok", "title": "A 文", "topic": "主线回暖", "core_points": ["回暖"]}],
                batch_id="20260611_220500",
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls[0][0], "http://summary.example/v1/chat/completions")
            self.assertEqual(calls[0][1]["model"], "summary:model")
            self.assertEqual(calls[0][2], 7)
        finally:
            article_analysis.requests.post = old_post

    def test_summarize_analysis_batch_normalizes_string_list_fields(self):
        def fake_post(url, json=None, timeout=0):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"batch_focus\":\"题材轮动\",\"shared_themes\":\"风险偏好回升\",\"priority_reads\":\"A 文，因信息密度高\"}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            result = article_analysis.summarize_analysis_batch(
                {"analysis_enabled": True},
                [{"status": "ok", "title": "A 文", "topic": "主线回暖", "core_points": ["回暖"]}],
                batch_id="20260611_220000",
            )
            self.assertEqual(result["shared_themes"], ["风险偏好回升"])
            self.assertEqual(result["priority_reads"], ["A 文，因信息密度高"])
        finally:
            article_analysis.requests.post = old_post

    def test_summarize_analysis_batch_normalizes_batch_focus_to_string(self):
        def fake_post(url, json=None, timeout=0):
            class Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "message": {
                            "content": "{\"batch_focus\":[\"题材轮动\"],\"shared_themes\":[\"风险偏好回升\"],\"priority_reads\":[\"A 文，因信息密度高\"]}"
                        }
                    }

            return Resp()

        old_post = article_analysis.requests.post
        article_analysis.requests.post = fake_post
        try:
            result = article_analysis.summarize_analysis_batch(
                {"analysis_enabled": True},
                [{"status": "ok", "title": "A 文", "topic": "主线回暖", "core_points": ["回暖"]}],
                batch_id="20260611_221500",
            )
            self.assertEqual(result["batch_focus"], "题材轮动")
        finally:
            article_analysis.requests.post = old_post


class TestArticleAnalysisRendering(unittest.TestCase):
    def test_persist_analysis_outputs_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            config = {
                "analysis_enabled": True,
                "analysis_output_dir": d,
                "analysis_save_json": True,
                "analysis_save_markdown": True,
            }
            analysis = {
                "status": "ok",
                "article_id": "abc123",
                "topic": "主线方向",
                "core_points": ["主线回流", "高位震荡"],
                "audience": "短线跟踪者",
                "risks": ["不宜追高"],
            }

            article_analysis.persist_single_analysis_outputs(config, analysis)

            self.assertTrue((Path(d) / "article_analysis" / "abc123.json").exists())
            self.assertTrue((Path(d) / "article_analysis" / "abc123.md").exists())

    def test_persist_batch_analysis_outputs_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            config = {
                "analysis_enabled": True,
                "analysis_output_dir": d,
                "analysis_save_json": True,
                "analysis_save_markdown": True,
            }
            batch_analysis = {
                "status": "ok",
                "batch_id": "20260611_223000",
                "batch_focus": "情绪修复",
                "shared_themes": ["资金回流"],
                "priority_reads": ["A 文，因信息密度高"],
            }

            article_analysis.persist_batch_analysis_outputs(config, batch_analysis)

            self.assertTrue((Path(d) / "article_batches" / "20260611_223000.json").exists())
            self.assertTrue((Path(d) / "article_batches" / "20260611_223000.md").exists())

    def test_render_single_analysis_markdown_for_serverchan(self):
        article = {
            "account": "测试号",
            "group": "测试分组",
            "title": "标题",
            "published_at": "2026-06-11 21:30",
            "url": "https://mp.weixin.qq.com/s/x",
            "analysis": {
                "status": "ok",
                "topic": "题材切换",
                "core_points": ["主线修复", "轮动加快"],
                "audience": "短线观察者",
                "risks": ["情绪反复"],
            },
        }

        desp = wechat_crawler.build_serverchan_markdown(article)

        self.assertIn("/article_analysis", desp)
        self.assertNotIn("https://wx.coco777.vip/article_analysis", desp)
        self.assertNotIn("分类：", desp)
        self.assertNotIn("测试分组", desp)
        self.assertNotIn("https://mp.weixin.qq.com/s/x", desp)
        self.assertNotIn("阅读全文", desp)
        self.assertNotIn("AI解读", desp)
        self.assertNotIn("题材切换", desp)
        self.assertNotIn("轮动加快", desp)


class TestBuildAnalysisIndexHtml(unittest.TestCase):
    def _is_inside_details(self, html: str, token: str) -> bool:
        pos = html.find(token)
        if pos < 0:
            return False
        last_details = html.rfind("<details", 0, pos)
        if last_details < 0:
            return False
        last_close = html.rfind("</details>", 0, pos)
        return last_details > last_close

    def _build_and_read_index_html(self, output_root: str) -> str:
        cfg = {"analysis_enabled": True, "analysis_output_dir": output_root}
        func = getattr(article_analysis, "build_analysis_index_html", None)
        if callable(func):
            try:
                func(cfg)
            except TypeError:
                func(output_root)
        index_path = Path(output_root) / "article_analysis" / "index.html"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return ""

    def _read_generated_account_pages(self, output_root: str) -> dict[str, str]:
        accounts_dir = Path(output_root) / "article_analysis" / "accounts"
        if not accounts_dir.exists():
            return {}
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(accounts_dir.glob("*.html"))
        }

    def _find_account_page(self, output_root: str, account: str):
        for name, html in self._read_generated_account_pages(output_root).items():
            if account in html:
                return name, html
        return None, ""

    def test_load_account_categories_from_doc(self):
        func = getattr(article_analysis, "_load_account_categories", None)
        self.assertTrue(callable(func))
        with tempfile.TemporaryDirectory() as d:
            doc_path = Path(d) / "公众号名字"
            doc_path.write_text(
                "daily公众号：\n盘前纪要\n\n投研公众号：\n研训社\n盘前纪要\n",
                encoding="utf-8",
            )

            mapping, order = func(doc_path)

            self.assertEqual(order, ["daily公众号", "投研公众号"])
            self.assertEqual(mapping["盘前纪要"], "daily公众号")
            self.assertEqual(mapping["研训社"], "投研公众号")

    def test_build_analysis_index_html_groups_directory_by_account_doc(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (Path(d) / "公众号名字").write_text(
                "\n".join(
                    [
                        "daily公众号：",
                        "盘前纪要",
                        "",
                        "投研公众号：",
                        "研训社",
                        "",
                        "misc公众号：",
                        "差评X.PIN",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "daily.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "daily",
                        "account": "盘前纪要",
                        "title": "盘前文章",
                        "url": "https://mp.weixin.qq.com/s/daily",
                        "published_at": "2026-06-12 09:00",
                        "topic": "日更",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "research.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "research",
                        "account": "研训社",
                        "title": "投研文章",
                        "url": "https://mp.weixin.qq.com/s/research",
                        "published_at": "2026-06-12 10:00",
                        "topic": "投研",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "fallback.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "fallback",
                        "account": "未收录公众号",
                        "title": "兜底文章",
                        "url": "https://mp.weixin.qq.com/s/fallback",
                        "published_at": "2026-06-12 11:00",
                        "topic": "兜底",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)

            self.assertIn("daily公众号", html)
            self.assertIn("投研公众号", html)
            self.assertIn("misc公众号", html)
            self.assertLess(html.find("daily公众号"), html.find("投研公众号"))
            self.assertLess(html.find("投研公众号"), html.find("misc公众号"))
            self.assertIn("盘前纪要（1）｜最新：2026-06-12 09:00", html)
            self.assertIn("研训社（1）｜最新：2026-06-12 10:00", html)
            self.assertIn("未收录公众号（1）｜最新：2026-06-12 11:00", html)

    def test_build_analysis_index_html_skips_empty_directory_categories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (Path(d) / "公众号名字").write_text(
                "\n".join(
                    [
                        "daily公众号：",
                        "盘前纪要",
                        "",
                        "官媒公众号：",
                        "新华社",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "official.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "official",
                        "account": "新华社",
                        "title": "官媒文章",
                        "url": "https://mp.weixin.qq.com/s/official",
                        "published_at": "2026-06-12 12:00",
                        "topic": "官媒",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)

            self.assertIn("官媒公众号", html)
            self.assertNotIn("daily公众号", html)

    def test_build_analysis_index_html_directory_item_shows_latest_time_and_title(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "dir-latest-title",
                        "account": "目录测试号",
                        "title": "目录最新标题",
                        "url": "https://mp.weixin.qq.com/s/directory-latest-title",
                        "published_at": "2026-06-12 10:00",
                        "topic": "目录主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)
            page_href = article_analysis._account_page_relative_path("目录测试号")

            self.assertRegex(
                html,
                rf'<a class="directory-link" href="{re.escape(page_href)}">目录测试号（1）｜最新：2026-06-12 10:00｜标题：目录最新标题</a>',
            )

    def test_build_analysis_index_html_directory_item_shows_title_with_mtime_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            entry_path = root / "entry.json"
            entry_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "dir-title-only",
                        "account": "无时间目录号",
                        "title": "只有标题也要显示",
                        "url": "https://mp.weixin.qq.com/s/directory-title-only",
                        "topic": "目录主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.utime(entry_path, (1760000000, 1760000000))

            html = self._build_and_read_index_html(d)
            page_href = article_analysis._account_page_relative_path("无时间目录号")
            expected = article_analysis.datetime.fromtimestamp(1760000000).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            self.assertRegex(
                html,
                rf'<a class="directory-link" href="{re.escape(page_href)}">无时间目录号（1）｜最新：{expected}｜标题：只有标题也要显示</a>',
            )

    def test_format_latest_time_falls_back_to_mtime_for_invalid_date_text(self):
        func = getattr(article_analysis, "_format_latest_time", None)
        self.assertTrue(callable(func))
        expected = article_analysis.datetime.fromtimestamp(1760000000).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.assertEqual(
            func({"date_text": "Unknown", "_mtime": 1760000000}),
            expected,
        )

    def test_normalize_account_name_rejects_internal_identifiers(self):
        func = getattr(article_analysis, "_normalize_account_name", None)
        self.assertTrue(callable(func))
        self.assertEqual(func("正规公众号"), "正规公众号")
        self.assertEqual(func(" gh_2ba2404c01c0 "), "Unknown_Account")
        self.assertEqual(func("Unknown_Account"), "Unknown_Account")
        self.assertEqual(func("   "), "Unknown_Account")

    def test_render_analysis_item_html_renders_provider_reanalyze_buttons_with_url(self):
        html = article_analysis._render_analysis_item_html(
            {
                "article_id": "aid-123",
                "title": "带链接文章",
                "url": "https://mp.weixin.qq.com/s/with-url",
                "published_at": "2026-06-12 10:00",
                "topic": "测试主题",
                "core_points": ["测试观点"],
                "audience": "测试读者",
                "risks": ["测试风险"],
            }
        )

        self.assertEqual(html.count('class="reanalyze-button"'), 2)
        self.assertIn("元宝解读", html)
        self.assertIn("本地模型解读", html)
        self.assertEqual(html.count('data-article-id="aid-123"'), 2)
        self.assertEqual(
            html.count('data-url="https://mp.weixin.qq.com/s/with-url"'),
            2,
        )
        self.assertIn('data-provider="yuanbao"', html)
        self.assertIn('data-provider="ollama"', html)
        self.assertNotIn("disabled", html)

    def test_render_analysis_item_html_disables_provider_reanalyze_buttons_without_url(self):
        html = article_analysis._render_analysis_item_html(
            {
                "article_id": "aid-456",
                "title": "无链接文章",
                "published_at": "2026-06-12 10:00",
                "topic": "测试主题",
                "core_points": ["测试观点"],
                "audience": "测试读者",
                "risks": ["测试风险"],
            }
        )

        self.assertEqual(html.count('class="reanalyze-button"'), 2)
        self.assertIn("元宝解读", html)
        self.assertIn("本地模型解读", html)
        self.assertEqual(html.count("disabled"), 2)
        self.assertIn('data-provider="yuanbao"', html)
        self.assertIn('data-provider="ollama"', html)
        self.assertIn("缺少原文链接，无法重解读", html)

    def test_render_analysis_item_html_prefers_normalized_summary(self):
        html = article_analysis._render_analysis_item_html(
            {
                "article_id": "aid-summary",
                "title": "总结文章",
                "url": "https://mp.weixin.qq.com/s/summary",
                "published_at": "2026-06-13 10:00",
                "summary": ["第一段总结", "第二段总结"],
                "topic": "旧主题",
                "core_points": ["旧观点"],
                "audience": "旧读者",
                "risks": ["旧风险"],
            }
        )

        self.assertIn("总结：", html)
        self.assertIn('class="summary-block"', html)
        self.assertIn('<p class="summary-paragraph">第一段总结</p>', html)
        self.assertIn('<p class="summary-paragraph">第二段总结</p>', html)
        self.assertNotIn("主题：", html)
        self.assertNotIn("核心观点：", html)

    def test_render_analysis_item_html_treats_empty_ok_payload_as_retryable(self):
        html = article_analysis._render_analysis_item_html(
            {
                "article_id": "aid-empty",
                "title": "空白解读文章",
                "url": "https://mp.weixin.qq.com/s/empty-render",
                "published_at": "2026-06-13 10:15",
                "status": "ok",
                "reason": "empty_analysis",
                "topic": "",
                "core_points": [],
                "audience": "",
                "risks": [],
            }
        )

        self.assertIn("解读失败，可重试", html)
        self.assertIn("<li>empty_analysis</li>", html)
        self.assertNotIn('class="summary-block"', html)

    def test_render_summary_html_renders_sections_lists_and_escapes_html(self):
        html = article_analysis._render_summary_html(
            "## 核心结论\n普通段落 <b>需要转义</b>\n### 利好\n- 第一条\n- 第二条"
        )

        self.assertIn('class="summary-block"', html)
        self.assertIn('class="summary-section-title"', html)
        self.assertIn('class="summary-subsection-title"', html)
        self.assertIn('class="summary-list"', html)
        self.assertIn('<p class="summary-paragraph">', html)
        self.assertIn('<div class="summary-section-title">核心结论</div>', html)
        self.assertIn('<p class="summary-paragraph">普通段落 &lt;b&gt;需要转义&lt;/b&gt;</p>', html)
        self.assertIn('<div class="summary-subsection-title">利好</div>', html)
        self.assertIn('<ul class="summary-list">', html)
        self.assertIn("<li>第一条</li>", html)
        self.assertIn("<li>第二条</li>", html)
        self.assertNotIn("## 核心结论", html)
        self.assertNotIn("### 利好", html)
        self.assertNotIn("- 第一条", html)
        self.assertNotIn("- 第二条", html)

    def test_render_summary_html_uses_compact_markup_for_single_line_summary(self):
        html = article_analysis._render_summary_html("<b>unsafe</b>")

        self.assertIn('class="summary-inline"', html)
        self.assertIn("&lt;b&gt;unsafe&lt;/b&gt;", html)
        self.assertNotIn("<b>unsafe</b>", html)
        self.assertNotIn('class="summary-block"', html)
        self.assertNotIn("<p>", html)

    def test_build_analysis_index_html_renders_structured_summary_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "structured-summary.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "structured-summary",
                        "account": "结构化摘要号",
                        "title": "多段摘要文章",
                        "url": "https://mp.weixin.qq.com/s/structured-summary",
                        "published_at": "2026-06-13 10:30",
                        "summary": "## 核心结论\n市场情绪回暖\n### 利好\n- 量能修复\n- 主线回流",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "结构化摘要号")

            self.assertIn('class="summary-block"', html)
            self.assertIn('class="summary-section-title"', html)
            self.assertIn('class="summary-subsection-title"', html)
            self.assertIn('class="summary-list"', html)
            self.assertIn('<p class="summary-paragraph">', html)
            self.assertIn('<div class="summary-section-title">核心结论</div>', html)
            self.assertIn('<p class="summary-paragraph">市场情绪回暖</p>', html)
            self.assertIn('<div class="summary-subsection-title">利好</div>', html)
            self.assertIn('<ul class="summary-list">', html)
            self.assertIn("<li>量能修复</li>", html)
            self.assertIn("<li>主线回流</li>", html)
            self.assertNotIn("## 核心结论", html)
            self.assertNotIn("### 利好", html)
            self.assertNotIn("- 量能修复", html)
            self.assertNotIn("- 主线回流", html)

    def test_build_analysis_index_html_groups_by_account_and_collapses_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "a_new.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "a_new",
                        "account": "号A",
                        "title": "A 新",
                        "url": "https://mp.weixin.qq.com/s/a_new",
                        "published_at": "2026-06-12 10:00",
                        "topic": "A主题",
                        "core_points": ["A观点"],
                        "audience": "A读者",
                        "risks": ["A风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "a_old.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "a_old",
                        "account": "号A",
                        "title": "A 旧",
                        "url": "https://mp.weixin.qq.com/s/a_old",
                        "published_at": "2026-06-11 10:00",
                        "topic": "A旧主题",
                        "core_points": ["A旧观点"],
                        "audience": "A旧读者",
                        "risks": ["A旧风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "b_only.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "b_only",
                        "account": "号B",
                        "title": "B 单篇",
                        "url": "https://mp.weixin.qq.com/s/b_only",
                        "published_at": "2026-06-12 09:00",
                        "topic": "B主题",
                        "core_points": ["B观点"],
                        "audience": "B读者",
                        "risks": ["B风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)
            _, account_a_html = self._find_account_page(d, "号A")
            _, account_b_html = self._find_account_page(d, "号B")

            self.assertIn("号A", html)
            self.assertIn("号B", html)
            self.assertIn("公众号目录", html)
            self.assertIn(f'href="{article_analysis._account_page_relative_path("号A")}"', html)
            self.assertIn(f'href="{article_analysis._account_page_relative_path("号B")}"', html)
            self.assertLess(html.find("号A"), html.find("号B"))
            self.assertIn("A 新", html)
            self.assertIn("B 单篇", html)
            self.assertNotIn("A 旧", html)
            self.assertNotIn("A主题", html)
            self.assertNotIn("A旧主题", html)
            self.assertNotIn("B主题", html)
            self.assertIn("A 新", account_a_html)
            self.assertIn("A 旧", account_a_html)
            self.assertIn("A主题", account_a_html)
            self.assertIn("A旧主题", account_a_html)
            self.assertIn("B主题", account_b_html)
            self.assertLess(account_a_html.find("A 新"), account_a_html.find("A 旧"))
            self.assertTrue(self._is_inside_details(account_a_html, "A 旧"))
            self.assertFalse(self._is_inside_details(account_a_html, "A 新"))
            self.assertFalse(self._is_inside_details(account_b_html, "B 单篇"))

    def test_build_analysis_index_html_directory_page_only_shows_directory_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "account-only-directory",
                        "account": "目录专用号",
                        "title": "目录页不应展示正文",
                        "url": "https://mp.weixin.qq.com/s/directory-only",
                        "published_at": "2026-06-13 09:30",
                        "summary": "这里只应该出现在单公众号页",
                        "topic": "目录主题",
                        "core_points": ["目录观点"],
                        "audience": "目录读者",
                        "risks": ["目录风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "second.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "account-only-directory-2",
                        "account": "第二目录号",
                        "title": "目录第二条",
                        "url": "https://mp.weixin.qq.com/s/directory-only-2",
                        "published_at": "2026-06-13 08:30",
                        "summary": "第二条正文也不应出现在目录页",
                        "topic": "第二目录主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)
            page_names = sorted(self._read_generated_account_pages(d))
            linked_pages = sorted(set(re.findall(r'href="accounts/([^"]+\.html)"', html)))

            self.assertIn("公众号目录", html)
            self.assertIn("目录专用号（1）｜最新：2026-06-13 09:30｜标题：目录页不应展示正文", html)
            self.assertIn("第二目录号（1）｜最新：2026-06-13 08:30｜标题：目录第二条", html)
            self.assertEqual(linked_pages, page_names)
            self.assertNotIn('class="item"', html)
            self.assertNotIn('class="reanalyze-button"', html)
            self.assertNotIn('class="back-link"', html)
            self.assertNotIn('href="#', html)
            self.assertNotIn("<details>", html)
            self.assertNotIn('href="https://mp.weixin.qq.com/s/directory-only"', html)
            self.assertNotIn('href="https://mp.weixin.qq.com/s/directory-only-2"', html)
            self.assertNotIn("这里只应该出现在单公众号页", html)
            self.assertNotIn("第二条正文也不应出现在目录页", html)
            self.assertNotIn("目录主题", html)
            self.assertNotIn("目录观点", html)
            self.assertNotIn("第二目录主题", html)

    def test_build_analysis_index_html_generates_single_account_pages(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "focus_latest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "focus-latest",
                        "account": "单页目标号",
                        "title": "最新解读",
                        "url": "https://mp.weixin.qq.com/s/focus-latest",
                        "published_at": "2026-06-13 10:00",
                        "summary": "最新一条完整解读",
                        "topic": "最新主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "focus_other.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "focus-other",
                        "account": "另一个号",
                        "title": "其他账号文章",
                        "url": "https://mp.weixin.qq.com/s/focus-other",
                        "published_at": "2026-06-13 09:00",
                        "summary": "其他账号解读",
                        "topic": "其他主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            page_name, account_html = self._find_account_page(d, "单页目标号")

            self.assertTrue(page_name, "应生成单公众号页面")
            self.assertTrue(page_name.endswith(".html"))
            self.assertIn("单页目标号", account_html)
            self.assertIn("最新解读", account_html)
            self.assertIn("最新一条完整解读", account_html)
            self.assertIn('href="../index.html"', account_html)
            self.assertIn(
                'href="https://mp.weixin.qq.com/s/focus-latest"',
                account_html,
            )
            self.assertIn('class="reanalyze-button"', account_html)
            self.assertIn('data-article-id="focus-latest"', account_html)
            self.assertIn('data-url="https://mp.weixin.qq.com/s/focus-latest"', account_html)
            self.assertNotIn("另一个号", account_html)
            self.assertNotIn("其他账号文章", account_html)

    def test_build_analysis_index_html_uses_relative_links_for_directory_and_account_pages(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "relative-links-entry",
                        "account": "相对链接号",
                        "title": "相对链接文章",
                        "url": "https://mp.weixin.qq.com/s/relative-links-entry",
                        "published_at": "2026-06-13 10:00",
                        "summary": "相对链接解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)
            _, account_html = self._find_account_page(d, "相对链接号")
            page_href = article_analysis._account_page_relative_path("相对链接号")

            self.assertIn(f'href="{page_href}"', html)
            self.assertNotIn(f'href="#{article_analysis._account_anchor_id("相对链接号")}"', html)
            self.assertNotIn(f'href="/article_analysis/{page_href}"', html)
            self.assertNotIn(f'href="http://localhost:8765/article_analysis/{page_href}"', html)
            self.assertIn('href="../index.html"', account_html)
            self.assertNotIn('href="/article_analysis/index.html"', account_html)
            self.assertNotIn('href="http://localhost:8765/article_analysis/index.html"', account_html)

    def test_build_analysis_index_html_history_items_collapse_one_by_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            entries = [
                {
                    "path": "latest.json",
                    "article_id": "history-latest",
                    "account": "逐条折叠号",
                    "title": "最新文章",
                    "url": "https://mp.weixin.qq.com/s/history-latest",
                    "published_at": "2026-06-13 10:00",
                    "summary": "最新文章完整解读",
                },
                {
                    "path": "history-1.json",
                    "article_id": "history-1",
                    "account": "逐条折叠号",
                    "title": "历史文章一",
                    "url": "https://mp.weixin.qq.com/s/history-1",
                    "published_at": "2026-06-12 10:00",
                    "summary": "历史文章一完整解读",
                },
                {
                    "path": "history-2.json",
                    "article_id": "history-2",
                    "account": "逐条折叠号",
                    "url": "https://mp.weixin.qq.com/s/history-2",
                    "published_at": "2026-06-11 09:00",
                    "summary": "历史文章二完整解读",
                    "topic": "缺标题时仍需折叠",
                },
            ]
            for entry in entries:
                payload = dict(entry)
                path = payload.pop("path")
                (root / path).write_text(
                    json.dumps({"status": "ok", **payload}, ensure_ascii=False),
                    encoding="utf-8",
                )

            self._build_and_read_index_html(d)
            _, account_html = self._find_account_page(d, "逐条折叠号")

            self.assertIn("最新文章完整解读", account_html)
            self.assertEqual(account_html.count("<details"), 2)
            self.assertNotIn("<summary>历史解读</summary>", account_html)
            self.assertIn("<summary>2026-06-12 10:00｜历史文章一</summary>", account_html)
            self.assertIn("<summary>2026-06-11 09:00｜(无标题)</summary>", account_html)
            self.assertTrue(self._is_inside_details(account_html, "历史文章一完整解读"))
            self.assertTrue(self._is_inside_details(account_html, "历史文章二完整解读"))
            self.assertFalse(self._is_inside_details(account_html, "最新文章完整解读"))

    def test_build_analysis_index_html_history_summary_labels_do_not_include_full_text(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "latest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "history-summary-latest",
                        "account": "历史摘要号",
                        "title": "最新文章",
                        "url": "https://mp.weixin.qq.com/s/history-summary-latest",
                        "published_at": "2026-06-13 10:00",
                        "summary": "最新文章完整解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            history_summary = "这段历史全文只应在展开后出现，不应泄露到折叠标题"
            (root / "history.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "history-summary-history",
                        "account": "历史摘要号",
                        "title": "历史文章",
                        "url": "https://mp.weixin.qq.com/s/history-summary-history",
                        "published_at": "2026-06-12 09:00",
                        "summary": history_summary,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, account_html = self._find_account_page(d, "历史摘要号")
            summary_labels = re.findall(r"<summary>(.*?)</summary>", account_html)

            self.assertIn("2026-06-12 09:00｜历史文章", summary_labels)
            self.assertEqual(account_html.count(history_summary), 1)
            self.assertTrue(self._is_inside_details(account_html, history_summary))
            self.assertNotIn(history_summary, "".join(summary_labels))

    def test_build_analysis_index_html_removes_stale_account_pages_after_second_build(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "first.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "first-account-entry",
                        "account": "第一轮账号",
                        "title": "第一轮文章",
                        "url": "https://mp.weixin.qq.com/s/first-account-entry",
                        "published_at": "2026-06-13 10:00",
                        "summary": "第一轮解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            stale_page_path = (
                Path(d)
                / "article_analysis"
                / article_analysis._account_page_relative_path("第一轮账号")
            )
            self.assertTrue(stale_page_path.exists())

            (root / "first.json").unlink()
            (root / "second.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "second-account-entry",
                        "account": "第二轮账号",
                        "title": "第二轮文章",
                        "url": "https://mp.weixin.qq.com/s/second-account-entry",
                        "published_at": "2026-06-13 11:00",
                        "summary": "第二轮解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)

            self.assertFalse(stale_page_path.exists())
            self.assertIn(
                f'href="{article_analysis._account_page_relative_path("第二轮账号")}"',
                html,
            )
            self.assertNotIn(
                f'href="{article_analysis._account_page_relative_path("第一轮账号")}"',
                html,
            )

    def test_build_analysis_index_html_second_build_directory_links_match_current_account_pages(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "alpha.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "alpha-entry",
                        "account": "Alpha号",
                        "title": "Alpha文章",
                        "url": "https://mp.weixin.qq.com/s/alpha-entry",
                        "published_at": "2026-06-13 09:00",
                        "summary": "Alpha解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "beta.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "beta-entry",
                        "account": "Beta号",
                        "title": "Beta文章",
                        "url": "https://mp.weixin.qq.com/s/beta-entry",
                        "published_at": "2026-06-13 10:00",
                        "summary": "Beta解读",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)

            (root / "alpha.json").unlink()
            html = self._build_and_read_index_html(d)
            page_names = sorted(self._read_generated_account_pages(d))
            linked_pages = sorted(set(re.findall(r'href="accounts/([^"]+\.html)"', html)))

            self.assertEqual(linked_pages, page_names)
            self.assertEqual(
                linked_pages,
                [Path(article_analysis._account_page_relative_path("Beta号")).name],
            )

    def test_build_analysis_index_html_generates_distinct_account_pages_when_slug_collides(self):
        old_account_slug = article_analysis._account_slug
        try:
            article_analysis._account_slug = lambda account: "dup-slug"
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "article_analysis"
                root.mkdir(parents=True, exist_ok=True)
                (root / "a.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": "slug-collision-a",
                            "account": "冲突账号A",
                            "title": "A文章",
                            "url": "https://mp.weixin.qq.com/s/slug-collision-a",
                            "published_at": "2026-06-13 09:00",
                            "summary": "A解读",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (root / "b.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": "slug-collision-b",
                            "account": "冲突账号B",
                            "title": "B文章",
                            "url": "https://mp.weixin.qq.com/s/slug-collision-b",
                            "published_at": "2026-06-13 10:00",
                            "summary": "B解读",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                html = self._build_and_read_index_html(d)
                page_map = self._read_generated_account_pages(d)
                page_a_name, page_a_html = self._find_account_page(d, "冲突账号A")
                page_b_name, page_b_html = self._find_account_page(d, "冲突账号B")

                self.assertEqual(len(page_map), 2)
                self.assertTrue(page_a_name)
                self.assertTrue(page_b_name)
                self.assertNotEqual(page_a_name, page_b_name)
                self.assertIn("冲突账号A", page_a_html)
                self.assertIn("冲突账号B", page_b_html)
                self.assertIn(f'href="accounts/{page_a_name}"', html)
                self.assertIn(f'href="accounts/{page_b_name}"', html)
        finally:
            article_analysis._account_slug = old_account_slug

    def test_build_analysis_index_html_prefers_latest_topic_for_same_url_even_if_old_has_more_points(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "old_rich.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "old_rich",
                        "account": "号A",
                        "title": "旧标题",
                        "url": "https://mp.weixin.qq.com/s/same-url",
                        "published_at": "2026-06-11 08:00",
                        "topic": "旧主题",
                        "core_points": ["旧观点1", "旧观点2", "旧观点3"],
                        "audience": "旧读者",
                        "risks": ["旧风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "new_fresh.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "new_fresh",
                        "account": "号A",
                        "title": "新标题",
                        "url": "https://mp.weixin.qq.com/s/same-url",
                        "published_at": "2026-06-12 09:00",
                        "topic": "新主题",
                        "core_points": ["新观点1"],
                        "audience": "新读者",
                        "risks": ["新风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn("新主题", html)
            self.assertIn("新标题", html)
            self.assertNotIn("旧主题", html)
            self.assertNotIn("旧标题", html)

    def test_build_analysis_index_html_skips_invalid_json_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "good.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "good",
                        "account": "号A",
                        "title": "可用条目",
                        "url": "https://mp.weixin.qq.com/s/good",
                        "published_at": "2026-06-12 10:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "bad.json").write_text("{bad json", encoding="utf-8")

            html = self._build_and_read_index_html(d)
            _, account_html = self._find_account_page(d, "号A")

            self.assertIn("可用条目", account_html)
            self.assertNotIn("bad.json", html)
            self.assertNotIn("{bad json", html)
            self.assertNotIn("bad json", html)

    def test_build_analysis_index_html_missing_fields_fallbacks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "missing_fields.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "missing_fields",
                        "date": "2026-06-10",
                        "topic": "缺字段主题",
                        "core_points": ["缺字段观点"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "Unknown_Account")

            self.assertIn("Unknown_Account", html)
            self.assertIn("(无标题)", html)
            self.assertIn("2026-06-10", html)
            self.assertNotIn("https://mp.weixin.qq.com", html)

    def test_build_analysis_index_html_skips_retry_impossible_unknown_placeholder_entries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "stale.json").write_text(
                json.dumps(
                    {
                        "status": "error",
                        "account": "Unknown_Account",
                        "title": "Unknown",
                        "topic": "解读失败，可重试",
                        "risks": ["ollama_timeout"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "valid.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "account": "正确账号",
                        "title": "正常文章",
                        "url": "https://mp.weixin.qq.com/s/valid-entry",
                        "published_at": "2026-06-12 10:00",
                        "topic": "正常主题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)

            self.assertIn("正确账号", html)
            self.assertNotIn("Unknown_Account", html)
            self.assertNotIn("解读失败，可重试", html)

    def test_build_analysis_index_html_normalizes_invalid_account_names(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "bad_account.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "bad_account",
                        "account": "gh_2ba2404c01c0",
                        "title": "错误账号名文章",
                        "url": "https://mp.weixin.qq.com/s/bad-account",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "Unknown_Account")

            self.assertIn("Unknown_Account", html)
            self.assertNotIn("gh_2ba2404c01c0", html)

    def test_build_analysis_index_html_prefers_best_entry_for_same_url(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            bad_path = root / "bad_entry.json"
            good_path = root / "good_entry.json"
            bad_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "bad_entry",
                        "account": "Unknown_Account",
                        "title": "Unknown",
                        "url": "https://mp.weixin.qq.com/s/shared-url",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            good_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "good_entry",
                        "account": "差评X.PIN",
                        "title": "正确文章",
                        "url": "https://mp.weixin.qq.com/s/shared-url",
                        "published_at": "2026-06-12 10:00",
                        "topic": "正确主题",
                        "core_points": ["正确观点"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "差评X.PIN")

            self.assertIn("差评X.PIN", html)
            self.assertIn("正确文章", html)
            self.assertNotIn("Unknown_Account", html)
            self.assertNotIn(">Unknown<", html)

    def test_build_analysis_index_html_injects_provider_reanalyze_contract_with_relative_api_url_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry",
                        "account": "号A",
                        "title": "可重解读文章",
                        "url": "https://mp.weixin.qq.com/s/reanalyze-entry",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn('const REANALYZE_API_URL = "/api/reanalyze";', html)
            self.assertNotIn("http://127.0.0.1:8766/api/reanalyze", html)
            self.assertIn("fetch(REANALYZE_API_URL", html)
            self.assertIn('data-provider="yuanbao"', html)
            self.assertIn('data-provider="ollama"', html)
            self.assertRegex(html, r'body\s*:\s*.*provider')
            self.assertIn("元宝解读中...", html)
            self.assertIn("元宝解读成功，正在刷新...", html)
            self.assertIn("元宝解读失败，请稍后重试", html)
            self.assertIn("本地模型解读中...", html)
            self.assertIn("本地模型解读成功，正在刷新...", html)
            self.assertIn("本地模型解读失败，请稍后重试", html)

    def test_build_analysis_index_html_uses_provider_specific_reanalyze_error_messages(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry",
                        "account": "号A",
                        "title": "可重解读文章",
                        "url": "https://mp.weixin.qq.com/s/reanalyze-entry",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn("元宝解读失败，请稍后重试", html)
            self.assertIn("本地模型解读失败，请稍后重试", html)
            self.assertNotIn("重新解读失败，请稍后重试", html)
            self.assertNotIn("重新解读失败：${message}", html)
            self.assertNotIn("payload.reason", html)

    def test_build_analysis_index_html_links_same_article_provider_buttons_busy_and_restore_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry-shared-aid",
                        "account": "号A",
                        "title": "双 provider 文章",
                        "url": "https://mp.weixin.qq.com/s/reanalyze-entry",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn("function getRelatedReanalyzeButtons(button)", html)
            self.assertIn(
                'return (candidate.getAttribute("data-article-id") || "") === articleId;',
                html,
            )
            self.assertIn("function setReanalyzeBusyState(button, busy)", html)
            self.assertIn("candidate.disabled = busy;", html)
            self.assertIn('candidate.classList.toggle("is-busy", busy);', html)
            self.assertIn("setReanalyzeBusyState(button, true);", html)
            self.assertIn("setReanalyzeBusyState(button, false);", html)
            self.assertEqual(html.count('data-article-id="entry-shared-aid"'), 2)

    def test_build_analysis_index_html_uses_public_reanalyze_url_and_prefers_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry",
                        "account": "号A",
                        "title": "可重解读文章",
                        "url": "https://mp.weixin.qq.com/s/reanalyze-entry",
                        "published_at": "2026-06-12 08:00",
                        "summary": ["这里是第一段总结。", "这里是第二段总结。"],
                        "topic": "旧主题",
                        "core_points": ["旧观点"],
                        "audience": "旧读者",
                        "risks": ["旧风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            article_analysis.build_analysis_index_html(
                {
                    "analysis_output_dir": d,
                    "analysis_public_base_url": "https://wx.coco777.vip",
                    "analysis_reanalyze_path": "/api/reanalyze",
                }
            )
            _, html = self._find_account_page(d, "号A")

            self.assertIn("https://wx.coco777.vip/api/reanalyze", html)
            self.assertNotIn("http://127.0.0.1:8766/api/reanalyze", html)
            self.assertIn('class="summary-block"', html)
            self.assertIn("这里是第一段总结。", html)
            self.assertIn("这里是第二段总结。", html)
            self.assertIn('<p class="summary-paragraph">这里是第一段总结。</p>', html)
            self.assertNotIn("旧主题", html)

    def test_build_analysis_index_html_injects_reanalyze_status_styles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry",
                        "account": "号A",
                        "title": "样式文章",
                        "url": "https://mp.weixin.qq.com/s/reanalyze-style",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn('.reanalyze-button.is-busy{', html)
            self.assertIn('.reanalyze-status.is-success{', html)
            self.assertIn('.reanalyze-status.is-error{', html)

    def test_build_analysis_index_html_sets_explicit_light_background_and_text_colors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "entry.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "article_id": "entry",
                        "account": "号A",
                        "title": "浅色主题文章",
                        "url": "https://mp.weixin.qq.com/s/light-theme",
                        "published_at": "2026-06-12 08:00",
                        "topic": "主题",
                        "core_points": ["观点"],
                        "audience": "读者",
                        "risks": ["风险"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            html = self._build_and_read_index_html(d)

            self.assertIn('<meta name="color-scheme" content="light" />', html)
            self.assertIn("html{background:#ffffff;}", html)
            self.assertIn("background:#ffffff;", html)
            self.assertIn("color:#24292f;", html)

    def test_build_analysis_index_html_includes_failed_entries_as_retryable_items(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "article_analysis"
            root.mkdir(parents=True, exist_ok=True)
            (root / "failed.json").write_text(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "ollama_timeout",
                        "article_id": "failed-entry",
                        "account": "号A",
                        "title": "失败文章",
                        "url": "https://mp.weixin.qq.com/s/failed-entry",
                        "published_at": "2026-06-12 08:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self._build_and_read_index_html(d)
            _, html = self._find_account_page(d, "号A")

            self.assertIn("失败文章", html)
            self.assertIn('data-article-id="failed-entry"', html)
            self.assertIn('data-url="https://mp.weixin.qq.com/s/failed-entry"', html)
            self.assertIn("ollama_timeout", html)


class TestCrawlerSingleAnalysisIntegration(unittest.TestCase):
    def test_is_allowed_reanalyze_url_requires_https_mp_weixin_valid_article(self):
        self.assertTrue(
            wechat_crawler._is_allowed_reanalyze_url("https://mp.weixin.qq.com/s/valid-article")
        )
        self.assertFalse(
            wechat_crawler._is_allowed_reanalyze_url("http://mp.weixin.qq.com/s/not-https")
        )
        self.assertFalse(
            wechat_crawler._is_allowed_reanalyze_url("https://example.com/s/not-wechat")
        )
        self.assertFalse(
            wechat_crawler._is_allowed_reanalyze_url("https://mp.weixin.qq.com/cgi-bin/login")
        )
        self.assertFalse(
            wechat_crawler._is_allowed_reanalyze_url("https://mp.weixin.qq.com/cgi-bin/home?t=home/index")
        )

    def test_is_trusted_local_reanalyze_source_only_allows_local_origin_or_referer(self):
        trusted = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Origin": "http://127.0.0.1:8765"}
        )
        trusted_referer = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Referer": "http://localhost:8765/output/article_analysis/index.html"}
        )
        untrusted = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Origin": "https://evil.example.com"}
        )

        self.assertTrue(trusted)
        self.assertTrue(trusted_referer)
        self.assertFalse(untrusted)

    def test_is_trusted_local_reanalyze_source_accepts_configured_public_origin(self):
        trusted = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Origin": "https://wx.coco777.vip"},
            {
                "analysis_public_base_url": "https://wx.coco777.vip",
                "analysis_reanalyze_path": "custom/reanalyze",
            },
        )
        trusted_referer = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Referer": "https://wx.coco777.vip/custom/reanalyze"},
            {
                "analysis_public_base_url": "https://wx.coco777.vip",
                "analysis_reanalyze_path": "custom/reanalyze",
            },
        )
        untrusted = wechat_crawler._is_trusted_local_reanalyze_source(
            {"Origin": "https://evil.example.com"},
            {
                "analysis_public_base_url": "https://wx.coco777.vip",
                "analysis_reanalyze_path": "custom/reanalyze",
            },
        )

        self.assertTrue(trusted)
        self.assertTrue(trusted_referer)
        self.assertFalse(untrusted)

    def test_reanalyze_path_resolution_matches_frontend_and_backend(self):
        config = {
            "analysis_public_base_url": "https://wx.coco777.vip",
            "analysis_reanalyze_path": "custom/reanalyze",
        }

        self.assertEqual(
            article_analysis._resolve_reanalyze_api_url(config),
            "https://wx.coco777.vip/custom/reanalyze",
        )
        self.assertEqual(
            wechat_crawler._resolve_reanalyze_api_path(config),
            "/custom/reanalyze",
        )

    def test_make_reanalyze_request_handler_serves_custom_path_over_http(self):
        config = {
            "analysis_enabled": True,
            "analysis_public_base_url": "https://wx.coco777.vip",
            "analysis_reanalyze_path": "custom/reanalyze",
        }
        old_handle = wechat_crawler.handle_reanalyze_api_request
        server = None
        thread = None
        try:
            wechat_crawler.handle_reanalyze_api_request = (
                lambda payload, config, request_headers=None: {
                    "status": "ok",
                    "article_id": "aid-http",
                    "account": "测试号",
                    "title": "标题",
                }
            )
            server = wechat_crawler.ThreadingHTTPServer(
                ("127.0.0.1", 0),
                wechat_crawler.make_reanalyze_request_handler(config),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            conn.request(
                "POST",
                "/custom/reanalyze",
                body=json.dumps({"article_id": "aid-http", "url": "https://mp.weixin.qq.com/s/http-path"}),
                headers={
                    "Origin": "https://wx.coco777.vip",
                    "Content-Type": "application/json",
                },
            )
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            conn.close()

            bad_conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            bad_conn.request(
                "POST",
                "/api/reanalyze",
                body=json.dumps({"article_id": "aid-http", "url": "https://mp.weixin.qq.com/s/http-path"}),
                headers={
                    "Origin": "https://wx.coco777.vip",
                    "Content-Type": "application/json",
                },
            )
            bad_resp = bad_conn.getresponse()
            bad_body = bad_resp.read().decode("utf-8")
            bad_conn.close()

            self.assertEqual(resp.status, 200)
            self.assertIn('"status": "ok"', body)
            self.assertEqual(bad_resp.status, 404)
            self.assertIn("not_found", bad_body)
        finally:
            wechat_crawler.handle_reanalyze_api_request = old_handle
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=3)

    def test_make_analysis_static_request_handler_serves_static_file_and_reanalyze_post(self):
        old_handle = wechat_crawler.handle_reanalyze_api_request
        server = None
        thread = None
        captured = []
        try:
            wechat_crawler.handle_reanalyze_api_request = (
                lambda payload, config, request_headers=None: captured.append(
                    {
                        "payload": payload,
                        "origin": request_headers.get("Origin") if request_headers else "",
                    }
                )
                or {"status": "ok", "article_id": "aid-public", "source": "yuanbao"}
            )

            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                article_dir = root / "article_analysis"
                article_dir.mkdir(parents=True, exist_ok=True)
                (article_dir / "index.html").write_text("<html><body>analysis ok</body></html>", encoding="utf-8")

                server = wechat_crawler.ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    wechat_crawler.make_analysis_static_request_handler(
                        {
                            "analysis_enabled": True,
                            "analysis_public_base_url": "https://wx.coco777.vip",
                            "analysis_reanalyze_path": "/api/reanalyze",
                        },
                        directory=str(root),
                    ),
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                conn.request("GET", "/article_analysis/index.html")
                resp = conn.getresponse()
                body = resp.read().decode("utf-8")
                conn.close()

                post_conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                post_conn.request(
                    "POST",
                    "/api/reanalyze",
                    body=json.dumps({"article_id": "aid-public", "url": "https://mp.weixin.qq.com/s/public"}),
                    headers={
                        "Origin": "https://wx.coco777.vip",
                        "Content-Type": "application/json",
                    },
                )
                post_resp = post_conn.getresponse()
                post_body = post_resp.read().decode("utf-8")
                post_conn.close()

                self.assertEqual(resp.status, 200)
                self.assertIn("analysis ok", body)
                self.assertEqual(post_resp.status, 200)
                self.assertIn('"status": "ok"', post_body)
                self.assertEqual(captured[0]["payload"]["article_id"], "aid-public")
                self.assertEqual(captured[0]["origin"], "")
        finally:
            wechat_crawler.handle_reanalyze_api_request = old_handle
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=3)

    def test_detect_wechat_article_access_error_does_not_misclassify_normal_article_keywords(self):
        body = """
        <html>
          <body>
            <h1>环境异常下的流量分配</h1>
            <p>文章讨论访问过于频繁场景的系统设计，以及安全验证流程优化。</p>
            <div id="js_content">这是正常文章正文，不是微信安全页。</div>
          </body>
        </html>
        """

        reason = wechat_crawler._detect_wechat_article_access_error(body)

        self.assertEqual(reason, "")

    def test_detect_wechat_article_access_error_keeps_real_security_page_detection(self):
        body = """
        <html>
          <title>环境异常</title>
          <body>
            当前环境异常，需完成安全验证后继续访问
          </body>
        </html>
        """

        reason = wechat_crawler._detect_wechat_article_access_error(body)

        self.assertEqual(reason, "wechat_security_verification_required")

    def test_fetch_article_markdown_extracts_title_account_and_time_from_html(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="测试文章标题" />
            <meta name="twitter:title" content="测试文章标题" />
          </head>
          <body>
            <script>
              var user_name = "gh_test_internal_id";
              var publish_time = "1770811200";
            </script>
            <div id="js_name">测试公众号</div>
            <h1 id="activity-name">测试文章标题</h1>
            <span class="publish_time">2026-06-12 08:00</span>
            <div id="js_content"><p>正文内容</p></div>
          </body>
        </html>
        """

        class Resp:
            text = html
            encoding = "utf-8"

        old_get = wechat_crawler.requests.get
        try:
            wechat_crawler.requests.get = lambda url, headers=None: Resp()
            fetched = wechat_crawler.fetch_article_markdown(
                {
                    "title": "Unknown",
                    "link": "https://mp.weixin.qq.com/s/test-html",
                    "create_time": 0,
                    "digest": "",
                    "author": "",
                },
                headers={"User-Agent": "test"},
                account_name=None,
            )
            self.assertEqual(fetched["title"], "测试文章标题")
            self.assertEqual(fetched["account"], "测试公众号")
            self.assertEqual(fetched["published_at"], "2026-06-12 08:00")
        finally:
            wechat_crawler.requests.get = old_get

    def test_fetch_article_markdown_raises_explicit_error_on_wechat_login_page(self):
        class Resp:
            text = "<html><title>微信公众平台</title><body>使用微信扫一扫</body></html>"
            encoding = "utf-8"

        old_get = wechat_crawler.requests.get
        try:
            wechat_crawler.requests.get = lambda url, headers=None: Resp()
            with self.assertRaisesRegex(RuntimeError, "wechat_auth_required"):
                wechat_crawler.fetch_article_markdown(
                    {
                        "title": "Unknown",
                        "link": "https://mp.weixin.qq.com/s/test-login",
                        "create_time": 0,
                        "digest": "",
                        "author": "",
                    },
                    headers={"User-Agent": "test"},
                    account_name=None,
                )
        finally:
            wechat_crawler.requests.get = old_get

    def test_run_extract_from_url_attaches_analysis(self):
        persist_calls = []
        refresh_calls = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-11",
                "published_at": "2026-06-11 21:30",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
            wechat_crawler.analyze_single_article = lambda config, article: {
                "status": "ok",
                "topic": "主线回暖",
                "core_points": ["资金回流"],
                "audience": "短线观察者",
                "risks": ["持续性待确认"],
                "article_id": "abc",
            }
            wechat_crawler.persist_single_analysis_outputs = (
                lambda config, analysis: persist_calls.append(analysis["article_id"])
            )
            wechat_crawler.build_analysis_index_html = lambda config: refresh_calls.append(1)

            payload = wechat_crawler.run_extract_from_url(
                "https://mp.weixin.qq.com/s/test",
                account_name="测试号",
                save_markdown=False,
                push=False,
                config={"analysis_enabled": True},
            )

            self.assertEqual(payload["analysis"]["topic"], "主线回暖")
            self.assertEqual(persist_calls, ["abc"])
            self.assertEqual(refresh_calls, [1])
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index

    def test_attach_single_article_analysis_rewrites_cached_metadata_when_fields_change(self):
        persisted = []

        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        try:
            wechat_crawler.analyze_single_article = lambda config, article: {
                "status": "ok",
                "article_id": "cached-1",
                "account": "旧账号名",
                "title": "旧标题",
                "url": "https://mp.weixin.qq.com/s/old",
                "published_at": "2026-06-10 08:00",
                "topic": "缓存主题",
                "core_points": ["缓存观点"],
                "audience": "缓存读者",
                "risks": ["缓存风险"],
            }
            wechat_crawler.persist_single_analysis_outputs = (
                lambda config, analysis: persisted.append(dict(analysis))
            )
            wechat_crawler.build_analysis_index_html = lambda config: None

            analysis = wechat_crawler._attach_single_article_analysis(
                {"analysis_enabled": True},
                {
                    "account": "新账号名",
                    "title": "新标题",
                    "url": "https://mp.weixin.qq.com/s/new",
                    "published_at": "2026-06-12 08:00",
                    "date": "2026-06-12",
                    "markdown": "# 正文",
                },
            )

            self.assertEqual(analysis["account"], "新账号名")
            self.assertEqual(analysis["title"], "新标题")
            self.assertEqual(analysis["url"], "https://mp.weixin.qq.com/s/new")
            self.assertEqual(analysis["published_at"], "2026-06-12 08:00")
            self.assertEqual(len(persisted), 1)
        finally:
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index

    def test_run_reanalyze_from_url_forces_analysis_skip_flag_off(self):
        analyze_configs = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-11",
                "published_at": "2026-06-11 21:30",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
            wechat_crawler.analyze_single_article = lambda config, article: analyze_configs.append(dict(config)) or {
                "status": "ok",
                "topic": "强制重解读",
                "core_points": ["覆盖旧缓存"],
                "audience": "测试者",
                "risks": ["无"],
                "article_id": "force-1",
            }
            wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
            wechat_crawler.build_analysis_index_html = lambda config: None

            payload = wechat_crawler.run_reanalyze_from_url(
                "https://mp.weixin.qq.com/s/force-reanalyze",
                account_name="测试号",
                save_markdown=False,
                push=False,
                config={"analysis_enabled": True, "analysis_skip_if_exists": True},
            )

            self.assertEqual(payload["analysis"]["topic"], "强制重解读")
            self.assertFalse(analyze_configs[0]["analysis_skip_if_exists"])
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index

    def test_run_reanalyze_from_url_rewrites_config_for_provider_and_keeps_force_reanalyze(self):
        captured = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_attach = wechat_crawler._attach_single_article_analysis
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-14",
                "published_at": "2026-06-14 10:00",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: self.fail(
                "push=False 时不应推送"
            )

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                captured.append(
                    {
                        "config": dict(config or {}),
                        "refresh_index": refresh_index,
                        "force_reanalyze": force_reanalyze,
                        "fetched": dict(fetched or {}),
                    }
                )
                return {
                    "status": "ok",
                    "article_id": "aid-provider-config",
                    "source": str((config or {}).get("analysis_force_provider") or ""),
                }

            wechat_crawler._attach_single_article_analysis = fake_attach

            expected_news_url = "https://news.example.com/api/telegraph/interpret"
            for provider in ("yuanbao", "ollama"):
                with self.subTest(provider=provider):
                    payload = wechat_crawler.run_reanalyze_from_url(
                        f"https://mp.weixin.qq.com/s/provider-{provider}",
                        article_id=f"aid-provider-{provider}",
                        provider=provider,
                        push=False,
                        config={
                            "analysis_enabled": True,
                            "analysis_skip_if_exists": True,
                            "analysis_news_interpret_url": expected_news_url,
                        },
                    )

                    self.assertEqual(payload["analysis"]["source"], provider)
                    self.assertTrue(captured[-1]["force_reanalyze"])
                    self.assertFalse(captured[-1]["config"]["analysis_skip_if_exists"])
                    self.assertEqual(
                        captured[-1]["config"]["analysis_force_provider"], provider
                    )
                    if provider == "yuanbao":
                        self.assertEqual(
                            captured[-1]["config"]["analysis_news_interpret_url"],
                            expected_news_url,
                        )
                    else:
                        self.assertEqual(
                            captured[-1]["config"]["analysis_news_interpret_url"], ""
                        )
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            wechat_crawler._attach_single_article_analysis = old_attach

    def test_run_reanalyze_from_url_raises_ollama_timeout_floor_for_manual_reanalyze(self):
        captured = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_attach = wechat_crawler._attach_single_article_analysis
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-14",
                "published_at": "2026-06-14 10:00",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: self.fail(
                "push=False 时不应推送"
            )

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                captured.append(
                    {
                        "config": dict(config or {}),
                        "force_reanalyze": force_reanalyze,
                    }
                )
                return {
                    "status": "ok",
                    "article_id": "aid-timeout-floor",
                    "source": "local",
                }

            wechat_crawler._attach_single_article_analysis = fake_attach

            payload = wechat_crawler.run_reanalyze_from_url(
                "https://mp.weixin.qq.com/s/provider-ollama-timeout",
                article_id="aid-timeout-floor",
                provider="ollama",
                push=False,
                config={
                    "analysis_enabled": True,
                    "analysis_timeout_seconds": 30,
                    "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                },
            )

            self.assertEqual(payload["analysis"]["source"], "local")
            self.assertTrue(captured[-1]["force_reanalyze"])
            self.assertEqual(captured[-1]["config"]["analysis_force_provider"], "ollama")
            self.assertEqual(captured[-1]["config"]["analysis_news_interpret_url"], "")
            self.assertGreaterEqual(captured[-1]["config"]["analysis_timeout_seconds"], 90)
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            wechat_crawler._attach_single_article_analysis = old_attach

    def test_handle_reanalyze_api_request_passes_supported_provider_to_runner(self):
        calls = []

        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = (
                lambda article_url, account_name=None, article_id=None, provider=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True, config=None: calls.append(
                    {
                        "article_url": article_url,
                        "account_name": account_name,
                        "article_id": article_id,
                        "provider": provider,
                        "push": push,
                    }
                )
                or {
                    "analysis": {"article_id": "aid-provider", "source": provider},
                    "account": "测试号",
                    "title": "标题",
                }
            )

            for provider in ("yuanbao", "ollama"):
                with self.subTest(provider=provider):
                    result = wechat_crawler.handle_reanalyze_api_request(
                        {
                            "article_id": f"aid-provider-{provider}",
                            "url": f"https://mp.weixin.qq.com/s/api-provider-{provider}",
                            "provider": provider,
                        },
                        {"analysis_enabled": True},
                    )

                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(calls[-1]["provider"], provider)
                    self.assertEqual(result["source"], provider)
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_rejects_missing_or_invalid_provider(self):
        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = lambda *args, **kwargs: self.fail(
                "provider 缺失或非法时不应进入重解读流程"
            )

            for payload in (
                {"article_id": "aid-missing-provider", "url": "https://mp.weixin.qq.com/s/missing-provider"},
                {
                    "article_id": "aid-bad-provider",
                    "url": "https://mp.weixin.qq.com/s/bad-provider",
                    "provider": "auto",
                },
            ):
                with self.subTest(payload=payload):
                    result = wechat_crawler.handle_reanalyze_api_request(
                        payload,
                        {"analysis_enabled": True},
                    )

                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["article_id"], payload["article_id"])
                    self.assertEqual(result["reason"], "invalid_provider")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_calls_force_reanalyze(self):
        calls = []

        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = (
                lambda article_url, account_name=None, article_id=None, provider=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True, config=None: calls.append(
                    {
                        "article_url": article_url,
                        "account_name": account_name,
                        "article_id": article_id,
                        "provider": provider,
                        "push": push,
                        "config": dict(config or {}),
                    }
                )
                or {
                    "analysis": {"article_id": "aid-1", "source": "yuanbao"},
                    "account": "测试号",
                    "title": "标题",
                }
            )

            result = wechat_crawler.handle_reanalyze_api_request(
                {
                    "article_id": "aid-1",
                    "url": "https://mp.weixin.qq.com/s/api-reanalyze",
                    "provider": "yuanbao",
                },
                {"analysis_enabled": True},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["article_id"], "aid-1")
            self.assertEqual(result["source"], "yuanbao")
            self.assertEqual(calls[0]["article_url"], "https://mp.weixin.qq.com/s/api-reanalyze")
            self.assertEqual(calls[0]["article_id"], "aid-1")
            self.assertFalse(calls[0]["push"])
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_rejects_non_wechat_url(self):
        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("非法 URL 不应进入重解读流程")
            )

            result = wechat_crawler.handle_reanalyze_api_request(
                {"article_id": "aid-2", "url": "https://example.com/not-wechat"},
                {"analysis_enabled": True},
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["article_id"], "aid-2")
            self.assertEqual(result["reason"], "invalid_url")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_rejects_untrusted_origin(self):
        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("非法来源不应进入重解读流程")
            )

            result = wechat_crawler.handle_reanalyze_api_request(
                {"article_id": "aid-bad-origin", "url": "https://mp.weixin.qq.com/s/api-origin"},
                {"analysis_enabled": True},
                request_headers={"Origin": "https://evil.example.com"},
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["article_id"], "aid-bad-origin")
            self.assertEqual(result["reason"], "forbidden_origin")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_accepts_configured_public_origin(self):
        calls = []

        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = (
                lambda article_url, account_name=None, article_id=None, provider=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True, config=None: calls.append(
                    {
                        "article_url": article_url,
                        "account_name": account_name,
                        "article_id": article_id,
                        "provider": provider,
                        "config": dict(config or {}),
                    }
                )
                or {"analysis": {"article_id": "aid-public"}, "account": "测试号", "title": "标题"}
            )

            result = wechat_crawler.handle_reanalyze_api_request(
                {
                    "article_id": "aid-public",
                    "url": "https://mp.weixin.qq.com/s/public-origin",
                    "provider": "yuanbao",
                },
                {
                    "analysis_enabled": True,
                    "analysis_public_base_url": "https://wx.coco777.vip",
                    "analysis_reanalyze_path": "custom/reanalyze",
                },
                request_headers={"Origin": "https://wx.coco777.vip"},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls[0]["article_id"], "aid-public")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_passes_payload_account_to_runner(self):
        calls = []

        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            wechat_crawler.run_reanalyze_from_url = (
                lambda article_url, account_name=None, article_id=None, provider=None, save_markdown=False, output_json_path=None, serverchan_sendkey=None, push=True, config=None: calls.append(
                    {
                        "article_url": article_url,
                        "account_name": account_name,
                        "article_id": article_id,
                        "provider": provider,
                    }
                )
                or {"analysis": {"article_id": "aid-3"}, "account": "前端账号", "title": "标题"}
            )

            result = wechat_crawler.handle_reanalyze_api_request(
                {
                    "article_id": "aid-3",
                    "account": "前端账号",
                    "url": "https://mp.weixin.qq.com/s/api-account",
                    "provider": "ollama",
                },
                {"analysis_enabled": True},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls[0]["account_name"], "前端账号")
            self.assertEqual(calls[0]["article_id"], "aid-3")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_returns_explicit_auth_error_from_fetch(self):
        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            def raise_login(*args, **kwargs):
                raise RuntimeError("wechat_auth_required")

            wechat_crawler.run_reanalyze_from_url = raise_login

            result = wechat_crawler.handle_reanalyze_api_request(
                {
                    "article_id": "aid-login",
                    "url": "https://mp.weixin.qq.com/s/login-page",
                    "provider": "yuanbao",
                },
                {"analysis_enabled": True},
                request_headers={"Origin": "http://127.0.0.1:8765"},
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["article_id"], "aid-login")
            self.assertEqual(result["reason"], "wechat_auth_required")
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_run_reanalyze_from_url_uses_cached_account_when_fetch_falls_back_unknown(self):
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                cache_dir = Path(d) / "article_analysis"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / "cached-keep-account.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": "cached-keep-account",
                            "account": "旧缓存账号",
                            "title": "旧标题",
                            "url": "https://mp.weixin.qq.com/s/old-cache-account",
                            "published_at": "2026-06-11 21:30",
                            "topic": "旧主题",
                            "core_points": ["旧观点"],
                            "audience": "旧读者",
                            "risks": ["旧风险"],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                seen = {}

                def fake_fetch(article, headers, account_name=None):
                    seen["account_name"] = account_name
                    return {
                        "account": account_name or "Unknown_Account",
                        "title": "新标题",
                        "date": "2026-06-12",
                        "published_at": "2026-06-12 09:00",
                        "url": article["link"],
                        "markdown": "# 标题\n\n正文",
                        "article_id": "cached-keep-account",
                    }

                wechat_crawler.fetch_article_markdown = fake_fetch
                wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
                wechat_crawler.analyze_single_article = lambda config, article: {
                    "status": "ok",
                    "article_id": article["article_id"],
                    "account": article["account"],
                    "title": article["title"],
                    "url": article["url"],
                    "published_at": article["published_at"],
                    "topic": "强制重解读",
                    "core_points": ["新观点"],
                    "audience": "新读者",
                    "risks": ["新风险"],
                }
                wechat_crawler.build_analysis_index_html = lambda config: None

                payload = wechat_crawler.run_reanalyze_from_url(
                    "https://mp.weixin.qq.com/s/keep-cache-account",
                    article_id="cached-keep-account",
                    push=False,
                    config={"analysis_enabled": True, "analysis_output_dir": d},
                )

                self.assertEqual(seen["account_name"], "旧缓存账号")
                self.assertEqual(payload["account"], "旧缓存账号")
                self.assertEqual(payload["analysis"]["account"], "旧缓存账号")
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index

    def test_run_reanalyze_from_url_reuses_existing_cache_file_when_fields_change(self):
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                article_root = Path(d) / "article_analysis"
                article_root.mkdir(parents=True, exist_ok=True)
                (article_root / "stable-existing-id.json").write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "article_id": "stable-existing-id",
                            "account": "旧账号",
                            "title": "旧标题",
                            "url": "https://mp.weixin.qq.com/s/old-link",
                            "published_at": "2026-06-11 21:30",
                            "topic": "旧主题",
                            "core_points": ["旧观点"],
                            "audience": "旧读者",
                            "risks": ["旧风险"],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "article_id": "stable-existing-id",
                    "account": account_name or "旧账号",
                    "title": "新标题",
                    "date": "2026-06-12",
                    "published_at": "2026-06-12 08:00",
                    "url": "https://mp.weixin.qq.com/s/new-link",
                    "markdown": "# 新标题\n\n正文",
                }
                wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
                wechat_crawler.analyze_single_article = lambda config, article: {
                    "status": "ok",
                    "article_id": article["article_id"],
                    "account": article["account"],
                    "title": article["title"],
                    "url": article["url"],
                    "published_at": article["published_at"],
                    "topic": "新主题",
                    "core_points": ["新观点"],
                    "audience": "新读者",
                    "risks": ["新风险"],
                }

                wechat_crawler.run_reanalyze_from_url(
                    "https://mp.weixin.qq.com/s/new-link",
                    article_id="stable-existing-id",
                    push=False,
                    config={"analysis_enabled": True, "analysis_output_dir": d},
                )

                json_files = sorted(p.name for p in article_root.glob("*.json"))
                self.assertEqual(json_files, ["stable-existing-id.json"])
                saved = json.loads((article_root / "stable-existing-id.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["title"], "新标题")
                self.assertEqual(saved["url"], "https://mp.weixin.qq.com/s/new-link")
                self.assertEqual(saved["published_at"], "2026-06-12 08:00")
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze

    def test_run_reanalyze_from_url_propagates_index_refresh_failure(self):
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-12",
                "published_at": "2026-06-12 08:00",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
            wechat_crawler.analyze_single_article = lambda config, article: {
                "status": "ok",
                "article_id": "refresh-fail",
                "account": "测试号",
                "title": "标题",
                "url": article["url"],
                "published_at": article["published_at"],
                "topic": "主题",
                "core_points": ["观点"],
                "audience": "读者",
                "risks": ["风险"],
            }
            wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None

            def raise_refresh(config):
                raise RuntimeError("refresh_failed")

            wechat_crawler.build_analysis_index_html = raise_refresh

            with self.assertRaisesRegex(RuntimeError, "refresh_failed"):
                wechat_crawler.run_reanalyze_from_url(
                    "https://mp.weixin.qq.com/s/refresh-fail",
                    push=False,
                    config={"analysis_enabled": True},
                )
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist

    def test_handle_reanalyze_api_request_returns_error_when_index_refresh_fails(self):
        old_runner = getattr(wechat_crawler, "run_reanalyze_from_url", None)
        try:
            def raise_refresh(*args, **kwargs):
                raise RuntimeError("refresh_failed")

            wechat_crawler.run_reanalyze_from_url = raise_refresh

            result = wechat_crawler.handle_reanalyze_api_request(
                {
                    "article_id": "aid-refresh",
                    "url": "https://mp.weixin.qq.com/s/refresh-fail",
                    "provider": "ollama",
                },
                {"analysis_enabled": True},
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["article_id"], "aid-refresh")
            self.assertIn("refresh_failed", result["reason"])
        finally:
            if old_runner is not None:
                wechat_crawler.run_reanalyze_from_url = old_runner

    def test_handle_reanalyze_api_request_preserves_success_cache_when_forced_provider_run_fails(self):
        article_id = "aid-preserve-cache"
        cached = {
            "status": "ok",
            "article_id": article_id,
            "account": "测试号",
            "title": "旧成功标题",
            "url": "https://mp.weixin.qq.com/s/api-preserve-cache",
            "published_at": "2026-06-14 11:00",
            "date": "",
            "summary": "旧成功缓存结果",
            "topic": "",
            "core_points": [],
            "audience": "",
            "risks": [],
            "source": "cache",
        }
        cached_text = json.dumps(cached, ensure_ascii=False, indent=2)

        old_fetch = wechat_crawler.fetch_article_markdown
        old_build_index = getattr(wechat_crawler, "build_analysis_index_html", None)
        old_crawler_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_analysis_local = article_analysis.call_ollama_chat
        try:
            with tempfile.TemporaryDirectory() as d:
                cache_path = Path(d) / "article_analysis" / f"{article_id}.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(cached_text, encoding="utf-8")

                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "article_id": article_id,
                    "account": "测试号",
                    "title": "本次重解读标题",
                    "date": "2026-06-14",
                    "published_at": "2026-06-14 11:05",
                    "url": article["link"],
                    "markdown": "# 标题\n\n正文",
                }
                wechat_crawler.build_analysis_index_html = lambda config: None
                wechat_crawler.analyze_single_article = article_analysis.analyze_single_article
                article_analysis.call_ollama_chat = lambda config, prompt: (_ for _ in ()).throw(
                    requests.Timeout("forced timeout")
                )

                result = wechat_crawler.handle_reanalyze_api_request(
                    {
                        "article_id": article_id,
                        "url": "https://mp.weixin.qq.com/s/api-preserve-cache",
                        "provider": "ollama",
                    },
                    {
                        "analysis_enabled": True,
                        "analysis_output_dir": d,
                        "analysis_skip_if_exists": True,
                        "analysis_news_interpret_url": "https://news.example.com/api/telegraph/interpret",
                    },
                )

                self.assertEqual(result["status"], "error")
                self.assertEqual(result["article_id"], article_id)
                self.assertEqual(result["reason"], "ollama_timeout")
                self.assertEqual(cache_path.read_text(encoding="utf-8"), cached_text)
                self.assertEqual(
                    json.loads(cache_path.read_text(encoding="utf-8"))["summary"],
                    "旧成功缓存结果",
                )
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            if old_build_index is not None:
                wechat_crawler.build_analysis_index_html = old_build_index
            if old_crawler_analyze is not None:
                wechat_crawler.analyze_single_article = old_crawler_analyze
            article_analysis.call_ollama_chat = old_analysis_local

    def test_main_supports_serve_reanalyze_cli(self):
        old_argv = list(wechat_crawler.sys.argv)
        old_loader = wechat_crawler.load_json
        old_server = getattr(wechat_crawler, "run_reanalyze_api_server", None)
        try:
            wechat_crawler.sys.argv = ["wechat_crawler.py", "--serve-reanalyze"]
            wechat_crawler.load_json = lambda path: {}
            called = []
            wechat_crawler.run_reanalyze_api_server = (
                lambda config, host="127.0.0.1", port=8766: called.append((config, host, port))
            )

            wechat_crawler.main()

            self.assertEqual(called, [({}, "127.0.0.1", 8766)])
        finally:
            wechat_crawler.sys.argv = old_argv
            wechat_crawler.load_json = old_loader
            if old_server is not None:
                wechat_crawler.run_reanalyze_api_server = old_server

    def test_main_supports_serve_analysis_static_cli(self):
        old_argv = list(wechat_crawler.sys.argv)
        old_loader = wechat_crawler.load_json
        old_server = getattr(wechat_crawler, "run_analysis_static_server", None)
        try:
            wechat_crawler.sys.argv = ["wechat_crawler.py", "--serve-analysis-static"]
            wechat_crawler.load_json = lambda path: {}
            called = []
            wechat_crawler.run_analysis_static_server = (
                lambda config, host="127.0.0.1", port=8765, directory=None: called.append((config, host, port, directory))
            )

            wechat_crawler.main()

            self.assertEqual(called, [({}, "127.0.0.1", 8765, str(wechat_crawler.OUTPUT_ROOT))])
        finally:
            wechat_crawler.sys.argv = old_argv
            wechat_crawler.load_json = old_loader
            if old_server is not None:
                wechat_crawler.run_analysis_static_server = old_server

    def test_main_does_not_wait_for_async_jobs_in_single_extract_latest_cli(self):
        old_argv = list(wechat_crawler.sys.argv)
        old_loader = wechat_crawler.load_json
        old_runner = wechat_crawler.run_extract_latest
        old_wait = getattr(wechat_crawler, "_wait_for_async_jobs", None)
        try:
            wechat_crawler.sys.argv = ["wechat_crawler.py", "--extract-latest", "--account", "测试号"]
            wechat_crawler.load_json = lambda path: {"token": "t", "cookie": "c", "analysis_enabled": True}
            wechat_crawler.run_extract_latest = lambda *args, **kwargs: {"analysis": {"status": "pending"}}
            wechat_crawler._wait_for_async_jobs = lambda *args, **kwargs: self.fail("CLI 不应等待异步解读完成")

            wechat_crawler.main()
        finally:
            wechat_crawler.sys.argv = old_argv
            wechat_crawler.load_json = old_loader
            wechat_crawler.run_extract_latest = old_runner
            if old_wait is not None:
                wechat_crawler._wait_for_async_jobs = old_wait

    def test_schedule_async_job_uses_detached_process_in_cli_mode(self):
        calls = []

        old_mode = getattr(wechat_crawler, "_ASYNC_JOB_DISPATCH_MODE", None)
        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_popen = getattr(wechat_crawler.subprocess, "Popen", None)
        try:
            wechat_crawler._ASYNC_JOB_DISPATCH_MODE = "process"

            class DummyProcess:
                def __init__(self):
                    self.pid = 12345

            wechat_crawler.subprocess.Popen = lambda *args, **kwargs: calls.append((args, kwargs)) or DummyProcess()

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                result = wechat_crawler._schedule_async_job(
                    "extract_latest_analysis",
                    wechat_crawler._attach_single_article_analysis,
                    {"analysis_enabled": False},
                    {
                        "account": "测试号",
                        "title": "标题",
                        "date": "2026-06-13",
                        "published_at": "2026-06-13 09:30",
                        "url": "https://mp.weixin.qq.com/s/test",
                    },
                )

            self.assertEqual(result["status"], "scheduled")
            self.assertEqual(result["mode"], "process")
            self.assertEqual(len(calls), 1)
            cmd = calls[0][0][0]
            self.assertIn("--run-async-job-file", cmd)
            self.assertTrue(calls[0][1]["start_new_session"])
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            if old_mode is not None:
                wechat_crawler._ASYNC_JOB_DISPATCH_MODE = old_mode
            if old_popen is not None:
                wechat_crawler.subprocess.Popen = old_popen

    def test_schedule_async_job_dedupes_same_single_article_by_effective_article_id(self):
        spawned = []

        old_mode = getattr(wechat_crawler, "_ASYNC_JOB_DISPATCH_MODE", None)
        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        try:
            class DummyProcess:
                pid = 56789

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            wechat_crawler._ASYNC_JOB_DISPATCH_MODE = "process"
            wechat_crawler._spawn_async_job_process = fake_spawn

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)

                first = wechat_crawler._schedule_async_job(
                    "extract_latest_analysis",
                    wechat_crawler._attach_single_article_analysis,
                    {"analysis_enabled": True},
                    {
                        "account": "测试号A",
                        "title": "同一篇文章",
                        "published_at": "2026-06-13 10:00",
                        "url": "https://mp.weixin.qq.com/s/same-article",
                    },
                )
                second = wechat_crawler._schedule_async_job(
                    "extract_latest_analysis",
                    wechat_crawler._attach_single_article_analysis,
                    {"analysis_enabled": True},
                    {
                        "account": "测试号B",
                        "title": "同一篇文章",
                        "published_at": "2026-06-13 10:00",
                        "url": "https://mp.weixin.qq.com/s/same-article",
                    },
                )

                self.assertEqual(first["status"], "scheduled")
                self.assertEqual(second["status"], "deduped")
                self.assertEqual(len(spawned), 1)
                self.assertEqual(len(list((wechat_crawler.OUTPUT_ROOT / "async_jobs").glob("*.json"))), 1)
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            if old_mode is not None:
                wechat_crawler._ASYNC_JOB_DISPATCH_MODE = old_mode
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn

    def test_schedule_async_job_allows_distinct_single_article_jobs(self):
        spawned = []

        old_mode = getattr(wechat_crawler, "_ASYNC_JOB_DISPATCH_MODE", None)
        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        try:
            class DummyProcess:
                pid = 56790

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            wechat_crawler._ASYNC_JOB_DISPATCH_MODE = "process"
            wechat_crawler._spawn_async_job_process = fake_spawn

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)

                first = wechat_crawler._schedule_async_job(
                    "extract_latest_analysis",
                    wechat_crawler._attach_single_article_analysis,
                    {"analysis_enabled": True},
                    {
                        "account": "测试号",
                        "title": "文章A",
                        "published_at": "2026-06-13 10:00",
                        "url": "https://mp.weixin.qq.com/s/article-a",
                    },
                )
                second = wechat_crawler._schedule_async_job(
                    "extract_latest_analysis",
                    wechat_crawler._attach_single_article_analysis,
                    {"analysis_enabled": True},
                    {
                        "account": "测试号",
                        "title": "文章B",
                        "published_at": "2026-06-13 10:01",
                        "url": "https://mp.weixin.qq.com/s/article-b",
                    },
                )

                self.assertEqual(first["status"], "scheduled")
                self.assertEqual(second["status"], "scheduled")
                self.assertEqual(len(spawned), 2)
                self.assertEqual(len(list((wechat_crawler.OUTPUT_ROOT / "async_jobs").glob("*.json"))), 2)
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            if old_mode is not None:
                wechat_crawler._ASYNC_JOB_DISPATCH_MODE = old_mode
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn

    def test_run_async_job_file_executes_single_article_analysis_job(self):
        captured = []

        old_attach = wechat_crawler._attach_single_article_analysis
        try:
            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                captured.append((config, fetched, refresh_index, force_reanalyze))
                return {"status": "ok"}

            wechat_crawler._attach_single_article_analysis = fake_attach

            with tempfile.TemporaryDirectory() as d:
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {"title": "标题", "url": "https://mp.weixin.qq.com/s/test"},
                                "refresh_index": False,
                                "force_reanalyze": True,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertEqual(len(captured), 1)
                self.assertEqual(captured[0][0]["analysis_enabled"], True)
                self.assertEqual(captured[0][1]["title"], "标题")
                self.assertFalse(job_path.exists())
        finally:
            wechat_crawler._attach_single_article_analysis = old_attach

    def test_run_async_job_file_rewrites_same_job_for_recoverable_failure_attempt_1_to_2(self):
        spawned = []

        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_attach = wechat_crawler._attach_single_article_analysis
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        try:
            class DummyProcess:
                pid = 23456

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return {
                    "status": "skipped",
                    "reason": "ollama_timeout",
                    "article_id": "aid-retry-1",
                    "title": fetched["title"],
                    "url": fetched["url"],
                }

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler._spawn_async_job_process = fake_spawn

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {
                                    "account": "测试号",
                                    "title": "可恢复失败文章",
                                    "url": "https://mp.weixin.qq.com/s/retry-once",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 1,
                                "retry_mode": "until_success",
                                "first_failed_at": "2026-06-13T09:00:00",
                                "last_failed_at": "2026-06-13T09:00:00",
                                "last_reason": "news_interpret_timeout",
                                "next_retry_at": "2026-06-13T09:00:10",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertTrue(job_path.exists())
                self.assertEqual(spawned, [job_path])
                retry_job = json.loads(job_path.read_text(encoding="utf-8"))
                retry_state = retry_job.get("retry_state") or {}
                self.assertEqual(retry_state.get("attempt"), 2)
                self.assertEqual(retry_state.get("retry_mode"), "until_success")
                self.assertEqual(retry_state.get("first_failed_at"), "2026-06-13T09:00:00")
                self.assertEqual(retry_state.get("last_reason"), "ollama_timeout")
                self.assertTrue(retry_state.get("last_failed_at"))
                self.assertTrue(retry_state.get("next_retry_at"))
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            wechat_crawler._attach_single_article_analysis = old_attach
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn

    def test_run_async_job_file_rewrites_same_job_for_high_attempt_recoverable_failure(self):
        spawned = []

        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_attach = wechat_crawler._attach_single_article_analysis
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        old_notify_once = getattr(wechat_crawler, "send_serverchan_message_once", None)
        try:
            class DummyProcess:
                pid = 23457

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return {
                    "status": "skipped",
                    "reason": "news_interpret_timeout",
                    "article_id": "aid-retry-high-attempt",
                    "title": fetched["title"],
                    "url": fetched["url"],
                }

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler._spawn_async_job_process = fake_spawn
            wechat_crawler.send_serverchan_message_once = (
                lambda *args, **kwargs: self.fail("高 attempt 的 recoverable 失败不应停止并发送失败通知")
            )

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {
                                    "account": "测试号",
                                    "title": "高重试次数文章",
                                    "url": "https://mp.weixin.qq.com/s/retry-high-attempt",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 7,
                                "retry_mode": "until_success",
                                "first_failed_at": "2026-06-13T09:00:00",
                                "last_failed_at": "2026-06-13T09:30:00",
                                "last_reason": "ollama_timeout",
                                "next_retry_at": "2026-06-13T10:00:00",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertTrue(job_path.exists())
                self.assertEqual(spawned, [job_path])
                retry_job = json.loads(job_path.read_text(encoding="utf-8"))
                retry_state = retry_job.get("retry_state") or {}
                self.assertEqual(retry_state.get("attempt"), 8)
                self.assertEqual(retry_state.get("retry_mode"), "until_success")
                self.assertEqual(retry_state.get("first_failed_at"), "2026-06-13T09:00:00")
                self.assertEqual(retry_state.get("last_reason"), "news_interpret_timeout")
                self.assertTrue(retry_state.get("last_failed_at"))
                self.assertTrue(retry_state.get("next_retry_at"))
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            wechat_crawler._attach_single_article_analysis = old_attach
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn
            if old_notify_once is not None:
                wechat_crawler.send_serverchan_message_once = old_notify_once

    def test_run_async_job_file_fail_once_then_ok_rewrites_then_cleans_same_job(self):
        spawned = []
        attach_results = [
            {
                "status": "skipped",
                "reason": "ollama_timeout",
                "article_id": "aid-retry-then-ok",
                "title": "失败一次后成功",
                "url": "https://mp.weixin.qq.com/s/fail-once-then-ok",
            },
            {
                "status": "ok",
                "article_id": "aid-retry-then-ok",
                "summary": "最终成功",
                "title": "失败一次后成功",
                "url": "https://mp.weixin.qq.com/s/fail-once-then-ok",
            },
        ]

        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_attach = wechat_crawler._attach_single_article_analysis
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        old_notify_once = getattr(wechat_crawler, "send_serverchan_message_once", None)
        try:
            class DummyProcess:
                pid = 34567

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return attach_results.pop(0)

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler._spawn_async_job_process = fake_spawn
            wechat_crawler.send_serverchan_message_once = (
                lambda *args, **kwargs: self.fail("recoverable->ok 不应发送失败通知")
            )

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {
                                    "account": "测试号",
                                    "title": "失败一次后成功",
                                    "url": "https://mp.weixin.qq.com/s/fail-once-then-ok",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 1,
                                "retry_mode": "until_success",
                                "first_failed_at": "2026-06-13T09:00:00",
                                "last_failed_at": "2026-06-13T09:00:00",
                                "last_reason": "news_interpret_timeout",
                                "next_retry_at": "2026-06-13T09:00:10",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))
                self.assertTrue(job_path.exists())
                self.assertEqual(spawned, [job_path])

                spawned.clear()
                wechat_crawler._run_async_job_file(str(job_path))

                self.assertFalse(job_path.exists())
                self.assertEqual(spawned, [])
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            wechat_crawler._attach_single_article_analysis = old_attach
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn
            if old_notify_once is not None:
                wechat_crawler.send_serverchan_message_once = old_notify_once

    def test_run_async_job_file_external_failure_notifies_with_minimum_contract_and_stops(self):
        notifications = []
        spawned = []

        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_attach = wechat_crawler._attach_single_article_analysis
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        old_notify_once = getattr(wechat_crawler, "send_serverchan_message_once", None)
        try:
            class DummyProcess:
                pid = 45678

            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return {
                    "status": "skipped",
                    "reason": "wechat_auth_required",
                    "article_id": "aid-external-stop",
                    "title": fetched["title"],
                    "url": fetched["url"],
                    "account": fetched["account"],
                }

            def fake_spawn(job_path):
                spawned.append(Path(job_path))
                return DummyProcess()

            def fake_notify_once(sendkey, title, desp, timeout=20, dedupe_key=None, ttl_seconds=0, state_dir=None):
                notifications.append(
                    {
                        "sendkey": sendkey,
                        "title": title,
                        "desp": desp,
                    }
                )
                return {"ok": True}

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler._spawn_async_job_process = fake_spawn
            wechat_crawler.send_serverchan_message_once = fake_notify_once

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {
                                    "analysis_enabled": True,
                                    "serverchan_sendkey": "sct-test",
                                },
                                "fetched": {
                                    "account": "测试号",
                                    "title": "外部失败文章",
                                    "url": "https://mp.weixin.qq.com/s/external-stop",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 1,
                                "retry_mode": "stop_on_external",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertFalse(job_path.exists())
                self.assertEqual(spawned, [])
                self.assertEqual(len(notifications), 1)
                self.assertEqual(notifications[0]["sendkey"], "sct-test")
                self.assertIn("测试号", notifications[0]["desp"])
                self.assertIn("外部失败文章", notifications[0]["desp"])
                self.assertIn("https://mp.weixin.qq.com/s/external-stop", notifications[0]["desp"])
                self.assertIn("wechat_auth_required", notifications[0]["desp"])
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            wechat_crawler._attach_single_article_analysis = old_attach
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn
            if old_notify_once is not None:
                wechat_crawler.send_serverchan_message_once = old_notify_once

    def test_run_async_job_file_waits_until_next_retry_at_before_running(self):
        captured = []
        sleep_calls = []
        fake_now = {"value": 1_800_000_000.0}

        old_attach = wechat_crawler._attach_single_article_analysis
        old_time = wechat_crawler.time.time
        old_sleep = wechat_crawler.time.sleep
        try:
            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                captured.append((config, fetched, refresh_index, force_reanalyze, fake_now["value"]))
                return {"status": "ok", "article_id": "aid-wait-retry"}

            def fake_sleep(seconds):
                sleep_calls.append(seconds)
                fake_now["value"] += seconds

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler.time.time = lambda: fake_now["value"]
            wechat_crawler.time.sleep = fake_sleep

            with tempfile.TemporaryDirectory() as d:
                job_path = Path(d) / "job.json"
                next_retry_at = wechat_crawler._async_retry_time_text(fake_now["value"] + 10)
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {
                                    "account": "测试号",
                                    "title": "等待重试文章",
                                    "url": "https://mp.weixin.qq.com/s/wait-retry",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 2,
                                "retry_mode": "until_success",
                                "last_reason": "ollama_timeout",
                                "next_retry_at": next_retry_at,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertEqual(len(captured), 1)
                self.assertEqual(len(sleep_calls), 1)
                self.assertAlmostEqual(sleep_calls[0], 10, delta=0.5)
                self.assertFalse(job_path.exists())
        finally:
            wechat_crawler._attach_single_article_analysis = old_attach
            wechat_crawler.time.time = old_time
            wechat_crawler.time.sleep = old_sleep

    def test_run_async_job_file_keeps_recoverable_job_when_respawn_fails(self):
        old_output_root = wechat_crawler.OUTPUT_ROOT
        old_attach = wechat_crawler._attach_single_article_analysis
        old_spawn = getattr(wechat_crawler, "_spawn_async_job_process", None)
        try:
            def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                return {
                    "status": "skipped",
                    "reason": "ollama_timeout",
                    "article_id": "aid-spawn-fail-keep",
                    "title": fetched["title"],
                    "url": fetched["url"],
                }

            def fail_spawn(job_path):
                raise OSError("spawn boom")

            wechat_crawler._attach_single_article_analysis = fake_attach
            wechat_crawler._spawn_async_job_process = fail_spawn

            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.OUTPUT_ROOT = Path(d)
                job_path = Path(d) / "job.json"
                job_path.write_text(
                    json.dumps(
                        {
                            "name": "extract_latest_analysis",
                            "job_type": "single_article_analysis",
                            "payload": {
                                "config": {"analysis_enabled": True},
                                "fetched": {
                                    "account": "测试号",
                                    "title": "重排拉起失败文章",
                                    "url": "https://mp.weixin.qq.com/s/spawn-fail-keep",
                                },
                                "refresh_index": False,
                                "force_reanalyze": False,
                            },
                            "retry_state": {
                                "attempt": 1,
                                "retry_mode": "until_success",
                                "first_failed_at": "2026-06-13T09:00:00",
                                "last_failed_at": "2026-06-13T09:00:00",
                                "last_reason": "news_interpret_timeout",
                                "next_retry_at": "2026-06-13T09:00:10",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                wechat_crawler._run_async_job_file(str(job_path))

                self.assertTrue(job_path.exists())
                retry_job = json.loads(job_path.read_text(encoding="utf-8"))
                retry_state = retry_job.get("retry_state") or {}
                self.assertEqual(retry_state.get("attempt"), 2)
                self.assertEqual(retry_state.get("last_reason"), "ollama_timeout")
                self.assertTrue(retry_state.get("next_retry_at"))
        finally:
            wechat_crawler.OUTPUT_ROOT = old_output_root
            wechat_crawler._attach_single_article_analysis = old_attach
            if old_spawn is not None:
                wechat_crawler._spawn_async_job_process = old_spawn

    def test_run_extract_from_url_uses_authenticated_headers_from_config(self):
        captured_headers = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        try:
            def fake_fetch(article, headers, account_name=None):
                captured_headers.append(headers)
                return {
                    "account": "测试号",
                    "title": "标题",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": article["link"],
                    "markdown": "# 标题\n\n正文",
                }

            wechat_crawler.fetch_article_markdown = fake_fetch
            wechat_crawler.analyze_single_article = lambda config, article: None

            wechat_crawler.run_extract_from_url(
                "https://mp.weixin.qq.com/s/test-auth",
                save_markdown=False,
                push=False,
                config={"cookie": "cookie=test", "token": "123456"},
            )

            self.assertEqual(captured_headers[0]["Cookie"], "cookie=test")
            self.assertIn("token=123456", captured_headers[0]["Referer"])
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze

    def test_run_extract_from_url_passes_real_config_and_skips_analysis_when_disabled(self):
        analyze_calls = []
        push_configs = []

        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        try:
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-11",
                "published_at": "2026-06-11 21:30",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = (
                lambda config, payload, override_sendkey=None: push_configs.append(config) or {"ok": True}
            )
            wechat_crawler.analyze_single_article = lambda config, article: analyze_calls.append(config) or {
                "status": "ok",
                "topic": "不应执行",
                "core_points": ["不应执行"],
                "audience": "不应执行",
                "risks": ["不应执行"],
                "article_id": "unexpected",
            }
            wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: self.fail("不应持久化分析结果")

            cfg = {"analysis_enabled": False, "serverchan_sendkey": "sct-test"}
            payload = wechat_crawler.run_extract_from_url(
                "https://mp.weixin.qq.com/s/test-config",
                account_name="测试号",
                save_markdown=False,
                push=True,
                config=cfg,
            )

            self.assertIsNone(payload["analysis"])
            self.assertEqual(analyze_calls, [])
            self.assertEqual(push_configs, [cfg])
            self.assertEqual(payload["serverchan"], {"ok": True})
        finally:
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist

    def test_run_extract_latest_attaches_analysis(self):
        persist_calls = []

        old_resolve_fakeid = wechat_crawler.resolve_fakeid
        old_get_headers = wechat_crawler.get_headers
        old_get_articles = wechat_crawler.get_articles
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_save_md = wechat_crawler.save_url_to_md
        try:
            wechat_crawler.resolve_fakeid = lambda *args, **kwargs: "fakeid123"
            wechat_crawler.get_headers = lambda cookie, token: {"Cookie": cookie}
            wechat_crawler.get_articles = lambda *args, **kwargs: (
                [
                    {
                        "title": "标题",
                        "link": "https://mp.weixin.qq.com/s/latest-test",
                        "create_time": 1710000000,
                    }
                ],
                1,
                None,
            )
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-11",
                "published_at": "2026-06-11 21:30",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = lambda *args, **kwargs: {"ok": True}
            wechat_crawler.analyze_single_article = lambda config, article: {
                "status": "ok",
                "topic": "主线延续",
                "core_points": ["量能配合"],
                "audience": "波段跟踪者",
                "risks": ["高位分歧"],
                "article_id": "latest-1",
            }
            wechat_crawler.persist_single_analysis_outputs = (
                lambda config, analysis: persist_calls.append(analysis["article_id"])
            )
            wechat_crawler.save_url_to_md = lambda *args, **kwargs: None

            payload = wechat_crawler.run_extract_latest(
                {"token": "t", "cookie": "c", "analysis_enabled": True},
                account_name_arg="测试号",
                save_markdown=False,
                push=False,
            )

            self.assertEqual(payload["analysis"]["topic"], "主线延续")
            self.assertEqual(persist_calls, ["latest-1"])
        finally:
            wechat_crawler.resolve_fakeid = old_resolve_fakeid
            wechat_crawler.get_headers = old_get_headers
            wechat_crawler.get_articles = old_get_articles
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            wechat_crawler.save_url_to_md = old_save_md


class TestCrawlerBatchAnalysisIntegration(unittest.TestCase):
    def test_push_article_to_serverchan_uses_configured_summary_link_and_hides_analysis(self):
        calls = []

        old_send = wechat_crawler.send_serverchan_message
        try:
            wechat_crawler.send_serverchan_message = (
                lambda sendkey, title, desp: calls.append((sendkey, title, desp)) or {"ok": True}
            )
            result = wechat_crawler.push_article_to_serverchan(
                {"serverchan_sendkey": "sct-test", "analysis_public_base_url": "https://wx.example.com"},
                {
                    "account": "测试号",
                    "group": "测试分组",
                    "title": "单篇标题",
                    "published_at": "2026-06-13 09:30",
                    "url": "https://mp.weixin.qq.com/s/single",
                    "analysis": {"status": "ok", "summary": "不应出现在通知里"},
                },
            )

            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(calls), 1)
            self.assertIn("https://wx.example.com/article_analysis", calls[0][2])
            self.assertNotIn("分类：", calls[0][2])
            self.assertNotIn("测试分组", calls[0][2])
            self.assertNotIn("https://mp.weixin.qq.com/s/single", calls[0][2])
            self.assertNotIn("阅读全文", calls[0][2])
            self.assertNotIn("AI解读", calls[0][2])
            self.assertNotIn("不应出现在通知里", calls[0][2])
        finally:
            wechat_crawler.send_serverchan_message = old_send

    def test_push_articles_to_serverchan_hides_batch_section_when_disabled(self):
        calls = []

        old_send = wechat_crawler.send_serverchan_message
        try:
            wechat_crawler.send_serverchan_message = (
                lambda sendkey, title, desp: calls.append((sendkey, title, desp)) or {"ok": True}
            )
            result = wechat_crawler.push_articles_to_serverchan(
                {"serverchan_sendkey": "sct-test", "analysis_push_batch": False},
                [
                    {
                        "account": "号A",
                        "group": "测试分组",
                        "title": "A 文",
                        "published_at": "2026-06-11 21:30",
                        "url": "https://mp.weixin.qq.com/s/a",
                    }
                ],
                batch_analysis={
                    "status": "ok",
                    "batch_focus": "情绪修复",
                    "shared_themes": ["资金回流"],
                    "priority_reads": ["A 文，因信息密度高"],
                },
            )

            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(calls), 1)
            self.assertNotIn("本轮解读", calls[0][2])
        finally:
            wechat_crawler.send_serverchan_message = old_send

    def test_build_serverchan_markdown_articles_renders_batch_analysis(self):
        old_public = os.environ.get("WECHAT_ANALYSIS_PUBLIC_BASE_URL")
        try:
            os.environ.pop("WECHAT_ANALYSIS_PUBLIC_BASE_URL", None)
            desp = wechat_crawler.build_serverchan_markdown_articles(
                [
                    {
                        "account": "号A",
                        "group": "测试分组",
                        "title": "A 文",
                        "published_at": "2026-06-11 21:30",
                        "url": "https://mp.weixin.qq.com/s/a",
                    }
                ],
                batch_analysis={
                    "status": "ok",
                    "batch_focus": "情绪修复",
                    "shared_themes": ["资金回流"],
                    "priority_reads": ["A 文，因信息密度高"],
                },
            )

            self.assertIn("/article_analysis", desp)
            self.assertNotIn("https://wx.coco777.vip/article_analysis", desp)
            self.assertNotIn("https://mp.weixin.qq.com/s/a", desp)
            self.assertNotIn("本轮解读", desp)
            self.assertNotIn("情绪修复", desp)
            self.assertNotIn("资金回流", desp)
        finally:
            if old_public is None:
                os.environ.pop("WECHAT_ANALYSIS_PUBLIC_BASE_URL", None)
            else:
                os.environ["WECHAT_ANALYSIS_PUBLIC_BASE_URL"] = old_public

    def test_build_serverchan_markdown_articles_renders_per_article_analysis(self):
        old_public = os.environ.get("WECHAT_ANALYSIS_PUBLIC_BASE_URL")
        try:
            os.environ.pop("WECHAT_ANALYSIS_PUBLIC_BASE_URL", None)
            desp = wechat_crawler.build_serverchan_markdown_articles(
                [
                    {
                        "account": "号A",
                        "group": "测试分组",
                        "title": "A 文",
                        "published_at": "2026-06-11 21:30",
                        "url": "https://mp.weixin.qq.com/s/a",
                        "analysis": {
                            "status": "ok",
                            "topic": "单篇主题",
                            "core_points": ["观点一", "观点二"],
                            "audience": "测试者",
                            "risks": ["风险一"],
                        },
                    }
                ],
                batch_analysis=None,
            )

            self.assertIn("/article_analysis", desp)
            self.assertNotIn("https://wx.coco777.vip/article_analysis", desp)
            self.assertNotIn("https://mp.weixin.qq.com/s/a", desp)
            self.assertNotIn("AI解读", desp)
            self.assertNotIn("单篇主题", desp)
            self.assertNotIn("观点一", desp)
        finally:
            if old_public is None:
                os.environ.pop("WECHAT_ANALYSIS_PUBLIC_BASE_URL", None)
            else:
                os.environ["WECHAT_ANALYSIS_PUBLIC_BASE_URL"] = old_public

    def test_push_articles_to_serverchan_uses_configured_summary_link_and_hides_all_analysis(self):
        calls = []

        old_send = wechat_crawler.send_serverchan_message
        try:
            wechat_crawler.send_serverchan_message = (
                lambda sendkey, title, desp: calls.append((sendkey, title, desp)) or {"ok": True}
            )
            result = wechat_crawler.push_articles_to_serverchan(
                {"serverchan_sendkey": "sct-test", "analysis_public_base_url": "https://wx.example.com"},
                [
                    {
                        "account": "号A",
                        "group": "测试分组",
                        "title": "A 文",
                        "published_at": "2026-06-11 21:30",
                        "url": "https://mp.weixin.qq.com/s/a",
                        "analysis": {"status": "ok", "summary": "不应展示"},
                    }
                ],
                batch_analysis={
                    "status": "ok",
                    "batch_focus": "也不应展示",
                    "shared_themes": ["资金回流"],
                    "priority_reads": ["A 文，因信息密度高"],
                },
            )

            self.assertEqual(result, {"ok": True})
            self.assertEqual(len(calls), 1)
            self.assertIn("https://wx.example.com/article_analysis", calls[0][2])
            self.assertNotIn("https://mp.weixin.qq.com/s/a", calls[0][2])
            self.assertNotIn("AI解读", calls[0][2])
            self.assertNotIn("本轮解读", calls[0][2])
            self.assertNotIn("不应展示", calls[0][2])
            self.assertNotIn("也不应展示", calls[0][2])
        finally:
            wechat_crawler.send_serverchan_message = old_send

    def test_run_push_latest_all_skips_batch_analysis_when_disabled(self):
        old_load_accounts = wechat_crawler.load_accounts_list
        old_extract = wechat_crawler._extract_latest_payload_for_account
        old_fetch = wechat_crawler.fetch_article_markdown
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_batch = getattr(wechat_crawler, "summarize_analysis_batch", None)
        old_persist_single = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_persist_batch = getattr(wechat_crawler, "persist_batch_analysis_outputs", None)
        old_save_md = wechat_crawler.save_url_to_md
        try:
            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.load_accounts_list = lambda config, accounts_file_override=None: [
                    {"name": "号A", "fakeid": "fidA", "group": "测试分组"}
                ]
                wechat_crawler._extract_latest_payload_for_account = lambda **kwargs: {
                    "account": "号A",
                    "fakeid": "fidA",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/a",
                    "_raw_article": {"title": "A 文", "link": "https://mp.weixin.qq.com/s/a"},
                }
                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "account": "号A",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": article["link"],
                    "markdown": "# A 文\n\n正文",
                }
                wechat_crawler.analyze_single_article = lambda config, article: {
                    "status": "skipped",
                    "reason": "analysis_disabled",
                    "article_id": "aid-disabled",
                }
                wechat_crawler.summarize_analysis_batch = (
                    lambda config, analyses, batch_id: {"status": "skipped", "reason": "analysis_disabled", "batch_id": batch_id}
                )
                wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
                wechat_crawler.persist_batch_analysis_outputs = lambda config, analysis: None
                wechat_crawler.save_url_to_md = lambda *args, **kwargs: None

                payload = wechat_crawler.run_push_latest_all(
                    {"token": "t", "cookie": "c", "analysis_enabled": False},
                    push=False,
                    save_markdown=False,
                    push_state_file=str(Path(d) / "push_state.json"),
                )

                self.assertEqual(payload["articles"][0]["analysis"]["status"], "skipped")
                self.assertEqual(payload["batch_analysis"]["status"], "skipped")
                self.assertEqual(payload["batch_analysis"]["reason"], "analysis_disabled")
        finally:
            wechat_crawler.load_accounts_list = old_load_accounts
            wechat_crawler._extract_latest_payload_for_account = old_extract
            wechat_crawler.fetch_article_markdown = old_fetch
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_batch is not None:
                wechat_crawler.summarize_analysis_batch = old_batch
            if old_persist_single is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist_single
            if old_persist_batch is not None:
                wechat_crawler.persist_batch_analysis_outputs = old_persist_batch
            wechat_crawler.save_url_to_md = old_save_md

    def test_run_push_latest_all_adds_batch_analysis(self):
        old_load_accounts = wechat_crawler.load_accounts_list
        old_extract = wechat_crawler._extract_latest_payload_for_account
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_articles_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_batch = getattr(wechat_crawler, "summarize_analysis_batch", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_persist_batch = getattr(wechat_crawler, "persist_batch_analysis_outputs", None)
        old_config = getattr(wechat_crawler, "get_analysis_config", None)
        old_save_md = wechat_crawler.save_url_to_md
        try:
            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.load_accounts_list = lambda config, accounts_file_override=None: [
                    {"name": "号A", "fakeid": "fidA", "group": "测试分组"}
                ]
                wechat_crawler._extract_latest_payload_for_account = lambda **kwargs: {
                    "account": "号A",
                    "fakeid": "fidA",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/a",
                    "_raw_article": {"title": "A 文", "link": "https://mp.weixin.qq.com/s/a"},
                }
                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "account": "号A",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": article["link"],
                    "markdown": "# A 文\n\n正文",
                }
                wechat_crawler.push_articles_to_serverchan = lambda *args, **kwargs: {"ok": True}
                wechat_crawler.analyze_single_article = lambda config, article: {
                    "status": "ok",
                    "article_id": "aid1",
                    "topic": "题材修复",
                    "core_points": ["回流"],
                    "audience": "观察者",
                    "risks": ["震荡"],
                }
                wechat_crawler.summarize_analysis_batch = lambda config, analyses, batch_id: {
                    "status": "ok",
                    "batch_id": batch_id,
                    "batch_focus": "情绪修复",
                    "shared_themes": ["资金回流"],
                    "priority_reads": ["A 文，因信息密度高"],
                }
                wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
                wechat_crawler.persist_batch_analysis_outputs = lambda config, analysis: None
                wechat_crawler.get_analysis_config = lambda config: {"analysis_enabled": True}
                wechat_crawler.save_url_to_md = lambda *args, **kwargs: None

                config = {"token": "t", "cookie": "c"}
                payload = wechat_crawler.run_push_latest_all(
                    config,
                    push=False,
                    save_markdown=False,
                    push_state_file=str(Path(d) / "push_state.json"),
                )

                self.assertEqual(payload["articles"][0]["analysis"]["topic"], "题材修复")
                self.assertEqual(payload["batch_analysis"]["batch_focus"], "情绪修复")
        finally:
            wechat_crawler.load_accounts_list = old_load_accounts
            wechat_crawler._extract_latest_payload_for_account = old_extract
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_articles_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_batch is not None:
                wechat_crawler.summarize_analysis_batch = old_batch
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            if old_persist_batch is not None:
                wechat_crawler.persist_batch_analysis_outputs = old_persist_batch
            if old_config is not None:
                wechat_crawler.get_analysis_config = old_config
            wechat_crawler.save_url_to_md = old_save_md

    def test_run_extract_latest_pushes_before_analysis(self):
        events = []
        scheduled = []

        old_resolve_fakeid = wechat_crawler.resolve_fakeid
        old_get_headers = wechat_crawler.get_headers
        old_get_articles = wechat_crawler.get_articles
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_article_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_persist = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_save_md = wechat_crawler.save_url_to_md
        old_schedule = getattr(wechat_crawler, "_schedule_async_job", None)
        try:
            wechat_crawler.resolve_fakeid = lambda *args, **kwargs: "fakeid123"
            wechat_crawler.get_headers = lambda cookie, token: {"Cookie": cookie}
            wechat_crawler.get_articles = lambda *args, **kwargs: (
                [
                    {
                        "title": "标题",
                        "link": "https://mp.weixin.qq.com/s/latest-order",
                        "create_time": 1710000000,
                    }
                ],
                1,
                None,
            )
            wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                "account": "测试号",
                "title": "标题",
                "date": "2026-06-11",
                "published_at": "2026-06-11 21:30",
                "url": article["link"],
                "markdown": "# 标题\n\n正文",
            }
            wechat_crawler.push_article_to_serverchan = (
                lambda *args, **kwargs: events.append("push") or {"ok": True}
            )
            wechat_crawler.analyze_single_article = (
                lambda config, article: events.append("analyze")
                or {
                    "status": "ok",
                    "summary": "解读结果",
                    "article_id": "latest-order-1",
                }
            )
            wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
            wechat_crawler.save_url_to_md = lambda *args, **kwargs: None
            wechat_crawler._schedule_async_job = (
                lambda name, func, *args, **kwargs: scheduled.append(name) or {"status": "scheduled", "name": name}
            )

            payload = wechat_crawler.run_extract_latest(
                {"token": "t", "cookie": "c", "analysis_enabled": True},
                account_name_arg="测试号",
                save_markdown=False,
                push=True,
            )

            self.assertEqual(events, ["push"])
            self.assertEqual(scheduled, ["extract_latest_analysis"])
            self.assertEqual(payload["analysis"]["status"], "pending")
            self.assertEqual(payload["analysis"]["reason"], "scheduled_async")
        finally:
            wechat_crawler.resolve_fakeid = old_resolve_fakeid
            wechat_crawler.get_headers = old_get_headers
            wechat_crawler.get_articles = old_get_articles
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_article_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_persist is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist
            wechat_crawler.save_url_to_md = old_save_md
            if old_schedule is not None:
                wechat_crawler._schedule_async_job = old_schedule

    def test_run_push_latest_all_pushes_before_analysis(self):
        events = []
        scheduled = []

        old_load_accounts = wechat_crawler.load_accounts_list
        old_extract = wechat_crawler._extract_latest_payload_for_account
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_articles_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_batch = getattr(wechat_crawler, "summarize_analysis_batch", None)
        old_persist_single = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_persist_batch = getattr(wechat_crawler, "persist_batch_analysis_outputs", None)
        old_save_md = wechat_crawler.save_url_to_md
        old_refresh = getattr(wechat_crawler, "build_analysis_index_html", None)
        old_schedule = getattr(wechat_crawler, "_schedule_async_job", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.load_accounts_list = lambda config, accounts_file_override=None: [
                    {"name": "号A", "fakeid": "fidA", "group": "测试分组"}
                ]
                wechat_crawler._extract_latest_payload_for_account = lambda **kwargs: {
                    "account": "号A",
                    "fakeid": "fidA",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/a",
                    "_raw_article": {"title": "A 文", "link": "https://mp.weixin.qq.com/s/a"},
                }
                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "account": "号A",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": article["link"],
                    "markdown": "# A 文\n\n正文",
                }
                wechat_crawler.push_articles_to_serverchan = (
                    lambda *args, **kwargs: events.append("push") or {"ok": True}
                )
                wechat_crawler.analyze_single_article = (
                    lambda config, article: events.append("analyze")
                    or {
                        "status": "ok",
                        "article_id": "aid1",
                        "summary": "单篇解读",
                    }
                )
                wechat_crawler.summarize_analysis_batch = (
                    lambda config, analyses, batch_id: events.append("batch")
                    or {
                        "status": "ok",
                        "batch_id": batch_id,
                        "batch_focus": "情绪修复",
                        "shared_themes": ["资金回流"],
                        "priority_reads": ["A 文"],
                    }
                )
                wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
                wechat_crawler.persist_batch_analysis_outputs = lambda config, analysis: None
                wechat_crawler.save_url_to_md = lambda *args, **kwargs: None
                wechat_crawler.build_analysis_index_html = lambda config: None
                wechat_crawler._schedule_async_job = (
                    lambda name, func, *args, **kwargs: scheduled.append(name) or {"status": "scheduled", "name": name}
                )
                state_path = Path(d) / "push_state.json"

                payload = wechat_crawler.run_push_latest_all(
                    {"token": "t", "cookie": "c", "analysis_enabled": True},
                    push=True,
                    save_markdown=False,
                    push_state_file=str(state_path),
                )

                self.assertEqual(events, ["push"])
                self.assertEqual(scheduled, ["push_latest_all_analysis"])
                self.assertEqual(payload["batch_analysis"]["status"], "pending")
                self.assertEqual(payload["batch_analysis"]["reason"], "scheduled_async")
                push_state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(push_state["fidA"]["last_pushed_url"], "https://mp.weixin.qq.com/s/a")
        finally:
            wechat_crawler.load_accounts_list = old_load_accounts
            wechat_crawler._extract_latest_payload_for_account = old_extract
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_articles_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_batch is not None:
                wechat_crawler.summarize_analysis_batch = old_batch
            if old_persist_single is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist_single
            if old_persist_batch is not None:
                wechat_crawler.persist_batch_analysis_outputs = old_persist_batch
            wechat_crawler.save_url_to_md = old_save_md
            if old_refresh is not None:
                wechat_crawler.build_analysis_index_html = old_refresh
            if old_schedule is not None:
                wechat_crawler._schedule_async_job = old_schedule

    def test_run_push_latest_all_passes_article_summary_to_batch_summary_and_keeps_summary(self):
        captured = {}

        old_load_accounts = wechat_crawler.load_accounts_list
        old_extract = wechat_crawler._extract_latest_payload_for_account
        old_fetch = wechat_crawler.fetch_article_markdown
        old_push = wechat_crawler.push_articles_to_serverchan
        old_analyze = getattr(wechat_crawler, "analyze_single_article", None)
        old_batch = getattr(wechat_crawler, "summarize_analysis_batch", None)
        old_persist_single = getattr(wechat_crawler, "persist_single_analysis_outputs", None)
        old_persist_batch = getattr(wechat_crawler, "persist_batch_analysis_outputs", None)
        old_save_md = wechat_crawler.save_url_to_md
        try:
            with tempfile.TemporaryDirectory() as d:
                wechat_crawler.load_accounts_list = lambda config, accounts_file_override=None: [
                    {"name": "号A", "fakeid": "fidA", "group": "测试分组"}
                ]
                wechat_crawler._extract_latest_payload_for_account = lambda **kwargs: {
                    "account": "号A",
                    "fakeid": "fidA",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": "https://mp.weixin.qq.com/s/a",
                    "_raw_article": {"title": "A 文", "link": "https://mp.weixin.qq.com/s/a"},
                }
                wechat_crawler.fetch_article_markdown = lambda article, headers, account_name=None: {
                    "account": "号A",
                    "title": "A 文",
                    "date": "2026-06-11",
                    "published_at": "2026-06-11 21:30",
                    "url": article["link"],
                    "markdown": "# A 文\n\n正文",
                }
                wechat_crawler.push_articles_to_serverchan = lambda *args, **kwargs: {"ok": True}
                wechat_crawler.analyze_single_article = lambda config, article: {
                    "status": "ok",
                    "article_id": "aid1",
                    "summary": "单篇总结内容",
                }
                def fake_summarize(config, analyses, batch_id):
                    captured["analyses"] = analyses
                    return {
                        "status": "ok",
                        "batch_id": batch_id,
                        "summary": "本轮汇总总结",
                        "batch_focus": "情绪修复",
                        "shared_themes": ["资金回流"],
                        "priority_reads": ["A 文"],
                    }

                wechat_crawler.summarize_analysis_batch = fake_summarize
                wechat_crawler.persist_single_analysis_outputs = lambda config, analysis: None
                wechat_crawler.persist_batch_analysis_outputs = lambda config, analysis: None
                wechat_crawler.save_url_to_md = lambda *args, **kwargs: None

                payload = wechat_crawler.run_push_latest_all(
                    {"token": "t", "cookie": "c", "analysis_enabled": True},
                    push=False,
                    save_markdown=False,
                    push_state_file=str(Path(d) / "push_state.json"),
                )

                self.assertEqual(captured["analyses"][0]["summary"], "单篇总结内容")
                self.assertEqual(payload["batch_analysis"]["summary"], "本轮汇总总结")
        finally:
            wechat_crawler.load_accounts_list = old_load_accounts
            wechat_crawler._extract_latest_payload_for_account = old_extract
            wechat_crawler.fetch_article_markdown = old_fetch
            wechat_crawler.push_articles_to_serverchan = old_push
            if old_analyze is not None:
                wechat_crawler.analyze_single_article = old_analyze
            if old_batch is not None:
                wechat_crawler.summarize_analysis_batch = old_batch
            if old_persist_single is not None:
                wechat_crawler.persist_single_analysis_outputs = old_persist_single
            if old_persist_batch is not None:
                wechat_crawler.persist_batch_analysis_outputs = old_persist_batch
            wechat_crawler.save_url_to_md = old_save_md


class TestBatchSummaryOutput(unittest.TestCase):
    def test_summarize_analysis_batch_keeps_summary_field(self):
        old_call = article_analysis.call_ollama_chat
        try:
            article_analysis.call_ollama_chat = (
                lambda config, prompt: json.dumps(
                    {
                        "summary": "本轮汇总总结",
                        "batch_focus": "题材轮动",
                        "shared_themes": ["风险偏好回升"],
                        "priority_reads": ["A 文，因信息密度高"],
                    },
                    ensure_ascii=False,
                )
            )

            result = article_analysis.summarize_analysis_batch(
                {"analysis_enabled": True},
                [
                    {
                        "status": "ok",
                        "account": "号A",
                        "title": "A 文",
                        "summary": "单篇总结",
                    }
                ],
                batch_id="20260613_010000",
            )

            self.assertEqual(result["summary"], "本轮汇总总结")
            self.assertEqual(result["batch_focus"], "题材轮动")
        finally:
            article_analysis.call_ollama_chat = old_call


if __name__ == "__main__":
    unittest.main()

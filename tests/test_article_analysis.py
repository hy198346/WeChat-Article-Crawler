import json
import tempfile
import unittest
from pathlib import Path

import article_analysis


class TestArticleAnalysis(unittest.TestCase):
    def test_get_analysis_config_merges_defaults(self):
        cfg = article_analysis.get_analysis_config(
            {"analysis_enabled": False, "analysis_timeout_seconds": 9}
        )
        self.assertFalse(cfg["analysis_enabled"])
        self.assertEqual(cfg["analysis_timeout_seconds"], 9)
        self.assertEqual(cfg["analysis_model"], "qwen2.5-coder:14b-cpu")

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
                self.assertIn("\"topic\"", prompt)
                self.assertIn("\"core_points\"", prompt)
                self.assertIn("测试标题", prompt)
                saved = Path(d) / "article_analysis" / f"{result['article_id']}.json"
                self.assertTrue(saved.exists())
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
                    {"status": "ok", "article_id": article_id, "topic": "缓存命中"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = article_analysis.analyze_single_article(config, article)
            self.assertEqual(result["topic"], "缓存命中")

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


if __name__ == "__main__":
    unittest.main()

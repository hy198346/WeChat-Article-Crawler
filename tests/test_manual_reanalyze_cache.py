import tempfile
import unittest
from pathlib import Path

import wechat_crawler


class TestManualReanalyzeCache(unittest.TestCase):
    def test_load_cached_reanalyze_article_prefers_local_markdown(self):
        article_id = "8c072e31ebfdf21b319ad9bb5c27195fa0d5bb99"
        article_url = "https://mp.weixin.qq.com/s/15AfclsKv0RAg-LTRpctGA"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out_dir = root / "output" / "article_analysis"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{article_id}.md").write_text(
                "# 给一个选择，飞三亚，还是去青岛？\n\n"
                "**Date:** 2026-06-16 12:20\n"
                f"**Link:** {article_url}\n"
                "**Account:** 旅行雷达\n\n"
                "正文内容\n",
                encoding="utf-8",
            )
            (out_dir / f"{article_id}.json").write_text(
                '{"article_id":"8c072e31ebfdf21b319ad9bb5c27195fa0d5bb99","title":"给一个选择，飞三亚，还是去青岛？","url":"https://mp.weixin.qq.com/s/15AfclsKv0RAg-LTRpctGA","published_at":"2026-06-16 12:20","account":"旅行雷达"}',
                encoding="utf-8",
            )

            cached = wechat_crawler._load_cached_reanalyze_article(
                {"analysis_output_dir": str(root / "output")},
                article_id=article_id,
                article_url=article_url,
                account_name="旅行雷达",
            )

            self.assertIsNotNone(cached)
            self.assertEqual(cached["title"], "给一个选择，飞三亚，还是去青岛？")
            self.assertEqual(cached["url"], article_url)
            self.assertEqual(cached["published_at"], "2026-06-16 12:20")
            self.assertEqual(cached["account"], "旅行雷达")
            self.assertIn("正文内容", cached["markdown"])

    def test_run_reanalyze_from_url_refetches_when_cached_markdown_is_empty(self):
        article_id = "8c072e31ebfdf21b319ad9bb5c27195fa0d5bb99"
        article_url = "https://mp.weixin.qq.com/s/15AfclsKv0RAg-LTRpctGA"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out_dir = root / "output" / "article_analysis"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{article_id}.md").write_text("\n", encoding="utf-8")
            (out_dir / f"{article_id}.json").write_text(
                '{"article_id":"8c072e31ebfdf21b319ad9bb5c27195fa0d5bb99","title":"给一个选择，飞三亚，还是去青岛？","url":"https://mp.weixin.qq.com/s/15AfclsKv0RAg-LTRpctGA","published_at":"2026-06-16 12:20","account":"旅行雷达"}',
                encoding="utf-8",
            )

            fetch_calls = []
            attach_calls = []
            original_fetch = wechat_crawler.fetch_article_markdown
            original_attach = wechat_crawler._attach_single_article_analysis
            try:
                def fake_fetch(article, headers, account_name=None):
                    fetch_calls.append(
                        {
                            "article": dict(article or {}),
                            "account_name": account_name,
                        }
                    )
                    return {
                        "article_id": article_id,
                        "title": "给一个选择，飞三亚，还是去青岛？",
                        "url": article_url,
                        "date": "2026-06-16",
                        "published_at": "2026-06-16 12:20",
                        "account": "旅行雷达",
                        "markdown": "# 给一个选择，飞三亚，还是去青岛？\n\n正文内容\n",
                    }

                def fake_attach(config, fetched, refresh_index=True, force_reanalyze=False):
                    attach_calls.append(dict(fetched or {}))
                    return {
                        "status": "ok",
                        "article_id": article_id,
                        "account": "旅行雷达",
                        "title": "给一个选择，飞三亚，还是去青岛？",
                        "url": article_url,
                        "published_at": "2026-06-16 12:20",
                        "date": "2026-06-16",
                        "summary": "测试摘要",
                        "topic": "",
                        "core_points": [],
                        "audience": "",
                        "risks": [],
                        "source": "yuanbao",
                    }

                wechat_crawler.fetch_article_markdown = fake_fetch
                wechat_crawler._attach_single_article_analysis = fake_attach

                payload = wechat_crawler.run_reanalyze_from_url(
                    article_url,
                    account_name="旅行雷达",
                    article_id=article_id,
                    provider="yuanbao",
                    save_markdown=False,
                    push=False,
                    config={"analysis_output_dir": str(root / "output")},
                )
            finally:
                wechat_crawler.fetch_article_markdown = original_fetch
                wechat_crawler._attach_single_article_analysis = original_attach

            self.assertEqual(len(fetch_calls), 1)
            self.assertEqual(fetch_calls[0]["account_name"], "旅行雷达")
            self.assertEqual(len(attach_calls), 1)
            self.assertIn("正文内容", attach_calls[0]["markdown"])
            self.assertEqual(payload["analysis"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()

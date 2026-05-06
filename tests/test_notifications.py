import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zj_agent.config import Settings
from zj_agent.notifications import (
    build_weekly_summary_message,
    build_weekly_summary_post,
    notify_clue_pool,
    select_rows_for_notification,
)


class NotificationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            mysql_dsn="mysql+pymysql://demo:demo@localhost:3306/demo",
            timezone="Asia/Shanghai",
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/demo",
            feishu_sign_secret="",
            llm_provider="openai",
            llm_api_key="demo-key",
            llm_base_url="https://example.com/v1",
            llm_model="demo-model",
            llm_timeout_seconds=60,
            anthropic_version="2023-06-01",
            llm_max_tokens=200,
            llm_content_char_limit=2500,
            llm_budget_cny=20.0,
            llm_input_price_per_mtoken_cny=8.807,
            llm_output_price_per_mtoken_cny=44.035,
            http_timeout_seconds=10,
            max_listing_pages=5,
            max_links_per_source=20,
            bootstrap_lookback_days=730,
            weekly_lookback_days=7,
            near_duplicate_threshold=0.92,
            workspace_root=Path("E:/workplace/codex/scraper"),
        )

    def test_build_weekly_summary_message_contains_chinese_fields(self) -> None:
        message = build_weekly_summary_message(
            [
                {
                    "source_name": "浙江大学",
                    "source_key": "zju_itt",
                    "title": "面向视频理解的无损推理加速技术实现产业应用",
                    "page_url": "https://example.com/detail.htm",
                    "clue_type": "transfer",
                    "investment_relevance": "high",
                    "relevance_score": 88,
                    "core_technology": "视频理解大模型无损推理加速技术",
                    "summary": "面向产业视频场景，兼顾精度与算力成本。",
                    "published_at": datetime(2026, 5, 1),
                }
            ]
        )
        self.assertIn("单位：浙江大学", message)
        self.assertIn("发布时间：2026-05-01", message)
        self.assertIn("类型：成果转化", message)
        self.assertIn("链接：查看原文", message)

    def test_build_weekly_summary_post_uses_short_link_text(self) -> None:
        title, lines = build_weekly_summary_post(
            [
                {
                    "source_name": "浙江大学",
                    "source_key": "zju_itt",
                    "title": "面向视频理解的无损推理加速技术实现产业应用",
                    "page_url": "https://example.com/detail.htm",
                    "clue_type": "transfer",
                    "investment_relevance": "high",
                    "relevance_score": 88,
                    "core_technology": "视频理解大模型无损推理加速技术",
                    "summary": "面向产业视频场景，兼顾精度与算力成本。",
                    "published_at": datetime(2026, 5, 1),
                }
            ]
        )
        self.assertEqual("本周科技成果线索汇总", title)
        self.assertTrue(
            any(
                tag.get("tag") == "a" and tag.get("text") == "查看原文"
                for row in lines
                for tag in row
            )
        )

    def test_select_rows_for_notification_prefers_diverse_site_sources(self) -> None:
        now = datetime.now()
        rows = [
            {
                "id": 1,
                "source_key": "zju_itt",
                "source_name": "浙江大学",
                "title": "A1",
                "page_url": "https://example.com/1",
                "clue_type": "transfer",
                "core_technology": "t1",
                "summary": "s1",
                "published_at": now - timedelta(days=1),
                "relevance_score": 90,
            },
            {
                "id": 2,
                "source_key": "zju_itt",
                "source_name": "浙江大学",
                "title": "A2",
                "page_url": "https://example.com/2",
                "clue_type": "transfer",
                "core_technology": "t2",
                "summary": "s2",
                "published_at": now - timedelta(days=2),
                "relevance_score": 89,
            },
            {
                "id": 3,
                "source_key": "zjut_news",
                "source_name": "浙江工业大学",
                "title": "B1",
                "page_url": "https://example.com/3",
                "clue_type": "achievement",
                "core_technology": "t3",
                "summary": "s3",
                "published_at": now - timedelta(days=3),
                "relevance_score": 80,
            },
            {
                "id": 4,
                "source_key": "tmslab_kycg",
                "source_name": "天目山实验室",
                "title": "C1",
                "page_url": "https://example.com/4",
                "clue_type": "achievement",
                "core_technology": "t4",
                "summary": "s4",
                "published_at": now - timedelta(days=4),
                "relevance_score": 81,
            },
        ]
        selected = select_rows_for_notification(rows, limit=3)
        self.assertEqual(3, len(selected))
        self.assertEqual(3, len({row["source_name"] for row in selected}))

    def test_select_rows_for_notification_caps_search_results(self) -> None:
        now = datetime.now()
        rows = []
        for index in range(1, 9):
            rows.append(
                {
                    "id": index,
                    "source_key": f"site_{index}",
                    "source_name": f"单位{index}",
                    "title": f"站点线索{index}",
                    "page_url": f"https://example.com/site/{index}",
                    "clue_type": "achievement",
                    "core_technology": "站点技术",
                    "summary": "站点摘要",
                    "published_at": now - timedelta(days=index),
                    "relevance_score": 80 + index,
                }
            )
        for index in range(9, 15):
            rows.append(
                {
                    "id": index,
                    "source_key": "zj_search_recent",
                    "source_name": "浙江省搜索补充发现",
                    "title": f"搜索线索{index}",
                    "page_url": f"https://example.com/search/{index}",
                    "clue_type": "transfer",
                    "core_technology": "搜索技术",
                    "summary": "搜索摘要",
                    "published_at": now - timedelta(days=1),
                    "relevance_score": 95,
                }
            )
        selected = select_rows_for_notification(rows, limit=10)
        search_count = sum(1 for row in selected if row["source_key"] == "zj_search_recent")
        self.assertLessEqual(search_count, 3)
        self.assertGreaterEqual(len(selected) - search_count, 7)

    @patch("zj_agent.notifications.mark_clue_pool_pushed")
    @patch("zj_agent.notifications.fetch_clue_pool")
    @patch("zj_agent.notifications.FeishuNotifier.send_post")
    def test_notify_clue_pool_sends_single_chinese_summary(self, mock_send, mock_fetch, mock_mark) -> None:
        now = datetime.now()
        mock_fetch.return_value = [
            {
                "id": 1,
                "source_key": "zju_itt",
                "source_name": "浙江大学",
                "title": "面向视频理解的无损推理加速技术实现产业应用",
                "page_url": "https://example.com/detail.htm",
                "clue_type": "transfer",
                "investment_relevance": "high",
                "relevance_score": 88,
                "core_technology": "视频理解大模型无损推理加速技术",
                "summary": "面向产业视频场景，兼顾精度与算力成本。",
                "published_at": now - timedelta(days=1),
            },
            {
                "id": 2,
                "source_key": "zjut_news",
                "source_name": "浙江工业大学",
                "title": "面向固态电池的关键材料与工艺取得突破",
                "page_url": "https://example.com/detail2.htm",
                "clue_type": "achievement",
                "investment_relevance": "medium",
                "relevance_score": 76,
                "core_technology": "固态电池关键材料与制备工艺",
                "summary": "兼具产业化前景和性能提升空间。",
                "published_at": now - timedelta(days=3),
            },
            {
                "id": 3,
                "source_key": "zj_search_recent",
                "source_name": "浙江省搜索补充发现",
                "title": "浙江实验室概念验证项目进入产业化对接阶段",
                "page_url": "https://example.com/detail3.htm",
                "clue_type": "transfer",
                "investment_relevance": "high",
                "relevance_score": 90,
                "core_technology": "概念验证与产业化对接机制",
                "summary": "已形成面向企业的转化对接能力。",
                "published_at": now - timedelta(days=2),
            },
        ]
        stats = notify_clue_pool(engine=None, settings=self.settings, source_key=None, limit=2)
        self.assertEqual(3, stats["scanned_docs"])
        self.assertEqual(2, stats["sent_docs"])
        self.assertEqual(1, stats["sent_messages"])
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual("本周科技成果线索汇总", kwargs["title"])
        self.assertTrue(
            any(any(tag.get("text") == "查看原文" for tag in row) for row in kwargs["lines"])
        )
        mock_mark.assert_called_once_with(None, [1, 3])


if __name__ == "__main__":
    unittest.main()

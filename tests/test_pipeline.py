import unittest
from unittest.mock import patch

from zj_agent.config import Settings
from zj_agent.pipeline import crawl_source


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            mysql_dsn="mysql+pymysql://demo:demo@localhost:3306/demo",
            timezone="Asia/Shanghai",
            feishu_webhook_url="",
            feishu_sign_secret="",
            llm_provider="openai",
            llm_api_key="",
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
            workspace_root=__import__("pathlib").Path("E:/workplace/codex/scraper"),
        )
        self.source_row = {
            "id": 1,
            "source_key": "zju_itt",
            "name": "浙江大学成果页",
            "mode": "html",
            "bootstrap_depth": "full",
            "weekly_depth": "first_page",
            "allowed_attachments": ["pdf", "docx"],
            "enable_ai_link_classifier": False,
            "enable_ai_content_judge": False,
            "stop_when_older_than_days": 730,
            "entry_urls": ["https://example.com/list.htm"],
        }

    @patch("zj_agent.pipeline.mark_source_crawl_progress")
    @patch("zj_agent.pipeline.discover_links")
    @patch("zj_agent.pipeline.candidate_exists")
    def test_duplicate_url_increments_stat(self, mock_exists, mock_discover, _mock_progress) -> None:
        from zj_agent.crawler.models import DiscoveredLink

        mock_discover.return_value = [
            DiscoveredLink(
                entry_url="https://example.com/list.htm",
                source_page_url="https://example.com/list.htm",
                page_url="https://example.com/detail.htm",
                anchor_text="成果发布",
            )
        ]
        mock_exists.return_value = {"id": 10, "status": "kept", "canonical_doc_id": 10}
        stats = crawl_source(engine=None, source_row=self.source_row, settings=self.settings, weekly_mode=False)
        self.assertEqual(stats["url_duplicates"], 1)
        self.assertEqual(stats["kept_docs"], 0)

    @patch("zj_agent.pipeline.mark_source_crawl_progress")
    @patch("zj_agent.pipeline.record_failure")
    @patch("zj_agent.pipeline.update_candidate_status")
    @patch("zj_agent.pipeline.upsert_candidate_discovered", return_value=99)
    @patch("zj_agent.pipeline.fetch_document_by_page_url", return_value=None)
    @patch("zj_agent.pipeline.candidate_exists", return_value=None)
    @patch("zj_agent.pipeline.discover_links")
    @patch("zj_agent.pipeline.fetch_document", side_effect=TimeoutError("timeout"))
    def test_fetch_failure_recorded(
        self,
        _mock_fetch,
        mock_discover,
        _mock_candidate_exists,
        _mock_fetch_by_page,
        _mock_upsert,
        mock_update_status,
        mock_record_failure,
        _mock_progress,
    ) -> None:
        from zj_agent.crawler.models import DiscoveredLink

        mock_discover.return_value = [
            DiscoveredLink(
                entry_url="https://example.com/list.htm",
                source_page_url="https://example.com/list.htm",
                page_url="https://example.com/detail.htm",
                anchor_text="成果发布",
            )
        ]
        stats = crawl_source(engine=None, source_row=self.source_row, settings=self.settings, weekly_mode=False)
        self.assertEqual(stats["failed_docs"], 1)
        mock_record_failure.assert_called_once()
        mock_update_status.assert_called()


if __name__ == "__main__":
    unittest.main()

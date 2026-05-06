import unittest
from pathlib import Path
from unittest.mock import patch

from zj_agent.ai.clue_extractor import ClueExtraction
from zj_agent.config import Settings
from zj_agent.extraction import extract_clues


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            mysql_dsn="mysql+pymysql://demo:demo@localhost:3306/demo",
            timezone="Asia/Shanghai",
            feishu_webhook_url="",
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

    @patch("zj_agent.extraction.upsert_extracted_clue")
    @patch("zj_agent.extraction.fetch_documents_for_extraction")
    def test_noise_title_is_dropped_without_ai(self, mock_fetch_docs, mock_upsert) -> None:
        mock_fetch_docs.return_value = [
            {
                "id": 1,
                "source_id": 10,
                "source_name": "测试来源",
                "title": "百家讲坛预告",
                "content": "这是讲座预告。",
            }
        ]
        stats = extract_clues(engine=None, settings=self.settings, source_key=None, limit=10, offset=0)
        self.assertEqual(1, stats["dropped_docs"])
        self.assertEqual(0, stats["ai_used_docs"])
        mock_upsert.assert_called_once()
        self.assertEqual("dropped", mock_upsert.call_args.kwargs["status"])
        self.assertFalse(mock_upsert.call_args.kwargs["used_ai"])
        self.assertFalse(mock_fetch_docs.call_args.kwargs["only_failed"])

    @patch("zj_agent.extraction.upsert_extracted_clue")
    @patch("zj_agent.extraction.fetch_documents_for_extraction")
    @patch("zj_agent.extraction.ClueExtractor.extract")
    def test_ai_keep_is_persisted(self, mock_extract, mock_fetch_docs, mock_upsert) -> None:
        mock_fetch_docs.return_value = [
            {
                "id": 2,
                "source_id": 10,
                "source_name": "测试来源",
                "title": "重大科技成果转化项目完成中试验证",
                "content": "介绍关键技术、应用场景和产业化进展。",
            }
        ]
        mock_extract.return_value = ClueExtraction(
            keep=True,
            clue_type="transfer",
            investment_relevance="high",
            relevance_score=86,
            core_technology="高性能复合材料制备技术",
            application_scenarios=["新能源装备"],
            transformation_signals=["中试", "产业化"],
            summary="项目已完成中试验证，具备产业化潜力。",
            reason_tags=["成果转化", "关键技术"],
            decision_reason="技术对象和转化信号明确。",
            used_ai=True,
        )
        stats = extract_clues(engine=None, settings=self.settings, source_key=None, limit=10, offset=0)
        self.assertEqual(1, stats["kept_docs"])
        self.assertEqual(1, stats["ai_used_docs"])
        self.assertEqual("kept", mock_upsert.call_args.kwargs["status"])
        self.assertTrue(mock_upsert.call_args.kwargs["used_ai"])

    @patch("zj_agent.extraction.upsert_extracted_clue")
    @patch("zj_agent.extraction.fetch_documents_for_extraction")
    @patch("zj_agent.extraction.ClueExtractor.extract", return_value=None)
    def test_ai_failure_is_marked_failed(self, _mock_extract, mock_fetch_docs, mock_upsert) -> None:
        mock_fetch_docs.return_value = [
            {
                "id": 3,
                "source_id": 10,
                "source_name": "测试来源",
                "title": "重大科研进展",
                "content": "介绍技术细节。",
            }
        ]
        stats = extract_clues(engine=None, settings=self.settings, source_key=None, limit=10, offset=0)
        self.assertEqual(1, stats["failed_docs"])
        self.assertEqual("failed", mock_upsert.call_args.kwargs["status"])

    @patch("zj_agent.extraction.upsert_extracted_clue")
    @patch("zj_agent.extraction.fetch_documents_for_extraction")
    def test_retry_failed_only_queries_failed_rows(self, mock_fetch_docs, _mock_upsert) -> None:
        mock_fetch_docs.return_value = []
        extract_clues(
            engine=None,
            settings=self.settings,
            source_key="zju_itt",
            limit=5,
            offset=0,
            retry_failed=True,
        )
        self.assertTrue(mock_fetch_docs.call_args.kwargs["include_processed"])
        self.assertTrue(mock_fetch_docs.call_args.kwargs["only_failed"])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from zj_agent.pooling import sync_clue_pool


class PoolingTests(unittest.TestCase):
    @patch("zj_agent.pooling.upsert_clue_pool")
    @patch("zj_agent.pooling.fetch_extracted_for_pool")
    def test_sync_clue_pool_inserts_eligible_rows(self, mock_fetch, mock_upsert) -> None:
        mock_fetch.return_value = [
            {
                "id": 101,
                "raw_document_id": 201,
                "source_id": 301,
                "title": "New robot system enters pilot manufacturing",
                "page_url": "https://example.com/detail.htm",
                "clue_type": "transfer",
                "investment_relevance": "high",
                "relevance_score": 88,
                "core_technology": "Heavy-load robot control stack",
                "summary": "Pilot manufacturing progress for the new robot system.",
                "published_at": None,
            }
        ]

        stats = sync_clue_pool(engine=None, source_key="zju_itt", limit=10, offset=0)

        self.assertEqual(1, stats["scanned_docs"])
        self.assertEqual(1, stats["inserted_docs"])
        self.assertEqual(0, stats["skipped_docs"])
        mock_upsert.assert_called_once()
        self.assertEqual(101, mock_upsert.call_args.kwargs["extracted_clue_id"])
        self.assertEqual("transfer", mock_upsert.call_args.kwargs["clue_type"])

    @patch("zj_agent.pooling.clear_clue_pool")
    @patch("zj_agent.pooling.fetch_extracted_for_pool")
    def test_sync_clue_pool_can_clear_existing(self, mock_fetch, mock_clear) -> None:
        mock_fetch.return_value = []

        stats = sync_clue_pool(
            engine=None,
            source_key="zju_itt",
            limit=10,
            offset=0,
            clear_existing=True,
        )

        self.assertEqual(0, stats["inserted_docs"])
        self.assertEqual(0, stats["skipped_docs"])
        mock_clear.assert_called_once_with(None, source_key="zju_itt")

    @patch("zj_agent.pooling.upsert_clue_pool")
    @patch("zj_agent.pooling.fetch_extracted_for_pool")
    def test_sync_clue_pool_skips_meeting_style_titles(self, mock_fetch, mock_upsert) -> None:
        mock_fetch.return_value = [
            {
                "id": 102,
                "raw_document_id": 202,
                "source_id": 302,
                "title": "省重点研发计划项目启动会顺利召开",
                "page_url": "https://example.com/detail2.htm",
                "clue_type": "achievement",
                "investment_relevance": "high",
                "relevance_score": 85,
                "core_technology": "Aerospace sensing technology",
                "summary": "Kickoff meeting content only.",
                "published_at": None,
            }
        ]
        stats = sync_clue_pool(engine=None, source_key="hias_kydt", limit=10, offset=0)
        self.assertEqual(1, stats["scanned_docs"])
        self.assertEqual(0, stats["inserted_docs"])
        self.assertEqual(1, stats["skipped_docs"])
        mock_upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()

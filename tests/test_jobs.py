import unittest
from pathlib import Path
from unittest.mock import patch

from zj_agent.config import Settings
from zj_agent.jobs import run_job_recorded, run_weekly_job


class JobsTests(unittest.TestCase):
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
            weekly_notify_limit=5,
            extract_batch_size=2,
            pool_batch_size=2,
        )

    @patch("zj_agent.jobs.notify_clue_pool")
    @patch("zj_agent.jobs.sync_clue_pool")
    @patch("zj_agent.jobs.extract_clues")
    @patch("zj_agent.jobs.crawl_source")
    @patch("zj_agent.jobs.fetch_sources")
    def test_run_weekly_job_executes_whole_pipeline(
        self,
        mock_fetch_sources,
        mock_crawl,
        mock_extract,
        mock_pool,
        mock_notify,
    ) -> None:
        mock_fetch_sources.return_value = [{"id": 1, "source_key": "zju_itt"}]
        mock_crawl.return_value = {"kept_docs": 2}
        mock_extract.side_effect = [
            {"scanned_docs": 2, "kept_docs": 1, "dropped_docs": 1, "failed_docs": 0, "ai_used_docs": 2},
            {"scanned_docs": 0, "kept_docs": 0, "dropped_docs": 0, "failed_docs": 0, "ai_used_docs": 0},
            {"scanned_docs": 1, "kept_docs": 1, "dropped_docs": 0, "failed_docs": 0, "ai_used_docs": 1},
        ]
        mock_pool.side_effect = [
            {"scanned_docs": 2, "inserted_docs": 2, "skipped_docs": 0},
            {"scanned_docs": 0, "inserted_docs": 0, "skipped_docs": 0},
        ]
        mock_notify.return_value = {"scanned_docs": 2, "sent_docs": 2, "sent_messages": 1}

        summary = run_weekly_job(engine=None, settings=self.settings)

        self.assertIn("zju_itt", summary["crawl"])
        self.assertEqual(2, summary["extract"]["scanned_docs"])
        self.assertEqual(1, summary["retry_failed_extract"]["scanned_docs"])
        self.assertEqual(2, summary["pool_sync"]["inserted_docs"])
        self.assertEqual(2, summary["notify"]["sent_docs"])

    @patch("zj_agent.jobs.finish_job_run")
    @patch("zj_agent.jobs.start_job_run")
    def test_run_job_recorded_marks_success(self, mock_start, mock_finish) -> None:
        mock_start.return_value = 42
        summary = run_job_recorded(
            engine=None,
            job_type="weekly-job",
            trigger_mode="manual",
            source_key=None,
            runner=lambda: {"ok": True},
        )
        self.assertEqual({"ok": True}, summary)
        mock_finish.assert_called_once_with(
            None,
            42,
            status="succeeded",
            summary={"ok": True},
            error_message=None,
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from zj_agent.crawler.fetcher import fetch_document, normalize_datetime
from zj_agent.crawler.source_registry import SourceConfig


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SourceConfig(
            key="demo",
            name="demo",
            entry_urls=["https://example.com/list.htm"],
            mode="html",
            bootstrap_depth="full",
            weekly_depth="first_page",
            allowed_attachments=[],
            enable_ai_link_classifier=False,
            enable_ai_content_judge=False,
            stop_when_older_than_days=730,
            http_timeout_seconds=20,
        )

    @patch("zj_agent.crawler.fetcher.fetch_attachments", return_value=[])
    @patch("zj_agent.crawler.fetcher.trafilatura.extract", return_value="这是正文")
    @patch("zj_agent.crawler.fetcher._get")
    def test_fetch_document_extracts_title_and_content(self, mock_get, _mock_extract, _mock_attachments) -> None:
        mock_get.return_value = _FakeResponse(
            "<html><head><title>成果发布</title></head><body><div>正文</div></body></html>"
        )
        document = fetch_document("https://example.com/detail.htm", self.source, timeout_seconds=10)
        self.assertEqual(document.title, "成果发布")
        self.assertIn("这是正文", document.content)

    @patch("zj_agent.crawler.fetcher.fetch_attachments", return_value=[])
    @patch("zj_agent.crawler.fetcher.trafilatura.extract", return_value="")
    @patch("zj_agent.crawler.fetcher._get")
    def test_fetch_document_rejects_login_page(self, mock_get, _mock_extract, _mock_attachments) -> None:
        mock_get.return_value = _FakeResponse(
            "<html><head><title>用户登录</title></head><body>请输入用户名和密码</body></html>"
        )
        with self.assertRaises(ValueError):
            fetch_document("https://example.com/manage/login.html", self.source, timeout_seconds=10)

    def test_normalize_datetime_strips_timezone(self) -> None:
        aware = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
        normalized = normalize_datetime(aware)
        self.assertIsNone(normalized.tzinfo)
        self.assertEqual(normalized, aware.astimezone().replace(tzinfo=None))

    @patch("zj_agent.crawler.fetcher.dateparser.parse")
    def test_extract_published_at_returns_naive_datetime(self, mock_parse) -> None:
        from zj_agent.crawler.fetcher import _extract_published_at

        mock_parse.return_value = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
        published_at = _extract_published_at(
            __import__("bs4").BeautifulSoup(
                "<html><meta property='article:published_time' content='2026-05-20T12:00:00+00:00'></html>",
                "lxml",
            )
        )
        self.assertIsNotNone(published_at)
        assert published_at is not None
        self.assertIsNone(published_at.tzinfo)


if __name__ == "__main__":
    unittest.main()

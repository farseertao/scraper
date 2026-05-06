import unittest
from pathlib import Path

from zj_agent.crawler.source_registry import load_sources


class SourceRegistryTests(unittest.TestCase):
    def test_load_sources_with_search_queries(self) -> None:
        payload = """sources:
  - key: zj_search_recent
    name: 浙江省搜索补充发现
    entry_urls:
      - https://api.bochaai.com/v1/web-search
    mode: html
    bootstrap_depth: full
    weekly_depth: first_page
    allowed_attachments: [pdf, docx]
    enable_ai_link_classifier: false
    enable_ai_content_judge: true
    stop_when_older_than_days: 30
    search_freshness: oneWeek
    search_count: 12
    search_queries:
      - 浙江 高校 科技成果 转化
"""
        path = Path("E:/workplace/codex/scraper/tests/_tmp_sources.yaml")
        try:
            path.write_text(payload, encoding="utf-8")
            rows = load_sources(path)
        finally:
            if path.exists():
                path.unlink()
        self.assertEqual(1, len(rows))
        self.assertEqual(["浙江 高校 科技成果 转化"], rows[0].search_queries)
        self.assertEqual("oneWeek", rows[0].search_freshness)
        self.assertEqual(12, rows[0].search_count)


if __name__ == "__main__":
    unittest.main()

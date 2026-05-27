import unittest
from unittest.mock import Mock, patch

from requests.exceptions import HTTPError

from zj_agent.search import BochaSearchClient


class SearchTests(unittest.TestCase):
    @patch("zj_agent.search.requests.Session.post")
    def test_bocha_search_parses_nested_results(self, mock_post) -> None:
        response = Mock()
        response.json.return_value = {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "浙江大学科技成果转化项目",
                            "url": "https://example.com/a",
                            "snippet": "项目简介A",
                        },
                        {
                            "title": "天目山实验室成果",
                            "link": "https://example.com/b",
                            "summary": "项目简介B",
                        },
                    ]
                }
            }
        }
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        client = BochaSearchClient(
            api_key="demo",
            endpoint="https://api.bochaai.com/v1/web-search",
            timeout_seconds=10,
        )
        rows = client.search(query="浙江 科技成果 转化", freshness="oneWeek", count=5)
        self.assertEqual(2, len(rows))
        self.assertEqual("浙江大学科技成果转化项目", rows[0].title)
        self.assertEqual("https://example.com/b", rows[1].url)

    @patch("zj_agent.search.time.sleep")
    @patch("zj_agent.search.requests.Session.post")
    def test_bocha_search_retries_on_429(self, mock_post, mock_sleep) -> None:
        throttled = Mock()
        throttled.status_code = 429
        throttled.raise_for_status.side_effect = HTTPError("429")

        ok = Mock()
        ok.status_code = 200
        ok.raise_for_status.return_value = None
        ok.json.return_value = {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "title": "浙江成果",
                            "url": "https://example.com/a",
                            "snippet": "项目简介",
                        }
                    ]
                }
            }
        }
        mock_post.side_effect = [throttled, ok]

        client = BochaSearchClient(
            api_key="demo",
            endpoint="https://api.bochaai.com/v1/web-search",
            timeout_seconds=10,
            max_retries=3,
            retry_backoff_seconds=1.0,
        )
        rows = client.search(query="浙江 科技成果 转化", freshness="oneWeek", count=5)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, mock_post.call_count)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()

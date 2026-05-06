import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()

import unittest

from zj_agent.crawler.discovery import _is_article_url, _is_pagination_url


class DiscoveryTests(unittest.TestCase):
    def test_detects_webplus_article(self) -> None:
        self.assertTrue(
            _is_article_url(
                "http://www.itt.zju.edu.cn/2026/0408/c70618a3149127/page.htm",
                "www.itt.zju.edu.cn",
                "成果推介 | 项目",
            )
        )

    def test_detects_info_article(self) -> None:
        self.assertTrue(
            _is_article_url(
                "https://www.zstu.edu.cn/info/1052/1084.htm",
                "www.zstu.edu.cn",
                "科教动态",
            )
        )

    def test_detects_date_style_anchor_article(self) -> None:
        self.assertTrue(
            _is_article_url(
                "https://www.zstu.edu.cn/info/1001/99999.htm",
                "www.zstu.edu.cn",
                "072026-01 浙江理工大学科技园入选首批浙江省大学科技园",
            )
        )

    def test_detects_pagination(self) -> None:
        self.assertTrue(
            _is_pagination_url(
                "http://www.news.zjut.edu.cn/5422/list2.htm",
                "http://www.news.zjut.edu.cn/5422/list.htm",
            )
        )
        self.assertFalse(
            _is_pagination_url(
                "http://www.news.zjut.edu.cn/2026/0408/c5422a1/page.htm",
                "http://www.news.zjut.edu.cn/5422/list.htm",
            )
        )


if __name__ == "__main__":
    unittest.main()

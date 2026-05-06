import unittest

from zj_agent.crawler.deduper import compute_content_hash, is_near_duplicate, title_similarity


class DeduperTests(unittest.TestCase):
    def test_content_hash_is_whitespace_insensitive(self) -> None:
        left = compute_content_hash("科研成果", "同一 内容\n第二行")
        right = compute_content_hash("科研成果 ", "同一   内容 第二行")
        self.assertEqual(left, right)

    def test_title_similarity_detects_close_titles(self) -> None:
        score = title_similarity("某团队实现关键技术突破", "某团队实现关键技术重大突破")
        self.assertGreater(score, 0.8)

    def test_near_duplicate_threshold(self) -> None:
        self.assertTrue(is_near_duplicate("成果转化项目公示", "成果转化项目公示（更新）", 0.7))
        self.assertFalse(is_near_duplicate("成果转化项目公示", "科研工作会议通知", 0.7))


if __name__ == "__main__":
    unittest.main()

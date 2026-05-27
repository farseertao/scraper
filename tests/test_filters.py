import unittest

from zj_agent.crawler.filters import (
    evaluate_retention,
    evaluate_search_source_retention,
    is_blocked_url,
    is_login_page,
    is_noise_title,
    is_poolworthy_title,
)


class FilterTests(unittest.TestCase):
    def test_noise_title(self) -> None:
        self.assertTrue(is_noise_title("百家讲坛预告"))
        self.assertFalse(is_noise_title("重大科技成果转化项目落地"))

    def test_poolworthy_title(self) -> None:
        self.assertFalse(is_poolworthy_title("杭高院首个浙江省重点研发计划项目启动会顺利召开"))
        self.assertFalse(is_poolworthy_title("引力波时代基础物理的机遇与挑战研讨会"))
        self.assertTrue(is_poolworthy_title("新一代无人重载机器人系统"))

    def test_blocked_url(self) -> None:
        self.assertTrue(is_blocked_url("https://foo.example.com/manage/login/login.html"))
        self.assertTrue(is_blocked_url("http://foo.example.com/kydt/page.htm | broken"))
        self.assertFalse(is_blocked_url("https://foo.example.com/2026/04/page.htm"))

    def test_login_page_detection(self) -> None:
        self.assertTrue(is_login_page("统一身份认证", "请输入账号密码后访问系统"))
        self.assertFalse(is_login_page("重大科技成果发布", "介绍核心技术、应用场景和转化方向"))
        sidebar = "后台管理 " + ("浙江工商大学科研动态介绍项目转化进展。" * 20)
        self.assertFalse(is_login_page("科研成果转化签约", sidebar))

    def test_rule_keep(self) -> None:
        decision = evaluate_retention(
            title="重大科技成果转化项目完成专利许可签约",
            content="项目围绕关键技术完成中试验证，形成样机并推进产业化。",
            attachment_names=[],
            content_judge=None,
        )
        self.assertTrue(decision.keep)
        self.assertIn("成果转化", "".join(decision.reason_tags))

    def test_login_page_dropped(self) -> None:
        decision = evaluate_retention(
            title="管理员登录",
            content="请输入用户名密码后进入后台管理系统",
            attachment_names=[],
            content_judge=None,
        )
        self.assertFalse(decision.keep)
        self.assertIn("login_page", decision.reason_tags)

    def test_noise_content_dropped(self) -> None:
        decision = evaluate_retention(
            title="浙江工商大学召开科研工作会议暨服务创新浙江启动会",
            content="本次工作会议围绕年度安排、制度建设和安全教育进行部署。",
            attachment_names=[],
            content_judge=None,
        )
        self.assertFalse(decision.keep)
        self.assertEqual("noise", decision.doc_type)

    def test_generic_honor_without_technical_object_dropped(self) -> None:
        decision = evaluate_retention(
            title="某研究员荣获国际奖项",
            content="文章主要介绍获奖经历和学术影响，没有明确技术对象和转化信号。",
            attachment_names=[],
            content_judge=None,
        )
        self.assertFalse(decision.keep)

    def test_search_source_requires_conversion_signal(self) -> None:
        decision = evaluate_search_source_retention(
            title="浙江知识产权强省建设迈上新台阶",
            content="全省知识产权工作取得新进展，围绕制度建设、工作推进和整体布局进行总结。",
            page_url="https://example.com/news",
        )
        self.assertIsNotNone(decision)
        self.assertFalse(decision.keep)

    def test_search_source_allows_project_public_notice(self) -> None:
        decision = evaluate_search_source_retention(
            title="浙江省科学技术厅关于面向新型电力系统的嵌入式AI赋能量测关键技术及应用项目上升为省重大科技计划项目的公示",
            content="该项目列入省重大科技计划项目公示名单，涉及关键技术应用与产业化预期。",
            page_url="https://example.com/notice",
        )
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()

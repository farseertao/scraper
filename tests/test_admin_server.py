import io
import unittest
from pathlib import Path

from zj_agent.admin_server import AdminApp
from zj_agent.config import Settings


class AdminServerTests(unittest.TestCase):
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
            admin_username="admin",
            admin_password="secret",
            app_secret_key="signing-secret",
        )

    def test_login_page_is_public(self) -> None:
        app = AdminApp(engine=None, settings=self.settings)
        status_holder = {}

        def start_response(status, headers):
            status_holder["status"] = status
            status_holder["headers"] = headers

        body = b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/login",
                    "QUERY_STRING": "",
                    "wsgi.input": io.BytesIO(b""),
                    "CONTENT_LENGTH": "0",
                },
                start_response,
            )
        ).decode("utf-8")
        self.assertEqual("200 OK", status_holder["status"])
        self.assertIn("zj-agent admin", body)

    def test_dashboard_redirects_without_session(self) -> None:
        app = AdminApp(engine=None, settings=self.settings)
        status_holder = {}

        def start_response(status, headers):
            status_holder["status"] = status
            status_holder["headers"] = headers

        b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/",
                    "QUERY_STRING": "",
                    "wsgi.input": io.BytesIO(b""),
                    "CONTENT_LENGTH": "0",
                },
                start_response,
            )
        )
        self.assertEqual("302 Found", status_holder["status"])
        self.assertIn(("Location", "/login"), status_holder["headers"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mysql_dsn: str
    timezone: str
    feishu_webhook_url: str
    feishu_sign_secret: str
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int
    anthropic_version: str
    llm_max_tokens: int
    llm_content_char_limit: int
    llm_budget_cny: float
    llm_input_price_per_mtoken_cny: float
    llm_output_price_per_mtoken_cny: float
    http_timeout_seconds: int
    max_listing_pages: int
    max_links_per_source: int
    bootstrap_lookback_days: int
    weekly_lookback_days: int
    near_duplicate_threshold: float
    workspace_root: Path
    admin_username: str = "admin"
    admin_password: str = ""
    app_secret_key: str = ""
    app_log_dir: str = ""
    weekly_notify_limit: int = 10
    notify_resend: bool = False
    extract_batch_size: int = 50
    pool_batch_size: int = 50
    admin_session_hours: int = 12
    bocha_api_key: str = ""
    bocha_search_endpoint: str = "https://api.bochaai.com/v1/web-search"
    bocha_request_delay_seconds: float = 2.0
    bocha_max_retries: int = 4
    bocha_retry_backoff_seconds: float = 2.0


def load_settings() -> Settings:
    load_dotenv()
    workspace = Path(__file__).resolve().parents[2]

    llm_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    llm_api_key = os.getenv("LLM_API_KEY", "").strip()
    llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_model = os.getenv("LLM_MODEL", "gpt-4.1-mini").strip()

    anthropic_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
    anthropic_base = os.getenv("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    anthropic_model = (
        os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "").strip()
        or os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "").strip()
        or os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "").strip()
    )

    if not llm_provider:
        llm_provider = "anthropic" if anthropic_token else "openai"
    if llm_provider == "anthropic":
        llm_api_key = llm_api_key or anthropic_token
        if anthropic_base:
            llm_base_url = anthropic_base
        if anthropic_model:
            llm_model = anthropic_model

    return Settings(
        mysql_dsn=os.getenv(
            "MYSQL_DSN",
            "mysql+pymysql://scraper:123456@localhost:3306/scraper?charset=utf8mb4",
        ),
        timezone=os.getenv("TIMEZONE", "Asia/Shanghai"),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
        feishu_sign_secret=os.getenv("FEISHU_SIGN_SECRET", "").strip(),
        llm_provider=llm_provider,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        anthropic_version=os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "280")),
        llm_content_char_limit=int(os.getenv("LLM_CONTENT_CHAR_LIMIT", "2500")),
        llm_budget_cny=float(os.getenv("LLM_BUDGET_CNY", "20")),
        llm_input_price_per_mtoken_cny=float(
            os.getenv("LLM_INPUT_PRICE_PER_MTOKEN_CNY", "8.807")
        ),
        llm_output_price_per_mtoken_cny=float(
            os.getenv("LLM_OUTPUT_PRICE_PER_MTOKEN_CNY", "44.035")
        ),
        http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "10")),
        max_listing_pages=int(os.getenv("MAX_LISTING_PAGES", "20")),
        max_links_per_source=int(os.getenv("MAX_LINKS_PER_SOURCE", "300")),
        bootstrap_lookback_days=int(os.getenv("BOOTSTRAP_LOOKBACK_DAYS", "730")),
        weekly_lookback_days=int(os.getenv("WEEKLY_LOOKBACK_DAYS", "7")),
        near_duplicate_threshold=float(os.getenv("NEAR_DUPLICATE_THRESHOLD", "0.92")),
        workspace_root=workspace,
        admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        app_secret_key=os.getenv("APP_SECRET_KEY", "").strip(),
        app_log_dir=os.getenv("APP_LOG_DIR", "").strip(),
        weekly_notify_limit=int(os.getenv("WEEKLY_NOTIFY_LIMIT", "10")),
        notify_resend=os.getenv("NOTIFY_RESEND", "").strip().lower() in {"1", "true", "yes", "on"},
        extract_batch_size=int(os.getenv("EXTRACT_BATCH_SIZE", "50")),
        pool_batch_size=int(os.getenv("POOL_BATCH_SIZE", "50")),
        admin_session_hours=int(os.getenv("ADMIN_SESSION_HOURS", "12")),
        bocha_api_key=os.getenv("BOCHA_API_KEY", "").strip(),
        bocha_search_endpoint=os.getenv(
            "BOCHA_SEARCH_ENDPOINT", "https://api.bochaai.com/v1/web-search"
        ).strip(),
        bocha_request_delay_seconds=float(os.getenv("BOCHA_REQUEST_DELAY_SECONDS", "2")),
        bocha_max_retries=int(os.getenv("BOCHA_MAX_RETRIES", "4")),
        bocha_retry_backoff_seconds=float(os.getenv("BOCHA_RETRY_BACKOFF_SECONDS", "2")),
    )

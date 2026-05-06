from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceConfig:
    key: str
    name: str
    entry_urls: list[str]
    mode: str
    bootstrap_depth: str
    weekly_depth: str
    allowed_attachments: list[str]
    enable_ai_link_classifier: bool
    enable_ai_content_judge: bool
    stop_when_older_than_days: int
    http_timeout_seconds: int | None = None
    search_queries: list[str] = field(default_factory=list)
    search_freshness: str | None = None
    search_count: int = 10


def load_sources(config_path: Path) -> list[SourceConfig]:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = []
    for item in payload.get("sources", []):
        sources.append(
            SourceConfig(
                key=item["key"],
                name=item["name"],
                entry_urls=list(item.get("entry_urls") or []),
                mode=item.get("mode", "html"),
                bootstrap_depth=item.get("bootstrap_depth", "full"),
                weekly_depth=item.get("weekly_depth", "first_page"),
                allowed_attachments=[str(x).lower() for x in item.get("allowed_attachments", [])],
                enable_ai_link_classifier=bool(item.get("enable_ai_link_classifier", False)),
                enable_ai_content_judge=bool(item.get("enable_ai_content_judge", False)),
                stop_when_older_than_days=int(item.get("stop_when_older_than_days", 730)),
                http_timeout_seconds=(
                    int(item["http_timeout_seconds"])
                    if item.get("http_timeout_seconds") is not None
                    else None
                ),
                search_queries=[str(x).strip() for x in item.get("search_queries", []) if str(x).strip()],
                search_freshness=(str(item["search_freshness"]).strip() if item.get("search_freshness") else None),
                search_count=int(item.get("search_count", 10)),
            )
        )
    return sources


def get_source_by_key(config_path: Path, source_key: str) -> SourceConfig | None:
    for source in load_sources(config_path):
        if source.key == source_key:
            return source
    return None

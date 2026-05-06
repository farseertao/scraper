from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DiscoveredLink:
    entry_url: str
    source_page_url: str
    page_url: str
    anchor_text: str


@dataclass(frozen=True)
class AttachmentContent:
    url: str
    file_name: str
    file_ext: str
    content_text: str
    content_hash: str


@dataclass(frozen=True)
class FetchedDocument:
    page_url: str
    title: str
    content: str
    published_at: datetime | None
    attachments: list[AttachmentContent] = field(default_factory=list)

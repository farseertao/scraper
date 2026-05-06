from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from .crawler.filters import is_poolworthy_title
from .db import clear_clue_pool, fetch_extracted_for_pool, upsert_clue_pool


@dataclass
class PoolStats:
    scanned_docs: int = 0
    inserted_docs: int = 0
    skipped_docs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned_docs": self.scanned_docs,
            "inserted_docs": self.inserted_docs,
            "skipped_docs": self.skipped_docs,
        }


def sync_clue_pool(
    engine: Engine,
    *,
    source_key: str | None,
    limit: int,
    offset: int,
    min_score: int = 70,
    clear_existing: bool = False,
) -> dict[str, int]:
    if clear_existing:
        clear_clue_pool(engine, source_key=source_key)

    rows = fetch_extracted_for_pool(
        engine,
        source_key=source_key,
        limit=limit,
        offset=offset,
        min_score=min_score,
        include_pooled=False,
    )
    stats = PoolStats()

    for row in rows:
        stats.scanned_docs += 1
        if not is_poolworthy_title(str(row["title"] or "")):
            stats.skipped_docs += 1
            continue
        upsert_clue_pool(
            engine,
            extracted_clue_id=int(row["id"]),
            raw_document_id=int(row["raw_document_id"]),
            source_id=int(row["source_id"]),
            title=str(row["title"] or ""),
            page_url=str(row["page_url"] or ""),
            clue_type=str(row["clue_type"] or "") or None,
            investment_relevance=str(row["investment_relevance"] or "") or None,
            relevance_score=int(row["relevance_score"]) if row["relevance_score"] is not None else None,
            core_technology=str(row["core_technology"] or ""),
            summary=str(row["summary"] or ""),
            published_at=row["published_at"],
        )
        stats.inserted_docs += 1

    return stats.to_dict()

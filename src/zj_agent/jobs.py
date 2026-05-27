from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from .config import Settings
from .db import finish_job_run, fetch_sources, start_job_run
from .crawler.source_registry import get_source_by_key
from .extraction import extract_clues
from .notifications import notify_clue_pool
from .pipeline import crawl_source, inspect_source, retry_failures
from .pooling import sync_clue_pool


@dataclass
class AggregateStats:
    scanned_docs: int = 0
    kept_docs: int = 0
    dropped_docs: int = 0
    failed_docs: int = 0
    ai_used_docs: int = 0
    inserted_docs: int = 0
    skipped_docs: int = 0
    sent_docs: int = 0
    sent_messages: int = 0

    def add(self, stats: dict[str, int]) -> None:
        for key, value in stats.items():
            if hasattr(self, key):
                setattr(self, key, getattr(self, key) + int(value))

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned_docs": self.scanned_docs,
            "kept_docs": self.kept_docs,
            "dropped_docs": self.dropped_docs,
            "failed_docs": self.failed_docs,
            "ai_used_docs": self.ai_used_docs,
            "inserted_docs": self.inserted_docs,
            "skipped_docs": self.skipped_docs,
            "sent_docs": self.sent_docs,
            "sent_messages": self.sent_messages,
        }


@dataclass
class WeeklyJobSummary:
    crawl: dict[str, dict[str, int]] = field(default_factory=dict)
    extract: dict[str, int] = field(default_factory=dict)
    retry_failed_extract: dict[str, int] = field(default_factory=dict)
    pool_sync: dict[str, int] = field(default_factory=dict)
    notify: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "crawl": self.crawl,
            "extract": self.extract,
            "retry_failed_extract": self.retry_failed_extract,
            "pool_sync": self.pool_sync,
            "notify": self.notify,
        }


def _drain_extract(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str | None,
) -> dict[str, int]:
    aggregate = AggregateStats()
    while True:
        stats = extract_clues(
            engine,
            settings,
            source_key=source_key,
            limit=settings.extract_batch_size,
            offset=0,
            clear_existing=False,
            retry_failed=False,
        )
        aggregate.add(stats)
        if stats["scanned_docs"] < settings.extract_batch_size:
            break
    return aggregate.to_dict()


def _retry_failed_extract_once(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str | None,
) -> dict[str, int]:
    return extract_clues(
        engine,
        settings,
        source_key=source_key,
        limit=settings.extract_batch_size,
        offset=0,
        clear_existing=False,
        retry_failed=True,
    )


def _drain_pool(
    engine: Engine,
    *,
    source_key: str | None,
    settings: Settings,
) -> dict[str, int]:
    aggregate = AggregateStats()
    while True:
        stats = sync_clue_pool(
            engine,
            source_key=source_key,
            limit=settings.pool_batch_size,
            offset=0,
            min_score=70,
            clear_existing=False,
        )
        aggregate.add(stats)
        if stats["scanned_docs"] < settings.pool_batch_size:
            break
    return aggregate.to_dict()


def run_weekly_job(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str | None = None,
) -> dict[str, Any]:
    sources = fetch_sources(engine, source_key=source_key)
    if not sources:
        raise RuntimeError("No active sources found.")

    summary = WeeklyJobSummary()
    for row in sources:
        summary.crawl[str(row["source_key"])] = crawl_source(
            engine,
            row,
            settings,
            weekly_mode=True,
        )
    summary.extract = _drain_extract(engine, settings, source_key=source_key)
    summary.retry_failed_extract = _retry_failed_extract_once(
        engine,
        settings,
        source_key=source_key,
    )
    summary.pool_sync = _drain_pool(engine, source_key=source_key, settings=settings)
    summary.notify = notify_clue_pool(
        engine,
        settings,
        source_key=source_key,
        limit=settings.weekly_notify_limit,
        resend=settings.notify_resend,
    )
    return summary.to_dict()


def run_source_operation(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str,
    operation: str,
) -> dict[str, Any]:
    rows = fetch_sources(engine, source_key=source_key, include_inactive=True)
    if not rows:
        raise RuntimeError(f"Unknown source: {source_key}")
    row = rows[0]
    if operation == "inspect":
        source_config = get_source_by_key(settings.workspace_root / "configs" / "sources.yaml", source_key)
        if source_config is None:
            raise RuntimeError(f"Source config not found: {source_key}")
        links = inspect_source(source_config, settings)
        return {"source_key": source_key, "operation": operation, "link_count": len(links), "links": links[:10]}
    if operation == "bootstrap":
        return crawl_source(engine, row, settings, weekly_mode=False)
    if operation == "weekly":
        return crawl_source(engine, row, settings, weekly_mode=True)
    if operation == "retry-failures":
        from .db import fetch_pending_failures

        failures = fetch_pending_failures(engine, source_id=int(row["id"]))
        return retry_failures(engine, row, settings, failures)
    raise RuntimeError(f"Unsupported source operation: {operation}")


def run_job_recorded(
    engine: Engine,
    *,
    job_type: str,
    trigger_mode: str,
    source_key: str | None,
    runner,
) -> dict[str, Any]:
    run_id = start_job_run(
        engine,
        job_type=job_type,
        source_key=source_key,
        trigger_mode=trigger_mode,
    )
    try:
        summary = runner()
    except Exception as exc:
        finish_job_run(
            engine,
            run_id,
            status="failed",
            summary=None,
            error_message=str(exc),
        )
        raise
    finish_job_run(
        engine,
        run_id,
        status="succeeded",
        summary=summary,
        error_message=None,
    )
    return summary

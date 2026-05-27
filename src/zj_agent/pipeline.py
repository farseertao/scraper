from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .ai import AiLinkClassifier, ContentJudge, LlmClient
from .config import Settings
from .crawler.deduper import compute_content_hash, is_near_duplicate
from .crawler.discovery import discover_links
from .crawler.fetcher import fetch_document, normalize_datetime
from .crawler.filters import (
    evaluate_retention,
    evaluate_search_source_retention,
    is_blocked_url,
    is_noise_title,
)
from .crawler.models import AttachmentContent, DiscoveredLink
from .crawler.source_registry import SourceConfig, get_source_by_key
from .search import BochaSearchClient
from .db import (
    candidate_exists,
    fetch_document_by_content_hash,
    fetch_document_by_page_url,
    find_near_duplicate_title,
    increment_failure_retry,
    insert_attachments,
    insert_raw_document,
    mark_source_crawl_progress,
    record_failure,
    resolve_failure,
    update_candidate_status,
    upsert_candidate_discovered,
)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

ZHEJIANG_RESULT_HINTS = [
    "浙江",
    "之江",
    "良渚",
    "西湖实验室",
    "甬江",
    "天目山",
    "国科大杭州高等研究院",
    "杭州电子科技大学",
    "浙江大学",
    "浙江工业大学",
    "浙江工商大学",
]
ZHEJIANG_HOST_HINTS = [
    ".zj.",
    "zju.edu.cn",
    "zjut.edu.cn",
    "hdu.edu.cn",
    "zjgsu.edu.cn",
    "ylab.ac.cn",
    "tmslab.cn",
    "ucas.ac.cn",
    "kjt.zj.gov.cn",
    "zjlab.org.cn",
]


@dataclass
class CrawlStats:
    discovered_links: int = 0
    fetched_docs: int = 0
    kept_docs: int = 0
    url_duplicates: int = 0
    content_duplicates: int = 0
    title_duplicates: int = 0
    dropped_docs: int = 0
    failed_docs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered_links": self.discovered_links,
            "fetched_docs": self.fetched_docs,
            "kept_docs": self.kept_docs,
            "url_duplicates": self.url_duplicates,
            "content_duplicates": self.content_duplicates,
            "title_duplicates": self.title_duplicates,
            "dropped_docs": self.dropped_docs,
            "failed_docs": self.failed_docs,
        }


def _build_llm_client(settings: Settings) -> LlmClient:
    return LlmClient(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        anthropic_version=settings.anthropic_version,
        max_tokens=settings.llm_max_tokens,
        budget_cny=settings.llm_budget_cny,
        input_price_per_mtoken_cny=settings.llm_input_price_per_mtoken_cny,
        output_price_per_mtoken_cny=settings.llm_output_price_per_mtoken_cny,
    )


def _source_config_path(settings: Settings):
    return settings.workspace_root / "configs" / "sources.yaml"


def _source_timeout(source: SourceConfig, settings: Settings) -> int:
    return int(source.http_timeout_seconds or settings.http_timeout_seconds)


def _build_source_config(source_row: dict, settings: Settings) -> SourceConfig:
    source = get_source_by_key(_source_config_path(settings), str(source_row["source_key"]))
    if source is not None:
        return source
    return SourceConfig(
        key=source_row["source_key"],
        name=source_row["name"],
        entry_urls=source_row["entry_urls"],
        mode=source_row["mode"],
        bootstrap_depth=source_row["bootstrap_depth"],
        weekly_depth=source_row["weekly_depth"],
        allowed_attachments=list(source_row["allowed_attachments"]),
        enable_ai_link_classifier=bool(source_row["enable_ai_link_classifier"]),
        enable_ai_content_judge=bool(source_row["enable_ai_content_judge"]),
        stop_when_older_than_days=int(source_row["stop_when_older_than_days"]),
        http_timeout_seconds=None,
        search_queries=[],
        search_freshness=None,
        search_count=10,
    )


def _is_relevant_search_result(title: str, url: str, snippet: str) -> bool:
    bucket = f"{title} {snippet} {url}".lower()
    if any(blocked in bucket for blocked in ("reguser", "/account/", "login", "toutiao.com", "m.163.com", "c.m.163.com")):
        return False
    host_bucket = url.lower()
    if any(hint.lower() in host_bucket for hint in ZHEJIANG_HOST_HINTS):
        return True
    return any(hint.lower() in bucket for hint in ZHEJIANG_RESULT_HINTS)


def _discover_search_links(
    settings: Settings,
    source: SourceConfig,
    *,
    weekly_mode: bool,
) -> list[DiscoveredLink]:
    client = BochaSearchClient(
        api_key=settings.bocha_api_key,
        endpoint=settings.bocha_search_endpoint,
        timeout_seconds=max(10, _source_timeout(source, settings)),
        max_retries=settings.bocha_max_retries,
        retry_backoff_seconds=settings.bocha_retry_backoff_seconds,
    )
    if not client.enabled or not source.search_queries:
        return []
    freshness = source.search_freshness or ("oneWeek" if weekly_mode else "oneMonth")
    max_per_query = max(1, int(source.search_count or settings.max_links_per_source))
    discovered: list[DiscoveredLink] = []
    for index, query in enumerate(source.search_queries):
        if index > 0 and settings.bocha_request_delay_seconds > 0:
            time.sleep(settings.bocha_request_delay_seconds)
        try:
            results = client.search(
                query=query,
                freshness=freshness,
                count=max_per_query,
                summary=False,
            )
        except Exception as exc:
            logger.warning("Bocha search failed for query %s: %s", query, exc)
            continue
        for item in results:
            if not _is_relevant_search_result(item.title, item.url, item.snippet):
                continue
            discovered.append(
                DiscoveredLink(
                    entry_url=query,
                    source_page_url=query,
                    page_url=item.url,
                    anchor_text=item.title or item.snippet or query,
                )
            )
    return list(dict.fromkeys(discovered))


def inspect_source(source: SourceConfig, settings: Settings, limit: int = 20) -> list[str]:
    if source.search_queries:
        return [item.page_url for item in _discover_search_links(settings, source, weekly_mode=True)[:limit]]
    llm_client = _build_llm_client(settings)
    ai_classifier = AiLinkClassifier(llm_client=llm_client)
    links = []
    for entry_url in source.entry_urls:
        discovered = discover_links(
            source=source,
            entry_url=entry_url,
            timeout_seconds=_source_timeout(source, settings),
            max_pages=min(2, settings.max_listing_pages),
            max_links=limit,
            ai_classifier=ai_classifier,
            weekly_mode=True,
        )
        links.extend(item.page_url for item in discovered)
    return list(dict.fromkeys(links))[:limit]


def crawl_source(
    engine: Engine,
    source_row: dict,
    settings: Settings,
    *,
    weekly_mode: bool,
) -> dict[str, int]:
    llm_client = _build_llm_client(settings)
    source = _build_source_config(source_row, settings)
    timeout_seconds = _source_timeout(source, settings)
    ai_classifier = AiLinkClassifier(llm_client=llm_client)
    content_judge = ContentJudge(llm_client=llm_client) if source.enable_ai_content_judge else None
    stats = CrawlStats()
    oldest_allowed = datetime.now() - timedelta(days=source.stop_when_older_than_days)
    discovered_batches: list[tuple[str, list[DiscoveredLink]]] = []
    if source.search_queries:
        discovered_batches.append(
            (
                "bocha-search",
                _discover_search_links(settings, source, weekly_mode=weekly_mode),
            )
        )
    else:
        for entry_url in source.entry_urls:
            discovered_batches.append(
                (
                    entry_url,
                    discover_links(
                        source=source,
                        entry_url=entry_url,
                        timeout_seconds=timeout_seconds,
                        max_pages=1 if weekly_mode else settings.max_listing_pages,
                        max_links=settings.max_links_per_source,
                        ai_classifier=ai_classifier,
                        weekly_mode=weekly_mode,
                    ),
                )
            )
    for entry_url, discovered in discovered_batches:
        stats.discovered_links += len(discovered)
        for link in discovered:
            if is_blocked_url(link.page_url):
                stats.dropped_docs += 1
                continue
            if candidate_exists(engine, link.page_url):
                stats.url_duplicates += 1
                continue
            candidate_id = upsert_candidate_discovered(engine, source_row["id"], link)
            if is_noise_title(link.anchor_text):
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="prefilter_dropped",
                    title=link.anchor_text,
                    decision_reason="dropped by list-page title prefilter",
                    reason_tags=["title_noise"],
                )
                stats.dropped_docs += 1
                continue
            if fetch_document_by_page_url(engine, link.page_url):
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="duplicate_url",
                    canonical_doc_id=None,
                    decision_reason="page url already exists",
                )
                stats.url_duplicates += 1
                continue
            try:
                document = fetch_document(
                    page_url=link.page_url,
                    source=source,
                    timeout_seconds=timeout_seconds,
                )
                stats.fetched_docs += 1
            except Exception as exc:
                logger.warning("Failed fetching %s: %s", link.page_url, exc)
                record_failure(engine, source_row["id"], link.page_url, "fetch_document", str(exc))
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="failed",
                    decision_reason=str(exc),
                    reason_tags=["fetch_failed"],
                )
                stats.failed_docs += 1
                continue
            published_at = normalize_datetime(document.published_at)
            if published_at and published_at < oldest_allowed:
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="dropped",
                    title=document.title,
                    published_at=published_at,
                    decision_reason="older than configured lookback window",
                    reason_tags=["older_than_window"],
                )
                stats.dropped_docs += 1
                continue
            if source.search_queries:
                search_decision = evaluate_search_source_retention(
                    title=document.title,
                    content=document.content,
                    page_url=document.page_url,
                )
                if search_decision is not None and not search_decision.keep:
                    update_candidate_status(
                        engine,
                        candidate_id,
                        status="dropped",
                        title=document.title,
                        published_at=document.published_at,
                        doc_type=search_decision.doc_type,
                        decision_reason=search_decision.decision_reason,
                        reason_tags=search_decision.reason_tags,
                    )
                    stats.dropped_docs += 1
                    continue
            attachment_names = [item.file_name for item in document.attachments]
            decision = evaluate_retention(
                title=document.title,
                content=document.content[: settings.llm_content_char_limit],
                attachment_names=attachment_names,
                content_judge=content_judge,
            )
            if not decision.keep:
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="dropped",
                    title=document.title,
                    published_at=document.published_at,
                    doc_type=decision.doc_type,
                    decision_reason=decision.decision_reason,
                    reason_tags=decision.reason_tags,
                )
                stats.dropped_docs += 1
                continue
            content_hash = compute_content_hash(document.title, document.content)
            canonical = fetch_document_by_content_hash(engine, content_hash)
            if canonical:
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="duplicate_content",
                    title=document.title,
                    published_at=document.published_at,
                    doc_type=decision.doc_type,
                    decision_reason="duplicate content hash",
                    reason_tags=decision.reason_tags,
                    canonical_doc_id=int(canonical["id"]),
                )
                stats.content_duplicates += 1
                continue
            near_dup = find_near_duplicate_title(engine, source_row["id"], document.title)
            if near_dup and is_near_duplicate(
                document.title,
                str(near_dup["title"]),
                threshold=settings.near_duplicate_threshold,
            ):
                update_candidate_status(
                    engine,
                    candidate_id,
                    status="duplicate_title",
                    title=document.title,
                    published_at=document.published_at,
                    doc_type=decision.doc_type,
                    decision_reason="near duplicate title",
                    reason_tags=decision.reason_tags,
                    canonical_doc_id=int(near_dup["id"]),
                )
                stats.title_duplicates += 1
                continue
            doc_id = insert_raw_document(
                engine=engine,
                source_id=source_row["id"],
                candidate_id=candidate_id,
                entry_url=entry_url,
                page_url=document.page_url,
                title=document.title,
                content=document.content,
                published_at=document.published_at,
                doc_type=decision.doc_type,
                content_hash=content_hash,
                reason_tags=decision.reason_tags,
            )
            if document.attachments:
                insert_attachments(
                    engine,
                    doc_id,
                    [
                        {
                            "attachment_url": item.url,
                            "file_name": item.file_name,
                            "file_ext": item.file_ext,
                            "content_text": item.content_text,
                            "content_hash": item.content_hash,
                        }
                        for item in document.attachments
                    ],
                )
            update_candidate_status(
                engine,
                candidate_id,
                status="kept",
                title=document.title,
                published_at=document.published_at,
                doc_type=decision.doc_type,
                decision_reason=decision.decision_reason,
                reason_tags=decision.reason_tags,
                canonical_doc_id=doc_id,
            )
            resolve_failure(engine, link.page_url)
            mark_source_crawl_progress(
                engine,
                source_row["id"],
                last_seen_published_at=document.published_at,
                last_seen_url=document.page_url,
            )
            stats.kept_docs += 1
    if not weekly_mode:
        mark_source_crawl_progress(engine, source_row["id"], bootstrap_completed=True)
    return stats.to_dict()


def retry_failures(
    engine: Engine,
    source_row: dict,
    settings: Settings,
    failures: Iterable[dict],
) -> dict[str, int]:
    stats = CrawlStats()
    source = _build_source_config(source_row, settings)
    timeout_seconds = _source_timeout(source, settings)
    for failure in failures:
        increment_failure_retry(engine, int(failure["id"]))
        try:
            document = fetch_document(
                page_url=str(failure["page_url"]),
                source=source,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            record_failure(engine, source_row["id"], str(failure["page_url"]), "retry_fetch", str(exc))
            stats.failed_docs += 1
            continue
        resolve_failure(engine, document.page_url)
        stats.fetched_docs += 1
    return stats.to_dict()

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .crawler.deduper import normalize_title
from .crawler.models import DiscoveredLink
from .crawler.source_registry import SourceConfig, load_sources


def build_engine(mysql_dsn: str) -> Engine:
    return create_engine(mysql_dsn, pool_pre_ping=True, future=True)


def init_schema(engine: Engine, schema_file: Path) -> None:
    sql = schema_file.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    _ensure_schema_compat(engine)


def _column_exists(engine: Engine, table_name: str, column_name: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(1)
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {
                "table_name": table_name,
                "column_name": column_name,
            },
        ).scalar_one()
    return bool(row)


def _ensure_schema_compat(engine: Engine) -> None:
    if not _column_exists(engine, "clue_pool", "published_at"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE clue_pool ADD COLUMN published_at DATETIME NULL AFTER summary"))
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    job_type VARCHAR(64) NOT NULL,
                    source_key VARCHAR(64) NULL,
                    trigger_mode ENUM('manual', 'scheduled', 'admin') NOT NULL DEFAULT 'manual',
                    status ENUM('running', 'succeeded', 'failed') NOT NULL DEFAULT 'running',
                    summary_json JSON NULL,
                    error_message TEXT NULL,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    KEY idx_job_runs_status (status, started_at),
                    KEY idx_job_runs_type (job_type, started_at),
                    KEY idx_job_runs_source (source_key, started_at)
                )
                """
            )
        )


def seed_sources(engine: Engine, source_yaml: Path) -> None:
    sources = load_sources(source_yaml)
    with engine.begin() as conn:
        for source in sources:
            conn.execute(
                text(
                    """
                    INSERT INTO data_sources (
                        source_key, name, mode, bootstrap_depth, weekly_depth,
                        allowed_attachments_json, enable_ai_link_classifier,
                        enable_ai_content_judge, stop_when_older_than_days, is_active
                    ) VALUES (
                        :source_key, :name, :mode, :bootstrap_depth, :weekly_depth,
                        :allowed_attachments_json, :enable_ai_link_classifier,
                        :enable_ai_content_judge, :stop_when_older_than_days, 1
                    )
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        mode = VALUES(mode),
                        bootstrap_depth = VALUES(bootstrap_depth),
                        weekly_depth = VALUES(weekly_depth),
                        allowed_attachments_json = VALUES(allowed_attachments_json),
                        enable_ai_link_classifier = VALUES(enable_ai_link_classifier),
                        enable_ai_content_judge = VALUES(enable_ai_content_judge),
                        stop_when_older_than_days = VALUES(stop_when_older_than_days),
                        is_active = 1
                    """
                ),
                {
                    "source_key": source.key,
                    "name": source.name,
                    "mode": source.mode,
                    "bootstrap_depth": source.bootstrap_depth,
                    "weekly_depth": source.weekly_depth,
                    "allowed_attachments_json": json.dumps(
                        source.allowed_attachments, ensure_ascii=False
                    ),
                    "enable_ai_link_classifier": 1 if source.enable_ai_link_classifier else 0,
                    "enable_ai_content_judge": 1 if source.enable_ai_content_judge else 0,
                    "stop_when_older_than_days": source.stop_when_older_than_days,
                },
            )
            source_id = conn.execute(
                text("SELECT id FROM data_sources WHERE source_key = :source_key"),
                {"source_key": source.key},
            ).scalar_one()
            conn.execute(
                text("DELETE FROM source_entry_urls WHERE source_id = :source_id"),
                {"source_id": source_id},
            )
            for entry_url in source.entry_urls:
                conn.execute(
                    text(
                        """
                        INSERT INTO source_entry_urls (source_id, entry_url)
                        VALUES (:source_id, :entry_url)
                        """
                    ),
                    {"source_id": source_id, "entry_url": entry_url},
                )


def fetch_sources(
    engine: Engine,
    source_key: str | None = None,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            ds.id,
            ds.source_key,
            ds.name,
            ds.mode,
            ds.bootstrap_depth,
            ds.weekly_depth,
            ds.allowed_attachments_json,
            ds.enable_ai_link_classifier,
            ds.enable_ai_content_judge,
            ds.stop_when_older_than_days,
            ds.bootstrap_completed,
            ds.last_seen_published_at,
            ds.last_seen_url,
            seu.entry_url
        FROM data_sources ds
        JOIN source_entry_urls seu ON seu.source_id = ds.id
        WHERE 1 = 1
    """
    params: dict[str, Any] = {}
    if not include_inactive:
        sql += " AND ds.is_active = 1"
    if source_key:
        sql += " AND ds.source_key = :source_key"
        params["source_key"] = source_key
    sql += " ORDER BY ds.id ASC, seu.id ASC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            int(row["id"]),
            {
                "id": int(row["id"]),
                "source_key": row["source_key"],
                "name": row["name"],
                "mode": row["mode"],
                "bootstrap_depth": row["bootstrap_depth"],
                "weekly_depth": row["weekly_depth"],
                "allowed_attachments": json.loads(row["allowed_attachments_json"] or "[]"),
                "enable_ai_link_classifier": bool(row["enable_ai_link_classifier"]),
                "enable_ai_content_judge": bool(row["enable_ai_content_judge"]),
                "stop_when_older_than_days": int(row["stop_when_older_than_days"]),
                "bootstrap_completed": bool(row["bootstrap_completed"]),
                "last_seen_published_at": row["last_seen_published_at"],
                "last_seen_url": row["last_seen_url"],
                "entry_urls": [],
            },
        )
        item["entry_urls"].append(row["entry_url"])
    return list(grouped.values())


def candidate_exists(engine: Engine, page_url: str) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT id, status, canonical_doc_id
        FROM crawl_candidates
        WHERE page_url = :page_url
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"page_url": page_url}).mappings().first()
    return dict(row) if row else None


def upsert_candidate_discovered(engine: Engine, source_id: int, link: DiscoveredLink) -> int:
    existing = candidate_exists(engine, link.page_url)
    if existing:
        return int(existing["id"])
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO crawl_candidates (
                    source_id, entry_url, source_page_url, page_url, anchor_text, status
                ) VALUES (
                    :source_id, :entry_url, :source_page_url, :page_url, :anchor_text, 'discovered'
                )
                """
            ),
            {
                "source_id": source_id,
                "entry_url": link.entry_url,
                "source_page_url": link.source_page_url,
                "page_url": link.page_url,
                "anchor_text": link.anchor_text[:512],
            },
        )
        candidate_id = conn.execute(
            text("SELECT id FROM crawl_candidates WHERE page_url = :page_url"),
            {"page_url": link.page_url},
        ).scalar_one()
    return int(candidate_id)


def update_candidate_status(
    engine: Engine,
    candidate_id: int,
    *,
    status: str,
    title: str | None = None,
    published_at: datetime | None = None,
    doc_type: str | None = None,
    decision_reason: str | None = None,
    reason_tags: list[str] | None = None,
    canonical_doc_id: int | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE crawl_candidates
                SET
                    status = :status,
                    title = COALESCE(:title, title),
                    published_at = COALESCE(:published_at, published_at),
                    doc_type = COALESCE(:doc_type, doc_type),
                    decision_reason = COALESCE(:decision_reason, decision_reason),
                    reason_tags = COALESCE(:reason_tags, reason_tags),
                    canonical_doc_id = COALESCE(:canonical_doc_id, canonical_doc_id),
                    fetched_at = :fetched_at
                WHERE id = :candidate_id
                """
            ),
            {
                "candidate_id": candidate_id,
                "status": status,
                "title": title[:512] if title else None,
                "published_at": published_at,
                "doc_type": doc_type,
                "decision_reason": decision_reason,
                "reason_tags": json.dumps(reason_tags, ensure_ascii=False)
                if reason_tags is not None
                else None,
                "canonical_doc_id": canonical_doc_id,
                "fetched_at": datetime.now(),
            },
        )


def fetch_document_by_page_url(engine: Engine, page_url: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, page_url FROM raw_documents WHERE page_url = :page_url LIMIT 1"),
            {"page_url": page_url},
        ).mappings().first()
    return dict(row) if row else None


def fetch_document_by_content_hash(engine: Engine, content_hash: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, page_url, title FROM raw_documents WHERE content_hash = :content_hash LIMIT 1"),
            {"content_hash": content_hash},
        ).mappings().first()
    return dict(row) if row else None


def find_near_duplicate_title(
    engine: Engine,
    source_id: int,
    title: str,
) -> dict[str, Any] | None:
    title_norm = normalize_title(title)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, page_url, title, title_norm
                FROM raw_documents
                WHERE source_id = :source_id
                ORDER BY id DESC
                LIMIT 50
                """
            ),
            {"source_id": source_id},
        ).mappings().all()
    for row in rows:
        if row["title_norm"] == title_norm:
            return dict(row)
    return None


def insert_raw_document(
    engine: Engine,
    source_id: int,
    candidate_id: int,
    entry_url: str,
    page_url: str,
    title: str,
    content: str,
    published_at: datetime | None,
    doc_type: str,
    content_hash: str,
    reason_tags: list[str],
) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO raw_documents (
                    source_id, candidate_id, entry_url, page_url, title, content, published_at,
                    crawled_at, doc_type, content_hash, title_norm, reason_tags
                ) VALUES (
                    :source_id, :candidate_id, :entry_url, :page_url, :title, :content, :published_at,
                    :crawled_at, :doc_type, :content_hash, :title_norm, :reason_tags
                )
                """
            ),
            {
                "source_id": source_id,
                "candidate_id": candidate_id,
                "entry_url": entry_url,
                "page_url": page_url,
                "title": title[:512],
                "content": content,
                "published_at": published_at,
                "crawled_at": datetime.now(),
                "doc_type": doc_type,
                "content_hash": content_hash,
                "title_norm": normalize_title(title)[:512],
                "reason_tags": json.dumps(reason_tags, ensure_ascii=False),
            },
        )
        doc_id = conn.execute(
            text("SELECT id FROM raw_documents WHERE content_hash = :content_hash"),
            {"content_hash": content_hash},
        ).scalar_one()
    return int(doc_id)


def insert_attachments(engine: Engine, raw_document_id: int, attachments: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        for attachment in attachments:
            conn.execute(
                text(
                    """
                    INSERT INTO raw_attachments (
                        raw_document_id, attachment_url, file_name, file_ext, content_text, content_hash
                    ) VALUES (
                        :raw_document_id, :attachment_url, :file_name, :file_ext, :content_text, :content_hash
                    )
                    ON DUPLICATE KEY UPDATE
                        file_name = VALUES(file_name),
                        file_ext = VALUES(file_ext),
                        content_text = VALUES(content_text),
                        content_hash = VALUES(content_hash)
                    """
                ),
                {
                    "raw_document_id": raw_document_id,
                    "attachment_url": attachment["attachment_url"],
                    "file_name": attachment["file_name"],
                    "file_ext": attachment["file_ext"],
                    "content_text": attachment["content_text"],
                    "content_hash": attachment["content_hash"],
                },
            )


def record_failure(
    engine: Engine,
    source_id: int,
    page_url: str,
    error_stage: str,
    error_message: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO crawl_failures (
                    source_id, page_url, error_stage, error_message, retry_count, status, last_error_at
                ) VALUES (
                    :source_id, :page_url, :error_stage, :error_message, 0, 'pending', :last_error_at
                )
                """
            ),
            {
                "source_id": source_id,
                "page_url": page_url[:1024],
                "error_stage": error_stage[:64],
                "error_message": error_message[:4000],
                "last_error_at": datetime.now(),
            },
        )


def resolve_failure(engine: Engine, page_url: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE crawl_failures
                SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
                WHERE page_url = :page_url AND status = 'pending'
                """
            ),
            {"page_url": page_url},
        )


def fetch_pending_failures(engine: Engine, source_id: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, source_id, page_url, error_stage, retry_count
        FROM crawl_failures
        WHERE status = 'pending'
    """
    params: dict[str, Any] = {}
    if source_id is not None:
        sql += " AND source_id = :source_id"
        params["source_id"] = source_id
    sql += " ORDER BY id ASC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_raw_documents(
    engine: Engine,
    *,
    source_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            rd.id,
            rd.source_id,
            ds.source_key,
            rd.page_url,
            rd.title,
            rd.content,
            rd.doc_type,
            rd.published_at,
            rd.created_at
        FROM raw_documents rd
        JOIN data_sources ds ON ds.id = rd.source_id
    """
    params: dict[str, Any] = {"limit": int(limit)}
    if source_key:
        sql += " WHERE ds.source_key = :source_key"
        params["source_key"] = source_key
    sql += " ORDER BY rd.created_at DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_documents_for_extraction(
    engine: Engine,
    *,
    source_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_processed: bool = False,
    only_failed: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            rd.id,
            rd.source_id,
            ds.source_key,
            ds.name AS source_name,
            rd.page_url,
            rd.title,
            rd.content,
            rd.doc_type,
            rd.published_at
        FROM raw_documents rd
        JOIN data_sources ds ON ds.id = rd.source_id
        LEFT JOIN extracted_clues ec ON ec.raw_document_id = rd.id
    """
    where = []
    params: dict[str, Any] = {
        "limit": int(limit),
        "offset": int(offset),
    }
    if source_key:
        where.append("ds.source_key = :source_key")
        params["source_key"] = source_key
    if only_failed:
        where.append("ec.status = 'failed'")
    elif not include_processed:
        where.append("ec.id IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rd.created_at DESC LIMIT :limit OFFSET :offset"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def upsert_extracted_clue(
    engine: Engine,
    *,
    raw_document_id: int,
    source_id: int,
    status: str,
    clue_type: str | None,
    investment_relevance: str | None,
    relevance_score: int | None,
    core_technology: str | None,
    application_scenarios: list[str] | None,
    transformation_signals: list[str] | None,
    summary: str | None,
    decision_reason: str | None,
    reason_tags: list[str] | None,
    used_ai: bool,
    model_name: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO extracted_clues (
                    raw_document_id, source_id, status, clue_type, investment_relevance,
                    relevance_score, core_technology, application_scenarios,
                    transformation_signals, summary, decision_reason, reason_tags,
                    used_ai, model_name
                ) VALUES (
                    :raw_document_id, :source_id, :status, :clue_type, :investment_relevance,
                    :relevance_score, :core_technology, :application_scenarios,
                    :transformation_signals, :summary, :decision_reason, :reason_tags,
                    :used_ai, :model_name
                )
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    clue_type = VALUES(clue_type),
                    investment_relevance = VALUES(investment_relevance),
                    relevance_score = VALUES(relevance_score),
                    core_technology = VALUES(core_technology),
                    application_scenarios = VALUES(application_scenarios),
                    transformation_signals = VALUES(transformation_signals),
                    summary = VALUES(summary),
                    decision_reason = VALUES(decision_reason),
                    reason_tags = VALUES(reason_tags),
                    used_ai = VALUES(used_ai),
                    model_name = VALUES(model_name),
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "raw_document_id": raw_document_id,
                "source_id": source_id,
                "status": status,
                "clue_type": clue_type,
                "investment_relevance": investment_relevance,
                "relevance_score": relevance_score,
                "core_technology": core_technology,
                "application_scenarios": json.dumps(application_scenarios, ensure_ascii=False)
                if application_scenarios is not None
                else None,
                "transformation_signals": json.dumps(transformation_signals, ensure_ascii=False)
                if transformation_signals is not None
                else None,
                "summary": summary,
                "decision_reason": decision_reason,
                "reason_tags": json.dumps(reason_tags, ensure_ascii=False)
                if reason_tags is not None
                else None,
                "used_ai": 1 if used_ai else 0,
                "model_name": model_name,
            },
        )


def clear_extracted_clues(engine: Engine, *, source_key: str | None = None) -> None:
    with engine.begin() as conn:
        if source_key:
            conn.execute(
                text(
                    """
                    DELETE ec
                    FROM extracted_clues ec
                    JOIN data_sources ds ON ds.id = ec.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                {"source_key": source_key},
            )
            return
        conn.execute(text("DELETE FROM extracted_clues"))


def fetch_extracted_for_pool(
    engine: Engine,
    *,
    source_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
    min_score: int = 70,
    allowed_relevance: tuple[str, ...] = ("high", "medium"),
    include_pooled: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            ec.id,
            ec.raw_document_id,
            ec.source_id,
            ds.source_key,
            rd.title,
            rd.page_url,
            ec.clue_type,
            ec.investment_relevance,
            ec.relevance_score,
            ec.core_technology,
            ec.summary,
            rd.published_at,
            cp.id AS clue_pool_id
        FROM extracted_clues ec
        JOIN raw_documents rd ON rd.id = ec.raw_document_id
        JOIN data_sources ds ON ds.id = ec.source_id
        LEFT JOIN clue_pool cp ON cp.extracted_clue_id = ec.id
        WHERE ec.status = 'kept'
          AND COALESCE(ec.relevance_score, 0) >= :min_score
    """
    params: dict[str, Any] = {
        "min_score": int(min_score),
        "limit": int(limit),
        "offset": int(offset),
    }
    if allowed_relevance:
        placeholders = []
        for index, value in enumerate(allowed_relevance):
            key = f"relevance_{index}"
            placeholders.append(f":{key}")
            params[key] = value
        sql += f" AND ec.investment_relevance IN ({', '.join(placeholders)})"
    if source_key:
        sql += " AND ds.source_key = :source_key"
        params["source_key"] = source_key
    if not include_pooled:
        sql += " AND cp.id IS NULL"
    sql += " ORDER BY ec.updated_at DESC LIMIT :limit OFFSET :offset"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def upsert_clue_pool(
    engine: Engine,
    *,
    extracted_clue_id: int,
    raw_document_id: int,
    source_id: int,
    title: str,
    page_url: str,
    clue_type: str | None,
    investment_relevance: str | None,
    relevance_score: int | None,
    core_technology: str | None,
    summary: str | None,
    published_at: datetime | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO clue_pool (
                    extracted_clue_id, raw_document_id, source_id, title, page_url,
                    clue_type, investment_relevance, relevance_score, core_technology, summary, published_at
                ) VALUES (
                    :extracted_clue_id, :raw_document_id, :source_id, :title, :page_url,
                    :clue_type, :investment_relevance, :relevance_score, :core_technology, :summary, :published_at
                )
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    page_url = VALUES(page_url),
                    clue_type = VALUES(clue_type),
                    investment_relevance = VALUES(investment_relevance),
                    relevance_score = VALUES(relevance_score),
                    core_technology = VALUES(core_technology),
                    summary = VALUES(summary),
                    published_at = VALUES(published_at),
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "extracted_clue_id": extracted_clue_id,
                "raw_document_id": raw_document_id,
                "source_id": source_id,
                "title": title[:512],
                "page_url": page_url,
                "clue_type": clue_type,
                "investment_relevance": investment_relevance,
                "relevance_score": relevance_score,
                "core_technology": core_technology,
                "summary": summary,
                "published_at": published_at,
            },
        )


def clear_clue_pool(engine: Engine, *, source_key: str | None = None) -> None:
    with engine.begin() as conn:
        if source_key:
            conn.execute(
                text(
                    """
                    DELETE cp
                    FROM clue_pool cp
                    JOIN data_sources ds ON ds.id = cp.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                {"source_key": source_key},
            )
            return
        conn.execute(text("DELETE FROM clue_pool"))


def clear_runtime_data(engine: Engine, *, source_key: str | None = None) -> None:
    with engine.begin() as conn:
        if source_key:
            params = {"source_key": source_key}
            conn.execute(
                text(
                    """
                    DELETE ra
                    FROM raw_attachments ra
                    JOIN raw_documents rd ON rd.id = ra.raw_document_id
                    JOIN data_sources ds ON ds.id = rd.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    DELETE cp
                    FROM clue_pool cp
                    JOIN data_sources ds ON ds.id = cp.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    DELETE ec
                    FROM extracted_clues ec
                    JOIN data_sources ds ON ds.id = ec.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    DELETE rd
                    FROM raw_documents rd
                    JOIN data_sources ds ON ds.id = rd.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    DELETE cc
                    FROM crawl_candidates cc
                    JOIN data_sources ds ON ds.id = cc.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    DELETE cf
                    FROM crawl_failures cf
                    JOIN data_sources ds ON ds.id = cf.source_id
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            conn.execute(
                text(
                    """
                    UPDATE data_sources ds
                    SET
                        ds.bootstrap_completed = 0,
                        ds.last_seen_published_at = NULL,
                        ds.last_seen_url = NULL
                    WHERE ds.source_key = :source_key
                    """
                ),
                params,
            )
            return

        conn.execute(text("DELETE FROM raw_attachments"))
        conn.execute(text("DELETE FROM clue_pool"))
        conn.execute(text("DELETE FROM extracted_clues"))
        conn.execute(text("DELETE FROM raw_documents"))
        conn.execute(text("DELETE FROM crawl_candidates"))
        conn.execute(text("DELETE FROM crawl_failures"))
        conn.execute(text("DELETE FROM job_runs"))
        conn.execute(
            text(
                """
                UPDATE data_sources
                SET
                    bootstrap_completed = 0,
                    last_seen_published_at = NULL,
                    last_seen_url = NULL
                """
            )
        )


def fetch_clue_pool(
    engine: Engine,
    *,
    source_key: str | None = None,
    limit: int = 20,
    only_unpushed: bool = True,
    max_age_days: int | None = None,
    require_published_at: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            cp.id,
            cp.extracted_clue_id,
            cp.raw_document_id,
            cp.source_id,
            ds.source_key,
            ds.name AS source_name,
            cp.title,
            cp.page_url,
            cp.clue_type,
            cp.investment_relevance,
            cp.relevance_score,
            cp.core_technology,
            cp.summary,
            cp.published_at,
            cp.pool_status,
            cp.pushed_at
        FROM clue_pool cp
        JOIN data_sources ds ON ds.id = cp.source_id
        WHERE 1 = 1
    """
    params: dict[str, Any] = {"limit": int(limit)}
    if source_key:
        sql += " AND ds.source_key = :source_key"
        params["source_key"] = source_key
    if only_unpushed:
        sql += " AND cp.pushed_at IS NULL"
    if require_published_at:
        sql += " AND cp.published_at IS NOT NULL"
    if max_age_days is not None:
        # Use updated_at when published_at is missing so newly pooled rows still qualify.
        sql += " AND COALESCE(cp.published_at, cp.updated_at) >= :published_after"
        params["published_after"] = datetime.now() - timedelta(days=int(max_age_days))
    sql += " ORDER BY COALESCE(cp.published_at, cp.updated_at) DESC, cp.updated_at DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_clue_pool_admin(
    engine: Engine,
    *,
    source_key: str | None = None,
    pool_status: str | None = None,
    days: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            cp.id,
            cp.source_id,
            ds.source_key,
            ds.name AS source_name,
            cp.title,
            cp.page_url,
            cp.clue_type,
            cp.investment_relevance,
            cp.relevance_score,
            cp.core_technology,
            cp.summary,
            cp.published_at,
            cp.pool_status,
            cp.reviewer,
            cp.review_note,
            cp.pushed_at,
            cp.created_at,
            cp.updated_at
        FROM clue_pool cp
        JOIN data_sources ds ON ds.id = cp.source_id
        WHERE 1 = 1
    """
    params: dict[str, Any] = {"limit": int(limit)}
    if source_key:
        sql += " AND ds.source_key = :source_key"
        params["source_key"] = source_key
    if pool_status:
        sql += " AND cp.pool_status = :pool_status"
        params["pool_status"] = pool_status
    if days is not None:
        sql += " AND COALESCE(cp.published_at, cp.created_at) >= :created_after"
        params["created_after"] = datetime.now() - timedelta(days=int(days))
    sql += " ORDER BY COALESCE(cp.published_at, cp.created_at) DESC, cp.id DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def mark_clue_pool_pushed(engine: Engine, clue_pool_ids: list[int]) -> None:
    if not clue_pool_ids:
        return
    placeholders = ", ".join(f":id_{index}" for index in range(len(clue_pool_ids)))
    params = {f"id_{index}": clue_pool_ids[index] for index in range(len(clue_pool_ids))}
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                UPDATE clue_pool
                SET pushed_at = :pushed_at, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """
            ),
            {"pushed_at": datetime.now(), **params},
        )


def increment_failure_retry(engine: Engine, failure_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE crawl_failures
                SET retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = :failure_id
                """
            ),
            {"failure_id": failure_id},
        )


def mark_source_crawl_progress(
    engine: Engine,
    source_id: int,
    *,
    bootstrap_completed: bool | None = None,
    last_seen_published_at: datetime | None = None,
    last_seen_url: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE data_sources
                SET
                    bootstrap_completed = COALESCE(:bootstrap_completed, bootstrap_completed),
                    last_seen_published_at = COALESCE(:last_seen_published_at, last_seen_published_at),
                    last_seen_url = COALESCE(:last_seen_url, last_seen_url),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :source_id
                """
            ),
            {
                "source_id": source_id,
                "bootstrap_completed": 1 if bootstrap_completed else None,
                "last_seen_published_at": last_seen_published_at,
                "last_seen_url": last_seen_url,
            },
        )


def set_source_active(engine: Engine, source_key: str, is_active: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE data_sources
                SET is_active = :is_active, updated_at = CURRENT_TIMESTAMP
                WHERE source_key = :source_key
                """
            ),
            {
                "source_key": source_key,
                "is_active": 1 if is_active else 0,
            },
        )


def fetch_source_admin_rows(engine: Engine) -> list[dict[str, Any]]:
    sql = """
        SELECT
            ds.id,
            ds.source_key,
            ds.name,
            ds.mode,
            ds.is_active,
            ds.bootstrap_completed,
            ds.last_seen_published_at,
            ds.last_seen_url,
            ds.updated_at,
            GROUP_CONCAT(DISTINCT seu.entry_url ORDER BY seu.id SEPARATOR '\n') AS entry_urls,
            COALESCE(cc.candidate_count, 0) AS candidate_count,
            COALESCE(rd.doc_count, 0) AS raw_doc_count,
            COALESCE(ec.kept_count, 0) AS extracted_kept_count,
            COALESCE(cp.pool_count, 0) AS pool_count,
            COALESCE(cf.failure_count, 0) AS pending_failure_count
        FROM data_sources ds
        LEFT JOIN source_entry_urls seu ON seu.source_id = ds.id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS candidate_count
            FROM crawl_candidates
            GROUP BY source_id
        ) cc ON cc.source_id = ds.id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS doc_count
            FROM raw_documents
            GROUP BY source_id
        ) rd ON rd.source_id = ds.id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS kept_count
            FROM extracted_clues
            WHERE status = 'kept'
            GROUP BY source_id
        ) ec ON ec.source_id = ds.id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS pool_count
            FROM clue_pool
            GROUP BY source_id
        ) cp ON cp.source_id = ds.id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS failure_count
            FROM crawl_failures
            WHERE status = 'pending'
            GROUP BY source_id
        ) cf ON cf.source_id = ds.id
        GROUP BY
            ds.id, ds.source_key, ds.name, ds.mode, ds.is_active,
            ds.bootstrap_completed, ds.last_seen_published_at, ds.last_seen_url, ds.updated_at,
            cc.candidate_count, rd.doc_count, ec.kept_count, cp.pool_count, cf.failure_count
        ORDER BY ds.is_active DESC, ds.id ASC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(row) for row in rows]


def fetch_recent_failures(engine: Engine, *, limit: int = 20) -> list[dict[str, Any]]:
    sql = """
        SELECT
            cf.id,
            ds.source_key,
            ds.name AS source_name,
            cf.page_url,
            cf.error_stage,
            cf.error_message,
            cf.retry_count,
            cf.last_error_at
        FROM crawl_failures cf
        JOIN data_sources ds ON ds.id = cf.source_id
        WHERE cf.status = 'pending'
        ORDER BY cf.last_error_at DESC, cf.id DESC
        LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": int(limit)}).mappings().all()
    return [dict(row) for row in rows]


def fetch_dashboard_stats(engine: Engine) -> dict[str, Any]:
    sql = """
        SELECT
            (SELECT COUNT(*) FROM data_sources) AS total_sources,
            (SELECT COUNT(*) FROM data_sources WHERE is_active = 1) AS active_sources,
            (SELECT COUNT(*) FROM crawl_failures WHERE status = 'pending') AS pending_failures,
            (SELECT COUNT(*) FROM clue_pool WHERE pool_status = 'pending') AS pending_pool_items,
            (
                SELECT COUNT(*)
                FROM clue_pool
                WHERE pushed_at IS NULL
                  AND published_at IS NOT NULL
                  AND published_at >= :published_after
            ) AS ready_to_push
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(sql),
            {"published_after": datetime.now() - timedelta(days=7)},
        ).mappings().one()
    return dict(row)


def start_job_run(
    engine: Engine,
    *,
    job_type: str,
    source_key: str | None,
    trigger_mode: str,
) -> int:
    started_at = datetime.now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_runs (
                    job_type, source_key, trigger_mode, status, started_at
                ) VALUES (
                    :job_type, :source_key, :trigger_mode, 'running', :started_at
                )
                """
            ),
            {
                "job_type": job_type,
                "source_key": source_key,
                "trigger_mode": trigger_mode,
                "started_at": started_at,
            },
        )
        run_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
    return int(run_id)


def finish_job_run(
    engine: Engine,
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE job_runs
                SET
                    status = :status,
                    summary_json = :summary_json,
                    error_message = :error_message,
                    finished_at = :finished_at,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "summary_json": json.dumps(summary, ensure_ascii=False) if summary is not None else None,
                "error_message": error_message,
                "finished_at": datetime.now(),
            },
        )


def fetch_job_runs(engine: Engine, *, limit: int = 20) -> list[dict[str, Any]]:
    sql = """
        SELECT
            id,
            job_type,
            source_key,
            trigger_mode,
            status,
            summary_json,
            error_message,
            started_at,
            finished_at,
            updated_at
        FROM job_runs
        ORDER BY started_at DESC, id DESC
        LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": int(limit)}).mappings().all()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("summary_json"):
            try:
                item["summary"] = json.loads(item["summary_json"])
            except json.JSONDecodeError:
                item["summary"] = None
        else:
            item["summary"] = None
        items.append(item)
    return items

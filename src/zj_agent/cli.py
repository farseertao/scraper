from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .admin_server import serve_admin
from .config import Settings, load_settings
from .crawler.filters import evaluate_retention
from .crawler.source_registry import get_source_by_key
from .db import (
    build_engine,
    clear_runtime_data,
    fetch_pending_failures,
    fetch_raw_documents,
    fetch_sources,
    init_schema,
    seed_sources,
)
from .extraction import extract_clues
from .jobs import run_job_recorded, run_weekly_job
from .notifications import notify_clue_pool, send_feishu_test
from .pipeline import crawl_source, inspect_source, retry_failures
from .pooling import sync_clue_pool


def _setup_logging(verbose: bool, settings: Settings | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings and settings.app_log_dir:
        log_dir = Path(settings.app_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "zj-agent.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def _source_config_path(settings_path_root: Path) -> Path:
    return settings_path_root / "configs" / "sources.yaml"


def _ensure_schema(settings: Settings) -> None:
    engine = build_engine(settings.mysql_dsn)
    init_schema(engine, settings.workspace_root / "sql" / "schema.sql")


def cmd_init_db(settings: Settings) -> None:
    engine = build_engine(settings.mysql_dsn)
    init_schema(engine, settings.workspace_root / "sql" / "schema.sql")
    print("[OK] Database schema initialized.")


def cmd_seed_sources(settings: Settings) -> None:
    engine = build_engine(settings.mysql_dsn)
    seed_sources(engine, _source_config_path(settings.workspace_root))
    print("[OK] Source registry initialized.")


def cmd_clear_runtime_data(settings: Settings, source_key: str | None) -> None:
    engine = build_engine(settings.mysql_dsn)
    clear_runtime_data(engine, source_key=source_key)
    target = source_key or "all"
    print(f"[OK] clear-runtime-data {target}: runtime tables cleared and source progress reset.")


def cmd_inspect(settings: Settings, source_key: str) -> None:
    source = get_source_by_key(_source_config_path(settings.workspace_root), source_key)
    if not source:
        raise SystemExit(f"Unknown source: {source_key}")
    links = inspect_source(source=source, settings=settings)
    print(f"[OK] Inspect {source_key}: {len(links)} links")
    for link in links[:20]:
        print(link)


def cmd_crawl(settings: Settings, source_key: str | None, weekly_mode: bool) -> None:
    engine = build_engine(settings.mysql_dsn)
    rows = fetch_sources(engine, source_key=source_key)
    if not rows:
        raise SystemExit("No active sources found. Run seed-sources first.")
    for row in rows:
        stats = crawl_source(engine, row, settings, weekly_mode=weekly_mode)
        mode = "weekly" if weekly_mode else "bootstrap"
        print(f"[OK] {mode} {row['source_key']}: {stats}")


def cmd_retry_failures(settings: Settings, source_key: str | None) -> None:
    engine = build_engine(settings.mysql_dsn)
    source_rows = fetch_sources(engine, source_key=source_key)
    if not source_rows:
        raise SystemExit("No matching active sources found.")
    by_id = {int(row["id"]): row for row in source_rows}
    for source_id, row in by_id.items():
        failures = fetch_pending_failures(engine, source_id=source_id)
        if not failures:
            continue
        stats = retry_failures(engine, row, settings, failures)
        print(f"[OK] retry-failures {row['source_key']}: {stats}")


def cmd_audit_quality(settings: Settings, source_key: str | None, limit: int) -> None:
    engine = build_engine(settings.mysql_dsn)
    rows = fetch_raw_documents(engine, source_key=source_key, limit=limit)
    if not rows:
        print("[OK] audit-quality: no raw documents found")
        return

    flagged: list[dict] = []
    for row in rows:
        decision = evaluate_retention(
            title=str(row["title"] or ""),
            content=str(row["content"] or "")[: settings.llm_content_char_limit],
            attachment_names=[],
            content_judge=None,
        )
        if not decision.keep:
            flagged.append(
                {
                    "source_key": row["source_key"],
                    "title": row["title"],
                    "doc_type": row["doc_type"],
                    "reason_tags": decision.reason_tags,
                    "decision_reason": decision.decision_reason,
                    "page_url": row["page_url"],
                }
            )

    print(f"[OK] audit-quality scanned={len(rows)} flagged={len(flagged)}")
    for item in flagged[:30]:
        print(
            f"{item['source_key']} | {item['doc_type']} | {item['title']} | "
            f"{item['reason_tags']} | {item['decision_reason']}"
        )
        print(f"  {item['page_url']}")


def cmd_extract(
    settings: Settings,
    source_key: str | None,
    *,
    limit: int,
    offset: int,
    clear_existing: bool,
    retry_failed: bool,
) -> None:
    engine = build_engine(settings.mysql_dsn)
    stats = extract_clues(
        engine,
        settings,
        source_key=source_key,
        limit=limit,
        offset=offset,
        clear_existing=clear_existing,
        retry_failed=retry_failed,
    )
    target = source_key or "all"
    print(f"[OK] extract {target}: {stats}")


def cmd_pool_sync(
    settings: Settings,
    source_key: str | None,
    *,
    limit: int,
    offset: int,
    min_score: int,
    clear_existing: bool,
) -> None:
    engine = build_engine(settings.mysql_dsn)
    stats = sync_clue_pool(
        engine,
        source_key=source_key,
        limit=limit,
        offset=offset,
        min_score=min_score,
        clear_existing=clear_existing,
    )
    target = source_key or "all"
    print(f"[OK] pool sync {target}: {stats}")


def cmd_notify_feishu_test(settings: Settings, text: str) -> None:
    result = send_feishu_test(settings, text)
    print(f"[OK] notify feishu-test: {result}")


def cmd_notify_clue_pool(settings: Settings, source_key: str | None, *, limit: int, resend: bool) -> None:
    engine = build_engine(settings.mysql_dsn)
    stats = notify_clue_pool(engine, settings, source_key=source_key, limit=limit, resend=resend)
    target = source_key or "all"
    action = "resend" if resend else "notify"
    print(f"[OK] {action} clue-pool {target}: {stats}")


def cmd_run_weekly_job(settings: Settings, *, source_key: str | None) -> None:
    _ensure_schema(settings)
    engine = build_engine(settings.mysql_dsn)
    summary = run_job_recorded(
        engine,
        job_type="weekly-job",
        trigger_mode="manual",
        source_key=source_key,
        runner=lambda: run_weekly_job(engine, settings, source_key=source_key),
    )
    target = source_key or "all"
    print(f"[OK] weekly-job {target}: {summary}")


def cmd_serve(settings: Settings, *, host: str, port: int) -> None:
    _ensure_schema(settings)
    engine = build_engine(settings.mysql_dsn)
    serve_admin(engine, settings, host=host, port=port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Achievement crawler CLI")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Initialize MySQL schema")
    sub.add_parser("seed-sources", help="Seed source registry from YAML")
    clear_runtime = sub.add_parser("clear-runtime-data", help="Clear crawled/extracted/pooled runtime data")
    clear_runtime.add_argument("--source", default=None, help="Optional single source key")
    clear_runtime.add_argument("--all", action="store_true", help="Clear all runtime data")

    extract = sub.add_parser("extract", help="Extract structured clues from raw documents")
    extract.add_argument("--source", default=None, help="Source key")
    extract.add_argument("--all", action="store_true", help="Run all sources")
    extract.add_argument("--limit", type=int, default=50, help="Number of raw documents to process")
    extract.add_argument("--offset", type=int, default=0, help="Offset for raw document scan")
    extract.add_argument("--clear-existing", action="store_true", help="Clear existing extracted clues first")
    extract.add_argument("--retry-failed", action="store_true", help="Retry failed extracted clues only")

    pool = sub.add_parser("pool", help="Sync extracted clues into the business clue pool")
    pool_sub = pool.add_subparsers(dest="pool_command", required=True)
    pool_sync = pool_sub.add_parser("sync", help="Sync eligible extracted clues into clue_pool")
    pool_sync.add_argument("--source", default=None, help="Source key")
    pool_sync.add_argument("--all", action="store_true", help="Run all sources")
    pool_sync.add_argument("--limit", type=int, default=50, help="Number of extracted clues to sync")
    pool_sync.add_argument("--offset", type=int, default=0, help="Offset for extracted clue scan")
    pool_sync.add_argument("--min-score", type=int, default=70, help="Minimum relevance score to enter clue_pool")
    pool_sync.add_argument("--clear-existing", action="store_true", help="Clear existing clue_pool rows first")

    notify = sub.add_parser("notify", help="Send notifications")
    notify_sub = notify.add_subparsers(dest="notify_command", required=True)
    feishu_test = notify_sub.add_parser("feishu-test", help="Send a Feishu test message")
    feishu_test.add_argument("--text", required=True, help="Text content to send")
    clue_pool_notify = notify_sub.add_parser("clue-pool", help="Push clue_pool items to Feishu")
    clue_pool_notify.add_argument("--source", default=None, help="Source key")
    clue_pool_notify.add_argument("--all", action="store_true", help="Run all sources")
    clue_pool_notify.add_argument("--limit", type=int, default=10, help="Number of clue_pool items to send")
    clue_pool_notify.add_argument("--resend", action="store_true", help="Resend the latest summary even if already pushed")

    crawl = sub.add_parser("crawl", help="Run crawler commands")
    crawl_sub = crawl.add_subparsers(dest="crawl_command", required=True)

    bootstrap = crawl_sub.add_parser("bootstrap", help="Run bootstrap crawl")
    bootstrap.add_argument("--source", default=None, help="Source key")
    bootstrap.add_argument("--all", action="store_true", help="Run all sources")

    weekly = crawl_sub.add_parser("weekly", help="Run weekly crawl")
    weekly.add_argument("--source", default=None, help="Source key")
    weekly.add_argument("--all", action="store_true", help="Run all sources")

    inspect = crawl_sub.add_parser("inspect", help="Inspect one source")
    inspect.add_argument("--source", required=True, help="Source key")

    retry_cmd = crawl_sub.add_parser("retry-failures", help="Retry pending failures")
    retry_cmd.add_argument("--source", default=None, help="Source key")
    retry_cmd.add_argument("--all", action="store_true", help="Run all sources")

    audit_cmd = crawl_sub.add_parser("audit-quality", help="Audit retained documents with current rules")
    audit_cmd.add_argument("--source", default=None, help="Source key")
    audit_cmd.add_argument("--limit", type=int, default=100, help="Number of recent raw documents to scan")

    run = sub.add_parser("run", help="Run orchestrated jobs")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    weekly_job = run_sub.add_parser("weekly-job", help="Run the full weekly workflow")
    weekly_job.add_argument("--source", default=None, help="Optional single source key")

    serve = sub.add_parser("serve", help="Start the lightweight admin web server")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve.add_argument("--port", type=int, default=8080, help="Bind port")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()
    _setup_logging(args.verbose, settings)

    if args.command == "init-db":
        cmd_init_db(settings)
        return
    if args.command == "seed-sources":
        cmd_seed_sources(settings)
        return
    if args.command == "clear-runtime-data":
        cmd_clear_runtime_data(settings, None if args.all else args.source)
        return
    if args.command == "extract":
        cmd_extract(
            settings,
            None if args.all else args.source,
            limit=args.limit,
            offset=args.offset,
            clear_existing=args.clear_existing,
            retry_failed=args.retry_failed,
        )
        return
    if args.command == "pool" and args.pool_command == "sync":
        cmd_pool_sync(
            settings,
            None if args.all else args.source,
            limit=args.limit,
            offset=args.offset,
            min_score=args.min_score,
            clear_existing=args.clear_existing,
        )
        return
    if args.command == "notify":
        if args.notify_command == "feishu-test":
            cmd_notify_feishu_test(settings, args.text)
            return
        if args.notify_command == "clue-pool":
            cmd_notify_clue_pool(
                settings,
                None if args.all else args.source,
                limit=args.limit,
                resend=args.resend,
            )
            return
    if args.command == "crawl":
        if args.crawl_command == "inspect":
            cmd_inspect(settings, args.source)
            return
        if args.crawl_command == "bootstrap":
            cmd_crawl(settings, None if args.all else args.source, weekly_mode=False)
            return
        if args.crawl_command == "weekly":
            cmd_crawl(settings, None if args.all else args.source, weekly_mode=True)
            return
        if args.crawl_command == "retry-failures":
            cmd_retry_failures(settings, None if args.all else args.source)
            return
        if args.crawl_command == "audit-quality":
            cmd_audit_quality(settings, args.source, args.limit)
            return
    if args.command == "run" and args.run_command == "weekly-job":
        cmd_run_weekly_job(settings, source_key=args.source)
        return
    if args.command == "serve":
        cmd_serve(settings, host=args.host, port=args.port)
        return
    parser.error("Unsupported command")


if __name__ == "__main__":
    main()

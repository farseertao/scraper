from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from .ai import ClueExtractor, LlmClient
from .config import Settings
from .crawler.filters import is_login_page, is_noise_title
from .db import clear_extracted_clues, fetch_documents_for_extraction, upsert_extracted_clue


@dataclass
class ExtractionStats:
    scanned_docs: int = 0
    kept_docs: int = 0
    dropped_docs: int = 0
    failed_docs: int = 0
    ai_used_docs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned_docs": self.scanned_docs,
            "kept_docs": self.kept_docs,
            "dropped_docs": self.dropped_docs,
            "failed_docs": self.failed_docs,
            "ai_used_docs": self.ai_used_docs,
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


def extract_clues(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str | None,
    limit: int,
    offset: int,
    clear_existing: bool = False,
    retry_failed: bool = False,
) -> dict[str, int]:
    if clear_existing:
        clear_extracted_clues(engine, source_key=source_key)

    llm_client = _build_llm_client(settings)
    if not llm_client.enabled:
        raise RuntimeError("LLM is disabled. Configure LLM_API_KEY before running extract.")

    extractor = ClueExtractor(llm_client=llm_client)
    rows = fetch_documents_for_extraction(
        engine,
        source_key=source_key,
        limit=limit,
        offset=offset,
        include_processed=retry_failed,
        only_failed=retry_failed,
    )
    stats = ExtractionStats()

    for row in rows:
        stats.scanned_docs += 1
        title = str(row["title"] or "")
        content = str(row["content"] or "")

        if is_login_page(title, content):
            upsert_extracted_clue(
                engine,
                raw_document_id=int(row["id"]),
                source_id=int(row["source_id"]),
                status="dropped",
                clue_type="noise",
                investment_relevance="none",
                relevance_score=0,
                core_technology="",
                application_scenarios=[],
                transformation_signals=[],
                summary="登录页或后台页，非目标线索。",
                decision_reason="dropped because page looks like login or admin page",
                reason_tags=["login_page"],
                used_ai=False,
                model_name=None,
            )
            stats.dropped_docs += 1
            continue

        if is_noise_title(title):
            upsert_extracted_clue(
                engine,
                raw_document_id=int(row["id"]),
                source_id=int(row["source_id"]),
                status="dropped",
                clue_type="noise",
                investment_relevance="none",
                relevance_score=0,
                core_technology="",
                application_scenarios=[],
                transformation_signals=[],
                summary="标题呈现为通知或活动类内容，非目标科技成果线索。",
                decision_reason="dropped by extraction title prefilter",
                reason_tags=["title_noise"],
                used_ai=False,
                model_name=None,
            )
            stats.dropped_docs += 1
            continue

        extracted = extractor.extract(
            title=title,
            content=content[: settings.llm_content_char_limit],
            attachment_names=[],
            source_name=str(row["source_name"] or ""),
        )
        if extracted is None:
            upsert_extracted_clue(
                engine,
                raw_document_id=int(row["id"]),
                source_id=int(row["source_id"]),
                status="failed",
                clue_type="other",
                investment_relevance="none",
                relevance_score=0,
                core_technology="",
                application_scenarios=[],
                transformation_signals=[],
                summary="",
                decision_reason="extraction failed because AI result was unavailable",
                reason_tags=["extract_failed"],
                used_ai=False,
                model_name=settings.llm_model,
            )
            stats.failed_docs += 1
            continue

        upsert_extracted_clue(
            engine,
            raw_document_id=int(row["id"]),
            source_id=int(row["source_id"]),
            status="kept" if extracted.keep else "dropped",
            clue_type=extracted.clue_type,
            investment_relevance=extracted.investment_relevance,
            relevance_score=extracted.relevance_score,
            core_technology=extracted.core_technology,
            application_scenarios=extracted.application_scenarios,
            transformation_signals=extracted.transformation_signals,
            summary=extracted.summary,
            decision_reason=extracted.decision_reason,
            reason_tags=extracted.reason_tags,
            used_ai=extracted.used_ai,
            model_name=settings.llm_model,
        )
        stats.ai_used_docs += 1
        if extracted.keep:
            stats.kept_docs += 1
        else:
            stats.dropped_docs += 1

    return stats.to_dict()

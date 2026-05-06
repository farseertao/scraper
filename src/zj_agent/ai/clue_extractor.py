from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .llm_client import LlmClient


@dataclass(frozen=True)
class ClueExtraction:
    keep: bool
    clue_type: str
    investment_relevance: str
    relevance_score: int
    core_technology: str
    application_scenarios: list[str]
    transformation_signals: list[str]
    summary: str
    reason_tags: list[str]
    decision_reason: str
    used_ai: bool = True


class ClueExtractor:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    def extract(
        self,
        *,
        title: str,
        content: str,
        attachment_names: Sequence[str],
        source_name: str,
    ) -> ClueExtraction | None:
        if not self.llm_client.enabled:
            return None

        prompt = f"""
You extract structured clue data from Chinese science and technology webpages.

Return one JSON object only:
{{
  "keep": true,
  "clue_type": "achievement",
  "investment_relevance": "high",
  "relevance_score": 85,
  "core_technology": "one concise sentence",
  "application_scenarios": ["scenario 1", "scenario 2"],
  "transformation_signals": ["专利", "中试"],
  "summary": "A short Chinese summary within 120 Chinese characters.",
  "reason_tags": ["科技成果", "关键技术", "产业化"],
  "decision_reason": "short reason in Chinese"
}}

Goal:
- Keep only pages that are relevant to technology achievement discovery, technology transfer, or investable research progress.
- Drop pages that are mainly notices, visits, academic events, management rules, institution updates, or honor-only news without a clear technical object.

Definitions:
- clue_type should be one of: achievement, transfer, patent, award, poc, pilot, prototype, other, noise
- investment_relevance should be one of: high, medium, low, none
- relevance_score should be an integer from 0 to 100

Keep if the page clearly contains at least one of these:
- 科技成果、科研成果、关键技术、核心技术、技术突破、重大研究进展
- 成果转化、技术转移、技术转让、技术许可、产业化
- 发明专利、专利转化、专利奖、科技奖、提名项目、公示项目
- 概念验证、中试、样机、工程化、应用场景

Drop if the page is mainly about:
- 通知、预告、讲座、论坛、研讨会、学术交流、学院动态
- 来访、调研、党建、招聘、招生、工作会议、管理办法
- 保密宣传、安全教育、制度通报、地方合作概况
- 只有荣誉或表彰信息，但没有明确技术对象、成果内容或转化信号

Conservative rules:
- If uncertain, return keep=false.
- If keep=false, set investment_relevance to none or low.
- Prefer short, concrete phrases instead of long paragraphs.

Source: {source_name[:120]}
Title: {title[:300]}
Attachment names: {", ".join(attachment_names)[:500]}
Content:
{content[:3200]}
"""
        try:
            data = self.llm_client.extract_structured(prompt)
        except Exception:
            return None

        reason_tags = data.get("reason_tags") or []
        if not isinstance(reason_tags, list):
            reason_tags = [str(reason_tags)]

        app_scenarios = data.get("application_scenarios") or []
        if not isinstance(app_scenarios, list):
            app_scenarios = [str(app_scenarios)]

        transformation_signals = data.get("transformation_signals") or []
        if not isinstance(transformation_signals, list):
            transformation_signals = [str(transformation_signals)]

        try:
            relevance_score = int(data.get("relevance_score", 0) or 0)
        except Exception:
            relevance_score = 0
        relevance_score = max(0, min(100, relevance_score))

        return ClueExtraction(
            keep=bool(data.get("keep", False)),
            clue_type=str(data.get("clue_type", "other")).strip() or "other",
            investment_relevance=str(data.get("investment_relevance", "none")).strip() or "none",
            relevance_score=relevance_score,
            core_technology=str(data.get("core_technology", "")).strip(),
            application_scenarios=[str(item).strip() for item in app_scenarios if str(item).strip()],
            transformation_signals=[
                str(item).strip() for item in transformation_signals if str(item).strip()
            ],
            summary=str(data.get("summary", "")).strip(),
            reason_tags=[str(tag).strip() for tag in reason_tags if str(tag).strip()],
            decision_reason=str(data.get("decision_reason", "")).strip(),
            used_ai=True,
        )

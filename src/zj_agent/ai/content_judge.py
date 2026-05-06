from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .llm_client import LlmClient


@dataclass(frozen=True)
class RetentionDecision:
    keep: bool
    doc_type: str
    reason_tags: list[str]
    decision_reason: str
    used_ai: bool = False


class ContentJudge:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    def judge(
        self,
        title: str,
        content: str,
        attachment_names: Sequence[str],
    ) -> RetentionDecision | None:
        if not self.llm_client.enabled:
            return None

        prompt = f"""
You judge whether a Chinese webpage should be kept as a research-achievement clue for technology transfer or investment discovery.

Return one JSON object only:
{{
  "keep": true,
  "doc_type": "achievement",
  "reason_tags": ["成果转化", "关键技术"],
  "decision_reason": "short reason"
}}

Keep only if the page is mainly about at least one of these:
- 科技成果、科研成果、重大研究进展、技术突破
- 成果转化、技术转移、技术转让、技术许可、产业化
- 发明专利、专利转化、专利奖、科技奖、获奖项目、提名项目、公示项目
- 概念验证、中试、样机、关键技术、核心技术、应用场景

Drop if the page is mainly about these:
- 通知、预告、讲座、论坛、研讨会、学术交流、学院动态
- 来访、调研、党建、招聘、招生、工作会议、管理办法
- 安全教育、保密宣传、案件通报、政策解读、地方合作概况
- 只有荣誉或表彰信息，但没有明确技术对象、成果内容或转化信号

Rules:
- Be conservative. If unsure, return keep=false.
- A generic award or honor should be dropped unless it clearly refers to a technology, project, patent, or transferable achievement.
- A paper or academic report should be dropped unless it clearly highlights application, transfer, prototype, pilot, or major technical progress.
- Prefer doc_type from: achievement, transfer, patent, award, poc, pilot, prototype, other, noise.

Title: {title[:300]}
Attachment names: {", ".join(attachment_names)[:500]}
Content:
{content[:2800]}
"""
        try:
            data = self.llm_client.extract_structured(prompt)
        except Exception:
            return None

        reason_tags = data.get("reason_tags") or []
        if not isinstance(reason_tags, list):
            reason_tags = [str(reason_tags)]

        return RetentionDecision(
            keep=bool(data.get("keep", False)),
            doc_type=str(data.get("doc_type", "other")).strip() or "other",
            reason_tags=[str(tag).strip() for tag in reason_tags if str(tag).strip()],
            decision_reason=str(data.get("decision_reason", "")).strip(),
            used_ai=True,
        )

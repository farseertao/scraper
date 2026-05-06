from __future__ import annotations

from typing import Callable

from .llm_client import LlmClient

LinkDecision = str
BatchLinkDecider = Callable[[str, str, list[tuple[str, str]]], dict[str, LinkDecision]]


class AiLinkClassifier:
    def __init__(self, llm_client: LlmClient, max_links_per_page: int = 40) -> None:
        self.llm_client = llm_client
        self.max_links_per_page = max_links_per_page

    def _build_prompt(
        self,
        source_url: str,
        page_url: str,
        candidates: list[tuple[str, str]],
    ) -> str:
        rows = []
        for idx, (url, text) in enumerate(candidates, start=1):
            rows.append(f"{idx}. url={url} | text={(text or '').strip()[:100]}")
        return f"""
Classify each candidate link on a Chinese research website page.

Return one JSON object only:
{{
  "decisions": [
    {{"idx": 1, "label": "article"}},
    {{"idx": 2, "label": "pagination"}},
    {{"idx": 3, "label": "ignore"}}
  ]
}}

Rules:
- article: detail page for one news/article/achievement item.
- pagination: next page or numbered page inside the same listing.
- ignore: navigation, empty link, column page, homepage, downloads that are not detail pages.

source_url={source_url}
page_url={page_url}

Candidates:
{chr(10).join(rows)}
"""

    def classify_links(
        self,
        source_url: str,
        page_url: str,
        candidates: list[tuple[str, str]],
    ) -> dict[str, LinkDecision]:
        if not candidates or not self.llm_client.enabled:
            return {}
        picked = candidates[: self.max_links_per_page]
        try:
            data = self.llm_client.extract_structured(
                self._build_prompt(source_url=source_url, page_url=page_url, candidates=picked)
            )
        except Exception:
            return {}
        result: dict[str, LinkDecision] = {}
        for item in data.get("decisions", []):
            if not isinstance(item, dict):
                continue
            idx = item.get("idx")
            label = str(item.get("label", "")).strip().lower()
            if isinstance(idx, str) and idx.isdigit():
                idx = int(idx)
            if not isinstance(idx, int):
                continue
            if label not in {"article", "pagination", "ignore"}:
                continue
            if 1 <= idx <= len(picked):
                result[picked[idx - 1][0]] = label
        return result

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests
from requests.exceptions import HTTPError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class BochaSearchClient:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        timeout_seconds: int = 15,
        *,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.endpoint = (endpoint or "").strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff_seconds = max(0.5, float(retry_backoff_seconds))
        self.session = requests.Session()
        self.session.trust_env = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def search(
        self,
        *,
        query: str,
        freshness: str | None = None,
        count: int = 10,
        summary: bool = False,
    ) -> list[SearchResult]:
        if not self.enabled:
            return []
        payload: dict[str, object] = {
            "query": query,
            "count": int(count),
            "summary": bool(summary),
        }
        if freshness:
            payload["freshness"] = freshness
        response = self._post_with_retry(payload)
        data = response.json()
        rows = _extract_result_rows(data)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for row in rows:
            url = str(
                row.get("url")
                or row.get("link")
                or row.get("href")
                or row.get("display_url")
                or ""
            ).strip()
            title = str(row.get("title") or row.get("name") or "").strip()
            snippet = str(
                row.get("snippet")
                or row.get("summary")
                or row.get("description")
                or row.get("body")
                or ""
            ).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(SearchResult(title=title or url, url=url, snippet=snippet))
        return results

    def _post_with_retry(self, payload: dict[str, object]) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "Investment-Report-MCP/1.0",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code in {429, 503} and attempt + 1 < self.max_retries:
                    wait_seconds = self.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "Bocha search throttled with HTTP %s, retrying in %.1fs (attempt %s/%s)",
                        response.status_code,
                        wait_seconds,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(wait_seconds)
                    continue
                response.raise_for_status()
                return response
            except HTTPError as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    raise
                wait_seconds = self.retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Bocha search failed with HTTP error, retrying in %.1fs (attempt %s/%s): %s",
                    wait_seconds,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                time.sleep(wait_seconds)
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    raise
                wait_seconds = self.retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Bocha search request failed, retrying in %.1fs (attempt %s/%s): %s",
                    wait_seconds,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                time.sleep(wait_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Bocha search failed without a captured error")


def _extract_result_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    candidates = [
        payload.get("data"),
        payload.get("results"),
        payload.get("items"),
        payload.get("webPages"),
        payload.get("web"),
    ]
    for candidate in candidates:
        rows = _extract_nested_rows(candidate)
        if rows:
            return rows
    return []


def _extract_nested_rows(candidate: object) -> list[dict]:
    if isinstance(candidate, list):
        return [item for item in candidate if isinstance(item, dict)]
    if not isinstance(candidate, dict):
        return []
    for key in ("value", "results", "items", "data", "webPages"):
        inner = candidate.get(key)
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
        if isinstance(inner, dict):
            nested = _extract_nested_rows(inner)
            if nested:
                return nested
    return []

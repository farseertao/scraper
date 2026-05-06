from __future__ import annotations

import json
import re
from typing import Any

import requests


class LlmClient:
    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        anthropic_version: str = "2023-06-01",
        max_tokens: int = 300,
        budget_cny: float = 20.0,
        input_price_per_mtoken_cny: float = 8.807,
        output_price_per_mtoken_cny: float = 44.035,
    ) -> None:
        self.provider = (provider or "openai").strip().lower()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.anthropic_version = anthropic_version
        self.max_tokens = max_tokens
        self.budget_cny = budget_cny
        self.input_price_per_mtoken_cny = input_price_per_mtoken_cny
        self.output_price_per_mtoken_cny = output_price_per_mtoken_cny
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_cny = 0.0
        self._hard_stop = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and not self._hard_stop and not self.budget_exceeded

    @property
    def budget_exceeded(self) -> bool:
        return self.total_cost_cny >= self.budget_cny

    @staticmethod
    def _extract_json_from_text(text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise
            return json.loads(match.group(0))

    def _add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.total_prompt_tokens += max(0, int(prompt_tokens))
        self.total_completion_tokens += max(0, int(completion_tokens))
        self.total_cost_cny = (
            self.total_prompt_tokens * self.input_price_per_mtoken_cny / 1_000_000.0
            + self.total_completion_tokens * self.output_price_per_mtoken_cny / 1_000_000.0
        )
        if self.total_cost_cny >= self.budget_cny:
            self._hard_stop = True

    def _openai_extract(self, prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are an information extraction engine."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
        if resp.status_code >= 400:
            payload.pop("response_format", None)
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        self._add_usage(
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                chunk.get("text", "") if isinstance(chunk, dict) else str(chunk) for chunk in content
            )
        raw_text = str(content)
        try:
            return self._extract_json_from_text(raw_text)
        except Exception:
            repair_payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Convert the input into one valid JSON object. Return JSON only.",
                    },
                    {
                        "role": "user",
                        "content": f"Repair this into one valid JSON object only:\n{raw_text[:6000]}",
                    },
                ],
                "temperature": 0,
                "max_tokens": min(self.max_tokens, 400),
                "response_format": {"type": "json_object"},
            }
            repair_resp = requests.post(
                url,
                headers=headers,
                json=repair_payload,
                timeout=self.timeout_seconds,
            )
            if repair_resp.status_code >= 400:
                repair_payload.pop("response_format", None)
                repair_resp = requests.post(
                    url,
                    headers=headers,
                    json=repair_payload,
                    timeout=self.timeout_seconds,
                )
            repair_resp.raise_for_status()
            repair_data = repair_resp.json()
            repair_usage = repair_data.get("usage") or {}
            self._add_usage(
                int(repair_usage.get("prompt_tokens", 0) or 0),
                int(repair_usage.get("completion_tokens", 0) or 0),
            )
            repair_content = repair_data["choices"][0]["message"]["content"]
            if isinstance(repair_content, list):
                repair_content = "".join(
                    chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                    for chunk in repair_content
                )
            return self._extract_json_from_text(str(repair_content))

    def _anthropic_extract(self, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.1,
            "system": "You are an information extraction engine. Return one JSON object only.",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        resp = requests.post(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.anthropic_version,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        self._add_usage(
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0),
        )
        parts = data.get("content", [])
        text = "\n".join(
            item.get("text", "")
            for item in parts
            if isinstance(item, dict) and item.get("type") == "text"
        )
        return self._extract_json_from_text(text)

    def extract_structured(self, prompt: str) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM is disabled.")
        if self.provider == "anthropic":
            return self._anthropic_extract(prompt)
        return self._openai_extract(prompt)

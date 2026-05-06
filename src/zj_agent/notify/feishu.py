from __future__ import annotations

import base64
import hashlib
import hmac
import time

import requests


class FeishuNotifier:
    def __init__(self, webhook_url: str, sign_secret: str = "", timeout_seconds: int = 15) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.sign_secret = (sign_secret or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def _signature_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json; charset=utf-8"}

    def _signature_payload(self) -> dict[str, str]:
        if not self.sign_secret:
            return {}
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.sign_secret}".encode("utf-8")
        sign = base64.b64encode(
            hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        return {"timestamp": timestamp, "sign": sign}

    def send_text(self, text: str) -> dict:
        if not self.enabled:
            raise RuntimeError("Feishu webhook is not configured.")
        payload = {
            "msg_type": "text",
            "content": {
                "text": text,
            },
        }
        payload.update(self._signature_payload())
        response = requests.post(
            self.webhook_url,
            headers=self._signature_headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def send_post(self, *, title: str, lines: list[list[dict]]) -> dict:
        if not self.enabled:
            raise RuntimeError("Feishu webhook is not configured.")
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": lines,
                    }
                }
            },
        }
        payload.update(self._signature_payload())
        response = requests.post(
            self.webhook_url,
            headers=self._signature_headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

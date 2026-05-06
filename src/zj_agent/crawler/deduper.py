from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_title(title: str) -> str:
    value = normalize_whitespace(title).lower()
    value = re.sub(r"[【】\[\]()（）:：\-_|]+", " ", value)
    return normalize_whitespace(value)


def compute_content_hash(title: str, content: str) -> str:
    title_norm = normalize_title(title)
    content_norm = normalize_whitespace(content)
    return hashlib.sha256(f"{title_norm}\n{content_norm}".encode("utf-8")).hexdigest()


def is_near_duplicate(title_a: str, title_b: str, threshold: float) -> bool:
    if not title_a or not title_b:
        return False
    score = title_similarity(title_a, title_b)
    return score >= threshold


def title_similarity(title_a: str, title_b: str) -> float:
    if not title_a or not title_b:
        return 0.0
    return SequenceMatcher(None, normalize_title(title_a), normalize_title(title_b)).ratio()

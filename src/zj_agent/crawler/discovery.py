from __future__ import annotations

import re
import warnings
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError as RequestsSSLError

from ..ai.link_classifier import AiLinkClassifier
from .filters import is_blocked_url, is_noise_title
from .models import DiscoveredLink
from .source_registry import SourceConfig

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WECHAT_HOST = "mp.weixin.qq.com"
ARTICLE_HINTS = ["news", "article", "detail", "content", "kxyj", "kycg", "xwdt", "research"]
SESSION = requests.Session()
SESSION.trust_env = False


def _get(url: str, timeout_seconds: int) -> requests.Response:
    try:
        response = SESSION.get(url, timeout=timeout_seconds, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response
    except RequestsSSLError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            response = SESSION.get(
                url, timeout=timeout_seconds, headers={"User-Agent": USER_AGENT}, verify=False
            )
        response.raise_for_status()
        return response
    except RequestsConnectionError:
        parsed = urlparse(url)
        if parsed.scheme == "https":
            fallback = url.replace("https://", "http://", 1)
            response = SESSION.get(
                fallback, timeout=timeout_seconds, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            return response
        raise


def _decode_html(response: requests.Response) -> str:
    guessed = UnicodeDammit(response.content, is_html=True).unicode_markup
    if guessed:
        return guessed
    return response.text


def _is_article_url(full_url: str, source_host: str, anchor_text: str) -> bool:
    parsed = urlparse(full_url)
    if not parsed.scheme.startswith("http"):
        return False
    if parsed.netloc and parsed.netloc != source_host and WECHAT_HOST not in parsed.netloc:
        return False
    if WECHAT_HOST in parsed.netloc:
        return True
    path = parsed.path.lower()
    bucket = f"{full_url.lower()} {anchor_text.lower()}"
    if path.endswith("/list.htm") or re.search(r"/list\d+\.htm$", path) or path.endswith("/main.htm"):
        return False
    if re.search(r"/20\d{2}/\d{4}/c\d+a\d+/page\.htm$", path):
        return True
    if re.search(r"/info/\d+/\d+\.htm$", path):
        return True
    if re.match(r"^\d{2}20\d{2}-\d{2}\b", anchor_text.strip()):
        return True
    if path.endswith("/page.htm") and "/list" not in path:
        return True
    return any(hint in bucket for hint in ARTICLE_HINTS) and len(anchor_text.strip()) >= 6


def _is_pagination_url(full_url: str, source_url: str) -> bool:
    parsed = urlparse(full_url)
    source = urlparse(source_url)
    if parsed.netloc and parsed.netloc != source.netloc:
        return False
    path = parsed.path.lower()
    src_path = source.path.lower()
    source_name = src_path.rsplit("/", 1)[-1].replace(".htm", "")
    if re.search(r"/list(\d+)?\.htm$", path):
        src_dir = src_path.rsplit("/", 1)[0]
        return path.startswith(src_dir + "/")
    if source_name and re.search(rf"/{re.escape(source_name)}/\d+\.htm$", path):
        src_dir = src_path.rsplit("/", 1)[0]
        return path.startswith(src_dir + "/")
    return False


def _extract_links(
    entry_url: str,
    page_url: str,
    source_host: str,
    soup: BeautifulSoup,
    ai_classifier: AiLinkClassifier | None,
) -> tuple[list[str], list[DiscoveredLink]]:
    pagination_links: list[str] = []
    article_links: list[DiscoveredLink] = []
    candidates: list[tuple[str, str]] = []
    seen = set()
    for tag in soup.select("a[href]"):
        href = (tag.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(page_url, href)
        text = (tag.get_text() or "").strip()
        if full in seen:
            continue
        if is_blocked_url(full):
            continue
        seen.add(full)
        candidates.append((full, text))
    ai_decisions = ai_classifier.classify_links(entry_url, page_url, candidates) if ai_classifier else {}
    for full, text in candidates:
        if is_noise_title(text):
            continue
        label = ai_decisions.get(full)
        if label == "pagination" or (not label and _is_pagination_url(full, entry_url)):
            pagination_links.append(full)
            continue
        if label == "article" or (not label and _is_article_url(full, source_host, text)):
            article_links.append(
                DiscoveredLink(
                    entry_url=entry_url,
                    source_page_url=page_url,
                    page_url=full,
                    anchor_text=text,
                )
            )
    return pagination_links, article_links


def discover_links(
    source: SourceConfig,
    entry_url: str,
    timeout_seconds: int,
    max_pages: int,
    max_links: int,
    ai_classifier: AiLinkClassifier | None = None,
    weekly_mode: bool = False,
) -> list[DiscoveredLink]:
    source_host = urlparse(entry_url).netloc
    queue: deque[str] = deque([entry_url])
    visited_pages: set[str] = set()
    found: list[DiscoveredLink] = []
    while queue and len(visited_pages) < max_pages and len(found) < max_links:
        page_url = queue.popleft()
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)
        try:
            response = _get(page_url, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        html = _decode_html(response)
        soup = BeautifulSoup(html, "lxml")
        pagination, articles = _extract_links(
            entry_url=entry_url,
            page_url=page_url,
            source_host=source_host,
            soup=soup,
            ai_classifier=ai_classifier if source.enable_ai_link_classifier else None,
        )
        found.extend(articles)
        if weekly_mode:
            continue
        for link in pagination:
            if link not in visited_pages:
                queue.append(link)
    return list(dict.fromkeys(found))

from __future__ import annotations

import warnings
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit
from dateutil import parser as dateparser
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError as RequestsSSLError

from .attachments import fetch_attachments
from .filters import is_login_page
from .models import FetchedDocument
from .source_registry import SourceConfig

try:
    import trafilatura  # type: ignore
except Exception:  # pragma: no cover
    trafilatura = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
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


def _extract_published_at(soup: BeautifulSoup) -> datetime | None:
    candidates: list[str] = []
    for selector in [
        "meta[property='article:published_time']",
        "meta[name='pubdate']",
        "meta[name='publishdate']",
        "meta[name='ArticleTitle']",
        "time",
    ]:
        for node in soup.select(selector):
            if node.name == "meta":
                value = (node.get("content") or "").strip()
            else:
                value = (node.get("datetime") or "").strip() or node.get_text().strip()
            if value:
                candidates.append(value)
    text = soup.get_text(" ", strip=True)
    import re

    match = re.search(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)?)", text)
    if match:
        candidates.append(match.group(1))
    for dt_str in candidates:
        try:
            return dateparser.parse(dt_str, fuzzy=True)
        except Exception:
            continue
    return None


def fetch_document(page_url: str, source: SourceConfig, timeout_seconds: int) -> FetchedDocument:
    response = _get(page_url, timeout_seconds=timeout_seconds)
    html = _decode_html(response)
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else page_url
    content = ""
    if trafilatura is not None:
        content = (
            trafilatura.extract(
                html,
                include_comments=False,
                include_links=False,
                include_tables=False,
                no_fallback=False,
            )
            or ""
        ).strip()
    if not content:
        content = soup.get_text("\n", strip=True)
    attachments = fetch_attachments(
        page_url=page_url,
        soup=soup,
        allowed_exts=source.allowed_attachments,
        timeout_seconds=timeout_seconds,
    )
    if len(content) < 300 and attachments:
        attachment_text = "\n".join(item.content_text for item in attachments if item.content_text)
        if attachment_text:
            content = f"{content}\n{attachment_text}".strip()
    if is_login_page(title, content):
        raise ValueError("login/admin page detected")
    return FetchedDocument(
        page_url=page_url,
        title=title[:512],
        content=content,
        published_at=_extract_published_at(soup),
        attachments=attachments,
    )

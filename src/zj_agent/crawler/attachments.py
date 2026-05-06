from __future__ import annotations

import hashlib
import io
import zipfile
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from .models import AttachmentContent

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

SESSION = requests.Session()
SESSION.trust_env = False


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _parse_docx(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as docx:
        xml = docx.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    for para in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in para.findall(".//w:t", ns)).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _parse_pdf(content: bytes) -> str:
    if PdfReader is None:
        return ""
    reader = PdfReader(io.BytesIO(content))
    lines = []
    for page in reader.pages:
        try:
            lines.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(lines).strip()


def fetch_attachments(
    page_url: str,
    soup: BeautifulSoup,
    allowed_exts: list[str],
    timeout_seconds: int,
) -> list[AttachmentContent]:
    results: list[AttachmentContent] = []
    seen_urls: set[str] = set()
    for tag in soup.select("a[href]"):
        href = (tag.get("href") or "").strip()
        if not href:
            continue
        file_name = href.rsplit("/", 1)[-1].split("?", 1)[0]
        file_ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if file_ext not in allowed_exts:
            continue
        attachment_url = requests.compat.urljoin(page_url, href)
        if attachment_url in seen_urls:
            continue
        seen_urls.add(attachment_url)
        try:
            response = SESSION.get(attachment_url, timeout=timeout_seconds)
            response.raise_for_status()
            if file_ext == "pdf":
                text = _parse_pdf(response.content)
            elif file_ext == "docx":
                text = _parse_docx(response.content)
            else:
                text = ""
        except Exception:
            continue
        results.append(
            AttachmentContent(
                url=attachment_url,
                file_name=file_name,
                file_ext=file_ext,
                content_text=text,
                content_hash=_hash_text(text),
            )
        )
    return results

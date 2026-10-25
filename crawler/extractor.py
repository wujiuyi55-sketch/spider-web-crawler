"""把 HTML 页面解析成结构化数据。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .urls import is_same_site, normalize_url

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_REMOVABLE_TAGS = ("script", "style", "noscript", "template", "svg", "canvas")


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = []
    for line in value.split("\n"):
        line = _SPACE_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for meta in soup.find_all("meta"):
        key = (
            meta.get("name")
            or meta.get("property")
            or meta.get("http-equiv")
            or ""
        ).strip().lower()
        if key in wanted:
            content = meta.get("content")
            if content and content.strip():
                return _clip(str(content), 1_000)
    return ""


def _extract_meta(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for meta in soup.find_all("meta"):
        key = (
            meta.get("name")
            or meta.get("property")
            or meta.get("http-equiv")
            or meta.get("itemprop")
        )
        content = meta.get("content")
        if not key or content is None:
            continue
        key = _clip(str(key), 80).lower()
        value = _clip(str(content), 600)
        if not key or not value or key in result:
            continue
        result[key] = value
        if len(result) >= 50:
            break
    return result


def _extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if len(records) >= 20 or depth > 8:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("@type")
        if node_type:
            if isinstance(node_type, list):
                node_type = ", ".join(str(item) for item in node_type[:4])
            name = node.get("name") or node.get("headline") or node.get("title")
            record: dict[str, Any] = {"type": _clip(str(node_type), 120)}
            if name:
                record["name"] = _clip(str(name), 300)
            url = node.get("url") or node.get("@id")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                record["url"] = _clip(url, 500)
            records.append(record)
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 200_000:
            continue
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return records[:20]


def _extract_headings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        text = _clean_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        headings.append({"level": int(tag.name[1]), "text": _clip(text, 240)})
        if len(headings) >= 100:
            break
    return headings


def _extract_links(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        raw_href = str(anchor.get("href", "")).strip()
        if not raw_href or raw_href.startswith(
            ("#", "javascript:", "mailto:", "tel:", "data:", "blob:")
        ):
            continue
        try:
            resolved = normalize_url(raw_href, base=page_url)
        except (ValueError, TypeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _clean_text(anchor.get_text(" ", strip=True))
        if not text:
            text = (anchor.get("aria-label") or anchor.get("title") or "").strip()
        rel_value = anchor.get("rel") or []
        if isinstance(rel_value, str):
            rel_tokens = rel_value.lower().split()
        else:
            rel_tokens = [str(token).lower() for token in rel_value]
        links.append(
            {
                "url": resolved,
                "text": _clip(text, 180),
                "internal": is_same_site(page_url, resolved),
                "nofollow": "nofollow" in rel_tokens,
            }
        )
        if len(links) >= 400:
            break
    return links


def _extract_images(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for image in soup.find_all("img"):
        raw_src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or image.get("data-lazy-src")
            or ""
        )
        if not raw_src:
            continue
        try:
            resolved = urljoin(page_url, str(raw_src).strip())
            if urlsplit(resolved).scheme not in {"http", "https"}:
                continue
        except ValueError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        images.append(
            {
                "url": _clip(resolved, 1_000),
                "alt": _clip(str(image.get("alt") or ""), 240),
            }
        )
        if len(images) >= 100:
            break
    return images


def _extract_tables(soup: BeautifulSoup) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue
            values = [
                _clip(_clean_text(cell.get_text(" ", strip=True)), 300)
                for cell in cells[:10]
            ]
            if values:
                rows.append(values)
            if len(rows) >= 30:
                break
        if rows:
            caption = table.find("caption")
            tables.append(
                {
                    "caption": _clip(
                        _clean_text(caption.get_text(" ", strip=True))
                        if caption
                        else "",
                        300,
                    ),
                    "rows": rows,
                }
            )
        if len(tables) >= 3:
            break
    return tables


def _extract_favicon(soup: BeautifulSoup, page_url: str) -> str:
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        tokens = rel.lower().split() if isinstance(rel, str) else [
            str(item).lower() for item in rel
        ]
        if "icon" in tokens:
            try:
                return urljoin(page_url, str(link["href"]).strip())
            except ValueError:
                continue
    return ""


def count_words(text: str) -> int:
    """中文按字计数，拉丁文本按词计数。"""

    return len(_CJK_RE.findall(text)) + len(_WORD_RE.findall(text))


def extract_page(
    content: bytes,
    url: str,
    content_type: str = "",
    max_text_chars: int = 20_000,
    from_encoding: str | None = None,
) -> dict[str, Any]:
    """解析 HTML bytes，返回可直接 JSON 序列化的页面数据。"""

    if not content:
        raise ValueError("页面内容为空")

    soup = BeautifulSoup(content, "lxml", from_encoding=from_encoding or None)
    structured_data = _extract_json_ld(soup)
    for tag in soup.find_all(_REMOVABLE_TAGS):
        tag.decompose()

    html_tag = soup.find("html")
    lang = ""
    if html_tag and html_tag.get("lang"):
        lang = _clip(str(html_tag["lang"]), 40)

    meta = _extract_meta(soup)
    title = ""
    if soup.title:
        title = _clean_text(soup.title.get_text(" ", strip=True))
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = _clean_text(h1.get_text(" ", strip=True))
    title = _clip(title, 400)

    description = _meta_content(
        soup, "description", "og:description", "twitter:description"
    )
    keywords = _meta_content(soup, "keywords")

    headings = _extract_headings(soup)
    links = _extract_links(soup, url)
    images = _extract_images(soup, url)
    tables = _extract_tables(soup)
    favicon = _extract_favicon(soup, url)

    content_root = soup.find("main") or soup.find("article") or soup.body or soup
    full_text = _clean_text(content_root.get_text("\n", strip=True))
    word_count = count_words(full_text)
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    text_truncated = len(full_text) > max_text_chars
    text = full_text[:max_text_chars]
    if text_truncated:
        text = text.rstrip() + "…"

    return {
        "url": url,
        "title": title or "（无标题）",
        "description": description,
        "keywords": keywords,
        "lang": lang,
        "favicon": favicon,
        "content_type": content_type,
        "word_count": word_count,
        "text": text,
        "text_truncated": text_truncated,
        "headings": headings,
        "links": links,
        "images": images,
        "tables": tables,
        "meta": meta,
        "structured_data": structured_data,
        "content_hash": content_hash,
    }

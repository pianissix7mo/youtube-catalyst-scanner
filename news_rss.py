#!/usr/bin/env python3
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def parse_pubdate(value: str) -> datetime:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def clean_headline(title: str, source_name: str) -> str:
    title = str(title or "").strip()
    source_name = str(source_name or "").strip()
    if source_name:
        suffix = f" - {source_name}"
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return title


def fetch_google_news(
    session: requests.Session,
    query: str,
    when: str = "24h",
    timeout: int = 20,
) -> list[dict[str, Any]]:
    q = f"({query}) when:{when}".strip()
    r = session.get(
        GOOGLE_NEWS_RSS,
        params={
            "q": q,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)

    output: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        source_el = item.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = str(source_el.attrib.get("url") or "") if source_el is not None else ""
        title = clean_headline(item.findtext("title", default=""), source_name)
        link = (item.findtext("link", default="") or "").strip()
        guid = (item.findtext("guid", default="") or "").strip()
        published = parse_pubdate(item.findtext("pubDate", default=""))
        domain = urlparse(source_url).netloc.lower().removeprefix("www.") if source_url else ""
        if not title:
            continue
        output.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "published_at_utc": published.isoformat(),
                "source_name": source_name,
                "source_url": source_url,
                "domain": domain or re.sub(r"\s+", "-", source_name.lower()),
            }
        )
    return output

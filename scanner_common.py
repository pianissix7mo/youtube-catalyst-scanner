#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "says", "said", "the", "to",
    "up", "with", "will", "after", "before", "new", "amid", "about", "over", "under",
}

COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "limited", "plc",
    "holdings", "holding", "group", "sa", "nv", "ag", "llc", "lp", "the",
}

GENERIC_FIRST_WORDS = {
    "american", "global", "international", "national", "united", "first", "general", "new",
    "digital", "advanced", "capital", "financial", "energy", "technology", "technologies",
    "systems", "services", "resources", "communications", "health", "healthcare",
}

TICKER_DENYLIST = {
    "A", "AI", "ALL", "AM", "ARE", "AT", "BE", "BIG", "BY", "CAN", "CEO", "CFO", "CO",
    "DO", "FOR", "GO", "IT", "IPO", "IRS", "ON", "OR", "NOW", "SEC", "SO", "US", "USA",
    "UK", "EU", "EV", "ETF", "FED", "GDP", "CEO", "CPI", "PPI", "PMI", "EPS", "YTD",
}


def load_config() -> dict[str, Any]:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9$%+&./ -]+", " ", text)
    return " ".join(text.split())


def clean_company_name(value: str) -> str:
    words = normalize_text(value).replace("&", " and ").split()
    while words and words[-1].strip(".,") in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words).strip()


def content_tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", normalize_text(value))
        if len(token) >= 3 and token not in STOPWORDS
    }


def title_similarity(a: str, b: str) -> float:
    aa, bb = content_tokens(a), content_tokens(b)
    if not aa or not bb:
        return 0.0
    inter = len(aa & bb)
    union = len(aa | bb)
    jaccard = inter / union if union else 0.0
    containment = inter / min(len(aa), len(bb))
    return max(jaccard, 0.8 * containment)


def parse_timestamp(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    candidates = [
        raw,
        raw.replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def ratio_score(ratio: float) -> float:
    """1x=0, 2x=25, 4x=50, 8x=75, 16x=100."""
    if ratio <= 1:
        return 0.0
    return clamp(25.0 * math.log(ratio, 2))


def source_diversity_score(count: int) -> float:
    ladder = [(1, 20), (2, 38), (3, 55), (5, 72), (8, 88), (12, 100)]
    score = 0.0
    for threshold, value in ladder:
        if count >= threshold:
            score = float(value)
    return score


def freshness_score(hours_old: float) -> float:
    if hours_old <= 1:
        return 100.0
    if hours_old <= 3:
        return 92.0
    if hours_old <= 6:
        return 82.0
    if hours_old <= 12:
        return 68.0
    if hours_old <= 24:
        return 52.0
    if hours_old <= 48:
        return 30.0
    return 10.0


def catalyst_quality_score(categories: Iterable[str], sec_forms: Iterable[str] = ()) -> float:
    cats = {str(x) for x in categories}
    forms = {str(x).upper() for x in sec_forms}
    score = 45.0
    if forms & {"8-K", "6-K"}:
        score = max(score, 92.0)
    if forms & {"10-Q", "10-K", "20-F"}:
        score = max(score, 88.0)
    if forms & {"S-1", "S-3", "424B2", "424B5", "SC 13D", "SC 13G"}:
        score = max(score, 78.0)
    weights = {
        "mna": 94.0,
        "earnings_guidance": 90.0,
        "contract_order": 82.0,
        "regulatory_legal": 80.0,
        "pricing_capacity": 77.0,
        "macro_rates": 85.0,
        "crypto": 70.0,
        "ai_semis": 68.0,
    }
    for cat in cats:
        score = max(score, weights.get(cat, 0.0))
    return score


def median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def stable_event_id(entity: str, representative_title: str, category: str) -> str:
    payload = f"{normalize_text(entity)}|{category}|{normalize_text(representative_title)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def youtube_query(entity: str, ticker: str | None, title: str) -> tuple[str, list[str]]:
    entity_tokens = content_tokens(entity)
    extras = [
        token for token in re.findall(r"[A-Za-z0-9]+", title)
        if len(token) >= 4 and token.lower() not in STOPWORDS and token.lower() not in entity_tokens
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for token in extras:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(token)
        if len(unique) >= 3:
            break
    lead = ticker if ticker and len(ticker) >= 2 else entity
    query = " ".join([lead] + unique).strip()
    return query[:100], unique


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

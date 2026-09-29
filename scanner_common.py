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

DIRECTION_CONFLICT_GROUPS = (
    ({"inflow", "inflows", "buying"}, {"outflow", "outflows", "selling"}),
    (
        {"rise", "rises", "rising", "surge", "surges", "gain", "gains", "higher", "increase", "increases", "increased", "raises", "raised"},
        {"fall", "falls", "falling", "drop", "drops", "decline", "declines", "lower", "decrease", "decreases", "decreased", "cuts", "cut", "slump"},
    ),
    ({"beat", "beats", "beating"}, {"miss", "misses", "missed"}),
    ({"approve", "approves", "approved", "approval"}, {"reject", "rejects", "rejected", "denies", "denied"}),
    ({"upgrade", "upgrades", "upgraded"}, {"downgrade", "downgrades", "downgraded"}),
)

YOUTUBE_CATEGORY_ZH_TERMS = {
    "earnings_guidance": ("财报", "业绩", "营收", "利润", "指引", "预期"),
    "mna": ("收购", "并购", "合并", "要约"),
    "contract_order": ("合同", "订单", "合作", "供应"),
    "regulatory_legal": ("诉讼", "调查", "监管", "反垄断", "罚款"),
    "pricing_capacity": ("涨价", "价格", "产能", "短缺", "生产"),
    "ai_semis": ("人工智能", "芯片", "半导体", "GPU", "HBM", "数据中心"),
    "macro_rates": ("美联储", "利率", "国债", "收益率", "通胀", "关税"),
    "crypto": ("比特币", "以太坊", "加密", "ETF"),
}

YOUTUBE_TICKER_ZH_ALIASES = {
    "AAPL": ("苹果",), "AMZN": ("亚马逊",), "AMD": ("超威",), "AVGO": ("博通",),
    "BABA": ("阿里巴巴",), "GOOG": ("谷歌",), "GOOGL": ("谷歌",), "INTC": ("英特尔",),
    "JD": ("京东",), "META": ("Meta", "脸书"), "MSFT": ("微软",), "MU": ("美光",),
    "NIO": ("蔚来",), "NVDA": ("英伟达", "辉达"), "TSLA": ("特斯拉",),
}

YOUTUBE_ENTITY_ZH_ALIASES = {
    "Federal Reserve / Rates": ("美联储", "联储", "利率", "降息", "加息"),
    "Inflation": ("通胀", "通货膨胀", "CPI", "PPI"),
    "Tariffs / Trade": ("关税", "贸易"),
    "Bitcoin / Crypto": ("比特币", "以太坊", "加密", "BTC", "ETH"),
    "AI Infrastructure": ("人工智能", "AI", "数据中心", "GPU", "HBM", "芯片"),
    "OpenAI": ("OpenAI",), "Anthropic": ("Anthropic",), "SK hynix": ("SK海力士", "海力士"),
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


def explicit_ticker_tokens(title: str) -> list[str]:
    value = str(title or "")
    patterns = (
        r"\$([A-Z]{1,5})\b",
        r"\(([A-Z]{1,5})\)",
        r"\b(?:NASDAQ|NYSE|AMEX)\s*:\s*([A-Z]{1,5})\b",
    )
    output: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, value):
            token = str(match).upper()
            if token and token not in seen:
                seen.add(token)
                output.append(token)
    return output


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


def titles_have_direction_conflict(a: str, b: str) -> bool:
    aa = set(re.findall(r"[a-z0-9]+", normalize_text(a)))
    bb = set(re.findall(r"[a-z0-9]+", normalize_text(b)))
    for positive, negative in DIRECTION_CONFLICT_GROUPS:
        if (aa & positive and bb & negative) or (aa & negative and bb & positive):
            return True
    return False


def same_event_title(a: str, b: str, min_similarity: float = 0.42) -> bool:
    aa, bb = content_tokens(a), content_tokens(b)
    return (
        len(aa & bb) >= 2
        and not titles_have_direction_conflict(a, b)
        and title_similarity(a, b) >= min_similarity
    )


def parse_timestamp(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing timestamp")
    candidates = [raw, raw.replace("Z", "+00:00")]
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
    raise ValueError(f"unparseable timestamp: {raw!r}")


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


def youtube_identity_terms(entity: str, ticker: str | None) -> list[str]:
    terms: list[str] = []
    if ticker:
        terms.append(str(ticker).lower())
    clean = clean_company_name(entity) or str(entity or "")
    for token in content_tokens(clean):
        if token not in COMPANY_SUFFIXES and token not in GENERIC_FIRST_WORDS:
            terms.append(token)
    return list(dict.fromkeys(terms))


def youtube_identity_aliases(entity: str, ticker: str | None) -> list[str]:
    aliases: list[str] = []
    if ticker:
        aliases.extend(YOUTUBE_TICKER_ZH_ALIASES.get(str(ticker).upper(), ()))
    aliases.extend(YOUTUBE_ENTITY_ZH_ALIASES.get(str(entity), ()))
    return list(dict.fromkeys(str(x) for x in aliases if str(x).strip()))


def youtube_query(
    entity: str,
    ticker: str | None,
    title: str,
    categories: Iterable[str] = (),
) -> tuple[str, list[str]]:
    entity_tokens = content_tokens(entity)
    extras = [
        token for token in re.findall(r"[A-Za-z0-9]+", title)
        if len(token) >= 4 and token.lower() not in STOPWORDS and token.lower() not in entity_tokens
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for token in extras:
        key = token.lower()
        if key not in seen:
            seen.add(key)
            unique.append(token)
        if len(unique) >= 3:
            break

    lead_parts: list[str] = []
    if ticker:
        lead_parts.append(str(ticker).upper())
        brand_tokens = [
            x for x in content_tokens(clean_company_name(entity) or entity)
            if x not in COMPANY_SUFFIXES and x not in GENERIC_FIRST_WORDS
        ]
        if brand_tokens and brand_tokens[0].lower() != str(ticker).lower():
            lead_parts.append(brand_tokens[0])
    else:
        lead_parts.append(str(entity).strip())

    aliases = youtube_identity_aliases(entity, ticker)
    if aliases:
        lead_parts.append(aliases[0])

    category_terms: list[str] = []
    for category in categories:
        category_terms.extend(YOUTUBE_CATEGORY_ZH_TERMS.get(str(category), ()))
    if category_terms:
        lead_parts.append(category_terms[0])

    query = " ".join([x for x in lead_parts if x] + unique[:2]).strip()
    return query[:100], unique


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import requests

import collect_external as legacy
from collect_external_safe import ResilientSession, TICKER_MIRROR
from news_rss import fetch_google_news
from scanner_common import (
    DATA,
    TICKER_DENYLIST,
    clean_company_name,
    ensure_dirs,
    normalize_text,
    write_json,
)

# Keep discovery queries intentionally broad. Google News RSS is the raw news
# tape; strict entity matching and event clustering provide precision later.
CATALYST_RSS_QUERIES: dict[str, str] = {
    "earnings_guidance": "earnings OR guidance OR outlook OR forecast OR revenue OR profit",
    "mna": "acquisition OR merger OR takeover OR buyout",
    "contract_order": "contract OR partnership OR major order OR supply deal",
    "regulatory_legal": "investigation OR probe OR lawsuit OR antitrust OR regulator",
    "pricing_capacity": '"price increase" OR "price hike" OR shortage OR capacity OR production',
    "ai_semis": 'semiconductor OR GPU OR HBM OR datacenter OR "AI chip"',
    "macro_rates": '"Federal Reserve" OR inflation OR "interest rates" OR tariffs OR Treasury',
    "crypto": "Bitcoin OR Ethereum OR crypto ETF",
}

# Brand names that differ materially from SEC conformed names.
BRAND_ALIASES: dict[str, str] = {
    "google": "GOOGL",
    "youtube": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "instagram": "META",
    "amazon": "AMZN",
    "aws": "AMZN",
    "amazon web services": "AMZN",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "micron": "MU",
    "broadcom": "AVGO",
    "marvell": "MRVL",
    "palantir": "PLTR",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "supermicro": "SMCI",
    "super micro": "SMCI",
    "sandisk": "SNDK",
    "western digital": "WDC",
    "seagate": "STX",
    "coreweave": "CRWV",
    "cloudflare": "NET",
    "datadog": "DDOG",
}

# Relevant non-U.S.-listed/private entities should still be discoverable when
# they can affect U.S. stocks and AI/semiconductor supply chains.
CUSTOM_ENTITIES: dict[str, dict] = {
    "sk hynix": {"name": "SK hynix", "ticker": None, "cik": None, "entity_type": "foreign_company"},
    "samsung electronics": {"name": "Samsung Electronics", "ticker": None, "cik": None, "entity_type": "foreign_company"},
    "foxconn": {"name": "Foxconn / Hon Hai", "ticker": None, "cik": None, "entity_type": "foreign_company"},
    "hon hai": {"name": "Foxconn / Hon Hai", "ticker": None, "cik": None, "entity_type": "foreign_company"},
    "openai": {"name": "OpenAI", "ticker": None, "cik": None, "entity_type": "private_company"},
    "anthropic": {"name": "Anthropic", "ticker": None, "cik": None, "entity_type": "private_company"},
    "deepseek": {"name": "DeepSeek", "ticker": None, "cik": None, "entity_type": "private_company"},
}

# Real tickers that are also common English/industry/crypto tokens. They are
# allowed through explicit company/brand names, but never by ticker token alone.
AMBIGUOUS_TICKERS = {
    "ON", "HBM", "SOL", "ARM", "AI", "ALL", "BIG", "NOW", "OPEN", "LOVE",
    "RUN", "APP", "CAR", "YOU", "SO", "AM", "BE", "IT", "ARE", "CAN",
} | TICKER_DENYLIST


class MirrorFirstSession(ResilientSession):
    """Avoid the known SEC edge delay on GitHub-hosted runners."""

    def get(self, url, *args, **kwargs):  # type: ignore[override]
        if url == legacy.SEC_TICKERS:
            mirror_kwargs = dict(kwargs)
            mirror_kwargs.pop("params", None)
            return requests.Session.get(self, TICKER_MIRROR, *args, **mirror_kwargs)
        return requests.Session.get(self, url, *args, **kwargs)


def build_session() -> requests.Session:
    s = MirrorFirstSession()
    s.headers.update({"User-Agent": "youtube-catalyst-scanner/1.0"})
    return s


def phrase_in_title(title: str, phrase: str) -> bool:
    hay = f" {normalize_text(title)} "
    needle = normalize_text(phrase)
    return bool(needle and f" {needle} " in hay)


def match_brand_or_custom(title: str, by_ticker: dict[str, dict]) -> dict | None:
    # Longer aliases first so "amazon web services" wins before "amazon".
    for alias, ticker in sorted(BRAND_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase_in_title(title, alias):
            item = by_ticker.get(ticker)
            if item:
                return item
    for alias, item in sorted(CUSTOM_ENTITIES.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase_in_title(title, alias):
            return dict(item)
    return None


def strict_match_company(
    title: str,
    by_ticker: dict[str, dict],
    first_word_index: dict[str, list[dict]],
) -> dict | None:
    # 1) Explicit, non-ambiguous ticker tokens are high precision.
    for token in re.findall(r"(?<![A-Z0-9])\$?([A-Z]{2,5})(?![A-Z0-9])", title):
        if token in AMBIGUOUS_TICKERS:
            continue
        item = by_ticker.get(token)
        if item:
            return item

    # 2) Curated brands/private/foreign entities.
    explicit = match_brand_or_custom(title, by_ticker)
    if explicit:
        return explicit

    # 3) Full SEC company alias only. Never accept the old single-first-word
    # shortcut (e.g. "Trade" -> Trade Desk, "German" -> German American Bank).
    normalized = f" {normalize_text(title)} "
    words = set(re.findall(r"[a-z0-9]+", normalized))
    best: tuple[int, dict] | None = None
    for word in words:
        for item in first_word_index.get(word, []):
            full_alias = clean_company_name(str(item.get("name") or ""))
            indexed_alias = normalize_text(str(item.get("alias") or ""))
            if not full_alias or indexed_alias != full_alias:
                continue
            if len(full_alias) < 4 or f" {full_alias} " not in normalized:
                continue
            if best is None or len(full_alias) > best[0]:
                clean_item = dict(item)
                clean_item.pop("alias", None)
                best = (len(full_alias), clean_item)
    return best[1] if best else None


def match_theme(title: str, category: str) -> dict | None:
    for entity, entity_type, rule in legacy.THEME_RULES:
        if not rule.search(title):
            continue
        if category == "macro_rates" and entity_type == "macro":
            return {"name": entity, "ticker": None, "cik": None, "entity_type": entity_type}
        if category == "crypto" and entity_type == "crypto":
            return {"name": entity, "ticker": None, "cik": None, "entity_type": entity_type}
        if category == "ai_semis" and entity == "AI Infrastructure":
            return {"name": entity, "ticker": None, "cik": None, "entity_type": entity_type}
    return None


def append_evidence(evidence: list[dict], base: dict, entity: dict) -> None:
    evidence.append(
        {
            **base,
            "entity": entity["name"],
            "ticker": entity.get("ticker"),
            "cik": entity.get("cik"),
            "entity_type": entity.get("entity_type", "company"),
        }
    )


def main() -> None:
    ensure_dirs()
    config = json.loads(open("config.json", encoding="utf-8").read())
    lookback_hours = int(config.get("news_lookback_hours", 24))
    when = "1d" if lookback_hours <= 24 else f"{max(1, round(lookback_hours / 24))}d"

    s = build_session()
    by_cik, by_ticker, first_word_index = legacy.load_sec_universe(s)
    print(f"Loaded {len(by_ticker)} ticker mappings from mirror-first reference data")

    evidence: list[dict] = []
    seen: set[str] = set()
    per_category: dict[str, int] = {}

    for category, query in CATALYST_RSS_QUERIES.items():
        try:
            articles = fetch_google_news(s, query, when=when)
        except Exception as exc:
            print(f"Google News RSS failed [{category}]: {exc}")
            per_category[category] = 0
            continue

        accepted = 0
        for article in articles:
            dedupe_key = str(article.get("guid") or article.get("link") or article.get("title") or "")
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            title = str(article.get("title") or "")
            base = {
                "source_type": "news_rss",
                "category": category,
                "title": title,
                "url": article.get("link"),
                "domain": article.get("domain"),
                "source_name": article.get("source_name"),
                "timestamp_utc": article.get("published_at_utc"),
            }

            # Macro/crypto searches should become macro/crypto events first;
            # otherwise words such as Trade/SOL can steal them as stock symbols.
            entity = match_theme(title, category) if category in {"macro_rates", "crypto"} else None
            if entity is None:
                entity = strict_match_company(title, by_ticker, first_word_index)
            if entity is None:
                entity = match_theme(title, category)
            if entity is None:
                continue

            append_evidence(evidence, base, entity)
            accepted += 1

        per_category[category] = accepted
        print(f"RSS [{category}]: {len(articles)} articles, {accepted} mapped evidence rows")
        time.sleep(0.25)

    sec_evidence: list[dict] = []
    if bool(config.get("enable_sec_live", False)):
        forms = [str(x) for x in config.get("official_catalyst_forms", [])]
        current_forms = [x for x in forms if x in {"8-K", "10-Q", "10-K", "6-K", "20-F"}]
        sec_evidence = legacy.collect_sec_current(s, by_cik, current_forms, lookback_hours)

    all_evidence = evidence + sec_evidence
    clusters = legacy.cluster_evidence(all_evidence)
    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback_hours,
        "primary_news_source": "google_news_rss",
        "raw_evidence_count": len(all_evidence),
        "news_evidence_count": len(evidence),
        "sec_evidence_count": len(sec_evidence),
        "event_cluster_count": len(clusters),
        "category_mapped_counts": per_category,
        "events": clusters,
    }
    write_json(DATA / "raw_events.json", payload)
    print(f"Wrote {len(clusters)} event clusters from {len(all_evidence)} evidence rows")

    if not clusters:
        raise RuntimeError("Scanner B discovery produced zero event clusters")


if __name__ == "__main__":
    main()

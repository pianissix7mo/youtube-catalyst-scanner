#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import requests

import collect_external as legacy
from collect_external_safe import ResilientSession, TICKER_MIRROR
from news_rss import fetch_google_news
from scanner_common import DATA, ensure_dirs, write_json

# Keep discovery queries intentionally broad. Google News RSS is the raw news
# tape; company/theme matching and event clustering provide the precision later.
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

BRAND_ALIASES: dict[str, str] = {
    "google": "GOOGL",
    "youtube": "GOOGL",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "facebook": "META",
    "instagram": "META",
    "aws": "AMZN",
    "amazon web services": "AMZN",
    "supermicro": "SMCI",
    "super micro": "SMCI",
}


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


def match_alias(title: str, by_ticker: dict[str, dict]) -> dict | None:
    lower = f" {title.lower()} "
    for alias, ticker in BRAND_ALIASES.items():
        if f" {alias} " in lower or alias in lower:
            item = by_ticker.get(ticker)
            if item:
                return item
    return None


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
            company = legacy.match_company(title, by_ticker, first_word_index) or match_alias(title, by_ticker)
            base = {
                "source_type": "news_rss",
                "category": category,
                "title": title,
                "url": article.get("link"),
                "domain": article.get("domain"),
                "source_name": article.get("source_name"),
                "timestamp_utc": article.get("published_at_utc"),
            }
            if company:
                evidence.append(
                    {
                        **base,
                        "entity": company["name"],
                        "ticker": company["ticker"],
                        "cik": company.get("cik"),
                        "entity_type": "company",
                    }
                )
                accepted += 1
                continue

            for entity, entity_type, rule in legacy.THEME_RULES:
                if rule.search(title):
                    evidence.append(
                        {
                            **base,
                            "entity": entity,
                            "ticker": None,
                            "cik": None,
                            "entity_type": entity_type,
                        }
                    )
                    accepted += 1
                    break
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

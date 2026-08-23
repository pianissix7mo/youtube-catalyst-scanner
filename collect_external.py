#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from scanner_common import (
    DATA,
    GENERIC_FIRST_WORDS,
    TICKER_DENYLIST,
    clean_company_name,
    ensure_dirs,
    normalize_text,
    parse_timestamp,
    title_similarity,
    write_json,
)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_CURRENT = "https://www.sec.gov/cgi-bin/browse-edgar"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

CATALYST_QUERIES: dict[str, str] = {
    "earnings_guidance": '(earnings OR guidance OR outlook OR forecast OR revenue OR profit) (shares OR stock OR company)',
    "mna": '(acquisition OR merger OR takeover OR buyout) (shares OR stock OR company)',
    "contract_order": '(contract OR order OR deal OR partnership) (shares OR stock OR company)',
    "regulatory_legal": '(investigation OR probe OR lawsuit OR antitrust OR regulator) (shares OR stock OR company)',
    "pricing_capacity": '("price increase" OR "price hike" OR shortage OR capacity OR production) (shares OR stock OR company)',
    "ai_semis": '(semiconductor OR chip OR datacenter OR "data center" OR GPU OR HBM OR AI) (shares OR stock OR company)',
    "macro_rates": '(Federal Reserve OR Treasury OR inflation OR "interest rates" OR tariff OR tariffs) (market OR stocks OR bonds)',
    "crypto": '(Bitcoin OR Ethereum OR crypto) (ETF OR market OR price OR institutional)',
}

THEME_RULES = [
    ("Federal Reserve / Rates", "macro", re.compile(r"\b(federal reserve|fed|interest rates?|rate cut|rate hike|treasury yields?)\b", re.I)),
    ("Inflation", "macro", re.compile(r"\b(inflation|cpi|ppi)\b", re.I)),
    ("Tariffs / Trade", "macro", re.compile(r"\b(tariff|tariffs|trade war|trade deal|export controls?)\b", re.I)),
    ("Bitcoin / Crypto", "crypto", re.compile(r"\b(bitcoin|btc|ethereum|ether|crypto)\b", re.I)),
    ("AI Infrastructure", "theme", re.compile(r"\b(ai infrastructure|data centers?|datacenters?|gpu|hbm|semiconductor|chips?)\b", re.I)),
]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": os.getenv(
            "SEC_USER_AGENT",
            "youtube-catalyst-scanner/1.0 contact:https://github.com/pianissix7mo/youtube-catalyst-scanner",
        )
    })
    return s


def load_sec_universe(s: requests.Session) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    r = s.get(SEC_TICKERS, timeout=30)
    r.raise_for_status()
    payload = r.json()
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    by_cik: dict[str, dict[str, Any]] = {}
    by_ticker: dict[str, dict[str, Any]] = {}
    first_word_index: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in rows:
        row = dict(zip(fields, raw))
        cik = str(row.get("cik") or row.get("cik_str") or "").lstrip("0")
        ticker = str(row.get("ticker") or "").upper().strip()
        name = str(row.get("name") or row.get("title") or "").strip()
        exchange = str(row.get("exchange") or "").strip()
        if not ticker or not name:
            continue
        item = {"cik": cik, "ticker": ticker, "name": name, "exchange": exchange}
        by_ticker[ticker] = item
        if cik:
            by_cik[cik] = item

        alias = clean_company_name(name)
        words = alias.split()
        if alias and len(alias) >= 5 and words:
            first_word_index[words[0]].append({**item, "alias": alias})
            if len(words[0]) >= 5 and words[0] not in GENERIC_FIRST_WORDS:
                first_word_index[words[0]].append({**item, "alias": words[0]})

    return by_cik, by_ticker, first_word_index


def match_company(title: str, by_ticker: dict[str, dict[str, Any]], first_word_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    upper_tokens = re.findall(r"(?<![A-Z0-9])\$?([A-Z]{2,5})(?![A-Z0-9])", title)
    for token in upper_tokens:
        if token in TICKER_DENYLIST:
            continue
        if token in by_ticker:
            return by_ticker[token]

    normalized = f" {normalize_text(title)} "
    words = set(re.findall(r"[a-z0-9]+", normalized))
    best: tuple[int, dict[str, Any]] | None = None
    for word in words:
        for item in first_word_index.get(word, []):
            alias = str(item.get("alias") or "")
            if len(alias) < 5:
                continue
            if f" {alias} " in normalized:
                score = len(alias)
                if best is None or score > best[0]:
                    best = (score, item)
    if best:
        item = dict(best[1])
        item.pop("alias", None)
        return item
    return None


def gdelt_articles(s: requests.Session, query: str, hours: int) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 250,
        "sort": "HybridRel",
        "timespan": f"{hours}h",
    }
    r = s.get(GDELT_DOC, params=params, timeout=45)
    r.raise_for_status()
    try:
        payload = r.json()
    except Exception:
        return []
    return list(payload.get("articles") or [])


def collect_gdelt(
    s: requests.Session,
    by_ticker: dict[str, dict[str, Any]],
    first_word_index: dict[str, list[dict[str, Any]]],
    lookback_hours: int,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for category, query in CATALYST_QUERIES.items():
        try:
            articles = gdelt_articles(s, query, lookback_hours)
        except Exception as exc:
            print(f"GDELT query failed [{category}]: {exc}")
            continue
        for article in articles:
            url = str(article.get("url") or "").strip()
            title = str(article.get("title") or "").strip()
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            company = match_company(title, by_ticker, first_word_index)
            ts = parse_timestamp(str(article.get("seendate") or article.get("date") or ""))
            domain = str(article.get("domain") or urlparse(url).netloc).lower().removeprefix("www.")
            base = {
                "source_type": "news",
                "category": category,
                "title": title,
                "url": url,
                "domain": domain,
                "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
                "language": article.get("language"),
                "source_country": article.get("sourcecountry"),
            }
            if company:
                evidence.append({
                    **base,
                    "entity": company["name"],
                    "ticker": company["ticker"],
                    "cik": company.get("cik"),
                    "entity_type": "company",
                })
                continue
            for entity, entity_type, rule in THEME_RULES:
                if rule.search(title):
                    evidence.append({
                        **base,
                        "entity": entity,
                        "ticker": None,
                        "cik": None,
                        "entity_type": entity_type,
                    })
                    break
        time.sleep(0.15)
    return evidence


def collect_sec_current(s: requests.Session, by_cik: dict[str, dict[str, Any]], forms: list[str], lookback_hours: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    output: list[dict[str, Any]] = []
    atom_ns = {"a": "http://www.w3.org/2005/Atom"}
    for form in forms:
        params = {
            "action": "getcurrent",
            "type": form,
            "owner": "include",
            "count": 100,
            "output": "atom",
        }
        try:
            r = s.get(SEC_CURRENT, params=params, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.text)
        except Exception as exc:
            print(f"SEC current feed failed [{form}]: {exc}")
            continue

        for entry in root.findall("a:entry", atom_ns):
            title = (entry.findtext("a:title", default="", namespaces=atom_ns) or "").strip()
            updated = (entry.findtext("a:updated", default="", namespaces=atom_ns) or "").strip()
            ts = parse_timestamp(updated)
            if ts < cutoff:
                continue
            link_el = entry.find("a:link", atom_ns)
            href = str(link_el.attrib.get("href") if link_el is not None else "")
            cik_match = re.search(r"\((0*\d{5,10})\)", title)
            if not cik_match:
                cik_match = re.search(r"CIK=(\d+)", href, re.I)
            cik = str(int(cik_match.group(1))) if cik_match else ""
            company = by_cik.get(cik)
            if not company:
                continue
            output.append({
                "source_type": "sec",
                "category": "sec_filing",
                "sec_form": form,
                "title": title,
                "url": href,
                "domain": "sec.gov",
                "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
                "entity": company["name"],
                "ticker": company["ticker"],
                "cik": company.get("cik"),
                "entity_type": "company",
            })
        time.sleep(0.15)
    return output


def cluster_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        grouped[str(row.get("entity") or "Unknown")].append(row)

    clusters: list[dict[str, Any]] = []
    for entity, rows in grouped.items():
        rows.sort(key=lambda x: str(x.get("timestamp_utc") or ""), reverse=True)
        entity_clusters: list[dict[str, Any]] = []
        for row in rows:
            if row.get("source_type") == "sec":
                key = f"sec:{row.get('sec_form')}"
                target = next((c for c in entity_clusters if c.get("cluster_key") == key), None)
                if target is None:
                    target = {"cluster_key": key, "representative_title": row["title"], "evidence": []}
                    entity_clusters.append(target)
                target["evidence"].append(row)
                continue

            target = None
            for cluster in entity_clusters:
                if str(cluster.get("cluster_key") or "").startswith("sec:"):
                    continue
                if title_similarity(row["title"], cluster["representative_title"]) >= 0.34:
                    target = cluster
                    break
            if target is None:
                target = {
                    "cluster_key": f"news:{len(entity_clusters)+1}",
                    "representative_title": row["title"],
                    "evidence": [],
                }
                entity_clusters.append(target)
            target["evidence"].append(row)

        for cluster in entity_clusters:
            ev = cluster["evidence"]
            if not ev:
                continue
            categories = sorted({str(x.get("category") or "") for x in ev if x.get("category")})
            forms = sorted({str(x.get("sec_form") or "") for x in ev if x.get("sec_form")})
            domains = sorted({str(x.get("domain") or "") for x in ev if x.get("domain")})
            latest = max(parse_timestamp(str(x.get("timestamp_utc") or "")) for x in ev)
            first = ev[0]
            clusters.append({
                "entity": entity,
                "ticker": first.get("ticker"),
                "cik": first.get("cik"),
                "entity_type": first.get("entity_type"),
                "representative_title": cluster["representative_title"],
                "latest_timestamp_utc": latest.astimezone(timezone.utc).isoformat(),
                "categories": categories,
                "sec_forms": forms,
                "source_domains": domains,
                "recent_evidence_count": len(ev),
                "evidence": ev[:20],
            })
    clusters.sort(key=lambda x: (x["recent_evidence_count"], x["latest_timestamp_utc"]), reverse=True)
    return clusters


def main() -> None:
    ensure_dirs()
    config = json.loads(open("config.json", encoding="utf-8").read())
    lookback_hours = int(config.get("news_lookback_hours", 24))
    forms = [str(x) for x in config.get("official_catalyst_forms", [])]
    # Current-feed queries are most useful for the high-signal subset.
    current_forms = [x for x in forms if x in {"8-K", "10-Q", "10-K", "6-K", "20-F"}]

    s = session()
    by_cik, by_ticker, first_word_index = load_sec_universe(s)
    print(f"Loaded {len(by_ticker)} SEC ticker mappings")

    news = collect_gdelt(s, by_ticker, first_word_index, lookback_hours)
    sec = collect_sec_current(s, by_cik, current_forms, lookback_hours)
    evidence = news + sec
    clusters = cluster_evidence(evidence)

    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback_hours,
        "raw_evidence_count": len(evidence),
        "news_evidence_count": len(news),
        "sec_evidence_count": len(sec),
        "event_cluster_count": len(clusters),
        "events": clusters,
    }
    write_json(DATA / "raw_events.json", payload)
    print(f"Wrote {len(clusters)} event clusters to data/raw_events.json")


if __name__ == "__main__":
    main()

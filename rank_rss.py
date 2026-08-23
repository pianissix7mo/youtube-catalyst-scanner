#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import rank_candidates as legacy_rank
from collect_rss import build_session
from news_rss import fetch_google_news
from scanner_common import (
    DATA,
    clean_company_name,
    ensure_dirs,
    load_config,
    median,
    parse_timestamp,
    ratio_score,
    write_json,
)

THEME_BASELINE_QUERY = {
    "Federal Reserve / Rates": '"Federal Reserve" OR "interest rates" OR "Treasury yields"',
    "Inflation": 'inflation OR CPI OR PPI',
    "Tariffs / Trade": 'tariffs OR "trade war" OR "export controls"',
    "Bitcoin / Crypto": 'Bitcoin OR Ethereum OR crypto',
    "AI Infrastructure": '"AI infrastructure" OR datacenter OR GPU OR HBM OR semiconductor',
}


def baseline_query(event: dict) -> str:
    entity = str(event.get("entity") or "").strip()
    if entity in THEME_BASELINE_QUERY:
        return THEME_BASELINE_QUERY[entity]
    ticker = str(event.get("ticker") or "").strip()
    clean = clean_company_name(entity) or entity
    if ticker:
        return f'"{clean}" OR "{ticker}"'
    return f'"{clean}"'


def make_bucket_counts(
    timestamps: list[datetime],
    now: datetime,
    bucket_hours: int,
    max_lookback_hours: int,
    feed_was_capped: bool,
) -> tuple[float, list[float]]:
    bucket = timedelta(hours=bucket_hours)
    current_start = now - bucket
    current = float(sum(1 for ts in timestamps if current_start <= ts <= now))

    if timestamps:
        oldest = min(timestamps)
    else:
        oldest = now
    requested_start = now - timedelta(hours=max_lookback_hours)
    coverage_start = max(requested_start, oldest) if feed_was_capped else requested_start

    history: list[float] = []
    end = current_start
    while end - bucket >= coverage_start:
        start = end - bucket
        history.append(float(sum(1 for ts in timestamps if start <= ts < end)))
        end = start
    return current, history


def rss_baseline(session: requests.Session, event: dict) -> dict:
    query = baseline_query(event)
    try:
        articles = fetch_google_news(session, query, when="30d", timeout=20)
    except Exception as exc:
        return {"source": "rss_error", "error": str(exc), "news_burst_score": None}

    timestamps = [
        parse_timestamp(str(row.get("published_at_utc") or ""))
        for row in articles
        if row.get("published_at_utc")
    ]
    now = datetime.now(timezone.utc)
    # Google News search feeds are generally capped near 100 results.  When the
    # feed is saturated, do not pretend the missing older period contains zeros.
    capped = len(articles) >= 95

    current_3h, prior_3h = make_bucket_counts(timestamps, now, 3, 72, capped)
    baseline_3h = median(prior_3h)
    short_ratio = (current_3h + 1.0) / (baseline_3h + 1.0)
    short_score = ratio_score(short_ratio)

    current_day, prior_days = make_bucket_counts(timestamps, now, 24, 24 * 30, capped)
    baseline_day = median(prior_days)
    long_ratio = (current_day + 1.0) / (baseline_day + 1.0)
    long_score = ratio_score(long_ratio) if prior_days else 0.0

    # If a high-volume entity saturates the feed and gives us little daily
    # history, emphasize the reliable short-horizon acceleration component.
    if prior_days:
        burst = 0.72 * short_score + 0.28 * long_score
    else:
        burst = short_score

    return {
        "source": "google_news_rss",
        "query": query,
        "article_count": len(articles),
        "feed_capped": capped,
        "current_3h": int(current_3h),
        "baseline_3h_median": round(baseline_3h, 3),
        "short_ratio": round(short_ratio, 3),
        "current_day": int(current_day),
        "baseline_day_median": round(baseline_day, 3),
        "long_ratio": round(long_ratio, 3),
        "short_history_buckets": len(prior_3h),
        "daily_history_buckets": len(prior_days),
        "news_burst_score": round(max(0.0, min(100.0, burst)), 1),
    }


def main() -> None:
    ensure_dirs()
    config = load_config()
    raw = json.loads((DATA / "raw_events.json").read_text(encoding="utf-8"))
    events = list(raw.get("events") or [])
    if not events:
        raise RuntimeError("No raw events available for ranking")

    history = legacy_rank.load_history()
    baseline_candidate_cap = int(config.get("baseline_candidate_cap", 30))
    provisional = sorted(
        events,
        key=lambda x: (
            int(x.get("recent_evidence_count") or 0),
            len(x.get("source_domains") or []),
            str(x.get("latest_timestamp_utc") or ""),
        ),
        reverse=True,
    )[:baseline_candidate_cap]

    session = build_session()
    baseline_cache: dict[str, dict] = {}
    scored: list[dict] = []
    for i, event in enumerate(provisional, 1):
        entity_key = str(event.get("entity") or "")
        print(f"RSS baseline {i}/{len(provisional)}: {entity_key}")
        if entity_key not in baseline_cache:
            baseline_cache[entity_key] = rss_baseline(session, event)
            time.sleep(0.2)
        baseline = baseline_cache[entity_key]
        if baseline.get("news_burst_score") is None:
            baseline = legacy_rank.local_fallback(event, history)
        scored.append(legacy_rank.score_event(event, baseline))

    scored.sort(key=lambda x: float(x.get("discovery_score") or 0), reverse=True)
    limit = min(
        int(config.get("candidate_limit_before_youtube", 20)),
        int(config.get("youtube_search_budget_per_run", 20)),
    )
    selected: list[dict] = []
    per_entity: defaultdict[str, int] = defaultdict(int)
    for row in scored:
        entity = str(row.get("entity") or "")
        if per_entity[entity] >= 2:
            continue
        selected.append(row)
        per_entity[entity] += 1
        if len(selected) >= limit:
            break

    if not selected:
        raise RuntimeError("Scanner B selected zero events before YouTube")

    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_source": "google_news_rss+rolling_local",
        "youtube_search_budget_per_run": int(config.get("youtube_search_budget_per_run", 20)),
        "candidate_count_before_youtube": len(selected),
        "events": selected,
    }
    write_json(DATA / "selected_events.json", payload)
    legacy_rank.append_history(history, events)
    print(f"Selected {len(selected)} events before YouTube enrichment (hard cap {limit})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scanner_common import (
    DATA,
    catalyst_quality_score,
    clamp,
    clean_company_name,
    ensure_dirs,
    freshness_score,
    load_config,
    median,
    parse_timestamp,
    ratio_score,
    source_diversity_score,
    stable_event_id,
    write_json,
    youtube_query,
)

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
BASELINE_HISTORY = DATA / "baseline_history.json"
QUALITY_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "cnbc.com", "ft.com", "sec.gov",
    "marketwatch.com", "barrons.com", "finance.yahoo.com", "investing.com",
}


def gdelt_query_for_entity(event: dict[str, Any]) -> str:
    entity = str(event.get("entity") or "").strip()
    if str(event.get("entity_type")) == "company":
        entity = clean_company_name(entity) or entity
    return f'"{entity}"'


def fetch_timeline(session: requests.Session, query: str, timespan: str) -> list[tuple[datetime, float]]:
    params = {
        "query": query,
        "mode": "TimelineVolRaw",
        "format": "json",
        "timespan": timespan,
        "timelinesmooth": 0,
    }
    r = session.get(GDELT_DOC, params=params, timeout=45)
    r.raise_for_status()
    try:
        payload = r.json()
    except Exception:
        return []

    timeline = payload.get("timeline") or []
    if isinstance(timeline, dict):
        timeline = [timeline]
    points: list[tuple[datetime, float]] = []
    for series in timeline:
        if not isinstance(series, dict):
            continue
        data = series.get("data") or []
        for point in data:
            if not isinstance(point, dict):
                continue
            raw_date = point.get("date") or point.get("datetime") or point.get("time")
            raw_value = point.get("value")
            if raw_value is None:
                raw_value = point.get("count")
            if raw_date is None or raw_value is None:
                continue
            try:
                dt = parse_timestamp(str(raw_date))
                value = float(raw_value)
            except Exception:
                continue
            points.append((dt, value))
    points.sort(key=lambda x: x[0])
    return points


def chunks(values: list[float], size: int) -> list[float]:
    return [sum(values[i:i + size]) for i in range(0, len(values) - size + 1, size)]


def timeline_baseline(session: requests.Session, event: dict[str, Any]) -> dict[str, Any]:
    query = gdelt_query_for_entity(event)
    short_points: list[tuple[datetime, float]] = []
    long_points: list[tuple[datetime, float]] = []
    short_error = None
    long_error = None
    try:
        short_points = fetch_timeline(session, query, "7d")
    except Exception as exc:
        short_error = str(exc)
    time.sleep(0.15)
    try:
        long_points = fetch_timeline(session, query, "30d")
    except Exception as exc:
        long_error = str(exc)
    time.sleep(0.15)

    short_values = [v for _, v in short_points]
    current_3h = sum(short_values[-3:]) if short_values else 0.0
    historical_short = chunks(short_values[:-3], 3) if len(short_values) > 6 else []
    baseline_3h = median(historical_short)
    short_ratio = (current_3h + 1.0) / (baseline_3h + 1.0)

    long_values = [v for _, v in long_points]
    current_day = long_values[-1] if long_values else 0.0
    baseline_day = median(long_values[:-1]) if len(long_values) > 1 else 0.0
    long_ratio = (current_day + 1.0) / (baseline_day + 1.0)

    short_score = ratio_score(short_ratio)
    long_score = ratio_score(long_ratio)
    burst = clamp(0.72 * short_score + 0.28 * long_score)

    return {
        "query": query,
        "current_3h": round(current_3h, 3),
        "baseline_3h_median": round(baseline_3h, 3),
        "short_ratio": round(short_ratio, 3),
        "current_day": round(current_day, 3),
        "baseline_day_median": round(baseline_day, 3),
        "long_ratio": round(long_ratio, 3),
        "news_burst_score": round(burst, 1),
        "short_point_count": len(short_points),
        "long_point_count": len(long_points),
        "short_error": short_error,
        "long_error": long_error,
    }


def load_history() -> dict[str, list[dict[str, Any]]]:
    if not BASELINE_HISTORY.exists():
        return {}
    try:
        payload = json.loads(BASELINE_HISTORY.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def local_fallback(event: dict[str, Any], history: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    key = str(event.get("entity") or "")
    rows = history.get(key) or []
    past = [float(x.get("recent_evidence_count") or 0) for x in rows[-30:]]
    base = median(past)
    current = float(event.get("recent_evidence_count") or 0)
    ratio = (current + 1.0) / (base + 1.0)
    return {
        "query": None,
        "current_3h": None,
        "baseline_3h_median": None,
        "short_ratio": None,
        "current_day": current,
        "baseline_day_median": base,
        "long_ratio": round(ratio, 3),
        "news_burst_score": round(ratio_score(ratio), 1),
        "source": "rolling_local_fallback",
    }


def evidence_quality(event: dict[str, Any]) -> float:
    domains = {str(x).lower() for x in event.get("source_domains") or []}
    high = len(domains & QUALITY_DOMAINS)
    official = 1 if "sec.gov" in domains or event.get("sec_forms") else 0
    return clamp(30 + 12 * min(high, 4) + 18 * official + 3 * min(len(domains), 6))


def append_history(history: dict[str, list[dict[str, Any]]], events: list[dict[str, Any]]) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    for event in events:
        key = str(event.get("entity") or "")
        history.setdefault(key, []).append({
            "timestamp_utc": stamp,
            "recent_evidence_count": int(event.get("recent_evidence_count") or 0),
            "source_domain_count": len(event.get("source_domains") or []),
        })
        history[key] = history[key][-180:]
    write_json(BASELINE_HISTORY, history)


def score_event(event: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    latest = parse_timestamp(str(event.get("latest_timestamp_utc") or ""))
    hours_old = max(0.0, (now - latest).total_seconds() / 3600.0)
    source_score = source_diversity_score(len(event.get("source_domains") or []))
    catalyst_score = catalyst_quality_score(event.get("categories") or [], event.get("sec_forms") or [])
    fresh_score = freshness_score(hours_old)
    evidence_score = evidence_quality(event)
    burst_score = float(baseline.get("news_burst_score") or 0.0)

    # SEC/official catalysts should not disappear merely because broad-news volume is still quiet.
    official_boost = 8.0 if event.get("sec_forms") else 0.0
    discovery = clamp(
        0.35 * burst_score
        + 0.20 * source_score
        + 0.20 * catalyst_score
        + 0.15 * fresh_score
        + 0.10 * evidence_score
        + official_boost
    )

    query, event_terms = youtube_query(
        str(event.get("entity") or ""),
        str(event.get("ticker") or "") or None,
        str(event.get("representative_title") or ""),
    )
    category = (event.get("categories") or ["unknown"])[0]
    event_id = stable_event_id(
        str(event.get("entity") or ""),
        str(event.get("representative_title") or ""),
        str(category),
    )

    return {
        "event_id": event_id,
        "scanner": "B",
        "entity": event.get("entity"),
        "ticker": event.get("ticker"),
        "entity_type": event.get("entity_type"),
        "event_title": event.get("representative_title"),
        "event_timestamp_utc": event.get("latest_timestamp_utc"),
        "categories": event.get("categories") or [],
        "sec_forms": event.get("sec_forms") or [],
        "discovery_score": round(discovery, 1),
        "news_burst_score": round(burst_score, 1),
        "source_diversity_score": round(source_score, 1),
        "catalyst_quality_score": round(catalyst_score, 1),
        "freshness_score": round(fresh_score, 1),
        "evidence_quality_score": round(evidence_score, 1),
        "youtube_query": query,
        "youtube_event_terms": event_terms,
        "baseline": baseline,
        "source_domains": event.get("source_domains") or [],
        "recent_evidence_count": event.get("recent_evidence_count") or 0,
        "evidence": event.get("evidence") or [],
    }


def main() -> None:
    ensure_dirs()
    config = load_config()
    raw = json.loads((DATA / "raw_events.json").read_text(encoding="utf-8"))
    events = list(raw.get("events") or [])
    history = load_history()

    # Baseline calls are external/GDELT calls, not YouTube quota. Keep a practical cap anyway.
    baseline_candidate_cap = int(config.get("baseline_candidate_cap", 36))
    provisional = sorted(
        events,
        key=lambda x: (int(x.get("recent_evidence_count") or 0), len(x.get("source_domains") or [])),
        reverse=True,
    )[:baseline_candidate_cap]

    s = requests.Session()
    s.headers.update({"User-Agent": "youtube-catalyst-scanner/1.0"})
    scored: list[dict[str, Any]] = []
    for i, event in enumerate(provisional, 1):
        print(f"Baseline {i}/{len(provisional)}: {event.get('entity')} — {event.get('representative_title')}")
        baseline = timeline_baseline(s, event)
        if not baseline.get("short_point_count") and not baseline.get("long_point_count"):
            baseline = local_fallback(event, history)
        scored.append(score_event(event, baseline))

    scored.sort(key=lambda x: float(x.get("discovery_score") or 0), reverse=True)

    limit = min(
        int(config.get("candidate_limit_before_youtube", 20)),
        int(config.get("youtube_search_budget_per_run", 20)),
    )
    selected: list[dict[str, Any]] = []
    per_entity: defaultdict[str, int] = defaultdict(int)
    for row in scored:
        entity = str(row.get("entity") or "")
        if per_entity[entity] >= 2:
            continue
        selected.append(row)
        per_entity[entity] += 1
        if len(selected) >= limit:
            break

    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "youtube_search_budget_per_run": int(config.get("youtube_search_budget_per_run", 20)),
        "candidate_count_before_youtube": len(selected),
        "events": selected,
    }
    write_json(DATA / "selected_events.json", payload)
    append_history(history, events)
    print(f"Selected {len(selected)} events before YouTube enrichment (hard cap {limit})")


if __name__ == "__main__":
    main()

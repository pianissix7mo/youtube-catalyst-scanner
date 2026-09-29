#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from scanner_common import (
    DATA,
    OUT,
    YOUTUBE_CATEGORY_ZH_TERMS,
    clamp,
    ensure_dirs,
    load_config,
    normalize_text,
    parse_timestamp,
    write_json,
    youtube_identity_aliases,
    youtube_identity_terms,
)

YT_API = "https://www.googleapis.com/youtube/v3"


def api_get(session: requests.Session, path: str, params: dict[str, Any]) -> dict[str, Any]:
    r = session.get(f"{YT_API}/{path}", params=params, timeout=30)
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise RuntimeError(f"YouTube API {r.status_code}: {detail}")
    return r.json()


def event_title_relevant(title: str, event: dict[str, Any]) -> bool:
    if not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(title or "")):
        return False

    hay = normalize_text(title)
    raw_lower = str(title or "").lower()
    ticker = str(event.get("ticker") or "") or None
    entity = str(event.get("entity") or "")

    identity_hit = any(
        normalize_text(term) and normalize_text(term) in hay
        for term in youtube_identity_terms(entity, ticker)
    )
    if not identity_hit:
        identity_hit = any(
            str(alias).lower() in raw_lower
            for alias in youtube_identity_aliases(entity, ticker)
        )
    if not identity_hit:
        return False

    concept_terms = [str(x) for x in event.get("youtube_event_terms") or []]
    for category in event.get("categories") or []:
        concept_terms.extend(YOUTUBE_CATEGORY_ZH_TERMS.get(str(category), ()))
    if not concept_terms:
        return True

    return any(
        (normalize_text(term) and normalize_text(term) in hay)
        or str(term).lower() in raw_lower
        for term in concept_terms
    )


def supply_gap_score(relevant_sample_size: int) -> float:
    if relevant_sample_size <= 2:
        return 100.0
    if relevant_sample_size <= 5:
        return 95.0
    if relevant_sample_size <= 10:
        return 85.0
    if relevant_sample_size <= 20:
        return 70.0
    if relevant_sample_size <= 35:
        return 50.0
    return 30.0


def enrich_event(
    session: requests.Session,
    event: dict[str, Any],
    api_key: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    lookback_days = max(1, int(config.get("youtube_lookback_days", 3)))
    sample_size = min(50, max(1, int(config.get("youtube_sample_size", 50))))
    published_after = (now - timedelta(days=lookback_days)).isoformat().replace("+00:00", "Z")
    query = str(event.get("youtube_query") or event.get("entity") or "").strip()

    search = api_get(session, "search", {
        "part": "id",
        "q": query,
        "type": "video",
        "order": "date",
        "maxResults": sample_size,
        "publishedAfter": published_after,
        "relevanceLanguage": str(config.get("youtube_relevance_language", "zh-Hans")),
        "key": api_key,
    })
    video_ids = [
        str(item.get("id", {}).get("videoId") or "")
        for item in (search.get("items") or [])
    ]
    video_ids = [x for x in video_ids if x]
    estimated_total = int((search.get("pageInfo") or {}).get("totalResults") or 0)

    if not video_ids:
        return {
            "status": "ok_no_videos",
            "query": query,
            "raw_sample_size": 0,
            "relevant_sample_size": 0,
            "estimated_total_results": estimated_total,
            "youtube_supply_gap_score": None,
            "median_views_per_day": 0,
            "small_channel_hit_rate": 0.0,
            "sample_videos": [],
        }

    videos = api_get(session, "videos", {
        "part": "statistics,snippet",
        "id": ",".join(video_ids),
        "key": api_key,
    })

    parsed: list[dict[str, Any]] = []
    for item in videos.get("items") or []:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        title = str(snippet.get("title") or "")
        if not event_title_relevant(title, event):
            continue
        try:
            published = parse_timestamp(str(snippet.get("publishedAt") or ""))
            views = int(stats.get("viewCount") or 0)
        except Exception:
            continue
        age_days = max(0.125, (now - published).total_seconds() / 86400.0)
        parsed.append({
            "video_id": item.get("id"),
            "title": title,
            "channel_id": str(snippet.get("channelId") or ""),
            "published_at": published.isoformat(),
            "views": views,
            "views_per_day": int(round(views / age_days)),
        })

    channel_ids = list(dict.fromkeys([x["channel_id"] for x in parsed if x.get("channel_id")]))[:50]
    subscribers: dict[str, int | None] = {}
    if channel_ids:
        channels = api_get(session, "channels", {
            "part": "statistics",
            "id": ",".join(channel_ids),
            "maxResults": 50,
            "key": api_key,
        })
        for item in channels.get("items") or []:
            stats = item.get("statistics") or {}
            if stats.get("hiddenSubscriberCount"):
                subscribers[str(item.get("id"))] = None
            else:
                try:
                    subscribers[str(item.get("id"))] = int(stats.get("subscriberCount") or 0)
                except Exception:
                    subscribers[str(item.get("id"))] = None

    small_max = int(config.get("small_channel_max_subscribers", 50000))
    hit_vpd = int(config.get("small_channel_hit_views_per_day", 1000))
    small_count = 0
    small_hits = 0
    for row in parsed:
        subs = subscribers.get(row["channel_id"])
        row["channel_subscribers"] = subs
        if subs is not None and subs < small_max:
            small_count += 1
            if int(row["views_per_day"]) >= hit_vpd:
                small_hits += 1

    vpds = [int(x["views_per_day"]) for x in parsed]
    parsed.sort(key=lambda x: int(x["views_per_day"]), reverse=True)
    relevant_count = len(parsed)
    if relevant_count == 0:
        return {
            "status": "ok_no_relevant_videos",
            "query": query,
            "raw_sample_size": len(video_ids),
            "relevant_sample_size": 0,
            "estimated_total_results": estimated_total,
            "youtube_supply_gap_score": None,
            "median_views_per_day": 0,
            "small_channel_sample_size": 0,
            "small_channel_hit_rate": 0.0,
            "sample_videos": [],
        }

    return {
        "status": "ok",
        "query": query,
        "raw_sample_size": len(video_ids),
        "relevant_sample_size": relevant_count,
        "estimated_total_results": estimated_total,
        "youtube_supply_gap_score": supply_gap_score(relevant_count),
        "median_views_per_day": int(statistics.median(vpds)) if vpds else 0,
        "small_channel_sample_size": small_count,
        "small_channel_hit_rate": round(100.0 * small_hits / small_count, 1) if small_count else 0.0,
        "sample_videos": parsed[:10],
    }


def validate_selected_payload(
    selected: dict[str, Any],
    candidates: dict[str, Any],
    config: dict[str, Any],
) -> None:
    source_stamp = str(selected.get("source_candidates_generated_at_utc") or "")
    candidate_stamp = str(candidates.get("generated_at_utc") or "")
    if not source_stamp or source_stamp != candidate_stamp:
        raise RuntimeError(
            "selected_events.json provenance does not match current judge_candidates.json"
        )

    source_time = parse_timestamp(source_stamp)
    age_hours = (datetime.now(timezone.utc) - source_time).total_seconds() / 3600.0
    max_age_hours = float(config.get("selected_events_max_age_hours", 24))
    if age_hours < -0.25:
        raise RuntimeError("selected_events.json source timestamp is unexpectedly in the future")
    if age_hours > max_age_hours:
        raise RuntimeError(
            f"selected_events.json is stale ({age_hours:.1f}h > {max_age_hours:.1f}h); "
            "run fresh discovery/Judge B first"
        )


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Scanner B — Catalyst Opportunities",
        "",
        f"Generated: **{payload['generated_at_utc']}**",
        "",
        f"YouTube search calls used: **{payload['youtube_search_calls_used']} / {payload['youtube_search_budget_per_run']}**",
        "",
        "| # | Event | Ticker | Judge | Discovery | Burst | Sources | YT gap | B final |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(payload.get("events") or [], 1):
        yt = row.get("youtube_metrics") or {}
        lines.append(
            f"| {i} | {row.get('event_title','')} | {row.get('ticker') or '—'} | "
            f"{row.get('judge_score','—')} | {row.get('discovery_score','—')} | {row.get('news_burst_score','—')} | "
            f"{row.get('source_diversity_score','—')} | {yt.get('youtube_supply_gap_score','—')} | "
            f"{row.get('scanner_b_score','—')} |"
        )
    (OUT / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    config = load_config()
    selected = json.loads((DATA / "selected_events.json").read_text(encoding="utf-8"))
    candidates = json.loads((DATA / "judge_candidates.json").read_text(encoding="utf-8"))
    validate_selected_payload(selected, candidates, config)
    events = list(selected.get("events") or [])
    budget = int(config.get("youtube_search_budget_per_run", 20))
    hard_limit = min(budget, int(config.get("candidate_limit_before_youtube", 20)), len(events))
    events = events[:hard_limit]

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    session = requests.Session()
    enriched: list[dict[str, Any]] = []
    search_calls = 0

    for event in events:
        row = dict(event)
        if not api_key:
            yt = {
                "status": "skipped_no_api_key",
                "youtube_supply_gap_score": None,
                "query": row.get("youtube_query"),
            }
        else:
            if search_calls >= budget:
                yt = {"status": "skipped_budget_exhausted", "youtube_supply_gap_score": None}
            else:
                search_calls += 1
                try:
                    yt = enrich_event(session, row, api_key, config)
                except (requests.RequestException, RuntimeError, ValueError) as exc:
                    yt = {
                        "status": "error",
                        "youtube_supply_gap_score": None,
                        "query": row.get("youtube_query"),
                        "error": str(exc)[:500],
                    }
        row["youtube_metrics"] = yt
        gap = yt.get("youtube_supply_gap_score")
        discovery = float(row.get("discovery_score") or 0.0)
        judge = row.get("judge_score")
        judge_value = float(judge) if judge is not None else None
        if judge_value is not None:
            row["scanner_b_score"] = round(
                clamp(0.65 * judge_value + 0.35 * discovery)
                if gap is None
                else clamp(0.50 * judge_value + 0.30 * discovery + 0.20 * float(gap)),
                1,
            )
        else:
            row["scanner_b_score"] = round(
                discovery if gap is None else clamp(0.80 * discovery + 0.20 * float(gap)),
                1,
            )
        enriched.append(row)

    enriched.sort(key=lambda x: float(x.get("scanner_b_score") or 0), reverse=True)
    payload = {
        "scanner": "B",
        "judge_version": selected.get("judge_version"),
        "source_candidates_generated_at_utc": selected.get("source_candidates_generated_at_utc"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "youtube_search_budget_per_run": budget,
        "youtube_search_calls_used": search_calls,
        "events": enriched,
    }
    write_json(OUT / "latest.json", payload)
    write_markdown(payload)
    print(f"Wrote output/latest.json; YouTube search calls used: {search_calls}/{budget}")


if __name__ == "__main__":
    main()

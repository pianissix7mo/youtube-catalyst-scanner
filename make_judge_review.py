#!/usr/bin/env python3
from __future__ import annotations

import json

from scanner_common import DATA

OUT = DATA / "judge_review.jsonl"


def compact_evidence(evidence: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in evidence[:4]:
        rows.append({
            "title": item.get("title"),
            "domain": item.get("domain"),
            "source_name": item.get("source_name"),
            "timestamp_utc": item.get("timestamp_utc"),
            "source_type": item.get("source_type"),
            "category": item.get("category"),
            "sec_form": item.get("sec_form"),
        })
    return rows


def compact_baseline(baseline: dict) -> dict:
    keys = (
        "source",
        "current_3h",
        "baseline_3h_median",
        "short_ratio",
        "current_day",
        "baseline_day_median",
        "long_ratio",
        "news_burst_score",
        "feed_capped",
    )
    return {key: baseline.get(key) for key in keys if key in baseline}


def main() -> None:
    payload = json.loads((DATA / "judge_candidates.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    for idx, event in enumerate(payload.get("events") or [], 1):
        row = {
            "candidate_rank": idx,
            "event_id": event.get("event_id"),
            "entity": event.get("entity"),
            "ticker": event.get("ticker"),
            "entity_type": event.get("entity_type"),
            "event_title": event.get("event_title"),
            "event_timestamp_utc": event.get("event_timestamp_utc"),
            "categories": event.get("categories"),
            "discovery_score": event.get("discovery_score"),
            "news_burst_score": event.get("news_burst_score"),
            "source_diversity_score": event.get("source_diversity_score"),
            "catalyst_quality_score": event.get("catalyst_quality_score"),
            "freshness_score": event.get("freshness_score"),
            "evidence_quality_score": event.get("evidence_quality_score"),
            "baseline": compact_baseline(dict(event.get("baseline") or {})),
            "source_domains": list(event.get("source_domains") or [])[:6],
            "recent_evidence_count": event.get("recent_evidence_count"),
            "youtube_query": event.get("youtube_query"),
            "youtube_event_terms": event.get("youtube_event_terms"),
            "evidence_sample": compact_evidence(list(event.get("evidence") or [])),
        }
        lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    OUT.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"Wrote {len(lines)} compact Judge B review rows to {OUT}")


if __name__ == "__main__":
    main()

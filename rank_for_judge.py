#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone

import rank_candidates as legacy_rank
from rank_rss import rss_baseline
from collect_rss import build_session
from scanner_common import DATA, ensure_dirs, load_config, write_json


def cheap_priority(event: dict) -> tuple:
    """Cheap pre-rank used before any live baseline request.

    We intentionally avoid semantic judgement here. The goal is only to surface
    broad, fresh, well-supported candidates while preventing one entity/theme
    from consuming the entire Judge pool.
    """
    official = 1 if event.get("sec_forms") else 0
    evidence_count = int(event.get("recent_evidence_count") or 0)
    source_count = len(event.get("source_domains") or [])
    category_count = len(event.get("categories") or [])
    latest = str(event.get("latest_timestamp_utc") or "")
    return (official, evidence_count, source_count, category_count, latest)


def build_diverse_pool(events: list[dict], candidate_cap: int, per_entity_cap: int) -> list[dict]:
    ranked = sorted(events, key=cheap_priority, reverse=True)
    selected: list[dict] = []
    per_entity: defaultdict[str, int] = defaultdict(int)

    for event in ranked:
        entity = str(event.get("entity") or "Unknown")
        if per_entity[entity] >= per_entity_cap:
            continue
        selected.append(event)
        per_entity[entity] += 1
        if len(selected) >= candidate_cap:
            break

    return selected


def main() -> None:
    ensure_dirs()
    config = load_config()
    raw = json.loads((DATA / "raw_events.json").read_text(encoding="utf-8"))
    events = list(raw.get("events") or [])
    if not events:
        raise RuntimeError("No raw events available for Judge B preparation")

    history = legacy_rank.load_history()
    candidate_cap = int(config.get("judge_candidate_cap", 200))
    per_entity_cap = int(config.get("judge_pool_max_events_per_entity", 6))
    live_baseline_entity_cap = int(config.get("judge_live_baseline_entity_cap", 80))

    provisional = build_diverse_pool(events, candidate_cap, per_entity_cap)
    if not provisional:
        raise RuntimeError("Diversity-aware Judge B preselection produced zero candidates")

    session = build_session()
    baseline_cache: dict[str, dict] = {}
    live_baseline_entities: set[str] = set()
    live_baseline_events = 0
    fallback_events = 0
    scored: list[dict] = []

    for i, event in enumerate(provisional, 1):
        entity_key = str(event.get("entity") or "")
        print(f"Judge candidate {i}/{len(provisional)}: {entity_key}")

        if entity_key in baseline_cache:
            baseline = baseline_cache[entity_key]
        elif len(live_baseline_entities) < live_baseline_entity_cap:
            print(f"  live RSS baseline: {entity_key}")
            baseline = rss_baseline(session, event)
            live_baseline_entities.add(entity_key)
            if baseline.get("news_burst_score") is None:
                baseline = legacy_rank.local_fallback(event, history)
            baseline_cache[entity_key] = baseline
            time.sleep(0.2)
        else:
            baseline = legacy_rank.local_fallback(event, history)
            baseline_cache[entity_key] = baseline

        if str(baseline.get("source") or "") == "rolling_local_fallback":
            fallback_events += 1
        else:
            live_baseline_events += 1

        scored.append(legacy_rank.score_event(event, baseline))

    scored.sort(key=lambda x: float(x.get("discovery_score") or 0), reverse=True)

    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "judge_version": "B_V1",
        "raw_event_count": len(events),
        "judge_candidate_cap": candidate_cap,
        "judge_pool_max_events_per_entity": per_entity_cap,
        "judge_live_baseline_entity_cap": live_baseline_entity_cap,
        "live_baseline_entity_count": len(live_baseline_entities),
        "live_baseline_event_count": live_baseline_events,
        "local_fallback_event_count": fallback_events,
        "candidate_count": len(scored),
        "youtube_search_budget_per_run": int(config.get("youtube_search_budget_per_run", 20)),
        "events": scored,
    }
    write_json(DATA / "judge_candidates.json", payload)
    legacy_rank.append_history(history, events)
    print(
        f"Wrote {len(scored)} pre-YouTube candidates for Judge B "
        f"from {len(events)} raw events; max {per_entity_cap} per entity/theme; "
        f"live baseline entities {len(live_baseline_entities)}/{live_baseline_entity_cap}"
    )


if __name__ == "__main__":
    main()

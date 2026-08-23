#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import rank_candidates as legacy_rank
from rank_rss import rss_baseline
from collect_rss import build_session
from scanner_common import DATA, ensure_dirs, load_config, write_json


def main() -> None:
    ensure_dirs()
    config = load_config()
    raw = json.loads((DATA / "raw_events.json").read_text(encoding="utf-8"))
    events = list(raw.get("events") or [])
    if not events:
        raise RuntimeError("No raw events available for Judge B preparation")

    history = legacy_rank.load_history()
    candidate_cap = int(config.get("judge_candidate_cap", config.get("baseline_candidate_cap", 30)))
    provisional = sorted(
        events,
        key=lambda x: (
            int(x.get("recent_evidence_count") or 0),
            len(x.get("source_domains") or []),
            str(x.get("latest_timestamp_utc") or ""),
        ),
        reverse=True,
    )[:candidate_cap]

    session = build_session()
    baseline_cache: dict[str, dict] = {}
    scored: list[dict] = []
    for i, event in enumerate(provisional, 1):
        entity_key = str(event.get("entity") or "")
        print(f"Judge baseline {i}/{len(provisional)}: {entity_key}")
        if entity_key not in baseline_cache:
            baseline_cache[entity_key] = rss_baseline(session, event)
            time.sleep(0.2)
        baseline = baseline_cache[entity_key]
        if baseline.get("news_burst_score") is None:
            baseline = legacy_rank.local_fallback(event, history)
        scored.append(legacy_rank.score_event(event, baseline))

    scored.sort(key=lambda x: float(x.get("discovery_score") or 0), reverse=True)

    payload = {
        "scanner": "B",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "judge_version": "B_V1",
        "candidate_count": len(scored),
        "youtube_search_budget_per_run": int(config.get("youtube_search_budget_per_run", 20)),
        "events": scored,
    }
    write_json(DATA / "judge_candidates.json", payload)
    legacy_rank.append_history(history, events)
    print(f"Wrote {len(scored)} pre-YouTube candidates for Judge B")


if __name__ == "__main__":
    main()

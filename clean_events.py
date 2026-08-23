#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter

from scanner_common import DATA, write_json

BLOCKED_DOMAINS = {
    "indexbox.io",
    "tradingview.com",
}

NOISE_TITLE_PATTERNS = [
    re.compile(r"\bmarket to reach\b.*\bby 20\d{2}\b", re.I),
    re.compile(r"\bmarket driven by\b.*\bthrough 20\d{2}\b", re.I),
    re.compile(r"\bCAGR\b.*\b20\d{2}\b", re.I),
    re.compile(r"\bprice to sales forward of\b", re.I),
    re.compile(r"\bprice to book forward of\b", re.I),
    re.compile(r"\benterprise value to EBIT forward of\b", re.I),
    re.compile(r"\bdiluted shares outstanding of\b", re.I),
    re.compile(r"\bforward of .*\b(?:NASDAQ|OTC|BOATS):", re.I),
    re.compile(r"\bnews and statistics\b", re.I),
]


def is_noise_title(title: str) -> bool:
    return any(pattern.search(title or "") for pattern in NOISE_TITLE_PATTERNS)


def should_drop(event: dict) -> tuple[bool, str | None]:
    title = str(event.get("representative_title") or "")
    evidence = list(event.get("evidence") or [])
    domains = {str(x.get("domain") or "").lower() for x in evidence if x.get("domain")}

    if is_noise_title(title):
        return True, "seo_or_metric_title"
    if domains and domains.issubset(BLOCKED_DOMAINS):
        return True, "blocked_low_value_source"

    ticker = str(event.get("ticker") or "").upper()
    # ON is both a ticker and a common preposition. Require case-sensitive brand
    # evidence instead of normalized phrase coincidence ("... on semiconductor").
    if ticker == "ON" and not re.search(r"(?:\$ON\b|\bON Semiconductor\b)", title):
        return True, "ambiguous_on_ticker"

    # Nasdaq exchange labels can accidentally map to Nasdaq Inc. (NDAQ).
    if ticker == "NDAQ" and "NASDAQ:" in title.upper() and not re.search(r"\bNasdaq,? Inc\b", title, re.I):
        return True, "exchange_label_not_company"

    return False, None


def correct_entity(event: dict) -> dict:
    row = dict(event)
    title = str(row.get("representative_title") or "")
    ticker = str(row.get("ticker") or "").upper()

    # Paramount Skydance (PSKY) and Paramount Group (PGRE) share the word
    # "Paramount". Media/merger stories should map to the current media company.
    if ticker == "PGRE" and "paramount" in title.lower() and "paramount group" not in title.lower():
        row["entity"] = "Paramount Skydance Corporation"
        row["ticker"] = "PSKY"
        row["cik"] = "2041610"
        row["entity_type"] = "company"
        row["entity_correction"] = "paramount_media_disambiguation"

    return row


def main() -> None:
    path = DATA / "raw_events.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = list(payload.get("events") or [])

    kept: list[dict] = []
    reasons: Counter[str] = Counter()
    for event in events:
        drop, reason = should_drop(event)
        if drop:
            reasons[str(reason or "unknown")] += 1
            continue
        kept.append(correct_entity(event))

    payload["pre_clean_event_cluster_count"] = len(events)
    payload["event_cluster_count"] = len(kept)
    payload["cleaning_dropped_count"] = len(events) - len(kept)
    payload["cleaning_drop_reasons"] = dict(reasons)
    payload["events"] = kept
    write_json(path, payload)
    print(f"Event cleanup: kept {len(kept)}/{len(events)}; dropped {len(events)-len(kept)} {dict(reasons)}")

    if not kept:
        raise RuntimeError("All Scanner B event clusters were removed by cleanup")


if __name__ == "__main__":
    main()

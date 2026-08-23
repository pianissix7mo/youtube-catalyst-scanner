# youtube-catalyst-scanner

Scanner B for a Chinese-language U.S.-stock / investing YouTube topic radar.

Scanner B is intentionally independent from `youtube-trends-scanner` (Scanner A). Scanner A measures crowd/search attention; Scanner B looks for catalyst and information-flow breakouts outside YouTube, then checks whether YouTube supply is still light.

## Core idea

**External catalyst discovery first; YouTube only as a capped supply-gap check.**

Scanner B asks:

> What market-relevant event is accelerating outside YouTube before YouTube content supply becomes crowded?

It does **not** take Scanner A candidates as its discovery input. The future Ensemble layer will merge A and B only after both scanners finish independent discovery.

## Hard design rules

- Scanner A stays unchanged.
- Scanner B discovers events independently.
- Scanner B may scan broad external data, but only the final **Top 20** may call YouTube `search.list`.
- One formal Scanner B run is hard-capped at **20 YouTube search calls**.
- The future Ensemble layer must not spend extra YouTube search calls.
- A normal A+B day is therefore designed around **20 + 20 = 40** YouTube searches, leaving room for a full retry and safety margin under the 100-search daily operating budget.

## Data sources

### GDELT DOC API

Used for broad recent-news discovery and historical news-volume baselines.

V1 searches several catalyst families:

- earnings / guidance / outlook
- M&A
- contracts / orders / partnerships
- regulatory / legal
- pricing / capacity / production
- AI / semiconductors / datacenters
- rates / inflation / tariffs
- crypto / ETF themes

### SEC EDGAR

Used as an official-source catalyst stream. V1 scans recent high-signal forms including:

- 8-K
- 10-Q
- 10-K
- 6-K
- 20-F

SEC ticker/CIK mappings are loaded from the SEC's published ticker file at runtime.

### YouTube Data API

Used **after** external discovery and ranking. Each selected event receives at most one `search.list` query, followed by low-cost video/channel detail calls.

## Baseline logic

Scanner B uses two external-news baselines:

1. **7-day hourly baseline** — recent 3-hour burst versus historical 3-hour buckets.
2. **30-day daily baseline** — current daily coverage versus the prior daily median.

Burst scoring is robust and ratio-based:

- 1x normal -> 0 burst points
- 2x -> 25
- 4x -> 50
- 8x -> 75
- 16x -> 100

The repository also stores `data/baseline_history.json` after each run as a rolling proprietary fallback/history layer.

## Event scoring before YouTube

`discovery_score` is computed before any YouTube call:

- 35% news burst
- 20% source diversity
- 20% catalyst quality
- 15% freshness
- 10% evidence quality
- official SEC catalysts receive a small additional boost

This keeps Scanner B's actual discovery signal independent from YouTube.

## YouTube supply gap

Only the final Top 20 external events are enriched on YouTube.

The scanner measures:

- recent relevant video sample size
- median views/day
- small-channel hit rate
- channel subscriber size
- content supply gap

`scanner_b_score` currently uses:

- 80% external `discovery_score`
- 20% YouTube supply-gap score

Both scores remain separately available so the future Ensemble can choose whether to use the pure external signal or the content-opportunity version.

## Pipeline

```text
GDELT news + SEC filings
          |
          v
company/theme identification
          |
          v
event clustering
          |
          v
7d hourly + 30d daily baseline
          |
          v
external discovery scoring
          |
          v
HARD GATE: Top 20
          |
          v
YouTube supply-gap enrichment
          |
          v
output/latest.json + latest.md
```

## Files

- `collect_external.py` — GDELT + SEC discovery, entity matching, event clustering
- `rank_candidates.py` — historical baseline, scoring, Top-20 pre-YouTube gate
- `youtube_enrich.py` — quota-capped YouTube supply-gap measurement
- `scanner_common.py` — normalization and scoring utilities
- `config.json` — hard limits and thresholds
- `docs/DESIGN_V1.md` — design contract
- `.github/workflows/scanner_b.yml` — automated run

Generated state:

- `data/raw_events.json`
- `data/selected_events.json`
- `data/baseline_history.json`
- `output/latest.json`
- `output/latest.md`

## GitHub Actions schedule

The workflow runs daily at approximately **05:10 America/Toronto** using two UTC cron entries plus a Toronto local-hour guard, so daylight-saving changes do not shift the intended local run hour.

Code/config changes also trigger a test run automatically.

## Repository secrets

### `YOUTUBE_API_KEY`

Recommended. If it is absent, Scanner B still completes external discovery/baseline/ranking and writes output; YouTube enrichment is marked as skipped rather than failing the workflow.

### `SEC_USER_AGENT`

Optional. A safe repository-identifying default is supplied by the workflow. A custom SEC-compliant contact string can be added later if desired.

## Standardized event output

Each selected event exposes fields intended for later A+B merging:

- `event_id`
- `scanner = B`
- `entity`
- `ticker`
- `event_title`
- `event_timestamp_utc`
- `discovery_score`
- `news_burst_score`
- `source_diversity_score`
- `catalyst_quality_score`
- `freshness_score`
- `evidence_quality_score`
- `youtube_metrics`
- `scanner_b_score`
- `evidence[]`

## Next architecture step

Once Scanner B is stable, build the Ensemble layer:

```text
Scanner A independent output
            \
             -> normalize / merge / cross-check -> fixed Semantic Judge -> one email
            /
Scanner B independent output
```

The Semantic Judge will be versioned and rule-based rather than changing criteria day to day.

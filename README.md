# youtube-catalyst-scanner

Scanner B for a Chinese-language U.S.-stock / investing YouTube topic radar.

Scanner B is intentionally independent from `youtube-trends-scanner` (Scanner A). Scanner A measures crowd/search attention; Scanner B looks for catalyst and information-flow breakouts outside YouTube, then checks whether YouTube supply is still light.

## Core idea

**External catalyst discovery first; fixed semantic quality gate second; YouTube only after the gate.**

Scanner B asks:

> What market-relevant event is accelerating outside YouTube before YouTube content supply becomes crowded?

It does **not** take Scanner A candidates as its discovery input. The future Ensemble layer will merge A and B only after both scanners finish independent discovery.

## Hard design rules

- Scanner A stays unchanged.
- Scanner B discovers events independently.
- Scanner B may scan broad external data, but only events approved by **Judge B V1** may call YouTube `search.list`.
- Judge B selects at most **20** events and may select fewer.
- One formal Scanner B enrichment run is hard-capped at **20 YouTube search calls**.
- The future Ensemble layer must not spend extra YouTube search calls.
- A normal A+B day is therefore designed around **20 + 20 = 40** YouTube searches, leaving room for a full retry and safety margin under the 100-search daily operating budget.

## Data sources

### News discovery

The active V1 discovery path uses resilient Google News RSS queries across catalyst families such as:

- earnings / guidance / outlook
- M&A
- contracts / orders / partnerships
- regulatory / legal
- pricing / capacity / production
- AI / semiconductors / datacenters
- rates / inflation / tariffs
- crypto / ETF themes

The repository also contains GDELT-based code as a fallback/reference path.

### SEC EDGAR

SEC ticker/CIK mappings are used for company identity. Optional live official filing support is retained for high-signal forms such as 8-K, 10-Q, 10-K, 6-K, and 20-F.

### YouTube Data API

Used **only after Judge B approval**. Each selected event receives at most one `search.list` query, followed by low-cost video/channel detail calls.

## Baseline logic

Scanner B uses external-news baselines before any YouTube call:

1. recent 3-hour burst versus historical 3-hour buckets
2. current daily coverage versus the prior daily median

Burst scoring is ratio-based:

- 1x normal -> 0 burst points
- 2x -> 25
- 4x -> 50
- 8x -> 75
- 16x -> 100

The repository also stores `data/baseline_history.json` as a rolling proprietary fallback/history layer.

## Python discovery score

`discovery_score` is computed before any YouTube call from:

- news burst
- source diversity
- catalyst type
- freshness
- evidence quality

This is a quantitative discovery signal, **not permission to spend YouTube quota**.

## Judge B V1 — semantic pre-YouTube gate

The fixed rubric is versioned in `docs/JUDGE_B_V1.md`.

Judge B reviews every candidate and scores:

- Entity Accuracy — hard pass/fail
- Catalyst Reality — 25
- Investor Materiality — 25
- Novelty — 15
- Evidence Quality — 15
- Content Potential — 20

Hard rejection applies to entity mismatches, non-catalysts/evergreen noise, very low investor materiality, very weak evidence, or total `judge_score < 58`.

The rules must not drift day to day. Any change requires a new committed judge version.

Judge B runs as a ChatGPT scheduled task after the discovery files are committed. It writes `data/selected_events.json`. It does not call YouTube and it does not send email.

## YouTube supply gap

Only Judge-approved events are enriched on YouTube.

The scanner measures:

- recent relevant video sample size
- median views/day
- small-channel hit rate
- channel subscriber size
- content supply gap

`scanner_b_score` now gives Judge B the largest role:

- 50% Judge B score
- 30% external discovery score
- 20% YouTube supply-gap score

If YouTube data is unavailable, the fallback blend is:

- 65% Judge B score
- 35% external discovery score

This prevents `no videos found` from rescuing a low-quality event.

## Pipeline

```text
external news / filings
          |
          v
entity identification + clustering
          |
          v
Python cleanup / dedupe
          |
          v
baseline + discovery scoring
          |
          v
data/judge_candidates.json
          |
          v
Judge B V1 (fixed rubric)
          |
          v
data/selected_events.json  <= max 20
          |
          v
YouTube supply-gap enrichment
          |
          v
output/latest.json + latest.md
```

## Daily timing

- **05:10 America/Toronto** — GitHub discovery workflow builds fresh candidates.
- **05:55 America/Toronto** — ChatGPT Judge B V1 reviews candidates and writes the approved list.
- Writing `data/selected_events.json` automatically triggers YouTube enrichment.

The discovery workflow uses two UTC cron entries plus a Toronto local-hour guard so DST does not shift the intended local run hour.

## Files

Active discovery / gating:

- `collect_rss.py` — broad current catalyst discovery
- `clean_events.py` — cheap deterministic cleanup/disambiguation
- `rank_for_judge.py` — baseline + discovery scoring for the Judge candidate pool
- `make_judge_review.py` — compact JSONL review file for ChatGPT
- `docs/JUDGE_B_V1.md` — fixed semantic Judge contract
- `.github/workflows/scanner_b.yml` — discovery workflow

Post-Judge:

- `data/selected_events.json` — only Judge-approved events
- `youtube_enrich.py` — quota-capped YouTube supply-gap measurement
- `.github/workflows/enrich.yml` — automatically triggered enrichment workflow

Shared / legacy / fallback:

- `scanner_common.py`
- `rank_candidates.py`
- `rank_rss.py`
- `collect_external.py`
- `collect_external_safe.py`
- `news_rss.py`
- `config.json`

Generated state:

- `data/raw_events.json`
- `data/judge_candidates.json`
- `data/judge_review.jsonl`
- `data/baseline_history.json`
- `data/selected_events.json`
- `output/latest.json`
- `output/latest.md`

## Repository secrets

### `YOUTUBE_API_KEY`

Required for the post-Judge enrichment workflow.

### `SEC_USER_AGENT`

Optional. A safe repository-identifying default is supplied by the discovery workflow.

## Standardized event output

Each approved event keeps the quantitative Scanner B fields plus fixed Judge fields including:

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
- `judge_version`
- `entity_accuracy`
- `catalyst_reality_score`
- `investor_materiality_score`
- `novelty_score`
- `judge_evidence_quality_score`
- `content_potential_score`
- `judge_score`
- `judge_reason`
- `verification_note`
- `youtube_metrics`
- `scanner_b_score`
- `evidence[]`

## Next architecture step

Once Scanner B is stable, build the Ensemble layer:

```text
Scanner A independent output
            \
             -> normalize / merge / cross-check -> fixed Ensemble Judge -> one email
            /
Scanner B independent output
```

The Ensemble Judge will be separately versioned; Judge B remains only Scanner B's internal quality gate.

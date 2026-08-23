# Scanner B Design V1

Scanner B is an independent catalyst-driven topic discovery system for U.S.-stock YouTube content.

## Non-negotiable constraints

1. Scanner A remains unchanged.
2. Scanner B discovers events independently; it does not consume Scanner A candidates as discovery input.
3. Scanner B may scan broad external sources, but only the final top 20 candidates may call YouTube `search.list`.
4. One formal Scanner B run may use at most 20 YouTube search calls.
5. The later Ensemble layer must not make extra YouTube search calls.
6. Scanner B output must be standardized so it can later merge with Scanner A output.

## Core signal

Scanner B is designed to answer:

> What market-relevant event is accelerating outside YouTube before YouTube content supply becomes crowded?

Its edge should come from low correlation with Scanner A:

- Scanner A: crowd/search attention.
- Scanner B: catalyst / external information-flow breakout.

## V1 signal components

- Entity-specific news/event burst versus rolling baseline.
- Independent source diversity.
- Official catalyst evidence (especially SEC/company disclosures).
- Freshness / acceleration.
- Evidence quality.
- YouTube supply gap, measured only after external discovery and ranking.

## Baseline

Bootstrap from historical external-news observations when available, then persist rolling local observations so the scanner becomes less dependent on repeated historical backfills over time.

Prefer robust statistics (median / MAD or percentile-based normalization) over a raw mean so earnings days and one-off shocks do not permanently inflate the baseline.

## Standard event record

Each event should expose at least:

- event_id
- scanner = B
- entity
- ticker (nullable)
- event_title
- event_timestamp_utc
- discovery_score
- news_burst_score
- source_diversity_score
- catalyst_quality_score
- freshness_score
- evidence_quality_score
- youtube_supply_score
- evidence[]

This contract is intentionally close to what the future Ensemble layer will consume.

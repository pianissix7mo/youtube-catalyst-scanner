# Scanner B Judge V1

This file is the versioned semantic quality contract for Scanner B.

Judge B is a **pre-YouTube gate**. It does not replace external discovery and it does not rank the final A+B portfolio. Its only job is to decide which externally discovered events deserve one of Scanner B's scarce YouTube `search.list` calls.

## Non-negotiable behavior

- Judge only the evidence supplied by Scanner B. Do not invent facts.
- Do not reward an event merely because Python gave it a high burst/discovery score.
- Do not reward an event merely because it sounds entertaining or controversial.
- Do not penalize a valid early catalyst merely because news volume is still low.
- Do not pad to 20. Select fewer when fewer deserve YouTube checks.
- Maximum selected events: **20**.
- One event may consume at most one YouTube `search.list` call after selection.
- The rubric and thresholds below must stay stable until a new version of this file is committed.

## Hard gate: Entity Accuracy

`entity_accuracy` is `pass` or `fail`.

Fail when the mapped company/theme is not actually the subject of the event, including accidental matches from common words, exchange labels, generic words, or ambiguous tickers.

Examples of failures:

- `joint production` mapped to The Joint Corp (`JYNT`)
- ordinary English `on` mapped to ON Semiconductor
- an exchange label such as `NASDAQ:` mapped to Nasdaq Inc.

Entity Accuracy failure means **automatic rejection**, regardless of all other scores.

## Scored dimensions — 100 points

### 1. Catalyst Reality — 25

Is this a real new event rather than evergreen, SEO, generic commentary, price prediction, explainers, or routine market content?

- 22–25: clear new corporate/macro catalyst (earnings/guidance, M&A, major order, pricing change, material legal/regulatory event, financing, official policy change, meaningful product/capacity development)
- 14–21: real development but impact/timing is less clear
- 6–13: mostly commentary or incremental update
- 0–5: evergreen/SEO/prediction/no actual catalyst

### 2. Investor Materiality — 25

Could the event reasonably matter to U.S.-equity investors, a U.S.-listed company, a major investable sector, rates/liquidity, AI/semiconductor supply chains, or other market-relevant assets?

Do not confuse celebrity/controversy coverage around a public company with financial materiality.

- 22–25: likely meaningful to valuation, earnings, demand, supply, regulation, financing, competitive position, or a major macro driver
- 14–21: meaningful but second-order/niche
- 6–13: weak financial linkage
- 0–5: essentially entertainment/general-interest

### 3. Novelty — 15

Is there genuinely new information now?

- 13–15: new catalyst/event just emerged
- 8–12: important new development in an existing story
- 3–7: mostly repetition/repackaging
- 0–2: evergreen/old story

### 4. Evidence Quality — 15

Judge the supplied source set, not just source count.

- 13–15: company/official filing or multiple high-quality independent outlets
- 9–12: credible reporting with reasonable confirmation
- 5–8: limited/secondary evidence; worth checking but uncertain
- 0–4: SEO/aggregation/prediction/low-value sources only

Syndicated copies of the same story do not become multiple independent confirmations.

### 5. Content Potential — 20

Would a Chinese-language U.S.-stock/investing channel have a concrete, understandable angle?

Consider whether the event has a clean hook, investable consequence, contrarian/under-covered angle, or can connect to a company/sector thesis.

- 17–20: strong hook and clear investor angle
- 11–16: usable with some work
- 5–10: weak/niche angle
- 0–4: poor fit for the channel

## Decision thresholds

Calculate:

`judge_score = catalyst_reality + investor_materiality + novelty + evidence_quality + content_potential`

Then apply:

- `entity_accuracy = fail` → reject
- `catalyst_reality <= 5` → reject
- `investor_materiality <= 7` → reject
- `evidence_quality <= 3` → reject
- otherwise `judge_score >= 58` → eligible
- otherwise reject

Among eligible events, rank primarily by `judge_score`, then use external `discovery_score`, `news_burst_score`, freshness, and evidence as tie-breakers.

Select at most 20.

## Required output fields

For every selected event, preserve the original Scanner B fields needed by YouTube enrichment, including:

- `event_id`
- `scanner`
- `entity`
- `ticker`
- `entity_type`
- `event_title`
- `event_timestamp_utc`
- `discovery_score`
- `news_burst_score`
- `source_diversity_score`
- `catalyst_quality_score`
- `freshness_score`
- `evidence_quality_score`
- `youtube_query`
- `youtube_event_terms`
- `baseline`
- `source_domains`
- `recent_evidence_count`
- `evidence`

Add:

- `judge_version`: `B_V1`
- `entity_accuracy`: `pass`
- `catalyst_reality_score`
- `investor_materiality_score`
- `novelty_score`
- `judge_evidence_quality_score`
- `content_potential_score`
- `judge_score`
- `judge_reason`: concise explanation
- `verification_note`: concise note about what should be verified before using the topic, or `null`

The top-level JSON written to `data/selected_events.json` must contain:

- `generated_at_utc`
- `source_candidates_generated_at_utc`
- `judge_version`: `B_V1`
- `notes`
- `events`

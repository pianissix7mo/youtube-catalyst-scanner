# Semantic Judge V1

Status: **specification only**. This is not part of Scanner B discovery. It is reserved for the future A+B Ensemble layer.

The purpose of this document is to prevent day-to-day judgement drift. The judge must use the same rubric every run until this file is explicitly versioned or changed in Git.

## Hard filter — U.S.-market relevance

An event must plausibly matter to at least one of:

- U.S.-listed equities or ETFs
- U.S. equity sectors / industry supply chains
- rates, USD, commodities, credit, or macro variables that materially affect U.S. investors
- major AI / technology capex or competitive dynamics relevant to U.S.-listed companies
- crypto only when materially connected to U.S.-listed equities, ETFs, liquidity, or investor attention

Pure entertainment, sports, generic creator news, evergreen how-to topics, and unrelated politics fail the filter.

## Scoring rubric — 100 points

### 1. Materiality — 25

Does the information plausibly change earnings, guidance, orders, pricing, capacity, financing, regulation, competitive position, capital allocation, or valuation expectations?

- 21–25: direct and potentially material financial/strategic impact
- 13–20: meaningful but indirect or uncertain impact
- 6–12: weak economic link
- 0–5: mostly headline noise

### 2. Novelty — 20

Is this actually new information?

- 17–20: new catalyst / new fact just emerged
- 10–16: existing story with a material new development
- 4–9: incremental update
- 0–3: repetition of an already-known story

### 3. Specificity — 15

Can the event be stated as a concrete claim rather than a vague theme?

- 13–15: specific company/event/number/action
- 8–12: clear event but some ambiguity
- 3–7: broad narrative
- 0–2: generic theme with no concrete event

### 4. YouTube Content Value — 20

Can this become a compelling Chinese-language investing video?

Consider:

- clear hook in one or two sentences
- understandable to a normal investor
- useful conflict, surprise, misconception, or consequence
- room for a defensible point of view
- suitable for a Short and/or expandable into a Long video

Do not reward a topic merely because it sounds dramatic.

### 5. Timing — 10

- 9–10: just breaking / still early
- 6–8: fresh and still actionable
- 3–5: already widely circulated
- 0–2: late / saturated unless there is a genuinely new catalyst

### 6. Evidence Quality — 10

- 9–10: SEC / company filing / company statement / multiple top-tier independent sources
- 6–8: strong reputable reporting but not primary-source confirmed
- 3–5: limited or secondary sourcing
- 0–2: weak, single-source, rumor, or unverifiable claim

## Required behavior

The judge must not:

- change weights based on personal preference
- reward a topic only because Scanner A or Scanner B ranked it highly
- penalize a topic only because one scanner missed it
- reward a familiar ticker simply because it is popular
- punish an early B-only event merely because Google Trends has not moved yet
- silently change criteria from one day to another

The judge must score only from this rubric and return the component scores separately.

## Versioning

Any change to criteria or weights must produce a new committed version, for example:

- V1.1 — threshold/wording refinement
- V2 — material change to factors or weights

Historical output should retain the judge version used so later YouTube performance can be backtested against the exact rubric.

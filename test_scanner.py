#!/usr/bin/env python3
from __future__ import annotations

import unittest

from collect_rss import strict_match_company
from rank_candidates import local_fallback
from scanner_common import parse_timestamp, same_event_title
from youtube_enrich import event_title_relevant


class ScannerRegressionTests(unittest.TestCase):
    def test_bare_uppercase_word_is_not_ticker(self) -> None:
        by_ticker = {
            "JD": {"ticker": "JD", "name": "JD.com, Inc."},
            "MS": {"ticker": "MS", "name": "MORGAN STANLEY"},
            "TV": {"ticker": "TV", "name": "GRUPO TELEVISA, S.A.B."},
        }
        self.assertIsNone(strict_match_company("JD Vance reacts to oil production increase", by_ticker, {}))
        self.assertIsNone(strict_match_company("Hattiesburg MS weather forecast for today", by_ticker, {}))
        self.assertIsNone(strict_match_company("Google TV Streamer gets a price hike", by_ticker, {}))

    def test_explicit_ticker_is_accepted(self) -> None:
        item = {"ticker": "JD", "name": "JD.com, Inc."}
        by_ticker = {"JD": item}
        self.assertEqual(strict_match_company("$JD reports earnings", by_ticker, {}), item)
        self.assertEqual(strict_match_company("JD.com (JD) reports earnings", by_ticker, {}), item)
        self.assertEqual(strict_match_company("NASDAQ:JD reports earnings", by_ticker, {}), item)

    def test_direction_conflict_prevents_cluster_merge(self) -> None:
        self.assertFalse(
            same_event_title(
                "Bitcoin ETFs see biggest weekly inflow since October",
                "Bitcoin ETFs see $210M outflow after strong week",
            )
        )

    def test_similar_same_direction_titles_can_merge(self) -> None:
        self.assertTrue(
            same_event_title(
                "Eurozone inflation jumps to 3.3%, piling pressure on ECB to act",
                "Eurozone inflation rises to 3.3% as ECB faces pressure to act",
            )
        )

    def test_invalid_timestamp_is_not_now(self) -> None:
        with self.assertRaises(ValueError):
            parse_timestamp("")
        with self.assertRaises(ValueError):
            parse_timestamp("definitely-not-a-date")

    def test_local_fallback_requires_history(self) -> None:
        result = local_fallback({"entity": "Example Corp", "recent_evidence_count": 10}, {})
        self.assertEqual(result["news_burst_score"], 0.0)
        self.assertEqual(result["source"], "rolling_local_fallback_insufficient_history")

    def test_youtube_relevance_requires_chinese_entity_and_event(self) -> None:
        event = {
            "entity": "AMAZON COM INC",
            "ticker": "AMZN",
            "categories": ["regulatory_legal"],
            "youtube_event_terms": ["lawsuit"],
        }
        self.assertTrue(event_title_relevant("亚马逊 AMZN 遭监管机构反垄断调查", event))
        self.assertFalse(event_title_relevant("Amazon FTC lawsuit update", event))
        self.assertFalse(event_title_relevant("微软 MSFT 遭监管机构反垄断调查", event))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Resilient entry point for Scanner B external collection.

GitHub-hosted runners are sometimes blocked by SEC edge controls even with a
compliant User-Agent.  The SEC ticker/CIK map is reference data, so this entry
point retries that single reference-data request through a public GitHub mirror.
Live SEC filing feeds remain best-effort inside collect_external.py and must
never make the whole scanner fail.
"""
from __future__ import annotations

import os

import requests

import collect_external as core

TICKER_MIRROR = (
    "https://raw.githubusercontent.com/"
    "finsasdata/Bookdata/main/company_tickers_exchange.json"
)


class ResilientSession(requests.Session):
    def get(self, url, *args, **kwargs):  # type: ignore[override]
        response = super().get(url, *args, **kwargs)
        if url == core.SEC_TICKERS and response.status_code in {403, 429, 503}:
            print(
                f"SEC ticker reference returned {response.status_code}; "
                "using GitHub mirror fallback"
            )
            mirror_kwargs = dict(kwargs)
            # The mirror does not need SEC-specific request parameters.
            mirror_kwargs.pop("params", None)
            return super().get(TICKER_MIRROR, *args, **mirror_kwargs)
        return response


def resilient_session() -> requests.Session:
    session = ResilientSession()
    session.headers.update(
        {
            "User-Agent": os.getenv(
                "SEC_USER_AGENT",
                "youtube-catalyst-scanner/1.0 "
                "70549770+pianissix7mo@users.noreply.github.com",
            )
        }
    )
    return session


def main() -> None:
    # Replace only the session factory.  All discovery/clustering logic stays in
    # collect_external.py, so the fallback cannot change Scanner B semantics.
    core.session = resilient_session
    core.main()


if __name__ == "__main__":
    main()

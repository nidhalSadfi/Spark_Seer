"""X posts through TwitterAPI.io (third-party provider; key `twitterapi` in .env).

Each advanced_search page returns up to 20 posts and spends credit, so pages are
capped per run and per UTC day (Caps.TWITTER_PAGES_PER_RUN / _PER_DAY), counted in
the store's usage table. Terms of use and provenance of this provider's data are
the user's responsibility to confirm before any public reuse.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests

from .. import config
from ..store import now_iso
from . import Record, SourceError, parse_iso, utc_iso

URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


def build_query(terms: list[str], lang: str | None, since_iso: str, until_iso: str | None = None,
                must: str | None = None) -> str:
    """`must` is a phrase every tweet has to contain (the place name in a replay)."""
    ors = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    since = parse_iso(since_iso) or datetime.utcnow()
    q = (f'"{must}" ' if must else "") + f"({ors})"
    if lang:
        q += f" lang:{lang}"
    q += f" -is:retweet since_time:{int(since.timestamp())}"
    until = parse_iso(until_iso) if until_iso else None
    if until:
        q += f" until_time:{int(until.timestamp())}"
    return q


MIN_GAP_S = 6.0  # free tier measured 14 Nov 2026: "one request every 5 seconds"


class TwitterAPI:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.key = api_key or config.secret("twitterapi")
        self.s = session or requests.Session()
        self._last = 0.0

    def _pace(self) -> None:
        wait = MIN_GAP_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def search(self, terms: list[str], lang: str | None, since_iso: str, max_pages: int,
               until_iso: str | None = None, must: str | None = None) -> tuple[list[Record], int]:
        """Returns (records, pages_used)."""
        if not self.available:
            raise SourceError("x: no twitterapi key in .env")
        query = build_query(terms, lang, since_iso, until_iso, must)
        out: dict[str, Record] = {}
        cursor, pages = "", 0
        while pages < max_pages:
            params = {"query": query, "queryType": "Latest"}
            if cursor:
                params["cursor"] = cursor
            r = None
            for attempt in range(2):  # the free tier counts the gap from the previous reply
                self._pace()
                try:
                    r = self.s.get(URL, params=params, timeout=40, headers={"X-API-Key": self.key})
                except requests.RequestException as e:
                    raise SourceError(f"x search: {type(e).__name__}") from None
                finally:
                    self._last = time.monotonic()
                if r.status_code != 429:
                    break
            pages += 1
            if r.status_code != 200:
                raise SourceError(f"x search: HTTP {r.status_code} {config.redact(r.text[:120])}")
            d = r.json()
            fetched = now_iso()
            for t in d.get("tweets", []):
                url = t.get("url") or t.get("twitterUrl")
                if not url or url in out:
                    continue
                out[url] = Record(
                    source="x", kind="post", record_id=str(t.get("id")), url=url, text=t.get("text") or "",
                    published_at=utc_iso(t.get("createdAt")), fetched_at=fetched,
                    author=(t.get("author") or {}).get("userName"), lang=t.get("lang"),
                    meta={"retweets": t.get("retweetCount"), "likes": t.get("likeCount")})
            cursor = d.get("next_cursor") or ""
            if not d.get("has_next_page") or not cursor:
                break
        return list(out.values()), pages

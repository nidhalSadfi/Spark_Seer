"""GDELT DOC 2.0 article search (no key). One request per 5 seconds or it answers 429.

GDELT returns headlines and links, not article bodies. A headline is a clue for the
judge, never a confirmation. sourcecountry uses FIPS codes, not ISO-2.
"""

from __future__ import annotations

import threading
import time

import requests

from ..config import USER_AGENT
from ..store import now_iso
from . import RateLimited, Record, SourceError, utc_iso

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FIPS = {"FR": "FR", "DZ": "AG", "TN": "TS", "MA": "MO", "ES": "SP", "PT": "PO", "IT": "IT",
        "GR": "GR", "TR": "TU", "LB": "LE", "SY": "SY", "US": "US", "CA": "CA", "AU": "AS"}
LANG_NAMES = {"fr": "french", "ar": "arabic", "en": "english", "es": "spanish", "pt": "portuguese",
              "it": "italian", "el": "greek", "tr": "turkish"}


class Gdelt:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._lock = threading.Lock()
        self._last = 0.0

    def search(self, terms: list[str], lang: str, country: str, hours: int, max_records: int) -> list[Record]:
        fips = FIPS.get(country)
        if not fips:
            raise SourceError(f"gdelt: no FIPS code mapped for {country}")
        ors = " OR ".join(f'"{t}"' if " " in t else t for t in terms[:6])
        query = f"({ors}) sourcecountry:{fips}"
        if lang in LANG_NAMES:
            query += f" sourcelang:{LANG_NAMES[lang]}"
        with self._lock:
            wait = 5.5 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(URL, timeout=40, params={"query": query, "mode": "artlist", "format": "json",
                                                        "maxrecords": max_records, "sort": "datedesc",
                                                        "timespan": f"{max(1, hours)}h"})
            except requests.RequestException as e:
                raise SourceError(f"gdelt: {type(e).__name__}") from None
            finally:
                self._last = time.monotonic()
        if r.status_code == 429 or r.text.startswith("Please limit"):
            raise RateLimited("gdelt: rate limited (HTTP 429)")
        if r.status_code != 200:
            raise SourceError(f"gdelt: HTTP {r.status_code}")
        try:
            arts = r.json().get("articles", [])
        except ValueError:
            raise SourceError(f"gdelt: {r.text[:120]}") from None
        fetched = now_iso()
        return [Record(source="gdelt", kind="news", record_id=a["url"], url=a["url"], text=a.get("title") or "",
                       published_at=utc_iso(a.get("seendate")), fetched_at=fetched, author=a.get("domain"),
                       lang=lang, meta={"sourcecountry": a.get("sourcecountry"), "seen_is_crawl_time": True})
                for a in arts if a.get("url")]

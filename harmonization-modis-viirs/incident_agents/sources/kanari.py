"""Kanari (kanari.io): free, no key, CC BY 4.0, attribution "Source: kanari.io".

Their API page asks for at most one call per minute per endpoint and for caching;
responses are cached here for 5 minutes. Two uses:
- verified public signals (`/api/signals`), already geolocated and AI-checked by Kanari;
- satellite clusters through the MCP tool `active_fires` (country, bbox or near-point filter),
  used to confirm a ticket or, if enabled, to open satellite-only tickets.
"""

from __future__ import annotations

import json
import re
import threading
import time

import requests

from ..config import USER_AGENT
from ..store import now_iso
from . import Record, SourceError, utc_iso

BASE = "https://kanari.io"
ATTRIBUTION = "Source: kanari.io (CC BY 4.0) — https://kanari.io"
CACHE_S = 300
_HOURS = (6, 12, 24, 48, 72)


def kanari_hours(hours: int) -> int:
    return next((h for h in _HOURS if h >= hours), 72)


class Kanari:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_S:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.monotonic(), val)
        return val

    def mcp(self, tool: str, args: dict) -> dict:
        def call():
            try:
                r = self.s.post(f"{BASE}/api/mcp", timeout=60,
                                headers={"Content-Type": "application/json",
                                         "Accept": "application/json, text/event-stream"},
                                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": tool, "arguments": args}})
            except requests.RequestException as e:
                raise SourceError(f"kanari mcp {tool}: {type(e).__name__}") from None
            if r.status_code != 200:
                raise SourceError(f"kanari mcp {tool}: HTTP {r.status_code}")
            # No charset in the SSE reply, so requests would guess Latin-1; the body is UTF-8.
            m = re.search(r"\{.*\}", r.content.decode("utf-8", "replace"), re.S)
            if not m:
                raise SourceError(f"kanari mcp {tool}: empty reply")
            d = json.loads(m.group(0))
            if "error" in d:
                raise SourceError(f"kanari mcp {tool}: {d['error']}")
            content = d.get("result", {}).get("content") or [{}]
            if d.get("result", {}).get("isError"):
                raise SourceError(f"kanari mcp {tool}: {content[0].get('text', '')[:200]}")
            return json.loads(content[0].get("text") or "{}")
        return self._cached(f"mcp:{tool}:{json.dumps(args, sort_keys=True)}", call)

    def signals(self, hours: int) -> list[dict]:
        h = kanari_hours(hours)

        def call():
            try:
                r = self.s.get(f"{BASE}/api/signals", params={"hours": h}, timeout=60)
            except requests.RequestException as e:
                raise SourceError(f"kanari signals: {type(e).__name__}") from None
            if r.status_code != 200:
                raise SourceError(f"kanari signals: HTTP {r.status_code}")
            return json.loads(r.content.decode("utf-8", "replace")).get("signals", [])
        return self._cached(f"signals:{h}", call)

    def signal_records(self, hours: int, countries: list[str]) -> list[Record]:
        """One record per kept post, carrying Kanari's place and coordinates."""
        fetched = now_iso()
        out = []
        for sig in self.signals(hours):
            cc = (sig.get("countryCode") or "").upper()
            if cc not in countries:
                continue
            for p in sig.get("posts") or []:
                url = p.get("url")
                if not url:
                    continue
                out.append(Record(
                    source="kanari_signal", kind="verified_signal", record_id=url, url=url,
                    text=p.get("text") or "", published_at=utc_iso(p.get("createdAt")), fetched_at=fetched,
                    author=p.get("handle") or p.get("author"), lat=sig.get("lat"), lon=sig.get("lon"),
                    place=sig.get("place"), country=cc,
                    meta={"via": p.get("source"), "newFire": bool(sig.get("newFire")),
                          "attribution": ATTRIBUTION}))
        return out

    def fires_near(self, lat: float, lon: float, radius_km: float, hours: int,
                   country: str | None = None) -> list[dict]:
        """Clusters within radius_km. With a country, the whole country list is fetched once
        (cached 5 min) and filtered locally, so many tickets cost one call, as Kanari asks."""
        if country:
            from ..geo import haversine_km
            d = self.mcp("active_fires", {"country": country, "hours": kanari_hours(hours), "limit": 200})
            fires = d.get("fires", [])
            # The country list is capped at 200. If more clusters matched, an empty local filter
            # would wrongly read as "no satellite support"; ask Kanari for this point instead.
            if int(d.get("matching") or 0) <= len(fires):
                return [f for f in fires
                        if f.get("lat") is not None and haversine_km(lat, lon, f["lat"], f["lon"]) <= radius_km]
        d = self.mcp("active_fires", {"near": {"lat": round(lat, 3), "lon": round(lon, 3),
                                               "radiusKm": max(1, min(1000, round(radius_km)))},
                                      "hours": kanari_hours(hours), "limit": 20})
        return d.get("fires", [])

    def fires_in(self, country: str | None, box: tuple | None, hours: int, limit: int = 200) -> list[dict]:
        args: dict = {"hours": kanari_hours(hours), "limit": limit}
        if box:
            args["bbox"] = [round(v, 3) for v in box]
        elif country:
            args["country"] = country
        return self.mcp("active_fires", args).get("fires", [])

"""Merge located reports into tickets and check them against satellite detections.

One ticket per incident, not per post: a report joins an existing ticket in the same
country when the two locations are within the larger of their radii (at least
10 km) and within 48 hours. The same URL never counts twice. Status words:
- Satellite overlap: a Kanari cluster or FIRMS detection near the ticket in time and space.
- No satellite support: the check ran and found nothing (not evidence of no fire).
- Mentioned: no satellite check could run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from .geo import box_around, haversine_km
from .sources import Record, parse_iso
from .store import Store, now_iso

MERGE_HOURS = 48
MIN_MERGE_KM = 10.0
SAT_PAD_KM = 10.0

LABELS = {"active_fire_report": "Fire reported", "smoke_or_possible_fire": "Smoke reported",
          "news_about_fire": "Fire in the news", "satellite": "Satellite fire cluster"}


@dataclass
class Located:
    rec: Record
    verdict: dict
    place: str
    country: str
    lat: float
    lon: float
    precision: str
    radius_km: float

    @property
    def when(self) -> str:
        return self.rec.published_at or self.rec.fetched_at


def _hours_apart(a: str | None, b: str | None) -> float:
    da, db = parse_iso(a), parse_iso(b)
    if not da or not db:
        return 0.0
    return abs((da - db).total_seconds()) / 3600


def find_match(loc: Located, tickets: list[dict]) -> dict | None:
    best, best_d = None, None
    for t in tickets:
        if t["country"] != loc.country or t["lat"] is None:
            continue
        d = haversine_km(loc.lat, loc.lon, t["lat"], t["lon"])
        limit = max(MIN_MERGE_KM, loc.radius_km, t["radius_km"] or 0)
        near_in_time = (_hours_apart(loc.when, t["last_seen"]) <= MERGE_HOURS
                        or _hours_apart(loc.when, t["first_seen"]) <= MERGE_HOURS)
        if d <= limit and near_in_time and (best_d is None or d < best_d):
            best, best_d = t, d
    return best


def upsert(store: Store, loc: Located, run_id: int, open_tickets: list[dict]) -> tuple[int, bool]:
    """Attach the report to a ticket (new or existing). Returns (ticket_id, created)."""
    existing = store.url_ticket(loc.rec.url)
    if existing:
        return existing, False
    t = find_match(loc, open_tickets)
    now = now_iso()
    created = False
    if t is None:
        cat = loc.verdict.get("category", "satellite")
        title = f"{LABELS.get(cat, 'Fire report')}: {loc.place}"
        tid = store.x(
            "INSERT INTO tickets(created_at, updated_at, run_id, title, place, country, lat, lon, precision, "
            "radius_km, first_seen, last_seen, status, category) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, run_id, title, loc.place, loc.country, loc.lat, loc.lon, loc.precision, loc.radius_km,
             loc.when, loc.when, "Mentioned", cat)).lastrowid
        t = store.q("SELECT * FROM tickets WHERE id=?", (tid,))[0]
        open_tickets.append(t)
        created = True
    else:
        tid = t["id"]
        first = min(t["first_seen"], loc.when)
        last = max(t["last_seen"], loc.when)
        sets = {"updated_at": now, "first_seen": first, "last_seen": last, "run_id": run_id}
        if loc.radius_km < (t["radius_km"] or 1e9):  # keep the most precise location
            sets.update(place=loc.place, lat=loc.lat, lon=loc.lon, precision=loc.precision,
                        radius_km=loc.radius_km)
        store.x("UPDATE tickets SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?",
                (*sets.values(), tid))
        t.update(sets)
    store.x("INSERT OR IGNORE INTO ticket_sources(ticket_id, source, kind, url, author, published_at, fetched_at, "
            "excerpt, place_phrase, judge) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, loc.rec.source, loc.rec.kind, loc.rec.url, loc.rec.author, loc.rec.published_at,
             loc.rec.fetched_at, (loc.rec.text or "")[:280], loc.verdict.get("place_phrase") or loc.rec.place,
             json.dumps(loc.verdict, ensure_ascii=False)))
    return tid, created


def satellite_check(store: Store, ticket_id: int, kanari, firms, hours: int, report) -> str:
    t = store.q("SELECT * FROM tickets WHERE id=?", (ticket_id,))[0]
    radius = (t["radius_km"] or 10) + SAT_PAD_KM
    lo = parse_iso(t["first_seen"]) - timedelta(hours=24)
    hi = parse_iso(t["last_seen"]) + timedelta(hours=24)
    ran, found = [], []
    if kanari is not None:
        try:
            for f in kanari.fires_near(t["lat"], t["lon"], radius, max(hours, 24), country=t["country"]):
                seen_last, seen_first = parse_iso(f.get("lastSeen")), parse_iso(f.get("firstSeen"))
                if seen_last and seen_first and seen_last >= lo and seen_first <= hi:
                    found.append({"source": "kanari", "place": f.get("place"), "lat": f.get("lat"),
                                  "lon": f.get("lon"), "detections": f.get("detections"),
                                  "sensors": f.get("sensors"), "first_seen": f.get("firstSeen"),
                                  "last_seen": f.get("lastSeen"), "url": f.get("mapUrl"),
                                  "km": round(haversine_km(t["lat"], t["lon"], f["lat"], f["lon"]), 1)})
            ran.append("kanari")
        except Exception as e:  # source failure must not stop the run
            report("satellite", "error", f"ticket {ticket_id}: kanari {e}", source="kanari")
    if not found and firms is not None and firms.available:
        try:
            rows = firms.detections(box_around(t["lat"], t["lon"], radius), lo.date(), hi.date())
            for r in rows[:20]:
                found.append({"source": "firms", "sensor": r.get("sensor"), "lat": float(r["latitude"]),
                              "lon": float(r["longitude"]), "acq_date": r.get("acq_date"),
                              "acq_time": r.get("acq_time"), "confidence": r.get("confidence")})
            ran.append("firms")
        except Exception as e:
            report("satellite", "error", f"ticket {ticket_id}: FIRMS {type(e).__name__}", source="firms")
    status = "Satellite overlap" if found else ("No satellite support" if ran else "Mentioned")
    sat = {"checked": ran, "window": [lo.isoformat(), hi.isoformat()], "radius_km": radius, "matches": found[:10]}
    store.x("UPDATE tickets SET status=?, satellite=?, updated_at=? WHERE id=?",
            (status, json.dumps(sat, ensure_ascii=False), now_iso(), ticket_id))
    return status

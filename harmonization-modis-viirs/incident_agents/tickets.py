"""Merge located reports into tickets and check them against satellite detections.

One ticket per incident, not per post: a report joins an existing ticket in the same
country when the two locations are within the larger of their radii (at least
10 km) and within 48 hours. The same URL never counts twice. Status words:
- Satellite overlap: a Kanari cluster or FIRMS detection near the ticket in time and space.
- No satellite support: the check ran and found nothing (not evidence of no fire).
- Mentioned: no satellite check could run.

Lead time compares the first public post with the first satellite detection near the ticket,
in signed minutes (positive: people posted first). Two satellite clocks are kept apart:
Kanari's first detection (mostly Meteosat MTG, a geostationary satellite imaging every
10 minutes) and the first FIRMS polar-orbit pass (VIIRS / MODIS, a few passes a day).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .geo import box_around, haversine_km
from .history import cell_history
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


def upsert(store: Store, loc: Located, run_id: int, open_tickets: list[dict],
           mode: str = "live") -> tuple[int, bool]:
    """Attach the report to a ticket (new or existing). Returns (ticket_id, created)."""
    existing = store.url_ticket(loc.rec.url, mode)
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
            "radius_km, first_seen, last_seen, status, category, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, run_id, title, loc.place, loc.country, loc.lat, loc.lon, loc.precision, loc.radius_km,
             loc.when, loc.when, "Mentioned", cat, mode)).lastrowid
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
                                  "last_seen": f.get("lastSeen"), "url": f.get("mapUrl") or f.get("url"),
                                  "km": round(haversine_km(t["lat"], t["lon"], f["lat"], f["lon"]), 1)})
            ran.append("kanari")
        except Exception as e:  # source failure must not stop the run
            report("satellite", "error", f"ticket {ticket_id}: kanari {e}", source="kanari")
    if firms is not None and firms.available:  # also when Kanari matched: gives the polar-pass time
        try:
            rows = firms.detections(box_around(t["lat"], t["lon"], radius), lo.date(), hi.date())
            rows = [r for r in rows if _acq_dt(r) is None or lo <= _acq_dt(r) <= hi]
            for r in sorted(rows, key=lambda r: _acq_dt(r) or hi)[:20]:
                dt = _acq_dt(r)
                found.append({"source": "firms", "sensor": r.get("sensor"), "lat": float(r["latitude"]),
                              "lon": float(r["longitude"]), "acq_date": r.get("acq_date"),
                              "acq_time": r.get("acq_time"), "confidence": r.get("confidence"),
                              "first_seen": dt.isoformat().replace("+00:00", "Z") if dt else None})
            ran.append("firms")
        except Exception as e:
            report("satellite", "error", f"ticket {ticket_id}: FIRMS {type(e).__name__}", source="firms")
    status = "Satellite overlap" if found else ("No satellite support" if ran else "Mentioned")
    kanari_m = [m for m in found if m["source"] == "kanari"]
    firms_m = [m for m in found if m["source"] == "firms"]
    sat = {"checked": ran, "window": [lo.isoformat(), hi.isoformat()], "radius_km": radius,
           "matches": kanari_m[:6] + firms_m[:6], "lead": lead_times(store, ticket_id, found)}
    month = parse_iso(t["first_seen"]).month if t["first_seen"] else None
    hist = cell_history(t["lat"], t["lon"], t["country"], month)
    store.x("UPDATE tickets SET status=?, satellite=?, history=?, updated_at=? WHERE id=?",
            (status, json.dumps(sat, ensure_ascii=False), json.dumps(hist, ensure_ascii=False) if hist else None,
             now_iso(), ticket_id))
    return status


SOCIAL_KINDS = ("post", "news", "verified_signal")


def _iso(d: datetime | None) -> str | None:
    return d.isoformat().replace("+00:00", "Z") if d else None


def _acq_dt(row: dict) -> datetime | None:
    """FIRMS acq_date (YYYY-MM-DD) + acq_time (HHMM, UTC)."""
    try:
        t = str(row.get("acq_time") or "0").zfill(4)
        return datetime.strptime(f"{row['acq_date']} {t}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return None


def _minutes(sat: datetime | None, social: datetime | None) -> int | None:
    return round((sat - social).total_seconds() / 60) if sat and social else None


def lead_times(store: Store, ticket_id: int, matches: list[dict]) -> dict | None:
    """Signed minutes from the first public post to the first satellite detection (positive: post first)."""
    rows = store.q("SELECT published_at FROM ticket_sources WHERE ticket_id=? AND kind IN (?,?,?) "
                   "AND published_at IS NOT NULL ORDER BY published_at LIMIT 1", (ticket_id, *SOCIAL_KINDS))
    social = parse_iso(rows[0]["published_at"]) if rows else None
    if social is None:
        return None
    k = [d for d in (parse_iso(m.get("first_seen")) for m in matches if m["source"] == "kanari") if d]
    f = [d for d in (parse_iso(m.get("first_seen")) for m in matches if m["source"] == "firms") if d]
    first_k, first_f = (min(k) if k else None), (min(f) if f else None)
    if not first_k and not first_f:
        return None
    return {"social_first": _iso(social), "kanari_first": _iso(first_k), "polar_first": _iso(first_f),
            "minutes_vs_kanari": _minutes(first_k, social), "minutes_vs_polar": _minutes(first_f, social)}


def lead_summary(store: Store, ticket_ids: list[int]) -> dict:
    """n, median and split of lead times over tickets that have both a post and a satellite time."""
    leads = []
    for tid in ticket_ids:
        r = store.q("SELECT satellite FROM tickets WHERE id=?", (tid,))
        leads.append((json.loads(r[0]["satellite"]) if r and r[0]["satellite"] else {}).get("lead") or {})
    out = {}
    for key in ("minutes_vs_kanari", "minutes_vs_polar"):
        vals = sorted(lead[key] for lead in leads if lead.get(key) is not None)
        if vals:
            mid = len(vals) // 2
            median = vals[mid] if len(vals) % 2 else round((vals[mid - 1] + vals[mid]) / 2)
            out[key] = {"n": len(vals), "median": median, "posts_first": sum(v > 0 for v in vals),
                        "satellite_first": sum(v < 0 for v in vals), "min": vals[0], "max": vals[-1]}
    return out

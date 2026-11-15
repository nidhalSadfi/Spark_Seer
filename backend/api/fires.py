"""GET /fires/live (spec §10, amendment 2026-11-15): stored NRT detections for the fire map.

Reads `raw_detections` only, in a READ ONLY transaction; it never calls FIRMS (I1). The window
ends at the newest stored NRT detection (of the requested sources; the bbox does not move it), so
the map is not empty just because FIRMS publishes a few hours late, and reaches back `hours`.
"""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, HTTPException

from api import core, database
from api.database import iso, utc_datetime

router = APIRouter(tags=["fires"])

DEFAULT_HOURS = 48
MAX_HOURS = 168
SOURCE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def parse_hours(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_HOURS
    try:
        hours = int(raw)
    except ValueError:
        raise HTTPException(400, f"hours must be a whole number from 1 to {MAX_HOURS}") from None
    if not 1 <= hours <= MAX_HOURS:
        raise HTTPException(400, f"hours must be a whole number from 1 to {MAX_HOURS}")
    return hours


def parse_sources(raw: str | None) -> list[str]:
    sources = [s.strip().upper() for s in (raw or "").split(",") if s.strip()]
    bad = [s for s in sources if not SOURCE_RE.match(s)]
    if bad:
        raise HTTPException(400, f"invalid source {bad[0]!r}: FIRMS source ids such as VIIRS_NOAA20_NRT")
    return sources


def sensor_of(source: str, instrument: str | None) -> str:
    """"modis" for MODIS sources, "viirs" for VIIRS ones (by the FIRMS source id, then the instrument)."""
    if source.upper().startswith("MODIS") or (instrument or "").upper() == "MODIS":
        return "modis"
    return "viirs"


def live_fires(hours: int, bbox: tuple[float, float, float, float] | None, sources: list[str]) -> dict:
    src = "AND source = ANY(:sources)" if sources else ""
    params = {"sources": sources}
    with database.read(isolation_level="REPEATABLE READ") as c:
        newest = database.one(c, f"""
            SELECT acq_date, acq_time FROM raw_detections WHERE product = 'NRT' {src}
            ORDER BY acq_date DESC, acq_time DESC LIMIT 1""", **params)
        per_source = database.rows(c, f"""
            SELECT source, max(acq_date) AS max_acq_date FROM raw_detections WHERE product = 'NRT' {src}
            GROUP BY source ORDER BY source""", **params)
        snapshot = database.scalar(c, "SELECT max(fetched_at) FROM firms_fetches")
        detections, start, end = [], None, None
        if newest is not None:
            end = utc_datetime(newest["acq_date"], newest["acq_time"])
            start = end - timedelta(hours=hours)
            box = ""
            if bbox is not None:
                box = "AND longitude BETWEEN :x0 AND :x1 AND latitude BETWEEN :y0 AND :y1"
                params.update(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3])
            # acq_date >= the start date lets the (source, acq_date) index narrow the scan first.
            detections = database.rows(c, f"""
                SELECT id, latitude, longitude, acq_date, acq_time, source, satellite, instrument, confidence, frp,
                       daynight
                FROM raw_detections
                WHERE product = 'NRT' {src} {box} AND acq_date >= :start_date
                  AND (acq_date + acq_time) >= :start_ts AND (acq_date + acq_time) <= :end_ts
                ORDER BY acq_date, acq_time, id""",
                **params, start_date=start.date(), start_ts=start.replace(tzinfo=None),
                end_ts=end.replace(tzinfo=None))
    return {
        "snapshot": iso(snapshot) if snapshot is not None else iso(end),
        "window": {"start": iso(start), "end": iso(end)},
        "detections": [{
            "id": d["id"], "lat": d["latitude"], "lon": d["longitude"],
            "acq_time": iso(utc_datetime(d["acq_date"], d["acq_time"])),
            "source": d["source"], "satellite": d["satellite"], "sensor": sensor_of(d["source"], d["instrument"]),
            "confidence": d["confidence"], "frp": d["frp"], "daynight": d["daynight"],
        } for d in detections],
        "sources": [{"source": s["source"], "max_acq_date": iso(s["max_acq_date"])} for s in per_source],
        "meta": {"count": len(detections), "product": "NRT", "hours": hours,
                 "bbox": list(bbox) if bbox is not None else None},
    }


@router.get("/fires/live")
def fires_live(hours: str | None = None, bbox: str | None = None, source: str | None = None):
    """NRT detections within `hours` (1-168, default 48) of the newest stored one; 400 bad input, 503 no database."""
    n = parse_hours(hours)
    box = None
    if bbox is not None and bbox.strip() != "":
        try:
            box = core.parse_bbox(bbox)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    return live_fires(n, box, parse_sources(source))

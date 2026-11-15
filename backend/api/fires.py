"""GET /fires/live (spec §10, amendment 2026-11-15): stored NRT detections for the fire map.

Reads `raw_detections` only, in a READ ONLY transaction; it never calls FIRMS (I1). The window
ends at the newest stored NRT detection (of the requested sources; the bbox does not move it), so
the map is not empty just because FIRMS publishes a few hours late, and reaches back `hours`.

World scale (spec §16): the answer has a `mode`. "detections" is the original shape. "grid"
aggregates the window's detections into cells of `meta.resolution` degrees instead,
`{"lat", "lon", "count", "frp_sum", "frp_max", "modis", "viirs", "latest"}` (lat/lon = the cell
centre; "detections" is then empty). The grid is used when the window holds more than
FIRES_LIVE_MAX_DETECTIONS detections, when the bbox spans more than FIRES_LIVE_GRID_SPAN_DEG
(longitude or latitude), or with `mode=grid`. Its resolution is 0.25° up to
FIRES_LIVE_FINE_GRID_MAX_SPAN_DEG of span (or without a bbox: the whole world counts as 360°), 0.5°
beyond. Without a bbox only the detection cap switches to the grid, as before the world.
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


MODES = ("auto", "grid")
FINE_DEG, COARSE_DEG = 0.25, 0.5
_IS_MODIS = "(upper(source) LIKE 'MODIS%' OR upper(coalesce(instrument, '')) = 'MODIS')"  # = sensor_of


def parse_mode(raw: str | None) -> str:
    mode = (raw or "auto").strip().lower() or "auto"
    if mode not in MODES:
        raise HTTPException(400, "mode must be auto or grid")
    return mode


def span_deg(bbox: tuple[float, float, float, float] | None) -> float:
    """The bbox's larger side in degrees (the whole world without a bbox)."""
    if bbox is None:
        return 360.0
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def grid_resolution(bbox, fine_max_span_deg: float) -> float:
    return FINE_DEG if span_deg(bbox) <= fine_max_span_deg else COARSE_DEG


def live_fires(hours: int, bbox: tuple[float, float, float, float] | None, sources: list[str],
               mode: str = "auto") -> dict:
    s = database.settings()
    src = "AND source = ANY(:sources)" if sources else ""
    params = {"sources": sources}
    cells, count, resolution, chosen = [], 0, None, "detections"
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
            # acq_date >= the start date lets the NRT (acq_date, latitude, longitude) index (migration
            # 0004) narrow the scan to the window's days, and check the bbox inside the index.
            where = f"""
                FROM raw_detections
                WHERE product = 'NRT' {src} {box} AND acq_date >= :start_date AND acq_date <= :end_date
                  AND (acq_date + acq_time) >= :start_ts AND (acq_date + acq_time) <= :end_ts"""
            params.update(start_date=start.date(), end_date=end.date(), start_ts=start.replace(tzinfo=None),
                          end_ts=end.replace(tzinfo=None))
            count = int(database.scalar(c, f"SELECT count(*) {where}", **params))
            wide = bbox is not None and span_deg(bbox) > s.fires_live_grid_span_deg
            if mode == "grid" or wide or count > s.fires_live_max_detections:
                chosen, resolution = "grid", grid_resolution(bbox, s.fires_live_fine_grid_max_span_deg)
                cells = database.rows(c, f"""
                    SELECT floor(latitude / :res) AS gy, floor(longitude / :res) AS gx, count(*) AS n,
                           sum(frp) AS frp_sum, max(frp) AS frp_max,
                           count(*) FILTER (WHERE {_IS_MODIS}) AS modis, max(acq_date + acq_time) AS latest
                    {where} GROUP BY 1, 2 ORDER BY 1, 2""", **params, res=resolution)
            else:
                detections = database.rows(c, f"""
                    SELECT id, latitude, longitude, acq_date, acq_time, source, satellite, instrument, confidence,
                           frp, daynight
                    {where} ORDER BY acq_date, acq_time, id""", **params)
    out = {
        "mode": chosen,
        "snapshot": iso(snapshot) if snapshot is not None else iso(end),
        "window": {"start": iso(start), "end": iso(end)},
        "detections": [{
            "id": d["id"], "lat": d["latitude"], "lon": d["longitude"],
            "acq_time": iso(utc_datetime(d["acq_date"], d["acq_time"])),
            "source": d["source"], "satellite": d["satellite"], "sensor": sensor_of(d["source"], d["instrument"]),
            "confidence": d["confidence"], "frp": d["frp"], "daynight": d["daynight"],
        } for d in detections],
        "sources": [{"source": s["source"], "max_acq_date": iso(s["max_acq_date"])} for s in per_source],
        "meta": {"count": count if chosen == "grid" else len(detections), "product": "NRT", "hours": hours,
                 "bbox": list(bbox) if bbox is not None else None, "resolution": resolution,
                 "max_detections": s.fires_live_max_detections, "grid_span_deg": s.fires_live_grid_span_deg,
                 "fine_grid_max_span_deg": s.fires_live_fine_grid_max_span_deg},
    }
    if chosen == "grid":
        out["cells"] = [{
            "lat": (float(g["gy"]) + 0.5) * resolution, "lon": (float(g["gx"]) + 0.5) * resolution,
            "count": int(g["n"]), "frp_sum": None if g["frp_sum"] is None else round(float(g["frp_sum"]), 2),
            "frp_max": g["frp_max"], "modis": int(g["modis"]), "viirs": int(g["n"]) - int(g["modis"]),
            "latest": iso(g["latest"]),
        } for g in cells]
        out["meta"]["cells"] = len(cells)
    return out


@router.get("/fires/live")
def fires_live(hours: str | None = None, bbox: str | None = None, source: str | None = None,
               mode: str | None = None):
    """NRT detections within `hours` (1-168, default 48) of the newest stored one, or their grid
    (see the module docstring; `mode=grid` forces it); 400 bad input, 503 no database."""
    n = parse_hours(hours)
    box = None
    if bbox is not None and bbox.strip() != "":
        try:
            box = core.parse_bbox(bbox)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    return live_fires(n, box, parse_sources(source), parse_mode(mode))

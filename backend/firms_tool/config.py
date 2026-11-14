"""Load a request config and expand it into concrete archive-download jobs."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .sensors import Sensor, get_sensor

# Logical field -> form field name. The create form is only served to logged-in
# users, so these names are placeholders until `firms inspect-form` confirms them.
DEFAULT_FORM: dict[str, Any] = {
    "verified": False,
    "endpoint": "/api/download/",
    "method": "POST",
    "encoding": "form",
    "date_format": "%Y-%m-%d",
    "polygon_format": "flat",
    "fields": {
        "email": "email",
        "sensor": "sensor",
        "format": "format",
        "start_date": "start_date",
        "end_date": "end_date",
        "area_type": "area_type",
        "country": "country",
        "bbox": "coordinates",
        "polygon": "coordinates",
    },
    "sensor_values": {},
    "format_values": {"csv": "csv", "json": "json", "shp": "shp"},
    "area_type_values": {"world": "world", "country": "country", "bbox": "rectangle", "polygon": "polygon"},
    "constants": {},
}

FORMATS = ("csv", "json", "shp")
AREA_TYPES = ("world", "country", "bbox", "polygon")
# FIRMS archive download only serves detections older than the last 7 days.
ARCHIVE_LAG_DAYS = 7


class ConfigError(ValueError):
    pass


@dataclass
class Area:
    type: str
    country: str | None = None
    archive_country: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    polygon: list[tuple[float, float]] | None = None


@dataclass
class Job:
    sensor: Sensor
    start: date
    end: date
    payload: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass
class RequestConfig:
    email: str | None
    format: str
    area: Area
    start: date
    end: date
    sensors: list[Sensor]
    chunk: str
    form: dict[str, Any]
    raw: dict[str, Any]


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _as_date(v: Any, name: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise ConfigError(f"{name} must be YYYY-MM-DD, got {v!r}") from None


def _parse_area(a: dict | None) -> Area:
    if not a:
        raise ConfigError("area is required (type: world | country | bbox | polygon)")
    t = a.get("type")
    if t not in AREA_TYPES:
        raise ConfigError(f"area.type must be one of {AREA_TYPES}, got {t!r}")
    area = Area(type=t, country=a.get("country"), archive_country=a.get("archive_country"))
    if t == "country" and not area.country:
        raise ConfigError("area.country (ISO-3 code, e.g. AUS) is required for type: country")
    if t == "bbox":
        b = a.get("bbox")
        if not b or len(b) != 4:
            raise ConfigError("area.bbox must be [west, south, east, north]")
        w, s, e, n = (float(x) for x in b)
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ConfigError(f"area.bbox out of range or inverted: {b}")
        area.bbox = (w, s, e, n)
    if t == "polygon":
        p = a.get("polygon")
        if not p or len(p) < 3:
            raise ConfigError("area.polygon needs at least 3 [lon, lat] vertices")
        area.polygon = [(float(x), float(y)) for x, y in p]
    return area


def load_config(path: str | Path) -> RequestConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> RequestConfig:
    fmt = raw.get("format", "csv")
    if fmt not in FORMATS:
        raise ConfigError(f"format must be one of {FORMATS}, got {fmt!r}")
    dr = raw.get("date_range") or {}
    start = _as_date(dr.get("start"), "date_range.start")
    end = _as_date(dr.get("end"), "date_range.end")
    if end < start:
        raise ConfigError("date_range.end is before date_range.start")
    sensors = [get_sensor(k) for k in raw.get("sensors") or []]
    if not sensors:
        raise ConfigError("sensors must list at least one of modis_c61, viirs_snpp, viirs_noaa20, viirs_noaa21")
    chunk = str(raw.get("chunk", "year"))
    if not re.fullmatch(r"none|year|month|\d+d", chunk):
        raise ConfigError("chunk must be none, year, month or <N>d (e.g. 90d)")
    form = _deep_merge(DEFAULT_FORM, raw.get("form") or {})
    for key, s in ((s.key, s) for s in sensors):
        form["sensor_values"].setdefault(key, s.portal_code)
    return RequestConfig(
        email=raw.get("email"), format=fmt, area=_parse_area(raw.get("area")),
        start=start, end=end, sensors=sensors, chunk=chunk, form=form, raw=raw,
    )


def split_range(start: date, end: date, chunk: str) -> list[tuple[date, date]]:
    if chunk == "none":
        return [(start, end)]
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        if chunk == "year":
            nxt = date(cur.year + 1, 1, 1)
        elif chunk == "month":
            nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        else:
            nxt = cur + timedelta(days=int(chunk[:-1]))
        out.append((cur, min(end, nxt - timedelta(days=1))))
        cur = nxt
    return out


def _fmt_polygon(poly: list[tuple[float, float]], style: str) -> str:
    ring = poly if poly[0] == poly[-1] else poly + [poly[0]]
    if style == "wkt":
        return "POLYGON((" + ",".join(f"{x} {y}" for x, y in ring) + "))"
    return ",".join(f"{x},{y}" for x, y in ring)


def build_payload(cfg: RequestConfig, sensor: Sensor, start: date, end: date) -> dict[str, str]:
    form = cfg.form
    f = form["fields"]
    df = form["date_format"]
    p: dict[str, str] = {}
    if cfg.email:
        p[f["email"]] = cfg.email
    p[f["sensor"]] = form["sensor_values"].get(sensor.key, sensor.portal_code)
    p[f["format"]] = form["format_values"].get(cfg.format, cfg.format)
    p[f["start_date"]] = start.strftime(df)
    p[f["end_date"]] = end.strftime(df)
    p[f["area_type"]] = form["area_type_values"].get(cfg.area.type, cfg.area.type)
    a = cfg.area
    if a.type == "country":
        p[f["country"]] = a.country
    elif a.type == "bbox":
        p[f["bbox"]] = ",".join(f"{v:g}" for v in a.bbox)
    elif a.type == "polygon":
        p[f["polygon"]] = _fmt_polygon(a.polygon, form["polygon_format"])
    for k, v in (form.get("constants") or {}).items():
        p[k] = str(v)
    return p


def plan_jobs(cfg: RequestConfig, today: date | None = None) -> list[Job]:
    today = today or date.today()
    latest = today - timedelta(days=ARCHIVE_LAG_DAYS)
    jobs: list[Job] = []
    for sensor in cfg.sensors:
        s = max(cfg.start, sensor.coverage_start)
        e = min(cfg.end, latest, sensor.coverage_end or cfg.end)
        if s > e:
            continue
        for cs, ce in split_range(s, e, cfg.chunk):
            warnings = []
            if not cfg.email:
                warnings.append("no email set; FIRMS uses it to notify when the file is ready")
            jobs.append(Job(sensor, cs, ce, build_payload(cfg, sensor, cs, ce), warnings))
    return jobs


def skipped_sensors(cfg: RequestConfig, today: date | None = None) -> list[str]:
    today = today or date.today()
    latest = today - timedelta(days=ARCHIVE_LAG_DAYS)
    out = []
    for sensor in cfg.sensors:
        s = max(cfg.start, sensor.coverage_start)
        e = min(cfg.end, latest, sensor.coverage_end or cfg.end)
        if s > e:
            out.append(f"{sensor.key}: no overlap between requested dates and coverage "
                       f"{sensor.coverage_start}..{sensor.coverage_end or 'present'}")
    return out

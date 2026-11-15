"""Parse FIRMS area-API CSV into detections with a natural key and a content hash (spec §6 step 2).

Natural key: (source, satellite, latitude, longitude, acq_date, acq_time) from the raw strings as
delivered, trimmed. Content hash: sha256 over every delivered column (trimmed names and values) in
sorted column-name order, so column order does not matter and any changed value changes the hash.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass, field
from datetime import date, time
from typing import Mapping

KEY_COLUMNS = ("satellite", "latitude", "longitude", "acq_date", "acq_time")


class FirmsFormatError(ValueError):
    """The response is not the CSV layout we expect (e.g. a key column is missing)."""


def product_of(source: str) -> str:
    """'NRT' or 'SP' from a FIRMS source id such as VIIRS_NOAA20_NRT."""
    product = source.rsplit("_", 1)[-1]
    if product not in ("NRT", "SP"):
        raise ValueError(f"cannot tell NRT from SP for source {source!r}")
    return product


def clean_row(row: Mapping[str, str]) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in row.items()}


def natural_key(source: str, row: Mapping[str, str]) -> tuple[str, ...]:
    row = clean_row(row)
    return (source.strip(), *(row[c] for c in KEY_COLUMNS))


def content_hash(row: Mapping[str, str]) -> str:
    canonical = json.dumps(sorted(clean_row(row).items()), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Detection:
    source: str
    satellite: str
    latitude_raw: str
    longitude_raw: str
    acq_date_raw: str
    acq_time_raw: str
    content_hash: str
    product: str
    latitude: float
    longitude: float
    acq_date: date
    acq_time: time
    instrument: str | None
    confidence: str | None
    version: str | None
    frp: float | None
    daynight: str | None
    type: int | None
    delivered: dict = field(compare=False)

    @property
    def key(self) -> tuple[str, ...]:
        return (self.source, self.satellite, self.latitude_raw, self.longitude_raw, self.acq_date_raw,
                self.acq_time_raw)

    def params(self) -> dict:
        """Bind parameters for the incoming_detections temp table (delivered as a JSON string)."""
        out = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "delivered"}
        out["delivered"] = json.dumps(self.delivered, ensure_ascii=False, sort_keys=True)
        return out


@dataclass
class ParseResult:
    detections: list[Detection]
    rows: int        # data rows delivered
    malformed: int   # rows skipped: wrong field count, empty or unparseable key fields
    duplicates: int  # rows whose natural key repeats within this response (the last one is kept)


def parse_hhmm(raw: str) -> time:
    if not raw.isdigit() or len(raw) > 4:
        raise ValueError(f"acq_time {raw!r} is not HHMM")
    hours, minutes = divmod(int(raw), 100)
    return time(hours, minutes)


def _opt_float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _opt_int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def to_detection(source: str, raw: Mapping) -> Detection:
    if None in raw or any(v is None for v in raw.values()):
        raise ValueError("wrong number of fields")
    row = clean_row(raw)
    if not all(row.get(c) for c in KEY_COLUMNS):
        raise ValueError("empty key field")
    lat, lon = float(row["latitude"]), float(row["longitude"])
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    source = source.strip()
    return Detection(
        source=source, satellite=row["satellite"], latitude_raw=row["latitude"], longitude_raw=row["longitude"],
        acq_date_raw=row["acq_date"], acq_time_raw=row["acq_time"], content_hash=content_hash(row),
        product=product_of(source), latitude=lat, longitude=lon, acq_date=date.fromisoformat(row["acq_date"]),
        acq_time=parse_hhmm(row["acq_time"]), instrument=row.get("instrument") or None,
        confidence=row.get("confidence") or None, version=row.get("version") or None,
        frp=_opt_float(row.get("frp")), daynight=row.get("daynight") or None, type=_opt_int(row.get("type")),
        delivered=row)


def parse_csv(text: str, source: str) -> ParseResult:
    text = text.lstrip("﻿")
    if not text.strip():
        return ParseResult([], 0, 0, 0)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip() for h in reader.fieldnames or []]
    missing = [c for c in KEY_COLUMNS if c not in header]
    if missing:
        first_line = text.splitlines()[0][:200]
        raise FirmsFormatError(f"{source} response lacks column(s) {', '.join(missing)}; first line: {first_line!r}")
    by_key: dict[tuple[str, ...], Detection] = {}
    rows = malformed = duplicates = 0
    for raw in reader:
        rows += 1
        try:
            d = to_detection(source, raw)
        except ValueError:
            malformed += 1
            continue
        if d.key in by_key:
            duplicates += 1
        by_key[d.key] = d
    return ParseResult(list(by_key.values()), rows, malformed, duplicates)

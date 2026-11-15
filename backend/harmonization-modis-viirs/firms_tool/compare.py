"""Compare one day of area-API SP output with the public yearly archive CSV."""

from __future__ import annotations

import csv
import io
from datetime import date

from .area_api import Client, Window
from .portal import new_session
from .public import archive_url
from .sensors import Sensor


def archive_rows_for_day(sensor: Sensor, day: date, country: str) -> tuple[list[str], list[dict]]:
    url = archive_url(sensor, day.year, country)
    target = day.isoformat()
    with new_session().get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        lines = (ln.decode() for ln in r.iter_lines() if ln)
        reader = csv.DictReader(lines)
        rows = [row for row in reader if row["acq_date"] == target]
        return list(reader.fieldnames or []), rows


def _key(row: dict) -> tuple:
    return (round(float(row["latitude"]), 4), round(float(row["longitude"]), 4), row["acq_time"].zfill(4))


def compare(client: Client, sensor: Sensor, day: date, country: str) -> dict:
    if day < sensor.coverage_start or (sensor.coverage_end and day > sensor.coverage_end):
        return {"sensor": sensor.key, "error": f"{day} is outside {sensor.key} coverage"}
    if not sensor.archive_product:
        return {"sensor": sensor.key, "error": f"{sensor.key} is not published in the yearly country archive"}
    a_cols, a_rows = archive_rows_for_day(sensor, day, country)
    if not a_rows:
        return {"sensor": sensor.key, "error": f"no archive rows for {country} on {day}"}
    lats = [float(r["latitude"]) for r in a_rows]
    lons = [float(r["longitude"]) for r in a_rows]
    bbox = f"{min(lons) - 0.01:.4f},{min(lats) - 0.01:.4f},{max(lons) + 0.01:.4f},{max(lats) + 0.01:.4f}"
    text = client.fetch_window(Window(sensor.api_sp, day, 1), bbox)
    reader = csv.DictReader(io.StringIO(text))
    api_rows = list(reader)
    api_cols = list(reader.fieldnames or [])

    api_by_key = {_key(r): r for r in api_rows}
    matched = [(r, api_by_key[_key(r)]) for r in a_rows if _key(r) in api_by_key]
    common = [c for c in a_cols if c in api_cols]
    diffs: dict[str, list] = {}
    diff_counts: dict[str, int] = {}
    for arc, api in matched:
        for c in common:
            if arc[c].strip() != api[c].strip():
                try:
                    if float(arc[c]) == float(api[c]):
                        continue
                except ValueError:
                    pass
                diff_counts[c] = diff_counts.get(c, 0) + 1
                diffs.setdefault(c, [])
                if len(diffs[c]) < 5:
                    diffs[c].append({"archive": arc[c], "api": api[c]})
    return {
        "sensor": sensor.key, "source": sensor.api_sp, "day": day.isoformat(), "country": country, "bbox": bbox,
        "archive_columns": a_cols, "api_columns": api_cols,
        "only_in_archive": [c for c in a_cols if c not in api_cols],
        "only_in_api": [c for c in api_cols if c not in a_cols],
        "archive_rows": len(a_rows), "api_rows_in_bbox": len(api_rows),
        "archive_rows_matched_in_api": len(matched),
        "value_difference_counts": diff_counts,
        "value_differences": diffs,
        "archive_versions": sorted({r.get("version", "") for r in a_rows}),
        "api_versions": sorted({r.get("version", "") for r in api_rows}),
    }

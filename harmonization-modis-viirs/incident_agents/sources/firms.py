"""NASA FIRMS area API check for one small box around a located ticket.

Uses firms_tool (key FIRMS_MAP_KEY_1 from .env). The stock client can sleep up to
20 minutes waiting for the transaction budget; this subclass fails fast instead,
and the caller marks the ticket "Mentioned" when the check could not run.
S-NPP stopped on 2 Nov 2026, so NOAA-20, NOAA-21 and MODIS NRT are queried.
The request URL contains the key and is never stored.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from firms_tool import area_api

SENSORS = ("viirs_noaa20", "viirs_noaa21", "modis_c61")


class _FastClient(area_api.Client):
    def _get_text(self, url: str, attempts: int = 2) -> str:  # type: ignore[override]
        return super()._get_text(url, attempts=2)

    def wait_for_budget(self, reserve: int):  # type: ignore[override]
        raise area_api.RateLimited("FIRMS transaction budget exhausted; check skipped")


class Firms:
    def __init__(self):
        key = area_api.resolve_map_key()
        self.client = _FastClient(key, log=lambda m: None) if key else None

    @property
    def available(self) -> bool:
        return self.client is not None

    def detections(self, box: tuple[float, float, float, float], start: date, end: date) -> list[dict]:
        if not self.client:
            raise area_api.ApiError("no FIRMS map key")
        today = datetime.now(timezone.utc).date()
        end = min(end, today)
        start = max(start, today - timedelta(days=9))
        rows: list[dict] = []
        for key in SENSORS:
            text = area_api.query_viewport(box, key, start, end, mode="nrt", client=self.client)
            for row in csv.DictReader(io.StringIO(text)):
                row["sensor"] = key
                rows.append(row)
        return rows

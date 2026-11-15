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

from .. import config

SENSORS = ("viirs_noaa20", "viirs_noaa21", "modis_c61")
SNPP_END = date(2026, 11, 2)  # S-NPP data are valid before this date
NRT_DAYS = 9


class _FastClient(area_api.Client):
    def _get_text(self, url: str, attempts: int = 2) -> str:  # type: ignore[override]
        return super()._get_text(url, attempts=2)

    def wait_for_budget(self, reserve: int):  # type: ignore[override]
        raise area_api.RateLimited("FIRMS transaction budget exhausted; check skipped")


class Firms:
    def __init__(self):
        config.apply_env_files()  # the key may be in the repo-root .env, which firms_tool does not read
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
        if start >= today - timedelta(days=NRT_DAYS):  # live: near-real-time only
            mode, sensors = "nrt", SENSORS
        else:  # replay: standard processing where it exists, NRT after it
            mode, sensors = "auto", SENSORS + (("viirs_snpp",) if start < SNPP_END else ())
        rows: list[dict] = []
        missing = 0
        for key in sensors:
            try:
                text = area_api.query_viewport(box, key, start, end, mode=mode, client=self.client)
            except area_api.ApiError as e:
                if "coverage" not in str(e):
                    raise
                missing += 1  # this sensor has no data for these dates
                continue
            for row in csv.DictReader(io.StringIO(text)):
                row["sensor"] = key
                rows.append(row)
        if missing == len(sensors):
            raise area_api.ApiError("no FIRMS coverage for these dates")
        return rows

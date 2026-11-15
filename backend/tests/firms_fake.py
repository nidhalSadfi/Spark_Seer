"""Fake FIRMS for tests: a requests.Session stand-in that serves the SYNTHETIC fixtures by URL.

It sits under the real firms_tool.area_api.Client, so tests exercise the same client the worker
uses (URL building, error handling, key redaction) without any network access.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from firms_tool.area_api import Client

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "firms"
NRT_FILES = ("MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text


def read_table(source: str) -> list[dict[str, str]]:
    with (FIXTURES / f"{source}.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


class FakeFirmsSession:
    def __init__(self):
        self.availability_text = (FIXTURES / "data_availability.csv").read_text()
        self.tables = {s: read_table(s) for s in NRT_FILES}  # mutable: tests edit rows between polls
        self.fail_sources: set[str] = set()                   # answered with HTTP 400 (-> ApiError)
        self.urls: list[str] = []
        self.windows: list[tuple[str, date, int]] = []        # (source, start, days) per area call

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        parts = urlparse(url).path.strip("/").split("/")
        if parts[:3] == ["api", "data_availability", "csv"]:
            return FakeResponse(200, self.availability_text)
        if parts[:3] == ["api", "area", "csv"] and len(parts) == 8:
            source, days, start = parts[4], int(parts[6]), date.fromisoformat(parts[7])
            self.windows.append((source, start, days))
            if source in self.fail_sources:
                return FakeResponse(400, "Invalid request (simulated outage)")
            return FakeResponse(200, self.render(source, start, start + timedelta(days=days - 1)))
        return FakeResponse(404, "not found")

    def render(self, source: str, start: date, end: date) -> str:
        rows = self.tables.get(source, [])
        header = list(rows[0]) if rows else list(read_table("MODIS_NRT")[0])
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(r for r in rows if start <= date.fromisoformat(r["acq_date"]) <= end)
        return out.getvalue()

    def rows_between(self, source: str, start: date, end: date) -> int:
        return sum(start <= date.fromisoformat(r["acq_date"]) <= end for r in self.tables[source])


def client_factory(session: FakeFirmsSession):
    return lambda key: Client(key, session=session, log=lambda msg: None)

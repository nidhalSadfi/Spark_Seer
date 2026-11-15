"""Public yearly per-country archive files (no login, no key)."""

from __future__ import annotations

import requests

from .portal import BASE, new_session
from .sensors import Sensor

ARCHIVE_INDEX_URL = f"{BASE}/data/country/yearly_summary_files.txt"


def archive_index(s: requests.Session | None = None) -> set[str]:
    s = s or new_session()
    r = s.get(ARCHIVE_INDEX_URL, timeout=60)
    r.raise_for_status()
    return {line.split(",", 1)[0] for line in r.text.splitlines()[1:] if line}


def archive_url(sensor: Sensor, year: int, country: str) -> str:
    """country is the archive file-name form (e.g. United_States) or 'all'."""
    if not sensor.archive_product:
        raise ValueError(f"{sensor.key} is not published in the yearly country archive")
    p = sensor.archive_product
    if country == "all":
        return f"{BASE}/data/country/zips/{p}_{year}_all_countries.zip"
    return f"{BASE}/data/country/{p}/{year}/{p}_{year}_{country}.csv"

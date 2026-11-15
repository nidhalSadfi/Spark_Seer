"""Public yearly per-country archive files (no login, no key).

One country is a CSV under ``/data/country/<product>/<year>/``.
The world scope (``all``, ``world``, or ``global``) is the published zip
``/data/country/zips/<product>_<year>_all_countries.zip``.
"""

from __future__ import annotations

import requests

from .portal import BASE, new_session
from .sensors import Sensor

ARCHIVE_INDEX_URL = f"{BASE}/data/country/yearly_summary_files.txt"
# Names that select the all-countries zip. Other values are file-name countries
# such as Tunisia or United_States, and their spelling is left unchanged.
GLOBAL_SCOPES = {"all", "world", "global"}


def archive_index(s: requests.Session | None = None) -> set[str]:
    s = s or new_session()
    r = s.get(ARCHIVE_INDEX_URL, timeout=60)
    r.raise_for_status()
    return {line.split(",", 1)[0] for line in r.text.splitlines()[1:] if line}


def archive_scope(country: str) -> str:
    """Return ``all`` for a world scope, otherwise the country file-name token."""
    text = country.strip()
    if text.lower() in GLOBAL_SCOPES:
        return "all"
    return text


def archive_url(sensor: Sensor, year: int, country: str) -> str:
    """Build the public yearly URL for one country or for every country.

    ``country`` is the archive file-name form (for example ``United_States``
    or ``Tunisia``). ``all``, ``world``, and ``global`` all use the
    all-countries zip.
    """
    if not sensor.archive_product:
        raise ValueError(f"{sensor.key} is not published in the yearly country archive")
    p = sensor.archive_product
    scope = archive_scope(country)
    if scope == "all":
        return f"{BASE}/data/country/zips/{p}_{year}_all_countries.zip"
    return f"{BASE}/data/country/{p}/{year}/{p}_{year}_{scope}.csv"

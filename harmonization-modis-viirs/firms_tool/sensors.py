"""Sensor catalogue shared by the portal, public-archive and area-API paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Sensor:
    key: str
    label: str
    # Code FIRMS uses in archive-request file names (e.g. DL_FIRE_J1V-C2_755412.zip).
    portal_code: str
    coverage_start: date
    coverage_end: date | None
    # Product directory under /data/country/ (None = not published there).
    archive_product: str | None
    api_sp: str
    api_nrt: str


SENSORS: dict[str, Sensor] = {
    s.key: s
    for s in [
        Sensor("modis_c61", "MODIS Collection 6.1 (Terra+Aqua, 1 km)", "M-C61",
               date(2000, 11, 1), None, "modis", "MODIS_SP", "MODIS_NRT"),
        Sensor("viirs_snpp", "VIIRS S-NPP 375 m", "SV-C2",
               date(2012, 1, 20), date(2026, 11, 2), "viirs-snpp", "VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
        Sensor("viirs_noaa20", "VIIRS NOAA-20 375 m", "J1V-C2",
               date(2018, 4, 1), None, "viirs-jpss1", "VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
        Sensor("viirs_noaa21", "VIIRS NOAA-21 375 m", "J2V-C2",
               date(2023, 3, 1), None, None, "VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"),
    ]
}


def get_sensor(key: str) -> Sensor:
    try:
        return SENSORS[key]
    except KeyError:
        raise ValueError(f"unknown sensor {key!r}; choose from {', '.join(SENSORS)}") from None

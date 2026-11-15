"""Continent and latitude band of a 0.5° cell, for the world validation report (spec §16).

Simple bounding rules on the cell centre (no boundary file: Natural Earth continents are not
available offline here). They are approximate near the continental joins; known misassignments:
European Turkey (Thrace) and the Greek islands off Anatolia count as Asia, western Kazakhstan
counts as Europe north of 51°N, Sinai counts as Africa, north-west Colombia (west of 77°W, north of
7°N) counts as North America, the Azores count as North America, and Nusa Tenggara / Timor south
of 10°S count as Oceania. Rules, first match wins (lat, lon = cell centre, degrees):

  Antarctica     lat < -60
  Oceania        (lon >= 110 and lat < -10) or (lon >= 141 and lat < 0) or lon < -150
  North America  lon < -25 and (lat >= 13 or (lat >= 7 and lon < -77)), or lat >= 67 and lon < -10 (Greenland)
  South America  lon < -25
  Africa         lon >= -26 and one of:
                   lat < 12 and lon < 60 and lat >= -40           (south of the Sahel, Horn, Madagascar)
                   12 <= lat < 29.5 and lon < 43.3 - 0.62 (lat - 12.6)  (west of the Red Sea)
                   29.5 <= lat < 34 and lon < 34.2                (Libya, Egypt, Sinai)
                   lon < -1 and lat < 36                          (Morocco, west Algeria, Atlantic)
                   -1 <= lon < 12 and lat < 37.5                  (Algeria, Tunisia)
  Europe         -26 <= lon < 60 and lat >= 34, except lon >= 26 and lat < 42 (Anatolia, Levant,
                 Caucasus south) and lon >= 46 and lat < 51 (Caspian, Kazakhstan)
  Asia           everything else

Latitude bands are 30° wide, by cell centre: [60, 90), [30, 60), [0, 30), [-30, 0), [-60, -30), [-90, -60).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CONTINENTS = ("Africa", "Antarctica", "Asia", "Europe", "North America", "Oceania", "South America")
BANDS = ((60, 90), (30, 60), (0, 30), (-30, 0), (-60, -30), (-90, -60))


def cell_centre(cy, cx, cell_deg: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    return (np.asarray(cy, dtype=float) + 0.5) * cell_deg, (np.asarray(cx, dtype=float) + 0.5) * cell_deg


def continent(lat, lon) -> np.ndarray:
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    antarctica = lat < -60
    oceania = ((lon >= 110) & (lat < -10)) | ((lon >= 141) & (lat < 0)) | (lon < -150)
    americas = lon < -25
    north_am = (americas & ((lat >= 13) | ((lat >= 7) & (lon < -77)))) | ((lat >= 67) & (lon < -10))
    south_am = americas
    africa = (lon >= -26) & (
        ((lat < 12) & (lon < 60) & (lat >= -40))
        | ((lat >= 12) & (lat < 29.5) & (lon < 43.3 - 0.62 * (lat - 12.6)))
        | ((lat >= 29.5) & (lat < 34) & (lon < 34.2))
        | ((lon < -1) & (lat < 36))
        | ((lon >= -1) & (lon < 12) & (lat < 37.5)))
    europe = ((lon >= -26) & (lon < 60) & (lat >= 34)
              & ~((lon >= 26) & (lat < 42)) & ~((lon >= 46) & (lat < 51)))
    return np.select([antarctica, oceania, north_am, south_am, africa, europe],
                     ["Antarctica", "Oceania", "North America", "South America", "Africa", "Europe"],
                     default="Asia").astype(object)


def band_label(lo: int, hi: int) -> str:
    def f(v):
        return f"{abs(v)}°{'N' if v > 0 else 'S' if v < 0 else ''}"
    return f"{f(lo)}-{f(hi)}"


def lat_band(lat) -> np.ndarray:
    lat = np.asarray(lat, dtype=float)
    out = np.full(lat.shape, None, dtype=object)
    for lo, hi in BANDS:
        out[(lat >= lo) & (lat < hi) & pd.isnull(out)] = band_label(lo, hi)
    return out


def cell_regions(cells: pd.DataFrame, cell_deg: float = 0.5) -> pd.DataFrame:
    """Unique (cy, cx) of `cells` with `continent` and `lat_band` columns."""
    u = cells[["cy", "cx"]].drop_duplicates().reset_index(drop=True)
    lat, lon = cell_centre(u.cy, u.cx, cell_deg)
    return u.assign(continent=continent(lat, lon), lat_band=lat_band(lat))

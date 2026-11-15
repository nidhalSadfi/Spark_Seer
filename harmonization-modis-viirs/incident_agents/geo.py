"""Zone boxes (Nominatim + a mainland table) and place geocoding (Open-Meteo).

Nominatim usage policy: at most 1 request per second, an identifying User-Agent,
and results cached. Open-Meteo geocoding is used for town names found in posts,
always restricted to the planned country.
"""

from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass

import requests

from .config import USER_AGENT

# Mainland boxes (minLon, minLat, maxLon, maxLat). Nominatim returns the whole
# overseas extent for France, which would make every source query global.
COUNTRY_BOXES: dict[str, tuple[float, float, float, float]] = {
    "FR": (-5.2, 41.3, 9.6, 51.1),
    "DZ": (-8.7, 18.9, 12.0, 37.1),
    "TN": (7.5, 30.2, 11.6, 37.6),
    "MA": (-13.2, 27.6, -1.0, 35.95),
    "ES": (-9.4, 35.9, 3.4, 43.8),
    "PT": (-9.6, 36.9, -6.1, 42.2),
    "IT": (6.6, 36.6, 18.6, 47.1),
    "GR": (19.3, 34.8, 29.7, 41.8),
    "TR": (25.6, 35.8, 44.9, 42.2),
    "LB": (35.1, 33.0, 36.7, 34.7),
    "SY": (35.7, 32.3, 42.4, 37.4),
}

COUNTRY_NAMES = {"FR": "France", "DZ": "Algeria", "TN": "Tunisia", "MA": "Morocco", "ES": "Spain",
                 "PT": "Portugal", "IT": "Italy", "GR": "Greece", "TR": "Turkey", "LB": "Lebanon",
                 "SY": "Syria"}

# Open-Meteo feature codes -> (precision, radius km)
_FEATURE_PRECISION = {
    "PPLC": ("city", 15), "PPLA": ("city", 12), "PPLA2": ("town", 8), "PPLA3": ("town", 6),
    "PPLA4": ("town", 5), "PPL": ("town", 5), "PPLX": ("locality", 3), "PPLL": ("locality", 3),
    "ADM1": ("region", 80), "ADM2": ("region", 40), "ADM3": ("area", 20), "ADM4": ("area", 10),
    "MT": ("locality", 5), "MTS": ("area", 25), "FRST": ("area", 10), "PRK": ("area", 10),
    "LK": ("locality", 4), "ISL": ("area", 10), "VAL": ("area", 8), "MSF": ("locality", 3),
}


@dataclass
class Place:
    name: str
    country: str
    lat: float
    lon: float
    precision: str
    radius_km: float
    admin1: str | None = None


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def in_box(lat: float, lon: float, box: tuple[float, float, float, float], pad: float = 0.0) -> bool:
    x0, y0, x1, y1 = box
    return x0 - pad <= lon <= x1 + pad and y0 - pad <= lat <= y1 + pad


def box_around(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.2, math.cos(lat * math.pi / 180)))
    return (round(lon - dlon, 3), round(lat - dlat, 3), round(lon + dlon, 3), round(lat + dlat, 3))


class Geocoder:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._nom_lock = threading.Lock()
        self._nom_last = 0.0
        self._cache: dict[tuple, object] = {}

    def zone_box(self, name: str, country: str) -> tuple[tuple[float, float, float, float], str] | None:
        """Box for a zone inside one country. Returns (box, resolved label) or None."""
        country = country.upper()
        if name.strip().lower() in {country.lower(), COUNTRY_NAMES.get(country, "").lower()} \
                and country in COUNTRY_BOXES:
            return COUNTRY_BOXES[country], COUNTRY_NAMES[country]
        key = ("zone", name.lower(), country)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        with self._nom_lock:
            wait = 1.1 - (time.monotonic() - self._nom_last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get("https://nominatim.openstreetmap.org/search", timeout=30,
                               params={"q": name, "countrycodes": country.lower(), "format": "jsonv2", "limit": 1})
            finally:
                self._nom_last = time.monotonic()
        res = None
        if r.ok and r.json():
            hit = r.json()[0]
            s, n, w, e = (float(v) for v in hit["boundingbox"])
            if hit.get("addresstype") == "country" and country in COUNTRY_BOXES:
                res = (COUNTRY_BOXES[country], COUNTRY_NAMES[country])
            else:
                res = ((w, s, e, n), hit.get("display_name", name).split(",")[0])
        self._cache[key] = res
        return res

    def local_names(self, name: str, country: str, langs: list[str]) -> list[str]:
        """Other spellings of a place in the given languages (OSM name:ar, name:fr, ...), e.g.
        'Mateur' -> 'ماطر'. Used by replay so Arabic posts can match. Paced 1.1 s, cached."""
        key = ("names", name.lower(), country, tuple(langs))
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        with self._nom_lock:
            wait = 1.1 - (time.monotonic() - self._nom_last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get("https://nominatim.openstreetmap.org/search", timeout=30,
                               params={"q": name, "countrycodes": country.lower(), "format": "jsonv2",
                                       "limit": 1, "namedetails": 1})
            except requests.RequestException:
                return []
            finally:
                self._nom_last = time.monotonic()
        out: list[str] = []
        if r.ok and r.json():
            nd = r.json()[0].get("namedetails") or {}
            for lang in langs:
                v = (nd.get(f"name:{lang}") or "").strip()
                if v and _fold(v) != _fold(name) and v not in out:
                    out.append(v)
        self._cache[key] = out
        return out

    def country_at(self, lat: float, lon: float) -> str | None:
        """ISO-2 country under a point (Nominatim reverse, paced 1.1 s, cached). None if unknown."""
        key = ("rev", round(lat, 2), round(lon, 2))
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        with self._nom_lock:
            wait = 1.1 - (time.monotonic() - self._nom_last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get("https://nominatim.openstreetmap.org/reverse", timeout=30,
                               params={"lat": lat, "lon": lon, "zoom": 3, "format": "jsonv2"})
            except requests.RequestException:
                return None
            finally:
                self._nom_last = time.monotonic()
        cc = ((r.json().get("address") or {}).get("country_code") or "").upper() if r.ok else ""
        self._cache[key] = cc or None
        return cc or None

    def _place_single(self, name: str, country: str,
                      box: tuple[float, float, float, float] | None = None) -> Place | None:
        """Geocode one place name inside a country (and inside box, if given). Never guesses."""
        key = ("place", name.lower(), country.upper(), box)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        out = None
        for hit in self._om_search(name, country):
            lat, lon = float(hit["latitude"]), float(hit["longitude"])
            if box and not in_box(lat, lon, box, pad=0.05):
                continue
            prec, radius = _FEATURE_PRECISION.get(hit.get("feature_code", ""), ("locality", 5))
            out = Place(hit["name"], country.upper(), lat, lon, prec, radius, hit.get("admin1"))
            break
        if out is None and len(name) > 3:
            out = self._nominatim_place(name, country, box)
        self._cache[key] = out
        return out

    def place(self, name: str, country: str, box: tuple[float, float, float, float] | None = None) -> Place | None:
        """Geocode a place phrase inside a country. Phrases like "Jasper, Florida" or
        "Faro, Silves, Algoz" are split; a hit wins when its admin areas match the other parts."""
        parts = [p.strip() for p in re.split(r"[,;/(]|\s-\s", name) if p.strip(" )")]
        if len(parts) <= 1:
            return self._place_single(name, country, box)
        key = ("multi", name.lower(), country.upper(), box)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        folded = [_fold(p.strip(" )")) for p in parts]
        best: tuple | None = None
        for i, part in enumerate(parts):
            for hit in self._om_search(part.strip(" )"), country):
                lat, lon = float(hit["latitude"]), float(hit["longitude"])
                if box and not in_box(lat, lon, box, pad=0.05):
                    continue
                admins = [_fold(hit.get(k) or "") for k in ("admin1", "admin2", "admin3", "admin4")]
                score = sum(1 for j, f in enumerate(folded) if j != i and f
                            and any(a and (f in a or a in f) for a in admins))
                prec, radius = _FEATURE_PRECISION.get(hit.get("feature_code", ""), ("locality", 5))
                cand = (score, -radius, Place(hit["name"], country.upper(), lat, lon, prec, radius, hit.get("admin1")))
                if best is None or cand[:2] > best[:2]:
                    best = cand
        out = best[2] if best and best[0] >= 1 else None
        if out is None:
            out = self._nominatim_place(name, country, box)
        if out is None and best:
            out = best[2]
        self._cache[key] = out
        return out

    def _om_search(self, name: str, country: str) -> list[dict]:
        try:
            r = self.s.get("https://geocoding-api.open-meteo.com/v1/search", timeout=30,
                           params={"name": name, "count": 10, "countryCode": country.upper(), "format": "json"})
            hits = r.json().get("results", []) if r.ok else []
        except (requests.RequestException, ValueError):
            hits = []
        return [h for h in hits if (h.get("country_code") or "").upper() == country.upper()]

    def _nominatim_place(self, name: str, country: str, box) -> Place | None:
        """Regions, departments and small villages missing from Open-Meteo: centre of the Nominatim box."""
        z = self.zone_box(name, country)
        if not z:
            return None
        (x0, y0, x1, y1), label = z
        lat, lon = (y0 + y1) / 2, (x0 + x1) / 2
        if (box and not in_box(lat, lon, box, pad=0.05)) or (x0, y0, x1, y1) == COUNTRY_BOXES.get(country.upper()):
            return None
        radius = max(3.0, haversine_km(y0, x0, y1, x1) / 2)
        prec = "region" if radius > 30 else ("area" if radius > 6 else "locality")
        return Place(label, country.upper(), lat, lon, prec, round(radius, 1))

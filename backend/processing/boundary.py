"""Country clip (spec §5): keep detections inside the Tunisia or Algeria land boundary.

The boundary is a GeoJSON FeatureCollection (planned: Natural Earth admin-0 1:10m, public domain;
not committed yet). Features are selected by ISO-3 code from the `ISO_A3`, `ADM0_A3` or `iso_a3`
property. Points exactly on the border count as inside (shapely `intersects_xy`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

from .params import DEFAULT_PARAMS

ISO_PROPERTIES = ("ISO_A3", "ADM0_A3", "iso_a3")


class BoundaryError(ValueError):
    """The boundary file is unusable (not a FeatureCollection, a requested country is missing...)."""


@dataclass(frozen=True, eq=False)
class Boundary:
    geometry: shapely.Geometry  # union of the selected features, prepared
    codes: tuple[str, ...]
    sha256: str
    source: str
    path: str
    features: int
    repaired: bool = False

    def contains(self, lat, lon) -> np.ndarray:
        """Vectorised: True where (lat, lon) is inside or on the boundary."""
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        if lat.size == 0:
            return np.zeros(0, dtype=bool)
        return np.asarray(shapely.intersects_xy(self.geometry, lon, lat), dtype=bool)

    def summary(self) -> dict:
        return {"status": "enabled", "path": self.path, "source": self.source, "sha256": self.sha256,
                "codes": list(self.codes), "features": self.features, "repaired": self.repaired}


def load_boundary(path, codes: tuple[str, ...] = DEFAULT_PARAMS.country_codes, *,
                  source: str | None = None) -> Boundary:
    """Load a GeoJSON FeatureCollection and keep the features whose ISO-3 code is in `codes`."""
    raw = Path(path).read_bytes()
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise BoundaryError(f"{path} is not valid JSON: {e}") from None
    return boundary_from_geojson(doc, codes, sha256=hashlib.sha256(raw).hexdigest(),
                                 source=source or doc.get("source") or doc.get("name") or Path(path).name,
                                 path=str(path))


def boundary_from_geojson(doc: dict, codes: tuple[str, ...], *, sha256: str, source: str,
                          path: str = "") -> Boundary:
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise BoundaryError("boundary must be a GeoJSON FeatureCollection")
    wanted = tuple(sorted({c.strip().upper() for c in codes}))
    if not wanted:
        raise BoundaryError("no country codes given")
    geoms, found = [], set()
    for feature in doc.get("features") or []:
        props = feature.get("properties") or {}
        feature_codes = {str(props[p]).strip().upper() for p in ISO_PROPERTIES if props.get(p) is not None}
        hit = feature_codes.intersection(wanted)
        if hit and feature.get("geometry"):
            geoms.append(shape(feature["geometry"]))
            found |= hit
    missing = [c for c in wanted if c not in found]
    if missing:
        raise BoundaryError(f"boundary has no feature for {', '.join(missing)} "
                            f"(looked in properties {', '.join(ISO_PROPERTIES)})")
    repaired = not all(g.is_valid for g in geoms)
    geom = shapely.union_all([g if g.is_valid else shapely.make_valid(g) for g in geoms])
    shapely.prepare(geom)
    return Boundary(geometry=geom, codes=wanted, sha256=sha256, source=source, path=path,
                    features=len(geoms), repaired=repaired)

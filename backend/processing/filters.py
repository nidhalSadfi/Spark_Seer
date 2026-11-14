"""The ONE shared detection filter (spec §5). Every path that turns detections into counts uses it.

Rules, applied in this order (a dropped row is counted under the first rule it fails):
  unknown_source       family (from `source`) or product (NRT/SP) cannot be determined
  invalid_coordinates  latitude or longitude missing or not a number
  south_of_min_lat     latitude < min_lat (34)
  low_confidence       MODIS: numeric confidence < 30 (or not a number);
                       VIIRS: confidence not in {n, h, nominal, high} (trimmed, case-insensitive)
  sp_type_missing      SP row without `type`
  sp_type_nonzero      SP row with `type` != 0
  static_mask          NRT row (no `type`) inside the static-source mask   [if a mask is given]
  outside_country      outside the country boundary (country clip)        [if a boundary is given]

All VIIRS families are kept here; harmonization chooses which one feeds the reference. A rule
whose input is missing (mask or boundary None) is reported as "disabled", never silently passed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .boundary import Boundary
from .common import FAMILIES, family_series, jsonable
from .mask import StaticMask
from .params import DEFAULT_PARAMS, ProcessingParams

DROP_REASONS = ("unknown_source", "invalid_coordinates", "south_of_min_lat", "low_confidence",
                "sp_type_missing", "sp_type_nonzero", "static_mask", "outside_country")
UNKNOWN = "unknown"


@dataclass(frozen=True)
class FilterSummary:
    input_rows: int
    kept_rows: int
    kept_by_family: dict[str, int]
    dropped: dict[str, dict[str, int]]  # reason -> family -> rows (every reason listed)
    dropped_total: int
    drop_share: dict[str, float]        # reason -> dropped / input rows
    rules: dict

    def to_dict(self) -> dict:
        return jsonable(self)


def disabled(reason: str) -> dict:
    return {"status": "disabled", "reason": reason}


def apply_filters(detections: pd.DataFrame, *, mask: StaticMask | None, boundary: Boundary | None,
                  params: ProcessingParams = DEFAULT_PARAMS) -> tuple[pd.DataFrame, FilterSummary]:
    """Return (kept rows with `family` and `product` columns added, summary)."""
    df = detections.reset_index(drop=True)
    n = len(df)
    family = family_series(df["source"]) if n else pd.Series(dtype=object)
    if "product" in df:
        product = df["product"].astype("string").str.strip().str.upper()
    else:
        product = df["source"].astype("string").str.strip().str.upper().str.rsplit("_", n=1).str[-1]
    product = product.where(product.isin(["NRT", "SP"]))
    lat = pd.to_numeric(df["latitude"], errors="coerce").astype(float)
    lon = pd.to_numeric(df["longitude"], errors="coerce").astype(float)
    conf = df["confidence"].astype("string").str.strip() if "confidence" in df else pd.Series(pd.NA, index=df.index,
                                                                                           dtype="string")
    typ = (pd.to_numeric(df["type"], errors="coerce").astype(float) if "type" in df
           else pd.Series(np.nan, index=df.index))

    reason = np.full(n, None, dtype=object)

    def drop(fails, name: str) -> None:
        fails = np.asarray(fails, dtype=bool) & pd.isnull(reason)
        reason[fails] = name

    is_modis = (family == "MODIS").to_numpy()
    modis_ok = (pd.to_numeric(conf, errors="coerce") >= params.modis_min_confidence).fillna(False).to_numpy()
    viirs_ok = conf.str.lower().isin(params.viirs_confidences).fillna(False).to_numpy()
    is_sp = (product == "SP").fillna(False).to_numpy()
    is_nrt = (product == "NRT").fillna(False).to_numpy()

    drop(family.isna().to_numpy() | product.isna().to_numpy(), "unknown_source")
    drop(~(np.isfinite(lat.to_numpy()) & np.isfinite(lon.to_numpy())), "invalid_coordinates")
    drop(~(lat.to_numpy() >= params.min_lat), "south_of_min_lat")
    drop(~np.where(is_modis, modis_ok, viirs_ok), "low_confidence")
    drop(is_sp & typ.isna().to_numpy(), "sp_type_missing")
    drop(is_sp & typ.notna().to_numpy() & (typ.to_numpy() != params.sp_keep_type), "sp_type_nonzero")

    rules = {"min_lat": params.min_lat, "modis_min_confidence": params.modis_min_confidence,
             "viirs_confidences": list(params.viirs_confidences), "sp_keep_type": params.sp_keep_type}
    if mask is None:
        rules["static_mask"] = disabled("no static-source mask given (STATIC_MASK_FILE unset)")
    else:
        rules["static_mask"] = mask.summary()
        test = is_nrt & pd.isnull(reason)
        hit = np.zeros(n, dtype=bool)
        hit[test] = mask.contains(lat.to_numpy()[test], lon.to_numpy()[test])
        drop(hit, "static_mask")
    if boundary is None:
        rules["country_clip"] = disabled("no country boundary given (COUNTRY_BOUNDARY_FILE unset)")
    else:
        rules["country_clip"] = boundary.summary()
        test = pd.isnull(reason)
        outside = np.zeros(n, dtype=bool)
        outside[test] = ~boundary.contains(lat.to_numpy()[test], lon.to_numpy()[test])
        drop(outside, "outside_country")

    keep = pd.isnull(reason)
    kept = df[keep].copy()
    kept["family"] = family[keep].to_numpy()
    kept["product"] = product[keep].astype(object).to_numpy()
    kept["latitude"] = lat[keep].to_numpy()
    kept["longitude"] = lon[keep].to_numpy()
    kept = kept.reset_index(drop=True)

    fam_label = family.fillna(UNKNOWN).to_numpy() if n else np.array([], dtype=object)
    present = set(fam_label)
    families = [f for f in (*FAMILIES, UNKNOWN) if f in present]
    tab = pd.DataFrame({"reason": reason, "family": fam_label}).dropna().groupby(["reason", "family"]).size()
    dropped = {r: {f: int(tab[(r, f)]) for f in families if (r, f) in tab.index} for r in DROP_REASONS}
    dropped_total = int(n - keep.sum())
    summary = FilterSummary(
        input_rows=int(n), kept_rows=int(keep.sum()),
        kept_by_family={f: int((keep & (fam_label == f)).sum()) for f in families if f != UNKNOWN},
        dropped=dropped, dropped_total=dropped_total,
        drop_share={r: (round(sum(dropped[r].values()) / n, 6) if n else 0.0) for r in DROP_REASONS},
        rules=rules)
    return kept, summary
